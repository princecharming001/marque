"""Work-volume disk budget: estimate, reserve, reclaim (ARCHITECTURE §6 render stages, §8 job directory).

Full-quality renders write large intermediates (ProRes 422 HQ A-roll ≈ 27 MB/s at 1080x1920p30, a ProRes 4444
overlay layer that Remotion copies once more to move its moov atom, float WAV mixes and stems) and every job keeps a
mezzanine of its whole take. On a nearly full volume a render used to fail *after* the Director's work, or run the
disk out of space mid-encode. This module makes the budget explicit:

* **Estimates** (:func:`render_need_bytes`, :func:`mezz_bytes`, :func:`edit_need_bytes`) from calibrated rates
  (measured on the QA takes; see ``RATES``), scaled by pixels x frame rate. The check is ``free ≥ need + headroom``
  (``STUDIO_MIN_FREE_GB``, default 1 GB of headroom for the OS and swap), not a flat floor.
* **Lean intermediates.** When the ProRes path does not fit, the A-roll (and at ingest the mezzanine) is written as
  HEVC Main 4:2:2 10-bit on the VideoToolbox encoder at constant quality 85 with a 15-frame GOP (x264 High 4:2:2
  10-bit CRF 4 when VideoToolbox is unavailable): ~4-5x smaller than ProRes HQ and far below visible loss
  (measured on the d030 mezzanine: q80 56.8 dB / q90 63.3 dB PSNR, x264 CRF 2-8 57.8-62.0 dB, SSIM ≥ 0.9988), so
  the 8-bit 4:2:0 CRF 14-16 master cannot tell the difference. The choice is recorded in the render manifest and
  the trace.
* **Reclaim before refusing** (:func:`reclaim_job`, :func:`reclaim_work_dir`): only *regenerable* files are ever
  removed — render intermediates (never finals, timelines, QA or critique files), overlay-cache entries no render
  links to, and the mezzanine of an idle job (rebuilt from ``media/original.*`` by :func:`ensure_mezz` when that
  job is rendered again). Jobs held by a running edit/chat (:func:`job_activity`, a shared ``flock``) are skipped.
* **One heavy writer at a time** (:func:`render_slot`): full-quality renders and mezzanine encodes take a
  machine-wide lock (``STUDIO_MAX_CONCURRENT_RENDERS``, default 1), so two edits never both pass the disk check and
  then starve each other (and never both hold ~10 GB of render memory). The Director stages of two edits still run
  side by side.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings
    from studio.jobs import Job
    from studio.media.models import MediaInfo

__all__ = [
    "RATES", "LEAN", "HEADROOM_BYTES", "DiskSpaceError", "free_bytes", "headroom", "pixel_scale", "mezz_bytes",
    "render_need_bytes", "speech_span_s", "edit_need_bytes", "job_activity", "job_is_active", "render_slot",
    "RENDER_INTERMEDIATES", "prune_render_intermediates", "prune_overlay_cache", "reclaim_job", "reclaim_work_dir",
    "ensure_space", "choose_aroll_codec", "lean_video_args", "ensure_mezz", "mezz_pruned", "LEAN_VT", "LEAN_X264",
]

GB = 1_000_000_000
REF_RATE = 1920 * 1080 * 30  # pixels per second of the reference (1080x1920p30)

#: bytes per second at 1080x1920p30 (scaled by pixels x fps); measured on the QA takes where noted
RATES: dict[str, float] = {
    "prores_hq": 27.5e6,  # 220 Mbit/s (measured 26.0 MB/s on the 105 s QA take)
    "prores": 18.4e6,  # 147 Mbit/s
    "ffv1": 75.0e6,
    "lean": 9.0e6,  # x264 4:2:2 10-bit CRF 4, g15 (measured 5-8 MB/s on talking heads; margin for grain)
    "overlay": 4.5e6,  # ProRes 4444 + alpha, mostly transparent (measured 3.3 MB/s with captions + cards)
    "final": 2.5e6,  # one x264 CRF 14-16 master (measured 1.6-1.9 MB/s on the delivered QA edits)
    "preview_aroll": 0.6e6,  # 540x960 x264 CRF 20
    "preview_final": 0.8e6,
}
WAV_BYTES_PER_S = 48_000 * 4 * 2  # float32 stereo
#: HEVC 4:2:2 10-bit on VideoToolbox (hardware; constant quality), visually lossless; short GOP for cheap seeks
LEAN_VT: tuple[str, ...] = ("-c:v", "hevc_videotoolbox", "-profile:v", "main42210", "-q:v", "85", "-g", "15",
                            "-tag:v", "hvc1", "-pix_fmt", "p210le")
#: the software fallback: x264 High 4:2:2 10-bit CRF 4, short GOP without B-frames
LEAN_X264: tuple[str, ...] = ("-c:v", "libx264", "-preset", "fast", "-crf", "4", "-g", "15", "-bf", "0",
                              "-pix_fmt", "yuv422p10le")
LEAN = LEAN_VT
HEADROOM_BYTES = 1_000_000_000

#: Render intermediates (never deliverables): A-roll/overlay layers, WAV mixes and stems, conformed stills.
RENDER_INTERMEDIATES: tuple[str, ...] = (
    "aroll.mov", "aroll_preview.mp4", "overlays.mov", "mix.wav", "mix_nomusic.wav", "stems", "_video_*.mp4",
    "still_*.mkv", "*.cover_overlay.mov", "cover_overlay*.mov", ".overlays.rendering*")


class DiskSpaceError(RuntimeError):
    """Not enough free disk for a render or ingest (the message says how much is free and what to do)."""


def free_bytes(path: str | os.PathLike[str]) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def headroom(default: int = HEADROOM_BYTES) -> int:
    """Bytes that must stay free *after* an estimated write (``STUDIO_MIN_FREE_GB``)."""
    v = os.environ.get("STUDIO_MIN_FREE_GB")
    if v:
        with contextlib.suppress(ValueError):
            return max(0, int(float(v) * GB))
    return default


def pixel_scale(width: int, height: int, fps: float) -> float:
    return max(1e-6, (max(1, width) * max(1, height) * max(1.0, float(fps))) / REF_RATE)


def mezz_bytes(duration_s: float, width: int, height: int, fps: float, codec: str) -> int:
    return int(RATES["lean" if codec == "lean" else codec] * pixel_scale(width, height, fps) * max(0.0, duration_s))


def render_need_bytes(duration_s: float, *, width: int = 1080, height: int = 1920, fps: float = 30.0,
                      aroll: str = "prores_hq", preview: bool = False, finals: int = 2) -> int:
    """Peak bytes one render writes: A-roll + overlay layer (x2: Remotion's faststart copy) + frame PNGs, the WAV
    mixes and stems, and the finals."""
    d = max(0.5, float(duration_s))
    k = pixel_scale(width, height, fps)
    frames = d * float(fps)
    overlay = RATES["overlay"] * k * d * 2 + frames * 30_000 * (width * height) / (1080 * 1920)
    wavs = WAV_BYTES_PER_S * d * 4.5  # mix, mix_nomusic, stems (dialogue mono + music/sfx/ambience)
    if preview:
        return int(RATES["preview_aroll"] * d + overlay + wavs + RATES["preview_final"] * d * finals + 20e6)
    return int(RATES[aroll] * k * d + overlay + wavs + RATES["final"] * d * finals + 50e6)


def speech_span_s(index: Any) -> float:
    """Upper-bound-ish output length before the Director has cut: the take minus its dead air (silence gaps)."""
    dur = float(index.media.duration_us) / 1e6
    dead = sum(max(0, g.end_us - g.start_us) for g in getattr(index, "gaps", []) if g.kind == "silence") / 1e6
    return max(min(dur, 5.0), dur - dead)


def edit_need_bytes(index: Any, *, aroll: str = "lean") -> int:
    """Bytes the edit's renders still need (one render in flight + the champion's finals), for the pre-Director
    check."""
    m = index.media
    d = speech_span_s(index)
    return render_need_bytes(d, fps=float(m.fps), aroll=aroll) + int(RATES["final"] * d * 2)


# ============================================================================================ locks
def _slots_dir() -> Path:
    d = Path(os.environ.get("STUDIO_SLOTS_DIR") or tempfile.gettempdir()) / "studio-edit-slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextlib.contextmanager
def job_activity(job: Job) -> Iterator[None]:
    """Mark ``job`` in use for the ``with`` body (shared lock): :func:`reclaim_work_dir` leaves it alone."""
    import fcntl

    job.logs_dir.mkdir(parents=True, exist_ok=True)
    fh = open(job.logs_dir / ".active.lock", "a+")  # noqa: SIM115 - held for the with-body
    try:
        fcntl.flock(fh, fcntl.LOCK_SH)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def job_is_active(root: str | os.PathLike[str]) -> bool:
    import fcntl

    p = Path(root) / "logs" / ".active.lock"
    if not p.exists():
        return False
    try:
        fh = open(p, "a+")  # noqa: SIM115
    except OSError:
        return True
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False
    finally:
        fh.close()


@contextlib.contextmanager
def render_slot(*, poll_s: float = 5.0, on_wait: Callable[[], None] | None = None,
                max_concurrent: int | None = None) -> Iterator[int]:
    """Hold one of ``STUDIO_MAX_CONCURRENT_RENDERS`` (default 1) machine-wide heavy-write slots (full renders,
    mezzanine encodes). Re-entrant within a process."""
    import fcntl

    if _HELD["n"] > 0:
        _HELD["n"] += 1
        try:
            yield -1
        finally:
            _HELD["n"] -= 1
        return
    n = max(1, int(max_concurrent or os.environ.get("STUDIO_MAX_CONCURRENT_RENDERS") or 1))
    d = _slots_dir()
    waited = False
    while True:
        for i in range(n):
            fh = open(d / f"render{i}.lock", "a+")  # noqa: SIM115 - held for the with-body
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fh.close()
                continue
            _HELD["n"] = 1
            try:
                yield i
            finally:
                _HELD["n"] = 0
                with contextlib.suppress(OSError):
                    fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
            return
        if not waited and on_wait is not None:
            on_wait()
        waited = True
        time.sleep(poll_s)


_HELD = {"n": 0}


# ============================================================================================ reclaim
def _size(p: Path) -> int:
    try:
        if p.is_dir():
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        return p.stat().st_size
    except OSError:
        return 0


def prune_render_intermediates(rd: str | os.PathLike[str], *, keep_finals: bool = True) -> tuple[int, list[str]]:
    """Delete a render's intermediates (and its finals when ``keep_finals`` is False). Returns (bytes, names)."""
    rd = Path(rd)
    if not rd.is_dir():
        return 0, []
    freed = 0
    removed: list[str] = []
    pats = list(RENDER_INTERMEDIATES) + ([] if keep_finals else ["final_*.mp4", "cover.jpg"])
    for pat in pats:
        for p in rd.glob(pat):
            size = _size(p)
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            except OSError:
                continue
            freed += size
            removed.append(p.name)
    if removed:
        with contextlib.suppress(OSError, ValueError):
            prev = json.loads((rd / "pruned.json").read_text()) if (rd / "pruned.json").exists() else {}
            prev.setdefault("removed", []).extend(removed)
            prev["bytes"] = int(prev.get("bytes", 0)) + freed
            (rd / "pruned.json").write_text(json.dumps(prev, indent=2))
    return freed, removed


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


def mezz_pruned(job: Job) -> dict[str, Any] | None:
    """The record :func:`reclaim_job` left when it dropped the mezzanine (None when the mezzanine is in place)."""
    if job.mezz_path.exists():
        return None
    p = job.media_dir / "mezz.pruned.json"
    with contextlib.suppress(OSError, ValueError):
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _mezz_rebuildable(job: Job) -> bool:
    return (job.mezz_path.exists() and job.original_path is not None and job.media_info_path.exists()
            and job.audio_path.exists())


def reclaim_job(job: Job, *, keep_intermediates: set[str] | frozenset[str] = frozenset(), drop_mezz: bool = False,
                reason: str = "") -> int:
    """Remove ``job``'s regenerable files: intermediates of every render except ``keep_intermediates`` (finals,
    timelines, QA and critique inputs stay), unlinked overlay-cache entries, and (``drop_mezz``) the mezzanine,
    which :func:`ensure_mezz` rebuilds from ``media/original.*``. Returns bytes freed."""
    freed = 0
    pruned: list[str] = []
    for n in job.render_numbers():
        rd = job.render_dir(n)
        if rd.name in keep_intermediates:
            continue
        b, names = prune_render_intermediates(rd, keep_finals=True)
        if names:
            freed += b
            pruned.append(rd.name)
    freed += prune_overlay_cache(job, keep=0 if not keep_intermediates else 1)
    if drop_mezz and _mezz_rebuildable(job):
        if not (job.media_dir / "active_area.json").exists():  # compiles without the mezzanine read this cache
            with contextlib.suppress(Exception):
                from studio.compile.timeline import _job_active_area

                _job_active_area(job)
        size = _size(job.mezz_path)
        meta = (job.meta.get("meta") or {}).get("ingest") or {}
        rec = {"bytes": size, "codec": (meta.get("mezz") or {}).get("codec"), "reason": reason,
               "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with contextlib.suppress(OSError):
            (job.media_dir / "mezz.pruned.json").write_text(json.dumps(rec, indent=2))
            job.mezz_path.unlink()
            freed += size
            pruned.append("media/mezz.mov")
    if freed:
        with contextlib.suppress(Exception):
            job.trace("prune", note="reclaim", freed_mb=round(freed / 1e6, 1), what=pruned[:30], reason=reason or None)
    return freed


def _job_roots(work_dir: Path) -> list[Path]:
    if not work_dir.is_dir():
        return []
    roots = [p for p in work_dir.iterdir() if p.is_dir() and (p / "job.json").exists()]
    return sorted(roots, key=lambda p: p.stat().st_mtime)  # least recently touched first


def reclaim_work_dir(work_dir: str | os.PathLike[str], *, exclude: str | os.PathLike[str] | None = None,
                     need: int | None = None, drop_mezz: bool = True) -> int:
    """Reclaim regenerable files from *idle* jobs in ``work_dir`` (least recently used first) until ``need``
    bytes are freed (all of them when ``need`` is None). Jobs a running edit/chat holds are skipped."""
    from studio.jobs import Job

    freed = 0
    ex = Path(exclude).resolve() if exclude is not None else None
    for root in _job_roots(Path(work_dir)):
        if need is not None and freed >= need:
            break
        if ex is not None and root.resolve() == ex:
            continue
        if job_is_active(root):
            continue
        try:
            job = Job.open(root)
        except Exception:
            continue
        freed += reclaim_job(job, drop_mezz=drop_mezz, reason="work volume low: idle job reclaimed")
    return freed


def ensure_space(path: str | os.PathLike[str], need: int, *, what: str = "a render",
                 reclaim: Callable[[int], int] | None = None, work_dir: str | os.PathLike[str] | None = None,
                 exclude: str | os.PathLike[str] | None = None, raise_error: bool = True,
                 floor: int | None = None) -> tuple[bool, int]:
    """``free ≥ need + headroom``, reclaiming first from the job itself (``reclaim(short) -> freed``) and then from
    idle jobs in ``work_dir``. Returns ``(ok, free)``; raises :class:`DiskSpaceError` when not ok and
    ``raise_error``. ``floor`` overrides the headroom."""
    floor = headroom() if floor is None else floor
    free = free_bytes(path)
    if free >= need + floor:
        return True, free
    if reclaim is not None:
        with contextlib.suppress(Exception):
            reclaim(need + floor - free)
        free = free_bytes(path)
    if free < need + floor and work_dir is not None:
        reclaim_work_dir(work_dir, exclude=exclude, need=need + floor - free)
        free = free_bytes(path)
    if free >= need + floor:
        return True, free
    if raise_error:
        raise DiskSpaceError(
            f"only {free / GB:.2f} GB free on the work volume; {what} needs about {need / GB:.2f} GB plus "
            f"{floor / GB:.1f} GB headroom (after reclaiming render intermediates and idle jobs' mezzanines). Free "
            "some space, then resume with `studio edit <job_dir>`.")
    return False, free


def choose_aroll_codec(path: str | os.PathLike[str], duration_s: float, *, width: int, height: int, fps: float,
                       reclaim: Callable[[int], int] | None = None, work_dir: str | os.PathLike[str] | None = None,
                       exclude: str | os.PathLike[str] | None = None, what: str = "a full-quality render",
                       floor: int | None = None) -> tuple[str, int]:
    """``("prores_hq" | "lean", need)`` for a full render: ProRes 422 HQ when it fits (after reclaiming), else the
    lean intermediate; raises :class:`DiskSpaceError` when neither fits. ``STUDIO_AROLL_CODEC`` forces one."""
    forced = (os.environ.get("STUDIO_AROLL_CODEC") or "").strip().lower()
    order = [forced] if forced in ("prores_hq", "lean") else ["prores_hq", "lean"]
    last_need = 0
    for codec in order:
        need = render_need_bytes(duration_s, width=width, height=height, fps=fps, aroll=codec)
        last_need = need
        ok, _free = ensure_space(path, need, what=what, reclaim=reclaim, work_dir=work_dir, exclude=exclude,
                                 raise_error=codec == order[-1], floor=floor)
        if ok:
            return codec, need
    raise DiskSpaceError(f"{what} does not fit ({last_need / GB:.2f} GB)")  # pragma: no cover


_ENC_CACHE: dict[str, bool] = {}


def _has_encoder(name: str) -> bool:
    if name not in _ENC_CACHE:
        import subprocess

        try:
            out = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                                 text=True, timeout=20).stdout
            _ENC_CACHE[name] = f" {name} " in out
        except (OSError, subprocess.SubprocessError):
            _ENC_CACHE[name] = False
    return _ENC_CACHE[name]


def lean_video_args(encoder: str = "auto") -> list[str]:
    """Encoder arguments of the lean intermediate (``encoder``: auto | hevc_videotoolbox | libx264)."""
    if encoder == "libx264" or (encoder == "auto" and not _has_encoder("hevc_videotoolbox")):
        return list(LEAN_X264)
    return list(LEAN_VT)


# ============================================================================================ mezzanine
def ensure_mezz(job: Job, *, settings: Settings | None = None, log: Callable[[str], None] | None = None) -> bool:
    """Rebuild ``media/mezz.mov`` when :func:`reclaim_job` dropped it (same filter, pre-roll and codec family as
    the first ingest; the frames are identical). Returns True when a rebuild ran."""
    if job.mezz_path.exists() or not job.media_info_path.exists() or job.original_path is None:
        return False
    from studio.media import ingest as ing

    if log:
        log("rebuilding the mezzanine (it was reclaimed to save disk)")
    with render_slot():
        info = job.load_media_info()
        ing.rebuild_mezzanine(job, info, settings=settings)
    with contextlib.suppress(OSError):
        (job.media_dir / "mezz.pruned.json").unlink()
    job.trace("ingest", note="mezzanine rebuilt after a reclaim")
    return True


def describe(info: MediaInfo) -> str:  # pragma: no cover - debugging helper
    return f"{info.width}x{info.height}@{float(info.fps):.2f}"
