"""Phase 2 box 4 — idea bank into /v1/feed + /v1/ideas, keyless."""
from __future__ import annotations

import asyncio

import pytest

import main
from app import ideas, palo_flags


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self, briefs=None):
        self._briefs = briefs or []

    async def load_briefs(self, creator_id, status="", limit=30):
        return self._briefs


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "IDEA_BANK", True)


# --- pure merge (ideate-rank leads the feed, dedup, cap) ----------------------

def test_merge_prepends_dedups_caps():
    items = [{"id": "s1"}, {"id": "b2"}]
    briefs = [{"id": "b1"}, {"id": "b2"}, {"id": "b3"}, {"id": "b4"}]
    merged = ideas.merge_briefs_into_feed(items, briefs, max_briefs=2)
    assert [m["id"] for m in merged] == ["b1", "b3", "s1", "b2"]   # b2 deduped, cap 2
    assert ideas.merge_briefs_into_feed(items, []) == items         # no briefs => unchanged


def test_brief_feed_items_flag_and_threshold(on):
    store = FakeStore(briefs=[{"id": "b1", "title": "T1", "summary": "s", "score": 0.9},
                              {"id": "b2", "title": "T2", "summary": "s", "score": 0.1}])
    items = _run(ideas.brief_feed_items(store, "c1", limit=6, min_score=0.5))
    assert [i["id"] for i in items] == ["b1"] and items[0]["kind"] == "idea"


def test_brief_feed_items_flag_off_empty():
    assert _run(ideas.brief_feed_items(FakeStore(briefs=[{"id": "b1", "score": 1}]), "c1")) == []


# --- /v1/feed merge wrapper ---------------------------------------------------

def test_feed_merge_off_is_unchanged():
    res = {"mode": "mock", "items": [{"id": "s1"}], "next_cursor": 1}
    out = _run(main._merge_briefs(dict(res), "c1", 0))
    assert out == res


def test_feed_get_prepends_briefs_when_on(monkeypatch, on):
    async def fake_impl(*a, **k):
        return {"mode": "mock", "items": [{"id": "s1"}], "next_cursor": 1}
    monkeypatch.setattr(main, "_feed_impl", fake_impl)

    async def fake_briefs(store, cid, limit=6, min_score=0.0):
        return [{"id": "b1", "kind": "idea"}]
    monkeypatch.setattr(main.ideas, "brief_feed_items", fake_briefs)

    out = _run(main.feed(creator_id="c1", cursor=0))
    assert out["items"][0]["id"] == "b1" and out["items"][1]["id"] == "s1"
    # page 2 must NOT re-inject
    out2 = _run(main.feed(creator_id="c1", cursor=1))
    assert out2["items"] == [{"id": "s1"}]


# --- /v1/ideas route ----------------------------------------------------------

def test_ideas_route_off():
    assert _run(main.ideas_bank(main._IdeasRequest(creator_id="c1"))) == {"mode": "off", "briefs": []}


def test_ideas_route_on(monkeypatch, on):
    async def fake_briefs(store, cid, limit=6, min_score=0.0):
        return [{"id": "b1", "kind": "idea", "title": "T"}]
    monkeypatch.setattr(main.ideas, "brief_feed_items", fake_briefs)
    out = _run(main.ideas_bank(main._IdeasRequest(creator_id="c1")))
    assert out["briefs"][0]["id"] == "b1"
