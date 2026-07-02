import json

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_healthz():
    assert client.get("/healthz").json()["status"] == "ok"


def test_readyz_reports_modes():
    b = client.get("/readyz").json()
    assert b["status"] == "ready"
    assert b["ai"] in ("live", "mock")
    assert b["scrape"] in ("live", "mock")
    assert b["publish"] in ("live", "mock")


def test_pillars_niche_specific():
    r = client.post("/v1/pillars", json={"niche": "fitness coaching", "audience": "busy professionals",
                                         "known_for": "no-nonsense fitness"})
    p = r.json()["pillars"]
    assert len(p) == 5
    assert "fitness" in json.dumps(p).lower()
    assert all({"name", "summary", "angle", "exampleTopics"} <= set(x) for x in p)


def test_scripts_are_style_aware():
    for style in ("talking_head", "faceless", "split_three"):
        r = client.post("/v1/scripts", json={"niche": "fitness", "style": style, "count": 2,
                                             "pillar": "Myth-busting"})
        s = r.json()["scripts"]
        assert len(s) == 2
        assert all(x.get("style") == style for x in s)
        # formatId must stay within the style's allowed set
        from prompts import STYLES
        allowed = set(STYLES[style]["formats"])
        assert all(x["formatId"] in allowed for x in s)


def test_hooks_steer_captions_teardown_insights():
    assert client.post("/v1/hooks", json={"niche": "fitness", "topic": "squats"}).status_code == 200
    assert client.post("/v1/steer", json={"script": {"hook": "x", "body": "y", "cta": "z"}, "instruction": "shorter"}).status_code == 200
    lines = client.post("/v1/captions", json={"hook": "Stop overthinking fitness", "body": "Do this instead. It works."}).json()["lines"]
    assert lines and all(len(l.split()) <= 5 for l in lines)
    assert "liftPercent" in client.post("/v1/teardown", json={"clip": {"predictedScore": 88}}).json()
    assert "coaching" in client.post("/v1/insights", json={"niche": "fitness", "summary": "5 clips, +120 follows"}).json()


def test_trends_niche():
    t = client.get("/v1/trends", params={"niche": "fitness"}).json()["trends"]
    assert t and "fitness" in json.dumps(t).lower()


def test_brand_scan_with_posts_corpus():
    posts = [{"caption": "3 mobility drills before you squat", "hashtags": ["#fitness"], "likes": 1200, "comments": 40}]
    r = client.post("/v1/brand-scan/handle", json={"niche": "fitness", "handle": "coachx", "posts": posts})
    scan = r.json()["scan"]
    assert {"niche", "voice", "pillars"} <= set(scan)
    assert len(scan["pillars"]) == 5


def test_voice_finalize():
    r = client.post("/v1/voice-onboarding/finalize", json={
        "niche": "fitness",
        "transcript": [{"role": "agent", "text": "What do you make videos about?"},
                       {"role": "user", "text": "Strength training for busy dads"}]})
    assert {"niche", "pillars"} <= set(r.json()["scan"])


def test_publish_mock():
    b = client.post("/v1/publish", json={"caption": "hi", "platforms": ["instagram"]}).json()
    assert b["ok"] is True and b["mode"] == "mock"


def test_mint_upload_url():
    r = client.post("/v1/uploads/mint", json={"filename": "test.mov", "content_type": "video/quicktime"})
    assert r.status_code == 200
    b = r.json()
    assert "upload_url" in b
    assert "key" in b
    assert "public_url" in b
    assert b["mode"] in ("live", "mock")


def test_create_clip_job():
    r = client.post("/v1/clips", json={
        "source_url": "https://example.com/footage.mov",
        "source_id": "test-123",
        "formats": ["myth-buster"],
        "style": "talking_head",
        "script": {"hook": "Stop doing this", "body": "Here is why.", "cta": "Follow me.", "formatId": "myth-buster"},
    })
    assert r.status_code == 200
    b = r.json()
    assert "job_id" in b
    assert "clips" in b
    assert b["mode"] in ("live", "mock")
    assert len(b["clips"]) == 1
    assert b["clips"][0]["format"] == "myth-buster"


def test_get_clip_job():
    # Create a job first
    r = client.post("/v1/clips", json={
        "source_url": "https://example.com/footage.mov",
        "source_id": "test-456",
        "formats": ["listicle"],
        "style": "fast_cuts",
        "script": {"hook": "Three tips", "body": "Tip 1. Tip 2. Tip 3.", "cta": "Save this.", "formatId": "listicle"},
    })
    job_id = r.json()["job_id"]
    r2 = client.get(f"/v1/clips/{job_id}")
    assert r2.status_code == 200
    b = r2.json()
    assert b["job_id"] == job_id
    assert "status" in b
    assert "clips" in b
    assert b["mode"] in ("live", "mock")


def test_get_clip_job_not_found():
    r = client.get("/v1/clips/nonexistent-job-id")
    assert r.status_code == 404


def test_media_analyze():
    r = client.post("/v1/media/analyze", json={
        "content_hash": "abc123", "filename": "test.jpg", "kind": "photo", "public_url": ""
    })
    assert r.status_code == 200
    b = r.json()
    assert "broll_suitability" in b
    assert b["mode"] in ("live", "mock", "cached")


def test_broll_match():
    r = client.post("/v1/broll/match", json={
        "cue_text": "close-up of hands kneading dough",
        "corpus": [
            {"asset_id": "1", "description": "close-up of hands working with bread dough",
             "tags": ["hands", "dough", "bread", "close-up"], "broll_suitability": 85},
            {"asset_id": "2", "description": "exterior shot of a bakery storefront",
             "tags": ["bakery", "exterior", "shop"], "broll_suitability": 30},
        ]
    })
    assert r.status_code == 200
    b = r.json()
    assert "matches" in b
    assert b["mode"] in ("live", "mock")


def test_register_post():
    r = client.post("/v1/posts/register", json={
        "post_id": "test-post-1",
        "pillar": "Myth-busting",
        "style": "talking_head",
        "format_id": "myth-buster",
        "hook_signal": "contrarian",
        "predicted_score": 82,
    })
    assert r.status_code == 200
    assert r.json()["status"] in ("registered", "already_registered")


def test_ingest_metrics():
    client.post("/v1/posts/register", json={"post_id": "test-post-2", "pillar": "Hot takes",
                                             "style": "fast_cuts", "format_id": "listicle"})
    r = client.post("/v1/metrics/ingest", json={
        "post_id": "test-post-2",
        "views": 5000, "likes": 200, "shares": 50, "saves": 80,
        "reach": 4500, "avg_watch_pct": 0.65, "follows_gained": 30
    })
    assert r.status_code == 200
    b = r.json()
    assert b["status"] in ("ingested", "already_settled", "below_min_reach")


def test_recommendations():
    r = client.get("/v1/recommendations?niche=fitness")
    assert r.status_code == 200
    b = r.json()
    assert "arms" in b
    assert len(b["arms"]) > 0


def test_learned_insights():
    r = client.get("/v1/insights/learned")
    assert r.status_code == 200
    b = r.json()
    assert "insights" in b
    assert "learning_progress" in b


# ---------------------------------------------------------------------------
# V3: conversation engine + TTS
# ---------------------------------------------------------------------------

def _converse(messages, mode="chat", memory=None):
    return client.post("/v1/converse", json={
        "creator_id": "test-creator", "mode": mode,
        "messages": messages, "brand": {"niche": "fitness coaching", "audience": "busy dads"},
        "memory": memory or {},
    }).json()


def test_converse_basic_reply_and_memory():
    b = _converse([{"role": "user", "content": "I think most fitness advice ignores how little time parents have"}])
    assert b["mode"] in ("live", "mock")
    assert b["reply"].strip()
    assert isinstance(b["memory_updates"], list)
    # a perspective statement should produce a memory update in mock mode
    assert any(u["field"] in ("perspective", "facts") for u in b["memory_updates"])
    assert all({"op", "field", "value"} <= set(u) for u in b["memory_updates"])


def test_converse_day_plan_intent():
    b = _converse([{"role": "user", "content": "Build my day out for me"}])
    assert b["intent"] == "day_plan"
    blocks = b["payload"]["plan"]["blocks"]
    assert len(blocks) >= 4
    assert all({"time", "action", "detail"} <= set(x) for x in blocks)


def test_converse_scripts_intent_chains_scripts():
    b = _converse([{"role": "user", "content": "Write me a script about protein timing"}])
    assert b["intent"] == "generate_scripts"
    scripts = b["payload"]["scripts"]
    assert scripts and all("hook" in s and "body" in s for s in scripts)


def test_converse_voice_mode_short_plain():
    b = _converse([{"role": "user", "content": "What should I post today?"}], mode="voice")
    assert b["reply"].strip()
    assert "\n" not in b["reply"]          # spoken replies are single-block
    assert "**" not in b["reply"]          # no markdown in voice mode


def test_converse_angle_update_sets_memory():
    b = _converse([{"role": "user", "content": "My brand angle should be tough love for busy dads"}])
    assert b["intent"] == "update_brand_angle"
    assert any(u["field"] == "angle" and u["op"] == "set" for u in b["memory_updates"])


def test_converse_chips_present():
    b = _converse([{"role": "user", "content": "hey"}])
    assert isinstance(b["suggested_chips"], list) and len(b["suggested_chips"]) >= 1


def test_tts_mock_when_keyless():
    r = client.post("/v1/tts", json={"text": "Hello creator"})
    assert r.status_code == 200
    # keyless CI: JSON mock contract; with a key this would be audio/mpeg bytes
    if r.headers["content-type"].startswith("application/json"):
        assert r.json()["mode"] == "mock"
    else:
        assert r.headers["content-type"] == "audio/mpeg"


def test_tts_empty_text_rejected():
    assert client.post("/v1/tts", json={"text": "  "}).status_code == 400
