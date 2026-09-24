"""Bounded subprocess I/O for the ffmpeg/ffprobe probes (LV-20, editor audit 2026-09-24).

`asyncio.wait_for(proc.communicate(), timeout)` only stops WAITING — on a timeout (or when
the awaiting task is cancelled, e.g. by an outer wait_for) the child keeps running. On the
512 MiB / 0.5 CPU prod instance an abandoned ffmpeg that is still decoding a long take
competes with the live pipeline for the whole box. `communicate_or_kill` is the one shape
every ffmpeg/ffprobe call site uses instead: on ANY exit other than a normal return it
SIGKILLs the child and reaps it before re-raising, so a timed-out probe never outlives
its caller.
"""
from __future__ import annotations

import asyncio


async def kill_and_reap(proc, grace_s: float = 5.0) -> None:
    """SIGKILL `proc` (if still running) and wait for it to exit. Never raises for an
    already-exited process; tolerant of minimal test doubles (kill/wait may be absent)."""
    if proc is None:
        return
    kill = getattr(proc, "kill", None)
    if kill is not None:
        try:
            kill()
        except ProcessLookupError:
            pass                                # already exited
        except Exception:
            pass
    wait = getattr(proc, "wait", None)
    if wait is not None:
        try:
            await asyncio.wait_for(wait(), timeout=grace_s)
        except Exception:
            pass                                # SIGKILL can't be ignored; exit is imminent


async def communicate_or_kill(proc, timeout_s: float, input: bytes | None = None):
    """`proc.communicate()` bounded by `timeout_s`. On timeout (asyncio.TimeoutError) or
    cancellation the child is killed and reaped, then the exception propagates unchanged —
    callers keep their existing fail-soft `except` handling."""
    try:
        coro = proc.communicate() if input is None else proc.communicate(input)
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except BaseException:
        await kill_and_reap(proc)
        raise
