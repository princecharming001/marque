"""Voice chain: measure first, then process the **full source dialogue once** before any cut.

Why the whole source, once
--------------------------
Every seam in the edit joins two pieces of the same recording. If each piece were processed on its own,
the compressor, leveler and denoiser would start from different states and the timbre and noise floor
would jump at every cut, which reads as an edit even when the words flow (doctrine principle 4). So
:func:`process_dialogue` runs the chain over ``media/audio.wav`` end to end, caches the result under
``media/voice/`` keyed by the spec, and the compiler (:mod:`studio.compile.audio`) cuts from that
processed track. Room tone is built from the same processed track, so fills match the floor exactly.

Stages (each one must be called for by a measurement; :func:`plan_voice_chain` records why)
-------------------------------------------------------------------------------------------
1. **Restoration** (``spec.denoise``):

   * ``"none"`` — clean audio (SNR ≥ ~20 dB) or audio the phone already isolated (floor near digital
     silence): nothing.
   * ``"light"`` — a decision-directed Wiener filter (Ephraim–Malah style a-priori SNR, 43 ms STFT,
     75 % overlap) with the noise PSD taken from the take's own pauses and a hard **attenuation limit
     of 12 dB**, so residual room noise stays natural and there is no "underwater" artefact.
   * ``"isolate"`` — ElevenLabs Voice Isolator (``POST /v1/audio-isolation``, multipart ``audio``,
     ``file_format=other``, ``xi-api-key`` header; verified against the official SDK and API reference
     and with a real call, Sep 2026). Measured on a real take, the isolator is *generative*: its output
     has ~zero waveform coherence with the input and shifts voice bands by up to +10 dB. So the result is
     (1) aligned on multi-band **onsets** (waveform cross-correlation is only trusted when the output is
     coherent; a waveform peak on the real take was 29 ms off, an energy-envelope estimate 1.2 ms early
     because isolation strips reverb tails — the onsets agreed within a sample); (2) **timbre-matched**
     back to the original speaker's noise-subtracted speech spectrum with a bounded (±9 dB),
     reliability-weighted, octave-smoothed zero-phase FIR (real take: residual within ±1.7 dB per band);
     (3) level-matched on speech; (4) blended with the original at −18 dB (never unlimited suppression).
     A **guard** hook (e.g. :func:`make_wer_guard`: re-transcription WER must not get worse) can veto
     it, falling back to ``"light"``; without a key the chain also falls back to ``"light"``.
     Responses are cached per source under ``media/voice/`` so re-planning never re-calls the API.

2. **Breath attenuation** (``breath_atten_db``): loud breaths in the index's breath gaps are pulled
   toward ~18 dB below the neighbouring speech (never more than ``breath_atten_db``), and the removed
   power is replaced with the take's own room tone so the floor does not dip under the breath.
3. **High-pass**: 2nd-order Butterworth (12 dB/oct, the doctrine slope; pedalboard's HPF is only
   6 dB/oct), corner adapted to the speaker's 5th-percentile F0 and to measured boom/rumble.
4. **Corrective EQ** (``spec.eq``; pedalboard peak/shelf biquads): hum notches, a wide 2–4 dB mud cut
   only where the (noise-subtracted, octave-smoothed) long-term speech spectrum shows a 200–450 Hz bump
   against the long-term average speech spectrum shape, a small presence lift only when 2–5 kHz is
   measurably dull. When restoration is planned, these decisions are measured on a light-denoised proxy
   of the restored voice (the EQ acts after restoration; room noise or music must not read as timbre).
5. **Declick** (self-gating, no spec field): isolated non-speech transients (knocks, bumps, sharp mouth
   clicks) standing ≥ 16 dB over max(robust local envelope, phrase RMS) for < 8 ms are pulled to 12 dB
   with a smooth gain dip, before the dynamics stages can lift them (measured on a real take: two knocks
   ~20 dB over the phrase had forced 9 dB of master limiting; consonant bursts are never candidates).
6. **Leveler** (``spec.leveler``): slow gain rider over speech (±3 dB, ~0.8 s window, zero-phase
   smoothing, held through pauses so the floor never pumps). It runs *before* the compressor so the
   compressor treats every phrase alike (doctrine "gain rides on words that fall away, compression").
7. **Compressor**: soft-knee (6 dB) feed-forward RMS compressor, ratio ``comp_ratio``, attack 10 ms,
   release 120 ms, 3 ms look-ahead (offline). The threshold is *calibrated on the recording* so the
   90th-percentile speech level gets ``compression_db`` of gain reduction: typical words get less,
   emphasised words about the target — consistency without flattening the performance.
8. **De-esser**: pedalboard has none, so this is a split-band dynamic de-esser. The sibilance band is
   centred on the measured sibilance peak (default 5–9 kHz), extracted with a zero-phase band-pass so
   ``x - (1-g)·band`` is exactly transparent when ``g = 1``; gain reduction follows the band's level
   relative to the rest of the spectrum (fast attack, 25 ms release) and is capped at ``deess_db``.

Everything is float64 internally; outputs are float32 in the input's shape.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from scipy import signal as sps
from scipy.ndimage import uniform_filter1d

from studio.doc.model import EqBand, VoiceChainSpec

if TYPE_CHECKING:  # pragma: no cover
    from studio.config import Settings
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = [
    "CHAIN_VERSION",
    "SR",
    "VoiceChainResult",
    "IsolationError",
    "Guard",
    "load_audio",
    "write_audio",
    "measure_voice",
    "plan_voice_chain",
    "apply_voice_chain",
    "apply_voice_chain_detailed",
    "apply_voice_chain_file",
    "process_dialogue",
    "room_tone",
    "build_room_tone",
    "isolate_elevenlabs",
    "make_wer_guard",
    "speech_mask",
    "energy_vad",
    "frame_levels_db",
    "noise_floor_db",
    "highpass",
    "apply_eq",
    "leveler",
    "compress",
    "deess",
    "denoise_wiener",
    "tame_transients",
    "LTASS_BANDS_HZ",
    "LTASS_DB",
]

CHAIN_VERSION = "1"
SR = 48_000

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"
ISOLATION_PATH = "/v1/audio-isolation"
ISOLATION_RESIDUAL_DB = -18.0  # residual mixed back after isolation (attenuation limit 18 dB)
LIGHT_DENOISE_LIMIT_DB = 12.0

# Long-term average speech spectrum, 1/3-octave band levels (dB), after Byrne et al. (1994), male+female
# average. Only its *shape* is used (as a prior to detect local bumps/dips in a recording's spectrum).
LTASS_BANDS_HZ: tuple[float, ...] = (
    63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000,
    5000, 6300, 8000, 10000, 12500, 16000,
)
LTASS_DB: tuple[float, ...] = (
    38.6, 43.5, 54.4, 57.7, 56.8, 60.2, 60.3, 59.0, 62.1, 62.1, 60.5, 56.8, 53.7, 53.0, 52.0, 48.7, 48.1,
    46.8, 45.6, 44.5, 44.3, 43.7, 43.4, 41.3, 40.7,
)

_EPS = 1e-20

#: (original, processed, sr) -> bool or {"accept": bool, ...}
Guard = Callable[[np.ndarray, np.ndarray, int], "bool | Mapping[str, Any]"]


class IsolationError(RuntimeError):
    """The isolation provider failed (no key, HTTP error, undecodable response)."""


@dataclass
class VoiceChainResult:
    """What :func:`apply_voice_chain_detailed` did (audio in the input's shape, float32)."""

    audio: np.ndarray
    sr: int
    applied: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"sr": self.sr, "applied": self.applied, "warnings": self.warnings}


# ============================================================================================== I/O
def load_audio(path: str | os.PathLike[str], sr: int = SR, *, mono: bool = False) -> tuple[np.ndarray, int]:
    """Read a WAV/FLAC (soundfile) or anything ffmpeg can decode; resample to ``sr`` with soxr VHQ.

    Returns ``(x, sr)`` with ``x`` float64 shaped ``(n,)`` for mono or ``(ch, n)`` otherwise.
    """
    import soundfile as sf

    p = Path(path)
    try:
        data, file_sr = sf.read(str(p), dtype="float64", always_2d=True)
        x = data.T  # (ch, n)
    except Exception:
        x, file_sr = _ffmpeg_decode(p, sr), sr
    if file_sr != sr:
        import soxr

        x = soxr.resample(x.T, file_sr, sr, quality="VHQ").T
    if mono or x.shape[0] == 1:
        return np.ascontiguousarray(x.mean(axis=0)), sr
    return np.ascontiguousarray(x), sr


def _ffmpeg_decode(path: Path, sr: int, channels: int | None = None) -> np.ndarray:
    """Decode any media file's first audio stream to float64 ``(ch, n)`` at ``sr``."""
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    probe_ch = channels
    if probe_ch is None:
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                                  "stream=channels", "-of", "csv=p=0", str(path)], capture_output=True,
                                 text=True, check=True).stdout.strip()
            probe_ch = max(1, int(out.splitlines()[0])) if out else 1
        except Exception:
            probe_ch = 1
    probe_ch = min(probe_ch, 2)
    cmd = [ffmpeg, "-v", "error", "-i", str(path), "-vn", "-map", "0:a:0", "-ac", str(probe_ch), "-ar", str(sr),
           "-f", "f32le", "-"]
    res = subprocess.run(cmd, capture_output=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode audio from {path.name}: "
                           f"{res.stderr.decode(errors='replace')[-300:]}")
    raw = np.frombuffer(res.stdout, dtype="<f4").astype(np.float64)
    n = len(raw) // probe_ch
    return raw[: n * probe_ch].reshape(n, probe_ch).T


def _decode_bytes(data: bytes, sr: int) -> np.ndarray:
    """Decode an in-memory audio file (any container ffmpeg reads) to mono float64 at ``sr``."""
    import soundfile as sf

    try:
        arr, file_sr = sf.read(io.BytesIO(data), dtype="float64", always_2d=True)
        x = arr.mean(axis=1)
        if file_sr != sr:
            import soxr

            x = soxr.resample(x, file_sr, sr, quality="VHQ")
        return x
    except Exception:
        pass
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    res = subprocess.run([ffmpeg, "-v", "error", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le",
                          "-"], input=data, capture_output=True, check=False)
    if res.returncode != 0 or not res.stdout:
        raise IsolationError("could not decode the isolation response audio")
    return np.frombuffer(res.stdout, dtype="<f4").astype(np.float64)


def write_audio(path: str | os.PathLike[str], x: np.ndarray, sr: int = SR, *, subtype: str = "FLOAT") -> Path:
    """Write ``(n,)`` or ``(ch, n)`` audio as a WAV (32-bit float by default), atomically."""
    import soundfile as sf

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(x, dtype=np.float64)
    data = arr if arr.ndim == 1 else arr.T
    fd, tmp = tempfile.mkstemp(prefix=f".{p.stem}.", suffix=".wav", dir=p.parent)
    os.close(fd)
    try:
        sf.write(tmp, data.astype(np.float32), sr, subtype=subtype, format="WAV")
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return p


def _as_2d(audio: np.ndarray) -> tuple[np.ndarray, bool]:
    a = np.asarray(audio, dtype=np.float64)
    if a.ndim == 1:
        return a[None, :].copy(), True
    if a.ndim == 2:
        if a.shape[0] > 8 and a.shape[1] <= 8:  # (n, ch) given — be forgiving
            return a.T.copy(), False
        return a.copy(), False
    raise ValueError(f"audio must be (n,) or (ch, n), got shape {a.shape}")


# ============================================================================================== analysis
def frame_levels_db(x: np.ndarray, sr: int, win_s: float = 0.02, hop_s: float = 0.01) -> tuple[np.ndarray, np.ndarray]:
    """RMS level (dBFS) of frames of ``win_s`` every ``hop_s``. Returns ``(centres_in_samples, db)``."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = np.sqrt(np.mean(x**2, axis=0))
    n = len(x)
    win = max(1, round(win_s * sr))
    hop = max(1, round(hop_s * sr))
    if n < win:
        x = np.pad(x, (0, win - n))
        n = win
    cs = np.concatenate(([0.0], np.cumsum(x * x)))
    starts = np.arange(0, n - win + 1, hop)
    p = (cs[starts + win] - cs[starts]) / win
    return starts + win / 2.0, 10.0 * np.log10(np.maximum(p, _EPS))


def _rms_db(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return -200.0
    return float(10.0 * np.log10(max(float(np.mean(x * x)), _EPS)))


def _intervals_to_mask(n: int, intervals: Iterable[tuple[int, int]]) -> np.ndarray:
    m = np.zeros(n, dtype=bool)
    for a, b in intervals:
        a, b = max(0, int(a)), min(n, int(b))
        if b > a:
            m[a:b] = True
    return m


def _mask_to_intervals(mask: np.ndarray) -> list[tuple[int, int]]:
    if mask.size == 0:
        return []
    d = np.diff(np.concatenate(([0], mask.astype(np.int8), [0])))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def speech_mask(index: TakeIndex | None, n: int, sr: int, *, pad_s: float = 0.02,
                audio: np.ndarray | None = None) -> np.ndarray:
    """Boolean per-sample mask of speech (index words except audio events, padded), or an energy VAD
    on ``audio`` when there is no index / no words."""
    if index is not None and index.words:
        pad = round(pad_s * sr)
        iv = [(w.start_us * sr // 1_000_000 - pad, -(-w.end_us * sr // 1_000_000) + pad)
              for w in index.words if w.kind != "event"]
        m = _intervals_to_mask(n, iv)
        if m.any():
            return m
    if audio is None:
        return np.zeros(n, dtype=bool)
    return energy_vad(audio, sr)


#: margin kept around a measured breath span (the breath detector works on 10 ms frames), never past the gap
BREATH_MARGIN_US = 20_000


def breath_intervals(index: TakeIndex | None, n: int, sr: int) -> list[tuple[int, int, str]]:
    """``[(start, end, gap_id)]`` in samples for the breaths in the index's gaps.

    Uses the measured breath spans (``Gap.breaths_us``) widened by :data:`BREATH_MARGIN_US` inside the gap,
    so the gain never touches a word's decay tail or the pause around the inhale; a gap flagged as a breath
    without measured spans (hand-built indexes) falls back to the whole gap."""
    if index is None:
        return []
    out = []
    for g in index.gaps:
        if not (g.has_breath or g.kind == "breath"):
            continue
        spans = [(max(g.start_us, a - BREATH_MARGIN_US), min(g.end_us, b + BREATH_MARGIN_US))
                 for a, b in g.breaths_us] or [(g.start_us, g.end_us)]
        for s_us, e_us in spans:
            a = s_us * sr // 1_000_000
            b = -(-e_us * sr // 1_000_000)
            if b - a > round(0.03 * sr):
                out.append((max(0, a), min(n, b), g.id))
    return out


def noise_floor_db(x: np.ndarray, sr: int, mask: np.ndarray | None = None) -> float:
    """Noise floor estimate (dBFS RMS): 10th percentile of 20 ms frame levels (non-speech frames when a
    speech ``mask`` is given), ignoring frames of digital silence."""
    c, db = frame_levels_db(x, sr, 0.02, 0.01)
    keep = db > -150.0
    if mask is not None and mask.size:
        idx = np.clip(c.astype(int), 0, mask.size - 1)
        ns = keep & ~mask[idx]
        if ns.sum() >= 10:
            keep = ns
    vals = db[keep]
    if vals.size == 0:
        return -200.0
    return float(np.percentile(vals, 10))


def energy_vad(x: np.ndarray, sr: int, *, above_floor_db: float = 12.0, hang_s: float = 0.1) -> np.ndarray:
    """Energy VAD fallback: frames more than ``above_floor_db`` over the floor, with hangover."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=0)
    n = len(x)
    c, db = frame_levels_db(x, sr, 0.02, 0.01)
    floor = noise_floor_db(x, sr)
    act = db > max(floor + above_floor_db, -70.0)
    hang = max(1, round(hang_s / 0.01))
    act = uniform_filter1d(act.astype(float), size=2 * hang + 1, mode="nearest") > 0
    mask = np.zeros(n, dtype=bool)
    hop = round(0.01 * sr)
    for i in np.flatnonzero(act):
        s = int(c[i] - hop)
        mask[max(0, s): min(n, s + 2 * hop)] = True
    return mask


def _band_levels(freqs: np.ndarray, psd: np.ndarray, centres: Sequence[float]) -> np.ndarray:
    out = np.full(len(centres), np.nan)
    for i, fc in enumerate(centres):
        lo, hi = fc / 2 ** (1 / 6), fc * 2 ** (1 / 6)
        sel = (freqs >= lo) & (freqs < hi)
        if sel.any():
            out[i] = 10 * np.log10(max(float(np.sum(psd[sel])), _EPS))
    return out


def _octave_smooth(levels_db: np.ndarray) -> np.ndarray:
    p = 10 ** (np.nan_to_num(levels_db, nan=-200.0) / 10)
    padded = np.concatenate(([p[0]], p, [p[-1]]))
    sm = padded[:-2] + padded[1:-1] + padded[2:]
    out = 10 * np.log10(np.maximum(sm, _EPS))
    out[np.isnan(levels_db)] = np.nan
    return out


def _concat_regions(x: np.ndarray, mask: np.ndarray, max_s: float, sr: int) -> np.ndarray:
    ivs = _mask_to_intervals(mask)
    parts, total = [], 0
    for a, b in ivs:
        parts.append(x[a:b])
        total += b - a
        if total >= max_s * sr:
            break
    return np.concatenate(parts) if parts else np.zeros(0)


# ============================================================================================== measure
def measure_voice(job: Job | None, index: TakeIndex | None, *, audio: np.ndarray | None = None,
                  sr: int | None = None) -> dict[str, Any]:
    """Spectral/level measurements that drive the chain (advice for the Director).

    Reads ``job.audio_path`` unless ``audio`` (``(n,)`` or ``(ch, n)``) and ``sr`` are given. Keys:
    ``speech_level_db``, ``noise_floor_db``, ``snr_db`` (own) and ``snr_db_index``, ``f0_p5_hz``,
    ``f0_median_hz``, ``ltas`` (1/3-octave residual vs LTASS), ``mud``, ``presence``, ``boom_db``,
    ``rumble_db``, ``hum``, ``sibilance``, ``breaths``, ``clipping_ratio``, ``isolation_detected``,
    ``music_in_room``.
    """
    if audio is None:
        if job is None:
            raise ValueError("measure_voice needs a job or audio")
        audio, sr = load_audio(job.audio_path, SR)
    sr = int(sr or SR)
    x2, _ = _as_2d(audio)
    x = x2.mean(axis=0)
    n = len(x)
    m = speech_mask(index, n, sr, audio=x)
    out: dict[str, Any] = {"sr": sr, "duration_s": n / sr, "chain_version": CHAIN_VERSION}

    c, db = frame_levels_db(x, sr, 0.02, 0.01)
    idx = np.clip(c.astype(int), 0, n - 1)
    sp_frames = db[m[idx]] if m.any() else db
    floor = noise_floor_db(x, sr, m)
    loud_frames = sp_frames[sp_frames > floor + 6]
    if loud_frames.size:
        speech_level = float(np.median(loud_frames))
    elif sp_frames.size:
        speech_level = float(np.median(sp_frames))
    else:
        speech_level = -200.0
    out["noise_floor_db"] = round(floor, 2)
    out["speech_level_db"] = round(speech_level, 2)
    out["snr_db"] = round(speech_level - floor, 2)
    out["snr_db_index"] = index.audio.snr_db if index is not None and index.audio is not None else None
    out["music_in_room"] = bool(index.audio.music_in_room) if index is not None and index.audio is not None else False
    out["isolation_detected"] = bool(floor < -80.0 and speech_level > -45.0)
    out["clipping_ratio"] = float(np.mean(np.abs(x[m]) >= 0.999)) if m.any() else float(np.mean(np.abs(x) >= 0.999))

    # --- long-term spectra
    sp = _concat_regions(x, m, 120.0, sr)
    ns = _concat_regions(x, ~m, 60.0, sr)
    ltas: dict[str, Any] = {}
    if sp.size >= 4096:
        nper = 8192 if sp.size >= 8192 else 4096
        f, P = sps.welch(sp, fs=sr, nperseg=nper)
        if ns.size >= nper:  # subtract the pause (noise) spectrum: stationary noise must not read as timbre
            _fn, PN = sps.welch(ns, fs=sr, nperseg=nper)
            P = np.maximum(P - PN, 0.1 * P)
        # octave-smoothed (power sum of 3 adjacent third-octave bands) for measurement and reference alike,
        # so a band that falls between two harmonics of a low voice does not read as a dip
        bands = _octave_smooth(_band_levels(f, P, LTASS_BANDS_HZ))
        ref = _octave_smooth(np.asarray(LTASS_DB, dtype=np.float64))
        align = [i for i, fc in enumerate(LTASS_BANDS_HZ) if 500 <= fc <= 4000]
        offset = np.nanmean(bands[align] - ref[align])
        resid = bands - ref - offset
        ltas = {str(int(fc)): (None if np.isnan(r) else round(float(r), 2)) for fc, r in zip(LTASS_BANDS_HZ, resid,
                                                                                               strict=True)}

        def rmean(lo: float, hi: float) -> float:
            sel = [i for i, fc in enumerate(LTASS_BANDS_HZ) if lo <= fc <= hi and not np.isnan(resid[i])]
            return float(np.mean(resid[sel])) if sel else 0.0

        mud_idx = [i for i, fc in enumerate(LTASS_BANDS_HZ) if 200 <= fc <= 450]
        k = max(mud_idx, key=lambda i: -np.inf if np.isnan(resid[i]) else resid[i])
        neigh = (rmean(120, 165) + rmean(620, 1000)) / 2
        bump = float(resid[k] - neigh)
        out["mud"] = {"freq_hz": float(LTASS_BANDS_HZ[k]), "residual_db": round(float(resid[k]), 2),
                      "bump_db": round(bump, 2)}
        out["presence"] = {"deficit_db": round(rmean(2000, 5000) - rmean(500, 1250), 2)}
        out["boom_db"] = round(rmean(80, 160) - rmean(250, 1000), 2)
    else:
        out["mud"] = None
        out["presence"] = None
        out["boom_db"] = None
    out["ltas_residual_db"] = ltas

    # --- noise spectrum: rumble + hum
    out["rumble_db"] = None
    out["hum"] = None
    if ns.size >= 8192:
        nper = 1 << int(min(16, math.floor(math.log2(ns.size))))
        fN, PN = sps.welch(ns, fs=sr, nperseg=nper)
        lf = (fN >= 20) & (fN < 60)
        mid = (fN >= 200) & (fN < 1000)
        if lf.any() and mid.any():
            # power per octave (pink room noise is flat per octave, so only a real LF excess counts)
            p_lf = float(np.sum(PN[lf])) / math.log2(60 / 20)
            p_mid = float(np.sum(PN[mid])) / math.log2(1000 / 200)
            out["rumble_db"] = round(10 * math.log10((p_lf + _EPS) / (p_mid + _EPS)), 2)
        out["hum"] = _detect_hum(fN, PN)

    # --- F0 (parselmouth autocorrelation pitch) on speech
    out["f0_p5_hz"] = None
    out["f0_median_hz"] = None
    if sp.size >= sr // 2:
        try:
            import parselmouth

            seg = sp[: int(90 * sr)]
            snd = parselmouth.Sound(seg.astype(np.float64), sampling_frequency=sr)
            pitch = snd.to_pitch_ac(time_step=0.01, pitch_floor=60.0, pitch_ceiling=500.0)
            f0 = pitch.selected_array["frequency"]
            f0 = f0[f0 > 0]
            if f0.size >= 20:
                out["f0_p5_hz"] = round(float(np.percentile(f0, 5)), 1)
                out["f0_median_hz"] = round(float(np.median(f0)), 1)
        except Exception as e:  # pragma: no cover - parselmouth failure is advisory only
            out["f0_error"] = type(e).__name__

    out["sibilance"] = _measure_sibilance(x, sr, m, floor)
    out["breaths"] = _measure_breaths(x, sr, index, m)
    return out


def _detect_hum(f: np.ndarray, P: np.ndarray) -> dict[str, Any] | None:
    """Mains hum: peaks at k·50 or k·60 Hz standing ≥ 10 dB over their ±8 Hz neighbourhood median."""
    best: dict[str, Any] | None = None
    Pdb = 10 * np.log10(P + _EPS)
    for base in (50.0, 60.0):
        harmonics = []
        for k in range(1, 9):
            fk = base * k
            if fk > f[-1]:
                break
            sel = (f >= fk - 2.0) & (f <= fk + 2.0)
            nb = ((f >= fk - 10) & (f < fk - 4)) | ((f > fk + 4) & (f <= fk + 10))
            if not sel.any() or not nb.any():
                continue
            prom = float(np.max(Pdb[sel]) - np.median(Pdb[nb]))
            if prom >= 10.0:
                harmonics.append({"freq_hz": fk, "prominence_db": round(prom, 1)})
        if len(harmonics) >= 2 and (best is None or len(harmonics) > len(best["harmonics"])):
            best = {"base_hz": base, "harmonics": harmonics}
    return best


def _measure_sibilance(x: np.ndarray, sr: int, mask: np.ndarray, floor_db: float) -> dict[str, Any] | None:
    if x.size < 4096 or not mask.any():
        return None
    nfft, hop = 1024, 256
    f, _t, Z = sps.stft(x, fs=sr, nperseg=nfft, noverlap=nfft - hop, boundary=None, padded=False)
    P = np.abs(Z) ** 2
    centres = (np.arange(P.shape[1]) * hop + nfft // 2).clip(0, x.size - 1)
    active = mask[centres]
    hf = (f >= 4000) & (f <= 10000)
    lf = (f >= 100) & (f < 4000)
    band = (f >= 5000) & (f <= 9000)
    Phf = P[hf].sum(axis=0)
    Plf = P[lf].sum(axis=0)
    tot = P.sum(axis=0)
    # scipy's one-sided, window-sum-scaled STFT: sum |Z|^2 over bins ~= 0.75 x mean square (Hann)
    lvl = 10 * np.log10(tot / 0.75 + _EPS)
    R = 10 * np.log10((Phf + _EPS) / (Plf + _EPS))
    loud = active & (lvl > floor_db + 20)
    sib = loud & (R > 0)
    voiced = loud & (R < -10)
    if sib.sum() < 5 or voiced.sum() < 5:
        return {"peak_hz": None, "ratio_db": None, "sibilant_frames": int(sib.sum())}
    spec = P[:, sib].mean(axis=1)
    sel = (f >= 4000) & (f <= 10000)
    peak_hz = float(f[sel][np.argmax(uniform_filter1d(spec[sel], 5))])
    band_lvl = 10 * np.log10(P[band][:, sib].sum(axis=0) / 0.75 + _EPS)
    ratio = float(np.percentile(band_lvl, 95) - np.median(lvl[voiced]))
    return {"peak_hz": round(peak_hz, 0), "ratio_db": round(ratio, 2), "sibilant_frames": int(sib.sum())}


def _measure_breaths(x: np.ndarray, sr: int, index: TakeIndex | None, mask: np.ndarray) -> list[dict[str, Any]]:
    out = []
    n = x.size
    for a, b, gid in breath_intervals(index, n, sr):
        _c, db = frame_levels_db(x[a:b], sr, 0.02, 0.01)
        peak = float(np.max(db)) if db.size else -200.0
        ctx = np.zeros(n, dtype=bool)
        ctx[max(0, a - sr): min(n, b + sr)] = True
        near = ctx & mask
        sp = _concat_regions(x, near, 3.0, sr)
        speech = _rms_db(sp) if sp.size else -200.0
        out.append({"gap_id": gid, "peak_db": round(peak, 2), "speech_db": round(speech, 2),
                    "rel_db": round(peak - speech, 2)})
    return out


# ============================================================================================== plan
def plan_voice_chain(job: Job | None, index: TakeIndex | None, *, audio: np.ndarray | None = None,
                     sr: int | None = None, measurements: Mapping[str, Any] | None = None) -> VoiceChainSpec:
    """A measured starting chain for ``set_voice_chain``. Clean audio gets only a gentle chain
    (HPF, light compression, the leveler); every other stage names the measurement that called for it
    in ``notes``."""
    if measurements is None and audio is None and job is not None:
        audio, sr = load_audio(job.audio_path, SR, mono=True)
    m = dict(measurements) if measurements is not None else measure_voice(job, index, audio=audio, sr=sr)
    notes: list[str] = []
    snr = m.get("snr_db_index")
    snr_src = "index"
    if snr is None:
        snr, snr_src = m.get("snr_db"), "measured"
    snr = float(snr) if snr is not None else 30.0

    # --- restoration
    denoise, provider = "none", None
    if m.get("isolation_detected"):
        notes.append(f"denoise none: floor {m.get('noise_floor_db')} dBFS is near digital silence "
                     "(phone voice isolation already ran)")
    elif m.get("music_in_room"):
        denoise, provider = "isolate", "elevenlabs"
        notes.append("isolate: music playing in the room")
    elif snr < 12.0:
        denoise, provider = "isolate", "elevenlabs"
        notes.append(f"isolate: SNR {snr:.1f} dB ({snr_src}) < 12")
    elif snr < 20.0:
        denoise = "light"
        notes.append(f"light denoise (limit {LIGHT_DENOISE_LIMIT_DB:.0f} dB): SNR {snr:.1f} dB ({snr_src}) in 12-20")
    else:
        notes.append(f"no denoise: SNR {snr:.1f} dB ({snr_src}) >= 20")

    # --- spectrum for HPF/EQ/de-ess decisions: these stages act after restoration, so when restoration is
    # planned, measure on a light-denoised proxy of the restored voice rather than on the noisy original
    m_eq = m
    if denoise != "none" and audio is not None:
        a2, _ = _as_2d(audio)
        mono = a2.mean(axis=0)
        r_sr = int(sr or SR)
        proxy, _ = denoise_wiener(mono, r_sr, noise_mask=~speech_mask(index, mono.size, r_sr, audio=mono))
        m_eq = {**m, **{k: v for k, v in measure_voice(None, index, audio=proxy, sr=r_sr).items()
                        if k in ("mud", "presence", "boom_db", "sibilance", "ltas_residual_db")}}
        notes.append("EQ measured on a light-denoised proxy of the restored voice")

    # --- high-pass
    f0p5 = m.get("f0_p5_hz")
    boom = m_eq.get("boom_db")
    rumble = m.get("rumble_db")
    base = 80.0
    why = "default 80 Hz"
    if boom is not None and boom > 10.0:  # LTASS is a male+female average: normal male voices read ~+7
        base, why = 110.0, f"boomy low end (+{boom:.1f} dB vs LTASS)"
    elif rumble is not None and rumble > 10.0:
        base, why = 100.0, f"LF rumble in pauses (+{rumble:.1f} dB)"
    hpf = base
    if f0p5:
        cap = 0.9 * float(f0p5)
        if cap < hpf:
            hpf = cap
            why += f", capped below F0 p5 {f0p5:.0f} Hz"
    hpf = float(min(120.0, max(60.0, round(hpf))))
    notes.append(f"HPF {hpf:.0f} Hz 12 dB/oct: {why}")

    # --- corrective EQ
    eq: list[EqBand] = []
    hum = m.get("hum")
    if hum:
        for h in hum["harmonics"][:6]:
            if h["freq_hz"] >= hpf * 0.9 or h["freq_hz"] >= 90:
                eq.append(EqBand(type="peak", freq_hz=h["freq_hz"], gain_db=-min(18.0, round(h["prominence_db"], 1)),
                                 q=10.0))
        notes.append(f"hum notches at {hum['base_hz']:.0f} Hz family ({len(hum['harmonics'])} harmonics)")
    mud = m_eq.get("mud")
    if mud and mud["residual_db"] >= 3.5 and mud["bump_db"] >= 2.0:
        cut = float(min(4.0, max(2.0, mud["residual_db"] - 1.5)))
        eq.append(EqBand(type="peak", freq_hz=mud["freq_hz"], gain_db=-round(cut, 1), q=1.0))
        notes.append(f"mud cut -{cut:.1f} dB at {mud['freq_hz']:.0f} Hz: +{mud['residual_db']:.1f} dB over the speech "
                     f"spectrum shape (local bump +{mud['bump_db']:.1f} dB)")
    pres = m_eq.get("presence")
    if pres and pres["deficit_db"] < -6.0 and snr >= 18.0 and denoise != "isolate":
        boost = 3.0 if pres["deficit_db"] < -9.0 else 2.0
        eq.append(EqBand(type="peak", freq_hz=3500.0, gain_db=boost, q=0.8))
        notes.append(f"presence +{boost:.0f} dB at 3.5 kHz: 2-5 kHz {pres['deficit_db']:.1f} dB dull vs LTASS")

    # --- de-ess
    deess = 0.0
    sib = m_eq.get("sibilance") or {}
    ratio = sib.get("ratio_db")
    if ratio is not None:
        if ratio >= -6.0:
            deess = float(min(6.0, 3.0 + (ratio + 6.0) * 0.5))
        elif ratio >= -10.0:
            deess = 2.0
    if deess > 0:
        notes.append(f"de-ess up to {deess:.1f} dB around {sib.get('peak_hz')} Hz: sibilants {ratio:.1f} dB vs vowels")

    # --- breaths
    breath = 0.0
    loud = [b for b in m.get("breaths") or [] if b["rel_db"] > -15.0]
    if loud:
        worst = max(b["rel_db"] for b in loud)
        breath = float(min(12.0, max(6.0, worst + 18.0)))
        notes.append(f"breath attenuation up to {breath:.0f} dB: {len(loud)} breath(s) louder than -15 dB re speech "
                     f"(worst {worst:.1f})")

    # --- dynamics
    clean = snr >= 25.0 and denoise == "none"
    if clean:
        comp_db, ratio_c = 3.0, 2.0
        notes.append("clean recording: gentle compression 2:1, ~3 dB on loud words")
    else:
        comp_db, ratio_c = 4.0, 2.5
        notes.append("compression 2.5:1, ~4 dB on loud words")
    if m.get("clipping_ratio", 0.0) > 1e-4:
        notes.append(f"WARNING clipping in speech ({m['clipping_ratio']:.2e} of samples): prefer alternate takes")

    return VoiceChainSpec(
        enabled=True, denoise=denoise, isolation_provider=provider, hpf_hz=hpf, eq=eq, deess_db=round(deess, 1),
        compression_db=comp_db, comp_ratio=ratio_c, leveler=True, breath_atten_db=round(breath, 1),
        notes="; ".join(notes),
    )


# ============================================================================================== stages
def highpass(x: np.ndarray, sr: int, hz: float, order: int = 2) -> np.ndarray:
    """Causal Butterworth high-pass (12 dB/oct for ``order=2``; minimum phase like an analogue HPF)."""
    sos = sps.butter(order, hz, "highpass", fs=sr, output="sos")
    x2, was1 = _as_2d(x)
    y = sps.sosfilt(sos, x2, axis=1)
    return y[0] if was1 else y


def apply_eq(x: np.ndarray, sr: int, bands: Sequence[EqBand]) -> np.ndarray:
    """pedalboard peak/shelf biquads in series."""
    if not bands:
        return np.asarray(x, dtype=np.float64).copy()
    import pedalboard

    plugins = []
    for b in bands:
        cls = {"peak": pedalboard.PeakFilter, "low_shelf": pedalboard.LowShelfFilter,
               "high_shelf": pedalboard.HighShelfFilter}[b.type]
        f = min(float(b.freq_hz), sr * 0.45)
        plugins.append(cls(cutoff_frequency_hz=f, gain_db=float(b.gain_db), q=float(b.q)))
    board = pedalboard.Pedalboard(plugins)
    x2, was1 = _as_2d(x)
    y = board(x2.astype(np.float32), sr).astype(np.float64)
    return y[0] if was1 else y


def _frame_gain_to_samples(centres: np.ndarray, gain_db: np.ndarray, n: int, shift: float = 0.0) -> np.ndarray:
    return np.interp(np.arange(n, dtype=np.float64), centres - shift, gain_db)


def _smooth_ar(gr: np.ndarray, att_coef: float, rel_coef: float) -> np.ndarray:
    """Attack/release smoothing of a gain-reduction sequence (positive dB = more reduction)."""
    out = np.empty_like(gr)
    s = 0.0
    for i, v in enumerate(gr.tolist()):
        s = att_coef * s + (1 - att_coef) * v if v > s else rel_coef * s + (1 - rel_coef) * v
        out[i] = s
    return out


def leveler(x: np.ndarray, sr: int, mask: np.ndarray, *, range_db: float = 3.0, window_s: float = 0.8,
            smooth_s: float = 0.6, floor_db: float | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Slow speech-only gain rider. Returns ``(gain_db per sample, info)``.

    Level = mean power of the *body* of speech frames (within 15 dB of the median speech level, so
    soft attacks/decays do not read as a quiet phrase) over ``window_s`` (a word or two); gain pulls it
    toward the median speech level within ±``range_db``; pauses hold the interpolated gain (no floor
    pumping); the curve is smoothed zero-phase over ``smooth_s``.
    """
    mono = np.asarray(x, dtype=np.float64)
    if mono.ndim == 2:
        mono = mono.mean(axis=0)
    n = mono.size
    hop = 0.01
    c, db = frame_levels_db(mono, sr, 0.02, hop)
    idx = np.clip(c.astype(int), 0, n - 1)
    fl = noise_floor_db(mono, sr, mask) if floor_db is None else floor_db
    active = mask[idx] & (db > fl + 10.0)
    if active.sum() < 10:
        return np.zeros(n), {"applied": False, "reason": "too little speech"}
    body = active & (db > float(np.median(db[active])) - 15.0)
    if body.sum() >= 10:
        active = body
    p = 10 ** (db / 10)
    w = max(1, round(window_s / hop))
    num = uniform_filter1d(np.where(active, p, 0.0), size=w, mode="nearest")
    den = uniform_filter1d(active.astype(float), size=w, mode="nearest")
    lvl = 10 * np.log10(np.maximum(num / np.maximum(den, 1e-9), _EPS))
    target = float(np.median(lvl[active]))
    g = np.clip(target - lvl, -range_db, range_db)
    ai = np.flatnonzero(active)
    gfull = np.interp(np.arange(c.size), ai, g[ai])
    k = max(3, round(smooth_s / hop) | 1)
    win = np.hanning(k + 2)[1:-1]
    win /= win.sum()
    gs = np.convolve(np.pad(gfull, (k // 2, k // 2), mode="edge"), win, mode="valid")
    gs = np.clip(gs, -range_db, range_db)
    return _frame_gain_to_samples(c, gs, n), {"applied": True, "target_db": round(target, 2),
                                              "gain_range_db": [round(float(gs.min()), 2), round(float(gs.max()), 2)]}


def _soft_knee_gr(L: np.ndarray, T: float, ratio: float, knee: float) -> np.ndarray:
    s = 1.0 - 1.0 / ratio
    over = L - T
    gr = np.where(over > knee / 2, s * over, 0.0)
    inside = np.abs(over) <= knee / 2
    gr = np.where(inside, s * (over + knee / 2) ** 2 / (2 * knee), gr)
    return np.maximum(gr, 0.0)


def _calibrate_threshold(L_ref: float, target_gr: float, ratio: float, knee: float) -> float:
    s = 1.0 - 1.0 / ratio
    if target_gr <= 0 or s <= 0:
        return L_ref + 100.0
    if target_gr >= s * knee / 2:
        return L_ref - target_gr / s
    return L_ref + knee / 2 - math.sqrt(2 * knee * target_gr / s)


def compress(x: np.ndarray, sr: int, *, target_gr_db: float, ratio: float, mask: np.ndarray | None = None,
             knee_db: float = 6.0, attack_ms: float = 10.0, release_ms: float = 120.0, lookahead_ms: float = 3.0,
             ref_percentile: float = 90.0) -> tuple[np.ndarray, dict[str, Any]]:
    """Soft-knee feed-forward RMS compressor (linked across channels), threshold calibrated so the
    ``ref_percentile`` speech level receives ``target_gr_db``. Returns ``(y, info)``."""
    x2, was1 = _as_2d(x)
    mono_pow = np.mean(x2**2, axis=0)
    n = mono_pow.size
    hop = max(1, round(0.001 * sr))
    win = max(hop, round(0.005 * sr))
    cs = np.concatenate(([0.0], np.cumsum(mono_pow)))
    starts = np.arange(0, max(1, n - win + 1), hop)
    p = (cs[np.minimum(starts + win, n)] - cs[starts]) / win
    L = 10 * np.log10(np.maximum(p, _EPS))
    centres = starts + win / 2
    fl = noise_floor_db(np.sqrt(mono_pow), sr, mask)
    idx = np.clip(centres.astype(int), 0, n - 1)
    sel = fl + 10.0 < L
    if mask is not None and mask.any():
        sel &= mask[idx]
    if sel.sum() < 20:
        return (x2[0] if was1 else x2).copy(), {"applied": False, "reason": "too little speech"}
    L_ref = float(np.percentile(L[sel], ref_percentile))
    T = _calibrate_threshold(L_ref, target_gr_db, ratio, knee_db)
    gr = _soft_knee_gr(L, T, ratio, knee_db)
    fs_frames = sr / hop
    att = math.exp(-1.0 / (attack_ms * 1e-3 * fs_frames))
    rel = math.exp(-1.0 / (release_ms * 1e-3 * fs_frames))
    grs = _smooth_ar(gr, att, rel)
    g = _frame_gain_to_samples(centres, -grs, n, shift=lookahead_ms * 1e-3 * sr)
    y = x2 * (10 ** (g / 20))[None, :]
    info = {"applied": True, "threshold_db": round(T, 2), "ref_level_db": round(L_ref, 2), "ratio": ratio,
            "knee_db": knee_db, "attack_ms": attack_ms, "release_ms": release_ms,
            "gr_p50_db": round(float(np.percentile(grs[sel], 50)), 2),
            "gr_p90_db": round(float(np.percentile(grs[sel], 90)), 2),
            "gr_max_db": round(float(grs.max()), 2)}
    return (y[0] if was1 else y), info


def deess(x: np.ndarray, sr: int, *, max_db: float, centre_hz: float | None = None, mask: np.ndarray | None = None,
          release_ms: float = 25.0) -> tuple[np.ndarray, dict[str, Any]]:
    """Split-band dynamic de-esser (zero-phase band split: transparent when inactive)."""
    x2, was1 = _as_2d(x)
    if max_db <= 0:
        return (x2[0] if was1 else x2).copy(), {"applied": False}
    fc = float(centre_hz) if centre_hz else 6700.0
    fc = min(max(fc, 4500.0), 9500.0)
    lo, hi = max(3500.0, fc / 1.35), min(sr * 0.45, fc * 1.35)
    sos = sps.butter(4, [lo, hi], "bandpass", fs=sr, output="sos")
    band = sps.sosfiltfilt(sos, x2, axis=1)
    rest = x2 - band
    mono_b = np.mean(band**2, axis=0)
    mono_r = np.mean(rest**2, axis=0)
    n = mono_b.size
    hop = max(1, round(0.001 * sr))
    win = max(hop, round(0.003 * sr))
    cb = np.concatenate(([0.0], np.cumsum(mono_b)))
    cr = np.concatenate(([0.0], np.cumsum(mono_r)))
    starts = np.arange(0, max(1, n - win + 1), hop)
    e = np.minimum(starts + win, n)
    Lb = 10 * np.log10(np.maximum((cb[e] - cb[starts]) / win, _EPS))
    Lr = 10 * np.log10(np.maximum((cr[e] - cr[starts]) / win, _EPS))
    Lt = 10 * np.log10(np.maximum((cb[e] - cb[starts] + cr[e] - cr[starts]) / win, _EPS))
    R = Lb - Lr
    centres = starts + win / 2
    fl = noise_floor_db(np.sqrt(mono_b + mono_r), sr, mask)
    active = Lt > fl + 15.0
    if mask is not None and mask.any():
        active &= mask[np.clip(centres.astype(int), 0, n - 1)]
    if active.sum() < 10:
        return (x2[0] if was1 else x2).copy(), {"applied": False, "reason": "no active speech"}
    theta = max(-3.0, float(np.median(R[active])) + 15.0)
    gr = np.where(active, np.clip((R - theta) * 0.75, 0.0, max_db), 0.0)
    rel = math.exp(-1.0 / (release_ms * 1e-3 * (sr / hop)))
    grs = _smooth_ar(gr, 0.0, rel)
    g_db = _frame_gain_to_samples(centres, -grs, n, shift=0.001 * sr)
    g = 10 ** (g_db / 20)
    y = x2 - (1.0 - g)[None, :] * band
    return (y[0] if was1 else y), {"applied": True, "band_hz": [round(lo), round(hi)],
                                   "threshold_rel_db": round(theta, 2),
                                   "max_db": max_db, "gr_max_db": round(float(grs.max()), 2),
                                   "active_ratio": round(float(np.mean(grs > 0.5)), 4)}


def tame_transients(x: np.ndarray, sr: int, *, crest_db: float = 16.0, target_db: float = 12.0,
                    max_len_ms: float = 8.0, ramp_ms: float = 1.5) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Low-sensitivity declick for isolated non-speech transients (knocks, mic bumps, sharp mouth clicks).

    Reference level = max(robust local envelope — rolling median of 2.5 ms frame RMS over ~50 ms, so a
    click cannot inflate its own reference — and the ±250 ms phrase RMS), so a stop-consonant burst after a
    silent closure (no louder than the phrase's vowels) is never a candidate. A transient whose 1 ms peak
    envelope stands ≥ ``crest_db`` over that reference and stays > ``crest_db − 6`` dB for less than
    ``max_len_ms`` is pulled down to ``target_db`` with a smooth gain dip (``ramp_ms`` raised-cosine
    ramps) — gain only, nothing is interpolated or removed. Runs before the leveler/compressor (doctrine:
    declick at low sensitivity, before compression lifts the clicks) and does nothing on a take without
    such events. Measured on a real take: two knocks at ~20 dB forced 9 dB of master limiting."""
    from scipy.ndimage import maximum_filter1d, median_filter

    x2, was1 = _as_2d(x)
    mag = np.max(np.abs(x2), axis=0)
    mono = x2.mean(axis=0)
    n = mag.size
    hop = max(1, round(0.0025 * sr))
    nf = n // hop
    if nf < 25:
        return (x2[0] if was1 else x2).copy(), []
    fr = np.sqrt(np.mean((mono[: nf * hop] ** 2).reshape(nf, hop), axis=1))
    env_med = np.interp(np.arange(n), np.arange(nf) * hop + hop / 2, median_filter(fr, size=21, mode="nearest"))
    env_phr = np.sqrt(np.maximum(uniform_filter1d(mono * mono, max(1, round(0.5 * sr)), mode="nearest"), 0.0))
    ref = np.maximum(np.maximum(env_med, env_phr), 10 ** (-90 / 20))
    pk = maximum_filter1d(mag, size=max(1, round(0.001 * sr)), mode="nearest")
    crest = pk / ref
    hi = crest > 10 ** (crest_db / 20)
    if not hi.any():
        return (x2[0] if was1 else x2).copy(), []
    near_iv = _mask_to_intervals(crest > 10 ** ((crest_db - 6) / 20))
    gain = np.ones(n)
    ramp = max(2, round(ramp_ms * 1e-3 * sr))
    maxlen = round(max_len_ms * 1e-3 * sr)
    tgt = 10 ** (target_db / 20)
    events = []
    for s, e in near_iv:
        if not hi[s:e].any() or e - s > maxlen:
            continue
        g = min(1.0, float(np.min(tgt * ref[s:e] / np.maximum(mag[s:e], 1e-12))))
        if g >= 0.99:
            continue
        a0, b0 = max(0, s - ramp), min(n, e + ramp)
        w = np.ones(b0 - a0)
        r1, r2 = s - a0, b0 - e
        if r1 > 0:
            w[:r1] = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, r1))
        if r2 > 0:
            w[-r2:] = 0.5 + 0.5 * np.cos(np.linspace(0, np.pi, r2))
        gain[a0:b0] = np.minimum(gain[a0:b0], 1.0 - (1.0 - g) * w)
        events.append({"t_s": round(s / sr, 4), "len_ms": round((e - s) / sr * 1000, 2),
                       "crest_db": round(float(20 * np.log10(np.max(crest[s:e]))), 1),
                       "reduction_db": round(-20 * math.log10(g), 1)})
    y = x2 * gain[None, :]
    return (y[0] if was1 else y), events


def denoise_wiener(x: np.ndarray, sr: int, *, limit_db: float = LIGHT_DENOISE_LIMIT_DB,
                   noise_mask: np.ndarray | None = None, alpha: float = 0.98) -> tuple[np.ndarray, dict[str, Any]]:
    """Decision-directed Wiener suppression with an attenuation floor (``limit_db``). Zero delay."""
    x2, was1 = _as_2d(x)
    nfft, hop = 2048, 512
    n = x2.shape[1]
    ys = []
    info: dict[str, Any] = {"applied": True, "limit_db": limit_db}
    gmin = 10 ** (-limit_db / 20)
    for ch in range(x2.shape[0]):
        f, t, Z = sps.stft(x2[ch], fs=sr, window="hann", nperseg=nfft, noverlap=nfft - hop, boundary="zeros",
                           padded=True)
        P = np.abs(Z) ** 2
        frame_pos = np.clip((t * sr).astype(int), 0, n - 1)
        if noise_mask is not None and noise_mask.any() and (noise_mask[frame_pos]).sum() >= 10:
            nf = noise_mask[frame_pos]
        else:
            e = P.sum(axis=0)
            nf = e <= np.percentile(e, 10)
        N = np.median(P[:, nf], axis=1) * 1.2 + _EPS  # median underestimates the mean of |X|^2
        G = np.ones_like(P)
        prev = P[:, 0] * 0
        for k in range(P.shape[1]):
            gamma = P[:, k] / N
            xi = alpha * prev / N + (1 - alpha) * np.maximum(gamma - 1.0, 0.0)
            g = xi / (1.0 + xi)
            g = np.maximum(g, gmin)
            G[:, k] = g
            prev = (g**2) * P[:, k]
        G = uniform_filter1d(G, size=3, axis=0, mode="nearest")
        G = np.maximum(G, gmin)
        _t2, y = sps.istft(G * Z, fs=sr, window="hann", nperseg=nfft, noverlap=nfft - hop, boundary=True)
        y = y[:n] if y.size >= n else np.pad(y, (0, n - y.size))
        ys.append(y)
        info.setdefault("mean_gain_db", []).append(round(float(20 * np.log10(np.mean(G) + _EPS)), 2))
    y2 = np.vstack(ys)
    return (y2[0] if was1 else y2), info


def breath_attenuation(x: np.ndarray, sr: int, index: TakeIndex | None, mask: np.ndarray, *, max_db: float,
                       tone: np.ndarray | None, target_rel_db: float = -18.0) -> tuple[np.ndarray, list[dict]]:
    """Pull loud breaths toward ``target_rel_db`` re neighbouring speech (≤ ``max_db``) and back-fill the
    removed power with room tone so the floor stays continuous."""
    x2, was1 = _as_2d(x)
    y = x2.copy()
    mono = x2.mean(axis=0)
    n = mono.size
    log = []
    ramp = max(8, round(0.015 * sr))
    for a, b, gid in breath_intervals(index, n, sr):
        _c, db = frame_levels_db(mono[a:b], sr, 0.02, 0.01)
        peak = float(np.max(db)) if db.size else -200.0
        ctx = np.zeros(n, dtype=bool)
        ctx[max(0, a - sr): min(n, b + sr)] = True
        sp = _concat_regions(mono, ctx & mask, 3.0, sr)
        if sp.size < sr // 10:
            continue
        rel = peak - _rms_db(sp)
        att = min(max_db, max(0.0, rel - target_rel_db))
        if att < 0.5:
            log.append({"gap_id": gid, "rel_db": round(rel, 2), "atten_db": 0.0})
            continue
        L = b - a
        env = np.ones(L)
        r = min(ramp, L // 3)
        if r > 0:
            env[:r] = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, r))
            env[-r:] = env[:r][::-1]
        g = 1.0 - (1.0 - 10 ** (-att / 20)) * env
        y[:, a:b] *= g[None, :]
        if tone is not None and tone.size:
            fill = np.sqrt(np.maximum(0.0, 1.0 - g**2))
            seg = np.resize(tone, L)
            y[:, a:b] += (fill * seg)[None, :]
        log.append({"gap_id": gid, "rel_db": round(rel, 2), "atten_db": round(att, 2)})
    return (y[0] if was1 else y), log


# ============================================================================================== isolation
def _xcorr_norm(a: np.ndarray, b: np.ndarray, L: int) -> tuple[np.ndarray, np.ndarray]:
    """Normalized cross-correlation of ``b`` against ``a`` for lags ``-L..L`` (positive = b is late)."""
    a = a - a.mean()
    b = b - b.mean()
    m = min(a.size, b.size)
    a, b = a[:m], b[:m]
    nfft = 1 << math.ceil(math.log2(2 * m))
    xc = np.fft.irfft(np.fft.rfft(b, nfft) * np.conj(np.fft.rfft(a, nfft)), nfft)
    den = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b))) or 1.0
    lags = np.concatenate((np.arange(-L, 0), np.arange(0, L + 1)))
    vals = np.concatenate((xc[-L:], xc[: L + 1])) / den
    return lags, vals


def _parabolic_peak(lags: np.ndarray, v: np.ndarray, i: int) -> float:
    lag = float(lags[i])
    if 0 < i < v.size - 1:
        den = v[i - 1] - 2 * v[i] + v[i + 1]
        if den < 0:
            lag += 0.5 * (v[i - 1] - v[i + 1]) / den
    return lag


_ONSET_EDGES_HZ = np.geomspace(200.0, 6000.0, 9)


def _onset_strength(x: np.ndarray, sr: int, hop: int) -> np.ndarray:
    """Multi-band onset strength (sum over 8 log-spaced bands of the rectified rise of the log energy
    envelope, 2 ms RMS, ``hop``-sample frames)."""
    acc = None
    w = max(8, round(0.002 * sr))
    for lo, hi in itertools.pairwise(_ONSET_EDGES_HZ):
        b = sps.sosfiltfilt(sps.butter(3, [lo, hi], "bandpass", fs=sr, output="sos"), x)
        e = np.sqrt(np.maximum(uniform_filter1d(b * b, w), 0.0))[::hop]
        le = np.log(e + 1e-3 * (float(e.max()) or 1.0) + 1e-12)
        o = np.maximum(np.diff(le, prepend=le[:1]), 0.0)
        acc = o if acc is None else acc + o
    return acc if acc is not None else np.zeros(0)


def _align_lag(ref: np.ndarray, sig: np.ndarray, sr: int, max_lag_s: float = 0.25) -> dict[str, Any]:
    """Offset of a restoration output ``sig`` against ``ref`` (first ≤ 30 s). Returns
    ``{"lag": int (positive = sig is late), "coherent": bool, "polarity": ±1, "score": float, "method": str}``.

    * Masking-type processors keep the waveform: the normalized 200–4000 Hz waveform cross-correlation
      peaks near ±1 and gives a sample-exact lag and the polarity.
    * Generative isolators do not (ElevenLabs, measured Sep 2026 on a real take: coherence ≈ 0 in every
      band; the waveform peak is weak and 29 ms off). For those the lag comes from **multi-band onset
      strength** (0.5 ms frames, parabolic refinement): lip sync is judged on onsets, and isolation also
      removes reverb tails, which drags an energy-envelope estimate early (−1.2 ms on that take while the
      onsets were aligned within a sample). The envelope lag is kept as a coarse sanity check and as the
      fallback when onsets are too weak to correlate.
    """
    m = min(ref.size, sig.size, 30 * sr)
    sos = sps.butter(4, [200.0, 4000.0], "bandpass", fs=sr, output="sos")
    a = sps.sosfiltfilt(sos, ref[:m])
    b = sps.sosfiltfilt(sos, sig[:m])
    L = round(max_lag_s * sr)
    lags, v = _xcorr_norm(a, b, L)
    i = int(np.argmax(np.abs(v)))
    if abs(v[i]) >= 0.5:
        return {"lag": int(lags[i]), "coherent": True, "polarity": 1 if v[i] > 0 else -1,
                "score": round(float(v[i]), 3), "method": "waveform"}
    w = max(8, round(0.002 * sr))
    ea = np.sqrt(np.maximum(uniform_filter1d(a * a, w), 0.0))
    eb = np.sqrt(np.maximum(uniform_filter1d(b * b, w), 0.0))
    lags_e, ve = _xcorr_norm(ea, eb, L)
    ie = int(np.argmax(ve))
    env_lag = _parabolic_peak(lags_e, ve, ie)
    hop = max(1, round(0.0005 * sr))
    oa, ob = _onset_strength(ref[:m], sr, hop), _onset_strength(sig[:m], sr, hop)
    if oa.size > 16 and ob.size > 16:
        lags_o, vo = _xcorr_norm(oa, ob, round(max_lag_s * sr / hop))
        io = int(np.argmax(vo))
        far = np.abs(lags_o - lags_o[io]) > round(0.01 * sr / hop)
        runner_up = float(vo[far].max()) if far.any() else 0.0
        prominence = float(vo[io]) / runner_up if runner_up > 0 else float("inf")
        # related signals measured 1.8-3.0 (real isolator output 1.82), unrelated ones 1.0-1.3
        if vo[io] > 0 and prominence >= 1.4:
            on_lag = _parabolic_peak(lags_o, vo, io) * hop
            return {"lag": round(on_lag), "coherent": False, "polarity": 1, "score": round(float(vo[io]), 3),
                    "method": "onset", "prominence": round(prominence, 2), "envelope_lag": round(env_lag)}
    return {"lag": round(env_lag), "coherent": False, "polarity": 1, "score": round(float(ve[ie]), 3),
            "method": "envelope"}


def _speech_psd(x: np.ndarray, sr: int, mask: np.ndarray, *, nper: int = 4096
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Noise-subtracted speech PSD (speech frames minus pause frames) and the pause PSD (or None)."""
    sp = _concat_regions(x, mask, 120.0, sr) if mask.any() else x
    ns = _concat_regions(x, ~mask, 60.0, sr) if mask.any() else np.zeros(0)
    f, P = sps.welch(sp, fs=sr, nperseg=min(nper, max(256, sp.size)))
    N = None
    if ns.size >= nper:
        _f, N = sps.welch(ns, fs=sr, nperseg=nper)
        P = np.maximum(P - N, 0.1 * P)
    return f, P, N


def _band_power_db(f: np.ndarray, P: np.ndarray, grid: np.ndarray, width_oct: float = 1 / 3) -> np.ndarray:
    """Power integrated over ``width_oct``-wide bands centred on ``grid`` (dB). Integrating power *before*
    taking any ratio keeps harmonic (peaky) and smeared spectra comparable."""
    cs = np.concatenate(([0.0], np.cumsum(P)))
    lo = np.searchsorted(f, grid * 2 ** (-width_oct / 2))
    hi = np.maximum(np.searchsorted(f, grid * 2 ** (width_oct / 2)), lo + 1)
    hi = np.minimum(hi, P.size)
    lo = np.minimum(lo, hi - 1)
    return 10 * np.log10(np.maximum((cs[hi] - cs[lo]) / (hi - lo), _EPS))


def _log_smooth(v: np.ndarray, grid: np.ndarray, octaves: float = 1.0) -> np.ndarray:
    k = max(3, round(octaves * grid.size / math.log2(grid[-1] / grid[0])) | 1)
    win = np.hanning(k + 2)[1:-1]
    return np.convolve(np.pad(v, (k // 2, k // 2), mode="edge"), win / win.sum(), mode="valid")


def _match_ltas(x: np.ndarray, target: np.ndarray, sr: int, mask: np.ndarray, *, max_db: float = 9.0,
                lo_hz: float = 80.0, hi_hz: float = 12000.0, taps: int = 2049) -> tuple[np.ndarray, dict[str, Any]]:
    """Bounded, octave-smoothed EQ that moves ``x``'s speech spectrum back toward ``target``'s (both
    noise-subtracted), applied zero-phase (symmetric FIR, centred). Each frequency's correction is weighted
    by how reliably the target's speech is measured there (its speech-to-pause ratio: none below 3 dB, full
    above 12 dB), so a band where the original is noise-dominated is never "corrected" toward noise. Keeps a
    restored voice sounding like the person (doctrine: same person, same room; the measured ElevenLabs
    isolator shifted voice bands by up to +10 dB)."""
    f, Px, _ = _speech_psd(x, sr, mask)
    _f, Pt, Nt = _speech_psd(target, sr, mask)
    grid = np.geomspace(40.0, sr / 2 * 0.98, 256)
    bt = _band_power_db(f, Pt, grid)
    r = _log_smooth(bt - _band_power_db(f, Px, grid), grid, 0.5)
    if Nt is not None:
        snr = _log_smooth(bt - _band_power_db(f, Nt, grid), grid, 0.5)
        r *= np.clip((snr - 3.0) / 9.0, 0.0, 1.0)
    r = np.clip(r, -max_db, max_db)
    taper = np.clip(np.minimum((grid - lo_hz * 0.7) / (lo_hz * 0.3), (hi_hz * 1.3 - grid) / (hi_hz * 0.3)), 0, 1)
    r *= taper
    freqs = np.concatenate(([0.0], grid, [sr / 2]))
    gains = 10 ** (np.concatenate(([0.0], r, [0.0])) / 20)
    h = sps.firwin2(taps, freqs, gains, fs=sr)
    y = sps.oaconvolve(x, h, mode="same")
    at = np.interp(np.log([fc for fc in LTASS_BANDS_HZ if 100 <= fc <= 12500]), np.log(grid), r)
    return y, {"timbre_match_db": [round(float(r.min()), 2), round(float(r.max()), 2)],
               "timbre_correction_db": {str(int(fc)): round(float(v), 2) for fc, v in
                                        zip([fc for fc in LTASS_BANDS_HZ if 100 <= fc <= 12500], at, strict=True)}}


def isolate_elevenlabs(audio: np.ndarray, sr: int, *, settings: Settings | None = None, api_key: str | None = None,
                       client: Any = None, base_url: str = ELEVENLABS_BASE_URL, timeout: float = 900.0) -> np.ndarray:
    """ElevenLabs Voice Isolator on mono ``audio``; returns the isolated track at ``sr`` (not yet aligned).

    ``POST {base_url}/v1/audio-isolation`` with multipart ``audio`` (24-bit FLAC, lossless) and
    ``file_format=other``; auth header ``xi-api-key``. The response body is the isolated audio file,
    decoded with soundfile/ffmpeg whatever its container. Keys are never logged.
    """
    import httpx
    import soundfile as sf

    key = api_key
    if key is None:
        from studio.config import get_settings

        key = (settings or get_settings()).key("elevenlabs")
    if not key:
        raise IsolationError("elevenlabs key not configured (set ELEVENLABS_API_KEY or STUDIO_ENV_FILE)")
    mono = np.asarray(audio, dtype=np.float64)
    if mono.ndim == 2:
        mono = mono.mean(axis=0)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    scale = 0.95 / peak if peak > 0.95 else 1.0
    buf = io.BytesIO()
    sf.write(buf, (mono * scale).astype(np.float32), sr, format="FLAC", subtype="PCM_24")
    files = {"audio": ("dialogue.flac", buf.getvalue(), "audio/flac")}
    data = {"file_format": "other"}
    headers = {"xi-api-key": key, "accept": "*/*"}
    own = client is None
    cl = client if client is not None else httpx.Client(timeout=timeout)
    try:
        resp = cl.post(base_url.rstrip("/") + ISOLATION_PATH, files=files, data=data, headers=headers)
    except httpx.HTTPError as e:
        raise IsolationError(f"isolation request failed: {type(e).__name__}") from None
    finally:
        if own:
            cl.close()
    if resp.status_code >= 400:
        detail = resp.text[:300] if resp.headers.get("content-type", "").startswith("application/json") else ""
        raise IsolationError(f"isolation HTTP {resp.status_code} {detail}")
    y = _decode_bytes(resp.content, sr)
    return y / scale


def _integrate_isolation(orig: np.ndarray, iso: np.ndarray, sr: int, mask: np.ndarray,
                         residual_db: float = ISOLATION_RESIDUAL_DB, *, match_timbre: bool = True
                         ) -> tuple[np.ndarray, dict[str, Any]]:
    """Align (waveform or envelope, see :func:`_align_lag`), fix polarity, trim/pad, match the speech
    timbre back to the original (bounded ±6 dB), level-match on speech and mix the original back in at
    ``residual_db`` (the attenuation limit: the room never drops more than that)."""
    al = _align_lag(orig, iso, sr)
    lag = al["lag"]
    if lag > 0:
        iso = iso[lag:]
    elif lag < 0:
        iso = np.concatenate((np.zeros(-lag), iso))
    n = orig.size
    iso = (iso[:n] if iso.size >= n else np.pad(iso, (0, n - iso.size))) * al["polarity"]
    info: dict[str, Any] = {"lag_samples": lag, "coherent": al["coherent"], "polarity": al["polarity"],
                            "align_method": al["method"], "align_score": al["score"], "residual_db": residual_db}
    if match_timbre and n >= sr:
        iso, tinfo = _match_ltas(iso, orig, sr, mask)
        info.update(tinfo)
    sel = mask if mask.any() else np.ones(n, dtype=bool)
    g_db = float(np.clip(_rms_db(orig[sel]) - _rms_db(iso[sel]), -12.0, 12.0))
    iso = iso * 10 ** (g_db / 20)
    info["gain_match_db"] = round(g_db, 2)
    g = 10 ** (residual_db / 20)
    # coherent output: orig - iso is the removed noise; incoherent: this is a (1-g)/g blend with the original
    y = iso + g * (orig - iso)
    return y, info


def make_wer_guard(transcribe: Callable[[np.ndarray, int], str], reference_text: str, *,
                   max_wer_delta: float = 0.01) -> Guard:
    """Guard for restoration stages: re-transcribe both versions and reject the processed one if its WER
    against ``reference_text`` is more than ``max_wer_delta`` worse (doctrine: +1 point max)."""
    import jiwer

    def guard(original: np.ndarray, processed: np.ndarray, sr: int) -> dict[str, Any]:
        ref = reference_text.strip().lower()
        w0 = float(jiwer.wer(ref, transcribe(original, sr).strip().lower()))
        w1 = float(jiwer.wer(ref, transcribe(processed, sr).strip().lower()))
        return {"accept": w1 <= w0 + max_wer_delta, "wer_original": round(w0, 4), "wer_processed": round(w1, 4)}

    return guard


def _guard_accepts(res: bool | Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    if isinstance(res, Mapping):
        return bool(res.get("accept", False)), dict(res)
    return bool(res), {"accept": bool(res)}


# ============================================================================================== chain
def apply_voice_chain(audio: Any, sr: int, spec: VoiceChainSpec, **kwargs: Any) -> Any:
    """Process a float32 numpy array (``(n,)`` or ``(ch, n)``) and return the same shape.

    Keyword options are those of :func:`apply_voice_chain_detailed` (``index``, ``settings``,
    ``isolator``, ``guard``, ``preview``)."""
    return apply_voice_chain_detailed(audio, sr, spec, **kwargs).audio


def apply_voice_chain_detailed(audio: Any, sr: int, spec: VoiceChainSpec, *, index: TakeIndex | None = None,
                               settings: Settings | None = None,
                               isolator: Callable[[np.ndarray, int], np.ndarray] | None = None,
                               guard: Guard | None = None, preview: bool = False,
                               allow_network: bool = True) -> VoiceChainResult:
    """Run the chain (see module docstring for order and rationale) and report what each stage did."""
    x2, was1 = _as_2d(audio)
    in_shape = np.asarray(audio).shape
    res = VoiceChainResult(audio=np.zeros(0, np.float32), sr=sr)
    if not spec.enabled or x2.shape[1] == 0:
        res.audio = np.asarray(audio, dtype=np.float32).reshape(in_shape).copy()
        res.applied.append({"stage": "bypass"})
        return res
    n = x2.shape[1]
    mono = x2.mean(axis=0)
    mask = speech_mask(index, n, sr, audio=mono)
    y = x2

    # 1 — restoration
    mode = spec.denoise
    if mode == "isolate":
        orig = y.mean(axis=0)
        try:
            if isolator is not None:
                iso_raw = isolator(orig, sr)
            elif preview or not allow_network:
                raise IsolationError("isolation skipped (preview/offline)")
            else:
                provider = (spec.isolation_provider or "elevenlabs").lower()
                if provider != "elevenlabs":
                    raise IsolationError(f"isolation provider {provider!r} not available")
                iso_raw = isolate_elevenlabs(orig, sr, settings=settings)
            iso, info = _integrate_isolation(orig, np.asarray(iso_raw, dtype=np.float64), sr, mask)
            accepted, ginfo = (True, {"accept": True, "guard": "none"}) if guard is None else \
                _guard_accepts(guard(orig, iso, sr))
            info["guard"] = ginfo
            if accepted:
                y = np.repeat(iso[None, :], y.shape[0], axis=0)
                res.applied.append({"stage": "isolate", "provider": spec.isolation_provider or "elevenlabs", **info})
                if guard is None:
                    res.warnings.append("isolation applied without a WER guard")
            else:
                res.warnings.append("isolation rejected by guard; falling back to light denoise")
                res.applied.append({"stage": "isolate", "rejected": True, **info})
                mode = "light"
        except IsolationError as e:
            res.warnings.append(f"isolation unavailable ({e}); falling back to light denoise")
            mode = "light"
    if mode == "light":
        y, info = denoise_wiener(y, sr, limit_db=LIGHT_DENOISE_LIMIT_DB, noise_mask=~mask if mask.any() else None)
        res.applied.append({"stage": "denoise_light", **info})

    # 2 — breaths (clip gain with room-tone back-fill, before dynamics react to them)
    if spec.breath_atten_db > 0 and index is not None:
        tone = build_room_tone(y.mean(axis=0), sr, index, min(5.0, n / sr), seed=1)
        y, blog = breath_attenuation(y, sr, index, mask, max_db=spec.breath_atten_db, tone=tone)
        res.applied.append({"stage": "breaths", "breaths": blog})

    # 3 — high-pass
    y = highpass(y, sr, spec.hpf_hz)
    res.applied.append({"stage": "highpass", "hz": spec.hpf_hz, "slope_db_oct": 12})

    # 4 — corrective EQ
    if spec.eq:
        y = apply_eq(y, sr, spec.eq)
        res.applied.append({"stage": "eq", "bands": [b.model_dump() for b in spec.eq]})

    # 4b — declick isolated transients (self-gating) before dynamics can lift them
    y, clicks = tame_transients(y, sr)
    if clicks:
        res.applied.append({"stage": "declick", "events": clicks})

    # 5 — leveler (gain rider)
    if spec.leveler:
        g_db, info = leveler(y, sr, mask)
        if info.get("applied"):
            y = y * (10 ** (g_db / 20))[None, :]
        res.applied.append({"stage": "leveler", **info})

    # 6 — compressor
    if spec.compression_db > 0 and spec.comp_ratio > 1.0:
        y, info = compress(y, sr, target_gr_db=spec.compression_db, ratio=spec.comp_ratio, mask=mask)
        res.applied.append({"stage": "compressor", **info})

    # 7 — de-esser
    if spec.deess_db > 0:
        sib = _measure_sibilance(y.mean(axis=0), sr, mask, noise_floor_db(y.mean(axis=0), sr, mask))
        y, info = deess(y, sr, max_db=spec.deess_db, centre_hz=(sib or {}).get("peak_hz"), mask=mask)
        res.applied.append({"stage": "deess", **info})

    if not np.all(np.isfinite(y)):
        raise FloatingPointError("voice chain produced non-finite samples")
    out = y[0] if was1 else y
    res.audio = out.astype(np.float32).reshape(in_shape)
    return res


def apply_voice_chain_file(in_wav: str | os.PathLike[str], spec: VoiceChainSpec, out_wav: str | os.PathLike[str], *,
                           sr: int = SR, **kwargs: Any) -> VoiceChainResult:
    """File form: read ``in_wav`` (resampled to ``sr``), run the chain, write 32-bit float ``out_wav``."""
    x, sr = load_audio(in_wav, sr)
    res = apply_voice_chain_detailed(x, sr, spec, **kwargs)
    write_audio(out_wav, res.audio, sr)
    return res


# ============================================================================================== room tone
def _room_tone_candidates(index: TakeIndex | None, n: int, sr: int) -> list[tuple[int, int]]:
    cands: list[tuple[int, int]] = []
    if index is not None:
        if index.audio is not None:
            cands += [(a * sr // 1_000_000, b * sr // 1_000_000) for a, b in index.audio.room_tone_ranges_us]
        for g in index.gaps:
            if g.kind in ("pause", "silence") and not g.has_breath and g.duration_us >= 150_000:
                cands.append((g.start_us * sr // 1_000_000, g.end_us * sr // 1_000_000))
    margin = round(0.04 * sr)
    words = []
    if index is not None:
        pad = round(0.02 * sr)
        words = [(w.start_us * sr // 1_000_000 - pad, -(-w.end_us * sr // 1_000_000) + pad) for w in index.words]
    wmask = _intervals_to_mask(n, words)
    cmask = _intervals_to_mask(n, [(a + margin, b - margin) for a, b in cands if b - a > 2 * margin])
    return [(a, b) for a, b in _mask_to_intervals(cmask & ~wmask) if b - a >= round(0.08 * sr)]


def _score_region(x: np.ndarray, sr: int) -> tuple[float, float, float]:
    _c, db = frame_levels_db(x, sr, 0.01, 0.005)
    med = float(np.median(db))
    iqr = float(np.percentile(db, 75) - np.percentile(db, 25))
    return med, iqr, float(np.max(db) - med)


def _quietest_windows(x: np.ndarray, sr: int, exclude: np.ndarray, win_s: float = 0.2,
                      k: int = 10) -> list[tuple[int, int]]:
    win = round(win_s * sr)
    if x.size < win:
        return [(0, x.size)] if x.size > 0 else []
    c, db = frame_levels_db(x, sr, win_s, win_s / 2)
    order = np.argsort(db)
    picked: list[tuple[int, int]] = []
    taken = np.zeros(x.size, dtype=bool)
    for i in order:
        s = int(c[i] - win / 2)
        e = s + win
        if s < 0 or e > x.size or exclude[s:e].any() or taken[s:e].any() or db[i] < -150:
            continue
        picked.append((s, e))
        taken[s:e] = True
        if len(picked) >= k:
            break
    return picked


def _ep_fades(m: int) -> tuple[np.ndarray, np.ndarray]:
    th = (np.arange(m) + 0.5) / m * (np.pi / 2)
    return np.sin(th), np.cos(th)


def build_room_tone(audio: np.ndarray, sr: int, index: TakeIndex | None, duration_s: float, *, seed: int = 0,
                    max_material_s: float = 4.0) -> np.ndarray:
    """Loopable room tone of ``duration_s`` from the take's own quiet, steady pauses.

    Candidates: the index's ``room_tone_ranges_us`` plus inner pause/silence gaps (not breaths), trimmed
    40 ms at both ends and never overlapping a word. Regions must be steady (IQR of 10 ms levels ≤ 6 dB,
    no transient > 10 dB) and near the floor; chosen chunks are gain-matched to their median level,
    joined with equal-power crossfades into a circular loop, and tiled with random rotations and
    reversals (deterministic ``seed``) so no period is audible. Returns float32 ``(N,)``; zeros when the
    take has no usable pause (never synthetic noise).
    """
    x = np.asarray(audio, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=0)
    N = max(0, round(duration_s * sr))
    if N == 0 or x.size == 0:
        return np.zeros(N, dtype=np.float32)
    n = x.size
    floor = noise_floor_db(x, sr)
    cands = _room_tone_candidates(index, n, sr)
    scored = []
    for a, b in cands:
        med, iqr, tr = _score_region(x[a:b], sr)
        scored.append((a, b, med, iqr, tr))
    good = [s for s in scored if s[3] <= 6.0 and s[4] <= 10.0 and s[2] <= floor + 8.0 and s[2] > -150]
    if not good:
        good = [s for s in scored if s[3] <= 10.0 and s[2] <= floor + 12.0 and s[2] > -150]
    if not good:
        excl = np.zeros(n, dtype=bool)
        if index is not None and index.words:
            pad = round(0.03 * sr)
            excl = _intervals_to_mask(n, [(w.start_us * sr // 1_000_000 - pad, -(-w.end_us * sr // 1_000_000) + pad)
                                          for w in index.words])
        for a, b in _quietest_windows(x, sr, excl):
            med, iqr, tr = _score_region(x[a:b], sr)
            if med > -150:
                good.append((a, b, med, iqr, tr))
    if not good:
        return np.zeros(N, dtype=np.float32)
    target = float(np.median([g[2] for g in good]))
    good = [g for g in good if abs(g[2] - target) <= 3.0] or good
    good.sort(key=lambda g: (g[3], -(g[1] - g[0])))
    chunks, total = [], 0
    for a, b, _med, _iqr, _tr in good:
        seg = x[a:b].copy()
        seg -= seg.mean()
        seg *= 10 ** ((target - _rms_db(seg)) / 20)
        chunks.append(seg)
        total += seg.size
        if total >= max_material_s * sr:
            break
    xf = round(0.03 * sr)
    loop = chunks[0]
    for c in chunks[1:]:
        m = min(xf, loop.size // 3, c.size // 3)
        if m < 8:
            loop = np.concatenate((loop, c))
            continue
        fin, fout = _ep_fades(m)
        loop = np.concatenate((loop[:-m], loop[-m:] * fout + c[:m] * fin, c[m:]))
    # circular loop
    m = min(round(0.05 * sr), loop.size // 4)
    if m >= 8:
        fin, fout = _ep_fades(m)
        circ = loop[: loop.size - m].copy()
        circ[:m] = loop[:m] * fin + loop[loop.size - m:] * fout
    else:
        circ = loop
    Lc = circ.size
    if Lc < 16:
        return np.zeros(N, dtype=np.float32)
    xt = min(round(0.05 * sr), Lc // 4)
    if xt < 8:
        xt = 0
    rng = np.random.default_rng(seed)
    out = np.zeros(N + Lc + xt + 1)
    pos, first = 0, True
    fin, fout = _ep_fades(xt) if xt >= 8 else (np.ones(0), np.ones(0))
    while pos < N:
        off = 0 if first else int(rng.integers(0, Lc))
        seg = circ[np.arange(off, off + Lc + xt) % Lc].copy()
        if not first and rng.random() < 0.5:
            seg = seg[::-1].copy()
        if xt >= 8:
            if not first:
                seg[:xt] *= fin
            seg[-xt:] *= fout
        out[pos: pos + seg.size] += seg
        pos += Lc
        first = False
    tone = out[:N]
    return tone.astype(np.float32)


def room_tone(job: Job, index: TakeIndex, duration_s: float, *, sr: int = SR, audio: np.ndarray | None = None,
              seed: int = 0) -> Any:
    """Loopable room tone of ``duration_s`` built from the take's own pauses (float32 numpy).

    Uses ``audio`` (e.g. the processed voice, so fills match the processed floor) or ``job.audio_path``."""
    if audio is None:
        audio, sr = load_audio(job.audio_path, sr, mono=True)
    return build_room_tone(audio, sr, index, duration_s, seed=seed)


# ============================================================================================== job-level
def _file_sig(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _index_sig(index: TakeIndex | None) -> str:
    if index is None:
        return "none"
    h = hashlib.sha256()
    for w in index.words:
        h.update(f"{w.id}:{w.start_us}:{w.end_us}:{w.kind};".encode())
    for g in index.gaps:
        h.update(f"{g.id}:{g.start_us}:{g.end_us}:{g.kind}:{int(g.has_breath)}:{g.breaths_us};".encode())
    if index.audio is not None:
        h.update(json.dumps(index.audio.room_tone_ranges_us).encode())
    return h.hexdigest()[:16]


def process_dialogue(job: Job, index: TakeIndex | None, spec: VoiceChainSpec, *, settings: Settings | None = None,
                     isolator: Callable[[np.ndarray, int], np.ndarray] | None = None, guard: Guard | None = None,
                     preview: bool = False, force: bool = False, sr: int = SR) -> Path:
    """Run the chain over the **whole** ``media/audio.wav`` once and cache it as
    ``media/voice/voice_<key>.wav`` (+ ``.json`` log). The key covers the spec, the chain version, the
    source file and the index; isolation responses are cached separately per source so re-planning the
    chain never re-calls the API."""
    src = job.audio_path
    if not src.exists():
        raise FileNotFoundError(f"job {job.id} has no media/audio.wav")
    key_src = json.dumps({"spec": spec.model_dump(mode="json"), "v": CHAIN_VERSION, "src": _file_sig(src),
                          "index": _index_sig(index), "sr": sr, "preview_iso": preview and spec.denoise == "isolate"},
                         sort_keys=True)
    key = hashlib.sha256(key_src.encode()).hexdigest()[:16]
    out_dir = job.media_dir / "voice"
    out = out_dir / f"voice_{key}.wav"
    log = out.with_suffix(".json")
    if out.exists() and log.exists() and not force:
        return out
    x, sr = load_audio(src, sr, mono=True)

    iso_fn = isolator
    if iso_fn is None and spec.denoise == "isolate" and (spec.isolation_provider or "elevenlabs") == "elevenlabs":
        cache = out_dir / f"isolated_elevenlabs_{hashlib.sha256(_file_sig(src).encode()).hexdigest()[:12]}.wav"

        def iso_fn(a: np.ndarray, r: int) -> np.ndarray:
            if cache.exists():
                return load_audio(cache, r, mono=True)[0]
            if preview:
                raise IsolationError("isolation not cached and preview render")
            y = isolate_elevenlabs(a, r, settings=settings)
            write_audio(cache, y, r)
            job.trace("audio_isolation", provider="elevenlabs", seconds=round(a.size / r, 2))
            return y

    res = apply_voice_chain_detailed(x, sr, spec, index=index, settings=settings, isolator=iso_fn, guard=guard,
                                     preview=preview)
    write_audio(out, res.audio, sr)
    from studio.jobs import write_json_atomic

    write_json_atomic(log, {"key": key, "spec": spec.model_dump(mode="json"), **res.to_json()})
    return out
