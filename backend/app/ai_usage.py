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

# $ per 1M tokens (input, output). Defaults are approximate list prices; override via
# env to match real contract pricing without a code change.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (float(os.environ.get("PRICE_OPUS_IN", "15")),
                        float(os.environ.get("PRICE_OPUS_OUT", "75"))),
    "claude-sonnet-4-6": (float(os.environ.get("PRICE_SONNET_IN", "3")),
                          float(os.environ.get("PRICE_SONNET_OUT", "15"))),
    "claude-haiku-4-5-20251001": (float(os.environ.get("PRICE_HAIKU_IN", "1")),
                                  float(os.environ.get("PRICE_HAIKU_OUT", "5"))),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = _PRICES.get(model, (0.0, 0.0))
    return round((input_tokens * p_in + output_tokens * p_out) / 1_000_000, 6)


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
