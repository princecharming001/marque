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

import logging

from app import palo_flags

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
