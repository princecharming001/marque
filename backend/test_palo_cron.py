"""Phase 2 box 3 — idea-bank cron + tier cadence + route guard, keyless."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import main
from app import ideas, palo_flags, tiers

DAY = 86400.0


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self, creators=None, marks=None):
        self._creators = creators or []
        self.marks = dict(marks or {})
        self.upserts = []

    async def load_prompt_override(self, key):
        return None

    async def load_all_creators(self):
        return self._creators

    async def load_creator_tier(self, cid):
        return None

    async def get_watermark(self, cid, key):
        return self.marks.get((cid, key))

    async def set_watermark(self, cid, key, val):
        self.marks[(cid, key)] = val
        return True

    async def upsert_brief(self, b):
        self.upserts.append(b)
        return True

    async def record_ai_usage(self, row):
        return True


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "IDEA_BANK", True)


# --- cadence math --------------------------------------------------------------

def test_is_ideate_due_by_tier():
    now = 10_000_000.0
    # studio = nightly (1d): due after >1 day, not before
    assert ideas.is_ideate_due(tiers.STUDIO, now - 2 * DAY, now) is True
    assert ideas.is_ideate_due(tiers.STUDIO, now - 0.5 * DAY, now) is False
    # growth = 3x/week (~2.33d)
    assert ideas.is_ideate_due(tiers.GROWTH, now - 3 * DAY, now) is True
    assert ideas.is_ideate_due(tiers.GROWTH, now - 1 * DAY, now) is False
    # starter = weekly (7d)
    assert ideas.is_ideate_due(tiers.STARTER, now - 8 * DAY, now) is True
    assert ideas.is_ideate_due(tiers.STARTER, now - 3 * DAY, now) is False
    # never run -> due
    assert ideas.is_ideate_due(tiers.STUDIO, 0, now) is True


# --- run_ideate_for + cron -----------------------------------------------------

def test_run_ideate_for_generates_when_due_then_skips(on):
    store = FakeStore()
    now = 10_000_000.0
    n = _run(ideas.run_ideate_for(store, "c1", {"niche": "chess"}, tiers.STUDIO, now))
    assert n == 3 and len(store.upserts) == 3                # generated (mock) + persisted
    assert store.marks[("c1", "ideate_last_run")] == now
    # immediately after, not due
    assert _run(ideas.run_ideate_for(store, "c1", {"niche": "chess"}, tiers.STUDIO, now + 3600)) == 0


def test_run_ideate_for_flag_off_is_noop():
    store = FakeStore()
    assert _run(ideas.run_ideate_for(store, "c1", {"niche": "chess"}, tiers.STUDIO, 10_000_000.0)) == 0


def test_run_ideate_cron_sweeps_fleet(on):
    store = FakeStore(creators=[{"creator_id": "a", "niche": "chess"},
                                {"creator_id": "b", "niche": "cooking"}])
    total = _run(ideas.run_ideate_cron(store, 10_000_000.0))
    assert total == 6                                        # 2 creators x 3 briefs


# --- route guard ---------------------------------------------------------------

def test_cron_route_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(main, "INTERNAL_CRON_TOKEN", "secret")
    with pytest.raises(HTTPException) as ei:
        _run(main.cron_ideate(main._CronRequest(token="wrong")))
    assert ei.value.status_code == 403


def test_cron_route_flag_off(monkeypatch):
    monkeypatch.setattr(main, "INTERNAL_CRON_TOKEN", "secret")
    monkeypatch.setattr(main.palo_flags, "PALO_PORT", False)
    out = _run(main.cron_ideate(main._CronRequest(token="secret")))
    assert out == {"ran": 0, "skipped": "flag_off"}


def test_cron_route_runs_when_on(monkeypatch):
    monkeypatch.setattr(main, "INTERNAL_CRON_TOKEN", "secret")
    monkeypatch.setattr(main.palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(main.palo_flags, "IDEA_BANK", True)

    async def fake_cron(store, now):
        return 5
    monkeypatch.setattr(main.ideas, "run_ideate_cron", fake_cron)
    out = _run(main.cron_ideate(main._CronRequest(token="secret")))
    assert out == {"ran": 5}
