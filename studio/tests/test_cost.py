"""Model-spend estimates from trace events (studio.agent.cost)."""

from __future__ import annotations

import pytest

from studio.agent import cost as C


def _call(model: str, role: str, stage: str, inp: int, out: int, cr: int = 0, cw: int = 0) -> dict:
    return {"event": "model_call", "role": role, "stage": stage, "model": model, "input_tokens": inp,
            "output_tokens": out, "cache_read_tokens": cr, "cache_write_tokens": cw}


def test_call_cost_prices_each_token_class_at_the_cache_ttl() -> None:
    e = _call("claude-fable-5-1", "director", "brief", inp=100_000, out=10_000, cr=60_000, cw=30_000)
    # 10k uncached at $10, 10k out at $50, 60k reads at $0.25, 30k 1h-writes at 2 x $10
    assert C.call_cost(e, ttl="1h") == pytest.approx((10_000 * 10 + 10_000 * 50 + 60_000 * 0.25 + 30_000 * 20) / 1e6)
    assert C.call_cost(e, ttl="5m") == pytest.approx((10_000 * 10 + 10_000 * 50 + 60_000 * 0.25 + 30_000 * 12.5) / 1e6)
    assert C.call_cost(_call("claude-opus-5-5-20260101", "critic", "critique", 1000, 100)) == pytest.approx(
        (1000 * 4 + 100 * 20) / 1e6)  # a dated snapshot prices as its base model
    assert C.call_cost(_call("gemini-3.1-pro", "critic", "watch", 1000, 100)) is None
    assert C.call_cost(_call("claude-opus-5-5", "director", "brief", 0, 0)) is None  # a failed call carried no usage


def test_job_cost_sums_by_role_stage_and_model() -> None:
    events = [{"event": "tool_call", "name": "get_overview"},
              _call("claude-fable-5-1", "director", "brief", 10_000, 1_000),
              _call("claude-fable-5-1", "director", "revise:r1", 10_000, 1_000, cr=8_000),
              _call("claude-opus-5-5", "critic", "critique:r1:rubric", 5_000, 2_000),
              _call("gemini-3.1-pro", "watcher", "watch:r1", 5_000, 2_000)]
    cs = C.job_cost(events)
    assert cs.calls == 4 and cs.priced_calls == 3 and cs.unpriced_models == ["gemini-3.1-pro"]
    director = (10_000 * 10 + 1_000 * 50) / 1e6 + (2_000 * 10 + 8_000 * 0.25 + 1_000 * 50) / 1e6
    critic = (5_000 * 4 + 2_000 * 20) / 1e6
    assert cs.by_role == pytest.approx({"director": director, "critic": critic})
    assert set(cs.by_stage) == {"director:brief", "director:revise", "critic:critique"}
    assert cs.total_usd == pytest.approx(director + critic)
    assert cs.tokens == {"uncached_input": 17_000, "output": 4_000, "cache_read": 8_000, "cache_write": 0}
    line = C.format_cost(cs)
    assert line.startswith(f"${cs.total_usd:.2f} over 3 calls") and "unpriced: gemini-3.1-pro" in line
    assert cs.to_dict()["cache_ttl"] == "1h"
    assert C.format_cost(C.job_cost([])) == "no priced model calls"


def test_long_prompt_tier_prices_haiku_above_100k() -> None:
    small = C.call_cost(_call("claude-haiku-5-5", "planner", "plan", 90_000, 1_000))
    big = C.call_cost(_call("claude-haiku-5-5", "planner", "plan", 110_000, 1_000))
    assert small == pytest.approx((90_000 * 0.10 + 1_000 * 0.50) / 1e6)
    assert big == pytest.approx((110_000 * 0.50 + 1_000 * 2.50) / 1e6)
