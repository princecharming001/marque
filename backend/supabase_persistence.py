"""Durable storage for the learning stack (arm_stats + post_registry) via Supabase.

The bandit's arm statistics and post registry live in module-level dicts in main.py
(fast, but wiped on restart and not shared across Render instances). This module is
a thin async PostgREST client that write-throughs those dicts to Supabase and loads
them back on startup / cache-miss.

Design contract:
  - Keyless-green: main.py only constructs a client when SUPABASE_URL + key are set;
    with no client every learning path stays pure in-memory, exactly as before.
  - Never raises into the hot path: every method catches its own errors and returns
    a falsy value; callers log and continue on in-memory state.
  - Idempotent upserts keyed by the natural key (creator_id+arm_key / post_id) so
    concurrent instances converge (last-write-wins, which the bandit tolerates).
"""
from __future__ import annotations

import asyncio
import logging
import random

import httpx

# Columns we own on each table — filter dict payloads to these so a stray in-memory
# key (e.g. a shaped arm's lift_pct/label) can't break a PostgREST insert.
_ARM_COLS = ("n", "sum_y", "alpha", "beta", "effect", "confidence")
_POST_COLS = ("creator_id", "platform", "scheduled_at", "pillar", "style", "format_id",
              "hook_signal", "predicted_score", "outcome_y", "settled", "metrics")

_BACKOFF = (0.5, 2.0, 8.0)

# Sentinel returned by load_post when the DB COULDN'T ANSWER (transport failure,
# non-200, unparseable body) — as opposed to None, which means the DB answered
# and the row is genuinely absent. Callers that must not guess (e.g. the metrics
# settle path, where treating a failed lookup as "unregistered" would silently
# discard a creator's confirmed reward) check for this; everyone else can keep
# treating any falsy result as a miss. Truthiness is False so legacy falsy checks
# behave unchanged.
class _Unavailable:
    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "UNAVAILABLE"


UNAVAILABLE = _Unavailable()


class SupabaseClient:
    def __init__(self, url: str, key: str):
        self.base = url.rstrip("/") + "/rest/v1"
        self.key = key
        self._headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    @property
    def enabled(self) -> bool:
        return bool(self.key and self.base.startswith("http"))

    async def _request(self, method: str, path: str, *, params=None, json=None, headers=None) -> httpx.Response | None:
        """One REST call with the same retry/backoff shape as anthropic()."""
        if not self.enabled:
            return None
        merged = {**self._headers, **(headers or {})}
        for attempt, delay in enumerate(list(_BACKOFF) + [None]):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    r = await client.request(method, f"{self.base}{path}",
                                             params=params, json=json, headers=merged)
                if r.status_code < 500:
                    return r
                if delay is None:
                    return r
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if delay is None:
                    logging.warning("supabase %s %s failed after retries: %s", method, path, e)
                    return None
            jitter = delay * 0.2 * (random.random() * 2 - 1)
            await asyncio.sleep(delay + jitter)
        return None

    # --- arm_stats -----------------------------------------------------------

    async def upsert_arm_stat(self, creator_id: str, arm_key: str, stat: dict) -> bool:
        row = {"creator_id": creator_id, "arm_key": arm_key,
               **{k: stat[k] for k in _ARM_COLS if k in stat}}
        r = await self._request(
            "POST", "/arm_stats", params={"on_conflict": "creator_id,arm_key"}, json=row,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return bool(r and r.status_code < 300)

    async def load_arm_stats(self, creator_id: str) -> dict[str, dict]:
        r = await self._request(
            "GET", "/arm_stats",
            params={"creator_id": f"eq.{creator_id}",
                    "select": "arm_key," + ",".join(_ARM_COLS)})
        if not (r and r.status_code == 200):
            return {}
        try:
            rows = r.json()
        except Exception:
            return {}
        out: dict[str, dict] = {}
        for row in rows:
            key = row.pop("arm_key", None)
            if key:
                out[key] = {k: row[k] for k in _ARM_COLS if row.get(k) is not None}
        return out

    # --- post_registry -------------------------------------------------------

    async def upsert_post(self, post_id: str, post: dict,
                          resolution: str = "merge-duplicates") -> bool:
        """Insert/merge a post row. Register uses resolution='ignore-duplicates' so a
        replayed/concurrent registration can never overwrite (and un-settle) an
        existing row; settle writes go through settle_post_conditional, not here."""
        row = {"post_id": post_id, **{k: post[k] for k in _POST_COLS if k in post}}
        r = await self._request(
            "POST", "/post_registry", params={"on_conflict": "post_id"}, json=row,
            headers={"Prefer": f"resolution={resolution},return=minimal"})
        return bool(r and r.status_code < 300)

    async def settle_post_conditional(self, post_id: str, payload: dict) -> bool | _Unavailable:
        """Atomically flip a post's settled flag false→true and write its settled
        payload in ONE conditional PATCH (WHERE settled=false). Returns True if THIS
        call won the latch (a row came back), False if the post was already settled
        (0 rows), or UNAVAILABLE if the DB couldn't answer. This is the cross-instance
        idempotency latch for the bandit: arms are updated only by the winner, so a
        concurrent/retried settle can never double-count the reward."""
        row = {k: payload[k] for k in _POST_COLS if k in payload}
        row["settled"] = True
        r = await self._request(
            "PATCH", "/post_registry",
            params={"post_id": f"eq.{post_id}", "settled": "eq.false"}, json=row,
            headers={"Prefer": "return=representation"})
        if not (r and r.status_code < 300):
            return UNAVAILABLE
        try:
            rows = r.json()
        except Exception:
            return UNAVAILABLE
        return bool(rows)

    async def load_post(self, post_id: str) -> dict | None | _Unavailable:
        """Row dict, None if the DB answered and the row is absent, or UNAVAILABLE
        (falsy) if the DB couldn't answer — so the settle path never mistakes an
        outage for an unregistered post."""
        r = await self._request("GET", "/post_registry",
                                params={"post_id": f"eq.{post_id}", "select": "*"})
        if not (r and r.status_code == 200):
            return UNAVAILABLE
        try:
            rows = r.json()
        except Exception:
            return UNAVAILABLE
        return rows[0] if rows else None

    async def load_all_posts(self, creator_id: str = "") -> list[dict]:
        params = {"select": "*"}
        if creator_id:
            params["creator_id"] = f"eq.{creator_id}"
        r = await self._request("GET", "/post_registry", params=params)
        if not (r and r.status_code == 200):
            return []
        try:
            return r.json()
        except Exception:
            return []

    # --- emulation_profiles ---------------------------------------------------
    # Analyzed style-DNA for a creator someone wants to emulate — keyed by
    # lowercase handle so re-linking the same page across users hits cache.

    async def upsert_emulation_profile(self, handle: str, platform: str, profile: dict) -> bool:
        row = {"handle": handle.lower(), "platform": platform, "profile": profile}
        r = await self._request(
            "POST", "/emulation_profiles", params={"on_conflict": "handle"}, json=row,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return bool(r and r.status_code < 300)

    async def load_emulation_profile(self, handle: str) -> dict | None:
        r = await self._request("GET", "/emulation_profiles",
                                params={"handle": f"eq.{handle.lower()}", "select": "profile"})
        if not (r and r.status_code == 200):
            return None
        try:
            rows = r.json()
        except Exception:
            return None
        return rows[0]["profile"] if rows and rows[0].get("profile") else None

    # --- clip_edit_sessions (F15: durable manual-editor state) ----------------
    # The whole in-memory job dict, stored as one JSONB blob — see migrations.sql
    # for why this is a blob rather than a column-per-field table.

    async def upsert_clip_job(self, job_id: str, job: dict) -> bool:
        row = {"job_id": job_id, "state": job}
        r = await self._request(
            "POST", "/clip_edit_sessions", params={"on_conflict": "job_id"}, json=row,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return bool(r and r.status_code < 300)

    async def load_clip_job(self, job_id: str) -> dict | None:
        r = await self._request("GET", "/clip_edit_sessions",
                                params={"job_id": f"eq.{job_id}", "select": "state"})
        if not (r and r.status_code == 200):
            return None
        try:
            rows = r.json()
        except Exception:
            return None
        return rows[0]["state"] if rows and rows[0].get("state") else None
