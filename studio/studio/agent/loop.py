"""Champion loop (ARCHITECTURE §9, ``skills/editing/critique.md`` "How to decide" 9–10).

::

    render the Director's document at full quality → invariants → champion
    repeat:
        critique the champion (packet + frame judge + watcher + confirmation)      (cached per champion)
        Director revision from the notes (branches from the champion's version; any op family)
        no render-relevant change → a winless round
        else render the challenger at full quality → invariants
             pairwise(champion, challenger): two judges × both orders; the challenger wins only when it wins
             both orders for both judges and no metric regresses; a tie keeps the champion — unless the
             champion fails an invariant and the challenger passes (then it replaces it unless it loses)
        stop after 2 consecutive winless rounds (runaway guard 12; ``rounds`` overrides the guard)
    the champion always ships; a closing whole-video attention check is recorded.

Disk: full renders write ProRes intermediates (~26 MB/s at 1080p30). Free space is checked before every
render (:func:`ensure_disk`, default floor 1.5 GB, ``STUDIO_MIN_FREE_GB``); a loser's intermediates are
pruned as soon as its round is decided (:func:`prune_render`), and the overlay cache keeps only what a
render still links to. A crashed render directory (no ``render.json``) is removed on resume.

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

from studio.agent.providers import trace_event
from studio.config import get_settings

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
MIN_FREE_BYTES = 1_500_000_000
_STATE = "loop_state.json"

#: Render intermediates (never deliverables): ProRes A-roll/overlay layers, WAV mixes and stems, conformed stills.
INTERMEDIATES: tuple[str, ...] = ("aroll.mov", "aroll_preview.mp4", "overlays.mov", "mix.wav", "mix_nomusic.wav",
                                  "stems", "_video_*.mp4", "still_*.mkv", "*.cover_overlay.mov", "cover_overlay*.mov")


class DiskSpaceError(RuntimeError):
    """Not enough free disk for a render (the message says how much is free and what to do)."""


def free_bytes(path: str | os.PathLike[str]) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def _min_free(default: int = MIN_FREE_BYTES) -> int:
    v = os.environ.get("STUDIO_MIN_FREE_GB")
    if v:
        with contextlib.suppress(ValueError):
            return int(float(v) * 1e9)
    return default


def ensure_disk(path: str | os.PathLike[str], *, min_free: int | None = None, what: str = "a render") -> int:
    """Raise :class:`DiskSpaceError` when ``path``'s volume has less than ``min_free`` bytes free."""
    need = _min_free() if min_free is None else min_free
    free = free_bytes(path)
    if free < need:
        raise DiskSpaceError(
            f"only {free / 1e9:.2f} GB free on the work volume; {what} needs at least {need / 1e9:.1f} GB "
            "(full-quality renders write ProRes intermediates of ~26 MB/s at 1080p30). Free some space, then resume "
            "with `studio edit <job_dir>`.")
    return free


def render_complete(rd: Path) -> bool:
    return (rd / "render.json").exists() and any(rd.glob("final_*.mp4"))


def prune_render(rd: str | os.PathLike[str], *, keep_finals: bool = True, job: Job | None = None,
                 reason: str = "") -> int:
    """Delete a render's intermediates (and its finals when ``keep_finals`` is False); keeps the timeline, QA,
    manifests and critique inputs. Returns bytes freed."""
    rd = Path(rd)
    if not rd.is_dir():
        return 0
    freed = 0
    removed: list[str] = []
    pats = list(INTERMEDIATES) + ([] if keep_finals else ["final_*.mp4", "cover.jpg"])
    for pat in pats:
        for p in rd.glob(pat):
            try:
                if p.is_dir():
                    size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                    shutil.rmtree(p)
                else:
                    size = p.stat().st_size
                    p.unlink()
            except OSError:
                continue
            freed += size
            removed.append(p.name)
    if removed:
        with contextlib.suppress(OSError):
            prev = json.loads((rd / "pruned.json").read_text()) if (rd / "pruned.json").exists() else {}
            prev.setdefault("removed", []).extend(removed)
            prev["bytes"] = int(prev.get("bytes", 0)) + freed
            prev["reason"] = reason or prev.get("reason", "")
            (rd / "pruned.json").write_text(json.dumps(prev, indent=2))
        if job is not None:
            trace_event(job, "prune", render=rd.name, freed_mb=round(freed / 1e6, 1), files=removed[:30],
                        reason=reason or None)
    return freed


def prune_overlay_cache(job: Job, *, keep: int = 1) -> int:
    """Drop overlay-cache files no render links to any more (keep the ``keep`` newest)."""
    d = job.renders_dir / "_overlay_cache"
    if not d.is_dir():
        return 0
    files = sorted((p for p in d.glob("*.mov") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    freed = 0
    for k, p in enumerate(files):
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_nlink <= 1 and k >= keep:
            freed += st.st_size
            p.unlink(missing_ok=True)
    return freed


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


def render_full(job: Job, doc: CutDocument, index: TakeIndex | None = None, *, preview: bool = False,
                min_free: int | None = None) -> Path:
    """Render ``doc`` into a new ``renders/r{n}/`` (full quality unless ``preview``) after a disk check; a failed
    render's directory is removed."""
    from studio.compile.master import render_document

    ensure_disk(job.root, min_free=min_free, what="a preview render" if preview else "a full-quality render")
    index = index or job.load_index()
    before = set(job.render_numbers())
    t0 = time.monotonic()
    try:
        res = render_document(job, doc, index, preview=preview)
    except BaseException:
        for n in set(job.render_numbers()) - before:
            shutil.rmtree(job.render_dir(n), ignore_errors=True)
        raise
    trace_event(job, "render_done", render=res.render_dir.name, doc_version=doc.version, preview=preview,
                seconds=round(time.monotonic() - t0, 1))
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


@dataclass
class RoundRecord:
    round: int
    champion: str
    champion_doc: int
    challenger: str | None = None
    challenger_doc: int | None = None
    outcome: str = ""  # win | loss | tie | no_change | invariant_fail | replaced_failing | disk | error
    notes: int = 0
    challenger_passed: bool | None = None
    pairwise: dict[str, Any] | None = None
    summary: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0


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


# ============================================================================================ loop
class ChampionLoop:
    """The render → critique → revise → pairwise loop over one :class:`~studio.agent.director.Director`.

    ``renderer(doc) -> render_dir``, ``qa(render_dir) -> bool`` (invariants passed), ``critic(render_dir, doc) ->
    notes`` and ``judge(champion_dir, challenger_dir) -> {"winner": "a"|"b"|"tie", …}`` default to the real
    implementations and are injectable (tests)."""

    def __init__(self, job: Job, director: Director, index: TakeIndex | None = None, *,
                 settings: Settings | None = None, rounds: int | None = None, config: LoopConfig | None = None,
                 panel: JudgePanel | None = None,
                 renderer: Callable[[CutDocument], Path] | None = None,
                 qa: Callable[[Path], bool] | None = None,
                 critic: Callable[[Path, CutDocument], list[dict[str, Any]]] | None = None,
                 judge: Callable[[Path, Path], dict[str, Any]] | None = None,
                 final_watch: Callable[[Path], dict[str, Any]] | None = None):
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
        self.state = LoopState.load(job)

    # ------------------------------------------------------------------ defaults
    @property
    def panel(self) -> JudgePanel:
        if self._panel is None:
            from studio.agent.critics import default_panel

            self._panel = default_panel(self.settings, director=self.director.spec)
        return self._panel

    def _render(self, doc: CutDocument) -> Path:
        return render_full(self.job, doc, self.index, min_free=self.config.min_free)

    def _qa(self, rd: Path) -> bool:
        from studio.qa.invariants import evaluate_render

        return evaluate_render(self.job, rd, settings=self.settings).passed

    def _critique(self, rd: Path, doc: CutDocument) -> list[dict[str, Any]]:
        from studio.agent import critics

        return critics.critique(self.job, doc, self.index, rd, settings=self.settings, panel=self.panel)

    def _pairwise(self, a: Path, b: Path) -> dict[str, Any]:
        from studio.agent import critics

        return critics.pairwise(self.job, a, b, settings=self.settings, panel=self.panel)

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
        rec = self._critique_record(rd)
        if rec.get("notes") is not None and rec.get("doc_version") == doc.version:
            return list(rec["notes"])
        return list(self.critic(rd, doc))

    def _revision_brief(self, rd: Path, notes: list[dict[str, Any]]) -> str:
        from studio.agent.critics import format_notes

        rec = self._critique_record(rd)
        parts = ["CRITIC NOTES (confirmed P0/P1 first; unconfirmed ones were downgraded to P2):",
                 format_notes(notes) or "(no notes)"]
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
            self._save()
            trace_event(self.job, "loop", what="champion", render=rd.name, doc_version=doc.version,
                        passed=st.champion_passed)
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
                rec.outcome, rec.summary = "error", f"critique failed: {type(e).__name__}: {e}"
                trace_event(self.job, "loop", what="critique_error", round=k, error=str(e)[:300])
                self._finish_round(rec, winless=True)
                if st.winless >= self.config.winless_stop:
                    self._stop(f"{st.winless} winless rounds (critique unavailable)")
                continue
            rec.notes = len(notes)
            if not notes and st.champion_passed:
                rec.outcome, rec.summary = "no_change", "critics found nothing to change"
                self._finish_round(rec, winless=True)
                self._stop("critics found nothing to change")
                break
            try:
                revision = self.director.revise(self._revision_brief(champ_rd, notes), base_version=champ_doc.version,
                                                round_no=k)
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
            rec.challenger_doc = revision.doc.version
            try:
                ch_rd = self.renderer(revision.doc)
            except DiskSpaceError as e:
                rec.outcome, rec.summary = "disk", str(e)
                self._finish_round(rec, winless=True)
                self._stop(f"disk: {e}")
                break
            except Exception as e:
                rec.outcome, rec.summary = "error", f"challenger render failed: {type(e).__name__}: {e}"
                trace_event(self.job, "loop", what="render_error", round=k, error=str(e)[:500])
                self._finish_round(rec, winless=True)
                if st.winless >= self.config.winless_stop:
                    self._stop(f"{st.winless} winless rounds")
                continue
            rec.challenger = ch_rd.name
            passed = bool(self.qa(ch_rd))
            rec.challenger_passed = passed
            if st.champion_passed and not passed:
                rec.outcome = "invariant_fail"
                self._prune_loser(ch_rd, "challenger failed an invariant")
                self._finish_round(rec, winless=True)
                if st.winless >= self.config.winless_stop:
                    self._stop(f"{st.winless} winless rounds")
                continue
            pw = self.judge(champ_rd, ch_rd)
            rec.pairwise = pw
            winner = pw.get("winner", "tie")
            promote = winner == "b" or (not st.champion_passed and passed and winner != "a")
            if promote:
                rec.outcome = "win" if winner == "b" else "replaced_failing"
                old = champ_rd
                st.champion_render, st.champion_doc, st.champion_passed = ch_rd.name, revision.doc.version, passed
                if self.config.prune:
                    prune_render(old, keep_finals=self.config.keep_loser_finals and not self._low_disk(),
                                 job=self.job, reason="replaced as champion")
                    prune_overlay_cache(self.job)
                self._finish_round(rec, winless=False)
                trace_event(self.job, "loop", what="new_champion", round=k, render=ch_rd.name,
                            doc_version=revision.doc.version)
            else:
                rec.outcome = "loss" if winner == "a" else "tie"
                self._prune_loser(ch_rd, f"lost round {k} ({rec.outcome})")
                self._finish_round(rec, winless=True)
                if st.winless >= self.config.winless_stop:
                    self._stop(f"{st.winless} winless rounds")
        champ = self._rd(st.champion_render)  # type: ignore[arg-type]
        if self.config.final_watch and st.final_watch is None:
            try:
                st.final_watch = self.final_watch(champ)
            except Exception as e:
                st.final_watch = {"verdict": f"final watch failed: {type(e).__name__}: {e}"}
            self._save()
        # the champion keeps its session doc in step (chat and delivery read it)
        with contextlib.suppress(Exception):
            self.director.session.reload_doc(st.champion_doc)
        return LoopResult(champion_render=champ, champion_doc=int(st.champion_doc or 0), rounds=list(st.rounds),
                          stop_reason=st.stop_reason, champion_passed=st.champion_passed, final_watch=st.final_watch)

    def _finish_round(self, rec: RoundRecord, *, winless: bool) -> None:
        st = self.state
        rec.ended_at = time.time()
        st.rounds.append(rec)
        st.round = rec.round
        st.winless = st.winless + 1 if winless else 0
        self._save()
        trace_event(self.job, "loop", what="round", round=rec.round, outcome=rec.outcome, champion=st.champion_render,
                    challenger=rec.challenger, winless=st.winless)

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
