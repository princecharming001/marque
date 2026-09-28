"""Compiler × measured breath spans (``Gap.breaths_us``): breaths are kept or removed whole, never cut.

Doctrine (skills/editing/voice-and-loudness.md): "If pacing tightens a gap below the breath's length,
remove the breath whole, cutting at its edges. Never cut mid-breath." and the worked example (target 0.4 s,
inhale 0.35 s → the breath stays whole, the silence before it goes).
"""

from __future__ import annotations

from studio.compile.timeline import CompileOptions, compile
from studio.doc.model import Segment

MARGIN_US = round(CompileOptions().breath_margin_ms * 1000)


def _with_breath(ix, gap_id: str, spans: list[tuple[int, int]]):
    gaps = [g.model_copy(update={"breaths_us": spans, "has_breath": True}) if g.id == gap_id else g for g in ix.gaps]
    return ix.model_copy(update={"gaps": gaps})


def _one_segment_doc(doc, gap_id: str, ms: int):
    seg = Segment(id="seg001", from_word="w0001", to_word="w0011", gap_overrides={gap_id: ms})
    return doc.model_copy(update={"segments": [seg], "inserts": [], "texts": [], "captions": None})


def _pieces(tl):
    return [(s.src_in_us, s.src_out_us) for s in tl.segments]


def test_silence_before_a_preonset_breath_goes_first(take_index, cut_doc):
    # g0003: w0008 ends 2.748 s, w0009 starts 3.448 s; inhale 3.10–3.40 right before the onset
    ix = _with_breath(take_index, "g0003", [(3_100_000, 3_400_000)])
    tl = compile(_one_segment_doc(cut_doc, "g0003", 500), ix)
    (_, out0), (in1, _) = _pieces(tl)
    assert 2_748_000 < out0 < in1 <= 3_100_000 - MARGIN_US  # only silence removed, inhale kept whole
    assert abs((in1 - out0) - 200_000) <= 34_000  # ~200 ms removed (frame grid)


def test_breath_is_never_cut_when_the_target_is_tight(take_index, cut_doc):
    # the inhale runs up to the next word's lead pad: it cannot be removed whole, so it stays whole
    ix = _with_breath(take_index, "g0003", [(3_100_000, 3_400_000)])
    tl = compile(_one_segment_doc(cut_doc, "g0003", 200), ix)
    (_, out0), (in1, _) = _pieces(tl)
    assert in1 <= 3_100_000 - MARGIN_US
    assert (3_448_000 - in1) + (out0 - 2_748_000) >= 300_000  # the pause is longer than asked, breath intact


def test_breath_shorter_target_removes_the_breath_whole(take_index, cut_doc):
    # a mid-pause breath 2.95–3.20 with room before the next word; target 150 ms < breath 250 ms
    ix = _with_breath(take_index, "g0003", [(2_950_000, 3_200_000)])
    tl = compile(_one_segment_doc(cut_doc, "g0003", 150), ix)
    (_, out0), (in1, _) = _pieces(tl)
    assert out0 <= 2_950_000 - MARGIN_US and in1 >= 3_200_000 + MARGIN_US  # removed at its edges


def test_legacy_breath_gap_without_spans_is_unchanged(take_index, cut_doc):
    tl = compile(_one_segment_doc(cut_doc, "g0003", 500), take_index)
    assert len(tl.segments) == 2  # the old snap-point rule still shortens hand-built breath gaps


def test_segment_start_takes_a_preonset_inhale_whole(take_index, cut_doc):
    # seg002 starts at w0014 (5.748 s) after a cut; the inhale 5.30–5.60 straddles the snap point (5.388 s)
    base = compile(cut_doc, take_index)
    assert base.segments[1].src_in_us > 5_300_000  # without spans the lead starts mid-inhale
    ix = _with_breath(take_index, "g0004", [(5_300_000, 5_600_000)])
    tl = compile(cut_doc, ix)
    seg2 = next(s for s in tl.segments if "w0014" in s.word_ids)
    assert seg2.src_in_us <= 5_300_000 - MARGIN_US + 34_000 and seg2.audio_src_in_us <= 5_300_000


def test_segment_end_stops_before_a_trailing_exhale(take_index, cut_doc):
    # seg001 ends at w0008 (2.748 s); an exhale 2.80–3.00 would be cut by the 120 ms tail pad
    ix = _with_breath(take_index, "g0003", [(2_800_000, 3_000_000)])
    tl = compile(cut_doc, ix)
    seg1 = tl.segments[0]
    assert 2_748_000 <= seg1.audio_src_out_us <= 2_800_000 - MARGIN_US + 1
