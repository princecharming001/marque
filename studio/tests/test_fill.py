"""Second-pass ASR over speech the first pass dropped (studio.perception.fill).

Regression for a real Scribe re-run of real-take40 that returned "One, do the two [2.3 s] cuisines share" for audio
that says "One, do the two cuisines, [stall] do the two cuisines share": four words vanished, the gap held speech,
and a pause target trimmed it to "cui- … -two".
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from gaps_prosody_synth import SR, build_take

from studio.jobs import Job
from studio.perception import fill as F
from studio.perception import gaps as G
from studio.perception.index import AsrInfo, AsrResult, Word, word_id


def _take() -> tuple[np.ndarray, list[Word]]:
    ev = [
        {"type": "word", "text": "One,", "t": 0.40, "dur": 0.35, "f0": 130, "db": -20},
        {"type": "word", "text": "do", "t": 1.00, "dur": 0.20, "f0": 125, "db": -20},
        {"type": "word", "text": "the", "t": 1.22, "dur": 0.15, "f0": 125, "db": -20},
        {"type": "word", "text": "two", "t": 1.40, "dur": 0.25, "f0": 122, "db": -20},
        {"type": "word", "text": "cuisines,", "t": 1.70, "dur": 0.45, "f0": 120, "db": -20},  # dropped by ASR
        {"type": "word", "text": "do", "t": 3.40, "dur": 0.15, "f0": 125, "db": -20},  # dropped
        {"type": "word", "text": "the", "t": 3.58, "dur": 0.14, "f0": 125, "db": -20},  # dropped
        {"type": "word", "text": "two", "t": 3.75, "dur": 0.22, "f0": 122, "db": -20},  # dropped
        {"type": "word", "text": "cuisines", "t": 4.05, "dur": 0.45, "f0": 120, "db": -20},
        {"type": "word", "text": "share.", "t": 4.55, "dur": 0.35, "f0": 118, "db": -21},
    ]
    take = build_take(ev, total_s=5.5, floor_db=-62.0)
    kept = [0, 1, 2, 3, 8, 9]
    words = [Word(id=word_id(k + 1), text=take.words[i].text, start_us=round(take.words[i].start_s * 1e6),
                  end_us=round(take.words[i].end_s * 1e6)) for k, i in enumerate(kept)]
    return take.x, words


@pytest.fixture
def fill_job(work_dir: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Job, list[Word]]:
    x, words = _take()
    job = Job.create("fill", work_dir=work_dir)
    sf.write(str(job.audio_path), x.astype(np.float32), SR, subtype="FLOAT")
    # Silero does not call synthetic harmonic "words" speech: supply its probability from the ground truth
    n = int(np.ceil(x.size / (SR * G.HOP_S))) + 1
    t = np.arange(n) * G.HOP_S
    truth = [(0.40, 0.75), (1.00, 1.65), (1.70, 2.15), (3.40, 3.97), (4.05, 4.90)]
    vad = np.zeros(n)
    for a, b in truth:
        vad[(t >= a) & (t <= b)] = 0.95
    feat = G.compute_features(x, SR, vad=vad)
    monkeypatch.setattr(G, "load_features", lambda _p: feat)
    return job, words


def test_speech_without_words_is_found_in_its_gap(fill_job) -> None:
    job, words = fill_job
    refined = G.refine_word_boundaries(job, words)
    gaps = G.detect_gaps(job, refined)
    regions = F.untranscribed_speech(job, refined, gaps)
    g = next(g for g in gaps if g.after_word_id == "w0004")
    mine = [r for r in regions if r.gap_id == g.id]
    assert mine, regions
    covered = sum(r.end_us - r.start_us for r in mine)
    assert covered >= 700_000 and all(g.start_us <= r.start_us < r.end_us <= g.end_us for r in mine)
    assert all(r.level_below_speech_db < 6 for r in mine)
    # ordinary pauses hold no untranscribed speech
    assert not [r for r in regions if r.gap_id != g.id]


def test_fill_merges_second_pass_words_and_marks_what_stays_silent(fill_job, monkeypatch) -> None:
    job, words = fill_job
    refined = G.refine_word_boundaries(job, words)
    gaps = G.detect_gaps(job, refined)
    calls: list[float] = []

    def fake_transcribe_file(path, **kw):
        info = sf.info(str(path))
        dur = info.frames / info.samplerate
        calls.append(dur)
        # the excerpt starts LEAD_SILENCE_S before its context; answer with the dropped words in excerpt time
        # (the context word "two" rides along and must be dropped as a duplicate)
        return AsrResult(words=[], asr=AsrInfo(provider="fake", model="fake"))

    import studio.perception.transcribe as T

    monkeypatch.setattr(T, "transcribe_file", fake_transcribe_file)
    extra, report = F.fill_untranscribed(job, refined, gaps)
    assert extra == [] and report and calls  # the ASR heard nothing: no words invented
    left = F.untranscribed_speech(job, refined, gaps)
    marked = F.mark_untranscribed(gaps, left)
    g = next(g for g in marked if g.after_word_id == "w0004")
    assert g.kind == "noise" and g.sound_us and not any(a <= g.snap_us <= b for a, b in g.sound_us)

    truth = [("two", 1.40, 1.65), ("cuisines,", 1.70, 2.15), ("do", 3.40, 3.55), ("the", 3.58, 3.72),
             ("two", 3.75, 3.97), ("uh", 3.98, 4.0), ("cuisines", 4.05, 4.50)]
    state: dict[str, float] = {}
    real_excerpt = F._excerpt

    def excerpt(job_, a_us, b_us, out):
        off = real_excerpt(job_, a_us, b_us, out)
        state.update(off=off, a=a_us / 1e6, b=b_us / 1e6)
        return off

    def answering(path, **kw):
        # what a good ASR hears in the excerpt (its context words included), on the excerpt's clock
        off = state["off"]
        out = [Word(id=word_id(k + 1), text=t, start_us=round((a - off) * 1e6), end_us=round((b - off) * 1e6),
                    confidence=0.2 if t == "uh" else 0.98)
               for k, (t, a, b) in enumerate(truth) if a >= state["a"] - 0.01 and b <= state["b"] + 0.01]
        return AsrResult(words=out, asr=AsrInfo(provider="fake", model="fake"))

    monkeypatch.setattr(F, "_excerpt", excerpt)
    monkeypatch.setattr(T, "transcribe_file", answering)
    extra, report = F.fill_untranscribed(job, refined, gaps)
    texts = [w.text for w in extra]
    assert texts[:4] == ["cuisines,", "do", "the", "two"] and "uh" not in texts  # low confidence rejected
    assert all(1_650_000 <= w.start_us < w.end_us <= 4_050_000 for w in extra)


def test_quiet_sound_is_never_sent_to_asr(fill_job, monkeypatch) -> None:
    job, words = fill_job
    refined = G.refine_word_boundaries(job, words)
    gaps = G.detect_gaps(job, refined)
    regions = F.untranscribed_speech(job, refined, gaps)
    quiet = [F.SpeechRegion(r.gap_id, r.start_us, r.end_us, r.speech_ms, level_below_speech_db=20.0)
             for r in regions]
    monkeypatch.setattr(F, "untranscribed_speech", lambda *a, **k: quiet)
    import studio.perception.transcribe as T

    def boom(*a, **k):
        raise AssertionError("must not be called")

    monkeypatch.setattr(T, "transcribe_file", boom)
    extra, report = F.fill_untranscribed(job, refined, gaps)
    assert extra == [] and all(r.get("skipped") for r in report)


def test_unvoiced_sound_is_never_sent_to_asr(fill_job, monkeypatch) -> None:
    # real-take40's lead-in: a 36 %-voiced handling noise came back from an isolated Scribe pass as "Basically"
    job, words = fill_job
    refined = G.refine_word_boundaries(job, words)
    gaps = G.detect_gaps(job, refined)
    regions = F.untranscribed_speech(job, refined, gaps)
    assert regions and all(r.voiced >= F.MIN_VOICED for r in regions)  # the dropped words are voiced
    noisy = [F.SpeechRegion(r.gap_id, r.start_us, r.end_us, r.speech_ms, voiced=0.36) for r in regions]
    monkeypatch.setattr(F, "untranscribed_speech", lambda *a, **k: noisy)
    import studio.perception.transcribe as T

    monkeypatch.setattr(T, "transcribe_file", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ASR")))
    extra, report = F.fill_untranscribed(job, refined, gaps)
    assert extra == [] and all("not voiced" in r.get("skipped", "") for r in report)
