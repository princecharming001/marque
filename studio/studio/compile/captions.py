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
   order: a larger gap below the chin, smaller text, the relaxed lower floor (y 1436), the band above
   the head (close-up selfies whose chin sits below the band), then the least-bad position. Everything
   is clamped to the platform safe zone (the strictest zone over all deliverable platforms). Full-frame
   and split inserts are page boundaries, so a page never straddles two layouts.

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
from collections.abc import Iterable, Sequence
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
    "caption_block_height", "FONT_WIDTH_EM",
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


#: Published/third-party safe zones at 1080x1920 (platforms.md). TikTok uses the conservative end of the
#: third-party readings; Reels the official Meta 14/35/6 % ad spec; Shorts Google's vertical overlay.
#: "all" is the cross-platform house band x 65-888, y 288-1248 (the union of the three).
PLATFORM_SAFE_ZONES: dict[str, SafeZone] = {
    "tiktok": SafeZone(top=200, bottom=480, left=60, right=180, caption_floor=484),
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
    line_height: float = 1.12
    face_sample_s: float = 0.1
    card_reserve_px: float = 230.0  # caption band kept free at the bottom of card content
    # eyes-to-mouth region as fractions of the face box (MediaPipe landmark box: mid-forehead to chin):
    # brows at ~0.16, eyes ~0.3, mouth ~0.85 of the height
    protect_top_frac: float = 0.12
    protect_bottom_frac: float = 0.95
    protect_side_frac: float = 0.08
    obstacle_margin_px: float = 24.0  # breathing room around other overlays

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


def load_caption_params(settings: Settings | None = None, constants: dict[str, Any] | None = None) -> CaptionParams:
    """Defaults overridden by whatever matching keys ``constants.yaml`` provides."""
    flat = _flatten(constants if constants is not None else load_constants(settings))
    over: dict[str, Any] = {}
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
#: Only used to *plan* pages; the renderer measures the real glyphs and fits exactly.
FONT_WIDTH_EM: dict[str, tuple[float, float, float, float]] = {
    "montserrat": (0.64, 0.78, 0.68, 0.28),
    "inter": (0.57, 0.71, 0.62, 0.26),
    "anton": (0.42, 0.47, 0.47, 0.2),
    "tiktok sans": (0.56, 0.69, 0.6, 0.25),
    "tiktoksans": (0.56, 0.69, 0.6, 0.25),
    "archivo": (0.57, 0.7, 0.6, 0.25),
}


def estimate_text_width(text: str, size_px: float, font: str = "Montserrat", *, stroke_px: float = 0.0,
                        case: str = "as_is") -> float:
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
    return em * size_px + 2 * stroke_px


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
        return 3.0  # "the | budget", "of | the"
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
               max_words: int, run_len: int, width_scale: float) -> float:
    n = i - j
    if n > max_words:
        return math.inf
    page = toks[j:i]
    text = " ".join(t.text for t in page)
    width = estimate_text_width(text, style.size_px * width_scale, style.font, stroke_px=style.stroke_px,
                                case=style.case)
    limit = params.max_line_px * width_scale * style.lines
    cost = 0.0
    if n > 1 and width * params.min_font_scale > limit:
        return math.inf
    if width > limit:
        cost += 1.2 * (width / limit - 1.0) * 4
    chars = len(text)
    if chars > style.max_chars_per_line * style.lines:
        cost += 0.35 * (chars - style.max_chars_per_line * style.lines)
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
        # doctrine: <= 20 CPS; a fast page is a reason to re-page (never to delete words)
        cost += 0.3 * (cps - params.max_cps)
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
                           width_scale=width_scale)
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


def _accent_score(t: _Tok, params: CaptionParams) -> float:
    n = _norm(t.text)
    number = _is_number(t.text)
    if not number and (n in _STOP or len(n) <= 2):
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
    pages = _page_tokens(toks, style=st, params=params, width_scale=width_scale)
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
    """Face in output px at one sample: box, chin line and the eye-to-mouth protected box."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def chin(self) -> float:
        return self.y1

    @property
    def forehead(self) -> float:
        return self.y0

    def protected(self, params: CaptionParams | None = None) -> tuple[float, float, float, float]:
        """Brows-to-lips region that text may never cover (fractions from :class:`CaptionParams`)."""
        p = params or _DEFAULT_PARAMS
        w, h = self.x1 - self.x0, self.y1 - self.y0
        return (self.x0 + p.protect_side_frac * w, self.y0 + p.protect_top_frac * h,
                self.x1 - p.protect_side_frac * w, self.y0 + p.protect_bottom_frac * h)


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
                  step_s: float) -> list[_FaceOut]:
    """Face boxes in output px sampled across ``[a, b)`` (after framing and split re-layouts); samples
    under full-frame inserts/cards are skipped (the face is not on screen)."""
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
        out.append(_FaceOut(x0, y0, x1, y1))
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
    fallback: str = ""


def _page_block(style: CaptionStyle, text: str, width: int, params: CaptionParams, scale: float = 1.0) -> tuple[
        float, float]:
    """Estimated (width, height) of a caption block in px at ``scale``."""
    ws = width / REF_W
    size = style.size_px * ws * scale
    max_w = params.max_line_px * ws
    w = estimate_text_width(text, size, style.font, stroke_px=style.stroke_px * ws * scale, case=style.case)
    lines = 1
    if w > max_w:
        lines = 1 if w * params.min_font_scale <= max_w or style.lines == 1 else min(style.lines, 2)
        w = min(w, max_w)
    return w, caption_block_height(style, lines, scale=scale * ws, line_height=params.line_height)


class _Placer:
    """Stateful per-video placement with hysteresis."""

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
        self.history: list[tuple[float, float]] = []  # earlier good anchors (top, scale), oldest first

    # geometry ---------------------------------------------------------------
    def _x_range(self, bw: float) -> tuple[float, float]:
        left, right = self.zone.left, self.W - self.zone.right
        cx = min(max(self.W / 2, left + bw / 2), right - bw / 2)
        return cx - bw / 2, cx + bw / 2

    def _bottom(self, relaxed: bool) -> float:
        return self.H - (self.zone.caption_floor if relaxed else self.zone.bottom)

    def _valid(self, top: float, bw: float, bh: float, faces: list[_FaceOut], obs: list, relaxed: bool,
               *, need_gap: bool = True) -> bool:
        if top < self.zone.top - 0.5 or top + bh > self._bottom(relaxed) + 0.5:
            return False
        x0, x1 = self._x_range(bw)
        block = (x0, top, x1, top + bh)
        for f in faces:
            if _overlap(block, f.protected(self.p)) > 0:
                return False
            if need_gap and x0 < f.x1 and f.x0 < x1 and top < f.chin + self.p.chin_gap_min_px * self.hs \
                    and top + bh > f.forehead:
                return False  # a block over the face must clear the chin by the minimum gap
        m = self.p.obstacle_margin_px * self.hs
        return all(_overlap(block, (o[0] - m, o[1] - m, o[2] + m, o[3] + m)) <= 0 for o in obs)

    def _hysteresis_ok(self, top: float, faces: list[_FaceOut]) -> bool:
        if not faces:
            return True
        lo = self.p.chin_gap_min_px * self.hs
        hi = self.p.chin_gap_max_px * self.hs
        above = all(top < f.forehead for f in faces)
        if above:
            return True
        return all(lo <= top - f.chin for f in faces) and all(top - f.chin <= hi for f in faces)

    # main -------------------------------------------------------------------
    def place(self, style: CaptionStyle, text: str, a: Fraction, b: Fraction, *, position: str | None,
              y_norm: float | None) -> _Placement:
        p = self.p
        faces = _faces_during(self.tl, self.ix, a, b, p.face_sample_s)
        obs = _obstacles(self.tl, a, b, self.zone, p)
        bw, bh = _page_block(style, text, self.W, p)

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

        # hysteresis: keep the anchor while it still works
        if self.anchor is not None:
            s = self.anchor_scale
            bw_s, bh_s = _page_block(style, text, self.W, p, s)
            relaxed = self.anchor + bh_s > self._bottom(False)
            if self._valid(self.anchor, bw_s, bh_s, faces, obs, relaxed) and self._hysteresis_ok(self.anchor, faces):
                return _Placement(self.anchor, s, relaxed, "anchor")

        # return to an earlier position rather than inventing a new one (fewer distinct caption heights)
        for top, s in reversed(self.history):
            bw_s, bh_s = _page_block(style, text, self.W, p, s)
            relaxed = top + bh_s > self._bottom(False)
            if self._valid(top, bw_s, bh_s, faces, obs, relaxed) and self._hysteresis_ok(top, faces):
                return self._commit(_Placement(top, s, relaxed, "history"))

        if not faces:
            base = self.anchor if self.anchor is not None else p.default_center_y_px * self.hs - bh / 2
            for top in self._scan_from(base, bh):
                if self._valid(top, bw, bh, [], obs, False):
                    return self._commit(_Placement(top, 1.0, False, "no_face"))
            for top in self._scan_from(base, bh):
                if self._valid(top, bw, bh, [], obs, True):
                    return self._commit(_Placement(top, 1.0, True, "no_face_relaxed"))
            return self._commit(self._least_bad(style, text, faces, obs))

        chin = max(f.chin for f in faces)
        forehead = min(f.forehead for f in faces)
        for relaxed in (False, True):
            for s in (1.0, 0.88, 0.76):
                bw_s, bh_s = _page_block(style, text, self.W, p, s)
                # the 40-120 px window first (preferred gap, then the rest of the window), then further
                # down (an obstacle such as a card pushed it) before any shrinking
                prefs = [chin + p.chin_gap_px * self.hs, chin + p.chin_gap_min_px * self.hs]
                prefs += [chin + g * self.hs for g in range(int(p.chin_gap_min_px), int(p.chin_gap_max_px) + 1, 8)]
                y = chin + (p.chin_gap_max_px + 8) * self.hs
                while y + bh_s <= self._bottom(relaxed):
                    prefs.append(y)
                    y += 8 * self.hs
                for top in prefs:
                    if self._valid(top, bw_s, bh_s, faces, obs, relaxed):
                        return self._commit(_Placement(top, s, relaxed, "below_chin"))
        # above the head (between the safe top and the forehead)
        for s in (1.0, 0.88, 0.76):
            bw_s, bh_s = _page_block(style, text, self.W, p, s)
            top = forehead - p.chin_gap_px * self.hs - bh_s
            if top >= self.zone.top and self._valid(top, bw_s, bh_s, faces, obs, False, need_gap=False):
                return self._commit(_Placement(top, s, False, "above_head"))
        return self._commit(self._least_bad(style, text, faces, obs))

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

    def _least_bad(self, style: CaptionStyle, text: str, faces: list[_FaceOut], obs: list) -> _Placement:
        s = 0.76
        bw, bh = _page_block(style, text, self.W, self.p, s)
        x0, x1 = self._x_range(bw)
        lo, hi = self.zone.top, self._bottom(True) - bh
        best, best_cost = hi, math.inf
        y = lo
        while y <= hi:
            block = (x0, y, x1, y + bh)
            cost = sum(3.0 * _overlap(block, f.protected(self.p)) for f in faces)
            cost += sum(_overlap(block, o) for o in obs)
            cost += 0.01 * abs(y - (max((f.chin for f in faces), default=y)))  # prefer near the chin
            if cost < best_cost:
                best, best_cost = y, cost
            y += 4 * self.hs
        return _Placement(best, s, best + bh > self._bottom(False), "least_bad")

    def _commit(self, pl: _Placement) -> _Placement:
        self.anchor = pl.top
        self.anchor_scale = pl.scale
        if pl.fallback not in ("least_bad", "history") and (pl.top, pl.scale) not in self.history:
            self.history.append((pl.top, pl.scale))
        return pl


# ============================================================================================== public API
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


def _place_pages(pages: list[TimelineCaptionPage], timeline: Timeline, index: TakeIndex, zone: SafeZone,
                 params: CaptionParams, positions: Sequence[tuple[str | None, float | None]] | None = None
                 ) -> list[TimelineCaptionPage]:
    placer = _Placer(timeline, index, zone, params)
    out: list[TimelineCaptionPage] = []
    for k, pg in enumerate(pages):
        pos, yn = positions[k] if positions is not None else (None, None)
        text = pg.text or " ".join(w.text for w in pg.words)
        pl = placer.place(pg.style, text, to_fraction(pg.out_start), to_fraction(pg.out_end), position=pos, y_norm=yn)
        _, bh = _page_block(pg.style, text, timeline.width, params, pl.scale)
        y_norm = min(max((pl.top + bh / 2) / timeline.height, 0.0), 1.0)
        style = pg.style
        if pl.scale < 0.999:
            style = style.model_copy(update={"size_px": max(24, round(style.size_px * pl.scale)),
                                             "stroke_px": round(style.stroke_px * pl.scale, 2)})
        out.append(pg.model_copy(update={"y_norm": round(y_norm, 5), "style": style}))
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
    (face track after framing transforms). Empty list = pass."""
    constants = load_constants(settings)
    params = params or load_caption_params(settings, constants)
    zone = safe_zone_for(platform, width=timeline.width, height=timeline.height, constants=constants)
    W, H = timeline.width, timeline.height
    bottom = H - (zone.caption_floor if relaxed_floor else zone.bottom)
    issues: list[dict[str, Any]] = []
    for pg in timeline.captions:
        text = pg.text or " ".join(w.text for w in pg.words)
        bw, bh = _page_block(pg.style, text, W, params)
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
                              params.face_sample_s)
        if any(_overlap(block, f.protected(params)) > 0 for f in faces):
            issues.append({"page": pg.page_id, "kind": "covers_face", "refs": refs,
                           "block": [round(v, 1) for v in block]})
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

