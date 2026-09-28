"""Tests for studio.perception.prosody: per-word f0/intensity/duration z-scores, emphasis, energy.

Synthetic "words" are harmonic complexes with known f0, level and duration, so the emphasised word
and the energy ordering are known. Slow tests use the real QA media when present.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from gaps_prosody_synth import SR, asr_words, build_take

from studio.jobs import Job
from studio.perception import gaps as G
from studio.perception import prosody as P
from studio.perception.index import Energy, Sentence, Word, word_id

HERE = Path(__file__).parent
TESTDATA = Path("/Users/home/studio-testdata")

TEXTS = ["We", "build", "tools", "for", "people", "who", "really", "care", "about", "craft"]


def _sentence_take(emph_index: int = 6, *, emph_f0: float = 150.0, emph_db: float = -16.0, emph_dur: float = 0.42,
                   gap: float = 0.15, texts: list[str] | None = None):
    texts = texts or TEXTS
    ev, t = [], 0.3
    for i, tx in enumerate(texts):
        emph = i == emph_index
        dur = emph_dur if emph else 0.30
        ev.append({"type": "word", "text": tx, "t": t, "dur": dur,
                   "f0": emph_f0 if emph else 120 + 3 * ((i % 3) - 1), "db": emph_db if emph else -22})
        t += dur + gap
    return build_take(ev, total_s=t + 0.4, floor_db=-65)


@pytest.fixture(scope="module")
def emph_result():
    tk = _sentence_take()
    f = G.compute_features(tk.x, SR)
    words, energy = P.analyze_prosody(f, asr_words(tk), [])
    return tk, words, energy


def test_emphasised_word_stands_out(emph_result):
    _, words, _ = emph_result
    really = words[6]
    assert really.text == "really"
    assert really.emphasis >= 0.8
    assert max(w.emphasis for w in words if w.id != really.id) <= 0.3
    assert really.prosody is not None
    assert really.prosody.f0_z is not None and really.prosody.f0_z >= 2.0
    assert really.prosody.int_z is not None and really.prosody.int_z >= 2.0
    assert really.prosody.dur_z is not None and really.prosody.dur_z >= 0.8
    # everything is bounded and the order / ids are untouched
    assert [w.id for w in words] == [f"w{i:04d}" for i in range(1, 11)]
    for w in words:
        assert 0.0 <= w.emphasis <= 1.0
        p = w.prosody
        assert p is not None
        for v in (p.f0_z, p.int_z, p.dur_z):
            assert v is None or -5.0 <= v <= 5.0


def test_function_word_prior_and_pitch_only_emphasis():
    # same acoustic push on the function word "for" as on a content word: the prior keeps it lower
    tk_f = _sentence_take(3, emph_f0=145, emph_db=-17)
    tk_c = _sentence_take(2, emph_f0=145, emph_db=-17)
    wf = P.analyze_prosody(G.compute_features(tk_f.x, SR), asr_words(tk_f), [])[0]
    wc = P.analyze_prosody(G.compute_features(tk_c.x, SR), asr_words(tk_c), [])[0]
    assert wf[3].text == "for" and wc[2].text == "tools"
    assert wf[3].emphasis < wc[2].emphasis
    assert wf[3].emphasis == pytest.approx(wc[2].emphasis * P.FUNCTION_PRIOR, abs=0.08)


def test_final_lengthening_is_not_emphasis():
    """A 40 % longer word before a long pause (pre-boundary lengthening) scores lower than the same
    lengthening mid-phrase."""
    def run(final: bool):
        ev, t = [], 0.3
        for i, tx in enumerate(TEXTS):
            long_ = i == 4
            dur = 0.42 if long_ else 0.30
            ev.append({"type": "word", "text": tx, "t": t, "dur": dur, "f0": 120 + 3 * ((i % 3) - 1), "db": -22})
            t += dur + (0.6 if (final and long_) else 0.12)
        tk = build_take(ev, total_s=t + 0.3, floor_db=-65)
        return P.analyze_prosody(G.compute_features(tk.x, SR), asr_words(tk), [])[0][4]
    mid, fin = run(False), run(True)
    assert mid.prosody.dur_z == pytest.approx(fin.prosody.dur_z, abs=0.3)  # same measured lengthening
    assert fin.emphasis < mid.emphasis


def test_unvoiced_word_has_no_f0():
    ev = [{"type": "word", "text": w, "t": 0.3 + 0.45 * i, "dur": 0.3, "f0": 120, "db": -22}
          for i, w in enumerate(TEXTS[:6])]
    ev.insert(3, {"type": "unvoiced_word", "text": "shh", "t": 0.3 + 0.45 * 6, "dur": 0.3, "db": -28})
    tk = build_take(ev, total_s=4.0, floor_db=-65)
    words = asr_words(tk)
    words = sorted(words, key=lambda w: w.start_us)
    words = [w.model_copy(update={"id": word_id(i + 1)}) for i, w in enumerate(words)]
    out, _ = P.analyze_prosody(G.compute_features(tk.x, SR), words, [])
    shh = next(w for w in out if w.text == "shh")
    assert shh.prosody is not None and shh.prosody.f0_z is None and shh.prosody.int_z is not None


def test_fillers_events_and_sentences(emph_result):
    tk, _, _ = emph_result
    words = asr_words(tk)
    words[0] = words[0].model_copy(update={"kind": "filler", "text": "Um,"})
    words[9] = words[9].model_copy(update={"kind": "event", "text": "(laughs)"})
    sents = [Sentence(id="s001", word_ids=[w.id for w in words[:5]], text="a", start_us=words[0].start_us,
                      end_us=words[4].end_us),
             Sentence(id="s002", word_ids=[w.id for w in words[5:]], text="b", start_us=words[5].start_us,
                      end_us=words[9].end_us)]
    words = [w.model_copy(update={"sentence_id": "s001" if i < 5 else "s002"}) for i, w in enumerate(words)]
    out, energy = P.analyze_prosody(G.compute_features(tk.x, SR), words, sents)
    assert out[0].emphasis == 0.0 and out[0].prosody is not None  # filler: measured, never emphatic
    assert out[9].prosody is None and out[9].emphasis == 0.0  # event
    assert out[6].emphasis >= 0.8
    assert energy.wpm > 0


def test_energy_orders_lively_over_monotone():
    def seq(lively: bool, seed: int):
        rng = np.random.default_rng(seed)
        ev, t = [], 0.3
        for _ in range(24):
            dur = 0.22 if lively else 0.32
            ev.append({"type": "word", "text": "word", "t": t, "dur": dur,
                       "f0": float(rng.uniform(100, 210)) if lively else 118.0 + float(rng.uniform(-2, 2)),
                       "db": float(rng.uniform(-28, -14)) if lively else -21 + float(rng.uniform(-0.5, 0.5)),
                       "glide": float(rng.uniform(-4, 4)) if lively else 0.0})
            t += dur + (0.06 if lively else 0.25)
        tk = build_take(ev, total_s=t + 0.3, floor_db=-65, seed=seed)
        return P.analyze_prosody(G.compute_features(tk.x, SR), asr_words(tk), [])[1]
    lively, calm = seq(True, 1), seq(False, 2)
    assert lively.f0_var > 3.5 and calm.f0_var < 1.0
    assert lively.loudness_var > 3.5 and calm.loudness_var < 1.5
    assert lively.wpm > 190 and calm.wpm < 120
    assert lively.overall > 0.8 and calm.overall < 0.15


def test_wpm_counts_lexical_words_over_speaking_time():
    w = [Word(id=word_id(i + 1), text="x", start_us=i * 400_000, end_us=i * 400_000 + 300_000) for i in range(10)]
    # 10 words spanning 0-3.9 s, then a 2 s stall, then 5 more words
    w += [Word(id=word_id(11 + i), text="y", start_us=5_900_000 + i * 400_000, end_us=6_200_000 + i * 400_000)
          for i in range(5)]
    w.append(Word(id=word_id(16), text="um", kind="filler", start_us=7_900_000, end_us=8_100_000))
    assert P._speaking_time_us(w) == 3_900_000 + (8_100_000 - 5_900_000)
    _, energy = P.analyze_prosody(Path("/nonexistent/audio.wav"), w, [])  # no audio: rate only
    assert energy.wpm == pytest.approx(15 / 6.1 * 60, abs=0.2)
    assert energy.f0_var == 0.0 and 0.0 <= energy.overall <= 0.4


@pytest.mark.parametrize(("text", "n"), [("the", 1), ("people", 2), ("really", 2), ("restraint.", 2),
                                         ("beautiful", 3), ("jumped", 1), ("Yunicorn", 3), ("editing", 3),
                                         ("2024", 6), ("", 1), ("東京", 2)])
def test_count_syllables(text: str, n: int):
    assert P.count_syllables(text) == n


def test_duration_is_normalised_by_syllables():
    """A 4-syllable word is long because it has four syllables; a monosyllable of the same length is
    genuinely drawn out."""
    def run(text: str):
        texts = ["We", "build", "tools", text, "for", "people", "who", "care", "about", "craft"]
        ev, t = [], 0.3
        for tx in texts:
            dur = 0.62 if tx == text else (0.40 if P.count_syllables(tx) == 2 else 0.28)
            ev.append({"type": "word", "text": tx, "t": t, "dur": dur, "f0": 120, "db": -22})
            t += dur + 0.12
        tk = build_take(ev, total_s=t + 0.3, floor_db=-65)
        return P.analyze_prosody(G.compute_features(tk.x, SR), asr_words(tk), [])[0][3]
    poly, mono = run("wonderfully"), run("grand")
    assert poly.prosody.dur_z is not None and mono.prosody.dur_z is not None
    assert poly.prosody.dur_z < 1.5 <= mono.prosody.dur_z - 1.5
    assert poly.emphasis < mono.emphasis


def test_job_api(work_dir, emph_result):
    tk, expected, _ = emph_result
    job = Job.create("prosjob", work_dir=work_dir)
    sf.write(job.audio_path, tk.x.astype(np.float32), SR, subtype="FLOAT")
    out, energy = P.analyze_prosody(job, asr_words(tk), [])
    assert [w.emphasis for w in out] == [w.emphasis for w in expected]
    assert energy.overall >= 0.0
    assert P.analyze_prosody(job, [], []) == ([], Energy())


# ---------------------------------------------------------------------------------------------- real media (slow)
def _extract(src: Path, dst: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or not src.exists():
        pytest.skip(f"ffmpeg or {src.name} missing")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-map", "0:a:0", "-ac", "1",
                    "-ar", "48000", "-c:a", "pcm_f32le", str(dst)], check=True)
    return dst


@pytest.mark.slow
def test_real_take40_prosody(tmp_path):
    data = json.loads((HERE / "gaps_prosody_take40_words.json").read_text())
    wav = _extract(Path(data["media"]), tmp_path / "take40.wav")
    feat = G.load_features(wav)
    words = [Word(id=word_id(i + 1), text=w["text"], start_us=w["start_ms"] * 1000, end_us=w["end_ms"] * 1000)
             for i, w in enumerate(data["words"])]
    words = G.refine_word_boundaries(feat, words)
    out, energy = P.analyze_prosody(feat, words, [])
    # a fast, lively listicle delivery: ~213 wpm, varied pitch and loudness
    assert 195 <= energy.wpm <= 230
    assert 1.5 <= energy.f0_var <= 5.0
    assert 3.0 <= energy.loudness_var <= 8.0
    assert 0.45 <= energy.overall <= 0.9
    emph = np.array([w.emphasis for w in out])
    assert np.median(emph) < 0.25 and (emph >= 0.5).sum() >= 5 and (emph >= 0.5).mean() < 0.2
    assert sum(w.prosody is not None and w.prosody.f0_z is not None for w in out) >= 0.8 * len(out)
    assert 85 <= feat.pitch.floor_hz <= 100  # speaker range adapted (male voice ~120-140 Hz)


@pytest.mark.slow
def test_real_var_silences_energy(tmp_path):
    wav = _extract(TESTDATA / "qa-editor-var-silences.mov", tmp_path / "silences.wav")
    feat = G.load_features(wav)
    import soxr
    import torch
    from silero_vad import get_speech_timestamps

    y = soxr.resample(feat.x, feat.sr, 16000).astype(np.float32)
    ts = get_speech_timestamps(torch.from_numpy(y), G._silero_model(), sampling_rate=16000,
                               min_silence_duration_ms=120, speech_pad_ms=0)
    words = [Word(id=word_id(i + 1), text="word", start_us=int(t["start"] * 62.5), end_us=int(t["end"] * 62.5))
             for i, t in enumerate(ts)]
    out, energy = P.analyze_prosody(feat, words, [])
    assert len(out) == len(words)
    assert 0 < energy.wpm < 260  # one pseudo-word per speech run: far below the true rate, never above it
    assert 0.0 <= energy.overall <= 1.0 and energy.f0_var > 0.5 and energy.loudness_var > 0.5
    assert all(0.0 <= w.emphasis <= 1.0 for w in out)
