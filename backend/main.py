"""Marque backend — the server-side AI brain.

Holds every vendor key so the iOS app ships none (three-trust-plane model, docs/12). Every AI
route proxies Anthropic when ANTHROPIC_API_KEY is set and falls back to a deterministic mock
otherwise, so the whole surface is testable with zero keys. Prompt quality lives in prompts.py.
"""
from __future__ import annotations

import os
import json
import re
import uuid
import asyncio
import random
import logging

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import prompts
from prompts import OPUS, HAIKU, SONNET, STYLES, FORMAT_IDS
from app.edl import EDL, safe_default_edl, validate_and_repair, strip_fillers, ms_to_frame

app = FastAPI(title="Marque API", version="0.3.0")

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages"
AYRSHARE_KEY = os.environ.get("AYRSHARE_KEY", "")
BRIGHTDATA_KEY = os.environ.get("BRIGHTDATA_KEY", "")
APIFY_KEY = os.environ.get("APIFY_KEY", "")
VOYAGE_KEY = os.environ.get("VOYAGE_API_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# ---------------------------------------------------------------------------
# Learning loop — in-memory bandit (Supabase arm_stats in production)
# ---------------------------------------------------------------------------

_arm_stats: dict[str, dict] = {}
_post_registry: dict[str, dict] = {}

DIMENSIONS = ["pillar", "style", "format_id", "hook_signal"]
KAPPA = 5.0
EXPLORATION_FLOOR = 0.15


def _compute_y(m: dict, goal: str = "grow") -> float:
    import math
    reach = max(m.get("reach", 1), 1)
    save_rate = m.get("saves", 0) / reach
    share_rate = m.get("shares", 0) / reach
    follow_rate = m.get("follows_gained", 0) / reach
    watch_pct = m.get("avg_watch_pct", 0.0)
    like_rate = m.get("likes", 0) / reach
    comment_rate = m.get("comments", 0) / reach
    weights = {
        "grow":      {"follow_rate": 0.35, "share_rate": 0.25, "save_rate": 0.20, "watch_pct": 0.20},
        "authority": {"save_rate": 0.40, "watch_pct": 0.30, "share_rate": 0.20, "comment_rate": 0.10},
        "clients":   {"comment_rate": 0.35, "follow_rate": 0.30, "save_rate": 0.25, "share_rate": 0.10},
        "monetize":  {"save_rate": 0.40, "follow_rate": 0.30, "share_rate": 0.20, "watch_pct": 0.10},
    }.get(goal, {"follow_rate": 0.35, "share_rate": 0.25, "save_rate": 0.20, "watch_pct": 0.20})
    rates = {"follow_rate": follow_rate, "share_rate": share_rate, "save_rate": save_rate,
             "watch_pct": watch_pct, "like_rate": like_rate, "comment_rate": comment_rate}
    raw = sum(w * rates.get(k, 0) * 100 for k, w in weights.items())
    return 1 / (1 + math.exp(-0.5 * (raw - 2.0)))


def _update_arm(creator_id: str, dim_value: str, y: float):
    if creator_id not in _arm_stats:
        _arm_stats[creator_id] = {}
    stats = _arm_stats[creator_id]
    if dim_value not in stats:
        stats[dim_value] = {"n": 0, "sum_y": 0.0, "alpha": 1.0, "beta": 1.0}
    s = stats[dim_value]
    s["n"] += 1
    s["sum_y"] += y
    s["effect"] = (s["sum_y"] + KAPPA * 0.5) / (s["n"] + KAPPA)
    s["alpha"] = 1.0 + s["sum_y"]
    s["beta"] = 1.0 + (s["n"] - s["sum_y"])
    s["confidence"] = "confirmed" if s["n"] >= 8 else ("early_read" if s["n"] >= 4 else "insufficient")


def _thompson_sample(creator_id: str, candidates: list) -> list:
    import random
    stats = _arm_stats.get(creator_id, {})
    scored = []
    for c in candidates:
        s = stats.get(c, {"alpha": 1.0, "beta": 1.0})
        alpha, beta = s["alpha"], s["beta"]
        mean = alpha / (alpha + beta)
        std = (alpha * beta / ((alpha + beta)**2 * (alpha + beta + 1))) ** 0.5
        sample = min(1.0, max(0.0, mean + std * random.gauss(0, 1)))
        scored.append((c, sample))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Anthropic + JSON helpers
# ---------------------------------------------------------------------------

async def anthropic(system: str, user: str, model: str = OPUS, max_tokens: int = 3000) -> str:
    delays = [0.5, 2.0, 8.0]
    last_err = None
    for attempt, delay in enumerate(delays + [None]):
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(
                    ANTHROPIC_URL,
                    headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": model, "max_tokens": max_tokens, "system": system,
                          "messages": [{"role": "user", "content": user}]},
                )
            if r.status_code == 200:
                return "".join(b.get("text", "") for b in r.json().get("content", []))
            if r.status_code in (429, 500, 502, 503, 529):
                last_err = f"upstream {r.status_code}"
                if delay is not None:
                    logging.warning("anthropic: attempt %d got %d, retrying in %.1fs", attempt, r.status_code, delay)
                    jitter = delay * 0.2 * (random.random() * 2 - 1)
                    await asyncio.sleep(delay + jitter)
                    continue
            raise HTTPException(status_code=502, detail=f"upstream {r.status_code}")
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_err = str(e)
            if delay is not None:
                logging.warning("anthropic: attempt %d network error %s, retrying in %.1fs", attempt, last_err, delay)
                jitter = delay * 0.2 * (random.random() * 2 - 1)
                await asyncio.sleep(delay + jitter)
                continue
    raise HTTPException(status_code=502, detail=f"upstream error after retries: {last_err}")


def extract_json(text: str, array: bool):
    open_c, close_c = ("[", "]") if array else ("{", "}")
    start = text.find(open_c)
    if start == -1:
        logging.warning("extract_json: no opening bracket found in: %s", text[:200])
        return None
    depth, i = 0, start
    in_str, escape = False, False
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
        elif ch == '\\' and in_str:
            escape = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == open_c:
                depth += 1
            elif ch == close_c:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError as e:
                        logging.warning("extract_json parse error: %s in: %s", e, text[start:i+1][:200])
                        return None
        i += 1
    logging.warning("extract_json: unbalanced brackets in: %s", text[:200])
    return None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class Brand(BaseModel):
    niche: str = ""
    audience: str = ""
    known_for: str = ""
    what_you_do: str = ""
    goal: str = "Grow my audience"
    voice: dict = {}
    non_negotiables: list[str] = []

    def d(self) -> dict:
        return self.model_dump()


class PillarRequest(Brand):
    preferred_styles: list[str] = []
    posts: list[dict] = []


class ScriptRequest(Brand):
    pillar: str = ""
    pillar_summary: str = ""
    pillar_angle: str = ""
    example_topics: list[str] = []
    style: str = "talking_head"
    media_context: str = ""
    count: int = 3
    posts: list[dict] = []
    creator_id: str = "default"


class HooksRequest(Brand):
    topic: str = ""
    style: str = "talking_head"
    creator_id: str = "default"


class SteerRequest(Brand):
    script: dict = {}
    instruction: str = ""


class CaptionRequest(BaseModel):
    hook: str = ""
    body: str = ""


class TeardownRequest(BaseModel):
    clip: dict = {}


class InsightsRequest(Brand):
    summary: str = ""


class ScanRequest(Brand):
    handle: str = ""
    platform: str = "tiktok"
    posts: list[dict] = []          # caller-supplied posts (testing) or filled by the scraper


class VoiceFinalizeRequest(Brand):
    transcript: list[dict] = []


class VoiceSessionRequest(Brand):
    pass


class UploadMintRequest(BaseModel):
    filename: str = "footage.mov"
    content_type: str = "video/quicktime"


class ClipJobRequest(BaseModel):
    source_url: str = ""
    source_id: str = ""
    script: dict = {}
    formats: list[str] = []
    brand: dict = {}
    style: str = "talking_head"
    media_context: str = ""
    # Per-creator editing preferences (Settings → threaded into every edit)
    edit_prefs: dict = {}      # {auto_captions: bool, caption_style: clean|bold-word|karaoke, filler_trim: off|standard|aggressive}


class MediaAnalyzeRequest(BaseModel):
    content_hash: str          # SHA-256 of file bytes (dedup key)
    filename: str = "asset"
    kind: str = "photo"        # photo | video | screen
    storage_key: str = ""      # R2 key (for signed URL generation)
    public_url: str = ""       # direct URL for vision model


class BRollMatchRequest(BaseModel):
    cue_text: str              # the shotPlan beat description
    style: str = "faceless"
    corpus: list[dict] = []   # [{asset_id, description, tags, broll_suitability, duration_s}]
    top_k: int = 5


_media_cache: dict[str, dict] = {}


class PostRegisterRequest(BaseModel):
    post_id: str
    clip_id: str = ""
    platform: str = "instagram"
    scheduled_at: str = ""
    pillar: str = ""
    style: str = ""
    format_id: str = ""
    hook_signal: str = ""
    predicted_score: int = 0
    creator_id: str = "default"


class MetricsIngestRequest(BaseModel):
    post_id: str
    creator_id: str = "default"
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    reach: int = 0
    avg_watch_pct: float = 0.0
    follows_gained: int = 0


class ConverseRequest(BaseModel):
    creator_id: str = "default"
    mode: str = "chat"                 # chat | voice
    messages: list[dict] = []          # [{role: user|assistant, content: str}] recent window
    brand: dict = {}
    memory: dict = {}                  # client-held creator memory document
    attachments: list[dict] = []       # [{type: "video_link", url}] — analyzed in Phase 1


class TTSRequest(BaseModel):
    text: str = ""
    voice_id: str = ""


# ---------------------------------------------------------------------------
# Deterministic mock fallbacks (keyless dev / offline)
# ---------------------------------------------------------------------------

def mock_pillars(b: dict) -> list[dict]:
    niche = b.get("niche") or "your field"
    aud = (b.get("audience") or "your audience").lower()
    known = b.get("known_for") or niche
    seeds = [
        ("Teach the fundamentals", f"Lessons that make {aud} better at {niche}.",
         f"You break {known} into steps {aud} can copy today.",
         [f"The {niche} mistake most beginners make", f"A 60-second framework for {known.lower()}",
          f"What I wish I knew about {niche} on day one"]),
        ("Myth-busting", f"Correcting what {aud} get wrong about {niche}.",
         f"You call out popular {niche} advice that backfires.",
         [f"The {niche} advice hurting {aud} most", f"“Everyone says this about {niche}” — why it's wrong",
          f"Stop doing this one thing in {niche}"]),
        ("Behind the scenes", f"The real story of {b.get('what_you_do','what you do').lower()}.",
         "You show the messy middle, not the highlight reel.",
         [f"A day in the life of {niche}", f"The part of {niche} nobody shows you", f"How I actually {known.lower()}"]),
        ("Hot takes", f"Opinions that start conversations in {niche}.",
         f"You stake a position {aud} will share or argue with.",
         [f"My most controversial {niche} opinion", f"An unpopular truth about {niche}",
          f"Why most {aud} are wrong about {known.lower()}"]),
        ("Proof & results", f"Receipts and transformations in {niche}.",
         f"You show outcomes so {aud} trust the method.",
         [f"A before/after that proves {known.lower()} works", f"The result that changed how I see {niche}",
          f"Walk through a real {niche} win step by step"]),
    ]
    colors = [0x2C6BED, 0x2F9E60, 0x9A6A55, 0x8A6FA0, 0xB5791C]
    return [{"name": n, "summary": s, "angle": a, "exampleTopics": t,
             "weight": 0.2, "colorHex": colors[i % len(colors)]}
            for i, (n, s, a, t) in enumerate(seeds)]


def mock_scripts(req: ScriptRequest) -> list[dict]:
    s = STYLES.get(req.style, STYLES["talking_head"])
    niche = req.niche or "your craft"
    out = []
    for i in range(max(1, req.count)):
        fmt = s["formats"][i % len(s["formats"])]
        topic = (req.example_topics[i % len(req.example_topics)] if req.example_topics
                 else f"the {niche} mistake #{i + 1}")
        out.append({
            "title": topic[:48],
            "summary": f"A {s['label'].lower()} on {niche}.",
            "hook": f"Stop overthinking {niche}. Here's what actually works.",
            "hookSignal": "contrarian", "formatId": fmt,
            "body": f"[{s['label']}] Open on the hook. Give the core idea about {niche}, back it with one specific, land the lesson.",
            "cta": "Follow for more — I post this every week.",
            "shotPlan": s["exemplar"] and ["Hook on frame 1", "One punch-in", "CTA"],
            "targetSeconds": 24, "predictedScore": 80,
            "altHooks": [], "style": req.style,
        })
    return out


def mock_derive(b: dict, posts: list[dict]) -> dict:
    return {"niche": b.get("niche", ""), "audience": b.get("audience", ""),
            "knownFor": b.get("known_for", ""),
            "voice": {"funnyToSerious": 0.5, "polishedToRaw": 0.5, "teacherToPeer": 0.5},
            "bannedWords": [], "catchphrases": [], "pillars": mock_pillars(b)}


def mock_trends(niche: str) -> list[dict]:
    n = niche or "your niche"
    return [
        {"title": f"Myth-busting is spiking in {n}", "why": "Contrarian hooks are over-indexing on shares this week.", "formatId": "myth-buster"},
        {"title": f"“I did X for 30 days” experiments", "why": f"Receipt-driven {n} experiments are pulling huge saves — proof beats opinion right now.", "formatId": "before-after"},
        {"title": "“Do this, not that” splits", "why": "Side-by-side comparisons are getting high rewatch.", "formatId": "do-this-not-that"},
        {"title": "Faceless explainers", "why": "AI-visual voiceovers are cheap to test and trending.", "formatId": "faceless"},
        {"title": f"Green-screen reacts to bad {n} advice", "why": "Reacting to viral misinformation is an easy authority play with built-in stakes.", "formatId": "green-screen"},
        {"title": "Rapid-fire listicles under 25s", "why": "Sub-25-second fast-cut lists are looping — completion rate is the whole game.", "formatId": "listicle"},
    ]


# ---------------------------------------------------------------------------
# AI core (with the generate-then-judge specificity gate)
# ---------------------------------------------------------------------------

async def judge_and_fix_pillars(brand: dict, pillars: list[dict], posts: list[dict] | None) -> list[dict]:
    """Reject generic pillars; regenerate 2 candidates in parallel and pick the better one."""
    for _ in range(2):
        # Generate 2 candidate sets in parallel
        sys1, usr1 = prompts.pillars_prompt(brand, posts)
        sys2, usr2 = prompts.pillars_prompt(brand, posts)
        results = await asyncio.gather(
            anthropic(sys1, usr1, OPUS, 1800),
            anthropic(sys2, usr2, OPUS, 1800),
            return_exceptions=True
        )
        candidate_sets = []
        for r in results:
            if isinstance(r, str):
                p = extract_json(r, array=True)
                if p:
                    candidate_sets.append(p)
        if not candidate_sets:
            return pillars
        # Judge the first candidate set
        all_to_judge = candidate_sets[0]
        jsys, jusr = prompts.pillar_judge_prompt(brand.get("niche", ""), all_to_judge)
        verdicts = extract_json(await anthropic(jsys, jusr, HAIKU, 800), array=True) or []
        failed = [all_to_judge[v["index"]].get("name", "")
                  for v in verdicts
                  if isinstance(v, dict) and not v.get("pass", True) and 0 <= v.get("index", -1) < len(all_to_judge)]
        if not failed:
            return all_to_judge
        # Try the second candidate set if we have one
        if len(candidate_sets) > 1:
            jsys2, jusr2 = prompts.pillar_judge_prompt(brand.get("niche", ""), candidate_sets[1])
            verdicts2 = extract_json(await anthropic(jsys2, jusr2, HAIKU, 800), array=True) or []
            failed2 = [candidate_sets[1][v["index"]].get("name", "")
                       for v in verdicts2
                       if isinstance(v, dict) and not v.get("pass", True) and 0 <= v.get("index", -1) < len(candidate_sets[1])]
            if len(failed2) < len(failed):
                return candidate_sets[1]
        pillars = all_to_judge
    return pillars


async def generate_pillars(brand: dict, posts: list[dict] | None) -> tuple[str, list[dict]]:
    if not ANTHROPIC_KEY:
        return "mock", mock_pillars(brand)
    try:
        sys, usr = prompts.pillars_prompt(brand, posts)
        pillars = extract_json(await anthropic(sys, usr, OPUS, 1800), array=True)
        if not pillars:
            return "mock", mock_pillars(brand)
        pillars = await judge_and_fix_pillars(brand, pillars, posts)
        return "live", pillars
    except HTTPException:
        return "mock", mock_pillars(brand)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ready", "version": app.version,
            "ai": "live" if ANTHROPIC_KEY else "mock",
            "scrape": "live" if (BRIGHTDATA_KEY or APIFY_KEY) else "mock",
            "publish": "live" if AYRSHARE_KEY else "mock"}


@app.post("/v1/pillars")
async def pillars(req: PillarRequest):
    mode, p = await generate_pillars(req.d(), req.posts or None)
    return {"mode": mode, "pillars": p}


@app.post("/v1/scripts")
async def scripts(req: ScriptRequest):
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "scripts": mock_scripts(req)}
    pillar = {"name": req.pillar, "summary": req.pillar_summary,
              "angle": req.pillar_angle, "exampleTopics": req.example_topics}
    try:
        stats = list(_arm_stats.get(req.creator_id, {}).values())
        sys, usr = prompts.scripts_prompt(req.d(), pillar, req.style, req.count,
                                          req.media_context, req.posts or None,
                                          arm_stats=stats)
        out = extract_json(await anthropic(sys, usr, OPUS, 3800), array=True)
        return {"mode": "live", "scripts": out or mock_scripts(req)}
    except HTTPException:
        return {"mode": "mock", "scripts": mock_scripts(req)}


@app.post("/v1/hooks")
async def hooks(req: HooksRequest):
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "hooks": [{"text": f"The {req.topic} mistake nobody warns you about", "signal": "curiosity", "strength": 82}]}
    try:
        stats = list(_arm_stats.get(req.creator_id, {}).values())
        sys, usr = prompts.hooks_prompt(req.d(), req.topic, req.style, arm_stats=stats)
        out = extract_json(await anthropic(sys, usr, OPUS, 1200), array=True)
        return {"mode": "live", "hooks": out or []}
    except HTTPException:
        return {"mode": "mock", "hooks": []}


@app.post("/v1/steer")
async def steer(req: SteerRequest):
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "script": req.script}
    try:
        sys, usr = prompts.steer_prompt(req.d(), req.script, req.instruction)
        out = extract_json(await anthropic(sys, usr, SONNET, 1500), array=False)
        return {"mode": "live", "script": out or req.script}
    except HTTPException:
        return {"mode": "mock", "script": req.script}


@app.post("/v1/captions")
async def captions(req: CaptionRequest):
    def chunk(t):
        w, lines, cur = t.split(), [], []
        for x in w:
            cur.append(x)
            if len(cur) >= 5:
                lines.append(" ".join(cur)); cur = []
        if cur:
            lines.append(" ".join(cur))
        return lines
    if ANTHROPIC_KEY:
        try:
            sys, usr = prompts.captions_prompt(req.hook, req.body)
            out = extract_json(await anthropic(sys, usr, HAIKU, 800), array=True)
            if out:
                return {"mode": "live", "lines": out}
        except HTTPException:
            pass
    sentences = [req.hook] + [s.strip() for s in req.body.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    return {"mode": "mock", "lines": [ln for s in sentences if s for ln in chunk(s)]}


@app.post("/v1/teardown")
async def teardown(req: TeardownRequest):
    score = req.clip.get("predictedScore", 70)
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "headline": f"This beat {20 + score % 60}% of your posts",
                "detail": "The hook landed in 2 seconds and the format kept a visual change every few seconds.",
                "liftPercent": 20 + score % 60}
    try:
        sys, usr = prompts.teardown_prompt(req.clip)
        out = extract_json(await anthropic(sys, usr, OPUS, 500), array=False) or {}
        return {"mode": "live", "headline": out.get("headline", ""), "detail": out.get("detail", ""),
                "liftPercent": out.get("liftPercent", 30)}
    except HTTPException:
        return {"mode": "mock", "headline": "", "detail": "", "liftPercent": 30}


@app.post("/v1/insights")
async def insights(req: InsightsRequest):
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "coaching": "Your contrarian hooks are outperforming. Make two more in whichever format spiked."}
    try:
        sys, usr = prompts.insights_prompt(req.d(), req.summary)
        txt = (await anthropic(sys, usr, HAIKU, 250)).strip()
        return {"mode": "live", "coaching": txt}
    except HTTPException:
        return {"mode": "mock", "coaching": ""}


@app.get("/v1/trends")
async def trends(niche: str = ""):
    # TODO(task#6): serve from trends_cache populated by the scrape job. For now: niche-aware,
    # with a Haiku-written "why" when keyed.
    base = mock_trends(niche)
    return {"mode": "mock", "trends": base}


# ---------------------------------------------------------------------------
# Media upload + clip pipeline
# ---------------------------------------------------------------------------

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET", "marque-media")
R2_PUBLIC_BASE = os.environ.get("R2_PUBLIC_BASE", "https://media.marque.app")
ASSEMBLY_KEY = os.environ.get("ASSEMBLYAI_KEY", "")
REMOTION_SERVE_URL = os.environ.get("REMOTION_SERVE_URL", "")
REMOTION_ACCESS_KEY = os.environ.get("REMOTION_AWS_ACCESS_KEY_ID", "")
REMOTION_SECRET = os.environ.get("REMOTION_AWS_SECRET_ACCESS_KEY", "")

# In-memory job store (replaced by Supabase clip_jobs in Phase 4)
_clip_jobs: dict[str, dict] = {}


@app.post("/v1/uploads/mint")
async def mint_upload_url(req: UploadMintRequest):
    if not R2_ACCESS_KEY:
        key = f"mock/{uuid.uuid4()}/{req.filename}"
        return {"mode": "mock", "upload_url": f"https://mock-r2.example.com/{key}",
                "key": key, "public_url": f"{R2_PUBLIC_BASE}/{key}"}
    import hmac, hashlib, datetime
    key = f"uploads/{uuid.uuid4()}/{req.filename}"
    public_url = f"{R2_PUBLIC_BASE}/{key}"
    endpoint = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com/{R2_BUCKET}/{key}"
    now = datetime.datetime.utcnow()
    date_str = now.strftime("%Y%m%dT%H%M%SZ")
    date_short = now.strftime("%Y%m%d")
    region = "auto"
    service = "s3"
    scope = f"{date_short}/{region}/{service}/aws4_request"
    canonical = (f"PUT\n/{R2_BUCKET}/{key}\n"
                 f"X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential={R2_ACCESS_KEY}%2F{scope}"
                 f"&X-Amz-Date={date_str}&X-Amz-Expires=3600&X-Amz-SignedHeaders=content-type%3Bhost\n"
                 f"host:{R2_ACCOUNT_ID}.r2.cloudflarestorage.com\ncontent-type:{req.content_type}\n\n"
                 f"content-type;host\nUNSIGNED-PAYLOAD")
    str_to_sign = (f"AWS4-HMAC-SHA256\n{date_str}\n{scope}\n"
                   + hashlib.sha256(canonical.encode()).hexdigest())
    def _hmac(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()
    signing_key = _hmac(_hmac(_hmac(_hmac(
        f"AWS4{R2_SECRET_KEY}".encode(), date_short), region), service), "aws4_request")
    sig = hmac.new(signing_key, str_to_sign.encode(), hashlib.sha256).hexdigest()
    upload_url = (f"{endpoint}?X-Amz-Algorithm=AWS4-HMAC-SHA256"
                  f"&X-Amz-Credential={R2_ACCESS_KEY}%2F{scope}"
                  f"&X-Amz-Date={date_str}&X-Amz-Expires=3600"
                  f"&X-Amz-SignedHeaders=content-type%3Bhost&X-Amz-Signature={sig}")
    return {"mode": "live", "upload_url": upload_url, "key": key, "public_url": public_url}


def _apply_edit_prefs(edl: dict, prefs: dict) -> dict:
    """Post-process an EDL per the creator's editing preferences."""
    if not edl or not prefs:
        return edl
    if prefs.get("auto_captions") is False:
        edl["captions"] = []
    style = prefs.get("caption_style")
    if style in ("clean", "bold-word", "karaoke") and edl.get("captions") is not None:
        edl.setdefault("caption_style", style)
    trim = prefs.get("filler_trim")
    if trim == "off":
        edl["drops"] = []
    elif trim == "aggressive" and edl.get("drops") is not None:
        # tighten: mark every drop, and flag the EDL so the renderer trims gaps > 200ms
        edl.setdefault("trim_aggressiveness", "aggressive")
    return edl


@app.post("/v1/clips")
async def create_clip_job(req: ClipJobRequest):
    """Create a clip editing job. Returns immediately with job_id; pipeline runs async."""
    job_id = str(uuid.uuid4())
    clips = [{"clip_id": str(uuid.uuid4()), "format": f, "status": "queued"}
             for f in (req.formats or ["myth-buster"])]
    job = {
        "job_id": job_id, "source_id": req.source_id, "status": "transcribing",
        "clips": clips, "script": req.script, "style": req.style,
        "brand": req.brand, "media_context": req.media_context,
        "source_url": req.source_url, "edl": None, "error": None,
        "edit_prefs": req.edit_prefs or {},
    }
    _clip_jobs[job_id] = job
    if not ASSEMBLY_KEY:
        job["status"] = "mock_ready"
        for c in clips: c["status"] = "ready"
        job["edl"] = _apply_edit_prefs(_mock_edl(req.style, req.script), job["edit_prefs"])
        return {"mode": "mock", "job_id": job_id, "clips": clips}
    asyncio.create_task(_run_pipeline(job_id))
    return {"mode": "live", "job_id": job_id, "clips": clips}


@app.get("/v1/clips/{job_id}")
async def get_clip_job(job_id: str):
    if job_id not in _clip_jobs:
        raise HTTPException(status_code=404, detail="job not found")
    job = _clip_jobs[job_id]
    return {
        "mode": "mock" if job["status"] == "mock_ready" else "live",
        "job_id": job_id,
        "status": job["status"],
        "clips": job["clips"],
        "edl": job.get("edl"),
        "error": job.get("error"),
    }


def _mock_edl(style: str, script: dict) -> dict:
    """Deterministic mock EDL for dev/test."""
    return {
        "style": style, "format_id": script.get("formatId", "myth-buster"),
        "segments": [{"src_in": 0, "src_out": 720}],
        "drops": [{"src_in": 45, "src_out": 51, "reason": "filler"}],
        "captions": [{"word": w, "frame": i*20}
                     for i, w in enumerate(script.get("hook", "Great hook").split()[:8])],
        "overlays": [{"type": "punch_in", "src_in": 90, "src_out": 150, "scale": 1.08, "text": ""}],
        "broll": [], "layout": {"style": style, "panels": 1 if style != "split_three" else 3,
                                "panel_boundaries": [240, 480] if style == "split_three" else []},
        "audio": {"lufs_target": -14.0},
    }


async def _run_pipeline(job_id: str):
    """Background pipeline: transcribe → edit → render."""
    job = _clip_jobs[job_id]
    try:
        job["status"] = "transcribing"
        for c in job["clips"]: c["status"] = "transcribing"
        transcript_id = await _submit_transcription(job["source_url"])
        if not transcript_id:
            raise RuntimeError("transcription submit failed")
        words = await _poll_transcription(transcript_id)

        job["status"] = "editing"
        for c in job["clips"]: c["status"] = "editing"
        style = job["style"]
        script = job["script"]
        system, user = prompts.edl_prompt(style, words, script, job["brand"], job["media_context"])
        prefs = job.get("edit_prefs") or {}
        if prefs:
            hints = []
            if prefs.get("auto_captions") is False:
                hints.append("The creator has captions OFF — output an empty captions array.")
            if prefs.get("caption_style") in ("clean", "bold-word", "karaoke"):
                hints.append(f"Caption style: {prefs['caption_style']}.")
            trim = prefs.get("filler_trim")
            if trim == "off":
                hints.append("Filler trimming is OFF — output an empty drops array.")
            elif trim == "aggressive":
                hints.append("Filler trimming is AGGRESSIVE — also drop dead-air gaps > 200ms and hesitations.")
            if hints:
                user += "\n\nCREATOR EDIT PREFERENCES:\n" + "\n".join(f"- {h}" for h in hints)
        edl_text = await anthropic(system, user, model=HAIKU, max_tokens=4000)
        edl_data = extract_json(edl_text, array=False)

        if edl_data:
            try:
                edl_obj = EDL(**edl_data)
                edl_obj, issues = validate_and_repair(edl_obj)
                edl_data = edl_obj.model_dump()
            except Exception:
                total_frames = ms_to_frame(max((w.get("end_ms", 0) for w in words), default=30000))
                edl_obj = safe_default_edl(style, script.get("formatId", "myth-buster"), total_frames, words)
                edl_data = edl_obj.model_dump()
        else:
            total_frames = ms_to_frame(max((w.get("end_ms", 0) for w in words), default=30000))
            edl_obj = safe_default_edl(style, script.get("formatId", "myth-buster"), total_frames, words)
            edl_data = edl_obj.model_dump()

        job["edl"] = _apply_edit_prefs(edl_data, prefs)

        job["status"] = "rendering"
        for c in job["clips"]: c["status"] = "rendering"

        if REMOTION_SERVE_URL and REMOTION_ACCESS_KEY:
            for clip in job["clips"]:
                render_id = await _submit_remotion_render(
                    job["source_url"], edl_data, clip["format"], job["style"])
                if render_id:
                    clip["render_id"] = render_id
                    render_url = await _poll_remotion_render(render_id)
                    clip["render_url"] = render_url
                    clip["status"] = "ready" if render_url else "failed"
                else:
                    clip["status"] = "failed"
        else:
            for clip in job["clips"]:
                clip["status"] = "ready"
                clip["render_url"] = job["source_url"]

        job["status"] = "ready"
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        for c in job["clips"]: c["status"] = "failed"


async def _submit_transcription(video_url: str) -> str | None:
    if not ASSEMBLY_KEY:
        return None
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.assemblyai.com/v2/transcript",
            headers={"authorization": ASSEMBLY_KEY},
            json={"audio_url": video_url, "auto_highlights": True, "speaker_labels": False},
        )
    if r.status_code != 200:
        return None
    return r.json().get("id")


async def _poll_transcription(transcript_id: str, max_wait_s: int = 300) -> list[dict]:
    if not ASSEMBLY_KEY:
        return []
    for _ in range(max_wait_s // 5):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
                headers={"authorization": ASSEMBLY_KEY},
            )
        data = r.json()
        if data.get("status") == "completed":
            return data.get("words", [])
        if data.get("status") == "error":
            return []
    return []


async def _submit_remotion_render(source_url: str, edl: dict, format_id: str, style: str) -> str | None:
    if not REMOTION_SERVE_URL:
        return None
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.remotion.dev/renders",
            headers={"Authorization": f"Bearer {REMOTION_ACCESS_KEY}",
                     "Content-Type": "application/json"},
            json={
                "serveUrl": REMOTION_SERVE_URL,
                "composition": f"Marque_{style.title().replace('_', '')}",
                "inputProps": {"sourceUrl": source_url, "edl": edl, "formatId": format_id},
                "codec": "h264", "outputFormat": "mp4",
            },
        )
    if r.status_code not in (200, 201):
        return None
    return r.json().get("renderId") or r.json().get("id")


async def _poll_remotion_render(render_id: str, max_wait_s: int = 600) -> str | None:
    for _ in range(max_wait_s // 10):
        await asyncio.sleep(10)
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://api.remotion.dev/renders/{render_id}",
                headers={"Authorization": f"Bearer {REMOTION_ACCESS_KEY}"},
            )
        data = r.json()
        if data.get("status") == "done":
            return data.get("outputUrl") or data.get("url")
        if data.get("status") in ("failed", "error"):
            return None
    return None


# ----- brand-scan + voice onboarding -----

async def scrape_posts(handle: str, platform: str) -> list[dict]:
    """Real scrape when keyed (Bright Data / Apify); else empty (caller supplies posts for testing)."""
    if not (BRIGHTDATA_KEY or APIFY_KEY) or not handle:
        return []
    # Structural Bright Data call — wired to the dataset trigger when the key is present.
    # (Left as the single integration point; returns [] until provisioned.)
    return []


@app.post("/v1/brand-scan/handle")
async def brand_scan_handle(req: ScanRequest):
    posts = req.posts or await scrape_posts(req.handle, req.platform)
    brand = req.d()
    if not ANTHROPIC_KEY or not posts:
        # No evidence (or no key) → niche-aware fallback so onboarding never dead-ends.
        return {"mode": "mock", "scanned_posts": len(posts), "scan": mock_derive(brand, posts)}
    try:
        sys, usr = prompts.derive_from_posts_prompt(brand, posts)
        derived = extract_json(await anthropic(sys, usr, OPUS, 2200), array=False) or mock_derive(brand, posts)
        if derived.get("pillars"):
            merged = {**brand, "niche": derived.get("niche", brand.get("niche", ""))}
            derived["pillars"] = await judge_and_fix_pillars(merged, derived["pillars"], posts)
        return {"mode": "live", "scanned_posts": len(posts), "scan": derived}
    except HTTPException:
        return {"mode": "mock", "scanned_posts": len(posts), "scan": mock_derive(brand, posts)}


@app.post("/v1/voice-onboarding/session")
async def voice_session(req: VoiceSessionRequest):
    """Mint an ElevenLabs Conversational AI session token so the key never ships to the app."""
    agent_id = os.environ.get("ELEVENLABS_AGENT_ID", "")
    el_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not (agent_id and el_key):
        return {"mode": "mock", "agent_system": prompts.VOICE_AGENT_SYSTEM,
                "conversation_token": "", "agent_id": "", "session_id": uuid.uuid4().hex}
    # Real token mint (ElevenLabs get-signed-url) is the single integration point here.
    return {"mode": "live", "agent_id": agent_id, "conversation_token": "",
            "session_id": uuid.uuid4().hex}


@app.post("/v1/voice-onboarding/finalize")
async def voice_finalize(req: VoiceFinalizeRequest):
    brand = req.d()
    if not ANTHROPIC_KEY or not req.transcript:
        return {"mode": "mock", "scan": mock_derive(brand, [])}
    try:
        sys, usr = prompts.voice_finalize_prompt(brand, req.transcript)
        derived = extract_json(await anthropic(sys, usr, OPUS, 2200), array=False) or mock_derive(brand, [])
        if derived.get("pillars"):
            merged = {**brand, "niche": derived.get("niche", brand.get("niche", ""))}
            derived["pillars"] = await judge_and_fix_pillars(merged, derived["pillars"], None)
        return {"mode": "live", "scan": derived}
    except HTTPException:
        return {"mode": "mock", "scan": mock_derive(brand, [])}


# ----- connect Instagram / TikTok (verify a link by fetching the real public profile) -----

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def _count(s: str) -> int:
    s = s.replace(",", "").strip()
    mult = 1.0
    if s and s[-1] in "KMB":
        mult = {"K": 1e3, "M": 1e6, "B": 1e9}[s[-1]]
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def _unesc(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except json.JSONDecodeError:
        return s


class ConnectPreviewRequest(BaseModel):
    handle: str = ""
    platform: str = "tiktok"


async def _fetch(url: str) -> str:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get(url, headers={"User-Agent": MOBILE_UA})
    return r.text if r.status_code == 200 else ""


async def preview_tiktok(handle: str) -> dict:
    try:
        html = await _fetch(f"https://www.tiktok.com/@{handle}")
    except httpx.HTTPError:
        html = ""
    f = re.search(r'"followerCount":(\d+)', html)
    a = re.search(r'"avatarLarger":"([^"]+)"', html) or re.search(r'"avatarMedium":"([^"]+)"', html)
    n = re.search(r'"nickname":"([^"]+)"', html)
    b = re.search(r'"signature":"([^"]*)"', html)
    if not (f or a):
        return {"found": False, "platform": "tiktok", "handle": handle}
    return {"found": True, "platform": "tiktok", "handle": handle,
            "displayName": _unesc(n.group(1)) if n else handle,
            "followers": int(f.group(1)) if f else 0,
            "avatarUrl": _unesc(a.group(1)) if a else "",
            "bio": _unesc(b.group(1)) if b else ""}


async def preview_instagram(handle: str) -> dict:
    try:
        html = await _fetch(f"https://www.instagram.com/{handle}/")
    except httpx.HTTPError:
        html = ""
    img = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    desc = re.search(r'<meta property="og:description" content="([^"]+)"', html)
    if not (img or desc):
        return {"found": False, "platform": "instagram", "handle": handle}
    followers, name = 0, handle
    if desc:
        d = desc.group(1)
        fm = re.search(r'([\d.,]+[KMB]?)\s+Followers', d)
        if fm:
            followers = _count(fm.group(1))
        nm = re.search(r'from (.+?) \(@', d)
        if nm:
            name = nm.group(1)
    return {"found": True, "platform": "instagram", "handle": handle,
            "displayName": name, "followers": followers,
            "avatarUrl": img.group(1).replace("&amp;", "&") if img else "", "bio": ""}


@app.post("/v1/connect/preview")
async def connect_preview(req: ConnectPreviewRequest):
    """Verify a creator's IG/TikTok link by fetching their real public profile."""
    handle = req.handle.lstrip("@").strip()
    if not handle:
        return {"found": False}
    if req.platform == "instagram":
        return await preview_instagram(handle)
    return await preview_tiktok(handle)


# ----- publishing (phase 2; kept so the surface is complete) -----

class PublishRequest(BaseModel):
    caption: str = ""
    media_url: str = ""
    platforms: list[str] = []
    schedule_date: str = ""


@app.post("/v1/publish")
async def publish(req: PublishRequest):
    if not AYRSHARE_KEY:
        return {"ok": True, "mode": "mock", "id": f"post_{uuid.uuid4().hex[:10]}"}
    body = {"post": req.caption, "platforms": req.platforms}
    if req.schedule_date:
        body["scheduleDate"] = req.schedule_date
    if req.media_url.startswith("http"):
        body["mediaUrls"] = [req.media_url]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post("https://api.ayrshare.com/api/post",
                                  headers={"Authorization": f"Bearer {AYRSHARE_KEY}"}, json=body)
        return {"ok": 200 <= r.status_code < 300, "mode": "live", "status": r.status_code}
    except httpx.HTTPError:
        return {"ok": False, "mode": "live", "error": "network"}


# ---------------------------------------------------------------------------
# Phase 3: Media analysis + auto B-roll
# ---------------------------------------------------------------------------

@app.post("/v1/media/analyze")
async def analyze_media(req: MediaAnalyzeRequest):
    """Analyze a media asset for B-roll suitability. Idempotent via content_hash cache."""
    if req.content_hash in _media_cache:
        return {"mode": "cached", **_media_cache[req.content_hash]}

    if not ANTHROPIC_KEY or not req.public_url:
        mock = {
            "description": f"A {req.kind} asset suitable for B-roll use.",
            "scene": "indoor", "subjects": ["person", "environment"], "has_face": False,
            "on_screen_text": "", "motion": "slow", "quality": "high",
            "dominant_colors": ["warm white", "natural", "neutral"],
            "broll_suitability": 72, "broll_suitability_reason": "Good framing for B-roll.",
            "usable_as": "broll", "suggested_kind": req.kind,
            "tags": [req.kind, "interior", "natural light", "close-up", "lifestyle"],
        }
        _media_cache[req.content_hash] = mock
        return {"mode": "mock", **mock}

    system, user_text = prompts.media_analyze_prompt(req.filename, req.kind)
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            ANTHROPIC_URL,
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": HAIKU, "max_tokens": 1000, "system": system,
                  "messages": [{"role": "user", "content": [
                      {"type": "image", "source": {"type": "url", "url": req.public_url}},
                      {"type": "text", "text": user_text},
                  ]}]},
        )
    if r.status_code != 200:
        return {"mode": "mock", "error": f"vision {r.status_code}"}
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    result = extract_json(text, array=False) or {}
    _media_cache[req.content_hash] = result
    return {"mode": "live", **result}


@app.post("/v1/broll/match")
async def match_broll(req: BRollMatchRequest):
    """Score corpus assets against a shot-plan beat; optionally use Haiku for tie-breaking."""
    if not req.corpus:
        return {"mode": "mock", "matches": []}

    cue_lower = req.cue_text.lower()
    scored = []
    for i, asset in enumerate(req.corpus):
        desc = (asset.get("description", "") + " " + " ".join(asset.get("tags", []))).lower()
        keyword_hits = sum(1 for word in cue_lower.split() if len(word) > 3 and word in desc)
        suitability = asset.get("broll_suitability", 50) / 100.0
        score = 0.55 * min(1.0, keyword_hits / max(1, len(cue_lower.split()))) + 0.45 * suitability
        scored.append({"index": i, "asset_id": asset.get("asset_id", ""), "score": round(score, 3),
                        "description": asset.get("description", "")})

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:req.top_k]

    # Haiku tie-break when scores are close and we have a key
    if ANTHROPIC_KEY and len(top) >= 2 and top[0]["score"] - top[1]["score"] < 0.05:
        system, user = prompts.broll_match_prompt(req.cue_text, top[:3])
        try:
            text = await anthropic(system, user, model=HAIKU, max_tokens=100)
            pick = extract_json(text, array=False)
            if pick and "chosen_index" in pick:
                chosen = scored[pick["chosen_index"]]
                top = [chosen] + [t for t in top if t["asset_id"] != chosen["asset_id"]]
        except Exception:
            pass

    # Pexels fallback for unmatched beats
    if not top or top[0]["score"] < 0.3:
        pexels = await _fetch_pexels(req.cue_text)
        top = [{"asset_id": None, "source": "pexels", "pexels_url": pexels,
                "score": 0.5, "description": req.cue_text}] + top

    return {"mode": "live" if ANTHROPIC_KEY else "mock", "matches": top}


async def _fetch_pexels(query: str) -> str | None:
    if not PEXELS_KEY:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get("https://api.pexels.com/videos/search",
                             headers={"Authorization": PEXELS_KEY},
                             params={"query": query, "per_page": 1, "orientation": "portrait"})
    if r.status_code != 200:
        return None
    videos = r.json().get("videos", [])
    if not videos:
        return None
    files = videos[0].get("video_files", [])
    hd = next((f for f in files if f.get("quality") == "hd"), files[0] if files else None)
    return hd.get("link") if hd else None


# ---------------------------------------------------------------------------
# Phase 4: Learning loop routes
# ---------------------------------------------------------------------------

@app.post("/v1/posts/register")
async def register_post(req: PostRegisterRequest):
    """Register a scheduled post as a learning experiment."""
    if req.post_id in _post_registry:
        return {"mode": "mock", "status": "already_registered"}
    _post_registry[req.post_id] = {
        "creator_id": req.creator_id,
        "platform": req.platform, "scheduled_at": req.scheduled_at,
        "pillar": req.pillar, "style": req.style,
        "format_id": req.format_id, "hook_signal": req.hook_signal,
        "predicted_score": req.predicted_score,
        "outcome_y": None, "settled": False, "metrics": None,
    }
    return {"mode": "live" if SUPABASE_URL else "mock", "status": "registered", "post_id": req.post_id}


@app.post("/v1/metrics/ingest")
async def ingest_metrics(req: MetricsIngestRequest):
    """Ingest post metrics and update the learning bandit (idempotent on post_id)."""
    entry = _post_registry.get(req.post_id, {})
    if entry.get("settled"):
        return {"mode": "mock", "status": "already_settled"}
    if req.reach < 20:
        return {"mode": "mock", "status": "below_min_reach", "reach": req.reach}

    m = req.model_dump()
    y = _compute_y(m)
    creator_id = req.creator_id

    for dim in DIMENSIONS:
        val = entry.get(dim, "")
        if val:
            _update_arm(creator_id, f"{dim}:{val}", y)

    entry["outcome_y"] = y
    entry["settled"] = True
    entry["metrics"] = m
    _post_registry[req.post_id] = entry

    return {"mode": "live" if SUPABASE_URL else "mock", "status": "ingested",
            "outcome_y": round(y, 3), "post_id": req.post_id}


@app.get("/v1/recommendations")
async def get_recommendations(niche: str = "", creator_id: str = "default"):
    """Return top 3 Thompson-sampled arms for the creator's home feed."""
    stats = _arm_stats.get(creator_id, {})

    if not stats:
        return {"mode": "mock", "arms": [
            {"pillar": "Myth-busting", "style": "talking_head", "reason": "Top performer in your niche"},
            {"pillar": "Teach the fundamentals", "style": "faceless", "reason": "High saves for faceless content"},
            {"pillar": "Hot takes", "style": "fast_cuts", "reason": "Fast-cuts trend spiking"},
        ]}

    styles = ["talking_head", "faceless", "split_three", "fast_cuts", "green_screen"]
    pillars = list(set(
        k.split(":", 1)[1] for k in stats if k.startswith("pillar:")
    )) or ["Myth-busting", "Teach the fundamentals", "Hot takes"]

    sampled_styles = _thompson_sample(creator_id, [f"style:{s}" for s in styles])
    sampled_pillars = _thompson_sample(creator_id, [f"pillar:{p}" for p in pillars])

    arms = []
    for i in range(min(3, len(sampled_pillars))):
        pillar_key, pillar_score = sampled_pillars[i]
        style_key, style_score = sampled_styles[i % len(sampled_styles)]
        pillar = pillar_key.replace("pillar:", "")
        style = style_key.replace("style:", "")
        style_stats = stats.get(style_key, {})
        effect = style_stats.get("effect", 0.5)
        lift = round((effect - 0.5) * 200)
        reason = (f"{style.replace('_', ' ').title()} {'outperforms' if lift > 0 else 'tracks'} "
                  f"your average by {abs(lift)}% ({style_stats.get('confidence', 'early read')})")
        arms.append({"pillar": pillar, "style": style, "score": round(pillar_score + style_score, 3),
                     "reason": reason})

    return {"mode": "live" if SUPABASE_URL else "mock", "arms": arms}


@app.get("/v1/insights/learned")
async def get_learned_insights(creator_id: str = "default"):
    """Return the creator's winning formula derived from arm_stats."""
    stats = _arm_stats.get(creator_id, {})
    if not stats:
        return {"mode": "mock", "insights": [], "posts_learned": 0,
                "winning_formula": None, "learning_progress": 0}

    total_posts = max(s.get("n", 0) for s in stats.values()) if stats else 0

    confirmed = [(k, v) for k, v in stats.items()
                 if v.get("confidence") in ("confirmed", "early_read")]
    confirmed.sort(key=lambda x: x[1].get("effect", 0), reverse=True)

    insights = []
    for k, v in confirmed[:5]:
        dim, val = k.split(":", 1) if ":" in k else ("", k)
        effect = v.get("effect", 0.5)
        lift = round((effect - 0.5) * 200)
        n = v.get("n", 0)
        confidence = v.get("confidence", "early_read")
        if abs(lift) >= 5:
            insights.append({
                "dimension": dim, "value": val,
                "lift_pct": lift, "n_posts": n, "confidence": confidence,
                "label": f"{val.replace('_', ' ').title()}: {'+' if lift>0 else ''}{lift}% vs your average",
            })

    winning = None
    if confirmed:
        top = confirmed[0]
        dim, val = top[0].split(":", 1) if ":" in top[0] else ("", top[0])
        lift = round((top[1].get("effect", 0.5) - 0.5) * 200)
        winning = f"{val.replace('_', ' ').title()} content outperforms your average by {lift}%"

    target = 15
    return {"mode": "live" if SUPABASE_URL else "mock",
            "insights": insights, "posts_learned": total_posts,
            "winning_formula": winning, "learning_progress": min(1.0, total_posts / target)}


# ---------------------------------------------------------------------------
# Conversation engine — the voice bubble + chat brain (client-held memory)
# ---------------------------------------------------------------------------

_VALID_MEMORY_OPS = {"add", "remove", "set"}
_VALID_MEMORY_FIELDS = set(prompts.MEMORY_FIELDS) | {"angle"}
_VALID_INTENTS = {"none", "generate_scripts", "day_plan", "save_idea", "update_brand_angle"}


def _sanitize_memory_updates(raw) -> list[dict]:
    """Keep only well-formed ops so a sloppy envelope can't corrupt client memory."""
    out = []
    for u in (raw or [])[:6]:
        if not isinstance(u, dict):
            continue
        op, field = u.get("op"), u.get("field")
        value = u.get("value")
        if op not in _VALID_MEMORY_OPS or field not in _VALID_MEMORY_FIELDS:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        if field == "angle" and op != "set":
            op = "set"
        out.append({"op": op, "field": field, "value": value.strip()[:280]})
    return out


def _mock_day_plan(brand: dict, memory: dict) -> dict:
    niche = brand.get("niche") or "your niche"
    ideas = [i for i in (memory.get("ideas") or []) if isinstance(i, str)]
    idea1 = ideas[0] if ideas else f"the {niche} mistake everyone makes"
    idea2 = ideas[1] if len(ideas) > 1 else f"a myth-buster on {niche}"
    return {"blocks": [
        {"time": "9:00", "action": "Voice check-in", "detail": "Two minutes: tell Marque today's angle so scripts stay sharp."},
        {"time": "9:30", "action": "Batch-film two scripts", "detail": f"Film \"{idea1}\" and \"{idea2}\" back-to-back while energy is high."},
        {"time": "12:30", "action": "Submit edits", "detail": "Send both takes for AI editing and review yesterday's finished clip."},
        {"time": "17:00", "action": "Post + engage", "detail": "Publish at peak hours, then reply to every comment for 15 minutes."},
        {"time": "20:00", "action": "Log a win", "detail": "Note one thing that worked today — it feeds your learning loop."},
    ]}


def mock_converse(req: ConverseRequest) -> dict:
    """Deterministic, context-echoing conversation for keyless demo mode."""
    last = ""
    for m in reversed(req.messages):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            last = m["content"].strip()
            break
    low = last.lower()
    niche = req.brand.get("niche") or "your niche"
    voice = req.mode == "voice"
    updates: list[dict] = []
    intent, intent_args = "none", {}
    chips = ["Build my day", "Write me a script", "What should I post today?"]

    if any(k in low for k in ("build my day", "build my content", "plan my day", "day plan", "build out my day")):
        intent = "day_plan"
        intent_args = {"plan": _mock_day_plan(req.brand, req.memory)}
        reply = ("Here's your day — front-load the filming while you're fresh, then let the edits run while "
                 "you live your life. Two takes before noon and today compounds.")
        chips = ["Adjust the plan", "Write the first script", "What's trending?"]
    elif "script" in low and any(k in low for k in ("write", "make", "give", "create", "draft", "need")):
        intent = "generate_scripts"
        topic = niche
        for marker in ("about ", "on "):
            if marker in low:
                topic = last[low.index(marker) + len(marker):].strip().rstrip("?.!") or niche
                break
        intent_args = {"topic": topic, "style": "", "count": 1}
        reply = (f"On it — one script on {topic}, in your voice, hook-first. "
                 "It's attached below; save it to your film queue when it feels right.")
        chips = ["Make it punchier", "Give me two more angles", "Add it to my queue"]
    elif any(k in low for k in ("my angle", "brand angle", "direction", "positioning", "reposition")):
        intent = "update_brand_angle"
        updates.append({"op": "set", "field": "angle", "value": last[:280]})
        reply = ("Noted — that's a sharper lane and I've locked it into your brand memory. "
                 "Everything I write from here leans that way. Want a script that plants the flag?")
        chips = ["Write the flag-planting script", "What does this change?", "Build my day"]
    elif any(k in low for k in ("idea", "thinking about", "what if i", "i want to make")):
        intent = "save_idea"
        updates.append({"op": "add", "field": "ideas", "value": last[:280]})
        reply = ("That's worth keeping — saved to your idea bank. "
                 "The specific version of that idea beats the general one; want me to script it?")
        chips = ["Script this idea", "Poke holes in it", "Save and move on"]
    elif any(k in low for k in ("i think", "i believe", "my take", "honestly")):
        updates.append({"op": "add", "field": "perspective", "value": last[:280]})
        reply = ("That perspective is exactly the kind of thing your audience can't get anywhere else — "
                 "I've noted it. Say it on camera the way you just said it to me.")
    elif any(k in low for k in ("what should i post", "post today", "content today")):
        reply = (f"Lead with your strongest lane: one contrarian take on {niche} — hook in the first sentence, "
                 "one specific number, one clear takeaway. Check today's picks on your home screen; "
                 "the top script is ranked for you.")
        chips = ["Write it for me", "Show me the trend", "Build my day"]
    elif not last:
        reply = ("Morning. Tell me what's on your mind — an idea, a frustration, an angle you're chewing on. "
                 "I'll remember what matters and turn the good stuff into content.")
    else:
        updates.append({"op": "add", "field": "facts", "value": last[:280]})
        reply = ("Got it — noted. The more you tell me like this, the sharper your scripts get. "
                 "Anything you want me to turn into a post?")

    if voice:
        reply = reply.split("\n")[0]
    return {"reply": reply, "memory_updates": updates, "intent": intent,
            "intent_args": intent_args, "chips": chips}


async def _chain_scripts(req: ConverseRequest, intent_args: dict) -> list[dict]:
    """generate_scripts intent → run the real scripts engine and attach the results."""
    topic = (intent_args.get("topic") or req.brand.get("niche") or "your next post").strip()
    style = intent_args.get("style") or "talking_head"
    if style not in STYLES:
        style = "talking_head"
    count = max(1, min(3, int(intent_args.get("count") or 1)))
    angle = (req.memory.get("angle") or "").strip()
    sreq = ScriptRequest(
        niche=req.brand.get("niche", ""), audience=req.brand.get("audience", ""),
        known_for=req.brand.get("known_for", ""), what_you_do=req.brand.get("what_you_do", ""),
        goal=req.brand.get("goal", "Grow my audience"), voice=req.brand.get("voice", {}) or {},
        non_negotiables=req.brand.get("non_negotiables", []) or [],
        pillar=topic, pillar_summary=f"A one-off script request from conversation: {topic}",
        pillar_angle=angle, style=style, count=count, creator_id=req.creator_id,
    )
    result = await scripts(sreq)
    return result.get("scripts", [])


@app.post("/v1/converse")
async def converse(req: ConverseRequest):
    if req.mode not in ("chat", "voice"):
        req.mode = "chat"
    if not ANTHROPIC_KEY:
        out = mock_converse(req)
        if out["intent"] == "generate_scripts":
            out["payload"] = {"scripts": await _chain_scripts(req, out.get("intent_args", {}))}
        elif out["intent"] == "day_plan":
            out["payload"] = {"plan": out.get("intent_args", {}).get("plan", {})}
        return {"mode": "mock", "reply": out["reply"], "memory_updates": out["memory_updates"],
                "intent": out["intent"], "payload": out.get("payload"), "suggested_chips": out["chips"]}

    stats = list(_arm_stats.get(req.creator_id, {}).values())
    system = prompts.converse_system(req.mode)
    user = prompts.converse_user(req.brand, req.memory, req.messages,
                                 arm_stats=stats, trends=mock_trends(req.brand.get("niche", "")))
    envelope = None
    try:
        model = SONNET if req.mode == "voice" else OPUS
        raw = await anthropic(system, user, model, 1600)
        envelope = extract_json(raw, array=False)
        if envelope is None:
            logging.warning("converse: envelope parse failed, retrying once")
            raw = await anthropic(system, user + "\n\nREMINDER: output ONLY the JSON envelope.", model, 1600)
            envelope = extract_json(raw, array=False)
    except HTTPException:
        envelope = None
    if not isinstance(envelope, dict) or not (envelope.get("reply") or "").strip():
        out = mock_converse(req)
        return {"mode": "mock", "reply": out["reply"], "memory_updates": out["memory_updates"],
                "intent": out["intent"], "payload": None, "suggested_chips": out["chips"]}

    intent = envelope.get("intent") if envelope.get("intent") in _VALID_INTENTS else "none"
    intent_args = envelope.get("intent_args") if isinstance(envelope.get("intent_args"), dict) else {}
    payload = None
    if intent == "generate_scripts":
        payload = {"scripts": await _chain_scripts(req, intent_args)}
    elif intent == "day_plan":
        plan = intent_args.get("plan")
        payload = {"plan": plan if isinstance(plan, dict) and plan.get("blocks") else _mock_day_plan(req.brand, req.memory)}

    chips = [c for c in (envelope.get("chips") or []) if isinstance(c, str) and c.strip()][:3]
    return {"mode": "live", "reply": envelope["reply"],
            "memory_updates": _sanitize_memory_updates(envelope.get("memory_updates")),
            "intent": intent, "payload": payload, "suggested_chips": chips}


# ---------------------------------------------------------------------------
# TTS proxy — ElevenLabs when keyed; client falls back to AVSpeechSynthesizer
# ---------------------------------------------------------------------------

ELEVENLABS_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_DEFAULT_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
_tts_cache: dict[str, bytes] = {}


@app.post("/v1/tts")
async def tts(req: TTSRequest):
    from fastapi.responses import Response, JSONResponse
    text = (req.text or "").strip()[:1000]
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    if not ELEVENLABS_KEY:
        return JSONResponse({"mode": "mock"})
    import hashlib
    voice = req.voice_id or ELEVENLABS_DEFAULT_VOICE
    key = hashlib.sha256(f"{voice}:{text}".encode()).hexdigest()
    if key in _tts_cache:
        return Response(content=_tts_cache[key], media_type="audio/mpeg")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
                headers={"xi-api-key": ELEVENLABS_KEY, "content-type": "application/json"},
                json={"text": text, "model_id": "eleven_turbo_v2_5"},
            )
        if r.status_code == 200 and r.content:
            if len(_tts_cache) > 64:
                _tts_cache.pop(next(iter(_tts_cache)))
            _tts_cache[key] = r.content
            return Response(content=r.content, media_type="audio/mpeg")
        logging.warning("tts: elevenlabs %d %s", r.status_code, r.text[:200])
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        logging.warning("tts: network error %s", e)
    return JSONResponse({"mode": "mock"})


# ---------------------------------------------------------------------------
# Auth (light) — derive creator_id from an optional bearer token.
# Real enforcement lands with Supabase RLS; for now the sub claim just scopes state.
# ---------------------------------------------------------------------------

def _creator_from_bearer(authorization: str | None, fallback: str) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return fallback
    token = authorization.split(" ", 1)[1].strip()
    parts = token.split(".")
    if len(parts) != 3:
        return fallback
    try:
        import base64
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims.get("sub") or fallback
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# Reels feed — influencer reels to mimic (mock corpus now; Apify/BrightData later)
# ---------------------------------------------------------------------------

class MimicRequest(BaseModel):
    reel: dict = {}
    brand: dict = {}
    memory: dict = {}
    creator_id: str = "default"


class AnalyzeVideoRequest(BaseModel):
    url: str = ""
    brand: dict = {}
    memory: dict = {}
    creator_id: str = "default"


class BrandSummaryRequest(BaseModel):
    brand: dict = {}
    memory: dict = {}
    creator_id: str = "default"


_REEL_TEMPLATES = [
    # (handle_stub, platform, title, hook, transcript_skeleton, why_trending, format_id, style, views, likes)
    ("daily{slug}", "tiktok", "I did {niche} wrong for 3 years",
     "I wasted 3 years on {niche} — these 20 seconds save you the trouble.",
     "Bold confession of the mistake. The moment it clicked. The one change that fixed it. Direct takeaway to copy today.",
     "Confession hooks + a redemption arc are pulling massive completion rates this week.",
     "pov-story", "talking_head", 2_400_000, 310_000),
    ("the{slug}guy", "instagram", "3 {niche} rules I'd tattoo on my arm",
     "Three {niche} rules — the third one nobody says out loud.",
     "Rule one, quick and obvious. Rule two, sharper. Rule three, the contrarian one that reframes the first two.",
     "Numbered rules with a withheld payoff are driving rewatch loops.",
     "listicle", "fast_cuts", 1_800_000, 220_000),
    ("honest{slug}", "tiktok", "Stop doing this in {niche}",
     "If you're doing this in {niche}, stop. It's costing you every week.",
     "Call out the common practice. Show why it backfires with one specific number. Give the replacement behavior.",
     "Direct call-out hooks are over-indexing on shares — people tag friends who do the thing.",
     "do-this-not-that", "talking_head", 3_100_000, 405_000),
    ("{slug}lab", "instagram", "The {niche} myth that won't die",
     "This {niche} myth has two million believers. Here's the receipt it's wrong.",
     "State the myth respectfully. Bring one piece of hard evidence. Land the correct model in one sentence.",
     "Myth-busting with receipts converts skeptics into followers — saves are spiking.",
     "myth-buster", "green_screen", 1_500_000, 190_000),
    ("quiet{slug}", "tiktok", "A day of {niche} in 25 seconds",
     "Nobody shows you the boring part of {niche}. Watch this.",
     "Fast montage of the unglamorous process. One honest line about why it matters. Soft CTA to follow the journey.",
     "Anti-highlight-reel content reads as authentic and is out-performing polished edits.",
     "broll-hook", "faceless", 950_000, 140_000),
    ("{slug}decoded", "instagram", "Before/after: 30 days of {niche}",
     "Day 1 vs day 30 of doing {niche} right — the difference is stupid.",
     "Show the before state with a number. The exact protocol in three beats. The after state with the same number.",
     "30-day receipt experiments are the highest-save format in the niche right now.",
     "before-after", "split_three", 2_700_000, 350_000),
    ("real{slug}talk", "tiktok", "The uncomfortable {niche} truth",
     "Nobody in {niche} wants to say this, so I will.",
     "The uncomfortable claim. Why everyone avoids saying it. The evidence. What to do about it in one line.",
     "Hot takes with evidence are driving comment wars — the algorithm is eating it up.",
     "myth-buster", "talking_head", 4_200_000, 520_000),
]


def _mock_reels(niche: str, watched: list[str]) -> list[dict]:
    n = niche or "your niche"
    slug = "".join(c for c in n.lower().split()[0] if c.isalpha()) or "creator"
    reels = []
    # Watched creators first — 2 reels each, attributed to the actual handle
    for wi, w in enumerate(watched[:2]):
        for ti in (0, 2):
            t = _REEL_TEMPLATES[(wi * 3 + ti) % len(_REEL_TEMPLATES)]
            reels.append(_reel_from_template(t, n, slug, handle_override=w, idx=len(reels), watched=True))
    for i, t in enumerate(_REEL_TEMPLATES):
        reels.append(_reel_from_template(t, n, slug, idx=len(reels)))
    # Second pass with platform flipped for volume (14+ total)
    for i, t in enumerate(_REEL_TEMPLATES[:5]):
        flipped = (t[0] + "s", "instagram" if t[1] == "tiktok" else "tiktok", *t[2:])
        reels.append(_reel_from_template(flipped, n, slug, idx=len(reels)))
    return reels


def _reel_from_template(t, niche: str, slug: str, handle_override: str | None = None,
                        idx: int = 0, watched: bool = False) -> dict:
    handle_stub, platform, title, hook, transcript, why, fmt, style, views, likes = t
    handle = handle_override or handle_stub.format(slug=slug)
    return {
        "id": f"reel-{slug}-{idx}",
        "creator_handle": handle.lstrip("@"),
        "platform": platform,
        "title": title.format(niche=niche),
        "hook_text": hook.format(niche=niche),
        "transcript": transcript.format(niche=niche),
        "thumbnail_url": "",
        "video_url": "",
        "views": views + idx * 37_000,
        "likes": likes + idx * 4_100,
        "why_trending": why,
        "format_id": fmt,
        "style": style,
        "from_watched": watched,
    }


REELS_PAGE = 6


@app.get("/v1/reels")
async def reels(niche: str = "", creator_id: str = "default", watched: str = "", cursor: int = 0):
    watched_list = [w.strip().lstrip("@") for w in watched.split(",") if w.strip()]
    # Real path (key-gated scraper) would land here; mock corpus is niche-parameterized.
    corpus = _mock_reels(niche, watched_list)
    page = corpus[cursor * REELS_PAGE:(cursor + 1) * REELS_PAGE]
    next_cursor = cursor + 1 if (cursor + 1) * REELS_PAGE < len(corpus) else None
    return {"mode": "live" if (APIFY_KEY or BRIGHTDATA_KEY) else "mock",
            "reels": page, "next_cursor": next_cursor}


# ---------------------------------------------------------------------------
# Daily feed — server-composed mix of script suggestions + reels + a trend
# ---------------------------------------------------------------------------

_FEED_MAX_PAGES = 5


@app.get("/v1/feed")
async def feed(creator_id: str = "default", niche: str = "", audience: str = "",
               known_for: str = "", goal: str = "Grow my audience",
               styles: str = "", watched: str = "", cursor: int = 0):
    allowed = [s for s in styles.split(",") if s in STYLES] or list(STYLES.keys())
    style = allowed[cursor % len(allowed)]
    topics = [
        f"the {niche or 'creator'} mistake everyone makes",
        f"what nobody tells beginners about {niche or 'your field'}",
        f"a myth in {niche or 'your niche'} that needs to die",
        f"the fastest win in {niche or 'your field'} this month",
        f"what I'd do differently starting {niche or 'out'} today",
    ]
    sreq = ScriptRequest(
        niche=niche, audience=audience, known_for=known_for, goal=goal,
        pillar=topics[cursor % len(topics)],
        pillar_summary="Daily feed suggestion",
        style=style, count=3, creator_id=creator_id,
    )
    script_result = await scripts(sreq)
    items = [{"type": "script", "script": s} for s in script_result.get("scripts", [])[:3]]

    reel_result = await reels(niche=niche, creator_id=creator_id, watched=watched, cursor=cursor)
    items += [{"type": "reel", "reel": r} for r in reel_result.get("reels", [])[:4]]

    all_trends = mock_trends(niche)
    items.append({"type": "trend", "trend": all_trends[cursor % len(all_trends)]})

    reels_more = reel_result.get("next_cursor") is not None
    next_cursor = cursor + 1 if (cursor + 1 < _FEED_MAX_PAGES or reels_more) else None
    return {"mode": script_result.get("mode", "mock"), "items": items, "next_cursor": next_cursor}


# ---------------------------------------------------------------------------
# Mimic — turn an influencer reel into a script in the creator's voice
# ---------------------------------------------------------------------------

def _mock_mimic(reel: dict, brand: dict) -> dict:
    niche = brand.get("niche") or "your niche"
    fmt = reel.get("format_id", "myth-buster")
    style = reel.get("style", "talking_head")
    # Structure-preserving skeleton swap: keep the shape, replace the substance
    my_hook = reel.get("hook_text", "")
    for other in ("fitness", "finance", "cooking", "your niche"):
        my_hook = my_hook.replace(other, niche)
    return {
        "title": f"{niche}: {reel.get('title','their idea')[:40]}",
        "summary": f"Your take on @{reel.get('creator_handle','them')}'s structure, rebuilt for {niche}.",
        "hook": my_hook or f"Everyone in {niche} gets this wrong — here's the fix.",
        "hookSignal": "contrarian",
        "formatId": fmt,
        "body": (f"[Same skeleton as the original, your substance] Open on the boldest claim you can defend "
                 f"about {niche}. Walk the same beats: {reel.get('transcript','claim → proof → takeaway')} "
                 f"— but every example, number, and story is YOURS."),
        "cta": "Follow for the next one.",
        "shotPlan": ["Hook on frame 1, direct eye contact", "One punch-in on the key beat", "CTA to camera"],
        "targetSeconds": 26, "predictedScore": 82,
        "altHooks": [], "style": style,
    }


@app.post("/v1/mimic")
async def mimic(req: MimicRequest):
    provenance = {"creator_handle": req.reel.get("creator_handle", ""),
                  "platform": req.reel.get("platform", ""), "reel_id": req.reel.get("id", "")}
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "script": _mock_mimic(req.reel, req.brand), "mimicked_from": provenance}
    try:
        sys, usr = prompts.mimic_prompt(req.reel, req.brand, req.memory)
        out = extract_json(await anthropic(sys, usr, OPUS, 2000), array=False)
        if not out:
            return {"mode": "mock", "script": _mock_mimic(req.reel, req.brand), "mimicked_from": provenance}
        return {"mode": "live", "script": out, "mimicked_from": provenance}
    except HTTPException:
        return {"mode": "mock", "script": _mock_mimic(req.reel, req.brand), "mimicked_from": provenance}


# ---------------------------------------------------------------------------
# Video-link analysis — pasted TikTok/IG/YT link → teardown + your version
# ---------------------------------------------------------------------------

def _platform_from_url(url: str) -> str:
    u = url.lower()
    if "tiktok" in u: return "tiktok"
    if "instagram" in u or "instagr.am" in u: return "instagram"
    if "youtu" in u: return "youtube"
    return "unknown"


_MOCK_VIDEO_TRANSCRIPT = (
    "Hook: a bold claim delivered in the first second with on-screen text mirroring it. "
    "Beat 2: the creator stakes credibility with one specific number. "
    "Beat 3: quick visual proof — the pattern is shown, not described. "
    "Beat 4: the reframe — why everyone reads this wrong. "
    "Close: a single takeaway line and a one-word comment prompt."
)


@app.post("/v1/analyze-video")
async def analyze_video(req: AnalyzeVideoRequest):
    platform = _platform_from_url(req.url)
    # Real path: scraper (APIFY_KEY) fetches media → AssemblyAI transcribes. Keyless: canned structure.
    transcript = _MOCK_VIDEO_TRANSCRIPT
    niche = req.brand.get("niche") or "your niche"
    if ANTHROPIC_KEY:
        try:
            sys, usr = prompts.analyze_video_prompt(req.url, transcript, req.brand, req.memory)
            out = extract_json(await anthropic(sys, usr, OPUS, 2600), array=False)
            if out and out.get("your_version"):
                return {"mode": "live" if APIFY_KEY else "live_structure", "platform": platform,
                        "transcript": transcript, **out}
        except HTTPException:
            pass
    mock_reel = {"creator_handle": "the original creator", "platform": platform,
                 "title": "the linked video", "hook_text": f"A proven hook pattern, rebuilt for {niche}",
                 "transcript": transcript, "format_id": "myth-buster", "style": "talking_head"}
    return {
        "mode": "mock", "platform": platform, "transcript": transcript,
        "hook_analysis": "The hook lands a bold claim inside the first second and mirrors it in on-screen text — a double pattern-interrupt that stops both sound-on and sound-off scrollers.",
        "structure_beats": ["Bold claim (0-1.5s)", "Credibility number", "Visual proof", "The reframe", "Single takeaway + comment prompt"],
        "why_it_works": "Every beat earns the next second: specificity builds trust, the proof is shown rather than told, and the loop opened in the hook only closes on the final line — which is what holds retention to the end.",
        "suggestions": [f"Steal the claim→number→proof skeleton for your next {niche} post",
                        "Mirror your hook as on-screen text in the first frame",
                        "End with a one-word comment prompt to feed the algorithm early signals"],
        "your_version": _mock_mimic(mock_reel, req.brand),
    }


# ---------------------------------------------------------------------------
# Brand summary — the Profile "what Marque knows about you" card
# ---------------------------------------------------------------------------

@app.post("/v1/brand-summary")
async def brand_summary(req: BrandSummaryRequest):
    b = req.brand
    niche = b.get("niche") or "your niche"
    audience = b.get("audience") or "your audience"
    known = b.get("known_for") or f"a sharper take on {niche}"
    angle = (req.memory.get("angle") or "").strip()
    if ANTHROPIC_KEY:
        try:
            stats = list(_arm_stats.get(req.creator_id, {}).values())
            sys, usr = prompts.brand_summary_prompt(b, req.memory, stats)
            out = extract_json(await anthropic(sys, usr, HAIKU, 900), array=False)
            if out and out.get("summary"):
                return {"mode": "live", **out}
        except HTTPException:
            pass
    working = angle or f"Turning {known.lower()} into a consistent short-form presence."
    return {"mode": "mock",
            "summary": (f"You're a {niche} creator making content for {audience}. What sets you apart is "
                        f"{known.lower()} — you'd rather be specific and right than loud and vague. "
                        f"Marque's read: your best content happens when you take a clear stance and back it "
                        f"with something only you could know."),
            "traits": [niche.split()[0].title() if niche else "Creator", "Specific over vague",
                       "Receipts over hype", "Consistency-first"],
            "working_on": working}


# ---------------------------------------------------------------------------
# Performance summary — last-30-days aggregates for the Performance tab
# ---------------------------------------------------------------------------

@app.get("/v1/performance/summary")
async def performance_summary(creator_id: str = "default", days: int = 30):
    days = max(7, min(90, days))
    settled = [(pid, p) for pid, p in _post_registry.items()
               if p.get("creator_id") == creator_id and p.get("settled") and p.get("metrics")]
    if settled:
        totals = {"views": 0, "likes": 0, "follows_gained": 0, "posts": len(settled)}
        platforms: dict[str, dict] = {}
        best = None
        fmt_mix: dict[str, int] = {}
        for pid, p in settled:
            m = p["metrics"]
            totals["views"] += m.get("views", 0)
            totals["likes"] += m.get("likes", 0)
            totals["follows_gained"] += m.get("follows_gained", 0)
            plat = p.get("platform", "instagram")
            ps = platforms.setdefault(plat, {"views": 0, "likes": 0, "follows_gained": 0, "posts": 0})
            ps["views"] += m.get("views", 0); ps["likes"] += m.get("likes", 0)
            ps["follows_gained"] += m.get("follows_gained", 0); ps["posts"] += 1
            fmt = p.get("format_id") or "other"
            fmt_mix[fmt] = fmt_mix.get(fmt, 0) + 1
            if best is None or m.get("views", 0) > best["views"]:
                best = {"post_id": pid, "views": m.get("views", 0), "likes": m.get("likes", 0),
                        "format_id": fmt, "platform": plat}
        eng = round((totals["likes"] / max(totals["views"], 1)) * 100, 1)
        return {"mode": "live", "days": days, "totals": {**totals, "engagement_rate": eng},
                "platforms": platforms, "daily": [],
                "best_post": best, "format_mix": [{"format": k, "count": v} for k, v in
                                                  sorted(fmt_mix.items(), key=lambda x: -x[1])]}
    # Deterministic mock series (seeded by creator_id) so the UI charts something believable
    rng = random.Random(creator_id)
    base = rng.randint(300, 900)
    daily = []
    tv = tl = 0
    for i in range(days):
        growth = 1.0 + (i / days) * rng.uniform(0.5, 1.2)
        spike = rng.choice([1, 1, 1, 1, 2.6]) if i % 7 in (2, 5) else 1
        views = int(base * growth * spike * rng.uniform(0.7, 1.3))
        likes = int(views * rng.uniform(0.06, 0.11))
        daily.append({"day": i, "views": views, "likes": likes})
        tv += views; tl += likes
    ig_share = rng.uniform(0.45, 0.65)
    follows = int(tv * 0.004)
    return {"mode": "mock", "days": days,
            "totals": {"views": tv, "likes": tl, "follows_gained": follows, "posts": days // 3,
                       "engagement_rate": round(tl / max(tv, 1) * 100, 1)},
            "platforms": {
                "instagram": {"views": int(tv * ig_share), "likes": int(tl * ig_share),
                              "follows_gained": int(follows * ig_share), "posts": days // 6},
                "tiktok": {"views": int(tv * (1 - ig_share)), "likes": int(tl * (1 - ig_share)),
                           "follows_gained": int(follows * (1 - ig_share)), "posts": days // 6},
            },
            "daily": daily,
            "best_post": {"post_id": "", "views": max(d["views"] for d in daily) * 3,
                          "likes": max(d["likes"] for d in daily) * 3,
                          "format_id": "myth-buster", "platform": "tiktok"},
            "format_mix": [{"format": "myth-buster", "count": 4}, {"format": "listicle", "count": 3},
                           {"format": "pov-story", "count": 2}, {"format": "faceless", "count": 1}]}
