"""The one Anthropic call helper scripts v2 uses.

Neither existing wrapper fits: `main.anthropic()` raises and sends one user turn;
`palo_llm.anthropic_cached_json` sends one user turn and returns no usage. v2 needs:

  - multi-turn messages (the creator's real transcripts ride as earlier ASSISTANT turns,
    the strongest voice lever in the research; prefill of the LAST turn is a 400 on 5.x
    models, earlier assistant turns are fine);
  - cache_control on the stable prefix (doctrine system block; last exemplar turn);
  - output_config.effort + structured output;
  - the model's thinking config / sampling hygiene (app.llm_usage.prepare_body);
  - real usage back, metered to ai_usage (app.llm_usage.record_response);
  - never raising: (None, {}) on keyless / refusal / exhausted retries, so every caller
    degrades to v1 or a deterministic fallback.

It reuses palo_llm's pooled client and key so there is one connection pool.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import httpx

from app import llm_usage, palo_llm

_BACKOFF = (0.5, 2.0, 6.0)
# Sonnet 5.5 refusals: route to the platform's default fallback model instead of
# returning nothing (Claude API only; the header is ignored by older models).
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def system_block(text: str, *, cache: bool = True) -> list[dict]:
    blk: dict = {"type": "text", "text": text}
    if cache:
        blk["cache_control"] = {"type": "ephemeral"}
    return [blk]


def text_turn(role: str, text: str, *, cache: bool = False) -> dict:
    blk: dict = {"type": "text", "text": text}
    if cache:
        blk["cache_control"] = {"type": "ephemeral"}
    return {"role": role, "content": [blk]}


def _parse_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


async def call(*, model: str, system: str | list[dict], messages: list[dict],
               schema: dict | None = None, max_tokens: int = 1500,
               effort: str | None = None, route: str | None = None,
               timeout: float | None = None) -> tuple[Any, dict]:
    """One call. Returns (parsed JSON when `schema` else text, usage-dict) or (None, {})."""
    if not palo_llm._KEY:
        return None, {}
    body: dict = {"model": model, "max_tokens": max_tokens,
                  "system": system_block(system) if isinstance(system, str) else system,
                  "messages": messages}
    oc: dict = {}
    if schema is not None:
        oc["format"] = {"type": "json_schema", "schema": schema}
    if effort and llm_usage.is_v5(model):
        oc["effort"] = effort
    if oc:
        body["output_config"] = oc
    llm_usage.prepare_body(body)
    headers = {"x-api-key": palo_llm._KEY, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    if model.startswith("claude-sonnet-5-5"):
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = _FALLBACK_BETA

    async def _once() -> tuple[Any, dict] | None:
        r = await palo_llm._get_client().post(palo_llm._URL, json=body, headers=headers)
        if r.status_code == 200:
            try:
                payload = r.json()
            except ValueError:
                return None, {}
            acct = llm_usage.record_response(payload, str(payload.get("model") or model), route)
            stop = payload.get("stop_reason")
            if stop == "refusal":
                logging.info("[v2.llm] refusal from %s (%s)", model,
                             (payload.get("stop_details") or {}).get("category"))
                return None, acct["usage"]
            if stop == "max_tokens":
                logging.warning("[v2.llm] %s hit max_tokens=%d", model, max_tokens)
            text = "".join(b.get("text", "") for b in payload.get("content", [])
                           if isinstance(b, dict) and b.get("type", "text") == "text")
            return (_parse_json(text) if schema is not None else text), acct["usage"]
        if r.status_code in (429, 500, 502, 503, 529):
            return None
        logging.warning("[v2.llm] %s -> %d: %s", model, r.status_code, r.text[:300])
        return None, {}

    for delay in list(_BACKOFF) + [None]:
        try:
            coro = _once()
            res = await (asyncio.wait_for(coro, timeout) if timeout else coro)
        except asyncio.TimeoutError:
            logging.info("[v2.llm] %s timed out after %.1fs", model, timeout or 0)
            return None, {}
        except httpx.HTTPError as e:
            logging.info("[v2.llm] transport error %s", e)
            res = None
        if res is not None:
            return res
        if delay is None:
            break
        await asyncio.sleep(delay + delay * 0.2 * (random.random() * 2 - 1))
    return None, {}
