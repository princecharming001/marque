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

import calendar
import logging
import re
import time

from app import ai_usage, dossier_adapter, palo_flags, palo_prompts, prompt_assembly, tiers
from app.palo_llm import anthropic_cached
from prompts import OPUS, SONNET

_REQUIRED = ("Insights", "Plan", "Buckets", "Brand Bets", "Not-Doing")
_COMPILE_INTERVAL_DAYS = {"weekly": 7.0, "biweekly": 14.0, "monthly": 30.0, "off": 0.0}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_to_epoch(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")))
    except Exception:
        return 0.0


def is_compile_due(tier: str, last_updated_iso: str | None, now_epoch: float) -> bool:
    """Freshness gate: has enough time passed for this tier's compile cadence? Never
    compiled ⇒ due; 'off' cadence ⇒ never."""
    interval = _COMPILE_INTERVAL_DAYS.get(tiers.cadence(tier, "compile"), 7.0)
    if interval <= 0:
        return False
    last = _iso_to_epoch(last_updated_iso)
    if not last:
        return True
    return (now_epoch - last) >= interval * 86400


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


async def strategy_block(store, creator_id: str) -> str:
    """The compiled strategy as an injectable prompt block for script gen + converse, so
    the brain actually shapes output. Flag-gated + keyless (no store / no strategy) => ''."""
    if not palo_flags.enabled(palo_flags.STRATEGY_COMPILER) or store is None or not creator_id:
        return ""
    try:
        strat = await store.load_strategy(creator_id)
    except Exception:
        strat = None
    md = (strat or {}).get("strategy_markdown", "") if strat else ""
    if not md.strip():
        return ""
    return ("<creator_strategy>\nApply this compiled strategy to shape the output "
            "(apply, don't recite):\n" + md.strip() + "\n</creator_strategy>")


async def run_compile_cron(store, now_epoch: float) -> int:
    """Weekly sweep: compile the strategy for each creator that is allowlisted AND whose
    tier-cadence freshness window has elapsed. The three gates (flag + allowlist + freshness)
    keep the Opus bill bounded. Returns #compiled. Flag-gated + keyless no-op."""
    if not palo_flags.enabled(palo_flags.STRATEGY_COMPILER) or store is None:
        return 0
    compiled = 0
    for c in await store.load_all_creators():
        cid = c.get("creator_id")
        if not cid:
            continue
        if not ai_usage.compile_allowed(cid, True):            # allowlist gate (cheap, first)
            continue
        tier = await tiers.tier_for(cid, store)
        prev = await store.load_strategy(cid) or {}
        if not is_compile_due(tier, prev.get("strategy_updated_at"), now_epoch):  # freshness
            continue
        # Videos (dossier list) are a data hookup from the creator's analyzed reels; [] here
        # falls back to the template strategy until that source is wired.
        if await compile_strategy(store, cid, [], {"niche": c.get("niche", "")}):
            compiled += 1
    return compiled
