"""Compiler × measured blinks (``index.visual.events``): the first frame and the face frame a full-screen/card
insert returns to are never mid-blink (doctrine broll.md "Return frame: come back on a live face (eyes open,
not mid-blink)"; platforms.md "a blink on frame 0 ships a bad cover")."""

from __future__ import annotations

from fractions import Fraction

from studio.compile.timeline import CompileOptions, compile
from studio.perception.index import VisualEvent


def _with_blink(ix, start_us: int, end_us: int):
    vis = ix.visual.model_copy(update={"events": [*ix.visual.events,
                                                  VisualEvent(kind="blink", start_us=start_us, end_us=end_us)]})
    return ix.model_copy(update={"visual": vis})


def _src_us(tl, out_t: Fraction) -> int:
    seg = tl.segment_at(out_t)
    assert seg is not None
    return seg.out_to_src_us(out_t)


def _in(us: int, a: int, b: int) -> bool:
    return a <= us < b


def test_first_frame_avoids_a_blink(take_index, cut_doc):
    base = compile(cut_doc, take_index)
    s0 = base.segments[0].src_in_us
    ix = _with_blink(take_index, s0 - 20_000, s0 + 60_000)
    tl = compile(cut_doc, ix)
    first = tl.segments[0].src_in_us
    assert not _in(first, s0 - 20_000, s0 + 60_000)
    assert take_index.words[0].start_us - first >= 100_000  # first word still ≥ 0.1 s in


def test_insert_returns_to_a_live_face(take_index, cut_doc):
    base = compile(cut_doc, take_index)
    ins = base.inserts[0]  # i001: a card over w0020..w0024
    assert ins.mode == "card"
    ret = _src_us(base, ins.out_end)
    ix = _with_blink(take_index, ret - 30_000, ret + 90_000)
    tl = compile(cut_doc, ix)
    moved = tl.inserts[0]
    assert moved.out_end != ins.out_end
    assert not _in(_src_us(tl, moved.out_end), ret - 30_000, ret + 90_000)
    assert abs(moved.out_end - ins.out_end) <= Fraction(CompileOptions().blink_guard_frames, 30)
    span = tl.word_span("w0024")
    assert span is not None and moved.out_end >= span.out_end  # still covers its anchor words


def test_guard_off_keeps_the_old_edges(take_index, cut_doc):
    base = compile(cut_doc, take_index)
    s0 = base.segments[0].src_in_us
    ix = _with_blink(take_index, s0 - 20_000, s0 + 60_000)
    tl = compile(cut_doc, ix, options=CompileOptions(blink_guard_frames=0))
    assert tl.segments[0].src_in_us == s0
