"""Sound effects: sparse, event-locked, licensed one-shots placed by their sync point and levelled against
the local dialogue (sfx.md: "felt more than heard"; the voice wins every collision).

Sources
-------
* **Procedural library** (always available, self-generated, licence-clean): whoosh, swoosh_down, pop,
  riser, ding, click, tick, impact, shutter and typing, synthesised with numpy at 48 kHz. These are dry,
  click-free, DC-free one-shots peak-normalised to -1 dBFS with a known sync point. Design notes:

  - whoosh / swoosh_down: pink noise shaped in the STFT domain by a log-frequency Gaussian band whose
    centre sweeps up to the peak (whoosh, swells and peaks late, "no low boom") or down (swoosh_down,
    peaks early), plus a softer body band an octave below and a gentle constant-power L→R pan with
    partially decorrelated channels (mono-compatible). Sync = the loudness peak.
  - riser: noise band sweeping 250 Hz → 7 kHz with a dB-linear accelerating swell and widening stereo,
    ending in a dead stop (sync = the end, so it stops on the reveal frame). Atonal by default so it
    cannot clash with a bed.
  - pop: phase-continuous rising sine chirp (bubble pop) + a faint noise tick for definition.
  - ding: soft glassy bell (partials 1, 2, 2.76, 5.4 with individual decays and a slow beating pair);
    its pitch follows the music bed's key when known (sfx.md: dings in key with the bed).
  - click (UI) and tick (wood block): short modal/noise-burst transients with sub-millisecond attacks.
  - impact: pitch-dropping body (140 → 62 Hz) saturated for harmonics, a 190–280 Hz "knock" resonance
    and a 150–900 Hz thud (over a quarter of the energy sits where phone speakers play), a small high
    click, and nothing below 45 Hz (no earbud-only sub boom).
  - shutter: two bandpassed mechanical clicks ~75 ms apart with a short rattle and a low "thunk".
  - typing: varied key clicks at irregular 85–170 ms intervals (seeded, so repeats never loop).
* **ElevenLabs sound effects** (``POST /v1/sound-generation?output_format=pcm_48000``, model
  ``eleven_text_to_sound_v2``; body ``text``, ``duration_seconds`` 0.5–30, ``prompt_influence`` 0–1,
  ``loop``; verified against the API reference, Sep 2026). Prompts follow sfx.md: family + material +
  character + envelope + "one-shot, dry, no reverb tail, no music, no voice", always with a duration and
  a high prompt influence. ``n`` candidates are generated and the best is chosen by measurement
  (length vs the family, reverb-tail decay, sub-bass share, clipping, peak position, voice detector);
  the winner is DC-filtered, trimmed to a few ms before its onset with a short fade, and normalised.
  Output is ``pcm_48000`` (lossless at the engine rate; ElevenLabs only offers 48 kHz lossless for non-looping
  effects), which arrives as interleaved stereo s16le (verified live, Sep 2026).

``auto`` sourcing uses the procedural library for families where synthesis is already ideal (pop, click,
tick, ding) and ElevenLabs (when a key is configured) for organic families (whoosh, swoosh_down, riser,
impact, shutter, typing, and any unknown kind), falling back to procedural. Every asset is cached per job
under ``assets/sfx/<asset_id>.wav`` with ``.licence.json`` (invariant 5) and ``.analysis.json`` (sync
point, levels, candidate metrics) and registered for ops to reference by ID.

Levels (:func:`recommend_level`)
--------------------------------
Measured against the dialogue around the event (sfx.md priors): transients (pop, click, tick, ding,
impact, shutter) peak 6–9 dB under the local speech peak; textures (whoosh, riser, typing) sit ≥10 LU
(riser 12, typing 15) under local short-term speech loudness where they overlap words and about 6 LU
under in a clean gap (momentary-max loudness of the effect vs the 3 s short-term loudness of the voice).
``SfxCue.gain_db`` is a trim relative to that recommendation (the default −12 means "as recommended");
the voice is protected by a ceiling (transients ≥2 dB under the local speech peak, textures ≥4 LU under
the voice). Without dialogue the delivery targets are assumed (−14 LUFS short-term, −3 dBFS peaks).

:func:`render_sfx_track` returns ``(2, n)`` float32 aligned to the timeline, each effect's sync point on
its ``out_t`` sample; :func:`sfx_duck_spans` gives the extra 3 dB bed dips for
:func:`studio.audio.music.ducking_envelope`.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from studio.audio.music import (
    ElevenLabsError,
    ElevenLabsHTTP,
    _now_iso,
    as_2d,
    decode_elevenlabs_audio,
    detect_speech_spans,
    gain_to_db,
    load_audio,
    max_momentary_lufs,
    peak_dbfs,
    raised_cosine,
    sha256_file,
    short_term_lufs_at,
    to_channels,
    to_mono,
    write_wav,
)
from studio.config import Settings, get_settings
from studio.doc.model import AssetRef, Licence, SfxCue
from studio.timebase import SAMPLE_RATE, sample_index

if TYPE_CHECKING:  # pragma: no cover
    from studio.compile.models import Timeline
    from studio.jobs import Job

__all__ = [
    "SFX_ENDPOINT", "SFX_MODEL", "SFX_OUTPUT_FORMAT", "DEFAULT_CUE_GAIN_DB", "SfxError", "SfxFamily", "FAMILIES",
    "family_for", "SfxClip", "synthesize", "prepare_clip", "detect_sync", "score_clip", "key_to_pitch",
    "ElevenLabsSfxClient", "generate_sfx", "procedural_sfx", "resolve_sfx", "load_sfx", "LevelAdvice",
    "recommend_level", "level_guidance", "SfxPlacement", "place_sfx", "render_sfx_track", "sfx_duck_spans",
]

SFX_ENDPOINT = "/v1/sound-generation"
SFX_MODEL = "eleven_text_to_sound_v2"
SFX_OUTPUT_FORMAT = "pcm_48000"
SFX_MIN_S, SFX_MAX_S = 0.5, 30.0
SYNTH_VERSION = 1
#: ``SfxCue.gain_db`` default: means "use the recommended level" (the value is a trim relative to it)
DEFAULT_CUE_GAIN_DB = -12.0
SFX_TERMS_URL = "https://elevenlabs.io/terms-of-use"
SFX_TERMS_NAME = "ElevenLabs Terms of Service (Sound Effects outputs)"
#: ElevenLabs SFX ``pcm_48000`` is interleaved stereo s16le (verified live, Sep 2026)
_PCM_DEFAULT_CHANNELS_SFX = 2
_DRY = "one-shot, dry, no reverb tail, no music, no voice"


class SfxError(RuntimeError):
    """No licensed asset could be resolved for a cue."""


@dataclass(frozen=True)
class SfxFamily:
    kind: str
    duration_s: float
    sync: Literal["onset", "peak", "end"]
    texture: bool  # textures are levelled by loudness, transients by peak
    peak_under_speech_db: float  # transients: peak this many dB under the local speech peak
    lu_under_speech_overlap: float  # textures overlapping words: LU under local short-term speech
    lu_under_speech_gap: float  # textures in a clean gap
    max_decay_s: float  # longer tails than this read as reverb/smear
    prompt: str
    prompt_influence: float = 0.75
    generative: bool = False  # prefer ElevenLabs in ``auto`` sourcing
    tonal: bool = False
    vary: bool = False  # repeated cues get subtle per-cue variation
    aliases: tuple[str, ...] = ()
    peak_frac: float | None = None  # expected loudness-peak position (fraction of the active duration)
    description: str = ""


FAMILIES: dict[str, SfxFamily] = {f.kind: f for f in (
    SfxFamily("whoosh", 0.55, "peak", True, -8.0, -10.0, -6.0, 0.25,
              f"Soft airy whoosh, short, swells and peaks at the very end, no low boom, {_DRY}", 0.75,
              generative=True, aliases=("swish", "swoosh", "woosh", "transition", "swoosh_up", "whoosh_up"),
              peak_frac=0.72, description="motion the viewer sees: a card sliding in, a whip, a style punch"),
    SfxFamily("swoosh_down", 0.6, "peak", True, -8.0, -10.0, -6.0, 0.35,
              f"Soft downward swoosh, air falling in pitch, peaks early and trails off quickly, {_DRY}", 0.75,
              generative=True, aliases=("swoosh-down", "whoosh_down", "whoosh-down", "downswoosh", "swoosh down",
                                        "exit"), peak_frac=0.3, description="an element leaving or dropping away"),
    SfxFamily("pop", 0.12, "onset", False, -7.0, -12.0, -8.0, 0.12,
              f"Soft bubbly pop, a single short blip, {_DRY}", 0.8,
              aliases=("bubble", "blip", "pop_in", "appear"), description="one key text element or emoji appearing"),
    SfxFamily("riser", 1.5, "end", True, -8.0, -12.0, -7.0, 0.1,
              f"Tension riser, filtered noise sweeping upward and building, ends abruptly with a dead stop, {_DRY}",
              0.8, generative=True, aliases=("rise", "build", "uplifter", "tension"),
              description="only into a real visual reveal; stops dead on the reveal frame"),
    SfxFamily("ding", 0.9, "onset", False, -9.0, -12.0, -8.0, 1.2,
              "Soft glassy ding, a single gentle bell tone, short natural decay, dry, no music, no voice", 0.8,
              tonal=True, aliases=("chime", "bell", "notification", "notification_ding", "soft_ding"),
              description="a list marker or completion chime; in key with the bed"),
    SfxFamily("click", 0.05, "onset", False, -8.0, -12.0, -8.0, 0.05,
              f"Crisp soft UI click, single, {_DRY}", 0.8, vary=True,
              aliases=("ui_click", "mouse_click", "button_click", "tap"), description="UI actions on screen"),
    SfxFamily("tick", 0.15, "onset", False, -6.0, -12.0, -8.0, 0.1,
              "Soft wooden tick, a mallet on a wood block, single hit, dry, one-shot", 0.8,
              aliases=("wood_tick", "woodblock", "wood_block", "counter", "list_tick", "list"),
              description="list marker: the same tick on every counter"),
    SfxFamily("impact", 0.8, "onset", False, -8.0, -12.0, -8.0, 0.6,
              f"Subtle soft impact, a short muffled thump with a little punch, no sub-bass boom, {_DRY}", 0.75,
              generative=True, aliases=("hit", "thump", "boom", "thud", "subtle_impact", "punch"),
              description="once, for a hard cut to a big number"),
    SfxFamily("shutter", 0.22, "onset", False, -8.0, -12.0, -8.0, 0.15,
              "Camera shutter click, a single DSLR shutter release, dry, no music, no voice", 0.8,
              generative=True, aliases=("camera", "camera_shutter", "snapshot", "screenshot", "photo"),
              description="a real screenshot or photo landing"),
    SfxFamily("typing", 1.2, "onset", True, -8.0, -15.0, -9.0, 0.1,
              "Light laptop keyboard typing, a short burst of soft key presses, close, dry, no music, no voice", 0.7,
              generative=True, vary=True, aliases=("keyboard", "keys", "typing_keyboard", "type"),
              description="only under on-screen typing"),
)}


def _norm_kind(kind: str) -> str:
    return re.sub(r"[\s\-]+", "_", (kind or "").strip().lower())


def family_for(kind: str) -> SfxFamily | None:
    """The family for a cue kind (aliases accepted), or None for kinds outside the library."""
    k = _norm_kind(kind)
    if k in FAMILIES:
        return FAMILIES[k]
    for f in FAMILIES.values():
        if k in {_norm_kind(a) for a in f.aliases}:
            return f
    return None


# ============================================================================================ synthesis
@dataclass
class SfxClip:
    audio: np.ndarray  # (ch, n) float32
    sr: int
    sync_s: float
    kind: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.audio.shape[-1] / self.sr


def _pink(n: int, rng: np.random.Generator) -> np.ndarray:
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(spec.size, dtype=np.float64)
    f[0] = 1.0
    x = np.fft.irfft(spec / np.sqrt(f), n)
    return x / (np.std(x) + 1e-12)


def _band_sweep(noise: np.ndarray, sr: int, fc: np.ndarray, bw_oct: float) -> np.ndarray:
    """Time-varying log-Gaussian band-pass via STFT shaping; ``fc`` is the centre per output sample."""
    from scipy.signal import istft, stft

    nper, hop = 1024, 256
    f, t, Z = stft(noise, fs=sr, nperseg=nper, noverlap=nper - hop, boundary="even", padded=True)
    tc = np.clip((t * sr).astype(int), 0, fc.size - 1)
    centres = fc[tc]
    sigma = bw_oct / 2.355
    ff = np.maximum(f, 1.0)[:, None]
    G = np.exp(-0.5 * (np.log2(ff / centres[None, :]) / sigma) ** 2)
    G[0] = 0.0
    _, y = istft(Z * G, fs=sr, nperseg=nper, noverlap=nper - hop, boundary=True)
    y = y[: noise.size]
    return np.pad(y, (0, noise.size - y.size))


def _hpf(x: np.ndarray, sr: int, hz: float, order: int = 2) -> np.ndarray:
    from scipy.signal import butter, sosfiltfilt

    sos = butter(order, hz, btype="highpass", fs=sr, output="sos")
    return sosfiltfilt(sos, x, axis=-1)


def _bpf(x: np.ndarray, sr: int, lo: float, hi: float, order: int = 2) -> np.ndarray:
    from scipy.signal import butter, sosfilt

    sos = butter(order, [lo, min(hi, 0.45 * sr)], btype="bandpass", fs=sr, output="sos")
    return sosfilt(sos, x, axis=-1)


def _lpf(x: np.ndarray, sr: int, hz: float, order: int = 2) -> np.ndarray:
    from scipy.signal import butter, sosfilt

    sos = butter(order, min(hz, 0.45 * sr), btype="lowpass", fs=sr, output="sos")
    return sosfilt(sos, x, axis=-1)


def _pan(mono: np.ndarray, pos: np.ndarray | float, *, side: np.ndarray | None = None, width: float = 0.0
         ) -> np.ndarray:
    """Constant-power pan (``pos`` −1..1) with optional decorrelated ``side`` signal for width."""
    p = np.broadcast_to(np.asarray(pos, dtype=np.float64), mono.shape)
    ang = (p + 1.0) * np.pi / 4.0
    left, right = mono * np.cos(ang) * math.sqrt(2), mono * np.sin(ang) * math.sqrt(2)
    if side is not None and width:
        left, right = left + width * side, right - width * side
    return np.stack([left, right])


def _decay(n: int, sr: int, tau_s: float, attack_s: float = 0.0005) -> np.ndarray:
    t = np.arange(n) / sr
    env = np.exp(-t / max(tau_s, 1e-5))
    if attack_s > 0:
        env *= np.minimum(1.0, t / attack_s)
    return env


def _finish(x: np.ndarray, sr: int, *, fade_in_s: float = 0.0005, fade_out_s: float = 0.005,
            peak_dbfs_target: float = -1.0, hpf_hz: float | None = 20.0) -> np.ndarray:
    """DC/rumble removal, click-free edges, peak normalisation; returns float32 (ch, n)."""
    a = as_2d(x).astype(np.float64)
    if hpf_hz:
        a = _hpf(a, sr, hpf_hz)
    a -= a.mean(axis=-1, keepdims=True)
    n = a.shape[-1]
    fi, fo = max(1, round(fade_in_s * sr)), max(1, round(fade_out_s * sr))
    a[:, :fi] *= raised_cosine(fi)[None, :]
    a[:, n - fo:] *= raised_cosine(fo, rising=False)[None, :]
    pk = float(np.max(np.abs(a))) if a.size else 0.0
    if pk > 0:
        a *= 10 ** (peak_dbfs_target / 20.0) / pk
    return a.astype(np.float32)


def _synth_whoosh(sr: int, dur: float, rng: np.random.Generator, *, down: bool = False) -> tuple[np.ndarray, float]:
    n = round(dur * sr)
    t = np.arange(n) / sr
    tp = (0.3 if down else 0.72) * dur
    if down:
        fc = np.where(t < tp, 3200.0 * (1400.0 / 3200.0) ** (t / tp),
                      1400.0 * (280.0 / 1400.0) ** np.clip((t - tp) / (dur - tp), 0, 1))
        env = np.where(t < tp, (t / tp) ** 1.5, np.exp(-(t - tp) / 0.16))
    else:
        fc = np.where(t < tp, 350.0 * (2800.0 / 350.0) ** ((t / tp) ** 1.3),
                      2800.0 * (1800.0 / 2800.0) ** np.clip((t - tp) / (dur - tp), 0, 1))
        env = np.where(t < tp, (t / tp) ** 2.2, np.exp(-(t - tp) / 0.06))
    air = _band_sweep(_pink(n, rng), sr, fc, 1.6)
    body = _band_sweep(_pink(n, rng), sr, fc / 2.2, 1.2) * 10 ** (-8 / 20)
    side = _band_sweep(_pink(n, rng), sr, fc, 1.6)
    mono = (air + body) * env
    pos = np.linspace(0.35, -0.35, n) if down else np.linspace(-0.35, 0.35, n)
    st = _pan(mono, pos, side=side * env, width=0.25)
    st = _hpf(st, sr, 150.0 if not down else 120.0)
    return st, float(tp)


def _synth_riser(sr: int, dur: float, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    n = round(dur * sr)
    t = np.arange(n) / sr
    u = t / dur
    fc = 250.0 * (7000.0 / 250.0) ** (u ** 1.6)
    env = 10 ** ((-32.0 + 32.0 * u ** 1.4) / 20.0)
    a = _band_sweep(_pink(n, rng), sr, fc, 1.3)
    b = _band_sweep(_pink(n, rng), sr, np.minimum(fc * 2.0, 16000.0), 1.0) * 10 ** (-9 / 20)
    side = _band_sweep(_pink(n, rng), sr, fc, 1.3)
    mono = (a + b) * env
    st = _pan(mono, 0.0, side=side * env * u, width=0.5)
    st = _hpf(st, sr, 120.0)
    return st, float(dur)


def _synth_pop(sr: int, dur: float, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    n = round(dur * sr)
    t = np.arange(n) / sr
    f = 420.0 * (1100.0 / 420.0) ** np.clip(t / 0.025, 0.0, 1.0)
    ph = 2 * np.pi * np.cumsum(f) / sr
    env = _decay(n, sr, 0.018, 0.0008)
    x = np.sin(ph) * env + 0.2 * np.sin(2 * ph) * env
    tick = _hpf(rng.standard_normal(n), sr, 3000.0) * _decay(n, sr, 0.0006, 0.0001) * 0.12
    return x + tick, 0.0008


def _synth_ding(sr: int, dur: float, rng: np.random.Generator, pitch_hz: float) -> tuple[np.ndarray, float]:
    n = round(dur * sr)
    t = np.arange(n) / sr
    x = np.zeros(n)
    for ratio, amp, t60 in ((1.0, 1.0, 0.9), (2.0, 0.25, 0.5), (2.76, 0.12, 0.3), (5.4, 0.04, 0.15)):
        f = pitch_hz * ratio
        if f >= 0.45 * sr:
            continue
        x += amp * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi)) * np.exp(-6.91 * t / t60)
    x += 0.3 * np.sin(2 * np.pi * (pitch_hz + 0.8) * t) * np.exp(-6.91 * t / 0.9)  # slow beating = warmth
    attack = np.minimum(1.0, t / 0.003)
    x *= 0.5 - 0.5 * np.cos(np.pi * attack)
    x = _lpf(x, sr, 7000.0, order=1)
    return x, 0.0015


def _synth_click(sr: int, dur: float, rng: np.random.Generator, *, var: float = 0.0) -> tuple[np.ndarray, float]:
    n = round(dur * sr)
    t = np.arange(n) / sr
    burst = _bpf(rng.standard_normal(n), sr, 1500.0 * (1 + 0.2 * var), 7000.0) * _decay(n, sr, 0.0012, 0.0002)
    f1 = 2200.0 * (1 + 0.15 * var)
    body = 0.5 * np.sin(2 * np.pi * f1 * t) * _decay(n, sr, 0.004, 0.0002)
    tock = 0.3 * np.sin(2 * np.pi * 900.0 * (1 + 0.1 * var) * t) * _decay(n, sr, 0.006, 0.0003)
    return burst + body + tock, 0.0003


def _synth_tick(sr: int, dur: float, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    n = round(dur * sr)
    t = np.arange(n) / sr
    f0 = 1150.0
    x = np.zeros(n)
    for ratio, amp, tau in ((1.0, 1.0, 0.035), (2.6, 0.5, 0.018), (4.3, 0.2, 0.010)):
        x += amp * np.sin(2 * np.pi * f0 * ratio * t) * _decay(n, sr, tau, 0.0004)
    x += 0.25 * _lpf(rng.standard_normal(n), sr, 5000.0) * _decay(n, sr, 0.0015, 0.0002)
    return x, 0.0004


def _synth_impact(sr: int, dur: float, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    n = round(dur * sr)
    t = np.arange(n) / sr
    f = 62.0 + 78.0 * np.exp(-t / 0.045)  # 140 → 62 Hz pitch drop: weight without an earbud-only sub boom
    body = np.sin(2 * np.pi * np.cumsum(f) / sr) * _decay(n, sr, 0.14, 0.0015)
    body = np.tanh(3.0 * body) / np.tanh(3.0)  # harmonics that survive phone speakers
    fk = 190.0 + 90.0 * np.exp(-t / 0.02)  # mid "knock" resonance: what a phone speaker actually plays
    knock = np.sin(2 * np.pi * np.cumsum(fk) / sr) * _decay(n, sr, 0.06, 0.001)
    thud = _bpf(_pink(n, rng), sr, 150.0, 900.0) * _decay(n, sr, 0.05, 0.001)
    click = np.sin(2 * np.pi * 3000.0 * t) * _decay(n, sr, 0.002, 0.0002) * 0.25
    x = 0.8 * body + 0.9 * knock + 1.5 * thud + click
    return _hpf(x, sr, 45.0, order=4), 0.0015


def _synth_shutter(sr: int, dur: float, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    n = round(dur * sr)
    x = np.zeros(n)

    def burst(at: float, amp: float, tau: float, lo: float = 1200.0, hi: float = 9000.0) -> None:
        i = round(at * sr)
        m = n - i
        if m <= 0:
            return
        b = _bpf(rng.standard_normal(m), sr, lo, hi) * _decay(m, sr, tau, 0.0002) * amp
        x[i:] += b

    burst(0.0, 1.0, 0.0025)
    for k, at in enumerate((0.012, 0.020, 0.031)):
        burst(at, 0.25 / (k + 1), 0.0015, 2500.0, 9000.0)
    burst(0.075, 0.7, 0.003)
    tt = np.arange(n) / sr
    x += 0.35 * np.sin(2 * np.pi * 400.0 * tt) * _decay(n, sr, 0.008, 0.0005)
    return x, 0.0003


def _synth_typing(sr: int, dur: float, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    n = round(dur * sr)
    x = np.zeros(n)
    at = 0.002
    first = at
    while at < dur - 0.06:
        m = round(0.05 * sr)
        var = rng.uniform(-1, 1)
        space = rng.random() < 0.12
        k, _ = _synth_click(sr, 0.05, rng, var=var - (0.8 if space else 0.0))
        k = k * 10 ** ((rng.uniform(-2, 1.5) - (2.0 if space else 0.0)) / 20)
        i = round(at * sr)
        x[i:i + m] += k[: max(0, min(m, n - i))]
        at += rng.uniform(0.085, 0.17)
    return x, first


def key_to_pitch(key: str | None, *, lo: float = 700.0, hi: float = 1400.0) -> float | None:
    """Tonic of a key name ("A minor", "F# major", "Bb") mapped into ``[lo, hi)`` Hz."""
    if not key:
        return None
    m = re.match(r"^\s*([A-Ga-g])([#b♯♭]?)", key)
    if not m:
        return None
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[m.group(1).upper()]
    acc = {"#": 1, "♯": 1, "b": -1, "♭": -1}.get(m.group(2), 0)
    pc = (base + acc) % 12
    f = 440.0 * 2 ** ((pc - 9) / 12)
    while f < lo:
        f *= 2
    while f >= hi:
        f /= 2
    return f


def synthesize(kind: str, *, sr: int = SAMPLE_RATE, duration_s: float | None = None, seed: int = 0,
               pitch_hz: float | None = None) -> SfxClip:
    """Render one procedural effect (see module docstring). Deterministic for a given seed."""
    fam = family_for(kind)
    if fam is None:
        raise SfxError(f"no procedural sound for kind {kind!r} (families: {', '.join(FAMILIES)})")
    rng = np.random.default_rng(seed)
    dur = float(duration_s or fam.duration_s)
    var = float(rng.uniform(-1, 1)) if (fam.vary and seed) else 0.0
    k = fam.kind
    if k == "whoosh":
        x, sync = _synth_whoosh(sr, dur, rng)
        fi, fo = 0.004, 0.01
    elif k == "swoosh_down":
        x, sync = _synth_whoosh(sr, dur, rng, down=True)
        fi, fo = 0.004, 0.01
    elif k == "riser":
        x, sync = _synth_riser(sr, dur, rng)
        fi, fo = 0.004, 0.004
    elif k == "pop":
        x, sync = _synth_pop(sr, dur, rng)
        fi, fo = 0.0003, 0.01
    elif k == "ding":
        x, sync = _synth_ding(sr, dur, rng, float(pitch_hz or 880.0))
        fi, fo = 0.0005, 0.03
    elif k == "click":
        x, sync = _synth_click(sr, dur, rng, var=var)
        fi, fo = 0.0002, 0.005
    elif k == "tick":
        x, sync = _synth_tick(sr, dur, rng)
        fi, fo = 0.0002, 0.01
    elif k == "impact":
        x, sync = _synth_impact(sr, dur, rng)
        fi, fo = 0.0005, 0.03
    elif k == "shutter":
        x, sync = _synth_shutter(sr, dur, rng)
        fi, fo = 0.0002, 0.01
    elif k == "typing":
        x, sync = _synth_typing(sr, dur, rng)
        fi, fo = 0.001, 0.01
    else:  # pragma: no cover - every family has a synth
        raise SfxError(f"no synth for {k}")
    audio = _finish(x, sr, fade_in_s=fi, fade_out_s=fo, hpf_hz=None if k in ("impact",) else 20.0)
    if fam.vary and seed:  # subtle level variation for repeated action sounds (±1.5 dB)
        audio = (audio * 10 ** (-abs(var) * 1.5 / 20.0)).astype(np.float32)
    audio = to_channels(audio, 2)
    if fam.sync == "peak":  # the perceived arrival is the measured loudness peak, not the design value
        sync = detect_sync(audio, sr, "peak")
    return SfxClip(audio=audio, sr=sr, sync_s=float(min(sync, audio.shape[-1] / sr)), kind=k,
                   meta={"source": "procedural", "seed": seed, "pitch_hz": pitch_hz, "duration_s": dur,
                         "synth_version": SYNTH_VERSION})


# ============================================================================================ measurement
def _envelope(x: np.ndarray, sr: int, win_s: float) -> np.ndarray:
    w = max(1, round(win_s * sr))
    p = np.convolve(x.astype(np.float64) ** 2, np.ones(w) / w, mode="same")
    return np.sqrt(p)


def detect_sync(audio: Any, sr: int, mode: Literal["onset", "peak", "end"]) -> float:
    """Sync point of an effect: first transient onset, loudness peak, or end of the active sound."""
    x = to_mono(audio)
    if x.size == 0 or not np.any(x):
        return 0.0
    if mode == "peak":
        env = _envelope(x, sr, 0.03)
        return float(np.argmax(env) / sr)
    env = _envelope(x, sr, 0.0005)
    mx = float(env.max())
    if mode == "end":
        idx = np.nonzero(env >= mx * 10 ** (-40 / 20))[0]
        return float((idx[-1] + 1) / sr) if idx.size else x.size / sr
    idx = np.nonzero(env >= 0.3 * mx)[0]
    return float(idx[0] / sr) if idx.size else 0.0


def prepare_clip(audio: Any, sr: int, fam: SfxFamily | None, *, kind: str | None = None) -> SfxClip:
    """Clean a generated effect: DC/rumble filter, trim leading silence to ~3 ms before the onset (fade in),
    trim the tail at −60 dB (fade out), normalise to −1 dBFS, find the sync point."""
    a = as_2d(audio).astype(np.float64)
    a = _hpf(a, sr, 25.0)
    x = a.mean(axis=0)
    env = _envelope(x, sr, 0.001)
    mx = float(env.max()) if env.size else 0.0
    if mx <= 0:
        raise SfxError("silent sound effect")
    lead_thr = mx * 10 ** ((-50 if (fam and fam.texture) else -45) / 20)
    on = np.nonzero(env >= lead_thr)[0]
    start = max(0, int(on[0]) - round(0.003 * sr)) if on.size else 0
    tail = np.nonzero(env >= mx * 10 ** (-60 / 20))[0]
    end = min(a.shape[-1], int(tail[-1]) + round(0.01 * sr)) if tail.size else a.shape[-1]
    a = a[:, start:max(start + 1, end)]
    fin = 0.005 if (fam and fam.texture) else 0.002
    clip = _finish(a, sr, fade_in_s=fin, fade_out_s=0.01, hpf_hz=None)
    sync = detect_sync(clip, sr, fam.sync if fam else "onset")
    return SfxClip(audio=to_channels(clip, 2), sr=sr, sync_s=sync,
                   kind=(fam.kind if fam else _norm_kind(kind or "sfx")), meta={"trim_start_s": round(start / sr, 4)})


def score_clip(clip: SfxClip, fam: SfxFamily | None, *, raw: Any | None = None,
               detect_voice: bool = True) -> tuple[float, dict[str, float | None]]:
    """Measure a candidate; higher score is better. Penalises wrong length, reverb-like tails, sub-bass,
    clipping, a misplaced loudness peak and any detected voice."""
    x = to_mono(clip.audio).astype(np.float64)
    sr = clip.sr
    env = _envelope(x, sr, 0.01)
    mx = float(env.max()) if env.size else 0.0
    active = np.nonzero(env >= mx * 10 ** (-40 / 20))[0] if mx > 0 else np.zeros(0, int)
    act_dur = float((active[-1] - active[0] + 1) / sr) if active.size else 0.0
    pk_i = int(np.argmax(env)) if env.size else 0
    below = np.nonzero(env[pk_i:] <= mx * 10 ** (-30 / 20))[0]
    decay_s = float(below[0] / sr) if below.size else float((env.size - pk_i) / sr)
    # boxcar periodogram of the whole clip: a windowed segment would erase a transient at the clip start
    nfft = max(8192, 1 << math.ceil(math.log2(max(2, x.size))))
    p = np.abs(np.fft.rfft(x, nfft)) ** 2
    f = np.fft.rfftfreq(nfft, 1.0 / sr)
    tot = float(p[f >= 20].sum()) or 1e-20
    sub = float(p[(f >= 20) & (f < 60)].sum() / tot)
    clip_ratio = 0.0
    if raw is not None:
        r = np.asarray(raw)
        clip_ratio = float(np.mean(np.abs(r) >= 0.999)) if r.size else 0.0
    peak_frac = float((pk_i - active[0]) / max(1, active[-1] - active[0])) if active.size else 0.0
    voice = None
    if detect_voice and x.size >= int(0.5 * sr):
        from studio.audio.music import _voice_ratio

        voice = _voice_ratio(x.astype(np.float32), sr)
    target = fam.duration_s if fam else 1.0
    pen = abs(math.log2(max(act_dur, 0.01) / target)) * 1.0
    if fam is not None:
        pen += 2.0 * max(0.0, decay_s - fam.max_decay_s)
        pen += 4.0 * max(0.0, sub - (0.35 if fam.kind == "impact" else 0.15))
        if fam.peak_frac is not None:
            pen += 1.5 * abs(peak_frac - fam.peak_frac)
    pen += 50.0 * clip_ratio
    if voice is not None:
        pen += 5.0 * voice
    metrics = {"active_s": round(act_dur, 4), "decay_s": round(decay_s, 4), "sub_share": round(sub, 4),
               "clip_ratio": round(clip_ratio, 5), "peak_frac": round(peak_frac, 3),
               "voice_ratio": None if voice is None else round(voice, 3)}
    return -pen, metrics


# ============================================================================================ ElevenLabs
class ElevenLabsSfxClient(ElevenLabsHTTP):
    def generate(self, text: str, *, duration_s: float | None = None, prompt_influence: float = 0.75,
                 loop: bool = False, model_id: str = SFX_MODEL, output_format: str = SFX_OUTPUT_FORMAT
                 ) -> tuple[bytes, dict[str, str]]:
        body = _sfx_body(text, duration_s=duration_s, prompt_influence=prompt_influence, loop=loop, model_id=model_id)
        return self.post_audio(SFX_ENDPOINT, params={"output_format": output_format}, body=body)


def _sfx_body(text: str, *, duration_s: float | None, prompt_influence: float, loop: bool, model_id: str
              ) -> dict[str, Any]:
    if not text or not text.strip():
        raise ValueError("sound-effect text must not be empty")
    if duration_s is not None and not (SFX_MIN_S <= duration_s <= SFX_MAX_S):
        raise ValueError(f"duration_seconds must be {SFX_MIN_S}..{SFX_MAX_S}")
    if not (0.0 <= prompt_influence <= 1.0):
        raise ValueError("prompt_influence must be 0..1")
    body: dict[str, Any] = {"text": text.strip(), "prompt_influence": float(prompt_influence), "loop": bool(loop),
                            "model_id": model_id}
    if duration_s is not None:
        body["duration_seconds"] = round(float(duration_s), 3)
    return body


def _sfx_licence(record_path: str, *, source: str, tier: str | None = None) -> Licence:
    if source == "procedural":
        return Licence(name="Self-generated (procedural synthesis)", source="procedural", holder="Yunicorn Studio",
                       commercial_use=True, attribution_required=False, record_path=record_path,
                       acquired_at=_now_iso(),
                       notes="Synthesised in code by studio.audio.sfx (numpy); contains no third-party audio.")
    t = (tier or "").lower()
    free = t == "free"
    notes = ("ElevenLabs sound-effect output; commercial use follows the account plan (paid plans: commercial "
             "use; Free plan: non-commercial with attribution). "
             + (f"Account plan tier: {tier}." if tier else "Account plan tier could not be verified; assumes a "
                                                          "paid plan."))
    return Licence(name=SFX_TERMS_NAME, source="elevenlabs", url=SFX_TERMS_URL, holder="ElevenLabs account holder "
                   "(Yunicorn)", attribution="ElevenLabs" if free else None, attribution_required=free,
                   commercial_use=not free, record_path=record_path, acquired_at=_now_iso(), notes=notes)


def _write_clip(job: Job, asset_id: str, clip: SfxClip, *, licence: Licence, record: dict[str, Any],
                source: str, source_id: str | None, description: str, query: str | None) -> AssetRef:
    rel = f"assets/sfx/{asset_id}.wav"
    wav = write_wav(job.path(rel), clip.audio, clip.sr, subtype="PCM_24")
    x = clip.audio
    analysis = {"asset_id": asset_id, "kind": clip.kind, "sync_s": round(clip.sync_s, 5),
                "sync_mode": (family_for(clip.kind).sync if family_for(clip.kind) else "onset"),
                "duration_s": round(clip.duration_s, 5), "sr": clip.sr, "peak_dbfs": round(peak_dbfs(x), 2),
                "momentary_max_lufs": round(max_momentary_lufs(x, clip.sr), 2), **clip.meta}
    job.save_json(f"assets/sfx/{asset_id}.analysis.json", analysis)
    job.save_json(licence.record_path or f"assets/sfx/{asset_id}.licence.json", {
        "asset_id": asset_id, "kind": "sfx", "sfx_kind": clip.kind, "source": source,
        "licence": licence.model_dump(mode="json"), "file": rel, "sha256": sha256_file(wav), "sample_rate": clip.sr,
        "duration_s": round(clip.duration_s, 5), "created_at": _now_iso(), **record})
    asset = AssetRef(kind="audio", source=source, source_id=source_id, path=rel,
                     duration_ms=round(clip.duration_s * 1000), description=description[:1000], query=query,
                     licence=licence)
    return job.register_asset(asset, asset_id=asset_id, overwrite=True)


def _cached(job: Job, asset_id: str) -> AssetRef | None:
    a = job.load_asset(asset_id)
    if a is not None and a.path and job.path(a.path).exists():
        return a
    return None


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def generate_sfx(job: Job, kind: str, *, prompt: str | None = None, duration_s: float | None = None,
                 prompt_influence: float | None = None, n: int = 4, settings: Settings | None = None,
                 client: ElevenLabsSfxClient | None = None, detect_voice: bool = True) -> AssetRef:
    """Generate ``n`` ElevenLabs candidates for ``kind`` and keep the best-measured one (cached per job)."""
    fam = family_for(kind)
    text = prompt or (fam.prompt if fam else f"{kind.replace('_', ' ')}, {_DRY}")
    dur = float(np.clip(duration_s or (fam.duration_s if fam else 1.0), SFX_MIN_S, SFX_MAX_S))
    pi = float(prompt_influence if prompt_influence is not None else (fam.prompt_influence if fam else 0.7))
    body = _sfx_body(text, duration_s=dur, prompt_influence=pi, loop=False, model_id=SFX_MODEL)
    key = _hash({"endpoint": SFX_ENDPOINT, "output_format": SFX_OUTPUT_FORMAT, "body": body, "n": n})
    kslug = re.sub(r"[^a-z0-9]+", "-", _norm_kind(fam.kind if fam else kind))[:20].strip("-") or "sfx"
    asset_id = f"elsfx_{kslug}_{key[:12]}"
    hit = _cached(job, asset_id)
    if hit is not None:
        return hit
    cl = client or ElevenLabsSfxClient.from_settings(settings)
    cands: list[tuple[float, SfxClip, dict[str, Any], dict[str, str]]] = []
    errors: list[str] = []
    t0 = time.monotonic()
    for i in range(max(1, n)):
        try:
            data, headers = cl.post_audio(SFX_ENDPOINT, params={"output_format": SFX_OUTPUT_FORMAT}, body=body)
            raw, sr = decode_elevenlabs_audio(data, SFX_OUTPUT_FORMAT, expected_s=dur,
                                              pcm_default_channels=_PCM_DEFAULT_CHANNELS_SFX)
            clip = prepare_clip(raw, sr, fam, kind=kind)
            score, metrics = score_clip(clip, fam, raw=raw, detect_voice=detect_voice)
            cands.append((score, clip, {"candidate": i, "score": round(score, 4), "payload_channels": int(raw.shape[0]),
                                        "payload_s": round(raw.shape[-1] / sr, 4), **metrics}, headers))
        except (ElevenLabsError, SfxError, ValueError) as e:
            errors.append(str(e)[:200])
    if not cands:
        raise SfxError(f"ElevenLabs produced no usable {kind!r} candidates: {'; '.join(errors)[:600]}")
    cands.sort(key=lambda c: -c[0])
    score, clip, metrics, headers = cands[0]
    clip.meta.update({"source": "elevenlabs", "chosen": metrics, "candidates": [c[2] for c in cands]})
    tier = cl.subscription_tier()
    lic_rel = f"assets/sfx/{asset_id}.licence.json"
    licence = _sfx_licence(lic_rel, source="elevenlabs", tier=tier)
    rec = {"endpoint": f"POST {SFX_ENDPOINT}", "model_id": SFX_MODEL, "plan_tier": tier,
           "terms": {"name": SFX_TERMS_NAME, "url": SFX_TERMS_URL},
           "request": {"output_format": SFX_OUTPUT_FORMAT, "body": body, "candidates": n},
           "response_headers": headers}
    asset = _write_clip(job, asset_id, clip, licence=licence, record=rec, source="elevenlabs",
                        source_id=headers.get("request-id") or key[:16],
                        description=f"{clip.kind}: {text} (best of {len(cands)} by measurement)", query=text)
    job.trace("model_call", role="sfx_generator", provider="elevenlabs", model=SFX_MODEL,
              latency_ms=int((time.monotonic() - t0) * 1000), stage="sfx", asset_id=asset_id,
              candidates=len(cands), plan_tier=tier)
    return asset


def procedural_sfx(job: Job, kind: str, *, seed: int = 0, pitch_hz: float | None = None,
                   duration_s: float | None = None) -> AssetRef:
    """Synthesise (or reuse) a procedural effect as a registered, licensed asset."""
    fam = family_for(kind)
    if fam is None:
        raise SfxError(f"no procedural sound for kind {kind!r}")
    params = {"kind": fam.kind, "seed": int(seed), "pitch_hz": None if pitch_hz is None else round(float(pitch_hz), 2),
              "duration_s": None if duration_s is None else round(float(duration_s), 4), "v": SYNTH_VERSION}
    asset_id = f"sfxp_{fam.kind}_{_hash(params)[:10]}"
    hit = _cached(job, asset_id)
    if hit is not None:
        return hit
    clip = synthesize(fam.kind, seed=seed, pitch_hz=pitch_hz, duration_s=duration_s)
    lic_rel = f"assets/sfx/{asset_id}.licence.json"
    return _write_clip(job, asset_id, clip, licence=_sfx_licence(lic_rel, source="procedural"),
                       record={"synth": params}, source="procedural", source_id=asset_id,
                       description=f"{fam.kind} (procedural): {fam.description}", query=None)


def _seed_for(cue_id: str | None) -> int:
    if not cue_id:
        return 0
    return int(hashlib.sha256(cue_id.encode()).hexdigest()[:8], 16) % (2 ** 31 - 1) or 1


def _resolve_kind(job: Job, kind: str, *, cue_id: str | None = None, settings: Settings | None = None,
                  prefer: Literal["auto", "elevenlabs", "procedural"] = "auto",
                  client: ElevenLabsSfxClient | None = None, music_key: str | None = None,
                  n_candidates: int = 4) -> AssetRef:
    fam = family_for(kind)
    s = settings or get_settings()
    has_key = client is not None or s.has_key("elevenlabs")
    pitch = key_to_pitch(music_key) if (fam is not None and fam.tonal) else None
    seed = _seed_for(cue_id) if (fam is not None and fam.vary) else 0
    if prefer == "procedural" or (prefer == "auto" and fam is not None and (not fam.generative or not has_key)):
        if fam is None:
            raise SfxError(f"no procedural sound for kind {kind!r}")
        return procedural_sfx(job, fam.kind, seed=seed, pitch_hz=pitch)
    if not has_key:
        if prefer == "elevenlabs":
            raise SfxError("ElevenLabs sound effects need an ElevenLabs key")
        raise SfxError(f"no licensed source for kind {kind!r}: not in the procedural library and no ElevenLabs key")
    try:
        return generate_sfx(job, kind, settings=s, client=client, n=n_candidates)
    except Exception as e:
        if prefer == "auto" and fam is not None:
            job.trace("sfx_generation_failed", kind=kind, error=str(e)[:300], fallback="procedural", stage="sfx")
            return procedural_sfx(job, fam.kind, seed=seed, pitch_hz=pitch)
        raise


def resolve_sfx(job: Job, cue: SfxCue, *, settings: Settings | None = None,
                prefer: Literal["auto", "elevenlabs", "procedural"] = "auto",
                client: ElevenLabsSfxClient | None = None, music_key: str | None = None,
                n_candidates: int = 4) -> AssetRef:
    """A licensed asset for ``cue`` (the cue's own asset, or one picked/generated by ``kind``)."""
    if cue.asset is not None:
        if cue.asset.licence is None:
            raise SfxError(f"sfx {cue.id}: asset has no licence record (invariant 5)")
        return cue.asset
    return _resolve_kind(job, cue.kind, cue_id=cue.id, settings=settings, prefer=prefer, client=client,
                         music_key=music_key, n_candidates=n_candidates)


def load_sfx(job: Job, asset: AssetRef, *, sr: int = SAMPLE_RATE, kind: str | None = None,
             path: str | None = None) -> SfxClip:
    """Load an SFX asset with its sync point (sidecar analysis, or measured when missing)."""
    p = job.path(asset.path or "") if path is None else path
    audio = load_audio(p, sr=sr, channels=2)
    side = job.load_json(f"assets/sfx/{asset.id}.analysis.json", default=None) if asset.id else None
    k = (side or {}).get("kind") or kind or "sfx"
    fam = family_for(k)
    if isinstance(side, dict) and "sync_s" in side and int(side.get("sr", sr)) in (sr, SAMPLE_RATE):
        sync = float(side["sync_s"])
    else:
        sync = detect_sync(audio, sr, fam.sync if fam else "onset")
    return SfxClip(audio=audio, sr=sr, sync_s=sync, kind=fam.kind if fam else _norm_kind(k), meta=dict(side or {}))


# ============================================================================================ levels
@dataclass
class LevelAdvice:
    gain_db: float  # linear gain (dB) to apply to the normalised asset
    basis: Literal["peak", "loudness"]
    target: float  # target peak (dBFS) or momentary-max loudness (LUFS)
    speech_ref: float  # local speech peak (dBFS) or short-term loudness (LUFS)
    overlaps_speech: bool
    assumed: bool  # True when dialogue levels were assumed (no dialogue given / silent locally)
    clamped: bool = False
    note: str = ""


_ASSUMED_SPEECH_PEAK = -3.0
_ASSUMED_SPEECH_ST = -14.0


def _speech_refs(dialogue: np.ndarray | None, sr: int, at_s: float) -> tuple[float, float, bool]:
    """(local speech peak dBFS, local short-term LUFS, assumed?) around ``at_s``."""
    if dialogue is None:
        return _ASSUMED_SPEECH_PEAK, _ASSUMED_SPEECH_ST, True
    x = to_mono(dialogue)
    for half in (1.0, 3.0):
        i0, i1 = max(0, round((at_s - half) * sr)), min(x.size, round((at_s + half) * sr))
        if i1 - i0 > 0:
            pk = float(gain_to_db(np.max(np.abs(x[i0:i1]))))
            st = short_term_lufs_at(x, sr, at_s)
            if pk > -45.0 and st > -50.0:
                return pk, st, False
    # locally silent: use the take's own speech level
    if x.size and np.any(x):
        pk = float(gain_to_db(np.percentile(np.abs(x), 99.95)))
        from studio.audio.music import window_loudness

        _, lufs = window_loudness(x, sr, window_s=3.0, hop_s=0.5)
        live = lufs[lufs > -60]
        st = float(np.percentile(live, 75)) if live.size else _ASSUMED_SPEECH_ST
        return pk, st, False
    return _ASSUMED_SPEECH_PEAK, _ASSUMED_SPEECH_ST, True


def recommend_level(kind: str, clip: SfxClip | Any, sr: int = SAMPLE_RATE, *, at_s: float = 0.0,
                    sync_s: float | None = None, dialogue: Any | None = None,
                    speech_spans: Sequence[tuple[float, float]] | None = None,
                    cue_gain_db: float = DEFAULT_CUE_GAIN_DB) -> LevelAdvice:
    """Level for an effect placed with its sync point at ``at_s`` (seconds on the dialogue's clock)."""
    c = clip if isinstance(clip, SfxClip) else SfxClip(audio=as_2d(clip), sr=sr, sync_s=sync_s or 0.0,
                                                         kind=_norm_kind(kind))
    fam = family_for(kind) or FAMILIES["click"]
    sync = c.sync_s if sync_s is None else float(sync_s)
    dia = None if dialogue is None else to_mono(dialogue)
    speech_pk, speech_st, assumed = _speech_refs(dia, sr, at_s)
    x = to_mono(c.audio)
    env = _envelope(x, c.sr, 0.01)
    act = np.nonzero(env >= (env.max() if env.size else 0) * 10 ** (-30 / 20))[0]
    a0 = at_s - sync + (act[0] / c.sr if act.size else 0.0)
    a1 = at_s - sync + (act[-1] / c.sr if act.size else c.duration_s)
    if speech_spans is None and dia is not None:
        speech_spans = detect_speech_spans(dia, sr)
    overlaps = bool(speech_spans and any(s < a1 and e > a0 for s, e in speech_spans)) if speech_spans is not None \
        else True
    trim = float(cue_gain_db) - DEFAULT_CUE_GAIN_DB
    clamped = False
    if fam.texture:
        own = max_momentary_lufs(c.audio, c.sr)
        rel = fam.lu_under_speech_overlap if overlaps else fam.lu_under_speech_gap
        target = speech_st + rel + trim
        ceiling = speech_st - 4.0
        if target > ceiling:
            target, clamped = ceiling, True
        gain = target - own
        basis: Literal["peak", "loudness"] = "loudness"
    else:
        own = peak_dbfs(c.audio)
        target = speech_pk + fam.peak_under_speech_db + trim
        ceiling = speech_pk - 2.0
        if target > ceiling:
            target, clamped = ceiling, True
        gain = target - own
        basis = "peak"
    note = (f"{fam.kind}: {basis} {target:.1f} vs speech {'peak' if basis == 'peak' else 'short-term'} "
            f"{speech_pk if basis == 'peak' else speech_st:.1f}" + (" (over words)" if overlaps else " (in a gap)")
            + (" [assumed dialogue levels]" if assumed else "")
            + (" [clamped to protect the voice]" if clamped else ""))
    return LevelAdvice(gain_db=round(float(gain), 3), basis=basis, target=round(float(target), 3),
                       speech_ref=round(float(speech_pk if basis == "peak" else speech_st), 3),
                       overlaps_speech=overlaps, assumed=assumed, clamped=clamped, note=note)


def level_guidance(kind: str | None = None) -> str:
    """Level priors as text (for the Director / sound designer)."""
    fams = [family_for(kind)] if kind else list(FAMILIES.values())
    lines = []
    for f in fams:
        if f is None:
            continue
        if f.texture:
            lines.append(f"{f.kind}: {abs(f.lu_under_speech_overlap):.0f} LU under local speech over words, "
                         f"{abs(f.lu_under_speech_gap):.0f} LU under in a clean gap; sync on the {f.sync}. "
                         f"{f.description}")
        else:
            lines.append(f"{f.kind}: peak {abs(f.peak_under_speech_db):.0f} dB under the local speech peak; "
                         f"sync on the {f.sync}. {f.description}")
    lines.append("SfxCue.gain_db is a trim relative to these (default -12 = as recommended); the voice is protected "
                 "by a ceiling. Duck any bed a further ~3 dB under each effect.")
    return "\n".join(lines)


# ============================================================================================ placement
@dataclass
class SfxPlacement:
    sfx_id: str
    kind: str
    asset_id: str | None
    out_t: float
    start_s: float  # where the file starts in output time (sync point lands on out_t)
    end_s: float
    active_start_s: float
    active_end_s: float
    gain_db: float
    advice: LevelAdvice | None
    clip: SfxClip | None = None

    def report(self) -> dict[str, Any]:
        return {"sfx_id": self.sfx_id, "kind": self.kind, "asset_id": self.asset_id, "out_t": round(self.out_t, 5),
                "start_s": round(self.start_s, 5), "end_s": round(self.end_s, 5), "gain_db": round(self.gain_db, 2),
                "note": self.advice.note if self.advice else ""}


def place_sfx(job: Job, timeline: Timeline, *, sr: int = SAMPLE_RATE, dialogue: Any | None = None,
              speech_spans: Sequence[tuple[float, float]] | None = None, music_key: str | None = None,
              settings: Settings | None = None, prefer: Literal["auto", "elevenlabs", "procedural"] = "auto",
              client: ElevenLabsSfxClient | None = None,
              on_error: Callable[[str, Exception], None] | None = None) -> list[SfxPlacement]:
    """Resolve, load and level every timeline SFX (no mixing). Unresolvable cues are skipped (traced)."""
    dia = None if dialogue is None else (load_audio(dialogue, sr=sr, channels=1)[0]
                                         if isinstance(dialogue, (str, bytes)) or hasattr(dialogue, "__fspath__")
                                         else to_mono(dialogue))
    if speech_spans is None:
        if timeline.word_map:
            from studio.audio.music import speech_spans_from_timeline

            speech_spans = speech_spans_from_timeline(timeline)
        elif dia is not None:
            speech_spans = detect_speech_spans(dia, sr)
    out: list[SfxPlacement] = []
    for fx in sorted(timeline.sfx, key=lambda f: f.out_t):
        try:
            asset = fx.asset if (fx.asset is not None and fx.asset.licence is not None) else _resolve_kind(
                job, fx.kind, cue_id=fx.sfx_id, settings=settings, prefer=prefer, client=client, music_key=music_key)
            clip = load_sfx(job, asset, sr=sr, kind=fx.kind, path=fx.asset_path)
        except Exception as e:
            job.trace("sfx_skipped", sfx_id=fx.sfx_id, kind=fx.kind, error=str(e)[:300], stage="sfx")
            if on_error is not None:
                on_error(fx.sfx_id, e)
            continue
        t = float(fx.out_t)
        adv = recommend_level(fx.kind, clip, sr, at_s=t, dialogue=dia, speech_spans=speech_spans,
                              cue_gain_db=fx.gain_db)
        x = to_mono(clip.audio)
        env = _envelope(x, sr, 0.01)
        act = np.nonzero(env >= (env.max() if env.size else 0) * 10 ** (-30 / 20))[0]
        start = t - clip.sync_s
        out.append(SfxPlacement(sfx_id=fx.sfx_id, kind=clip.kind, asset_id=asset.id, out_t=t, start_s=start,
                                end_s=start + clip.duration_s,
                                active_start_s=start + (act[0] / sr if act.size else 0.0),
                                active_end_s=start + (act[-1] / sr if act.size else clip.duration_s),
                                gain_db=adv.gain_db, advice=adv, clip=clip))
    return out


def render_sfx_track(job: Job, timeline: Timeline, *, sr: int = 48000, dialogue: Any | None = None,
                     speech_spans: Sequence[tuple[float, float]] | None = None, music_key: str | None = None,
                     settings: Settings | None = None, prefer: Literal["auto", "elevenlabs", "procedural"] = "auto",
                     client: ElevenLabsSfxClient | None = None, channels: int = 2,
                     report: list[dict[str, Any]] | None = None) -> Any:
    """All SFX mixed onto one float32 track aligned to the timeline: ``(2, n)`` (``(n,)`` for
    ``channels=1``), ``n = sample_index(timeline.duration, sr)``. Each effect's sync point lands on the
    sample of its ``out_t``; levels follow :func:`recommend_level` against ``dialogue`` (the processed
    voice track on the same clock) when given."""
    n = sample_index(timeline.duration, sr)
    out = np.zeros((2, n), dtype=np.float64)
    placements = place_sfx(job, timeline, sr=sr, dialogue=dialogue, speech_spans=speech_spans, music_key=music_key,
                           settings=settings, prefer=prefer, client=client)
    for p in placements:
        assert p.clip is not None
        a = p.clip.audio.astype(np.float64) * 10 ** (p.gain_db / 20.0)
        i0 = sample_index(p.out_t, sr) - round(p.clip.sync_s * sr)
        lo, hi = max(0, i0), min(n, i0 + a.shape[-1])
        if hi <= lo:
            continue
        seg = a[:, lo - i0:hi - i0].copy()
        if i0 < 0:  # head cut by the timeline start: short fade so it cannot click
            f = min(seg.shape[-1], round(0.002 * sr))
            seg[:, :f] *= raised_cosine(f)[None, :]
        if i0 + a.shape[-1] > n:
            f = min(seg.shape[-1], round(0.005 * sr))
            seg[:, seg.shape[-1] - f:] *= raised_cosine(f, rising=False)[None, :]
        out[:, lo:hi] += seg
    reps = [p.report() for p in placements]
    if report is not None:
        report.extend(reps)
    if reps:
        job.trace("sfx_render", placements=reps, peak_dbfs=round(peak_dbfs(out), 2), stage="sfx")
    res = out.astype(np.float32)
    return res.mean(axis=0) if channels == 1 else res


def sfx_duck_spans(job: Job, timeline: Timeline, *, depth_db: float = 3.0, sr: int = SAMPLE_RATE,
                   placements: Sequence[SfxPlacement] | None = None, **kw: Any) -> list[tuple[float, float, float]]:
    """``(start_s, end_s, depth_db)`` dips for the music bed under each effect's active span (sfx.md: duck
    the bed a further 2–4 dB for the sound's duration). Feed to ``music.ducking_envelope(extra_spans=…)``."""
    ps = placements if placements is not None else place_sfx(job, timeline, sr=sr, **kw)
    return [(max(0.0, p.active_start_s), p.active_end_s, float(depth_db)) for p in ps]
