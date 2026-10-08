"""Estimated model spend of a job, from ``trace.jsonl`` ``model_call`` events and provider list prices.

Every model call the engine makes is traced with its token usage (input, output, cache read, cache write). This
module prices those at Anthropic's public list prices (USD per million tokens, as of :data:`PRICES_AS_OF`), with cache
writes at the engine's cache TTL (:data:`studio.agent.providers.CACHE_TTL`: 1h writes cost 2x the input price, 5m
writes 1.25x). Other providers' models are counted but reported as unpriced. The figure is an estimate for the
report and the CLI summary, not a bill: check the provider console for the invoice.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from studio.agent.providers import CACHE_TTL

if TYPE_CHECKING:  # pragma: no cover
    from studio.jobs import Job

__all__ = ["PRICES", "PRICES_AS_OF", "CostSummary", "call_cost", "job_cost", "format_cost"]

PRICES_AS_OF = "2026-10-06"

#: USD per million tokens: (input, output, cache read). Cache writes derive from the input price by TTL.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 0.50),
    "claude-opus-4-6": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}

_WRITE_MULTIPLIER = {"5m": 1.25, "1h": 2.0}


def _price(model: str | None) -> tuple[float, float, float] | None:
    if not model:
        return None
    if model in PRICES:
        return PRICES[model]
    base = model.rsplit("-", 1)[0]  # a dated snapshot id ("claude-opus-4-6-20260101")
    return PRICES.get(base)


def call_cost(event: dict[str, Any], *, ttl: str = CACHE_TTL) -> float | None:
    """USD for one ``model_call`` trace event, or None when its model is unpriced or the call carried no usage."""
    p = _price(str(event.get("model_resolved") or event.get("model") or ""))
    if p is None:
        return None
    try:
        inp = int(event.get("input_tokens") or 0)
        out = int(event.get("output_tokens") or 0)
        cr = int(event.get("cache_read_tokens") or 0)
        cw = int(event.get("cache_write_tokens") or 0)
    except (TypeError, ValueError):
        return None
    if not (inp or out):
        return None
    uncached = max(0, inp - cr - cw)
    write = p[0] * _WRITE_MULTIPLIER.get(ttl, 2.0)
    return (uncached * p[0] + out * p[1] + cr * p[2] + cw * write) / 1e6


@dataclass
class CostSummary:
    total_usd: float = 0.0
    calls: int = 0
    priced_calls: int = 0
    by_role: dict[str, float] = field(default_factory=dict)
    by_stage: dict[str, float] = field(default_factory=dict)  # "role:stage"
    by_model: dict[str, float] = field(default_factory=dict)
    unpriced_models: list[str] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=dict)  # uncached_input, output, cache_read, cache_write

    def to_dict(self) -> dict[str, Any]:
        return {"total_usd": round(self.total_usd, 2), "calls": self.calls, "priced_calls": self.priced_calls,
                "by_role": {k: round(v, 2) for k, v in self.by_role.items()},
                "by_stage": {k: round(v, 2) for k, v in self.by_stage.items()},
                "by_model": {k: round(v, 2) for k, v in self.by_model.items()},
                "unpriced_models": list(self.unpriced_models), "tokens": dict(self.tokens),
                "prices_as_of": PRICES_AS_OF, "cache_ttl": CACHE_TTL}


def job_cost(job: Job | list[dict[str, Any]], *, ttl: str = CACHE_TTL) -> CostSummary:
    """Sum :func:`call_cost` over a job's trace (or a list of trace events)."""
    events = job if isinstance(job, list) else job.read_trace()
    cs = CostSummary()
    role: dict[str, float] = defaultdict(float)
    stage: dict[str, float] = defaultdict(float)
    model: dict[str, float] = defaultdict(float)
    tokens: dict[str, int] = defaultdict(int)
    unpriced: set[str] = set()
    for e in events:
        if e.get("event") != "model_call":
            continue
        cs.calls += 1
        m = str(e.get("model_resolved") or e.get("model") or "?")
        c = call_cost(e, ttl=ttl)
        if c is None:
            if _price(m) is None:
                unpriced.add(m)
            continue
        cs.priced_calls += 1
        cs.total_usd += c
        role[str(e.get("role") or "?")] += c
        stage[f"{e.get('role') or '?'}:{str(e.get('stage') or '?').split(':')[0]}"] += c
        model[m] += c
        inp, cr, cw = (int(e.get(k) or 0) for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens"))
        tokens["uncached_input"] += max(0, inp - cr - cw)
        tokens["output"] += int(e.get("output_tokens") or 0)
        tokens["cache_read"] += cr
        tokens["cache_write"] += cw
    cs.by_role = dict(sorted(role.items(), key=lambda kv: -kv[1]))
    cs.by_stage = dict(sorted(stage.items(), key=lambda kv: -kv[1]))
    cs.by_model = dict(sorted(model.items(), key=lambda kv: -kv[1]))
    cs.unpriced_models = sorted(unpriced)
    cs.tokens = dict(tokens)
    return cs


def format_cost(cs: CostSummary) -> str:
    """One line for the CLI summary."""
    if not cs.priced_calls:
        return "no priced model calls"
    roles = ", ".join(f"{r} ${v:.2f}" for r, v in cs.by_role.items())
    note = f"; unpriced: {', '.join(cs.unpriced_models)}" if cs.unpriced_models else ""
    return f"${cs.total_usd:.2f} over {cs.priced_calls} calls ({roles}; list prices {PRICES_AS_OF}{note})"
