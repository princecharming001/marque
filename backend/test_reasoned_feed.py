"""UX-G1: the feed consumes the Thompson arms and says WHY every pick is here."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)

_ARMS = [
    {"pillar": "Myth-bust the common advice", "style": "talking_head",
     "reason": "Talking Head outperforms your average by 32% (confirmed)"},
    {"pillar": "Teach one specific thing well", "style": "faceless",
     "reason": "Faceless — exploring where your data is still thin"},
]


def _stub_arms(monkeypatch, arms=_ARMS):
    async def fake_top_arms(creator_id, niche=""):
        return [dict(a) for a in arms]
    monkeypatch.setattr(main, "_top_arms", fake_top_arms)


def test_feed_sreq_consumes_arms():
    sreq, why = main._feed_sreq("fitness", "", "", "Grow", "", 0, "c1", None, arms=_ARMS)
    assert sreq.pillar == _ARMS[0]["pillar"]
    assert sreq.style == _ARMS[0]["style"]
    assert why == _ARMS[0]["reason"]


def test_feed_sreq_template_fallback_when_arms_exhausted():
    sreq, why = main._feed_sreq("fitness", "", "", "Grow", "", 5, "c1", None, arms=_ARMS)
    assert sreq.pillar                                   # template pillar
    assert why.startswith("From your '")


def test_feed_stamps_why_picked_fast_path(monkeypatch):
    _stub_arms(monkeypatch)
    main._feed_cache.clear()
    body = client.get("/v1/feed?creator_id=g1&niche=fitness&fresh=1").json()
    scripts = [it["script"] for it in body["items"] if it["type"] == "script"]
    assert scripts
    for s in scripts:
        assert s.get("why_picked") == _ARMS[0]["reason"]
    main._feed_cache.clear()


def test_feed_why_picked_cold_start_reasons():
    """No arm data at all → _top_arms returns niche priors with honest reasons; the
    feed still explains every pick (cold-start path, no stubbing)."""
    main._feed_cache.clear()
    main._arm_stats.pop("cold-g1", None)
    body = client.get("/v1/feed?creator_id=cold-g1&niche=cooking&fresh=1").json()
    scripts = [it["script"] for it in body["items"] if it["type"] == "script"]
    assert scripts
    for s in scripts:
        assert s.get("why_picked")                       # present
    # the cold reason is the honest niche-baseline copy from _cold_recommendations
    assert any("niche baseline" in s["why_picked"] or "From your" in s["why_picked"]
               for s in scripts)
    main._feed_cache.clear()


def test_full_quality_refresh_keeps_why_picked(monkeypatch):
    """The background full-quality path stamps the same why_picked (threaded through
    _refresh_feed_page)."""
    _stub_arms(monkeypatch)

    # The refresh now only writes when the pipeline produced genuine LIVE scripts (the
    # no-downgrade guard) — stub scripts() so this keyless test exercises that write path.
    async def live_scripts(sreq):
        return {"mode": "live", "scripts": main.mock_scripts(sreq)}
    monkeypatch.setattr(main, "scripts", live_scripts)

    sreq, why = main._feed_sreq("fitness", "", "", "Grow", "", 0, "g2", None, arms=_ARMS)
    asyncio.run(main._refresh_feed_page("k-g2", sreq, "fitness", "g2", "", 0, why_picked=why))
    entry = main._feed_cache.pop("k-g2")
    scripts = [it["script"] for it in entry["items"] if it["type"] == "script"]
    assert scripts and all(s.get("why_picked") == _ARMS[0]["reason"] for s in scripts)


def test_next_idea_routes_through_top_arms(monkeypatch):
    _stub_arms(monkeypatch)
    body = client.get("/v1/suggestions/next-idea?creator_id=g3&niche=fitness").json()
    idea = body["idea"]
    # topic steered by the top arm's pillar; grounding is the arm's honest reason
    assert _ARMS[0]["pillar"] in idea["title"]
    assert idea["grounding"] == _ARMS[0]["reason"]
