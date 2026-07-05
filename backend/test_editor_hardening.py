"""Editor pipeline hardening gate — the Ralph loop's pass/fail signal.

Every test runs KEYLESS. Live-path behavior is exercised by monkeypatching the
external seams (AssemblyAI submit/poll, the Remotion bridge) so the real pipeline
code runs end-to-end with injected failures. The core contract under test:

    A clip job ALWAYS lands in a terminal state, fast, with a structured error —
    never a silent hang, never "ready" without a playable render_url.
"""
import asyncio
import time

from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)

SCRIPT = {"hook": "Test hook", "body": "Body text here", "cta": "Follow", "formatId": "myth-buster"}


def _make_live_job(monkeypatch, **env):
    """Create a clip job wired for the LIVE code path (keys faked), with the
    external seams monkeypatched by each test. Returns job_id."""
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "test-key")
    for k, v in env.items():
        monkeypatch.setattr(main, k, v)
    r = client.post("/v1/clips", json={
        "source_id": "src1", "script": SCRIPT, "style": "talking_head",
        "formats": ["myth-buster"], "source_url": "https://example.com/video.mov",
    })
    assert r.status_code == 200
    return r.json()["job_id"]


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
    r = client.post("/v1/clips", json={
        "source_id": "s", "script": SCRIPT, "style": "talking_head",
        "formats": ["myth-buster"], "source_url": "mock://source"})
    job_id = r.json()["job_id"]
    job = main._clip_jobs[job_id]
    clip_id = job["clips"][0]["clip_id"]
    async def exploding_submit(*a, **k):
        raise RuntimeError("mid-flight death")
    monkeypatch.setattr(main, "_submit_remotion_render", exploding_submit)
    job["clips"][0]["render_url"] = "https://prev.example/v.mp4"
    asyncio.run(main._rerender_clip(job_id, clip_id))
    clip = job["clips"][0]
    assert clip["status"] == "ready"                       # prev URL restored
    assert clip["render_url"] == "https://prev.example/v.mp4"


def test_rerender_no_prev_url_fails_structured(monkeypatch):
    r = client.post("/v1/clips", json={
        "source_id": "s", "script": SCRIPT, "style": "talking_head",
        "formats": ["myth-buster"], "source_url": "mock://source"})
    job_id = r.json()["job_id"]
    job = main._clip_jobs[job_id]
    clip = job["clips"][0]
    clip["render_url"] = None
    async def exploding_submit(*a, **k):
        raise main.PipelineError("render_submit_failed", "no bridge", "render")
    monkeypatch.setattr(main, "_submit_remotion_render", exploding_submit)
    asyncio.run(main._rerender_clip(job_id, clip["clip_id"]))
    assert clip["status"] == "failed" and clip["error"] == "render_submit_failed"


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
    r = client.post("/v1/clips", json={
        "source_id": "s", "script": SCRIPT, "style": "talking_head",
        "formats": ["myth-buster"], "source_url": "mock://source"})
    job_id = r.json()["job_id"]
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
    r = client.post("/v1/clips", json={
        "source_id": "s", "script": SCRIPT, "style": "talking_head",
        "formats": ["myth-buster"], "source_url": "mock://source"})
    job_id = r.json()["job_id"]
    clip_id = r.json()["clips"][0]["clip_id"]
    out = client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id,
        "ops": [{"type": "set_caption_style", "style": "karaoke"}],
    }).json()
    assert out["mode"] == "direct"
    assert any(a["type"] == "set_caption_style" for a in out["applied"])
    edl = client.get(f"/v1/clips/{job_id}").json()["edl"]
    assert edl["caption_style"] == "karaoke"


def test_tweak_direct_ops_unknown_skipped():
    r = client.post("/v1/clips", json={
        "source_id": "s", "script": SCRIPT, "style": "talking_head",
        "formats": ["myth-buster"], "source_url": "mock://source"})
    job_id = r.json()["job_id"]
    clip_id = r.json()["clips"][0]["clip_id"]
    out = client.post(f"/v1/clips/{job_id}/tweak", json={
        "clip_id": clip_id,
        "ops": [{"type": "definitely_not_an_op"}],
    }).json()
    assert out["mode"] == "direct"
    assert not out["applied"]
    assert out["skipped"]


def test_tweak_requires_instruction_or_ops():
    r = client.post("/v1/clips", json={
        "source_id": "s", "script": SCRIPT, "style": "talking_head",
        "formats": ["myth-buster"], "source_url": "mock://source"})
    job_id = r.json()["job_id"]
    clip_id = r.json()["clips"][0]["clip_id"]
    assert client.post(f"/v1/clips/{job_id}/tweak",
                       json={"clip_id": clip_id}).status_code == 422


def test_get_clip_job_include_words():
    r = client.post("/v1/clips", json={
        "source_id": "s", "script": SCRIPT, "style": "talking_head",
        "formats": ["myth-buster"], "source_url": "mock://source"})
    job_id = r.json()["job_id"]
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
