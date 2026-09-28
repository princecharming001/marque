"""Tests for studio.audio.voice (keyless, offline; one optional real ElevenLabs isolation call)."""

from __future__ import annotations

import io
import itertools
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf
from audio_voice_synth import SR, db, evenly_spaced_words, make_index, synth_voice
from scipy import signal as sps

from studio.audio import voice as V
from studio.config import Settings
from studio.doc.model import EqBand, VoiceChainSpec
from studio.jobs import Job

WORDS = evenly_spaced_words(10.0)


def voice(noise_db: float = -62.0, **kw) -> np.ndarray:
    return synth_voice(10.0, WORDS, noise_db=noise_db, **kw)


def region_db(x: np.ndarray, spans: list[tuple[float, float]], *, trim: float = 0.05) -> float:
    parts = [x[round((a + trim) * SR): round((b - trim) * SR)] for a, b in spans]
    return db(np.concatenate(parts))


def gaps_of(words: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(a[1], b[0]) for a, b in itertools.pairwise(words)]


# ============================================================================================== measure/plan
def test_measure_floor_snr_f0():
    x = voice(noise_db=-60.0, level_db=-20.0, f0=150.0)
    m = V.measure_voice(None, make_index(WORDS, 10.0), audio=x, sr=SR)
    assert m["noise_floor_db"] == pytest.approx(-60.0, abs=2.5)
    assert m["snr_db"] == pytest.approx(40.0, abs=3.5)
    assert 120 < m["f0_p5_hz"] < 150 < m["f0_median_hz"] + 10
    assert m["hum"] is None and not m["isolation_detected"]
    assert abs(m["rumble_db"]) < 6  # pink room noise is not rumble


def test_plan_clean_recording_gets_a_gentle_chain():
    ix = make_index(WORDS, 10.0)
    spec = V.plan_voice_chain(None, ix, audio=voice(), sr=SR)
    assert spec.denoise == "none" and spec.eq == [] and spec.deess_db == 0.0 and spec.breath_atten_db == 0.0
    assert spec.compression_db <= 3.0 and spec.comp_ratio <= 2.0 and spec.leveler
    assert 60.0 <= spec.hpf_hz <= 100.0
    assert "SNR" in spec.notes and "HPF" in spec.notes


@pytest.mark.parametrize(("noise_db", "expect", "provider"), [(-35.0, "light", None), (-26.0, "isolate", "elevenlabs")])
def test_plan_denoise_follows_snr_bands(noise_db: float, expect: str, provider: str | None):
    ix = make_index(WORDS, 10.0)
    spec = V.plan_voice_chain(None, ix, audio=voice(noise_db=noise_db), sr=SR)
    assert spec.denoise == expect and spec.isolation_provider == provider
    assert spec.compression_db >= 4.0


def test_plan_prefers_index_snr_and_music_in_room():
    ix = make_index(WORDS, 10.0, snr_db=15.0)
    assert V.plan_voice_chain(None, ix, audio=voice(), sr=SR).denoise == "light"
    ix2 = make_index(WORDS, 10.0, music_in_room=True)
    assert V.plan_voice_chain(None, ix2, audio=voice(), sr=SR).denoise == "isolate"


def test_plan_skips_denoise_when_phone_isolation_already_ran():
    x = voice(noise_db=-110.0)
    spec = V.plan_voice_chain(None, make_index(WORDS, 10.0), audio=x, sr=SR)
    assert spec.denoise == "none" and "digital silence" in spec.notes


def test_plan_cuts_measured_mud_only():
    import pedalboard

    x = voice()
    dry = synth_voice(10.0, WORDS, noise_db=-200.0)
    muddy = pedalboard.PeakFilter(cutoff_frequency_hz=300, gain_db=8.0, q=1.2)(dry[None, :].astype(np.float32), SR)[0]
    muddy = muddy.astype(np.float64) + (x - dry)
    spec = V.plan_voice_chain(None, make_index(WORDS, 10.0), audio=muddy, sr=SR)
    cuts = [b for b in spec.eq if b.type == "peak" and 200 <= b.freq_hz <= 450 and b.gain_db < 0]
    assert len(cuts) == 1 and -4.0 <= cuts[0].gain_db <= -2.0 and cuts[0].q == 1.0
    assert "mud" in spec.notes


def test_plan_hpf_stays_below_f0_and_rises_for_boom():
    low = synth_voice(10.0, WORDS, f0=82.0)
    spec = V.plan_voice_chain(None, make_index(WORDS, 10.0), audio=low, sr=SR)
    m = V.measure_voice(None, make_index(WORDS, 10.0), audio=low, sr=SR)
    assert spec.hpf_hz <= 0.9 * m["f0_p5_hz"] + 1 and spec.hpf_hz >= 60
    import pedalboard

    dry = synth_voice(10.0, WORDS, f0=140.0, noise_db=-200.0)
    boomy = pedalboard.LowShelfFilter(cutoff_frequency_hz=200, gain_db=14.0, q=0.7)(
        dry[None, :].astype(np.float32), SR)[0].astype(np.float64) + synth_voice(10.0, [], noise_db=-62.0, seed=9)
    spec_b = V.plan_voice_chain(None, make_index(WORDS, 10.0), audio=boomy, sr=SR)
    assert spec_b.hpf_hz >= 100 and "boom" in spec_b.notes
    mb = V.measure_voice(None, make_index(WORDS, 10.0), audio=boomy, sr=SR)
    assert spec_b.hpf_hz <= 0.9 * mb["f0_p5_hz"] + 1
    plain = V.plan_voice_chain(None, make_index(WORDS, 10.0), audio=voice(f0=110.0), sr=SR)
    assert "boom" not in plain.notes  # a normal male voice is not "boomy"


def test_plan_notches_mains_hum():
    x = voice()
    t = np.arange(x.size) / SR
    hum = sum(10 ** (-48 / 20) / k * np.sin(2 * np.pi * 60 * k * t) for k in (1, 2, 3, 4))
    spec = V.plan_voice_chain(None, make_index(WORDS, 10.0), audio=x + hum, sr=SR)
    notches = sorted(round(b.freq_hz) for b in spec.eq if b.q == 10.0 and b.gain_db < 0)
    assert {120, 180, 240} <= set(notches) and 60 not in notches  # 60 Hz is below the HPF already
    assert "hum" in spec.notes


def test_plan_deess_only_when_sibilance_measured():
    ix = make_index(WORDS, 10.0)
    harsh = V.plan_voice_chain(None, ix, audio=voice(sibilant=(1, 4, 7, 10), sib_rel_db=6.0), sr=SR)
    assert 4.0 <= harsh.deess_db <= 6.0
    soft = V.plan_voice_chain(None, ix, audio=voice(sibilant=(1, 4, 7, 10), sib_rel_db=-18.0), sr=SR)
    assert soft.deess_db == 0.0


def test_plan_breaths_attenuated_only_when_loud():
    words = [(0.4, 0.9), (1.5, 2.0), (2.6, 3.1), (3.7, 4.2)]
    loud = synth_voice(5.0, words, breaths=[(1.0, 1.4, -26.0)], level_db=-20)
    ix = make_index(words, 5.0, breaths=[(1.0, 1.4)])
    spec = V.plan_voice_chain(None, ix, audio=loud, sr=SR)
    assert 6.0 <= spec.breath_atten_db <= 12.0
    quiet = synth_voice(5.0, words, breaths=[(1.0, 1.4, -48.0)], level_db=-20)
    assert V.plan_voice_chain(None, ix, audio=quiet, sr=SR).breath_atten_db == 0.0


# ============================================================================================== stages
def test_highpass_is_12db_per_octave_butterworth():
    imp = np.zeros(SR)
    imp[0] = 1.0
    h = V.highpass(imp, SR, 100.0)
    H = 20 * np.log10(np.abs(np.fft.rfft(h)) + 1e-12)
    assert H[100] == pytest.approx(-3.01, abs=0.1)
    assert H[50] == pytest.approx(-12.3, abs=0.5)
    assert H[25] == pytest.approx(-24.1, abs=0.6)
    assert H[400] > -0.1


def test_eq_bands_apply_measured_gain():
    rng = np.random.default_rng(1)
    x = rng.standard_normal(SR * 2) * 0.05
    y = V.apply_eq(x, SR, [EqBand(type="peak", freq_hz=300, gain_db=-4.0, q=1.0)])
    f, Px = sps.welch(x, SR, nperseg=8192)
    _, Py = sps.welch(y, SR, nperseg=8192)
    g = 10 * np.log10(Py / Px)
    assert g[np.argmin(np.abs(f - 300))] == pytest.approx(-4.0, abs=0.6)
    assert abs(g[np.argmin(np.abs(f - 3000))]) < 0.3


def test_compressor_calibrates_to_target_gain_reduction():
    lv = ([-14, -20, -26, -20, -17, -23, -20, -29, -20, -15, -21, -20, -18, -24] * 2)[: len(WORDS)]
    x = synth_voice(10.0, WORDS, word_levels_db=lv)
    mask = V.speech_mask(make_index(WORDS, 10.0), x.size, SR)
    y, info = V.compress(x, SR, target_gr_db=4.0, ratio=2.5, mask=mask)
    assert info["applied"] and info["gr_p90_db"] == pytest.approx(4.0, abs=1.0)
    per_word = [db(x[round(a * SR): round(b * SR)]) - db(y[round(a * SR): round(b * SR)]) for a, b in WORDS]
    order = np.argsort([lv[i] for i in range(len(WORDS))])
    gr_sorted = [per_word[i] for i in order]
    assert gr_sorted[0] < gr_sorted[-1]  # quietest word compressed least, loudest most
    assert max(per_word) < 8.0  # never crushing
    # gaps (room tone) are not pumped by more than a fraction of a dB
    assert abs(region_db(y, gaps_of(WORDS)) - region_db(x, gaps_of(WORDS))) < 1.0


def test_deesser_reduces_sibilants_and_leaves_vowels():
    sib = (1, 4, 7, 10)
    x = voice(sibilant=sib, sib_rel_db=6.0)
    mask = V.speech_mask(make_index(WORDS, 10.0), x.size, SR)
    y, info = V.deess(x, SR, max_db=5.0, centre_hz=7000, mask=mask)
    assert info["applied"]
    band = sps.butter(4, [5500, 8500], "bandpass", fs=SR, output="sos")
    s_spans = [(WORDS[i][0] + 0.2, WORDS[i][1] - 0.03) for i in sib]
    red = region_db(sps.sosfilt(band, x), s_spans, trim=0) - region_db(sps.sosfilt(band, y), s_spans, trim=0)
    assert 2.0 <= red <= 5.5
    v_spans = [WORDS[i] for i in range(len(WORDS)) if i not in sib]
    assert abs(region_db(y, v_spans) - region_db(x, v_spans)) < 0.3
    y0, _ = V.deess(x, SR, max_db=0.0)
    assert np.array_equal(y0, x)


def test_leveler_evens_phrase_levels_within_range():
    lv = [-18.0] * 4 + [-22.0] * 4 + [-26.0] * 4
    words = WORDS[:12]
    x = synth_voice(10.0, words, word_levels_db=lv)
    mask = V.speech_mask(make_index(words, 10.0), x.size, SR)
    g, info = V.leveler(x, SR, mask)
    y = x * 10 ** (g / 20)
    phrases = [words[0:4], words[4:8], words[8:12]]
    before = [region_db(x, p) for p in phrases]
    after = [region_db(y, p) for p in phrases]
    assert max(before) - min(before) == pytest.approx(8.0, abs=0.5)
    assert max(after) - min(after) <= 3.0
    assert np.max(np.abs(g)) <= 3.0 + 1e-9 and info["applied"]


def test_breath_attenuation_keeps_the_floor_continuous():
    words = [(0.4, 0.9), (1.5, 2.0), (2.6, 3.1), (3.7, 4.2)]
    x = synth_voice(5.0, words, breaths=[(1.0, 1.4, -26.0)], level_db=-20, noise_db=-60)
    ix = make_index(words, 5.0, breaths=[(1.0, 1.4)])
    mask = V.speech_mask(ix, x.size, SR)
    tone = V.build_room_tone(x, SR, ix, 2.0)
    y, log = V.breath_attenuation(x, SR, ix, mask, max_db=12.0, tone=tone)
    assert log and log[0]["atten_db"] >= 6.0
    br = slice(round(1.15 * SR), round(1.25 * SR))
    assert db(x[br]) - db(y[br]) == pytest.approx(log[0]["atten_db"], abs=1.5)
    _c, lv = V.frame_levels_db(y[round(0.95 * SR): round(1.45 * SR)], SR, 0.02, 0.01)
    assert lv.min() > -60.0 - 3.0  # never below the floor


def test_declick_tames_knocks_but_never_consonant_bursts():
    rng = np.random.default_rng(7)
    x = voice(noise_db=-60.0)
    assert V.tame_transients(x, SR)[1] == []  # clean speech: nothing to do
    y0, _ = V.tame_transients(x, SR)
    assert np.array_equal(y0, x)
    # a "t"-like burst at vowel level right after a 60 ms silent closure, inside a word
    a, b = WORDS[5]
    closure = slice(round((a + 0.12) * SR), round((a + 0.18) * SR))
    burst = slice(closure.stop, closure.stop + round(0.015 * SR))
    xc = x.copy()
    xc[closure] = 10 ** (-60 / 20) * rng.standard_normal(closure.stop - closure.start)
    xc[burst] = sps.sosfilt(sps.butter(2, 3000, "highpass", fs=SR, output="sos"),
                            rng.standard_normal(burst.stop - burst.start)) * 10 ** (-17 / 20)
    y1, ev1 = V.tame_transients(xc, SR)
    assert ev1 == [] and np.array_equal(y1, xc)
    # a 2 ms knock ~22 dB over the phrase inside a pause
    k = round((WORDS[3][1] + 0.1) * SR)
    xk = x.copy()
    n = round(0.002 * SR)
    xk[k: k + n] += 0.9 * np.hanning(n) * np.sin(2 * np.pi * 900 * np.arange(n) / SR)
    yk, evk = V.tame_transients(xk, SR)
    assert len(evk) == 1 and evk[0]["reduction_db"] > 6.0
    assert np.max(np.abs(yk[k: k + n])) < 0.5 * np.max(np.abs(xk[k: k + n]))
    far = np.r_[0: k - SR // 10, k + SR // 10: x.size]
    assert np.array_equal(yk[far], xk[far])  # gain only, local


def test_light_denoise_is_limited():
    x = voice(noise_db=-38.0)
    clean = voice(noise_db=-200.0)
    ix = make_index(WORDS, 10.0)
    mask = V.speech_mask(ix, x.size, SR)
    y, info = V.denoise_wiener(x, SR, limit_db=12.0, noise_mask=~mask)
    red = region_db(x, gaps_of(WORDS)) - region_db(y, gaps_of(WORDS))
    assert 7.0 <= red <= 12.5  # suppression capped by the attenuation limit
    sp = [(a + 0.05, b - 0.05) for a, b in WORDS]
    assert abs(region_db(y, sp) - region_db(clean, sp)) < 1.0
    assert info["limit_db"] == 12.0 and y.shape == x.shape


# ============================================================================================== chain
def test_apply_chain_preserves_shape_and_dtype():
    x = voice().astype(np.float32)
    spec = VoiceChainSpec(deess_db=3.0, eq=[EqBand(freq_hz=300, gain_db=-2)])
    y = V.apply_voice_chain(x, SR, spec, index=make_index(WORDS, 10.0))
    assert y.shape == x.shape and y.dtype == np.float32 and np.all(np.isfinite(y))
    st = np.vstack([x, 0.8 * x])
    ys = V.apply_voice_chain(st, SR, spec)
    assert ys.shape == st.shape and ys.dtype == np.float32
    off = V.apply_voice_chain(x, SR, VoiceChainSpec(enabled=False))
    assert np.array_equal(off, x)


def test_apply_chain_logs_every_stage_in_order():
    ix = make_index(WORDS, 10.0)
    spec = VoiceChainSpec(denoise="light", deess_db=3.0, eq=[EqBand(freq_hz=300, gain_db=-2)], breath_atten_db=0.0)
    res = V.apply_voice_chain_detailed(voice(noise_db=-36), SR, spec, index=ix)
    stages = [a["stage"] for a in res.applied]
    assert stages == ["denoise_light", "highpass", "eq", "leveler", "compressor", "deess"]


# ============================================================================================== isolation
def _wav_bytes(x: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, x.astype(np.float32), sr, format="WAV", subtype="FLOAT")
    return buf.getvalue()


def test_isolation_request_format_alignment_and_residual_mix():
    import soxr

    clean = voice(noise_db=-200.0)
    noisy = clean + 10 ** (-30 / 20) * np.random.default_rng(3).standard_normal(clean.size)
    delay = 480  # the "service" returns audio 10 ms late and at 44.1 kHz
    iso = np.concatenate((np.zeros(delay), clean))[: clean.size]
    body = _wav_bytes(soxr.resample(iso, SR, 44100, quality="VHQ"), 44100)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"], seen["path"] = request.method, request.url.path
        seen["key"] = request.headers.get("xi-api-key")
        seen["ctype"] = request.headers.get("content-type", "")
        seen["body"] = request.read()
        return httpx.Response(200, content=body, headers={"content-type": "audio/wav"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    spec = VoiceChainSpec(denoise="isolate", isolation_provider="elevenlabs", compression_db=0.0, leveler=False)
    res = V.apply_voice_chain_detailed(
        noisy, SR, spec, index=make_index(WORDS, 10.0),
        isolator=lambda a, r: V.isolate_elevenlabs(a, r, api_key="test-key-123456", client=client))
    assert seen["method"] == "POST" and seen["path"] == "/v1/audio-isolation"
    assert seen["key"] == "test-key-123456" and seen["ctype"].startswith("multipart/form-data")
    assert b'name="audio"' in seen["body"] and b'name="file_format"' in seen["body"] and b"other" in seen["body"]
    iso_stage = res.applied[0]
    assert iso_stage["stage"] == "isolate" and abs(iso_stage["lag_samples"] - delay) <= 2
    assert iso_stage["coherent"] is True and iso_stage["polarity"] == 1
    gaps = gaps_of(WORDS)
    assert region_db(noisy, gaps) - region_db(res.audio, gaps) > 12.0  # noise down ~18 dB (residual mix-back)
    assert region_db(res.audio, gaps) > region_db(clean, gaps)  # but never unlimited
    assert "test-key-123456" not in repr(res.to_json())


def _dispersed(x: np.ndarray, seed: int = 0, nper: int = 128) -> np.ndarray:
    """Same short-time magnitudes (onsets kept to ~1 ms), scrambled waveform — like a generative
    isolator's output: STFT with random phases, resynthesized."""
    rng = np.random.default_rng(seed)
    _f, _t, Z = sps.stft(x, fs=SR, nperseg=nper, noverlap=nper * 3 // 4)
    Z = np.abs(Z) * np.exp(1j * rng.uniform(0, 2 * np.pi, Z.shape))
    _t2, y = sps.istft(Z, fs=SR, nperseg=nper, noverlap=nper * 3 // 4)
    return y[: x.size]


def _reverberant(x: np.ndarray, rt60: float = 0.35, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = round(rt60 * SR)
    t = np.arange(n) / SR
    rir = rng.standard_normal(n) * np.exp(-6.9 * t / rt60) * 0.08
    rir[0] = 1.0
    return sps.fftconvolve(x, rir)[: x.size]


def test_isolation_alignment_survives_a_phase_incoherent_dereverberating_isolator():
    clean = voice(noise_db=-200.0)
    orig = _reverberant(clean) + 10 ** (-40 / 20) * np.random.default_rng(4).standard_normal(clean.size)
    gen = _dispersed(clean, nper=64)
    for delay in (-137, 0, 311):
        shifted = np.roll(gen, delay)
        al = V._align_lag(orig, shifted, SR)
        assert al["coherent"] is False and al["method"] == "onset"
        assert abs(al["lag"] - delay) <= 24  # within 0.5 ms
    # a coherent output with flipped polarity is detected and undone
    dry = clean + 10 ** (-40 / 20) * np.random.default_rng(6).standard_normal(clean.size)
    al = V._align_lag(dry, -np.roll(clean, 50), SR)
    assert al["coherent"] and al["polarity"] == -1 and al["lag"] == 50


def test_isolation_timbre_is_matched_back_to_the_original():
    import pedalboard

    clean = voice(noise_db=-200.0)
    noisy = clean + 10 ** (-65 / 20) * np.random.default_rng(5).standard_normal(clean.size)
    bright = pedalboard.PeakFilter(cutoff_frequency_hz=3000, gain_db=8.0, q=0.9)(
        _dispersed(clean, nper=64)[None, :].astype(np.float32), SR)[0].astype(np.float64)
    mask = V.speech_mask(make_index(WORDS, 10.0), clean.size, SR)
    y, info = V._integrate_isolation(noisy, bright, SR, mask)
    band = sps.butter(4, [2400, 3700], "bandpass", fs=SR, output="sos")
    sp = [(a + 0.05, b - 0.05) for a, b in WORDS]

    def tilt(z: np.ndarray) -> float:  # 3 kHz band relative to the whole voice
        return region_db(sps.sosfilt(band, z), sp) - region_db(z, sp)

    before, after = tilt(bright) - tilt(clean), tilt(y) - tilt(clean)
    assert before > 5.0 and abs(after) < 0.3 * before  # ≥ 70 % of the timbre shift undone (cap ±9 dB)
    assert info["timbre_correction_db"]["3150"] < -3.0
    # where the original is noise-dominated the correction is withheld (never EQ toward noise)
    very_noisy = clean + 10 ** (-30 / 20) * np.random.default_rng(5).standard_normal(clean.size)
    _y2, info2 = V._integrate_isolation(very_noisy, bright, SR, mask)
    assert abs(info2["timbre_correction_db"]["3150"]) < 1.0 and abs(info2["timbre_correction_db"]["8000"]) < 1.0
    assert info["timbre_match_db"][0] < -3.0


def test_isolation_http_error_and_missing_key_fall_back_to_light():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "bad key"})))
    with pytest.raises(V.IsolationError, match="401"):
        V.isolate_elevenlabs(np.zeros(SR), SR, api_key="k" * 20, client=client)
    spec = VoiceChainSpec(denoise="isolate", isolation_provider="elevenlabs")
    res = V.apply_voice_chain_detailed(voice(noise_db=-30), SR, spec, index=make_index(WORDS, 10.0),
                                       settings=Settings.load(env={}))
    assert any("isolation unavailable" in w for w in res.warnings)
    assert res.applied[0]["stage"] == "denoise_light"


def test_isolation_guard_can_reject():
    clean = voice(noise_db=-200.0)
    spec = VoiceChainSpec(denoise="isolate")
    res = V.apply_voice_chain_detailed(voice(noise_db=-30), SR, spec, index=make_index(WORDS, 10.0),
                                       isolator=lambda a, r: clean,
                                       guard=lambda o, p, r: {"accept": False, "why": "wer"})
    assert res.applied[0].get("rejected") and res.applied[1]["stage"] == "denoise_light"
    assert any("rejected by guard" in w for w in res.warnings)


def test_make_wer_guard():
    texts = {"orig": "the real secret is restraint", "proc": "the real secret is restrained"}

    def fake_asr(a: np.ndarray, r: int) -> str:
        return texts["proc"] if a[0] == 1.0 else texts["orig"]

    g = V.make_wer_guard(fake_asr, "the real secret is restraint")
    o = np.zeros(10)
    p = np.zeros(10)
    p[0] = 1.0
    res = g(o, p, SR)
    assert res["accept"] is False and res["wer_processed"] > res["wer_original"]
    assert g(o, o.copy(), SR)["accept"] is True


# ============================================================================================== room tone
def test_room_tone_matches_floor_excludes_words_and_loops_cleanly():
    x = voice(noise_db=-58.0, f0=150.0)
    ix = make_index(WORDS, 10.0)
    tone = V.build_room_tone(x, SR, ix, 23.7, seed=3)
    assert tone.dtype == np.float32 and tone.size == round(23.7 * SR)
    assert db(tone) == pytest.approx(-58.0, abs=1.5)
    f, P = sps.welch(tone, SR, nperseg=8192)
    band = (f > 120) & (f < 180)
    assert 10 * np.log10(P[band].max() / np.median(P[band])) < 10  # no voiced harmonic leaked in
    _c, lv = V.frame_levels_db(tone, SR, 0.05, 0.025)
    assert lv.max() - lv.min() < 6.0  # steady, no joins or bumps
    assert np.array_equal(tone, V.build_room_tone(x, SR, ix, 23.7, seed=3))  # deterministic


def test_room_tone_without_pauses_is_silence_not_synthetic_noise():
    x = np.zeros(SR * 2)
    assert np.max(np.abs(V.build_room_tone(x, SR, None, 1.0))) == 0.0


# ============================================================================================== job level
def test_process_dialogue_runs_once_and_caches(work_dir: Path):
    ix = make_index(WORDS, 10.0)
    job = Job.create("voice-job", work_dir=work_dir)
    V.write_audio(job.audio_path, voice(), SR)
    spec = V.plan_voice_chain(job, ix)
    p1 = V.process_dialogue(job, ix, spec)
    mt = p1.stat().st_mtime_ns
    p2 = V.process_dialogue(job, ix, spec)
    assert p1 == p2 and p2.stat().st_mtime_ns == mt and p1.with_suffix(".json").exists()
    y, sr = sf.read(p1)
    assert sr == SR and y.size == 10 * SR
    p3 = V.process_dialogue(job, ix, spec.model_copy(update={"hpf_hz": 100.0}))
    assert p3 != p1


def test_apply_voice_chain_file_roundtrip(tmp_path: Path):
    x = voice()
    V.write_audio(tmp_path / "in.wav", x, SR)
    res = V.apply_voice_chain_file(tmp_path / "in.wav", VoiceChainSpec(), tmp_path / "out.wav")
    y, sr = sf.read(tmp_path / "out.wav")
    assert sr == SR and y.shape == x.shape and sf.info(str(tmp_path / "out.wav")).subtype == "FLOAT"
    assert res.applied[0]["stage"] == "highpass"


# ============================================================================================== real
@pytest.mark.real
def test_isolation_real_elevenlabs(tmp_path: Path):
    """ONE small real call: 6 s of a real talking-head take through the Voice Isolator."""
    src = Path("/Users/home/studio-testdata")
    takes = sorted(src.glob("*real-take40*")) or sorted(src.glob("*.mov")) + sorted(src.glob("*.mp4"))
    if not takes or shutil.which("ffmpeg") is None:
        pytest.skip("no test media")
    wav = tmp_path / "clip.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "5", "-t", "6", "-i", str(takes[0]), "-vn", "-ac", "1",
                    "-ar", str(SR), "-c:a", "pcm_f32le", str(wav)], check=True)
    x, _ = V.load_audio(wav, SR, mono=True)
    assert os.environ.get("STUDIO_ENV_FILE"), "set STUDIO_ENV_FILE"
    iso = V.isolate_elevenlabs(x, SR)
    assert abs(iso.size - x.size) < 0.25 * SR
    y, info = V._integrate_isolation(x, iso, SR, V.energy_vad(x, SR))
    # measured Sep 2026: the isolator is generative (not waveform-coherent); onsets align within ~1 sample
    assert info["coherent"] is False and info["align_method"] in ("onset", "envelope")
    assert abs(info["lag_samples"]) < 0.005 * SR and y.size == x.size
    assert V.noise_floor_db(y, SR) < V.noise_floor_db(x, SR) - 6.0
