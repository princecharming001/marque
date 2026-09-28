"""Synthetic speech-like signals with known ground truth for the gaps / prosody / audio-metrics tests.

"Words" are harmonic complexes (sawtooth-like spectrum, gentle formant shaping, syllable-rate AM) with a
known f0, level and duration, so Praat tracks their pitch and the exact acoustic edges are known.
Everything is deterministic (seeded) and offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, sosfilt

SR = 48_000


def db_to_amp(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def rms_db(x: np.ndarray) -> float:
    return float(10 * np.log10(np.mean(np.asarray(x, dtype=np.float64) ** 2) + 1e-20))


def _ramp(n: int, attack: int, release: int) -> np.ndarray:
    env = np.ones(n)
    a = min(attack, n // 2)
    r = min(release, n // 2)
    if a > 0:
        env[:a] = 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)
    if r > 0:
        env[n - r:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(r) / r)
    return env


def harmonic_word(dur_s: float, f0: float, level_db: float, *, sr: int = SR, glide_st: float = 0.0,
                  am_hz: float = 4.0, am_depth: float = 0.25, attack_s: float = 0.012, release_s: float = 0.02,
                  seed: int = 0) -> np.ndarray:
    """Voiced 'word': harmonics of ``f0`` (optionally gliding by ``glide_st`` semitones) at RMS ``level_db``."""
    n = round(dur_s * sr)
    t = np.arange(n) / sr
    f_inst = f0 * 2.0 ** (glide_st * (t / max(dur_s, 1e-9) - 0.5) / 12.0)
    phase = 2 * np.pi * np.cumsum(f_inst) / sr
    rng = np.random.default_rng(seed)
    y = np.zeros(n)
    kmax = int(min(40, (0.45 * sr) // (f0 * 2.0 ** (abs(glide_st) / 12))))
    for k in range(1, kmax + 1):
        fk = k * f0
        # soft formant emphasis around 700 Hz and 1.2 kHz, −6 dB/oct overall
        form = 1.0 + 1.5 * np.exp(-((fk - 700) / 250) ** 2) + 1.0 * np.exp(-((fk - 1200) / 300) ** 2)
        y += (form / k) * np.sin(k * phase + rng.uniform(0, 2 * np.pi))
    y *= 1.0 - am_depth + am_depth * np.cos(2 * np.pi * am_hz * t) ** 2
    y *= _ramp(n, int(attack_s * sr), int(release_s * sr))
    body = y[int(0.1 * n):int(0.9 * n)] if n > 20 else y
    y *= db_to_amp(level_db) / (np.sqrt(np.mean(body ** 2)) + 1e-12)
    return y


def band_noise(dur_s: float, lo: float, hi: float, level_db: float, *, sr: int = SR, seed: int = 1,
               shape: str = "hann") -> np.ndarray:
    """Band-limited Gaussian noise with a Hann (bump) or flat envelope; ``level_db`` = RMS of the peak
    region (flat envelope) or of the whole burst scaled so its middle third reaches ``level_db``."""
    n = round(dur_s * sr)
    rng = np.random.default_rng(seed)
    w = rng.standard_normal(n + 4096)
    sos = butter(4, [lo, min(hi, 0.45 * sr)], btype="bandpass", fs=sr, output="sos")
    y = sosfilt(sos, w)[4096:]
    if shape == "hann":
        env = np.hanning(n)
    else:
        env = _ramp(n, int(0.01 * sr), int(0.01 * sr))
    y = y / (np.sqrt(np.mean(y ** 2)) + 1e-12)
    y *= env
    mid = y[n // 3: 2 * n // 3] if n > 3 else y
    return y * db_to_amp(level_db) / (np.sqrt(np.mean(mid ** 2)) + 1e-12)


def white_floor(dur_s: float, level_db: float, *, sr: int = SR, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(round(dur_s * sr)) * db_to_amp(level_db)


@dataclass
class TrueWord:
    text: str
    start_s: float
    end_s: float  # acoustic end including any attached fricative tail
    f0: float | None
    level_db: float
    kind: str = "word"


@dataclass
class Take:
    x: np.ndarray
    sr: int
    words: list[TrueWord] = field(default_factory=list)
    breaths: list[tuple[float, float]] = field(default_factory=list)
    noises: list[tuple[float, float]] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return len(self.x) / self.sr


def add_at(x: np.ndarray, y: np.ndarray, start_s: float, sr: int = SR) -> None:
    a = round(start_s * sr)
    b = min(len(x), a + len(y))
    x[a:b] += y[: b - a]


def build_take(events: list[dict], *, total_s: float, floor_db: float = -60.0, sr: int = SR, seed: int = 3) -> Take:
    """Mix events onto a white-noise floor. Event dicts:

    ``{"type": "word", "text", "t", "dur", "f0", "db", ["tail": (dur, db)], ["glide"]}``
    ``{"type": "breath", "t", "dur", "db"}``, ``{"type": "beep", "t", "dur", "db", "hz"}``,
    ``{"type": "unvoiced_word", "text", "t", "dur", "db"}`` (whispered: band noise, no f0).
    """
    x = white_floor(total_s, floor_db, sr=sr, seed=seed) if floor_db > -200 else np.zeros(int(total_s * sr))
    take = Take(x=x, sr=sr)
    for i, ev in enumerate(events):
        typ = ev["type"]
        if typ == "word":
            y = harmonic_word(ev["dur"], ev["f0"], ev["db"], sr=sr, glide_st=ev.get("glide", 0.0), seed=100 + i,
                              am_depth=ev.get("am", 0.25))
            add_at(x, y, ev["t"], sr)
            end = ev["t"] + ev["dur"]
            if "tail" in ev:
                tdur, tdb = ev["tail"]
                tail = band_noise(tdur + 0.02, 4000, 9000, tdb, sr=sr, seed=200 + i, shape="flat")
                add_at(x, tail, end - 0.02, sr)
                end = end + tdur
            take.words.append(TrueWord(ev.get("text", f"w{i}"), ev["t"], end, ev["f0"], ev["db"]))
        elif typ == "unvoiced_word":
            y = band_noise(ev["dur"], 500, 6000, ev["db"], sr=sr, seed=300 + i, shape="flat")
            add_at(x, y, ev["t"], sr)
            take.words.append(TrueWord(ev.get("text", f"w{i}"), ev["t"], ev["t"] + ev["dur"], None, ev["db"]))
        elif typ == "breath":
            y = band_noise(ev["dur"], 300, 5000, ev["db"], sr=sr, seed=400 + i, shape="hann")
            add_at(x, y, ev["t"], sr)
            take.breaths.append((ev["t"], ev["t"] + ev["dur"]))
        elif typ == "beep":
            n = round(ev["dur"] * sr)
            t = np.arange(n) / sr
            y = np.sin(2 * np.pi * ev.get("hz", 1000.0) * t) * db_to_amp(ev["db"] + 3.01) * _ramp(n, 240, 240)
            add_at(x, y, ev["t"], sr)
            take.noises.append((ev["t"], ev["t"] + ev["dur"]))
        else:  # pragma: no cover
            raise ValueError(typ)
    return take


def asr_words(take: Take, *, jitter_s: list[tuple[float, float]] | None = None):
    """Index ``Word`` objects from the truth, with optional (start, end) ASR errors per word."""
    from studio.perception.index import Word, word_id

    out = []
    for i, w in enumerate(take.words):
        ds, de = (jitter_s[i] if jitter_s else (0.0, 0.0))
        s = max(0.0, w.start_s + ds)
        e = max(s, w.end_s + de)
        out.append(Word(id=word_id(i + 1), text=w.text, start_us=round(s * 1e6), end_us=round(e * 1e6),
                        kind=w.kind))  # type: ignore[arg-type]
    return out


def standard_take() -> Take:
    """8 s take used by several tests (times in seconds):

    * 0.00-0.40 leading room tone
    * w1 0.40-0.80, w2 0.80-1.16 (abut, 0 gap), w3 1.40-1.90 with a 90 ms sibilant tail → 1.99
    * gap 1.99-2.65 (pause 660 ms; quiet)
    * w4 2.65-3.10
    * gap 3.10-3.95 with a breath 3.55-3.90 (pre-onset inhale, 350 ms) → has_breath
    * w5 3.95-4.40
    * gap 4.40-4.75: a 300 ms breath 4.43-4.73 fills it → kind "breath"
    * w6 4.75-5.20
    * gap 5.20-5.90 with a 1 kHz beep 5.35-5.75 → kind "noise"
    * w7 5.90-6.30 with an 80 ms sibilant tail → 6.38
    * trailing 6.38-8.00 (silence)

    Sibilant tails sit ~7 dB below the vowels (as real /s/ does), so breaths (−40 dB) are well below the
    speaker's sibilant level above 3 kHz, as in real recordings.
    """
    ev = [
        {"type": "word", "text": "Most", "t": 0.40, "dur": 0.40, "f0": 130, "db": -20},
        {"type": "word", "text": "people", "t": 0.80, "dur": 0.36, "f0": 125, "db": -21},
        {"type": "word", "text": "cuts", "t": 1.40, "dur": 0.50, "f0": 120, "db": -20, "tail": (0.09, -27)},
        {"type": "word", "text": "the", "t": 2.65, "dur": 0.45, "f0": 135, "db": -19},
        {"type": "breath", "t": 3.55, "dur": 0.35, "db": -40},
        {"type": "word", "text": "real", "t": 3.95, "dur": 0.45, "f0": 128, "db": -20},
        {"type": "breath", "t": 4.43, "dur": 0.30, "db": -40},
        {"type": "word", "text": "secret", "t": 4.75, "dur": 0.45, "f0": 122, "db": -20},
        {"type": "beep", "t": 5.35, "dur": 0.40, "db": -32, "hz": 1000.0},
        {"type": "word", "text": "restraint.", "t": 5.90, "dur": 0.40, "f0": 118, "db": -21, "tail": (0.08, -28)},
    ]
    return build_take(ev, total_s=8.0, floor_db=-62.0)
