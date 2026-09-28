"""Audio render (ARCHITECTURE §6 stage 3): dialogue edit, speed, room tone, music, SFX, loudness.

Pipeline (:func:`render_audio`)
-------------------------------
1. **Voice first, once.** The dialogue is the *processed* full source (:func:`studio.audio.voice.process_dialogue`,
   cached per chain spec), so every seam joins material that went through the same compressor, leveler
   and denoiser states — no timbre or floor jumps at cuts.
2. **Sample-accurate segment extraction.** Each :class:`~studio.compile.models.TimelineSegment` maps
   source to output with the same rational mapping the picture uses
   (``out(t) = out_start + (t − src_in) / speed``, anchored at the segment's frame-snapped
   ``out_start``); audio sample positions are derived from those exact Fractions (at most half a sample
   of rounding, 10 µs). J/L cuts shift the *audio* seam: a J-lead on the incoming segment moves the
   handover earlier, an L-lag on the outgoing segment later; when both are present the incoming seam
   treatment (``jcut``/``lcut``) decides.
3. **Speed** via the Rubber Band CLI, R3 engine (``-3``, formant flag ``-F``, pitch preserved), rendered
   with 250 ms of source context on each side so the stretcher is in steady state at the cut points,
   then sliced sample-exactly. Rubber Band clamps its output at ±1.0 even for float files, so the
   input is pre-scaled into headroom and scaled back (verified: gain preserved to 2e-6, length exact,
   markers within ~1 ms).
4. **Seams**: every seam gets a crossfade whose **length adapts to the energy at the seam** (30 ms in
   room tone, down to 10 ms where the cut meets louder material; 5 ms only when both handles contain a
   word onset) and whose **placement** keeps foreign material out: if the outgoing handle (source
   beyond the cut) contains an onset, the fade is placed before the seam; if the incoming handle does,
   after it. The fade law is **correlation-adaptive constant power**: ``g_out = cos θ / √(1 + r·sin 2θ)``,
   ``g_in = sin θ / √(1 + r·sin 2θ)`` with ``r`` the measured correlation of the two signals in the
   fade — equal-power for unrelated material (r≈0), equal-gain when re-joining one continuous sound
   (r≈1), and no level bump or dip in between.
5. **Room tone**: a continuous bed built from the take's own processed pauses fills every region the
   dialogue does not cover (leading/trailing, holes between segments, handles that run off the source),
   power-complementary to the dialogue fades, plus a floor keeper that fills any stretch that would drop
   ≥ 6 dB below the take's floor (no dead air, invariant 9).
6. **Music**: a bed from :func:`studio.audio.music.render_music_bed` (fitted there, requested *unlevelled*;
   or the timeline's asset placed directly) is set
   ``level_lu_under_speech`` below the dialogue *during speech* (BS.1770 loudness of the bed under
   speech vs the dialogue), with a look-ahead **duck envelope from the dialogue's word spans**: ramp
   starts 200 ms before the first word (150 ms raised-cosine attack), holds through gaps < 1.2 s,
   releases 150 ms after the last word over 700 ms; the bed swells by ``duck_db`` only in real gaps,
   intro and outro. A 3 dB zero-phase dynamic dip at 1–4 kHz while speech plays and a further 3 dB
   under each SFX keep the voice clear.
7. **SFX**: the timeline's cues go through :func:`studio.audio.sfx.render_sfx_track`, which is handed the
   edited dialogue *at the loudness target* on the output clock (and the speech spans) so each effect is
   levelled against the voice it will actually sit under and placed by its sync point; explicit
   :class:`SfxPlacement` lists are placed sample-accurately (file start at ``out_t``, peak-normalised,
   ``gain_db`` dBFS). Each effect's span dips the bed a further 3 dB.
8. **Master**: linear gain to the integrated target (BS.1770-4 gated, measured on the stereo mix as
   delivered) then a **true-peak limiter on 4× oversampled audio** (soxr VHQ): per-sample required gain
   from the oversampled peak, look-ahead window-min (2 ms) with 5 ms hold, Hann-shaped attack (the gain
   is provably ≤ the requirement at every sample), log-linear release; iterated until integrated
   loudness is within 0.05 LU and the 4× true peak is under the ceiling (target TP − 0.5 dB, i.e.
   −1.5 dBTP by default, leaving room for AAC overshoot).

Outputs (``out_dir``): ``mix.wav``, ``mix_nomusic.wav`` (stereo float32, 48 kHz, exactly
``timeline.sample_count`` samples), ``stems/dialogue.wav`` (mono), ``stems/music.wav``, ``stems/sfx.wav``,
``stems/ambience.wav`` (when present) at mix gain, and ``audio_report.json`` (per-seam crossfade length,
placement, correlation, levels and click score; loudness; ducking; warnings).
"""

from __future__ import annotations

import contextlib
import itertools
import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from scipy import signal as sps
from scipy.ndimage import minimum_filter1d

from studio.audio import voice as _voice
from studio.timebase import US_PER_S, round_fraction, sample_index, to_fraction

if TYPE_CHECKING:  # pragma: no cover
    from studio.compile.models import Timeline, TimelineSegment
    from studio.config import Settings
    from studio.doc.model import CutDocument
    from studio.jobs import Job
    from studio.perception.index import TakeIndex

__all__ = [
    "AudioRender",
    "MixParams",
    "SfxPlacement",
    "DialogueResult",
    "render_audio",
    "assemble_dialogue",
    "resolve_audio_spans",
    "time_stretch",
    "duck_envelope",
    "speech_activity",
    "k_weight",
    "integrated_loudness",
    "loudness_curve",
    "true_peak_dbtp",
    "true_peak_limit",
    "master_loudness",
    "seam_click_db",
    "decode_audio",
]

_EPS = 1e-20
_AUTO: Any = object()


@dataclass(frozen=True)
class AudioRender:
    mix: Path  # mix.wav, 48 kHz
    mix_nomusic: Path  # mix_nomusic.wav, 48 kHz
    stems: Mapping[str, Path] = field(default_factory=dict)
    report: Path | None = None
    sample_rate: int = 48_000
    num_samples: int = 0
    loudness: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class MixParams:
    """Craft priors for the mix (doctrine: voice-and-loudness.md, music.md, sfx.md)."""

    xfade_min_ms: float = 10.0
    xfade_max_ms: float = 30.0
    xfade_tight_ms: float = 5.0
    xfade_quiet_rel_db: float = 6.0  # seam level (re floor) at/below which the longest fade is used
    xfade_loud_rel_db: float = 24.0  # … at/above which the shortest normal fade is used
    handle_onset_db: float = 6.0  # a handle this much louder than the kept side contains an onset
    rubberband_context_s: float = 0.25
    floor_keeper_db: float = 6.0  # fill when the dialogue drops this far below the room tone
    duck_lookahead_ms: float = 200.0
    duck_attack_ms: float = 150.0
    duck_release_delay_ms: float = 150.0
    duck_release_ms: float = 700.0
    duck_hold_gap_s: float = 1.2
    music_carve_db: float = 3.0
    music_carve_band_hz: tuple[float, float] = (1000.0, 4000.0)
    sfx_music_duck_db: float = 3.0
    ambience_lu_under_speech: float = -15.0
    limiter_lookahead_ms: float = 2.0
    limiter_hold_ms: float = 5.0
    limiter_release_db_per_s: float = 30.0
    true_peak_margin_db: float = 0.5
    master_fade_in_ms: float = 5.0
    master_fade_out_ms: float = 10.0
    loudness_tolerance_lu: float = 0.05


@dataclass
class SfxPlacement:
    """An SFX to place: file start at ``out_t`` (seconds, exact Fraction preferred)."""

    out_t: Fraction | float
    audio: np.ndarray | str | os.PathLike[str]
    gain_db: float = -12.0
    sfx_id: str = ""
    normalize: bool = True  # peak-normalize the asset before applying gain_db


@dataclass
class DialogueResult:
    audio: np.ndarray  # (N,) float64
    seams: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    room_tone_rms_db: float | None = None
    floor_db: float | None = None


# ============================================================================================== media
def decode_audio(path: str | os.PathLike[str], sr: int = 48_000, channels: int = 2) -> np.ndarray:
    """Decode any audio/video file's first audio stream to float64 ``(channels, n)`` at ``sr``."""
    p = Path(path)
    if p.suffix.lower() in (".wav", ".flac", ".aiff", ".aif", ".ogg"):
        try:
            x, _ = _voice.load_audio(p, sr)
            x2 = x[None, :] if x.ndim == 1 else x
            return _to_channels(x2, channels)
        except Exception:
            pass
    x2 = _voice._ffmpeg_decode(p, sr)
    return _to_channels(x2, channels)


def _to_channels(x2: np.ndarray, channels: int) -> np.ndarray:
    if x2.shape[0] == channels:
        return x2
    if channels == 1:
        return x2.mean(axis=0, keepdims=True)
    if x2.shape[0] == 1:
        return np.repeat(x2, channels, axis=0)
    return x2[:channels] if x2.shape[0] > channels else np.vstack([x2] + [x2[-1:]] * (channels - x2.shape[0]))


def _fit_len(x: np.ndarray, n: int) -> np.ndarray:
    if x.shape[-1] == n:
        return x
    if x.shape[-1] > n:
        return x[..., :n]
    pad = [(0, 0)] * (x.ndim - 1) + [(0, n - x.shape[-1])]
    return np.pad(x, pad)


def _as_stereo_track(a: Any, n: int) -> np.ndarray:
    arr = np.asarray(a, dtype=np.float64)
    if arr.ndim == 1:
        arr = np.vstack([arr, arr])
    elif arr.ndim == 2 and arr.shape[0] > 2 and arr.shape[1] <= 2:
        arr = arr.T
    arr = _to_channels(arr, 2)
    return _fit_len(arr, n)


# ============================================================================================== loudness
# ITU-R BS.1770-4 K-weighting at 48 kHz (stage 1 shelving, stage 2 RLB high-pass).
_K48 = (
    (np.array([1.53512485958697, -2.69169618940638, 1.19839281085285]), np.array([1.0, -1.69065929318241,
                                                                                 0.73248077421585])),
    (np.array([1.0, -2.0, 1.0]), np.array([1.0, -1.99004745483398, 0.99007225036621])),
)


def k_weight(x: np.ndarray, sr: int) -> np.ndarray:
    """K-weighted copy of ``(ch, n)`` audio (exact BS.1770 coefficients at 48 kHz, pyloudnorm's design
    elsewhere)."""
    x2 = np.atleast_2d(np.asarray(x, dtype=np.float64))
    if sr == 48_000:
        stages = [(b, a, 1.0) for b, a in _K48]
    else:
        import pyloudnorm

        meter = pyloudnorm.Meter(sr)
        stages = [(f.b, f.a, f.passband_gain) for f in meter._filters.values()]
    y = x2
    for b, a, g in stages:
        y = g * sps.lfilter(b, a, y, axis=1)
    return y


def _block_ms(xk: np.ndarray, sr: int, block_s: float, step_s: float) -> np.ndarray:
    """Mean square per channel of blocks ``[j·step, j·step + block)`` (BS.1770 block layout)."""
    n = xk.shape[1]
    B = round(block_s * sr)
    if n < B:
        return np.zeros((xk.shape[0], 0))
    step = step_s * sr
    nb = math.floor((n - B) / step + 1e-9) + 1
    starts = np.floor(np.arange(nb) * step).astype(np.int64)
    cs = np.concatenate((np.zeros((xk.shape[0], 1)), np.cumsum(xk * xk, axis=1)), axis=1)
    return (cs[:, starts + B] - cs[:, starts]) / B


def integrated_loudness(x: np.ndarray, sr: int) -> float:
    """BS.1770-4 integrated loudness (LUFS) of ``(ch, n)`` or ``(n,)`` audio (channel weights 1 for L/R).
    ``-inf`` for silence or audio shorter than one 400 ms block."""
    z = _block_ms(k_weight(x, sr), sr, 0.4, 0.1)
    if z.shape[1] == 0:
        return float("-inf")
    tot = z.sum(axis=0)
    lj = -0.691 + 10 * np.log10(np.maximum(tot, _EPS))
    abs_g = lj > -70.0
    if not abs_g.any():
        return float("-inf")
    rel = -0.691 + 10 * np.log10(np.mean(tot[abs_g])) - 10.0
    g = abs_g & (lj > rel)
    return float(-0.691 + 10 * np.log10(np.mean(tot[g])))


def loudness_curve(x: np.ndarray, sr: int, window_s: float = 3.0, hop_s: float = 0.1) -> tuple[np.ndarray, np.ndarray]:
    """Sliding BS.1770 loudness (``window_s`` = 3 → short-term, 0.4 → momentary). Returns
    ``(window_start_seconds, lufs)``."""
    z = _block_ms(k_weight(x, sr), sr, window_s, hop_s)
    if z.shape[1] == 0:
        return np.zeros(0), np.zeros(0)
    lufs = -0.691 + 10 * np.log10(np.maximum(z.sum(axis=0), _EPS))
    return np.arange(z.shape[1]) * hop_s, lufs


def _os_peaks(x2: np.ndarray, sr: int, factor: int, *, chunk_s: float = 5.0) -> np.ndarray:
    """Per-sample oversampled peak: ``out[k] = max_ch max(|x_os[k·factor … k·factor + factor − 1]|)``
    (soxr VHQ, streamed in chunks so memory stays bounded for long mixes; bit-identical to one-shot)."""
    import soxr

    n, ch = x2.shape[1], x2.shape[0]
    out = np.zeros(n)
    if n == 0:
        return out
    st = soxr.ResampleStream(sr, sr * factor, ch, dtype="float64", quality="VHQ")
    step = max(1, round(chunk_s * sr))
    buf = np.zeros(0)
    k = 0
    xt = np.ascontiguousarray(x2.T)
    for i in range(0, n, step):
        y = st.resample_chunk(xt[i: i + step], last=i + step >= n)
        a = np.max(np.abs(y), axis=1) if y.size else np.zeros(0)
        buf = np.concatenate((buf, a))
        m = min(buf.size // factor, n - k)
        if m > 0:
            out[k: k + m] = buf[: m * factor].reshape(m, factor).max(axis=1)
            buf = buf[m * factor:]
            k += m
    if k < n and buf.size:
        m = min(n - k, -(-buf.size // factor))
        pad = np.pad(buf, (0, m * factor - buf.size))
        out[k: k + m] = pad.reshape(m, factor).max(axis=1)
    return np.maximum(out, np.max(np.abs(x2), axis=0))


def true_peak_dbtp(x: np.ndarray, sr: int, oversample: int = 4) -> float:
    """True peak (dBTP) from ``oversample``× soxr-VHQ interpolation (BS.1770 uses 4× at 48 kHz)."""
    x2 = np.atleast_2d(np.asarray(x, dtype=np.float64))
    if x2.size == 0:
        return float("-inf")
    return 20 * math.log10(max(float(np.max(_os_peaks(x2, sr, oversample))), _EPS))


def _forward_min(a: np.ndarray, size: int) -> np.ndarray:
    """``out[j] = min(a[j : j + size])`` (the tail is padded with the last value)."""
    if size <= 1:
        return a.copy()
    return minimum_filter1d(a, size, origin=-(size // 2), mode="nearest")


def true_peak_limit(x: np.ndarray, sr: int, ceiling_dbtp: float, *, lookahead_ms: float = 2.0, hold_ms: float = 5.0,
                    release_db_per_s: float = 30.0, oversample: int = 4, max_iter: int = 6) -> tuple[np.ndarray, dict]:
    """Look-ahead brick-wall limiter driven by the ``oversample``× true peak (linked channels).

    Gain construction (all offline, no latency): ``req[k] = min(1, ceiling / peak_os(k±1))``;
    ``m[j] = min(req[j−hold … j+W−1])``; ``g = HannSmooth_W(m)`` (a normalized kernel over
    ``[k−W+1, k]`` whose every input window contains ``k``, hence ``g ≤ req``); release in dB is
    ``rel[k] = min_{j≤k}(g_dB[j] + (k−j)·rate)``. Verified by re-measuring the true peak; the internal
    ceiling is tightened and the pass repeated if any overshoot remains.
    """
    x2 = np.atleast_2d(np.asarray(x, dtype=np.float64))
    n = x2.shape[1]
    c = 10 ** (ceiling_dbtp / 20)
    info: dict[str, Any] = {"ceiling_dbtp": ceiling_dbtp, "iterations": 0, "max_gr_db": 0.0}
    if n == 0:
        return x2, info
    pk = _os_peaks(x2, sr, oversample)
    pk = np.maximum.reduce([pk, np.concatenate(([0.0], pk[:-1])), np.concatenate((pk[1:], [0.0]))])
    W = max(2, round(lookahead_ms * 1e-3 * sr))
    H = max(0, round(hold_ms * 1e-3 * sr))
    w = np.hanning(W + 2)[1:-1]
    w /= w.sum()
    step = release_db_per_s / sr
    kk = np.arange(n, dtype=np.float64)
    c_int = c
    y = x2
    for it in range(max_iter):
        info["iterations"] = it + 1
        req = np.minimum(1.0, c_int / np.maximum(pk, _EPS))
        if np.all(req >= 1.0):
            y = x2.copy()
            gain_db = np.zeros(n)
        else:
            P = H + W
            reqp = np.concatenate((np.ones(P), req, np.ones(W)))
            cp = _forward_min(reqp, H + W)
            m_ext = cp[: n + W]
            g = np.convolve(m_ext, w)[W: W + n]
            g = np.minimum(g, 1.0)
            gdb = 20 * np.log10(np.maximum(g, _EPS))
            gain_db = kk * step + np.minimum.accumulate(gdb - kk * step)
            gain_db = np.minimum(gain_db, gdb)
            y = x2 * (10 ** (gain_db / 20))[None, :]
        tp = true_peak_dbtp(y, sr, oversample)
        info["max_gr_db"] = round(float(-gain_db.min()), 3)
        info["true_peak_dbtp"] = round(tp, 3)
        if tp <= ceiling_dbtp + 0.005:
            break
        c_int *= 10 ** (-(tp - ceiling_dbtp + 0.02) / 20)
    return y, info


def _fade_edges(x2: np.ndarray, sr: int, in_ms: float, out_ms: float) -> np.ndarray:
    y = x2.copy()
    n = y.shape[1]
    a = min(n // 2, round(in_ms * 1e-3 * sr))
    b = min(n // 2, round(out_ms * 1e-3 * sr))
    if a > 0:
        y[:, :a] *= (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, a)))[None, :]
    if b > 0:
        y[:, n - b:] *= (0.5 + 0.5 * np.cos(np.linspace(0, np.pi, b)))[None, :]
    return y


def master_loudness(x: np.ndarray, sr: int, *, target_lufs: float = -14.0, ceiling_dbtp: float = -1.5,
                    params: MixParams | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Edge fades → linear gain to ``target_lufs`` → 4× true-peak limiter at ``ceiling_dbtp``; iterated
    until the integrated loudness is within ``params.loudness_tolerance_lu``."""
    p = params or MixParams()
    x2 = _fade_edges(np.atleast_2d(np.asarray(x, dtype=np.float64)), sr, p.master_fade_in_ms, p.master_fade_out_ms)
    L0 = integrated_loudness(x2, sr)
    info: dict[str, Any] = {"target_lufs": target_lufs, "ceiling_dbtp": ceiling_dbtp, "input_lufs": L0}
    if not math.isfinite(L0):
        info.update({"gain_db": 0.0, "integrated_lufs": L0, "true_peak_dbtp": true_peak_dbtp(x2, sr), "silent": True})
        return x2, info
    g = target_lufs - L0
    y, linfo, L = x2, {}, L0
    for _ in range(8):
        y, linfo = true_peak_limit(x2 * 10 ** (g / 20), sr, ceiling_dbtp, lookahead_ms=p.limiter_lookahead_ms,
                                   hold_ms=p.limiter_hold_ms, release_db_per_s=p.limiter_release_db_per_s)
        L = integrated_loudness(y, sr)
        if abs(L - target_lufs) <= p.loudness_tolerance_lu:
            break
        g += target_lufs - L
    _t, st = loudness_curve(y, sr, 3.0, 0.1)
    _t, mo = loudness_curve(y, sr, 0.4, 0.1)
    info.update({
        "gain_db": round(g, 3), "integrated_lufs": round(L, 3), "true_peak_dbtp": round(true_peak_dbtp(y, sr), 3),
        "sample_peak_dbfs": round(20 * math.log10(max(float(np.max(np.abs(y))), _EPS)), 3),
        "max_short_term_lufs": round(float(st.max()), 3) if st.size else None,
        "max_momentary_lufs": round(float(mo.max()), 3) if mo.size else None,
        "limiter_max_gr_db": linfo.get("max_gr_db", 0.0), "limiter_iterations": linfo.get("iterations", 0),
    })
    return y, info


# ============================================================================================== stretch
def time_stretch(x: np.ndarray, sr: int, speed: float | Fraction, *, preview: bool = False,
                 rubberband: str | None = None) -> np.ndarray:
    """Pitch-preserving time stretch by ``speed`` (> 1 = faster/shorter) with Rubber Band R3 (CLI ``-3``,
    formant flag ``-F``). Output length is exactly ``round(len / speed)``. Falls back to pedalboard's
    librubberband (also R3, ``high_quality=True``) if the CLI is missing."""
    import soundfile as sf

    x = np.asarray(x, dtype=np.float64)
    sp = to_fraction(speed)
    n_out = round_fraction(Fraction(x.size) / sp)
    if sp == 1 or x.size == 0:
        return _fit_len(x.copy(), n_out)
    peak = float(np.max(np.abs(x)))
    scale = 0.25 / peak if peak > 0.25 else 1.0
    exe = rubberband or shutil.which("rubberband")
    if exe:
        with tempfile.TemporaryDirectory(prefix="studio_rb_") as td:
            fin, fout = Path(td) / "in.wav", Path(td) / "out.wav"
            sf.write(str(fin), (x * scale).astype(np.float32), sr, subtype="FLOAT")
            ratio = 1 / sp
            cmd = [exe, "-3", "-F", "--ignore-clipping", "-q", "-t", f"{float(ratio):.12f}"]
            if preview:
                cmd.append("--window-short")
            res = subprocess.run([*cmd, str(fin), str(fout)], capture_output=True, text=True, check=False)
            if res.returncode != 0 or not fout.exists():
                raise RuntimeError(f"rubberband failed ({res.returncode}): {res.stderr[-300:]}")
            y, _ = sf.read(str(fout), dtype="float64", always_2d=True)
            y = y.mean(axis=1)
    else:  # pragma: no cover - CLI is installed in this environment
        import pedalboard

        y = pedalboard.time_stretch((x * scale).astype(np.float32)[None, :], sr, stretch_factor=float(sp),
                                    high_quality=True, preserve_formants=True)[0].astype(np.float64)
    return _fit_len(y / scale, n_out)


# ============================================================================================== dialogue
@dataclass
class _Plan:
    i: int
    seg: TimelineSegment
    A: Fraction  # natural audio span start (output seconds)
    B: Fraction  # natural audio span end
    S: Fraction  # resolved start
    E: Fraction  # resolved end
    in_kind: str = "start"  # start | xfade | hole
    out_kind: str = "end"  # end | xfade | hole
    nS: int = 0
    nE: int = 0
    dropped: bool = False


def _audio_span(seg: TimelineSegment) -> tuple[Fraction, Fraction]:
    sp = to_fraction(seg.speed)
    A = seg.out_start - Fraction(seg.src_in_us - seg.audio_src_in_us, US_PER_S) / sp
    B = seg.out_start + Fraction(seg.audio_src_out_us - seg.src_in_us, US_PER_S) / sp
    return A, B


def resolve_audio_spans(timeline: Timeline) -> tuple[list[_Plan], list[str]]:
    """Resolve where each segment's audio starts/ends in output time, applying J/L precedence."""
    warnings: list[str] = []
    fps = to_fraction(timeline.fps)
    tol = max(Fraction(1) / fps, Fraction(20, 1000))
    plans = []
    for i, s in enumerate(timeline.segments):
        A, B = _audio_span(s)
        plans.append(_Plan(i=i, seg=s, A=A, B=B, S=A, E=B))
    for a, b in itertools.pairwise(plans):
        sa, sb = a.seg, b.seg
        lead, lag = sb.audio_lead_us > 0, sa.audio_lag_us > 0
        if lead and (not lag or sb.seam_in.kind == "jcut"):
            T = b.A
        elif lag and (not lead or sb.seam_in.kind == "lcut"):
            T = a.B
        elif lead and lag:
            T = (a.B + b.A) / 2
        elif sb.out_start <= sa.out_end + tol and a.B + tol >= b.A:
            T = sb.out_start  # plain cut: the audio seam is the picture seam
        elif a.B > b.A:
            T = (a.B + b.A) / 2  # overlapping audio around a picture gap: split the overlap
        else:
            T = None
        if T is not None and T - tol <= a.B and T + tol >= b.A:
            a.E, b.S = T, T
            a.out_kind, b.in_kind = "xfade", "xfade"
        else:  # a real gap in the audio: both sides fade into room tone
            a.E, b.S = a.B, b.A
            a.out_kind, b.in_kind = "hole", "hole"
    dur = to_fraction(timeline.duration)
    if plans:  # frame snapping leaves < 1 frame between the audio span and the timeline edges: extend
        first, last = plans[0], plans[-1]
        if first.seg.out_start == 0 and 0 < first.S <= tol:
            first.S = Fraction(0)
        if last.seg.out_end == dur and dur - tol <= last.E < dur:
            last.E = dur
    for p in plans:
        p.S = max(Fraction(0), p.S)
        p.E = min(dur, p.E)
        p.nS = sample_index(p.S, timeline.sample_rate)
        p.nE = sample_index(p.E, timeline.sample_rate)
        if p.nE - p.nS < 8:
            p.dropped = True
            warnings.append(f"{p.seg.seg_id}: no audio left after J/L resolution; dropped")
    return plans, warnings


def _render_piece(voice: np.ndarray, sr: int, seg: TimelineSegment, p0: int, p1: int, *, context_s: float,
                  preview: bool) -> tuple[np.ndarray, np.ndarray]:
    """Output samples ``[p0, p1)`` of ``seg``'s audio (with handles) and a validity mask (inside source)."""
    n_src = voice.size
    L = p1 - p0
    piece = np.zeros(L)
    if L <= 0 or n_src == 0:
        return piece, np.zeros(max(L, 0), dtype=bool)
    out0 = to_fraction(seg.out_start) * sr
    src0 = Fraction(seg.src_in_us * sr, US_PER_S)
    sp = to_fraction(seg.speed)
    outs = np.arange(p0, p1)
    if sp == 1:
        off = round_fraction(src0 - out0)
        idx = outs + off
        valid = (idx >= 0) & (idx < n_src)
        piece[valid] = voice[idx[valid]]
        return piece, valid
    s_lo = src0 + (p0 - out0) * sp
    s_hi = src0 + (p1 - out0) * sp
    C = round(context_s * sr)
    c0 = max(0, math.floor(s_lo) - C)
    c1 = min(n_src, math.ceil(s_hi) + C)
    if c1 - c0 < 16:
        return piece, np.zeros(L, dtype=bool)
    y = time_stretch(voice[c0:c1], sr, sp, preview=preview)
    off = round_fraction((src0 - c0) / sp - out0)
    j = outs + off
    srcpos = float(src0) + (outs - float(out0)) * float(sp)
    valid = (j >= 0) & (j < y.size) & (srcpos >= 0) & (srcpos < n_src)
    piece[valid] = y[j[valid]]
    return piece, valid


def _lvl(piece: np.ndarray, valid: np.ndarray, off: int, a: int, b: int) -> float | None:
    """RMS dB of piece samples at output ``[a, b)``; None if any is outside the piece or invalid."""
    i0, i1 = a - off, b - off
    if b <= a or i0 < 0 or i1 > piece.size or not valid[i0:i1].all():
        return None
    return _voice._rms_db(piece[i0:i1])


def _law(X: int, r: float) -> tuple[np.ndarray, np.ndarray]:
    th = (np.arange(X) + 0.5) / X * (np.pi / 2)
    k = 1.0 / np.sqrt(1.0 + r * np.sin(2 * th))
    return np.cos(th) * k, np.sin(th) * k  # (fade_out, fade_in)


def _xfade_ms_for(level_rel: float, p: MixParams) -> float:
    lo, hi = p.xfade_quiet_rel_db, p.xfade_loud_rel_db
    t = min(1.0, max(0.0, (level_rel - lo) / (hi - lo)))
    return p.xfade_max_ms + t * (p.xfade_min_ms - p.xfade_max_ms)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 4:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    den = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
    if den <= 1e-18:
        return 0.0
    return float(min(1.0, max(0.0, float(np.dot(a, b)) / den)))


def seam_click_db(x: np.ndarray, sr: int, n: int, *, win_ms: float = 5.0, ctx_ms: float = 60.0) -> float | None:
    """Click score at sample ``n``: peak of the >4 kHz band within ±``win_ms`` over its 99.5th
    percentile in the surrounding ±``ctx_ms`` (dB). ≲ 3 dB means nothing stands out."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim == 2:
        x = x.mean(axis=0)
    w = round(win_ms * 1e-3 * sr)
    c = round(ctx_ms * 1e-3 * sr)
    pad = round(0.01 * sr)
    a, b = max(0, n - c - pad), min(x.size, n + c + pad)
    if b - a < 4 * w:
        return None
    sos = sps.butter(4, 4000, "highpass", fs=sr, output="sos")
    hp = np.abs(sps.sosfiltfilt(sos, x[a:b]))
    m = n - a
    inner = hp[max(0, m - w): m + w]
    outer = np.concatenate((hp[pad: max(pad, m - w)], hp[m + w: hp.size - pad]))
    if inner.size == 0 or outer.size < 10:
        return None
    ref = float(np.percentile(outer, 99.5))
    return round(20 * math.log10((float(inner.max()) + 1e-12) / (ref + 1e-12)), 2)


def assemble_dialogue(timeline: Timeline, voice: np.ndarray, sr: int | None = None, *, tone: np.ndarray | None = None,
                      params: MixParams | None = None, preview: bool = False,
                      index: TakeIndex | None = None) -> DialogueResult:
    """Cut the (processed) dialogue per the timeline: sample-accurate extraction, J/L, speed, adaptive
    correlation-aware crossfades, room-tone fill. Returns mono float64 of exactly ``sample_count``."""
    p = params or MixParams()
    sr = int(sr or timeline.sample_rate)
    N = timeline.sample_count
    voice = np.asarray(voice, dtype=np.float64)
    if voice.ndim == 2:
        voice = voice.mean(axis=0)
    res = DialogueResult(audio=np.zeros(N))
    plans, warns = resolve_audio_spans(timeline)
    res.warnings += warns
    plans = [q for q in plans if not q.dropped]
    Hm = round(p.xfade_max_ms * 1e-3 * sr)
    H = Hm + 2
    W = round(0.015 * sr)
    floor = _voice.noise_floor_db(voice, sr) if voice.size else -200.0
    res.floor_db = round(floor, 2)

    pieces: list[tuple[np.ndarray, np.ndarray, int]] = []
    for q in plans:
        p0, p1 = max(0, q.nS - H), min(N, q.nE + H)
        pc, v = _render_piece(voice, sr, q.seg, p0, p1, context_s=p.rubberband_context_s, preview=preview)
        pieces.append((pc, v, p0))

    # fade regions per plan: (r0, r1, law_curve) for in and out
    fin: dict[int, tuple[int, int, np.ndarray]] = {}
    fout: dict[int, tuple[int, int, np.ndarray]] = {}
    cross: list[tuple[int, int, int, int, float]] = []  # (ia, ib, r0, r1, r)

    def own_len(k: int) -> int:
        return plans[k].nE - plans[k].nS

    def edge(k: int, side: str) -> tuple[int, int, dict[str, Any]]:
        q = plans[k]
        pc, v, off = pieces[k]
        nE = q.nE if side == "out" else q.nS
        if side == "out":
            own, hnd = _lvl(pc, v, off, nE - W, nE), _lvl(pc, v, off, nE, nE + Hm)
        else:
            own, hnd = _lvl(pc, v, off, nE, nE + W), _lvl(pc, v, off, nE - Hm, nE)
        own_v = own if own is not None else floor
        X = round(_xfade_ms_for(own_v - floor, p) * 1e-3 * sr)
        X = max(8, min(X, own_len(k) // 2))
        ok = hnd is not None and hnd <= max(own_v, floor + 6) + p.handle_onset_db
        if ok:
            r0 = nE - X // 2
        else:
            r0 = nE - X if side == "out" else nE
        r0, r1 = max(0, r0), min(N, r0 + X)
        return r0, r1, {"own_db": own, "handle_db": hnd, "handle_ok": ok}

    for k, q in enumerate(plans):
        # incoming edge
        if q.in_kind == "hole" or (q.in_kind == "start" and q.nS > 0):
            r0, r1, info = edge(k, "in")
            if r1 > r0:
                fin[k] = (r0, r1, _law(r1 - r0, 0.0)[1])
                res.seams.append({"kind": "roomtone_in", "segment": q.seg.seg_id, "sample": q.nS,
                                  "t": float(q.S), "xfade_ms": round((r1 - r0) / sr * 1000, 2),
                                  "placement": "symmetric" if info["handle_ok"] else "inside", "r": 0.0,
                                  "levels_db": {k2: (None if v2 is None else round(v2, 2)) for k2, v2 in info.items()
                                                if k2 != "handle_ok"}})
        if q.out_kind == "hole" or (q.out_kind == "end" and q.nE < N):
            r0, r1, info = edge(k, "out")
            if r1 > r0:
                fout[k] = (r0, r1, _law(r1 - r0, 0.0)[0])
                res.seams.append({"kind": "roomtone_out", "segment": q.seg.seg_id, "sample": q.nE,
                                  "t": float(q.E), "xfade_ms": round((r1 - r0) / sr * 1000, 2),
                                  "placement": "symmetric" if info["handle_ok"] else "inside", "r": 0.0,
                                  "levels_db": {k2: (None if v2 is None else round(v2, 2)) for k2, v2 in info.items()
                                                if k2 != "handle_ok"}})

    for k in range(len(plans) - 1):
        a, b = plans[k], plans[k + 1]
        if a.out_kind != "xfade":
            continue
        nT = a.nE
        pa, va, oa = pieces[k]
        pb, vb, ob = pieces[k + 1]
        a_in = _lvl(pa, va, oa, nT - W, nT)
        a_post = _lvl(pa, va, oa, nT, nT + Hm)
        b_pre = _lvl(pb, vb, ob, nT - Hm, nT)
        b_in = _lvl(pb, vb, ob, nT, nT + W)
        a_iv = a_in if a_in is not None else floor
        b_iv = b_in if b_in is not None else floor
        S_rel = max(a_iv, b_iv) - floor
        a_ok = a_post is not None and a_post <= max(a_iv, floor + 6) + p.handle_onset_db
        b_ok = b_pre is not None and b_pre <= max(b_iv, floor + 6) + p.handle_onset_db
        X_ms = _xfade_ms_for(S_rel, p)
        if a_ok and b_ok:
            placement = "symmetric"
        elif b_ok:
            placement = "before"
        elif a_ok:
            placement = "after"
        else:
            placement, X_ms = "tight", p.xfade_tight_ms
        X = round(X_ms * 1e-3 * sr)
        if placement in ("symmetric", "tight"):
            X = min(X, own_len(k), own_len(k + 1), 2 * Hm)  # half of it lies in each segment
            r0 = nT - X // 2
        elif placement == "before":
            X = min(X, own_len(k) // 2)
            r0 = nT - X
        else:
            X = min(X, own_len(k + 1) // 2)
            r0 = nT
        X = max(X, 4)
        r0, r1 = max(0, r0), min(N, r0 + X)
        X = r1 - r0
        seg_a = pa[r0 - oa: r1 - oa] if r0 - oa >= 0 and r1 - oa <= pa.size else np.zeros(0)
        seg_b = pb[r0 - ob: r1 - ob] if r0 - ob >= 0 and r1 - ob <= pb.size else np.zeros(0)
        r = _corr(seg_a, seg_b) if seg_a.size == seg_b.size else 0.0
        go, gi = _law(X, r) if X > 0 else (np.zeros(0), np.zeros(0))
        fout[k] = (r0, r1, go)
        fin[k + 1] = (r0, r1, gi)
        cross.append((k, k + 1, r0, r1, r))
        res.seams.append({"kind": "xfade", "from": a.seg.seg_id, "to": b.seg.seg_id, "sample": nT, "t": float(a.E),
                          "xfade_ms": round(X / sr * 1000, 2), "placement": placement, "r": round(r, 3),
                          "law": "equal_gain" if r > 0.9 else "equal_power" if r < 0.1 else "adaptive",
                          "levels_db": {"a_in": a_in, "a_post": a_post, "b_pre": b_pre, "b_in": b_in,
                                        "floor": round(floor, 2)}})
        res.warnings += _truncation_warnings(a, b, index)

    out = np.zeros(N)
    cov = np.zeros(N)
    gains: list[np.ndarray] = []
    for k, q in enumerate(plans):
        pc, v, off = pieces[k]
        g = np.zeros(pc.size)
        s_own = q.nS - off
        e_own = q.nE - off
        if k in fin:
            r0, r1, curve = fin[k]
            g[r0 - off: r1 - off] = curve
            s_one = r1 - off
        else:
            s_one = s_own
        if k in fout:
            r0, r1, curve = fout[k]
            e_one = r0 - off
            g[max(s_one, 0): max(e_one, 0)] = 1.0
            g[r0 - off: r1 - off] = curve
        else:
            g[max(s_one, 0): max(e_own, 0)] = 1.0
        g *= v
        gains.append(g)
        out[off: off + pc.size] += g * pc
        cov[off: off + pc.size] += g * g
        res.segments.append({"seg_id": q.seg.seg_id, "start_sample": q.nS, "end_sample": q.nE,
                             "speed": q.seg.speed, "in": q.in_kind, "out": q.out_kind})
    for ia, ib, r0, r1, r in cross:
        if r <= 0:
            continue
        ga, gb = gains[ia], gains[ib]
        oa, ob = pieces[ia][2], pieces[ib][2]
        cov[r0:r1] += 2 * r * ga[r0 - oa: r1 - oa] * gb[r0 - ob: r1 - ob]

    if tone is not None and tone.size:
        t = _fit_len(np.asarray(tone, dtype=np.float64), N)
        rt_rms = _voice._rms_db(t[t != 0]) if np.any(t != 0) else -200.0
        res.room_tone_rms_db = round(rt_rms, 2)
        fill = np.sqrt(np.clip(1.0 - cov, 0.0, 1.0))
        out += fill * t
        if rt_rms > -150:
            out += _floor_keeper(out, t, sr, rt_rms, p.floor_keeper_db) * t
    for s in res.seams:
        s["click_db"] = seam_click_db(out, sr, int(s["sample"]))
    res.audio = out
    return res


def _floor_keeper(x: np.ndarray, tone: np.ndarray, sr: int, tone_db: float, below_db: float) -> np.ndarray:
    """Extra room-tone gain for stretches where ``x`` falls more than ``below_db`` under the tone level."""
    n = x.size
    c, db = _voice.frame_levels_db(x, sr, 0.02, 0.005)
    target_p = 10 ** ((tone_db - below_db) / 10)
    Ep = 10 ** (db / 10)
    g = np.sqrt(np.clip(1.0 - Ep / target_p, 0.0, 1.0))
    if not np.any(g > 0):
        return np.zeros(n)
    g = np.convolve(g, np.ones(4) / 4, mode="same")
    return np.interp(np.arange(n), c, g)


def _truncation_warnings(a: _Plan, b: _Plan, index: TakeIndex | None) -> list[str]:
    if index is None:
        return []
    out = []
    a_src = a.seg.out_to_src_us(a.E)
    b_src = b.seg.out_to_src_us(b.S)
    for wid in a.seg.word_ids:
        if index.has_word(wid) and index.word(wid).end_us > a_src + 5_000 and index.word(wid).start_us < a_src:
            out.append(f"seam {a.seg.seg_id}->{b.seg.seg_id}: audio of {wid} cut at its tail")
    for wid in b.seg.word_ids:
        if index.has_word(wid) and index.word(wid).start_us < b_src - 5_000 and index.word(wid).end_us > b_src:
            out.append(f"seam {a.seg.seg_id}->{b.seg.seg_id}: audio of {wid} cut at its head")
    return out


# ============================================================================================== ducking
def _merge(iv: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(iv):
        if out and a - out[-1][1] < gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def speech_activity(timeline: Timeline, dialogue: np.ndarray | None, sr: int) -> tuple[list[tuple[int, int]], str]:
    """Speech spans (output samples) from the timeline's word map; energy detection on the dialogue when
    there are no mapped words. Returns ``(intervals, source)``."""
    iv = [(sample_index(s.out_start, sr), sample_index(s.out_end, sr)) for s in timeline.word_map.values()
          if s is not None and s.out_end > s.out_start]
    if iv:
        return _merge(iv, 1), "word_map"
    if dialogue is None or dialogue.size == 0:
        return [], "none"
    x = dialogue if dialogue.ndim == 1 else dialogue.mean(axis=0)
    c, db = _voice.frame_levels_db(x, sr, 0.02, 0.01)
    fl = _voice.noise_floor_db(x, sr)
    act = db > max(fl + 12.0, -70.0)
    if act.any():
        ref = float(np.median(db[act]))
        act &= db > ref - 15.0
    hop = round(0.01 * sr)
    iv = [(int(c[i] - hop), int(c[i] + hop)) for i in np.flatnonzero(act)]
    return _merge(iv, round(0.05 * sr)), "energy"


def duck_envelope(intervals: Sequence[tuple[int, int]], n: int, sr: int, *,
                  params: MixParams | None = None) -> np.ndarray:
    """``d(t) ∈ [0, 1]`` (1 = fully ducked): look-ahead raised-cosine attack before speech, hold through
    gaps shorter than ``duck_hold_gap_s``, raised-cosine release after a short delay."""
    p = params or MixParams()
    d = np.zeros(n)
    look = round(p.duck_lookahead_ms * 1e-3 * sr)
    att = max(1, round(p.duck_attack_ms * 1e-3 * sr))
    rdel = round(p.duck_release_delay_ms * 1e-3 * sr)
    rel = max(1, round(p.duck_release_ms * 1e-3 * sr))
    for a, b in _merge(list(intervals), round(p.duck_hold_gap_s * sr)):
        s0 = a - look
        e0, e1 = b + rdel, b + rdel + rel
        env_idx = np.arange(max(0, s0), min(n, e1))
        if env_idx.size == 0:
            continue
        up = 0.5 - 0.5 * np.cos(np.pi * np.clip((env_idx - s0) / att, 0, 1))
        down = 0.5 + 0.5 * np.cos(np.pi * np.clip((env_idx - e0) / rel, 0, 1))
        env = np.minimum(up, down)
        d[env_idx] = np.maximum(d[env_idx], env)
    return d


# ============================================================================================== layers
def _resolve_path(job: Job | None, path: str | None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute() and job is not None:
        p = job.root / p
    return p if p.exists() else None


def _place(track: np.ndarray, clip: np.ndarray, start: int) -> tuple[int, int]:
    n = track.shape[1]
    a, b = start, start + clip.shape[1]
    ca, cb = max(0, -a), clip.shape[1] - max(0, b - n)
    a, b = max(0, a), min(n, b)
    if b > a and cb > ca:
        track[:, a:b] += clip[:, ca:cb]
    return a, b


def _activity_spans(track: np.ndarray, sr: int, *, thresh_db: float = -70.0, bridge_s: float = 0.08
                    ) -> list[tuple[int, int]]:
    """Active stretches of a pre-mixed track (10 ms frames above ``thresh_db``), one span per event."""
    c, lv = _voice.frame_levels_db(np.atleast_2d(track), sr, 0.01, 0.005)
    hop = round(0.005 * sr)
    iv = [(max(0, int(c[i]) - hop), int(c[i]) + hop) for i in np.flatnonzero(lv > thresh_db)]
    return _merge(iv, round(bridge_s * sr))


def _sfx_layer(job: Job | None, timeline: Timeline, sr: int, N: int, sfx: Any, warnings: list[str], *,
               dialogue: np.ndarray | None = None, speech_spans_s: Sequence[tuple[float, float]] | None = None,
               music_key: str | None = None) -> tuple[np.ndarray, list[tuple[int, int]], list[dict[str, Any]]]:
    """SFX track ``(2, N)``, per-event sample spans (for the music dip) and placement metadata.

    By default the timeline's cues go through :func:`studio.audio.sfx.render_sfx_track`, which levels each
    effect against ``dialogue`` (the edited dialogue at the loudness target, on the output clock), pitches
    tonal effects into the bed's ``music_key`` and places each by its sync point; explicit
    :class:`SfxPlacement` lists are placed file-start-at-``out_t`` with the asset peak-normalised and
    ``gain_db`` applied."""
    track = np.zeros((2, N))
    spans: list[tuple[int, int]] = []
    meta: list[dict[str, Any]] = []
    if sfx is None:
        return track, spans, meta
    if isinstance(sfx, np.ndarray):
        tr = _as_stereo_track(sfx, N)
        return tr, _activity_spans(tr, sr), [{"source": "array"}]
    items: list[SfxPlacement] = []
    if sfx is _AUTO:
        tr = None
        rep: list[dict[str, Any]] = []
        if job is not None and timeline.sfx:
            try:
                from studio.audio import sfx as _sfx

                tr = _sfx.render_sfx_track(job, timeline, sr=sr, dialogue=dialogue, speech_spans=speech_spans_s,
                                           music_key=music_key, report=rep)
            except Exception as e:  # never lose the render over an SFX module failure
                warnings.append(f"render_sfx_track failed ({type(e).__name__}: {e}); placing timeline SFX directly")
                tr = None
        if tr is not None:
            arr = _as_stereo_track(np.asarray(tr, dtype=np.float64), N)
            return arr, _activity_spans(arr, sr), rep or [{"source": "studio.audio.sfx"}]
        for c in timeline.sfx:
            path = c.asset_path or (c.asset.path if c.asset is not None else None)
            rp = _resolve_path(job, path)
            if rp is None:
                warnings.append(f"sfx {c.sfx_id}: no asset file; skipped")
                continue
            items.append(SfxPlacement(out_t=c.out_t, audio=rp, gain_db=c.gain_db, sfx_id=c.sfx_id))
    else:
        for c in sfx:
            if isinstance(c, SfxPlacement):
                items.append(c)
            else:  # TimelineSfx-like
                rp = _resolve_path(job, getattr(c, "asset_path", None))
                if rp is None:
                    warnings.append(f"sfx {getattr(c, 'sfx_id', '?')}: no asset file; skipped")
                    continue
                items.append(SfxPlacement(out_t=c.out_t, audio=rp, gain_db=c.gain_db, sfx_id=c.sfx_id))
    for it in items:
        clip = decode_audio(it.audio, sr, 2) if not isinstance(it.audio, np.ndarray) else \
            _to_channels(np.atleast_2d(np.asarray(it.audio, dtype=np.float64)), 2)
        if clip.shape[1] == 0:
            continue
        pk = float(np.max(np.abs(clip)))
        if it.normalize and pk > 0:
            clip = clip / pk
        clip = clip * 10 ** (it.gain_db / 20)
        m = min(clip.shape[1] // 2, round(0.002 * sr))
        if m > 0:  # declick the file edges
            clip[:, :m] *= np.linspace(0, 1, m)[None, :]
            clip[:, -m:] *= np.linspace(1, 0, m)[None, :]
        start = sample_index(to_fraction(it.out_t), sr)
        a, b = _place(track, clip, start)
        spans.append((a, b))
        meta.append({"sfx_id": it.sfx_id, "sample": start, "gain_db": it.gain_db,
                     "len_ms": round(clip.shape[1] / sr * 1000)})
    return track, spans, meta


def _music_key(job: Job | None, doc: CutDocument | None, music: Any) -> str | None:
    """Key of the document's music bed (from its analysis sidecar) so tonal SFX sit in key; None when the
    render has no registered bed or the key is unknown."""
    if music is not _AUTO or job is None or doc is None or doc.audio.music is None:
        return None
    spec = doc.audio.music
    aid = spec.asset_id or (spec.asset.id if spec.asset is not None else None)
    if not aid:
        return None
    try:
        from studio.audio import music as _music

        return _music.music_analysis_for(job, aid).key
    except Exception:  # an unanalysable bed only loses the in-key nicety
        return None


def _music_bed(job: Job | None, doc: CutDocument | None, timeline: Timeline, sr: int, N: int, music: Any,
               warnings: list[str]) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Pre-ducking bed ``(2, N)`` (None when there is no music) and where it came from."""
    if music is None:
        return None, {"source": "none"}
    if isinstance(music, np.ndarray):
        return _as_stereo_track(music, N), {"source": "array"}
    if isinstance(music, (str, os.PathLike)):
        bed = decode_audio(music, sr, 2)
        tm = timeline.music
        start = sample_index(tm.out_start, sr) if tm is not None else 0
        return _fit_len(_shift(bed, start), N), {"source": "file"}
    tm = timeline.music
    if tm is None:
        return None, {"source": "none"}
    if job is not None and doc is not None and doc.audio.music is not None:
        try:
            from studio.audio import music as _music

            # the bed is levelled here (against the dialogue under speech), so ask for it unlevelled
            arr = _music.render_music_bed(job, doc.audio.music, timeline, sr=sr, level=False)
            if arr is not None:
                return _as_stereo_track(arr, N), {"source": "studio.audio.music"}
        except Exception as e:  # never lose the render over a music module failure
            warnings.append(f"render_music_bed failed ({type(e).__name__}: {e}); placing the timeline asset "
                            "directly")
    path = tm.asset_path or (tm.asset.path if tm.asset is not None else None)
    rp = _resolve_path(job, path)
    if rp is None:
        warnings.append("timeline music has no asset file; no music")
        return None, {"source": "none"}
    bed = decode_audio(rp, sr, 2)
    a_in = tm.asset_in_us * sr // US_PER_S
    s, e = sample_index(tm.out_start, sr), sample_index(tm.out_end, sr)
    seg = bed[:, a_in: a_in + max(0, e - s)]
    if seg.shape[1] < e - s:
        warnings.append(f"music asset ends {((e - s) - seg.shape[1]) / sr:.2f}s before the timeline music span")
    fi = min(seg.shape[1] // 2, round(tm.fade_in_ms * 1e-3 * sr))
    fo = min(seg.shape[1] // 2, round(tm.fade_out_ms * 1e-3 * sr))
    seg = seg.copy()
    if fi > 0:
        seg[:, :fi] *= np.sin(np.linspace(0, np.pi / 2, fi))[None, :] ** 2
    if fo > 0:
        seg[:, -fo:] *= np.cos(np.linspace(0, np.pi / 2, fo))[None, :] ** 2
    track = np.zeros((2, N))
    _place(track, seg, s)
    return track, {"source": "timeline_asset", "path": str(rp)}


def _shift(x: np.ndarray, start: int) -> np.ndarray:
    if start <= 0:
        return x[:, -start:]
    return np.concatenate((np.zeros((x.shape[0], start)), x), axis=1)


def _ambience_layer(job: Job | None, timeline: Timeline, sr: int, N: int,
                    warnings: list[str]) -> list[tuple[np.ndarray, str]]:
    """Natural sound of inserts whose audio mode asks for it (``duck`` / ``with_sfx``), faded 30 ms."""
    out = []
    for ins in timeline.inserts:
        if ins.audio not in ("duck", "with_sfx"):
            continue
        rp = _resolve_path(job, ins.asset_path)
        if rp is None:
            continue
        try:
            clip = decode_audio(rp, sr, 2)
        except Exception:
            continue  # no audio stream
        a_in = ins.asset_in_us * sr // US_PER_S
        s, e = sample_index(ins.out_start, sr), sample_index(ins.out_end, sr)
        seg = clip[:, a_in: a_in + max(0, e - s)].copy()
        if seg.shape[1] < 16 or not np.any(np.abs(seg) > 1e-6):
            continue
        f = min(seg.shape[1] // 2, round(0.03 * sr))
        seg[:, :f] *= np.sin(np.linspace(0, np.pi / 2, f))[None, :]
        seg[:, -f:] *= np.cos(np.linspace(0, np.pi / 2, f))[None, :]
        tr = np.zeros((2, N))
        _place(tr, seg, s)
        out.append((tr, ins.insert_id))
    return out


def _band_component(x2: np.ndarray, sr: int, band: tuple[float, float]) -> np.ndarray:
    sos = sps.butter(2, [band[0], min(band[1], sr * 0.45)], "bandpass", fs=sr, output="sos")
    return sps.sosfiltfilt(sos, x2, axis=1)


def _level_under(track: np.ndarray, ref_lufs: float, rel_lu: float, sr: int,
                 sel: np.ndarray | None) -> tuple[float, float]:
    """Gain (dB) putting ``track``'s loudness (over ``sel`` samples when long enough) at ``ref + rel``."""
    part = track[:, sel] if sel is not None and sel.sum() >= sr else track[:, np.max(np.abs(track), axis=0) > 1e-7]
    L = integrated_loudness(part, sr) if part.shape[1] >= round(0.4 * sr) else float("-inf")
    if not math.isfinite(L) or not math.isfinite(ref_lufs):
        return 0.0, L
    return ref_lufs + rel_lu - L, L


# ============================================================================================== render
def render_audio(job: Job, doc: CutDocument, timeline: Timeline, out_dir: str | os.PathLike[str], *,
                 preview: bool = False, index: TakeIndex | None = None,
                 voice_wav: str | os.PathLike[str] | None = None, music: Any = _AUTO, sfx: Any = _AUTO,
                 params: MixParams | None = None, settings: Settings | None = None,
                 guard: Any = None) -> AudioRender:
    """Render ``mix.wav`` and ``mix_nomusic.wav`` (+ stems and ``audio_report.json``) into ``out_dir``.

    Optional overrides: ``voice_wav`` (an already processed dialogue track, full source length),
    ``music`` (``None`` = no music, an array ``(2, N)``/``(N,)`` pre-ducking bed aligned to the timeline,
    or a file placed at the timeline music start), ``sfx`` (``None``, a list of :class:`SfxPlacement` /
    ``TimelineSfx``, or a pre-mixed array). By default they come from the job, the document and the
    timeline. ``guard`` is passed to the voice chain's isolation stage.
    """
    p = params or MixParams()
    sr = int(timeline.sample_rate)
    N = timeline.sample_count
    out = Path(out_dir)
    (out / "stems").mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    if index is None:
        try:
            index = job.load_index()
        except Exception:
            index = None
            warnings.append("no take index: room tone and masks from energy detection")

    # 1 — processed dialogue (full source, once)
    voice_log: str | None = None
    if voice_wav is not None:
        voice, _ = _voice.load_audio(voice_wav, sr, mono=True)
    elif job.audio_path.exists():
        vpath = _voice.process_dialogue(job, index, doc.audio.voice, settings=settings, guard=guard, preview=preview,
                                        sr=sr)
        voice, _ = _voice.load_audio(vpath, sr, mono=True)
        voice_log = str(vpath.with_suffix(".json"))
    else:
        voice = np.zeros(0)
        warnings.append("job has no dialogue audio (media/audio.wav)")

    # 2 — room tone + dialogue edit
    tone = None
    if doc.audio.room_tone and voice.size:
        tone = _voice.build_room_tone(voice, sr, index, N / sr, seed=0)
        if not np.any(tone != 0):
            warnings.append("no usable pause for room tone in this take")
    elif not doc.audio.room_tone:
        warnings.append("room tone disabled by the document: gaps may contain digital silence")
    dres = assemble_dialogue(timeline, voice, sr, tone=tone, params=p, preview=preview, index=index)
    warnings += dres.warnings
    target = float(doc.audio.loudness_target_lufs)
    ceiling = float(doc.audio.true_peak_dbtp) - p.true_peak_margin_db
    d_st = np.vstack([dres.audio, dres.audio])
    Ld = integrated_loudness(d_st, sr)
    d_gain = target - Ld if math.isfinite(Ld) else 0.0
    d_st *= 10 ** (d_gain / 20)
    L_speech = target if math.isfinite(Ld) else float("-inf")

    # 3 — speech activity + duck envelope
    act, act_src = speech_activity(timeline, dres.audio, sr)
    d = duck_envelope(act, N, sr, params=p)
    speech_sel = d >= 0.99

    # 4 — SFX (levelled against the dialogue as it will sit in the mix)
    sfx_st, sfx_spans, sfx_meta = _sfx_layer(job, timeline, sr, N, sfx, warnings, dialogue=d_st[0],
                                             speech_spans_s=[(a / sr, b / sr) for a, b in act],
                                             music_key=_music_key(job, doc, music))

    # 5 — insert ambience (natural sound under b-roll), level set against the dialogue
    amb_st = np.zeros((2, N))
    amb_meta = []
    for tr, iid in _ambience_layer(job, timeline, sr, N, warnings):
        g, L = _level_under(tr, L_speech, p.ambience_lu_under_speech, sr, None)
        amb_st += tr * 10 ** (g / 20)
        amb_meta.append({"insert_id": iid, "gain_db": round(g, 2),
                         "native_lufs": None if not math.isfinite(L) else round(L, 2)})

    # 6 — music: carve, level under speech, duck, extra dip under SFX
    bed, mmeta = _music_bed(job, doc, timeline, sr, N, music, warnings)
    mus_st = np.zeros((2, N))
    duck_info: dict[str, Any] = {"activity_source": act_src, "speech_intervals": len(act)}
    if bed is not None and np.any(np.abs(bed) > 1e-9):
        tm = timeline.music
        level = tm.level_lu_under_speech if tm is not None else -18.0
        duck_on = tm.duck if tm is not None else True
        duck_db = float(tm.duck_db) if tm is not None else 6.0
        b2 = bed
        if duck_on and p.music_carve_db > 0:
            band = _band_component(bed, sr, p.music_carve_band_hz)
            b2 = bed - (1.0 - 10 ** (-p.music_carve_db * d / 20))[None, :] * band
        base, Lm = _level_under(b2, L_speech, level, sr, speech_sel if duck_on else None)
        gdb = np.full(N, base)
        if duck_on:
            gdb += duck_db * (1.0 - d)
        if sfx_spans and p.sfx_music_duck_db > 0:
            dip = np.zeros(N)
            r = round(0.02 * sr)
            for a, b in sfx_spans:
                idx = np.arange(max(0, a - r), min(N, b + r))
                env = np.minimum(np.clip((idx - (a - r)) / r, 0, 1), np.clip(((b + r) - idx) / r, 0, 1))
                dip[idx] = np.maximum(dip[idx], env)
            gdb -= p.sfx_music_duck_db * dip
        mus_st = b2 * (10 ** (gdb / 20))[None, :]
        duck_info.update({"level_lu_under_speech": level, "duck": duck_on, "duck_db": duck_db,
                          "base_gain_db": round(base, 2),
                          "music_lufs_under_speech_pre": None if not math.isfinite(Lm) else round(Lm, 2),
                          "carve_db": p.music_carve_db if duck_on else 0.0,
                          "ducked_fraction": round(float(np.mean(speech_sel)), 4)})

    # 7 — mixes + master
    mix_nm = d_st + sfx_st + amb_st
    mix = mix_nm + mus_st
    y, minfo = master_loudness(mix, sr, target_lufs=target, ceiling_dbtp=ceiling, params=p)
    y_nm, minfo_nm = master_loudness(mix_nm, sr, target_lufs=target, ceiling_dbtp=ceiling, params=p)
    for info, name in ((minfo, "mix"), (minfo_nm, "mix_nomusic")):
        if info.get("limiter_max_gr_db", 0.0) > 3.0:
            warnings.append(f"{name}: limiter pulled {info['limiter_max_gr_db']:.1f} dB (> 3 dB: fix compression, "
                            "not the ceiling)")
        if info.get("silent"):
            warnings.append(f"{name}: silent, loudness not normalized")

    mix_path = _voice.write_audio(out / "mix.wav", y, sr)
    nm_path = _voice.write_audio(out / "mix_nomusic.wav", y_nm, sr)
    g_lin = 10 ** (minfo.get("gain_db", 0.0) / 20)
    stems = {"dialogue": _voice.write_audio(out / "stems" / "dialogue.wav", d_st[0] * g_lin, sr)}
    if bed is not None:
        stems["music"] = _voice.write_audio(out / "stems" / "music.wav", mus_st * g_lin, sr)
    if sfx_meta or np.any(sfx_st != 0):
        stems["sfx"] = _voice.write_audio(out / "stems" / "sfx.wav", sfx_st * g_lin, sr)
    if amb_meta:
        stems["ambience"] = _voice.write_audio(out / "stems" / "ambience.wav", amb_st * g_lin, sr)

    report = {
        "sample_rate": sr, "num_samples": N,
        "duration": f"{timeline.duration.numerator}/{timeline.duration.denominator}",
        "doc_version": timeline.doc_version, "preview": preview,
        "voice": {"spec": doc.audio.voice.model_dump(mode="json"), "log": voice_log,
                  "floor_db": dres.floor_db, "room_tone_rms_db": dres.room_tone_rms_db,
                  "dialogue_gain_db": round(d_gain, 3),
                  "dialogue_lufs_raw": None if not math.isfinite(Ld) else round(Ld, 3)},
        "segments": dres.segments, "seams": dres.seams, "sfx": sfx_meta, "ambience": amb_meta,
        "music": {**mmeta, **duck_info}, "master": {"mix": minfo, "mix_nomusic": minfo_nm},
        "params": asdict(p), "warnings": warnings,
    }
    from studio.jobs import to_jsonable, write_json_atomic

    rep = write_json_atomic(out / "audio_report.json", to_jsonable(report))
    with contextlib.suppress(Exception):  # tracing is best-effort
        job.trace("stage", stage="render_audio", out_dir=str(out), lufs=minfo.get("integrated_lufs"),
                  true_peak=minfo.get("true_peak_dbtp"), seams=len(dres.seams), warnings=len(warnings))
    return AudioRender(mix=mix_path, mix_nomusic=nm_path, stems=stems, report=rep, sample_rate=sr, num_samples=N,
                       loudness={"mix": {k: minfo.get(k) for k in ("integrated_lufs", "true_peak_dbtp",
                                                                  "max_short_term_lufs")},
                                 "mix_nomusic": {k: minfo_nm.get(k) for k in ("integrated_lufs", "true_peak_dbtp",
                                                                              "max_short_term_lufs")}})
