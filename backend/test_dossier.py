"""Tests for the video dossier adapter (app/dossier.py) — Phase 1, keyless.

Every external boundary (_tl_request, _extract_keyframes, _vision_json, _sleep) is
monkeypatched, so these run offline and prove: the provider chain fails DOWN correctly,
timestamps normalize to frame anchors, and the stored shape matches dossier v1.
"""
from __future__ import annotations

import asyncio
import pytest

from app import dossier as D


def _run(coro):
    return asyncio.run(coro)


# --- provider chain -----------------------------------------------------------

def test_off_returns_none():
    assert _run(D.generate_dossier("http://x/v.mp4", 30000, provider="off")) is None


def test_no_source_returns_none():
    assert _run(D.generate_dossier("", 30000, provider="claude_frames")) is None


def test_twelvelabs_fails_down_to_claude_frames(monkeypatch):
    # TL yields nothing (no key) → chain should try claude_frames next.
    monkeypatch.setattr(D, "TWELVELABS_KEY", "")
    called = {"frames": False}

    async def fake_frames(url, dur):
        called["frames"] = True
        return [(0, b"jpegbytes")]

    async def fake_vision(system, user, images, schema):
        return {"first_frame": {"desc": "d", "pattern_interrupt": True, "score": 0.5},
                "delivery_curve": [{"t0": 0, "t1": 2, "energy": 0.9, "note": "n"}]}

    monkeypatch.setattr(D, "_extract_keyframes", fake_frames)
    monkeypatch.setattr(D, "_vision_json", fake_vision)
    monkeypatch.setattr(D, "ANTHROPIC_KEY", "sk-test")

    d = _run(D.generate_dossier("http://x/v.mp4", 30000, provider="twelvelabs"))
    assert called["frames"] is True
    assert d and d["provider"] == "claude_frames"
    assert d["version"] == D.DOSSIER_VERSION


def test_claude_frames_no_frames_returns_none(monkeypatch):
    async def no_frames(url, dur):
        return []
    monkeypatch.setattr(D, "_extract_keyframes", no_frames)
    assert _run(D.generate_dossier("http://x/v.mp4", 30000, provider="claude_frames")) is None


# --- twelvelabs happy path (all HTTP mocked) ----------------------------------

def test_twelvelabs_full_lifecycle(monkeypatch):
    monkeypatch.setattr(D, "TWELVELABS_KEY", "tl-key")
    monkeypatch.setattr(D, "TWELVELABS_INDEX_ID", "idx-1")

    async def fake_sleep(s):  # no real waiting
        return None
    monkeypatch.setattr(D, "_sleep", fake_sleep)

    state = {"polls": 0}

    async def fake_req(method, path, **kw):
        if method == "POST" and path == "/tasks":
            return {"_id": "task-1"}
        if method == "GET" and path.startswith("/tasks/"):
            state["polls"] += 1
            if state["polls"] < 2:
                return {"status": "indexing"}
            return {"status": "ready", "video_id": "vid-1"}
        if method == "POST" and path == "/generate":
            return {"data": {
                "first_frame": {"desc": "open", "pattern_interrupt": True, "score": 0.8},
                "delivery_curve": [{"t0": 0, "t1": 3, "energy": 0.9, "note": "hot open"}],
                "visual_events": [{"t0": 1, "t1": 1.5, "kind": "gesture", "desc": "point"}],
                "framing": {"shot": "mid", "eye_contact": True},
                "broll_visual_opportunities": [{"t0": 2, "t1": 4, "cue": "result", "why": "named"}],
            }}
        return {}

    monkeypatch.setattr(D, "_tl_request", fake_req)
    d = _run(D.generate_dossier("http://x/v.mp4", 30000, provider="twelvelabs"))
    assert d["provider"] == "twelvelabs"
    assert state["polls"] >= 2  # actually polled until ready
    # seconds → frame anchors
    ev = d["visual_events"][0]
    assert ev["f0"] == D.ms_to_frame(1000) and ev["kind"] == "gesture"
    assert d["broll_visual_opportunities"][0]["cue"] == "result"


def test_twelvelabs_task_failure_returns_none(monkeypatch):
    monkeypatch.setattr(D, "TWELVELABS_KEY", "tl-key")
    monkeypatch.setattr(D, "TWELVELABS_INDEX_ID", "idx-1")

    async def fake_req(method, path, **kw):
        if path == "/tasks":
            return {"_id": "task-x"}
        if path.startswith("/tasks/"):
            return {"status": "failed"}
        return {}
    monkeypatch.setattr(D, "_tl_request", fake_req)
    # TL fails → chain falls to claude_frames which (no frames) → None
    async def no_frames(url, dur):
        return []
    monkeypatch.setattr(D, "_extract_keyframes", no_frames)
    assert _run(D.generate_dossier("http://x/v.mp4", 30000, provider="twelvelabs")) is None


# --- normalization + mock -----------------------------------------------------

def test_normalize_frame_anchors_and_shape():
    d = D.mock_dossier(30000)
    assert d["version"] == D.DOSSIER_VERSION and d["provider"] == "mock"
    # every span carries integer frame anchors
    for span in d["delivery_curve"] + d["visual_events"] + d["broll_visual_opportunities"]:
        assert isinstance(span["f0"], int) and isinstance(span["f1"], int)
        assert span["f1"] >= span["f0"]
    # required top-level keys present
    for k in ("first_frame", "framing", "scenes", "on_screen_text", "gaffes"):
        assert k in d
    assert set(d["first_frame"]) == {"desc", "pattern_interrupt", "score"}


def test_normalize_tolerates_garbage():
    d = D._normalize({"delivery_curve": ["not a dict", {"t0": "x"}], "framing": None}, "mock")
    # bad rows dropped/coerced, never raises
    assert d["provider"] == "mock"
    assert isinstance(d["delivery_curve"], list)
