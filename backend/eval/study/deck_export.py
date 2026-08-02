"""Export the swipe deck: measured corpus reels -> backend/assets/style_deck.json.

The deck is what the onboarding taste-swiper shows. Two requirements shape it:

1. SPREAD, not "best". A taste quiz only teaches us something if consecutive cards
   differ along the dimensions we're trying to learn. So we pick by FARTHEST-POINT
   SAMPLING in the style-vector space (start at the medoid, then repeatedly take the
   candidate furthest from everything picked so far), and then repair any dimension
   whose extremes went uncovered. Picking the top-viewed reels instead would hand the
   creator 18 near-identical fast-cut reels and learn nothing.

2. COMMITTED + DURABLE. The per-reel anatomy lives in a gitignored local data dir, so
   this step serializes the attributes OUT into an asset the server can ship. The video
   URLs are the already-rehosted Supabase ones (raw CDN links 403 within hours).

CLI:  cd backend && python3 -m eval.study.deck_export [--n 18] [--out ../assets/style_deck.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import style_profile as sp
from eval.study.common import ANATOMY_DIR, DATA_DIR

ASSETS = Path(__file__).resolve().parents[2] / "assets"
DEFAULT_N = 18


# Card-appropriateness (owner pass, build 63): a swipe card is judged in ~5-10s, so a
# 3-minute reel wastes its slot, and 4 near-identical startup reels teach the same thing
# 4 times. Both constraints trade a little vector spread for a deck that FEELS varied.
MAX_DURATION_S = 90.0
MAX_PER_NICHE = 2
# Yunicorn is a TALKING-HEAD app, so every card must actually BE one. The old gate read
# `a.get("tier") or "th"`, and `tier` is absent from every anatomy file on disk — so the
# fallback made it unconditionally true and the deck filled with pure-b-roll reels (owner:
# "it's showing all kinds of videos"). Measured instead, from the per-shot face data that
# IS in every record: the share of runtime with a face on screen. The corpus median is
# 0.44 and a third of it is 0.00 — 0.60 keeps "the creator is on camera for most of it".
MIN_FACE_SHARE = 0.60


def _face_share(a: dict) -> float:
    shots = a.get("shots") or []
    dur = float(a.get("duration_s") or 0.0)
    if not shots or dur <= 0:
        return 0.0
    on = sum(float(s.get("t1", 0)) - float(s.get("t0", 0))
             for s in shots if s.get("face_present"))
    return max(0.0, min(1.0, on / dur))


def _load_candidates() -> list[dict]:
    """Analyzed corpus reels that are actually playable and actually talking-head."""
    manifest = json.loads((DATA_DIR / "corpus_manifest.json").read_text())
    by_id = {r["reel_id"]: r for r in manifest.get("reels", [])}
    out = []
    for f in sorted(ANATOMY_DIR.glob("*.json")):
        a = json.loads(f.read_text())
        if a.get("excluded") or a.get("platform") == "local":
            continue
        # b-roll norms only hold for TRUE talking-head reels; the deck teaches editing
        # taste, so voiceover-over-b-roll reels would muddy every dimension — and the
        # creator is being asked "which edits feel like you", which is meaningless over
        # footage with no presenter in it.
        if _face_share(a) < MIN_FACE_SHARE:
            continue
        entry = by_id.get(a["reel_id"])
        if not entry or not (entry.get("video_url_cdn") or "").startswith("http"):
            continue
        if float(a.get("duration_s") or 0.0) > MAX_DURATION_S:
            continue
        out.append({
            "reel_id": a["reel_id"],
            "video_url": entry["video_url_cdn"],
            "thumbnail_url": entry.get("thumbnail_url") or "",
            "niche": a.get("niche") or entry.get("niche") or "",
            "author": entry.get("author") or "",
            "views": int(entry.get("views") or 0),
            "duration_s": float(a.get("duration_s") or 0.0),
            "vector": sp.vector_from_anatomy(a),
            # shown on the card so the swipe is informed, not blind
            "display_attrs": _display_attrs(a),
        })
    return out


def _display_attrs(a: dict) -> list[str]:
    """Three short human tags per card (Tinder's attribute-chip pattern)."""
    v = sp.vector_from_anatomy(a)
    tags = []
    tags.append("fast cuts" if v["pace"] >= 0.6 else "slow cuts" if v["pace"] <= 0.3 else "steady pace")
    tags.append("bold captions" if v["caption_boldness"] >= 0.5 else "clean captions")
    if v["broll_density"] >= 0.5:
        tags.append("b-roll heavy")
    elif v["broll_density"] <= 0.15:
        tags.append("all face")
    else:
        tags.append("some b-roll")
    return tags


def _medoid(items: list[dict]) -> int:
    best, best_sum = 0, float("inf")
    for i, a in enumerate(items):
        s = sum(sp.distance(a["vector"], b["vector"]) for b in items)
        if s < best_sum:
            best, best_sum = i, s
    return best


def farthest_point_sample(items: list[dict], n: int) -> list[dict]:
    """Maximize spread: each new pick is the candidate furthest from the current set —
    subject to the per-niche cap, so the deck also FEELS varied (a taste quiz where four
    consecutive cards are startup founders reads as one card asked four times)."""
    if len(items) <= n:
        return list(items)
    picked = [items[_medoid(items)]]
    remaining = [x for x in items if x is not picked[0]]
    while len(picked) < n and remaining:
        counts: dict[str, int] = {}
        for p in picked:
            counts[p.get("niche") or ""] = counts.get(p.get("niche") or "", 0) + 1
        eligible = [c for c in remaining
                    if counts.get(c.get("niche") or "", 0) < MAX_PER_NICHE]
        pool = eligible or remaining      # cap exhausts the pool → relax, don't starve
        nxt = max(pool,
                  key=lambda c: min(sp.distance(c["vector"], p["vector"]) for p in picked))
        picked.append(nxt)
        remaining.remove(nxt)
    return picked


def repair_pole_coverage(picked: list[dict], pool: list[dict]) -> list[dict]:
    """Every dimension needs examples at BOTH ends, else a swipe on it teaches nothing.
    Swap the most redundant pick for the best uncovered-pole candidate."""
    picked = list(picked)
    for dim in sp.DIMS:
        for want_high in (True, False):
            have = any((p["vector"][dim] >= 0.66) if want_high else (p["vector"][dim] <= 0.33)
                       for p in picked)
            if have:
                continue
            cands = [c for c in pool if c not in picked and
                     ((c["vector"][dim] >= 0.66) if want_high else (c["vector"][dim] <= 0.33))]
            if not cands:
                continue   # the corpus genuinely has no such reel — report, don't fake
            # drop whichever pick is closest to another pick (least informative)
            drop = min(picked, key=lambda p: min(
                (sp.distance(p["vector"], q["vector"]) for q in picked if q is not p),
                default=9.9))
            picked[picked.index(drop)] = cands[0]
    return picked


def build(n: int) -> dict:
    pool = _load_candidates()
    if not pool:
        raise SystemExit("no analyzed talking-head reels with durable URLs — run the study first")
    picked = repair_pole_coverage(farthest_point_sample(pool, n), pool)
    coverage = {d: {"low": sum(1 for p in picked if p["vector"][d] <= 0.33),
                    "high": sum(1 for p in picked if p["vector"][d] >= 0.66)}
                for d in sp.DIMS}
    return {
        "schema_version": sp.SCHEMA_VERSION,
        "deck_version": 1,
        "dims": list(sp.DIMS),
        "anchors": sp.ANCHORS,
        "cold_start": sp.COLD_START,
        "pool_size": len(pool),
        "coverage": coverage,
        "reels": picked,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=DEFAULT_N)
    ap.add_argument("--out", type=str, default=str(ASSETS / "style_deck.json"))
    a = ap.parse_args()
    deck = build(a.n)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(deck, indent=2))
    print(f"deck: {len(deck['reels'])} reels from a pool of {deck['pool_size']} -> {a.out}")
    thin = [d for d, c in deck["coverage"].items() if c["low"] == 0 or c["high"] == 0]
    for d in thin:
        print(f"  NOTE: '{d}' has no examples at one pole "
              f"(low={deck['coverage'][d]['low']} high={deck['coverage'][d]['high']}) — "
              "the corpus doesn't contain them; that dimension will learn slowly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
