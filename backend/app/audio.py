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
                        target_lufs: float = DEFAULT_TARGET_LUFS) -> list[str] | None:
    """ffmpeg argv for loudnorm APPLY pass 2 — video stream-copied (duration
    stays byte-identical, so a caller can ffprobe-verify before adopting the
    output), audio re-encoded through loudnorm seeded with pass 1's measured
    stats. Returns None if `measured` is missing a required key (caller keeps
    the un-normalized source in that case)."""
    required = ("input_i", "input_tp", "input_lra", "input_thresh")
    if not all(k in measured for k in required):
        return None
    offset = measured.get("target_offset", 0.0)
    filt = (f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11:"
            f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={offset}:linear=true")
    return ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", url,
            "-c:v", "copy", "-af", filt, out_path]


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
