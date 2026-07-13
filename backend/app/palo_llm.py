"""Cache-aware Anthropic helper for the ported background brains.

main.py's `anthropic()` is the right call for request-path work: it raises
HTTPException so a route degrades to its keyless mock. But the ported jobs (strategy
compile, idea bank, insight cards) run in the background over big, mostly-static
prompts (doctrine + strategy + identity prefix), so they need two things `anthropic()`
doesn't give:

  1. Prompt caching. Ports Palo's `<<<CACHE_BREAKPOINT>>>` splitter — the static prefix
     becomes a cache_control:ephemeral block, cutting repeated-call input cost ~90%.
  2. Never raises. A background job must not 502 a caller; on keyless / exhausted
     retries this returns None so the job skips or writes its deterministic fallback.

Self-contained (own client + env read, no import of main) to avoid a circular import.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random

import httpx

_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages"
_BACKOFF = (0.5, 2.0, 8.0)

CACHE_BREAKPOINT = "<<<CACHE_BREAKPOINT>>>"

_client: httpx.AsyncClient | None = None
_client_loop = None


def _get_client() -> httpx.AsyncClient:
    global _client, _client_loop
    loop = asyncio.get_event_loop()
    if _client is None or _client_loop is not loop:
        _client = httpx.AsyncClient(timeout=120)
        _client_loop = loop
    return _client


def build_system(system: str) -> str | list[dict]:
    """If `system` contains a CACHE_BREAKPOINT marker, split into a cached prefix block
    (cache_control:ephemeral) + a dynamic tail block. Otherwise return the plain string.
    A marker with an empty prefix degrades to a plain string (nothing worth caching)."""
    if CACHE_BREAKPOINT not in system:
        return system
    prefix, _, tail = system.partition(CACHE_BREAKPOINT)
    prefix = prefix.strip()
    if not prefix:
        return tail.strip()
    blocks: list[dict] = [{"type": "text", "text": prefix,
                           "cache_control": {"type": "ephemeral"}}]
    if tail.strip():
        blocks.append({"type": "text", "text": tail.strip()})
    return blocks


async def anthropic_cached(system: str, user: str, model: str, max_tokens: int = 4000,
                           temperature: float | None = None,
                           schema: dict | None = None) -> str | None:
    """Cached, never-raising Anthropic call. Returns the text, or None when the call
    can't be made (no key) or fails after retries — background callers treat None as
    'use the deterministic fallback / skip this run'."""
    if not _KEY:
        return None
    body: dict = {"model": model, "max_tokens": max_tokens,
                  "system": build_system(system),
                  "messages": [{"role": "user", "content": user}]}
    if temperature is not None:
        body["temperature"] = temperature
    if schema is not None:
        body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    last = None
    for attempt, delay in enumerate(list(_BACKOFF) + [None]):
        try:
            r = await _get_client().post(
                _URL, json=body,
                headers={"x-api-key": _KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            if r.status_code == 200:
                try:
                    return "".join(b.get("text", "") for b in r.json().get("content", []))
                except (ValueError, KeyError, TypeError) as e:
                    logging.warning("[palo_llm] malformed 200 body: %s", e)
                    return None
            if r.status_code in (429, 500, 502, 503, 529) and delay is not None:
                last = f"upstream {r.status_code}"
                await asyncio.sleep(delay + delay * 0.2 * (random.random() * 2 - 1))
                continue
            logging.warning("[palo_llm] upstream %d (no retry)", r.status_code)
            return None
        except httpx.HTTPError as e:
            last = str(e)
            if delay is not None:
                await asyncio.sleep(delay + delay * 0.2 * (random.random() * 2 - 1))
                continue
    logging.warning("[palo_llm] failed after retries: %s", last)
    return None


async def anthropic_cached_json(system: str, user: str, schema: dict, model: str,
                                max_tokens: int = 4000, temperature: float | None = None):
    """Structured-output variant — returns parsed JSON (dict/list) or None. Falls back
    to a lenient brace-scan if native structured output produced non-JSON text."""
    raw = await anthropic_cached(system, user, model, max_tokens, temperature, schema=schema)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None
