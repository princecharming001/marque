"""QA pass 2026-10-13: regression tests for edge cases found by tracing the app's real calls.

Each test pins a bug that shipped builds hit (the app can't change until the next build, so
these are backend-side fixes that land over the air)."""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from app import studio_engine as se
from main import app
from test_studio_engine import READY, FakeWorker, _no_spawn, _submit, studio_on  # noqa: F401 (fixture)

client = TestClient(app)


def _ready_studio_job(monkeypatch):
    _no_spawn(monkeypatch)
    monkeypatch.setattr(se, "_call", FakeWorker([READY]))
    jid = _submit()["job_id"]
    asyncio.run(main._run_auto_pipeline(jid))
    return jid, main._clip_jobs[jid]


# ---------------------------------------------------------------------------------------------- chat on Studio
def test_studio_chat_preview_is_declined_so_the_app_sends_one_commit(studio_on):
    """The chat sheet always sends ?preview=1 first and only commits when preview_requested is
    false. The Studio path used to start the re-edit on the preview call, so the app's commit
    hit 409 'still rendering' and the new cut was never picked up."""
    jid, job = _ready_studio_job(studio_on)
    clip = job["clips"][0]
    fw = FakeWorker([], chat_script=[{"chat": {"status": "ready"}, "rec": {"render_url": "https://s3/r1.mp4"}}])
    fw.rec = {"job_id": jid, "status": "ready", "render_url": READY["render_url"], "chat": None}
    studio_on.setattr(se, "_call", fw)
    studio_on.setattr(main, "_spawn", lambda coro: coro.close())

    r = client.post(f"/v1/clips/{jid}/tweak?preview=1", json={"clip_id": clip["clip_id"], "instruction": "shorter"})
    assert r.status_code == 200
    body = r.json()
    assert body["preview_requested"] is False and not body.get("error") and body["needs_render"] is False
    assert clip["status"] == "ready" and not [c for c in fw.calls if c[1].endswith("/chat")]  # nothing started
    r2 = client.post(f"/v1/clips/{jid}/tweak", json={"clip_id": clip["clip_id"], "instruction": "shorter"})
    assert r2.status_code == 200 and r2.json()["needs_render"] is True and clip["status"] == "rendering"
    assert len([c for c in fw.calls if c[1].endswith("/chat")]) == 1


def test_studio_refusals_reach_the_user_as_a_reply(studio_on):
    """Shipped builds turn every 409 into "I'm still rendering your last tweak", so the real
    reason (manual edits unsupported, worker down) never showed. Refusals are 200 + error +
    reply, and typed ops carry the reason in skipped[] for the editor's Save."""
    jid, job = _ready_studio_job(studio_on)
    clip = job["clips"][0]
    r = client.post(f"/v1/clips/{jid}/tweak", json={"clip_id": clip["clip_id"], "instruction": "",
                                                   "ops": [{"type": "trim", "seg": 0}]})
    assert r.status_code == 200
    b = r.json()
    assert b["error"] is True and "describe the change in chat" in b["reply"].lower()
    assert b["skipped"] and b["skipped"][0]["reason"] == b["reply"] and b["needs_render"] is False

    async def dead(*a, **k):
        raise se.StudioUnavailable("worker_unreachable", "ConnectError")
    studio_on.setattr(se, "_call", dead)
    r = client.post(f"/v1/clips/{jid}/tweak", json={"clip_id": clip["clip_id"], "instruction": "bigger captions"})
    assert r.status_code == 200 and r.json()["error"] is True and "try again in a few minutes" in r.json()["reply"].lower()
    assert clip["status"] == "ready"


def test_an_overlong_instruction_gets_a_readable_reply_not_a_422():
    out = client.post("/v1/clips", json={"source_url": "mock://take.mov", "analyze_first": True,
                                         "auto_confirm": True, "script": {"hook": "h", "body": "b", "cta": "c"}}).json()
    jid, cid = out["job_id"], out["clips"][0]["clip_id"]
    r = client.post(f"/v1/clips/{jid}/tweak", json={"clip_id": cid, "instruction": "make it pop " * 200})
    assert r.status_code == 200
    assert r.json()["error"] is True and "sentence or two" in r.json()["reply"]
    assert not main._clip_jobs[jid]["tweaks"]  # nothing stored into the prompt history


# ---------------------------------------------------------------------------------------------- routing
def test_free_tier_jobs_stay_on_the_watermarking_legacy_pipeline(monkeypatch):
    """Free-tier legacy renders carry the "powered by Yunicorn" watermark; Studio renders had
    none, so routing free jobs to Studio gave the watermark away."""
    monkeypatch.setattr(se, "WORKER_URL", "http://w")
    monkeypatch.setattr(se, "WORKER_TOKEN", "t")
    base = dict(creator_id="c1", auto_confirm=True, style="talking_head", react_source_url=None)
    assert se.choose_engine(**base, config={"is_pro": "0"}, mode="on") == "legacy"
    assert se.choose_engine(**base, config={"is_pro": "1"}, mode="on") == "studio"
    assert se.choose_engine(**base, config={}, mode="on") == "studio"  # older clients: same as legacy (no mark)


# ---------------------------------------------------------------------------------------------- progress
def test_an_overdue_stage_reports_no_eta_instead_of_a_frozen_one_minute():
    now = time.time()
    assert main._job_eta_seconds({"status": "editing", "stage_started_at": now - 10_000}) is None
    assert main._job_eta_seconds({"status": "editing", "stage_started_at": now}) >= 125


def test_studio_stages_have_their_own_estimates():
    now = time.time()
    job = {"status": "editing", "engine": "studio", "stage_started_at": now, "duration_ms": 30_000}
    assert main._job_eta_seconds(job) >= 280            # minutes, not the legacy 130 s
    long_take = {**job, "duration_ms": 180_000}
    assert main._job_eta_seconds(long_take) >= 800      # scaled by the take, like legacy words


def test_the_watchdog_leaves_studio_time_to_fall_back():
    """The job watchdog failed a slow Studio stage at 960 s + 3 s/source-second, before the
    Studio client's own limits (which fall back to legacy) could fire: the creator got 'a
    brief server restart' instead of a finished clip."""
    job = {"engine": "studio", "duration_ms": 30_000}
    assert main._live_pipeline_ceiling_s(job, main.RENDER_WATCHDOG_S) >= se.QUEUE_MAX_S + se.MAX_S
    assert main._live_pipeline_ceiling_s({"duration_ms": 30_000}, main.RENDER_WATCHDOG_S) < se.MAX_S


# ---------------------------------------------------------------------------------------------- uploads
def test_upload_urls_are_signed_with_upsert_so_a_lost_response_retry_succeeds(monkeypatch):
    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"url": "/object/upload/sign/marque-clips/uploads/x/take.mov?token=t"}

    async def fake_post(self, url, headers=None, **k):
        seen["headers"] = headers or {}
        return FakeResp()
    monkeypatch.setattr(main, "SUPABASE_URL", "https://sb.example")
    monkeypatch.setattr(main, "SUPABASE_KEY", "service")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    out = asyncio.run(main._mint_supabase_upload("qa-editor-take.mov"))
    assert out and out["upload_url"].startswith("https://sb.example/storage/v1/object/upload/sign/")
    assert seen["headers"].get("x-upsert") == "true"


# ---------------------------------------------------------------------------------------------- errors
def test_internal_errors_never_show_raw_exception_text():
    """The app prints internal_error's detail verbatim: "Something unexpected happened
    (KeyError: 'segments')"."""
    job = {"status": "editing", "clips": [{"clip_id": "c", "status": "rendering"}]}
    main._fail_job(job, "internal_error", "KeyError: 'segments'")
    assert job["error_detail"] == "" and job["error_debug"] == "KeyError: 'segments'"
    assert "error_detail" not in job["clips"][0] and job["clips"][0]["error"] == "internal_error"
    other = {"status": "editing", "clips": []}
    main._fail_job(other, "source_unreachable", "HTTP 404 fetching the source")
    assert other["error_detail"] == "HTTP 404 fetching the source"  # specific codes keep their detail


def test_the_job_says_which_engine_made_it_and_what_it_supports(studio_on):
    jid, _job = _ready_studio_job(studio_on)
    g = client.get(f"/v1/clips/{jid}").json()
    assert g["engine"] == "studio"
    assert g["edit_capabilities"] == {"chat_tweak": True, "manual_edit": False, "restore": False, "retheme": False}
    studio_on.setattr(se, "MODE", "off")
    out = client.post("/v1/clips", json={"source_url": "mock://take.mov", "analyze_first": True, "auto_confirm": True,
                                         "script": {"hook": "h", "body": "b", "cta": "c"}}).json()
    g2 = client.get(f"/v1/clips/{out['job_id']}").json()
    assert g2["engine"] == "legacy" and g2["edit_capabilities"]["manual_edit"] is False  # not edited yet
    assert main._edit_capabilities({"engine": "legacy", "edl": {"segments": []}})["manual_edit"] is True


# ---------------------------------------------------------------------------------------------- uploads (security)
@pytest.mark.parametrize("name,want", [
    ("../../posters/victim.jpg", "victim.jpg"), ("../../../other-bucket/x.mov", "x.mov"),
    ("x?y#z.mov", "x_y_z.mov"), ("", "footage.mov"), ("..", "footage.mov")])
def test_an_upload_filename_cannot_leave_its_uploads_folder(monkeypatch, name, want):
    """The storage key was uploads/<uuid>/<raw filename>; "../../posters/x.jpg" signed an upload
    over other files (posters, SFX, other creators' takes) with the service key."""
    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"url": "/object/upload/sign/k?token=t"}

    async def fake_post(self, url, headers=None, **k):
        seen["url"] = url
        return FakeResp()
    monkeypatch.setattr(main, "SUPABASE_URL", "https://sb.example")
    monkeypatch.setattr(main, "SUPABASE_KEY", "service")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    out = asyncio.run(main._mint_supabase_upload(name))
    prefix = "https://sb.example/storage/v1/object/upload/sign/marque-clips/uploads/"
    assert seen["url"].startswith(prefix) and seen["url"].endswith("/" + want)
    assert seen["url"][len(prefix):].count("/") == 1 and ".." not in seen["url"]
    assert out["key"].startswith("uploads/") and out["key"].endswith("/" + want)


# ---------------------------------------------------------------------------------------------- confirm / retry
def test_a_late_confirm_on_a_finished_job_keeps_the_clip_and_the_cut():
    out = client.post("/v1/clips", json={"source_url": "mock://take.mov", "analyze_first": True,
                                         "auto_confirm": True, "script": {"hook": "h", "body": "b", "cta": "c"}}).json()
    jid = out["job_id"]
    job = main._clip_jobs[jid]
    clip_id, edl = job["clips"][0]["clip_id"], job["edl"]
    r = client.post(f"/v1/clips/{jid}/confirm", json={"toggles": {"broll": True}})
    assert r.status_code == 200 and r.json()["clips"][0]["clip_id"] == clip_id
    assert job["clips"][0]["clip_id"] == clip_id and job["edl"] is edl and job["status"] == "mock_ready"


def test_retry_on_a_job_awaiting_brief_review_does_not_skip_the_review():
    r = client.post("/v1/clips", json={"source_url": "mock://x", "analyze_first": True,
                                       "script": {"hook": "h", "body": "b", "cta": "c"}}).json()
    assert r["status"] == "brief_ready"
    assert client.post(f"/v1/clips/{r['job_id']}/retry").status_code == 409
    assert main._clip_jobs[r["job_id"]]["status"] == "brief_ready"


# ---------------------------------------------------------------------------------------------- Studio robustness
def _legacy_stub(monkeypatch, calls):
    async def fake_analyze(job_id, **k):
        calls.append("analyze")

    async def fake_edit(job_id, words):
        calls.append("edit")
        job = main._clip_jobs[job_id]
        job["status"] = "ready"
        job["clips"][0].update(status="ready", render_url="https://lambda/out.mp4")
    monkeypatch.setattr(main, "_analyze_to_brief", fake_analyze)
    monkeypatch.setattr(main, "_run_edit", fake_edit)


def test_the_kill_switch_also_stops_resumed_and_retried_studio_jobs(studio_on):
    _no_spawn(studio_on)
    fw = FakeWorker([READY])
    studio_on.setattr(se, "_call", fw)
    jid = _submit()["job_id"]
    assert main._clip_jobs[jid]["engine"] == "studio"
    studio_on.setattr(se, "MODE", "off")                 # STUDIO_ENGINE flipped off, then a resume
    calls = []
    _legacy_stub(studio_on, calls)
    asyncio.run(main._run_auto_pipeline(jid))
    job = main._clip_jobs[jid]
    assert job["engine"] == "legacy" and job["engine_fallback"]["reason"] == "kill_switch"
    assert calls == ["analyze", "edit"] and not [c for c in fw.calls if c[:2] == ("POST", "/jobs")]


def test_a_retry_after_an_unseen_studio_failure_goes_straight_to_legacy(studio_on):
    _no_spawn(studio_on)
    fw = FakeWorker([READY])
    studio_on.setattr(se, "_call", fw)
    jid = _submit()["job_id"]
    main._clip_jobs[jid]["studio"] = {"status": "failed", "error": "studio_no_speech"}
    calls = []
    _legacy_stub(studio_on, calls)
    asyncio.run(main._run_auto_pipeline(jid))
    assert main._clip_jobs[jid]["engine_fallback"]["reason"] == "studio_failed_before"
    assert calls == ["analyze", "edit"]


def test_a_fallback_cancels_the_worker_copy_and_skips_the_second_probe(studio_on):
    _no_spawn(studio_on)
    fw = FakeWorker([{"status": "failed", "error": "studio_error"}])
    studio_on.setattr(se, "_call", fw)
    probes = []

    async def counted_probe(job):
        probes.append(1)
        job["source_duration_s"] = 30.0
    studio_on.setattr(main, "_prepare_source", counted_probe)

    async def run(jid):
        studio_on.setattr(main, "_run_edit", lambda *a, **k: asyncio.sleep(0))
        await main._run_auto_pipeline(jid)
        await asyncio.sleep(0)                       # let the spawned cancel run
    studio_on.setattr(main, "_transcribe_job", lambda jid: asyncio.sleep(0, result=[]))
    studio_on.setattr(main, "_dossier_job", lambda jid: asyncio.sleep(0))
    studio_on.setattr(main.audio_mod, "probe_loudness", lambda *a, **k: asyncio.sleep(0))
    studio_on.setattr(main, "_generate_edit_brief", lambda *a, **k: asyncio.sleep(0))
    jid = _submit()["job_id"]
    asyncio.run(run(jid))
    assert ("DELETE", f"/jobs/{jid}", None) in fw.calls
    assert len(probes) == 1                          # the legacy analyze reused the Studio probe


def test_a_studio_chat_in_flight_across_an_api_restart_still_lands(studio_on):
    jid, job = _ready_studio_job(studio_on)
    clip = job["clips"][0]
    clip.update(status="rendering", studio_request_id="req-1", prev_render_url=READY["render_url"])
    fw = FakeWorker([], chat_script=[{"chat": {"status": "ready"}, "rec": {"render_url": "https://s3/r1.mp4"}}])
    fw.rec = {"job_id": jid, "status": "ready", "render_url": READY["render_url"],
              "chat": {"request_id": "req-1", "status": "running"}}
    studio_on.setattr(se, "_call", fw)
    spawned = []
    studio_on.setattr(main, "_spawn", lambda coro: spawned.append(coro))
    main._reattach_studio_chats(job)                  # what _restore_clip_job runs after a restart

    async def drain():
        for c in list(spawned):
            await c
    asyncio.run(drain())
    assert clip["status"] == "ready" and clip["render_url"] == "https://s3/r1.mp4"
    assert "studio_request_id" not in clip and not clip.get("last_render_failed")


def test_a_studio_re_edit_that_overruns_restores_the_previous_cut_not_a_failure():
    job = {"job_id": "qa-editor-j", "status": "ready", "engine": "studio", "clips": [{
        "clip_id": "c", "status": "rendering", "render_url": "https://s3/new-pending.mp4",
        "prev_render_url": "https://s3/r0.mp4", "studio_request_id": "req",
        "render_started_at": time.time() - 99_999, "render_budget_s": 100}]}
    main._sweep_stuck_renders({"qa-editor-j": job})
    c = job["clips"][0]
    assert c["status"] == "ready" and c["render_url"] == "https://s3/r0.mp4" and c["last_render_failed"] is True
    assert c.get("error") is None


def test_a_studio_success_clears_a_stale_watchdog_error(studio_on):
    _no_spawn(studio_on)
    studio_on.setattr(se, "_call", FakeWorker([READY]))
    jid = _submit()["job_id"]
    main._clip_jobs[jid]["clips"][0].update(error_detail="render exceeded 1800s watchdog", error="render_stalled")
    asyncio.run(main._run_auto_pipeline(jid))
    c = main._clip_jobs[jid]["clips"][0]
    assert c["status"] == "ready" and c["error"] is None and "error_detail" not in c
