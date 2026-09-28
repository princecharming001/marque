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


def test_jl_picture_edit_moves_onto_a_blink(take_index, cut_doc):
    """Doctrine cutting-and-pacing.md: a J/L picture cut may sit anywhere in the shared silent gap — pick a blink.
    The Director's lead is nudged (within half the lead / 3 frames) so the cut lands on a measured blink."""
    from studio.doc.model import SeamTreatment

    segs = list(cut_doc.segments)
    segs[1] = segs[1].model_copy(update={"seam_in": SeamTreatment(kind="jcut", lead_ms=200)})  # 6 frames at 30 fps
    doc = cut_doc.model_copy(update={"segments": segs})
    plain = compile(doc, take_index, options=CompileOptions(jl_snap_frames=0))
    a0 = plain.segments[0]
    assert a0.src_out_us - a0.audio_src_out_us == 200_000  # no blink nearby: exactly the lead asked for
    # a blink on the outgoing face 2 frames after that picture edit
    t = a0.src_out_us + 2 * 33_333
    ix = _with_blink(take_index, t - 10_000, t + 60_000)
    tl = compile(doc, ix)
    a = tl.segments[0]
    shift_frames = round((a.src_out_us - a.audio_src_out_us) / 33_333.33)
    assert shift_frames in (7, 8, 9)  # the last outgoing frame now sits in the blink
    last_frame = a.src_out_us - 33_333
    assert _in(last_frame, t - 10_000 - 16_667, t + 60_000 + 16_667)
    assert tl.word_map == plain.word_map  # audio (and every word's heard time) is untouched
    assert tl.segments[1].seam_in.lead_ms == round(shift_frames * 1000 / 30)


def test_jl_reads_frame_accurate_eyes_from_the_dense_analysis(take_index, cut_doc, tmp_path):
    """``index/visual_dense.npz`` (per-frame eyes/mouth) is used when the job has it: a closure seen only in the
    dense signal (no blink event) still pulls the J/L picture edit onto it."""
    import numpy as np

    from studio.doc.model import SeamTreatment
    from studio.jobs import Job

    segs = list(cut_doc.segments)
    segs[1] = segs[1].model_copy(update={"seam_in": SeamTreatment(kind="jcut", lead_ms=200)})
    doc = cut_doc.model_copy(update={"segments": segs})
    job = Job.create("dense-job", work_dir=tmp_path)
    job.save_media_info(take_index.media)
    job.save_index(take_index)
    plain = compile(doc, take_index, job=job)
    a0 = plain.segments[0]
    t = np.arange(0, take_index.media.duration_us, 33_333, dtype=np.int64)
    eyes = np.full(t.size, 0.9, np.float32)
    closed = a0.src_out_us + 2 * 33_333  # two frames past the requested picture edit
    eyes[np.abs(t - closed) < 40_000] = 0.1
    mouth = np.full(t.size, 0.2, np.float32)
    np.savez_compressed(job.index_dir / "visual_dense.npz", t_us=t, eyes_open=eyes, mouth_open=mouth,
                        face=np.ones(t.size, bool), fps=np.array([30, 1], np.int64))
    tl = compile(doc, take_index, job=job)
    a = tl.segments[0]
    assert a.src_out_us > a0.src_out_us and abs((a.src_out_us - 33_333) - closed) <= 40_000
    assert tl.word_map == plain.word_map
