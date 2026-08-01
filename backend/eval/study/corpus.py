"""Corpus builder: scrape -> filter -> quota -> corpus_manifest.json.

Reuses main.py's Apify plumbing (import-main is the established eval/ pattern).
Verified gotchas handled here:
  - _normalize_apify_post DROPS permalinks -> we capture url/webVideoUrl FIRST.
  - IG hashtag items often lack view counts -> likes-based threshold fallback,
    stamped in selection.metric_used.
  - _run_apify_actor defaults to timeout_s=110 -> we pass 300.

Threshold: views >= max(views_floor, per-niche p75) — the absolute floor kills
junk; the relative cut stops a mega-niche filling its quota with its own tail.
IG-primary (>=75% target): TikTok only fills niches where IG under-delivers.

CLI:
  cd backend && python3 -m eval.study.corpus build [--niches "..."] [--per-niche 8]
  cd backend && python3 -m eval.study.corpus audit          # CP-2 data-quality audit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import main  # noqa: E402 — established eval/ pattern

from eval.study.common import (StudyConfig, config_dict, ensure_dirs,
                               load_manifest, reel_id, save_manifest,
                               PER_NICHE_FLOOR)

APIFY_TIMEOUT_S = 300
IG_RESULTS = 100
TT_RESULTS = 40


def _study_normalize(item: dict, platform: str) -> dict | None:
    permalink = item.get("url") if platform == "instagram" else item.get("webVideoUrl")
    post = main._normalize_apify_post(item, platform)
    if not post or not permalink:
        return None
    post["permalink"] = permalink
    return post


async def _scrape_niche(niche: str, cfg: StudyConfig) -> list[dict]:
    posts: list[dict] = []
    if "instagram" in cfg.platforms:
        tags = main._niche_hashtags(niche)
        items = await main._run_apify_actor(
            "apify~instagram-hashtag-scraper",
            {"hashtags": tags, "resultsLimit": IG_RESULTS},
            timeout_s=APIFY_TIMEOUT_S)
        posts += [p for p in (_study_normalize(i, "instagram") for i in items or []) if p]
    ig_over_floor = [p for p in posts if _metric(p, cfg)[0] > 0]
    if "tiktok" in cfg.platforms and len(ig_over_floor) < cfg.per_niche:
        items = await main._run_apify_actor(
            "clockworks~tiktok-scraper",
            {"searchQueries": [niche], "resultsPerPage": TT_RESULTS,
             "shouldDownloadVideos": True},
            timeout_s=APIFY_TIMEOUT_S)
        posts += [p for p in (_study_normalize(i, "tiktok") for i in items or []) if p]
    return posts


def _metric(post: dict, cfg: StudyConfig) -> tuple[int, str]:
    v = int(post.get("views") or 0)
    if v > 0:
        return v, "views"
    return int(post.get("likes") or 0), "likes"


def _select(posts: list[dict], niche: str, cfg: StudyConfig) -> list[dict]:
    """Hybrid absolute+relative threshold with the likes fallback."""
    with_views = [p for p in posts if int(p.get("views") or 0) > 0]
    use_likes = len(with_views) < 0.3 * len(posts) if posts else True
    metric_key = "likes" if use_likes else "views"
    floor = cfg.likes_floor if use_likes else cfg.views_floor
    vals = sorted(int(p.get(metric_key) or 0) for p in posts)
    p75 = vals[int(len(vals) * 0.75)] if vals else 0
    threshold = max(floor, p75)
    survivors = [p for p in posts if int(p.get(metric_key) or 0) >= threshold]
    # TH prefilter (loose tier-1 heuristic; the HARD gate is post-download YuNet)
    kept = []
    for p in survivors:
        ef = main._classify_edit_format(p)[0]
        if ef in ("talking_head", "talking_head_broll"):
            p["th_prefilter"] = ef
            kept.append(p)
    kept.sort(key=lambda p: -int(p.get(metric_key) or 0))
    quota = int(cfg.per_niche * cfg.overprovision)
    out = kept[:quota]
    for rank, p in enumerate(out):
        p["selection"] = {"rule": f"{metric_key}_floor+p75", "threshold": threshold,
                          "metric_used": metric_key, "rank_in_niche": rank,
                          "niche_candidates": len(posts)}
        p["niche"] = niche
        p["role"] = "primary" if rank < cfg.per_niche else "spare"
    return out


async def build(cfg: StudyConfig) -> dict:
    ensure_dirs()
    reels: list[dict] = []
    for niche in cfg.niches:
        try:
            posts = await _scrape_niche(niche, cfg)
        except Exception as e:
            print(f"[corpus] {niche}: scrape failed: {e}")
            posts = []
        picked = _select(posts, niche, cfg)
        print(f"[corpus] {niche}: {len(posts)} scraped -> {len(picked)} selected")
        for p in picked:
            rid = reel_id(p["platform"] if p.get("platform") else
                          ("instagram" if "instagram" in p["permalink"] else "tiktok"),
                          p["permalink"])
            reels.append({
                "reel_id": rid,
                "platform": p.get("platform") or ("instagram" if "instagram" in p["permalink"] else "tiktok"),
                "niche": niche, "permalink": p["permalink"],
                "author": p.get("author", ""), "caption": (p.get("caption") or "")[:500],
                "views": int(p.get("views") or 0), "likes": int(p.get("likes") or 0),
                "duration_s": p.get("duration_s"), "posted_at": p.get("posted_at"),
                "video_url_cdn": p.get("video_url"), "thumbnail_url": p.get("thumbnail_url"),
                "selection": p["selection"], "th_prefilter": p.get("th_prefilter"),
                "role": p["role"], "status": "pending",
                "anatomy_path": f"anatomy/{rid}.json",
            })
    m = {"built_at": time.time(), "config": config_dict(cfg), "reels": reels}
    save_manifest(m)
    return m


def audit(m: dict | None = None) -> int:
    """CP-2 data-quality audit. Exit 1 on a blocking problem."""
    m = m or load_manifest()
    reels = m.get("reels", [])
    by_niche: dict[str, list] = {}
    for r in reels:
        by_niche.setdefault(r["niche"], []).append(r)
    problems = []
    print(f"corpus: {len(reels)} reels across {len(by_niche)} niches "
          f"(IG {sum(1 for r in reels if r['platform'] == 'instagram')} / "
          f"TT {sum(1 for r in reels if r['platform'] == 'tiktok')})")
    for niche, rs in by_niche.items():
        primaries = [r for r in rs if r["role"] == "primary"]
        likes_rate = sum(1 for r in rs if r["selection"]["metric_used"] == "likes") / len(rs)
        print(f"  {niche}: {len(primaries)} primary + {len(rs) - len(primaries)} spare | "
              f"likes-fallback {likes_rate:.0%}")
        if len(primaries) < PER_NICHE_FLOOR and m.get("source") != "reels_cache_durable":
            problems.append(f"{niche}: only {len(primaries)} primaries (< {PER_NICHE_FLOOR})")
    ig_share = (sum(1 for r in reels if r["platform"] == "instagram") / len(reels)) if reels else 0
    if reels and ig_share < 0.75 and m.get("source") != "reels_cache_durable":
        problems.append(f"IG share {ig_share:.0%} < 75% target")
    elif reels and ig_share < 0.75:
        print(f"  note: IG share {ig_share:.0%} (cache-sourced corpus — platform mix "
              "reported per-metric, not blocking)")
    if not reels:
        problems.append("empty corpus")
    for p in problems:
        print(f"  PROBLEM: {p}")
    return 1 if problems else 0


async def build_from_cache(cfg: StudyConfig) -> dict:
    """Alternate corpus source: the durable Supabase reels_cache — scraped winners
    with views/likes + REHOSTED (stable) video URLs. Used when fresh Apify scrapes
    are unavailable (2026-07-29: account-wide 403 platform-feature-disabled).
    Durable URLs make the permalink re-download fallback unnecessary; permalink is
    stamped 'cache:<id>' for traceability."""
    import os
    import httpx
    ensure_dirs()
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
           or os.environ.get("SUPABASE_KEY", ""))
    async with httpx.AsyncClient(timeout=60) as cl:
        r = await cl.get(f"{url}/rest/v1/reels_cache?select=cache_key,entry",
                         headers={"apikey": key, "Authorization": f"Bearer {key}"})
        r.raise_for_status()
        rows = r.json()
    reels: list[dict] = []
    for row in rows:
        ckey = row.get("cache_key", "")
        if not ckey.startswith("niche:"):
            continue                         # watched-creator caches: not "winners" pools
        niche = ckey.split(":", 1)[1]
        pool = []
        for x in (row.get("entry") or {}).get("reels") or []:
            durable = (x.get("video_url") or "").startswith(url)
            th = (x.get("edit_format") or "") in ("talking_head", "talking_head_broll")
            if durable and th and int(x.get("views") or 0) >= cfg.views_floor:
                pool.append(x)
        if len(pool) < 2:
            continue                         # junk/test niches
        pool.sort(key=lambda x: -int(x.get("views") or 0))
        for rank, x in enumerate(pool):
            rid = reel_id(x.get("platform") or "instagram", x.get("id") or x["video_url"])
            reels.append({
                "reel_id": rid,
                "platform": x.get("platform") or "instagram",
                "niche": niche, "permalink": f"cache:{x.get('id', '')}",
                "author": x.get("creator_handle", ""),
                "caption": (x.get("hook_text") or "")[:500],
                "views": int(x.get("views") or 0), "likes": int(x.get("likes") or 0),
                "duration_s": None, "posted_at": None,
                "video_url_cdn": x["video_url"],       # durable — download-stable
                "thumbnail_url": x.get("thumbnail_url", ""),
                "selection": {"rule": "cache_durable_th", "metric_used": "views",
                              "rank_in_niche": rank, "niche_candidates": len(pool)},
                "th_prefilter": x.get("edit_format"),
                "role": "primary",
                "status": "pending", "anatomy_path": f"anatomy/{rid}.json",
            })
    m = {"built_at": time.time(), "config": config_dict(cfg),
         "source": "reels_cache_durable", "reels": reels}
    save_manifest(m)
    print(f"[corpus] from cache: {len(reels)} durable TH reels across "
          f"{len({r['niche'] for r in reels})} niches")
    return m


def main_cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "audit"])
    ap.add_argument("--niches", type=str, default="")
    ap.add_argument("--per-niche", type=int, default=8)
    ap.add_argument("--platforms", type=str, default="instagram,tiktok")
    ap.add_argument("--from-cache", action="store_true")
    a = ap.parse_args()
    cfg = StudyConfig(per_niche=a.per_niche,
                      platforms=tuple(a.platforms.split(",")))
    if a.niches:
        cfg.niches = [n.strip() for n in a.niches.split(",") if n.strip()]
    if a.cmd == "build":
        m = asyncio.run(build_from_cache(cfg) if a.from_cache else build(cfg))
        sys.exit(audit(m))
    sys.exit(audit())


if __name__ == "__main__":
    main_cli()
