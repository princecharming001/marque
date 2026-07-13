"""Editing-pipeline hardening, round 2 — regression tests for the 2026-07-12
"editing builds are failing" fix batch. Every test runs KEYLESS (same seams as
test_editor_hardening.py).

Covered fixes:
  1. Job-level watchdog anchors at the latest progress marker (stage_started_at),
     not created_at — confirming after a long brief review no longer insta-fails.
  2. The sweep spares a job while one of its clips is actively rendering inside
     the clip watchdog window (multi-clip jobs legitimately exceed budget*2).
  3. Retry stamps the stage (_mark_stage), so the fresh attempt's ETA/watchdog
     don't inherit the failed run's stale clock.
  4. render_started_at is re-stamped when the render semaphore is ACQUIRED —
     queue time under a burst never counts against the render watchdog.
  5. _poll_remotion_render tolerates transient bridge-poll failures (a missed
     observation is not a failed render); only 3 consecutive misses fail.
  6. Submit props travel via stdin (argv "-"), never a >128KB argv string.
  7. strip_fillers cuts discourse markers ONLY at clause boundaries — never
     mid-sentence content words ("turn right here").
  8. safe_default_edl actually strips fillers (drops present), matching the
     "fillers stripped" copy the app shows.
"""
import asyncio
import json
import time
import uuid

import main
from app.edl import safe_default_edl, strip_fillers


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
# 1. Watchdog anchor — parked-on-user time never counts as pipeline time
# ---------------------------------------------------------------------------

def test_confirm_after_long_brief_review_survives_sweep():
    """A creator who reviews a brief for >2×budget then confirms must NOT get the
    job killed as pipeline_interrupted on the next poll (the old sweep anchored
    at created_at, which is ancient by then)."""
    job_id = _job(status="editing",
                  created_at=time.time() - 10_000,          # created long ago
                  stage_started_at=time.time() - 5)         # but editing JUST began
    main._sweep_stuck_renders(main._clip_jobs)
    assert main._clip_jobs[job_id]["status"] == "editing"   # untouched
    _cleanup(job_id)


def test_sweep_still_fails_restart_stranded_job():
    """Restart recovery intact: a job whose LATEST progress marker is ancient
    (stale stage stamp from before the restart) still gets failed cleanly."""
    job_id = _job(status="analyzing",
                  created_at=time.time() - 10_000,
                  stage_started_at=time.time() - 9_999)
    main._sweep_stuck_renders(main._clip_jobs)
    job = main._clip_jobs[job_id]
    assert job["status"] == "failed" and job["error"] == "pipeline_interrupted"
    _cleanup(job_id)


def test_sweep_handles_missing_stage_stamp():
    """Jobs persisted before stage_started_at existed (or created by old code)
    still sweep on created_at alone."""
    job_id = _job(status="processing", created_at=time.time() - 10_000)
    main._clip_jobs[job_id].pop("stage_started_at", None)
    main._sweep_stuck_renders(main._clip_jobs)
    assert main._clip_jobs[job_id]["status"] == "failed"
    _cleanup(job_id)


# ---------------------------------------------------------------------------
# 2. Sweep spares actively-rendering multi-clip jobs
# ---------------------------------------------------------------------------

def test_sweep_spares_job_while_a_clip_renders_within_budget():
    """Sequential multi-clip renders legitimately push the job stage past
    budget*2 — while a clip is inside ITS OWN watchdog window, the job-level
    sweep must not kill the job."""
    job_id = _job(status="rendering",
                  created_at=time.time() - 10_000,
                  stage_started_at=time.time() - 10_000,
                  clips=[{"clip_id": "c1", "format": "a", "status": "ready",
                          "render_url": "https://cdn/a.mp4"},
                         {"clip_id": "c2", "format": "b", "status": "rendering",
                          "render_started_at": time.time() - 5}])
    main._sweep_stuck_renders(main._clip_jobs)
    job = main._clip_jobs[job_id]
    assert job["status"] == "rendering"                       # spared
    assert job["clips"][1]["status"] == "rendering"           # clip untouched
    _cleanup(job_id)


def test_sweep_kills_job_once_no_clip_is_actively_rendering():
    """Same shape, but the rendering clip is ALSO past its clip budget: the clip
    sweep fails it first, then the job-level sweep terminates the job."""
    job_id = _job(status="rendering",
                  created_at=time.time() - 10_000,
                  stage_started_at=time.time() - 10_000,
                  clips=[{"clip_id": "c2", "format": "b", "status": "rendering",
                          "render_started_at": time.time() - 99_999}])
    main._sweep_stuck_renders(main._clip_jobs)
    job = main._clip_jobs[job_id]
    assert job["clips"][0]["status"] == "failed"
    assert job["clips"][0]["error"] == "render_stalled"
    assert job["status"] == "failed" and job["error"] == "pipeline_interrupted"
    _cleanup(job_id)


# ---------------------------------------------------------------------------
# 3. Retry stamps the stage
# ---------------------------------------------------------------------------

def test_retry_stamps_stage_clock():
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    job_id = _job(status="failed", error="render_stalled",
                  created_at=time.time() - 10_000,
                  stage_started_at=time.time() - 10_000,     # stale stamp from failed run
                  edl={"style": "talking_head", "format_id": "x",
                       "segments": [{"src_in": 0, "src_out": 300}]},
                  clips=[{"clip_id": "c1", "format": "myth-buster", "status": "failed",
                          "error": "render_stalled"}])
    r = client.post(f"/v1/clips/{job_id}/retry")
    assert r.status_code == 200
    job = main._clip_jobs[job_id]
    # The fresh attempt's clock is NOW on both anchors — watchdog and ETA sane.
    assert time.time() - job["created_at"] < 5
    assert time.time() - job["stage_started_at"] < 5
    _cleanup(job_id)


# ---------------------------------------------------------------------------
# 4. Semaphore queue time never counts against the render watchdog
# ---------------------------------------------------------------------------

def test_render_started_at_restamped_after_semaphore_wait(monkeypatch):
    async def scenario():
        for k in ("REMOTION_SERVE_URL", "REMOTION_ACCESS_KEY", "REMOTION_FUNCTION_NAME"):
            monkeypatch.setattr(main, k, "x")

        async def bridge(*args, timeout_s=None, **kwargs):
            if args[0] == "submit":
                return {"renderId": "r1", "bucketName": "b"}
            return {"done": True, "outputFile": "https://cdn/out.mp4"}
        monkeypatch.setattr(main, "_run_render_bridge", bridge)

        sem = asyncio.Semaphore(1)
        monkeypatch.setattr(main, "_render_semaphore", sem)
        await sem.acquire()                                   # simulate a busy renderer

        job_id = _job(status="rendering",
                      edl={"style": "talking_head", "format_id": "x",
                           "segments": [{"src_in": 0, "src_out": 300}]},
                      clips=[{"clip_id": "c1", "format": "myth-buster", "status": "queued"}])
        task = asyncio.ensure_future(main._render_all_clips(job_id))
        await asyncio.sleep(0.15)                             # task now queued on the semaphore
        clip = main._clip_jobs[job_id]["clips"][0]
        queued_stamp = clip["render_started_at"]
        await asyncio.sleep(0.15)                             # more queue time accrues
        sem.release()                                         # renderer frees up
        await task
        assert clip["status"] == "ready"
        # Re-stamped at acquisition: strictly later than the pre-queue stamp.
        assert clip["render_started_at"] > queued_stamp
        _cleanup(job_id)
    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 5. Poll tolerates transient bridge failures
# ---------------------------------------------------------------------------

def test_poll_survives_two_transient_errors(monkeypatch):
    calls = {"n": 0}
    async def bridge(*args, timeout_s=None, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            return {"_error": "node OOM-killed"}              # transient blips
        return {"done": True, "outputFile": "https://cdn/out.mp4"}
    async def fast_sleep(_): pass
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    out = asyncio.run(main._poll_remotion_render("r1", "b", max_wait_s=60))
    assert out == "https://cdn/out.mp4" and calls["n"] == 3


def test_poll_fails_after_three_consecutive_errors(monkeypatch):
    async def bridge(*args, timeout_s=None, **kwargs):
        return {"_error": "bridge is truly dead"}
    async def fast_sleep(_): pass
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    try:
        asyncio.run(main._poll_remotion_render("r1", "b", max_wait_s=60))
        assert False, "should have raised"
    except main.PipelineError as e:
        assert e.code == "bridge_error"


def test_poll_error_counter_resets_on_success(monkeypatch):
    """error, ok, error, ok... never accumulates to the 3-strike threshold."""
    calls = {"n": 0}
    async def bridge(*args, timeout_s=None, **kwargs):
        calls["n"] += 1
        if calls["n"] % 2 == 1 and calls["n"] < 8:
            return {"_error": "flaky"}
        if calls["n"] >= 8:
            return {"done": True, "outputFile": "https://cdn/out.mp4"}
        return {"overallProgress": calls["n"] / 10.0}
    async def fast_sleep(_): pass
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    out = asyncio.run(main._poll_remotion_render("r1", "b", max_wait_s=60))
    assert out == "https://cdn/out.mp4"


# ---------------------------------------------------------------------------
# 6. Submit props via stdin — no argv size ceiling
# ---------------------------------------------------------------------------

def test_submit_sends_props_via_stdin(monkeypatch):
    monkeypatch.setattr(main, "REMOTION_SERVE_URL", "https://serve.example")
    monkeypatch.setattr(main, "REMOTION_FUNCTION_NAME", "fn")
    seen = {}
    async def bridge(*args, timeout_s=None, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return {"renderId": "r1", "bucketName": "b1"}
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    asyncio.run(main._submit_remotion_render(
        "https://x/v.mov", {"style": "talking_head", "format_id": "x"},
        "myth-buster", "talking_head"))
    assert seen["args"][2] == "-"                             # argv placeholder only
    props = json.loads(seen["kwargs"]["stdin_data"])          # real payload on stdin
    assert props["sourceUrl"] == "https://x/v.mov"
    assert props["edl"]["total_frames"] >= 1


def test_run_render_bridge_pipes_stdin(monkeypatch):
    seen = {}
    class FakeProc:
        returncode = 0
        async def communicate(self, input=None):
            seen["stdin"] = input
            return (b'{"ok": true}', b"")
        def kill(self): pass
    async def fake_exec(*a, **k):
        seen["exec_kwargs"] = k
        return FakeProc()
    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", fake_exec)
    big = "x" * 300_000                                       # far past MAX_ARG_STRLEN
    out = asyncio.run(main._run_render_bridge("submit", "comp", "-", "0", stdin_data=big))
    assert out == {"ok": True}
    assert seen["stdin"] == big.encode()
    assert seen["exec_kwargs"]["stdin"] is not None


# ---------------------------------------------------------------------------
# 7. Filler stripping — clause-boundary discourse markers only
# ---------------------------------------------------------------------------

def test_mid_sentence_discourse_markers_are_content():
    words = [
        {"word": "turn", "start_ms": 0, "end_ms": 200},
        {"word": "right", "start_ms": 210, "end_ms": 400},    # content: a direction!
        {"word": "here", "start_ms": 410, "end_ms": 600},
        {"word": "I", "start_ms": 610, "end_ms": 700},
        {"word": "feel", "start_ms": 710, "end_ms": 900},
        {"word": "like", "start_ms": 910, "end_ms": 1050},    # content: "feel like"
        {"word": "it", "start_ms": 1060, "end_ms": 1150},
        {"word": "works", "start_ms": 1160, "end_ms": 1400},
    ]
    kept, drops = strip_fillers(words)
    assert [w["word"] for w in kept] == ["turn", "right", "here", "I", "feel", "like", "it", "works"]
    assert not any(d.reason == "filler" for d in drops)


def test_clause_initial_discourse_marker_is_cut():
    words = [
        {"word": "hello", "start_ms": 0, "end_ms": 500},
        {"word": "so", "start_ms": 1000, "end_ms": 1200},     # after a 500ms pause → filler
        {"word": "topic", "start_ms": 1210, "end_ms": 1500},
    ]
    kept, drops = strip_fillers(words)
    assert [w["word"] for w in kept] == ["hello", "topic"]
    assert any(d.reason == "filler" for d in drops)


def test_take_opening_discourse_marker_is_cut():
    words = [
        {"word": "So", "start_ms": 0, "end_ms": 200},         # first word of the take
        {"word": "muscles", "start_ms": 210, "end_ms": 600},
    ]
    kept, _ = strip_fillers(words)
    assert [w["word"] for w in kept] == ["muscles"]


def test_filler_chain_is_cut_whole():
    """'um, so, ...' — the marker right after a filler goes too, even with no pause."""
    words = [
        {"word": "um", "start_ms": 0, "end_ms": 100},
        {"word": "so", "start_ms": 110, "end_ms": 250},
        {"word": "topic", "start_ms": 260, "end_ms": 500},
    ]
    kept, drops = strip_fillers(words)
    assert [w["word"] for w in kept] == ["topic"]
    assert sum(1 for d in drops if d.reason == "filler") == 2


def test_um_uh_always_cut_even_mid_sentence():
    words = [
        {"word": "the", "start_ms": 0, "end_ms": 150},
        {"word": "um", "start_ms": 160, "end_ms": 250},
        {"word": "answer", "start_ms": 260, "end_ms": 600},
    ]
    kept, _ = strip_fillers(words)
    assert [w["word"] for w in kept] == ["the", "answer"]


# ---------------------------------------------------------------------------
# 8. Safe default actually strips fillers
# ---------------------------------------------------------------------------

def test_safe_default_edl_includes_filler_drops():
    words = [
        {"word": "um", "start_ms": 0, "end_ms": 200},
        {"word": "hello", "start_ms": 210, "end_ms": 600},
        {"word": "world", "start_ms": 610, "end_ms": 900},
    ]
    edl = safe_default_edl("talking_head", "myth-buster", 300, words)
    data = edl.model_dump()
    assert any(d["reason"] == "filler" for d in data["drops"])          # footage cut
    assert all(c["word"] != "um" for c in data["captions"])             # caption gone
    assert len(data["captions"]) == 2


# ---------------------------------------------------------------------------
# 9. TTL sweep — restored / in-flight sessions are never re-evicted
# ---------------------------------------------------------------------------

def test_ttl_sweep_spares_freshly_restored_old_job():
    """A session restored from Supabase days after creation must get a fresh
    in-memory lease — the old sweep evicted it on the next poll (created_at is
    days old), orphaning any in-flight re-render's writes."""
    job_id = _job(status="ready",
                  created_at=time.time() - 3 * 86_400,
                  restored_at=time.time() - 30)
    main._sweep_ttl_jobs(main._clip_jobs)
    assert job_id in main._clip_jobs
    _cleanup(job_id)


def test_ttl_sweep_spares_job_with_render_in_flight():
    job_id = _job(status="ready",
                  created_at=time.time() - 3 * 86_400,
                  clips=[{"clip_id": "c1", "format": "a", "status": "rendering",
                          "render_started_at": time.time() - 5}])
    main._sweep_ttl_jobs(main._clip_jobs)
    assert job_id in main._clip_jobs
    _cleanup(job_id)


def test_ttl_sweep_still_evicts_stale_terminal_jobs():
    job_id = _job(status="ready", created_at=time.time() - 3 * 86_400,
                  clips=[{"clip_id": "c1", "format": "a", "status": "ready"}])
    main._sweep_ttl_jobs(main._clip_jobs)
    assert job_id not in main._clip_jobs
    assert job_id in main._expired_job_ids


# ---------------------------------------------------------------------------
# 10. Storage outage ≠ session expired
# ---------------------------------------------------------------------------

def test_restore_unavailable_storage_returns_503_not_404(monkeypatch):
    from fastapi.testclient import TestClient
    import supabase_persistence as sp

    class FakeSupa:
        async def load_clip_job(self, job_id):
            return sp.UNAVAILABLE                     # transport down, NOT absent
    monkeypatch.setattr(main, "_supabase_client", FakeSupa())
    client = TestClient(main.app)
    r = client.get(f"/v1/clips/{uuid.uuid4()}")
    assert r.status_code == 503                       # retryable, session NOT declared dead
    assert "unavailable" in r.json()["detail"]


def test_restore_definitively_absent_still_404s(monkeypatch):
    from fastapi.testclient import TestClient

    class FakeSupa:
        async def load_clip_job(self, job_id):
            return None                               # DB answered: no such session
    monkeypatch.setattr(main, "_supabase_client", FakeSupa())
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")   # disable demo-job synthesis
    client = TestClient(main.app)
    r = client.get(f"/v1/clips/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 11. remove_overlays: a garbage range must not wipe everything
# ---------------------------------------------------------------------------

def test_remove_overlays_invalid_range_is_skipped_not_wipe_all():
    from app.edl import apply_edl_ops
    edl = {"style": "talking_head", "format_id": "x",
           "segments": [{"src_in": 0, "src_out": 600}],
           "overlays": [{"type": "punch_in", "src_in": 30, "src_out": 90,
                         "scale": 1.08, "text": ""},
                        {"type": "punch_in", "src_in": 200, "src_out": 260,
                         "scale": 1.08, "text": ""}]}
    new_edl, results = apply_edl_ops(
        edl, [{"type": "remove_overlays", "kind": "punch_in",
               "start_frame": 500, "end_frame": 100}])   # inverted → invalid
    assert results[0]["applied"] is False
    assert "range" in results[0]["reason"]
    assert len(new_edl["overlays"]) == 2                  # nothing wiped


# ---------------------------------------------------------------------------
# 12. Same-tick double confirm — second one 409s
# ---------------------------------------------------------------------------

def test_double_confirm_same_tick_409s(monkeypatch):
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "k")

    async def slow_edit(job_id, words):                       # never flips status itself
        return None
    monkeypatch.setattr(main, "_run_edit", slow_edit)

    job_id = _job(status="brief_ready", edit_brief={"strategy": "keep it"},
                  clips=[{"clip_id": "c1", "format": "myth-buster", "status": "queued"}])
    r1 = client.post(f"/v1/clips/{job_id}/confirm", json={})
    assert r1.status_code == 200
    # Status was set SYNCHRONOUSLY by the endpoint, so an immediate second confirm
    # hits the 409 guard even though _run_edit hasn't run a single instruction yet.
    assert main._clip_jobs[job_id]["status"] == "editing"
    r2 = client.post(f"/v1/clips/{job_id}/confirm", json={})
    assert r2.status_code == 409
    _cleanup(job_id)


# ---------------------------------------------------------------------------
# 13. Retry guards + auto-confirm routing
# ---------------------------------------------------------------------------

def test_retry_409s_during_analysis():
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    for status in ("analyzing", "processing"):
        job_id = _job(status=status)
        r = client.post(f"/v1/clips/{job_id}/retry")
        assert r.status_code == 409, f"retry during {status} must 409"
        _cleanup(job_id)


def test_retry_of_auto_confirm_job_reruns_auto_pipeline(monkeypatch):
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    called = {}

    async def fake_auto(job_id):
        called["auto"] = job_id
    async def fake_full(job_id):
        called["full"] = job_id
    monkeypatch.setattr(main, "_run_auto_pipeline", fake_auto)
    monkeypatch.setattr(main, "_run_pipeline", fake_full)

    job_id = _job(status="failed", error="transcribe_timeout", auto_confirm=True,
                  toggles={"broll": True},
                  clips=[{"clip_id": "c1", "format": "myth-buster", "status": "failed",
                          "error": "transcribe_timeout"}])
    r = client.post(f"/v1/clips/{job_id}/retry")
    assert r.status_code == 200
    assert r.json()["status"] == "processing"
    assert called.get("auto") == job_id and "full" not in called
    _cleanup(job_id)


# ---------------------------------------------------------------------------
# 14. Tweak race re-check after the LLM await
# ---------------------------------------------------------------------------

def test_tweak_commit_rechecks_state_after_llm_await(monkeypatch):
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    job_id = _job(status="ready",
                  edl=main._mock_edl("talking_head", {"formatId": "myth-buster"}),
                  words=main._mock_words({"hook": "a b c"}),
                  clips=[{"clip_id": "c1", "format": "myth-buster", "status": "ready",
                          "render_url": "https://cdn/x.mp4"}])

    async def racing_llm(*a, **k):
        # While the tweak awaited the LLM, a confirm/retry started a pipeline.
        main._clip_jobs[job_id]["status"] = "editing"
        return {"reply": "sure", "ops": [{"type": "set_caption_style", "style": "karaoke"}]}
    monkeypatch.setattr(main, "anthropic_json", racing_llm)

    r = client.post(f"/v1/clips/{job_id}/tweak",
                    json={"clip_id": "c1", "instruction": "karaoke captions"})
    assert r.status_code == 409                              # NOT a mixed-state commit
    assert main._clip_jobs[job_id]["edl"].get("caption_style") != "karaoke"
    _cleanup(job_id)


# ---------------------------------------------------------------------------
# 15. Superseded render bails at semaphore acquisition (no wasted Lambda spend)
# ---------------------------------------------------------------------------

def test_superseded_render_skips_lambda_submit(monkeypatch):
    async def scenario():
        for k in ("REMOTION_SERVE_URL", "REMOTION_ACCESS_KEY", "REMOTION_FUNCTION_NAME"):
            monkeypatch.setattr(main, k, "x")
        submits = {"n": 0}

        async def bridge(*args, timeout_s=None, **kwargs):
            if args[0] == "submit":
                submits["n"] += 1
                return {"renderId": "r1", "bucketName": "b"}
            return {"done": True, "outputFile": "https://cdn/out.mp4"}
        monkeypatch.setattr(main, "_run_render_bridge", bridge)

        sem = asyncio.Semaphore(1)
        monkeypatch.setattr(main, "_render_semaphore", sem)
        await sem.acquire()

        job_id = _job(status="rendering",
                      edl={"style": "talking_head", "format_id": "x",
                           "segments": [{"src_in": 0, "src_out": 300}]},
                      clips=[{"clip_id": "c1", "format": "myth-buster", "status": "queued"}])
        task = asyncio.ensure_future(main._render_all_clips(job_id))
        await asyncio.sleep(0.1)                              # task queued on semaphore
        clip = main._clip_jobs[job_id]["clips"][0]
        main._bump_render_gen(clip)                           # watchdog/retry superseded it
        sem.release()
        await task
        assert submits["n"] == 0                              # never reached Lambda
        _cleanup(job_id)
    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 16. Duet react schedule synthesized by the assembler
# ---------------------------------------------------------------------------

def test_assemble_duet_synthesizes_react_schedule():
    from app.edl import assemble_edl, build_render_plan
    words = [{"word": f"w{i}", "start_ms": i * 400, "end_ms": i * 400 + 350}
             for i in range(30)]                              # 12s take
    edl = assemble_edl({}, words, "duet_split", "myth-buster")
    data = edl.model_dump()
    sched = data["react_schedule"]
    assert sched, "duet_split must get a play/freeze schedule"
    assert sched[0]["state"] == "play" and sched[0]["audio_gain"] == 1.0
    assert (sched[0]["src_out"] - sched[0]["src_in"]) <= 75   # ≤2.5s claim
    assert all(w["state"] == "freeze" for w in sched[1:])
    # Every window survives the plan mapping (aligned to kept intervals).
    warnings = []
    plan = build_render_plan(data, warnings)
    assert not any("react_window_dropped" in w for w in warnings)
    assert len(plan["react_schedule"]) == len(sched)


def test_assemble_normalizes_highlight_words():
    from app.edl import assemble_edl
    words = [{"word": "growth", "start_ms": 0, "end_ms": 400}]
    edl = assemble_edl({"caption_plan": {"highlight_words": ["A.I.", "Growth!", "$$$"]}},
                       words, "talking_head", "myth-buster")
    hw = edl.model_dump()["caption_options"]["highlight_words"]
    assert hw == ["ai", "growth"]                             # renderer-matchable forms only


# ---------------------------------------------------------------------------
# 17. Zombie pipeline task cannot clobber a retry's fresh state
# ---------------------------------------------------------------------------

def test_zombie_pipeline_cannot_clobber_new_owner(monkeypatch):
    """asyncio never cancels a watchdog-failed pipeline task. When it wakes up
    after a retry has taken ownership (pipeline_gen bumped), every job-level
    write it would make — edl install, status, error — must be dropped."""
    async def scenario():
        monkeypatch.setattr(main, "ASSEMBLY_KEY", "k")

        job_id = _job(status="editing",
                      words=main._mock_words({"hook": "a b c"}),
                      clips=[{"clip_id": "c1", "format": "myth-buster", "status": "editing"}])
        job = main._clip_jobs[job_id]

        async def usurping_resolve(edl, dossier=None, **kw):
            # While the old task resolved b-roll, a retry took over the job.
            main._bump_pipeline_gen(job)
            return edl
        monkeypatch.setattr(main, "_resolve_broll", usurping_resolve)

        await main._run_edit(job_id, job["words"])
        assert job["edl"] is None                    # stale edit NOT installed
        assert job["status"] == "editing"            # tail writes all dropped
        assert "error" not in job or not job["error"]
        _cleanup(job_id)
    asyncio.run(scenario())
