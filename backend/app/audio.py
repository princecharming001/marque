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
