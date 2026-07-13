"""Phase 3 box 1 — metric pollers + tier source-chain, keyless (fetchers injected)."""
from __future__ import annotations

import asyncio

import pytest

from app import metrics_pollers as mp
from app import palo_flags, tiers


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self):
        self.inserted = []

    async def insert_metrics(self, rows):
        self.inserted.extend(rows)
        return True


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "TRACK_INSIGHTS", True)


# --- source selection (tier chain + availability) -----------------------------

def test_source_available_keyless_all_false():
    assert not mp.source_available("apify")
    assert not mp.source_available("postforme")
    assert not mp.source_available("ig_graph")
    assert mp.pick_source(tiers.STUDIO) is None          # whole chain unconfigured


def test_pick_source_walks_chain(monkeypatch):
    monkeypatch.setattr(mp, "_APIFY_KEY", "x")            # only apify configured
    assert mp.pick_source(tiers.STUDIO) == "apify"        # ig_graph/postforme absent -> apify
    assert mp.pick_source(tiers.STARTER) == "apify"
    monkeypatch.setattr(mp, "_POSTFORME_KEY", "y")        # now postforme available too
    assert mp.pick_source(tiers.GROWTH) == "postforme"    # growth prefers postforme
    assert mp.pick_source(tiers.STUDIO) == "postforme"    # studio: ig_graph absent -> postforme


# --- row shaping --------------------------------------------------------------

def test_poll_apify_row_shape(monkeypatch):
    monkeypatch.setattr(mp, "_APIFY_KEY", "x")
    monkeypatch.setattr(mp, "_apify_fetch",
                        lambda h: [{"id": "p1", "videoViewCount": 1200, "likesCount": 80, "commentsCount": 5}])
    rows = mp.poll_apify("c1", "handle", captured_at="T")
    assert len(rows) == 3
    by_metric = {r["metric"]: r for r in rows}
    assert by_metric["views"]["value"] == 1200.0 and by_metric["views"]["source"] == "apify"
    assert all(r["entity_id"] == "p1" and r["entity_type"] == "post" for r in rows)


def test_poll_apify_keyless_empty():
    assert mp.poll_apify("c1", "handle") == []            # no key -> no fetch -> no rows


# --- poll_creator dispatch + gate ---------------------------------------------

def test_poll_creator_flag_off_noop():
    assert _run(mp.poll_creator(FakeStore(), "c1", tiers.STARTER, "handle")) == 0


def test_poll_creator_ingests(on, monkeypatch):
    monkeypatch.setattr(mp, "_APIFY_KEY", "x")
    monkeypatch.setattr(mp, "_apify_fetch",
                        lambda h: [{"id": "p1", "views": 900, "likes": 40, "comments": 3},
                                   {"id": "p2", "views": 100, "likes": 2, "comments": 0}])
    store = FakeStore()
    n = _run(mp.poll_creator(store, "c1", tiers.STARTER, "handle"))
    assert n == 6 and len(store.inserted) == 6            # 2 posts x 3 metrics
    assert {r["source"] for r in store.inserted} == {"apify"}


def test_poll_creator_no_source_noop(on):
    # keyless: STUDIO chain has nothing configured -> no source -> 0
    assert _run(mp.poll_creator(FakeStore(), "c1", tiers.STUDIO, "handle")) == 0
