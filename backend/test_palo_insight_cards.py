"""Phase 3 box 3 — Insight Discovery Engine cards: dedup, anti-repetition, template."""
from __future__ import annotations

import asyncio

import pytest

from app import palo_flags
from app import track_insights as ti


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self, recent=None):
        self.recent = recent or []
        self.rows = []
        self.seen = set()
        self.loaded_limit = None

    async def load_prompt_override(self, key):
        return None

    async def load_insights(self, creator_id, limit=50):
        self.loaded_limit = limit
        return self.recent

    async def upsert_insight(self, insight):
        h = insight["dedup_hash"]
        if h in self.seen:
            return False                    # duplicate
        self.seen.add(h)
        self.rows.append(insight)
        return True

    async def record_ai_usage(self, row):
        return True


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "TRACK_INSIGHTS", True)


def test_dedup_hash_stable_and_distinct():
    e1 = {"type": "view_milestone", "value": 100000}
    assert ti._dedup_hash("c1", e1) == ti._dedup_hash("c1", e1)
    assert ti._dedup_hash("c1", e1) != ti._dedup_hash("c1", {"type": "view_milestone", "value": 50000})
    assert ti._dedup_hash("c1", e1) != ti._dedup_hash("c2", e1)   # creator-scoped


def test_template_cards():
    assert "100,000 views" in ti._template_card({"type": "view_milestone", "value": 100000})["title"]
    assert "3.0x" in ti._template_card({"type": "video_spike", "multiplier": 3.0})["title"]


def test_write_insights_keyless_template_and_dedup(on):
    store = FakeStore()
    events = [{"type": "view_milestone", "value": 100000},
              {"type": "video_spike", "video_id": "v9", "multiplier": 4.0}]
    new = _run(ti.write_insights(store, "c1", events))
    assert len(new) == 2 and len(store.rows) == 2
    assert store.loaded_limit == 50                          # ≤50 anti-repetition context loaded
    assert new[0]["title"].startswith("You crossed 100,000")   # keyless -> template card
    assert new[0]["category"] == "blue" and new[1]["category"] == "yellow"
    # re-run the SAME events -> dedup_hash blocks all -> zero new
    assert _run(ti.write_insights(store, "c1", events)) == []
    assert len(store.rows) == 2


def test_scan_and_write_flag_off():
    assert _run(ti.scan_and_write(FakeStore(), "c1", {"total_views": 9_000_000})) == []
