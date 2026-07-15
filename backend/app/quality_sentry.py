"""T3 (superintelligence epic) — daily prod-sampling quality cron. Prod generations
aren't persisted anywhere (the feed cache is in-memory, wiped on every deploy) — this
is the only honest way to sample what real creators are actually seeing: re-generate
through the REAL production code paths for a rotating slice of the real creator
roster, using their ACTUAL stored profile+posts (identical inputs, identical code to
what they'd get from a live request).

generate_fast/generate_full are INJECTED callables (not imported from main.py) —
this module lives under app/ and main.py imports it, so a reverse import would be
circular (same reasoning as retention.py's sfx_assets parameter, and the existing
settle_hook injection pattern in track_insights.run_insights_cron). Each callable is
`async (creator_id, brand, posts) -> list[dict]` returning script dicts shaped for
eval.invariants.evaluate_batch.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone

from eval.invariants import evaluate_batch
import prompts

QUALITY_CRON_MAX_CREATORS_DEFAULT = 5
QUALITY_CRON_FULL_PIPELINE_N_DEFAULT = 1

MIN_GATE_PASS_RATE = 0.90
MIN_RELEVANCE_MEAN = 60.0


def _rotated_roster(creators: list[dict], now_epoch: float, max_creators: int) -> list[dict]:
    """Deterministic day-of-year rotation over creators with a real (non-empty)
    niche — a bare `creators` row with no niche never actually onboarded. Pure,
    keyless-testable."""
    real = [c for c in creators if (c.get("creator_id") or "") and (c.get("niche") or "").strip()]
    if not real:
        return []
    real = sorted(real, key=lambda c: c["creator_id"])
    day_index = int(now_epoch // 86400)
    start = day_index % len(real)
    return (real[start:] + real[:start])[:max_creators]


def breached(card: dict) -> list[str]:
    """Pure — breach reasons for one scorecard row, or [] if healthy."""
    reasons = []
    if card.get("speakability_violations", 0) > 0:
        reasons.append(f"speakability_violations={card['speakability_violations']}")
    if card.get("gate_pass_rate") is not None and card["gate_pass_rate"] < MIN_GATE_PASS_RATE:
        reasons.append(f"gate_pass_rate={card['gate_pass_rate']:.2f}")
    rel = card.get("relevance_mean")
    if rel is not None and rel < MIN_RELEVANCE_MEAN:
        reasons.append(f"relevance_mean={rel}")
    return reasons


def _score(creator_id: str, path: str, scripts: list[dict], brand: dict, day) -> dict:
    card = evaluate_batch(scripts, brand)
    violations = sum(len(prompts.speakability_report(s).get("violations", [])) for s in scripts)
    row = {"day": day.isoformat(), "creator_id": creator_id, "path": path, "n": card["n"],
          "gate_pass_rate": card["gate_pass_rate"], "speakability_violations": violations,
          "relevance_mean": None, "voice_match_mean": None, "judge": {}}
    row["breach"] = bool(breached(row))
    return row


async def run_quality_cron(store, now_epoch: float, generate_fast, generate_full=None) -> int:
    """Daily. Returns the number of scorecard rows written. Never raises — every
    creator/path is wrapped so one bad generation can't kill the whole sweep."""
    if store is None:
        return 0
    max_creators = int(os.environ.get("QUALITY_CRON_MAX_CREATORS", str(QUALITY_CRON_MAX_CREATORS_DEFAULT)))
    full_n = int(os.environ.get("QUALITY_CRON_FULL_PIPELINE_N", str(QUALITY_CRON_FULL_PIPELINE_N_DEFAULT)))
    try:
        creators = await store.load_all_creators()
    except Exception as e:
        logging.warning("[quality-cron] load_all_creators failed: %s", e)
        creators = []
    roster = _rotated_roster(creators or [], now_epoch, max_creators)
    if not roster:
        return 0

    day = datetime.fromtimestamp(now_epoch, tz=timezone.utc).date()
    rows_written = 0
    breaches: list[dict] = []
    for i, c in enumerate(roster):
        creator_id = c["creator_id"]
        try:
            profile = await store.load_creator_profile(creator_id)
            brand = (profile or {}).get("brand") or {
                k: c.get(k) for k in ("niche", "audience", "known_for", "what_you_do", "goal")
                if c.get(k)}
            posts = await store.load_creator_posts(creator_id) or []

            fast_scripts = await generate_fast(creator_id, brand, posts)
            row = _score(creator_id, "feed_fast", fast_scripts, brand, day)
            if await store.insert_quality_scorecard(row):
                rows_written += 1
            if row["breach"]:
                breaches.append(row)

            # One rotating creator/day also gets the full judged pipeline (Opus cost cap).
            if generate_full is not None and full_n > 0 and (day.toordinal() + i) % max(1, len(roster)) < full_n:
                full_scripts = await generate_full(creator_id, brand, posts)
                full_row = _score(creator_id, "scripts_full", full_scripts, brand, day)
                if await store.insert_quality_scorecard(full_row):
                    rows_written += 1
                if full_row["breach"]:
                    breaches.append(full_row)
        except Exception as e:
            logging.warning("[quality-cron] creator=%s failed: %s", creator_id, e)

    if breaches:
        await alert(breaches)
    return rows_written


async def alert(breaches: list[dict]) -> None:
    """(1) a grep-able error log line — always. (2) one APNs push to the owner, if
    push is configured and OWNER_CREATOR_ID is set. No new alerting service."""
    for row in breaches:
        logging.error("[quality-alert] creator=%s path=%s gate=%s speak_violations=%s relevance=%s",
                      row.get("creator_id"), row.get("path"), row.get("gate_pass_rate"),
                      row.get("speakability_violations"), row.get("relevance_mean"))
    owner = os.environ.get("OWNER_CREATOR_ID", "")
    if not owner:
        return
    try:
        from app import push as push_mod
        if push_mod.PUSH_CONFIGURED:
            paths = sorted({r.get("path", "?") for r in breaches})
            await push_mod.send_insight(
                owner, "Quality sentry", f"{len(breaches)} quality breach(es) today ({', '.join(paths)})")
    except Exception as e:
        logging.warning("[quality-cron] alert push failed: %s", e)
