"""The production worker (studio.server.app): queueing, idempotent submits, stages, uploads, chat, restart recovery and
auth. The engine and the source download are faked: no media, no keys."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from studio.server import app as srv

TOKEN = "t0k"
H = {"Authorization": f"Bearer {TOKEN}"}


class FakeEngine(srv.Engine):
    def __init__(self, tmp: Path, *, fail: Exception | None = None):
        self.tmp, self.fail, self.edits, self.chats = tmp, fail, [], []

    def edit(self, src, *, platform, brief, out, log_stage):
        self.edits.append((src, platform, brief))
        for line in ("studio: perception (Take Index)", "studio: planner (x)", "studio: render (standard)"):
            log_stage(line)
        if self.fail:
            raise self.fail
        out.mkdir(parents=True, exist_ok=True)
        (out / "final.mp4").write_bytes(b"mp4")
        (out / "cover.jpg").write_bytes(b"jpg")
        return {"final": out / "final.mp4", "cover": out / "cover.jpg", "job_dir": str(self.tmp / "job"),
                "cost": "$0.02", "invariants_passed": True, "compute_seconds": 1.0}

    def chat(self, job_dir, instruction, *, platform, out):
        self.chats.append(instruction)
        if "music" in instruction:
            raise srv.EngineError("chat_no_change", "added music is not in the standard tier")
        out.mkdir(parents=True, exist_ok=True)
        (out / "final.mp4").write_bytes(b"mp4-2")
        return {"final": out / "final.mp4", "summary": "captions bigger"}


@pytest.fixture
def make(tmp_path, monkeypatch):
    def _make(**kw: Any):
        cfg = srv.Config.from_env({"STUDIO_WORKER_DATA": str(tmp_path / "data"), "STUDIO_WORKER_TOKEN": TOKEN,
                                   "STUDIO_WORKER_PUBLIC_URL": "http://worker"})
        eng = FakeEngine(tmp_path, **kw)
        worker = srv.Worker(cfg, engine=eng)

        def fake_download(url, root):
            if "missing" in url:
                raise srv.EngineError("source_unreachable", "HTTP 404 fetching the source")
            root.mkdir(parents=True, exist_ok=True)
            p = root / "source.mov"
            p.write_bytes(b"src")
            return p

        monkeypatch.setattr(worker, "_download", fake_download)
        return worker, eng, cfg

    return _make


def wait(client: TestClient, job_id: str, *, until=lambda r: r["status"] in srv.TERMINAL, timeout: float = 5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/jobs/{job_id}", headers=H).json()
        if until(r):
            return r
        time.sleep(0.02)
    raise AssertionError(f"timed out: {r}")


BODY = {"job_id": "job-1", "clip_id": "clip-1", "source_url": "https://x/uploads/a/take.mov", "brief": "be punchy"}


def test_an_edit_runs_to_ready_with_public_urls_and_is_idempotent(make) -> None:
    worker, eng, cfg = make()
    with TestClient(srv.create_app(worker)) as c:
        assert c.get("/healthz").json()["ok"] is True
        r = c.post("/jobs", json=BODY, headers=H)
        assert r.status_code == 200 and r.json()["status"] in ("queued", "downloading", "editing", "rendering",
                                                                 "uploading", "ready")
        done = wait(c, "job-1")
        assert done["status"] == "ready" and done["error"] is None
        assert done["render_url"] == "http://worker/files/studio/job-1/clip-1-r0.mp4"
        assert done["thumbnail_url"] == "http://worker/files/posters/job-1-clip-1.jpg"
        assert c.get("/files/studio/job-1/clip-1-r0.mp4").content == b"mp4"
        assert eng.edits[0][1:] == ("tiktok", "be punchy")
        # the same job submitted again (an API resume) returns the finished job without a second edit
        again = c.post("/jobs", json=BODY, headers=H).json()
        assert again["status"] == "ready" and len(eng.edits) == 1


def test_auth_ids_and_urls_are_checked(make) -> None:
    worker, _eng, _cfg = make()
    with TestClient(srv.create_app(worker)) as c:
        assert c.post("/jobs", json=BODY).status_code == 401
        assert c.post("/jobs", json=BODY, headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.post("/jobs", json={**BODY, "job_id": "../etc"}, headers=H).status_code == 400
        assert c.post("/jobs", json={**BODY, "source_url": "file:///etc/passwd"}, headers=H).status_code == 400
        assert c.get("/jobs/nope", headers=H).status_code == 404
        assert c.get("/files/../jobs/job-1.json").status_code in (401, 404)  # never served
        assert c.get("/files/%2e%2e/jobs/job-1.json").status_code == 404


def test_a_failed_edit_reports_a_code_and_a_retry_queues_it_again(make) -> None:
    worker, eng, _cfg = make(fail=RuntimeError("source is 700 s, over the 10 min limit"))
    with TestClient(srv.create_app(worker)) as c:
        c.post("/jobs", json=BODY, headers=H)
        r = wait(c, "job-1")
        assert r["status"] == "failed" and r["error"] == "studio_rejected"
        eng.fail = None
        c.post("/jobs", json=BODY, headers=H)
        assert wait(c, "job-1")["status"] == "ready" and len(eng.edits) == 2
        c.post("/jobs", json={**BODY, "job_id": "job-2", "source_url": "https://x/missing.mov"}, headers=H)
        assert wait(c, "job-2")["error"] == "source_unreachable"


def test_chat_re_edits_a_ready_job_and_reports_no_change(make) -> None:
    worker, eng, _cfg = make()
    with TestClient(srv.create_app(worker)) as c:
        assert c.post("/jobs/job-1/chat", json={"instruction": "x"}, headers=H).status_code == 404
        c.post("/jobs", json=BODY, headers=H)
        wait(c, "job-1")
        assert c.post("/jobs/job-1/chat", json={"instruction": "  "}, headers=H).status_code == 400
        r = c.post("/jobs/job-1/chat", json={"instruction": "bigger captions", "request_id": "q1"}, headers=H)
        assert r.status_code == 200 and r.json()["chat"]["request_id"] == "q1"
        done = wait(c, "job-1", until=lambda r: (r["chat"] or {}).get("status") in ("ready", "failed"))
        assert done["chat"]["status"] == "ready" and done["revision"] == 1
        assert done["render_url"].endswith("clip-1-r1.mp4") and done["chat"]["summary"] == "captions bigger"
        c.post("/jobs/job-1/chat", json={"instruction": "add music", "request_id": "q2"}, headers=H)
        bad = wait(c, "job-1", until=lambda r: r["chat"]["request_id"] == "q2" and r["chat"]["status"] == "failed")
        assert bad["chat"]["error"] == "chat_no_change" and bad["revision"] == 1  # the last good cut stands
        assert eng.chats == ["bigger captions", "add music"]


def test_a_restart_fails_running_work_and_requeues_queued_work(make) -> None:
    worker, _eng, cfg = make()
    worker.store.put({"job_id": "run", "clip_id": "c", "status": "rendering", "created_at": 1})
    worker.store.put({"job_id": "wait", "clip_id": "c", "source_url": "https://x/a.mov", "platform": "tiktok",
                      "status": "queued", "created_at": 2})
    fresh = srv.Worker(cfg, engine=FakeEngine(cfg.data))
    assert fresh.store.get("run")["error"] == "worker_restarted"
    assert fresh.q.qsize() == 1


def test_cancel_only_touches_queued_jobs(make) -> None:
    worker, _eng, _cfg = make()  # not started: submitted jobs stay queued
    worker.submit(BODY)
    assert worker.cancel("job-1")["status"] == "cancelled"
    worker._run_edit("job-1")  # a cancelled job never runs
    assert worker.store.get("job-1")["status"] == "cancelled"
