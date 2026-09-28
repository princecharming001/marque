"""Tests for studio.perception.audio_metrics: loudness, true peak, clipping, noise floor/SNR, RT60 proxy,
music-in-room, room tone and the Job API. Keyless and offline; slow tests compare with ffmpeg ebur128 on
the real QA media when present.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from gaps_prosody_synth import SR, add_at, asr_words, band_noise, build_take, db_to_amp, standard_take

from studio.jobs import Job
from studio.perception import audio_metrics as M
from studio.perception import gaps as G
from studio.perception.index import AudioMetrics, Word, word_id

TESTDATA = Path("/Users/home/studio-testdata")


def _sine(freq: float, amp: float, dur: float, phase: float = 0.0, sr: int = SR) -> np.ndarray:
    t = np.arange(int(dur * sr)) / sr
    return amp * np.sin(2 * np.pi * freq * t + phase)


# ---------------------------------------------------------------------------------------------- loudness / peaks
@pytest.mark.parametrize("amp", [1.0, 0.1, 0.01])
def test_integrated_loudness_calibration(amp: float):
    # BS.1770: a 997 Hz sine at 0 dBFS in one channel reads -3.01 LUFS
    x = _sine(997, amp, 5.0)
    assert M.integrated_loudness(x, SR) == pytest.approx(20 * np.log10(amp) - 3.01, abs=0.05)


def test_loudness_short_and_silent():
    assert M.integrated_loudness(np.zeros(1000), SR) is None
    assert M.integrated_loudness(np.zeros(SR), SR) is None
    assert M.loudness_range(np.zeros(SR), SR) is None


def test_loudness_range_two_levels():
    x = np.concatenate([_sine(997, 0.03, 10.0), _sine(997, 0.3, 10.0)])  # -30 and -10 dBFS peak: 20 LU apart
    lra = M.loudness_range(x, SR)
    assert lra is not None and 17.0 <= lra <= 21.0


def test_true_peak_catches_intersample_peak():
    # fs/4 sine at 45°: every sample sits at ±0.707·A, the waveform peaks at A between samples
    x = _sine(SR / 4, 0.5, 1.0, phase=np.pi / 4)
    assert M.sample_peak_dbfs(x) == pytest.approx(20 * np.log10(0.5 * np.sqrt(0.5)), abs=0.01)
    assert M.true_peak_dbtp(x, SR) == pytest.approx(20 * np.log10(0.5), abs=0.15)
    assert M.true_peak_dbtp(np.zeros(100), SR) == G.DB_FLOOR


def test_clipping_ratio():
    clean = _sine(220, 0.5, 1.0)
    assert M.clipping_ratio(clean) == (0.0, 0)
    hard = np.clip(_sine(220, 1.5, 1.0), -1, 1)
    ratio, runs = M.clipping_ratio(hard)
    expected = 1 - (2 / np.pi) * np.arcsin(0.985 / 1.5)
    assert ratio == pytest.approx(expected, abs=0.01) and runs == 440
    # clipped at 0.8 then left there: flat tops below full scale are still clipping
    turned_down = np.clip(_sine(220, 1.0, 1.0), -0.8, 0.8).astype(np.float32)
    ratio2, runs2 = M.clipping_ratio(turned_down)
    assert ratio2 > 0.3 and runs2 == 440


# ---------------------------------------------------------------------------------------------- whole-take metrics
@pytest.fixture(scope="module")
def std():
    tk = standard_take()
    feat = G.compute_features(tk.x, tk.sr)
    res = G.analyze_gaps(feat, asr_words(tk))
    return tk, feat, res


def test_noise_floor_snr_and_room_tone(std):
    tk, feat, res = std
    m = M.measure_features(feat, gaps=res.gaps, words=res.words)
    assert isinstance(m, AudioMetrics)
    assert m.noise_floor_db == pytest.approx(-62.0, abs=1.0)
    speech = [tk.x[int(w.start_s * SR):int(w.end_s * SR)] for w in tk.words]
    speech_db = 10 * np.log10(np.mean(np.concatenate(speech) ** 2))
    assert m.snr_db == pytest.approx(speech_db - (-62.0), abs=2.5)
    assert m.extras["snr_p10_db"] is not None and m.extras["snr_p10_db"] <= m.snr_db + 1
    assert m.clipping_ratio == 0.0
    assert m.integrated_lufs is not None and m.true_peak_dbtp is not None and m.true_peak_dbtp < 0
    assert m.room_tone_ranges_us == res.room_tone_ranges_us and m.room_tone_ranges_us
    assert m.extras["room_tone_min_ms"] == 300
    assert m.music_in_room is False
    assert m.extras["breaths"]["count"] == 2
    assert m.extras["gated_floor"] is False and m.extras["digital_silence_ratio"] == 0.0
    # without gaps/words (VAD-blind synthetic speech) the floor still comes out right
    m2 = M.measure_features(feat)
    assert m2.noise_floor_db == pytest.approx(-62.0, abs=1.5)


def test_digital_silence_pauses():
    ev = [{"type": "word", "text": "a", "t": 0.5, "dur": 0.5, "f0": 120, "db": -20},
          {"type": "word", "text": "b", "t": 1.6, "dur": 0.5, "f0": 125, "db": -20},
          {"type": "word", "text": "c", "t": 2.8, "dur": 0.5, "f0": 118, "db": -20}]
    tk = build_take(ev, total_s=4.0, floor_db=-300)
    feat = G.compute_features(tk.x, SR)
    res = G.analyze_gaps(feat, asr_words(tk))
    m = M.measure_features(feat, gaps=res.gaps, words=res.words)
    assert m.noise_floor_db == G.DB_FLOOR and m.snr_db == M.SNR_CAP_DB
    assert m.extras["gated_floor"] is True and m.extras["digital_silence_ratio"] > 0.5
    assert m.room_tone_ranges_us == []


def _decay_take(rt60: float, n: int = 6, seed: int = 5):
    """Noise-burst 'words' with exponential room decays of known RT60 into a -90 dB floor."""
    rng = np.random.default_rng(seed)
    total = 0.4 + n * 1.3
    x = rng.standard_normal(int(total * SR)) * db_to_amp(-90)
    words = []
    for i in range(n):
        t0 = 0.4 + i * 1.3
        burst = band_noise(0.3 + 1.0, 400, 6000, -12, seed=10 + i, shape="flat")
        tt = np.arange(burst.size) / SR
        env = np.where(tt < 0.3, 1.0, np.exp(-6.9078 * (tt - 0.3) / rt60))
        add_at(x, burst * env, t0)
        words.append(Word(id=word_id(i + 1), text="ha", start_us=int(t0 * 1e6), end_us=int((t0 + 0.3) * 1e6)))
    return x, words


@pytest.mark.parametrize("rt60", [0.25, 0.5, 0.9])
def test_rt60_proxy_on_known_decays(rt60: float):
    x, words = _decay_take(rt60)
    feat = G.compute_features(x, SR)
    res = G.analyze_gaps(feat, words)
    est, n = M.estimate_rt60(feat, res.gaps)
    assert n >= 3 and est is not None
    assert est == pytest.approx(rt60, rel=0.3)


def test_rt60_needs_decays():
    feat = G.compute_features(np.random.default_rng(0).standard_normal(SR) * 1e-3, SR)
    assert M.estimate_rt60(feat, []) == (None, 0)


def _with_bed(bed: np.ndarray):
    tk = standard_take()
    x = tk.x + bed[: tk.x.size]
    feat = G.compute_features(x, SR)
    res = G.analyze_gaps(feat, asr_words(tk))
    return feat, res


def _chords(level_db: float, total_s: float = 8.0) -> np.ndarray:
    """A quiet chord progression (triads with 3 harmonics each, new chord every 0.5 s)."""
    roots = [220.0, 246.9, 196.0, 261.6]
    x = np.zeros(int(total_s * SR))
    seg = int(0.5 * SR)
    for c in range(int(total_s / 0.5)):
        t = np.arange(seg) / SR
        r = roots[c % len(roots)]
        chord = sum(sum((0.5 ** h) * np.sin(2 * np.pi * r * ratio * (h + 1) * t)
                        for h in range(3)) for ratio in (1.0, 1.26, 1.5))
        env = np.minimum(1.0, np.minimum(t / 0.01, (0.5 - t) / 0.01))
        x[c * seg:(c + 1) * seg] = chord * env
    return x / np.sqrt(np.mean(x ** 2)) * db_to_amp(level_db)


def test_music_in_room_quiet_chords():
    feat, res = _with_bed(_chords(-46.0))
    flag, detail = M.music_in_room(feat, res.gaps, res.words)
    assert flag, detail
    assert detail["tonal_fraction"] >= 0.5  # caught by its partials although it sits ~25 dB under the voice
    feat0, res0 = _with_bed(np.zeros(8 * SR))
    assert M.music_in_room(feat0, res0.gaps, res0.words)[1]["tonal_fraction"] < 0.1


def test_music_in_room_loud_bed():
    feat, res = _with_bed(_chords(-30.0))
    m = M.measure_features(feat, gaps=res.gaps, words=res.words)
    assert m.music_in_room is True and m.extras["music"]["score"] > 0.5


@pytest.mark.parametrize("kind", ["hum", "hiss"])
def test_no_music_for_hum_or_hiss(kind: str):
    t = np.arange(8 * SR) / SR
    if kind == "hum":
        bed = sum((0.6 ** h) * np.sin(2 * np.pi * 60 * (h + 1) * t) for h in range(8))
        bed = bed / np.sqrt(np.mean(bed ** 2)) * db_to_amp(-48)
    else:
        bed = np.random.default_rng(1).standard_normal(t.size) * db_to_amp(-45)
    feat, res = _with_bed(bed)
    m = M.measure_features(feat, gaps=res.gaps, words=res.words)
    assert m.music_in_room is False, m.extras["music"]
    if kind == "hum":
        assert m.extras.get("hum", {}).get("present") is True and m.extras["hum"]["hz"] == 60.0


def test_measure_audio_job(work_dir, std):
    tk, _, res = std
    job = Job.create("metricsjob", work_dir=work_dir)
    with pytest.raises(FileNotFoundError):
        M.measure_audio(job, gaps=res.gaps)
    sf.write(job.audio_path, tk.x.astype(np.float32), SR, subtype="FLOAT")
    m = M.measure_audio(job, gaps=res.gaps)
    assert m.noise_floor_db == pytest.approx(-62.0, abs=1.0)
    m_file = M.measure_audio_file(job.audio_path, gaps=res.gaps)
    assert m_file.model_dump() == m.model_dump()


def test_short_clean_take_uses_shorter_room_tone():
    ev = [{"type": "word", "text": f"w{i}", "t": 0.25 + 0.61 * i, "dur": 0.35, "f0": 120, "db": -20} for i in range(6)]
    tk = build_take(ev, total_s=0.25 + 0.61 * 6, floor_db=-60)
    feat = G.compute_features(tk.x, SR)
    words = asr_words(tk)
    gaps = [g for g in G.analyze_gaps(feat, words).gaps if g.is_inner]  # 260 ms pauses only
    m = M.measure_features(feat, gaps=gaps, words=words)
    assert m.room_tone_ranges_us and m.extras["room_tone_min_ms"] == M.ROOM_TONE_FALLBACK_MS


# ---------------------------------------------------------------------------------------------- real media (slow)
def _extract(src: Path, dst: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or not src.exists():
        pytest.skip(f"ffmpeg or {src.name} missing")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:a:0", "-ac", "1",
                    "-ar", "48000", "-c:a", "pcm_f32le", str(dst)], check=True)
    return dst


def _ebur128(path: Path) -> tuple[float, float]:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    summary = out[out.rfind("Summary:"):]
    i = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1))  # type: ignore[union-attr]
    tp = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary).group(1))  # type: ignore[union-attr]
    return i, tp


@pytest.mark.slow
def test_real_var_silences_metrics(tmp_path):
    wav = _extract(TESTDATA / "qa-editor-var-silences.mov", tmp_path / "silences.wav")
    m = M.measure_audio_file(wav)
    ref_i, ref_tp = _ebur128(wav)
    assert m.integrated_lufs == pytest.approx(ref_i, abs=0.2)
    assert m.true_peak_dbtp == pytest.approx(ref_tp, abs=0.2)
    assert m.music_in_room is False
    assert m.extras["digital_silence_ratio"] > 0.5
    assert m.extras["gated_floor"] is True and m.noise_floor_db is not None and m.noise_floor_db < -75
    assert m.clipping_ratio == 0.0
    assert m.lra_lu is not None and 3.0 < m.lra_lu < 8.0


@pytest.mark.slow
def test_real_music_in_room(tmp_path):
    wav = _extract(TESTDATA / "qa-editor-var-music.mov", tmp_path / "music.wav")
    m = M.measure_audio_file(wav)
    assert m.music_in_room is True
    assert m.snr_db is not None and m.snr_db < 15


@pytest.mark.slow
def test_real_take40_room(tmp_path):
    wav = _extract(TESTDATA / "qa-editor-real-take40.mov", tmp_path / "take40.wav")
    m = M.measure_audio_file(wav)
    ref_i, ref_tp = _ebur128(wav)
    assert m.integrated_lufs == pytest.approx(ref_i, abs=0.2)
    assert m.true_peak_dbtp == pytest.approx(ref_tp, abs=0.2)
    assert m.music_in_room is False and m.extras["gated_floor"] is False
    assert -52 < (m.noise_floor_db or 0) < -42 and 20 < (m.snr_db or 0) < 35
    assert m.room_tone_ranges_us


def test_build_index_stage_sequence_on_a_job(work_dir, media_info):
    """refine → gaps → prosody → audio metrics on one Job (the order build_index uses) yields a valid
    Take Index; all stages share one cached analysis of media/audio.wav."""
    from studio.perception import prosody as P
    from studio.perception.index import TakeIndex

    tk = standard_take()
    job = Job.create("stagejob", work_dir=work_dir)
    sf.write(job.audio_path, tk.x.astype(np.float32), SR, subtype="FLOAT")
    words = G.refine_word_boundaries(job, asr_words(tk, jitter_s=[(0.04, -0.04)] * 7))
    gaps = G.detect_gaps(job, words)
    words, energy = P.analyze_prosody(job, words, [])
    audio = M.measure_audio(job, gaps=gaps)
    media = media_info.model_copy(update={"duration_us": int(tk.duration_s * 1e6)})
    ix = TakeIndex(media=media, words=words, gaps=gaps, audio=audio, energy=energy)
    assert ix.gap_after("w0003") is not None and ix.gap_after("w0001") is None
    assert ix.get_gaps(min_ms=500) and all(g.snap_us >= g.start_us for g in ix.gaps)
    text = ix.render_transcript("full")
    for token in ("[g0001 0.40s silence]", "[g0004 0.85s]", "[g0005 0.35s breath]", "[g0006 0.70s noise]"):
        assert token in text, text
    assert ix.audio is not None and ix.audio.room_tone_ranges_us
    assert G.load_features(job.audio_path) is G.load_features(job.audio_path)
