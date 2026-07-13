"""Phase 2 (box 1) — idea bank: onboarding idea generation + eval gate → briefs.

Ported from Palo onboarding_agent/idea_generation.py + idea_eval.py. Generates 3
niche-specific video ideas (safest bet / creative stretch / high ceiling) by adapting
proven exemplar structures, then a cheap HAIKU eval gate drops any idea with zero
niche connection (Palo's guard against the "Minecraft creator gets a morning-routine
idea" failure). Survivors become `briefs` rows the feed reads from.

Keyless-green: no key ⇒ deterministic mock ideas + pass-through eval; no store ⇒ ideas
returned but not persisted. Flag IDEA_BANK gates the on-demand entry point.
"""
from __future__ import annotations

import logging

from app import ai_usage, palo_flags, palo_prompts
from app.palo_llm import anthropic_cached_json
from app.prompt_store import get_prompt
from app.recall_ledger import new_ulid
from prompts import HAIKU, SONNET

_IDEASET_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ideas"],
    "properties": {
        "ideas": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "content"],
            "properties": {"title": {"type": "string"}, "content": {"type": "string"}}}},
        "justification": {"type": "string"}},
}

_EVAL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["results"],
    "properties": {"results": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["idea_index", "pass"],
        "properties": {"idea_index": {"type": "integer"}, "pass": {"type": "boolean"},
                       "reason": {"type": "string"}}}}},
}


def _context_from_brand(brand: dict) -> tuple[str, str, str, str]:
    """(creator_signals, channel_identity, topic, format) from Marque's Brand dict."""
    niche = (brand.get("niche") or "").strip()
    signals = "; ".join(x for x in [
        f"niche: {niche}" if niche else "",
        f"known for: {brand.get('known_for', '')}" if brand.get("known_for") else "",
        f"catchphrases: {', '.join(brand.get('catchphrases', []))}" if brand.get("catchphrases") else "",
    ] if x)
    identity = "; ".join(x for x in [
        f"audience: {brand.get('audience', '')}" if brand.get("audience") else "",
        f"voice: {brand.get('voice', '')}" if brand.get("voice") else "",
        f"platform: {brand.get('primary_platform', '')}" if brand.get("primary_platform") else "",
    ] if x)
    fmt = brand.get("primary_platform") or brand.get("camera_comfort") or "short-form"
    return signals or "(none)", identity or "(none)", niche or "content", fmt


def mock_ideas(brand: dict) -> list[dict]:
    niche = (brand.get("niche") or "your niche").strip()
    return [
        {"title": f"I Tried the Most-Watched {niche} Format for 7 Days",
         "content": f"Open on the setup every {niche} viewer recognizes. Escalate one constraint each day. End on the before/after. Film with your phone."},
        {"title": f"The {niche} Mistake Everyone Makes (I Tested It)",
         "content": f"Hook with the common belief. Run the experiment on camera. Reveal what actually happened. One take, talking to camera."},
        {"title": f"What 100 Hours of {niche} Taught Me",
         "content": f"Fast montage of the grind. Land three counterintuitive lessons. Close on the one that breaks out of {niche}. B-roll heavy."},
    ]


async def generate_ideas(store, brand: dict, exemplars: str = "",
                         knowledge: str = "basic", creator_id: str = "") -> list[dict]:
    signals, identity, _topic, _fmt = _context_from_brand(brand)
    base_sys, user = palo_prompts.idea_generation_prompt(signals, identity, exemplars, knowledge)
    system = await get_prompt("palo.idea.generate", base_sys, store=store)
    data = await anthropic_cached_json(system, user, _IDEASET_SCHEMA, SONNET, max_tokens=1400)
    if not isinstance(data, dict) or not data.get("ideas"):
        return mock_ideas(brand)                       # keyless / failure fallback
    ideas = [{"title": i.get("title", ""), "content": i.get("content", "")}
             for i in data["ideas"] if i.get("title")][:3]
    if len(ideas) < 3:
        return mock_ideas(brand)
    await ai_usage.record(store, creator_id, "idea.generate", SONNET, 3000, 900)
    return ideas


async def eval_ideas(store, ideas: list[dict], topic: str, fmt: str,
                     creator_id: str = "") -> list[bool]:
    """Per-idea pass flags. Keyless ⇒ all pass (never drop ideas we can't judge)."""
    if not ideas:
        return []
    base_sys, user = palo_prompts.idea_eval_prompt(topic, fmt, ideas)
    system = await get_prompt("palo.idea.eval", base_sys, store=store)
    data = await anthropic_cached_json(system, user, _EVAL_SCHEMA, HAIKU, max_tokens=500)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return [True] * len(ideas)
    verdict = {r.get("idea_index"): bool(r.get("pass", True)) for r in data["results"]}
    await ai_usage.record(store, creator_id, "idea.eval", HAIKU, 700, 200)
    # idea_index is 1-based in the prompt; default to pass if the judge omitted one.
    return [verdict.get(i + 1, verdict.get(i, True)) for i in range(len(ideas))]


def to_briefs(creator_id: str, ideas: list[dict], source: str = "onboarding") -> list[dict]:
    briefs = []
    for i, idea in enumerate(ideas):
        briefs.append({
            "id": new_ulid(), "creator_id": creator_id, "source": source,
            "title": idea.get("title", ""), "summary": idea.get("content", ""),
            "beginning": "", "middle": "", "ending": "",
            "score": round(1.0 - i * 0.1, 3), "status": "new",
        })
    return briefs


async def suggest_ideas(store, creator_id: str, brand: dict, source: str = "onboarding",
                        exemplars: str = "") -> list[dict]:
    """Full pipeline: generate → eval-filter → briefs → persist → return. Flag-gated.
    Never returns empty when generation produced ideas (keeps the top idea if the gate
    would drop them all). Swallows persistence errors."""
    if not palo_flags.enabled(palo_flags.IDEA_BANK):
        return []
    try:
        _, _, topic, fmt = _context_from_brand(brand)
        ideas = await generate_ideas(store, brand, exemplars, creator_id=creator_id)
        passes = await eval_ideas(store, ideas, topic, fmt, creator_id=creator_id)
        kept = [idea for idea, ok in zip(ideas, passes) if ok] or ideas[:1]
        briefs = to_briefs(creator_id, kept, source)
        if store is not None:
            for b in briefs:
                try:
                    await store.upsert_brief(b)
                except Exception as e:
                    logging.warning("[ideas] upsert_brief failed: %s", e)
        return briefs
    except Exception as e:
        logging.warning("[ideas] suggest_ideas failed: %s", e)
        return []
