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
import hashlib
import json
import logging
import re
import time

from app import ai_usage, dossier_adapter, palo_flags, palo_prompts, prompt_assembly, tiers
from app.palo_llm import anthropic_cached
from prompts import OPUS, SONNET

_REQUIRED = ("Insights", "Plan", "Buckets", "Brand Bets", "Not-Doing")
_COMPILE_INTERVAL_DAYS = {"weekly": 7.0, "biweekly": 14.0, "monthly": 30.0, "off": 0.0}

# B3 (superintelligence epic): brand-edit staleness detection. Duplicated (not imported)
# from main._brand_hash — strategy_compiler is imported BY main.py, so the reverse import
# would be circular. Both sides MUST hash the exact same field set to agree.
_BRAND_HASH_FIELDS = ("niche", "audience", "known_for", "what_you_do", "goal", "voice",
                     "catchphrases", "non_negotiables", "emulation_targets")
_RECOMPILE_DEBOUNCE_S = 24 * 3600


def _brand_hash(brand: dict) -> str:
    if not brand:
        return ""
    try:
        payload = {k: brand.get(k) for k in _BRAND_HASH_FIELDS}
        return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]
    except (TypeError, ValueError):
        return ""


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


# A fixed line the deterministic template always carries and a real synthesized strategy
# never reproduces verbatim — lets the API flag a not-ready/template doc even for rows
# written before strategy_footnotes carried the explicit "template" marker.
_TEMPLATE_SENTINEL = "LEVER: escape the current view band by sharpening the first 3 seconds"


def is_template_markdown(md: str | None) -> bool:
    """True when the strategy doc is the deterministic placeholder (no real analyzed
    videos yet), not a compiled-from-evidence strategy. The app shows a simple 'not ready'
    state for these instead of rendering the generic template as if it were real."""
    return bool(md) and _TEMPLATE_SENTINEL in md


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


def _strip_reasoning(md: str) -> str:
    """The synthesis prompt does its cognitive pass in a <reasoning> block (Palo parity:
    stage → confound checks → lever). It is scratch work — discard it so it is never
    persisted into strategy_markdown nor shown in the app's Strategy sheet."""
    return re.sub(r"<reasoning>.*?</reasoning>", "", md, flags=re.DOTALL).strip()


async def synthesize(store, creator_id: str, digest_text: str, brand: dict | None) -> str:
    system, user = palo_prompts.strategy_synthesis_prompt(digest_text, brand)
    system = prompt_assembly.replace_doctrine_blocks(system)   # fill {DOCTRINE_CORE} in cached prefix
    from app.prompt_store import get_prompt
    system = await get_prompt("palo.strategy.synthesis", system, store=store)
    # 4000 (was 2500): the reasoning pass spends tokens BEFORE the artifact — a tight cap
    # truncated the artifact mid-section, failed validation, and silently shipped the
    # template instead of the strategy the Opus call was billed for.
    out = await anthropic_cached(system, user, OPUS, max_tokens=4000)
    if out:
        out = _strip_reasoning(out)
        # Bill the Opus call whenever it ran — even if the sections fail validation and we
        # fall back to the template. Billing only on the valid path let the priciest model
        # go unmetered on malformed output (the exact thing ai_usage exists to catch).
        await ai_usage.record(store, creator_id, "strategy.synthesis", OPUS, 30000, 1600)
        if validate_sections(out):
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
        if not evidence.strip():
            # No real analyzed videos yet — write the deterministic template WITHOUT spending
            # ~$1.60 of Sonnet+Opus to produce a generic strategy from "(no videos)".
            md = _template_strategy(brand)
        else:
            dg = await digest(store, creator_id, evidence, brand)
            md = await synthesize(store, creator_id, dg, brand)
            if not validate_sections(md):                      # never persist an unusable doc
                md = _template_strategy(brand)
        prev = await store.load_strategy(creator_id) or {}
        rev = int(prev.get("strategy_revision", 0) or 0) + 1
        await store.upsert_strategy(creator_id, {
            "strategy_markdown": md, "strategy_revision": rev,
            # Explicit not-ready marker so the app shows a simple "still forming" state
            # instead of rendering the generic placeholder as a real strategy. The API
            # also content-detects the template (is_template_markdown) as a fallback for
            # rows written before this flag existed.
            "strategy_footnotes": "template" if is_template_markdown(md) else "",
            "strategy_updated_at": _now_iso(), "brand_hash": _brand_hash(brand or {})})
        return md
    except Exception as e:
        logging.warning("[strategy_compiler] compile failed: %s", e)
        return None


async def maybe_recompile_on_brand_edit(store, creator_id: str, brand: dict) -> None:
    """B3: a niche pivot / voice edit leaves the compiled strategy stale until the next
    weekly/biweekly cron sweep — up to 30 days of the brain shaping output around the OLD
    brand. On a brand-hash mismatch (AND compile_allowed AND a 24h debounce so a burst of
    edits doesn't spam Opus), kick a background recompile. Fire-and-forget; never raises."""
    if not palo_flags.enabled(palo_flags.STRATEGY_COMPILER) or store is None or not creator_id or not brand:
        return
    new_hash = _brand_hash(brand)
    if not new_hash:
        return
    try:
        prev = await store.load_strategy(creator_id) or {}
    except Exception:
        return
    if prev.get("brand_hash") == new_hash:
        return   # already compiled against this brand
    last_compiled = _iso_to_epoch(prev.get("strategy_updated_at"))
    if last_compiled and (time.time() - last_compiled) < _RECOMPILE_DEBOUNCE_S:
        return   # debounce: don't spam Opus on a burst of brand edits
    if not ai_usage.compile_allowed(creator_id, True):
        return
    try:
        loader = getattr(store, "load_clip_sessions", None)
        sessions = await loader(creator_id) if loader else []
        videos = dossier_adapter.videos_from_clip_sessions(sessions)
        await compile_strategy(store, creator_id, videos, brand)
    except Exception as e:
        logging.warning("[strategy_compiler] brand-edit recompile failed for %s: %s", creator_id, e)


async def strategy_block(store, creator_id: str, brand_hash: str | None = None) -> str:
    """The compiled strategy as an injectable prompt block for script gen + converse, so
    the brain actually shapes output. Flag-gated + keyless (no store / no strategy) => ''.
    `brand_hash` (B3, optional): the CALLER's current brand hash — if it mismatches what
    the strategy was actually compiled against, append an honest staleness note so the
    model knows the live Creator brand block wins over anything conflicting here."""
    if not palo_flags.enabled(palo_flags.STRATEGY_COMPILER) or store is None or not creator_id:
        return ""
    try:
        strat = await store.load_strategy(creator_id)
    except Exception:
        strat = None
    md = (strat or {}).get("strategy_markdown", "") if strat else ""
    if not md.strip():
        return ""
    stale_note = ""
    if brand_hash and strat and strat.get("brand_hash") and strat["brand_hash"] != brand_hash:
        stale_note = ("\n\nNOTE: the creator's brand changed after this strategy was "
                      "compiled — where they conflict, the Creator brand block wins.")
    return ("<creator_strategy>\nApply this compiled strategy to shape the output "
            "(apply, don't recite):\n" + md.strip() + stale_note + "\n</creator_strategy>")


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
        # Feed the creator's REAL analyzed videos (clip_edit_sessions dossiers) as evidence.
        loader = getattr(store, "load_clip_sessions", None)
        sessions = await loader(cid) if loader else []
        videos = dossier_adapter.videos_from_clip_sessions(sessions)
        if await compile_strategy(store, cid, videos, {"niche": c.get("niche", "")}):
            compiled += 1
    return compiled
