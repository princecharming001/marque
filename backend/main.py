"""Marque backend — the server-side AI brain.

Holds every vendor key so the iOS app ships none (three-trust-plane model, docs/12). Every AI
route proxies Anthropic when ANTHROPIC_API_KEY is set and falls back to a deterministic mock
otherwise, so the whole surface is testable with zero keys. Prompt quality lives in prompts.py.
"""
from __future__ import annotations

import os
import json
import math
import re
import copy
import time
import uuid
import hashlib
import hmac
import asyncio
import random
import logging
import shutil
import tempfile
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import prompts
from prompts import OPUS, HAIKU, SONNET, STYLES, FORMAT_IDS
from contextlib import asynccontextmanager

from app.edl import (EDL, safe_default_edl, validate_and_repair, strip_fillers,
                     ms_to_frame, build_render_plan, apply_edl_ops,
                     style_capabilities, TWEAK_OP_TYPES,
                     assemble_edl, check_edl_invariants, clamp_edl_to_source)
from app import audio as audio_mod
from app import knowledge as knowledge_mod
from app import dossier as dossier_mod
from app import push as push_mod
from app import higgsfield as higgsfield_mod
from app import retention as retention_mod
from app import edit_lint as edit_lint_mod
from app import themes as themes_mod
from app import quality_sentry
import supabase_persistence as sp
from supabase_persistence import SupabaseClient


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # NON-BLOCKING (blank-home audit): awaiting the fleet learning-state load here held
    # EVERY first request (and the health check) behind serial Supabase roundtrips —
    # after a health-check kill the instance came back cold, stalled again, and got
    # killed again (the observed crash loop + the 104s first /v1/feed). Serving before
    # the load finishes is safe: _ensure_arms_loaded lazy-loads per creator and
    # ingest_metrics falls back to load_post on a registry miss.
    _spawn(_load_learning_state())
    _spawn(_warm_reels_on_boot())      # B-11: pre-warm known niches' reels (non-blocking)
    if palo_flags.PALO_PORT:           # Palo port: run the sweep jobs in-process (no cron services)
        _spawn(_palo_scheduler())
    yield
    if _anthropic_client is not None:
        await _anthropic_client.aclose()
    # Palo port: close the ported modules' pooled httpx clients too.
    from app import memory_v2 as _mv
    from app import metrics_pollers as _mp
    from app import palo_llm as _pl
    for _closer in (_pl.aclose, _mp.aclose, _mv.aclose):
        try:
            await _closer()
        except Exception:
            pass


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
# Post for Me — unified publishing API (per-post pricing, unlimited accounts, brings its
# own approved IG/TikTok apps so users OAuth-link via Post for Me). Replaces Ayrshare as
# the publish backend; the key stays server-side only. Empty => mock (nothing posts).
POSTFORME_KEY = os.environ.get("POSTFORME_KEY", "")
POSTFORME_BASE = os.environ.get("POSTFORME_BASE", "https://api.postforme.dev/v1")
APIFY_KEY = os.environ.get("APIFY_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_KEY", "")
BROLL_CANDIDATES = int(os.environ.get("BROLL_CANDIDATES", "6"))   # P4.1: Pexels per_page for vision re-rank
_HIGGSFIELD_MAX_PER_JOB = int(os.environ.get("HIGGSFIELD_MAX_PER_JOB", "2"))  # credit + latency cap
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
# The client compresses each take to fit under this cap before upload. Server-driven
# (returned in every mint response) so raising the Supabase storage tier is a
# backend-only change — the iOS upload ladder reads it and targets a bitrate to fit.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", "48000000"))
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

_bg_tasks: set = set()
# B-09: max seconds the DIRECT scrape endpoints (emulate/analyze, brand-scan/handle)
# will block a request before degrading to mock — kept under typical proxy timeouts.
# Background callers pass None to run unbounded.
_SCRAPE_BUDGET_S = 25.0


def _spawn(coro):
    """asyncio.create_task that keeps a STRONG reference to the task. The event loop
    only holds a weak reference, so an un-referenced fire-and-forget task can be
    garbage-collected mid-flight — stranding a clip job or leaving a _refreshing key
    stuck forever (audit B-08/F12). Auto-discards on completion."""
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


_arm_stats: dict[str, dict] = {}
# Creators whose durable arm history has been fetched from Supabase at least once this
# process. Distinct from `creator_id in _arm_stats` because _update_arm creates a local
# arm dict on cache-miss — keying "already loaded?" on _arm_stats presence let that
# optimistic n=1 arm suppress the real DB load and then clobber the creator's history
# on write-through (audit A-04). This set is the honest "loaded" signal.
_arms_loaded: set[str] = set()
_post_registry: dict[str, dict] = {}
# creator_id -> freeform niche, learned opportunistically (recommendations query,
# post register) so the bandit can seed cold arms from a niche prior. In-memory only,
# no migration; a miss just means the neutral Beta(1,1) prior, i.e. today's behavior.
_creator_niche: dict[str, str] = {}
# Durable backing store for the two dicts above. None keyless → pure in-memory (unchanged).
_supabase_client: SupabaseClient | None = (
    SupabaseClient(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None
)
push_mod.SUPABASE = _supabase_client   # UX-B2a: device tokens persist when Supabase is wired

# --- Palo port (branch palo-port) — memory/ledger store. None keyless => every ported
# path is a pure no-op; all capabilities gated OFF by app.palo_flags (PALO_PORT unset).
from app import exemplar, ideas, memory_v2, palo_flags, recall_ledger, strategy_compiler, tiers, track_insights, write_agent  # noqa: E402
from app.palo_persistence import make_store  # noqa: E402

_palo_store = make_store(SUPABASE_URL, SUPABASE_KEY)
# Internal cron auth — Render cron jobs POST with this token. Empty => the endpoint is
# closed (403 to everyone), so it's never publicly triggerable by default.
INTERNAL_CRON_TOKEN = os.environ.get("INTERNAL_CRON_TOKEN", "")
# Dev-only tier switcher (the iOS DEBUG demo button). OFF by default so it can never be used
# to grab a paid tier in prod — set ALLOW_DEV_TIER=1 only in local/stage.
ALLOW_DEV_TIER = os.environ.get("ALLOW_DEV_TIER", "0") != "0"


class _DevTierRequest(BaseModel):
    creator_id: str = "default"
    tier: str = ""                    # starter|growth|studio ; empty clears the override


def _tier_payload(creator_id: str, tier: str) -> dict:
    return {"creator_id": creator_id, "tier": tier, "entitlements": tiers.entitlements(tier),
            "metrics_sources": list(tiers.metrics_sources(tier))}


@app.post("/v1/dev/tier")
async def dev_set_tier(req: _DevTierRequest):
    """DEBUG demo: force this creator's tier so paid tiers can be tried without billing.
    403 unless ALLOW_DEV_TIER=1 (never on in prod)."""
    if not ALLOW_DEV_TIER:
        raise HTTPException(status_code=403, detail="dev tier override disabled")
    if req.tier:
        t = tiers.set_override(req.creator_id, req.tier)
        if _palo_store:
            try:
                await _palo_store.set_creator_tier(req.creator_id, t)
            except Exception as e:
                logging.warning("[dev] set_creator_tier failed: %s", e)
    else:
        tiers.clear_override(req.creator_id)
        t = tiers.DEFAULT_TIER
    return _tier_payload(req.creator_id, t)


@app.get("/v1/dev/tier")
async def dev_get_tier(creator_id: str = "default"):
    """Current resolved tier + entitlements (for the demo switcher to display)."""
    t = await tiers.tier_for(creator_id, _palo_store)
    return _tier_payload(creator_id, t)


# Per-kind "already running" latch: an overlapping schedule or a client retry can't start
# a second fleet sweep (double Opus spend, duplicate rows). In-process (single Render instance).
_cron_running: dict[str, bool] = {}


class _CronRequest(BaseModel):
    token: str = ""


def _cron_auth(req: "_CronRequest") -> None:
    """Constant-time token check — `hmac.compare_digest` avoids the timing side-channel of a
    plain `!=`. Empty token env => the endpoint is closed to everyone (403)."""
    if not (INTERNAL_CRON_TOKEN and hmac.compare_digest(req.token, INTERNAL_CRON_TOKEN)):
        raise HTTPException(status_code=403, detail="forbidden")


def _start_cron(kind: str, make_coro) -> dict:
    """Spawn the fleet sweep in the background and return immediately, so the cron HTTP call
    never blocks on per-creator LLM/network work (proxy-timeout safe). The latch prevents
    overlapping runs. `make_coro` is a 0-arg callable returning the sweep coroutine."""
    if _cron_running.get(kind):
        return {"started": False, "reason": "already_running"}
    _cron_running[kind] = True

    async def _runner():
        try:
            n = await make_coro()
            logging.info("[cron] %s sweep done: %s", kind, n)
        except Exception as e:
            logging.warning("[cron] %s sweep failed: %s", kind, e)
        finally:
            _cron_running[kind] = False

    _spawn(_runner())
    return {"started": True}


@app.post("/internal/cron/ideate")
async def cron_ideate(req: _CronRequest):
    """Nightly idea-bank sweep (Render cron). Token-guarded + flag-gated (IDEA_BANK)."""
    _cron_auth(req)
    if not palo_flags.enabled(palo_flags.IDEA_BANK):
        return {"started": False, "skipped": "flag_off"}
    return _start_cron("ideate", lambda: ideas.run_ideate_cron(_palo_store, time.time()))


@app.post("/internal/cron/insights")
async def cron_insights(req: _CronRequest):
    """Daily post-performance sweep: poll metrics → detect + write cards → deliver via APNs."""
    _cron_auth(req)
    if not palo_flags.enabled(palo_flags.TRACK_INSIGHTS):
        return {"started": False, "skipped": "flag_off"}
    return _start_cron("insights", lambda: track_insights.run_insights_cron(_palo_store, time.time(), settle_hook=_settle_from_scrape))


@app.post("/internal/cron/compile")
async def cron_compile(req: _CronRequest):
    """Weekly strategy-compile sweep. Gates: flag + allowlist + per-tier freshness."""
    _cron_auth(req)
    if not palo_flags.enabled(palo_flags.STRATEGY_COMPILER):
        return {"started": False, "skipped": "flag_off"}
    return _start_cron("compile", lambda: strategy_compiler.run_compile_cron(_palo_store, time.time()))


@app.post("/internal/cron/exemplar")
async def cron_exemplar(req: _CronRequest):
    """Daily exemplar-bank refresh sweep. Gates: flag + allowlist + freshness."""
    _cron_auth(req)
    if not palo_flags.enabled(palo_flags.EXEMPLAR_BANK):
        return {"started": False, "skipped": "flag_off"}
    return _start_cron("exemplar", lambda: exemplar.run_exemplar_cron(_palo_store, time.time()))


async def _quality_cron_generate_fast(creator_id: str, brand: dict, posts: list[dict]) -> list[dict]:
    """T3: the SAME code path a real feed_fast request hits — _fast_feed_scripts
    on a ScriptRequest built from the creator's own stored brand+posts."""
    sreq = ScriptRequest(**_brand_only(brand), pillar="Quality sentry sample",
                         pillar_summary="Daily production-path sample", pillar_angle="",
                         style="talking_head", count=3, posts=posts, creator_id=creator_id)
    res = await _fast_feed_scripts(sreq)
    return res.get("scripts", [])


async def _quality_cron_generate_full(creator_id: str, brand: dict, posts: list[dict]) -> list[dict]:
    """T3: the full judged /v1/scripts pipeline, same construction as above."""
    sreq = ScriptRequest(**_brand_only(brand), pillar="Quality sentry sample",
                         pillar_summary="Daily production-path sample", pillar_angle="",
                         style="talking_head", count=3, posts=posts, creator_id=creator_id)
    res = await scripts(sreq)
    return res.get("scripts", [])


@app.post("/internal/cron/quality")
async def cron_quality(req: _CronRequest):
    """T3: daily prod-sampling quality cron — re-generates through the real feed_fast
    (+ one rotating creator/day through the full judged pipeline) for a slice of the
    real creator roster, scores each batch, and writes a quality_scorecards row per
    (creator, path). Always on (no flag) — this is testing infra, not a user feature."""
    _cron_auth(req)
    return _start_cron("quality", lambda: quality_sentry.run_quality_cron(
        _palo_store, time.time(), _quality_cron_generate_fast, _quality_cron_generate_full))


async def _palo_scheduler():
    """In-process scheduler for the ported sweep jobs (the plan's chosen worker model — no
    separate Render cron services). Each sweep internally skips creators not due per their tier
    cadence + watermarks and is DB-deduped, so a restart / overlap is safe on a single instance.
    First run ~2 min after boot, then every PALO_SCHED_INTERVAL_S (default 6h). The cron HTTP
    endpoints stay as a manual-trigger fallback."""
    await asyncio.sleep(float(os.environ.get("PALO_SCHED_FIRST_DELAY_S", "120")))
    interval = float(os.environ.get("PALO_SCHED_INTERVAL_S", "21600"))
    while True:
        try:
            if palo_flags.enabled(palo_flags.IDEA_BANK):
                _start_cron("ideate", lambda: ideas.run_ideate_cron(_palo_store, time.time()))
            if palo_flags.enabled(palo_flags.TRACK_INSIGHTS):
                _start_cron("insights", lambda: track_insights.run_insights_cron(_palo_store, time.time(), settle_hook=_settle_from_scrape))
            if palo_flags.enabled(palo_flags.STRATEGY_COMPILER):
                _start_cron("compile", lambda: strategy_compiler.run_compile_cron(_palo_store, time.time()))
            if palo_flags.enabled(palo_flags.EXEMPLAR_BANK):
                _start_cron("exemplar", lambda: exemplar.run_exemplar_cron(_palo_store, time.time()))
            # T3: quality cron is DAILY, not every 6h tick — a watermark (not a flag;
            # this is always-on testing infra) gates it to once per UTC day, reusing
            # the same metric_watermarks table every other sweep already uses.
            import datetime as _dt
            today_ord = float(_dt.date.today().toordinal())
            last_day = await _palo_store.get_watermark("_system", "quality_cron_last_day")
            if last_day is None or last_day < today_ord:
                _start_cron("quality", lambda: quality_sentry.run_quality_cron(
                    _palo_store, time.time(), _quality_cron_generate_fast, _quality_cron_generate_full))
                await _palo_store.set_watermark("_system", "quality_cron_last_day", today_ord)
        except Exception as e:
            logging.warning("[palo_scheduler] tick failed: %s", e)
        await asyncio.sleep(interval)


async def _inject_strategy(system: str, creator_id: str) -> str:
    """Palo port (flag STRATEGY_COMPILER, OFF): append the creator's compiled strategy to
    a system prompt so script gen + converse are shaped by the brain. No-op off/keyless."""
    if not palo_flags.enabled(palo_flags.STRATEGY_COMPILER):
        return system
    try:
        block = await strategy_compiler.strategy_block(_palo_store, creator_id)
        return f"{system}\n\n{block}" if block else system
    except Exception as e:
        logging.warning("[strategy] inject failed: %s", e)
        return system


async def _inject_brain(system: str, creator_id: str, query: str = "") -> str:
    """Palo port: fold the full brain — compiled strategy + exemplar bank + never-re-pitch
    ledger (and, when a query is given, self-learned memory) — into a generation system
    prompt, so every script/steer/mimic/hook path is as brain-aware as converse (audit
    G1-G5). Each block is flag-gated + best-effort; off/keyless => unchanged."""
    system = await _inject_strategy(system, creator_id)
    if not palo_flags.real_creator(creator_id):
        return system
    blocks: list[str] = []
    try:
        if palo_flags.enabled(palo_flags.EXEMPLAR_BANK):
            ex = await exemplar.exemplar_block(_palo_store, creator_id)
            if ex:
                blocks.append(ex)
    except Exception as e:
        logging.warning("[brain] exemplar inject failed: %s", e)
    try:
        if palo_flags.enabled(palo_flags.MEMORY_V2):
            led = await recall_ledger.ledger_block(_palo_store, creator_id)
            if led:
                blocks.append(led)
            if query:
                mem = memory_v2.memory_block(
                    await memory_v2.retrieve(_palo_store, creator_id, query))
                if mem:
                    blocks.append(mem)
    except Exception as e:
        logging.warning("[brain] memory inject failed: %s", e)
    return system + ("\n\n" + "\n\n".join(blocks) if blocks else "")


async def _persist_creator(creator_id: str, **fields):
    """Best-effort durable write of per-creator brand facts (niche/goal). Absent
    `creators` table or any error is swallowed — this is opportunistic."""
    fields = {k: v for k, v in fields.items() if v}
    if not (_supabase_client and fields):
        return
    try:
        await _supabase_client.upsert_creator(creator_id, fields)
    except Exception as e:
        logging.warning("supabase upsert_creator failed: %s", e)


async def _load_learning_state():
    """Rehydrate the bandit + post registry from Supabase so a restart / new Render
    instance doesn't start cold. No-op keyless. Never blocks startup on failure."""
    if not _supabase_client:
        return
    try:
        try:
            for c in await _supabase_client.load_all_creators():   # rehydrate niches (A-10)
                cid, niche = c.get("creator_id"), c.get("niche")
                if cid and niche:
                    _creator_niche[cid] = niche
        except Exception as e:
            logging.warning("startup creators load failed: %s", e)
        posts = await _supabase_client.load_all_posts()
        for p in posts:
            pid = p.get("post_id")
            if pid:
                _post_registry[pid] = p
        async def _boot_arms(cid: str) -> None:
            arms = await _supabase_client.load_arm_stats(cid)
            if arms:
                _arm_stats[cid] = arms
            _arms_loaded.add(cid)                          # booted → don't re-load on first update
        # Parallel (was a serial per-creator loop): fleet startup time is one roundtrip,
        # not N — matters because this now runs behind live traffic, not before it.
        await asyncio.gather(*(_boot_arms(cid) for cid in
                               {p.get("creator_id") for p in posts if p.get("creator_id")}))
        logging.info("learning state loaded: %d posts, %d creators", len(_post_registry), len(_arm_stats))
    except Exception as e:
        logging.warning("startup learning-state load failed: %s", e)

DIMENSIONS = ["pillar", "style", "format_id", "hook_signal"]
KAPPA = 5.0
EXPLORATION_FLOOR = 0.15

# Cold-start: seed a new arm's Beta prior from its niche so Thompson sampling favors
# what tends to over-index in that niche BEFORE the creator has their own data. Small
# pseudo-count → a handful of real posts dominate it. Neutral arms (and every arm when
# the niche is unknown) get exactly Beta(1,1) — i.e. today's uniform prior, unchanged.
_PRIOR_PSEUDO = 1.5                      # niche prior weight, in pseudo-observations
_PRIOR_EFFECT = (0.65, 0.60, 0.57)       # optimism by rank in the niche's preferred list


def _niche_prior_for_arm(dim_value: str, niche: str) -> tuple[float, float]:
    """Beta(alpha, beta) prior for a fresh arm, from its niche. Returns the neutral
    (1.0, 1.0) — the existing uniform prior — unless the arm's value is among the
    niche's preferred hook_signals/formats/styles, where a small pseudo-count nudges
    it optimistic. Pillars are creator-specific → always neutral."""
    if not niche or ":" not in dim_value:
        return (1.0, 1.0)
    dim, val = dim_value.split(":", 1)
    key = {"hook_signal": "signals", "format_id": "formats", "style": "styles"}.get(dim)
    if not key:
        return (1.0, 1.0)
    prefs = prompts.niche_priors_for(niche).get(key, [])
    if val not in prefs:
        return (1.0, 1.0)
    e = _PRIOR_EFFECT[min(prefs.index(val), len(_PRIOR_EFFECT) - 1)]
    return (1.0 + _PRIOR_PSEUDO * e, 1.0 + _PRIOR_PSEUDO * (1.0 - e))


def _compute_raw(m: dict, goal: str = "grow") -> float:
    """The pre-sigmoid, goal-weighted engagement composite (0..~10). This is the
    scale on which lift is measured against the creator's own mean — the sigmoid y
    below squashes it into a bounded Beta-update signal, which is great for the
    bandit but useless for 'how much better than my average' (everything saturates).
    Keeping raw lets the learning UI make an honest relative claim."""
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
    return sum(w * rates.get(k, 0) * 100 for k, w in weights.items())


def _compute_y(m: dict, goal: str = "grow") -> float:
    import math
    return 1 / (1 + math.exp(-0.5 * (_compute_raw(m, goal) - 2.0)))


# Shrinkage for the DISPLAYED lift (distinct from KAPPA, which shrinks the sigmoid
# `effect` toward 0.5 for calibration). An arm's raw mean is pulled toward the
# creator's own mean by LIFT_KAPPA pseudo-observations: 1.0 means a single fluke at
# n=1 reads halfway to the mean, while a genuine 2× arm crosses the DRIVER band
# (1.8×) exactly when it first qualifies at n≥4. Small, because arms already need
# n≥4 to surface, so the data:prior ratio is ≥4:1.
LIFT_KAPPA = 1.0
_creator_mean_raw_cache: dict[str, float | None] = {}


def _creator_mean_raw(creator_id: str) -> float | None:
    """Mean raw-composite over the creator's settled posts (their personal baseline),
    or None if they have none yet. Cached; invalidated on each settle."""
    if creator_id in _creator_mean_raw_cache:
        return _creator_mean_raw_cache[creator_id]
    vals = [p["outcome_raw"] for p in _post_registry.values()
            if p.get("creator_id") == creator_id and p.get("settled")
            and isinstance(p.get("outcome_raw"), (int, float))]
    mean = (sum(vals) / len(vals)) if vals else None
    _creator_mean_raw_cache[creator_id] = mean
    return mean


def _invalidate_creator_mean(creator_id: str):
    _creator_mean_raw_cache.pop(creator_id, None)


def _arm_lift(stat: dict, mean_raw: float | None) -> tuple[int, bool]:
    """Percent lift of an arm vs the creator's baseline, and whether the claim is
    grounded. (0, False) when there's no raw history — callers must then make NO
    performance claim rather than invent one.

    AF-2 (audit): the denominator is n_raw — the count of settles that actually
    accumulated into sum_raw — NOT n. A row whose sum_raw covers fewer settles than n
    (sum_raw's DEFAULT 0.0 backfills history the composite never saw) would otherwise
    divide a partial sum by the full count and report a large fake negative lift as
    'grounded'. No n_raw → ungrounded, honestly."""
    sum_raw = stat.get("sum_raw")
    n_raw = stat.get("n_raw", 0)
    if sum_raw is None or n_raw <= 0 or not mean_raw or mean_raw <= 0:
        return 0, False
    arm_raw = (sum_raw + LIFT_KAPPA * mean_raw) / (n_raw + LIFT_KAPPA)
    return round((arm_raw / mean_raw - 1.0) * 100), True


async def _update_arm(creator_id: str, dim_value: str, y: float,
                      raw: float | None = None, niche: str = ""):
    # Merge this creator's durable arm history in BEFORE we create-on-miss, so a fresh
    # Render instance increments the real DB counts instead of resetting them to n=1
    # and then overwriting the row (audit A-04).
    await _ensure_arms_loaded(creator_id)
    if creator_id not in _arm_stats:
        _arm_stats[creator_id] = {}
    stats = _arm_stats[creator_id]
    if dim_value not in stats:
        # Beta prior: niche-seeded for a fresh arm, else the neutral (1,1). Stored so
        # the alpha/beta recompute below stays consistent; defaults to 1.0 for arms
        # loaded from Supabase (which don't persist the prior) → unchanged behavior.
        pa, pb = _niche_prior_for_arm(dim_value, niche or _creator_niche.get(creator_id, ""))
        stats[dim_value] = {"n": 0, "sum_y": 0.0, "prior_alpha": pa, "prior_beta": pb,
                            "alpha": pa, "beta": pb}
    s = stats[dim_value]
    pa, pb = s.get("prior_alpha", 1.0), s.get("prior_beta", 1.0)
    s["n"] += 1
    s["sum_y"] += y
    if raw is not None:                          # accumulate the raw composite for honest lift
        s["sum_raw"] = s.get("sum_raw", 0.0) + raw
        s["n_raw"] = s.get("n_raw", 0) + 1       # AF-2: honest denominator for _arm_lift
    s["effect"] = (s["sum_y"] + KAPPA * 0.5) / (s["n"] + KAPPA)
    # B-7: α/β include the decoupled feedback accumulators (fb_*) so a like/dislike shifts
    # Thompson sampling immediately, while n / sum_raw / confidence stay settled-posts-only
    # (honest "+N% lift" + "seen in N posts" claims never count free taps).
    s["alpha"] = pa + s["sum_y"] + s.get("fb_sum_y", 0.0)
    s["beta"] = pb + (s["n"] - s["sum_y"]) + (s.get("fb_n", 0.0) - s.get("fb_sum_y", 0.0))
    s["confidence"] = "confirmed" if s["n"] >= 8 else ("early_read" if s["n"] >= 4 else "insufficient")
    if _supabase_client:                                  # write-through (best-effort)
        try:
            if not await _supabase_client.upsert_arm_stat(creator_id, dim_value, s):
                logging.warning("supabase upsert_arm_stat wrote nothing: %s %s", creator_id, dim_value)
        except Exception as e:
            logging.warning("supabase upsert_arm_stat failed: %s", e)


# B-7: like/dislike reward values (env-tunable). Asymmetric around the 0.5 neutral point —
# a dislike is an active rejection (−0.40), a like is a weak pre-outcome signal (+0.15) —
# and quarter-weighted because taps are free while a settled post carries real reach/watch.
FEEDBACK_LIKE_Y = float(os.environ.get("FEEDBACK_LIKE_Y", "0.65"))
FEEDBACK_DISLIKE_Y = float(os.environ.get("FEEDBACK_DISLIKE_Y", "0.10"))
FEEDBACK_WEIGHT = float(os.environ.get("FEEDBACK_WEIGHT", "0.25"))


async def _update_arm_feedback(creator_id: str, dim_value: str, y: float, niche: str = ""):
    """B-7: fold a feed like/dislike into an arm's Thompson α/β via SEPARATE fb_n / fb_sum_y
    accumulators (weighted), leaving n / sum_raw / n_raw / confidence untouched so honest
    performance claims stay grounded in real settled posts. Mirrors _update_arm's arm setup."""
    await _ensure_arms_loaded(creator_id)
    stats = _arm_stats.setdefault(creator_id, {})
    if dim_value not in stats:
        pa, pb = _niche_prior_for_arm(dim_value, niche or _creator_niche.get(creator_id, ""))
        stats[dim_value] = {"n": 0, "sum_y": 0.0, "prior_alpha": pa, "prior_beta": pb,
                            "alpha": pa, "beta": pb}
    s = stats[dim_value]
    pa, pb = s.get("prior_alpha", 1.0), s.get("prior_beta", 1.0)
    s["fb_n"] = s.get("fb_n", 0.0) + FEEDBACK_WEIGHT
    s["fb_sum_y"] = s.get("fb_sum_y", 0.0) + FEEDBACK_WEIGHT * y
    s["alpha"] = pa + s.get("sum_y", 0.0) + s["fb_sum_y"]
    s["beta"] = pb + (s.get("n", 0) - s.get("sum_y", 0.0)) + (s["fb_n"] - s["fb_sum_y"])
    if _supabase_client:
        try:
            await _supabase_client.upsert_arm_stat(creator_id, dim_value, s)
        except Exception as e:
            logging.warning("supabase upsert_arm_stat (feedback) failed: %s", e)


async def _ensure_arms_loaded(creator_id: str):
    """Lazy-load a creator's arms from Supabase the first time this process touches
    them, MERGING durable history under any optimistic local arms (local wins per key).
    Keyed on _arms_loaded, not _arm_stats presence, so create-on-miss can't suppress
    the load. No-op keyless or once loaded."""
    if creator_id in _arms_loaded or not _supabase_client:
        return
    try:
        arms = await _supabase_client.load_arm_stats(creator_id)
    except Exception as e:
        logging.warning("lazy load_arm_stats failed: %s", e)
        return                                            # not marked loaded → retried later
    local = _arm_stats.setdefault(creator_id, {})
    for key, stat in arms.items():
        local.setdefault(key, stat)                       # DB fills gaps; local edits win
    if creator_id not in _creator_niche:                  # rehydrate niche for cold-arm seeding
        try:
            row = await _supabase_client.load_creator(creator_id)
            if row and row.get("niche"):
                _creator_niche[creator_id] = row["niche"]
        except Exception as e:
            logging.warning("lazy load_creator failed: %s", e)
    _arms_loaded.add(creator_id)


async def _attribute_settled_post(creator_id: str, post: dict) -> dict:
    """Attribute a just-settled post to the single dimension that most drove it —
    scoped to THIS post's own four dim:value arms, never the creator's globally
    strongest arm (a talking_head post must never be 'explained' by style:faceless).
    Deterministic by default; the live path (ANTHROPIC_KEY) lets the model phrase the
    verdict but only from the same pre-computed lifts, and any failure/keyless falls
    back to the deterministic result — standard keyless-mock discipline."""
    all_arms = await _arms_for_prompt(creator_id)
    own_keys = {f"{d}:{post.get(d, '')}" for d in DIMENSIONS if post.get(d)}
    scoped = [a for a in all_arms if f"{a['dimension']}:{a['value']}" in own_keys]
    deterministic = prompts.attribute_from_arms(scoped)
    if not (ANTHROPIC_KEY and AI_QUALITY and scoped):
        return deterministic
    try:
        sys, usr = prompts.attribution_prompt(post, scoped)
        data = extract_json(await anthropic(sys, usr, HAIKU, 300), array=False) or {}
    except HTTPException:
        return deterministic
    dim = data.get("dimension", "")
    # The model may only name a dimension THIS post used (or 'none'); anything else is
    # drift → trust the deterministic result rather than a hallucinated cause.
    if dim == "none":
        if deterministic["dimension"] == "none":
            return deterministic
        # Model demurred where the data has a signal — return an honest none-shape;
        # never echo unvalidated LLM fields (numbers included) back to the client.
        return {"dimension": "none", "arm_value": "", "lift_pct": 0, "band": "noise",
                "confidence": "insufficient",
                "verdict": str(data.get("verdict", ""))[:160] or deterministic["verdict"]}
    if dim not in DIMENSIONS or f"{dim}:{data.get('arm_value', '')}" not in own_keys:
        return deterministic
    # AF-1 (audit): the model PHRASES, Python owns the NUMBERS — lift/band/confidence
    # come from the computed arm, and a verdict that doesn't cite the real lift
    # verbatim is drift → deterministic phrasing (mirrors the coach card guard).
    arm = next((a for a in scoped
                if a["dimension"] == dim and a["value"] == data.get("arm_value")), None)
    if arm is None:
        return deterministic
    real_lift = int(arm["lift_pct"])
    band = prompts.classify_arm_lift(real_lift)
    verdict = str(data.get("verdict", ""))[:160]
    if f"{real_lift}%" not in verdict:
        verdict = f"{arm.get('label', 'This dimension')} — a {band} in your data."
    return {"dimension": dim, "arm_value": data.get("arm_value", ""),
            "lift_pct": real_lift, "band": band,
            "confidence": arm.get("confidence", "early_read"),
            "verdict": verdict}


COACH_MIN_SETTLED = 4          # need a real read before the coach speaks


async def _coach_insight(creator_id: str) -> dict | None:
    """The single strongest GROUNDED, non-noise arm for the Today coach — or None (the
    NO-INSIGHT gate). Deterministic: Python finds the insight from the honest per-creator
    bands (Loop A); the LLM later only phrases it. Silence is a valid, common output."""
    settled = sum(1 for p in _post_registry.values()
                  if p.get("creator_id") == creator_id and p.get("settled"))
    if settled < COACH_MIN_SETTLED:
        return None
    for a in await _arms_for_prompt(creator_id):        # already sorted by |lift|, grounded flagged
        if a.get("has_lift") and prompts.classify_arm_lift(a["lift_pct"]) != "noise":
            return {"dimension": a["dimension"], "value": a["value"], "lift_pct": a["lift_pct"],
                    "band": prompts.classify_arm_lift(a["lift_pct"]),
                    "confidence": a.get("confidence", "early_read"), "label": a["label"],
                    "n": a.get("n", 0)}
    return None


# creator_id -> when the Today card was last shown (UTC). ≤1 card/day gate, backed by
# creators.coach_last_shown so a restart / new instance doesn't re-show the same card.
_coach_shown: dict[str, datetime] = {}


# OPT-6: creators whose durable coach_last_shown we've already looked up this process —
# a never-shown creator otherwise costs one Supabase round-trip on EVERY coach poll.
_coach_rehydrated: set[str] = set()


async def _coach_last_shown(creator_id: str) -> datetime | None:
    """In-memory first; on miss, best-effort rehydrate from creators.coach_last_shown
    (once per process — OPT-6). Absent table/column/row (or keyless) just means
    'never shown' — falsy degrade."""
    ts = _coach_shown.get(creator_id)
    if ts is not None or not _supabase_client or creator_id in _coach_rehydrated:
        return ts
    _coach_rehydrated.add(creator_id)
    try:
        row = await _supabase_client.load_creator(creator_id) or {}
        raw = row.get("coach_last_shown")
        if raw:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            _coach_shown[creator_id] = ts
            return ts
    except Exception as e:
        logging.warning("coach_last_shown rehydrate failed: %s", e)
    return None


async def _coach_mark_shown(creator_id: str) -> None:
    """Stamp the daily gate: in-memory (authoritative this process) + best-effort
    durable write so tomorrow's instance still honors ≤1/day."""
    now = datetime.now(timezone.utc)
    _coach_shown[creator_id] = now
    await _persist_creator(creator_id, coach_last_shown=now.isoformat())

_COACH_DIM_WORD = {"style": "style", "format_id": "format",
                   "hook_signal": "hook", "pillar": "pillar"}


def _coach_template_card(insight: dict) -> dict:
    """Deterministic phrasing — the keyless mock AND the LLM-degrade fallback. Every
    number comes verbatim from the insight; this function cannot fabricate a stat."""
    val = insight["value"].replace("_", " ")
    word = _COACH_DIM_WORD.get(insight["dimension"], insight["dimension"])
    conf = insight["confidence"].replace("_", " ")
    body = (f"Your {val} {word} runs {insight['lift_pct']:+d}% vs your average over "
            f"{insight['n']} settled posts ({conf}).")
    if insight["lift_pct"] >= 0:
        return {"headline": f"{val.title()} is carrying you",
                "body": body, "cta": f"Lean into {val} on your next post."}
    return {"headline": f"{val.title()} is dragging you down",
            "body": body, "cta": f"Try a different {word} on your next post."}


@app.get("/v1/coach/today")
async def coach_today(creator_id: str = "default"):
    """The Today coach: at most ONE card per day, and only when Python found a real,
    grounded, non-noise signal (_coach_insight) — the LLM only phrases the handed
    numbers. Zero-settled creators get a setup nudge with NO performance claim.
    Silence ({"card": null}) is the common, correct output."""
    last = await _coach_last_shown(creator_id)
    if last and (datetime.now(timezone.utc) - last).total_seconds() < 86400:
        return {"card": None}
    # AF-7 (audit): claim the daily slot SYNCHRONOUSLY before any await — concurrent
    # requests otherwise both pass the gate during the insight/LLM awaits and each get
    # a card. Rolled back on the silent paths below.
    prior = _coach_shown.get(creator_id)
    _coach_shown[creator_id] = datetime.now(timezone.utc)

    def _release_claim():
        if prior is None:
            _coach_shown.pop(creator_id, None)
        else:
            _coach_shown[creator_id] = prior

    insight = await _coach_insight(creator_id)
    if insight is None:
        settled = sum(1 for p in _post_registry.values()
                      if p.get("creator_id") == creator_id and p.get("settled"))
        if settled == 0:
            await _coach_mark_shown(creator_id)
            return {"card": {"kind": "setup", "mode": "mock",
                             "headline": "Post one to start learning",
                             "body": "Once your first posts settle I can tell you what's "
                                     "actually moving your numbers — no guesses until then.",
                             "cta": "Record your first clip"}}
        _release_claim()                         # silence spends no daily slot
        return {"card": None}                    # data exists but no honest claim → silence
    card = _coach_template_card(insight)
    mode = "mock"
    if ANTHROPIC_KEY and AI_QUALITY:
        try:
            sys, usr = prompts.coach_card_prompt(insight)
            data = extract_json(await anthropic(sys, usr, HAIKU, 300), array=False) or {}
            # Accept the LLM's phrasing only if it kept the real lift verbatim — a
            # drifted or invented number falls back to the deterministic template.
            if (data.get("headline") and data.get("body")
                    and f"{insight['lift_pct']:+d}%" in str(data.get("body"))):
                card = {"headline": str(data["headline"])[:80], "body": str(data["body"])[:280],
                        "cta": str(data.get("cta", card["cta"]))[:80]}
                mode = "live"
        except HTTPException:
            pass
    await _coach_mark_shown(creator_id)
    return {"card": {**card, "kind": "insight", "mode": mode, "insight": insight}}


@app.get("/v1/suggestions/next-idea")
async def next_idea(creator_id: str = "default", niche: str = ""):
    """One next-video idea brief (title + hook + beats). Grounded in the creator's own
    strongest arm when one exists (via _coach_insight — same NO-INSIGHT honesty gate),
    else niche-prior framing. The grounding line is ALWAYS deterministic Python — the
    LLM may shape the idea but can never make the performance claim."""
    await _ensure_arms_loaded(creator_id)
    if niche:
        _creator_niche[creator_id] = niche
    niche = niche or _creator_niche.get(creator_id, "")
    insight = await _coach_insight(creator_id)
    # UX-G1: the idea's TOPIC comes from the same Thompson source as the feed — the
    # top arm's pillar steers, its (honest, deterministic) reason grounds when no
    # settled insight exists yet.
    arms = await _top_arms(creator_id, niche)
    pillar = (arms[0].get("pillar") or "") if arms else ""
    arm_reason = (arms[0].get("reason") or "") if arms else ""
    idea = prompts.mock_next_idea(niche, insight, pillar=pillar, arm_reason=arm_reason)
    mode = "mock"
    if ANTHROPIC_KEY and AI_QUALITY:
        try:
            sys, usr = prompts.next_idea_prompt(niche, insight, pillar=pillar)
            data = extract_json(await anthropic(sys, usr, HAIKU, 700), array=False) or {}
            beats = [str(b)[:200] for b in (data.get("beats") or []) if str(b).strip()][:5]
            if data.get("title") and data.get("hook") and len(beats) >= 3:
                idea = {"title": str(data["title"])[:120], "hook": str(data["hook"])[:200],
                        "beats": beats, "grounding": idea["grounding"]}
                mode = "live"
        except HTTPException:
            pass
    return {"idea": {**idea, "mode": mode}}


async def _arms_for_prompt(creator_id: str) -> list[dict]:
    """Shape raw bandit arms into the {lift_pct, label, confidence} form that
    prompts.learning_block() actually reads. Without this the raw arm dicts lack
    lift_pct/label, so learning_block always returns "" and post-performance
    never reaches script/hook/converse generation — the loop is cosmetic. Emit
    only arms with an early read (n>=4), strongest signals first."""
    await _ensure_arms_loaded(creator_id)
    mean_raw = _creator_mean_raw(creator_id)
    _dim_word = {"style": "style", "format_id": "format",
                 "hook_signal": "hook", "pillar": "pillar"}
    out = []
    for key, s in _arm_stats.get(creator_id, {}).items():
        if s.get("n", 0) < 4 or ":" not in key:
            continue
        dim, val = key.split(":", 1)
        lift, grounded = _arm_lift(s, mean_raw)
        word = _dim_word.get(dim, dim)
        if grounded:
            sign = "+" if lift >= 0 else ""
            label = f"{val.replace('_', ' ')} {word}: {sign}{lift}% vs your average"
        else:
            # No raw baseline yet — make NO performance claim (audit A-05/A2).
            label = f"{val.replace('_', ' ')} {word}: seen in {s.get('n', 0)} settled posts"
        out.append({**s, "dimension": dim, "value": val, "lift_pct": lift, "label": label,
                    "has_lift": grounded, "confidence": s.get("confidence", "early_read")})
    out.sort(key=lambda a: abs(a["lift_pct"]), reverse=True)
    return out


def _thompson_sample(creator_id: str, candidates: list, niche: str = "") -> list:
    import random
    stats = _arm_stats.get(creator_id, {})
    niche = niche or _creator_niche.get(creator_id, "")
    scored = []
    for c in candidates:
        s = stats.get(c)
        if s is not None:
            alpha, beta = s["alpha"], s["beta"]
        else:                                    # unseen arm → niche-seeded prior (neutral if unknown)
            alpha, beta = _niche_prior_for_arm(c, niche)
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


_SCHEMA_STRIPPED_ONCE = False


def _sanitize_schema(node):
    """Recursively strip JSON-schema keywords that native Structured Outputs rejects
    with a 400 (array length bounds): maxItems always, and minItems when it isn't 0/1.
    These are enforced in code at every call site, so dropping them changes no behavior
    except turning a silent hard failure into a working call. Logs once per process the
    first time it strips something, so a reintroduced keyword is at least visible."""
    global _SCHEMA_STRIPPED_ONCE
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "maxItems":
                if not _SCHEMA_STRIPPED_ONCE:
                    logging.warning("[schema] stripped unsupported keyword %r for structured outputs", k)
                    _SCHEMA_STRIPPED_ONCE = True
                continue
            if k == "minItems" and v not in (0, 1):
                if not _SCHEMA_STRIPPED_ONCE:
                    logging.warning("[schema] stripped unsupported %r=%r for structured outputs", k, v)
                    _SCHEMA_STRIPPED_ONCE = True
                continue
            out[k] = _sanitize_schema(v)
        return out
    if isinstance(node, list):
        return [_sanitize_schema(x) for x in node]
    return node


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
        # Defensive sanitize: Structured Outputs rejects array-length bounds other than
        # 0/1 (minItems) and maxItems entirely with a hard 400. A single such keyword
        # buried in a schema silently 400'd the whole edit-plan call and degraded every
        # edit to the default cut. Strip the unsupported keywords (the corresponding
        # shape checks live in code) so no future schema edit can resurrect that class.
        body["output_config"] = {"format": {"type": "json_schema", "schema": _sanitize_schema(schema)}}
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
                try:
                    _j = r.json()
                    # Surface a max_tokens truncation (script-quality audit): a cut-off
                    # structured response is invalid JSON that silently degrades to
                    # mock/[] downstream — logging it makes the "incomplete scripts"
                    # symptom diagnosable and flags when a max_tokens budget is too low.
                    if _j.get("stop_reason") == "max_tokens":
                        logging.warning("[anthropic] %s hit max_tokens=%d — response truncated",
                                        model, max_tokens)
                    return "".join(b.get("text", "") for b in _j.get("content", []))
                except (ValueError, KeyError, TypeError) as e:
                    # A malformed 200 body must be degradeable (HTTPException), not a raw
                    # ValueError that escapes every route's `except HTTPException`.
                    raise HTTPException(status_code=502, detail=f"upstream malformed body: {e}")
            if r.status_code in (429, 500, 502, 503, 529):
                last_err = f"upstream {r.status_code}"
                if delay is not None:
                    logging.warning("anthropic: attempt %d got %d, retrying in %.1fs", attempt, r.status_code, delay)
                    jitter = delay * 0.2 * (random.random() * 2 - 1)
                    await asyncio.sleep(delay + jitter)
                    continue
            # Non-retryable (400/401/403/404/...): log the API's error body — a bare
            # "upstream 400" hid a schema-rejection that silently killed edit authoring.
            _errbody = ""
            try:
                _errbody = r.text[:300]
            except Exception:
                pass
            logging.warning("anthropic: non-retryable %d for %s: %s", r.status_code, model, _errbody)
            raise HTTPException(status_code=502, detail=f"upstream {r.status_code}: {_errbody}")
        # httpx.HTTPError is the transport base (Timeout/Connect/Read/RemoteProtocol/Pool);
        # catching only Timeout/Connect let a mid-stream ReadError escape the route degrades.
        except httpx.HTTPError as e:
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
    # Quiz context the prompts read via brand_block(). These MUST be declared:
    # pydantic's default extra='ignore' silently dropped them for months, so the
    # blocker/comfort/pace strategy hints never fired (same failure class as the
    # EDL loose-key gotcha).
    primary_platform: str = ""
    stage: str = ""
    posting_frequency: str = ""
    biggest_blocker: str = ""
    camera_comfort: str = ""
    weekly_target: int = 0
    why_now: str = ""                 # the creator's stated trigger for starting now
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


class FeedRequest(Brand):
    """B-6/B3: POST /v1/feed — inherits the FULL Brand (niche/audience/known_for/goal/
    voice/catchphrases/non_negotiables/emulation_targets/...), not just 4 fields.
    Before B3 this only carried niche/audience/known_for/goal — the feed had NEVER seen
    the creator's voice, catchphrases, or banned words, which was the structural root
    cause of "scripts don't match my content." Plus the creator's memory, so Today's
    picks are personalized by what they told the orb in a yap session."""
    creator_id: str = "default"
    styles: str = ""
    watched: str = ""
    cursor: int = 0
    fresh: int = 0
    memory: dict = {}


# --- B3: brand-dict helpers shared by the feed + posts hydration + strategy staleness ---

_BRAND_FIELD_NAMES = set(Brand.model_fields.keys())


def _brand_only(d: dict) -> dict:
    """Narrow any dict (a FeedRequest.model_dump(), a stored profile row, ...) down to
    exactly Brand's fields, so it's always safe to spread into ScriptRequest(**brand)."""
    return {k: v for k, v in (d or {}).items() if k in _BRAND_FIELD_NAMES}


# Fields that materially change what a script SAYS — used for both the strategy-
# staleness hash and the feed cache key (a niche/voice/catchphrase edit must invalidate
# both instead of silently serving stale content for a brand the creator just edited).
_BRAND_HASH_FIELDS = ("niche", "audience", "known_for", "what_you_do", "goal", "voice",
                     "catchphrases", "non_negotiables", "emulation_targets")


def _brand_hash(brand: dict) -> str:
    if not brand:
        return ""
    try:
        payload = {k: brand.get(k) for k in _BRAND_HASH_FIELDS}
        return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]
    except (TypeError, ValueError):
        return ""


class FeedFeedbackRequest(BaseModel):
    """B-7: a Today's-picks like/dislike. `script` carries the bandit dims (pillar/style/
    formatId/hookSignal) + title/hook for fingerprinting."""
    creator_id: str = "default"
    verdict: str = "like"              # "like" | "dislike"
    niche: str = ""
    script: dict = {}


class MemoryDistillRequest(BaseModel):
    """B-8: end-of-voice-session memory safety net."""
    creator_id: str = "default"
    transcript: list[dict] = []        # [{role, text}] of the session
    memory: dict = {}
    brand: dict = {}


class HooksRequest(Brand):
    topic: str = ""
    style: str = "talking_head"
    creator_id: str = "default"
    memory: dict = {}                  # client-held creator memory


class SteerRequest(Brand):
    script: dict = {}
    instruction: str = ""
    creator_id: str = "default"


class CaptionRequest(BaseModel):
    hook: str = ""
    body: str = ""


class TeardownRequest(BaseModel):
    clip: dict = {}


class InsightsRequest(Brand):
    summary: str = ""
    persona: str = "closer"       # C-09: coach voice


class ScanRequest(Brand):
    handle: str = ""
    platform: str = "tiktok"
    posts: list[dict] = []          # caller-supplied posts (testing) or filled by the scraper
    creator_id: str = "default"     # B3: so a scan's real posts persist to creator_posts


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
    # Loop F analyze-first flow: analyze the raw take → edit brief BEFORE editing.
    analyze_first: bool = False
    custom_instructions: str = ""       # free-text editing instructions from the creator
    # WS4: the creator's analyzed imported-media corpus (each {asset_id, description, tags,
    # broll_suitability, remote_url}). When b-roll is on, _resolve_broll scores these
    # against each cue and uses the creator's OWN footage before falling back to stock.
    corpus: list[dict] = []
    # The cut treatment the creator explicitly picked at submit time (a key of
    # prompts.EDIT_FORMATS). Empty = infer from the take (legacy behavior).
    edit_format: str = ""
    # Optional reference reel this cut should FEEL like — pacing/energy/caption
    # vibe, never the words. Whitelisted before storage (_clean_reference_reel).
    reference_reel: dict = {}
    # UX-B1a one-tap submit: run the WHOLE pipeline (transcribe → brief → confirm-
    # defaults → edit → render) without stopping at brief_ready. The response then
    # includes the clips array so the client can track/poll immediately. `toggles`
    # (broll/punch_ins/music) are applied as if the creator confirmed them; None →
    # the edit format's defaults. creator_id keys push notifications + learning.
    auto_confirm: bool = False
    toggles: dict | None = None
    creator_id: str = "default"
    # A7: explicit theme pick (a key of app.themes.THEMES). "" = infer from the
    # edit format's default_theme (prompts.EDIT_FORMATS[...]["default_theme"]).
    theme_id: str = ""
    # Addendum Part 1: creator style config. Only the knobs the current render honors are
    # read (broll_coverage, energy, allow_generated_broll); the rest (speaker_treatment,
    # pip_position, background_style) are accepted for forward-compat but await the
    # composition-mode render phase. Empty {} = exactly v1 behavior.
    config: dict = {}


class ConfirmRequest(BaseModel):
    toggles: dict = {}                  # broll/punch_ins/music (captions + cuts are always-on)
    custom_instructions: str = ""


class DeviceRegisterRequest(BaseModel):
    """UX-B2a: APNs device registration (POST /v1/devices)."""
    token: str
    environment: str = "sandbox"        # sandbox | prod (DEBUG builds → sandbox)
    creator_id: str = "default"
    platform: str = "ios"
    app_version: str = ""
    timezone: str = ""
    permission: str = ""                # authorized | denied | provisional | notDetermined


class TweakRequest(BaseModel):
    """One tweak turn on a finished clip: a natural-language instruction (chat)
    OR pre-typed ops (the manual editor) — ops bypass LLM interpretation entirely
    and go straight to deterministic application."""
    clip_id: str = ""
    instruction: str = ""
    ops: list[dict] = []


class RethemeRequest(BaseModel):
    """A7 feature #1: switch a finished clip's style bundle. clip_id="" retargets
    every clip on the job (the common case — most jobs have exactly one)."""
    theme_id: str = ""
    clip_id: str = ""


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
# F9: remember recently-swept job_ids so a subsequent lookup can tell "your
# session expired" (410) apart from "that id never existed" (404) — the client
# UX differs (re-record vs a bad/typo'd id). Bounded FIFO so this never grows
# unbounded; only needs to outlive a client's next poll after expiry.
_expired_job_ids: dict[str, float] = {}


def _sweep_ttl_jobs(jobs: dict, ttl_s: float = _JOB_TTL_S) -> None:
    """Evict jobs older than ttl_s. Sweep-on-access (called from the GET poll
    endpoints) rather than a background timer — no extra event loop task, and
    the in-memory job stores are already accepted-orphaned-on-restart, so a
    lazily-swept TTL is a strict improvement with zero new failure modes.

    Age is measured from the job's LATEST activity (created/stage-entry/restore),
    not created_at alone: a session restored from Supabase days later keeps its
    original created_at, and sweeping on that evicted the job on the very next
    poll after restore — an in-flight re-render then wrote to an orphaned dict
    (its "ready" never became visible) while the creator polled a 410. Jobs with
    work actively in flight are never TTL-evicted at all; the stall watchdog
    owns terminating runaway work, after which the TTL reaps them normally."""
    now = time.time()
    dead = []
    for jid, j in jobs.items():
        anchor = max(float(j.get("created_at") or now),
                     float(j.get("stage_started_at") or 0.0),
                     float(j.get("restored_at") or 0.0))
        if now - anchor <= ttl_s:
            continue
        in_flight = (j.get("status") in ("transcribing", "analyzing", "processing",
                                         "editing", "rendering")
                     or any(c.get("status") == "rendering"
                            or c.get("preview_status") == "rendering"
                            for c in j.get("clips") or []))
        if in_flight:
            continue
        dead.append(jid)
    for jid in dead:
        jobs.pop(jid, None)
        _expired_job_ids[jid] = now
    _cap_evict(_expired_job_ids, 4096)


def _raise_job_not_found(job_id: str) -> None:
    """404 for a never-existed id; structured 410 job_expired for a swept one."""
    if job_id in _expired_job_ids:
        raise HTTPException(status_code=410, detail="job_expired")
    raise HTTPException(status_code=404, detail="job_not_found")


class PostRegisterRequest(BaseModel):
    post_id: str
    clip_id: str = ""
    permalink: str = ""             # live post URL (B2: public-metric scrape join key)
    platform: str = "instagram"
    scheduled_at: str = ""
    pillar: str = ""
    style: str = ""
    format_id: str = ""
    hook_signal: str = ""
    predicted_score: int = 0
    creator_id: str = "default"
    niche: str = ""
    handle: str = ""                # Palo port: creator's social handle/account for metrics polling


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
    goal: str = "grow"          # the creator's objective; picks the _compute_y weighting
    niche: str = ""             # optional, for cold-arm Beta seeding consistency


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
    """Deterministic fallback ONLY when live generation is unavailable/slow. Kept
    believable (no "[Talking-Head] open on the hook" meta-instructions that read as a
    broken app) and niche-interpolated, but the real feed path is AI — this is a net."""
    s = STYLES.get(req.style, STYLES["talking_head"])
    niche = (req.niche or "your craft").strip()
    # A few distinct angle templates so three picks don't read identically.
    # W3: audience-facing + bracketed fill-ins — the keyless demo must not put invented
    # personal history ("I tracked my X for 90 days") in the creator's mouth either.
    # B-3: bodies are broken into short paragraphs (\n\n) — one beat each — so the reader/
    # teleprompter shows structure, not a wall of text (mirrors the live BODY_FORMAT_RULE).
    angles = [
        ("contrarian",
         f"Most {niche} advice is backwards. Here's what the top 1% actually do.",
         f"Everyone tells you to do more.\n\n"
         f"The people winning at {niche} do the opposite — they cut the noise and go deep on one thing.\n\n"
         f"Here's the one move to start this week."),
        ("specificity",
         f"One number decides most of your progress in {niche} — and you're probably not tracking it.",
         f"Pick the single metric that actually reflects progress in {niche}, and log it once a day for a week.\n\n"
         f"The thing most people obsess over barely moves it. This other habit does.\n\n"
         f"Here's how to run the test yourself."),
        ("authority",
         f"The part of {niche} nobody warns beginners about.",
         f"Almost everyone's first months in {niche} stall for the same reason — chasing the wrong signal.\n\n"
         f"Here's how to spot it early.\n\n"
         f"And the fix that costs nothing but attention."),
    ]
    out = []
    for i in range(max(1, req.count)):
        fmt = s["formats"][i % len(s["formats"])]
        signal, hook, body = angles[i % len(angles)]
        topic = (req.example_topics[i % len(req.example_topics)] if req.example_topics
                 else None)
        title = (topic or hook)[:48]
        title = title[:1].upper() + title[1:] if title else title
        entry = {
            "title": title,
            "summary": f"A {s['label'].lower()} on {niche}.",
            "hook": hook,
            "hookSignal": signal, "formatId": fmt,
            "body": body,
            "cta": "Follow for more — I break this down every week.",
            "shotPlan": s["exemplar"] and ["Hook on frame 1", "One punch-in on the key number", "Direct CTA"],
            "targetSeconds": 26,
            "altHooks": [], "style": req.style,
        }
        entry["predictedScore"] = _draft_score(req.creator_id, entry)
        out.append(entry)
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
    """Reject generic pillars. OPT-1: the INPUT set is judged first (one cheap HAIKU
    call) and returned when it passes — the common case. The 2-candidate OPUS
    regeneration only runs on failure; previously EVERY call burned two extra OPUS
    generations and never judged the input at all (~3× cost + a serial round-trip
    of onboarding latency for nothing)."""
    async def _judge_failures(candidate: list[dict]) -> list[str]:
        jsys, jusr = prompts.pillar_judge_prompt(brand.get("niche", ""), candidate)
        verdicts = extract_json(await anthropic(jsys, jusr, HAIKU, 800), array=True) or []
        return [candidate[v["index"]].get("name", "")
                for v in verdicts
                if isinstance(v, dict) and not v.get("pass", True)
                and 0 <= v.get("index", -1) < len(candidate)]

    avoid = await _judge_failures(pillars)
    if not avoid:
        return pillars                               # first draft passed — no regen spend
    for _ in range(2):
        # Generate 2 candidate sets in parallel, steering away from names the judge
        # already rejected this round (else the retry can reproduce them — audit B-10/F10).
        sys1, usr1 = prompts.pillars_prompt(brand, posts, avoid=avoid or None)
        sys2, usr2 = prompts.pillars_prompt(brand, posts, avoid=avoid or None)
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
        all_to_judge = candidate_sets[0]
        failed = await _judge_failures(all_to_judge)
        if not failed:
            return all_to_judge
        # Try the second candidate set if we have one
        if len(candidate_sets) > 1:
            failed2 = await _judge_failures(candidate_sets[1])
            if len(failed2) < len(failed):
                return candidate_sets[1]
        pillars = all_to_judge
        avoid = failed                # next regeneration avoids the rejected names
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
    generator's self-flattery. Hook dominates because it dominates retention.
    B4: relevance_to_creator folded in at 0.15 (renormalized from hook/specificity/
    format_fit) — a script that reads as generic advice for any creator in the niche,
    or is outright off-niche, must not score as if it were a well-targeted draft.
    Missing relevance_to_creator (an older cached judge response) falls back to the
    pre-B4 weights so a stale verdict shape doesn't silently zero out the score."""
    try:
        has_relevance = "relevance_to_creator" in v
        if has_relevance:
            s = (0.42 * float(v.get("hook_strength", 0))
                 + 0.20 * float(v.get("specificity", 0))
                 + 0.13 * float(v.get("format_fit", 0))
                 + 0.10 * float(v.get("voice_match", 0))
                 + 0.15 * float(v.get("relevance_to_creator", 0)))
        else:
            s = (0.50 * float(v.get("hook_strength", 0))
                 + 0.25 * float(v.get("specificity", 0))
                 + 0.15 * float(v.get("format_fit", 0))
                 + 0.10 * float(v.get("voice_match", 0)))
        if v.get("slop"):
            s -= 12
        if v.get("fabricated"):          # W3: a fabricated personal receipt is worse than slop
            s -= 15
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


# B5: the unjudged/mock paths (fast feed paint, keyless mocks) previously hardcoded
# predictedScore=78 — a fabricated number with no relationship to the actual draft.
# No LLM call (these paths are latency-critical); cheap deterministic craft signals
# instead, blended toward the creator's real outcomes via the SAME calibration
# machinery _final_score uses. Mirrors eval/invariants.py's SLOP_OPENERS (kept as its
# own small copy here — main.py must not import the eval/ harness).
_DRAFT_SLOP_OPENERS = (
    "in this video", "in today's video", "in todays video", "let me tell you",
    "here's the thing", "heres the thing", "ever wondered", "picture this",
    "buckle up", "welcome back", "hey guys", "what's up", "whats up",
    "let's dive in", "lets dive in", "without further ado", "today i want to talk",
)


def _draft_score(creator_id: str, script: dict) -> int:
    """Honest draft-tier predictedScore — no LLM. A DRAFT may never outrank the judged
    tier's typical floor (clamped 40..74), since it hasn't been through best-of-N +
    a critic pass. Base 60, adjusted by cheap deterministic craft signals, then blended
    toward the creator's real arm outcomes (same mechanism _final_score uses, so the
    number means something once there's evidence)."""
    hook = script.get("hook", "")
    hook = (hook.get("text", "") if isinstance(hook, dict) else hook) or ""
    hook = hook.strip()
    body = script.get("body", "") or ""
    hook_lower = hook.lower()
    words = hook.split()

    s = 60.0
    if 6 <= len(words) <= 14:
        s += 4
    if any(ch.isdigit() for ch in hook):
        s += 5
    if hook.endswith("?") or hook_lower.startswith(("why ", "what ", "how ", "when ", "who ")):
        s -= 6
    if hook_lower.startswith(_DRAFT_SLOP_OPENERS):
        s -= 10
    if "\n\n" in body:
        s += 3
    if not prompts.flag_stage_direction(body, script.get("style", "")):
        s += 5
    base = max(40, min(74, round(s)))

    cal, w = _calibration_signal(creator_id, script)
    if cal is None:
        return base
    return max(40, min(74, round((1 - w) * base + w * cal)))


async def quality_scripts(brand: dict, style: str, scripts: list[dict],
                          posts: list[dict] | None = None,
                          creator_id: str = "default",
                          mandated_hooks: list[dict] | None = None,
                          memory: dict | None = None) -> list[dict]:
    """Generate -> judge -> targeted self-repair for scripts. A strict HAIKU critic
    scores each draft; we swap in the strongest alt-hook, rewrite only the weak
    ones with OPUS, and re-ground predictedScore on the critic's axes calibrated
    against the creator's real learning-loop outcomes. Any failure falls back to
    the untouched drafts — this never strands generation."""
    if not (AI_QUALITY and scripts):
        return scripts
    try:
        # W3: give the judge the creator context so it can flag fabricated personal facts.
        jsys, jusr = prompts.script_judge_prompt(scripts, style, brand=brand, posts=posts, memory=memory)
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
                # Only adopt the alt's signal if it's a real taxonomy value — an
                # off-taxonomy signal would leak straight into the hook_signal arm.
                if alt.get("signal") in prompts.SIGNAL_LIST:
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
            # The revise pass exists to FIX quality issues — a rewrite that still reads
            # as a description is worse than the original draft. Skip the swap on a
            # dirty revise; the caller's final _ensure_speakable pass still covers the
            # pre-revise draft that's left in place.
            if prompts.flag_stage_direction(new["body"], style):
                logging.info("[speakable] revise output still dirty at pos=%d — keeping pre-revise draft", f["pos"])
                continue
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
                     n: int = 2, memory: dict | None = None,
                     emulation: list[dict] | None = None) -> list[dict]:
    """Best-of-N hooks: generate a diverse pool at temp 1.0, judge + drop slop via
    quality_hooks, and return the top n. These become MANDATED script openers — the
    body is written around a vetted hook instead of the model's first-draft guess.
    Returns [] keyless or on failure (caller then generates without a mandate)."""
    if not (BEST_OF_N_HOOKS and AI_QUALITY and ANTHROPIC_KEY):
        return []
    try:
        stats = await _arms_for_prompt(creator_id)
        hsys, husr = prompts.hooks_prompt(brand, topic, style, arm_stats=stats,
                                          memory=memory, emulation=emulation)
        hsys = await _inject_brain(hsys, creator_id, topic)   # G5: best-of-N hooks are brain-aware too
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
            "publish": "live" if POSTFORME_KEY else "mock",
            # "storage" surfaces whether recorded footage can actually be saved +
            # fetched by the pipeline. "mock" here means every real upload is doomed
            # (source unreachable) — the exact prod failure this makes visible.
            "storage": "live" if STORAGE_CONFIGURED else "mock",
            "tts": _tts_provider()}


@app.get("/v1/editor/capabilities")
def editor_capabilities():
    """Per-style edit-op capability map so the iOS editor hides toggles that would be
    silent no-ops in the current style (audit D4)."""
    from app.edl import style_capabilities
    return {"mode": "live", "capabilities": {s: style_capabilities(s) for s in STYLES}}


@app.get("/v1/themes")
def themes_catalog():
    """A7: the style-bundle catalog the editor picks from. `default_for_formats`
    lets the client pre-select the right theme chip when the creator has
    already chosen an edit format, without hardcoding the mapping client-side."""
    default_for: dict[str, list[str]] = {}
    for fmt, spec in prompts.EDIT_FORMATS.items():
        dt = spec.get("default_theme", "")
        if dt:
            default_for.setdefault(dt, []).append(fmt)
    return {"mode": "live", "themes": [
        {"id": t.id, "label": t.label, "blurb": t.blurb,
         "default_for_formats": default_for.get(t.id, [])}
        for t in themes_mod.THEMES.values()
    ]}


@app.get("/v1/music")
def music_catalog():
    """The music-bed catalog the editor picks from, so iOS shows the SAME tracks the
    render uses (no more client-only fallback drifting from the backend). Every URL is an
    AVPlayer-native, range-served bed; tags drive tone matching. Swap-able via the
    MUSIC_CATALOG env with zero code change."""
    return {"mode": "live", "tracks": [
        {"name": t.get("name", ""), "url": t.get("url", ""), "vibe": t.get("vibe", ""),
         "tone": t.get("tone", ""), "bpm": t.get("bpm", 0), "energy": t.get("energy", "")}
        for t in (MUSIC_TRACKS or _BUILTIN_MUSIC_TRACKS) if t.get("url")
    ]}


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
    # B3: second persist point (the feed POST is the other) — a direct /v1/scripts
    # caller's brand snapshot should also be kept fresh for GET-feed hydration + the
    # T3 cron. Dedup'd on unchanged hash inside _persist_creator_profile.
    _spawn(_persist_creator_profile(req.creator_id, _brand_only(req.d())))
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "scripts": mock_scripts(req)}
    pillar = {"name": req.pillar, "summary": req.pillar_summary,
              "angle": req.pillar_angle, "exampleTopics": req.example_topics}
    try:
        stats = await _arms_for_prompt(req.creator_id)
        emulation = await _resolve_emulation_profiles(req.emulation_targets)
        # Best-of-N: pre-select the strongest openers, then write bodies around them.
        topic = req.pillar or req.niche or "your next post"
        mandated = await best_hooks(req.d(), topic, req.style, req.creator_id, n=min(2, req.count),
                                    memory=req.memory or None, emulation=emulation or None)
        sys, usr = prompts.scripts_prompt(req.d(), pillar, req.style, req.count,
                                          req.media_context, req.posts or None,
                                          arm_stats=stats, memory=req.memory or None,
                                          mandated_hooks=mandated or None, emulation=emulation or None)
        sys = await _inject_brain(sys, req.creator_id)   # Palo port: strategy + exemplar + ledger (G4)
        out = await anthropic_json(sys, usr, _array_schema("scripts", prompts.SCRIPT_JSON_ELEMENT),
                                   OPUS, 3800, array_key="scripts")
        if not out:
            return {"mode": "mock", "scripts": mock_scripts(req)}
        out = await quality_scripts(req.d(), req.style, out, req.posts or None,
                                    creator_id=req.creator_id, mandated_hooks=mandated or None,
                                    memory=req.memory or None)
        # Final speakability gate on the judged pipeline: the judge's own slop axis
        # shares the old lint's blind spots, so a deterministic drop is the real guard.
        # Never ship fewer than requested — backfill any drop from the mock templates.
        out = await _ensure_speakable(out, policy="repair_or_drop")
        if len(out) < req.count:
            out = out + mock_scripts(req)[len(out):req.count]
        for s in out:
            if isinstance(s, dict) and s.get("title"):
                s["title"] = _clamp_title(str(s["title"]))
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
        sys = await _inject_brain(sys, req.creator_id, req.topic)   # G5: hooks seed a brain-aware body
        out = await anthropic_json(sys, usr, _array_schema("hooks", prompts.HOOK_JSON_ELEMENT),
                                   OPUS, 1200, array_key="hooks")
        out = await quality_hooks(req.topic, out)
        return {"mode": "live", "hooks": out}
    except HTTPException:
        # Live blip → the SAME populated shape the keyless mock returns, never a blank grid.
        return {"mode": "mock", "hooks": [{"text": f"The {req.topic} mistake nobody warns you about",
                                           "signal": "curiosity", "strength": 82}]}


@app.post("/v1/steer")
async def steer(req: SteerRequest):
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "script": req.script}
    try:
        stats = await _arms_for_prompt(req.creator_id)
        sys, usr = prompts.steer_prompt(req.d(), req.script, req.instruction, arm_stats=stats)
        sys = await _inject_brain(sys, req.creator_id, req.instruction)   # G1: refine stays brain-aware
        out = extract_json(await anthropic(sys, usr, SONNET, 1500), array=False)
        if out:
            # steer is unjudged — guard the body; the safe floor is the creator's own
            # pre-edit script, never a shipped description.
            (out,) = await _ensure_speakable(
                [out], policy="repair_or_keep_input", fallback=lambda i: req.script)
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
    has_metrics = (req.clip.get("metrics") or {}).get("views", 0) > 0
    if not ANTHROPIC_KEY:
        # No real per-post baseline exists → never fabricate a "beat N%" lift. Speak to
        # the CONTENT; liftPercent stays null unless there are real metrics to ground it.
        return {"mode": "mock",
                "headline": "Solid clip — here's the read" if not has_metrics else "Strong performer",
                "detail": "The hook lands in the first 2 seconds and the format keeps a visual change every few seconds.",
                "liftPercent": None}
    try:
        sys, usr = prompts.teardown_prompt(req.clip)
        out = extract_json(await anthropic(sys, usr, OPUS, 500), array=False) or {}
        lift = out.get("liftPercent")
        if not has_metrics:
            lift = None                        # discard any number the model invented without data
        return {"mode": "live", "headline": out.get("headline", ""), "detail": out.get("detail", ""),
                "liftPercent": lift}
    except HTTPException:
        return {"mode": "mock",
                "headline": "Solid clip — here's the read" if not has_metrics else "Strong performer",
                "detail": "The hook lands in the first 2 seconds and the format keeps a visual change every few seconds.",
                "liftPercent": None}


_MOCK_COACHING = "Your contrarian hooks are outperforming. Make two more in whichever format spiked."


@app.post("/v1/insights")
async def insights(req: InsightsRequest):
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "coaching": _MOCK_COACHING}
    try:
        sys, usr = prompts.insights_prompt(req.d(), req.summary, persona=req.persona)
        txt = (await anthropic(sys, usr, HAIKU, 250)).strip()
        return {"mode": "live", "coaching": txt or _MOCK_COACHING}
    except HTTPException:
        return {"mode": "mock", "coaching": _MOCK_COACHING}


class ScoreRequest(BaseModel):
    hook: str = ""
    body: str = ""
    style: str = "talking_head"


# all_scores.txt (High/Mid/Low) → a 0-100 read the app can display. Fluff is inverted
# (less filler = better). This scorer is deliberately kept OUT of the bandit reward.
_RATING_NUM = {"High": 85, "Mid": 60, "Low": 30}
_FLUFF_GOOD = {"Low": 90, "Mid": 60, "High": 30}


def _score_overall(hook: str, fluff: str, sat: str) -> int:
    return max(0, min(100, round(
        0.4 * _RATING_NUM.get(hook, 60) + 0.4 * _RATING_NUM.get(sat, 60)
        + 0.2 * _FLUFF_GOOD.get(fluff, 60))))


def _mock_score_script(hook: str, body: str) -> dict:
    """Deterministic keyless heuristic mirroring the all_scores axes so /v1/score is
    testable and never blank without a key."""
    h, b = (hook or "").strip(), (body or "").strip()
    words = len(b.split())
    text_l = (h + " " + b).lower()
    has_number = any(c.isdigit() for c in h + b)
    contrarian = any(w in h.lower() for w in
                     ("wrong", "myth", "stop", "nobody", "everyone", "actually", "don't", "backwards"))
    hook_pts = ((0 < len(h.split()) <= 16) + has_number + contrarian
                + bool(h and not h.rstrip().endswith("?")))
    hook_r = "High" if hook_pts >= 3 else "Mid" if hook_pts >= 1 else "Low"
    fluff_r = "High" if words > 130 else "Mid" if words > 80 else "Low"
    has_cta = any(w in text_l for w in ("try ", "save ", "follow", "comment", "do this", "here's"))
    sat_r = "High" if (has_cta and 20 <= words <= 120) else "Mid" if words >= 15 else "Low"
    weakest = ("fluff" if fluff_r == "High" else "hook" if hook_r == "Low"
               else "payoff" if sat_r != "High" else "none")
    fix = ("Tighten the body — cut any line that doesn't move the story." if fluff_r == "High"
           else "Open on a more specific, contrarian first line." if hook_r != "High"
           else "Land a clearer, more surprising payoff before the CTA.")
    return {"hook": hook_r, "fluff": fluff_r, "satisfaction": sat_r,
            "overall": _score_overall(hook_r, fluff_r, sat_r),
            "strongest": "hook" if hook_r == "High" else "payoff" if sat_r == "High" else "clarity",
            "weakest": weakest, "fix": fix}


@app.post("/v1/score")
async def score(req: ScoreRequest):
    """Independent Hook/Fluff/Payoff read on a script, for the creator to see BEFORE
    filming. Not a performance metric and not fed to the bandit."""
    if not ANTHROPIC_KEY:
        return {"mode": "mock", **_mock_score_script(req.hook, req.body)}
    try:
        sys, usr = prompts.score_script_prompt(req.hook, req.body, req.style)
        # temperature 0: the score is meant to be deterministic (same script → same read).
        out = extract_json(await anthropic(sys, usr, HAIKU, 400, temperature=0.0), array=False)
        if out and out.get("hook") in _RATING_NUM:
            out["overall"] = (int(out["overall"]) if isinstance(out.get("overall"), (int, float))
                              else _score_overall(out.get("hook"), out.get("fluff"), out.get("satisfaction")))
            out["overall"] = max(0, min(100, out["overall"]))
            return {"mode": "live", **out}
    except HTTPException:
        pass
    return {"mode": "mock", **_mock_score_script(req.hook, req.body)}


@app.get("/v1/trends")
async def trends(niche: str = ""):
    # W1: serve live niche trends (derived from the scraped reels corpus) when available;
    # else the mock set ROTATED by the 6h bucket so the ticker never looks frozen.
    live = _niche_live_trends(niche)
    if live:
        return {"mode": "live", "trends": live}
    base = mock_trends(niche)
    shift = _trend_bucket() % len(base)
    return {"mode": "mock", "trends": base[shift:] + base[:shift]}


# ---------------------------------------------------------------------------
# Edit-format example reels — the "match a vibe" cards shown before submit.
# Curated per-format exemplars communicate what each cut treatment FEELS like;
# real scraped reels of the matching style top the list when the niche cache
# has them. Serving is cache-only (never blocks on an Apify run).
# ---------------------------------------------------------------------------

_FORMAT_EXAMPLES = {
    "talking_head": [
        ("cameraconfident", "tiktok", "The {niche} advice I'd give my younger self",
         "One take, straight to camera — every cut lands on a sentence break.",
         "Clean single-take energy: tight trims, punch-ins on the key lines, zero gimmicks."),
        ("directwith{slug}", "instagram", "Why your {niche} progress stalled",
         "Direct eye contact, one idea, thirty seconds.",
         "The classic creator cut — face carries it, captions catch the skimmers."),
        ("real{slug}talk", "tiktok", "Nobody says this about {niche}",
         "A contrarian take delivered like a friend leveling with you.",
         "Conversational pacing with surgical filler removal — feels effortless, is not."),
    ],
    "talking_head_broll": [
        ("show{slug}", "instagram", "How I actually plan my {niche} week",
         "Talking head that cuts away to the desk, the notebook, the app.",
         "Every key noun gets 2s of b-roll — face for trust, cutaways for proof."),
        ("proofby{slug}", "tiktok", "3 {niche} tools I can't live without",
         "Each tool name triggers a cutaway of it in use.",
         "The show-don't-tell cut: b-roll on the nouns, hook and CTA stay on the face."),
        ("workingon{slug}", "instagram", "A {niche} mistake hiding in plain sight",
         "The claim on camera, the evidence in cutaways.",
         "Cutaways every 4-6 seconds keep watch time up without losing the person."),
    ],
    "recap_music": [
        ("{slug}inmotion", "instagram", "My week in {niche}, 20 seconds",
         "Hard cuts on the beat, music forward, captions carry the story.",
         "Montage energy: only the best beats survive, every cut hits with the track."),
        ("fastforward{slug}", "tiktok", "30 days of {niche} in 25 seconds",
         "A before-after arc told in rapid-fire moments.",
         "Music-driven recap — no talking, high energy, rewatch-loop pacing."),
        ("{slug}supercut", "instagram", "Everything we shipped this month",
         "5-8 punchy moments, one hard cut each, zero filler.",
         "The supercut treatment: momentum over narration, captions land the message."),
    ],
    "recap_voiceover": [
        ("{slug}narrates", "tiktok", "What a real {niche} day looks like",
         "Calm voiceover over the footage — the visuals change, the voice carries.",
         "Documentary-style recap: continuous narration, visuals cut on beat boundaries."),
        ("storyof{slug}", "instagram", "How this {niche} project actually went",
         "The honest story narrated over the b-roll of it happening.",
         "Voice-led recap — narration never cut mid-sentence, footage illustrates each beat."),
        ("behind{slug}", "tiktok", "The part of {niche} nobody films",
         "A reflective voiceover over unpolished, real footage.",
         "Vlog-style voiceover recap: intimate read, clean audio, captions on."),
    ],
}


def _format_example_reels(fmt: str, niche: str) -> list[dict]:
    """Deterministic curated example reels for one edit format (keyless + top-up)."""
    spec = prompts.EDIT_FORMATS[fmt]
    n = niche or "your niche"
    slug = "".join(c for c in n.lower().split()[0] if c.isalpha()) or "creator"
    fmt_id = (STYLES.get(spec["style"]) or {}).get("formats", ["myth-buster"])[0]
    out = []
    for i, (handle, platform, title, hook, why) in enumerate(_FORMAT_EXAMPLES[fmt]):
        out.append({
            "id": f"ex-{fmt}-{i}",
            "creator_handle": handle.format(slug=slug),
            "platform": platform,
            "title": title.format(niche=n),
            "hook_text": hook.format(niche=n),
            "transcript": "",
            "thumbnail_url": f"https://picsum.photos/seed/ex-{fmt}-{slug}-{i}/400/711",
            "video_url": "",
            "views": 1_200_000 + i * 310_000,
            "likes": 140_000 + i * 36_000,
            "why_trending": why,
            "format_id": fmt_id,
            "style": spec["style"],
            "from_watched": False,
            "edit_format": fmt,
        })
    return out


# The "match a vibe" picker is a STYLE chooser, not a reel-to-copy chooser. These are the
# theme bundles offered, ordered most-broadly-useful first. faceless_explainer is omitted
# — it's a voiceover-recap treatment, not a talking-head editing style.
_STYLE_GALLERY_ORDER = ["clean_creator", "hormozi_punch", "energetic_pop",
                        "docu_calm", "premium_brand"]


async def _global_th_demo_pool(niche: str) -> list[dict]:
    """The shared demo pool for the style/b-roll pickers: GENERAL talking heads pooled
    across EVERY cached niche + watched creator (a style choice is not niche-relevant),
    playable only, deduped by reel AND creator so options don't twin, durable-URL-first
    (raw CDN links 403 into static cards). Hydrates the niche's durable Supabase copy
    first so demos appear even on a cold post-deploy process, and kicks a background
    warm so the pool keeps filling."""
    if niche:
        try:
            await _hydrate_reels_caches(niche, [])
        except Exception:
            pass
    pool: list[dict] = []
    seen_ids: set = set()
    seen_handles: set = set()
    try:
        for entry in list(_niche_reels_cache.values()) + list(_watched_reels_cache.values()):
            for r in (entry.get("reels") or []):
                if not (r.get("video_url") and _is_talking_head_reel(r)):
                    continue
                rid = r.get("id")
                handle = (r.get("creator_handle") or "").lower()
                if rid in seen_ids or (handle and handle in seen_handles):
                    continue
                seen_ids.add(rid)
                if handle:
                    seen_handles.add(handle)
                pool.append(r)
    except Exception:
        pool = []
    _sb = SUPABASE_URL.rstrip("/") if SUPABASE_URL else "\x00"
    pool.sort(key=lambda r: (0 if (r.get("video_url") or "").startswith(_sb) else 1,
                             -(r.get("views") or 0)))
    if niche:
        key = _niche_cache_key(niche)
        entry = _niche_reels_cache.get(key)
        stale = not entry or (time.time() - entry.get("ts", 0)) > _NICHE_REELS_TTL_S
        if stale and APIFY_KEY and key not in _reels_refreshing:
            _reels_refreshing.add(key)
            _spawn(_refresh_niche_reels(niche))
    return pool


@app.get("/v1/styles")
async def styles_gallery(niche: str = ""):
    """The theme-bundle gallery (kept for wire compat — the record flow now uses
    /v1/broll-styles instead). Each style is illustrated by a real, playable talking-head
    reel; selecting one sends `theme_id` to POST /v1/clips (apply_theme). Demos are
    distinct per style; when no live reels exist the styles come back sample:true."""
    themes = [themes_mod.get_theme(tid) for tid in _STYLE_GALLERY_ORDER]
    pool = await _global_th_demo_pool(niche)
    out = []
    for i, th in enumerate(themes):
        demo = pool[i] if i < len(pool) else None      # distinct per style, no wrap
        out.append({
            "theme_id": th.id, "label": th.label, "blurb": th.blurb,
            "video_url": (demo or {}).get("video_url", ""),
            "thumbnail_url": (demo or {}).get("thumbnail_url", ""),
            "handle": (demo or {}).get("creator_handle", ""),
            "sample": demo is None,
        })
    return {"mode": "live" if pool else "mock", "styles": out}


# The "which composition style?" picker options (Addendum Part 2 vocabulary) — WHAT
# KIND of visual treatment, not how much. Each is illustrated by a real clip self-rendered
# through this exact pipeline (guaranteed pixel-accurate, unlike a scraped reel which can't
# be reliably classified into these treatments): see scripts/gen_composition_demos or the
# durable Supabase path demo-assets/composition-styles/<id>.mp4. The picked id returns to
# POST /v1/clips as config.composition_style (green_screen/split_screen → forces job style)
# or config.broll_mode (cutaway/panel/card → forces every b-roll insert's mode; see
# assemble_edl's broll loop in app/edl.py).
_DEMO_BASE = "https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/demo-assets/composition-styles"
_COMPOSITION_STYLE_OPTIONS = [
    {"id": "cutaway", "label": "Cutaways", "config_key": "broll_mode",
     "blurb": "Full-screen b-roll cuts in on key moments, then cuts back to you."},
    {"id": "panel", "label": "Panel Overlay", "config_key": "broll_mode",
     "blurb": "B-roll drops into a rounded panel up top — your face stays on screen."},
    {"id": "card", "label": "Floating Cards", "config_key": "broll_mode",
     "blurb": "Small b-roll card floats over your shoulder while you talk."},
    {"id": "green_screen", "label": "Green Screen", "config_key": "composition_style",
     "blurb": "You're composited over what you're reacting to, like a real green screen."},
    {"id": "split_screen", "label": "Split Screen", "config_key": "composition_style",
     "blurb": "Your face and the source clip share the frame side by side."},
]


class _ClientEventRequest(BaseModel):
    event: str = ""
    detail: str = ""
    creator_id: str = "unknown"
    build: str = "?"


@app.post("/v1/telemetry/client")
async def client_telemetry(req: _ClientEventRequest):
    """Client-failure breadcrumbs. An on-device failure between upload-mint and job
    creation (e.g. the chat edit flow memory-killing mid-upload) previously left ZERO
    server trace — clips showed 'failed' with nothing in any log. The app now reports
    those here so they're greppable in Render logs as [client]."""
    logging.warning("[client] build=%s creator=%s %s: %s",
                    req.build[:8], req.creator_id[:24], req.event[:40], req.detail[:300])
    return {"ok": True}


@app.get("/v1/broll-styles")
async def broll_styles(niche: str = ""):
    """The record flow's B-ROLL STYLE picker: WHICH composition treatment the creator
    wants (cutaway / panel / floating card / green screen / split screen), each option
    illustrated by a real clip self-rendered through this exact composition (not a
    scraped reel — our reel classifier can't reliably tag panel/card/green-screen/split
    treatments, so a self-render is the only way to guarantee the preview matches what
    the creator actually gets). The picked id returns to POST /v1/clips as
    config.broll_mode (cutaway/panel/card) or config.composition_style (green_screen/
    split_screen) and actually drives the edit."""
    return {"mode": "live", "styles": [
        {"id": opt["id"], "label": opt["label"], "blurb": opt["blurb"],
         "video_url": f"{_DEMO_BASE}/{opt['id']}.mp4", "thumbnail_url": "",
         "handle": "", "sample": False}
        for opt in _COMPOSITION_STYLE_OPTIONS
    ]}


@app.get("/v1/reels/examples")
async def reels_examples(format: str = "talking_head", niche: str = ""):
    """UX-A2: example reels for one edit format — what the creator's cut could feel
    like. Matching ladder: `edit_format` classification (dossier beats heuristic) →
    legacy engine-style match. Ranked by engagement + recency of work done (transcribed)
    + durable (re-hosted) playable URL; playable cards take the top slots. LIVE MODE
    NEVER PADS WITH FABRICATED CARDS — fewer real beats fake. The keyless/no-match
    fallback returns curated exemplars honestly flagged `sample: true`. Every card
    carries `selection_reason`. The picked reel returns to POST /v1/clips as
    `reference_reel` and steers the brief + EDL prompts."""
    fmt = format if format in prompts.EDIT_FORMATS else "talking_head"
    target_style = prompts.EDIT_FORMATS[fmt]["style"]
    out: list[dict] = []
    if niche:
        try:
            key = _niche_cache_key(niche)
            entry = await _prev_reels_entry(_niche_reels_cache, key)
            # Mimic cards must be previewable. If the niche was never warmed (or is stale),
            # kick a background scrape now — same trigger /v1/reels uses — so the NEXT open
            # of the record screen shows real, playable reels instead of unplayable samples.
            stale = not entry or (time.time() - entry.get("ts", 0)) > _NICHE_REELS_TTL_S
            if stale and APIFY_KEY and key not in _reels_refreshing:
                _reels_refreshing.add(key)
                _spawn(_refresh_niche_reels(niche))
            reels = (entry or {}).get("reels") or []

            def _tier(r: dict) -> int | None:
                if r.get("edit_format") == fmt:
                    return 0 if r.get("fmt_source") == "dossier" else 1
                if r.get("style") == target_style:
                    return 2                        # legacy style match, weakest signal
                return None

            sb_base = SUPABASE_URL.rstrip("/") if SUPABASE_URL else "\x00"
            def _rank(pair: tuple[int, dict]):
                t, r = pair
                eng = math.log10(max(10, (r.get("views") or 0) or (r.get("likes") or 0) * 10))
                bonus = (1.5 if r.get("transcribed") else 0.0) \
                      + (2.0 if (r.get("video_url") or "").startswith(sb_base) else 0.0)
                return (t, -(eng + bonus))

            cands = sorted(((t, r) for r in reels
                            if r.get("title") and (t := _tier(r)) is not None), key=_rank)
            ranked = [r for _, r in cands]
            # Top slots require a PLAYABLE card — unplayable ones sink, never lead.
            ranked = [r for r in ranked if r.get("video_url")] \
                   + [r for r in ranked if not r.get("video_url")]
            reason = {0: "Watched it — this reel cuts exactly like this treatment",
                      1: "Caption + pacing match this treatment",
                      2: "Same engine style in your niche"}
            tier_by_id = {r.get("id"): t for t, r in cands}
            for r in ranked[:6]:
                out.append({**r, "edit_format": r.get("edit_format") or fmt, "sample": False,
                            "selection_reason": f"{reason[tier_by_id.get(r.get('id'), 2)]} · "
                                                f"{_compact_count(r.get('views') or 0)} views"})
        except Exception:
            out = []                               # examples must never 500 over a cache hiccup
    if out:
        # LIVE: real matches only — even if fewer than 3. Honesty beats volume.
        return {"mode": "live", "format": fmt, "reels": out[:6]}
    fab = _format_example_reels(fmt, niche)[:6]
    for r in fab:
        r["sample"] = True
        r["selection_reason"] = "Curated sample — no live reels matched this treatment yet"
    return {"mode": "mock", "format": fmt, "reels": fab}


# ---------------------------------------------------------------------------
# Media upload + clip pipeline
# ---------------------------------------------------------------------------

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET", "marque-media")
R2_PUBLIC_BASE = os.environ.get("R2_PUBLIC_BASE", "https://media.marque.app")
# Supabase Storage is the primary object store for recorded footage (R2 was never
# provisioned; media.marque.app never resolved — see mint_upload_url). Reuses the
# same project as the learning stack (SUPABASE_URL/SUPABASE_KEY above). The bucket
# is PUBLIC so AssemblyAI + Remotion Lambda can fetch the source by URL; keys are
# unguessable UUIDs. Storage is "live" whenever the Supabase project is configured.
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "marque-clips")
STORAGE_CONFIGURED = bool(SUPABASE_URL and SUPABASE_KEY) or bool(R2_ACCESS_KEY)
ASSEMBLY_KEY = os.environ.get("ASSEMBLYAI_KEY", "")
REMOTION_SERVE_URL = os.environ.get("REMOTION_SERVE_URL", "")
REMOTION_ACCESS_KEY = os.environ.get("REMOTION_AWS_ACCESS_KEY_ID", "")
REMOTION_SECRET = os.environ.get("REMOTION_AWS_SECRET_ACCESS_KEY", "")
REMOTION_FUNCTION_NAME = os.environ.get("REMOTION_FUNCTION_NAME", "")
REMOTION_BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "render", "dist", "lambda-render.js")

# In-memory job store — the fast path for every request. Best-effort durably
# backed by Supabase clip_edit_sessions (F15) so a 24h TTL sweep or a Render
# restart doesn't lose a creator's edit session; see _persist_clip_job /
# _restore_clip_job below. Keyless/no Supabase configured → pure in-memory,
# unchanged from before.
_clip_jobs: dict[str, dict] = {}


async def _persist_clip_job(job_id: str) -> None:
    """Best-effort durable write-through for one edit session (F15). Call from
    a fire-and-forget asyncio.create_task at the end of any code path that
    materially changes a job (creation, pipeline completion, tweak, retry,
    re-render) — never awaited inline, so a slow/unavailable Supabase never
    adds latency to the hot path. No-op without Supabase configured."""
    if not _supabase_client:
        return
    job = _clip_jobs.get(job_id)
    if not job:
        return
    try:
        # OPT-4: the durable copy carries only the last 5 undo states — the full
        # 25-deep in-memory stack of full EDLs (each with per-word captions) pushed
        # each tweak's fire-and-forget PATCH toward ~1MB. Post-restart undo depth
        # drops to 5; in-process undo keeps all 25.
        durable = job
        if len(job.get("edl_history") or []) > 5:
            durable = {**job, "edl_history": job["edl_history"][-5:]}
        if "_theme" in durable:
            # A7: job["_theme"] holds the resolved Theme pydantic object (internal-only,
            # scoped to one _run_edit call) — not JSON-serializable and not needed for
            # restore (job["theme_id"] is the durable, honest record; retheme() and any
            # future restore path re-resolve the Theme from that id).
            durable = {**durable, "_theme": None}
        await _supabase_client.upsert_clip_job(job_id, durable)
    except Exception as e:
        logging.warning("supabase upsert_clip_job failed: %s", e)


async def _restore_clip_job(job_id: str) -> dict | None:
    """Lazy-restore an edit session from Supabase on an in-memory miss (this
    Render instance never saw it, a restart wiped it, or the 24h in-memory TTL
    swept it) — kills the "edit session expired" class for creators returning
    to a clip days later. Restores directly into _clip_jobs so the caller can
    proceed exactly as if it had been there all along. Returns None (does
    nothing) keyless, or when nothing durable exists for this id."""
    if not _supabase_client:
        return None
    try:
        state = await _supabase_client.load_clip_job(job_id)
    except Exception as e:
        logging.warning("supabase load_clip_job failed: %s", e)
        state = sp.UNAVAILABLE
    # "The DB couldn't answer" is NOT "the session doesn't exist": mapping a
    # transient Supabase outage to the caller's 404/410 made iOS declare the
    # creator's edit session expired (a destructive, re-record-your-video UX)
    # over a network blip. Surface 503 instead — the app's poll just tries again.
    if state is sp.UNAVAILABLE:
        raise HTTPException(status_code=503, detail="session_storage_unavailable")
    if not state:
        return None
    # Restore stamps its own clock: TTL age is measured from latest activity, so
    # a days-old session gets a fresh 24h in-memory lease instead of being
    # re-evicted by the very next poll's sweep (see _sweep_ttl_jobs).
    state["restored_at"] = time.time()
    # AF-3 (audit): setdefault, not assignment — two concurrent restorers (a tweak and
    # the 5s poll both missing memory after a restart) otherwise get two DIFFERENT dict
    # objects, and whichever assigned last silently discarded the other's mutations
    # (an applied tweak vanished from memory AND its trailing persist).
    job = _clip_jobs.setdefault(job_id, state)
    # Restart-fragility audit: an in-flight Remotion Lambda keeps running after the
    # instance dies — re-attach its poller instead of letting the watchdog fail the job
    # (double-spend + "server restarted"). The persisted render_id/bucket_name (now
    # written mid-render) make this possible; _poll_remotion_render is idempotent.
    if job is state:                    # we won the restore (first to materialize it)
        _reattach_in_flight_renders(job)
    return job


def _reattach_in_flight_renders(job: dict) -> None:
    """For every clip restored mid-render, re-spawn the (idempotent) Lambda poller so a
    render that's genuinely still finishing completes instead of being watchdog-killed
    and re-rendered. Fire-and-forget; no-op for clips without a durable render_id."""
    for clip in job.get("clips", []):
        if clip.get("status") == "rendering" and clip.get("render_id") and clip.get("bucket_name"):
            clip["render_started_at"] = time.time()      # fresh watchdog lease for the re-attached poll
            my_gen = _bump_render_gen(clip)
            try:
                _spawn(_reattach_one_render(job, clip, my_gen))
            except RuntimeError:
                pass                                     # no loop (sync restore in a test)


async def _reattach_one_render(job: dict, clip: dict, my_gen: int) -> None:
    """Await the still-running Lambda for a restored clip and apply its result, mirroring
    _render_all_clips' completion path (respecting the render-generation guard so a
    concurrent retry still wins)."""
    try:
        render_url = await _poll_remotion_render(
            clip["render_id"], clip["bucket_name"],
            total_frames=clip.get("render_total_frames"))
        if _is_current_render(clip, my_gen):
            clip["render_url"] = render_url
            clip["status"] = "ready"
            # Finalize the JOB once no clip is still rendering (mirrors _run_edit's
            # terminal write) — a restored render's completion must flip the job out
            # of the non-terminal `rendering` status or the watchdog eventually trips it.
            if not any(c.get("status") == "rendering" for c in job.get("clips", [])):
                job["status"] = "ready" if any(c.get("status") == "ready"
                                               for c in job["clips"]) else "failed"
    except PipelineError as e:
        if _is_current_render(clip, my_gen):
            _fail_clip(clip, e.code, e.detail)
    except Exception as e:
        if _is_current_render(clip, my_gen):
            _fail_clip(clip, "internal_error", str(e))
    if job.get("job_id"):
        _spawn(_persist_clip_job(job["job_id"]))


async def _mint_supabase_upload(filename: str) -> dict | None:
    """Mint a Supabase Storage signed-upload URL + its public read URL.

    Flow (verified against the live Storage API): POST .../object/upload/sign/
    {bucket}/{key} with the service key returns a relative signed path; the client
    PUTs the bytes to {SUPABASE_URL}/storage/v1{that path} with the file's
    Content-Type. The bucket is public, so {SUPABASE_URL}/storage/v1/object/public/
    {bucket}/{key} is the durable, no-expiry read URL AssemblyAI + Remotion fetch.

    Returns None (caller falls through to the next storage backend / mock) if
    Supabase isn't configured or the sign call fails — never raises into the request."""
    if not (SUPABASE_URL and SUPABASE_KEY):
        return None
    key = f"uploads/{uuid.uuid4()}/{filename}"
    base = SUPABASE_URL.rstrip("/")
    sign_url = f"{base}/storage/v1/object/upload/sign/{SUPABASE_STORAGE_BUCKET}/{key}"
    headers = {"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(sign_url, headers=headers)
        if r.status_code != 200:
            logging.warning("supabase upload-sign failed: %s %s", r.status_code, r.text[:200])
            return None
        signed_path = (r.json() or {}).get("url") or ""
    except Exception as e:
        logging.warning("supabase upload-sign error: %s", e)
        return None
    if not signed_path:
        return None
    return {
        "mode": "live",
        # signed_path is relative to /storage/v1 (e.g. /object/upload/sign/...?token=)
        "upload_url": f"{base}/storage/v1{signed_path}",
        "key": key,
        "public_url": f"{base}/storage/v1/object/public/{SUPABASE_STORAGE_BUCKET}/{key}",
        "max_upload_bytes": MAX_UPLOAD_BYTES,
    }


@app.post("/v1/uploads/mint")
async def mint_upload_url(req: UploadMintRequest):
    # Primary: Supabase Storage (R2 was never provisioned — see STORAGE_CONFIGURED).
    supa = await _mint_supabase_upload(req.filename)
    if supa:
        return supa
    if not R2_ACCESS_KEY:
        # No storage backend configured: return a clearly-mock response. `public_url`
        # is deliberately empty (not a real-looking dead domain) so a client can tell
        # this apart from a live mint and fall back to local/mock clips instead of
        # creating a live job whose source can never be fetched.
        key = f"mock/{uuid.uuid4()}/{req.filename}"
        return {"mode": "mock", "upload_url": "", "key": key, "public_url": "",
                "max_upload_bytes": MAX_UPLOAD_BYTES}
    # P-06 NOTE (removal-or-fix): this hand-rolled AWS4 presigner below is DEAD in
    # practice — R2 was never provisioned (R2_ACCESS_KEY unset everywhere; Supabase
    # Storage above is the real path) and it has never been exercised against a live
    # bucket. If R2 is ever provisioned, replace this with boto3/aws4 presigning and
    # verify content-type-signed PUTs end-to-end; until then prefer deleting it over
    # trusting it. Kept only because removing the env-gated branch changes no behavior.
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
    return {"mode": "live", "upload_url": upload_url, "key": key, "public_url": public_url,
            "max_upload_bytes": MAX_UPLOAD_BYTES}


# Server-side music catalog (same tracks the iOS Sound-mode ships) — the source for
# the music toggle's ACTUAL injection. Until this existed the toggle was a silent
# no-op: it set prefs["music"]=true and nothing ever put a track in the EDL.
# Music catalog. Each track is TAGGED (vibe/tone/bpm/energy) so selection matches the
# creator's brand voice + the edit format, not a fixed index — and `bpm` feeds the
# beat-aligned-cut pass for music recaps. The built-in set is CORS-clean (per-seam music
# handling needs it); DROP IN LICENSED TRACKS by setting the MUSIC_CATALOG env var to a
# JSON array of {name,url,vibe,tone,bpm,energy} — the whole selection/beat machinery is
# track-agnostic, so richer music is a data change, no code change.
# Founder/talking-head music bed catalog. INVARIANTS learned the hard way:
#   • mp3/m4a ONLY — AVPlayer cannot decode Ogg Vorbis, so any .ogg track played as dead
#     silence on iOS (two of the old three tracks were .ogg → effectively 1 usable bed).
#   • every URL must serve `audio/*` with HTTP range support (206) so the bed can be
#     seeked/looped by both AVPlayer and the Remotion render.
# Tags (vibe/tone/energy) drive tone-matched selection (_select_music_track); they cover
# all three brand-tone buckets (calm/confident/energetic) with ≥2 tracks each so a match
# always has variety. These are durable, royalty-free instrumental beds (SoundHelix + a
# Google-hosted track) — swap in licensed/branded tracks with ZERO code change by setting
# the MUSIC_CATALOG env (JSON list) or dropping files in the Supabase `music/` bucket.
_MUSIC_SH = "https://www.soundhelix.com/examples/mp3"
_BUILTIN_MUSIC_TRACKS = [
    {"name": "Momentum",   "url": f"{_MUSIC_SH}/SoundHelix-Song-1.mp3", "vibe": "driving",  "tone": "energetic", "bpm": 126, "energy": "high"},
    {"name": "Groundwork", "url": f"{_MUSIC_SH}/SoundHelix-Song-2.mp3", "vibe": "steady",   "tone": "confident", "bpm": 112, "energy": "medium"},
    {"name": "Still Air",  "url": f"{_MUSIC_SH}/SoundHelix-Song-3.mp3", "vibe": "chill",    "tone": "calm",      "bpm": 90,  "energy": "low"},
    {"name": "Uplift",     "url": f"{_MUSIC_SH}/SoundHelix-Song-5.mp3", "vibe": "upbeat",   "tone": "energetic", "bpm": 128, "energy": "high"},
    {"name": "Reflect",    "url": f"{_MUSIC_SH}/SoundHelix-Song-6.mp3", "vibe": "chill",    "tone": "calm",      "bpm": 84,  "energy": "low"},
    {"name": "Assured",    "url": f"{_MUSIC_SH}/SoundHelix-Song-8.mp3", "vibe": "steady",   "tone": "confident", "bpm": 116, "energy": "medium"},
    {"name": "Throughline","url": f"{_MUSIC_SH}/SoundHelix-Song-9.mp3", "vibe": "driving",  "tone": "confident", "bpm": 120, "energy": "medium"},
    {"name": "Neverwritten","url": "https://commondatastorage.googleapis.com/codeskulptor-demos/DDR_assets/Kangaroo_MusiQue_-_The_Neverwritten_Role_Playing_Game.mp3",
     "vibe": "upbeat", "tone": "energetic", "bpm": 128, "energy": "high"},
]


def _load_music_catalog() -> list[dict]:
    raw = os.environ.get("MUSIC_CATALOG", "").strip()
    if raw:
        try:
            cat = json.loads(raw)
            if isinstance(cat, list) and all(isinstance(t, dict) and t.get("url") for t in cat):
                return cat
        except (ValueError, TypeError):
            logging.warning("[music] MUSIC_CATALOG env is not valid JSON — using built-in tracks")
    return list(_BUILTIN_MUSIC_TRACKS)


MUSIC_TRACKS = _load_music_catalog()

# Brand-voice tone → preferred track tone, so a calm founder gets a restrained bed and a
# high-energy creator gets a driving one. Falls back to the vibe map, then any track.
_TONE_TO_TRACK_TONE = {
    "calm": "calm", "restrained": "calm", "thoughtful": "calm", "serious": "calm",
    "energetic": "energetic", "hype": "energetic", "playful": "energetic", "bold": "energetic",
    "confident": "confident", "authoritative": "confident", "professional": "confident",
}


def _select_music_track(vibe: str = "", tone: str = "", montage: bool = False,
                        seed: int = 0) -> dict:
    """Pick the best-matching track: brand tone first, then the plan's vibe, then a
    montage gets the highest-energy track, else a deterministic seed pick. Never raises."""
    cat = MUSIC_TRACKS or _BUILTIN_MUSIC_TRACKS
    want_tone = _TONE_TO_TRACK_TONE.get((tone or "").strip().lower())
    if want_tone:
        matches = [t for t in cat if (t.get("tone") or "").lower() == want_tone]
        if matches:
            return matches[seed % len(matches)]
    if vibe:
        matches = [t for t in cat if (t.get("vibe") or "").lower() == vibe.strip().lower()]
        if matches:
            return matches[seed % len(matches)]
    if montage:
        hi = [t for t in cat if (t.get("energy") or "").lower() == "high"]
        if hi:
            return hi[seed % len(hi)]
    return cat[seed % len(cat)]

# P4: kind -> hosted one-shot SFX URL, read by retention.synthesize_sfx (passed in
# as sfx_assets=SFX_ASSETS, never imported the other direction — main.py already
# imports retention_mod). Defaults point at Marque's own public Supabase bucket
# (same bucket/pattern as _rehost_media): whoosh + pop are Mixkit "Free License"
# one-shots (royalty-free, no attribution, commercial use OK - mixkit.co/license),
# hit is Kenney's "Impact Sounds" pack (CC0 / public domain - kenney.nl/assets/
# impact-sounds), re-encoded to mp3 and uploaded to sfx/<kind>.mp3. Env vars still
# override per-deploy if the assets ever need swapping. synthesize_sfx and
# build_render_plan both already skip any cue whose kind resolves to a falsy URL
# (same "clean backdrop beats fake copy" fail-soft philosophy as unresolved
# b-roll/GreenScreen's text_card), so a missing/blank override stays a safe no-op.
_SFX_DEFAULT_BASE = "https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/sfx"
SFX_ASSETS: dict[str, str | None] = {
    "whoosh": os.environ.get("SFX_URL_WHOOSH", f"{_SFX_DEFAULT_BASE}/whoosh.mp3"),
    "pop": os.environ.get("SFX_URL_POP", f"{_SFX_DEFAULT_BASE}/pop.mp3"),
    "hit": os.environ.get("SFX_URL_HIT", f"{_SFX_DEFAULT_BASE}/hit.mp3"),
}


def _apply_edit_prefs(edl: dict, prefs: dict, emphasis_spans: list | None = None) -> dict:
    """Post-process an EDL per the creator's editing preferences."""
    if not edl or not prefs:
        return edl
    if prefs.get("auto_captions") is False:
        edl["captions"] = []
    # P0.9: the b-roll / punch-in toggles were WRITTEN into prefs but never read here, so
    # turning them off did nothing. Now they actually strip (off) or synthesize (on).
    if prefs.get("broll") is False:
        edl["broll"] = []
    if prefs.get("punch_ins") is False:
        edl["overlays"] = [o for o in (edl.get("overlays") or []) if o.get("type") != "punch_in"]
    elif prefs.get("punch_ins") is True:
        overlays = edl.get("overlays") or []
        has_punch = any(o.get("type") == "punch_in" for o in overlays)
        # punch_ins ON but the edit produced none → synthesize one on the top emphasis span
        # (only for styles that actually render punch-ins).
        if not has_punch and emphasis_spans and style_capabilities(edl.get("style", "")).get("punch_ins"):
            s_in, s_out = emphasis_spans[0][0], emphasis_spans[0][1]
            overlays.append({"type": "punch_in", "src_in": s_in,
                             "src_out": min(s_out, s_in + 60), "scale": 1.08, "text": ""})
            edl["overlays"] = overlays
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
    if prefs.get("music"):
        audio = edl.get("audio")
        if not isinstance(audio, dict):
            audio = {"lufs_target": -14.0}
            edl["audio"] = audio
        if not audio.get("music"):
            montage = edl.get("style") == "fast_cuts"
            track = _select_music_track(tone=(prefs.get("brand_tone") or ""),
                                        montage=montage, seed=len(edl.get("segments") or []))
            # A montage recap is music-forward (louder, no duck); a talking cut sits
            # the track quietly under the voice.
            audio["music"] = {"url": track["url"], "query": None, "bpm": track.get("bpm"),
                              "volume": 0.3 if montage else 0.12,
                              "duck_voice": not montage}
    return edl


@app.post("/v1/clips")
async def create_clip_job(req: ClipJobRequest):
    """Create a clip editing job (analyze-first). Returns immediately; analysis runs async."""
    # Cutover: the old single-shot flow (pick a format up front → immediate render) is
    # gone. A request without analyze_first is a stale client — fail loud and clear so it
    # shows an update prompt instead of a silent 500 (user decision: cut over immediately).
    if not req.analyze_first:
        raise HTTPException(status_code=426, detail={
            "error": "update_required",
            "message": "Please update Yunicorn — the editor now analyzes your video first."})
    job_id = str(uuid.uuid4())
    # UX-B1a: one-tap submits render ONCE (same as confirm's no-fan-out rule), and the
    # clip id must be minted NOW — the create response returns it for tracking, so the
    # pipeline must never swap it out mid-flight.
    if req.auto_confirm:
        clips = [{"clip_id": str(uuid.uuid4()),
                  "format": (req.formats or ["myth-buster"])[0], "status": "queued"}]
    else:
        clips = [{"clip_id": str(uuid.uuid4()), "format": f, "status": "queued"}
                 for f in (req.formats or ["myth-buster"])]
    # An explicit edit format pins the engine style from the start — inference
    # (brief/confirm) must never override the creator's choice.
    edit_format = req.edit_format if req.edit_format in prompts.EDIT_FORMATS else ""
    style = prompts.EDIT_FORMATS[edit_format]["style"] if edit_format else req.style
    # Composition-style picker override: green_screen/split_screen aren't reachable via
    # edit_format (that enum is talking-head-only), so the picker sends them through
    # config.composition_style instead. This wins over the edit_format-derived style —
    # the creator explicitly picked a composition treatment.
    _comp_style = (req.config or {}).get("composition_style", "")
    if _comp_style == "green_screen":
        style = "green_screen"
    elif _comp_style == "split_screen":
        style = "duet_split"
    # A7: explicit pick > the edit format's default_theme > clean_creator (the
    # golden-diff no-op). Resolution happens regardless of EDIT_THEMES so
    # job["theme_id"] is always an honest record; only `_theme` (what
    # apply_retention_passes actually reads) is flag-gated to None.
    theme_id = req.theme_id or prompts.EDIT_FORMATS.get(edit_format, {}).get("default_theme", "")
    job = {
        "job_id": job_id, "source_id": req.source_id, "status": "transcribing",
        "clips": clips, "script": req.script, "style": style,
        "brand": req.brand, "media_context": req.media_context,
        "source_url": req.source_url, "edl": None, "error": None,
        "edit_prefs": req.edit_prefs or {},
        "broll_corpus": req.corpus or [],   # WS4: creator's own media for auto b-roll
        "react_source_url": req.react_source_url,
        "react_credit_label": req.react_credit_label,
        "edit_format": edit_format,
        "theme_id": theme_id,
        "config": req.config or {},
        "_theme": themes_mod.get_theme(theme_id) if EDIT_THEMES else None,
        "reference_reel": _clean_reference_reel(req.reference_reel),
        # Conversational-tweak state: transcript kept for re-editing, prior EDLs
        # for undo, and the tweak chat history.
        "words": [], "edl_history": [], "tweaks": [],
        "created_at": time.time(),
        # Remembered for retry: a failed one-tap job must restart through
        # _run_auto_pipeline (toggle ladder + auto-applied confirm), not the
        # legacy pipeline — retrying via _run_pipeline silently dropped the
        # creator's toggles/brief when the failure predated confirm-application.
        "auto_confirm": bool(req.auto_confirm),
    }
    job["custom_instructions"] = req.custom_instructions
    job["creator_id"] = req.creator_id                       # UX-B1a: keys push + learning
    if req.toggles:
        job["toggles"] = req.toggles                          # explicit beats every default
    # Split screen with no reacted-to clip silently degrades to plain talking-head
    # (DuetSplit's second panel has nothing to show) — surface that rather than let
    # the creator wonder why their pick did nothing.
    if style == "duet_split" and not req.react_source_url:
        for c in job["clips"]:
            c.setdefault("warnings", []).append(
                "react_source_missing: split screen needs a source clip to react to")
    _clip_jobs[job_id] = job

    # UX-B1a one-tap submit: no brief_ready stop — the whole pipeline runs and the
    # response includes the clips array so the client tracks + polls immediately.
    if req.auto_confirm:
        if not ASSEMBLY_KEY:
            # Keyless: immediate mock_ready with real clips, mirroring confirm's mock
            # branch so the client's tracking path is identical to live.
            job["words"] = _mock_words(req.script or {})
            total_f = ms_to_frame(max((w["end_ms"] for w in job["words"]), default=0))
            brief = _mock_edit_brief(job["words"], " ".join(w["word"] for w in job["words"]),
                                     req.custom_instructions, edit_format=edit_format)
            job["edit_brief"] = _resolve_strategy(brief, total_f)
            prefs = _apply_confirm_to_job(job, toggles=req.toggles or None, reset_clips=False)
            job["status"] = "mock_ready"
            job["clips"][0]["status"] = "ready"
            job["edl"] = _apply_edit_prefs(_mock_edl(job["style"], job.get("script") or {}), prefs)
            job["knowledge_version"] = knowledge_mod.knowledge_version()
            return {"mode": "mock", "job_id": job_id, "status": "mock_ready", "clips": job["clips"]}
        _mark_stage(job, "processing")
        _spawn(_run_auto_pipeline(job_id))
        _spawn(_persist_clip_job(job_id))
        return {"mode": "live", "job_id": job_id, "status": "processing", "clips": job["clips"],
                "eta_seconds": _job_eta_seconds(job)}

    # Analyze-first (Loop F): analyze the raw take → edit brief, STOP at brief_ready.
    # The creator reviews the brief + toggles, then calls /confirm to edit + render.
    if req.analyze_first:
        if not ASSEMBLY_KEY:
            job["words"] = _mock_words(req.script or {})
            total_f = ms_to_frame(max((w["end_ms"] for w in job["words"]), default=0))
            brief = _mock_edit_brief(job["words"], " ".join(w["word"] for w in job["words"]),
                                     req.custom_instructions, edit_format=edit_format)
            job["edit_brief"] = _resolve_strategy(brief, total_f)
            job["status"] = "brief_ready"
            return {"mode": "mock", "job_id": job_id, "status": "brief_ready",
                    "edit_brief": job["edit_brief"],
                    "toggles": _default_toggles(job["edit_brief"], edit_format)}
    job["status"] = "analyzing"
    _spawn(_run_analysis(job_id))
    _spawn(_persist_clip_job(job_id))
    return {"mode": "live", "job_id": job_id, "status": "analyzing"}


@app.post("/v1/devices")
async def register_device(req: DeviceRegisterRequest):
    """UX-B2a: APNs device registration — idempotent (token, environment) upsert;
    re-registering re-enables a soft-disabled token. Works keyless (in-memory) so the
    app's registration path never errors in dev."""
    if not req.token.strip():
        raise HTTPException(status_code=422, detail="token required")
    row = await push_mod.upsert_device(
        creator_id=req.creator_id, token=req.token.strip(), environment=req.environment,
        platform=req.platform, app_version=req.app_version,
        timezone=req.timezone, permission=req.permission)
    return {"ok": True, "environment": row["environment"],
            "push_configured": push_mod.PUSH_CONFIGURED}


@app.get("/v1/clips/{job_id}")
async def get_clip_job(job_id: str, include_words: int = 0):
    _sweep_ttl_jobs(_clip_jobs)
    _sweep_stuck_renders(_clip_jobs)
    if job_id not in _clip_jobs and not await _restore_clip_job(job_id):
        # Keyless dev/sim affordance: a `demo-`/`sim-` prefixed id synthesizes a
        # 3-segment mock job so the manual editor is drivable without the record
        # flow. Real unknown ids still 404 (test_get_clip_job_not_found).
        if not ANTHROPIC_KEY and job_id.startswith(("demo-", "sim-")):
            _synthesize_demo_clip_job(job_id)
        else:
            _raise_job_not_found(job_id)
    job = _clip_jobs[job_id]
    out = {
        "mode": "mock" if job["status"] == "mock_ready" else "live",
        "job_id": job_id,
        "status": job["status"],
        "clips": job["clips"],
        "edl": job.get("edl"),
        "error": job.get("error"),
        "error_detail": job.get("error_detail"),
        "undo_available": bool(job.get("edl_history")),
        # H7: the original source video URL — the manual editor's rough-cut
        # local preview plays THIS (seeking through kept intervals) rather
        # than the rendered output, so it needs it and previously had no way
        # to get it from this endpoint at all.
        "source_url": job.get("source_url"),
        # UX: honest remaining-time estimate for the poller ("Ready in ~2 min");
        # None once the job is terminal.
        "eta_seconds": _job_eta_seconds(job),
    }
    if job.get("edit_brief"):                     # analyze-first: surface the brief + toggles
        out["edit_brief"] = job["edit_brief"]
        out["toggles"] = job.get("toggles") or _default_toggles(job["edit_brief"], job.get("edit_format", ""))
    if job.get("edit_format"):
        out["edit_format"] = job["edit_format"]
    # #8 AI report card: surface whatever the pipeline already computed — the
    # active theme, the self-review vision score/issues, and the deterministic
    # lint scoreboard. All three are None/absent-safe (a job that never ran
    # self-review, e.g. keyless, simply omits that key rather than faking one).
    if job.get("theme_id"):
        out["theme_id"] = job["theme_id"]
    if job.get("self_review"):
        out["self_review"] = job["self_review"]
    if job.get("lint"):
        out["lint"] = job["lint"]
    if include_words:
        # Opt-in only — real transcripts are thousands of words and this endpoint
        # is polled every 5s; the manual editor is the only caller that needs them.
        out["words"] = job.get("words") or []
    return out


def _apply_confirm_to_job(job: dict, toggles: dict | None = None,
                          custom_instructions: str = "", reset_clips: bool = True) -> dict:
    """UX-B1a: the confirm MUTATION, extracted verbatim from confirm_clip_job so the
    one-tap auto pipeline applies identical semantics. Resolves toggles (explicit →
    stored → defaults), pins style (explicit edit_format beats brief inference), fills
    script.formatId, folds toggles into edit_prefs, and (reset_clips) collapses to ONE
    render per confirmed edit — no N-format fan-out (audit D5). The auto pipeline passes
    reset_clips=False because its clip ids were minted at create time and already
    returned to the client for tracking. Returns the updated prefs dict."""
    brief = job.get("edit_brief") or {}
    edit_format = job.get("edit_format", "")
    toggles = toggles or job.get("toggles") or _default_toggles(brief, edit_format)
    job["toggles"] = toggles
    if custom_instructions:
        job["custom_instructions"] = custom_instructions
    inf = brief.get("inferred") or {}
    if edit_format in prompts.EDIT_FORMATS:
        # The creator picked this cut treatment at submit — it PINS the style; the
        # brief's inference only fills in when no explicit format was chosen.
        job["style"] = prompts.EDIT_FORMATS[edit_format]["style"]
    else:
        job["style"] = inf.get("style") if inf.get("style") in STYLES else (job.get("style") or "talking_head")
    fmt = inf.get("format_id") if inf.get("format_id") in FORMAT_IDS else "myth-buster"
    job.setdefault("script", {})
    if isinstance(job["script"], dict):
        job["script"].setdefault("formatId", fmt)
    # Visual-channel floor: a `faceless` edit hides the speaker (footage rendered at
    # opacity 0), so with no b-roll it ships as a BLACK screen + captions. Some clients
    # send broll:false for recap_voiceover (toggle drift), which zeroed the only visual
    # channel. Force b-roll on for faceless styles so a voiceover-over-visuals format can
    # never render black — regardless of what the client sent.
    if job.get("style") == "faceless" and not toggles.get("broll"):
        toggles = {**toggles, "broll": True}
        job["toggles"] = toggles
        logging.info("visual-channel floor: forced broll ON for faceless job %s", job.get("job_id", "?"))
    prefs = dict(job.get("edit_prefs") or {})
    prefs.update({"broll": bool(toggles.get("broll")), "punch_ins": bool(toggles.get("punch_ins")),
                  "music": bool(toggles.get("music"))})
    job["edit_prefs"] = prefs
    if reset_clips:
        # ONE render per confirmed edit — no N-format fan-out (audit D5).
        job["clips"] = [{"clip_id": str(uuid.uuid4()), "format": fmt, "status": "queued"}]
    return prefs


@app.post("/v1/clips/{job_id}/confirm")
async def confirm_clip_job(job_id: str, req: ConfirmRequest):
    """Analyze-first phase 2: the creator reviewed the brief + toggles → edit + render.
    Strategy/cuts come from the brief; style/format from brief.inferred; toggles →
    edit prefs. Renders ONCE (no per-format fan-out)."""
    job = _clip_jobs.get(job_id) or await _restore_clip_job(job_id)
    if job is None:
        _raise_job_not_found(job_id)
    # AF-4 (audit): same in-progress guard retry/tweak have — a double-tapped or
    # timeout-retried confirm otherwise spawned a SECOND _run_edit racing the first
    # on the same job dict (double LLM + double Lambda spend, render-gen thrash).
    if job["status"] in ("transcribing", "analyzing", "editing", "rendering") \
            or any(c.get("status") == "rendering" for c in job.get("clips") or []):
        raise HTTPException(status_code=409, detail="confirm_in_progress")
    brief = job.get("edit_brief") or {}
    if not brief:
        raise HTTPException(status_code=409, detail="no edit brief — analyze the video first")

    prefs = _apply_confirm_to_job(job, toggles=req.toggles or None,
                                  custom_instructions=req.custom_instructions)

    if not ASSEMBLY_KEY:
        job["status"] = "mock_ready"
        job["clips"][0]["status"] = "ready"
        job["edl"] = _apply_edit_prefs(_mock_edl(job["style"], job.get("script") or {}), prefs)
        job["knowledge_version"] = knowledge_mod.knowledge_version()
        job["words"] = job.get("words") or _mock_words(job.get("script") or {})
        return {"mode": "mock", "job_id": job_id, "status": "mock_ready", "clips": job["clips"]}
    # Set the status SYNCHRONOUSLY (no await between the 409 guard above and here):
    # _run_edit only stamps "editing" when the spawned task first runs, so two
    # confirms landing in the same event-loop tick both passed the guard and spawned
    # two racing _run_edit tasks (double LLM + double Lambda spend). _run_edit's own
    # _mark_stage re-stamp a tick later is a harmless refresh of the same stage.
    _mark_stage(job, "editing")
    _bump_pipeline_gen(job)   # this confirm owns the pipeline; zombies stand down
    _spawn(_run_edit(job_id, job.get("words") or []))
    _spawn(_persist_clip_job(job_id))
    return {"mode": "live", "job_id": job_id, "status": "editing", "clips": job["clips"]}


@app.post("/v1/clips/{job_id}/retry")
async def retry_clip_job(job_id: str):
    """Recover a failed job. If an EDL exists, only the render stage re-runs (the
    transcript + edit are still good); otherwise the full pipeline restarts. The
    job dict retains everything needed — no re-upload from the app."""
    job = _clip_jobs.get(job_id) or await _restore_clip_job(job_id)
    if job is None:
        _raise_job_not_found(job_id)
    if job["status"] in ("transcribing", "analyzing", "processing", "editing", "rendering") \
            or any(c.get("status") == "rendering" for c in job["clips"]):
        # analyzing/processing included (they were missing): a retry mid-analysis
        # otherwise spawned a SECOND pipeline racing the first on the same job dict.
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

    # A retry is a FRESH pipeline run — reset the job clock so the job-level watchdog
    # (created_at * 2) measures from now, not the original creation. Without this,
    # retrying any job older than the watchdog window (RENDER_WATCHDOG_S*2) instantly
    # re-failed as "job exceeded the pipeline watchdog" before the render even ran.
    job["created_at"] = time.time()
    # ...and take ownership: any zombie task from the failed run (asyncio never
    # cancelled it) must not overwrite this fresh attempt's state when it wakes.
    _bump_pipeline_gen(job)

    if job.get("edl"):
        # _mark_stage (not a bare status set): the watchdog anchor and the ETA both
        # read stage_started_at, and the stale stamp from the FAILED run would
        # otherwise make this fresh attempt look hours old.
        _mark_stage(job, "rendering")
        _spawn(_retry_render(job_id))
    elif job.get("auto_confirm"):
        # One-tap jobs restart through the auto pipeline so the toggle ladder +
        # auto-applied confirm run again — the legacy pipeline would silently
        # drop them when the original failure predated confirm-application.
        _mark_stage(job, "processing")
        _spawn(_run_auto_pipeline(job_id))
    else:
        _mark_stage(job, "transcribing")
        _spawn(_run_pipeline(job_id))
    return {"mode": "live", "job_id": job_id, "status": job["status"], "clips": job["clips"]}


async def _retry_render(job_id: str) -> None:
    """Render-stage-only retry, with the same terminal-state guarantees."""
    job = _clip_jobs.get(job_id)
    if not job:
        return
    my_pgen = job.get("pipeline_gen", 0)
    try:
        await _render_all_clips(job_id)
        if not _owns_pipeline(job, my_pgen):
            return
        job["status"] = "ready" if any(c["status"] == "ready" for c in job["clips"]) else "failed"
        if job["status"] == "failed" and not job.get("error"):
            first = next((c for c in job["clips"] if c.get("error")), None)
            job["error"] = (first or {}).get("error", "render_no_output")
            job["error_detail"] = (first or {}).get("error_detail", "")
    except PipelineError as e:
        if _owns_pipeline(job, my_pgen):
            _fail_job(job, e.code, e.detail, e.stage)
    except Exception as e:
        if _owns_pipeline(job, my_pgen):
            _fail_job(job, "internal_error", str(e))
    finally:
        if _owns_pipeline(job, my_pgen):
            _spawn(_persist_clip_job(job_id))   # F15: durable after retry


@app.get("/v1/clips/{job_id}/suggested-edits")
async def suggested_edits(job_id: str):
    """2-4 one-tap edit chips the manual editor can apply straight through /tweak
    (mode=direct). Fully deterministic from the job's own edl + words — computed,
    never invented — and style-gated so a chip is never a silent no-op. An already-
    applied suggestion is not re-offered."""
    job = _clip_jobs.get(job_id) or await _restore_clip_job(job_id)
    if job is None:
        _raise_job_not_found(job_id)
    edl, words = job.get("edl"), job.get("words") or []
    if not edl:
        return {"chips": []}                                # analyze not confirmed yet
    chips: list[dict] = []
    existing = edl.get("drops") or []

    def _already_cut(s: int, e: int) -> bool:
        return any(d["src_in"] <= s and d["src_out"] >= e for d in existing)

    # 1) Remove-fluff: the biggest cluster of filler/dead-air (drops within ~0.5s
    #    merge into one region — that run of "um, so, basically" IS the fluff).
    _, drops = strip_fillers(words)
    clusters: list[list[int]] = []                          # [start, end, dropped_frames]
    for d in sorted(drops, key=lambda d: d.src_in):
        if d.src_out <= d.src_in:
            continue
        if clusters and d.src_in - clusters[-1][1] <= 15:
            clusters[-1][1] = max(clusters[-1][1], d.src_out)
            clusters[-1][2] += d.src_out - d.src_in
        else:
            clusters.append([d.src_in, d.src_out, d.src_out - d.src_in])
    clusters = [c for c in clusters if not _already_cut(c[0], c[1])]
    if clusters:
        s, e, _n = max(clusters, key=lambda c: c[2])
        chips.append({"kind": "remove_fluff", "label": "Remove the fluff",
                      "ops": [{"type": "cut_range", "start_frame": s, "end_frame": e}]})

    # 2) Tighten: silent lead-in / tail margins outside the spoken words.
    if words and edl.get("segments"):
        seg_in = min(s["src_in"] for s in edl["segments"])
        seg_out = max(s["src_out"] for s in edl["segments"])
        lead = ms_to_frame(words[0].get("start_ms", 0)) - seg_in
        tail = seg_out - ms_to_frame(words[-1].get("end_ms", 0))
        ops = ([{"type": "trim_start", "frames": lead}] if lead > 15 else []) + \
              ([{"type": "trim_end", "frames": tail}] if tail > 15 else [])
        if ops:
            chips.append({"kind": "tighten", "label": "Tighten the ends", "ops": ops})

    # 3) Punch-in on an emphasis region — only for styles whose comps render it (G-04),
    #    and never stacked on an existing punch-in.
    if style_capabilities(edl.get("style", ""))["punch_ins"] and words:
        emph = next((w for w in words if w.get("is_emphasized")), None)
        if emph:
            s = ms_to_frame(emph.get("start_ms", 0))
            e = ms_to_frame(emph.get("end_ms", emph.get("start_ms", 0) + 280)) + 15
        else:                                               # no ASR emphasis → the hook
            head = words[:8]
            s = ms_to_frame(head[0].get("start_ms", 0))
            e = ms_to_frame(head[-1].get("end_ms", 0))
        overlaps = any(o.get("type") == "punch_in" and o["src_in"] < e and o["src_out"] > s
                       for o in edl.get("overlays") or [])
        if e > s and not overlaps:
            chips.append({"kind": "punch_in", "label": "Punch in on the hook",
                          "ops": [{"type": "add_punch_in", "start_frame": s,
                                   "end_frame": e, "scale": 1.08}]})
    return {"chips": chips[:4]}


@app.post("/v1/clips/{job_id}/tweak")
async def tweak_clip(job_id: str, req: TweakRequest, preview: int = 0, defer_render: int = 0):
    """One conversational tweak turn: interpret the instruction into typed ops
    (LLM live / keyword grammar keyless), apply them deterministically to the
    stored EDL, and re-render just the targeted clip.

    preview=1 (G9): after applying the ops, ALSO kick off a cheap low-res
    proof render (fire-and-forget) so the manual editor can show the creator
    roughly what changed before they commit to the full-quality re-render.
    Writes to clip["preview_url"], never render_url — see _preview_rerender_clip."""
    job = _clip_jobs.get(job_id) or await _restore_clip_job(job_id)
    _is_demo = not ANTHROPIC_KEY and job_id.startswith(("demo-", "sim-"))
    if job is None:
        # In-memory job store — a backend restart orphans old jobs (F15: unless a
        # durable Supabase copy exists, tried above via _restore_clip_job).
        if _is_demo:                       # keyless sim: re-synthesize the demo job
            job = _synthesize_demo_clip_job(job_id)
        else:
            _raise_job_not_found(job_id)
    # Case-insensitive: iOS UUID.uuidString is uppercase, uuid4() is lowercase.
    want = req.clip_id.lower()
    clip = next((c for c in job["clips"] if c["clip_id"].lower() == want), None)
    if clip is None:
        # The demo clip is seeded on the iOS side with a random UUID the backend
        # never issued — for a keyless demo/sim job, adopt the requested id onto
        # the synthetic clip so the editor's Save round-trips in the sim.
        if _is_demo and job["clips"]:
            clip = job["clips"][0]
            clip["clip_id"] = req.clip_id
        else:
            raise HTTPException(status_code=404, detail="clip_not_found")
    if not req.instruction.strip() and not req.ops:
        raise HTTPException(status_code=422, detail="empty_instruction")
    # Concurrency guard: asyncio is single-threaded, so status checks + the
    # later synchronous status set (before any await) are atomic per request.
    if clip.get("status") == "rendering" \
            or job["status"] in ("transcribing", "analyzing", "processing", "editing", "rendering"):
        raise HTTPException(status_code=409, detail="render_in_progress")
    if not job.get("edl"):
        raise HTTPException(status_code=409, detail="no_edl")

    # 1) Interpretation → (reply, ops). The manual editor sends typed ops directly —
    #    no LLM in the loop, fully deterministic and keyless-testable.
    mode = "mock"
    reply, ops = "", []
    degraded = False   # F13: mode stayed "live" even when the LLM call failed and
                       # fell back to the deterministic mock reply — no signal to
                       # the client that this turn wasn't actually AI-interpreted.
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
            degraded = True
    else:
        reply, ops = _mock_tweak(req.instruction)

    # RACE RE-CHECK: the LLM interpretation above awaited, so a retry/confirm may
    # have started a whole pipeline since the top-of-request guard. Committing ops
    # (or popping undo history) into a job whose EDL a running pipeline is about to
    # regenerate would produce a mixed-state edit that neither side authored —
    # same 409 as the top guard, just re-checked on this side of the await.
    if clip.get("status") == "rendering" \
            or job["status"] in ("transcribing", "analyzing", "processing", "editing", "rendering"):
        raise HTTPException(status_code=409, detail="render_in_progress")

    applied: list[dict] = []
    skipped: list[dict] = []
    changed = False
    resolve_broll_needed = False

    # 2) undo is server-level (needs the history stack apply_edl_ops can't see)
    edit_ops = []
    for o in ops:
        if o.get("type") == "undo":
            if preview:
                # AF-6: preview turns are look-don't-commit — an undo pops committed
                # history, which is a commit. Reject rather than silently mutate.
                skipped.append({"type": "undo", "applied": False,
                                "reason": "undo can't be previewed"})
            elif job["edl_history"]:
                job["edl"] = job["edl_history"].pop()
                applied.append({"type": "undo", "applied": True, "reason": ""})
                changed = True
            else:
                skipped.append({"type": "undo", "applied": False, "reason": "nothing to undo yet"})
        else:
            edit_ops.append(o)

    # 3) Deterministic application + validation round-trip.
    #    AF-6: under preview=1 the candidate EDL is NEVER installed on the job —
    #    previously a preview committed the ops and only skipped the render, so
    #    "Preview" then "Apply" applied everything TWICE (double trims, duplicate
    #    overlays), and Cancel-after-preview silently kept the edits.
    candidate_edl: dict | None = None
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
            if preview:
                candidate_edl = new_edl                  # staged only — job untouched
            else:
                job["edl_history"].append(copy.deepcopy(job["edl"]))
                del job["edl_history"][:-25]             # cap the undo stack (F8: 10→25)
                job["edl"] = new_edl
                resolve_broll_needed = any(r["type"] == "add_broll" for r in ok)
            changed = True
        applied.extend(r for r in results if r["applied"])
        skipped.extend(r for r in results if not r["applied"])

    # 4) History entry (feeds the next turn's prompt context). Preview turns stay out —
    #    they committed nothing and must not pollute the next turn's LLM context.
    if not preview:
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
    can_render = bool(REMOTION_SERVE_URL and REMOTION_ACCESS_KEY and REMOTION_FUNCTION_NAME)
    preview_requested = False
    needs_render = changed and job["status"] == "ready" and can_render
    if preview and changed and can_render:
        # G9: preview=1 asks for a cheap proof render of the CANDIDATE edl (AF-6:
        # nothing was committed above) — never touches render_url/status/render_gen,
        # so it can't race with a real render. Fire-and-forget.
        needs_render = False
        preview_requested = True
        _spawn(_preview_rerender_clip(job_id, req.clip_id, edl_override=candidate_edl))
    elif defer_render and needs_render:
        # AF-6: commit-without-render (the manual editor's split uses this — a pure
        # structural change renders pixel-identically; the NEXT apply's render picks
        # the committed EDL up). Honest tradeoff: render_url is stale until then.
        needs_render = False
    elif needs_render:
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
            _spawn(_rerender_clip(job_id, req.clip_id, my_gen,
                                               resolve_broll=resolve_broll_needed))

    if not preview:                     # AF-6: a preview committed nothing to persist
        _spawn(_persist_clip_job(job_id))   # F15: durable after every tweak
    return {"mode": mode, "reply": reply, "applied": applied, "skipped": skipped,
            "changed": changed, "needs_render": needs_render, "clip_status": clip["status"],
            "undo_available": bool(job["edl_history"]), "degraded": degraded,
            "preview_requested": preview_requested,
            # UX-D2: on a preview turn the client needs the FULL typed ops (applied[]
            # carries only result stubs) so "Apply" can re-submit them deterministically
            # via the direct-ops path. Additive; empty on non-preview turns.
            "ops": edit_ops if preview_requested else []}


@app.post("/v1/clips/{job_id}/retheme")
async def retheme_clip(job_id: str, req: RethemeRequest):
    """A7 feature #1 ("Change theme"): force-restamp a finished clip's caption
    style/options/grade/duck to a DIFFERENT bundle and re-render. Cheap and
    safe relative to a full re-edit — it only overwrites the theme-owned
    fields (never segments/drops/overlays/broll), via apply_theme(force=True),
    then re-renders through the exact same path a manual tweak does."""
    job = _clip_jobs.get(job_id) or await _restore_clip_job(job_id)
    if job is None:
        _raise_job_not_found(job_id)
    theme = themes_mod.get_theme(req.theme_id)
    if req.theme_id and theme.id != req.theme_id:
        raise HTTPException(status_code=422, detail=f"unknown theme '{req.theme_id}'")
    targets = ([c for c in job["clips"] if c["clip_id"].lower() == req.clip_id.lower()]
              if req.clip_id else job["clips"])
    if not targets:
        raise HTTPException(status_code=404, detail="clip_not_found")
    if not job.get("edl"):
        raise HTTPException(status_code=409, detail="no_edl")
    if any(c.get("status") == "rendering" for c in targets) or \
            job["status"] in ("transcribing", "analyzing", "processing", "editing", "rendering"):
        raise HTTPException(status_code=409, detail="render_in_progress")

    job["edl_history"].append(copy.deepcopy(job["edl"]))
    del job["edl_history"][:-25]
    job["edl"] = themes_mod.apply_theme(job["edl"], theme, force=True)
    job["theme_id"] = theme.id

    can_render = bool(REMOTION_SERVE_URL and REMOTION_ACCESS_KEY and REMOTION_FUNCTION_NAME)
    rendering: list[str] = []
    if job["status"] == "ready" and can_render:
        for clip in targets:
            if clip.get("status") == "rendering":
                continue
            clip["status"] = "rendering"
            clip["render_started_at"] = time.time()
            my_gen = _bump_render_gen(clip)
            _spawn(_rerender_clip(job_id, clip["clip_id"], my_gen))
            rendering.append(clip["clip_id"])
    _spawn(_persist_clip_job(job_id))
    return {"theme_id": theme.id, "rethemed_clips": [c["clip_id"] for c in targets],
            "rendering": rendering, "undo_available": bool(job["edl_history"])}


def _mark_tweak_render_failed(job: dict, clip: dict, err: str) -> None:
    """Surface a tweak re-render failure on the CLIP (visible via GET), not just buried in
    job['tweaks'][-1] — otherwise the app sees the old URL come back and thinks the edit
    silently did nothing (audit D6)."""
    clip["last_render_failed"] = True
    clip["last_render_error"] = err[:200]
    if job.get("tweaks"):
        job["tweaks"][-1]["render_error"] = err[:200]


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
                job["edl"] = await _resolve_broll(job["edl"], allow_generation=False)
            except Exception:
                clip.setdefault("warnings", []).append("broll_unresolved: resolve failed")
            # #16: a resolve that finds no stock clip (no key / no match) leaves the cue
            # unresolved — build_render_plan then silently drops it and the re-render looks
            # PIXEL-IDENTICAL, yet the tweak already replied "added b-roll". Surface the
            # gap as a warning (mirrors the pipeline path) instead of a silent no-op.
            # Refresh, not append, so a later successful resolve clears the stale warning.
            clip["warnings"] = [w for w in clip.get("warnings", [])
                                if not str(w).startswith("broll_unresolved")]
            for b in (job["edl"].get("broll") or []):
                if b.get("source") != "own_media" and not b.get("resolved_url"):
                    clip.setdefault("warnings", []).append(
                        f"broll_unresolved: {b.get('broll_query') or b.get('cue_text', '')}"[:120])
            # The resolve above ran INSIDE the rendering-watchdog window — restart the
            # clock so slow (but succeeding) resolution can't get the render falsely
            # failed as stalled and its good result discarded.
            clip["render_started_at"] = time.time()
        async with _render_semaphore:   # G7: bound cross-job Lambda concurrency
            # Superseded while queued? Bail before spending a Lambda render whose
            # result every write site (incl. our finally) would discard anyway.
            if not _is_current_render(clip, my_gen):
                return
            # Same queue-time exemption as _render_all_clips: re-stamp at acquisition
            # so semaphore wait never counts against the render watchdog.
            clip["render_started_at"] = time.time()
            submission = await _submit_remotion_render(
                job["source_url"], job["edl"], clip["format"], job["style"])
            if not submission:
                raise PipelineError("render_submit_failed", "no renderId from bridge", "render")
            clip["render_id"] = submission["render_id"]
            clip["bucket_name"] = submission["bucket_name"]
            clip["render_total_frames"] = submission.get("total_frames")
            if job.get("job_id"):
                _spawn(_persist_clip_job(job["job_id"]))   # durable render_id -> restart re-attach
            render_url = await _poll_remotion_render(
                submission["render_id"], submission["bucket_name"],
                total_frames=submission.get("total_frames"))
        if _is_current_render(clip, my_gen):
            clip["render_url"] = render_url
            clip.pop("error", None)
            clip.pop("error_detail", None)
            clip.pop("last_render_error", None)          # G-05: a good render clears the flag
            clip["last_render_failed"] = False
            # Refresh the poster for the new cut (the old thumbnail is now stale).
            _spawn(_attach_poster(job.get("job_id", ""), clip, render_url, my_gen))
            # A tweak (e.g. a fresh cut) can newly straddle an existing duet react
            # window, which build_render_plan then silently drops — this only
            # surfaces here (not in _run_pipeline's one-time check) since it's a
            # NEW drop introduced by this tweak's edit, not the original EDL.
            if submission.get("plan_warnings"):
                clip.setdefault("warnings", []).extend(submission["plan_warnings"])
    except PipelineError as e:
        if _is_current_render(clip, my_gen):
            clip["render_url"] = prev_url
            _mark_tweak_render_failed(job, clip, f"{e.code}: {e.detail}")   # G-05: visible on the clip
            if not prev_url:
                _fail_clip(clip, e.code, e.detail)
    except Exception as e:
        if _is_current_render(clip, my_gen):
            clip["render_url"] = prev_url
            _mark_tweak_render_failed(job, clip, str(e))
            if not prev_url:
                _fail_clip(clip, "internal_error", str(e))
    finally:
        if _is_current_render(clip, my_gen):
            if clip.get("status") != "failed":
                clip["status"] = "ready" if clip.get("render_url") else "failed"
            _spawn(_persist_clip_job(job_id))   # F15: durable after re-render


async def _preview_rerender_clip(job_id: str, clip_id: str,
                                 edl_override: dict | None = None) -> None:
    """G9: a cheap, non-committing low-res proof render triggered by the manual
    editor's "HD preview" button. Deliberately separate from _rerender_clip:
    writes ONLY clip["preview_status"]/["preview_url"], never touches
    render_url/status/render_gen — a preview must never race with or overwrite
    the clip's real, committed render. Still goes through _render_semaphore
    (G7) since it's a genuine Lambda invocation and competes for the same cap.

    AF-6: edl_override is the CANDIDATE edl from a preview=1 tweak — previews
    render the staged (uncommitted) state, since preview tweaks no longer
    install their ops on the job."""
    job = _clip_jobs.get(job_id)
    if not job:
        return
    clip = next((c for c in job["clips"] if c["clip_id"].lower() == clip_id.lower()), None)
    if not clip:
        return
    clip["preview_status"] = "rendering"
    clip["preview_started_at"] = time.time()          # G-09: watchdog can now fail a stranded preview
    my_gen = clip["preview_gen"] = clip.get("preview_gen", 0) + 1   # guard: newest preview wins
    try:
        async with _render_semaphore:
            if clip.get("preview_gen") == my_gen:   # queue time ≠ render time (see _render_all_clips)
                clip["preview_started_at"] = time.time()
            submission = await _submit_remotion_render(
                job["source_url"], edl_override or job["edl"], clip["format"], job["style"],
                preview=True)
            if not submission:
                raise PipelineError("render_submit_failed", "no renderId from bridge", "render")
            preview_url = await _poll_remotion_render(
                submission["render_id"], submission["bucket_name"],
                total_frames=submission.get("total_frames"))
        if clip.get("preview_gen") == my_gen:         # a newer preview / watchdog didn't supersede us
            clip["preview_url"] = preview_url
            clip["preview_status"] = "ready"
    except PipelineError as e:
        if clip.get("preview_gen") == my_gen:
            clip["preview_status"] = "failed"
            clip["preview_error"] = f"{e.code}: {e.detail}"[:200]
    except Exception as e:
        if clip.get("preview_gen") == my_gen:
            clip["preview_status"] = "failed"
            clip["preview_error"] = str(e)[:200]


def _mock_edl(style: str, script: dict) -> dict:
    """Deterministic mock EDL for dev/test."""
    return {
        "style": style, "format_id": script.get("formatId", "myth-buster"),
        "segments": [{"src_in": 0, "src_out": 720}],
        "drops": [{"src_in": 45, "src_out": 51, "reason": "filler"}],
        "captions": [{"word": w, "frame": i*20, "end_frame": i*20 + 18}
                     for i, w in enumerate(script.get("hook", "Great hook").split()[:8])],
        # Punch-ins only for styles whose composition draws them (mirrors _PUNCH_STYLES) —
        # a recap/faceless mock shouldn't carry a zoom the caps say isn't supported.
        "overlays": ([{"type": "punch_in", "src_in": 90, "src_out": 150, "scale": 1.08, "text": ""}]
                     if style in ("talking_head", "duet_split") else []),
        "broll": [], "layout": {"style": style, "panels": 1 if style != "split_three" else 3,
                                "panel_boundaries": [240, 480] if style == "split_three" else []},
        "audio": {"lufs_target": -14.0},
    }


def _demo_editor_edl(style: str, script: dict) -> dict:
    """A 3-segment mock EDL used ONLY to make the manual editor sim-drivable
    (reorder needs ≥2 segments). Kept separate from _mock_edl so the create/
    confirm flow's single-segment contract — which many tests assert on — is
    untouched. Three ~8s segments across a 720-frame (24s @30fps) source."""
    hook = (script.get("hook") or "Great hook here").split()[:9]
    edl = {
        "style": style, "format_id": script.get("formatId", "myth-buster"),
        "segments": [{"src_in": 0, "src_out": 240},
                     {"src_in": 240, "src_out": 480},
                     {"src_in": 480, "src_out": 720}],
        "drops": [{"src_in": 300, "src_out": 312, "reason": "filler"}],
        "captions": [{"word": w, "frame": i * 22, "end_frame": i * 22 + 20} for i, w in enumerate(hook)],
        "overlays": [{"type": "punch_in", "src_in": 90, "src_out": 150, "scale": 1.08, "text": ""}],
        "broll": [], "layout": {"style": style, "panels": 1, "panel_boundaries": []},
        "audio": {"lufs_target": -14.0},
        # LOOP U: a boundary transition so the editor's transitionSimOverlay
        # (ProEditorView.swift editorPro.transitionDip) has something to render —
        # every prior demo job omitted transitions entirely, so that preview was
        # never actually reachable from the sim-driven demo path. frames=90 (3s,
        # vs. a real edit's typical ~12) is deliberately widened: this is a
        # black-box Maestro flow driving real-time playback with no frame-exact
        # scrubbing affordance, so the ramp window needs to be wide enough for a
        # `wait for real-time playback to reach it` step to reliably land inside
        # it. This EDL is never rendered (editor-preview only, not fed through
        # build_render_plan), so it doesn't interact with that function's own
        # frames clamp (max 45) for real transitions.
        "transitions": [{"after_segment": 0, "style": "fade_black", "frames": 90}],
    }
    if style == "duet_split":
        # LOOP U: duet_split is the one style whose editor preview reads fields
        # beyond `style` itself (a react source + play schedule) — a minimal but
        # complete placeholder so the iOS side has something to parse, not just
        # an absent key it may not handle as gracefully as GreenScreen's
        # documented "no fake copy" no-overlay fallback does.
        edl["react_source"] = {"resolved_url": None, "kind": "video", "credit_label": "@original"}
        edl["react_schedule"] = [{"state": "play", "src_in": 0, "src_out": 720, "clip_from": 0, "audio_gain": 1.0}]
    return edl


# LOOP U: styles the UI-formatting audit can request via a `demo-<style>` job id
# (e.g. "demo-green_screen") — anything else, including the pre-existing plain
# "demo-clip-job" id, falls back to the original talking_head default below.
_DEMO_STYLES = {"talking_head", "green_screen", "broll_cutaway", "split_three",
               "duet_split", "faceless", "fast_cuts"}


def _synthesize_demo_clip_job(job_id: str) -> dict:
    """Keyless-only: build an in-memory mock_ready clip job for a `demo-`/`sim-`
    prefixed job id so the editor loads without the full record flow. No source
    video (placeholder player). Deterministic — safe to call repeatedly."""
    script = {"hook": "Stop overthinking your content.",
              "body": "Here is the one system that actually works. Pick one idea, "
                      "film it in a single take, and ship it. Follow for more.",
              "cta": "Follow for more", "formatId": "myth-buster"}
    suffix = job_id[len("demo-"):] if job_id.startswith("demo-") else ""
    style = suffix if suffix in _DEMO_STYLES else "talking_head"
    job = {
        "status": "mock_ready", "style": style, "script": script,
        "source_url": None,
        "clips": [{"clip_id": f"{job_id}-c0", "format": "myth-buster", "status": "ready"}],
        "edl": _apply_edit_prefs(_demo_editor_edl(style, script), {}),
        "words": _mock_words(script), "edl_history": [], "tweaks": [],
        "created_at": time.time(),
    }
    _clip_jobs[job_id] = job
    return job


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


def _clean_reference_reel(reel: dict | None) -> dict:
    """Whitelist + truncate the client's reference reel before it lands on the job
    (it flows into prompts — never store arbitrary keys or unbounded strings)."""
    if not reel or not isinstance(reel, dict):
        return {}
    keys = ("id", "creator_handle", "platform", "title", "hook_text",
            "why_trending", "format_id", "style")
    out = {k: str(reel[k])[:220] for k in keys if isinstance(reel.get(k), (str, int)) and str(reel[k]).strip()}
    # P2.3: a playable reel URL (validated http[s]) flows through so the dossier adapter can
    # measure its patterns. Kept separate from the text fields; never trusted for anything but
    # the dossier fetch.
    vu = reel.get("video_url")
    if isinstance(vu, str) and vu[:8].lower().startswith(("http://", "https:/")):
        out["video_url"] = vu[:500]
    return out if out.get("title") or out.get("creator_handle") else {}


async def _resolve_reference_patterns(job: dict) -> None:
    """P2.3: if the reference reel has a playable URL and a dossier provider is on, measure
    its patterns (cached per URL) and stash them on the reel for the prompts. Fully fail-soft
    — any error leaves the reel's text-only mimic context untouched."""
    reel = job.get("reference_reel") or {}
    url = reel.get("video_url")
    if not url or (dossier_mod.VIDEO_UNDERSTANDING or "off").lower() == "off":
        return
    try:
        d = await dossier_mod.dossier_for_reference(url, int(job.get("reference_duration_ms") or 0))
        pat = dossier_mod.reference_patterns(d, int(job.get("reference_duration_ms") or 0))
        if pat:
            reel["patterns"] = pat
            job["reference_reel"] = reel
    except Exception as e:
        logging.warning("reference patterns: %s", e)


def _mock_edit_brief(words: list[dict], transcript: str = "", custom_instructions: str = "",
                     edit_format: str = "") -> dict:
    """Deterministic keyless edit brief (Loop F). filler/dead-air cut_regions come
    ONLY from strip_fillers (never invented); the hook candidate is the opening kept
    words; everything else is a sane talking-head default. Works with no script.
    An explicit edit_format pins inferred.style + shapes pacing (recaps read high)."""
    from app.edl import strip_fillers, ms_to_frame
    kept, drops = strip_fillers(words or [])
    cut_regions = [{"start_frame": d.src_in, "end_frame": d.src_out,
                    "reason": d.reason if d.reason in prompts.CUT_REASONS else "filler",
                    "severity": "low", "quote": ""}
                   for d in drops if d.src_out > d.src_in]
    if kept:
        head = kept[:8]
        start_f = ms_to_frame(head[0].get("start_ms", 0))
        end_f = ms_to_frame(head[-1].get("end_ms", head[-1].get("start_ms", 0) + 280))
        quote = " ".join(w.get("word", "") for w in head).strip()
    else:
        start_f, end_f, quote = 0, 30, (transcript[:60].strip() or "Your opening line")
    through = (transcript.split(".")[0].strip() if transcript else "") or "The main point of this video."
    spec = prompts.EDIT_FORMATS.get(edit_format)
    style = spec["style"] if spec else "talking_head"
    fmt_id = (STYLES.get(style) or {}).get("formats", ["myth-buster"])[0]
    recap = edit_format in ("recap_music", "recap_voiceover")
    return {
        "video_type": "scripted_talking_head", "is_scripted": bool(transcript),
        "through_line": through[:160],
        "hook_candidates": [{"start_frame": start_f, "end_frame": max(end_f, start_f + 1),
                             "quote": quote[:120], "reason": "Opens on the core claim.",
                             "signal": "curiosity"}],
        "cut_regions": cut_regions,
        "pacing": ({"energy": "high", "read": "Montage pacing — hard cuts between beats."} if recap
                   else {"energy": "medium", "read": "Steady, conversational pacing."}),
        "broll_moments": [], "punch_in_moments": [],
        "strategy": "trim_only", "restructure_order": [],
        "inferred": {"style": style, "format_id": fmt_id,
                     "hook_signal": "curiosity", "pillar": ""},
    }


def _default_toggles(brief: dict, edit_format: str = "") -> dict:
    """Sensible defaults for the editable toggles (captions + filler/dead-air/flub cuts
    are always-on and not toggles). An explicit edit format seeds its own defaults
    (recap_music turns music ON, +b-roll turns b-roll ON); otherwise fall back to the
    per-video-type heuristics. The creator can still flip any of these."""
    if edit_format in prompts.EDIT_FORMATS:
        return dict(prompts.EDIT_FORMATS[edit_format]["toggles"])
    vtype = brief.get("video_type", "other")
    # B-roll is the #1 retention lever after captions (viral_editing doctrine / OpusClip
    # 13.5M-clip data), so it defaults ON for every talking-style take — the author emits
    # light cutaways on concrete visual nouns, hook/CTA stay on the face. Only pure
    # music montages opt out by default. The creator can still flip it off.
    return {
        "broll": vtype not in ("recap_music",),
        "punch_ins": vtype in ("scripted_talking_head", "freestyle_rant", "reaction", "story"),
        "music": False,
    }


async def _generate_edit_brief(words: list[dict], transcript: str = "",
                               custom_instructions: str = "", brand: dict | None = None,
                               edit_format: str = "", reference: dict | None = None,
                               dossier: dict | None = None) -> dict:
    """Live edit brief (SONNET) with the deterministic mock as the keyless path AND the
    degrade fallback. Re-grounds two things the LLM must NOT own: (1) inferred dims are
    validated against the taxonomies; (2) filler/dead-air cut_regions are re-merged from
    strip_fillers (the model only contributes flub/ramble/tangent). An explicit
    edit_format steers the analysis AND pins inferred.style afterward — the creator's
    choice beats the model's inference."""
    mock = _mock_edit_brief(words, transcript, custom_instructions, edit_format=edit_format)
    if not (ANTHROPIC_KEY and AI_QUALITY):
        return mock
    try:
        sys, usr = prompts.edit_brief_prompt(words, custom_instructions, brand or {},
                                             edit_format=edit_format, reference=reference,
                                             dossier=dossier)
        data = await anthropic_json(sys, usr, prompts.EDIT_BRIEF_SCHEMA, SONNET, 1600)
    except HTTPException:
        return mock
    if not isinstance(data, dict) or not data.get("inferred"):
        return mock
    inf = dict(data.get("inferred") or {})
    if edit_format in prompts.EDIT_FORMATS:
        inf["style"] = prompts.EDIT_FORMATS[edit_format]["style"]
    elif inf.get("style") not in STYLES:
        inf["style"] = "talking_head"
    if inf.get("format_id") not in FORMAT_IDS:
        inf["format_id"] = "myth-buster"
    if inf.get("hook_signal") not in prompts.SIGNAL_LIST:
        inf["hook_signal"] = "curiosity"
    data["inferred"] = {**mock["inferred"], **inf}
    # Deterministic filler/dead-air always wins; keep only the model's editorial cuts.
    det = [c for c in mock["cut_regions"] if c["reason"] in ("filler", "dead_air")]
    llm = [c for c in (data.get("cut_regions") or []) if c.get("reason") in ("flub", "ramble", "tangent")]
    data["cut_regions"] = det + llm
    return data


# Video types that must NEVER be reordered — their structure carries meaning (a
# listicle/tutorial is sequential; a reaction tracks the source). scripted_talking_head
# is trim-only too, but earns a limited hook-pull-forward when its hook is buried.
_TRIM_ONLY_TYPES = {"scripted_talking_head", "listicle", "tutorial", "reaction"}
_HOOK_BURIED_FRAC = 0.15


def _resolve_strategy(brief: dict, total_frames: int) -> dict:
    """Apply the deterministic type→strategy policy ON TOP of the LLM's proposal, so a
    hallucinated 'restructure' can't scramble a listicle and a genuine buried hook in a
    freestyle rant can be pulled forward. Adds a runtime pull_hook_forward flag (a
    limited move for scripted takes that isn't a full reorder)."""
    b = dict(brief)
    vtype = b.get("video_type", "other")
    order = [i for i in (b.get("restructure_order") or []) if isinstance(i, int)]
    hooks = b.get("hook_candidates") or []
    top_start = hooks[0].get("start_frame", 0) if hooks else 0
    buried = total_frames > 0 and top_start > _HOOK_BURIED_FRAC * total_frames

    if vtype in _TRIM_ONLY_TYPES:
        b["strategy"], b["restructure_order"] = "trim_only", []
        b["pull_hook_forward"] = (vtype == "scripted_talking_head") and buried
    elif b.get("strategy") == "restructure" and order and buried:
        # freestyle_rant / story with a genuinely buried hook → honor the reorder proposal
        b["strategy"], b["restructure_order"] = "restructure", order
        b["pull_hook_forward"] = False
    else:
        b["strategy"], b["restructure_order"] = "trim_only", []
        b["pull_hook_forward"] = False
    return b


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
    # Caption OPTION phrases (position / size / case / color / grouping) — caption context
    # required so "make the top clip bigger" doesn't rewrite captions. Checked BEFORE the
    # on/off toggles: "captions one word at a time" contains the substring "captions on".
    if "caption" in low:
        if any(p in low for p in ("at the top", "to the top", "up top")):
            return "Captions moved to the top.", [{"type": "set_caption_options", "position": "top"}]
        if any(p in low for p in ("in the middle", "to the middle", "center of the screen")):
            return "Captions centered.", [{"type": "set_caption_options", "position": "middle"}]
        if any(p in low for p in ("at the bottom", "to the bottom", "down low")):
            return "Captions moved back to the bottom.", [{"type": "set_caption_options", "position": "bottom"}]
        if any(p in low for p in ("bigger", "larger", "huge")):
            return "Captions bumped up a size.", [{"type": "set_caption_options", "size": "large"}]
        if any(p in low for p in ("smaller", "tinier", "less big")):
            return "Captions taken down a size.", [{"type": "set_caption_options", "size": "small"}]
        if any(p in low for p in ("uppercase", "all caps", "capital")):
            return "Captions set to ALL CAPS.", [{"type": "set_caption_options", "uppercase": True}]
        if any(p in low for p in ("one word at a time", "word by word", "single word")):
            return "Captions now show one word at a time.", [{"type": "set_caption_options", "grouping": "word"}]
        if any(p in low for p in ("in phrases", "few words", "chunks")):
            return "Captions grouped into short phrases.", [{"type": "set_caption_options", "grouping": "phrase"}]
        for name, hexv in (("yellow", "#FFD60A"), ("gold", "#FFD60A"), ("green", "#34D399"),
                           ("pink", "#F472B6"), ("blue", "#60A5FA"), ("white", "default")):
            if name in low:
                return (f"Caption highlight set to {name}.",
                        [{"type": "set_caption_options", "accent": hexv}])
    if any(p in low for p in ("captions off", "no captions", "remove captions", "remove the captions")):
        return "Captions are off for this clip.", [{"type": "set_captions_enabled", "enabled": False}]
    if any(p in low for p in ("captions on", "add captions", "turn on captions")):
        return "Captions are back on.", [{"type": "set_captions_enabled", "enabled": True}]
    # Whole-video looks — distinctive names fire alone; ambiguous ones need color context.
    if any(p in low for p in ("black and white", "grayscale", "monochrome")):
        return "Switched to a black & white look.", [{"type": "set_filter", "name": "mono"}]
    if "vivid" in low or "more punch" in low:
        return "Cranked up a vivid look.", [{"type": "set_filter", "name": "vivid"}]
    if "cinematic" in low or "film look" in low:
        return "Applied a film look.", [{"type": "set_filter", "name": "film"}]
    if "golden" in low:
        return "Applied a golden-hour look.", [{"type": "set_filter", "name": "golden"}]
    if any(p in low for p in ("no filter", "remove filter", "remove the filter", "normal colors")):
        return "Filter removed.", [{"type": "set_filter", "name": "none"}]
    if any(k in low for k in ("filter", "look", "tone")) and "warm" in low:
        return "Warmed the look up.", [{"type": "set_filter", "name": "warm"}]
    if any(k in low for k in ("filter", "look", "tone")) and any(k in low for k in ("cool", "cold")):
        return "Cooled the look down.", [{"type": "set_filter", "name": "cool"}]
    if any(p in low for p in ("add a transition", "add transitions", "fade between")):
        return "Added a fade between your first two clips.", [
            {"type": "set_transition", "after_segment": 0, "style": "fade_black"}]
    return ("I can change caption styles, size, position and color, apply filters, add "
            "transitions and text, speed clips up, cut or restore sections, add punch-ins "
            "or b-roll, and undo tweaks — tell me what to change."), []


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
    "pipeline_interrupted",     # job-level watchdog: restart/stall mid-stage (retryable)
    "render_misconfigured",     # PARTIAL Remotion env on the server (deploy/secret gap)
    "internal_error",           # catch-all
]

# Fail-fast budgets — env-tunable, monkeypatchable in tests.
SOURCE_PROBE_TIMEOUT_S = float(os.environ.get("SOURCE_PROBE_TIMEOUT_S", "5"))
TRANSCRIBE_MAX_S = int(os.environ.get("TRANSCRIBE_MAX_S", "300"))
RENDER_POLL_MAX_S = int(os.environ.get("RENDER_POLL_MAX_S", "240"))
RENDER_STALL_S = int(os.environ.get("RENDER_STALL_S", "75"))
BRIDGE_CALL_TIMEOUT_S = float(os.environ.get("BRIDGE_CALL_TIMEOUT_S", "30"))
RENDER_WATCHDOG_S = int(os.environ.get("RENDER_WATCHDOG_S", "480"))
# #17: the poll/stall budgets above are FLAT — a 3-minute take renders far longer
# than a 15-second one and would trip render_timeout/render_stalled at the flat
# 240s/75s. Scale both by the render's output frame count (Lambda parallelizes
# chunks, so wall-clock grows sublinearly — a gentle linear term is enough).
RENDER_POLL_PER_FRAME_S = float(os.environ.get("RENDER_POLL_PER_FRAME_S", "0.12"))
RENDER_POLL_CEIL_S = int(os.environ.get("RENDER_POLL_CEIL_S", "900"))
# #18: renderMediaOnLambda DISPATCHES the render as part of the submit call, so a
# killed-and-retried submit starts a SECOND, orphaned render. Give the submit a
# generous cold-start-covering budget and never auto-retry it (_submit_remotion_render).
RENDER_SUBMIT_TIMEOUT_S = float(os.environ.get("RENDER_SUBMIT_TIMEOUT_S", "90"))


def _scaled_render_budgets(total_frames: int | None) -> tuple[int, int]:
    """(poll_budget_s, stall_budget_s) for a render of `total_frames` output frames —
    the flat defaults when the count is unknown. #17: keeps a long-but-succeeding
    render from being killed as timed-out/stalled while short clips keep tight budgets."""
    if not total_frames or total_frames <= 0:
        return RENDER_POLL_MAX_S, RENDER_STALL_S
    budget = min(RENDER_POLL_CEIL_S,
                 max(RENDER_POLL_MAX_S, int(RENDER_POLL_MAX_S + total_frames * RENDER_POLL_PER_FRAME_S)))
    # Stall grows more gently — a heavy single chunk can sit at one progress value a
    # while before the next reports, but a genuinely-dead render still trips it.
    stall = min(RENDER_STALL_S * 3,
                max(RENDER_STALL_S, int(RENDER_STALL_S + total_frames * 0.03)))
    return budget, stall

# G7: clips WITHIN one job already render sequentially (the for-loop below has
# no gather/create_task fan-out) — but separate JOBS each run in their own
# asyncio task, so a burst of users submitting around the same time can still
# stack up many concurrent Lambda invocations with no cap at all. A process-
# wide semaphore bounds that across every render path (initial pipeline, retry,
# and tweak-triggered re-render all funnel through _submit_remotion_render).
RENDER_CONCURRENCY_CAP = int(os.environ.get("RENDER_CONCURRENCY_CAP", "3"))
_render_semaphore = asyncio.Semaphore(RENDER_CONCURRENCY_CAP)


def _fail_clip(clip: dict, code: str, detail: str = "") -> None:
    clip["status"] = "failed"
    clip["error"] = code
    if detail:
        clip["error_detail"] = detail[:300]


# Stage-based remaining-time estimate (seconds) for the full live pipeline:
# transcribe ~60 + brief ~45 + edit ~45 + render ~90 ≈ 240s end to end. Honest rough
# numbers (the app shows "~N min"), clamped so a slow stage never shows 0 then hangs.
_STAGE_ETA_S = {
    "processing": 240, "transcribing": 240, "analyzing": 180,
    "editing": 130, "rendering": 90,
    # brief_ready is deliberately ABSENT: the job is parked on a USER action there —
    # any countdown would be a lie (review finding: dwell time counted as progress).
}


def _mark_stage(job: dict, status: str) -> None:
    """Set the job status AND stamp when this stage began — the ETA anchors here,
    so time the user spends parked (e.g. reviewing a brief) never counts as
    pipeline progress. Also write-through to Supabase on EVERY transition (restart-
    fragility audit): the durable copy used to freeze at submit/confirm, so a
    mid-`editing`/`rendering` restart restored a stale status the watchdog then failed
    as `pipeline_interrupted` ('a brief server restart'). Fire-and-forget; no-op if
    there's no running loop (sync test calls) or no Supabase."""
    job["status"] = status
    job["stage_started_at"] = time.time()
    jid = job.get("job_id")
    if jid:
        try:
            _spawn(_persist_clip_job(jid))
        except RuntimeError:
            pass                       # no running loop (unit test) — the caller's own persist covers it


def _job_eta_seconds(job: dict) -> int | None:
    """Remaining-time estimate for a non-terminal job, or None once terminal or
    parked on user action. Stage baseline minus elapsed-IN-STAGE, floored at 20s."""
    base = _STAGE_ETA_S.get(job.get("status") or "")
    if base is None:
        return None
    anchor = float(job.get("stage_started_at") or job.get("created_at") or time.time())
    elapsed = max(0.0, time.time() - anchor)
    return max(20, int(base - min(elapsed, base - 20)))


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


def _bump_pipeline_gen(job: dict) -> int:
    """JOB-level analogue of _bump_render_gen. asyncio doesn't cancel a pipeline
    task the watchdog failed — it keeps running and, when it finally finishes,
    used to overwrite whatever a subsequent retry/confirm had built (job status,
    edl, error fields) with its stale result. Every restart site (retry, confirm,
    the job-level sweep) bumps this; pipeline tasks capture it at entry and drop
    their job-level terminal writes if a newer owner has taken over. Clip-level
    writes stay guarded by render_gen exactly as before."""
    job["pipeline_gen"] = job.get("pipeline_gen", 0) + 1
    return job["pipeline_gen"]


def _owns_pipeline(job: dict, my_gen: int) -> bool:
    return job.get("pipeline_gen", 0) == my_gen


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
    _touched: set[str] = set()          # jobs whose terminal state must be persisted (below)
    for job in jobs.values():
        for c in job.get("clips", []):
            if c.get("status") == "rendering" and now - c.get("render_started_at", now) > budget:
                # Bump the render generation so the still-running task's late write is
                # discarded (_is_current_render fails) — else it could flip the clip back
                # to ready with contradictory state (audit D8).
                _bump_render_gen(c)
                _fail_clip(c, "render_stalled", f"render exceeded {int(budget)}s watchdog")
                if job.get("job_id"): _touched.add(job["job_id"])
            if c.get("preview_status") == "rendering" \
                    and now - c.get("preview_started_at", now) > budget:
                c["preview_gen"] = c.get("preview_gen", 0) + 1   # discard the stale preview's late write
                c["preview_status"] = "failed"
                c["preview_error"] = f"preview exceeded {int(budget)}s watchdog"
        # "analyzing" and "processing" (one-tap submit) were MISSING from this set — a
        # deploy restart mid-analysis stranded the job in "analyzing" forever (observed
        # in prod 2026-07-12: job 2b0fc44c). Now every non-terminal pipeline stage is
        # watchdogged; the app's retry endpoint restarts a failed pipeline cleanly.
        #
        # Anchor at the LATEST progress marker, not created_at alone: a creator who
        # reviews a brief_ready job for >2×budget and then confirms would otherwise be
        # insta-failed on the next poll (created_at is minutes-to-hours old the moment
        # "editing" starts). _mark_stage stamps stage_started_at on every stage entry;
        # max() keeps restart recovery intact (a restored job's stale stamp still
        # trips the sweep) while parked-on-user time never counts as pipeline time.
        if job.get("status") in ("transcribing", "analyzing", "processing", "editing", "rendering"):
            anchor = max(float(job.get("created_at") or now),
                         float(job.get("stage_started_at") or 0.0))
            # While any clip is actively rendering inside ITS OWN watchdog window, the
            # per-clip sweep above owns termination — a multi-clip job legitimately
            # spends > budget*2 in "rendering" (clips render sequentially), and killing
            # it here would fail renders that are progressing fine.
            clip_actively_rendering = any(
                c.get("status") == "rendering"
                and now - c.get("render_started_at", now) <= budget
                for c in job.get("clips", []))
            if now - anchor > budget * 2 and not clip_actively_rendering:
                # Take ownership before failing: the stalled task (if it's alive at
                # all) must not overwrite this terminal state when it wakes up —
                # same discipline as the clip sweep's _bump_render_gen above.
                _bump_pipeline_gen(job)
                _fail_job(job, "pipeline_interrupted",
                          "the edit was interrupted (server restart or stall) — retry to restart it")
                if job.get("job_id"): _touched.add(job["job_id"])
    # Persist the terminal writes (restart-fragility audit): the watchdog used to fail
    # jobs in-memory only, so a SECOND restart in a crash-loop resurrected the zombie as
    # `editing` and re-cycled the spinner. Fire-and-forget; guarded for sync/no-loop callers.
    if _touched:
        try:
            for _jid in _touched:
                _spawn(_persist_clip_job(_jid))
        except RuntimeError:
            pass


async def _transcribe_job(job_id: str) -> list[dict]:
    """Transcribe the job's source into word-frames (shared by the full pipeline and the
    analyze-first flow). Sets job['words'] + stashes the transcript's auto_highlights."""
    job = _clip_jobs[job_id]
    _mark_stage(job, "transcribing")
    for c in job["clips"]:
        c["status"] = "transcribing"
    await _validate_source_url(job["source_url"])
    transcript_id = await _submit_transcription(job["source_url"])
    if not transcript_id:
        raise PipelineError("transcribe_submit_failed", "AssemblyAI rejected the submission", "transcribe")
    transcript = await _poll_transcription(transcript_id)
    job["words"] = transcript["words"]     # kept for conversational tweaks + the edit brief
    job["_auto_highlights"] = transcript.get("auto_highlights")
    return transcript["words"]


async def _dossier_job(job_id: str) -> dict | None:
    """P1.2: build the visual dossier IN PARALLEL with transcription (the user accepts the
    1–3 min Twelve Labs indexing wait; overlapping it costs no extra wall-clock). Fully
    fail-soft — any error / missing key / disabled provider leaves job['dossier']=None and
    the transcript-only edit runs unchanged. Surfaces staged progress for the poller."""
    job = _clip_jobs[job_id]
    if (dossier_mod.VIDEO_UNDERSTANDING or "off").lower() == "off":
        job["dossier_status"] = "off"
        return None
    job["dossier_status"] = "watching"     # "watching your take…" reads as intelligence, not lag
    try:
        d = await dossier_mod.generate_dossier(job.get("source_url") or "",
                                               int(job.get("duration_ms") or 0))
    except Exception as e:               # generate_dossier is already fail-soft; belt+braces
        logging.warning("dossier: unexpected error for %s: %s", job_id, e)
        d = None
    job["dossier"] = d
    job["dossier_status"] = "ready" if d else "unavailable"
    return d


async def _analyze_to_brief(job_id: str, briefless_on_error: bool = False) -> list[dict]:
    """UX-B1a: the shared middle of both analyze paths — transcribe (+ loudness +
    dossier in ONE parallel gather, exactly as before) → reference patterns → edit
    brief. Returns the transcript words. Transcription errors always propagate
    (callers fail the job as today); brief errors propagate for the analyze-first
    flow (brief_ready IS the product there) but with briefless_on_error=True the
    one-tap pipeline proceeds with edit_brief=None instead — a briefless auto edit
    still beats a dead job."""
    job = _clip_jobs[job_id]
    # P0.6: measure the take's loudness IN PARALLEL with transcription (user accepts
    # the wait; overlapping it costs no extra wall-clock). Fails soft to None → no
    # gain. transcribe raising propagates to the caller; probe never raises.
    words, lufs, dossier = await asyncio.gather(
        _transcribe_job(job_id),
        audio_mod.probe_loudness(job.get("source_url") or ""),
        _dossier_job(job_id))
    job["loudness_lufs"] = lufs
    job["dossier"] = dossier
    _mark_stage(job, "analyzing")
    for c in job["clips"]:
        c["status"] = "analyzing"
    await _resolve_reference_patterns(job)   # P2.3: measure the reference reel (cached)
    transcript_text = " ".join(w.get("word", "") for w in words)
    try:
        brief = await _generate_edit_brief(words, transcript_text,
                                           job.get("custom_instructions", ""), job.get("brand") or {},
                                           edit_format=job.get("edit_format", ""),
                                           reference=job.get("reference_reel") or None,
                                           dossier=job.get("dossier"))
        total_f = ms_to_frame(max((w.get("end_ms", 0) for w in words), default=0))
        job["edit_brief"] = _resolve_strategy(brief, total_f)
    except Exception as e:
        if not briefless_on_error:
            raise
        logging.warning("auto pipeline: brief failed (%s) — proceeding briefless", e)
        job["edit_brief"] = None
    return words


async def _run_analysis(job_id: str) -> None:
    """Analyze-first phase 1: transcribe → edit brief → stop at 'brief_ready' (no EDL
    or render yet). ALWAYS leaves the job terminal-or-brief_ready."""
    job = _clip_jobs[job_id]
    my_pgen = job.get("pipeline_gen", 0)
    try:
        await _analyze_to_brief(job_id)
        if _owns_pipeline(job, my_pgen):
            job["status"] = "brief_ready"
    except PipelineError as e:
        if _owns_pipeline(job, my_pgen):
            _fail_job(job, e.code, e.detail, e.stage)
    except Exception as e:
        if _owns_pipeline(job, my_pgen):
            _fail_job(job, "internal_error", str(e))
    finally:
        if _owns_pipeline(job, my_pgen):
            _spawn(_persist_clip_job(job_id))


async def _run_auto_pipeline(job_id: str) -> None:
    """UX-B1a one-tap submit: the analyze-first pipeline WITHOUT the brief_ready stop —
    transcribe → brief (briefless on failure) → confirm defaults auto-applied → edit →
    render. Clip ids were minted at create and already returned to the client, so the
    confirm mutation runs with reset_clips=False. Always leaves the job terminal."""
    job = _clip_jobs[job_id]
    my_pgen = job.get("pipeline_gen", 0)
    try:
        await _analyze_to_brief(job_id, briefless_on_error=True)
    except PipelineError as e:
        if _owns_pipeline(job, my_pgen):
            _fail_job(job, e.code, e.detail, e.stage)
            _spawn(_persist_clip_job(job_id))
        return
    except Exception as e:
        if _owns_pipeline(job, my_pgen):
            _fail_job(job, "internal_error", str(e))
            _spawn(_persist_clip_job(job_id))
        return
    if not _owns_pipeline(job, my_pgen):
        return                        # a retry took over while we analyzed
    # Toggles: client-explicit (stored at create) → edit-format defaults → brief
    # heuristics — the same ladder confirm uses, via the same helper.
    _apply_confirm_to_job(job, toggles=job.get("toggles"), reset_clips=False)
    _spawn(_persist_clip_job(job_id))
    await _run_edit(job_id, job.get("words") or [])


async def _run_pipeline(job_id: str):
    """Full pipeline: transcribe → edit → render. Always terminal on exit."""
    job = _clip_jobs[job_id]
    my_pgen = job.get("pipeline_gen", 0)
    try:
        # P0.6: loudness probe parallel with transcription (same as _run_analysis).
        # P1.2: + visual dossier in the same gather (all fail-soft).
        words, lufs, dossier = await asyncio.gather(
            _transcribe_job(job_id),
            audio_mod.probe_loudness(job.get("source_url") or ""),
            _dossier_job(job_id))
        job["loudness_lufs"] = lufs
        job["dossier"] = dossier
    except PipelineError as e:
        if _owns_pipeline(job, my_pgen):
            _fail_job(job, e.code, e.detail, e.stage)
            _spawn(_persist_clip_job(job_id))
        return
    except Exception as e:
        if _owns_pipeline(job, my_pgen):
            _fail_job(job, "internal_error", str(e))
            _spawn(_persist_clip_job(job_id))
        return
    if not _owns_pipeline(job, my_pgen):
        return                        # a retry took over while we transcribed
    await _run_edit(job_id, words)


EDL_AUTHOR = os.environ.get("EDL_AUTHOR", "legacy").lower()   # plan | legacy | shadow (P3.3 rollout flag)
# A1: deterministic pre-render "amateur tell" lint. "" = off. "observe" = run + log +
# store job["lint"], never mutate. "fix" = additionally apply error-severity fix_ops
# ONE round via apply_edl_ops, then re-lint. Ship "observe" first — a live scoreboard
# of what the lint would fix, with zero behavior change — before ever flipping to "fix".
EDIT_LINT = os.environ.get("EDIT_LINT", "").lower()
# A5b: true 2-pass loudness normalization on the FINAL rendered mp4. "" = off
# (today's behavior — CutVideo.tsx's per-source-gain normalization only).
AUDIO_FINALIZE = os.environ.get("AUDIO_FINALIZE", "").lower() in ("1", "true", "on")
# A7: style bundles. "" (default) = off — job["_theme"] stays None, every
# theme-aware pass's `theme=None` default keeps today's behavior unchanged.
EDIT_THEMES = os.environ.get("EDIT_THEMES", "").lower() in ("1", "true", "on")


async def _log_shadow_diff(job_id: str, job: dict, legacy_edl: dict, style: str, script: dict,
                           words: list[dict], prefs: dict, emphasis_spans: list | None) -> None:
    """P6 de-risking gate for flipping EDL_AUTHOR's default to "plan": with
    EDL_AUTHOR=shadow, the LEGACY author still ships (unchanged behavior) but
    this ALSO authors the same take via the plan path (with the SAME job
    context — brand/brief/dossier/custom_instructions/reference — so it's a
    fair comparison, not the plan author working blind) and logs a structured
    diff — real-traffic evidence gathered with zero user-facing risk. Always
    fire-and-forget (_spawn) so a slow/failing shadow run can never add latency
    or fail the real pipeline; every failure mode here is swallowed into a log
    line, never raised."""
    try:
        plan_edl, llm_contributed, _plan_data = await _author_edl_via_plan(
            job, style, script, words, prefs, emphasis_spans)
        legacy_issues = check_edl_invariants(legacy_edl, words)
        legacy_kept = _kept_frames(legacy_edl)
        legacy_drops = len(legacy_edl.get("drops") or [])
        if plan_edl is None:
            logging.info("[shadow] job=%s plan_author_failed=true legacy_issues=%d legacy_kept=%d legacy_drops=%d",
                        job_id, len(legacy_issues), legacy_kept, legacy_drops)
            return
        plan_issues = check_edl_invariants(plan_edl, words)
        plan_kept = _kept_frames(plan_edl)
        plan_drops = len(plan_edl.get("drops") or [])
        logging.info(
            "[shadow] job=%s llm_contributed=%s legacy_issues=%d plan_issues=%d "
            "legacy_kept=%d plan_kept=%d legacy_drops=%d plan_drops=%d",
            job_id, llm_contributed, len(legacy_issues), len(plan_issues),
            legacy_kept, plan_kept, legacy_drops, plan_drops)
    except Exception as e:
        logging.info("[shadow] job=%s shadow_diff_failed=%s", job_id, str(e)[:200])


async def _author_edl_via_plan(job: dict, style: str, script: dict, words: list[dict],
                               prefs: dict, emphasis_spans: list | None) -> tuple[dict | None, bool, dict]:
    """P3 authoring path: the LLM emits a typed EDIT PLAN; code assembles the EDL. The
    assembler can't emit an invalid EDL, so verification is deterministic (check_edl_
    invariants) with the LLM reserved for reorder coherence only.

    Returns (edl_dict | None, llm_contributed, plan). `llm_contributed` is False when the LLM
    was absent/down/returned nothing — the assembled EDL is then a generic whole-take cut, so
    the caller flags `ai_edit_unavailable` (F13: never silently hand back an untailored edit).
    None edl_dict means even assembly failed → caller uses the safe default. `plan` (P5) is the
    raw typed plan dict ({} when the LLM didn't contribute) — assemble_edl already consumed
    the mechanics fields (keeps/cuts/order/punch_ins/broll/caption_plan/text_cards); the
    caller extracts the RETENTION fields (pacing/interrupt_density/hook_text/end_card/music)
    from it since those are consumed downstream in the shared _run_edit tail, not here."""
    brief = job.get("edit_brief")
    format_id = script.get("formatId", "myth-buster")
    plan: dict = {}
    if ANTHROPIC_KEY and AI_QUALITY:
        try:
            _theme_for_plan = job.get("_theme")
            sys, usr = prompts.edit_plan_prompt(
                style, words, script, job["brand"], brief=brief, dossier=job.get("dossier"),
                emphasis_spans=emphasis_spans, custom_instructions=job.get("custom_instructions", ""),
                reference=job.get("reference_reel") or None,
                video_type=(brief or {}).get("video_type", ""),
                theme_label=_theme_for_plan.label if _theme_for_plan else "",
                theme_blurb=_theme_for_plan.blurb if _theme_for_plan else "",
                broll_coverage=(job.get("config") or {}).get("broll_coverage", ""),
                energy=(job.get("config") or {}).get("energy", ""))
            plan = await anthropic_json(sys, usr, prompts.EDIT_PLAN_JSON_SCHEMA, SONNET, 3000, temperature=0.0)
        except HTTPException as e:
            # NEVER swallow silently: a swallowed HTTPException here (e.g. a schema the
            # structured-outputs API rejects with a 400) degrades EVERY edit to the
            # untailored safe-default cut with no trace. Log so the "all edits look
            # generic" symptom is diagnosable from Render logs.
            logging.warning("[plan-author] authoring call failed (%s) → safe default cut",
                            getattr(e, "detail", e))
            plan = {}
    if not isinstance(plan, dict):
        plan = {}
    llm_contributed = bool(plan)   # a real plan came back → the edit is tailored
    # Missed-word protection: measure real silence so the dead-air trim only removes
    # VERIFIED-silent gaps. A gap the transcriber left because it dropped a word still
    # carries speech energy → it's not a silent span → the word's audio is spared and the
    # sentence stays intact. Cached on the job; fails soft to None (no ffmpeg/unfetchable
    # URL) → the prior timestamp-only behavior.
    if "_silent_spans" not in job:
        try:
            from app.audio import detect_silence_spans
            job["_silent_spans"] = await detect_silence_spans(job.get("source_url") or "")
        except Exception:
            job["_silent_spans"] = None
    try:
        edl_obj = assemble_edl(plan, words, style, format_id, prefs=prefs, brief=brief,
                               silent_spans=job.get("_silent_spans"))
    except Exception as e:
        logging.warning("assemble_edl failed (%s) → safe default", e)
        return None, llm_contributed, plan
    edl_data = edl_obj.model_dump()

    # Deterministic verify. The assembler produces structurally-valid EDLs by construction,
    # so only HARD structural issues (overlaps, out-of-bounds, invalid perm) justify bailing
    # to the safe default — the "kept duration <3s" advisory is legitimate for a short take
    # and bailing there would only lose the assembler's brief-cut folds for nothing.
    issues = check_edl_invariants(edl_data, words)
    hard = [i for i in issues if "kept duration" not in i]
    if hard and all(("overlay" in i or "broll" in i) for i in hard):
        # A stray overlay/b-roll window is a DECORATION bug, not an edit bug —
        # stripping the offenders keeps the tailored cut. Nuking the whole plan
        # to the untailored safe default over one bad punch-in threw away the
        # entire edit the creator was promised.
        bad_ov = {int(m.group(1)) for i in hard if (m := re.match(r"overlay (\d+)", i))}
        bad_br = {int(m.group(1)) for i in hard if (m := re.match(r"broll (\d+)", i))}
        edl_data["overlays"] = [o for k, o in enumerate(edl_data.get("overlays") or [])
                                if k not in bad_ov]
        edl_data["broll"] = [b for k, b in enumerate(edl_data.get("broll") or [])
                             if k not in bad_br]
        logging.warning("assemble_edl stripped %d incoherent overlay/broll windows", len(hard))
        issues = check_edl_invariants(edl_data, words)
        hard = [i for i in issues if "kept duration" not in i]
    if hard:
        logging.warning("assemble_edl hard invariant issues %s → safe default", hard[:4])
        return None, llm_contributed, plan
    # LLM verify reserved for reorder coherence — only when a non-identity reorder exists.
    order = edl_data.get("segment_order")
    if order is not None and order != list(range(len(edl_data.get("segments") or []))):
        edl_data = await verify_and_repair_edl(style, edl_data, words, script,
                                               emphasis_spans=emphasis_spans)
    return edl_data, llm_contributed, plan


# ---------------------------------------------------------------------------
# P5: consume the plan author's own typed retention decisions (pacing/
# interrupt_density/hook_text/end_card/music) — previously collected by the
# schema but dropped on the floor once assemble_edl finished with the plan's
# mechanics fields. Kept as standalone pure functions (not inlined in _run_edit)
# so they're independently testable, matching _extract_emphasis_regions/
# _merge_drops/_apply_edit_prefs' own pattern.
# ---------------------------------------------------------------------------

def _extract_plan_retention_hints(plan: dict) -> dict:
    """Distill the raw plan dict into the hints shape apply_retention_passes
    expects. {} (or a partial dict) for anything the LLM omitted/mistyped or
    when the plan is empty (safe-default/legacy paths) — every retention pass
    already treats a missing hint as "use the style default", so a partial
    extraction here is never unsafe, just less-tailored."""
    if not plan:
        return {}
    hints: dict = {}
    pacing = plan.get("pacing")
    if isinstance(pacing, dict) and pacing.get("lift") in ("none", "subtle", "medium"):
        hints["pacing"] = {"lift": pacing["lift"],
                           "fast_forward_silences": bool(pacing.get("fast_forward_silences"))}
    density = plan.get("interrupt_density")
    if density in ("calm", "standard", "dense"):
        hints["interrupt_density"] = density
    hook_text = (plan.get("hook_text") or "").strip()
    if hook_text:
        hints["hook_text"] = hook_text
    end_card = plan.get("end_card")
    if isinstance(end_card, dict) and end_card.get("wanted"):
        text = (end_card.get("text") or "").strip()
        if text:
            hints["end_card"] = {"wanted": True, "text": text}
    return hints


# vibe -> MUSIC_TRACKS index: a fixed, deterministic map, not a search — the
# catalog is small and was ALREADY ordered upbeat/chill/driving in anticipation
# of exactly this lookup (see MUSIC_TRACKS above).
_MUSIC_VIBE_TRACK_INDEX = {"upbeat": 0, "chill": 1, "driving": 2}


def _apply_plan_music_vibe(edl_data: dict, prefs: dict, music_hint: dict | None) -> dict:
    """P5: honor the plan author's music{wanted,vibe} decision when the creator
    hasn't explicitly toggled prefs.music either way. The creator's own toggle
    always wins over the plan's suggestion: prefs.music is False -> never;
    prefs.music is True -> always (vibe-matched if the plan supplied one, else
    the existing deterministic segment-count pick); prefs.music unset -> defer
    entirely to the plan. Never overrides music already set on the EDL (e.g. by
    a prior tweak)."""
    music_hint = music_hint or {}
    creator_pref = prefs.get("music")
    if creator_pref is False:
        return edl_data
    wants_music = True if creator_pref is True else bool(music_hint.get("wanted"))
    if not wants_music:
        return edl_data
    audio = edl_data.get("audio")
    if not isinstance(audio, dict):
        audio = {"lufs_target": -14.0}
        edl_data["audio"] = audio
    if audio.get("music"):
        return edl_data
    montage = edl_data.get("style") == "fast_cuts"
    seed = len(edl_data.get("segments") or [])
    track = _select_music_track(vibe=music_hint.get("vibe") or "",
                                tone=(prefs.get("brand_tone") or ""),
                                montage=montage, seed=seed)
    audio["music"] = {"url": track["url"], "query": None, "bpm": track.get("bpm"),
                      "volume": 0.3 if montage else 0.12, "duck_voice": not montage}
    return edl_data


async def _run_edit(job_id: str, words: list[dict]):
    """Edit + render from an already-transcribed job. Shared by the full pipeline and
    the analyze-first /confirm stage. Always leaves the job + clips terminal."""
    job = _clip_jobs[job_id]
    my_pgen = job.get("pipeline_gen", 0)
    try:
        _mark_stage(job, "editing")
        for c in job["clips"]: c["status"] = "editing"
        style = job["style"]
        script = job["script"]
        # Deterministic grounding: fillers from AssemblyAI disfluency tags (source of
        # truth for cuts) and emphasis regions for punch-in placement.
        _clean_words, filler_drops = strip_fillers(words)
        disfluency_spans = [(d.src_in, d.src_out) for d in filler_drops if d.reason == "filler"]
        emphasis_spans = _extract_emphasis_regions(words, job.get("_auto_highlights"))
        system, user = prompts.edl_prompt(style, words, script, job["brand"], job["media_context"],
                                          disfluency_spans=disfluency_spans,
                                          emphasis_spans=emphasis_spans,
                                          custom_instructions=job.get("custom_instructions", ""),
                                          brief=job.get("edit_brief"),
                                          reference=job.get("reference_reel") or None)
        prefs = job.get("edit_prefs") or {}
        # Composition-style picker: cutaway/panel/card force EVERY b-roll insert's mode
        # (assemble_edl's broll loop, app/edl.py) rather than leaving it to the LLM's
        # per-item discretion — the creator picked a specific look, so it must be honored.
        _broll_mode = (job.get("config") or {}).get("broll_mode", "")
        if _broll_mode in ("full", "panel", "card"):
            prefs = {**prefs, "broll_mode": _broll_mode}
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
            # P0.9: steer the author on the b-roll / punch-in toggles (post-processing in
            # _apply_edit_prefs enforces them regardless, but hinting avoids wasted output).
            if prefs.get("broll") is False:
                hints.append("B-roll is OFF — output an empty broll array.")
            if prefs.get("punch_ins") is False:
                hints.append("Punch-ins are OFF — do not add any punch_in overlays.")
            if hints:
                user += "\n\nCREATOR EDIT PREFERENCES:\n" + "\n".join(f"- {h}" for h in hints)
        used_safe_default = False
        plan_data: dict = {}   # P5: the plan path's own typed retention decisions (see below)
        if EDL_AUTHOR == "plan":
            # P3.3: LLM emits a typed plan; code assembles the EDL (captions, filler drops,
            # brief cuts, b-roll grammar, prefs all enforced inside assemble_edl). No legacy
            # post-processing needed — go straight to the shared render tail.
            edl_data, llm_contributed, plan_data = await _author_edl_via_plan(job, style, script, words, prefs, emphasis_spans)
            if edl_data is None:
                used_safe_default = True
                total_frames = ms_to_frame(max((w.get("end_ms", 0) for w in words), default=30000))
                edl_data = safe_default_edl(style, script.get("formatId", "myth-buster"), total_frames, words).model_dump()
            elif not llm_contributed:
                # assembled a valid edit, but the LLM never contributed a plan → the creator
                # got a generic whole-take cut, not a tailored edit (F13: surface it).
                used_safe_default = True
        else:
            try:
                # P0.9: author the EDL via structured outputs on Sonnet at temperature 0 —
                # deterministic, no free-form JSON-parse failures, a real editing model instead
                # of Haiku at temp 1.0. Falls back to the safe-default edit on LLM failure.
                # 8000 tokens (was 4000): the legacy schema has the model echo captions,
                # so a long take's EDL routinely blew the 4000 cap — truncated JSON parsed
                # as a failure and EVERY long take silently got the untailored safe
                # default. Scale with the transcript; structured outputs stop at the
                # closing brace, so the extra headroom costs nothing on short takes.
                _edl_max_tokens = 8000 if len(words) > 400 else 4000
                edl_data = await anthropic_json(system, user, prompts.EDL_JSON_SCHEMA,
                                                SONNET, _edl_max_tokens, temperature=0.0)
            except HTTPException:
                # LLM down ≠ pipeline dead: the safe default edit (full footage +
                # caption timing + deterministic filler cuts) still renders fine.
                edl_data = None

            if edl_data:
                try:
                    # The model authored source-frame numbers on trust — clamp every
                    # range to the REAL source extent before anything renders (a
                    # hallucinated src_out past the end broke the Lambda render).
                    total_frames = ms_to_frame(max((w.get("end_ms", 0) for w in words), default=30000))
                    edl_data = clamp_edl_to_source(edl_data, total_frames)
                    edl_obj = EDL(**edl_data)
                    edl_obj, issues = validate_and_repair(edl_obj)
                    edl_data = edl_obj.model_dump()
                    # Captions are derived data (G3 rationale): if the model echoed
                    # none back despite a transcript, rebuild them deterministically
                    # rather than shipping a caption-less edit.
                    if not edl_data.get("captions") and _clean_words \
                            and (prefs.get("auto_captions") is not False):
                        edl_data["captions"] = [
                            {"word": w["word"], "frame": ms_to_frame(w.get("start_ms", 0)),
                             "end_frame": ms_to_frame(w["end_ms"]) if w.get("end_ms") else None}
                            for w in _clean_words if w.get("word")]
                except Exception:
                    used_safe_default = True
                    total_frames = ms_to_frame(max((w.get("end_ms", 0) for w in words), default=30000))
                    edl_obj = safe_default_edl(style, script.get("formatId", "myth-buster"), total_frames, words)
                    edl_data = edl_obj.model_dump()
            else:
                used_safe_default = True
                total_frames = ms_to_frame(max((w.get("end_ms", 0) for w in words), default=30000))
                edl_obj = safe_default_edl(style, script.get("formatId", "myth-buster"), total_frames, words)
                edl_data = edl_obj.model_dump()

            # Analyze-first: fold the edit brief's editorial cut_regions (flub/ramble/
            # tangent — filler/dead-air are already deterministic) into the drops before
            # the merge, so the confirmed edit reflects what the analysis found.
            brief = job.get("edit_brief")
            if brief:
                for cr in brief.get("cut_regions", []):
                    if cr.get("reason") in ("flub", "ramble", "tangent") and cr.get("end_frame", 0) > cr.get("start_frame", 0):
                        reason = "false_start" if cr["reason"] == "flub" else "off_topic"
                        edl_data.setdefault("drops", []).append(
                            {"src_in": cr["start_frame"], "src_out": cr["end_frame"], "reason": reason})
            # Merge the deterministic filler drops in as source of truth (unless the
            # creator turned trimming off), then self-verify + repair the EDL once.
            if prefs.get("filler_trim") != "off":
                edl_data["drops"] = _merge_drops(edl_data.get("drops", []),
                                                 [d.model_dump() for d in filler_drops])
            edl_data = await verify_and_repair_edl(style, edl_data, words, script,
                                                   emphasis_spans=emphasis_spans)
            edl_data = _apply_edit_prefs(edl_data, prefs, emphasis_spans=emphasis_spans)

        # Ownership check after the authoring awaits: a watchdog kill + retry may
        # have started a NEWER pipeline while the LLM was thinking. Bail before
        # spending b-roll resolution (Pexels/Higgsfield credits) on a stale edit
        # and before touching clip/job state the new owner is now writing.
        if not _owns_pipeline(job, my_pgen):
            return
        # F13: safe-default degradation is surfaced, not silent (both author paths).
        if used_safe_default:
            for c in job["clips"]:
                c.setdefault("warnings", []).append(
                    "ai_edit_unavailable: used a safe default cut (full take, fillers stripped)")
        # P6 de-risking: EDL_AUTHOR=shadow ships legacy (this branch already ran it)
        # but also fires a fire-and-forget plan-author attempt + structured diff log,
        # gathering real-traffic evidence before flipping the default. Zero effect on
        # what ships; zero added latency (never awaited).
        if EDL_AUTHOR == "shadow":
            _spawn(_log_shadow_diff(job_id, job, copy.deepcopy(edl_data), style, script,
                                    words, prefs, emphasis_spans))
        # Retention-editor upgrade: deterministic post-passes applied to WHATEVER EDL
        # either author path produced — so both the plan path and the legacy
        # direct-EDL author benefit identically. Flag-gated (RETENTION_PASSES env,
        # default off = today's behavior unchanged); every pass is individually
        # fail-soft (app/retention.py _safe_pass), so this can never turn a working
        # pipeline run into a failure.
        # hints: the edit brief's `pacing.energy` is a coarse fallback available to
        # EITHER path (a low-energy/rambling brief lifts the global pace more —
        # retention.PACING_LIFT_MULT["medium"] — than a naturally energetic one).
        # P5: the plan path's own typed decisions (plan_data, {} for the legacy/
        # safe-default paths) are a per-take editorial judgment and take priority
        # over that coarse heuristic wherever the LLM actually supplied one — every
        # pass still has its own style-driven default when neither is present.
        brief_pacing = (job.get("edit_brief") or {}).get("pacing") or {}
        retention_hints = ({"pacing": {"lift": "medium" if brief_pacing.get("energy") == "low" else "subtle"}}
                           if brief_pacing.get("energy") else {})
        retention_hints.update(_extract_plan_retention_hints(plan_data))
        # A7: a theme's own vibe is a FALLBACK under the plan's own vibe choice —
        # never forces music on/off (that's still purely prefs/plan-driven, so
        # clean_creator stays a golden-diff no-op); it only flavors track
        # selection once music is already wanted by some other signal.
        music_hint = dict(plan_data.get("music") or {})
        _theme_for_music = job.get("_theme")
        if _theme_for_music is not None and not music_hint.get("vibe"):
            music_hint["vibe"] = _theme_for_music.music.get("vibe", "")
        edl_data = _apply_plan_music_vibe(edl_data, prefs, music_hint)
        trim_level = prefs.get("filler_trim") if prefs.get("filler_trim") in ("conservative", "aggressive") else "default"
        # A9: genre profile's interrupt_density is a pre-resolved plain string —
        # the LOWEST-precedence fallback (llm hint > theme > genre > style
        # default), computed here so retention.py never needs to import
        # prompts.GENRE_PROFILES.
        _video_type_for_genre = (job.get("edit_brief") or {}).get("video_type", "")
        genre_density = prompts.GENRE_PROFILES.get(_video_type_for_genre, {}).get("interrupt_density", "")
        edl_data = retention_mod.apply_retention_passes(
            edl_data, words, style=style, prefs=prefs, emphasis_spans=emphasis_spans,
            dossier=job.get("dossier"), hints=retention_hints, script=script, level=trim_level,
            sfx_assets=SFX_ASSETS, job_seed=job_id, theme=job.get("_theme"),
            genre_density=genre_density)
        # A1: deterministic pre-render lint — a live scoreboard of "amateur tells"
        # (dead stretches, glitch cuts, metronomic pacing, off-anchor effects, mixed
        # caption/transition grammars) on WHATEVER the two author paths + retention
        # passes produced. "observe" only logs + stores job["lint"]; "fix" additionally
        # applies error-severity fix_ops ONE round via apply_edl_ops, then re-lints and
        # reverts any fix that introduces a NEW error (mirrors _safe_pass's contract).
        if EDIT_LINT:
            try:
                lint_findings = edit_lint_mod.lint_edl(
                    edl_data, words, style=style, emphasis_spans=emphasis_spans,
                    theme=job.get("_theme"))
                if EDIT_LINT == "fix":
                    fix_ops = [f["fix_op"] for f in lint_findings
                              if f["severity"] == "error" and f["fix_op"]]
                    if fix_ops:
                        before_errors = {f["code"] for f in lint_findings if f["severity"] == "error"}
                        fixed_edl, _ = apply_edl_ops(edl_data, fix_ops, words)
                        refindings = edit_lint_mod.lint_edl(
                            fixed_edl, words, style=style, emphasis_spans=emphasis_spans,
                            theme=job.get("_theme"))
                        after_errors = {f["code"] for f in refindings if f["severity"] == "error"}
                        if not (after_errors - before_errors):
                            edl_data, lint_findings = fixed_edl, refindings
                job["lint"] = edit_lint_mod.lint_summary(lint_findings)
                for f in lint_findings:
                    if f["severity"] == "error":
                        logging.warning("[edit-lint] job=%s %s @out_f%s: %s",
                                        job_id, f["code"], f["at_out_frame"], f["detail"])
            except Exception as e:
                logging.warning("[edit-lint] failed for job=%s: %s", job_id, e)
        # Resolve b-roll cues to real video URLs (Pexels) and attach the duet react
        # source — both must happen before the render plan is built. B-roll resolution
        # is a NICETY: a failure here must degrade to a warning, never fail the whole
        # clip job (the tweak path already guards this the same way — audit B-05/F4).
        try:
            edl_data = await _resolve_broll(edl_data, dossier=job.get("dossier"),
                                            corpus=job.get("broll_corpus"))
            # Addendum Part 8: surface the b-roll decision log (need → asset/tier → action)
            # for the report card. After the tier pass, edl["broll"] holds only kept assets,
            # so a literal need that fell back to a text card is no longer a "broll_unresolved".
            job["broll_log"] = edl_data.pop("_broll_log", [])
            # Addendum mode H: listicle hook flash. When the take is a LISTICLE and >=4
            # distinct real assets resolved, flash them full-frame right after the hook
            # line (12f each, <=5 items = <=2s, inside the 2.5s cap). OUTPUT coords —
            # build_render_plan copies the field verbatim. Conservative by design: any
            # doubt (few assets, not a listicle) → no montage.
            _vtype = (job.get("edit_brief") or {}).get("video_type", "")
            _m_urls: list[str] = []
            for _b in (edl_data.get("broll") or []):
                _u = _b.get("resolved_url")
                if _u and _u not in _m_urls:
                    _m_urls.append(_u)
            if _vtype == "listicle" and len(_m_urls) >= 4:
                edl_data["montage"] = {"frame_in": 75, "frames_per": 12, "items": _m_urls[:5]}
            unresolved = [b.get("broll_query") or b.get("cue_text", "")
                          for b in (edl_data.get("broll") or [])
                          if b.get("source") != "own_media" and not b.get("resolved_url")]
        except Exception as e:
            logging.warning("pipeline b-roll resolve failed: %s", e)
            unresolved = ["resolve failed"]
        if unresolved:
            for c in job["clips"]:
                c.setdefault("warnings", []).extend(f"broll_unresolved: {q}"[:120] for q in unresolved)
        edl_data = _attach_react_source(edl_data, job)
        # A duet_split react window whose length a cut/reorder would desync gets
        # silently dropped by build_render_plan's length-preservation guard (see
        # app/edl.py) — surface it the same way as the broll_unresolved warning
        # just above, rather than the reaction video silently vanishing.
        plan_warnings: list[str] = []
        build_render_plan(edl_data, plan_warnings)
        if plan_warnings:
            for c in job["clips"]:
                c.setdefault("warnings", []).extend(plan_warnings)
        # G3: (re-)populate speech_frames from the actual transcript, not whatever
        # the LLM's JSON did/didn't echo back through the edit + verify/repair
        # passes above — it's derived data, never something an LLM should author,
        # and it must survive regardless of what those steps preserved.
        edl_data["speech_frames"] = [ms_to_frame(w["start_ms"]) for w in _clean_words if w.get("word")]
        # P0.6: loudness normalization — gain = clamp(target − measured, ±12dB). Measured
        # in _run_analysis/_run_pipeline; None (no ffmpeg / unmeasurable) → gain 0 (no-op).
        _audio_block = edl_data.setdefault("audio", {"lufs_target": -14.0})
        _audio_block["gain"] = audio_mod.gain_db(
            job.get("loudness_lufs"), target_lufs=float(_audio_block.get("lufs_target") or -14.0))
        # Second ownership check: the b-roll resolve above also awaited.
        if not _owns_pipeline(job, my_pgen):
            return
        job["edl"] = edl_data
        # P2.2: stamp which KB version steered this edit → A/B + revert + eval scorecard.
        job["knowledge_version"] = knowledge_mod.knowledge_version()

        # P5b: optional self-review — preview render → vision score vs rubric → one
        # revision if below threshold — BEFORE the final render. Flag-gated + fail-soft.
        await _self_review_edl(job_id)

        if not _owns_pipeline(job, my_pgen):   # self-review awaited too
            return
        _mark_stage(job, "rendering")
        await _render_all_clips(job_id)

        if not _owns_pipeline(job, my_pgen):
            return                             # newer owner's state stands
        # Ready ONLY if at least one clip actually delivered a render. The old
        # unconditional ready-set is how "ready" jobs with zero playable clips
        # reached the app.
        job["status"] = "ready" if any(c["status"] == "ready" for c in job["clips"]) else "failed"
        # UX-B2a: one push per JOB, first ready-landing only (push_sent flag) — a
        # tweak re-render goes through _rerender_clip, never here, so it can't push.
        if job["status"] == "ready" and job.get("creator_id") and not job.get("push_sent"):
            job["push_sent"] = True
            ready_clips = [c for c in job["clips"] if c["status"] == "ready"]
            _spawn(push_mod.send_clips_ready(job["creator_id"],
                                             ready_clips[0]["clip_id"], len(ready_clips)))
        if job["status"] == "failed" and not job.get("error"):
            first = next((c for c in job["clips"] if c.get("error")), None)
            job["error"] = (first or {}).get("error", "render_no_output")
            job["error_detail"] = (first or {}).get("error_detail", "")
            job["error_stage"] = "render"
    except PipelineError as e:
        if _owns_pipeline(job, my_pgen):
            _fail_job(job, e.code, e.detail, e.stage)
    except Exception as e:
        if _owns_pipeline(job, my_pgen):
            _fail_job(job, "internal_error", str(e))
    finally:
        if _owns_pipeline(job, my_pgen):
            _spawn(_persist_clip_job(job_id))   # F15: durable at terminal state


SELF_REVIEW = os.environ.get("SELF_REVIEW", "0").lower() in ("1", "true", "yes")
SELF_REVIEW_THRESHOLD = int(os.environ.get("SELF_REVIEW_THRESHOLD", "70"))


async def _sample_render_frames(url: str, n: int = 6) -> list[bytes]:
    """ffmpeg-sample n evenly-spaced frames from a rendered (preview) video → jpeg bytes.
    Fail-soft to [] (no ffmpeg / unreadable). Monkeypatched in tests."""
    import shutil, tempfile, subprocess, glob
    if not shutil.which("ffmpeg") or not url:
        return []
    out: list[bytes] = []
    with tempfile.TemporaryDirectory() as td:
        pat = os.path.join(td, "rev_%03d.jpg")
        try:
            subprocess.run(["ffmpeg", "-y", "-i", url, "-vf", "fps=1,scale=360:-1", "-q:v", "5", pat],
                           capture_output=True, timeout=120)
        except (subprocess.SubprocessError, OSError):
            return []
        paths = sorted(glob.glob(os.path.join(td, "rev_*.jpg")))
        # even sample of at most n
        step = max(1, len(paths) // max(1, n))
        for p in paths[::step][:n]:
            try:
                with open(p, "rb") as fh:
                    out.append(fh.read())
            except OSError:
                continue
    return out


async def _score_edl_vision(frames: list[bytes], plan: dict,
                            lint_findings: list | None = None) -> dict | None:
    """One Claude-vision call scoring sampled frames + the render plan against the review
    rubric → {score_0_100, issues:[{code, frame, fix_op}]}. fix_op is a tweak-envelope op.
    `lint_findings` (A1, optional): unresolved deterministic-lint findings prepended as
    plain text so the vision judge both VERIFIES them visually and can target its own
    fix_ops at the same problems, instead of rediscovering them from pixels alone.
    Monkeypatched in tests; keyless / no frames → None."""
    if not ANTHROPIC_KEY or not frames:
        return None
    import base64, pathlib
    rubric_path = pathlib.Path(__file__).resolve().parent / "knowledge" / "review_rubric.md"
    rubric = rubric_path.read_text() if rubric_path.exists() else "hook 0-3s; caption sync; no slivers; audio; no flashes."
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["score_0_100", "issues"],
        "properties": {
            "score_0_100": {"type": "integer"},
            "issues": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["code", "frame", "fix_op"],
                "properties": {
                    "code": {"type": "string"},
                    "frame": {"type": "integer"},
                    "fix_op": {"type": "object", "additionalProperties": True,
                               "required": ["type"], "properties": {
                                   "type": {"type": "string", "enum": TWEAK_OP_TYPES}}},
                }}},
        },
    }
    lint_block = ""
    if lint_findings:
        lines = "\n".join(f"- {f['code']} (out frame {f['at_out_frame']}): {f['detail']}"
                          for f in lint_findings)
        lint_block = (f"\n\nA DETERMINISTIC LINT already found these issues in this edit's "
                      f"structure — verify them against the actual frames and fix what's real "
                      f"(some may already be addressed by later passes):\n{lines}")
    content: list[dict] = [{"type": "text", "text":
        f"REVIEW RUBRIC:\n{rubric}\n\nRENDER PLAN (output frames):\n{json.dumps(plan, default=str)[:4000]}"
        f"{lint_block}\n\n"
        f"The {len(frames)} images are evenly-sampled frames of the rendered edit. Score 0-100 and list "
        f"issues; each fix_op MUST be a valid tweak op (type from the allowed set) that would fix it."}]
    for fr in frames[:8]:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                        "data": base64.b64encode(fr).decode("ascii")}})
    body = {"model": SONNET, "max_tokens": 1200,
            "system": "You are a strict short-form video QA reviewer. Score against the rubric.",
            "messages": [{"role": "user", "content": content}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}}}
    try:
        client = _get_anthropic_client()
        r = await client.post(ANTHROPIC_URL,
                              headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"}, json=body)
        if r.status_code != 200:
            return None
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        return json.loads(text)
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def _matte_qc(render_url: str, edl_data: dict, style: str) -> dict | None:
    """Addendum Part 6 — cutout/PIP quality gates, run on the FINISHED render of the
    speaker-composited modes (green_screen's rounded speaker card, source_pip's
    circle/rect PIP). Samples ~1 frame/sec and vision-checks: (1) no halo/fringe wider
    than ~2px around hair/shoulders, (2) no flickering matte between adjacent samples,
    (3) no amputated features (scalp/ears/hair cut off by the crop — the circular PIP's
    real failure mode). Returns {"pass": bool, "issues": [...]} or None when not
    applicable / keyless / sampling failed (never blocks the render)."""
    st = (edl_data.get("layout") or {}).get("speaker_treatment", "")
    cutoutish = style == "green_screen" or st.startswith("pip")
    if not cutoutish or not ANTHROPIC_KEY or not render_url:
        return None
    frames = await _sample_render_frames(render_url, 6)
    if not frames:
        return None
    import base64
    schema = {"type": "object", "additionalProperties": False,
              "required": ["pass", "issues"],
              "properties": {"pass": {"type": "boolean"},
                             "issues": {"type": "array", "items": {"type": "string"}}}}
    content: list[dict] = [{"type": "text", "text":
        "These are evenly-sampled frames of a short vertical video where the SPEAKER is "
        "composited in a card / picture-in-picture window over other media. Check the "
        "speaker window ONLY, against these gates:\n"
        "1. No visible halo or color fringe wider than ~2px around hair/shoulders.\n"
        "2. No flickering/jumping matte between frames (window contents shifting without "
        "real subject movement).\n"
        "3. No amputated features: the crop must not cut off the scalp, ears, or chin — "
        "the head and shoulders should sit comfortably inside the window.\n"
        "pass=false ONLY for a clear violation a viewer would notice. List each issue "
        "briefly. An empty speaker window in a frame (speaker hidden by design) is fine."}]
    for fr in frames[:6]:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                        "data": base64.b64encode(fr).decode("ascii")}})
    body = {"model": SONNET, "max_tokens": 400,
            "system": "You are a strict video-compositing QA checker.",
            "messages": [{"role": "user", "content": content}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}}}
    try:
        client = _get_anthropic_client()
        r = await client.post(ANTHROPIC_URL,
                              headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"}, json=body)
        if r.status_code != 200:
            return None
        return json.loads("".join(b.get("text", "") for b in r.json().get("content", [])))
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def _self_review_edl(job_id: str, is_rerender: bool = False) -> None:
    """P5b: render a cheap preview → vision-score it against the rubric → if score <
    SELF_REVIEW_THRESHOLD, apply the returned fix_ops (tweak-envelope) ONCE and re-commit
    the EDL before the final render. Hard limits: flag-gated, one revision, never on
    re-renders/tweaks. Fully fail-soft — any miss leaves the EDL unchanged."""
    if not SELF_REVIEW or is_rerender or not ANTHROPIC_KEY:
        return
    job = _clip_jobs.get(job_id)
    if not job or not job.get("edl"):
        return
    clip = next((c for c in job.get("clips") or [] if c.get("format")), None)
    if not clip:
        return
    try:
        async with _render_semaphore:
            submission = await _submit_remotion_render(
                job["source_url"], job["edl"], clip["format"], job["style"], preview=True)
            if not submission:
                return
            preview_url = await _poll_remotion_render(submission["render_id"], submission["bucket_name"],
                                                      total_frames=submission.get("total_frames"))
    except Exception as e:
        logging.warning("self-review preview render failed: %s", e)
        return
    frames = await _sample_render_frames(preview_url, 6)
    if not frames:
        return
    # A1: hand the vision judge whatever deterministic lint already found (unresolved
    # error-severity findings — EDIT_LINT=fix already cleared what it could upstream)
    # so it verifies real problems instead of re-discovering them from pixels alone.
    lint_for_review = None
    if EDIT_LINT:
        try:
            lint_for_review = [f for f in edit_lint_mod.lint_edl(
                job["edl"], job.get("words") or [], style=job.get("style", ""),
                theme=job.get("_theme")) if f["severity"] == "error"]
        except Exception:
            lint_for_review = None
    review = await _score_edl_vision(frames, build_render_plan(job["edl"]), lint_for_review)
    if not isinstance(review, dict):
        return
    job["self_review"] = {"score": review.get("score_0_100"), "issues": review.get("issues", [])}
    if (review.get("score_0_100") or 100) >= SELF_REVIEW_THRESHOLD:
        return
    ops = [i["fix_op"] for i in (review.get("issues") or [])
           if isinstance(i.get("fix_op"), dict) and i["fix_op"].get("type") in TWEAK_OP_TYPES]
    if not ops:
        return
    try:
        new_edl, results = apply_edl_ops(job["edl"], ops, job.get("words") or [])
        # A1: never let a self-review "fix" introduce a NEW lint error (mirrors
        # app.retention._safe_pass's revert-on-regression contract).
        if EDIT_LINT:
            try:
                before = {f["code"] for f in (lint_for_review or [])}
                after_findings = edit_lint_mod.lint_edl(
                    new_edl, job.get("words") or [], style=job.get("style", ""),
                    theme=job.get("_theme"))
                after = {f["code"] for f in after_findings if f["severity"] == "error"}
                if after - before:
                    logging.info("self-review fix introduced new lint error(s) %s — reverted",
                                 after - before)
                    return
            except Exception:
                pass
        job["edl"] = new_edl
        job["self_review"]["applied"] = [r for r in results if r.get("applied")]
    except Exception as e:
        logging.warning("self-review op apply failed: %s", e)


async def _ffprobe_duration_s(src: str, timeout_s: float = 30.0) -> float | None:
    """Duration in seconds via ffprobe, or None if unmeasurable. `src` may be a
    URL or a local path — ffprobe handles both. Used by A5b/A5c to verify a
    stream-copied-video ffmpeg pass didn't silently change duration before a
    caller adopts its output."""
    if not src or shutil.which("ffprobe") is None:
        return None
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
          "-of", "default=noprint_wrapper=1:nokey=1", src]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        return float(stdout.decode("utf-8", "ignore").strip())
    except Exception:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        return None


async def _finalize_audio_loudness(render_url: str, job_id: str) -> str | None:
    """A5b: true 2-pass loudness normalization on the FINAL rendered mp4 (the
    Lambda output). Video is stream-copied (untouched) throughout; the audio is
    re-encoded to -14 LUFS using ffmpeg's loudnorm filter in its accurate
    2-pass mode. Fail-soft at every step — any missing binary, unmeasurable
    take, subprocess failure, or duration mismatch (the stream-copy guard)
    returns None and the caller keeps the un-normalized Lambda URL. Never
    raises; never fails the job over this."""
    if not AUDIO_FINALIZE or not render_url or shutil.which("ffmpeg") is None:
        return None
    if not (SUPABASE_URL and SUPABASE_KEY):
        return None
    try:
        p1 = await asyncio.create_subprocess_exec(
            *audio_mod.loudnorm_pass1_args(render_url),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr1 = await asyncio.wait_for(p1.communicate(), timeout=60)
        measured = audio_mod.parse_loudnorm_json(stderr1.decode("utf-8", "ignore"))
        if not measured:
            return None
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "finalized.mp4")
            args2 = audio_mod.loudnorm_pass2_args(render_url, measured, out_path)
            if not args2:
                return None
            p2 = await asyncio.create_subprocess_exec(
                *args2, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await asyncio.wait_for(p2.communicate(), timeout=90)
            if p2.returncode != 0 or not os.path.exists(out_path):
                return None
            orig_dur = await _ffprobe_duration_s(render_url)
            new_dur = await _ffprobe_duration_s(out_path)
            if orig_dur is None or new_dur is None or abs(orig_dur - new_dur) > 0.1:
                return None
            with open(out_path, "rb") as f:
                data = f.read()
        base = SUPABASE_URL.rstrip("/")
        key = f"finalized/{job_id}.mp4"
        async with httpx.AsyncClient(timeout=60) as c:
            up = await c.post(
                f"{base}/storage/v1/object/{SUPABASE_STORAGE_BUCKET}/{key}",
                headers={"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY,
                         "Content-Type": "video/mp4", "x-upsert": "true"},
                content=data)
        if 200 <= up.status_code < 300:
            return f"{base}/storage/v1/object/public/{SUPABASE_STORAGE_BUCKET}/{key}"
    except Exception as e:
        logging.warning("[audio-finalize] failed for job=%s: %s", job_id, e)
    return None


async def _generate_poster(render_url: str, job_id: str, clip_id: str) -> str | None:
    """Extract a single poster frame from the finished render and host it, so Library
    cards have a real thumbnail instead of a gray placeholder. ffmpeg range-reads the
    remote mp4 (fast keyframe seek at ~0.6s to skip a black first frame) — no full
    download. Fully fail-soft: any missing binary / bad frame / upload failure returns
    None and the card just keeps its play-icon placeholder. Never raises."""
    if not render_url or shutil.which("ffmpeg") is None:
        return None
    if not (SUPABASE_URL and SUPABASE_KEY):
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "poster.jpg")
            args = ["ffmpeg", "-y", "-ss", "0.6", "-i", render_url,
                    "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "4",
                    "-f", "image2", out_path]
            p = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await asyncio.wait_for(p.communicate(), timeout=45)
            if p.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                return None
            with open(out_path, "rb") as f:
                data = f.read()
        base = SUPABASE_URL.rstrip("/")
        key = f"posters/{job_id}-{clip_id}.jpg"
        async with httpx.AsyncClient(timeout=45) as c:
            up = await c.post(
                f"{base}/storage/v1/object/{SUPABASE_STORAGE_BUCKET}/{key}",
                headers={"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY,
                         "Content-Type": "image/jpeg", "x-upsert": "true"},
                content=data)
        if 200 <= up.status_code < 300:
            return f"{base}/storage/v1/object/public/{SUPABASE_STORAGE_BUCKET}/{key}"
    except Exception as e:
        logging.info("[poster] failed for job=%s clip=%s: %s", job_id, clip_id, e)
    return None


async def _attach_poster(job_id: str, clip: dict, render_url: str, my_gen: int) -> None:
    """Fire-and-forget: generate + attach a poster without delaying the clip's 'ready'
    transition. Re-checks render ownership before writing so a newer render's poster
    isn't clobbered by a stale one."""
    url = await _generate_poster(render_url, job_id, clip.get("clip_id", "c"))
    if url and _is_current_render(clip, my_gen):
        clip["thumbnail_url"] = url


async def _render_all_clips(job_id: str) -> None:
    """Render every non-ready clip from job['edl']. Per-clip isolation: one clip's
    failure marks THAT clip failed (with a structured code) and the others continue.
    Invariant on exit: every touched clip is 'ready' (with render_url) or 'failed'."""
    job = _clip_jobs[job_id]
    edl_data = job["edl"]
    _remotion_env = (REMOTION_SERVE_URL, REMOTION_ACCESS_KEY, REMOTION_FUNCTION_NAME)
    if not all(_remotion_env):
        if any(_remotion_env):
            # PARTIAL env is prod misconfiguration (a rotated secret, a missed var
            # on deploy) — the old keyless fallback silently shipped the raw,
            # UNEDITED source video as a "ready" edit. Fail loud and structured.
            for clip in job["clips"]:
                if clip.get("status") != "ready":
                    _fail_clip(clip, "render_misconfigured",
                               "Remotion env incomplete on the server — check REMOTION_* vars")
            return
        # Fully keyless = dev/test mock: pass the source through as the "render".
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
            async with _render_semaphore:   # G7: bound cross-job Lambda concurrency
                # Superseded while queued (watchdog fail + retry started a newer
                # attempt)? Don't spend a Lambda render whose result every write
                # site would discard anyway.
                if not _is_current_render(clip, my_gen):
                    continue
                # Queue time is not render time: under a burst, waiting on the
                # semaphore can alone exceed RENDER_WATCHDOG_S and get a render
                # that never even submitted falsely killed as render_stalled.
                # Re-stamp at acquisition so the watchdog measures the actual
                # render. (The pre-queue stamp above still covers a task that
                # dies IN the queue — the sweep sees it and fails the clip.)
                clip["render_started_at"] = time.time()
                submission = await _submit_remotion_render(
                    job["source_url"], edl_data, clip["format"], job["style"])
                if not submission:
                    raise PipelineError("render_submit_failed", "no renderId from bridge", "render")
                clip["render_id"] = submission["render_id"]
                clip["bucket_name"] = submission["bucket_name"]
                clip["render_total_frames"] = submission.get("total_frames")
                if job.get("job_id"):
                    _spawn(_persist_clip_job(job["job_id"]))   # durable render_id -> restart re-attach
                render_url = await _poll_remotion_render(
                    submission["render_id"], submission["bucket_name"],
                    total_frames=submission.get("total_frames"))
            # A5b: optional true 2-pass loudness normalization on the finished
            # render — fully fail-soft (returns None on any issue, keeping the
            # Lambda URL). Outside the Lambda-concurrency semaphore (local ffmpeg
            # work, not a Lambda call). Scoped to the main pipeline render only
            # (not tweak re-renders / preview renders / restart re-attach) for now.
            if _is_current_render(clip, my_gen):
                finalized_url = await _finalize_audio_loudness(render_url, job_id)
                if finalized_url:
                    render_url = finalized_url
            # Addendum Part 6: cutout/PIP quality gates on the finished render. A failed
            # circular PIP falls back ONCE to the rounded-rect PIP ("a clean rectangle
            # always beats a bad cutout"); a failed rect/green_screen just records the
            # issues on the job. Fully fail-soft — QC can never fail a good render.
            if _is_current_render(clip, my_gen):
                qc = await _matte_qc(render_url, edl_data, job.get("style", ""))
                if qc is not None:
                    job["matte_qc"] = qc
                    if not qc.get("pass", True) and \
                            (edl_data.get("layout") or {}).get("speaker_treatment") == "pip_circle":
                        try:
                            edl_data.setdefault("layout", {})["speaker_treatment"] = "pip_rounded_rect"
                            job["edl"] = edl_data
                            async with _render_semaphore:
                                if _is_current_render(clip, my_gen):
                                    sub2 = await _submit_remotion_render(
                                        job["source_url"], edl_data, clip["format"], job["style"])
                                    if sub2:
                                        url2 = await _poll_remotion_render(
                                            sub2["render_id"], sub2["bucket_name"],
                                            total_frames=sub2.get("total_frames"))
                                        if url2:
                                            render_url = url2
                                            qc["fallback"] = "pip_rounded_rect"
                        except Exception as e:
                            logging.warning("[matte-qc] fallback re-render failed (keeping "
                                            "original): %s", e)
            if _is_current_render(clip, my_gen):
                clip["render_url"] = render_url
                clip["status"] = "ready"
                # Poster for the Library card — fire-and-forget so it never delays 'ready';
                # iOS picks up clip["thumbnail_url"] on its next poll.
                _spawn(_attach_poster(job_id, clip, render_url, my_gen))
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
    Idempotent — already-normalized (mock) words pass through unchanged.

    F10 hygiene (a corrupt/malformed transcript used to flow straight through,
    producing empty-text captions, zero-length caption-frame collisions from
    missing timestamps, and exact duplicates): drops blank/whitespace-only words,
    drops entries where end<=start (no positive duration), clamps negative
    timestamps to 0, and dedupes exact (word, start_ms, end_ms) repeats."""
    out = []
    seen: set[tuple[str, int, int]] = set()
    for w in raw:
        word = (w.get("word") or w.get("text", "")).strip()
        if not word:
            continue
        start_ms = max(0, int(w.get("start_ms", w.get("start", 0)) or 0))
        end_ms = max(0, int(w.get("end_ms", w.get("end", 0)) or 0))
        if end_ms <= start_ms:
            continue
        key = (word, start_ms, end_ms)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "word": word,
            "start_ms": start_ms,
            "end_ms": end_ms,
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
    """Union of drop lists. A new (filler) drop that overlaps an existing (LLM)
    drop is MERGED into it (extending the boundaries), not skipped outright —
    skipping used to leave the non-overlapping remainder of a filler word un-cut
    whenever a nearby editorial cut only partially covered it (F11)."""
    combined = ([dict(e) for e in (existing or [])]
                + [dict(n) for n in (new or []) if n.get("src_out", 0) > n.get("src_in", 0)])
    if not combined:
        return []
    combined.sort(key=lambda d: d["src_in"])
    out = [combined[0]]
    for d in combined[1:]:
        last = out[-1]
        if d["src_in"] <= last["src_out"]:
            last["src_out"] = max(last["src_out"], d["src_out"])
            if last.get("reason") != "manual" and d.get("reason") == "manual":
                last["reason"] = "manual"
        else:
            out.append(d)
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
        # #5: the SONNET repair authored fresh source-frame numbers on trust. It runs
        # DOWNSTREAM of the primary author path's clamp_edl_to_source, so re-apply the
        # same deterministic clamp here — otherwise the repair can reintroduce an
        # out-of-bounds src_out (past the real source end) that froze/broke the Lambda
        # render, exactly the drift clamp_edl_to_source exists to stop. Same order as
        # the primary path: clamp → construct → validate_and_repair.
        total_frames = ms_to_frame(max((w.get("end_ms", 0) for w in words), default=30000))
        repaired = clamp_edl_to_source(repaired, total_frames)
        obj = EDL(**repaired)
        obj, _ = validate_and_repair(obj)
        return obj.model_dump()
    except Exception:
        return edl_data                                  # repair broke it → keep original


async def _run_render_bridge(*args: str, timeout_s: float | None = None,
                             stdin_data: str | None = None) -> dict:
    """Remotion's render API (renderMediaOnLambda/getRenderProgress) is Node-only —
    there's no documented cross-language wire contract for invoking a deployed Lambda
    function directly. The Node bridge at render/dist/lambda-render.js (built from
    render/src/lambda-render.ts) is the integration point; AWS creds pass through via
    the subprocess's inherited environment (Remotion's SDK reads the exact env var
    names REMOTION_AWS_ACCESS_KEY_ID / REMOTION_AWS_SECRET_ACCESS_KEY itself).

    Hardened: the subprocess call is bounded (a hung node process used to strand a
    clip in 'rendering' forever), and errors come back in-band via `_error` so they
    reach the clip's error field instead of dying in a log line."""
    # stdin_data: large payloads (the render plan JSON) go through a pipe, never
    # argv — Linux caps a single argv string at 128KB (MAX_ARG_STRLEN), and a long
    # take's caption-heavy plan blows past it, failing execve with E2BIG before
    # node even launches (surfaced as internal_error on the clip).
    proc = await asyncio.create_subprocess_exec(
        "node", REMOTION_BRIDGE, *args,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_data.encode() if stdin_data is not None else None),
            timeout=timeout_s or BRIDGE_CALL_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        # Drain whatever the node process buffered before the kill — otherwise a hang
        # (e.g. a Remotion client/function version skew) surfaces only as an opaque
        # "timed out" with zero AWS detail, which is exactly what hid the 4.0.486-vs-484
        # bug. Bounded so a truly-wedged process can't block this path too.
        tail = ""
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
            tail = ((err.decode(errors="replace") or out.decode(errors="replace")) or "")[:300].strip()
        except Exception:
            pass
        t = timeout_s or BRIDGE_CALL_TIMEOUT_S
        msg = f"bridge timed out after {t:.0f}s" + (f" — {tail}" if tail else "")
        logging.warning("remotion bridge TIMEOUT (cmd=%s): %s", args[0] if args else "?", msg)
        return {"_error": msg}
    if proc.returncode != 0:
        # The bridge prints ONE JSON error object as its last stderr line, but node
        # runtime warnings (S3-offload notices, deprecations) can prefix it — and
        # truncating BEFORE parsing chopped the real Remotion error off the end,
        # surfacing "(node:12) Warning: ..." as the clip's error instead.
        full = stderr.decode(errors="replace")
        logging.warning("remotion bridge failed: %s", full[:500])
        detail = None
        idx = full.rfind('{"error"')          # the bridge's own JSON is written LAST
        if idx != -1:
            try:
                detail = json.loads(full[idx:]).get("error")
            except json.JSONDecodeError:
                pass
        return {"_error": str(detail or full[:500].strip())[:300]}
    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError:
        raw = stdout.decode(errors="replace")[:300]
        logging.warning("remotion bridge non-JSON output: %s", raw)
        return {"_error": f"bridge non-JSON output: {raw}"}


async def _submit_remotion_render(source_url: str, edl: dict, format_id: str, style: str,
                                  preview: bool = False) -> dict | None:
    if not (REMOTION_SERVE_URL and REMOTION_FUNCTION_NAME):
        return None
    # Remotion Lambda composition IDs may only contain a-z, A-Z, 0-9, CJK, and "-" —
    # underscores are rejected at render time (discovered live: "Composition id can
    # only contain ... You passed Marque_TalkingHead"). Must match Root.tsx exactly.
    # Addendum mode E: a duet with speaker_treatment=pip_* renders through the
    # source-primary + speaker-PIP composition instead of the top/bottom split.
    if style == "duet_split" and \
            (edl.get("layout") or {}).get("speaker_treatment", "").startswith("pip"):
        style = "source_pip"
    composition_id = f"Marque-{style.title().replace('_', '')}"
    # Transform the editorial EDL (source coords) into a render-ready plan: the actual
    # cut list + captions/overlays remapped to the post-cut output timeline. The
    # compositions consume this plan directly (they no longer trim it themselves).
    plan_warnings: list[str] = []
    plan = build_render_plan(edl, plan_warnings)
    input_props = json.dumps({"sourceUrl": source_url, "edl": plan, "formatId": format_id})
    # G9: preview=true asks the bridge for a cheap low-res proof render (half
    # scale, higher CRF — see lambda-render.ts submit()); the caller is
    # responsible for writing the result to a side-channel field, never
    # render_url, since this is not the final output.
    #
    # Props travel via STDIN (argv "-"): a single Linux argv string caps at 128KB
    # and long-take plans exceed it (execve E2BIG → the whole submit dies).
    # #18: renderMediaOnLambda DISPATCHES the render as part of this call. A bridge
    # timeout most likely means the render already STARTED server-side but the killed
    # node process never returned its id — so the old "retry on timeout" would spin up
    # a SECOND, orphaned render (billed, never polled). Instead: one attempt with a
    # generous cold-start-covering budget (G8: a cold Lambda's dispatch can be slow),
    # and any failure surfaces as bridge_error for the clip-level retry/watchdog to
    # handle cleanly — no blind re-dispatch.
    result = await _run_render_bridge("submit", composition_id, "-", "1" if preview else "0",
                                      timeout_s=RENDER_SUBMIT_TIMEOUT_S, stdin_data=input_props)
    if result.get("_error"):
        raise PipelineError("bridge_error", result["_error"], "render")
    if not result.get("renderId"):
        return None
    return {"render_id": result["renderId"], "bucket_name": result.get("bucketName", ""),
            "plan_warnings": plan_warnings,
            # #17: output length drives the poll/stall budgets at the caller.
            "total_frames": plan.get("total_frames")}


async def _poll_remotion_render(render_id: str, bucket_name: str,
                                max_wait_s: int | None = None,
                                total_frames: int | None = None) -> str:
    """Poll the Lambda render to completion. Fail-FAST: exponential backoff within a
    hard wall-clock budget, stall detection on overallProgress, and every failure
    raises a structured PipelineError. (The old version linear-polled for 10 minutes
    and returned None with no reason — the single biggest 'clips never finish' vector.)

    #17: budget + stall tolerance scale with `total_frames` (the render's output
    length) so a long take isn't falsely failed as timed-out/stalled."""
    scaled_budget, stall_budget = _scaled_render_budgets(total_frames)
    budget = max_wait_s if max_wait_s is not None else scaled_budget
    start = time.time()
    delays = [2.0, 4.0, 8.0]
    i = 0
    last_progress = -1.0
    last_change = start
    poll_errors = 0   # consecutive bridge-poll failures (see below)
    while time.time() - start < budget:
        await asyncio.sleep(delays[i] if i < len(delays) else 15.0)
        i += 1
        progress = await _run_render_bridge("poll", render_id, bucket_name)
        if progress.get("_error"):
            # A poll is pure observation — the Lambda render is unaffected by a
            # missed one. A single transient failure (node OOM-kill, AWS throttle,
            # network blip) used to fail the WHOLE render as bridge_error while it
            # was succeeding server-side. Tolerate up to 3 consecutive misses;
            # only a persistently-dead bridge fails the clip.
            poll_errors += 1
            if poll_errors >= 3:
                raise PipelineError("bridge_error", progress["_error"], "render")
            continue
        poll_errors = 0
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
        elif now - last_change > stall_budget:
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
                         "shouldDownloadVideos": True, "profileScrapeSections": ["videos"]}
    else:
        actor = "apify~instagram-scraper"
        payload = {"directUrls": [f"https://www.instagram.com/{handle}/"],
                   "resultsType": "posts", "resultsLimit": limit}
    items = await _run_apify_actor(actor, payload)
    posts = [p for p in (_normalize_apify_post(i, platform) for i in items if isinstance(i, dict)) if p]
    return posts[:limit]


# Over-broad single words that, alone, pull off-niche reels ("fitness" returns all of
# fitness, not "fitness for busy parents"). We keep them only as part of a compound tag.
_BROAD_NICHE_WORDS = frozenset({
    "fitness", "food", "cooking", "business", "money", "finance", "travel", "beauty",
    "fashion", "health", "life", "tips", "content", "video", "creator", "marketing",
})


def _niche_hashtags(niche: str) -> list[str]:
    """Turn a free-text niche into SPECIFIC search hashtags. The old '#fitness' broadener
    pulled the whole category (the off-niche complaint); now we build compound tags
    (full slug + meaningful word pairs) and only fall back to a bare word when it's
    already specific (not in _BROAD_NICHE_WORDS)."""
    stop = {"for", "and", "the", "a", "an", "of", "to", "in", "on", "with", "your", "my"}
    words = [w for w in re.split(r"[^a-z0-9]+", niche.lower()) if w and w not in stop]
    if not words:
        return []
    tags: list[str] = ["".join(words)[:30]]                      # full slug, most specific
    if len(words) >= 2:
        tags.append((words[-1] + words[0])[:30])                # e.g. "parentsfitness"
        tags.append((words[0] + words[-1])[:30])                # e.g. "fitnessparents"
    # a single bare word only if it's specific enough to not pull the whole category
    for w in words:
        if w not in _BROAD_NICHE_WORDS and w not in tags:
            tags.append(w)
            break
    # de-dupe, keep order, cap at 3 (Apify hashtag scraper cost)
    seen: set[str] = set()
    return [t for t in tags if t and not (t in seen or seen.add(t))][:3]


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
                                        "shouldDownloadVideos": True})
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
    # Skip posts that already carry a transcript (merged from a previous cycle) —
    # each refresh then transcribes up to top_n NEW reels, so coverage accumulates.
    ranked = sorted((p for p in posts if p.get("video_url") and not p.get("transcript")),
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
async def emulate_analyze(req: EmulateAnalyzeRequest, _budget_s: float | None = _SCRAPE_BUDGET_S):
    """Kick off (and cache) style analysis for a custom emulation target. Called
    fire-and-forget from onboarding — the profile resolves lazily on the next
    generation call via _resolve_emulation_profiles, so this never blocks the UI.

    B-09: the scrape+transcribe is bounded by _budget_s (default ~25s) so the DIRECT
    endpoint always returns inside a proxy timeout instead of hanging up to ~5min; on
    timeout it degrades to an un-cached mock the client can re-trigger (response shape
    unchanged → no iOS coordination needed). Background callers (the digest) pass
    _budget_s=None to run unbounded, since nothing is waiting on them."""
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

    budget = None if _budget_s is None else max(1.0, min(float(_budget_s), 60.0))  # clamp client input
    async def _scrape_and_transcribe():
        p = await scrape_posts(handle, req.platform)
        return await _transcribe_top_posts(p)
    try:
        posts = (await asyncio.wait_for(_scrape_and_transcribe(), timeout=budget)
                 if budget else await _scrape_and_transcribe())
    except asyncio.TimeoutError:
        logging.warning("emulate scrape exceeded %ss budget for %s — degrading", _budget_s, handle)
        posts = []                                # → un-cached mock below, client can re-trigger
    real = False                                  # True only for a genuine live analysis
    if not ANTHROPIC_KEY or not posts:
        profile = _mock_emulation_profile(handle)
        mode = "mock"
    else:
        try:
            sys, usr = prompts.derive_emulation_prompt(handle, posts)
            parsed = extract_json(await anthropic(sys, usr, HAIKU, 900), array=False)
            profile = parsed or _mock_emulation_profile(handle)
            mode, real = "live", bool(parsed)
        except HTTPException:
            profile = _mock_emulation_profile(handle)
            mode = "mock"

    # Cache a REAL analysis, or a pure-keyless mock (deterministic demo, nothing to
    # retry). Do NOT cache/persist a live-attempt that fell back to mock (empty scrape
    # / LLM error) — persisting the generic mock durably poisons this handle so every
    # future call short-circuits at "cached" and never re-analyzes (audit B-07/F7).
    keyless = not ANTHROPIC_KEY
    if real or keyless:
        _emulation_cache[handle] = profile
        _cap_evict(_emulation_cache, _EMULATION_CACHE_CAP)
    if real and _supabase_client:
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
    if req.posts:
        posts = req.posts
    else:
        try:                                       # B-09: bound the scrape (proxy-timeout safety)
            posts = await asyncio.wait_for(scrape_posts(req.handle, req.platform),
                                           timeout=_SCRAPE_BUDGET_S)
        except asyncio.TimeoutError:
            logging.warning("brand-scan scrape exceeded budget for %s — degrading", req.handle)
            posts = []
    if posts:
        # B3: persist real scraped posts so the feed/mimic/analyze-video/converse prompts
        # can pull verbatim voice exemplars later, without the client ever holding them.
        _spawn(_persist_creator_posts(req.creator_id, posts))
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
    _spawn(_run_digest(job_id))
    # Palo port (audit: suggest_ideas was fully unwired): onboarding completion IS the
    # first-ideas moment — generate → niche-connection eval gate → persist briefs, so a
    # brand-new creator's idea bank isn't empty until the nightly spitfire sweep. Off the
    # hot path; flag- and real-creator-gated inside.
    if palo_flags.enabled(palo_flags.IDEA_BANK) and palo_flags.real_creator(req.creator_id):
        _spawn(ideas.suggest_ideas(_palo_store, req.creator_id, req.d(), source="onboarding"))
    return {"mode": "live", "job_id": job_id, "status": "running"}


@app.get("/v1/onboarding/digest/{job_id}")
async def get_digest_job(job_id: str):
    _sweep_ttl_jobs(_digest_jobs)
    if job_id not in _digest_jobs:
        # P-06: same 410-expired vs 404-never-existed split the clip endpoints give —
        # the app treats "session expired, regenerate locally" and "bad id" differently.
        _raise_job_not_found(job_id)
    return _digest_public(_digest_jobs[job_id])


def _digest_script_request(req: DigestRequest, scan: dict) -> ScriptRequest:
    """Build the starter-scripts request from the derived scan (voice + first pillar
    flow straight from the digest evidence into the script pipeline)."""
    pillars = scan.get("pillars") or []
    first = pillars[0] if pillars else {}
    voice = scan.get("voice") or req.voice or {}
    # DERIVE_SCHEMA puts catchphrases + bannedWords at the TOP LEVEL of the scan (voice
    # holds only the tone sliders) — reading scan["voice"]["catchphrases"] always missed
    # the derived phrases. Also fold derived bannedWords into non_negotiables so the
    # script engine actually honors them (audit B-10/F13/F15).
    catchphrases = scan.get("catchphrases") or req.catchphrases
    non_negotiables = list(req.non_negotiables or []) + list(scan.get("bannedWords") or [])
    return ScriptRequest(
        niche=scan.get("niche") or req.niche,
        audience=req.audience, known_for=req.known_for, what_you_do=req.what_you_do,
        goal=req.goal, voice=voice, non_negotiables=non_negotiables,
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
        if posts:
            # B3: persist (with real transcripts) so later prompts get verbatim voice
            # exemplars without the client ever holding these posts.
            _spawn(_persist_creator_posts(req.creator_id, posts))

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
                    await emulate_analyze(EmulateAnalyzeRequest(handle=handle, platform=t.get("platform", "instagram")),
                                          _budget_s=None)   # background: run unbounded, nothing waits
                except Exception:
                    pass
        # Each derive stage degrades independently: a transient Anthropic 5xx here must
        # NOT throw away a successful scrape+transcription (the onboarding centerpiece).
        # scan → mock_derive, judge → keep unjudged pillars; scripts degrade inside
        # _generate_scripts. (audit B-06/F5)
        scan: dict | None = None
        try:
            if posts:
                sys, usr = prompts.derive_from_posts_prompt(brand, posts)
                scan = extract_json(await anthropic(sys, usr, OPUS, 2200), array=False)
            elif req.voice_transcript:
                sys, usr = prompts.voice_finalize_prompt(brand, req.voice_transcript)
                scan = extract_json(await anthropic(sys, usr, OPUS, 2200), array=False)
        except HTTPException:
            scan = None                        # degrade to the deterministic brand below
        scan = scan or mock_derive(brand, posts)
        if scan.get("pillars"):
            try:
                merged = {**brand, "niche": scan.get("niche", brand.get("niche", ""))}
                scan["pillars"] = await judge_and_fix_pillars(merged, scan["pillars"], posts or None)
            except HTTPException:
                pass                           # keep the unjudged pillars rather than fail the digest

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
    # The real token mint (ElevenLabs get-signed-url) isn't implemented yet, so we
    # cannot return a usable session — report mode:"mock" (not "live") rather than hand
    # the client an empty token it can't start a session with (audit B-10/F19).
    return {"mode": "mock", "agent_system": prompts.VOICE_AGENT_SYSTEM,
            "agent_id": agent_id, "conversation_token": "", "session_id": uuid.uuid4().hex}


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


# ----- publishing via Post for Me -----------------------------------------
# Post for Me is the publish backend (per-post pricing, unlimited accounts, brings its
# own approved IG/TikTok apps). The account-link flow is per-user: the app asks the
# backend for an OAuth `auth-url` (tagged with the creator's external_id), the user
# authorizes through Post for Me, then we look the account(s) up by external_id to get
# their `spc_...` ids and post to them. The key lives only here (server-side).


async def _pfm_request(method: str, path: str, *, json_body: dict | None = None,
                       params: dict | None = None) -> tuple[int, dict]:
    """One place all Post for Me calls go through. Returns (status_code, json)."""
    headers = {"Authorization": f"Bearer {POSTFORME_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, f"{POSTFORME_BASE}{path}",
                                 headers=headers, json=json_body, params=params)
    try:
        data = r.json()
    except (ValueError, json.JSONDecodeError):
        data = {}
    return r.status_code, data


class SocialAuthURLRequest(BaseModel):
    platform: str                    # "instagram" | "tiktok" | "youtube" | ...
    external_id: str = ""            # our per-user tag; how we find this account later
    redirect_url: str = ""           # optional: deep-link back into the app after OAuth


@app.post("/v1/social/auth-url")
async def social_auth_url(req: SocialAuthURLRequest):
    """Mint a Post for Me OAuth URL for a user to connect one IG/TikTok account.
    Mock (no key) returns an empty url so the client shows 'linking unavailable'."""
    if not POSTFORME_KEY:
        return {"url": "", "platform": req.platform, "mode": "mock"}
    body: dict = {"platform": req.platform, "permissions": ["posts"]}
    if req.external_id:
        body["external_id"] = req.external_id
    if req.redirect_url:
        body["redirect_url_override"] = req.redirect_url
    try:
        code, data = await _pfm_request("POST", "/social-accounts/auth-url", json_body=body)
        # Quickstart PFM projects reject per-request redirect overrides ("Redirect URL
        # Override is not allowed for Quickstart Projects…") with a 4xx + EMPTY url — which
        # would silently break Instagram/TikTok linking. Retry once WITHOUT the override so
        # the connect flow falls back to the dashboard-configured Project Redirect URL. The
        # iOS client sends an empty redirect today, but this makes the endpoint robust to any
        # caller that supplies one.
        if not (200 <= code < 300) and "redirect_url_override" in body \
                and "redirect url override" in str(data.get("message", "")).lower():
            body.pop("redirect_url_override", None)
            code, data = await _pfm_request("POST", "/social-accounts/auth-url", json_body=body)
    except httpx.HTTPError:
        return {"url": "", "platform": req.platform, "mode": "live", "error": "network"}
    if 200 <= code < 300:
        return {"url": data.get("url", ""), "platform": data.get("platform", req.platform), "mode": "live"}
    return {"url": "", "platform": req.platform, "mode": "live", "error": data.get("message", f"http_{code}")}


@app.get("/v1/social/accounts")
async def social_accounts(external_id: str = "", platform: str = ""):
    """List a user's connected accounts (filtered by our external_id tag). Returns the
    normalized shape the app stores: id (spc_...), platform, username, profile photo."""
    if not POSTFORME_KEY:
        return {"accounts": [], "mode": "mock"}
    params: dict = {}
    if external_id:
        params["external_id"] = external_id
    if platform:
        params["platform"] = platform
    try:
        code, data = await _pfm_request("GET", "/social-accounts", params=params)
    except httpx.HTTPError:
        return {"accounts": [], "mode": "live", "error": "network"}
    accounts = [
        {
            "id": a.get("id", ""),
            "platform": a.get("platform", ""),
            "username": a.get("username", ""),
            "profile_photo_url": a.get("profile_photo_url", ""),
            "status": a.get("status", ""),
            "external_id": a.get("external_id", ""),
        }
        for a in (data.get("data") or [])
    ]
    return {"accounts": accounts, "mode": "live"}


class SocialDisconnectRequest(BaseModel):
    account_id: str


@app.post("/v1/social/disconnect")
async def social_disconnect(req: SocialDisconnectRequest):
    if not POSTFORME_KEY:
        return {"ok": True, "mode": "mock"}
    try:
        code, _ = await _pfm_request("POST", f"/social-accounts/{req.account_id}/disconnect")
    except httpx.HTTPError:
        return {"ok": False, "mode": "live", "error": "network"}
    return {"ok": 200 <= code < 300, "mode": "live"}


class PublishRequest(BaseModel):
    caption: str = ""
    media_url: str = ""
    platforms: list[str] = []            # legacy field (kept for back-compat)
    schedule_date: str = ""
    social_account_ids: list[str] = []   # Post for Me spc_ids to post to
    draft: bool = False                  # if true, create as draft (no real post) — used for tests


@app.post("/v1/publish")
async def publish(req: PublishRequest):
    # No key, or no linked accounts to target => mock (nothing is actually posted).
    # C-01: `posted`/`reason` are ADDITIVE and honest — the app must not show "Posted"
    # for a mock. `ok` semantics are FROZEN (shipped build 9 reads only `ok`).
    if not POSTFORME_KEY or not req.social_account_ids:
        return {"ok": True, "mode": "mock", "id": f"post_{uuid.uuid4().hex[:10]}",
                "posted": False, "reason": "no_key" if not POSTFORME_KEY else "no_accounts"}
    body: dict = {"caption": req.caption, "social_accounts": req.social_account_ids}
    if req.media_url.startswith("http"):
        body["media"] = [{"url": req.media_url}]
    if req.schedule_date:
        body["scheduled_at"] = req.schedule_date
    if req.draft:
        body["isDraft"] = True
    try:
        code, data = await _pfm_request("POST", "/social-posts", json_body=body)
    except httpx.HTTPError:
        return {"ok": False, "mode": "live", "error": "network",
                "posted": False, "reason": "network"}
    ok = 200 <= code < 300
    return {"ok": ok, "mode": "live", "id": data.get("id", ""),
            "status": data.get("status", ""), "http": code,
            "posted": ok, "reason": None if ok else "upstream",
            **({"error": data.get("message")} if code >= 300 else {})}


# ---------------------------------------------------------------------------
# Phase 3: Media analysis + auto B-roll
# ---------------------------------------------------------------------------

@app.post("/v1/media/analyze")
async def analyze_media(req: MediaAnalyzeRequest):
    """Analyze a media asset for B-roll suitability. Idempotent via content_hash cache."""
    if req.content_hash in _media_cache:
        return {"mode": "cached", **_media_cache[req.content_hash]}

    mock = {
        "description": f"A {req.kind} asset suitable for B-roll use.",
        "scene": "indoor", "subjects": ["person", "environment"], "has_face": False,
        "on_screen_text": "", "motion": "slow", "quality": "high",
        "dominant_colors": ["warm white", "natural", "neutral"],
        "broll_suitability": 72, "broll_suitability_reason": "Good framing for B-roll.",
        "usable_as": "broll", "suggested_kind": req.kind,
        "tags": [req.kind, "interior", "natural light", "close-up", "lifestyle"],
    }
    if not ANTHROPIC_KEY or not req.public_url:
        _media_cache[req.content_hash] = mock
        _cap_evict(_media_cache, 256)
        return {"mode": "mock", **mock}

    system, user_text = prompts.media_analyze_prompt(req.filename, req.kind)
    try:
        async with httpx.AsyncClient(timeout=60) as vclient:
            r = await vclient.post(
                ANTHROPIC_URL,
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": HAIKU, "max_tokens": 1000, "system": system,
                      "messages": [{"role": "user", "content": [
                          {"type": "image", "source": {"type": "url", "url": req.public_url}},
                          {"type": "text", "text": user_text},
                      ]}]},
            )
        result = extract_json("".join(b.get("text", "") for b in r.json().get("content", [])),
                              array=False) if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        logging.warning("media vision failed: %s", e)
        result = None
    # Only cache a REAL analysis (has the shape fields). A transport error, non-200, or
    # malformed reply degrades to the full mock and is NOT cached — so a transient blip
    # can't poison this content_hash forever (audit B-03/F2/F15).
    if not (result and "broll_suitability" in result):
        return {"mode": "mock", **mock}
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

    # Tie-break when scores are close and we have a key. P4.1: prefer a VISION re-rank over
    # the top own-media candidates' thumbnails (thumb_url on the corpus asset) — the same
    # picker the auto-edit b-roll uses; fall back to the text-only Haiku tie-break when no
    # thumbnails are available. Positional candidate_index avoids the score-sorted corpus-
    # index bug (audit B-04/F12/F14).
    if ANTHROPIC_KEY and len(top) >= 2 and top[0]["score"] - top[1]["score"] < 0.05:
        top_assets = [req.corpus[t["index"]] for t in top[:BROLL_CANDIDATES]]
        thumb_urls = [a.get("thumb_url") for a in top_assets]
        if sum(1 for u in thumb_urls if u) >= 2:
            thumbs: list[bytes] = []
            try:
                async with httpx.AsyncClient(timeout=10) as tc:
                    for u in thumb_urls:
                        if not u:
                            thumbs.append(b""); continue
                        tr = await tc.get(u)
                        thumbs.append(tr.content if tr.status_code == 200 else b"")
                ci = await _broll_vision_pick(req.cue_text, thumbs, None)
            except httpx.HTTPError:
                ci = None
            if isinstance(ci, int) and 0 <= ci < len(top):
                chosen = top[ci]
                top = [chosen] + [t for t in top if t["asset_id"] != chosen["asset_id"]]
        else:
            cands = [{"candidate_index": j, "asset_id": t["asset_id"], "description": t["description"]}
                     for j, t in enumerate(top[:3])]
            system, user = prompts.broll_match_prompt(req.cue_text, cands)
            try:
                pick = extract_json(await anthropic(system, user, model=HAIKU, max_tokens=100), array=False)
                ci = pick.get("chosen_index") if isinstance(pick, dict) else None
                if isinstance(ci, int) and 0 <= ci < len(cands):
                    chosen = top[ci]
                    top = [chosen] + [t for t in top if t["asset_id"] != chosen["asset_id"]]
            except Exception:
                pass

    # Pexels fallback for unmatched beats — vision-re-ranked over BROLL_CANDIDATES.
    if not top or top[0]["score"] < 0.3:
        cands = await _fetch_pexels_candidates(req.cue_text, BROLL_CANDIDATES)
        pexels = await _rerank_broll(req.cue_text, cands, None)
        top = [{"asset_id": None, "source": "pexels", "pexels_url": pexels,
                "score": 0.5, "description": req.cue_text}] + top

    return {"mode": "live" if ANTHROPIC_KEY else "mock", "matches": top}


def _pexels_best_file(video: dict) -> str | None:
    """Pick the best renderable file link from one Pexels video (portrait/hd first).
    G5: orientation=portrait only biases which VIDEOS come back — a matched video can
    still expose landscape transcodes among its own video_files; objectFit:cover never
    letterboxes but a native-portrait rendition needs far less cropping."""
    files = video.get("video_files", [])
    if not files:
        return None
    def _is_portrait(f: dict) -> bool:
        return (f.get("height") or 0) > (f.get("width") or 0)
    best = (next((f for f in files if _is_portrait(f) and f.get("quality") == "hd"), None)
            or next((f for f in files if _is_portrait(f)), None)
            or next((f for f in files if f.get("quality") == "hd"), None)
            or files[0])
    return best.get("link")


async def _fetch_pexels_candidates(query: str, n: int = 1) -> list[dict]:
    """P4.1: fetch up to n candidate videos → [{link, thumb}] (thumb = the preview image
    for vision re-rank). Fail-soft to [] (no key / error / no results). Boundary is
    monkeypatchable in tests."""
    if not PEXELS_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.pexels.com/videos/search",
                                 headers={"Authorization": PEXELS_KEY},
                                 params={"query": query, "per_page": max(1, n), "orientation": "portrait"})
        if r.status_code != 200:
            return []
        videos = r.json().get("videos", [])
    except (httpx.HTTPError, ValueError) as e:      # transport error / malformed body → no b-roll
        logging.warning("pexels fetch failed: %s", e)
        return []
    out: list[dict] = []
    for v in videos:
        link = _pexels_best_file(v)
        if link:
            out.append({"link": link, "thumb": v.get("image")})
    return out


async def _fetch_pexels(query: str) -> str | None:
    """Single best b-roll link for `query` (back-compat single-candidate path)."""
    cands = await _fetch_pexels_candidates(query, 1)
    return cands[0]["link"] if cands else None


async def _broll_vision_pick(cue: str, thumbs: list[bytes], dossier: dict | None) -> int | None:
    """One Haiku vision call scoring candidate thumbnails against the cue + the a-roll's
    palette/energy (from the dossier). Returns the best index, or None to fall back to
    top-1. Monkeypatched in tests; keyless → None."""
    if not ANTHROPIC_KEY or len(thumbs) < 2:
        return None
    import base64
    fr = (dossier or {}).get("framing") or {}
    aroll_hint = f"a-roll look: lighting={fr.get('lighting')}, shot={fr.get('shot')}" if fr else ""
    content: list[dict] = [{"type": "text", "text":
        f"Pick the single best b-roll clip for the cue \"{cue}\". {aroll_hint}. "
        f"Score cue relevance first, then palette/energy match to the a-roll. "
        f"Return JSON {{\"best_index\": int}} (0-based)."}]
    for t in thumbs[:BROLL_CANDIDATES]:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                        "data": base64.b64encode(t).decode("ascii")}})
    body = {"model": HAIKU, "max_tokens": 100,
            "system": "You are a b-roll art director. Choose the best-matching clip.",
            "messages": [{"role": "user", "content": content}],
            "output_config": {"format": {"type": "json_schema", "schema": {
                "type": "object", "additionalProperties": False, "required": ["best_index"],
                "properties": {"best_index": {"type": "integer"}}}}}}
    try:
        client = _get_anthropic_client()
        r = await client.post(ANTHROPIC_URL,
                              headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"}, json=body)
        if r.status_code != 200:
            return None
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        idx = (json.loads(text) or {}).get("best_index")
        return idx if isinstance(idx, int) and 0 <= idx < len(thumbs) else None
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def _rerank_broll(cue: str, candidates: list[dict], dossier: dict | None = None) -> str | None:
    """Choose the best candidate link via vision re-rank (fetch thumbnails → _broll_vision_pick),
    falling back to top-1 on any miss. Keyless / single-candidate → top-1."""
    if not candidates:
        return None
    if len(candidates) == 1 or not ANTHROPIC_KEY:
        return candidates[0]["link"]
    thumbs: list[bytes] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            for c in candidates[:BROLL_CANDIDATES]:
                if not c.get("thumb"):
                    thumbs.append(b"")
                    continue
                tr = await client.get(c["thumb"])
                thumbs.append(tr.content if tr.status_code == 200 else b"")
    except httpx.HTTPError:
        return candidates[0]["link"]
    valid = [t for t in thumbs if t]
    if len(valid) < 2:
        return candidates[0]["link"]
    idx = await _broll_vision_pick(cue, thumbs, dossier)
    if idx is None or idx >= len(candidates):
        return candidates[0]["link"]
    return candidates[idx]["link"]


_broll_url_cache: dict[str, str] = {}


# Queries whose GENERATION already failed once — never re-burn credits/latency on them
# (a successful resolution is cached in _broll_url_cache; this is the negative side).
_broll_gen_failed: set[str] = set()


def _score_broll_corpus(cue_text: str, corpus: list[dict]) -> tuple[dict | None, float]:
    """Pure: best-matching own-media asset for a cue by keyword overlap + suitability
    (shared with /v1/broll/match). Returns (asset, score) or (None, 0)."""
    cue_lower = (cue_text or "").lower()
    words = [w for w in cue_lower.split() if len(w) > 3]
    best, best_score = None, 0.0
    for asset in corpus or []:
        desc = (str(asset.get("description", "")) + " " + " ".join(asset.get("tags", []) or [])).lower()
        hits = sum(1 for w in words if w in desc)
        suitability = float(asset.get("broll_suitability", 50)) / 100.0
        score = 0.55 * min(1.0, hits / max(1, len(words))) + 0.45 * suitability
        if score > best_score:
            best, best_score = asset, score
    return best, best_score


_OWN_MEDIA_FLOOR = float(os.environ.get("OWN_MEDIA_BROLL_FLOOR", "0.45"))


async def _resolve_broll(edl: dict, dossier: dict | None = None,
                         allow_generation: bool = True,
                         corpus: list[dict] | None = None) -> dict:
    """Resolve each b-roll cue to a real portrait video URL. WS4: try the creator's OWN
    analyzed media (corpus) FIRST — a matching own clip above the score floor becomes an
    own_media hit — then fall back to Pexels stock, then Higgsfield generation. Cached by
    query so re-renders don't re-hit Pexels/vision."""
    broll = edl.get("broll") or []
    if not broll:
        return edl
    has_stock = PEXELS_KEY or higgsfield_mod.CONFIGURED
    can_resolve = has_stock or corpus
    generated_count = 0
    for b in broll if can_resolve else []:
        if b.get("resolved_url") or b.get("source") == "own_media":
            continue
        query = (b.get("broll_query") or b.get("cue_text") or "").strip()
        if not query:
            continue
        # Own media first — the creator's real footage beats stock when it matches.
        if corpus:
            asset, score = _score_broll_corpus(b.get("cue_text") or query, corpus)
            url = asset.get("remote_url") if asset else None
            if asset and url and score >= _OWN_MEDIA_FLOOR:
                b["source"] = "own_media"
                b["resolved_url"] = url
                b["asset_id"] = asset.get("asset_id", "")
                continue
        if not has_stock:
            continue
        if query in _broll_url_cache:
            b["resolved_url"] = _broll_url_cache[query]
            continue
        candidates = await _fetch_pexels_candidates(query, BROLL_CANDIDATES)
        url = await _rerank_broll(b.get("cue_text") or query, candidates, dossier)
        # Higgsfield fallback: stock had NOTHING for this cue → generate a clip instead
        # of silently dropping the cutaway. Runs ONLY on the initial edit pipeline
        # (allow_generation=False on tweak re-renders — generation inside the render
        # watchdog window falsely failed succeeding renders, and re-tweaks re-burned
        # credits), capped per pass, negative-cached per query. Fail-soft.
        if not url and allow_generation and higgsfield_mod.CONFIGURED \
                and generated_count < _HIGGSFIELD_MAX_PER_JOB \
                and query not in _broll_gen_failed:
            generated_count += 1
            url = await higgsfield_mod.generate_broll(b.get("cue_text") or query)
            if not url:
                _broll_gen_failed.add(query)
                if len(_broll_gen_failed) > 5000:
                    _broll_gen_failed.clear()     # bounded; a rare full reset is fine
        if url:
            _broll_url_cache[query] = url
            _cap_evict(_broll_url_cache, 10_000)
            b["resolved_url"] = url

    # --- Addendum Part 4A: tier rule + no-repeat + decision log ---
    # entity/data/evidence needs must show the REAL thing (own_media = T1). If the only
    # asset is generic stock (or nothing), a text card BEATS a wrong clip — convert it.
    # action/concept may keep stock. Also dedupe a repeated asset within ~15s (450f).
    _BROLL_REPEAT_FRAMES = 450
    _LITERAL_NEEDS = ("entity", "data", "evidence")
    kept: list[dict] = []
    fallback_cards: list[dict] = []
    log: list[dict] = []
    seen_urls: list[tuple[str, int]] = []
    for b in broll:
        need = b.get("need", "action")
        is_own = b.get("source") == "own_media"
        resolved = bool(b.get("resolved_url"))
        cue = b.get("cue_text", "")
        s_in, s_out = int(b.get("src_in", 0)), int(b.get("src_out", 0))
        txt = (b.get("fallback_text") or cue or "").strip()
        # LITERAL need (entity/data/evidence) without the real asset → text card, never
        # generic stock. This is the only case that overrides v1: action/concept and any
        # legacy item with no `need` field keep v1 behavior (kept, resolved or not).
        if need in _LITERAL_NEEDS and not is_own:
            if txt and s_out > s_in:
                fallback_cards.append({"src_in": s_in, "src_out": s_out, "text": txt})
                log.append({"need": need, "cue": cue, "tier": "stock" if resolved else "none",
                            "action": "text_card", "why": "literal need with no real asset"})
            else:
                log.append({"need": need, "cue": cue, "tier": "none", "action": "dropped",
                            "why": "no asset and no fallback text"})
            continue
        # everything else keeps v1 behavior; resolved assets dedupe a repeat within 15s
        u = b.get("resolved_url") or b.get("asset_id") or ""
        if u and any(u == su and abs(s_in - sf) < _BROLL_REPEAT_FRAMES for su, sf in seen_urls):
            log.append({"need": need, "cue": cue, "tier": "own_media" if is_own else "stock",
                        "action": "skipped_repeat"})
            continue
        if u:
            seen_urls.append((u, s_in))
        kept.append(b)
        log.append({"need": need, "cue": cue, "tier": "own_media" if is_own else "stock",
                    "action": "broll"})

    edl["broll"] = kept
    if fallback_cards:
        overlays = edl.get("overlays") or []
        for c in fallback_cards:
            overlays.append({"type": "text_card", "src_in": c["src_in"], "src_out": c["src_out"],
                             "scale": 1.0, "text": str(c["text"])[:200]})
        edl["overlays"] = overlays
    edl["_broll_log"] = log
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
    # Addendum mode E: the creator's speaker_treatment config selects the source-primary
    # + speaker-PIP layout instead of the top/bottom split. Stamped on layout so
    # build_render_plan carries it and _submit_remotion_render picks Marque-SourcePip.
    cfg = job.get("config") or {}
    st = (cfg.get("speaker_treatment") or "").strip()
    if st in ("pip_circle", "pip_rounded_rect"):
        lay = dict(edl.get("layout") or {})
        lay["speaker_treatment"] = st
        pos = (cfg.get("pip_position") or "").strip()
        if pos in ("bottom_left", "bottom_right", "bottom_center"):
            lay["pip_position"] = pos
        edl["layout"] = lay
    return edl


# ---------------------------------------------------------------------------
# Phase 4: Learning loop routes
# ---------------------------------------------------------------------------

@app.post("/v1/posts/register")
async def register_post(req: PostRegisterRequest):
    """Register a scheduled post as a learning experiment."""
    if req.niche:
        _creator_niche[req.creator_id] = req.niche      # remember for cold-arm Beta seeding
        await _persist_creator(req.creator_id, niche=req.niche)
    if req.handle:
        # Palo port: opportunistically persist the creator's social handle so the metrics
        # poller (run_insights_cron) has an account to scrape. No-op if unset / no store.
        await _persist_creator(req.creator_id, handle=req.handle)
    live_mode = "live" if _supabase_client else "mock"
    if req.post_id in _post_registry:
        return {"mode": live_mode, "status": "already_registered", "post_id": req.post_id}
    # Registered on another instance / before a deploy? Check the DB before treating it
    # as new — a blind insert would merge-overwrite a possibly-SETTLED row back to
    # unsettled and re-open it for a second reward (audit A-03/A5).
    if _supabase_client:
        try:
            existing = await _supabase_client.load_post(req.post_id)
        except Exception as e:
            logging.warning("supabase load_post (register) failed: %s", e)
            existing = sp.UNAVAILABLE
        if existing and existing is not sp.UNAVAILABLE:
            _post_registry[req.post_id] = existing
            return {"mode": live_mode, "status": "already_registered", "post_id": req.post_id}
    # Whitelist the enumerated dimensions before they can become permanent bandit arms:
    # an off-taxonomy style/format/hook_signal (e.g. a client bug or a drifted LLM value)
    # would otherwise seed a junk arm that pollutes learning_block forever. Invalid values
    # are dropped to "" (that dim just doesn't update); pillar stays freeform by design.
    dropped = []
    style = req.style if req.style in STYLES else ""
    if req.style and not style:
        dropped.append("style")
    format_id = req.format_id if req.format_id in FORMAT_IDS else ""
    if req.format_id and not format_id:
        dropped.append("format_id")
    hook_signal = req.hook_signal if req.hook_signal in prompts.SIGNAL_LIST else ""
    if req.hook_signal and not hook_signal:
        dropped.append("hook_signal")
    if dropped:
        logging.warning("register_post dropped off-taxonomy dims %s for %s", dropped, req.post_id)
    # Omit the regressive outcome_y/metrics fields from the persisted row (settled
    # defaults FALSE in-schema); keep them in-memory so the settle path has its shape.
    post_data = {
        "creator_id": req.creator_id, "clip_id": req.clip_id, "permalink": req.permalink,
        "platform": req.platform, "scheduled_at": req.scheduled_at,
        "pillar": req.pillar, "style": style,
        "format_id": format_id, "hook_signal": hook_signal,
        "predicted_score": req.predicted_score,
        "settled": False,
    }
    _post_registry[req.post_id] = {**post_data, "outcome_y": None, "metrics": None}
    if _supabase_client:
        try:
            # ignore-duplicates: if a racing settle already created/settled the row,
            # this insert is a no-op rather than a clobber.
            if not await _supabase_client.upsert_post(req.post_id, post_data,
                                                      resolution="ignore-duplicates"):
                logging.warning("supabase upsert_post wrote nothing: %s", req.post_id)
        except Exception as e:
            logging.warning("supabase upsert_post failed: %s", e)
    resp = {"mode": live_mode, "status": "registered", "post_id": req.post_id}
    if dropped:
        resp["dropped"] = dropped
    return resp


@app.post("/v1/metrics/ingest")
async def ingest_metrics(req: MetricsIngestRequest):
    """Ingest post metrics and update the learning bandit (idempotent on post_id)."""
    entry = _post_registry.get(req.post_id)
    if entry is None and _supabase_client:                # registered on another instance?
        try:
            loaded = await _supabase_client.load_post(req.post_id)
        except Exception as e:                            # belt: the client shouldn't raise, but never guess
            logging.warning("supabase load_post failed: %s", e)
            loaded = sp.UNAVAILABLE
        if loaded is sp.UNAVAILABLE:
            # The DB couldn't answer — treating that as "unregistered" would silently
            # discard the creator's confirmed reward. Tell the client to retry instead.
            return {"mode": "live", "status": "retry_later", "post_id": req.post_id}
        if loaded:
            entry = loaded
            _post_registry[req.post_id] = entry
    if not entry:
        # A post the system never registered: update zero arms, write zero rows, and
        # say so — the old path fabricated a settled ghost row and answered "ingested"
        # while the reward vanished (audit A-01/A7).
        return {"mode": "live" if _supabase_client else "mock",
                "status": "unregistered", "post_id": req.post_id}
    if entry.get("settled"):
        return {"mode": "live" if _supabase_client else "mock", "status": "already_settled"}
    if req.reach < 20:
        return {"mode": "mock", "status": "below_min_reach", "reach": req.reach}

    m = req.model_dump()
    goal = req.goal if req.goal in ("grow", "authority", "clients", "monetize") else "grow"
    y = _compute_y(m, goal)                      # score on the creator's OWN objective, not always "grow"
    raw = _compute_raw(m, goal)                  # the un-squashed composite for honest lift
    creator_id = req.creator_id
    if req.niche:
        _creator_niche[creator_id] = req.niche
    niche = req.niche or _creator_niche.get(creator_id, "")

    # In-memory latch FIRST, synchronously, before any await: on a single instance
    # asyncio is cooperative, so no concurrent ingest can slip between the settled
    # check above and this set — the second coroutine sees settled=True and bails
    # before touching an arm. Restored below if we don't actually win the settle.
    entry["settled"] = True
    entry["settled_at"] = datetime.now(timezone.utc).isoformat()
    entry["outcome_y"] = y
    entry["outcome_raw"] = raw
    entry["metrics"] = m
    _post_registry[req.post_id] = entry

    # Cross-instance latch: one atomic conditional PATCH decides the single winner.
    # Arms are updated ONLY by the winner, so a retry or a second Render instance
    # holding a stale unsettled copy can never double-count the reward.
    if _supabase_client:
        try:
            won = await _supabase_client.settle_post_conditional(req.post_id, entry)
        except Exception as e:
            logging.warning("supabase settle_post_conditional failed: %s", e)
            won = sp.UNAVAILABLE
        if won is sp.UNAVAILABLE:                 # couldn't decide — restore + let client retry
            entry["settled"] = False
            entry["settled_at"] = None
            entry["outcome_y"] = None
            entry["outcome_raw"] = None
            entry["metrics"] = None
            _post_registry[req.post_id] = entry
            return {"mode": "live", "status": "retry_later", "post_id": req.post_id}
        if not won:                               # another instance already settled it
            return {"mode": "live", "status": "already_settled", "post_id": req.post_id}

    await _persist_creator(creator_id, niche=req.niche, goal=goal)   # durable niche/goal (A-10)
    _invalidate_creator_mean(creator_id)          # this settle shifts the personal baseline
    for dim in DIMENSIONS:
        val = entry.get(dim, "")
        if val:
            await _update_arm(creator_id, f"{dim}:{val}", y, raw, niche)

    # Attribute the just-settled post to ITS OWN driving dimension (not the creator's
    # globally strongest arm). Honest by construction — only driver/error bands it can
    # ground in the data, else "none".
    attribution = await _attribute_settled_post(creator_id, entry)

    return {"mode": "live" if _supabase_client else "mock", "status": "ingested",
            "outcome_y": round(y, 3), "goal": goal, "attribution": attribution,
            "post_id": req.post_id}


async def _settle_from_scrape(creator_id: str, rows: list[dict]) -> int:
    """Palo-port audit fix (bandit loop closure): polled metrics_ts rows settle their
    registered posts through the SAME /v1/metrics/ingest machinery — idempotent settled
    latch, cross-instance conditional PATCH, arm updates, attribution — instead of the
    dead settle_candidates bridge. Unregistered posts (organic, not published through
    the app) return status 'unregistered' and correctly update zero arms. Scraped
    sources carry no reach; views stands in (reach≈plays for reels), which keeps the
    engagement composite sane. Never raises."""
    latest: dict[str, dict[str, float]] = {}
    for r in rows:                                   # rows are captured_at-asc → last wins
        if r.get("entity_type") == "account":
            continue
        pid = str(r.get("entity_id") or "")
        metric = r.get("metric")
        if pid and metric in ("views", "likes", "comments"):
            latest.setdefault(pid, {})[metric] = float(r.get("value", 0))
    settled = 0
    for pid, m in latest.items():
        views = int(m.get("views", 0))
        if views < 20:                               # mirrors the ingest reach floor
            continue
        try:
            resp = await ingest_metrics(MetricsIngestRequest(
                post_id=pid, creator_id=creator_id, views=views,
                likes=int(m.get("likes", 0)), comments=int(m.get("comments", 0)),
                reach=views))
        except Exception as e:
            logging.warning("[settle] scrape settle failed for %s: %s", pid, e)
            continue
        if isinstance(resp, dict) and resp.get("status") == "ingested":
            settled += 1
    if settled:
        logging.info("[settle] %s: %d registered posts settled from scraped metrics",
                     creator_id, settled)
    return settled


def _cold_recommendations(niche: str) -> list[dict]:
    """Cold-start recommendations from the niche prior (before any own arm data).
    Pairs the niche's strongest styles with sensible starter pillars and an honest
    'niche baseline, refines as you post' reason. Beats the old static mock, which
    was niche-blind."""
    p = prompts.niche_priors_for(niche)
    styles = (p["styles"] + ["talking_head", "green_screen"])
    fmts = p["formats"]
    sigs = p["signals"]
    niche_label = niche.strip() or "your niche"
    pillars = ["Myth-bust the common advice", "Teach one specific thing well", "Contrarian take on a hot topic"]
    _sig_word = {"patternInterrupt": "pattern-interrupt", "callOut": "call-out"}
    arms = []
    for i in range(3):
        sig = _sig_word.get(sigs[i % len(sigs)], sigs[i % len(sigs)])
        arms.append({
            "pillar": pillars[i],
            "style": styles[i % len(styles)],
            "reason": (f"{sig} hooks + {fmts[i % len(fmts)]} tend to over-index in {niche_label} "
                       "(niche baseline — refines to your own data as you post)"),
        })
    return arms


async def _top_arms(creator_id: str, niche: str = "") -> list[dict]:
    """UX-G1: the top Thompson-sampled (pillar, style) arms with their HUMAN reason —
    factored from get_recommendations so the feed + next-idea consume the same source
    of judgment instead of rotating templates. Cold start (no arm data) falls back to
    the honest niche-prior recommendations."""
    await _ensure_arms_loaded(creator_id)
    if niche:
        _creator_niche[creator_id] = niche              # remember for cold-arm Beta seeding
    stats = _arm_stats.get(creator_id, {})
    if not stats:
        return _cold_recommendations(niche)

    mean_raw = _creator_mean_raw(creator_id)
    styles = list(prompts.ACTIVE_STYLES)     # only recommend styles the app actually offers
    pillars = list(set(
        k.split(":", 1)[1] for k in stats if k.startswith("pillar:")
    )) or ["Myth-busting", "Teach the fundamentals", "Hot takes"]

    sampled_styles = _thompson_sample(creator_id, [f"style:{s}" for s in styles], niche)
    sampled_pillars = _thompson_sample(creator_id, [f"pillar:{p}" for p in pillars], niche)

    arms = []
    for i in range(min(3, len(sampled_pillars))):
        pillar_key, pillar_score = sampled_pillars[i]
        style_key, style_score = sampled_styles[i % len(sampled_styles)]
        pillar = pillar_key.replace("pillar:", "")
        style = style_key.replace("style:", "")
        style_stats = stats.get(style_key, {})
        lift, grounded = _arm_lift(style_stats, mean_raw)
        conf = style_stats.get("confidence", "early read")
        if grounded and abs(lift) >= 5:
            verb = "outperforms" if lift > 0 else "underperforms"
            reason = f"{style.replace('_', ' ').title()} {verb} your average by {abs(lift)}% ({conf})"
        else:
            reason = f"{style.replace('_', ' ').title()} — exploring where your data is still thin"
        arms.append({"pillar": pillar, "style": style, "score": round(pillar_score + style_score, 3),
                     "reason": reason})

    return arms


@app.get("/v1/recommendations")
async def get_recommendations(niche: str = "", creator_id: str = "default"):
    """Return top 3 Thompson-sampled arms for the creator's home feed."""
    arms = await _top_arms(creator_id, niche)
    if not _arm_stats.get(creator_id):
        return {"mode": "mock", "arms": arms}
    return {"mode": "live" if _supabase_client else "mock", "arms": arms}


@app.get("/v1/insights/learned")
async def get_learned_insights(creator_id: str = "default"):
    """Return the creator's winning formula derived from arm_stats."""
    await _ensure_arms_loaded(creator_id)
    stats = _arm_stats.get(creator_id, {})
    if not stats:
        return {"mode": "mock", "insights": [], "posts_learned": 0,
                "winning_formula": None, "learning_progress": 0}

    total_posts = max(s.get("n", 0) for s in stats.values()) if stats else 0
    mean_raw = _creator_mean_raw(creator_id)

    # Rank by grounded lift magnitude; arms without a raw baseline make no claim.
    scored = []
    for k, v in stats.items():
        if v.get("confidence") not in ("confirmed", "early_read") or ":" not in k:
            continue
        lift, grounded = _arm_lift(v, mean_raw)
        if grounded:
            scored.append((k, v, lift))
    # P-06: magnitude, not raw value — a -60% error arm is a bigger insight than a
    # +10% winner, and sorting by raw lift buried every error below weak positives.
    scored.sort(key=lambda x: abs(x[2]), reverse=True)

    insights = []
    for k, v, lift in scored[:5]:
        dim, val = k.split(":", 1)
        if abs(lift) >= 5:
            verb = "+" if lift > 0 else ""
            insights.append({
                "dimension": dim, "value": val,
                "lift_pct": lift, "n_posts": v.get("n", 0),
                "confidence": v.get("confidence", "early_read"),
                "band": prompts.classify_arm_lift(lift),
                "label": f"{val.replace('_', ' ').title()}: {verb}{lift}% vs your average",
            })

    winning = None
    positives = [(k, v, lift) for k, v, lift in scored if lift > 0]
    if positives:
        k, v, lift = positives[0]
        val = k.split(":", 1)[1]
        winning = f"{val.replace('_', ' ').title()} content outperforms your average by {lift}%"

    target = 15
    return {"mode": "live" if _supabase_client else "mock",
            "insights": insights, "posts_learned": total_posts,
            "winning_formula": winning, "learning_progress": min(1.0, total_posts / target)}


# ---------------------------------------------------------------------------
# Conversation engine — the voice bubble + chat brain (client-held memory)
# ---------------------------------------------------------------------------

_VALID_MEMORY_OPS = {"add", "remove", "set"}
_VALID_MEMORY_FIELDS = set(prompts.MEMORY_FIELDS) | {"angle"}
_VALID_INTENTS = {"none", "generate_scripts", "day_plan", "save_idea", "update_brand_angle", "edit_video"}


def _sanitize_memory_updates(raw, limit: int = 6) -> list[dict]:
    """Keep only well-formed ops so a sloppy envelope can't corrupt client memory."""
    out = []
    for u in (raw or [])[:limit]:
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

    # W5: the creator attached video clips this turn and asked to edit them.
    if (req.attachments and any(k in low for k in ("edit", "stitch", "cut", "trim", "combine", "clip"))):
        intent = "edit_video"
        intent_args = {"instructions": last}
        reply = ("On it — I'll stitch your clips, cut the dead air, and tighten the pacing per your notes. "
                 "You'll see it building in your Library.")
        chips = ["Add captions", "Make it punchier", "Show me when it's done"]
    elif any(k in low for k in ("build my day", "build my content", "plan my day", "day plan", "build out my day")):
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
        reply = ("Locked that sharper lane into your brand memory — everything I write from here leans that way.")
        chips = ["Write the flag-planting script", "What does this change?", "Build my day"]
    elif any(k in low for k in ("idea", "thinking about", "what if i", "i want to make")):
        intent = "save_idea"
        updates.append({"op": "add", "field": "ideas", "value": last[:280]})
        reply = ("Saved to your idea bank. The specific version of that idea beats the general one — "
                 "sharpen it to one concrete moment and it films itself.")
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
        reply = ("Noted. The more you tell me like this, the sharper your scripts get — "
                 "the details you just gave me are exactly what makes a post sound like you.")

    if voice:
        reply = reply.split("\n")[0]
    reply = _apply_persona_voice(reply, req.persona, req.response_length)
    return {"reply": reply, "memory_updates": updates, "intent": intent,
            "intent_args": intent_args, "chips": chips}


# Persona voice + response-length shaping for the MOCK path only (the live-Claude path
# gets this from converse_system's persona/length instructions instead). Keeps the
# deterministic reply logic above untouched — this just re-flavors the final string so
# the coach picker is visibly real even fully offline.
# B-4: short, non-greeting connectors (the de-filler rule forbids openers like "Let's GO"),
# tuned to the current personas — Strategist (calm/plan), Hype Coach (momentum), Straight
# Shooter (blunt). Mock-only flavor so the coach picker is visibly real offline.
_PERSONA_OPENERS = {
    "machine": [
        "Here's the play — ", "The move that matters: ", "",
    ],
    "closer": [
        "Big one — ", "This is the rep: ", "",
    ],
    "sergeant": [
        "Straight up — ", "No fluff: ", "",
    ],
}


def _apply_persona_voice(reply: str, persona: str, length: str) -> str:
    import random
    openers = _PERSONA_OPENERS.get(persona)
    if openers:
        reply = random.choice(openers) + reply[0].lower() + reply[1:]
    if length == "concise":
        reply = reply.split(". ")[0].rstrip(".") + "."
    # B-4: no trailing "want me to go deeper" append — the chips already carry follow-ups,
    # and a mandatory offer reads as filler.
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
        # B3: spread the FULL brand (voice/catchphrases/non_negotiables/what_you_do/
        # emulation_targets/...) instead of hand-picking 7 fields — this previously
        # silently dropped whatever Brand fields got added later. + real posts, so a
        # script requested from the orb also gets verbatim voice exemplars.
        posts = await _creator_posts(req.creator_id)
        sreq = ScriptRequest(
            **_brand_only(req.brand),
            pillar=topic, pillar_summary=f"A one-off script request from conversation: {topic}",
            pillar_angle=angle, style=style, count=count, creator_id=req.creator_id,
            memory=req.memory or {},          # carry chat-learned memory into generation
            posts=posts,
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
        elif out["intent"] == "edit_video":
            out["payload"] = {"edit_instructions": out.get("intent_args", {}).get("instructions", "")}
        return {"mode": "mock", "reply": out["reply"], "memory_updates": out["memory_updates"],
                "intent": out["intent"], "payload": out.get("payload"), "suggested_chips": out["chips"]}

    stats = await _arms_for_prompt(req.creator_id)
    system = prompts.converse_system(req.mode, persona=req.persona, response_length=req.response_length)
    # Palo port (flag MEMORY_V2, default OFF): inject compounding memory + the never-
    # re-pitch ledger into the strategist's system prompt. Defined unconditionally so the
    # write-side hooks below can reuse it; zero added work/latency when the flag is off.
    _last_user = next((m.get("content", "") for m in reversed(req.messages)
                       if m.get("role") == "user"), "")
    if palo_flags.enabled(palo_flags.MEMORY_V2):
        try:                                 # defense-in-depth: never 500 converse on a store hiccup
            _mem_block = memory_v2.memory_block(
                await memory_v2.retrieve(_palo_store, req.creator_id, _last_user))
            _led_block = await recall_ledger.ledger_block(_palo_store, req.creator_id)
            _inject = "\n\n".join(b for b in (_mem_block, _led_block) if b)
            if _inject:
                system = f"{system}\n\n{_inject}"
        except Exception as e:
            logging.warning("[converse] memory/ledger injection failed: %s", e)
    # Audit fix: converse was the ONLY brain surface without the exemplar bank (scripts/
    # mimic/hooks all get it via _inject_brain) — the strategist should cite the creator's
    # own proven patterns too.
    if palo_flags.enabled(palo_flags.EXEMPLAR_BANK) and palo_flags.real_creator(req.creator_id):
        try:
            _ex_block = await exemplar.exemplar_block(_palo_store, req.creator_id)
            if _ex_block:
                system = f"{system}\n\n{_ex_block}"
        except Exception as e:
            logging.warning("[converse] exemplar injection failed: %s", e)
    system = await _inject_strategy(system, req.creator_id)   # Palo port: brain shapes converse
    # No trends passed: mock_trends is hand-authored filler, and injecting it as
    # "Trending right now" into a LIVE strategist makes the model relay invented trend
    # claims as fact. Omit until a real trend source exists (audit B-10/F16).
    user = prompts.converse_user(req.brand, req.memory, req.messages, arm_stats=stats,
                                 attachments=req.attachments or None)
    envelope = None
    try:
        # OPT-5: chat runs SONNET by default — voice mode already trusted it with the
        # SAME envelope schema + intent contract, and chat is the chattiest endpoint
        # (~40% price cut + lower latency per turn). CONVERSE_MODEL=opus restores the
        # old behavior instantly via env (no deploy-coupled code change).
        _converse_pick = os.environ.get("CONVERSE_MODEL", "sonnet").lower()
        model = OPUS if (_converse_pick == "opus" and req.mode != "voice") else SONNET
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
    elif intent == "edit_video":
        # W5: the app owns the upload + edit; the backend just relays the instructions.
        payload = {"edit_instructions": (intent_args.get("instructions") or "").strip()}

    chips = [c for c in (envelope.get("chips") or []) if isinstance(c, str) and c.strip()][:3]
    # Palo port (flag MEMORY_V2, default OFF): fire-and-forget learn from this turn —
    # extract stable memories + record what was proposed (never re-pitch). Off the hot path.
    if palo_flags.enabled(palo_flags.MEMORY_V2):
        _spawn(memory_v2.remember(_palo_store, req.creator_id, _last_user, envelope["reply"]))
        _spawn(recall_ledger.record(_palo_store, req.creator_id, _last_user, envelope["reply"]))
    return {"mode": "live", "reply": envelope["reply"],
            "memory_updates": _sanitize_memory_updates(envelope.get("memory_updates")),
            "intent": intent, "payload": payload, "suggested_chips": chips}


@app.post("/v1/memory/distill")
async def memory_distill(req: MemoryDistillRequest):
    """B-8: re-read a whole voice-session transcript and pull durable memory the per-turn
    extraction missed. Keyless or a too-short session → no-op (empty). Additive: iOS treats
    a 404/empty response as "nothing to add"."""
    user_turns = [m for m in req.transcript if (m.get("role") or "user") == "user"
                  and (m.get("text") or m.get("content"))]
    if not ANTHROPIC_KEY or len(user_turns) < 4:
        return {"mode": "mock", "memory_updates": []}
    try:
        sys_p, usr_p = prompts.memory_distill_prompt(req.transcript, req.memory, req.brand)
        out = await anthropic_json(
            sys_p, usr_p, _array_schema("memory_updates", prompts.MEMORY_UPDATE_ELEMENT),
            HAIKU, 900, array_key="memory_updates")
    except HTTPException:
        out = None
    return {"mode": "live", "memory_updates": _sanitize_memory_updates(out, limit=10)}


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
    except httpx.HTTPError as e:                  # transport base, incl. mid-stream ReadError
        logging.warning("tts: network error %s", e)
    return JSONResponse({"mode": "mock"})


# ---------------------------------------------------------------------------
# Auth (light) — derive creator_id from an optional bearer token.
# Real enforcement lands with Supabase RLS; for now the sub claim just scopes state.
# ---------------------------------------------------------------------------

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


# UX-A1: which EDIT TREATMENT (prompts.EDIT_FORMATS key) a real post most resembles.
# Human "why this matches" copy per treatment — shown on the mimic cards.
_WHY_MATCH = {
    "talking_head": "Straight to camera — the take itself carries it.",
    "talking_head_broll": "Spoken take with cutaways landing on the visual words.",
    "recap_music": "Montage on a track — barely any talking, hard cuts.",
    "recap_voiceover": "A voice narrates over the footage — no face on screen.",
}


def _classify_edit_format(post: dict) -> tuple[str, str]:
    """UX-A1 tier-1 heuristic: classify a scraped post into an edit treatment
    (EDIT_FORMATS key, engine style) from caption/hashtags/duration/transcript/views.
    Free + deterministic; the dossier tier-2 pass upgrades the top reels by actually
    watching them and overrides this."""
    cap = (post.get("caption") or "").strip()
    low = cap.lower()
    transcript = (post.get("transcript") or "").strip()
    dur = float(post.get("duration_s") or 0)
    words = len(transcript.split())
    musicish = any(t in low for t in ("#montage", "#edit", "#recap", "#aesthetic", "#asmr",
                                      "sound on", "🎵", "#transition", "#fyp"))
    # Short-or-no speech + short duration + music-ish caption → music recap.
    if (words < 12 and dur and dur <= 20) or (musicish and words < 25):
        return "recap_music", "fast_cuts"
    # Existing faceless signal (media with a near-empty caption) + real narration.
    if dur and len(cap) < 15 and words >= 12:
        return "recap_voiceover", "faceless"
    # Spoken take + a visual-noun-dense caption → talking head with b-roll cutaways.
    visual_nouns = ("desk", "setup", "gym", "kitchen", "recipe", "routine", "travel",
                    "room", "car", "studio", "office", "screen", "tutorial",
                    "how i", "how to", "day in", "before", "after")
    if words >= 25 and any(t in low for t in visual_nouns):
        return "talking_head_broll", "broll_cutaway"
    return "talking_head", "talking_head"


_REEL_CLASSIFY_TOP_K = int(os.environ.get("REEL_CLASSIFY_TOP_K", "8"))

# "Steal these" exists so a creator can EMULATE the reel — a person talking to camera
# with real spoken words is a script they can steal; a music montage isn't. Rank the
# edit treatments by emulatability; sorts below are STABLE so views/recency order is
# preserved within each tier.
_EDIT_FORMAT_RANK = {"talking_head": 0, "talking_head_broll": 1,
                     "recap_voiceover": 2, "recap_music": 3}


def _emulatable_rank(reel: dict) -> tuple[int, int, int]:
    """Sort key for a SERVED reel dict: talking-head first; then durable (Supabase-hosted)
    video before raw CDN — a durable URL keeps playing where a CDN link 403s (static-card
    bug); then a real spoken transcript (words the creator can mimic) first."""
    tier = _EDIT_FORMAT_RANK.get(reel.get("edit_format") or "", 1)
    _sb = SUPABASE_URL.rstrip("/") if SUPABASE_URL else "\x00"
    durable = 0 if (reel.get("video_url") or "").startswith(_sb) else 1
    return (tier, durable, 0 if reel.get("transcribed") else 1)


# The app edits talking-head content; a montage / faceless-voiceover reel can't be
# mimicked into a talking-head cut, so it has no business in the "steal these" surfaces.
_TALKING_HEAD_FORMATS = {"talking_head", "talking_head_broll"}


def _is_talking_head_reel(reel: dict) -> bool:
    """HARD filter for recommended/mimic reels: keep only a person-talking-to-camera reel
    (with or without b-roll cutaways). A served reel usually carries `edit_format`; when
    it doesn't, fall back to the free heuristic. Excludes recap_music/recap_voiceover and
    anything the heuristic reads as a montage."""
    ef = reel.get("edit_format") or ""
    if not ef:
        ef = _classify_edit_format(reel)[0]
    return ef in _TALKING_HEAD_FORMATS


def _talking_head_first(posts: list[dict]) -> None:
    """Stable-sort scraped POSTS talking-head-first (heuristic or carried-forward dossier
    classification) so the per-cycle enrichment budgets — transcription top-N, rehost
    top-N, dossier watch top-K — are spent on reels a creator can emulate, not montages."""
    posts.sort(key=lambda p: _EDIT_FORMAT_RANK.get(
        p.get("edit_format") or _classify_edit_format(p)[0], 1))


def _classify_from_dossier(dossier: dict | None, post: dict) -> tuple[str, str] | None:
    """UX-A1 tier 2: classify by WATCHING (the dossier adapter's measured signals).
    None → keep the tier-1 heuristic."""
    if not dossier:
        return None
    pat = dossier_mod.reference_patterns(dossier, int(float(post.get("duration_s") or 0) * 1000))
    if not pat:
        return None
    words = len((post.get("transcript") or "").split())
    eye_contact = bool((dossier.get("framing") or {}).get("eye_contact"))
    if not eye_contact:
        # no face → voiceover recap when narrated, music recap when not
        return ("recap_voiceover", "faceless") if words >= 12 else ("recap_music", "fast_cuts")
    if pat.get("cut_density_per_s", 0) >= 0.8 and words < 25:
        return "recap_music", "fast_cuts"
    if len(dossier.get("broll_visual_opportunities") or []) >= 2 or pat.get("cuts", 0) >= 4:
        return "talking_head_broll", "broll_cutaway"
    return "talking_head", "talking_head"


# Circuit breaker for the dossier providers (blank-home audit): TwelveLabs +
# claude_frames were 400ing on EVERY reel — a retry storm of wasted CPU/network on the
# instance while it was already health-check starved. After N consecutive provider
# failures, stop attempting for the rest of this process's life (heuristic
# classification still runs; a deploy/restart re-arms the breaker).
_DOSSIER_BREAKER_LIMIT = int(os.environ.get("DOSSIER_BREAKER_LIMIT", "3"))
_dossier_breaker = {"fails": 0, "open": False}


async def _dossier_classify_reels(posts: list[dict]) -> None:
    """UX-A1 tier 2: run the dossier adapter over the top-K engagement-sorted reels
    that lack a dossier classification. Fully fail-soft (keyless/off → no-op); results
    are set on the POST dicts so _reel_from_post + the carry-forward persist them —
    the watch cost is paid once per reel, ever."""
    if (dossier_mod.VIDEO_UNDERSTANDING or "off").lower() == "off" or _dossier_breaker["open"]:
        return
    sb_base = SUPABASE_URL.rstrip("/") if SUPABASE_URL else None
    for p in posts[:_REEL_CLASSIFY_TOP_K]:
        vurl = p.get("video_url") or ""
        if p.get("fmt_source") == "dossier" or not vurl:
            continue
        # Only attempt on a DURABLE (rehosted-to-Supabase) URL — an expiring IG/TikTok CDN
        # link makes ffmpeg's keyframe fetch fail, which is a per-reel data miss, NOT a
        # provider error. Skipping it (rather than counting it as a breaker failure) is why
        # the breaker was opening on every restart before any real classification ran.
        if sb_base and not vurl.startswith(sb_base):
            continue
        try:
            d = await dossier_mod.dossier_for_reference(
                vurl, int(float(p.get("duration_s") or 0) * 1000))
            # A None here is a fail-soft frame/parse miss for THIS reel, not a provider
            # outage — just skip it. Only a raised exception (a real provider error, the
            # 400-storm case the breaker exists for) counts.
            _dossier_breaker["fails"] = 0
            fs = _classify_from_dossier(d, p)
            if fs:
                p["edit_format"], p["fmt_source"] = fs[0], "dossier"
                p["why_match"] = f"Watched it: {_WHY_MATCH[fs[0]].lower()}"
        except Exception as e:
            _dossier_breaker["fails"] += 1
            logging.warning("dossier reel classify failed: %s", e)
            if _dossier_breaker["fails"] >= _DOSSIER_BREAKER_LIMIT:
                _dossier_breaker["open"] = True
                logging.warning("[dossier] circuit OPEN after %d consecutive PROVIDER errors — "
                                "reel classification disabled until restart", _dossier_breaker["fails"])
                return


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


def _reel_public_id(post: dict, handle: str, platform: str, idx: int) -> str:
    """The stable public id a post maps to in _reel_from_post — extracted so the
    refresh cycle can match a re-scraped post against its previous cache entry
    (to carry forward transcripts + re-hosted media)."""
    cap = (post.get("caption") or "").strip()
    seed = post.get("posted_at") or cap or str(idx)
    sid = hashlib.sha1(f"{platform}:{handle}:{seed}".encode()).hexdigest()[:10]
    return f"real-{platform}-{handle}-{sid}"


def _reel_from_post(post: dict, handle: str, platform: str, idx: int, watched: bool) -> dict:
    """Map a normalized Apify post → the ReelItem shape iOS renders. Stable id
    (hash of platform+handle+timestamp) so FeedStore dedupes across pages."""
    ann = _heuristic_reel_annotation(post)
    cap = (post.get("caption") or "").strip()
    first_line = cap.split("\n")[0].strip()
    title = re.sub(r"(#\w+\s*)+$", "", first_line).strip()[:80] or f"@{handle} — {ann['format_id'].replace('-', ' ')}"
    return {
        "id": _reel_public_id(post, handle, platform, idx),
        "creator_handle": (post.get("author") or handle).lstrip("@"),
        "platform": platform,
        "title": title,
        "hook_text": ann["hook_text"] or title,
        "transcript": post.get("transcript") or cap,
        # True only when a REAL spoken transcript exists (vs the caption fallback
        # above) — lets the next refresh cycle know this work is done and carry it.
        "transcribed": bool(post.get("transcript")),
        "thumbnail_url": post.get("thumbnail_url") or "",
        "video_url": post.get("video_url") or "",
        "views": int(post.get("views", 0) or 0),
        "likes": int(post.get("likes", 0) or 0),
        "why_trending": ann["why_trending"],
        "format_id": ann["format_id"],
        "style": ann["style"],
        "from_watched": watched,
        # UX-A1 (additive): the edit TREATMENT this reel matches. Tier-2 dossier
        # results (set on the post dict by _dossier_classify_reels / carried forward
        # by _merge_prev_reel_work) win over the tier-1 heuristic.
        "edit_format": post.get("edit_format") or _classify_edit_format(post)[0],
        "fmt_source": post.get("fmt_source") or "heuristic",
        "why_match": post.get("why_match")
                     or _WHY_MATCH.get(post.get("edit_format") or _classify_edit_format(post)[0], ""),
    }


async def _refresh_watched_creator(platform: str, handle: str) -> None:
    """Background: scrape one watched creator's top posts → real reels in cache.
    Mirrored to Supabase (survives deploys); previous transcripts carried forward."""
    key = f"{platform}:{handle}"
    try:
        posts = await scrape_posts(handle, platform, limit=8)
        if not posts:
            return
        posts.sort(key=lambda p: (p.get("views", 0), p.get("likes", 0)), reverse=True)
        prev = await _prev_reels_entry(_watched_reels_cache, key)
        _merge_prev_reel_work(posts, (prev or {}).get("reels") or [], handle=handle)
        _talking_head_first(posts)          # emulatable reels get the enrichment budget
        posts = await _transcribe_top_posts(posts, top_n=2)
        await _dossier_classify_reels(posts[:6])      # UX-A1 tier 2 (fail-soft)
        reels = [_reel_from_post(p, handle, platform, i, True) for i, p in enumerate(posts[:6])]
        entry = {"reels": reels, "ts": time.time()}
        _watched_reels_cache[key] = entry
        _cap_evict(_watched_reels_cache, _WATCHED_CACHE_CAP)
        if _supabase_client:
            await _supabase_client.upsert_reels_cache(key, entry)
    except Exception as e:
        logging.warning("watched-reel refresh failed for %s: %s", key, e)
    finally:
        _reels_refreshing.discard(key)


_REEL_TRANSCRIBE_TOP_N = int(os.environ.get("REEL_TRANSCRIBE_TOP_N", "4"))
_REEL_REHOST_TOP_N = int(os.environ.get("REEL_REHOST_TOP_N", "6"))


async def _rehost_media(url: str, key: str, content_type: str, max_bytes: int,
                        min_bytes: int = 0) -> str | None:
    """W2: download a scraped CDN asset and re-upload it to the PUBLIC Supabase bucket so the
    app can play it reliably (IG/TikTok CDN URLs 403/expire). Deterministic keys (overwrite,
    never accumulate). Returns the durable public URL, or None (keyless/unconfigured/oversize/
    undersize/any failure) — the caller then keeps the original CDN url and the client falls
    back. `min_bytes` rejects degenerate payloads (a CDN error page or a few-KB junk stream
    served with a 200) — persisting one as a durable .mp4 renders as a full-screen smear."""
    if not (SUPABASE_URL and SUPABASE_KEY and url and url.startswith("http")):
        return None
    base = SUPABASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            async with c.stream("GET", url) as r:
                if r.status_code != 200:
                    return None
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        return None
            if len(buf) < min_bytes:
                return None
            up = await c.post(
                f"{base}/storage/v1/object/{SUPABASE_STORAGE_BUCKET}/{key}",
                headers={"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY,
                         "Content-Type": content_type, "x-upsert": "true"},
                content=bytes(buf))
            if 200 <= up.status_code < 300:
                return f"{base}/storage/v1/object/public/{SUPABASE_STORAGE_BUCKET}/{key}"
    except Exception as e:
        logging.warning("rehost media failed for %s: %s", key[:40], e)
    return None


def _reel_storage_stem(post: dict) -> str:
    raw = f"{post.get('platform','ig')}:{post.get('author','x')}:{post.get('timestamp', post.get('id',''))}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _merge_prev_reel_work(posts: list[dict], prev_reels: list[dict], handle: str = "") -> None:
    """Carry forward the expensive per-reel work from a previous refresh cycle:
    real transcripts and re-hosted (durable Supabase) media URLs. Matched by the
    stable public reel id, applied in place BEFORE transcription/rehosting so
    those steps can skip what's already done — coverage accumulates across
    cycles instead of resetting. `handle` pins the id handle for the watched-
    creator path (which maps every post under one handle); the niche path leaves
    it empty and falls back to each post's author."""
    if not prev_reels:
        return
    prev_by_id = {r.get("id"): r for r in prev_reels if r.get("id")}
    sb_base = SUPABASE_URL.rstrip("/") if SUPABASE_URL else None
    for i, p in enumerate(posts):
        h = handle or (p.get("author") or "creator")
        old = prev_by_id.get(_reel_public_id(p, h, p.get("platform", "instagram"), i))
        if not old:
            continue
        if not p.get("transcript") and old.get("transcribed") and old.get("transcript"):
            p["transcript"] = old["transcript"]
        for field in ("video_url", "thumbnail_url"):
            u = old.get(field) or ""
            if sb_base and u.startswith(sb_base):     # durable URLs never expire — keep them
                p[field] = u
        # UX-A1: a dossier classification is paid-for work — carry it forward so the
        # watch cost is once per reel, ever (tier-1 heuristics recompute for free).
        if old.get("fmt_source") == "dossier":
            p["edit_format"] = old.get("edit_format")
            p["fmt_source"] = "dossier"
            p["why_match"] = old.get("why_match")


async def _prev_reels_entry(cache: dict, key: str) -> dict | None:
    """The previous cache entry for a key: in-memory first, else the durable
    Supabase copy (survives deploys)."""
    entry = cache.get(key)
    if entry:
        return entry
    if _supabase_client:
        try:
            return await _supabase_client.load_reels_cache(key)
        except Exception:
            return None
    return None


def _reels_from_posts(posts: list[dict]) -> list[dict]:
    return [_reel_from_post(p, p.get("author") or "creator",
                            p.get("platform", "instagram"), i, False)
            for i, p in enumerate(posts)]


async def _write_niche_reels(key: str, posts: list[dict], partial: bool) -> None:
    entry = {"reels": _reels_from_posts(posts), "ts": time.time(), "partial": partial}
    _niche_reels_cache[key] = entry
    _cap_evict(_niche_reels_cache, _NICHE_CACHE_CAP)
    if _supabase_client:                        # durability: survive the next deploy
        try:
            await _supabase_client.upsert_reels_cache(key, entry)
        except Exception as e:
            logging.warning("niche reels upsert failed for %s: %s", key, e)


async def _rehost_reel_media(posts: list[dict]) -> None:
    """B-10: re-host the top reels' video + all thumbnails to stable Supabase URLs, in
    PARALLEL with a small concurrency cap (was a serial loop — the long tail of the cold
    refresh). Per-post failures are isolated; the CDN URL is kept on failure."""
    sb_base = SUPABASE_URL.rstrip("/") if SUPABASE_URL else None
    def _durable(u: str) -> bool:
        return bool(sb_base and u and u.startswith(sb_base))
    sem = asyncio.Semaphore(int(os.environ.get("REEL_REHOST_CONCURRENCY", "4")))

    async def _one(i: int, p: dict) -> None:
        async with sem:
            stem = _reel_storage_stem(p)
            try:
                if p.get("thumbnail_url") and not _durable(p["thumbnail_url"]):
                    d = await _rehost_media(p["thumbnail_url"], f"reels/{stem}.jpg", "image/jpeg",
                                            2_000_000, min_bytes=2_000)
                    if d:
                        p["thumbnail_url"] = d
                if i < _REEL_REHOST_TOP_N and p.get("video_url") and not _durable(p["video_url"]):
                    # 25MB cap (was 60MB): _rehost_media buffers the whole payload in
                    # RAM, and 60MB x concurrency on a 512MB instance was an OOM /
                    # health-check-kill trigger (blank-home audit). A 25MB ceiling still
                    # covers virtually every sub-60s reel at mobile bitrates.
                    d = await _rehost_media(p["video_url"], f"reels/{stem}.mp4", "video/mp4",
                                            int(os.environ.get("REEL_REHOST_MAX_VIDEO_MB", "25")) * 1_000_000,
                                            min_bytes=100_000)
                    if d:
                        p["video_url"] = d
            except Exception as e:
                logging.warning("rehost worker failed (%s): %s", stem, e)

    await asyncio.gather(*(_one(i, p) for i, p in enumerate(posts)), return_exceptions=True)


async def _refresh_niche_reels(niche: str) -> None:
    """Background: scrape trending niche posts → real reels in cache. B-9: PROGRESSIVE serve
    — write the scraped+annotated reels (caption transcript + CDN URLs) to cache IMMEDIATELY
    (partial), so "Steal these" fills in ~30-60s instead of waiting 2-4 min for transcription
    + rehost; then enrich (real transcripts + durable Supabase media) and overwrite. Prior
    transcripts/durable URLs are carried forward so coverage accumulates across cycles."""
    key = _niche_cache_key(niche)
    try:
        posts = await scrape_niche_posts(niche, limit=20)
        posts = [p for p in posts if p.get("views", 0) >= 10_000]
        posts.sort(key=lambda p: (p.get("views", 0), p.get("likes", 0)), reverse=True)
        posts = posts[:18]
        # Carry forward transcripts + durable media from the previous cycle first,
        # so the enrichment below only runs for posts that still need it.
        prev = await _prev_reels_entry(_niche_reels_cache, key)
        _merge_prev_reel_work(posts, (prev or {}).get("reels") or [])
        # AFTER the carry-forward (so prior transcripts/classifications inform the tiers):
        # talking-head candidates first — they get the transcription/rehost/dossier budget.
        _talking_head_first(posts)

        # PHASE 1 — serve now. Renderable reels (caption fallback + CDN URLs).
        if posts:
            await _write_niche_reels(key, posts, partial=True)

        # PHASE 2 — enrich: real spoken transcript for the top reels, then durable media.
        posts = await _transcribe_top_posts(posts, top_n=_REEL_TRANSCRIBE_TOP_N)
        await _rehost_reel_media(posts)
        # UX-A1 tier 2: classify the top-K by WATCHING (dossier adapter, fail-soft).
        # After transcription so the transcript-length signal is real; before the final
        # write so the classification persists via the reels cache.
        await _dossier_classify_reels(posts)
        if posts:
            await _write_niche_reels(key, posts, partial=False)
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
            _spawn(_refresh_watched_creator(platform, handle))
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
        _spawn(_refresh_niche_reels(niche))
    return out


# ---------------------------------------------------------------------------
# W1: live niche trends — derived from the SAME scraped corpus the reels use (zero extra
# Apify spend). Heuristic format-clustering with computed view stats (honest numbers), plus
# an optional HAIKU naming pass. SWR-on-request; falls back to the rotated mock keyless.
# ---------------------------------------------------------------------------

_TREND_BUCKET_S = 6 * 3600
_niche_trends_cache: dict[str, dict] = {}     # key -> {"trends", "ts"}
_trends_refreshing: set[str] = set()
_NICHE_TRENDS_TTL_S = 24 * 3600

_TREND_TITLES = {
    "listicle": "Rapid-fire {n} listicles", "myth-buster": "Myth-busting {n} takes",
    "do-this-not-that": "“Do this, not that” {n} splits", "before-after": "{n} before/after receipts",
    "pov-story": "POV {n} stories", "broll-hook": "B-roll hook {n} explainers", "faceless": "Faceless {n} explainers",
}


def _trend_bucket() -> int:
    return int(time.time() // _TREND_BUCKET_S)


def _heuristic_niche_trends(niche: str, posts: list[dict]) -> list[dict]:
    """Cluster the scraped posts by format, rank by combined views — the 'why' is a REAL
    computed stat (honesty rule), never invented."""
    from collections import defaultdict
    agg: dict[str, dict] = defaultdict(lambda: {"count": 0, "views": 0})
    for p in posts:
        fmt = _heuristic_reel_annotation(p)["format_id"]
        agg[fmt]["count"] += 1
        agg[fmt]["views"] += p.get("views", 0) or (p.get("likes", 0) * 10)
    total = max(1, len(posts))
    ranked = sorted(agg.items(), key=lambda kv: kv[1]["views"], reverse=True)[:6]
    out = []
    for fmt, s in ranked:
        title = _TREND_TITLES.get(fmt, f"{fmt.replace('-', ' ').title()} {niche}").format(n=niche)
        why = (f"{s['count']} of the top {total} {niche} reels right now are {fmt.replace('-', ' ')} — "
               f"{_compact_count(s['views'])} combined views.")
        out.append({"title": title, "why": why, "formatId": fmt})
    return out


async def _refresh_niche_trends(niche: str) -> None:
    key = _niche_cache_key(niche)
    try:
        # Reuse the reels corpus if fresh, else a light scrape.
        reels_entry = _niche_reels_cache.get(key)
        posts = await scrape_niche_posts(niche, limit=20) if not reels_entry else \
            [{"caption": r.get("hook_text", ""), "views": r.get("views", 0), "likes": r.get("likes", 0),
              "transcript": r.get("transcript", "")} for r in reels_entry["reels"]]
        trends = _heuristic_niche_trends(niche, posts)
        if not trends:
            return
        if ANTHROPIC_KEY and AI_QUALITY:
            try:
                sysp, usr = prompts.niche_trends_prompt(niche, posts[:12])
                named = extract_json(await anthropic(sysp, usr, HAIKU, 800), array=True) or []
                clean = [{"title": str(t.get("title", ""))[:80], "why": str(t.get("why", ""))[:160],
                          "formatId": t.get("formatId") if t.get("formatId") in FORMAT_IDS else "pov-story"}
                         for t in named if isinstance(t, dict) and t.get("title")][:6]
                if clean:
                    trends = clean
            except HTTPException:
                pass
        _niche_trends_cache[key] = {"trends": trends, "ts": time.time()}
        _cap_evict(_niche_trends_cache, 128)
    except Exception as e:
        logging.warning("niche trends refresh failed for %s: %s", niche, e)
    finally:
        _trends_refreshing.discard(key)


def _niche_live_trends(niche: str) -> list[dict] | None:
    if not niche.strip():
        return None
    key = _niche_cache_key(niche)
    entry = _niche_trends_cache.get(key)
    out = list(entry["trends"]) if entry else None
    stale = not entry or (time.time() - entry["ts"]) > _NICHE_TRENDS_TTL_S
    if stale and APIFY_KEY and key not in _trends_refreshing:
        _trends_refreshing.add(key)
        _spawn(_refresh_niche_trends(niche))
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
        _spawn(_refresh_watched_creator(platform, handle))
    return {"ok": True, "mode": "live" if APIFY_KEY else "mock", "cached": cached}


async def _hydrate_reels_caches(niche: str, parsed: list[tuple[str, str]]) -> None:
    """Cold-miss hydration: after a deploy the in-memory caches are empty but the
    durable Supabase copies (with transcripts + re-hosted media) are not — load
    them so the first paint isn't an empty list. Preserves the stored ts so the
    normal staleness check still kicks a background re-scrape when due. Bounded
    to a few seconds; on any failure the SWR path behaves exactly as before."""
    if not _supabase_client:
        return
    wanted: list[tuple[dict, str]] = []
    if niche.strip():
        k = _niche_cache_key(niche)
        if k not in _niche_reels_cache:
            wanted.append((_niche_reels_cache, k))
    for platform, handle in parsed:
        k = f"{platform}:{handle}"
        if k not in _watched_reels_cache:
            wanted.append((_watched_reels_cache, k))
    if not wanted:
        return

    async def _one(cache: dict, k: str) -> None:
        entry = await _supabase_client.load_reels_cache(k)
        if entry and isinstance(entry.get("reels"), list) and entry["reels"]:
            cache[k] = {"reels": entry["reels"], "ts": float(entry.get("ts") or 0)}

    try:
        await asyncio.wait_for(
            asyncio.gather(*(_one(c, k) for c, k in wanted), return_exceptions=True),
            timeout=4.0)
    except asyncio.TimeoutError:
        pass


async def _warm_reels_on_boot() -> None:
    """B-11: after a deploy, proactively hydrate the niches real creators actually use
    (from the durable creators table, loaded by _load_learning_state) and kick a background
    re-scrape for any that are missing or stale — so the FIRST creator to open Home after a
    release doesn't hit a 2-4 min cold scrape. Non-blocking, best-effort, keyless no-op."""
    if not (_supabase_client and APIFY_KEY):
        return
    try:
        niches = [n for n in {v.strip() for v in _creator_niche.values() if v and v.strip()}][:8]
        for niche in niches:
            await _hydrate_reels_caches(niche, [])       # pull durable copy if present
            key = _niche_cache_key(niche)
            entry = _niche_reels_cache.get(key)
            stale = not entry or (time.time() - entry.get("ts", 0)) > _NICHE_REELS_TTL_S
            if stale and key not in _reels_refreshing:
                _reels_refreshing.add(key)
                _spawn(_refresh_niche_reels(niche))
    except Exception as e:
        logging.warning("boot reels warm failed: %s", e)


@app.get("/v1/reels")
async def reels(niche: str = "", creator_id: str = "default", watched: str = "", cursor: int = 0):
    cursor = max(0, min(cursor, 50))
    parsed = _parse_watched(watched)
    if APIFY_KEY:
        await _hydrate_reels_caches(niche, parsed)
        # Production: ONLY real reels. Watched creators' top posts first, then
        # niche-trending. No fabricated filler — an empty list (cold cache) is
        # honest; iOS shows a "finding real reels" state and pull-to-refresh fills.
        corpus, seen = [], set()
        for r in _watched_real_reels(parsed) + _niche_real_reels(niche):
            if r["id"] not in seen:
                seen.add(r["id"])
                corpus.append(r)
        # HARD talking-head filter: this app mimics talking-head content, so a montage or
        # faceless-voiceover reel can't be turned into a cut the creator could make —
        # exclude them entirely (not just rank them down). Then order the survivors
        # talking-head-with-transcript first. Keep a watched reel even if unclassifiable
        # (the creator explicitly follows them) so the "watched" row never empties.
        # PLAYABILITY: never serve a card that can only ever be a static thumbnail — a reel
        # with no video_url isn't "steal-able," it's just a picture. Require a video_url.
        corpus = [r for r in corpus
                  if r.get("video_url") and (_is_talking_head_reel(r) or r.get("from_watched"))]
        corpus.sort(key=_emulatable_rank)
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

_FEED_MAX_PAGES = 8      # B-5: more pages before exhaustion so "Load more" keeps producing

# B-7: per-creator dismissed-script fingerprints (content hash of title|hook), capped so a
# disliked pick doesn't reappear when a near-identical script regenerates. In-memory only —
# the durable signal is the bandit penalty; this is a serve-time cosmetic filter.
_feed_dismissed: dict[str, "collections.deque"] = {}
_feed_feedback_seen: dict[str, set] = {}       # per-creator fingerprints already scored (idempotency)
_FEED_DISMISS_CAP = 100


def _script_fingerprint(script: dict) -> str:
    return hashlib.sha1(
        (str(script.get("title", "")) + "|" + str(script.get("hook", ""))).lower().encode()
    ).hexdigest()[:16]


def _record_dismissal(creator_id: str, fingerprint: str) -> None:
    import collections
    dq = _feed_dismissed.get(creator_id)
    if dq is None:
        dq = collections.deque(maxlen=_FEED_DISMISS_CAP)
        _feed_dismissed[creator_id] = dq
    if fingerprint not in dq:
        dq.append(fingerprint)
_FEED_CACHE_TTL_S = 6 * 3600
# Mock pages exist only to bridge to the background upgrade — expire them fast so a
# repoll gets the upgraded page, but NOT so fast that immediate repolls re-run the
# generation gauntlet (blank-home audit).
_FEED_MOCK_TTL_S = int(os.environ.get("FEED_MOCK_TTL_S", "90"))
_FEED_CACHE_CAP = 512

_feed_cache: dict[str, dict] = {}       # key -> {"items", "next_cursor", "mode", "ts"}
_feed_refreshing: set[str] = set()      # keys with a background upgrade already in flight
_feed_inflight: dict[str, asyncio.Task] = {}   # single-flight: cold paints per cache key


def _feed_topics(niche: str, known_for: str, what_you_do: str, cursor: int) -> str:
    """B3 (B2 of the audit): cold-start topics were pure niche-string mad-libs, ignoring
    what the creator actually does / wants to be known for. Weave those in when present;
    fall back to the original niche-only templates only when both are empty."""
    if known_for or what_you_do:
        topics = [
            f"the {known_for or niche or 'creator'} take nobody in {niche or 'your niche'} is saying",
            f"what {what_you_do or 'what you do'} taught you that {niche or 'people'} get wrong",
            f"the {niche or 'your field'} mistake everyone makes — that {known_for or 'you'} would never",
            f"the fastest win in {niche or 'your field'} this month, from someone who does {what_you_do or 'this'}",
            f"what you'd tell someone starting {niche or 'out'} today, given {known_for or 'what you know'}",
        ]
    else:
        topics = [
            f"the {niche or 'creator'} mistake everyone makes",
            f"what nobody tells beginners about {niche or 'your field'}",
            f"a myth in {niche or 'your niche'} that needs to die",
            f"the fastest win in {niche or 'your field'} this month",
            f"what I'd do differently starting {niche or 'out'} today",
        ]
    return topics[cursor % len(topics)]


def _memory_digest(memory: dict | None) -> str:
    """B-6: a short stable hash of the creator memory so personalized pages don't collide
    with (or get served from) the non-personalized cache. Empty memory → "" so a GET and an
    empty-memory POST produce BYTE-IDENTICAL cache keys and share the same entry."""
    if not memory:
        return ""
    try:
        return hashlib.sha1(json.dumps(memory, sort_keys=True, default=str).encode()).hexdigest()[:12]
    except (TypeError, ValueError):
        return ""


# --- B3: creator profile (brand) + posts hydration -----------------------------
# The client OWNS the brand and posts NEVER live client-side at all — this is the
# durable server-side mirror everything that can't rely on a client payload reads from
# (GET /v1/feed, write-turn, the T3 quality cron). In-memory TTL caches over the Supabase
# read (same pattern as _watched_reels_cache) keep the hot feed path from adding a
# Supabase round-trip to every single request.
_CREATOR_PROFILE_TTL_S = 300
_creator_profile_cache: dict[str, tuple[dict, float]] = {}     # creator_id -> (brand, ts)
_last_persisted_brand_hash: dict[str, str] = {}                 # avoid a write per request
_CREATOR_POSTS_TTL_S = 3600
_creator_posts_cache: dict[str, tuple[list, float]] = {}        # creator_id -> (posts, ts)


async def _hydrate_creator_profile(creator_id: str) -> dict:
    """The stored brand snapshot for a creator, or {} if none / keyless. TTL-cached."""
    if not creator_id or not _supabase_client:
        return {}
    cached = _creator_profile_cache.get(creator_id)
    if cached and (time.time() - cached[1]) < _CREATOR_PROFILE_TTL_S:
        return cached[0]
    try:
        row = await _supabase_client.load_creator_profile(creator_id)
    except Exception:
        row = None
    brand = (row or {}).get("brand") or {}
    _creator_profile_cache[creator_id] = (brand, time.time())
    _cap_evict(_creator_profile_cache, _FEED_CACHE_CAP)
    return brand


async def _persist_creator_profile(creator_id: str, brand: dict) -> None:
    """Fire-and-forget: write the brand snapshot when it's actually changed (dedup via
    the in-memory last-hash map so an unchanged brand doesn't write every request)."""
    if not creator_id or not _supabase_client or not brand:
        return
    h = _brand_hash(brand)
    if not h or _last_persisted_brand_hash.get(creator_id) == h:
        return
    try:
        ok = await _supabase_client.upsert_creator_profile(creator_id, brand, h)
        if ok:
            _last_persisted_brand_hash[creator_id] = h
            _creator_profile_cache[creator_id] = (brand, time.time())   # keep the read cache warm
            # B3: a real brand change (not just the first-ever snapshot) may have left the
            # compiled strategy stale until the next weekly cron — check now, debounced.
            _spawn(strategy_compiler.maybe_recompile_on_brand_edit(_palo_store, creator_id, brand))
    except Exception as e:
        logging.warning("[creator_profile] persist failed for %s: %s", creator_id, e)
    _cap_evict(_last_persisted_brand_hash, _FEED_CACHE_CAP)


async def _creator_posts(creator_id: str) -> list[dict]:
    """Stored scraped posts for prompt grounding (_voice_exemplars). [] keyless/absent/
    on any failure — never raises. TTL-cached (1h — posts change slowly)."""
    if not creator_id or not _supabase_client:
        return []
    cached = _creator_posts_cache.get(creator_id)
    if cached and (time.time() - cached[1]) < _CREATOR_POSTS_TTL_S:
        return cached[0]
    try:
        posts = await _supabase_client.load_creator_posts(creator_id)
    except Exception:
        posts = None
    posts = posts or []
    _creator_posts_cache[creator_id] = (posts, time.time())
    _cap_evict(_creator_posts_cache, _FEED_CACHE_CAP)
    return posts


async def _persist_creator_posts(creator_id: str, posts: list[dict]) -> None:
    """Fire-and-forget. Caps at 10 posts, strips media URLs — this is prompt grounding
    (caption/transcript/hashtags/engagement), not a data lake."""
    if not creator_id or not _supabase_client or not posts:
        return
    trimmed = [
        {"caption": p.get("caption", ""), "transcript": p.get("transcript", ""),
         "hashtags": p.get("hashtags", []), "likes": p.get("likes", 0),
         "comments": p.get("comments", 0), "views": p.get("views", 0)}
        for p in posts[:10]
    ]
    try:
        ok = await _supabase_client.upsert_creator_posts(creator_id, trimmed)
        if ok:
            _creator_posts_cache[creator_id] = (trimmed, time.time())
    except Exception as e:
        logging.warning("[creator_posts] persist failed for %s: %s", creator_id, e)


def _feed_cache_key(creator_id: str, brand: dict, styles: str, watched: str, cursor: int,
                    memory: dict | None = None, posts_token: str = "") -> str:
    # Param-signature sub-key: a brand edit (niche/voice/catchphrases/...) or a fresh
    # posts scan invalidates the cached page instead of serving stale content for a
    # brand the creator just edited (previously only niche/audience/known_for/goal —
    # editing voice sliders or catchphrases silently kept serving the old page).
    sig = "|".join([_brand_hash(brand), styles, watched, str(cursor), posts_token])
    mem = _memory_digest(memory)
    return f"{creator_id}::{sig}" + (f"::m{mem}" if mem else "")


_SPEAKABLE_REPAIR_SYS = (
    "You rewrite a short-form video script BODY so it is the EXACT words the creator says "
    "out loud to camera — a verbatim spoken script, never a description of what to talk "
    "about. Remove every stage direction / instruction ('talk about', 'mention', 'explain "
    "that', 'Beat 1', 'show a chart', 'cut to') and replace it with the actual spoken line "
    "that would go there, in the creator's voice. Keep the meaning, the length, and any "
    "\\n\\n paragraph breaks. Return ONLY the rewritten body text, nothing else."
)


async def _ensure_speakable(scripts: list[dict], *, policy: str = "repair_or_drop",
                            fallback=None, timeout_s: float = 8.0) -> list[dict]:
    """Runtime speakability guard for EVERY script-generation path. NEVER returns a
    lint-dirty body: for any hit, one bounded HAIKU rewrite is attempted, then RE-LINTED
    (a repair that still reads as a description doesn't count). If the body is still
    dirty (repair failed, timed out, or keyless), `policy` decides what happens instead
    of silently shipping the description:
      repair_or_drop       -> the script is removed from the returned list (caller
                              backfills from a mock/template if the result is empty)
      repair_or_fallback   -> replaced by fallback(i) — a script guaranteed speakable
                              (a template mock; the caller supplies it)
      repair_or_keep_input -> same mechanics as repair_or_fallback — pass
                              fallback=lambda i: <the pre-edit input> as the safe floor
    fallback may also return None as a sentinel the CALLER interprets itself (used by
    the write-turn route to know "convert this action to an answer" rather than ship a
    reconstructed script dict). Every non-clean outcome is logged. Runs even keyless —
    the policy still applies (fail-CLOSED, not fail-open)."""
    out: list[dict] = []
    for i, s in enumerate(scripts):
        body = s.get("body") or ""
        style = s.get("style", "")
        reason = prompts.flag_stage_direction(body, style)
        if not reason:
            out.append(s)
            continue
        fixed = None
        if ANTHROPIC_KEY:
            try:
                fixed = await asyncio.wait_for(
                    anthropic(_SPEAKABLE_REPAIR_SYS, body, HAIKU, 600), timeout=timeout_s)
            except (HTTPException, asyncio.TimeoutError):
                fixed = None
            if fixed and prompts.flag_stage_direction(fixed, style):
                fixed = None   # the repair itself still reads as a description
        if fixed and fixed.strip():
            logging.info("[speakable] i=%d outcome=repaired reason=%s", i, reason)
            out.append({**s, "body": fixed.strip()})
            continue
        if policy == "repair_or_drop":
            logging.info("[speakable] i=%d outcome=dropped reason=%s", i, reason)
            continue
        if fallback is not None:
            logging.info("[speakable] i=%d outcome=fallback reason=%s", i, reason)
            out.append(fallback(i))
        else:
            logging.info("[speakable] i=%d outcome=dropped(no_fallback) reason=%s", i, reason)
    return out


async def _fast_feed_scripts(sreq: "ScriptRequest") -> dict:
    """First-paint script generation: ONE lean HAIKU call, no best-of-N hooks and no
    judge/repair pass. The blank-home audit measured the old SONNET + full-schema call
    at ~23-30s — past ANY budget, so the first paint was ALWAYS mock and every cold
    request held a worker slot for the full 22s (which is what starved the health
    checks). HAIKU + FAST_SCRIPT_JSON_ELEMENT lands in ~2-4s; the extras the lean
    schema drops are synthesized below so the wire shape is unchanged, and the
    background OPUS pass still upgrades to full quality."""
    if not ANTHROPIC_KEY:
        return {"mode": "mock", "scripts": mock_scripts(sreq)}
    pillar = {"name": sreq.pillar, "summary": sreq.pillar_summary,
              "angle": sreq.pillar_angle, "exampleTopics": sreq.example_topics}
    try:
        stats = await _arms_for_prompt(sreq.creator_id)   # data-rich creators get real learning,
        sys, usr = prompts.scripts_prompt(sreq.d(), pillar, sreq.style, sreq.count,
                                          sreq.media_context, sreq.posts or None,
                                          arm_stats=stats, memory=sreq.memory or None)
        # Palo brain on the cold paint too: a single compiled-strategy read (cheap) so the
        # first-ever picks reflect the creator's strategy — the full brain rides the
        # background OPUS upgrade. Best-effort; never blocks the paint.
        sys = await _inject_strategy(sys, sreq.creator_id)
        # Output tokens are the latency long pole (HAIKU ~130 tok/s: three full bodies
        # ≈ 1000+ tokens ≈ 8-10s — measured live blowing the budget). Tight first-paint
        # bodies keep generation ~4-6s; the background OPUS pass restores full depth.
        sys += ("\n\nFIRST-PAINT BUDGET: keep each script body under 80 words — a tight, "
                "specific, filmable draft. A fuller quality pass follows separately.")
        out = await asyncio.wait_for(
            anthropic_json(sys, usr, _array_schema("scripts", prompts.FAST_SCRIPT_JSON_ELEMENT),
                           HAIKU, 900, array_key="scripts"),
            timeout=float(os.environ.get("FEED_FAST_TIMEOUT_S", "10")),
        )
        if not out:
            return {"mode": "mock", "scripts": mock_scripts(sreq)}
        for s in out:
            # Synthesize the card extras the lean schema dropped — the client treats
            # them as optional-with-defaults, but keeping the shape identical to the
            # full pipeline means zero decode-path differences between paints.
            s.setdefault("altHooks", [])
            s.setdefault("shotPlan", [])
            s.setdefault("targetSeconds", 30)
            s["predictedScore"] = _draft_score(sreq.creator_id, s)
        # Pad a short array (HAIKU overshot the word cap → truncation dropped items, or
        # it just emitted fewer) up to count with mock fills, so the picks row is never
        # a lonely single card (script-quality audit — the schema has no minItems).
        if len(out) < sreq.count:
            out = out + mock_scripts(sreq)[len(out):sreq.count]
        # Speakability guard: the fast paint is unjudged, so a description-style body
        # (the "some scripts describe what to say" report) would otherwise ship. Repair
        # any that trip the lint into verbatim spoken copy; anything still dirty is
        # swapped for the matching mock card (instant, deterministic — no added latency).
        mocks = mock_scripts(sreq)
        out = await _ensure_speakable(
            out, policy="repair_or_fallback",
            fallback=lambda i: mocks[i % len(mocks)])
        # "live_fast" (NOT "live"): a real-AI DRAFT (lean HAIKU, ≤80-word bodies, no
        # judge pass). The client uses this to keep polling for the full Opus upgrade —
        # marking it plain "live" stranded users on the draft (script-quality audit).
        return {"mode": "live_fast", "scripts": out}
    except (HTTPException, asyncio.TimeoutError):
        return {"mode": "mock", "scripts": mock_scripts(sreq)}


def _clamp_title(title: str, limit: int = 42) -> str:
    """Display-safe pick-card title: ≤limit chars, cut at a word boundary, no
    dangling punctuation. The prompts ask for ≤6 words but LLM/cached/mock paths
    can all exceed it — this is the enforcement so the card never truncates
    mid-word with an ellipsis."""
    t = (title or "").strip()
    if len(t) <= limit:
        return t
    cut = t[:limit + 1]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,;:-–—(&/").rstrip()


async def _compose_feed_items(script_result: dict, niche: str, creator_id: str,
                              watched: str, cursor: int,
                              why_picked: str = "") -> tuple[list[dict], int | None]:
    """Shared item-composition body — the fast path, the cached path, and the
    background full-quality refresh all emit byte-identical FeedResp shapes.
    B-7: scripts whose fingerprint the creator dismissed are dropped here (serve-time)."""
    dismissed = _feed_dismissed.get(creator_id)
    items = []
    for s in script_result.get("scripts", [])[:3]:
        if isinstance(s, dict) and dismissed and _script_fingerprint(s) in dismissed:
            continue                          # creator disliked a near-identical pick
        if isinstance(s, dict) and s.get("title"):
            s = {**s, "title": _clamp_title(str(s["title"]))}
        if isinstance(s, dict) and why_picked and not s.get("why_picked"):
            s = {**s, "why_picked": why_picked}   # UX-G1: every pick says WHY it's here
        items.append({"type": "script", "script": s})

    # A reels failure (Apify scrape flake, cache hydration error) must not 500 the
    # whole feed — picks + trend still ship, the client just gets no reels this page.
    # Reels were the single point of failure for the entire response, which the app
    # surfaced as "couldn't load today's picks" even though picks were fine.
    try:
        reel_result = await reels(niche=niche, creator_id=creator_id, watched=watched, cursor=cursor)
    except Exception as e:
        logging.warning("feed reels failed (serving picks without them): %s", e)
        reel_result = {"reels": [], "next_cursor": None}
    items += [{"type": "reel", "reel": r} for r in reel_result.get("reels", [])[:4]]

    # W1: live niche trends when cached, else mock ROTATED by cursor + 6h bucket so a
    # given page's trend actually changes through the day.
    all_trends = _niche_live_trends(niche) or mock_trends(niche)
    items.append({"type": "trend", "trend": all_trends[(cursor + _trend_bucket()) % len(all_trends)]})

    reels_more = reel_result.get("next_cursor") is not None
    next_cursor = cursor + 1 if (cursor + 1 < _FEED_MAX_PAGES or reels_more) else None
    return items, next_cursor


def _feed_sreq(brand: dict, styles: str, cursor: int, creator_id: str, memory: dict | None,
               posts: list[dict] | None = None,
               arms: list[dict] | None = None) -> tuple["ScriptRequest", str]:
    """UX-G1 + B3: build the page's script request FROM the creator's top arms (pillar +
    style chosen by the bandit, with its human reason as why_picked) — template
    rotation only when the arms are exhausted/absent. B3: now carries the FULL brand
    (voice/catchphrases/non_negotiables/what_you_do/emulation_targets) + real posts —
    previously only niche/audience/known_for/goal reached the feed's script generation
    at all, so it had never seen how the creator actually talks. Returns (sreq, why_picked)."""
    niche = brand.get("niche", "")
    known_for = brand.get("known_for", "")
    what_you_do = brand.get("what_you_do", "")
    allowed = [s for s in styles.split(",") if s in STYLES] or list(STYLES.keys())
    arm = arms[cursor] if arms and cursor < len(arms) else None
    if arm and arm.get("pillar"):
        pillar = arm["pillar"]
        style = arm["style"] if arm.get("style") in allowed else allowed[cursor % len(allowed)]
        why_picked = arm.get("reason") or f"From your '{pillar}' pillar"
    else:
        pillar = _feed_topics(niche, known_for, what_you_do, cursor)
        style = allowed[cursor % len(allowed)]
        why_picked = f"From your '{pillar}' pillar"
    return ScriptRequest(
        **_brand_only(brand),
        pillar=pillar, pillar_summary="Daily feed suggestion",
        style=style, count=3, creator_id=creator_id,
        memory=memory or {},                  # B-6: picks personalized by yap-session memory
        posts=posts or [],                    # B3: real posts -> verbatim voice exemplars
    ), why_picked


async def _refresh_feed_page(key: str, sreq: "ScriptRequest", niche: str, creator_id: str,
                             watched: str, cursor: int, why_picked: str = "") -> None:
    """Background upgrade: run the full quality-gated pipeline and overwrite the
    cache entry so the NEXT fetch for this key is both instant and high-quality."""
    try:
        script_result = await scripts(sreq)
        # NO-DOWNGRADE (script-quality audit): only overwrite when the full pipeline
        # actually produced live scripts. An Opus flake returns mode:"mock" (template
        # copy) — writing that would replace the perfectly good fast-paint "live_fast"
        # picks with template copy that then persists for the 6h TTL.
        if script_result.get("mode") != "live":
            logging.info("feed refresh for %s produced %s — keeping the fast paint",
                         key, script_result.get("mode"))
            return
        items, next_cursor = await _compose_feed_items(script_result, niche, creator_id, watched, cursor,
                                                       why_picked=why_picked)
        _feed_cache[key] = {"items": items, "next_cursor": next_cursor,
                            "mode": "live", "ts": time.time()}
        _cap_evict(_feed_cache, _FEED_CACHE_CAP)
    except Exception as e:
        logging.warning("feed background refresh failed for %s: %s", key, e)
    finally:
        _feed_refreshing.discard(key)


async def _prefetch_feed_page(key: str, sreq: "ScriptRequest", niche: str, creator_id: str,
                              watched: str, cursor: int, why_picked: str = "") -> None:
    """B-5: warm the NEXT page (fast single-call path) so "Load more" is a cache hit
    instead of a ~10-18s live generation. Re-checks freshness inside the task so it never
    clobbers a fresher entry (e.g. a background Opus upgrade that landed meanwhile)."""
    try:
        existing = _feed_cache.get(key)
        if existing and (time.time() - existing["ts"]) < _FEED_CACHE_TTL_S:
            return                            # already warm (a real fetch beat us here)
        script_result = await _fast_feed_scripts(sreq)
        items, next_cursor = await _compose_feed_items(script_result, niche, creator_id, watched, cursor,
                                                       why_picked=why_picked)
        if key not in _feed_cache:            # don't overwrite a fresher/higher-quality entry
            _feed_cache[key] = {"items": items, "next_cursor": next_cursor,
                                "mode": script_result.get("mode", "mock"), "ts": time.time()}
            _cap_evict(_feed_cache, _FEED_CACHE_CAP)
    except Exception as e:
        logging.warning("feed prefetch failed for %s: %s", key, e)
    finally:
        _feed_refreshing.discard(key)


def _maybe_prefetch_next(creator_id, brand, styles, watched, cursor, memory, posts,
                         next_cursor, arms=None) -> None:
    """Spawn a prefetch of cursor+1 so the next "Load more" is instant. Guarded by
    _feed_refreshing (single writer per key; the current page holds cursor, prefetch
    holds cursor+1 — disjoint). No-op when the next page is already cached/exhausted."""
    if next_cursor is None:
        return
    posts_token = str(len(posts or []))
    nkey = _feed_cache_key(creator_id, brand, styles, watched, next_cursor, memory, posts_token)
    if nkey in _feed_cache or nkey in _feed_refreshing:
        return
    _feed_refreshing.add(nkey)
    nsreq, nwhy = _feed_sreq(brand, styles, next_cursor, creator_id, memory, posts, arms=arms)
    _spawn(_prefetch_feed_page(nkey, nsreq, brand.get("niche", ""), creator_id, watched,
                               next_cursor, why_picked=nwhy))


async def _prefetch_after_hit(creator_id: str, brand: dict, styles: str, watched: str,
                              cursor: int, memory: dict | None, posts: list[dict],
                              next_cursor: int | None) -> None:
    """Next-page warmup for the pure-cache-hit path, moved OFF the response (the
    blank-home audit found `_top_arms` — 2 Supabase roundtrips on first touch —
    sitting above the cache-hit return, taxing the hottest path in the app)."""
    try:
        arms = await _top_arms(creator_id, brand.get("niche", ""))
        _maybe_prefetch_next(creator_id, brand, styles, watched, cursor, memory, posts,
                             next_cursor, arms=arms)
    except Exception as e:
        logging.warning("[feed] prefetch-after-hit failed: %s", e)


async def _cold_feed_page(key: str, sreq: "ScriptRequest", niche: str, creator_id: str,
                          watched: str, cursor: int, why_picked: str) -> tuple[list, int | None, str]:
    """The shared cold-paint body behind the single-flight latch: generate (lean HAIKU,
    bounded), compose, cache. Every concurrent request for the same key awaits THIS one
    task instead of each burning its own generation budget."""
    script_result = await _fast_feed_scripts(sreq)
    items, next_cursor = await _compose_feed_items(script_result, niche, creator_id, watched, cursor,
                                                   why_picked=why_picked)
    mode = script_result.get("mode", "mock")
    _feed_cache[key] = {"items": items, "next_cursor": next_cursor, "mode": mode, "ts": time.time()}
    _cap_evict(_feed_cache, _FEED_CACHE_CAP)
    return items, next_cursor, mode


async def _feed_impl(creator_id: str, brand: dict, styles: str, watched: str, cursor: int,
                     fresh: int, memory: dict | None) -> dict:
    """Shared feed body for GET (hydrated from the stored profile) and POST (the client's
    live brand + memory). B3: `brand` now carries the full Brand shape, and posts are
    hydrated server-side (the client never holds them) so the generator finally sees the
    creator's real voice/catchphrases/verbatim posts, not just niche/audience/known_for/goal."""
    cursor = max(0, min(cursor, 50))
    creator_id = creator_id or "default"
    # Persist the brand snapshot fire-and-forget (dedup'd on unchanged hash) so GET /v1/feed,
    # write-turn, and the T3 cron can hydrate it later without a client payload.
    _spawn(_persist_creator_profile(creator_id, brand))
    posts = await _creator_posts(creator_id)
    posts_token = str(len(posts))
    key = _feed_cache_key(creator_id, brand, styles, watched, cursor, memory, posts_token)

    cached = _feed_cache.get(key)
    # Live pages keep the long TTL; mock pages expire fast so repolls pick up the
    # background upgrade (but immediate repolls still hit cache, not the gauntlet).
    ttl = _FEED_CACHE_TTL_S if (cached and cached.get("mode") == "live") else _FEED_MOCK_TTL_S
    fresh_enough = cached and (time.time() - cached["ts"]) < ttl
    if cached and fresh_enough and not fresh:
        # Pure cache hit: answer with ZERO awaits — next-page warmup happens off-response.
        _spawn(_prefetch_after_hit(creator_id, brand, styles, watched, cursor, memory,
                                   posts, cached["next_cursor"]))
        return {"mode": cached["mode"], "items": cached["items"], "next_cursor": cached["next_cursor"]}

    # UX-G1: the bandit's judgment (or honest cold-start priors) picks the page's
    # pillar/style and explains WHY — fetched once, threaded through every path.
    arms = await _top_arms(creator_id, brand.get("niche", ""))
    sreq, why_picked = _feed_sreq(brand, styles, cursor, creator_id, memory, posts, arms=arms)

    if cached and not fresh:
        # Stale-while-revalidate: serve the last good page instantly, upgrade in the background.
        if key not in _feed_refreshing:
            _feed_refreshing.add(key)
            _spawn(_refresh_feed_page(key, sreq, brand.get("niche", ""), creator_id, watched,
                                      cursor, why_picked=why_picked))
        _maybe_prefetch_next(creator_id, brand, styles, watched, cursor, memory, posts,
                             cached["next_cursor"], arms=arms)
        return {"mode": cached["mode"], "items": cached["items"], "next_cursor": cached["next_cursor"]}

    # No cache yet (first-ever fetch) — fast single-call path, SINGLE-FLIGHT per key:
    # a burst of cold requests (fresh install + repolls, several new users on one
    # instance) shares one generation instead of stacking 8s calls until the health
    # check starves (the crash-loop from the blank-home audit).
    task = _feed_inflight.get(key)
    if task is None:
        task = asyncio.create_task(_cold_feed_page(key, sreq, brand.get("niche", ""), creator_id,
                                                   watched, cursor, why_picked))
        _feed_inflight[key] = task
        task.add_done_callback(lambda _t, _k=key: _feed_inflight.pop(_k, None))
    # shield: a disconnecting client must not cancel the shared paint under its followers.
    items, next_cursor, mode = await asyncio.shield(task)

    # page 0 → background full-quality OPUS upgrade for the SECOND fetch.
    if cursor == 0 and key not in _feed_refreshing:
        _feed_refreshing.add(key)
        _spawn(_refresh_feed_page(key, sreq, brand.get("niche", ""), creator_id, watched,
                                  cursor, why_picked=why_picked))
    # B-5: always warm the next page so "Load more" is instant at any depth.
    _maybe_prefetch_next(creator_id, brand, styles, watched, cursor, memory, posts,
                         next_cursor, arms=arms)

    return {"mode": mode, "items": items, "next_cursor": next_cursor}


async def _merge_briefs(result: dict, creator_id: str, cursor: int) -> dict:
    """Palo port (flag IDEA_BANK, OFF): prepend the idea bank's ranked briefs onto the
    first feed page. No-op off / on later pages, so paginated fetches don't duplicate."""
    if not palo_flags.enabled(palo_flags.IDEA_BANK) or cursor or not isinstance(result, dict):
        return result
    try:
        briefs = await ideas.brief_feed_items(_palo_store, creator_id)
        result["items"] = ideas.merge_briefs_into_feed(result.get("items", []), briefs)
    except Exception as e:
        logging.warning("[feed] idea-bank merge failed: %s", e)
    return result


@app.get("/v1/feed")
async def feed(creator_id: str = "default", niche: str = "", audience: str = "",
               known_for: str = "", goal: str = "Grow my audience",
               styles: str = "", watched: str = "", cursor: int = 0, fresh: int = 0):
    """B3: GET has no body, so it can't carry voice/catchphrases/etc — hydrate the FULL
    brand from the server-side snapshot (written by a prior POST /v1/feed or /v1/scripts)
    and let any explicit query params override just those 4 legacy fields, so an older
    client (or a query-string deep link) still works exactly as before."""
    brand = await _hydrate_creator_profile(creator_id)
    if niche:
        brand["niche"] = niche
    if audience:
        brand["audience"] = audience
    if known_for:
        brand["known_for"] = known_for
    if goal and goal != "Grow my audience":
        brand["goal"] = goal
    brand.setdefault("goal", goal)
    result = await _feed_impl(creator_id, brand, styles, watched, cursor, fresh, None)
    return await _merge_briefs(result, creator_id, cursor)


@app.post("/v1/feed")
async def feed_post(req: FeedRequest):
    """B-6/B3: memory-personalized feed carrying the client's FULL live brand (voice,
    catchphrases, non_negotiables, what_you_do, emulation_targets — not just the 4 legacy
    fields). Same response shape as GET; the creator's yap-session memory feeds the script
    prompt so Today's picks reflect what they told the orb."""
    brand = _brand_only(req.model_dump())
    result = await _feed_impl(req.creator_id, brand, req.styles, req.watched, req.cursor,
                              req.fresh, req.memory)
    return await _merge_briefs(result, req.creator_id, req.cursor)


class _IdeasRequest(BaseModel):
    creator_id: str = "default"
    limit: int = 12


class _WriteRequest(BaseModel):
    creator_id: str = "default"
    script: dict = {}          # {title, body}
    instruction: str = ""


async def _guard_write_actions(actions: list[dict]) -> list[dict]:
    """Write-turn had NO speakability guard — a <fill> action is a FULL BODY REWRITE,
    so a description-style fill would overwrite good copy with a description. Lint every
    fill's content; repair it, or convert the action to an <answer> so the creator is
    asked for the exact line instead of having a description silently applied."""
    fixed: list[dict] = []
    for a in actions:
        if a.get("op") != "fill" or not a.get("content"):
            fixed.append(a)
            continue
        mini = {"body": a["content"], "style": ""}
        (checked,) = await _ensure_speakable(
            [mini], policy="repair_or_fallback", fallback=lambda i: None)
        if checked is None:
            fixed.append({"op": "answer",
                          "text": "I drafted that as an outline — tell me the exact line "
                                  "you want and I'll write it out spoken."})
        else:
            fixed.append({**a, "content": checked["body"]})
    return fixed


@app.post("/v1/write/turn")
async def write_turn_route(req: _WriteRequest):
    """Palo port (flag WRITE_AGENT): one co-writing turn. Returns the proposed actions
    (exact-substring, accept/reject on iOS via the tweak-ops UI), a preview of the applied
    script, and any chat answer. Off/keyless => a mock answer, no script change."""
    if not palo_flags.enabled(palo_flags.WRITE_AGENT):
        return {"mode": "off", "actions": [], "preview": req.script, "answer": ""}
    body = (req.script or {}).get("body", "")
    result = await write_agent.write_turn(_palo_store, req.creator_id, body, req.instruction)
    actions = await _guard_write_actions(result.get("actions", []))
    new_body, outcomes = write_agent.apply_actions(body, actions)
    answer = next((a.get("text", "") for a in actions if a.get("op") == "answer"), "")
    return {"mode": result.get("mode", "live"), "actions": outcomes,
            "preview": {**(req.script or {}), "body": new_body},
            "invariants": write_agent.check_invariants(body, actions, new_body), "answer": answer}


class _BriefScriptRequest(BaseModel):
    creator_id: str = "default"
    brief: dict = {}
    brand: dict = {}


@app.post("/v1/write/from-brief")
async def script_from_brief_route(req: _BriefScriptRequest):
    """Palo port (flag WRITE_AGENT): turn a selected idea-bank brief into a full script.
    Off/keyless still returns a usable script assembled from the brief beats."""
    out = await write_agent.script_from_brief(_palo_store, req.creator_id, req.brief, req.brand)
    # Unjudged path: guard the body reads as spoken copy, not a description of the brief.
    # Still-dirty falls back to the brief's own deterministic assembly — never a description.
    if isinstance(out, dict) and out.get("body"):
        (fixed,) = await _ensure_speakable(
            [out], policy="repair_or_fallback",
            fallback=lambda i: {**write_agent._mock_script_from_brief(req.brief), "mode": "mock"})
        out = fixed
    return out


@app.post("/v1/ideas")
async def ideas_bank(req: _IdeasRequest):
    """The creator's idea bank (Palo port, flag IDEA_BANK). Ranked briefs the app can
    render as a dedicated 'ideas' surface. Off / keyless => empty."""
    if not palo_flags.enabled(palo_flags.IDEA_BANK):
        return {"mode": "off", "briefs": []}
    limit = max(1, min(req.limit, 50))               # clamp: negative breaks PostgREST, huge = big fetch
    briefs = await ideas.brief_feed_items(_palo_store, req.creator_id, limit=limit)
    return {"mode": "live" if _palo_store else "mock", "briefs": briefs}


@app.get("/v1/insights")
async def get_insights(creator_id: str = "default", limit: int = 50):
    """Palo port (flag TRACK_INSIGHTS): the creator's insight feed (P7.3 inbox). Off/keyless => empty."""
    if not palo_flags.enabled(palo_flags.TRACK_INSIGHTS):
        return {"mode": "off", "insights": []}
    limit = max(1, min(limit, 100))
    rows = await _palo_store.load_insights(creator_id, limit=limit) if _palo_store else []
    return {"mode": "live" if _palo_store else "mock", "insights": rows}


@app.get("/v1/strategy")
async def get_strategy(creator_id: str = "default"):
    """Palo port (flag STRATEGY_COMPILER): the compiled strategy + recent updates (P7.4
    'Your Strategy'). Off/keyless => null."""
    if not palo_flags.enabled(palo_flags.STRATEGY_COMPILER) or not _palo_store:
        return {"mode": "off" if not palo_flags.enabled(palo_flags.STRATEGY_COMPILER) else "mock",
                "strategy": None, "updates": []}
    strat = await _palo_store.load_strategy(creator_id)
    updates = await _palo_store.load_strategy_updates(creator_id)
    return {"mode": "live", "strategy": strat, "updates": updates}


@app.post("/v1/feed/feedback")
async def feed_feedback(req: FeedFeedbackRequest):
    """B-7: a Today's-picks like/dislike. Folds a small reward into the creator's bandit
    arms (decoupled from real-post stats) and, on dislike, records the pick's fingerprint
    so a near-identical script doesn't reappear. Idempotent per fingerprint."""
    verdict = req.verdict if req.verdict in ("like", "dislike") else "like"
    fp = _script_fingerprint(req.script)
    seen = _feed_feedback_seen.setdefault(req.creator_id, set())
    if fp in seen:
        return {"mode": "live" if _supabase_client else "mock", "status": "duplicate",
                "arms_updated": 0, "dismissed": verdict == "dislike"}
    if len(seen) < 400:                        # bound the idempotency set
        seen.add(fp)
    y = FEEDBACK_LIKE_Y if verdict == "like" else FEEDBACK_DISLIKE_Y
    niche = req.niche or _creator_niche.get(req.creator_id, "")
    sc = req.script
    # Map each bandit dimension to the script's field(s) — mirrors /v1/metrics/ingest's dims.
    dim_values = {
        "pillar": sc.get("pillar") or sc.get("pillarName"),
        "style": sc.get("style"),
        "format_id": sc.get("format_id") or sc.get("formatId"),
        "hook_signal": sc.get("hook_signal") or sc.get("hookSignal"),
    }
    updated = 0
    for dim in DIMENSIONS:
        val = dim_values.get(dim)
        if val:
            await _update_arm_feedback(req.creator_id, f"{dim}:{val}", y, niche)
            updated += 1
    if verdict == "dislike":
        _record_dismissal(req.creator_id, fp)
    return {"mode": "live" if _supabase_client else "mock", "status": "recorded",
            "arms_updated": updated, "dismissed": verdict == "dislike"}


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
        "body": (f"Here's the thing most people in {niche} get completely backwards.\n\n"
                 f"They chase the obvious move and wonder why nothing changes. The real lever is the "
                 f"one nobody talks about — and once you see it, you can't unsee it.\n\n"
                 f"Do this one thing differently this week and watch what happens."),
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
        stats = await _arms_for_prompt(req.creator_id)
        posts = await _creator_posts(req.creator_id)   # B3: real posts -> verbatim voice exemplars
        sys, usr = prompts.mimic_prompt(req.reel, req.brand, req.memory, arm_stats=stats, posts=posts)
        sys = await _inject_brain(sys, req.creator_id)   # G2: "your version" shaped by the brain
        out = extract_json(await anthropic(sys, usr, OPUS, 2000), array=False)
        if not out:
            return {"mode": "mock", "script": _mock_mimic(req.reel, req.brand), "mimicked_from": provenance}
        # mimic is unjudged — guard the body; still-dirty falls back to the deterministic mock mimic.
        (out,) = await _ensure_speakable(
            [out], policy="repair_or_fallback",
            fallback=lambda i: _mock_mimic(req.reel, req.brand))
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


async def _resolve_post_media(url: str) -> str | None:
    """Resolve a pasted IG/TikTok/YT post URL to a downloadable media URL via Apify.
    Net-new (scrape_posts is handle-based). Returns None keyless / on failure."""
    if not APIFY_KEY or not url:
        return None
    platform = _platform_from_url(url)
    if platform == "tiktok":
        actor, payload = "clockworks~tiktok-scraper", {"postURLs": [url], "shouldDownloadVideos": True}
    else:
        actor, payload = "apify~instagram-scraper", {"directUrls": [url], "resultsType": "posts", "resultsLimit": 1}
    try:
        items = await _run_apify_actor(actor, payload)
    except Exception as e:
        logging.warning("post-media resolve failed: %s", e)
        return None
    for it in items or []:
        if isinstance(it, dict):
            media = (it.get("videoUrl") or it.get("video_url") or it.get("mediaUrl")
                     or it.get("downloadAddr") or it.get("videoUrlNoWaterMark"))
            if media:
                return media
    return None


async def _transcribe_post_url(url: str) -> str | None:
    """Resolve a pasted post URL to its media and transcribe it for real. Returns the
    transcript text, or None if we can't (keyless, unsupported URL, scrape/transcribe
    failure) — the caller then labels the analysis 'live_structure', never 'live'."""
    if not (APIFY_KEY and ASSEMBLY_KEY):
        return None
    try:
        media = await _resolve_post_media(url)
        if not media:
            return None
        tid = await _submit_transcription(media)
        if not tid:
            return None
        transcript = await _poll_transcription(tid)
        text = " ".join(w.get("word", "") for w in (transcript.get("words") or [])).strip()
        return text or None
    except Exception as e:
        logging.warning("analyze-video transcribe failed: %s", e)
        return None


@app.post("/v1/analyze-video")
async def analyze_video(req: AnalyzeVideoRequest):
    platform = _platform_from_url(req.url)
    # Real path: resolve the pasted link → media → AssemblyAI transcript. If we can't get
    # the REAL transcript, we analyze a canned structure and label it honestly
    # 'live_structure' (a pattern read, not this exact video) — never a fake 'live'.
    real_transcript = await _transcribe_post_url(req.url)
    transcript = real_transcript or _MOCK_VIDEO_TRANSCRIPT
    is_real = real_transcript is not None
    niche = req.brand.get("niche") or "your niche"
    if ANTHROPIC_KEY:
        try:
            stats = await _arms_for_prompt(req.creator_id)
            posts = await _creator_posts(req.creator_id)   # B3: real posts -> verbatim voice exemplars
            sys, usr = prompts.analyze_video_prompt(req.url, transcript, req.brand, req.memory,
                                                    arm_stats=stats, posts=posts)
            sys = await _inject_brain(sys, req.creator_id)   # G3: your_version shaped by the brain
            out = extract_json(await anthropic(sys, usr, OPUS, 2600), array=False)
            if out and out.get("your_version"):
                # This path had NO speakability guard — the "your_version" script is the
                # only script-body field here; still-dirty falls back to the deterministic
                # mock mimic built from the same (real or canned) transcript.
                mock_reel_for_fallback = {"creator_handle": "the original creator", "platform": platform,
                                          "title": "the linked video", "transcript": transcript,
                                          "format_id": "myth-buster", "style": "talking_head"}
                (yv,) = await _ensure_speakable(
                    [out["your_version"]], policy="repair_or_fallback",
                    fallback=lambda i: _mock_mimic(mock_reel_for_fallback, req.brand))
                out["your_version"] = yv
                return {"mode": "live" if is_real else "live_structure", "platform": platform,
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

def _post_effective_date(post: dict) -> str | None:
    """The timestamp a post is bucketed/windowed by: when it settled, else when it was
    scheduled. B-2: legacy rows with neither are excluded (were silently included, which
    over-counted history into every window)."""
    return post.get("settled_at") or post.get("scheduled_at")


def _within_window(post: dict, cutoff: datetime) -> bool:
    """True if the post's effective date is on/after the cutoff. No effective date → excluded."""
    eff = _post_effective_date(post)
    if not eff:
        return False
    try:
        return datetime.fromisoformat(eff) >= cutoff
    except (ValueError, TypeError):
        return False


@app.get("/v1/performance/summary")
async def performance_summary(creator_id: str = "default", days: int = 30, now: str = ""):
    days = max(7, min(90, days))
    try:
        now_dt = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    except ValueError:
        now_dt = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = now_dt - timedelta(days=days)
    settled = [(pid, p) for pid, p in _post_registry.items()
               if p.get("creator_id") == creator_id and p.get("settled") and p.get("metrics")
               and _within_window(p, cutoff)]
    # B-1: a CONFIGURED backend (AI key or a real DB) NEVER fabricates — with zero settled
    # posts it returns honest zeros + no_data. Only a truly keyless dev/demo build charts
    # the seeded placeholder series below.
    _configured = bool(ANTHROPIC_KEY or _supabase_client)
    if settled or _configured:
        totals = {"views": 0, "likes": 0, "comments": 0, "shares": 0,
                  "follows_gained": 0, "posts": len(settled)}
        platforms: dict[str, dict] = {}
        best = None
        fmt_mix: dict[str, int] = {}
        # B-1: dense, zero-filled daily buckets by the post's effective date, so the graph
        # renders a real series for real creators (live mode used to return []). Bucket 0 is
        # the oldest day in the window (cutoff+1); bucket days-1 is today (now_dt).
        day0 = (cutoff + timedelta(days=1)).date()
        daily_views = [0] * days
        daily_likes = [0] * days
        for pid, p in settled:
            m = p["metrics"]
            v, lk = m.get("views", 0), m.get("likes", 0)
            cm, sh = m.get("comments", 0), m.get("shares", 0)
            totals["views"] += v; totals["likes"] += lk
            totals["comments"] += cm; totals["shares"] += sh
            totals["follows_gained"] += m.get("follows_gained", 0)
            plat = p.get("platform", "instagram")
            ps = platforms.setdefault(plat, {"views": 0, "likes": 0, "comments": 0, "shares": 0,
                                             "follows_gained": 0, "posts": 0})
            ps["views"] += v; ps["likes"] += lk; ps["comments"] += cm; ps["shares"] += sh
            ps["follows_gained"] += m.get("follows_gained", 0); ps["posts"] += 1
            fmt = p.get("format_id") or "other"
            fmt_mix[fmt] = fmt_mix.get(fmt, 0) + 1
            if best is None or v > best["views"]:
                best = {"post_id": pid, "views": v, "likes": lk, "format_id": fmt, "platform": plat}
            eff = _post_effective_date(p)
            try:
                idx = (datetime.fromisoformat(eff).date() - day0).days if eff else None
            except (ValueError, TypeError):
                idx = None
            if idx is not None and 0 <= idx < days:
                daily_views[idx] += v; daily_likes[idx] += lk
        # B-2: engagement unified to (likes+comments+shares)/views (matches the iOS model).
        eng_num = totals["likes"] + totals["comments"] + totals["shares"]
        eng = round((eng_num / max(totals["views"], 1)) * 100, 1)
        # Honest empty: a configured backend with nothing settled charts NO series (not a
        # flat fake line); real creators get the dense dated series.
        daily = ([] if not settled else
                 [{"day": i, "date": (day0 + timedelta(days=i)).isoformat(),
                   "views": daily_views[i], "likes": daily_likes[i]} for i in range(days)])
        # C-11: the creator's actual best posting hour — mode hour weighted by views, gated
        # on enough evidence (N>=4). Below the gate the field is omitted.
        hour_views: dict[int, int] = {}
        for _pid, p in settled:
            sa = p.get("scheduled_at") or p.get("settled_at")
            try:
                hr = datetime.fromisoformat(sa).hour if sa else None
            except (ValueError, TypeError):
                hr = None
            if hr is not None:
                hour_views[hr] = hour_views.get(hr, 0) + p["metrics"].get("views", 0)
        best_hour = max(hour_views, key=hour_views.get) if len(settled) >= 4 and hour_views else None
        out = {"mode": "live", "days": days, "totals": {**totals, "engagement_rate": eng},
               "platforms": platforms, "daily": daily,
               # zeroed dict (never null) so build-10/11 clients decode best_post safely
               "best_post": best or {"post_id": "", "views": 0, "likes": 0,
                                     "format_id": "", "platform": ""},
               "format_mix": [{"format": k, "count": v} for k, v in
                              sorted(fmt_mix.items(), key=lambda x: -x[1])]}
        if best_hour is not None:
            out["best_hour"] = best_hour
        if not settled:
            out["no_data"] = True      # configured but nothing measured yet — honest empty
        return out
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
    # C-04: honest signal — the series below is placeholder, not measured. The client shows an
    # empty state on no_data instead of charting fabricated numbers.
    return {"mode": "mock", "no_data": True, "days": days,
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
