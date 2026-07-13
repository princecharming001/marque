"""LOOP F — pitch-preservation regression guard (formatting risk #8).

Design-phase investigation of the installed Remotion version found that speed
changes are pitch-preserved server-side (FFmpeg `atempo`, not naive resampling)
— see render/src/components/CutVideo.tsx and app/retention.py's pacing-engine
docstring. This module is the REGRESSION GUARD that keeps that fact true: it
decodes a rendered clip's audio and confirms the KNOWN reference tone (440Hz,
baked into the synthetic source by scripts/make_eval_source.sh) still reads as
440Hz-dominant after a speed change, not shifted to double/half.

Pure Python — a from-scratch Goertzel single-frequency detector (no numpy), so
this needs nothing beyond ffmpeg (already a repo dependency for encoding) and
the standard library.
"""
from __future__ import annotations
import math
import struct
import subprocess

SAMPLE_RATE = 8000   # downsample target — plenty for detecting a ~440/880Hz tone


def _decode_pcm(video_path: str) -> list[float]:
    """Decode the video's audio track to mono 16-bit PCM at SAMPLE_RATE via
    ffmpeg, returned as normalized floats in [-1, 1]. Raises CalledProcessError
    if ffmpeg fails (e.g. no audio track) — callers should catch this."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", video_path, "-f", "s16le",
         "-ar", str(SAMPLE_RATE), "-ac", "1", "-"],
        capture_output=True, check=True,
    )
    n_samples = len(proc.stdout) // 2
    samples = struct.unpack(f"<{n_samples}h", proc.stdout[:n_samples * 2])
    return [s / 32768.0 for s in samples]


def goertzel_energy(samples: list[float], sample_rate: int, target_freq: float) -> float:
    """Single-frequency Goertzel power at `target_freq` — cheaper than a full
    FFT when only a couple of known frequencies matter."""
    n = len(samples)
    if n == 0:
        return 0.0
    k = int(0.5 + (n * target_freq) / sample_rate)
    omega = (2 * math.pi * k) / n
    coeff = 2 * math.cos(omega)
    s_prev = 0.0
    s_prev2 = 0.0
    for sample in samples:
        s = sample + coeff * s_prev - s_prev2
        s_prev2 = s_prev
        s_prev = s
    return s_prev2 * s_prev2 + s_prev * s_prev - coeff * s_prev * s_prev2


def check_pitch_preserved(video_path: str, reference_hz: float = 440.0,
                          chipmunk_hz: float = 880.0, min_ratio: float = 4.0) -> dict:
    """Pass iff the reference tone's energy dominates the chipmunk-shifted
    (2x) frequency's energy by at least `min_ratio` — i.e. the tone reads as
    itself, not pitched up by the speed change. Returns a structured result
    dict rather than raising, so a caller can report WHY without a try/except
    around a bare assert."""
    try:
        samples = _decode_pcm(video_path)
    except subprocess.CalledProcessError as e:
        return {"ok": False, "reason": f"ffmpeg decode failed: {e}"}
    if not samples:
        return {"ok": False, "reason": "no audio samples decoded"}
    ref_energy = goertzel_energy(samples, SAMPLE_RATE, reference_hz)
    shifted_energy = goertzel_energy(samples, SAMPLE_RATE, chipmunk_hz)
    ratio = (ref_energy / shifted_energy) if shifted_energy > 1e-9 else float("inf")
    return {
        "ok": ratio >= min_ratio,
        "reference_hz": reference_hz, "chipmunk_hz": chipmunk_hz,
        "reference_energy": ref_energy, "chipmunk_energy": shifted_energy,
        "ratio": ratio, "min_ratio": min_ratio,
    }
