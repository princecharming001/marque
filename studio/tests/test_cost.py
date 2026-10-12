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


# ---------------------------------------------------------------------------------------------- ASR accounting
_ASR_MODEL = {"elevenlabs": "scribe_v2", "assemblyai": "universal-3-5-pro"}


def _asr(provider: str, purpose: str, seconds: float, *, model: str | None = None, cached: bool = False,
         status: str = "ok") -> dict:
    """An ``asr`` trace event as :func:`studio.perception.transcribe.transcribe_file` reports it."""
    return {"event": "asr", "status": status, "provider": provider, "purpose": purpose, "audio_seconds": seconds,
            "model": model or _ASR_MODEL.get(provider, provider), "cached": cached}


def test_asr_cost_prices_audio_seconds_by_the_provider_hour() -> None:
    assert C.asr_cost(_asr("elevenlabs", "index", 3600.0)) == pytest.approx(0.22)  # Scribe v2 $0.22/h
    assert C.asr_cost(_asr("assemblyai", "qa", 1800.0)) == pytest.approx(0.075)  # AssemblyAI $0.15/h
    assert C.asr_cost({"event": "asr", "model": "scribe_v2", "audio_seconds": 3600.0}) == pytest.approx(0.22)
    assert C.asr_cost(_asr("elevenlabs", "qa", 45.0, cached=True)) == 0.0  # a re-parse of the raw cache
    assert C.asr_cost(_asr("elevenlabs", "qa", 45.0, status="failed")) is None  # a 5xx: nothing transcribed
    assert C.asr_cost({"event": "asr", "provider": "elevenlabs", "purpose": "qa"}) is None  # no length recorded
    assert C.asr_cost(_asr("whisper", "qa", 45.0, model="large-v3")) is None  # unpriced provider


def test_job_cost_sums_asr_events_under_role_asr_by_purpose() -> None:
    events = [_asr("elevenlabs", "index", 60.0), _asr("elevenlabs", "fill", 3.0), _asr("assemblyai", "qa", 45.0),
              _asr("elevenlabs", "qa", 45.0, cached=True),  # traced, free
              _asr("elevenlabs", "index", 60.0, status="failed"),  # never billed, never counted
              _asr("whisper", "qa", 10.0, model="large-v3")]
    cs = C.job_cost(events)
    scribe, aai = 63.0 / 3600 * 0.22, 45.0 / 3600 * 0.15
    assert cs.calls == 0 and cs.priced_calls == 0
    assert cs.asr_calls == 5 and cs.asr_cached_calls == 1 and cs.asr_seconds == pytest.approx(108.0)
    assert cs.by_role == {"asr": pytest.approx(scribe + aai)} and cs.total_usd == pytest.approx(scribe + aai)
    assert cs.by_stage == pytest.approx({"asr:index": 60.0 / 3600 * 0.22, "asr:fill": 3.0 / 3600 * 0.22, "asr:qa": aai})
    assert cs.by_model == pytest.approx({"scribe_v2": scribe, "universal-3-5-pro": aai})
    assert cs.unpriced_models == ["large-v3"]
    d = cs.to_dict()
    assert d["asr_seconds"] == 108.0 and d["asr_calls"] == 5 and d["asr_cached_calls"] == 1
    assert d["asr_usd"] == round(scribe + aai, 4) and d["by_stage"]["asr:qa"] == 0.0  # 2-decimal dict: see asr_usd
    line = C.format_cost(cs)
    assert line.startswith("$0.01 over 0 calls and 5 ASR calls (asr $0.006 (108 s audio); list prices")
    assert "unpriced: large-v3" in line


def test_asr_rides_along_with_model_calls_in_the_summary() -> None:
    events = [_call("claude-sonnet-5-5", "planner", "plan", 3_500, 2_000),
              _asr("elevenlabs", "index", 60.0), _asr("elevenlabs", "qa", 54.0)]
    cs = C.job_cost(events)
    planner, asr = (3_500 * 2 + 2_000 * 10) / 1e6, 114.0 / 3600 * 0.22
    assert cs.priced_calls == 1 and cs.asr_calls == 2 and cs.total_usd == pytest.approx(planner + asr)
    assert list(cs.by_role) == ["planner", "asr"]  # by spend
    assert C.format_cost(cs).startswith(
        f"${planner + asr:.2f} over 1 calls and 2 ASR calls (planner ${planner:.2f}, asr $0.007 (114 s audio);")
    # sub-cent figures keep their digits instead of rounding a real charge to $0.00
    assert [C.format_usd(v) for v in (0.0, 0.0275, 0.00275, 0.00012)] == ["$0.00", "$0.03", "$0.003", "$0.0001"]
