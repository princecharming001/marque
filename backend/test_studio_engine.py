"""Studio engine routing (app/studio_engine.py + main._run_studio / _studio_tweak): which jobs route, a delivered
Studio edit, every fallback to the legacy pipeline (the per-job kill switch), resume, and chat tweaks. The worker is
faked at the HTTP-call layer; no network."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import main
from app import studio_engine as se
from main import app

client = TestClient(app)


@pytest.fixture
def studio_on(monkeypatch):
    monkeypatch.setattr(se, "MODE", "on")
    monkeypatch.setattr(se, "WORKER_URL", "http://studio-worker")
    monkeypatch.setattr(se, "WORKER_TOKEN", "tok")
    monkeypatch.setattr(se, "POLL_S", 0)
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "test-key")

    async def ok_source(job):
        job["source_duration_s"] = 30.0
    monkeypatch.setattr(main, "_prepare_source", ok_source)
    monkeypatch.setattr(main, "_source_duration_s", lambda job: job.get("source_duration_s"))
    return monkeypatch


class FakeWorker:
    """Answers _call like the worker: each GET advances one step through ``script``."""

    def __init__(self, script, *, chat_script=None):
        self.script, self.chat_script = list(script), list(chat_script or [])
        self.calls = []
        self.rec = {}

    async def __call__(self, method, path, *, json=None, timeout=20):
        self.calls.append((method, path, json))
        if method == "POST" and path == "/jobs":
            self.rec = {"job_id": json["job_id"], "status": "queued", "revision": 0, "chat": None}
            return dict(self.rec)
        if method == "POST" and path.endswith("/chat"):
            self.rec["chat"] = {"request_id": json["request_id"], "status": "queued"}
            return dict(self.rec)
        if method == "DELETE":
            self.rec["status"] = "cancelled"
            return dict(self.rec)
        if self.rec.get("chat") and self.chat_script:
            step = self.chat_script.pop(0)
            if isinstance(step, Exception):
                raise step
            self.rec.update(step.get("rec", {}))
            self.rec["chat"] = {**self.rec["chat"], **step.get("chat", {})}
            return dict(self.rec)
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            self.rec.update(step)
        return dict(self.rec)


READY = {"status": "ready", "render_url": "https://s3/studio/j/c-r0.mp4", "thumbnail_url": "https://sb/p.jpg"}


def _submit(extra=None):
    body = {"source_url": "https://sb/uploads/a/take.mov", "analyze_first": True, "auto_confirm": True,
            "script": {"hook": "h", "body": "b", "cta": "c"}, "creator_id": "creator-1"}
    body.update(extra or {})
    r = client.post("/v1/clips", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _no_spawn(monkeypatch):
    """Create the job without letting the create route start the pipeline (the test runs it)."""
    monkeypatch.setattr(main, "_spawn_pipeline", lambda jid, coro: coro.close())


# ---------------------------------------------------------------------------------------------- routing
def test_choose_engine_rules(monkeypatch):
    monkeypatch.setattr(se, "WORKER_URL", "http://w")
    monkeypatch.setattr(se, "WORKER_TOKEN", "t")
    base = dict(creator_id="c1", auto_confirm=True, style="talking_head", react_source_url=None, config={})
    assert se.choose_engine(**base, mode="off") == "legacy"
    assert se.choose_engine(**base, mode="on") == "studio"
    assert se.choose_engine(**{**base, "auto_confirm": False}, mode="on") == "legacy"
    assert se.choose_engine(**{**base, "style": "duet_split"}, mode="on") == "legacy"
    assert se.choose_engine(**{**base, "react_source_url": "https://x"}, mode="on") == "legacy"
    assert se.choose_engine(**{**base, "config": {"edl_only": True}}, mode="on") == "legacy"
    monkeypatch.setattr(se, "ALLOWLIST", {"c1"})
    assert se.choose_engine(**base, mode="allowlist") == "studio"
    assert se.choose_engine(**{**base, "creator_id": "c2"}, mode="allowlist") == "legacy"
    monkeypatch.setattr(se, "WORKER_TOKEN", "")
    assert se.choose_engine(**base, mode="on") == "legacy"  # no worker configured: never routes


def test_default_is_off_and_jobs_stay_legacy(monkeypatch):
    _no_spawn(monkeypatch)
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "test-key")
    out = _submit()
    assert main._clip_jobs[out["job_id"]]["engine"] == "legacy"


# ---------------------------------------------------------------------------------------------- delivery
def test_a_studio_job_is_delivered_on_the_clip_the_app_tracks(studio_on):
    _no_spawn(studio_on)
    pushes = []

    async def fake_push(creator, clip_id, n):
        pushes.append((creator, clip_id, n))
    studio_on.setattr(main.push_mod, "send_clips_ready", fake_push)
    fw = FakeWorker([{"status": "downloading"}, {"status": "editing", "stage": "planner"},
                     {"status": "rendering"}, READY])
    studio_on.setattr(se, "_call", fw)
    out = _submit({"custom_instructions": "keep it punchy"})
    jid = out["job_id"]
    job = main._clip_jobs[jid]
    assert job["engine"] == "studio"

    seen = []
    real_mark = main._mark_stage
    studio_on.setattr(main, "_mark_stage", lambda j, st: (seen.append(st), real_mark(j, st)))

    async def run():
        await main._run_auto_pipeline(jid)
        await asyncio.sleep(0)  # let the push task run
    asyncio.run(run())
    clip = job["clips"][0]
    assert job["status"] == "ready" and clip["status"] == "ready"
    assert clip["clip_id"] == out["clips"][0]["clip_id"]
    assert clip["render_url"] == READY["render_url"] and clip["thumbnail_url"] == READY["thumbnail_url"]
    assert seen == ["processing", "editing", "rendering"]
    post = fw.calls[0]
    assert post[0:2] == ("POST", "/jobs") and post[2]["brief"] == "keep it punchy"
    assert post[2]["clip_id"] == clip["clip_id"] and post[2]["source_url"].endswith("take.mov")
    assert pushes == [("creator-1", clip["clip_id"], 1)]
    # the poll route serves it like any ready job
    r = client.get(f"/v1/clips/{jid}").json()
    assert r["status"] == "ready" and r["clips"][0]["render_url"] == READY["render_url"]


@pytest.mark.parametrize("script,reason", [
    ([{"status": "failed", "error": "studio_rejected", "error_detail": "too long"}], "studio_rejected"),
    ([se.StudioUnavailable("worker_unreachable")] * 6, "worker_unreachable"),
])
def test_a_studio_failure_falls_back_to_the_legacy_pipeline(studio_on, script, reason):
    _no_spawn(studio_on)
    studio_on.setattr(se, "_call", FakeWorker(script))
    legacy = []

    async def fake_analyze(job_id, **k):
        legacy.append("analyze")
        main._clip_jobs[job_id]["words"] = []

    async def fake_edit(job_id, words):
        legacy.append("edit")
        job = main._clip_jobs[job_id]
        job["status"] = "ready"
        job["clips"][0].update(status="ready", render_url="https://lambda/out.mp4")
    studio_on.setattr(main, "_analyze_to_brief", fake_analyze)
    studio_on.setattr(main, "_run_edit", fake_edit)
    jid = _submit()["job_id"]
    asyncio.run(main._run_auto_pipeline(jid))
    job = main._clip_jobs[jid]
    assert legacy == ["analyze", "edit"]
    assert job["engine"] == "legacy" and job["engine_fallback"]["reason"] == reason
    assert job["clips"][0]["render_url"] == "https://lambda/out.mp4"


def test_a_long_queue_and_a_long_source_fall_back(studio_on):
    _no_spawn(studio_on)
    fw = FakeWorker([{"status": "queued"}] * 3)
    studio_on.setattr(se, "_call", fw)
    studio_on.setattr(se, "QUEUE_MAX_S", -1)
    studio_on.setattr(main, "_analyze_to_brief", lambda *a, **k: asyncio.sleep(0))
    studio_on.setattr(main, "_run_edit", lambda *a, **k: asyncio.sleep(0))
    jid = _submit()["job_id"]
    asyncio.run(main._run_auto_pipeline(jid))
    assert main._clip_jobs[jid]["engine_fallback"]["reason"] == "worker_busy"
    assert ("DELETE", f"/jobs/{jid}", None) in fw.calls  # the worker drops the queued job

    async def long_source(job):
        job["source_duration_s"] = 900.0
    studio_on.setattr(main, "_prepare_source", long_source)
    jid2 = _submit()["job_id"]
    asyncio.run(main._run_auto_pipeline(jid2))
    assert main._clip_jobs[jid2]["engine_fallback"]["reason"] == "source_too_long"


def test_a_dead_source_fails_like_legacy(studio_on):
    _no_spawn(studio_on)

    async def dead(job):
        raise main.PipelineError("source_unreachable", "404", "transcribe")
    studio_on.setattr(main, "_prepare_source", dead)
    jid = _submit()["job_id"]
    asyncio.run(main._run_auto_pipeline(jid))
    job = main._clip_jobs[jid]
    assert job["status"] == "failed" and job["error"] == "source_unreachable" and job["engine"] == "studio"


def test_an_orphaned_studio_job_resumes_from_rendering(studio_on):
    _no_spawn(studio_on)
    jid = _submit()["job_id"]
    job = main._clip_jobs[jid]
    job["status"] = "rendering"
    spawned = []
    studio_on.setattr(main, "_spawn_pipeline", lambda j, coro: (spawned.append(j), coro.close()))
    studio_on.setattr(main, "_pipeline_alive", lambda j: False)
    assert main._try_resume_pipeline(job) is True and spawned == [jid]


# ---------------------------------------------------------------------------------------------- chat tweaks
def _ready_studio_job(studio_on):
    _no_spawn(studio_on)
    studio_on.setattr(se, "_call", FakeWorker([READY]))
    jid = _submit()["job_id"]
    asyncio.run(main._run_auto_pipeline(jid))
    return jid, main._clip_jobs[jid]


def test_a_chat_tweak_re_edits_through_the_worker(studio_on):
    jid, job = _ready_studio_job(studio_on)
    clip = job["clips"][0]
    fw = FakeWorker([], chat_script=[{"chat": {"status": "running"}},
                                     {"chat": {"status": "ready"}, "rec": {"render_url": "https://s3/c-r1.mp4",
                                                                           "revision": 1}}])
    fw.rec = {"job_id": jid, "status": "ready", "render_url": READY["render_url"], "chat": None}
    studio_on.setattr(se, "_call", fw)
    finished = []
    studio_on.setattr(main, "_spawn", lambda coro: finished.append(coro))

    r = client.post(f"/v1/clips/{jid}/tweak", json={"clip_id": clip["clip_id"].upper(),
                                                   "instruction": "make the captions bigger"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["needs_render"] is True and body["clip_status"] == "rendering"
    assert clip["status"] == "rendering"
    assert client.post(f"/v1/clips/{jid}/tweak",  # a second tweak while it renders is refused
                       json={"clip_id": clip["clip_id"], "instruction": "x"}).status_code == 409

    async def drain():
        for coro in list(finished):
            await coro
    asyncio.run(drain())
    assert clip["status"] == "ready" and clip["render_url"] == "https://s3/c-r1.mp4"
    assert not clip.get("last_render_failed")


def test_a_failed_chat_keeps_the_last_cut_and_typed_ops_are_refused(studio_on):
    jid, job = _ready_studio_job(studio_on)
    clip = job["clips"][0]
    fw = FakeWorker([], chat_script=[{"chat": {"status": "failed", "error": "chat_no_change"}}])
    fw.rec = {"job_id": jid, "status": "ready", "render_url": READY["render_url"], "chat": None}
    studio_on.setattr(se, "_call", fw)
    finished = []
    studio_on.setattr(main, "_spawn", lambda coro: finished.append(coro))
    r = client.post(f"/v1/clips/{jid}/tweak", json={"clip_id": clip["clip_id"], "instruction": "add music"})
    assert r.status_code == 200

    async def drain():
        for coro in list(finished):
            await coro
    asyncio.run(drain())
    assert clip["status"] == "ready" and clip["render_url"] == READY["render_url"]
    assert clip["last_render_failed"] is True
    r = client.post(f"/v1/clips/{jid}/tweak", json={"clip_id": clip["clip_id"], "instruction": "",
                                                   "ops": [{"op": "trim"}]})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "not_supported"
