"""AI cost accounting + the compile kill-switch — ported discipline from Palo.

Palo controls a per-channel Opus bill (~$1.60-2.40/strategy compile) with three gates:
an allowlist kill-switch, a paying gate, and per-stage ai_usage rows. Marque has no
per-creator cost accounting today, so the port plan lands this in Phase 0 BEFORE any
feature that spends Opus — never as an afterthought (risk #5).

  - estimate_cost(): $/call from token counts at current model prices (env-overridable).
  - record():        fire-and-forget ai_usage row (never raises into the hot path).
  - compile_allowed(): the allowlist gate. STRATEGY_ALLOWLIST defaults EMPTY, so the
    expensive weekly compile is OFF for everyone until a creator is explicitly opted in
    — the safe default that stops a fleet-wide Opus bill on day one.
"""
from __future__ import annotations

import logging
import os

from app import llm_usage


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """$ for a call priced at plain input/output rates. The price table (and the
    PRICE_<MODEL>_IN/OUT env overrides) live in app/llm_usage.py, which is also where
    the wrappers meter REAL usage; this helper remains for the background modules that
    still log an estimate before the call returns."""
    return llm_usage.estimate_cost(model, {"input": input_tokens, "output": output_tokens})


async def record(store, creator_id: str, operation: str, model: str,
                 input_tokens: int, output_tokens: int) -> float:
    """Write one ai_usage row and return the estimated cost. Best-effort: a store
    outage (or no store) just skips the row, never blocks the caller."""
    cost = estimate_cost(model, input_tokens, output_tokens)
    if store is not None:
        try:
            await store.record_ai_usage({
                "creator_id": creator_id, "operation": operation, "model": model,
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "cost_usd": cost,
            })
        except Exception as e:
            logging.warning("[ai_usage] record failed (%s); continuing", e)
    return cost


def _allowlist() -> set[str]:
    raw = os.environ.get("STRATEGY_ALLOWLIST", "").strip()
    if raw == "*":
        return {"*"}
    return {c.strip() for c in raw.split(",") if c.strip()}


def compile_allowed(creator_id: str, is_paying: bool = True) -> bool:
    """The strategy-compile gate: allowlisted AND paying. Empty allowlist => nobody
    (compiles OFF), '*' => everyone paying. Keeps the Opus bill opt-in by default."""
    if not is_paying:
        return False
    allow = _allowlist()
    return bool(allow) and ("*" in allow or creator_id in allow)
