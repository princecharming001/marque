"""Music beds: generate (ElevenLabs Music) or import a licensed bed, analyse it, fit it to the locked cut
and duck it under speech. The bed is fitted *after* the speech cut and never moves a cut (music.md §5).

Sources (``MusicSpec.source``)
-----------------------------
* ``"elevenlabs"`` — a bespoke instrumental generated for this edit (:func:`generate_music`).
* ``"file"`` / ``"creator"`` / ``"library"`` — a bed the caller holds rights to, imported with an explicit
  :class:`~studio.doc.model.Licence` (:func:`import_music_file`). Catalogue music (Epidemic) plugs in here
  once a partnership key exists.
* ``"none"`` — no music (always valid; the no-music master is rendered anyway).

Every bed lives under ``assets/music/<asset_id>.wav`` with ``<asset_id>.licence.json`` (invariant 5) and
``<asset_id>.analysis.json`` and is registered in ``assets/registry`` so ops can reference it by ID.
Generated beds are cached by a hash of the exact API request, so re-renders never re-generate.

ElevenLabs Music (verified against the API reference, Sep 2026)
---------------------------------------------------------------
``POST /v1/music?output_format=pcm_48000`` with either ``prompt`` + ``music_length_ms`` (3–600 s) +
``force_instrumental``, or a ``composition_plan``. Models: ``music_v1`` (``sections``; the only model that
strictly respects section durations), ``music_v2`` and ``music_v2_5`` (``chunks``: ≤30, each 3–120 s,
``text`` = "[Section]" + inline "{directions}", ``positive_styles``/``negative_styles`` ≤50,
``context_adherence``). We default to **music_v2_5**, documented as the most advanced model (best audio
quality and prompt adherence), in **composition-plan mode**, because a plan lets the bed hold back through
the setup, lift on the payoff and write an explicit ending ("ends on a single final hit, no fade out"),
and because ``seed`` is only accepted with a plan. ``force_instrumental`` does not apply to plans, so every
chunk carries empty lyrics plus "vocals, lyrics, vocal chops, choir, humming…" as negative styles, and the
analysis measures the result with a voice detector instead of trusting the prompt. Output is requested
as ``pcm_48000``: lossless at the engine rate, so there is no codec loss and no resampling.

Live behaviour that shaped the plan (four music_v2_5 generations, Sep 2026): ``pcm_48000`` is interleaved
*stereo* s16le; the total length is honoured exactly; the stated BPM is held within 0.6%; section changes
land on the bar line nearest each chunk boundary; but an ending direction written *inside* a long chunk
resolves about two bars in and then rings out. So every plan is bar-aligned from the requested BPM and
closes with a dedicated [Ending] chunk that starts on the bar where the button must land (just after the
last word; measured landing error 1 ms), and the requested BPM is nudged (integer, within ±7% of the
energy prior) so the payoff→ending distance is a whole number of bars, putting the lift on the payoff.
Restrictive cues ("drums and bass only") live in chunk text, never in the first chunk's styles, which set
the tone for the whole piece (one early bed came back as a lone hit per beat with silence between). The
fitter still works from measured beats, so a miss costs a bar edit, never a wrong ending.

Licence: Eleven Music Model-Specific Terms (last updated 26 May 2026): paid self-serve plans (Creator and
up) allow all online and offline commercial use except film, TV, radio and studio games; the Free plan
requires attribution and Starter is limited. The account's plan tier is looked up
(``GET /v1/user/subscription``, best effort) and written into every licence record.

Analysis (librosa, ISC)
-----------------------
beat_this would track better but brings model weights (its README notes copyrighted training files) and
madmom's models are non-commercial, so analysis uses librosa with safeguards: octave correction of the
tempo against the requested BPM, strict (high-tightness) dynamic-programming beat tracking at a 5.8 ms
hop, a regularised beat grid for constant-tempo beds, downbeat phase from low-band onsets + harmonic
(chroma) change + energy rise with a small bar-one prior, a meter check (3 vs 4), Foote novelty on
beat-synchronous chroma/MFCC for section boundaries, energy "lifts", a Krumhansl–Kessler key estimate (so
tonal SFX can sit in key), and an ending classifier (``button`` / ``fade`` / ``cut`` / ``sustain``).

Fitting (:func:`fit`)
---------------------
Backtime first: the bed's musical ending (the detected button, or the downbeat where its fade begins) is
placed on the end anchor (the last word's onset), then the length is solved with bar-line edits only:
straight backtime, one forward skip of whole bars, or a repeated loop of whole bars (extension). Each
candidate is scored on seam similarity (beat-synchronous chroma/MFCC before and after both edit points),
phrase preservation (jumps by multiples of 4 bars), number of edits, intro kept, payoff hits landing on
downbeats or section lifts (±35 ms), downbeats landing on hard seams (free sync only), and any time
stretch. Edits crossfade over ~30 ms that *end* 5 ms before the incoming downbeat transient, so the new bar
attacks cleanly (equal-gain for correlated material, equal-power otherwise). A ≤3% Rubber Band stretch
(pedalboard, high-quality, crisp transients, latency-compensated) is used only when a downbeat entry must
be exact. Fades are raised-cosine; a natural button keeps its ring-out; a bed without a musical ending is
faded after the last word and the fit says so.

Ducking (:func:`ducking_envelope`, :func:`duck_bed`)
----------------------------------------------------
The duck is drawn from word times (look-ahead): the ramp starts ~180 ms before a word and reaches full
depth ~30 ms before it, holds through gaps shorter than 1.2 s (no pumping in breaths), and releases over
0.75 s into real gaps; ramps are raised-cosine in dB. While speech plays, a zero-phase 1–4 kHz dynamic dip
(default 3 dB) carves room for consonants. The bed level is set against speech loudness:
``level_lu_under_speech`` is the level *under* speech, so the un-ducked bed sits ``duck_db`` higher (the
swell in gaps, intro and outro).

Array conventions: audio is float32 ``(channels, n)`` at 48 kHz unless stated; mono helpers accept
``(n,)``. :func:`render_music_bed` returns ``(2, n)`` (stereo keeps the bed's width out of the centre
where the voice sits); pass ``mono=True`` for ``(n,)``.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import io
import json
import logging
import math
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import httpx
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from studio.config import MissingKeyError, Settings, get_settings, redact
from studio.doc.model import AssetRef, Licence, MusicSpec
from studio.timebase import SAMPLE_RATE, sample_index, to_fraction

if TYPE_CHECKING:  # pragma: no cover
    from studio.compile.models import Timeline, TimelineMusic
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

log = logging.getLogger(__name__)

__all__ = [
    # audio utils (shared with sfx)
    "as_2d", "to_mono", "to_channels", "resample", "load_audio", "write_wav", "db_to_gain", "gain_to_db",
    "peak_dbfs", "k_weight", "integrated_lufs", "window_loudness", "short_term_lufs_at", "max_momentary_lufs",
    "raised_cosine", "decode_elevenlabs_audio", "pcm16_to_array", "infer_pcm_channels", "sha256_file",
    # ElevenLabs
    "ElevenLabsError", "ElevenLabsHTTP", "ElevenLabsMusicClient", "MusicChunk", "MusicRequest",
    "build_music_request", "plan_chunks", "generate_music", "import_music_file", "music_licence",
    # analysis
    "MusicEnding", "MusicSection", "MusicAnalysis", "analyze_music", "load_music", "music_analysis_for",
    "candidate_score",
    # fit
    "FitPiece", "FitResult", "fit",
    # ducking
    "DuckParams", "detect_speech_spans", "speech_spans_from_timeline", "duck_amount", "ducking_envelope",
    "carve_speech_band", "duck_bed", "bed_gain_db", "loudness_over_spans",
    # scaffold API
    "find_music", "render_music_bed", "fit_music_for_timeline",
]

# ============================================================================================ constants
ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"
MUSIC_ENDPOINT = "/v1/music"
MUSIC_MODEL = "music_v2_5"
MUSIC_MODELS: tuple[str, ...] = ("music_v1", "music_v2", "music_v2_5")
MUSIC_OUTPUT_FORMAT = "pcm_48000"
MUSIC_MIN_MS, MUSIC_MAX_MS = 3_000, 600_000
CHUNK_MIN_MS, CHUNK_MAX_MS = 3_000, 120_000
MAX_CHUNKS = 30
MAX_STYLES = 50
#: Plan structure calibration (3 live music_v2_5 samples, Sep 2026): the model honours the total length
#: exactly and places section changes on the bar line nearest each chunk boundary, but an ending direction
#: written inside a long chunk resolves early (about two bars into it) and then rings out. So every plan is
#: bar-aligned from the requested BPM and ends with a dedicated [Ending] chunk that *starts* on the bar where
#: the button should land: at least ``ENDING_PREROLL_S`` after the region's end anchor, so the fitter only
#: trims the head (no bar edit) and the lift, planned on the payoff's bar, stays on the payoff.
ENDING_CHUNK_S = 3.5
ENDING_PREROLL_S = 0.3

MUSIC_TERMS_URL = "https://elevenlabs.io/eleven-music-model-specific-terms"
MUSIC_TERMS_VERSION = "Eleven Music Model-Specific Terms (last updated 26 May 2026)"
MUSIC_TERMS_SCOPE = ("Paid self-serve plans (Creator, Pro, Scale, Business): all online and offline commercial use "
                     "except film, TV, radio and studio games (Enterprise only). Free plan: attribution "
                     "('Eleven Music') required, limited commercial use; Starter: limited commercial use.")

VOCAL_NEGATIVES: tuple[str, ...] = ("vocals", "lyrics", "vocal chops", "choir", "humming", "spoken word",
                                    "singing", "vocal samples")
BED_NEGATIVES: tuple[str, ...] = ("lead melody", "busy lead line", "guitar solo", "big drops", "risers",
                                  "dramatic tempo changes", "distortion", "harsh hi-hats")
BED_USE_STYLES: tuple[str, ...] = ("instrumental only", "background music bed for a spoken-word video",
                                   "warm full arrangement that stays out of the way of a voice",
                                   "uncluttered midrange", "no lead melody", "steady consistent groove")

#: response headers worth keeping in a licence record (never auth headers)
SAFE_RESPONSE_HEADERS: tuple[str, ...] = ("request-id", "x-request-id", "song-id", "x-song-id",
                                          "history-item-id", "content-type", "character-cost")
_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

ANALYSIS_VERSION = 1
_ASR = 22050  # analysis sample rate
_HOP = 128  # 5.8 ms at 22.05 kHz

_KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
PITCH_NAMES: tuple[str, ...] = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), nd)


# ============================================================================================ audio utils
def as_2d(audio: Any) -> np.ndarray:
    """``(n,)`` → ``(1, n)``; ``(ch, n)`` unchanged (float32). ``(n, ch)`` with n ≫ ch is transposed."""
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 1:
        return a[None, :]
    if a.ndim != 2:
        raise ValueError(f"audio must be 1-D or 2-D, got shape {a.shape}")
    if a.shape[0] > 8 and a.shape[1] <= 8:
        a = a.T
    return a


def to_mono(audio: Any) -> np.ndarray:
    a = as_2d(audio)
    return a[0].copy() if a.shape[0] == 1 else a.mean(axis=0).astype(np.float32)


def to_channels(audio: Any, channels: int) -> np.ndarray:
    """Conform to ``channels`` (1 or 2): mono is duplicated, extra channels folded."""
    a = as_2d(audio)
    if a.shape[0] == channels:
        return a
    if channels == 1:
        return a.mean(axis=0, keepdims=True).astype(np.float32)
    if a.shape[0] == 1:
        return np.repeat(a, channels, axis=0)
    return a[:channels].copy()


def resample(audio: Any, sr_from: int, sr_to: int) -> np.ndarray:
    """High-quality (soxr VHQ) resampling of ``(ch, n)`` or ``(n,)``; returns the same rank."""
    if int(sr_from) == int(sr_to):
        return np.asarray(audio, dtype=np.float32)
    import soxr

    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 1:
        return soxr.resample(a, sr_from, sr_to, quality="VHQ").astype(np.float32)
    return soxr.resample(a.T, sr_from, sr_to, quality="VHQ").T.astype(np.float32).copy()


def _ffmpeg_decode(path: Path, sr: int, channels: int) -> np.ndarray:
    ff = shutil.which(get_settings().ffmpeg) or "ffmpeg"
    cmd = [ff, "-v", "error", "-nostdin", "-i", str(path), "-vn", "-map", "0:a:0", "-ac", str(channels),
           "-af", "aresample=resampler=soxr:precision=28", "-ar", str(sr), "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    out = subprocess.run(cmd, check=True, capture_output=True).stdout
    data = np.frombuffer(out, dtype=np.float32)
    return data.reshape(-1, channels).T.copy()


def load_audio(path: str | os.PathLike[str], *, sr: int = SAMPLE_RATE, channels: int | None = 2) -> np.ndarray:
    """Decode any audio file to float32 ``(channels, n)`` at ``sr`` (soundfile + soxr; ffmpeg fallback).
    ``channels=None`` keeps the file's channel count."""
    import soundfile as sf

    p = Path(path)
    try:
        data, file_sr = sf.read(str(p), dtype="float32", always_2d=True)
        a = data.T.copy()
        if file_sr != sr:
            a = resample(a, file_sr, sr)
    except Exception:
        a = _ffmpeg_decode(p, sr, channels or 2)
    return a if channels is None else to_channels(a, channels)


def write_wav(path: str | os.PathLike[str], audio: Any, sr: int, *, subtype: str = "PCM_24") -> Path:
    """Atomically write ``(ch, n)``/``(n,)`` float audio as WAV."""
    import soundfile as sf

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    a = as_2d(audio)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.stem}.", suffix=".wav", dir=p.parent)
    os.close(fd)
    try:
        sf.write(tmp, np.clip(a, -1.0, 1.0).T if subtype.startswith("PCM") else a.T, sr, subtype=subtype,
                 format="WAV")
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return p


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def db_to_gain(db: float | np.ndarray) -> Any:
    return np.power(10.0, np.asarray(db, dtype=np.float64) / 20.0)


def gain_to_db(g: float | np.ndarray, floor: float = -200.0) -> Any:
    return np.maximum(20.0 * np.log10(np.maximum(np.abs(np.asarray(g, dtype=np.float64)), 1e-12)), floor)


def peak_dbfs(audio: Any) -> float:
    a = np.asarray(audio)
    return float(gain_to_db(np.max(np.abs(a)) if a.size else 0.0))


def raised_cosine(n: int, *, rising: bool = True) -> np.ndarray:
    """Raised-cosine ramp of ``n`` samples from 0→1 (``rising``) or 1→0."""
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    t = (np.arange(n, dtype=np.float64) + 0.5) / n
    r = 0.5 - 0.5 * np.cos(np.pi * t)
    return r if rising else r[::-1]


def _apply_fade(a: np.ndarray, n: int, *, at_start: bool) -> None:
    n = int(min(max(n, 0), a.shape[-1]))
    if n <= 0:
        return
    ramp = raised_cosine(n, rising=at_start).astype(np.float32)
    if at_start:
        a[..., :n] *= ramp
    else:
        a[..., a.shape[-1] - n:] *= ramp


# ---------------------------------------------------------------------------------------------- loudness
def _biquad_k(sr: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """BS.1770 K-weighting as two biquads (high shelf +4 dB @1.5 kHz, high pass @38 Hz), derived for any
    rate with the same parameters pyloudnorm uses (matches the published 48 kHz coefficients)."""
    out = []
    # stage 1: high shelf
    G, Q, fc = 3.999843853973347, 0.7071752369554196, 1681.974450955533
    K = math.tan(math.pi * fc / sr)
    Vh = 10.0 ** (G / 20.0)
    Vb = Vh ** 0.4996667741545416
    a0 = 1.0 + K / Q + K * K
    b = np.array([(Vh + Vb * K / Q + K * K) / a0, 2.0 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0])
    a = np.array([1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0])
    out.append((b, a))
    # stage 2: high pass (RLB)
    Q2, fc2 = 0.5003270373238773, 38.13547087602444
    K2 = math.tan(math.pi * fc2 / sr)
    a02 = 1.0 + K2 / Q2 + K2 * K2
    b2 = np.array([1.0, -2.0, 1.0])
    a2 = np.array([1.0, 2.0 * (K2 * K2 - 1.0) / a02, (1.0 - K2 / Q2 + K2 * K2) / a02])
    out.append((b2, a2))
    return out


def k_weight(audio: Any, sr: int) -> np.ndarray:
    from scipy.signal import lfilter

    a = as_2d(audio).astype(np.float64)
    for b, aa in _biquad_k(sr):
        a = lfilter(b, aa, a, axis=-1)
    return a


def integrated_lufs(audio: Any, sr: int) -> float | None:
    """BS.1770-4 gated integrated loudness (pyloudnorm); None for silence/too short."""
    import pyloudnorm as pyln

    a = as_2d(audio)
    if a.shape[-1] < int(0.4 * sr) + 1:
        a = np.pad(a, ((0, 0), (0, int(0.4 * sr) + 1 - a.shape[-1])))
    try:
        val = pyln.Meter(sr).integrated_loudness(a.T.astype(np.float64))
    except Exception:  # pragma: no cover - defensive
        return None
    return float(val) if math.isfinite(val) else None


def window_loudness(audio: Any, sr: int, *, window_s: float = 3.0, hop_s: float = 0.1,
                    kw: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Ungated sliding K-weighted loudness (``window_s`` = 0.4 momentary, 3.0 short-term, EBU Tech 3341).
    Returns (window centre times s, LUFS)."""
    k = kw if kw is not None else k_weight(audio, sr)
    ms = (k ** 2).sum(axis=0)
    w = max(1, round(window_s * sr))
    h = max(1, round(hop_s * sr))
    if ms.size < w:
        ms = np.pad(ms, (0, w - ms.size))
    c = np.concatenate([[0.0], np.cumsum(ms)])
    starts = np.arange(0, ms.size - w + 1, h)
    power = (c[starts + w] - c[starts]) / w
    lufs = -0.691 + 10.0 * np.log10(np.maximum(power, 1e-20))
    return (starts + w / 2) / sr, lufs


def short_term_lufs_at(audio: Any, sr: int, t_s: float, *, window_s: float = 3.0) -> float:
    """Ungated short-term loudness of the ``window_s`` window centred at ``t_s`` (zero-padded at edges)."""
    a = as_2d(audio)
    w = round(window_s * sr)
    c = round(t_s * sr)
    lo, hi = c - w // 2, c - w // 2 + w
    seg = a[:, max(0, lo):max(0, min(a.shape[-1], hi))]
    pad_l, pad_r = max(0, -lo), max(0, hi - a.shape[-1])
    seg = np.pad(seg, ((0, 0), (pad_l, pad_r)))
    k = k_weight(seg, sr)
    p = float((k ** 2).sum(axis=0).mean())
    return -0.691 + 10.0 * math.log10(max(p, 1e-20))


def max_momentary_lufs(audio: Any, sr: int) -> float:
    """Maximum momentary (400 ms) loudness; short clips are zero-padded to one window."""
    _, lufs = window_loudness(audio, sr, window_s=0.4, hop_s=0.01)
    return float(np.max(lufs)) if lufs.size else -120.0


# ---------------------------------------------------------------------------------------------- decoding
def pcm16_to_array(data: bytes, channels: int) -> np.ndarray:
    """Little-endian signed 16-bit PCM → float32 ``(channels, n)``."""
    n = len(data) // (2 * channels)
    x = np.frombuffer(data[: n * 2 * channels], dtype="<i2").astype(np.float32) / 32768.0
    return x.reshape(n, channels).T.copy()


def infer_pcm_channels(n_values: int, sr: int, expected_s: float | None, *, default: int = 1) -> int:
    """Raw PCM carries no header: infer mono/stereo from the value count vs the requested duration."""
    if not expected_s or expected_s <= 0:
        return default
    ratio = n_values / (sr * expected_s)
    return 2 if ratio > 1.5 else 1


def decode_elevenlabs_audio(data: bytes, output_format: str, *, expected_s: float | None = None,
                            sr: int = SAMPLE_RATE, pcm_default_channels: int = 1) -> tuple[np.ndarray, int]:
    """Decode an ElevenLabs audio payload to float32 ``(ch, n)`` at ``sr``. Containers are sniffed by
    magic bytes (RIFF/ID3/MPEG/Ogg) before trusting ``output_format``; raw ``pcm_*`` is s16le."""
    import soundfile as sf

    if not data:
        raise ValueError("empty audio payload")
    head = data[:4]
    is_container = head == b"RIFF" or head[:3] == b"ID3" or head == b"OggS" or (
        len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0 and not output_format.startswith("pcm_"))
    if output_format.startswith("pcm_") and not is_container:
        fmt_sr = int(output_format.split("_")[1])
        n_vals = len(data) // 2
        ch = infer_pcm_channels(n_vals, fmt_sr, expected_s, default=pcm_default_channels)
        a = pcm16_to_array(data, ch)
        return (resample(a, fmt_sr, sr) if fmt_sr != sr else a), sr
    try:
        arr, file_sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        a = arr.T.copy()
    except Exception:
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as fh:
            fh.write(data)
            tmp = Path(fh.name)
        try:
            a = _ffmpeg_decode(tmp, sr, 2)
            file_sr = sr
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
    return (resample(a, file_sr, sr) if file_sr != sr else a), sr


# ============================================================================================ ElevenLabs HTTP
class ElevenLabsError(RuntimeError):
    """An ElevenLabs API call failed (message never contains the key)."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class ElevenLabsHTTP:
    """Minimal ElevenLabs client: ``xi-api-key`` auth, retries with backoff on 408/409/425/429/5xx and
    network errors (honouring ``Retry-After``), redacted errors. ``transport`` lets tests mock HTTP."""

    def __init__(self, api_key: str, *, base_url: str = ELEVENLABS_BASE_URL, timeout_s: float = 900.0,
                 transport: httpx.BaseTransport | None = None, max_retries: int = 3, backoff_s: float = 2.0,
                 sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise MissingKeyError("elevenlabs key not configured (set ELEVENLABS_API_KEY or STUDIO_ENV_FILE)")
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.transport = transport
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self._sleep = sleep
        self._tier: str | Literal[False] | None = False  # False = not looked up yet
        self.calls = 0

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self.base_url!r}, key=<redacted>)"

    @classmethod
    def from_settings(cls, settings: Settings | None = None, **kw: Any) -> ElevenLabsHTTP:
        s = settings or get_settings()
        return cls(s.require_key("elevenlabs"), **kw)

    def _redact(self, text: str) -> str:
        return str(redact(text, [self._key]))

    def _retry_delay(self, r: httpx.Response | None, attempt: int) -> float:
        if r is not None:
            ra = r.headers.get("retry-after")
            if ra:
                try:
                    return min(60.0, max(0.0, float(ra)))
                except ValueError:
                    pass
        return min(60.0, self.backoff_s * (2 ** attempt))

    def _error_message(self, method: str, path: str, r: httpx.Response) -> str:
        detail: Any = ""
        try:
            body = r.json()
            detail = body.get("detail", body) if isinstance(body, dict) else body
        except Exception:
            detail = r.text[:500]
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail)[:800]
        return self._redact(f"ElevenLabs {method} {path} failed: HTTP {r.status_code}: {detail}")

    def request(self, method: str, path: str, *, params: Mapping[str, Any] | None = None,
                body: Mapping[str, Any] | None = None, accept: str = "*/*") -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = {"xi-api-key": self._key, "accept": accept}
        last_exc: str = ""
        for attempt in range(self.max_retries + 1):
            self.calls += 1
            try:
                with httpx.Client(timeout=httpx.Timeout(self.timeout_s, connect=30.0),
                                  transport=self.transport) as client:
                    r = client.request(method, url, params=dict(params or {}),
                                       json=dict(body) if body is not None else None, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = type(e).__name__
                if attempt >= self.max_retries:
                    raise ElevenLabsError(f"ElevenLabs {method} {path}: network error ({last_exc})") from None
                self._sleep(self._retry_delay(None, attempt))
                continue
            if r.status_code in _RETRY_STATUS and attempt < self.max_retries:
                self._sleep(self._retry_delay(r, attempt))
                continue
            if r.status_code >= 400:
                raise ElevenLabsError(self._error_message(method, path, r), status=r.status_code)
            return r
        raise ElevenLabsError(f"ElevenLabs {method} {path}: retries exhausted ({last_exc})")  # pragma: no cover

    def post_audio(self, path: str, *, params: Mapping[str, Any], body: Mapping[str, Any]
                   ) -> tuple[bytes, dict[str, str]]:
        r = self.request("POST", path, params=params, body=body)
        hdrs = {k: v for k, v in r.headers.items() if k.lower() in SAFE_RESPONSE_HEADERS}
        return r.content, hdrs

    def get_json(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params, accept="application/json").json()

    def subscription_tier(self) -> str | None:
        """The account's plan tier (``GET /v1/user/subscription``), cached; None when unavailable."""
        if self._tier is False:
            try:
                data = self.get_json("/v1/user/subscription")
                tier = data.get("tier") if isinstance(data, dict) else None
                self._tier = str(tier) if tier else None
            except Exception:
                self._tier = None
        return self._tier or None


class ElevenLabsMusicClient(ElevenLabsHTTP):
    def compose(self, request: MusicRequest) -> tuple[bytes, dict[str, str]]:
        request.validate_api()
        return self.post_audio(MUSIC_ENDPOINT, params={"output_format": request.output_format}, body=request.body())


# ============================================================================================ requests
class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MusicChunk(_M):
    """One composition-plan chunk (v2/v2.5 schema; mapped to a v1 ``section`` when needed)."""

    text: str = Field(min_length=1)
    duration_ms: int = Field(ge=CHUNK_MIN_MS, le=CHUNK_MAX_MS)
    positive_styles: list[str] = Field(default_factory=list, max_length=MAX_STYLES)
    negative_styles: list[str] = Field(default_factory=list, max_length=MAX_STYLES)
    context_adherence: Literal["low", "medium", "high"] = "high"


class MusicRequest(_M):
    """An exact ElevenLabs Music request plus the descriptive intent behind it (not sent)."""

    mode: Literal["plan", "prompt"] = "plan"
    model_id: str = MUSIC_MODEL
    output_format: str = MUSIC_OUTPUT_FORMAT
    prompt: str | None = None
    music_length_ms: int | None = None
    force_instrumental: bool = True
    chunks: list[MusicChunk] = Field(default_factory=list)
    seed: int | None = None
    # --- intent (recorded, hashed only through ``variant``) ---
    bpm: float | None = None
    key: str | None = None
    mood: str | None = None
    genre: str | None = None
    instruments: list[str] = Field(default_factory=list)
    style: str | None = None
    target_s: float | None = None
    end_anchor_s: float | None = None
    hits_s: list[float] = Field(default_factory=list)
    variant: int = 0
    gen_offset_s: float = 0.0  # generated time − region time (head the fitter is expected to trim)
    ending_gen_s: float | None = None  # where the plan puts the button, in generated time

    def validate_api(self) -> None:
        """Raise ``ValueError`` if the request violates the documented API constraints."""
        if self.model_id not in MUSIC_MODELS:
            raise ValueError(f"unknown music model {self.model_id!r} (expected one of {MUSIC_MODELS})")
        if self.mode == "prompt":
            if not (self.prompt and self.prompt.strip()):
                raise ValueError("prompt mode needs a prompt")
            if self.chunks:
                raise ValueError("prompt and composition_plan are mutually exclusive")
            if self.music_length_ms is not None and not (MUSIC_MIN_MS <= self.music_length_ms <= MUSIC_MAX_MS):
                raise ValueError(f"music_length_ms must be {MUSIC_MIN_MS}..{MUSIC_MAX_MS}")
            if self.seed is not None:
                raise ValueError("seed cannot be used with a prompt (use a composition plan)")
        else:
            if not self.chunks:
                raise ValueError("plan mode needs at least one chunk")
            if len(self.chunks) > MAX_CHUNKS:
                raise ValueError(f"at most {MAX_CHUNKS} chunks")
            total = sum(c.duration_ms for c in self.chunks)
            if not (MUSIC_MIN_MS <= total <= MUSIC_MAX_MS):
                raise ValueError(f"total plan length {total} ms outside {MUSIC_MIN_MS}..{MUSIC_MAX_MS}")
            if self.model_id == "music_v1" and len(self.chunks) > MAX_CHUNKS:
                raise ValueError("too many sections")

    def body(self) -> dict[str, Any]:
        """JSON body exactly as sent."""
        if self.mode == "prompt":
            b: dict[str, Any] = {"prompt": self.prompt, "model_id": self.model_id,
                                 "force_instrumental": bool(self.force_instrumental)}
            if self.music_length_ms is not None:
                b["music_length_ms"] = int(self.music_length_ms)
            return b
        if self.model_id == "music_v1":
            plan: dict[str, Any] = {
                "positive_global_styles": list(self.chunks[0].positive_styles),
                "negative_global_styles": sorted({s for c in self.chunks for s in c.negative_styles}),
                "sections": [{
                    "section_name": _section_name(c.text), "positive_local_styles": list(c.positive_styles),
                    "negative_local_styles": list(c.negative_styles), "duration_ms": int(c.duration_ms),
                    "lines": [],
                } for c in self.chunks],
            }
            b = {"composition_plan": plan, "model_id": self.model_id, "respect_sections_durations": True}
        else:
            b = {"composition_plan": {"chunks": [c.model_dump() for c in self.chunks]}, "model_id": self.model_id}
        if self.seed is not None:
            b["seed"] = int(self.seed)
        return b

    def cache_key(self) -> str:
        payload = {"endpoint": MUSIC_ENDPOINT, "output_format": self.output_format, "body": self.body(),
                   "variant": self.variant if self.mode == "prompt" else None}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()

    def expected_duration_s(self) -> float | None:
        if self.mode == "prompt":
            return self.music_length_ms / 1000.0 if self.music_length_ms else None
        return sum(c.duration_ms for c in self.chunks) / 1000.0

    def prompt_text(self) -> str:
        if self.mode == "prompt":
            return self.prompt or ""
        first = self.chunks[0] if self.chunks else None
        return ", ".join(first.positive_styles) if first else ""

    def summary(self) -> str:
        bits = [f"ElevenLabs {self.model_id} {self.mode}"]
        if self.genre:
            bits.append(self.genre)
        if self.bpm:
            bits.append(f"{self.bpm:.0f} BPM")
        if self.key:
            bits.append(self.key)
        exp = self.expected_duration_s()
        if exp:
            bits.append(f"{exp:.1f} s")
        return " · ".join(bits)


def _section_name(text: str) -> str:
    t = text.strip()
    if t.startswith("[") and "]" in t:
        return t[1:t.index("]")].strip()[:100] or "Section"
    return (t.splitlines()[0] if t else "Section")[:100]


# ---------------------------------------------------------------------------------------------- palettes
@dataclass(frozen=True)
class _Palette:
    genre: str
    moods: tuple[str, ...]
    instruments: tuple[tuple[str, ...], ...]  # alternatives, one per candidate variant
    bpm: tuple[int, int]
    mode: Literal["major", "minor", "either"]
    intro: str = "soft start: drums, bass and soft chords, restrained"
    lift: str = "fuller: warm pad and light percussion join, gentle lift in energy, still no lead melody"


_PALETTES: dict[str, _Palette] = {
    "educational": _Palette("minimal ambient electronic", ("calm", "focused", "warm"), (
        ("warm electric piano chords", "soft analog pads", "gentle sub bass", "light brushed percussion"),
        ("felt piano chords, low register", "soft string pad", "round bass", "subtle shaker"),
        ("airy synth pad chords", "muted plucks", "warm bass", "soft kick")), (72, 96), "major",
        intro="soft start: pads and bass, light percussion", lift="fuller: gentle drums join, still calm"),
    "storytime": _Palette("cinematic ambient", ("intimate", "reflective", "warm"), (
        ("felt piano chords, sustained", "soft string pad", "gentle low drone", "very light pulse"),
        ("warm guitar harmonics", "ambient pad chords", "soft sub bass", "brushed texture"),
        ("soft music-box texture", "warm pad chords", "gentle pulse bass", "airy strings")), (65, 90), "either",
        intro="soft start: sustained chords only", lift="fuller: strings swell gently, no drums hits"),
    "comedy": _Palette("light playful acoustic", ("playful", "light", "cheeky"), (
        ("pizzicato strings", "soft marimba chords", "upright bass", "brushed snare"),
        ("light ukulele strums", "upright bass", "finger snaps", "soft glockenspiel accents"),
        ("bouncy plucked synth chords", "round bass", "light claps", "soft kick")), (95, 118), "major"),
    "hot_take": _Palette("minimal modern electronic", ("confident", "tense", "driving"), (
        ("deep pulsing bass", "tight minimal kick", "dark soft pad chords", "sparse closed hats"),
        ("muted synth arpeggio, low register", "sub bass", "dark pad chords", "soft percussion"),
        ("filtered drum loop", "warm bass", "moody electric piano chords", "dark textures")), (90, 110), "minor"),
    "listicle": _Palette("minimal lo-fi pop", ("bright", "upbeat", "light"), (
        ("soft kick and claps", "warm bass", "mellow rhodes chords", "soft muted plucks"),
        ("lo-fi drum loop", "mellow keys chords", "round bass", "light shaker"),
        ("muted guitar chords", "soft kick", "warm bass", "airy pad")), (96, 118), "major"),
    "tutorial": _Palette("lo-fi hip hop", ("chill", "focused", "warm"), (
        ("dusty lo-fi drum loop", "mellow rhodes chords", "warm upright bass", "vinyl texture"),
        ("soft boom bap drums", "jazzy electric piano chords", "round bass", "light vinyl crackle"),
        ("gentle lo-fi beat", "warm pad chords", "round sub bass", "soft muted guitar")), (80, 98), "major"),
    "podcast": _Palette("minimal ambient", ("neutral", "calm", "warm"), (
        ("soft pad chords", "low drone", "gentle pulse", "warm bass"),
        ("warm electric piano chords", "soft pad", "round bass", "very light percussion"),
        ("soft pulse", "warm sub bass", "airy pad chords", "light texture")), (70, 90), "either",
        intro="soft start: pads only", lift="fuller: gentle pulse joins"),
    "sales_ugc": _Palette("upbeat modern pop instrumental", ("bright", "positive", "energetic"), (
        ("punchy soft kick", "claps", "warm bass", "bright piano chords", "light plucks"),
        ("funky muted guitar chords", "tight drums", "round bass", "warm synth pad"),
        ("marimba chords", "soft four-on-the-floor kick", "warm bass", "airy pad")), (100, 124), "major"),
    "founder": _Palette("modern minimal electronic", ("confident", "inspiring", "polished"), (
        ("warm evolving pad chords", "soft pulse bass", "light percussion", "clean electric piano"),
        ("felt piano ostinato, low register", "soft string pad", "subtle kick", "warm bass"),
        ("clean synth pulse", "warm sub bass", "airy pad chords", "soft percussion")), (84, 104), "major"),
}
_DEFAULT_PALETTE = _Palette("minimal lo-fi instrumental", ("warm", "light", "steady"), (
    ("soft drums", "warm bass", "mellow keys chords", "soft pad"),
    ("gentle lo-fi beat", "warm pad chords", "round bass", "light shaker"),
    ("light percussion", "warm electric piano chords", "round bass", "muted plucks")), (84, 104), "either")

_MAJOR_KEYS = ("C major", "F major", "D major", "G major", "A major")
_MINOR_KEYS = ("A minor", "D minor", "E minor", "C minor")
_DARK_MOODS = ("sad", "serious", "melanchol", "tense", "dark", "grief", "somber", "sombre", "dramatic", "moody")


def _palette_for(style: str | None) -> _Palette:
    if not style:
        return _DEFAULT_PALETTE
    s = style.lower().replace("-", "_").replace(" ", "_")
    aliases = {"explainer": "educational", "education": "educational", "story": "storytime", "skit": "comedy",
               "hottake": "hot_take", "opinion": "hot_take", "list": "listicle", "demo": "tutorial",
               "howto": "tutorial", "how_to": "tutorial", "sales": "sales_ugc", "ugc": "sales_ugc",
               "brand": "founder", "podcast_clip": "podcast"}
    return _PALETTES.get(s) or _PALETTES.get(aliases.get(s, ""), _DEFAULT_PALETTE)


def _energy_value(doc: CutDocument | None, index: TakeIndex | None) -> float:
    dial = float(doc.style.dials.energy) if doc is not None else 0.5
    if index is not None and index.energy is not None and (index.energy.overall or index.energy.wpm):
        return float(np.clip(0.6 * index.energy.overall + 0.4 * dial, 0.0, 1.0))
    return float(np.clip(dial, 0.0, 1.0))


def _choose_bpm(palette: _Palette, e: float) -> int:
    """Tempo priors (music.md): calm 60–90, conversational 85–105, high energy 100–125, intersected with
    the style's range; the measured energy wins when they disagree (match, never fake, energy)."""
    band = (60, 90) if e < 0.35 else (85, 105) if e < 0.65 else (100, 125)
    frac = (e / 0.35) if e < 0.35 else ((e - 0.35) / 0.30) if e < 0.65 else ((e - 0.65) / 0.35)
    frac = float(np.clip(frac, 0.0, 1.0))
    lo, hi = max(palette.bpm[0], band[0]), min(palette.bpm[1], band[1])
    if lo > hi:
        lo, hi = band
    return round(lo + (hi - lo) * frac)


def _bpm_for_bars(base: int, dist_s: float, *, window: float = 0.07) -> int:
    """Integer tempo within ±``window`` of ``base`` that makes ``dist_s`` (payoff → ending) closest to a whole
    number of 4/4 bars, so the lift and the button can both sit on bar lines (1 BPM of change ≈ 20 ms)."""
    if dist_s <= 0:
        return base
    best, best_cost = base, math.inf
    for b in range(max(40, math.ceil(base * (1 - window) - 1e-9)), math.floor(base * (1 + window) + 1e-9) + 1):
        bars = dist_s * b / 240.0
        resid_s = abs(bars - round(bars)) * 240.0 / b
        cost = resid_s + 0.02 * abs(b - base) + (1.0 if round(bars) < 1 else 0.0)
        if cost < best_cost - 1e-12:
            best, best_cost = b, cost
    return best


def _choose_key(palette: _Palette, mood_text: str, variant: int, e: float) -> str:
    dark = any(m in mood_text.lower() for m in _DARK_MOODS)
    if palette.mode == "minor" or (palette.mode == "either" and (dark or e < 0.3)):
        return _MINOR_KEYS[variant % len(_MINOR_KEYS)]
    if dark:
        return _MINOR_KEYS[variant % len(_MINOR_KEYS)]
    return _MAJOR_KEYS[variant % len(_MAJOR_KEYS)]


def _dedupe(items: Sequence[str], limit: int = MAX_STYLES) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for s in items:
        s = " ".join(str(s).split()).strip(" ,.")
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s[:200])
    return out[:limit]


_ENDING_TEXT = ("{one final chord hit exactly at the start of this section, then only its natural ring-out; "
                "no drums or groove after the hit, no fade out}")


def plan_chunks(end_hit_s: float, *, bar_s: float | None = None, hits_s: Sequence[float] = (),
                ending_s: float = ENDING_CHUNK_S,
                intro: str = "soft start: drums, bass and soft chords, restrained",
                lift: str = "fuller: warm pad and light percussion join, gentle lift in energy, still no lead melody",
                global_styles: Sequence[str] = (), section_styles: Sequence[str] = (),
                negatives: Sequence[str] = VOCAL_NEGATIVES + BED_NEGATIVES) -> list[MusicChunk]:
    """The music map as plan chunks in generated time: [Intro] (held back) → [Lift] on the first payoff's
    bar (or [Main]) → a dedicated [Ending] chunk that starts on ``end_hit_s`` (the bar where the button must
    land) and holds only the hit and its ring-out. Boundaries snap to ``bar_s`` when given; chunks respect
    the 3–120 s bounds (long sections split on bar lines)."""
    bar = bar_s if bar_s and bar_s > 0 else None

    def snap(t: float) -> float:
        return round(t / bar) * bar if bar else t

    E = max(0.0, float(end_hit_s))
    body: list[tuple[str, float, str]] = []  # (name, start_s, direction)
    lift_t = next((snap(h) for h in sorted(hits_s)
                   if snap(h) * 1000 >= CHUNK_MIN_MS and (E - snap(h)) * 1000 >= CHUNK_MIN_MS), None)
    if E * 1000 < CHUNK_MIN_MS:
        body = []
    elif lift_t is not None:
        body = [("Intro", 0.0, "{" + intro + "}"), ("Lift", lift_t, "{" + lift + "}")]
    elif E >= 20.0:
        intro_end = snap(min(6.0, max(3.0, 0.12 * E)))
        if intro_end * 1000 < CHUNK_MIN_MS and bar:
            intro_end += bar
        body = [("Intro", 0.0, "{" + intro + "}"), ("Main", intro_end, "{steady groove: drums, bass and chords}")]
    else:
        body = [("Main", 0.0, "{steady groove: drums, bass and chords}")]
    sections: list[tuple[str, float, float, str]] = []  # (name, start, end, direction)
    for i, (name, t0, direction) in enumerate(body):
        t1 = body[i + 1][1] if i + 1 < len(body) else E
        parts = max(1, math.ceil((t1 - t0) * 1000 / CHUNK_MAX_MS))
        for p in range(parts):
            a0 = t0 + (t1 - t0) * p / parts
            a1 = t0 + (t1 - t0) * (p + 1) / parts
            if bar and p + 1 < parts:
                a1 = t0 + math.floor(((t1 - t0) * (p + 1) / parts) / bar) * bar
            if p:
                a0 = sections[-1][2]
            sections.append((name if parts == 1 else f"{name} {p + 1}", a0, a1, direction))
    total = E + ending_s
    if sections:
        sections.append(("Ending", E, total, _ENDING_TEXT))
    else:  # a region too short for a body chunk: one chunk that still ends on a hit
        sections.append(("Main", 0.0, max(total, CHUNK_MIN_MS / 1000.0),
                         "{steady groove, then one final chord hit and its natural ring-out, no fade out}"))
    edges = [round(sec[1] * 1000) for sec in sections] + [round(sections[-1][2] * 1000)]
    chunks: list[MusicChunk] = []
    for i, (name, _, _, direction) in enumerate(sections[:MAX_CHUNKS]):
        pos = list(global_styles) if i == 0 else list(section_styles)
        neg = list(negatives)
        if name.startswith("Lift"):
            pos = [*pos, "gentle lift in energy"]
        if name == "Ending":
            pos = [*pos, "single final chord hit", "natural ring-out"]
            neg = [*neg, "drums after the final hit", "fade out", "new melody"]
        dur = int(np.clip(edges[i + 1] - edges[i], CHUNK_MIN_MS, CHUNK_MAX_MS))
        chunks.append(MusicChunk(text=f"[{name}]\n{direction}", duration_ms=dur, positive_styles=_dedupe(pos),
                                 negative_styles=_dedupe(neg)))
    return chunks


def build_music_request(doc: CutDocument | None = None, *, duration_s: float, index: TakeIndex | None = None,
                        end_anchor_s: float | None = None, hits_s: Sequence[float] = (), variant: int = 0,
                        mood: str | None = None, prompt: str | None = None, style: str | None = None,
                        bpm: float | None = None, key: str | None = None, mode: Literal["plan", "prompt"] = "plan",
                        model_id: str = MUSIC_MODEL, seed: int | None = None,
                        output_format: str = MUSIC_OUTPUT_FORMAT) -> MusicRequest:
    """Build a bed request from the brief, style and measured energy. ``duration_s`` is the music region
    (music start → video end); ``end_anchor_s`` (default ``duration_s - 0.5``) is where the ending lands
    and ``hits_s`` are payoff moments (both relative to the region start). The generated length is
    bar-aligned from the requested BPM with a dedicated [Ending] chunk on the bar just after the anchor
    (see ``ENDING_CHUNK_S``); the fitter aligns what actually comes back."""
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    music = doc.audio.music if doc is not None else None
    style_name = style or (doc.style.primary if doc is not None else None)
    palette = _palette_for(style_name)
    e = _energy_value(doc, index)
    brief = doc.brief if doc is not None else None
    mood_txt = " ".join(x for x in (mood, music.mood if music else None, brief.vibe if brief else None) if x)
    tempo = round(bpm) if bpm else _choose_bpm(palette, e)
    music_key = key or _choose_key(palette, mood_txt or " ".join(palette.moods), variant, e)
    instruments = list(palette.instruments[variant % len(palette.instruments)])
    moods = _dedupe([m.strip() for m in (mood_txt.split(",") if mood_txt else [])] + list(palette.moods))[:3]
    director_prompt = prompt or (music.prompt if music is not None else None)
    anchor = end_anchor_s if end_anchor_s is not None else max(0.0, duration_s - 0.5)
    anchor = float(np.clip(anchor, 0.0, duration_s))
    payoff = next((float(h) for h in sorted(hits_s) if 3.0 <= h <= anchor - 3.0), None)
    if payoff is not None and not bpm:
        tempo = _bpm_for_bars(tempo, anchor - payoff)
    bar = 4 * 60.0 / tempo
    hit_gen = bar * math.ceil((anchor + ENDING_PREROLL_S) / bar - 1e-9)  # the button's bar in generated time
    offset = hit_gen - anchor  # generated time = region time + offset (the fitter trims the head)
    gen_total = max(hit_gen + ENDING_CHUNK_S, MUSIC_MIN_MS / 1000.0)
    gen_hits = [float(h) + offset for h in hits_s]
    seed_val = seed if seed is not None else (None if mode == "prompt" else 1000 + variant)
    global_styles = _dedupe([
        *([director_prompt] if director_prompt else []), palette.genre, *moods, *instruments,
        f"{tempo} BPM", f"in {music_key}", *BED_USE_STYLES])
    section_styles = _dedupe([palette.genre, *instruments, f"{tempo} BPM", f"in {music_key}", "instrumental only",
                              "background music bed for a spoken-word video", "no lead melody"])
    req_common: dict[str, Any] = dict(model_id=model_id, output_format=output_format, bpm=float(tempo),
                                      key=music_key, mood=", ".join(moods), genre=palette.genre,
                                      instruments=instruments, style=style_name, target_s=float(duration_s),
                                      end_anchor_s=anchor, hits_s=[float(h) for h in hits_s], variant=variant,
                                      gen_offset_s=round(offset, 6), ending_gen_s=round(hit_gen, 6))
    if mode == "prompt":
        lift = next((round(h / bar) * bar for h in sorted(gen_hits) if 3.0 <= round(h / bar) * bar <= hit_gen - 3.0),
                    None)
        structure = "Starts soft and restrained"
        if lift is not None:
            structure += f", lifts gently at {lift:.1f} seconds (bar {round(lift / bar) + 1})"
        structure += (f", then ends on a single final chord hit exactly at {hit_gen:.1f} seconds (the downbeat of "
                      f"bar {round(hit_gen / bar) + 1}) and lets it ring out naturally; no drums after the hit, "
                      "no fade out.")
        text = (f"{director_prompt + '. ' if director_prompt else ''}{palette.genre}, {', '.join(moods)}. "
                f"{', '.join(instruments)}. {tempo} BPM, in {music_key}. Instrumental only: a warm, full background "
                f"bed for a spoken-word video that stays out of the way of the voice, uncluttered midrange, no lead "
                f"melody, no vocals, steady consistent groove. "
                f"{structure}")
        return MusicRequest(mode="prompt", prompt=text[:4000], music_length_ms=round(gen_total * 1000),
                            force_instrumental=True, **req_common)
    chunks = plan_chunks(hit_gen, bar_s=bar, hits_s=gen_hits, intro=palette.intro, lift=palette.lift,
                         global_styles=global_styles, section_styles=section_styles)
    return MusicRequest(mode="plan", chunks=chunks, seed=seed_val, **req_common)


# ============================================================================================ licences + assets
def music_licence(*, source: str, asset_id: str, record_path: str, tier: str | None = None,
                  acquired_at: str | None = None) -> Licence:
    """Licence for a generated ElevenLabs bed (Eleven Music Model-Specific Terms)."""
    t = (tier or "").lower()
    free = t == "free"
    limited = t in ("free", "starter")
    notes = MUSIC_TERMS_SCOPE + (f" Account plan tier: {tier}." if tier else
                                 " Account plan tier could not be verified; commercial use assumes a paid "
                                 "self-serve plan (Creator or above).")
    return Licence(name=f"Eleven Music — {MUSIC_TERMS_VERSION}", source=source, url=MUSIC_TERMS_URL,
                   holder="ElevenLabs account holder (Yunicorn)", attribution="Eleven Music" if free else None,
                   attribution_required=free, commercial_use=not limited, record_path=record_path,
                   acquired_at=acquired_at or _now_iso(), notes=notes)


def _write_licence_json(job: Job, rel: str, record: Mapping[str, Any]) -> Path:
    return job.save_json(rel, dict(record))


def _analysis_rel(asset_id: str, sub: str = "music") -> str:
    return f"assets/{sub}/{asset_id}.analysis.json"


def generate_music(job: Job, request: MusicRequest, *, settings: Settings | None = None,
                   client: ElevenLabsMusicClient | None = None, analyze: bool = True,
                   detect_voice: bool = True) -> MusicSpec:
    """Generate (or reuse from the job cache) one instrumental bed; returns a :class:`MusicSpec` whose
    ``asset`` is registered with its licence. Raises :class:`ElevenLabsError` / ``MissingKeyError``."""
    request.validate_api()
    key = request.cache_key()
    asset_id = f"elmusic_{key[:16]}"
    rel = f"assets/music/{asset_id}.wav"
    wav = job.path(rel)
    existing = job.load_asset(asset_id)
    if existing is not None and wav.exists():
        job.trace("music_cache_hit", asset_id=asset_id, stage="music")
        return MusicSpec(asset_id=asset_id, source="elevenlabs", prompt=request.prompt_text()[:2000] or None,
                         mood=request.mood, asset=existing)
    cl = client or ElevenLabsMusicClient.from_settings(settings)
    t0 = time.monotonic()
    data, headers = cl.compose(request)
    latency_ms = int((time.monotonic() - t0) * 1000)
    audio, sr = decode_elevenlabs_audio(data, request.output_format, expected_s=request.expected_duration_s(),
                                        pcm_default_channels=_PCM_DEFAULT_CHANNELS_MUSIC)
    if audio.shape[-1] < sr // 2 or not np.any(np.abs(audio) > 1e-5):
        raise ElevenLabsError("ElevenLabs returned empty or silent music")
    write_wav(wav, audio, sr, subtype="PCM_16" if request.output_format.startswith("pcm_") else "PCM_24")
    analysis = analyze_music(audio, sr, bpm_hint=request.bpm, detect_voice=detect_voice) if analyze else None
    if analysis is not None:
        job.save_json(_analysis_rel(asset_id), analysis)
    tier = cl.subscription_tier()
    lic_rel = f"assets/music/{asset_id}.licence.json"
    licence = music_licence(source="elevenlabs", asset_id=asset_id, record_path=lic_rel, tier=tier)
    _write_licence_json(job, lic_rel, {
        "asset_id": asset_id, "kind": "music", "source": "elevenlabs", "product": "Eleven Music API",
        "endpoint": f"POST {MUSIC_ENDPOINT}", "model_id": request.model_id,
        "terms": {"name": MUSIC_TERMS_VERSION, "url": MUSIC_TERMS_URL, "scope": MUSIC_TERMS_SCOPE},
        "plan_tier": tier, "licence": licence.model_dump(mode="json"),
        "request": {"output_format": request.output_format, "body": request.body()},
        "intent": request.model_dump(mode="json", include={"bpm", "key", "mood", "genre", "instruments", "style",
                                                          "target_s", "end_anchor_s", "hits_s", "variant",
                                                          "gen_offset_s", "ending_gen_s"}),
        "response_headers": headers, "generated_at": _now_iso(), "file": rel, "sha256": sha256_file(wav),
        "sample_rate": sr, "channels": int(audio.shape[0]), "duration_s": _r(audio.shape[-1] / sr, 3),
    })
    desc = request.summary() + (f" | measured: {analysis.summary()}" if analysis is not None else "")
    asset = AssetRef(kind="audio", source="elevenlabs",
                     source_id=headers.get("song-id") or headers.get("x-song-id") or key[:16], path=rel,
                     duration_ms=round(audio.shape[-1] * 1000 / sr), description=desc[:1000],
                     query=request.prompt_text()[:500] or None, licence=licence)
    asset = job.register_asset(asset, asset_id=asset_id, overwrite=True)
    job.trace("model_call", role="music_generator", provider="elevenlabs", model=request.model_id,
              latency_ms=latency_ms, stage="music", asset_id=asset_id, mode=request.mode,
              duration_s=_r(audio.shape[-1] / sr, 2), plan_tier=tier)
    return MusicSpec(asset_id=asset_id, source="elevenlabs", prompt=request.prompt_text()[:2000] or None,
                     mood=request.mood, asset=asset)


#: Raw PCM has no header. ElevenLabs Music returns interleaved **stereo** s16le for ``pcm_48000`` (verified
#: with a live call, Sep 2026: 13.4 s requested → 2 × 13.4 s × 48 000 values); the duration check in
#: :func:`infer_pcm_channels` decides whenever the requested length is known.
_PCM_DEFAULT_CHANNELS_MUSIC = 2


def import_music_file(job: Job, path: str | os.PathLike[str], *, licence: Licence, source: str = "file",
                      description: str = "", bpm_hint: float | None = None, asset_id: str | None = None,
                      detect_voice: bool = True) -> MusicSpec:
    """Import a bed the caller holds rights to (creator-owned, library, catalogue download). The audio is
    decoded to 48 kHz stereo WAV under ``assets/music/`` and registered with ``licence`` (required)."""
    if licence is None:
        raise ValueError("a licence is required to import music (invariant 5)")
    src = Path(path)
    digest = sha256_file(src)
    aid = asset_id or f"{source}_{digest[:12]}"
    rel = f"assets/music/{aid}.wav"
    wav = job.path(rel)
    audio = load_audio(src, sr=SAMPLE_RATE, channels=2)
    if audio.shape[-1] < SAMPLE_RATE // 2:
        raise ValueError(f"music file too short: {src.name}")
    write_wav(wav, audio, SAMPLE_RATE, subtype="PCM_24")
    analysis = analyze_music(audio, SAMPLE_RATE, bpm_hint=bpm_hint, detect_voice=detect_voice)
    job.save_json(_analysis_rel(aid), analysis)
    lic_rel = f"assets/music/{aid}.licence.json"
    lic = licence.model_copy(update={"record_path": lic_rel, "acquired_at": licence.acquired_at or _now_iso()})
    _write_licence_json(job, lic_rel, {
        "asset_id": aid, "kind": "music", "source": source, "original_filename": src.name, "sha256_original": digest,
        "licence": lic.model_dump(mode="json"), "imported_at": _now_iso(), "file": rel, "sample_rate": SAMPLE_RATE,
        "channels": 2, "duration_s": _r(audio.shape[-1] / SAMPLE_RATE, 3),
    })
    asset = AssetRef(kind="audio", source=source, source_id=digest[:16], path=rel,
                     duration_ms=round(audio.shape[-1] * 1000 / SAMPLE_RATE),
                     description=(description + " | " if description else "") + analysis.summary(), licence=lic)
    asset = job.register_asset(asset, asset_id=aid, overwrite=True)
    return MusicSpec(asset_id=aid, source=source, asset=asset)


# ============================================================================================ analysis
class MusicEnding(_M):
    kind: Literal["button", "fade", "cut", "sustain", "none"] = "none"
    ending_s: float | None = None  # musical resolution point (button onset / downbeat before the fade)
    ring_out_s: float = 0.0
    confidence: float = 0.0


class MusicSection(_M):
    t_s: float
    strength: float = 0.0
    kind: Literal["novelty", "lift"] = "novelty"


class MusicAnalysis(_M):
    version: int = ANALYSIS_VERSION
    sr: int = SAMPLE_RATE
    duration_s: float = 0.0
    channels: int = 2
    tempo_bpm: float = 0.0
    bpm_hint: float | None = None
    tempo_stability: float = 1.0  # coefficient of variation of inter-beat intervals (lower = steadier)
    beats_s: list[float] = Field(default_factory=list)
    beats_per_bar: int = 4
    downbeat_phase: int = 0
    downbeat_confidence: float = 0.0
    downbeats_s: list[float] = Field(default_factory=list)
    bar_s: float = 0.0
    first_onset_s: float = 0.0
    sound_end_s: float = 0.0
    ending: MusicEnding = Field(default_factory=MusicEnding)
    sections: list[MusicSection] = Field(default_factory=list)
    bar_energy_db: list[float] = Field(default_factory=list)
    key: str | None = None
    key_confidence: float = 0.0
    integrated_lufs: float | None = None
    peak_dbfs: float = -120.0
    speech_band_ratio_db: float | None = None  # 1–4 kHz share of the bed's energy (masking risk)
    gap_ratio: float | None = None  # share of 100 ms windows in the body >30 dB under its median (staccato bed)
    voice_ratio: float | None = None  # share of time a voice detector fires (should be ~0)
    notes: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        bits = [f"{self.duration_s:.1f} s"]
        if self.tempo_bpm:
            bits.append(f"{self.tempo_bpm:.1f} BPM" + (f" (asked {self.bpm_hint:.0f})" if self.bpm_hint else ""))
        if self.key:
            bits.append(self.key)
        if self.ending.kind != "none":
            e = f"ending {self.ending.kind}"
            if self.ending.ending_s is not None:
                e += f" @ {self.ending.ending_s:.2f} s"
            bits.append(e)
        if self.integrated_lufs is not None:
            bits.append(f"{self.integrated_lufs:.1f} LUFS")
        if self.speech_band_ratio_db is not None:
            bits.append(f"1–4 kHz share {self.speech_band_ratio_db:.1f} dB")
        if self.voice_ratio is not None:
            bits.append(f"voice {self.voice_ratio:.2f}")
        if self.gap_ratio is not None and self.gap_ratio > 0.05:
            bits.append(f"gaps {self.gap_ratio:.0%}")
        return " · ".join(bits)

    def section_times(self) -> list[float]:
        return [s.t_s for s in self.sections]


def _zscore(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x
    sd = float(np.std(x))
    return (x - float(np.mean(x))) / sd if sd > 1e-12 else np.zeros_like(x)


def _octave_correct(tempo: float, hint: float | None) -> float:
    if tempo <= 0:
        return hint or 100.0
    cands = [tempo * f for f in (0.5, 2 / 3, 1.0, 1.5, 2.0)]
    cands = [c for c in cands if 45.0 <= c <= 210.0] or [tempo]
    if hint:
        return min(cands, key=lambda c: abs(math.log(c / hint)))
    # no hint: fold into the common bed range
    t = tempo
    while t < 70:
        t *= 2
    while t > 160:
        t /= 2
    return t


def _beat_sync_mean(F: np.ndarray, frames: np.ndarray, T: int) -> np.ndarray:
    """Mean of feature matrix ``F`` (d, T) over each beat interval [b_k, b_{k+1}) → (K, d)."""
    K = len(frames)
    out = np.zeros((K, F.shape[0]), dtype=np.float64)
    for k in range(K):
        a = int(np.clip(frames[k], 0, T - 1))
        b = int(np.clip(frames[k + 1] if k + 1 < K else frames[k] + max(1, (frames[k] - frames[k - 1]) if k else 1),
                        a + 1, T))
        out[k] = F[:, a:b].mean(axis=1)
    return out


class _Feats:
    """Frame-level features at the analysis rate (shared by analysis and fitting)."""

    def __init__(self, x: np.ndarray, sr: int):
        import librosa

        self.xa = resample(x, sr, _ASR) if sr != _ASR else x.astype(np.float32)
        if self.xa.size < 4096:
            self.xa = np.pad(self.xa, (0, 4096 - self.xa.size))
        M = librosa.feature.melspectrogram(y=self.xa, sr=_ASR, n_fft=1024, hop_length=_HOP, n_mels=64, fmax=11025)
        logM = librosa.power_to_db(M, ref=np.max)
        self.env = librosa.onset.onset_strength(S=logM, sr=_ASR, aggregate=np.median)
        mf = librosa.mel_frequencies(66, fmax=11025)[1:-1]
        low = mf < 200.0
        if not np.any(low):  # pragma: no cover
            low[:2] = True
        self.low_env = librosa.onset.onset_strength(S=logM[low], sr=_ASR, aggregate=np.mean)
        self.mfcc = librosa.feature.mfcc(S=librosa.power_to_db(M), n_mfcc=14)[1:]
        self.chroma = librosa.feature.chroma_stft(y=self.xa, sr=_ASR, n_fft=4096, hop_length=_HOP)
        self.rms = librosa.feature.rms(y=self.xa, frame_length=1024, hop_length=_HOP)[0]
        self.rms_db = gain_to_db(self.rms, floor=-120.0)
        self.T = int(min(self.env.size, self.chroma.shape[1], self.rms.size, self.mfcc.shape[1]))
        self.hop_s = _HOP / _ASR

    def frames(self, times: Sequence[float]) -> np.ndarray:
        return np.clip(np.round(np.asarray(times, dtype=np.float64) / self.hop_s).astype(int), 0, self.T - 1)

    def at(self, env: np.ndarray, frames: np.ndarray, w: int = 2) -> np.ndarray:
        return np.array([float(np.max(env[max(0, f - w):min(env.size, f + w + 1)])) for f in frames])

    def beat_matrix(self, beats_s: Sequence[float]) -> np.ndarray:
        """Beat-synchronous feature rows (chroma 12 + MFCC 13 + level 1), standardised, L2-normalised."""
        fr = self.frames(beats_s)
        if len(fr) == 0:
            return np.zeros((0, 26))
        ch = _beat_sync_mean(self.chroma[:, :self.T], fr, self.T)
        mf = _beat_sync_mean(self.mfcc[:, :self.T], fr, self.T)
        lv = _beat_sync_mean(self.rms_db[None, :self.T], fr, self.T)
        X = np.concatenate([ch, mf / 10.0, lv / 10.0], axis=1)
        X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-6)
        return X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)


def _estimate_meter(F: np.ndarray) -> int:
    if F.size < 16:
        return 4
    f = F - F.mean()
    denom = float(np.dot(f, f)) or 1.0

    def ac(lag: int) -> float:
        return float(np.dot(f[:-lag], f[lag:]) / denom) if lag < f.size else 0.0

    s3, s4 = ac(3) + ac(6), ac(4) + ac(8)
    return 3 if s3 > s4 + 0.25 else 4


def _key_estimate(chroma: np.ndarray, weights: np.ndarray | None = None) -> tuple[str | None, float]:
    if chroma.size == 0:
        return None, 0.0
    w = weights if weights is not None else np.ones(chroma.shape[1])
    v = (chroma * w[None, : chroma.shape[1]]).sum(axis=1)
    if not np.any(v > 0):
        return None, 0.0
    best, best_r = None, -2.0
    for tonic in range(12):
        for name, prof in (("major", _KK_MAJOR), ("minor", _KK_MINOR)):
            r = float(np.corrcoef(v, np.roll(prof, tonic))[0, 1])
            if r > best_r:
                best, best_r = f"{PITCH_NAMES[tonic]} {name}", r
    return best, max(0.0, best_r)


def _voice_ratio(x: np.ndarray, sr: int) -> float | None:
    """Share of time Silero VAD hears a voice (advice: generated beds should score ~0)."""
    try:
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad

        model = _silero_model(load_silero_vad)
        x16 = resample(x, sr, 16000)
        ts = get_speech_timestamps(torch.from_numpy(np.ascontiguousarray(x16)), model, sampling_rate=16000,
                                   threshold=0.6, min_speech_duration_ms=300)
        speech = sum(int(t["end"]) - int(t["start"]) for t in ts)
        return float(speech / max(1, x16.size))
    except Exception:  # pragma: no cover - optional dependency path
        return None


_SILERO: list[Any] = []


def _silero_model(loader: Callable[[], Any]) -> Any:
    if not _SILERO:
        _SILERO.append(loader())
    return _SILERO[0]


def _speech_band_ratio_db(x: np.ndarray, sr: int) -> float | None:
    from scipy.signal import welch

    if x.size < 4096 or not np.any(x):
        return None
    f, p = welch(x, fs=sr, nperseg=4096)
    tot = p[(f >= 60) & (f <= 16000)].sum()
    band = p[(f >= 1000) & (f <= 4000)].sum()
    return float(10 * np.log10(max(band, 1e-20) / max(tot, 1e-20)))


def _gap_ratio(x: np.ndarray, sr: int, t0: float, t1: float) -> float | None:
    w = int(0.1 * sr)
    a, b = int(max(0.0, t0) * sr), int(max(0.0, t1) * sr)
    if b - a < 10 * w:
        return None
    seg = x[a:b][: ((b - a) // w) * w].reshape(-1, w)
    lv = gain_to_db(np.sqrt((seg.astype(np.float64) ** 2).mean(axis=1)), floor=-120.0)
    return float(np.mean(lv < float(np.median(lv)) - 30.0))


def analyze_music(audio: Any, sr: int = SAMPLE_RATE, *, bpm_hint: float | None = None,
                  beats_per_bar: int | None = None, detect_voice: bool = True) -> MusicAnalysis:
    """Measure a bed: tempo, beats, downbeats, bars, sections, energy, key, ending, loudness, masking risk."""
    import librosa

    a = as_2d(audio)
    x = to_mono(a)
    dur = x.size / sr
    notes: list[str] = []
    peak = peak_dbfs(x)
    base = dict(sr=sr, duration_s=_r(dur, 4) or 0.0, channels=int(a.shape[0]), bpm_hint=bpm_hint, peak_dbfs=_r(peak, 2))
    if dur < 1.5 or peak < -80:
        return MusicAnalysis(**base, notes=["too short or silent to analyse"])
    fe = _Feats(x, sr)
    T, hop_s = fe.T, fe.hop_s
    env = fe.env[:T]
    rdb = fe.rms_db[:T]
    ref = float(np.percentile(rdb[rdb > -100], 95)) if np.any(rdb > -100) else -60.0
    active = np.nonzero(rdb > ref - 45.0)[0]
    first_active = float(active[0] * hop_s) if active.size else 0.0
    sound_end = float(min(dur, (active[-1] + 1) * hop_s)) if active.size else dur
    on_frames = librosa.onset.onset_detect(onset_envelope=env, sr=_ASR, hop_length=_HOP, units="frames")
    on_frames = np.asarray(on_frames, dtype=int)
    first_onset = float(min(first_active, on_frames[0] * hop_s)) if on_frames.size else first_active

    # ---- tempo + beats
    t0 = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=env, sr=_ASR, hop_length=_HOP,
                                                    start_bpm=bpm_hint or 110.0,
                                                    std_bpm=0.5 if bpm_hint else 1.0))[0])
    tempo = _octave_correct(t0, bpm_hint)
    _, bfr = librosa.beat.beat_track(onset_envelope=env, sr=_ASR, hop_length=_HOP, bpm=tempo, tightness=300,
                                     trim=False, units="frames")
    beats = np.asarray(bfr, dtype=np.float64) * hop_s
    beats = beats[(beats >= first_onset - 0.08) & (beats <= sound_end + 0.02)]
    period = 60.0 / tempo
    stability = 1.0
    if beats.size >= 4:
        ibi = np.diff(beats)
        med = float(np.median(ibi))
        stability = float(np.std(ibi) / max(med, 1e-6))
        period = med
        # regularise: constant-tempo beds get a least-squares grid; tracked beats that sit on it are kept
        k = np.round((beats - beats[0]) / period).astype(int)
        if len(np.unique(k)) >= 4:
            slope, icpt = np.polyfit(k, beats, 1)
            resid = beats - (icpt + slope * k)
            if float(np.median(np.abs(resid))) < 0.02 and stability < 0.08:
                period = float(slope)
                k_lo = math.ceil((first_onset - 0.06 - icpt) / period)
                last_on = float(on_frames[-1] * hop_s) if on_frames.size else sound_end
                k_hi = math.floor((max(last_on, beats[-1]) + 0.06 - icpt) / period)
                grid = icpt + period * np.arange(k_lo, k_hi + 1)
                fixed = []
                for g in grid:
                    j = int(np.argmin(np.abs(beats - g)))
                    fixed.append(beats[j] if abs(beats[j] - g) < 0.025 else g)
                beats = np.asarray(fixed)
                stability = float(np.std(np.diff(beats)) / period) if beats.size > 2 else stability
    elif on_frames.size:
        beats = np.arange(first_onset, sound_end, period)
        notes.append("beat tracker found too few beats; used a tempo grid from the first onset")
    tempo_bpm = 60.0 / period if period > 0 else tempo

    # ---- downbeats
    bpb = beats_per_bar or 4
    phase, conf = 0, 0.0
    if beats.size >= 2 * bpb:
        bf = fe.frames(beats)
        low_on = fe.at(fe.low_env[:T], bf)
        full_on = fe.at(env, bf)
        ch = _beat_sync_mean(fe.chroma[:, :T], bf, T)
        chn = ch / (np.linalg.norm(ch, axis=1, keepdims=True) + 1e-9)
        chg = np.concatenate([[0.0], 1.0 - np.sum(chn[1:] * chn[:-1], axis=1)])
        chg[0] = float(np.median(chg[1:])) if chg.size > 1 else 0.0
        lvl = _beat_sync_mean(rdb[None, :], bf, T)[:, 0]
        rise = np.concatenate([[0.0], np.diff(lvl)])
        F = _zscore(low_on) + _zscore(chg) + 0.5 * _zscore(rise) + 0.25 * _zscore(full_on)
        if beats_per_bar is None:
            bpb = _estimate_meter(F)
        scores = np.array([float(np.mean(F[p::bpb])) for p in range(bpb)])
        first_idx = int(np.argmax(beats >= first_onset - 0.06)) if np.any(beats >= first_onset - 0.06) else 0
        order = np.argsort(scores)[::-1]
        margin = float(scores[order[0]] - scores[order[1]])
        if margin < 0.35:  # unaccented: trust that the piece starts on bar one
            phase, conf = first_idx % bpb, float(np.tanh(max(0.0, margin)) * 0.5)
        else:
            phase, conf = int(order[0]), float(np.tanh(margin))
    downbeats = beats[phase::bpb] if beats.size else np.zeros(0)
    bar_s = bpb * (60.0 / tempo_bpm) if tempo_bpm > 0 else 0.0

    # ---- sections: Foote novelty on beat-synchronous features + energy lifts
    sections: list[MusicSection] = []
    bar_energy: list[float] = []
    if beats.size >= 4 * bpb:
        X = fe.beat_matrix(beats)
        S = X @ X.T
        L = 2 * bpb
        g = np.exp(-0.5 * ((np.arange(-L, L) + 0.5) / (L / 2.0)) ** 2)
        kern = np.outer(g, g) * np.outer(np.sign(np.arange(-L, L) + 0.5), np.sign(np.arange(-L, L) + 0.5))
        nov = np.zeros(len(beats))
        for kk in range(L, len(beats) - L):
            nov[kk] = float(np.sum(kern * S[kk - L:kk + L, kk - L:kk + L]))
        nov = np.maximum(nov, 0.0)
        if nov.max() > 0:
            nov /= nov.max()
            thr = float(nov[L:len(beats) - L].mean() + 0.5 * nov[L:len(beats) - L].std())
            for kk in range(L, len(beats) - L):
                lo, hi = max(0, kk - 2 * bpb), min(len(beats), kk + 2 * bpb + 1)
                if nov[kk] >= thr and nov[kk] == nov[lo:hi].max() and nov[kk] > 0.2:
                    t = float(beats[kk])
                    if downbeats.size:
                        j = int(np.argmin(np.abs(downbeats - t)))
                        if abs(downbeats[j] - t) <= bar_s / 2:
                            t = float(downbeats[j])
                    sections.append(MusicSection(t_s=_r(t) or 0.0, strength=_r(float(nov[kk]), 3) or 0.0))
    if downbeats.size >= 2:
        edges = [*list(downbeats), downbeats[-1] + bar_s]
        for i in range(len(downbeats)):
            fa, fb = fe.frames([edges[i], edges[i + 1]])
            seg = fe.rms[fa:max(fa + 1, fb)]
            bar_energy.append(float(gain_to_db(np.sqrt(np.mean(seg ** 2)) if seg.size else 0.0)))
        for i in range(2, len(bar_energy)):
            if bar_energy[i] - float(np.mean(bar_energy[i - 2:i])) >= 3.0 and bar_energy[i] > ref - 30:
                t = float(downbeats[i])
                if all(abs(s.t_s - t) > bar_s / 2 for s in sections):
                    sections.append(MusicSection(t_s=_r(t) or 0.0, strength=_r(min(1.0, (bar_energy[i] - np.mean(
                        bar_energy[i - 2:i])) / 9.0), 3) or 0.0, kind="lift"))
                else:
                    for s in sections:
                        if abs(s.t_s - t) <= bar_s / 2:
                            s.kind = "lift"
    sections.sort(key=lambda s: s.t_s)
    sections = sections[:16]

    # ---- ending
    ending = _classify_ending(fe, rdb, ref, on_frames, env, downbeats, bar_s or 2.0, sound_end, dur)

    # ---- key, loudness, masking, voice
    wts = np.where(rdb > ref - 30, 1.0, 0.0)
    key, kconf = _key_estimate(fe.chroma[:, :T], wts)
    if conf < 0.15 and downbeats.size:
        notes.append("low downbeat confidence: bar positions are an estimate")
    if bpm_hint and tempo_bpm and abs(math.log(tempo_bpm / bpm_hint)) > math.log(1.08):
        notes.append(f"measured tempo {tempo_bpm:.1f} differs from the requested {bpm_hint:.0f} BPM")
    gap_ratio = _gap_ratio(x, sr, first_onset, ending.ending_s if ending.ending_s is not None else sound_end)
    if gap_ratio is not None and gap_ratio > 0.2:
        notes.append(f"staccato bed: {gap_ratio:.0%} of the body drops >30 dB (a bed should sustain under speech)")
    vr = _voice_ratio(x, sr) if detect_voice else None
    if vr is not None and vr > 0.1:
        notes.append(f"a voice detector fires on {vr:.0%} of the bed (check for vocals)")
    return MusicAnalysis(
        **base, tempo_bpm=_r(tempo_bpm, 3) or 0.0, tempo_stability=_r(stability, 4) or 0.0,
        beats_s=[_r(b) or 0.0 for b in beats], beats_per_bar=bpb, downbeat_phase=phase,
        downbeat_confidence=_r(conf, 3) or 0.0, downbeats_s=[_r(b) or 0.0 for b in downbeats],
        bar_s=_r(bar_s, 5) or 0.0, first_onset_s=_r(first_onset) or 0.0, sound_end_s=_r(sound_end) or 0.0,
        ending=ending, sections=sections, bar_energy_db=[_r(v, 2) or 0.0 for v in bar_energy], key=key,
        key_confidence=_r(kconf, 3) or 0.0, integrated_lufs=_r(integrated_lufs(a, sr), 2),
        speech_band_ratio_db=_r(_speech_band_ratio_db(x, sr), 2), gap_ratio=_r(gap_ratio, 3), voice_ratio=_r(vr, 3),
        notes=notes)


def _smooth_db(rdb: np.ndarray, k: int) -> np.ndarray:
    """Moving average in the power domain (dB averaging collapses on sparse, gappy material)."""
    k = max(1, int(k))
    p = np.power(10.0, rdb / 10.0)
    return 10.0 * np.log10(np.maximum(np.convolve(p, np.ones(k) / k, mode="same"), 1e-12))


def _classify_ending(fe: _Feats, rdb: np.ndarray, ref: float, on_frames: np.ndarray, env: np.ndarray,
                     downbeats: np.ndarray, bar_s: float, sound_end: float, dur: float) -> MusicEnding:
    hop_s = fe.hop_s
    T = rdb.size
    fr = lambda t: int(np.clip(round(t / hop_s), 0, T - 1))  # noqa: E731
    on_t = on_frames * hop_s
    sm5 = _smooth_db(rdb, 5)  # ~30 ms
    sm = _smooth_db(rdb, round(max(0.4, 0.5 * bar_s) / hop_s))  # level contour over half a bar
    i_end = fr(sound_end)
    body = sm[fr(fe.frames([0.0])[0] * hop_s):max(1, fr(max(0.0, sound_end - 2.0)))]
    body = body[body > ref - 40.0]
    plateau = float(np.median(body)) if body.size else ref - 6.0
    # 1) button: the earliest onset in the last bars after which the level only decays (no re-attack bumps,
    #    which separates a button from a fade over continuing beats). The hit must be loud for the track, or
    #    stand clear of a break just before it (a soft final chord after a stop).
    lo_t = max(0.0, sound_end - 4 * bar_s - 0.5)
    for t in (float(x) for x in on_t if lo_t <= x <= sound_end - 0.10):
        i = fr(t)
        w = max(1, int(0.08 / hop_s))
        p = i + int(np.argmax(sm5[i:min(T, i + w)]))
        pk = float(sm5[p])
        before = float(np.max(sm5[max(0, i - int(0.15 / hop_s)):max(1, i - 1)]))
        if pk < ref - 24.0 or (pk < ref - 12.0 and pk < before + 6.0):
            continue
        after = sm5[p + 1:max(p + 2, i_end)]
        if after.size and float(np.max(after - np.minimum.accumulate(after))) > 2.5:
            continue  # something re-attacks later: not the final hit
        pre = sm[max(0, i - int(1.5 / hop_s)):max(1, i - int(0.3 / hop_s))]
        if pre.size and float(np.median(pre)) < plateau - 6.0 and before > pk - 6.0:
            continue  # already inside a fade: its last beat is not a button
        ring = sound_end - t
        decay = pk - (float(after.min()) if after.size else pk)
        if decay < 10.0 and ring > 0.3:
            continue
        return MusicEnding(kind="button", ending_s=_r(t), ring_out_s=_r(ring) or 0.0,
                           confidence=_r(min(1.0, 0.3 + decay / 30.0), 3) or 0.0)
    # 2) abrupt cut: still loud at the very end of the file
    tail = sm5[max(0, T - int(0.1 / hop_s)):]
    if tail.size and float(tail.max()) > ref - 18.0 and dur - sound_end < 0.05:
        return MusicEnding(kind="cut", ending_s=None, ring_out_s=0.0, confidence=0.6)
    # 3) fade: a steady fall from the plateau to the sound end
    above = np.nonzero(sm[:i_end] >= plateau - 3.0)[0]
    fade_start = float(above[-1] * hop_s) if above.size else max(0.0, sound_end - 2.0)
    fade_len = sound_end - fade_start
    if fade_len >= 1.2:
        slope = (float(sm[max(0, i_end - 1)]) - float(sm[fr(fade_start)])) / fade_len
        prior = downbeats[downbeats <= fade_start + 0.05] if downbeats.size else np.zeros(0)
        e = float(prior[-1]) if prior.size else fade_start
        if slope <= -4.0:
            return MusicEnding(kind="fade", ending_s=_r(e), ring_out_s=_r(sound_end - e) or 0.0,
                               confidence=_r(min(1.0, -slope / 15.0), 3) or 0.0)
        return MusicEnding(kind="sustain", ending_s=_r(e), ring_out_s=_r(sound_end - e) or 0.0, confidence=0.3)
    return MusicEnding(kind="cut", ending_s=None, ring_out_s=0.0, confidence=0.3)


def candidate_score(analysis: MusicAnalysis) -> float:
    """Advisory ranking of generated candidates (higher = safer bed). Taste stays with the Director."""
    s = 0.0
    s += {"button": 1.0, "fade": 0.4, "sustain": 0.3}.get(analysis.ending.kind, 0.0)
    s += 0.5 * analysis.downbeat_confidence
    s -= 5.0 * min(1.0, analysis.tempo_stability)
    if analysis.voice_ratio is not None:
        s -= 4.0 * max(0.0, analysis.voice_ratio - 0.02)
    if analysis.bpm_hint and analysis.tempo_bpm:
        s -= 8.0 * max(0.0, abs(math.log(analysis.tempo_bpm / analysis.bpm_hint)) - 0.03)
    if analysis.speech_band_ratio_db is not None:
        s -= 0.15 * max(0.0, analysis.speech_band_ratio_db + 9.0)
    if analysis.gap_ratio is not None:
        s -= 4.0 * max(0.0, analysis.gap_ratio - 0.05)
    return round(s, 4)


def load_music(job: Job, asset: AssetRef | str, *, sr: int = SAMPLE_RATE) -> tuple[np.ndarray, MusicAnalysis]:
    """Stereo audio ``(2, n)`` at ``sr`` plus its (cached) analysis for a registered bed."""
    a = job.load_asset(asset) if isinstance(asset, str) else asset
    if a is None or not a.path:
        raise FileNotFoundError(f"music asset {asset!r} is not registered with a file")
    audio = load_audio(job.path(a.path), sr=sr, channels=2)
    return audio, music_analysis_for(job, a, audio=audio, sr=sr)


def music_analysis_for(job: Job, asset: AssetRef | str, *, audio: np.ndarray | None = None,
                       sr: int = SAMPLE_RATE) -> MusicAnalysis:
    """The cached analysis sidecar of a bed (computed and saved when missing or stale)."""
    a = job.load_asset(asset) if isinstance(asset, str) else asset
    if a is None or not a.id:
        raise FileNotFoundError(f"music asset {asset!r} is not registered")
    rel = _analysis_rel(a.id)
    data = job.load_json(rel, default=None)
    if isinstance(data, dict) and data.get("version") == ANALYSIS_VERSION and int(data.get("sr", 0)) == sr:
        return MusicAnalysis.model_validate(data)
    if audio is None:
        audio = load_audio(job.path(a.path or ""), sr=sr, channels=2)
    an = analyze_music(audio, sr)
    job.save_json(rel, an)
    return an


# ============================================================================================ fit
@dataclass
class FitPiece:
    src_start_s: float
    src_end_s: float
    out_start_s: float


@dataclass
class FitResult:
    audio: np.ndarray
    sr: int
    pieces: list[FitPiece]
    stretch: float
    entry_src_s: float
    ending_kind: str
    ending_src_s: float | None
    ending_out_s: float | None
    downbeats_out_s: list[float]
    hits: list[dict[str, Any]] = field(default_factory=list)
    junctions: list[dict[str, Any]] = field(default_factory=list)
    cost: float = 0.0
    lead_silence_s: float = 0.0
    notes: list[str] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        return {
            "pieces": [p.__dict__ for p in self.pieces], "stretch": round(self.stretch, 5),
            "entry_src_s": _r(self.entry_src_s), "lead_silence_s": _r(self.lead_silence_s),
            "ending_kind": self.ending_kind, "ending_src_s": _r(self.ending_src_s),
            "ending_out_s": _r(self.ending_out_s), "downbeats_out_s": [_r(d, 3) for d in self.downbeats_out_s[:64]],
            "hits": self.hits, "junctions": self.junctions, "cost": round(self.cost, 3), "notes": self.notes,
            "duration_s": _r(self.audio.shape[-1] / self.sr, 4),
        }


@dataclass
class _Plan:
    pieces: list[tuple[float, float]]  # source ranges in play order; the last ends at the ending point E
    junctions: list[tuple[int, int]]  # (downbeat index out, downbeat index in)
    E: float
    stretch: float = 1.0  # >1 = assembled material played faster (pedalboard stretch_factor)
    lead_s: float = 0.0  # output silence before the music enters (delayed entry on the next beat)
    entry_cost: float = 0.0
    cost: float = 0.0
    kind: str = "straight"

    def with_entry(self, h: float, *, stretch: float = 1.0, lead_s: float = 0.0, entry_cost: float = 0.0) -> _Plan:
        return _Plan(pieces=[(h, self.pieces[0][1]), *self.pieces[1:]], junctions=list(self.junctions), E=self.E,
                     stretch=stretch, lead_s=lead_s, entry_cost=entry_cost, kind=self.kind)


_PRE_S = 0.005  # junctions sit this far before the incoming downbeat's detected onset
_HIT_TOL_S = 0.035


class _FitContext:
    def __init__(self, analysis: MusicAnalysis, audio: np.ndarray, sr: int):
        self.an = analysis
        self.D = np.asarray(analysis.downbeats_s, dtype=np.float64)
        self.B = np.asarray(analysis.beats_s, dtype=np.float64)
        self.bar = float(analysis.bar_s) or 2.0
        self.bpb = analysis.beats_per_bar
        self.db_beat_idx = (np.array([int(np.argmin(np.abs(self.B - d))) for d in self.D])
                            if self.D.size and self.B.size else np.zeros(0, int))
        self.sections = [(s.t_s, s.kind) for s in analysis.sections]
        self._sim: dict[tuple[int, int], float] = {}
        self.X = _Feats(to_mono(audio), sr).beat_matrix(self.B) if self.B.size else np.zeros((0, 26))
        first = np.nonzero(analysis.first_onset_s - 0.06 <= self.D)[0]
        self.d0 = int(first[0]) if first.size else 0  # phrase origin (bar 1)

    def bar_vec(self, bi: int) -> np.ndarray | None:
        if bi < 0 or bi + self.bpb > self.X.shape[0]:
            return None
        v = self.X[bi:bi + self.bpb].reshape(-1)
        n = np.linalg.norm(v)
        return v / n if n > 0 else None

    def sim(self, i_out: int, i_in: int) -> float:
        """Seam similarity of jumping from downbeat ``i_out`` to downbeat ``i_in`` (0..1): the bar before
        and the bar after both edit points, compared on beat-synchronous chroma/MFCC/level."""
        key = (i_out, i_in)
        if key not in self._sim:
            bo, bi = int(self.db_beat_idx[i_out]), int(self.db_beat_idx[i_in])
            vals = []
            for off in (-self.bpb, 0):
                a, b = self.bar_vec(bo + off), self.bar_vec(bi + off)
                if a is not None and b is not None:
                    vals.append(0.5 * (float(np.dot(a, b)) + 1.0))
            self._sim[key] = float(np.mean(vals)) if vals else 0.5
        return self._sim[key]

    def entry_points(self) -> tuple[np.ndarray, np.ndarray]:
        """Sorted musical entry points (downbeats cost 0, other beats 0.3, the first onset 0.1)."""
        if not hasattr(self, "_entry"):
            pts = [(float(d), 0.0) for d in self.D]
            pts += [(float(b), 0.3) for b in self.B if not np.any(np.abs(self.D - b) < 0.02)]
            fo = max(0.0, self.an.first_onset_s - _PRE_S)
            if not self.D.size or abs(float(self.D[0]) - fo) > 0.02:
                pts.append((fo, 0.1))
            pts.sort()
            self._entry = (np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
        return self._entry

    def is_section(self, t: float) -> str | None:
        for st, kind in self.sections:
            if abs(st - t) <= 0.04:
                return kind
        return None


def _plan_out_downbeats(ctx: _FitContext, plan: _Plan) -> list[tuple[float, float]]:
    """(final output time, source time) of every downbeat the plan plays (stretch and lead applied)."""
    outs: list[np.ndarray] = []
    srcs: list[np.ndarray] = []
    o = 0.0
    D = ctx.D
    last = len(plan.pieces) - 1
    for idx, (a, b) in enumerate(plan.pieces):
        lo = int(np.searchsorted(D, a - 1e-6, side="left"))
        hi = D.size if idx == last else int(np.searchsorted(D, b - 1e-6, side="left"))
        if hi > lo:
            d = D[lo:hi]
            outs.append(plan.lead_s + (o + (d - a)) / plan.stretch)
            srcs.append(d)
        o += b - a
    if not outs:
        return []
    return list(zip(np.concatenate(outs).tolist(), np.concatenate(srcs).tolist(), strict=True))


def _score_plan(ctx: _FitContext, plan: _Plan, *, R: float, h_min: float, hits: Sequence[float],
                seams: Sequence[float], e_cost: float) -> float:
    cost = e_cost + plan.entry_cost + 1.5 * len(plan.junctions) + 40.0 * abs(plan.stretch - 1.0)
    for (j_out, j_in) in plan.junctions:
        cost += 2.5 * (1.0 - ctx.sim(j_out, j_in))
        if (j_out - j_in) % 4 != 0:
            cost += 0.8
        elif (j_out - ctx.d0) % 4 == 0:
            cost -= 0.2
    h = plan.pieces[0][0]
    cost += min(1.0, 0.12 * max(0.0, h - h_min) / ctx.bar)
    if plan.kind == "loop":
        cost += 0.4 * max(0, len(plan.pieces) - 2)
    dbs = _plan_out_downbeats(ctx, plan)
    outs = np.array([o for o, _ in dbs]) if dbs else np.zeros(0)
    for hit in hits:
        if not outs.size or hit > R + 0.05:
            continue
        j = int(np.argmin(np.abs(outs - hit)))
        if abs(outs[j] - hit) <= _HIT_TOL_S:
            cost -= 1.0
            if ctx.is_section(dbs[j][1]):
                cost -= 0.8
    if seams and outs.size:
        bonus = sum(0.15 for s in seams if s < R and float(np.min(np.abs(outs - s))) <= 0.034)
        cost -= min(0.6, bonus)
    o = 0.0
    for a, b in plan.pieces[:-1]:  # junctions hidden under a hit or a hard seam
        o += b - a
        jt = plan.lead_s + o / plan.stretch
        if any(abs(jt - hh) <= 0.1 for hh in hits):
            cost -= 0.3
        if any(abs(jt - s) <= 0.034 for s in seams):
            cost -= 0.2
    return cost


def _base_plans(ctx: _FitContext, *, R: float, E: float, h_floor: float) -> list[_Plan]:
    """Bar-line plans whose pre-ending material is exactly ``R`` seconds (entry not yet constrained)."""
    D, bar = ctx.D, ctx.bar
    min_first = 0.25 * bar
    min_last = bar * 0.99
    plans: list[_Plan] = [_Plan(pieces=[(E - R, E)], junctions=[], E=E, kind="straight")]
    idx_before = [i for i, d in enumerate(D) if d <= E - min_last + 1e-6]
    # one forward skip of whole bars: [h, D_i) + [D_j, E)
    for j in idx_before:
        first_len = R - (E - D[j])
        if first_len < min_first:
            continue
        for i in range(0, j):
            h = D[i] - first_len
            if h < h_floor:
                continue
            plans.append(_Plan(pieces=[(h, float(D[i])), (float(D[j]), E)], junctions=[(i, j)], E=E, kind="skip"))
    # repeat a loop of whole bars (extension): [h, D_j) + (m-1)×[D_i, D_j) + [D_i, E)
    for i in idx_before:
        for j in range(i + 1, len(D)):
            if D[j] > E + 1e-6:
                break
            loop = D[j] - D[i]
            if loop < bar * 0.99:
                continue
            h1 = D[j] + (E - D[i]) - R
            m = 1 if h1 >= h_floor else 1 + math.ceil((h_floor - h1) / loop)
            if m > 64:
                continue
            h = h1 + (m - 1) * loop
            if D[j] - h < min_first:
                continue
            pieces = [(h, float(D[j]))] + [(float(D[i]), float(D[j]))] * (m - 1) + [(float(D[i]), E)]
            plans.append(_Plan(pieces=pieces, junctions=[(j, i)] * m, E=E, kind="loop"))
    return plans


def _entry_variants(ctx: _FitContext, plan: _Plan, *, R: float, h_min: float, entry: str,
                    max_stretch: float) -> list[_Plan]:
    """Resolve the entry. ``any``: play from ``h`` (mid-bar allowed, under the fade-in). ``downbeat``: move the
    entry onto a downbeat (or, at a small cost, a beat) with a ≤``max_stretch`` stretch, or delay the
    entry to the next beat/downbeat (output silence first) when no stretch fits."""
    h, first_end = plan.pieces[0]
    min_first = 0.25 * ctx.bar
    if entry != "downbeat":
        return [plan] if h >= h_min - 1e-6 and first_end - h >= min_first - 1e-6 else []
    out: list[_Plan] = []
    pts_t, pts_c = ctx.entry_points()
    lo = int(np.searchsorted(pts_t, h - max_stretch * R - 1e-6, side="left"))
    hi = int(np.searchsorted(pts_t, h + max_stretch * R + 1e-6, side="right"))
    best_stretch: _Plan | None = None
    best_c = math.inf
    for k in range(lo, hi):
        ep, c = float(pts_t[k]), float(pts_c[k])
        if ep < h_min - 1e-6 or first_end - ep < min_first:
            continue
        s = (R + h - ep) / R if R > 0 else 1.0
        if abs(s - 1.0) <= max_stretch + 1e-9 and 40 * abs(s - 1) + c < best_c:
            best_c = 40 * abs(s - 1) + c
            best_stretch = plan.with_entry(ep, stretch=s, entry_cost=c)
    if best_stretch is not None:
        out.append(best_stretch)
    k = int(np.searchsorted(pts_t, max(h, h_min) - 1e-6, side="left"))
    while k < pts_t.size and first_end - float(pts_t[k]) >= min_first:
        ep, c = float(pts_t[k]), float(pts_c[k])
        delay = ep - h
        if delay > ctx.bar:
            break
        out.append(plan.with_entry(ep, lead_s=delay, entry_cost=c + 0.5 + delay / ctx.bar))
        break
    return out


def _hit_variants(ctx: _FitContext, plan: _Plan, *, R: float, h_min: float, hits: Sequence[float],
                  max_stretch: float) -> list[_Plan]:
    """Micro-stretch variants (≤``max_stretch``) that land a downbeat on a payoff hit while the ending stays
    on its anchor: ``out(d) = R - (R - o_d) / s``."""
    if plan.stretch != 1.0 or plan.lead_s:
        return []
    h, first_end = plan.pieces[0]
    out: list[_Plan] = []
    for hit in hits:
        if hit >= R - 0.5:
            continue
        for o_d, _src in _plan_out_downbeats(ctx, plan):
            if o_d >= R or abs(o_d - hit) <= _HIT_TOL_S or abs(o_d - hit) > max_stretch * (R - hit) + _HIT_TOL_S:
                continue
            s = (R - o_d) / (R - hit)
            if abs(s - 1.0) > max_stretch:
                continue
            h_s = h - R * (s - 1.0)
            if h_s < h_min - 1e-6 or first_end - h_s < 0.25 * ctx.bar:
                continue
            out.append(plan.with_entry(h_s, stretch=s))
    return out


def _render_pieces(src: np.ndarray, sr: int, pieces: list[tuple[float, float]], *, tail_s: float,
                   xfade_s: float) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Concatenate source pieces; the last one is extended by ``tail_s`` (ring-out). Junctions crossfade
    over ``xfade_s`` ending ``_PRE_S`` before the incoming downbeat onset (equal-gain when the two
    signals correlate, equal-power otherwise)."""
    ch, n = src.shape
    pre = round(_PRE_S * sr)
    xf = max(1, round(xfade_s * sr))

    def grab(a_i: int, b_i: int) -> np.ndarray:
        seg = np.zeros((ch, max(0, b_i - a_i)), dtype=np.float64)
        lo, hi = max(a_i, 0), min(b_i, n)
        if hi > lo:
            seg[:, lo - a_i:hi - a_i] = src[:, lo:hi]
        return seg

    outs: list[np.ndarray] = []
    info: list[dict[str, Any]] = []
    k = len(pieces)
    for idx, (a, b) in enumerate(pieces):
        a_i, b_i = round(a * sr), round(b * sr)
        if idx == k - 1:
            b_i += round(tail_s * sr)
        start = a_i if idx == 0 else a_i - pre
        end = b_i if idx == k - 1 else b_i - pre
        seg = grab(start, end)
        if idx > 0 and outs:
            prev = outs[-1]
            incoming = grab(start - xf, start)
            m = min(xf, prev.shape[-1], incoming.shape[-1])
            if m > 0:
                tail = prev[:, prev.shape[-1] - m:]
                inc = incoming[:, incoming.shape[-1] - m:]
                x1, x2 = tail.mean(axis=0), inc.mean(axis=0)
                den = float(np.sqrt(np.dot(x1, x1) * np.dot(x2, x2)))
                corr = float(np.dot(x1, x2) / den) if den > 0 else 0.0
                t = (np.arange(m) + 0.5) / m
                if corr > 0.6:
                    g_in, kind = 0.5 - 0.5 * np.cos(np.pi * t), "equal_gain"
                    g_out = 1.0 - g_in
                else:
                    g_in, kind = np.sin(0.5 * np.pi * t), "equal_power"
                    g_out = np.cos(0.5 * np.pi * t)
                prev[:, prev.shape[-1] - m:] = tail * g_out + inc * g_in
                info.append({"crossfade": kind, "xfade_ms": round(m * 1000 / sr, 1), "corr": round(corr, 3)})
        outs.append(seg)
    return (np.concatenate(outs, axis=1) if outs else np.zeros((ch, 0))), info


def _splice_ending(head: np.ndarray, src: np.ndarray, sr: int, *, E: float, pre_len: int, tail_s: float,
                   xfade_s: float) -> np.ndarray:
    """``head`` (output-rate material that must end exactly at the ending) trimmed/padded to ``pre_len``
    samples, then the source from the ending point ``E`` onward, joined like a bar junction (crossfade
    ending ``_PRE_S`` before the ending's onset)."""
    ch, n = src.shape
    pre = round(_PRE_S * sr)
    xf = max(1, round(xfade_s * sr))
    cut = max(0, pre_len - pre)
    body = np.zeros((ch, cut), dtype=np.float64)
    k = min(cut, head.shape[-1])
    body[:, :k] = head[:, :k]
    e_i = round(E * sr)
    a_i, b_i = e_i - pre, e_i + round(tail_s * sr)
    tail = np.zeros((ch, max(0, b_i - a_i)), dtype=np.float64)
    lo, hi = max(a_i, 0), min(b_i, n)
    if hi > lo:
        tail[:, lo - a_i:hi - a_i] = src[:, lo:hi]
    m = min(xf, cut, max(0, a_i))
    if m > 0:
        inc = src[:, a_i - m:a_i].astype(np.float64)
        t = (np.arange(m) + 0.5) / m
        body[:, cut - m:] = body[:, cut - m:] * np.cos(0.5 * np.pi * t) + inc * np.sin(0.5 * np.pi * t)
    return np.concatenate([body, tail], axis=1)


def fit(music: Any, target_duration_s: float, hit_points_s: Sequence[float] = (), *, sr: int = SAMPLE_RATE,
        analysis: MusicAnalysis | None = None, end_anchor_s: float | None = None,
        soft_end_anchor_s: float | None = None, backtime: bool = True, seams_s: Sequence[float] = (),
        fade_in_ms: float = 500.0, fade_out_ms: float = 1500.0,
        entry: Literal["auto", "any", "downbeat"] = "auto", max_stretch: float = 0.03,
        xfade_ms: float = 30.0) -> FitResult:
    """Fit a bed to a region of ``target_duration_s`` seconds (region-relative times throughout).

    * ``end_anchor_s`` — where the musical ending (button, or the downbeat where the fade begins) must
      land; default ``target - 0.5``. Beds without a musical ending land a downbeat on
      ``soft_end_anchor_s`` (default: the anchor) and fade out after it.
    * ``hit_points_s`` / ``seams_s`` — times that earn a bonus when a downbeat (or a section lift) lands
      within ±35 ms / ±1 frame; they never force an edit. A payoff hit may justify a ≤``max_stretch``
      stretch, a plain downbeat on a hit usually not.
    * ``entry`` — ``"downbeat"`` enters on a bar line (small stretch, or a delayed entry on the next
      beat); ``"any"`` may enter mid-bar under the fade-in; ``"auto"`` = downbeat when the fade-in is
      ≤150 ms.
    Returns exactly ``round(target_duration_s * sr)`` samples ``(ch, n)``; never changes pitch.
    """
    src = as_2d(music).astype(np.float32)
    ch = src.shape[0]
    L = float(target_duration_s)
    if L <= 0:
        raise ValueError("target_duration_s must be positive")
    n_out = round(L * sr)
    an = analysis or analyze_music(src, sr, detect_voice=False)
    notes: list[str] = []
    src_len = src.shape[-1] / sr
    if entry == "auto":
        entry = "downbeat" if fade_in_ms <= 150 else "any"
    h_min = 0.0 if entry == "any" else max(0.0, an.first_onset_s - _PRE_S)
    hits = [float(h) for h in hit_points_s if 0 <= h <= L]
    seams = [float(s) for s in seams_s if 0 <= s <= L]
    ctx = _FitContext(an, src, sr)
    grid_ok = ctx.D.size >= 2 and ctx.bar > 0
    natural = backtime and an.ending.kind in ("button", "fade", "sustain") and an.ending.ending_s is not None
    R = float(np.clip(end_anchor_s if end_anchor_s is not None else L - 0.5, 0.0, L))
    ending_kind = an.ending.kind if natural else "none"

    E_opts: list[tuple[float, float, float]] = []  # (E, R, extra cost)
    if natural:
        E_opts.append((float(an.ending.ending_s), R, 0.0))  # type: ignore[arg-type]
    elif backtime:
        R = float(np.clip(soft_end_anchor_s if soft_end_anchor_s is not None else R, 0.0, L))
        if grid_ok:
            cands = [d for d in ctx.D if an.first_onset_s + ctx.bar <= d <= an.sound_end_s - 0.25 * ctx.bar]
            for d in cands[-12:]:
                di = int(np.argmin(np.abs(ctx.D - d)))
                E_opts.append((float(d), R, 1.0 + (0.0 if (di - ctx.d0) % 4 == 0 else 0.4)))
        notes.append("no musical ending in this bed: a downbeat lands on the last word and the bed fades after it")
    else:
        R = L
        E_opts.append((min(src_len, h_min + L), L, 0.0) if src_len - h_min >= L else (an.sound_end_s, L, 0.0))

    best: _Plan | None = None
    if grid_ok:
        h_floor = h_min - (ctx.bar if entry == "downbeat" else 0.0)
        for E, R_e, e_cost in E_opts:
            base = _base_plans(ctx, R=R_e, E=E, h_floor=h_floor)
            scored: list[_Plan] = []
            for p0 in base:
                for p in _entry_variants(ctx, p0, R=R_e, h_min=h_min, entry=entry, max_stretch=max_stretch):
                    p.cost = _score_plan(ctx, p, R=R_e, h_min=h_min, hits=hits, seams=seams, e_cost=e_cost)
                    scored.append(p)
            if hits and entry == "any":
                for p0 in sorted(scored, key=lambda q: q.cost)[:40]:
                    for p in _hit_variants(ctx, p0, R=R_e, h_min=h_min, hits=hits, max_stretch=max_stretch):
                        p.cost = _score_plan(ctx, p, R=R_e, h_min=h_min, hits=hits, seams=seams, e_cost=e_cost)
                        scored.append(p)
            for p in scored:
                if best is None or p.cost < best.cost - 1e-9 or (
                        abs(p.cost - best.cost) <= 1e-9 and len(p.pieces) < len(best.pieces)):
                    best = p
    if best is None:
        # no bar grid (ambient/rubato) or nothing feasible: straight backtime, else tile with long crossfades
        E = float(an.ending.ending_s) if natural else min(src_len, an.sound_end_s)  # type: ignore[arg-type]
        if not natural:
            ending_kind = "none"
            R = min(R, E) if backtime else L
        if E - R >= 0:
            best = _Plan(pieces=[(E - R, E)], junctions=[], E=E, kind="straight")
            if grid_ok:
                notes.append("no bar-line plan fits the entry constraint: entered mid-bar under the fade-in")
        else:
            notes.append("bed shorter than the region and no bar grid: tiled with 1.5 s crossfades")
            return _tile_fallback(src, sr, n_out, an, fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms, notes=notes)
    s = best.stretch
    lead_n = round(best.lead_s * sr)
    post_needed = (L - R) + 0.1
    xf_s = xfade_ms / 1000.0
    if abs(s - 1.0) > 1e-5:
        # stretch only the material before the ending; the ending itself (the button) is spliced in
        # unstretched at an exact sample position, so the hit keeps its attack and lands on the anchor
        import pedalboard

        pre_part, jinfo = _render_pieces(src, sr, best.pieces, tail_s=0.0, xfade_s=xf_s)
        stretched = as_2d(pedalboard.time_stretch(np.ascontiguousarray(pre_part.astype(np.float32)), float(sr),
                                                  stretch_factor=float(s), high_quality=True,
                                                  transient_mode="crisp")).astype(np.float64)
        assembled = _splice_ending(stretched, src, sr, E=best.E, pre_len=round(R * sr) - lead_n,
                                   tail_s=post_needed, xfade_s=xf_s)
        notes.append(f"tempo changed by {100 * (s - 1):+.2f}% (Rubber Band, pitch kept) "
                     + ("for an exact bar-line entry" if entry == "downbeat" else "to land a downbeat on a hit"))
    else:
        assembled, jinfo = _render_pieces(src, sr, best.pieces, tail_s=post_needed, xfade_s=xf_s)
    out = np.zeros((ch, n_out), dtype=np.float64)
    m = min(n_out - lead_n, assembled.shape[-1])
    if m > 0:
        out[:, lead_n:lead_n + m] = assembled[:, :m]
    if lead_n:
        notes.append(f"music enters {best.lead_s * 1000:.0f} ms after the region start, on a beat")
    # fades
    fi = round(max(5.0, fade_in_ms) * sr / 1000.0)
    seg = out[:, lead_n:]
    _apply_fade(seg, min(fi, seg.shape[-1] // 2), at_start=True)
    R_i = round(R * sr)
    if natural:
        # keep the hit; fade only the ring-out that would run past the region end
        room = n_out - (R_i + round(0.05 * sr))
        fo = int(min(round(fade_out_ms * sr / 1000.0), max(0, room)))
        if fo > 0:
            _apply_fade(out, fo, at_start=False)
    elif backtime:
        start = int(np.clip(min(R_i, n_out - round(0.4 * sr)), 0, n_out))
        _apply_fade(out, n_out - start, at_start=False)
    else:
        _apply_fade(out, min(n_out, round(max(5.0, fade_out_ms) * sr / 1000.0)), at_start=False)
    _apply_fade(out, round(0.005 * sr), at_start=False)
    # report
    pairs = sorted((o, src_t) for o, src_t in (_plan_out_downbeats(ctx, best) if grid_ok else []) if 0 <= o <= L)
    downbeats_out = [o for o, _ in pairs]
    outs_arr = np.asarray(downbeats_out)
    hit_rep = []
    for hh in hits:
        if outs_arr.size:
            j = int(np.argmin(np.abs(outs_arr - hh)))
            hit_rep.append({"hit_s": _r(hh), "downbeat_s": _r(downbeats_out[j]),
                            "error_ms": float(round(1000 * (downbeats_out[j] - hh), 1)),
                            "section": ctx.is_section(pairs[j][1])})
    pieces_rep: list[FitPiece] = []
    junctions = []
    o = 0.0
    for idx, (a, b) in enumerate(best.pieces):
        pieces_rep.append(FitPiece(src_start_s=_r(a) or 0.0, src_end_s=_r(b) or 0.0,
                                   out_start_s=_r(best.lead_s + o / s) or 0.0))
        o += b - a
        if idx < len(best.junctions):
            j_out, j_in = best.junctions[idx]
            j = {"out_s": _r(best.lead_s + o / s), "from_src_s": _r(b), "to_src_s": _r(best.pieces[idx + 1][0]),
                 "similarity": round(ctx.sim(j_out, j_in), 3)}
            if idx < len(jinfo):
                j.update(jinfo[idx])
            junctions.append(j)
    if best.kind == "loop":
        notes.append(f"extended by repeating whole bars ({len(best.pieces) - 1}x)")
    elif best.kind == "skip":
        notes.append("shortened by skipping whole bars")
    return FitResult(audio=out.astype(np.float32), sr=sr, pieces=pieces_rep, stretch=s,
                     entry_src_s=best.pieces[0][0], ending_kind=ending_kind,
                     ending_src_s=best.E if backtime else None, ending_out_s=R if backtime else None,
                     downbeats_out_s=downbeats_out, hits=hit_rep, junctions=junctions, cost=best.cost,
                     lead_silence_s=best.lead_s, notes=notes)


def _tile_fallback(src: np.ndarray, sr: int, n_out: int, an: MusicAnalysis, *, fade_in_ms: float,
                   fade_out_ms: float, notes: list[str]) -> FitResult:
    xf = round(1.5 * sr)
    h = round(max(0.0, an.first_onset_s - _PRE_S) * sr)
    e = max(h + 2 * xf + 1, round(an.sound_end_s * sr))
    unit = src[:, h:e].astype(np.float64)
    if unit.shape[-1] < 2 * xf + 1:
        unit = np.pad(unit, ((0, 0), (0, 2 * xf + 1 - unit.shape[-1])))
    out = unit.copy()
    while out.shape[-1] < n_out:
        m = min(xf, unit.shape[-1] // 2, out.shape[-1])
        t = (np.arange(m) + 0.5) / m
        out[:, out.shape[-1] - m:] = (out[:, out.shape[-1] - m:] * np.cos(0.5 * np.pi * t)
                                      + unit[:, :m] * np.sin(0.5 * np.pi * t))
        out = np.concatenate([out, unit[:, m:]], axis=1)
    out = out[:, :n_out]
    _apply_fade(out, round(max(5.0, fade_in_ms) * sr / 1000), at_start=True)
    _apply_fade(out, round(max(5.0, fade_out_ms) * sr / 1000), at_start=False)
    return FitResult(audio=out.astype(np.float32), sr=sr, pieces=[], stretch=1.0, entry_src_s=h / sr,
                     ending_kind="none", ending_src_s=None, ending_out_s=None, downbeats_out_s=[], notes=notes)


# ============================================================================================ ducking
@dataclass(frozen=True)
class DuckParams:
    """Sidechain-free, look-ahead ducking drawn from speech times (music.md defaults)."""

    depth_db: float = 6.0
    attack_ms: float = 150.0  # ramp length down
    lookahead_ms: float = 180.0  # ramp starts this long before the first word
    hold_ms: float = 1200.0  # gaps shorter than this stay ducked (no pumping in breaths)
    release_ms: float = 750.0  # ramp back up into a real gap
    carve_db: float = 3.0  # 1–4 kHz dip while speech plays
    carve_lo_hz: float = 1000.0
    carve_hi_hz: float = 4000.0
    extra_ramp_ms: float = 30.0  # ramps of extra (e.g. SFX) dips

    @classmethod
    def from_music(cls, music: TimelineMusic | MusicSpec | Mapping[str, Any] | None, **overrides: Any) -> DuckParams:
        if music is None:
            return cls(**overrides)
        get = (lambda k, d=None: music.get(k, d)) if isinstance(music, Mapping) else (
            lambda k, d=None: getattr(music, k, d))
        depth = float(get("duck_db", 6.0)) if get("duck", True) else 0.0
        return cls(**{"depth_db": depth, **overrides})


def detect_speech_spans(dialogue: Any, sr: int = SAMPLE_RATE, *, threshold_db: float = -32.0,
                        min_speech_ms: float = 60.0, bridge_ms: float = 120.0) -> list[tuple[float, float]]:
    """Speech-active spans from a dialogue signal (10 ms RMS against the take's own speech level and noise
    floor). Prefer :func:`speech_spans_from_timeline` (word times) when a timeline exists."""
    x = to_mono(dialogue).astype(np.float64)
    hop = max(1, int(0.01 * sr))
    nfr = x.size // hop
    if nfr < 3:
        return []
    fr = x[: nfr * hop].reshape(nfr, hop)
    rdb = gain_to_db(np.sqrt((fr ** 2).mean(axis=1)), floor=-120.0)
    live = rdb[rdb > -90]
    if live.size == 0:
        return []
    speech_ref = float(np.percentile(live, 90))
    noise = float(np.percentile(live, 10))
    thr = max(speech_ref + threshold_db, noise + 8.0)
    on = rdb > thr
    spans: list[list[float]] = []
    i = 0
    while i < nfr:
        if on[i]:
            j = i
            while j < nfr and on[j]:
                j += 1
            spans.append([i * hop / sr, j * hop / sr])
            i = j
        else:
            i += 1
    merged: list[list[float]] = []
    for s in spans:
        if merged and s[0] - merged[-1][1] < bridge_ms / 1000.0:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    return [(a, b) for a, b in merged if (b - a) * 1000.0 >= min_speech_ms]


def speech_spans_from_timeline(timeline: Timeline, *, bridge_s: float = 0.0) -> list[tuple[float, float]]:
    """Output-time speech spans from the timeline's word map (kept words), merged where they touch."""
    spans = sorted((float(w.out_start), float(w.out_end)) for w in timeline.word_map.values() if w is not None)
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a - merged[-1][1] <= bridge_s + 1e-9:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def duck_amount(n: int, sr: int, spans: Sequence[tuple[float, float]], params: DuckParams | None = None
                ) -> np.ndarray:
    """Duck amount 0..1 per sample (1 = fully ducked) from speech spans, with look-ahead, hold and
    raised-cosine attack/release, computed at 1 kHz control rate and interpolated."""
    p = params or DuckParams()
    ctrl = 1000
    nc = math.ceil(n * ctrl / sr) + 2
    a = np.zeros(nc, dtype=np.float64)
    merged: list[list[float]] = []
    for s, e in sorted((float(s), float(e)) for s, e in spans if e > s):
        if merged and s - merged[-1][1] < p.hold_ms / 1000.0:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    att, look, rel = p.attack_ms / 1000.0, p.lookahead_ms / 1000.0, p.release_ms / 1000.0
    for s, e in merged:
        r0 = s - look
        r1 = min(s, r0 + att)
        k0, k3 = math.floor(r0 * ctrl), math.ceil((e + rel) * ctrl)
        seg_idx = np.arange(max(0, k0), min(nc, k3 + 1))
        if seg_idx.size == 0:
            continue
        t = seg_idx / ctrl
        v = np.ones_like(t)
        up = t < r1
        if r1 > r0:
            u = np.clip((t[up] - r0) / (r1 - r0), 0.0, 1.0)
            v[up] = 0.5 - 0.5 * np.cos(np.pi * u)
        down = t > e
        if rel > 0:
            u = np.clip((t[down] - e) / rel, 0.0, 1.0)
            v[down] = 0.5 + 0.5 * np.cos(np.pi * u)
        a[seg_idx] = np.maximum(a[seg_idx], v)
    tc = np.arange(nc) / ctrl
    ts = np.arange(n) / sr
    return np.interp(ts, tc, a).astype(np.float32)


def ducking_envelope(dialogue: Any = None, params: DuckParams | Mapping[str, Any] | None = None, *,
                     sr: int = SAMPLE_RATE, speech_spans: Sequence[tuple[float, float]] | None = None,
                     n: int | None = None, extra_spans: Sequence[tuple[float, float, float]] = (),
                     return_amount: bool = False) -> Any:
    """Linear gain curve for the music bed (length ``n`` or the dialogue length).

    ``dialogue`` may be an array or a WAV path; ``speech_spans`` (seconds, e.g. from
    :func:`speech_spans_from_timeline`) take precedence over detection. ``extra_spans`` are further
    ``(start_s, end_s, depth_db)`` dips (e.g. 2–4 dB under each SFX, see ``sfx.sfx_duck_spans``).
    With ``return_amount`` returns ``(gain, amount)`` where amount (0..1) drives :func:`carve_speech_band`.
    """
    p = params if isinstance(params, DuckParams) else DuckParams(**dict(params or {}))
    dia: np.ndarray | None = None
    if dialogue is not None:
        dia = (load_audio(dialogue, sr=sr, channels=1)[0] if isinstance(dialogue, (str, os.PathLike))
               else to_mono(dialogue))
    if n is None:
        if dia is not None:
            n = dia.size
        elif speech_spans:
            n = math.ceil(max(e for _, e in speech_spans) * sr) + int(p.release_ms * sr / 1000)
        else:
            raise ValueError("give dialogue, n or speech_spans")
    if speech_spans is not None:
        spans = list(speech_spans)
    else:
        spans = detect_speech_spans(dia, sr) if dia is not None else []
    amt = duck_amount(n, sr, spans, p)
    gdb = -p.depth_db * amt.astype(np.float64)
    ramp = p.extra_ramp_ms / 1000.0
    for s, e, depth in extra_spans:
        if depth <= 0 or e <= s:
            continue
        i0, i1 = max(0, int((s - ramp) * sr)), min(n, int((e + ramp) * sr))
        if i1 <= i0:
            continue
        t = np.arange(i0, i1) / sr
        v = np.ones(t.size)
        up = t < s
        v[up] = 0.5 - 0.5 * np.cos(np.pi * np.clip((t[up] - (s - ramp)) / ramp, 0, 1))
        dn = t > e
        v[dn] = 0.5 + 0.5 * np.cos(np.pi * np.clip((t[dn] - e) / ramp, 0, 1))
        gdb[i0:i1] -= depth * v
    gain = np.power(10.0, gdb / 20.0).astype(np.float32)
    return (gain, amt) if return_amount else gain


def carve_speech_band(bed: Any, amount: np.ndarray, *, sr: int = SAMPLE_RATE, depth_db: float = 3.0,
                      lo_hz: float = 1000.0, hi_hz: float = 4000.0) -> np.ndarray:
    """Dynamic 1–4 kHz dip on the bed, scaled by ``amount`` (0..1): zero-phase band split, so the
    un-ducked bed is reconstructed exactly."""
    from scipy.signal import butter, sosfiltfilt

    b = as_2d(bed).astype(np.float64)
    if depth_db <= 0 or not np.any(amount > 0):
        return b.astype(np.float32)
    sos = butter(2, [lo_hz, min(hi_hz, 0.45 * sr)], btype="bandpass", fs=sr, output="sos")
    band = sosfiltfilt(sos, b, axis=-1)
    g = np.power(10.0, -depth_db * np.asarray(amount, dtype=np.float64)[: b.shape[-1]] / 20.0)
    if g.size < b.shape[-1]:
        g = np.pad(g, (0, b.shape[-1] - g.size), constant_values=1.0)
    return (b - band * (1.0 - g)[None, :]).astype(np.float32)


def loudness_over_spans(audio: Any, sr: int, spans: Sequence[tuple[float, float]]) -> float | None:
    """Loudness (LUFS) of ``audio`` restricted to ``spans``: K-weighted power averaged over the span samples.
    Measuring both the voice and the (ducked) bed this way gives the doctrine's "LU below speech during
    speech runs"; None when the spans hold no audio."""
    a = as_2d(audio)
    n = a.shape[-1]
    mask = np.zeros(n, dtype=bool)
    for s0, s1 in spans:
        i0, i1 = max(0, round(s0 * sr)), min(n, round(s1 * sr))
        if i1 > i0:
            mask[i0:i1] = True
    if not mask.any():
        return None
    p = float((k_weight(a, sr) ** 2).sum(axis=0)[mask].mean())
    return -0.691 + 10.0 * math.log10(p) if p > 1e-20 else None


def duck_bed(bed: Any, *, sr: int = SAMPLE_RATE, dialogue: Any = None,
             speech_spans: Sequence[tuple[float, float]] | None = None, params: DuckParams | None = None,
             extra_spans: Sequence[tuple[float, float, float]] = (), speech_lufs: float | None = None,
             level_lu_under_speech: float | None = None, info: dict[str, Any] | None = None) -> np.ndarray:
    """Duck (and carve) a bed under speech; returns the same shape as ``bed``.

    With ``level_lu_under_speech`` the ducked bed is then calibrated exactly: its loudness over the speech
    spans is set to ``speech_lufs + level_lu_under_speech`` (``speech_lufs`` defaults to the dialogue's own
    loudness over the same spans). ``info`` (optional) receives the measured values and the applied gain."""
    p = params or DuckParams()
    b = np.asarray(bed, dtype=np.float32)
    n = b.shape[-1]
    dia = None
    if dialogue is not None:
        dia = (load_audio(dialogue, sr=sr, channels=1)[0] if isinstance(dialogue, (str, os.PathLike))
               else to_mono(dialogue))
    spans = list(speech_spans) if speech_spans is not None else (detect_speech_spans(dia, sr) if dia is not None
                                                                 else [])
    gain, amt = ducking_envelope(None, p, sr=sr, speech_spans=spans, n=n, extra_spans=extra_spans,
                                 return_amount=True)
    out = carve_speech_band(b, amt, sr=sr, depth_db=p.carve_db, lo_hz=p.carve_lo_hz, hi_hz=p.carve_hi_hz)
    out = out * gain[None, :]
    if level_lu_under_speech is not None and spans:
        ref = speech_lufs if speech_lufs is not None else (
            loudness_over_spans(dia, sr, spans) if dia is not None else None)
        measured = loudness_over_spans(out, sr, spans)
        if ref is not None and measured is not None:
            g = (ref + level_lu_under_speech) - measured
            out = out * np.float32(db_to_gain(g))
            if info is not None:
                info.update({"speech_lufs": round(ref, 2), "bed_under_speech_lufs": round(measured + g, 2),
                             "calibration_gain_db": round(g, 2)})
    return out[0] if b.ndim == 1 else out


def bed_gain_db(bed: Any, sr: int, *, speech_lufs: float, level_lu_under_speech: float = -18.0,
                duck_db: float = 6.0, duck: bool = True) -> float:
    """Gain that puts the bed's un-ducked loudness at ``speech + level + duck_db`` (so under speech,
    after ducking, it sits ``level_lu_under_speech`` LU below the voice)."""
    target = speech_lufs + level_lu_under_speech + (duck_db if duck else 0.0)
    measured = integrated_lufs(bed, sr)
    if measured is None:
        return 0.0
    return float(target - measured)


# ============================================================================================ scaffold API
def find_music(job: Job, doc: CutDocument, *, duration_s: float, settings: Settings | None = None, n: int = 3,
               client: ElevenLabsMusicClient | None = None, index: TakeIndex | None = None,
               end_anchor_s: float | None = None, hits_s: Sequence[float] = (),
               mode: Literal["plan", "prompt"] = "plan", model_id: str = MUSIC_MODEL,
               detect_voice: bool = True) -> list[MusicSpec]:
    """Candidate beds (with downloaded, licensed assets under ``assets/music/``), best-measured first.

    * ``doc.audio.music.source == "none"`` → ``[]``; a spec that already references a registered asset is
      returned as the single candidate; ``file``/``creator``/``library`` without an asset → ``[]`` (import
      it with :func:`import_music_file`).
    * Otherwise up to ``n`` ElevenLabs beds that differ in instrumentation (music.md: three candidates),
      built from the brief, style and measured energy. Without an ElevenLabs key → ``[]`` (no music is a
      valid answer) and a trace note.
    """
    m = doc.audio.music
    if m is not None and m.source == "none":
        return []
    if m is not None and (m.asset is not None or m.asset_id):
        asset = m.asset or job.load_asset(m.asset_id or "")
        return [m.model_copy(update={"asset": asset})] if asset is not None else []
    if m is not None and m.source in ("file", "creator", "library", "epidemic"):
        job.trace("music_unavailable", reason=f"source {m.source!r} needs an imported, licensed asset", stage="music")
        return []
    s = settings or get_settings()
    if client is None and not s.has_key("elevenlabs"):
        job.trace("music_unavailable", reason="no ElevenLabs key configured", stage="music")
        return []
    if index is None:
        try:
            index = job.load_index()
        except Exception:
            index = None
    cl = client or ElevenLabsMusicClient.from_settings(s)
    specs: list[tuple[float, MusicSpec]] = []
    for v in range(max(1, n)):
        req = build_music_request(doc, duration_s=duration_s, index=index, end_anchor_s=end_anchor_s,
                                  hits_s=hits_s, variant=v, mode=mode, model_id=model_id)
        try:
            spec = generate_music(job, req, settings=s, client=cl, detect_voice=detect_voice)
        except (ElevenLabsError, ValueError) as e:
            job.trace("music_generation_failed", variant=v, error=str(e)[:300], stage="music")
            continue
        try:
            score = candidate_score(music_analysis_for(job, spec.asset or spec.asset_id or ""))
        except Exception:
            score = 0.0
        base = m.model_dump(exclude={"asset", "asset_id", "source", "prompt"}) if m is not None else {}
        specs.append((score, MusicSpec(**{**base, "asset_id": spec.asset_id, "source": "elevenlabs",
                                           "prompt": spec.prompt, "mood": spec.mood or base.get("mood"),
                                           "asset": spec.asset})))
    specs.sort(key=lambda t: -t[0])
    return [sp for _, sp in specs]


def _region(spec: MusicSpec, timeline: Timeline) -> tuple[Fraction, Fraction]:
    tm = timeline.music
    if tm is not None:
        return to_fraction(tm.out_start), to_fraction(tm.out_end)
    start = Fraction(0)
    if spec.start_word:
        ws = timeline.word_span(spec.start_word)
        if ws is not None:
            start = to_fraction(ws.out_start)
    return start, to_fraction(timeline.duration)


def _last_word(spec: MusicSpec, timeline: Timeline) -> tuple[float, float] | None:
    if spec.end_word:
        ws = timeline.word_span(spec.end_word)
        if ws is not None:
            return float(ws.out_start), float(ws.out_end)
    spans = [w for w in timeline.word_map.values() if w is not None]
    if not spans:
        return None
    last = max(spans, key=lambda w: w.out_start)
    return float(last.out_start), float(last.out_end)


def fit_music_for_timeline(job: Job, spec: MusicSpec, timeline: Timeline, *, sr: int = SAMPLE_RATE
                           ) -> tuple[FitResult, Fraction]:
    """Fit ``spec``'s bed to the timeline's music region; returns (fit, exact region start seconds)."""
    asset = spec.asset or (job.load_asset(spec.asset_id) if spec.asset_id else None)
    if asset is None:
        raise FileNotFoundError("music spec has no registered asset")
    tm = timeline.music
    path = Path(tm.asset_path) if (tm is not None and tm.asset_path) else job.path(asset.path or "")
    audio = load_audio(path, sr=sr, channels=2)
    analysis = music_analysis_for(job, asset, audio=audio, sr=sr)
    start, end = _region(spec, timeline)
    L = float(end - start)
    lw = _last_word(spec, timeline)
    anchor = soft = None
    if lw is not None:
        anchor = min(max(0.0, lw[0] - float(start)), L)
        soft = min(max(0.0, lw[1] - float(start)), L)
    hits_abs = [float(h) for h in (tm.hits if tm is not None else [])]
    if not hits_abs:
        for w in spec.hit_word_ids:
            ws = timeline.word_span(w)
            if ws is not None:
                hits_abs.append(float(ws.out_start))
    hits = [h - float(start) for h in hits_abs]
    seams = [float(s) - float(start) for s in timeline.seams]
    fade_in = float(tm.fade_in_ms if tm is not None else spec.fade_in_ms)
    fade_out = float(tm.fade_out_ms if tm is not None else spec.fade_out_ms)
    entry: Literal["auto", "any", "downbeat"] = "auto" if float(start) > 0 else "any"
    fr = fit(audio, L, hits, sr=sr, analysis=analysis, end_anchor_s=anchor, soft_end_anchor_s=soft,
             backtime=spec.backtime, seams_s=seams, fade_in_ms=fade_in, fade_out_ms=fade_out, entry=entry)
    return fr, start


def render_music_bed(job: Job, spec: MusicSpec, timeline: Timeline, *, sr: int = 48000,
                     speech_lufs: float | None = None, level: bool = True, mono: bool = False) -> Any:
    """The fitted, faded music bed for the timeline (float32 numpy, pre-ducking): ``(2, n)`` with
    ``n = sample_index(timeline.duration, sr)`` (``(n,)`` with ``mono=True``).

    With ``level`` the bed is scaled so that, after ducking by ``duck_db``, it sits
    ``level_lu_under_speech`` LU below speech; ``speech_lufs`` is the processed dialogue's short-term
    loudness over speech (default: the -14 LUFS delivery target, which the final mix is normalised to).
    Then apply :func:`duck_bed` with ``level_lu_under_speech`` (and the processed voice or its loudness) for
    an exact level under speech (compile/audio owns the mix)."""
    n_total = sample_index(timeline.duration, sr)
    out = np.zeros((2, n_total), dtype=np.float32)
    if spec.source == "none" or (spec.asset is None and not spec.asset_id):
        return out[0] if mono else out
    fr, start = fit_music_for_timeline(job, spec, timeline, sr=sr)
    i0 = sample_index(start, sr)
    seg = to_channels(fr.audio, 2)
    m = min(seg.shape[-1], max(0, n_total - i0))
    out[:, i0:i0 + m] = seg[:, :m]
    tm = timeline.music
    gain_db = 0.0
    if level and m > 0:
        lvl = float(tm.level_lu_under_speech if tm is not None else spec.level_lu_under_speech)
        duck = bool(tm.duck if tm is not None else spec.duck)
        ddb = float(tm.duck_db if tm is not None else spec.duck_db)
        gain_db = bed_gain_db(seg[:, :m], sr, speech_lufs=-14.0 if speech_lufs is None else speech_lufs,
                              level_lu_under_speech=lvl, duck_db=ddb, duck=duck)
        out *= np.float32(db_to_gain(gain_db))
    with contextlib.suppress(Exception):  # tracing must never break a render
        job.trace("music_fit", asset_id=spec.asset_id or (spec.asset.id if spec.asset else None),
                  region_start_s=_r(float(start), 4), gain_db=_r(gain_db, 2), **fr.report())
    return out[0] if mono else out
