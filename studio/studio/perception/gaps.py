"""Acoustic word-boundary refinement, gap measurement, breaths and room tone (Take Index layer L2).

Word edges and cut points come from the measured signal, never from a model (ARCHITECTURE §0/§4).
ASR word times (Scribe drifts 50-100 ms) are *voters*; this module moves every edge to the acoustic
onset/offset within ±80 ms and measures every silence between words.

Features (:func:`load_features`, cached per file so refine/gaps/prosody/metrics share one analysis)
----------------------------------------------------------------------------------------------------
One 10 ms grid; 20 ms Hann STFT frames (a 10 ms hop with 20 ms Hann windows weighs every sample
equally, and 10 ms windows are too short for stable spectral shape):

* ``db`` full band (≥50 Hz) — used only to *report* levels (gap energy, noise floor, SNR);
* ``vb_db`` voice band 250 Hz-12 kHz — the activity/snap measure. Real phone rooms are dominated by
  50-300 Hz rumble (the QA take reads -47 dB full band but -60 dB at 300 Hz-3 kHz), which would
  swamp speech tails in a full-band measure; vowels, formants and sibilants all live in this band;
* ``mid_db`` 1-4 kHz and ``hf_db`` 3-12 kHz — further activity votes (formant tails; weak fricatives
  /f θ s/, plosive releases and quiet inhales that only show above 3 kHz in a rumbly room);
* three speech bands (breath "broadband" test), spectral flatness 300 Hz-8 kHz and spectral tilt
  P(4-10 kHz) − P(0.5-4 kHz) (breaths are noise-like and not sibilant-tilted);
* Silero VAD v6 speech probability (16 kHz via soxr VHQ, 32 ms chunks, interpolated onto the grid);
* Praat two-pass pitch (pass 1 60-700 Hz, pass 2 speaker range ``0.75·q1 .. 2·q3``, Hirst 2011 with a
  ceiling widened for expressive creators; "very accurate" AC) — voicing here, f0 in prosody.

Levels are dBFS of mean-square power (a full-scale sine reads -3 dB); digital silence reads -120.

Thresholds come from the room itself: frames the VAD calls non-speech give the noise median and a
robust spread σ (MAD) of the 30 ms-smoothed band level; a minimum-statistics tracker (3 s window,
smoothed, offset to the median) follows the noise if it changes during the take (never below the
global median, at most 12 dB above it). A frame is *active* when the smoothed voice band exceeds
``local noise + clamp(3.5σ, 6, 15) dB`` (never below ``speech − 50 dB``), or the mid or HF band does the
same against its own noise (bands are used only where speech stands ≥12 dB above their noise).

Word boundaries (:func:`refine_word_boundaries`)
------------------------------------------------
Per ASR gap the offset scan walks forward from ``end − 80 ms`` bridging internal silences shorter than
70 ms (a stop closure before its release burst stays inside the word); the onset scan walks backward
from ``start + 80 ms``. Breath runs count as inactive, so an inhale is never absorbed into a word.
Overlapping results are split at the longest silent run between them, else at the energy valley.
Frame decisions are then refined on a 1 ms envelope (6 ms window) of the same bands. Nothing moves
where the local speech-to-noise contrast is below 10 dB (music or noise louder than the voice) or
where ASR words overlap by more than 50 ms (an audio event over speech). An edge that finds no silence
within its window stays at the window limit (the widest, never-clipping choice); two words joined by
continuous sound split at the deepest energy dip within ±40 ms of their ASR junction. Refinement is
meant to run once on ASR times; on its own output it is stable wherever the true edge lies inside the
window.

Breaths
-------
"Short broadband energy bumps": 120 ms-1 s runs of mostly unvoiced, non-tonal (flatness ≥0.03), not
sibilant-tilted frames that rise ≥ max(9 dB, 4σ) above the local noise in their best band (a rumbly
room can mask everything below 3 kHz, where a quiet inhale still stands 15-20 dB clear), stay ≥8 dB
below typical speech in the voice band and ≥6 dB below the speaker's own sibilant level above 3 kHz
(p95 of speech frames, so a word-initial /ʃ/ is not a breath), and reach the 1-3 kHz or 3-8 kHz band.
A breath must *rise* out of quiet (≥3 dB from its first frame to its peak, or quiet just before it)
and may not be attached to voiced or sibilant-level frames (20 ms): the decaying tail of a word-final
fricative is never taken for a breath, so it can never be cut off a word. VAD is deliberately not
used: Silero's probability lags 150-300 ms behind every word offset, exactly where post-word inhales
sit.

Gaps (:func:`detect_gaps`)
--------------------------
Every inter-word interval ≥20 ms (stop closures count: they are clean cut points) becomes a
:class:`~studio.perception.index.Gap` spanning exactly the refined word edges; gaps under 80 ms must
contain a real dip. ``snap_us`` is the minimum of the 30 ms voice-band envelope, searched after any
post-word breath and before any pre-onset breath (the inhale stays with the words it precedes) and
away from noise events; among near-ties (within 3 dB) the middle of the longest quiet run wins so a
10-30 ms crossfade sits in room tone; then refined to ~1 ms. ``kind``: ``noise`` (non-breath sound,
or speech-like VAD without words, covers ≥30 % of the gap) > ``breath`` (breath covers ≥40 %) >
``silence`` (leading/trailing gap, dead air ≥1.5 s, or digital silence) > ``pause``; a gap whose
interior is active throughout (an untranscribed filler) is ``noise``. ``has_breath`` marks any breath
in the gap. ``energy_db`` is the median full-band frame level of the gap interior (its typical level;
-120 when it is digital silence).

Room tone (:func:`room_tone_ranges`)
------------------------------------
Stationary stretches ≥300 ms inside gaps (≥40 ms from word edges: decay tails), within
``max(3 dB, 2σ)`` of the local noise, with no breath, noise event or VAD speech nearby, never digital
silence; sorted by time so the audio stage can loop room tone from the stretch nearest each seam.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import threading
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover
    from studio.jobs import Job
    from studio.perception.index import Gap, Word

__all__ = [
    "AudioFeatures",
    "BandStats",
    "PitchTrack",
    "Breath",
    "GapAnalysis",
    "load_audio",
    "load_features",
    "compute_features",
    "clear_feature_cache",
    "pitch_track",
    "vad_probabilities",
    "refine_word_boundaries",
    "detect_gaps",
    "build_gaps",
    "analyze_gaps",
    "find_breaths",
    "room_tone_ranges",
    "HOP_S",
    "MAX_SHIFT_US",
    "MIN_GAP_US",
]

# ---------------------------------------------------------------------------------------------- constants
HOP_S = 0.010  # analysis grid (10 ms)
VAD_SR = 16_000
VAD_CHUNK = 512  # Silero v6 window at 16 kHz (32 ms)
LOW_CUT_HZ = 50.0  # rumble below this is ignored for all level measurements
VOICE_BAND = (250.0, 12_000.0)
MID_BAND = (1_000.0, 4_000.0)
HF_BAND = (3_000.0, 12_000.0)
DB_FLOOR = -120.0  # level reported for digital silence
DIGITAL_EPS = 1e-6  # |x| below this (≈ -120 dBFS) counts as digital silence

MAX_SHIFT_US = 80_000  # word edges move at most this far from the ASR time
MIN_GAP_US = 20_000  # shortest silence recorded as a gap (a stop closure)
SHORT_GAP_US = 80_000  # gaps shorter than this must contain a measurable dip
MIN_WORD_US = 20_000  # refined words keep at least this duration (unless ASR gave less)
BRIDGE_US = 70_000  # internal silences shorter than this stay inside a word (stop closures)
OVERLAP_KEEP_US = 50_000  # ASR overlaps larger than this (event over speech) are left untouched
MIN_CONTRAST_DB = 10.0  # below this speech-to-local-noise contrast, energy refinement is skipped
ACT_SIGMAS = 3.5  # activity margin in noise σ (clamped to 6..15 dB)

BREATH_MIN_US = 120_000  # /f h ʃ/ are usually shorter; inhalations 150-700 ms
BREATH_MAX_US = 1_000_000
BREATH_FLATNESS = 0.03  # spectral flatness 300 Hz-8 kHz: rejects tonal/harmonic sound (vowels < 0.01)
BREATH_MAX_TILT_DB = 3.0  # sibilants are strongly HF-tilted; inhalations are not
BREATH_PEAK_DB = 9.0  # bump peak above the local noise in its best band (or 4σ, whichever is larger)
BREATH_BELOW_SPEECH_DB = 8.0  # voice-band level at least this far below typical speech
BREATH_MAX_VOICED = 0.3
BREATH_BELOW_SIBILANT_DB = 6.0  # HF level at least this far below the speaker's sibilants (p90 HF of speech)

SILENCE_MIN_US = 1_500_000  # inner gaps at least this long read as dead air
NOISE_COVERAGE = 0.30
BREATH_COVERAGE = 0.40
ROOM_TONE_MIN_US = 300_000
ROOM_TONE_EDGE_US = 40_000
ROOM_TONE_TOL_DB = 3.0
ROOM_TONE_MAX_RANGES = 64

_BAND_EDGES = ((300.0, 1000.0), (1000.0, 3000.0), (3000.0, 8000.0))


# ---------------------------------------------------------------------------------------------- data
@dataclass(eq=False)
class PitchTrack:
    """Praat f0 on the feature grid (``f0_hz`` = 0 where unvoiced)."""

    f0_hz: np.ndarray
    floor_hz: float
    ceiling_hz: float

    @property
    def voiced(self) -> np.ndarray:
        return self.f0_hz > 0


@dataclass(eq=False)
class BandStats:
    """Noise and speech statistics of one band's 30 ms-smoothed level curve (dBFS)."""

    noise_med: float
    noise_sigma: float
    local: np.ndarray  # local noise-median track per frame (≥ noise_med)
    speech: float  # typical speech level (median of VAD-speech frames)
    speech_hi: float = DB_FLOOR  # loud speech level (p95 of speech frames; sibilants in the HF band)

    def threshold(self, speech: float | None = None) -> np.ndarray:
        sp = self.speech if speech is None else speech
        margin = float(np.clip(ACT_SIGMAS * self.noise_sigma, 6.0, 15.0))
        return np.maximum(self.local + margin, sp - 50.0)

    def contrast(self, speech: float | None = None) -> np.ndarray:
        sp = self.speech if speech is None else speech
        return sp - self.local


@dataclass(eq=False)
class AudioFeatures:
    """Frame features of one mono recording on a 10 ms grid (frame ``k`` is centred at ``k·hop``)."""

    sr: int
    x: np.ndarray  # mono float32 as decoded
    x_hp: np.ndarray  # zero-phase 50 Hz high-pass (time-domain envelopes)
    hop: int
    win: int
    t_us: np.ndarray  # int64 frame centres
    db: np.ndarray  # full band (≥50 Hz) power, dBFS — for reporting levels
    vb_db: np.ndarray  # voice band 250 Hz-12 kHz power, dBFS — activity / snap
    mid_db: np.ndarray  # 1-4 kHz power, dBFS
    hf_db: np.ndarray  # 3-12 kHz power, dBFS
    band_db: np.ndarray  # (n, 3) 0.3-1 / 1-3 / 3-8 kHz power, dBFS
    tilt_db: np.ndarray  # P(4-10k) − P(0.5-4k), dB
    flatness: np.ndarray  # spectral flatness 300 Hz-8 kHz (0 tonal .. ~0.56 white)
    vad: np.ndarray  # Silero speech probability
    digital: np.ndarray  # bool, digital silence
    duration_us: int
    noise_db: float = DB_FLOOR  # full-band noise median (non-speech frames)
    speech_db: float = DB_FLOOR  # full-band typical speech level
    vbs: BandStats | None = None
    mids: BandStats | None = None
    hfs: BandStats | None = None
    band_noise_db: np.ndarray = field(default_factory=lambda: np.full(3, DB_FLOOR))
    source: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------------ grid helpers
    @property
    def n_frames(self) -> int:
        return int(self.db.shape[0])

    @property
    def hop_us(self) -> int:
        return round(self.hop * 1_000_000 / self.sr)

    def _pos(self, t_us: int) -> float:
        return t_us * self.sr / (1_000_000 * self.hop)

    def frame_at(self, t_us: int) -> int:
        """Nearest frame index for a time (clamped)."""
        return min(max(round(self._pos(t_us)), 0), self.n_frames - 1)

    def frame_ceil(self, t_us: int) -> int:
        return min(max(int(np.ceil(self._pos(t_us) - 1e-9)), 0), self.n_frames - 1)

    def frame_floor(self, t_us: int) -> int:
        return min(max(int(np.floor(self._pos(t_us) + 1e-9)), 0), self.n_frames - 1)

    def frame_time(self, k: int) -> int:
        return int(self.t_us[min(max(k, 0), self.n_frames - 1)])

    def sample_at(self, t_us: int) -> int:
        return min(max(round(t_us * self.sr / 1_000_000), 0), len(self.x))

    # ------------------------------------------------------------------ derived (lazy)
    @cached_property
    def pitch(self) -> PitchTrack:
        with self._lock:
            return _compute_pitch(self)

    @cached_property
    def vb_smooth(self) -> np.ndarray:
        """30 ms power-domain moving average of :attr:`vb_db`."""
        out = _smooth_db(self.vb_db, 3)
        out[self.digital] = DB_FLOOR
        return out

    @cached_property
    def mid_smooth(self) -> np.ndarray:
        out = _smooth_db(self.mid_db, 3)
        out[self.digital] = DB_FLOOR
        return out

    @cached_property
    def hf_smooth(self) -> np.ndarray:
        out = _smooth_db(self.hf_db, 3)
        out[self.digital] = DB_FLOOR
        return out

    @cached_property
    def breath_runs(self) -> list[tuple[int, int]]:
        """Frame ranges ``[k0, k1)`` of detected breaths over the whole recording."""
        return _detect_breath_runs(self)

    @cached_property
    def breath_mask(self) -> np.ndarray:
        m = np.zeros(self.n_frames, dtype=bool)
        for a, b in self.breath_runs:
            m[a:b] = True
        return m


@dataclass(frozen=True)
class Breath:
    """One detected inhalation/exhalation (source clock)."""

    start_us: int
    end_us: int
    peak_db: float  # full-band dBFS
    level_db: float  # full-band power-mean dBFS
    rel_speech_db: float  # peak relative to the recording's typical speech level (negative)
    gap_id: str | None = None


@dataclass
class GapAnalysis:
    words: list[Word]
    gaps: list[Gap]
    breaths: list[Breath]
    room_tone_ranges_us: list[tuple[int, int]]


@dataclass(eq=False)
class _Thresholds:
    vb: np.ndarray  # per-frame activity threshold, voice band (dBFS)
    mid: np.ndarray  # per-frame threshold, 1-4 kHz band
    hf: np.ndarray  # per-frame threshold, HF band
    mid_ok: bool
    hf_ok: bool
    contrast: np.ndarray  # speech − local noise (voice band)
    speech_vb: float
    active: np.ndarray  # bool per frame (energy activity; breath runs excluded)


# ---------------------------------------------------------------------------------------------- audio io
def load_audio(path: str | os.PathLike[str]) -> tuple[np.ndarray, int]:
    """Mono float32 samples and sample rate. WAV/FLAC/AIFF via soundfile; anything else via ffmpeg
    (48 kHz float, first audio stream)."""
    import soundfile as sf

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"audio not found: {p}")
    try:
        data, sr = sf.read(str(p), dtype="float32", always_2d=True)
        x = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
        return np.ascontiguousarray(x, dtype=np.float32), int(sr)
    except Exception:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(p), "-map", "0:a:0", "-ac", "1",
             "-ar", "48000", "-f", "f32le", "-"],
            check=True, capture_output=True,
        )
        return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32), 48_000


_CACHE: OrderedDict[tuple[str, int, int], AudioFeatures] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_SIZE = 2


def load_features(path: str | os.PathLike[str]) -> AudioFeatures:
    """Features of an audio file, cached by (path, size, mtime) so refine/gaps/prosody/metrics share one
    analysis of ``media/audio.wav``."""
    p = Path(path).resolve()
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime_ns)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return hit
    x, sr = load_audio(p)
    feat = compute_features(x, sr, source=str(p))
    with _CACHE_LOCK:
        _CACHE[key] = feat
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return feat


def clear_feature_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------------------------- VAD
_VAD_LOCK = threading.Lock()


@functools.lru_cache(maxsize=1)
def _silero_model() -> Any:
    import torch

    n_threads = torch.get_num_threads()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from silero_vad import load_silero_vad  # importing sets torch threads to 1 globally

        model = load_silero_vad()
    torch.set_num_threads(n_threads)
    return model


def vad_probabilities(x: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    """Silero VAD v6 speech probability per 32 ms chunk of the 16 kHz signal, and the chunk length (s)."""
    import soxr
    import torch

    if len(x) == 0:
        return np.zeros(0, dtype=np.float32), VAD_CHUNK / VAD_SR
    y = x.astype(np.float32) if sr == VAD_SR else soxr.resample(x, sr, VAD_SR, quality="VHQ").astype(np.float32)
    model = _silero_model()
    with _VAD_LOCK, torch.no_grad():
        # Silero runs a small recurrent net on 32 ms chunks: one thread is fastest (the package's own
        # setting) and far more predictable under load; restore the caller's thread count afterwards.
        n_threads = torch.get_num_threads()
        torch.set_num_threads(1)
        try:
            if hasattr(model, "reset_states"):
                model.reset_states()
            probs = model.audio_forward(torch.from_numpy(np.ascontiguousarray(y)), VAD_SR)
            if hasattr(model, "reset_states"):
                model.reset_states()
        finally:
            torch.set_num_threads(n_threads)
    return probs.detach().cpu().numpy().reshape(-1).astype(np.float32), VAD_CHUNK / VAD_SR


# ---------------------------------------------------------------------------------------------- features
def _smooth_db(db: np.ndarray, n: int) -> np.ndarray:
    """Power-domain moving average of a dB curve over ``n`` frames (centred)."""
    from scipy.ndimage import uniform_filter1d

    if n <= 1 or db.size == 0:
        return db.astype(np.float64)
    p = np.power(10.0, db / 10.0)
    return 10.0 * np.log10(uniform_filter1d(p, n, mode="nearest") + 1e-12)


def _power_mean_db(db: np.ndarray) -> float:
    if db.size == 0:
        return DB_FLOOR
    return float(10.0 * np.log10(np.mean(np.power(10.0, db / 10.0)) + 1e-12))


def _robust_sigma(v: np.ndarray) -> float:
    if v.size < 2:
        return 0.0
    med = float(np.median(v))
    return float(1.4826 * np.median(np.abs(v - med)))


def compute_features(x: np.ndarray, sr: int, *, source: str | None = None, vad: np.ndarray | None = None
                     ) -> AudioFeatures:
    """Analyse a mono signal (see module docstring). ``vad`` may pre-supply per-frame probabilities."""
    from numpy.lib.stride_tricks import sliding_window_view
    from scipy.signal import butter, get_window, sosfiltfilt

    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr <= 0:
        raise ValueError("sample rate must be positive")
    # no DC removal on x: it would turn digital silence into a constant offset (band powers skip <50 Hz,
    # and the zero-phase high-pass below removes DC for time-domain envelopes)
    hop = max(1, round(sr * HOP_S))
    win = 2 * hop
    nfft = 1 << int(np.ceil(np.log2(win)))
    n = x.size
    n_frames = n // hop + 1

    # zero-phase high-pass for time-domain envelopes (no timing shift)
    if n > 64:
        sos = butter(4, LOW_CUT_HZ, btype="highpass", fs=sr, output="sos")
        x_hp = sosfiltfilt(sos, x, padlen=min(n - 1, 3 * 2 * sos.shape[0]))
    else:
        x_hp = x.copy()

    # --- STFT band powers (frame k centred at sample k*hop)
    pad = win // 2
    mode = "reflect" if n > pad + win else "constant"
    xp = np.pad(x, (pad, pad + win), mode=mode)
    w = get_window("hann", win, fftbins=True)
    norm = nfft * float(np.sum(w * w))
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    nyq = sr / 2.0

    def bmask(lo: float, hi: float) -> np.ndarray:
        return (freqs >= lo) & (freqs < min(hi, nyq))

    m_full = freqs >= LOW_CUT_HZ
    m_vb = bmask(*VOICE_BAND)
    m_mid = bmask(*MID_BAND)
    m_hf = bmask(*HF_BAND)
    m_bands = [bmask(lo, hi) for lo, hi in _BAND_EDGES]
    m_flat = bmask(300.0, 8000.0)
    m_tilt_hi = bmask(4000.0, 10000.0)
    m_tilt_lo = bmask(500.0, 4000.0)

    frames_view = sliding_window_view(xp, win)[::hop][:n_frames]
    db = np.empty(n_frames)
    vb_db = np.empty(n_frames)
    mid_db = np.empty(n_frames)
    hf_db = np.empty(n_frames)
    band_db = np.empty((n_frames, 3))
    tilt = np.empty(n_frames)
    flat = np.empty(n_frames)
    peak = np.empty(n_frames)
    eps = 1e-12

    def bsum(pw: np.ndarray, m: np.ndarray) -> np.ndarray:
        if not m.any():
            return np.full(pw.shape[0], DB_FLOOR)
        return 10 * np.log10(pw[:, m].sum(1) + eps)

    for c in range(0, n_frames, 2048):
        fr = frames_view[c:c + 2048]
        m = fr.shape[0]
        peak[c:c + m] = np.max(np.abs(fr), axis=1)
        spec = np.fft.rfft(fr * w, n=nfft)
        pw = (spec.real ** 2 + spec.imag ** 2) * (2.0 / norm)
        db[c:c + m] = bsum(pw, m_full)
        vb_db[c:c + m] = bsum(pw, m_vb)
        mid_db[c:c + m] = bsum(pw, m_mid)
        hf_db[c:c + m] = bsum(pw, m_hf)
        for b, mb in enumerate(m_bands):
            band_db[c:c + m, b] = bsum(pw, mb)
        if m_tilt_hi.any() and m_tilt_lo.any():
            tilt[c:c + m] = 10 * np.log10((pw[:, m_tilt_hi].sum(1) + eps) / (pw[:, m_tilt_lo].sum(1) + eps))
        else:
            tilt[c:c + m] = -30.0
        sub = pw[:, m_flat] + 1e-20
        flat[c:c + m] = np.exp(np.mean(np.log(sub), axis=1)) / np.mean(sub, axis=1)

    digital = peak < DIGITAL_EPS
    for arr in (db, vb_db, mid_db, hf_db):
        arr[digital] = DB_FLOOR
        np.maximum(arr, DB_FLOOR, out=arr)
    band_db[digital] = DB_FLOOR
    np.maximum(band_db, DB_FLOOR, out=band_db)
    flat[digital] = 0.0
    tilt[digital] = 0.0

    t_us = np.rint(np.arange(n_frames) * hop * 1_000_000 / sr).astype(np.int64)

    # --- VAD
    if vad is None:
        probs, chunk_s = vad_probabilities(x.astype(np.float32), sr)
        if probs.size:
            centres = (np.arange(probs.size) + 0.5) * chunk_s * 1_000_000
            vad = np.interp(t_us.astype(np.float64), centres, probs).astype(np.float32)
        else:
            vad = np.zeros(n_frames, dtype=np.float32)
    vad = np.array(vad, dtype=np.float32)
    if vad.shape[0] != n_frames:
        raise ValueError("vad length must equal the number of frames")
    vad[digital] = 0.0

    feat = AudioFeatures(
        sr=int(sr), x=x.astype(np.float32), x_hp=x_hp.astype(np.float32), hop=hop, win=win, t_us=t_us,
        db=db, vb_db=vb_db, mid_db=mid_db, hf_db=hf_db, band_db=band_db, tilt_db=tilt, flatness=flat, vad=vad,
        digital=digital, duration_us=round(n * 1_000_000 / sr), source=source,
    )
    _fit_levels(feat)
    return feat


def _noise_frames(feat: AudioFeatures, values: np.ndarray) -> np.ndarray:
    """Frames for noise statistics: VAD non-speech *and* within 12 dB of the quiet end (10th percentile)
    of the level distribution, so VAD-blind material (tones, music) cannot pass words off as noise;
    fallback: the quietest 15 %."""
    nd = ~feat.digital
    if not nd.any():
        return nd
    quiet_end = float(np.percentile(values[nd], 10))
    cand = nd & (feat.vad < 0.2) & (values <= quiet_end + 12.0)
    if cand.sum() >= 30:
        return cand
    return nd & (values <= float(np.percentile(values[nd], 15)))


def _speech_frames(feat: AudioFeatures, values: np.ndarray, noise_med: float) -> np.ndarray:
    nd = ~feat.digital
    sp = nd & (feat.vad >= 0.5)
    if sp.sum() >= 50:
        return sp
    top = float(np.percentile(values[nd], 99)) if nd.any() else DB_FLOOR
    return nd & (values > noise_med + 0.5 * (top - noise_med))


def _band_stats(feat: AudioFeatures, smooth: np.ndarray, raw: np.ndarray) -> BandStats:
    from scipy.ndimage import minimum_filter1d, uniform_filter1d

    nd = ~feat.digital
    n = feat.n_frames
    if not nd.any():
        return BandStats(DB_FLOOR, 0.0, np.full(n, DB_FLOOR), DB_FLOOR, DB_FLOOR)
    ns = _noise_frames(feat, smooth)
    med = float(np.median(smooth[ns])) if ns.any() else float(np.min(smooth[nd]))
    sigma = _robust_sigma(smooth[ns]) if ns.any() else 0.0
    # minimum-statistics local track (digital holes held at the global median)
    v = smooth.copy()
    v[~nd] = med
    mn = minimum_filter1d(v, size=max(3, round(3.0 / HOP_S) | 1), mode="nearest")
    tr = uniform_filter1d(mn, max(3, round(1.0 / HOP_S)), mode="nearest")
    bias = float(np.median(v[ns]) - np.median(tr[ns])) if ns.any() else 0.0
    local = np.clip(tr + bias, med, med + 12.0)
    sp = _speech_frames(feat, raw, med)
    speech = float(np.median(raw[sp])) if sp.any() else med
    speech_hi = float(np.percentile(raw[sp], 95)) if sp.any() else med
    return BandStats(med, sigma, local, speech, speech_hi)


def _fit_levels(feat: AudioFeatures) -> None:
    feat.vbs = _band_stats(feat, feat.vb_smooth, feat.vb_db)
    feat.mids = _band_stats(feat, feat.mid_smooth, feat.mid_db)
    feat.hfs = _band_stats(feat, feat.hf_smooth, feat.hf_db)
    nd = ~feat.digital
    if nd.any():
        ns = _noise_frames(feat, feat.db)
        feat.noise_db = float(np.median(feat.db[ns])) if ns.any() else DB_FLOOR
        feat.speech_db = float(np.median(feat.db[_speech_frames(feat, feat.db, feat.noise_db)]))
        feat.band_noise_db = np.array([float(np.median(feat.band_db[ns, b])) if ns.any() else DB_FLOOR
                                       for b in range(3)])


def _compute_pitch(feat: AudioFeatures) -> PitchTrack:
    """Two-pass Praat autocorrelation pitch mapped onto the feature grid."""
    import parselmouth

    n = feat.n_frames
    if feat.x.size < int(0.1 * feat.sr) or not (~feat.digital).any():
        return PitchTrack(np.zeros(n), 60.0, 700.0)
    snd = parselmouth.Sound(feat.x.astype(np.float64), sampling_frequency=float(feat.sr))

    def run(floor: float, ceiling: float, accurate: bool) -> np.ndarray:
        p = snd.to_pitch_ac(time_step=HOP_S, pitch_floor=floor, pitch_ceiling=ceiling, very_accurate=accurate)
        f = np.asarray(p.selected_array["frequency"], dtype=np.float64)
        if f.size == 0:
            return np.zeros(n)
        x1 = float(p.xs()[0])
        dt = float(p.time_step)
        idx = np.rint((feat.t_us / 1_000_000.0 - x1) / dt).astype(np.int64)
        ok = (idx >= 0) & (idx < f.size)
        out = np.zeros(n)
        out[ok] = f[idx[ok]]
        return out

    floor, ceiling = 60.0, 700.0
    f0 = run(floor, ceiling, False)
    v = f0[f0 > 0]
    if v.size >= 20:
        q1, q3 = np.percentile(v, [25, 75])
        floor = float(np.clip(0.75 * q1, 50.0, 400.0))
        ceiling = float(np.clip(2.0 * q3, floor + 100.0, 1100.0))
        f0 = run(floor, ceiling, True)
    f0[feat.digital] = 0.0
    return PitchTrack(f0, floor, ceiling)


def pitch_track(source: Any) -> PitchTrack:
    """Speaker-adapted Praat f0 track (10 ms grid) for a Job / path / :class:`AudioFeatures`."""
    return _features(source).pitch


# ---------------------------------------------------------------------------------------------- helpers
def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of True as ``[start, end)`` index pairs."""
    if mask.size == 0:
        return []
    m = np.concatenate(([False], mask.astype(bool), [False]))
    d = np.diff(m.astype(np.int8))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def _close(mask: np.ndarray, holes: int) -> np.ndarray:
    """Fill False holes of at most ``holes`` frames between True runs."""
    out = mask.copy()
    for a, b in _runs(~mask):
        if a > 0 and b < mask.size and b - a <= holes:
            out[a:b] = True
    return out


def _dilate(mask: np.ndarray, k: int) -> np.ndarray:
    if k <= 0 or not mask.any():
        return mask.copy()
    from scipy.ndimage import binary_dilation

    return binary_dilation(mask, structure=np.ones(2 * k + 1, dtype=bool))


def _features(source: Any) -> AudioFeatures:
    if isinstance(source, AudioFeatures):
        return source
    if isinstance(source, tuple) and len(source) == 2:
        return compute_features(np.asarray(source[0]), int(source[1]))
    from studio.jobs import Job

    if isinstance(source, Job):
        return load_features(source.audio_path)
    if isinstance(source, (str, os.PathLike)):
        return load_features(source)
    raise TypeError(f"cannot get audio features from {type(source).__name__}")


def _audio_available(source: Any) -> bool:
    from studio.jobs import Job

    if isinstance(source, Job):
        return source.audio_path.exists()
    if isinstance(source, (str, os.PathLike)):
        return Path(source).exists()
    return True


def _thresholds(feat: AudioFeatures, words: list[Word] | None = None) -> _Thresholds:
    assert feat.vbs is not None and feat.mids is not None and feat.hfs is not None
    speech_vb, speech_mid, speech_hf = feat.vbs.speech, feat.mids.speech, feat.hfs.speech
    if words:
        inside = np.zeros(feat.n_frames, dtype=bool)
        for w in words:
            if w.kind in ("word", "filler", "cutoff") and w.end_us > w.start_us:
                inside[feat.frame_ceil(w.start_us):feat.frame_floor(w.end_us) + 1] = True
        inside &= ~feat.digital
        if inside.sum() >= 20:
            # the loud part of word frames = nuclei; robust to ASR padding inside word spans
            speech_vb = float(np.percentile(feat.vb_db[inside], 60))
            speech_mid = float(np.percentile(feat.mid_db[inside], 60))
            speech_hf = float(np.percentile(feat.hf_db[inside], 60))
    vb_thr = feat.vbs.threshold(speech_vb)
    mid_thr = feat.mids.threshold(speech_mid)
    hf_thr = feat.hfs.threshold(speech_hf)
    mid_ok = bool(np.median(feat.mids.contrast(speech_mid)) >= 12.0)
    hf_ok = bool(np.median(feat.hfs.contrast(speech_hf)) >= 12.0)
    act = feat.vb_smooth > vb_thr
    if mid_ok:
        act |= feat.mid_smooth > mid_thr
    if hf_ok:
        act |= feat.hf_smooth > hf_thr
    act &= ~feat.digital
    act &= ~feat.breath_mask
    return _Thresholds(vb=vb_thr, mid=mid_thr, hf=hf_thr, mid_ok=mid_ok, hf_ok=hf_ok,
                       contrast=feat.vbs.contrast(speech_vb), speech_vb=speech_vb, active=act)


def _envelope(sig: np.ndarray, sr: int, a: int, b: int, win_s: float = 0.006, step_s: float = 0.001
              ) -> tuple[np.ndarray, np.ndarray]:
    """Mean-square envelope (dB) at sample centres ``a..b`` every ``step_s`` with a ``win_s`` box window."""
    w = max(2, round(win_s * sr))
    st = max(1, round(step_s * sr))
    a = max(0, a)
    b = min(len(sig) - 1, b)
    if b < a:
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    lo = max(0, a - w // 2)
    hi = min(len(sig), b + w - w // 2 + 1)
    seg = sig[lo:hi].astype(np.float64)
    c = np.concatenate(([0.0], np.cumsum(seg * seg)))
    centres = np.arange(a, b + 1, st, dtype=np.int64)
    i0 = np.clip(centres - w // 2 - lo, 0, seg.size)
    i1 = np.clip(centres + (w - w // 2) - lo, 0, seg.size)
    ms = (c[i1] - c[i0]) / np.maximum(i1 - i0, 1)
    return centres, 10.0 * np.log10(ms + 1e-12)


def _band_signal(feat: AudioFeatures, a: int, b: int, lo_hz: float, hi_hz: float) -> tuple[int, np.ndarray]:
    """Zero-phase band-pass of samples ``[a, b]`` (with 20 ms padding); returns (offset, signal)."""
    from scipy.signal import butter, sosfiltfilt

    pad = int(0.02 * feat.sr)
    lo = max(0, a - pad)
    hi = min(len(feat.x_hp), b + pad + 1)
    seg = feat.x_hp[lo:hi].astype(np.float64)
    top = min(hi_hz, 0.45 * feat.sr)
    if top <= lo_hz * 1.1 or seg.size < 32:
        return lo, np.zeros_like(seg)
    sos = butter(4, [lo_hz, top], btype="bandpass", fs=feat.sr, output="sos")
    return lo, sosfiltfilt(sos, seg, padlen=min(seg.size - 1, 24))


def _band_envelope(feat: AudioFeatures, a: int, b: int, band: tuple[float, float], win_s: float = 0.006
                   ) -> tuple[np.ndarray, np.ndarray]:
    off, sig = _band_signal(feat, a, b, *band)
    c, env = _envelope(sig, feat.sr, a - off, b - off, win_s=win_s)
    return c + off, env


def _fine_edge(feat: AudioFeatures, thr: _Thresholds, t_us: int, *, onset: bool,
               lo_us: int | None = None, hi_us: int | None = None) -> int:
    """Move a frame-level edge to the 1 ms envelope threshold crossing. The 30 ms smoothing puts coarse
    onsets up to ~20 ms early and offsets late, so the search reaches 30 ms inward and 12 ms outward."""
    k = feat.frame_at(t_us)
    back, fwd = (12_000, 30_000) if onset else (30_000, 12_000)
    a_us = t_us - back if lo_us is None else max(t_us - back, lo_us)
    b_us = t_us + fwd if hi_us is None else min(t_us + fwd, hi_us)
    if b_us <= a_us:
        return t_us
    a, b = feat.sample_at(a_us), feat.sample_at(b_us)
    centres, env = _band_envelope(feat, a, b, VOICE_BAND)
    if centres.size == 0:
        return t_us
    above = env > thr.vb[k]
    for ok, band, level in ((thr.mid_ok, MID_BAND, thr.mid[k]), (thr.hf_ok, HF_BAND, thr.hf[k])):
        if ok:
            _, benv = _band_envelope(feat, a, b, band)
            if benv.size == above.size:
                above |= benv > level
    idx = np.flatnonzero(above)
    if idx.size == 0:
        return t_us
    half = round(0.0005 * feat.sr)
    s = int(centres[idx[0]]) - half if onset else int(centres[idx[-1]]) + half
    t = round(s * 1_000_000 / feat.sr)
    return int(min(max(t, a_us), b_us))


# ---------------------------------------------------------------------------------------------- breaths
def _detect_breath_runs(feat: AudioFeatures) -> list[tuple[int, int]]:
    if feat.n_frames == 0 or feat.vbs is None or feat.mids is None or feat.hfs is None:
        return []
    voiced = feat.pitch.voiced
    bands = [(feat.vb_smooth, feat.vbs), (feat.mid_smooth, feat.mids), (feat.hf_smooth, feat.hfs)]
    bumped = np.zeros(feat.n_frames, dtype=bool)
    peak_ok = np.zeros(feat.n_frames, dtype=bool)
    for sm, st in bands:
        bump = sm - st.local
        bumped |= bump >= max(5.0, 2.5 * st.noise_sigma)
        peak_ok |= bump >= max(BREATH_PEAK_DB, 4.0 * st.noise_sigma)
    cand = (
        ~feat.digital
        & (feat.flatness >= BREATH_FLATNESS)
        & (feat.tilt_db <= BREATH_MAX_TILT_DB)
        & bumped
        & (feat.vb_smooth <= feat.vbs.speech - BREATH_BELOW_SPEECH_DB)
        & (feat.hf_smooth <= feat.hfs.speech_hi - BREATH_BELOW_SIBILANT_DB)
    )
    cand = _close(cand, 2)
    best_bump = np.max(np.stack([sm - st.local for sm, st in bands]), axis=0)
    speechy = voiced | (feat.hf_smooth > feat.hfs.speech_hi - BREATH_BELOW_SIBILANT_DB)
    min_f = int(np.ceil(BREATH_MIN_US / 1_000_000 / HOP_S))
    max_f = int(np.floor(BREATH_MAX_US / 1_000_000 / HOP_S))
    out: list[tuple[int, int]] = []
    for a, b in _runs(cand):
        if not (min_f <= b - a <= max_f):
            continue
        if not peak_ok[a:b].any():
            continue
        if float(np.mean(voiced[a:b])) > BREATH_MAX_VOICED:
            continue
        if np.any(speechy[max(0, a - 2):a]):
            continue  # attached to a vowel or sibilant: the tail of a word-final fricative, not a breath
        rises = float(np.max(best_bump[a:b]) - best_bump[a]) >= 3.0
        quiet_before = a == 0 or float(best_bump[a - 1]) < 4.0 or bool(feat.digital[a - 1])
        if not (rises or quiet_before):
            continue  # starts at its loudest: a decay, not an inhale
        up = np.median(feat.band_db[a:b] - feat.band_noise_db[None, :], axis=0)
        if not (up[1] >= 6.0 or up[2] >= 6.0):
            continue  # a low-frequency thump, not aspiration noise
        out.append((a, b))
    return out


def _breath_from_run(feat: AudioFeatures, a: int, b: int, gap_id: str | None = None) -> Breath:
    seg = feat.db[a:b]
    peak = float(np.max(seg)) if seg.size else DB_FLOOR
    half = feat.hop_us // 2
    return Breath(
        start_us=max(0, feat.frame_time(a) - half), end_us=min(feat.duration_us, feat.frame_time(b - 1) + half),
        peak_db=round(peak, 2), level_db=round(_power_mean_db(seg), 2),
        rel_speech_db=round(peak - feat.speech_db, 2), gap_id=gap_id,
    )


# ---------------------------------------------------------------------------------------------- refinement
def _scan_offset(active: np.ndarray, k_lo: int, k_hi: int, bridge: int) -> int:
    """Last active frame connected (holes < ``bridge``) to ``k_lo``; -1 if none."""
    last = -1
    hole = 0
    for k in range(k_lo, k_hi + 1):
        if active[k]:
            last = k
            hole = 0
        else:
            hole += 1
            if hole >= bridge:
                break
    return last


def _scan_onset(active: np.ndarray, k_lo: int, k_hi: int, bridge: int) -> int:
    """First active frame connected (holes < ``bridge``) to ``k_hi`` scanning backward; -1 if none."""
    first = -1
    hole = 0
    for k in range(k_hi, k_lo - 1, -1):
        if active[k]:
            first = k
            hole = 0
        else:
            hole += 1
            if hole >= bridge:
                break
    return first


def _refine(feat: AudioFeatures, words: list[Word]) -> list[Word]:
    if not words:
        return []
    thr = _thresholds(feat, words)
    act = thr.active
    n = len(words)
    end_audio = feat.duration_us
    starts = [w.start_us for w in words]
    ends = [w.end_us for w in words]
    new_s = list(starts)
    new_e = list(ends)
    bridge = max(1, round(BRIDGE_US / 1_000_000 / HOP_S))
    half = feat.hop_us // 2
    lowc = thr.contrast < MIN_CONTRAST_DB

    def contrast_ok(t0: int, t1: int) -> bool:
        a, b = feat.frame_at(t0), feat.frame_at(t1)
        return not bool(np.any(lowc[min(a, b):max(a, b) + 1]))

    def onset(S: int, lo_us: int, hi_us: int) -> int:
        lo_us, hi_us = max(0, lo_us), min(end_audio, hi_us)
        if hi_us < lo_us:
            return min(max(S, lo_us), max(lo_us, hi_us))
        k_lo, k_hi = feat.frame_ceil(lo_us), feat.frame_floor(hi_us)
        if k_hi < k_lo:
            return S
        k = _scan_onset(act, k_lo, k_hi, bridge)
        if k < 0:
            return hi_us
        coarse = max(lo_us, feat.frame_time(k) - half)
        return _fine_edge(feat, thr, coarse, onset=True, lo_us=lo_us, hi_us=hi_us)

    def offset(E: int, lo_us: int, hi_us: int) -> int:
        lo_us, hi_us = max(0, lo_us), min(end_audio, hi_us)
        if hi_us < lo_us:
            return min(max(E, lo_us), max(lo_us, hi_us))
        k_lo, k_hi = feat.frame_ceil(lo_us), feat.frame_floor(hi_us)
        if k_hi < k_lo:
            return E
        k = _scan_offset(act, k_lo, k_hi, bridge)
        if k < 0:
            return lo_us
        coarse = min(hi_us, feat.frame_time(k) + half)
        return _fine_edge(feat, thr, coarse, onset=False, lo_us=lo_us, hi_us=hi_us)

    def valley(t0: int, t1: int, junction: int) -> int:
        """Deepest dip of the smoothed voice band within ±40 ms of the ASR junction (inside [t0, t1]):
        where two words are joined by continuous sound, cut where it is quietest near where ASR put it."""
        lo_t, hi_t = max(t0, junction - 40_000), min(t1, junction + 40_000)
        if hi_t < lo_t:
            lo_t, hi_t = t0, t1
        a, b = feat.frame_ceil(lo_t), feat.frame_floor(hi_t)
        if b < a:
            return int(min(max(junction, t0), t1))
        k = a + int(np.argmin(feat.vb_smooth[a:b + 1]))
        return int(min(max(feat.frame_time(k), t0), t1))

    # first word onset
    if starts[0] < end_audio and contrast_ok(starts[0] - MAX_SHIFT_US, starts[0] + MAX_SHIFT_US):
        hi = starts[0] + MAX_SHIFT_US
        if ends[0] - starts[0] >= 2 * MIN_WORD_US:
            hi = min(hi, ends[0] - MIN_WORD_US)
        new_s[0] = onset(starts[0], starts[0] - MAX_SHIFT_US, max(hi, starts[0]))

    for i in range(n - 1):
        E, S = ends[i], starts[i + 1]
        if S < E - OVERLAP_KEEP_US or end_audio < E or end_audio < S:
            continue  # overlapping event / outside the audio: keep ASR times
        if not contrast_ok(min(E, S) - MAX_SHIFT_US, max(E, S) + MAX_SHIFT_US):
            continue
        lo_e = E - MAX_SHIFT_US
        if E - new_s[i] >= 2 * MIN_WORD_US:
            lo_e = max(lo_e, new_s[i] + MIN_WORD_US)
        lo_e = max(lo_e, new_s[i])
        hi_e = E + MAX_SHIFT_US
        lo_s = max(S - MAX_SHIFT_US, new_s[i])
        hi_s = S + MAX_SHIFT_US
        if ends[i + 1] - S >= 2 * MIN_WORD_US:
            hi_s = min(hi_s, ends[i + 1] - MIN_WORD_US)
        hi_s = max(hi_s, S)
        off = offset(E, lo_e, hi_e)
        on = onset(S, lo_s, hi_s)
        if off > on:
            # overlap: split at the longest silent run between them, else at the energy valley
            a, b = feat.frame_ceil(on), feat.frame_floor(off)
            best: tuple[int, int] | None = None
            if b >= a:
                for ra, rb in _runs(~act[a:b + 1]):
                    if rb - ra >= 2 and (best is None or rb - ra > best[1] - best[0]):
                        best = (a + ra, a + rb)
            if best is not None:
                off_new = max(on, feat.frame_time(best[0]) - half)
                on_new = min(off, feat.frame_time(best[1] - 1) + half)
                off, on = off_new, max(on_new, off_new)
            else:
                off = on = valley(on, off, (E + S) // 2)
        off = int(min(max(off, E - MAX_SHIFT_US, new_s[i]), E + MAX_SHIFT_US))
        on = int(min(max(on, S - MAX_SHIFT_US), S + MAX_SHIFT_US))
        if on < off:
            mid = int(min(max((on + off) // 2, S - MAX_SHIFT_US, E - MAX_SHIFT_US), S + MAX_SHIFT_US,
                          E + MAX_SHIFT_US))
            on = off = max(mid, new_s[i])
        new_e[i], new_s[i + 1] = off, on

    # last word offset
    E = ends[-1]
    if end_audio >= E and contrast_ok(E - MAX_SHIFT_US, E + MAX_SHIFT_US):
        lo = E - MAX_SHIFT_US
        if E - new_s[-1] >= 2 * MIN_WORD_US:
            lo = max(lo, new_s[-1] + MIN_WORD_US)
        new_e[-1] = offset(E, max(lo, new_s[-1]), E + MAX_SHIFT_US)

    # sanitize: start ≤ end; starts non-decreasing
    out: list[Word] = []
    prev_start = 0
    for i, w in enumerate(words):
        s = max(int(new_s[i]), prev_start, 0)
        e = max(int(new_e[i]), s)
        prev_start = s
        if s == w.start_us and e == w.end_us:
            out.append(w)
            continue
        update: dict[str, Any] = {"start_us": s, "end_us": e}
        if w.chars:
            chars = []
            last = len(w.chars) - 1
            for j, ch in enumerate(w.chars):
                cs = s if j == 0 else min(max(ch.start_us, s), e)  # first char starts with the word
                ce = e if j == last else min(max(ch.end_us, cs), e)  # last char ends with it
                chars.append(ch.model_copy(update={"start_us": cs, "end_us": max(ce, cs)}))
            update["chars"] = chars
        out.append(w.model_copy(update=update))
    return out


# ---------------------------------------------------------------------------------------------- gaps
def _gap_spans(words: list[Word], duration_us: int) -> list[tuple[str | None, str | None, int, int]]:
    spans: list[tuple[str | None, str | None, int, int]] = []
    if not words:
        return spans
    if words[0].start_us >= MIN_GAP_US:
        spans.append((None, words[0].id, 0, words[0].start_us))
    run_end = words[0].end_us
    for i in range(len(words) - 1):
        run_end = max(run_end, words[i].end_us)
        s, e = run_end, words[i + 1].start_us
        if e - s >= MIN_GAP_US:
            spans.append((words[i].id, words[i + 1].id, s, e))
    last_end = max(run_end, words[-1].end_us)
    if duration_us - last_end >= MIN_GAP_US:
        spans.append((words[-1].id, None, last_end, duration_us))
    return spans


def _measure_gap(feat: AudioFeatures, thr: _Thresholds, gid: str, after: str | None, before: str | None,
                 start: int, end: int) -> tuple[Gap, list[Breath]] | None:
    from studio.perception.index import Gap

    dur = end - start
    guard = int(min(15_000, dur // 4))
    k0 = feat.frame_ceil(start + guard)
    k1 = feat.frame_floor(end - guard)
    if k1 < k0:
        k0 = k1 = feat.frame_at((start + end) // 2)
    sl = slice(k0, k1 + 1)
    nfr = k1 - k0 + 1

    # short gaps must contain a real dip below the activity threshold
    if (after is not None and before is not None and dur < SHORT_GAP_US
            and float(np.min(feat.vb_smooth[sl] - thr.vb[sl])) > 0.0):
        return None

    # breaths inside the gap (clipped to it)
    breaths: list[Breath] = []
    bmask = np.zeros(nfr, dtype=bool)
    for a, b in feat.breath_runs:
        ca, cb = max(a, k0), min(b, k1 + 1)
        if cb - ca >= max(2, int(0.08 / HOP_S)):
            bmask[ca - k0:cb - k0] = True
            breaths.append(_breath_from_run(feat, ca, cb, gid))
    breath_us = int(bmask.sum()) * feat.hop_us

    # noise: active non-breath runs not attached to the word edges, or speech-like VAD without words
    act = thr.active[sl] & ~bmask
    noise = np.zeros(nfr, dtype=bool)
    for a, b in _runs(act):
        touches_left = after is not None and a == 0
        touches_right = before is not None and b == nfr
        if not (touches_left or touches_right) or (a == 0 and b == nfr):
            noise[a:b] = True  # isolated sound, or sound through the whole gap (an untranscribed filler)
    far0 = feat.frame_ceil(start + 150_000) - k0
    far1 = feat.frame_floor(end - 50_000) - k0
    if far1 - far0 >= 10:
        g0, g1 = k0 + far0, k0 + far1 + 1
        speechy = (feat.vad[g0:g1] >= 0.7) & (feat.vb_smooth[g0:g1] > thr.vb[g0:g1] - 3.0)
        if speechy.mean() >= 0.5:
            noise[far0:far1 + 1] |= speechy & ~bmask[far0:far1 + 1]
    noise_us = int(noise.sum()) * feat.hop_us

    energy_db = float(np.median(feat.db[sl]))  # typical level: robust to decay/onset energy at the edges
    digital_gap = float(np.mean(feat.digital[sl])) >= 0.9

    if dur > 0 and noise_us >= 60_000 and noise_us / dur >= NOISE_COVERAGE:
        kind = "noise"
    elif breaths and dur > 0 and breath_us / dur >= BREATH_COVERAGE:
        kind = "breath"
    elif after is None or before is None or dur >= SILENCE_MIN_US or digital_gap or energy_db <= -100.0:
        kind = "silence"
    else:
        kind = "pause"

    snap = _snap_point(feat, start, end, k0, k1, bmask, noise, after, before)
    spans = []  # measured breath spans for the audio stage (breath attenuation acts on these, not the gap)
    for br in breaths:
        a, b = max(int(start), br.start_us), min(int(end), br.end_us)
        if b > a:
            spans.append((a, b))
    gap = Gap(id=gid, after_word_id=after, before_word_id=before, start_us=int(start), end_us=int(end),
              kind=kind, snap_us=int(snap), energy_db=round(energy_db, 2), has_breath=bool(breaths),  # type: ignore[arg-type]
              breaths_us=spans)
    return gap, breaths


def _snap_point(feat: AudioFeatures, start: int, end: int, k0: int, k1: int, bmask: np.ndarray,
                noise: np.ndarray, after: str | None, before: str | None) -> int:
    nfr = k1 - k0 + 1
    score = feat.vb_smooth[k0:k1 + 1].copy()
    excl = _dilate(bmask, 2) | _dilate(noise, 1)
    # Breaths belong to the words they precede (an inhale prepares the next thought), unless one clearly
    # trails the previous word (starts ≤60 ms after it and ends ≥300 ms before the next). Snap after
    # trailing breaths and before the first preparatory one; if there is no room there, after it.
    lo, hi = 0, nfr
    pre_start: int | None = None
    for a, b in _runs(bmask):
        trailing = (after is not None and a * feat.hop_us <= 60_000
                    and (before is None or (nfr - b) * feat.hop_us >= 300_000))
        if trailing:
            lo = max(lo, b)
        elif pre_start is None:
            pre_start = a
    if pre_start is not None:
        hi = max(lo, pre_start)
    allowed = np.zeros(nfr, dtype=bool)
    allowed[lo:hi] = True
    cand = allowed & ~excl
    if int(cand.sum()) < 2 and pre_start is not None:
        after_pre = np.zeros(nfr, dtype=bool)
        last_b = max(b for _, b in _runs(bmask))
        after_pre[last_b:] = True
        if (after_pre & ~excl).sum() >= 2:
            cand = after_pre & ~excl
    for fallback in (~excl, ~bmask, np.ones(nfr, dtype=bool)):
        if cand.any():
            break
        cand = fallback
    gmin = float(np.min(score[cand]))
    quiet = cand & (score <= gmin + 3.0)
    ra, rb = max(_runs(quiet), key=lambda r: (r[1] - r[0], -float(np.mean(score[r[0]:r[1]]))))
    centre = (ra + rb - 1) / 2.0
    length = max(rb - ra, 1)
    idx = np.arange(ra, rb)
    k_rel = int(idx[np.argmin(score[ra:rb] + 2.0 * np.abs(idx - centre) / length)])
    t = feat.frame_time(k0 + k_rel)
    # ~1 ms refinement (5 ms window) inside the chosen quiet run
    lo_t = max(start, feat.frame_time(k0 + ra) - feat.hop_us // 2)
    hi_t = min(end, feat.frame_time(k0 + rb - 1) + feat.hop_us // 2)
    a = feat.sample_at(max(lo_t, t - feat.hop_us))
    b = feat.sample_at(min(hi_t, t + feat.hop_us))
    if b > a:
        centres, env = _band_envelope(feat, a, b, VOICE_BAND, win_s=0.005)
        if centres.size and np.ptp(env) > 0.5:
            t = round(int(centres[int(np.argmin(env))]) * 1_000_000 / feat.sr)
    return int(min(max(t, start), end))


def _fallback_gaps(words: list[Word], duration_us: int | None) -> list[Gap]:
    """Gaps from word times only (no audio): snap at the midpoint, kind by position/duration."""
    from studio.perception.index import Gap, gap_id

    total = duration_us if duration_us is not None else (max(w.end_us for w in words) if words else 0)
    out = []
    for i, (after, before, s, e) in enumerate(_gap_spans(words, total), start=1):
        kind = "silence" if (after is None or before is None or e - s >= SILENCE_MIN_US) else "pause"
        out.append(Gap(id=gap_id(i), after_word_id=after, before_word_id=before, start_us=s, end_us=e,
                       kind=kind, snap_us=(s + e) // 2, energy_db=None, has_breath=False))  # type: ignore[arg-type]
    return out


def _gaps(feat: AudioFeatures, words: list[Word], thr: _Thresholds | None = None
          ) -> tuple[list[Gap], list[Breath]]:
    from studio.perception.index import gap_id

    if not words:
        return [], []
    thr = thr or _thresholds(feat, words)
    gaps: list[Gap] = []
    breaths: list[Breath] = []
    for after, before, s, e in _gap_spans(words, feat.duration_us):
        e_c = min(e, feat.duration_us)
        if e_c - s < MIN_GAP_US:
            continue
        res = _measure_gap(feat, thr, gap_id(len(gaps) + 1), after, before, s, e_c)
        if res is None:
            continue
        gaps.append(res[0])
        breaths.extend(res[1])
    return gaps, breaths


# ---------------------------------------------------------------------------------------------- room tone
def _room_tone(feat: AudioFeatures, gaps: list[Gap] | None, min_us: int, thr: _Thresholds | None = None
               ) -> list[tuple[int, int]]:
    n = feat.n_frames
    if n == 0 or feat.vbs is None:
        return []
    region = np.zeros(n, dtype=bool)
    if gaps:
        for g in gaps:
            s = g.start_us + (ROOM_TONE_EDGE_US if g.after_word_id is not None else 0)
            e = g.end_us - (ROOM_TONE_EDGE_US if g.before_word_id is not None else 0)
            if e - s >= min_us:
                region[feat.frame_ceil(s):feat.frame_floor(e) + 1] = True
    else:
        region = _close(feat.vad < 0.3, 2)
    thr = thr or _thresholds(feat)
    tol = max(ROOM_TONE_TOL_DB, 2.0 * feat.vbs.noise_sigma)
    ok = (
        region
        & ~feat.digital
        & ~_dilate(feat.breath_mask, 3)
        & ~_dilate(thr.active, 3)
        & (feat.vad < 0.5)
        & (feat.vb_smooth <= feat.vbs.local + tol)
    )
    ok = _close(ok, 1) & region & ~feat.digital
    min_f = int(np.ceil(min_us / 1_000_000 / HOP_S))
    half = feat.hop_us // 2
    found: list[tuple[float, int, int]] = []
    for a, b in _runs(ok):
        if b - a < min_f:
            continue
        seg = feat.vb_db[a:b]
        if _robust_sigma(seg) > 6.0:
            continue  # not stationary
        found.append((_power_mean_db(seg), max(0, feat.frame_time(a) - half),
                      min(feat.duration_us, feat.frame_time(b - 1) + half)))
    if len(found) > ROOM_TONE_MAX_RANGES:
        found = sorted(found)[:ROOM_TONE_MAX_RANGES]
    return sorted(((s, e) for _, s, e in found), key=lambda r: r[0])


# ---------------------------------------------------------------------------------------------- public API
def refine_word_boundaries(job: Job | AudioFeatures | str | os.PathLike[str], words: list[Word]) -> list[Word]:
    """Return copies of ``words`` with acoustically refined ``start_us``/``end_us`` (order preserved).

    Each edge moves at most ±80 ms to the measured onset/offset (speech tails and sibilants included,
    breaths excluded). ``job`` may be a :class:`~studio.jobs.Job`, an audio path or precomputed
    :class:`AudioFeatures`. Without audio the words are returned unchanged.
    """
    if not words:
        return []
    if not _audio_available(job):
        return list(words)
    return _refine(_features(job), list(words))


def detect_gaps(job: Job | AudioFeatures | str | os.PathLike[str], words: list[Word], *, refine: bool = False
                ) -> list[Gap]:
    """Measure every inter-word silence (plus leading/trailing) with a snap point.

    ``words`` should already be refined (:func:`refine_word_boundaries`, as :func:`build_index` does);
    gaps then span exactly the word edges. ``refine=True`` refines internally first (the refined words
    are then not returned; prefer :func:`analyze_gaps`). Without audio, gaps come from word times only
    (snap at the midpoint, ``energy_db`` None).
    """
    if not words:
        return []
    if not _audio_available(job):
        from studio.jobs import Job

        dur = None
        if isinstance(job, Job):
            try:
                dur = job.load_media_info().duration_us
            except Exception:
                dur = None
        return _fallback_gaps(list(words), dur)
    feat = _features(job)
    ws = _refine(feat, list(words)) if refine else list(words)
    return _gaps(feat, ws)[0]


def build_gaps(audio_path: str | os.PathLike[str] | AudioFeatures, words: list[Word], *, refine: bool = True
               ) -> list[Gap]:
    """Gaps for an audio file and ASR words (refined internally by default)."""
    return analyze_gaps(audio_path, words, refine=refine).gaps


def analyze_gaps(source: Any, words: list[Word], *, refine: bool = True,
                 room_tone_min_ms: float = ROOM_TONE_MIN_US / 1000) -> GapAnalysis:
    """One pass: refined words, gaps, breaths and room-tone ranges."""
    feat = _features(source)
    ws = _refine(feat, list(words)) if refine else list(words)
    thr = _thresholds(feat, ws)
    gaps, breaths = _gaps(feat, ws, thr)
    rt = _room_tone(feat, gaps, int(room_tone_min_ms * 1000), thr)
    return GapAnalysis(words=ws, gaps=gaps, breaths=breaths, room_tone_ranges_us=rt)


def find_breaths(source: Any, gaps: list[Gap] | None = None) -> list[Breath]:
    """Detected breaths (with levels, for target-mode breath attenuation). With ``gaps`` only breaths
    inside gaps are returned (tagged with the gap id); otherwise every breath in the recording."""
    feat = _features(source)
    if gaps is None:
        return [_breath_from_run(feat, a, b) for a, b in feat.breath_runs]
    out: list[Breath] = []
    for g in gaps:
        k0, k1 = feat.frame_ceil(g.start_us), feat.frame_floor(g.end_us)
        for a, b in feat.breath_runs:
            ca, cb = max(a, k0), min(b, k1 + 1)
            if cb - ca >= max(2, int(0.08 / HOP_S)):
                out.append(_breath_from_run(feat, ca, cb, g.id))
    return out


def room_tone_ranges(source: Any, gaps: list[Gap] | None = None, *, min_ms: float = ROOM_TONE_MIN_US / 1000
                     ) -> list[tuple[int, int]]:
    """Quietest stationary non-speech stretches ≥ ``min_ms`` (inside ``gaps`` when given, else anywhere
    VAD says non-speech), sorted by time, never digital silence."""
    feat = _features(source)
    return _room_tone(feat, gaps, int(min_ms * 1000))
