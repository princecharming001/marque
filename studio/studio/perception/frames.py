"""Frame grabs, contact sheets, seam pairs and filmstrips (ARCHITECTURE §4, "view_frames").

Everything a model sees of the picture goes through here, and every image carries **burned IDs and
timecodes** (word ID, frame number, ``M:SS.ss``): grounding a vision model on overlaid frame numbers is
what makes its answers addressable (NumPro, CVPR 2025: 12.5 → 31.3 mIoU). Models never read times off
these sheets to make edits; they answer with the IDs printed on them.

Public API
----------
``probe_video(path)``
    Display geometry (after rotation), exact fps, duration and colour tags (cached per file mtime).
``iter_frames(path, fps=..., width=..., height=...)``
    Streams RGB frames from one ffmpeg process on a CFR grid ``k / fps`` (used by
    :mod:`studio.perception.visual`).
``grab_frame(job, t_us, source=...)`` / ``grab_frames(path, times_us)``
    Frame-exact single-frame decodes.
``contact_sheet(job, items, ...)``
    PNG grid; items are word IDs (frame at the onset; ``"w0012:end"`` / ``":mid"`` for other points),
    gap IDs (snap point), sentence IDs (first word onset), :class:`FrameRequest` or ``int`` µs.
``seam_pairs(render_path, seam_times)``
    For each output seam: the last outgoing frame, the first incoming frame and an onion-skin blend,
    labelled with a pixel-difference score and (when the face model is available locally) the face
    shift/scale across the cut, the numbers that predict a visible jump.
``filmstrip(job, span)``
    Evenly spaced frames over a range with a waveform strip underneath (word boundaries with IDs, gaps
    shaded, frame tick marks), so picture, words and silence can be read together.

Frame exactness
---------------
The frame shown at time ``t`` on a CFR stream is ``n = floor(t * fps)`` (with 2 µs of slack that absorbs
the ±0.5 µs rounding of exact frame instants stored as integer µs). ffmpeg's accurate
input seek returns the first frame whose pts is ≥ the seek target, so we seek to ``(n - 1/4) / fps``,
which lands on frame ``n`` whatever the container time base (verified on NTSC and integer rates, with
B-frames). Output seams sit on the frame grid: the incoming frame is ``round(T * fps)`` and the
outgoing one the frame before it.

Colour
------
Decodes convert YUV→RGB with the matrix the stream is tagged with (BT.709 when untagged: HD/UHD phone
footage), never swscale's silent BT.601 default, so skin in the sheets matches the render. Thumbnails
are scaled by ffmpeg with Lanczos straight from the full-resolution source.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from studio.timebase import US_PER_S, format_us, normalize_fps

if TYPE_CHECKING:  # pragma: no cover
    from PIL import Image as PILImage
    from PIL import ImageFont

    from studio.compile.models import Timeline
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = [
    "FrameRequest",
    "ResolvedFrame",
    "VideoProbe",
    "probe_video",
    "resolve_source",
    "frame_at",
    "frame_time_us",
    "iter_frames",
    "grab_frame",
    "grab_frames",
    "resolve_items",
    "contact_sheet",
    "seam_pairs",
    "filmstrip",
    "read_sheet_meta",
]

SourceName = Literal["mezz", "proxy", "original"]

# ---------------------------------------------------------------------------------------------- style
_BG = (17, 17, 19)
_PANEL = (30, 30, 34)
_FG = (236, 236, 236)
_ID = (255, 214, 10)  # IDs in yellow: the thing a model must quote back
_DIM = (150, 150, 158)
_WAVE = (110, 170, 255)
_WAVE_RMS = (190, 220, 255)
_GAP = (60, 44, 44)
_WORD_LINE = (95, 95, 105)
_TICK = (255, 120, 80)
_MISSING = (70, 20, 20)
_GUTTER = 6
_MAX_SHEET_W = 1600  # Claude downsamples images above ~1568 px on the long side

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)


# ---------------------------------------------------------------------------------------------- tools
def _tools() -> tuple[str, str]:
    """``(ffmpeg, ffprobe)`` executables from settings (falls back to PATH names)."""
    try:
        from studio.config import get_settings

        s = get_settings()
        return s.ffmpeg, s.ffprobe
    except Exception:  # pragma: no cover - settings are always importable in the package
        return "ffmpeg", "ffprobe"


# ---------------------------------------------------------------------------------------------- probe
@dataclass(frozen=True)
class VideoProbe:
    """What frame I/O needs to know about a video file."""

    path: str
    width: int  # display width (after rotation)
    height: int  # display height (after rotation)
    fps: Fraction  # nominal CFR rate (r_frame_rate, normalized)
    duration_us: int
    rotation: int = 0  # display rotation, degrees in {0, 90, 180, 270}
    color_space: str | None = None  # ffprobe matrix name, e.g. "bt709"
    color_transfer: str | None = None
    pix_fmt: str | None = None
    has_audio: bool = False

    @property
    def frame_us(self) -> float:
        return US_PER_S / float(self.fps)

    @property
    def frame_count(self) -> int:
        return max(1, math.floor(self.duration_us * self.fps / US_PER_S + 1e-6))

    @property
    def portrait(self) -> bool:
        return self.height > self.width

    def swscale_matrix(self) -> str:
        """``in_color_matrix`` for swscale (BT.709 unless the stream says otherwise)."""
        cs = (self.color_space or "").lower()
        if cs in ("bt2020nc", "bt2020c", "bt2020"):
            return "bt2020"
        if cs in ("smpte170m", "bt470bg", "bt601"):
            return "bt601"
        if cs == "smpte240m":
            return "smpte240m"
        if cs == "fcc":
            return "fcc"
        return "bt709"


_probe_cache: dict[tuple[str, float, int], VideoProbe] = {}
_probe_lock = threading.Lock()


def _parse_rate(s: str | None) -> Fraction | None:
    if not s or s in ("0/0", "0"):
        return None
    try:
        f = Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None
    return f if f > 0 else None


def probe_video(path: str | os.PathLike[str]) -> VideoProbe:
    """ffprobe the first video stream of ``path`` (cached by path + mtime + size)."""
    p = Path(path)
    st = p.stat()
    key = (str(p.resolve()), st.st_mtime, st.st_size)
    with _probe_lock:
        hit = _probe_cache.get(key)
    if hit is not None:
        return hit
    _, ffprobe = _tools()
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    streams = data.get("streams", [])
    vs = next((s for s in streams if s.get("codec_type") == "video"
               and not (s.get("disposition") or {}).get("attached_pic")), None)
    if vs is None:
        raise ValueError(f"no video stream in {p}")
    rot = 0
    for sd in vs.get("side_data_list") or []:
        if "rotation" in sd:
            with contextlib.suppress(TypeError, ValueError):
                rot = round(float(sd["rotation"]))
    if not rot and (vs.get("tags") or {}).get("rotate"):
        with contextlib.suppress(TypeError, ValueError):
            rot = -int(vs["tags"]["rotate"])
    rot = (-rot) % 360  # ffprobe reports the display-matrix angle (counter-clockwise); normalize to cw
    w, h = int(vs["width"]), int(vs["height"])
    if rot in (90, 270):
        w, h = h, w
    rate = _parse_rate(vs.get("r_frame_rate")) or _parse_rate(vs.get("avg_frame_rate")) or Fraction(30)
    fps = normalize_fps(rate)
    dur_s = None
    for cand in (vs.get("duration"), (data.get("format") or {}).get("duration")):
        with contextlib.suppress(TypeError, ValueError):
            if cand is not None and float(cand) > 0:
                dur_s = float(cand)
                break
    if dur_s is None:
        nb = vs.get("nb_frames")
        dur_s = (int(nb) / float(fps)) if nb and str(nb).isdigit() else 0.0
    res = VideoProbe(
        path=str(p), width=w, height=h, fps=fps, duration_us=round(dur_s * US_PER_S), rotation=rot,
        color_space=vs.get("color_space"), color_transfer=vs.get("color_transfer"), pix_fmt=vs.get("pix_fmt"),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )
    with _probe_lock:
        _probe_cache[key] = res
    return res


# ---------------------------------------------------------------------------------------------- sources
def resolve_source(job: Job | None, source: str | os.PathLike[str] = "proxy") -> Path:
    """Map ``"mezz" | "proxy" | "original" | <path>`` to an existing file.

    ``proxy`` falls back to the mezzanine and then the original when it has not been made yet;
    ``mezz`` falls back to the original (auto-rotated on decode, un-tone-mapped). A path is used as is.
    """
    if isinstance(source, os.PathLike) or (isinstance(source, str) and source not in ("mezz", "proxy", "original")):
        p = Path(source)
        if not p.exists():
            raise FileNotFoundError(f"video source not found: {p}")
        return p
    if job is None:
        raise ValueError(f"source {source!r} needs a job")
    chain = {"proxy": ("proxy", "mezz", "original"), "mezz": ("mezz", "original"), "original": ("original",)}[source]
    for name in chain:
        p = {"proxy": job.proxy_path, "mezz": job.mezz_path, "original": job.original_path}[name]
        if p is not None and Path(p).exists():
            return Path(p)
    raise FileNotFoundError(f"job {job.id} has no {source} video (run ingest first)")


# ---------------------------------------------------------------------------------------------- frame math
def frame_at(t_us: int, fps: Fraction) -> int:
    """Index of the frame displayed at ``t_us`` on the CFR grid ``n / fps`` (floor, with 2 µs of slack for
    frame instants that were rounded to integer µs)."""
    return max(0, math.floor(Fraction(int(t_us) + 2) * Fraction(fps) / US_PER_S))


def frame_time_us(n: int, fps: Fraction) -> int:
    """Start time of frame ``n`` in integer µs (half-up)."""
    return math.floor(Fraction(n) / Fraction(fps) * US_PER_S + Fraction(1, 2))


def _seek_seconds(n: int, fps: Fraction) -> str:
    """Seek target that makes ffmpeg's accurate seek return exactly frame ``n``."""
    t = max(Fraction(0), (Fraction(n) - Fraction(1, 4)) / Fraction(fps))
    return f"{float(t):.6f}"


def _even(x: float) -> int:
    return max(2, round(x / 2.0) * 2)


def _thumb_dims(vp: VideoProbe, width: int | None) -> tuple[int, int]:
    if width is None or width >= vp.width:
        return vp.width, vp.height
    w = _even(width)
    return w, _even(w * vp.height / vp.width)


def _scale_filter(vp: VideoProbe, w: int, h: int, *, flags: str = "lanczos") -> str:
    return (f"scale={w}:{h}:flags={flags}+accurate_rnd+full_chroma_int"
            f":in_color_matrix={vp.swscale_matrix()}:out_range=pc")


# ---------------------------------------------------------------------------------------------- decoding
def iter_frames(
    path: str | os.PathLike[str],
    *,
    fps: Fraction | None = None,
    width: int | None = None,
    height: int | None = None,
    start_us: int = 0,
    end_us: int | None = None,
    scale_flags: str = "area",
    strict: bool = True,
) -> Iterator[tuple[int, int, np.ndarray]]:
    """Stream ``(k, t_us, rgb)`` frames on the CFR grid ``t = k / fps`` (default: the stream's rate).

    One ffmpeg process decodes the display-oriented picture (auto-rotated), resamples it to the grid
    with the ``fps`` filter (nearest source frame, ``start_time=0`` so frame ``k`` is exactly ``k/fps``
    from the file start) and scales it to ``width``×``height`` (default: display size) as RGB24 with the
    tagged YUV matrix. ``k`` counts from 0 at ``start_us``'s grid frame; ``t_us`` is absolute.
    With ``strict`` a stream that ends more than max(0.5 s, 5 %) short of the probed duration (a
    truncated or corrupt file) raises ``RuntimeError`` instead of silently yielding fewer frames.
    """
    vp = probe_video(path)
    fps = normalize_fps(fps) if fps is not None else vp.fps
    if width is None and height is None:
        w, h = vp.width, vp.height
    elif height is None:
        w, h = _even(width), _even(width * vp.height / vp.width)  # type: ignore[operator]
    elif width is None:
        w, h = _even(height * vp.width / vp.height), _even(height)
    else:
        w, h = _even(width), _even(height)
    k0 = frame_at(start_us, fps) if start_us > 0 else 0
    total = math.floor(Fraction(vp.duration_us) * fps / US_PER_S + Fraction(1, 1000))
    if end_us is not None:
        total = min(total, math.ceil(Fraction(end_us) * fps / US_PER_S - Fraction(1, 1000)))
    n_frames = max(0, total - k0)
    if n_frames <= 0:
        return
    ffmpeg, _ = _tools()
    rate = f"{fps.numerator}/{fps.denominator}"
    scale = _scale_filter(vp, w, h, flags=scale_flags)
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error"]
    if k0 > 0:
        # input seek to just before frame k0 (timestamps then restart near 0): re-anchor the grid on k0
        t0 = float(Fraction(k0) / fps)
        cmd += ["-ss", _seek_seconds(k0, fps)]
        vf = f"setpts=PTS-STARTPTS+{t0:.6f}/TB,fps=fps={rate}:start_time={t0:.6f}:round=near,{scale}"
    else:
        vf = f"fps=fps={rate}:start_time=0:round=near,{scale}"
    cmd += ["-i", str(path), "-map", "0:v:0", "-an", "-sn", "-dn"]
    cmd += ["-vf", vf, "-frames:v", str(n_frames), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    frame_bytes = w * h * 3
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, bufsize=frame_bytes * 2)
        assert proc.stdout is not None
        k = 0
        killed = False
        try:
            while k < n_frames:
                buf = proc.stdout.read(frame_bytes)
                if not buf or len(buf) < frame_bytes:
                    break  # EOF: the stream ended (possibly a frame short of the probed duration)
                yield k0 + k, frame_time_us(k0 + k, fps), np.frombuffer(buf, np.uint8).reshape(h, w, 3)
                k += 1
        finally:  # also runs when the consumer stops early (GeneratorExit skips the check below)
            with contextlib.suppress(Exception):
                proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
                killed = True
            rc = proc.wait()
        short = n_frames - k
        if (not killed and rc != 0) or (strict and short > max(float(fps) * 0.5, 0.05 * n_frames)):
            # a decode error or a truncated file must never silently shorten an analysis
            err.seek(0)
            tail = err.read().decode("utf-8", "replace")[-800:]
            raise RuntimeError(f"ffmpeg decoded only {k} of {n_frames} frames from {path} (exit {rc}): {tail}")


def _grab(vp: VideoProbe, n: int, w: int, h: int) -> np.ndarray:
    ffmpeg, _ = _tools()
    n = min(max(0, n), vp.frame_count - 1)
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-ss", _seek_seconds(n, vp.fps), "-i", vp.path,
           "-map", "0:v:0", "-an", "-sn", "-dn", "-frames:v", "1", "-vf", _scale_filter(vp, w, h),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg frame grab failed ({vp.path} frame {n}): "
                           f"{r.stderr.decode('utf-8', 'replace')[-400:]}")
    if len(r.stdout) < w * h * 3:
        # the container's last frames can sit a hair past the probed duration (or before it): step back
        if n > 0 and n - 1 >= vp.frame_count - 4:
            return _grab(vp, n - 1, w, h)
        return _blank(w, h)
    return np.frombuffer(r.stdout[: w * h * 3], np.uint8).reshape(h, w, 3).copy()


def _blank(w: int, h: int) -> np.ndarray:
    return np.zeros((h, w, 3), np.uint8)


def grab_frames(
    path: str | os.PathLike[str],
    times_us: Sequence[int],
    *,
    width: int | None = None,
    frames: Sequence[int] | None = None,
    workers: int = 4,
) -> list[np.ndarray]:
    """Frame-exact RGB frames (``HxWx3 uint8``) at source times (or explicit frame indices)."""
    vp = probe_video(path)
    w, h = _thumb_dims(vp, width)
    idx = list(frames) if frames is not None else [frame_at(t, vp.fps) for t in times_us]
    if not idx:
        return []
    cache: dict[int, np.ndarray] = {}
    uniq = sorted(set(idx))
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(uniq)))) as ex:
        for n, img in zip(uniq, ex.map(lambda n: _grab(vp, n, w, h), uniq), strict=True):
            cache[n] = img
    return [cache[n] for n in idx]


def grab_frame(
    job: Job | str | os.PathLike[str] | None,
    t_us: int,
    *,
    source: str | os.PathLike[str] = "proxy",
    width: int | None = None,
) -> np.ndarray:
    """Decode one RGB frame (``HxWx3 uint8``) at source time ``t_us`` (the frame displayed at that time).

    ``job`` may be a :class:`~studio.jobs.Job` (then ``source`` names ``mezz``/``proxy``/``original`` or a
    path) or directly a video path.
    """
    from studio.jobs import Job

    path = resolve_source(job, source) if isinstance(job, Job) or job is None else Path(job)
    return grab_frames(path, [int(t_us)], width=width)[0]


# ---------------------------------------------------------------------------------------------- items
@dataclass(frozen=True)
class FrameRequest:
    """A frame at a measured source time (code-derived, never model-provided)."""

    t_us: int
    label: str = ""


@dataclass
class ResolvedFrame:
    """One sheet item resolved to a time on the chosen source's clock (passed to label functions)."""

    ref: str  # "w0012", "w0012:end", "g0003", "s002", "t=1.234567s"
    kind: str  # "word" | "gap" | "sentence" | "time"
    t_us: int | None  # None when the item is not present in the source (e.g. a cut word in a render)
    text: str = ""
    label: str = ""
    frame: int | None = None


def _split_ref(item: str) -> tuple[str, str, bool]:
    """``"w0012:end"`` → ``("w0012", "end", True)``; a bare ID → ``(id, "start", False)``."""
    base, sep, point = item.partition(":")
    point = point or "start"
    if point not in ("start", "mid", "end"):
        raise ValueError(f"bad item point {item!r} (use :start, :mid or :end)")
    return base, point, bool(sep)


def _pick(start: int, end: int, point: str) -> int:
    if point == "start":
        return start
    if point == "end":
        return max(start, end - 1)
    return (start + end) // 2


def resolve_items(
    items: Sequence[str | FrameRequest | int],
    *,
    index: TakeIndex | None = None,
    timeline: Timeline | None = None,
    index_loader: Callable[[], TakeIndex] | None = None,
) -> list[ResolvedFrame]:
    """Resolve word/gap/sentence IDs, :class:`FrameRequest` and ``int`` µs to times.

    With a ``timeline`` (render sources) word IDs map to output time via ``timeline.word_map``
    (``t_us=None`` when the word was cut); gap/sentence IDs resolve through their bordering words.
    Unknown IDs raise ``KeyError``.
    """
    from studio.perception.index import GAP_ID_RE, SENTENCE_ID_RE, WORD_ID_RE

    ix = index
    out: list[ResolvedFrame] = []

    def need_ix() -> TakeIndex:
        nonlocal ix
        if ix is None:
            if index_loader is None:
                raise ValueError("ID items need a Take Index (pass index=...)")
            ix = index_loader()
        return ix

    def out_span(wid: str) -> tuple[int, int] | None:
        assert timeline is not None
        if wid not in timeline.word_map and not need_ix().has_word(wid):
            raise KeyError(f"unknown word id {wid!r}")
        sp = timeline.word_map.get(wid)
        if sp is None:
            return None
        return (math.floor(Fraction(sp.out_start) * US_PER_S + Fraction(1, 2)),
                math.floor(Fraction(sp.out_end) * US_PER_S + Fraction(1, 2)))

    for it in items:
        if isinstance(it, FrameRequest):
            out.append(ResolvedFrame(ref=f"t={it.t_us / US_PER_S:.3f}s", kind="time", t_us=int(it.t_us),
                                     label=it.label))
            continue
        if isinstance(it, (int, np.integer)) and not isinstance(it, bool):
            out.append(ResolvedFrame(ref=f"t={int(it) / US_PER_S:.3f}s", kind="time", t_us=int(it)))
            continue
        if not isinstance(it, str):
            raise TypeError(f"unsupported sheet item {it!r}")
        it = it.strip()
        base, point, explicit = _split_ref(it)
        if WORD_ID_RE.match(base):
            w = need_ix().word(base)
            if timeline is not None:
                sp = out_span(base)
                t = None if sp is None else _pick(sp[0], sp[1], point)
            else:
                t = _pick(w.start_us, w.end_us, point)
            out.append(ResolvedFrame(ref=it, kind="word", t_us=t, text=w.display()))
        elif GAP_ID_RE.match(base):
            g = need_ix().gap(base)
            if timeline is not None:
                # a gap exists in a render only between two kept words; use the midpoint between them
                a = out_span(g.after_word_id) if g.after_word_id else None
                b = out_span(g.before_word_id) if g.before_word_id else None
                if a is not None and b is not None and b[0] >= a[1]:
                    t = {"start": a[1], "mid": (a[1] + b[0]) // 2, "end": max(a[1], b[0] - 1)}[point]
                else:
                    t = None
            else:  # a bare gap ID is its snap point (the quietest instant); :start/:mid/:end are literal
                t = _pick(g.start_us, g.end_us, point) if explicit else g.snap_us
            out.append(ResolvedFrame(ref=it, kind="gap", t_us=t, text=f"[{g.duration_ms / 1000:.2f}s {g.kind}]"))
        elif SENTENCE_ID_RE.match(base):
            s = need_ix().sentence(base)
            if timeline is not None:
                first = out_span(s.first_word)
                last = out_span(s.last_word)
                if first is None or last is None:
                    t = None
                else:
                    t = _pick(first[0], last[1], point)
            else:
                t = _pick(s.start_us, s.end_us, point)
            out.append(ResolvedFrame(ref=it, kind="sentence", t_us=t, text=s.text))
        else:
            raise KeyError(f"unknown item {it!r} (expected wNNNN, gNNNN, sNNN, FrameRequest or µs int)")
    return out


# ---------------------------------------------------------------------------------------------- drawing
_font_cache: dict[int, Any] = {}


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    from PIL import ImageFont

    size = max(8, int(size))
    f = _font_cache.get(size)
    if f is not None:
        return f
    for cand in _FONT_CANDIDATES:
        if os.path.exists(cand):
            try:
                f = ImageFont.truetype(cand, size)
                break
            except OSError:
                continue
    if f is None:
        f = ImageFont.load_default(size=size)
    _font_cache[size] = f
    return f


def _text_w(font: Any, text: str) -> int:
    try:
        return int(font.getlength(text))
    except AttributeError:  # pragma: no cover - bitmap fallback
        return len(text) * 7


def _fit(font: Any, text: str, max_w: int) -> str:
    if _text_w(font, text) <= max_w:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _text_w(font, text[:mid] + ell) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ell


def _line_h(font: Any) -> int:
    try:
        a, d = font.getmetrics()
        return int(a + d + 2)
    except AttributeError:  # pragma: no cover
        return 14


def _default_thumb_w(vp: VideoProbe, columns: int) -> int:
    base = 270 if vp.portrait else 400
    return max(96, min(base, (_MAX_SHEET_W - _GUTTER * (columns + 1)) // max(1, columns)))


def _render_grid(
    tiles: Sequence[tuple[np.ndarray | None, list[tuple[str, tuple[int, int, int]]]]],
    *,
    thumb_w: int,
    thumb_h: int,
    columns: int,
    title: str | None,
    subtitle: str | None,
    lines_per_tile: int,
) -> tuple[PILImage.Image, list[list[int]]]:
    """Draw the grid; returns the image and each tile's thumbnail rectangle ``[x, y, w, h]``."""
    from PIL import Image, ImageDraw

    columns = max(1, min(columns, len(tiles))) if tiles else 1
    rows = max(1, math.ceil(len(tiles) / columns))
    fsize = max(11, min(22, thumb_w // 15))
    font = _font(fsize)
    lh = _line_h(font)
    label_h = lh * lines_per_tile + 6
    head_font = _font(fsize + 3)
    head_h = (_line_h(head_font) + (lh if subtitle else 0) + 10) if (title or subtitle) else 0
    W = columns * thumb_w + (columns + 1) * _GUTTER
    H = head_h + rows * (thumb_h + label_h) + (rows + 1) * _GUTTER
    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)
    y = 5
    if title:
        draw.text((_GUTTER, y), _fit(head_font, title, W - 2 * _GUTTER), font=head_font, fill=_FG)
        y += _line_h(head_font)
    if subtitle:
        draw.text((_GUTTER, y), _fit(font, subtitle, W - 2 * _GUTTER), font=font, fill=_DIM)
    rects: list[list[int]] = []
    for i, (arr, lines) in enumerate(tiles):
        r, c = divmod(i, columns)
        x0 = _GUTTER + c * (thumb_w + _GUTTER)
        y0 = head_h + _GUTTER + r * (thumb_h + label_h + _GUTTER)
        rects.append([x0, y0, thumb_w, thumb_h])
        if arr is None:
            draw.rectangle([x0, y0, x0 + thumb_w - 1, y0 + thumb_h - 1], fill=_MISSING)
        else:
            tile = Image.fromarray(arr)
            if tile.size != (thumb_w, thumb_h):
                tile = tile.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            img.paste(tile, (x0, y0))
        draw.rectangle([x0, y0 + thumb_h, x0 + thumb_w - 1, y0 + thumb_h + label_h - 1], fill=_PANEL)
        ty = y0 + thumb_h + 3
        for text, color in lines[:lines_per_tile]:
            draw.text((x0 + 4, ty), _fit(font, text, thumb_w - 8), font=font, fill=color)
            ty += lh
    return img, rects


def _default_out(job: Job | None, kind: str, key: Any, near: Path | None = None) -> Path:
    digest = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:12]
    if job is not None:
        d = job.critique_dir / "frames"
    elif near is not None:
        d = near.parent
    else:  # pragma: no cover - callers always pass one
        d = Path(tempfile.gettempdir())
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{kind}_{digest}.png"


def _save_png(img: PILImage.Image, out_path: str | os.PathLike[str], meta: dict[str, Any] | None = None) -> Path:
    """Atomic PNG write; ``meta`` is stored as JSON in the ``studio`` text chunk (see :func:`read_sheet_meta`)."""
    from PIL.PngImagePlugin import PngInfo

    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp.png")
    info = None
    if meta is not None:
        info = PngInfo()
        info.add_text("studio", json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
    img.save(tmp, format="PNG", compress_level=6, pnginfo=info)
    os.replace(tmp, p)
    return p


def read_sheet_meta(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Machine-readable layout of a sheet written by this module: which tile shows which item, its time,
    frame and ``rect`` (``[x, y, w, h]`` of the thumbnail), plus seam metrics / filmstrip range."""
    from PIL import Image

    with Image.open(path) as im:
        raw = getattr(im, "text", {}).get("studio") or im.info.get("studio")
    return json.loads(raw) if raw else {}


def _timeline_for_render(render: Path, timeline: Timeline | str | os.PathLike[str] | None) -> Timeline | None:
    from studio.compile.models import Timeline

    if isinstance(timeline, Timeline):
        return timeline
    if timeline is not None:
        return Timeline.load(timeline)
    cand = render.parent / "timeline.json"
    if cand.exists():
        with contextlib.suppress(Exception):
            return Timeline.load(cand)
    return None


def _is_named_source(source: Any) -> bool:
    return isinstance(source, str) and source in ("mezz", "proxy", "original")


# ---------------------------------------------------------------------------------------------- contact sheet
def contact_sheet(
    job: Job | None,
    items: Sequence[str | FrameRequest | int],
    *,
    label: str | Callable[[ResolvedFrame], str | None] | None = None,
    index: TakeIndex | None = None,
    source: str | os.PathLike[str] = "proxy",
    columns: int = 4,
    cols: int | None = None,
    thumb_w: int | None = None,
    out_path: str | os.PathLike[str] | None = None,
    title: str | None = None,
    timeline: Timeline | str | os.PathLike[str] | None = None,
) -> Path:
    """Render a PNG grid of frames with burned IDs + timecodes; returns the PNG path.

    Each tile shows the frame and, under it, ``<ID>  <M:SS.ss>  f<frame>`` plus a second line with the
    word/sentence text (or the :class:`FrameRequest` label). ``label`` is either a sheet title (str) or
    a function ``ResolvedFrame -> str`` whose result replaces the second line. ``source`` is
    ``"mezz" | "proxy" | "original"`` or a path to any video (e.g. a render; word IDs then map to output
    time through ``timeline`` or ``<render dir>/timeline.json``). Default output:
    ``<job>/critique/frames/sheet_<hash>.png``.
    """
    if cols is not None:
        columns = cols
    if not items:
        raise ValueError("contact_sheet needs at least one item")
    path = resolve_source(job, source)
    named = _is_named_source(source)
    vp = probe_video(path)
    tl = None if named else _timeline_for_render(path, timeline)
    loader = job.load_index if job is not None else None
    resolved = resolve_items(items, index=index, timeline=tl, index_loader=loader)
    if not named and tl is None and any(r.kind != "time" for r in resolved):
        raise ValueError("word/gap/sentence IDs on a render need its compiled timeline (timeline=...)")
    columns = max(1, columns)
    tw = _even(thumb_w) if thumb_w else _default_thumb_w(vp, min(columns, len(resolved)))
    tw, th = _thumb_dims(vp, tw)
    present = [r for r in resolved if r.t_us is not None]
    for r in present:
        r.frame = min(frame_at(r.t_us, vp.fps), vp.frame_count - 1)  # type: ignore[arg-type]
    imgs = grab_frames(path, [], width=tw, frames=[r.frame for r in present])  # type: ignore[misc]
    by_id = {id(r): im for r, im in zip(present, imgs, strict=True)}
    fn = label if callable(label) else None
    sheet_title = title or (label if isinstance(label, str) else None)
    tiles = []
    for r in resolved:
        if r.t_us is None:
            head = f"{r.ref}  —  not in this render"
            second = r.text
        else:
            head = f"{r.ref}  {format_us(r.t_us)}  f{r.frame}"
            second = r.label or r.text
        if fn is not None:
            custom = fn(r)
            if custom:
                second = custom
        tiles.append((by_id.get(id(r)), [(head, _ID), (second, _FG)]))
    src_name = source if named else Path(path).name
    subtitle = f"{src_name} · {vp.width}x{vp.height} @ {float(vp.fps):.3f} fps · {len(resolved)} frames"
    img, rects = _render_grid(tiles, thumb_w=tw, thumb_h=th, columns=columns, title=sheet_title,
                              subtitle=subtitle, lines_per_tile=2)
    out = Path(out_path) if out_path else _default_out(job, "sheet", (str(path), [r.ref for r in resolved], tw,
                                                                       columns, sheet_title), near=path)
    meta = {"kind": "contact_sheet", "source": str(path), "fps": f"{vp.fps.numerator}/{vp.fps.denominator}",
            "tiles": [{"ref": r.ref, "kind": r.kind, "t_us": r.t_us, "frame": r.frame, "rect": rect}
                      for r, rect in zip(resolved, rects, strict=True)]}
    return _save_png(img, out, meta)


# ---------------------------------------------------------------------------------------------- seam pairs
def _seam_frame(t: Fraction | int | float, fps: Fraction) -> int:
    """Incoming frame index for a seam: Fractions/floats are seconds, ints are µs."""
    if isinstance(t, bool):
        raise TypeError("bool is not a seam time")
    if isinstance(t, (int, np.integer)):
        sec = Fraction(int(t), US_PER_S)
    else:
        sec = Fraction(t) if isinstance(t, Fraction) else Fraction(str(float(t)))
    return max(0, math.floor(sec * Fraction(fps) + Fraction(1, 2)))


def _luma_small(a: np.ndarray, w: int = 96) -> np.ndarray:
    from PIL import Image

    im = Image.fromarray(a)
    h = max(2, round(w * im.height / im.width))
    small = np.asarray(im.resize((w, h), Image.Resampling.BILINEAR), dtype=np.float32)
    return small @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def _pixel_delta(a: np.ndarray, b: np.ndarray) -> float:
    """Mean absolute luma difference on 96-px-wide downscales, as % of full scale (0 = identical)."""
    return float(np.mean(np.abs(_luma_small(a) - _luma_small(b))) / 255.0 * 100.0)


def _face_boxes(frames: Sequence[np.ndarray]) -> list[tuple[float, float, float, float] | None]:
    """Normalized face boxes ``(cx, cy, w, h)`` via the face landmarker (IMAGE mode); Nones when the model
    file is not available locally (never downloads from here)."""
    try:
        from studio.perception.visual import detect_face_boxes

        return detect_face_boxes(frames, allow_download=False)
    except Exception:
        return [None] * len(frames)


def seam_pairs(
    render_path: str | os.PathLike[str],
    seam_times: Sequence[Fraction | int | float] | None = None,
    *,
    timeline: Timeline | str | os.PathLike[str] | None = None,
    out_path: str | os.PathLike[str] | None = None,
    thumb_w: int | None = None,
    columns: int = 2,
    onion: bool = True,
    face_delta: bool = True,
    labels: Sequence[str] | None = None,
    job: Job | None = None,
) -> Path:
    """PNG of the frames either side of each seam (outgoing last frame | incoming first frame | onion).

    ``seam_times`` are output times: Fractions/floats in seconds (``Timeline.seams``), ints in µs;
    default = ``timeline.seams``. Each group is labelled ``seam k @ M:SS.ss`` with ``Δpx`` (mean absolute
    luma difference, % of full scale) and, when faces are found on both sides, the face-centre shift (%
    of frame height) and scale ratio: small shift + ratio near 1.0 is a visible micro-jump; a clear
    ≥1.2x change reads as an intentional punch. ``columns`` = seams per row.
    """
    path = Path(render_path)
    if not path.exists():
        raise FileNotFoundError(f"render not found: {path}")
    vp = probe_video(path)
    if seam_times is None:
        tl = _timeline_for_render(path, timeline)
        seam_times = list(tl.seams) if tl is not None else []
    seams = list(seam_times)
    if not seams:
        raise ValueError("seam_pairs needs at least one seam time")
    per = 3 if onion else 2
    tw = _even(thumb_w) if thumb_w else max(96, min(240 if vp.portrait else 320,
                                                  (_MAX_SHEET_W - _GUTTER * (columns * per + 1)) // (columns * per)))
    tw, th = _thumb_dims(vp, tw)
    idx_in = [min(max(1, _seam_frame(t, vp.fps)), vp.frame_count - 1) for t in seams]
    frames = grab_frames(path, [], width=tw, frames=[n - 1 for n in idx_in] + idx_in)
    outs, ins = frames[: len(seams)], frames[len(seams):]
    boxes = _face_boxes(outs + ins) if face_delta else [None] * (2 * len(seams))
    tiles = []
    info: list[dict[str, Any]] = []
    for k, (n, a, b) in enumerate(zip(idx_in, outs, ins, strict=True)):
        name = labels[k] if labels and k < len(labels) else f"seam {k + 1}"
        t_us = frame_time_us(n, vp.fps)
        dpx = _pixel_delta(a, b)
        fa, fb = boxes[k], boxes[len(seams) + k]
        shift = ratio = None
        if fa is not None and fb is not None:
            shift = math.hypot((fb[0] - fa[0]) * vp.width / vp.height, fb[1] - fa[1]) * 100.0
            ratio = (fb[3] / fa[3]) if fa[3] > 0 else None
            face_txt = f"face Δ{shift:.1f}%H ×{ratio:.2f}" if ratio is not None else f"face Δ{shift:.1f}%H"
        elif face_delta and (fa is not None) != (fb is not None):
            face_txt = "face on one side only"
        else:
            face_txt = ""
        info.append({"name": name, "t_us": t_us, "frame_out": n - 1, "frame_in": n, "px_delta": round(dpx, 3),
                     "face_shift_pct_h": None if shift is None else round(shift, 3),
                     "face_scale": None if ratio is None else round(ratio, 4),
                     "face_out": fa is not None if face_delta else None,
                     "face_in": fb is not None if face_delta else None})
        tiles.append((a, [(f"{name} OUT f{n - 1}", _ID), (f"@ {format_us(t_us)}  Δpx {dpx:.1f}", _FG)]))
        tiles.append((b, [(f"{name} IN f{n}", _ID), (face_txt or f"@ {format_us(t_us)}", _FG)]))
        if onion:
            blend = ((a.astype(np.uint16) + b.astype(np.uint16)) // 2).astype(np.uint8)
            tiles.append((blend, [(f"{name} onion", _ID), ("out+in 50/50", _DIM)]))
    img, rects = _render_grid(tiles, thumb_w=tw, thumb_h=th, columns=columns * per,
                              title=f"Seams · {path.name}",
                              subtitle=f"{len(seams)} seams · {vp.width}x{vp.height} @ {float(vp.fps):.3f} fps · "
                                       "OUT = last outgoing frame, IN = first incoming frame",
                              lines_per_tile=2)
    for k, rec in enumerate(info):
        group = rects[k * per:(k + 1) * per]
        rec["rects"] = {"out": group[0], "in": group[1], **({"onion": group[2]} if onion else {})}
    out = Path(out_path) if out_path else (
        _default_out(job, "seams", (str(path), [str(s) for s in seams], tw)) if job is not None
        else path.with_name(f"{path.stem}_seams.png"))
    meta = {"kind": "seam_pairs", "source": str(path), "fps": f"{vp.fps.numerator}/{vp.fps.denominator}",
            "seams": info}
    return _save_png(img, out, meta)


# ---------------------------------------------------------------------------------------------- filmstrip
def _span_of(ref: str | FrameRequest | int, ix_loader: Callable[[], TakeIndex] | None, index: TakeIndex | None,
             timeline: Timeline | None) -> tuple[int, int, str]:
    r = resolve_items([ref], index=index, timeline=timeline, index_loader=ix_loader)[0]
    if r.t_us is None:
        raise ValueError(f"{r.ref} is not present in this source")
    if r.kind == "time" or (isinstance(ref, str) and _split_ref(ref.strip())[2]):
        return r.t_us, r.t_us, r.ref
    # whole object span
    s = resolve_items([f"{ref}:start", f"{ref}:end"], index=index, timeline=timeline, index_loader=ix_loader)
    a, b = s[0].t_us, s[1].t_us
    if a is None or b is None:
        raise ValueError(f"{ref} is not present in this source")
    return a, b + 1, r.ref


def _read_audio(job: Job | None, path: Path, named: bool, start_us: int, end_us: int,
                sr: int = 48_000) -> tuple[np.ndarray, int] | None:
    """Mono float32 audio for ``[start_us, end_us)`` from ``media/audio.wav`` (source clock) or, for a
    render, from the file itself. None when there is no audio."""
    if named and job is not None and job.audio_path.exists():
        import soundfile as sf

        info = sf.info(str(job.audio_path))
        a = int(start_us * info.samplerate // US_PER_S)
        b = math.ceil(end_us * info.samplerate / US_PER_S)
        data, fs = sf.read(str(job.audio_path), start=max(0, a), stop=max(a + 1, b), dtype="float32",
                           always_2d=True)
        return data.mean(axis=1), int(fs)
    vp = probe_video(path)
    if not vp.has_audio:
        return None
    ffmpeg, _ = _tools()
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-ss", f"{start_us / US_PER_S:.6f}", "-i", str(path),
           "-t", f"{max(1, end_us - start_us) / US_PER_S:.6f}", "-map", "0:a:0", "-ac", "1", "-ar", str(sr),
           "-f", "f32le", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    return np.frombuffer(r.stdout, np.float32).copy(), sr


def filmstrip(
    job: Job | None,
    span: str | FrameRequest | int | tuple[str | FrameRequest | int, str | FrameRequest | int],
    *,
    n_frames: int = 8,
    source: str | os.PathLike[str] = "proxy",
    index: TakeIndex | None = None,
    timeline: Timeline | str | os.PathLike[str] | None = None,
    thumb_w: int | None = None,
    pad_ms: int = 0,
    waveform_height: int = 140,
    out_path: str | os.PathLike[str] | None = None,
    title: str | None = None,
) -> Path:
    """Frames evenly spaced over a range with a waveform strip underneath; returns the PNG path.

    ``span`` is a single ID (``"s003"`` = the sentence, ``"w0012"`` = the word, ``"g0004"`` = the gap) or
    a ``(start, end)`` pair of items (IDs, :class:`FrameRequest`, µs ints; ``end`` is inclusive of the
    end item's own span). The waveform shows the peak envelope and RMS, word boundaries with IDs and
    text, gaps shaded (source clock only) and a tick at each sampled frame. ``pad_ms`` adds context on
    both sides.
    """
    from PIL import Image, ImageDraw

    path = resolve_source(job, source)
    named = _is_named_source(source)
    vp = probe_video(path)
    tl = None if named else _timeline_for_render(path, timeline)
    loader = job.load_index if job is not None else None
    ix = index
    refs = span if isinstance(span, tuple) else (span,)
    if not named and tl is None and any(isinstance(r, str) for r in refs):
        raise ValueError("word/gap/sentence IDs on a render need its compiled timeline (timeline=...)")
    if isinstance(span, tuple):
        a0, _a1, ra = _span_of(span[0], loader, ix, tl)
        _b0, b1, rb = _span_of(span[1], loader, ix, tl)
        start, end, name = a0, b1, f"{ra} → {rb}"
    else:
        start, end, name = _span_of(span, loader, ix, tl)
    if end <= start:
        end = start + int(vp.frame_us)
    start = max(0, start - pad_ms * 1000)
    end = min(vp.duration_us, end + pad_ms * 1000)
    if end <= start:
        raise ValueError(f"empty filmstrip range {start}..{end} µs")
    n = max(1, int(n_frames))
    tw = _even(thumb_w) if thumb_w else max(96, min(240 if vp.portrait else 360,
                                                  (_MAX_SHEET_W - _GUTTER * (n + 1)) // n))
    tw, th = _thumb_dims(vp, tw)
    times = [start + int((i + 0.5) * (end - start) / n) for i in range(n)]
    frames_idx = [min(frame_at(t, vp.fps), vp.frame_count - 1) for t in times]
    imgs = grab_frames(path, [], width=tw, frames=frames_idx)
    tiles = [(im, [(f"f{fi}  {format_us(frame_time_us(fi, vp.fps))}", _ID)]) for im, fi in zip(imgs, frames_idx,
                                                                                               strict=True)]
    src_name = source if named else path.name
    grid, rects = _render_grid(tiles, thumb_w=tw, thumb_h=th, columns=n,
                        title=title or f"{name}  {format_us(start)}–{format_us(end)}",
                        subtitle=f"{src_name} · {(end - start) / US_PER_S:.2f}s · {n} frames", lines_per_tile=1)

    # ---- waveform strip
    W = grid.width
    font = _font(max(10, min(16, tw // 16)))
    lh = _line_h(font)
    words_h = 2 * lh + 4
    strip = Image.new("RGB", (W, waveform_height + words_h + lh + 8), _BG)
    d = ImageDraw.Draw(strip)
    x_left, x_right = _GUTTER, W - _GUTTER
    span_us = end - start

    def x_of(t: int) -> int:
        return round(x_left + (t - start) / span_us * (x_right - x_left))

    wave_top, wave_bot = 2, waveform_height - 2
    mid = (wave_top + wave_bot) // 2
    d.rectangle([x_left, wave_top, x_right, wave_bot], fill=_PANEL)
    # gaps and words (source clock with an index; render clock through the timeline word map)
    word_spans: list[tuple[int, int, str, str]] = []
    gap_spans: list[tuple[int, int]] = []
    try:
        ixx = ix if ix is not None else (loader() if loader is not None else None)
    except Exception:
        ixx = None
    if ixx is not None:
        if tl is None and named:
            for g in ixx.gaps:
                if g.end_us > start and g.start_us < end:
                    gap_spans.append((g.start_us, g.end_us))
            for w in ixx.words_between_us(start, end):
                word_spans.append((w.start_us, w.end_us, w.id, w.display()))
        elif tl is not None:
            for wid, sp in tl.word_map.items():
                if sp is None or not ixx.has_word(wid):
                    continue
                a = math.floor(Fraction(sp.out_start) * US_PER_S)
                b = math.floor(Fraction(sp.out_end) * US_PER_S)
                if b > start and a < end:
                    word_spans.append((a, b, wid, ixx.word(wid).display()))
            word_spans.sort()
    for a, b in gap_spans:
        d.rectangle([max(x_left, x_of(a)), wave_top, min(x_right, x_of(b)), wave_bot], fill=_GAP)
    audio = _read_audio(job, path, named, start, end)
    if audio is not None and audio[0].size:
        sig, _fs = audio
        cols = max(1, x_right - x_left)
        edges = np.linspace(0, sig.size, cols + 1).astype(int)
        peak_all = float(np.max(np.abs(sig))) if sig.size else 0.0
        scale = (wave_bot - wave_top) / 2 / max(peak_all, 0.05)
        for c in range(cols):
            seg = sig[edges[c]:max(edges[c] + 1, edges[c + 1])]
            if seg.size == 0:
                continue
            hi, lo = float(seg.max()), float(seg.min())
            rms = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
            x = x_left + c
            d.line([(x, mid - hi * scale), (x, mid - lo * scale)], fill=_WAVE)
            d.line([(x, mid - rms * scale), (x, mid + rms * scale)], fill=_WAVE_RMS)
    else:
        d.text((x_left + 6, mid - lh // 2), "no audio", font=font, fill=_DIM)
    d.line([(x_left, mid), (x_right, mid)], fill=_WORD_LINE)
    # word boundaries + labels (two staggered rows so neighbours don't collide)
    last_x = [-(10**9), -(10**9)]
    for i, (a, b, wid, text) in enumerate(word_spans):
        xa, xb = max(x_left, x_of(a)), min(x_right, x_of(b))
        d.line([(xa, wave_top), (xa, wave_bot)], fill=_WORD_LINE)
        row = i % 2
        tx = max(xa, last_x[row] + 4)
        label = f"{wid} {text}"
        if tx + _text_w(font, wid) <= x_right:
            room = max(_text_w(font, wid), min(xb - tx + 60, x_right - tx))
            s = _fit(font, label, room)
            ty = waveform_height + 2 + row * lh
            d.text((tx, ty), s, font=font, fill=_ID if row == 0 else _FG)
            last_x[row] = tx + _text_w(font, s)
    # frame ticks
    for t in times:
        x = x_of(t)
        d.line([(x, wave_top), (x, wave_top + 10)], fill=_TICK, width=2)
        d.line([(x, wave_bot - 10), (x, wave_bot)], fill=_TICK, width=2)
    d.text((x_left, waveform_height + words_h + 2), f"{format_us(start)}", font=font, fill=_DIM)
    end_s = format_us(end)
    d.text((x_right - _text_w(font, end_s), waveform_height + words_h + 2), end_s, font=font, fill=_DIM)

    out_img = Image.new("RGB", (W, grid.height + strip.height), _BG)
    out_img.paste(grid, (0, 0))
    out_img.paste(strip, (0, grid.height))
    out = Path(out_path) if out_path else _default_out(job, "filmstrip", (str(path), start, end, n, tw), near=path)
    meta = {"kind": "filmstrip", "source": str(path), "fps": f"{vp.fps.numerator}/{vp.fps.denominator}",
            "start_us": start, "end_us": end,
            "frames": [{"frame": fi, "t_us": frame_time_us(fi, vp.fps), "rect": rect}
                       for fi, rect in zip(frames_idx, rects, strict=True)],
            "waveform_rect": [x_left, grid.height + wave_top, x_right - x_left, wave_bot - wave_top],
            "words": [{"id": wid, "start_us": a, "end_us": b} for a, b, wid, _t in word_spans]}
    return _save_png(out_img, out, meta)
