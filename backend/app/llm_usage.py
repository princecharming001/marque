"""Real token metering + request hygiene for every Anthropic call.

Before this module the script paths (the biggest spenders) were unmetered: the two
wrappers (`main.anthropic`, `app.palo_llm.anthropic_cached`) discarded `usage`, and the
background modules logged hard-coded token guesses. This module is the one place that:

  1. `prepare_body()`  — makes a request body legal for the model it names. The 5.x
     models reject non-default `temperature` / `top_p` / `top_k` with a 400, and run
     adaptive thinking by default, which burns a tight `max_tokens` on thinking before
     a structured output is written (Palo measured zero text at 768 and 2048). So for
     those models we drop sampling knobs and send an explicit thinking config.
  2. `record_response()` — reads the real `usage` block off a 200 response, prices it,
     bumps in-process counters, and writes one `ai_usage` row through whatever sink
     main registered (never raises, never blocks the caller).
  3. `route()` — a contextvar so call sites can label spend ("scripts.write",
     "feed.fast") and attach a creator id without threading arguments through 15k
     lines of main.py.

Self-contained (no import of main) so palo_llm can import it without a cycle.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Awaitable, Callable

# ---------------------------------------------------------------------------
# Prices, $ per 1M tokens: (input, cache_read, cache_write, output).
# List prices as of 2026-10; override with PRICE_<SLUG>_IN/_OUT env where contract
# pricing differs. Cache write = 1.25x input (5-minute TTL), cache read = 0.1x input.
# ---------------------------------------------------------------------------
_LIST: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 0.20, 5.0, 20.0),
    "claude-opus-5": (5.0, 0.50, 6.25, 25.0),
    "claude-opus-4-8": (5.0, 0.50, 6.25, 25.0),
    "claude-opus-4-7": (5.0, 0.50, 6.25, 25.0),
    "claude-opus-4-6": (5.0, 0.50, 6.25, 25.0),
    "claude-sonnet-5-5": (2.0, 0.20, 2.50, 10.0),
    "claude-sonnet-5": (2.0, 0.20, 2.50, 10.0),
    "claude-sonnet-4-6": (3.0, 0.30, 3.75, 15.0),
    "claude-haiku-5-5": (0.10, 0.01, 0.125, 0.50),
    "claude-haiku-4-5": (1.0, 0.10, 1.25, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 0.10, 1.25, 5.0),
}


def _env_price(slug: str, kind: str, default: float) -> float:
    try:
        return float(os.environ.get(f"PRICE_{slug}_{kind}", default))
    except ValueError:
        return default


def prices_for(model: str) -> tuple[float, float, float, float]:
    """(input, cache_read, cache_write, output) $/M for `model`; unknown models price
    at zero so a typo shows up as a $0 row rather than a crash."""
    base = _LIST.get(model)
    if base is None:
        # Dated snapshot ids ("claude-haiku-4-5-20251001") fall back to their family.
        fam = re.sub(r"-\d{8}$", "", model)
        base = _LIST.get(fam, (0.0, 0.0, 0.0, 0.0))
    slug = "OPUS" if "opus" in model else "SONNET" if "sonnet" in model else "HAIKU" if "haiku" in model else "OTHER"
    p_in = _env_price(slug, "IN", base[0])
    p_out = _env_price(slug, "OUT", base[3])
    # Cache rates scale with the (possibly overridden) input rate.
    ratio = p_in / base[0] if base[0] else 1.0
    return (p_in, base[1] * ratio, base[2] * ratio, p_out)


# ---------------------------------------------------------------------------
# Request hygiene for the 5.x models
# ---------------------------------------------------------------------------
_V5_RE = re.compile(r"^claude-(opus|sonnet|haiku|fable|mythos)-5(-\d+)?$")
_SAMPLING_KEYS = ("temperature", "top_p", "top_k")
_warned: set[str] = set()


def is_v5(model: str) -> bool:
    return bool(_V5_RE.match(model or ""))


def thinking_for(model: str) -> dict | None:
    """The thinking config a fixed-format (structured output / tight schema) call should
    send for `model`, or None to leave the API default alone.

    Haiku 5.5: thinking is on by default; `disabled` is accepted at effort <= high
    (its default effort is medium). Sonnet 5.5: `disabled` is a 400, `between_tools`
    is the off-switch at effort <= high (its default). Opus 5.5 can't turn thinking
    off at all; effort is the only lever, so we send nothing and callers size
    max_tokens for it. Pre-5 models don't think unless asked."""
    if model.startswith("claude-haiku-5"):
        return {"type": "disabled"}
    if model.startswith("claude-sonnet-5"):
        return {"type": "between_tools"}
    return None


def prepare_body(body: dict) -> dict:
    """Return `body` made legal for `body["model"]`. Mutates and returns the same dict.
    For 5.x models: strips temperature/top_p/top_k (a 400 otherwise) and adds the
    model's thinking config unless the caller set one. No-op for older models."""
    model = str(body.get("model") or "")
    if not is_v5(model):
        return body
    for k in _SAMPLING_KEYS:
        if k in body:
            body.pop(k, None)
            if model not in _warned:
                _warned.add(model)
                logging.info("[llm_usage] dropped %s for %s (5.x models reject sampling knobs)", k, model)
    if "thinking" not in body:
        th = thinking_for(model)
        if th is not None:
            body["thinking"] = th
    return body


# ---------------------------------------------------------------------------
# Usage accounting
# ---------------------------------------------------------------------------
def usage_from_response(payload: dict) -> dict:
    """Normalize the API's `usage` block. Keys always present, ints."""
    u = (payload or {}).get("usage") or {}

    def _i(k: str) -> int:
        try:
            return int(u.get(k) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "input": _i("input_tokens"),
        "cache_read": _i("cache_read_input_tokens"),
        "cache_write": _i("cache_creation_input_tokens"),
        "output": _i("output_tokens"),
    }


def estimate_cost(model: str, usage: dict) -> float:
    p_in, p_read, p_write, p_out = prices_for(model)
    usd = (usage.get("input", 0) * p_in + usage.get("cache_read", 0) * p_read
           + usage.get("cache_write", 0) * p_write + usage.get("output", 0) * p_out) / 1_000_000
    return round(usd, 6)


# Call-site labels ride a contextvar so wrappers deep in main.py don't need new args.
_route: contextvars.ContextVar[tuple[str, str]] = contextvars.ContextVar("llm_route", default=("unlabeled", ""))


@contextmanager
def route(name: str, creator_id: str = ""):
    """`with llm_usage.route("scripts.write", creator_id): ...` labels every call
    inside (including ones awaited concurrently under the same task tree)."""
    token = _route.set((name, creator_id or ""))
    try:
        yield
    finally:
        _route.reset(token)


def current_route() -> tuple[str, str]:
    return _route.get()


def routed(name: str):
    """Decorator form of `route()` for async functions. The creator id is taken from a
    `creator_id` kwarg, else from the first positional argument's `.creator_id`
    (request models), else from a `sreq`/`req` kwarg. Nested labels win (a judge
    call inside the full script path is billed as the judge)."""
    def deco(fn):
        import functools

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            cid = kwargs.get("creator_id")
            if not cid:
                for cand in (args[0] if args else None, kwargs.get("sreq"), kwargs.get("req")):
                    cid = getattr(cand, "creator_id", None)
                    if cid:
                        break
            with route(name, str(cid or "")):
                return await fn(*args, **kwargs)
        return wrapper
    return deco


# In-process totals: route -> {calls, input, cache_read, cache_write, output, usd}.
# Survive only for the process lifetime; the DB row is the durable record.
_lock = threading.Lock()
_totals: dict[str, dict[str, float]] = {}

Sink = Callable[[dict], Awaitable[Any]]
_sink: Sink | None = None


def set_sink(fn: Sink | None) -> None:
    """main registers the Supabase writer here (PaloStore.record_ai_usage). None keeps
    counting in-process only (keyless / tests)."""
    global _sink
    _sink = fn


def totals() -> dict[str, dict[str, float]]:
    with _lock:
        return {k: dict(v) for k, v in _totals.items()}


def reset_totals() -> None:
    with _lock:
        _totals.clear()


def _bump(name: str, usage: dict, usd: float) -> None:
    with _lock:
        t = _totals.setdefault(name, {"calls": 0, "input": 0, "cache_read": 0,
                                      "cache_write": 0, "output": 0, "usd": 0.0})
        t["calls"] += 1
        for k in ("input", "cache_read", "cache_write", "output"):
            t[k] += usage.get(k, 0)
        t["usd"] = round(t["usd"] + usd, 6)


def record_response(payload: dict, model: str, route_name: str | None = None,
                    creator_id: str | None = None) -> dict:
    """Account one 200 response. Returns {"usage":..., "usd":...}. Never raises; the
    DB write is fire-and-forget on the running loop (or skipped with no loop)."""
    usage = usage_from_response(payload)
    usd = estimate_cost(model, usage)
    name, cid = current_route()
    name = route_name or name
    cid = creator_id if creator_id is not None else cid
    _bump(name, usage, usd)
    if _sink is not None and any(usage.values()):
        row = {
            "creator_id": cid or "",
            "operation": name,
            "model": model,
            # The ai_usage table has two token columns; total input (uncached + cache
            # read + cache write) goes in input_tokens so sums stay meaningful, and
            # cost_usd is priced with the real per-bucket rates above.
            "input_tokens": usage["input"] + usage["cache_read"] + usage["cache_write"],
            "output_tokens": usage["output"],
            "cost_usd": usd,
        }
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            async def _write() -> None:
                try:
                    await _sink(row)  # type: ignore[misc]
                except Exception as e:  # noqa: BLE001
                    logging.warning("[llm_usage] ai_usage write failed: %s", e)
            try:
                loop.create_task(_write())
            except RuntimeError:
                pass
    return {"usage": usage, "usd": usd}
