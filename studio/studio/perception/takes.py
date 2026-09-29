"""Sentences, completeness and retake clusters (ARCHITECTURE §4).

:func:`segment_sentences` splits the verbatim word stream into sentences ``s001``… (contiguous word
runs, one speaker each) and marks ``complete=False`` only on **evidence of abandonment**;
:func:`cluster_takes` groups retakes / false starts / rephrasings / pickups of the same line into
clusters ``c01``… with ``recommended_sentence_id`` = the last complete full delivery by default.
Everything is deterministic text + timing analysis (rapidfuzz); models never set these fields, and the
Director overrides the recommendation on delivery (``choose_take``).

Doctrine this encodes (``skills/editing/cutting-and-pacing.md``)
----------------------------------------------------------------
* "A false start needs a later re-delivery; a spoken payoff or CTA is never a false start." So a
  sentence is only marked incomplete when it ends in a cut-off word, is interrupted by a dash *and*
  something confirms the interruption, or is the abandoned opening of a restart. A final line without a
  full stop (the take just ends) stays complete.
* "A repeat is deliberate (keep) when it adds emphasis ('Two weeks. Two weeks.', a triplet, a
  callback). It is a retake when the earlier attempt is incomplete, corrected or weaker and the same
  clause restarts." Short verbatim repeats of complete sentences are therefore *not* clustered, a repeat
  must extend the line (or follow a cut-off) to count as an in-sentence restart, and parallel
  structure ("They buy the outcome they want. They buy the identity they want.") is rejected by a
  content-word similarity guard.

Segmentation
------------
1. Hard boundaries: terminal punctuation (``. ? ! …`` — abbreviations like "Dr." and mid-sentence
   ellipses excluded), speaker change, and silences ≥ ``hard_pause_ms`` (2 s: a reset, not a beat).
2. Inside each run, split points (earliest first, repeatedly):
   * an interrupted word (cut-off ``restr-`` or em-dash ``months—``) whose continuation *confirms* the
     interruption: it restarts the current line ("The real secret is restr- the real secret is…"),
     re-says the start or the tail of the previous sentence (the "months— people will buy." pickup),
     starts a capitalised new clause after a pause, or (dash only) follows a ≥500 ms silence. Without
     confirmation a cut-off is a stutter inside the sentence ("we need to b- build it") and stays;
   * a restart of the sentence opening without any dash ("So founders spend months so founders spend
     months worshipping…"): ≥2 repeated words that are then extended; if the first attempt ran on past
     the repeated words, a real reset (≥600 ms pause or a filler) is also required so anaphora ("we
     fight on the beaches, we fight on the landing grounds") is not split.
   Fillers between an abandoned attempt and its restart stay with the abandoned part (they leave with
   it). Pieces holding only fillers/cut-offs are merged into the next sentence (a stutter or "Um."
   is part of the line that follows). Audio events attach to the nearer neighbouring sentence.

Clustering (sentence pairs, same speaker; union-find)
----------------------------------------------------
* ``retake`` — near-verbatim re-delivery within ``max_distance`` (8) sentences (char similarity ≥0.85,
  content-word similarity ≥0.75, comparable length). Short (≤3-word) complete repeats are skipped
  (deliberate emphasis). Farther apart (a whole script recorded twice) only long lines (≥6 words) at
  ≥0.9 / ≥0.85 similarity count, and only when a neighbouring line (±2) is re-delivered at the same
  offset — a lone distant repeat is a callback or loop bookend and is never clustered (that would
  put the hook up for deletion).
* ``false_start`` — an incomplete earlier sentence whose words are the opening of the *nearest* later
  one (cut-off stems match: ``restr-`` ~ ``restraint``), or an unpunctuated fragment (e.g. split off by
  a long silence) that is exactly the opening of a nearby longer line; the fragment is then marked
  ``complete=False`` — its re-delivery is the evidence.
* ``extension`` — a complete sentence repeated verbatim as the opening of the very next one ("Most people
  fail. Most people fail because they quit."): retake or deliberate build-up; notes say which to check.
* ``abandoned_retake`` — a later incomplete attempt at a line already delivered in full.
* ``pickup`` — a later short sentence that re-says only the *tail* of an earlier one, with an
  incomplete sentence in between (the speaker backed up for a running start). The full delivery stays
  recommended; the notes give the word-ID splice to use the pickup's delivery instead (``choose_take``
  on the pickup would drop the rest of the line).
* ``rephrase`` — adjacent (≤3 apart) moderately similar lines (char ≥0.62, content-word ≥0.67) with a
  reset signal (earlier incomplete, ≥0.9 s pause before the later one, or char and content ≥0.75).
  Lower confidence. The content threshold sits between measured parallel/antithesis pairs ("People
  never start because they fear failure." / "… never finish because they fear success.": 0.60) and
  restatements ("The problem is that people never start." / "The real problem is that most people
  never start at all.": 0.80).
Recommended take: the last member that is complete *and* a full delivery (≥75 % of the longest
member's words and not the short side of a partial relation); else the last complete; else the last full.
Consecutive clusters that pair up sentence-by-sentence (A1 B1 A2 B2) are flagged as one re-delivered
passage so the Director can keep a whole pass for continuity.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import TYPE_CHECKING

from rapidfuzz import fuzz
from rapidfuzz.distance import Indel

from studio.perception.index import Cluster, Sentence, Word, cluster_id, sentence_id
from studio.perception.transcribe import ends_with_dash, is_cutoff_token, normalize_token

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings

__all__ = ["TakeParams", "segment_sentences", "cluster_takes", "analyze_takes", "line_similarity"]


@dataclass(frozen=True)
class TakeParams:
    """Perception thresholds (measurement heuristics, not craft priors)."""

    hard_pause_ms: float = 2000.0  # silence that always ends a sentence
    ellipsis_pause_ms: float = 700.0  # "…" ends a sentence only before a capital or this long a pause
    dash_pause_ms: float = 250.0  # pause after a dash/cut-off that, with a capitalised next word, confirms a break
    dash_long_pause_ms: float = 500.0  # pause after an em-dash that alone confirms the interruption
    restart_pause_ms: float = 600.0  # reset pause required when a restart's first attempt ran on
    long_restart_pause_ms: float = 1000.0  # … and when it ran on for more than 3 extra words
    reset_pause_ms: float = 900.0  # pause before a line that signals a retake (rephrase evidence)
    max_distance: int = 8  # sentences apart for verbatim retakes
    far_min_words: int = 6  # beyond max_distance only long, near-identical lines count (whole-script retakes)
    far_sim: float = 0.9
    far_content_sim: float = 0.85
    near_distance: int = 3  # sentences apart for partial/rephrase relations
    verbatim_sim: float = 0.85
    verbatim_content_sim: float = 0.75
    verbatim_len_ratio: float = 0.7
    rephrase_sim: float = 0.62
    rephrase_content_sim: float = 0.67  # parallel/antithesis lines measure ≤0.6, restatements ≥0.72
    rephrase_len_ratio: float = 0.6
    rephrase_min_words: int = 4
    prefix_match: float = 0.75  # share of a false start's words that must match the retake's opening
    short_repeat_words: int = 3  # complete verbatim repeats this short are deliberate (not clustered)
    full_coverage: float = 0.75


# ---------------------------------------------------------------------------------------------- lexicon
_STOPWORDS = frozenset("""
a an the and or but so to of in on at for with from by as if then than that this these those there here
is are was were be been being am do does did doing have has had having will would can could should may might
must shall i me my mine we us our ours you your yours he him his she her hers it its they them their theirs
what which who whom whose how when where why not no just also very really too all any some such only own
same other into onto out up down over about again once because while until
""".split())  # noqa: SIM905
_CONJ = frozenset({"and", "or", "but", "so", "because", "then", "that", "if", "when", "while", "as", "to", "of"})
_TITLE_ABBR = frozenset({"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "gen", "sen", "rep", "gov", "vs"})
_OTHER_ABBR = frozenset({"etc", "inc", "ltd", "co", "corp", "e.g", "i.e", "a.m", "p.m", "u.s", "u.k", "approx",
                         "no", "fig", "vol"})
_TERMINAL_RE = re.compile(r"[.!?。！？]+$")
_ELLIPSIS_RE = re.compile(r"(\.\.\.|…)$")
_CLOSERS = "\"'”’)]}»"


def _strip(text: str) -> str:
    t = (text or "").strip()
    while t and t[-1] in _CLOSERS:
        t = t[:-1]
    return t


def _terminal(text: str) -> bool:
    return bool(_TERMINAL_RE.search(_strip(text))) and not _ELLIPSIS_RE.search(_strip(text))


def _starts_capital(text: str) -> bool:
    t = (text or "").lstrip("\"'“‘([{¿¡")
    if not t or not t[0].isalpha() or not t[0].isupper():
        return False
    n = normalize_token(t)
    return not (n == "i" or n.startswith(("i'", "i’")))


def _stem(tok: str) -> str:
    t = tok[:-2] if tok.endswith("'s") else tok
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    return t


# ---------------------------------------------------------------------------------------------- tokens
@dataclass(frozen=True)
class _Lex:
    """A lexical (non-filler, non-event) token."""

    pos: int  # index in the word list
    norm: str
    cutoff: bool = False  # partial word ("restr-")
    dash: bool = False  # utterance interrupted after this word ("months—")

    @property
    def interrupted(self) -> bool:
        return self.cutoff or self.dash


def _lex_of(w: Word, pos: int) -> _Lex | None:
    if w.kind in ("event", "filler"):
        return None
    n = normalize_token(w.text)
    if not n:
        return None
    # a word a dropout cut at its *start* ("-nough.") was still delivered to its end: it is not an interruption
    cut = (w.kind == "cutoff" and w.truncated != "start") or is_cutoff_token(w.text)
    return _Lex(pos=pos, norm=n, cutoff=cut, dash=ends_with_dash(w.text))


def _eq(a: _Lex, b: _Lex) -> bool:
    """Token equality where a cut-off matches any word it is a prefix of (``restr-`` ~ ``restraint``)."""
    if a.cutoff and b.cutoff:
        return a.norm.startswith(b.norm) or b.norm.startswith(a.norm)
    if a.cutoff:
        return bool(a.norm) and b.norm.startswith(a.norm)
    if b.cutoff:
        return bool(b.norm) and a.norm.startswith(b.norm)
    return a.norm == b.norm


def _common_prefix(a: Sequence[_Lex], b: Sequence[_Lex]) -> int:
    k = 0
    while k < len(a) and k < len(b) and _eq(a[k], b[k]):
        k += 1
    return k


def _tail_match(prev: Sequence[_Lex], after: Sequence[_Lex], min_len: int = 2) -> int:
    """Largest k ≥ min_len with ``after[:k]`` == the last k tokens of ``prev`` (0 if none)."""
    for k in range(min(len(prev), len(after)), min_len - 1, -1):
        if all(_eq(prev[len(prev) - k + q], after[q]) for q in range(k)):
            return k
    return 0


def line_similarity(a: Sequence[str], b: Sequence[str]) -> tuple[float, float]:
    """``(char_similarity, content_similarity)`` of two normalized token lists, both 0..1."""
    if not a or not b:
        return 0.0, 0.0
    char = fuzz.ratio(" ".join(a), " ".join(b)) / 100.0
    ca = [_stem(t) for t in a if t not in _STOPWORDS]
    cb = [_stem(t) for t in b if t not in _STOPWORDS]
    if len(ca) < 2 or len(cb) < 2:
        content = Indel.normalized_similarity([_stem(t) for t in a], [_stem(t) for t in b])
    else:
        content = Indel.normalized_similarity(ca, cb)
    return float(char), float(content)


# ---------------------------------------------------------------------------------------------- segmentation
def _is_sentence_end(w: Word, nxt: Word, pause_us: int, p: TakeParams) -> bool:
    t = _strip(w.text)
    if _ELLIPSIS_RE.search(t):
        return _starts_capital(nxt.text) or pause_us >= p.ellipsis_pause_ms * 1000
    if not _terminal(t):
        return False
    if t.endswith("."):
        n = normalize_token(t)
        if n in _TITLE_ABBR:
            return False
        if n in _OTHER_ABBR or (len(n) == 1 and n.isalpha() and n != "i"):
            return _starts_capital(nxt.text) and pause_us >= 150_000
    return True


def _pause_after(ws: Sequence[Word], run: Sequence[int], idx_in_run: int) -> int:
    """Silence between run[idx] and the next spoken word of the run (µs; 0 at the end)."""
    if idx_in_run + 1 >= len(run):
        return 0
    return max(0, ws[run[idx_in_run + 1]].start_us - ws[run[idx_in_run]].end_us)


def _find_split(run: list[int], ws: Sequence[Word], prev: list[_Lex], p: TakeParams) -> int | None:
    """Index into ``run`` where a new sentence starts (the earliest confirmed break), or None."""
    lex = [x for x in (_lex_of(ws[i], i) for i in run) if x is not None]
    if len(lex) < 2:
        return None
    where = {i: k for k, i in enumerate(run)}
    best: int | None = None

    # (i) interrupted words (cut-off / em-dash) whose continuation confirms the interruption
    for q in range(len(lex) - 1):
        cur = lex[q]
        if not cur.interrupted:
            continue
        nxt = lex[q + 1]
        pause = _pause_after(ws, run, where[cur.pos])
        first, after = lex[: q + 1], lex[q + 1:]
        confirmed = (
            _common_prefix(first, after) >= 1
            or _common_prefix(prev, after) >= 2
            or _tail_match(prev, after) >= 2
            or (_starts_capital(ws[nxt.pos].text) and pause >= p.dash_pause_ms * 1000)
            or (ends_with_dash(ws[cur.pos].text) and pause >= p.dash_long_pause_ms * 1000)
        )
        if confirmed:
            best = where[nxt.pos]
            break

    # (ii) restart of the sentence opening without a dash
    for j in range(1, len(lex)):
        if best is not None and where[lex[j].pos] >= best:
            break
        first, again = lex[:j], lex[j:]
        k = _common_prefix(first, again)
        if k < 2:
            continue
        last = first[-1]
        extends = len(again) > k
        interrupted = last.interrupted
        if not (extends or interrupted):
            continue  # "buy it now, buy it now!" — a deliberate repeat
        a, b = where[last.pos], where[again[0].pos]
        pause = max(0, ws[run[a + 1]].start_us - ws[last.pos].end_us) if a + 1 < len(run) else 0
        filler_between = any(ws[run[x]].kind == "filler" for x in range(a + 1, b))
        extra = j - k  # words of the first attempt beyond the repeated opening
        if extra == 0:
            ok = last.norm not in _CONJ or interrupted or filler_between or pause >= p.restart_pause_ms * 1000
        elif extra <= 3:
            ok = interrupted or filler_between or pause >= p.restart_pause_ms * 1000
        else:
            ok = interrupted or (k >= 3 and pause >= p.long_restart_pause_ms * 1000)
        if ok:
            best = b
            break

    # (iii) a phrase restarted mid-sentence after a reset ("One, do the two cuisines, [1.3 s] do the two cuisines
    # share …"): split off what precedes the abandoned attempt (it is delivered, so it stays complete); rule (ii)
    # then separates the attempt from its restart on the next pass
    if best is None:
        for j in range(2, len(lex)):
            first, again = lex[:j], lex[j:]
            k = _tail_match(first, again, min_len=3)
            if k < 3 or len(again) <= k or len(first) <= k:
                continue
            rep = first[len(first) - k:]
            if all(x.norm in _STOPWORDS for x in rep):
                continue
            a_last, b_first = first[-1], again[0]
            pause = max(0, ws[b_first.pos].start_us - ws[a_last.pos].end_us)
            filler_between = any(ws[run[x]].kind == "filler" for x in range(where[a_last.pos] + 1, where[b_first.pos]))
            if pause >= p.restart_pause_ms * 1000 or filler_between or a_last.interrupted:
                return (where[rep[0].pos], True)  # type: ignore[return-value]
    return best


def _audible_pause(a_us: int, b_us: int, dropouts: Sequence[tuple[int, int]]) -> int:
    """``b - a`` minus the recording dropouts (digital silence) inside it: a lost stretch is not a pause."""
    lost = sum(max(0, min(b_us, e) - max(a_us, s)) for s, e in dropouts)
    return max(0, b_us - a_us - lost)


def segment_sentences(words: list[Word], *, params: TakeParams | None = None,
                      dropouts: Sequence[tuple[int, int]] = ()) -> tuple[list[Word], list[Sentence]]:
    """Split words into sentences; returns words with ``sentence_id`` set (``cluster_id`` cleared), and the
    sentences (``complete=False`` only on evidence of abandonment; see module docstring). ``dropouts``
    (digital-silence runs, source µs) never count as pause time, so a dropout alone never ends a sentence."""
    p = params or TakeParams()
    ws = [w.model_copy(update={"sentence_id": None, "cluster_id": None}) for w in words]
    spoken = [i for i, w in enumerate(ws) if w.kind != "event"]
    if not spoken:
        return ws, []

    # 1. hard boundaries
    runs: list[list[int]] = []
    cur = [spoken[0]]
    for a, b in pairwise(spoken):
        wa, wb = ws[a], ws[b]
        pause = _audible_pause(wa.end_us, wb.start_us, dropouts) if dropouts else wb.start_us - wa.end_us
        if ((wa.speaker is not None and wb.speaker is not None and wa.speaker != wb.speaker)
                or pause >= p.hard_pause_ms * 1000 or _is_sentence_end(wa, wb, pause, p)):
            runs.append(cur)
            cur = [b]
        else:
            cur.append(b)
    runs.append(cur)

    # 2. confirmed interruptions / restarts inside each run
    pieces: list[tuple[list[int], bool]] = []
    prev: list[_Lex] = []
    for run in runs:
        rest = run
        while True:
            found = _find_split(rest, ws, prev, p)
            head_complete = False
            if isinstance(found, tuple):
                found, head_complete = found
            cut = found
            if cut is None or cut <= 0:
                break
            head = rest[:cut]
            pieces.append((head, head_complete))
            prev = [x for x in (_lex_of(ws[i], i) for i in head) if x is not None]
            rest = rest[cut:]
        lex = [x for x in (_lex_of(ws[i], i) for i in rest) if x is not None]
        complete = not (lex and lex[-1].interrupted)
        pieces.append((rest, complete))
        prev = lex

    # 3. merge pieces without a full word (stutters, lone fillers) into the following line
    merged: list[tuple[list[int], bool]] = []
    carry: list[int] = []
    for k, (pos, complete) in enumerate(pieces):
        has_word = any(ws[i].kind == "word" and not is_cutoff_token(ws[i].text) and normalize_token(ws[i].text)
                       for i in pos)
        nxt_same_speaker = k + 1 < len(pieces) and ws[pieces[k + 1][0][0]].speaker == ws[pos[-1]].speaker
        if not has_word and nxt_same_speaker:
            carry.extend(pos)
            continue
        if not has_word and merged and ws[merged[-1][0][-1]].speaker == ws[pos[0]].speaker:
            merged[-1] = (merged[-1][0] + carry + pos, merged[-1][1])
            carry = []
            continue
        merged.append((carry + pos, complete))
        carry = []
    if carry:
        if merged:
            merged[-1] = (merged[-1][0] + carry, merged[-1][1])
        else:
            merged.append((carry, True))

    # 4. attach audio events to the nearer neighbouring sentence (keeps sentences contiguous)
    owner: dict[int, int] = {}
    for si, (pos, _) in enumerate(merged):
        for i in pos:
            owner[i] = si
    firsts = [pos[0] for pos, _ in merged]
    lasts = [pos[-1] for pos, _ in merged]
    events = [i for i, w in enumerate(ws) if w.kind == "event"]
    for e in events:
        # sentence whose span of word positions contains e, else the nearer of prev/next
        prev_si = max((si for si in range(len(merged)) if firsts[si] < e), default=None)
        next_si = min((si for si in range(len(merged)) if firsts[si] > e), default=None)
        if prev_si is not None and lasts[prev_si] > e:
            owner[e] = prev_si
            continue
        if prev_si is None:
            owner[e] = next_si  # type: ignore[assignment]
            continue
        if next_si is None:
            owner[e] = prev_si
            continue
        ev = ws[e]
        d_prev = ev.start_us - ws[lasts[prev_si]].end_us
        d_next = ws[firsts[next_si]].start_us - ev.end_us
        owner[e] = prev_si if d_prev <= d_next else next_si
    # enforce contiguity: an event may only join the next sentence if all later events do too
    for si in range(len(merged) - 1):
        between = [e for e in events if lasts[si] < e < firsts[si + 1]]
        switched = False
        for e in between:
            if owner[e] == si + 1:
                switched = True
            elif switched:
                owner[e] = si + 1

    groups: list[list[int]] = [[] for _ in merged]
    for i in sorted(owner):
        groups[owner[i]].append(i)

    sentences: list[Sentence] = []
    out = list(ws)
    for si, idxs in enumerate(groups):
        sid = sentence_id(si + 1)
        idxs = sorted(idxs)
        for i in idxs:
            out[i] = out[i].model_copy(update={"sentence_id": sid})
        spk = [out[i].speaker for i in idxs if out[i].kind != "event" and out[i].speaker is not None]
        speaker = max(set(spk), key=lambda s: (spk.count(s), -spk.index(s))) if spk else None
        sentences.append(Sentence(
            id=sid, word_ids=[out[i].id for i in idxs], text=" ".join(out[i].text for i in idxs),
            start_us=min(out[i].start_us for i in idxs), end_us=max(out[i].end_us for i in idxs),
            complete=merged[si][1], cluster_id=None, speaker=speaker,
        ))
    return out, sentences


# ---------------------------------------------------------------------------------------------- clustering
@dataclass
class _SInfo:
    idx: int
    s: Sentence
    lex: list[_Lex]  # incl. cut-offs
    core: list[str]  # normalized, excl. cut-offs
    terminal: bool
    reset_us: int  # silence before the sentence's first spoken word
    fillers: int
    roles: set[str] = field(default_factory=set)


@dataclass
class _Edge:
    a: int
    b: int
    kind: str
    sim: float
    short: int | None = None  # index of the partial side
    detail: str = ""


def _prefix_ratio(short: Sequence[_Lex], long: Sequence[_Lex]) -> tuple[int, float]:
    """Position-wise matches of ``short`` against the opening of ``long`` (count, share)."""
    m = len(short)
    if m == 0 or len(long) < m:
        return 0, 0.0
    hits = sum(1 for q in range(m) if _eq(short[q], long[q]))
    return hits, hits / m


def _relate(A: _SInfo, B: _SInfo, dist: int, between: Sequence[_SInfo], p: TakeParams) -> _Edge | None:
    """Relation of an earlier sentence A to a later sentence B (or None)."""
    la, lb = len(A.lex), len(B.lex)
    if la == 0 or lb == 0:
        return None
    ca, cb = len(A.core), len(B.core)
    ratio = min(ca, cb) / max(ca, cb) if max(ca, cb) else 0.0
    if dist > p.max_distance:
        # whole-script re-deliveries: only long, near-identical lines (cheap length checks first)
        if min(ca, cb) < p.far_min_words or ratio < p.verbatim_len_ratio:
            return None
        sa, sb = set(A.core), set(B.core)
        if len(sa & sb) < 0.75 * min(len(sa), len(sb)):
            return None  # cheap vocabulary pre-filter before the edit-distance measures
        char, content = line_similarity(A.core, B.core)
        if char >= p.far_sim and content >= p.far_content_sim:
            return _Edge(A.idx, B.idx, "retake", round(char, 3), detail="far")
        return None
    char, content = line_similarity(A.core, B.core)

    # an unpunctuated line that is exactly the opening of a nearby longer one was abandoned and re-delivered
    if dist <= p.near_distance and 2 <= la < lb and not A.terminal and not A.lex[-1].interrupted:
        hits, _ = _prefix_ratio(A.lex, B.lex)
        if hits == la:
            return _Edge(A.idx, B.idx, "false_start", 1.0, short=A.idx)

    # near-verbatim retake of the whole line
    if (dist <= p.max_distance and ratio >= p.verbatim_len_ratio and char >= p.verbatim_sim
            and content >= p.verbatim_content_sim):
        if min(ca, cb) <= p.short_repeat_words and A.s.complete:
            return None  # "Two weeks. Two weeks." — deliberate emphasis
        return _Edge(A.idx, B.idx, "retake", round(char, 3))

    if dist > p.near_distance:
        return None

    # A is the abandoned opening of B (false start), or a complete line B repeats and extends
    interrupted_a = A.lex[-1].interrupted
    if la <= lb and (la < lb or interrupted_a):
        hits, share = _prefix_ratio(A.lex, B.lex)
        opening_ok = _eq(A.lex[0], B.lex[0]) and (la == 1 or _eq(A.lex[1], B.lex[1]))
        if opening_ok:
            if not A.s.complete and share >= p.prefix_match and (la >= 2 or (interrupted_a and dist <= 2)):
                return _Edge(A.idx, B.idx, "false_start", round(share, 3), short=A.idx)
            if A.s.complete and A.terminal and dist == 1 and la >= 3 and hits == la and lb > la:
                return _Edge(A.idx, B.idx, "extension", 1.0, short=A.idx)

    # B is a later abandoned attempt at a line A already delivered
    interrupted_b = B.lex[-1].interrupted
    if not B.s.complete and lb <= la and (lb >= 2 or interrupted_b):
        hits, share = _prefix_ratio(B.lex, A.lex)
        if lb >= 2 and share >= p.prefix_match and _eq(A.lex[0], B.lex[0]):
            return _Edge(A.idx, B.idx, "abandoned_retake", round(share, 3), short=B.idx)

    # B re-says only the tail of A, right after an abandoned sentence (running start into a retake). A line
    # that trails off ("So founders spend months..." then a pause) is abandoned even though the segmenter
    # closed it as a sentence: only a terminal full stop counts as finished here.
    if 2 <= lb < la and any(not x.s.complete or not x.terminal for x in between):
        k = _tail_match(A.lex, B.lex)
        if k == lb and not _eq(A.lex[0], B.lex[0]):
            return _Edge(A.idx, B.idx, "pickup", 1.0, short=B.idx)

    # rephrased retake (lower confidence)
    if (min(ca, cb) >= p.rephrase_min_words and ratio >= p.rephrase_len_ratio and char >= p.rephrase_sim
            and content >= p.rephrase_content_sim):
        reset = (not A.s.complete) or B.reset_us >= p.reset_pause_ms * 1000 or (char >= 0.75 and content >= 0.75)
        if reset:
            return _Edge(A.idx, B.idx, "rephrase", round(char, 3))
    return None


class _DSU:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _fmt_s(us: int) -> str:
    return f"{us / 1e6:.1f}s"


def cluster_takes(
    words: list[Word],
    sentences: list[Sentence],
    *,
    settings: Settings | None = None,
    params: TakeParams | None = None,
) -> tuple[list[Word], list[Sentence], list[Cluster]]:
    """Group retakes; returns words/sentences with ``cluster_id`` set (and ``complete`` refined for false
    starts), and the clusters in time order. ``settings`` is accepted for interface symmetry (unused)."""
    del settings
    p = params or TakeParams()
    pos = {w.id: i for i, w in enumerate(words)}
    infos: list[_SInfo] = []
    for k, s in enumerate(sentences):
        idxs = [pos[w] for w in s.word_ids if w in pos]
        lex = [x for x in (_lex_of(words[i], i) for i in idxs) if x is not None]
        spoken = [i for i in idxs if words[i].kind != "event"]
        reset = 0
        if spoken:
            first = spoken[0]
            before = [j for j in range(first - 1, -1, -1) if words[j].kind != "event"]
            reset = words[first].start_us - words[before[0]].end_us if before else words[first].start_us
        last_text = words[spoken[-1]].text if spoken else ""
        infos.append(_SInfo(
            idx=k, s=s, lex=lex, core=[x.norm for x in lex if not x.cutoff], terminal=_terminal(last_text),
            reset_us=max(0, reset), fillers=sum(1 for i in idxs if words[i].kind == "filler"),
        ))

    # candidate relations
    edges: list[_Edge] = []
    n = len(infos)
    for i in range(n):
        A = infos[i]
        ca = len(A.core)
        for j in range(i + 1, n):
            B = infos[j]
            if A.s.speaker is not None and B.s.speaker is not None and A.s.speaker != B.s.speaker:
                continue
            dist = j - i
            if dist > p.max_distance:  # cheap gate for the (rare) whole-script retake check
                cb = len(B.core)
                if min(ca, cb) < p.far_min_words or min(ca, cb) < p.verbatim_len_ratio * max(ca, cb):
                    continue
            e = _relate(A, B, dist, infos[i + 1:j] if dist <= p.near_distance else (), p)
            if e is not None:
                edges.append(e)
    # a distant verbatim repeat counts only as part of a second pass: another line within ±2 sentences must
    # be re-delivered at the same offset (a lone far repeat is a callback/bookend — never cluster the hook)
    retakes = [e for e in edges if e.kind == "retake"]
    edges = [e for e in edges if e.detail != "far" or any(
        o is not e and (o.b - o.a) == (e.b - e.a) and abs(o.a - e.a) <= 2 for o in retakes)]
    # a partial side links only to its nearest counterpart (a false start re-delivered twice must not
    # chain two different lines together)
    kept: list[_Edge] = []
    seen: set[tuple[int, str]] = set()
    for e in sorted(edges, key=lambda e: (abs(e.b - e.a), e.a, e.b)):
        if e.short is not None:
            key = (e.short, "partial")
            if key in seen:
                continue
            seen.add(key)
        kept.append(e)
    kept.sort(key=lambda e: (e.a, e.b))

    dsu = _DSU(n)
    for e in kept:
        dsu.union(e.a, e.b)
        if e.short is not None:
            infos[e.short].roles.add(e.kind)
    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(dsu.find(i), []).append(i)
    groups = sorted((sorted(m) for m in comps.values() if len(m) > 1), key=lambda m: m[0])

    # a false start is incomplete by definition (its re-delivery is the evidence the doctrine asks for)
    new_complete = {i: infos[i].s.complete for i in range(n)}
    for e in kept:
        if e.kind == "false_start" and not infos[e.a].terminal:
            new_complete[e.a] = False

    clusters: list[Cluster] = []
    sent_cluster: dict[int, str] = {}
    for ci, members in enumerate(groups, start=1):
        cid = cluster_id(ci)
        for m in members:
            sent_cluster[m] = cid
        cedges = [e for e in kept if e.a in members]
        ref = max(len(infos[m].lex) for m in members)
        partial = {m for m in members if infos[m].roles & {"false_start", "extension", "abandoned_retake", "pickup"}}
        full = [m for m in members if m not in partial and len(infos[m].lex) >= p.full_coverage * ref]
        complete = [m for m in members if new_complete[m]]
        cands = [m for m in members if m in full and new_complete[m]]
        if cands:
            rec, why = cands[-1], "last complete full take"
        elif complete:
            rec, why = complete[-1], "last complete take (no complete full delivery)"
        elif full:
            rec, why = full[-1], "last full take (every take is incomplete)"
        else:
            rec, why = members[-1], "latest take (all partial)"
        later_incomplete = [m for m in members if m > rec and not new_complete[m]]
        if later_incomplete and why.startswith("last complete"):
            why += "; later " + ", ".join(infos[m].s.id for m in later_incomplete) + " incomplete"
        sim = sum(e.sim for e in cedges) / len(cedges) if cedges else 0.0
        clusters.append(Cluster(
            id=cid, sentence_ids=[infos[m].s.id for m in members], similarity=round(min(1.0, max(0.0, sim)), 3),
            recommended_sentence_id=infos[rec].s.id,
            notes=_notes(members, cedges, infos, words, rec, why, new_complete),
        ))

    _passage_notes(clusters, groups, kept, infos)

    out_words = list(words)
    out_sents: list[Sentence] = []
    for i, info in enumerate(infos):
        cid = sent_cluster.get(i)
        out_sents.append(info.s.model_copy(update={"cluster_id": cid, "complete": new_complete[i]}))
    member_words = {wid: sent_cluster[i] for i, info in enumerate(infos) if i in sent_cluster
                    for wid in info.s.word_ids}
    for k, w in enumerate(out_words):
        cid = member_words.get(w.id)
        if w.cluster_id != cid:
            out_words[k] = w.model_copy(update={"cluster_id": cid})
    return out_words, out_sents, clusters


def _notes(members: list[int], edges: list[_Edge], infos: list[_SInfo], words: list[Word], rec: int, why: str,
           complete: dict[int, bool]) -> str:
    parts: list[str] = []
    for e in edges:
        A, B = infos[e.a], infos[e.b]
        a, b = A.s.id, B.s.id
        if e.kind == "retake" and e.detail == "far":
            parts.append(f"{b} re-delivers {a} (similarity {e.sim:.2f}, {e.b - e.a} sentences later: a second pass "
                         f"of the script, or a deliberate callback)")
        elif e.kind == "retake":
            parts.append(f"{b} re-delivers {a} (similarity {e.sim:.2f})")
        elif e.kind == "rephrase":
            parts.append(f"{b} rephrases {a} (similarity {e.sim:.2f}; lower confidence, check wording)")
        elif e.kind == "false_start":
            parts.append(f"{a} is a false start of {b} (same opening, abandoned)")
        elif e.kind == "extension":
            parts.append(f"{b} repeats {a} and extends it: a retake, or a deliberate build-up (check delivery)")
        elif e.kind == "abandoned_retake":
            parts.append(f"{b} restarts {a} and is abandoned")
        elif e.kind == "pickup":
            k = len(B.lex)
            tail_first = A.lex[-k].pos
            a_ids = A.s.word_ids
            first_tail_id = words[tail_first].id
            cut_at = a_ids.index(first_tail_id) if first_tail_id in a_ids else len(a_ids)
            keep_to = a_ids[cut_at - 1] if cut_at > 0 else None
            tail_txt = " ".join(words[x.pos].text for x in A.lex[-k:])
            splice = (f"; to use {b}'s delivery keep {a} through {keep_to} then {b}" if keep_to else "")
            parts.append(f"{b} re-says only the tail of {a} ({first_tail_id}-{a_ids[-1]} \"{tail_txt}\"): a pickup "
                         f"before a retake; choose_take({b}) would drop the rest of {a}{splice}")
    takes = []
    for m in members:
        info = infos[m]
        bits = ["complete" if complete[m] else "incomplete", f"{len(info.lex)} words",
                _fmt_s(info.s.end_us - info.s.start_us)]
        if info.fillers:
            bits.append(f"{info.fillers} filler{'s' if info.fillers != 1 else ''}")
        takes.append(f"{info.s.id} ({', '.join(bits)})")
    parts.append("takes: " + "; ".join(takes))
    parts.append(f"recommended {infos[rec].s.id}: {why} (prior only; delivery decides)")
    return ". ".join(parts) + "."


def _passage_notes(clusters: list[Cluster], groups: list[list[int]], edges: list[_Edge], infos: list[_SInfo]) -> None:
    """Flag clusters whose takes pair up sentence-by-sentence (a whole passage delivered twice).

    A first pass that is abandoned in its last sentence and then re-delivered whole (A1 B1… A2 B2, B1 a false
    start of B2: the speaker broke off and restarted the passage) is a passage too: the second pass is the
    creator's take, and mixing passes would put a seam inside the passage."""
    pairs_by_cluster: list[set[tuple[int, int]]] = []
    abandoned_by_cluster: list[set[tuple[int, int]]] = []
    for members in groups:
        mset = set(members)
        # only full re-deliveries form a passage (a pickup + false start is a splice, not a second pass)
        pairs_by_cluster.append({(e.a, e.b) for e in edges
                                 if e.a in mset and e.b in mset and e.kind in ("retake", "rephrase")})
        abandoned_by_cluster.append({(e.a, e.b) for e in edges
                                     if e.a in mset and e.b in mset and e.kind == "false_start" and e.short == e.a})
    for x in range(len(groups)):
        for y in range(len(groups)):
            if x == y:
                continue
            full = [(a, b) for (a, b) in pairs_by_cluster[x] if (a + 1, b + 1) in pairs_by_cluster[y]] if x < y \
                else []
            broken = [(a, b) for (a, b) in pairs_by_cluster[x] if (a + 1, b + 1) in abandoned_by_cluster[y]]
            if full:
                a, b = full[0]
                span = f"{infos[a].s.id}-{infos[a + 1].s.id} re-delivered as {infos[b].s.id}-{infos[b + 1].s.id}"
                tail = "prefer takes from one pass for continuity."
            elif broken:
                a, b = broken[0]
                span = (f"{infos[a].s.id}-{infos[a + 1].s.id} is a first pass abandoned in {infos[a + 1].s.id}, "
                        f"re-delivered whole as {infos[b].s.id}-{infos[b + 1].s.id}")
                tail = (f"keep the second pass whole ({infos[b].s.id}-{infos[b + 1].s.id}): mixing passes puts a seam "
                        "inside the passage, between two deliveries.")
            else:
                continue
            for idx, other in ((x, clusters[y].id), (y, clusters[x].id)):
                c = clusters[idx]
                if f"Passage with {other}" in c.notes:
                    continue
                clusters[idx] = c.model_copy(update={"notes": c.notes + f" Passage with {other}: {span}; {tail}"})


def analyze_takes(words: list[Word], *, params: TakeParams | None = None
                  ) -> tuple[list[Word], list[Sentence], list[Cluster]]:
    """:func:`segment_sentences` then :func:`cluster_takes`."""
    ws, ss = segment_sentences(words, params=params)
    return cluster_takes(ws, ss, params=params)
