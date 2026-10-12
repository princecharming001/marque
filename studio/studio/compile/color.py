"""Colour measurement of the mezzanine at word times (doctrine ``color-and-look``: look before you touch).

Why a free function: the Director's ``measure_color`` tool measured frames inside the agent, so nothing else
could ask the same question. The standard tier needs it twice (standard-tier-plan.md §2: the code plan sets
``set_color`` only on a measured fault; §3 step 8: a chat edit shows the measurement when asked) and has no
Director. Both tiers now call :func:`measure_color`; the Director's tool formats the result with
:func:`format_color_measurement`, word for word what it always printed, and :meth:`ColorMeasurement.summary`
is the one-line form for the planner's digest.

What is measured, per word: the mezzanine frame at the word's midpoint, decoded at :data:`MEASURE_WIDTH` px wide
(these are area statistics, so the downscale changes nothing and the decode is fast): the frame's mean BT.709
Y' on the 0-255 scale, the share of pixels clipped (Y' >= :data:`CLIPPED_Y`) and crushed (Y' <= :data:`CRUSHED_Y`),
and, when the Take Index has a face at that time, the face box's mean Y' and CIELAB a*/b* (+b* warmer/yellow,
+a* magenta). Skin usually reads natural around face Y' 150-190 in SDR with a small positive a*/b*; the
doctrine judges differences between takes, not absolute numbers, because the mezzanine is tone-mapped SDR and
the frame mean follows the background as much as the light.

Word references may carry a suffix after a colon (``w0012:hook``), which is ignored, as the Director's tool
always allowed. When no words are named, :func:`default_items` spreads :data:`DEFAULT_SAMPLES` words over the
kept story (or over the whole take when the document has no story yet), so a code-only caller gets a
representative set without choosing frames itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = [
    "CLIPPED_Y", "CRUSHED_Y", "DEFAULT_SAMPLES", "MAX_ITEMS", "MEASURE_WIDTH",
    "ColorMeasurement", "FaceColor", "FrameColor",
    "default_items", "format_color_measurement", "measure_color", "rgb_to_lab",
]

#: Decode width for the measured frames (area statistics; the aspect follows the source).
MEASURE_WIDTH = 540
#: Pixels at or above this BT.709 Y' (0-255) count as clipped; at or below :data:`CRUSHED_Y` as crushed.
CLIPPED_Y = 250
CRUSHED_Y = 6
#: Most words measured in one call (the Director's tool cap; each is one frame decode).
MAX_ITEMS = 24
#: Words sampled when the caller names none.
DEFAULT_SAMPLES = 6


@dataclass(frozen=True)
class FaceColor:
    """The face box's colour: mean BT.709 Y' (0-255) and CIELAB a*/b* (D65) of its pixels."""

    y_mean: float
    a: float
    b: float


@dataclass(frozen=True)
class FrameColor:
    """One measured frame: the word it stands for, the source time, whole-frame luma statistics, the face."""

    word_id: str
    t_us: int
    y_mean: float        # BT.709 Y' 0-255 over the whole frame
    clipped_pct: float   # % of pixels with Y' >= CLIPPED_Y
    crushed_pct: float   # % of pixels with Y' <= CRUSHED_Y
    face: FaceColor | None = None


@dataclass(frozen=True)
class ColorMeasurement:
    """The measured frames in the order asked, and the file they were decoded from."""

    source: Path
    frames: list[FrameColor]

    def summary(self) -> str:
        """One line for a digest: the ranges over the measured frames (``Colour (4 frames w0001..w0041): ...``)."""
        if not self.frames:
            return "Colour: nothing measured"
        fs = self.frames
        span = fs[0].word_id if len(fs) == 1 else f"{fs[0].word_id}..{fs[-1].word_id}"
        parts = [f"frame Y' {_rng(f.y_mean for f in fs)}"]
        faces = [f.face for f in fs if f.face is not None]
        if faces:
            parts.append(f"face Y' {_rng(f.y_mean for f in faces)} a* {_rng((f.a for f in faces), signed=True)} "
                         f"b* {_rng((f.b for f in faces), signed=True)}" + ("" if len(faces) == len(fs) else
                                                                           f" ({len(faces)}/{len(fs)} frames)"))
        else:
            parts.append("no face")
        parts.append(f"clipped ≤{max(f.clipped_pct for f in fs):.1f}%, crushed ≤{max(f.crushed_pct for f in fs):.1f}%")
        return f"Colour ({len(fs)} frame{'s' if len(fs) != 1 else ''} {span}): " + "; ".join(parts)


def _rng(values: Any, *, signed: bool = False) -> str:
    vs = list(values)
    lo, hi = min(vs), max(vs)
    fmt = "{:+.1f}" if signed else "{:.0f}"
    a, b = fmt.format(lo), fmt.format(hi)
    return a if a == b else f"{a}..{b}"


def rgb_to_lab(rgb: Any) -> Any:
    """sRGB (``Nx3`` in 0..1, BT.709 primaries) → CIELAB (D65), ``Nx3``."""
    c = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[:, 1] - 16
    a = 500 * (f[:, 0] - f[:, 1])
    b = 200 * (f[:, 1] - f[:, 2])
    return np.stack([L, a, b], axis=1)


def default_items(doc: CutDocument | None, index: TakeIndex, n: int = DEFAULT_SAMPLES) -> list[str]:
    """``n`` spoken word IDs spread evenly over the kept story (output order), or over the whole take when the
    document has no story yet. Fewer when the take is shorter than that; ``[]`` for a take without words."""
    kept = doc.kept_word_ids(index) if doc is not None and doc.segments else [w.id for w in index.words]
    pool = [w for w in kept if index.word(w).is_spoken]
    if n <= 0:
        return []
    if len(pool) <= n:
        return pool
    if n == 1:
        return [pool[len(pool) // 2]]
    step = (len(pool) - 1) / (n - 1)
    return [pool[round(k * step)] for k in range(n)]


def measure_color(job: Job, index: TakeIndex, doc: CutDocument | None, items: Sequence[str] | None = None,
                  ) -> ColorMeasurement:
    """Measure the mezzanine at the midpoint of each word in ``items`` (``w0012`` or ``w0012:note``; at most
    :data:`MAX_ITEMS`), or at :func:`default_items` when none are given.

    Raises ``FileNotFoundError`` when the job has no mezzanine (nor original) video, ``KeyError`` for an unknown
    word ID and ``ValueError`` when there is nothing to measure (a take without words).
    """
    from studio.perception import frames as fr

    refs = [str(x).strip().split(":")[0] for x in (items or []) if str(x).strip()][:MAX_ITEMS]
    if not refs:
        refs = default_items(doc, index)
    if not refs:
        raise ValueError("no words to measure: the take has no spoken words")
    src = fr.resolve_source(job, "mezz")
    words = [index.word(r) for r in refs]
    times = [(w.start_us + w.end_us) // 2 for w in words]
    imgs = fr.grab_frames(src, times, width=MEASURE_WIDTH)
    out: list[FrameColor] = []
    for w, t, raw in zip(words, times, imgs, strict=True):
        img = raw.astype(np.float32) / 255.0
        y = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
        face: FaceColor | None = None
        box = index.face_at(t)
        if box is not None:
            h, wd = y.shape
            x0, y0 = int(max(0, (box.cx - box.w / 2) * wd)), int(max(0, (box.cy - box.h / 2) * h))
            x1, y1 = int(min(wd, (box.cx + box.w / 2) * wd)), int(min(h, (box.cy + box.h / 2) * h))
            if x1 > x0 + 2 and y1 > y0 + 2:
                lab = rgb_to_lab(img[y0:y1, x0:x1].reshape(-1, 3))
                face = FaceColor(y_mean=float(y[y0:y1, x0:x1].mean()) * 255, a=float(lab[:, 1].mean()),
                                 b=float(lab[:, 2].mean()))
        out.append(FrameColor(word_id=w.id, t_us=t, y_mean=float(y.mean()) * 255,
                              clipped_pct=float((y >= CLIPPED_Y / 255).mean()) * 100,
                              crushed_pct=float((y <= CRUSHED_Y / 255).mean()) * 100, face=face))
    return ColorMeasurement(source=src, frames=out)


def format_color_measurement(m: ColorMeasurement) -> str:
    """The Director's ``measure_color`` tool text: a header with the scales, one line per word, the reading note."""
    lines = ["Measured on the mezzanine (BT.709 Y' 0-255; a*/b* CIELAB of the face: + b* warmer/yellow, + a* "
             f"magenta; clipped = Y' ≥ {CLIPPED_Y}, crushed = Y' ≤ {CRUSHED_Y}):"]
    for f in m.frames:
        face = (f"face Y' {f.face.y_mean:.0f}, a* {f.face.a:+.1f}, b* {f.face.b:+.1f}" if f.face is not None
                else "no face")
        lines.append(f"  {f.word_id}: frame Y' mean {f.y_mean:.0f}; {face}; clipped {f.clipped_pct:.1f}%, "
                     f"crushed {f.crushed_pct:.1f}%")
    lines.append("Skin usually reads natural with face Y' ≈ 150-190 (SDR) and a small positive a*/b*; judge "
                 "differences between takes, not absolute numbers.")
    return "\n".join(lines)
