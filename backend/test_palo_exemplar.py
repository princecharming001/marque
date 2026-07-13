"""Phase 6 box 1 — exemplar bank retrieval/index (hand-seeded), keyless."""
from __future__ import annotations

import asyncio

import pytest

from app import exemplar as ex
from app import palo_flags

BANK = {
    "hook": [{"id": "h1", "mechanism": "open with a question", "lift": 2.3, "examples": [{"observed": "x"}]},
             {"id": "h2", "mechanism": "cold open on the result", "lift": 3.1}],
    "payoff": [{"id": "p1", "mechanism": "decisive reveal", "lift": 1.5}],
}


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self, bank=None):
        self.bank = bank

    async def load_strategy(self, cid):
        return {"exemplar_bank": self.bank} if self.bank is not None else None


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "EXEMPLAR_BANK", True)


def test_flag_off_empty():
    assert _run(ex.load_index(FakeStore(BANK), "c1")) == []
    assert _run(ex.exemplar_block(FakeStore(BANK), "c1")) == ""


def test_index_is_lift_ordered(on):
    idx = _run(ex.load_index(FakeStore(BANK), "c1"))
    assert [p["id"] for p in idx] == ["h2", "h1", "p1"]      # 3.1 > 2.3 > 1.5
    assert idx[0]["category"] == "hook" and idx[0]["mechanism"] == "cold open on the result"


def test_render_and_block(on):
    idx = _run(ex.load_index(FakeStore(BANK), "c1"))
    rendered = ex.render_index(idx, limit=2)
    assert "[hook:h2] lift 3.1" in rendered and "[hook:h1]" in rendered and "p1" not in rendered
    block = _run(ex.exemplar_block(FakeStore(BANK), "c1"))
    assert "exemplar_patterns" in block and "cold open on the result" in block


def test_empty_bank_block_is_empty(on):
    assert _run(ex.exemplar_block(FakeStore({}), "c1")) == ""
    assert _run(ex.exemplar_block(FakeStore(None), "c1")) == ""


def test_dereference_returns_full_cards(on):
    cards = _run(ex.dereference(FakeStore(BANK), "c1", ["h1", "nope", "p1"]))
    assert [c["id"] for c in cards] == ["h1", "p1"]          # unknown id dropped
    assert cards[0]["examples"] == [{"observed": "x"}]       # full card (with examples)
