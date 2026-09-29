"""Tests for studio.perception.gaps: boundary refinement, gaps, breaths, snap points, room tone.

Keyless and offline (Silero's weights ship inside the silero-vad wheel). Synthetic takes have exact
ground truth (see ``gaps_prosody_synth``); slow tests run on the real QA media when present.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from gaps_prosody_synth import SR, asr_words, build_take, harmonic_word, standard_take

from studio.jobs import Job
from studio.perception import gaps as G
from studio.perception.index import CharTime, Gap, TakeIndex, Word, word_id

TESTDATA = Path("/Users/home/studio-testdata")
HERE = Path(__file__).parent


# ---------------------------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def std_take():
    return standard_take()


@pytest.fixture(scope="module")
def std_feat(std_take):
    return G.compute_features(std_take.x, std_take.sr)


@pytest.fixture(scope="module")
def std_analysis(std_take, std_feat):
    return G.analyze_gaps(std_feat, asr_words(std_take))


def _us(s: float) -> int:
    return round(s * 1e6)


def _gap_between(gaps: list[Gap], after: str | None, before: str | None) -> Gap:
    for g in gaps:
        if g.after_word_id == after and g.before_word_id == before:
            return g
    raise AssertionError(f"no gap {after}->{before}: {[(g.after_word_id, g.before_word_id) for g in gaps]}")


# ---------------------------------------------------------------------------------------------- features
def test_features_grid_levels_and_vad(std_take, std_feat):
    f = std_feat
    assert f.hop == 480 and f.win == 960 and f.hop_us == 10_000
    assert f.n_frames == len(std_take.x) // f.hop + 1
    assert f.t_us[0] == 0 and f.t_us[1] == 10_000
    assert f.duration_us == _us(8.0)
    # white floor at -62 dBFS (full band ≥50 Hz)
    assert f.noise_db == pytest.approx(-62.0, abs=1.5)
    assert f.vbs is not None and f.vbs.noise_sigma < 3.0
    assert f.speech_db > -26.0
    assert f.vad.shape == (f.n_frames,) and float(f.vad.min()) >= 0.0 and float(f.vad.max()) <= 1.0
    # pitch tracks the 118-135 Hz synthetic voice
    voiced_f0 = f.pitch.f0_hz[f.pitch.voiced]
    assert 110 < float(np.median(voiced_f0)) < 140
    assert 50 <= f.pitch.floor_hz < 118 and f.pitch.ceiling_hz > 135


def test_vad_probabilities_shape():
    rng = np.random.default_rng(0)
    probs, chunk_s = G.vad_probabilities(rng.standard_normal(16_000).astype(np.float32) * 0.01, 16_000)
    assert chunk_s == pytest.approx(0.032)
    assert probs.shape == (32,)
    assert np.all((probs >= 0) & (probs <= 1))


def test_digital_silence_is_flagged():
    x = np.zeros(SR)
    x[SR // 2:] = harmonic_word(0.5, 120, -20)
    f = G.compute_features(x, SR)
    assert f.digital[: f.frame_at(400_000)].all()
    assert not f.digital[f.frame_at(700_000)]
    assert f.db[0] == G.DB_FLOOR


def test_load_features_cache(tmp_path, std_take):
    p = tmp_path / "a.wav"
    sf.write(p, std_take.x.astype(np.float32), SR, subtype="FLOAT")
    G.clear_feature_cache()
    a = G.load_features(p)
    assert G.load_features(p) is a
    sf.write(p, (std_take.x * 0.5).astype(np.float32), SR, subtype="FLOAT")
    b = G.load_features(p)
    assert b is not a


# ---------------------------------------------------------------------------------------------- refinement
def test_refine_exact_asr_times_is_accurate(std_take, std_analysis):
    for w, t in zip(std_analysis.words, std_take.words, strict=True):
        assert abs(w.start_us - _us(t.start_s)) <= 3_000, (w.text, w.start_us, t.start_s)
        assert abs(w.end_us - _us(t.end_s)) <= 3_000, (w.text, w.end_us, t.end_s)


JITTERS = [
    [(-0.05, 0.04), (0.03, -0.05), (0.06, -0.07), (-0.07, 0.05), (-0.08, 0.02), (0.05, 0.06), (-0.04, -0.06)],
    [(0.07, -0.03), (-0.02, 0.07), (-0.06, 0.08), (0.04, -0.08), (0.03, -0.04), (-0.07, -0.02), (0.08, 0.07)],
]


@pytest.mark.parametrize("jitter", JITTERS)
def test_refine_recovers_jittered_asr_edges(std_take, std_feat, jitter):
    """ASR edges off by up to ±80 ms (incl. a sibilant tail cut short by 60-80 ms and a start inside the
    pre-onset breath) come back to the acoustic edges within a few ms."""
    words = asr_words(std_take, jitter_s=jitter)
    refined = G.refine_word_boundaries(std_feat, words)
    assert [w.id for w in refined] == [w.id for w in words]
    for w, a, t in zip(refined, words, std_take.words, strict=True):
        assert abs(w.start_us - a.start_us) <= G.MAX_SHIFT_US and abs(w.end_us - a.end_us) <= G.MAX_SHIFT_US
        assert abs(w.start_us - _us(t.start_s)) <= 5_000, (w.text, w.start_us / 1e6, t.start_s)
        assert abs(w.end_us - _us(t.end_s)) <= 5_000, (w.text, w.end_us / 1e6, t.end_s)


def test_refine_keeps_sibilant_tail(std_take, std_feat):
    words = asr_words(std_take)
    # ASR ends "cuts" 70 ms before its /s/ tail does
    words[2] = words[2].model_copy(update={"end_us": _us(1.92)})
    refined = G.refine_word_boundaries(std_feat, words)
    assert refined[2].end_us == pytest.approx(_us(1.99), abs=5_000)
    # missing more than the ±80 ms window allows: the edge stops at the window limit, never inside the word
    words[2] = words[2].model_copy(update={"end_us": _us(1.88)})
    assert G.refine_word_boundaries(std_feat, words)[2].end_us == _us(1.88) + G.MAX_SHIFT_US


def test_refine_does_not_absorb_breath(std_take, std_feat):
    words = asr_words(std_take)
    # ASR starts "real" 60 ms early, inside the inhale that ends at 3.90
    words[4] = words[4].model_copy(update={"start_us": _us(3.89)})
    refined = G.refine_word_boundaries(std_feat, words)
    assert refined[4].start_us == pytest.approx(_us(3.95), abs=5_000)


def test_refine_is_stable_on_its_own_output(std_take, std_feat):
    once = G.refine_word_boundaries(std_feat, asr_words(std_take, jitter_s=JITTERS[0]))
    twice = G.refine_word_boundaries(std_feat, once)
    shifts = [max(abs(a.start_us - b.start_us), abs(a.end_us - b.end_us)) for a, b in zip(once, twice, strict=True)]
    assert max(shifts) <= 2_000


def test_refine_never_moves_more_than_80ms():
    # one long continuous voiced stretch; ASR claims the word ends 300 ms early
    x = build_take([{"type": "word", "text": "long", "t": 0.3, "dur": 1.2, "f0": 120, "db": -20}], total_s=2.0).x
    f = G.compute_features(x, SR)
    w = [Word(id="w0001", text="long", start_us=_us(0.3), end_us=_us(1.2))]
    out = G.refine_word_boundaries(f, w)[0]
    assert out.end_us == _us(1.2) + G.MAX_SHIFT_US
    assert abs(out.start_us - _us(0.3)) <= 3_000


def test_refine_abutting_words_split_at_junction_dip():
    ev = [{"type": "word", "text": "one", "t": 0.4, "dur": 0.4, "f0": 120, "db": -20},
          {"type": "word", "text": "two", "t": 0.8, "dur": 0.4, "f0": 125, "db": -20}]
    tk = build_take(ev, total_s=1.6, floor_db=-62)
    f = G.compute_features(tk.x, SR)
    words = asr_words(tk, jitter_s=[(0.0, 0.035), (0.03, 0.0)])  # ASR overlap around the junction
    out = G.refine_word_boundaries(f, words)
    assert out[0].end_us == out[1].start_us
    assert out[0].end_us == pytest.approx(_us(0.80), abs=12_000)
    gaps = G.detect_gaps(f, out)
    assert all(not (g.after_word_id == "w0001" and g.before_word_id == "w0002") for g in gaps)


def test_refine_skips_low_contrast_background():
    ev = [{"type": "word", "text": "a", "t": 0.4, "dur": 0.4, "f0": 120, "db": -22},
          {"type": "word", "text": "b", "t": 1.2, "dur": 0.4, "f0": 120, "db": -22}]
    tk = build_take(ev, total_s=2.0, floor_db=-26.0)
    f = G.compute_features(tk.x, SR)
    words = asr_words(tk, jitter_s=[(-0.05, 0.05), (0.05, -0.05)])
    assert G.refine_word_boundaries(f, words) == words


def test_refine_leaves_overlapping_event_alone(std_feat):
    words = [Word(id="w0001", text="Most", start_us=_us(0.40), end_us=_us(0.80)),
             Word(id="w0002", text="(laughter)", kind="event", start_us=_us(0.50), end_us=_us(0.90)),
             Word(id="w0003", text="people", start_us=_us(0.95), end_us=_us(1.16))]
    out = G.refine_word_boundaries(std_feat, words)
    assert out[0].end_us == _us(0.80) and out[1].start_us == _us(0.50)
    assert all(a.start_us <= b.start_us for a, b in pairwise(out))


def test_refine_clamps_char_times(std_take, std_feat):
    words = asr_words(std_take, jitter_s=[(0.05, 0.05)] + [(0.0, 0.0)] * 6)
    w0 = words[0]
    chars = [CharTime(text=c, start_us=w0.start_us + i * 60_000, end_us=w0.start_us + (i + 1) * 60_000)
             for i, c in enumerate("Most")]
    chars[-1] = chars[-1].model_copy(update={"end_us": w0.end_us})
    words[0] = w0.model_copy(update={"chars": chars})
    out = G.refine_word_boundaries(std_feat, words)[0]
    assert out.chars is not None and len(out.chars) == 4
    assert all(out.start_us <= c.start_us <= c.end_us <= out.end_us for c in out.chars)
    assert out.chars[0].start_us == out.start_us and out.chars[-1].end_us == out.end_us


def test_refine_without_audio_returns_words(work_dir, std_take):
    job = Job.create("noaudio", work_dir=work_dir)
    words = asr_words(std_take)
    assert G.refine_word_boundaries(job, words) == words
    gaps = G.detect_gaps(job, words)
    assert gaps and all(g.energy_db is None and g.snap_us == (g.start_us + g.end_us) // 2 for g in gaps)


# ---------------------------------------------------------------------------------------------- gaps
def test_standard_take_gaps(std_take, std_analysis):
    gaps = std_analysis.gaps
    words = std_analysis.words
    assert [g.id for g in gaps] == [f"g{i:04d}" for i in range(1, len(gaps) + 1)]
    assert all(a.start_us < b.start_us for a, b in pairwise(gaps))
    by = {(g.after_word_id, g.before_word_id): g for g in gaps}
    assert ("w0001", "w0002") not in by  # abutting words: no gap
    lead = _gap_between(gaps, None, "w0001")
    trail = _gap_between(gaps, "w0007", None)
    assert lead.kind == "silence" and lead.start_us == 0 and lead.end_us == words[0].start_us
    assert trail.kind == "silence" and trail.end_us == _us(8.0)
    assert _gap_between(gaps, "w0002", "w0003").kind == "pause"
    quiet = _gap_between(gaps, "w0003", "w0004")
    assert quiet.kind == "pause" and not quiet.has_breath
    assert quiet.energy_db == pytest.approx(-62.0, abs=1.5)
    pre_breath = _gap_between(gaps, "w0004", "w0005")
    assert pre_breath.has_breath and pre_breath.kind == "pause"  # 350 ms breath in an 850 ms gap
    full_breath = _gap_between(gaps, "w0005", "w0006")
    assert full_breath.kind == "breath" and full_breath.has_breath
    beep = _gap_between(gaps, "w0006", "w0007")
    assert beep.kind == "noise" and not beep.has_breath
    # every gap spans exactly the refined word edges
    wmap = {w.id: w for w in words}
    for g in gaps:
        if g.after_word_id:
            assert g.start_us == wmap[g.after_word_id].end_us
        if g.before_word_id:
            assert g.end_us == wmap[g.before_word_id].start_us
    # the result is a valid Take Index
    TakeIndex.model_validate({"media": {"path": "x", "width": 1080, "height": 1920, "fps": "30/1",
                                         "duration_us": _us(8.0)},
                              "words": [w.model_dump() for w in words], "gaps": [g.model_dump() for g in gaps]})


def test_snap_points_sit_in_room_tone_before_breaths_and_away_from_noise(std_take, std_feat, std_analysis):
    gaps = std_analysis.gaps
    f = std_feat
    for g in gaps:
        assert g.start_us <= g.snap_us <= g.end_us
        k = f.frame_at(g.snap_us)
        assert f.vb_smooth[k] <= f.vbs.noise_med + 4.0, (g.id, f.vb_smooth[k])
    # pre-onset breath (3.55-3.90): snap before it, so the inhale stays with "real"
    assert _gap_between(gaps, "w0004", "w0005").snap_us < _us(3.55)
    # breath filling the gap (4.43-4.73): snap in the 30 ms of room tone before it
    assert _gap_between(gaps, "w0005", "w0006").snap_us < _us(4.44)
    # beep 5.35-5.75: snap outside it
    snap = _gap_between(gaps, "w0006", "w0007").snap_us
    assert not (_us(5.34) <= snap <= _us(5.76))
    # quiet gaps: snap well inside (crossfade room on both sides)
    q = _gap_between(gaps, "w0003", "w0004")
    assert q.start_us + 100_000 < q.snap_us < q.end_us - 100_000


def test_breaths_found_with_levels(std_take, std_analysis):
    br = std_analysis.breaths
    assert len(br) == 2
    for b, (ts, te) in zip(br, std_take.breaths, strict=True):
        assert _us(ts) - 20_000 <= b.start_us and b.end_us <= _us(te) + 20_000
        assert b.end_us - b.start_us >= 200_000
        assert -30.0 < b.rel_speech_db < -10.0
        assert b.gap_id is not None


def test_find_breaths_with_and_without_gaps(std_feat, std_analysis):
    all_b = G.find_breaths(std_feat)
    in_gaps = G.find_breaths(std_feat, std_analysis.gaps)
    assert len(all_b) == 2 and len(in_gaps) == 2
    assert {b.gap_id for b in in_gaps} == {g.id for g in std_analysis.gaps if g.has_breath}


def test_room_tone_ranges(std_take, std_feat, std_analysis):
    rt = std_analysis.room_tone_ranges_us
    assert rt and rt == sorted(rt)
    for s, e in rt:
        assert e - s >= 300_000
        assert any(g.start_us <= s and e <= g.end_us for g in std_analysis.gaps)
        for bs, be in std_take.breaths + std_take.noises:
            assert e <= _us(bs) or s >= _us(be)  # never overlaps a breath or the beep
        k0, k1 = std_feat.frame_ceil(s), std_feat.frame_floor(e)
        assert std_feat.vb_db[k0:k1 + 1].mean() < std_feat.vbs.noise_med + 3
    # VAD-only mode also finds room tone
    assert G.room_tone_ranges(std_feat) != []


def test_untranscribed_sound_gap_is_noise():
    ev = [{"type": "word", "text": "so", "t": 0.4, "dur": 0.4, "f0": 120, "db": -20},
          {"type": "word", "text": "uh", "t": 0.95, "dur": 0.2, "f0": 110, "db": -24},
          {"type": "word", "text": "then", "t": 1.3, "dur": 0.4, "f0": 120, "db": -20}]
    tk = build_take(ev, total_s=2.2, floor_db=-62)
    words = asr_words(tk)
    words = [words[0], words[2].model_copy(update={"id": "w0002"})]  # ASR dropped the filler
    res = G.analyze_gaps(G.compute_features(tk.x, SR), words)
    g = _gap_between(res.gaps, "w0001", "w0002")
    assert g.kind == "noise"
    assert not (_us(0.94) <= g.snap_us <= _us(1.16))


def test_short_asr_gap_in_continuous_sound_is_not_a_gap():
    tk = build_take([{"type": "word", "text": "together", "t": 0.3, "dur": 0.9, "f0": 120, "db": -20}], total_s=1.6)
    words = [Word(id="w0001", text="to", start_us=_us(0.3), end_us=_us(0.73)),
             Word(id="w0002", text="gether", start_us=_us(0.78), end_us=_us(1.2))]
    f = G.compute_features(tk.x, SR)
    gaps = G.detect_gaps(f, words)  # unrefined: a 50 ms ASR gap with no dip
    assert all(g.is_inner is False for g in gaps)


def test_digital_silence_gaps():
    ev = [{"type": "word", "text": "a", "t": 0.5, "dur": 0.4, "f0": 120, "db": -20},
          {"type": "word", "text": "b", "t": 1.6, "dur": 0.4, "f0": 125, "db": -20}]
    tk = build_take(ev, total_s=2.5, floor_db=-300)
    res = G.analyze_gaps(G.compute_features(tk.x, SR), asr_words(tk))
    inner = _gap_between(res.gaps, "w0001", "w0002")
    assert inner.kind == "silence" and inner.energy_db is not None and inner.energy_db <= -100
    assert res.room_tone_ranges_us == []
    for w, t in zip(res.words, tk.words, strict=True):
        assert abs(w.start_us - _us(t.start_s)) <= 10_000 and abs(w.end_us - _us(t.end_s)) <= 10_000


def test_job_api_and_build_gaps(work_dir, std_take):
    job = Job.create("gapjob", work_dir=work_dir)
    sf.write(job.audio_path, std_take.x.astype(np.float32), SR, subtype="FLOAT")
    words = asr_words(std_take, jitter_s=JITTERS[1])
    refined = G.refine_word_boundaries(job, words)
    gaps = G.detect_gaps(job, refined)
    assert len(gaps) == 7
    assert gaps == G.build_gaps(job.audio_path, words)  # path API refines internally, same result
    assert G.pitch_track(job).f0_hz.shape[0] == G.load_features(job.audio_path).n_frames


# ---------------------------------------------------------------------------------------------- real media (slow)
def _extract(src: Path, dst: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or not src.exists():
        pytest.skip(f"ffmpeg or {src.name} missing")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:a:0", "-ac", "1",
                    "-ar", "48000", "-c:a", "pcm_f32le", str(dst)], check=True)
    return dst


def _pseudo_words(feat: G.AudioFeatures) -> list[Word]:
    """Speech segments from Silero (as ASR stand-ins), edges nudged ±40 ms like ASR drift."""
    import soxr
    import torch
    from silero_vad import get_speech_timestamps

    y = soxr.resample(feat.x, feat.sr, 16000).astype(np.float32)
    ts = get_speech_timestamps(torch.from_numpy(y), G._silero_model(), sampling_rate=16000,
                               min_silence_duration_ms=120, speech_pad_ms=0)
    words = []
    for i, t in enumerate(ts):
        s = t["start"] / 16000 + (0.04 if i % 2 else -0.03)
        e = t["end"] / 16000 + (-0.04 if i % 3 else 0.03)
        words.append(Word(id=word_id(i + 1), text="word", start_us=max(0, _us(s)), end_us=max(_us(s), _us(e))))
    return words


@pytest.mark.slow
def test_real_var_silences(tmp_path):
    """qa-editor-var-silences: three ~20 s digital-silence holes in a clean take."""
    wav = _extract(TESTDATA / "qa-editor-var-silences.mov", tmp_path / "silences.wav")
    feat = G.load_features(wav)
    assert 0.5 < float(feat.digital.mean()) < 0.65
    words = _pseudo_words(feat)
    assert len(words) > 10
    res = G.analyze_gaps(feat, words)
    TakeIndex.model_validate({"media": {"path": "x", "width": 1080, "height": 1920, "fps": "30/1",
                                         "duration_us": feat.duration_us},
                              "words": [w.model_dump() for w in res.words], "gaps": [g.model_dump() for g in res.gaps]})
    edges = {e for d in feat.dropouts for e in (d.start_us, d.end_us)}
    for w, a in zip(res.words, words, strict=True):
        # a word the dropout cuts takes the digital edge as its own (sound runs straight into / out of the zeros)
        assert abs(w.start_us - a.start_us) <= G.MAX_SHIFT_US or (w.truncated and w.start_us in edges)
        assert abs(w.end_us - a.end_us) <= G.MAX_SHIFT_US or (w.truncated and w.end_us in edges)
    assert len(feat.dropouts) == 3 and all(d.cut_before for d in feat.dropouts)
    chopped = [w for w in res.words if w.truncated]
    assert chopped and all(w.kind == "cutoff" for w in chopped)
    assert {w.end_us for w in chopped if w.truncated in ("end", "both")} == {d.start_us for d in feat.dropouts}
    long = [g for g in res.gaps if g.duration_us >= 10_000_000]
    assert len(long) == 3 and all(g.dropouts_us for g in long)
    for g in long:
        assert g.kind == "silence" and g.energy_db is not None and g.energy_db <= -100
        assert g.start_us < g.snap_us < g.end_us
        # no word edge reaches into the digital hole
        assert feat.digital[feat.frame_ceil(g.start_us + 20_000):feat.frame_floor(g.end_us - 20_000)].mean() > 0.95
    assert all(g.start_us <= g.snap_us <= g.end_us for g in res.gaps)


@pytest.mark.slow
def test_real_take40_with_asr_words(tmp_path):
    """Real phone take in a rumbly room with real (older, drifting) ASR word times."""
    data = json.loads((HERE / "gaps_prosody_take40_words.json").read_text())
    wav = _extract(Path(data["media"]), tmp_path / "take40.wav")
    feat = G.load_features(wav)
    words = [Word(id=word_id(i + 1), text=w["text"], start_us=w["start_ms"] * 1000, end_us=w["end_ms"] * 1000)
             for i, w in enumerate(data["words"])]
    res = G.analyze_gaps(feat, words)
    wmap = {w.id: w for w in res.words}
    for w, a in zip(res.words, words, strict=True):
        assert abs(w.start_us - a.start_us) <= G.MAX_SHIFT_US and abs(w.end_us - a.end_us) <= G.MAX_SHIFT_US
    # quiet inhales before sentences are found (only visible above 3 kHz in this room)
    assert len(res.breaths) >= 6
    assert all(-25 < b.rel_speech_db < -8 for b in res.breaths)
    # the /st/ tail of "first." (24.52-24.92) is speech, not a breath: the word keeps it
    first = next(w for w in res.words if w.text == "first.")
    assert first.end_us >= 24_830_000
    # the 1.3 s pause after "cuisines—" is plain room tone with a clean snap and the take's room tone
    g = next(g for g in res.gaps if g.after_word_id == "w0029")
    assert g.kind == "pause" and g.duration_us > 1_200_000 and not g.has_breath
    assert any(s >= g.start_us and e <= g.end_us for s, e in res.room_tone_ranges_us)
    # snaps sit near the room floor, before the inhale when there is room
    for gp in res.gaps:
        if gp.is_inner and gp.duration_us >= 150_000 and gp.kind != "noise":
            k = feat.frame_at(gp.snap_us)
            assert feat.vb_smooth[k] <= feat.vbs.noise_med + 8.0, gp.id
    pre = [gp for gp in res.gaps if gp.has_breath and gp.kind != "noise"]
    assert pre
    br_by_gap = {b.gap_id: b for b in res.breaths}
    before = sum(gp.snap_us <= br_by_gap[gp.id].start_us for gp in pre if gp.id in br_by_gap)
    assert before >= len(pre) // 2
    assert wmap["w0001"].start_us < 680_000


def test_refine_word_never_spans_a_dropout():
    """Real retake join (qa-editor-var-multitake): the first pass breaks off in "proof" and the recording drops
    to digital zero for 1.2 s; Scribe stretched "proof..." over the whole dropout (29.18-30.64 s), so the gap
    before the retake read 0.08 s. The word must end where its sound ends."""
    ev = [{"type": "word", "text": "that", "t": 0.40, "dur": 0.30, "f0": 120, "db": -20},
          {"type": "word", "text": "proof", "t": 0.72, "dur": 0.40, "f0": 118, "db": -20},
          {"type": "word", "text": "They", "t": 2.20, "dur": 0.30, "f0": 125, "db": -20}]
    tk = build_take(ev, total_s=3.0, floor_db=-70)
    x = tk.x.copy()
    x[round(0.84 * SR):round(2.05 * SR)] = 0.0  # the take is broken off inside "proof", then a dropout
    f = G.compute_features(x, SR)
    words = [Word(id="w0001", text="that", start_us=_us(0.40), end_us=_us(0.70)),
             Word(id="w0002", text="proof...", start_us=_us(0.72), end_us=_us(2.12)),  # ASR: over the dropout
             Word(id="w0003", text="They", start_us=_us(2.20), end_us=_us(2.50))]
    out = G.refine_word_boundaries(f, words)
    assert out[1].end_us == pytest.approx(_us(0.84), abs=15_000)
    assert out[1].start_us == pytest.approx(_us(0.72), abs=15_000)
    assert out[2].start_us == pytest.approx(_us(2.20), abs=15_000)
    gap = _gap_between(G.detect_gaps(f, out), "w0002", "w0003")
    assert gap.end_us - gap.start_us >= 1_300_000 and gap.kind == "silence"


def test_recording_that_stops_on_another_syllable():
    """Real multitake ending: "truth" decays into a dip, then the file ends 30 ms into the next syllable. ASR
    gives "truth" everything up to the end of the file; the word must end at the dip and the fragment after
    it must read as sound ('noise'), so no out-point keeps the chopped onset."""
    ev = [{"type": "word", "text": "different", "t": 0.40, "dur": 0.40, "f0": 120, "db": -18},
          {"type": "word", "text": "truth", "t": 0.86, "dur": 0.24, "f0": 118, "db": -18},
          {"type": "word", "text": "next", "t": 1.16, "dur": 0.30, "f0": 125, "db": -16}]
    tk = build_take(ev, total_s=1.20, floor_db=-80)  # the file ends 40 ms into "next"
    f = G.compute_features(tk.x, SR)
    words = [Word(id="w0001", text="different", start_us=_us(0.40), end_us=_us(0.80)),
             Word(id="w0002", text="truth", start_us=_us(0.86), end_us=f.duration_us)]
    res = G.analyze_gaps(f, words)
    assert res.words[1].end_us <= _us(1.14)
    assert res.words[1].end_us >= _us(1.07)
    tail = _gap_between(res.gaps, "w0002", None)
    assert tail.kind == "noise" and tail.snap_us <= _us(1.16)
    # a take that ends in room tone keeps its word end and a clean trailing silence
    tk2 = build_take(ev[:2], total_s=1.60, floor_db=-80)
    f2 = G.compute_features(tk2.x, SR)
    res2 = G.analyze_gaps(f2, [words[0], words[1].model_copy(update={"end_us": _us(1.10)})])
    assert abs(res2.words[1].end_us - _us(1.10)) <= 15_000
    assert _gap_between(res2.gaps, "w0002", None).kind == "silence"
