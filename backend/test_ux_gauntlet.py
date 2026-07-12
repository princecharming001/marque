"""UX-F.2: end-to-end mock walkthrough across every fix's API surface, keyless."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

import main
from app import push as P
from main import app

client = TestClient(app)


def test_full_ux_walkthrough_keyless():
    # 1) DEVICES: register a push token (in-memory keyless registry).
    P._mem_tokens.clear()
    r = client.post("/v1/devices", json={"token": "walk-tok", "environment": "sandbox",
                                         "creator_id": "walker"})
    assert r.json()["ok"] is True

    # 2) ONE-TAP SUBMIT: auto_confirm keyless → immediate mock_ready + tracked clips.
    r = client.post("/v1/clips", json={
        "source_url": "mock://take.mov", "analyze_first": True, "auto_confirm": True,
        "creator_id": "walker", "edit_format": "recap_music",
        "script": {"hook": "Big claim", "body": "Proof.", "cta": "Follow."}}).json()
    assert r["status"] == "mock_ready" and r["clips"][0]["status"] == "ready"
    job_id, clip_id = r["job_id"], r["clips"][0]["clip_id"]
    job = main._clip_jobs[job_id]
    assert job["edit_prefs"]["music"] is True          # format toggles auto-applied
    assert job["edl"] is not None

    # 3) EXAMPLES: keyless per-format cards are honestly SAMPLE-flagged, every format.
    for fmt in main.prompts.EDIT_FORMATS:
        body = client.get(f"/v1/reels/examples?format={fmt}&niche=fitness").json()
        assert body["reels"], fmt
        assert all(x["sample"] is True and x["selection_reason"] for x in body["reels"])

    # 4) FEED: every script pick carries why_picked (cold-start honest reasons).
    main._feed_cache.clear()
    feed = client.get("/v1/feed?creator_id=walker&niche=fitness&fresh=1").json()
    scripts = [it["script"] for it in feed["items"] if it["type"] == "script"]
    assert scripts and all(s.get("why_picked") for s in scripts)

    # 5) TWEAK PREVIEW (mocked renderer config): stage → ops echoed → EDL untouched →
    #    apply commits once.
    before = copy.deepcopy(job["edl"])
    import unittest.mock as um
    with um.patch.object(main, "REMOTION_SERVE_URL", "https://serve"), \
         um.patch.object(main, "REMOTION_ACCESS_KEY", "k"), \
         um.patch.object(main, "REMOTION_FUNCTION_NAME", "fn"), \
         um.patch.object(main, "_spawn", lambda coro: coro.close()):
        pr = client.post(f"/v1/clips/{job_id}/tweak?preview=1",
                         json={"clip_id": clip_id,
                               "ops": [{"type": "set_caption_style", "style": "karaoke"}]}).json()
        assert pr["preview_requested"] is True and pr["ops"]
        assert main._clip_jobs[job_id]["edl"] == before
        client.post(f"/v1/clips/{job_id}/tweak", json={"clip_id": clip_id, "ops": pr["ops"]})
        assert main._clip_jobs[job_id]["edl"]["caption_style"] == "karaoke"

    # 6) PUSH HOOK: the job pushed nothing keyless (PUSH_CONFIGURED False) — but the
    #    creator's token is registered and enabled, ready for the live path.
    import asyncio
    assert asyncio.run(P.tokens_for("walker"))
    P._mem_tokens.clear()
    main._feed_cache.clear()
