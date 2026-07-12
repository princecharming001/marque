"""UX-B2a: APNs push adapter (app/push.py) — keyless, throwaway-key, mocked transport."""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import main
from app import push as P
from main import app

client = TestClient(app)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_registry():
    P._mem_tokens.clear()
    P._jwt_cache.update(token=None, issued_at=0.0)
    yield
    P._mem_tokens.clear()


def _throwaway_p8() -> str:
    """Generate a throwaway ES256 (P-256) private key PEM — never a real credential."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode()


def _configure(monkeypatch, p8: str):
    monkeypatch.setattr(P, "APNS_KEY_ID", "KEY123")
    monkeypatch.setattr(P, "APNS_TEAM_ID", "TEAM456")
    monkeypatch.setattr(P, "APNS_P8", p8)
    monkeypatch.setattr(P, "APNS_TOPIC", "com.getmarque.app")
    monkeypatch.setattr(P, "PUSH_CONFIGURED", True)


# --- keyless no-op --------------------------------------------------------------

def test_keyless_send_is_noop():
    assert P.PUSH_CONFIGURED is False              # CI contract: env keys absent
    assert _run(P.send_clips_ready("c1", "clip-1")) == 0
    assert P._provider_jwt() is None


# --- JWT claims with a throwaway key --------------------------------------------

def test_provider_jwt_claims_and_cache(monkeypatch):
    _configure(monkeypatch, _throwaway_p8())
    tok = P._provider_jwt(now=1000.0)
    assert tok
    import jwt as pyjwt
    header = pyjwt.get_unverified_header(tok)
    claims = pyjwt.decode(tok, options={"verify_signature": False})
    assert header["alg"] == "ES256" and header["kid"] == "KEY123"
    assert claims["iss"] == "TEAM456" and claims["iat"] == 1000
    # cached within TTL, re-signed after
    assert P._provider_jwt(now=1000.0 + 60) == tok
    assert P._provider_jwt(now=1000.0 + 41 * 60) != tok


# --- registry -------------------------------------------------------------------

def test_upsert_idempotent_and_reenables():
    r1 = _run(P.upsert_device("c1", "tok-a", "sandbox"))
    _run(P._disable("tok-a", "sandbox"))
    assert not _run(P.tokens_for("c1"))            # disabled → filtered out
    r2 = _run(P.upsert_device("c1", "tok-a", "sandbox"))   # re-register re-enables
    rows = _run(P.tokens_for("c1"))
    assert len(rows) == 1 and rows[0]["token"] == "tok-a"
    assert len(P._mem_tokens) == 1                 # (token, env) unique — no dup


def test_devices_endpoint_upserts():
    r = client.post("/v1/devices", json={"token": "tok-x", "environment": "sandbox",
                                         "creator_id": "c9", "timezone": "America/New_York"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["push_configured"] is False
    assert ("tok-x", "sandbox") in P._mem_tokens
    # bad environment coerces to sandbox; empty token rejected
    r2 = client.post("/v1/devices", json={"token": "tok-y", "environment": "weird"})
    assert r2.json()["environment"] == "sandbox"
    assert client.post("/v1/devices", json={"token": "  "}).status_code == 422


# --- send: success, 410 soft-disable, one-per-job hook ---------------------------

def test_send_and_410_soft_disable(monkeypatch):
    _configure(monkeypatch, _throwaway_p8())
    _run(P.upsert_device("c1", "tok-good", "sandbox"))
    _run(P.upsert_device("c1", "tok-dead", "prod"))

    calls = []
    async def fake_post(host, token, payload, jwt_token):
        calls.append((host, token, payload))
        return (410, "Unregistered") if token == "tok-dead" else (200, "")
    monkeypatch.setattr(P, "_post_apns", fake_post)

    sent = _run(P.send_clips_ready("c1", "clip-42", count=2))
    assert sent == 1
    # payload shape: alert + category/thread + deeplink
    _, _, payload = calls[0]
    assert payload["aps"]["alert"]["title"] == "Your clip is ready"
    assert payload["aps"]["category"] == "clips_ready"
    assert payload["deeplink"] == "marque://library/clip/clip-42"
    # environments routed to their hosts
    hosts = {h for h, t, _ in calls}
    assert P._HOSTS["sandbox"] in hosts and P._HOSTS["prod"] in hosts
    # the 410 token is now soft-disabled
    assert P._mem_tokens[("tok-dead", "prod")]["disabled_at"] is not None
    assert len(_run(P.tokens_for("c1"))) == 1


def test_ready_hook_pushes_once_per_job(monkeypatch):
    """_run_edit's ready-landing fires send_clips_ready exactly once (push_sent)."""
    sends = []
    async def fake_send(creator_id, clip_id, count=1):
        sends.append((creator_id, clip_id, count))
        return 1
    monkeypatch.setattr(main.push_mod, "send_clips_ready", fake_send)
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "test-key")

    async def fake_transcribe(job_id):
        words = [{"word": w, "start_ms": i * 300, "end_ms": i * 300 + 250}
                 for i, w in enumerate("a b c d e f".split())]
        main._clip_jobs[job_id]["words"] = words
        return words
    async def fake_loudness(url, **k):
        return None
    async def render_ready(job_id):
        for c in main._clip_jobs[job_id]["clips"]:
            c["status"] = "ready"
    monkeypatch.setattr(main, "_transcribe_job", fake_transcribe)
    monkeypatch.setattr(main.audio_mod, "probe_loudness", fake_loudness)
    monkeypatch.setattr(main, "_render_all_clips", render_ready)

    r = client.post("/v1/clips", json={"source_url": "mock://x", "analyze_first": True,
                                       "auto_confirm": True, "creator_id": "push-me",
                                       "script": {"hook": "h", "body": "b", "cta": "c"}})
    job_id = r.json()["job_id"]
    _run(main._run_auto_pipeline(job_id))
    assert len(sends) == 1 and sends[0][0] == "push-me"
    # a second edit pass on the same job must NOT push again (push_sent)
    _run(main._run_edit(job_id, main._clip_jobs[job_id]["words"]))
    assert len(sends) == 1
