"""Tests for studio.compile.audio (keyless, offline, synthetic audio)."""

from __future__ import annotations

import json
import math
import shutil
from fractions import Fraction as F
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from audio_voice_synth import SR, db, evenly_spaced_words, make_index, make_timeline, synth_voice
from scipy import signal as sps

from studio.audio import voice as V
from studio.compile import audio as A
from studio.compile.models import Timeline, TimelineMusic, TimelineSegment, TimelineSfx
from studio.doc.model import AudioPlan, CutDocument, SeamTreatment, VoiceChainSpec
from studio.jobs import Job
from studio.timebase import sample_index

needs_rubberband = pytest.mark.skipif(shutil.which("rubberband") is None, reason="rubberband CLI not installed")


def tp8(x: np.ndarray) -> float:
    """Independent true-peak check: 8x polyphase oversampling (scipy)."""
    x2 = np.atleast_2d(x)
    up = sps.resample_poly(x2, 8, 1, axis=1, window=("kaiser", 12.0))
    return 20 * math.log10(max(float(np.max(np.abs(up))), float(np.max(np.abs(x2))), 1e-20))


def marker_positions(y: np.ndarray, sr: int, freq: float, *, thresh_rel: float = 0.3) -> list[float]:
    """Centres (in samples) of tone bursts at ``freq`` in ``y`` (band-passed envelope)."""
    sos = sps.butter(4, [freq * 0.85, freq * 1.15], "bandpass", fs=sr, output="sos")
    env = np.abs(sps.hilbert(sps.sosfiltfilt(sos, y)))
    env = sps.convolve(env, np.ones(48) / 48, mode="same")
    on = env > thresh_rel * env.max()
    d = np.diff(np.concatenate(([0], on.astype(int), [0])))
    out = []
    for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1), strict=True):
        w = env[a:b] ** 2
        out.append(float((np.arange(a, b) * w).sum() / w.sum()))
    return out


def burst(sr: int, freq: float, ms: float = 6.0, amp: float = 0.5) -> np.ndarray:
    n = round(ms * 1e-3 * sr)
    return amp * np.hanning(n) * np.sin(2 * np.pi * freq * np.arange(n) / sr)


def one_seg_timeline(src_in_us: int, src_out_us: int, *, out_start: F = F(0), fps: F = F(30), speed: float = 1.0,
                     lead_us: int = 0, lag_us: int = 0, extra: F = F(0)) -> Timeline:
    from studio.timebase import snap_to_frame

    dur = snap_to_frame(F(src_out_us - src_in_us, 1_000_000) / F(str(speed)), fps)
    seg = TimelineSegment(seg_id="seg001", src_in_us=src_in_us, src_out_us=src_out_us, out_start=out_start,
                          out_end=out_start + dur, speed=speed, audio_src_in_us=src_in_us - lead_us,
                          audio_src_out_us=src_out_us + lag_us)
    return Timeline(fps=fps, duration=out_start + dur + extra, segments=[seg])


# ============================================================================================== loudness
def test_integrated_loudness_matches_bs1770_reference():
    t = np.arange(SR * 10) / SR
    s = np.sin(2 * np.pi * 997 * t)
    # BS.1770: a 0 dBFS 997 Hz sine in one channel reads -3.01 LKFS
    assert A.integrated_loudness(np.vstack([s, 0 * s]), SR) == pytest.approx(-3.01, abs=0.01)
    assert A.integrated_loudness(0.1 * s, SR) == pytest.approx(-23.01, abs=0.01)
    import pyloudnorm

    rng = np.random.default_rng(1)
    x = rng.standard_normal((2, SR * 6)) * 0.05
    x[:, SR: 2 * SR] *= 4
    x[:, 4 * SR:] *= 0.001  # gated-out quiet tail
    ref = pyloudnorm.Meter(SR).integrated_loudness(x.T)
    assert A.integrated_loudness(x, SR) == pytest.approx(ref, abs=0.06)  # pyloudnorm's 48k filter is ~0.04 low
    assert A.integrated_loudness(np.zeros((2, SR)), SR) == float("-inf")


def test_true_peak_sees_intersample_peaks():
    t = np.arange(SR) / SR
    s = np.sin(2 * np.pi * (SR / 4) * t + np.pi / 4)  # samples at ±0.707, waveform peaks at 1.0
    assert 20 * np.log10(np.max(np.abs(s))) == pytest.approx(-3.01, abs=0.02)
    assert A.true_peak_dbtp(s, SR) > -0.2
    assert tp8(s) > -0.2


def test_true_peak_limiter_holds_ceiling_and_is_transparent_elsewhere():
    rng = np.random.default_rng(2)
    n = SR * 3
    t = np.arange(n) / SR
    x = 0.05 * rng.standard_normal((2, n))
    x[:, SR: SR + 4800] += 1.4 * np.sin(2 * np.pi * 11025 * t[SR: SR + 4800] + 0.7)  # hot ISP burst
    x[:, 2 * SR: 2 * SR + 480] += 0.9  # DC-ish slam
    y, info = A.true_peak_limit(x, SR, -1.5)
    assert A.true_peak_dbtp(y, SR) <= -1.5 + 0.01
    # an independent 8x reading may sit up to ~0.15 dB above the BS.1770 4x one for near-Nyquist content;
    # the -0.5 dB codec margin under the -1 dBTP requirement covers it
    assert tp8(y) <= -1.5 + 0.2
    assert info["max_gr_db"] > 3
    # far from the peaks the signal is untouched
    quiet = slice(round(0.2 * SR), round(0.8 * SR))
    assert np.array_equal(y[:, quiet], x[:, quiet])
    assert y.shape == x.shape


def test_forward_min_helper_matches_bruteforce():
    rng = np.random.default_rng(3)
    a = rng.random(200)
    for size in (1, 2, 5, 8, 33):
        m = A._forward_min(a, size)
        brute = np.array([a[j: j + size].min() for j in range(200 - size + 1)])
        assert np.allclose(m[: 200 - size + 1], brute)


@pytest.mark.parametrize("target", [-14.0, -16.0])
def test_master_hits_loudness_target_and_true_peak(target: float):
    words = evenly_spaced_words(8.0)
    x = synth_voice(8.0, words, level_db=-18, noise_db=-60, seed=4)
    x[round(2 * SR): round(2 * SR) + 200] += 0.9  # a transient the limiter must catch
    mix = np.vstack([x, x])
    y, info = A.master_loudness(mix, SR, target_lufs=target, ceiling_dbtp=-1.5)
    assert A.integrated_loudness(y, SR) == pytest.approx(target, abs=0.1)
    assert abs(info["integrated_lufs"] - target) <= 0.5
    assert tp8(y) <= -1.0
    assert info["true_peak_dbtp"] <= -1.49
    assert y[:, 0].max() == 0.0 and y[:, -1].max() == 0.0  # edge fades


# ============================================================================================== extraction
def test_speed1_extraction_is_sample_exact():
    rng = np.random.default_rng(5)
    voice = rng.standard_normal(SR * 5) * 0.1
    tl = one_seg_timeline(1_000_013, 3_000_000)  # µs start that is not on the 48k grid
    res = A.assemble_dialogue(tl, voice, SR)
    assert res.audio.size == tl.sample_count
    off = round(1_000_013 * SR / 1_000_000)  # 48000.62 -> 48001
    mid = np.arange(1000, tl.sample_count - 1000)
    assert np.array_equal(res.audio[mid], voice[mid + off])


def test_jcut_and_lcut_move_the_audio_seam():
    n = SR * 6
    voice = np.zeros(n)
    marks_a = [1.30, 1.95]  # inside segment a's source (a: 0.5 .. 2.0)
    marks_b = [3.85, 4.40]  # inside segment b's source (b: 4.0 .. 5.5); 3.85 lies in b's J-lead
    for t in marks_a:
        voice[round(t * SR): round(t * SR) + 288] += burst(SR, 2000)
    for t in marks_b:
        voice[round(t * SR): round(t * SR) + 288] += burst(SR, 3000)
    words: list[tuple[float, float]] = []
    tl = make_timeline([dict(src_in=0.5, src_out=2.0), dict(src_in=4.0, src_out=5.5, lead_ms=200)], words=words)
    b = tl.segments[1]
    assert b.out_start == F(3, 2)
    res = A.assemble_dialogue(tl, voice, SR)
    got_b = marker_positions(res.audio, SR, 3000)
    # b's audio starts 200 ms before its picture: the 3.85 s marker lands at 1.5 - 0.15 = 1.35 s
    exp_b = [float((b.out_start + (F(round(t * 1e6), 1_000_000) - F(4))) * SR) + 144 for t in marks_b]
    assert len(got_b) == 2 and all(abs(g - e) < 0.5e-3 * SR for g, e in zip(got_b, exp_b, strict=True))
    # a's audio is handed over at 1.3 s: its 1.95 s marker (output 1.45 s) must be gone
    got_a = marker_positions(res.audio, SR, 2000)
    assert len(got_a) == 1 and abs(got_a[0] - (0.8 * SR + 144)) < 0.5e-3 * SR
    seam = next(s for s in res.seams if s["kind"] == "xfade")
    assert seam["t"] == pytest.approx(1.3)

    # L cut: a keeps talking 300 ms over b's picture; b's first 300 ms of audio are skipped
    tl2 = make_timeline([dict(src_in=0.5, src_out=1.5, lag_ms=500), dict(src_in=4.0, src_out=5.5, kind="lcut")],
                        words=words)
    res2 = A.assemble_dialogue(tl2, voice, SR)
    a_end = float(tl2.segments[0].out_end)
    got_a2 = marker_positions(res2.audio, SR, 2000)
    # a's 1.95 s marker lies in its 500 ms lag: output 1.45 s, after a's picture ended at 1.0 s
    assert any(abs(g - (1.45 * SR + 144)) < 0.5e-3 * SR for g in got_a2)
    assert a_end == pytest.approx(1.0)
    seam2 = next(s for s in res2.seams if s["kind"] == "xfade")
    assert seam2["t"] == pytest.approx(1.5)


def test_resolve_spans_precedence_when_both_j_and_l():
    segs = [TimelineSegment(seg_id="seg001", src_in_us=0, src_out_us=1_000_000, out_start=F(0), out_end=F(1),
                            audio_src_in_us=0, audio_src_out_us=1_200_000),
            TimelineSegment(seg_id="seg002", src_in_us=3_000_000, src_out_us=4_000_000, out_start=F(1), out_end=F(2),
                            audio_src_in_us=2_900_000, audio_src_out_us=4_000_000,
                            seam_in=SeamTreatment(kind="jcut", lead_ms=100))]
    tl = Timeline(fps=F(30), duration=F(2), segments=segs)
    plans, _ = A.resolve_audio_spans(tl)
    assert F(9, 10) == plans[0].E and F(9, 10) == plans[1].S
    segs[1] = segs[1].model_copy(update={"seam_in": SeamTreatment(kind="lcut", lead_ms=200)})
    plans, _ = A.resolve_audio_spans(Timeline(fps=F(30), duration=F(2), segments=segs))
    assert F(6, 5) == plans[0].E and F(6, 5) == plans[1].S
    segs[1] = segs[1].model_copy(update={"seam_in": SeamTreatment(kind="cut")})
    plans, _ = A.resolve_audio_spans(Timeline(fps=F(30), duration=F(2), segments=segs))
    assert F(21, 20) == plans[0].E  # midpoint of 0.9 and 1.2


# ============================================================================================== seams
def _continuous_source(seconds: float = 6.0, seed: int = 6) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(round(seconds * SR)) / SR
    return (0.25 * np.sin(2 * np.pi * 220 * t) + 0.15 * np.sin(2 * np.pi * 331 * t + 1.0)
            + 0.003 * rng.standard_normal(t.size))


def test_seams_have_no_discontinuity_spikes():
    src = _continuous_source()
    tl = make_timeline([dict(src_in=0.5, src_out=1.537), dict(src_in=2.911, src_out=4.0),
                        dict(src_in=1.2, src_out=2.0)])
    res = A.assemble_dialogue(tl, src, SR)
    y = res.audio
    seams = [s for s in res.seams if s["kind"] == "xfade"]
    assert len(seams) == 2
    d1 = np.abs(np.diff(y))
    typical = np.percentile(d1, 99.9)
    for s in seams:
        n = s["sample"]
        assert s["click_db"] is not None and s["click_db"] < 3.0
        assert d1[n - 480: n + 480].max() <= 1.2 * typical
    # sanity: the same cut as a butt splice has a detectable click
    a0 = round(1.537 * SR) - round(0.5 * SR)
    naive = np.concatenate((src[round(0.5 * SR): round(1.537 * SR)], src[round(2.911 * SR): round(4.0 * SR)]))
    assert A.seam_click_db(naive, SR, a0) > 6.0


def test_crossfade_length_adapts_to_seam_energy_and_stays_in_range():
    words = evenly_spaced_words(10.0, word_s=0.4, gap_s=0.4)
    x = synth_voice(10.0, words, level_db=-20, noise_db=-62, seed=7)
    # words at 0.4 + 0.8k (0.4 s long): pauses are [0.8 + 0.8k, 1.2 + 0.8k] — quiet seams cut mid-pause
    q = make_timeline([dict(src_in=0.2, src_out=1.0), dict(src_in=2.6, src_out=4.2), dict(src_in=5.0, src_out=6.6)])
    rq = A.assemble_dialogue(q, x, SR)
    xs = [s for s in rq.seams if s["kind"] == "xfade"]
    assert [s["xfade_ms"] for s in xs] == [30.0, 30.0]
    assert all(s["placement"] == "symmetric" for s in xs)
    # loud seams: cut inside words on both sides (continuous vowel material)
    w = words
    loud = make_timeline([dict(src_in=0.2, src_out=w[1][0] + 0.2), dict(src_in=w[4][0] + 0.15, src_out=w[6][1])])
    rl = A.assemble_dialogue(loud, x, SR)
    s = next(s for s in rl.seams if s["kind"] == "xfade")
    assert 10.0 <= s["xfade_ms"] < 20.0
    # the outgoing handle contains the next word's onset -> fade placed before the seam
    onset = make_timeline([dict(src_in=0.2, src_out=w[1][0] - 0.004), dict(src_in=w[3][1] + 0.2, src_out=w[5][1])])
    ro = A.assemble_dialogue(onset, x, SR)
    so = next(s for s in ro.seams if s["kind"] == "xfade")
    assert so["placement"] == "before"
    assert 10.0 <= so["xfade_ms"] <= 30.0
    # the incoming handle contains a word tail -> fade placed after the seam
    tail = make_timeline([dict(src_in=0.2, src_out=1.0), dict(src_in=w[3][1] + 0.004, src_out=w[5][1])])
    rt = A.assemble_dialogue(tail, x, SR)
    st = next(s for s in rt.seams if s["kind"] == "xfade")
    assert st["placement"] == "after"


def test_equal_gain_when_rejoining_one_continuous_sound():
    src = _continuous_source(seed=8)
    # contiguous in source and exactly on the 30 fps grid: the two pieces are the same samples
    tl = make_timeline([dict(src_in=1.0, src_out=2.0), dict(src_in=2.0, src_out=3.0)])
    res = A.assemble_dialogue(tl, src, SR)
    s = next(s for s in res.seams if s["kind"] == "xfade")
    assert s["r"] > 0.99 and s["law"] == "equal_gain"
    n = s["sample"]
    seg = slice(n - 2000, n + 2000)
    assert np.allclose(res.audio[seg], src[round(1.0 * SR):][seg], atol=1e-6)


def test_equal_power_keeps_level_for_uncorrelated_material():
    rng = np.random.default_rng(9)
    src = rng.standard_normal(SR * 6) * 0.05
    tl = make_timeline([dict(src_in=0.5, src_out=1.5), dict(src_in=3.0, src_out=4.0)])
    res = A.assemble_dialogue(tl, src, SR)
    s = next(s for s in res.seams if s["kind"] == "xfade")
    assert s["law"] == "equal_power" and s["xfade_ms"] == 30.0
    n, half = s["sample"], round(0.015 * SR)
    inside = res.audio[n - half: n + half]
    around = np.concatenate((res.audio[n - 20 * half: n - 2 * half], res.audio[n + 2 * half: n + 20 * half]))
    assert abs(db(inside) - db(around)) < 0.5


@needs_rubberband
def test_time_stretch_length_pitch_and_level():
    t = np.arange(round(1.3 * SR)) / SR
    x = 0.3 * np.sin(2 * np.pi * 440 * t)
    y = A.time_stretch(x, SR, 1.25)
    assert y.size == round(x.size / 1.25)
    f, P = sps.welch(y[2000:-2000], fs=SR, nperseg=8192)
    assert abs(f[np.argmax(P)] - 440) < 6
    assert abs(db(y[4000:-4000]) - db(x[4000:-4000])) < 0.2
    hot = A.time_stretch(3.0 * x, SR, 0.8)  # rubberband clamps at 1.0 unless pre-scaled
    assert np.max(np.abs(hot)) > 0.85 and abs(db(hot[4000:-4000]) - db(3 * x[4000:-4000])) < 0.2


@needs_rubberband
def test_speed_segment_markers_land_on_the_mapped_output_times():
    n = SR * 6
    rng = np.random.default_rng(10)
    voice = rng.standard_normal(n) * 0.001
    marks = [1.25, 1.9, 2.6, 3.3]
    for tm in marks:
        voice[round(tm * SR): round(tm * SR) + 288] += burst(SR, 2500)
    tl = make_timeline([dict(src_in=0.5, src_out=1.0), dict(src_in=1.0, src_out=3.6, speed=1.25)])
    seg = tl.segments[1]
    res = A.assemble_dialogue(tl, voice, SR)
    assert res.audio.size == tl.sample_count
    got = marker_positions(res.audio, SR, 2500)
    exp = [float((seg.out_start + (F(round(tm * 1e6), 1_000_000) - F(1)) / F(5, 4)) * SR) + 144 / 1.25 for tm in marks]
    assert len(got) == len(exp)
    for g, e in zip(got, exp, strict=True):
        assert abs(g - e) < 3e-3 * SR  # within 3 ms (R3 alignment measured ≲1 ms)


# ============================================================================================== room tone
def test_room_tone_fills_holes_edges_and_dropouts():
    words = evenly_spaced_words(10.0, word_s=0.4, gap_s=0.5)
    x = synth_voice(10.0, words, level_db=-20, noise_db=-58, seed=11)
    ix = make_index(words, 10.0)
    # a digital-silence dropout inside a kept pause
    drop = slice(round((words[2][1] + 0.1) * SR), round((words[2][1] + 0.3) * SR))
    x[drop] = 0.0
    tl = make_timeline([dict(src_in=0.2, src_out=2.8), dict(src_in=4.4, src_out=6.2, gap_frames=9)],
                       lead_frames=6, tail_frames=12)
    tone = V.build_room_tone(x, SR, ix, tl.sample_count / SR)
    res = A.assemble_dialogue(tl, x, SR, tone=tone, index=ix)
    floor = V.noise_floor_db(x[x != 0], SR)
    _c, lv = V.frame_levels_db(res.audio, SR, 0.02, 0.01)
    assert lv.min() > floor - 7.0  # never dead: holes, lead/tail, dropout all carry room tone
    kinds = {s["kind"] for s in res.seams}
    assert {"roomtone_in", "roomtone_out"} <= kinds
    h0 = sample_index(tl.segments[0].out_end + F(1, 10), SR)
    h1 = sample_index(tl.segments[1].out_start - F(1, 10), SR)
    hole = res.audio[h0:h1]
    assert abs(db(hole) - floor) < 3.0
    lead = res.audio[: sample_index(F(1, 10), SR)]
    assert abs(db(lead) - floor) < 3.0
    # without room tone the hole is digital silence (what the fill prevents)
    bare = A.assemble_dialogue(tl, x, SR, tone=None)
    hb = bare.audio[h0:h1]
    assert np.max(np.abs(hb)) == 0.0


# ============================================================================================== ducking
def test_duck_envelope_lookahead_hold_and_release():
    p = A.MixParams()
    n = SR * 10
    d = A.duck_envelope([(2 * SR, 3 * SR), (round(3.8 * SR), round(4.5 * SR)), (8 * SR, 9 * SR)], n, SR, params=p)
    assert d[round(1.7 * SR)] == 0.0  # before the look-ahead
    assert d[round((2 - 0.05) * SR)] == 1.0  # fully ducked 50 ms before the first word
    assert d[round(3.4 * SR)] == 1.0  # 0.8 s gap is held
    assert d[round(6.0 * SR)] == 0.0  # released in the 3.5 s gap
    assert 0 < d[round((4.5 + 0.15 + 0.35) * SR)] < 1  # mid-release
    assert np.all(np.diff(d[: round(2 * SR)]) >= 0)


def _job_with_voice(work_dir: Path, x: np.ndarray, ix, name: str = "audio-job") -> Job:
    job = Job.create(name, work_dir=work_dir)
    job.save_media_info(ix.media)
    job.save_index(ix)
    V.write_audio(job.audio_path, x, SR)
    return job


def test_music_level_under_speech_and_ducking_depth(work_dir: Path):
    words = [(0.5, 0.9), (1.0, 1.4), (1.5, 1.9), (2.0, 2.4), (6.0, 6.4), (6.5, 6.9), (7.0, 7.4)]
    x = synth_voice(9.0, words, level_db=-20, noise_db=-62, seed=12)
    ix = make_index(words, 9.0)
    job = _job_with_voice(work_dir, x, ix)
    tl = make_timeline([dict(src_in=0.0, src_out=8.4)], words=words)
    t = np.arange(tl.sample_count) / SR
    bed = 0.2 * np.sin(2 * np.pi * 150 * t)  # below the 1-4 kHz carve band
    tl = tl.model_copy(update={"music": TimelineMusic(out_start=F(0), out_end=tl.duration, duck_db=8.0,
                                                      level_lu_under_speech=-18.0)})
    r = A.render_audio(job, CutDocument(job_id=job.id), tl, work_dir / "r1", index=ix, music=np.vstack([bed, bed]),
                       sfx=None)
    mus, _ = sf.read(r.stems["music"], always_2d=True)
    dia, _ = sf.read(r.stems["dialogue"])
    m = mus.T
    speech = slice(round(1.0 * SR), round(2.0 * SR))
    gap = slice(round(3.4 * SR), round(4.8 * SR))  # released, before the next pre-duck
    assert db(m[:, gap]) - db(m[:, speech]) == pytest.approx(8.0, abs=0.5)
    # the bed is ~18 LU under the dialogue while speech plays
    d_st = np.vstack([dia, dia])
    L_d = A.integrated_loudness(d_st[:, round(0.5 * SR): round(2.4 * SR)], SR)
    L_m = A.integrated_loudness(m[:, round(0.5 * SR): round(2.4 * SR)], SR)
    assert L_d - L_m == pytest.approx(18.0, abs=1.5)
    # look-ahead: already ducked 60 ms before the first word of the second phrase
    pre = slice(round((6.0 - 0.06) * SR) - 480, round((6.0 - 0.06) * SR) + 480)
    assert db(m[:, pre]) - db(m[:, speech]) < 0.5
    rep = json.loads(r.report.read_text())
    assert rep["music"]["activity_source"] == "word_map" and rep["music"]["duck_db"] == 8.0


# ============================================================================================== render
def test_render_audio_end_to_end(work_dir: Path, tmp_path: Path):
    words = evenly_spaced_words(12.0)
    x = synth_voice(12.0, words, level_db=-24, noise_db=-62, seed=13, sibilant=(3,))
    ix = make_index(words, 12.0)
    job = _job_with_voice(work_dir, x, ix)
    specs = [dict(src_in=0.3, src_out=2.2), dict(src_in=3.3, src_out=5.0, lead_ms=150),
             dict(src_in=5.5, src_out=7.4, gap_frames=6), dict(src_in=8.3, src_out=11.6)]
    tl = make_timeline(specs, words=words, tail_frames=10)
    bed = np.vstack([0.2 * np.sin(2 * np.pi * 196 * np.arange(SR * 20) / SR)] * 2)
    V.write_audio(tmp_path / "bed.wav", bed, SR)
    pop = np.zeros(2400)
    pop[:240] = np.hanning(240) * np.sin(2 * np.pi * 1500 * np.arange(240) / SR)
    V.write_audio(tmp_path / "pop.wav", pop, SR)
    out_t = F(12345, SR)
    tl = tl.model_copy(update={
        "music": TimelineMusic(asset_path=str(tmp_path / "bed.wav"), out_start=F(0), out_end=tl.duration),
        "sfx": [TimelineSfx(sfx_id="fx001", kind="pop", out_t=out_t, asset_path=str(tmp_path / "pop.wav"),
                            gain_db=-10.0)],
    })
    doc = CutDocument(job_id=job.id, audio=AudioPlan(loudness_target_lufs=-16.0))
    r = A.render_audio(job, doc, tl, job.renders_dir / "r1", index=ix)
    for p in (r.mix, r.mix_nomusic, r.stems["dialogue"], r.stems["music"], r.stems["sfx"], r.report):
        assert p.exists()
    for p in (r.mix, r.mix_nomusic):
        y, sr = sf.read(p, always_2d=True)
        info = sf.info(str(p))
        assert sr == SR and y.shape == (tl.sample_count, 2) and info.subtype == "FLOAT"
        assert A.integrated_loudness(y.T, SR) == pytest.approx(-16.0, abs=0.5)
        assert tp8(y.T) <= -1.0
    d, _ = sf.read(r.stems["dialogue"])
    assert d.ndim == 1 and d.size == tl.sample_count
    sfx, _ = sf.read(r.stems["sfx"], always_2d=True)
    # timeline cues go through studio.audio.sfx (sync point on out_t, levelled against the dialogue)
    assert abs(int(np.argmax(np.abs(sfx[:, 0]))) - 12345) < 0.05 * SR
    assert 0 < np.max(np.abs(sfx)) < 0.9
    rep = json.loads(r.report.read_text())
    xf = [s for s in rep["seams"] if s["kind"] == "xfade"]
    assert len(xf) == 2 and all(5.0 <= s["xfade_ms"] <= 30.0 for s in xf)
    assert all(s["click_db"] is None or s["click_db"] < 6.0 for s in rep["seams"])
    assert rep["master"]["mix"]["integrated_lufs"] == pytest.approx(-16.0, abs=0.1)
    assert rep["music"]["source"] == "timeline_asset"
    assert r.num_samples == tl.sample_count and r.loudness["mix"]["integrated_lufs"] == pytest.approx(-16.0, abs=0.1)
    # the processed voice is cached once per chain spec
    assert len(list((job.media_dir / "voice").glob("voice_*.wav"))) == 1
    r2 = A.render_audio(job, doc, tl, job.renders_dir / "r2", index=ix)
    assert len(list((job.media_dir / "voice").glob("voice_*.wav"))) == 1
    y1, _ = sf.read(r.mix)
    y2, _ = sf.read(r2.mix)
    assert np.array_equal(y1, y2)  # deterministic


def test_render_uses_processed_voice_and_respects_disabled_chain(work_dir: Path):
    words = evenly_spaced_words(6.0)
    x = synth_voice(6.0, words, level_db=-20, noise_db=-60, seed=14)
    ix = make_index(words, 6.0)
    job = _job_with_voice(work_dir, x, ix)
    tl = make_timeline([dict(src_in=0.2, src_out=5.6)], words=words)
    on = A.render_audio(job, CutDocument(job_id=job.id), tl, work_dir / "on", index=ix, music=None, sfx=None)
    off_doc = CutDocument(job_id=job.id, audio=AudioPlan(voice=VoiceChainSpec(enabled=False)))
    off = A.render_audio(job, off_doc, tl, work_dir / "off", index=ix, music=None, sfx=None)
    a, _ = sf.read(on.stems["dialogue"])
    b, _ = sf.read(off.stems["dialogue"])
    assert a.shape == b.shape and not np.allclose(a, b, atol=1e-4)
    assert len(list((job.media_dir / "voice").glob("voice_*.wav"))) == 2


def test_render_without_dialogue_audio_still_delivers(work_dir: Path):
    words = evenly_spaced_words(4.0)
    ix = make_index(words, 4.0)
    job = Job.create("noaudio", work_dir=work_dir)
    tl = make_timeline([dict(src_in=0.2, src_out=3.2)], words=words)
    bed = 0.1 * np.sin(2 * np.pi * 300 * np.arange(tl.sample_count) / SR)
    r = A.render_audio(job, CutDocument(job_id=job.id), tl, work_dir / "r", index=ix, music=bed, sfx=None)
    y, _ = sf.read(r.mix, always_2d=True)
    assert y.shape == (tl.sample_count, 2)
    nm, _ = sf.read(r.mix_nomusic, always_2d=True)
    assert np.max(np.abs(nm)) == 0.0
    rep = json.loads(r.report.read_text())
    assert any("no dialogue audio" in w for w in rep["warnings"])
    assert A.integrated_loudness(y.T, SR) == pytest.approx(-14.0, abs=0.5)


def test_sfx_placements_are_sample_accurate_and_duck_the_music(work_dir: Path):
    words = evenly_spaced_words(5.0)
    x = synth_voice(5.0, words, level_db=-20, noise_db=-60, seed=15)
    ix = make_index(words, 5.0)
    job = _job_with_voice(work_dir, x, ix)
    tl = make_timeline([dict(src_in=0.2, src_out=4.7)], words=words)
    clip = np.zeros(4800)
    clip[100] = 1.0
    bed = np.vstack([0.1 * np.sin(2 * np.pi * 180 * np.arange(tl.sample_count) / SR)] * 2)
    at = F(3 * SR + 17, SR)
    r = A.render_audio(job, CutDocument(job_id=job.id), tl, work_dir / "r", index=ix, music=bed,
                       sfx=[A.SfxPlacement(out_t=at, audio=clip, gain_db=-6.0, sfx_id="fx001")])
    s, _ = sf.read(r.stems["sfx"], always_2d=True)
    assert int(np.argmax(np.abs(s[:, 0]))) == 3 * SR + 17 + 100
    rep = json.loads(r.report.read_text())
    assert rep["sfx"][0]["sample"] == 3 * SR + 17


def test_timeline_sfx_are_levelled_against_the_mixed_dialogue(work_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from studio.audio import sfx as sfx_mod

    words = evenly_spaced_words(5.0)
    x = synth_voice(5.0, words, level_db=-30, noise_db=-70, seed=16)
    ix = make_index(words, 5.0)
    job = _job_with_voice(work_dir, x, ix)
    tl = make_timeline([dict(src_in=0.2, src_out=4.7)], words=words)
    tl = tl.model_copy(update={"sfx": [TimelineSfx(sfx_id="fx001", kind="pop", out_t=F(2))]})
    seen: dict = {}

    def fake(job_, timeline, *, sr=48000, dialogue=None, speech_spans=None, report=None, **kw):
        seen["dialogue"], seen["spans"] = dialogue, speech_spans
        n = sample_index(timeline.duration, sr)
        out = np.zeros((2, n), dtype=np.float32)
        out[:, 2 * sr: 2 * sr + 480] = 0.1
        out[:, 3 * sr: 3 * sr + 480] = 0.1
        if report is not None:
            report.append({"sfx_id": "fx001"})
        return out

    monkeypatch.setattr(sfx_mod, "render_sfx_track", fake)
    bed = np.vstack([0.1 * np.sin(2 * np.pi * 200 * np.arange(tl.sample_count) / SR)] * 2)
    r = A.render_audio(job, CutDocument(job_id=job.id), tl, work_dir / "r", index=ix, music=bed)
    d = seen["dialogue"]
    assert d is not None and d.size == tl.sample_count
    assert A.integrated_loudness(np.vstack([d, d]), SR) == pytest.approx(-14.0, abs=0.05)
    assert seen["spans"] and all(0 <= a < b for a, b in seen["spans"])
    rep = json.loads(r.report.read_text())
    assert rep["sfx"] == [{"sfx_id": "fx001"}]
    # the bed dips under each effect separately, not across the whole stretch between them
    m, _ = sf.read(r.stems["music"], always_2d=True)
    mid = slice(round(2.5 * SR) - 240, round(2.5 * SR) + 240)
    at = slice(2 * SR + 100, 2 * SR + 380)
    assert db(m[mid].T) - db(m[at].T) > 1.5
