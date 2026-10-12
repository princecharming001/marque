"""The planner's answer: :class:`PlanDecision`, its compact schema string and the guards that reject an unusable one
(standard-tier plan §2 step 5, ``docs/standard-tier-interfaces.md`` "Decision").

The decision names **IDs only** (sentences, clusters, words, gaps from the Take Index), never times: the compiler
keeps invariant 3 ("no model timestamps") by construction. Every field is something the Director actually decided
on the real jobs (the ``Part one:`` label cut, the payoff pin, one 1.15x lean-in, caption emphasis, take40's ``1/3``
labels, the "restates the hook" drop); everything else is code. A label said in the same sentence as the claim is a
word-level ``trim_words`` range, not a sentence drop: the ASR often merges "Part one: Everyone says..." into one
sentence, and dropping it opened d030 on "They're wrong." with its setup gone.

Why the models carry no ``pattern``/``max_length`` constraints: the planner call sends this schema as a native
structured output, and Anthropic's grammar compiler rejects or ignores string/array constraints (HANDOFF §6:
"compiled grammar too large" on strict tools). The limits live in :func:`validate_decision` instead, whose
rejections are plain text the retry carries back to the model.

:func:`validate_decision` is the planner's safety net, not its taste: it refuses only what code could not apply
or what the doctrine forbids outright (unknown IDs; drops and trims past half the spoken words; a ``repeat`` drop
with no later take; the payoff/CTA inside removed words; dropping the spoken CTA; dropping the sentence that carries
the hook while no kept sentence does; a trim that is not at a sentence edge, ends mid-clause or leaves under two
words; removing a spoken list number while another stays; numbered labels with a gap; a filler cut that would remove
a lexical word; more than one punch-in; more than eight emphasis words).
Anything it rejects is listed so the one retry can fix it; after that the pure-code plan ships.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.perception.index import Cluster, TakeIndex

__all__ = [
    "CTA_LEXICON", "HOOK_MIN_OVERLAP", "MAX_DROP_SHARE", "MAX_EMPHASIS_WORDS", "PLAN_SCHEMA_JSON",
    "TRIM_SEAM_MIN_PAUSE_MS", "PlanDecision", "PunchIn", "Reason", "SentenceDrop", "StructureMarker", "TakeChoice",
    "TextFix", "TrimReason", "WordRange", "WordTrim", "hook_sentence_id", "is_cta_sentence", "kept_word_ids",
    "trim_problem", "validate_decision",
]

Reason = Literal["preamble", "tangent", "after_payoff", "unfinished", "repeat", "repeats_hook"]
TrimReason = Literal["label", "preamble", "tangent", "after_payoff"]

#: A trim's seam needs clause punctuation or at least this much silence (the articulatory maximum in
#: ``skills/editing/constants.yaml``): a cut inside running speech is a mid-clause seam the listener hears.
TRIM_SEAM_MIN_PAUSE_MS = 200.0
#: A sentence "carries the hook" when it holds at least this share of the hook promise's content words (and at
#: least two of them). The sentence that carries it best is never dropped unless a kept sentence also carries it.
HOOK_MIN_OVERLAP = 0.5
#: A kept sentence also carries the hook when it scores at least this share of the best match (a restated hook).
_HOOK_ALSO_CARRIED = 0.6

#: Drops may remove at most this share of the take's spoken words (the planner trims, it does not rewrite).
MAX_DROP_SHARE = 0.5
#: Caption emphasis words per edit (the doctrine's "a few accents, never a highlighter").
MAX_EMPHASIS_WORDS = 8

#: Spoken call-to-action markers (lower-cased sentence text, punctuation stripped). A sentence matching one is the
#: creator's CTA: the doctrine protects it ("never invent a CTA or drop the one they say"), so it is dropped only
#: as a ``repeat`` (a retake of the same line exists). The patterns are the imperative forms: a bare "follow",
#: "link", "download", "check out" or "next week" is ordinary content ("if you follow this rule", "a link between
#: sleep and mood", "next week I tried it again") and must not be pinned or protected as a CTA.
CTA_LEXICON: tuple[str, ...] = (
    r"\bfollow (me|us|for|along)\b", r"\bsubscribe\b", r"\bcomments?\b", r"\blet me know\b",
    r"\blike and share\b", r"\bshare (this|it|with)\b", r"\blink (in|below)\b", r"\bin (my|the) bio\b",
    r"\bpart (two|2|ii)\b", r"\bnext (video|part|episode)\b", r"\bsave (this|it)\b", r"\bsend this\b",
    r"\bdm me\b", r"\bcheck out (my|our|the link)\b", r"\bsign up\b", r"\bturn on (post )?notifications\b",
    r"\btag (a|some|your)\b", r"\bdownload (my|our|the|this|it)\b", r"\bpre-?order\b", r"\bjoin (the|my|our)\b",
    r"\bgrab (a|my|your|the) (free|copy|guide|course|spot|seat|tickets?|template|discount|link|freebie)\b",
    r"\bbook (a|your) (call|session|consult\w*|demo|spot|appointment)\b", r"\brate (this|that|it)\b",
)
_CTA_RE = re.compile("|".join(CTA_LEXICON))


def _every_field_required(schema: dict[str, Any]) -> None:
    """List every property as required in the JSON schema (the Python defaults still apply when validating).

    Anthropic's structured-output grammar rejects this schema as "too complex" while 13 of its fields are optional
    (measured 2026-10-12: the same schema with every property required is accepted). The model answers every
    field: an empty list or ``null`` means "nothing to decide"."""
    props = schema.get("properties")
    if isinstance(props, dict):
        schema["required"] = list(props)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_every_field_required)


class WordRange(_Model):
    from_word: str
    to_word: str


class SentenceDrop(_Model):
    sentence_id: str
    reason: Reason


class WordTrim(_Model):
    """Words cut from one edge of a sentence that otherwise stays: a series label or preamble said in the same
    sentence as the claim ("Part one: Everyone says..." trims "Part one:"), or a trailing aside after the point.
    The seam is at clause punctuation or a pause, and at least two spoken words of the sentence stay."""

    from_word: str
    to_word: str
    reason: TrimReason


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
    trim_words: list[WordTrim] = Field(default_factory=list)  # a label/aside at one edge of a kept sentence
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
    """Source-order word IDs the story keeps under ``d``: everything outside dropped sentences, outside the takes
    a cluster does not keep and outside the valid trims. Words without a sentence are kept (the code plan decides
    on them)."""
    dropped = _dropped_sentence_ids(d, index)
    trimmed = _trimmed_word_ids(d, index)
    return [w.id for w in index.words if not (w.sentence_id and w.sentence_id in dropped) and w.id not in trimmed]


# ---------------------------------------------------------------------------------------------- trims
_CLAUSE_END = re.compile(r"[,;:.!?…—–]+[\"'”’)\]]*$")


def _seam_pause_ms(index: TakeIndex, a_id: str, b_id: str) -> float:
    """Silence between two source-adjacent words; a recording dropout is not a pause the speaker made."""
    a, b = index.word(a_id), index.word(b_id)
    g = index.gap_after(a.id)
    if g is not None and g.before_word_id == b.id:
        return 0.0 if g.is_dropout else g.duration_ms
    return max(0.0, (b.start_us - a.end_us) / 1000.0)


def _clause_seam(index: TakeIndex, a_id: str, b_id: str) -> bool:
    """A clean seam between ``a`` and the next word ``b``: clause punctuation on ``a`` or a real pause."""
    return bool(_CLAUSE_END.search(index.word(a_id).text.strip())) or \
        _seam_pause_ms(index, a_id, b_id) >= TRIM_SEAM_MIN_PAUSE_MS


def trim_problem(t: WordTrim, index: TakeIndex) -> str | None:
    """Why the code plan cannot apply ``t`` (a rejection line), or None when it can: both words exist in order and in
    one sentence, the range touches that sentence's first or last spoken word, the seam on the other side is at
    clause punctuation or a pause (:data:`TRIM_SEAM_MIN_PAUSE_MS`), at least two spoken words of the sentence stay,
    and a spoken call to action stays one."""
    ix = index
    rng = f"trim_words: {t.from_word}-{t.to_word}"
    missing = [w for w in (t.from_word, t.to_word) if not ix.has_word(w)]
    if missing:
        return f"trim_words: {', '.join(missing)} is not a word of this take"
    if ix.word_pos(t.from_word) > ix.word_pos(t.to_word):
        return f"{rng} is reversed ({t.to_word} comes first)"
    sid = ix.word(t.from_word).sentence_id
    if not sid or sid not in ix.sentence_map or ix.word(t.to_word).sentence_id != sid:
        return f"{rng} must lie inside one sentence (use drop_sentences to remove whole sentences)"
    s = ix.sentence_map[sid]
    cut = set(ix.word_ids(t.from_word, t.to_word))
    order = list(s.word_ids)
    first, last = order.index(t.from_word), order.index(t.to_word)
    lexical = [i for i, w in enumerate(order) if ix.word(w).kind == "word"]
    head = not any(i < first for i in lexical)
    tail = not any(i > last for i in lexical)
    left = [w for w in order if w not in cut and ix.word(w).kind == "word"]
    if len(left) < 2:
        return f"{rng} leaves fewer than two spoken words of {sid}; drop the sentence instead, or trim less"
    if not (head or tail):
        return (f"{rng} is in the middle of {sid}; a trim cuts a label or aside at the start or the end of a "
                "sentence")
    a, b = (order[last], order[last + 1]) if head else (order[first - 1], order[first])
    if not _clause_seam(ix, a, b):
        return (f"{rng} ends mid-clause: no punctuation or pause between {a} \"{ix.word(a).text.strip()}\" and "
                f"{b} \"{ix.word(b).text.strip()}\"; trim to a comma, colon, full stop or pause, or keep the words")
    if is_cta_sentence(s.text) and not is_cta_sentence(" ".join(ix.word(w).text for w in order if w not in cut)):
        return f"{rng} cuts the spoken call to action out of {sid} (\"{s.text.strip()}\"); the CTA is never trimmed"
    return None


def _trimmed_word_ids(d: PlanDecision, index: TakeIndex) -> set[str]:
    out: set[str] = set()
    for t in d.trim_words:
        if trim_problem(t, index) is None:
            out.update(index.word_ids(t.from_word, t.to_word))
    return out


# ---------------------------------------------------------------------------------------------- the hook
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = (
    "a an the and but or so if then than you your yours i im me my we our us it its is are was were be been am to "
    "of in on at for with that this these those there they them their he she his her as by from not no do does did "
    "just really very what which who how about all can will would could should have has had get got")
_STOP = frozenset(_STOP_WORDS.split())


def _content_tokens(text: str) -> set[str]:
    """Lower-cased content words of ``text`` with a light plural fold ("stories" ~ "story", "says" ~ "say")."""
    out: set[str] = set()
    for tok in _TOKEN.findall(text.lower().replace("'", "").replace("’", "")):
        if tok in _STOP or (len(tok) < 3 and not tok.isdigit()):
            continue
        if tok.endswith("ies") and len(tok) > 4:
            tok = tok[:-3] + "y"
        elif tok.endswith("s") and not tok.endswith("ss") and len(tok) > 3:
            tok = tok[:-1]
        out.add(tok)
    return out


def _hook_scores(hook_promise: str, index: TakeIndex) -> dict[str, tuple[float, int]]:
    """Per sentence: the share of the hook promise's content words it holds, and how many."""
    hook = _content_tokens(hook_promise)
    if not hook:
        return {}
    out: dict[str, tuple[float, int]] = {}
    for s in index.sentences:
        n = len(hook & _content_tokens(s.text))
        out[s.id] = (n / len(hook), n)
    return out


def hook_sentence_id(hook_promise: str, index: TakeIndex) -> str | None:
    """The sentence that carries the hook promise best: the largest share of its content words, at least
    :data:`HOOK_MIN_OVERLAP` and two words (the earliest wins a tie). None when no sentence clears that bar."""
    scores = _hook_scores(hook_promise, index)
    best: str | None = None
    for s in index.sentences:
        share, n = scores.get(s.id, (0.0, 0))
        if share >= HOOK_MIN_OVERLAP and n >= 2 and (best is None or share > scores[best][0]):
            best = s.id
    return best


def _label_before_hook(hook_promise: str, index: TakeIndex, sid: str) -> tuple[str, str] | None:
    """A trim the retry can use instead of dropping the hook sentence: the words before the first hook word, when
    they end at a clean seam and :func:`trim_problem` accepts them."""
    hook = _content_tokens(hook_promise)
    order = index.sentence_map[sid].word_ids
    k = next((i for i, w in enumerate(order) if _content_tokens(index.word(w).text) & hook), None)
    if not k:
        return None
    t = WordTrim(from_word=order[0], to_word=order[k - 1], reason="label")
    return (t.from_word, t.to_word) if trim_problem(t, index) is None else None


# ---------------------------------------------------------------------------------------------- spoken lists
_ORDINALS = {
    "one": 1, "first": 1, "firstly": 1, "two": 2, "second": 2, "secondly": 2, "three": 3, "third": 3, "thirdly": 3,
    "four": 4, "fourth": 4, "five": 5, "fifth": 5, "six": 6, "sixth": 6, "seven": 7, "seventh": 7, "eight": 8,
    "eighth": 8, "nine": 9, "ninth": 9, "ten": 10, "tenth": 10,
}
#: Nouns that make "<noun> <number>" a list item ("Step two", "Number one", "Tip 3"); not "part" (a series label).
_ITEM_NOUNS = frozenset({"number", "step", "tip", "rule", "reason", "mistake", "lesson", "way", "question", "point"})
_ITEM_PUNCT = re.compile(r"[,;:.)!?—–]+[\"'”’]*$")


def _list_number(index: TakeIndex, sid: str) -> tuple[int, list[str]] | None:
    """The list position a sentence opens with and the words that say it: an ordinal or number followed by
    punctuation ("One," / "Second:" / "3.") or after an item noun ("Step two", "Number one"). Plain content that
    starts with a number ("One of my favourites", "Two weeks later") is not a list item."""
    lex = [w for w in index.sentence_map[sid].word_ids if index.word(w).kind == "word"][:2]
    if not lex:
        return None
    toks = [re.sub(r"[^\w]", "", index.word(w).text.lower()) for w in lex]

    def value(tok: str) -> int | None:
        return int(tok) if tok.isdigit() and 0 < int(tok) <= 20 else _ORDINALS.get(tok)

    first = value(toks[0])
    if first is not None and _ITEM_PUNCT.search(index.word(lex[0]).text.strip()):
        return first, lex[:1]
    if len(toks) == 2 and toks[0] in _ITEM_NOUNS and (second := value(toks[1])) is not None:
        return second, lex
    return None


def _list_breaks(d: PlanDecision, index: TakeIndex, kept: set[str]) -> list[str]:
    """Rejections for a spoken list item number the decision removes while another number of the list stays ("Two,"
    and "Three," kept, "One," dropped; or "Three," dropped after "One," and "Two,"): the spoken count breaks, and a
    label cannot fix what the listener hears. Only in a real list (at least two numbered items)."""
    items = [(s.id, n) for s in index.sentences if (n := _list_number(index, s.id)) is not None]
    if len({v for _sid, (v, _w) in items}) < 2:
        return []
    kept_values = {v for _sid, (v, words) in items if all(w in kept for w in words)}
    explicit = {x.sentence_id for x in d.drop_sentences}
    trimmed = _trimmed_word_ids(d, index)
    out: list[str] = []
    for sid, (v, words) in items:
        if all(w in kept for w in words) or v in kept_values:
            continue
        if sid in explicit:
            field = "drop_sentences"
        elif any(w in trimmed for w in words):
            field = "trim_words"
        else:  # a take the retake cluster does not keep: the index's call, not this decision's
            continue
        others = [(s2, w2) for s2, (v2, w2) in items if v2 != v and all(w in kept for w in w2)]
        if not others:
            continue
        said = " ".join(index.word(w).text.strip() for w in words)
        other = " ".join(index.word(w).text.strip() for w in others[0][1])
        out.append(f"{field}: {sid} opens list item {v} (\"{said}\") and {others[0][0]} (\"{other}\") is kept, so the "
                   f"spoken count breaks; keep \"{said}\" with its item (a list keeps every number or none)")
    return out


def _marker_gaps(d: PlanDecision) -> list[str]:
    """Rejections for numbered structure labels with a gap ("2/3" and "3/3" without "1/3")."""
    fractions: dict[int, set[int]] = {}
    steps: dict[str, set[int]] = {}
    for mk in d.structure_markers:
        label = mk.label.strip().lower()
        if m := re.fullmatch(r"(\d+)\s*/\s*(\d+)", label):
            fractions.setdefault(int(m.group(2)), set()).add(int(m.group(1)))
        elif m := re.fullmatch(r"(step|tip|rule|#|no\.?|number)\s*(\d+)", label):
            steps.setdefault(m.group(1), set()).add(int(m.group(2)))
    out: list[str] = []
    for total, seen in fractions.items():
        missing = [f"{i}/{total}" for i in range(1, total + 1) if i not in seen]
        if missing:
            out.append(f"structure_markers: the {total}-item labels skip {', '.join(missing)}; label every item "
                       "(on the words that open it) or none")
    for noun, seen in steps.items():
        missing = [f"{noun} {i}" for i in range(1, max(seen) + 1) if i not in seen]
        if missing:
            out.append(f"structure_markers: the labels skip {', '.join(missing)}; label every item or none")
    return out


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

    # --- trims: a label or aside at one edge of a sentence the story keeps, cut at a clean seam
    dropped_now = _dropped_sentence_ids(d, ix)
    trimmed: set[str] = set()
    for t in d.trim_words:
        problem = trim_problem(t, ix)
        if problem:
            out.append(problem)
            continue
        sid = wmap[t.from_word].sentence_id
        ids = set(ix.word_ids(t.from_word, t.to_word))
        if sid in dropped_now:
            out.append(f"trim_words: {t.from_word}-{t.to_word} is inside {sid}, which is dropped; trim a sentence "
                       "the story keeps, or drop it and not both")
        elif ids & trimmed:
            out.append(f"trim_words: {t.from_word}-{t.to_word} overlaps another trim")
        trimmed |= ids

    # --- drops (and trims) never remove more than half of the spoken words
    spoken = [w for w in ix.words if w.is_spoken]
    explicit = {x.sentence_id for x in d.drop_sentences if x.sentence_id in smap}
    removed = sum(1 for w in spoken if w.sentence_id in explicit or w.id in trimmed)
    if spoken and removed > MAX_DROP_SHARE * len(spoken):
        out.append(f"drop_sentences: {len(explicit)} drop(s) and {len(d.trim_words)} trim(s) remove {removed} of "
                   f"{len(spoken)} spoken words ({removed / len(spoken):.0%}); the limit is {MAX_DROP_SHARE:.0%}. "
                   "Drop less, or let the code plan trim.")

    # --- the sentence that carries the hook stays (the opening claim), unless a kept sentence carries it too; a
    #     label said before the claim in the same sentence ("Part one: Everyone says...") is a trim, not a drop
    best = hook_sentence_id(d.hook_promise, ix)
    if best is not None:
        scores = _hook_scores(d.hook_promise, ix)
        bar = scores[best][0] * _HOOK_ALSO_CARRIED
        carried = any(sid not in dropped_now and share >= bar and n >= 2 for sid, (share, n) in scores.items())
        for drop in d.drop_sentences:
            if drop.sentence_id != best or drop.reason == "repeat" or carried:
                continue
            msg = (f"drop_sentences: {best} carries the hook (\"{smap[best].text.strip()}\"); dropping it loses the "
                   "opening claim. Keep it")
            label = _label_before_hook(d.hook_promise, ix, best)
            if label is not None:
                words = " ".join(wmap[w].text.strip() for w in ix.word_ids(*label))
                msg += f", and trim only the words before the claim with trim_words {label[0]}-{label[1]} \"{words}\""
            out.append(msg + ".")

    # --- a repeat needs a kept re-delivery of the same line in its cluster (normally the later, recommended take;
    #     with choose_take, whichever take the decision keeps); repeats_hook needs the hook it repeats
    dropped = dropped_now
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

    # --- a spoken list keeps its count ("One," stays while "Two," and "Three," do), and numbered labels have no gap
    out.extend(_list_breaks(d, ix, kept))
    out.extend(_marker_gaps(d))
    return out
