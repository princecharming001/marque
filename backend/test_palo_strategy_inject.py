"""Phase 4 box 4 — inject compiled strategy into converse + script gen. Keyless."""
from __future__ import annotations

import asyncio

import pytest

import main
from app import palo_flags
from app import strategy_compiler as sc


def _run(coro):
    return asyncio.run(coro)


class StratStore:
    def __init__(self, md=""):
        self.md = md

    async def load_strategy(self, cid):
        return {"strategy_markdown": self.md} if self.md else None


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "STRATEGY_COMPILER", True)


# --- strategy_block -----------------------------------------------------------

def test_strategy_block_flag_off():
    assert _run(sc.strategy_block(StratStore("## Insights\nx"), "c1")) == ""


def test_strategy_block_renders(on):
    block = _run(sc.strategy_block(StratStore("## Insights\nname the viewer"), "c1"))
    assert "creator_strategy" in block and "name the viewer" in block
    assert _run(sc.strategy_block(StratStore(""), "c1")) == ""      # no compiled strategy


# --- _inject_strategy ---------------------------------------------------------

def test_inject_off_is_noop(monkeypatch):
    monkeypatch.setattr(main.palo_flags, "STRATEGY_COMPILER", False)
    assert _run(main._inject_strategy("BASE", "c1")) == "BASE"


def test_inject_appends_block(on, monkeypatch):
    async def fake_block(store, cid):
        return "<creator_strategy>S</creator_strategy>"
    monkeypatch.setattr(main.strategy_compiler, "strategy_block", fake_block)
    out = _run(main._inject_strategy("BASE", "c1"))
    assert out.startswith("BASE") and "creator_strategy" in out


def test_inject_no_strategy_unchanged(on, monkeypatch):
    async def empty_block(store, cid):
        return ""
    monkeypatch.setattr(main.strategy_compiler, "strategy_block", empty_block)
    assert _run(main._inject_strategy("BASE", "c1")) == "BASE"


# --- converse shaped by the brain ---------------------------------------------

def test_converse_injects_strategy(on, monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "x")
    monkeypatch.setattr(main.palo_flags, "MEMORY_V2", False)         # isolate strategy

    async def fake_arms(cid):
        return []
    monkeypatch.setattr(main, "_arms_for_prompt", fake_arms)

    async def fake_block(store, cid):
        return "<creator_strategy>REGIME: breakout</creator_strategy>"
    monkeypatch.setattr(main.strategy_compiler, "strategy_block", fake_block)

    captured = {}

    async def fake_json(system, user, schema, model, max_tokens):
        captured["system"] = system
        return {"reply": "ok", "intent": "none", "chips": [], "memory_updates": []}
    monkeypatch.setattr(main, "anthropic_json", fake_json)

    out = _run(main.converse(main.ConverseRequest(creator_id="c1",
                             messages=[{"role": "user", "content": "hi"}])))
    assert out["mode"] == "live"
    assert "creator_strategy" in captured["system"] and "REGIME: breakout" in captured["system"]
