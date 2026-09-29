"""Compiler: CutDocument + TakeIndex → :class:`~studio.compile.models.Timeline` (ARCHITECTURE §6).

The compiler is the only place where word/gap IDs become times. Everything is exact rational math
(:class:`fractions.Fraction` seconds, integer µs from the Take Index); the output frame grid is applied
exactly once, here, and every renderer (A-roll, overlays, audio, QA) reads the same instants.

Cut edges
---------
Each story segment plays the inclusive word range ``from_word..to_word`` of one source.

* **True cuts** (the next output segment does not continue the source) get pads: the segment starts
  ``lead_pad_ms`` (default 60 ms, doctrine 40–80) before its first word and ends ``tail_pad_ms`` (default
  120 ms, doctrine 80–150) after its last word, keeping fricative tails and plosive releases. A pad never
  crosses the snap point (RMS minimum) of the neighbouring gap, so audio from the other side of the
  silence (a removed word, the next inhale) never leaks in; where words abut, the midpoint is the bound.
  The very first segment gets a longer lead-in (``head_lead_ms`` 200: first word at 0.1–0.5 s) and the
  last a longer tail (``end_tail_ms`` 300: last word → last frame 0.15–0.5 s); as no other segment shares
  those silences, the head may reach back past the snap point to the removed previous word's padded
  tail (and the ending forward to the removed next word's lead pad, never into an inhale), so a snap
  point that hugs the first onset does not force a hard start. When the gap before a
  segment holds a breath, the lead extends back to the snap point so an inhale is never clipped
  mid-way. Edges are then snapped to the **source frame grid** (the mezzanine is CFR at the output
  rate), preferring positions inside the silence and never cutting into a kept word; at speed 1.0 this
  makes picture and audio edits sample- and frame-identical (zero A/V offset at every seam).
* **Continuous joins** (two output-adjacent segments that are contiguous in the source, e.g. a
  ``set_framing`` word-range split or a speed change mid-phrase) are not cuts: audio runs straight
  through (``audio_src_out_us`` of the left piece equals ``audio_src_in_us`` of the right) and only the
  picture treatment changes, one frame (``punch_lead_ms``) before the onset of the first word of the right
  segment (doctrine: a punch lands 0–70 ms before the stressed onset). Consumers detect a continuous join
  by ``left.audio_src_out_us == right.audio_src_in_us`` and must not crossfade it.
* **Shortened pauses** (``gap_overrides``) remove the middle of the gap around its snap point, leaving
  padding after the previous word and before the next (so the next inhale stays); the segment is emitted
  as several :class:`TimelineSegment` pieces that share the ``seg_id``. Each removal is a real seam.
* **J/L cuts** keep the audio edit at its padded, snap-bounded position and move the *picture* edit
  (doctrine: "audio seam at the acoustic minimum; the picture cut may sit anywhere in the shared silent
  gap"). A J cut of ``lead_ms`` delays the picture cut: the outgoing picture holds ``lead_ms`` longer
  (``src_out_us > audio_src_out_us``) while the incoming voice starts; the incoming picture starts later
  than its audio (``audio_src_in_us < src_in_us``). An L cut is the mirror image. Leads are whole frames,
  so audio seams stay on the grid, and they are clamped so a picture extension never shows a removed
  word being spoken. Audio windows of adjacent pieces always abut in output time: the audio seam is at
  ``out_start - audio_lead_us/speed``.
* **Speed** maps output time: a piece of ``n`` output frames covers ``n * speed / fps`` seconds of source
  (the out point is derived from the frame count, so durations are exact and cannot drift).
* **Breaths.** A shortened pause that holds a breath keeps the inhale whole: the removal ends at the snap
  point (which the gap detector places before a pre-onset inhale) instead of straddling it, and only runs
  past it when the pause is too short to shorten otherwise. J/L picture extensions stop
  ``jl_picture_margin_ms`` short of a removed neighbouring word, so the mouth is never seen shaping it.

Framing
-------
Framing keys are emitted in output time with the crop centre normalized to the upright source frame
(see :func:`framing_state` / :func:`crop_window`, which renderers and QA share). Choices follow
``skills/editing/framing-and-zooms.md``:

* the crop is **locked** per segment on the median face position (1€-smoothed track from the index) and
  only re-centres when the face leaves a ±10 % dead zone of the crop width for more than 0.7 s, with an
  eased 0.5 s move; across a cut the previous anchor is kept when the new one is inside the dead zone,
  so takes do not "bump";
* punch-ins scale **around the face** (fixation-preserving): the face anchor keeps its on-screen position
  at every scale, so the eyes do not jump across a punch; every scale is clamped to the source's lossless
  ceiling x ``max_face_upsample`` (1.25x, the doctrine's hard limit: a soft face reads as cheap), so a
  1080p source never punches past 1.25x and an already-upsampled source (720p, a 1080p landscape reframe)
  not at all; clamps are logged to the job trace;
* a slow push is never combined with a re-centring move (doctrine): the anchor is locked for the segment;
* ``ease="cut"`` switches at the anchor word's onset minus one frame, ``"smooth"`` eases over ``ease_ms``
  to reach the scale at that onset, ``"push"`` eases 1.0 → scale across the segment. A ``"punch"`` seam
  without explicit framing toggles between base and ``seam_punch_scale`` (1.3×, bounded by the source's
  face-safe maximum but at least the 1.25× that hides a pose jump);
* landscape (or any non-9:16) sources get a face-tracked 9:16 reframe by default; split-screen inserts
  re-frame the speaker into the remaining region. Wherever the crop has vertical freedom (split regions,
  sources taller than 9:16) the face is placed by its **eye line**, derived from the landmark box and the
  segment's median face size, not by the box centre: a fixed centre line puts a big selfie face's eyes
  against the split line and a small face's eyes too low. Priorities: eyes inside the platform's UI-safe
  band (doctrine: under a top split, eyes above y≈1248), at most the upper forehead past the region's
  top edge, chin inside the region, then eyes about a third down the region.

Everything else (inserts, captions, text overlays, SFX, music) is anchored to word IDs and mapped through
``word_map``; overlay spans are snapped to the frame grid, SFX stay sample-exact.
"""

from __future__ import annotations

import contextlib
import math
import shutil
import statistics
import subprocess
from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from studio.compile.models import (
    FramingKey,
    Timeline,
    TimelineCaptionPage,
    TimelineCaptionWord,
    TimelineInsert,
    TimelineMusic,
    TimelineSegment,
    TimelineSfx,
    TimelineText,
    WordSpan,
)
from studio.doc.model import AssetRef, CaptionStyle, Framing, Point, SeamTreatment, Segment
from studio.timebase import normalize_fps, round_fraction, to_fraction

if TYPE_CHECKING:  # pragma: no cover
    from studio.doc.model import CaptionPlan, CutDocument, Insert
    from studio.jobs import Job
    from studio.media.models import MediaInfo
    from studio.perception.index import Gap, TakeIndex, Word

__all__ = [
    "CompileOptions", "CompileError", "compile", "compile_timeline",
    "framing_state", "crop_window", "base_window", "region_for_time", "map_source_point",
    "audio_seams", "piece_is_continuous", "ease_value", "SAFE_ZONES", "detect_active_area",
    "lossless_ceiling", "max_face_scale",
]

US = 1_000_000
_ZERO = Fraction(0)
#: MediaPipe's landmark face box runs from the upper forehead to the chin; the eye line sits ~27 % of
#: the box height below its top (measured on the d030 take: box 0.398–0.756, eyes at 0.49 of the frame)
_EYE_FROM_TOP = 0.27

#: Per-platform UI bands (fractions of the 1080x1920 frame) — Meta's official Reels ad guidance (14 % top,
#: 35 % bottom, 6 % sides) is the strictest published figure and is used as the master band.
SAFE_ZONES: dict[str, dict[str, float]] = {
    "reels": {"top": 0.14, "bottom": 0.35, "left": 0.06, "right": 0.06},
    "tiktok": {"top": 0.14, "bottom": 0.35, "left": 0.06, "right": 0.11},
    "shorts": {"top": 0.15, "bottom": 0.35, "left": 0.05, "right": 0.10},
    "master": {"top": 0.15, "bottom": 0.35, "left": 0.06, "right": 0.11},
}


class CompileError(ValueError):
    """The document cannot be compiled against this index (unknown IDs, reversed ranges …)."""


@dataclass(frozen=True)
class CompileOptions:
    """Tunable priors for the compiler (craft numbers from ``skills/editing``; all configurable)."""

    lead_pad_ms: float = 60.0  # before the first word at a cut (doctrine 40–80 ms)
    tail_pad_ms: float = 120.0  # after the last word at a cut (doctrine 80–150 ms)
    head_lead_ms: float = 200.0  # before the very first word of the video (first word at 0.1–0.5 s)
    end_tail_ms: float = 300.0  # after the very last word (0.15–0.5 s, target ~0.3 s)
    end_hold_ms: float = 200.0  # when the recording stops on the last word: hold its last frame this long after it
    breath_lead_max_ms: float = 400.0  # keep a whole inhale before an incoming phrase, up to this
    keep_breath_before: bool = True
    #: margin around a measured breath span (``Gap.breaths_us``): edits and removals stay this far outside
    #: an inhale, so it is kept or removed whole, never cut (doctrine: "never cut mid-breath")
    breath_margin_ms: float = 20.0
    #: the first frame of the video and the face frame a full-screen/card insert returns to avoid a measured
    #: blink, moving at most this many frames within the existing bounds (doctrine: "come back on a live
    #: face (eyes open, not mid-blink)"; "a blink on frame 0 ships a bad cover")
    blink_guard_frames: int = 4
    steady_head: bool = True  # frame 0 avoids a soft/moving picture (the camera settling) inside the lead-in window
    punch_lead_ms: float = 33.0  # picture change before the onset at a continuous (framing) join
    seam_punch_scale: float = 1.3  # default punch for a "punch" seam without explicit framing
    min_seam_punch_scale: float = 1.25  # a punch below this does not hide a pose jump
    #: hard limit on upsampling the speaker's face (doctrine: ~1.1x default, 1.2x on clean footage, 1.25x
    #: hard): any punch is clamped to ``lossless ceiling x max_face_upsample`` (never below 1.0)
    max_face_upsample: float = 1.25
    #: a J/L picture extension stops this far from the neighbouring (removed) word: the mouth shapes a word
    #: before it is heard and closes after it
    jl_picture_margin_ms: float = 80.0
    #: a J/L picture edit may move this many frames (and at most this share of the lead) from the Director's
    #: lead onto a blink or a closed-mouth frame (doctrine: "pick a blink"); 0 disables the snap
    jl_snap_frames: int = 3
    jl_snap_frac: float = 0.5
    face_dead_zone: float = 0.10  # fraction of the crop size the face may wander before re-centring
    face_dead_time_ms: float = 700.0
    face_move_ms: float = 500.0  # eased re-centre duration (doctrine ≥ 400 ms)
    # Vertical placement when the crop has vertical freedom (split-screen regions, sources taller than
    # 9:16) is by the **eye line**, not the face-box centre, so a big selfie face and a small wide-shot face
    # both read right (see :meth:`_Framer._face_line`). Fractions of the speaker region's height:
    eye_line: float = 0.36  # full frame: eyes ~1/3 down (doctrine example: eyes at y≈635 of 1920)
    split_eye_line_below: float = 0.36  # speaker region under top content
    split_eye_line_above: float = 0.42  # speaker region above bottom content (under the top UI band)
    #: eyes stay this far inside the platform's top/bottom UI bands (fractions of the frame); doctrine:
    #: under a top split the eyes stay above y≈1248 (0.65) — 0.03 below that is the ceiling used
    eye_band_margin: float = 0.03
    forehead_cut_max: float = 0.10  # share of the face box (upper forehead) allowed above the region top
    chin_margin: float = 0.01  # chin kept this far (of the frame) inside the region's bottom edge
    insert_min_ms: float = 400.0
    insert_return_max_ms: float = 400.0  # an insert may hold into the following pause up to this
    insert_return_margin_frames: int = 1  # … returning this many frames before the next word
    caption_end_hold_ms: float = 400.0  # doctrine 0.3–0.5 s after the last word of a run
    caption_close_gap_ms: float = 500.0  # pages closer than this are held until the next page
    caption_page_gap_frames: int = 2  # blank frames between consecutive pages
    caption_chin_margin_px: float = 80.0  # caption top edge below the chin (40–120 px prior)
    text_min_read: bool = True  # hold text overlays for 0.3 s/word + margin
    text_read_s_per_word: float = 0.3
    text_margin_s: float = 0.5
    hook_title_margin_s: float = 1.0
    caption_builder: Literal["captions_module", "builtin"] = "captions_module"
    pip_width: float = 0.40  # of frame width
    pip_max_height: float = 0.28  # of frame height
    platform: str = "master"
    #: black bars (pillar/letterbox) in the source: ``"auto"`` detects them on the job's mezzanine, a
    #: normalized ``(x, y, w, h)`` gives the active picture, None disables the guard
    active_area: tuple[float, float, float, float] | Literal["auto"] | None = "auto"
    max_bar_crop_scale: float = 1.25  # crop bars away only if that costs at most this much extra zoom


# ============================================================================================ geometry
def base_window(src_w: int, src_h: int, region_aspect: float) -> tuple[float, float]:
    """Largest window of pixel aspect ``region_aspect`` (w/h) inside the source, as normalized (w, h)."""
    src_aspect = src_w / src_h
    if src_aspect > region_aspect:
        return (region_aspect * src_h / src_w, 1.0)
    return (1.0, src_w / (region_aspect * src_h))


def crop_window(scale: float, cx: float, cy: float, src_w: int, src_h: int, region_w: float,
                region_h: float) -> tuple[float, float, float, float]:
    """Crop rectangle ``(x0, y0, w, h)`` in **source pixels** (floats) for a framing state rendered into a
    region of ``region_w x region_h`` output pixels. The window is the base fill for the region's aspect
    divided by ``scale``, centred at ``(cx, cy)`` (normalized), shifted (never shrunk) to stay inside."""
    bw, bh = base_window(src_w, src_h, region_w / region_h)
    s = max(1.0, float(scale))
    w, h = bw * src_w / s, bh * src_h / s
    x0 = min(max(cx * src_w - w / 2.0, 0.0), src_w - w)
    y0 = min(max(cy * src_h - h / 2.0, 0.0), src_h - h)
    return (x0, y0, w, h)


_ACTIVE_CACHE: dict[tuple[str, int, int], tuple[float, float, float, float] | None] = {}


def detect_active_area(path: str | Path, *, samples: int = 12, black: int = 30,
                       min_bar: float = 0.01) -> tuple[float, float, float, float] | None:
    """Active picture ``(x, y, w, h)`` (normalized) of a source with constant black bars (a portrait video
    pillarboxed into 16:9, a letterboxed clip), or None when there are none. Samples ``samples`` frames
    across the file at quarter resolution; a bar is an edge band whose rows/columns stay black (8-bit
    ≤ ``black``) in every non-black sample and is at least ``min_bar`` of the dimension. The result is
    shrunk by one quarter-res pixel so a soft bar edge never shows. Cached per file (path, size, mtime)."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    if key in _ACTIVE_CACHE:
        return _ACTIVE_CACHE[key]
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    result: tuple[float, float, float, float] | None = None
    if ffmpeg and ffprobe:
        result = _detect_bars(p, ffmpeg, ffprobe, samples, black, min_bar)
    _ACTIVE_CACHE[key] = result
    return result


def _detect_bars(p: Path, ffmpeg: str, ffprobe: str, samples: int, black: int, min_bar: float
                 ) -> tuple[float, float, float, float] | None:
    import numpy as np

    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "csv=p=0:s=,", str(p)],
                       capture_output=True, text=True)
    vals = [v for v in r.stdout.replace("\n", ",").split(",") if v.strip()]
    try:
        w0, h0, dur = int(vals[0]), int(vals[1]), float(vals[2])
    except (IndexError, ValueError):
        return None
    w, h = max(8, w0 // 4 // 2 * 2), max(8, h0 // 4 // 2 * 2)
    bars: list[tuple[int, int, int, int]] = []
    for k in range(samples):
        t = dur * (k + 0.5) / samples
        out = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(p), "-frames:v", "1",
                              "-vf", f"scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
                             capture_output=True)
        if out.returncode != 0 or len(out.stdout) < w * h:
            continue
        img = np.frombuffer(out.stdout[:w * h], np.uint8).reshape(h, w)
        if img.max() <= black:  # a black frame (fade) says nothing about bars
            continue
        cols = img.max(axis=0) <= black
        rows = img.max(axis=1) <= black

        def run(mask: np.ndarray) -> int:
            n = 0
            for v in mask:
                if not v:
                    break
                n += 1
            return n

        bars.append((run(cols), run(cols[::-1]), run(rows), run(rows[::-1])))
    if len(bars) < max(2, samples // 3):
        return None
    left, right, top, bottom = (min(b[i] for b in bars) for i in range(4))
    left = left + 1 if left >= min_bar * w else 0
    right = right + 1 if right >= min_bar * w else 0
    top = top + 1 if top >= min_bar * h else 0
    bottom = bottom + 1 if bottom >= min_bar * h else 0
    if not (left or right or top or bottom) or left + right >= w - 2 or top + bottom >= h - 2:
        return None
    return (left / w, top / h, (w - left - right) / w, (h - top - bottom) / h)


def ease_value(kind: str, p: float) -> float:
    """Normalized progress for an interpolation ``kind`` ("hold" → 0 until the key, "linear", or a sine
    "ease_in_out")."""
    p = min(1.0, max(0.0, p))
    if kind == "hold":
        return 0.0 if p < 1.0 else 1.0
    if kind == "linear":
        return p
    return 0.5 - 0.5 * math.cos(math.pi * p)


def framing_state(keys: Sequence[FramingKey], out_t: Fraction | float) -> tuple[float, float, float]:
    """``(scale, cx, cy)`` at output time ``out_t``.

    Semantics: a key's ``ease`` describes how the state **arrives** at that key from the previous one
    ("hold" = hard switch at the key time). Scale is interpolated geometrically (constant perceived zoom
    speed); the centre moves in proportion to the crop width while the scale changes (which keeps a
    fixation-preserving punch exactly anchored), and with the eased time otherwise. Before the first key
    the first state holds; after the last key the last state holds."""
    if not keys:
        return (1.0, 0.5, 0.5)
    t = float(out_t)
    times = [float(k.out_t) for k in keys]
    i = bisect_right(times, t)
    if i == 0:
        k = keys[0]
        return (k.scale, k.cx, k.cy)
    if i >= len(keys):
        k = keys[-1]
        return (k.scale, k.cx, k.cy)
    a, b = keys[i - 1], keys[i]
    span = times[i] - times[i - 1]
    p = 0.0 if span <= 0 else (t - times[i - 1]) / span
    e = ease_value(b.ease, p)
    if e <= 0.0:
        return (a.scale, a.cx, a.cy)
    s = a.scale * (b.scale / a.scale) ** e
    wa, wb = 1.0 / a.scale, 1.0 / b.scale
    if abs(wb - wa) > 1e-9:
        f = (1.0 / s - wa) / (wb - wa)
    else:
        f = e
    return (s, a.cx + (b.cx - a.cx) * f, a.cy + (b.cy - a.cy) * f)


def region_for_time(timeline: Timeline, out_t: Fraction) -> tuple[float, float, float, float]:
    """Normalized output rectangle ``(x, y, w, h)`` the A-roll speaker occupies at ``out_t`` (the full
    frame, or the complement of an active split-screen insert)."""
    t = Fraction(out_t)
    for ins in timeline.inserts:
        if isinstance(ins.asset, AssetRef) and ins.asset_path is None:
            continue  # not drawn: the speaker keeps the full frame
        if ins.mode in ("split_top", "split_bottom") and ins.out_start <= t < ins.out_end and ins.rect:
            x, y, w, h = ins.rect
            if ins.mode == "split_top":
                return (0.0, y + h, 1.0, 1.0 - (y + h))
            return (0.0, 0.0, 1.0, y)
    return (0.0, 0.0, 1.0, 1.0)


def map_source_point(timeline: Timeline, out_t: Fraction, x_norm: float, y_norm: float, src_w: int,
                     src_h: int) -> tuple[float, float] | None:
    """Where a normalized source point (e.g. the chin from the face track) lands in the output frame at
    ``out_t`` after framing transforms, as normalized output coordinates; None outside the story."""
    seg = timeline.segment_at(Fraction(out_t))
    if seg is None:
        return None
    rx, ry, rw, rh = region_for_time(timeline, Fraction(out_t))
    s, cx, cy = framing_state(seg.framing, out_t)
    x0, y0, w, h = crop_window(s, cx, cy, src_w, src_h, rw * timeline.width, rh * timeline.height)
    u = (x_norm * src_w - x0) / w
    v = (y_norm * src_h - y0) / h
    return (rx + u * rw, ry + v * rh)


def piece_is_continuous(left: TimelineSegment, right: TimelineSegment) -> bool:
    """True when ``right`` continues ``left``'s source audio without a seam (no crossfade wanted)."""
    return left.source == right.source and left.audio_src_out_us == right.audio_src_in_us


def audio_seams(timeline: Timeline) -> list[Fraction]:
    """Output times of the audio edits (true cuts only; J/L-shifted), for click detection."""
    out: list[Fraction] = []
    for a, b in zip(timeline.segments, timeline.segments[1:], strict=False):
        if piece_is_continuous(a, b):
            continue
        out.append(b.out_start - Fraction(b.audio_lead_us, US) / to_fraction(b.speed))
    return out


# ============================================================================================ internals
@dataclass
class _Piece:
    seg: Segment
    seg_idx: int
    words: list[Word]
    speed: Fraction
    a_in: Fraction  # audio source (s)
    a_out: Fraction
    p_in: Fraction = _ZERO  # picture source (s)
    p_out: Fraction = _ZERO
    join_in: Literal["start", "cut", "trim", "continuous"] = "cut"
    seam_in: SeamTreatment = field(default_factory=SeamTreatment)
    out_start: Fraction = _ZERO
    out_end: Fraction = _ZERO
    framing: list[FramingKey] = field(default_factory=list)


def _us(us: int) -> Fraction:
    return Fraction(us, US)


def _to_us(t: Fraction) -> int:
    return round_fraction(t * US)


class _Grid:
    def __init__(self, fps: Fraction):
        self.fps = fps

    def floor(self, t: Fraction) -> Fraction:
        return Fraction(math.floor(t * self.fps)) / self.fps

    def ceil(self, t: Fraction) -> Fraction:
        return Fraction(math.ceil(t * self.fps)) / self.fps

    def round(self, t: Fraction) -> Fraction:
        return Fraction(round_fraction(t * self.fps)) / self.fps

    def frames(self, t: Fraction) -> int:
        return round_fraction(t * self.fps)

    @property
    def frame(self) -> Fraction:
        return 1 / self.fps

    def nearest_in(self, target: Fraction, lo: Fraction, hi: Fraction) -> Fraction | None:
        """Grid point in [lo, hi] nearest to ``target`` (None if the interval holds no grid point)."""
        a, b = self.ceil(lo), self.floor(hi)
        if a > b:
            return None
        c = self.round(min(max(target, lo), hi))
        return min(max(c, a), b)


def _media_end(index: TakeIndex) -> Fraction:
    end = _us(index.media.duration_us) if index.media.duration_us > 0 else _ZERO
    if index.words:
        end = max(end, _us(index.words[-1].end_us))
    return end


def _sound_lo(g: Gap | None, onset: Fraction) -> Fraction | None:
    """End of the last non-word sound (a fragment of a lost word, a noise) in ``g`` before ``onset``: a lead pad
    never reaches back into it."""
    if g is None:
        return None
    ends = [_us(b) for _a, b in g.sound_us if _us(b) <= onset + Fraction(1, 1000)]
    return min(max(ends), onset) if ends else None


def _sound_hi(g: Gap | None, end: Fraction) -> Fraction | None:
    """Start of the first non-word sound in ``g`` after ``end``: a tail pad never runs into it."""
    if g is None:
        return None
    starts = [_us(a) for a, _b in g.sound_us if _us(a) >= end - Fraction(1, 1000)]
    return max(min(starts), end) if starts else None


def _left_bounds(index: TakeIndex, first: Word) -> tuple[Fraction, Fraction, Gap | None]:
    """(soft_lo, hard_lo, gap) for the region before ``first``: never cross the snap point of an inner
    gap (soft) nor into the previous word (hard)."""
    prev = index.prev_word(first.id)
    g = index.gap_before(first.id)
    start = _us(first.start_us)
    if prev is None:
        lo = _us(g.start_us) if g is not None else _ZERO
        return (min(lo, start), min(lo, start), g)
    hard = min(_us(prev.end_us), start)
    if g is not None and g.after_word_id is not None:
        return (min(max(_us(g.snap_us), hard), start), hard, g)
    mid = (hard + start) / 2
    return (mid, hard, g)


def _right_bounds(index: TakeIndex, last: Word) -> tuple[Fraction, Fraction, Gap | None]:
    """(soft_hi, hard_hi, gap) after ``last`` (mirror of :func:`_left_bounds`)."""
    nxt = index.next_word(last.id)
    g = index.gap_after(last.id)
    end = _us(last.end_us)
    if nxt is None:  # last word of the recording: only the media end bounds the tail …
        hi = max(_media_end(index), end)
        if g is not None and g.kind == "noise":
            # … unless untranscribed sound follows it (the recording stopping on the onset of another syllable):
            # the out-point stays at the quietest point before that sound
            hi = max(min(_us(g.snap_us), hi), end)
        return (hi, hi, g)
    hard = max(_us(nxt.start_us), end)
    if g is not None and g.before_word_id is not None:
        return (max(min(_us(g.snap_us), hard), end), hard, g)
    mid = (end + hard) / 2
    return (mid, hard, g)


def _gap_between(index: TakeIndex, left: Word, right: Word) -> Gap | None:
    g = index.gap_after(left.id)
    if g is not None and g.before_word_id == right.id:
        return g
    return None


def _blinks(index: TakeIndex) -> list[tuple[Fraction, Fraction]]:
    """Measured blinks (source seconds), time order."""
    if index.visual is None:
        return []
    return sorted((_us(e.start_us), _us(e.end_us)) for e in index.visual.events if e.kind == "blink")


def _steady_head(index: TakeIndex, grid: _Grid, e: Fraction, lo: Fraction, hi: Fraction,
                 blinks: Sequence[tuple[Fraction, Fraction]], bspans: Sequence[tuple[Fraction, Fraction]]) -> Fraction:
    """Frame 0 is the feed's default cover and the first look: when the picture at ``e`` is unsteady (blur and
    face motion well above the take's own — a phone still settling after the record tap), move the start to the
    steadiest grid point in ``[lo, hi]`` (never into a blink or a breath). Returns ``e`` when it is fine."""
    vis = index.visual
    if vis is None or not vis.samples or hi < lo:
        return e
    smp = [q for q in vis.samples if q.face_box is not None]
    if len(smp) < 10:
        return e
    blur = [q.blur for q in smp if q.blur is not None]
    if not blur:
        return e
    med_blur = statistics.median(blur) or 1e-6
    t = [q.t_us for q in smp]
    motion = [0.0] + [math.hypot(smp[k].face_box.cx - smp[k - 1].face_box.cx,  # type: ignore[union-attr]
                                 smp[k].face_box.cy - smp[k - 1].face_box.cy)  # type: ignore[union-attr]
                      / max(1e-3, (t[k] - t[k - 1]) / 1e5) for k in range(1, len(smp))]
    med_motion = statistics.median(motion[1:]) or 1e-4

    def nearest(c: Fraction) -> int | None:
        k = bisect_right(t, _to_us(c))
        near = [j for j in (k - 1, k) if 0 <= j < len(smp)]
        return min(near, key=lambda q: abs(t[q] - _to_us(c))) if near else None

    def blur_at(c: Fraction) -> float:
        j = nearest(c)
        b = smp[j].blur if j is not None else None
        return b if b is not None else med_blur

    def unsteady(c: Fraction) -> float:
        j = nearest(c)
        if j is None:
            return 0.0
        m = max(motion[j], motion[j + 1] if j + 1 < len(motion) else 0.0)
        return blur_at(c) / med_blur + 0.5 * m / med_motion

    here = unsteady(e)
    if here <= 2.2:
        return e
    b_here = blur_at(e)
    best, best_v = e, here
    c = grid.ceil(lo)
    while c <= hi:
        # only a genuinely sharper picture counts (a held, frozen pre-roll frame is still, but not sharper)
        if (not _in_blink(blinks, c) and not any(bs < c < be for bs, be in bspans)
                and blur_at(c) <= 0.85 * b_here):
            v = unsteady(c) + 0.3 * abs(float(c - e))  # the nearest steady frame keeps the planned lead-in
            if v < best_v - 1e-9:
                best, best_v = c, v
        c += grid.frame
    return best if best_v <= 0.75 * here else e


def _in_blink(blinks: Sequence[tuple[Fraction, Fraction]], src_t: Fraction) -> bool:
    return any(a <= src_t < b for a, b in blinks)


def _breath_spans(gap: Gap | None, opts: CompileOptions) -> list[tuple[Fraction, Fraction]]:
    """The gap's measured breath spans widened by ``breath_margin_ms`` (clamped to the gap), merged, in time
    order. Empty when the index carries no measured spans."""
    if gap is None or not gap.breaths_us:
        return []
    m = Fraction(round(opts.breath_margin_ms * 1000), US)
    gs, ge = _us(gap.start_us), _us(gap.end_us)
    out: list[tuple[Fraction, Fraction]] = []
    for a, b in sorted(gap.breaths_us):
        s, e = max(gs, _us(a) - m), min(ge, _us(b) + m)
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _clear_of_breaths(t: Fraction, spans: Sequence[tuple[Fraction, Fraction]], lo: Fraction, hi: Fraction,
                      grid: _Grid, *, prefer: Literal["before", "after"]) -> Fraction:
    """Move a grid edge ``t`` out of any breath span (never cut mid-breath, doctrine voice-and-loudness):
    to the span's grid start (``prefer="before"``) or end, whichever fits ``[lo, hi]`` first; unchanged
    when neither fits (the caller's hard bounds win)."""
    for s, e in spans:
        if s < t < e:
            cands = (grid.floor(s), grid.ceil(e)) if prefer == "before" else (grid.ceil(e), grid.floor(s))
            for c in cands:
                if lo <= c <= hi:
                    return c
            return t
    return t


def _removal_around_breaths(gap: Gap, keep: Fraction, rem: Fraction, lo: Fraction, hi: Fraction,
                            breaths: list[tuple[Fraction, Fraction]], grid: _Grid
                            ) -> tuple[Fraction, Fraction] | None:
    """Removal inside ``[lo, hi]`` that never cuts through a measured breath (doctrine: "If pacing tightens a
    gap below the breath's length, remove the breath whole, cutting at its edges. Never cut mid-breath.")

    1. The removal fits in breath-free silence → remove only silence (next to the breath, so the inhale
       stays with the words it precedes).
    2. The target is shorter than the breath → remove the breath whole with the silence around it.
    3. Otherwise the breath stays whole and the pause keeps whatever silence cannot go (longer than asked).
    """
    snap = _us(gap.snap_us)
    free: list[tuple[Fraction, Fraction]] = []
    cur = lo
    for s, e in breaths:
        if s > cur:
            free.append((cur, min(s, hi)))
        cur = max(cur, e)
    if cur < hi:
        free.append((cur, hi))
    free = [(s, e) for s, e in free if e > s]

    def placed(fs: Fraction, fe: Fraction, length: Fraction) -> tuple[Fraction, Fraction] | None:
        """``length`` of removal inside ``[fs, fe]``: against the following breath, after a preceding one,
        else centred on the snap point; grid-snapped without leaving the interval."""
        if any(abs(fe - s) < grid.frame for s, _ in breaths):
            rs = fe - length
        elif any(abs(fs - e) < grid.frame for _, e in breaths):
            rs = fs
        else:
            rs = snap - length / 2
        rs = min(max(rs, fs), fe - length)
        rs_g = grid.nearest_in(rs, fs, fe - grid.frame)
        if rs_g is None:
            return None
        re_g = grid.nearest_in(rs_g + length, rs_g + grid.frame, fe)
        return (rs_g, re_g) if re_g is not None and re_g > rs_g else None

    # 1 — silence only
    fits = [f for f in free if f[1] - f[0] >= rem]
    if fits:
        best = min(fits, key=lambda f: (not (f[0] <= snap <= f[1]), -(f[1] - f[0])))
        r = placed(best[0], best[1], rem)
        if r is not None:
            return r
    # 2 — the breath goes whole (the kept pause would be shorter than the breath itself)
    breath_len = sum((_us(b) - _us(a) for a, b in gap.breaths_us), _ZERO)
    if keep < breath_len:
        for s, e in sorted(breaths, key=lambda sp: -(sp[1] - sp[0])):
            L = max(rem, e - s)
            r_lo, r_hi = max(lo, e - L), min(s, hi - L)
            if r_lo > r_hi:
                continue
            rs = min(max(s - (L - (e - s)) / 2, r_lo), r_hi)
            # grid edges: inside [lo, hi] and still enclosing the breath span
            rs_g = grid.ceil(max(rs, lo))
            if rs_g > s:
                rs_g = grid.floor(s)
            re_g = grid.floor(min(rs_g + L, hi))
            if re_g < e:
                re_g = grid.ceil(e)
            if lo <= rs_g <= s and e <= re_g <= hi and rs_g < re_g and not any(
                    (bs < rs_g < be) or (bs < re_g < be) for bs, be in breaths):
                return (rs_g, re_g)
    # 3 — the breath stays whole; remove what silence there is (longest stretch, next to the breath)
    for fs, fe in sorted(free, key=lambda f: -(f[1] - f[0])):
        r = placed(fs, fe, min(rem, fe - fs))
        if r is not None:
            return r
    return None


def _removal(gap: Gap, target_ms: int, opts: CompileOptions, grid: _Grid) -> tuple[Fraction, Fraction] | None:
    """Source interval to remove so ``gap`` plays ~``target_ms``: the middle, centred on the snap point,
    keeping padding after the previous word and before the next; snapped to the frame grid. Measured
    breaths (``Gap.breaths_us``) are kept or removed whole, never cut (:func:`_removal_around_breaths`)."""
    gs, ge, snap = _us(gap.start_us), _us(gap.end_us), _us(gap.snap_us)
    dur = ge - gs
    keep = Fraction(max(0, int(target_ms)) * 1000, US)
    if keep >= dur:
        return None
    rem = dur - keep
    tail = Fraction(round(opts.tail_pad_ms * 1000), US)
    lead = Fraction(round(opts.lead_pad_ms * 1000), US)
    share = tail / (tail + lead) if tail + lead > 0 else Fraction(1, 2)
    a = min(tail, keep * share)
    b = min(lead, keep - a)
    breaths = _breath_spans(gap, opts)
    if breaths:
        return _removal_around_breaths(gap, keep, rem, gs + a, ge - b, breaths, grid)
    if gap.has_breath or gap.kind == "breath":
        # keep the inhale whole: the snap point sits before a pre-onset breath, so end the removal there
        # (and cross it only when the pause before the snap point is too short)
        rs = snap - rem
    else:
        rs = snap - rem / 2
    rs = min(max(rs, gs + a), ge - b - rem)
    re = rs + rem
    if gap.sound_us:  # non-word sound (a fragment of a lost word, a noise) is removed whole, never kept in a pad
        lo_need = min(_us(x) for x, _y in gap.sound_us)
        hi_need = max(_us(y) for _x, y in gap.sound_us)
        rs = min(rs, lo_need)
        re = max(re, hi_need)
    rs_g = grid.nearest_in(rs, gs, ge)
    if rs_g is None:
        return None
    re_g = grid.nearest_in(re, rs_g + grid.frame, ge)
    if re_g is None or re_g <= rs_g:
        return None
    return (rs_g, re_g)


def _check_segments(doc: CutDocument, index: TakeIndex) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    seen: set[int] = set()
    for s in doc.segments:
        try:
            a, b = index.word_pos(s.from_word), index.word_pos(s.to_word)
        except KeyError as e:
            raise CompileError(f"{s.id}: {e.args[0] if e.args else e}") from None
        if a > b:
            raise CompileError(f"{s.id}: {s.from_word} comes after {s.to_word}")
        src = {w.source for w in index.words[a:b + 1]}
        if len(src) > 1:
            raise CompileError(f"{s.id}: word range spans several sources {sorted(src)}")
        for p in range(a, b + 1):
            if p in seen:
                raise CompileError(f"{s.id}: word {index.words[p].id} is used twice in the story")
            seen.add(p)
        spans.append((a, b))
    return spans


# ============================================================================================ story → pieces
def _build_pieces(doc: CutDocument, index: TakeIndex, opts: CompileOptions, grid: _Grid,
                  dense: _DenseVisual | None = None) -> list[_Piece]:
    spans = _check_segments(doc, index)
    n = len(doc.segments)
    words = index.words
    # joins between output-adjacent segments
    join: list[str] = ["start"] + ["cut"] * (n - 1)  # join into segment i
    forced: dict[int, tuple[Fraction, Fraction]] = {}  # i -> (left end, right start) for boundary trims
    join_point: dict[int, Fraction] = {}
    for i in range(1, n):
        (_, pb), (qa, _) = spans[i - 1], spans[i]
        left, right = words[pb], words[qa]
        if qa == pb + 1 and left.source == right.source:
            g = _gap_between(index, left, right)
            ov = None
            if g is not None:
                ov = doc.segments[i - 1].gap_overrides.get(g.id, doc.segments[i].gap_overrides.get(g.id))
            rem = _removal(g, ov, opts, grid) if (g is not None and ov is not None) else None
            if rem is not None:
                join[i] = "trim"
                forced[i] = rem
                continue
            join[i] = "continuous"
            onset = _us(right.start_us)
            lead = Fraction(round(opts.punch_lead_ms * 1000), US)
            j = grid.floor(onset - lead)
            lo = _us(left.start_us) + grid.frame  # keep at least a frame of the left segment's words
            if j < lo:
                j = grid.floor(onset) if grid.floor(onset) >= lo else grid.ceil(lo)
            join_point[i] = j

    pieces: list[_Piece] = []
    for i, seg in enumerate(doc.segments):
        a, b = spans[i]
        seg_words = words[a:b + 1]
        speed = to_fraction(seg.speed)
        first, last = seg_words[0], seg_words[-1]
        # ---- left edge
        if join[i] == "continuous":
            left_edge = join_point[i]
        elif join[i] == "trim":
            left_edge = forced[i][1]
        else:
            soft_lo, hard_lo, gap = _left_bounds(index, first)
            lead_ms = opts.head_lead_ms if i == 0 else opts.lead_pad_ms
            onset = _us(first.start_us)
            if i == 0 and index.prev_word(first.id) is not None:
                # the head shares this silence with no outgoing segment: the lead-in may reach back past the
                # snap point to the removed word's padded tail (its release stays out), so the first word
                # can land 0.1-0.5 s after frame 0 even when the snap point hugs the onset
                relaxed = min(hard_lo + Fraction(round(opts.tail_pad_ms * 1000), US), onset)
                soft_lo = min(soft_lo, max(relaxed, hard_lo))
            s_lo = _sound_lo(gap, onset)
            if s_lo is not None:  # a fragment of a lost word / a noise just before the word stays out
                if i == 0:  # ... except the video's lead-in (first word at >= 0.1 s): the audio stage mutes it there
                    s_lo = min(s_lo, onset - Fraction(round(opts.head_lead_ms * 1000), US))
                soft_lo = max(soft_lo, s_lo)
            target = onset - Fraction(round(lead_ms * 1000), US)
            if (opts.keep_breath_before and gap is not None and gap.after_word_id is not None
                    and (gap.has_breath or gap.kind == "breath")):
                target = min(target, max(soft_lo, onset - Fraction(round(opts.breath_lead_max_ms * 1000), US)))
            target = max(target, soft_lo)
            e = grid.nearest_in(target, soft_lo, onset)
            if e is None:
                e = grid.floor(onset)
                if e < hard_lo and grid.ceil(hard_lo) <= onset:
                    e = grid.ceil(hard_lo)
            # never start inside a measured breath: a pre-onset inhale comes in whole, anything earlier
            # (the removed word's exhale) stays out
            pre_onset = Fraction(round(opts.breath_lead_max_ms * 1000), US)
            for bs, be in _breath_spans(gap, opts):
                if bs < e < be:
                    e = _clear_of_breaths(e, [(bs, be)], max(hard_lo, _ZERO), onset, grid,
                                          prefer="before" if onset - be <= pre_onset else "after")
            if i == 0 and opts.blink_guard_frames > 0:  # frame 0 is the feed's default cover: not mid-blink
                blinks = _blinks(index)
                if _in_blink(blinks, e):
                    lo_head = max(soft_lo, _ZERO)
                    hi_head = onset - Fraction(100, 1000)  # first word stays ≥ 0.1 s after frame 0
                    bspans = _breath_spans(gap, opts)
                    for k in range(1, opts.blink_guard_frames + 1):
                        cand = [c for c in (e - k * grid.frame, e + k * grid.frame)
                                if lo_head <= c <= hi_head and not _in_blink(blinks, c)
                                and not any(bs < c < be for bs, be in bspans)]
                        if cand:
                            e = cand[0]
                            break
            if i == 0 and opts.steady_head:  # ... nor the phone still settling after the record tap (soft, moving)
                lo_head = max(soft_lo, _ZERO, onset - Fraction(1, 2))
                hi_head = onset - Fraction(100, 1000)
                e = _steady_head(index, grid, e, lo_head, hi_head, _blinks(index), _breath_spans(gap, opts))
            left_edge = max(e, _ZERO)
        # ---- right edge
        if i + 1 < n and join[i + 1] == "continuous":
            right_edge = join_point[i + 1]
        elif i + 1 < n and join[i + 1] == "trim":
            right_edge = forced[i + 1][0]
        else:
            soft_hi, hard_hi, g_after = _right_bounds(index, last)
            tail_ms = opts.end_tail_ms if i == n - 1 else opts.tail_pad_ms
            end = _us(last.end_us)
            if (i == n - 1 and index.next_word(last.id) is not None
                    and not (g_after is not None and (g_after.has_breath or g_after.kind == "breath"))):
                # likewise the ending may run on to the removed next word's lead pad (never into an inhale)
                relaxed = max(hard_hi - Fraction(round(opts.lead_pad_ms * 1000), US), end)
                soft_hi = max(soft_hi, min(relaxed, hard_hi))
            s_hi = _sound_hi(g_after, end)
            if s_hi is not None:  # untranscribed sound after the word (a fragment, a noise) stays out
                soft_hi = min(soft_hi, s_hi)
            target = min(end + Fraction(round(tail_ms * 1000), US), soft_hi)
            e = grid.nearest_in(target, end, soft_hi)
            if e is None:
                e = grid.ceil(end)
                if e > hard_hi and grid.floor(hard_hi) >= end:
                    e = grid.floor(hard_hi)
            # never end inside a measured breath: stop before it (or, if it starts right on the word, after it)
            right_edge = _clear_of_breaths(e, _breath_spans(g_after, opts), end, hard_hi, grid, prefer="before")
            if i == n - 1:  # the last frame is what a loop replays into: not caught with the mouth open
                right_edge = _closed_mouth_end(index, dense, grid, right_edge, end, min(soft_hi, hard_hi),
                                               _breath_spans(g_after, opts))
        # ---- inner pause trims → several pieces
        cuts: list[tuple[Fraction, Fraction, int]] = []  # (remove_start, remove_end, split word position)
        for g in index.gaps_between(seg.from_word, seg.to_word):
            if g.id not in seg.gap_overrides:
                continue
            rem = _removal(g, seg.gap_overrides[g.id], opts, grid)
            if rem is not None:
                cuts.append((rem[0], rem[1], index.word_pos(g.before_word_id)))  # type: ignore[arg-type]
        cuts.sort(key=lambda c: c[0])
        bounds = [left_edge]
        splits = [a]
        for rs, re, pos in cuts:
            bounds.extend([rs, re])
            splits.append(pos)
        bounds.append(right_edge)
        splits.append(b + 1)
        for k in range(len(splits) - 1):
            p_words = words[splits[k]:splits[k + 1]]
            ain, aout = bounds[2 * k], bounds[2 * k + 1]
            if k == 0:
                kind: Literal["start", "cut", "trim", "continuous"] = join[i]  # type: ignore[assignment]
                seam = seg.seam_in if kind == "cut" else SeamTreatment(kind="punch" if seg.seam_in.kind == "punch"
                                                                         else "cut")
            else:
                kind, seam = "trim", SeamTreatment()
            if aout <= ain:  # degenerate (should not happen): keep at least one frame
                aout = ain + grid.frame
            pieces.append(_Piece(seg=seg, seg_idx=i, words=list(p_words), speed=speed, a_in=ain, a_out=aout,
                                 p_in=ain, p_out=aout, join_in=kind, seam_in=seam))
        if i == n - 1 and opts.end_hold_ms > 0 and pieces and index.next_word(last.id) is None:
            # the recording stops (almost) on the last word: no tail to end on. The picture holds its last frame
            # past the source end (renderers clamp to the last source frame) while the audio ends where it must
            # (before any fragment of a next syllable) and room tone carries the hold
            end_w = _us(last.end_us)
            want = end_w + Fraction(round(opts.end_hold_ms * 1000), US)
            p = pieces[-1]
            if p.p_out < want and _media_end(index) < want:
                p.p_out = grid.ceil(want)
    _apply_jl(pieces, index, grid, opts, dense)
    _assign_output_times(pieces, grid)
    return pieces


class _DenseVisual:
    """Frame-accurate eyes/mouth signals from ``index/visual_dense.npz`` (the analysis grid of
    :func:`studio.perception.visual.analyze_visual`), for seam placement."""

    def __init__(self, t_us: Any, eyes: Any, mouth: Any, frame_us: float):
        self.t_us, self._eyes, self._mouth, self.frame_us = t_us, eyes, mouth, frame_us

    @classmethod
    def load(cls, job: Job | None) -> _DenseVisual | None:
        if job is None:
            return None
        try:
            from studio.perception.visual import load_dense

            d = load_dense(job)
        except Exception:
            return None
        if not d or "t_us" not in d or not len(d["t_us"]):
            return None
        fps = d.get("fps")
        frame_us = 1e6 * float(fps[1]) / float(fps[0]) if fps is not None and len(fps) == 2 and fps[0] else 33_333.0
        return cls(d["t_us"], d.get("eyes_open"), d.get("mouth_open"), frame_us)

    def _at(self, arr: Any, src_t: Fraction) -> float | None:
        import numpy as np

        if arr is None:
            return None
        t = float(src_t * US)
        i = int(np.searchsorted(self.t_us, t))
        cands = [j for j in (i - 1, i) if 0 <= j < len(self.t_us)]
        if not cands:
            return None
        j = min(cands, key=lambda k: abs(float(self.t_us[k]) - t))
        if abs(float(self.t_us[j]) - t) > self.frame_us:
            return None
        v = float(arr[j])
        return None if v != v else v  # NaN: no face on that frame

    def eyes(self, src_t: Fraction) -> float | None:
        return self._at(self._eyes, src_t)

    def mouth(self, src_t: Fraction) -> float | None:
        return self._at(self._mouth, src_t)


def _closed_mouth_end(index: TakeIndex, dense: _DenseVisual | None, grid: _Grid, e: Fraction, end: Fraction,
                      hi: Fraction, bspans: Sequence[tuple[Fraction, Fraction]]) -> Fraction:
    """The video's out-point, moved (within ±0.2 s, keeping ≥ 0.15 s after the last word and inside the silence)
    to a frame whose last picture shows the mouth closed when the planned one catches it open."""
    def mouth(t: Fraction) -> float | None:
        last = t - grid.frame  # the last frame shown
        v = dense.mouth(last) if dense is not None else None
        return v if v is not None else _mouth_at(index, last)

    here = mouth(e)
    if here is None or here <= 0.3:
        return e
    lo = max(end + Fraction(150, 1000), e - Fraction(1, 5))
    top = min(hi, e + Fraction(1, 5))
    best, best_v = e, here
    c = grid.ceil(lo)
    while c <= top:
        if not any(bs < c < be for bs, be in bspans):
            v = mouth(c)
            if v is not None and v + 0.2 * abs(float(c - e)) < best_v:
                best, best_v = c, v + 0.2 * abs(float(c - e))
        c += grid.frame
    return best if best_v <= 0.2 else e


def _mouth_at(index: TakeIndex, src_t: Fraction) -> float | None:
    """Measured mouth opening (0..1) at a source instant (nearest visual sample; None when unmeasured)."""
    samples = index.visual.samples if index.visual is not None else []
    if not samples:
        return None
    t_us = int(src_t * US)
    lo, hi = 0, len(samples) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if samples[mid].t_us < t_us:
            lo = mid + 1
        else:
            hi = mid
    cands = [j for j in (lo - 1, lo) if 0 <= j < len(samples)]
    s = samples[min(cands, key=lambda j: abs(samples[j].t_us - t_us))]
    if abs(s.t_us - t_us) > 150_000:
        return None
    return s.mouth_open


def _jl_frames(want: int, room: int, cut_frames: Callable[[int], tuple[Fraction, Fraction]], index: TakeIndex,
               blinks: Sequence[tuple[Fraction, Fraction]], grid: _Grid, opts: CompileOptions | None,
               dense: _DenseVisual | None = None) -> int:
    """How many frames to shift a J/L picture edit: the Director's lead, moved by at most ``jl_snap_frac`` of
    it (and ``jl_snap_frames``) onto a better frame pair (doctrine cutting-and-pacing.md: the picture cut sits
    in the shared silent gap "where the mouth is closed on both sides: pick a blink"). A blink touching the
    last outgoing or first incoming frame hides the cut; an open outgoing mouth reads as a word being cut."""
    m0 = max(0, min(want, room))
    if m0 == 0 or opts is None or opts.jl_snap_frames <= 0:
        return m0
    span = max(1, min(opts.jl_snap_frames, math.floor(want * opts.jl_snap_frac)))
    lo, hi = max(1, m0 - span), min(room, m0 + span)
    half = grid.frame / 2

    def cost(m: int) -> float:
        t_out, t_in = cut_frames(m)
        c = abs(m - want) / max(1, want) * 0.8
        in_blink = any(a - half <= t_out < b + half or a - half <= t_in < b + half for a, b in blinks)
        if not in_blink and dense is not None:  # frame-accurate eyes from the dense analysis
            in_blink = any(e is not None and e < 0.35 for e in (dense.eyes(t_out), dense.eyes(t_in)))
        if in_blink:
            c -= 1.0
        mo = dense.mouth(t_out) if dense is not None else None
        if mo is None:
            mo = _mouth_at(index, t_out)
        if mo is not None:
            c += 0.6 * max(0.0, mo - 0.15)
        return c

    return min(range(lo, hi + 1), key=lambda m: (cost(m), abs(m - m0)))


def _apply_jl(pieces: list[_Piece], index: TakeIndex, grid: _Grid, opts: CompileOptions | None = None,
              dense: _DenseVisual | None = None) -> None:
    """Shift picture edits for J/L cuts at true cuts (audio stays at its snap-bounded edit); the shift is the
    Director's lead nudged onto a blink / a closed mouth when one is within reach (:func:`_jl_frames`)."""
    margin = Fraction(round((opts.jl_picture_margin_ms if opts is not None else 0.0) * 1000), US)
    blinks = _blinks(index)
    for k in range(1, len(pieces)):
        B = pieces[k]
        if B.join_in != "cut" or B.seam_in.kind not in ("jcut", "lcut") or B.seam_in.lead_ms <= 0:
            continue
        A = pieces[k - 1]
        want = max(1, grid.frames(Fraction(B.seam_in.lead_ms, 1000)))
        L = grid.frame  # output seconds per frame
        nxt = index.next_word(A.words[-1].id)
        prv = index.prev_word(B.words[0].id)
        a_hard_hi = _us(nxt.start_us) - margin if nxt is not None else _media_end(index)
        b_hard_lo = _us(prv.end_us) + margin if prv is not None else _ZERO
        if B.seam_in.kind == "jcut":
            # outgoing picture extends past its audio; incoming picture starts after its audio
            room_a = math.floor((a_hard_hi - A.p_out) * grid.fps / A.speed)
            room_b = math.floor((B.p_out - B.p_in) * grid.fps / B.speed) - 1
            a_out, b_in = A.p_out, B.p_in
            m = _jl_frames(want, max(0, min(room_a, room_b)),
                           lambda n, a_out=a_out, b_in=b_in, A=A, B=B, L=L: (a_out + (n - 1) * L * A.speed,
                                                                              b_in + n * L * B.speed),
                           index, blinks, grid, opts, dense)
            A.p_out += m * L * A.speed
            B.p_in += m * L * B.speed
        else:
            room_a = math.floor((A.p_out - A.p_in) * grid.fps / A.speed) - 1
            room_b = math.floor((B.p_in - b_hard_lo) * grid.fps / B.speed)
            a_out, b_in = A.p_out, B.p_in
            m = _jl_frames(want, max(0, min(room_a, room_b)),
                           lambda n, a_out=a_out, b_in=b_in, A=A, B=B, L=L: (a_out - (n + 1) * L * A.speed,
                                                                              b_in - n * L * B.speed),
                           index, blinks, grid, opts, dense)
            A.p_out -= m * L * A.speed
            B.p_in -= m * L * B.speed
        if m == 0:
            B.seam_in = SeamTreatment(kind="cut")
        elif m != want:
            B.seam_in = SeamTreatment(kind=B.seam_in.kind, lead_ms=round(float(m * L) * 1000))


def _assign_output_times(pieces: list[_Piece], grid: _Grid) -> None:
    t = _ZERO
    for k, p in enumerate(pieces):
        if k > 0 and p.join_in == "continuous":
            prev = pieces[k - 1]
            p.p_in = p.a_in = prev.p_out  # keep the source continuous after speed rounding
        n = max(1, round_fraction((p.p_out - p.p_in) * grid.fps / p.speed))
        new_out = p.p_in + Fraction(n) / grid.fps * p.speed
        lag = p.a_out - p.p_out
        p.p_out = new_out
        p.a_out = new_out + lag
        p.out_start = t
        p.out_end = t + Fraction(n) / grid.fps
        t = p.out_end


# ============================================================================================ word map
def _word_map(pieces: list[_Piece], index: TakeIndex) -> dict[str, WordSpan | None]:
    """Output span of every kept word (when it is *heard*: J/L-aware); removed words map to None. Spans
    are exact images of the measured source times (not frame-snapped) and are clamped to the piece's
    audio window, except across continuous joins where the audio runs on."""
    wm: dict[str, WordSpan | None] = {w.id: None for w in index.words}
    for k, p in enumerate(pieces):
        lo = p.out_start - (p.p_in - p.a_in) / p.speed
        hi = p.out_end + (p.a_out - p.p_out) / p.speed
        if k > 0 and p.join_in == "continuous":
            lo = pieces[k - 1].out_start
        if k + 1 < len(pieces) and pieces[k + 1].join_in == "continuous":
            hi = pieces[k + 1].out_end
        for w in p.words:
            s = p.out_start + (_us(w.start_us) - p.p_in) / p.speed
            e = p.out_start + (_us(w.end_us) - p.p_in) / p.speed
            s = min(max(s, lo), hi)
            e = min(max(e, s), hi)
            wm[w.id] = WordSpan(out_start=s, out_end=e)
    return wm


# ============================================================================================ framing
@dataclass
class _Layout:
    t0: Fraction
    t1: Fraction
    kind: Literal["full", "below", "above"]  # speaker region: full frame / under top content / above bottom
    rect: tuple[float, float, float, float]  # normalized output rect of the speaker region


class _FaceSampler:
    def __init__(self, index: TakeIndex):
        self.index = index
        vis = index.visual
        pts = vis.face_track.points if vis.face_track is not None else []
        self.track = [(p.t_us, p.cx, p.cy, p.conf) for p in pts if p.conf >= 0.3]
        self.samples = [(s.t_us, s.face_box.cx, s.face_box.cy) for s in vis.samples
                        if s.face_box is not None and s.face_conf >= 0.3]
        self.lost = [(e.start_us, e.end_us) for e in vis.events if e.kind == "face_lost"]
        self.heights = ([(p.t_us, p.h) for p in pts if p.conf >= 0.3 and p.h > 0] or
                        [(s.t_us, s.face_box.h) for s in vis.samples
                         if s.face_box is not None and s.face_conf >= 0.3 and s.face_box.h > 0])
        self._height_times = [t for t, _ in self.heights]

    @property
    def available(self) -> bool:
        return bool(self.track or self.samples)

    def height_at(self, t_us: int) -> float | None:
        """Face-box height (normalized to the source height) nearest ``t_us`` (within 0.5 s)."""
        if not self.heights:
            return None
        k = bisect_right(self._height_times, t_us)
        best = min(self.heights[max(0, k - 1):k + 1], key=lambda th: abs(th[0] - t_us))
        return best[1] if abs(best[0] - t_us) <= 500_000 else None

    def at(self, t_us: int) -> tuple[float, float] | None:
        for a, b in self.lost:
            if a <= t_us <= b:
                return None
        if self.track:
            box = self.index.visual.face_track.at(t_us)  # type: ignore[union-attr]
            return (box.cx, box.cy) if box is not None else None
        if self.samples:
            best = min(self.samples, key=lambda s: abs(s[0] - t_us))
            if abs(best[0] - t_us) <= 500_000:
                return (best[1], best[2])
        return None


def lossless_ceiling(src_w: int, src_h: int, out_w: int = 1080, out_h: int = 1920) -> float:
    """Scale (relative to the 9:16 fill) at which the crop window has exactly the output's pixel count
    (> 1: headroom for punches without upsampling; < 1: the fill itself already upsamples)."""
    _bw, bh = base_window(src_w, src_h, out_w / out_h)
    return (bh * src_h) / out_h


def max_face_scale(src_w: int, src_h: int, out_w: int = 1080, out_h: int = 1920, max_upsample: float = 1.25
                   ) -> float:
    """Largest punch the compiler will render for this source: lossless ceiling x ``max_upsample``,
    never below 1.0 (the base framing itself is never reduced)."""
    return max(1.0, lossless_ceiling(src_w, src_h, out_w, out_h) * max_upsample)


def _ok_scale(index: TakeIndex, out_h: int) -> float:
    """Face-safe maximum punch for this source (lossless ceiling x 1.2, doctrine)."""
    return max(1.0, lossless_ceiling(index.media.width, index.media.height, out_h * 9 // 16, out_h) * 1.2)


class _Framer:
    def __init__(self, doc: CutDocument, index: TakeIndex, pieces: list[_Piece], layouts: list[_Layout],
                 opts: CompileOptions, grid: _Grid, width: int, height: int,
                 word_map: dict[str, WordSpan | None],
                 active: tuple[float, float, float, float] | None = None):
        self.doc, self.index, self.pieces, self.layouts = doc, index, pieces, layouts
        self.active = active
        self.opts, self.grid, self.W, self.H = opts, grid, width, height
        self.word_map = word_map
        self.face = _FaceSampler(index)
        self.src_w, self.src_h = index.media.width, index.media.height
        self.default_anchor = (0.5, 0.4)
        self.last_anchor: tuple[float, float] | None = None
        self.last_scale = 1.0
        self.max_scale = min(1.8, max_face_scale(self.src_w, self.src_h, width, height, opts.max_face_upsample))
        self.notes: list[str] = []
        #: captions sit above the head on this framing (the chin is under the platform band): punch-ins then zoom
        #: about the top of the head, so the head never rises into the caption band (set by :func:`compile`)
        self.hold_head_top = False
        vis = index.visual
        self.head_ratio = (vis.head_top_ratio if vis is not None and vis.head_top_ratio is not None else 0.55)
        self.held_segments: list[str] = []

    # --------------------------------------------------------------- helpers
    def _out_to_src_us(self, seg_pieces: list[_Piece], t: Fraction) -> int | None:
        for p in seg_pieces:
            if p.out_start <= t < p.out_end or (t == p.out_end and p is seg_pieces[-1]):
                return _to_us(p.p_in + (t - p.out_start) * p.speed)
        return None

    def _layout_at(self, t: Fraction) -> _Layout:
        for lay in self.layouts:
            if lay.t0 <= t < lay.t1:
                return lay
        return _Layout(_ZERO, _ZERO, "full", (0.0, 0.0, 1.0, 1.0))

    def _bar_scale(self, layout: _Layout) -> float:
        """Extra zoom that keeps the window inside the active picture (1.0: no bars, or cropping them
        would cost more than ``max_bar_crop_scale`` — then the source's own bars are kept)."""
        if self.active is None:
            return 1.0
        _ax, _ay, aw, ah = self.active
        rw, rh = layout.rect[2] * self.W, layout.rect[3] * self.H
        bw, bh = base_window(self.src_w, self.src_h, rw / rh)
        need = max(1.0, bw / max(aw, 1e-6), bh / max(ah, 1e-6))
        return need if need <= self.opts.max_bar_crop_scale else 1.0

    def _clamp_active(self, scale: float, cx: float, cy: float, layout: _Layout) -> tuple[float, float]:
        """Keep a window that fits inside the active picture from sliding over a bar."""
        if self.active is None or not self._fits(scale, layout):
            return (cx, cy)
        ax, ay, aw, ah = self.active
        rw, rh = layout.rect[2] * self.W, layout.rect[3] * self.H
        bw, bh = base_window(self.src_w, self.src_h, rw / rh)
        w, h = bw / scale, bh / scale
        cx = ax + aw / 2 if w >= aw else min(max(cx, ax + w / 2), ax + aw - w / 2)
        cy = ay + ah / 2 if h >= ah else min(max(cy, ay + h / 2), ay + ah - h / 2)
        return (cx, cy)

    def _fits(self, scale: float, layout: _Layout) -> bool:
        if self.active is None:
            return False
        _ax, _ay, aw, ah = self.active
        rw, rh = layout.rect[2] * self.W, layout.rect[3] * self.H
        bw, bh = base_window(self.src_w, self.src_h, rw / rh)
        return bw / scale <= aw + 1e-9 and bh / scale <= ah + 1e-9

    def _centre(self, anchor: tuple[float, float], scale: float, layout: _Layout,
                point: Point | None, face_h: float | None = None) -> tuple[float, float]:
        """Crop centre (normalized source) that puts the face anchor where the layout wants it; with
        :attr:`hold_head_top` a punch-in zooms about the top of the head instead (it stays where the section's
        base framing has it, so captions placed above the head never meet the hair)."""
        cx, cy = self._centre_plain(anchor, scale, layout, point, face_h)
        if not self.hold_head_top or point is not None or not face_h or layout.kind != "full":
            return (cx, cy)
        s_b = self._bar_scale(layout)
        if scale <= s_b + 1e-6:
            return (cx, cy)
        _bw, bh = base_window(self.src_w, self.src_h, (layout.rect[2] * self.W) / (layout.rect[3] * self.H))
        ht = anchor[1] - face_h * (0.5 + self.head_ratio)  # top of the head (hair), normalized source
        _cxb, cyb = self._centre_plain(anchor, s_b, layout, None, face_h)
        _cxb, cyb = self._clamp_active(s_b, _cxb, cyb, layout)
        hb = bh / s_b
        y0b = min(max(cyb - hb / 2, 0.0), 1.0 - hb)
        frac = (ht - y0b) / hb  # where the head top sits in the base window (0 top .. 1 bottom)
        if not (0.0 <= frac <= 0.6):
            return (cx, cy)
        h = bh / scale
        return (cx, min(max(ht - frac * h + h / 2, h / 2), 1.0 - h / 2))

    def _centre_plain(self, anchor: tuple[float, float], scale: float, layout: _Layout,
                      point: Point | None, face_h: float | None = None) -> tuple[float, float]:
        """Crop centre (normalized source) that puts the face anchor where the layout wants it.

        With no vertical freedom (the window spans the source height at scale 1) the face keeps its
        source height, which makes punches fixation-preserving. With vertical freedom the face is placed
        by its eye line (:meth:`_face_line`); ``face_h`` is the face-box height (normalized source)."""
        if point is not None:
            return (point.x, point.y)
        rw = layout.rect[2] * self.W
        rh = layout.rect[3] * self.H
        bw, bh = base_window(self.src_w, self.src_h, rw / rh)
        ax, ay = anchor
        ux = ax if bw >= 0.999 else 0.5
        w, h = bw / scale, bh / scale
        uy = ay if bh >= 0.999 else self._face_line(layout, (face_h / h) if face_h else None)
        return (ax + (0.5 - ux) * w, ay + (0.5 - uy) * h)

    def _face_line(self, layout: _Layout, fh_r: float | None) -> float:
        """Where the face-box centre sits inside the speaker region (fraction of its height), chosen by
        the eye line. ``fh_r`` is the face-box height as a fraction of the window height (None: a typical
        0.3). Priorities, strongest first: the eyes stay inside the platform's UI-safe band (under the top
        band, above the bottom band: doctrine keeps the eyes above y≈1248 under a top split); at most
        ``forehead_cut_max`` of the face box (upper forehead) runs past the region's top edge; the chin
        stays inside the region; then the eyes sit on the layout's line (about a third down)."""
        o = self.opts
        ry, rh = layout.rect[1], layout.rect[3]
        fh = (0.3 if fh_r is None else max(0.02, fh_r)) * rh  # face-box height, frame fraction
        zone = SAFE_ZONES.get(o.platform, SAFE_ZONES["master"])
        line = {"below": o.split_eye_line_below, "above": o.split_eye_line_above}.get(layout.kind, o.eye_line)
        eye = ry + line * rh  # frame fraction
        eye = min(eye, ry + rh - o.chin_margin - (1.0 - _EYE_FROM_TOP) * fh)  # chin inside the region
        eye = max(eye, ry + (_EYE_FROM_TOP - o.forehead_cut_max) * fh)  # headroom under the region top
        e_lo = max(ry, zone["top"] + 0.05)
        e_hi = min(ry + rh, 1.0 - zone["bottom"] - o.eye_band_margin)
        if e_lo <= e_hi:
            eye = min(max(eye, e_lo), e_hi)
        centre = eye + (0.5 - _EYE_FROM_TOP) * fh
        return (centre - ry) / rh

    def _face_height(self, seg_pieces: list[_Piece], S: Fraction, E: Fraction) -> float | None:
        """Median face-box height (normalized source) over the segment's output span."""
        hs: list[float] = []
        t = S
        while t < E:
            su = self._out_to_src_us(seg_pieces, t)
            if su is not None and (h := self.face.height_at(su)) is not None:
                hs.append(h)
            t += Fraction(1, 5)
        return statistics.median(hs) if hs else None

    def _anchor_path(self, seg_pieces: list[_Piece], S: Fraction, E: Fraction, crop_w: float,
                     crop_h: float, continuous: bool) -> list[tuple[Fraction, Fraction, tuple[float, float]]]:
        """Dead-zone anchor path: [(move_start, move_end, anchor)] (first entry = initial anchor at S)."""
        step = Fraction(1, 10)
        samples: list[tuple[Fraction, tuple[float, float]]] = []
        t = S
        while t < E:
            su = self._out_to_src_us(seg_pieces, t)
            if su is not None:
                f = self.face.at(su)
                if f is not None:
                    samples.append((t, f))
            t += step
        dz_x = self.opts.face_dead_zone * max(crop_w, 1e-6)
        dz_y = self.opts.face_dead_zone * max(crop_h, 1e-6)
        free_x, free_y = crop_w < 0.999, crop_h < 0.999

        def med(pts: Sequence[tuple[Fraction, tuple[float, float]]]) -> tuple[float, float]:
            return (statistics.median(p[1][0] for p in pts), statistics.median(p[1][1] for p in pts))

        if not samples:
            init = self.last_anchor or self.default_anchor
            return [(S, S, init)]
        dead = Fraction(round(self.opts.face_dead_time_ms), 1000)
        first = [p for p in samples if p[0] < S + dead] or samples[:1]
        init = med(first)
        if self.last_anchor is not None:
            lx, ly = self.last_anchor
            if continuous or (abs(init[0] - lx) <= dz_x and abs(init[1] - ly) <= dz_y):
                init = self.last_anchor
        path = [(S, S, init)]
        cur = init
        out_since: Fraction | None = None
        move = Fraction(round(max(self.opts.face_move_ms, 400)), 1000)
        for t, f in samples:
            off = (free_x and abs(f[0] - cur[0]) > dz_x) or (free_y and abs(f[1] - cur[1]) > dz_y)
            if not off:
                out_since = None
                continue
            if out_since is None:
                out_since = t
                continue
            if t - out_since >= dead:
                window = [p for p in samples if out_since <= p[0] <= t]
                target = med(window)
                ts = self.grid.round(t)
                te = self.grid.round(t + move)
                if te < E - self.grid.frame:
                    path.append((ts, te, target))
                    cur = target
                out_since = None
        return path

    # --------------------------------------------------------------- main
    def run(self) -> None:
        by_seg: dict[int, list[_Piece]] = {}
        for p in self.pieces:
            by_seg.setdefault(p.seg_idx, []).append(p)
        prev_scale = 1.0
        for si in sorted(by_seg):
            seg_pieces = by_seg[si]
            seg = seg_pieces[0].seg
            S, E = seg_pieces[0].out_start, seg_pieces[-1].out_end
            continuous = seg_pieces[0].join_in == "continuous"
            framing = seg.framing
            # a "punch" seam without explicit framing toggles base <-> punch; on a true cut it hides the pose
            # jump, on a continuous join (same take, e.g. a word-range split) it is an emphasis punch-in
            if framing is None and seg_pieces[0].seam_in.kind == "punch" and seg_pieces[0].join_in != "start":
                if prev_scale >= 1.15:
                    framing = None
                else:
                    sc = min(self.opts.seam_punch_scale, max(self.opts.min_seam_punch_scale,
                                                             _ok_scale(self.index, self.H)))
                    framing = Framing(scale=min(1.8, sc), center="face", ease="cut")
            target_scale = framing.scale if framing is not None else 1.0
            if target_scale > self.max_scale + 1e-9:
                self.notes.append(f"{seg.id}: framing scale {target_scale:.2f} clamped to {self.max_scale:.2f} "
                                  f"(source {self.src_w}x{self.src_h}: lossless ceiling x "
                                  f"{self.opts.max_face_upsample:g} upsampling limit)")
                target_scale = self.max_scale
            if framing is not None and target_scale <= 1.0 + 1e-9 and not isinstance(framing.center, Point):
                framing = None  # nothing left of the punch: keep the base framing (and its dead-zone lock)
            ok = _ok_scale(self.index, self.H)
            if framing is not None and target_scale > ok + 1e-9:
                self.notes.append(f"{seg.id}: {target_scale:.2f}x upsamples the face past the {ok:.2f}x "
                                  "clean-footage limit (lossless ceiling x 1.2); on this source a cutaway or an "
                                  "honest jump cut may read better than a punch")
            point = framing.center if (framing is not None and isinstance(framing.center, Point)) else None
            # scale schedule [(t, scale, ease)]
            sched: list[tuple[Fraction, float, str]] = [(S, 1.0 if framing is not None and framing.ease != "cut"
                                                         else target_scale, "hold")]
            if framing is not None and target_scale != 1.0:
                anchor_id = framing.anchor_word or seg.from_word
                span = self.word_map.get(anchor_id)
                onset = span.out_start if span is not None else S
                lead = Fraction(round(self.opts.punch_lead_ms * 1000), US)
                ta = min(max(self.grid.floor(onset - lead), S), E - self.grid.frame)
                if framing.ease == "cut":
                    if ta <= S + self.grid.frame:
                        sched = [(S, target_scale, "hold")]
                    else:
                        sched = [(S, 1.0, "hold"), (ta, target_scale, "hold")]
                elif framing.ease == "smooth":
                    t0 = max(S, self.grid.round(ta - Fraction(framing.ease_ms, 1000)))
                    if ta <= S:
                        sched = [(S, target_scale, "hold")]
                    else:
                        sched = [(S, 1.0, "hold")]
                        if t0 > S:
                            sched.append((t0, 1.0, "hold"))
                        sched.append((ta, target_scale, "ease_in_out"))
                else:  # push
                    te = max(S + self.grid.frame, E - self.grid.frame)
                    sched = [(S, 1.0, "hold"), (te, target_scale, "ease_in_out")]
            # anchor path (dead zone relative to the tightest crop of the segment)
            smax = max(s for _, s, _ in sched)
            bw, bh = base_window(self.src_w, self.src_h, self.W / self.H)
            path = self._anchor_path(seg_pieces, S, E, bw / smax, bh / smax, continuous)
            if framing is not None and framing.ease == "push":
                path = path[:1]  # a slow push is never combined with a re-centring move
            # layout changes inside the segment
            lay_times = sorted({lay.t0 for lay in self.layouts if S < lay.t0 < E}
                               | {lay.t1 for lay in self.layouts if S < lay.t1 < E})
            times = sorted({S, *(t for t, _, _ in sched), *(a for a, _, _ in path[1:]),
                            *(b for _, b, _ in path[1:]), *lay_times})
            face_h = self._face_height(seg_pieces, S, E)
            keys: list[FramingKey] = []
            for t in times:
                lay = self._layout_at(t)
                s = min(1.8, _scale_at(sched, t) * self._bar_scale(lay))
                anc = _anchor_at(path, t)
                cx, cy = self._centre(anc, s, lay, point, face_h)
                cx, cy = self._clamp_active(s, cx, cy, lay)
                ease = "hold"
                for tt, _s, ez in sched:
                    if tt == t and ez != "hold":
                        ease = ez
                for _a, b, _tg in path[1:]:
                    if b == t:
                        ease = "ease_in_out"
                if t in lay_times:
                    ease = "hold"
                cx = min(max(cx, 0.0), 1.0)
                cy = min(max(cy, 0.0), 1.0)
                keys.append(FramingKey(out_t=t, scale=round(min(1.8, max(1.0, s)), 6), cx=round(cx, 6),
                                       cy=round(cy, 6), ease=ease))  # type: ignore[arg-type]
            keys = _dedupe_keys(keys)
            self.last_anchor = _anchor_at(path, E)
            prev_scale = _scale_at(sched, E - self.grid.frame)
            for p in seg_pieces:
                p.framing = _keys_for_piece(keys, p.out_start, p.out_end)


def _scale_at(sched: list[tuple[Fraction, float, str]], t: Fraction) -> float:
    """Scale at ``t`` from a schedule of (time, scale, arrival ease) steps (geometric interpolation)."""
    cur = sched[0][1]
    for k in range(1, len(sched)):
        t1, s1, ez = sched[k]
        t0, s0, _ = sched[k - 1]
        if t >= t1:
            cur = s1
            continue
        if t > t0:
            cur = s0 * (s1 / s0) ** ease_value(ez, float((t - t0) / (t1 - t0)))
        break
    return cur


def _anchor_at(path: list[tuple[Fraction, Fraction, tuple[float, float]]], t: Fraction) -> tuple[float, float]:
    """Face anchor at ``t`` along a dead-zone path of eased moves."""
    cur = path[0][2]
    for k in range(1, len(path)):
        a, b, tgt = path[k]
        if t >= b:
            cur = tgt
            continue
        if t > a:
            p = ease_value("ease_in_out", float((t - a) / (b - a)))
            cur = (cur[0] + (tgt[0] - cur[0]) * p, cur[1] + (tgt[1] - cur[1]) * p)
        break
    return cur


def _dedupe_keys(keys: list[FramingKey]) -> list[FramingKey]:
    """Merge keys at the same instant (the later wins). Identical consecutive keys are kept: a key is
    also the start point of the next key's interpolation."""
    out: list[FramingKey] = []
    for k in keys:
        if out and out[-1].out_t == k.out_t:
            out[-1] = k
            continue
        out.append(k)
    return out


def _keys_for_piece(keys: list[FramingKey], t0: Fraction, t1: Fraction) -> list[FramingKey]:
    """Keys affecting [t0, t1): the last key at/before t0, every key inside, and the first key at/after
    t1 (so interpolation across piece boundaries stays exact)."""
    before = [k for k in keys if k.out_t <= t0]
    inside = [k for k in keys if t0 < k.out_t < t1]
    after = [k for k in keys if k.out_t >= t1]
    out = ([before[-1]] if before else []) + inside + (after[:1] if after else [])
    return [k.model_copy() for k in out]


# ============================================================================================ inserts etc.
def _resolve_asset_path(asset: AssetRef | None, job: Job | None) -> str | None:
    if asset is None:
        return None
    rec = asset
    if rec.path is None and rec.id and job is not None:
        loaded = job.load_asset(rec.id)
        if loaded is not None:
            rec = loaded
    if rec.path is None:
        return None
    p = Path(rec.path)
    if not p.is_absolute():
        if job is None:
            return None
        p = job.root / p
    return str(p.resolve()) if p.exists() else None


def _span_of(word_map: dict[str, WordSpan | None], a: str, b: str) -> tuple[Fraction, Fraction] | None:
    sa, sb = word_map.get(a), word_map.get(b)
    if sa is None or sb is None:
        return None
    return (min(sa.out_start, sb.out_start), max(sa.out_end, sb.out_end))


def _next_onset_after(word_map: dict[str, WordSpan | None], order: list[str], wid: str) -> Fraction | None:
    try:
        i = order.index(wid)
    except ValueError:
        return None
    for w in order[i + 1:]:
        s = word_map.get(w)
        if s is not None:
            return s.out_start
    return None


def _pip_rect(ins: Insert, opts: CompileOptions, W: int, H: int, face_box: tuple[float, float, float, float] | None
              ) -> tuple[float, float, float, float]:
    asset = ins.asset
    aspect = 9 / 16
    if isinstance(asset, AssetRef) and asset.width and asset.height:
        aspect = asset.width / asset.height
    w_px = opts.pip_width * W
    h_px = w_px / aspect
    if h_px > opts.pip_max_height * H:
        h_px = opts.pip_max_height * H
        w_px = h_px * aspect
    w, h = w_px / W, h_px / H
    z = SAFE_ZONES.get(opts.platform, SAFE_ZONES["master"])
    left = z["left"]
    right = 1.0 - max(z["left"], 0.06) - w
    pos = ins.pip_position
    x = left if pos.endswith("left") else right
    top_y = z["top"] + 0.01
    bottom_y = 1.0 - z["bottom"] - 0.01 - h
    candidates = ([top_y + d for d in (0.0, 0.05, 0.10, 0.15)] if pos.startswith("top")
                  else [bottom_y - d for d in (0.0, 0.05, 0.10, 0.15)])
    candidates = [c for c in candidates if top_y - 1e-9 <= c <= bottom_y + 1e-9] or [top_y]

    def overlap(y: float) -> float:
        if face_box is None:
            return 0.0
        fx, fy, fw, fh = face_box
        ix = max(0.0, min(x + w, fx + fw) - max(x, fx))
        iy = max(0.0, min(y + h, fy + fh) - max(y, fy))
        return ix * iy

    area = w * h

    def cost(c: float) -> float:  # grazing the face box margin (<5 % of the PiP) does not count
        o = overlap(c)
        return 0.0 if o < 0.05 * area else o

    y = min(candidates, key=lambda c: (round(cost(c), 4), abs(c - candidates[0])))
    return (round(x, 4), round(y, 4), round(w, 4), round(h, 4))


# ============================================================================================ compile
def _src_at(pieces: Sequence[_Piece], out_t: Fraction) -> Fraction | None:
    """Picture source time shown at output time ``out_t``."""
    for p in pieces:
        if p.out_start <= out_t < p.out_end:
            return p.p_in + (out_t - p.out_start) * p.speed
    return None


def _live_return(end: Fraction, pieces: Sequence[_Piece], blinks: Sequence[tuple[Fraction, Fraction]], grid: _Grid,
                 *, lo: Fraction, hi: Fraction, nxt: Fraction | None, max_frames: int) -> Fraction:
    """Move an insert's out point by at most ``max_frames`` (within ``[lo, hi]``) so the face it returns to is
    not mid-blink. Preference: earlier (the insert still covers its anchor words, ``lo``), then later but before
    the next word's onset ``nxt``, then a few frames into that word (doctrine: a weak return frame is worse
    than moving the out point)."""
    src = _src_at(pieces, end)
    if src is None or not _in_blink(blinks, src):
        return end

    def live(c: Fraction) -> bool:
        s = _src_at(pieces, c) if lo <= c < hi else None
        return s is not None and not _in_blink(blinks, s)

    steps = range(1, max_frames + 1)
    tiers = ([end - k * grid.frame for k in steps],
             [end + k * grid.frame for k in steps if nxt is None or end + k * grid.frame <= nxt],
             [end + k * grid.frame for k in steps if nxt is not None and end + k * grid.frame > nxt])
    for tier in tiers:
        for c in tier:
            if live(c):
                return c
    return end


def compile(doc: CutDocument, index: TakeIndex, *, job: Job | None = None, width: int = 1080,
            height: int = 1920, options: CompileOptions | None = None) -> Timeline:
    """Resolve IDs to exact output times on the source-fps frame grid (see module docstring)."""
    opts = options or CompileOptions()
    if width % 2 or height % 2:
        raise CompileError("output width/height must be even")
    fps = normalize_fps(index.media.fps)
    grid = _Grid(fps)
    source_path = str(job.mezz_path) if job is not None else None
    job_id = doc.job_id or (job.id if job is not None else "")
    if not doc.segments:
        return Timeline(fps=fps, width=width, height=height, duration=_ZERO, doc_version=doc.version,
                        job_id=job_id, source_path=source_path,
                        word_map={w.id: None for w in index.words})
    pieces = _build_pieces(doc, index, opts, grid, _DenseVisual.load(job))
    word_map = _word_map(pieces, index)
    duration = pieces[-1].out_end
    order = [w for s in doc.segments for w in index.word_ids(s.from_word, s.to_word)]

    # ---- inserts (needed before framing: split layouts re-frame the speaker)
    inserts: list[TimelineInsert] = []
    layouts: list[_Layout] = []
    face = _FaceSampler(index)
    blinks_src = _blinks(index)
    for z, ins in enumerate(doc.inserts):
        span = _span_of(word_map, ins.anchor_from_word, ins.anchor_to_word)
        if span is None:
            continue
        start = grid.round(span[0])
        end = span[1]
        nxt = _next_onset_after(word_map, order, ins.anchor_to_word)
        hold_max = end + Fraction(round(opts.insert_return_max_ms), 1000)
        if nxt is not None:
            ret = nxt - opts.insert_return_margin_frames * grid.frame
            end = max(end, min(ret, hold_max))
        else:
            end = max(end, min(duration, hold_max))
        end = grid.round(end)
        min_end = start + grid.ceil(Fraction(round(opts.insert_min_ms), 1000))
        end = max(end, min_end)
        end = min(end, duration)
        start = min(start, duration - grid.frame)
        if end <= start:
            continue
        if ins.mode in ("full", "card") and opts.blink_guard_frames > 0 and end < duration:
            end = _live_return(end, pieces, blinks_src, grid, lo=max(min_end, grid.ceil(span[1])), hi=duration,
                               nxt=nxt, max_frames=opts.blink_guard_frames)
        asset = ins.asset
        if isinstance(asset, AssetRef) and asset.licence is None and ins.licence is not None:
            asset = asset.model_copy(update={"licence": ins.licence})
        asset_path = _resolve_asset_path(asset if isinstance(asset, AssetRef) else None, job)
        drawn = not isinstance(asset, AssetRef) or asset_path is not None
        rect = None
        if ins.mode == "split_top":
            rect = (0.0, 0.0, 1.0, round(ins.split_ratio, 4))
            if drawn:
                layouts.append(_Layout(start, end, "below", (0.0, rect[3], 1.0, 1.0 - rect[3])))
        elif ins.mode == "split_bottom":
            rect = (0.0, round(1.0 - ins.split_ratio, 4), 1.0, round(ins.split_ratio, 4))
            if drawn:
                layouts.append(_Layout(start, end, "above", (0.0, 0.0, 1.0, rect[1])))
        inserts.append(TimelineInsert(
            insert_id=ins.id, mode=ins.mode, out_start=start, out_end=end, asset=asset,
            asset_path=asset_path, asset_in_us=(asset.in_ms * 1000) if isinstance(asset, AssetRef) else 0,
            audio=ins.audio, transition_in=ins.transition_in, transition_out=ins.transition_out, rect=rect, z=z,
        ))

    # ---- framing
    active = opts.active_area
    if active == "auto":
        active = _job_active_area(job)
    framer = _Framer(doc, index, pieces, layouts, opts, grid, width, height, word_map, active)
    framer.hold_head_top = _captions_above_head(doc, index, width, height)
    framer.run()
    if framer.hold_head_top and any(p.seg.framing is not None and p.seg.framing.scale > 1.0 for p in pieces):
        framer.notes.append("captions sit above the head on this framing (the chin is under the platform's caption "
                            "band), so punch-ins zoom about the top of the head: the head never rises into the "
                            "captions (the face grows downward instead)")
    if job is not None:
        for msg in framer.notes:
            job.trace("compile_note", doc_version=doc.version, note=msg)

    # ---- segments; then PiP rects (they need the transformed face)
    segments = [TimelineSegment(
        seg_id=p.seg.id, src_in_us=_to_us(p.p_in), src_out_us=_to_us(p.p_out), out_start=p.out_start,
        out_end=p.out_end, speed=p.seg.speed, audio_src_in_us=_to_us(p.a_in), audio_src_out_us=_to_us(p.a_out),
        framing=p.framing, seam_in=p.seam_in, word_ids=[w.id for w in p.words],
        source=p.words[0].source if p.words else "main",
    ) for p in pieces]
    seams = [p.out_start for p in pieces[1:] if p.join_in in ("cut", "trim")]
    tl = Timeline(fps=fps, width=width, height=height, duration=duration, doc_version=doc.version,
                  job_id=job_id, source_path=source_path, segments=segments, inserts=inserts,
                  word_map=word_map, seams=seams)
    for k, ti in enumerate(tl.inserts):
        if ti.mode != "pip":
            continue
        mid = (ti.out_start + ti.out_end) / 2
        fb = _face_box_out(tl, index, face, mid)
        tl.inserts[k] = ti.model_copy(update={"rect": _pip_rect(doc.insert(ti.insert_id), opts, width, height, fb)})

    # ---- captions, texts, sfx, music
    tl.captions = _build_captions(doc, index, tl, grid, opts, face)
    tl.texts = _texts(doc, tl, grid, opts)
    tl.sfx = _sfx(doc, tl, job)
    tl.music = _music(doc, tl, job)
    return Timeline.model_validate(tl.model_dump())


def _job_active_area(job: Job | None) -> tuple[float, float, float, float] | None:
    """The mezzanine's active picture (black bars excluded), cached in ``media/active_area.json`` so a compile gives
    the same framing after the mezzanine was reclaimed (it is regenerable and rebuilt before any render)."""
    if job is None:
        return None
    import json

    cache = job.media_dir / "active_area.json"
    if job.mezz_path.exists():
        area = detect_active_area(job.mezz_path)
        with contextlib.suppress(OSError):
            cache.write_text(json.dumps({"active": list(area) if area is not None else None}))
        return area
    try:
        data = json.loads(cache.read_text())
    except (OSError, ValueError):
        return None
    a = data.get("active")
    return tuple(float(v) for v in a) if a else None  # type: ignore[return-value]


def _captions_above_head(doc: CutDocument, index: TakeIndex, width: int, height: int) -> bool:
    """True when the document's captions will sit above the head (auto placement, and a one-line block does not fit
    between the chin and the platform's relaxed caption floor at the base framing)."""
    cap = doc.captions
    if cap is not None and (not cap.enabled or cap.position not in ("auto", "upper_third")):
        return False
    if index.visual is None or not (index.visual.face_track or index.visual.samples):
        return False
    try:
        from studio.compile import captions as C

        platforms = [d.platform for d in doc.deliverables] or ["tiktok"]
        zone = C.safe_zone_for(platforms, width=width, height=height)
        style = cap.style if cap is not None else CaptionStyle()
        info = C.under_chin_reframe(index, style=style, zone=zone, width=width, height=height)
    except Exception:
        return False
    return info is not None and info["scale"] > 1.0 + 1e-6


def compile_timeline(doc: CutDocument, index: TakeIndex, media: MediaInfo | None = None, *, job: Job | None = None,
                     width: int = 1080, height: int = 1920, options: CompileOptions | None = None) -> Timeline:
    """:func:`compile` with an optional ``media`` override (e.g. the MediaInfo of the mezzanine actually
    rendered, when it differs from the one embedded in the index)."""
    if media is not None and media != index.media:
        index = index.model_copy(update={"media": media})
    return compile(doc, index, job=job, width=width, height=height, options=options)


# ============================================================================================ overlays
def _face_box_out(tl: Timeline, index: TakeIndex, face: _FaceSampler, t: Fraction
                  ) -> tuple[float, float, float, float] | None:
    seg = tl.segment_at(t)
    if seg is None:
        return None
    su = seg.out_to_src_us(t)
    box = index.face_at(su) if face.available else None
    if box is None:
        return None
    src_w, src_h = index.media.width, index.media.height
    p0 = map_source_point(tl, t, box.x, box.y, src_w, src_h)
    p1 = map_source_point(tl, t, box.x + box.w, box.y + box.h, src_w, src_h)
    if p0 is None or p1 is None:
        return None
    return (p0[0], p0[1], p1[0] - p0[0], p1[1] - p0[1])


def _caption_y(policy: str, tl: Timeline, index: TakeIndex, face: _FaceSampler, t: Fraction, block_h: float,
               opts: CompileOptions) -> float:
    z = SAFE_ZONES.get(opts.platform, SAFE_ZONES["master"])
    lo = z["top"] + block_h / 2
    hi = 1.0 - z["bottom"] - block_h / 2
    fixed = {"lower_third": 0.60, "center": 0.50, "upper_third": 0.30}
    if policy in fixed:
        return min(max(fixed[policy], lo), hi)
    fb = _face_box_out(tl, index, face, t)
    if fb is None:
        return min(max(0.60, lo), hi)
    chin = fb[1] + fb[3]
    y = chin + opts.caption_chin_margin_px / tl.height + block_h / 2
    return min(max(y, lo), hi)


def _build_captions(doc: CutDocument, index: TakeIndex, tl: Timeline, grid: _Grid, opts: CompileOptions,
                    face: _FaceSampler) -> list[TimelineCaptionPage]:
    """Caption pages for the timeline. The captions module owns caption policy (paging, emphasis,
    doctrine timing incl. seam snapping, face-aware placement); the built-in pager below is the fallback
    and follows the same contract: word spans are the measured onsets on the frame grid, the *page*
    leads the first onset by ``highlight_lead_frames`` and the overlay renderer leads the word highlight
    by the same amount (so the lead is applied once)."""
    if opts.caption_builder == "captions_module":
        # only an explicit "not available" falls back; any other error inside the captions module
        # propagates (a silent fallback to the simpler pager would ship worse captions unnoticed)
        from studio.compile import captions as captions_mod

        try:
            return list(captions_mod.build_caption_pages(index, doc, tl))
        except NotImplementedError:
            pass
    return _captions(doc.captions, index, tl, grid, opts, face)


def _captions(plan: CaptionPlan | None, index: TakeIndex, tl: Timeline, grid: _Grid, opts: CompileOptions,
              face: _FaceSampler) -> list[TimelineCaptionPage]:
    if plan is None or not plan.enabled or not plan.pages:
        return []
    wm = tl.word_map
    style = plan.style
    lead = grid.frame * style.highlight_lead_frames
    built: list[tuple[object, list[TimelineCaptionWord]]] = []
    for page in plan.pages:
        words: list[TimelineCaptionWord] = []
        for wid in page.word_ids:
            span = wm.get(wid)
            if span is None:
                continue
            s = max(_ZERO, grid.floor(span.out_start))
            e = max(s + grid.frame, grid.round(span.out_end))
            words.append(TimelineCaptionWord(word_id=wid, text=index.word(wid).text, out_start=s, out_end=e,
                                             emphasis=wid in page.emphasis_word_ids))
        if words:
            words.sort(key=lambda w: w.out_start)
            built.append((page, words))
    built.sort(key=lambda pw: pw[1][0].out_start)
    pages: list[TimelineCaptionPage] = []
    gap = grid.frame * opts.caption_page_gap_frames
    hold = grid.round(Fraction(round(opts.caption_end_hold_ms), 1000))
    close = Fraction(round(opts.caption_close_gap_ms), 1000)
    prev_y: float | None = None
    block_h = style.size_px * 1.25 * style.lines / 1920
    for k, (page, words) in enumerate(built):
        start = max(_ZERO, words[0].out_start - lead)  # the page appears just ahead of the voice
        if pages and start < pages[-1].out_end + gap:  # never overlap the previous page
            start = pages[-1].out_end + gap
        last_end = max(w.out_end for w in words)
        nxt = max(_ZERO, built[k + 1][1][0].out_start - lead) if k + 1 < len(built) else None
        end = nxt - gap if (nxt is not None and nxt - last_end < close) else last_end + hold
        end = max(end, start + grid.frame)
        if nxt is not None:
            end = min(end, max(nxt - gap, start + grid.frame))
        end = min(end, tl.duration)
        if end <= start:
            continue
        for w in words:  # a word's highlight stays inside its page
            w.out_start = min(max(w.out_start, start), end - grid.frame)
            w.out_end = min(max(w.out_end, w.out_start + grid.frame), end)
        policy = page.position or plan.position  # type: ignore[attr-defined]
        if page.y_norm is not None:  # type: ignore[attr-defined]
            y = page.y_norm  # type: ignore[attr-defined]
        else:
            y = _caption_y(policy, tl, index, face, (start + end) / 2, block_h, opts)
            if prev_y is not None and abs(y - prev_y) < 60 / 1920:  # hysteresis: do not twitch
                y = prev_y
        prev_y = y
        pages.append(TimelineCaptionPage(page_id=page.id, words=words, text=page.text, out_start=start,  # type: ignore[attr-defined]
                                         out_end=end, style=style, y_norm=round(min(max(y, 0.0), 1.0), 4)))
    return pages


_TEXT_Y = {"top": 0.19, "upper_third": 0.27, "center": 0.50, "lower_third": 0.60, "bottom": 0.62}
_TEXT_AUTO_Y = {"hook_title": 0.21, "callout": 0.27, "list": 0.40, "lower_third": 0.60, "label": 0.27,
                "cta": 0.58}


def _texts(doc: CutDocument, tl: Timeline, grid: _Grid, opts: CompileOptions) -> list[TimelineText]:
    out: list[TimelineText] = []
    for t in doc.texts:
        span = _span_of(tl.word_map, t.anchor_from_word, t.anchor_to_word)
        if span is None:
            continue
        start = max(_ZERO, grid.floor(span[0] - grid.frame * 2))
        end = grid.round(span[1])
        if opts.text_min_read:
            n_words = max(1, len((t.text + " " + " ".join(t.items)).split()))
            margin = opts.hook_title_margin_s if t.kind == "hook_title" else opts.text_margin_s
            need = Fraction(round((n_words * opts.text_read_s_per_word + margin) * 1000), 1000)
            end = max(end, grid.ceil(start + need))
        end = min(end, tl.duration)
        if end <= start:
            continue
        if isinstance(t.position, Point):
            x, y = t.position.x, t.position.y
        elif t.position == "auto":
            x, y = 0.5, _TEXT_AUTO_Y.get(t.kind, 0.27)
        else:
            x, y = 0.5, _TEXT_Y.get(t.position, 0.27)
        out.append(TimelineText(text_id=t.id, kind=t.kind, text=t.text, items=list(t.items), out_start=start,
                                out_end=end, x_norm=x, y_norm=y, style=t.style, animation=t.animation))
    return out


def _sfx(doc: CutDocument, tl: Timeline, job: Job | None) -> list[TimelineSfx]:
    out: list[TimelineSfx] = []
    for cue in doc.audio.sfx:
        span = tl.word_map.get(cue.anchor_word)
        if span is None:
            continue
        t = (span.out_start if cue.at == "start" else span.out_end) + Fraction(cue.offset_ms, 1000)
        t = min(max(t, _ZERO), tl.duration)
        out.append(TimelineSfx(sfx_id=cue.id, kind=cue.kind, out_t=t, asset=cue.asset,
                               asset_path=_resolve_asset_path(cue.asset, job), gain_db=cue.gain_db))
    return out


def _music(doc: CutDocument, tl: Timeline, job: Job | None) -> TimelineMusic | None:
    m = doc.audio.music
    if m is None:
        return None
    grid = _Grid(tl.fps)
    start, end = _ZERO, tl.duration
    ws = tl.word_map.get(m.start_word) if m.start_word else None
    we = tl.word_map.get(m.end_word) if m.end_word else None
    if ws is not None:
        start = grid.floor(ws.out_start)
    if we is not None:
        end = min(grid.ceil(we.out_end), tl.duration)
    if end <= start:
        start, end = _ZERO, tl.duration
    hits = sorted(span.out_start for w in m.hit_word_ids if (span := tl.word_map.get(w)) is not None)
    return TimelineMusic(asset=m.asset, asset_path=_resolve_asset_path(m.asset, job), out_start=start, out_end=end,
                         asset_in_us=(m.asset.in_ms * 1000) if m.asset is not None else 0,
                         level_lu_under_speech=m.level_lu_under_speech, duck=m.duck, duck_db=m.duck_db,
                         fade_in_ms=m.fade_in_ms, fade_out_ms=m.fade_out_ms, hits=hits)
