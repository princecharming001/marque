"""Dev tier switcher — override resolution + /v1/dev/tier route (ALLOW_DEV_TIER-gated)."""
from __future__ import annotations

import asyncio

import pytest

import main
from app import tiers


def _run(coro):
    return asyncio.run(coro)


def test_override_wins_and_clears():
    tiers.clear_override("c1")
    assert _run(tiers.tier_for("c1", None)) == tiers.DEFAULT_TIER
    tiers.set_override("c1", "studio")
    assert _run(tiers.tier_for("c1", None)) == "studio"
    tiers.set_override("c1", "nonsense")                 # normalized to default
    assert _run(tiers.tier_for("c1", None)) == tiers.DEFAULT_TIER
    tiers.clear_override("c1")
    assert _run(tiers.tier_for("c1", None)) == tiers.DEFAULT_TIER


def test_dev_route_disabled_by_default(monkeypatch):
    monkeypatch.setattr(main, "ALLOW_DEV_TIER", False)
    with pytest.raises(main.HTTPException) as ei:
        _run(main.dev_set_tier(main._DevTierRequest(creator_id="c1", tier="studio")))
    assert ei.value.status_code == 403


def test_dev_route_sets_and_reads_and_clears(monkeypatch):
    monkeypatch.setattr(main, "ALLOW_DEV_TIER", True)
    monkeypatch.setattr(main, "_palo_store", None)       # works without Supabase
    tiers.clear_override("c1")
    out = _run(main.dev_set_tier(main._DevTierRequest(creator_id="c1", tier="studio")))
    assert out["tier"] == "studio" and out["entitlements"]["video_brain"] is True
    assert "ig_graph" in out["metrics_sources"]
    assert _run(main.dev_get_tier(creator_id="c1"))["tier"] == "studio"
    cleared = _run(main.dev_set_tier(main._DevTierRequest(creator_id="c1", tier="")))
    assert cleared["tier"] == tiers.DEFAULT_TIER
    tiers.clear_override("c1")
