"""CutDocument models (ARCHITECTURE §5).

The CutDocument is the single, versioned description of the edit. It never holds edit timestamps:
the story is a list of :class:`Segment` word-ID ranges in **output order**; everything else (inserts,
captions, texts, SFX, music anchors) is anchored to word IDs. The compiler resolves IDs to time.

Only :func:`studio.doc.ops.apply_ops` changes a document. Versions are saved as ``doc/v{n}.json``.

Story invariants (enforced by ops, re-checked by :mod:`studio.doc.validate`):

* each segment covers the inclusive source range ``from_word..to_word`` (same source, ordered);
* a word appears in at most one segment (no line reuse in v1, so word IDs map to one output time);
* words not covered by any segment are *removed*; ``removed`` records why.

ID formats: segments ``seg001``, inserts ``i001``, texts ``t001``, SFX cues ``fx001``, caption pages
``p001``, hook alternates ``h01``. IDs are assigned by code (ops), never by models.

All nested models forbid unknown fields (``extra="forbid"``) and avoid open-ended dicts where they are
used in op schemas, so the op JSON schemas are strict-tool compatible.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.perception.index import CLUSTER_ID_PATTERN, GAP_ID_PATTERN, WORD_ID_PATTERN

if TYPE_CHECKING:  # pragma: no cover
    from studio.perception.index import TakeIndex

__all__ = [
    "DOC_SCHEMA_VERSION",
    "SEG_ID_PATTERN", "INSERT_ID_PATTERN", "TEXT_ID_PATTERN", "SFX_ID_PATTERN", "PAGE_ID_PATTERN",
    "HOOK_ID_PATTERN", "WordId", "GapId", "Platform", "PinKind", "SeamKind", "InsertMode",
    "Brief", "StyleDials", "NamedDial", "Style", "Pins", "Point", "Framing", "SeamTreatment",
    "Segment", "RemovedRange", "WordRange", "Licence", "AssetRef", "CardSpec", "Transition", "Insert",
    "CaptionStyle", "CaptionPage", "CaptionPlan", "TextStyle", "TextOverlay", "EqBand",
    "VoiceChainSpec", "MusicParams", "MusicSpec", "SfxCue", "AssetId", "ASSET_ID_PATTERN", "AudioPlan",
    "ColorSpec", "Deliverable", "HookAlternate",
    "Note", "CutDocument", "new_document", "format_id", "id_number",
]

DOC_SCHEMA_VERSION = 1

SEG_ID_PATTERN = r"^seg\d{3,}$"
INSERT_ID_PATTERN = r"^i\d{3,}$"
TEXT_ID_PATTERN = r"^t\d{3,}$"
SFX_ID_PATTERN = r"^fx\d{3,}$"
PAGE_ID_PATTERN = r"^p\d{3,}$"
HOOK_ID_PATTERN = r"^h\d{2,}$"
#: registered asset IDs (``assets/registry/<id>.json``), assigned by code, e.g. ``px_2499611``
ASSET_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

#: prefix -> zero-padded width used by :func:`format_id`
ID_WIDTHS: dict[str, int] = {"seg": 3, "i": 3, "t": 3, "fx": 3, "p": 3, "h": 2}

WordId = Annotated[str, Field(pattern=WORD_ID_PATTERN, description="Word ID like w0012")]
AssetId = Annotated[str, Field(pattern=ASSET_ID_PATTERN, description="Registered asset ID, e.g. px_2499611")]
GapId = Annotated[str, Field(pattern=GAP_ID_PATTERN, description="Gap ID like g0007")]
ClusterId = Annotated[str, Field(pattern=CLUSTER_ID_PATTERN, description="Cluster ID like c01")]

Platform = Literal["tiktok", "reels", "shorts"]
PinKind = Literal["must_keep", "payoff", "cta"]
SeamKind = Literal["cut", "jcut", "lcut", "punch", "cutaway", "crossfade"]
InsertMode = Literal["full", "split_top", "split_bottom", "pip", "card"]
InsertAudio = Literal["voice_only", "duck", "with_sfx"]
TransitionKind = Literal["cut", "fade", "dissolve", "slide", "zoom", "whip"]
CaptionPosition = Literal["auto", "below_chin", "lower_third", "center", "upper_third"]
TextPosition = Literal["auto", "top", "upper_third", "center", "lower_third", "bottom"]

#: insert modes that cover the whole frame
FULLSCREEN_MODES: frozenset[str] = frozenset({"full", "card"})


def format_id(prefix: str, n: int) -> str:
    """``format_id("seg", 3) == "seg003"``; ``format_id("h", 2) == "h02"``."""
    return f"{prefix}{n:0{ID_WIDTHS.get(prefix, 3)}d}"


def id_number(value: str) -> int | None:
    """Trailing integer of an ID (``"seg012"`` → 12) or None."""
    m = re.search(r"(\d+)$", value or "")
    return int(m.group(1)) if m else None


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------------------------- brief/style
class Brief(_Model):
    """The Director's edit brief (S4). Free text plus the structured parts critics check against."""

    goal: str = ""
    audience: str = ""
    hook: str = ""
    beats: list[str] = Field(default_factory=list)
    target_length_s: float | None = Field(default=None, gt=0, le=600,
                                          description="Desired output length (a length, not an edit point)")
    cta: str = ""
    vibe: str = ""
    visual_plan: str = ""
    sound_plan: str = ""
    donts: list[str] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list, description="Justified deviations from doctrine")
    rubric: list[str] = Field(default_factory=list, description="Per-edit binary rubric questions")
    creator_question: str | None = None
    creator_answer: str | None = None
    text: str = Field(default="", description="The brief as free text")


class NamedDial(_Model):
    name: str = Field(min_length=1)
    value: float = Field(ge=0.0, le=1.0)


class StyleDials(_Model):
    """Style dials 0..1 (0.5 = neutral)."""

    energy: float = Field(default=0.5, ge=0.0, le=1.0)
    pace: float = Field(default=0.5, ge=0.0, le=1.0)
    polish: float = Field(default=0.5, ge=0.0, le=1.0)
    humor: float = Field(default=0.3, ge=0.0, le=1.0)
    extra: list[NamedDial] = Field(default_factory=list)

    def get(self, name: str, default: float | None = None) -> float | None:
        if name in ("energy", "pace", "polish", "humor"):
            return float(getattr(self, name))
        for d in self.extra:
            if d.name == name:
                return d.value
        return default


class Style(_Model):
    primary: str | None = Field(default=None, description="e.g. educational, storytime, comedy, hot_take, "
                                                           "listicle, tutorial, podcast, sales_ugc, founder")
    blend: list[str] = Field(default_factory=list)
    dials: StyleDials = Field(default_factory=StyleDials)


class Pins(_Model):
    must_keep_word_ids: list[WordId] = Field(default_factory=list)
    payoff_word_ids: list[WordId] = Field(default_factory=list)
    cta_word_ids: list[WordId] = Field(default_factory=list)

    def of_kind(self, kind: PinKind) -> list[str]:
        return {"must_keep": self.must_keep_word_ids, "payoff": self.payoff_word_ids,
                "cta": self.cta_word_ids}[kind]

    def all(self) -> dict[str, str]:
        """``{word_id: kind}`` for every pinned word (payoff/cta win over must_keep for labelling)."""
        out: dict[str, str] = {}
        for kind in ("must_keep", "payoff", "cta"):
            for w in self.of_kind(kind):  # type: ignore[arg-type]
                out[w] = kind
        return out


# ---------------------------------------------------------------------------------------------- story
class Point(_Model):
    """Normalized point in the upright source frame (0..1, top-left origin)."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class Framing(_Model):
    """Reframe/punch-in for a segment.

    ``scale`` is relative to the default 9:16 fill framing (1.0 = no punch). ``center`` is ``"face"``
    (face-tracked, smoothed) or a fixed point. ``ease``: ``"cut"`` = hard switch at the onset of
    ``anchor_word`` (default: the segment's first word); ``"smooth"`` = eased move reaching ``scale`` at
    the anchor over ``ease_ms``; ``"push"`` = slow linear push from 1.0 to ``scale`` across the segment.
    """

    scale: float = Field(default=1.0, ge=1.0, le=1.8)
    center: Literal["face"] | Point = "face"
    anchor_word: WordId | None = None
    ease: Literal["cut", "smooth", "push"] = "cut"
    ease_ms: int = Field(default=300, ge=0, le=3000)


class SeamTreatment(_Model):
    """How the cut *into* a segment is treated. ``lead_ms``: J/L-cut audio lead/lag, crossfade length."""

    kind: SeamKind = "cut"
    lead_ms: int = Field(default=0, ge=0, le=2000)


class Segment(_Model):
    """A contiguous source word range played in the story (inclusive, same source)."""

    id: str = Field(pattern=SEG_ID_PATTERN)
    from_word: WordId
    to_word: WordId
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    gap_overrides: dict[str, int] = Field(default_factory=dict,
                                          description="gap_id -> target pause length in ms (shorten only)")
    framing: Framing | None = None
    seam_in: SeamTreatment = Field(default_factory=SeamTreatment)
    note: str = ""

    @model_validator(mode="after")
    def _gap_ids(self) -> Segment:
        for gid, ms in self.gap_overrides.items():
            if not re.match(GAP_ID_PATTERN, gid):
                raise ValueError(f"{self.id}: bad gap id {gid!r} in gap_overrides")
            if ms < 0:
                raise ValueError(f"{self.id}: negative gap target for {gid}")
        return self


class WordRange(_Model):
    from_word: WordId
    to_word: WordId


class RemovedRange(_Model):
    """Explicit record of a cut and why (source-order inclusive range)."""

    from_word: WordId
    to_word: WordId
    reason: str = ""


# ---------------------------------------------------------------------------------------------- assets
class Licence(_Model):
    """Licence record (invariant 5). ``record_path`` points at the licence json under ``assets/``."""

    name: str = Field(min_length=1, description='e.g. "Pexels License", "CC0", "creator-owned"')
    source: str | None = None
    url: str | None = None
    holder: str | None = None
    attribution: str | None = None
    attribution_required: bool = False
    commercial_use: bool = True
    record_path: str | None = None
    acquired_at: str | None = None
    notes: str = ""


class AssetRef(_Model):
    """A media asset (b-roll clip, still, screenshot, audio) stored under ``assets/``.

    Assets are registered by code (sourcing/generation modules) with :meth:`studio.jobs.Job.register_asset`,
    which stores this record, including its licence, under ``assets/registry/<id>.json``. Ops reference
    assets only by ``id``, so a model never authors a licence record (invariant 5).
    """

    type: Literal["asset"] = "asset"
    id: str | None = Field(default=None, pattern=ASSET_ID_PATTERN)
    kind: Literal["video", "image", "audio", "gif", "screenshot", "generated_image", "generated_video"] = "video"
    source: str = Field(min_length=1, description="pexels | creator | higgsfield | epidemic | elevenlabs | "
                                                  "playwright | local …")
    source_id: str | None = None
    path: str | None = Field(default=None, description="Job-relative path, e.g. assets/broll/px_123.mp4")
    url: str | None = None
    in_ms: int = Field(default=0, ge=0, description="Use range start inside the asset")
    out_ms: int | None = Field(default=None, ge=0)
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    description: str = ""
    query: str | None = None
    licence: Licence | None = None


class CardSpec(_Model):
    """A designed card rendered by the overlay renderer (self-made: needs no licence)."""

    type: Literal["card"] = "card"
    template: Literal["title", "number", "stat", "list", "quote", "steps", "comparison"] = "title"
    title: str = ""
    subtitle: str = ""
    body: str = ""
    items: list[str] = Field(default_factory=list)
    number: str | None = None
    unit: str | None = None
    accent: str | None = Field(default=None, description="Accent colour, e.g. #FFD400")
    background: str | None = None


class Transition(_Model):
    kind: TransitionKind = "cut"
    ms: int = Field(default=0, ge=0, le=1500)


class Insert(_Model):
    """B-roll or card anchored to kept words (output time is derived from the anchors)."""

    id: str = Field(pattern=INSERT_ID_PATTERN)
    anchor_from_word: WordId
    anchor_to_word: WordId
    mode: InsertMode = "full"
    asset: Annotated[AssetRef | CardSpec, Field(discriminator="type")]
    job: str = Field(min_length=1, description="Why this insert exists (what it does for the viewer)")
    audio: InsertAudio = "voice_only"
    transition_in: Transition = Field(default_factory=Transition)
    transition_out: Transition = Field(default_factory=Transition)
    licence: Licence | None = None
    alternates: list[AssetRef] = Field(default_factory=list)
    pip_position: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "top_right"
    split_ratio: float = Field(default=0.5, ge=0.3, le=0.7, description="Share of height for the insert")
    creator_pinned: bool = False
    note: str = ""

    @property
    def effective_licence(self) -> Licence | None:
        if self.licence is not None:
            return self.licence
        return self.asset.licence if isinstance(self.asset, AssetRef) else None

    @property
    def needs_licence(self) -> bool:
        return isinstance(self.asset, AssetRef)


# ---------------------------------------------------------------------------------------------- captions/text
class CaptionStyle(_Model):
    font: str = "Montserrat"
    weight: int = Field(default=800, ge=100, le=1000)
    size_px: int = Field(default=72, ge=24, le=220, description="At 1080 px frame width")
    stroke_px: float = Field(default=6.0, ge=0.0, le=30.0)
    stroke_color: str = "#000000"
    color: str = "#FFFFFF"
    highlight_color: str = "#FFD400"
    background: str | None = None
    case: Literal["as_is", "upper", "lower", "title"] = "as_is"
    animation: Literal["none", "pop", "karaoke", "fade", "slide"] = "pop"
    max_words_per_page: int = Field(default=4, ge=1, le=10)
    max_chars_per_line: int = Field(default=20, ge=6, le=48)
    lines: int = Field(default=1, ge=1, le=3)
    shadow: bool = True
    highlight_lead_frames: int = Field(default=2, ge=0, le=6)


class CaptionPage(_Model):
    id: str | None = Field(default=None, pattern=PAGE_ID_PATTERN)
    word_ids: list[WordId] = Field(min_length=1)
    emphasis_word_ids: list[WordId] = Field(default_factory=list)
    text: str | None = Field(default=None, description="Display override (e.g. digits); None = verbatim")
    position: CaptionPosition | None = None
    y_norm: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _emph(self) -> CaptionPage:
        extra = [w for w in self.emphasis_word_ids if w not in self.word_ids]
        if extra:
            raise ValueError(f"emphasis words not on the page: {extra}")
        if len(set(self.word_ids)) != len(self.word_ids):
            raise ValueError("duplicate word ids on a caption page")
        return self


class CaptionPlan(_Model):
    enabled: bool = True
    pages: list[CaptionPage] = Field(default_factory=list)
    style: CaptionStyle = Field(default_factory=CaptionStyle)
    position: CaptionPosition = "auto"


class TextStyle(_Model):
    font: str = "Montserrat"
    weight: int = Field(default=800, ge=100, le=1000)
    size_px: int = Field(default=64, ge=16, le=240)
    color: str = "#FFFFFF"
    background: str | None = None
    stroke_px: float = Field(default=0.0, ge=0.0, le=30.0)
    stroke_color: str = "#000000"
    align: Literal["left", "center", "right"] = "center"
    case: Literal["as_is", "upper", "lower", "title"] = "as_is"


class TextOverlay(_Model):
    """Hook titles, callouts, lists, lower thirds — anchored to words."""

    id: str = Field(pattern=TEXT_ID_PATTERN)
    kind: Literal["hook_title", "callout", "list", "lower_third", "label", "cta"] = "callout"
    text: str = Field(min_length=1)
    items: list[str] = Field(default_factory=list)
    anchor_from_word: WordId
    anchor_to_word: WordId
    position: TextPosition | Point = "auto"
    style: TextStyle = Field(default_factory=TextStyle)
    animation: Literal["none", "pop", "fade", "slide", "typewriter"] = "pop"
    job: str = ""
    creator_pinned: bool = False


# ---------------------------------------------------------------------------------------------- audio
class EqBand(_Model):
    type: Literal["peak", "low_shelf", "high_shelf"] = "peak"
    freq_hz: float = Field(ge=20.0, le=20000.0)
    gain_db: float = Field(ge=-18.0, le=18.0)
    q: float = Field(default=1.0, ge=0.1, le=10.0)


class VoiceChainSpec(_Model):
    """Dialogue chain (pedalboard). Defaults are a gentle, measured-first starting point."""

    enabled: bool = True
    denoise: Literal["none", "light", "isolate"] = "none"
    isolation_provider: str | None = None  # "elevenlabs" | "clearervoice" | …
    hpf_hz: float = Field(default=80.0, ge=20.0, le=300.0)
    eq: list[EqBand] = Field(default_factory=list)
    deess_db: float = Field(default=0.0, ge=0.0, le=12.0, description="Max de-ess reduction")
    compression_db: float = Field(default=4.0, ge=0.0, le=12.0, description="Target gain reduction")
    comp_ratio: float = Field(default=3.0, ge=1.0, le=20.0)
    leveler: bool = True
    breath_atten_db: float = Field(default=0.0, ge=0.0, le=24.0)
    notes: str = ""


class MusicParams(_Model):
    """Model-facing music settings (``set_music``); the asset is referenced by registry ID."""

    asset_id: AssetId | None = None
    source: str = Field(default="none", description="epidemic | elevenlabs | library | creator | none")
    prompt: str | None = None
    mood: str | None = None
    level_lu_under_speech: float = Field(default=-18.0, ge=-30.0, le=-6.0)
    duck: bool = True
    duck_db: float = Field(default=6.0, ge=0.0, le=24.0)
    start_word: WordId | None = None
    end_word: WordId | None = None
    fade_in_ms: int = Field(default=500, ge=0, le=10000)
    fade_out_ms: int = Field(default=1500, ge=0, le=10000)
    backtime: bool = True
    hit_word_ids: list[WordId] = Field(default_factory=list)


class MusicSpec(MusicParams):
    """Music bed, fitted after the speech cut (never moves a cut). ``asset`` is resolved by code from
    ``asset_id`` and carries the licence."""

    asset: AssetRef | None = None


class SfxCue(_Model):
    id: str = Field(pattern=SFX_ID_PATTERN)
    kind: str = Field(min_length=1, description="whoosh | pop | hit | riser | click | ding | …")
    anchor_word: WordId
    at: Literal["start", "end"] = "start"
    offset_ms: int = Field(default=0, ge=-500, le=500, description="Small nudge relative to the anchor")
    gain_db: float = Field(default=-12.0, ge=-40.0, le=6.0)
    asset: AssetRef | None = None
    job: str = ""


class AudioPlan(_Model):
    voice: VoiceChainSpec = Field(default_factory=VoiceChainSpec)
    music: MusicSpec | None = None
    sfx: list[SfxCue] = Field(default_factory=list)
    room_tone: bool = True
    loudness_target_lufs: float = Field(default=-14.0, ge=-24.0, le=-9.0)
    true_peak_dbtp: float = Field(default=-1.0, ge=-9.0, le=-1.0)


# ---------------------------------------------------------------------------------------------- colour/delivery/meta
class ColorSpec(_Model):
    exposure: float = Field(default=0.0, ge=-2.0, le=2.0, description="EV")
    white_balance_k: int | None = Field(default=None, ge=2000, le=12000)
    temp: float = Field(default=0.0, ge=-1.0, le=1.0, description="+ warmer")
    tint: float = Field(default=0.0, ge=-1.0, le=1.0, description="+ magenta")
    contrast: float = Field(default=1.0, ge=0.5, le=1.5)
    saturation: float = Field(default=1.0, ge=0.0, le=2.0)
    look: str | None = None
    lut_strength: float = Field(default=1.0, ge=0.0, le=1.0)


class Deliverable(_Model):
    platform: Platform = "tiktok"
    variant: str = "main"


class HookAlternate(_Model):
    """An alternate opening built from word ranges (may use words outside the main story)."""

    id: str = Field(pattern=HOOK_ID_PATTERN)
    ranges: list[WordRange] = Field(min_length=1)
    title_text: str | None = None
    note: str = ""


class Note(_Model):
    by: str = ""
    text: str = Field(min_length=1)
    ref: str | None = Field(default=None, description="Optional ID the note is about (w…/seg…/i…)")
    version: int | None = None


# ---------------------------------------------------------------------------------------------- document
class CutDocument(_Model):
    schema_version: int = DOC_SCHEMA_VERSION
    version: int = Field(default=0, ge=0)
    parent_version: int | None = None
    job_id: str = ""
    created_by: str = ""
    brief: Brief | None = None
    style: Style = Field(default_factory=Style)
    pins: Pins = Field(default_factory=Pins)
    segments: list[Segment] = Field(default_factory=list)
    removed: list[RemovedRange] = Field(default_factory=list)
    inserts: list[Insert] = Field(default_factory=list)
    captions: CaptionPlan | None = None
    texts: list[TextOverlay] = Field(default_factory=list)
    audio: AudioPlan = Field(default_factory=AudioPlan)
    color: ColorSpec | None = None
    deliverables: list[Deliverable] = Field(default_factory=lambda: [Deliverable()])
    hook_alternates: list[HookAlternate] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    counters: dict[str, int] = Field(default_factory=dict, description="Highest ID number issued per prefix")
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_ids(self) -> CutDocument:
        def check(kind: str, ids: Iterable[str | None]) -> None:
            seen: set[str] = set()
            for i in ids:
                if i is None:
                    continue
                if i in seen:
                    raise ValueError(f"duplicate {kind} id {i}")
                seen.add(i)

        check("segment", (s.id for s in self.segments))
        check("insert", (i.id for i in self.inserts))
        check("text", (t.id for t in self.texts))
        check("sfx", (x.id for x in self.audio.sfx))
        check("hook", (h.id for h in self.hook_alternates))
        if self.captions is not None:
            check("caption page", (p.id for p in self.captions.pages))
        return self

    # ------------------------------------------------------------------ id lookups
    def segment(self, seg_id: str) -> Segment:
        for s in self.segments:
            if s.id == seg_id:
                return s
        raise KeyError(f"unknown segment id {seg_id!r}")

    def segment_index(self, seg_id: str) -> int:
        for i, s in enumerate(self.segments):
            if s.id == seg_id:
                return i
        raise KeyError(f"unknown segment id {seg_id!r}")

    def insert(self, insert_id: str) -> Insert:
        for x in self.inserts:
            if x.id == insert_id:
                return x
        raise KeyError(f"unknown insert id {insert_id!r}")

    def text(self, text_id: str) -> TextOverlay:
        for x in self.texts:
            if x.id == text_id:
                return x
        raise KeyError(f"unknown text id {text_id!r}")

    def sfx(self, sfx_id: str) -> SfxCue:
        for x in self.audio.sfx:
            if x.id == sfx_id:
                return x
        raise KeyError(f"unknown sfx id {sfx_id!r}")

    def caption_page(self, page_id: str) -> CaptionPage:
        if self.captions is not None:
            for p in self.captions.pages:
                if p.id == page_id:
                    return p
        raise KeyError(f"unknown caption page id {page_id!r}")

    # ------------------------------------------------------------------ story views (need the index)
    def segment_word_ids(self, seg: Segment | str, index: TakeIndex) -> list[str]:
        """Word IDs covered by a segment, in source order."""
        s = self.segment(seg) if isinstance(seg, str) else seg
        return index.word_ids(s.from_word, s.to_word)

    def kept_word_ids(self, index: TakeIndex) -> list[str]:
        """All kept word IDs in **output** order."""
        out: list[str] = []
        for s in self.segments:
            out.extend(index.word_ids(s.from_word, s.to_word))
        return out

    def output_positions(self, index: TakeIndex) -> dict[str, int]:
        """``{word_id: output position}`` for kept words (first occurrence if reused)."""
        pos: dict[str, int] = {}
        for i, w in enumerate(self.kept_word_ids(index)):
            pos.setdefault(w, i)
        return pos

    def segment_of_word(self, word_id: str, index: TakeIndex) -> Segment | None:
        p = index.word_pos(word_id)
        for s in self.segments:
            if index.word_pos(s.from_word) <= p <= index.word_pos(s.to_word):
                return s
        return None

    def removed_word_ids(self, index: TakeIndex) -> list[str]:
        """Words not in the story, in source order."""
        kept = set(self.kept_word_ids(index))
        return [w.id for w in index.words if w.id not in kept]

    def estimated_duration_us(self, index: TakeIndex) -> int:
        """Rough output length: word spans + kept inner gaps (with overrides) / speed. The compiler
        (pads, snapping, J/L) is authoritative; use this only for planning."""
        total = 0.0
        for s in self.segments:
            ws = index.get_words(s.from_word, s.to_word)
            span = ws[-1].end_us - ws[0].start_us
            for g in index.gaps_between(s.from_word, s.to_word):
                if g.id in s.gap_overrides:
                    span -= g.duration_us - min(g.duration_us, s.gap_overrides[g.id] * 1000)
            total += span / s.speed
        return round(total)

    def render_story(self, index: TakeIndex, *, view: Literal["full", "compact"] = "compact") -> str:
        """The cut as text in output order: one block per segment with its treatment, e.g.
        ``seg002 (w0014-w0018) speed 1.00 seam cut`` followed by the transcript of its words."""
        lines: list[str] = []
        for s in self.segments:
            attrs = [f"speed {s.speed:.2f}", f"seam {s.seam_in.kind}"
                     + (f" {s.seam_in.lead_ms}ms" if s.seam_in.lead_ms else "")]
            if s.framing is not None:
                c = s.framing.center if isinstance(s.framing.center, str) else \
                    f"({s.framing.center.x:.2f},{s.framing.center.y:.2f})"
                attrs.append(f"framing x{s.framing.scale:.2f} {c} {s.framing.ease}")
            if s.gap_overrides:
                attrs.append("gaps " + ",".join(f"{g}={ms}ms" for g, ms in sorted(s.gap_overrides.items())))
            lines.append(f"{s.id} ({s.from_word}-{s.to_word}) " + " · ".join(attrs))
            body = index.render_transcript(view, word_ids=index.word_ids(s.from_word, s.to_word))
            lines.extend("  " + ln for ln in body.splitlines())
        return "\n".join(lines)


def new_document(
    job_id: str = "",
    *,
    created_by: str = "",
    brief: Brief | None = None,
    style: Style | None = None,
    platforms: Iterable[Platform] = ("tiktok",),
) -> CutDocument:
    """A fresh version-0 document with an empty story."""
    return CutDocument(
        job_id=job_id,
        created_by=created_by,
        brief=brief,
        style=style or Style(),
        deliverables=[Deliverable(platform=p) for p in platforms],
    )
