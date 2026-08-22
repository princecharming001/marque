"""Long-take hardening (2026-08-22 audit) — regression tests for the ">1min footage"
defect batch. Every test runs KEYLESS (same seams as test_editor_hardening.py).

Covered fixes:
  1. The three editing LLM calls scale max_tokens with the transcript instead of
     flat caps that truncated long takes into silent degradation (brief → mock,
     plan → safe default, legacy EDL → safe default).
  2. The render sweep honors each clip's SCALED budget (render_budget_s, stamped at
     submit from the same _scaled_render_budgets call the poller uses) — a long
     render the poll is still legitimately inside of is no longer killed at the
     flat RENDER_WATCHDOG_S.
  3. Anchor hole: a 'rendering' clip with a MISSING render_started_at used to read
     as age-0 forever (now-default) and was never swept — it now falls back to the
     job's stage_started_at/created_at.
  4. retry/resume pop the persisted restored_at when they reset created_at, so the
     durable row can't fossilize restored_at < created_at (broken forensics).
  5. _reattach_one_render finalizes the JOB on the failure branches too — a failed
     last clip no longer leaves the job 'rendering' forever.
  6. A Higgsfield TIMEOUT (budget miss) is not negative-cached; only definitive
     rejections poison _broll_gen_failed.
"""
import asyncio
import time
import uuid

import main
from app import higgsfield as H


def _run(coro):
    return asyncio.run(coro)


def _job(job_id=None, **over):
    job_id = job_id or str(uuid.uuid4())
    job = {
        "job_id": job_id, "status": "transcribing", "created_at": time.time(),
        "clips": [{"clip_id": "c1", "format": "myth-buster", "status": "transcribing"}],
        "edl": None, "words": [], "edl_history": [], "tweaks": [],
        "script": {"hook": "h", "formatId": "myth-buster"}, "style": "talking_head",
        "brand": {}, "media_context": "", "source_url": "mock://x", "error": None,
        "edit_prefs": {}, "react_source_url": "", "react_credit_label": "",
        "custom_instructions": "",
    }
    job.update(over)
    main._clip_jobs[job_id] = job
    return job_id


def _cleanup(job_id):
    main._clip_jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# 1. Transcript-scaled LLM output budgets
# ---------------------------------------------------------------------------

def test_llm_budgets_scale_with_transcript():
    # 200-word take (~80s): modest bumps over the old flat caps.
    assert main._brief_max_tokens(200) == 2100          # 1600 + 200*2.5
    assert main._plan_max_tokens(200) == 3800           # 3000 + 200*4
    assert main._edl_max_tokens(200) == 6400            # 4000 + 200*12
    # 1500-word take (~8-10min of speech): all three MUST clear their old flat caps
    # by a wide margin — this is exactly the regime that silently degraded.
    assert main._brief_max_tokens(1500) == 5350
    assert main._plan_max_tokens(1500) == 9000
    assert main._edl_max_tokens(1500) == 22000
    # Old flat caps are strictly exceeded at 1500 words (the load-bearing claim).
    assert main._brief_max_tokens(1500) > 1600
    assert main._plan_max_tokens(1500) > 3000
    assert main._edl_max_tokens(1500) > 8000


def test_llm_budgets_are_capped_and_floored():
    # Absurd inputs cap out (never request an impossible completion budget)…
    assert main._brief_max_tokens(100_000) == 6000
    assert main._plan_max_tokens(100_000) == 16000
    assert main._edl_max_tokens(100_000) == 32000
    # …and an empty transcript keeps the old short-take budgets as the floor.
    assert main._brief_max_tokens(0) == 1600
    assert main._plan_max_tokens(0) == 3000
    assert main._edl_max_tokens(0) == 4000


def test_edit_brief_call_passes_scaled_budget(monkeypatch):
    """The call site must actually consume the helper — a 1500-word take asks for
    the scaled budget, not the old flat 1600."""
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "AI_QUALITY", True)
    seen = {}

    async def fake_json(sys, usr, schema, model, max_tokens, **kw):
        seen["max_tokens"] = max_tokens
        raise main.HTTPException(status_code=500, detail="down")   # degrade to mock
    monkeypatch.setattr(main, "anthropic_json", fake_json)

    words = [{"word": f"w{i}", "start_ms": i * 300, "end_ms": i * 300 + 250}
             for i in range(1500)]
    out = _run(main._generate_edit_brief(words, "t"))
    assert seen["max_tokens"] == main._brief_max_tokens(1500) == 5350
    assert out.get("inferred")                          # mock fallback still shipped


# ---------------------------------------------------------------------------
# 2. Sweep honors the per-clip scaled render budget
# ---------------------------------------------------------------------------

def test_clip_with_scaled_budget_survives_flat_watchdog(monkeypatch):
    """A 500s-old render whose stamped scaled budget is 900s is INSIDE its earned
    window — the flat 480s watchdog must not kill it."""
    monkeypatch.setattr(main, "RENDER_WATCHDOG_S", 480)
    now = time.time()
    jobs = {"js": {"job_id": None, "status": "rendering", "created_at": now - 500,
                   "stage_started_at": now - 500,
                   "clips": [{"clip_id": "c1", "status": "rendering",
                              "render_started_at": now - 500,
                              "render_budget_s": 900}]}}
    main._sweep_stuck_renders(jobs)
    assert jobs["js"]["clips"][0]["status"] == "rendering"     # spared
    assert jobs["js"]["status"] == "rendering"                 # job spared too


def test_flat_clip_still_fails_at_watchdog(monkeypatch):
    """Same age, NO stamped budget (short clip / legacy row): the flat watchdog
    still owns it — the scaled window is earned per-render, never a blanket raise."""
    monkeypatch.setattr(main, "RENDER_WATCHDOG_S", 480)
    now = time.time()
    jobs = {"jf": {"job_id": None, "status": "rendering", "created_at": now - 500,
                   "stage_started_at": now - 500,
                   "clips": [{"clip_id": "c1", "status": "rendering",
                              "render_started_at": now - 500}]}}
    main._sweep_stuck_renders(jobs)
    c = jobs["jf"]["clips"][0]
    assert c["status"] == "failed" and c["error"] == "render_stalled"


def test_render_start_stamps_scaled_budget(monkeypatch):
    """The render path stamps render_budget_s from the SAME _scaled_render_budgets
    call the poller uses — the watchdog/poller budget agreement is set at submit."""
    async def scenario():
        for k in ("REMOTION_SERVE_URL", "REMOTION_ACCESS_KEY",
                  "REMOTION_SECRET", "REMOTION_FUNCTION_NAME"):
            monkeypatch.setattr(main, k, "x")

        async def bridge(*args, timeout_s=None, **kwargs):
            if args[0] == "submit":
                return {"renderId": "r1", "bucketName": "b"}
            return {"done": True, "outputFile": "https://cdn/out.mp4"}
        monkeypatch.setattr(main, "_run_render_bridge", bridge)

        job_id = _job(status="rendering",
                      edl={"style": "talking_head", "format_id": "x",
                           "segments": [{"src_in": 0, "src_out": 300}]},
                      clips=[{"clip_id": "c1", "format": "myth-buster", "status": "queued"}])
        await main._render_all_clips(job_id)
        clip = main._clip_jobs[job_id]["clips"][0]
        assert clip["status"] == "ready"
        assert clip["render_budget_s"] == \
            main._scaled_render_budgets(clip["render_total_frames"])[0]
        _cleanup(job_id)
    _run(scenario())


def test_poll_ceiling_covers_long_outputs():
    """#4: the scaled poll budget's ceiling must admit a 4-5min output (was 900,
    which killed those renders at the cap while Lambda was still progressing)."""
    assert main.RENDER_POLL_CEIL_S >= 1200
    # a ~5min output at 30fps: 9000 frames → wants 240 + 9000*0.12 = 1320s, capped
    budget, _stall = main._scaled_render_budgets(9000)
    assert budget == main.RENDER_POLL_CEIL_S


# ---------------------------------------------------------------------------
# 3. Anchor hole — missing render_started_at no longer suppresses the sweep
# ---------------------------------------------------------------------------

def test_missing_render_stamp_no_longer_reads_as_age_zero():
    """Old bug: now - clip.get('render_started_at', now) == 0 forever → a stamp-less
    'rendering' clip was NEVER swept. It must fall back to the job's own clocks."""
    now = time.time()
    jobs = {"ja": {"job_id": None, "status": "rendering", "created_at": now - 99_999,
                   "stage_started_at": now - 99_999,
                   "clips": [{"clip_id": "c1", "status": "rendering"}]}}
    main._sweep_stuck_renders(jobs, max_render_s=1)
    c = jobs["ja"]["clips"][0]
    assert c["status"] == "failed" and c["error"] == "render_stalled"


def test_missing_render_stamp_with_fresh_job_anchor_survives():
    """The fallback anchors on real progress markers — a FRESH job whose clip lost
    its stamp is not insta-failed."""
    now = time.time()
    jobs = {"jb": {"job_id": None, "status": "rendering", "created_at": now - 5,
                   "stage_started_at": now - 5,
                   "clips": [{"clip_id": "c1", "status": "rendering"}]}}
    main._sweep_stuck_renders(jobs, max_render_s=480)
    assert jobs["jb"]["clips"][0]["status"] == "rendering"


# ---------------------------------------------------------------------------
# 4. restored_at hygiene on retry + resume
# ---------------------------------------------------------------------------

def test_retry_pops_restored_at():
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    job_id = _job(status="failed", error="render_stalled",
                  created_at=time.time() - 10_000,
                  restored_at=time.time() - 5_000,       # restored from Supabase earlier
                  edl={"style": "talking_head", "format_id": "x",
                       "segments": [{"src_in": 0, "src_out": 300}]},
                  clips=[{"clip_id": "c1", "format": "myth-buster", "status": "failed",
                          "error": "render_stalled"}])
    r = client.post(f"/v1/clips/{job_id}/retry")
    assert r.status_code == 200
    job = main._clip_jobs[job_id]
    # created_at was reset to now — a surviving restore stamp would persist the
    # impossible restored_at < created_at ordering.
    assert "restored_at" not in job
    assert time.time() - job["created_at"] < 5
    _cleanup(job_id)


def test_resume_pops_restored_at(monkeypatch):
    calls = []

    async def fake_edit(jid, words):
        calls.append(jid)
    monkeypatch.setattr(main, "_run_edit", fake_edit)

    async def run():
        old = time.time() - 3600
        job = {"job_id": "jrs", "status": "editing", "created_at": old,
               "stage_started_at": old, "restored_at": old + 10,
               "clips": [], "pipeline_gen": 0,
               "words": [{"word": "a", "end_ms": 100}]}
        assert main._try_resume_pipeline(job) is True
        await asyncio.sleep(0.01)
        return job
    job = asyncio.run(run())
    assert calls == ["jrs"]
    assert "restored_at" not in job
    assert time.time() - job["created_at"] < 5
    main._pipeline_tasks.pop("jrs", None)


# ---------------------------------------------------------------------------
# 5. _reattach_one_render failure branch finalizes the job
# ---------------------------------------------------------------------------

def _reattach_clip(**over):
    clip = {"clip_id": "c1", "status": "rendering", "render_id": "r-1",
            "bucket_name": "b-1", "render_gen": 1}
    clip.update(over)
    return clip


def test_reattach_failure_finalizes_last_clip_job(monkeypatch):
    """The old failure branch failed the CLIP but left the JOB 'rendering' forever
    when that was the last in-flight clip — non-terminal in Supabase, eternal spinner."""
    async def boom(render_id, bucket_name, total_frames=None):
        raise main.PipelineError("render_fatal", "lambda died", "render")
    monkeypatch.setattr(main, "_poll_remotion_render", boom)

    clip = _reattach_clip()
    job = {"status": "rendering", "clips": [clip]}      # no job_id → no persist spawn
    _run(main._reattach_one_render(job, clip, 1))
    assert clip["status"] == "failed" and clip["error"] == "render_fatal"
    assert job["status"] == "failed"                    # terminal, mirrored from ready branch


def test_reattach_failure_keeps_job_ready_when_a_sibling_shipped(monkeypatch):
    async def boom(render_id, bucket_name, total_frames=None):
        raise main.PipelineError("render_fatal", "lambda died", "render")
    monkeypatch.setattr(main, "_poll_remotion_render", boom)

    clip = _reattach_clip()
    job = {"status": "rendering",
           "clips": [{"clip_id": "c0", "status": "ready", "render_url": "https://cdn/a.mp4"},
                     clip]}
    _run(main._reattach_one_render(job, clip, 1))
    assert clip["status"] == "failed"
    assert job["status"] == "ready"                     # one playable clip → job is ready


def test_reattach_failure_spares_job_while_sibling_renders(monkeypatch):
    """Mirror of the ready branch's guard: finalize ONLY once no clip is rendering."""
    async def boom(render_id, bucket_name, total_frames=None):
        raise RuntimeError("transport exploded")        # generic-exception branch too
    monkeypatch.setattr(main, "_poll_remotion_render", boom)

    clip = _reattach_clip()
    job = {"status": "rendering",
           "clips": [clip,
                     {"clip_id": "c2", "status": "rendering",
                      "render_started_at": time.time()}]}
    _run(main._reattach_one_render(job, clip, 1))
    assert clip["status"] == "failed" and clip["error"] == "internal_error"
    assert job["status"] == "rendering"                 # sibling still owns the job


def test_reattach_stale_generation_never_finalizes(monkeypatch):
    """A superseded attempt (newer render_gen) must not write clip OR job state."""
    async def boom(render_id, bucket_name, total_frames=None):
        raise main.PipelineError("render_fatal", "stale", "render")
    monkeypatch.setattr(main, "_poll_remotion_render", boom)

    clip = _reattach_clip(render_gen=2)                 # a newer attempt took over
    job = {"status": "rendering", "clips": [clip]}
    _run(main._reattach_one_render(job, clip, 1))       # my_gen=1 is stale
    assert clip["status"] == "rendering" and job["status"] == "rendering"


# ---------------------------------------------------------------------------
# 6. Higgsfield timeout is not negative-cached
# ---------------------------------------------------------------------------

def test_timed_out_sentinel_is_falsy_and_distinct():
    assert not H.TIMED_OUT                              # every `if not url` keeps working
    assert H.TIMED_OUT is not None                      # but identity-distinguishable


def test_generate_broll_timeout_returns_sentinel(monkeypatch):
    monkeypatch.setattr(H, "CONFIGURED", True)

    async def submit_ok(model_id, body):
        return "req-1"

    async def poll_timeout(request_id, deadline):
        return H.TIMED_OUT                              # deadline expired mid-chain
    monkeypatch.setattr(H, "_submit", submit_ok)
    monkeypatch.setattr(H, "_poll_request", poll_timeout)
    out = _run(H.generate_broll("slow but fine query"))
    assert out is H.TIMED_OUT
    # definitive rejection is still a plain None (test_higgsfield covers the rest)
    async def poll_failed(request_id, deadline):
        return None
    monkeypatch.setattr(H, "_poll_request", poll_failed)
    assert _run(H.generate_broll("rejected query")) is None


def test_generate_still_timeout_returns_sentinel(monkeypatch):
    monkeypatch.setattr(H, "CONFIGURED", True)

    async def submit_ok(model_id, body):
        return "req-1"

    async def poll_timeout(request_id, deadline):
        return H.TIMED_OUT
    monkeypatch.setattr(H, "_submit", submit_ok)
    monkeypatch.setattr(H, "_poll_request", poll_timeout)
    assert _run(H.generate_still("slow still")) is H.TIMED_OUT


def test_generate_broll_still_propagates_timeout(monkeypatch):
    """The still-tier wrapper must not launder the sentinel into a cache-poisoning
    None when no FAL fallback exists."""
    monkeypatch.setattr(main.higgsfield_mod, "CONFIGURED", True)
    monkeypatch.setattr(main, "FAL_KEY", "")

    async def st(query):
        return main.higgsfield_mod.TIMED_OUT
    monkeypatch.setattr(main.higgsfield_mod, "generate_still", st)
    assert _run(main._generate_broll_still("q")) is main.higgsfield_mod.TIMED_OUT


def test_timeout_not_negative_cached_definitive_failure_is(monkeypatch):
    """A timed-out generation is retried on the next job (the budget was the problem);
    a definitive failure still closes the door after one attempt."""
    monkeypatch.setattr(main, "PEXELS_KEY", "px")
    monkeypatch.setattr(main.higgsfield_mod, "CONFIGURED", True)
    main._broll_url_cache.clear(); main._broll_gen_failed.clear()

    calls = []

    async def no_candidates(query, n):
        return []

    async def timing_out(cue, duration_s=5):
        calls.append(cue)
        return main.higgsfield_mod.TIMED_OUT
    monkeypatch.setattr(main, "_fetch_pexels_candidates", no_candidates)
    monkeypatch.setattr(main.higgsfield_mod, "generate_broll", timing_out)

    # Fresh EDL per pass: the tier pass mutates cue dicts in place after a failed
    # resolve, and the negative cache under test is keyed on the QUERY, not the dict.
    def edl():
        return {"broll": [{"broll_query": "slow query", "cue_text": "c", "source": "stock"}]}

    _run(main._resolve_broll(edl()))
    _run(main._resolve_broll(edl()))                    # retried — no poison
    assert len(calls) == 2
    assert not main._broll_gen_failed                   # cache never touched

    async def hard_no(cue, duration_s=5):
        calls.append(cue)
        return None                                     # definitive rejection
    monkeypatch.setattr(main.higgsfield_mod, "generate_broll", hard_no)
    _run(main._resolve_broll(edl()))                    # third call: definitive no → cached
    _run(main._resolve_broll(edl()))                    # fourth pass: cache holds
    assert len(calls) == 3
    assert main._broll_gen_failed                       # now (and only now) poisoned
    main._broll_gen_failed.clear(); main._broll_url_cache.clear()
