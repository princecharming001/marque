"""Recording dropouts (digital silence inside a take): perception, compile pads, QA gates, ops messages.

Regression for the var-silences E2E: 20 s mutes chopped "meaningfu-|-nough" and "actua-|[lost vowel] care", the
Take Index called them complete words and sentence ends, pads reached into the lost vowel, and no gate caught it.
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest
from gaps_prosody_synth import SR, build_take

from studio.compile.audio import soften_dropout_edges
from studio.compile.timeline import compile as compile_timeline
from studio.doc.model import CutDocument, Segment
from studio.doc.ops import apply_ops
from studio.perception import gaps as G
from studio.perception import takes
from studio.perception.index import TakeIndex, Word, word_id
from studio.qa import metrics as M


# ---------------------------------------------------------------------------------------------- synthetic take
def _take():
    """start 0.40 · sound 1.00 · meaningful 1.60-2.30 (zeros from 2.00: its end is lost) · [dropout 2.00-4.00] ·
    enough. 3.80-4.30 (its onset lost: sound resumes at 4.00 straight into it) · But 4.60 · actually 4.95-5.40 (zeros
    from 5.20) · [dropout 5.20-7.00] · a lost word 6.80-7.10 (only 7.00-7.10 survives) · dip · care 7.16-7.50 ·
    solving. 7.60-8.00."""
    ev = [
        {"type": "word", "text": "start", "t": 0.40, "dur": 0.40, "f0": 130, "db": -20},
        {"type": "word", "text": "sound", "t": 1.00, "dur": 0.40, "f0": 125, "db": -20},
        {"type": "word", "text": "meaningful", "t": 1.60, "dur": 0.70, "f0": 120, "db": -20},
        {"type": "word", "text": "enough.", "t": 3.80, "dur": 0.50, "f0": 128, "db": -20},
        {"type": "word", "text": "But", "t": 4.60, "dur": 0.30, "f0": 130, "db": -20},
        {"type": "word", "text": "actually", "t": 4.95, "dur": 0.45, "f0": 122, "db": -20},
        {"type": "word", "text": "lost", "t": 6.80, "dur": 0.30, "f0": 118, "db": -20},
        {"type": "word", "text": "care", "t": 7.16, "dur": 0.34, "f0": 126, "db": -20},
        {"type": "word", "text": "solving.", "t": 7.60, "dur": 0.40, "f0": 118, "db": -21},
    ]
    take = build_take(ev, total_s=9.0, floor_db=-62.0)
    x = take.x
    x[round(2.00 * SR):round(4.00 * SR)] = 0.0
    x[round(5.20 * SR):round(7.00 * SR)] = 0.0
    # what a real ASR hands us: the chopped words at full-word times, the onset of "enough." late, no "lost" word
    asr = [("start", 0.40, 0.80), ("sound", 1.00, 1.40), ("meaningful", 1.60, 2.03), ("enough.", 4.15, 4.30),
           ("But", 4.60, 4.90), ("actually", 4.95, 5.22), ("care", 7.16, 7.50), ("solving.", 7.60, 8.00)]
    words = [Word(id=word_id(i + 1), text=t, start_us=round(a * 1e6), end_us=round(b * 1e6))
             for i, (t, a, b) in enumerate(asr)]
    return x, words


@pytest.fixture(scope="module")
def analysis():
    x, words = _take()
    feat = G.compute_features(x, SR)
    return feat, G.analyze_gaps(feat, words)


def _w(res, text):
    return next(w for w in res.words if w.text == text)


def test_dropouts_are_found_with_their_cut_edges(analysis) -> None:
    feat, _res = analysis
    d = feat.dropouts
    assert [(round(a.start_us / 1e3), round(a.end_us / 1e3)) for a in d] == [(2000, 4000), (5200, 7000)]
    assert all(x.cut_before and x.cut_after for x in d)


def test_words_cut_by_a_dropout_are_marked_and_snapped_to_the_edge(analysis) -> None:
    _feat, res = analysis
    m, e, a = _w(res, "meaningful"), _w(res, "enough."), _w(res, "actually")
    assert (m.kind, m.truncated, m.end_us) == ("cutoff", "end", 2_000_000) and m.display() == "meaningful-"
    # the resumed sound runs straight into "enough.": it is the word's own (cut-off) onset
    assert (e.kind, e.truncated) == ("cutoff", "start") and abs(e.start_us - 4_000_000) <= 1_000
    assert e.display() == "-enough."
    assert (a.truncated, a.end_us) == ("end", 5_200_000)
    # "care" is whole: the sound before it is a fragment of a lost word, separated by a dip
    c = _w(res, "care")
    assert c.truncated is None and c.kind == "word" and c.start_us > 7_100_000
    for text in ("start", "sound", "But", "solving."):
        assert _w(res, text).truncated is None


def test_gaps_record_the_dropout_and_the_lost_fragment(analysis) -> None:
    _feat, res = analysis
    g1 = next(g for g in res.gaps if g.after_word_id == _w(res, "meaningful").id)
    assert g1.is_dropout and g1.dropouts_us == [(2_000_000, g1.end_us)] and not g1.sound_us
    assert g1.dropouts_us[0][0] <= g1.snap_us <= g1.dropouts_us[0][1]
    g2 = next(g for g in res.gaps if g.before_word_id == _w(res, "care").id)
    assert g2.is_dropout and len(g2.sound_us) == 1
    fa, fb = g2.sound_us[0]
    assert abs(fa - 7_000_000) <= 10_000 and 7_080_000 <= fb <= _w(res, "care").start_us


def test_a_dropout_never_ends_a_sentence(analysis) -> None:
    _feat, res = analysis
    drops = [d for g in res.gaps for d in g.dropouts_us]
    _ws, sents = takes.segment_sentences(res.words, dropouts=drops)
    texts = [s.text for s in sents]
    assert texts[0].startswith("start sound meaningful") and texts[0].endswith("enough.")
    assert texts[1].startswith("But actually") and texts[1].endswith("solving.")
    assert all(s.complete for s in sents)  # "-enough." lost its onset, not its end: the line was delivered
    # without the dropout information the 2 s mute split the sentence mid-clause (the old behaviour)
    _ws2, old = takes.segment_sentences(res.words)
    assert old[0].text == "start sound meaningful"


def test_soften_dropout_edges_fades_into_and_out_of_the_zeros(analysis, take_index: TakeIndex) -> None:
    x, _words = _take()
    g = take_index.gaps[3].model_copy(update={"start_us": 1_900_000, "end_us": 4_100_000, "snap_us": 3_000_000,
                                              "dropouts_us": [(2_000_000, 4_000_000)]})
    ix = take_index.model_copy(update={"gaps": [g]})
    y = soften_dropout_edges(x, SR, ix)
    a, b = 2 * SR, 4 * SR
    assert abs(y[a - 1]) < 0.05 * np.max(np.abs(x[a - 400:a])) and np.array_equal(y[a - 2000:a - 500],
                                                                                    x[a - 2000:a - 500])
    assert abs(y[b]) == 0.0 and abs(y[b + 5]) < abs(x[b + 5]) + 1e-12 and np.array_equal(y[b + 1000:], x[b + 1000:])
    assert soften_dropout_edges(x, SR, take_index) is x  # nothing to do


# ---------------------------------------------------------------------------------------------- compile pads
def _with_gap(ix: TakeIndex, gid: str, **upd) -> TakeIndex:
    return ix.model_copy(update={"gaps": [g.model_copy(update=upd) if g.id == gid else g for g in ix.gaps]})


def test_lead_pad_never_reaches_into_untranscribed_sound(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    g = take_index.gap("g0004")  # the cut before seg002 (w0013 | w0014)
    onset = take_index.word("w0014").start_us
    base = compile_timeline(cut_doc, take_index)
    p0 = next(p for p in base.segments if p.seg_id == "seg002")
    assert p0.audio_src_in_us <= onset - 40_000  # normal lead pad (~60 ms)
    frag = (g.end_us - 120_000, g.end_us - 20_000)
    ix = _with_gap(take_index, "g0004", sound_us=[frag])
    tl = compile_timeline(cut_doc, ix)
    p = next(p for p in tl.segments if p.seg_id == "seg002")
    frame_us = 1_000_000 / 30
    assert p.audio_src_in_us >= frag[1] - frame_us  # at most the frame-grid remainder of the fragment
    integ = M.word_integrity(tl, ix)
    assert not integ.sound_leaks and not integ.clipped
    # the old pad would have played ~40 ms of it: reported (and muted by the audio stage)
    integ_old = M.word_integrity(base, ix)
    assert integ_old.sound_leaks and integ_old.sound_leaks[0]["gap_id"] == "g0004"
    assert integ_old.sound_leaks[0]["muted"]


def test_the_audio_stage_mutes_pad_sound_under_room_tone(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    import numpy as np

    from studio.compile.audio import assemble_dialogue

    g = take_index.gap("g0004")
    frag = (g.end_us - 120_000, g.end_us - 20_000)
    ix = _with_gap(take_index, "g0004", sound_us=[frag])
    tl = compile_timeline(cut_doc, take_index)  # the old compile: its lead pad reaches into the fragment
    sr = tl.sample_rate
    n_src = round(ix.media.duration_us * sr / 1e6) + sr
    voice = np.full(n_src, 0.1)
    res = assemble_dialogue(tl, voice, sr, index=ix)
    seg = next(p for p in tl.segments if p.seg_id == "seg002")
    t_frag = float(seg.out_start) + (frag[1] - 12_000 - seg.src_in_us) / 1e6  # inside the fragment, in the pad
    t_word = float(seg.out_start) + (take_index.word("w0014").start_us + 50_000 - seg.src_in_us) / 1e6
    assert abs(res.audio[round(t_frag * sr)]) < 0.01 and abs(res.audio[round(t_word * sr)] - 0.1) < 0.01
    assert any("muted under room tone" in w for w in res.warnings)
    plain = assemble_dialogue(tl, voice, sr, index=take_index)
    assert abs(plain.audio[round(t_frag * sr)] - 0.1) < 0.02


def test_tail_pad_stops_before_untranscribed_sound(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    end = take_index.word("w0008").end_us
    ix = _with_gap(take_index, "g0003", sound_us=[(end + 40_000, end + 200_000)])
    tl = compile_timeline(cut_doc, ix)
    p = next(p for p in tl.segments if p.seg_id == "seg001")
    assert end <= p.audio_src_out_us <= end + 40_000 + 34_000


def test_a_pause_trim_removes_untranscribed_sound_whole(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    g = take_index.gap("g0009")  # w0028 | w0029 (seg003 → seg004 continue the source)
    frag = (g.start_us + 200_000, g.start_us + 320_000)
    ix = _with_gap(take_index, "g0009", sound_us=[frag])
    doc, res = apply_ops(cut_doc, [{"op": "set_gap", "gap_id": "g0009", "ms": 400}], ix)
    assert all(r.applied for r in res), res
    tl = compile_timeline(doc, ix)
    a = next(p for p in tl.segments if p.word_ids and p.word_ids[-1] == "w0028")
    b = next(p for p in tl.segments if p.word_ids and p.word_ids[0] == "w0029")
    frame_us = 1_000_000 / 30
    assert a.audio_src_out_us <= frag[0] + frame_us and b.audio_src_in_us >= frag[1] - frame_us


# ---------------------------------------------------------------------------------------------- QA
def test_kept_words_the_recording_chops_are_reported(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    words = [w.model_copy(update={"kind": "cutoff", "truncated": "end"}) if w.id == "w0008" else w
             for w in take_index.words]
    ix = take_index.model_copy(update={"words": words})
    tl = compile_timeline(cut_doc, ix)
    integ = M.word_integrity(tl, ix)
    assert integ.truncated == [{"word_id": "w0008", "text": "much.", "side": "end"}]


def test_dropout_edges_inside_kept_audio_are_mapped_to_output_time(take_index: TakeIndex,
                                                                   cut_doc: CutDocument) -> None:
    w = take_index.word("w0016")  # inside seg002
    edge = (w.start_us + w.end_us) // 2
    g = take_index.gap("g0005")
    ix = _with_gap(take_index, "g0005", dropouts_us=[(g.start_us + 10_000, g.start_us + 60_000)])
    tl = compile_timeline(cut_doc, ix)
    edges = M.dropout_edges_in_output(tl, ix)
    assert len(edges) == 2 and all(isinstance(t, Fraction) for t, _l, _r in edges)
    seg = next(p for p in tl.segments if p.seg_id == "seg002")
    t0 = Fraction(seg.out_start) + Fraction(g.start_us + 10_000 - seg.src_in_us, 1_000_000)
    assert edges[0][0] == t0 and edges[0][1] == "w0017" and edges[0][2] == "w0018"
    del edge


def test_seams_are_classified_from_punctuation(take_index: TakeIndex) -> None:
    assert M._punct_start(take_index, "w0009")  # after "much."
    assert M._punct_start(take_index, "w0015") is False  # "real" after "The"
    assert M._punct_start(take_index, "w0020")  # "keep" after "Um," (a clause edge)
    assert M._punct_start(take_index, "w0001")


# ---------------------------------------------------------------------------------------------- ops
def test_set_gap_messages_explain_cuts_and_removed_material(take_index: TakeIndex, cut_doc: CutDocument) -> None:
    doc = cut_doc.model_copy(update={"segments": [s for s in cut_doc.segments if s.id != "seg002"]})
    _d, res = apply_ops(doc, [{"op": "set_gap", "gap_id": "g0005", "ms": 50}], take_index)
    assert not res[0].applied and "removed material" in res[0].reason
    # g0009 at a real cut (seg003 moved after seg004)
    moved, r1 = apply_ops(cut_doc, [{"op": "move_segment", "seg_id": "seg003", "after": "seg004"}], take_index)
    assert r1[0].applied
    _d2, r2 = apply_ops(moved, [{"op": "set_gap", "gap_id": "g0009", "ms": 100}], take_index)
    assert not r2[0].applied and "sits at a CUT" in r2[0].reason and "cut_words" in r2[0].reason


def test_a_framing_split_keeps_the_pause_target_at_the_join(take_index: TakeIndex) -> None:
    doc = CutDocument(version=1, job_id="j", segments=[Segment(id="seg001", from_word="w0020", to_word="w0041")],
                      counters={"seg": 1})
    doc, r0 = apply_ops(doc, [{"op": "set_gap", "gap_id": "g0009", "ms": 150}], take_index)
    assert r0[0].applied and doc.segments[0].gap_overrides == {"g0009": 150}
    split, r1 = apply_ops(doc, [{"op": "set_framing", "from_word": "w0029", "to_word": "w0041", "scale": 1.15}],
                          take_index)
    assert r1[0].applied, r1
    left, right = split.segments
    assert (left.to_word, right.from_word) == ("w0028", "w0029")
    assert left.gap_overrides == {"g0009": 150}  # kept at the continuous join, not dropped
    tl = compile_timeline(split, take_index)
    a = next(p for p in tl.segments if p.word_ids and p.word_ids[-1] == "w0028")
    b = next(p for p in tl.segments if p.word_ids and p.word_ids[0] == "w0029")
    kept_ms = (float(tl.word_map["w0029"].out_start) - float(tl.word_map["w0028"].out_end)) * 1000
    assert kept_ms < 300 and b.audio_src_in_us > a.audio_src_out_us  # trimmed: a real removal at the join
    # once the join becomes a real cut the target is dropped with a warning
    moved, r2 = apply_ops(split, [{"op": "move_segment", "seg_id": left.id, "after": right.id}], take_index)
    assert r2[0].applied and all(not s.gap_overrides for s in moved.segments)
    assert any("dropped" in w for w in r2[0].warnings)
    # a set_gap on a continuous join is accepted too
    again, r3 = apply_ops(split, [{"op": "set_gap", "gap_id": "g0009", "ms": 250}], take_index)
    assert r3[0].applied and again.segments[0].gap_overrides == {"g0009": 250}


def test_the_video_lead_in_may_reach_into_muted_sound(take_index: TakeIndex) -> None:
    # real-take40: a mumble fills the whole 0.7 s before "I run…"; bounding the head by it started the first word at
    # 0.06 s (AAC priming then left digital silence under it: invariant 9)
    doc = CutDocument(version=1, job_id="j", segments=[Segment(id="seg001", from_word="w0001", to_word="w0008")],
                      counters={"seg": 1})
    onset = take_index.word("w0001").start_us
    ix = _with_gap(take_index, "g0001", sound_us=[(20_000, onset - 5_000)])
    tl = compile_timeline(doc, ix)
    assert float(tl.word_map["w0001"].out_start) >= 0.1
    import numpy as np

    from studio.compile.audio import assemble_dialogue

    sr = tl.sample_rate
    voice = np.full(round(ix.media.duration_us * sr / 1e6) + sr, 0.1)
    res = assemble_dialogue(tl, voice, sr, index=ix)
    t_mid = float(tl.word_map["w0001"].out_start) / 2
    assert abs(res.audio[round(t_mid * sr)]) < 0.01  # the mumble under the lead-in is muted (room tone fills it)


def test_check_seams_reports_what_the_ear_gets(job, take_index: TakeIndex, cut_doc: CutDocument) -> None:
    """The fine cut's check_seams audio facts: compiled pause, clause edge, chopped words, click detector."""
    import soundfile as sf
    from director_script import ScriptedDirector
    from qa_fixtures import synth_source_audio

    from studio.agent.director import Director
    from studio.compile.timeline import piece_is_continuous

    words = [w.model_copy(update={"kind": "cutoff", "truncated": "start"}) if w.id == "w0014" else w
             for w in take_index.words]
    ix = take_index.model_copy(update={"words": words})
    sf.write(str(job.audio_path), synth_source_audio(ix).astype("float32"), 48_000, subtype="FLOAT")
    d = Director(job, ix, model=ScriptedDirector({}).model(), sleep=lambda _s: None)
    tl = compile_timeline(cut_doc, ix)
    pairs = [(a, b) for a, b in zip(tl.segments, tl.segments[1:], strict=False) if not piece_is_continuous(a, b)]
    facts, _head = d._seam_audio_facts(tl, pairs)
    first = facts[0]  # seg001 → seg002: "much." | "The"
    assert "compiled pause" in first and "between sentences" in first and "CHOPPED" in first
    assert "click" in first.lower()
