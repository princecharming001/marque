"""Tests for studio.plan.code_plan (the standard tier's deterministic plan). Keyless and offline: the hand-built
fixture index from conftest (s001 hook; s002 false start ending in ``restr-``, c01 take 1; s003 the retake, c01 rec;
s004 opens with the filler ``Um``; s005 the spoken CTA) and small synthetic takes built here for the rules the
fixture does not exercise (recording dropouts, restarts, fillers, long pauses). Nothing renders; the colour
measurement is monkeypatched (no mezzanine decode)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from conftest import build_take_index

from studio.compile import captions as cap
from studio.compile.color import ColorMeasurement, FaceColor, FrameColor
from studio.compile.timeline import compile as compile_timeline
from studio.doc.model import CutDocument, Pins, Point, Segment, new_document
from studio.doc.ops import apply_ops
from studio.doc.validate import radio_blockers, validate_document
from studio.jobs import Job
from studio.perception.index import (
    AudioMetrics,
    Cluster,
    Energy,
    Gap,
    Sentence,
    TakeIndex,
    Visual,
    Word,
    gap_id,
    sentence_id,
    word_id,
)
from studio.plan import code_plan as cp
from studio.plan.code_plan import (
    CAPTION_SIZE_LADDER,
    PAID_ISOLATION_MAX_S,
    PLAN_BY,
    CodePlanError,
    RadioBlocked,
    code_plan,
    energy_band,
    plan_document,
    radio_check,
)
from studio.plan.decision import PlanDecision

BASE = {"style_primary": "explainer", "goal": "teach restraint in one line", "hook_promise": "most people over-cut"}
CTA_IDS = [f"w{n:04d}" for n in range(29, 42)]  # s005, every word lexical


# ============================================================================================ helpers
def decision(**over: Any) -> PlanDecision:
    return PlanDecision.model_validate({**BASE, **over})


def rng(a: str, b: str | None = None) -> dict[str, str]:
    return {"from_word": a, "to_word": b or a}


def plan(ix: TakeIndex, d: PlanDecision | None = None, *, platform: str = "tiktok", **kw: Any
         ) -> tuple[CutDocument, list[dict[str, Any]], str]:
    """``(document, ops, summary note)``: the op list applied to a fresh document, every op accepted."""
    ops = code_plan(ix, platform=platform, decision=d, **kw)
    doc, res = apply_ops(new_document(created_by=PLAN_BY, platforms=(platform,)), ops, ix, by=PLAN_BY, persist=False)
    rejected = [(r.op, r.reason) for r in res if not r.applied and r.reason != "no change"]
    assert rejected == [], rejected
    note = next(o["text"] for o in ops if o["op"] == "note")
    return doc, ops, note


def kept(doc: CutDocument, ix: TakeIndex) -> list[str]:
    return doc.kept_word_ids(ix)


def kept_text(doc: CutDocument, ix: TakeIndex) -> str:
    return " ".join(ix.word(w).text for w in kept(doc, ix))


def ops_named(ops: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [o for o in ops if o["op"] == name]


def wids(a: int, b: int) -> list[str]:
    return [word_id(n) for n in range(a, b + 1)]


# ---------------------------------------------------------------------------------------------- synthetic takes
@dataclass(frozen=True)
class T:
    """One word of a synthetic take: ``gap`` ms of silence after it (a Gap object; 0 = abutting with 40 ms and no
    gap, or nothing at all with ``glue``), ``dropout`` makes that gap a recording dropout."""

    text: str
    kind: str = "word"
    gap: int = 0
    truncated: str | None = None
    dropout: bool = False
    glue: bool = False
    emphasis: float = 0.1


def words(text: str, end_gap: int = 400) -> list[T]:
    """Plain words; the last one is followed by ``end_gap`` ms (a sentence pause; 0 = none)."""
    toks = [T(t) for t in text.split()]
    if end_gap:
        toks[-1] = T(toks[-1].text, gap=end_gap)
    return toks


@dataclass(frozen=True)
class S:
    toks: list[T]
    complete: bool = True
    cluster: str | None = None


def build(*sents: S, clusters: tuple[Cluster, ...] = (), energy: float = 0.55, audio: AudioMetrics | None = None,
          media_wh: tuple[int, int] = (1080, 1920)) -> TakeIndex:
    """A valid TakeIndex from sentences of :class:`T` words (no face track; media from the conftest fixture)."""
    ws: list[Word] = []
    gaps: list[Gap] = []
    sentences: list[Sentence] = []
    t = 300_000
    n = 0
    for si, s in enumerate(sents, start=1):
        sid = sentence_id(si)
        ids: list[str] = []
        for x in s.toks:
            n += 1
            wid = word_id(n)
            dur = 320_000 if x.kind == "filler" else (160 + 22 * len(x.text.strip(".,-"))) * 1000
            ws.append(Word(id=wid, text=x.text, start_us=t, end_us=t + dur, kind=x.kind, truncated=x.truncated,
                           sentence_id=sid, cluster_id=s.cluster, emphasis=x.emphasis))  # type: ignore[arg-type]
            ids.append(wid)
            t += dur
            if x.gap:
                g0, t = t, t + x.gap * 1000
                gaps.append(Gap(id="g0000", after_word_id=wid, before_word_id=None, start_us=g0, end_us=t,
                                kind="silence" if x.dropout else "pause", snap_us=(g0 + t) // 2,
                                dropouts_us=[(g0 + 1000, t - 1000)] if x.dropout else []))
            elif not x.glue:
                t += 40_000
        mine = ws[-len(s.toks):]
        sentences.append(Sentence(id=sid, word_ids=ids, text=" ".join(w.text for w in mine), start_us=mine[0].start_us,
                                  end_us=mine[-1].end_us, complete=s.complete, cluster_id=s.cluster))
    pos = {w.id: i for i, w in enumerate(ws)}
    fixed = [Gap(id="g0000", after_word_id=None, before_word_id=ws[0].id, start_us=0, end_us=300_000, kind="silence",
                 snap_us=150_000)]
    for g in gaps:
        i = pos[g.after_word_id]  # type: ignore[index]
        fixed.append(g.model_copy(update={"before_word_id": ws[i + 1].id if i + 1 < len(ws) else None}))
    if fixed[-1].after_word_id != ws[-1].id:
        fixed.append(Gap(id="g0000", after_word_id=ws[-1].id, start_us=t, end_us=t + 300_000, kind="silence",
                         snap_us=t + 150_000))
        t += 300_000
    else:
        t = fixed[-1].end_us
    fixed = [g.model_copy(update={"id": gap_id(k)}) for k, g in enumerate(fixed, start=1)]
    base = build_take_index().media
    media = base.model_copy(update={"duration_us": t, "width": media_wh[0], "height": media_wh[1],
                                    "audio": base.audio.model_copy(update={"duration_us": t})})
    return TakeIndex(media=media, words=ws, sentences=sentences, clusters=list(clusters), gaps=fixed, visual=Visual(),
                     audio=audio or AudioMetrics(snr_db=30.0, noise_floor_db=-60.0),
                     energy=Energy(wpm=160.0, overall=energy), transcript_text=" ".join(s.text for s in sentences))


def wid_of(ix: TakeIndex, text: str, nth: int = 0) -> str:
    return [w.id for w in ix.words if w.text == text][nth]


def gap_after_text(ix: TakeIndex, text: str) -> Gap:
    g = ix.gap_after(wid_of(ix, text))
    assert g is not None
    return g


# ============================================================================================ the pure-code plan
def test_the_pure_code_plan_is_a_valid_complete_document(take_index: TakeIndex) -> None:
    ix = take_index
    doc, ops, note = plan(ix)
    assert [o["op"] for o in ops[:4]] == ["set_brief", "set_style", "set_deliverables", "set_story"]
    assert ops[-1]["op"] == "note" and "no decision" in note
    # the retake cluster keeps s003, the false start (with its cut-off) and the opening filler go
    assert kept(doc, ix) == wids(1, 8) + wids(14, 18) + wids(20, 41)
    assert [e for e in validate_document(doc, ix) if e.level == "error"] == []
    assert radio_blockers(doc, ix) == [] and radio_check(doc, ix) == []
    tl = compile_timeline(doc, ix)
    assert tl.duration > 0 and tl.captions
    # every removed range says why, in words a report can show
    reasons = {r.from_word: r.reason for r in doc.removed}
    assert "retake: c01 keeps s003" in reasons["w0009"] and "filler" in reasons["w0019"]
    # deliverables, voice chain and captions are set; the spoken CTA is protected
    assert [d.platform for d in doc.deliverables] == ["tiktok"]
    assert doc.audio.voice is not None and doc.captions is not None and doc.captions.pages
    assert doc.pins.cta_word_ids == CTA_IDS and doc.pins.payoff_word_ids == []


def test_the_plan_names_ids_never_times_and_reapplies_identically(take_index: TakeIndex) -> None:
    ops = code_plan(take_index, platform="tiktok", decision=None)
    text = json.dumps(ops)  # plain JSON

    def keys(node: Any) -> list[str]:
        if isinstance(node, dict):
            return [*node, *(k for v in node.values() for k in keys(v))]
        if isinstance(node, list):
            return [k for v in node for k in keys(v)]
        return []

    assert not [k for k in keys(json.loads(text)) if k.endswith("_us") or k in ("t", "time", "start", "end", "at")]
    a, _ = apply_ops(new_document(created_by=PLAN_BY), json.loads(text), take_index, persist=False)
    b, _ = apply_ops(new_document(created_by=PLAN_BY), ops, take_index, persist=False)
    assert a.model_dump() == b.model_dump()


def test_never_music_sfx_broll_or_an_invented_cta(take_index: TakeIndex) -> None:
    full = decision(payoff=rng("w0018"), punch_in={"anchor_word": "w0020", "reason": "lean in"},
                    structure_markers=[{"anchor_from_word": "w0014", "anchor_to_word": "w0018", "label": "1/2"}])
    for d in (None, full):
        doc, ops, note = plan(take_index, d)
        assert not {o["op"] for o in ops} & {"set_music", "add_sfx", "add_insert", "set_hook_alternates"}
        assert doc.audio.music is None and doc.audio.sfx == [] and doc.inserts == []
        assert all(t.kind == "label" for t in doc.texts)
        assert "music/SFX/b-roll none" in note
    # a take without a spoken CTA gets no CTA pin and no CTA in the brief
    ix = build(S(words("Most people cut too much.")), S(words("Keep the pauses that matter.")))
    doc, _ops, _note = plan(ix)
    assert doc.pins.cta_word_ids == [] and doc.brief is not None and doc.brief.cta == ""


def test_brief_and_style_from_measurement_or_the_decision(take_index: TakeIndex) -> None:
    doc, _ops, _note = plan(take_index)
    assert doc.brief is not None and doc.brief.goal.startswith("Standard tier code plan")
    assert doc.brief.hook.startswith("Most people cut") and doc.brief.cta.startswith("If this helped, follow me")
    assert doc.style.primary is None and doc.style.dials.energy == pytest.approx(0.55)
    doc, _ops, _note = plan(take_index, style="educational", brief="for new editors")
    assert doc.style.primary == "educational" and doc.brief is not None and doc.brief.text == "for new editors"
    d = decision(style_secondary="founder_brand", notes="keep it tight")
    doc, _ops, _note = plan(take_index, d)
    assert doc.brief is not None and doc.brief.goal == BASE["goal"] and doc.brief.hook == BASE["hook_promise"]
    assert doc.style.primary == "explainer" and doc.style.blend == ["founder_brand"]
    assert doc.brief.text == "keep it tight"


def test_energy_bands() -> None:
    assert energy_band(0.2) == "calm" and energy_band(0.5) == "conversational" and energy_band(0.9) == "high"


# ============================================================================================ decision: story
def test_decision_drops_are_honoured_but_never_the_cta_or_past_half(take_index: TakeIndex) -> None:
    ix = take_index
    doc, _ops, _note = plan(ix, decision(drop_sentences=[{"sentence_id": "s004", "reason": "tangent"}]))
    assert not set(wids(19, 28)) & set(kept(doc, ix))
    assert any("tangent" in r.reason for r in doc.removed)
    # the spoken CTA survives any reason but repeat
    doc, _ops, note = plan(ix, decision(drop_sentences=[{"sentence_id": "s005", "reason": "after_payoff"}]))
    assert set(CTA_IDS) <= set(kept(doc, ix)) and "spoken call to action" in note
    doc, _ops, _note = plan(ix, decision(drop_sentences=[{"sentence_id": "s005", "reason": "repeat"}]))
    assert not set(CTA_IDS) & set(kept(doc, ix)) and doc.pins.cta_word_ids == []
    # drops stop before half of the 41 spoken words: s001 (8) + s003 (5) fit, s004 (10) would pass 50 %
    drops = [{"sentence_id": s, "reason": "tangent"} for s in ("s001", "s003", "s004")]
    doc, _ops, note = plan(ix, decision(drop_sentences=drops))
    assert not set(wids(1, 8) + wids(14, 18)) & set(kept(doc, ix)) and set(wids(20, 28)) <= set(kept(doc, ix))
    assert "drop s004 (tangent) skipped" in note and "50%" in note


def test_take_choices_are_honoured_and_fall_back_when_unusable() -> None:
    c = Cluster(id="c01", sentence_ids=["s001", "s002"], similarity=0.9, recommended_sentence_id="s002")
    ix = build(S(words("The real secret is restraint."), cluster="c01"),
               S(words("The real secret is restraint."), cluster="c01"), S(words("Keep what matters.")),
               clusters=(c,))
    doc, _ops, _note = plan(ix)
    assert kept(doc, ix)[:5] == wids(6, 10)  # the recommended (later) take
    doc, _ops, _note = plan(ix, decision(choose_take=[{"cluster_id": "c01", "sentence_id": "s001"}]))
    assert kept(doc, ix)[:5] == wids(1, 5)
    assert any("keeps s001 (chosen by the plan)" in r.reason for r in doc.removed)
    # a choice outside the cluster is noted and the recommendation stands
    doc, _ops, note = plan(ix, decision(choose_take=[{"cluster_id": "c01", "sentence_id": "s003"}]))
    assert kept(doc, ix)[:5] == wids(6, 10) and "choose_take c01/s003 skipped" in note


def test_a_chosen_take_that_loses_its_line_to_a_cut_off_falls_back(take_index: TakeIndex) -> None:
    # s002 ends in "restr-" with no clause edge before it: it cannot be kept, so c01 keeps s003 after all
    doc, _ops, note = plan(take_index, decision(choose_take=[{"cluster_id": "c01", "sentence_id": "s002"}]))
    assert set(wids(14, 18)) <= set(kept(doc, take_index)) and not set(wids(9, 13)) & set(kept(doc, take_index))
    assert "s002 (chosen by the plan) is unusable" in note


def test_payoff_cta_and_must_keep_pins_cover_kept_lexical_words(take_index: TakeIndex) -> None:
    d = decision(payoff=rng("w0018"), cta=rng("w0032", "w0033"), must_keep=[rng("w0019", "w0022")])
    doc, ops, _note = plan(take_index, d)
    assert doc.pins.payoff_word_ids == ["w0018"]
    assert doc.pins.must_keep_word_ids == ["w0020", "w0021", "w0022"]  # the filler w0019 is cut, never pinned
    assert doc.pins.cta_word_ids == CTA_IDS
    assert {o["kind"] for o in ops_named(ops, "pin")} == {"payoff", "cta", "must_keep"}
    # a reversed or unknown range is skipped with a note, never fatal
    doc, _ops, note = plan(take_index, decision(payoff=rng("w0018", "w0014"), must_keep=[rng("w9999")]))
    assert doc.pins.payoff_word_ids == [] and note.count("pin ") >= 2


def test_unknown_ids_in_a_decision_are_skipped_never_fatal(take_index: TakeIndex) -> None:
    d = decision(drop_sentences=[{"sentence_id": "s999", "reason": "tangent"}],
                 choose_take=[{"cluster_id": "c09", "sentence_id": "s001"}], payoff=rng("w9999"),
                 punch_in={"anchor_word": "w9999", "reason": "x"}, caption_emphasis_word_ids=["w9999"],
                 caption_text_fixes=[{"word_id": "w9999", "text": "x"}], cut_filler_word_ids=["w9999"],
                 deliberate_beat_gap_ids=["g9999"],
                 structure_markers=[{"anchor_from_word": "w9998", "anchor_to_word": "w9999", "label": "1/2"}])
    doc, _ops, note = plan(take_index, d)
    assert kept(doc, take_index) == plan(take_index)[0].kept_word_ids(take_index)
    for needle in ("drop s999 skipped", "choose_take c09/s001 skipped", "punch-in at w9999 skipped",
                   "caption emphasis w9999 skipped", "marker '1/2' skipped"):
        assert needle in note, needle


# ============================================================================================ cut-offs and dropouts
def _dropout_take() -> TakeIndex:
    """The var-silences shapes: words chopped by 20 s recording dropouts, a restart, a fragment glued to its line."""
    return build(
        S(words("Everyone says you need a story.")),
        S([*words("So founders spend months, staring at their life, trying to sound", 0),
           T("meaningful", "cutoff", 20_000, truncated="end", dropout=True),
           T("enough.", "cutoff", 400, truncated="start")]),
        S([*words("Start with who buys, what they", 0), T("actually", "cutoff", 20_000, truncated="end", dropout=True),
           *words("care about solving, and build it backward.")]),
        S([*words("They", 0), T("really", "cutoff", 3_000, truncated="end", dropout=True), *words("matter a lot.")]),
        S([T("nough,", "cutoff", truncated="start"), *words("so we move on.")]),
        S([T("uh", "filler"), T("ready,", "cutoff", truncated="start"), *words("we keep going.")]),
        S([*words("We need to", 0), T("b", "cutoff", 150), *words("build it right.")]),
        S([*words("Look, we need", 0), T("to", glue=True), T("b", "cutoff", 150), *words("build it, then ship it.")]),
    )


def test_chopped_and_cut_off_words_go_back_to_the_nearest_clause_edge() -> None:
    ix = _dropout_take()
    doc, _ops, note = plan(ix)
    text = kept_text(doc, ix)
    # both chopped words at the end of a clause list: back to "life," (the dropout is not an edge)
    assert "staring at their life, Start" in text and "trying" not in text and "meaningful" not in text
    # a chopped word mid-sentence takes its clause both ways: back to "buys," and on past the dropout to "solving,"
    assert "Start with who buys, and build it backward." in text and "care about" not in text
    # no clause edge before the chopped word: the sentence goes whole
    assert "They" not in text.split() and "matter a lot." not in text
    # the sentence's first word lost its onset: the sentence start is its clause edge
    assert "so we move on." in text and "nough," not in text
    assert "on. we keep going." in text and "uh" not in text.split()  # only a filler before it: same edge
    # a restart with a closure on both sides goes alone; one glued to its line has no closure to cut in, so the
    # speaker's own stumble stays inside continuous speech (never at a join) and the content around it stays too
    assert "We need to build it right." in text
    assert "Look, we need to b build it, then ship it." in text
    glued = [w for w in kept(doc, ix) if ix.word(w).kind == "cutoff"]
    assert len(glued) == 1 and ix.word(glued[0]).text == "b" and ix.word(glued[0]).truncated is None
    assert f"stumble {glued[0]} 'b-' kept: no closure to cut in" in note
    assert radio_blockers(doc, ix) == [] and [e for e in validate_document(doc, ix) if e.level == "error"] == []
    reasons = " ".join(r.reason for r in doc.removed)
    assert "dropout-chopped word" in reasons and "stumble" in reasons and "no clause edge" in reasons
    assert "kept" in note and compile_timeline(doc, ix).duration > 0


def _cta_stumble_take() -> TakeIndex:
    """take40's ending with a speaker stumble in the CTA ("nex-" glued to both neighbours) and a dropout-chopped word
    in a protected payoff sentence with no clause edge before it."""
    return build(
        S(words("Fusion food is two cuisines at once.")),
        S([*words("The real trick", 0), T("is", "cutoff", 3_000, truncated="end", dropout=True),
           *words("balance every bite.")]),
        S([*words("Save this before your", 0), T("nex", "cutoff", glue=True), *words("next fusion experiment.")]),
    )


def test_a_stumble_or_a_chopped_word_never_costs_the_cta_or_the_payoff() -> None:
    """A stumble with no closure stays (the CTA survives whole, pinned, and is the brief's CTA); a dropout-chopped
    word with no clause edge in the decision's payoff sentence goes alone (a mid-clause seam) instead of the
    sentence."""
    ix = _cta_stumble_take()
    cta = [w.id for w in ix.words if w.sentence_id == "s003"]
    doc, _ops, note = plan(ix)
    text = kept_text(doc, ix)
    assert "Save this before your nex next fusion experiment." in text
    assert set(cta) - {wid_of(ix, "nex")} <= set(doc.pins.cta_word_ids) and doc.brief.cta.startswith("Save this")
    assert "The real trick" not in text  # not protected without a decision: no clause edge, the sentence goes
    payoff = rng(wid_of(ix, "balance"), wid_of(ix, "bite."))
    doc2, _ops, note2 = plan(ix, decision(payoff=payoff, cta=rng(cta[0], cta[-1])))
    text2 = kept_text(doc2, ix)
    assert "The real trick balance every bite." in text2
    assert [w for w in kept(doc2, ix) if ix.word(w).truncated] == []  # the chopped word itself is gone
    assert "only the chopped word removed (a mid-clause seam)" in " ".join(r.reason for r in doc2.removed)
    assert wid_of(ix, "bite.") in doc2.pins.payoff_word_ids and radio_blockers(doc2, ix) == []
    assert "Save this before your nex next fusion experiment." in text2


def test_a_decision_range_that_lost_words_is_named_in_the_note(take_index: TakeIndex) -> None:
    # the payoff range runs into the false start the retake rule removes: the note names the missing words
    doc, _ops, note = plan(take_index, decision(payoff=rng("w0009", "w0018")))
    assert "pin payoff w0009-w0018: 4 of its words are not in the story (w0009, w0010, w0011, w0012)" in note


# ============================================================================================ fillers
def _filler_take() -> TakeIndex:
    return build(
        S([T("um", "filler"), *words("so this works.")]),  # the first sentence: no fillers
        S([*words("I think", 0), T("uh", "filler"), *words("we should go.")]),  # mid-clause, no pauses: stays
        S([*words("We should", 0), T("now,", gap=200), T("um", "filler", 200), *words("go home.")]),  # bounded
        S([T("um", "filler"), *words("next we ship it.")]),  # sentence-initial
        S([*words("And then", 0), T("er", "filler", glue=True), *words("it worked.")]),  # coarticulated
    )


def test_fillers_follow_the_doctrine_and_the_decision() -> None:
    ix = _filler_take()
    um1, uh, um3, um4, er = (wid_of(ix, "um"), wid_of(ix, "uh"), wid_of(ix, "um", 1), wid_of(ix, "um", 2),
                             wid_of(ix, "er"))
    doc, ops, _note = plan(ix)
    k = set(kept(doc, ix))
    assert um1 not in k and um3 not in k and um4 not in k  # first sentence, bounded, sentence-initial
    assert uh in k and er in k  # a lone mid-clause filler stays
    why = {o["from_word"]: o["reason"] for o in ops_named(ops, "cut_words")}
    assert "first sentence" in why[um1] and "bounded" in why[um3] and "sentence-initial" in why[um4]
    # the decision may cut a mid-clause filler, but never one without a closure on both sides
    doc, _ops, note = plan(ix, decision(cut_filler_word_ids=[uh, er]))
    assert uh not in set(kept(doc, ix)) and er in set(kept(doc, ix))
    assert f"{er} {{er}} kept: no closure" in note


# ============================================================================================ pauses
def _pause_take(energy: float = 0.55) -> TakeIndex:
    return build(
        S(words("We start here.", 400)), S(words("Then we go on.", 450)), S(words("It gets better.", 500)),
        S(words("Wait for it.", 2_000)),  # dead air at a boundary
        S(words("Now the claim.", 1_100)),  # a long boundary pause, over twice the median
        S([*words("We went", 0), T("over", gap=800), *words("there today.", 400)]),  # a mid-clause hesitation
        S([*words("We came", 0), T("back", gap=550), *words("again.", 400)]),  # a short one: kept
        S([*words("The tape", 0), T("dropped", gap=1_800, dropout=True), *words("out here.", 400)]),  # a dropout
        S(words("That is all.")),
        energy=energy,
    )


def test_pauses_tighten_to_the_doctrine_never_below_the_median() -> None:
    ix = _pause_take()
    median = cp._speaker_median_ms(ix)
    assert median == pytest.approx(475.0)  # 400 x4, 450, 500, 550, 800, 1100, 2000 (the dropout is not a pause)
    doc, ops, _note = plan(ix)
    sets = {o["gap_id"]: o["ms"] for o in ops_named(ops, "set_gap")}
    dead, long_, hes = (gap_after_text(ix, "it.").id, gap_after_text(ix, "claim.").id, gap_after_text(ix, "over").id)
    drop = gap_after_text(ix, "dropped").id
    # a long boundary pause to the beat window's top (conversational 0.9 s); the 2 s stall to the speaker's boundary
    # pause (the 475 ms median held in the 250-450 ms band); the hesitation to the median, above the 400 ms target;
    # the 1.8 s recording dropout between kept words is dead air: down to its class's target (mid-clause: 475)
    assert sets == {dead: 450, long_: 900, hes: 475, drop: 475}
    overrides = {g: ms for s in doc.segments for g, ms in s.gap_overrides.items()}
    assert overrides == sets
    assert gap_after_text(ix, "back").id not in sets
    # the decision's deliberate beats keep their measured length (a dropout is never one)
    doc, ops, _note = plan(ix, decision(deliberate_beat_gap_ids=[dead, drop]))
    assert {o["gap_id"] for o in ops_named(ops, "set_gap")} == {long_, hes, drop}
    # a high-energy take tightens boundary pauses to its shorter beat window
    ops = code_plan(_pause_take(energy=0.85), platform="tiktok", decision=None)
    assert {o["gap_id"]: o["ms"] for o in ops_named(ops, "set_gap")}[long_] == 600


def test_a_stall_comes_down_to_the_speakers_boundary_pause_not_the_beat_window() -> None:
    """The doctrine's worked example: a 2.1 s stall goes to about the speaker's boundary pause (~0.4 s), not to the top
    of the deliberate-beat window (a calm take's 3 s stall must not ship as 1.5 s, the length doctrine calls dead
    air)."""
    def take(energy: float) -> TakeIndex:
        return build(S(words("We start here.", 380)), S(words("Then we go on.", 380)), S(words("It gets better.", 380)),
                     S(words("First.", 2_100)), S(words("Now the claim.", 3_000)), S(words("That is all.")),
                     energy=energy)

    for energy, want in ((0.55, 380), (0.3, 380), (0.85, 300)):
        ix = take(energy)
        sets = {o["gap_id"]: o["ms"] for o in ops_named(code_plan(ix, platform="tiktok", decision=None), "set_gap")}
        assert sets[gap_after_text(ix, "First.").id] == want, energy
        assert sets[gap_after_text(ix, "claim.").id] == want, energy
    assert cp._stall_target_ms(300.0, (350.0, 700.0)) == 350.0  # a calm band floor above a fast median
    assert cp._stall_target_ms(500.0, (200.0, 300.0)) == 350.0  # never under 0.7x the speaker's median
    assert cp._stall_target_ms(0.0, (250.0, 450.0)) == 350.0  # no median: the band's middle


def test_a_dropout_inside_kept_speech_is_cut_as_dead_air() -> None:
    """var-silences had ~20 s dropouts; when one lands in a pause between two intact kept words it must not play at
    full length (QA fills it with room tone, so no invariant would catch it)."""
    ix = build(S(words("Everyone says you need a story.")),
               S([*words("It has to feel", 0), T("meaningful", gap=20_000, dropout=True), *words("enough to matter.")]),
               S(words("That is all.")))
    doc, ops, _note = plan(ix)
    g = gap_after_text(ix, "meaningful").id
    assert {o["gap_id"]: o["ms"] for o in ops_named(ops, "set_gap")}[g] < 1_000
    assert "meaningful enough to matter." in kept_text(doc, ix)
    assert compile_timeline(doc, ix).duration < 8.0  # not ~25 s of output


# ============================================================================================ framing
def test_the_punch_in_is_applied_only_where_it_can_land(take_index: TakeIndex) -> None:
    ix = take_index
    doc, ops, _note = plan(ix, decision(punch_in={"anchor_word": "w0020", "reason": "lean in on the rule"}))
    framed = [s for s in doc.segments if s.framing is not None]
    assert len(framed) == 1 and framed[0].framing.scale == pytest.approx(1.15)  # type: ignore[union-attr]
    assert framed[0].framing.anchor_word == "w0020"  # type: ignore[union-attr]
    # it holds to the end of the anchor's sentence and returns to base at that boundary, never mid-sentence
    assert (framed[0].from_word, framed[0].to_word) == ("w0020", "w0028")
    after = doc.segments[doc.segment_index(framed[0].id) + 1]
    assert after.from_word == "w0029" and after.framing is None and after.seam_in.kind == "punch"
    assert len(ops_named(ops, "set_framing")) == 1
    assert [e for e in validate_document(doc, ix) if e.level == "error"] == [] and radio_blockers(doc, ix) == []
    for anchor, needle in (("w0003", "hook's first seconds"), ("w0010", "not in the story"),
                           ("w0040", "would hold only")):
        doc, _ops, note = plan(ix, decision(punch_in={"anchor_word": anchor, "reason": "x"}))
        assert all(s.framing is None for s in doc.segments) and needle in note, anchor
    # a source that is already upsampled at the base framing never gets a punch-in
    small = ix.model_copy(update={"media": ix.media.model_copy(update={"width": 720, "height": 1280})})
    doc, _ops, note = plan(small, decision(punch_in={"anchor_word": "w0020", "reason": "x"}))
    assert all(s.framing is None for s in doc.segments) and "stays clean only to" in note


# ============================================================================================ captions
def _fits(style: Any, text: str, width: int = 1080, platform: str = "tiktok") -> bool:
    zone = cap.safe_zone_for(platform, width=width, height=width * 16 // 9)
    f = cap.caption_fit(style, text, width, cap.load_caption_params(), max_width=width - zone.left - zone.right)
    return f.lines == 1 and f.shrink >= 0.999


def test_captions_share_one_size_and_every_page_renders_on_one_line(take_index: TakeIndex) -> None:
    ix = take_index
    doc, _ops, note = plan(ix)
    assert doc.captions is not None
    size = doc.captions.style.size_px
    assert size in CAPTION_SIZE_LADDER and doc.captions.style.lines == 1
    assert doc.captions.style.stroke_px == pytest.approx(size / 10.0, abs=0.05)
    assert f"captions {size} px" in note
    tl = compile_timeline(doc, ix)
    assert {p.style.size_px for p in tl.captions} == {size}  # the placer shrank nothing
    for p in doc.captions.pages:
        assert _fits(doc.captions.style, cp._page_text(ix, p.word_ids)), p
        assert len(p.emphasis_word_ids) <= 1
    # the captioned words are the story's displayed words, in order
    shown = [w for p in doc.captions.pages for w in p.word_ids]
    assert shown == [w for w in kept(doc, ix) if ix.word(w).kind not in ("filler", "event")]


def test_pages_are_re_paged_at_each_size_never_shrunk(take_index: TakeIndex) -> None:
    ix = take_index
    sc = cp._Scratch(ix, "tiktok", None)
    sc.push({"op": "set_story", "segments": [{"from_word": "w0001", "to_word": "w0008"},
                                             {"from_word": "w0014", "to_word": "w0041"}]})
    tl = sc.compile()
    assert tl is not None
    params = cap.load_caption_params()
    zone = cap.safe_zone_for("tiktok")
    sz = cp._Sizing(W=1080, max_w=1080 - zone.left - zone.right, params=params)
    # the pager on its own lets a page at 88 px render shrunk; the plan's paging at 88 px never does
    raw = cap.auto_caption_plan(sc.doc, ix, style=cp._caption_style(88), timeline=tl, params=params)
    assert not all(sz.fits(cp._caption_style(88), cp._page_text(ix, p.word_ids)) for p in raw.pages)
    for size in (88, 80, 64):
        pages = cp._pages_at(sc, tl, size, sz)
        assert pages is not None
        assert all(sz.fits(cp._caption_style(size), cp._page_text(ix, p.word_ids)) for p in pages)
        assert [w for p in pages for w in p.word_ids] == [w for p in raw.pages for w in p.word_ids]
        assert [p.id for p in pages] == [f"p{k:03d}" for k in range(1, len(pages) + 1)]
        assert cp._paging_faults(sc, pages, tl, params)[2] == 0  # no split leaves a page under min_page_s
    # at 76 px the only one-line split of "for more simple editing" leaves "editing" on screen 0.31 s: no paging at
    # that size (the ladder steps down instead of flickering)
    assert cp._pages_at(sc, tl, 76, sz) is None


def test_bigger_text_is_chosen_only_while_the_paging_stays_clean(take_index: TakeIndex) -> None:
    # short sentences of short words between long pauses: every page fits at the house size
    ix = build(*(S(words(t, 900)) for t in ("Go now.", "Be bold.", "Ship it.", "Do it.")))
    doc, _ops, _note = plan(ix)
    assert doc.captions is not None and doc.captions.style.size_px == CAPTION_SIZE_LADDER[0]
    # the paging faults the size choice compares: a lone non-punch page and a phrase broken after "the"
    sc = cp._Scratch(take_index, "tiktok", None)
    from studio.doc.model import CaptionPage

    pages = [CaptionPage(word_ids=["w0020", "w0021"]), CaptionPage(word_ids=["w0022"]),  # "keep the | pauses"
             CaptionPage(word_ids=["w0023", "w0024"]), CaptionPage(word_ids=["w0018"])]  # "restraint." is a punch
    assert cp._paging_faults(sc, pages) == (1, 1, 0)  # (no story yet: no timing to measure flicker on)
    # flicker: a page whose next page takes over sooner than min_page_s (0.5 s) counts; a one-word punch page has 0.25 s
    fast = build(S(words("Pour one into the other now.")), S(words("Then 3 more.")))
    st = cp._Scratch(fast, "tiktok", None)
    st.push({"op": "set_story", "segments": [{"from_word": fast.words[0].id, "to_word": fast.words[-1].id}]})
    w = [x.id for x in fast.words]
    flicker = [CaptionPage(word_ids=w[:1]), CaptionPage(word_ids=w[1:4]), CaptionPage(word_ids=w[4:6]),
               CaptionPage(word_ids=w[6:])]
    # "Pour" is on screen 0.22 s before "one into the" takes over: a lone page, a flicker, and "the | other" is broken
    assert cp._paging_faults(st, flicker) == (1, 1, 1)
    calm = [CaptionPage(word_ids=w[:4]), CaptionPage(word_ids=w[4:6]), CaptionPage(word_ids=w[6:])]
    assert cp._paging_faults(st, calm)[2] == 0


def test_decision_accents_keep_the_page_share_and_skip_device_words(take_index: TakeIndex) -> None:
    """Doctrine: accents on at most ~25% of pages, and never on a word a punch-in (or a label) already lands on;
    the planner's accents follow the same rules as the automatic ones."""
    ix = take_index
    auto, _ops, _note = plan(ix)
    pages = auto.captions.pages  # type: ignore[union-attr]
    one_per_page = [p.word_ids[-1] for p in pages][:8]
    d = decision(caption_emphasis_word_ids=["w0020", *[w for w in one_per_page if w != "w0020"]][:8],
                 punch_in={"anchor_word": "w0020", "reason": "lean in on the rule"})
    doc, _ops, note = plan(ix, d)
    assert doc.captions is not None and any(s.framing is not None for s in doc.segments)
    accented = [w for p in doc.captions.pages for w in p.emphasis_word_ids]
    budget = max(1, -(-len(doc.captions.pages) // 4))
    assert "w0020" not in accented and "caption emphasis w0020 skipped: a punch-in or label" in note
    assert 0 < len(accented) <= budget < 8 and "accent(s) already, the most for" in note


def test_a_word_too_wide_for_any_size_ships_at_the_floor_with_a_note() -> None:
    ix = build(S(words("This is Pneumonoultramicroscopicsilicovolcanoconiosis indeed.")), S(words("Short one.")))
    doc, _ops, note = plan(ix)
    assert doc.captions is not None and doc.captions.style.size_px == CAPTION_SIZE_LADDER[-1]
    assert "keeps every page on one line" in note


def test_decision_accents_replace_the_automatic_ones_and_text_fixes_must_fit(take_index: TakeIndex) -> None:
    ix = take_index
    auto, _ops, _note = plan(ix)
    assert auto.captions is not None and all(len(p.emphasis_word_ids) <= 1 for p in auto.captions.pages)
    assert any(p.emphasis_word_ids for p in auto.captions.pages)  # prosody picked some accents on its own
    two = next(p.word_ids[:2] for p in auto.captions.pages if len(p.word_ids) >= 2 and "w0018" not in p.word_ids)
    d = decision(caption_emphasis_word_ids=["w0018", *two],
                 caption_text_fixes=[{"word_id": "w0041", "text": "week!"},
                                     {"word_id": "w0029", "text": "Iffffffffffffffffffffffffffffffffffff"}])
    doc, ops, note = plan(ix, d)
    assert doc.captions is not None
    # the planner's accents replace the automatic ones, one per page
    assert [w for p in doc.captions.pages for w in p.emphasis_word_ids] == sorted(["w0018", two[0]])
    assert f"caption emphasis {two[1]} skipped" in note and "already carries an accent" in note
    # a display fix that fits is applied to its page; one that would not fit the line is skipped
    fixed = [p for p in doc.captions.pages if p.text]
    assert len(fixed) == 1 and "w0041" in fixed[0].word_ids
    assert fixed[0].text is not None and fixed[0].text.endswith("week!")
    assert "does not fit one line" in note
    assert len(ops_named(ops, "edit_caption_page")) == 3


# ============================================================================================ markers
def test_structure_markers_become_labels_on_kept_words(take_index: TakeIndex) -> None:
    d = decision(structure_markers=[
        {"anchor_from_word": "w0014", "anchor_to_word": "w0018", "label": "1/2"},
        {"anchor_from_word": "w0019", "anchor_to_word": "w0041", "label": "2/2"},
        {"anchor_from_word": "w0009", "anchor_to_word": "w0013", "label": "0/2"},
        {"anchor_from_word": "w0001", "anchor_to_word": "w0008", "label": "Step one: the hook"}])
    doc, ops, note = plan(take_index, d)
    texts = {t.text: t for t in doc.texts}
    assert set(texts) == {"1/2", "2/2", "Step one: the hook"} and all(t.kind == "label" for t in doc.texts)
    assert (texts["2/2"].anchor_from_word, texts["2/2"].anchor_to_word) == ("w0020", "w0041")  # the filler is cut
    assert texts["1/2"].position == Point(x=0.15, y=0.5) and texts["1/2"].style.size_px == 56
    assert texts["Step one: the hook"].position == "auto"
    assert "marker '0/2' skipped: its words are not in the story" in note
    # labels go in before the captions, so the caption placer steers around them
    names = [o["op"] for o in ops]
    assert names.index("add_text") < names.index("set_captions")


# ============================================================================================ voice / colour
def _with_audio(ix: TakeIndex, *, duration_s: float | None = None, **audio: Any) -> TakeIndex:
    upd: dict[str, Any] = {"audio": (ix.audio or AudioMetrics()).model_copy(update=audio)}
    if duration_s is not None:
        upd["media"] = ix.media.model_copy(update={"duration_us": round(duration_s * 1e6)})
    return ix.model_copy(update=upd)


def test_paid_isolation_only_for_music_in_the_room_on_a_short_source(take_index: TakeIndex) -> None:
    doc, _ops, note = plan(_with_audio(take_index, music_in_room=True))
    v = doc.audio.voice
    assert v is not None and (v.denoise, v.isolation_provider) == ("isolate", "elevenlabs") and "voice isolate" in note
    doc, _ops, _note = plan(_with_audio(take_index, music_in_room=True, duration_s=PAID_ISOLATION_MAX_S + 30))
    v = doc.audio.voice
    assert v is not None and (v.denoise, v.isolation_provider) == ("light", None)
    assert "needs isolation add-on" in v.notes and "longer than 60 s" in v.notes
    doc, _ops, _note = plan(_with_audio(take_index, snr_db=8.0))  # noisy, but no music: no paid isolation either
    v = doc.audio.voice
    assert v is not None and v.denoise == "light" and "no music in the room" in v.notes
    doc, _ops, _note = plan(take_index)
    v = doc.audio.voice
    assert v is not None and v.denoise == "none"


def test_paid_isolation_respects_the_tier_budget(take_index: TakeIndex, job: Job) -> None:
    """Plan §2: isolation only while the running API subtotal stays <= $0.17. The job's trace (planner call and
    retry, index ASR) plus the isolation plus a QA ASR reserve decides; without a job, only sources up to 30 s."""
    from studio.plan.code_plan import ISOLATION_BUDGET_USD, UNMETERED_ISOLATION_MAX_S

    assert ISOLATION_BUDGET_USD == 0.17 and UNMETERED_ISOLATION_MAX_S == 30.0
    ix = _with_audio(take_index, music_in_room=True, duration_s=50.0)
    doc, _ops, _note = plan(ix)  # no job: 50 s is over the unmetered limit
    assert doc.audio.voice.denoise == "light" and "budget: source longer than 30 s" in doc.audio.voice.notes
    doc, _ops, _note = plan(ix, job=job)  # a job with nothing spent yet: $0.10 isolation + $0.009 reserve fits
    assert doc.audio.voice.denoise == "isolate"
    # a planner call with its retry already in the trace: $0.10 + $0.10 isolation passes the budget
    job.trace("model_call", role="planner", stage="plan", model="claude-sonnet-5-5", input_tokens=5000,
              output_tokens=9000)
    doc, _ops, _note = plan(ix, job=job)
    v = doc.audio.voice
    assert v.denoise == "light" and v.isolation_provider is None
    assert "paid isolation skipped (budget: $0.100 spent + $0.100 isolation" in v.notes and "> $0.17" in v.notes
    # an isolation this job already paid for is cached (a re-plan, e.g. the pure-code second attempt): it is not
    # counted again ($0.12 spent + $0.08 again would pass; $0.12 + the reserve does not)
    job2 = Job.create("iso-paid", work_dir=job.root.parent)
    job2.trace("audio_isolation", provider="elevenlabs", seconds=60.0)
    doc, _ops, _note = plan(_with_audio(take_index, music_in_room=True, duration_s=40.0), job=job2)
    assert doc.audio.voice.denoise == "isolate"


def _measurement(face_y: float | None) -> ColorMeasurement:
    face = FaceColor(y_mean=face_y, a=8.0, b=14.0) if face_y is not None else None
    return ColorMeasurement(source=Path("mezz.mov"), frames=[
        FrameColor(word_id="w0001", t_us=400_000, y_mean=100.0, clipped_pct=0.0, crushed_pct=0.0, face=face)])


def test_colour_is_set_only_on_a_measured_fault(job: Job, take_index: TakeIndex, monkeypatch: pytest.MonkeyPatch
                                                 ) -> None:
    _doc, ops, note = plan(take_index)
    assert not ops_named(ops, "set_color") and "colour not measured (no job)" in note
    _doc, ops, note = plan(take_index, job=job)  # no mezzanine: nothing is decoded
    assert not ops_named(ops, "set_color") and "not measured (no mezzanine)" in note
    job.mezz_path.parent.mkdir(parents=True, exist_ok=True)
    job.mezz_path.write_bytes(b"")
    for face_y, ev in ((25.0, 0.5), (225.0, -0.5), (120.0, None), (None, None)):
        monkeypatch.setattr("studio.compile.color.measure_color", lambda *a, _y=face_y, **k: _measurement(_y))
        doc, ops, note = plan(take_index, job=job)
        if ev is None:
            assert not ops_named(ops, "set_color") and doc.color is None
        else:
            assert doc.color is not None and doc.color.exposure == pytest.approx(ev), face_y
            assert ("underexposed" if ev > 0 else "overexposed") in note


# ============================================================================================ plan_document
def test_plan_document_writes_versions_and_the_oplog(job: Job, take_index: TakeIndex, settings: Any) -> None:
    doc = plan_document(job, take_index, "tiktok", None, settings=settings)
    assert doc.version == job.latest_doc_version() and doc.created_by == PLAN_BY
    assert job.load_doc().model_dump() == doc.model_dump()
    assert kept(doc, take_index) == plan(take_index)[0].kept_word_ids(take_index)
    log = job.read_oplog()
    assert log and {e["by"] for e in log} == {PLAN_BY} and all(e["applied"] or e["reason"] == "no change" for e in log)
    ev = [e for e in job.read_jsonl("trace.jsonl") if e.get("event") == "code_plan"]
    assert ev and ev[-1]["rejected"] == [] and ev[-1]["version"] == doc.version and ev[-1]["decision"] is False


def test_a_re_plan_replaces_an_earlier_document_whatever_it_pinned(job: Job, take_index: TakeIndex,
                                                                    settings: Any) -> None:
    # an earlier version that keeps the false start and pins a word in it (a set_story on top of it would fail)
    old = CutDocument(version=3, parent_version=2, job_id=job.id, created_by="director",
                      segments=[Segment(id="seg007", from_word="w0001", to_word="w0041")],
                      pins=Pins(must_keep_word_ids=["w0010"]), counters={"seg": 7, "p": 40})
    job.save_doc(old)
    d = decision(caption_emphasis_word_ids=["w0018"], payoff=rng("w0018"))
    doc = plan_document(job, take_index, "tiktok", d, settings=settings)
    assert doc.version == 4 and doc.parent_version == 3
    assert "w0010" not in kept(doc, take_index) and doc.pins.must_keep_word_ids == []
    assert doc.captions is not None and [w for p in doc.captions.pages for w in p.emphasis_word_ids] == ["w0018"]
    ev = [e for e in job.read_jsonl("trace.jsonl") if e.get("event") == "code_plan"]
    assert ev[-1]["rejected"] == []


def test_the_radio_gate_blocks_a_broken_story(job: Job, take_index: TakeIndex, settings: Any,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    # the real gate: a story that ends on the cut-off "restr-" fails it
    broken = CutDocument(segments=[Segment(id="seg001", from_word="w0001", to_word="w0013")])
    blockers = radio_check(broken, take_index)
    assert blockers and "restr-" in blockers[0]
    with pytest.raises(RadioBlocked) as e:
        radio_check(broken, take_index, raise_on_block=True)
    assert e.value.blockers == blockers
    # plan_document raises it (the pipeline then falls back), unless asked not to check
    monkeypatch.setattr(cp, "radio_blockers", lambda doc, ix: ["a chopped word is kept"])
    with pytest.raises(RadioBlocked) as e:
        plan_document(job, take_index, "tiktok", None, settings=settings)
    assert e.value.blockers == ["a chopped word is kept"]
    assert plan_document(job, take_index, "tiktok", None, settings=settings, check_radio=False).segments


def test_code_plan_errors_when_nothing_can_be_kept(job: Job, take_index: TakeIndex, settings: Any) -> None:
    abandoned = build(S(words("We were going to"), complete=False), S(words("and then the"), complete=False))
    with pytest.raises(CodePlanError, match="every word"):
        code_plan(abandoned, platform="tiktok", decision=None)
    with pytest.raises(CodePlanError, match="no words"):
        code_plan(take_index.model_copy(update={"words": [], "sentences": [], "clusters": [], "gaps": []}),
                  platform="tiktok", decision=None)
    with pytest.raises(CodePlanError):
        plan_document(job, abandoned, "tiktok", None, settings=settings)


def test_the_brief_hook_skips_a_series_label_and_the_cta_comes_from_the_decision() -> None:
    """d030/var-silences open with the spoken label "Part one.": it is not the hook (the first kept line that says
    something is). The brief's CTA is the decision's range, verbatim, when the lexicon does not know the wording."""
    ix = build(S(words("Part one.")), S(words("Everyone says you need a story first.")),
               S(words("Here is why that is wrong.")), S(words("Tell your sister about this trick.")))
    doc, _ops, _note = plan(ix)
    assert doc.brief.hook == "Everyone says you need a story first." and doc.brief.cta == ""
    last = [w.id for w in ix.words if w.sentence_id == "s004"]
    doc2, _ops, _note = plan(ix, decision(cta=rng(last[0], last[-1])))
    assert doc2.brief.cta == "Tell your sister about this trick."
    assert set(last) <= set(doc2.pins.cta_word_ids)


def _stop_mid_line(ix: TakeIndex, *, tail_after_s: float) -> TakeIndex:
    """The fixture with its last word unpunctuated ("week") and the recording ending ``tail_after_s`` after it."""
    words = [w.model_copy(update={"text": "week"}) if w.id == "w0041" else w for w in ix.words]
    end = words[-1].end_us + round(tail_after_s * 1e6)
    media = ix.media.model_copy(update={"duration_us": end})
    return ix.model_copy(update={"words": words, "media": media})


def test_a_last_line_the_recording_cuts_off_is_dropped_even_when_it_is_the_cta(take_index: TakeIndex) -> None:
    """Regression (real d030, standard tier): the take stops mid-line ("Start with who buys, what they actually"),
    the index marks the sentence complete for lack of later evidence, and the pure-code cut ended mid-sentence."""
    cut = _stop_mid_line(take_index, tail_after_s=0.2)
    doc, _ops, note = plan(cut)
    assert not set(wids(29, 41)) & set(kept(doc, cut))  # s005 gone, even though it is the spoken CTA
    assert "cut off by the end of the recording" in {r.from_word: r.reason for r in doc.removed}["w0029"]
    assert kept(doc, cut)[-1] == "w0028"  # the story ends on the last finished sentence
    # a finished last line, or a recording that runs on after it, is kept
    assert set(wids(29, 41)) <= set(kept(plan(take_index)[0], take_index))
    later = _stop_mid_line(take_index, tail_after_s=2.0)
    assert set(wids(29, 41)) <= set(kept(plan(later)[0], later))
    # never the only line: a one-sentence take the ASR left unpunctuated ships as spoken instead of shipping nothing
    one = build(S(words("hey everyone check out my new studio", end_gap=0)))
    assert kept(plan(one)[0], one) == wids(1, 7)


def test_a_trim_cuts_the_series_label_and_the_cut_opens_on_the_claim() -> None:
    """Regression (real d030, standard tier): the ASR merged "Part one:" into the hook's sentence and the planner,
    able only to drop whole sentences, dropped it: the video opened on "They're wrong." The decision now trims the
    label at its colon and the story opens on the claim."""
    ix = build(S([T("Part"), T("one:", gap=220), *words("Everyone says you need an authentic brand story.")]),
               S(words("They're wrong.")), S(words("Your story is just the frame you hang the proof on.")))
    hook = "Everyone says you need an authentic brand story, but they're wrong."
    d = decision(hook_promise=hook, trim_words=[{"from_word": "w0001", "to_word": "w0002", "reason": "label"}])
    doc, _ops, _note = plan(ix, d)
    assert kept_text(doc, ix).startswith("Everyone says you need an authentic brand story. They're wrong.")
    assert {r.from_word: r.reason for r in doc.removed}["w0001"] == "label: trimmed 'Part one:' from s001"
    assert radio_check(doc, ix) == []
    # a trim the validator refuses ("Part one: Everyone | says" runs on) is skipped with a note, never applied
    bad = decision(hook_promise=hook, trim_words=[{"from_word": "w0001", "to_word": "w0003", "reason": "preamble"}])
    doc2, _ops2, note2 = plan(ix, bad)
    assert kept(doc2, ix)[:4] == wids(1, 4)
    assert "trim w0001-w0003 skipped" in note2 and "ends mid-clause" in note2
