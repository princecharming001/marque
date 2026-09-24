"""Talking-head script realism eval (2026-09-23).

Owner report: "the scripts are not realistic, they are overly short or something."
This drives the REAL generation paths the app uses for Home picks and grades every
script with an independent judge (its own prompt, not the pipeline's judge, so the
grader doesn't share the generator's blind spots).

Paths:
  fast  — main._fast_feed_scripts   (what Home paints first; most views)
  full  — main._generate_scripts    (the background Opus upgrade / onboarding)

Personas mirror real accounts: most carry only niche + audience + goal (that is what
production profiles actually contain), a few are richer.

Usage (needs real keys; uses prod Supabase for arms/strategy reads):
  cd backend && set -a && . ./.env && set +a
  .venv/bin/python -m eval.script_realism_eval --paths fast,full --label baseline
Writes eval/out/script_realism_<label>.json and prints a scorecard.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import time
from pathlib import Path

import main
from app import honesty
from prompts import OPUS

PERSONAS = [
    # thin profiles, exactly the shape of real production accounts
    {"id": "fashion-founders", "niche": "Fashion", "audience": "Founders & business owners",
     "goal": "Build authority", "posting_frequency": "4–5x a week", "weekly_target": 5},
    {"id": "mental-health", "niche": "Mental health", "audience": "Students & Gen Z",
     "goal": "Grow my audience", "posting_frequency": "3x a week", "weekly_target": 3},
    {"id": "beauty-30", "niche": "Beauty", "audience": "Women 30+", "goal": "Grow my audience",
     "posting_frequency": "daily", "weekly_target": 7},
    {"id": "finance-genz", "niche": "Personal finance", "audience": "Students & Gen Z",
     "goal": "Build authority", "posting_frequency": "4–5x a week", "weekly_target": 5},
    {"id": "parenting", "niche": "Parenting", "audience": "Parents & families",
     "goal": "Grow my audience", "posting_frequency": "3x a week", "weekly_target": 3},
    {"id": "cooking-busy", "niche": "Food & cooking", "audience": "Busy professionals",
     "goal": "Monetize", "posting_frequency": "4–5x a week", "weekly_target": 5},
    # richer profiles
    {"id": "fitness-desk", "niche": "Fitness", "audience": "Busy professionals", "goal": "Get clients",
     "known_for": "no-nonsense strength training for people with desk jobs",
     "what_you_do": "I coach office workers to get strong in three short sessions a week",
     "posting_frequency": "4–5x a week", "weekly_target": 5},
    {"id": "saas-founder", "niche": "Startups", "audience": "Founders & business owners",
     "goal": "Get clients", "known_for": "building a bootstrapped B2B SaaS in public",
     "what_you_do": "I run a 6-person SaaS that sells scheduling software to dental clinics",
     "posting_frequency": "3x a week", "weekly_target": 3},
]

JUDGE_SYSTEM = """You are a veteran short-form video producer who has scripted thousands of \
TALKING-HEAD reels (one person, face to camera, one take, the editor adds captions and b-roll). \
You are grading scripts an AI wrote for a real creator to read to camera. Be strict and concrete.

Score EACH script 1-10 on:
- spoken: would a real person actually say these exact words out loud to camera? Natural rhythm, \
contractions, varied sentence length, sounds like talking not like a caption, a list of slogans, or \
an ad. Chains of clipped fragments ("Thirty minutes. Compound lifts. Done.") score low.
- substance: is there enough real content to be worth watching for its length: a point, the reason \
behind it, one concrete example or mechanism, and a payoff? A handful of assertions scores low.
- specificity: concrete, ownable details (numbers, mechanisms, named situations) vs generic advice \
that fits any account.
- hook: does the first line stop the scroll and open a question the rest answers?
- honesty: 10 = nothing the creator would have to lie about; low if it invents personal anecdotes, \
client stories, credentials or results the creator profile does not support.
- fit: sounds like THIS creator for THIS audience (not a generic niche account).
- talking_head: fully filmable as one person talking to camera, no demos/props/locations required.

Also judge length against the product's 35-55 second target, at about 165 spoken words per minute:
too_short (under ~80 words / 30s, feels thin), right (~80-160 words), or too_long (over ~160 words / 60s).
Then for the SET: variety 1-10 (different topics AND different formats/structures; three takes on one \
idea scores low) and would_post: how many of the scripts a real creator would film as written (0-N).
Give one sentence of the single most important fix."""

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["scripts", "variety", "would_post", "fix"],
    "properties": {
        "scripts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["index", "spoken", "substance", "specificity", "hook", "honesty", "fit",
                         "talking_head", "length", "note"],
            "properties": {
                "index": {"type": "integer"}, "spoken": {"type": "integer"},
                "substance": {"type": "integer"}, "specificity": {"type": "integer"},
                "hook": {"type": "integer"}, "honesty": {"type": "integer"},
                "fit": {"type": "integer"}, "talking_head": {"type": "integer"},
                "length": {"type": "string", "enum": ["too_short", "right", "too_long"]},
                "note": {"type": "string"}}}},
        "variety": {"type": "integer"}, "would_post": {"type": "integer"}, "fix": {"type": "string"},
    },
}

AXES = ("spoken", "substance", "specificity", "hook", "honesty", "fit", "talking_head")
_TELLS = re.compile(r"\b(here's the thing|let me tell you|in today's video|picture this|buckle up|"
                    r"game[- ]changer|unlock|level up|dive in|journey)\b", re.I)
_STOP = set("the a an and or but to of in on for with is are was be it that this you your i my me we "
            "they them their not no do does don't at as by so if just more most than then from".split())


def _spoken(s: dict) -> str:
    return " ".join(x for x in (s.get("hook", ""), s.get("body", ""), s.get("cta", "")) if x)


def _content_words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", t.lower()) if len(w) > 3 and w not in _STOP}


def metrics(scripts: list[dict]) -> dict:
    words = [len(_spoken(s).split()) for s in scripts]
    body = [len((s.get("body") or "").split()) for s in scripts]
    overlaps = []
    for i in range(len(scripts)):
        for j in range(i + 1, len(scripts)):
            a, b = _content_words(_spoken(scripts[i])), _content_words(_spoken(scripts[j]))
            if a and b:
                overlaps.append(len(a & b) / len(a | b))
    frag = []
    for s in scripts:
        sents = [x for x in re.split(r"[.!?]+", s.get("body") or "") if x.strip()]
        frag.append(sum(1 for x in sents if len(x.split()) <= 4) / max(1, len(sents)))
    return {
        "total_words": words, "body_words": body,
        "est_seconds": [round(w / 2.5) for w in words],          # 150 wpm talking head
        "formats": [s.get("formatId") for s in scripts],
        "distinct_formats": len({s.get("formatId") for s in scripts}),
        "topic_overlap_max": round(max(overlaps), 2) if overlaps else 0.0,
        "fragment_ratio": [round(f, 2) for f in frag],
        "tells": sum(len(_TELLS.findall(_spoken(s))) for s in scripts),
        "dashes": sum(_spoken(s).count("—") + _spoken(s).count("–") for s in scripts),
        # 2026-09-23 content-engine pass
        "first_person_claims": [c for c in (honesty.script_claims(s) for s in scripts) if c],
        "dangling_titles": sum(1 for s in scripts if (s.get("title") or "").split()
                               and (s.get("title") or "").split()[-1].lower() in main._TITLE_TAIL_WORDS),
        "seconds_off": [abs(int(s.get("targetSeconds") or 0) - round(len(_spoken(s).split()) / 2.75))
                        for s in scripts],
    }


def cross_page_dupes(pages: list[list[dict]], thresh: float = 0.35) -> int:
    """Near-duplicate script pairs ACROSS pages (content-word Jaccard of title+hook)."""
    items = [(pi, _content_words(f"{s.get('title', '')} {s.get('hook', '')}"))
             for pi, pg in enumerate(pages) for s in pg]
    n = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (pa, a), (pb, b) = items[i], items[j]
            if pa != pb and a and b and len(a & b) / len(a | b) >= thresh:
                n += 1
    return n


async def judge(persona: dict, scripts: list[dict]) -> dict:
    profile = {k: v for k, v in persona.items() if k != "id"}
    blob = "\n\n".join(
        f"SCRIPT {i}\nformat: {s.get('formatId')}\ntitle: {s.get('title')}\nHOOK: {s.get('hook')}\n"
        f"BODY:\n{s.get('body')}\nCTA: {s.get('cta')}" for i, s in enumerate(scripts))
    user = f"CREATOR PROFILE: {json.dumps(profile)}\n\n{blob}"
    out = await main.anthropic_json(JUDGE_SYSTEM, user, JUDGE_SCHEMA, OPUS, 2500)
    out = out if isinstance(out, dict) else {}
    # The judge sometimes grades phantom scripts (an index past the end, or one index
    # twice); keep the first grade per REAL script so means and counts aren't inflated.
    seen, kept = set(), []
    for s in out.get("scripts") or []:
        i = s.get("index")
        if isinstance(i, int) and 0 <= i < len(scripts) and i not in seen:
            seen.add(i)
            kept.append(s)
    if out:
        out["scripts"] = kept
        out["would_post"] = max(0, min(int(out.get("would_post") or 0), len(scripts)))
    return out


CHAT_TOPICS = {"fashion-founders": "pricing your first collection", "mental-health": "exam stress",
               "beauty-30": "retinol for beginners", "finance-genz": "your first budget",
               "parenting": "toddler tantrums", "cooking-busy": "meal prep",
               "fitness-desk": "training on a desk job", "saas-founder": "cold outreach"}


async def run_path(path: str, persona: dict, cursor: int) -> dict:
    brand = {k: v for k, v in persona.items() if k != "id"}
    cid = f"qa-eval-{persona['id']}"
    # A returning account has its identity doc already (built in the background on its
    # first request); build it up front here so the eval measures the steady state.
    await main._ensure_account_context(cid, brand)
    arms = await main._top_arms(cid, brand.get("niche", ""))
    t0 = time.monotonic()
    pages: list[list[dict]] = []
    if path == "fast":
        # Three pages like a scrolling creator: each page's titles feed the planner's
        # topic memory exactly as _compose_feed_items does in the live feed.
        main._pitched_titles.pop(cid, None)
        res = {}
        for c in range(cursor, cursor + 3):
            sreq, _why = main._feed_sreq(brand, "", c, cid, None, [], arms=arms)
            r = await main._fast_feed_scripts(sreq, c)
            res = res or r
            pages.append(r.get("scripts") or [])
            main._remember_titles(main._pitched_titles, cid, [s.get("title") for s in pages[-1]])
        secs = round((time.monotonic() - t0) / 3, 1)
        return {"mode": res.get("mode"), "secs": secs, "pillar": "", "scripts": pages[0],
                "pages": pages}
    if path == "full":
        sreq, _why = main._feed_sreq(brand, "", cursor, cid, None, [], arms=arms)
        res = await main._generate_scripts(sreq)
    elif path == "chat":
        req = main.ConverseRequest(creator_id=cid, brand=brand, memory={}, messages=[])
        scripts = await main._chain_scripts(req, {"topic": CHAT_TOPICS.get(persona["id"], "your next post"),
                                                  "count": 2})
        res = {"mode": "live" if scripts else "empty", "scripts": scripts}
    elif path == "steer":
        sreq, _why = main._feed_sreq(brand, "", cursor, cid, None, [], arms=arms)
        base = (await main._fast_feed_scripts(sreq, cursor)).get("scripts") or []
        t0 = time.monotonic()
        steered = []
        for s in base[:2]:
            r = await main.steer(main.SteerRequest(**brand, creator_id=cid, script=s,
                                                   instruction="make it punchier and more specific"))
            if r.get("script"):
                steered.append(r["script"])
        res = {"mode": "live" if steered else "empty", "scripts": steered}
    else:
        raise ValueError(path)
    return {"mode": res.get("mode"), "secs": round(time.monotonic() - t0, 1),
            "pillar": "", "scripts": res.get("scripts") or []}


async def main_async(paths: list[str], label: str, cursor: int, only: str | None) -> None:
    personas = [p for p in PERSONAS if not only or p["id"] in only.split(",")]
    sem = asyncio.Semaphore(int(os.environ.get("EVAL_CONCURRENCY", "4")))

    async def one(path: str, persona: dict) -> dict:
        async with sem:
            gen = await run_path(path, persona, cursor)
            j = await judge(persona, gen["scripts"]) if gen["scripts"] else {}
            return {"path": path, "persona": persona["id"], **gen, "metrics": metrics(gen["scripts"]),
                    "judge": j}

    rows = await asyncio.gather(*(one(p, per) for p in paths for per in personas))
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)
    from app import palo_flags
    config = {"flags": {f: bool(palo_flags.enabled(getattr(palo_flags, f)))
                        for f in ("PALO_PORT", "CHANNEL_IDENTITY", "IDEA_BANK", "STRATEGY_COMPILER",
                                  "EXEMPLAR_BANK", "MEMORY_V2", "WRITE_AGENT") if hasattr(palo_flags, f)},
              "models": {"fast": main.HAIKU, "full": OPUS}, "label": label}
    (out / f"script_realism_{label}.json").write_text(json.dumps({"config": config, "rows": rows}, indent=1))
    print("config:", json.dumps(config["flags"]))

    for path in paths:
        pr = [r for r in rows if r["path"] == path]
        per_axis = {a: [] for a in AXES}
        lengths, variety, would, posted_n = [], [], [], 0
        for r in pr:
            for s in r["judge"].get("scripts", []):
                for a in AXES:
                    per_axis[a].append(s[a])
                lengths.append(s["length"])
            if r["judge"]:
                variety.append(r["judge"]["variety"])
                would.append(r["judge"]["would_post"])
                posted_n += len(r["scripts"])
        m = [r["metrics"] for r in pr]
        print(f"\n===== {label} / {path}: {len(pr)} personas, modes={sorted({r['mode'] for r in pr})}, "
              f"latency p50={statistics.median([r['secs'] for r in pr]):.1f}s max={max(r['secs'] for r in pr):.1f}s")
        print("  judge:", " ".join(f"{a}={statistics.mean(v):.1f}" for a, v in per_axis.items() if v))
        print(f"  set: variety={statistics.mean(variety) if variety else 0:.1f}  would_post={sum(would)}/{posted_n}  "
              f"length: short={lengths.count('too_short')} right={lengths.count('right')} "
              f"long={lengths.count('too_long')}")
        allw = [w for x in m for w in x["total_words"]]
        if not allw:
            print("  (no scripts returned on this path)")
            continue
        print(f"  words: median={statistics.median(allw)} min={min(allw)} max={max(allw)}  "
              f"est_sec median={statistics.median([s for x in m for s in x['est_seconds']])}  "
              f"distinct_formats/page={statistics.mean([x['distinct_formats'] for x in m]):.1f}  "
              f"topic_overlap_max={statistics.mean([x['topic_overlap_max'] for x in m]):.2f}  "
              f"fragments={statistics.mean([f for x in m for f in x['fragment_ratio']]):.2f}  "
              f"tells={sum(x['tells'] for x in m)} dashes={sum(x['dashes'] for x in m)}")
        claims = [c for x in m for c in x["first_person_claims"]]
        print(f"  honesty: first-person claims={len(claims)} {claims[:4]}  dangling titles="
              f"{sum(x['dangling_titles'] for x in m)}  seconds off median="
              f"{statistics.median([o for x in m for o in x['seconds_off']] or [0])}")
        paged = [r["pages"] for r in pr if r.get("pages")]
        if paged:
            print(f"  cross-page near-duplicates (3 pages): total={sum(cross_page_dupes(p) for p in paged)} "
                  f"per persona={[cross_page_dupes(p) for p in paged]}")
        for r in pr:
            if r["judge"].get("fix"):
                print(f"   - {r['persona']}: {r['judge']['fix'][:170]}")


if __name__ == "__main__":
    import logging
    # INFO so slot drops ("[fast] dropped (timeout ...)") and lint outcomes
    # ("[speakable] ... outcome=dropped reason=...") land in the run log.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", default="fast,full")
    ap.add_argument("--label", default="run")
    ap.add_argument("--cursor", type=int, default=0)
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    asyncio.run(main_async(a.paths.split(","), a.label, a.cursor, a.only))
