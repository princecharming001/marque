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
