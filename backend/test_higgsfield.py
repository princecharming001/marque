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


def test_generate_still_soul_only(monkeypatch):
    import asyncio
    from app import higgsfield as hf
    monkeypatch.setattr(hf, "CONFIGURED", True)
    submitted = []
    async def fake_submit(model, body):
        submitted.append(model)
        return "req-1"
    async def fake_poll(rid, deadline):
        return {"images": [{"url": "https://h/still.jpg"}]}
    monkeypatch.setattr(hf, "_submit", fake_submit)
    monkeypatch.setattr(hf, "_poll_request", fake_poll)
    url = asyncio.run(hf.generate_still("gochujang jar closeup"))
    assert url == "https://h/still.jpg"
    assert submitted == [hf._T2I_MODEL], "still tier must call SOUL only, never DoP"


def test_generate_still_keyless_noop(monkeypatch):
    import asyncio
    from app import higgsfield as hf
    monkeypatch.setattr(hf, "CONFIGURED", False)
    assert asyncio.run(hf.generate_still("x")) is None


# --- per-stage timing instrumentation ----------------------------------------------
# The ETA table above is a promise to the USER; this is the number that tells US whether
# the promise was kept. Before it existed, "editing is taking unusually long" could only
# be answered by reading code.

def test_stage_timings_accumulate_and_log(caplog):
    import logging as _logging
    job: dict = {}
    with main._timed(job, "broll_resolve"):
        pass
    main._mark_timing(job, "render", main.time.perf_counter() - 12.0)
    main._mark_timing(job, "render", main.time.perf_counter() - 3.0)   # additive, not last-wins
    assert 14.9 < job["_stage_timings"]["render"] < 15.2
    with caplog.at_level(_logging.INFO):
        main._log_stage_timings("job-1", job)
    lines = [r.getMessage() for r in caplog.records if "[timing]" in r.getMessage()]
    assert len(lines) == 1                                  # ONE line per job
    assert "job=job-1" in lines[0] and "render=15.0s" in lines[0]
    # slowest stage first — the whole point is that the answer is the first thing you read
    assert lines[0].index("render=") < lines[0].index("broll_resolve=")


def test_stage_timings_survive_a_raising_stage():
    """A stage that blew up is exactly the one whose duration you want."""
    job: dict = {}
    try:
        with main._timed(job, "author"):
            raise RuntimeError("llm down")
    except RuntimeError:
        pass
    assert "author" in job["_stage_timings"]


def test_log_stage_timings_silent_when_nothing_measured(caplog):
    import logging as _logging
    with caplog.at_level(_logging.INFO):
        main._log_stage_timings("job-2", {})
    assert not [r for r in caplog.records if "[timing]" in r.getMessage()]


def test_higgsfield_timeout_cannot_dominate_an_edit():
    """Generation is a NICETY on the interactive path: the whole-chain budget x the
    per-job cap must stay well inside the editing stage's own ETA."""
    assert main.higgsfield_mod.HIGGSFIELD_TIMEOUT_S <= 60
    assert (main.higgsfield_mod.HIGGSFIELD_TIMEOUT_S * main._HIGGSFIELD_MAX_PER_JOB
            < main._STAGE_ETA_S["editing"])
