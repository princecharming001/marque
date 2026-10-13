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
        # an instruction the standard tier can't do is refused up front with a creator-facing reason (QA 2026-10-13)
        r = c.post("/jobs/job-1/chat", json={"instruction": "add some background music", "request_id": "q2"},
                   headers=H)
        assert r.status_code == 422 and r.json()["detail"]["error"] == "chat_refused"
        assert "background music" in r.json()["detail"]["message"]
        assert eng.chats == ["bigger captions"] and worker.store.get("job-1")["revision"] == 1
        # an engine-side no-change still fails the chat and keeps the last good cut
        c.post("/jobs/job-1/chat", json={"instruction": "music please, then shorter", "request_id": "q3"}, headers=H)
        bad = wait(c, "job-1", until=lambda r: r["chat"]["request_id"] == "q3" and r["chat"]["status"] == "failed")
        assert bad["chat"]["error"] == "chat_no_change" and bad["revision"] == 1


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


# ============================================================================================ QA pass 2026-10-13
class BareEngine(FakeEngine):
    """Emits progress the way studio.pipeline really does: bare lines (only the CLI prefixes "studio: ")."""

    def __init__(self, tmp: Path, lines: list[str] | None = None, **kw: Any):
        super().__init__(tmp, **kw)
        self.lines = lines or [f"job {tmp}/data/engine/qa-job (standard tier)", "ingest", "perception (Take Index)",
                               "code plan (planner decision)", "render (standard profile) of document v1", "deliver"]
        self.seen: list[str] = []
        self.hook = None

    def edit(self, src, *, platform, brief, out, log_stage):
        for line in self.lines:
            if self.hook:
                self.hook(line)
            log_stage(line)
        if self.fail:
            raise self.fail
        out.mkdir(parents=True, exist_ok=True)
        (out / "final.mp4").write_bytes(b"mp4")
        return {"final": out / "final.mp4", "cover": None, "job_dir": str(self.tmp / "job"), "cost": "$0.02",
                "invariants_passed": True, "compute_seconds": 1.0}


def _worker(tmp_path, monkeypatch, engine=None, **env):
    cfg = srv.Config.from_env({"STUDIO_WORKER_DATA": str(tmp_path / "data"), "STUDIO_WORKER_TOKEN": TOKEN,
                               "STUDIO_WORKER_PUBLIC_URL": "http://worker", **env})
    worker = srv.Worker(cfg, engine=engine or BareEngine(tmp_path))

    def fake_download(url, root):
        root.mkdir(parents=True, exist_ok=True)
        (root / "source.mov").write_bytes(b"src")
        return root / "source.mov"
    monkeypatch.setattr(worker, "_download", fake_download)
    return worker


def test_bare_engine_progress_lines_advance_the_status(tmp_path, monkeypatch) -> None:
    """The worker matched only "studio: "-prefixed lines, so a real job sat on editing/ingest for the whole edit and
    the API never gave the clip its render budget."""
    eng = BareEngine(tmp_path)
    worker = _worker(tmp_path, monkeypatch, eng)
    statuses = []
    eng.hook = lambda line: statuses.append(worker.store.get("job-1")["status"])
    worker.submit(BODY)
    worker._run_edit("job-1")
    rec = worker.store.get("job-1")
    assert rec["status"] == "ready"
    assert statuses[-1] == "rendering"  # by "deliver" the job reads rendering (it used to stay on editing)
    assert rec["planner_used"] is True and rec["engine_job_dir"].endswith("qa-job")


def test_a_pure_code_edit_is_flagged_as_shipped_without_the_planner(tmp_path, monkeypatch) -> None:
    eng = BareEngine(tmp_path, lines=["ingest", "code plan (pure-code: no planner decision)", "render (standard)"])
    worker = _worker(tmp_path, monkeypatch, eng)
    worker.submit(BODY)
    worker._run_edit("job-1")
    assert worker.store.get("job-1")["planner_used"] is False


def test_cancel_stops_a_running_edit_and_cleans_its_folders(tmp_path, monkeypatch) -> None:
    eng = BareEngine(tmp_path)
    worker = _worker(tmp_path, monkeypatch, eng)
    engine_dir = tmp_path / "data" / "engine" / "qa-job"
    engine_dir.mkdir(parents=True)
    eng.hook = lambda line: worker.cancel("job-1") if line.startswith("perception") else None
    worker.submit(BODY)
    worker._run_edit("job-1")
    rec = worker.store.get("job-1")
    assert rec["status"] == "failed" and rec["error"] == "cancelled" and rec["render_url"] is None
    assert not engine_dir.exists() and not (tmp_path / "data" / "work" / "job-1").exists()


def test_a_failed_edit_frees_its_engine_folder(tmp_path, monkeypatch) -> None:
    eng = BareEngine(tmp_path, fail=RuntimeError("PipelineError: the take has no words: there is no speech"))
    worker = _worker(tmp_path, monkeypatch, eng)
    engine_dir = tmp_path / "data" / "engine" / "qa-job"
    engine_dir.mkdir(parents=True)
    worker.submit(BODY)
    worker._run_edit("job-1")
    rec = worker.store.get("job-1")
    assert rec["error"] == "studio_no_speech" and not engine_dir.exists()


def test_racing_duplicate_submits_queue_the_job_once(tmp_path, monkeypatch) -> None:
    import threading

    worker = _worker(tmp_path, monkeypatch)
    barrier = threading.Barrier(8)

    def go():
        barrier.wait()
        worker.submit(BODY)
    ts = [threading.Thread(target=go) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert worker.q.qsize() == 1


def test_chats_jump_ahead_of_queued_edits(tmp_path, monkeypatch) -> None:
    worker = _worker(tmp_path, monkeypatch)
    worker.submit(BODY)
    worker._run_edit("job-1")                       # job-1 is ready
    worker.submit({**BODY, "job_id": "job-2"})      # a full edit waits in the queue
    worker.chat("job-1", {"instruction": "bigger captions", "request_id": "q"})
    first = worker.q.get()
    assert first[2:] == ("chat", "job-1")


def test_downloads_only_from_the_storage_host_and_never_follow_redirects(tmp_path) -> None:
    cfg = srv.Config.from_env({"STUDIO_WORKER_DATA": str(tmp_path / "d"), "STUDIO_WORKER_TOKEN": TOKEN,
                               "SUPABASE_URL": "https://abc.supabase.co"})
    assert cfg.source_hosts == ("abc.supabase.co",)
    worker = srv.Worker(cfg, engine=FakeEngine(tmp_path))
    with pytest.raises(srv.EngineError) as e:
        worker._download("http://169.254.169.254/latest/meta-data/x.mov", tmp_path / "w")
    assert e.value.code == "source_unreachable" and "not an allowed storage host" in e.value.detail


def test_a_half_configured_uploader_refuses_to_start() -> None:
    assert srv.Config.from_env({"REMOTION_BUCKET": "b", "REMOTION_AWS_ACCESS_KEY_ID": "k"}).problems()
    assert srv.Config.from_env({"SUPABASE_URL": "https://x"}).problems()
    ok = srv.Config.from_env({"REMOTION_BUCKET": "b", "REMOTION_AWS_ACCESS_KEY_ID": "k",
                              "REMOTION_AWS_SECRET_ACCESS_KEY": "s", "SUPABASE_URL": "https://x",
                              "SUPABASE_SERVICE_KEY": "k"})
    assert ok.problems() == []


def test_a_corrupt_job_record_reads_as_missing_not_a_500(tmp_path, monkeypatch) -> None:
    worker = _worker(tmp_path, monkeypatch)
    (tmp_path / "data" / "jobs" / "job-9.json").write_text("{not json")
    with TestClient(srv.create_app(worker)) as c:
        assert c.get("/jobs/job-9", headers=H).status_code == 404
