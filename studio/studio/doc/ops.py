"""Typed ops: the ONLY way to change a CutDocument (ARCHITECTURE §5).

``apply_ops(doc, ops, index, job=…) -> (new_doc, results)``

* Ops are small strict Pydantic models (``extra="forbid"``) discriminated by ``op``. They address
  word/gap/sentence/cluster/segment/insert IDs only; there are **no absolute time fields**, so a
  model-provided timestamp can never become an edit coordinate (invariant 3). Durations such as a
  target pause (``set_gap.ms``) or a J/L lead are relative and bounded.
* Every op is validated against the Take Index and the current document. Invalid ops are rejected
  with a human-readable ``reason``; ``apply_ops`` never raises on bad input.
* Ops apply sequentially to a working copy; one rejected op does not block the others.
* When at least one op applied, the result is a new version with ``parent_version = doc.version``.
  Without a job the new version is ``doc.version + 1``; with a ``job`` it is one past the highest saved
  version (so several revisions may branch from the same parent, e.g. in the champion loop), it is saved
  to ``doc/v{n}.json`` and every op (applied or not) is appended to ``doc/oplog.jsonl``.
* ``undo(n)`` walks ``n`` steps up the ``parent_version`` chain and restores that version's content as
  a *new* version (history is append-only, so undoing an undo is a redo). It must be the only op in
  its batch and needs the job (or a ``history`` callable).
* Assets (b-roll, music, SFX) are referenced by **registered asset ID** (``asset_id``). Code registers
  assets with their licence records (:meth:`studio.jobs.Job.register_asset`); ``apply_ops`` resolves IDs
  through the job (or an ``assets`` mapping/callable) and rejects unknown or unlicensed assets, so a
  model never authors a licence record (invariant 5). Designed cards are given inline (``card``).
* Story ops re-anchor dependent items (inserts, texts, caption pages, SFX, music anchors) when words
  are cut and report every change as a warning; items whose anchor words are all gone are dropped
  (warned). Pinned words (must_keep/payoff/cta) can never be removed while pinned.

Op families (one agent tool per family, see :func:`tool_definitions`):

======== ================================================================================
cut      set_story, cut_words, restore_words, choose_take, move_segment, set_gap, set_speed, set_seam
framing  set_framing, clear_framing
inserts  add_insert, update_insert, remove_insert
captions set_captions, edit_caption_page, set_caption_style, add_text, update_text, remove_text
audio    set_voice_chain, set_music, add_sfx, remove_sfx, set_loudness
color    set_color
meta     set_brief, set_style, pin, unpin, note, set_deliverables, set_hook_alternates, undo
======== ================================================================================
"""

from __future__ import annotations

import copy
import datetime as _dt
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from studio.doc.model import (
    FULLSCREEN_MODES,
    AssetId,
    AssetRef,
    Brief,
    CaptionPage,
    CaptionPlan,
    CaptionPosition,
    CaptionStyle,
    CardSpec,
    ClusterId,
    ColorSpec,
    CutDocument,
    Deliverable,
    Framing,
    GapId,
    HookAlternate,
    Insert,
    InsertAudio,
    InsertMode,
    MusicParams,
    MusicSpec,
    Note,
    PinKind,
    Point,
    RemovedRange,
    SeamTreatment,
    Segment,
    SfxCue,
    Style,
    TextOverlay,
    TextPosition,
    TextStyle,
    Transition,
    VoiceChainSpec,
    WordId,
    WordRange,
    format_id,
    id_number,
)

if TYPE_CHECKING:  # pragma: no cover
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = [
    "OpRejected", "OpBase", "OpResult", "Op", "OP_ADAPTER", "OP_FAMILIES", "OP_CLASSES", "FAMILY_DESCRIPTIONS",
    # cut
    "GapOverride", "SegmentSpec", "SetStory", "CutWords", "RestoreWords", "ChooseTake", "MoveSegment",
    "SetGap", "SetSpeed", "SetSeam",
    # framing
    "SetFraming", "ClearFraming",
    # inserts
    "AddInsert", "UpdateInsert", "RemoveInsert",
    # captions/text
    "SetCaptions", "EditCaptionPage", "SetCaptionStyle", "AddText", "UpdateText", "RemoveText",
    # audio
    "SetVoiceChain", "SetMusic", "AddSfx", "RemoveSfx", "SetLoudness",
    # color
    "SetColor",
    # meta
    "SetBrief", "SetStyle", "Pin", "Unpin", "NoteOp", "SetDeliverables", "HookAlternateSpec",
    "SetHookAlternates", "Undo",
    # functions
    "parse_op", "apply_ops", "undo", "oplog_entries", "op_json_schema", "family_tool_schema",
    "tool_definitions", "strict_schema", "clean_schema", "DEFAULT_STRICT_FAMILIES", "STAGE_FAMILIES",
    "AssetResolver",
]


class OpRejected(Exception):
    """Raised inside handlers to reject an op with a reason (caught by :func:`apply_ops`)."""


class OpBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    family: ClassVar[str] = ""


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ============================================================================================ cut
class GapOverride(_M):
    gap_id: GapId
    ms: int = Field(ge=0, le=10000, description="Target pause length in ms (can only shorten)")


class SegmentSpec(_M):
    """One story segment for ``set_story`` (IDs are assigned by code)."""

    from_word: WordId
    to_word: WordId
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    seam_in: SeamTreatment = Field(default_factory=SeamTreatment)
    framing: Framing | None = None
    gap_overrides: list[GapOverride] = Field(default_factory=list)
    note: str = ""


class SetStory(OpBase):
    """Replace the whole story (typically the first cut). Words not covered are removed."""

    family: ClassVar[str] = "cut"
    op: Literal["set_story"] = "set_story"
    segments: list[SegmentSpec] = Field(min_length=1)
    removed: list[RemovedRange] = Field(default_factory=list,
                                        description="Optional reasons for removed ranges")
    reason: str = ""


class CutWords(OpBase):
    """Remove an inclusive source word range from the story (splits segments as needed)."""

    family: ClassVar[str] = "cut"
    op: Literal["cut_words"] = "cut_words"
    from_word: WordId
    to_word: WordId
    reason: str = Field(min_length=1)


class RestoreWords(OpBase):
    """Put removed words back (extends neighbouring segments or adds one in source order)."""

    family: ClassVar[str] = "cut"
    op: Literal["restore_words"] = "restore_words"
    from_word: WordId
    to_word: WordId
    reason: str = ""


class ChooseTake(OpBase):
    """Keep ``sentence_id`` as the take of ``cluster_id`` and remove its other takes."""

    family: ClassVar[str] = "cut"
    op: Literal["choose_take"] = "choose_take"
    cluster_id: ClusterId
    sentence_id: str = Field(pattern=r"^s\d{3,}$")
    reason: str = ""


class MoveSegment(OpBase):
    """Reorder: place ``seg_id`` right after ``after`` (None = at the start)."""

    family: ClassVar[str] = "cut"
    op: Literal["move_segment"] = "move_segment"
    seg_id: str = Field(pattern=r"^seg\d{3,}$")
    after: str | None = Field(default=None, pattern=r"^seg\d{3,}$")


class SetGap(OpBase):
    """Shorten a kept pause to ``ms`` (the measured length restores it)."""

    family: ClassVar[str] = "cut"
    op: Literal["set_gap"] = "set_gap"
    gap_id: GapId
    ms: int = Field(ge=0, le=10000)


class SetSpeed(OpBase):
    family: ClassVar[str] = "cut"
    op: Literal["set_speed"] = "set_speed"
    seg_id: str = Field(pattern=r"^seg\d{3,}$")
    speed: float = Field(ge=0.5, le=2.0)


class SetSeam(OpBase):
    """Seam treatment into ``seg_id`` (J/L cut leads need ``lead_ms`` > 0)."""

    family: ClassVar[str] = "cut"
    op: Literal["set_seam"] = "set_seam"
    seg_id: str = Field(pattern=r"^seg\d{3,}$")
    treatment: SeamTreatment


# ============================================================================================ framing
class _Target(OpBase):
    seg_id: str | None = Field(default=None, pattern=r"^seg\d{3,}$")
    from_word: WordId | None = None
    to_word: WordId | None = None

    @model_validator(mode="after")
    def _one_target(self) -> _Target:
        by_seg = self.seg_id is not None
        by_words = self.from_word is not None or self.to_word is not None
        if by_seg == by_words:
            raise ValueError("give either seg_id or from_word+to_word")
        if by_words and (self.from_word is None or self.to_word is None):
            raise ValueError("word-range target needs both from_word and to_word")
        return self


class SetFraming(_Target):
    """Punch-in/reframe a segment, or a word range inside one segment (the segment is split)."""

    family: ClassVar[str] = "framing"
    op: Literal["set_framing"] = "set_framing"
    scale: float = Field(ge=1.0, le=1.8, description="Relative to the default 9:16 framing (1.0 = no punch-in)")
    center: Literal["face"] | Point = "face"
    ease: Literal["cut", "smooth", "push"] = "cut"
    ease_ms: int = Field(default=300, ge=0, le=3000)
    anchor_word: WordId | None = None


class ClearFraming(_Target):
    """Remove framing from a segment or from every segment overlapping a word range."""

    family: ClassVar[str] = "framing"
    op: Literal["clear_framing"] = "clear_framing"


# ============================================================================================ inserts
class AddInsert(OpBase):
    """Add b-roll (a registered ``asset_id``) or a designed ``card`` over kept words."""

    family: ClassVar[str] = "inserts"
    op: Literal["add_insert"] = "add_insert"
    anchor_from_word: WordId
    anchor_to_word: WordId
    mode: InsertMode = "full"
    asset_id: AssetId | None = None
    card: CardSpec | None = None
    job: str = Field(min_length=1, description="What this insert does for the viewer")
    audio: InsertAudio = "voice_only"
    transition_in: Transition = Field(default_factory=Transition)
    transition_out: Transition = Field(default_factory=Transition)
    alternate_asset_ids: list[AssetId] = Field(default_factory=list)
    pip_position: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "top_right"
    split_ratio: float = Field(default=0.5, ge=0.3, le=0.7)
    note: str = ""

    @model_validator(mode="after")
    def _one_visual(self) -> AddInsert:
        if (self.asset_id is None) == (self.card is None):
            raise ValueError("give exactly one of asset_id or card")
        return self


class UpdateInsert(OpBase):
    """Change fields of an insert (omitted/null fields stay as they are). ``asset_id`` or ``card``
    replaces the visual."""

    family: ClassVar[str] = "inserts"
    op: Literal["update_insert"] = "update_insert"
    id: str = Field(pattern=r"^i\d{3,}$")
    anchor_from_word: WordId | None = None
    anchor_to_word: WordId | None = None
    mode: InsertMode | None = None
    asset_id: AssetId | None = None
    card: CardSpec | None = None
    job: str | None = Field(default=None, min_length=1)
    audio: InsertAudio | None = None
    transition_in: Transition | None = None
    transition_out: Transition | None = None
    pip_position: Literal["top_left", "top_right", "bottom_left", "bottom_right"] | None = None
    split_ratio: float | None = Field(default=None, ge=0.3, le=0.7)
    note: str | None = None

    @model_validator(mode="after")
    def _at_most_one_visual(self) -> UpdateInsert:
        if self.asset_id is not None and self.card is not None:
            raise ValueError("give asset_id or card, not both")
        return self


class RemoveInsert(OpBase):
    family: ClassVar[str] = "inserts"
    op: Literal["remove_insert"] = "remove_insert"
    id: str = Field(pattern=r"^i\d{3,}$")
    reason: str = ""


# ============================================================================================ captions/text
class SetCaptions(OpBase):
    """Replace the caption plan (pages in output order; page IDs assigned when missing)."""

    family: ClassVar[str] = "captions"
    op: Literal["set_captions"] = "set_captions"
    plan: CaptionPlan


class EditCaptionPage(OpBase):
    """Edit one page. ``text=""`` clears a display override; null fields are unchanged."""

    family: ClassVar[str] = "captions"
    op: Literal["edit_caption_page"] = "edit_caption_page"
    page_id: str = Field(pattern=r"^p\d{3,}$")
    word_ids: list[WordId] | None = None
    emphasis_word_ids: list[WordId] | None = None
    text: str | None = None
    position: CaptionPosition | None = None
    y_norm: float | None = Field(default=None, ge=0.0, le=1.0)


class SetCaptionStyle(OpBase):
    family: ClassVar[str] = "captions"
    op: Literal["set_caption_style"] = "set_caption_style"
    style: CaptionStyle
    enabled: bool | None = None
    position: CaptionPosition | None = None


class AddText(OpBase):
    family: ClassVar[str] = "captions"
    op: Literal["add_text"] = "add_text"
    kind: Literal["hook_title", "callout", "list", "lower_third", "label", "cta"] = "callout"
    text: str = Field(min_length=1)
    items: list[str] = Field(default_factory=list)
    anchor_from_word: WordId
    anchor_to_word: WordId
    position: TextPosition | Point = "auto"
    style: TextStyle = Field(default_factory=TextStyle)
    animation: Literal["none", "pop", "fade", "slide", "typewriter"] = "pop"
    job: str = ""


class UpdateText(OpBase):
    family: ClassVar[str] = "captions"
    op: Literal["update_text"] = "update_text"
    id: str = Field(pattern=r"^t\d{3,}$")
    kind: Literal["hook_title", "callout", "list", "lower_third", "label", "cta"] | None = None
    text: str | None = Field(default=None, min_length=1)
    items: list[str] | None = None
    anchor_from_word: WordId | None = None
    anchor_to_word: WordId | None = None
    position: TextPosition | Point | None = None
    style: TextStyle | None = None
    animation: Literal["none", "pop", "fade", "slide", "typewriter"] | None = None
    job: str | None = None


class RemoveText(OpBase):
    family: ClassVar[str] = "captions"
    op: Literal["remove_text"] = "remove_text"
    id: str = Field(pattern=r"^t\d{3,}$")


# ============================================================================================ audio
class SetVoiceChain(OpBase):
    family: ClassVar[str] = "audio"
    op: Literal["set_voice_chain"] = "set_voice_chain"
    spec: VoiceChainSpec


class SetMusic(OpBase):
    """Set the music bed (null = no music). ``spec.asset_id`` must be a registered, licensed asset."""

    family: ClassVar[str] = "audio"
    op: Literal["set_music"] = "set_music"
    spec: MusicParams | None = None


class AddSfx(OpBase):
    family: ClassVar[str] = "audio"
    op: Literal["add_sfx"] = "add_sfx"
    kind: str = Field(min_length=1)
    anchor_word: WordId
    at: Literal["start", "end"] = "start"
    offset_ms: int = Field(default=0, ge=-500, le=500)
    gain_db: float = Field(default=-12.0, ge=-40.0, le=6.0)
    asset_id: AssetId | None = Field(default=None, description="Registered SFX asset; null = pick by kind")
    job: str = ""


class RemoveSfx(OpBase):
    family: ClassVar[str] = "audio"
    op: Literal["remove_sfx"] = "remove_sfx"
    id: str = Field(pattern=r"^fx\d{3,}$")


class SetLoudness(OpBase):
    family: ClassVar[str] = "audio"
    op: Literal["set_loudness"] = "set_loudness"
    lufs: float = Field(ge=-24.0, le=-9.0)
    true_peak_dbtp: float | None = Field(default=None, ge=-9.0, le=-1.0)


# ============================================================================================ color
class SetColor(OpBase):
    family: ClassVar[str] = "color"
    op: Literal["set_color"] = "set_color"
    spec: ColorSpec | None = None


# ============================================================================================ meta
class SetBrief(OpBase):
    family: ClassVar[str] = "meta"
    op: Literal["set_brief"] = "set_brief"
    brief: Brief


class SetStyle(OpBase):
    family: ClassVar[str] = "meta"
    op: Literal["set_style"] = "set_style"
    style: Style


class Pin(OpBase):
    """Pin kept words so no op can remove them (payoff/CTA pins back invariant 4)."""

    family: ClassVar[str] = "meta"
    op: Literal["pin"] = "pin"
    word_ids: list[WordId] = Field(min_length=1)
    kind: PinKind


class Unpin(OpBase):
    family: ClassVar[str] = "meta"
    op: Literal["unpin"] = "unpin"
    word_ids: list[WordId] = Field(min_length=1)
    kind: PinKind


class NoteOp(OpBase):
    """Record rationale (optionally about an ID)."""

    family: ClassVar[str] = "meta"
    op: Literal["note"] = "note"
    text: str = Field(min_length=1)
    ref: str | None = None


class SetDeliverables(OpBase):
    family: ClassVar[str] = "meta"
    op: Literal["set_deliverables"] = "set_deliverables"
    deliverables: list[Deliverable] = Field(min_length=1)


class HookAlternateSpec(_M):
    ranges: list[WordRange] = Field(min_length=1)
    title_text: str | None = None
    note: str = ""


class SetHookAlternates(OpBase):
    family: ClassVar[str] = "meta"
    op: Literal["set_hook_alternates"] = "set_hook_alternates"
    alternates: list[HookAlternateSpec] = Field(default_factory=list)


class Undo(OpBase):
    """Restore the content of the version ``n`` steps up the parent chain as a new version. Send alone."""

    family: ClassVar[str] = "meta"
    op: Literal["undo"] = "undo"
    n: int = Field(default=1, ge=1, le=1000)


# ============================================================================================ registry
OP_FAMILIES: dict[str, tuple[type[OpBase], ...]] = {
    "cut": (SetStory, CutWords, RestoreWords, ChooseTake, MoveSegment, SetGap, SetSpeed, SetSeam),
    "framing": (SetFraming, ClearFraming),
    "inserts": (AddInsert, UpdateInsert, RemoveInsert),
    "captions": (SetCaptions, EditCaptionPage, SetCaptionStyle, AddText, UpdateText, RemoveText),
    "audio": (SetVoiceChain, SetMusic, AddSfx, RemoveSfx, SetLoudness),
    "color": (SetColor,),
    "meta": (SetBrief, SetStyle, Pin, Unpin, NoteOp, SetDeliverables, SetHookAlternates, Undo),
}

FAMILY_DESCRIPTIONS: dict[str, str] = {
    "cut": "Story and fine-cut ops: set the whole story, cut/restore word ranges, choose retakes, reorder "
           "segments, shorten pauses (gap IDs), change segment speed and seam treatments. Address words "
           "by ID (w0001), never by time.",
    "framing": "Punch-ins and reframes on a segment or a word range inside one segment.",
    "inserts": "B-roll and designed cards anchored to kept word IDs. Every insert needs a job; asset "
               "inserts need a licence record.",
    "captions": "Caption plan (pages of word IDs, emphasis, style) and on-screen text overlays anchored "
                "to word IDs.",
    "audio": "Voice chain, music bed, SFX anchored to words, loudness target.",
    "color": "Colour correction and look.",
    "meta": "Brief, style, pins (protected words), notes, deliverables, hook alternates and undo.",
}

OP_CLASSES: dict[str, type[OpBase]] = {
    cls.model_fields["op"].default: cls for fam in OP_FAMILIES.values() for cls in fam
}

Op = Annotated[Union[tuple(cls for fam in OP_FAMILIES.values() for cls in fam)],  # type: ignore[valid-type]
               Field(discriminator="op")]
OP_ADAPTER: TypeAdapter[Any] = TypeAdapter(Op)


class OpResult(BaseModel):
    """Outcome of one op. ``ids`` lists IDs created by the op (e.g. a new insert ``i003``)."""

    index: int
    op: str
    applied: bool
    reason: str = ""
    ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _fmt_validation(err: ValidationError, limit: int = 6) -> str:
    parts = []
    for e in err.errors()[:limit]:
        loc = ".".join(str(x) for x in e.get("loc", ()) if x != "__root__")
        msg = e.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    more = len(err.errors()) - limit
    if more > 0:
        parts.append(f"(+{more} more)")
    return "; ".join(parts)


def parse_op(raw: OpBase | Mapping[str, Any]) -> OpBase:
    """Validate one op (model or dict). Raises ``ValueError`` with a readable message."""
    if isinstance(raw, OpBase):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError(f"op must be an object, got {type(raw).__name__}")
    name = raw.get("op")
    if name is None:
        raise ValueError("missing 'op' field")
    if name not in OP_CLASSES:
        raise ValueError(f"unknown op {name!r}; valid ops: {', '.join(sorted(OP_CLASSES))}")
    try:
        return OP_CLASSES[name].model_validate(dict(raw))
    except ValidationError as e:
        raise ValueError(f"{name}: {_fmt_validation(e)}") from None


# ============================================================================================ helpers
AssetResolver = Callable[[str], "AssetRef | None"]


@dataclass
class _Ctx:
    index: TakeIndex
    by: str
    new_version: int
    assets: AssetResolver | None = None
    ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _resolve_asset(ctx: _Ctx, asset_id: str, what: str) -> AssetRef:
    """Registered asset for ``asset_id``; it must carry a licence record (invariant 5)."""
    if ctx.assets is None:
        raise OpRejected(f"{what}: asset_id needs the job's asset registry (pass job= or assets=)")
    a = ctx.assets(asset_id)
    if a is None:
        raise OpRejected(f"{what}: unknown asset {asset_id} (not registered)")
    if a.licence is None:
        raise OpRejected(f"{what}: asset {asset_id} has no licence record (invariant 5)")
    return a.model_copy(update={"id": asset_id}, deep=True)


def _wpos(ix: TakeIndex, wid: str) -> int:
    try:
        return ix.word_pos(wid)
    except KeyError:
        raise OpRejected(f"unknown word id {wid}") from None


def _word_label(ix: TakeIndex, wid: str) -> str:
    try:
        return f"{wid} '{ix.word(wid).text}'"
    except KeyError:
        return wid


def _source_range(ix: TakeIndex, from_w: str, to_w: str, what: str = "range") -> tuple[int, int]:
    a, b = _wpos(ix, from_w), _wpos(ix, to_w)
    if a > b:
        raise OpRejected(f"{what} {from_w}-{to_w} is reversed ({from_w} comes after {to_w} in the take)")
    if ix.words[a].source != ix.words[b].source:
        raise OpRejected(f"{what} {from_w}-{to_w} spans two sources")
    return a, b


def _seg_range(ix: TakeIndex, seg: Segment) -> tuple[int, int]:
    return _wpos(ix, seg.from_word), _wpos(ix, seg.to_word)


def _runs(positions: Iterable[int]) -> list[tuple[int, int]]:
    """Sorted maximal runs of consecutive integers."""
    out: list[tuple[int, int]] = []
    for p in sorted(set(positions)):
        if out and p == out[-1][1] + 1:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out


def _kept_positions(doc: CutDocument, ix: TakeIndex) -> dict[int, str]:
    """source position -> segment id."""
    cov: dict[int, str] = {}
    for s in doc.segments:
        a, b = _seg_range(ix, s)
        for p in range(a, b + 1):
            if p in cov:
                raise OpRejected(f"word {ix.words[p].id} is in both {cov[p]} and {s.id}")
            cov[p] = s.id
    return cov


def _existing_numbers(doc: CutDocument, prefix: str) -> list[int]:
    if prefix == "seg":
        ids = [s.id for s in doc.segments]
    elif prefix == "i":
        ids = [x.id for x in doc.inserts]
    elif prefix == "t":
        ids = [x.id for x in doc.texts]
    elif prefix == "fx":
        ids = [x.id for x in doc.audio.sfx]
    elif prefix == "p":
        ids = [p.id for p in (doc.captions.pages if doc.captions else []) if p.id]
    elif prefix == "h":
        ids = [h.id for h in doc.hook_alternates]
    else:  # pragma: no cover - programming error
        raise ValueError(prefix)
    return [n for n in (id_number(i) for i in ids) if n is not None]


def _next_id(doc: CutDocument, prefix: str) -> str:
    n = max([doc.counters.get(prefix, 0), *_existing_numbers(doc, prefix)]) + 1
    doc.counters[prefix] = n
    return format_id(prefix, n)


def _gap_too_long(ms: int, gap_duration_us: int) -> bool:
    """Targets may only shorten a pause (1 ms rounding tolerance)."""
    return ms * 1000 > gap_duration_us + 999


def _inner_gaps_in(ix: TakeIndex, a: int, b: int) -> set[str]:
    return {g.id for g in ix.gaps_between(ix.words[a].id, ix.words[b].id)}


def _piece(ix: TakeIndex, seg: Segment, a: int, b: int, *, new_id: str | None = None,
           seam: SeamTreatment | None = None) -> Segment:
    """Copy of ``seg`` restricted to source positions ``a..b`` (gap overrides/framing anchor filtered)."""
    gaps = _inner_gaps_in(ix, a, b)
    framing = seg.framing
    if framing is not None and framing.anchor_word is not None:
        ap = ix.word_pos(framing.anchor_word)
        if not (a <= ap <= b):
            framing = framing.model_copy(update={"anchor_word": None})
    return seg.model_copy(update={
        "id": new_id or seg.id,
        "from_word": ix.words[a].id,
        "to_word": ix.words[b].id,
        "gap_overrides": {g: ms for g, ms in seg.gap_overrides.items() if g in gaps},
        "framing": framing.model_copy(deep=True) if framing is not None else None,
        "seam_in": (seam if seam is not None else seg.seam_in).model_copy(deep=True),
    }, deep=True)


def _remove_positions(doc: CutDocument, ix: TakeIndex, positions: set[int], ctx: _Ctx,
                      exclude: frozenset[str] = frozenset(), quiet: bool = False) -> set[int]:
    """Remove source positions from every segment (except ``exclude``); return the positions that were
    actually kept before. The first remaining piece of a segment keeps its ID and incoming seam; later
    pieces get new IDs and a plain cut."""
    new_segments: list[Segment] = []
    removed: set[int] = set()
    for seg in doc.segments:
        if seg.id in exclude:
            new_segments.append(seg)
            continue
        a, b = _seg_range(ix, seg)
        hit = {p for p in range(a, b + 1) if p in positions}
        if not hit:
            new_segments.append(seg)
            continue
        removed |= hit
        keep = [p for p in range(a, b + 1) if p not in hit]
        runs = _runs(keep)
        if not runs:
            if not quiet:
                ctx.warnings.append(f"{seg.id} removed (all its words were cut)")
            continue
        for k, (ra, rb) in enumerate(runs):
            if k == 0:
                new_segments.append(_piece(ix, seg, ra, rb))
            else:
                nid = _next_id(doc, "seg")
                new_segments.append(_piece(ix, seg, ra, rb, new_id=nid, seam=SeamTreatment()))
                if not quiet:
                    ctx.warnings.append(f"{seg.id} split; new segment {nid} "
                                        f"({ix.words[ra].id}-{ix.words[rb].id})")
    doc.segments = new_segments
    return removed


def _add_removed(doc: CutDocument, ix: TakeIndex, positions: Iterable[int], reason: str) -> None:
    for ra, rb in _runs(positions):
        doc.removed.append(RemovedRange(from_word=ix.words[ra].id, to_word=ix.words[rb].id, reason=reason))
    doc.removed.sort(key=lambda r: ix.word_pos(r.from_word))


def _trim_removed(doc: CutDocument, ix: TakeIndex, restored: set[int]) -> None:
    out: list[RemovedRange] = []
    for r in doc.removed:
        try:
            a, b = ix.word_pos(r.from_word), ix.word_pos(r.to_word)
        except KeyError:
            out.append(r)
            continue
        rest = [p for p in range(a, b + 1) if p not in restored]
        for ra, rb in _runs(rest):
            out.append(RemovedRange(from_word=ix.words[ra].id, to_word=ix.words[rb].id, reason=r.reason))
    doc.removed = out


def _rebuild_removed(doc: CutDocument, ix: TakeIndex, explicit: Sequence[RemovedRange], default_reason: str,
                     ctx: _Ctx) -> None:
    kept = set(_kept_positions(doc, ix))
    uncovered = {p for p in range(len(ix.words)) if p not in kept}
    described: set[int] = set()
    records: list[RemovedRange] = []
    for r in explicit:
        a, b = _source_range(ix, r.from_word, r.to_word, "removed range")
        inside = [p for p in range(a, b + 1) if p in uncovered and p not in described]
        if len(inside) < b - a + 1:
            ctx.warnings.append(f"removed range {r.from_word}-{r.to_word} overlaps kept words; trimmed")
        for ra, rb in _runs(inside):
            records.append(RemovedRange(from_word=ix.words[ra].id, to_word=ix.words[rb].id, reason=r.reason))
        described |= set(inside)
    for ra, rb in _runs(uncovered - described):
        records.append(RemovedRange(from_word=ix.words[ra].id, to_word=ix.words[rb].id, reason=default_reason))
    records.sort(key=lambda r: ix.word_pos(r.from_word))
    doc.removed = records


def _insert_by_source(doc: CutDocument, ix: TakeIndex, seg: Segment) -> None:
    """Insert a new segment after the story segment with the greatest source end before it."""
    start = ix.word_pos(seg.from_word)
    best_i, best_end = -1, -1
    for i, s in enumerate(doc.segments):
        e = ix.word_pos(s.to_word)
        if e < start and e > best_end:
            best_i, best_end = i, e
    doc.segments.insert(best_i + 1, seg)


def _compatible(a: Segment, b: Segment) -> bool:
    return a.speed == b.speed and a.framing == b.framing


def _merge_contiguous(doc: CutDocument, ix: TakeIndex, ctx: _Ctx, only: set[str] | None = None) -> None:
    """Merge output-adjacent, source-contiguous, attribute-identical segments joined by a plain cut."""
    segs = doc.segments
    only = set(only) if only is not None else None
    i = 0
    while i < len(segs) - 1:
        a, b = segs[i], segs[i + 1]
        if ((only is None or a.id in only or b.id in only)
                and ix.word_pos(a.to_word) + 1 == ix.word_pos(b.from_word)
                and _compatible(a, b) and b.seam_in.kind in ("cut", "punch") and b.seam_in.lead_ms == 0):
            merged = dict(a.gap_overrides)
            merged.update(b.gap_overrides)
            segs[i] = a.model_copy(update={"to_word": b.to_word, "gap_overrides": merged})
            del segs[i + 1]
            ctx.warnings.append(f"{b.id} merged into {a.id}")
            if only is not None:
                only.add(a.id)  # keep merging along the chain
            continue
        i += 1


def _require_kept(doc: CutDocument, ix: TakeIndex, wids: Iterable[str], what: str) -> dict[str, int]:
    pos = doc.output_positions(ix)
    for w in wids:
        _wpos(ix, w)
        if w not in pos:
            raise OpRejected(f"{what}: {_word_label(ix, w)} is not in the story")
    return pos


def _check_anchor_span(doc: CutDocument, ix: TakeIndex, fr: str, to: str, what: str) -> tuple[int, int]:
    pos = _require_kept(doc, ix, (fr, to), what)
    if pos[fr] > pos[to]:
        raise OpRejected(f"{what}: {fr} comes after {to} in the cut")
    return pos[fr], pos[to]


def _insert_conflicts(doc: CutDocument, ix: TakeIndex, span: tuple[int, int], mode: str,
                      exclude: str | None = None) -> list[str]:
    pos = doc.output_positions(ix)
    out = []
    for other in doc.inserts:
        if other.id == exclude:
            continue
        if other.anchor_from_word not in pos or other.anchor_to_word not in pos:
            continue
        o = (pos[other.anchor_from_word], pos[other.anchor_to_word])
        overlapping = o[0] <= span[1] and span[0] <= o[1]
        if overlapping and (mode == other.mode or mode in FULLSCREEN_MODES or other.mode in FULLSCREEN_MODES):
            out.append(other.id)
    return out


def _check_insert(doc: CutDocument, ix: TakeIndex, ins: Insert) -> None:
    span = _check_anchor_span(doc, ix, ins.anchor_from_word, ins.anchor_to_word, f"insert {ins.id}")
    if ins.mode == "card" and not isinstance(ins.asset, CardSpec):
        raise OpRejected("mode 'card' needs a card asset (type 'card')")
    if ins.needs_licence and ins.effective_licence is None:
        raise OpRejected("asset inserts need a licence record (invariant 5)")
    conflicts = _insert_conflicts(doc, ix, span, ins.mode, exclude=ins.id)
    if conflicts:
        raise OpRejected(f"overlaps insert(s) {', '.join(conflicts)} ({ins.mode}); "
                         "full-screen inserts and same-layer inserts cannot overlap")


def _check_pages(doc: CutDocument, ix: TakeIndex, pages: Sequence[CaptionPage]) -> None:
    pos = doc.output_positions(ix)
    last = -1
    for k, p in enumerate(pages):
        label = p.id or f"page #{k + 1}"
        for w in p.word_ids:
            _wpos(ix, w)
            if w not in pos:
                raise OpRejected(f"caption {label}: {_word_label(ix, w)} is not in the story")
            if pos[w] <= last:
                raise OpRejected(f"caption {label}: {w} is out of output order or already on an earlier page")
            last = pos[w]


def _assign_page_ids(doc: CutDocument, plan: CaptionPlan) -> list[str]:
    seen: set[str] = set()
    for p in plan.pages:
        if p.id is not None:
            if p.id in seen:
                raise OpRejected(f"duplicate caption page id {p.id}")
            seen.add(p.id)
    created = []
    top = max([doc.counters.get("p", 0), *(n for n in (id_number(i) for i in seen) if n is not None)])
    for p in plan.pages:
        if p.id is None:
            top += 1
            p.id = format_id("p", top)
            created.append(p.id)
    doc.counters["p"] = max(top, doc.counters.get("p", 0))
    return created


# -------------------------------------------------------------------------------------------- reconcile
def _reconcile(old: CutDocument, new: CutDocument, ix: TakeIndex, ctx: _Ctx) -> None:
    """Re-anchor items after story changes (warn on every change; drop items with no words left)."""
    old_order = old.kept_word_ids(ix)
    old_pos: dict[str, int] = {}
    for i, w in enumerate(old_order):
        old_pos.setdefault(w, i)
    new_pos = new.output_positions(ix)

    def remap(fr: str, to: str, label: str) -> tuple[str, str] | Literal[False] | None:
        """(new_from, new_to), None = drop, False = unchanged."""
        if fr in new_pos and to in new_pos:
            if new_pos[fr] > new_pos[to]:
                ctx.warnings.append(f"{label}: anchors {fr}-{to} are now out of output order")
            return False
        if fr not in old_pos or to not in old_pos or old_pos[fr] > old_pos[to]:
            ctx.warnings.append(f"{label}: anchors {fr}-{to} are not in the story")
            return False
        kept = [w for w in old_order[old_pos[fr]:old_pos[to] + 1] if w in new_pos]
        if not kept:
            return None
        nf = min(kept, key=lambda w: new_pos[w])
        nt = max(kept, key=lambda w: new_pos[w])
        ctx.warnings.append(f"{label}: re-anchored {fr}-{to} -> {nf}-{nt}")
        return nf, nt

    ins_out = []
    for ins in new.inserts:
        r = remap(ins.anchor_from_word, ins.anchor_to_word, f"insert {ins.id}")
        if r is None:
            ctx.warnings.append(f"insert {ins.id} dropped: all its anchor words were cut")
            continue
        if r:
            ins = ins.model_copy(update={"anchor_from_word": r[0], "anchor_to_word": r[1]})
        ins_out.append(ins)
    new.inserts = ins_out

    txt_out = []
    for t in new.texts:
        r = remap(t.anchor_from_word, t.anchor_to_word, f"text {t.id}")
        if r is None:
            ctx.warnings.append(f"text {t.id} dropped: all its anchor words were cut")
            continue
        if r:
            t = t.model_copy(update={"anchor_from_word": r[0], "anchor_to_word": r[1]})
        txt_out.append(t)
    new.texts = txt_out

    if new.captions is not None:
        pages = []
        for p in new.captions.pages:
            words = [w for w in p.word_ids if w in new_pos]
            if not words:
                ctx.warnings.append(f"caption page {p.id} dropped: its words were cut")
                continue
            if len(words) != len(p.word_ids):
                ctx.warnings.append(f"caption page {p.id}: removed cut words")
                p = p.model_copy(update={"word_ids": words,
                                         "emphasis_word_ids": [w for w in p.emphasis_word_ids if w in new_pos]})
            pages.append(p)
        ordered = [new_pos[w] for p in pages for w in p.word_ids]
        if any(b <= a for a, b in pairwise(ordered)):
            ctx.warnings.append("caption pages are no longer in output order; re-page the captions")
        new.captions.pages = pages

    sfx_out = []
    for x in new.audio.sfx:
        if x.anchor_word not in new_pos:
            ctx.warnings.append(f"sfx {x.id} dropped: anchor {x.anchor_word} was cut")
            continue
        sfx_out.append(x)
    new.audio.sfx = sfx_out

    m = new.audio.music
    if m is not None:
        upd: dict[str, Any] = {}
        if m.start_word is not None and m.start_word not in new_pos:
            nxt = next((w for w in old_order[old_pos.get(m.start_word, len(old_order)):] if w in new_pos), None)
            upd["start_word"] = nxt
            ctx.warnings.append(f"music start re-anchored {m.start_word} -> {nxt or 'start'}")
        if m.end_word is not None and m.end_word not in new_pos:
            prev_ = None
            if m.end_word in old_pos:
                prev_ = next((w for w in reversed(old_order[:old_pos[m.end_word] + 1]) if w in new_pos), None)
            upd["end_word"] = prev_
            ctx.warnings.append(f"music end re-anchored {m.end_word} -> {prev_ or 'end'}")
        hits = [w for w in m.hit_word_ids if w in new_pos]
        if len(hits) != len(m.hit_word_ids):
            upd["hit_word_ids"] = hits
            ctx.warnings.append("music hits on cut words removed")
        if upd:
            new.audio.music = m.model_copy(update=upd)


def _post_check(doc: CutDocument, ix: TakeIndex) -> None:
    """Structural safety net + pins (invariant 4)."""
    cov = _kept_positions(doc, ix)  # raises on overlap/unknown ids
    for s in doc.segments:
        a, b = _source_range(ix, s.from_word, s.to_word, f"segment {s.id}")
        inner = _inner_gaps_in(ix, a, b)
        for gid, ms in s.gap_overrides.items():
            if gid not in inner:
                raise OpRejected(f"{s.id}: gap {gid} is not inside the segment")
            if _gap_too_long(ms, ix.gap(gid).duration_us):
                raise OpRejected(f"{s.id}: gap {gid} target {ms} ms is longer than measured")
        if s.framing is not None and s.framing.anchor_word is not None:
            ap = _wpos(ix, s.framing.anchor_word)
            if not (a <= ap <= b):
                raise OpRejected(f"{s.id}: framing anchor {s.framing.anchor_word} is outside the segment")
    kept_ids = {ix.words[p].id for p in cov}
    missing = [(w, k) for w, k in doc.pins.all().items() if w not in kept_ids]
    if missing:
        desc = ", ".join(f"{k} {_word_label(ix, w)}" for w, k in missing[:6])
        raise OpRejected(f"would remove pinned word(s): {desc} (unpin first)")


# ============================================================================================ handlers
def _h_set_story(doc: CutDocument, op: SetStory, ctx: _Ctx) -> None:
    ix = ctx.index
    old = doc.model_copy(deep=True)
    segs: list[Segment] = []
    seen: dict[int, int] = {}
    for k, spec in enumerate(op.segments):
        a, b = _source_range(ix, spec.from_word, spec.to_word, f"segment #{k + 1}")
        for p in range(a, b + 1):
            if p in seen:
                raise OpRejected(f"word {ix.words[p].id} is in segment #{seen[p] + 1} and #{k + 1}; "
                                 "each word may appear once")
            seen[p] = k
        inner = _inner_gaps_in(ix, a, b)
        overrides: dict[str, int] = {}
        for go in spec.gap_overrides:
            if go.gap_id not in ix.gap_map:
                raise OpRejected(f"unknown gap id {go.gap_id}")
            if go.gap_id not in inner:
                raise OpRejected(f"gap {go.gap_id} is not inside segment #{k + 1}")
            g = ix.gap(go.gap_id)
            if _gap_too_long(go.ms, g.duration_us):
                raise OpRejected(f"gap {go.gap_id} is {g.duration_ms:.0f} ms; cannot lengthen to {go.ms} ms")
            overrides[go.gap_id] = go.ms
        framing = spec.framing
        if framing is not None and framing.anchor_word is not None:
            ap = _wpos(ix, framing.anchor_word)
            if not (a <= ap <= b):
                raise OpRejected(f"framing anchor {framing.anchor_word} is outside segment #{k + 1}")
        seam = spec.seam_in
        if k == 0 and seam.kind not in ("cut", "punch"):
            ctx.warnings.append(f"first segment has no incoming seam; '{seam.kind}' replaced by 'cut'")
            seam = SeamTreatment()
        segs.append(Segment(id="seg000", from_word=spec.from_word, to_word=spec.to_word, speed=spec.speed,
                            gap_overrides=overrides, framing=framing, seam_in=seam, note=spec.note))
    doc.segments = []
    for s in segs:
        s.id = _next_id(doc, "seg")
        doc.segments.append(s)
        ctx.ids.append(s.id)
    _rebuild_removed(doc, ix, op.removed, op.reason or "not selected for the story", ctx)
    _reconcile(old, doc, ix, ctx)


def _h_cut_words(doc: CutDocument, op: CutWords, ctx: _Ctx) -> None:
    ix = ctx.index
    a, b = _source_range(ix, op.from_word, op.to_word)
    old = doc.model_copy(deep=True)
    removed = _remove_positions(doc, ix, set(range(a, b + 1)), ctx)
    if not removed:
        raise OpRejected(f"nothing to cut: {op.from_word}-{op.to_word} is not in the story")
    _add_removed(doc, ix, removed, op.reason)
    _reconcile(old, doc, ix, ctx)


def _h_restore_words(doc: CutDocument, op: RestoreWords, ctx: _Ctx) -> None:
    ix = ctx.index
    a, b = _source_range(ix, op.from_word, op.to_word)
    kept = _kept_positions(doc, ix)
    missing = [p for p in range(a, b + 1) if p not in kept]
    if not missing:
        raise OpRejected(f"no change: {op.from_word}-{op.to_word} is already in the story")
    for ra, rb in _runs(missing):
        by_end = {ix.word_pos(s.to_word): i for i, s in enumerate(doc.segments)}
        by_start = {ix.word_pos(s.from_word): i for i, s in enumerate(doc.segments)}
        li, ri = by_end.get(ra - 1), by_start.get(rb + 1)
        if li is not None and ri is not None and ri == li + 1 and _compatible(doc.segments[li], doc.segments[ri]):
            L, R = doc.segments[li], doc.segments[ri]
            merged = dict(L.gap_overrides)
            merged.update(R.gap_overrides)
            doc.segments[li] = L.model_copy(update={"to_word": R.to_word, "gap_overrides": merged})
            del doc.segments[ri]
            ctx.warnings.append(f"{R.id} merged into {L.id}")
        elif li is not None:
            L = doc.segments[li]
            doc.segments[li] = L.model_copy(update={"to_word": ix.words[rb].id})
            if ri is not None and doc.segments[ri].seam_in.kind in ("jcut", "lcut", "crossfade", "cutaway"):
                R = doc.segments[ri]
                kind = "punch" if R.framing != L.framing else "cut"
                doc.segments[ri] = R.model_copy(update={"seam_in": SeamTreatment(kind=kind)})
                ctx.warnings.append(f"{R.id}: seam reset to {kind} (no longer a cut point)")
        elif ri is not None:
            R = doc.segments[ri]
            doc.segments[ri] = R.model_copy(update={"from_word": ix.words[ra].id})
        else:
            nid = _next_id(doc, "seg")
            _insert_by_source(doc, ix, Segment(id=nid, from_word=ix.words[ra].id, to_word=ix.words[rb].id))
            ctx.ids.append(nid)
    _trim_removed(doc, ix, set(missing))


def _h_choose_take(doc: CutDocument, op: ChooseTake, ctx: _Ctx) -> None:
    """Keep one take of a retake cluster where the cluster first appears in the cut; drop the others."""
    ix = ctx.index
    try:
        cl = ix.cluster(op.cluster_id)
    except KeyError:
        raise OpRejected(f"unknown cluster id {op.cluster_id}") from None
    if op.sentence_id not in cl.sentence_ids:
        raise OpRejected(f"{op.sentence_id} is not a take of {op.cluster_id} "
                         f"(takes: {', '.join(cl.sentence_ids)})")
    chosen_words = ix.sentence(op.sentence_id).word_ids
    ca, cb = ix.word_pos(chosen_words[0]), ix.word_pos(chosen_words[-1])
    chosen = set(range(ca, cb + 1))
    others = {ix.word_pos(w) for sid in cl.sentence_ids if sid != op.sentence_id
              for w in ix.sentence(sid).word_ids} - chosen
    kept = _kept_positions(doc, ix)
    if chosen <= set(kept) and not (others & set(kept)):
        raise OpRejected(f"no change: {op.sentence_id} is already the take of {op.cluster_id} in the story")
    old = doc.model_copy(deep=True)
    group = chosen | others
    # the chosen take goes where the cluster first appears in output order (else by source order)
    anchor: tuple[int, int] | None = None
    for i, seg in enumerate(doc.segments):
        a, b = _seg_range(ix, seg)
        hit = [p for p in range(a, b + 1) if p in group]
        if hit:
            anchor = (i, hit[0])
            break
    new_id = _next_id(doc, "seg")
    new_seg = Segment(id=new_id, from_word=ix.words[ca].id, to_word=ix.words[cb].id)
    if anchor is None:
        _insert_by_source(doc, ix, new_seg)
    else:
        i, p = anchor
        host = doc.segments[i]
        a, b = _seg_range(ix, host)
        new_seg = new_seg.model_copy(update={
            "speed": host.speed,
            "framing": _piece(ix, host, ca, cb).framing if host.framing is not None else None,
        })
        if p > a:
            pieces = [_piece(ix, host, a, p - 1), new_seg,
                      _piece(ix, host, p, b, new_id=_next_id(doc, "seg"), seam=SeamTreatment())]
        else:
            new_seg = new_seg.model_copy(update={"seam_in": host.seam_in.model_copy()})
            pieces = [new_seg, _piece(ix, host, p, b, seam=SeamTreatment())]
        doc.segments[i:i + 1] = pieces
    removed = _remove_positions(doc, ix, group, ctx, exclude=frozenset({new_id}), quiet=True)
    dropped = removed & others
    if dropped:
        _add_removed(doc, ix, dropped, op.reason or f"retake: chose {op.sentence_id} in {op.cluster_id}")
        for ra, rb in _runs(dropped):
            ctx.warnings.append(f"removed other take words {ix.words[ra].id}-{ix.words[rb].id}")
    _trim_removed(doc, ix, chosen)
    _merge_contiguous(doc, ix, ctx, only={new_id})
    owner = next(s.id for s in doc.segments if ix.word_pos(s.from_word) <= ca <= ix.word_pos(s.to_word))
    ctx.ids.append(owner)
    _reconcile(old, doc, ix, ctx)


def _h_move_segment(doc: CutDocument, op: MoveSegment, ctx: _Ctx) -> None:
    try:
        i = doc.segment_index(op.seg_id)
    except KeyError:
        raise OpRejected(f"unknown segment {op.seg_id}") from None
    if op.after == op.seg_id:
        raise OpRejected("cannot move a segment after itself")
    if op.after is not None and not any(s.id == op.after for s in doc.segments):
        raise OpRejected(f"unknown segment {op.after}")
    old = doc.model_copy(deep=True)
    seg = doc.segments.pop(i)
    j = 0 if op.after is None else doc.segment_index(op.after) + 1
    doc.segments.insert(j, seg)
    first = doc.segments[0]
    if first.seam_in.kind not in ("cut", "punch"):
        doc.segments[0] = first.model_copy(update={"seam_in": SeamTreatment()})
        ctx.warnings.append(f"{first.id} is now first; its '{first.seam_in.kind}' seam reset to cut")
    _reconcile(old, doc, ctx.index, ctx)


def _h_set_gap(doc: CutDocument, op: SetGap, ctx: _Ctx) -> None:
    ix = ctx.index
    try:
        g = ix.gap(op.gap_id)
    except KeyError:
        raise OpRejected(f"unknown gap id {op.gap_id}") from None
    if not g.is_inner:
        raise OpRejected(f"{op.gap_id} is a leading/trailing gap; the compiler trims those")
    kept = _kept_positions(doc, ix)
    pa, pb = ix.word_pos(g.after_word_id), ix.word_pos(g.before_word_id)  # type: ignore[arg-type]
    if pa not in kept or pb not in kept or kept[pa] != kept[pb]:
        raise OpRejected(f"{op.gap_id} is not inside a kept segment (it is at a cut or removed)")
    if _gap_too_long(op.ms, g.duration_us):
        raise OpRejected(f"{op.gap_id} is {g.duration_ms:.0f} ms; cannot lengthen a pause to {op.ms} ms")
    i = doc.segment_index(kept[pa])
    seg = doc.segments[i]
    ov = dict(seg.gap_overrides)
    if op.ms * 1000 >= g.duration_us - 500:  # within half a millisecond of natural = restore
        ov.pop(op.gap_id, None)
    else:
        ov[op.gap_id] = op.ms
    doc.segments[i] = seg.model_copy(update={"gap_overrides": ov})


def _h_set_speed(doc: CutDocument, op: SetSpeed, ctx: _Ctx) -> None:
    try:
        i = doc.segment_index(op.seg_id)
    except KeyError:
        raise OpRejected(f"unknown segment {op.seg_id}") from None
    doc.segments[i] = doc.segments[i].model_copy(update={"speed": op.speed})


def _h_set_seam(doc: CutDocument, op: SetSeam, ctx: _Ctx) -> None:
    try:
        i = doc.segment_index(op.seg_id)
    except KeyError:
        raise OpRejected(f"unknown segment {op.seg_id}") from None
    t = op.treatment
    if i == 0 and t.kind not in ("cut", "punch"):
        raise OpRejected(f"{op.seg_id} is the first segment; it has no incoming seam for '{t.kind}'")
    if t.kind in ("jcut", "lcut", "crossfade") and t.lead_ms <= 0:
        raise OpRejected(f"'{t.kind}' needs lead_ms > 0")
    doc.segments[i] = doc.segments[i].model_copy(update={"seam_in": t.model_copy()})


def _resolve_target(doc: CutDocument, op: _Target, ctx: _Ctx) -> tuple[int, int, int]:
    """(segment index, a, b) for a framing target; word ranges must lie inside one segment."""
    ix = ctx.index
    if op.seg_id is not None:
        try:
            i = doc.segment_index(op.seg_id)
        except KeyError:
            raise OpRejected(f"unknown segment {op.seg_id}") from None
        a, b = _seg_range(ix, doc.segments[i])
        return i, a, b
    a, b = _source_range(ix, op.from_word, op.to_word)  # type: ignore[arg-type]
    kept = _kept_positions(doc, ix)
    owners = {kept.get(p) for p in range(a, b + 1)}
    if None in owners:
        raise OpRejected(f"{op.from_word}-{op.to_word} includes words that are not in the story")
    if len(owners) > 1:
        raise OpRejected(f"{op.from_word}-{op.to_word} spans {', '.join(sorted(o for o in owners if o))}; "
                         "target one segment at a time")
    return doc.segment_index(owners.pop()), a, b  # type: ignore[arg-type]


def _h_set_framing(doc: CutDocument, op: SetFraming, ctx: _Ctx) -> None:
    ix = ctx.index
    i, a, b = _resolve_target(doc, op, ctx)
    host = doc.segments[i]
    sa, sb = _seg_range(ix, host)
    anchor = op.anchor_word
    if anchor is not None:
        ap = _wpos(ix, anchor)
        if not (a <= ap <= b):
            raise OpRejected(f"anchor {anchor} is outside the framed range")
    fr = Framing(scale=op.scale, center=op.center, ease=op.ease, ease_ms=op.ease_ms, anchor_word=anchor)
    if (a, b) == (sa, sb):
        doc.segments[i] = host.model_copy(update={"framing": fr})
        return
    pieces: list[Segment] = []
    if a > sa:
        pieces.append(_piece(ix, host, sa, a - 1))
        mid_id = _next_id(doc, "seg")
        mid = _piece(ix, host, a, b, new_id=mid_id, seam=SeamTreatment(kind="punch"))
        ctx.ids.append(mid_id)
    else:
        mid = _piece(ix, host, a, b)
    mid = mid.model_copy(update={"framing": fr})
    pieces.append(mid)
    if b < sb:
        rid = _next_id(doc, "seg")
        pieces.append(_piece(ix, host, b + 1, sb, new_id=rid, seam=SeamTreatment(kind="punch")))
        ctx.warnings.append(f"{host.id} split at the framed range; tail is {rid}")
    doc.segments[i:i + 1] = pieces


def _h_clear_framing(doc: CutDocument, op: ClearFraming, ctx: _Ctx) -> None:
    ix = ctx.index
    if op.seg_id is not None:
        try:
            targets = {op.seg_id: doc.segment_index(op.seg_id)}
        except KeyError:
            raise OpRejected(f"unknown segment {op.seg_id}") from None
    else:
        a, b = _source_range(ix, op.from_word, op.to_word)  # type: ignore[arg-type]
        targets = {}
        for k, s in enumerate(doc.segments):
            sa, sb = _seg_range(ix, s)
            if sa <= b and a <= sb:
                targets[s.id] = k
        if not targets:
            raise OpRejected(f"{op.from_word}-{op.to_word} is not in the story")
    for k in targets.values():
        doc.segments[k] = doc.segments[k].model_copy(update={"framing": None})
    _merge_contiguous(doc, ix, ctx, only=set(targets))


def _h_add_insert(doc: CutDocument, op: AddInsert, ctx: _Ctx) -> None:
    visual: AssetRef | CardSpec = (_resolve_asset(ctx, op.asset_id, "insert") if op.asset_id is not None
                                   else op.card.model_copy(deep=True))  # type: ignore[union-attr]
    alternates = [_resolve_asset(ctx, a, "insert alternate") for a in op.alternate_asset_ids]
    data = op.model_dump(exclude={"op", "asset_id", "card", "alternate_asset_ids"})
    ins = Insert(id="i000", asset=visual, alternates=alternates, **data)
    _check_insert(doc, ctx.index, ins)
    ins.id = _next_id(doc, "i")
    doc.inserts.append(ins)
    ctx.ids.append(ins.id)


def _h_update_insert(doc: CutDocument, op: UpdateInsert, ctx: _Ctx) -> None:
    try:
        cur = doc.insert(op.id)
    except KeyError:
        raise OpRejected(f"unknown insert {op.id}") from None
    upd = op.model_dump(exclude={"op", "id", "asset_id", "card"}, exclude_none=True)
    if op.asset_id is not None:
        upd["asset"] = _resolve_asset(ctx, op.asset_id, f"insert {op.id}").model_dump()
    elif op.card is not None:
        upd["asset"] = op.card.model_dump()
    if not upd:
        raise OpRejected("no fields to update")
    merged = cur.model_dump()
    merged.update(upd)
    ins = Insert.model_validate(merged)
    _check_insert(doc, ctx.index, ins)
    doc.inserts = [ins if x.id == op.id else x for x in doc.inserts]


def _h_remove_insert(doc: CutDocument, op: RemoveInsert, ctx: _Ctx) -> None:
    if not any(x.id == op.id for x in doc.inserts):
        raise OpRejected(f"unknown insert {op.id}")
    doc.inserts = [x for x in doc.inserts if x.id != op.id]


def _h_set_captions(doc: CutDocument, op: SetCaptions, ctx: _Ctx) -> None:
    plan = op.plan.model_copy(deep=True)
    _check_pages(doc, ctx.index, plan.pages)
    ctx.ids.extend(_assign_page_ids(doc, plan))
    if plan.enabled and not plan.pages:
        ctx.warnings.append("captions enabled but no pages")
    doc.captions = plan


def _h_edit_caption_page(doc: CutDocument, op: EditCaptionPage, ctx: _Ctx) -> None:
    if doc.captions is None:
        raise OpRejected("no caption plan; use set_captions first")
    try:
        cur = doc.caption_page(op.page_id)
    except KeyError:
        raise OpRejected(f"unknown caption page {op.page_id}") from None
    upd: dict[str, Any] = {}
    if op.word_ids is not None:
        upd["word_ids"] = op.word_ids
        if op.emphasis_word_ids is None:
            upd["emphasis_word_ids"] = [w for w in cur.emphasis_word_ids if w in op.word_ids]
    if op.emphasis_word_ids is not None:
        upd["emphasis_word_ids"] = op.emphasis_word_ids
    if op.text is not None:
        upd["text"] = op.text or None
    if op.position is not None:
        upd["position"] = op.position
    if op.y_norm is not None:
        upd["y_norm"] = op.y_norm
    if not upd:
        raise OpRejected("no fields to update")
    data = cur.model_dump()
    data.update(upd)
    try:
        page = CaptionPage.model_validate(data)
    except ValidationError as e:
        raise OpRejected(_fmt_validation(e)) from None
    pages = [page if p.id == op.page_id else p for p in doc.captions.pages]
    _check_pages(doc, ctx.index, pages)
    doc.captions.pages = pages


def _h_set_caption_style(doc: CutDocument, op: SetCaptionStyle, ctx: _Ctx) -> None:
    if doc.captions is None:
        doc.captions = CaptionPlan(style=op.style)
        ctx.warnings.append("no caption pages yet (style stored)")
    else:
        doc.captions.style = op.style.model_copy()
    if op.enabled is not None:
        doc.captions.enabled = op.enabled
    if op.position is not None:
        doc.captions.position = op.position


def _h_add_text(doc: CutDocument, op: AddText, ctx: _Ctx) -> None:
    _check_anchor_span(doc, ctx.index, op.anchor_from_word, op.anchor_to_word, "text")
    t = TextOverlay(id=_next_id(doc, "t"), **op.model_dump(exclude={"op"}))
    doc.texts.append(t)
    ctx.ids.append(t.id)


def _h_update_text(doc: CutDocument, op: UpdateText, ctx: _Ctx) -> None:
    try:
        cur = doc.text(op.id)
    except KeyError:
        raise OpRejected(f"unknown text {op.id}") from None
    upd = op.model_dump(exclude={"op", "id"}, exclude_none=True)
    if not upd:
        raise OpRejected("no fields to update")
    data = cur.model_dump()
    data.update(upd)
    t = TextOverlay.model_validate(data)
    _check_anchor_span(doc, ctx.index, t.anchor_from_word, t.anchor_to_word, f"text {t.id}")
    doc.texts = [t if x.id == op.id else x for x in doc.texts]


def _h_remove_text(doc: CutDocument, op: RemoveText, ctx: _Ctx) -> None:
    if not any(x.id == op.id for x in doc.texts):
        raise OpRejected(f"unknown text {op.id}")
    doc.texts = [x for x in doc.texts if x.id != op.id]


def _h_set_voice_chain(doc: CutDocument, op: SetVoiceChain, ctx: _Ctx) -> None:
    doc.audio.voice = op.spec.model_copy(deep=True)


def _h_set_music(doc: CutDocument, op: SetMusic, ctx: _Ctx) -> None:
    params = op.spec
    if params is None:
        doc.audio.music = None
        return
    ix = ctx.index
    asset = _resolve_asset(ctx, params.asset_id, "music") if params.asset_id is not None else None
    pos = _require_kept(doc, ix, [w for w in (params.start_word, params.end_word) if w] + params.hit_word_ids,
                        "music")
    if params.start_word and params.end_word and pos[params.start_word] > pos[params.end_word]:
        raise OpRejected(f"music start {params.start_word} comes after end {params.end_word}")
    doc.audio.music = MusicSpec(**params.model_dump(), asset=asset)


def _h_add_sfx(doc: CutDocument, op: AddSfx, ctx: _Ctx) -> None:
    _require_kept(doc, ctx.index, [op.anchor_word], "sfx")
    asset = _resolve_asset(ctx, op.asset_id, "sfx") if op.asset_id is not None else None
    cue = SfxCue(id=_next_id(doc, "fx"), asset=asset, **op.model_dump(exclude={"op", "asset_id"}))
    doc.audio.sfx.append(cue)
    ctx.ids.append(cue.id)


def _h_remove_sfx(doc: CutDocument, op: RemoveSfx, ctx: _Ctx) -> None:
    if not any(x.id == op.id for x in doc.audio.sfx):
        raise OpRejected(f"unknown sfx {op.id}")
    doc.audio.sfx = [x for x in doc.audio.sfx if x.id != op.id]


def _h_set_loudness(doc: CutDocument, op: SetLoudness, ctx: _Ctx) -> None:
    doc.audio.loudness_target_lufs = op.lufs
    if op.true_peak_dbtp is not None:
        doc.audio.true_peak_dbtp = op.true_peak_dbtp


def _h_set_color(doc: CutDocument, op: SetColor, ctx: _Ctx) -> None:
    doc.color = op.spec.model_copy() if op.spec is not None else None


def _h_set_brief(doc: CutDocument, op: SetBrief, ctx: _Ctx) -> None:
    doc.brief = op.brief.model_copy(deep=True)


def _h_set_style(doc: CutDocument, op: SetStyle, ctx: _Ctx) -> None:
    doc.style = op.style.model_copy(deep=True)


def _h_pin(doc: CutDocument, op: Pin, ctx: _Ctx) -> None:
    _require_kept(doc, ctx.index, op.word_ids, f"pin {op.kind}")
    cur = doc.pins.of_kind(op.kind)
    for w in op.word_ids:
        if w not in cur:
            cur.append(w)


def _h_unpin(doc: CutDocument, op: Unpin, ctx: _Ctx) -> None:
    cur = doc.pins.of_kind(op.kind)
    absent = [w for w in op.word_ids if w not in cur]
    if len(absent) == len(op.word_ids):
        raise OpRejected(f"no change: none of {', '.join(op.word_ids)} is pinned as {op.kind}")
    if absent:
        ctx.warnings.append(f"not pinned as {op.kind}: {', '.join(absent)}")
    cur[:] = [w for w in cur if w not in op.word_ids]


def _h_note(doc: CutDocument, op: NoteOp, ctx: _Ctx) -> None:
    doc.notes.append(Note(by=ctx.by, text=op.text, ref=op.ref, version=ctx.new_version))


def _h_set_deliverables(doc: CutDocument, op: SetDeliverables, ctx: _Ctx) -> None:
    seen = set()
    for d in op.deliverables:
        key = (d.platform, d.variant)
        if key in seen:
            raise OpRejected(f"duplicate deliverable {d.platform}/{d.variant}")
        seen.add(key)
    doc.deliverables = [d.model_copy() for d in op.deliverables]


def _h_set_hook_alternates(doc: CutDocument, op: SetHookAlternates, ctx: _Ctx) -> None:
    ix = ctx.index
    alts: list[HookAlternate] = []
    doc.hook_alternates = []
    for k, spec in enumerate(op.alternates):
        for r in spec.ranges:
            _source_range(ix, r.from_word, r.to_word, f"hook alternate #{k + 1}")
        hid = _next_id(doc, "h")
        alts.append(HookAlternate(id=hid, ranges=[r.model_copy() for r in spec.ranges],
                                  title_text=spec.title_text, note=spec.note))
        doc.hook_alternates = list(alts)
        ctx.ids.append(hid)
    doc.hook_alternates = alts


_HANDLERS: dict[str, Callable[[CutDocument, Any, _Ctx], None]] = {
    "set_story": _h_set_story, "cut_words": _h_cut_words, "restore_words": _h_restore_words,
    "choose_take": _h_choose_take, "move_segment": _h_move_segment, "set_gap": _h_set_gap,
    "set_speed": _h_set_speed, "set_seam": _h_set_seam,
    "set_framing": _h_set_framing, "clear_framing": _h_clear_framing,
    "add_insert": _h_add_insert, "update_insert": _h_update_insert, "remove_insert": _h_remove_insert,
    "set_captions": _h_set_captions, "edit_caption_page": _h_edit_caption_page,
    "set_caption_style": _h_set_caption_style, "add_text": _h_add_text, "update_text": _h_update_text,
    "remove_text": _h_remove_text,
    "set_voice_chain": _h_set_voice_chain, "set_music": _h_set_music, "add_sfx": _h_add_sfx,
    "remove_sfx": _h_remove_sfx, "set_loudness": _h_set_loudness,
    "set_color": _h_set_color,
    "set_brief": _h_set_brief, "set_style": _h_set_style, "pin": _h_pin, "unpin": _h_unpin,
    "note": _h_note, "set_deliverables": _h_set_deliverables, "set_hook_alternates": _h_set_hook_alternates,
}
assert set(_HANDLERS) | {"undo"} == set(OP_CLASSES), "every op needs a handler"


# ============================================================================================ apply
def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def oplog_entries(
    results: Sequence[OpResult],
    raw_ops: Sequence[Any],
    *,
    base_version: int,
    new_version: int,
    by: str,
) -> list[dict[str, Any]]:
    """Oplog records (one per op) for ``doc/oplog.jsonl``."""
    ts = _now_iso()
    out = []
    for r in results:
        raw = raw_ops[r.index] if r.index < len(raw_ops) else None
        op_json = raw.model_dump(mode="json") if isinstance(raw, BaseModel) else copy.deepcopy(raw)
        out.append({
            "ts": ts, "by": by, "base_version": base_version,
            "version": new_version if r.applied else base_version,
            "op": op_json, "applied": r.applied, "reason": r.reason, "ids": r.ids, "warnings": r.warnings,
        })
    return out


def apply_ops(
    doc: CutDocument,
    ops: Sequence[OpBase | Mapping[str, Any]],
    index: TakeIndex,
    *,
    job: Job | None = None,
    by: str = "director",
    persist: bool = True,
    history: Callable[[int], CutDocument | None] | None = None,
    assets: Mapping[str, AssetRef] | AssetResolver | None = None,
) -> tuple[CutDocument, list[OpResult]]:
    """Apply ops in order; return ``(new_doc, results)``. Never raises on bad ops.

    ``doc`` is not modified. If nothing applied, the same ``doc`` object is returned (no version bump).
    With ``job`` (and ``persist``), the new version is saved and all ops are appended to the oplog.
    ``history(v)`` returns saved version ``v`` for ``undo`` (defaults to ``job.load_doc``).
    ``assets`` resolves ``asset_id`` references (a mapping or callable; defaults to ``job.load_asset``).
    """
    base = doc.version
    latest = job.latest_doc_version() if job is not None else None
    new_version = max(base, latest if latest is not None else -1) + 1
    results: list[OpResult] = []
    parsed: list[OpBase | None] = []
    for i, raw in enumerate(ops):
        try:
            parsed.append(parse_op(raw))
        except ValueError as e:
            name = raw.get("op", "?") if isinstance(raw, Mapping) else "?"
            parsed.append(None)
            results.append(OpResult(index=i, op=str(name), applied=False, reason=str(e)))

    if history is None and job is not None:
        def history(v: int) -> CutDocument | None:
            try:
                return job.load_doc(v)
            except Exception:
                return None

    resolver: AssetResolver | None
    if isinstance(assets, Mapping):
        resolver = assets.get
    elif assets is not None:
        resolver = assets
    else:
        resolver = job.load_asset if job is not None else None

    working = doc.model_copy(deep=True)
    any_applied = False
    undo_count = sum(1 for p in parsed if isinstance(p, Undo))

    for i, op in enumerate(parsed):
        if op is None:
            continue
        name = op.op  # type: ignore[attr-defined]
        if isinstance(op, Undo):
            if len(ops) != 1 or undo_count != 1:
                results.append(OpResult(index=i, op=name, applied=False,
                                        reason="undo must be the only op in its batch"))
                continue
            if history is None:
                results.append(OpResult(index=i, op=name, applied=False,
                                        reason="undo needs the job history (pass job= or history=)"))
                continue
            prior: CutDocument | None = doc
            steps = 0
            while steps < op.n:
                parent = prior.parent_version if prior is not None else None
                nxt = history(parent) if parent is not None else None
                if nxt is None:
                    prior = None
                    break
                prior, steps = nxt, steps + 1
            if prior is None:
                results.append(OpResult(index=i, op=name, applied=False,
                                        reason=f"cannot undo {op.n}: only {steps} earlier version(s) available "
                                               f"from v{base}"))
                continue
            target = prior.version
            working = prior.model_copy(deep=True)
            for k, v in doc.counters.items():  # never re-issue an ID after undo
                working.counters[k] = max(v, working.counters.get(k, 0))
            any_applied = True
            results.append(OpResult(index=i, op=name, applied=True, reason=f"restored content of v{target}"))
            continue

        ctx = _Ctx(index=index, by=by, new_version=new_version, assets=resolver)
        before = working.model_dump(mode="json")
        trial = working.model_copy(deep=True)
        try:
            _HANDLERS[name](trial, op, ctx)
            trial = CutDocument.model_validate(trial.model_dump())
            _post_check(trial, index)
        except OpRejected as e:
            results.append(OpResult(index=i, op=name, applied=False, reason=str(e)))
            continue
        except ValidationError as e:
            results.append(OpResult(index=i, op=name, applied=False, reason=_fmt_validation(e)))
            continue
        except KeyError as e:
            results.append(OpResult(index=i, op=name, applied=False, reason=str(e).strip("'\"")))
            continue
        except Exception as e:  # never raise on bad ops; surface internal errors as rejections
            results.append(OpResult(index=i, op=name, applied=False,
                                    reason=f"internal error {type(e).__name__}: {e}"))
            if job is not None:
                job.trace("op_internal_error", op=name, error=f"{type(e).__name__}: {e}")
            continue
        if trial.model_dump(mode="json") == before:
            results.append(OpResult(index=i, op=name, applied=False, reason="no change",
                                    warnings=ctx.warnings))
            continue
        working = trial
        any_applied = True
        results.append(OpResult(index=i, op=name, applied=True, ids=ctx.ids, warnings=ctx.warnings))

    results.sort(key=lambda r: r.index)
    if any_applied:
        working.version = new_version
        working.parent_version = base
        if not working.job_id:
            working.job_id = doc.job_id or (job.id if job is not None else "")
        out = CutDocument.model_validate(working.model_dump())
    else:
        out = doc
    if job is not None and persist:
        if any_applied:
            out = _save_new_version(job, out)
        job.append_oplog(oplog_entries(results, list(ops), base_version=base,
                                       new_version=out.version, by=by))
    return out, results


def _save_new_version(job: Job, doc: CutDocument) -> CutDocument:
    """Save ``doc`` as a new version; if another writer took the number meanwhile, take the next one."""
    from studio.jobs import JobError

    for _ in range(20):
        try:
            job.save_doc(doc)
            return doc
        except JobError:
            latest = job.latest_doc_version() or doc.version
            doc = doc.model_copy(update={"version": max(latest, doc.version) + 1})
    job.save_doc(doc, overwrite=True)  # pragma: no cover - pathological contention
    return doc


def undo(doc: CutDocument, index: TakeIndex, job: Job, n: int = 1, *, by: str = "director",
         ) -> tuple[CutDocument, OpResult]:
    """Convenience: ``apply_ops(doc, [Undo(n=n)], index, job=job)``."""
    new, res = apply_ops(doc, [Undo(n=n)], index, job=job, by=by)
    return new, res[0]


# ============================================================================================ schemas
_SUPPORTED_FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
_NUM_KEYS = {"minimum": "at least", "maximum": "at most", "exclusiveMinimum": "greater than",
             "exclusiveMaximum": "less than", "multipleOf": "a multiple of"}
_STR_KEYS = {"minLength": "min length", "maxLength": "max length"}


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic JSON schema acceptable to strict tool use (Anthropic structured outputs subset):
    ``oneOf``→``anyOf``, no ``discriminator``, ``additionalProperties: false`` on every object, numeric/
    string-length constraints and ``minItems`` > 1 / ``maxItems`` moved into ``description``, unsupported
    ``format`` values dropped. Raises ``ValueError`` on open maps (``additionalProperties`` schemas).
    Pydantic still validates the full constraints when the op is applied."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        n = {k: walk(v) for k, v in node.items() if k not in ("discriminator",)}
        if "oneOf" in n:
            n["anyOf"] = n.pop("oneOf")
        notes: list[str] = []
        for k, word in _NUM_KEYS.items():
            if k in n:
                notes.append(f"{word} {n.pop(k)}")
        for k, word in _STR_KEYS.items():
            if k in n:
                notes.append(f"{word} {n.pop(k)}")
        if "minItems" in n and n["minItems"] > 1:
            notes.append(f"at least {n.pop('minItems')} items")
        if "maxItems" in n:
            notes.append(f"at most {n.pop('maxItems')} items")
        if "format" in n and n["format"] not in _SUPPORTED_FORMATS:
            n.pop("format")
        if notes:
            desc = n.get("description", "")
            n["description"] = (desc + " " if desc else "") + "(" + "; ".join(notes) + ")"
        is_object = n.get("type") == "object" or "properties" in n
        if is_object:
            ap = n.get("additionalProperties")
            if isinstance(ap, dict):
                raise ValueError("open map (additionalProperties schema) is not strict-compatible")
            n["additionalProperties"] = False
        return n

    return walk(schema)


def _require_op(schema: dict[str, Any]) -> None:
    """Pydantic marks ``op`` optional (it has a default); tools must always send it."""
    nodes = [schema, *schema.get("$defs", {}).values()]
    for node in nodes:
        props = node.get("properties", {})
        if "op" in props and "const" in props["op"]:
            req = node.setdefault("required", [])
            if "op" not in req:
                req.insert(0, "op")


def op_json_schema(family: str | None = None, *, strict: bool = False) -> dict[str, Any]:
    """JSON schema of one op of ``family`` (all ops when None): a union discriminated by ``op``."""
    if family is not None and family not in OP_FAMILIES:
        raise KeyError(f"unknown op family {family!r}; families: {', '.join(OP_FAMILIES)}")
    classes = OP_FAMILIES[family] if family else tuple(OP_CLASSES.values())
    if len(classes) == 1:
        schema = classes[0].model_json_schema()
    else:
        union = Annotated[Union[classes], Field(discriminator="op")]  # type: ignore[valid-type]
        schema = TypeAdapter(union).json_schema()
    _require_op(schema)
    return strict_schema(schema) if strict else schema


def clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Light cleanup for non-strict tools: ``oneOf``→``anyOf``, no ``discriminator``, closed objects;
    numeric/string constraints are kept (they guide the model and Pydantic enforces them)."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        n = {k: walk(v) for k, v in node.items() if k != "discriminator"}
        if "oneOf" in n:
            n["anyOf"] = n.pop("oneOf")
        if (n.get("type") == "object" or "properties" in n) and "additionalProperties" not in n:
            n["additionalProperties"] = False
        return n

    return walk(schema)


def family_tool_schema(family: str, *, strict: bool = True) -> dict[str, Any]:
    """Tool ``input_schema`` for one family: ``{"ops": [<op>, …]}`` (``$defs`` hoisted to the root).
    ``strict=True`` returns the strict-tool subset (:func:`strict_schema`), else :func:`clean_schema`."""
    item = op_json_schema(family, strict=False)
    defs = item.pop("$defs", {})
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ops": {"type": "array", "items": item, "minItems": 1,
                    "description": f"{family} ops, applied in order"},
        },
        "required": ["ops"],
        "additionalProperties": False,
    }
    if defs:
        schema["$defs"] = defs
    return strict_schema(schema) if strict else clean_schema(schema)


#: Families sent with ``strict: true`` by default: none. Verified against the Anthropic API
#: (claude-opus-5-5, 2026-09-27): all seven families strict in one request, and even just cut + framing +
#: color strict, fail with HTTP 400 "The compiled grammar is too large … reduce the number of strict
#: tools". Non-strict ops are fully validated by :func:`apply_ops`, whose readable rejection reasons let
#: the model retry (plan §8 fallback). Strict export stays available via ``strict=True``/an iterable for
#: providers or flatter per-op schemas that fit a grammar budget.
DEFAULT_STRICT_FAMILIES: tuple[str, ...] = ()

#: Suggested tool sets per Director stage (smaller tool lists = smaller grammars and better focus).
STAGE_FAMILIES: dict[str, tuple[str, ...]] = {
    "brief": ("meta",),
    "story": ("cut", "meta"),
    "fine_cut": ("cut", "framing", "meta"),
    "finishing": ("framing", "inserts", "captions", "audio", "color", "meta"),
    "chat": tuple(OP_FAMILIES),
}


def tool_definitions(
    *,
    strict: bool | Iterable[str] = DEFAULT_STRICT_FAMILIES,
    families: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """One tool per op family, Anthropic tool format::

        [{"name": "cut_ops", "description": …, "strict": True, "input_schema": {…}}, …]

    ``strict``: True (every listed family strict — may exceed the provider's grammar limit when all
    seven are sent together), False (none), or an iterable of family names (default
    :data:`DEFAULT_STRICT_FAMILIES`). ``families`` limits the tools (e.g. ``STAGE_FAMILIES["story"]``).
    Tool input is ``{"ops": [...]}``; pass ``input["ops"]`` to :func:`apply_ops`.
    """
    fams = list(families) if families is not None else list(OP_FAMILIES)
    for f in fams:
        if f not in OP_FAMILIES:
            raise KeyError(f"unknown op family {f!r}")
    strict_set = set(fams) if strict is True else set() if strict is False else set(strict)  # type: ignore[arg-type]
    return [{"name": f"{f}_ops", "description": FAMILY_DESCRIPTIONS[f], "strict": f in strict_set,
             "input_schema": family_tool_schema(f, strict=f in strict_set)} for f in fams]
