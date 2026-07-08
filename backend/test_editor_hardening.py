"""Editor pipeline hardening gate — the Ralph loop's pass/fail signal.

Every test runs KEYLESS. Live-path behavior is exercised by monkeypatching the
external seams (AssemblyAI submit/poll, the Remotion bridge) so the real pipeline
code runs end-to-end with injected failures. The core contract under test:

    A clip job ALWAYS lands in a terminal state, fast, with a structured error —
    never a silent hang, never "ready" without a playable render_url.
"""
import asyncio
import json
import time
import uuid

from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)

SCRIPT = {"hook": "Test hook", "body": "Body text here", "cta": "Follow", "formatId": "myth-buster"}


def seed_clip_job(source_url="mock://source", script=None, style="talking_head",
                  formats=("myth-buster",), edit_prefs=None, **extra):
    """Seed a keyless MOCK-READY clip job directly (endpoint is analyze-first now; these
    white-box tests drive tweak/rerender internals on a ready job). Mirrors the old
    keyless create: edl from _mock_edl + prefs, words from _mock_words."""
    script = script if script is not None else dict(SCRIPT)
    edit_prefs = edit_prefs or {}
    job_id = str(uuid.uuid4())
    clips = [{"clip_id": str(uuid.uuid4()), "format": f, "status": "ready",
              "render_url": source_url} for f in formats]
    job = {
        "job_id": job_id, "source_id": "src1", "status": "mock_ready", "clips": clips,
        "script": script, "style": style, "brand": {}, "media_context": "",
        "source_url": source_url, "error": None, "edit_prefs": edit_prefs,
        "react_source_url": "", "react_credit_label": "",
        "edl": main._apply_edit_prefs(main._mock_edl(style, script), edit_prefs),
        "words": main._mock_words(script), "edl_history": [], "tweaks": [],
        "custom_instructions": "", "created_at": time.time(),
    }
    job.update(extra)
    main._clip_jobs[job_id] = job
    return job_id


def _make_live_job(monkeypatch, **env):
    """Bare live-path clip job (status 'queued', edl None) for driving _run_pipeline."""
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "test-key")
    for k, v in env.items():
        monkeypatch.setattr(main, k, v)
    job_id = str(uuid.uuid4())
    main._clip_jobs[job_id] = {
        "job_id": job_id, "source_id": "src1", "status": "queued",
        "clips": [{"clip_id": str(uuid.uuid4()), "format": "myth-buster", "status": "queued"}],
        "script": dict(SCRIPT), "style": "talking_head", "brand": {}, "media_context": "",
        "source_url": "https://example.com/video.mov", "edl": None, "error": None,
        "edit_prefs": {}, "react_source_url": "", "react_credit_label": "",
        "words": [], "edl_history": [], "tweaks": [], "custom_instructions": "",
        "created_at": time.time(),
    }
    return job_id


def _run_pipeline_sync(job_id):
    asyncio.run(main._run_pipeline(job_id))


def _mock_transcript_ok(monkeypatch):
    async def submit(url): return "tid-1"
    async def poll(tid, max_wait_s=None):
        return {"words": main._mock_words(SCRIPT), "auto_highlights": []}
    monkeypatch.setattr(main, "_submit_transcription", submit)
    monkeypatch.setattr(main, "_poll_transcription", poll)


def _assert_terminal(job):
    assert job["status"] in ("ready", "failed", "mock_ready"), job["status"]
    for c in job["clips"]:
        assert c["status"] in ("ready", "failed"), c["status"]
        if c["status"] == "ready":
            assert c.get("render_url"), "ready clip must have a render_url"


# ---------------------------------------------------------------------------
# Source validation — bad URLs must fail in seconds, not minutes
# ---------------------------------------------------------------------------

def test_pipeline_bad_source_url_fails_fast(monkeypatch):
    job_id = _make_live_job(monkeypatch)
    async def bad_probe(url):
        raise main.PipelineError("source_unreachable", "probe failed", "transcribe")
    monkeypatch.setattr(main, "_validate_source_url", bad_probe)
    start = time.time()
    _run_pipeline_sync(job_id)
    assert time.time() - start < 2
    job = main._clip_jobs[job_id]
    assert job["status"] == "failed"
    assert job["error"] == "source_unreachable"
    assert job["error_stage"] == "transcribe"
    _assert_terminal(job)


def test_validate_source_url_skips_non_http():
    # data:/file: URLs (and mock paths) skip the probe entirely.
    asyncio.run(main._validate_source_url("file:///tmp/x.mov"))  # no raise


def test_pipeline_broll_resolve_failure_is_a_warning_not_a_failure(monkeypatch):
    # B-05: a b-roll resolve blow-up must degrade to a warning, never fail the clip job.
    job_id = _renderable_job(monkeypatch)

    async def boom(edl):
        raise RuntimeError("pexels exploded")
    monkeypatch.setattr(main, "_resolve_broll", boom)

    async def bridge(*args, timeout_s=None):
        if args[0] == "submit":
            return {"renderId": "r1", "bucketName": "b"}
        return {"done": True, "outputFile": "https://cdn/out.mp4"}
    async def fast_sleep(_):
        return None
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)

    _run_pipeline_sync(job_id)
    job = main._clip_jobs[job_id]
    assert job["status"] == "ready"                       # NOT failed by the b-roll blow-up
    assert any("broll_unresolved" in w for c in job["clips"] for w in c.get("warnings", []))


# ---------------------------------------------------------------------------
# Transcription failures — loud and structured, never silently empty
# ---------------------------------------------------------------------------

def test_transcribe_submit_failed_structured(monkeypatch):
    job_id = _make_live_job(monkeypatch)
    async def ok_probe(url): pass
    async def submit(url): return None
    monkeypatch.setattr(main, "_validate_source_url", ok_probe)
    monkeypatch.setattr(main, "_submit_transcription", submit)
    _run_pipeline_sync(job_id)
    job = main._clip_jobs[job_id]
    assert job["status"] == "failed" and job["error"] == "transcribe_submit_failed"
    _assert_terminal(job)


def test_transcribe_error_structured(monkeypatch):
    job_id = _make_live_job(monkeypatch)
    async def ok_probe(url): pass
    async def submit(url): return "tid-1"
    async def poll(tid, max_wait_s=None):
        raise main.PipelineError("transcribe_failed", "audio unintelligible", "transcribe")
    monkeypatch.setattr(main, "_validate_source_url", ok_probe)
    monkeypatch.setattr(main, "_submit_transcription", submit)
    monkeypatch.setattr(main, "_poll_transcription", poll)
    _run_pipeline_sync(job_id)
    job = main._clip_jobs[job_id]
    assert job["error"] == "transcribe_failed"
    assert "unintelligible" in job.get("error_detail", "")
    _assert_terminal(job)


def test_transcribe_timeout_structured(monkeypatch):
    job_id = _make_live_job(monkeypatch)
    async def ok_probe(url): pass
    async def submit(url): return "tid-1"
    async def poll(tid, max_wait_s=None):
        raise main.PipelineError("transcribe_timeout", "no transcript after 300s", "transcribe")
    monkeypatch.setattr(main, "_validate_source_url", ok_probe)
    monkeypatch.setattr(main, "_submit_transcription", submit)
    monkeypatch.setattr(main, "_poll_transcription", poll)
    _run_pipeline_sync(job_id)
    assert main._clip_jobs[job_id]["error"] == "transcribe_timeout"


def test_poll_transcription_empty_words_raises(monkeypatch):
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "k")
    class FakeResp:
        status_code = 200
        def json(self):
            return {"status": "completed", "words": []}
    async def fake_get(self, url, headers=None):
        return FakeResp()
    async def fast_sleep(_): pass
    monkeypatch.setattr(main.httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    try:
        asyncio.run(main._poll_transcription("tid", max_wait_s=5))
        assert False, "should have raised"
    except main.PipelineError as e:
        assert e.code == "transcribe_failed"


# ---------------------------------------------------------------------------
# Render failures — every bridge/Lambda failure mode maps to a code
# ---------------------------------------------------------------------------

def _renderable_job(monkeypatch):
    """Job that reaches the render stage: transcript mocked ok, EDL via mock LLM
    (keyless anthropic path is impossible, so mock extract via edl fallback)."""
    job_id = _make_live_job(monkeypatch,
                            REMOTION_SERVE_URL="https://serve.example",
                            REMOTION_ACCESS_KEY="ak",
                            REMOTION_FUNCTION_NAME="fn")
    async def ok_probe(url): pass
    monkeypatch.setattr(main, "_validate_source_url", ok_probe)
    _mock_transcript_ok(monkeypatch)
    # Keyless ANTHROPIC → anthropic() raises HTTPException → safe_default_edl path;
    # patch anthropic to raise cleanly so the EDL fallback engages deterministically.
    async def no_llm(*a, **k):
        raise main.HTTPException(status_code=502, detail="keyless")
    monkeypatch.setattr(main, "anthropic", no_llm)
    return job_id


def test_render_fatal_propagates_bridge_error(monkeypatch):
    job_id = _renderable_job(monkeypatch)
    calls = {"n": 0}
    async def bridge(*args, timeout_s=None):
        if args[0] == "submit":
            return {"renderId": "r1", "bucketName": "b"}
        return {"fatalErrorEncountered": True, "errors": [{"message": "boom composition"}]}
    async def fast_sleep(_): pass
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    _run_pipeline_sync(job_id)
    job = main._clip_jobs[job_id]
    clip = job["clips"][0]
    assert clip["status"] == "failed" and clip["error"] == "render_fatal"
    assert "boom" in clip.get("error_detail", "")
    assert job["status"] == "failed"          # all clips failed → job failed
    _assert_terminal(job)


def test_render_submit_bridge_error(monkeypatch):
    job_id = _renderable_job(monkeypatch)
    async def bridge(*args, timeout_s=None):
        return {"_error": "node exploded"}
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    _run_pipeline_sync(job_id)
    clip = main._clip_jobs[job_id]["clips"][0]
    assert clip["status"] == "failed" and clip["error"] == "bridge_error"
    assert "exploded" in clip.get("error_detail", "")


def test_render_no_output_structured(monkeypatch):
    job_id = _renderable_job(monkeypatch)
    async def bridge(*args, timeout_s=None):
        if args[0] == "submit":
            return {"renderId": "r1", "bucketName": "b"}
        return {"done": True}                  # done but no outputFile
    async def fast_sleep(_): pass
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    _run_pipeline_sync(job_id)
    clip = main._clip_jobs[job_id]["clips"][0]
    assert clip["error"] == "render_no_output"
    _assert_terminal(main._clip_jobs[job_id])


def test_render_poll_stall_detection(monkeypatch):
    async def bridge(*args, timeout_s=None):
        return {"overallProgress": 0.4}        # frozen forever
    async def fast_sleep(_): pass
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(main, "RENDER_STALL_S", 0)     # stall immediately
    try:
        asyncio.run(main._poll_remotion_render("r1", "b", max_wait_s=60))
        assert False
    except main.PipelineError as e:
        assert e.code == "render_stalled"


def test_render_poll_timeout(monkeypatch):
    async def bridge(*args, timeout_s=None):
        return {"overallProgress": 0.1}
    async def fast_sleep(_): pass
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(main, "RENDER_STALL_S", 10 ** 9)   # never stall
    try:
        asyncio.run(main._poll_remotion_render("r1", "b", max_wait_s=0))
        assert False
    except main.PipelineError as e:
        assert e.code == "render_timeout"


def test_bridge_subprocess_timeout(monkeypatch):
    class FakeProc:
        returncode = 0
        async def communicate(self):
            await asyncio.sleep(3600)
        def kill(self): self.killed = True
    async def fake_exec(*a, **k): return FakeProc()
    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", fake_exec)
    out = asyncio.run(main._run_render_bridge("poll", "r", "b", timeout_s=0.05))
    assert "_error" in out and "timed out" in out["_error"]


# ---------------------------------------------------------------------------
# Watchdog + terminal-state invariant
# ---------------------------------------------------------------------------

def test_watchdog_sweeps_stuck_rendering():
    job_id = "stuck-job-test"
    main._clip_jobs[job_id] = {
        "job_id": job_id, "status": "ready", "created_at": time.time(),
        "clips": [{"clip_id": "c1", "format": "myth-buster", "status": "rendering",
                   "render_started_at": time.time() - 99999}],
        "edl": {}, "words": [], "edl_history": [], "tweaks": [],
    }
    r = client.get(f"/v1/clips/{job_id}")
    clip = r.json()["clips"][0]
    assert clip["status"] == "failed" and clip["error"] == "render_stalled"
    main._clip_jobs.pop(job_id, None)


def test_watchdog_fails_ancient_inflight_job():
    job_id = "ancient-job-test"
    main._clip_jobs[job_id] = {
        "job_id": job_id, "status": "transcribing", "created_at": time.time() - 999999,
        "clips": [{"clip_id": "c1", "format": "myth-buster", "status": "transcribing"}],
        "edl": None, "words": [], "edl_history": [], "tweaks": [],
    }
    # sweep_ttl would evict a >24h job first; use a fresh-enough timestamp instead
    main._clip_jobs[job_id]["created_at"] = time.time() - (main.RENDER_WATCHDOG_S * 2 + 60)
    r = client.get(f"/v1/clips/{job_id}")
    body = r.json()
    assert body["status"] == "failed" and body["error"] == "render_stalled"
    main._clip_jobs.pop(job_id, None)


def test_rerender_never_strands(monkeypatch):
    # Mock job (keyless) → tweak → simulate render crash mid-flight.
    job_id = seed_clip_job(source_url="mock://source")
    job = main._clip_jobs[job_id]
    clip_id = job["clips"][0]["clip_id"]
    async def exploding_submit(*a, **k):
        raise RuntimeError("mid-flight death")
    monkeypatch.setattr(main, "_submit_remotion_render", exploding_submit)
    job["clips"][0]["render_url"] = "https://prev.example/v.mp4"
    my_gen = main._bump_render_gen(job["clips"][0])
    asyncio.run(main._rerender_clip(job_id, clip_id, my_gen))
    clip = job["clips"][0]
    assert clip["status"] == "ready"                       # prev URL restored
    assert clip["render_url"] == "https://prev.example/v.mp4"


def test_rerender_no_prev_url_fails_structured(monkeypatch):
    job_id = seed_clip_job(source_url="mock://source")
    job = main._clip_jobs[job_id]
    clip = job["clips"][0]
    clip["render_url"] = None
    async def exploding_submit(*a, **k):
        raise main.PipelineError("render_submit_failed", "no bridge", "render")
    monkeypatch.setattr(main, "_submit_remotion_render", exploding_submit)
    my_gen = main._bump_render_gen(clip)
    asyncio.run(main._rerender_clip(job_id, clip["clip_id"], my_gen))
    assert clip["status"] == "failed" and clip["error"] == "render_submit_failed"


def test_stale_rerender_cannot_overwrite_a_newer_one(monkeypatch):
    # F7: a stale render task (superseded by a newer tweak/retry while it was
    # still in flight — e.g. after a watchdog marked it failed but the original
    # asyncio task keeps running to completion) must not overwrite the newer
    # attempt's result when it finally finishes.
    job_id = seed_clip_job(source_url="mock://source")
    job = main._clip_jobs[job_id]
    clip = job["clips"][0]
    clip_id = clip["clip_id"]

    # Start the STALE attempt (captures gen=1) but don't let it finish yet —
    # simulate it being slow by NOT awaiting it until after a newer one starts.
    stale_gen = main._bump_render_gen(clip)

    async def slow_submit_stale(*a, **k):
        return {"render_id": "stale-render", "bucket_name": "b"}
    async def poll_stale(*a, **k):
        return "https://stale.example/v.mp4"

    # A NEWER attempt (e.g. a retry) starts and bumps the generation while the
    # stale one is "still in flight" (we just haven't awaited it yet).
    newer_gen = main._bump_render_gen(clip)
    assert newer_gen != stale_gen

    monkeypatch.setattr(main, "_submit_remotion_render", slow_submit_stale)
    monkeypatch.setattr(main, "_poll_remotion_render", poll_stale)
    # Now the stale task finally "completes" — it must write NOTHING.
    asyncio.run(main._rerender_clip(job_id, clip_id, stale_gen))
    assert clip.get("render_url") != "https://stale.example/v.mp4"

    # The newer attempt completing normally DOES write.
    async def poll_fresh(*a, **k):
        return "https://fresh.example/v.mp4"
    monkeypatch.setattr(main, "_poll_remotion_render", poll_fresh)
    asyncio.run(main._rerender_clip(job_id, clip_id, newer_gen))
    assert clip["render_url"] == "https://fresh.example/v.mp4"
    assert clip["status"] == "ready"


# ---------------------------------------------------------------------------
# Retry endpoint
# ---------------------------------------------------------------------------

def test_retry_404():
    assert client.post("/v1/clips/nonexistent/retry").status_code == 404


def test_retry_409_while_inflight():
    job_id = "inflight-retry-test"
    main._clip_jobs[job_id] = {
        "job_id": job_id, "status": "rendering", "created_at": time.time(),
        "clips": [{"clip_id": "c1", "format": "m", "status": "rendering",
                   "render_started_at": time.time()}],
        "edl": {}, "words": [], "edl_history": [], "tweaks": [],
    }
    assert client.post(f"/v1/clips/{job_id}/retry").status_code == 409
    main._clip_jobs.pop(job_id, None)


def test_retry_mock_job_noop():
    job_id = seed_clip_job(source_url="mock://source")
    out = client.post(f"/v1/clips/{job_id}/retry").json()
    assert out["mode"] == "mock" and out["status"] == "mock_ready"


def test_retry_from_edl_stage(monkeypatch):
    """Failed render, EDL intact → retry re-runs ONLY the render stage."""
    job_id = "edl-retry-test"
    main._clip_jobs[job_id] = {
        "job_id": job_id, "status": "failed", "error": "render_fatal",
        "created_at": time.time(), "source_url": "https://x/v.mov",
        "style": "talking_head", "script": SCRIPT, "brand": {}, "media_context": "",
        "clips": [{"clip_id": "c1", "format": "myth-buster", "status": "failed",
                   "error": "render_fatal"}],
        "edl": {"style": "talking_head", "format_id": "myth-buster"},
        "words": [], "edl_history": [], "tweaks": [],
        "edit_prefs": {}, "react_source_url": None, "react_credit_label": None,
    }
    async def render_all(jid):
        for c in main._clip_jobs[jid]["clips"]:
            c["status"] = "ready"
            c["render_url"] = "https://ok/v.mp4"
    monkeypatch.setattr(main, "_render_all_clips", render_all)
    out = client.post(f"/v1/clips/{job_id}/retry").json()
    assert out["status"] == "rendering"
    # Let the created task run on the TestClient loop
    import anyio
    async def settle():
        await asyncio.sleep(0.05)
    # poll until the task completes
    for _ in range(50):
        if main._clip_jobs[job_id]["status"] == "ready":
            break
        time.sleep(0.02)
    assert main._clip_jobs[job_id]["status"] == "ready"
    assert main._clip_jobs[job_id].get("error") is None
    main._clip_jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# Direct-ops tweak path (the manual editor's contract)
# ---------------------------------------------------------------------------

def test_tweak_direct_ops_bypasses_llm():
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    out = client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id,
        "ops": [{"type": "set_caption_style", "style": "karaoke"}],
    }).json()
    assert out["mode"] == "direct"
    assert any(a["type"] == "set_caption_style" for a in out["applied"])
    edl = client.get(f"/v1/clips/{job_id}").json()["edl"]
    assert edl["caption_style"] == "karaoke"


def test_tweak_direct_ops_unknown_skipped():
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    out = client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id,
        "ops": [{"type": "definitely_not_an_op"}],
    }).json()
    assert out["mode"] == "direct"
    assert not out["applied"]
    assert out["skipped"]


def test_tweak_requires_instruction_or_ops():
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    assert client.post(f"/v1/clips/{job_id}/tweak",
                       json={"clip_id": clip_id}).status_code == 422


def test_get_clip_job_include_words():
    job_id = seed_clip_job(source_url="mock://source")
    without = client.get(f"/v1/clips/{job_id}").json()
    assert "words" not in without
    with_words = client.get(f"/v1/clips/{job_id}?include_words=1").json()
    assert isinstance(with_words["words"], list) and with_words["words"]


# ---------------------------------------------------------------------------
# E11: EDL model extensions — segment_order + audio round-trip
# ---------------------------------------------------------------------------

def _base_edl(**extra):
    return {
        "style": "talking_head", "format_id": "myth-buster",
        "segments": [{"src_in": 0, "src_out": 100}, {"src_in": 100, "src_out": 200},
                     {"src_in": 200, "src_out": 300}],
        "layout": {"style": "talking_head"},
        **extra,
    }


def test_segment_order_roundtrips():
    from app.edl import EDL
    e = EDL(**_base_edl(segment_order=[2, 0, 1]))
    dumped = e.model_dump()
    assert dumped["segment_order"] == [2, 0, 1]
    assert EDL(**dumped).segment_order == [2, 0, 1]      # survives the tweak round-trip


def test_segment_order_must_be_permutation():
    from app.edl import EDL
    for bad in ([0, 0, 1], [0, 1], [0, 1, 5]):
        try:
            EDL(**_base_edl(segment_order=bad))
            assert False, f"accepted invalid order {bad}"
        except ValueError:
            pass


def test_audio_music_and_volume_ranges_roundtrip():
    from app.edl import EDL
    e = EDL(**_base_edl(audio={
        "lufs_target": -14.0,
        "music": {"url": "https://cdn/track.mp3", "volume": 0.2, "duck_voice": True},
        "volume_ranges": [{"src_in": 0, "src_out": 60, "volume": 0.0}],
    }))
    d = e.model_dump()
    assert d["audio"]["music"]["url"] == "https://cdn/track.mp3"
    assert d["audio"]["volume_ranges"][0]["volume"] == 0.0
    assert EDL(**d).audio.music.volume == 0.2


def test_volume_range_rejects_backwards():
    from app.edl import VolumeRange
    try:
        VolumeRange(src_in=100, src_out=50, volume=0.5)
        assert False
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# E12: new ops — reorder / music / volume through apply_edl_ops
# ---------------------------------------------------------------------------

def test_reorder_op_applies_and_identity_clears():
    from app.edl import apply_edl_ops
    edl = _base_edl()
    out, res = apply_edl_ops(edl, [{"type": "reorder_segments", "order": [2, 0, 1]}])
    assert res[0]["applied"] and out["segment_order"] == [2, 0, 1]
    out2, res2 = apply_edl_ops(out, [{"type": "reorder_segments", "order": [0, 1, 2]}])
    assert res2[0]["applied"] and out2["segment_order"] is None    # identity clears


def test_reorder_op_rejects_bad_permutation():
    from app.edl import apply_edl_ops
    _, res = apply_edl_ops(_base_edl(), [{"type": "reorder_segments", "order": [0, 0, 1]}])
    assert not res[0]["applied"]


def test_trim_start_remaps_segment_order():
    # F1 regression: "trim the start" means the start the VIEWER sees, so trim_start
    # must walk PLAY order (segment_order), not array order. segment_order=[2,0,1]
    # means segment 2 plays FIRST — trimming 100 frames (exactly segment 2's length)
    # must consume segment 2 (index 2), not segment 0.
    from app.edl import apply_edl_ops
    edl = _base_edl(segment_order=[2, 0, 1])
    out, res = apply_edl_ops(edl, [{"type": "trim_start", "frames": 100}])
    assert res[0]["applied"]
    assert len(out["segments"]) == 2
    assert out["segments"] == [{"src_in": 0, "src_out": 100}, {"src_in": 100, "src_out": 200}]
    # segment 2 (played first) is gone; remaining play order [0, 1] is identity.
    assert out.get("segment_order") is None
    from app.edl import EDL
    EDL(**out)                                       # still validates


def test_trim_start_partial_into_play_order_first_segment():
    # Trimming MORE than the first-played segment's length must spill into the
    # NEXT-played segment (not the next array-order segment).
    from app.edl import apply_edl_ops
    edl = _base_edl(segment_order=[2, 0, 1])
    out, res = apply_edl_ops(edl, [{"type": "trim_start", "frames": 150}])
    assert res[0]["applied"]
    # segment 2 (100f, played first) fully consumed; 50 more frames taken from the
    # start of segment 0 (played second) since it's now first in the shrunken order.
    assert out["segments"] == [{"src_in": 50, "src_out": 100}, {"src_in": 100, "src_out": 200}]
    assert out.get("segment_order") is None


def test_trim_end_respects_play_order():
    # trim_end must consume the LAST-PLAYED segment first, not the highest array index.
    from app.edl import apply_edl_ops
    edl = _base_edl(segment_order=[1, 2, 0])   # segment 0 plays LAST
    out, res = apply_edl_ops(edl, [{"type": "trim_end", "frames": 100}])
    assert res[0]["applied"]
    # segment 0 (100f, played last) fully consumed; segments 1 and 2 untouched.
    assert out["segments"] == [{"src_in": 100, "src_out": 200}, {"src_in": 200, "src_out": 300}]
    assert out.get("segment_order") is None


def test_trim_start_identity_order_unaffected():
    # No segment_order set (None) → behaves exactly as before: trims array-order front.
    from app.edl import apply_edl_ops
    edl = _base_edl()
    out, res = apply_edl_ops(edl, [{"type": "trim_start", "frames": 50}])
    assert res[0]["applied"]
    assert out["segments"][0] == {"src_in": 50, "src_out": 100}
    assert out.get("segment_order") is None


def test_set_music_and_remove():
    from app.edl import apply_edl_ops
    out, res = apply_edl_ops(_base_edl(), [
        {"type": "set_music", "enabled": True, "url": "https://cdn/track.mp3",
         "volume": 0.3, "duck_voice": False}])
    assert res[0]["applied"]
    assert out["audio"]["music"]["volume"] == 0.3
    assert out["audio"]["music"]["duck_voice"] is False
    out2, res2 = apply_edl_ops(out, [{"type": "set_music", "enabled": False}])
    assert res2[0]["applied"] and out2["audio"]["music"] is None


def test_set_music_requires_url_or_query():
    from app.edl import apply_edl_ops
    _, res = apply_edl_ops(_base_edl(), [{"type": "set_music", "enabled": True}])
    assert not res[0]["applied"]


def test_mute_range_and_volume_replace_semantics():
    from app.edl import apply_edl_ops
    out, _ = apply_edl_ops(_base_edl(), [
        {"type": "set_segment_volume", "start_frame": 0, "end_frame": 200, "volume": 0.5}])
    # Now mute the middle — the 0.5 range must split around it.
    out2, res = apply_edl_ops(out, [{"type": "mute_range", "start_frame": 50, "end_frame": 100}])
    assert res[0]["applied"]
    ranges = out2["audio"]["volume_ranges"]
    assert [(r["src_in"], r["src_out"], r["volume"]) for r in ranges] == [
        (0, 50, 0.5), (50, 100, 0.0), (100, 200, 0.5)]


def test_new_ops_roundtrip_through_edl_model():
    from app.edl import apply_edl_ops, EDL
    out, _ = apply_edl_ops(_base_edl(), [
        {"type": "reorder_segments", "order": [1, 0, 2]},
        {"type": "set_music", "enabled": True, "query": "lofi", "volume": 0.2},
        {"type": "mute_range", "start_frame": 0, "end_frame": 30},
    ])
    validated = EDL(**out).model_dump()
    assert validated["segment_order"] == [1, 0, 2]
    assert validated["audio"]["music"]["query"] == "lofi"
    assert validated["audio"]["volume_ranges"]


# ---------------------------------------------------------------------------
# E13: build_render_plan — reorder + audio remap
# ---------------------------------------------------------------------------

def test_reorder_identity_plan_unchanged():
    from app.edl import build_render_plan
    edl = _base_edl(captions=[{"word": "hi", "frame": 150}],
                    overlays=[{"type": "punch_in", "src_in": 110, "src_out": 130,
                               "scale": 1.1, "text": ""}])
    base_plan = build_render_plan(edl)
    identity_plan = build_render_plan({**edl, "segment_order": [0, 1, 2]})
    assert base_plan == identity_plan


def test_reorder_remaps_captions_with_segment():
    from app.edl import build_render_plan
    # Word at source frame 150 lives in segment 1 (100-200). Order [1,0,2] puts
    # segment 1 FIRST → the word's output frame becomes 150-100+0 = 50.
    edl = _base_edl(captions=[{"word": "moved", "frame": 150}],
                    segment_order=[1, 0, 2])
    plan = build_render_plan(edl)
    assert plan["clips"][0] == {"src_in": 100, "src_out": 200}   # segment 1 plays first
    assert plan["captions"][0]["frame"] == 50


def test_reorder_overlay_travels_and_does_not_smear():
    from app.edl import build_render_plan
    # Overlay spans source 90-120: 10 frames in segment 0 (ends at out 100 in
    # identity), 20 frames in segment 1. Under order [1,0,2] the pieces land
    # non-contiguously — the plan must keep the LONGEST piece, not smear min..max.
    edl = _base_edl(overlays=[{"type": "punch_in", "src_in": 90, "src_out": 120,
                               "scale": 1.1, "text": ""}],
                    segment_order=[1, 0, 2])
    plan = build_render_plan(edl)
    o = plan["overlays"][0]
    # Segment 1 plays at out 0-100; its piece of the overlay is source 100-120 → out 0-20 (20 frames).
    # Segment 0 plays at out 100-200; its piece is source 90-100 → out 190-200 (10 frames).
    assert (o["frame_in"], o["frame_out"]) == (0, 20)


def test_reorder_with_drops_composes():
    from app.edl import build_render_plan
    edl = _base_edl(drops=[{"src_in": 100, "src_out": 150, "reason": "manual"}],
                    segment_order=[1, 0, 2])
    plan = build_render_plan(edl)
    # Segment 1 (100-200) minus drop (100-150) = kept (150-200) plays first.
    assert plan["clips"][0] == {"src_in": 150, "src_out": 200}
    assert plan["total_frames"] == 50 + 100 + 100


def test_volume_ranges_remap_as_split_pieces():
    from app.edl import build_render_plan
    # Mute source 90-160 with a cut at 100-150: output pieces must be the two
    # surviving slivers (90-100 and 150-160), NOT one merged span.
    edl = _base_edl(drops=[{"src_in": 100, "src_out": 150, "reason": "manual"}],
                    audio={"lufs_target": -14.0, "music": None,
                           "volume_ranges": [{"src_in": 90, "src_out": 160, "volume": 0.0}]})
    plan = build_render_plan(edl)
    vr = plan["audio"]["volume_ranges"]
    assert [(v["frame_in"], v["frame_out"]) for v in vr] == [(90, 100), (100, 110)]
    assert all(v["volume"] == 0.0 for v in vr)


def test_caption_frame_exactly_at_drop_boundary_maps_correctly():
    # F2 (no-repro, pinned as regression): map_point's half-open interval check
    # (s_in <= f < s_out) is internally consistent because segments/drops/captions
    # are all derived from the same ms_to_frame() — verify the exact boundary frames
    # around a drop resolve as expected (audited as a suspected off-by-one; disproved
    # by direct repro, pinned here so a future change can't silently reintroduce it).
    from app.edl import build_render_plan
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 100}],
                     drops=[{"src_in": 40, "src_out": 50, "reason": "filler"}],
                     captions=[{"word": "before", "frame": 39},
                               {"word": "dropped1", "frame": 40},
                               {"word": "dropped2", "frame": 49},
                               {"word": "after", "frame": 50}])
    plan = build_render_plan(edl)
    words_kept = {c["word"]: c["frame"] for c in plan["captions"]}
    assert words_kept == {"before": 39, "after": 40}   # dropped1/dropped2 excluded


def test_plan_audio_music_passthrough():
    from app.edl import build_render_plan
    edl = _base_edl(audio={"lufs_target": -14.0,
                           "music": {"url": "https://cdn/t.mp3", "query": None,
                                     "volume": 0.2, "duck_voice": True},
                           "volume_ranges": []})
    plan = build_render_plan(edl)
    assert plan["audio"]["music"]["url"] == "https://cdn/t.mp3"
    assert plan["audio"]["volume_ranges"] == []


# ---------------------------------------------------------------------------
# E14: reorder + audio ops through the tweak endpoint (direct-ops)
# ---------------------------------------------------------------------------

def test_endpoint_direct_reorder_and_music():
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    # The mock EDL is single-segment; reorder needs several — split it in place.
    job = main._clip_jobs[job_id]
    extent = job["edl"]["segments"][0]["src_out"]
    third = max(1, extent // 3)
    job["edl"]["segments"] = [
        {"src_in": 0, "src_out": third},
        {"src_in": third, "src_out": 2 * third},
        {"src_in": 2 * third, "src_out": extent},
    ]
    order = [2, 0, 1]
    out = client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id,
        "ops": [
            {"type": "reorder_segments", "order": order},
            {"type": "set_music", "enabled": True, "url": "https://cdn/t.mp3", "volume": 0.25},
            {"type": "mute_range", "start_frame": 0, "end_frame": 30},
        ],
    }).json()
    assert out["mode"] == "direct"
    applied_types = {a["type"] for a in out["applied"]}
    assert {"reorder_segments", "set_music", "mute_range"} <= applied_types
    edl = client.get(f"/v1/clips/{job_id}").json()["edl"]
    assert edl["segment_order"] == order
    assert edl["audio"]["music"]["url"] == "https://cdn/t.mp3"
    assert edl["audio"]["volume_ranges"]
    # And undo still works across the new ops
    undo = client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id, "ops": [{"type": "undo"}]}).json()
    assert any(a["type"] == "undo" for a in undo["applied"])
    edl2 = client.get(f"/v1/clips/{job_id}").json()["edl"]
    assert edl2.get("segment_order") is None


# ---- F8: undo restores the full EDL triple; depth 25; undo_available exposed ----

def test_undo_restores_segment_order_audio_captions_together():
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    job = main._clip_jobs[job_id]
    extent = job["edl"]["segments"][0]["src_out"]
    third = max(1, extent // 3)
    job["edl"]["segments"] = [{"src_in": 0, "src_out": third},
                               {"src_in": third, "src_out": 2 * third},
                               {"src_in": 2 * third, "src_out": extent}]
    captions_before = list(job["edl"]["captions"])
    client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id,
        "ops": [{"type": "reorder_segments", "order": [2, 0, 1]},
                {"type": "set_music", "enabled": True, "url": "https://cdn/t.mp3", "volume": 0.3},
                {"type": "set_captions_enabled", "enabled": False}]})
    undo = client.post(f"/v1/clips/{job_id}/tweak",
                       json={"clip_id": clip_id, "ops": [{"type": "undo"}]}).json()
    assert undo["undo_available"] is False   # single tweak → stack now empty
    edl = client.get(f"/v1/clips/{job_id}").json()["edl"]
    assert edl.get("segment_order") is None
    assert edl["audio"].get("music") is None
    assert edl["captions"] == captions_before


def test_undo_depth_is_25():
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    for i in range(30):
        client.post(f"/v1/clips/{job_id}/tweak", json={
            "clip_id": clip_id,
            "ops": [{"type": "set_caption_style", "style": "karaoke" if i % 2 else "clean"}]})
    assert len(main._clip_jobs[job_id]["edl_history"]) == 25


def test_get_clip_exposes_undo_available():
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    assert client.get(f"/v1/clips/{job_id}").json()["undo_available"] is False
    client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id, "ops": [{"type": "set_caption_style", "style": "karaoke"}]})
    assert client.get(f"/v1/clips/{job_id}").json()["undo_available"] is True


# ---- F9: swept (TTL-expired) jobs return 410, never-existed jobs return 404 ----

def test_never_existed_job_returns_404():
    r = client.get("/v1/clips/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
    assert r.json()["detail"] == "job_not_found"


def test_swept_job_returns_410_job_expired(monkeypatch):
    job_id = seed_clip_job(source_url="mock://source")
    # Force it past the TTL, then trigger the lazy sweep via a GET.
    main._clip_jobs[job_id]["created_at"] = time.time() - main._JOB_TTL_S - 10
    r2 = client.get(f"/v1/clips/{job_id}")
    assert r2.status_code == 410
    assert r2.json()["detail"] == "job_expired"
    # And it stays 410 on a second lookup (not swallowed back to plain 404).
    r3 = client.get(f"/v1/clips/{job_id}")
    assert r3.status_code == 410


# ---- F10: transcript hygiene — malformed words dropped, duplicates deduped ----

def test_normalize_words_hygiene():
    raw = [
        {"text": "hello", "start": 0, "end": 300, "confidence": 0.9},
        {"text": "", "start": 300, "end": 400, "confidence": 0.9},        # blank
        {"text": "  ", "start": 400, "end": 500, "confidence": 0.9},      # whitespace-only
        {"text": "world", "start": 500, "end": 400, "confidence": 0.9},   # end < start
        {"text": "world", "start": 500, "end": 500, "confidence": 0.9},   # end == start
        {"text": "world", "start": 500, "end": 800, "confidence": 0.9},   # valid
        {"text": "world", "start": 500, "end": 800, "confidence": 0.9},   # exact duplicate
        {"text": "there", "start": -100, "end": 1200, "confidence": 0.9}, # negative start
    ]
    out = main._normalize_words(raw)
    assert [w["word"] for w in out] == ["hello", "world", "there"]
    assert out[1]["start_ms"] == 500 and out[1]["end_ms"] == 800
    assert out[2]["start_ms"] == 0   # clamped, never negative


def test_normalize_words_idempotent_on_already_normalized_input():
    # Already-normalized (mock) words must pass through unchanged (existing contract).
    words = [{"word": "hi", "start_ms": 0, "end_ms": 280, "confidence": 1.0,
              "type": None, "is_emphasized": False}]
    assert main._normalize_words(words) == words


# ---- F11: partial-overlap filler drop must union, not vanish entirely ----

def test_merge_drops_partial_overlap_unions_not_discards():
    # An LLM editorial cut [1250,1500) that only PARTIALLY overlaps a filler word's
    # drop [1000,1300) used to discard the filler drop entirely, leaving frames
    # 1000-1250 of that filler word un-cut in the final render.
    existing = [{"src_in": 1250, "src_out": 1500, "reason": "manual"}]
    new = [{"src_in": 1000, "src_out": 1300, "reason": "filler"}]
    out = main._merge_drops(existing, new)
    assert len(out) == 1
    assert out[0]["src_in"] == 1000 and out[0]["src_out"] == 1500


# ---- F12 (no-repro, pinned): manual tweaks survive a retry without prefs reverting ----

def test_manual_captions_off_survives_a_retry():
    # _apply_edit_prefs is only ever called once, at initial pipeline generation
    # (main.py _run_pipeline) — a retry re-runs _render_all_clips only, which never
    # calls _apply_edit_prefs again, so a manual tweak can't be silently reverted
    # by stale edit_prefs on retry.
    job_id = seed_clip_job(source_url="mock://source", edit_prefs={"auto_captions": True})
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id, "ops": [{"type": "set_captions_enabled", "enabled": False}]})
    assert client.get(f"/v1/clips/{job_id}").json()["edl"]["captions"] == []

    job = main._clip_jobs[job_id]
    job["clips"][0]["status"] = "failed"
    job["status"] = "failed"
    client.post(f"/v1/clips/{job_id}/retry")
    assert client.get(f"/v1/clips/{job_id}").json()["edl"]["captions"] == []


# ---- F13: silent degradations now surface a warning / flag ----

def test_safe_default_fallback_warns_the_clip(monkeypatch):
    # _renderable_job forces the LLM-down path (anthropic() raises), which used to
    # silently substitute a generic safe-default cut with zero signal to the client
    # that they didn't get a tailored AI edit.
    job_id = _renderable_job(monkeypatch)
    async def bridge(*args, timeout_s=None):
        if args[0] == "submit":
            return {"renderId": "r1", "bucketName": "b"}
        return {"done": True, "outputFile": "https://cdn/out.mp4"}
    async def fast_sleep(_): pass
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    _run_pipeline_sync(job_id)
    job = main._clip_jobs[job_id]
    assert any("ai_edit_unavailable" in w for w in job["clips"][0].get("warnings", []))


def test_tweak_flags_degraded_when_live_llm_falls_back_to_mock(monkeypatch):
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "test-key")
    async def failing_llm(*a, **k):
        raise main.HTTPException(status_code=502, detail="down")
    monkeypatch.setattr(main, "anthropic_json", failing_llm)
    out = client.post(f"/v1/clips/{job_id}/tweak",
                      json={"clip_id": clip_id, "instruction": "make it punchier"}).json()
    assert out["mode"] == "live"          # contract unchanged
    assert out["degraded"] is True        # but now flagged as a fallback turn


def test_tweak_not_degraded_on_direct_ops():
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    out = client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id, "ops": [{"type": "set_caption_style", "style": "karaoke"}]}).json()
    assert out["degraded"] is False


# ---- F14 (no-repro, pinned): react_schedule clip_from doesn't need segment_order
# remapping — it's a cursor into the INDEPENDENT react-source video, unrelated to
# the creator's own segment order. A window straddled by a cut/reorder that would
# desync it is already dropped outright by the existing length-preservation guard.

def test_react_window_reorders_correctly_clip_from_untouched():
    from app.edl import build_render_plan
    edl = _base_edl(
        style="duet_split",
        segments=[{"src_in": 0, "src_out": 100}, {"src_in": 100, "src_out": 200}],
        segment_order=[1, 0],
        react_schedule=[{"state": "freeze", "src_in": 120, "src_out": 180, "clip_from": 50}])
    plan = build_render_plan(edl)
    assert len(plan["react_schedule"]) == 1
    w = plan["react_schedule"][0]
    assert (w["frame_in"], w["frame_out"]) == (20, 80)
    assert w["clip_from"] == 50   # unchanged — independent of segment_order by design


def test_react_window_straddled_by_cut_dropped_not_desynced():
    from app.edl import build_render_plan
    edl = _base_edl(style="duet_split", segments=[{"src_in": 0, "src_out": 200}],
                    drops=[{"src_in": 140, "src_out": 160, "reason": "manual"}],
                    react_schedule=[{"state": "freeze", "src_in": 100, "src_out": 180, "clip_from": 20}])
    plan = build_render_plan(edl)
    assert plan["react_schedule"] == []   # dropped outright rather than desynced


# ---- react_window_dropped: the straddled-drop case above used to vanish with zero
# trace anywhere in the render plan (mirrors the F6 broll_unresolved class) ----

def test_react_window_straddled_by_cut_appends_warning():
    from app.edl import build_render_plan
    edl = _base_edl(style="duet_split", segments=[{"src_in": 0, "src_out": 200}],
                    drops=[{"src_in": 140, "src_out": 160, "reason": "manual"}],
                    react_schedule=[{"state": "freeze", "src_in": 100, "src_out": 180, "clip_from": 20}])
    warnings = []
    build_render_plan(edl, warnings)
    assert any("react_window_dropped" in w for w in warnings)


def test_react_window_fully_inside_cut_no_warning():
    # A window landing entirely inside a cut is just the creator's own cut removing
    # it — expected, not a desync — so it must NOT get the same warning as the
    # straddled case above.
    from app.edl import build_render_plan
    edl = _base_edl(style="duet_split", segments=[{"src_in": 0, "src_out": 200}],
                    drops=[{"src_in": 90, "src_out": 200, "reason": "manual"}],
                    react_schedule=[{"state": "freeze", "src_in": 100, "src_out": 180, "clip_from": 20}])
    warnings = []
    plan = build_render_plan(edl, warnings)
    assert plan["react_schedule"] == []
    assert warnings == []


def test_react_window_not_dropped_no_warning():
    from app.edl import build_render_plan
    edl = _base_edl(
        style="duet_split",
        segments=[{"src_in": 0, "src_out": 100}, {"src_in": 100, "src_out": 200}],
        segment_order=[1, 0],
        react_schedule=[{"state": "freeze", "src_in": 120, "src_out": 180, "clip_from": 50}])
    warnings = []
    build_render_plan(edl, warnings)
    assert warnings == []


def test_run_pipeline_warns_clip_on_react_window_desync(monkeypatch):
    # End-to-end: the desync-drop case above must actually reach the clip's
    # warnings[] through _run_pipeline, not just build_render_plan's return value.
    job_id = seed_clip_job(source_url="https://example.com/video.mov", style="duet_split", react_source_url="https://example.com/react.mp4")
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "test-key")
    async def ok_probe(url): pass
    monkeypatch.setattr(main, "_validate_source_url", ok_probe)
    _mock_transcript_ok(monkeypatch)
    async def no_llm(*a, **k):
        raise main.HTTPException(status_code=502, detail="keyless")
    monkeypatch.setattr(main, "anthropic", no_llm)

    from app.edl import EDL, Layout
    def fake_safe_default(style, format_id, total_frames, words):
        return EDL(
            style=style, format_id=format_id,
            segments=[{"src_in": 0, "src_out": 200}],
            drops=[{"src_in": 140, "src_out": 160, "reason": "filler"}],
            react_schedule=[{"state": "freeze", "src_in": 100, "src_out": 180, "clip_from": 20}],
            layout=Layout(style=style),
        )
    monkeypatch.setattr(main, "safe_default_edl", fake_safe_default)
    _run_pipeline_sync(job_id)
    job = main._clip_jobs[job_id]
    assert any("react_window_dropped" in w for w in job["clips"][0].get("warnings", []))


# ---- F15: durable edit sessions (Supabase write-through + lazy restore) ----

class _FakeSupabase:
    """In-memory stand-in for SupabaseClient — exercises the real persist/restore
    code paths in main.py without a network call."""
    def __init__(self):
        self.jobs: dict[str, dict] = {}

    async def upsert_clip_job(self, job_id, job):
        self.jobs[job_id] = json.loads(json.dumps(job))   # round-trips like real JSON storage
        return True

    async def load_clip_job(self, job_id):
        return self.jobs.get(job_id)


def _wait_until(predicate, timeout_s=2.0):
    """Poll from a SYNC test while a fire-and-forget asyncio.create_task (running
    on the TestClient's background event-loop thread) catches up."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_tweak_persists_to_supabase_when_configured(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(main, "_supabase_client", fake)
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id, "ops": [{"type": "set_caption_style", "style": "karaoke"}]})
    assert _wait_until(lambda: job_id in fake.jobs)
    assert fake.jobs[job_id]["edl"]["caption_style"] == "karaoke"


def test_get_clip_restores_from_supabase_on_in_memory_miss(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(main, "_supabase_client", fake)
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id, "ops": [{"type": "set_caption_style", "style": "bold-word"}]})
    assert _wait_until(lambda: job_id in fake.jobs)

    # Simulate this instance never having seen it (restart / TTL sweep) — pop it
    # from the in-memory store, but the durable copy remains.
    main._clip_jobs.pop(job_id, None)
    r2 = client.get(f"/v1/clips/{job_id}")
    assert r2.status_code == 200   # NOT 404/410 — restored from Supabase
    assert r2.json()["edl"]["caption_style"] == "bold-word"
    assert job_id in main._clip_jobs   # cached back into memory


def test_tweak_restores_from_supabase_on_in_memory_miss(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(main, "_supabase_client", fake)
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id, "ops": [{"type": "set_caption_style", "style": "clean"}]})
    assert _wait_until(lambda: job_id in fake.jobs)

    main._clip_jobs.pop(job_id, None)
    out = client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id, "ops": [{"type": "set_captions_enabled", "enabled": False}]})
    assert out.status_code == 200
    assert client.get(f"/v1/clips/{job_id}").json()["edl"]["captions"] == []


def test_restore_is_a_noop_keyless():
    # No _supabase_client configured (the default in every other test in this
    # file) — restore must return None and never raise.
    assert main._supabase_client is None
    out = asyncio.run(main._restore_clip_job("00000000-0000-0000-0000-000000000000"))
    assert out is None


# ---- F16: fuzz gate — random op sequences over random EDLs must never violate
# the core invariants. Seeded (deterministic), not sampled at collection time. ----

def test_fuzz_random_op_sequences_preserve_invariants():
    import random as _random
    from app.edl import EDL, apply_edl_ops, build_render_plan, _kept_frames

    OP_TYPES = ["trim_start", "trim_end", "cut_range", "restore_range",
                "mute_range", "reorder_segments", "set_music",
                "set_caption_style", "set_captions_enabled", "remove_overlays"]

    for seed in range(50):
        rng = _random.Random(seed)
        n_segs = rng.randint(1, 5)
        segs, cursor = [], 0
        for _ in range(n_segs):
            length = rng.randint(30, 300)
            segs.append({"src_in": cursor, "src_out": cursor + length})
            cursor += length
        edl = {
            "style": "talking_head", "format_id": "myth-buster",
            "segments": segs, "layout": {"style": "talking_head"},
            "captions": [{"word": f"w{i}", "frame": rng.randint(0, cursor - 1)}
                         for i in range(rng.randint(0, 5))],
        }
        ops = []
        for _ in range(rng.randint(1, 8)):
            t = rng.choice(OP_TYPES)
            if t in ("trim_start", "trim_end"):
                ops.append({"type": t, "frames": rng.randint(1, 50)})
            elif t in ("cut_range", "restore_range", "mute_range"):
                a = rng.randint(0, cursor)
                ops.append({"type": t, "start_frame": a, "end_frame": a + rng.randint(1, 50)})
            elif t == "reorder_segments":
                order = list(range(len(edl["segments"])))
                rng.shuffle(order)
                ops.append({"type": t, "order": order})
            elif t == "set_music":
                ops.append({"type": t, "enabled": rng.choice([True, False]),
                           "url": "https://cdn/t.mp3", "volume": rng.random()})
            elif t == "set_caption_style":
                ops.append({"type": t, "style": rng.choice(["clean", "bold-word", "karaoke"])})
            elif t == "set_captions_enabled":
                ops.append({"type": t, "enabled": rng.choice([True, False])})
            elif t == "remove_overlays":
                ops.append({"type": t})

        out, results = apply_edl_ops(edl, ops)
        ctx = {"seed": seed, "ops": ops, "segments": out["segments"]}

        # 1) segments stay monotonic (no overlaps, no non-positive length)
        prev_out = -1
        for s in out["segments"]:
            assert s["src_in"] < s["src_out"], ctx
            assert s["src_in"] >= prev_out, ctx
            prev_out = s["src_out"]

        # 2) segment_order (if set) is a valid permutation of the CURRENT segments
        order = out.get("segment_order")
        if order is not None:
            assert sorted(order) == list(range(len(out["segments"]))), ctx

        # 3) the output is itself a legally constructible EDL
        EDL(**out)

        # 4) kept-frames never drops to (or below) zero — every op that would
        # violate the min-duration guard must have been rejected, not applied.
        assert _kept_frames(out) > 0, ctx

        # 5) the render plan always builds without raising
        plan = build_render_plan(out)

        # 6) every caption/overlay frame lands within the plan's own output bounds
        total = sum(c["src_out"] - c["src_in"] for c in plan["clips"])
        for cap in plan["captions"]:
            assert 0 <= cap["frame"] < total, (ctx, cap, total)
        for ov in plan["overlays"]:
            assert 0 <= ov["frame_in"] < ov["frame_out"] <= total, (ctx, ov, total)


# ---- G1: golden plan-contract fixtures — build_render_plan's output MUST match
# render/src/types.ts's RenderPlan interface field-for-field. This is the single
# source of truth the render bridge consumes; a drift here silently breaks/no-ops
# a feature in the rendered video with no error anywhere. Key sets are checked
# EXACT (not superset/subset) so adding/removing a field on either side fails
# this test until the other side is updated to match. ----

_TS_RENDER_PLAN_KEYS = {"style", "format_id", "clips", "captions", "overlays", "broll",
                        "react_source", "react_schedule", "layout", "caption_style",
                        "audio", "total_frames"}
_TS_CLIP_KEYS = {"src_in", "src_out"}
_TS_CAPTION_KEYS = {"word", "frame"}
_TS_OVERLAY_KEYS = {"type", "frame_in", "frame_out", "scale", "text"}
_TS_BROLL_KEYS = {"frame_in", "frame_out", "cue_text", "asset_id", "broll_query",
                  "source", "resolved_url"}
_TS_LAYOUT_KEYS = {"style", "panels", "panel_boundaries", "split_fraction"}
_TS_REACT_SOURCE_KEYS = {"resolved_url", "kind", "credit_label"}
_TS_REACT_WINDOW_KEYS = {"state", "frame_in", "frame_out", "clip_from", "audio_gain"}
_TS_MUSIC_KEYS = {"url", "query", "volume", "duck_voice"}
_TS_VOLUME_RANGE_KEYS = {"frame_in", "frame_out", "volume"}
_TS_AUDIO_PLAN_KEYS = {"lufs_target", "music", "volume_ranges", "speech_frames"}


def test_render_plan_matches_typescript_contract_exactly():
    from app.edl import build_render_plan
    edl = {
        "style": "duet_split", "format_id": "myth-buster",
        "segments": [{"src_in": 0, "src_out": 300}, {"src_in": 300, "src_out": 600}],
        "drops": [{"src_in": 100, "src_out": 120, "reason": "filler"}],
        "captions": [{"word": "hi", "frame": 10}],
        "overlays": [{"type": "punch_in", "src_in": 20, "src_out": 60, "scale": 1.1, "text": "wow"}],
        "broll": [{"src_in": 200, "src_out": 250, "cue_text": "city", "asset_id": "a1",
                   "broll_query": "city skyline", "source": "stock",
                   "resolved_url": "https://cdn/city.mp4"}],
        "react_source": {"resolved_url": "https://cdn/react.mp4", "kind": "video",
                         "credit_label": "@original"},
        "react_schedule": [{"state": "play", "src_in": 300, "src_out": 400,
                            "clip_from": 50, "audio_gain": 0.5}],
        "layout": {"style": "duet_split", "panels": 1, "panel_boundaries": [],
                  "split_fraction": 0.6},
        "caption_style": "karaoke",
        "audio": {"lufs_target": -14.0,
                 "music": {"url": "https://cdn/t.mp3", "query": None, "volume": 0.2,
                          "duck_voice": True},
                 "volume_ranges": [{"src_in": 0, "src_out": 30, "volume": 0.0}]},
    }
    plan = build_render_plan(edl)

    assert set(plan.keys()) == _TS_RENDER_PLAN_KEYS
    for c in plan["clips"]:
        assert set(c.keys()) == _TS_CLIP_KEYS
    for c in plan["captions"]:
        assert set(c.keys()) == _TS_CAPTION_KEYS
    for o in plan["overlays"]:
        assert set(o.keys()) == _TS_OVERLAY_KEYS
    for b in plan["broll"]:
        assert set(b.keys()) == _TS_BROLL_KEYS
    assert set(plan["layout"].keys()) == _TS_LAYOUT_KEYS
    assert set(plan["react_source"].keys()) == _TS_REACT_SOURCE_KEYS
    for w in plan["react_schedule"]:
        assert set(w.keys()) == _TS_REACT_WINDOW_KEYS
    assert set(plan["audio"].keys()) == _TS_AUDIO_PLAN_KEYS
    assert set(plan["audio"]["music"].keys()) == _TS_MUSIC_KEYS
    for vr in plan["audio"]["volume_ranges"]:
        assert set(vr.keys()) == _TS_VOLUME_RANGE_KEYS


def test_render_plan_matches_contract_with_all_optionals_absent():
    # The minimal case (no drops/captions/overlays/broll/react/music) must still
    # produce every REQUIRED top-level key, with the right (empty/None) shape for
    # the optional ones — never a missing key (types.ts has no way to signal
    # "field just isn't there" at the JS runtime level; a missing key reads as
    # `undefined` for a required field, which is exactly the drift this guards).
    from app.edl import build_render_plan
    edl = {"style": "talking_head", "format_id": "myth-buster",
          "segments": [{"src_in": 0, "src_out": 300}],
          "layout": {"style": "talking_head"}}
    plan = build_render_plan(edl)
    assert set(plan.keys()) == _TS_RENDER_PLAN_KEYS
    assert plan["captions"] == [] and plan["overlays"] == [] and plan["broll"] == []
    assert plan["react_source"] is None and plan["react_schedule"] == []
    assert set(plan["audio"].keys()) == _TS_AUDIO_PLAN_KEYS
    assert plan["audio"]["music"] is None and plan["audio"]["volume_ranges"] == []
    assert isinstance(plan["total_frames"], int) and plan["total_frames"] >= 1


def test_render_plan_contract_holds_for_every_composition_style():
    # One golden fixture per composition (7 styles, matching render/src/Root.tsx's
    # 7 <Composition> entries) — every style must produce the exact same top-level
    # contract; no style-specific code path is allowed to silently omit a key.
    from app.edl import build_render_plan
    for style in ("talking_head", "faceless", "split_three", "fast_cuts",
                  "green_screen", "broll_cutaway", "duet_split"):
        edl = {"style": style, "format_id": "x",
              "segments": [{"src_in": 0, "src_out": 300}],
              "layout": {"style": style}}
        plan = build_render_plan(edl)
        assert set(plan.keys()) == _TS_RENDER_PLAN_KEYS, style
        assert set(plan["layout"].keys()) == _TS_LAYOUT_KEYS, style


# ---- G3: ducking must survive the captions-off toggle (independent creative
# choices — a creator can want music ducked under their voice without on-screen
# captions) ----

def test_speech_frames_independent_of_captions_toggle():
    from app.edl import apply_edl_ops, build_render_plan
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 300}],
                    captions=[{"word": "hi", "frame": 30}, {"word": "there", "frame": 60}],
                    speech_frames=[30, 60],
                    audio={"lufs_target": -14.0,
                          "music": {"url": "https://cdn/t.mp3", "volume": 0.2,
                                   "duck_voice": True}})
    out, _ = apply_edl_ops(edl, [{"type": "set_captions_enabled", "enabled": False}])
    plan = build_render_plan(out)
    assert plan["captions"] == []                        # visual toggle honored
    assert plan["audio"]["speech_frames"] == [30, 60]     # ducking signal survives


def test_speech_frames_populated_from_transcript_in_live_pipeline(monkeypatch):
    # The LLM's own JSON never emits speech_frames (it's derived, not authored) —
    # _run_pipeline must (re-)populate it from the actual transcript regardless.
    job_id = _renderable_job(monkeypatch)
    async def bridge(*args, timeout_s=None):
        if args[0] == "submit":
            return {"renderId": "r1", "bucketName": "b"}
        return {"done": True, "outputFile": "https://cdn/out.mp4"}
    async def fast_sleep(_): pass
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    monkeypatch.setattr(main.asyncio, "sleep", fast_sleep)
    _run_pipeline_sync(job_id)
    job = main._clip_jobs[job_id]
    assert job["edl"]["speech_frames"], "speech_frames must be populated after the pipeline runs"


# ---- G4 (deliberately deferred, documented not silent): lufs_target flows
# through the full contract with its published-platform-target default, but is
# not yet applied by any composition — real normalization needs an ffmpeg
# loudnorm pass or equivalent that doesn't exist in this render bridge. ----

def test_lufs_target_flows_through_contract_with_documented_default():
    from app.edl import build_render_plan
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 300}])   # no audio block set
    plan = build_render_plan(edl)
    assert plan["audio"]["lufs_target"] == -14.0   # TikTok/YouTube's published target
    # explicit override round-trips too (the field is a real contract slot, not
    # a hardcoded constant, so it's ready for whenever normalization ships)
    edl2 = _base_edl(segments=[{"src_in": 0, "src_out": 300}],
                     audio={"lufs_target": -12.0})
    assert build_render_plan(edl2)["audio"]["lufs_target"] == -12.0


# ---- G5: b-roll resolution prefers an actual portrait file among a video's
# renditions, not just any "hd"-quality one (orientation=portrait only biases
# which VIDEOS are returned, not which transcoded FILE gets picked) ----

def test_fetch_pexels_prefers_portrait_file_over_landscape_hd(monkeypatch):
    monkeypatch.setattr(main, "PEXELS_KEY", "test-key")
    class FakeResp:
        status_code = 200
        def json(self):
            return {"videos": [{"video_files": [
                {"quality": "hd", "width": 1920, "height": 1080, "link": "landscape-hd"},
                {"quality": "sd", "width": 720, "height": 1280, "link": "portrait-sd"},
                {"quality": "hd", "width": 1080, "height": 1920, "link": "portrait-hd"},
            ]}]}
    async def fake_get(self, url, headers=None, params=None):
        return FakeResp()
    monkeypatch.setattr(main.httpx.AsyncClient, "get", fake_get)
    link = asyncio.run(main._fetch_pexels("city skyline"))
    assert link == "portrait-hd"


def test_fetch_pexels_falls_back_to_landscape_hd_when_no_portrait_exists(monkeypatch):
    monkeypatch.setattr(main, "PEXELS_KEY", "test-key")
    class FakeResp:
        status_code = 200
        def json(self):
            return {"videos": [{"video_files": [
                {"quality": "sd", "width": 640, "height": 360, "link": "landscape-sd"},
                {"quality": "hd", "width": 1920, "height": 1080, "link": "landscape-hd"},
            ]}]}
    async def fake_get(self, url, headers=None, params=None):
        return FakeResp()
    monkeypatch.setattr(main.httpx.AsyncClient, "get", fake_get)
    link = asyncio.run(main._fetch_pexels("office"))
    assert link == "landscape-hd"   # never letterboxed either way — objectFit:cover


# ---- G7: a process-wide semaphore must cap concurrent Lambda submissions
# ACROSS jobs (clips within one job were already sequential — the audit's
# framing was off, but a burst of separate jobs had no cap at all) ----

def test_render_semaphore_caps_cross_job_concurrency(monkeypatch):
    cap = 2
    monkeypatch.setattr(main, "RENDER_CONCURRENCY_CAP", cap)
    monkeypatch.setattr(main, "_render_semaphore", asyncio.Semaphore(cap))
    monkeypatch.setattr(main, "REMOTION_SERVE_URL", "https://serve.example")
    monkeypatch.setattr(main, "REMOTION_ACCESS_KEY", "ak")
    monkeypatch.setattr(main, "REMOTION_FUNCTION_NAME", "fn")

    state = {"current": 0, "peak": 0}

    async def submit(*a, **k):
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(0.03)
        return {"render_id": "r", "bucket_name": "b"}

    async def poll(*a, **k):
        await asyncio.sleep(0.03)
        state["current"] -= 1
        return "https://cdn/out.mp4"

    monkeypatch.setattr(main, "_submit_remotion_render", submit)
    monkeypatch.setattr(main, "_poll_remotion_render", poll)

    job_ids = []
    for i in range(6):
        job_id = f"g7-job-{i}"
        main._clip_jobs[job_id] = {
            "job_id": job_id, "status": "rendering", "source_url": "https://x/v.mov",
            "style": "talking_head", "edl": {"style": "talking_head", "format_id": "x"},
            "clips": [{"clip_id": f"c{i}", "format": "myth-buster", "status": "queued"}],
            "created_at": time.time(), "script": SCRIPT, "brand": {}, "media_context": "",
            "words": [], "edl_history": [], "tweaks": [], "edit_prefs": {},
            "react_source_url": None, "react_credit_label": None,
        }
        job_ids.append(job_id)

    async def run_all():
        await asyncio.gather(*(main._render_all_clips(jid) for jid in job_ids))
    asyncio.run(run_all())

    assert state["peak"] <= cap, f"peak concurrent renders {state['peak']} exceeded cap {cap}"
    for jid in job_ids:
        assert main._clip_jobs[jid]["clips"][0]["status"] == "ready"
        main._clip_jobs.pop(jid, None)


# ---- G8: a cold-Lambda submit timeout is transient — one retry with double the
# budget should recover instead of failing the clip outright ----

def test_submit_retries_once_on_timeout_then_succeeds(monkeypatch):
    monkeypatch.setattr(main, "REMOTION_SERVE_URL", "https://serve.example")
    monkeypatch.setattr(main, "REMOTION_FUNCTION_NAME", "fn")
    calls = {"n": 0, "timeouts": []}
    async def bridge(*args, timeout_s=None):
        calls["n"] += 1
        calls["timeouts"].append(timeout_s)
        if calls["n"] == 1:
            return {"_error": "bridge timed out after 30s"}   # cold-start
        return {"renderId": "r1", "bucketName": "b1"}
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    out = asyncio.run(main._submit_remotion_render(
        "https://x/v.mov", {"style": "talking_head", "format_id": "x"}, "myth-buster", "talking_head"))
    assert out == {"render_id": "r1", "bucket_name": "b1", "plan_warnings": []}
    assert calls["n"] == 2
    assert calls["timeouts"][1] == main.BRIDGE_CALL_TIMEOUT_S * 2   # retry gets more room


def test_submit_does_not_retry_on_a_non_timeout_bridge_error(monkeypatch):
    monkeypatch.setattr(main, "REMOTION_SERVE_URL", "https://serve.example")
    monkeypatch.setattr(main, "REMOTION_FUNCTION_NAME", "fn")
    calls = {"n": 0}
    async def bridge(*args, timeout_s=None):
        calls["n"] += 1
        return {"_error": "composition not found"}
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    try:
        asyncio.run(main._submit_remotion_render(
            "https://x/v.mov", {"style": "talking_head", "format_id": "x"}, "myth-buster", "talking_head"))
        assert False, "should have raised"
    except main.PipelineError as e:
        assert e.code == "bridge_error"
    assert calls["n"] == 1   # no retry — only timeouts are transient


# ---- G9: preview render path — cheap proof render, never touches render_url ----

def test_submit_passes_preview_flag_to_the_bridge(monkeypatch):
    monkeypatch.setattr(main, "REMOTION_SERVE_URL", "https://serve.example")
    monkeypatch.setattr(main, "REMOTION_FUNCTION_NAME", "fn")
    seen = {}
    async def bridge(*args, timeout_s=None):
        seen["args"] = args
        return {"renderId": "r1", "bucketName": "b1"}
    monkeypatch.setattr(main, "_run_render_bridge", bridge)
    asyncio.run(main._submit_remotion_render(
        "https://x/v.mov", {"style": "talking_head", "format_id": "x"},
        "myth-buster", "talking_head", preview=True))
    assert seen["args"][-1] == "1"

    asyncio.run(main._submit_remotion_render(
        "https://x/v.mov", {"style": "talking_head", "format_id": "x"},
        "myth-buster", "talking_head", preview=False))
    assert seen["args"][-1] == "0"


def test_preview_rerender_never_touches_render_url_or_status(monkeypatch):
    job_id = seed_clip_job(source_url="mock://source")
    job = main._clip_jobs[job_id]
    clip = job["clips"][0]
    clip["render_url"] = "https://real.example/committed.mp4"
    clip["status"] = "ready"

    async def submit(*a, **k):
        return {"render_id": "r1", "bucket_name": "b1"}
    async def poll(*a, **k):
        return "https://cdn/preview.mp4"
    monkeypatch.setattr(main, "_submit_remotion_render", submit)
    monkeypatch.setattr(main, "_poll_remotion_render", poll)

    asyncio.run(main._preview_rerender_clip(job_id, clip["clip_id"]))

    assert clip["preview_url"] == "https://cdn/preview.mp4"
    assert clip["preview_status"] == "ready"
    assert clip["render_url"] == "https://real.example/committed.mp4"   # untouched
    assert clip["status"] == "ready"                                     # untouched


def test_preview_rerender_failure_never_touches_committed_state(monkeypatch):
    job_id = seed_clip_job(source_url="mock://source")
    job = main._clip_jobs[job_id]
    clip = job["clips"][0]
    clip["render_url"] = "https://real.example/committed.mp4"
    clip["status"] = "ready"

    async def exploding_submit(*a, **k):
        raise main.PipelineError("render_submit_failed", "no bridge", "render")
    monkeypatch.setattr(main, "_submit_remotion_render", exploding_submit)

    asyncio.run(main._preview_rerender_clip(job_id, clip["clip_id"]))

    assert clip["preview_status"] == "failed"
    assert "render_submit_failed" in clip["preview_error"]
    assert clip["render_url"] == "https://real.example/committed.mp4"
    assert clip["status"] == "ready"


def test_tweak_preview_flag_triggers_preview_not_full_render(monkeypatch):
    # A mock (keyless-source) job already has a valid EDL from creation; only
    # the REMOTION_* capability flags need faking so can_render is true.
    job_id = seed_clip_job(source_url="mock://source")
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    # needs_render/preview both gate on status=="ready" (a completed LIVE job) —
    # a mock job starts "mock_ready" by design (no real render to trigger), so
    # override it here purely to exercise this branch; the EDL is already valid.
    main._clip_jobs[job_id]["status"] = "ready"
    monkeypatch.setattr(main, "REMOTION_SERVE_URL", "https://serve.example")
    monkeypatch.setattr(main, "REMOTION_ACCESS_KEY", "ak")
    monkeypatch.setattr(main, "REMOTION_FUNCTION_NAME", "fn")

    async def fake_preview(jid, cid): pass
    async def fake_full(jid, cid, gen, resolve_broll=False): pass
    monkeypatch.setattr(main, "_preview_rerender_clip", fake_preview)
    monkeypatch.setattr(main, "_rerender_clip", fake_full)

    out = client.post(f"/v1/clips/{job_id}/tweak?preview=1", json={
        "clip_id": clip_id, "ops": [{"type": "set_caption_style", "style": "karaoke"}]}).json()
    assert out["preview_requested"] is True
    assert out["needs_render"] is False


# ---- G10 (no-repro, pinned): FastCuts' flash boundary formula and CutVideo's
# outStart formula are IDENTICAL (verified by hand-trace against a degenerate
# zero-length clip in both TSX files — no test runner exists in render/ to
# assert this directly, so it's documented there and pinned here on the
# backend invariant that actually rules the edge case out in practice: the
# render plan's clips can never be degenerate to begin with, since
# _kept_intervals already filters b<=a). volumeAt's half-open interval check
# matches every other interval convention in this codebase — not an off-by-one. ----

def test_render_plan_clips_never_degenerate():
    from app.edl import build_render_plan
    edl = _base_edl(
        segments=[{"src_in": 0, "src_out": 100}, {"src_in": 100, "src_out": 100}],  # 2nd is zero-length
        drops=[{"src_in": 100, "src_out": 100, "reason": "manual"}])                # zero-length drop too
    plan = build_render_plan(edl)
    for c in plan["clips"]:
        assert c["src_out"] > c["src_in"], plan["clips"]


# ---- H4 (iOS Loop): fixture parity for EditorView.computeOps()'s canonical
# op order (cuts/mutes by ORIGINAL index -> reorder -> trims -> captions/music).
# This is a cross-layer contract test: it applies ops in the EXACT sequence
# EditorView.swift emits them and pins the result. Given F1 (trim walks PLAY
# order), applying reorder BEFORE trim means "trim the start/end" refers to
# whatever the creator's reorder just put at the front/back — the least-
# surprising reading, since the editor UI displays segments in that same
# (reordered) order. If EditorView.swift's op order ever changes, update the
# `ops` list below to match and re-verify the expected result by hand. ----

def test_ios_canonical_op_order_end_to_end():
    from app.edl import apply_edl_ops
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 100},
                              {"src_in": 100, "src_out": 200},
                              {"src_in": 200, "src_out": 300}])
    ops = [
        {"type": "cut_range", "start_frame": 100, "end_frame": 200},   # cut original segment 1
        {"type": "reorder_segments", "order": [2, 0, 1]},              # segment 2 now plays first
        {"type": "trim_start", "frames": 50},                          # trims the NEW first segment (2)
        {"type": "set_captions_enabled", "enabled": False},
    ]
    out, results = apply_edl_ops(edl, ops)
    assert all(r["applied"] for r in results), results
    assert out["segments"] == [{"src_in": 0, "src_out": 100},
                                {"src_in": 100, "src_out": 200},
                                {"src_in": 250, "src_out": 300}]       # segment 2 trimmed, not segment 0
    assert out["drops"] == [{"src_in": 100, "src_out": 200, "reason": "manual"}]
    assert out["segment_order"] == [2, 0, 1]
    assert out["captions"] == []


# ---- H7 (iOS Loop): GET must expose source_url so the manual editor's local
# rough-cut preview can play the original footage ----

def test_get_clip_exposes_source_url():
    job_id = seed_clip_job(source_url="https://cdn.example/take.mov")
    assert client.get(f"/v1/clips/{job_id}").json()["source_url"] == "https://cdn.example/take.mov"


# ---- F5 (no-repro, pinned): out-of-bounds ops already rejected, not clamped ----

def test_way_out_of_bounds_cut_range_rejected_not_cut_everything():
    from app.edl import apply_edl_ops
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 300}])
    out, res = apply_edl_ops(edl, [{"type": "cut_range", "start_frame": 5000, "end_frame": 6000}])
    assert res[0]["applied"] is False
    assert out["segments"] == [{"src_in": 0, "src_out": 300}]   # untouched


def test_reversed_and_negative_ranges_rejected():
    from app.edl import apply_edl_ops
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 300}])
    for op in ({"type": "cut_range", "start_frame": 200, "end_frame": 100},
               {"type": "cut_range", "start_frame": -500, "end_frame": -100}):
        _, res = apply_edl_ops(edl, [op])
        assert res[0]["applied"] is False, op


def test_malformed_op_input_never_crashes():
    from app.edl import apply_edl_ops
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 300}])
    _, res = apply_edl_ops(edl, [{"type": "mute_range", "start_frame": "abc", "end_frame": 100}])
    assert res[0]["applied"] is False
    assert "malformed" in res[0]["reason"]


def test_overlong_end_clamped_but_min_duration_guard_still_blocks_whole_clip_cut():
    from app.edl import apply_edl_ops
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 300}])
    _, res = apply_edl_ops(edl, [{"type": "cut_range", "start_frame": 0, "end_frame": 100000}])
    assert res[0]["applied"] is False
    assert "2 seconds" in res[0]["reason"]


# ---- F4 (no-repro, pinned): overlap handling was already correct ----

def test_overlapping_mute_ranges_produce_no_overlap():
    from app.edl import apply_edl_ops
    out, res = apply_edl_ops(_base_edl(segments=[{"src_in": 0, "src_out": 300}]), [
        {"type": "mute_range", "start_frame": 0, "end_frame": 50},
        {"type": "mute_range", "start_frame": 40, "end_frame": 60},
    ])
    ranges = sorted((v["src_in"], v["src_out"]) for v in out["audio"]["volume_ranges"])
    for i in range(len(ranges) - 1):
        assert ranges[i][1] <= ranges[i + 1][0], f"overlap found: {ranges}"


def test_kept_frames_unions_overlapping_drops():
    from app.edl import _kept_frames
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 300}], drops=[
        {"src_in": 100, "src_out": 200, "reason": "manual"},
        {"src_in": 150, "src_out": 250, "reason": "manual"},   # overlaps by 50f
    ])
    # union of [100,200) and [150,250) = [100,250) = 150f cut → 300-150=150 kept,
    # NOT double-subtracted (which would wrongly report 300-100-100=100).
    assert _kept_frames(edl) == 150


# ---- F3: overlays/b-roll must not silently drop a non-adjacent piece ----

def test_overlay_survives_reorder_as_two_pieces():
    from app.edl import build_render_plan
    edl = _base_edl(
        segments=[{"src_in": 0, "src_out": 100}, {"src_in": 100, "src_out": 200}],
        segment_order=[1, 0],
        overlays=[{"src_in": 50, "src_out": 150, "type": "punch_in"}])
    plan = build_render_plan(edl)
    assert len(plan["overlays"]) == 2
    assert {(o["frame_in"], o["frame_out"]) for o in plan["overlays"]} == {(0, 50), (150, 200)}


def test_overlay_adjacent_pieces_still_merge():
    # Same single-segment-straddles-a-drop case as test_render_plan_overlay_
    # remapped_and_clamped in test_main.py — pinned here too so a future change
    # to map_range_all can't silently un-merge the common (non-reorder) case.
    from app.edl import build_render_plan
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 200}],
                    drops=[{"src_in": 80, "src_out": 100, "reason": "dead_air"}],
                    overlays=[{"src_in": 60, "src_out": 120, "type": "punch_in"}])
    plan = build_render_plan(edl)
    assert len(plan["overlays"]) == 1
    assert (plan["overlays"][0]["frame_in"], plan["overlays"][0]["frame_out"]) == (60, 100)


def test_broll_survives_reorder_as_two_pieces():
    from app.edl import build_render_plan
    edl = _base_edl(
        segments=[{"src_in": 0, "src_out": 100}, {"src_in": 100, "src_out": 200}],
        segment_order=[1, 0],
        broll=[{"src_in": 50, "src_out": 150, "cue_text": "city skyline",
                "resolved_url": "https://cdn/skyline.mp4"}])
    plan = build_render_plan(edl)
    assert len(plan["broll"]) == 2
    assert {(b["frame_in"], b["frame_out"]) for b in plan["broll"]} == {(0, 50), (150, 200)}


# ---- F6: unresolved b-roll must fail-soft, never a None-URL render layer ----

def test_unresolved_broll_stripped_from_render_plan():
    from app.edl import build_render_plan
    edl = _base_edl(
        segments=[{"src_in": 0, "src_out": 200}],
        broll=[{"src_in": 20, "src_out": 80, "cue_text": "unresolved",
                "source": "stock", "resolved_url": None},
               {"src_in": 100, "src_out": 150, "cue_text": "resolved",
                "source": "stock", "resolved_url": "https://cdn/office.mp4"}])
    plan = build_render_plan(edl)
    assert len(plan["broll"]) == 1
    assert plan["broll"][0]["cue_text"] == "resolved"


# ---- regression: AssemblyAI auto_highlights bool crashed real edits ----
# _poll_transcription used to fall back to data["auto_highlights"] (a bool flag)
# when highlight results were empty; _extract_emphasis_regions then iterated a
# bool → "'bool' object is not iterable", failing every low-highlight recording.

def test_extract_emphasis_regions_survives_bool_and_junk():
    assert main._extract_emphasis_regions([], True) == []      # the exact poison value
    assert main._extract_emphasis_regions([], False) == []
    assert main._extract_emphasis_regions([], None) == []
    # a valid highlight still yields a span; a non-dict entry is skipped, not fatal
    out = main._extract_emphasis_regions(
        [], [True, {"timestamps": [{"start": 1000, "end": 2000}]}])
    assert out and out[0][1] > out[0][0]


def test_poll_transcription_never_returns_bool_highlights(monkeypatch):
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "k")
    class FakeResp:
        status_code = 200
        def json(self):
            # AssemblyAI shape when there are no highlights: results empty,
            # but the boolean request-echo flag is present.
            return {"status": "completed",
                    "words": [{"text": "hi", "start": 0, "end": 400, "confidence": 0.9}],
                    "auto_highlights": True,
                    "auto_highlights_result": {"status": "success", "results": []}}
    async def fake_get(self, url, headers=None):
        return FakeResp()
    monkeypatch.setattr(main.httpx.AsyncClient, "get", fake_get)
    out = asyncio.run(main._poll_transcription("t123", max_wait_s=5))
    assert isinstance(out["auto_highlights"], list)   # never a bool again
    # and the downstream consumer stays crash-free
    assert main._extract_emphasis_regions(out["words"], out["auto_highlights"]) == []
