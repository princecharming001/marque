"""Phase 1 — self-learning memory (ported from Palo memory/ + vector_service reconcile).

Two guarantees carried over verbatim from Palo (learned the hard way there):
  1. Insights are BANNED from memory — enforced in CODE (_DROP_TYPES), because the
     extraction prompt is override-able and can't be trusted to hold the line. Memory
     is stable ACTIONABLES only (preferences, personal facts, instructions); Strategy
     owns performance/pattern truth.
  2. Channel/user SCOPE is a hard filter on retrieval, never a soft ranking signal —
     so a preference for one account never leaks into another.

Fire-and-forget after each turn (zero user latency); retrieval is cue-gated + weighted
(0.55 similarity + 0.25 confidence + 0.20 recency). Keyless-green: no key ⇒ extraction
returns [] and retrieval degrades to recency; no store ⇒ pure no-op.
"""
from __future__ import annotations

import logging
import asyncio
import os
import time

import httpx

from app import ai_usage, palo_flags, palo_prompts
from app.palo_llm import anthropic_cached_json
from prompts import HAIKU

_MEMORY_CAP = 200                # per-creator memory cap (prune lowest-confidence/oldest beyond this)

# Insight/performance/episodic memory types are DROPPED in code (never stored).
_DROP_TYPES = frozenset({"successful_pattern", "conversation_insight", "episodic",
                         "performance", "insight"})

# Cheap cue-gate: only spend an LLM call on turns that plausibly contain a durable
# fact — an explicit memory cue, or a first-person preference/identity statement.
_CUES = ("remember", "keep in mind", "note that", "fyi", "for future", "don't forget",
         "i prefer", "i like", "i don't like", "i hate", "i want", "i always", "i never",
         "my name", "i'm based", "im based", "i live", "i'm a", "call me", "from now on",
         "always ", "never ", "no emojis", "in bullet")

_EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["memories"],
    "properties": {"memories": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["type", "key", "value", "confidence", "scope"],
        "properties": {
            "type": {"type": "string"}, "key": {"type": "string"},
            "value": {"type": "string"},
            "confidence": {"type": "number"}, "scope": {"type": "string"},
        }}}},
}

_OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
_EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")


def _should_extract(user_message: str) -> bool:
    m = (user_message or "").lower().strip()
    if not m:
        return False
    return any(c in m for c in _CUES)


# Shared loop-aware embed client (a fresh AsyncClient per call = a TLS handshake on the
# converse hot path). Timeout 5s: this is inline before the reply, so we can't wait long.
_embed_client: httpx.AsyncClient | None = None
_embed_loop = None


def _get_embed_client() -> httpx.AsyncClient:
    global _embed_client, _embed_loop
    loop = asyncio.get_running_loop()
    if _embed_client is None or _embed_loop is not loop:
        _embed_client = httpx.AsyncClient(timeout=5)
        _embed_loop = loop
    return _embed_client


async def aclose() -> None:
    """Close the pooled embed client on app shutdown (main._lifespan)."""
    global _embed_client
    if _embed_client is not None:
        try:
            await _embed_client.aclose()
        finally:
            _embed_client = None


async def _embed(text: str) -> list[float] | None:
    """OpenAI embedding for pgvector search. None keyless — retrieval then falls back
    to recency, and storage saves the memory without a vector (still retrievable)."""
    if not (_OPENAI_KEY and text.strip()):
        return None
    try:
        r = await _get_embed_client().post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {_OPENAI_KEY}"},
            json={"model": _EMBED_MODEL, "input": text[:8000]})
        if r.status_code == 200:
            return r.json()["data"][0]["embedding"]
    except Exception as e:
        logging.warning("[memory_v2] embed failed: %s", e)
    return None


async def _extract_facts(store, user_msg: str, assistant_msg: str) -> list[dict]:
    system, user = palo_prompts.memory_extract_prompt(user_msg, assistant_msg)
    from app.prompt_store import get_prompt
    system = await get_prompt("palo.memory.extract", system, store=store)
    data = await anthropic_cached_json(system, user, _EXTRACT_SCHEMA, HAIKU, max_tokens=800)
    if not isinstance(data, dict):
        return []
    out = []
    for m in data.get("memories", []) or []:
        if not isinstance(m, dict):
            continue
        if (m.get("type") or "").lower() in _DROP_TYPES:  # insights banned in code
            continue
        if m.get("value"):
            out.append(m)
    return out


def reconcile(existing: list[dict], candidates: list[dict]) -> list[dict]:
    """mem0-style, deterministic: match a candidate to an existing memory by
    (scope, type, key). Same key ⇒ UPDATE only if the value changed or confidence rose
    (else NOOP, so re-stating a fact doesn't churn the row). New key ⇒ ADD. Returns a
    list of {op, row} — 'update' rows carry the existing id."""
    index = {(e.get("scope", ""), e.get("type", ""), e.get("key", "")): e for e in existing}
    ops: list[dict] = []
    for c in candidates:
        k = (c.get("scope", "user"), c.get("type", ""), c.get("key", ""))
        prior = index.get(k)
        if prior is None:
            ops.append({"op": "add", "row": {
                "creator_id": "", "type": c.get("type", "content_context"),
                "key": c.get("key", ""), "value": c["value"],
                "confidence": float(c.get("confidence", 0.7) or 0.7),
                "scope": c.get("scope", "user")}})
        else:
            newer_value = (c.get("value") or "").strip() != (prior.get("value") or "").strip()
            higher_conf = float(c.get("confidence", 0) or 0) > float(prior.get("confidence", 0) or 0)
            if newer_value or higher_conf:
                ops.append({"op": "update", "row": {
                    "id": prior.get("id"), "value": c["value"],
                    "confidence": float(c.get("confidence", prior.get("confidence", 0.7))),
                    "updated_at": _now_iso()}})
    return ops


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def _prune(store, creator_id: str, existing: list[dict], ops: list[dict]) -> None:
    """Bound per-creator memory growth: soft-delete the lowest-confidence / oldest memories
    beyond _MEMORY_CAP. Best-effort; never raises."""
    try:
        total = len(existing) + sum(1 for o in ops if o.get("op") == "add")
        if total <= _MEMORY_CAP:
            return
        prunable = sorted(existing, key=lambda m: (float(m.get("confidence", 0) or 0),
                                                   m.get("updated_at") or ""))
        for m in prunable[:total - _MEMORY_CAP]:
            if m.get("id"):
                await store.soft_delete_memory(m["id"])
    except Exception as e:
        logging.warning("[memory_v2] prune failed: %s", e)


async def remember(store, creator_id: str, user_msg: str, assistant_msg: str,
                   scope_default: str = "user") -> int:
    """Fire-and-forget: extract → reconcile vs existing → upsert. Returns #ops applied
    (0 on gate-miss/keyless/no-store). Swallows all errors (never touches the hot path)."""
    try:
        if not palo_flags.enabled(palo_flags.MEMORY_V2) or store is None or not creator_id:
            return 0
        if not _should_extract(user_msg):
            return 0
        candidates = await _extract_facts(store, user_msg, assistant_msg)
        if not candidates:
            return 0
        existing = await store.load_memories(creator_id)
        ops = reconcile(existing, candidates)
        applied = 0
        for op in ops:
            row = op["row"]
            row["creator_id"] = creator_id
            if row.get("value"):          # embed on ADD *and* UPDATE (update left the vector stale)
                row["embedding"] = await _embed(row["value"])
            if await store.upsert_memory({k: v for k, v in row.items() if v is not None}):
                applied += 1
        await _prune(store, creator_id, existing, ops)
        await ai_usage.record(store, creator_id, "memory.extract", HAIKU, 600, 200)
        return applied
    except Exception as e:
        logging.warning("[memory_v2] remember failed: %s", e)
        return 0


def _rank(mems: list[dict]) -> list[dict]:
    """Weighted score 0.55*similarity + 0.25*confidence + 0.20*recency. `similarity`
    is present on RPC results (0 for the recency fallback); recency is the row's
    position in updated_at-desc order, normalized."""
    n = max(len(mems), 1)
    scored = []
    for i, m in enumerate(mems):
        sim = float(m.get("similarity", 0.0) or 0.0)
        conf = float(m.get("confidence", 0.7) or 0.7)
        recency = 1.0 - (i / n)
        scored.append((0.55 * sim + 0.25 * conf + 0.20 * recency, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored]


async def retrieve(store, creator_id: str, query: str, scope: str = "",
                   k: int = 5) -> list[dict]:
    if not palo_flags.enabled(palo_flags.MEMORY_V2) or store is None or not creator_id:
        return []
    # Never-raise: retrieve runs on the /v1/converse read path (before the route's try),
    # so a transient store error must degrade to no-memory, not 500 the turn.
    try:
        emb = await _embed(query)
        if emb is not None:
            mems = await store.match_memories(creator_id, emb, scope=scope, limit=k * 2)
        else:
            mems = await store.load_memories(creator_id, scope=scope)  # recency fallback
        return _rank(mems)[:k]
    except Exception as e:
        logging.warning("[memory_v2] retrieve failed: %s", e)
        return []


def memory_block(mems: list[dict]) -> str:
    """Render retrieved memories as the <memory> block injected into the prompt."""
    if not mems:
        return ""
    lines = [f"- {m.get('value', '').strip()}" for m in mems if m.get("value")]
    if not lines:
        return ""
    return "<memory>\nWhat you know about this creator (apply, don't recite):\n" + \
           "\n".join(lines) + "\n</memory>"
