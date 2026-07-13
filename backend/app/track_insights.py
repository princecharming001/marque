"""Phase 3 (box 2) — deterministic post-performance detection (the LOOP I discipline).

This is where Palo shipped bugs, so the port is failing-test-first and pure:
  1. First-run baseline — the FIRST scan records a watermark and fires ZERO insights, so
     day-one history can't flood the creator with false milestones.
  2. Watermark de-dup — a milestone only fires on the read that crosses it; later reads
     never re-fire it (byte-content dedup is layered on top in box 3's dedup_hash).
  3. Underperformer skip — a video far below channel average short-circuits BEFORE any
     LLM call (asserted via a call counter in the tests).

The math (median + MAD, ≥2 confirmed reads, ≥2.5x spike, milestone ladders) is small,
pure, and unit-tested. The LLM card-writing + persistence is box 3. Flag TRACK_INSIGHTS.
"""
from __future__ import annotations

import hashlib
import logging

from app import ai_usage, palo_flags, palo_prompts
from app.palo_llm import anthropic_cached_json
from app.recall_ledger import new_ulid
from prompts import HAIKU

VIEW_MILESTONES = (10_000, 25_000, 50_000, 100_000, 250_000, 500_000,
                   1_000_000, 5_000_000, 10_000_000, 50_000_000)
FOLLOWER_MILESTONES = (1_000, 5_000, 10_000, 25_000, 50_000, 100_000,
                       250_000, 500_000, 1_000_000)

# A video only counts as a spike/milestone if it clears this share of channel average —
# below it, skip before spending any LLM (Palo's underperformer guard).
UNDERPERFORMER_RATIO = 0.10
SPIKE_MULT = 2.5
SPIKE_MIN_READS = 2


def crossed_milestones(prev: float, curr: float, ladder: tuple) -> list[int]:
    """Milestones strictly above `prev` and at/below `curr`."""
    if curr <= prev:
        return []
    return [m for m in ladder if prev < m <= curr]


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def median_mad(xs: list[float]) -> tuple[float, float]:
    """(median, median absolute deviation)."""
    if not xs:
        return 0.0, 0.0
    med = _median(xs)
    return med, _median([abs(x - med) for x in xs])


def is_underperformer(value: float, channel_avg: float, ratio: float = UNDERPERFORMER_RATIO) -> bool:
    return channel_avg > 0 and value < channel_avg * ratio


def detect_spike(value: float, history: list[float], mult: float = SPIKE_MULT,
                 min_reads: int = SPIKE_MIN_READS) -> bool:
    """A spike needs ≥`min_reads` prior reads and `value` ≥ `mult` × the median baseline
    (median resists the outlier the spike itself would be)."""
    if len(history) < min_reads:
        return False
    med, _ = median_mad(history)
    return med > 0 and value >= mult * med


async def detect_milestones(store, creator_id: str, key: str, curr: float,
                            ladder: tuple) -> list[int]:
    """Watermark-based milestone crossings with FIRST-RUN-ZERO. Returns [] on the first
    ever read for this key (records the baseline instead), and advances the watermark so
    a crossed milestone never re-fires."""
    if store is None:
        return []
    wm = await store.get_watermark(creator_id, f"{key}_milestone")
    if wm is None:                                     # first run: baseline only, fire nothing
        await store.set_watermark(creator_id, f"{key}_milestone", float(curr))
        return []
    crossed = crossed_milestones(float(wm), float(curr), ladder)
    if curr > float(wm):
        await store.set_watermark(creator_id, f"{key}_milestone", float(curr))
    return crossed


async def deterministic_events(store, creator_id: str, snapshot: dict) -> list[dict]:
    """The full deterministic pass: view/follower milestones + per-video spikes (skipping
    underperformers). Returns raw insight events (box 3 turns them into cards). Flag-gated;
    no store / off ⇒ []."""
    if not palo_flags.enabled(palo_flags.TRACK_INSIGHTS) or store is None:
        return []
    events: list[dict] = []
    try:
        for m in await detect_milestones(store, creator_id, "views",
                                         float(snapshot.get("total_views", 0)), VIEW_MILESTONES):
            events.append({"type": "view_milestone", "value": m})
        for m in await detect_milestones(store, creator_id, "followers",
                                         float(snapshot.get("followers", 0)), FOLLOWER_MILESTONES):
            events.append({"type": "follower_milestone", "value": m})

        channel_avg = float(snapshot.get("channel_avg", 0))
        for v in snapshot.get("videos", []):
            value = float(v.get("views", 0))
            if is_underperformer(value, channel_avg):
                continue                                # skip BEFORE any spike/LLM work
            if detect_spike(value, [float(h) for h in v.get("history", [])]):
                mult = round(value / max(median_mad(v.get("history", []))[0], 1), 1)
                events.append({"type": "video_spike", "video_id": v.get("id"),
                               "value": value, "multiplier": mult})
    except Exception as e:
        logging.warning("[track_insights] deterministic_events failed: %s", e)
    return events


# --- card writing (Insight Discovery Engine + dedup + anti-repetition) ---------
_CARD_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["title", "description"],
    "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
}
_CATEGORY = {"view_milestone": "blue", "follower_milestone": "blue",
             "video_spike": "yellow", "content_pattern": "green"}


def _dedup_hash(creator_id: str, event: dict) -> str:
    """Stable content hash so a re-run of the daily scan can never post the same card
    twice (enforced by the insight_feed.dedup_hash UNIQUE constraint too)."""
    key = f"{creator_id}|{event.get('type')}|{event.get('value') or event.get('video_id')}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _template_card(event: dict) -> dict:
    t, v = event.get("type"), event.get("value")
    if t == "view_milestone":
        return {"title": f"You crossed {int(v):,} views", "description": "New milestone. Keep the format that got you here."}
    if t == "follower_milestone":
        return {"title": f"{int(v):,} followers", "description": "Thank the audience and double down on what's working."}
    if t == "video_spike":
        return {"title": f"A reel is {event.get('multiplier')}x your average",
                "description": "Study what worked and make a fast follow-up."}
    return {"title": "New performance signal", "description": "Worth a look."}


def _seed(event: dict) -> dict:
    return {"kind": "insight", "event_type": event.get("type"),
            "value": event.get("value"), "video_id": event.get("video_id")}


async def _card(store, creator_id: str, event: dict, recent_titles: list[str],
                brand: dict | None) -> dict:
    """LLM card via the Insight Discovery Engine (anti-repetition context); template on
    keyless / failure. Title/description clamped to schema limits."""
    base = _template_card(event)
    system, user = palo_prompts.insight_card_prompt(event, recent_titles, brand)
    from app.prompt_store import get_prompt
    system = await get_prompt("palo.insight.discovery", system, store=store)
    data = await anthropic_cached_json(system, user, _CARD_SCHEMA, HAIKU, max_tokens=200)
    if isinstance(data, dict) and data.get("title"):
        await ai_usage.record(store, creator_id, "insight.card", HAIKU, 500, 80)
        return {"title": str(data["title"])[:60],
                "description": str(data.get("description", base["description"]))[:100]}
    return base


async def write_insights(store, creator_id: str, events: list[dict],
                         brand: dict | None = None) -> list[dict]:
    """Turn deterministic events into deduped insight_feed cards. Returns the NEW cards
    (box 4 delivers them). Flag-gated + keyless (no store) => []."""
    if not palo_flags.enabled(palo_flags.TRACK_INSIGHTS) or store is None or not events:
        return []
    recent = await store.load_insights(creator_id, limit=50)   # ≤50 anti-repetition context
    recent_titles = [r.get("title", "") for r in recent if r.get("title")]
    new_cards: list[dict] = []
    for ev in events:
        try:
            card = await _card(store, creator_id, ev, recent_titles, brand)
            insight = {"id": new_ulid(), "creator_id": creator_id, "type": ev.get("type"),
                       "category": _CATEGORY.get(ev.get("type"), "blue"),
                       "title": card["title"], "description": card["description"],
                       "content": ev, "chips": [], "dedup_hash": _dedup_hash(creator_id, ev),
                       "delivered": False, "conversation_seed": _seed(ev)}
            res = await store.upsert_insight(insight)
            if res is True:                                    # True=new row, False=dup, UNAVAILABLE
                new_cards.append(insight)
                recent_titles.append(card["title"])            # avoid intra-run repeats too
        except Exception as e:
            logging.warning("[track_insights] write_insights failed: %s", e)
    return new_cards


async def scan_and_write(store, creator_id: str, snapshot: dict,
                         brand: dict | None = None) -> list[dict]:
    """Box-3 entry: detect deterministic events, then write deduped cards. Returns new
    cards for delivery."""
    events = await deterministic_events(store, creator_id, snapshot)
    return await write_insights(store, creator_id, events, brand)
