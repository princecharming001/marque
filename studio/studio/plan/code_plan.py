"""The standard tier's code plan: the ordered op list that turns a Take Index (and the planner's decision, when there
is one) into a complete CutDocument without a Director (standard-tier plan §2 step 4 and §3 step 3; the binding
signatures are in ``docs/standard-tier-interfaces.md`` "Code plan").

Why code and not a model: on the real jobs the Director's own decisions were a handful of sentence drops, pins, one
lean-in and some caption accents; everything else it emitted drove deterministic tools this package already has
(recommended takes, gap snap points, the caption pager and placer, the measured voice chain). The planner's
:class:`~studio.plan.decision.PlanDecision` carries that handful as IDs; this module does the rest from measurements
and the doctrine priors in ``constants.yaml``, and ships the same document shape the Director ships, so ``studio chat``,
``studio qa`` and a Pro upgrade resume from it.

What :func:`code_plan` decides, in op order (every op is one of the existing :mod:`studio.doc.ops` schemas and is
applied with :func:`studio.doc.ops.apply_ops`; IDs only, never times):

* ``set_brief`` / ``set_style`` / ``set_deliverables`` from the decision, else from the measured energy band; the
  brief's ``cta`` is the creator's spoken call to action when one is kept (never an invented one).
* ``set_story``: one take per retake cluster (the decision's ``choose_take``, else the index's recommendation),
  abandoned sentences dropped, the decision's drops (never the spoken CTA except as a ``repeat``, never past half of
  the spoken words) and trims (a label or aside at one edge of a kept sentence, only where
  :func:`~studio.plan.decision.trim_problem` accepts it), and the cut-off words handled. A fragment the speaker
  restarts ("we need to b- build") goes alone when there is a closure on both sides of it, and stays where it is when
  there is none (the speaker's own stumble inside continuous speech, never at a join; it goes after all if another
  removal would leave it at one); a word a recording dropout chops, or a fragment with no restart, takes its clause
  with it: back to the nearest clause edge inside the sentence (punctuation, or a pause of at least the articulatory
  maximum; a dropout is never an edge; the sentence start counts only when no lexical word precedes the cut-off) and
  forward to the next one, else the sentence is dropped, except the spoken CTA and the decision's payoff/CTA/must-keep
  sentences, which lose only the chopped word (a mid-clause seam). Seams are plain cuts (the document default).
* ``cut_words`` for fillers: the decision's, every filler in the first kept sentence, sentence-initial fillers and
  fillers bounded by pauses of at least ``fillers.cut_if_bounded_by_gap_ms``; a filler without a closure of
  ``fillers.coarticulated_cut_min_closure_ms`` on both sides is left (the seam would land inside speech).
* ``set_gap`` for pauses longer than the doctrine allows for the speaker's energy band, never on the decision's
  ``deliberate_beat_gap_ids``: a pause at a sentence or clause boundary above both the deliberate-beat window and
  twice the median comes down to the window's top; a stall (past both twice the median and
  ``pauses.dead_air_cut_over_s``) comes down to the speaker's boundary pause (the median inside the band's
  boundary range and 0.7-1.3x the median); a mid-clause hesitation from ``pauses.hesitation_tighten_from_s`` comes
  down to the hesitation target, never below the median; other dead air over the threshold is tightened whatever
  the median says. A recording dropout between kept words is dead air whatever its length (the recording's
  silence, not the speaker's): it comes down to its class's target.
* ``pin`` the decision's payoff/CTA/must-keep words (kept lexical words only) and, always, a spoken CTA sentence.
* ``set_framing`` for the decision's single punch-in at the doctrine's lean-in scale, only when the source's clean
  scale allows it and the anchor is past the hook's first seconds; it holds to the end of the anchor's sentence (at
  least ``framing.punch_hold_min_s``) and returns to base there or at the next seam, never mid-sentence.
* ``add_text`` labels for the decision's structure markers (before the captions: the caption placer steers around
  on-screen text, so the size check below must see them); a numbered set holds each label for its whole item, short
  labels on a dark pill beside the face.
* ``set_captions`` at one uniform size, the largest in :data:`CAPTION_SIZE_LADDER` at which every page renders on one
  line at full size, the placer's ``auto`` ladder shrinks no page, and the paging needs no more one-word pages,
  phrases broken mid-unit or pages on screen under ``min_page_s`` than at the floor size (bigger text only while it
  costs nothing in phrasing or timing, so fast talkers land near the floor). At each size the pager re-pages for that
  size with its floor at that size; a page the renderer would still shrink or wrap is re-paged with the pager's own
  dynamic program into one-line pages, accepted only when every part stays on screen at least ``min_page_s``
  (``punch_page_s`` for a one-word punch page); otherwise that size has no paging. When no size has one, the
  pager's own pages at the floor ship (a long page wraps: the doctrine prefers two lines to flicker).
  Then ``edit_caption_page`` for the decision's emphasis words (they replace the automatic accents; one per page, at
  most ``accent_page_share`` of the pages by emphasis, never on the punch-in anchor or a label) and display-text
  fixes that still fit the line.
* ``set_voice_chain`` from :func:`studio.audio.voice.plan_voice_chain` (the job's ``audio.wav`` when it exists, else
  the index's own measurements), with paid isolation only when music plays in the room and the source is at most
  :data:`PAID_ISOLATION_MAX_S`; otherwise the local light denoise with a "needs isolation add-on" note.
* ``set_color`` only when :func:`studio.compile.color.measure_color` on the job's mezzanine (skipped without one)
  reports a fault no skin tone explains (face luma under the darkest band's floor or over the lit-skin ceiling).
  Music, SFX and b-roll never.

:func:`plan_document` applies the ops through :class:`studio.agent.tools.EditSession`, so the versions and the oplog
are written exactly as the Director writes them, and :func:`radio_check` is the listener's gate
(:func:`studio.doc.validate.radio_blockers`) that the pipeline runs before compiling.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from studio.doc.model import (
    Brief,
    CaptionPage,
    CaptionPlan,
    CaptionStyle,
    CutDocument,
    Point,
    Style,
    StyleDials,
    TextStyle,
    new_document,
)
from studio.doc.ops import apply_ops
from studio.doc.validate import radio_blockers
from studio.plan.decision import MAX_DROP_SHARE, PlanDecision, is_cta_sentence, trim_problem

if TYPE_CHECKING:  # pragma: no cover
    from studio.compile.captions import CaptionParams
    from studio.compile.models import Timeline
    from studio.config import Settings
    from studio.jobs import Job
    from studio.perception.index import Cluster, Gap, Sentence, TakeIndex, Word

__all__ = [
    "CAPTION_SIZE_LADDER", "ENERGY_BANDS", "ISOLATION_BUDGET_USD", "PAID_ISOLATION_MAX_S", "PLAN_BY",
    "UNMETERED_ISOLATION_MAX_S",
    "CodePlanError", "RadioBlocked", "code_plan", "energy_band", "plan_document", "radio_check", "story_speech_s",
]

#: ``created_by`` / oplog author of a code-planned document.
PLAN_BY = "code_plan"
#: Caption sizes tried, largest first (px at 1080 wide): the doctrine's phrase range capped at the house default.
CAPTION_SIZE_LADDER: tuple[int, ...] = tuple(range(88, 63, -2))
#: Paid voice isolation is bought only for sources up to this long (standard-tier plan §2: $0.12/min stays inside
#: the tier's API budget only on short takes); longer ones get the local light denoise.
PAID_ISOLATION_MAX_S = 60.0
#: ...and only while the job's API spend so far (the planner call and its retry, the index ASR, already in the trace)
#: plus the isolation itself plus a reserve for the QA round trips stays at or under this (plan §2: "the running API
#: subtotal stays <= $0.17"). A 45-60 s music-in-room take after a planner retry would otherwise pass $0.20.
ISOLATION_BUDGET_USD = 0.17
#: Without a job to meter the spend, the stricter length that keeps the realistic worst case near $0.185.
UNMETERED_ISOLATION_MAX_S = 30.0
#: QA renders the reserve covers (the render, one repair re-render, the pure-code second attempt).
_QA_RENDERS_RESERVED = 3
#: ``Energy.overall`` ceilings of the doctrine's energy bands (prosody.py: calm educator ≈0.2-0.35, conversational
#: founder ≈0.4-0.6, hot take / sales ≥0.7).
ENERGY_BANDS: tuple[tuple[float, str], ...] = ((0.38, "calm"), (0.65, "conversational"), (float("inf"), "high"))

_CLAUSE_PUNCT = re.compile(r"[,;:.!?…—–]+[\"'”’)\]]*$")
_SENT_PUNCT = re.compile(r"[.!?…]+[\"'”’)\]]*$")
#: A pause trim that saves less than this is not worth an override (it is inaudible and only adds an op).
_MIN_TRIM_MS = 100.0
#: Structure-marker labels up to this many characters sit beside the face (left edge, mid height, small), where
#: the Director put take40's ``1/3`` labels; longer labels take the overlay's automatic position.
_SIDE_LABEL_MAX_CHARS = 5
_SIDE_LABEL_POINT = Point(x=0.15, y=0.5)
_SIDE_LABEL_PX = 56
#: The side label's pill (the Director's take40 counters; the listicle doctrine's "counter pill"): white text on a
#: dark fill stays legible on a light wall, where bare white text vanished.
_SIDE_LABEL_PILL = "#1F1F1F"
#: A numbered item label ("1/3", "Step 2", "#4"): in a numbered set each holds until the next item opens.
_NUMBERED_LABEL = re.compile(r"(?:\d+\s*/\s*\d+|(?:step|tip|rule|#|no\.?|number)\s*\d+)", re.IGNORECASE)
#: A page break with at least this :func:`~studio.compile.captions.line_break_cost` splits a syntactic unit
#: (after a determiner, preposition, auxiliary, subject pronoun or conjunction; inside a number or a name).
_BROKEN_PHRASE_COST = 2.0

# constants.yaml priors the plan reads, with the doctrine's published values as defaults (the file wins when present)
_DEFAULTS: dict[str, Any] = {
    "pauses.articulatory_max_ms": 200.0,
    "pauses.hesitation_tighten_from_s": (0.6, 1.5),
    "pauses.hesitation_target_ms": (250.0, 400.0),
    "pauses.dead_air_cut_over_s": 1.5,
    "pauses.relative_tighten_over_x_median": 2.0,
    "pauses.deliberate_beat_by_energy_s": {"high": (0.0, 0.6), "conversational": (0.5, 0.9), "calm": (0.8, 1.5)},
    "pauses.boundary_by_energy_ms": {"high": (200.0, 300.0), "conversational": (250.0, 450.0),
                                     "calm": (350.0, 700.0)},
    "fillers.cut_if_bounded_by_gap_ms": 150.0,
    "fillers.coarticulated_cut_min_closure_ms": 20.0,
    "framing.punch_lean_in_x": (1.15, 1.25),
    "framing.punch_hold_min_s": 1.5,
    "hook.no_punch_in_first_s": (1.0, 2.0),
    "color.exposure_change_stops": 0.5,
    "color.lit_skin_soft_ceiling_pct": 80.0,
    "color.face_luma_sdr_pct": {"lighter": (45.0, 70.0), "medium": (30.0, 60.0), "deep": (15.0, 35.0)},
    "length_s.default": (30.0, 60.0),
}


class CodePlanError(RuntimeError):
    """The code plan could not produce a story (no words to keep, or the story op was rejected)."""


class RadioBlocked(RuntimeError):
    """The planned story fails the listener's gate; ``blockers`` are the ID-level fixes."""

    def __init__(self, blockers: Sequence[str]):
        super().__init__("; ".join(blockers))
        self.blockers = list(blockers)


# ============================================================================================ priors
def _constants(settings: Settings | None) -> dict[str, Any]:
    try:
        from studio.compile.captions import load_constants

        return load_constants(settings) or {}
    except Exception:  # the priors have defaults; a missing or broken file is never a reason to fail a plan
        return {}


def _num(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


def _coerce(node: Any, default: Any) -> Any:
    """``node`` from the yaml in the shape of ``default`` (a number, a ``[min, max]`` pair or a dict of pairs)."""
    if isinstance(default, tuple):
        if isinstance(node, list | tuple) and len(node) == 2 and all(_num(x) for x in node):
            return float(node[0]), float(node[1])
        return default
    if isinstance(default, dict):
        if not isinstance(node, dict):
            return default
        return {k: _coerce(node.get(k, v), v) for k, v in default.items()}
    return float(node) if _num(node) else default


def _prior(consts: dict[str, Any], key: str) -> Any:
    default = _DEFAULTS[key]
    node: Any = consts
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return _coerce(node, default)


def energy_band(overall: float) -> str:
    """``calm`` / ``conversational`` / ``high`` for a measured ``Energy.overall``."""
    for ceiling, name in ENERGY_BANDS:
        if overall < ceiling:
            return name
    return ENERGY_BANDS[-1][1]


# ============================================================================================ gaps and edges
def _silence_ms(ix: TakeIndex, a: Word, b: Word, *, dropout_is_pause: bool) -> float:
    """Silence between two source-adjacent words: the measured gap, else the raw inter-word time. A recording dropout
    is the recording's, not the speaker's: it counts as no pause unless ``dropout_is_pause``."""
    g = ix.gap_after(a.id)
    if g is not None and g.before_word_id == b.id:
        if g.is_dropout and not dropout_is_pause:
            return 0.0
        return g.duration_ms
    return max(0.0, (b.start_us - a.end_us) / 1000.0)


def _sides_ms(ix: TakeIndex, w: Word, edge_ms: float) -> tuple[float, float]:
    """Silence before and after ``w`` (a dropout counts: the cut lands in digital silence); ``edge_ms`` at the take's
    first/last word, where there is no neighbour to cut against."""
    prev_, next_ = ix.prev_word(w.id), ix.next_word(w.id)
    before = _silence_ms(ix, prev_, w, dropout_is_pause=True) if prev_ is not None else edge_ms
    after = _silence_ms(ix, w, next_, dropout_is_pause=True) if next_ is not None else edge_ms
    return before, after


def _speaker_median_ms(ix: TakeIndex) -> float | None:
    """The speaker's own median pause: inner gaps of at least 250 ms that are not recording dropouts."""
    inner = [g.duration_ms for g in ix.gaps if g.is_inner and g.duration_ms >= 250.0 and not g.is_dropout]
    return statistics.median(inner) if inner else None


def _gap_class(ix: TakeIndex, g: Gap) -> str:
    """``sentence`` (between sentences or after sentence-final punctuation), ``clause`` (after a comma, colon, dash)
    or ``hesitation`` (mid-clause: the doctrine's tighten case, typically after a filler)."""
    a, b = ix.word(g.after_word_id), ix.word(g.before_word_id)  # type: ignore[arg-type]
    text = a.text.strip()
    if a.sentence_id != b.sentence_id or _SENT_PUNCT.search(text):
        return "sentence"
    if a.kind != "filler" and _CLAUSE_PUNCT.search(text):
        return "clause"
    return "hesitation"


# ============================================================================================ story
@dataclass
class _Story:
    keep: list[bool]
    reasons: dict[int, str] = field(default_factory=dict)  # removed position -> why
    notes: list[str] = field(default_factory=list)
    takes: dict[str, str] = field(default_factory=dict)  # cluster id -> kept sentence id


def _cluster_choice(c: Cluster, ix: TakeIndex) -> str:
    """The index's recommendation, else the last complete take (the digest and the decision guards agree)."""
    if c.recommended_sentence_id:
        return c.recommended_sentence_id
    smap = ix.sentence_map
    complete = [sid for sid in c.sentence_ids if sid in smap and smap[sid].complete]
    return (complete or c.sentence_ids)[-1]


def _cutoff_removal(ix: TakeIndex, s: Sentence, consts: dict[str, Any], *,
                    protected: bool = False) -> tuple[set[int], str] | None:
    """Source positions a sentence loses to its cut-off words, with the reason, or None when the sentence must go.

    A fragment the speaker restarts inside the sentence (``truncated`` None, a word follows) goes alone when both
    sides of it have a closure to cut in; without one it stays where it is: it is inside continuous kept speech, not
    at a join, so the listener hears the speaker's own stumble (cutting-and-pacing.md: a stutter with no gap to cut in
    stays; ``validate.cutoffs_at_joins``: "stays a judgment call"), never a broken word and a jump. It is cut after
    all when another removal in the sentence would leave it at a join.
    Any other cut-off (a dropout-chopped word, a fragment at the sentence's end) takes its clause: back to the nearest
    clause edge inside the sentence and forward to the next one (punctuation, or a pause of at least the articulatory
    maximum; a dropout is not a pause the speaker made, and a chopped neighbour never ends the removal). The sentence
    start is that edge only when no lexical word comes before the cut-off (nothing said is lost); a cut-off later in a
    clause that starts the sentence drops the sentence, as does a removal that leaves fewer than two lexical words.
    A ``protected`` sentence (the spoken CTA, or the decision's payoff/CTA/must-keep) is never dropped by this rule:
    with no clause edge, only the cut-off word itself goes (a mid-clause seam), and the reason says so.
    """
    art = _prior(consts, "pauses.articulatory_max_ms")
    closure_min = _prior(consts, "fillers.coarticulated_cut_min_closure_ms")
    words = [ix.word(w) for w in s.word_ids]
    cut_idx = [i for i, w in enumerate(words) if w.kind == "cutoff"]
    if not cut_idx:
        return set(), ""

    def pause_between(i: int, j: int) -> float:
        return _silence_ms(ix, words[i], words[j], dropout_is_pause=False)

    def edge_before(j: int) -> bool:  # a clause edge between words[j-1] and words[j] (j >= 1)
        return bool(_CLAUSE_PUNCT.search(words[j - 1].text.strip())) or pause_between(j - 1, j) >= art

    def edge_after(k: int) -> bool:  # a clause edge after words[k]
        if k + 1 >= len(words):
            return True
        if words[k + 1].kind == "cutoff":
            return False
        return bool(_CLAUSE_PUNCT.search(words[k].text.strip())) or pause_between(k, k + 1) >= art

    remove: set[int] = set()
    why: list[str] = []
    stumbles: list[int] = []  # restarted fragments left in place (no closure to cut in)
    for i in cut_idx:
        w = words[i]
        restarted = w.truncated is None and i + 1 < len(words) and words[i + 1].kind != "cutoff"
        if restarted:
            if min(_sides_ms(ix, w, closure_min)) >= closure_min:
                remove.add(i)
                why.append(f"stumble {w.id} {w.display()!r} cut alone (the line restarts after it)")
            else:
                stumbles.append(i)
            continue
        opens = all(words[k].kind != "word" for k in range(i))  # only fillers/events/fragments before it
        start = next((j for j in range(i, 0, -1) if edge_before(j)), 0 if opens else None)
        if start is None:
            if not protected:
                return None
            end = i
            while end + 1 < len(words) and words[end + 1].kind == "cutoff":
                end += 1
            remove.update(range(i, end + 1))
            why.append(f"{'dropout-chopped' if w.truncated else 'cut-off'} word {w.id} {w.display()!r} in a protected "
                       "sentence with no clause edge: only the chopped word removed (a mid-clause seam)")
            continue
        end = i
        while not edge_after(end):
            end += 1
        remove.update(range(start, end + 1))
        kind = "dropout-chopped word" if w.truncated else "cut-off word"
        back = f" back to {words[start - 1].id} {words[start - 1].display()!r}" if start > 0 else " (sentence start)"
        why.append(f"{kind} {w.id} {w.display()!r}: clause {words[start].id}-{words[end].id} removed{back}")
    for i in stumbles:
        w = words[i]
        if i in remove:
            continue
        if (i - 1) in remove or (i + 1) in remove:  # another removal would leave it at a join: it goes too
            remove.add(i)
            why.append(f"stumble {w.id} {w.display()!r} cut with the removal beside it (it would sit at a join)")
        else:
            why.append(f"stumble {w.id} {w.display()!r} kept: no closure to cut in, inside continuous speech")
    lexical_left = sum(1 for k, w in enumerate(words) if k not in remove and w.kind == "word")
    if lexical_left < 2 and not protected:
        return None
    return {ix.word_pos(words[k].id) for k in remove}, "; ".join(why)


#: How close (µs) to the end of the media a last word must end for its sentence to count as cut off by the stop
UNFINISHED_TAIL_US = 600_000
_CLOSING = re.compile(r"[.!?…][\"')\]]*$")


def _unfinished_tail(ix: TakeIndex) -> Sentence | None:
    """The take's last sentence when the recording stops on it mid-line: its last spoken word has no closing
    punctuation and ends within :data:`UNFINISHED_TAIL_US` of the media's end."""
    if not ix.sentences:
        return None
    last = ix.sentences[-1]
    spoken = [ix.word(w) for w in last.word_ids if ix.word(w).kind != "event"]
    if not spoken or _CLOSING.search(spoken[-1].text.strip()):
        return None
    end = int(getattr(ix.media, "duration_us", 0) or 0)
    if end and end - spoken[-1].end_us > UNFINISHED_TAIL_US:
        return None
    return last


def _protected_sentences(ix: TakeIndex, decision: PlanDecision) -> set[str]:
    """Sentences the decision's payoff/CTA/must-keep ranges point into (the planner saw them and wants them)."""
    out: set[str] = set()
    ranges = [*decision.must_keep, *(r for r in (decision.payoff, decision.cta) if r is not None)]
    for r in ranges:
        if not (ix.has_word(r.from_word) and ix.has_word(r.to_word)):
            continue
        if ix.word_pos(r.from_word) > ix.word_pos(r.to_word):
            continue
        out.update(sid for sid in (ix.word(w).sentence_id for w in ix.word_ids(r.from_word, r.to_word)) if sid)
    return out


def _story(ix: TakeIndex, decision: PlanDecision | None, consts: dict[str, Any]) -> _Story:
    smap, cmap = ix.sentence_map, ix.cluster_map
    n = len(ix.words)
    st = _Story(keep=[True] * n)
    dropped: set[str] = set()

    def drop(sid: str, why: str) -> None:
        if sid in dropped or sid not in smap:
            return
        dropped.add(sid)
        for wid in smap[sid].word_ids:
            p = ix.word_pos(wid)
            st.keep[p] = False
            st.reasons[p] = why

    # what the cut-off rule says about every sentence, decided first so a take choice can fall back on it; the spoken
    # CTA and the decision's payoff/CTA/must-keep sentences are never dropped by it
    protected: set[str] = _protected_sentences(ix, decision) if decision is not None else set()
    shielded = protected | {s.id for s in ix.sentences if is_cta_sentence(s.text)}
    verdict: dict[str, tuple[set[int], str] | None] = {
        s.id: _cutoff_removal(ix, s, consts, protected=s.id in shielded) for s in ix.sentences}

    # --- one take per retake cluster
    chosen: dict[str, tuple[str, str]] = {c.id: (_cluster_choice(c, ix), "recommended") for c in ix.clusters}
    if decision is not None:
        for tc in decision.choose_take:
            c = cmap.get(tc.cluster_id)
            if c is not None and tc.sentence_id in c.sentence_ids:
                chosen[c.id] = (tc.sentence_id, "chosen by the plan")
            else:
                st.notes.append(f"choose_take {tc.cluster_id}/{tc.sentence_id} skipped: not a take of that cluster")
    for c in ix.clusters:
        sid, how = chosen[c.id]
        if verdict.get(sid) is None:  # the take loses its line to a cut-off: fall back to one that survives
            alts = [x for x in reversed(c.sentence_ids) if verdict.get(x) is not None and smap[x].complete] \
                or [x for x in reversed(c.sentence_ids) if verdict.get(x) is not None]
            if alts:
                st.notes.append(f"{c.id}: {sid} ({how}) is unusable (its cut-off has no clause edge); kept {alts[0]}")
                sid, how = alts[0], "fallback"
        protected.add(sid)
        st.takes[c.id] = sid
        for other in c.sentence_ids:
            if other != sid:
                drop(other, f"retake: {c.id} keeps {sid} ({how})")

    # --- abandoned sentences (incomplete on evidence: a false start, a line the take never finishes)
    for s in ix.sentences:
        if not s.complete and s.id not in protected:
            drop(s.id, f"{s.id} is abandoned (incomplete): a false start or a line the take never finishes")
    # --- a last line the recording cuts off: no closing punctuation and the take ends within a breath of it. The
    # index can still mark it complete (the transcript has no later evidence), but ending on it ends mid-sentence
    # ("Start with who buys, what they actually"). Dropped even when protected: an unfinished line is never an ending.
    # Never the only line left: a short take the ASR left unpunctuated still ships rather than ships nothing.
    tail = _unfinished_tail(ix)
    if tail is not None and tail.id not in dropped and any(s.id not in dropped and s.id != tail.id
                                                           for s in ix.sentences):
        drop(tail.id, f"{tail.id} is cut off by the end of the recording (no closing punctuation)")

    # --- the decision's drops: never the spoken CTA (except as a repeat), never past half of the spoken words
    if decision is not None:
        spoken_total = sum(1 for w in ix.words if w.is_spoken)
        removed = 0
        for d in decision.drop_sentences:
            s = smap.get(d.sentence_id)
            if s is None:
                st.notes.append(f"drop {d.sentence_id} skipped: not a sentence of this take")
                continue
            if s.id in dropped:
                continue
            if d.reason != "repeat" and is_cta_sentence(s.text):
                st.notes.append(f"drop {s.id} ({d.reason}) skipped: it is the spoken call to action")
                continue
            spoken = sum(1 for w in s.word_ids if ix.word(w).is_spoken)
            if spoken_total and removed + spoken > MAX_DROP_SHARE * spoken_total:
                st.notes.append(f"drop {s.id} ({d.reason}) skipped: drops would remove more than "
                                f"{MAX_DROP_SHARE:.0%} of the spoken words")
                continue
            removed += spoken
            drop(s.id, f"{d.reason}: {s.text.strip()[:60]}")
        # --- the decision's trims: a label or aside at one edge of a kept sentence ("Part one:" before the claim),
        # applied only when the validator's own rules accept it and the sentence is still in the story
        for t in decision.trim_words:
            problem = trim_problem(t, ix)
            if problem:
                st.notes.append(f"trim {t.from_word}-{t.to_word} skipped: {problem.removeprefix('trim_words: ')}")
                continue
            sid = ix.word(t.from_word).sentence_id
            if sid in dropped:
                continue
            positions = [ix.word_pos(w) for w in ix.word_ids(t.from_word, t.to_word)]
            spoken = sum(1 for p in positions if st.keep[p] and ix.words[p].is_spoken)
            if spoken_total and removed + spoken > MAX_DROP_SHARE * spoken_total:
                st.notes.append(f"trim {t.from_word}-{t.to_word} ({t.reason}) skipped: drops and trims would remove "
                                f"more than {MAX_DROP_SHARE:.0%} of the spoken words")
                continue
            removed += spoken
            text = " ".join(ix.words[p].text.strip() for p in positions)
            for p in positions:
                st.keep[p] = False
                st.reasons[p] = f"{t.reason}: trimmed {text!r} from {sid}"

    # --- cut-off words in the kept sentences
    for s in ix.sentences:
        if s.id in dropped:
            continue
        v = verdict[s.id]
        if v is None:
            drop(s.id, f"{s.id} has a cut-off word with no clause edge to cut back to: dropped whole")
            continue
        positions, why = v
        for p in positions:
            st.keep[p] = False
            st.reasons[p] = why
        if why and "kept: no closure" in why:
            st.notes.append(f"{s.id}: " + "; ".join(x for x in why.split("; ") if "kept: no closure" in x))

    # --- words outside every sentence (events, stray tokens) stay only beside kept speech; a stray cut-off never
    if any(w.sentence_id for w in ix.words):
        for i, w in enumerate(ix.words):
            if w.sentence_id:
                continue
            left = next((st.keep[j] for j in range(i - 1, -1, -1) if ix.words[j].sentence_id), None)
            right = next((st.keep[j] for j in range(i + 1, n) if ix.words[j].sentence_id), None)
            if not (left or right):
                st.keep[i] = False
                st.reasons[i] = "between removed sentences"
    for i, w in enumerate(ix.words):
        if not (st.keep[i] and w.kind == "cutoff"):
            continue
        if not w.sentence_id:  # a stray fragment outside any sentence
            st.keep[i] = False
            st.reasons[i] = f"cut-off word {w.id} {w.display()!r} outside any sentence"
        elif i == 0 or i + 1 == n or not st.keep[i - 1] or not st.keep[i + 1]:
            # a kept stumble that the story's other removals leave at a join (or at the take's edge) goes
            st.keep[i] = False
            st.reasons[i] = f"stumble {w.id} {w.display()!r} at a join after the story's removals"
    return st


def _runs(flags: Sequence[bool], ix: TakeIndex) -> list[tuple[int, int]]:
    """Kept source runs (inclusive positions), split where the source file changes."""
    out: list[tuple[int, int]] = []
    for p, k in enumerate(flags):
        if not k:
            continue
        if out and p == out[-1][1] + 1 and ix.words[p].source == ix.words[out[-1][0]].source:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out


def _removed_ranges(st: _Story, ix: TakeIndex) -> list[dict[str, str]]:
    """Removed source runs with their reasons (consecutive positions sharing a reason form one range)."""
    out: list[dict[str, str]] = []
    cur: list[int] = []
    cur_reason = ""
    for p, k in enumerate(st.keep):
        why = st.reasons.get(p, "not selected for the story")
        if not k and cur and p == cur[-1] + 1 and why == cur_reason:
            cur.append(p)
            continue
        if cur:
            out.append({"from_word": ix.words[cur[0]].id, "to_word": ix.words[cur[-1]].id, "reason": cur_reason})
            cur = []
        if not k:
            cur, cur_reason = [p], why
    if cur:
        out.append({"from_word": ix.words[cur[0]].id, "to_word": ix.words[cur[-1]].id, "reason": cur_reason})
    return out


# ============================================================================================ brief / style
def _style_window(consts: dict[str, Any], primary: str | None) -> tuple[float, float]:
    windows = consts.get("length_s") if isinstance(consts.get("length_s"), dict) else {}
    if primary and isinstance(windows, dict):
        key = primary.strip().lower().replace("-", "_").replace(" ", "_")
        v = windows.get(key)
        if isinstance(v, list | tuple) and len(v) == 2 and all(_num(x) for x in v):
            return float(v[0]), float(v[1])
    return _prior(consts, "length_s.default")


def _brief_and_style(ix: TakeIndex, decision: PlanDecision | None, st: _Story, *, brief: str | None,
                     style: str | None, consts: dict[str, Any], band: str) -> tuple[Brief, Style]:
    from studio.plan.digest import STANDARD_MAX_OUTPUT_S

    kept = sum(1 for i, w in enumerate(ix.words) if st.keep[i] and w.is_spoken)
    spoken = sum(1 for w in ix.words if w.is_spoken)
    kept_sids = list(dict.fromkeys(w.sentence_id for i, w in enumerate(ix.words) if st.keep[i] and w.sentence_id))

    def lexical(sid: str) -> int:
        return sum(1 for w in ix.sentence_map[sid].word_ids if ix.word(w).kind == "word"
                   and st.keep[ix.word_pos(w)])

    # the hook is the first kept line that says something: a spoken series label ("Part one.") or a two-word
    # greeting before the claim is not the hook (story-and-hook.md: the first kept line is content)
    hook_sid = next((s for s in kept_sids if lexical(s) >= 3), kept_sids[0] if kept_sids else None)
    hook_text = ix.sentence_map[hook_sid].text.strip() if hook_sid else ""
    cta_text = ""
    if decision is not None and decision.cta is not None and ix.has_word(decision.cta.from_word) \
            and ix.has_word(decision.cta.to_word) \
            and ix.word_pos(decision.cta.from_word) <= ix.word_pos(decision.cta.to_word):
        # the planner found the CTA (the lexicon may not know its wording): the brief carries the kept words of
        # that range, verbatim (never an invented one)
        cta_text = " ".join(ix.word(w).text.strip() for w in ix.word_ids(decision.cta.from_word, decision.cta.to_word)
                            if st.keep[ix.word_pos(w)] and ix.word(w).kind == "word")
    if not cta_text:
        cta_text = " ".join(ix.sentence_map[s].text.strip() for s in kept_sids
                            if is_cta_sentence(ix.sentence_map[s].text))
    code_goal = (f"Standard tier code plan: the take as spoken ({kept} of {spoken} spoken words kept; recommended "
                 f"takes, false starts and chopped words removed; pauses tightened relative to the speaker's median, "
                 f"{band} energy)")
    creator = " ".join((brief or "").split())
    primary = (decision.style_primary.strip() if decision is not None else "") or (style or "").strip() or None
    if decision is not None:
        b = Brief(goal=decision.goal.strip() or code_goal, hook=decision.hook_promise.strip() or hook_text,
                  text=creator or decision.notes.strip() or code_goal, cta=cta_text)
        blend = [decision.style_secondary] if decision.style_secondary else []
    else:
        b = Brief(goal=code_goal, hook=hook_text, text=creator or code_goal, cta=cta_text)
        blend = []
    _lo, hi = _style_window(consts, primary)
    b.target_length_s = min(float(STANDARD_MAX_OUTPUT_S), hi)
    e = ix.energy
    pace = min(1.0, max(0.0, (e.wpm - 110.0) / 80.0)) if e.wpm > 0 else 0.5
    dials = StyleDials(energy=round(min(1.0, max(0.0, e.overall)), 3), pace=round(pace, 3))
    return b, Style(primary=primary, blend=blend, dials=dials)


# ============================================================================================ scratch application
class _Scratch:
    """The op list under construction, applied as it grows so later steps read the real document state (page and
    segment IDs are assigned by the ops). An op the ops layer rejects is left out and noted, never emitted."""

    def __init__(self, ix: TakeIndex, platform: str, job: Job | None):
        self.ix = ix
        self.job = job
        self.doc: CutDocument = new_document(created_by=PLAN_BY, platforms=(platform,))  # type: ignore[arg-type]
        self.ops: list[dict[str, Any]] = []
        self.notes: list[str] = []

    def push(self, op: dict[str, Any], *, keep_no_change: bool = False) -> bool:
        new, res = apply_ops(self.doc, [op], self.ix, by=PLAN_BY, persist=False)
        r = res[0]
        if r.applied or (keep_no_change and r.reason == "no change"):
            self.doc = new
            self.ops.append(op)
            return True
        self.notes.append(f"{op.get('op')} dropped: {r.reason}")
        return False

    def kept_positions(self) -> dict[int, str]:
        out: dict[int, str] = {}
        for s in self.doc.segments:
            for p in range(self.ix.word_pos(s.from_word), self.ix.word_pos(s.to_word) + 1):
                out[p] = s.id
        return out

    def compile(self, doc: CutDocument | None = None) -> Timeline | None:
        """The compiled timeline of ``doc`` (default: the document so far), or None (noted) when it fails: a plan
        never fails on a compile problem; the pipeline's own compile reports it."""
        try:
            from studio.compile.timeline import compile as compile_timeline

            return compile_timeline(doc if doc is not None else self.doc, self.ix, job=self.job)
        except Exception as e:
            self.notes.append(f"compile for planning failed: {type(e).__name__}: {str(e)[:120]}")
            return None


# ============================================================================================ fillers / pauses
def _filler_ops(sc: _Scratch, decision: PlanDecision | None, consts: dict[str, Any]) -> int:
    """``cut_words`` per filler the doctrine (or the decision) removes; see the module docstring for the rules."""
    ix = sc.ix
    bounded_ms = _prior(consts, "fillers.cut_if_bounded_by_gap_ms")
    closure_min = _prior(consts, "fillers.coarticulated_cut_min_closure_ms")
    kept = sc.kept_positions()
    first_sid = next((ix.words[p].sentence_id for p in sorted(kept) if ix.words[p].is_spoken), None)
    wanted = set(decision.cut_filler_word_ids) if decision is not None else set()
    smap = ix.sentence_map
    n = 0
    for p in sorted(kept):
        w = ix.words[p]
        if w.kind != "filler" and w.id not in wanted:
            continue
        before, after = _sides_ms(ix, w, bounded_ms)
        s = smap.get(w.sentence_id) if w.sentence_id else None
        initial = s is not None and next((x for x in s.word_ids if ix.word(x).is_spoken), None) == w.id
        if w.id in wanted:
            why = f"filler {w.display()} (the plan asked)"
        elif w.sentence_id is not None and w.sentence_id == first_sid:
            why = f"filler {w.display()} in the first sentence (fillers.in_first_sentence 0)"
        elif initial:
            why = f"sentence-initial filler {w.display()} (the seam falls between thoughts)"
        elif before >= bounded_ms and after >= bounded_ms:
            why = f"filler {w.display()} bounded by pauses >= {bounded_ms:.0f} ms"
        else:
            continue
        if min(before, after) < closure_min:
            sc.notes.append(f"{w.id} {w.display()} kept: no closure on both sides to cut in (coarticulated)")
            continue
        if any(q in kept and ix.words[q].kind == "cutoff" for q in (p - 1, p + 1)):
            sc.notes.append(f"{w.id} {w.display()} kept: cutting it would leave the kept stumble beside it at a join")
            continue
        if sc.push({"op": "cut_words", "from_word": w.id, "to_word": w.id, "reason": why}):
            n += 1
    return n


def _stall_target_ms(median: float, band_range: tuple[float, float]) -> float:
    """Where a stall (a boundary pause past both twice the median and the dead-air threshold) comes down to: the
    speaker's median pause held inside the energy band's boundary range (cutting-and-pacing.md: 250-450 ms
    conversational, 350-700 ms calm), then inside 0.7-1.3x the median so the speaker's own rhythm wins over the band.
    Without a measured median, the band's middle."""
    lo, hi = band_range
    if median <= 0:
        return (lo + hi) / 2.0
    return min(1.3 * median, max(0.7 * median, min(hi, max(lo, median))))


def _pause_ops(sc: _Scratch, decision: PlanDecision | None, consts: dict[str, Any], band: str) -> int:
    """``set_gap`` per playing pause the doctrine tightens; see the module docstring for the rules."""
    ix = sc.ix
    median = _speaker_median_ms(ix) or 0.0
    beats = _prior(consts, "pauses.deliberate_beat_by_energy_s")
    beat_hi_ms = 1000.0 * beats.get(band, beats["conversational"])[1]
    bounds = _prior(consts, "pauses.boundary_by_energy_ms")
    stall_ms = _stall_target_ms(median, bounds.get(band, bounds["conversational"]))
    hes_from_ms = 1000.0 * _prior(consts, "pauses.hesitation_tighten_from_s")[0]
    hes_target_ms = _prior(consts, "pauses.hesitation_target_ms")[1]
    dead_air_ms = 1000.0 * _prior(consts, "pauses.dead_air_cut_over_s")
    over_x = _prior(consts, "pauses.relative_tighten_over_x_median")
    keep_ids = set(decision.deliberate_beat_gap_ids) if decision is not None else set()
    kept = sc.kept_positions()
    n = 0
    for g in ix.gaps:
        if not g.is_inner:
            continue
        if g.id in keep_ids and not g.is_dropout:  # a recording dropout is never the speaker's beat
            continue
        pa, pb = ix.word_pos(g.after_word_id), ix.word_pos(g.before_word_id)  # type: ignore[arg-type]
        if pa not in kept or pb not in kept or kept[pa] != kept[pb]:
            continue  # at a cut (the seam's pads decide) or in removed material
        dur = g.duration_ms
        cls = _gap_class(ix, g)
        if g.is_dropout:
            # the recording's own silence, not the speaker's: dead air whatever its length. The override keeps the
            # gap's edges and drops its middle, where the zeros are; the dropout softening and the room-tone floor
            # cover the edges
            target = max(hes_target_ms, median) if cls == "hesitation" else stall_ms
            threshold = target
        elif cls == "hesitation":
            target = max(hes_target_ms, median)
            threshold = min(hes_from_ms, dead_air_ms)
        elif dur > max(over_x * median, dead_air_ms):
            # a stall at a boundary: down to the speaker's boundary pause, not to the top of the beat window (a calm
            # take's 3 s stall must not ship as the 1.5 s doctrine calls dead air)
            target = stall_ms
            threshold = target
        else:  # a boundary pause keeps the speaker's rhythm: only one past a beat and past twice the median shrinks
            target = max(beat_hi_ms, median)
            threshold = min(max(target, over_x * median), dead_air_ms)  # dead air whatever the median says
        if dur <= threshold or dur - target < _MIN_TRIM_MS:
            continue
        if sc.push({"op": "set_gap", "gap_id": g.id, "ms": round(target)}):
            n += 1
    return n


# ============================================================================================ pins / framing
def _pin_ops(sc: _Scratch, decision: PlanDecision | None) -> int:
    ix = sc.ix
    kept = sc.kept_positions()

    def lexical(ids: Iterable[str]) -> list[str]:
        return [w for w in ids if ix.has_word(w) and ix.word_pos(w) in kept and ix.word(w).kind == "word"]

    pins: dict[str, list[str]] = {"must_keep": [], "payoff": [], "cta": []}
    if decision is not None:
        wanted = (("must_keep", decision.must_keep), ("payoff", [decision.payoff] if decision.payoff else []),
                  ("cta", [decision.cta] if decision.cta else []))
        for kind, ranges in wanted:
            for r in ranges:
                if not (ix.has_word(r.from_word) and ix.has_word(r.to_word)) \
                        or ix.word_pos(r.from_word) > ix.word_pos(r.to_word):
                    sc.notes.append(f"pin {kind} {r.from_word}-{r.to_word} skipped: not a forward range of this take")
                    continue
                ids = ix.word_ids(r.from_word, r.to_word)
                pins[kind].extend(lexical(ids))
                lost = [w for w in ids if ix.word(w).kind == "word" and ix.word_pos(w) not in kept]
                if lost:  # the planner asked for these words; say plainly that the story does not carry them
                    sc.notes.append(f"pin {kind} {r.from_word}-{r.to_word}: {len(lost)} of its words are not in the "
                                    f"story ({', '.join(lost[:6])}{' …' if len(lost) > 6 else ''})")
    for s in ix.sentences:  # the spoken CTA is always protected (endings-loops-ctas.md: retained via pinned IDs)
        if is_cta_sentence(s.text):
            pins["cta"].extend(lexical(s.word_ids))
    n = 0
    for kind, ids in pins.items():
        uniq = sorted(set(ids), key=ix.word_pos)  # source order, each word once
        if uniq and sc.push({"op": "pin", "word_ids": uniq, "kind": kind}):
            n += len(uniq)
    return n


def _framing_op(sc: _Scratch, decision: PlanDecision | None, consts: dict[str, Any]) -> bool:
    if decision is None or decision.punch_in is None:
        return False
    from studio.compile.timeline import _ok_scale

    ix = sc.ix
    anchor = decision.punch_in.anchor_word
    lean = _prior(consts, "framing.punch_lean_in_x")[0]
    kept = sc.kept_positions()
    if not ix.has_word(anchor) or ix.word_pos(anchor) not in kept:
        sc.notes.append(f"punch-in at {anchor} skipped: the word is not in the story")
        return False
    ok = _ok_scale(ix, 1920)
    if ok + 1e-6 < lean:
        sc.notes.append(f"punch-in at {anchor} skipped: this source stays clean only to x{ok:.2f} (needs x{lean:.2f})")
        return False
    # hold to the end of the anchor's sentence (at least punch_hold_min_s, so a later sentence end if that one comes
    # too soon), returning to base at that boundary or at the next seam, never mid-sentence (framing-and-zooms.md)
    seg = sc.doc.segment(kept[ix.word_pos(anchor)])
    run = ix.word_ids(anchor, seg.to_word)
    hold_min = _prior(consts, "framing.punch_hold_min_s")
    t0 = ix.word(anchor).start_us
    end: str | None = None
    for k, wid in enumerate(run):
        boundary = k + 1 == len(run) or ix.word(run[k + 1]).sentence_id != ix.word(wid).sentence_id
        if boundary and (ix.word(wid).end_us - t0) / 1e6 >= hold_min:
            end = wid
            break
    if end is None:
        hold_s = (ix.word(seg.to_word).end_us - t0) / 1e6
        sc.notes.append(f"punch-in at {anchor} skipped: it would hold only {hold_s:.1f} s before the cut")
        return False
    tl = sc.compile()
    if tl is not None:
        span = tl.word_map.get(anchor)
        if span is not None and float(span.out_start) < _prior(consts, "hook.no_punch_in_first_s")[1]:
            sc.notes.append(f"punch-in at {anchor} skipped: inside the hook's first seconds")
            return False
    return sc.push({"op": "set_framing", "from_word": anchor, "to_word": end, "scale": round(lean, 3),
                    "center": "face", "ease": "cut", "anchor_word": anchor})


# ============================================================================================ markers
def _marker_ops(sc: _Scratch, decision: PlanDecision | None) -> int:
    """``add_text`` labels for the decision's structure markers, on their kept words. A numbered set ("1/3, 2/3,
    3/3") is the listicle's item counter (``styles/educational-tutorial-listicle.md``: one identical marker per item,
    entering on each ordinal, for the muted or re-watching viewer): each label holds from its item's first word to
    the word before the next item opens, the last to the word before the spoken CTA (else the story's end), whatever
    shorter range the planner named ("the words opening each item" lasted 0.8 s on take40). Short labels sit beside
    the face on a dark pill."""
    if decision is None:
        return 0
    ix = sc.ix
    pos = sc.doc.output_positions(ix)
    order = sorted(pos, key=pos.__getitem__)
    marks: list[tuple[str, str, str]] = []  # (label, first kept word, last kept word)
    for mk in decision.structure_markers:
        label = " ".join(mk.label.split())
        if not label or not (ix.has_word(mk.anchor_from_word) and ix.has_word(mk.anchor_to_word)):
            sc.notes.append(f"marker {mk.label!r} skipped: empty label or unknown anchor words")
            continue
        if ix.word_pos(mk.anchor_from_word) > ix.word_pos(mk.anchor_to_word):
            sc.notes.append(f"marker {label!r} skipped: reversed anchors")
            continue
        kept = sorted((w for w in ix.word_ids(mk.anchor_from_word, mk.anchor_to_word) if w in pos), key=pos.__getitem__)
        if not kept:
            sc.notes.append(f"marker {label!r} skipped: its words are not in the story")
            continue
        marks.append((label, kept[0], kept[-1]))
    if len(marks) >= 2 and all(_NUMBERED_LABEL.fullmatch(m[0]) for m in marks):
        marks.sort(key=lambda m: pos[m[1]])
        cta = [w for w in ((decision.cta.from_word,) if decision.cta is not None else ()) if w in pos] \
            or [w for w in sc.doc.pins.cta_word_ids if w in pos]
        held: list[tuple[str, str, str]] = []
        for i, (label, first, last) in enumerate(marks):
            if i + 1 < len(marks):
                stop = pos[marks[i + 1][1]] - 1
            elif cta and pos[cta[0]] > pos[first]:
                stop = pos[cta[0]] - 1
            else:
                stop = len(order) - 1
            held.append((label, first, order[stop] if stop > pos[last] else last))
        marks = held
    n = 0
    for label, first, last in marks:
        op: dict[str, Any] = {"op": "add_text", "kind": "label", "text": label, "anchor_from_word": first,
                              "anchor_to_word": last, "job": "structure marker from the plan"}
        if len(label) <= _SIDE_LABEL_MAX_CHARS:
            op.update(position=_SIDE_LABEL_POINT.model_dump(), animation="fade",
                      style=TextStyle(size_px=_SIDE_LABEL_PX, background=_SIDE_LABEL_PILL).model_dump())
        if sc.push(op):
            n += 1
    return n


# ============================================================================================ captions
def _page_text(ix: TakeIndex, word_ids: Sequence[str], fixes: dict[str, str] | None = None) -> str:
    """The page as the caption builder shows it: fillers and audio events dropped, a decision's fix in place."""
    out = []
    for w in word_ids:
        word = ix.word(w)
        if word.kind in ("filler", "event"):
            continue
        out.append((fixes or {}).get(w) or word.text.strip())
    return " ".join(t for t in out if t)


@dataclass
class _Sizing:
    """What the caption geometry needs at every size step: the frame, the safe line width and the pager priors."""

    W: int
    max_w: float
    params: CaptionParams

    def fits(self, style: CaptionStyle, text: str) -> bool:
        from studio.compile.captions import caption_fit

        f = caption_fit(style, text, self.W, self.params, max_width=self.max_w)
        return f.lines == 1 and f.shrink >= 0.999


def _caption_style(size: int) -> CaptionStyle:
    """The house caption style at ``size`` px: one line, outline at 10 % of the font (doctrine 8-12 %)."""
    return CaptionStyle(size_px=size, stroke_px=round(size / 10.0, 1), lines=1)


def _word_spans(sc: _Scratch, tl: Timeline | None) -> dict[str, tuple[Any, Any, int]]:
    """Output spans of the story's words (the compiled timeline's, else the planning estimate)."""
    from studio.compile import captions as cap

    return cap._segment_word_spans(tl, sc.ix) if tl is not None else cap._estimated_spans(sc.doc, sc.ix)


def _page_screen_s(spans: dict[str, tuple[Any, Any, int]], ids: Sequence[str], next_ids: Sequence[str] | None,
                   params: CaptionParams) -> float | None:
    """The longest a page can stay on screen (the pager's own measure): until the next page takes over, less the
    2-frame gap, or to the hold after its last word at the end; never less than its speech. None without spans."""
    shown = [w for w in ids if w in spans]
    if not shown:
        return None
    a, b = float(spans[shown[0]][0]), float(spans[shown[-1]][1])
    nxt = [w for w in (next_ids or ()) if w in spans]
    d = (float(spans[nxt[0]][0]) - a - params.gap_s) if nxt else (b - a + params.hold_s)
    return max(d, b - a)


def _min_screen_s(sc: _Scratch, ids: Sequence[str], params: CaptionParams) -> float:
    """``min_page_s``, or ``punch_page_s`` for a one-word page on a number, a payoff word or a strong emphasis."""
    from studio.compile.captions import _is_number

    shown = [w for w in ids if sc.ix.word(w).kind not in ("filler", "event")]
    if len(shown) == 1:
        w = sc.ix.word(shown[0])
        if _is_number(w.text) or w.id in set(sc.doc.pins.payoff_word_ids) or w.emphasis >= 0.8:
            return params.punch_page_s
    return params.min_page_s


def _split_page(sc: _Scratch, page: CaptionPage, style: CaptionStyle, strict: CaptionParams, sz: _Sizing,
                spans: dict[str, tuple[Any, Any, int]], next_ids: Sequence[str] | None) -> list[list[str]] | None:
    """A page that would wrap at this size, re-paged with the pager's own dynamic program (break costs, page
    timing) into shorter one-line pages; None when no such split keeps every part on one line at full size and on
    screen at least ``min_page_s`` (``punch_page_s`` for a one-word punch page): flicker is worse than a smaller
    size, so the ladder steps down instead."""
    from studio.compile import captions as cap

    ix = sc.ix
    toks = cap._build_tokens(page.word_ids, spans, ix, strict, set(sc.doc.pins.payoff_word_ids))
    if len(toks) < 2 or len(toks) != len(page.word_ids):
        return None
    n = len(toks)
    dur = float(toks[-1].end - toks[0].start)
    chars = sum(len(x.text) + 1 for x in toks)
    fast = dur > 0 and chars / dur > strict.max_cps  # the pager allows one more word in a fast run
    params = strict.with_overrides(max_words=max(1, n - 1 - (1 if fast else 0)))
    parts = cap._page_run(toks, style=style, params=params, width_scale=sz.W / cap.REF_W)
    groups = [[x.wid for x in part] for part in parts]
    if len(groups) < 2:
        return None
    for k, ids in enumerate(groups):
        if not sz.fits(style, _page_text(ix, ids)):
            return None
        screen = _page_screen_s(spans, ids, groups[k + 1] if k + 1 < len(groups) else next_ids, strict)
        if screen is not None and screen + 1e-6 < _min_screen_s(sc, ids, strict):
            return None
    return groups


def _pages_at(sc: _Scratch, tl: Timeline | None, size: int, sz: _Sizing) -> list[CaptionPage] | None:
    """The pager's pages at ``size`` with no page shrunk or wrapped, or None when no paging can do that without a
    flicker (a word wider than the line at this size, or a split that would leave a page on screen under
    ``min_page_s``). The pager pages with its floor at this size, so it splits short pages rather than squeeze them;
    a long page it still lets wrap (the renderer would shrink or wrap it) is re-paged with the pager's own dynamic
    program (:func:`_split_page`), never cut at the line-balance point of the wrap."""
    from studio.compile.captions import auto_caption_plan

    ix = sc.ix
    style = _caption_style(size)
    strict = sz.params.with_overrides(min_size_px=float(size), min_font_scale=1.0)
    plan = auto_caption_plan(sc.doc, ix, style=style, timeline=tl, params=strict)
    spans = _word_spans(sc, tl)
    out: list[CaptionPage] = []
    for k, p in enumerate(plan.pages):
        text = _page_text(ix, p.word_ids)
        if sz.fits(style, text):
            out.append(p)
            continue
        nxt = plan.pages[k + 1].word_ids if k + 1 < len(plan.pages) else None
        groups = _split_page(sc, p, style, strict, sz, spans, nxt)
        if groups is None:
            return None
        for ids in groups:
            out.append(CaptionPage(word_ids=ids, emphasis_word_ids=[w for w in p.emphasis_word_ids if w in ids][:1]))
    return [pg.model_copy(update={"id": f"p{k:03d}"}) for k, pg in enumerate(out, start=1)]


def _paging_faults(sc: _Scratch, pages: Sequence[CaptionPage], tl: Timeline | None = None,
                   params: CaptionParams | None = None) -> tuple[int, int, int]:
    """``(one-word pages, broken phrases, flicker pages)`` of a paging: one-word pages that are not punch words
    (doctrine: 2-4 words, one only for hook, numbers, punchlines), page breaks inside a syntactic unit of one
    sentence ("the | budget", "is | going": :func:`studio.compile.captions.line_break_cost` of at least
    :data:`_BROKEN_PHRASE_COST`) and pages on screen under ``min_page_s`` (``punch_page_s`` for a one-word punch
    page) on the story's timing. Bigger text is only worth it while it costs none of them; the floor's own paging
    is measured the same way."""
    from studio.compile.captions import _is_number, line_break_cost, load_caption_params

    ix = sc.ix
    payoff = set(sc.doc.pins.payoff_word_ids)
    lone = 0
    for p in pages:
        if len(p.word_ids) != 1:
            continue
        w = ix.word(p.word_ids[0])
        if not (w.id in payoff or w.emphasis >= 0.8 or _is_number(w.text)):
            lone += 1
    broken = 0
    for a, b in pairwise(pages):
        wa, wb = ix.word(a.word_ids[-1]), ix.word(b.word_ids[0])
        if wa.sentence_id is None or wa.sentence_id != wb.sentence_id:
            continue
        ta, tb = _page_text(ix, a.word_ids).split(), _page_text(ix, b.word_ids).split()
        if ta and tb and line_break_cost(ta + tb, len(ta)) >= _BROKEN_PHRASE_COST:
            broken += 1
    prm = params or load_caption_params()
    spans = _word_spans(sc, tl)
    short = 0
    for k, p in enumerate(pages):
        nxt = pages[k + 1].word_ids if k + 1 < len(pages) else None
        screen = _page_screen_s(spans, p.word_ids, nxt, prm)
        if screen is not None and screen + 1e-6 < _min_screen_s(sc, p.word_ids, prm):
            short += 1
    return lone, broken, short


def _caption_ops(sc: _Scratch, decision: PlanDecision | None, platform: str, settings: Settings | None,
                 consts: dict[str, Any]) -> tuple[int | None, int]:
    """``set_captions`` at the uniform size plus the decision's page edits; returns ``(size_px, pages)``."""
    from studio.compile import captions as cap

    ix = sc.ix
    tl = sc.compile()
    W = tl.width if tl is not None else cap.REF_W
    H = tl.height if tl is not None else cap.REF_H
    zone = cap.safe_zone_for(platform, width=W, height=H, constants=consts)
    sz = _Sizing(W=W, max_w=W - zone.left - zone.right, params=cap.load_caption_params(settings, consts))
    try:
        floor_size = CAPTION_SIZE_LADDER[-1]
        floor_pages = _pages_at(sc, tl, floor_size, sz)
        floor_faults = _paging_faults(sc, floor_pages, tl, sz.params) if floor_pages is not None else (0, 0, 0)
        chosen: tuple[int, list[CaptionPage]] | None = None
        for size in CAPTION_SIZE_LADDER:
            pages = floor_pages if size == floor_size else _pages_at(sc, tl, size, sz)
            if not pages:
                continue
            faults = _paging_faults(sc, pages, tl, sz.params)
            if any(f > g for f, g in zip(faults, floor_faults, strict=True)):
                continue  # more one-word pages, broken phrases or flicker pages than the floor: step down
            plan = CaptionPlan(pages=pages, style=_caption_style(size))
            placed = sc.compile(sc.doc.model_copy(update={"captions": plan})) if tl is not None else None
            if placed is not None and any(p.style.size_px != size for p in placed.captions):
                continue  # the placer had to shrink a page here: the whole video steps down instead
            chosen = (size, pages)
            break
        if chosen is None:  # even the floor cannot keep every page on one line: the pager's own pages at the floor
            raw = cap.auto_caption_plan(sc.doc, ix, style=_caption_style(floor_size), timeline=tl, params=sz.params)
            if not raw.pages:
                return None, 0
            sc.notes.append(f"captions: no size in {floor_size}-{CAPTION_SIZE_LADDER[0]} px keeps every page on one "
                            f"line at full size without a page under the minimum time on screen; the pager's own "
                            f"pages at {floor_size} px (a page too long for one line wraps rather than flickers)")
            chosen = (floor_size, list(raw.pages))
    except Exception as e:  # captions are optional for the plan; the pipeline's compile builds its own if missing
        sc.notes.append(f"captions skipped: paging failed ({type(e).__name__}: {str(e)[:100]})")
        return None, 0
    size, pages = chosen
    accents = list(dict.fromkeys(decision.caption_emphasis_word_ids)) if decision is not None else []
    if accents:  # the planner's accents replace the automatic ones (a few accents, never both sets)
        pages = [p.model_copy(update={"emphasis_word_ids": []}) for p in pages]
    plan = CaptionPlan(pages=pages, style=_caption_style(size))
    if not sc.push({"op": "set_captions", "plan": plan.model_dump(mode="json")}) or sc.doc.captions is None:
        return None, 0
    doc_plan = sc.doc.captions
    style = doc_plan.style
    page_of = {w: p for p in doc_plan.pages for w in p.word_ids}
    # the doctrine's accent rules hold for the planner's accents too: never on a word another device already lands
    # on (the punch-in anchor, a label), one per page, and at most accent_page_share of the pages (the strongest
    # emphasis first); the automatic path applies the same rules in the pager
    from studio.compile.captions import _device_words

    devices = _device_words(sc.doc)
    budget = max(1, math.ceil(sz.params.accent_page_share * len(doc_plan.pages))) if doc_plan.pages else 0
    done: set[str] = set()
    ranked = sorted(accents, key=lambda w: (-(ix.word(w).emphasis if ix.has_word(w) else 0.0), accents.index(w)))
    for wid in ranked:
        p = page_of.get(wid)
        if p is None or p.id is None:
            sc.notes.append(f"caption emphasis {wid} skipped: not on a caption page")
            continue
        if wid in devices:
            sc.notes.append(f"caption emphasis {wid} skipped: a punch-in or label already lands on it")
            continue
        if p.id in done:
            sc.notes.append(f"caption emphasis {wid} skipped: {p.id} already carries an accent (one per page)")
            continue
        if len(done) >= budget:
            sc.notes.append(f"caption emphasis {wid} skipped: {budget} accent(s) already, the most for "
                            f"{len(doc_plan.pages)} pages ({sz.params.accent_page_share:.0%})")
            continue
        if sc.push({"op": "edit_caption_page", "page_id": p.id, "emphasis_word_ids": [wid]}):
            done.add(p.id)
    if decision is not None:
        fixes = {f.word_id: " ".join(f.text.split()) for f in decision.caption_text_fixes if f.text.strip()}
        for wid in fixes:
            if wid not in page_of:
                sc.notes.append(f"caption text fix {wid} skipped: not on a caption page")
        for p in doc_plan.pages:
            hit = {w: t for w, t in fixes.items() if w in p.word_ids}
            if not hit or p.id is None:
                continue
            text = _page_text(ix, p.word_ids, hit)
            if not sz.fits(style, text):
                sc.notes.append(f"caption text fix on {p.id} skipped: {text!r} does not fit one line at {size} px")
                continue
            sc.push({"op": "edit_caption_page", "page_id": p.id, "text": text})
    return size, len(doc_plan.pages)


# ============================================================================================ voice / colour
def _voice_op(sc: _Scratch) -> str:
    from studio.audio.voice import plan_voice_chain
    from studio.doc.model import VoiceChainSpec

    ix, job = sc.ix, sc.job
    a = ix.audio
    try:
        if job is not None and job.audio_path.exists():
            spec = plan_voice_chain(job, ix)
        else:
            m = {} if a is None else {"snr_db_index": a.snr_db, "noise_floor_db": a.noise_floor_db,
                                      "music_in_room": a.music_in_room, "clipping_ratio": a.clipping_ratio}
            spec = plan_voice_chain(None, ix, measurements=m)
    except Exception as e:  # the default chain is safe; the measurement failure is noted, not fatal
        sc.notes.append(f"voice chain: measurement failed ({type(e).__name__}); default chain")
        spec = VoiceChainSpec()
    if spec.denoise == "isolate":
        music = bool(a is not None and a.music_in_room)
        src_s = ix.duration_us / 1e6
        why = None
        if not music:
            why = "no music in the room"
        elif src_s > PAID_ISOLATION_MAX_S:
            why = f"source longer than {PAID_ISOLATION_MAX_S:.0f} s"
        else:
            why = _isolation_budget_problem(job, src_s, spec.isolation_provider or "elevenlabs")
        if why:
            spec = spec.model_copy(update={
                "denoise": "light", "isolation_provider": None,
                "notes": (spec.notes + "; " if spec.notes else "") + f"standard tier: paid isolation skipped ({why}); "
                "local light denoise instead; needs isolation add-on"})
    sc.push({"op": "set_voice_chain", "spec": spec.model_dump(mode="json")})
    return spec.denoise + (f" ({spec.isolation_provider})" if spec.isolation_provider else "")


def _isolation_budget_problem(job: Job | None, src_s: float, provider: str) -> str | None:
    """Why paid isolation would break the tier's budget (:data:`ISOLATION_BUDGET_USD`), else None. With a job: the
    API spend already in its trace + the isolation's price + a reserve for :data:`_QA_RENDERS_RESERVED` QA ASR round
    trips (at most the source's length each); an isolation already paid for this job is cached and costs nothing
    again. Without a job: only sources up to :data:`UNMETERED_ISOLATION_MAX_S`."""
    from studio.agent.cost import ASR_PRICES_PER_HOUR, ISOLATION_PRICES_PER_MIN, job_cost

    if job is None:
        if src_s > UNMETERED_ISOLATION_MAX_S:
            return f"budget: source longer than {UNMETERED_ISOLATION_MAX_S:.0f} s and no job to meter the spend"
        return None
    try:
        trace = job.read_trace()
        spent = job_cost(trace).total_usd
        paid = any(e.get("event") == "audio_isolation" for e in trace)
    except Exception:  # unreadable accounting: the conservative length rule decides
        return _isolation_budget_problem(None, src_s, provider)
    iso = 0.0 if paid else src_s / 60.0 * ISOLATION_PRICES_PER_MIN.get(provider, max(ISOLATION_PRICES_PER_MIN.values()))
    reserve = _QA_RENDERS_RESERVED * src_s / 3600.0 * max(ASR_PRICES_PER_HOUR.values())
    if spent + iso + reserve > ISOLATION_BUDGET_USD + 1e-9:
        return (f"budget: ${spent:.3f} spent + ${iso:.3f} isolation + ${reserve:.3f} QA reserve > "
                f"${ISOLATION_BUDGET_USD:.2f}")
    return None


def _color_op(sc: _Scratch, consts: dict[str, Any]) -> str:
    """``set_color`` on a measured exposure fault only. Skin tone is unknown, so the limits are the ones no skin tone
    explains: a face under the darkest band's floor is underexposed, one over the lit-skin ceiling overexposed; the
    correction is the doctrine's single step (``color.exposure_change_stops``)."""
    job = sc.job
    if job is None:
        return "not measured (no job)"
    if not job.mezz_path.exists():
        return "not measured (no mezzanine)"
    from studio.compile.color import measure_color

    try:
        m = measure_color(job, sc.ix, sc.doc)
    except Exception as e:  # no words, a decode problem: the look stays untouched
        return f"not measured ({type(e).__name__})"
    faces = [f.face for f in m.frames if f.face is not None]
    if not m.frames or not faces:
        return "measured: no face, no correction"
    face_pct = statistics.median(f.y_mean for f in faces) / 255.0 * 100.0
    lo = min(band[0] for band in _prior(consts, "color.face_luma_sdr_pct").values())
    hi = _prior(consts, "color.lit_skin_soft_ceiling_pct")
    step = _prior(consts, "color.exposure_change_stops")
    if face_pct < lo:
        ev, why = step, f"underexposed: face Y' {face_pct:.0f}% < {lo:.0f}% (the darkest skin band's floor)"
    elif face_pct > hi:
        ev, why = -step, f"overexposed: face Y' {face_pct:.0f}% > {hi:.0f}% (the lit-skin ceiling)"
    else:
        return f"measured: face Y' {face_pct:.0f}% (within {lo:.0f}-{hi:.0f}%): no fault"
    if not sc.push({"op": "set_color", "spec": {"exposure": round(ev, 2)}}):
        return f"fault not corrected ({why})"
    return f"exposure {ev:+.2f} EV ({why})"


# ============================================================================================ public API
def code_plan(index: TakeIndex, *, platform: str, decision: PlanDecision | None, settings: Settings | None = None,
              brief: str | None = None, style: str | None = None, job: Job | None = None) -> list[dict[str, Any]]:
    """The ordered op list for a complete document from ``index`` on ``platform`` (see the module docstring).

    ``decision`` is the planner's answer or None for the pure-code plan; unknown IDs in it are skipped with a note,
    never fatal. ``job`` is optional: with it the compile sees the job's media (active area, dense visual track), the
    voice chain is measured on ``audio.wav`` and the colour on the mezzanine (each skipped without). Applying the list
    with :func:`studio.doc.ops.apply_ops` to a fresh document (:func:`studio.doc.model.new_document`) yields the plan;
    :func:`plan_document` does that through an :class:`~studio.agent.tools.EditSession`. Raises
    :class:`CodePlanError` when nothing can be kept.
    """
    ix = index
    if not ix.words:
        raise CodePlanError("the take has no words")
    consts = _constants(settings)
    band = energy_band(ix.energy.overall)
    st = _story(ix, decision, consts)
    runs = _runs(st.keep, ix)
    if not runs:
        raise CodePlanError("every word of the take is removed by the plan (retakes, abandoned lines, cut-offs)")
    sc = _Scratch(ix, platform, job)
    sc.notes.extend(st.notes)
    b, s = _brief_and_style(ix, decision, st, brief=brief, style=style, consts=consts, band=band)
    sc.push({"op": "set_brief", "brief": b.model_dump(mode="json")})
    sc.push({"op": "set_style", "style": s.model_dump(mode="json")})
    sc.push({"op": "set_deliverables", "deliverables": [{"platform": platform, "variant": "main"}]},
            keep_no_change=True)
    story_op = {"op": "set_story",
                "segments": [{"from_word": ix.words[a].id, "to_word": ix.words[z].id} for a, z in runs],
                "removed": _removed_ranges(st, ix), "reason": "not selected by the code plan"}
    if not sc.push(story_op):
        raise CodePlanError("the story op was rejected: " + (sc.notes[-1] if sc.notes else "unknown reason"))
    n_fillers = _filler_ops(sc, decision, consts)
    n_pauses = _pause_ops(sc, decision, consts, band)
    n_pins = _pin_ops(sc, decision)
    punched = _framing_op(sc, decision, consts)
    n_marks = _marker_ops(sc, decision)
    size, n_pages = _caption_ops(sc, decision, platform, settings, consts)
    voice = _voice_op(sc)
    colour = _color_op(sc, consts)
    kept = sum(1 for i, w in enumerate(ix.words) if st.keep[i] and w.is_spoken)
    spoken = sum(1 for w in ix.words if w.is_spoken)
    takes = ", ".join(f"{c}: {sid}" for c, sid in st.takes.items()) or "none"
    summary = (f"code plan ({'planner decision' if decision is not None else 'no decision'}; {band} energy): kept "
               f"{kept}/{spoken} spoken words in {len(runs)} segment(s); retake clusters {takes}; {n_fillers} "
               f"filler(s) cut; {n_pauses} pause(s) tightened; {n_pins} word(s) pinned; punch-in "
               f"{'yes' if punched else 'no'}; captions {f'{size} px, {n_pages} pages' if size else 'none'}; "
               f"{n_marks} label(s); voice {voice}; colour {colour}; music/SFX/b-roll none (standard tier)")
    if sc.notes:
        summary += "; notes: " + " | ".join(sc.notes[:12])
    sc.push({"op": "note", "text": summary[:1800]})
    return sc.ops


def story_speech_s(index: TakeIndex, *, decision: PlanDecision | None = None,
                   settings: Settings | None = None) -> float:
    """Seconds of speech in the story the code plan keeps (kept spoken words only, no pauses): a floor under the
    output length, cheap enough to check before the planner is paid (the pipeline refuses a take whose floor, after
    the most the planner may drop, is still over the tier's output limit)."""
    st = _story(index, decision, _constants(settings))
    return sum(w.end_us - w.start_us for i, w in enumerate(index.words) if st.keep[i] and w.is_spoken) / 1e6


def radio_check(doc: CutDocument, index: TakeIndex, *, raise_on_block: bool = False) -> list[str]:
    """The listener's gate on a planned story: :func:`studio.doc.validate.radio_blockers`, returned (empty = passes)
    or raised as :class:`RadioBlocked` when ``raise_on_block``."""
    blockers = radio_blockers(doc, index)
    if blockers and raise_on_block:
        raise RadioBlocked(blockers)
    return blockers


def plan_document(job: Job, index: TakeIndex, platform: str, decision: PlanDecision | None, *,
                  settings: Settings | None = None, brief: str | None = None, style: str | None = None,
                  by: str = PLAN_BY, check_radio: bool = True) -> CutDocument:
    """Plan and apply: :func:`code_plan` applied through an :class:`~studio.agent.tools.EditSession` so the versions
    (``doc/v{n}.json``) and the oplog are written like the Director's, then the radio gate.

    The ops address IDs they create themselves (segments, caption pages), so they are applied to a fresh document,
    never on top of an earlier version (a re-plan, e.g. the repair's pure-code second attempt, replaces the whole
    document); its parent is the job's latest saved version, so ``undo`` returns to it. Returns the new document.
    Rejected ops (none expected: the list was pre-applied) are traced as ``rejected`` on the ``code_plan`` event;
    raises :class:`CodePlanError` when no story could be built and :class:`RadioBlocked` when ``check_radio`` and the
    story fails the listener's gate.
    """
    from studio.agent.tools import EditSession

    ops = code_plan(index, platform=platform, decision=decision, settings=settings, brief=brief, style=style, job=job)
    base = new_document(job.id, created_by=by, platforms=(platform,))  # type: ignore[arg-type]
    latest = job.latest_doc_version()
    if latest is not None:
        base = base.model_copy(update={"version": latest})
    session = EditSession(job, index, base, settings=settings, by=by, platforms=(platform,))
    outcome = session.apply_ops(ops, by=by)
    rejected = [(r.op, r.reason) for r in outcome.results if not r.applied and r.reason != "no change"]
    job.trace("code_plan", platform=platform, decision=decision is not None, ops=len(ops),
              applied=outcome.applied, rejected=rejected[:10], version=outcome.doc.version)
    if not outcome.doc.segments:
        raise CodePlanError("the planned document has no story: " + "; ".join(f"{o}: {r}" for o, r in rejected[:3]))
    if check_radio:
        radio_check(outcome.doc, index, raise_on_block=True)
    return outcome.doc
