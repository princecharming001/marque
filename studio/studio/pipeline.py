"""End-to-end orchestration used by :mod:`studio.cli`.

``edit``: ingest → build_index → Director (brief → story → fine cut → finishing) → champion loop →
final render → QA → report. The other entry points run one part on an existing job directory.
All functions return small result dataclasses (paths only; never keys).

Wired: :func:`index` (ingest + Take Index), :func:`render` (compile → A-roll → overlays → audio → master via
:func:`studio.compile.master.render_document`), :func:`qa` (the ten invariants on a render) and
:func:`report`. Not yet: :func:`edit` and :func:`chat`, which need the Director and the champion loop
(:mod:`studio.agent.director`, :mod:`studio.agent.loop`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings
    from studio.jobs import Job

PathLike = str | os.PathLike[str]


@dataclass
class EditResult:
    job_dir: Path
    finals: dict[str, Path] = field(default_factory=dict)  # platform -> final_<platform>.mp4
    extras: dict[str, Path] = field(default_factory=dict)  # nomusic, cover, srt, report …
    doc_version: int | None = None


@dataclass
class ChatResult:
    job_dir: Path
    summary: str = ""
    doc_version: int | None = None
    finals: dict[str, Path] = field(default_factory=dict)


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


def edit(video: PathLike, *, brief: str | None = None, style: str | None = None, platform: str = "tiktok",
         director_provider: str | None = None, director_model: str | None = None,
         director_key_env: str | None = None, rounds: int | None = None, out: PathLike | None = None,
         settings: Settings | None = None) -> EditResult:
    """Edit ``video`` end to end. ``out`` (optional) receives copies of the deliverables."""
    raise NotImplementedError("studio.pipeline.edit")


def chat(job_dir: PathLike, instruction: str, *, director_provider: str | None = None,
         director_model: str | None = None, director_key_env: str | None = None,
         settings: Settings | None = None) -> ChatResult:
    """Apply a creator instruction to the job's latest document, re-render and re-judge."""
    raise NotImplementedError("studio.pipeline.chat")


def _settings(settings: Settings | None) -> Settings:
    from studio.config import get_settings

    return settings if settings is not None else get_settings()


def _open(job_dir: PathLike, settings: Settings | None) -> Job:
    from studio.jobs import Job

    return Job.open(job_dir, work_dir=_settings(settings).work_dir)


def index(video: PathLike, *, asr_provider: str | None = None, settings: Settings | None = None) -> IndexResult:
    """Create a job, ingest ``video`` and build its Take Index."""
    from studio.jobs import Job
    from studio.media.ingest import ingest
    from studio.perception.index import build_index

    s = _settings(settings)
    src = Path(video).expanduser().resolve()
    job = Job.create(work_dir=s.work_dir, meta={"source_name": src.name})
    job.trace("stage", stage="pipeline.index", video=src.name)
    ingest(src, job, settings=s)
    ix = build_index(job, asr_provider=asr_provider, settings=s)
    return IndexResult(job_dir=job.root, index_path=job.index_path, words=len(ix.words),
                       duration_s=round(ix.media.duration_us / 1e6, 3))


def render(job_dir: PathLike, *, version: int | None = None, preview: bool = False,
           settings: Settings | None = None) -> RenderResult:
    """Render a document version (latest by default) into a new ``renders/r{n}/``."""
    from studio.compile.master import render_document

    job = _open(job_dir, settings)
    doc = job.load_doc(version)
    res = render_document(job, doc, job.load_index(), preview=preview)
    return RenderResult(job_dir=job.root, render_dir=res.render_dir, finals=dict(res.finals))


def report(job_dir: PathLike, *, settings: Settings | None = None) -> Path:
    """(Re)write ``report.md`` for the job."""
    from studio.qa.report import write_report

    return write_report(_open(job_dir, settings), settings=_settings(settings))


def qa(job_dir: PathLike, *, render: int | None = None, settings: Settings | None = None) -> QaResult:
    """Run the invariants on a render (latest by default); results are saved to ``<render>/qa/``."""
    from studio.qa.invariants import evaluate_render

    job = _open(job_dir, settings)
    rd = job.render_dir(render) if render is not None else None
    run = evaluate_render(job, rd, settings=_settings(settings))
    saved = run.render_dir / "qa" / "invariants.json"
    return QaResult(job_dir=job.root, passed=run.passed, results=[r.to_dict() for r in run.results],
                    report=saved if saved.exists() else None)
