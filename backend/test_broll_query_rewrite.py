"""Part 5.2: cultural b-roll query rewrite (_culturalize_broll_queries) — keyless fail-soft,
one-call batching, cache, meme shaping, tweak-skip."""
from __future__ import annotations

import asyncio

import main


def _run(coro):
    return asyncio.run(coro)


def _edl():
    return {"broll": [
        {"broll_query": "person working", "cue_text": "grinding", "need": "concept"},
        {"broll_query": "surprised", "cue_text": "no way", "need": "meme"},
    ]}


def test_rewrite_keyless_passthrough(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    edl = _edl()
    out = _run(main._culturalize_broll_queries(edl, {"brand": {"niche": "startup"}}))
    assert out["broll"][0]["broll_query"] == "person working"   # untouched, no key


def test_rewrite_batches_one_call_and_applies(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    main._broll_query_cache.clear()
    calls = {"n": 0}

    async def fake_json(system, user, schema, model=None, temperature=None):
        calls["n"] += 1
        return {"queries": [
            {"i": 0, "query": "founder typing on a macbook in a dark neon office"},
            {"i": 1, "query": "mind blown"},
        ]}
    monkeypatch.setattr(main, "anthropic_json", fake_json)

    edl = _edl()
    out = _run(main._culturalize_broll_queries(edl, {"brand": {"niche": "startup"}}))
    assert calls["n"] == 1                                       # ONE call for all cues
    assert out["broll"][0]["broll_query"].startswith("founder typing")
    assert out["broll"][1]["broll_query"] == "mind blown"        # meme shaped to a canonical term
    assert out["_queries_rewritten"] is True


def test_rewrite_cache_hit_skips_call(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    main._broll_query_cache.clear()
    main._broll_query_cache["startup::person working"] = {"query": "founder at a laptop", "need": "action"}
    main._broll_query_cache["startup::surprised"] = {"query": "shocked pikachu", "need": "concept"}
    calls = {"n": 0}

    async def fake_json(system, user, schema, model=None, temperature=None):
        calls["n"] += 1
        return {"queries": []}
    monkeypatch.setattr(main, "anthropic_json", fake_json)

    out = _run(main._culturalize_broll_queries(_edl(), {"brand": {"niche": "startup"}}))
    assert calls["n"] == 0                                       # fully cached → no LLM call
    assert out["broll"][0]["broll_query"] == "founder at a laptop"


def test_rewrite_skipped_on_tweak_rerender(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")

    async def boom(*a, **k):
        raise AssertionError("must not call the LLM once queries are already rewritten")
    monkeypatch.setattr(main, "anthropic_json", boom)

    edl = _edl()
    edl["_queries_rewritten"] = True                             # a prior pass already ran
    out = _run(main._culturalize_broll_queries(edl, {"brand": {"niche": "startup"}}))
    assert out["broll"][0]["broll_query"] == "person working"


def test_rewrite_survives_llm_error(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    main._broll_query_cache.clear()

    async def blow_up(*a, **k):
        raise RuntimeError("transient")
    monkeypatch.setattr(main, "anthropic_json", blow_up)

    out = _run(main._culturalize_broll_queries(_edl(), {"brand": {"niche": "startup"}}))
    assert out["broll"][0]["broll_query"] == "person working"    # fail-soft to originals
    assert out["_queries_rewritten"] is True                     # still marked so it won't retry-loop
