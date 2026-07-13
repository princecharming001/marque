"""Phase 6 (box 1) — exemplar bank retrieval / index (ported from Palo exemplar_bank).

An "exemplar bank" is a per-creator library of golden CRAFT patterns — proven hook /
builder / rhythm / payoff moves distilled from what performs in their niche, each with a
mechanism, a lift score, and example lines. Retrieval renders a compact lift-ordered
INDEX into the write/idea prompts (so generation draws on proven moves), and dereferences
a pattern id to its full card on demand.

This box is retrieval/index — it works against a HAND-SEEDED bank in
channel_strategies.exemplar_bank (JSONB); the 5-Opus build + daily refresh is box 2.
Flag EXEMPLAR_BANK; keyless / empty bank ⇒ "" (generation runs without it, unchanged).
"""
from __future__ import annotations

from app import palo_flags

_CATEGORIES = ("hook", "builder", "rhythm", "payoff")


async def _bank(store, creator_id: str) -> dict:
    if store is None or not creator_id:
        return {}
    try:
        strat = await store.load_strategy(creator_id)
    except Exception:
        return {}
    return (strat or {}).get("exemplar_bank") or {}


def _flatten(bank: dict) -> list[dict]:
    """All patterns across categories, each tagged with its category, lift-ordered."""
    out: list[dict] = []
    for cat in _CATEGORIES:
        for p in bank.get(cat, []) or []:
            if isinstance(p, dict) and p.get("id"):
                out.append({**p, "category": cat, "lift": float(p.get("lift", 0) or 0)})
    out.sort(key=lambda p: p["lift"], reverse=True)
    return out


async def load_index(store, creator_id: str) -> list[dict]:
    """Lift-ordered compact index: {id, category, mechanism, lift}. Empty keyless / no bank."""
    if not palo_flags.enabled(palo_flags.EXEMPLAR_BANK):
        return []
    return [{"id": p["id"], "category": p["category"], "lift": p["lift"],
             "mechanism": p.get("mechanism", "")} for p in _flatten(await _bank(store, creator_id))]


def render_index(index: list[dict], limit: int = 8) -> str:
    lines = [f"[{p['category']}:{p['id']}] lift {p['lift']:.1f} — {p.get('mechanism', '')}"
             for p in index[:limit] if p.get("mechanism")]
    return "\n".join(lines)


async def exemplar_block(store, creator_id: str, limit: int = 8) -> str:
    """The injectable index block for write/idea prompts. Flag-gated + keyless ⇒ ''."""
    if not palo_flags.enabled(palo_flags.EXEMPLAR_BANK) or store is None or not creator_id:
        return ""
    idx = await load_index(store, creator_id)
    body = render_index(idx, limit)
    if not body:
        return ""
    return ("<exemplar_patterns>\nProven craft patterns in this niche — reference the "
            "MECHANISM, never copy the examples verbatim:\n" + body + "\n</exemplar_patterns>")


async def dereference(store, creator_id: str, ids: list[str]) -> list[dict]:
    """Full pattern cards (mechanism + example lines) for the given ids — the exemplar_tool
    the agent calls when it wants the detail behind an index entry."""
    if not palo_flags.enabled(palo_flags.EXEMPLAR_BANK):
        return []
    by_id = {p["id"]: p for p in _flatten(await _bank(store, creator_id))}
    return [by_id[i] for i in ids if i in by_id]
