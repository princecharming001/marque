"""Durable storage for the ported Palo brains — memories, ledger, strategy, briefs,
insights, metrics timeseries, watermarks, prompt overrides, AI-usage accounting.

Subclasses the existing `SupabaseClient` so it reuses its battle-tested `_request`
(same retry/backoff, never-raises, 4xx-visible logging) and keyless-green contract:
with no SUPABASE_URL/key every method returns a falsy value and callers fall back to
in-memory / mock, exactly like the bandit's store. New tables live in migrations.sql
under the "PALO PORT" section; pgvector similarity search goes through the
`match_memories` RPC (PostgREST can't express cosine distance directly).
"""
from __future__ import annotations

import logging

from supabase_persistence import SupabaseClient, UNAVAILABLE

_MEMORY_COLS = ("id", "creator_id", "type", "key", "value", "confidence", "scope",
                "created_at", "updated_at", "deleted")
_STRATEGY_COLS = ("creator_id", "strategy_markdown", "strategy_playbooks",
                  "strategy_footnotes", "strategy_revision", "strategy_updated_at",
                  "exemplar_bank", "element_inventory", "exemplar_bank_revision",
                  "exemplar_bank_built_at")
_BRIEF_COLS = ("id", "creator_id", "source", "title", "summary", "beginning",
               "middle", "ending", "score", "status", "meta", "created_at")
_INSIGHT_COLS = ("id", "creator_id", "type", "category", "title", "description",
                 "content", "chips", "dedup_hash", "delivered", "conversation_seed",
                 "created_at")
_USAGE_COLS = ("creator_id", "operation", "model", "input_tokens", "output_tokens",
               "cost_usd")


class PaloStore(SupabaseClient):
    # --- creator tier (entitlement seam) -------------------------------------

    async def load_creator_tier(self, creator_id: str) -> str | None:
        r = await self._request("GET", "/creators",
                                params={"creator_id": f"eq.{creator_id}", "select": "tier"})
        if not (r and r.status_code == 200):
            return None
        try:
            rows = r.json()
        except Exception:
            return None
        return rows[0]["tier"] if rows and rows[0].get("tier") else None

    async def set_creator_tier(self, creator_id: str, tier: str) -> bool:
        r = await self._request(
            "POST", "/creators", params={"on_conflict": "creator_id"},
            json={"creator_id": creator_id, "tier": tier},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return bool(r and r.status_code < 300)

    # --- prompt overrides (get_prompt fallback source) -----------------------

    async def load_prompt_override(self, key: str) -> str | None:
        r = await self._request("GET", "/prompt_overrides",
                                params={"key": f"eq.{key}", "select": "prompt_text"})
        if not (r and r.status_code == 200):
            return None
        try:
            rows = r.json()
        except Exception:
            return None
        return rows[0]["prompt_text"] if rows and rows[0].get("prompt_text") else None

    async def load_all_prompt_overrides(self) -> dict[str, str]:
        r = await self._request("GET", "/prompt_overrides",
                                params={"select": "key,prompt_text"})
        if not (r and r.status_code == 200):
            return {}
        try:
            return {row["key"]: row["prompt_text"] for row in r.json()
                    if row.get("key") and row.get("prompt_text")}
        except Exception:
            return {}

    # --- ai_usage (cost accounting) ------------------------------------------

    async def record_ai_usage(self, row: dict) -> bool:
        payload = {k: row.get(k) for k in _USAGE_COLS if row.get(k) is not None}
        r = await self._request("POST", "/ai_usage", json=payload,
                                headers={"Prefer": "return=minimal"})
        return bool(r and r.status_code < 300)

    # --- memories (pgvector, mem0-style) -------------------------------------

    async def upsert_memory(self, row: dict) -> bool:
        payload = {k: row[k] for k in _MEMORY_COLS if k in row}
        if "embedding" in row:
            payload["embedding"] = row["embedding"]
        r = await self._request(
            "POST", "/memories", params={"on_conflict": "id"}, json=payload,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return bool(r and r.status_code < 300)

    async def soft_delete_memory(self, mem_id: str) -> bool:
        r = await self._request("PATCH", "/memories",
                                params={"id": f"eq.{mem_id}"}, json={"deleted": True},
                                headers={"Prefer": "return=minimal"})
        return bool(r and r.status_code < 300)

    async def match_memories(self, creator_id: str, embedding: list[float],
                             scope: str = "", limit: int = 8) -> list[dict]:
        """Cosine-nearest live memories for a creator via the match_memories RPC.
        Empty list on any failure (caller degrades to no-memory context)."""
        r = await self._request(
            "POST", "/rpc/match_memories",
            json={"p_creator_id": creator_id, "p_embedding": embedding,
                  "p_scope": scope or None, "p_limit": limit})
        if not (r and r.status_code == 200):
            return []
        try:
            return r.json() or []
        except Exception:
            return []

    async def load_memories(self, creator_id: str, scope: str = "") -> list[dict]:
        params = {"creator_id": f"eq.{creator_id}", "deleted": "is.false",
                  "select": ",".join(_MEMORY_COLS), "order": "updated_at.desc"}
        if scope:
            params["scope"] = f"eq.{scope}"
        r = await self._request("GET", "/memories", params=params)
        if not (r and r.status_code == 200):
            return []
        try:
            return r.json() or []
        except Exception:
            return []

    # --- recommendation_ledger (never re-pitch) ------------------------------

    async def append_ledger(self, creator_id: str, entries: list[dict]) -> bool:
        rows = [{"creator_id": creator_id, **e} for e in entries]
        if not rows:
            return True
        r = await self._request("POST", "/recommendation_ledger", json=rows,
                                headers={"Prefer": "return=minimal"})
        return bool(r and r.status_code < 300)

    async def load_ledger(self, creator_id: str, limit: int = 200) -> list[dict]:
        r = await self._request(
            "GET", "/recommendation_ledger",
            params={"creator_id": f"eq.{creator_id}", "select": "kind,summary,created_at",
                    "order": "created_at.desc", "limit": str(limit)})
        if not (r and r.status_code == 200):
            return []
        try:
            return r.json() or []
        except Exception:
            return []

    # --- channel_strategies (the compiled brain) -----------------------------

    async def upsert_strategy(self, creator_id: str, fields: dict) -> bool:
        row = {"creator_id": creator_id, **{k: fields[k] for k in _STRATEGY_COLS if k in fields}}
        r = await self._request(
            "POST", "/channel_strategies", params={"on_conflict": "creator_id"}, json=row,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return bool(r and r.status_code < 300)

    async def load_strategy(self, creator_id: str) -> dict | None:
        r = await self._request("GET", "/channel_strategies",
                                params={"creator_id": f"eq.{creator_id}", "select": "*"})
        if not (r and r.status_code == 200):
            return None
        try:
            rows = r.json()
        except Exception:
            return None
        return rows[0] if rows else None

    async def append_strategy_update(self, creator_id: str, update_text: str,
                                     source: str = "chat") -> bool:
        r = await self._request(
            "POST", "/strategy_updates",
            json={"creator_id": creator_id, "update_text": update_text,
                  "source": source, "applied": False},
            headers={"Prefer": "return=minimal"})
        return bool(r and r.status_code < 300)

    async def load_strategy_updates(self, creator_id: str, applied: bool | None = False) -> list[dict]:
        params = {"creator_id": f"eq.{creator_id}", "select": "id,update_text,source,created_at",
                  "order": "created_at.desc"}
        if applied is not None:
            params["applied"] = f"is.{str(applied).lower()}"
        r = await self._request("GET", "/strategy_updates", params=params)
        if not (r and r.status_code == 200):
            return []
        try:
            return r.json() or []
        except Exception:
            return []

    # --- briefs (idea bank) --------------------------------------------------

    async def upsert_brief(self, brief: dict) -> bool:
        row = {k: brief[k] for k in _BRIEF_COLS if k in brief}
        r = await self._request(
            "POST", "/briefs", params={"on_conflict": "id"}, json=row,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return bool(r and r.status_code < 300)

    async def load_briefs(self, creator_id: str, status: str = "", limit: int = 30) -> list[dict]:
        params = {"creator_id": f"eq.{creator_id}", "select": ",".join(_BRIEF_COLS),
                  "order": "score.desc,created_at.desc", "limit": str(limit)}
        if status:
            params["status"] = f"eq.{status}"
        r = await self._request("GET", "/briefs", params=params)
        if not (r and r.status_code == 200):
            return []
        try:
            return r.json() or []
        except Exception:
            return []

    # --- insight_feed (track insights + pulse) -------------------------------

    async def upsert_insight(self, insight: dict) -> bool | _Unavailable:  # noqa: F821
        """Insert with dedup_hash unique + resolution=ignore-duplicates so a re-run of
        the daily scan can never post the same card twice. Returns True on a NEW row,
        False if the dedup_hash already existed, UNAVAILABLE on DB outage."""
        row = {k: insight[k] for k in _INSIGHT_COLS if k in insight}
        r = await self._request(
            "POST", "/insight_feed", params={"on_conflict": "dedup_hash"}, json=row,
            headers={"Prefer": "resolution=ignore-duplicates,return=representation"})
        if not (r and r.status_code < 300):
            return UNAVAILABLE
        try:
            return bool(r.json())
        except Exception:
            return UNAVAILABLE

    async def load_insights(self, creator_id: str, limit: int = 50) -> list[dict]:
        r = await self._request(
            "GET", "/insight_feed",
            params={"creator_id": f"eq.{creator_id}", "select": ",".join(_INSIGHT_COLS),
                    "order": "created_at.desc", "limit": str(limit)})
        if not (r and r.status_code == 200):
            return []
        try:
            return r.json() or []
        except Exception:
            return []

    async def mark_insight_delivered(self, insight_id: str) -> bool:
        r = await self._request("PATCH", "/insight_feed",
                                params={"id": f"eq.{insight_id}"}, json={"delivered": True},
                                headers={"Prefer": "return=minimal"})
        return bool(r and r.status_code < 300)

    # --- metrics_ts + watermarks (post-performance) --------------------------

    async def insert_metrics(self, rows: list[dict]) -> bool:
        if not rows:
            return True
        # ignore-duplicates against metrics_ts_uniq so a re-run/overlapping poll of the same
        # (creator, post, metric, timestamp) reading can't insert a duplicate that skews spikes.
        r = await self._request(
            "POST", "/metrics_ts",
            params={"on_conflict": "creator_id,entity_id,metric,captured_at"}, json=rows,
            headers={"Prefer": "resolution=ignore-duplicates,return=minimal"})
        return bool(r and r.status_code < 300)

    async def load_metrics(self, creator_id: str, entity_id: str = "",
                           metric: str = "", since: str = "") -> list[dict]:
        params = {"creator_id": f"eq.{creator_id}", "select": "*",
                  "order": "captured_at.asc"}
        if entity_id:
            params["entity_id"] = f"eq.{entity_id}"
        if metric:
            params["metric"] = f"eq.{metric}"
        if since:                       # bound the read so the insight snapshot isn't O(all rows)
            params["captured_at"] = f"gte.{since}"
        r = await self._request("GET", "/metrics_ts", params=params)
        if not (r and r.status_code == 200):
            return []
        try:
            return r.json() or []
        except Exception:
            return []

    # --- clip sessions (the creator's analyzed videos → compiler/exemplar evidence) ---

    async def load_clip_sessions(self, creator_id: str, limit: int = 50) -> list[dict]:
        """The creator's stored clip-job states (dossier + transcript + title) for the
        strategy compiler / exemplar builder. JSONB filter on state->>creator_id; empty on
        any failure. `clip_edit_sessions` is written by main.py's upsert_clip_job."""
        r = await self._request(
            "GET", "/clip_edit_sessions",
            params={"state->>creator_id": f"eq.{creator_id}", "select": "state",
                    "limit": str(limit)})
        if not (r and r.status_code == 200):
            return []
        try:
            return [row["state"] for row in r.json() if isinstance(row.get("state"), dict)]
        except Exception:
            return []

    async def get_watermark(self, creator_id: str, key: str) -> float | None:
        r = await self._request("GET", "/metric_watermarks",
                                params={"creator_id": f"eq.{creator_id}", "key": f"eq.{key}",
                                        "select": "value"})
        if not (r and r.status_code == 200):
            return None
        try:
            rows = r.json()
        except Exception:
            return None
        return rows[0]["value"] if rows else None

    async def set_watermark(self, creator_id: str, key: str, value: float) -> bool:
        r = await self._request(
            "POST", "/metric_watermarks", params={"on_conflict": "creator_id,key"},
            json={"creator_id": creator_id, "key": key, "value": value},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        return bool(r and r.status_code < 300)


# _Unavailable is re-exported for the type hint above without importing privately.
from supabase_persistence import _Unavailable  # noqa: E402


def make_store(url: str, key: str) -> PaloStore | None:
    """One constructor mirroring main.py's _supabase_client wiring: None keyless so the
    whole port stays pure in-memory / mock with no Supabase configured."""
    if not (url and key):
        return None
    store = PaloStore(url, key)
    if not store.enabled:
        logging.info("[palo] Supabase not configured; Palo store disabled (in-memory/mock).")
        return None
    return store
