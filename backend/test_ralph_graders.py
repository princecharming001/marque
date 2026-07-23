"""Unit tests for the Ralph-campaign pure graders (broll_timing, layout_qc,
audio_qc parsers/detectors, campaign_common mapping). All keyless-runnable:
handcrafted plans + canned ffmpeg stderr, no network, no ffmpeg binary."""
from __future__ import annotations

from app.layout_constants import CAPTION_POS_Y_MAX
from eval import broll_timing as bt
from eval import layout_qc as lq
from eval.audio_qc import check_bed_separation, detect_seam_pops, parse_astats
from eval.campaign_common import (CLASS_SEVERITY, finding, seam_out_frames,
                                  src_to_out, total_out_frames)

# Words at source frames 60-72 and 78-87 (ms_to_frame(2000)=60 etc.).
WORDS = [
    {"word": "gochujang", "start_ms": 2000, "end_ms": 2400},
    {"word": "pan", "start_ms": 2600, "end_ms": 2900},
]
ONE_CLIP = {"clips": [{"src_in": 0, "src_out": 300, "speed": 1.0}]}


def _classes(findings):
    return [f["class"] for f in findings]


# ---------------------------------------------------------------- common ----

def test_finding_severity_comes_from_the_table():
    f = finding("v", "j", "audio_pop", t=1.234, evidence="x", source="audio_qc")
    assert f["severity"] == "P0" and f["t_seconds"] == 1.23
    assert finding("v", "j", "never_heard_of_it")["severity"] == "P2"


def test_frame_mapping_and_seams():
    plan = {"clips": [{"src_in": 0, "src_out": 100, "speed": 1.0},
                      {"src_in": 150, "src_out": 250, "speed": 1.0}]}
    assert src_to_out(plan, 50) == 50
    assert src_to_out(plan, 120) is None          # inside the cut gap
    assert src_to_out(plan, 200) == 150
    assert seam_out_frames(plan) == [100]
    assert total_out_frames(plan) == 200


# ---------------------------------------------------------- broll_timing ----

def _broll_edl(items):
    return {"broll": items}


def test_broll_timing_clean_window_passes():
    # entry = word start (60) - jcut lead; hold inside the entity/full band.
    si = 60 - bt._BROLL_JCUT_LEAD
    edl = _broll_edl([{"src_in": si, "src_out": si + 20, "need": "entity",
                       "mode": "full", "cue_text": "gochujang"}])
    assert bt.verify(edl, WORDS, ONE_CLIP) == []


def test_broll_timing_entry_off_word():
    edl = _broll_edl([{"src_in": 100, "src_out": 120, "need": "entity",
                       "mode": "full", "cue_text": "x"}])
    assert "broll_timing_off" in _classes(bt.verify(edl, WORDS, ONE_CLIP))


def test_broll_timing_linger_and_clipped():
    si = 60 - bt._BROLL_JCUT_LEAD
    long = _broll_edl([{"src_in": si, "src_out": si + 24 + bt._BROLL_HOLD_JITTER + 5,
                        "need": "entity", "mode": "full"}])
    assert "broll_linger" in _classes(bt.verify(long, WORDS, ONE_CLIP))
    short = _broll_edl([{"src_in": si, "src_out": si + 30,   # < evidence min 42
                         "need": "evidence", "mode": "full"}])
    assert "broll_clipped" in _classes(bt.verify(short, WORDS, ONE_CLIP))


def test_broll_timing_overrun_phrase():
    # evidence window whose exit runs 26f past the last covered word end (87).
    si = 60 - bt._BROLL_JCUT_LEAD
    edl = _broll_edl([{"src_in": si, "src_out": si + 69,
                       "need": "evidence", "mode": "full"}])
    assert "broll_overrun_phrase" in _classes(bt.verify(edl, WORDS, ONE_CLIP))


def test_broll_timing_crowded():
    si = 60 - bt._BROLL_JCUT_LEAD
    edl = _broll_edl([
        {"src_in": si, "src_out": si + 20, "need": "entity", "mode": "full"},
        {"src_in": si + 26, "src_out": si + 46, "need": "entity", "mode": "full"},
    ])
    assert "broll_crowded" in _classes(bt.verify(edl, WORDS, ONE_CLIP))


def test_broll_timing_midword_ramp_is_p1():
    # Source-CONTIGUOUS seam (speed boundary) inside a word: tempo lurch → P1.
    plan = {"clips": [{"src_in": 0, "src_out": 100, "speed": 1.0},
                      {"src_in": 100, "src_out": 300, "speed": 1.0}]}
    words = [{"word": "unbelievable", "start_ms": 3167, "end_ms": 3500}]
    f = bt.verify({"broll": []}, words, plan)
    assert _classes(f) == ["midword_ramp"]
    assert f[0]["severity"] == "P1"


def test_broll_timing_midword_splice_is_p0():
    # A REAL butt-splice: the seam removes source [100,150) inside the word.
    plan = {"clips": [{"src_in": 0, "src_out": 100, "speed": 1.0},
                      {"src_in": 150, "src_out": 300, "speed": 1.0}]}
    words = [{"word": "unbelievable", "start_ms": 3167, "end_ms": 5333}]
    f = bt.verify({"broll": []}, words, plan)
    assert _classes(f) == ["midword_cut"]
    assert f[0]["severity"] == "P0"


def test_broll_timing_exit_in_drop_not_flagged():
    # Exit frames swallowed by a drop never render — no overrun for them.
    plan = {"clips": [{"src_in": 0, "src_out": 120, "speed": 1.0},
                      {"src_in": 200, "src_out": 300, "speed": 1.0}]}
    si = 60 - bt._BROLL_JCUT_LEAD
    edl = _broll_edl([{"src_in": si, "src_out": 180,   # 60f "past" — all in the drop
                       "need": "evidence", "mode": "full"}])
    words = [{"word": "gochujang", "start_ms": 2000, "end_ms": 2400},
             {"word": "pan", "start_ms": 3667, "end_ms": 3933}]  # ends 110, 118
    f = bt.verify(edl, words, plan)
    assert "broll_overrun_phrase" not in _classes(f)


# -------------------------------------------------------------- layout_qc ----

def test_layout_pos_y_unclamped():
    plan = dict(ONE_CLIP, caption_options={"pos_y": CAPTION_POS_Y_MAX + 0.1})
    assert "pos_y_unclamped" in _classes(lq.grade_layout(plan))


def test_layout_sticker_panel_collision():
    plan = dict(ONE_CLIP,
                overlays=[{"type": "text_sticker", "text": "BIG SALE",
                           "pos_x": 0.5, "pos_y": 0.3, "scale": 1.0,
                           "frame_in": 0, "frame_out": 100}],
                broll=[{"mode": "panel", "frame_in": 0, "frame_out": 100}])
    f = lq.grade_layout(plan)
    assert "layout_collision" in _classes(f)
    assert CLASS_SEVERITY["layout_collision"] == "P0"


def test_layout_default_captions_are_clean():
    # A plain bottom-anchored caption plan must NOT flag (the band is inside
    # the render's own safe clamp; the platform bottom tier is sticker-only).
    plan = dict(ONE_CLIP, captions=[{"frame": 0, "end_frame": 60, "text": "hi"}])
    assert lq.grade_layout(plan) == []


def test_layout_no_collision_when_windows_disjoint():
    plan = dict(ONE_CLIP,
                overlays=[{"type": "text_sticker", "text": "BIG SALE",
                           "pos_x": 0.5, "pos_y": 0.3, "scale": 1.0,
                           "frame_in": 0, "frame_out": 50}],
                broll=[{"mode": "panel", "frame_in": 60, "frame_out": 120}])
    assert "layout_collision" not in _classes(lq.grade_layout(plan))


def test_layout_end_card_takeover_collision():
    plan = dict(ONE_CLIP,
                overlays=[{"type": "text_sticker", "text": "BIG SALE",
                           "pos_x": 0.5, "pos_y": 0.3, "scale": 1.0,
                           "frame_in": 0, "frame_out": 100}],
                end_card={"start_frame": 50, "frames": 60})
    assert "layout_collision" in _classes(lq.grade_layout(plan))


# --------------------------------------------------------------- audio_qc ----

_CANNED_STDERR = """\
[Parsed_ametadata_1 @ 0x1] frame:0    pts:0       pts_time:0
[Parsed_ametadata_1 @ 0x1] lavfi.astats.Overall.RMS_level=-40.0
[Parsed_ametadata_1 @ 0x1] lavfi.astats.Overall.Peak_level=-30.0
[Parsed_ametadata_1 @ 0x1] lavfi.astats.Overall.Max_difference=0.010000
[Parsed_ametadata_1 @ 0x1] frame:1    pts:480     pts_time:0.01
[Parsed_ametadata_1 @ 0x1] lavfi.astats.Overall.RMS_level=-41.5
[Parsed_ametadata_1 @ 0x1] lavfi.astats.Overall.Peak_level=-31.0
[Parsed_ametadata_1 @ 0x1] lavfi.astats.Overall.Max_difference=0.012000
"""


def test_parse_astats_canned():
    frames = parse_astats(_CANNED_STDERR)
    assert len(frames) == 2
    assert frames[0] == {"t": 0.0, "rms": -40.0, "peak": -30.0, "maxdiff": 0.01}
    assert frames[1]["t"] == 0.01 and frames[1]["maxdiff"] == 0.012


def _seam_timelines(seam_t: float):
    """Quiet HF floor with a spike at the seam; flat broadband maxdiff with a
    discontinuity at the seam."""
    hf = [{"t": seam_t + dt, "rms": -60.0, "peak": -55.0}
          for dt in (-0.06, -0.05, -0.04, -0.03, 0.03, 0.04, 0.05, 0.06)]
    hf.append({"t": seam_t, "rms": -30.0, "peak": -20.0})
    bb = [{"t": i * 0.01, "rms": -30.0, "peak": -20.0, "maxdiff": 0.01}
          for i in range(0, 400)]
    bb.append({"t": seam_t, "rms": -20.0, "peak": -10.0, "maxdiff": 0.5})
    return hf, bb


def test_detect_seam_pop_p0():
    plan = {"clips": [{"src_in": 0, "src_out": 100, "speed": 1.0},
                      {"src_in": 150, "src_out": 250, "speed": 1.0}]}
    hf, bb = _seam_timelines(100 / 30)
    f = detect_seam_pops(hf, bb, plan, [])
    assert _classes(f) == ["audio_pop"] and f[0]["severity"] == "P0"


def test_detect_seam_pop_masked_by_transition_is_p2():
    plan = {"clips": [{"src_in": 0, "src_out": 100, "speed": 1.0},
                      {"src_in": 150, "src_out": 250, "speed": 1.0}],
            "transitions": [{"at_frame": 100, "type": "flash"}]}
    hf, bb = _seam_timelines(100 / 30)
    f = detect_seam_pops(hf, bb, plan, [])
    assert len(f) == 1 and f[0]["severity"] == "P2"
    assert f[0]["extra"]["masked_by_transition"] is True


def test_detect_seam_pop_word_onset_guard():
    # An onset within 30ms raises the HF threshold to 18dB; a 25dB spike still
    # fires, a 15dB spike must not.
    plan = {"clips": [{"src_in": 0, "src_out": 100, "speed": 1.0},
                      {"src_in": 150, "src_out": 250, "speed": 1.0}]}
    # src frame 150 (= clip 2's first frame) maps to out 100, the seam itself.
    words = [{"word": "pop", "start_ms": 5000}]
    hf, bb = _seam_timelines(100 / 30)
    for fr in hf:
        if fr["t"] == 100 / 30:
            fr["peak"] = -45.0            # only 15dB over the -60 floor
    assert detect_seam_pops(hf, bb, plan, words) == []


def test_bed_separation_flags_music_over_speech():
    plan = {"clips": [{"src_in": 0, "src_out": 300, "speed": 1.0}],
            "audio": {"music": {"url": "http://x/bed.mp3"},
                      "speech_frames": [30, 60, 90]}}
    bb = []
    for i in range(0, 300):
        t = i * 0.01
        near = any(abs(t - s / 30) <= 0.15 for s in (30, 60, 90))
        bb.append({"t": t, "rms": -20.0 if near else -25.0})
    f = check_bed_separation(bb, plan)
    assert _classes(f) == ["music_over_speech"]


def test_bed_separation_clean_when_separated():
    plan = {"clips": [{"src_in": 0, "src_out": 300, "speed": 1.0}],
            "audio": {"music": {"url": "http://x/bed.mp3"},
                      "speech_frames": [30, 60, 90]}}
    bb = []
    for i in range(0, 300):
        t = i * 0.01
        near = any(abs(t - s / 30) <= 0.15 for s in (30, 60, 90))
        bb.append({"t": t, "rms": -18.0 if near else -32.0})
    assert check_bed_separation(bb, plan) == []
