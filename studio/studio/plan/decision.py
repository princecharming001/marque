"""The planner's answer: :class:`PlanDecision`, its compact schema string and the guards that reject an unusable one
(standard-tier plan §2 step 5, ``docs/standard-tier-interfaces.md`` "Decision").

The decision names **IDs only** (sentences, clusters, words, gaps from the Take Index), never times: the compiler
keeps invariant 3 ("no model timestamps") by construction. Every field is something the Director actually decided
on the real jobs (the ``Part one.`` drop, the payoff pin, one 1.15x lean-in, caption emphasis, take40's ``1/3``
labels, the "restates the hook" drop); everything else is code.

Why the models carry no ``pattern``/``max_length`` constraints: the planner call sends this schema as a native
structured output, and Anthropic's grammar compiler rejects or ignores string/array constraints (HANDOFF §6:
"compiled grammar too large" on strict tools). The limits live in :func:`validate_decision` instead, whose
rejections are plain text the retry carries back to the model.

:func:`validate_decision` is the planner's safety net, not its taste: it refuses only what code could not apply
or what the doctrine forbids outright (unknown IDs; drops past half the spoken words; a ``repeat`` drop with no
later take; the payoff/CTA inside dropped words; dropping the spoken CTA; a filler cut that would remove a lexical
word; more than one punch-in; more than eight emphasis words). Anything it rejects is listed so the one retry can
fix it; after that the pure-code plan ships.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.perception.index import Cluster, TakeIndex

__all__ = [
    "CTA_LEXICON", "MAX_DROP_SHARE", "MAX_EMPHASIS_WORDS", "PLAN_SCHEMA_JSON",
    "PlanDecision", "PunchIn", "Reason", "SentenceDrop", "StructureMarker", "TakeChoice", "TextFix", "WordRange",
    "is_cta_sentence", "kept_word_ids", "validate_decision",
]

Reason = Literal["preamble", "tangent", "after_payoff", "unfinished", "repeat", "repeats_hook"]

#: Drops may remove at most this share of the take's spoken words (the planner trims, it does not rewrite).
MAX_DROP_SHARE = 0.5
#: Caption emphasis words per edit (the doctrine's "a few accents, never a highlighter").
MAX_EMPHASIS_WORDS = 8

#: Spoken call-to-action markers (lower-cased sentence text, punctuation stripped). A sentence matching one is the
#: creator's CTA: the doctrine protects it ("never invent a CTA or drop the one they say"), so it is dropped only
#: as a ``repeat`` (a retake of the same line exists).
CTA_LEXICON: tuple[str, ...] = (
    r"\bfollow\b", r"\bsubscribe\b", r"\bcomment\b", r"\blike and share\b", r"\bshare (this|it|with)\b",
    r"\blink\b", r"\bin (my|the) bio\b", r"\bpart (two|2|ii)\b", r"\bnext (video|part|episode|week)\b",
    r"\bsave this\b", r"\bdm me\b", r"\bcheck out\b", r"\bsign up\b", r"\bturn on (post )?notifications\b",
    r"\btag (a|some|your)\b", r"\bdownload\b", r"\bpre-?order\b", r"\bjoin (the|my|our)\b",
    r"\bgrab (the|my|your|a)\b", r"\bbook a\b",
)
_CTA_RE = re.compile("|".join(CTA_LEXICON))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WordRange(_Model):
    from_word: str
    to_word: str


class SentenceDrop(_Model):
    sentence_id: str
    reason: Reason


class TakeChoice(_Model):
    cluster_id: str
    sentence_id: str


class PunchIn(_Model):
    anchor_word: str
    reason: str


class TextFix(_Model):
    """Caption display text for one word (never the audio)."""

    word_id: str
    text: str


class StructureMarker(_Model):
    """A short on-screen label over a word range: ``"1/3"``, ``"Step 2"``."""

    anchor_from_word: str
    anchor_to_word: str
    label: str


class PlanDecision(_Model):
    style_primary: str
    style_secondary: str | None = None
    goal: str  # <= 25 words (stated in the planner prompt; not a rejection)
    hook_promise: str
    drop_sentences: list[SentenceDrop] = Field(default_factory=list)
    choose_take: list[TakeChoice] = Field(default_factory=list)
    payoff: WordRange | None = None
    cta: WordRange | None = None
    must_keep: list[WordRange] = Field(default_factory=list)
    deliberate_beat_gap_ids: list[str] = Field(default_factory=list)  # pauses kept at their measured length
    cut_filler_word_ids: list[str] = Field(default_factory=list)
    punch_in: PunchIn | None = None  # at most one; applied only when the source's clean scale allows >= 1.15x
    caption_emphasis_word_ids: list[str] = Field(default_factory=list)  # <= 8, at most one per page
    caption_text_fixes: list[TextFix] = Field(default_factory=list)
    structure_markers: list[StructureMarker] = Field(default_factory=list)
    notes: str = ""


def _compact(node: Any) -> Any:
    """Strip titles and descriptions (the prompt explains the fields); keep enums, types and required lists."""
    if isinstance(node, dict):
        return {k: _compact(v) for k, v in node.items() if k not in ("title", "description")}
    if isinstance(node, list):
        return [_compact(x) for x in node]
    return node


#: The schema the planner prompt carries in its cached prefix (compact JSON, about 2.8k chars).
PLAN_SCHEMA_JSON: str = json.dumps(_compact(PlanDecision.model_json_schema()), separators=(",", ":"))


# ============================================================================================ helpers
def is_cta_sentence(text: str) -> bool:
    """True when the sentence text carries a spoken call to action (see :data:`CTA_LEXICON`)."""
    plain = re.sub(r"[^\w\s'-]", " ", text.lower())
    return _CTA_RE.search(" ".join(plain.split())) is not None


def _cluster_choice(c: Cluster, index: TakeIndex) -> str:
    if c.recommended_sentence_id:
        return c.recommended_sentence_id
    smap = index.sentence_map
    complete = [sid for sid in c.sentence_ids if sid in smap and smap[sid].complete]
    return (complete or c.sentence_ids)[-1]


def _dropped_sentence_ids(d: PlanDecision, index: TakeIndex) -> set[str]:
    """Sentences that leave the story: the decision's drops plus every take a cluster does not keep (the decision's
    choice when it names a valid one, else the index's recommendation)."""
    smap, cmap = index.sentence_map, index.cluster_map
    dropped = {x.sentence_id for x in d.drop_sentences if x.sentence_id in smap}
    choice = {c.id: _cluster_choice(c, index) for c in index.clusters}
    for tc in d.choose_take:
        c = cmap.get(tc.cluster_id)
        if c is not None and tc.sentence_id in c.sentence_ids:
            choice[c.id] = tc.sentence_id
    for c in index.clusters:
        dropped.update(sid for sid in c.sentence_ids if sid != choice[c.id])
    return dropped


def kept_word_ids(d: PlanDecision, index: TakeIndex) -> list[str]:
    """Source-order word IDs the story keeps under ``d``: everything outside dropped sentences and outside the takes
    a cluster does not keep. Words without a sentence are kept (the code plan decides on them)."""
    dropped = _dropped_sentence_ids(d, index)
    return [w.id for w in index.words if not (w.sentence_id and w.sentence_id in dropped)]


# ============================================================================================ validation
def validate_decision(d: PlanDecision, index: TakeIndex) -> list[str]:
    """The rejections (empty = valid), each a sentence the retry prompt can carry back to the model."""
    ix = index
    out: list[str] = []
    smap, cmap, wmap, gmap = ix.sentence_map, ix.cluster_map, ix.word_map, ix.gap_map

    def has_word(field: str, wid: str) -> bool:
        if wid in wmap:
            return True
        out.append(f"{field}: {wid} is not a word of this take")
        return False

    def check_range(field: str, r: WordRange) -> bool:
        ok = has_word(field, r.from_word) & has_word(field, r.to_word)  # `&`: report both ends when both are wrong
        if ok and ix.word_pos(r.from_word) > ix.word_pos(r.to_word):
            out.append(f"{field}: range {r.from_word}-{r.to_word} is reversed ({r.to_word} comes first)")
            return False
        return ok

    # --- every ID exists (and belongs where it is named)
    for drop in d.drop_sentences:
        if drop.sentence_id not in smap:
            out.append(f"drop_sentences: {drop.sentence_id} is not a sentence of this take")
    for tc in d.choose_take:
        c = cmap.get(tc.cluster_id)
        if c is None:
            out.append(f"choose_take: {tc.cluster_id} is not a retake cluster of this take")
        elif tc.sentence_id not in c.sentence_ids:
            out.append(f"choose_take: {tc.sentence_id} is not a take of {tc.cluster_id} "
                       f"(takes: {', '.join(c.sentence_ids)})")
        elif any(x.sentence_id == tc.sentence_id for x in d.drop_sentences):
            out.append(f"choose_take: {tc.sentence_id} is chosen for {tc.cluster_id} and also dropped")
    ranges: list[tuple[str, WordRange]] = [("must_keep", r) for r in d.must_keep]
    if d.payoff is not None:
        ranges.append(("payoff", d.payoff))
    if d.cta is not None:
        ranges.append(("cta", d.cta))
    valid_ranges = [(name, r) for name, r in ranges if check_range(name, r)]
    for gid in d.deliberate_beat_gap_ids:
        if gid not in gmap:
            out.append(f"deliberate_beat_gap_ids: {gid} is not a gap of this take")
    for wid in d.cut_filler_word_ids:
        if has_word("cut_filler_word_ids", wid) and wmap[wid].kind not in ("filler", "event"):
            out.append(f"cut_filler_word_ids: {wid} \"{wmap[wid].text}\" is a spoken word, not a filler "
                       f"(fillers are marked {{um}} in the digest)")
    if d.punch_in is not None:
        has_word("punch_in", d.punch_in.anchor_word)
    for wid in d.caption_emphasis_word_ids:
        has_word("caption_emphasis_word_ids", wid)
    if len(d.caption_emphasis_word_ids) > MAX_EMPHASIS_WORDS:
        out.append(f"caption_emphasis_word_ids: {len(d.caption_emphasis_word_ids)} words, at most "
                   f"{MAX_EMPHASIS_WORDS} (a few accents, not a highlighter)")
    for fix in d.caption_text_fixes:
        has_word("caption_text_fixes", fix.word_id)
    for mk in d.structure_markers:
        check_range("structure_markers", WordRange(from_word=mk.anchor_from_word, to_word=mk.anchor_to_word))

    # --- drops never remove more than half of the spoken words
    spoken = [w for w in ix.words if w.is_spoken]
    explicit = {x.sentence_id for x in d.drop_sentences if x.sentence_id in smap}
    removed = sum(1 for w in spoken if w.sentence_id in explicit)
    if spoken and removed > MAX_DROP_SHARE * len(spoken):
        out.append(f"drop_sentences: {len(explicit)} drop(s) remove {removed} of {len(spoken)} spoken words "
                   f"({removed / len(spoken):.0%}); the limit is {MAX_DROP_SHARE:.0%}. Drop less, or let the "
                   "code plan trim.")

    # --- a repeat needs a kept re-delivery of the same line in its cluster (normally the later, recommended take;
    #     with choose_take, whichever take the decision keeps); repeats_hook needs the hook it repeats
    dropped = _dropped_sentence_ids(d, ix)
    order = {s.id: i for i, s in enumerate(ix.sentences)}
    hook_stated = bool(d.hook_promise.strip())
    for drop in d.drop_sentences:
        s = smap.get(drop.sentence_id)
        if s is None or drop.reason not in ("repeat", "repeats_hook"):
            continue
        c = cmap.get(s.cluster_id) if s.cluster_id else None
        redelivered = c is not None and any(sid != s.id and sid not in dropped for sid in c.sentence_ids)
        if drop.reason == "repeat" and not redelivered:
            out.append(f"drop_sentences: {s.id} reason repeat needs another kept take of the same line in its "
                       "retake cluster" + (f" ({c.id})" if c is not None else " (it is in no cluster)")
                       + "; use preamble/tangent/after_payoff/unfinished, or repeats_hook with the hook stated")
        elif drop.reason == "repeats_hook":
            earlier_kept = any(order[sid] < order[s.id] and sid not in dropped for sid in order)
            if not (redelivered or (hook_stated and earlier_kept)):
                out.append(f"drop_sentences: {s.id} reason repeats_hook needs hook_promise stated and an earlier "
                           "kept sentence that carries the hook")

    # --- the payoff, CTA and must-keep ranges lie inside kept words
    kept = set(kept_word_ids(d, ix))
    for name, r in valid_ranges:
        lost = [w for w in ix.word_ids(r.from_word, r.to_word) if w not in kept]
        if lost:
            where = sorted({wmap[w].sentence_id or "no sentence" for w in lost})
            out.append(f"{name}: {r.from_word}-{r.to_word} is not inside the kept words ({lost[0]}"
                       + (f" and {len(lost) - 1} more" if len(lost) > 1 else "") + f" in dropped {', '.join(where)})")

    # --- the spoken CTA is never dropped, unless a retake of it exists (reason repeat)
    for drop in d.drop_sentences:
        s = smap.get(drop.sentence_id)
        if s is not None and drop.reason != "repeat" and is_cta_sentence(s.text):
            out.append(f"drop_sentences: {s.id} is the creator's spoken call to action (\"{s.text.strip()}\"); it "
                       "is never dropped unless the reason is repeat")
    return out
