"""Phase 1 — recommendation ledger (ported from Palo recall/ledger.py).

Append-only record of what the assistant PROPOSED / DECIDED / JUDGED, so it never
re-pitches the same idea and can answer "you suggested that 2 days ago". Distinct from
memory, which supersedes on contradiction (fatal for a ledger — the ledger must retain
contradictions). Write fire-and-forget after the turn; read one recency-ranked query,
injected as <prior_recommendations>.

Keyless-green: no key ⇒ extraction returns []; no store ⇒ no-op.
"""
from __future__ import annotations

import logging
import os
import time

from app import ai_usage, palo_flags, palo_prompts
from app.palo_llm import anthropic_cached_json
from prompts import HAIKU

_VALID_KINDS = {"idea", "script", "outline", "verdict", "decision"}

# Crockford base32 ULID (stdlib only) — matches Palo's channel_strategies id format.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    n = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[n & 0x1F])
        n >>= 5
    return "".join(reversed(out))


_LEDGER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["recs"],
    "properties": {"recs": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["kind", "summary"],
        "properties": {
            "kind": {"type": "string"}, "summary": {"type": "string"},
            "verdict": {"type": "string"}, "score": {"type": "integer"},
        }}}},
}


async def _extract(store, user_msg: str, assistant_msg: str) -> list[dict]:
    system, user = palo_prompts.ledger_extract_prompt(user_msg, assistant_msg)
    from app.prompt_store import get_prompt
    system = await get_prompt("palo.ledger.extract", system, store=store)
    data = await anthropic_cached_json(system, user, _LEDGER_SCHEMA, HAIKU, max_tokens=700)
    if not isinstance(data, dict):
        return []
    out = []
    for r in data.get("recs", []) or []:
        if isinstance(r, dict) and r.get("kind") in _VALID_KINDS and r.get("summary"):
            out.append({"kind": r["kind"], "summary": r["summary"][:200]})
    return out


async def record(store, creator_id: str, user_msg: str, assistant_msg: str,
                 conversation_id: str = "") -> int:
    """Fire-and-forget: extract the assistant's proposals and APPEND to the ledger.
    Returns #rows appended (0 on gate-miss/keyless/no-store). Swallows all errors."""
    try:
        if not palo_flags.enabled(palo_flags.MEMORY_V2) or store is None or not palo_flags.real_creator(creator_id):
            return 0
        if not assistant_msg:
            return 0
        recs = await _extract(store, user_msg, assistant_msg)
        if not recs:
            return 0
        entries = [{"conversation_id": conversation_id, "kind": r["kind"],
                    "summary": r["summary"]} for r in recs]
        if await store.append_ledger(creator_id, entries):
            await ai_usage.record(store, creator_id, "ledger.extract", HAIKU, 500, 150)
            return len(entries)
        return 0
    except Exception as e:
        logging.warning("[recall_ledger] record failed: %s", e)
        return 0


async def ledger_block(store, creator_id: str, limit: int = 25) -> str:
    """Render recent proposals/decisions as the <prior_recommendations> block, so the
    agent doesn't re-pitch. Empty string when off/empty."""
    if not palo_flags.enabled(palo_flags.MEMORY_V2) or store is None or not palo_flags.real_creator(creator_id):
        return ""
    # Never-raise: runs on the /v1/converse read path before the route's try.
    try:
        rows = await store.load_ledger(creator_id, limit=limit)
    except Exception as e:
        logging.warning("[recall_ledger] ledger_block failed: %s", e)
        return ""
    if not rows:
        return ""
    lines = [f"- [{r.get('kind', 'idea')}] {r.get('summary', '').strip()}"
             for r in rows if r.get("summary")]
    if not lines:
        return ""
    return ("<prior_recommendations>\nAlready proposed to this creator — do NOT re-pitch "
            "these; build on or diverge from them:\n" + "\n".join(lines) +
            "\n</prior_recommendations>")
