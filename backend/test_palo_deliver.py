"""Phase 3 box 4 — insight delivery (APNs) + settle bridge + cron/route, keyless."""
from __future__ import annotations

import asyncio

import pytest

import main
from app import palo_flags
from app import track_insights as ti


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self, creators=None, metrics=None):
        self._creators = creators or []
        self._metrics = metrics or []
        self.marked = []

    async def load_all_creators(self):
        return self._creators

    async def load_creator_tier(self, cid):
        return None

    async def load_metrics(self, cid, entity_id="", metric=""):
        return self._metrics

    async def mark_insight_delivered(self, iid):
        self.marked.append(iid)
        return True


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "TRACK_INSIGHTS", True)


# --- snapshot aggregation + settle bridge (pure) ------------------------------

def test_snapshot_from_metrics():
    rows = [
        {"entity_type": "post", "entity_id": "p1", "metric": "views", "value": 100},
        {"entity_type": "post", "entity_id": "p1", "metric": "views", "value": 900},   # latest
        {"entity_type": "post", "entity_id": "p2", "metric": "views", "value": 200},
        {"entity_type": "account", "entity_id": "acct", "metric": "views", "value": 5000},
    ]
    snap = ti._snapshot_from_metrics(rows)
    p1 = next(v for v in snap["videos"] if v["id"] == "p1")
    assert p1["views"] == 900 and p1["history"] == [100]
    assert snap["followers"] == 5000 and snap["channel_avg"] == (900 + 200) / 2


def test_settle_candidates_normalizes():
    rows = [{"entity_type": "post", "entity_id": "p1", "metric": "views", "value": 1000}]
    out = ti.settle_candidates(rows, channel_avg=500)
    assert out == [("p1", 1.0)]                              # 1000 / (2*500) = 1.0, clamped


# --- delivery ------------------------------------------------------------------

def test_deliver_pushes_and_marks(on, monkeypatch):
    async def fake_send(cid, title, body, iid="", seed=None):
        return 1
    monkeypatch.setattr("app.push.send_insight", fake_send)
    store = FakeStore()
    cards = [{"id": "i1", "title": "t", "description": "d", "conversation_seed": {"kind": "insight"}}]
    assert _run(ti.deliver_insights(store, "c1", cards)) == 1
    assert store.marked == ["i1"]


def test_deliver_keyless_still_marks(on, monkeypatch):
    async def no_push(*a, **k):
        return 0                                             # no APNs configured
    monkeypatch.setattr("app.push.send_insight", no_push)
    store = FakeStore()
    assert _run(ti.deliver_insights(store, "c1", [{"id": "i1", "title": "t", "description": "d"}])) == 0
    assert store.marked == ["i1"]                            # card still shown in-app


# --- cron pipeline + route -----------------------------------------------------

def test_run_insights_cron(on, monkeypatch):
    async def fake_poll(store, cid, tier, handle):
        return 0
    monkeypatch.setattr("app.metrics_pollers.poll_creator", fake_poll)

    async def fake_scan(store, cid, snap, brand=None):
        return [{"id": "i1", "title": "t", "description": "d"}]
    monkeypatch.setattr(ti, "scan_and_write", fake_scan)

    async def fake_send(*a, **k):
        return 1
    monkeypatch.setattr("app.push.send_insight", fake_send)

    store = FakeStore(creators=[{"creator_id": "c1", "niche": "chess"}])
    assert _run(ti.run_insights_cron(store, 1e7)) == 1


def test_cron_route_guard_and_flag(monkeypatch):
    monkeypatch.setattr(main, "INTERNAL_CRON_TOKEN", "secret")
    with pytest.raises(main.HTTPException):
        _run(main.cron_insights(main._CronRequest(token="nope")))
    monkeypatch.setattr(main.palo_flags, "PALO_PORT", False)
    assert _run(main.cron_insights(main._CronRequest(token="secret"))) == {"delivered": 0, "skipped": "flag_off"}
