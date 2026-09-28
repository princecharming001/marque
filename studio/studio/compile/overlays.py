"""Overlay layer: Timeline → ``overlay_props.json`` → Remotion render → ``overlays.mov`` (ProRes 4444 + alpha).

Captions, text overlays (hook title, callout, numbered list, lower third, label, CTA), designed cards
(title / number / stat / list / steps / quote / comparison) and pointing graphics (arrow, circle, box,
underline) are drawn by the Remotion project in ``studio/overlay/`` (composition ``Overlay``) on a
transparent 1080x1920 canvas at the timeline's fps and exact frame count. The master stage composites
this layer over the A-roll once.

Quality choices
---------------
* **ProRes 4444 with alpha, PNG frame capture, BT.709.** PNG keeps the alpha channel and avoids JPEG
  ringing around caption strokes; ``yuva444p10le`` keeps chroma at full resolution (4:2:0 would smear
  the thin coloured edge between a white glyph and its black stroke); ``--color-space=bt709`` because
  Remotion's v4 default matrix is BT.601, which would shift every caption colour after the BT.709
  master encode. The render is muted (audio is mixed separately).
* **Straight alpha.** Chrome captures un-premultiplied RGBA; ProRes 4444 stores it straight, which is
  what ffmpeg's ``overlay`` filter expects by default (no ``premultiplied`` flag in the master).
* **Frame-exact props.** Every time is converted once from the timeline's rational output times to
  integer frames (start inclusive, end exclusive); fps/duration/size come from the props through
  ``calculateMetadata``, so the overlay can never drift from the A-roll.
* **Real glyph metrics.** Fonts are vendored (Montserrat, Inter, Anton, TikTok Sans, Archivo, Noto Color
  Emoji; all OFL, from @remotion/google-fonts) and loaded before the first frame; line fitting and
  safe-zone clamping use measured widths, not estimates.
* **Bundle once.** The Remotion project is bundled into ``overlay/.cache/bundle-<hash>`` keyed by the
  content of its sources, fonts and lockfile, under a file lock, and reused by every render. Rendered
  layers are cached per job by the hash of their props, so champion-loop revisions that do not touch
  text re-use the previous file.
* **Verified output.** After every render the file is probed: ProRes 4444, a pixel format with alpha,
  exact frame count, size, frame rate and BT.709 tags, plus a decoded alpha plane that really contains
  transparency (an opaque "alpha" layer would black out the video in the master).

Graphics (arrow/circle/box/underline) are not in the CutDocument model yet; callers pass them as
:class:`OverlayGraphic` items in output time.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from studio.compile.captions import (
    CaptionParams,
    SafeZone,
    _apply_case,
    load_caption_params,
    load_constants,
    safe_zone_for,
)
from studio.compile.models import Timeline, TimelineCaptionPage, TimelineInsert, TimelineText
from studio.doc.model import CardSpec, TextStyle
from studio.timebase import Rational, frame_index, normalize_fps, to_fraction

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = [
    "OverlayRenderError", "OverlayGraphic", "OverlayProps", "OverlayCheck", "COMPOSITION_ID",
    "overlay_props", "build_overlay_props", "has_overlay_content", "render_overlays", "render_overlay_props",
    "ensure_overlay_project", "bundle_overlay", "verify_overlay", "validate_props_with_schema",
    "default_text_anchor", "transparent_probe_frame", "bundle_key", "HOUSE_ACCENT", "HOUSE_CARD_BACKGROUND",
]

COMPOSITION_ID = "Overlay"
HOUSE_ACCENT = "#FFD400"
HOUSE_CARD_BACKGROUND = "radial-gradient(120% 80% at 50% 28%, #22222a 0%, #0c0c0f 72%)"
HOUSE_DISPLAY_FONT = "Anton"
KNOWN_FONTS = ("Montserrat", "Inter", "Anton", "TikTok Sans", "Archivo")
_TEXT_KINDS = ("hook_title", "callout", "list", "lower_third", "label", "cta")
_TEXT_ANIMS = ("none", "pop", "fade", "slide", "typewriter")
_CAPTION_ANIMS = ("none", "pop", "karaoke", "fade", "slide")
_REF_W, _REF_H = 1080, 1920


class OverlayRenderError(RuntimeError):
    """The Remotion render failed or produced a file that does not meet the overlay contract."""


# ============================================================================================== props models
class _P(BaseModel):
    """Props models mirror ``overlay/src/schema.ts`` (camelCase on the wire)."""

    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)


class SafeZoneProps(_P):
    top: float
    bottom: float
    left: float
    right: float


class CaptionLayoutProps(_P):
    x_center_px: float
    max_width_px: float = Field(gt=0)
    left_px: float
    right_px: float
    min_scale: float = Field(ge=0.3, le=1.0)


class CaptionStyleProps(_P):
    font: str
    weight: int = Field(ge=100, le=1000)
    size_px: float = Field(gt=0)
    stroke_px: float = Field(ge=0)
    stroke_color: str
    color: str
    highlight_color: str
    background: str | None = None
    animation: Literal["none", "pop", "karaoke", "fade", "slide"] = "pop"
    shadow: bool = True
    highlight_lead_frames: int = Field(ge=0, le=12)
    max_lines: int = Field(ge=1, le=3)
    letter_spacing_em: float = 0.0
    line_height: float = 1.12


class CaptionWordProps(_P):
    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    emphasis: bool = False


class CaptionPageProps(_P):
    id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    words: list[CaptionWordProps] = Field(min_length=1)
    y_norm: float = Field(ge=0.0, le=1.0)
    style: CaptionStyleProps


class TextStyleProps(_P):
    font: str
    weight: int = Field(ge=100, le=1000)
    size_px: float = Field(gt=0)
    color: str
    background: str | None = None
    stroke_px: float = Field(ge=0)
    stroke_color: str
    align: Literal["left", "center", "right"] = "center"


class TextOverlayProps(_P):
    id: str
    kind: Literal["hook_title", "callout", "list", "lower_third", "label", "cta"]
    text: str
    items: list[str] = Field(default_factory=list)
    item_starts: list[int | None] = Field(default_factory=list)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    x_norm: float = Field(ge=0.0, le=1.0)
    y_norm: float = Field(ge=0.0, le=1.0)
    style: TextStyleProps
    accent: str
    animation: Literal["none", "pop", "fade", "slide", "typewriter"] = "pop"
    max_width_px: float = Field(gt=0)


class TransitionProps(_P):
    kind: Literal["cut", "fade", "dissolve", "slide", "zoom", "whip"] = "cut"
    frames: int = Field(default=0, ge=0)


class CardProps(_P):
    id: str
    template: Literal["title", "number", "stat", "list", "quote", "steps", "comparison"]
    title: str = ""
    subtitle: str = ""
    body: str = ""
    items: list[str] = Field(default_factory=list)
    number: str | None = None
    unit: str | None = None
    accent: str
    background: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    rect: tuple[float, float, float, float] | None = None
    transition_in: TransitionProps = Field(default_factory=TransitionProps)
    transition_out: TransitionProps = Field(default_factory=TransitionProps)
    font: str
    display_font: str
    reserve_bottom_px: float = Field(default=0.0, ge=0)


class PointProps(_P):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class GraphicProps(_P):
    id: str
    kind: Literal["arrow", "circle", "box", "underline"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(ge=0.0, le=1.0)
    h: float = Field(ge=0.0, le=1.0)
    from_: PointProps | None = Field(default=None, alias="from")
    to: PointProps | None = None
    color: str
    stroke_px: float = Field(gt=0)
    curvature: float = Field(default=0.0, ge=-1.0, le=1.0)
    draw_frames: int = Field(ge=1)


class ThemeProps(_P):
    accent: str
    font: str
    display_font: str
    card_background: str
    text_color: str = "#FFFFFF"


class OverlayProps(_P):
    version: Literal[1] = 1
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    duration_in_frames: int = Field(gt=0)
    safe: SafeZoneProps
    caption_layout: CaptionLayoutProps
    theme: ThemeProps
    captions: list[CaptionPageProps] = Field(default_factory=list)
    texts: list[TextOverlayProps] = Field(default_factory=list)
    cards: list[CardProps] = Field(default_factory=list)
    graphics: list[GraphicProps] = Field(default_factory=list)
    debug: bool = False

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class OverlayGraphic(BaseModel):
    """A pointing graphic in output time (normalized output coordinates)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["arrow", "circle", "box", "underline"]
    out_start: Rational
    out_end: Rational
    x: float = Field(default=0.5, ge=0.0, le=1.0)
    y: float = Field(default=0.5, ge=0.0, le=1.0)
    w: float = Field(default=0.2, ge=0.0, le=1.0)
    h: float = Field(default=0.1, ge=0.0, le=1.0)
    from_pt: tuple[float, float] | None = None
    to_pt: tuple[float, float] | None = None
    color: str | None = None
    stroke_px: float = Field(default=10.0, gt=0, le=60)
    curvature: float = Field(default=0.2, ge=-1.0, le=1.0)
    draw_ms: int = Field(default=280, ge=33, le=2000)


# ============================================================================================== helpers
def _luminance(colour: str) -> float | None:
    m = re.fullmatch(r"#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?|#([0-9a-fA-F]{3})", colour.strip())
    if not m:
        return None
    h = m.group(1) or "".join(c * 2 for c in m.group(3))

    def lin(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _ink_on(background: str) -> str:
    """Near-black or white text, whichever contrasts more with a (hex) background."""
    lum = _luminance(background)
    if lum is None:
        return "#FFFFFF"
    return "#111111" if (lum + 0.05) / 0.0556 >= 1.05 / (lum + 0.05) else "#FFFFFF"


def _font(name: str | None) -> str:
    if not name:
        return "Montserrat"
    for f in KNOWN_FONTS:
        if name.strip().lower().replace(" ", "") == f.lower().replace(" ", ""):
            return f
    return "Montserrat"


def default_text_anchor(kind: str, position: str | tuple[float, float] | None, zone: SafeZone, width: int = _REF_W,
                        height: int = _REF_H) -> tuple[float, float]:
    """Normalized anchor (centre) for a text overlay from a ``TextPosition`` keyword.

    Hook titles live in the upper band (y 288-600 at 1920), lower thirds just above the caption band,
    callouts/labels in the upper third; everything is centred on the band's centre line (x ≈ 476 for the
    cross-platform zone), not the frame centre, so wide text never runs under the right rail.
    """
    if isinstance(position, tuple):
        return position
    x0, y0, x1, y1 = zone.band(width, height)
    cx = (x0 + x1) / 2 / width
    band = y1 - y0
    table = {"top": y0 + 0.12 * band, "upper_third": y0 + 0.2 * band, "center": y0 + 0.5 * band,
             "lower_third": y0 + 0.78 * band, "bottom": y0 + 0.92 * band}
    if position in table:
        return cx, table[position] / height
    default = {"hook_title": y0 + 0.14 * band, "callout": y0 + 0.24 * band, "list": y0 + 0.3 * band,
               "lower_third": y0 + 0.72 * band, "label": y0 + 0.24 * band, "cta": y0 + 0.6 * band}
    return cx, default.get(kind, y0 + 0.2 * band) / height


def _house_text_style(kind: str, st: TextStyle, accent: str, ws: float) -> TextStyleProps:
    """House look per kind when the document leaves the style at its defaults; otherwise the given
    style, made legible (a stroke when there is neither a box nor a stroke)."""
    size = st.size_px * ws
    if st == TextStyle():
        looks: dict[str, dict[str, Any]] = {
            "hook_title": dict(weight=900, size=84, color="#FFFFFF", background=None, stroke=8.0),
            "callout": dict(weight=800, size=54, color=_ink_on(accent), background=accent, stroke=0.0),
            "list": dict(weight=800, size=62, color="#FFFFFF", background="rgba(10,10,12,0.62)", stroke=0.0),
            "lower_third": dict(weight=800, size=54, color="#FFFFFF", background="rgba(12,12,14,0.78)", stroke=0.0),
            "label": dict(weight=800, size=50, color="#FFFFFF", background=None, stroke=5.0),
            "cta": dict(weight=900, size=60, color=_ink_on(accent), background=accent, stroke=0.0),
        }
        lk = looks.get(kind, looks["label"])
        return TextStyleProps(font=_font(st.font), weight=lk["weight"], size_px=lk["size"] * ws, color=lk["color"],
                              background=lk["background"], stroke_px=lk["stroke"] * ws, stroke_color="#000000",
                              align="left" if kind == "list" else "center")
    stroke = st.stroke_px * ws
    if st.background is None and stroke <= 0 and kind not in ("lower_third", "list"):
        stroke = round(0.09 * size, 2)
    color = st.color
    if color.strip().lower() == "auto":
        color = _ink_on(st.background or "#000000")
    return TextStyleProps(font=_font(st.font), weight=st.weight, size_px=size, color=color, background=st.background,
                          stroke_px=stroke, stroke_color=st.stroke_color, align=st.align)


_WORD_RE = re.compile(r"[^\w']+")


def _norm_word(s: str) -> str:
    return _WORD_RE.sub("", s.lower().replace("’", "'"))


def _item_starts(t: TimelineText, timeline: Timeline, index: TakeIndex | None, fps: Fraction) -> list[int | None]:
    """Reveal frame per list item: the onset of the item's first word(s) spoken inside the text's span
    (in order), 2 frames early; None when not found (the renderer staggers those)."""
    if not t.items or index is None:
        return [None] * len(t.items)
    a, b = to_fraction(t.out_start), to_fraction(t.out_end)
    spoken: list[tuple[Fraction, str]] = []
    for wid, span in timeline.word_map.items():
        if span is None or not index.has_word(wid):
            continue
        s = to_fraction(span.out_start)
        if a <= s < b:
            spoken.append((s, _norm_word(index.word(wid).text)))
    spoken.sort()
    out: list[int | None] = []
    cursor = 0
    for item in t.items:
        toks = [_norm_word(x) for x in item.split() if _norm_word(x)]
        found = None
        if toks:
            for k in range(cursor, len(spoken)):
                if spoken[k][1] == toks[0] and (len(toks) < 2 or k + 1 >= len(spoken) or spoken[k + 1][1] == toks[1]
                                               or len(toks[0]) > 3):
                    found = k
                    break
        if found is None:
            out.append(None)
        else:
            cursor = found + 1
            out.append(max(0, frame_index(spoken[found][0], fps, "floor") - 2))
    return out


def _frames(t: Fraction | int | float | str, fps: Fraction, total: int) -> int:
    return min(max(frame_index(to_fraction(t), fps, "round"), 0), total)


def _transition(tr: Any, fps: Fraction) -> TransitionProps:
    frames = round(tr.ms * float(fps) / 1000.0) if tr.kind != "cut" else 0
    if tr.kind != "cut" and frames == 0:
        frames = max(1, round(0.25 * float(fps)))
    return TransitionProps(kind=tr.kind, frames=frames)


def _card_rect(ins: TimelineInsert) -> tuple[float, float, float, float] | None:
    if ins.mode in ("full", "card"):
        return None
    if ins.rect is not None:
        return ins.rect
    if ins.mode == "split_top":
        return (0.0, 0.0, 1.0, 0.5)
    if ins.mode == "split_bottom":
        return (0.0, 0.5, 1.0, 0.5)
    return (0.55, 0.06, 0.4, 0.24)  # pip default: top right, inside the band


def _caption_props(pg: TimelineCaptionPage, k: int, fps: Fraction, total: int, ws: float) -> CaptionPageProps | None:
    st = pg.style
    start, end = _frames(pg.out_start, fps, total), _frames(pg.out_end, fps, total)
    if end <= start:
        return None
    words = pg.words
    if pg.text:
        toks = pg.text.split()
        if len(toks) == len(words):
            pairs = list(zip(toks, words, strict=True))
        else:  # override with a different token count: spread tokens over the words' timing
            pairs = [(tok, words[min(len(words) - 1, i * len(words) // max(1, len(toks)))]) for i, tok in
                     enumerate(toks)]
    else:
        pairs = [(w.text, w) for w in words]
    wp = []
    for text, w in pairs:
        ws_f = min(max(frame_index(to_fraction(w.out_start), fps, "floor"), 0), total)
        we_f = min(max(frame_index(to_fraction(w.out_end), fps, "ceil"), ws_f), total)
        wp.append(CaptionWordProps(text=_apply_case(text, st.case), start=ws_f, end=we_f, emphasis=w.emphasis))
    if not wp:
        return None
    anim = st.animation if st.animation in _CAPTION_ANIMS else "pop"
    style = CaptionStyleProps(
        font=_font(st.font), weight=st.weight, size_px=st.size_px * ws, stroke_px=st.stroke_px * ws,
        stroke_color=st.stroke_color, color=st.color, highlight_color=st.highlight_color, background=st.background,
        animation=anim, shadow=st.shadow,
        highlight_lead_frames=min(12, round(st.highlight_lead_frames * float(fps) / 30)),
        max_lines=min(3, st.lines), letter_spacing_em=0.0, line_height=1.12,
    )
    return CaptionPageProps(id=pg.page_id or f"p{k:03d}", start=start, end=end, words=wp, y_norm=pg.y_norm, style=style)


# ============================================================================================== props
def build_overlay_props(timeline: Timeline, *, index: TakeIndex | None = None,
                        graphics: Sequence[OverlayGraphic] | None = None,
                        platforms: str | Iterable[str] | None = None, settings: Settings | None = None,
                        params: CaptionParams | None = None, theme: dict[str, str] | None = None,
                        debug: bool = False) -> OverlayProps:
    """Frame-quantized props for the ``Overlay`` composition (see module docstring)."""
    constants = load_constants(settings)
    params = params or load_caption_params(settings, constants)
    fps = normalize_fps(timeline.fps)
    total = max(1, timeline.frame_count)
    W, H = timeline.width, timeline.height
    ws = W / _REF_W
    zone = safe_zone_for(platforms, width=W, height=H, constants=constants)

    first_style = timeline.captions[0].style if timeline.captions else None
    th = {
        "accent": first_style.highlight_color if first_style else HOUSE_ACCENT,
        "font": _font(first_style.font if first_style else "Montserrat"),
        "display_font": HOUSE_DISPLAY_FONT,
        "card_background": HOUSE_CARD_BACKGROUND,
        "text_color": "#FFFFFF",
    }
    th.update(theme or {})
    theme_p = ThemeProps(**th)

    captions = [c for k, pg in enumerate(sorted(timeline.captions, key=lambda p: p.out_start), start=1)
                if (c := _caption_props(pg, k, fps, total, ws)) is not None]

    band_w = W - zone.left - zone.right
    texts: list[TextOverlayProps] = []
    for t in timeline.texts:
        start, end = _frames(t.out_start, fps, total), _frames(t.out_end, fps, total)
        if end <= start:
            continue
        kind = t.kind if t.kind in _TEXT_KINDS else "label"
        st = _house_text_style(kind, t.style, theme_p.accent, ws)
        case = t.style.case
        texts.append(TextOverlayProps(
            id=t.text_id, kind=kind, text=_apply_case(t.text, case), items=[_apply_case(i, case) for i in t.items],
            item_starts=_item_starts(t, timeline, index, fps), start=start, end=end,
            x_norm=min(max(t.x_norm, 0.0), 1.0), y_norm=min(max(t.y_norm, 0.0), 1.0), style=st,
            accent=theme_p.accent, animation=t.animation if t.animation in _TEXT_ANIMS else "pop",
            max_width_px=band_w * (0.96 if kind == "hook_title" else 0.9),
        ))

    reserve = params.card_reserve_px * H / _REF_H
    cards: list[CardProps] = []
    for ins in timeline.inserts:
        if not isinstance(ins.asset, CardSpec):
            continue
        spec = ins.asset
        start, end = _frames(ins.out_start, fps, total), _frames(ins.out_end, fps, total)
        if end <= start:
            continue
        overlaps_captions = any(c.start < end and start < c.end for c in captions)
        cards.append(CardProps(
            id=ins.insert_id, template=spec.template, title=spec.title, subtitle=spec.subtitle, body=spec.body,
            items=list(spec.items), number=spec.number, unit=spec.unit, accent=spec.accent or theme_p.accent,
            background=spec.background or theme_p.card_background, start=start, end=end, rect=_card_rect(ins),
            transition_in=_transition(ins.transition_in, fps), transition_out=_transition(ins.transition_out, fps),
            font=theme_p.font, display_font=theme_p.display_font,
            reserve_bottom_px=reserve if overlaps_captions and _card_rect(ins) is None else 0.0,
        ))

    gfx: list[GraphicProps] = []
    for g in graphics or ():
        start, end = _frames(g.out_start, fps, total), _frames(g.out_end, fps, total)
        if end <= start:
            continue
        if g.kind == "arrow" and (g.from_pt is None or g.to_pt is None):
            raise ValueError(f"graphic {g.id}: an arrow needs from_pt and to_pt")
        gfx.append(GraphicProps(
            id=g.id, kind=g.kind, start=start, end=end, x=g.x, y=g.y, w=g.w, h=g.h,
            from_=PointProps(x=g.from_pt[0], y=g.from_pt[1]) if g.from_pt else None,
            to=PointProps(x=g.to_pt[0], y=g.to_pt[1]) if g.to_pt else None,
            color=g.color or theme_p.accent, stroke_px=g.stroke_px * ws, curvature=g.curvature,
            draw_frames=max(1, round(g.draw_ms * float(fps) / 1000.0)),
        ))

    left, right = zone.left, W - zone.right
    layout = CaptionLayoutProps(x_center_px=W / 2, max_width_px=min(params.max_line_px * ws, right - left),
                                left_px=left, right_px=right, min_scale=params.min_font_scale)
    return OverlayProps(
        width=W, height=H, fps=float(fps), duration_in_frames=total,
        safe=SafeZoneProps(top=zone.top, bottom=zone.bottom, left=zone.left, right=zone.right),
        caption_layout=layout, theme=theme_p, captions=captions, texts=texts, cards=cards, graphics=gfx,
        debug=debug,
    )


def overlay_props(timeline: Timeline, *, job: Job | None = None, index: TakeIndex | None = None,
                  graphics: Sequence[OverlayGraphic] | None = None, platforms: str | Iterable[str] | None = None,
                  settings: Settings | None = None, debug: bool = False) -> dict[str, Any]:
    """``overlay_props.json`` content (camelCase, frame-quantized) for the Remotion composition.

    With ``job`` the Take Index (for list-item reveal timing) and the document's deliverable platforms
    (for the safe zone) are loaded when not given.
    """
    index, platforms = _job_context(job, timeline, index, platforms)
    return build_overlay_props(timeline, index=index, graphics=graphics, platforms=platforms, settings=settings,
                               debug=debug).to_json_dict()


def has_overlay_content(props: OverlayProps | dict[str, Any]) -> bool:
    d = props.to_json_dict() if isinstance(props, OverlayProps) else props
    return any(d.get(k) for k in ("captions", "texts", "cards", "graphics"))


def _job_context(job: Job | None, timeline: Timeline, index: TakeIndex | None,
                 platforms: str | Iterable[str] | None) -> tuple[TakeIndex | None, str | Iterable[str] | None]:
    if job is None:
        return index, platforms
    from studio.jobs import JobError

    if index is None:
        with contextlib.suppress(JobError, OSError, ValueError):
            index = job.load_index()
    if platforms is None:
        with contextlib.suppress(JobError, OSError, ValueError):
            doc = job.load_doc(timeline.doc_version)
            platforms = sorted({d.platform for d in doc.deliverables}) or None
    return index, platforms


# ============================================================================================== project / bundle
def _settings(settings: Settings | None) -> Settings:
    if settings is not None:
        return settings
    from studio.config import get_settings

    return get_settings()


def _overlay_dir(settings: Settings | None) -> Path:
    return Path(_settings(settings).overlay_dir)


def _remotion_bin(root: Path) -> list[str]:
    local = root / "node_modules" / ".bin" / "remotion"
    if local.exists():
        return [str(local)]
    return ["npx", "--no-install", "remotion"]


def _child_env() -> dict[str, str]:
    """Environment for node: no provider secrets (the overlay never needs them)."""
    from studio.config import KEY_ENV_NAMES

    drop = {n for names in KEY_ENV_NAMES.values() for n in names} | {"STUDIO_ENV_FILE"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.setdefault("NODE_NO_WARNINGS", "1")
    return env


def _run(cmd: list[str], cwd: Path, *, timeout: float, log: Path | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=_child_env(),
                          check=False)
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"$ {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}\n[exit {proc.returncode}]\n")
    return proc


def ensure_overlay_project(settings: Settings | None = None, *, install: bool = True) -> Path:
    """Make sure the Remotion project is installed, its fonts are vendored and headless Chrome exists.

    ``install=False`` only checks (raises :class:`OverlayRenderError` when something is missing).
    Installing needs the network once; renders afterwards are offline.
    """
    root = _overlay_dir(settings)
    if not (root / "package.json").exists():
        raise OverlayRenderError(f"overlay project not found at {root}")
    steps: list[tuple[bool, list[str], str]] = [
        ((root / "node_modules" / ".bin" / "remotion").exists(),
         ["npm", "ci", "--no-audit", "--no-fund"], "npm ci"),
        (_fonts_ok(root), ["node", "scripts/fetch-fonts.mjs"], "fetch fonts"),
        ((root / "node_modules" / ".remotion").exists(), [*_remotion_bin(root), "browser", "ensure"],
         "headless Chrome"),
    ]
    for ok, cmd, what in steps:
        if ok:
            continue
        if not install:
            raise OverlayRenderError(f"overlay project incomplete: {what} missing (run ensure_overlay_project)")
        proc = _run(cmd, root, timeout=900)
        if proc.returncode != 0:
            raise OverlayRenderError(f"{what} failed: {proc.stderr[-2000:]}")
    return root


def _fonts_ok(root: Path) -> bool:
    manifest = root / "src" / "fonts-manifest.json"
    if not manifest.exists():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return all((root / "public" / f["file"]).exists() for f in data.get("fonts", []))


def _bundle_inputs(root: Path) -> list[Path]:
    files: list[Path] = []
    for sub in ("src", "public"):
        files.extend(p for p in sorted((root / sub).rglob("*")) if p.is_file())
    for name in ("package-lock.json", "package.json", "remotion.config.ts", "tsconfig.json"):
        if (root / name).exists():
            files.append(root / name)
    return files


def bundle_key(settings: Settings | None = None) -> str:
    """Content hash of everything that goes into the bundle."""
    root = _overlay_dir(settings)
    h = hashlib.sha256()
    for p in _bundle_inputs(root):
        h.update(str(p.relative_to(root)).encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:20]


@contextlib.contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def bundle_overlay(settings: Settings | None = None, *, force: bool = False, log: Path | None = None) -> Path:
    """Bundle the Remotion project once per content hash (``overlay/.cache/bundle-<hash>``)."""
    root = ensure_overlay_project(settings)
    cache = root / ".cache"
    key = bundle_key(settings)
    target = cache / f"bundle-{key}"
    with _file_lock(cache / "bundle.lock"):
        if target.exists() and (target / "index.html").exists() and not force:
            return target
        tmp = Path(tempfile.mkdtemp(prefix="bundle-tmp-", dir=cache))
        try:
            proc = _run([*_remotion_bin(root), "bundle", "src/index.ts", "--out-dir", str(tmp), "--public-dir",
                         "public", "--log=error"], root, timeout=900, log=log)
            if proc.returncode != 0 or not (tmp / "index.html").exists():
                raise OverlayRenderError(f"remotion bundle failed: {(proc.stderr or proc.stdout)[-2000:]}")
            if target.exists():
                shutil.rmtree(target)
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
        for old in cache.glob("bundle-*"):  # keep the cache lean: only the current bundle
            if old != target and old.is_dir() and not old.name.startswith("bundle-tmp-"):
                shutil.rmtree(old, ignore_errors=True)
    return target


# ============================================================================================== render + verify
@dataclass
class OverlayCheck:
    path: Path
    codec: str
    profile: str
    pix_fmt: str
    width: int
    height: int
    frames: int
    fps: Fraction
    color_space: str | None
    alpha_min: int
    alpha_max: int
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _ffprobe(settings: Settings | None) -> str:
    return _settings(settings).ffprobe


def _ffmpeg(settings: Settings | None) -> str:
    return _settings(settings).ffmpeg


def _alpha_stats(path: Path, frame: int, width: int, height: int, settings: Settings | None) -> tuple[int, int]:
    """(min, max) of the decoded 8-bit alpha plane of one frame."""
    cmd = [_ffmpeg(settings), "-v", "error", "-i", str(path), "-vf",
           f"select=eq(n\\,{frame}),alphaextract,format=gray", "-frames:v", "1", "-f", "rawvideo", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    data = proc.stdout
    if proc.returncode != 0 or len(data) < width * height:
        raise OverlayRenderError(f"could not decode the alpha plane of {path.name} frame {frame}: "
                                 f"{proc.stderr.decode(errors='replace')[-500:]}")
    import numpy as np

    a = np.frombuffer(data[: width * height], dtype=np.uint8)
    return int(a.min()), int(a.max())


def verify_overlay(path: str | os.PathLike[str], *, frames: int | None = None, width: int | None = None,
                   height: int | None = None, fps: Fraction | str | float | None = None,
                   content_frame: int | None = None, settings: Settings | None = None,
                   raise_on_error: bool = True) -> OverlayCheck:
    """Probe an overlay file against the contract (see module docstring).

    ``content_frame`` is the frame whose decoded alpha plane must contain transparent pixels (default:
    the middle frame; ``-1`` skips the pixel check, e.g. when every frame is an opaque card)."""
    p = Path(path)
    proc = subprocess.run([_ffprobe(settings), "-v", "error", "-select_streams", "v:0", "-count_packets",
                           "-show_streams", "-of", "json", str(p)], capture_output=True, text=True, timeout=120,
                          check=False)
    if proc.returncode != 0:
        raise OverlayRenderError(f"ffprobe failed on {p}: {proc.stderr[-500:]}")
    streams = json.loads(proc.stdout).get("streams", [])
    if not streams:
        raise OverlayRenderError(f"{p} has no video stream")
    s = streams[0]
    n = int(s.get("nb_read_packets") or s.get("nb_frames") or 0)
    rate = Fraction(s.get("r_frame_rate", "0/1"))
    chk = OverlayCheck(path=p, codec=s.get("codec_name", ""), profile=str(s.get("profile", "")),
                       pix_fmt=s.get("pix_fmt", ""), width=int(s.get("width", 0)), height=int(s.get("height", 0)),
                       frames=n, fps=rate, color_space=s.get("color_space"), alpha_min=-1, alpha_max=-1)
    if chk.codec != "prores":
        chk.problems.append(f"codec {chk.codec} != prores")
    if "4444" not in chk.profile:
        chk.problems.append(f"profile {chk.profile} is not 4444")
    if not (chk.pix_fmt.startswith("yuva") or "rgba" in chk.pix_fmt or chk.pix_fmt.startswith("gbrap")):
        chk.problems.append(f"pix_fmt {chk.pix_fmt} has no alpha")
    if frames is not None and n != frames:
        chk.problems.append(f"{n} frames != expected {frames}")
    if width is not None and chk.width != width:
        chk.problems.append(f"width {chk.width} != {width}")
    if height is not None and chk.height != height:
        chk.problems.append(f"height {chk.height} != {height}")
    if fps is not None and rate != normalize_fps(fps):
        chk.problems.append(f"frame rate {rate} != {normalize_fps(fps)}")
    if chk.color_space not in (None, "bt709"):
        chk.problems.append(f"colour space {chk.color_space} != bt709")
    if not chk.problems and content_frame != -1:
        k = content_frame if content_frame is not None else max(0, n // 2)
        chk.alpha_min, chk.alpha_max = _alpha_stats(p, min(k, max(n - 1, 0)), chk.width, chk.height, settings)
        if chk.alpha_min != 0:
            chk.problems.append(f"frame {k} has no transparent pixels (alpha min {chk.alpha_min}): opaque layer")
    if chk.problems and raise_on_error:
        raise OverlayRenderError(f"overlay check failed for {p.name}: " + "; ".join(chk.problems))
    return chk


def _concurrency() -> int:
    n = os.cpu_count() or 4
    return max(2, min(8, n - 2))


def render_overlay_props(props: OverlayProps | dict[str, Any] | str | os.PathLike[str],
                         out_path: str | os.PathLike[str], *, settings: Settings | None = None,
                         concurrency: int | None = None, log: Path | None = None, verify: bool = True) -> Path:
    """Render props (model, dict or a json file) to a ProRes 4444 alpha ``.mov`` and verify it."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(props, (str, os.PathLike)):
        data = json.loads(Path(props).read_text(encoding="utf-8"))
    elif isinstance(props, OverlayProps):
        data = props.to_json_dict()
    else:
        data = props
    model = OverlayProps.model_validate(data)
    bundle = bundle_overlay(settings, log=log)
    root = _overlay_dir(settings)
    with tempfile.TemporaryDirectory(prefix="ovl-") as td:
        props_file = Path(td) / "props.json"
        props_file.write_text(json.dumps(model.to_json_dict()), encoding="utf-8")
        tmp_out = out.with_name(f".{out.stem}.rendering{out.suffix}")
        cmd = [*_remotion_bin(root), "render", str(bundle), COMPOSITION_ID, str(tmp_out),
               f"--props={props_file}", "--codec=prores", "--prores-profile=4444", "--pixel-format=yuva444p10le",
               "--image-format=png", "--color-space=bt709", "--muted", "--overwrite",
               f"--concurrency={concurrency or _concurrency()}", "--timeout=120000", "--log=error"]
        timeout = max(900.0, model.duration_in_frames * 1.5)
        proc = _run(cmd, root, timeout=timeout, log=log)
        if proc.returncode != 0 or not tmp_out.exists():
            with contextlib.suppress(FileNotFoundError):
                tmp_out.unlink()
            raise OverlayRenderError(f"remotion render failed ({proc.returncode}): "
                                     f"{(proc.stderr or proc.stdout)[-3000:]}")
        os.replace(tmp_out, out)
    if verify:
        probe = transparent_probe_frame(model)
        verify_overlay(out, frames=model.duration_in_frames, width=model.width, height=model.height,
                       fps=Fraction(model.fps).limit_denominator(1001), settings=settings,
                       content_frame=-1 if probe is None else probe)
    return out


def transparent_probe_frame(props: OverlayProps) -> int | None:
    """A frame that must contain transparent pixels (not under an opaque full-frame card), preferring
    one with content drawn on it; None when every frame is covered by an opaque card."""
    n = props.duration_in_frames
    opaque = [(c.start, c.end) for c in props.cards
              if c.rect is None and c.background.strip().lower() != "transparent"]

    def covered(f: int) -> bool:
        return any(a <= f < b for a, b in opaque)

    drawn = sorted({(c.start + c.end) // 2 for c in props.captions} | {(t.start + t.end) // 2 for t in props.texts})
    for f in drawn:
        if not covered(f):
            return f
    for k in range(1, 9):
        f = min(n - 1, n * k // 9)
        if not covered(f):
            return f
    return None


def _props_digest(props: OverlayProps, settings: Settings | None) -> str:
    h = hashlib.sha256(json.dumps(props.to_json_dict(), sort_keys=True).encode())
    h.update(bundle_key(settings).encode())
    return h.hexdigest()[:24]


def render_overlays(job: Job, timeline: Timeline, out_dir: str | os.PathLike[str], *, preview: bool = False,
                    index: TakeIndex | None = None, graphics: Sequence[OverlayGraphic] | None = None,
                    platforms: str | Iterable[str] | None = None, settings: Settings | None = None,
                    debug: bool = False) -> Path | None:
    """Render ``overlays.mov`` (ProRes 4444 + alpha) into ``out_dir``; None when there is nothing to draw.

    Also writes ``overlay_props.json`` next to it. ``preview`` renders the identical layer (overlays
    are cheap next to the A-roll, and the per-props cache makes the following final free).
    """
    del preview  # same quality for previews and finals (see docstring)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index, platforms = _job_context(job, timeline, index, platforms)
    props = build_overlay_props(timeline, index=index, graphics=graphics, platforms=platforms, settings=settings,
                                debug=debug)
    from studio.jobs import write_json_atomic

    write_json_atomic(out_dir / "overlay_props.json", props.to_json_dict())
    if not has_overlay_content(props):
        return None
    out = out_dir / "overlays.mov"
    log = job.log_path("overlay_render")
    digest = _props_digest(props, settings)
    cache_dir = job.renders_dir / "_overlay_cache"
    cached = cache_dir / f"{digest}.mov"
    fps = normalize_fps(timeline.fps)
    probe = transparent_probe_frame(props)
    probe_arg = -1 if probe is None else probe
    if cached.exists():
        try:
            verify_overlay(cached, frames=props.duration_in_frames, width=props.width, height=props.height, fps=fps,
                           settings=settings, content_frame=-1)
            _link_or_copy(cached, out)
            job.trace("overlay_render", cached=True, frames=props.duration_in_frames, digest=digest)
            return out
        except OverlayRenderError:
            cached.unlink(missing_ok=True)
    render_overlay_props(props, out, settings=settings, log=log, verify=False)
    verify_overlay(out, frames=props.duration_in_frames, width=props.width, height=props.height, fps=fps,
                   settings=settings, content_frame=probe_arg)
    cache_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        _link_or_copy(out, cached)
    job.trace("overlay_render", cached=False, frames=props.duration_in_frames, digest=digest,
              captions=len(props.captions), texts=len(props.texts), cards=len(props.cards),
              graphics=len(props.graphics))
    return out


def _link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


# ============================================================================================== schema check
def validate_props_with_schema(props: OverlayProps | dict[str, Any] | str | os.PathLike[str], *,
                               settings: Settings | None = None) -> tuple[bool, list[str]]:
    """Validate props against the composition's real zod schema (runs ``scripts/validate-props.mts``)."""
    root = _overlay_dir(settings)
    with tempfile.TemporaryDirectory(prefix="ovl-val-") as td:
        if isinstance(props, (str, os.PathLike)):
            path = Path(props)
        else:
            data = props.to_json_dict() if isinstance(props, OverlayProps) else props
            path = Path(td) / "props.json"
            path.write_text(json.dumps(data), encoding="utf-8")
        proc = _run(["node", "scripts/validate-props.mts", str(path)], root, timeout=120)
    try:
        res = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, [f"validator failed: {proc.stderr[-1000:]}"]
    return bool(res.get("ok")), list(res.get("issues", []))

