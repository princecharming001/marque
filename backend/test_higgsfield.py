"""Higgsfield generative b-roll adapter + _resolve_broll fallback + ETA (keyless)."""
from __future__ import annotations

import asyncio

import pytest

import main
from app import higgsfield as H


def _run(coro):
    return asyncio.run(coro)


# --- adapter ------------------------------------------------------------------

def test_keyless_is_noop():
    assert H.CONFIGURED is False                   # CI contract: no HIGGSFIELD_KEY
    assert _run(H.generate_broll("city at night")) is None


def test_generate_broll_happy_chain(monkeypatch):
    monkeypatch.setattr(H, "CONFIGURED", True)
    calls = []

    async def fake_submit(model_id, body):
        calls.append((model_id, body))
        return f"req-{len(calls)}"

    async def fake_poll(request_id, deadline):
        if request_id == "req-1":
            return {"status": "completed", "images": [{"url": "https://hf/img.jpg"}]}
        return {"status": "completed", "video": {"url": "https://hf/broll.mp4"}}

    monkeypatch.setattr(H, "_submit", fake_submit)
    monkeypatch.setattr(H, "_poll_request", fake_poll)

    url = _run(H.generate_broll("barista pouring latte art", duration_s=5))
    assert url == "https://hf/broll.mp4"
    # chain shape: t2i first (9:16), then i2v with the produced image
    assert calls[0][0] == H._T2I_MODEL and calls[0][1]["aspect_ratio"] == "9:16"
    assert calls[1][0] == H._I2V_MODEL and calls[1][1]["image_url"] == "https://hf/img.jpg"
    assert calls[1][1]["duration"] == 5


def test_generate_broll_failure_modes(monkeypatch):
    monkeypatch.setattr(H, "CONFIGURED", True)

    async def submit_none(model_id, body):
        return None
    monkeypatch.setattr(H, "_submit", submit_none)
    assert _run(H.generate_broll("x")) is None      # submit failed → None

    async def submit_ok(model_id, body):
        return "req-1"
    async def poll_failed(request_id, deadline):
        return None                                 # timeout / failed / nsfw
    monkeypatch.setattr(H, "_submit", submit_ok)
    monkeypatch.setattr(H, "_poll_request", poll_failed)
    assert _run(H.generate_broll("x")) is None


# --- _resolve_broll fallback -----------------------------------------------------

def test_resolve_broll_falls_back_to_higgsfield(monkeypatch):
    monkeypatch.setattr(main, "PEXELS_KEY", "px")
    monkeypatch.setattr(main.higgsfield_mod, "CONFIGURED", True)
    main._broll_url_cache.clear()

    async def no_candidates(query, n):
        return []                                   # stock has nothing
    async def fake_generate(cue, duration_s=5):
        return "https://hf/generated.mp4"
    monkeypatch.setattr(main, "_fetch_pexels_candidates", no_candidates)
    monkeypatch.setattr(main.higgsfield_mod, "generate_broll", fake_generate)

    edl = {"broll": [{"broll_query": "impossible query", "cue_text": "the thing", "source": "stock"}]}
    out = _run(main._resolve_broll(edl))
    assert out["broll"][0]["resolved_url"] == "https://hf/generated.mp4"
    main._broll_url_cache.clear()


def test_resolve_broll_generation_capped_per_job(monkeypatch):
    monkeypatch.setattr(main, "PEXELS_KEY", "px")
    monkeypatch.setattr(main.higgsfield_mod, "CONFIGURED", True)
    monkeypatch.setattr(main, "_HIGGSFIELD_MAX_PER_JOB", 2)
    main._broll_url_cache.clear()

    gen_calls = []
    async def no_candidates(query, n):
        return []
    async def fake_generate(cue, duration_s=5):
        gen_calls.append(cue)
        return f"https://hf/{len(gen_calls)}.mp4"
    monkeypatch.setattr(main, "_fetch_pexels_candidates", no_candidates)
    monkeypatch.setattr(main.higgsfield_mod, "generate_broll", fake_generate)

    edl = {"broll": [{"broll_query": f"q{i}", "cue_text": f"c{i}", "source": "stock"}
                     for i in range(4)]}
    out = _run(main._resolve_broll(edl))
    assert len(gen_calls) == 2                      # cap holds
    resolved = [b for b in out["broll"] if b.get("resolved_url")]
    assert len(resolved) == 2
    main._broll_url_cache.clear()


def test_resolve_broll_pexels_still_wins(monkeypatch):
    monkeypatch.setattr(main, "PEXELS_KEY", "px")
    monkeypatch.setattr(main.higgsfield_mod, "CONFIGURED", True)
    main._broll_url_cache.clear()

    async def stock_hit(query, n):
        return [{"link": "https://pexels/v.mp4", "thumb": None}]
    async def must_not_run(cue, duration_s=5):
        raise AssertionError("higgsfield must not run when stock resolves")
    monkeypatch.setattr(main, "_fetch_pexels_candidates", stock_hit)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")   # rerank → top-1
    monkeypatch.setattr(main.higgsfield_mod, "generate_broll", must_not_run)

    edl = {"broll": [{"broll_query": "city", "cue_text": "city", "source": "stock"}]}
    out = _run(main._resolve_broll(edl))
    assert out["broll"][0]["resolved_url"] == "https://pexels/v.mp4"
    main._broll_url_cache.clear()


# --- ETA -------------------------------------------------------------------------

def test_job_eta_by_stage():
    import time
    now = time.time()
    assert 235 <= main._job_eta_seconds({"status": "processing", "created_at": now}) <= 240
    assert 85 <= main._job_eta_seconds({"status": "rendering", "created_at": now}) <= 90
    assert main._job_eta_seconds({"status": "ready"}) is None
    assert main._job_eta_seconds({"status": "failed"}) is None
    # elapsed eats the estimate but never below the 20s floor
    assert main._job_eta_seconds({"status": "processing", "created_at": now - 10_000}) == 20


def test_eta_in_create_and_get(monkeypatch):
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "test-key")
    async def nop(job_id):
        return None
    monkeypatch.setattr(main, "_run_auto_pipeline", nop)
    r = client.post("/v1/clips", json={"source_url": "mock://x", "analyze_first": True,
                                       "auto_confirm": True,
                                       "script": {"hook": "h", "body": "b", "cta": "c"}}).json()
    assert r["status"] == "processing" and r["eta_seconds"] >= 20
    g = client.get(f"/v1/clips/{r['job_id']}").json()
    assert g["eta_seconds"] >= 20
    main._clip_jobs.pop(r["job_id"], None)


# --- adversarial-review regression tests -------------------------------------------

def test_eta_anchors_at_stage_not_created_at():
    """Review finding: user dwell at brief_ready must not count as pipeline progress."""
    import time
    now = time.time()
    # parked on the user → NO estimate at all
    assert main._job_eta_seconds({"status": "brief_ready", "created_at": now - 500}) is None
    # a fresh stage on an OLD job uses the stage anchor, not created_at
    job = {"status": "editing", "created_at": now - 500, "stage_started_at": now}
    assert main._job_eta_seconds(job) >= 125          # full editing baseline, not the floor


def test_tweak_rerender_never_generates(monkeypatch):
    """Review finding: generation inside the render-watchdog window falsely failed
    succeeding renders — _rerender_clip's resolve must pass allow_generation=False."""
    import asyncio
    monkeypatch.setattr(main, "PEXELS_KEY", "px")
    monkeypatch.setattr(main.higgsfield_mod, "CONFIGURED", True)
    main._broll_url_cache.clear(); main._broll_gen_failed.clear()

    async def no_candidates(query, n):
        return []
    async def must_not_run(cue, duration_s=5):
        raise AssertionError("generation must not run on the tweak path")
    monkeypatch.setattr(main, "_fetch_pexels_candidates", no_candidates)
    monkeypatch.setattr(main.higgsfield_mod, "generate_broll", must_not_run)

    edl = {"broll": [{"broll_query": "nothing matches", "cue_text": "c", "need": "action", "source": "stock"}]}
    out = asyncio.run(main._resolve_broll(edl, allow_generation=False))
    # No burn: nothing resolved to a real clip (the action cue degraded to a face-keeping punch-in
    # rather than generating one). The load-bearing assertion is `must_not_run` never firing.
    assert not any(b.get("resolved_url") for b in out["broll"])


def test_failed_generation_negative_cached(monkeypatch):
    import asyncio
    monkeypatch.setattr(main, "PEXELS_KEY", "px")
    monkeypatch.setattr(main.higgsfield_mod, "CONFIGURED", True)
    main._broll_url_cache.clear(); main._broll_gen_failed.clear()

    calls = []
    async def no_candidates(query, n):
        return []
    async def failing_generate(cue, duration_s=5):
        calls.append(cue)
        return None
    monkeypatch.setattr(main, "_fetch_pexels_candidates", no_candidates)
    monkeypatch.setattr(main.higgsfield_mod, "generate_broll", failing_generate)

    edl = {"broll": [{"broll_query": "hopeless", "cue_text": "c", "source": "stock"}]}
    asyncio.run(main._resolve_broll(edl))
    asyncio.run(main._resolve_broll(edl))               # second pass: negative cache holds
    assert len(calls) == 1
    main._broll_gen_failed.clear()
