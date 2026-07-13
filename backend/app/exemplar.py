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

import calendar
import logging
import time

from app import ai_usage, dossier_adapter, palo_flags, palo_prompts
from app.palo_llm import anthropic_cached_json
from prompts import OPUS

_CATEGORIES = ("hook", "builder", "rhythm", "payoff")

_PATTERN = {
    "type": "object", "additionalProperties": False, "required": ["id", "mechanism"],
    "properties": {"id": {"type": "string"}, "mechanism": {"type": "string"},
                   "lift": {"type": "number"},
                   "examples": {"type": "array", "items": {"type": "string"}}},
}
_BANK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {c: {"type": "array", "items": _PATTERN} for c in _CATEGORIES},
}
_REFRESH_INTERVAL_DAYS = 30.0


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


# --- build + refresh decider (box 2) ------------------------------------------
def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_to_epoch(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")))
    except Exception:
        return 0.0


def _template_bank(brand: dict | None) -> dict:
    niche = (brand or {}).get("niche", "your niche")
    return {
        "hook": [{"id": "h_question", "mechanism": "open with a question the viewer needs answered",
                  "lift": 1.5, "examples": []}],
        "builder": [{"id": "b_escalate", "mechanism": "raise the stakes each beat", "lift": 1.4, "examples": []}],
        "rhythm": [{"id": "r_tight", "mechanism": "cut on every complete thought, no dead air", "lift": 1.3, "examples": []}],
        "payoff": [{"id": "p_decisive", "mechanism": f"resolve the {niche} promise in-video", "lift": 1.6, "examples": []}],
    }


def _valid_bank(data) -> bool:
    return isinstance(data, dict) and any(
        isinstance(data.get(c), list) and data.get(c) for c in _CATEGORIES)


async def build_bank(store, creator_id: str, videos: list[dict],
                     brand: dict | None = None) -> dict | None:
    """Extract golden patterns from the creator's videos (via the dossier adapter) with
    Opus, and persist to channel_strategies.exemplar_bank (revision+1). Gated by flag AND
    compile_allowed (Opus cost). Keyless / thin catalog ⇒ template bank. None when gated off."""
    if not palo_flags.enabled(palo_flags.EXEMPLAR_BANK) or store is None or not creator_id:
        return None
    if not ai_usage.compile_allowed(creator_id, True):        # Opus cost gate (allowlist)
        return None
    try:
        evidence = dossier_adapter.catalog_block(videos or [])
        system, user = palo_prompts.exemplar_build_prompt(evidence, brand)
        from app.prompt_store import get_prompt
        system = await get_prompt("palo.exemplar.build", system, store=store)
        data = await anthropic_cached_json(system, user, _BANK_SCHEMA, OPUS, max_tokens=2000)
        if _valid_bank(data):
            await ai_usage.record(store, creator_id, "exemplar.build", OPUS, 20000, 1500)
            bank = {c: data.get(c, []) for c in _CATEGORIES}
        else:
            bank = _template_bank(brand)
        prev = await store.load_strategy(creator_id) or {}
        rev = int(prev.get("exemplar_bank_revision", 0) or 0) + 1
        await store.upsert_strategy(creator_id, {
            "exemplar_bank": bank, "exemplar_bank_revision": rev,
            "exemplar_bank_built_at": _now_iso()})
        return bank
    except Exception as e:
        logging.warning("[exemplar] build_bank failed: %s", e)
        return None


async def should_rebuild(store, creator_id: str, now_epoch: float,
                         interval_days: float = _REFRESH_INTERVAL_DAYS) -> bool:
    """Daily refresh decider: rebuild if never built or the freshness window elapsed. Cheap
    (one strategy read, no LLM) — the SCAN step before the expensive Opus build."""
    if store is None:
        return False
    strat = await store.load_strategy(creator_id) or {}
    built = _iso_to_epoch(strat.get("exemplar_bank_built_at"))
    if not built:
        return True
    return (now_epoch - built) >= interval_days * 86400


async def run_exemplar_cron(store, now_epoch: float) -> int:
    """Daily sweep: rebuild the bank for allowlisted creators whose freshness window
    elapsed. Flag + allowlist + freshness bound the Opus spend. Returns #rebuilt."""
    if not palo_flags.enabled(palo_flags.EXEMPLAR_BANK) or store is None:
        return 0
    built = 0
    for c in await store.load_all_creators():
        cid = c.get("creator_id")
        if not cid or not ai_usage.compile_allowed(cid, True):
            continue
        if not await should_rebuild(store, cid, now_epoch):
            continue
        if await build_bank(store, cid, [], {"niche": c.get("niche", "")}):
            built += 1
    return built
