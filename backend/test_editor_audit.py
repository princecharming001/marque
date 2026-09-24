"""Editor audit (2026-09-24) — keyless regression tests.

Every test runs with NO vendor keys (same seams as test_long_take_hardening.py). The QA seam
tests pin the dev-only long-clip harness so it can never serve in a keyed (prod) process.
"""
import os

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def _media_dir(tmp_path, secs=60):
    p = tmp_path / f"{secs}.mov"
    p.write_bytes(b"\x00" * 64)
    return str(tmp_path)


def test_demo_src_job_spans_real_duration(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MEDIA_DIR", _media_dir(tmp_path, 600))
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._clip_jobs.pop("demo-src-600", None)
    r = client.get("/v1/clips/demo-src-600", params={"include_words": 1})
    assert r.status_code == 200
    b = r.json()
    assert b["source_url"].endswith("/v1/dev/media/600.mov")
    words = b["words"]
    assert words and words[-1]["end_ms"] > 590_000          # words cover the whole take
    segs = b["edl"]["segments"]
    assert segs[0]["src_in"] == 0 and segs[-1]["src_out"] == 600 * 30
    assert all(a["src_out"] <= b2["src_in"] for a, b2 in zip(segs, segs[1:]))   # monotonic
    assert len(b["edl"]["captions"]) == len(words)


def test_demo_src_tagged_ids_are_independent_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MEDIA_DIR", _media_dir(tmp_path, 60))
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    for jid in ("demo-src-60-se", "demo-src-60-pm"):
        main._clip_jobs.pop(jid, None)
        assert client.get(f"/v1/clips/{jid}").json()["source_url"].endswith("/60.mov")
    assert main._clip_jobs["demo-src-60-se"] is not main._clip_jobs["demo-src-60-pm"]


def test_demo_src_without_media_falls_back_to_placeholder(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_MEDIA_DIR", raising=False)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._clip_jobs.pop("demo-src-45", None)
    b = client.get("/v1/clips/demo-src-45").json()
    assert b["source_url"] is None                          # the classic placeholder demo job


def test_dev_media_route_never_serves_when_keyed(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MEDIA_DIR", _media_dir(tmp_path, 60))
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    assert client.get("/v1/dev/media/60.mov").status_code == 200
    assert client.get("/v1/dev/media/..%2Fetc").status_code == 404
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk-live")
    assert client.get("/v1/dev/media/60.mov").status_code == 404
