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
