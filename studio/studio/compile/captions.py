"""Caption paging, emphasis, timing, face-aware placement and SRT (ARCHITECTURE §6 stage 2, invariant 7).

Doctrine: ``skills/editing/captions-and-text.md`` and ``platforms.md``. Craft numbers are priors read from
``skills/editing/constants.yaml`` when present (see :func:`load_caption_params`), with the doctrine's
published values as defaults.

Pipeline (:func:`build_caption_pages`)
--------------------------------------
1. **Tokens.** Kept words in output order with their output spans (``timeline.word_map``; falls back to
   the segment mapping). Clean verbatim: fillers and audio events stay in the audio but are dropped
   from the page text; every lexical word is kept, nothing is paraphrased.
2. **Paging** (unless the document's :class:`CaptionPlan` lists pages). A dynamic program over each run
   between hard breaks (sentence ends, long pauses, the start of a pinned payoff so setup and punchline
   never share a page) chooses page boundaries minimizing a cost made of: page size (3 words ideal, 2-4
   normal, 1 only for punch words), estimated line width against the safe caption width, breaking
   inside syntactic units (article+noun, auxiliary+verb, pronoun+verb, first+last name, number+unit),
   spanning a comma/pause/seam, very short pages and reading speed above ~20 CPS (fast runs may use
   one more word per page instead of flickering). A DP beats greedy chunking because it can trade a
   slightly longer page now for a clean break later.
3. **Emphasis.** At most one accent per page, on at most ~25% of pages, at least ~2 s apart. Candidates
   come from meaning (numbers, negations/contrast words, the payoff) and must be supported by measured
   prosody (a z-score >= +1.5 or the index's emphasis score); words that already carry another device
   (SFX, card, punch-in, callout) are skipped: one device per beat.
4. **Timing** on the output frame grid: page-in leads the measured onset by ~90 ms (never lags, never
   more than 125 ms); 2-frame gap between pages; gaps under 0.5 s are closed; 0.4 s hold after a run
   (0.3 s before a dramatic pause); pages at least 0.5 s; a page change a few frames after a visible
   seam moves onto the seam (and the previous page ends exactly on the cut) so the eye sees one change.
5. **Placement** (:func:`place_captions`): the caption block's top edge sits 40-120 px (prior 80) below
   the chin, measured on the face track *after* the framing transform of the segment on screen, with
   hysteresis (the anchor only moves on a collision with the brows-to-lips region, another overlay or
   the safe band, or when the chin gap leaves the 40-120 px window); when it must move it first returns
   to an earlier height, so a video uses as few caption heights as possible. Fallbacks in doctrine
   order: a larger gap below the chin, smaller text (never under the 64 px legibility floor), the relaxed
   lower floor (y 1436); the matte step (text behind the subject) is not available, so next comes the
   band above the head, clear of the *measured* hair (``Visual.head_top_ratio``; the landmark box stops
   at the upper forehead), at one height per framing section; then the least-bad position. Close-up
   selfies whose chin sits under the platform band (the first real TikTok edit) land above the head. An
   anchored block keeps its place and size while the head rises into it (``on_hair``: never onto the
   forehead, eyes or mouth). Everything is clamped to the platform safe zone (the strictest zone over all
   deliverable platforms). Full-frame and split inserts are page boundaries, so a page never straddles
   two layouts.
6. **Size.** :func:`caption_fit` mirrors the renderer's fitter: one line at full size, a shrink down to the
   floor (a multi-line style only shrinks ~5 % before wrapping, so pages keep one size), then a wrap.
   The pager uses it, the placer places the block it describes, and the renderer is told the planned
   line count, so the drawn block is the placed block.

Geometry: face boxes are mapped to the output frame with the compiler's shared
``studio.compile.timeline.map_source_point`` (framing keys, punch-ins and split-screen re-layouts, the same
math the A-roll renderer uses). :func:`crop_rect` is a local equivalent used only if that is unavailable:
the base framing is the largest 9:16 crop of the upright source, a key of scale ``s`` crops ``1/s`` of it
around ``(cx, cy)`` (normalized source coordinates), clamped inside the source frame.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from fractions import Fraction
from functools import lru_cache
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from studio.compile.models import (
    FramingKey,
    Timeline,
    TimelineCaptionPage,
    TimelineCaptionWord,
    TimelineInsert,
)
from studio.doc.model import CaptionPage, CaptionPlan, CaptionStyle, CardSpec, CutDocument, format_id
from studio.timebase import frame_index, frame_time, normalize_fps, to_fraction

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings
    from studio.perception.index import FaceBox, TakeIndex, Word

__all__ = [
    "SafeZone", "PLATFORM_SAFE_ZONES", "CaptionParams", "load_caption_params", "load_constants",
    "safe_zone_for", "estimate_text_width", "auto_caption_plan", "build_caption_pages", "place_captions",
    "write_srt", "srt_text", "caption_placement_issues", "crop_rect", "source_to_output",
    "caption_block_height", "FONT_WIDTH_EM", "CaptionFit", "caption_fit", "line_break_cost", "under_chin_reframe",
    "caption_geometry", "caption_geometry_text", "placement_options", "cap_height_px", "CAP_HEIGHT_EM",
]

REF_W = 1080
REF_H = 1920


# ============================================================================================== safe zones
@dataclass(frozen=True)
class SafeZone:
    """Text safe zone as margins (px at the reference 1080x1920) kept free of text, from each edge.
    ``caption_floor`` is the relaxed bottom margin captions may drop to (organic posts, short
    descriptions) when the strict band cannot hold them below the chin."""

    top: float
    bottom: float
    left: float
    right: float
    caption_floor: float = 484.0

    def scaled(self, width: int, height: int) -> SafeZone:
        sx, sy = width / REF_W, height / REF_H
        return SafeZone(self.top * sy, self.bottom * sy, self.left * sx, self.right * sx, self.caption_floor * sy)

    def union(self, other: SafeZone) -> SafeZone:
        """The stricter zone satisfying both (max of every margin)."""
        return SafeZone(max(self.top, other.top), max(self.bottom, other.bottom), max(self.left, other.left),
                        max(self.right, other.right), max(self.caption_floor, other.caption_floor))

    def band(self, width: int, height: int) -> tuple[float, float, float, float]:
        """``(x0, y0, x1, y1)`` of the strict text band in px."""
        return self.left, self.top, width - self.right, height - self.bottom

    @property
    def relaxed_bottom(self) -> float:
        """Bottom margin for captions on the relaxed floor (never stricter than the strict band)."""
        return min(self.bottom, self.caption_floor)


#: How far (px at 1080x1920) the renderer paints past a caption block's layout box at the QA alpha threshold: the
#: stroke (``strokePx``, half of it outside the glyph) and the block's drop shadows (CaptionPage.tsx: ``0 4px 14px``
#: and ``0 1px 2px``); 6 px measured on a real edit. A block planned flush on the relaxed floor bleeds into the
#: platform UI, so the placer keeps this clear of the floor and the QA gate tolerates this much over any band edge
#: (past the strict band the bleed lands in the caption-allowed zone).
RENDER_BLEED_PX = 8.0

#: Published/third-party safe zones at 1080x1920 (platforms.md). TikTok uses the conservative end of the
#: third-party readings; Reels the official Meta 14/35/6 % ad spec; Shorts Google's vertical overlay.
#: "all" is the cross-platform house band x 65-888, y 288-1248 (the union of the three).
PLATFORM_SAFE_ZONES: dict[str, SafeZone] = {
    # TikTok (platforms.md, third-party maps): the bottom UI runs ~250-480 px "with format and caption length". The
    # strict band takes the conservative end (a long description). Captions may drop to y 1600 (bottom 320, inside
    # that range [X]): the username line of an organic post with a short description starts about there on current
    # phones. It is the TikTok counterpart of the house band's relaxed organic floor (y 1436).
    "tiktok": SafeZone(top=200, bottom=480, left=60, right=180, caption_floor=320),
    "reels": SafeZone(top=269, bottom=672, left=65, right=65, caption_floor=484),
    "shorts": SafeZone(top=288, bottom=672, left=48, right=192, caption_floor=484),
    "all": SafeZone(top=288, bottom=672, left=65, right=192, caption_floor=484),
}


# ============================================================================================== params
@dataclass(frozen=True)
class CaptionParams:
    """Caption craft priors (seconds / px at 1080x1920). Overridable from constants.yaml."""

    lead_s: float = 0.09  # page-in before the measured onset (70-100 ms)
    max_lead_s: float = 0.125  # text ahead of the voice becomes detectable beyond this
    gap_s: float = 0.067  # blank between pages (2 frames at 30 fps)
    close_gap_s: float = 0.5  # gaps shorter than this are closed
    min_page_s: float = 0.5
    punch_page_s: float = 0.25
    hold_s: float = 0.4  # after the last word of a run
    dramatic_pause_s: float = 1.0  # a pause at least this long reads as deliberate
    dramatic_hold_s: float = 0.3
    seam_snap_s: float = 0.14  # ~4 frames at 30 fps
    strong_gap_ms: float = 300.0  # break at pauses this long
    mild_gap_ms: float = 150.0
    hard_gap_ms: float = 700.0  # never page across a pause this long
    max_cps: float = 20.0
    max_words: int = 4
    accent_page_share: float = 0.25
    accent_min_spacing_s: float = 2.0
    accent_prosody_z: float = 1.5
    accent_emphasis_min: float = 0.6
    accent_score_min: float = 0.7
    chin_gap_min_px: float = 40.0
    chin_gap_px: float = 80.0
    chin_gap_max_px: float = 120.0
    default_center_y_px: float = 1100.0  # no face on screen
    max_line_px: float = 690.0  # widest caption line
    min_font_scale: float = 0.72  # the renderer may shrink a page this far before wrapping
    # legibility floor: a phrase page never renders smaller than this (px at 1080 wide; doctrine 64-96)
    min_size_px: float = 64.0
    overflow_lines: int = 2  # a page too wide for one line at the floor wraps to this many lines
    line_height: float = 1.12
    face_sample_s: float = 0.1
    card_reserve_px: float = 230.0  # caption band kept free at the bottom of card content
    # eyes-to-mouth region as fractions of the face box (MediaPipe landmark box: upper forehead to chin).
    # Measured on real takes: brows 0.10-0.15, eyes 0.24-0.30, lower lip 0.71-0.77, mouth bottom 0.79-0.84.
    protect_top_frac: float = 0.08
    protect_bottom_frac: float = 0.95
    protect_side_frac: float = 0.08
    obstacle_margin_px: float = 24.0  # breathing room around other overlays
    # above the head: the head top (hair included) is the landmark box top minus a per-take ratio of its
    # height (measured by perception; this prior when unmeasured), and captions keep this gap above it
    head_top_prior: float = 0.55
    crown_gap_px: float = 24.0
    head_overlap_tol_frac: float = 0.05  # a block may graze the top of the hair by this share of the face

    def with_overrides(self, **kw: Any) -> CaptionParams:
        known = {f.name for f in fields(self)}
        return replace(self, **{k: v for k, v in kw.items() if k in known})


_DEFAULT_PARAMS = CaptionParams()


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(_flatten(v, key))
    else:
        out[prefix] = obj
    return out


@lru_cache(maxsize=8)
def _load_yaml(path: str, mtime: float) -> dict[str, Any]:
    import yaml

    del mtime  # cache key only
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {}


def load_constants(settings: Settings | None = None) -> dict[str, Any]:
    """``skills/editing/constants.yaml`` as a dict ({} when absent or unreadable)."""
    if settings is None:
        from studio.config import get_settings

        settings = get_settings()
    p = Path(settings.skills_dir) / "constants.yaml"
    try:
        return _load_yaml(str(p), p.stat().st_mtime)
    except (OSError, ValueError, ImportError):
        return {}
    except Exception:  # malformed yaml: fall back to defaults rather than failing a render
        return {}


def _find(flat: dict[str, Any], *names: str) -> Any:
    """First value whose dotted key ends with one of ``names`` (most specific name first)."""
    for n in names:
        for k, v in flat.items():
            if (k == n or k.endswith("." + n)) and isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
    return None


# constants.yaml key candidates per param (seconds params also accept *_ms keys)
_PARAM_KEYS: dict[str, tuple[str, ...]] = {
    "lead_s": ("captions.lead_s", "caption_lead_s", "captions.lead_ms", "caption_lead_ms"),
    "max_lead_s": ("captions.max_lead_s", "captions.max_lead_ms", "caption_max_lead_ms"),
    "gap_s": ("captions.gap_s", "captions.page_gap_s", "captions.gap_ms", "captions.page_gap_ms"),
    "close_gap_s": ("captions.close_gap_s", "captions.close_gap_ms"),
    "min_page_s": ("captions.min_page_s", "captions.min_page_ms", "caption_min_page_s"),
    "punch_page_s": ("captions.punch_page_s", "captions.punch_page_ms"),
    "hold_s": ("captions.hold_s", "captions.end_hold_s", "captions.hold_ms", "captions.end_hold_ms"),
    "strong_gap_ms": ("captions.break_gap_ms", "captions.strong_gap_ms"),
    "max_cps": ("captions.max_cps", "caption_max_cps", "reading.max_cps"),
    "max_words": ("captions.max_words", "captions.max_words_per_page"),
    "accent_page_share": ("captions.accent_page_share", "captions.max_accent_share"),
    "accent_prosody_z": ("captions.accent_z", "captions.accent_prosody_z", "emphasis_z"),
    "chin_gap_min_px": ("captions.chin_gap_min_px",),
    "chin_gap_px": ("captions.chin_gap_px", "captions.below_chin_px"),
    "chin_gap_max_px": ("captions.chin_gap_max_px",),
    "max_line_px": ("captions.max_line_px", "captions.max_width_px"),
}


def _find_range(flat: dict[str, Any], *names: str) -> tuple[float, float] | None:
    """A ``[min, max]`` pair (constants.yaml writes ranges as two-item lists)."""
    for n in names:
        for k, v in flat.items():
            if (k == n or k.endswith("." + n)) and isinstance(v, (list, tuple)) and len(v) == 2 \
                    and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
                return float(v[0]), float(v[1])
    return None


def load_caption_params(settings: Settings | None = None, constants: dict[str, Any] | None = None) -> CaptionParams:
    """Defaults overridden by whatever matching keys ``constants.yaml`` provides."""
    flat = _flatten(constants if constants is not None else load_constants(settings))
    over: dict[str, Any] = {}
    # geometry priors written as ranges in constants.yaml (captions-and-text.md)
    if (r := _find_range(flat, "captions.below_chin_px")) is not None:
        over.update(chin_gap_min_px=r[0], chin_gap_max_px=r[1], chin_gap_px=(r[0] + r[1]) / 2)
    if (r := _find_range(flat, "captions.size_px_phrase")) is not None:
        over["min_size_px"] = r[0]
    if (v := _find(flat, "captions.line_width_max_px")) is not None:
        over["max_line_px"] = float(v)
    for name, keys in _PARAM_KEYS.items():
        for k in keys:
            v = _find(flat, k)
            if v is None:
                continue
            if name.endswith("_s") and k.endswith("_ms"):
                v = float(v) / 1000.0
            over[name] = int(v) if name == "max_words" else float(v)
            break
    return CaptionParams().with_overrides(**over)


def _zone_from_constants(flat: dict[str, Any], platform: str) -> SafeZone | None:
    vals = {}
    for side in ("top", "bottom", "left", "right"):
        v = None
        for prefix in (f"safe_zones.{platform}", f"safe_zone.{platform}", f"platforms.{platform}.safe_zone",
                       f"{platform}.safe_zone"):
            v = _find(flat, f"{prefix}.{side}", f"{prefix}.{side}_px")
            if v is not None:
                break
        if v is None:
            return None
        vals[side] = float(v)
    base = PLATFORM_SAFE_ZONES.get(platform, PLATFORM_SAFE_ZONES["all"])
    floor = _find(flat, "captions.relaxed_floor_y", "captions.caption_floor_y")
    cf = (REF_H - float(floor)) if floor is not None else base.caption_floor
    return SafeZone(caption_floor=max(cf, 0.0), **vals)


def safe_zone_for(platforms: str | Iterable[str] | None = None, *, width: int = REF_W, height: int = REF_H,
                  constants: dict[str, Any] | None = None) -> SafeZone:
    """Safe zone (px at ``width`` x ``height``) satisfying every platform in ``platforms``.

    ``None``/``"all"`` → the cross-platform house band. Values from ``constants.yaml`` win over the
    built-in table when present.
    """
    flat = _flatten(constants) if constants else {}
    names = ["all"] if platforms is None else ([platforms] if isinstance(platforms, str) else list(platforms))
    names = [n for n in names if n] or ["all"]
    zone: SafeZone | None = None
    for n in names:
        z = (_zone_from_constants(flat, n) if flat else None) or PLATFORM_SAFE_ZONES.get(n) \
            or PLATFORM_SAFE_ZONES["all"]
        zone = z if zone is None else zone.union(z)
    assert zone is not None
    return zone.scaled(width, height)


# ============================================================================================== text metrics
#: Average advance (em) per character class at weight ~800: (lowercase, uppercase, digit, space).
#: Only used to *plan* pages; the renderer measures the real glyphs and fits exactly. Calibrated against
#: Remotion renders of the vendored Montserrat: measured ink runs ~1.5 % wider at 800 and ~4 % at 900,
#: hence the weight factor in :func:`estimate_text_width`.
FONT_WIDTH_EM: dict[str, tuple[float, float, float, float]] = {
    "montserrat": (0.64, 0.78, 0.68, 0.28),
    "inter": (0.57, 0.71, 0.62, 0.26),
    "anton": (0.42, 0.47, 0.47, 0.2),
    "tiktok sans": (0.56, 0.69, 0.6, 0.25),
    "tiktoksans": (0.56, 0.69, 0.6, 0.25),
    "archivo": (0.57, 0.7, 0.6, 0.25),
}


def estimate_text_width(text: str, size_px: float, font: str = "Montserrat", *, stroke_px: float = 0.0,
                        case: str = "as_is", weight: int = 800) -> float:
    """Rough rendered width in px (heavy weights), including an outer stroke on both sides."""
    lo, up, dg, sp = FONT_WIDTH_EM.get(font.strip().lower(), FONT_WIDTH_EM["montserrat"])
    t = _apply_case(text, case)
    em = 0.0
    for ch in t:
        if ch.isspace():
            em += sp
        elif ch.isdigit():
            em += dg
        elif ch.isupper():
            em += up
        elif ch.isalpha():
            em += lo * (0.45 if ch in "iljtf" else 1.35 if ch in "mw" else 1.0)
        else:
            em += 0.34
    wf = 1.015 + 0.023 * (min(max(weight, 400), 1000) - 800) / 100.0
    return em * size_px * wf + 2 * stroke_px


def _apply_case(text: str, case: str) -> str:
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "title":
        return " ".join(w[:1].upper() + w[1:] for w in text.split(" "))
    return text


def caption_block_height(style: CaptionStyle, lines: int = 1, *, scale: float = 1.0,
                         line_height: float = 1.12) -> float:
    """Height in px of a caption block (lines + stroke + box padding) — mirrors CaptionPage.tsx."""
    size = style.size_px * scale
    stroke = 0.0 if style.background else style.stroke_px * scale
    pad = size * 0.14 if style.background else 0.0
    return lines * size * line_height + 2 * pad + stroke


#: A style that allows more lines wraps rather than shrinking a page more than this (a size jump between
#: consecutive pages reads as a mistake); mirrored in overlay/src/components/CaptionPage.tsx.
WRAP_BEFORE_SHRINK = 0.95


@dataclass(frozen=True)
class CaptionFit:
    """Estimated rendered layout of one caption page. The line breaks are chosen here (syntax-aware) and handed
    to the renderer (``breaks`` in the overlay props), so the drawn lines are the planned ones."""

    width: float  # block width incl. stroke/box padding, output px
    height: float  # block height, output px
    size_px: float  # rendered font size, output px
    lines: int
    shrink: float  # rendered size / requested size (1.0 = no shrink)
    breaks: tuple[int, ...] = ()  # word index where each line after the first starts
    line_texts: tuple[str, ...] = ()  # the lines as displayed (case applied)

    @property
    def display(self) -> str:
        """``"survive / the marriage?"`` — the page as it will be drawn."""
        return " / ".join(self.line_texts)


#: joins a line break should not split, from the doctrine's anti-patterns ("the / budget"): cost of starting a new
#: line at ``words[i]``. Page breaks use :func:`_break_cost` (which also knows the timing).
def line_break_cost(words: Sequence[str], i: int) -> float:
    """Cost of a line break before ``words[i]`` (0 = natural: after a comma or a sentence end)."""
    if i <= 0 or i >= len(words):
        return 0.0
    a, b = words[i - 1], words[i]
    if _SENT_END.search(a) or _STRONG_PUNCT.search(a):
        return 0.0
    pa, pb = _norm(a), _norm(b)
    prev = _norm(words[i - 2]) if i >= 2 else ""
    if pa in _DETERMINERS:
        return 3.0  # "the | budget", "three | questions": never inside a noun phrase
    if pa in _PREPOSITIONS:
        return 2.8  # "through | three questions" is bad, but better than splitting the noun phrase itself
    if _is_number(a) and (pb in _UNITS or _is_number(b)):
        return 2.5  # "sixty | dollars"
    if _is_name_part(a) and _is_name_part(b) and i >= 2:
        return 2.5  # "Maya | Chen" (a capitalised first word is just the sentence start)
    if pa in _AUX:
        return 1.0 if (pb in _DETERMINERS or pb in _PREPOSITIONS) else 2.5  # "is | an addition" ok, "is | going"
    if pa in _SUBJECTS and pb not in _CONJ and pb not in _PREPOSITIONS:
        return 2.5  # "then you | need"
    if pa in _CONJ:
        return 2.0  # "and | the third"
    content_a = pa not in _STOP and not _is_number(a)
    content_b = pb not in _STOP
    if content_a and content_b:
        if prev in _SUBJECTS or prev in _AUX:
            return 0.6  # after a verb, before its object: "If I put | butter chicken"
        closes = (i == len(words) - 1 or bool(_SENT_END.search(b) or _STRONG_PUNCT.search(b))
                  or _norm(words[i + 1]) in _STOP)
        return 1.8 if closes else 1.2  # a modifier + noun closing its phrase: "fusion | experiment."
    if pb in _CONJ or pb in _PREPOSITIONS:
        return 0.4  # before the function word that opens the next phrase
    return 0.9


def _balanced_lines(words: Sequence[str], k: int, width: Any, avail: float | None = None) -> list[list[str]]:
    """Split ``words`` into ``k`` lines. With ``avail`` (the line width that fits at the planned size): among
    splits that fit, the syntactically cleanest (:func:`line_break_cost`) and then the most balanced; when none
    fits, the narrowest widest line with a syntax penalty. Without ``avail``: minimize the widest line."""
    n = len(words)
    if k <= 1 or n <= 1:
        return [list(words)]
    k = min(k, n)
    if avail is not None and n <= 14:
        from itertools import combinations

        best_key: tuple[float, ...] | None = None
        best_split: tuple[int, ...] = ()
        for cut in combinations(range(1, n), k - 1):
            bounds = (0, *cut, n)
            ws_ = [width(words[bounds[j]:bounds[j + 1]]) for j in range(k)]
            widest, narrowest = max(ws_), min(ws_)
            syn = sum(line_break_cost(words, c) for c in cut)
            if widest <= avail:
                key = (0.0, syn + 0.8 * (widest - narrowest) / max(avail, 1.0), widest)
            else:
                key = (1.0, widest + syn * 0.08 * avail, syn)  # a mid-phrase break costs as much as ~25 % size
            if best_key is None or key < best_key:
                best_key, best_split = key, cut
        bounds = (0, *best_split, n)
        return [list(words[bounds[j]:bounds[j + 1]]) for j in range(k)]
    inf = math.inf
    best = [[inf] * (n + 1) for _ in range(k + 1)]
    prev = [[-1] * (n + 1) for _ in range(k + 1)]
    best[0][0] = 0.0
    for j in range(1, k + 1):
        for i in range(1, n + 1):
            for s_ in range(j - 1, i):
                if best[j - 1][s_] == inf:
                    continue
                c = max(best[j - 1][s_], width(words[s_:i]))
                if c < best[j][i]:
                    best[j][i], prev[j][i] = c, s_
    lines: list[list[str]] = []
    i = n
    for j in range(k, 0, -1):
        s_ = prev[j][i]
        lines.insert(0, list(words[s_:i]))
        i = s_
    return lines


def caption_fit(style: CaptionStyle, text: str, width: int = REF_W, params: CaptionParams | None = None, *,
                scale: float = 1.0, max_width: float | None = None) -> CaptionFit:
    """How the renderer lays out ``text`` in ``style`` on a ``width``-wide frame (estimate).

    Order, as in ``fitWords``: one line at full size; one line shrunk down to the legibility floor
    (``min_size_px``, and never below ``min_font_scale``); the style's extra lines; then up to
    ``overflow_lines`` lines rather than shrinking under the floor; finally whatever shrink fits.
    ``scale`` is a placer-chosen size step (the page is re-styled at ``size_px * scale``)."""
    p = params or _DEFAULT_PARAMS
    ws = width / REF_W
    size0 = style.size_px * ws * scale
    stroke = 0.0 if style.background else style.stroke_px * ws * scale
    pad_x = size0 * 0.32 if style.background else 0.0
    pad_y = size0 * 0.14 if style.background else 0.0
    extra = 2 * stroke + 2 * pad_x
    max_w = p.max_line_px * ws if max_width is None else min(max_width, p.max_line_px * ws)
    avail = max(1.0, max_w - extra)
    floor = min(size0, p.min_size_px * ws)
    min_scale = min(1.0, max(p.min_font_scale, floor / size0)) if size0 > 0 else 1.0
    words = _apply_case(text, style.case).split() or [text]

    def w_at(ws_: Sequence[str]) -> float:
        return estimate_text_width(" ".join(ws_), size0, style.font, weight=style.weight)

    max_lines = max(1, min(3, style.lines))
    most = min(len(words), max(max_lines, min(3, p.overflow_lines)))
    chosen: tuple[int, float, float, list[list[str]]] | None = None
    last: tuple[int, float, list[list[str]]] = (1, w_at(words), [list(words)])
    for k in range(1, most + 1):
        split = _balanced_lines(words, k, w_at, avail)
        widest = max(w_at(line) for line in split)
        last = (k, widest, split)
        if widest <= avail:
            chosen = (k, 1.0, widest, split)
            break
        # while the style still allows another line, only a slight shrink beats wrapping (pages of one
        # video keep one size); on the style's last line count, shrink down to the floor before overflowing
        step = min_scale if k >= max_lines else max(min_scale, WRAP_BEFORE_SHRINK)
        if avail / widest >= step:
            chosen = (k, avail / widest, widest, split)
            break
    if chosen is None:
        k, widest, split = last
        chosen = (k, min(1.0, avail / widest), widest, split)
    k, sc, widest, split = chosen
    size = size0 * sc
    breaks: list[int] = []
    pos = 0
    for line in split[:-1]:
        pos += len(line)
        breaks.append(pos)
    return CaptionFit(width=widest * sc + extra, height=k * size * p.line_height + 2 * pad_y + stroke,
                      size_px=size, lines=k, shrink=sc, breaks=tuple(breaks),
                      line_texts=tuple(" ".join(line) for line in split))


# ============================================================================================== lexicon
_DETERMINERS = {"a", "an", "the", "this", "that", "these", "those", "my", "your", "his", "her", "its", "our",
                "their", "some", "any", "every", "each", "no", "one", "two", "three", "few", "many", "much",
                "more", "most", "less", "least", "another", "such", "what", "which", "whose"}
_PREPOSITIONS = {"of", "to", "in", "on", "at", "for", "with", "from", "by", "about", "into", "onto", "over",
                 "under", "than", "like", "as", "per", "without", "through", "between", "after", "before",
                 "during", "since", "until", "via", "off", "out", "up", "down"}
_AUX = {"is", "are", "was", "were", "be", "been", "being", "am", "have", "has", "had", "do", "does", "did",
        "will", "would", "can", "could", "should", "shall", "may", "might", "must", "gonna", "wanna",
        "gotta", "i'm", "you're", "we're", "they're", "it's", "that's", "he's", "she's", "i've", "you've",
        "we've", "they've", "i'll", "you'll", "we'll", "they'll", "i'd", "you'd", "don't", "doesn't",
        "didn't", "can't", "won't", "isn't", "aren't", "wasn't", "weren't", "not", "to"}
_SUBJECTS = {"i", "you", "we", "they", "he", "she", "it", "people", "someone", "everyone", "nobody"}
_CONJ = {"and", "but", "or", "so", "because", "cause", "'cause", "if", "when", "while", "then", "that",
         "which", "who", "where", "though", "although", "unless", "until", "since", "yet"}
_STOP = _DETERMINERS | _PREPOSITIONS | _AUX | _SUBJECTS | _CONJ | {"me", "him", "them", "us", "just", "really",
                                                                  "very", "there", "here", "okay", "ok",
                                                                  "yeah", "well", "right", "also", "too"}
_KEYWORDS = {"not", "never", "no", "nothing", "none", "nobody", "stop", "don't", "can't", "won't", "only",
             "real", "secret", "wrong", "mistake", "best", "worst", "free", "every", "always", "instead",
             "actually", "biggest", "fastest", "cheapest", "exactly", "zero", "double", "half", "twice",
             "massive", "huge", "insane", "first", "last", "one", "without"}
_NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                 "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
                 "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
                 "hundred", "thousand", "million", "billion", "trillion", "percent", "half", "double", "twice"}
_UNITS = {"percent", "%", "dollars", "dollar", "bucks", "k", "grand", "million", "billion", "thousand",
          "hundred", "hours", "hour", "minutes", "minute", "seconds", "second", "days", "day", "weeks", "week",
          "months", "month", "years", "year", "times", "x", "pounds", "kg", "lbs", "miles", "km", "people",
          "followers", "views"}
_SENT_END = re.compile(r"[.!?…]+[\"')\]]*$")
_STRONG_PUNCT = re.compile(r"[,;:—–]+[\"')\]]*$|\s-$")
_NUM_RE = re.compile(r"^[$€£]?\d[\d,.]*[%kKmMxX]?$")


def _norm(text: str) -> str:
    return re.sub(r"[^\w'%$€£]+", "", text.lower().replace("’", "'"))


def _is_number(text: str) -> bool:
    n = _norm(text)
    return bool(_NUM_RE.match(n)) or n in _NUMBER_WORDS


def _is_name_part(text: str) -> bool:
    t = text.strip("\"'([{")
    return len(t) > 1 and t[0].isupper() and t[1:].islower() and t.lower() not in _STOP


# ============================================================================================== tokens
@dataclass
class _Tok:
    wid: str
    text: str  # clean display text (no case transform)
    start: Fraction  # output seconds
    end: Fraction
    word: Word
    seg: int  # index of the segment in output order
    payoff: bool = False
    gap_after: Fraction | None = None  # output silence to the next displayed token
    seam_after: bool = False
    hard_after: bool = False
    strong_after: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def dur(self) -> Fraction:
        return self.end - self.start


def _display_word(w: Word) -> str | None:
    """Clean-verbatim page text for a word; None when it is not shown (fillers, audio events)."""
    if w.kind in ("filler", "event"):
        return None
    t = w.text.strip()
    return t or None


def _segment_word_spans(timeline: Timeline, index: TakeIndex) -> dict[str, tuple[Fraction, Fraction, int]]:
    """Output span of every word listed in the timeline's segments: ``word_map`` when present, else
    mapped through the segment (speed-aware, clamped to the segment)."""
    out: dict[str, tuple[Fraction, Fraction, int]] = {}
    for k, seg in enumerate(timeline.segments):
        wids = seg.word_ids or [w.id for w in index.words_between_us(seg.src_in_us, seg.src_out_us)]
        for wid in wids:
            span = timeline.word_map.get(wid)
            if span is not None:
                out[wid] = (to_fraction(span.out_start), to_fraction(span.out_end), k)
                continue
            if wid in timeline.word_map:  # explicitly None: not visible in the cut
                continue
            if not index.has_word(wid):
                continue
            w = index.word(wid)
            sp = to_fraction(seg.speed)
            a = seg.out_start + Fraction(max(w.start_us, seg.src_in_us) - seg.src_in_us, 1_000_000) / sp
            b = seg.out_start + Fraction(min(w.end_us, seg.src_out_us) - seg.src_in_us, 1_000_000) / sp
            a = min(max(a, seg.out_start), seg.out_end)
            b = min(max(b, a), seg.out_end)
            out[wid] = (a, b, k)
    # word_map entries for words not listed in segment word_ids
    for wid, span in timeline.word_map.items():
        if span is None or wid in out:
            continue
        t = to_fraction(span.out_start)
        seg_k = next((k for k, s in enumerate(timeline.segments) if s.out_start <= t < s.out_end), 0)
        out[wid] = (t, to_fraction(span.out_end), seg_k)
    return out


def _estimated_spans(doc: CutDocument, index: TakeIndex) -> dict[str, tuple[Fraction, Fraction, int]]:
    """Planning-time output spans (no timeline yet): segments concatenated, gap overrides and speed
    applied. The compiler is authoritative; this only drives page sizing."""
    out: dict[str, tuple[Fraction, Fraction, int]] = {}
    cursor = Fraction(0)
    for k, s in enumerate(doc.segments):
        ws = index.get_words(s.from_word, s.to_word)
        if not ws:
            continue
        sp = to_fraction(s.speed)
        base = ws[0].start_us
        shrink = 0
        for i, w in enumerate(ws):
            if i > 0:
                g = index.gap_before(w.id)
                if g is not None and g.id in s.gap_overrides:
                    shrink += max(0, g.duration_us - s.gap_overrides[g.id] * 1000)
            a = cursor + Fraction(w.start_us - base - shrink, 1_000_000) / sp
            b = cursor + Fraction(w.end_us - base - shrink, 1_000_000) / sp
            out[w.id] = (a, max(a, b), k)
        cursor = max(v[1] for v in out.values()) + Fraction(1, 20)
    return out


def _insert_breaks(doc: CutDocument | None) -> tuple[set[str], set[str]]:
    """Words a page must start at / end at because a full-frame or split insert cuts in or out there
    (a page change then coincides with the picture change, and placement never straddles two layouts)."""
    before: set[str] = set()
    after: set[str] = set()
    if doc is None:
        return before, after
    for ins in doc.inserts:
        if ins.mode in ("full", "card", "split_top", "split_bottom"):
            before.add(ins.anchor_from_word)
            after.add(ins.anchor_to_word)
    return before, after


def _build_tokens(word_ids: Sequence[str], spans: dict[str, tuple[Fraction, Fraction, int]], index: TakeIndex,
                  params: CaptionParams, payoff_ids: set[str],
                  breaks: tuple[set[str], set[str]] = (set(), set())) -> list[_Tok]:
    toks: list[_Tok] = []
    for wid in word_ids:
        if wid not in spans or not index.has_word(wid):
            continue
        w = index.word(wid)
        text = _display_word(w)
        if text is None:
            continue
        a, b, k = spans[wid]
        toks.append(_Tok(wid=wid, text=text, start=a, end=b, word=w, seg=k, payoff=wid in payoff_ids))
    toks.sort(key=lambda t: (t.start, index.word_pos(t.wid)))
    for i, t in enumerate(toks[:-1]):
        nxt = toks[i + 1]
        t.gap_after = max(Fraction(0), nxt.start - t.end)
        t.seam_after = nxt.seg != t.seg
        gap_ms = float(t.gap_after) * 1000.0
        sent_break = bool(_SENT_END.search(t.text)) or (
            t.word.sentence_id is not None and nxt.word.sentence_id is not None
            and t.word.sentence_id != nxt.word.sentence_id)
        layout_change = nxt.wid in breaks[0] or t.wid in breaks[1]
        t.seam_after = t.seam_after or layout_change
        t.hard_after = sent_break or gap_ms >= params.hard_gap_ms or (nxt.payoff and not t.payoff) or layout_change
        t.strong_after = t.hard_after or t.seam_after or gap_ms >= params.strong_gap_ms \
            or bool(_STRONG_PUNCT.search(t.text))
    if toks:
        toks[-1].hard_after = toks[-1].strong_after = True
    return toks


# ============================================================================================== paging (DP)
def _break_cost(a: _Tok, b: _Tok, params: CaptionParams) -> float:
    """Cost of ending a page between ``a`` and ``b`` (0 = natural break)."""
    if a.strong_after:
        return 0.0
    pa, pb = _norm(a.text), _norm(b.text)
    gap_ms = float(a.gap_after or 0) * 1000.0
    if pa in _DETERMINERS or pa in _PREPOSITIONS:
        return 3.5  # "the | budget", "of | the", "before your | next"
    if pa in _AUX or (pa in _SUBJECTS and pb not in _CONJ and pb not in _PREPOSITIONS):
        return 2.5  # "is | going", "I | spent"
    if pa in _CONJ:
        return 2.0  # a conjunction opens the next phrase: "and | cut"
    if _is_name_part(a.text) and _is_name_part(b.text):
        return 2.5  # "Maya | Chen"
    if _is_number(a.text) and (pb in _UNITS or _is_number(b.text)):
        return 2.5  # "sixty | dollars"
    if gap_ms >= params.mild_gap_ms:
        return 0.3
    if pb in _CONJ or pb in _PREPOSITIONS:
        return 0.4  # break before a function word that opens the next phrase
    return 0.9


def _is_punch(t: _Tok) -> bool:
    return t.payoff or _is_number(t.text) or t.word.emphasis >= 0.8


def _page_cost(toks: Sequence[_Tok], j: int, i: int, *, style: CaptionStyle, params: CaptionParams,
               max_words: int, run_len: int, width_scale: float, overflow: bool = False) -> float:
    n = i - j
    if n > max_words:
        return math.inf
    page = toks[j:i]
    text = " ".join(t.text for t in page)
    # the renderer's layout (caption_fit): shrink down to the legibility floor, then wrap
    fit = caption_fit(style, text, round(REF_W * width_scale), params)
    cost = 0.0
    if n > 1 and fit.lines > style.lines:
        # too wide for the style's lines even at the floor: a wrapped page (only when the wrap holds it at full
        # size) is the doctrine's answer to flicker ("larger two-line pages rather than faster flicker"), so it
        # competes with the short pages it would merge; cheaper in a fast run. A short phrase (≤ 3 words) is
        # split instead: wrapping it grows the block (onto the hair above a head) for no reading gain.
        if fit.shrink < 0.999 or n <= 3:
            return math.inf
        cost += (0.8 if overflow else 1.6) * (fit.lines - style.lines)
    elif fit.shrink < 0.999:
        cost += 10.0 * (1.0 / max(fit.shrink, 1e-3) - 1.0)  # shrinking costs legibility: split, don't squeeze
    chars = len(text)
    line_chars = style.max_chars_per_line * max(style.lines, fit.lines)
    if chars > line_chars:
        cost += 0.35 * (chars - line_chars)
    size_cost = {1: 2.6, 2: 0.3, 3: 0.0, 4: 0.35, 5: 1.4, 6: 2.4}.get(n, 3.0 + n)
    if n == 1 and (run_len == 1 or _is_punch(page[0])):
        size_cost = 0.0 if run_len == 1 else 0.8
    cost += size_cost
    for t in page[:-1]:  # spanning natural breaks
        if t.seam_after:
            cost += 2.0
        elif t.strong_after:
            cost += 1.2
    # time on screen, not the speech span: a page shows from its first onset (minus the lead) until the next
    # page takes over (the 2-frame gap before it) or, at the end of a run, until the hold after its last word
    if i < len(toks):
        d = float(toks[i].start - page[0].start) - params.gap_s
    else:
        d = float(page[-1].end - page[0].start) + params.hold_s
    d = max(d, float(page[-1].end - page[0].start), 1e-3)
    if d < params.min_page_s and not (n == 1 and _is_punch(page[0])):
        cost += 6.0 * (params.min_page_s - d) / params.min_page_s
    cps = chars / d
    if cps > params.max_cps:
        # doctrine: <= 20 CPS; a fast page is a reason to re-page (never to delete words): a longer page (two lines
        # when the style allows) beats a flash that cannot be read
        cost += 0.45 * (cps - params.max_cps)
    return cost


def _page_run(toks: Sequence[_Tok], *, style: CaptionStyle, params: CaptionParams, width_scale: float) -> list[
        list[_Tok]]:
    n = len(toks)
    if n == 0:
        return []
    dur = float(toks[-1].end - toks[0].start)
    chars = sum(len(t.text) + 1 for t in toks)
    fast = dur > 0 and chars / dur > params.max_cps
    max_words = max(1, min(style.max_words_per_page, params.max_words)) + (1 if fast else 0)
    best = [math.inf] * (n + 1)
    back = [-1] * (n + 1)
    best[0] = 0.0
    for i in range(1, n + 1):
        for j in range(max(0, i - max_words), i):
            if best[j] == math.inf:
                continue
            c = _page_cost(toks, j, i, style=style, params=params, max_words=max_words, run_len=n,
                           width_scale=width_scale, overflow=fast)
            if c == math.inf:
                continue
            if i < n:
                c += _break_cost(toks[i - 1], toks[i], params)
            if best[j] + c < best[i]:
                best[i] = best[j] + c
                back[i] = j
    if best[n] == math.inf:  # nothing valid (e.g. very long words): one word per page
        return [[t] for t in toks]
    pages: list[list[_Tok]] = []
    i = n
    while i > 0:
        j = back[i]
        pages.append(list(toks[j:i]))
        i = j
    pages.reverse()
    return pages


def _page_tokens(toks: Sequence[_Tok], *, style: CaptionStyle, params: CaptionParams, width_scale: float) -> list[
        list[_Tok]]:
    pages: list[list[_Tok]] = []
    run: list[_Tok] = []
    for t in toks:
        run.append(t)
        if t.hard_after:
            pages.extend(_page_run(run, style=style, params=params, width_scale=width_scale))
            run = []
    if run:
        pages.extend(_page_run(run, style=style, params=params, width_scale=width_scale))
    return pages


# ============================================================================================== emphasis
def _device_words(doc: CutDocument) -> set[str]:
    """Words that already carry another device (SFX, card, callout, punch-in): no caption accent."""
    out: set[str] = {c.anchor_word for c in doc.audio.sfx}
    for ins in doc.inserts:
        if isinstance(ins.asset, CardSpec) or ins.mode == "card":
            out.add(ins.anchor_from_word)
    for t in doc.texts:
        if t.kind in ("callout", "label"):
            out.add(t.anchor_from_word)
    for s in doc.segments:
        if s.framing is not None and s.framing.scale >= 1.1:
            out.add(s.framing.anchor_word or s.from_word)
    return out


#: common verbs that carry little meaning on their own: an accent belongs on the word the line is about (the
#: noun, the negation, the number), not on "start" or "build"
_LIGHT_VERBS = {"start", "starts", "started", "build", "builds", "built", "make", "makes", "made", "get", "gets",
                "got", "go", "goes", "went", "put", "puts", "take", "takes", "took", "give", "gives", "gave", "use",
                "uses", "used", "know", "knows", "knew", "think", "thinks", "thought", "see", "sees", "saw", "want",
                "wants", "wanted", "need", "needs", "needed", "keep", "keeps", "kept", "let", "lets", "come",
                "comes", "came", "tell", "tells", "told", "say", "says", "said", "try", "tries", "tried", "look",
                "looks", "looked", "feel", "feels", "felt", "find", "finds", "found", "run", "runs", "ran", "work",
                "works", "worked", "call", "calls", "called", "show", "shows", "showed", "turn", "turns", "turned"}


def _accent_score(t: _Tok, params: CaptionParams) -> float:
    n = _norm(t.text)
    number = _is_number(t.text)
    if not number and (n in _STOP or len(n) <= 2):
        return 0.0
    if n in _LIGHT_VERBS and n not in _KEYWORDS and not t.payoff:
        return 0.0
    p = t.word.prosody
    zs = [z for z in ((p.f0_z, p.int_z, p.dur_z) if p is not None else ()) if z is not None]
    zmax = max(zs) if zs else 0.0
    supported = zmax >= params.accent_prosody_z or t.word.emphasis >= params.accent_emphasis_min \
        or (t.payoff and t.word.emphasis >= 0.4)
    if not supported:
        return 0.0
    score = t.word.emphasis + (0.1 if zmax >= params.accent_prosody_z else 0.0)
    if number:
        score += 0.25
    if n in _KEYWORDS:
        score += 0.15
    if t.payoff:
        score += 0.3
    return score


def _choose_emphasis(pages: Sequence[Sequence[_Tok]], params: CaptionParams, device_words: set[str]) -> set[str]:
    cands: list[tuple[float, int, _Tok]] = []
    for k, page in enumerate(pages):
        scored = [(_accent_score(t, params), t) for t in page if t.wid not in device_words]
        scored = [(s, t) for s, t in scored if s >= params.accent_score_min]
        if scored:
            s, t = max(scored, key=lambda x: x[0])
            cands.append((s, k, t))
    budget = max(1, math.ceil(params.accent_page_share * len(pages))) if pages else 0
    chosen: list[_Tok] = []
    for _s, _k, t in sorted(cands, key=lambda x: (-x[0], x[1])):
        if len(chosen) >= budget:
            break
        if any(abs(float(t.start - c.start)) < params.accent_min_spacing_s for c in chosen):
            continue
        chosen.append(t)
    return {t.wid for t in chosen}


# ============================================================================================== plan
def _payoff_ids(doc: CutDocument) -> set[str]:
    return set(doc.pins.payoff_word_ids)


def auto_caption_plan(doc: CutDocument, index: TakeIndex, *, style: CaptionStyle | None = None,
                      timeline: Timeline | None = None, params: CaptionParams | None = None) -> CaptionPlan:
    """Page the kept words into a :class:`CaptionPlan` (the Director applies it via ``set_captions``).

    Uses the compiled ``timeline`` spans when given, else a planning estimate from the document.
    Emphasis words are chosen from prosody + meaning (see module docstring).
    """
    params = params or load_caption_params()
    st = style or (doc.captions.style if doc.captions is not None else CaptionStyle())
    spans = _segment_word_spans(timeline, index) if timeline is not None else _estimated_spans(doc, index)
    kept = doc.kept_word_ids(index)
    toks = _build_tokens(kept, spans, index, params, _payoff_ids(doc), _insert_breaks(doc))
    width_scale = (timeline.width / REF_W) if timeline is not None else 1.0
    page_style = st
    if st.lines > 1:
        # above the head (the chin sits under the platform band) a two-line block grows down onto the hair:
        # page for one line there, the style's extra lines only catch a word too long for one
        from studio.compile.timeline import _captions_above_head

        W = timeline.width if timeline is not None else REF_W
        H = timeline.height if timeline is not None else REF_H
        with_style = doc.model_copy(update={"captions": (doc.captions or CaptionPlan()).model_copy(
            update={"style": st})})
        if _captions_above_head(with_style, index, W, H):
            page_style = st.model_copy(update={"lines": 1})
    pages = _page_tokens(toks, style=page_style, params=params, width_scale=width_scale)
    emph = _choose_emphasis(pages, params, _device_words(doc))
    out = [CaptionPage(id=format_id("p", k), word_ids=[t.wid for t in page],
                       emphasis_word_ids=[t.wid for t in page if t.wid in emph])
           for k, page in enumerate(pages, start=1)]
    position = doc.captions.position if doc.captions is not None else "auto"
    return CaptionPlan(enabled=True, pages=out, style=st, position=position)


# ============================================================================================== timing
def _visible_seams(timeline: Timeline) -> list[Fraction]:
    """Output instants where the picture visibly changes: cuts, and full-frame/split insert edges."""
    seams: set[Fraction] = {to_fraction(s) for s in timeline.seams}
    seams.update(s.out_start for s in timeline.segments[1:])
    for ins in timeline.inserts:
        if ins.mode in ("full", "card", "split_top", "split_bottom"):
            seams.add(to_fraction(ins.out_start))
            seams.add(to_fraction(ins.out_end))
    return sorted(t for t in seams if 0 < t < timeline.duration)


@dataclass
class _PageDraft:
    toks: list[_Tok]
    emphasis: set[str]
    text: str | None = None
    position: str | None = None
    y_norm: float | None = None
    page_id: str | None = None
    in_f: int = 0
    out_f: int = 0
    on_seam: bool = False


def _time_pages(drafts: list[_PageDraft], timeline: Timeline, params: CaptionParams) -> list[_PageDraft]:
    fps = normalize_fps(timeline.fps)
    total = timeline.frame_count
    gap_f = max(1, round(params.gap_s * float(fps)))
    seam_frames = sorted({frame_index(s, fps, "round") for s in _visible_seams(timeline)})
    snap_f = max(1, round(params.seam_snap_s * float(fps)))
    max_lead = Fraction(params.max_lead_s).limit_denominator(100000)
    for d in drafts:
        onset = d.toks[0].start
        onset_f = frame_index(onset, fps, "floor")
        latest_f = frame_index(onset, fps, "ceil")  # a seam may trail the onset by < 1 frame (< 45 ms)
        d.in_f = max(0, min(onset_f, frame_index(onset - Fraction(params.lead_s).limit_denominator(100000), fps)))
        # a seam just before/at the page change: move the change onto the cut (never lag, lead <= max)
        best = None
        for sf in seam_frames:
            near = d.in_f - snap_f <= sf <= latest_f and onset - frame_time(sf, fps) <= max_lead
            if near and (best is None or abs(sf - d.in_f) < abs(best - d.in_f)):
                best = sf
        if best is not None:
            d.in_f = best
            d.on_seam = True
        d.in_f = min(d.in_f, max(total - 1, 0))
    # enforce ordering (a page never starts before the previous one)
    for a, b in pairwise(drafts):
        if b.in_f <= a.in_f:
            b.in_f = min(a.in_f + 1, max(total - 1, 0))
    for k, d in enumerate(drafts):
        last_end = d.toks[-1].end
        nxt = drafts[k + 1] if k + 1 < len(drafts) else None
        pause = (nxt.toks[0].start - last_end) if nxt is not None else Fraction(10)
        hold = params.dramatic_hold_s if float(pause) >= params.dramatic_pause_s else params.hold_s
        out_f = frame_index(last_end + Fraction(hold).limit_denominator(100000), fps, "ceil")
        if nxt is not None:
            limit = nxt.in_f if nxt.on_seam else nxt.in_f - gap_f
            if limit - out_f < round(params.close_gap_s * float(fps)):
                out_f = limit  # close short gaps between pages
            out_f = min(out_f, limit)
        min_f = round((params.punch_page_s if len(d.toks) == 1 and _is_punch(d.toks[0]) else params.min_page_s)
                      * float(fps))
        if out_f - d.in_f < min_f:
            cap = (nxt.in_f if nxt.on_seam else nxt.in_f - gap_f) if nxt is not None else total
            out_f = max(out_f, min(d.in_f + min_f, cap))
        d.out_f = max(d.in_f + 1, min(out_f, total))
    # crammed pages (extreme speech rates): never overlap; a page left with no frame merges forward
    out: list[_PageDraft] = []
    for k, d in enumerate(drafts):
        nxt = drafts[k + 1] if k + 1 < len(drafts) else None
        if nxt is not None and d.out_f > nxt.in_f:
            d.out_f = nxt.in_f
        if d.out_f <= d.in_f:
            if nxt is not None:
                if d.text is not None or nxt.text is not None:
                    nxt.text = f"{d.text or _page_text(d.toks)} {nxt.text or _page_text(nxt.toks)}"
                nxt.toks = d.toks + nxt.toks
                nxt.emphasis = set(sorted(nxt.emphasis)[:1]) or set(sorted(d.emphasis)[:1])
                nxt.in_f = min(nxt.in_f, d.in_f)
            continue
        out.append(d)
    return out


# ============================================================================================== geometry
def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _framing_at(keys: Sequence[FramingKey], t: Fraction) -> tuple[float, float, float]:
    """(scale, cx, cy) at output time ``t``; the ease of a key describes the move *into* that key."""
    if not keys:
        return 1.0, 0.5, 0.5
    ks = sorted(keys, key=lambda k: k.out_t)
    if t <= ks[0].out_t:
        return ks[0].scale, ks[0].cx, ks[0].cy
    for a, b in pairwise(ks):
        if a.out_t <= t < b.out_t:
            if b.ease == "hold":
                return a.scale, a.cx, a.cy
            u = float((t - a.out_t) / (b.out_t - a.out_t)) if b.out_t > a.out_t else 1.0
            if b.ease == "ease_in_out":
                u = u * u * (3 - 2 * u)
            return _lerp(a.scale, b.scale, u), _lerp(a.cx, b.cx, u), _lerp(a.cy, b.cy, u)
    last = ks[-1]
    return last.scale, last.cx, last.cy


def crop_rect(src_w: int, src_h: int, out_w: int, out_h: int, scale: float = 1.0, cx: float = 0.5,
              cy: float = 0.5) -> tuple[float, float, float, float]:
    """Source crop ``(x0, y0, w, h)`` in source px: the largest ``out_w:out_h`` rect inside the source,
    divided by ``scale``, centred on ``(cx, cy)`` (normalized) and clamped inside the source."""
    aspect = out_w / out_h
    if src_w / src_h > aspect:
        bh, bw = float(src_h), src_h * aspect
    else:
        bw, bh = float(src_w), src_w / aspect
    s = max(1.0, scale)
    w, h = bw / s, bh / s
    x0 = min(max(cx * src_w - w / 2, 0.0), src_w - w)
    y0 = min(max(cy * src_h - h / 2, 0.0), src_h - h)
    return x0, y0, w, h


def source_to_output(pt: tuple[float, float], rect: tuple[float, float, float, float], src_w: int, src_h: int,
                     out_w: int, out_h: int) -> tuple[float, float]:
    """Map a normalized source point to output px through a crop rect."""
    x0, y0, w, h = rect
    return (pt[0] * src_w - x0) / w * out_w, (pt[1] * src_h - y0) / h * out_h


@dataclass
class _FaceOut:
    """Face in output px at one sample: landmark box (upper forehead to chin), the top of the head (hair
    included) and the eye-to-mouth protected box."""

    x0: float
    y0: float
    x1: float
    y1: float
    crown: float | None = None  # top of the head, hair included (None: estimate from the prior)

    @property
    def chin(self) -> float:
        return self.y1

    @property
    def forehead(self) -> float:
        return self.y0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def head_top(self, params: CaptionParams | None = None) -> float:
        p = params or _DEFAULT_PARAMS
        return self.crown if self.crown is not None else self.y0 - p.head_top_prior * self.h

    def head_x(self) -> tuple[float, float]:
        """Horizontal extent of the head: hair runs wider than the landmark box."""
        w = self.x1 - self.x0
        return self.x0 - 0.25 * w, self.x1 + 0.25 * w

    def protected(self, params: CaptionParams | None = None) -> tuple[float, float, float, float]:
        """Brows-to-lips region that text may never cover (fractions from :class:`CaptionParams`)."""
        p = params or _DEFAULT_PARAMS
        w, h = self.x1 - self.x0, self.y1 - self.y0
        return (self.x0 + p.protect_side_frac * w, self.y0 + p.protect_top_frac * h,
                self.x1 - p.protect_side_frac * w, self.y0 + p.protect_bottom_frac * h)

    def hair(self, params: CaptionParams | None = None) -> tuple[float, float, float, float]:
        """Hair and forehead: from the head top down to the protected region."""
        p = params or _DEFAULT_PARAMS
        hx0, hx1 = self.head_x()
        return hx0, self.head_top(p), hx1, self.y0 + p.protect_top_frac * self.h

    def lower_face(self, params: CaptionParams | None = None) -> tuple[float, float, float, float]:
        """Below the mouth down to the chin (covering it is a blemish, not a violation)."""
        p = params or _DEFAULT_PARAMS
        return self.x0, self.y0 + p.protect_bottom_frac * self.h, self.x1, self.y1

def _face_at(index: TakeIndex, t_us: int) -> FaceBox | None:
    """Face at a source time, or None when the face is not visible (outside the track, face_lost)."""
    for ev in index.visual.events:
        if ev.kind == "face_lost" and ev.start_us <= t_us <= ev.end_us:
            return None
    track = index.visual.face_track
    if track is not None and track.points:
        tol = 400_000
        if track.points[0].t_us - tol <= t_us <= track.points[-1].t_us + tol:
            return track.at(t_us)
        return None
    faced = [s for s in index.visual.samples if s.face_box is not None and s.face_conf >= 0.3]
    if not faced:
        return None
    best = min(faced, key=lambda s: abs(s.t_us - t_us))
    return best.face_box if abs(best.t_us - t_us) <= 300_000 else None


def _covering_inserts(timeline: Timeline, t: Fraction) -> list[TimelineInsert]:
    return [x for x in timeline.inserts if to_fraction(x.out_start) <= t < to_fraction(x.out_end)]


def _shared_map_source_point() -> Any:
    """The compiler's ``map_source_point`` (the geometry video.py renders with), when importable."""
    try:
        from studio.compile import timeline as tl_mod
    except Exception:  # pragma: no cover - sibling module mid-edit or broken: use the local fallback
        return None
    return getattr(tl_mod, "map_source_point", None)


def _map_point(timeline: Timeline, t: Fraction, x: float, y: float, src_w: int, src_h: int
               ) -> tuple[float, float] | None:
    """Normalized source point → normalized output point after the framing transform at ``t``."""
    shared = _shared_map_source_point()
    if shared is not None:
        return shared(timeline, t, x, y, src_w, src_h)
    seg = timeline.segment_at(t)
    if seg is None:
        return None
    sc, cx, cy = _framing_at(seg.framing, t)
    rect = crop_rect(src_w, src_h, timeline.width, timeline.height, sc, cx, cy)
    px, py = source_to_output((x, y), rect, src_w, src_h, timeline.width, timeline.height)
    return px / timeline.width, py / timeline.height


def _faces_during(timeline: Timeline, index: TakeIndex, a: Fraction, b: Fraction,
                  step_s: float, params: CaptionParams | None = None) -> list[_FaceOut]:
    """Face boxes in output px sampled across ``[a, b)`` (after framing and split re-layouts); samples
    under full-frame inserts/cards are skipped (the face is not on screen). Each face carries the top of
    the head: the take's measured head-top ratio (``Visual.head_top_ratio``) or the prior."""
    p = params or _DEFAULT_PARAMS
    src_w, src_h = index.media.width, index.media.height
    W, H = timeline.width, timeline.height
    out: list[_FaceOut] = []
    step = Fraction(step_s).limit_denominator(1000)
    times: list[Fraction] = []
    t = a
    while t < b:
        times.append(t)
        t += step
    times.append(max(a, b - Fraction(1, 1000)))
    for t in times:
        if any(x.mode in ("full", "card") for x in _covering_inserts(timeline, t)):
            continue
        seg = timeline.segment_at(t)
        if seg is None:
            continue
        src_us = min(max(seg.out_to_src_us(t), seg.src_in_us), seg.src_out_us)
        fb = _face_at(index, src_us)
        if fb is None or fb.w <= 0 or fb.h <= 0:
            continue
        p0 = _map_point(timeline, t, fb.x, fb.y, src_w, src_h)
        p1 = _map_point(timeline, t, fb.x + fb.w, fb.y + fb.h, src_w, src_h)
        if p0 is None or p1 is None:
            continue
        x0, y0, x1, y1 = p0[0] * W, p0[1] * H, p1[0] * W, p1[1] * H
        if x1 <= 0 or y1 <= 0 or x0 >= W or y0 >= H:
            continue
        crown = None
        pc = _map_point(timeline, t, fb.x + fb.w / 2, index.visual.head_top_y(fb, p.head_top_prior), src_w, src_h)
        if pc is not None:
            crown = pc[1] * H
        out.append(_FaceOut(x0, y0, x1, y1, crown))
    return out

def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _text_rect(t: Any, width: int, height: int, zone: SafeZone) -> tuple[float, float, float, float]:
    size = float(t.style.size_px) * width / REF_W
    band_w = width - zone.left - zone.right
    est = min(estimate_text_width(t.text, size, t.style.font), band_w)
    lines = max(1, math.ceil(estimate_text_width(t.text, size, t.style.font) / max(band_w, 1)))
    if t.kind == "list":
        lines += len(t.items)
        est = band_w * 0.9
    h = lines * size * 1.2 + size * 0.5
    cx, cy = t.x_norm * width, t.y_norm * height
    x0 = min(max(cx - est / 2, zone.left), width - zone.right - est)
    y0 = min(max(cy - h / 2, zone.top), height - zone.bottom - h)
    return x0, y0, x0 + est, y0 + h


def _obstacles(timeline: Timeline, a: Fraction, b: Fraction, zone: SafeZone,
               params: CaptionParams) -> list[tuple[float, float, float, float]]:
    """Other overlays on screen during [a, b): PiP rects, text overlays, card content areas."""
    W, H = timeline.width, timeline.height
    obs: list[tuple[float, float, float, float]] = []
    for x in timeline.inserts:
        if not (to_fraction(x.out_start) < b and a < to_fraction(x.out_end)):
            continue
        if x.mode == "pip" and x.rect is not None:
            rx, ry, rw, rh = x.rect
            obs.append((rx * W, ry * H, (rx + rw) * W, (ry + rh) * H))
        elif isinstance(x.asset, CardSpec) and x.mode in ("full", "card"):
            reserve = params.card_reserve_px * H / REF_H
            obs.append((zone.left, zone.top, W - zone.right, H - zone.bottom - reserve))
    for t in timeline.texts:
        if to_fraction(t.out_start) < b and a < to_fraction(t.out_end):
            obs.append(_text_rect(t, W, H, zone))
    return obs


def _split_boundary(timeline: Timeline, a: Fraction, b: Fraction) -> float | None:
    """Normalized y of the split line when a split insert covers most of the page."""
    for x in timeline.inserts:
        if x.mode not in ("split_top", "split_bottom"):
            continue
        s, e = to_fraction(x.out_start), to_fraction(x.out_end)
        ov = min(e, b) - max(s, a)
        if ov > 0 and ov >= (b - a) / 2:
            if x.rect is not None:
                return x.rect[1] + x.rect[3] if x.mode == "split_top" else x.rect[1]
            return 0.5
    return None


@dataclass
class _Placement:
    top: float
    scale: float = 1.0
    relaxed: bool = False
    fallback: str = ""  # below_chin | below_chin_relaxed | above_head | no_face | fixed | split | least_bad
    held: bool = False  # kept by hysteresis (an earlier anchor)


def _page_block(style: CaptionStyle, text: str, width: int, params: CaptionParams, scale: float = 1.0) -> tuple[
        float, float]:
    """Estimated (width, height) of a caption block in px at ``scale`` (see :func:`caption_fit`)."""
    f = caption_fit(style, text, width, params, scale=scale)
    return f.width, f.height


def _robust_min(values: Sequence[float], q: float = 0.03) -> float:
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


class _Placer:
    """Stateful per-video placement with hysteresis.

    Doctrine order (captions-and-text.md, "Place"): just below the chin (top edge 40-120 px under it);
    when nothing fits, smaller text (down to the legibility floor), then the lower band (relaxed floor);
    the matte step (text behind the subject) is not available, so the next step is the band above the
    head, clear of the hair (close-up selfies whose chin sits below the band); then the least-bad spot.
    Eyes and mouth are never covered by choice. Anchors hold while they still work, one per framing
    section when the video allows it."""

    def __init__(self, timeline: Timeline, index: TakeIndex, zone: SafeZone, params: CaptionParams):
        self.tl = timeline
        self.ix = index
        self.zone = zone
        self.p = params
        self.W, self.H = timeline.width, timeline.height
        self.ws = self.W / REF_W
        self.hs = self.H / REF_H
        self.anchor: float | None = None  # current block top (px)
        self.anchor_scale = 1.0
        self.anchor_kind = ""
        self.history: list[tuple[float, float, str]] = []  # earlier good anchors (top, scale, kind), oldest first

    # geometry ---------------------------------------------------------------
    def _fit(self, style: CaptionStyle, text: str, scale: float) -> CaptionFit:
        return caption_fit(style, text, self.W, self.p, scale=scale,
                           max_width=self.W - self.zone.left - self.zone.right)

    def _scales(self, style: CaptionStyle) -> list[float]:
        """Size steps from full size down to the legibility floor (never below it)."""
        floor = min(1.0, self.p.min_size_px / style.size_px) if style.size_px > 0 else 1.0
        out = [1.0] + [s for s in (0.9, 0.8) if s > floor + 1e-6]
        if floor < out[-1] - 1e-6:
            out.append(round(floor, 4))
        return out

    def _x_range(self, bw: float) -> tuple[float, float]:
        left, right = self.zone.left, self.W - self.zone.right
        cx = min(max(self.W / 2, left + bw / 2), right - bw / 2)
        return cx - bw / 2, cx + bw / 2

    def _bottom(self, relaxed: bool) -> float:
        """Lowest block bottom in the band; on the relaxed floor the render bleed stays clear of the UI."""
        if relaxed:
            return self.H - self.zone.relaxed_bottom - RENDER_BLEED_PX * self.hs
        return self.H - self.zone.bottom

    def _above_limit(self, f: _FaceOut, hair_ok: bool) -> float:
        """Lowest bottom edge for a block above the head: clear of the hair (grazing its top allowed), or with
        ``hair_ok`` anywhere on the hair but never on the forehead (the landmark box top)."""
        if hair_ok:
            return f.forehead
        return f.head_top(self.p) + self.p.head_overlap_tol_frac * f.h

    def _head_clear(self, top: float, bh: float, x0: float, x1: float, faces: list[_FaceOut], *,
                    hair_ok: bool = False) -> bool:
        """The block keeps off the head: above it (see :meth:`_above_limit`) or under the chin by the minimum
        gap."""
        gap = self.p.chin_gap_min_px * self.hs
        for f in faces:
            hx0, hx1 = f.head_x()
            if not (x0 < hx1 and hx0 < x1):
                continue
            above = top + bh <= self._above_limit(f, hair_ok)
            below = top >= f.chin + gap
            if not (above or below):
                return False
        return True

    def _valid(self, top: float, bw: float, bh: float, faces: list[_FaceOut], obs: list, relaxed: bool,
               *, need_gap: bool = True, hair_ok: bool = False) -> bool:
        if top < self.zone.top - 0.5 or top + bh > self._bottom(relaxed) + 0.5:
            return False
        x0, x1 = self._x_range(bw)
        block = (x0, top, x1, top + bh)
        for f in faces:
            if _overlap(block, f.protected(self.p)) > 0:
                return False
        if need_gap and not self._head_clear(top, bh, x0, x1, faces, hair_ok=hair_ok):
            return False
        m = self.p.obstacle_margin_px * self.hs
        return all(_overlap(block, (o[0] - m, o[1] - m, o[2] + m, o[3] + m)) <= 0 for o in obs)

    def _kind_at(self, top: float, bh: float, faces: list[_FaceOut], relaxed: bool) -> str | None:
        """What a (valid) held position is on this page: above the head (``on_hair`` while the head has risen
        into it) or below the chin."""
        if not faces:
            return None
        if all(top + bh <= self._above_limit(f, False) for f in faces):
            return "above_head"
        if all(top + bh <= self._above_limit(f, True) for f in faces):
            return "on_hair"
        if all(top >= f.chin for f in faces):
            return "below_chin_relaxed" if relaxed else "below_chin"
        return None

    def _hysteresis_ok(self, top: float, bh: float, faces: list[_FaceOut]) -> bool:
        if not faces:
            return True
        if all(top + bh <= self._above_limit(f, True) for f in faces):
            # above the head: an anchored caption holds its place and size while the head rises into it
            # (doctrine: move only on a collision with eyes, mouth, UI or another overlay; hair is none)
            return True
        lo = self.p.chin_gap_min_px * self.hs
        hi = self.p.chin_gap_max_px * self.hs
        return all(lo <= top - f.chin for f in faces) and all(top - f.chin <= hi for f in faces)

    # main -------------------------------------------------------------------
    def place(self, style: CaptionStyle, text: str, a: Fraction, b: Fraction, *, position: str | None,
              y_norm: float | None, faces: list[_FaceOut] | None = None,
              section_faces: list[_FaceOut] | None = None) -> _Placement:
        p = self.p
        if faces is None:
            faces = _faces_during(self.tl, self.ix, a, b, p.face_sample_s, p)
        obs = _obstacles(self.tl, a, b, self.zone, p)
        f1 = self._fit(style, text, 1.0)
        bw, bh = f1.width, f1.height

        # explicit placements (Director): honoured unless they cover eyes/mouth or leave the band
        fixed: float | None = None
        if y_norm is not None:
            fixed = y_norm * self.H - bh / 2
        elif position in ("lower_third", "center", "upper_third"):
            y0, y1 = self.zone.top, self._bottom(False)
            frac = {"lower_third": 0.82, "center": 0.5, "upper_third": 0.17}[position]
            fixed = y0 + frac * (y1 - y0) - bh / 2
        if fixed is not None:
            fixed = min(max(fixed, self.zone.top), self._bottom(True) - bh)
            if self._valid(fixed, bw, bh, faces, [], True, need_gap=False):
                return _Placement(fixed, 1.0, fixed + bh > self._bottom(False), "fixed")

        split = _split_boundary(self.tl, a, b) if not faces else None
        if split is not None:  # speaker not measurable in the split: sit on the split line
            top = min(max(split * self.H - bh / 2, self.zone.top), self._bottom(False) - bh)
            return _Placement(top, 1.0, False, "split")

        scales = self._scales(style)
        # ``below_chin`` (the Director's explicit choice): only positions under the chin, strict band then the
        # relaxed floor, never above the head; when nothing fits there the least-bad spot (it pulls toward the
        # chin and never covers eyes or mouth)
        forced_below = position == "below_chin"

        def below_ok(top: float, bh_: float, relaxed_: bool) -> bool:
            kind_ = self._kind_at(top, bh_, faces, relaxed_)
            return not forced_below or kind_ in (None, "below_chin", "below_chin_relaxed")

        # hysteresis: keep the anchor (its top edge) while it still works, at the largest size that fits there
        if self.anchor is not None:
            for s in scales:
                fs = self._fit(style, text, s)
                relaxed = self.anchor + fs.height > self._bottom(False)
                if self._valid(self.anchor, fs.width, fs.height, faces, obs, relaxed, hair_ok=True) \
                        and self._hysteresis_ok(self.anchor, fs.height, faces) \
                        and below_ok(self.anchor, fs.height, relaxed):
                    self.anchor_scale = s
                    self.anchor_kind = self._kind_at(self.anchor, fs.height, faces, relaxed) or self.anchor_kind
                    return _Placement(self.anchor, s, relaxed, self.anchor_kind, held=True)

        # return to an earlier position rather than inventing a new one (fewer distinct caption heights)
        for top, _s, kind in reversed(self.history):
            for s in scales:
                fs = self._fit(style, text, s)
                relaxed = top + fs.height > self._bottom(False)
                if self._valid(top, fs.width, fs.height, faces, obs, relaxed, hair_ok=True) \
                        and self._hysteresis_ok(top, fs.height, faces) and below_ok(top, fs.height, relaxed):
                    kind = self._kind_at(top, fs.height, faces, relaxed) or kind
                    return self._commit(_Placement(top, s, relaxed, kind, held=True))

        if not faces:
            base = self.anchor if self.anchor is not None else p.default_center_y_px * self.hs - bh / 2
            for relaxed in (False, True):
                for top in self._scan_from(base, bh):
                    if self._valid(top, bw, bh, [], obs, relaxed):
                        return self._commit(_Placement(top, 1.0, relaxed, "no_face_relaxed" if relaxed else "no_face"))
            return self._commit(self._least_bad(style, text, faces, obs, scales))

        chin = max(f.chin for f in faces)
        # 1. just below the chin: strict band, then the relaxed floor; full size first, then smaller text
        for relaxed in (False, True):
            if relaxed and self._bottom(True) <= self._bottom(False) + 0.5:
                continue
            for s in scales:
                fs = self._fit(style, text, s)
                # the 40-120 px window first (preferred gap, then the rest of the window), then further
                # down (an obstacle such as a card pushed it) before any shrinking
                prefs = [chin + p.chin_gap_px * self.hs, chin + p.chin_gap_min_px * self.hs]
                prefs += [chin + g * self.hs for g in range(int(p.chin_gap_min_px), int(p.chin_gap_max_px) + 1, 8)]
                y = chin + (p.chin_gap_max_px + 8) * self.hs
                while y + fs.height <= self._bottom(relaxed):
                    prefs.append(y)
                    y += 8 * self.hs
                for top in prefs:
                    if self._valid(top, fs.width, fs.height, faces, obs, relaxed):
                        return self._commit(_Placement(top, s, relaxed,
                                                       "below_chin_relaxed" if relaxed else "below_chin"))
        if forced_below:
            # nothing clears the chin by the minimum gap: sit on the relaxed floor (the chin may graze the block's
            # top), largest size first, never over the eyes-to-mouth region; only then the least-bad spot
            for s in scales:
                fs = self._fit(style, text, s)
                top = self._bottom(True) - fs.height
                if self._valid(top, fs.width, fs.height, faces, obs, True, need_gap=False) and all(
                        top >= f.y0 + p.protect_bottom_frac * f.h for f in faces):
                    return self._commit(_Placement(top, s, True, "below_chin_relaxed"))
            return self._commit(self._least_bad(style, text, faces, obs, scales))
        # 2. the chin sits too low for the band: above the head, clear of the hair. One height for the whole
        #    framing section when it fits (captions stay put while the head bobs), else this page's.
        refs = [section_faces] if section_faces else []
        refs.append(faces)
        for s in scales:
            fs = self._fit(style, text, s)
            for ref in refs:
                crown = _robust_min([f.head_top(p) for f in ref])
                # the gap above the hair is a preference; the band top and the hair (grazing allowed) are limits
                top = max(crown - p.crown_gap_px * self.hs - fs.height, self.zone.top)
                if self._valid(top, fs.width, fs.height, faces, obs, False):
                    return self._commit(_Placement(top, s, False, "above_head"))
        # 3. nothing fits cleanly
        return self._commit(self._least_bad(style, text, faces, obs, scales))

    def _scan_from(self, base: float, bh: float) -> Iterable[float]:
        lo, hi = self.zone.top, self._bottom(True) - bh
        yield min(max(base, lo), hi)
        step = 8 * self.hs
        k = 1
        while base - k * step >= lo or base + k * step <= hi:
            for cand in (base + k * step, base - k * step):
                if lo <= cand <= hi:
                    yield cand
            k += 1

    def _least_bad(self, style: CaptionStyle, text: str, faces: list[_FaceOut], obs: list,
                   scales: Sequence[float]) -> _Placement:
        """Lowest-cost spot: eyes/mouth cost the most, hair/forehead and other overlays less, the chin
        least; bigger text wins ties."""
        p = self.p
        n = max(1, len(faces))
        pull = max((f.chin for f in faces), default=p.default_center_y_px * self.hs)
        best: tuple[float, float, float, bool] | None = None
        for s in scales:
            fs = self._fit(style, text, s)
            x0, x1 = self._x_range(fs.width)
            lo, hi = self.zone.top, self._bottom(True) - fs.height
            y = lo
            while y <= hi + 1e-6:
                block = (x0, y, x1, y + fs.height)
                cost = sum(10.0 * _overlap(block, f.protected(p)) + 1.0 * _overlap(block, f.hair(p))
                           + 0.3 * _overlap(block, f.lower_face(p)) for f in faces) / n
                cost += 2.0 * sum(_overlap(block, o) for o in obs)
                cost += 0.02 * fs.width * fs.height * (1.0 - s) + 0.01 * abs(y - pull)
                if best is None or cost < best[0]:
                    best = (cost, y, s, y + fs.height > self._bottom(False))
                y += 4 * self.hs
        if best is None:
            fs = self._fit(style, text, scales[-1])
            return _Placement(max(self.zone.top, self._bottom(True) - fs.height), scales[-1], True, "least_bad")
        return _Placement(best[1], best[2], best[3], "least_bad")

    def _commit(self, pl: _Placement) -> _Placement:
        self.anchor = pl.top
        self.anchor_scale = pl.scale
        self.anchor_kind = pl.fallback
        if pl.fallback != "least_bad" and not pl.held and all(
                (t, s) != (pl.top, pl.scale) for t, s, _k in self.history):
            self.history.append((pl.top, pl.scale, pl.fallback))
        return pl

# ============================================================================================== public API
def under_chin_reframe(index: TakeIndex, *, style: CaptionStyle, zone: SafeZone, width: int = REF_W,
                       height: int = REF_H, params: CaptionParams | None = None) -> dict[str, float] | None:
    """Can captions sit under the chin on this take? From the median face: the chin's output height at the base
    framing, the highest chin line a one-line block still fits under (``target_px``), and the base-reframe scale
    that would lift the chin there with the crop pushed to the bottom of the source (``scale``, 1.0 when it already
    fits; inf when no scale can). None without a face track."""
    import statistics

    from studio.compile.timeline import base_window

    p = params or _DEFAULT_PARAMS
    vis = index.visual
    pts = [(q.cy, q.h) for q in (vis.face_track.points if vis is not None and vis.face_track is not None else [])
           if q.conf >= 0.3 and q.h > 0]
    if not pts and vis is not None:
        pts = [(q.face_box.cy, q.face_box.h) for q in vis.samples if q.face_box is not None and q.face_conf >= 0.3]
    if not pts:
        return None
    cy, fh = statistics.median(a for a, _ in pts), statistics.median(b for _, b in pts)
    src_w, src_h = index.media.width, index.media.height
    _bw, bh = base_window(src_w, src_h, width / height)
    win_h = bh * src_h
    chin_px = (cy + fh / 2) * src_h
    y0 = min(max(cy * src_h - win_h / 2, 0.0), src_h - win_h)
    chin_out = (chin_px - y0) / win_h * height
    band_bottom = height - zone.relaxed_bottom
    target = band_bottom - caption_block_height(style, 1, scale=width / REF_W) - p.chin_gap_min_px * height / REF_H
    if chin_out <= target:
        scale = 1.0
    elif src_h - chin_px <= 1.0:
        scale = math.inf
    else:
        scale = max(1.0, (1.0 - target / height) * win_h / (src_h - chin_px))
    centre_y = (src_h - win_h / (2 * scale)) / src_h if math.isfinite(scale) else 0.5
    return {"chin_px": chin_out, "target_px": target, "scale": scale, "centre_y": centre_y,
            "band_bottom_px": band_bottom}


#: Cap height as a share of the font size (from each font's OS/2 table; Montserrat 700/1000). Used only to report
#: how big the letters are on screen (rendered cap height), never to lay out.
CAP_HEIGHT_EM: dict[str, float] = {"montserrat": 0.70, "inter": 0.727, "anton": 0.73, "tiktok sans": 0.70,
                                   "tiktoksans": 0.70, "archivo": 0.70}
#: Eye line inside the landmark face box (upper forehead to chin), measured on real takes: 0.24-0.30.
_EYE_FRAC = 0.27


def cap_height_px(style: CaptionStyle, size_px: float | None = None) -> float:
    """Rendered cap height (px) of a caption at ``size_px`` (default: the style's size)."""
    em = CAP_HEIGHT_EM.get(style.font.strip().lower(), 0.70)
    return em * float(size_px if size_px is not None else style.size_px)


def caption_geometry(timeline: Timeline, index: TakeIndex, *, platform: str | Iterable[str] | None = None,
                     settings: Settings | None = None, params: CaptionParams | None = None,
                     style_px: float | None = None) -> dict[str, Any]:
    """Where and how big every caption page renders, measured against the face *after framing transforms* and
    the platform bands (advisory: critics and the Director read it; nothing gates on it).

    Per page: the block's top/bottom (px), rendered font size and cap height (px and % of the frame height), the
    face relation (``below_chin`` with the gap to the chin, ``above_head``, ``on_hair`` = over the hair or
    forehead, ``over_face`` = over the eyes-to-mouth region, ``over_lower_face``, ``no_face``), whether the block
    sits above the eye line, and how far it reaches into the strict platform band (``into_ui_px``: UI when the post
    description runs long) or past the relaxed caption floor. ``summary`` aggregates them (shares, size spread,
    distinct heights) and quotes the doctrine priors (chin gap window, phrase-page size range)."""
    import statistics

    constants = load_constants(settings)
    params = params or load_caption_params(settings, constants)
    W, H = timeline.width, timeline.height
    zone = safe_zone_for(platform, width=W, height=H, constants=constants)
    strict_bottom = H - zone.bottom
    floor = H - zone.relaxed_bottom
    hs = H / REF_H
    lo_gap, hi_gap = params.chin_gap_min_px * hs, params.chin_gap_max_px * hs
    rows: list[dict[str, Any]] = []
    for k, pg in enumerate(timeline.captions):
        text = pg.text or " ".join(w.text for w in pg.words)
        fit = caption_fit(pg.style, text, W, params, max_width=W - zone.left - zone.right)
        top = pg.y_norm * H - fit.height / 2
        bottom = top + fit.height
        cap = cap_height_px(pg.style, fit.size_px)
        a, b = to_fraction(pg.out_start), to_fraction(pg.out_end)
        faces = _faces_during(timeline, index, a, b, params.face_sample_s, params) if index.visual is not None else []
        row: dict[str, Any] = {
            "page": pg.page_id or f"p{k + 1:03d}", "text": text, "top_px": round(top, 1), "bottom_px": round(bottom, 1),
            "size_px": round(fit.size_px * REF_W / W, 1), "lines": fit.lines, "cap_height_px": round(cap, 1),
            "cap_height_pct": round(cap / H * 100, 2), "placement": pg.placement or "",
            "into_ui_px": round(max(0.0, bottom - strict_bottom), 1),
            "past_floor_px": round(max(0.0, bottom - floor), 1)}
        if not faces:
            row["relation"] = "no_face"
        else:
            chin = max(f.chin for f in faces)
            eyes = statistics.median(f.y0 + _EYE_FRAC * f.h for f in faces)
            crown = min(f.head_top(params) for f in faces)
            x0, x1 = W / 2 - fit.width / 2, W / 2 + fit.width / 2
            block = (x0, top, x1, bottom)
            row["chin_px"] = round(chin, 1)
            row["eye_line_px"] = round(eyes, 1)
            row["above_eyes"] = bottom <= eyes
            if top >= chin - 0.5:
                row["relation"] = "below_chin"
                row["chin_gap_px"] = round(top - chin, 1)
            elif any(_overlap(block, f.protected(params)) > 0 for f in faces):
                row["relation"] = "over_face"
            elif bottom <= crown + params.head_overlap_tol_frac * max(f.h for f in faces):
                row["relation"] = "above_head"
            elif bottom <= eyes:
                row["relation"] = "on_hair"
            else:
                row["relation"] = "over_lower_face"
        rows.append(row)
    summary: dict[str, Any] = {"pages": len(rows)}
    if rows:
        n = len(rows)
        rel: dict[str, int] = {}
        for r in rows:
            rel[r["relation"]] = rel.get(r["relation"], 0) + 1
        sizes = [r["size_px"] for r in rows]
        caps = [r["cap_height_px"] for r in rows]
        gaps = [r["chin_gap_px"] for r in rows if "chin_gap_px" in r]
        summary.update({
            "relation": rel,
            "share_above_eyes": round(sum(1 for r in rows if r.get("above_eyes")) / n, 3),
            "share_below_chin_in_window": round(sum(1 for g in gaps if lo_gap - 0.5 <= g <= hi_gap + 0.5) / n, 3),
            "median_chin_gap_px": round(statistics.median(gaps), 1) if gaps else None,
            "size_px": {"min": min(sizes), "median": statistics.median(sizes), "max": max(sizes),
                        # the document's style size (pages shrunk to fit carry a smaller copy of the style)
                        "style": float(style_px) if style_px else
                        float(max(pg.style.size_px for pg in timeline.captions))},
            "cap_height_px": {"min": min(caps), "median": round(statistics.median(caps), 1), "max": max(caps)},
            "cap_height_pct_median": round(statistics.median(r["cap_height_pct"] for r in rows), 2),
            "size_spread": round(max(sizes) / max(min(sizes), 1e-6), 3),
            "distinct_tops": len({round(r["top_px"] / 8) for r in rows}),
            "pages_into_ui": sum(1 for r in rows if r["into_ui_px"] > 0.5),
            "max_into_ui_px": max(r["into_ui_px"] for r in rows),
            "pages_past_floor": sum(1 for r in rows if r["past_floor_px"] > 0.5),
            "two_line_pages": sum(1 for r in rows if r["lines"] > 1),
        })
    size_rng = None
    with_c = _flatten(constants)
    rng = _find_range(with_c, "captions.size_px_phrase")
    if rng is not None:
        size_rng = [rng[0], rng[1]]
    return {"platform": platform if isinstance(platform, str) or platform is None else list(platform),
            "frame": [W, H], "strict_band_bottom_px": round(strict_bottom, 1), "relaxed_floor_px": round(floor, 1),
            "band_top_px": round(zone.top, 1),
            "priors": {"chin_gap_px": [params.chin_gap_min_px, params.chin_gap_max_px],
                       "phrase_size_px": size_rng or [params.min_size_px, 96.0], "lines": 1},
            "summary": summary, "pages": rows}


def caption_geometry_text(geo: Mapping[str, Any], *, max_pages: int = 12) -> str:
    """A few lines a model reads: the summary, the priors and the pages that sit outside them."""
    s = geo.get("summary") or {}
    if not s.get("pages"):
        return "Caption geometry: no caption pages."
    H = (geo.get("frame") or [1080, 1920])[1]
    pri = geo.get("priors") or {}
    sz = s.get("size_px") or {}
    cap = s.get("cap_height_px") or {}
    rel = ", ".join(f"{k} {v}" for k, v in sorted((s.get("relation") or {}).items()))
    glo, ghi = (pri.get("chin_gap_px") or [40, 120])[:2]
    lines = [
        f"Caption geometry (measured by code on the {H}-px-tall frame, after framing transforms): {s['pages']} pages; "
        f"relation to the face: {rel}; share of pages above the eye line {s.get('share_above_eyes', 0):.0%}; "
        f"below the chin inside the {glo:.0f}-{ghi:.0f} px window {s.get('share_below_chin_in_window', 0):.0%}"
        + (f" (median gap {s['median_chin_gap_px']:.0f} px)" if s.get("median_chin_gap_px") is not None else "") + ".",
        f"  size on screen: {sz.get('min')}-{sz.get('max')} px font at 1080 wide (style {sz.get('style')} px; doctrine "
        f"phrase pages {pri.get('phrase_size_px', [64, 96])[0]:.0f}-{pri.get('phrase_size_px', [64, 96])[1]:.0f} px), "
        f"cap height {cap.get('min')}-{cap.get('max')} px (median {s.get('cap_height_pct_median')}% of the frame "
        f"height); size spread x{s.get('size_spread')}; {s.get('distinct_tops')} distinct caption heights; "
        f"{s.get('two_line_pages', 0)} two-line pages.",
        f"  platform bands: strict text band ends at y {geo.get('strict_band_bottom_px')}, relaxed caption floor "
        f"y {geo.get('relaxed_floor_px')} (between them: platform UI only when the post description runs long); "
        f"{s.get('pages_into_ui', 0)} pages reach into the strict band (max {s.get('max_into_ui_px', 0)} px), "
        f"{s.get('pages_past_floor', 0)} past the floor.",
    ]
    lo, hi = (pri.get("chin_gap_px") or [40, 120])[:2]
    odd = [r for r in geo.get("pages") or []
           if r.get("relation") not in ("below_chin", "no_face")
           or (r.get("relation") == "below_chin" and not (lo - 0.5 <= r.get("chin_gap_px", lo) <= hi + 0.5))
           or r.get("into_ui_px", 0) > 0.5 or r.get("size_px", 0) < (sz.get("style") or 0) * 0.9 - 0.5]
    if odd:
        lines.append("  pages outside the priors: " + "; ".join(
            f"{r['page']} \"{str(r['text'])[:24]}\" {r['relation']}"
            + (f" gap {r['chin_gap_px']:.0f}px" if "chin_gap_px" in r else "")
            + f", {r['size_px']:.0f}px, top y{r['top_px']:.0f}"
            + (f", {r['into_ui_px']:.0f}px into the strict band" if r.get("into_ui_px", 0) > 0.5 else "")
            for r in odd[:max_pages]) + (f" (+{len(odd) - max_pages} more)" if len(odd) > max_pages else ""))
    return "\n".join(lines)


def placement_options(index: TakeIndex, *, style: CaptionStyle, zone: SafeZone, width: int = REF_W,
                      height: int = REF_H, params: CaptionParams | None = None) -> dict[str, Any] | None:
    """Measured costs of the caption positions available on this take at the base framing (median face), for the
    Director to choose between (none is imposed): just under the chin (strict band, relaxed floor), the largest
    text that fits under the chin in the strict band, a base reframe that lifts the chin, and the band above the
    head. None without a face track."""
    import statistics

    from studio.compile.timeline import _ok_scale, base_window

    p = params or _DEFAULT_PARAMS
    vis = index.visual
    pts = [(q.cy, q.h) for q in (vis.face_track.points if vis is not None and vis.face_track is not None else [])
           if q.conf >= 0.3 and q.h > 0]
    if not pts and vis is not None:
        pts = [(q.face_box.cy, q.face_box.h) for q in vis.samples if q.face_box is not None and q.face_conf >= 0.3]
    if not pts:
        return None
    cy, fh = statistics.median(a for a, _ in pts), statistics.median(b for _, b in pts)
    src_w, src_h = index.media.width, index.media.height
    _bw, bh = base_window(src_w, src_h, width / height)
    win_h = bh * src_h
    y0 = min(max(cy * src_h - win_h / 2, 0.0), src_h - win_h)

    def out_y(v_src_px: float) -> float:
        return (v_src_px - y0) / win_h * height

    chin = out_y((cy + fh / 2) * src_h)
    face_top = out_y((cy - fh / 2) * src_h)
    eyes = face_top + _EYE_FRAC * (chin - face_top)
    head_ratio = vis.head_top_ratio if vis is not None and getattr(vis, "head_top_ratio", None) else p.head_top_prior
    crown = face_top - head_ratio * (chin - face_top)
    hs = height / REF_H
    block = caption_block_height(style, 1, scale=width / REF_W)
    strict_bottom = height - zone.bottom
    floor = height - zone.relaxed_bottom
    top = chin + p.chin_gap_px * hs
    top_min = chin + p.chin_gap_min_px * hs
    out: dict[str, Any] = {"chin_px": round(chin, 1), "eye_line_px": round(eyes, 1), "head_top_px": round(crown, 1),
                           "block_h_px": round(block, 1), "strict_band_bottom_px": round(strict_bottom, 1),
                           "relaxed_floor_px": round(floor, 1), "band_top_px": round(zone.top, 1),
                           "size_px": style.size_px}
    # (a) just under the chin at the preferred gap, else the minimum gap
    best_top = top if top + block <= floor else top_min
    out["below_chin"] = {"top_px": round(best_top, 1), "bottom_px": round(best_top + block, 1),
                         "fits_strict": best_top + block <= strict_bottom + 0.5,
                         "fits_relaxed": best_top + block <= floor + 0.5,
                         "into_ui_px": round(max(0.0, best_top + block - strict_bottom), 1)}
    # (b) the largest size (not under the legibility floor) that fits under the chin, strict band / relaxed floor
    def largest(bottom_limit: float) -> int | None:
        for sz in range(int(style.size_px), int(p.min_size_px) - 1, -2):
            bk = caption_block_height(style.model_copy(update={"size_px": sz}), 1, scale=width / REF_W)
            if top_min + bk <= bottom_limit + 0.5:
                return sz
        return None

    out["smaller_text_px"] = largest(strict_bottom)
    out["smaller_text_relaxed_px"] = largest(floor) if not out["below_chin"]["fits_relaxed"] else None
    # (c) a base reframe lifting the chin (crop pushed to the bottom of the source) so a full-size one-line block sits
    #     at the preferred gap under it; x1.2 upsampling is clean on a 1080p source
    chin_src = (cy + fh / 2) * src_h

    def lift(bottom_limit: float) -> float:
        target = bottom_limit - block - p.chin_gap_px * hs
        if chin <= target:
            return 1.0
        if src_h - chin_src <= 1.0:
            return math.inf
        return max(1.0, (1.0 - target / height) * win_h / (src_h - chin_src))

    ok = _ok_scale(index, height)
    relaxed_scale, strict_scale = lift(floor), lift(strict_bottom)
    out["reframe"] = {"relaxed_scale": relaxed_scale, "strict_scale": strict_scale, "clean_scale": round(ok, 3),
                      "centre_y": (src_h - win_h / (2 * relaxed_scale)) / src_h if math.isfinite(relaxed_scale)
                      else None,
                      "head_top_after_px": round(height - (height - crown) * relaxed_scale, 1)
                      if math.isfinite(relaxed_scale) else None}
    # (d) the band above the head, clear of the hair
    ab_top = max(crown - p.crown_gap_px * hs - block, zone.top)
    out["above_head"] = {"top_px": round(ab_top, 1), "bottom_px": round(ab_top + block, 1),
                         "clear_of_hair": ab_top + block <= crown + p.head_overlap_tol_frac * (chin - face_top) + 0.5,
                         "above_eyes": True}
    return out


def _page_text(toks: Sequence[_Tok]) -> str:
    return " ".join(t.text for t in toks)


def _style_for(doc: CutDocument | None) -> CaptionStyle:
    if doc is not None and doc.captions is not None:
        return doc.captions.style
    return CaptionStyle()


def _platforms_of(doc: CutDocument | None) -> list[str] | None:
    if doc is None or not doc.deliverables:
        return None
    return sorted({d.platform for d in doc.deliverables})


def build_caption_pages(index: TakeIndex, doc: CutDocument, timeline: Timeline, *,
                        platforms: str | Iterable[str] | None = None, settings: Settings | None = None,
                        params: CaptionParams | None = None) -> list[TimelineCaptionPage]:
    """Timed, placed caption pages for ``timeline`` (output frame grid).

    Uses the document's :class:`CaptionPlan` pages when it lists any (Director-authored: its word IDs,
    emphasis, text overrides and positions are honoured); otherwise pages automatically. Captions are on
    by default (``doc.captions is None``); ``enabled=False`` returns ``[]``.
    """
    if doc.captions is not None and not doc.captions.enabled:
        return []
    constants = load_constants(settings)
    params = params or load_caption_params(settings, constants)
    style = _style_for(doc)
    spans = _segment_word_spans(timeline, index)
    kept = [w for w in doc.kept_word_ids(index) if w in spans]
    payoff = _payoff_ids(doc)
    drafts: list[_PageDraft] = []
    plan_pages = doc.captions.pages if doc.captions is not None else []
    if plan_pages:
        for pg in plan_pages:
            ids = [w for w in pg.word_ids if w in spans]
            if not ids:
                continue
            if pg.text is None:
                toks = _build_tokens(ids, spans, index, params, payoff)
            else:  # display override: timing from all its words (fillers included)
                toks = []
                for wid in ids:
                    a, b, k = spans[wid]
                    toks.append(_Tok(wid=wid, text=index.word(wid).text, start=a, end=b, word=index.word(wid),
                                     seg=k))
                toks.sort(key=lambda t: t.start)
            if not toks:
                continue
            drafts.append(_PageDraft(toks=toks, emphasis=set(pg.emphasis_word_ids), text=pg.text,
                                     position=pg.position, y_norm=pg.y_norm, page_id=pg.id))
        drafts.sort(key=lambda d: d.toks[0].start)
    else:
        toks = _build_tokens(kept, spans, index, params, payoff, _insert_breaks(doc))
        pages = _page_tokens(toks, style=style, params=params, width_scale=timeline.width / REF_W)
        emph = _choose_emphasis(pages, params, _device_words(doc))
        drafts = [_PageDraft(toks=pg, emphasis={t.wid for t in pg if t.wid in emph}) for pg in pages]
    if not drafts:
        return []
    drafts = _time_pages(drafts, timeline, params)
    fps = normalize_fps(timeline.fps)
    pages_out: list[TimelineCaptionPage] = []
    for k, d in enumerate(drafts, start=1):
        pages_out.append(TimelineCaptionPage(
            page_id=d.page_id or format_id("p", k),
            words=[TimelineCaptionWord(word_id=t.wid, text=t.text, out_start=t.start, out_end=t.end,
                                       emphasis=t.wid in d.emphasis) for t in d.toks],
            text=d.text,
            out_start=frame_time(d.in_f, fps),
            out_end=frame_time(d.out_f, fps),
            style=style,
            y_norm=0.5,
        ))
    policy = doc.captions.position if doc.captions is not None else "auto"
    positions = [(d.position or (policy if policy != "auto" else None), d.y_norm) for d in drafts]
    zone = safe_zone_for(platforms if platforms is not None else _platforms_of(doc), width=timeline.width,
                         height=timeline.height, constants=constants)
    return _place_pages(pages_out, timeline, index, zone, params, positions)


def _section_key(timeline: Timeline, a: Fraction, b: Fraction) -> tuple:
    """Pages with the same key share one framing section (same crop, same split/PiP layout)."""
    t = (a + b) / 2
    seg = timeline.segment_at(t)
    sc, cx, cy = _framing_at(seg.framing, t) if seg is not None else (1.0, 0.5, 0.5)
    lay = tuple(sorted(x.insert_id for x in _covering_inserts(timeline, t)))
    return (round(sc, 2), round(cx, 2), round(cy, 2), lay)


def _place_pages(pages: list[TimelineCaptionPage], timeline: Timeline, index: TakeIndex, zone: SafeZone,
                 params: CaptionParams, positions: Sequence[tuple[str | None, float | None]] | None = None
                 ) -> list[TimelineCaptionPage]:
    placer = _Placer(timeline, index, zone, params)
    spans = [(to_fraction(pg.out_start), to_fraction(pg.out_end)) for pg in pages]
    faces = [_faces_during(timeline, index, a, b, params.face_sample_s, params) for a, b in spans]
    keys = [_section_key(timeline, a, b) for a, b in spans]
    section: dict[int, list[_FaceOut]] = {}
    k = 0
    while k < len(pages):
        j = k
        while j + 1 < len(pages) and keys[j + 1] == keys[k]:
            j += 1
        merged = [f for i in range(k, j + 1) for f in faces[i]]
        for i in range(k, j + 1):
            section[i] = merged
        k = j + 1
    out: list[TimelineCaptionPage] = []
    for k, pg in enumerate(pages):
        pos, yn = positions[k] if positions is not None else (None, None)
        text = pg.text or " ".join(w.text for w in pg.words)
        a, b = spans[k]
        pl = placer.place(pg.style, text, a, b, position=pos, y_norm=yn, faces=faces[k],
                          section_faces=section.get(k) or None)
        bh = placer._fit(pg.style, text, pl.scale).height
        y_norm = min(max((pl.top + bh / 2) / timeline.height, 0.0), 1.0)
        style = pg.style
        if pl.scale < 0.999:
            style = style.model_copy(update={"size_px": max(24, round(style.size_px * pl.scale)),
                                             "stroke_px": round(style.stroke_px * pl.scale, 2)})
        out.append(pg.model_copy(update={"y_norm": round(y_norm, 5), "style": style, "placement": pl.fallback}))
    return out

def place_captions(timeline: Timeline, index: TakeIndex, *, platform: str | Iterable[str] | None = "tiktok",
                   settings: Settings | None = None, params: CaptionParams | None = None) -> Timeline:
    """Return a copy of ``timeline`` with caption ``y_norm`` chosen per page (safe zone, off the face,
    below the chin, with hysteresis). Page timing and text are left untouched."""
    constants = load_constants(settings)
    params = params or load_caption_params(settings, constants)
    zone = safe_zone_for(platform, width=timeline.width, height=timeline.height, constants=constants)
    placed = _place_pages(list(timeline.captions), timeline, index, zone, params)
    return timeline.model_copy(update={"captions": placed})


def caption_placement_issues(timeline: Timeline, index: TakeIndex, *, platform: str | Iterable[str] | None = None,
                             settings: Settings | None = None, params: CaptionParams | None = None,
                             relaxed_floor: bool = True) -> list[dict[str, Any]]:
    """QA helper for invariant 7: pages whose estimated block leaves the safe zone or covers the eyes/mouth
    (face track after framing transforms). Empty list = pass. Advisory kinds that do not fail invariant 7:
    ``covers_hair`` (the block sits on the hair or forehead) and ``below_size_floor`` (the page renders
    under the legibility floor)."""
    constants = load_constants(settings)
    params = params or load_caption_params(settings, constants)
    zone = safe_zone_for(platform, width=timeline.width, height=timeline.height, constants=constants)
    W, H = timeline.width, timeline.height
    bottom = H - (zone.relaxed_bottom if relaxed_floor else zone.bottom)
    issues: list[dict[str, Any]] = []
    for pg in timeline.captions:
        text = pg.text or " ".join(w.text for w in pg.words)
        fit = caption_fit(pg.style, text, W, params, max_width=W - zone.left - zone.right)
        bw, bh = fit.width, fit.height
        cy = pg.y_norm * H
        left, right = zone.left, W - zone.right
        cx = min(max(W / 2, left + bw / 2), right - bw / 2)
        block = (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
        refs = [w.word_id for w in pg.words]
        if block[1] < zone.top - 0.5 or block[3] > bottom + 0.5:
            issues.append({"page": pg.page_id, "kind": "outside_safe_zone", "refs": refs,
                           "block": [round(v, 1) for v in block]})
        if bw > right - left + 0.5:
            issues.append({"page": pg.page_id, "kind": "too_wide", "refs": refs, "width": round(bw, 1)})
        faces = _faces_during(timeline, index, to_fraction(pg.out_start), to_fraction(pg.out_end),
                              params.face_sample_s, params)
        if any(_overlap(block, f.protected(params)) > 0 for f in faces):
            issues.append({"page": pg.page_id, "kind": "covers_face", "refs": refs,
                           "block": [round(v, 1) for v in block]})
        elif any(_overlap(block, f.hair(params)) > 0 and block[1] < f.chin
                 and block[3] > f.head_top(params) + params.head_overlap_tol_frac * f.h + 0.5 for f in faces):
            issues.append({"page": pg.page_id, "kind": "covers_hair", "refs": refs,
                           "block": [round(v, 1) for v in block]})
        floor = min(pg.style.size_px, params.min_size_px) * W / REF_W
        if fit.size_px < floor - 0.5:
            issues.append({"page": pg.page_id, "kind": "below_size_floor", "refs": refs,
                           "size_px": round(fit.size_px, 1)})
    return issues


# ============================================================================================== SRT
def _srt_time(t: Fraction) -> str:
    ms = max(0, math.floor(t * 1000 + Fraction(1, 2)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_text(timeline: Timeline) -> str:
    """SRT body for the timeline's caption pages (verbatim page text, natural case)."""
    blocks = []
    pages = sorted(timeline.captions, key=lambda p: p.out_start)
    for k, pg in enumerate(pages, start=1):
        text = (pg.text or " ".join(w.text for w in pg.words)).strip()
        if not text:
            continue
        blocks.append(f"{k}\n{_srt_time(to_fraction(pg.out_start))} --> {_srt_time(to_fraction(pg.out_end))}\n{text}\n")
    return "\n".join(blocks)


def write_srt(timeline: Timeline, path: str | os.PathLike[str]) -> Path:
    """Write the SRT sidecar from the timeline's caption pages (UTF-8, CRLF-free)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(srt_text(timeline), encoding="utf-8")
    return p

