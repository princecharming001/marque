"""Champion loop (ARCHITECTURE §9, ``skills/editing/critique.md`` "How to decide" 9–10).

::

    render the Director's document at full quality → invariants → champion
    repeat:
        critique the champion (packet + frame judge + watcher + confirmation)      (cached when complete)
            a failed review is retried (backoff); if it keeps failing the loop stops and the champion ships
            marked NOT REVIEWED — a failed review is never "nothing to change"
        Director revision from the notes (branches from the champion's version; any op family; it sees the
            champion render through view_frames(source='render') and the critics' own sheets)
        no render-relevant change → a winless round
        else render the challenger at full quality → invariants
             pairwise(champion, challenger): two judges × both orders with equal evidence and a neutral diff;
             taste changes need every judge in both orders; a fix of confirmed P0/P1 notes wins when nothing
             regresses and no judge prefers the champion; a tie keeps the champion — unless the champion fails
             an invariant and the challenger passes (then it replaces it unless it loses)
        stop after 2 consecutive winless rounds (runaway guard 12; ``rounds`` overrides the guard)
    variant rounds: each recorded hook alternate is built from the champion by the Director, rendered and judged
        (the unanimous rule); the winner ships, the other is delivered as an alternate
    a closing review of a never-critiqued champion; the closing whole-video attention check — a confirmed P0/P1
    from it opens one more round (once). The champion always ships.

Disk (:mod:`studio.storage`): full renders write ProRes intermediates (~27 MB/s at 1080p30). Every render holds the
machine-wide render slot and first checks ``free ≥ estimated need + headroom`` (``STUDIO_MIN_FREE_GB``, default 1 GB),
reclaiming this job's stale intermediates and idle jobs' regenerable files before it falls back to the lean A-roll
intermediate, and only then refuses. Every render's intermediates are pruned as soon as its QA has run (critique and
pairwise read the finals and the saved QA), and the overlay cache keeps only what a render still links to. A crashed
render directory (no ``render.json``) is removed on resume.

State lives in ``logs/loop_state.json`` so an interrupted loop resumes at its last decided round.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from studio import storage
from studio.agent.providers import trace_event
from studio.config import get_settings
from studio.storage import DiskSpaceError

if TYPE_CHECKING:  # pragma: no cover
    from studio.agent.critics import JudgePanel
    from studio.agent.director import Director
    from studio.agent.providers import ModelSpec
    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = [
    "DEFAULT_GUARD", "WINLESS_STOP", "MIN_FREE_BYTES", "DiskSpaceError", "LoopConfig", "RoundRecord", "LoopState",
    "LoopResult", "ChampionLoop", "champion_loop", "ensure_disk", "free_bytes", "render_full", "prune_render",
    "clean_incomplete_renders", "prune_overlay_cache", "render_complete",
]

DEFAULT_GUARD = 12
WINLESS_STOP = 2
MIN_FREE_BYTES = storage.HEADROOM_BYTES
_STATE = "loop_state.json"

#: Render intermediates (never deliverables): A-roll/overlay layers, WAV mixes and stems, conformed stills.
INTERMEDIATES: tuple[str, ...] = storage.RENDER_INTERMEDIATES


def free_bytes(path: str | os.PathLike[str]) -> int:
    return storage.free_bytes(path)


def ensure_disk(path: str | os.PathLike[str], *, min_free: int | None = None, what: str = "a render",
                need: int = 0) -> int:
    """Raise :class:`DiskSpaceError` when ``path``'s volume has less than ``need`` + headroom bytes free
    (headroom: ``min_free``, else ``STUDIO_MIN_FREE_GB``, default 1 GB). Returns the free bytes."""
    floor = storage.headroom() if min_free is None else min_free
    free = storage.free_bytes(path)
    if free < need + floor:
        raise DiskSpaceError(
            f"only {free / 1e9:.2f} GB free on the work volume; {what} needs about {need / 1e9:.2f} GB plus "
            f"{floor / 1e9:.1f} GB headroom. Free some space, then resume with `studio edit <job_dir>`.")
    return free


def render_complete(rd: Path) -> bool:
    return (rd / "render.json").exists() and any(rd.glob("final_*.mp4"))


def prune_render(rd: str | os.PathLike[str], *, keep_finals: bool = True, job: Job | None = None,
                 reason: str = "") -> int:
    """Delete a render's intermediates (and its finals when ``keep_finals`` is False); keeps the timeline, QA,
    manifests and critique inputs. Returns bytes freed."""
    rd = Path(rd)
    freed, removed = storage.prune_render_intermediates(rd, keep_finals=keep_finals)
    if removed:
        with contextlib.suppress(OSError, ValueError):
            prev = json.loads((rd / "pruned.json").read_text())
            prev["reason"] = reason or prev.get("reason", "")
            (rd / "pruned.json").write_text(json.dumps(prev, indent=2))
        if job is not None:
            trace_event(job, "prune", render=rd.name, freed_mb=round(freed / 1e6, 1), files=removed[:30],
                        reason=reason or None)
    return freed


def prune_overlay_cache(job: Job, *, keep: int = 1) -> int:
    """Drop overlay-cache files no render links to any more (keep the ``keep`` newest)."""
    return storage.prune_overlay_cache(job, keep=keep)


def clean_incomplete_renders(job: Job) -> list[str]:
    """Remove render directories a crash left without ``render.json`` (they cannot be trusted or resumed)."""
    out = []
    for n in job.render_numbers():
        rd = job.render_dir(n)
        if not (rd / "render.json").exists():
            shutil.rmtree(rd, ignore_errors=True)
            out.append(rd.name)
    if out:
        trace_event(job, "prune", note="removed incomplete renders", renders=out)
    return out


def _reclaim_own(job: Job) -> Callable[[int], int]:
    """Reclaim callback for a render of ``job``: every existing render's intermediates (renders are QA'd before the
    next one starts, so none is still needed) and unlinked overlay-cache entries."""
    def run(_short: int) -> int:
        return storage.reclaim_job(job, reason="a new render needs the space")
    return run


def render_full(job: Job, doc: CutDocument, index: TakeIndex | None = None, *, preview: bool = False,
                min_free: int | None = None, on_wait: Callable[[], None] | None = None) -> Path:
    """Render ``doc`` into a new ``renders/r{n}/`` (full quality unless ``preview``) inside the machine-wide render
    slot, after a disk budget check (reclaim first; the lean A-roll intermediate when ProRes HQ does not fit); a
    failed render's directory is removed."""
    from studio.compile.master import render_document

    index = index or job.load_index()
    try:
        dur_s = max(0.5, doc.estimated_duration_us(index) / 1e6)
    except Exception:
        dur_s = max(0.5, index.media.duration_us / 1e6)
    fps = float(index.media.fps) if index.media.fps else 30.0
    what = "a preview render" if preview else "a full-quality render"
    with storage.render_slot(on_wait=on_wait):
        codec: str | None = None
        if preview:
            need = storage.render_need_bytes(dur_s, fps=fps, preview=True)
            storage.ensure_space(job.root, need, what=what, reclaim=_reclaim_own(job), work_dir=job.root.parent,
                                 exclude=job.root, floor=min_free)
        else:
            codec, need = storage.choose_aroll_codec(job.root, dur_s, width=1080, height=1920, fps=fps,
                                                     reclaim=_reclaim_own(job), work_dir=job.root.parent,
                                                     exclude=job.root, what=what, floor=min_free)
            if codec != "prores_hq":
                trace_event(job, "disk", note="lean A-roll intermediate (ProRes HQ would not fit)",
                            need_gb=round(need / 1e9, 2), free_gb=round(storage.free_bytes(job.root) / 1e9, 2))
        storage.ensure_mezz(job)
        before = set(job.render_numbers())
        t0 = time.monotonic()
        try:
            res = render_document(job, doc, index, preview=preview, aroll_codec=codec)
        except BaseException:
            for n in set(job.render_numbers()) - before:
                shutil.rmtree(job.render_dir(n), ignore_errors=True)
            raise
    trace_event(job, "render_done", render=res.render_dir.name, doc_version=doc.version, preview=preview,
                seconds=round(time.monotonic() - t0, 1), aroll_codec=codec)
    return res.render_dir


# ============================================================================================ state
@dataclass
class LoopConfig:
    guard: int = DEFAULT_GUARD
    winless_stop: int = WINLESS_STOP
    min_free: int | None = None
    prune: bool = True
    keep_loser_finals: bool = True
    final_watch: bool = True
    low_disk_bytes: int = 3_000_000_000  # below this, losers lose their finals too and the champion its intermediates
    #: a review that fails (after the critics' own retries) is retried this many more times before the champion
    #: ships NOT REVIEWED, waiting ``critique_backoff_s`` between attempts
    critique_retries: int = 2
    critique_backoff_s: tuple[float, ...] = (60.0, 180.0)
    #: a pairwise whose judge calls failed is redone once after this wait
    pairwise_retry_s: float = 60.0
    #: confirmed P0/P1 notes from the closing watch open one more round (once)
    final_watch_round: bool = True
    #: recorded hook alternates are built from the champion, rendered and compared pairwise (SKILL.md directive 8)
    variants: bool = True
    max_variants: int = 2


@dataclass
class RoundRecord:
    round: int
    champion: str
    champion_doc: int
    challenger: str | None = None
    challenger_doc: int | None = None
    # win | loss | tie | no_change | invariant_fail | replaced_failing | disk | error | unreviewed | undecided
    outcome: str = ""
    notes: int = 0
    challenger_passed: bool | None = None
    pairwise: dict[str, Any] | None = None
    summary: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    kind: str = "revise"  # revise | variant | final_watch
    variant: str | None = None  # the hook alternate a variant round built


@dataclass
class LoopState:
    champion_render: str | None = None
    champion_doc: int | None = None
    champion_passed: bool = False
    round: int = 0
    winless: int = 0
    rounds: list[RoundRecord] = field(default_factory=list)
    done: bool = False
    stop_reason: str = ""
    final_watch: dict[str, Any] | None = None
    #: the current champion has a complete critique (False: it ships NOT REVIEWED, with ``review_note``)
    reviewed: bool = False
    review_note: str = ""
    #: questions the Director asked the critics, per render name (answered when that render is critiqued)
    questions: dict[str, list[str]] = field(default_factory=dict)
    final_watch_round_used: bool = False
    pending_notes: list[dict[str, Any]] = field(default_factory=list)
    #: hook alternates tried: {hook, render, doc_version, outcome, passed}
    variants: list[dict[str, Any]] = field(default_factory=list)
    variants_done: bool = False

    @classmethod
    def load(cls, job: Job) -> LoopState:
        p = job.logs_dir / _STATE
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        rounds = [RoundRecord(**{k: v for k, v in r.items() if k in RoundRecord.__dataclass_fields__})
                  for r in data.pop("rounds", [])]
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(rounds=rounds, **known)

    def save(self, job: Job) -> None:
        from studio.jobs import write_json_atomic

        write_json_atomic(job.logs_dir / _STATE, asdict(self))


@dataclass
class LoopResult:
    champion_render: Path
    champion_doc: int
    rounds: list[RoundRecord]
    stop_reason: str
    champion_passed: bool
    final_watch: dict[str, Any] | None = None
    reviewed: bool = True
    review_note: str = ""
    alternates: list[dict[str, Any]] = field(default_factory=list)


def critique_record_complete(rec: dict[str, Any]) -> bool:
    """A saved critique that really reviewed the render (records from before ``complete`` existed count only when
    their rubric pass did not fail)."""
    if rec.get("notes") is None:
        return False
    if "complete" in rec:
        return bool(rec["complete"])
    return not any("frame judge failed" in str(e) for e in rec.get("errors") or [])


# ============================================================================================ loop
class ChampionLoop:
    """The render → critique → revise → pairwise loop over one :class:`~studio.agent.director.Director`.

    ``renderer(doc) -> render_dir``, ``qa(render_dir) -> bool`` (invariants passed), ``critic(render_dir, doc) ->
    notes`` and ``judge(champion_dir, challenger_dir) -> {"winner": "a"|"b"|"tie", …}`` default to the real
    implementations and are injectable (tests). A critic that raises (the real one raises
    :class:`~studio.agent.critics.CritiqueUnavailable` when its rubric pass fails) is retried; it is never read as
    "nothing to change"."""

    def __init__(self, job: Job, director: Director, index: TakeIndex | None = None, *,
                 settings: Settings | None = None, rounds: int | None = None, config: LoopConfig | None = None,
                 panel: JudgePanel | None = None,
                 renderer: Callable[[CutDocument], Path] | None = None,
                 qa: Callable[[Path], bool] | None = None,
                 critic: Callable[[Path, CutDocument], list[dict[str, Any]]] | None = None,
                 judge: Callable[[Path, Path], dict[str, Any]] | None = None,
                 final_watch: Callable[[Path], dict[str, Any]] | None = None,
                 sleep: Callable[[float], None] | None = None):
        self.job = job
        self.director = director
        self.index = index or director.index
        self.settings = settings or get_settings()
        self.config = config or LoopConfig()
        if rounds is not None:
            self.config.guard = int(rounds)
        self._panel = panel
        self.renderer = renderer or self._render
        self.qa = qa or self._qa
        self.critic = critic or self._critique
        self.judge = judge or self._pairwise
        self.final_watch = final_watch or self._final_watch
        self._sleep = sleep or time.sleep
        self._fix_notes: list[dict[str, Any]] = []
        self.state = LoopState.load(job)

    # ------------------------------------------------------------------ defaults
    @property
    def panel(self) -> JudgePanel:
        if self._panel is None:
            from studio.agent.critics import default_panel

            active = self.director.model_label if getattr(self.director, "using_fallback", False) else ""
            self._panel = default_panel(self.settings, director=self.director.spec, avoid_models=[active])
        return self._panel

    def _render(self, doc: CutDocument) -> Path:
        return render_full(self.job, doc, self.index, min_free=self.config.min_free)

    def _qa(self, rd: Path) -> bool:
        from studio.qa.invariants import evaluate_render

        return evaluate_render(self.job, rd, settings=self.settings).passed

    def _critique(self, rd: Path, doc: CutDocument) -> list[dict[str, Any]]:
        from studio.agent import critics

        return critics.critique(self.job, doc, self.index, rd, settings=self.settings, panel=self.panel,
                                questions=self.state.questions.get(rd.name) or None)

    def _pairwise(self, a: Path, b: Path) -> dict[str, Any]:
        from studio.agent import critics

        return critics.pairwise(self.job, a, b, settings=self.settings, panel=self.panel,
                                fix_notes=self._fix_notes or None)

    def _final_watch(self, rd: Path) -> dict[str, Any]:
        from studio.agent import critics

        return critics.attention_check(self.job, rd, settings=self.settings, panel=self.panel)

    # ------------------------------------------------------------------ helpers
    def _rd(self, name: str) -> Path:
        return self.job.renders_dir / name

    def _save(self) -> None:
        self.state.save(self.job)

    def _low_disk(self) -> bool:
        return free_bytes(self.job.root) < self.config.low_disk_bytes

    def _prune_loser(self, rd: Path, reason: str) -> None:
        if not self.config.prune:
            return
        keep = self.config.keep_loser_finals and not self._low_disk()
        prune_render(rd, keep_finals=keep, job=self.job, reason=reason)
        prune_overlay_cache(self.job)

    def _after_qa(self, rd: Path) -> None:
        """Once its QA has run, a render's intermediates are no longer read (critique and pairwise use the finals and
        the saved QA): drop them so a job never holds more than its finals between renders."""
        if not self.config.prune:
            return
        prune_render(rd, keep_finals=True, job=self.job, reason="QA done (the loop reads only finals and QA)")
        prune_overlay_cache(self.job)

    def _prune_orphans(self) -> None:
        """A crash between a challenger's render and its verdict leaves a render no round refers to: drop its
        intermediates (the round is redone)."""
        if not self.config.prune:
            return
        st = self.state
        known = {st.champion_render, *(r.challenger for r in st.rounds)}
        for n in self.job.render_numbers():
            rd = self.job.render_dir(n)
            if rd.name not in known and any((rd / x).exists() for x in ("aroll.mov", "overlays.mov", "mix.wav")):
                prune_render(rd, keep_finals=True, job=self.job, reason="orphaned by an interrupted round")

    def _critique_record(self, rd: Path) -> dict[str, Any]:
        p = self.job.critique_dir / rd.name / "notes.json"
        with contextlib.suppress(OSError, ValueError):
            return json.loads(p.read_text(encoding="utf-8"))
        return {}

    def _notes_for(self, rd: Path, doc: CutDocument) -> list[dict[str, Any]]:
        """The champion's critique (cached when complete); retried with backoff when the critics fail. Raises the
        last error when every attempt failed."""
        rec = self._critique_record(rd)
        if rec.get("doc_version") == doc.version and critique_record_complete(rec):
            self.state.reviewed, self.state.review_note = True, ""
            return list(rec["notes"])
        attempts = 1 + max(0, self.config.critique_retries)
        last: BaseException | None = None
        for k in range(attempts):
            try:
                notes = list(self.critic(rd, doc))
            except Exception as e:  # CritiqueUnavailable or any critic failure: never "nothing to change"
                last = e
                trace_event(self.job, "loop", what="critique_error", render=rd.name, attempt=k + 1,
                            error=f"{type(e).__name__}: {str(e)[:300]}")
                if k + 1 < attempts:
                    back = self.config.critique_backoff_s
                    self._sleep(back[min(k, len(back) - 1)] if back else 0.0)
                continue
            self.state.reviewed, self.state.review_note = True, ""
            return notes
        assert last is not None
        raise last

    def _fixable(self, notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Confirmed P0/P1 notes (the revision is a fix, not taste, when it answers these)."""
        return [n for n in notes if n.get("severity") in ("P0", "P1") and (n.get("confirmed_by") or
                                                                        n.get("by") == "metrics")]

    def _revision_brief(self, rd: Path, notes: list[dict[str, Any]]) -> str:
        from studio.agent.critics import format_notes

        rec = self._critique_record(rd)
        parts = ["CRITIC NOTES (confirmed P0/P1 first; unconfirmed ones were downgraded to P2):",
                 format_notes(notes) or "(no notes)"]
        if rec.get("verdict"):
            parts.append(f"Frame judge's verdict on this render: {rec['verdict']}.")
        from studio.agent.critics import rubric_failed

        nos = [r for r in rec.get("rubric") or [] if rubric_failed(r)]
        if nos:
            parts.append("Rubric checks that FAILED:\n" + "\n".join(
                f"- {r.get('question')}" + (f" [{', '.join(r.get('refs') or [])}]" if r.get("refs") else "")
                + (f": {r.get('evidence')}" if r.get("evidence") else "") for r in nos[:12]))
        if rec.get("answers"):
            parts.append("Answers to your questions: " + " | ".join(map(str, rec["answers"])))
        naive = rec.get("naive") or {}
        if naive:
            parts.append(f"Naive first-time viewer: reactions {naive.get('reactions')}; wanted to swipe at "
                         f"{naive.get('swipe_moments')}; remembered \"{naive.get('remembered_sentence', '')}\".")
        if rec.get("keep"):
            parts.append("Moments that work and must survive: " + "; ".join(map(str, rec["keep"])))
        if rec.get("advice"):
            parts.append("Metric advice (priors, not gates):\n" + "\n".join(f"- {a}" for a in rec["advice"][:20]))
        w = rec.get("watcher") or {}
        if w and not w.get("ran"):
            parts.append(f"Watcher: {w.get('reason')}")
        prev = [r for r in self.state.rounds if r.champion == rd.name and r.challenger]
        if prev:
            parts.append("Earlier revisions of this champion and how they fared:")
            for r in prev:
                why = ""
                if r.pairwise:
                    reasons = [v.get("reason", "") for v in r.pairwise.get("votes", []) if v.get("reason")]
                    regress = (r.pairwise.get("regressions") or {}).get("b") or []
                    why = ("; judges: " + " | ".join(reasons[:4])) if reasons else ""
                    if regress:
                        why += "; measured regressions: " + ", ".join(regress)
                parts.append(f"- round {r.round}: v{r.challenger_doc} → {r.outcome}{why}. {r.summary}")
            parts.append("Try a different fix (often a smaller one), or accept the champion.")
        return "\n".join(parts)

    def _attachments(self, rd: Path) -> list[tuple[str, Path]]:
        """The critics' own evidence of the champion (the images the notes are about) for the revising Director."""
        d = self.job.critique_dir / rd.name / "frames"
        out = []
        for name, label in (("captions_phone.png", "Critic sheet: caption pages at phone scale under the UI mask"),
                            ("hook.png", "Critic sheet: the champion's first 3 s every 0.25 s"),
                            ("captions_1.png", "Critic sheet: caption pages (1) under the UI mask")):
            if (d / name).exists():
                out.append((label, d / name))
        return out

    def _questions_of_revision(self) -> list[str]:
        return list(getattr(self.director.session, "critic_questions", None) or [])

    # ------------------------------------------------------------------ run
    def run(self) -> LoopResult:
        st = self.state
        clean_incomplete_renders(self.job)
        self._prune_orphans()
        if st.champion_render is not None and not render_complete(self._rd(st.champion_render)):
            st.champion_render = None  # the champion's render is gone: render again
        if st.champion_render is None:
            doc = self.director.session.doc
            rd = self.renderer(doc)
            st.champion_render, st.champion_doc = rd.name, doc.version
            st.champion_passed = bool(self.qa(rd))
            st.reviewed = False
            qs = self._first_questions()
            if qs:
                st.questions[rd.name] = qs
            self._after_qa(rd)
            self._save()
            trace_event(self.job, "loop", what="champion", render=rd.name, doc_version=doc.version,
                        passed=st.champion_passed)
        while True:
            self._rounds()
            if st.stop_reason.startswith("critique unavailable"):
                break
            if self.config.variants and not st.variants_done:
                self._variant_rounds()
            if not st.reviewed:
                self._closing_review()
            champ = self._rd(st.champion_render)  # type: ignore[arg-type]
            if not self.config.final_watch or st.final_watch is not None:
                break
            try:
                st.final_watch = self.final_watch(champ)
            except Exception as e:
                st.final_watch = {"ran": False, "verdict": f"final watch failed: {type(e).__name__}: {e}"}
            self._save()
            confirmed = list((st.final_watch or {}).get("confirmed") or [])
            if (confirmed and self.config.final_watch_round and not st.final_watch_round_used
                    and st.round < self.config.guard):
                # the closing watch found a confirmed problem: one more round on it, then watch again
                st.final_watch_round_used = True
                st.pending_notes = confirmed
                st.final_watch = None
                st.done = False
                st.winless = max(0, self.config.winless_stop - 1)
                self._save()
                trace_event(self.job, "loop", what="final_watch_round", notes=len(confirmed))
                continue
            break
        champ = self._rd(st.champion_render)  # type: ignore[arg-type]
        # the champion keeps its session doc in step (chat and delivery read it)
        with contextlib.suppress(Exception):
            self.director.session.reload_doc(st.champion_doc)
        return LoopResult(champion_render=champ, champion_doc=int(st.champion_doc or 0), rounds=list(st.rounds),
                          stop_reason=st.stop_reason, champion_passed=st.champion_passed, final_watch=st.final_watch,
                          reviewed=st.reviewed, review_note=st.review_note,
                          alternates=[v for v in st.variants if v.get("alternate")])

    def _first_questions(self) -> list[str]:
        """Questions the Director wrote for the critics at finalize (answered in the first review)."""
        with contextlib.suppress(Exception):
            for r in reversed(self.director.state.completed):
                if r.stage == "finalize" and getattr(r, "questions", None):
                    return list(r.questions)
        return []

    def _rounds(self) -> None:
        st = self.state
        while not st.done:
            if st.round >= self.config.guard:
                self._stop(f"round guard ({self.config.guard}) reached")
                break
            k = st.round + 1
            champ_rd = self._rd(st.champion_render)  # type: ignore[arg-type]
            champ_doc = self.job.load_doc(st.champion_doc)
            rec = RoundRecord(round=k, champion=champ_rd.name, champion_doc=champ_doc.version, started_at=time.time())
            try:
                notes = self._notes_for(champ_rd, champ_doc)
            except Exception as e:
                st.reviewed = False
                st.review_note = f"{type(e).__name__}: {str(e)[:300]}"
                rec.outcome, rec.summary = "unreviewed", f"critique failed: {st.review_note}"
                self._finish_round(rec, winless=True)
                self._stop(f"critique unavailable after {1 + self.config.critique_retries} attempts: the champion "
                           f"ships NOT REVIEWED ({st.review_note})")
                break
            if st.pending_notes:
                rec.kind = "final_watch"
                notes = [*st.pending_notes, *notes]
                st.pending_notes = []
            rec.notes = len(notes)
            if not notes and st.champion_passed:
                rec.outcome, rec.summary = "no_change", "critics found nothing to change"
                self._finish_round(rec, winless=True)
                self._stop("critics found nothing to change")
                break
            self._fix_notes = self._fixable(notes)
            try:
                revision = self.director.revise(self._revision_brief(champ_rd, notes), base_version=champ_doc.version,
                                                round_no=k, champion_render=champ_rd,
                                                attachments=self._attachments(champ_rd))
            except Exception as e:  # the Director is unavailable: the champion ships
                rec.outcome, rec.summary = "error", f"revision failed: {type(e).__name__}: {str(e)[:300]}"
                trace_event(self.job, "loop", what="revision_error", round=k, error=str(e)[:500])
                self._finish_round(rec, winless=True)
                self._stop(f"the Director could not revise ({type(e).__name__}); the champion ships")
                break
            rec.summary = revision.summary
            if not revision.changed:
                rec.outcome = "no_change"
                self._finish_round(rec, winless=True)
                if st.winless >= self.config.winless_stop:
                    self._stop(f"{st.winless} winless rounds")
                continue
            if not self._challenge(rec, champ_rd, revision, fix=bool(self._fix_notes)):
                break

    def _challenge(self, rec: RoundRecord, champ_rd: Path, revision: Any, *, fix: bool) -> bool:
        """Render, QA and judge a challenger; decide the round. Returns False when the loop must stop."""
        st = self.state
        k = rec.round
        rec.challenger_doc = revision.doc.version
        try:
            ch_rd = self.renderer(revision.doc)
        except DiskSpaceError as e:
            rec.outcome, rec.summary = "disk", str(e)
            self._finish_round(rec, winless=True)
            self._stop(f"disk: {e}")
            return False
        except Exception as e:
            rec.outcome, rec.summary = "error", f"challenger render failed: {type(e).__name__}: {e}"
            trace_event(self.job, "loop", what="render_error", round=k, error=str(e)[:500])
            self._finish_round(rec, winless=True)
            if st.winless >= self.config.winless_stop:
                self._stop(f"{st.winless} winless rounds")
            return True
        rec.challenger = ch_rd.name
        passed = bool(self.qa(ch_rd))
        self._after_qa(ch_rd)
        rec.challenger_passed = passed
        qs = self._questions_of_revision()
        if qs:
            st.questions[ch_rd.name] = qs
        if st.champion_passed and not passed:
            rec.outcome = "invariant_fail"
            self._prune_loser(ch_rd, "challenger failed an invariant")
            self._finish_round(rec, winless=True)
            if st.winless >= self.config.winless_stop:
                self._stop(f"{st.winless} winless rounds")
            return True
        if not fix:
            self._fix_notes = []
        pw = self.judge(champ_rd, ch_rd)
        if pw.get("incomplete"):  # a judge call failed after its retries: decide it again rather than tie
            self._sleep(self.config.pairwise_retry_s)
            pw = self.judge(champ_rd, ch_rd)
        rec.pairwise = pw
        winner = pw.get("winner", "tie")
        promote = winner == "b" or (not st.champion_passed and passed and winner != "a")
        if promote:
            rec.outcome = "win" if winner == "b" else "replaced_failing"
            old = champ_rd
            st.champion_render, st.champion_doc, st.champion_passed = ch_rd.name, revision.doc.version, passed
            st.reviewed = False  # the new champion has not been critiqued yet
            if self.config.prune:
                keep_old = rec.kind == "variant" or (self.config.keep_loser_finals and not self._low_disk())
                prune_render(old, keep_finals=keep_old, job=self.job, reason="replaced as champion")
                prune_overlay_cache(self.job)
            self._finish_round(rec, winless=False)
            trace_event(self.job, "loop", what="new_champion", round=k, render=ch_rd.name,
                        doc_version=revision.doc.version)
        else:
            rec.outcome = "loss" if winner == "a" else ("undecided" if pw.get("incomplete") else "tie")
            if rec.kind == "variant":  # a losing variant is still delivered as an alternate: keep its finals
                prune_render(ch_rd, keep_finals=True, job=self.job, reason=f"variant lost round {k}")
            else:
                self._prune_loser(ch_rd, f"lost round {k} ({rec.outcome})")
            self._finish_round(rec, winless=True)
            if rec.kind != "variant" and st.winless >= self.config.winless_stop:
                self._stop(f"{st.winless} winless rounds")
        return True

    def _variant_rounds(self) -> None:
        """SKILL.md directive 8: each hook alternate the Director recorded is built from the champion by the Director,
        rendered at full quality and judged pairwise against the champion (the unanimous rule); the winner ships and
        the other is delivered as an alternate."""
        st = self.state
        tried = {v.get("hook") for v in st.variants}
        champ_doc = self.job.load_doc(st.champion_doc)
        alts = [h for h in champ_doc.hook_alternates if h.id not in tried][: max(0, self.config.max_variants)]
        for h in alts:
            if st.round >= self.config.guard + self.config.max_variants:
                break
            champ_rd = self._rd(st.champion_render)  # type: ignore[arg-type]
            champ_doc = self.job.load_doc(st.champion_doc)
            k = st.round + 1
            rec = RoundRecord(round=k, champion=champ_rd.name, champion_doc=champ_doc.version, started_at=time.time(),
                              kind="variant", variant=h.id)
            ranges = "; ".join(f"{r.from_word}-{r.to_word}" for r in h.ranges)
            words = " / ".join(
                " ".join(self.index.word(w).display() for w in self.index.word_ids(r.from_word, r.to_word))[:160]
                for r in h.ranges)
            prompt = (f"RENDER REVIEW — VARIANT {h.id} (round {k}). You recorded hook alternate {h.id} ({h.note or 'no '
                      'note'}): open with {ranges} (\"{words}\")"
                      + (f" and the title \"{h.title_text}\"" if h.title_text else "") + ". Build exactly that "
                      "alternate from the champion: the open plays those words, then the champion's story continues "
                      "from where it would after them; repair captions, inserts, texts, SFX and music anchors that "
                      "hang off changed word IDs; change nothing else. It is rendered at full quality and judged "
                      "pairwise against the champion (positions swapped); the winner ships and the other is delivered "
                      "as an alternate. If the champion already opens this way, finish_stage without ops.")
            try:
                revision = self.director.revise(prompt, base_version=champ_doc.version, round_no=k,
                                                champion_render=champ_rd, raw_prompt=True)
            except Exception as e:
                rec.outcome, rec.summary = "error", f"variant build failed: {type(e).__name__}: {str(e)[:300]}"
                self._finish_round(rec, winless=False)
                st.variants.append({"hook": h.id, "outcome": "error", "note": rec.summary})
                continue
            rec.summary = revision.summary
            if not revision.changed:
                rec.outcome = "no_change"
                self._finish_round(rec, winless=False)
                st.variants.append({"hook": h.id, "outcome": "no_change", "note": revision.summary[:300]})
                continue
            self._fix_notes = []
            before = st.champion_render
            if not self._challenge(rec, champ_rd, revision, fix=False):
                break
            won = st.champion_render != before
            alt_render = before if won else rec.challenger
            alt_doc = champ_doc.version if won else rec.challenger_doc
            st.variants.append({"hook": h.id, "render": rec.challenger, "doc_version": rec.challenger_doc,
                                "outcome": rec.outcome, "passed": rec.challenger_passed,
                                "alternate": {"render": alt_render, "doc_version": alt_doc,
                                              "label": f"without_{h.id}" if won else h.id}
                                if alt_render and (won or rec.challenger_passed) else None})
            self._save()
        st.variants_done = True
        self._save()

    def _closing_review(self) -> None:
        """A champion promoted in the last round (or by a variant) has not been critiqued: review it once so the
        report says what the critics think of the shipped version (no revision follows)."""
        st = self.state
        champ_rd = self._rd(st.champion_render)  # type: ignore[arg-type]
        with contextlib.suppress(Exception):
            self._notes_for(champ_rd, self.job.load_doc(st.champion_doc))
        if not st.reviewed and not st.review_note:
            st.review_note = "the closing review of the shipped version failed"
        self._save()

    def _finish_round(self, rec: RoundRecord, *, winless: bool) -> None:
        st = self.state
        rec.ended_at = time.time()
        st.rounds.append(rec)
        st.round = rec.round
        if rec.kind != "variant":
            st.winless = st.winless + 1 if winless else 0
        self._save()
        trace_event(self.job, "loop", what="round", round=rec.round, kind=rec.kind, outcome=rec.outcome,
                    champion=st.champion_render, challenger=rec.challenger, winless=st.winless)

    def _stop(self, reason: str) -> None:
        self.state.done = True
        self.state.stop_reason = reason
        self._save()
        trace_event(self.job, "loop", what="stop", reason=reason, champion=self.state.champion_render,
                    rounds=self.state.round)


def champion_loop(job: Job, doc: CutDocument, index: TakeIndex, *, rounds: int | None = None,
                  spec: ModelSpec | None = None, settings: Settings | None = None, director: Director | None = None,
                  **kwargs: Any) -> CutDocument:
    """Iterate to convergence; returns the champion document (always shippable)."""
    from studio.agent.director import Director

    d = director or Director(job, index, spec=spec, settings=settings)
    if d.session.doc.version != doc.version:
        d.session.reload_doc(doc.version)
    res = ChampionLoop(job, d, index, settings=settings, rounds=rounds, **kwargs).run()
    return job.load_doc(res.champion_doc)
