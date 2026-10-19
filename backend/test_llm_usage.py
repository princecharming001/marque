"""app/llm_usage: request hygiene for 5.x models + real-usage metering.

Each test fails on the pre-metering code: before this module, the wrappers sent
`temperature` to any model (a 400 on Haiku/Sonnet/Opus 5.x), sent no thinking config
(Haiku 5.5 burns a tight max_tokens on thinking before a structured output), and
discarded `usage`, so the script paths — the biggest spend — were unmetered.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app import llm_usage


@pytest.fixture(autouse=True)
def _clean():
    llm_usage.reset_totals()
    llm_usage.set_sink(None)
    yield
    llm_usage.reset_totals()
    llm_usage.set_sink(None)


# --- prepare_body ---------------------------------------------------------------

def test_is_v5_matches_only_the_5_generation():
    assert llm_usage.is_v5("claude-haiku-5-5")
    assert llm_usage.is_v5("claude-sonnet-5-5")
    assert llm_usage.is_v5("claude-opus-5-5")
    assert llm_usage.is_v5("claude-opus-5")
    assert not llm_usage.is_v5("claude-opus-4-8")
    assert not llm_usage.is_v5("claude-haiku-4-5-20251001")
    assert not llm_usage.is_v5("")


def test_prepare_body_strips_sampling_knobs_for_5x():
    body = {"model": "claude-haiku-5-5", "max_tokens": 400, "temperature": 0.0,
            "top_p": 0.9, "top_k": 5, "messages": []}
    out = llm_usage.prepare_body(body)
    assert out is body
    assert "temperature" not in out and "top_p" not in out and "top_k" not in out


def test_prepare_body_keeps_sampling_knobs_for_4x():
    body = {"model": "claude-opus-4-8", "temperature": 1.0, "messages": []}
    llm_usage.prepare_body(body)
    assert body["temperature"] == 1.0
    assert "thinking" not in body


def test_prepare_body_pins_thinking_off_for_haiku_and_between_tools_for_sonnet():
    h = llm_usage.prepare_body({"model": "claude-haiku-5-5", "messages": []})
    assert h["thinking"] == {"type": "disabled"}
    s = llm_usage.prepare_body({"model": "claude-sonnet-5-5", "messages": []})
    assert s["thinking"] == {"type": "between_tools"}
    # Opus 5.5 cannot turn thinking off: leave the API default alone.
    o = llm_usage.prepare_body({"model": "claude-opus-5-5", "messages": []})
    assert "thinking" not in o


def test_prepare_body_respects_an_explicit_thinking_config():
    body = {"model": "claude-haiku-5-5", "thinking": {"type": "adaptive"}, "messages": []}
    llm_usage.prepare_body(body)
    assert body["thinking"] == {"type": "adaptive"}


# --- pricing + usage --------------------------------------------------------------

def test_usage_from_response_reads_every_bucket_and_defaults_to_zero():
    u = llm_usage.usage_from_response({"usage": {"input_tokens": 100, "output_tokens": 7,
                                                 "cache_read_input_tokens": 2000,
                                                 "cache_creation_input_tokens": 50}})
    assert u == {"input": 100, "cache_read": 2000, "cache_write": 50, "output": 7}
    assert llm_usage.usage_from_response({}) == {"input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
    assert llm_usage.usage_from_response({"usage": {"input_tokens": "x"}})["input"] == 0


def test_estimate_cost_uses_cache_rates():
    # Sonnet 5.5: $2 in, $0.20 cache read, $2.50 cache write, $10 out per MTok.
    usd = llm_usage.estimate_cost("claude-sonnet-5-5",
                                  {"input": 1_000_000, "cache_read": 1_000_000,
                                   "cache_write": 1_000_000, "output": 1_000_000})
    assert usd == pytest.approx(2 + 0.2 + 2.5 + 10)
    # Haiku 5.5 is 10x cheaper than Haiku 4.5 on plain input.
    h55 = llm_usage.estimate_cost("claude-haiku-5-5", {"input": 1_000_000})
    h45 = llm_usage.estimate_cost("claude-haiku-4-5-20251001", {"input": 1_000_000})
    assert h55 == pytest.approx(0.10) and h45 == pytest.approx(1.0)
    # Unknown model prices at zero rather than crashing.
    assert llm_usage.estimate_cost("claude-made-up", {"input": 10, "output": 10}) == 0.0


def test_record_response_counts_in_process_and_writes_one_row():
    rows: list[dict] = []

    async def sink(row):
        rows.append(row)

    llm_usage.set_sink(sink)
    payload = {"usage": {"input_tokens": 300, "cache_read_input_tokens": 1200,
                         "cache_creation_input_tokens": 0, "output_tokens": 80},
               "content": [{"type": "text", "text": "{}"}]}

    async def go():
        with llm_usage.route("scripts.write", "creator-1"):
            out = llm_usage.record_response(payload, "claude-haiku-5-5")
        await asyncio.sleep(0)   # let the fire-and-forget write run
        return out

    out = asyncio.run(go())
    assert out["usage"]["cache_read"] == 1200
    assert out["usd"] > 0
    assert rows and rows[0]["operation"] == "scripts.write"
    assert rows[0]["creator_id"] == "creator-1"
    assert rows[0]["model"] == "claude-haiku-5-5"
    assert rows[0]["input_tokens"] == 1500          # uncached + cached, so sums stay meaningful
    assert rows[0]["output_tokens"] == 80
    assert rows[0]["cost_usd"] == out["usd"]
    t = llm_usage.totals()["scripts.write"]
    assert t["calls"] == 1 and t["output"] == 80 and t["usd"] == out["usd"]


def test_record_response_route_override_and_sink_failure_is_swallowed():
    async def bad_sink(row):
        raise RuntimeError("db down")

    llm_usage.set_sink(bad_sink)

    async def go():
        llm_usage.record_response({"usage": {"input_tokens": 1, "output_tokens": 1}},
                                  "claude-opus-4-8", "edl.vision_score", "c2")
        await asyncio.sleep(0)

    asyncio.run(go())
    assert llm_usage.totals()["edl.vision_score"]["calls"] == 1


def test_record_response_outside_a_loop_still_counts():
    llm_usage.record_response({"usage": {"input_tokens": 5, "output_tokens": 5}}, "claude-haiku-5-5")
    assert llm_usage.totals()["unlabeled"]["calls"] == 1


# --- the wrappers actually use it ---------------------------------------------------

class _Resp:
    def __init__(self, payload: dict, status: int = 200):
        self._p, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self._p


class _Client:
    def __init__(self, payload: dict):
        self.payload, self.sent = payload, []

    async def post(self, url, **kw):
        self.sent.append(kw.get("json"))
        return _Resp(self.payload)


def test_main_anthropic_meters_and_sanitizes(monkeypatch):
    import main
    payload = {"content": [{"type": "text", "text": "hi"}],
               "usage": {"input_tokens": 42, "output_tokens": 3}}
    client = _Client(payload)
    monkeypatch.setattr(main, "_get_anthropic_client", lambda: client)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def go():
        with llm_usage.route("test.route", "c9"):
            return await main.anthropic("sys", "usr", "claude-haiku-5-5", 100, temperature=0.0)

    assert asyncio.run(go()) == "hi"
    body = client.sent[0]
    assert "temperature" not in body and body["thinking"] == {"type": "disabled"}
    t = llm_usage.totals()["test.route"]
    assert {k: t[k] for k in ("calls", "input", "cache_read", "cache_write", "output")} == \
        {"calls": 1, "input": 42, "cache_read": 0, "cache_write": 0, "output": 3}
    assert t["usd"] == pytest.approx(42 * 0.1e-6 + 3 * 0.5e-6, abs=1e-6)   # rows round to 6 dp


def test_palo_llm_meters_and_sanitizes(monkeypatch):
    from app import palo_llm
    payload = {"content": [{"type": "text", "text": '{"ok": true}'}],
               "usage": {"input_tokens": 10, "cache_read_input_tokens": 500, "output_tokens": 4}}
    client = _Client(payload)
    monkeypatch.setattr(palo_llm, "_get_client", lambda: client)
    monkeypatch.setattr(palo_llm, "_KEY", "k")

    async def go():
        return await palo_llm.anthropic_cached_json("sys", "usr", {"type": "object"},
                                                    "claude-sonnet-5-5", temperature=1.0)

    assert asyncio.run(go()) == {"ok": True}
    body = client.sent[0]
    assert "temperature" not in body and body["thinking"] == {"type": "between_tools"}
    t = llm_usage.totals()["unlabeled"]
    assert t["cache_read"] == 500 and t["calls"] == 1


def test_haiku_constant_is_the_5_5_model():
    import prompts
    assert prompts.HAIKU == "claude-haiku-5-5"
