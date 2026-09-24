"""Editor audit (2026-09-24) — keyless regression tests.

Every test runs with NO vendor keys (same seams as test_long_take_hardening.py). The QA seam
tests pin the dev-only long-clip harness so it can never serve in a keyed (prod) process.
"""
import os

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def _media_dir(tmp_path, secs=60):
    p = tmp_path / f"{secs}.mov"
    p.write_bytes(b"\x00" * 64)
    return str(tmp_path)


def test_demo_src_job_spans_real_duration(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MEDIA_DIR", _media_dir(tmp_path, 600))
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._clip_jobs.pop("demo-src-600", None)
    r = client.get("/v1/clips/demo-src-600", params={"include_words": 1})
    assert r.status_code == 200
    b = r.json()
    assert b["source_url"].endswith("/v1/dev/media/600.mov")
    words = b["words"]
    assert words and words[-1]["end_ms"] > 590_000          # words cover the whole take
    segs = b["edl"]["segments"]
    assert segs[0]["src_in"] == 0 and segs[-1]["src_out"] == 600 * 30
    assert all(a["src_out"] <= b2["src_in"] for a, b2 in zip(segs, segs[1:]))   # monotonic
    assert len(b["edl"]["captions"]) == len(words)


def test_demo_src_tagged_ids_are_independent_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MEDIA_DIR", _media_dir(tmp_path, 60))
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    for jid in ("demo-src-60-se", "demo-src-60-pm"):
        main._clip_jobs.pop(jid, None)
        assert client.get(f"/v1/clips/{jid}").json()["source_url"].endswith("/60.mov")
    assert main._clip_jobs["demo-src-60-se"] is not main._clip_jobs["demo-src-60-pm"]


def test_demo_src_without_media_falls_back_to_placeholder(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_MEDIA_DIR", raising=False)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._clip_jobs.pop("demo-src-45", None)
    b = client.get("/v1/clips/demo-src-45").json()
    assert b["source_url"] is None                          # the classic placeholder demo job


def test_dev_media_route_never_serves_when_keyed(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MEDIA_DIR", _media_dir(tmp_path, 60))
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    assert client.get("/v1/dev/media/60.mov").status_code == 200
    assert client.get("/v1/dev/media/..%2Fetc").status_code == 404
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk-live")
    assert client.get("/v1/dev/media/60.mov").status_code == 404


# ---------------------------------------------------------------------------
# LV-1 — mint advertises what storage will actually accept (50 MiB project limit)
# ---------------------------------------------------------------------------

_MINT_BODY = {"filename": "take.mov", "content_type": "video/quicktime"}


def test_mint_mock_branch_advertises_storage_clamped_cap(monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_URL", "")
    monkeypatch.setattr(main, "SUPABASE_KEY", "")
    b = client.post("/v1/uploads/mint", json=_MINT_BODY).json()
    assert b["mode"] == "mock"
    # Defaults: 150MB product ceiling vs 50 MiB storage limit → 48 MiB advertised.
    assert main.MAX_UPLOAD_BYTES == 150_000_000
    assert main.STORAGE_OBJECT_LIMIT_BYTES == 52_428_800
    assert b["max_upload_bytes"] == 52_428_800 - 2 * 1024 * 1024 == 50_331_648
    assert b["max_upload_bytes"] < 52_428_801            # the size proven to 413 live


def test_mint_live_branch_advertises_storage_clamped_cap(monkeypatch):
    import asyncio

    class _Resp:
        status_code = 200

        def json(self):
            return {"url": "/object/upload/sign/marque-clips/uploads/x/take.mov?token=t"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(main, "SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setattr(main, "SUPABASE_KEY", "service-key")
    monkeypatch.setattr(main.httpx, "AsyncClient", _Client)
    out = asyncio.run(main._mint_supabase_upload("take.mov"))
    assert out["mode"] == "live"
    assert out["max_upload_bytes"] == 50_331_648          # never the 150MB product ceiling


def test_upload_cap_follows_both_knobs(monkeypatch):
    # Raising the dashboard limit + STORAGE_OBJECT_LIMIT_BYTES lifts the cap up to the
    # product ceiling; a product ceiling below the storage limit still binds.
    monkeypatch.setattr(main, "STORAGE_OBJECT_LIMIT_BYTES", 256 * 1024 * 1024)
    assert main._advertised_upload_cap_bytes() == main.MAX_UPLOAD_BYTES
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 30_000_000)
    assert main._advertised_upload_cap_bytes() == 30_000_000
    # Misconfigured storage limit smaller than the headroom: raw limit, never <= 0.
    monkeypatch.setattr(main, "STORAGE_OBJECT_LIMIT_BYTES", 1_000_000)
    assert main._advertised_upload_cap_bytes() == 1_000_000


# ---------------------------------------------------------------------------
# LV-20 — audio-only probes (-vn), duration-scaled timeouts, children killed on timeout
# ---------------------------------------------------------------------------

import asyncio as _aio
import inspect as _inspect
import re as _re
import sys as _sys
import time as _time

from app import audio as _audio
from app import subproc as _subproc


class _SlowProc:
    """A child that never finishes on its own — records whether it was killed/reaped."""

    def __init__(self, argv=()):
        self.argv = list(argv)
        self.killed = False
        self.waited = False
        self.returncode = None

    async def communicate(self, input=None):
        await _aio.sleep(3600)

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        self.waited = True
        return self.returncode


def _patch_slow_exec(monkeypatch):
    procs: list[_SlowProc] = []

    async def fake_exec(*argv, **kw):
        p = _SlowProc(argv)
        procs.append(p)
        return p
    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(main.shutil, "which", lambda name: f"/usr/bin/{name}")
    return procs


def test_audio_only_probe_args_never_map_video():
    for args in (_audio.loudness_probe_args("https://cdn/t.mov"),
                 _audio.silence_detect_args("https://cdn/t.mov"),
                 _audio.loudnorm_pass1_args("https://cdn/t.mov"),
                 _audio.snr_probe_args("https://cdn/t.mov")):
        for flag in ("-vn", "-sn", "-dn"):
            assert flag in args, args
            # OUTPUT options: after the input, before the `-f null -` sink.
            assert args.index("-i") < args.index(flag) < args.index("-f")
        assert args[-3:] == ["-f", "null", "-"]
    # The finalize APPLY pass writes the deliverable — it must keep (copy) its video.
    p2 = _audio.loudnorm_pass2_args("u", {"input_i": -20, "input_tp": -2, "input_lra": 5,
                                          "input_thresh": -30}, "/tmp/o.mp4")
    assert "-vn" not in p2 and p2[p2.index("-c:v") + 1] == "copy"


def test_probe_timeouts_scale_with_duration():
    assert _audio.analysis_timeout_s(None) == 60.0            # unknown → old flat value
    assert _audio.analysis_timeout_s(120) == 60.0             # short take → floor
    assert _audio.analysis_timeout_s(600) == 150.0            # 10-min take → 0.25 s/s
    assert _audio.analysis_timeout_s(600, floor_s=180) == 180.0
    assert main._finalize_timeouts_s(None) == (180.0, 90.0)   # historical floors kept
    assert main._finalize_timeouts_s(1200) == (300.0, 300.0)  # 20-min output scales both


def test_communicate_or_kill_kills_and_reaps_a_slow_child():
    async def run():
        p = await _aio.create_subprocess_exec(
            _sys.executable, "-c", "import time; time.sleep(30)")
        t0 = _time.monotonic()
        try:
            await _subproc.communicate_or_kill(p, 0.2)
            raise AssertionError("expected a timeout")
        except TimeoutError:
            pass
        return p, _time.monotonic() - t0
    p, took = _aio.run(run())
    assert p.returncode == -9          # SIGKILLed AND reaped (returncode only set once waited)
    assert took < 5


def test_communicate_or_kill_kills_on_cancellation():
    # The poster path runs under an outer wait_for(8s) that CANCELS it mid-seek — the old
    # `except Exception` never saw the CancelledError and orphaned the ffmpeg.
    async def run():
        p = await _aio.create_subprocess_exec(
            _sys.executable, "-c", "import time; time.sleep(30)")
        task = _aio.ensure_future(_subproc.communicate_or_kill(p, 60))
        await _aio.sleep(0.2)
        task.cancel()
        try:
            await task
            raise AssertionError("expected cancellation")
        except _aio.CancelledError:
            pass
        return p
    assert _aio.run(run()).returncode == -9


def test_probe_loudness_timeout_kills_ffmpeg(monkeypatch):
    procs = _patch_slow_exec(monkeypatch)
    out = _aio.run(_audio.probe_loudness("https://cdn/take.mov", timeout_s=0.05))
    assert out is None
    assert procs and procs[0].killed and procs[0].waited
    assert "-vn" in procs[0].argv


def test_silence_scan_timeout_kills_ffmpeg(monkeypatch):
    procs = _patch_slow_exec(monkeypatch)
    out = _aio.run(_audio.detect_silence_spans("https://cdn/take.mov", timeout_s=0.05))
    assert out is None and procs[0].killed and procs[0].waited


def test_finalize_pass1_timeout_kills_ffmpeg(monkeypatch):
    procs = _patch_slow_exec(monkeypatch)
    monkeypatch.setattr(main, "AUDIO_FINALIZE", True)
    monkeypatch.setattr(main, "VOICE_ENHANCE", False)
    monkeypatch.setattr(main, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(main, "SUPABASE_KEY", "k")
    monkeypatch.setattr(main, "FINALIZE_PASS1_TIMEOUT_S", 0.05)
    out = _aio.run(main._finalize_audio_loudness("https://cdn/render.mp4", "job-lv20"))
    assert out is None                                   # fail-soft: keep the Lambda URL
    assert len(procs) == 1 and procs[0].killed and procs[0].waited
    assert "-vn" in procs[0].argv                        # pass-1 never decodes the video


def test_ffprobe_duration_timeout_kills_ffprobe(monkeypatch):
    procs = _patch_slow_exec(monkeypatch)
    assert _aio.run(main._ffprobe_duration_s("https://cdn/r.mp4", timeout_s=0.05)) is None
    assert procs[0].killed and procs[0].waited


def test_poster_cancelled_by_outer_wait_kills_ffmpeg(monkeypatch):
    procs = _patch_slow_exec(monkeypatch)
    monkeypatch.setattr(main, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(main, "SUPABASE_KEY", "k")

    async def run():
        try:
            await _aio.wait_for(main._generate_poster("https://cdn/r.mp4", "j", "c"), 0.1)
        except TimeoutError:
            pass
    _aio.run(run())
    assert procs and all(p.killed and p.waited for p in procs)


def test_no_unbounded_ffmpeg_wait_remains():
    # Every ffmpeg/ffprobe wait goes through communicate_or_kill. The only raw
    # wait_for(...communicate(...)) left is the node render bridge, which kills itself.
    pat = _re.compile(r"wait_for\(\s*\w+\.communicate\(")
    bridge = _inspect.getsource(main._run_render_bridge)
    in_main = len(pat.findall(_inspect.getsource(main))) - len(pat.findall(bridge))
    assert in_main == 0
    assert not pat.findall(_inspect.getsource(_audio))


# ---------------------------------------------------------------------------
# LV-21 — Anthropic read timeout scales with the call's own max_tokens
# ---------------------------------------------------------------------------

def test_anthropic_read_timeout_scales_with_max_tokens():
    t = main._anthropic_timeout
    assert t(0).read == 90.0 and t(1000).read == 90.0          # short calls keep the old 90s
    assert t(3000).read == 135.0                              # 60 + 3000/40
    assert t(main._plan_max_tokens(1500)).read == 285.0       # 9000-token plan (~10-min take)
    assert t(16000).read == 420.0 and t(10 ** 6).read == 420.0  # clamped
    for x in (t(0), t(16000)):
        assert x.connect <= 15 and x.write <= 30 and x.pool <= 30   # the rest stay bounded


def test_anthropic_passes_scaled_timeout_per_request(monkeypatch):
    seen: list = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"content": [{"text": "{\"ok\": true}"}]}

    class _Client:
        async def post(self, url, headers=None, json=None, timeout=None):
            seen.append((json["max_tokens"], timeout))
            return _Resp()
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "_get_anthropic_client", lambda: _Client())
    _aio.run(main.anthropic("s", "u", main.OPUS, 16000))
    _aio.run(main.anthropic_json("s", "u", {"type": "object"}, main.OPUS,
                                 main._plan_max_tokens(1500)))
    _aio.run(main.anthropic("s", "u", main.HAIKU, 500))
    assert [(mt, to.read) for mt, to in seen] == [(16000, 420.0), (9000, 285.0), (500, 90.0)]


# ---------------------------------------------------------------------------
# LV-22 — the job watchdog spares LIVE pipelines up to a duration-scaled ceiling
# ---------------------------------------------------------------------------

def _stage_job(jid, age_s, **over):
    now = _time.time()
    job = {"job_id": jid, "status": "editing", "created_at": now - age_s,
           "stage_started_at": now - age_s, "clips": [], "pipeline_gen": 0,
           "duration_ms": 600_000}                        # a 10-minute take
    job.update(over)
    return job


def _sweep_with_owner(monkeypatch, job, alive: bool):
    """Run one sweep over {job}; when `alive`, a real task owns the job's pipeline."""
    monkeypatch.setattr(main, "RENDER_WATCHDOG_S", 480)

    async def fake_persist(jid):
        pass
    monkeypatch.setattr(main, "_persist_clip_job", fake_persist)

    async def run():
        t = None
        if alive:
            t = main._spawn(_aio.sleep(30))
            main._pipeline_tasks[job["job_id"]] = t
        try:
            main._sweep_stuck_renders({job["job_id"]: job})
            await _aio.sleep(0)
        finally:
            if t is not None:
                t.cancel()
                main._pipeline_tasks.pop(job["job_id"], None)
    _aio.run(run())
    return job


def test_live_long_job_survives_flat_watchdog(monkeypatch):
    job = _sweep_with_owner(monkeypatch, _stage_job("lv22-live", 1000), alive=True)
    assert job["status"] == "editing" and job["pipeline_gen"] == 0   # untouched at 1000s


def test_orphan_still_failed_at_flat_watchdog(monkeypatch):
    job = _stage_job("lv22-orphan", 1000, resume_count=main._RESUME_MAX)   # resumes spent
    job = _sweep_with_owner(monkeypatch, job, alive=False)
    assert job["status"] == "failed" and job["error"] == "pipeline_interrupted"


def test_live_job_past_scaled_ceiling_is_failed(monkeypatch):
    assert main._live_pipeline_ceiling_s(_stage_job("x", 0), 480) == 960 + 3 * 600
    job = _sweep_with_owner(monkeypatch, _stage_job("lv22-over", 2800), alive=True)
    assert job["status"] == "failed" and job["error"] == "pipeline_interrupted"
    assert job["pipeline_gen"] == 1                  # the live task can no longer write


def test_live_ceiling_is_duration_earned_and_capped():
    base = _stage_job("x", 0)
    base.pop("duration_ms")
    assert main._live_pipeline_ceiling_s(base, 480) == 960            # unknown → no exemption
    words = [{"word": "w", "start_ms": 599_000, "end_ms": 600_000}]
    assert main._live_pipeline_ceiling_s({**base, "words": words}, 480) == 2760   # transcript
    assert main._live_pipeline_ceiling_s({"duration_ms": 3_600_000}, 480) == 3600  # capped


# ---------------------------------------------------------------------------
# LV-34 — a sub-frame filler must never produce a zero-frame drop, and a degenerate
# drop must be repaired, not traded for the untailored safe default
# ---------------------------------------------------------------------------

from app.edl import (assemble_edl as _assemble_edl, check_edl_invariants as _check_inv,
                     detect_disfluencies as _detect_disfluencies, strip_fillers as _strip_fillers)

# Verbatim slice of the prod 5-minute take (job d300-fix-a5f9a674, words 359-367): the
# 10ms "Um" at 115600-115610ms rounds to frame 3468 on both ends.
_UM_SLICE = [
    {"word": "and", "start_ms": 114550, "end_ms": 114695, "confidence": 0.99587655, "type": None},
    {"word": "actually", "start_ms": 114695, "end_ms": 115160, "confidence": 0.9997335, "type": None},
    {"word": "call", "start_ms": 115256, "end_ms": 115465, "confidence": 0.99967563, "type": None},
    {"word": "them.", "start_ms": 115513, "end_ms": 115600, "confidence": 0.9907142, "type": None},
    {"word": "Um", "start_ms": 115600, "end_ms": 115610, "confidence": 0.91404605, "type": None},
    {"word": "Part", "start_ms": 116139, "end_ms": 116459, "confidence": 0.9962999, "type": None},
    {"word": "3:", "start_ms": 116459, "end_ms": 116652, "confidence": 0.83016956, "type": None},
    {"word": "Most", "start_ms": 117037, "end_ms": 117326, "confidence": 0.9890637, "type": None},
    {"word": "fashion", "start_ms": 117342, "end_ms": 117711, "confidence": 0.9900121, "type": None},
]


def _degenerate(drops):
    return [d for d in drops
            if (d["src_out"] if isinstance(d, dict) else d.src_out)
            <= (d["src_in"] if isinstance(d, dict) else d.src_in)]


def test_sub_frame_filler_never_yields_a_degenerate_drop():
    kept, drops = _strip_fillers(_UM_SLICE)
    assert _degenerate(drops) == []                     # was [Drop(3468, 3468, 'filler')]
    assert "Um" not in [w["word"] for w in kept]         # still removed from the captions


def test_disfluency_detectors_never_emit_degenerate_drops():
    words = [
        {"word": "a", "start_ms": 72989, "end_ms": 73005},             # 16ms stutter "a a"
        {"word": "a", "start_ms": 73020, "end_ms": 73200},
        {"word": "plan", "start_ms": 73260, "end_ms": 73600},
        {"word": "hmm", "start_ms": 74000, "end_ms": 74010, "confidence": 0.1},   # sub-frame garble
        {"word": "works", "start_ms": 74100, "end_ms": 74500},
    ]
    for level in ("conservative", "default", "aggressive"):
        assert _degenerate(_detect_disfluencies(words, level)) == []


def test_assembled_real_slice_has_no_hard_drop_issue():
    edl = _assemble_edl({}, _UM_SLICE, "talking_head", "myth-buster").model_dump()
    assert [i for i in _check_inv(edl, _UM_SLICE) if "src_out<=src_in" in i] == []


def test_repair_strips_degenerate_drop_and_keeps_plan():
    edl = _assemble_edl({}, _UM_SLICE, "talking_head", "myth-buster").model_dump()
    real_drops = list(edl["drops"])
    edl["drops"] = real_drops + [{"src_in": 3468, "src_out": 3468, "reason": "filler"}]
    repaired, hard = main._repair_edl_hard_issues(edl, _UM_SLICE)
    assert hard == []
    assert repaired["drops"] == real_drops               # only the zero-frame drop went


def test_repair_strips_zero_length_segment_and_remaps_order():
    edl = {"style": "talking_head", "drops": [], "overlays": [], "broll": [],
           "segments": [{"src_in": 0, "src_out": 200}, {"src_in": 200, "src_out": 200},
                        {"src_in": 200, "src_out": 400}],
           "segment_order": [2, 1, 0]}
    repaired, hard = main._repair_edl_hard_issues(edl, [])
    assert hard == []
    assert repaired["segments"] == [{"src_in": 0, "src_out": 200}, {"src_in": 200, "src_out": 400}]
    assert repaired["segment_order"] == [1, 0]           # still the same playback order


def test_repair_leaves_structural_issues_hard():
    only = {"style": "talking_head", "drops": [], "segments": [{"src_in": 50, "src_out": 50}]}
    assert main._repair_edl_hard_issues(only, [])[1]     # never strips the last segment
    overlap = {"style": "talking_head", "drops": [],
               "segments": [{"src_in": 0, "src_out": 300}, {"src_in": 100, "src_out": 400}]}
    assert main._repair_edl_hard_issues(overlap, [])[1]  # a real overlap still → safe default


def test_plan_author_keeps_tailored_edit_despite_degenerate_drop(monkeypatch):
    real_assemble = main.assemble_edl

    class _Dumped:
        def __init__(self, d):
            self._d = d

        def model_dump(self):
            return self._d

    def assemble_with_degenerate_drop(*a, **k):
        d = real_assemble(*a, **k).model_dump()
        d["drops"] = list(d.get("drops") or []) + [
            {"src_in": 3468, "src_out": 3468, "reason": "filler"}]
        return _Dumped(d)

    async def fake_plan(*a, **k):
        return {"cuts": [], "keeps": [], "broll": []}      # a real (tailored) plan came back

    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "AI_QUALITY", True)
    monkeypatch.setattr(main, "anthropic_json", fake_plan)
    monkeypatch.setattr(main, "assemble_edl", assemble_with_degenerate_drop)
    job = {"job_id": "lv34", "brand": {}, "edit_brief": None, "config": {},
           "_silent_spans": None}
    edl, llm_contributed, plan = _aio.run(main._author_edl_via_plan(
        job, "talking_head", {"formatId": "myth-buster"}, _UM_SLICE, {}, None))
    assert edl is not None, "a zero-frame drop must not discard the tailored edit"
    assert llm_contributed is True and plan
    assert _degenerate(edl["drops"]) == []


# ---------------------------------------------------------------------------
# LV-23 — queue time is not render time; poll ceiling keeps scaling; the clip watchdog
# covers the post-render tail
# ---------------------------------------------------------------------------

def _rendering_job(jid, clip, status="ready", stage_age=5.0):
    now = _time.time()
    return {jid: {"job_id": None, "status": status, "created_at": now - stage_age,
                  "stage_started_at": now - stage_age, "clips": [clip]}}


def test_queued_clip_is_not_swept_even_with_an_ancient_stamp(monkeypatch):
    monkeypatch.setattr(main, "RENDER_WATCHDOG_S", 480)
    now = _time.time()
    clip = {"clip_id": "c1", "status": "rendering", "render_gen": 3,
            "render_started_at": now - 99_999, "render_queued_at": now - 900}
    main._sweep_stuck_renders(_rendering_job("q1", clip))
    assert clip["status"] == "rendering" and clip["render_gen"] == 3   # waiting ≠ stalled


def test_queue_backstop_fails_a_clip_waiting_past_the_max(monkeypatch):
    now = _time.time()
    clip = {"clip_id": "c1", "status": "rendering", "render_gen": 3,
            "render_queued_at": now - main.RENDER_QUEUE_MAX_S - 5}
    main._sweep_stuck_renders(_rendering_job("q2", clip))
    assert clip["status"] == "failed" and clip["error"] == "render_stalled"
    assert "render_queued_at" not in clip and clip["render_gen"] == 4


def test_queued_clip_spares_its_rendering_job(monkeypatch):
    monkeypatch.setattr(main, "RENDER_WATCHDOG_S", 480)
    now = _time.time()
    clip = {"clip_id": "c1", "status": "rendering", "render_queued_at": now - 600}
    jobs = _rendering_job("q3", clip, status="rendering", stage_age=10_000)
    main._sweep_stuck_renders(jobs)
    assert jobs["q3"]["status"] == "rendering" and clip["status"] == "rendering"


def test_burst_queue_wait_never_kills_another_creators_render(monkeypatch):
    async def scenario():
        for k in ("REMOTION_SERVE_URL", "REMOTION_ACCESS_KEY", "REMOTION_SECRET",
                  "REMOTION_FUNCTION_NAME"):
            monkeypatch.setattr(main, k, "x")
        monkeypatch.setattr(main, "RENDER_WATCHDOG_S", 480)

        async def bridge(*args, timeout_s=None, **kwargs):
            if args[0] == "submit":
                return {"renderId": "r1", "bucketName": "b"}
            return {"done": True, "outputFile": "https://cdn/out.mp4"}
        monkeypatch.setattr(main, "_run_render_bridge", bridge)
        sem = _aio.Semaphore(1)
        monkeypatch.setattr(main, "_render_semaphore", sem)
        await sem.acquire()                                  # every slot busy (long renders)
        jid = "lv23-burst"
        main._clip_jobs[jid] = {
            "job_id": jid, "status": "rendering", "created_at": _time.time(),
            "stage_started_at": _time.time(), "style": "talking_head", "source_url": "mock://x",
            "edl": {"style": "talking_head", "format_id": "x",
                    "segments": [{"src_in": 0, "src_out": 300}]},
            "clips": [{"clip_id": "c1", "format": "myth-buster", "status": "queued"}]}
        task = _aio.ensure_future(main._render_all_clips(jid))
        await _aio.sleep(0.05)
        clip = main._clip_jobs[jid]["clips"][0]
        assert clip.get("render_queued_at")                  # visibly waiting for a slot
        # …for far longer than the 480s watchdog (half the queue backstop):
        clip["render_started_at"] -= main.RENDER_QUEUE_MAX_S / 2
        clip["render_queued_at"] -= main.RENDER_QUEUE_MAX_S / 2
        main._sweep_stuck_renders({jid: main._clip_jobs[jid]})
        assert clip["status"] == "rendering"                 # NOT failed as render_stalled
        sem.release()
        await task
        assert clip["status"] == "ready" and "render_queued_at" not in clip
        assert _time.time() - clip["render_started_at"] < 5  # clock started at acquisition
        main._clip_jobs.pop(jid, None)
    _aio.run(scenario())


def test_restore_drops_a_stale_queue_marker(monkeypatch):
    now = _time.time()

    class _Store:
        async def load_clip_job(self, job_id):
            return {"job_id": job_id, "status": "ready", "created_at": now - 9_000,
                    "clips": [{"clip_id": "c1", "status": "rendering",
                               "render_started_at": now - 9_000,
                               "render_queued_at": now - 60}]}   # persisted mid-wait
    monkeypatch.setattr(main, "_supabase_client", _Store())
    main._clip_jobs.pop("lv23-restored", None)
    job = _aio.run(main._restore_clip_job("lv23-restored"))
    clip = job["clips"][0]
    assert "render_queued_at" not in clip                # nobody is waiting in THIS process
    main._sweep_stuck_renders({"lv23-restored": job})
    assert clip["status"] == "failed"                    # judged by its (ancient) render clock
    main._clip_jobs.pop("lv23-restored", None)


def test_poll_budget_keeps_scaling_past_8000_frames():
    assert main.RENDER_POLL_CEIL_S == 2400
    assert main._scaled_render_budgets(8000)[0] == 1200
    assert main._scaled_render_budgets(9000)[0] == 1320      # was capped at 1200
    assert main._scaled_render_budgets(18000)[0] == 2400     # a 10-min output: full 240+0.12/f
    assert main._scaled_render_budgets(40000)[0] == 2400     # still capped far out


def test_clip_watchdog_window_covers_the_post_render_tail(monkeypatch):
    monkeypatch.setattr(main, "RENDER_WATCHDOG_S", 480)
    frames = 18000
    poll = main._scaled_render_budgets(frames)[0]
    tail = main._post_render_allowance_s(frames)
    p1, p2 = main._finalize_timeouts_s(frames / 30)
    assert tail >= p1 + p2 + main.POSTER_INLINE_WAIT_S
    assert main._clip_render_budget_s(frames) == poll + tail
    # Render finished right at its poll budget; finalize is 30s in → must NOT be swept.
    now = _time.time()
    clip = {"clip_id": "c1", "status": "rendering", "render_started_at": now - poll - 30,
            "render_budget_s": main._clip_render_budget_s(frames)}
    main._sweep_stuck_renders(_rendering_job("t1", clip))
    assert clip["status"] == "rendering"


def test_queued_preview_is_spared_and_preview_budget_scales(monkeypatch):
    monkeypatch.setattr(main, "RENDER_WATCHDOG_S", 480)
    now = _time.time()
    queued = {"clip_id": "c1", "status": "ready", "preview_status": "rendering",
              "preview_started_at": now - 99_999, "preview_queued_at": now - 600}
    long_preview = {"clip_id": "c2", "status": "ready", "preview_status": "rendering",
                    "preview_started_at": now - 600, "preview_budget_s": 900}
    stale = {"clip_id": "c3", "status": "ready", "preview_status": "rendering",
             "preview_started_at": now - 600}
    jobs = {"p": {"job_id": None, "status": "ready", "created_at": now,
                  "clips": [queued, long_preview, stale]}}
    main._sweep_stuck_renders(jobs)
    assert queued["preview_status"] == "rendering"
    assert long_preview["preview_status"] == "rendering"     # inside its scaled window
    assert stale["preview_status"] == "failed"               # flat window still applies


# ---------------------------------------------------------------------------
# LV-24 — no full-length self-review preview render for long outputs
# ---------------------------------------------------------------------------

def _review_job(jid, out_frames):
    job = {"job_id": jid, "status": "editing", "source_url": "mock://s", "style": "talking_head",
           "created_at": _time.time(),
           "clips": [{"clip_id": "c1", "format": "myth-buster", "status": "queued"}],
           "words": [{"word": "hi", "start_ms": 0, "end_ms": 300}],
           "edl": {"style": "talking_head", "format_id": "myth-buster",
                   "segments": [{"src_in": 0, "src_out": out_frames}],
                   "captions": [], "layout": {"style": "talking_head"}}}
    main._clip_jobs[jid] = job
    return job


def _count_preview_submits(monkeypatch):
    calls: list = []

    async def fake_submit(url, edl, fmt, style, preview=False):
        calls.append(preview)
        return None                                          # stop right after the submit
    monkeypatch.setattr(main, "_submit_remotion_render", fake_submit)
    monkeypatch.setattr(main, "SELF_REVIEW", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    return calls


def test_self_review_skips_long_outputs_and_says_why(monkeypatch):
    calls = _count_preview_submits(monkeypatch)
    job = _review_job("lv24-long", 9000)                     # 5-minute output
    _aio.run(main._self_review_edl("lv24-long"))
    assert calls == []                                       # no preview render at all
    sr = job["self_review"]
    assert sr["skipped"] == "long_take" and sr["max_frames"] == 5400
    assert sr["output_frames"] > 5400
    # observable on the poll payload (the #8 report card surfaces job["self_review"])
    assert client.get("/v1/clips/lv24-long").json()["self_review"]["skipped"] == "long_take"
    main._clip_jobs.pop("lv24-long", None)


def test_self_review_still_runs_for_short_outputs(monkeypatch):
    calls = _count_preview_submits(monkeypatch)
    job = _review_job("lv24-short", 900)                     # 30-second output
    _aio.run(main._self_review_edl("lv24-short"))
    assert calls == [True] and "self_review" not in job      # the preview was attempted
    monkeypatch.setattr(main, "SELF_REVIEW_MAX_FRAMES", 0)   # <= 0 disables the cap
    long_job = _review_job("lv24-uncapped", 9000)
    _aio.run(main._self_review_edl("lv24-uncapped"))
    assert calls == [True, True] and "self_review" not in long_job
    for jid in ("lv24-short", "lv24-uncapped"):
        main._clip_jobs.pop(jid, None)


# ---------------------------------------------------------------------------
# LV-25 — every clip_edit_sessions upsert refreshes updated_at
# ---------------------------------------------------------------------------

def test_upsert_clip_job_sends_a_fresh_updated_at(monkeypatch):
    import datetime as _dt
    import supabase_persistence as sp

    captured: list[dict] = []

    class _Resp:
        status_code = 201
        text = ""

    class _Http:
        async def request(self, method, url, params=None, json=None, headers=None):
            captured.append({"method": method, "url": url, "params": params,
                             "json": json, "headers": headers})
            return _Resp()

    store = sp.SupabaseClient("https://proj.supabase.co", "service-key")
    monkeypatch.setattr(store, "_pooled", lambda: _Http())
    before = _dt.datetime.now(_dt.timezone.utc)
    assert _aio.run(store.upsert_clip_job("job-lv25", {"status": "ready", "clips": []})) is True
    after = _dt.datetime.now(_dt.timezone.utc)

    req = captured[0]
    assert req["method"] == "POST" and req["url"].endswith("/rest/v1/clip_edit_sessions")
    assert req["params"] == {"on_conflict": "job_id"}
    assert "merge-duplicates" in req["headers"]["Prefer"]
    row = req["json"]
    assert row["job_id"] == "job-lv25" and row["state"]["status"] == "ready"
    stamp = _dt.datetime.fromisoformat(row["updated_at"])
    assert stamp.tzinfo is not None and stamp.utcoffset() == _dt.timedelta(0)   # UTC
    assert before <= stamp <= after                      # "now", not the insert time


# ---------------------------------------------------------------------------
# LV-26 — the orphan-resume rate limit is process-wide, not per sweep call
# ---------------------------------------------------------------------------

def _orphans(n, prefix):
    old = _time.time() - 3600
    return {f"{prefix}{i}": {"job_id": f"{prefix}{i}", "status": "processing",
                             "created_at": old, "stage_started_at": old, "clips": [],
                             "pipeline_gen": 0, "auto_confirm": True} for i in range(n)}


def _stub_resume_targets(monkeypatch):
    async def fake_auto(jid):
        pass
    monkeypatch.setattr(main, "_run_auto_pipeline", fake_auto)

    async def fake_persist(jid):
        pass
    monkeypatch.setattr(main, "_persist_clip_job", fake_persist)


def test_resume_rate_limit_is_global_across_sweep_callers(monkeypatch):
    _stub_resume_targets(monkeypatch)
    jobs = _orphans(5, "lv26-")

    async def run():
        for _caller in range(3):          # the liveness loop + two clients' GET polls
            main._sweep_stuck_renders(jobs)
            await _aio.sleep(0.01)
        resumed_now = sum(1 for j in jobs.values() if j.get("resume_count"))
        # One window later the backlog keeps draining at the same pace.
        for i in range(len(main._resume_log)):
            main._resume_log[i] -= main._RESUME_RATE_WINDOW_S + 1
        main._sweep_stuck_renders(jobs)
        await _aio.sleep(0.01)
        return resumed_now, sum(1 for j in jobs.values() if j.get("resume_count"))
    resumed_now, resumed_later = _aio.run(run())
    assert resumed_now == main._RESUME_RATE_MAX == 2        # per-call counters allowed 5
    assert resumed_later == 4
    assert all(j["status"] == "processing" for j in jobs.values())   # deferred, never failed
    for jid in jobs:
        main._pipeline_tasks.pop(jid, None)


def test_polling_gets_share_the_resume_budget(monkeypatch):
    _stub_resume_targets(monkeypatch)
    jobs = _orphans(4, "lv26g-")
    monkeypatch.setattr(main, "_clip_jobs", jobs)            # isolate from other tests' jobs
    for jid in list(jobs)[:3]:                               # three clients polling at once
        assert client.get(f"/v1/clips/{jid}").status_code == 200
    assert sum(1 for j in jobs.values() if j.get("resume_count")) == 2
    for jid in jobs:
        main._pipeline_tasks.pop(jid, None)


# ---------------------------------------------------------------------------
# LV-27 — probe the source duration once at pipeline start; transcription budget scales
# ---------------------------------------------------------------------------

def test_transcribe_budget_scales_with_duration(monkeypatch):
    b = main._transcribe_budget_s
    assert b(None) == 300 and b(30) == 300           # unknown / short → the old flat budget
    assert b(300) == 450                             # 5-min take: 90 + 1.2*300
    assert b(600) == 810 and b(1800) == 2250
    assert b(3000) == 2400                           # capped
    monkeypatch.setattr(main, "TRANSCRIBE_MAX_S", 3000)
    assert b(600) == 3000                            # an operator floor is never reduced


def test_source_duration_probe_is_http_only_and_fail_soft(monkeypatch):
    async def must_not_run(src, timeout_s=30.0):
        raise AssertionError("ffprobe must not run for a non-http source")
    monkeypatch.setattr(main, "_ffprobe_duration_s", must_not_run)
    assert _aio.run(main._probe_source_duration_s("mock://take.mov")) is None
    assert _aio.run(main._probe_source_duration_s("")) is None

    seen = []
    results = iter([612.5, None, float("nan"), 0.0])

    async def fake_ffprobe(src, timeout_s=30.0):
        seen.append(timeout_s)
        return next(results)
    monkeypatch.setattr(main, "_ffprobe_duration_s", fake_ffprobe)
    url = "https://proj.supabase.co/storage/v1/object/public/b/take.mov"
    assert _aio.run(main._probe_source_duration_s(url)) == 612.5
    assert [_aio.run(main._probe_source_duration_s(url)) for _ in range(3)] == [None] * 3
    assert seen[0] == main.SOURCE_DURATION_PROBE_S == 10.0      # short, bounded


def _live_transcribe_job(monkeypatch, jid, **over):
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "test-key")
    job = {"job_id": jid, "status": "transcribing", "created_at": _time.time(),
           "clips": [{"clip_id": "c1", "format": "myth-buster", "status": "queued"}],
           "script": {"hook": "h", "formatId": "myth-buster"}, "style": "talking_head",
           "brand": {}, "media_context": "", "edl": None, "error": None, "edit_prefs": {},
           "words": [], "edl_history": [], "tweaks": [], "custom_instructions": "",
           "source_url": "https://proj.supabase.co/storage/v1/object/public/b/take.mov"}
    job.update(over)
    main._clip_jobs[jid] = job
    return job


def test_pipeline_probes_duration_once_and_scales_every_budget(monkeypatch):
    probes, polls, loudness = [], [], []

    async def ok_validate(url):
        pass

    async def fake_probe(url):
        probes.append(url)
        return 600.0                                      # a 10-minute take

    async def submit(url):
        return "tid-1"

    async def poll(tid, max_wait_s=None):
        polls.append(max_wait_s)
        raise main.PipelineError("transcribe_timeout", "stop here", "transcribe")

    async def fake_loudness(url, **kw):
        loudness.append(kw.get("duration_s"))
        return None
    monkeypatch.setattr(main, "_validate_source_url", ok_validate)
    monkeypatch.setattr(main, "_probe_source_duration_s", fake_probe)
    monkeypatch.setattr(main, "_submit_transcription", submit)
    monkeypatch.setattr(main, "_poll_transcription", poll)
    monkeypatch.setattr(main.audio_mod, "probe_loudness", fake_loudness)
    job = _live_transcribe_job(monkeypatch, "lv27-pipe")
    _aio.run(main._run_pipeline("lv27-pipe"))
    assert job["duration_ms"] == 600_000                 # written (was read but never set)
    assert probes == [job["source_url"]]                 # probed exactly once
    assert polls == [810]                                # 90 + 1.2*600, not the flat 300
    assert loudness == [600.0]                           # known BEFORE the parallel probe
    assert job["error"] == "transcribe_timeout"          # (the stub's stop signal)
    main._clip_jobs.pop("lv27-pipe", None)


def test_known_duration_is_never_reprobed_and_bad_sources_fail_first(monkeypatch):
    probes = []

    async def fake_probe(url):
        probes.append(url)
        return 99.0
    monkeypatch.setattr(main, "_probe_source_duration_s", fake_probe)
    job = {"job_id": "lv27-known", "duration_ms": 245_000, "source_url": "https://x/t.mov"}
    assert _aio.run(main._ensure_source_duration(job)) == 245.0 and probes == []

    async def dead_source(url):
        raise main.PipelineError("source_unreachable", "404", "transcribe")
    monkeypatch.setattr(main, "_validate_source_url", dead_source)
    job = _live_transcribe_job(monkeypatch, "lv27-dead")
    _aio.run(main._run_pipeline("lv27-dead"))
    assert job["error"] == "source_unreachable" and probes == []   # no probe on a dead URL
    main._clip_jobs.pop("lv27-dead", None)


def test_demo_src_tweak_models_a_finished_render(tmp_path, monkeypatch):
    """Keyless QA seam: a committed direct edit on a demo-src job returns needs_render with a
    NEW render URL (version history/share drivable in the sim); defer_render does not."""
    monkeypatch.setenv("DEMO_MEDIA_DIR", _media_dir(tmp_path, 60))
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    jid = "demo-src-60-tw"
    main._clip_jobs.pop(jid, None)
    client.get(f"/v1/clips/{jid}")
    job = main._clip_jobs[jid]
    cid = job["clips"][0]["clip_id"]
    seg = job["edl"]["segments"][0]
    r = client.post(f"/v1/clips/{jid}/tweak", json={
        "clip_id": cid, "ops": [{"type": "split_segment", "index": 0, "at_frame": seg["src_in"] + 120}]})
    b = r.json()
    assert r.status_code == 200 and b["needs_render"] is True
    assert job["clips"][0]["render_url"].endswith("/60.mov?v=1")
    r2 = client.post(f"/v1/clips/{jid}/tweak", params={"defer_render": 1}, json={
        "clip_id": cid, "ops": [{"type": "split_segment", "index": 0, "at_frame": seg["src_in"] + 60}]})
    assert r2.json()["needs_render"] is False


def test_undo_batch_is_all_or_nothing_when_history_is_short():
    """ED-4: the app restores version N by sending N+1 undos. If the server kept fewer (only 5
    survive a restart), popping what exists silently rewinds the server to its OLDEST version
    while the app keeps showing the current cut. The batch must be rejected untouched."""
    jid = "audit-undo-batch"
    edl = {"style": "talking_head", "format_id": "myth-buster",
           "segments": [{"src_in": 0, "src_out": 900}], "drops": [], "captions": [], "overlays": [],
           "broll": [], "layout": {}, "audio": {}}
    hist = [dict(edl, segments=[{"src_in": 0, "src_out": 900 - 30 * i}]) for i in range(1, 4)]
    main._clip_jobs[jid] = {"status": "ready", "style": "talking_head", "script": {},
                            "source_url": "https://example.com/a.mov",
                            "clips": [{"clip_id": "c1", "format": "myth-buster", "status": "ready"}],
                            "edl": dict(edl), "edl_history": list(hist), "tweaks": [], "words": [],
                            "created_at": 0}
    r = client.post(f"/v1/clips/{jid}/tweak", json={"clip_id": "c1", "ops": [{"type": "undo"}] * 5})
    b = r.json()
    assert r.status_code == 200 and b["applied"] == [] and b["changed"] is False
    assert len(main._clip_jobs[jid]["edl_history"]) == 3            # nothing popped
    assert main._clip_jobs[jid]["edl"]["segments"][0]["src_out"] == 900
    r2 = client.post(f"/v1/clips/{jid}/tweak", json={"clip_id": "c1", "ops": [{"type": "undo"}] * 2})
    assert len(r2.json()["applied"]) == 2 and len(main._clip_jobs[jid]["edl_history"]) == 1
    main._clip_jobs.pop(jid, None)
