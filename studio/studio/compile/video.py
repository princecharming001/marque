"""A-roll video render: Timeline → ``aroll.mov`` (ProRes 422 HQ, 1080x1920, timeline fps).

Pipeline (one pass, one resampling of the speaker's pixels, one encode)::

    mezz.mov ──ffmpeg decode per piece (exact frames)──▶ Python geometry ──raw 10-bit──▶ ffmpeg
                                                          (sub-pixel crop +                grade LUT
                                                           Lanczos-3 resample)            b-roll layers
                                                                                           ProRes 422 HQ

Why the geometry is done in Python rather than with ``crop``/``scale``/``zoompan``:

* **No jitter.** ffmpeg's ``crop`` positions are whole (chroma-aligned) pixels and ``zoompan`` rounds to
  whole pixels, so slow pushes, eased punches and dead-zone re-centres stair-step. Here every frame's
  window is resampled at its exact sub-pixel position with Pillow's separable Lanczos-3 (``resize`` with
  a float ``box``), whose kernel is widened by the downscale factor (correct anti-aliasing when a 4K
  mezzanine is reduced to 1080x1920; plain Lanczos interpolation for punch-ins that upsample).
* **One code path** for static crops, animated framing, speed changes and split-screen layouts, so a
  hold after an ease lands on exactly the pixels the ease ended on.
* **Frame-exact retiming.** Each output frame is mapped to a source frame index from the timeline's
  rational times (nearest-frame retiming for speed changes, as doctrine requires for a speaking face:
  never frame-blend or optical-flow a face). Pieces are decoded with an accurate input seek, so there is
  no concat/trim timestamp ambiguity; the total frame count equals ``timeline.frame_count`` by
  construction and is verified after encoding.

Each Y'CbCr plane is resampled at its native subsampling with correct chroma siting (MPEG-2 "left",
co-sited 4:2:2 output), in float, from the full-resolution mezzanine; values stay in limited range and
are rounded once to 10 bits. Identity windows (a 9:16 source rendered 1:1) are copied bit-exactly.

The ffmpeg stage then:

* applies the colour spec as **one 65³ 3D LUT** (tetrahedral) built in float from the ColorSpec in the
  doctrine's order (exposure in linear light with a soft highlight shoulder, white balance as von
  Kries gains from a Planckian illuminant + temp/tint, pivoted contrast, luma-preserving saturation,
  optional look blended at ``lut_strength``), in 16-bit RGB with explicit BT.709 limited↔full
  conversions (zscale, error-diffusion dither on the way back to 10-bit) — correct range handling and no
  8-bit ``eq`` stage;
* composites b-roll layers from the conformed asset files named in ``timeline.inserts``: full-screen
  cutaways (the voice continues, the A-roll picture is covered), split top/bottom (the speaker is
  re-framed into the remaining region by the Python stage), picture-in-picture (rounded corners and a
  soft shadow, alpha-masked) and card underlays; transitions (fade/dissolve, slide, zoom, whip) are
  alpha/position animations. Stills get a gentle 6 % push rendered sub-pixel in Python (doctrine: 5–10 %
  over the hold). The look (not the A-roll's primary correction) is applied to footage b-roll only,
  never to screenshots or cards;
* encodes ProRes 422 HQ (``prores_ks`` profile 3, yuv422p10le, BT.709 tagged) — or, for previews, a
  540x960 H.264 ``veryfast`` file.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from studio.compile.models import Timeline, TimelineInsert, TimelineSegment
from studio.compile.timeline import crop_window, framing_state
from studio.doc.model import AssetRef
from studio.timebase import normalize_fps, round_fraction

if TYPE_CHECKING:  # pragma: no cover
    from studio.doc.model import ColorSpec
    from studio.jobs import Job

__all__ = [
    "RenderError", "VideoProbe", "probe_video", "render_aroll", "source_frame_map", "build_grade_lut",
    "write_cube", "grade_is_identity", "OUTPUT_PIX_FMT",
]

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
OUTPUT_PIX_FMT = "yuv422p10le"
_BLACK10 = (64, 512, 512)
_STILL_PUSH = 1.06
_IMAGE_KINDS = frozenset({"image", "generated_image", "screenshot"})
_FOOTAGE_KINDS = frozenset({"video", "generated_video", "gif"})


class RenderError(RuntimeError):
    """ffmpeg failed or the render does not match the timeline."""


# ============================================================================================ probing
@dataclass
class VideoProbe:
    path: str
    width: int
    height: int
    pix_fmt: str
    fps: Fraction
    nb_frames: int | None = None
    duration_s: float | None = None
    codec: str | None = None
    color_space: str | None = None
    color_range: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    chroma_location: str | None = None
    rotation: int = 0

    @property
    def frame_count(self) -> int:
        if self.nb_frames:
            return self.nb_frames
        if self.duration_s:
            return max(1, round(self.duration_s * float(self.fps)))
        return 0


def probe_video(path: str | os.PathLike[str]) -> VideoProbe:
    """ffprobe the first video stream."""
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,pix_fmt,r_frame_rate,avg_frame_rate,nb_frames,duration,codec_name,color_space,"
         "color_range,color_transfer,color_primaries,chroma_location:stream_side_data=rotation:"
         "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RenderError(f"ffprobe failed on {path}: {out.stderr.strip()[-500:]}")
    data = json.loads(out.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise RenderError(f"no video stream in {path}")
    s = streams[0]
    rate = s.get("r_frame_rate") or s.get("avg_frame_rate") or "30/1"
    try:
        fps = normalize_fps(rate)
    except (ValueError, ZeroDivisionError):
        fps = Fraction(30)
    dur = s.get("duration") or (data.get("format") or {}).get("duration")
    nb = s.get("nb_frames")
    rot = 0
    for sd in s.get("side_data_list") or []:
        if "rotation" in sd:
            rot = round(float(sd["rotation"])) % 360
    return VideoProbe(
        path=str(path), width=int(s["width"]), height=int(s["height"]), pix_fmt=s.get("pix_fmt") or "",
        fps=fps, nb_frames=int(nb) if nb not in (None, "N/A", "0") else None,
        duration_s=float(dur) if dur not in (None, "N/A") else None, codec=s.get("codec_name"),
        color_space=s.get("color_space"), color_range=s.get("color_range"),
        color_transfer=s.get("color_transfer"), color_primaries=s.get("color_primaries"),
        chroma_location=s.get("chroma_location"), rotation=rot,
    )


# ============================================================================================ pixel formats
@dataclass(frozen=True)
class _Fmt:
    name: str
    bits: int
    sub_x: int
    sub_y: int

    @property
    def dtype(self) -> type:
        return np.uint8 if self.bits == 8 else np.uint16

    def plane_shapes(self, w: int, h: int) -> list[tuple[int, int]]:
        cw, ch = -(-w // self.sub_x), -(-h // self.sub_y)
        return [(h, w), (ch, cw), (ch, cw)]

    def frame_bytes(self, w: int, h: int) -> int:
        item = 1 if self.bits == 8 else 2
        return sum(a * b for a, b in self.plane_shapes(w, h)) * item


_FMTS: dict[str, _Fmt] = {f.name: f for f in (
    _Fmt("yuv420p", 8, 2, 2), _Fmt("yuv422p", 8, 2, 1), _Fmt("yuv444p", 8, 1, 1),
    _Fmt("yuv420p10le", 10, 2, 2), _Fmt("yuv422p10le", 10, 2, 1), _Fmt("yuv444p10le", 10, 1, 1),
    _Fmt("yuv420p12le", 12, 2, 2), _Fmt("yuv422p12le", 12, 2, 1), _Fmt("yuv444p12le", 12, 1, 1),
    _Fmt("yuv444p16le", 16, 1, 1),
)}
_OUT_FMT = _FMTS[OUTPUT_PIX_FMT]


def _decode_format(probe: VideoProbe) -> tuple[_Fmt, bool]:
    """(format to decode to, needs conversion) — native planar Y'CbCr is decoded as is."""
    if probe.pix_fmt in _FMTS and (probe.color_range or "tv") != "pc":
        return _FMTS[probe.pix_fmt], False
    return _FMTS["yuv444p16le"], True


def _siting(fmt: _Fmt, chroma_location: str | None) -> tuple[float, float]:
    """(d_x, d_y) chroma offsets in the plane→luma relation ``L = sub*c + d`` (edge coordinates)."""
    loc = (chroma_location or "left").lower()
    dx = -0.5 if (fmt.sub_x == 2 and loc in ("left", "topleft", "bottomleft", "unspecified", "unknown")) else 0.0
    dy = -0.5 if (fmt.sub_y == 2 and loc in ("topleft", "top")) else 0.0
    return dx, dy


# ============================================================================================ resampling
def _plane_box(win: tuple[float, float, float, float], region_px: tuple[int, int], plane: int, src: _Fmt,
               src_siting: tuple[float, float], plane_size: tuple[int, int]) -> tuple[float, float, float, float]:
    """Pillow ``box`` (source plane edge coordinates) so that output plane samples land exactly where the
    luma-space window maps them (chroma siting honoured on both sides; output is co-sited 4:2:2)."""
    x0, y0, w, h = win
    rw, rh = region_px
    if plane == 0:
        sx, sy, dx, dy = 1, 1, 0.0, 0.0
        osx, odx = 1, 0.0
    else:
        sx, sy = src.sub_x, src.sub_y
        dx, dy = src_siting
        osx, odx = _OUT_FMT.sub_x, -0.5  # output chroma: co-sited horizontally, no vertical subsampling
    del osx
    rx = w / rw
    bx0 = (x0 + odx * rx - dx) / sx
    by0 = (y0 - dy) / sy
    bw, bh = w / sx, h / sy
    pw, ph = plane_size
    bw, bh = min(bw, pw), min(bh, ph)
    bx0 = min(max(bx0, 0.0), pw - bw)
    by0 = min(max(by0, 0.0), ph - bh)
    return (bx0, by0, bx0 + bw, by0 + bh)


def _to_code10(plane: np.ndarray, bits: int) -> np.ndarray:
    f = plane.astype(np.float32)
    if bits != 10:
        f *= np.float32(2.0 ** (10 - bits))
    return f


def _resample(plane_f: np.ndarray, box: tuple[float, float, float, float], size: tuple[int, int]) -> np.ndarray:
    img = Image.fromarray(plane_f)
    return np.asarray(img.resize(size, Image.Resampling.LANCZOS, box=box), dtype=np.float32)


def _quantize10(a: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(a), 0, 1023).astype(np.uint16)


@dataclass
class _Job:
    planes: list[np.ndarray] | None  # decoded source planes (native dtype)
    windows: list[tuple[tuple[float, float, float, float], tuple[int, int, int, int]]]  # (win, region px)


class _FrameRenderer:
    def __init__(self, out_w: int, out_h: int, src: _Fmt, src_w: int, src_h: int, siting: tuple[float, float]):
        self.W, self.H = out_w, out_h
        self.src, self.src_w, self.src_h, self.siting = src, src_w, src_h, siting
        shapes = src.plane_shapes(src_w, src_h)
        self.src_plane_sizes = [(s[1], s[0]) for s in shapes]  # (w, h)
        self._black = self._blank()

    def _blank(self) -> bytes:
        y = np.full((self.H, self.W), _BLACK10[0], np.uint16)
        c = np.full((self.H, self.W // 2), _BLACK10[1], np.uint16)
        return y.tobytes() + c.tobytes() + c.tobytes()

    @property
    def black(self) -> bytes:
        return self._black

    def render(self, job: _Job) -> bytes:
        if job.planes is None:
            return self._black
        out = [np.full((self.H, self.W), _BLACK10[0], np.uint16),
               np.full((self.H, self.W // 2), _BLACK10[1], np.uint16),
               np.full((self.H, self.W // 2), _BLACK10[2], np.uint16)]
        cache: dict[int, np.ndarray] = {}
        for win, (rx, ry, rw, rh) in job.windows:
            identity = (self.src.sub_x == _OUT_FMT.sub_x and self.src.sub_y == _OUT_FMT.sub_y
                        and rw == self.src_w and rh == self.src_h
                        and abs(win[0]) < 1e-6 and abs(win[1]) < 1e-6
                        and abs(win[2] - self.src_w) < 1e-6 and abs(win[3] - self.src_h) < 1e-6)
            for p in range(3):
                src_plane = job.planes[p]
                if identity:
                    if self.src.bits == 10:
                        res = src_plane
                    else:
                        res = _quantize10(_to_code10(src_plane, self.src.bits))
                    if p == 0:
                        out[0][ry:ry + rh, rx:rx + rw] = res
                    else:
                        out[p][ry:ry + rh, rx // 2:(rx + rw) // 2] = res
                    continue
                if p not in cache:
                    cache[p] = _to_code10(src_plane, self.src.bits)
                box = _plane_box(win, (rw, rh), p, self.src, self.siting, self.src_plane_sizes[p])
                size = (rw, rh) if p == 0 else (rw // 2, rh)
                res = _quantize10(_resample(cache[p], box, size))
                if p == 0:
                    out[0][ry:ry + rh, rx:rx + rw] = res
                else:
                    out[p][ry:ry + rh, rx // 2:(rx + rw) // 2] = res
        return out[0].tobytes() + out[1].tobytes() + out[2].tobytes()


# ============================================================================================ frame plan
def source_frame_map(timeline: Timeline, src_fps: Fraction | None = None, src_frames: int | None = None
                     ) -> list[tuple[int, int]]:
    """For every output frame ``f``: ``(segment index, source frame index)`` — nearest-frame retiming of
    ``src_in + (f/fps - out_start) * speed`` (exact rationals, half-up)."""
    fps = timeline.fps
    sfps = normalize_fps(src_fps) if src_fps is not None else fps
    out: list[tuple[int, int]] = []
    for si, seg in enumerate(timeline.segments):
        f0 = round_fraction(seg.out_start * fps)
        f1 = round_fraction(seg.out_end * fps)
        pos = Fraction(seg.src_in_us, 1_000_000) * sfps
        if abs(pos - round(pos)) < Fraction(1, 1000):  # a frame-aligned instant stored as rounded µs
            pos = Fraction(round(pos))
        speed = Fraction(repr(seg.speed)) if isinstance(seg.speed, float) else Fraction(seg.speed)
        for f in range(f0, f1):
            k = round_fraction(pos + (Fraction(f) / fps - seg.out_start) * speed * sfps)
            if src_frames is not None:
                k = min(max(k, 0), src_frames - 1)
            out.append((si, max(0, k)))
    return out


def _even(v: float) -> int:
    return round(v / 2) * 2


def _speaker_rect(timeline: Timeline, W: int, H: int, f: int, drawn: set[str]) -> tuple[int, int, int, int]:
    """Pixel rect of the speaker at output frame ``f``: the full frame, or the complement of an active
    split-screen layer that is actually drawn (asset file or designed card). Boundaries are even and
    identical to the layer's own pixel rect."""
    fr = Fraction(f)
    for ins in timeline.inserts:
        if ins.mode not in ("split_top", "split_bottom") or ins.rect is None or ins.insert_id not in drawn:
            continue
        if not (round_fraction(ins.out_start * timeline.fps) <= fr < round_fraction(ins.out_end * timeline.fps)):
            continue
        _x, y, _w, h = ins.rect
        if ins.mode == "split_top":
            y0 = _even(h * H)
            return (0, y0, W, max(2, H - y0))
        y1 = _even(y * H)
        return (0, 0, W, max(2, y1))
    return (0, 0, W, H)


def _transition_frames(tr: Any, fps: Fraction) -> int:
    if tr is None or tr.kind == "cut" or tr.ms <= 0:
        return 0
    return max(1, round_fraction(Fraction(tr.ms, 1000) * fps))


def _usable_path(ins: TimelineInsert, job: Job | None) -> str | None:
    if ins.asset_path and Path(ins.asset_path).exists():
        return ins.asset_path
    if isinstance(ins.asset, AssetRef) and ins.asset.path and job is not None:
        p = Path(ins.asset.path)
        p = p if p.is_absolute() else job.root / p
        if p.exists():
            return str(p)
    return None


def _covered_frames(timeline: Timeline, paths: dict[str, str]) -> set[int]:
    """Output frames fully hidden under an opaque full-screen layer (no transition in progress)."""
    fps = timeline.fps
    cov: set[int] = set()
    for ins in timeline.inserts:
        if ins.mode not in ("full", "card") or ins.insert_id not in paths:
            continue
        s = round_fraction(ins.out_start * fps)
        e = round_fraction(ins.out_end * fps)
        s += _transition_frames(ins.transition_in, fps)
        e -= _transition_frames(ins.transition_out, fps)
        cov.update(range(s, e))
    return cov


# ============================================================================================ decoding
def plan_chunks(needs: list[tuple[int, list[int]]], max_len: int = 12, max_gap: int = 6
                ) -> list[tuple[int, list[int]]]:
    """Split each piece's needed source frames into short runs decoded by separate processes (a new run
    starts after ``max_len`` frames or a skip of more than ``max_gap`` frames)."""
    out: list[tuple[int, list[int]]] = []
    for piece, ks in needs:
        cur: list[int] = []
        for k in ks:
            if cur and (k - cur[0] >= max_len or k - cur[-1] > max_gap):
                out.append((piece, cur))
                cur = []
            cur.append(k)
        if cur:
            out.append((piece, cur))
    return out


class _ChunkDecoder(threading.Thread):
    """Decodes one run of source frames (exact frames via an accurate input seek) into its own queue."""

    def __init__(self, owner: _Decoder, piece: int, ks: list[int]):
        super().__init__(daemon=True)
        self.o, self.piece, self.ks = owner, piece, ks
        self.q: queue.Queue[tuple[int, int, list[np.ndarray]] | Exception | None] = queue.Queue()
        self.proc: subprocess.Popen[bytes] | None = None

    def run(self) -> None:
        o = self.o
        try:
            kmin, kmax = self.ks[0], self.ks[-1]
            want = set(self.ks)
            cmd = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error"]
            if kmin > 0:
                cmd += ["-ss", f"{float((Fraction(kmin) - Fraction(1, 2)) / o.src_fps):.9f}"]
            # passthrough timing: in the default CFR mode ffmpeg duplicates the first frame after an accurate
            # seek whenever the input has more than one stream (the first kept frame sits half a frame after
            # the seek point), which would shift every frame of the run by one
            cmd += ["-i", o.src, "-map", "0:v:0", "-an", "-sn", "-dn", "-frames:v", str(kmax - kmin + 1),
                    "-fps_mode", "passthrough"]
            vf = []
            if o.scaled:
                vf.append(f"scale={o.w}:{o.h}:flags=area+accurate_rnd")
            if o.convert:
                vf.append("scale=out_range=tv")
            if vf:
                cmd += ["-vf", ",".join(vf)]
            cmd += ["-f", "rawvideo", "-pix_fmt", o.fmt.name, "pipe:1"]
            with open(o.log, "ab") as lf:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=lf)
            assert self.proc.stdout is not None
            k = kmin
            last: list[np.ndarray] | None = None
            while k <= kmax and not o.stopped.is_set():
                buf = self.proc.stdout.read(o.nbytes)
                if len(buf) < o.nbytes:
                    break
                last = _split_planes(buf, o.fmt, o.shapes)
                if k in want:
                    self.q.put((self.piece, k, last))
                k += 1
            self.proc.stdout.close()
            self.proc.wait()
            if k <= kmax and not o.stopped.is_set():  # the source ended early: hold the last frame
                if last is None:
                    raise RenderError(f"could not decode frames {kmin}-{kmax} of {o.src}")
                for kk in range(k, kmax + 1):
                    if kk in want:
                        self.q.put((self.piece, kk, last))
            self.q.put(None)
        except Exception as e:  # pragma: no cover - surfaced in the consumer
            self.q.put(e)

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()


class _Decoder:
    """Runs up to ``parallel`` chunk decoders ahead of the consumer and yields frames in order. A single
    ffmpeg→pipe stream tops out well below the resampler's throughput on 4K 10-bit sources, so runs are
    decoded concurrently; memory is bounded by ``parallel x max_len`` frames."""

    def __init__(self, src: str, fmt: _Fmt, convert: bool, src_w: int, src_h: int, src_fps: Fraction,
                 needs: list[tuple[int, list[int]]], log: Path, decode_size: tuple[int, int] | None,
                 parallel: int = 3):
        self.src, self.fmt, self.convert = src, fmt, convert
        self.w, self.h = decode_size or (src_w, src_h)
        self.scaled = decode_size is not None
        self.src_fps, self.log = src_fps, log
        self.nbytes = fmt.frame_bytes(self.w, self.h)
        self.shapes = fmt.plane_shapes(self.w, self.h)
        # ~400 MB of decoded frames per run: 12 frames of 4K 10-bit 4:2:2, ~48 at 1080p, whole pieces when small
        max_len = int(min(240, max(12, (400 << 20) // max(1, self.nbytes))))
        self.chunks = plan_chunks(needs, max_len=max_len, max_gap=max(6, max_len // 2))
        self.parallel = max(1, parallel)
        self.stopped = threading.Event()
        self.threads: list[_ChunkDecoder] = []
        self.ci = 0

    def _ensure(self) -> None:
        while len(self.threads) < min(len(self.chunks), self.ci + self.parallel):
            piece, ks = self.chunks[len(self.threads)]
            t = _ChunkDecoder(self, piece, ks)
            self.threads.append(t)
            t.start()

    def next(self) -> tuple[int, int, list[np.ndarray]]:
        while True:
            if self.ci >= len(self.chunks):
                raise RenderError("decoder exhausted")
            self._ensure()
            item = self.threads[self.ci].q.get()
            if item is None:
                self.threads[self.ci] = None  # type: ignore[call-overload]  # free the finished run
                self.ci += 1
                continue
            if isinstance(item, Exception):
                raise item
            return item

    def stop(self) -> None:
        self.stopped.set()
        for t in self.threads:
            if t is not None:
                t.kill()


def _split_planes(buf: bytes, fmt: _Fmt, shapes: list[tuple[int, int]]) -> list[np.ndarray]:
    arr = np.frombuffer(buf, dtype=np.dtype(fmt.dtype).newbyteorder("<"))
    out = []
    off = 0
    for h, w in shapes:
        n = h * w
        out.append(arr[off:off + n].reshape(h, w))
        off += n
    return out


# ============================================================================================ colour
def grade_is_identity(color: ColorSpec | None) -> bool:
    if color is None:
        return True
    return (abs(color.exposure) < 1e-6 and color.white_balance_k is None and abs(color.temp) < 1e-6
            and abs(color.tint) < 1e-6 and abs(color.contrast - 1.0) < 1e-6 and abs(color.saturation - 1.0) < 1e-6
            and (not color.look or color.look.lower() in ("none", "") or color.lut_strength <= 0))


def _planck_xy(k: float) -> tuple[float, float]:
    """CIE 1931 xy of a Planckian radiator (Kim et al. 2002 cubic approximation, 1667–25000 K)."""
    t = min(max(k, 1667.0), 25000.0)
    if t <= 4000:
        x = -0.2661239e9 / t**3 - 0.2343589e6 / t**2 + 0.8776956e3 / t + 0.179910
    else:
        x = -3.0258469e9 / t**3 + 2.1070379e6 / t**2 + 0.2226347e3 / t + 0.240390
    if t <= 2222:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif t <= 4000:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483
    return x, y


_XYZ_TO_709 = np.array([[3.2404542, -1.5371385, -0.4985314],
                        [-0.9692660, 1.8760108, 0.0415560],
                        [0.0556434, -0.2040259, 1.0572252]])
_LUMA = np.array([0.2126, 0.7152, 0.0722])


def _illuminant_rgb(k: float) -> np.ndarray:
    x, y = _planck_xy(k)
    xyz = np.array([x / y, 1.0, (1 - x - y) / y])
    rgb = _XYZ_TO_709 @ xyz
    return rgb / rgb[1]


def _wb_gains(color: ColorSpec) -> np.ndarray:
    g = np.ones(3)
    if color.white_balance_k is not None:
        # "white balance set to K" (camera semantics): neutralize a K-kelvin illuminant to D65 white
        g = _illuminant_rgb(6504.0) / _illuminant_rgb(float(color.white_balance_k))
    g = g * np.array([2.0 ** (0.15 * color.temp), 2.0 ** (-0.10 * color.tint), 2.0 ** (-0.15 * color.temp)])
    return g / float(_LUMA @ g)


def _contrast(v: np.ndarray, c: float, pivot: float = 0.45) -> np.ndarray:
    if abs(c - 1.0) < 1e-9:
        return v
    v = np.clip(v, 0.0, 1.0)
    lo = pivot * np.power(np.clip(v / pivot, 0.0, None), c)
    hi = 1.0 - (1.0 - pivot) * np.power(np.clip((1.0 - v) / (1.0 - pivot), 0.0, None), c)
    return np.where(v < pivot, lo, hi)


def _saturate(v: np.ndarray, s: float) -> np.ndarray:
    if abs(s - 1.0) < 1e-9:
        return v
    y = (v @ _LUMA)[..., None]
    return y + s * (v - y)


def _primary(rgb: np.ndarray, color: ColorSpec) -> np.ndarray:
    """Exposure → white balance (linear light) → shoulder → contrast → saturation (encoded domain)."""
    gamma = 2.4
    lin = np.power(np.clip(rgb, 0.0, 1.0), gamma)
    gains = _wb_gains(color) * (2.0 ** color.exposure)
    lin = lin * gains
    v = np.power(np.clip(lin, 0.0, None), 1.0 / gamma)
    vmax = float(np.max(gains)) ** (1.0 / gamma)
    if vmax > 1.0001:  # soft highlight shoulder instead of a hard clip (protect the top of the range)
        knee = 0.85
        over = v > knee
        v = np.where(over, knee + (1.0 - knee) * np.tanh((v - knee) / (1.0 - knee)), v)
    v = _contrast(v, color.contrast)
    v = _saturate(v, color.saturation)
    return np.clip(v, 0.0, 1.0)


def _look_fn(name: str, job: Job | None) -> Any:
    n = name.strip()
    key = n.lower().replace("-", "_").replace(" ", "_")

    def warm(v: np.ndarray, t: float) -> np.ndarray:
        g = np.array([2.0 ** (0.15 * t), 1.0, 2.0 ** (-0.15 * t)])
        g = g / float(_LUMA @ g)
        lin = np.power(np.clip(v, 0, 1), 2.4) * g
        return np.clip(np.power(np.clip(lin, 0, None), 1 / 2.4), 0, 1)

    if key == "clean":
        return lambda v: np.clip(_saturate(_contrast(warm(v, 0.2), 1.08), 1.03), 0, 1)
    if key == "warm":
        return lambda v: np.clip(_saturate(_contrast(warm(v, 0.35), 1.04), 1.02), 0, 1)
    if key == "cool":
        return lambda v: np.clip(_contrast(warm(v, -0.3), 1.03), 0, 1)
    if key in ("film", "warm_film", "filmic"):
        def film(v: np.ndarray) -> np.ndarray:
            v = warm(v, 0.25)
            v = 0.02 + 0.96 * v  # lifted blacks, softened top
            knee = 0.8
            v = np.where(v > knee, knee + (1 - knee) * np.tanh((v - knee) / (1 - knee) * 1.2) / 1.0, v)
            return np.clip(_saturate(_contrast(v, 1.05), 0.94), 0, 1)
        return film
    if key in ("bw", "b_w", "mono", "monochrome", "black_and_white", "blackandwhite"):
        return lambda v: np.clip(_contrast(np.repeat((v @ _LUMA)[..., None], 3, axis=-1), 1.05), 0, 1)
    p = Path(n)
    if p.suffix.lower() == ".cube":
        if not p.is_absolute() and job is not None:
            p = job.root / p
        if p.exists():
            lut = _read_cube(p)
            return lambda v: _apply_lut(lut, v)
    return None


def _read_cube(path: Path) -> np.ndarray:
    size = None
    rows: list[list[float]] = []
    dmin, dmax = np.zeros(3), np.ones(3)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        up = s.upper()
        if up.startswith("LUT_3D_SIZE"):
            size = int(s.split()[1])
        elif up.startswith("DOMAIN_MIN"):
            dmin = np.array([float(x) for x in s.split()[1:4]])
        elif up.startswith("DOMAIN_MAX"):
            dmax = np.array([float(x) for x in s.split()[1:4]])
        elif up[0].isalpha():
            continue
        else:
            rows.append([float(x) for x in s.split()[:3]])
    if size is None or len(rows) != size**3:
        raise RenderError(f"bad .cube file {path}")
    lut = np.array(rows, dtype=np.float64).reshape(size, size, size, 3)  # [b][g][r]
    lut = np.transpose(lut, (2, 1, 0, 3))  # → [r][g][b]
    if not (np.allclose(dmin, 0) and np.allclose(dmax, 1)):
        lut = (lut - dmin) / (dmax - dmin)
    return lut


def _apply_lut(lut: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Trilinear lookup of ``v`` (…,3) in a [r][g][b] LUT."""
    n = lut.shape[0]
    x = np.clip(v, 0.0, 1.0) * (n - 1)
    i0 = np.clip(np.floor(x).astype(int), 0, n - 2)
    f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    c = 0.0
    for dr in (0, 1):
        for dg in (0, 1):
            for db in (0, 1):
                w = (fr if dr else 1 - fr) * (fg if dg else 1 - fg) * (fb if db else 1 - fb)
                c = c + w * lut[r0 + dr, g0 + dg, b0 + db]
    return c


def build_grade_lut(color: ColorSpec | None, *, size: int = 65, look_only: bool = False,
                    job: Job | None = None) -> np.ndarray | None:
    """3D LUT ``[r][g][b] → rgb`` (full-range BT.709 R'G'B') for the ColorSpec, or None when identity.
    ``look_only`` builds the look alone (for footage b-roll, which is matched by conform, not graded)."""
    if color is None:
        return None
    look = None
    if color.look and color.look.lower() not in ("none", "") and color.lut_strength > 0:
        look = _look_fn(color.look, job)
    if look_only and look is None:
        return None
    if not look_only and grade_is_identity(color) and look is None:
        return None
    ax = np.linspace(0.0, 1.0, size)
    r, g, b = np.meshgrid(ax, ax, ax, indexing="ij")
    v = np.stack([r, g, b], axis=-1)
    out = v if look_only else _primary(v, color)
    if look is not None:
        lv = look(out)
        out = out + float(color.lut_strength) * (lv - out)
    return np.clip(out, 0.0, 1.0)


def write_cube(path: str | os.PathLike[str], lut: np.ndarray, title: str = "yunicorn grade") -> Path:
    """Write a ``.cube`` file (red varies fastest)."""
    n = lut.shape[0]
    p = Path(path)
    data = np.transpose(lut, (2, 1, 0, 3)).reshape(-1, 3)  # b outer, r inner
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{a:.6f} {b:.6f} {c:.6f}" for a, b, c in data]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


_SETP709 = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
_GRADE_IN = "zscale=rin=limited:min=709:pin=709:tin=709:r=full:p=709:t=709:filter=spline36,format=gbrp16le"
_GRADE_OUT = ("zscale=m=709:r=limited:p=709:t=709:filter=spline36:d=error_diffusion,format={fmt},"
              "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")


# ============================================================================================ inserts graph
def _pip_assets(render_dir: Path, name: str, w: int, h: int, radius: int, shadow: bool) -> tuple[str, str | None]:
    """Anti-aliased rounded-rect mask (and soft shadow) PNGs for a PiP of ``w x h``."""
    ss = 4
    big = Image.new("L", (w * ss, h * ss), 0)
    from PIL import ImageDraw

    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=radius * ss, fill=255)
    mask = big.resize((w, h), Image.Resampling.LANCZOS)
    mpath = render_dir / f"{name}_mask.png"
    mask.save(mpath)
    spath = None
    if shadow:
        pad = 40
        sh = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
        ImageDraw.Draw(sh).rounded_rectangle((pad, pad, pad + w - 1, pad + h - 1), radius=radius, fill=115)
        sh = sh.filter(ImageFilter.GaussianBlur(18))
        rgba = Image.merge("RGBA", (Image.new("L", sh.size, 0),) * 3 + (sh,))
        spath = render_dir / f"{name}_shadow.png"
        rgba.save(spath)
    return mpath.name, (spath.name if spath else None)


def _render_still_push(img_path: str, out_path: Path, w: int, h: int, frames: int, fps: Fraction,
                       push: float = _STILL_PUSH) -> Path:
    """Render a still as a clip with a slow, sub-pixel-exact push (cover-fit, centred), lossless RGB."""
    img = ImageOps.exif_transpose(Image.open(img_path)).convert("RGB")
    iw, ih = img.size
    aspect = w / h
    if iw / ih > aspect:
        bw, bh = ih * aspect, float(ih)
    else:
        bw, bh = float(iw), iw / aspect
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s",
           f"{w}x{h}", "-framerate", f"{fps.numerator}/{fps.denominator}", "-i", "pipe:0", "-c:v", "ffv1",
           "-level", "3", "-pix_fmt", "gbrp", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for f in range(frames):
            p = f / max(1, frames - 1)
            s = push ** p  # constant perceived zoom speed
            cw, ch = bw / s, bh / s
            x0, y0 = (iw - cw) / 2, (ih - ch) / 2
            fr = img.resize((w, h), Image.Resampling.LANCZOS, box=(x0, y0, x0 + cw, y0 + ch))
            proc.stdin.write(fr.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise RenderError(f"still push render failed: {err[-800:]}")
    return out_path


def _to709(probe: VideoProbe | None, is_image: bool) -> str:
    """Filter snippet converting an insert source to BT.709 limited-range 10-bit Y'CbCr (tagged)."""
    return _to709_raw(probe, is_image) + "," + _SETP709


def _to709_raw(probe: VideoProbe | None, is_image: bool) -> str:
    if is_image or probe is None:
        return ("scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,"
                "format=yuv444p10le")
    tr = (probe.color_transfer or "").lower()
    if tr in ("arib-std-b67", "smpte2084"):
        tin = "arib-std-b67" if tr == "arib-std-b67" else "smpte2084"
        return (f"zscale=tin={tin}:min=2020_ncl:pin=2020:rin=limited:t=linear:npl=203,format=gbrpf32le,"
                "zscale=p=709,tonemap=tonemap=hable:desat=0,"
                "zscale=t=709:m=709:r=limited:dither=error_diffusion,format=yuv444p10le")
    if probe.pix_fmt.startswith(("rgb", "bgr", "gbr", "argb", "abgr", "rgba", "bgra", "pal8", "gray")):
        return ("scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,"
                "format=yuv444p10le")
    rng = "full" if (probe.color_range == "pc" or probe.pix_fmt.startswith("yuvj")) else "limited"
    cs = (probe.color_space or "").lower()
    if cs in ("smpte170m", "bt470bg", "fcc"):
        return f"zscale=min=170m:rin={rng}:m=709:r=limited:filter=spline36,format=yuv444p10le"
    if rng == "full":
        return "zscale=rin=full:r=limited:filter=spline36,format=yuv444p10le"
    return "format=yuv444p10le"


@dataclass
class _GraphInput:
    args: list[str]


@dataclass
class _Graph:
    inputs: list[_GraphInput] = field(default_factory=list)
    chains: list[str] = field(default_factory=list)

    def add_input(self, args: list[str]) -> int:
        self.inputs.append(_GraphInput(args))
        return len(self.inputs)  # stdin is input 0


def _ease_expr(p: str, kind: str) -> str:
    if kind == "whip":
        return f"(1-pow(1-{p},3))"
    return f"(0.5-0.5*cos(PI*{p}))"


def _layer_rect(ins: TimelineInsert, W: int, H: int) -> tuple[int, int, int, int]:
    """Pixel rect ``(X, Y, RW, RH)`` of an insert layer (even-aligned, inside the frame)."""
    if ins.mode in ("full", "card") or ins.rect is None:
        return (0, 0, W, H)
    x, y, w, h = ins.rect
    X, Y = _even(x * W), _even(y * H)
    RW, RH = _even(w * W), _even(h * H)
    return (X, Y, max(2, min(RW, W - X)), max(2, min(RH, H - Y)))


def _conform_inserts(job: Job | None, timeline: Timeline, W: int, H: int, paths: dict[str, str],
                     log: Any) -> set[str]:
    """Replace usable insert sources with clips from :func:`studio.broll.conform.conform_asset` (exact layer
    size, timeline cadence, grade-matched to the pre-grade mezzanine, subject-aware crop, still pushes).
    Returns the IDs now pointing at conformed clips (which start at the use range). Failures keep the
    raw source and the in-graph fallback conform."""
    done: set[str] = set()
    if job is None or not paths:
        return done
    try:
        from studio.broll import conform as conform_mod
    except ImportError:  # pragma: no cover
        return done
    fps = timeline.fps
    for ins in timeline.inserts:
        src = paths.get(ins.insert_id)
        if src is None or not isinstance(ins.asset, AssetRef):
            continue
        S = round_fraction(ins.out_start * fps)
        E = round_fraction(ins.out_end * fps)
        if E <= S:
            continue
        _X, _Y, RW, RH = _layer_rect(ins, W, H)
        extra: dict[str, Any] = {}
        if ins.mode in ("split_top", "split_bottom") and ins.rect is not None:
            extra["split_ratio"] = ins.rect[3]
        try:
            out = conform_mod.conform_asset(job, ins.asset.model_copy(update={"path": src}), fps=fps, width=RW,
                                            height=RH, duration_s=Fraction(E - S) / fps, mode=ins.mode, **extra)
        except NotImplementedError:
            return done
        except Exception as e:  # a failed conform falls back to the in-graph conform
            log(f"insert {ins.insert_id}: conform failed ({type(e).__name__}: {e}); in-graph conform used")
            continue
        if out is not None and Path(out).exists():
            paths[ins.insert_id] = str(out)
            done.add(ins.insert_id)
    return done


def _build_insert_layers(g: _Graph, timeline: Timeline, W: int, H: int, render_dir: Path, paths: dict[str, str],
                         look_lut: str | None, base: str, preview: bool, log: Any, *,
                         conformed: set[str] | None = None, grade_lut: str | None = None) -> str:
    """Composite every drawable insert onto ``base``. Conformed clips (grade-matched to the pre-grade
    A-roll) get the same full grade as the A-roll (screenshots excepted); raw fallback footage gets only
    the look; screenshots, stills-as-UI and cards are never graded."""
    fps = timeline.fps
    tb = f"{fps.denominator}/{fps.numerator}"
    fps_s = f"{fps.numerator}/{fps.denominator}"
    conformed = conformed or set()
    cur = base
    for n, ins in enumerate(sorted(timeline.inserts, key=lambda i: (i.z, i.out_start))):
        path = paths.get(ins.insert_id)
        if path is None:
            continue
        S = round_fraction(ins.out_start * fps)
        E = round_fraction(ins.out_end * fps)
        D = E - S
        if D <= 0:
            continue
        X, Y, RW, RH = _layer_rect(ins, W, H)
        asset = ins.asset if isinstance(ins.asset, AssetRef) else None
        kind = asset.kind if asset is not None else "video"
        is_conformed = ins.insert_id in conformed
        is_image = not is_conformed and (
            kind in _IMAGE_KINDS or Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".heic"))
        probe = None
        if is_image and kind != "screenshot" and not preview:
            clip = render_dir / f"still_{ins.insert_id}.mkv"
            _render_still_push(path, clip, RW, RH, D, fps)
            idx = g.add_input(["-i", str(clip)])
            src_chain = f"[{idx}:v]setpts=PTS-STARTPTS"
            is_image_src = True
        elif is_image:
            idx = g.add_input(["-loop", "1", "-framerate", fps_s, "-t", f"{float(D / fps) + 1:.6f}", "-i", path])
            src_chain = f"[{idx}:v]setpts=PTS-STARTPTS"
            is_image_src = True
        else:
            try:
                probe = probe_video(path)
            except RenderError as e:
                log(f"insert {ins.insert_id}: {e}; skipped")
                continue
            loop = ["-stream_loop", "-1"] if kind == "gif" or Path(path).suffix.lower() == ".gif" else []
            idx = g.add_input([*loop, "-i", path])
            start_s = 0.0 if is_conformed else ins.asset_in_us / 1e6  # conformed clips start at the use range
            trim = f"trim=start={start_s:.6f}," if start_s > 0 else ""
            src_chain = f"[{idx}:v]{trim}setpts=PTS-STARTPTS"
            is_image_src = probe.pix_fmt.startswith(("rgb", "bgr", "gbr", "pal8"))
        tin = _transition_frames(ins.transition_in, fps)
        tout = _transition_frames(ins.transition_out, fps)
        kin = ins.transition_in.kind if tin else "cut"
        kout = ins.transition_out.kind if tout else "cut"
        parts = [src_chain, f"fps={fps_s}", _to709(probe, is_image_src),
                 f"scale={RW}:{RH}:force_original_aspect_ratio=increase:flags=lanczos+accurate_rnd+full_chroma_int",
                 f"crop={RW}:{RH}", "setsar=1"]
        lut = None
        if is_conformed and kind != "screenshot":
            lut = grade_lut or look_lut
        elif kind in _FOOTAGE_KINDS:
            lut = look_lut
        if lut:
            parts += [_GRADE_IN, f"lut3d=file={lut}:interp=tetrahedral", _GRADE_OUT.format(fmt="yuv444p10le")]
        parts += [f"tpad=stop_mode=clone:stop={D}", f"trim=end_frame={D}"]
        if kin == "zoom" or kout == "zoom":
            zi = f"if(lt(n,{tin}),0.06*(1-{_ease_expr(f'(n/{max(tin, 1)})', 'ease')}),0)" if kin == "zoom" else "0"
            zo = (f"if(gte(n,{D - tout}),0.06*{_ease_expr(f'((n-{D - tout})/{max(tout, 1)})', 'ease')},0)"
                  if kout == "zoom" else "0")
            z = f"(1+{zi}+{zo})"
            parts += [f"scale=w='2*trunc({RW}*{z}/2)':h='2*trunc({RH}*{z}/2)':eval=frame:flags=bicubic",
                      f"crop={RW}:{RH}:(iw-{RW})/2:(ih-{RH})/2"]
        if kin == "whip":
            parts.append(f"gblur=sigma=30:sigmaV=0.01:enable='lt(n,{tin})'")
        if kout == "whip":
            parts.append(f"gblur=sigma=30:sigmaV=0.01:enable='gte(n,{D - tout})'")
        parts += ["format=yuva444p10le", f"settb={tb}", f"setpts=N+{S}"]
        layer = f"ins{n}"
        chain = ",".join(parts)
        if ins.mode == "pip":
            radius = max(8, round(min(RW, RH) * 0.06))
            mname, sname = _pip_assets(render_dir, f"pip_{ins.insert_id}", RW, RH, radius, shadow=not preview)
            midx = g.add_input(["-loop", "1", "-framerate", fps_s, "-t", f"{float(D / fps) + 1:.6f}", "-i", mname])
            g.chains.append(f"{chain}[{layer}c]")
            g.chains.append(f"[{midx}:v]format=gray10le,trim=end_frame={D},settb={tb},setpts=N+{S}[{layer}m]")
            chain = f"[{layer}c][{layer}m]alphamerge"
            if sname:
                sidx = g.add_input(["-loop", "1", "-framerate", fps_s, "-t", f"{float(D / fps) + 1:.6f}", "-i",
                                    sname])
                sh = f"{layer}s"
                sparts = [f"[{sidx}:v]format=yuva444p10le", f"trim=end_frame={D}", f"settb={tb}", f"setpts=N+{S}"]
                if tin and kin in ("fade", "dissolve", "zoom", "slide", "whip"):
                    sparts.append(f"fade=t=in:s=0:n={tin}:alpha=1")
                if tout and kout in ("fade", "dissolve", "zoom", "slide", "whip"):
                    sparts.append(f"fade=t=out:s={D - tout}:n={tout}:alpha=1")
                g.chains.append(",".join(sparts) + f"[{sh}]")
                nxt = f"base{n}s"
                g.chains.append(f"[{cur}][{sh}]overlay=x={X - 40 + 6}:y={Y - 40 + 14}:format=yuv422p10:"
                                f"eof_action=pass:repeatlast=0:enable='between(n,{S},{E - 1})'[{nxt}]")
                cur = nxt
        fades = []
        if tin and kin in ("fade", "dissolve", "zoom"):
            fades.append(f"fade=t=in:s=0:n={tin}:alpha=1")
        if tout and kout in ("fade", "dissolve", "zoom"):
            fades.append(f"fade=t=out:s={D - tout}:n={tout}:alpha=1")
        g.chains.append(chain + ("," + ",".join(fades) if fades else "") + f"[{layer}]")
        # position (slides/whips animate x)
        xin = f"{RW}*(1-{_ease_expr(f'min(max((n-{S})/{tin},0),1)', kin)})" if kin in ("slide", "whip") else "0"
        xout = (f"-{RW}*{_ease_expr(f'min(max((n-{E - tout})/{tout},0),1)', kout)}"
                if kout in ("slide", "whip") else "0")
        xexpr = f"{X}+{xin}+({xout})" if (xin != "0" or xout != "0") else str(X)
        nxt = f"base{n}"
        g.chains.append(f"[{cur}][{layer}]overlay=x='{xexpr}':y={Y}:format=yuv422p10:eof_action=pass:"
                        f"repeatlast=0:enable='between(n,{S},{E - 1})'[{nxt}]")
        cur = nxt
    return cur


# ============================================================================================ render
def _log_writer(job: Job | None, name: str) -> Any:
    def log(msg: str) -> None:
        if job is not None:
            with contextlib.suppress(Exception):
                job.trace("render_note", stage=name, note=msg)
    return log


def render_aroll(job: Job, timeline: Timeline, out_dir: str | os.PathLike[str], *, preview: bool = False,
                 color: ColorSpec | None = None, source: str | os.PathLike[str] | None = None,
                 threads: int | None = None, conform: bool | None = None, codec: str = "prores_hq") -> Path:
    """Render ``aroll.mov`` (ProRes 422 HQ at ``timeline.width x height``, or with ``codec="lean"`` the disk-budget
    HEVC/x264 4:2:2 10-bit intermediate of :mod:`studio.storage`; preview: ``aroll_preview.mp4``, 540x960 H.264)
    into ``out_dir`` and return its path.

    ``out_dir`` may also be the output file path. ``color`` is the document's ColorSpec (the timeline
    does not carry it). ``source`` overrides the mezzanine (default ``timeline.source_path`` or
    ``job.mezz_path``). ``conform`` routes b-roll through :mod:`studio.broll.conform` (default: finals
    yes, previews no — previews use the fast in-graph conform)."""
    t_start = time.monotonic()
    if isinstance(job, Timeline) and not isinstance(timeline, Timeline):  # render_aroll(timeline, job, …)
        job, timeline = timeline, job  # type: ignore[assignment]
    out = Path(out_dir)
    if out.suffix.lower() in (".mov", ".mp4", ".mkv"):
        render_dir = out.parent
    else:
        render_dir = out
        out = render_dir / ("aroll_preview.mp4" if preview else "aroll.mov")
    render_dir.mkdir(parents=True, exist_ok=True)
    src_path = Path(source) if source is not None else (
        Path(timeline.source_path) if timeline.source_path else (job.mezz_path if job is not None else None))
    if src_path is None or not Path(src_path).exists():
        raise RenderError(f"mezzanine not found: {src_path}")
    probe = probe_video(src_path)
    fps = timeline.fps
    N = timeline.frame_count
    if N <= 0:
        raise RenderError("empty timeline (no frames)")
    if preview:
        W, H = max(2, timeline.width // 4 * 2), max(2, timeline.height // 4 * 2)
    else:
        W, H = timeline.width, timeline.height
    fmt, convert = _decode_format(probe)
    native_w, native_h = probe.width, probe.height
    if probe.rotation in (90, 270):  # the mezzanine is upright by contract; tolerate a tagged file
        native_w, native_h = native_h, native_w
        convert, fmt = True, _FMTS["yuv444p16le"]
    decode_size = None
    if preview and native_h > H * 1.8 * 1.05:  # previews: decode just big enough for a 1.8x punch
        f = (H * 1.8) / native_h
        decode_size = (max(2, int(native_w * f) // 2 * 2), max(2, int(native_h * f) // 2 * 2))
    src_w, src_h = decode_size or (native_w, native_h)
    siting = _siting(fmt, probe.chroma_location if not convert else "center")
    log_path = job.log_path("render_aroll") if job is not None else render_dir / "render_aroll.log"
    note = _log_writer(job, "render_aroll")

    # ---- inserts and colour
    paths: dict[str, str] = {}
    drawn: set[str] = set()
    for ins in timeline.inserts:
        if not isinstance(ins.asset, AssetRef):
            drawn.add(ins.insert_id)  # designed cards are drawn by the overlay renderer
            continue
        p = _usable_path(ins, job)
        if p is None:
            note(f"insert {ins.insert_id}: no local asset file; skipped")
            continue
        paths[ins.insert_id] = p
        drawn.add(ins.insert_id)
    conformed = _conform_inserts(job, timeline, W, H, paths, note) if (conform if conform is not None
                                                                         else not preview) else set()
    covered = _covered_frames(timeline, paths)
    grade_lut = build_grade_lut(color, job=job)
    look_lut = build_grade_lut(color, look_only=True, job=job)
    if grade_lut is not None:
        write_cube(render_dir / "grade.cube", grade_lut)
    if look_lut is not None:
        write_cube(render_dir / "look.cube", look_lut)

    # ---- frame plan
    src_frames = probe.frame_count or None
    fmap = source_frame_map(timeline, probe.fps, src_frames)
    if len(fmap) != N:
        raise RenderError(f"frame plan has {len(fmap)} frames, timeline {N}")
    needs_by_piece: dict[int, set[int]] = {}
    for f, (si, k) in enumerate(fmap):
        if f not in covered:
            needs_by_piece.setdefault(si, set()).add(k)
    needs = [(si, sorted(ks)) for si, ks in sorted(needs_by_piece.items())]

    # ---- encoder
    g = _Graph()
    base = ("[0:v]setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv,"
            f"settb={fps.denominator}/{fps.numerator},setpts=N")
    if grade_lut is not None:
        base += f",{_GRADE_IN},lut3d=file=grade.cube:interp=tetrahedral,{_GRADE_OUT.format(fmt=OUTPUT_PIX_FMT)}"
    g.chains.append(base + "[base]")
    last = _build_insert_layers(g, timeline, W, H, render_dir, paths, "look.cube" if look_lut is not None else None,
                                "base", preview, note, conformed=conformed,
                                grade_lut="grade.cube" if grade_lut is not None else None)
    final_fmt = "yuv420p" if preview else OUTPUT_PIX_FMT
    g.chains.append(f"[{last}]format={final_fmt},setparams=color_primaries=bt709:color_trc=bt709:"
                    f"colorspace=bt709:range=tv[vout]")
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-y", "-f", "rawvideo", "-pix_fmt", OUTPUT_PIX_FMT,
           "-s", f"{W}x{H}", "-framerate", f"{fps.numerator}/{fps.denominator}", "-i", "pipe:0"]
    for inp in g.inputs:
        cmd += inp.args
    cmd += ["-filter_complex", ";".join(g.chains), "-map", "[vout]", "-an", "-fps_mode", "passthrough"]
    if preview:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
                "-movflags", "+faststart+negative_cts_offsets", "-use_editlist", "0"]
    elif codec == "lean":
        from studio.storage import lean_video_args

        cmd += lean_video_args()
    else:
        cmd += ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", OUTPUT_PIX_FMT]
    cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
            str(out)]
    with open(log_path, "ab") as lf:
        lf.write(("\n$ " + " ".join(cmd) + "\n").encode())
    lf = open(log_path, "ab")  # noqa: SIM115 - kept open for the encoder's lifetime
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=lf, cwd=str(render_dir))
    assert enc.stdin is not None

    renderer = _FrameRenderer(W, H, fmt, src_w, src_h, siting)
    decoder = _Decoder(str(src_path), fmt, convert, native_w, native_h, probe.fps, needs, log_path, decode_size)
    workers = threads or max(2, min(12, (os.cpu_count() or 4)))
    pool = ThreadPoolExecutor(max_workers=workers)
    pending: deque[Future[bytes] | bytes] = deque()
    frames_cache: dict[tuple[int, int], list[np.ndarray]] = {}
    error: BaseException | None = None
    written = 0

    def pull(si: int, k: int) -> list[np.ndarray]:
        key = (si, k)
        while key not in frames_cache:
            psi, pk, planes = decoder.next()
            # drop frames of earlier pieces/indices that are no longer needed
            for old in [x for x in frames_cache if x[0] < psi or (x[0] == psi and x[1] < pk - 1)]:
                del frames_cache[old]
            frames_cache[(psi, pk)] = planes
        return frames_cache[key]

    def drain(limit: int) -> None:
        nonlocal written
        while len(pending) > limit:
            item = pending.popleft()
            data = item if isinstance(item, bytes) else item.result()
            enc.stdin.write(data)  # type: ignore[union-attr]
            written += 1

    try:
        last_key: tuple[Any, ...] | None = None
        last_item: Future[bytes] | bytes | None = None
        for f in range(N):
            if f in covered:
                pending.append(renderer.black)
                drain(workers * 2)
                continue
            si, k = fmap[f]
            seg = timeline.segments[si]
            t = Fraction(f) / fps
            rects = [_speaker_rect(timeline, W, H, f, drawn)]
            s, cx, cy = framing_state(seg.framing, t)
            windows = []
            for (rx, ry, rw, rh) in rects:
                win = crop_window(s, cx, cy, src_w, src_h, rw, rh)
                windows.append((win, (rx, ry, rw, rh)))
            key = (si, k, tuple((tuple(round(v, 4) for v in w), r) for w, r in windows))
            if key == last_key and last_item is not None:
                pending.append(last_item)
            else:
                planes = pull(si, k)
                item: Future[bytes] = pool.submit(renderer.render, _Job(planes, windows))
                pending.append(item)
                last_key, last_item = key, item
            drain(workers * 2)
        drain(0)
    except BaseException as e:  # incl. BrokenPipeError when the encoder dies
        error = e
    finally:
        decoder.stop()
        pool.shutdown(wait=True, cancel_futures=True)
        with contextlib.suppress(BrokenPipeError):
            enc.stdin.close()
        rc = enc.wait()
        lf.close()
    if error is not None or rc != 0:
        out.unlink(missing_ok=True)  # never leave a truncated intermediate behind
        tail = log_path.read_text(errors="replace")[-3000:] if log_path.exists() else ""
        if error is not None and not isinstance(error, BrokenPipeError):
            raise RenderError(f"A-roll render failed after {written}/{N} frames: {error}\n{tail}") from error
        raise RenderError(f"ffmpeg A-roll encode failed (rc={rc}) after {written}/{N} frames:\n{tail}")
    got = _count_frames(out)
    if got != N:
        raise RenderError(f"A-roll has {got} frames, timeline expects {N}")
    if job is not None:
        job.trace("render", stage="aroll", frames=N, width=W, height=H, preview=preview,
                  seconds=round(time.monotonic() - t_start, 2), inserts=len(paths), conformed=len(conformed),
                  graded=grade_lut is not None, path=str(out))
    return out


def _count_frames(path: Path) -> int:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return int(r.stdout.strip().split(",")[0])
    except ValueError:
        return -1


def segment_for_frame(timeline: Timeline, f: int) -> TimelineSegment | None:
    """The timeline segment showing output frame ``f``."""
    return timeline.segment_at(Fraction(f) / timeline.fps)

