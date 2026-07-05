"""Marque backend — the server-side AI brain.

Holds every vendor key so the iOS app ships none (three-trust-plane model, docs/12). Every AI
route proxies Anthropic when ANTHROPIC_API_KEY is set and falls back to a deterministic mock
otherwise, so the whole surface is testable with zero keys. Prompt quality lives in prompts.py.
"""
from __future__ import annotations

import os
import json
import re
import copy
import time
import uuid
import hashlib
import asyncio
import random
import logging

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import prompts
from prompts import OPUS, HAIKU, SONNET, STYLES, FORMAT_IDS
from contextlib import asynccontextmanager

from app.edl import (EDL, safe_default_edl, validate_and_repair, strip_fillers,
                     ms_to_frame, build_render_plan, apply_edl_ops)
from supabase_persistence import SupabaseClient


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _load_learning_state()
    yield
    if _anthropic_client is not None:
        await _anthropic_client.aclose()


app = FastAPI(title="Marque API", version="0.3.0", lifespan=_lifespan)


@app.middleware("http")
async def _timing_middleware(request, call_next):
    """One log line per request (method path status ms) — the only latency
    instrumentation the backend had was silence; this makes p50/p95 measurable
    from Render logs with a plain awk/grep one-liner."""
    start = time.time()
    response = await call_next(request)
    elapsed_ms = round((time.time() - start) * 1000, 1)
    logging.info("timing %s %s %d %sms", request.method, request.url.path,
                response.status_code, elapsed_ms)
    return response


ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages"
AYRSHARE_KEY = os.environ.get("AYRSHARE_KEY", "")
APIFY_KEY = os.environ.get("APIFY_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
# Inference-time quality gate (generate -> judge -> targeted self-repair). On by
# default; set AI_QUALITY=0 to fall back to raw single-shot generation.
AI_QUALITY = os.environ.get("AI_QUALITY", "1") != "0"
# Best-of-N hooks: generate a diverse hook pool at temp 1.0, judge, and mandate the
# winners as the script openers. The strongest single quality lever, at +2 LLM calls
# of latency. Set BEST_OF_N_HOOKS=0 to skip and rely on inline alt-hook selection.
BEST_OF_N_HOOKS = os.environ.get("BEST_OF_N_HOOKS", "1") != "0"

# ---------------------------------------------------------------------------
# Learning loop — in-memory bandit (Supabase arm_stats in production)
# ---------------------------------------------------------------------------

_arm_stats: dict[str, dict] = {}
_post_registry: dict[str, dict] = {}
# Durable backing store for the two dicts above. None keyless → pure in-memory (unchanged).
_supabase_client: SupabaseClient | None = (
    SupabaseClient(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None
)


async def _load_learning_state():
    """Rehydrate the bandit + post registry from Supabase so a restart / new Render
    instance doesn't start cold. No-op keyless. Never blocks startup on failure."""
    if not _supabase_client:
        return
    try:
        posts = await _supabase_client.load_all_posts()
        for p in posts:
            pid = p.get("post_id")
            if pid:
                _post_registry[pid] = p
        for cid in {p.get("creator_id") for p in posts if p.get("creator_id")}:
            arms = await _supabase_client.load_arm_stats(cid)
            if arms:
                _arm_stats[cid] = arms
        logging.info("learning state loaded: %d posts, %d creators", len(_post_registry), len(_arm_stats))
    except Exception as e:
        logging.warning("startup learning-state load failed: %s", e)

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


async def _update_arm(creator_id: str, dim_value: str, y: float):
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
    if _supabase_client:                                  # write-through (best-effort)
        try:
            await _supabase_client.upsert_arm_stat(creator_id, dim_value, s)
        except Exception as e:
            logging.warning("supabase upsert_arm_stat failed: %s", e)


async def _ensure_arms_loaded(creator_id: str):
    """Lazy-load a creator's arms from Supabase on cache miss (e.g. this Render
    instance never saw them). No-op keyless or when already cached."""
    if creator_id in _arm_stats or not _supabase_client:
        return
    try:
        arms = await _supabase_client.load_arm_stats(creator_id)
        if arms:
            _arm_stats[creator_id] = arms
    except Exception as e:
        logging.warning("lazy load_arm_stats failed: %s", e)


async def _arms_for_prompt(creator_id: str) -> list[dict]:
    """Shape raw bandit arms into the {lift_pct, label, confidence} form that
    prompts.learning_block() actually reads. Without this the raw arm dicts lack
    lift_pct/label, so learning_block always returns "" and post-performance
    never reaches script/hook/converse generation — the loop is cosmetic. Emit
    only arms with an early read (n>=4), strongest signals first."""
    await _ensure_arms_loaded(creator_id)
    _dim_word = {"style": "style", "format_id": "format",
                 "hook_signal": "hook", "pillar": "pillar"}
    out = []
    for key, s in _arm_stats.get(creator_id, {}).items():
        if s.get("n", 0) < 4 or ":" not in key:
            continue
        dim, val = key.split(":", 1)
        lift = round((s.get("effect", 0.5) - 0.5) * 200)
        sign = "+" if lift >= 0 else ""
        label = f"{val.replace('_', ' ')} {_dim_word.get(dim, dim)}: {sign}{lift}% vs your average"
        out.append({**s, "lift_pct": lift, "label": label,
                    "confidence": s.get("confidence", "early_read")})
    out.sort(key=lambda a: abs(a["lift_pct"]), reverse=True)
    return out


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

_anthropic_client: httpx.AsyncClient | None = None
_anthropic_client_loop: object | None = None


def _get_anthropic_client() -> httpx.AsyncClient:
    """A shared, connection-pooled client instead of one-per-call. Loop-aware:
    tests drive anthropic() via asyncio.run() per test (a fresh event loop each
    time), and an httpx client is bound to the loop it was created under — reusing
    one across a closed loop raises. So a loop change transparently recreates the
    client; in production (one long-lived uvicorn loop) this is created exactly
    once, giving real connection pooling. Closed in _lifespan on shutdown."""
    global _anthropic_client, _anthropic_client_loop
    loop = asyncio.get_running_loop()
    if _anthropic_client is None or _anthropic_client_loop is not loop:
        _anthropic_client = httpx.AsyncClient(timeout=90)
        _anthropic_client_loop = loop
    return _anthropic_client


async def anthropic(system: str, user: str, model: str = OPUS, max_tokens: int = 3000,
                    temperature: float | None = None, schema: dict | None = None) -> str:
    delays = [0.5, 2.0, 8.0]
    last_err = None
    body = {"model": model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}]}
    if temperature is not None:
        body["temperature"] = temperature
    if schema is not None:
        # Native Structured Outputs (GA): the model's text is guaranteed to be valid
        # JSON conforming to `schema`. No beta header; works with anthropic-version
        # 2023-06-01. https://platform.claude.com/docs/en/build-with-claude/structured-outputs
        body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    for attempt, delay in enumerate(delays + [None]):
        try:
            client = _get_anthropic_client()
            r = await client.post(
                ANTHROPIC_URL,
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json=body,
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


async def anthropic_json(system: str, user: str, schema: dict, model: str = OPUS,
                         max_tokens: int = 3000, temperature: float | None = None,
                         array_key: str | None = None):
    """Structured-output call: returns parsed JSON guaranteed to match `schema`.

    Because arrays can't be a top-level structured-output root, array call sites pass
    an object schema wrapping the array under `array_key` and get the unwrapped list
    back. Falls back to the hand-rolled extract_json (so a transient schema/API issue
    degrades to today's behavior instead of failing), and returns None only if both
    paths fail — callers keep their existing mock fallback."""
    raw = await anthropic(system, user, model, max_tokens, temperature, schema=schema)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        data = extract_json(raw, array=False)
    if array_key:
        if isinstance(data, dict):
            return data.get(array_key) or []
        return extract_json(raw, array=True) or []
    return data


def _array_schema(name: str, element: dict) -> dict:
    """Wrap an element schema as {name: [element...]} — a structured-output-legal
    object root (arrays can't be the root)."""
    return {
        "type": "object", "additionalProperties": False, "required": [name],
        "properties": {name: {"type": "array", "items": element}},
    }


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
    catchphrases: list[str] = []      # verbatim signature phrases (from brand-scan)
    # Creators whose style this creator wants scripts to channel — presets resolve
    # instantly (PRESET_EMULATION); custom links resolve from cache/Supabase/scrape.
    emulation_targets: list[dict] = []

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
    memory: dict = {}                  # client-held creator memory (facts/angle/ideas/...)


class HooksRequest(Brand):
    topic: str = ""
    style: str = "talking_head"
    creator_id: str = "default"
    memory: dict = {}                  # client-held creator memory


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


class DigestRequest(Brand):
    """The onboarding brand digest: everything the quiz collected, plus an optional
    connected handle (reel analysis) or spoken-interview transcript."""
    handle: str = ""
    scan_platform: str = "instagram"       # "instagram" | "tiktok" (avoid Brand field clashes)
    voice_transcript: list[dict] = []      # interview alternative to a handle
    posts: list[dict] = []                 # test injection (same as ScanRequest)
    preferred_styles: list[str] = []
    creator_id: str = "default"
    memory: dict = {}


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
    # duet_split: the reacted-to clip the creator responds to (a direct, renderable
    # video/image URL — the app supplies it via paste-URL/upload/screenshot).
    react_source_url: str = ""
    react_credit_label: str = ""
    # Per-creator editing preferences (Settings → threaded into every edit)
    edit_prefs: dict = {}      # {auto_captions: bool, caption_style: clean|bold-word|karaoke, filler_trim: off|standard|aggressive}


class TweakRequest(BaseModel):
    """One tweak turn on a finished clip: a natural-language instruction (chat)
    OR pre-typed ops (the manual editor) — ops bypass LLM interpretation entirely
    and go straight to deterministic application."""
    clip_id: str = ""
    instruction: str = ""
    ops: list[dict] = []


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


def _cap_evict(cache: dict, cap: int) -> None:
    """FIFO-evict oldest entries once a dict cache exceeds `cap` (dicts preserve
    insertion order in Python 3.7+). Same pattern _tts_cache already used inline;
    shared here so every in-memory cache gets a bound."""
    while len(cache) > cap:
        cache.pop(next(iter(cache)))


_JOB_TTL_S = 24 * 3600


def _sweep_ttl_jobs(jobs: dict, ttl_s: float = _JOB_TTL_S) -> None:
    """Evict jobs older than ttl_s. Sweep-on-access (called from the GET poll
    endpoints) rather than a background timer — no extra event loop task, and
    the in-memory job stores are already accepted-orphaned-on-restart, so a
    lazily-swept TTL is a strict improvement with zero new failure modes."""
    now = time.time()
    dead = [jid for jid, j in jobs.items() if now - j.get("created_at", now) > ttl_s]
    for jid in dead:
        jobs.pop(jid, None)


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
    persona: str = "closer"            # closer | machine | sergeant — coaching voice
    response_length: str = "medium"    # concise | medium | detailed


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
        title = topic[:48]
        title = title[:1].upper() + title[1:] if title else title  # sentence-case for display
        out.append({
            "title": title,
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


def _blend_score(v: dict) -> int:
    """Ground predictedScore in the independent critic's axes instead of the
    generator's self-flattery. Hook dominates because it dominates retention."""
    try:
        s = (0.50 * float(v.get("hook_strength", 0))
             + 0.25 * float(v.get("specificity", 0))
             + 0.15 * float(v.get("format_fit", 0))
             + 0.10 * float(v.get("voice_match", 0)))
        if v.get("slop"):
            s -= 12
        return max(0, min(100, round(s)))
    except (TypeError, ValueError):
        return 0


def _calibration_signal(creator_id: str, script: dict) -> tuple[int | None, float]:
    """Outcome calibration from the learning loop: what the creator's REAL posts
    in this script's style / format / hook-signal actually earned. Returns
    (score_0_100, weight_0_1); weight scales with accumulated evidence and is 0
    until at least one arm has an early read (n>=4). No data → (None, 0)."""
    stats = _arm_stats.get(creator_id, {})
    keys = []
    if script.get("style"):
        keys.append(f"style:{script['style']}")
    if script.get("formatId"):
        keys.append(f"format_id:{script['formatId']}")
    if script.get("hookSignal"):
        keys.append(f"hook_signal:{script['hookSignal']}")
    effects, evidence = [], 0
    for k in keys:
        s = stats.get(k)
        if s and s.get("n", 0) >= 4:                     # early_read or better
            effects.append(float(s.get("effect", 0.5)))
            evidence += s["n"]
    if not effects:
        return None, 0.0
    cal = round(sum(effects) / len(effects) * 100)       # mean arm effect → 0-100
    weight = min(0.5, 0.04 * evidence)                   # trust grows with data, capped at 0.5
    return cal, weight


def _final_score(creator_id: str, script: dict, verdict: dict) -> int:
    """Critic score, pulled toward the creator's real outcomes as evidence accrues."""
    critic = _blend_score(verdict)
    cal, w = _calibration_signal(creator_id, script)
    if cal is None:
        return critic
    return max(0, min(100, round((1 - w) * critic + w * cal)))


async def quality_scripts(brand: dict, style: str, scripts: list[dict],
                          posts: list[dict] | None = None,
                          creator_id: str = "default",
                          mandated_hooks: list[dict] | None = None) -> list[dict]:
    """Generate -> judge -> targeted self-repair for scripts. A strict HAIKU critic
    scores each draft; we swap in the strongest alt-hook, rewrite only the weak
    ones with OPUS, and re-ground predictedScore on the critic's axes calibrated
    against the creator's real learning-loop outcomes. Any failure falls back to
    the untouched drafts — this never strands generation."""
    if not (AI_QUALITY and scripts):
        return scripts
    try:
        jsys, jusr = prompts.script_judge_prompt(scripts, style)
        verdicts = await anthropic_json(jsys, jusr,
                                        _array_schema("verdicts", prompts.SCRIPT_JUDGE_JSON_ELEMENT),
                                        HAIKU, 1400, array_key="verdicts")
    except HTTPException:
        return scripts
    by_index: dict[int, dict] = {}
    for v in verdicts:
        if isinstance(v, dict) and isinstance(v.get("index"), int) and 0 <= v["index"] < len(scripts):
            by_index[v["index"]] = v

    # Hooks that best_hooks() already generated + judged + mandated: the script-judge
    # must NOT swap these for an un-vetted altHook (its rubric differs from the hook
    # judge that already crowned them the strongest opener).
    mandated_texts = {(h.get("text") or "").strip() for h in (mandated_hooks or []) if h.get("text")}

    flagged: list[dict] = []
    for i, sc in enumerate(scripts):
        v = by_index.get(i)
        if not v:
            continue
        # Swap in the critic's preferred hook (0 = keep main; 1..n = altHooks[n-1]),
        # unless the current hook was mandated by best_hooks (keep the vetted winner).
        bh = v.get("best_hook", 0)
        alts = sc.get("altHooks", []) or []
        if (sc.get("hook", "").strip() not in mandated_texts) and isinstance(bh, int) and 1 <= bh <= len(alts):
            alt = alts[bh - 1]
            if alt.get("text"):
                sc["hook"] = alt["text"]
                if alt.get("signal"):
                    sc["hookSignal"] = alt["signal"]
        # Re-ground the virality score on the critic, calibrated by real outcomes.
        sc["predictedScore"] = _final_score(creator_id, sc, v)
        if v.get("verdict") == "revise":
            flagged.append({"pos": i, "script": sc, "verdict": v})

    if not flagged:
        return scripts
    try:
        rsys, rusr = prompts.script_revise_prompt(brand, style, flagged, posts)
        revised = await anthropic_json(rsys, rusr,
                                       _array_schema("scripts", prompts.SCRIPT_JSON_ELEMENT),
                                       OPUS, 3800, array_key="scripts")
    except HTTPException:
        return scripts
    for f, new in zip(flagged, revised):
        if isinstance(new, dict) and new.get("hook") and new.get("body"):
            new.setdefault("style", style)
            # Keep the critic+calibration-grounded score unless the rewrite lifted it.
            new["predictedScore"] = max(_final_score(creator_id, new, f["verdict"]),
                                        int(new.get("predictedScore", 0) or 0))
            scripts[f["pos"]] = new
    return scripts


async def quality_hooks(topic: str, hooks: list[dict]) -> list[dict]:
    """Re-score generated hooks with an independent critic, drop AI-slop/dupes,
    and re-rank by honest strength. Falls back to the raw hooks on any failure."""
    if not (AI_QUALITY and hooks):
        return hooks
    try:
        jsys, jusr = prompts.hook_judge_prompt(topic, hooks)
        verdicts = await anthropic_json(jsys, jusr,
                                        _array_schema("verdicts", prompts.HOOK_JUDGE_JSON_ELEMENT),
                                        HAIKU, 700, array_key="verdicts")
    except HTTPException:
        return hooks
    scored: list[tuple[int, dict]] = []
    for v in verdicts:
        if not (isinstance(v, dict) and isinstance(v.get("index"), int)):
            continue
        i = v["index"]
        if not (0 <= i < len(hooks)) or v.get("slop"):
            continue
        h = dict(hooks[i])
        h["strength"] = max(0, min(100, int(v.get("strength", h.get("strength", 0)) or 0)))
        scored.append((h["strength"], h))
    if not scored:
        return hooks
    scored.sort(key=lambda t: t[0], reverse=True)
    return [h for _, h in scored]


async def best_hooks(brand: dict, topic: str, style: str, creator_id: str,
                     n: int = 2, memory: dict | None = None) -> list[dict]:
    """Best-of-N hooks: generate a diverse pool at temp 1.0, judge + drop slop via
    quality_hooks, and return the top n. These become MANDATED script openers — the
    body is written around a vetted hook instead of the model's first-draft guess.
    Returns [] keyless or on failure (caller then generates without a mandate)."""
    if not (BEST_OF_N_HOOKS and AI_QUALITY and ANTHROPIC_KEY):
        return []
    try:
        stats = await _arms_for_prompt(creator_id)
        hsys, husr = prompts.hooks_prompt(brand, topic, style, arm_stats=stats, memory=memory)
        pool = await anthropic_json(hsys, husr, _array_schema("hooks", prompts.HOOK_JSON_ELEMENT),
                                    OPUS, 1200, temperature=1.0, array_key="hooks")
    except HTTPException:
        return []
    ranked = await quality_hooks(topic, pool)
    return ranked[:max(1, n)]


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
            "scrape": "live" if APIFY_KEY else "mock",
            "publish": "live" if AYRSHARE_KEY else "mock",
            "tts": _tts_provider()}


@app.post("/v1/pillars")
async def pillars(req: PillarRequest):
    mode, p = await generate_pillars(req.d(), req.posts or None)
    return {"mode": mode, "pillars": p}


@app.post("/v1/scripts")
async def scripts(req: ScriptRequest):
    return await _generate_scripts(req)


async def _generate_scripts(req: ScriptRequest) -> dict:
    """The full quality-gated script pipeline (best-of-N hooks → write → judge →
    repair). Shared by /v1/scripts and the onboarding digest job."""
    req.count = max(1, min(5, req.count))
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "scripts": mock_scripts(req)}
    pillar = {"name": req.pillar, "summary": req.pillar_summary,
              "angle": req.pillar_angle, "exampleTopics": req.example_topics}
    try:
        stats = await _arms_for_prompt(req.creator_id)
        emulation = await _resolve_emulation_profiles(req.emulation_targets)
        # Best-of-N: pre-select the strongest openers, then write bodies around them.
        topic = req.pillar or req.niche or "your next post"
        mandated = await best_hooks(req.d(), topic, req.style, req.creator_id, n=min(2, req.count))
        sys, usr = prompts.scripts_prompt(req.d(), pillar, req.style, req.count,
                                          req.media_context, req.posts or None,
                                          arm_stats=stats, memory=req.memory or None,
                                          mandated_hooks=mandated or None, emulation=emulation or None)
        out = await anthropic_json(sys, usr, _array_schema("scripts", prompts.SCRIPT_JSON_ELEMENT),
                                   OPUS, 3800, array_key="scripts")
        if not out:
            return {"mode": "mock", "scripts": mock_scripts(req)}
        out = await quality_scripts(req.d(), req.style, out, req.posts or None,
                                    creator_id=req.creator_id, mandated_hooks=mandated or None)
        return {"mode": "live", "scripts": out}
    except HTTPException:
        return {"mode": "mock", "scripts": mock_scripts(req)}


@app.post("/v1/hooks")
async def hooks(req: HooksRequest):
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "hooks": [{"text": f"The {req.topic} mistake nobody warns you about", "signal": "curiosity", "strength": 82}]}
    try:
        stats = await _arms_for_prompt(req.creator_id)
        emulation = await _resolve_emulation_profiles(req.emulation_targets)
        sys, usr = prompts.hooks_prompt(req.d(), req.topic, req.style, arm_stats=stats,
                                        memory=req.memory or None, emulation=emulation or None)
        out = await anthropic_json(sys, usr, _array_schema("hooks", prompts.HOOK_JSON_ELEMENT),
                                   OPUS, 1200, array_key="hooks")
        out = await quality_hooks(req.topic, out)
        return {"mode": "live", "hooks": out}
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
    # DECISION (2026-07): real trend-scraping is explicitly deferred, not a live
    # gap to close opportunistically. The niche-aware mock set is good enough for
    # the current surface (a ticker line, not a ranked feed); wiring a scrape job
    # here is only worth it once trends becomes a primary discovery surface.
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
REMOTION_FUNCTION_NAME = os.environ.get("REMOTION_FUNCTION_NAME", "")
REMOTION_BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "render", "dist", "lambda-render.js")

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
        # Falsy-check, not setdefault: caption_style is now a real EDL model field,
        # so model_dump() emits the key with value None when unset.
        if not edl.get("caption_style"):
            edl["caption_style"] = style
    trim = prefs.get("filler_trim")
    if trim == "off":
        edl["drops"] = []
    elif trim == "aggressive" and edl.get("drops") is not None:
        # tighten: mark every drop, and flag the EDL so the renderer trims gaps > 200ms
        if not edl.get("trim_aggressiveness"):
            edl["trim_aggressiveness"] = "aggressive"
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
        "react_source_url": req.react_source_url,
        "react_credit_label": req.react_credit_label,
        # Conversational-tweak state: transcript kept for re-editing, prior EDLs
        # for undo, and the tweak chat history.
        "words": [], "edl_history": [], "tweaks": [],
        "created_at": time.time(),
    }
    _clip_jobs[job_id] = job
    if not ASSEMBLY_KEY:
        job["status"] = "mock_ready"
        for c in clips: c["status"] = "ready"
        job["edl"] = _apply_edit_prefs(_mock_edl(req.style, req.script), job["edit_prefs"])
        # Deterministic transcript so caption-rebuild tweaks work in keyless demo.
        job["words"] = _mock_words(req.script)
        return {"mode": "mock", "job_id": job_id, "clips": clips}
    asyncio.create_task(_run_pipeline(job_id))
    return {"mode": "live", "job_id": job_id, "clips": clips}


@app.get("/v1/clips/{job_id}")
async def get_clip_job(job_id: str, include_words: int = 0):
    _sweep_ttl_jobs(_clip_jobs)
    _sweep_stuck_renders(_clip_jobs)
    if job_id not in _clip_jobs:
        raise HTTPException(status_code=404, detail="job not found")
    job = _clip_jobs[job_id]
    out = {
        "mode": "mock" if job["status"] == "mock_ready" else "live",
        "job_id": job_id,
        "status": job["status"],
        "clips": job["clips"],
        "edl": job.get("edl"),
        "error": job.get("error"),
        "error_detail": job.get("error_detail"),
    }
    if include_words:
        # Opt-in only — real transcripts are thousands of words and this endpoint
        # is polled every 5s; the manual editor is the only caller that needs them.
        out["words"] = job.get("words") or []
    return out


@app.post("/v1/clips/{job_id}/retry")
async def retry_clip_job(job_id: str):
    """Recover a failed job. If an EDL exists, only the render stage re-runs (the
    transcript + edit are still good); otherwise the full pipeline restarts. The
    job dict retains everything needed — no re-upload from the app."""
    job = _clip_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    if job["status"] in ("transcribing", "editing", "rendering") \
            or any(c.get("status") == "rendering" for c in job["clips"]):
        raise HTTPException(status_code=409, detail="retry_in_progress")
    if job["status"] == "mock_ready":
        return {"mode": "mock", "job_id": job_id, "status": job["status"], "clips": job["clips"]}

    for key in ("error", "error_detail", "error_stage"):
        job.pop(key, None)
    for c in job["clips"]:
        if c.get("status") == "failed":
            c.pop("error", None)
            c.pop("error_detail", None)
            c["status"] = "queued"

    if job.get("edl"):
        job["status"] = "rendering"
        asyncio.create_task(_retry_render(job_id))
    else:
        job["status"] = "transcribing"
        asyncio.create_task(_run_pipeline(job_id))
    return {"mode": "live", "job_id": job_id, "status": job["status"], "clips": job["clips"]}


async def _retry_render(job_id: str) -> None:
    """Render-stage-only retry, with the same terminal-state guarantees."""
    job = _clip_jobs.get(job_id)
    if not job:
        return
    try:
        await _render_all_clips(job_id)
        job["status"] = "ready" if any(c["status"] == "ready" for c in job["clips"]) else "failed"
        if job["status"] == "failed" and not job.get("error"):
            first = next((c for c in job["clips"] if c.get("error")), None)
            job["error"] = (first or {}).get("error", "render_no_output")
            job["error_detail"] = (first or {}).get("error_detail", "")
    except PipelineError as e:
        _fail_job(job, e.code, e.detail, e.stage)
    except Exception as e:
        _fail_job(job, "internal_error", str(e))


@app.post("/v1/clips/{job_id}/tweak")
async def tweak_clip(job_id: str, req: TweakRequest):
    """One conversational tweak turn: interpret the instruction into typed ops
    (LLM live / keyword grammar keyless), apply them deterministically to the
    stored EDL, and re-render just the targeted clip."""
    job = _clip_jobs.get(job_id)
    if job is None:
        # In-memory job store — a backend restart orphans old jobs.
        raise HTTPException(status_code=404, detail="job_not_found")
    # Case-insensitive: iOS UUID.uuidString is uppercase, uuid4() is lowercase.
    want = req.clip_id.lower()
    clip = next((c for c in job["clips"] if c["clip_id"].lower() == want), None)
    if clip is None:
        raise HTTPException(status_code=404, detail="clip_not_found")
    if not req.instruction.strip() and not req.ops:
        raise HTTPException(status_code=422, detail="empty_instruction")
    # Concurrency guard: asyncio is single-threaded, so status checks + the
    # later synchronous status set (before any await) are atomic per request.
    if clip.get("status") == "rendering" or job["status"] in ("transcribing", "editing", "rendering"):
        raise HTTPException(status_code=409, detail="render_in_progress")
    if not job.get("edl"):
        raise HTTPException(status_code=409, detail="no_edl")

    # 1) Interpretation → (reply, ops). The manual editor sends typed ops directly —
    #    no LLM in the loop, fully deterministic and keyless-testable.
    mode = "mock"
    reply, ops = "", []
    if req.ops:
        mode = "direct"
        ops = [o for o in req.ops if isinstance(o, dict)]
    elif ANTHROPIC_KEY:
        mode = "live"
        try:
            sys_p, usr_p = prompts.tweak_prompt(job["edl"], job.get("words") or [],
                                                req.instruction, job["tweaks"])
            envelope = await anthropic_json(sys_p, usr_p, prompts.TWEAK_ENVELOPE_JSON_SCHEMA,
                                            SONNET, 1000)
        except HTTPException:
            envelope = None
        if isinstance(envelope, dict) and (envelope.get("reply") or "").strip():
            reply = envelope["reply"]
            ops = [o for o in (envelope.get("ops") or []) if isinstance(o, dict)]
        else:
            reply, ops = _mock_tweak(req.instruction)   # degrade, never strand the chat
    else:
        reply, ops = _mock_tweak(req.instruction)

    applied: list[dict] = []
    skipped: list[dict] = []
    changed = False
    resolve_broll_needed = False

    # 2) undo is server-level (needs the history stack apply_edl_ops can't see)
    edit_ops = []
    for o in ops:
        if o.get("type") == "undo":
            if job["edl_history"]:
                job["edl"] = job["edl_history"].pop()
                applied.append({"type": "undo", "applied": True, "reason": ""})
                changed = True
            else:
                skipped.append({"type": "undo", "applied": False, "reason": "nothing to undo yet"})
        else:
            edit_ops.append(o)

    # 3) Deterministic application + validation round-trip
    if edit_ops:
        new_edl, results = apply_edl_ops(job["edl"], edit_ops, job.get("words") or [])
        ok = [r for r in results if r["applied"]]
        if ok:
            try:
                obj = EDL(**new_edl)
                obj, _ = validate_and_repair(obj)
                new_edl = obj.model_dump()
            except Exception:
                # The batch produced an invalid EDL — reject it wholesale, keep the current cut.
                for r in results:
                    if r["applied"]:
                        r.update(applied=False, reason="change failed validation")
                ok = []
        if ok:
            job["edl_history"].append(copy.deepcopy(job["edl"]))
            del job["edl_history"][:-10]                 # cap the undo stack
            job["edl"] = new_edl
            changed = True
            resolve_broll_needed = any(r["type"] == "add_broll" for r in ok)
        applied.extend(r for r in results if r["applied"])
        skipped.extend(r for r in results if not r["applied"])

    # 4) History entry (feeds the next turn's prompt context)
    job["tweaks"].append({
        "instruction": req.instruction or "manual edit", "reply": reply,
        "summary": ", ".join(r["type"] for r in applied) or "no changes",
        "applied": applied, "skipped": skipped,
    })
    del job["tweaks"][:-20]

    # 5) Re-render just this clip (real renderer only; keyless/mock jobs keep
    #    their clip ready — the EDL still updates, visible via GET).
    #    RACE NOTE: the LLM call above was awaited, so a concurrent tweak may have
    #    started a render since the top-of-request 409 check. Re-check NOW and set
    #    the rendering flag synchronously (no await between check and set) — that
    #    pair is atomic under asyncio's single thread.
    needs_render = (changed and job["status"] == "ready"
                    and bool(REMOTION_SERVE_URL and REMOTION_ACCESS_KEY and REMOTION_FUNCTION_NAME))
    if needs_render:
        if clip.get("status") == "rendering":
            # Someone else's re-render is in flight; our EDL change is saved and
            # will be picked up by the next render rather than double-rendering.
            needs_render = False
        else:
            # Status set + create_task are adjacent with NO await between them —
            # the b-roll resolve (an await that used to sit here and could strand
            # the clip in "rendering" if it died) now lives inside the task's try.
            clip["status"] = "rendering"
            clip["render_started_at"] = time.time()
            my_gen = _bump_render_gen(clip)
            asyncio.create_task(_rerender_clip(job_id, req.clip_id, my_gen,
                                               resolve_broll=resolve_broll_needed))

    return {"mode": mode, "reply": reply, "applied": applied, "skipped": skipped,
            "changed": changed, "needs_render": needs_render, "clip_status": clip["status"]}


async def _rerender_clip(job_id: str, clip_id: str, my_gen: int, resolve_broll: bool = False):
    """Re-render one clip after a tweak. NEVER strands the clip: on any failure the
    previous render_url is restored (status ready) — or, if there was never a good
    render to fall back to, the clip fails with a structured code instead of going
    fake-ready with no playable URL.

    Every write is gated on _is_current_render(clip, my_gen): a watchdog can mark
    this clip failed while this task is still silently running in the background
    (asyncio doesn't cancel it), and a subsequent retry/tweak can start a NEWER
    render for the same clip. If that happens, my_gen no longer matches the
    clip's current generation and this stale attempt writes NOTHING at all —
    the newer attempt's result (or in-flight state) is left untouched (F7)."""
    job = _clip_jobs.get(job_id)
    if not job:
        return
    clip = next((c for c in job["clips"] if c["clip_id"].lower() == clip_id.lower()), None)
    if not clip:
        return
    prev_url = clip.get("render_url")
    try:
        if resolve_broll:
            try:
                job["edl"] = await _resolve_broll(job["edl"])
            except Exception:
                clip.setdefault("warnings", []).append("broll_unresolved: resolve failed")
        submission = await _submit_remotion_render(
            job["source_url"], job["edl"], clip["format"], job["style"])
        if not submission:
            raise PipelineError("render_submit_failed", "no renderId from bridge", "render")
        clip["render_id"] = submission["render_id"]
        render_url = await _poll_remotion_render(
            submission["render_id"], submission["bucket_name"])
        if _is_current_render(clip, my_gen):
            clip["render_url"] = render_url
            clip.pop("error", None)
            clip.pop("error_detail", None)
    except PipelineError as e:
        if _is_current_render(clip, my_gen):
            clip["render_url"] = prev_url
            if job["tweaks"]:
                job["tweaks"][-1]["render_error"] = f"{e.code}: {e.detail}"[:200]
            if not prev_url:
                _fail_clip(clip, e.code, e.detail)
    except Exception as e:
        if _is_current_render(clip, my_gen):
            clip["render_url"] = prev_url
            if job["tweaks"]:
                job["tweaks"][-1]["render_error"] = str(e)[:200]
            if not prev_url:
                _fail_clip(clip, "internal_error", str(e))
    finally:
        if _is_current_render(clip, my_gen) and clip.get("status") != "failed":
            clip["status"] = "ready" if clip.get("render_url") else "failed"


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


def _mock_words(script: dict) -> list[dict]:
    """Deterministic word-frame transcript for keyless jobs — enough for tweak
    ops that need words (caption rebuild) and for the tweak prompt's context."""
    text = " ".join(filter(None, [script.get("hook", ""), script.get("body", ""), script.get("cta", "")]))
    words = text.split()[:80] or ["Great", "hook", "here"]
    out, t = [], 0
    for w in words:
        out.append({"word": w, "start_ms": t, "end_ms": t + 280, "confidence": 1.0,
                    "type": None, "is_emphasized": False})
        t += 300
    return out


def _mock_tweak(instruction: str) -> tuple[str, list[dict]]:
    """Keyless tweak grammar (deterministic, first-match) so the demo/tests work
    without a key: returns (reply, ops)."""
    low = instruction.lower()
    if "undo" in low:
        return "Rolling back your last tweak.", [{"type": "undo"}]
    for style, word in (("karaoke", "karaoke"), ("bold-word", "bold"), ("clean", "clean")):
        # karaoke/bold read as caption intent on their own; "clean" is too generic
        # a word ("clean up the audio") so it requires explicit caption context.
        if word in low and (style != "clean" or "caption" in low):
            return f"Switched your captions to the {style} style.", [{"type": "set_caption_style", "style": style}]
    if any(p in low for p in ("captions off", "no captions", "remove captions", "remove the captions")):
        return "Captions are off for this clip.", [{"type": "set_captions_enabled", "enabled": False}]
    if any(p in low for p in ("captions on", "add captions", "turn on captions")):
        return "Captions are back on.", [{"type": "set_captions_enabled", "enabled": True}]
    return ("I can change caption styles, cut or restore sections, add punch-ins or b-roll, "
            "and undo tweaks — tell me what to change."), []


# ---------------------------------------------------------------------------
# Pipeline error contract — every way a clip job can fail maps to a short,
# machine-readable code the app can translate into human copy. The old behavior
# (raw exception strings, silent empty transcripts, 10-minute hangs) is exactly
# what read as "the editor keeps failing" — clips must now always land in a
# terminal state (ready with a render_url, or failed with a code) and fast.
# ---------------------------------------------------------------------------

class PipelineError(RuntimeError):
    """Structured pipeline failure. `code` is a short slug from ERROR_CODES."""
    def __init__(self, code: str, detail: str = "", stage: str = ""):
        super().__init__(detail or code)
        self.code, self.detail, self.stage = code, detail[:300], stage


ERROR_CODES = [
    "source_unreachable",       # HEAD/Range probe of source_url failed
    "transcribe_submit_failed",
    "transcribe_failed",        # AssemblyAI returned status=error (or empty transcript)
    "transcribe_timeout",       # poll exhausted TRANSCRIBE_MAX_S
    "render_submit_failed",     # bridge submit returned no renderId
    "render_fatal",             # fatalErrorEncountered from Lambda
    "render_stalled",           # progress flat for RENDER_STALL_S, or watchdog sweep
    "render_timeout",           # poll exhausted RENDER_POLL_MAX_S
    "render_no_output",         # done=true but outputFile missing
    "bridge_error",             # node bridge crashed / non-JSON / subprocess timeout
    "internal_error",           # catch-all
]

# Fail-fast budgets — env-tunable, monkeypatchable in tests.
SOURCE_PROBE_TIMEOUT_S = float(os.environ.get("SOURCE_PROBE_TIMEOUT_S", "5"))
TRANSCRIBE_MAX_S = int(os.environ.get("TRANSCRIBE_MAX_S", "300"))
RENDER_POLL_MAX_S = int(os.environ.get("RENDER_POLL_MAX_S", "240"))
RENDER_STALL_S = int(os.environ.get("RENDER_STALL_S", "75"))
BRIDGE_CALL_TIMEOUT_S = float(os.environ.get("BRIDGE_CALL_TIMEOUT_S", "30"))
RENDER_WATCHDOG_S = int(os.environ.get("RENDER_WATCHDOG_S", "480"))


def _fail_clip(clip: dict, code: str, detail: str = "") -> None:
    clip["status"] = "failed"
    clip["error"] = code
    if detail:
        clip["error_detail"] = detail[:300]


def _fail_job(job: dict, code: str, detail: str = "", stage: str = "") -> None:
    """Fail the job AND every non-terminal clip — nothing is ever left mid-flight."""
    job["status"] = "failed"
    job["error"] = code
    job["error_detail"] = detail[:300]
    if stage:
        job["error_stage"] = stage
    for c in job["clips"]:
        if c.get("status") not in ("ready", "failed"):
            _fail_clip(c, code, detail)


def _bump_render_gen(clip: dict) -> int:
    """Increment + return this clip's render generation. Call synchronously (no
    await before/after) right where a new render attempt starts, so the returned
    value can be captured as the attempt's identity. F7: a watchdog can mark a
    clip failed while its render task is still silently running in the
    background (asyncio doesn't actually cancel it) — if a retry/tweak then
    starts a NEWER render, the stale task must not be allowed to overwrite it
    when it eventually completes. Every write site checks _is_current_render
    first and silently discards its result if a newer generation has started."""
    clip["render_gen"] = clip.get("render_gen", 0) + 1
    return clip["render_gen"]


def _is_current_render(clip: dict, my_gen: int) -> bool:
    return clip.get("render_gen", 0) == my_gen


async def _validate_source_url(url: str) -> None:
    """Probe the source before handing it to AssemblyAI/Remotion — a bad URL used
    to hang the pipeline 5-15 minutes across two external services before failing.
    HEAD first; some CDNs reject HEAD, so fall back to a 1-byte ranged GET."""
    if not url.startswith(("http://", "https://")):
        return
    try:
        async with httpx.AsyncClient(timeout=SOURCE_PROBE_TIMEOUT_S, follow_redirects=True) as client:
            r = await client.head(url)
            if r.status_code in (405, 501):
                r = await client.get(url, headers={"Range": "bytes=0-0"})
            if r.status_code not in (200, 206):
                raise PipelineError("source_unreachable", f"source returned {r.status_code}", "transcribe")
    except PipelineError:
        raise
    except Exception as e:
        raise PipelineError("source_unreachable", str(e), "transcribe")


def _sweep_stuck_renders(jobs: dict, max_render_s: float | None = None) -> None:
    """Watchdog, swept on every GET poll (same zero-background-task pattern as
    _sweep_ttl_jobs): any clip stuck in 'rendering' past the watchdog budget is
    failed as render_stalled — this catches every stranding vector (bridge hang,
    task death, pre-finally crash) that used to leave clips spinning forever."""
    budget = max_render_s if max_render_s is not None else RENDER_WATCHDOG_S
    now = time.time()
    for job in jobs.values():
        for c in job.get("clips", []):
            if c.get("status") == "rendering" and now - c.get("render_started_at", now) > budget:
                _fail_clip(c, "render_stalled", f"render exceeded {int(budget)}s watchdog")
        if job.get("status") in ("transcribing", "editing", "rendering") \
                and now - job.get("created_at", now) > budget * 2:
            _fail_job(job, "render_stalled", "job exceeded the pipeline watchdog")


async def _run_pipeline(job_id: str):
    """Background pipeline: transcribe → edit → render. Contract: this function
    ALWAYS leaves the job and every clip in a terminal state."""
    job = _clip_jobs[job_id]
    try:
        job["status"] = "transcribing"
        for c in job["clips"]: c["status"] = "transcribing"
        await _validate_source_url(job["source_url"])
        transcript_id = await _submit_transcription(job["source_url"])
        if not transcript_id:
            raise PipelineError("transcribe_submit_failed", "AssemblyAI rejected the submission", "transcribe")
        transcript = await _poll_transcription(transcript_id)
        words = transcript["words"]
        job["words"] = words          # kept for conversational tweaks (re-editing needs the transcript)

        job["status"] = "editing"
        for c in job["clips"]: c["status"] = "editing"
        style = job["style"]
        script = job["script"]
        # Deterministic grounding: fillers from AssemblyAI disfluency tags (source of
        # truth for cuts) and emphasis regions for punch-in placement.
        _clean_words, filler_drops = strip_fillers(words)
        disfluency_spans = [(d.src_in, d.src_out) for d in filler_drops if d.reason == "filler"]
        emphasis_spans = _extract_emphasis_regions(words, transcript.get("auto_highlights"))
        system, user = prompts.edl_prompt(style, words, script, job["brand"], job["media_context"],
                                          disfluency_spans=disfluency_spans,
                                          emphasis_spans=emphasis_spans)
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
        try:
            edl_text = await anthropic(system, user, model=HAIKU, max_tokens=4000)
            edl_data = extract_json(edl_text, array=False)
        except HTTPException:
            # LLM down ≠ pipeline dead: the safe default edit (full footage +
            # caption timing + deterministic filler cuts) still renders fine.
            edl_data = None

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

        # Merge the deterministic filler drops in as source of truth (unless the
        # creator turned trimming off), then self-verify + repair the EDL once.
        if prefs.get("filler_trim") != "off":
            edl_data["drops"] = _merge_drops(edl_data.get("drops", []),
                                             [d.model_dump() for d in filler_drops])
        edl_data = await verify_and_repair_edl(style, edl_data, words, script,
                                               emphasis_spans=emphasis_spans)

        edl_data = _apply_edit_prefs(edl_data, prefs)
        # Resolve b-roll cues to real video URLs (Pexels) and attach the duet react
        # source — both must happen before the render plan is built.
        edl_data = await _resolve_broll(edl_data)
        # Unresolved stock b-roll is a WARNING, not a failure — but no longer silent.
        unresolved = [b.get("broll_query") or b.get("cue_text", "")
                      for b in (edl_data.get("broll") or [])
                      if b.get("source") != "own_media" and not b.get("resolved_url")]
        if unresolved:
            for c in job["clips"]:
                c.setdefault("warnings", []).extend(f"broll_unresolved: {q}"[:120] for q in unresolved)
        edl_data = _attach_react_source(edl_data, job)
        job["edl"] = edl_data

        job["status"] = "rendering"
        await _render_all_clips(job_id)

        # Ready ONLY if at least one clip actually delivered a render. The old
        # unconditional ready-set is how "ready" jobs with zero playable clips
        # reached the app.
        job["status"] = "ready" if any(c["status"] == "ready" for c in job["clips"]) else "failed"
        if job["status"] == "failed" and not job.get("error"):
            first = next((c for c in job["clips"] if c.get("error")), None)
            job["error"] = (first or {}).get("error", "render_no_output")
            job["error_detail"] = (first or {}).get("error_detail", "")
            job["error_stage"] = "render"
    except PipelineError as e:
        _fail_job(job, e.code, e.detail, e.stage)
    except Exception as e:
        _fail_job(job, "internal_error", str(e))


async def _render_all_clips(job_id: str) -> None:
    """Render every non-ready clip from job['edl']. Per-clip isolation: one clip's
    failure marks THAT clip failed (with a structured code) and the others continue.
    Invariant on exit: every touched clip is 'ready' (with render_url) or 'failed'."""
    job = _clip_jobs[job_id]
    edl_data = job["edl"]
    if not (REMOTION_SERVE_URL and REMOTION_ACCESS_KEY and REMOTION_FUNCTION_NAME):
        for clip in job["clips"]:
            if clip.get("status") != "ready":
                clip["status"] = "ready"
                clip["render_url"] = job["source_url"]
        return
    for clip in job["clips"]:
        if clip.get("status") == "ready":
            continue
        clip["status"] = "rendering"
        clip["render_started_at"] = time.time()
        my_gen = _bump_render_gen(clip)
        try:
            submission = await _submit_remotion_render(
                job["source_url"], edl_data, clip["format"], job["style"])
            if not submission:
                raise PipelineError("render_submit_failed", "no renderId from bridge", "render")
            clip["render_id"] = submission["render_id"]
            render_url = await _poll_remotion_render(
                submission["render_id"], submission["bucket_name"])
            if _is_current_render(clip, my_gen):
                clip["render_url"] = render_url
                clip["status"] = "ready"
        except PipelineError as e:
            if _is_current_render(clip, my_gen):
                _fail_clip(clip, e.code, e.detail)
        except Exception as e:
            if _is_current_render(clip, my_gen):
                _fail_clip(clip, "internal_error", str(e))


async def _submit_transcription(video_url: str) -> str | None:
    if not ASSEMBLY_KEY:
        return None
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.assemblyai.com/v2/transcript",
            headers={"authorization": ASSEMBLY_KEY},
            # disfluencies=True tags um/uh/false-starts with type="filler" (source of
            # truth for cuts); auto_highlights surfaces the key phrases for punch-ins.
            json={"audio_url": video_url, "auto_highlights": True,
                  "disfluencies": True, "speaker_labels": False},
        )
    if r.status_code != 200:
        return None
    return r.json().get("id")


def _normalize_words(raw: list[dict]) -> list[dict]:
    """Map AssemblyAI word objects ({text,start,end,confidence,type,...}) onto the
    EDL's expected shape ({word,start_ms,end_ms,confidence,type,is_emphasized}).
    Idempotent — already-normalized (mock) words pass through unchanged."""
    out = []
    for w in raw:
        out.append({
            "word": w.get("word") or w.get("text", ""),
            "start_ms": w.get("start_ms", w.get("start", 0)),
            "end_ms": w.get("end_ms", w.get("end", 0)),
            "confidence": w.get("confidence", 1.0),
            "type": w.get("type"),                      # "filler" | None
            "is_emphasized": bool(w.get("is_emphasized", False)),
        })
    return out


async def _poll_transcription(transcript_id: str, max_wait_s: int | None = None) -> dict:
    """Return {"words": [...normalized...], "auto_highlights": [...]}. Keyless returns
    empty (mock path never calls this). Live failures raise structured PipelineErrors —
    the old silent-empty return made bad transcriptions produce a caption-less,
    cut-less "safe default" edit with no indication anything went wrong."""
    if not ASSEMBLY_KEY:
        return {"words": [], "auto_highlights": []}
    budget = max_wait_s if max_wait_s is not None else TRANSCRIBE_MAX_S
    for _ in range(max(1, budget // 5)):
        await asyncio.sleep(5)
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
                headers={"authorization": ASSEMBLY_KEY},
            )
        data = r.json()
        if data.get("status") == "completed":
            words = _normalize_words(data.get("words", []))
            if not words:
                raise PipelineError("transcribe_failed", "transcription completed with no words "
                                    "(is there speech in the video?)", "transcribe")
            # `auto_highlights_result.results` is the list of highlight phrases.
            # Do NOT fall back to `data["auto_highlights"]` — that is AssemblyAI's
            # boolean request-echo flag (`true`), and returning it here made
            # _extract_emphasis_regions iterate a bool ('bool' object is not
            # iterable) whenever a clip had no highlights (short/music/sparse speech).
            highlights = (data.get("auto_highlights_result") or {}).get("results") or []
            if not isinstance(highlights, list):
                highlights = []
            return {"words": words, "auto_highlights": highlights}
        if data.get("status") == "error":
            raise PipelineError("transcribe_failed", str(data.get("error", ""))[:300], "transcribe")
    raise PipelineError("transcribe_timeout", f"no transcript after {budget}s", "transcribe")


def _extract_emphasis_regions(words: list[dict], auto_highlights: list[dict] | None = None,
                              min_confidence: float = 0.0) -> list[tuple[int, int]]:
    """Frame ranges worth a punch-in: words flagged is_emphasized, plus the spans of
    AssemblyAI auto-highlight phrases. Deduped + merged so the editor gets a clean
    list of 'emphasize here' regions instead of guessing."""
    spans: list[tuple[int, int]] = []
    for w in words:
        if w.get("is_emphasized"):
            a, b = ms_to_frame(w.get("start_ms", 0)), ms_to_frame(w.get("end_ms", 0))
            if b > a:
                spans.append((a, b))
    for h in (auto_highlights if isinstance(auto_highlights, list) else []):
        if not isinstance(h, dict):
            continue
        for ts in h.get("timestamps", []) or []:
            a, b = ms_to_frame(ts.get("start", 0)), ms_to_frame(ts.get("end", 0))
            if b > a:
                spans.append((a, b))
    if not spans:
        return []
    spans.sort()
    merged = [spans[0]]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:                          # overlap → merge
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _merge_drops(existing: list[dict], new: list[dict]) -> list[dict]:
    """Union of drop lists; a new drop is added only if it doesn't overlap an
    existing one (so deterministic filler cuts don't collide with the LLM's cuts)."""
    out = list(existing or [])
    for nd in new or []:
        a, b = nd.get("src_in", 0), nd.get("src_out", 0)
        if b <= a:
            continue
        if any(not (b <= e.get("src_in", 0) or a >= e.get("src_out", 0)) for e in out):
            continue                                     # overlaps → skip
        out.append(nd)
    out.sort(key=lambda d: d.get("src_in", 0))
    return out


async def verify_and_repair_edl(style: str, edl_data: dict, words: list[dict],
                                script: dict, emphasis_spans: list | None = None) -> dict:
    """Self-verify gate for the AI editor (the EDL analogue of quality_scripts): a
    strict HAIKU judge checks the invariants a renderer can't recover from — no
    overlapping/backwards segments, captions & overlays inside clip bounds, punch-ins
    on real emphasis, sane total duration. On a violation ONE SONNET repair pass fixes
    only the named issues. Any failure (or a repair that won't validate) falls back to
    the input EDL — the pipeline never breaks. Gated by AI_QUALITY; no-op keyless."""
    if not (AI_QUALITY and ANTHROPIC_KEY):
        return edl_data
    try:
        vsys, vusr = prompts.edl_verify_prompt(style, edl_data, words, emphasis_spans)
        verdict = extract_json(await anthropic(vsys, vusr, HAIKU, 900), array=False) or {}
    except HTTPException:
        return edl_data
    if verdict.get("verdict") == "pass" or not verdict.get("issues"):
        return edl_data
    try:
        rsys, rusr = prompts.edl_repair_prompt(style, edl_data, verdict.get("issues", []), words, script)
        repaired = extract_json(await anthropic(rsys, rusr, SONNET, 4000), array=False)
    except HTTPException:
        return edl_data
    if not repaired:
        return edl_data
    try:
        obj = EDL(**repaired)
        obj, _ = validate_and_repair(obj)
        return obj.model_dump()
    except Exception:
        return edl_data                                  # repair broke it → keep original


async def _run_render_bridge(*args: str, timeout_s: float | None = None) -> dict:
    """Remotion's render API (renderMediaOnLambda/getRenderProgress) is Node-only —
    there's no documented cross-language wire contract for invoking a deployed Lambda
    function directly. The Node bridge at render/dist/lambda-render.js (built from
    render/src/lambda-render.ts) is the integration point; AWS creds pass through via
    the subprocess's inherited environment (Remotion's SDK reads the exact env var
    names REMOTION_AWS_ACCESS_KEY_ID / REMOTION_AWS_SECRET_ACCESS_KEY itself).

    Hardened: the subprocess call is bounded (a hung node process used to strand a
    clip in 'rendering' forever), and errors come back in-band via `_error` so they
    reach the clip's error field instead of dying in a log line."""
    proc = await asyncio.create_subprocess_exec(
        "node", REMOTION_BRIDGE, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s or BRIDGE_CALL_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        return {"_error": f"bridge timed out after {timeout_s or BRIDGE_CALL_TIMEOUT_S:.0f}s"}
    if proc.returncode != 0:
        raw = stderr.decode(errors="replace")[:500]
        logging.warning("remotion bridge failed: %s", raw)
        try:
            detail = json.loads(raw).get("error", raw)
        except json.JSONDecodeError:
            detail = raw
        return {"_error": str(detail)[:300]}
    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError:
        raw = stdout.decode(errors="replace")[:300]
        logging.warning("remotion bridge non-JSON output: %s", raw)
        return {"_error": f"bridge non-JSON output: {raw}"}


async def _submit_remotion_render(source_url: str, edl: dict, format_id: str, style: str) -> dict | None:
    if not (REMOTION_SERVE_URL and REMOTION_FUNCTION_NAME):
        return None
    # Remotion Lambda composition IDs may only contain a-z, A-Z, 0-9, CJK, and "-" —
    # underscores are rejected at render time (discovered live: "Composition id can
    # only contain ... You passed Marque_TalkingHead"). Must match Root.tsx exactly.
    composition_id = f"Marque-{style.title().replace('_', '')}"
    # Transform the editorial EDL (source coords) into a render-ready plan: the actual
    # cut list + captions/overlays remapped to the post-cut output timeline. The
    # compositions consume this plan directly (they no longer trim it themselves).
    plan = build_render_plan(edl)
    input_props = json.dumps({"sourceUrl": source_url, "edl": plan, "formatId": format_id})
    result = await _run_render_bridge("submit", composition_id, input_props)
    if result.get("_error"):
        raise PipelineError("bridge_error", result["_error"], "render")
    if not result.get("renderId"):
        return None
    return {"render_id": result["renderId"], "bucket_name": result.get("bucketName", "")}


async def _poll_remotion_render(render_id: str, bucket_name: str,
                                max_wait_s: int | None = None) -> str:
    """Poll the Lambda render to completion. Fail-FAST: exponential backoff within a
    hard wall-clock budget, stall detection on overallProgress, and every failure
    raises a structured PipelineError. (The old version linear-polled for 10 minutes
    and returned None with no reason — the single biggest 'clips never finish' vector.)"""
    budget = max_wait_s if max_wait_s is not None else RENDER_POLL_MAX_S
    start = time.time()
    delays = [2.0, 4.0, 8.0]
    i = 0
    last_progress = -1.0
    last_change = start
    while time.time() - start < budget:
        await asyncio.sleep(delays[i] if i < len(delays) else 15.0)
        i += 1
        progress = await _run_render_bridge("poll", render_id, bucket_name)
        if progress.get("_error"):
            raise PipelineError("bridge_error", progress["_error"], "render")
        if progress.get("fatalErrorEncountered"):
            errs = progress.get("errors") or []
            detail = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e)
                               for e in errs)[:300] or "Lambda reported a fatal render error"
            raise PipelineError("render_fatal", detail, "render")
        if progress.get("done"):
            output = progress.get("outputFile")
            if not output:
                raise PipelineError("render_no_output", "render finished but produced no file", "render")
            return output
        p = float(progress.get("overallProgress") or 0.0)
        now = time.time()
        if p > last_progress:
            last_progress, last_change = p, now
        elif now - last_change > RENDER_STALL_S:
            raise PipelineError("render_stalled", f"progress stuck at {p:.0%} for {int(now - last_change)}s", "render")
    raise PipelineError("render_timeout", f"render exceeded {int(budget)}s budget", "render")


# ----- brand-scan + voice onboarding -----

def _normalize_apify_post(item: dict, platform: str) -> dict | None:
    """Map one Apify actor item onto the corpus shape prompts already consume
    ({caption, hashtags, likes, comments}) extended with the reel-analysis fields
    ({views, video_url, duration_s, posted_at}). Pure + defensive: actor output
    schemas drift between versions, so everything is .get() chains; items with no
    caption AND no video are dropped."""
    if platform == "tiktok":
        caption = (item.get("text") or item.get("desc") or "").strip()
        hashtags = [h.get("name", "") if isinstance(h, dict) else str(h)
                    for h in (item.get("hashtags") or [])]
        likes = item.get("diggCount") or item.get("likes") or 0
        comments = item.get("commentCount") or item.get("comments") or 0
        views = item.get("playCount") or item.get("views") or 0
        meta = item.get("videoMeta") or {}
        video_url = (meta.get("downloadAddr") or (item.get("mediaUrls") or [None])[0]
                     or item.get("videoUrl") or "")
        duration = meta.get("duration") or item.get("duration") or 0
        posted_at = item.get("createTimeISO") or item.get("createTime") or ""
        thumbnail = (meta.get("coverUrl") or meta.get("originalCoverUrl")
                     or (item.get("covers") or [None])[0] or "")
        author = ((item.get("authorMeta") or {}).get("name")
                  or item.get("authorName") or "")
    else:  # instagram
        caption = (item.get("caption") or "").strip()
        hashtags = item.get("hashtags") or []
        likes = item.get("likesCount") or item.get("likes") or 0
        comments = item.get("commentsCount") or item.get("comments") or 0
        views = item.get("videoViewCount") or item.get("videoPlayCount") or 0
        video_url = item.get("videoUrl") or ""
        duration = item.get("videoDuration") or 0
        posted_at = item.get("timestamp") or ""
        thumbnail = item.get("displayUrl") or ""
        author = item.get("ownerUsername") or item.get("ownerFullName") or ""
    if not caption and not video_url:
        return None
    return {"caption": caption[:600], "hashtags": [h for h in hashtags if h][:8],
            "likes": int(likes or 0), "comments": int(comments or 0),
            "views": int(views or 0), "video_url": video_url or "",
            "thumbnail_url": thumbnail or "", "author": (author or "").lstrip("@"),
            "duration_s": int(duration or 0), "posted_at": str(posted_at)}


async def _run_apify_actor(actor: str, payload: dict, timeout_s: int = 110) -> list[dict]:
    """Run a paid Apify actor synchronously and return its dataset items. Never
    raises — any failure (402 no-budget, timeout, network, non-list body) degrades
    to []. Logs a one-liner on the budget/HTTP failure so the Render logs show WHY
    a scrape came back empty (the difference between 'no budget' and 'no results')."""
    if not APIFY_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                params={"token": APIFY_KEY, "timeout": timeout_s - 20},
                json=payload,
            )
        if r.status_code not in (200, 201):
            detail = ""
            try:
                detail = (r.json().get("error", {}) or {}).get("type", "")
            except Exception:
                detail = r.text[:80]
            logging.warning("apify %s -> HTTP %d %s", actor, r.status_code, detail)
            return []
        items = r.json()
        return items if isinstance(items, list) else []
    except Exception as e:
        logging.warning("apify %s failed: %s", actor, e)
        return []


async def scrape_posts(handle: str, platform: str, limit: int = 10) -> list[dict]:
    """Scrape a specific creator's recent posts via Apify when keyed; else empty
    (caller supplies posts for testing / mock derive covers keyless)."""
    if not APIFY_KEY or not handle:
        return []
    handle = handle.lstrip("@")
    if platform == "tiktok":
        actor = "clockworks~tiktok-scraper"
        payload: dict = {"profiles": [handle], "resultsPerPage": limit,
                         "shouldDownloadVideos": False, "profileScrapeSections": ["videos"]}
    else:
        actor = "apify~instagram-scraper"
        payload = {"directUrls": [f"https://www.instagram.com/{handle}/"],
                   "resultsType": "posts", "resultsLimit": limit}
    items = await _run_apify_actor(actor, payload)
    posts = [p for p in (_normalize_apify_post(i, platform) for i in items if isinstance(i, dict)) if p]
    return posts[:limit]


def _niche_hashtags(niche: str) -> list[str]:
    """Turn a free-text niche into 1-2 search hashtags/terms (alnum-slugged)."""
    words = [w for w in re.split(r"[^a-z0-9]+", niche.lower()) if w]
    if not words:
        return []
    slug = "".join(words)[:30]
    tags = [slug]
    if words[0] != slug:
        tags.append(words[0])
    return tags


async def scrape_niche_posts(niche: str, limit: int = 20) -> list[dict]:
    """Scrape recent well-performing posts for a niche across IG (hashtag) + TikTok
    (search) via Apify. Returns normalized posts (platform tagged). Never raises."""
    if not APIFY_KEY or not niche.strip():
        return []
    tags = _niche_hashtags(niche)
    if not tags:
        return []

    async def _ig() -> list[dict]:
        items = await _run_apify_actor("apify~instagram-hashtag-scraper",
                                       {"hashtags": tags, "resultsLimit": limit})
        out = [_normalize_apify_post(i, "instagram") for i in items if isinstance(i, dict)]
        for p in out:
            if p:
                p["platform"] = "instagram"
        return [p for p in out if p]

    async def _tt() -> list[dict]:
        items = await _run_apify_actor("clockworks~tiktok-scraper",
                                       {"searchQueries": [niche], "resultsPerPage": limit,
                                        "shouldDownloadVideos": False})
        out = [_normalize_apify_post(i, "tiktok") for i in items if isinstance(i, dict)]
        for p in out:
            if p:
                p["platform"] = "tiktok"
        return [p for p in out if p]

    ig, tt = await asyncio.gather(_ig(), _tt(), return_exceptions=True)
    posts: list[dict] = []
    for res in (ig, tt):
        if isinstance(res, list):
            posts.extend(res)
    return posts


async def _transcribe_top_posts(posts: list[dict], top_n: int = 4) -> list[dict]:
    """Transcribe the creator's strongest recent reels (by views, then likes) so the
    derive step can weigh how they actually SPEAK, not just how they caption.
    Per-post failures are non-fatal (CDN URLs 403/expire); keyless is a no-op."""
    if not ASSEMBLY_KEY or not posts:
        return posts
    ranked = sorted((p for p in posts if p.get("video_url")),
                    key=lambda p: (p.get("views", 0), p.get("likes", 0)), reverse=True)[:top_n]
    if not ranked:
        return posts

    async def _one(post: dict) -> None:
        tid = await _submit_transcription(post["video_url"])
        if not tid:
            return
        result = await _poll_transcription(tid, max_wait_s=180)
        words = result.get("words") or []
        if words:
            post["transcript"] = " ".join(w.get("word", "") for w in words)[:1500]

    await asyncio.gather(*(_one(p) for p in ranked), return_exceptions=True)
    return posts


# ---------------------------------------------------------------------------
# Emulate creators — analyze a target creator's transferable style DNA and
# thread it into script/hook generation. Presets resolve instantly (hand-
# authored, keyless-safe); custom links resolve from memory cache → Supabase →
# live scrape, and NEVER scrape synchronously inside a generation call — a
# target that hasn't finished analyzing yet is silently omitted this round.
# ---------------------------------------------------------------------------

_emulation_cache: dict[str, dict] = {}   # handle.lower() -> profile
_EMULATION_CACHE_CAP = 1024


class EmulateAnalyzeRequest(BaseModel):
    handle: str
    platform: str = "instagram"


def _mock_emulation_profile(handle: str) -> dict:
    return {
        "top_hooks": [f"The thing nobody tells you about being @{handle}."],
        "hook_signals": ["curiosity"],
        "top_format": "direct-to-camera with a fast cut every few seconds",
        "pacing": "quick, confident, minimal pauses",
        "voice": {"funnyToSerious": 0.5, "polishedToRaw": 0.5, "teacherToPeer": 0.5},
        "never_borrow": [f"@{handle}'s specific stories or claims"],
    }


@app.post("/v1/emulate/analyze")
async def emulate_analyze(req: EmulateAnalyzeRequest):
    """Kick off (and cache) style analysis for a custom emulation target. Called
    fire-and-forget from onboarding — the profile resolves lazily on the next
    generation call via _resolve_emulation_profiles, so this never blocks the UI."""
    handle = req.handle.lstrip("@").lower()
    if not handle:
        raise HTTPException(status_code=422, detail="handle required")
    if handle in _emulation_cache:
        return {"mode": "cached", "ok": True}
    if _supabase_client:
        cached = await _supabase_client.load_emulation_profile(handle)
        if cached:
            _emulation_cache[handle] = cached
            return {"mode": "cached", "ok": True}

    posts = await scrape_posts(handle, req.platform)
    posts = await _transcribe_top_posts(posts)
    if not ANTHROPIC_KEY or not posts:
        profile = _mock_emulation_profile(handle)
        mode = "mock"
    else:
        try:
            sys, usr = prompts.derive_emulation_prompt(handle, posts)
            profile = extract_json(await anthropic(sys, usr, HAIKU, 900), array=False) \
                or _mock_emulation_profile(handle)
            mode = "live"
        except HTTPException:
            profile = _mock_emulation_profile(handle)
            mode = "mock"

    _emulation_cache[handle] = profile
    _cap_evict(_emulation_cache, _EMULATION_CACHE_CAP)
    if _supabase_client:
        await _supabase_client.upsert_emulation_profile(handle, req.platform, profile)
    return {"mode": mode, "ok": True}


async def _resolve_emulation_profiles(targets: list[dict]) -> list[dict]:
    """Resolve each {name, handle, platform, source} target to a style profile.
    Preset lookup by name is instant; custom targets resolve from the in-memory
    cache (backfilled from Supabase on miss). A target still mid-analysis (or
    never explicitly analyzed) is silently omitted — generation never blocks on
    a scrape, and a missing profile degrades to "no emulation this round", not
    an error."""
    if not targets:
        return []
    out: list[dict] = []
    for t in targets[:3]:
        name = t.get("name", "")
        if t.get("source") == "preset" and name in prompts.PRESET_EMULATION:
            out.append({"name": name, **prompts.PRESET_EMULATION[name]})
            continue
        handle = (t.get("handle") or "").lstrip("@").lower()
        if not handle:
            continue
        profile = _emulation_cache.get(handle)
        if not profile and _supabase_client:
            profile = await _supabase_client.load_emulation_profile(handle)
            if profile:
                _emulation_cache[handle] = profile
        if profile:
            out.append({"name": name or f"@{handle}", **profile})
    return out


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


# ----- onboarding brand digest (async job — clone of the _clip_jobs pattern) -----
# In-memory like _clip_jobs (wiped on deploy — accepted v1; the app falls back to
# local generation on 404). Stages: scraping → transcribing → deriving →
# writing_scripts → ready.

_digest_jobs: dict[str, dict] = {}


def _digest_public(job: dict) -> dict:
    """The poll payload — scan/scripts/pillar at top level (what the app decodes)."""
    result = job.get("result") or {}
    return {
        "mode": job.get("mode", "live"),
        "job_id": job["job_id"],
        "status": job["status"],
        "stage": job.get("stage", ""),
        "scan": result.get("scan"),
        "scripts": result.get("scripts") or [],
        "pillar": result.get("pillar", ""),
        "scanned_posts": result.get("scanned_posts", 0),
        "transcribed": result.get("transcribed", 0),
        "error": job.get("error"),
    }


@app.post("/v1/onboarding/digest")
async def create_digest_job(req: DigestRequest):
    """Comprehensive brand digest: scrape recent reels → transcribe the top ones →
    derive brand/voice/pillars → write 3 quality-gated starter scripts. Returns a
    job_id immediately; the app can be closed while it runs."""
    job_id = str(uuid.uuid4())
    job = {"job_id": job_id, "status": "running", "stage": "scraping",
           "mode": "live", "req": req, "result": None, "error": None,
           "created_at": time.time()}
    _digest_jobs[job_id] = job
    if not ANTHROPIC_KEY:
        # Keyless: complete synchronously with the mock derive + scripts so demo
        # mode and tests stay deterministic and instant.
        brand = req.d()
        scan = mock_derive(brand, req.posts)
        sreq = _digest_script_request(req, scan)
        job.update(status="ready", stage="ready", mode="mock",
                   result={"scan": scan, "scripts": mock_scripts(sreq),
                           "pillar": sreq.pillar, "scanned_posts": len(req.posts),
                           "transcribed": 0})
        return {"mode": "mock", "job_id": job_id, "status": "ready"}
    asyncio.create_task(_run_digest(job_id))
    return {"mode": "live", "job_id": job_id, "status": "running"}


@app.get("/v1/onboarding/digest/{job_id}")
async def get_digest_job(job_id: str):
    _sweep_ttl_jobs(_digest_jobs)
    if job_id not in _digest_jobs:
        raise HTTPException(status_code=404, detail="job not found")
    return _digest_public(_digest_jobs[job_id])


def _digest_script_request(req: DigestRequest, scan: dict) -> ScriptRequest:
    """Build the starter-scripts request from the derived scan (voice + first pillar
    flow straight from the digest evidence into the script pipeline)."""
    pillars = scan.get("pillars") or []
    first = pillars[0] if pillars else {}
    voice = scan.get("voice") or req.voice or {}
    catchphrases = (scan.get("voice") or {}).get("catchphrases") or req.catchphrases
    return ScriptRequest(
        niche=scan.get("niche") or req.niche,
        audience=req.audience, known_for=req.known_for, what_you_do=req.what_you_do,
        goal=req.goal, voice=voice, non_negotiables=req.non_negotiables,
        catchphrases=catchphrases,
        pillar=first.get("name", ""), pillar_summary=first.get("summary", ""),
        pillar_angle=first.get("angle", ""),
        example_topics=first.get("exampleTopics") or [],
        style=(req.preferred_styles[0] if req.preferred_styles else "talking_head"),
        count=3, creator_id=req.creator_id, memory=req.memory,
        emulation_targets=req.emulation_targets,
    )


async def _run_digest(job_id: str) -> None:
    job = _digest_jobs[job_id]
    req: DigestRequest = job["req"]
    brand = req.d()
    try:
        # 1) Evidence: caller-supplied posts (tests) or a real scrape.
        posts = req.posts
        if not posts and req.handle:
            posts = await scrape_posts(req.handle, req.scan_platform)

        # 2) Speech: transcribe the creator's strongest reels.
        job["stage"] = "transcribing"
        posts = await _transcribe_top_posts(posts)
        transcribed = sum(1 for p in posts if p.get("transcript"))

        # 3) Derive brand/voice/pillars from the best evidence available. Also
        # best-effort analyze any emulation target that hasn't been resolved yet
        # (e.g. the user linked a page seconds before hitting "Build my plan") —
        # the digest job is already a background task, so absorbing that scrape
        # here costs nothing the UI is waiting on.
        job["stage"] = "deriving"
        for t in req.emulation_targets:
            handle = (t.get("handle") or "").lstrip("@").lower()
            if t.get("source") == "custom" and handle and handle not in _emulation_cache:
                try:
                    await emulate_analyze(EmulateAnalyzeRequest(handle=handle, platform=t.get("platform", "instagram")))
                except Exception:
                    pass
        scan: dict | None = None
        if posts:
            sys, usr = prompts.derive_from_posts_prompt(brand, posts)
            scan = extract_json(await anthropic(sys, usr, OPUS, 2200), array=False)
        elif req.voice_transcript:
            sys, usr = prompts.voice_finalize_prompt(brand, req.voice_transcript)
            scan = extract_json(await anthropic(sys, usr, OPUS, 2200), array=False)
        scan = scan or mock_derive(brand, posts)
        if scan.get("pillars"):
            merged = {**brand, "niche": scan.get("niche", brand.get("niche", ""))}
            scan["pillars"] = await judge_and_fix_pillars(merged, scan["pillars"], posts or None)

        # 4) Starter scripts through the full quality gate.
        job["stage"] = "writing_scripts"
        sreq = _digest_script_request(req, scan)
        sreq.posts = posts
        script_out = await _generate_scripts(sreq)

        job["result"] = {"scan": scan, "scripts": script_out.get("scripts") or [],
                         "pillar": sreq.pillar, "scanned_posts": len(posts),
                         "transcribed": transcribed}
        job["status"] = "ready"
        job["stage"] = "ready"
    except Exception as e:  # never leave a job stuck in "running"
        job["status"] = "failed"
        job["error"] = str(e)


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
        _cap_evict(_media_cache, 256)
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
    _cap_evict(_media_cache, 256)
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


_broll_url_cache: dict[str, str] = {}


async def _resolve_broll(edl: dict) -> dict:
    """Resolve each b-roll cue (broll_query, source='stock') to a real portrait video URL
    via Pexels, in place. own_media entries (already have an asset/URL) are left alone.
    Cached by query so re-renders don't re-hit Pexels. No-op without PEXELS_KEY."""
    broll = edl.get("broll") or []
    if not broll or not PEXELS_KEY:
        return edl
    for b in broll:
        if b.get("resolved_url") or b.get("source") == "own_media":
            continue
        query = (b.get("broll_query") or b.get("cue_text") or "").strip()
        if not query:
            continue
        if query in _broll_url_cache:
            b["resolved_url"] = _broll_url_cache[query]
            continue
        url = await _fetch_pexels(query)
        if url:
            _broll_url_cache[query] = url
            _cap_evict(_broll_url_cache, 10_000)
            b["resolved_url"] = url
    return edl


def _attach_react_source(edl: dict, job: dict) -> dict:
    """duet_split: attach the reacted-to clip (the app supplies a direct, renderable URL).
    Share-URL resolution (TikTok/Reels → mp4) is a follow-up; today we accept a direct URL
    or an uploaded-clip URL as-is."""
    if job.get("style") != "duet_split":
        return edl
    url = (job.get("react_source_url") or "").strip()
    if not url:
        return edl
    kind = "image" if url.lower().rsplit("?", 1)[0].endswith((".png", ".jpg", ".jpeg", ".webp")) else "video"
    edl["react_source"] = {
        "resolved_url": url, "kind": kind,
        "credit_label": job.get("react_credit_label", ""),
    }
    return edl


# ---------------------------------------------------------------------------
# Phase 4: Learning loop routes
# ---------------------------------------------------------------------------

@app.post("/v1/posts/register")
async def register_post(req: PostRegisterRequest):
    """Register a scheduled post as a learning experiment."""
    if req.post_id in _post_registry:
        return {"mode": "mock", "status": "already_registered"}
    post_data = {
        "creator_id": req.creator_id,
        "platform": req.platform, "scheduled_at": req.scheduled_at,
        "pillar": req.pillar, "style": req.style,
        "format_id": req.format_id, "hook_signal": req.hook_signal,
        "predicted_score": req.predicted_score,
        "outcome_y": None, "settled": False, "metrics": None,
    }
    _post_registry[req.post_id] = post_data
    if _supabase_client:
        try:
            await _supabase_client.upsert_post(req.post_id, post_data)
        except Exception as e:
            logging.warning("supabase upsert_post failed: %s", e)
    return {"mode": "live" if SUPABASE_URL else "mock", "status": "registered", "post_id": req.post_id}


@app.post("/v1/metrics/ingest")
async def ingest_metrics(req: MetricsIngestRequest):
    """Ingest post metrics and update the learning bandit (idempotent on post_id)."""
    entry = _post_registry.get(req.post_id)
    if entry is None and _supabase_client:                # registered on another instance?
        try:
            entry = await _supabase_client.load_post(req.post_id)
            if entry:
                _post_registry[req.post_id] = entry
        except Exception as e:
            logging.warning("supabase load_post failed: %s", e)
    entry = entry or {}
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
            await _update_arm(creator_id, f"{dim}:{val}", y)

    entry["outcome_y"] = y
    entry["settled"] = True
    entry["metrics"] = m
    _post_registry[req.post_id] = entry
    if _supabase_client:
        try:
            await _supabase_client.upsert_post(req.post_id, entry)
        except Exception as e:
            logging.warning("supabase upsert_post (settled) failed: %s", e)

    return {"mode": "live" if SUPABASE_URL else "mock", "status": "ingested",
            "outcome_y": round(y, 3), "post_id": req.post_id}


@app.get("/v1/recommendations")
async def get_recommendations(niche: str = "", creator_id: str = "default"):
    """Return top 3 Thompson-sampled arms for the creator's home feed."""
    await _ensure_arms_loaded(creator_id)
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
    await _ensure_arms_loaded(creator_id)
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
    reply = _apply_persona_voice(reply, req.persona, req.response_length)
    return {"reply": reply, "memory_updates": updates, "intent": intent,
            "intent_args": intent_args, "chips": chips}


# Persona voice + response-length shaping for the MOCK path only (the live-Claude path
# gets this from converse_system's persona/length instructions instead). Keeps the
# deterministic reply logic above untouched — this just re-flavors the final string so
# the coach picker is visibly real even fully offline.
_PERSONA_OPENERS = {
    "machine": [
        "Let's GO. ", "Big number energy: ", "Here's the play — ", "No cap, ",
    ],
    "closer": [
        "Here's the move: ", "Straight talk — ", "ROI first: ", "Cut to it: ",
    ],
    "sergeant": [
        "Listen up. ", "No excuses. ", "Here's your order: ", "Discipline first: ",
    ],
}


def _apply_persona_voice(reply: str, persona: str, length: str) -> str:
    import random
    openers = _PERSONA_OPENERS.get(persona)
    if openers:
        reply = random.choice(openers) + reply[0].lower() + reply[1:]
    if length == "concise":
        reply = reply.split(". ")[0].rstrip(".") + "."
    elif length == "detailed" and not reply.endswith(("chips", "?")):
        reply = reply + " Want me to go deeper on any part of that?"
    return reply


async def _chain_scripts(req: ConverseRequest, intent_args: dict) -> list[dict]:
    """generate_scripts intent → run the real scripts engine and attach the results.
    Fully guarded: a malformed model-emitted intent_args (non-numeric count) or a
    malformed client brand dict must degrade to "no scripts this turn", never a
    500 that kills the whole conversational reply."""
    try:
        topic = (intent_args.get("topic") or req.brand.get("niche") or "your next post").strip()
        style = intent_args.get("style") or "talking_head"
        if style not in STYLES:
            style = "talking_head"
        try:
            count = max(1, min(3, int(intent_args.get("count") or 1)))
        except (TypeError, ValueError):
            count = 1
        angle = (req.memory.get("angle") or "").strip()
        sreq = ScriptRequest(
            niche=req.brand.get("niche", ""), audience=req.brand.get("audience", ""),
            known_for=req.brand.get("known_for", ""), what_you_do=req.brand.get("what_you_do", ""),
            goal=req.brand.get("goal", "Grow my audience"), voice=req.brand.get("voice", {}) or {},
            non_negotiables=req.brand.get("non_negotiables", []) or [],
            catchphrases=req.brand.get("catchphrases", []) or [],
            pillar=topic, pillar_summary=f"A one-off script request from conversation: {topic}",
            pillar_angle=angle, style=style, count=count, creator_id=req.creator_id,
            memory=req.memory or {},          # carry chat-learned memory into generation
            emulation_targets=req.brand.get("emulation_targets", []) or [],
        )
        result = await scripts(sreq)
        return result.get("scripts", [])
    except Exception as e:
        logging.warning("chain_scripts failed, degrading to reply-only: %s", e)
        return []


def _parse_intent_args(envelope: dict) -> dict:
    """Read intent args from the structured envelope's intent_args_json (a JSON
    string, because its shape varies by intent). Back-compat: accept a raw
    intent_args dict if a caller/model still emits one. Never raises."""
    raw = envelope.get("intent_args")
    if isinstance(raw, dict):
        return raw
    s = envelope.get("intent_args_json")
    if isinstance(s, str) and s.strip():
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


@app.post("/v1/converse")
async def converse(req: ConverseRequest):
    if req.mode not in ("chat", "voice"):
        req.mode = "chat"
    if len(req.messages) > 40:
        req.messages = req.messages[-40:]  # keep the recent tail, same window iOS already caps at
    if not ANTHROPIC_KEY:
        out = mock_converse(req)
        if out["intent"] == "generate_scripts":
            out["payload"] = {"scripts": await _chain_scripts(req, out.get("intent_args", {}))}
        elif out["intent"] == "day_plan":
            out["payload"] = {"plan": out.get("intent_args", {}).get("plan", {})}
        return {"mode": "mock", "reply": out["reply"], "memory_updates": out["memory_updates"],
                "intent": out["intent"], "payload": out.get("payload"), "suggested_chips": out["chips"]}

    stats = await _arms_for_prompt(req.creator_id)
    system = prompts.converse_system(req.mode, persona=req.persona, response_length=req.response_length)
    user = prompts.converse_user(req.brand, req.memory, req.messages,
                                 arm_stats=stats, trends=mock_trends(req.brand.get("niche", "")))
    envelope = None
    try:
        model = SONNET if req.mode == "voice" else OPUS
        # Structured output guarantees a valid envelope + a valid intent enum, so the
        # old parse-fail-and-retry dance is unnecessary.
        envelope = await anthropic_json(system, user, prompts.CONVERSE_ENVELOPE_JSON_SCHEMA, model, 1600)
    except HTTPException:
        envelope = None
    if not isinstance(envelope, dict) or not (envelope.get("reply") or "").strip():
        out = mock_converse(req)
        return {"mode": "mock", "reply": out["reply"], "memory_updates": out["memory_updates"],
                "intent": out["intent"], "payload": None, "suggested_chips": out["chips"]}

    intent = envelope.get("intent") if envelope.get("intent") in _VALID_INTENTS else "none"
    intent_args = _parse_intent_args(envelope)
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
# TTS proxy — provider-switchable; client falls back to AVSpeechSynthesizer
# when keyless. TTS_PROVIDER=cartesia|elevenlabs forces one; otherwise whichever
# key is present wins, Cartesia first (~3-4x cheaper per character and lower
# time-to-first-audio; ElevenLabs kept for maximum voice realism).
# ---------------------------------------------------------------------------

ELEVENLABS_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_DEFAULT_VOICE = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
CARTESIA_KEY = os.environ.get("CARTESIA_API_KEY", "")
CARTESIA_DEFAULT_VOICE = os.environ.get("CARTESIA_VOICE_ID", "")
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "").lower()
_tts_cache: dict[str, bytes] = {}


def _tts_provider() -> str:
    if TTS_PROVIDER in ("cartesia", "elevenlabs"):
        return TTS_PROVIDER
    if CARTESIA_KEY:
        return "cartesia"
    if ELEVENLABS_KEY:
        return "elevenlabs"
    return "mock"


async def _tts_elevenlabs(text: str, voice_id: str) -> bytes | None:
    voice = voice_id or ELEVENLABS_DEFAULT_VOICE
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
            headers={"xi-api-key": ELEVENLABS_KEY, "content-type": "application/json"},
            json={"text": text, "model_id": "eleven_turbo_v2_5"},
        )
    if r.status_code == 200 and r.content:
        return r.content
    logging.warning("tts: elevenlabs %d %s", r.status_code, r.text[:200])
    return None


async def _tts_cartesia(text: str, voice_id: str) -> bytes | None:
    voice = voice_id or CARTESIA_DEFAULT_VOICE
    if not voice:
        logging.warning("tts: cartesia keyed but CARTESIA_VOICE_ID unset")
        return None
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={"X-API-Key": CARTESIA_KEY, "Cartesia-Version": "2024-11-13",
                     "Content-Type": "application/json"},
            json={"model_id": "sonic-2", "transcript": text,
                  "voice": {"mode": "id", "id": voice},
                  "output_format": {"container": "mp3", "sample_rate": 44100,
                                    "bit_rate": 128000}},
        )
    if r.status_code == 200 and r.content:
        return r.content
    logging.warning("tts: cartesia %d %s", r.status_code, r.text[:200])
    return None


@app.post("/v1/tts")
async def tts(req: TTSRequest):
    from fastapi.responses import Response, JSONResponse
    text = (req.text or "").strip()[:1000]
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    provider = _tts_provider()
    if provider == "mock":
        return JSONResponse({"mode": "mock"})
    import hashlib
    key = hashlib.sha256(f"{provider}:{req.voice_id}:{text}".encode()).hexdigest()
    if key in _tts_cache:
        return Response(content=_tts_cache[key], media_type="audio/mpeg")
    try:
        synth = _tts_cartesia if provider == "cartesia" else _tts_elevenlabs
        audio = await synth(text, req.voice_id)
        if audio:
            _tts_cache[key] = audio
            _cap_evict(_tts_cache, 256)
            return Response(content=audio, media_type="audio/mpeg")
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
        # Deterministic placeholder still (no real reel video exists in mock mode),
        # but a real image URL — the iOS "Steal these" grid otherwise renders blank.
        "thumbnail_url": f"https://picsum.photos/seed/{handle.lstrip('@')}-{fmt}-{idx}/400/711",
        "video_url": "",
        "views": views + idx * 37_000,
        "likes": likes + idx * 4_100,
        "why_trending": why,
        "format_id": fmt,
        "style": style,
        "from_watched": watched,
    }


REELS_PAGE = 6

# ---------------------------------------------------------------------------
# Real reels — actual well-performing posts scraped from IG/TikTok via Apify.
# The fabricated _REEL_TEMPLATES above are used ONLY in keyless/dev mode; when
# APIFY_KEY is set (production) the "Steal these" grid serves ONLY real reels
# (watched creators' top posts + niche-trending posts). No fabricated handles.
# Stale-while-revalidate: a cold/stale read serves what's cached and kicks a
# background scrape — generation/UI never blocks on a 30-90s Apify run.
# ---------------------------------------------------------------------------

_watched_reels_cache: dict[str, dict] = {}   # "platform:handle" -> {"reels", "ts"}
_niche_reels_cache: dict[str, dict] = {}     # "niche:<slug>"    -> {"reels", "ts"}
_reels_refreshing: set[str] = set()
_WATCHED_REELS_TTL_S = 12 * 3600
_NICHE_REELS_TTL_S = 18 * 3600
_WATCHED_CACHE_CAP = 256
_NICHE_CACHE_CAP = 128

_VALID_REEL_FORMATS = {"pov-story", "listicle", "do-this-not-that",
                       "myth-buster", "broll-hook", "before-after"}


def _parse_watched(watched: str) -> list[tuple[str, str]]:
    """Parse the `watched` query param into [(platform, handle)]. New wire format
    is `platform:handle` (e.g. `tiktok:mrbeast`); a bare handle (old clients /
    tests) defaults to instagram. Handles lowercased, @-stripped."""
    out: list[tuple[str, str]] = []
    for tok in watched.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if ":" in tok:
            plat, _, handle = tok.partition(":")
            plat = plat.strip().lower()
            handle = handle.strip().lstrip("@").lower()
            if plat not in ("instagram", "tiktok"):
                plat, handle = "instagram", tok.lstrip("@").lower()
        else:
            plat, handle = "instagram", tok.lstrip("@").lower()
        if handle:
            out.append((plat, handle))
    return out


def _niche_cache_key(niche: str) -> str:
    return "niche:" + "".join(re.split(r"[^a-z0-9]+", niche.lower()))[:40]


def _compact_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _heuristic_reel_annotation(post: dict) -> dict:
    """Deterministic format/style/why/hook inference from a real post's caption +
    stats. Free (no LLM), so a reel refresh costs only the Apify scrape — respects
    the creator's scraping budget."""
    cap = (post.get("caption") or "").strip()
    low = cap.lower()
    if re.search(r"\b\d+\s+(things|rules|ways|tips|mistakes|reasons|signs|habits)\b", low) or re.match(r"^\s*\d+[\).\s]", cap):
        fmt = "listicle"
    elif any(w in low for w in ("myth", "wrong", "actually", "the truth", "lied", "lie")):
        fmt = "myth-buster"
    elif any(w in low for w in ("stop ", "don't", "dont", "instead", "never ")):
        fmt = "do-this-not-that"
    elif any(w in low for w in ("before", "after", "day 1", "30 days", "results", "transformation")):
        fmt = "before-after"
    elif post.get("duration_s", 0) and not cap:
        fmt = "broll-hook"
    else:
        fmt = "pov-story"
    style = "faceless" if (post.get("duration_s", 0) and len(cap) < 15) else "talking_head"
    views = post.get("views", 0) or post.get("likes", 0) * 10
    why = f"{_compact_count(views)} views — this {fmt.replace('-', ' ')} format is landing in the niche right now."
    src = (post.get("transcript") or cap or "").strip()
    hook = re.split(r"(?<=[.!?])\s", src)[0][:120] if src else ""
    return {"format_id": fmt if fmt in _VALID_REEL_FORMATS else "pov-story",
            "style": style, "why_trending": why, "hook_text": hook}


def _reel_from_post(post: dict, handle: str, platform: str, idx: int, watched: bool) -> dict:
    """Map a normalized Apify post → the ReelItem shape iOS renders. Stable id
    (hash of platform+handle+timestamp) so FeedStore dedupes across pages."""
    ann = _heuristic_reel_annotation(post)
    cap = (post.get("caption") or "").strip()
    first_line = cap.split("\n")[0].strip()
    title = re.sub(r"(#\w+\s*)+$", "", first_line).strip()[:80] or f"@{handle} — {ann['format_id'].replace('-', ' ')}"
    seed = post.get("posted_at") or cap or str(idx)
    sid = hashlib.sha1(f"{platform}:{handle}:{seed}".encode()).hexdigest()[:10]
    return {
        "id": f"real-{platform}-{handle}-{sid}",
        "creator_handle": (post.get("author") or handle).lstrip("@"),
        "platform": platform,
        "title": title,
        "hook_text": ann["hook_text"] or title,
        "transcript": post.get("transcript") or cap,
        "thumbnail_url": post.get("thumbnail_url") or "",
        "video_url": post.get("video_url") or "",
        "views": int(post.get("views", 0) or 0),
        "likes": int(post.get("likes", 0) or 0),
        "why_trending": ann["why_trending"],
        "format_id": ann["format_id"],
        "style": ann["style"],
        "from_watched": watched,
    }


async def _refresh_watched_creator(platform: str, handle: str) -> None:
    """Background: scrape one watched creator's top posts → real reels in cache."""
    key = f"{platform}:{handle}"
    try:
        posts = await scrape_posts(handle, platform, limit=8)
        if not posts:
            return
        posts = await _transcribe_top_posts(posts, top_n=2)
        posts.sort(key=lambda p: (p.get("views", 0), p.get("likes", 0)), reverse=True)
        reels = [_reel_from_post(p, handle, platform, i, True) for i, p in enumerate(posts[:6])]
        _watched_reels_cache[key] = {"reels": reels, "ts": time.time()}
        _cap_evict(_watched_reels_cache, _WATCHED_CACHE_CAP)
    except Exception as e:
        logging.warning("watched-reel refresh failed for %s: %s", key, e)
    finally:
        _reels_refreshing.discard(key)


async def _refresh_niche_reels(niche: str) -> None:
    """Background: scrape trending niche posts → real reels in cache."""
    key = _niche_cache_key(niche)
    try:
        posts = await scrape_niche_posts(niche, limit=20)
        posts = [p for p in posts if p.get("views", 0) >= 10_000]
        posts.sort(key=lambda p: (p.get("views", 0), p.get("likes", 0)), reverse=True)
        posts = posts[:18]
        reels = [_reel_from_post(p, p.get("author") or "creator",
                                 p.get("platform", "instagram"), i, False)
                 for i, p in enumerate(posts)]
        _niche_reels_cache[key] = {"reels": reels, "ts": time.time()}
        _cap_evict(_niche_reels_cache, _NICHE_CACHE_CAP)
    except Exception as e:
        logging.warning("niche-reel refresh failed for %s: %s", key, e)
    finally:
        _reels_refreshing.discard(key)


def _watched_real_reels(parsed: list[tuple[str, str]]) -> list[dict]:
    now = time.time()
    out: list[dict] = []
    for platform, handle in parsed:
        key = f"{platform}:{handle}"
        entry = _watched_reels_cache.get(key)
        if entry:
            out.extend(entry["reels"])
        stale = not entry or (now - entry["ts"]) > _WATCHED_REELS_TTL_S
        if stale and APIFY_KEY and key not in _reels_refreshing:
            _reels_refreshing.add(key)
            asyncio.create_task(_refresh_watched_creator(platform, handle))
    return out


def _niche_real_reels(niche: str) -> list[dict]:
    if not niche.strip():
        return []
    key = _niche_cache_key(niche)
    entry = _niche_reels_cache.get(key)
    out = list(entry["reels"]) if entry else []
    stale = not entry or (time.time() - entry["ts"]) > _NICHE_REELS_TTL_S
    if stale and APIFY_KEY and key not in _reels_refreshing:
        _reels_refreshing.add(key)
        asyncio.create_task(_refresh_niche_reels(niche))
    return out


class ReelsWarmRequest(BaseModel):
    handle: str = ""
    platform: str = "instagram"


@app.post("/v1/reels/warm")
async def reels_warm(req: ReelsWarmRequest):
    """Fire-and-forget: pre-scrape a newly-added watched creator so their real
    reels are cached before the user reaches the Home feed. Never blocks."""
    handle = req.handle.lstrip("@").lower()
    if not handle:
        raise HTTPException(status_code=422, detail="handle required")
    platform = req.platform if req.platform in ("instagram", "tiktok") else "instagram"
    key = f"{platform}:{handle}"
    cached = key in _watched_reels_cache
    if APIFY_KEY and key not in _reels_refreshing:
        _reels_refreshing.add(key)
        asyncio.create_task(_refresh_watched_creator(platform, handle))
    return {"ok": True, "mode": "live" if APIFY_KEY else "mock", "cached": cached}


@app.get("/v1/reels")
async def reels(niche: str = "", creator_id: str = "default", watched: str = "", cursor: int = 0):
    cursor = max(0, min(cursor, 50))
    parsed = _parse_watched(watched)
    if APIFY_KEY:
        # Production: ONLY real reels. Watched creators' top posts first, then
        # niche-trending. No fabricated filler — an empty list (cold cache) is
        # honest; iOS shows a "finding real reels" state and pull-to-refresh fills.
        corpus, seen = [], set()
        for r in _watched_real_reels(parsed) + _niche_real_reels(niche):
            if r["id"] not in seen:
                seen.add(r["id"])
                corpus.append(r)
        mode = "live"
    else:
        corpus = _mock_reels(niche, [h for _, h in parsed])
        mode = "mock"
    page = corpus[cursor * REELS_PAGE:(cursor + 1) * REELS_PAGE]
    next_cursor = cursor + 1 if (cursor + 1) * REELS_PAGE < len(corpus) else None
    return {"mode": mode, "reels": page, "next_cursor": next_cursor}


# ---------------------------------------------------------------------------
# Daily feed — server-composed mix of script suggestions + reels + a trend
#
# The full quality-gated script pipeline (best-of-N hooks + generate + judge +
# repair) is 4-6 LLM calls — measured ~40s live, which reads as "Couldn't load
# today's picks" on the client. Feed pages are cached per creator+params with a
# fast first paint (one cheap SONNET call, no judge) so the FIRST-EVER request
# still returns in seconds; a background task then upgrades the cache entry to
# the full quality-gated set for every subsequent fetch.
# ---------------------------------------------------------------------------

_FEED_MAX_PAGES = 5
_FEED_CACHE_TTL_S = 6 * 3600
_FEED_CACHE_CAP = 512

_feed_cache: dict[str, dict] = {}       # key -> {"items", "next_cursor", "mode", "ts"}
_feed_refreshing: set[str] = set()      # keys with a background upgrade already in flight


def _feed_topics(niche: str, cursor: int) -> str:
    topics = [
        f"the {niche or 'creator'} mistake everyone makes",
        f"what nobody tells beginners about {niche or 'your field'}",
        f"a myth in {niche or 'your niche'} that needs to die",
        f"the fastest win in {niche or 'your field'} this month",
        f"what I'd do differently starting {niche or 'out'} today",
    ]
    return topics[cursor % len(topics)]


def _feed_cache_key(creator_id: str, niche: str, audience: str, known_for: str,
                    goal: str, styles: str, watched: str, cursor: int) -> str:
    # Param-signature sub-key: a niche/style change invalidates the cached page
    # instead of serving stale content for a brand the creator just edited.
    sig = "|".join([niche, audience, known_for, goal, styles, watched, str(cursor)])
    return f"{creator_id}::{sig}"


async def _fast_feed_scripts(sreq: "ScriptRequest") -> dict:
    """First-paint script generation: ONE cheap SONNET call, no best-of-N hooks
    and no judge/repair pass. Bounded to 12s so a slow model never reproduces the
    original timeout — falls back to the deterministic mock set instead."""
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "scripts": mock_scripts(sreq)}
    pillar = {"name": sreq.pillar, "summary": sreq.pillar_summary,
              "angle": sreq.pillar_angle, "exampleTopics": sreq.example_topics}
    try:
        sys, usr = prompts.scripts_prompt(sreq.d(), pillar, sreq.style, sreq.count,
                                          sreq.media_context, sreq.posts or None)
        out = await asyncio.wait_for(
            anthropic_json(sys, usr, _array_schema("scripts", prompts.SCRIPT_JSON_ELEMENT),
                           SONNET, 2200, array_key="scripts"),
            timeout=12,
        )
        if not out:
            return {"mode": "mock", "scripts": mock_scripts(sreq)}
        return {"mode": "live", "scripts": out}
    except (HTTPException, asyncio.TimeoutError):
        return {"mode": "mock", "scripts": mock_scripts(sreq)}


async def _compose_feed_items(script_result: dict, niche: str, creator_id: str,
                              watched: str, cursor: int) -> tuple[list[dict], int | None]:
    """Shared item-composition body — the fast path, the cached path, and the
    background full-quality refresh all emit byte-identical FeedResp shapes."""
    items = [{"type": "script", "script": s} for s in script_result.get("scripts", [])[:3]]

    reel_result = await reels(niche=niche, creator_id=creator_id, watched=watched, cursor=cursor)
    items += [{"type": "reel", "reel": r} for r in reel_result.get("reels", [])[:4]]

    all_trends = mock_trends(niche)
    items.append({"type": "trend", "trend": all_trends[cursor % len(all_trends)]})

    reels_more = reel_result.get("next_cursor") is not None
    next_cursor = cursor + 1 if (cursor + 1 < _FEED_MAX_PAGES or reels_more) else None
    return items, next_cursor


async def _refresh_feed_page(key: str, sreq: "ScriptRequest", niche: str, creator_id: str,
                             watched: str, cursor: int) -> None:
    """Background upgrade: run the full quality-gated pipeline and overwrite the
    cache entry so the NEXT fetch for this key is both instant and high-quality."""
    try:
        script_result = await scripts(sreq)
        items, next_cursor = await _compose_feed_items(script_result, niche, creator_id, watched, cursor)
        _feed_cache[key] = {"items": items, "next_cursor": next_cursor,
                            "mode": script_result.get("mode", "mock"), "ts": time.time()}
        _cap_evict(_feed_cache, _FEED_CACHE_CAP)
    except Exception as e:
        logging.warning("feed background refresh failed for %s: %s", key, e)
    finally:
        _feed_refreshing.discard(key)


@app.get("/v1/feed")
async def feed(creator_id: str = "default", niche: str = "", audience: str = "",
               known_for: str = "", goal: str = "Grow my audience",
               styles: str = "", watched: str = "", cursor: int = 0, fresh: int = 0):
    cursor = max(0, min(cursor, 50))
    allowed = [s for s in styles.split(",") if s in STYLES] or list(STYLES.keys())
    style = allowed[cursor % len(allowed)]
    key = _feed_cache_key(creator_id, niche, audience, known_for, goal, styles, watched, cursor)

    cached = _feed_cache.get(key)
    fresh_enough = cached and (time.time() - cached["ts"]) < _FEED_CACHE_TTL_S
    if cached and fresh_enough and not fresh:
        return {"mode": cached["mode"], "items": cached["items"], "next_cursor": cached["next_cursor"]}

    sreq = ScriptRequest(
        niche=niche, audience=audience, known_for=known_for, goal=goal,
        pillar=_feed_topics(niche, cursor),
        pillar_summary="Daily feed suggestion",
        style=style, count=3, creator_id=creator_id,
    )

    if cached and not fresh:
        # Stale-while-revalidate: serve the last good page instantly, upgrade
        # in the background for next time.
        if key not in _feed_refreshing:
            _feed_refreshing.add(key)
            asyncio.create_task(_refresh_feed_page(key, sreq, niche, creator_id, watched, cursor))
        return {"mode": cached["mode"], "items": cached["items"], "next_cursor": cached["next_cursor"]}

    # No cache yet (first-ever fetch) — fast single-call path so the client
    # never waits on the full 4-6-call quality gate for its very first paint.
    script_result = await _fast_feed_scripts(sreq)
    items, next_cursor = await _compose_feed_items(script_result, niche, creator_id, watched, cursor)
    _feed_cache[key] = {"items": items, "next_cursor": next_cursor,
                        "mode": script_result.get("mode", "mock"), "ts": time.time()}
    _cap_evict(_feed_cache, _FEED_CACHE_CAP)

    # Kick the full quality-gated set in the background so the SECOND fetch
    # (e.g. pull-to-refresh, or the app reopening) gets the upgraded page.
    if key not in _feed_refreshing:
        _feed_refreshing.add(key)
        asyncio.create_task(_refresh_feed_page(key, sreq, niche, creator_id, watched, cursor))

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
            stats = await _arms_for_prompt(req.creator_id)
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
