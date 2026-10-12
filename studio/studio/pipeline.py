"""End-to-end orchestration used by :mod:`studio.cli`.

``edit``: ingest → Take Index → Director stages (brief → story → fine cut → reframe → b-roll → captions →
sound → colour → finalize) → champion loop (full-quality render → invariants → critique → revision →
pairwise; the champion always ships) → deliver (``final_<platform>.mp4``, ``final_nomusic.mp4``,
``cover.jpg``, ``captions.srt``, the document JSON and ``report.md``). Every step is resumable: run
``studio edit <job_dir>`` after a crash and finished steps are skipped (ingest and index by their files,
Director stages by ``logs/director_state.json``, the loop by ``logs/loop_state.json``).

``chat``: the Director applies a creator instruction to the champion (user intent wins: no pairwise veto),
the result is rendered at full quality and must pass the invariants (the Director gets two chances to
fix a failure); a critic sanity note is recorded, then the new render replaces the champion and is
delivered.

Tiers (docs/standard-tier-plan.md): the paragraphs above describe the ``director`` tier. ``edit(tier=...)`` /
``--tier`` / ``STUDIO_TIER`` (default ``standard``) select the path. A job records its ``tier`` and ``planner`` next
to its ``director`` in ``job.json`` so a resume or a chat stays in the tier it was edited with; flags given now win.

The ``standard`` tier (:func:`_edit_standard`) is the deterministic engine + one planner call + one render + the
invariants + code-only repair, shipped NOT REVIEWED: ingest with the mezzanine capped at the delivery canvas and
30 fps (1920 high for a portrait source, 1080 for a landscape one; the lean encoder) → Take Index → digest (plus the
colour line when the mezzanine exists) → one ``planner`` call → code plan (the decision, or none) → validation and
the radio gate (a blocked decision plan drops to the pure-code plan; a blocked pure-code plan fails the edit) →
``render_full(profile="standard")`` → invariants → one code-only repair (intermediates are kept until it has run)
→ when it still fails and the plan came from a decision, the pure-code plan's render as the second attempt → the
champion recorded in the loop state (finished, NOT REVIEWED, ``review_note`` "standard tier: code QA only") →
``report.md`` with a Plan section → one final delivered (no ``final_nomusic.mp4`` without music). A render that
still fails is not delivered. The tier's hard limits are checked before anything is paid for: a source over 10 min is
refused before ingest, a take no plan can bring under 90 s before the planner call, and a plan over 90 s before the
render. Model spend is the planner call alone (plus ASR), capped by ``max_usd`` (default $0.15). Progress is kept in
``logs/standard.json``: a resume of a finished standard edit only re-delivers, and a crashed one reuses the
recorded planner answer instead of paying for it again. The delivered champion keeps its lean A-roll and mixes
(reclaimable when another render needs the space) and the job its lean mezzanine, so a standard chat
(:func:`_chat_standard`, :mod:`studio.plan.chat`: one planner call → ``{ops, summary}`` → one retry on rejected
ops) re-renders only the overlay and the master when the cut is unchanged, and a full standard render otherwise.

Upgrade: ``studio edit <standard job> --tier director`` hands the job to the Director at its review rounds (detected
from the recorded tier or from the loop state still holding the unreviewed standard champion, so an interrupted
upgrade resumes as one): the
standard champion is kept in the loop state (finished flag and stop reason cleared), the Director's nine stages are
recorded as done on the standard document (``director_state.json``, with the code plan's summary on ``finalize``),
so the champion loop critiques the standard render and the Director revises it; the first pass is never paid
again and ``--max-usd`` caps the rounds. Without a complete standard render the Director runs its stages on the
standard document (the session opens the latest version). The upgraded job keeps the standard tier's mezzanine.

At most ``STUDIO_MAX_CONCURRENT_EDITS`` (default 2) edits/chats run machine-wide (file locks under
``STUDIO_SLOTS_DIR``, default the system temp dir); full-quality renders are slow and memory-hungry.
All functions return small result dataclasses (paths only; never keys).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from studio.agent.critics import JudgePanel
    from studio.agent.providers import ModelSpec
    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

PathLike = str | os.PathLike[str]


@dataclass
class EditResult:
    job_dir: Path
    finals: dict[str, Path] = field(default_factory=dict)  # platform -> final_<platform>.mp4
    extras: dict[str, Path] = field(default_factory=dict)  # nomusic, cover, srt, doc, report …
    doc_version: int | None = None
    render: str | None = None
    rounds: int = 0
    stop_reason: str = ""
    invariants_passed: bool | None = None
    #: False when the critics could not review the shipped version (it ships NOT REVIEWED; see review_note)
    reviewed: bool | None = None
    review_note: str = ""
    alternates: dict[str, Path] = field(default_factory=dict)  # label -> final of a delivered variant
    estimated_cost: str = ""  # model spend at list prices (studio.agent.cost); the report has the breakdown
    #: the tier the edit ran in ("director": the reviewed champion loop; "standard": one planner call, code QA only)
    tier: str = "director"
    #: wall-clock seconds of the standard tier's work after the Take Index (plan, render, QA, repair, delivery)
    compute_seconds: float | None = None


@dataclass
class ChatResult:
    job_dir: Path
    summary: str = ""
    doc_version: int | None = None
    finals: dict[str, Path] = field(default_factory=dict)
    changes: list[str] = field(default_factory=list)
    invariants_passed: bool | None = None
    critic_note: str = ""
    estimated_cost: str = ""


def _cost_record(job: Job) -> dict[str, Any]:
    """The job's spend at list prices as a dict (model calls, ASR, paid isolation), for ``logs/standard.json``."""
    try:
        from studio.agent.cost import job_cost

        return job_cost(job).to_dict()
    except Exception:  # pragma: no cover - accounting must never break a delivery
        return {}


def _cost_line(job: Job) -> str:
    """The job's model spend at list prices, for the CLI summary (never fails a result)."""
    try:
        from studio.agent.cost import format_cost, job_cost

        return format_cost(job_cost(job))
    except Exception:  # pragma: no cover - accounting must never break a delivery
        return ""


@dataclass
class IndexResult:
    job_dir: Path
    index_path: Path
    words: int = 0
    duration_s: float = 0.0


@dataclass
class RenderResult:
    job_dir: Path
    render_dir: Path
    finals: dict[str, Path] = field(default_factory=dict)


@dataclass
class QaResult:
    job_dir: Path
    passed: bool
    results: list[dict[str, Any]] = field(default_factory=list)
    report: Path | None = None


class PipelineError(RuntimeError):
    """A pipeline step cannot run (bad input, missing prerequisite)."""


# ============================================================================================ helpers
def _settings(settings: Settings | None) -> Settings:
    from studio.config import get_settings

    return settings if settings is not None else get_settings()


def _open(job_dir: PathLike, settings: Settings | None) -> Job:
    from studio.jobs import Job

    return Job.open(job_dir, work_dir=_settings(settings).work_dir)


@contextlib.contextmanager
def edit_slot(*, max_concurrent: int | None = None, poll_s: float = 10.0,
              on_wait: Callable[[], None] | None = None) -> Iterator[int]:
    """Hold one of ``max_concurrent`` machine-wide edit slots (``fcntl`` file locks) for the ``with`` body."""
    import fcntl

    n = max(1, int(max_concurrent or os.environ.get("STUDIO_MAX_CONCURRENT_EDITS") or 2))
    d = Path(os.environ.get("STUDIO_SLOTS_DIR") or tempfile.gettempdir()) / "studio-edit-slots"
    d.mkdir(parents=True, exist_ok=True)
    waited = False
    while True:
        for i in range(n):
            fh = open(d / f"slot{i}.lock", "a+")  # noqa: SIM115 - held for the with-body
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fh.close()
                continue
            try:
                yield i
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
            return
        if not waited and on_wait is not None:
            on_wait()
        waited = True
        time.sleep(poll_s)


#: The standard tier's mezzanine frame-rate cap (a 60 fps source is conformed to 30; the output is 30 fps anyway).
STANDARD_MAX_FPS = 30


def standard_ingest_caps(source: PathLike, settings: Settings | None = None) -> dict[str, int]:
    """``ingest`` caps for a standard-tier edit of ``source``: ``max_height`` is the delivery canvas in the source's
    display orientation (1920 for a portrait or square picture, 1080 for a landscape one, so a 4K source becomes a
    1080p mezzanine either way) and ``max_fps`` is 30. Probed first because the cap depends on the orientation."""
    from studio.media.probe import probe

    info = probe(source, settings=settings)
    return {"max_height": 1920 if info.height >= info.width else 1080, "max_fps": STANDARD_MAX_FPS}


def _standard_source_seconds(job: Job, source: str | None, settings: Settings) -> float | None:
    """The source's length for the standard tier's limit: the job's media info when it was ingested, else a probe of
    the source (or the job's kept original); None when neither can be read (ingest then reports the file itself)."""
    if job.media_info_path.exists():
        with contextlib.suppress(Exception):
            return job.load_media_info().duration_us / 1e6
    candidates = [c for c in (source, job.original_path) if c]
    for c in candidates:
        if not Path(c).is_file():
            continue
        try:
            from studio.media.probe import probe

            return probe(c, settings=settings).duration_us / 1e6
        except Exception as e:
            job.trace("pipeline_note", note=f"source length not probed ({type(e).__name__}: {str(e)[:200]})")
            return None
    return None


def _check_standard_source(job: Job, source: str | None, settings: Settings) -> None:
    """Refuse a standard-tier edit of a source longer than :data:`studio.plan.digest.STANDARD_MAX_SOURCE_S` (10 min),
    before ingest, ASR or the planner cost anything: index ASR, QA ASR and the render all grow with length, so a long
    upload would break the tier's price without a word. The Director tier takes it."""
    from studio.plan.digest import STANDARD_MAX_SOURCE_S

    secs = _standard_source_seconds(job, source, settings)
    if secs is not None and secs > STANDARD_MAX_SOURCE_S:
        job.trace("pipeline_note", note=f"standard tier refused: source {secs:.0f} s > {STANDARD_MAX_SOURCE_S} s")
        raise PipelineError(f"standard tier: the source is {secs / 60:.1f} min long, over the tier's "
                            f"{STANDARD_MAX_SOURCE_S // 60} min limit (its ASR, render and QA cost grow with length). "
                            "Trim the take, or edit it with `--tier director` (with `--max-usd` to cap the spend).")


def _note_uncapped_mezzanine(job: Job, say: Callable[[str], None]) -> None:
    """A standard edit of a job ingested without the tier's caps (``studio index`` in the Director tier, or a job
    ingested before tiers) renders from a full-size mezzanine: say so once (the compute line is higher). The job is
    not re-ingested here: its Take Index (frame grid, face track) was built on that mezzanine."""
    with contextlib.suppress(Exception):
        info = job.load_media_info()
        cap_h = 1920 if info.height >= info.width else 1080
        if info.conform is None and (info.height > cap_h or float(info.fps) > STANDARD_MAX_FPS + 0.01):
            note = (f"this job's mezzanine was ingested without the standard tier's caps ({info.width}x{info.height} "
                    f"at {float(info.fps):.2f} fps, not {cap_h} px high at {STANDARD_MAX_FPS} fps): the render costs "
                    "more compute than the tier's estimate")
            job.trace("pipeline_note", note=note)
            say(note)


def _ingest_for_edit(job: Job, source: str, settings: Settings, say: Callable[[str], None], *,
                     standard: bool = False) -> None:
    """Ingest inside the machine-wide heavy-write slot, keeping room for the edit's renders when choosing the
    mezzanine codec (ProRes HQ → ProRes 422 → lean) and reclaiming idle jobs first. ``standard``: the standard tier's
    caps (:func:`standard_ingest_caps`; the lean mezzanine by design)."""
    import dataclasses

    from studio import storage
    from studio.media.ingest import IngestError, IngestOptions, ingest

    opts = dataclasses.replace(IngestOptions.from_env(), reserve_render=True, reclaim_dir=str(job.root.parent))
    caps: dict[str, int] = {}
    if standard:
        try:
            caps = standard_ingest_caps(source, settings)
        except Exception as e:  # ingest probes again and reports an unreadable file itself
            job.trace("pipeline_note", note=f"standard caps not probed ({type(e).__name__}: {str(e)[:200]})")
        if caps:
            say(f"ingest caps (standard tier): at most {caps['max_height']} px high, {caps['max_fps']} fps")
    with storage.render_slot(on_wait=lambda: say("waiting for another edit's render or ingest to finish")):
        try:
            ingest(source, job, settings=settings, options=opts, **caps)
        except IngestError as e:
            if "insufficient disk" in str(e):
                raise storage.DiskSpaceError(str(e)) from e
            raise


def _after_delivery(job: Job) -> None:
    """A delivered job keeps its finals, documents and index; its mezzanine is dropped (``studio chat`` and
    ``studio render`` rebuild it from ``media/original.*``) unless ``STUDIO_KEEP_MEZZ=1``."""
    from studio import storage

    keep = (os.environ.get("STUDIO_KEEP_MEZZ") or "").strip().lower() in ("1", "true", "yes")
    storage.reclaim_job(job, drop_mezz=not keep, reason="delivered")


def _link_or_copy(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


def director_spec_for(provider: str | None, model: str | None, key_env: str | None,
                      settings: Settings, *, effort: str | None = None) -> ModelSpec | None:
    """The Director's :class:`ModelSpec` from CLI flags (None = the house Director). ``effort`` (a job's recorded
    effort on a resume) wins over the environment's."""
    if provider is None:
        return None
    from studio.agent.providers import spec_from_cli

    prov = provider.strip().lower()
    if model is None:
        if prov in ("anthropic", "claude"):
            model = settings.director_model
        else:
            raise PipelineError(f"--director-provider {provider} needs --director-model")
    effort = effort or os.environ.get("STUDIO_DIRECTOR_EFFORT") or settings.director_effort or "max"
    return spec_from_cli(provider, model, key_env, settings=settings, effort=effort)


def _house_director_record(settings: Settings) -> dict[str, Any]:
    """What a house-Director job records (``job.json``) so a resume or a chat keeps the model and effort it was
    edited with when the environment's ``STUDIO_DIRECTOR_MODEL``/``_EFFORT`` differ. The key stays the house key:
    no key name is stored (``key_env`` None), so the spec is not BYOK and fallbacks stay on."""
    effort = os.environ.get("STUDIO_DIRECTOR_EFFORT") or settings.director_effort or "max"
    return {"provider": settings.director_provider, "model": settings.director_model, "key_env": None,
            "effort": effort, "house": True}


def _recorded_effort(job: Job) -> str | None:
    d = _job_meta(job).get("director") or {}
    return d.get("effort") or None


def _resolve_tier(tier: str | None, job: Job | None, settings: Settings) -> str:
    """The tier an edit or chat runs in: the flag given now, else the tier ``job`` was edited with, else
    ``Settings.tier`` (``STUDIO_TIER``). A job that recorded a Director but no tier was edited before tiers existed:
    it is a Director job, so a resume or a chat never drops it into the standard path."""
    from studio.config import TIERS

    if tier is None and job is not None:
        m = _job_meta(job)
        tier = m.get("tier") or ("director" if m.get("director") else None)
    tier = (tier or settings.tier).strip().lower()
    if tier not in TIERS:
        raise PipelineError(f"unknown tier {tier!r} (one of {', '.join(TIERS)})")
    return tier


def _planner_record(settings: Settings, model: str | None = None) -> dict[str, Any]:
    """What a job records (``job.json`` ``planner``, next to ``director``): the standard tier's planner model and
    effort, so a resume or a chat keeps them when the environment's ``STUDIO_PLANNER_MODEL``/``_EFFORT`` differ;
    ``--planner-model`` given now wins. The key stays the house key: no key name is stored (``key_env`` None)."""
    return {"provider": "anthropic", "model": model or settings.planner_model, "key_env": None,
            "effort": settings.planner_effort, "house": True}


def _recorded_planner(job: Job) -> dict[str, Any] | None:
    rec = _job_meta(job).get("planner")
    return dict(rec) if rec and rec.get("model") else None


def planner_spec_for(model: str | None, settings: Settings, *, provider: str | None = None,
                     key_env: str | None = None, effort: str | None = None) -> ModelSpec:
    """The standard tier's planner :class:`ModelSpec`: the house planner (``Settings.planner_model`` at
    ``planner_effort``) unless ``model`` (``--planner-model``, or a job's recorded planner) says otherwise. A recorded
    ``effort`` wins over the environment's, like :func:`director_spec_for`; ``key_env`` names a creator's key (BYOK)."""
    from studio.agent.providers import spec_from_cli

    return spec_from_cli(provider or "anthropic", model or settings.planner_model, key_env, settings=settings,
                         effort=effort or settings.planner_effort, role="planner")


def _spec_from_planner_record(rec: dict[str, Any], settings: Settings) -> ModelSpec:
    """The planner a job recorded (see :func:`_planner_record`), with its effort winning over the environment's."""
    return planner_spec_for(rec.get("model"), settings, provider=rec.get("provider"), key_env=rec.get("key_env"),
                            effort=rec.get("effort"))


def _planner_spec(job: Job, planner_model: str | None, settings: Settings) -> ModelSpec:
    """The planner for a chat on a standard job: ``--planner-model`` given now wins, else the job's recorded planner,
    else the house planner."""
    if planner_model is not None:
        return planner_spec_for(planner_model, settings)
    return _spec_from_planner_record(_recorded_planner(job) or {}, settings)


def _director_flags(job: Job, provider: str | None, model: str | None, key_env: str | None,
                    house: bool) -> tuple[str | None, str | None, str | None]:
    """The Director flags for a resumed edit or a chat: flags given now win; otherwise the ones the job was edited
    with (``job.json`` keeps the provider, model and the NAME of the key's env var, never the key). A job edited
    with a creator's key never silently switches to the house model and billing: without the key it refuses, and
    ``--house`` switches explicitly."""
    if provider is not None or house:
        return provider, model, key_env
    d = _job_meta(job).get("director") or {}
    if not d.get("provider"):
        return None, None, None
    return d.get("provider"), d.get("model"), d.get("key_env")


def _job_for(video: Path, settings: Settings, meta: dict[str, Any]) -> tuple[Job, bool]:
    """``(job, resumed)``: a directory holding ``job.json`` is resumed, a video starts a new job."""
    from studio.jobs import Job, resolve_job_dir

    if video.is_dir():
        video = resolve_job_dir(video)
        if not (video / "job.json").exists():
            raise PipelineError(f"{video} is a directory but not a job directory")
        return Job.open(video, work_dir=settings.work_dir), True
    job = Job.create(work_dir=settings.work_dir, meta=meta)
    return job, False


def _job_meta(job: Job) -> dict[str, Any]:
    return dict(job.meta.get("meta") or {})


# ============================================================================================ callbacks
def make_preview_callback(job: Job, index: TakeIndex, settings: Settings) -> Callable[..., Any]:
    """``render_preview(session, scope)`` for the Director: a whole-document preview render (the proxy, a fast
    preset), its contact sheet and light metrics; intermediates are pruned right away."""

    def render_preview(session: Any, scope: str) -> Any:
        from studio.agent.loop import prune_render, render_full
        from studio.agent.tools import PreviewResult
        from studio.compile.models import Timeline
        from studio.qa.invariants import primary_final
        from studio.qa.metrics import measure
        from studio.qa.report import make_contact_sheet

        doc = session.doc
        rd = render_full(job, doc, index, preview=True)
        final = primary_final(rd, doc)
        if final is None:
            return PreviewResult(summary=f"Preview {rd.name} produced no final file.")
        tl = Timeline.load(rd / "timeline.json")
        images = []
        with contextlib.suppress(Exception):
            images.append(make_contact_sheet(job, final, rd / "contact_sheet.jpg", timeline=tl, index=index))
        metrics: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            pk = measure(job, tl, final, doc=doc, index=index, render_dir=rd, light=True, asr=False,
                         settings=settings)
            metrics = pk.summary()
        prune_render(rd, keep_finals=True, job=job, reason="preview")
        from studio.agent.loop import prune_overlay_cache

        prune_overlay_cache(job, keep=1)
        scope_note = "" if scope in ("full", "", None) else \
            f" Scope '{scope}' was not honoured: previews always render the whole document."
        return PreviewResult(
            summary=(f"Preview {rd.name} of document v{doc.version}: {float(tl.duration):.1f} s, {len(tl.segments)} "
                     f"pieces, {len(tl.captions)} caption pages (proxy quality; judge picture detail on "
                     f"view_frames source='source' hires).{scope_note}"),
            video_path=final, timeline_path=rd / "timeline.json", images=images, metrics=metrics)

    return render_preview


def make_critique_callback(job: Job, index: TakeIndex, settings: Settings,
                           panel: JudgePanel | None = None, director: ModelSpec | None = None) -> Callable[..., Any]:
    """``critique(session, questions)`` for the Director: the critics answer questions about the latest render (a
    proxy preview is labelled as such: the doctrine judges the final encode). The panel is built against the
    Director's own spec, so ``same_family`` is right for a BYOK Director."""

    def critique_cb(session: Any, questions: list[str]) -> Any:
        from studio.agent import critics

        if session.last_render is None:
            return "No render yet: call render_preview first, then ask the critics about it."
        rd = Path(session.last_render).parent
        man = {}
        with contextlib.suppress(Exception):
            import json as _json

            man = _json.loads((rd / "render.json").read_text(encoding="utf-8"))
        proxy = bool(man.get("preview"))
        pn = panel or critics.default_panel(settings, director=director)
        try:
            notes = critics.critique(job, None, index, rd, questions=questions, settings=settings, panel=pn,
                                     label=f"{'preview' if proxy else 'render'}_{rd.name}", confirm=True)
        except critics.CritiqueUnavailable as e:
            return f"The critics could not review {rd.name} ({e}). Nothing was reviewed: do not read this as approval."
        if proxy:
            notes = [{**n, "evidence": (str(n.get("evidence", "")) + " (reviewed on a proxy preview, not the final "
                                        "encode: fidelity notes are provisional)").strip()} for n in notes]
        return notes

    return critique_cb


# ============================================================================================ deliver
def deliver(job: Job, render_dir: Path, doc: CutDocument, *, out: PathLike | None = None,
            report: Path | None = None, alternates: Sequence[tuple[str, Path]] = (),
            nomusic: bool = True) -> tuple[dict[str, Path], dict[str, Path]]:
    """Put the champion's deliverables in ``<job>/deliver/`` (and ``out``): finals per platform, the no-music
    master, cover, SRT, the document JSON and the report; the finals of ``alternates`` (label, render dir: variants
    the champion beat, or the version a winning variant replaced) go under ``alternates/<label>/``. Returns
    ``(finals, extras)``; extras carries ``alt:<label>`` entries. ``nomusic=False`` leaves ``final_nomusic.mp4`` out
    (the standard tier passes it when the document has no music: that file would be the final again, byte for
    byte in picture and the same mix)."""
    ddir = job.root / "deliver"
    if ddir.exists():
        for p in ddir.iterdir():
            if p.is_file():
                p.unlink()
            elif p.is_dir() and p.name == "alternates":
                shutil.rmtree(p, ignore_errors=True)
    ddir.mkdir(parents=True, exist_ok=True)
    finals: dict[str, Path] = {}
    extras: dict[str, Path] = {}
    for f in sorted(render_dir.glob("final_*.mp4")):
        name = f.stem.removeprefix("final_")
        if name == "nomusic" and not nomusic:
            continue
        dst = _link_or_copy(f, ddir / f.name)
        if name == "nomusic":
            extras["nomusic"] = dst
        else:
            finals[name] = dst
    for name, key in (("cover.jpg", "cover"), ("captions.srt", "srt")):
        p = render_dir / name
        if p.exists():
            extras[key] = _link_or_copy(p, ddir / name)
    doc_path = ddir / f"edit_v{doc.version}.json"
    doc_path.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    extras["doc"] = doc_path
    tl = render_dir / "timeline.json"
    if tl.exists():
        extras["timeline"] = _link_or_copy(tl, ddir / "timeline.json")
    if report is not None and report.exists():
        extras["report"] = report
    alt_files: list[tuple[str, Path]] = []
    for label, rd in alternates:
        for f in sorted(Path(rd).glob("final_*.mp4")):
            if f.stem == "final_nomusic":
                continue
            dst = _link_or_copy(f, ddir / "alternates" / label / f.name)
            extras[f"alt:{label}"] = dst
            alt_files.append((label, dst))
    if out is not None:
        od = Path(out).expanduser()
        od.mkdir(parents=True, exist_ok=True)
        if (od / "alternates").is_dir():
            shutil.rmtree(od / "alternates", ignore_errors=True)
        for p in [*finals.values(), *(v for k, v in extras.items() if not k.startswith("alt:"))]:
            _link_or_copy(p, od / p.name)
        for label, p in alt_files:
            _link_or_copy(p, od / "alternates" / label / p.name)
        from studio.jobs import JOB_POINTER

        (od / JOB_POINTER).write_text(str(job.root) + "\n", encoding="utf-8")
        extras["out"] = od
    return finals, extras


# ============================================================================================ edit
def edit(video: PathLike, *, brief: str | None = None, style: str | None = None, platform: str | None = None,
         director_provider: str | None = None, director_model: str | None = None,
         director_key_env: str | None = None, house: bool = False, rounds: int | None = None,
         max_usd: float | None = None, tier: str | None = None, planner_model: str | None = None,
         out: PathLike | None = None,
         settings: Settings | None = None, creator_media: Sequence[PathLike] = (),
         asr_provider: str | None = None, director_model_override: Any = None,
         panel: JudgePanel | None = None, loop_kwargs: dict[str, Any] | None = None,
         log: Callable[[str], None] | None = None) -> EditResult:
    """Edit ``video`` end to end (or resume the job directory ``video``). ``out`` receives copies of the
    deliverables. ``tier`` (``standard`` | ``director``) defaults to the job's recorded tier on a resume, else
    ``Settings.tier``; ``planner_model`` overrides the standard tier's planner. ``max_usd`` caps the model spend (the
    Director's review rounds; the standard tier's planner call). ``director_model_override`` /
    ``panel`` / ``loop_kwargs`` inject models and loop callbacks (tests)."""
    from studio import storage
    from studio.agent.director import Director
    from studio.agent.loop import (
        ChampionLoop,
        LoopState,
        clean_incomplete_renders,
        is_standard_state,
        prune_overlay_cache,
        prune_render,
    )
    from studio.perception.index import build_index
    from studio.qa.report import write_report

    s = _settings(settings)
    say = log or (lambda _m: None)
    src = Path(video).expanduser().resolve()
    meta = {"source_name": src.name, "source_path": str(src), "brief": brief, "style": style,
            "platform": platform or "tiktok",
            "creator_media": [str(Path(m).expanduser().resolve()) for m in creator_media]}
    with edit_slot(on_wait=lambda: say("waiting for a free edit slot (at most 2 edits run at once)")), \
            contextlib.ExitStack() as stack:
        job, resumed = _job_for(src, s, meta)
        stack.enter_context(storage.job_activity(job))
        m = _job_meta(job)
        if resumed:  # flags given now win; otherwise the job's own settings
            brief = brief if brief is not None else m.get("brief")
            style = style if style is not None else m.get("style")
            platform = platform or m.get("platform")
            creator_media = list(creator_media) or list(m.get("creator_media") or [])
        platform = platform or "tiktok"
        tier = _resolve_tier(tier, job if resumed else None, s)
        if tier == "standard":  # the tier's hard source limit, before anything is recorded, ingested or paid for
            _check_standard_source(job, (m.get("source_path") if resumed else None) or str(src), s)
        flags_given = director_provider is not None or house
        if resumed:
            director_provider, director_model, director_key_env = _director_flags(
                job, director_provider, director_model, director_key_env, house)
        # the job records its Director (BYOK flags, or the house model and effort it started with) so a resume or
        # a chat keeps them when the environment changes; flags given now win
        if resumed and not flags_given:
            director_rec = _job_meta(job).get("director") or _house_director_record(s)
            effort = _recorded_effort(job)
        elif director_provider:
            director_rec, effort = {"provider": director_provider, "model": director_model,
                                    "key_env": director_key_env, "effort": None}, None
        else:
            director_rec, effort = _house_director_record(s), None
        # the planner is recorded the same way (the standard tier's one model): a resume keeps the job's planner
        # unless --planner-model is given now
        if planner_model is not None or not resumed:
            planner_rec = _planner_record(s, planner_model)
        else:
            planner_rec = _recorded_planner(job) or _planner_record(s)
        job.update_meta(brief=brief, style=style, platform=platform,
                        creator_media=[str(x) for x in creator_media], director=director_rec, tier=tier,
                        planner=planner_rec)
        job.trace("stage", stage="pipeline.edit", resumed=resumed, tier=tier)
        say(f"job {job.root} ({tier} tier" + (", resumed)" if resumed else ")"))

        # 1 — ingest (a mezzanine reclaimed after an earlier run is rebuilt on its own)
        have_media = job.media_info_path.exists() and job.audio_path.exists()
        if have_media and not job.mezz_path.exists() and job.original_path is not None:
            storage.ensure_mezz(job, settings=s, log=say)
        elif not (have_media and job.mezz_path.exists()):
            source = m.get("source_path") if resumed else str(src)
            if not source or not Path(source).is_file():
                orig = job.original_path
                if orig is None:
                    raise PipelineError(f"job {job.id} has no source video to ingest")
                source = str(orig)
            storage.ensure_space(job.root, 0, what="ingest (the mezzanine)", work_dir=job.root.parent,
                                 exclude=job.root)
            say("ingest")
            _ingest_for_edit(job, source, s, say, standard=tier == "standard")
        if tier == "standard":
            _note_uncapped_mezzanine(job, say)
        # 2 — Take Index
        if not job.index_path.exists():
            say("perception (Take Index)")
            index = build_index(job, asr_provider=asr_provider, settings=s)
        else:
            index = job.load_index()
        # fail fast: the edit's renders must fit (lean intermediates, after reclaiming) before any Director work
        clean_incomplete_renders(job)
        need = storage.edit_need_bytes(index, aroll="lean")
        storage.ensure_space(job.root, need, what="this edit's renders (checked before the Director starts)",
                             reclaim=lambda _short: storage.reclaim_job(job, reason="edit start"),
                             work_dir=job.root.parent, exclude=job.root)
        if tier == "standard":
            return _edit_standard(job, index, brief=brief, style=style, platform=platform,
                                  planner=_standard_planner(planner_rec, s), settings=s, out=out, say=say,
                                  resumed=resumed, max_usd=max_usd)
        # the upgrade: the Director takes over the standard champion. job.json already says "director" when an
        # earlier upgrade attempt stopped before this point (no disk, a failed mezzanine rebuild, Ctrl-C), so the loop
        # state decides too: it still holds the unreviewed standard champion until reopen_for_review clears it, and a
        # completed hand-over (review note cleared) is never seeded twice
        if resumed and (m.get("tier") == "standard" or is_standard_state(LoopState.load(job))):
            say(_resume_from_standard(job))
        # 3 — Director stages
        spec = _spec_or_refuse(director_provider, director_model, director_key_env, s, effort=effort)
        director = Director(job, index, spec=spec, settings=s, model=director_model_override, brief=brief,
                            style=style, platforms=(platform,),
                            render_preview=make_preview_callback(job, index, s),
                            critique=make_critique_callback(job, index, s, panel, spec),
                            creator_media=creator_media)
        say(f"Director ({director.model_label})")
        director.run()
        # 4 — champion loop
        say("render and critique (champion loop)")
        if max_usd is None and os.environ.get("STUDIO_MAX_MODEL_USD"):
            with contextlib.suppress(ValueError):
                max_usd = float(os.environ["STUDIO_MAX_MODEL_USD"])
        loop = ChampionLoop(job, director, index, settings=s, rounds=rounds, max_usd=max_usd, panel=panel,
                            **(loop_kwargs or {}))
        res = loop.run()
        # 5 — deliver + report
        doc = job.load_doc(res.champion_doc)
        rep = None
        try:
            rep = write_report(job, render_dir=res.champion_render, settings=s)
        except Exception as e:
            job.trace("pipeline_note", note=f"report failed: {type(e).__name__}: {e}")
        alts = [(a["label"], job.renders_dir / a["render"]) for a in
                (v.get("alternate") for v in res.alternates) if a and (job.renders_dir / a["render"]).is_dir()]
        finals, extras = deliver(job, res.champion_render, doc, out=out, report=rep, alternates=alts)
        prune_render(res.champion_render, keep_finals=True, job=job, reason="delivered")
        prune_overlay_cache(job, keep=0)
        _after_delivery(job)
        _delete_uploads(director)
        job.update_meta(champion_render=res.champion_render.name, champion_doc=res.champion_doc,
                        reviewed=res.reviewed, review_note=res.review_note)
        job.trace("stage", stage="pipeline.edit", status="done", render=res.champion_render.name,
                  doc_version=res.champion_doc, rounds=len(res.rounds), stop=res.stop_reason, reviewed=res.reviewed)
        if not res.reviewed:
            say(f"NOT REVIEWED: the critics could not review the shipped version ({res.review_note})")
    return EditResult(job_dir=job.root, finals=finals, extras=extras, doc_version=res.champion_doc,
                      render=res.champion_render.name, rounds=len(res.rounds), stop_reason=res.stop_reason,
                      estimated_cost=_cost_line(job), tier=tier,
                      invariants_passed=res.champion_passed, reviewed=res.reviewed, review_note=res.review_note,
                      alternates={k.removeprefix("alt:"): v for k, v in extras.items() if k.startswith("alt:")})


def _spec_or_refuse(provider: str | None, model: str | None, key_env: str | None, settings: Settings, *,
                    effort: str | None = None) -> Any:
    """The Director spec; a BYOK job whose key is missing refuses (never falls back to house billing)."""
    try:
        return director_spec_for(provider, model, key_env, settings, effort=effort)
    except Exception as e:
        if key_env:
            raise PipelineError(f"this job's Director is {provider}/{model} with the creator's key from ${key_env}, "
                                f"which is not available ({type(e).__name__}). Set ${key_env}, or pass --house to "
                                "switch this job to the house Director explicitly.") from None
        raise


# ============================================================================================ standard tier
#: What the standard tier records about its run (the report's Plan section reads it; a resume reuses it).
STANDARD_LOG = "logs/standard.json"
#: The critic note a standard-tier chat carries (no critic watches the result: the invariants are the gate).
STANDARD_NO_CRITIC = "standard tier: no critic review (code QA only)"


def _standard_planner(rec: dict[str, Any], settings: Settings) -> ModelSpec:
    """The planner a standard job recorded; a creator's key (BYOK) that is missing refuses instead of falling back to
    house billing, like the Director's."""
    try:
        return _spec_from_planner_record(rec, settings)
    except Exception as e:
        if rec.get("key_env"):
            raise PipelineError(f"this job's planner is {rec.get('provider')}/{rec.get('model')} with the creator's "
                                f"key from ${rec['key_env']}, which is not available ({type(e).__name__}). Set "
                                f"${rec['key_env']}, or pass --planner-model to switch to the house planner.") from None
        raise


def _standard_record(job: Job) -> dict[str, Any]:
    rec = job.load_json(STANDARD_LOG, default={})
    return dict(rec) if isinstance(rec, dict) else {}


def _save_standard(job: Job, rec: dict[str, Any], **fields: Any) -> None:
    rec.update(fields)
    job.save_json(STANDARD_LOG, rec)


def _colour_line(job: Job, index: TakeIndex) -> str | None:
    """The digest's colour line (``ColorMeasurement.summary``) from the mezzanine, or None without one (the
    measurement must never read the original instead) or when it cannot be measured."""
    if not job.mezz_path.exists():
        return None
    try:
        from studio.compile.color import measure_color

        return measure_color(job, index, None).summary()
    except Exception as e:
        job.trace("pipeline_note", note=f"colour not measured for the digest ({type(e).__name__}: {str(e)[:200]})")
        return None


def _paid_answer(logged: Any) -> bool:
    """A ``logs/plan.json`` record worth reusing on a crash-resume: a decision the model gave, or an answer the model
    gave and validation rejected after its retry (paid for; asking again would pay twice for the same rejection). A
    record of a call that never produced an answer (a provider 5xx, a refusal, a missing key, a crash) is not: the
    provider may be back or the key set, and nothing was paid for."""
    if not isinstance(logged, dict) or "decision" not in logged:
        return False
    if logged.get("decision"):
        return True
    return not logged.get("errors") and int(logged.get("attempts") or 0) >= 1 \
        and any(logged.get("rejections_by_attempt") or [])


def _standard_decision(job: Job, index: TakeIndex, digest: Any, planner: ModelSpec, settings: Settings, *,
                       brief: str | None, style: str | None, reuse: bool, say: Callable[[str], None],
                       max_usd: float | None = None) -> tuple[Any, dict[str, Any]]:
    """``(decision or None, record)``: the one planner call, or (``reuse``, a crash-resume) the answer it already
    gave, read back from ``logs/plan.json`` so it is never paid for twice (:func:`_paid_answer`: a call that got no
    answer is made again)."""
    from pydantic import ValidationError

    from studio.plan.decision import PlanDecision
    from studio.plan.planner import PLAN_LOG, plan

    if reuse:
        logged = job.load_json(PLAN_LOG, default=None)
        if _paid_answer(logged):
            decision = None
            if logged.get("decision"):
                with contextlib.suppress(ValidationError):
                    decision = PlanDecision.model_validate(logged["decision"])
            usage = logged.get("usage") or {}
            return decision, {"model": (logged.get("model") or {}).get("model"), "attempts": logged.get("attempts"),
                              "note": logged.get("note", ""), "decision": decision is not None, "reused": True,
                              "usage": {k: usage.get(k) for k in ("input_tokens", "output_tokens",
                                                                  "cache_read_tokens", "cache_write_tokens")}}
    say(f"planner ({planner.model}, effort {planner.effort})")
    try:
        res = plan(job, index, digest, spec=planner, settings=settings, brief=brief, style=style, max_usd=max_usd)
    except Exception as e:  # plan() never raises for a model failure; anything else must not fail the edit either
        note = f"planner crashed ({type(e).__name__}: {str(e)[:200]}); pure-code plan used"
        job.trace("pipeline_note", note=note)
        return None, {"model": planner.model, "attempts": 0, "note": note, "decision": False}
    usage = {k: res.usage.get(k) for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")}
    return res.decision, {"model": planner.model, "effort": planner.effort, "attempts": res.attempts,
                          "rejected": list(res.rejected)[:10], "note": res.note, "decision": res.decision is not None,
                          "usage": usage}


def _validation_errors(doc: CutDocument, index: TakeIndex, job: Job) -> list[str]:
    from studio.doc.validate import validate_document

    return [f"{f.code}: {f.message}" for f in validate_document(doc, index, job) if f.level == "error"]


def _standard_plan(job: Job, index: TakeIndex, *, platform: str, decision: Any, settings: Settings,
                   brief: str | None, style: str | None) -> tuple[CutDocument, str, list[str]]:
    """``(document, source, notes)``: the code plan of the decision, then (when that plan fails validation or the
    radio gate, or there is no decision) the pure-code plan. Raises :class:`PipelineError` when no plan passes."""
    from studio.plan.code_plan import CodePlanError, RadioBlocked, plan_document

    notes: list[str] = []
    for d in ([decision, None] if decision is not None else [None]):
        label = "planner decision" if d is not None else "pure-code plan"
        try:
            doc = plan_document(job, index, platform, d, settings=settings, brief=brief, style=style)
        except RadioBlocked as e:
            notes.append(f"the {label} fails the radio test: " + "; ".join(e.blockers[:6]))
            continue
        except CodePlanError as e:
            notes.append(f"the {label} could not be built: {e}")
            continue
        errors = _validation_errors(doc, index, job)
        if errors:
            notes.append(f"the {label} fails validation: " + "; ".join(errors[:6]))
            continue
        return doc, label, notes
    job.trace("pipeline_note", note="standard tier: no plan passes", notes=notes)
    raise PipelineError("standard tier: no plan passes validation and the radio test, so nothing was rendered ("
                        + " | ".join(notes) + "). `studio edit <job> --tier director` hands the take to the Director.")


class _SameRender(Exception):
    """The pure-code second attempt would render exactly what failed: not worth a render."""


def _check_standard_reachable(job: Job, index: TakeIndex, settings: Settings) -> None:
    """Refuse, before the planner is paid, a take no plan can bring under the tier's output limit: even with the most
    the planner may drop (:data:`studio.plan.decision.MAX_DROP_SHARE` of the spoken words) the pure-code story's
    speech alone (no pauses) would run past :data:`studio.plan.digest.STANDARD_MAX_OUTPUT_S`."""
    from studio.plan.code_plan import story_speech_s
    from studio.plan.decision import MAX_DROP_SHARE
    from studio.plan.digest import STANDARD_MAX_OUTPUT_S

    try:
        speech = story_speech_s(index, settings=settings)
    except Exception as e:  # an estimate never fails the edit; the plan's own length check still runs
        job.trace("pipeline_note", note=f"story length not estimated ({type(e).__name__}: {str(e)[:200]})")
        return
    shortest = speech * (1.0 - MAX_DROP_SHARE)
    if shortest > STANDARD_MAX_OUTPUT_S:
        job.trace("pipeline_note", note=f"standard tier refused: {speech:.0f} s of kept speech; at most "
                  f"{STANDARD_MAX_OUTPUT_S} s ships")
        raise PipelineError(f"standard tier: this take keeps {speech:.0f} s of speech, and no plan can bring it under "
                            f"the tier's {STANDARD_MAX_OUTPUT_S} s output limit (the planner may drop at most "
                            f"{MAX_DROP_SHARE:.0%} of the words). Nothing was paid for beyond the transcript. Edit it "
                            "with `--tier director` (with `--max-usd` to cap the spend), or trim the take first.")


def _check_standard_output(job: Job, doc: CutDocument, index: TakeIndex, source: str) -> None:
    """Refuse to render a plan longer than :data:`studio.plan.digest.STANDARD_MAX_OUTPUT_S` (the tier's hard output
    limit: the render and the QA ASR grow with the output length)."""
    from studio.plan.digest import STANDARD_MAX_OUTPUT_S

    secs = doc.estimated_duration_us(index) / 1e6
    if secs > STANDARD_MAX_OUTPUT_S:
        job.trace("pipeline_note", note=f"standard tier refused: the {source} runs {secs:.0f} s",
                  doc_version=doc.version)
        raise PipelineError(f"standard tier: the {source} (document v{doc.version}) runs about {secs:.0f} s, over the "
                            f"tier's {STANDARD_MAX_OUTPUT_S} s output limit, so nothing was rendered. Edit it with "
                            "`--tier director` (with `--max-usd` to cap the spend), or trim the take first.")


def _qa_failures(run: Any) -> list[dict[str, Any]]:
    return [r.to_dict() for r in run.results if not r.passed]


def _labels(failures: Sequence[dict[str, Any]]) -> list[str]:
    from studio.plan.repair import invariant_label

    return [invariant_label(int(f["number"])) for f in failures if isinstance(f.get("number"), int)]


def _after_standard_delivery(job: Job, champion: Path) -> None:
    """A delivered standard job keeps the champion's lean A-roll and mixes (a caption or text chat then re-renders
    only the overlay and the master; any render that needs the space reclaims them) and its lean mezzanine (a chat's
    full render does not rebuild it); every other render's intermediates go. ``STUDIO_KEEP_MEZZ=0`` drops the
    mezzanine."""
    from studio import storage

    drop = (os.environ.get("STUDIO_KEEP_MEZZ") or "").strip().lower() in ("0", "false", "no")
    storage.reclaim_job(job, keep_intermediates={champion.name}, drop_mezz=drop, reason="delivered (standard tier)")


def _edit_standard(job: Job, index: TakeIndex, *, brief: str | None, style: str | None, platform: str,
                   planner: ModelSpec, settings: Settings, out: PathLike | None, say: Callable[[str], None],
                   resumed: bool, max_usd: float | None = None) -> EditResult:
    """The standard tier after ingest and the Take Index (docs/standard-tier-interfaces.md, "Pipeline branch"; the
    module docstring has the steps). Returns the :class:`EditResult` with ``tier="standard"``, ``reviewed=False``;
    a render that still fails the invariants after repair is not delivered (``invariants_passed=False``).
    ``max_usd`` caps the planner's model spend (:func:`studio.plan.planner.plan`; default
    :data:`studio.plan.planner.DEFAULT_MAX_USD`)."""
    import dataclasses

    from studio.agent.director import render_relevant
    from studio.agent.loop import LoopState, is_standard_state, prune_render, render_complete, render_full
    from studio.plan.code_plan import plan_document
    from studio.plan.digest import build_digest, estimate_tokens
    from studio.plan.repair import PLAN_DEPENDENT, repair
    from studio.qa.invariants import evaluate_render
    from studio.storage import DiskSpaceError

    s = settings
    t0 = time.monotonic()
    rec = _standard_record(job)
    st = LoopState.load(job)
    # a finished standard edit only re-delivers, while the loop state is still the standard tier's: after an upgrade
    # it holds the Director's champion, which must not be shipped again labelled as a standard-tier result
    if (resumed and rec.get("done") and st.champion_render and st.champion_doc is not None
            and is_standard_state(st) and render_complete(job.renders_dir / st.champion_render)):
        say("standard edit already finished: delivering it again (no model call, no render)")
        return _ship_standard(job, job.renders_dir / st.champion_render, job.load_doc(st.champion_doc),
                              passed=st.champion_passed, stop=st.stop_reason, rec=rec, out=out, settings=s, say=say,
                              t0=t0, resumed=True)
    if resumed and rec.get("done") and not is_standard_state(st):
        say("this job was upgraded to the Director tier: a fresh standard edit replaces its champion")
        job.trace("pipeline_note", note="standard edit after an upgrade: the Director's champion is replaced",
                  champion=st.champion_render)
    reuse = resumed and bool(rec.get("planner"))
    rec = {"tier": "standard", "platform": platform, "started_at": time.time(),
           **({"planner": rec["planner"]} if reuse else {})}
    _save_standard(job, rec)
    # a take no plan can bring under the tier's output limit is refused before the planner is paid
    _check_standard_reachable(job, index, s)
    # 1 — digest (+ the colour line when the mezzanine exists)
    say("digest")
    digest = build_digest(index, platform=platform, brief=brief, style_hint=style)
    colour = _colour_line(job, index)
    if colour:
        digest = dataclasses.replace(digest, text=digest.text.rstrip() + "\n" + colour)
    _save_standard(job, rec, colour=colour, digest={
        "chars": len(digest.text), "tokens_est": estimate_tokens(digest.text), "sentences": len(digest.sentence_ids),
        "omitted": list(digest.stats.get("omitted_sentence_ids") or []),
        **{k: digest.stats.get(k) for k in ("wpm", "energy", "pause_median_ms", "snr_db", "music_in_room")}})
    # 2 — the one planner call
    decision, prec = _standard_decision(job, index, digest, planner, s, brief=brief, style=style, reuse=reuse, say=say,
                                        max_usd=max_usd)
    _save_standard(job, rec, planner=prec)
    # 3 — code plan → validation + radio gate (a blocked decision plan drops to the pure-code plan)
    say("code plan" + (" (planner decision)" if decision is not None else " (pure-code: no planner decision)"))
    doc, source, plan_notes = _standard_plan(job, index, platform=platform, decision=decision, settings=s,
                                             brief=brief, style=style)
    _save_standard(job, rec, plan_source=source, plan_notes=plan_notes, doc_version=doc.version)
    _check_standard_output(job, doc, index, source)
    # 4 — one render at the standard profile; its intermediates stay until repair has run
    say(f"render (standard profile) of document v{doc.version}")
    rd = render_full(job, doc, index, profile="standard")
    _save_standard(job, rec, render=rd.name)
    # 5 — the ten invariants on the encoded file
    say("QA (the ten invariants)")
    run = evaluate_render(job, rd, settings=s)
    passed, failures = run.passed, _qa_failures(run)
    # 6 — code-only repair (at most one re-render)
    if not passed:
        say("repair (code only): " + ", ".join(_labels(failures)))
        try:
            res = repair(job, doc, index, rd, failures, profile="standard", settings=s)
        except DiskSpaceError:
            raise
        except Exception as e:  # a repair that crashes is a failed repair: the second attempt may still pass
            job.trace("pipeline_note", note=f"repair crashed: {type(e).__name__}: {str(e)[:300]}")
            _save_standard(job, rec, repair={"from": rd.name, "render": rd.name, "mode": "none", "passed": False,
                                             "fixed": [], "remaining": _labels(failures), "rerendered": False,
                                             "notes": [f"repair crashed: {type(e).__name__}: {str(e)[:300]}"]})
        else:
            if res.rerendered and res.render_dir != rd:
                prune_render(rd, keep_finals=False, job=job, reason="replaced by repair")
            _save_standard(job, rec, repair={"from": rd.name, "render": res.render_dir.name, "mode": res.mode,
                                             "passed": res.passed, "fixed": res.fixed, "remaining": res.remaining,
                                             "rerendered": res.rerendered, "notes": res.notes[:20]})
            doc, rd, passed = res.doc, res.render_dir, res.passed
            failures = [] if passed else (list(res.failures) or failures)
    # 7 — the second attempt: the pure-code plan, when the failing plan came from a decision and what still fails
    # depends on the plan (an engine or media fault — HDR at ingest, the encoder, A/V alignment — would fail the
    # pure-code render the same way: a third full render for nothing)
    plan_faults = {int(f["number"]) for f in failures if isinstance(f.get("number"), int)} & PLAN_DEPENDENT
    if not passed and source == "planner decision" and not plan_faults:
        _save_standard(job, rec, second_attempt={"skipped": "the remaining failures do not depend on the plan ("
                                                 + ", ".join(_labels(failures) or ["QA"]) + ")"})
    elif not passed and source == "planner decision":
        say("second attempt: the pure-code plan")
        second: dict[str, Any] = {}
        try:
            doc2 = plan_document(job, index, platform, None, settings=s, brief=brief, style=style)
            errors = _validation_errors(doc2, index, job)
            if errors:
                raise PipelineError("; ".join(errors[:4]))
            if render_relevant(doc2) == render_relevant(doc):
                raise _SameRender("the pure-code plan renders the same picture and sound as the failed one")
            rd2 = render_full(job, doc2, index, profile="standard")
            run2 = evaluate_render(job, rd2, settings=s)
            second = {"doc_version": doc2.version, "render": rd2.name, "passed": run2.passed,
                      "remaining": _labels(_qa_failures(run2))}
            if run2.passed:
                prune_render(rd, keep_finals=False, job=job, reason="replaced by the pure-code second attempt")
                doc, rd, passed, failures = doc2, rd2, True, []
                source = "pure-code plan (second attempt)"
            else:
                prune_render(rd2, keep_finals=False, job=job, reason="pure-code second attempt failed invariants")
        except DiskSpaceError:
            raise
        except _SameRender as e:
            second = {"skipped": str(e)}
        except Exception as e:  # a second attempt that cannot run leaves the first attempt's verdict standing
            second = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
        _save_standard(job, rec, second_attempt=second, plan_source=source)
    from studio.agent.loop import STANDARD_STOP_REASON

    stop = STANDARD_STOP_REASON if passed else (
        "standard tier: the render fails invariants after repair (" + ", ".join(_labels(failures) or ["QA"])
        + "); nothing delivered")
    return _ship_standard(job, rd, doc, passed=passed, stop=stop, rec=rec, out=out, settings=s, say=say, t0=t0,
                          resumed=False)


def _ship_standard(job: Job, rd: Path, doc: CutDocument, *, passed: bool, stop: str, rec: dict[str, Any],
                   out: PathLike | None, settings: Settings, say: Callable[[str], None], t0: float,
                   resumed: bool) -> EditResult:
    """Record the champion, write the report, deliver one final when it passed (nothing otherwise)."""
    from studio.agent.loop import STANDARD_REVIEW_NOTE, prune_render, record_standard_champion
    from studio.qa.report import write_report

    if not resumed:
        record_standard_champion(job, rd, doc.version, passed=passed, stop_reason=stop)
        _save_standard(job, rec, done=True, passed=passed, stop_reason=stop, champion_render=rd.name,
                       champion_doc=doc.version, seconds=round(time.monotonic() - t0, 1), cost=_cost_record(job))
    rep = None
    try:
        rep = write_report(job, render_dir=rd, settings=settings)
    except Exception as e:
        job.trace("pipeline_note", note=f"report failed: {type(e).__name__}: {e}")
    finals: dict[str, Path] = {}
    extras: dict[str, Path] = {}
    if passed:
        say("deliver")
        finals, extras = deliver(job, rd, doc, out=out, report=rep, nomusic=doc.audio.music is not None)
        _after_standard_delivery(job, rd)
        job.update_meta(champion_render=rd.name, champion_doc=doc.version, reviewed=False,
                        review_note=STANDARD_REVIEW_NOTE)
        say(f"NOT REVIEWED: {STANDARD_REVIEW_NOTE} (no critic watched the delivered video)")
    else:
        if rep is not None:
            extras["report"] = rep
        prune_render(rd, keep_finals=True, job=job, reason="standard render failed invariants")
        say(f"NOT DELIVERED: {stop}")
    seconds = round(time.monotonic() - t0, 1)
    job.trace("stage", stage="pipeline.edit", status="done" if passed else "failed", tier="standard", render=rd.name,
              doc_version=doc.version, stop=stop, passed=passed, seconds=seconds, resumed=resumed)
    return EditResult(job_dir=job.root, finals=finals, extras=extras, doc_version=doc.version, render=rd.name,
                      rounds=0, stop_reason=stop, invariants_passed=passed, reviewed=False,
                      review_note=STANDARD_REVIEW_NOTE, estimated_cost=_cost_line(job), tier="standard",
                      compute_seconds=seconds)


def _resume_from_standard(job: Job) -> str:
    """The upgrade (``--tier director`` on a standard job): keep the standard champion in the loop state and record
    the Director's stages as done on its document, so the champion loop critiques the standard render and the
    Director starts at the review rounds (the first pass is not paid again). Without a complete champion the Director
    runs its stages on the standard document. Returns the progress line."""
    from studio.agent.director import STAGE_ORDER, DirectorState, StageRecord
    from studio.agent.loop import reopen_for_review

    st = reopen_for_review(job)
    if st.champion_render is None or st.champion_doc is None:
        job.update_meta(upgraded_from={"tier": "standard", "champion_render": None, "at": time.time()})
        job.trace("upgrade", from_tier="standard", to_tier="director", start="stages")
        return ("upgrade from the standard tier: no complete standard render, so the Director runs its stages on the "
                "standard document")
    ds = DirectorState.load(job)
    if not ds.completed:
        doc = job.load_doc(st.champion_doc)
        plan_note = next((n.text for n in reversed(doc.notes) if n.text.startswith("code plan")), "")
        summary = ("Standard tier (one planner call, the code plan, one render, code QA only; nobody reviewed it) "
                   f"made document v{doc.version} and render {st.champion_render}. You take over at the render "
                   "review: the first pass is not re-run. " + plan_note)[:1800]
        now = time.time()
        ds.completed = [StageRecord(stage=x, summary=summary if x == "finalize" else "", doc_version=doc.version,
                                    model="standard tier", started_at=now, ended_at=now,
                                    note="upgraded from the standard tier") for x in STAGE_ORDER]
        ds.save(job)
    job.update_meta(upgraded_from={"tier": "standard", "champion_render": st.champion_render,
                                   "champion_doc": st.champion_doc, "at": time.time()})
    job.trace("upgrade", from_tier="standard", to_tier="director", start="revise", champion=st.champion_render,
              doc_version=st.champion_doc)
    return (f"upgrade from the standard tier: the Director starts at the review rounds on {st.champion_render} "
            f"(document v{st.champion_doc}); the first pass is not paid again")


def _chat_standard(job: Job, instruction: str, *, planner: ModelSpec, settings: Settings,
                   out: PathLike | None, index: TakeIndex | None = None, max_usd: float | None = None) -> ChatResult:
    """A chat edit on a standard job (docs/standard-tier-interfaces.md, "Standard chat"; :mod:`studio.plan.chat`):
    one ``planner`` call → ``{ops, summary}`` → one version (one retry on rejected ops) → overlay + master on the
    champion's A-roll when the cut is unchanged, else a full standard render → invariants → repair → deliver. A
    priced refusal (music, SFX, b-roll, paid isolation) makes no call and no render. A render that still fails
    is not delivered and the previous champion stays. ``max_usd`` caps this chat's model spend
    (:func:`studio.plan.chat.edit_document`)."""
    from studio import storage
    from studio.agent.loop import LoopState, prune_render
    from studio.plan import chat as chat_mod
    from studio.qa.report import write_report

    s = settings
    index = index if index is not None else job.load_index()
    with edit_slot(), storage.job_activity(job):
        st = LoopState.load(job)
        base = job.load_doc(st.champion_doc if st.champion_doc is not None else job.latest_doc_version())
        ed = chat_mod.edit_document(job, index, base, instruction, spec=planner, settings=s, max_usd=max_usd)
        if not ed.changed:
            job.trace("chat", tier="standard", instruction=instruction[:500], changed=False, called=ed.called,
                      doc_version=ed.doc.version)
            return ChatResult(job_dir=job.root, summary=ed.summary or "No render-relevant change was needed.",
                              doc_version=ed.doc.version, changes=["no render-relevant change"],
                              critic_note=STANDARD_NO_CRITIC, estimated_cost=_cost_line(job))
        changes = describe_change(base, ed.doc, index)
        champ = job.renders_dir / st.champion_render if st.champion_render else None
        cr = chat_mod.render_change(job, index, base, ed.doc, champ if champ is not None and champ.is_dir() else None,
                                    settings=s)
        summary = ed.summary
        finals: dict[str, Path] = {}
        if cr.passed:
            old = st.champion_render
            st.champion_render, st.champion_doc, st.champion_passed = cr.render_dir.name, cr.doc.version, True
            st.save(job)
            if old and old != cr.render_dir.name:
                prune_render(job.renders_dir / old, keep_finals=True, job=job, reason="replaced by a chat edit")
            rep = None
            try:
                rep = write_report(job, render_dir=cr.render_dir, settings=s)
            except Exception as e:  # the chat still delivers; the trace says why report.md is missing
                job.trace("pipeline_note", note=f"report failed: {type(e).__name__}: {e}")
            finals, _extras = deliver(job, cr.render_dir, cr.doc, out=out, report=rep,
                                      nomusic=cr.doc.audio.music is not None)
            _after_standard_delivery(job, cr.render_dir)
            job.update_meta(champion_render=cr.render_dir.name, champion_doc=cr.doc.version)
        else:
            prune_render(cr.render_dir, keep_finals=True, job=job, reason="chat render failed invariants")
            summary += (" — NOT DELIVERED: the render still fails invariants ("
                        + ", ".join(_labels(cr.failures) or ["QA"]) + ") after repair; the previous champion stays.")
        job.trace("chat", tier="standard", instruction=instruction[:500], changed=True, doc_version=cr.doc.version,
                  render=cr.render_dir.name, mode=cr.mode, passed=cr.passed,
                  repaired=cr.repair.mode if cr.repair is not None else None)
    return ChatResult(job_dir=job.root, summary=summary, doc_version=cr.doc.version, finals=finals, changes=changes,
                      invariants_passed=cr.passed, critic_note=STANDARD_NO_CRITIC, estimated_cost=_cost_line(job))


def _delete_uploads(director: Any) -> None:
    """Frames uploaded for the Director (the Files API of the key's workspace, the creator's when BYOK) are deleted
    once the edit is delivered instead of waiting for them to expire."""
    with contextlib.suppress(Exception):
        up = getattr(director.session, "image_uploader", None)
        if up is not None and hasattr(up, "delete_all"):
            up.delete_all()


# ============================================================================================ chat
def describe_change(before: CutDocument, after: CutDocument, index: TakeIndex) -> list[str]:
    """Human-readable differences between two document versions (what a chat edit changed)."""
    out: list[str] = []
    kb, ka = before.kept_word_ids(index), after.kept_word_ids(index)
    if kb != ka:
        sb, sa = set(kb), set(ka)
        cut = [w for w in kb if w not in sa]
        added = [w for w in ka if w not in sb]
        if cut:
            out.append(f"removed {len(cut)} words: \"{' '.join(index.word(w).text for w in cut[:30])}\"")
        if added:
            out.append(f"restored {len(added)} words: \"{' '.join(index.word(w).text for w in added[:30])}\"")
        if not cut and not added:
            out.append("reordered the story")
        db, da = before.estimated_duration_us(index) / 1e6, after.estimated_duration_us(index) / 1e6
        out.append(f"length ≈ {db:.1f} s → {da:.1f} s")
    gb = {(s.from_word, g): ms for s in before.segments for g, ms in s.gap_overrides.items()}
    ga = {(s.from_word, g): ms for s in after.segments for g, ms in s.gap_overrides.items()}
    if {g for _s, g in gb} != {g for _s, g in ga} or sorted(gb.values()) != sorted(ga.values()):
        out.append("pause targets changed")
    fb = {s.id: (s.framing.scale if s.framing else None) for s in before.segments}
    fa = {s.id: (s.framing.scale if s.framing else None) for s in after.segments}
    if sorted(x for x in fb.values() if x) != sorted(x for x in fa.values() if x):
        out.append("framing/punch-ins changed")
    ib, ia = {i.id for i in before.inserts}, {i.id for i in after.inserts}
    if ia - ib:
        out.append("added inserts " + ", ".join(sorted(ia - ib)))
    if ib - ia:
        out.append("removed inserts " + ", ".join(sorted(ib - ia)))
    changed_ins = [i.id for i in after.inserts if i.id in ib and before.insert(i.id) != i]
    if changed_ins:
        out.append("changed inserts " + ", ".join(changed_ins))
    tb, ta = {t.id: t for t in before.texts}, {t.id: t for t in after.texts}
    if set(ta) - set(tb):
        out.append("added text " + ", ".join(f"{k} '{ta[k].text}'" for k in sorted(set(ta) - set(tb))))
    if set(tb) - set(ta):
        out.append("removed text " + ", ".join(sorted(set(tb) - set(ta))))
    if any(k in tb and tb[k] != v for k, v in ta.items()):
        out.append("edited on-screen text")
    if before.captions != after.captions:
        cb, ca = before.captions, after.captions
        if (cb is None) != (ca is None) or (cb and ca and cb.enabled != ca.enabled):
            out.append("captions " + ("on" if ca is None or ca.enabled else "off"))
        elif cb is not None and ca is not None and cb.style != ca.style:
            out.append(f"caption style → {ca.style.font} {ca.style.size_px}px {ca.style.animation} {ca.style.case}")
        else:
            out.append("caption pages edited")
    if before.audio.music != after.audio.music:
        m = after.audio.music
        out.append("music removed" if m is None else f"music → {m.asset_id or m.source} at "
                                                     f"{m.level_lu_under_speech:+.0f} LU under speech")
    if before.audio.sfx != after.audio.sfx:
        out.append(f"SFX {len(before.audio.sfx)} → {len(after.audio.sfx)} cues")
    if before.audio.voice != after.audio.voice:
        out.append("voice chain changed")
    if (before.audio.loudness_target_lufs, before.audio.true_peak_dbtp) != (after.audio.loudness_target_lufs,
                                                                            after.audio.true_peak_dbtp):
        out.append(f"loudness → {after.audio.loudness_target_lufs:.0f} LUFS")
    if before.color != after.color:
        out.append("colour changed" if after.color is not None else "colour correction removed")
    if [(s.id, s.speed) for s in before.segments] != [(s.id, s.speed) for s in after.segments] and kb == ka:
        out.append("segment speed/seams changed")
    return out or ["no render-relevant change"]


def chat(job_dir: PathLike, instruction: str, *, director_provider: str | None = None,
         director_model: str | None = None, director_key_env: str | None = None, house: bool = False,
         planner_model: str | None = None, max_usd: float | None = None,
         settings: Settings | None = None, director_model_override: Any = None, panel: JudgePanel | None = None,
         renderer: Callable[[CutDocument], Path] | None = None, qa: Callable[[Path], Any] | None = None,
         critic: Callable[..., list[dict[str, Any]]] | None = None, out: PathLike | None = None,
         fix_attempts: int = 2) -> ChatResult:
    """Apply a creator instruction to the job's champion document, re-render at full quality, gate on the
    invariants, record a critic sanity note and deliver. User intent wins: there is no pairwise veto. A chat runs
    in the tier the job was edited with (a standard job's chat is one planner call, ``planner_model`` given now
    winning over the job's recorded planner, and ``max_usd`` capping that chat's model spend)."""
    from studio.agent import critics as critics_mod
    from studio.agent.director import Director
    from studio.agent.loop import LoopState, prune_overlay_cache, prune_render, render_full
    from studio.qa.invariants import evaluate_render
    from studio.qa.report import write_report

    s = _settings(settings)
    if not instruction or not instruction.strip():
        raise PipelineError("empty instruction")
    job = _open(job_dir, s)
    if out is None and Path(job_dir).expanduser().resolve() != job.root.resolve():
        out = Path(job_dir).expanduser()  # run on a delivery folder: the new deliverables land there too
    index = job.load_index()
    loop_state = LoopState.load(job)
    base_version = loop_state.champion_doc if loop_state.champion_doc is not None else job.latest_doc_version()
    if base_version is None:
        raise PipelineError(f"job {job.id} has no document yet: run `studio edit` first")
    if _resolve_tier(None, job, s) == "standard":  # chat reuses the job's tier
        return _chat_standard(job, instruction, planner=_planner_spec(job, planner_model, s), settings=s, out=out,
                              index=index, max_usd=max_usd)
    base = job.load_doc(base_version)
    flags_given = director_provider is not None or house
    director_provider, director_model, director_key_env = _director_flags(job, director_provider, director_model,
                                                                          director_key_env, house)
    spec = _spec_or_refuse(director_provider, director_model, director_key_env, s,
                           effort=None if flags_given else _recorded_effort(job))
    render_fn = renderer or (lambda d: render_full(job, d, index))

    def qa_fn(rd: Path) -> tuple[bool, list[dict[str, Any]]]:
        if qa is not None:
            r = qa(rd)
            if isinstance(r, tuple):
                return r
            return bool(r), []
        run = evaluate_render(job, rd, settings=s)
        return run.passed, [x.to_dict() for x in run.results if not x.passed]

    from studio import storage

    with edit_slot(), storage.job_activity(job):
        storage.ensure_mezz(job, settings=s)
        platforms = tuple(d.platform for d in base.deliverables) or ("tiktok",)
        media = [m for m in (_job_meta(job).get("creator_media") or []) if Path(m).exists()]
        director = Director(job, index, spec=spec, settings=s, model=director_model_override, platforms=platforms,
                            render_preview=make_preview_callback(job, index, s),
                            critique=make_critique_callback(job, index, s, panel, spec), creator_media=media)
        champ = job.renders_dir / loop_state.champion_render if loop_state.champion_render else None
        res = director.chat(instruction, base_version=base_version,
                            champion_render=champ if champ is not None and champ.is_dir() else None)
        doc = res["doc"]
        summary = res["summary"]
        changes = describe_change(base, doc, index)
        if not res["changed"]:
            job.trace("chat", instruction=instruction[:500], changed=False, doc_version=doc.version)
            return ChatResult(job_dir=job.root, summary=summary or "No render-relevant change was needed.",
                              doc_version=doc.version, changes=changes, estimated_cost=_cost_line(job))
        rd = render_fn(doc)
        passed, failures = qa_fn(rd)
        attempts = 0
        while not passed and attempts < fix_attempts:
            attempts += 1
            fail_txt = "\n".join(f"- invariant {f.get('number')} {f.get('name')}: {f.get('detail')} "
                                 f"(refs {', '.join(f.get('refs') or [])})" for f in failures) or "- (see QA)"
            prompt = ("The render of your change FAILS hard invariants:\n" + fail_txt + "\nFix them without undoing "
                      f"the creator's request (\"{instruction}\"). Then finish_stage.")
            fix = director.run_stage("chat", prompt, record=False)
            new_doc = director.session.doc
            if new_doc.version == doc.version:
                break
            summary += f" (then, to pass the invariants: {fix.summary})"
            prune_render(rd, keep_finals=False, job=job, reason="chat render failed invariants")
            doc = new_doc
            rd = render_fn(doc)
            passed, failures = qa_fn(rd)
        # a critic sanity note (no veto): was the instruction carried out cleanly?
        note_txt = ""
        try:
            question = (f"The creator asked: \"{instruction}\". Was it carried out cleanly and completely, with "
                        "nothing else broken (seams, captions, inserts, audio)? Answer yes/no with IDs.")
            notes = (critic or (lambda r, q: critics_mod.critique(job, doc, index, r, questions=q, settings=s,
                                                                  panel=panel, label=f"chat_{r.name}")))(rd, [question])
            top = [n for n in notes if n.get("severity") in ("P0", "P1")]
            note_txt = critics_mod.format_notes(top[:3]) if top else "critics: no defect found in the change"
        except Exception as e:
            note_txt = f"critic sanity check unavailable ({type(e).__name__})"
        finals: dict[str, Path] = {}
        if passed:
            old = loop_state.champion_render
            loop_state.champion_render, loop_state.champion_doc, loop_state.champion_passed = rd.name, doc.version, True
            loop_state.save(job)
            if old and old != rd.name:
                prune_render(job.renders_dir / old, keep_finals=True, job=job, reason="replaced by a chat edit")
            rep = None
            with contextlib.suppress(Exception):
                rep = write_report(job, render_dir=rd, settings=s)
            finals, _extras = deliver(job, rd, doc, out=out, report=rep)
            prune_render(rd, keep_finals=True, job=job, reason="delivered")
            prune_overlay_cache(job, keep=0)
            _after_delivery(job)
            job.update_meta(champion_render=rd.name, champion_doc=doc.version)
            _delete_uploads(director)
        else:
            prune_render(rd, keep_finals=True, job=job, reason="chat render failed invariants")
            summary += (" — NOT DELIVERED: the render still fails invariants ("
                        + "; ".join(str(f.get("name")) for f in failures) + "); the previous champion stays.")
        job.trace("chat", instruction=instruction[:500], changed=True, doc_version=doc.version, render=rd.name,
                  passed=passed)
    return ChatResult(job_dir=job.root, summary=summary, doc_version=doc.version, finals=finals, changes=changes,
                      invariants_passed=passed, critic_note=note_txt, estimated_cost=_cost_line(job))


# ============================================================================================ single steps
def index(video: PathLike, *, asr_provider: str | None = None, tier: str | None = None,
          settings: Settings | None = None) -> IndexResult:
    """Create a job, ingest ``video`` and build its Take Index. ``tier`` (default ``Settings.tier``) picks the
    ingest: the standard tier's caps (:func:`standard_ingest_caps`: a 1080p30 lean mezzanine) or the Director tier's
    full-size one, so a later ``studio edit <job>`` in that tier renders from the mezzanine it expects. The tier is
    not recorded: the edit decides the job's tier."""
    from studio.agent.loop import ensure_disk
    from studio.jobs import Job
    from studio.media.ingest import ingest
    from studio.perception.index import build_index

    s = _settings(settings)
    src = Path(video).expanduser().resolve()
    tier = _resolve_tier(tier, None, s)
    job = Job.create(work_dir=s.work_dir, meta={"source_name": src.name, "source_path": str(src)})
    job.trace("stage", stage="pipeline.index", video=src.name, tier=tier)
    ensure_disk(job.root, what="ingest (the mezzanine)")
    caps: dict[str, int] = {}
    if tier == "standard":
        try:
            caps = standard_ingest_caps(src, s)
        except Exception as e:  # ingest probes again and reports an unreadable file itself
            job.trace("pipeline_note", note=f"standard caps not probed ({type(e).__name__}: {str(e)[:200]})")
    ingest(src, job, settings=s, **caps)
    ix = build_index(job, asr_provider=asr_provider, settings=s)
    return IndexResult(job_dir=job.root, index_path=job.index_path, words=len(ix.words),
                       duration_s=round(ix.media.duration_us / 1e6, 3))


def render(job_dir: PathLike, *, version: int | None = None, preview: bool = False,
           settings: Settings | None = None) -> RenderResult:
    """Render a document version (latest by default) into a new ``renders/r{n}/`` (disk-checked), at the profile of
    the job's tier: ``standard`` (x264 medium, the lean A-roll) for a standard job, ``final`` for a Director job;
    ``preview`` wins."""
    from studio import storage
    from studio.agent.loop import render_full

    s = _settings(settings)
    job = _open(job_dir, s)
    doc = job.load_doc(version)
    profile = "standard" if _resolve_tier(None, job, s) == "standard" else "final"
    with storage.job_activity(job):
        rd = render_full(job, doc, job.load_index(), preview=preview, profile=profile)
    finals = {p.stem.removeprefix("final_"): p for p in sorted(rd.glob("final_*.mp4"))
              if p.stem != "final_nomusic"}
    return RenderResult(job_dir=job.root, render_dir=rd, finals=finals)


def report(job_dir: PathLike, *, settings: Settings | None = None) -> Path:
    """(Re)write ``report.md`` for the job (the champion render when the loop has one)."""
    from studio.agent.loop import LoopState
    from studio.qa.report import write_report

    job = _open(job_dir, settings)
    st = LoopState.load(job)
    kw: dict[str, Any] = {}
    if st.champion_render and (job.renders_dir / st.champion_render).is_dir():
        kw["render_dir"] = job.renders_dir / st.champion_render
    return write_report(job, settings=_settings(settings), **kw)


def qa(job_dir: PathLike, *, render: int | None = None, settings: Settings | None = None) -> QaResult:
    """Run the invariants on a render (latest by default); results are saved to ``<render>/qa/``."""
    from studio.qa.invariants import evaluate_render

    job = _open(job_dir, settings)
    rd = job.render_dir(render) if render is not None else None
    run = evaluate_render(job, rd, settings=_settings(settings))
    saved = run.render_dir / "qa" / "invariants.json"
    return QaResult(job_dir=job.root, passed=run.passed, results=[r.to_dict() for r in run.results],
                    report=saved if saved.exists() else None)


__all__ = ["EditResult", "ChatResult", "IndexResult", "RenderResult", "QaResult", "PipelineError", "edit", "chat",
           "index", "render", "report", "qa", "deliver", "describe_change", "edit_slot", "director_spec_for",
           "planner_spec_for", "make_preview_callback", "make_critique_callback", "standard_ingest_caps"]
