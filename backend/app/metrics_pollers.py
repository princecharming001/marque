"""Phase 3 (box 1) — post metric ingestion into metrics_ts.

The genuinely-new build: Yunicorn has no metric timeseries. Three sources, chosen by
the creator's tier chain (tiers.metrics_sources): Apify own-profile scrape (starter),
Post for Me analytics (growth), official IG Graph insights (studio) — each falling
back down the chain when its key/access is absent, so a 'studio' creator still collects
via Post for Me / Apify until the IG app clears review.

Keyless-green: a source with no key yields no rows (its fetcher returns []), so with no
keys the whole poller is a no-op. Fetchers are thin + injectable so tests run offline.
Flag TRACK_INSIGHTS gates the entry point.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

from app import palo_flags, tiers

_APIFY_KEY = os.environ.get("APIFY_KEY", "")
_APIFY_ACTOR = os.environ.get("APIFY_PROFILE_ACTOR", "apify~instagram-profile-scraper")
_POSTFORME_KEY = os.environ.get("POSTFORME_KEY", "")
_POSTFORME_BASE = os.environ.get("POSTFORME_BASE", "https://api.postforme.dev/v1")
_IG_GRAPH_TOKEN = os.environ.get("IG_GRAPH_TOKEN", "")

_METRICS = ("views", "likes", "comments")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def source_available(source: str) -> bool:
    return {"apify": bool(_APIFY_KEY), "postforme": bool(_POSTFORME_KEY),
            "ig_graph": bool(_IG_GRAPH_TOKEN)}.get(source, False)


def pick_source(tier: str) -> str | None:
    """First available source in the tier's chain (primary → fallbacks). None if the
    creator's whole chain is unconfigured (keyless)."""
    for src in tiers.metrics_sources(tier):
        if source_available(src):
            return src
    return None


def _rows(creator_id: str, entity_id: str, metrics: dict, source: str,
          captured_at: str, entity_type: str = "post") -> list[dict]:
    return [{"creator_id": creator_id, "entity_type": entity_type, "entity_id": entity_id,
             "metric": k, "value": float(v), "source": source, "captured_at": captured_at}
            for k, v in metrics.items() if v is not None]


# --- shared async HTTP client (loop-aware, mirrors main._get_anthropic_client) ---
# The fetchers MUST be async: they run inside the async cron sweep on the single Render
# uvicorn loop, so a blocking (sync) httpx call would freeze the ENTIRE instance for its
# timeout (apify 60s) per creator. A pooled AsyncClient also avoids a TLS handshake/scrape.
_client: httpx.AsyncClient | None = None
_client_loop = None


def _get_client() -> httpx.AsyncClient:
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not loop:
        _client = httpx.AsyncClient(timeout=60)
        _client_loop = loop
    return _client


async def aclose() -> None:
    """Close the pooled client on app shutdown (called from main._lifespan)."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None


# --- source fetchers (thin, async; keyless => []) -----------------------------

async def _apify_fetch(handle: str) -> list[dict]:
    if not (_APIFY_KEY and handle):
        return []
    try:
        r = await _get_client().post(
            f"https://api.apify.com/v2/acts/{_APIFY_ACTOR}/run-sync-get-dataset-items",
            params={"token": _APIFY_KEY}, json={"usernames": [handle], "resultsLimit": 30},
            timeout=120)
        # run-sync-get-dataset-items answers 201 Created (verified live) — a `== 200`
        # check silently dropped every successful scrape.
        if r.status_code not in (200, 201):
            logging.warning("[metrics] apify fetch %s -> http %s", handle, r.status_code)
            return []
        items = r.json()
    except Exception as e:
        logging.warning("[metrics] apify fetch failed: %s", e)
        return []
    # The profile-scraper actor returns ONE PROFILE per username with the posts nested
    # under `latestPosts` (verified live: profile carries an `id` but no per-post counts,
    # so iterating profiles yields zero metric rows). Flatten to post dicts; items that
    # already look like posts (a posts-scraper actor) pass through unchanged.
    posts: list[dict] = []
    for it in items if isinstance(items, list) else []:
        nested = it.get("latestPosts")
        posts.extend(nested if isinstance(nested, list) else [it])
    return posts


async def _postforme_fetch(account_id: str) -> list[dict]:
    if not (_POSTFORME_KEY and account_id):
        return []
    try:
        r = await _get_client().get(
            f"{_POSTFORME_BASE}/accounts/{account_id}/posts",
            headers={"Authorization": f"Bearer {_POSTFORME_KEY}"}, timeout=30)
        return r.json().get("data", []) if r.status_code == 200 else []
    except Exception as e:
        logging.warning("[metrics] postforme fetch failed: %s", e)
        return []


async def _ig_graph_fetch(account_id: str) -> list[dict]:
    if not (_IG_GRAPH_TOKEN and account_id):
        return []
    try:
        r = await _get_client().get(
            f"https://graph.facebook.com/v21.0/{account_id}/media",
            params={"fields": "id,like_count,comments_count,insights.metric(reach,plays)",
                    "access_token": _IG_GRAPH_TOKEN}, timeout=30)
        return r.json().get("data", []) if r.status_code == 200 else []
    except Exception as e:
        logging.warning("[metrics] ig_graph fetch failed: %s", e)
        return []


async def poll_apify(creator_id: str, handle: str, captured_at: str = "") -> list[dict]:
    at = captured_at or _now_iso()
    rows: list[dict] = []
    for p in await _apify_fetch(handle):
        pid = str(p.get("id") or p.get("shortCode") or p.get("shortcode") or "")
        if not pid:
            continue
        rows += _rows(creator_id, pid, {
            "views": p.get("videoViewCount") or p.get("views"),
            "likes": p.get("likesCount") or p.get("likes"),
            "comments": p.get("commentsCount") or p.get("comments")}, "apify", at)
    return rows


async def poll_postforme(creator_id: str, account_id: str, captured_at: str = "") -> list[dict]:
    at = captured_at or _now_iso()
    rows: list[dict] = []
    for p in await _postforme_fetch(account_id):
        pid = str(p.get("id") or "")
        m = p.get("metrics", p)
        if pid:
            rows += _rows(creator_id, pid, {
                "views": m.get("views") or m.get("impressions"),
                "likes": m.get("likes"), "comments": m.get("comments")}, "postforme", at)
    return rows


async def poll_ig_graph(creator_id: str, account_id: str, captured_at: str = "") -> list[dict]:
    at = captured_at or _now_iso()
    rows: list[dict] = []
    for p in await _ig_graph_fetch(account_id):
        pid = str(p.get("id") or "")
        if not pid:
            continue
        views = None
        for ins in (p.get("insights", {}).get("data", []) if isinstance(p.get("insights"), dict) else []):
            if ins.get("name") in ("plays", "reach") and ins.get("values"):
                views = ins["values"][0].get("value")
        rows += _rows(creator_id, pid, {
            "views": views, "likes": p.get("like_count"),
            "comments": p.get("comments_count")}, "ig_graph", at)
    return rows


_POLLERS = {"apify": poll_apify, "postforme": poll_postforme, "ig_graph": poll_ig_graph}


async def poll_creator(store, creator_id: str, tier: str, handle: str,
                       captured_at: str = "") -> int:
    """Ingest one creator's post metrics into metrics_ts, trying the tier's source chain
    IN ORDER until one actually yields rows. Key-availability alone is not enough: a
    growth/studio creator whose Post for Me account isn't linked would otherwise pick
    postforme (key exists), get nothing, and never fall back to Apify — confirmed live
    (real handle, growth tier, 0 rows forever). Returns #rows written. Never raises."""
    if not palo_flags.enabled(palo_flags.TRACK_INSIGHTS) or store is None or not creator_id:
        return 0
    for src in tiers.metrics_sources(tier):
        if not source_available(src):
            continue
        try:
            rows = await _POLLERS[src](creator_id, handle, captured_at)
        except Exception as e:
            logging.warning("[metrics] poll_creator %s via %s failed: %s", creator_id, src, e)
            continue
        if rows:
            try:
                return len(rows) if await store.insert_metrics(rows) else 0
            except Exception as e:
                logging.warning("[metrics] insert failed for %s: %s", creator_id, e)
                return 0
    return 0
