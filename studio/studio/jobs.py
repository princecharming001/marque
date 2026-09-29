"""Job directories (ARCHITECTURE §8): everything is reproducible from one folder.

Layout of ``$STUDIO_WORK_DIR/<job_id>/`` (default work dir ``/Users/home/studio-work``)::

    job.json              id, created_at, studio version, free-form meta
    media/                original.<ext>, probe.json, media_info.json, mezz.mov, audio.wav, proxy.mp4
    index/                take_index.json (+ raw ASR responses etc.)
    doc/                  v0.json, v1.json, …  (CutDocument versions)  +  oplog.jsonl (append-only)
    renders/              r1/, r2/, …  (one folder per render)
    critique/             critic notes, judge verdicts
    assets/               broll/, music/, sfx/ media files; registry/<asset_id>.json = AssetRef + licence
    logs/                 tool logs (ffmpeg stderr, …)
    trace.jsonl           every model call / notable event (role, provider, model, tokens, latency, tools)
    report.md

Conventions other modules rely on are exposed as properties (``job.mezz_path``, ``job.index_path`` …).
JSON helpers write atomically (temp file + rename) and serialize Pydantic models with
``model_dump(mode="json")``. The trace logger redacts secrets via :func:`studio.config.redact`.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import secrets
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from studio.config import get_settings, redact

if TYPE_CHECKING:  # pragma: no cover
    from studio.doc.model import AssetRef, CutDocument
    from studio.media.models import MediaInfo
    from studio.perception.index import TakeIndex

__all__ = ["Job", "JobError", "SUBDIRS", "new_job_id", "to_jsonable", "write_json_atomic", "read_json"]

SUBDIRS: tuple[str, ...] = ("media", "index", "doc", "renders", "critique", "assets", "logs")
ASSET_SUBDIRS: tuple[str, ...] = ("broll", "music", "sfx", "registry")

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DOC_RE = re.compile(r"^v(\d+)\.json$")
_RENDER_RE = re.compile(r"^r(\d+)$")


class JobError(RuntimeError):
    """Bad job id/path or missing job artefact."""


def new_job_id() -> str:
    """``YYYYMMDD-HHMMSS-xxxxxx`` (UTC time + 6 random hex chars); sortable and unique."""
    now = _dt.datetime.now(_dt.UTC)
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_jsonable(obj: Any) -> Any:
    """Convert Pydantic models (``mode="json"``), Paths, sets, Fractions … into JSON-safe values."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Mapping):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(to_jsonable(v) for v in obj)
    if isinstance(obj, Path):
        return str(obj)
    from fractions import Fraction

    if isinstance(obj, Fraction):
        return f"{obj.numerator}/{obj.denominator}"
    if hasattr(obj, "item") and callable(obj.item):  # numpy scalar
        try:
            return obj.item()
        except Exception:  # pragma: no cover
            pass
    return obj


def write_json_atomic(path: Path, obj: Any, *, indent: int | None = 2) -> Path:
    """Write JSON atomically (temp file in the same dir, fsync, rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(to_jsonable(obj), indent=indent, ensure_ascii=False, sort_keys=False)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    return path


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


#: A delivery folder (``studio edit --out DIR``) names its job here, so ``studio chat DIR`` / ``studio edit DIR``
#: work on the delivery folder as on the job directory.
JOB_POINTER = ".studio-job"


def resolve_job_dir(p: Path) -> Path:
    """``p`` itself when it is a job directory, else the job a delivery folder's ``.studio-job`` names."""
    if (p / "job.json").exists() or not (p / JOB_POINTER).is_file():
        return p
    try:
        target = Path((p / JOB_POINTER).read_text(encoding="utf-8").strip()).expanduser()
    except OSError:
        return p
    return target if (target / "job.json").exists() else p


class Job:
    """Handle on one job directory. Create with :meth:`create`, reopen with :meth:`open`."""

    _append_lock = threading.Lock()

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.id = self.root.name

    # ------------------------------------------------------------------ construction
    @classmethod
    def create(
        cls,
        job_id: str | None = None,
        *,
        work_dir: str | os.PathLike[str] | None = None,
        meta: Mapping[str, Any] | None = None,
        exist_ok: bool = False,
    ) -> Job:
        """Create ``<work_dir>/<job_id>/`` with all subdirectories and ``job.json``."""
        jid = new_job_id() if job_id is None else job_id
        if not _JOB_ID_RE.match(jid):
            raise JobError(f"invalid job id {jid!r} (letters, digits, '.', '_', '-'; max 128)")
        base = Path(work_dir) if work_dir is not None else get_settings().work_dir
        root = base / jid
        if root.exists() and not exist_ok and (root / "job.json").exists():
            raise JobError(f"job already exists: {root}")
        root.mkdir(parents=True, exist_ok=True)
        job = cls(root)
        job._ensure_dirs()
        if not job.meta_path.exists():
            from studio import __version__

            write_json_atomic(job.meta_path, {
                "id": jid,
                "created_at": _now_iso(),
                "studio_version": __version__,
                "meta": dict(meta or {}),
            })
        return job

    @classmethod
    def open(cls, path_or_id: str | os.PathLike[str], *, work_dir: str | os.PathLike[str] | None = None) -> Job:
        """Open an existing job by directory path or by id (resolved under ``work_dir``)."""
        p = Path(path_or_id).expanduser()
        if not p.is_dir():
            base = Path(work_dir) if work_dir is not None else get_settings().work_dir
            cand = base / str(path_or_id)
            if cand.is_dir():
                p = cand
        p = resolve_job_dir(p)
        if not p.is_dir() or not (p / "job.json").exists():
            raise JobError(f"not a job directory: {path_or_id}")
        job = cls(p)
        job._ensure_dirs()
        return job

    def _ensure_dirs(self) -> None:
        for d in SUBDIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        for d in ASSET_SUBDIRS:
            (self.root / "assets" / d).mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:
        return f"Job({str(self.root)!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Job) and other.root == self.root

    def __hash__(self) -> int:
        return hash(self.root)

    # ------------------------------------------------------------------ paths
    def path(self, *parts: str | os.PathLike[str]) -> Path:
        """Path inside the job dir. Rejects absolute parts and ``..`` escapes."""
        p = self.root.joinpath(*[str(x) for x in parts]).resolve()
        if p != self.root and self.root not in p.parents:
            raise JobError(f"path escapes job dir: {parts}")
        return p

    media_dir = property(lambda self: self.root / "media")
    index_dir = property(lambda self: self.root / "index")
    doc_dir = property(lambda self: self.root / "doc")
    renders_dir = property(lambda self: self.root / "renders")
    critique_dir = property(lambda self: self.root / "critique")
    assets_dir = property(lambda self: self.root / "assets")
    logs_dir = property(lambda self: self.root / "logs")
    meta_path = property(lambda self: self.root / "job.json")
    trace_path = property(lambda self: self.root / "trace.jsonl")
    report_path = property(lambda self: self.root / "report.md")
    oplog_path = property(lambda self: self.doc_dir / "oplog.jsonl")

    # media conventions (ARCHITECTURE §3)
    probe_path = property(lambda self: self.media_dir / "probe.json")
    media_info_path = property(lambda self: self.media_dir / "media_info.json")
    mezz_path = property(lambda self: self.media_dir / "mezz.mov")
    audio_path = property(lambda self: self.media_dir / "audio.wav")
    proxy_path = property(lambda self: self.media_dir / "proxy.mp4")
    index_path = property(lambda self: self.index_dir / "take_index.json")

    @property
    def original_path(self) -> Path | None:
        """``media/original.<ext>`` if ingested, else None."""
        found = sorted(self.media_dir.glob("original.*"))
        return found[0] if found else None

    def log_path(self, name: str) -> Path:
        """``logs/<name>`` (``.log`` appended when no suffix)."""
        n = name if Path(name).suffix else f"{name}.log"
        return self.path("logs", n)

    # ------------------------------------------------------------------ meta + json
    @property
    def meta(self) -> dict[str, Any]:
        return read_json(self.meta_path) if self.meta_path.exists() else {"id": self.id}

    def update_meta(self, **fields: Any) -> dict[str, Any]:
        """Merge keys into ``job.json["meta"]``."""
        data = self.meta
        data.setdefault("meta", {}).update(to_jsonable(fields))
        write_json_atomic(self.meta_path, data)
        return data

    def save_json(self, rel: str | os.PathLike[str], obj: Any, *, indent: int | None = 2) -> Path:
        """Atomically write JSON at ``rel`` (relative to the job root)."""
        return write_json_atomic(self.path(rel), obj, indent=indent)

    def load_json(self, rel: str | os.PathLike[str], default: Any = ...) -> Any:
        """Read JSON at ``rel``; return ``default`` if given and the file is missing."""
        p = self.path(rel)
        if not p.exists():
            if default is not ...:
                return default
            raise JobError(f"missing {rel} in job {self.id}")
        return read_json(p)

    def append_jsonl(self, rel: str | os.PathLike[str], records: Iterable[Any]) -> Path:
        """Append records as JSON lines (one ``write`` per line, process-locked)."""
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = "".join(json.dumps(to_jsonable(r), ensure_ascii=False) + "\n" for r in records)
        if not lines:
            return p
        with self._append_lock, open(p, "a", encoding="utf-8") as fh:
            fh.write(lines)
        return p

    def read_jsonl(self, rel: str | os.PathLike[str]) -> list[Any]:
        p = self.path(rel)
        if not p.exists():
            return []
        out = []
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    # ------------------------------------------------------------------ trace
    def trace(self, event: str, **fields: Any) -> dict[str, Any]:
        """Append one redacted event to ``trace.jsonl`` and return it.

        Model calls should use ``event="model_call"`` with ``role``, ``provider``, ``model``,
        ``input_tokens``, ``output_tokens``, ``latency_ms``, ``tool_calls`` (names), ``stage``.
        Never pass raw keys; anything that looks like one is redacted anyway.
        """
        rec = {"ts": _now_iso(), "t": round(time.time(), 3), "event": event, **fields}
        rec = redact(to_jsonable(rec))
        self.append_jsonl("trace.jsonl", [rec])
        return rec

    def read_trace(self) -> list[dict[str, Any]]:
        return self.read_jsonl("trace.jsonl")

    # ------------------------------------------------------------------ media info
    def save_media_info(self, info: MediaInfo) -> Path:
        return write_json_atomic(self.media_info_path, info)

    def load_media_info(self) -> MediaInfo:
        from studio.media.models import MediaInfo

        if not self.media_info_path.exists():
            raise JobError(f"job {self.id} has no media/media_info.json (run ingest first)")
        return MediaInfo.model_validate(read_json(self.media_info_path))

    # ------------------------------------------------------------------ take index
    def save_index(self, index: TakeIndex) -> Path:
        return write_json_atomic(self.index_path, index)

    def load_index(self) -> TakeIndex:
        from studio.perception.index import TakeIndex

        if not self.index_path.exists():
            raise JobError(f"job {self.id} has no index/take_index.json (run build_index first)")
        return TakeIndex.model_validate(read_json(self.index_path))

    # ------------------------------------------------------------------ documents
    def doc_path(self, version: int) -> Path:
        if version < 0:
            raise JobError(f"invalid document version {version}")
        return self.doc_dir / f"v{version}.json"

    def doc_versions(self) -> list[int]:
        """Saved document versions, ascending."""
        out = []
        for p in self.doc_dir.glob("v*.json"):
            m = _DOC_RE.match(p.name)
            if m:
                out.append(int(m.group(1)))
        return sorted(out)

    def latest_doc_version(self) -> int | None:
        v = self.doc_versions()
        return v[-1] if v else None

    def save_doc(self, doc: CutDocument, *, overwrite: bool = False) -> Path:
        """Write ``doc/v{doc.version}.json``. Refuses to change an existing version unless ``overwrite``
        (re-saving identical content is a no-op)."""
        p = self.doc_path(doc.version)
        data = to_jsonable(doc)
        if p.exists() and not overwrite:
            if read_json(p) == data:
                return p
            raise JobError(f"document version {doc.version} already saved with different content")
        return write_json_atomic(p, data)

    def load_doc(self, version: int | None = None) -> CutDocument:
        """Load a saved version (latest when ``version`` is None)."""
        from studio.doc.model import CutDocument

        v = self.latest_doc_version() if version is None else version
        if v is None:
            raise JobError(f"job {self.id} has no saved documents")
        p = self.doc_path(v)
        if not p.exists():
            raise JobError(f"job {self.id} has no document v{v}")
        return CutDocument.model_validate(read_json(p))

    def append_oplog(self, entries: Iterable[Mapping[str, Any]]) -> Path:
        """Append op records to ``doc/oplog.jsonl`` (see :func:`studio.doc.ops.oplog_entries`)."""
        return self.append_jsonl("doc/oplog.jsonl", entries)

    def read_oplog(self) -> list[dict[str, Any]]:
        return self.read_jsonl("doc/oplog.jsonl")

    # ------------------------------------------------------------------ asset registry
    def asset_record_path(self, asset_id: str) -> Path:
        from studio.doc.model import ASSET_ID_PATTERN

        if not re.match(ASSET_ID_PATTERN, asset_id or ""):
            raise JobError(f"invalid asset id {asset_id!r}")
        return self.assets_dir / "registry" / f"{asset_id}.json"

    def register_asset(self, asset: AssetRef, *, asset_id: str | None = None, overwrite: bool = False) -> AssetRef:
        """Store an asset record (incl. its licence) so ops can reference it by ID; returns it with ``id``.

        The ID defaults to ``asset.id`` or ``<source>_<source_id>`` (sanitized) or ``<source>_<random>``.
        Registering a different record under an existing ID raises unless ``overwrite``.
        """
        aid = asset_id or asset.id
        if not aid:
            raw = f"{asset.source}_{asset.source_id}" if asset.source_id else f"{asset.source}_{secrets.token_hex(4)}"
            aid = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")[:64] or secrets.token_hex(4)
        rec = asset.model_copy(update={"id": aid})
        p = self.asset_record_path(aid)
        if p.exists() and not overwrite and read_json(p) != to_jsonable(rec):
            raise JobError(f"asset {aid} already registered with a different record")
        write_json_atomic(p, rec)
        return rec

    def load_asset(self, asset_id: str) -> AssetRef | None:
        """Registered asset by ID, or None."""
        from studio.doc.model import AssetRef

        try:
            p = self.asset_record_path(asset_id)
        except JobError:
            return None
        return AssetRef.model_validate(read_json(p)) if p.exists() else None

    def list_assets(self) -> list[AssetRef]:
        from studio.doc.model import AssetRef

        reg = self.assets_dir / "registry"
        return [AssetRef.model_validate(read_json(p)) for p in sorted(reg.glob("*.json"))]

    # ------------------------------------------------------------------ renders
    def render_numbers(self) -> list[int]:
        out = []
        for p in self.renders_dir.iterdir() if self.renders_dir.exists() else []:
            m = _RENDER_RE.match(p.name)
            if m and p.is_dir():
                out.append(int(m.group(1)))
        return sorted(out)

    def new_render_dir(self) -> Path:
        """Create and return the next ``renders/r{n}/`` (n starts at 1)."""
        with self._append_lock:
            n = (self.render_numbers() or [0])[-1] + 1
            while True:
                p = self.renders_dir / f"r{n}"
                try:
                    p.mkdir(parents=True, exist_ok=False)
                    return p
                except FileExistsError:
                    n += 1

    def render_dir(self, n: int) -> Path:
        return self.renders_dir / f"r{n}"

    def latest_render_dir(self) -> Path | None:
        nums = self.render_numbers()
        return self.render_dir(nums[-1]) if nums else None
