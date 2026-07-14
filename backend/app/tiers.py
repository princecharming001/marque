"""Creator tiers — the paid packaging for the Palo-powered AI backend.

Three tiers, each a COHERENT bundle (owner decision 2026-07-14): richer performance
data AND more feature depth climb together, so cost-to-serve tracks price.

  starter  (T1)  ~$4/creator/mo   Apify own-profile metrics, monthly strategy compile,
                                   weekly idea batches. The floor: real learning loop,
                                   scraped metrics.
  growth   (T2)  ~$7/creator/mo   Post for Me analytics, biweekly compile, daily insights,
                                   3x/week ideas. The default paid tier.
  studio   (T3)  ~$14/creator/mo  Official IG Graph insights, weekly compile, nightly
                                   ideas, + video-brain + exemplar bank. The ceiling.

Entitlement plumbing (owner decision): a stubbed `creator_tier` — resolved from a
`creators.tier` column when Supabase is wired, else PALO_DEFAULT_TIER — so nothing
touches the existing IAP/RevenueCat billing until you map SKUs later. `tier_for()` is
the ONE place that resolves a creator's tier; everything else reads entitlements off
the resolved tier string.
"""
from __future__ import annotations

import os

# Ordered floor -> ceiling. Rank is used for ">= this tier?" gates.
STARTER, GROWTH, STUDIO = "starter", "growth", "studio"
ORDER = (STARTER, GROWTH, STUDIO)
RANK = {t: i + 1 for i, t in enumerate(ORDER)}

# Default tier for any creator with no explicit entitlement yet (keyless / pre-billing).
# Set PALO_DEFAULT_TIER=studio in a dev env to exercise the full stack.
DEFAULT_TIER = os.environ.get("PALO_DEFAULT_TIER", GROWTH)
if DEFAULT_TIER not in RANK:
    DEFAULT_TIER = GROWTH

# Schedule -> runs/month (drives cost + the in-process scheduler cadence).
_RUNS = {"off": 0.0, "monthly": 1.0, "weekly": 4.3, "biweekly": 2.15,
         "3xweek": 13.0, "daily": 30.0, "nightly": 30.0}


def runs_per_month(schedule: str) -> float:
    return _RUNS.get(schedule, 0.0)


# The entitlement matrix. Base capabilities (memory, idea bank, write agent, insights,
# script gen) are available to EVERY tier; the tier modulates cadence, the metrics
# data source, and the two premium features (video-brain, exemplar bank).
_ENTITLEMENTS: dict[str, dict] = {
    STARTER: {
        "metrics": "apify",
        "compile": "monthly",
        "ideas": "weekly",
        "insights": "3xweek",
        "exemplar_bank": False,
    },
    GROWTH: {
        "metrics": "postforme",
        "compile": "biweekly",
        "ideas": "3xweek",
        "insights": "daily",
        "exemplar_bank": False,
    },
    STUDIO: {
        "metrics": "ig_graph",
        "compile": "weekly",
        "ideas": "nightly",
        "insights": "daily",
        "exemplar_bank": True,
    },
}

# Metrics-source fallback chain per tier: a tier's PRIMARY source, then what it may
# fall back to if that source has no key/access (IG Graph app-review pending, etc.).
# The poller walks this list and uses the first available source — so "studio" still
# collects metrics via Post for Me / Apify until the official IG app clears review.
_METRICS_CHAIN: dict[str, tuple[str, ...]] = {
    STARTER: ("apify",),
    GROWTH: ("postforme", "apify"),
    STUDIO: ("ig_graph", "postforme", "apify"),
}


def normalize(tier: str | None) -> str:
    return tier if tier in RANK else DEFAULT_TIER


def entitlements(tier: str | None) -> dict:
    return dict(_ENTITLEMENTS[normalize(tier)])


def at_least(tier: str | None, minimum: str) -> bool:
    """True if `tier` is `minimum` or higher (e.g. gate a feature to growth+)."""
    return RANK[normalize(tier)] >= RANK[normalize(minimum)]


def has_feature(tier: str | None, feature: str) -> bool:
    """Boolean features only (exemplar_bank; video_brain removed with its dead flag)."""
    return bool(_ENTITLEMENTS[normalize(tier)].get(feature))


def cadence(tier: str | None, kind: str) -> str:
    """Schedule string for a scheduled job kind: 'compile' | 'ideas' | 'insights'."""
    return _ENTITLEMENTS[normalize(tier)].get(kind, "off")


def metrics_sources(tier: str | None) -> tuple[str, ...]:
    """Ordered source chain for the metrics poller (primary first)."""
    return _METRICS_CHAIN[normalize(tier)]


# Dev-only in-process tier overrides (the /v1/dev/tier demo switcher). Checked first so a
# demo works even without Supabase; guarded at the route by ALLOW_DEV_TIER, never in prod.
_OVERRIDE: dict[str, str] = {}


def set_override(creator_id: str, tier: str) -> str:
    t = normalize(tier)
    _OVERRIDE[creator_id] = t
    return t


def clear_override(creator_id: str) -> None:
    _OVERRIDE.pop(creator_id, None)


async def tier_for(creator_id: str, store=None) -> str:
    """Resolve a creator's tier. Dev override wins (demo); then `creators.tier` via the
    Palo store when provided; otherwise DEFAULT_TIER. This is the single seam to wire real
    IAP/RevenueCat entitlements into later — every gate calls through here. Never raises."""
    if creator_id in _OVERRIDE:
        return _OVERRIDE[creator_id]
    if store is not None:
        try:
            t = await store.load_creator_tier(creator_id)
            if t in RANK:
                return t
        except Exception:
            pass
    return DEFAULT_TIER
