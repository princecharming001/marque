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
    assert r.json()["status"] == "ready"


def test_scripts_returns_payload():
    r = client.post("/v1/scripts", json={"pillar": "Lessons", "count": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] in ("mock", "live")
