"""Phase 6 box 2 — exemplar build (Opus) + refresh decider + cron. Keyless."""
from __future__ import annotations

import asyncio
import time

import pytest

from app import exemplar as ex
from app import palo_flags

DAY = 86400.0
NOW = 10_000_000.0


def _run(coro):
    return asyncio.run(coro)


def _iso_ago(days):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - days * DAY))


class FakeStore:
    def __init__(self, creators=None, strategies=None):
        self._creators = creators or []
        self._strategies = strategies or {}
        self.upserts = []

    async def load_prompt_override(self, key):
        return None

    async def load_all_creators(self):
        return self._creators

    async def load_creator_tier(self, cid):
        return None

    async def load_strategy(self, cid):
        return self._strategies.get(cid)

    async def upsert_strategy(self, cid, fields):
        self.upserts.append((cid, fields))
        self._strategies[cid] = {**(self._strategies.get(cid) or {}), **fields}
        return True

    async def record_ai_usage(self, row):
        return True


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "EXEMPLAR_BANK", True)
    monkeypatch.setenv("STRATEGY_ALLOWLIST", "*")


# --- build gates + persistence ------------------------------------------------

def test_build_flag_off():
    assert _run(ex.build_bank(FakeStore(), "c1", [], {"niche": "chess"})) is None


def test_build_allowlist_blocks(on, monkeypatch):
    monkeypatch.setenv("STRATEGY_ALLOWLIST", "other")
    assert _run(ex.build_bank(FakeStore(), "c1", [], {"niche": "chess"})) is None


def test_build_keyless_template_and_revision(on):
    store = FakeStore(strategies={"c1": {"exemplar_bank_revision": 4}})
    bank = _run(ex.build_bank(store, "c1", [{"title": "v", "views": 100}], {"niche": "chess"}))
    assert bank and "hook" in bank and bank["payoff"][0]["mechanism"]
    _, fields = store.upserts[0]
    assert fields["exemplar_bank_revision"] == 5 and fields["exemplar_bank_built_at"]


def test_build_uses_llm(on, monkeypatch):
    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        return {"hook": [{"id": "h9", "mechanism": "cold open", "lift": 3.0, "examples": []}]}
    monkeypatch.setattr(ex, "anthropic_cached_json", fake_json)
    bank = _run(ex.build_bank(FakeStore(), "c1", [], {"niche": "chess"}))
    assert bank["hook"][0]["id"] == "h9"


# --- refresh decider ----------------------------------------------------------

def test_should_rebuild():
    assert _run(ex.should_rebuild(FakeStore(strategies={"c1": {}}), "c1", NOW)) is True   # never built
    fresh = FakeStore(strategies={"c1": {"exemplar_bank_built_at": _iso_ago(2)}})
    assert _run(ex.should_rebuild(fresh, "c1", NOW)) is False
    stale = FakeStore(strategies={"c1": {"exemplar_bank_built_at": _iso_ago(35)}})
    assert _run(ex.should_rebuild(stale, "c1", NOW)) is True


def test_cron_rebuilds_only_stale(on):
    store = FakeStore(
        creators=[{"creator_id": "stale", "niche": "chess"}, {"creator_id": "fresh", "niche": "cooking"}],
        strategies={"stale": {"exemplar_bank_built_at": _iso_ago(40)},
                    "fresh": {"exemplar_bank_built_at": _iso_ago(1)}})
    assert _run(ex.run_exemplar_cron(store, NOW)) == 1
