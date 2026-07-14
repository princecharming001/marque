"""Prompt override store — Palo's `get_darkly_prompt_with_fallback`, minus LaunchDarkly.

Marque keeps prompts in git (prompts.py builders). Palo iterates prompts live via
LaunchDarkly flags. This bridges the two the way the owner chose: the in-code prompt
is always the fallback/default, and an optional Supabase `prompt_overrides` row (key,
prompt_text) wins when present — so the 3-4 hottest prompts (write, strategy synthesis,
insight discovery, spitfire generator) can be tuned in prod without a redeploy, while
everything stays green with zero DB configured.

Never raises; an outage or missing table degrades to the code fallback. A 60s TTL
cache (incl. negative caching) keeps per-request cost ~zero even when overrides exist.
"""
from __future__ import annotations

import time

_TTL_S = 60.0
# key -> (text_or_empty, expires_at). Empty string = "no override" (negative cache).
_CACHE: dict[str, tuple[str, float]] = {}


def _fresh(key: str) -> tuple[bool, str]:
    hit = _CACHE.get(key)
    if hit and hit[1] > time.monotonic():
        return True, hit[0]
    return False, ""


async def get_prompt(key: str, fallback: str, store=None) -> str:
    """Resolve a prompt: Supabase override (if any, cached) else the code fallback.

    `store` is a PaloStore or None. With no store this is a pure return of `fallback`
    at zero cost, so keyless dev and flag-off never touch the DB."""
    if store is None:
        return fallback
    ok, cached = _fresh(key)
    if ok:
        return cached or fallback
    try:
        override = await store.load_prompt_override(key)
    except Exception:
        override = None
    text = override if isinstance(override, str) and override.strip() else ""
    _CACHE[key] = (text, time.monotonic() + _TTL_S)
    return text or fallback


def clear_cache() -> None:
    """Test hook — reset the module cache between cases."""
    _CACHE.clear()
