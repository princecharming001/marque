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
    assert out == {"started": False, "skipped": "flag_off"}


def test_cron_route_spawns_and_returns(monkeypatch):
    # Spawn-and-return: the route returns immediately; the sweep runs in the background.
    async def scenario():
        monkeypatch.setattr(main, "INTERNAL_CRON_TOKEN", "secret")
        monkeypatch.setattr(main.palo_flags, "PALO_PORT", True)
        monkeypatch.setattr(main.palo_flags, "IDEA_BANK", True)
        main._cron_running.clear()
        ran = {"n": 0}

        async def fake_cron(store, now):
            ran["n"] += 1
            return 5
        monkeypatch.setattr(main.ideas, "run_ideate_cron", fake_cron)
        out = await main.cron_ideate(main._CronRequest(token="secret"))
        assert out == {"started": True}
        await asyncio.sleep(0.02)                     # let the spawned sweep run + clear latch
        assert ran["n"] == 1 and main._cron_running.get("ideate") is False
    asyncio.run(scenario())


def test_cron_latch_blocks_overlap(monkeypatch):
    async def scenario():
        main._cron_running.clear()
        started = []

        async def slow():
            started.append(1)
            await asyncio.sleep(0.05)
            return 0
        r1 = main._start_cron("t", lambda: slow())
        r2 = main._start_cron("t", lambda: slow())    # blocked: first still running
        assert r1 == {"started": True}
        assert r2 == {"started": False, "reason": "already_running"}
        await asyncio.sleep(0.08)
        assert started == [1] and main._cron_running.get("t") is False   # one run, latch cleared
    asyncio.run(scenario())


def test_palo_scheduler_runs_due_sweeps(monkeypatch):
    async def scenario():
        monkeypatch.setattr(main.palo_flags, "PALO_PORT", True)
        monkeypatch.setattr(main.palo_flags, "IDEA_BANK", True)
        monkeypatch.setenv("PALO_SCHED_FIRST_DELAY_S", "0.01")
        monkeypatch.setenv("PALO_SCHED_INTERVAL_S", "0.01")
        main._cron_running.clear()
        called = {"n": 0}

        async def fake_ideate(store, now):
            called["n"] += 1
            return 0
        monkeypatch.setattr(main.ideas, "run_ideate_cron", fake_ideate)
        t = asyncio.create_task(main._palo_scheduler())
        await asyncio.sleep(0.15)                    # let it tick + run the spawned sweep
        t.cancel()
        assert called["n"] >= 1                       # scheduler fired the due sweep
    asyncio.run(scenario())


def test_cron_exemplar_route(monkeypatch):
    async def scenario():
        monkeypatch.setattr(main, "INTERNAL_CRON_TOKEN", "secret")
        main._cron_running.clear()
        try:
            await main.cron_exemplar(main._CronRequest(token="nope"))
            assert False, "bad token should 403"
        except HTTPException as e:
            assert e.status_code == 403
        monkeypatch.setattr(main.palo_flags, "PALO_PORT", False)
        out = await main.cron_exemplar(main._CronRequest(token="secret"))
        assert out == {"started": False, "skipped": "flag_off"}
    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# T3 (superintelligence epic) — /internal/cron/quality route + scheduler watermark
# ---------------------------------------------------------------------------

def test_cron_quality_route_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(main, "INTERNAL_CRON_TOKEN", "secret")
    with pytest.raises(HTTPException) as ei:
        _run(main.cron_quality(main._CronRequest(token="wrong")))
    assert ei.value.status_code == 403


def test_cron_quality_route_spawns_and_returns(monkeypatch):
    async def scenario():
        monkeypatch.setattr(main, "INTERNAL_CRON_TOKEN", "secret")
        main._cron_running.clear()
        ran = {"n": 0}

        async def fake_cron(store, now, gen_fast, gen_full):
            ran["n"] += 1
            return 3
        monkeypatch.setattr(main.quality_sentry, "run_quality_cron", fake_cron)
        out = await main.cron_quality(main._CronRequest(token="secret"))
        assert out == {"started": True}
        await asyncio.sleep(0.02)
        assert ran["n"] == 1 and main._cron_running.get("quality") is False
    asyncio.run(scenario())


def test_cron_quality_has_no_flag_gate():
    # T3 is testing infra, not a user feature — always on, unlike the other
    # /internal/cron/* routes which each check a palo_flags.* gate.
    import inspect
    src = inspect.getsource(main.cron_quality)
    assert "palo_flags.enabled" not in src


def test_palo_scheduler_runs_quality_cron_once_per_day(monkeypatch):
    async def scenario():
        monkeypatch.setenv("PALO_SCHED_FIRST_DELAY_S", "0.01")
        monkeypatch.setenv("PALO_SCHED_INTERVAL_S", "0.02")
        monkeypatch.setattr(main.palo_flags, "PALO_PORT", False)   # skip the other sweeps
        main._cron_running.clear()
        store = FakeStore()
        monkeypatch.setattr(main, "_palo_store", store)
        called = {"n": 0}

        async def fake_quality_cron(store, now, gen_fast, gen_full):
            called["n"] += 1
            return 1
        monkeypatch.setattr(main.quality_sentry, "run_quality_cron", fake_quality_cron)
        t = asyncio.create_task(main._palo_scheduler())
        await asyncio.sleep(0.12)   # several ticks would fire without the daily watermark
        t.cancel()
        assert called["n"] == 1   # watermark gated every tick after the first to a no-op
    asyncio.run(scenario())
