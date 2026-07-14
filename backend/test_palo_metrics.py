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

    async def fake(h):
        return [{"id": "p1", "videoViewCount": 1200, "likesCount": 80, "commentsCount": 5}]
    monkeypatch.setattr(mp, "_apify_fetch", fake)
    rows = _run(mp.poll_apify("c1", "handle", captured_at="T"))
    assert len(rows) == 3
    by_metric = {r["metric"]: r for r in rows}
    assert by_metric["views"]["value"] == 1200.0 and by_metric["views"]["source"] == "apify"
    assert all(r["entity_id"] == "p1" and r["entity_type"] == "post" for r in rows)


def test_poll_apify_keyless_empty():
    assert _run(mp.poll_apify("c1", "handle")) == []      # no key -> no fetch -> no rows


def test_pollers_are_async_no_event_loop_blocking():
    # The fetchers + pollers MUST be coroutines so they never block the uvicorn event
    # loop (the CRITICAL bug: sync httpx froze the whole instance per creator).
    import inspect
    assert inspect.iscoroutinefunction(mp._apify_fetch)
    assert inspect.iscoroutinefunction(mp._postforme_fetch)
    assert inspect.iscoroutinefunction(mp._ig_graph_fetch)
    assert inspect.iscoroutinefunction(mp.poll_apify)


# --- poll_creator dispatch + gate ---------------------------------------------

def test_poll_creator_flag_off_noop():
    assert _run(mp.poll_creator(FakeStore(), "c1", tiers.STARTER, "handle")) == 0


def test_poll_creator_ingests(on, monkeypatch):
    monkeypatch.setattr(mp, "_APIFY_KEY", "x")

    async def fake(h):
        return [{"id": "p1", "views": 900, "likes": 40, "comments": 3},
                {"id": "p2", "views": 100, "likes": 2, "comments": 0}]
    monkeypatch.setattr(mp, "_apify_fetch", fake)
    store = FakeStore()
    n = _run(mp.poll_creator(store, "c1", tiers.STARTER, "handle"))
    assert n == 6 and len(store.inserted) == 6            # 2 posts x 3 metrics
    assert {r["source"] for r in store.inserted} == {"apify"}


def test_poll_creator_no_source_noop(on):
    # keyless: STUDIO chain has nothing configured -> no source -> 0
    assert _run(mp.poll_creator(FakeStore(), "c1", tiers.STUDIO, "handle")) == 0


def test_poll_creator_falls_through_chain_on_empty_rows(on, monkeypatch):
    # LIVE-CONFIRMED bug: growth tier picked postforme (key exists), the creator had no
    # linked PFM account -> [] -> never fell back to apify -> zero metrics forever.
    # poll_creator must try the chain IN ORDER until a source actually yields rows.
    monkeypatch.setattr(mp, "_POSTFORME_KEY", "y")
    monkeypatch.setattr(mp, "_APIFY_KEY", "x")

    async def pfm_empty(cid, account_id, captured_at=""):
        return []                                        # unlinked account: nothing

    async def apify_rows(cid, handle, captured_at=""):
        return mp._rows(cid, "p1", {"views": 500.0, "likes": 10.0}, "apify", "T")
    monkeypatch.setitem(mp._POLLERS, "postforme", pfm_empty)
    monkeypatch.setitem(mp._POLLERS, "apify", apify_rows)
    store = FakeStore()
    n = _run(mp.poll_creator(store, "c1", tiers.GROWTH, "realhandle"))
    assert n == 2 and {r["source"] for r in store.inserted} == {"apify"}


def test_poll_creator_source_error_falls_through(on, monkeypatch):
    # A raising source must not kill the sweep — the next source in the chain runs.
    monkeypatch.setattr(mp, "_POSTFORME_KEY", "y")
    monkeypatch.setattr(mp, "_APIFY_KEY", "x")

    async def pfm_boom(cid, account_id, captured_at=""):
        raise RuntimeError("pfm down")

    async def apify_rows(cid, handle, captured_at=""):
        return mp._rows(cid, "p1", {"views": 100.0}, "apify", "T")
    monkeypatch.setitem(mp._POLLERS, "postforme", pfm_boom)
    monkeypatch.setitem(mp._POLLERS, "apify", apify_rows)
    store = FakeStore()
    assert _run(mp.poll_creator(store, "c1", tiers.GROWTH, "h")) == 1


def test_apify_fetch_accepts_201_and_unwraps_latest_posts(on, monkeypatch):
    # LIVE-CONFIRMED double bug: run-sync-get-dataset-items answers HTTP 201 (the old
    # `== 200` check dropped every successful scrape), and the profile-scraper actor
    # nests the posts under profile.latestPosts (iterating profiles yields zero rows).
    monkeypatch.setattr(mp, "_APIFY_KEY", "x")

    class FakeResp:
        status_code = 201
        def json(self):
            return [{"id": "profile1", "followersCount": 50000, "latestPosts": [
                {"id": "post1", "videoViewCount": 14_666_566, "likesCount": 811_637,
                 "commentsCount": 4_193},
                {"id": "post2", "videoViewCount": 900, "likesCount": 40, "commentsCount": 2},
            ]}]

    class FakeClient:
        async def post(self, *a, **k):
            return FakeResp()
    monkeypatch.setattr(mp, "_get_client", lambda: FakeClient())
    posts = _run(mp._apify_fetch("arielyu.fit"))
    assert [p["id"] for p in posts] == ["post1", "post2"]  # posts, not the profile
    rows = _run(mp.poll_apify("c1", "arielyu.fit", captured_at="T"))
    assert len(rows) == 6                                  # 2 posts x 3 metrics
    assert max(r["value"] for r in rows) == 14_666_566.0


def test_apify_fetch_passes_through_flat_post_items(on, monkeypatch):
    # A posts-scraper actor (flat post dicts, no latestPosts) must keep working.
    monkeypatch.setattr(mp, "_APIFY_KEY", "x")

    class FakeResp:
        status_code = 200
        def json(self):
            return [{"id": "p1", "videoViewCount": 100, "likesCount": 5, "commentsCount": 1}]

    class FakeClient:
        async def post(self, *a, **k):
            return FakeResp()
    monkeypatch.setattr(mp, "_get_client", lambda: FakeClient())
    posts = _run(mp._apify_fetch("h"))
    assert posts == [{"id": "p1", "videoViewCount": 100, "likesCount": 5, "commentsCount": 1}]
