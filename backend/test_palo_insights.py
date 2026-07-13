"""Phase 3 box 2 — LOOP I: deterministic insight detection + the three Palo bugs, keyless."""
from __future__ import annotations

import asyncio

import pytest

from app import palo_flags
from app import track_insights as ti


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self, marks=None):
        self.marks = dict(marks or {})

    async def get_watermark(self, cid, key):
        return self.marks.get((cid, key))

    async def set_watermark(self, cid, key, val):
        self.marks[(cid, key)] = val
        return True


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "TRACK_INSIGHTS", True)


# --- pure math ----------------------------------------------------------------

def test_crossed_milestones():
    assert ti.crossed_milestones(9000, 60000, ti.VIEW_MILESTONES) == [10000, 25000, 50000]
    assert ti.crossed_milestones(60000, 55000, ti.VIEW_MILESTONES) == []   # went down
    assert ti.crossed_milestones(10000, 10000, ti.VIEW_MILESTONES) == []   # no new cross


def test_median_mad_and_spike():
    assert ti.median_mad([10, 10, 10]) == (10.0, 0.0)
    assert ti.detect_spike(30, [10, 10, 10]) is True        # 3x median
    assert ti.detect_spike(20, [10, 10, 10]) is False       # 2x < 2.5x
    assert ti.detect_spike(100, [10]) is False              # <2 reads


def test_underperformer():
    assert ti.is_underperformer(50, 1000) is True           # <10%
    assert ti.is_underperformer(500, 1000) is False


# --- BUG 1: first-run baseline fires ZERO -------------------------------------

def test_first_run_fires_zero(on):
    store = FakeStore()                                     # no watermark yet
    crossed = _run(ti.detect_milestones(store, "c1", "views", 5_000_000, ti.VIEW_MILESTONES))
    assert crossed == []                                    # day-1: nothing, even at 5M
    assert store.marks[("c1", "views_milestone")] == 5_000_000.0   # baseline recorded


# --- BUG 2: a crossed milestone never re-fires --------------------------------

def test_milestone_does_not_refire(on):
    store = FakeStore(marks={("c1", "views_milestone"): 9_000.0})
    first = _run(ti.detect_milestones(store, "c1", "views", 60_000, ti.VIEW_MILESTONES))
    assert first == [10_000, 25_000, 50_000]
    again = _run(ti.detect_milestones(store, "c1", "views", 60_000, ti.VIEW_MILESTONES))
    assert again == []                                     # watermark advanced -> no dup


# --- BUG 3: underperformer skips BEFORE any spike/LLM work --------------------

def test_underperformer_skips_before_work(on, monkeypatch):
    calls = {"n": 0}
    real = ti.detect_spike

    def spy(value, history, **kw):
        calls["n"] += 1
        return real(value, history, **kw)
    monkeypatch.setattr(ti, "detect_spike", spy)

    store = FakeStore(marks={("c1", "views_milestone"): 1e12, ("c1", "followers_milestone"): 1e12})
    snapshot = {"total_views": 0, "followers": 0, "channel_avg": 1000, "videos": [
        {"id": "lo", "views": 50, "history": [40, 40]},      # underperformer -> skipped
        {"id": "hi", "views": 5000, "history": [100, 100]}]}  # spike
    events = _run(ti.deterministic_events(store, "c1", snapshot))
    assert calls["n"] == 1                                  # detect_spike NOT called for 'lo'
    assert [e["type"] for e in events] == ["video_spike"]
    assert events[0]["video_id"] == "hi"


def test_deterministic_events_first_run_zero(on):
    store = FakeStore()
    snapshot = {"total_views": 9_000_000, "followers": 200_000, "channel_avg": 0, "videos": []}
    assert _run(ti.deterministic_events(store, "c1", snapshot)) == []   # day-1 fires nothing


def test_flag_off_noop():
    assert _run(ti.deterministic_events(FakeStore(), "c1", {"total_views": 9_000_000})) == []
