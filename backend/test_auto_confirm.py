"""UX-B1a: one-tap submit (auto_confirm) — keyless + monkeypatched-live paths."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)


def _submit(payload_extra: dict | None = None) -> dict:
    body = {"source_url": "mock://take.mov", "analyze_first": True, "auto_confirm": True,
            "script": {"hook": "h", "body": "b", "cta": "c"}, "formats": ["myth-buster"]}
    body.update(payload_extra or {})
    r = client.post("/v1/clips", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --- keyless: immediate mock_ready with a real clips array ---------------------

def test_keyless_auto_submit_returns_ready_clips():
    out = _submit()
    assert out["mode"] == "mock" and out["status"] == "mock_ready"
    assert out["clips"] and out["clips"][0]["status"] == "ready"
    job = main._clip_jobs[out["job_id"]]
    assert job["edl"] is not None                       # a real mock edit exists
    assert job.get("edit_brief") is not None            # brief generated, not skipped
    assert job["creator_id"] == "default"


def test_auto_submit_renders_once_no_fanout():
    out = _submit({"formats": ["myth-buster", "listicle", "pov-story"]})
    assert len(out["clips"]) == 1                       # confirm's no-fan-out rule


def test_explicit_toggles_respected():
    out = _submit({"toggles": {"broll": False, "punch_ins": False, "music": True}})
    job = main._clip_jobs[out["job_id"]]
    assert job["edit_prefs"]["broll"] is False
    assert job["edit_prefs"]["punch_ins"] is False
    assert job["edit_prefs"]["music"] is True


def test_toggles_default_from_edit_format_when_omitted():
    out = _submit({"edit_format": "recap_music"})       # EDIT_FORMATS: music True
    job = main._clip_jobs[out["job_id"]]
    assert job["edit_prefs"]["music"] is True
    assert job["toggles"] == main.prompts.EDIT_FORMATS["recap_music"]["toggles"]


def test_clip_ids_stable_from_create_response():
    out = _submit()
    job = main._clip_jobs[out["job_id"]]
    assert [c["clip_id"] for c in job["clips"]] == [c["clip_id"] for c in out["clips"]]


def test_426_guard_untouched():
    r = client.post("/v1/clips", json={"source_url": "mock://x", "auto_confirm": True})
    assert r.status_code == 426                          # analyze_first still required


def test_analyze_first_path_untouched():
    r = client.post("/v1/clips", json={"source_url": "mock://x", "analyze_first": True,
                                       "script": {"hook": "h", "body": "b", "cta": "c"}})
    out = r.json()
    assert out["status"] == "brief_ready"                # still stops at the brief
    assert "clips" not in out or out.get("status") == "brief_ready"


# --- live pipeline (monkeypatched vendors) --------------------------------------

def _seed_live_job(monkeypatch) -> str:
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "test-key")

    async def fake_transcribe(job_id):
        words = [{"word": w, "start_ms": i * 300, "end_ms": i * 300 + 250}
                 for i, w in enumerate("one two three four five six seven eight".split())]
        main._clip_jobs[job_id]["words"] = words
        return words
    async def fake_loudness(url, **k):
        return None
    async def no_render(job_id):
        job = main._clip_jobs[job_id]
        for c in job["clips"]:
            c["status"] = "ready"
            c["render_url"] = "https://cdn/out.mp4"
    monkeypatch.setattr(main, "_transcribe_job", fake_transcribe)
    monkeypatch.setattr(main.audio_mod, "probe_loudness", fake_loudness)
    monkeypatch.setattr(main, "_render_all_clips", no_render)

    out = _submit({"creator_id": "creator-1"})
    return out["job_id"], out


def test_live_auto_pipeline_reaches_ready(monkeypatch):
    job_id, out = _seed_live_job(monkeypatch)
    assert out["status"] == "processing" and out["mode"] == "live"
    assert out["clips"] and out["clips"][0]["status"] == "queued"
    asyncio.run(main._run_auto_pipeline(job_id))
    job = main._clip_jobs[job_id]
    assert job["status"] == "ready"
    assert job["clips"][0]["clip_id"] == out["clips"][0]["clip_id"]   # id survived
    assert job["clips"][0]["status"] == "ready"
    assert job.get("edit_brief") is not None
    assert job["creator_id"] == "creator-1"


def test_live_brief_failure_proceeds_briefless(monkeypatch):
    job_id, out = _seed_live_job(monkeypatch)

    async def boom_brief(*a, **k):
        raise RuntimeError("brief exploded")
    monkeypatch.setattr(main, "_generate_edit_brief", boom_brief)
    asyncio.run(main._run_auto_pipeline(job_id))
    job = main._clip_jobs[job_id]
    assert job["status"] == "ready"                      # edit still happened
    assert job.get("edit_brief") is None                 # briefless, honestly


def test_live_transcription_failure_fails_job(monkeypatch):
    job_id, out = _seed_live_job(monkeypatch)

    async def boom_transcribe(job_id):
        raise main.PipelineError("transcribe_submit_failed", "rejected", "transcribe")
    monkeypatch.setattr(main, "_transcribe_job", boom_transcribe)
    asyncio.run(main._run_auto_pipeline(job_id))
    job = main._clip_jobs[job_id]
    assert job["status"] == "failed"
    assert job["error"] == "transcribe_submit_failed"
