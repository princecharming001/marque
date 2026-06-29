import json

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz():
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["ai"] in ("live", "mock")
    assert body["publish"] in ("live", "mock")


def test_pillars_are_niche_specific():
    r = client.post("/v1/pillars", json={
        "niche": "fitness coaching", "audience": "busy professionals",
        "known_for": "no-nonsense fitness",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] in ("mock", "live")
    pillars = body["pillars"]
    assert len(pillars) == 5
    # The creator's niche must be woven in — not the old static generic list.
    blob = json.dumps(pillars).lower()
    assert "fitness" in blob
    assert all({"name", "summary", "angle", "exampleTopics"} <= set(p) for p in pillars)


def test_scripts_are_structured():
    r = client.post("/v1/scripts", json={"niche": "fitness", "pillar": "Myth-busting", "count": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] in ("mock", "live")
    scripts = body["scripts"]
    assert len(scripts) == 2
    assert all({"title", "summary", "formatId"} <= set(s) for s in scripts)


def test_captions_chunk_short():
    r = client.post("/v1/captions", json={"hook": "Stop overthinking fitness", "body": "Do this instead. It works every time."})
    assert r.status_code == 200
    lines = r.json()["lines"]
    assert len(lines) >= 1
    assert all(len(ln.split()) <= 5 for ln in lines)


def test_clip_job_descriptor():
    r = client.post("/v1/clips", json={"formats": ["myth-buster"], "auto_captions": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["job_id"].startswith("clip_")
    assert "burn-captions" in body["steps"]


def test_publish_mock_ok():
    r = client.post("/v1/publish", json={"caption": "hello", "platforms": ["instagram"]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["mode"] == "mock"
