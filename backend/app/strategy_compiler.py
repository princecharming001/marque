"""Phase 4 (box 2) — the strategy compiler / brain (ported from Palo strategy/compiler.py).

Two-pass: Sonnet digests the metrics-ranked evidence pack (dossier_adapter.catalog_block)
into a tight evidence digest, then Opus synthesizes the compiled strategy_markdown — the
one artifact every downstream prompt reads (via prompt_assembly.{STRATEGY_*}). The Opus
call caches the doctrine prefix (<<<CACHE_BREAKPOINT>>>) to hold cost down.

Persisted to channel_strategies with a monotonic revision (UPSERT). Keyless-green: both
passes fall back to a deterministic template strategy that still carries all five sections
+ REGIME/LEVER, so the whole downstream (write/idea/converse) works with no key. Gated by
the STRATEGY_COMPILER flag AND ai_usage.compile_allowed (allowlist default empty).
"""
from __future__ import annotations

import logging
import re
import time

from app import ai_usage, dossier_adapter, palo_flags, palo_prompts, prompt_assembly
from app.palo_llm import anthropic_cached
from prompts import OPUS, SONNET

_REQUIRED = ("Insights", "Plan", "Buckets", "Brand Bets", "Not-Doing")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def split_sections(md: str) -> dict[str, str]:
    """`## Name` blocks -> {name: body}. The compiler's structural contract."""
    out: dict[str, str] = {}
    for m in re.finditer(r"(?ms)^##\s+(?P<name>.+?)\s*$\n(?P<body>.*?)(?=^##\s|\Z)", md or ""):
        out[m.group("name").strip()] = m.group("body").strip()
    return out


def validate_sections(md: str) -> bool:
    sections = split_sections(md)
    return all(name in sections for name in _REQUIRED)


def _template_strategy(brand: dict | None) -> str:
    niche = ((brand or {}).get("niche") or "your niche").strip()
    return (f"## Insights\n- {niche} videos that open with a specific, curiosity-gap hook "
            f"hold attention longest.\n- Decisive payoffs (resolve in-video) outperform "
            f"cliffhangers.\n\n## Plan\nREGIME: sub-breakout\nLEVER: escape the current view "
            f"band by sharpening the first 3 seconds\nPriority: post {niche} content "
            f"consistently and lead with the hook.\n\n## Buckets\n- day-in-the-life\n- "
            f"how-to / tutorial\n- reaction / opinion\n\n## Brand Bets\n- a signature "
            f"recurring format the audience recognizes\n\n## Not-Doing\n- chasing off-niche "
            f"trends that dilute the {niche} identity\n")


async def digest(store, creator_id: str, evidence: str, brand: dict | None) -> str:
    system, user = palo_prompts.strategy_digest_prompt(evidence, brand)
    from app.prompt_store import get_prompt
    system = await get_prompt("palo.strategy.digest", system, store=store)
    out = await anthropic_cached(system, user, SONNET, max_tokens=1200)
    if out:
        await ai_usage.record(store, creator_id, "strategy.digest", SONNET, 8000, 1000)
        return out
    return f"Catalog digest unavailable; using baseline {(brand or {}).get('niche', 'niche')} craft priors."


async def synthesize(store, creator_id: str, digest_text: str, brand: dict | None) -> str:
    system, user = palo_prompts.strategy_synthesis_prompt(digest_text, brand)
    system = prompt_assembly.replace_doctrine_blocks(system)   # fill {DOCTRINE_CORE} in cached prefix
    from app.prompt_store import get_prompt
    system = await get_prompt("palo.strategy.synthesis", system, store=store)
    out = await anthropic_cached(system, user, OPUS, max_tokens=2500)
    if out and validate_sections(out):
        await ai_usage.record(store, creator_id, "strategy.synthesis", OPUS, 30000, 1600)
        return out
    return _template_strategy(brand)


async def compile_strategy(store, creator_id: str, videos: list[dict],
                           brand: dict | None = None, is_paying: bool = True) -> str | None:
    """Full compile: evidence pack → digest → synthesis → validate → UPSERT (revision+1).
    Gated by STRATEGY_COMPILER flag AND compile_allowed (allowlist). Returns the compiled
    strategy_markdown, or None when gated off. Never raises."""
    if not palo_flags.enabled(palo_flags.STRATEGY_COMPILER) or store is None or not creator_id:
        return None
    if not ai_usage.compile_allowed(creator_id, is_paying):   # allowlist + paying gate
        return None
    try:
        evidence = dossier_adapter.catalog_block(videos or [])
        dg = await digest(store, creator_id, evidence, brand)
        md = await synthesize(store, creator_id, dg, brand)
        if not validate_sections(md):                          # never persist an unusable doc
            md = _template_strategy(brand)
        prev = await store.load_strategy(creator_id) or {}
        rev = int(prev.get("strategy_revision", 0) or 0) + 1
        await store.upsert_strategy(creator_id, {
            "strategy_markdown": md, "strategy_revision": rev,
            "strategy_updated_at": _now_iso()})
        return md
    except Exception as e:
        logging.warning("[strategy_compiler] compile failed: %s", e)
        return None
