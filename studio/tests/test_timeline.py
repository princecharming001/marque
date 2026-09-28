"""Compiler tests (keyless, offline, no media): CutDocument + TakeIndex → Timeline."""

from __future__ import annotations

from fractions import Fraction as F
from itertools import pairwise

import pytest

from studio.compile.models import Timeline
from studio.compile.timeline import (
    CompileError,
    CompileOptions,
    audio_seams,
    base_window,
    compile,
    compile_timeline,
    crop_window,
    framing_state,
    map_source_point,
    piece_is_continuous,
    region_for_time,
)
from studio.doc.model import (
    AssetRef,
    CardSpec,
    Framing,
    Insert,
    Licence,
    MusicSpec,
    Point,
    SeamTreatment,
    Segment,
)
from studio.perception.index import FaceTrack, FaceTrackPoint, TakeIndex

US = 1_000_000


def _sec(us: int) -> F:
    return F(us, US)


def _audio_in(seg) -> F:
    return seg.out_start - _sec(seg.src_in_us - seg.audio_src_in_us) / F(repr(seg.speed))


def _audio_out(seg) -> F:
    return seg.out_end + _sec(seg.audio_src_out_us - seg.src_out_us) / F(repr(seg.speed))


def _with_segments(doc, segments, **extra):
    return doc.model_copy(update={"segments": segments, **extra})


def _check_invariants(tl: Timeline, index: TakeIndex, doc) -> None:
    fps = tl.fps
    assert tl.segments, "no segments"
    t = F(0)
    for s in tl.segments:
        assert s.out_start == t, "segments must be contiguous in output time"
        assert (s.out_start * fps).denominator == 1 and (s.out_end * fps).denominator == 1
        assert s.src_out_us > s.src_in_us and s.audio_src_out_us > s.audio_src_in_us
        t = s.out_end
    assert tl.duration == t
    assert tl.frame_count == sum(round((s.out_end - s.out_start) * fps) for s in tl.segments)
    # audio windows abut exactly (J/L move the seam, never overlap or leave holes)
    for a, b in pairwise(tl.segments):
        assert abs(_audio_out(a) - _audio_in(b)) <= F(2, US)
    assert abs(_audio_in(tl.segments[0])) <= F(2, US)
    assert abs(_audio_out(tl.segments[-1]) - tl.duration) <= F(2, US)
    # seams are output boundaries of true cuts
    starts = {s.out_start for s in tl.segments[1:]}
    assert set(tl.seams) <= starts
    # word map: every word listed; kept words mapped, removed None; order follows the story
    kept = doc.kept_word_ids(index)
    assert set(tl.word_map) == {w.id for w in index.words}
    for w in index.words:
        assert (tl.word_map[w.id] is not None) == (w.id in kept)
    prev = F(-1)
    for wid in kept:
        span = tl.word_map[wid]
        assert span.out_start >= prev - F(1, 1000) and span.out_end >= span.out_start
        prev = span.out_start
    # the word map is the exact image of the measured source times through its piece
    for s in tl.segments:
        sp = F(repr(s.speed))
        for wid in s.word_ids:
            w = index.word(wid)
            exp = s.out_start + (_sec(w.start_us) - _sec(s.src_in_us)) / sp
            assert abs(tl.word_map[wid].out_start - exp) <= F(3, US)


# ============================================================================================ basics
def test_fixture_compiles_with_invariants(take_index, cut_doc):
    tl = compile(cut_doc, take_index)
    _check_invariants(tl, take_index, cut_doc)
    assert tl.fps == 30 and tl.width == 1080 and tl.height == 1920
    assert tl.doc_version == 1 and tl.job_id == "fixture-job"
    assert [s.seg_id for s in tl.segments] == ["seg001", "seg002", "seg003", "seg004"]
    assert compile_timeline(cut_doc, take_index) == tl
    wide = take_index.media.model_copy(update={"width": 1920, "height": 1080})
    tw = compile_timeline(cut_doc, take_index, wide)
    assert tw.duration == tl.duration and tw.word_map == tl.word_map  # geometry changes, timing does not
    assert Timeline.from_json(tl.to_json()) == tl


def test_pads_frame_grid_and_snap_bounds(take_index, cut_doc):
    tl = compile(cut_doc, take_index)
    fps = tl.fps
    ix = take_index
    for s in tl.segments:  # speed 1: every edit is on the source frame grid (zero A/V offset)
        for us in (s.src_in_us, s.src_out_us, s.audio_src_in_us, s.audio_src_out_us):
            assert abs(us * 30 / US - round(us * 30 / US)) < 1e-4
    # first word lands 0.1–0.5 s after frame 0 (head lead-in)
    first = tl.word_map["w0001"]
    assert F(1, 10) <= first.out_start <= F(1, 2)
    # last word → last frame 0.15–0.5 s
    tail = tl.duration - tl.word_map["w0041"].out_end
    assert F(15, 100) <= tail <= F(1, 2)
    s1, s2 = tl.segments[0], tl.segments[1]
    # cut after "much." keeps ~120 ms tail but never passes the snap point of g0003
    g3 = ix.gap("g0003")
    assert ix.word("w0008").end_us + 80_000 <= s1.src_out_us <= g3.snap_us
    # cut into "The" (w0014): lead pad 40–80 ms, not before the snap point of g0004
    g4 = ix.gap("g0004")
    lead = ix.word("w0014").start_us - s2.src_in_us
    assert 0 < lead <= 80_000 + 33_334 and s2.src_in_us >= g4.snap_us
    assert fps == 30


def test_continuous_join_has_no_seam(take_index, cut_doc):
    tl = compile(cut_doc, take_index)
    s3, s4 = tl.segments[2], tl.segments[3]
    assert s3.seg_id == "seg003" and s4.seg_id == "seg004"
    assert piece_is_continuous(s3, s4)
    assert s3.src_out_us == s4.src_in_us == s3.audio_src_out_us == s4.audio_src_in_us
    onset = take_index.word("w0029").start_us
    assert 0 < onset - s4.src_in_us <= 70_000  # picture change lands just before the onset
    assert s4.out_start not in tl.seams
    assert tl.seams == [tl.segments[1].out_start, tl.segments[2].out_start]
    assert audio_seams(tl) == tl.seams


def _snap_at(ix: TakeIndex, gid: str, snap_us: int) -> TakeIndex:
    gaps = [g.model_copy(update={"snap_us": snap_us}) if g.id == gid else g for g in ix.gaps]
    return ix.model_copy(update={"gaps": gaps})


def test_head_and_end_reach_past_a_hugging_snap_point(take_index, cut_doc):
    # the story starts mid-take at w0014 and ends at w0028; the snap points of the neighbouring gaps hug
    # the kept words (20 ms), which at an inner cut would force almost no pad
    ix = _snap_at(take_index, "g0004", take_index.word("w0014").start_us - 20_000)
    ix = _snap_at(ix, "g0009", take_index.word("w0028").end_us + 20_000)
    segs = [Segment(id="seg001", from_word="w0014", to_word="w0018"),
            Segment(id="seg002", from_word="w0020", to_word="w0028")]
    doc = _with_segments(cut_doc, segs, inserts=[], texts=[], captions=None)
    doc.audio.sfx = []
    tl = compile(doc, ix)
    _check_invariants(tl, ix, doc)
    # the first word still lands 0.1–0.5 s after frame 0 …
    assert F(1, 10) <= tl.word_map["w0014"].out_start <= F(1, 2)
    # … without reaching the removed cut-off's release (its end + the 120 ms tail pad)
    assert tl.segments[0].src_in_us >= ix.word("w0013").end_us + 120_000
    # the ending keeps 0.15–0.5 s after the last word, stopping short of the next word's lead pad
    tail = tl.duration - tl.word_map["w0028"].out_end
    assert F(15, 100) <= tail <= F(1, 2)
    assert tl.segments[-1].src_out_us <= ix.word("w0029").start_us - 60_000
    # an inner cut keeps the strict snap bound
    g6 = ix.gap("g0006")
    assert tl.segments[0].src_out_us <= g6.snap_us


def test_end_does_not_run_into_an_inhale(take_index, cut_doc):
    # g0006 after "restraint." (w0018) holds a breath: the ending stops at its snap point
    segs = [Segment(id="seg001", from_word="w0014", to_word="w0018")]
    doc = _with_segments(cut_doc, segs, inserts=[], texts=[], captions=None)
    doc.audio.sfx = []
    tl = compile(doc, take_index)
    assert tl.segments[-1].src_out_us <= take_index.gap("g0006").snap_us


def test_breath_before_incoming_line_is_kept_whole(take_index, cut_doc):
    # g0006 (breath) precedes the filler w0019; start a segment at w0019 → lead extends to the snap point
    segs = [Segment(id="seg001", from_word="w0001", to_word="w0008"),
            Segment(id="seg002", from_word="w0019", to_word="w0028")]
    doc = _with_segments(cut_doc, segs, inserts=[], texts=[], captions=None)
    doc.audio.sfx = []
    tl = compile(doc, take_index)
    g6 = take_index.gap("g0006")
    s2 = tl.segments[1]
    assert s2.src_in_us <= take_index.word("w0019").start_us - 250_000
    assert s2.src_in_us >= g6.snap_us - 33_334


# ============================================================================================ pause trims
def test_gap_override_trims_middle_of_pause(take_index, cut_doc):
    segs = [Segment(id="seg001", from_word="w0001", to_word="w0012", gap_overrides={"g0003": 200})]
    doc = _with_segments(cut_doc, segs, inserts=[], texts=[], captions=None)
    doc.audio.sfx = []
    tl = compile(doc, take_index)
    _check_invariants(tl, take_index, doc)
    assert len(tl.segments) == 2 and {s.seg_id for s in tl.segments} == {"seg001"}
    a, b = tl.segments
    assert tl.seams == [b.out_start]
    pause = tl.word_map["w0009"].out_start - tl.word_map["w0008"].out_end
    assert abs(pause - F(1, 5)) <= F(1, 30)
    g = take_index.gap("g0003")
    assert g.start_us < a.src_out_us < b.src_in_us < g.end_us  # the removal is inside the silence
    assert a.src_out_us - g.start_us >= 100_000  # padding after "much." stays
    assert not piece_is_continuous(a, b)


def test_breath_pause_trim_keeps_the_inhale(take_index, cut_doc):
    # g0006 (800 ms breath after "restraint.", snap at 60 %): the removal ends at the snap point, so the
    # part of the pause after it (where the inhale sits) is kept whole
    segs = [Segment(id="seg001", from_word="w0014", to_word="w0028", gap_overrides={"g0006": 500})]
    doc = _with_segments(cut_doc, segs, inserts=[], texts=[], captions=None)
    doc.audio.sfx = []
    tl = compile(doc, take_index)
    g = take_index.gap("g0006")
    a, b = tl.segments[:2]
    assert abs(b.src_in_us - g.snap_us) <= 33_334  # removal ends at the snap point (frame grid)
    assert a.src_out_us - g.start_us >= 80_000  # tail padding after the word stays
    pause = tl.word_map["w0019"].out_start - tl.word_map["w0018"].out_end
    assert abs(pause - F(1, 2)) <= F(1, 30)
    # too short to shorten before the snap point alone: the removal has to cross it
    segs = [Segment(id="seg001", from_word="w0014", to_word="w0028", gap_overrides={"g0006": 250})]
    tl1 = compile(_with_segments(doc, segs), take_index)
    assert tl1.segments[1].src_in_us > g.snap_us
    assert abs(tl1.word_map["w0019"].out_start - tl1.word_map["w0018"].out_end - F(1, 4)) <= F(1, 30)
    # a plain pause trim stays centred on its snap point
    segs = [Segment(id="seg001", from_word="w0001", to_word="w0012", gap_overrides={"g0003": 200})]
    tl2 = compile(_with_segments(doc, segs), take_index)
    g3 = take_index.gap("g0003")
    a2, b2 = tl2.segments[:2]
    assert abs((a2.src_out_us + b2.src_in_us) / 2 - g3.snap_us) <= 34_000


def test_boundary_gap_override_between_contiguous_segments(take_index, cut_doc):
    # a framing split leaves two source-contiguous segments; an override on the boundary pause still applies
    segs = [Segment(id="seg001", from_word="w0001", to_word="w0008"),
            Segment(id="seg002", from_word="w0009", to_word="w0012", framing=Framing(scale=1.3),
                    seam_in=SeamTreatment(kind="punch"), gap_overrides={"g0003": 250})]
    doc = _with_segments(cut_doc, segs, inserts=[], texts=[], captions=None)
    doc.audio.sfx = []
    tl = compile(doc, take_index)
    pause = tl.word_map["w0009"].out_start - tl.word_map["w0008"].out_end
    assert abs(pause - F(1, 4)) <= F(1, 30)
    assert tl.seams == [tl.segments[1].out_start]


# ============================================================================================ J/L and speed
def test_jcut_offsets_picture_not_audio(take_index, cut_doc):
    base = compile(cut_doc, take_index)
    segs = list(cut_doc.segments)
    segs[1] = segs[1].model_copy(update={"seam_in": SeamTreatment(kind="jcut", lead_ms=100)})
    tl = compile(_with_segments(cut_doc, segs), take_index)
    _check_invariants(tl, take_index, cut_doc)
    a, b = tl.segments[0], tl.segments[1]
    ba, bb = base.segments[0], base.segments[1]
    # audio edits unchanged; picture edit moved 3 frames (100 ms at 30 fps) later
    assert (a.audio_src_out_us, b.audio_src_in_us) == (ba.audio_src_out_us, bb.audio_src_in_us)
    assert a.audio_lag_us == -100_000 and b.audio_lead_us == 100_000
    assert a.out_end == ba.out_end + F(3, 30)
    assert _audio_in(b) == b.out_start - F(1, 10)
    assert tl.duration == base.duration
    assert tl.word_map == base.word_map  # words are heard at the same instants


def test_lcut_and_clamping(take_index, cut_doc):
    segs = list(cut_doc.segments)
    segs[1] = segs[1].model_copy(update={"seam_in": SeamTreatment(kind="lcut", lead_ms=200)})
    tl = compile(_with_segments(cut_doc, segs), take_index)
    a, b = tl.segments[0], tl.segments[1]
    assert a.audio_lag_us == 200_000 and b.audio_lead_us == -200_000
    assert b.src_in_us >= take_index.word("w0013").end_us  # never shows the removed false start
    # a huge J lead is clamped so the outgoing picture never reaches the next (removed) word, nor the
    # 80 ms before it in which the mouth already shapes it
    segs[1] = segs[1].model_copy(update={"seam_in": SeamTreatment(kind="jcut", lead_ms=2000)})
    tl = compile(_with_segments(cut_doc, segs), take_index)
    a = tl.segments[0]
    assert a.src_out_us <= take_index.word("w0009").start_us - 80_000
    assert 0 < -a.audio_lag_us < 2_000_000
    _check_invariants(tl, take_index, cut_doc)


def test_speed_maps_output_time(take_index, cut_doc):
    base = compile(cut_doc, take_index)
    segs = list(cut_doc.segments)
    segs[2] = segs[2].model_copy(update={"speed": 1.5})
    tl = compile(_with_segments(cut_doc, segs), take_index)
    _check_invariants(tl, take_index, cut_doc)
    s, b = tl.segments[2], base.segments[2]
    assert s.speed == 1.5
    n_base = round((b.out_end - b.out_start) * 30)
    n = round((s.out_end - s.out_start) * 30)
    assert n == round(F(n_base) / F(3, 2))
    assert s.src_out_us - s.src_in_us == round((s.out_end - s.out_start) * F(3, 2) * US)
    assert s.out_to_src_us(s.out_start + F(1)) == s.src_in_us + 1_500_000
    # the continuous join after the sped segment keeps the audio continuous
    assert piece_is_continuous(tl.segments[2], tl.segments[3])


def test_ntsc_frame_grid(make_take_index, make_cut_doc):
    ix = make_take_index()
    ix.media = ix.media.model_copy(update={"fps": F(30000, 1001), "r_fps": F(30000, 1001), "avg_fps": F(30000, 1001)})
    doc = make_cut_doc(ix)
    tl = compile(doc, ix)
    _check_invariants(tl, ix, doc)
    assert tl.fps == F(30000, 1001)
    assert tl.grid_violations() == []
    for s in tl.segments:
        k = s.src_in_us * F(30000, 1001) / US
        assert abs(k - round(k)) < F(1, 1000)


# ============================================================================================ framing
def _uhd(ix: TakeIndex) -> TakeIndex:
    """The fixture index as a 4K vertical source (2160x3840: 2x lossless punch headroom)."""
    return ix.model_copy(update={"media": ix.media.model_copy(update={"width": 2160, "height": 3840})})


def test_punch_is_fixation_preserving(take_index, cut_doc):
    ix = _uhd(take_index)
    segs = list(cut_doc.segments)
    segs[1] = segs[1].model_copy(update={"framing": Framing(scale=1.4)})
    tl = compile(_with_segments(cut_doc, segs), ix)
    s = tl.segments[1]
    assert s.framing[0].out_t == s.out_start and s.framing[0].scale == pytest.approx(1.4)
    t = s.out_start + F(1, 2)
    box = ix.face_at(s.out_to_src_us(t))
    p = map_source_point(tl, t, box.cx, box.cy, 2160, 3840)
    assert p == pytest.approx((box.cx, box.cy), abs=0.003)  # the face stays where it was on screen
    # and the crop is 1/1.4 of the frame
    sc, cx, cy = framing_state(s.framing, t)
    x0, y0, w, h = crop_window(sc, cx, cy, 1080, 1920, 1080, 1920)
    assert w == pytest.approx(1080 / 1.4) and h == pytest.approx(1920 / 1.4)
    assert x0 >= 0 and x0 + w <= 1080 and y0 >= 0 and y0 + h <= 1920


def test_punch_on_anchor_word_and_smooth_and_push(take_index, cut_doc):
    take_index = _uhd(take_index)
    segs = list(cut_doc.segments)
    segs[3] = segs[3].model_copy(update={"framing": Framing(scale=1.3, anchor_word="w0032")})
    tl = compile(_with_segments(cut_doc, segs), take_index)
    s = tl.segments[3]
    onset = tl.word_map["w0032"].out_start
    ta = F(int((onset - F(33, 1000)) * 30), 30)
    assert framing_state(s.framing, ta - F(1, 30))[0] == pytest.approx(1.0)
    assert framing_state(s.framing, ta)[0] == pytest.approx(1.3)
    assert ta <= onset
    # smooth: reaches the scale at the anchor after an eased move of ease_ms
    segs[3] = segs[3].model_copy(update={"framing": Framing(scale=1.3, anchor_word="w0032", ease="smooth",
                                                            ease_ms=300)})
    s = compile(_with_segments(cut_doc, segs), take_index).segments[3]
    assert framing_state(s.framing, ta - F(3, 10))[0] == pytest.approx(1.0)
    mid = framing_state(s.framing, ta - F(3, 20))[0]
    assert 1.0 < mid < 1.3
    assert framing_state(s.framing, ta)[0] == pytest.approx(1.3)
    # push: 1.0 → scale across the segment, monotonic, eased
    segs[3] = segs[3].model_copy(update={"framing": Framing(scale=1.2, ease="push")})
    s = compile(_with_segments(cut_doc, segs), take_index).segments[3]
    vals = [framing_state(s.framing, s.out_start + F(k, 30))[0] for k in range(round((s.out_end - s.out_start) * 30))]
    assert vals[0] == pytest.approx(1.0) and vals[-1] == pytest.approx(1.2, abs=1e-3)
    assert all(b >= a - 1e-12 for a, b in pairwise(vals))
    steps = [b - a for a, b in pairwise(vals)]
    assert steps[0] < steps[len(steps) // 2] and steps[-1] < steps[len(steps) // 2]  # eased in and out


def test_punch_scale_is_clamped_to_the_upsampling_limit(job, take_index, cut_doc):
    segs = list(cut_doc.segments)
    segs[1] = segs[1].model_copy(update={"framing": Framing(scale=1.6)})
    # 1080x1920 source → 1080x1920: lossless ceiling 1.0, hard limit 1.25x
    tl = compile(_with_segments(cut_doc, segs), take_index, job=job)
    s = tl.segments[1]
    assert max(k.scale for k in s.framing) == pytest.approx(1.25)
    notes = [e["note"] for e in job.read_trace() if e.get("event") == "compile_note"]
    assert notes and "seg002" in notes[0] and "1.25" in notes[0]
    # … and the Director is told the punch still upsamples past the clean-footage limit (1.2x)
    assert any("seg002" in n and "clean-footage limit" in n for n in notes)
    # 4K keeps the requested punch; the limit is configurable
    assert max(k.scale for k in compile(_with_segments(cut_doc, segs), _uhd(take_index)).segments[1].framing) == \
        pytest.approx(1.6)
    loose = compile(_with_segments(cut_doc, segs), take_index, options=CompileOptions(max_face_upsample=1.1))
    assert max(k.scale for k in loose.segments[1].framing) == pytest.approx(1.1)
    # a 720p source is already upsampled 1.5x: no punch at all (base framing, locked)
    sd = take_index.model_copy(update={"media": take_index.media.model_copy(update={"width": 720, "height": 1280})})
    s720 = compile(_with_segments(cut_doc, segs), sd).segments[1]
    assert all(k.scale == 1.0 for k in s720.framing)
    from studio.compile.timeline import lossless_ceiling, max_face_scale

    assert lossless_ceiling(3840, 2160) == pytest.approx(2160 / 1920)  # 4K landscape: 1.125x headroom
    assert max_face_scale(1920, 1080) == 1.0  # 1080p landscape: the reframe itself upsamples 1.78x


def test_push_never_recentres(take_index, cut_doc):
    ix = _landscape_index(take_index, [(0, 0.35), (6_000_000, 0.70)])
    seg = Segment(id="seg001", from_word="w0001", to_word="w0041", framing=Framing(scale=1.1, ease="push"))
    doc = _with_segments(cut_doc, [seg], inserts=[], texts=[], captions=None)
    doc.audio.sfx = []
    keys = compile(doc, ix, options=CompileOptions(max_face_upsample=2.0)).segments[0].framing
    assert len(keys) == 2 and keys[-1].ease == "ease_in_out" and keys[-1].scale == pytest.approx(1.1)
    # the anchor holds (the centre only moves with the fixation-preserving zoom, not to x = 0.70)
    assert abs(keys[-1].cx - keys[0].cx) < 0.02


def test_punch_seam_default_toggles(take_index, cut_doc):
    segs = list(cut_doc.segments)
    segs[1] = segs[1].model_copy(update={"seam_in": SeamTreatment(kind="punch")})
    segs[2] = segs[2].model_copy(update={"seam_in": SeamTreatment(kind="punch")})
    tl = compile(_with_segments(cut_doc, segs), take_index)
    s2, s3 = tl.segments[1], tl.segments[2]
    sc2 = framing_state(s2.framing, s2.out_start)[0]
    assert sc2 >= 1.25  # a punch that hides a pose jump (1080p source: the 1.25x limit)
    assert framing_state(s3.framing, s3.out_start)[0] == pytest.approx(1.0)  # toggles back to base


def test_punch_seam_on_a_continuous_join_punches_in(take_index, cut_doc):
    # seg003 → seg004 is source-contiguous (w0028 | w0029): a "punch" seam there is an emphasis punch-in
    segs = list(cut_doc.segments)
    segs[3] = segs[3].model_copy(update={"seam_in": SeamTreatment(kind="punch")})
    tl = compile(_with_segments(cut_doc, segs), take_index)
    s3, s4 = tl.segments[2], tl.segments[3]
    assert piece_is_continuous(s3, s4)  # the audio still runs straight through
    assert framing_state(s3.framing, s4.out_start - F(1, 30))[0] == pytest.approx(1.0)
    assert framing_state(s4.framing, s4.out_start)[0] >= 1.25


def test_point_centre_is_literal(take_index, cut_doc):
    segs = list(cut_doc.segments)
    segs[0] = segs[0].model_copy(update={"framing": Framing(scale=1.5, center=Point(x=0.3, y=0.6))})
    s = compile(_with_segments(cut_doc, segs), take_index).segments[0]
    assert framing_state(s.framing, s.out_start)[1:] == pytest.approx((0.3, 0.6))


def _landscape_index(ix: TakeIndex, path: list[tuple[int, float]]) -> TakeIndex:
    media = ix.media.model_copy(update={"width": 1920, "height": 1080, "display_aspect": F(16, 9)})
    pts = []
    for t_us in range(0, ix.media.duration_us + 1, 100_000):
        cx = path[0][1]
        for start, x in path:
            if t_us >= start:
                cx = x
        pts.append(FaceTrackPoint(t_us=t_us, cx=cx, cy=0.4, w=0.12, h=0.22, conf=0.95))
    vis = ix.visual.model_copy(update={"face_track": FaceTrack(points=pts, method="test"), "samples": [],
                                       "events": []})
    return ix.model_copy(update={"media": media, "visual": vis})


def test_landscape_reframe_locks_on_face_with_dead_zone(take_index, cut_doc):
    ix = _landscape_index(take_index, [(0, 0.70)])
    doc = _with_segments(cut_doc, [Segment(id="seg001", from_word="w0001", to_word="w0041")], inserts=[],
                         texts=[], captions=None)
    doc.audio.sfx = []
    tl = compile(doc, ix)
    s = tl.segments[0]
    assert len(s.framing) == 1  # locked: no hunting
    sc, cx, cy = framing_state(s.framing, s.out_start)
    assert sc == 1.0 and cx == pytest.approx(0.70, abs=0.01)
    bw, bh = base_window(1920, 1080, 9 / 16)
    assert bw == pytest.approx((9 / 16) / (16 / 9)) and bh == 1.0
    x0, _, w, h = crop_window(sc, cx, cy, 1920, 1080, 1080, 1920)
    assert h == pytest.approx(1080) and w == pytest.approx(1080 * 9 / 16)
    # small wobble stays inside the dead zone → still one key
    ix2 = _landscape_index(take_index, [(0, 0.70), (3_000_000, 0.72), (6_000_000, 0.69)])
    assert len(compile(doc, ix2).segments[0].framing) == 1
    # the speaker relocates → one eased re-centre after the dead time
    ix3 = _landscape_index(take_index, [(0, 0.35), (6_000_000, 0.70)])
    keys = compile(doc, ix3).segments[0].framing
    assert len(keys) >= 3
    assert keys[0].cx == pytest.approx(0.35, abs=0.01) and keys[-1].cx == pytest.approx(0.70, abs=0.01)
    assert keys[-1].ease == "ease_in_out"
    move_start = next(k for k in keys[1:] if k.ease == "hold")
    assert float(move_start.out_t) >= (6_000_000 - 300_000) / US + 0.6  # waited for the dead time


# ============================================================================================ inserts
def _lic() -> Licence:
    return Licence(name="Pexels License")


def test_inserts_mapped_from_anchors(take_index, cut_doc):
    tl = compile(cut_doc, take_index)
    (ins,) = tl.inserts
    assert ins.insert_id == "i001" and ins.mode == "card" and isinstance(ins.asset, CardSpec)
    start = tl.word_map["w0020"].out_start
    assert abs(ins.out_start - start) <= F(1, 60)
    assert ins.out_end >= tl.word_map["w0024"].out_end - F(1, 60)
    assert ins.out_end - ins.out_start >= F(2, 5)
    assert tl.grid_violations() == []


def test_split_and_pip_layouts(take_index, cut_doc):
    inserts = [
        Insert(id="i001", anchor_from_word="w0030", anchor_to_word="w0036", mode="split_top",
               asset=CardSpec(template="stat", number="3"), job="show the number", split_ratio=0.45),
        Insert(id="i002", anchor_from_word="w0014", anchor_to_word="w0018", mode="pip",
               asset=AssetRef(source="pexels", width=1920, height=1080, licence=_lic()), job="show it"),
    ]
    tl = compile(cut_doc.model_copy(update={"inserts": inserts}), take_index)
    split, pip = tl.inserts
    assert split.rect == (0.0, 0.0, 1.0, 0.45)
    inside = (split.out_start + split.out_end) / 2
    assert region_for_time(tl, inside) == pytest.approx((0.0, 0.45, 1.0, 0.55))
    assert region_for_time(tl, split.out_end) == (0.0, 0.0, 1.0, 1.0)
    seg = tl.segment_at(inside)
    assert any(k.out_t == split.out_start for k in seg.framing)  # re-framed when the split starts
    # the face sits in the speaker region, below the content, eyes clear of the bottom UI band (y < 1248)
    box = take_index.face_at(seg.out_to_src_us(inside))
    top = map_source_point(tl, inside, box.cx, box.y, 1080, 1920)[1]
    eyes = map_source_point(tl, inside, box.cx, box.y + 0.27 * box.h, 1080, 1920)[1]
    assert top >= 0.45 - 1e-6
    assert 0.45 < eyes <= 0.62 + 1e-6
    # PiP: inside the safe zones, landscape aspect kept
    x, y, w, h = pip.rect
    assert x >= 0.06 - 1e-9 and x + w <= 0.94 + 1e-9 and y >= 0.14 and y + h <= 0.65 + 1e-9
    assert (w * 1080) / (h * 1920) == pytest.approx(16 / 9, rel=0.01)
    # an AssetRef without a local file is not drawn, so it does not steal the speaker's frame
    assert pip.asset_path is None


def _big_face_index(ix, cy: float = 0.57, h: float = 0.38):
    """A close selfie (face box 38 % of the frame height, centred low), as in the real d030 take."""
    pts = [FaceTrackPoint(t_us=t, cx=0.55, cy=cy, w=0.5, h=h, conf=0.97)
           for t in range(0, ix.media.duration_us + 1, 100_000)]
    vis = ix.visual.model_copy(update={"face_track": FaceTrack(points=pts, method="test"), "samples": [],
                                       "events": []})
    return ix.model_copy(update={"visual": vis})


@pytest.mark.parametrize("mode", ["split_top", "split_bottom"])
def test_split_places_a_big_face_by_its_eye_line(take_index, cut_doc, mode):
    ix = _big_face_index(take_index)
    ins = Insert(id="i001", anchor_from_word="w0030", anchor_to_word="w0036", mode=mode,
                 asset=CardSpec(template="stat", number="3"), job="show the number", split_ratio=0.5)
    tl = compile(cut_doc.model_copy(update={"inserts": [ins]}), ix)
    t = (tl.inserts[0].out_start + tl.inserts[0].out_end) / 2
    rx, ry, rw, rh = region_for_time(tl, t)
    seg = tl.segment_at(t)
    box = ix.face_at(seg.out_to_src_us(t))
    top, eyes, chin = (map_source_point(tl, t, box.cx, y, 1080, 1920)[1]
                       for y in (box.y, box.y + 0.27 * box.h, box.y + box.h))
    fh = chin - top
    if mode == "split_top":  # speaker below the content: headroom under the split line, eyes above y≈1190
        assert ry == pytest.approx(0.5)
        assert top >= ry - 0.10 * fh - 1e-6
        assert ry < eyes <= 0.62 + 1e-6
        assert chin <= 1.0
    else:  # speaker above the content: eyes under the top UI band, chin inside the region
        assert ry == 0.0 and rh == pytest.approx(0.5)
        assert eyes >= 0.19 - 1e-6
        assert chin <= rh - 0.01 + 1e-6
        assert top >= -0.10 * fh - 1e-6


def test_missing_split_asset_keeps_full_frame(take_index, cut_doc):
    ins = Insert(id="i001", anchor_from_word="w0030", anchor_to_word="w0036", mode="split_bottom",
                 asset=AssetRef(source="pexels", path="assets/broll/nope.mp4", licence=_lic()), job="x")
    tl = compile(cut_doc.model_copy(update={"inserts": [ins]}), take_index)
    mid = (tl.inserts[0].out_start + tl.inserts[0].out_end) / 2
    assert region_for_time(tl, mid) == (0.0, 0.0, 1.0, 1.0)


# ============================================================================================ captions/text/sfx/music
def test_captions_delegate_to_captions_module(take_index, cut_doc, monkeypatch):
    from studio.compile import captions as captions_mod
    from studio.compile.models import TimelineCaptionPage, TimelineCaptionWord

    calls = []

    def fake(index, doc, timeline, **kw):
        calls.append((index, doc, timeline.duration))
        w = timeline.word_map["w0001"]
        return [TimelineCaptionPage(page_id="p001", out_start=0, out_end=F(1, 2), words=[
            TimelineCaptionWord(word_id="w0001", text="Most", out_start=w.out_start, out_end=w.out_end)])]

    monkeypatch.setattr(captions_mod, "build_caption_pages", fake)
    tl = compile(cut_doc, take_index)
    assert len(calls) == 1 and calls[0][1] is cut_doc and calls[0][2] == tl.duration
    assert [p.page_id for p in tl.captions] == ["p001"]

    def unavailable(*a, **k):
        raise NotImplementedError

    monkeypatch.setattr(captions_mod, "build_caption_pages", unavailable)
    assert len(compile(cut_doc, take_index).captions) == len(cut_doc.captions.pages)  # built-in fallback


def test_captions_timing_and_placement(take_index, cut_doc):
    tl = compile(cut_doc, take_index, options=CompileOptions(caption_builder="builtin"))
    pages = tl.captions
    assert len(pages) == len(cut_doc.captions.pages)
    for a, b in pairwise(pages):
        assert b.out_start - a.out_end >= F(2, 30)  # never overlap; ≥ 2 blank frames
    for p in pages:
        assert p.out_end > p.out_start
        assert 0.14 <= p.y_norm <= 0.65  # inside the platform safe band
        for w in p.words:
            assert p.out_start <= w.out_start < w.out_end <= p.out_end
    first = pages[0].words[0]
    onset = tl.word_map[first.word_id].out_start
    assert first.out_start == F(int(onset * 30), 30)  # word spans are the measured onsets (grid)
    assert pages[0].out_start == F(int((onset - F(2, 30)) * 30), 30)  # the page leads by 2 frames
    emph = {w.word_id for p in pages for w in p.words if w.emphasis}
    assert emph == {"w0018", "w0024", "w0032"}
    assert tl.grid_violations() == []
    # the caption sits below the chin in the transformed frame
    t = (pages[0].out_start + pages[0].out_end) / 2
    box = take_index.face_at(tl.segment_at(t).out_to_src_us(t))
    chin = map_source_point(tl, t, box.cx, box.y + box.h, 1080, 1920)[1]
    assert pages[0].y_norm > chin


def test_texts_sfx_music(take_index, cut_doc):
    doc = cut_doc.model_copy(deep=True)
    doc.audio.music = MusicSpec(start_word="w0001", end_word="w0041", hit_word_ids=["w0018"],
                                asset=AssetRef(source="library", kind="audio", licence=_lic()))
    tl = compile(doc, take_index)
    (t,) = tl.texts
    assert t.kind == "hook_title" and t.out_end - t.out_start >= F(8, 5)  # 0.3 s/word + 1 s
    assert 0.14 <= t.y_norm <= 0.35
    (fx,) = tl.sfx
    assert fx.out_t == tl.word_map["w0018"].out_start  # sample-exact, not frame-snapped
    m = tl.music
    assert m.out_start == F(6, 30) and m.hits == [tl.word_map["w0018"].out_start]
    assert m.out_end == tl.duration or m.out_end <= tl.duration


# ============================================================================================ geometry + errors
def test_framing_state_semantics():
    from studio.compile.models import FramingKey

    keys = [FramingKey(out_t=0), FramingKey(out_t=1, scale=1.5, cx=0.4, cy=0.3, ease="hold")]
    assert framing_state(keys, F(1, 2)) == (1.0, 0.5, 0.5)  # hold switches at the key
    assert framing_state(keys, 1) == (1.5, 0.4, 0.3)
    keys[1] = keys[1].model_copy(update={"ease": "linear"})
    s, cx, _ = framing_state(keys, F(1, 2))
    assert s == pytest.approx(1.5 ** 0.5)  # geometric zoom
    # centre moves in proportion to the crop width (keeps an anchored point fixed)
    wa, wb, ws = 1.0, 1 / 1.5, 1 / s
    assert cx == pytest.approx(0.5 + (0.4 - 0.5) * (ws - wa) / (wb - wa))
    assert framing_state([], 3) == (1.0, 0.5, 0.5)
    # clamping keeps the window inside the source
    x0, y0, w, h = crop_window(1.2, 0.99, 0.01, 1080, 1920, 1080, 1920)
    assert x0 + w == pytest.approx(1080) and y0 == 0


def test_compile_errors(take_index, cut_doc):
    bad = _with_segments(cut_doc, [Segment(id="seg001", from_word="w0010", to_word="w0005")])
    with pytest.raises(CompileError):
        compile(bad, take_index)
    dup = _with_segments(cut_doc, [Segment(id="seg001", from_word="w0001", to_word="w0005"),
                                   Segment(id="seg002", from_word="w0004", to_word="w0008")])
    with pytest.raises(CompileError):
        compile(dup, take_index)
    unknown = _with_segments(cut_doc, [Segment(id="seg001", from_word="w0001", to_word="w0999")])
    with pytest.raises(CompileError):
        compile(unknown, take_index)
    with pytest.raises(CompileError):
        compile(cut_doc, take_index, width=1081)


def test_empty_document(take_index, cut_doc):
    tl = compile(_with_segments(cut_doc, []), take_index)
    assert tl.duration == 0 and tl.segments == [] and all(v is None for v in tl.word_map.values())


def test_options_change_pads(take_index, cut_doc):
    a = compile(cut_doc, take_index)
    b = compile(cut_doc, take_index, options=CompileOptions(tail_pad_ms=80.0, lead_pad_ms=40.0))
    assert b.segments[0].src_out_us <= a.segments[0].src_out_us
    assert b.segments[1].src_in_us >= a.segments[1].src_in_us


def test_job_sets_source_and_asset_paths(job, take_index, cut_doc):
    (job.assets_dir / "broll" / "clip.mov").write_bytes(b"x")
    ins = Insert(id="i001", anchor_from_word="w0020", anchor_to_word="w0024", mode="full",
                 asset=AssetRef(source="pexels", path="assets/broll/clip.mov", in_ms=1500, licence=_lic()), job="x")
    tl = compile(cut_doc.model_copy(update={"inserts": [ins]}), take_index, job=job)
    assert tl.source_path == str(job.mezz_path)
    assert tl.inserts[0].asset_path == str((job.assets_dir / "broll" / "clip.mov").resolve())
    assert tl.inserts[0].asset_in_us == 1_500_000
