"""Phase 1 wiring — /v1/converse memory/ledger hooks (flag MEMORY_V2), keyless.

Proves: with the flag ON the compiled memory + ledger blocks are injected into the
strategist's system prompt (read path), and with the flag OFF nothing ported is even
called — so the default behaviour is byte-for-byte unchanged.
"""
from __future__ import annotations

import asyncio

import main


def _live_env(monkeypatch, flag_on: bool):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "x")               # force the live path
    monkeypatch.setattr(main.palo_flags, "PALO_PORT", flag_on)
    monkeypatch.setattr(main.palo_flags, "MEMORY_V2", flag_on)
    monkeypatch.setattr(main, "_palo_store", object())           # truthy; retrieve is patched

    async def fake_arms(cid):
        return []
    monkeypatch.setattr(main, "_arms_for_prompt", fake_arms)

    async def _noop(*a, **k):
        return 0
    monkeypatch.setattr(main.memory_v2, "remember", _noop)
    monkeypatch.setattr(main.recall_ledger, "record", _noop)

    captured = {}

    async def fake_json(system, user, schema, model, max_tokens):
        captured["system"] = system
        return {"reply": "hey", "intent": "none", "chips": [], "memory_updates": []}
    monkeypatch.setattr(main, "anthropic_json", fake_json)
    return captured


def test_converse_injects_memory_and_ledger_when_on(monkeypatch):
    captured = _live_env(monkeypatch, flag_on=True)

    async def fake_retrieve(store, cid, q, **kw):
        return [{"value": "Creator's name is Ada"}]

    async def fake_ledger(store, cid, **kw):
        return "<prior_recommendations>\n- [idea] reframe X\n</prior_recommendations>"
    monkeypatch.setattr(main.memory_v2, "retrieve", fake_retrieve)
    monkeypatch.setattr(main.recall_ledger, "ledger_block", fake_ledger)

    req = main.ConverseRequest(creator_id="c1", messages=[{"role": "user", "content": "who am i"}])
    out = asyncio.run(main.converse(req))
    assert out["mode"] == "live"
    assert "Creator's name is Ada" in captured["system"]
    assert "prior_recommendations" in captured["system"]


def test_converse_untouched_when_flag_off(monkeypatch):
    captured = _live_env(monkeypatch, flag_on=False)

    async def boom(*a, **k):
        raise AssertionError("ported memory path must not run when MEMORY_V2 is off")
    monkeypatch.setattr(main.memory_v2, "retrieve", boom)
    monkeypatch.setattr(main.recall_ledger, "ledger_block", boom)

    req = main.ConverseRequest(creator_id="c1", messages=[{"role": "user", "content": "hi"}])
    out = asyncio.run(main.converse(req))
    assert out["mode"] == "live"
    # system prompt carries none of the ported blocks
    assert "prior_recommendations" not in captured["system"]
    assert "<memory>" not in captured["system"]
