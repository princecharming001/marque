"""Phase 4 box 3 — compile gates (allowlist + freshness) + weekly cron + route. Keyless."""
from __future__ import annotations

import asyncio
import time

import pytest

import main
from app import palo_flags, tiers
from app import strategy_compiler as sc

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
        self.compiled = []

    async def load_all_creators(self):
        return self._creators

    async def load_creator_tier(self, cid):
        return None

    async def load_prompt_override(self, key):
        return None

    async def load_strategy(self, cid):
        return self._strategies.get(cid)

    async def upsert_strategy(self, cid, fields):
        self.compiled.append(cid)
        return True

    async def record_ai_usage(self, row):
        return True


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "STRATEGY_COMPILER", True)
    monkeypatch.setenv("STRATEGY_ALLOWLIST", "*")


# --- freshness gate -----------------------------------------------------------

def test_is_compile_due():
    # growth = biweekly (14d)
    assert sc.is_compile_due(tiers.GROWTH, None, NOW) is True            # never compiled
    assert sc.is_compile_due(tiers.GROWTH, _iso_ago(20), NOW) is True    # 20d > 14d
    assert sc.is_compile_due(tiers.GROWTH, _iso_ago(3), NOW) is False    # 3d < 14d
    # studio = weekly (7d)
    assert sc.is_compile_due(tiers.STUDIO, _iso_ago(10), NOW) is True
    assert sc.is_compile_due(tiers.STUDIO, _iso_ago(2), NOW) is False


# --- cron applies all three gates ---------------------------------------------

def test_run_compile_cron_only_stale(on):
    store = FakeStore(
        creators=[{"creator_id": "stale", "niche": "chess"}, {"creator_id": "fresh", "niche": "cooking"}],
        strategies={"stale": {"strategy_updated_at": _iso_ago(30)},   # due
                    "fresh": {"strategy_updated_at": _iso_ago(1)}})    # too fresh
    n = _run(sc.run_compile_cron(store, NOW))
    assert n == 1 and store.compiled == ["stale"]


def test_run_compile_cron_allowlist_blocks(on, monkeypatch):
    monkeypatch.setenv("STRATEGY_ALLOWLIST", "someone_else")            # not our creator
    store = FakeStore(creators=[{"creator_id": "c1", "niche": "chess"}])
    assert _run(sc.run_compile_cron(store, NOW)) == 0


# --- route --------------------------------------------------------------------

def test_compile_route_guard_and_flag(monkeypatch):
    monkeypatch.setattr(main, "INTERNAL_CRON_TOKEN", "secret")
    with pytest.raises(main.HTTPException):
        _run(main.cron_compile(main._CronRequest(token="nope")))
    monkeypatch.setattr(main.palo_flags, "PALO_PORT", False)
    assert _run(main.cron_compile(main._CronRequest(token="secret"))) == {"started": False, "skipped": "flag_off"}
