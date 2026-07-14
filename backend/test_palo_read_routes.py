"""Hardening — GET /v1/insights + GET /v1/strategy read routes (P7.3/P7.4). Keyless."""
from __future__ import annotations

import asyncio

import main


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    async def load_insights(self, cid, limit=50):
        return [{"id": "i1", "title": "You crossed 100k"}]

    async def load_strategy(self, cid):
        return {"strategy_markdown": "## Insights\nx", "strategy_revision": 3}

    async def load_strategy_updates(self, cid, applied=False):
        return [{"update_text": "shifted to shorter hooks"}]


def test_get_insights_off_and_on(monkeypatch):
    assert _run(main.get_insights(creator_id="c1")) == {"mode": "off", "insights": []}
    monkeypatch.setattr(main.palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(main.palo_flags, "TRACK_INSIGHTS", True)
    monkeypatch.setattr(main, "_palo_store", FakeStore())
    out = _run(main.get_insights(creator_id="c1"))
    assert out["mode"] == "live" and out["insights"][0]["id"] == "i1"


def test_get_strategy_off_and_on(monkeypatch):
    off = _run(main.get_strategy(creator_id="c1"))
    assert off["mode"] == "off" and off["strategy"] is None
    monkeypatch.setattr(main.palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(main.palo_flags, "STRATEGY_COMPILER", True)
    monkeypatch.setattr(main, "_palo_store", FakeStore())
    out = _run(main.get_strategy(creator_id="c1"))
    assert out["mode"] == "live"
    assert out["strategy"]["strategy_revision"] == 3
    assert out["updates"][0]["update_text"] == "shifted to shorter hooks"
