"""Loudness measurement for the render pipeline (P0.6).

`probe_loudness` shells out to ffmpeg's `loudnorm` filter in analysis mode to read a
take's integrated loudness (LUFS); `gain_db` turns that into the dB gain needed to hit
the platform target. Everything FAILS SOFT — no ffmpeg binary, an unfetchable URL, a
timeout, or unparseable output all return None → the render applies no gain (the exact
prior behavior). This keeps the keyless CI contract: no vendor/env keys required, and a
box without ffmpeg degrades to a no-op instead of erroring.
"""
from __future__ import annotations
import asyncio
import json
import shutil

DEFAULT_TARGET_LUFS = -14.0     # TikTok/YouTube published loudness target
DEFAULT_CLAMP_DB = 12.0         # never boost/cut more than this (avoids pumping/clipping)


async def probe_loudness(url: str, timeout_s: float = 60.0) -> float | None:
    """Integrated loudness (LUFS) of the audio at `url`, or None if unmeasurable.

    Uses `ffmpeg -af loudnorm=...:print_format=json -f null -`, which prints a JSON
    block (with `input_i` = measured integrated LUFS) to stderr and decodes no output.
    """
    if not url or shutil.which("ffmpeg") is None:
        return None
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", url,
        "-af", f"loudnorm=I={DEFAULT_TARGET_LUFS}:print_format=json",
        "-f", "null", "-",
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except Exception:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        return None
    return _parse_input_i(stderr.decode("utf-8", "ignore"))


def _parse_input_i(text: str) -> float | None:
    """Pull `input_i` out of loudnorm's trailing JSON block on stderr."""
    start, end = text.rfind("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        val = float(json.loads(text[start:end + 1])["input_i"])
    except (ValueError, KeyError, TypeError):
        return None
    # loudnorm reports -inf (as a large negative or the literal string) for silence — ignore.
    return val if val > -70.0 else None


async def detect_silence_spans(url: str, noise_db: float = -30.0,
                               min_silence_s: float = 0.12,
                               timeout_s: float = 60.0) -> list[tuple[int, int]] | None:
    """Verified-silent spans (start_ms, end_ms) in the audio at `url`, or None if
    unmeasurable. Uses ffmpeg's `silencedetect` filter, which logs `silence_start:` and
    `silence_end:` (seconds) to stderr for every run of audio quieter than `noise_db`
    lasting at least `min_silence_s`.

    The editor uses this to tell a REAL pause (safe to tighten) apart from a gap where
    the transcriber simply DROPPED a word — that gap still carries speech energy, so it
    will NOT appear as a silent span and the dead-air trim skips it (protecting the word).
    Fails soft to None (no ffmpeg / unfetchable / timeout) → caller keeps prior behavior.
    """
    if not url or shutil.which("ffmpeg") is None:
        return None
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", url,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
        "-f", "null", "-",
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except Exception:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        return None
    return _parse_silence_spans(stderr.decode("utf-8", "ignore"))


def _parse_silence_spans(text: str) -> list[tuple[int, int]]:
    """Pair `silence_start:`/`silence_end:` lines from silencedetect stderr into
    (start_ms, end_ms) tuples. Tolerates an unterminated final span (drops it)."""
    import re
    spans: list[tuple[int, int]] = []
    cur: float | None = None
    for m in re.finditer(r"silence_(start|end):\s*(-?\d+(?:\.\d+)?)", text):
        kind, val = m.group(1), float(m.group(2))
        if kind == "start":
            cur = val
        elif kind == "end" and cur is not None:
            if val > cur:
                spans.append((int(cur * 1000), int(val * 1000)))
            cur = None
    return spans


def gain_db(integrated_lufs: float | None,
            target_lufs: float = DEFAULT_TARGET_LUFS,
            clamp_db: float = DEFAULT_CLAMP_DB) -> float:
    """dB gain to bring `integrated_lufs` up/down to `target_lufs`, clamped to ±clamp_db.
    Returns 0.0 when the loudness is unknown (no normalization applied)."""
    if integrated_lufs is None:
        return 0.0
    return round(max(-clamp_db, min(clamp_db, target_lufs - integrated_lufs)), 2)


# ---------------------------------------------------------------------------
# A5b (superintelligence epic) — true 2-pass loudness normalization on the
# FINAL rendered mp4 (AUDIO_FINALIZE=1). Pass 1 measures; pass 2 applies using
# the measured stats (single-pass-equivalent accuracy per ffmpeg's own
# loudnorm docs). Every function here is a PURE argv/parse helper — no
# subprocess execution — so they're keyless-testable; the caller (main.py)
# owns running ffmpeg, timeouts, and fail-soft (any error -> keep the
# un-normalized Lambda URL, never fail the job over this).
# ---------------------------------------------------------------------------

def loudnorm_pass1_args(url: str, target_lufs: float = DEFAULT_TARGET_LUFS) -> list[str]:
    """ffmpeg argv for loudnorm ANALYSIS pass 1 — measures the take's actual
    loudness stats (input_i/input_tp/input_lra/input_thresh), printed as a
    JSON block on stderr (parse with `parse_loudnorm_json`)."""
    return ["ffmpeg", "-hide_banner", "-nostats", "-i", url, "-af",
            f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11:print_format=json", "-f", "null", "-"]


def parse_loudnorm_json(stderr_text: str) -> dict | None:
    """Pull the trailing loudnorm JSON measurement block out of ffmpeg stderr.
    Returns None if the block is missing/malformed (caller falls back to
    skipping normalization, same fail-soft contract as `probe_loudness`)."""
    start, end = stderr_text.rfind("{"), stderr_text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(stderr_text[start:end + 1])
    except (ValueError, TypeError):
        return None


def loudnorm_pass2_args(url: str, measured: dict, out_path: str,
                        target_lufs: float = DEFAULT_TARGET_LUFS,
                        polish: bool = False) -> list[str] | None:
    """ffmpeg argv for loudnorm APPLY pass 2 — video stream-copied (duration
    stays byte-identical, so a caller can ffprobe-verify before adopting the
    output), audio re-encoded through loudnorm seeded with pass 1's measured
    stats. Returns None if `measured` is missing a required key (caller keeps
    the un-normalized source in that case).

    `polish` (WS1, build 49): prepend the voice-polish chain (HPF/EQ/de-esser/
    compressor/limiter — the previously ORPHANED `_VOICE_POLISH_FILTER`, built in
    A5c with zero callers) BEFORE loudnorm, so polish + normalization land in the
    ONE audio re-encode this step already pays for. loudnorm last keeps the final
    integrated loudness on target regardless of what the polish chain did to
    levels. Note the loudnorm measurement (pass 1) is taken on the UN-polished
    audio; the compressor/limiter changes dynamics slightly, so linear mode may
    fall back to dynamic — acceptable for speech, and the caller's duration guard
    still applies."""
    required = ("input_i", "input_tp", "input_lra", "input_thresh")
    if not all(k in measured for k in required):
        return None
    offset = measured.get("target_offset", 0.0)
    filt = (f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11:"
            f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={offset}:linear=true")
    if polish:
        filt = f"{_VOICE_POLISH_FILTER},{filt}"
    return ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", url,
            "-c:v", "copy", "-af", filt, out_path]


# ---------------------------------------------------------------------------
# WS1 (build 49) — cheap reference-free speech-quality gate. Enhancement models
# (DeepFilterNet-class) audibly smear ALREADY-CLEAN audio, so they must only run
# on takes that measurably need them. Rather than shipping torch/SQUIM to the
# server (a ~800MB dependency), the gate is an ffmpeg `astats` SNR proxy:
# "RMS level" ≈ programme level, "RMS trough" ≈ the noise floor between words —
# their difference approximates SNR for continuous speech over steady noise.
# Below ~25dB the take is noticeably noisy (phone-in-kitchen class) and worth a
# denoise pass; clean voice memos measure 35-50dB. Pure argv/parse helpers —
# the caller (main.py) runs ffmpeg and owns thresholds/fail-soft.
# ---------------------------------------------------------------------------

SNR_ENHANCE_THRESHOLD_DB = 25.0

def snr_probe_args(url: str) -> list[str]:
    """ffmpeg argv that prints astats measurements (incl. RMS level/trough) to stderr."""
    return ["ffmpeg", "-hide_banner", "-nostats", "-i", url,
            "-af", "astats=measure_perchannel=none", "-f", "null", "-"]


def parse_astats_snr(stderr_text: str) -> dict | None:
    """Parse Overall 'RMS level' + 'RMS trough' (dB) from astats stderr output →
    {"rms_db", "noise_floor_db", "snr_db"}. None when either stat is missing or
    non-finite (silent take / parse miss) — callers must then SKIP enhancement
    (fail-closed: never enhance what can't be measured)."""
    rms = trough = None
    for line in stderr_text.splitlines():
        line = line.strip()
        if "RMS level dB:" in line:
            rms = _parse_db(line.rsplit(":", 1)[-1])
        elif "RMS trough dB:" in line:
            trough = _parse_db(line.rsplit(":", 1)[-1])
    if rms is None or trough is None:
        return None
    return {"rms_db": rms, "noise_floor_db": trough, "snr_db": rms - trough}


def _parse_db(token: str) -> float | None:
    token = token.strip()
    if token in ("-inf", "inf", "nan", ""):
        return None
    try:
        return float(token)
    except ValueError:
        return None


def remux_enhanced_audio_args(video_url: str, enhanced_audio_path: str, out_path: str) -> list[str]:
    """ffmpeg argv to marry the ORIGINAL video stream (copied, untouched) with an
    enhanced audio file. -shortest guards against a denoiser that returned a
    slightly longer tail; the caller still ffprobe-verifies duration before adopting."""
    return ["ffmpeg", "-hide_banner", "-nostats", "-y",
            "-i", video_url, "-i", enhanced_audio_path,
            "-map", "0:v", "-map", "1:a", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k", "-shortest", out_path]


def extract_audio_args(video_url: str, out_wav_path: str) -> list[str]:
    """ffmpeg argv to pull the audio track as 48k mono wav (what DeepFilterNet-class
    enhancers expect)."""
    return ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", video_url,
            "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", out_wav_path]


# ---------------------------------------------------------------------------
# A5c (superintelligence epic) — voice polish chain (VOICE_POLISH=1), run at
# ANALYSIS time on the SOURCE take (never the transcription copy — timestamps
# must not shift). Rumble cut, a mud scoop + presence lift, de-essing,
# consistency compression, and a brickwall limiter. Video stream-copied so
# duration is identical; caller ffprobe-verifies before adopting (main.py) and
# discards on any mismatch.
# ---------------------------------------------------------------------------

_VOICE_POLISH_FILTER = (
    "highpass=f=90,"
    "equalizer=f=450:t=q:w=1.2:g=-2.5,"
    "equalizer=f=3200:t=q:w=1.0:g=2.5,"
    "deesser,"
    "acompressor=ratio=4:threshold=-18dB:attack=8:release=120,"
    "alimiter=limit=-1.5dB"
)


def voice_polish_args(url: str, out_path: str) -> list[str]:
    """ffmpeg argv for the voice-polish chain — see module doc above for the
    filter rationale. Pure argv builder; the caller runs ffmpeg and validates
    the output (duration match) before adopting it as the render source."""
    return ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", url,
            "-c:v", "copy", "-af", _VOICE_POLISH_FILTER, out_path]
