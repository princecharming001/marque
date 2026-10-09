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


def _ingest_for_edit(job: Job, source: str, settings: Settings, say: Callable[[str], None]) -> None:
    """Ingest inside the machine-wide heavy-write slot, keeping room for the edit's renders when choosing the
    mezzanine codec (ProRes HQ → ProRes 422 → lean) and reclaiming idle jobs first."""
    import dataclasses

    from studio import storage
    from studio.media.ingest import IngestError, IngestOptions, ingest

    opts = dataclasses.replace(IngestOptions.from_env(), reserve_render=True, reclaim_dir=str(job.root.parent))
    with storage.render_slot(on_wait=lambda: say("waiting for another edit's render or ingest to finish")):
        try:
            ingest(source, job, settings=settings, options=opts)
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
            report: Path | None = None, alternates: Sequence[tuple[str, Path]] = ()) -> tuple[dict[str, Path],
                                                                                              dict[str, Path]]:
    """Put the champion's deliverables in ``<job>/deliver/`` (and ``out``): finals per platform, the no-music
    master, cover, SRT, the document JSON and the report; the finals of ``alternates`` (label, render dir: variants
    the champion beat, or the version a winning variant replaced) go under ``alternates/<label>/``. Returns
    ``(finals, extras)``; extras carries ``alt:<label>`` entries."""
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
         max_usd: float | None = None,
         out: PathLike | None = None,
         settings: Settings | None = None, creator_media: Sequence[PathLike] = (),
         asr_provider: str | None = None, director_model_override: Any = None,
         panel: JudgePanel | None = None, loop_kwargs: dict[str, Any] | None = None,
         log: Callable[[str], None] | None = None) -> EditResult:
    """Edit ``video`` end to end (or resume the job directory ``video``). ``out`` receives copies of the
    deliverables. ``director_model_override`` / ``panel`` / ``loop_kwargs`` inject models and loop callbacks
    (tests)."""
    from studio import storage
    from studio.agent.director import Director
    from studio.agent.loop import ChampionLoop, clean_incomplete_renders, prune_overlay_cache, prune_render
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
        job.update_meta(brief=brief, style=style, platform=platform,
                        creator_media=[str(x) for x in creator_media], director=director_rec)
        job.trace("stage", stage="pipeline.edit", resumed=resumed)
        say(f"job {job.root}" + (" (resumed)" if resumed else ""))

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
            _ingest_for_edit(job, source, s, say)
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
                      estimated_cost=_cost_line(job),
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
         settings: Settings | None = None, director_model_override: Any = None, panel: JudgePanel | None = None,
         renderer: Callable[[CutDocument], Path] | None = None, qa: Callable[[Path], Any] | None = None,
         critic: Callable[..., list[dict[str, Any]]] | None = None, out: PathLike | None = None,
         fix_attempts: int = 2) -> ChatResult:
    """Apply a creator instruction to the job's champion document, re-render at full quality, gate on the
    invariants, record a critic sanity note and deliver. User intent wins: there is no pairwise veto."""
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
def index(video: PathLike, *, asr_provider: str | None = None, settings: Settings | None = None) -> IndexResult:
    """Create a job, ingest ``video`` and build its Take Index."""
    from studio.agent.loop import ensure_disk
    from studio.jobs import Job
    from studio.media.ingest import ingest
    from studio.perception.index import build_index

    s = _settings(settings)
    src = Path(video).expanduser().resolve()
    job = Job.create(work_dir=s.work_dir, meta={"source_name": src.name, "source_path": str(src)})
    job.trace("stage", stage="pipeline.index", video=src.name)
    ensure_disk(job.root, what="ingest (the mezzanine)")
    ingest(src, job, settings=s)
    ix = build_index(job, asr_provider=asr_provider, settings=s)
    return IndexResult(job_dir=job.root, index_path=job.index_path, words=len(ix.words),
                       duration_s=round(ix.media.duration_us / 1e6, 3))


def render(job_dir: PathLike, *, version: int | None = None, preview: bool = False,
           settings: Settings | None = None) -> RenderResult:
    """Render a document version (latest by default) into a new ``renders/r{n}/`` (disk-checked)."""
    from studio import storage
    from studio.agent.loop import render_full

    job = _open(job_dir, settings)
    doc = job.load_doc(version)
    with storage.job_activity(job):
        rd = render_full(job, doc, job.load_index(), preview=preview)
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
           "make_preview_callback", "make_critique_callback"]
