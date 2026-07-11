"""P1.2 + P1.3: dossier plumbing into the pipeline + brief fusion (keyless)."""
from __future__ import annotations

import asyncio

import prompts
from app import dossier as D


def _run(coro):
    return asyncio.run(coro)


def test_dossier_block_frame_anchored():
    d = D.mock_dossier(30000)
    block = prompts._dossier_block(d)
    assert "VIDEO DOSSIER" in block
    assert "[f" in block  # frame anchors, not seconds
    assert "b-roll cue" in block
    assert prompts._dossier_block(None) == ""


def test_brief_prompt_with_dossier_grounds_visuals():
    words = [{"word": "hi", "start_ms": 0, "end_ms": 300}]
    d = D.mock_dossier(30000)
    system, user = prompts.edit_brief_prompt(words, dossier=d)
    # the "you cannot see the video" clause is REPLACED by dossier grounding
    assert "cannot see the video" not in system
    assert "VISUAL FACTS come ONLY from the VIDEO DOSSIER" in system
    assert "VIDEO DOSSIER" in user


def test_brief_prompt_without_dossier_keeps_transcript_only():
    words = [{"word": "hi", "start_ms": 0, "end_ms": 300}]
    system, user = prompts.edit_brief_prompt(words)
    assert "cannot see the video" in system
    assert "VIDEO DOSSIER" not in user


def test_dossier_job_off_is_noop(monkeypatch):
    import main
    monkeypatch.setattr(D, "VIDEO_UNDERSTANDING", "off")
    job_id = "t-off"
    main._clip_jobs[job_id] = {"source_url": "http://x/v.mp4", "duration_ms": 30000}
    try:
        d = _run(main._dossier_job(job_id))
        assert d is None
        assert main._clip_jobs[job_id]["dossier_status"] == "off"
    finally:
        main._clip_jobs.pop(job_id, None)


def test_dossier_job_ready_sets_status(monkeypatch):
    import main
    monkeypatch.setattr(D, "VIDEO_UNDERSTANDING", "claude_frames")

    async def fake_gen(url, dur, provider=None):
        return D.mock_dossier(dur)
    monkeypatch.setattr(D, "generate_dossier", fake_gen)

    job_id = "t-ready"
    main._clip_jobs[job_id] = {"source_url": "http://x/v.mp4", "duration_ms": 30000}
    try:
        d = _run(main._dossier_job(job_id))
        assert d and d["version"] == D.DOSSIER_VERSION
        assert main._clip_jobs[job_id]["dossier_status"] == "ready"
    finally:
        main._clip_jobs.pop(job_id, None)


def test_dossier_job_failsoft_on_error(monkeypatch):
    import main
    monkeypatch.setattr(D, "VIDEO_UNDERSTANDING", "twelvelabs")

    async def boom(url, dur, provider=None):
        raise RuntimeError("provider exploded")
    monkeypatch.setattr(D, "generate_dossier", boom)

    job_id = "t-boom"
    main._clip_jobs[job_id] = {"source_url": "http://x/v.mp4", "duration_ms": 30000}
    try:
        d = _run(main._dossier_job(job_id))
        assert d is None  # never raises
        assert main._clip_jobs[job_id]["dossier_status"] == "unavailable"
    finally:
        main._clip_jobs.pop(job_id, None)
