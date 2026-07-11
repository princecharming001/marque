"""P5b: self-review loop (flag-gated, keyless via monkeypatched seams)."""
from __future__ import annotations

import asyncio

import main


def _run(coro):
    return asyncio.run(coro)


def _seed_job(edl=None):
    job = {"source_url": "mock://s", "style": "talking_head",
           "clips": [{"clip_id": "c1", "format": "myth-buster", "status": "queued"}],
           "words": [{"word": "hi", "start_ms": 0, "end_ms": 300}],
           "edl": edl or {"style": "talking_head", "format_id": "myth-buster",
                          "segments": [{"src_in": 0, "src_out": 300}],
                          "captions": [], "layout": {"style": "talking_head"}}}
    main._clip_jobs["srev"] = job
    return job


def test_self_review_off_is_noop(monkeypatch):
    monkeypatch.setattr(main, "SELF_REVIEW", False)
    job = _seed_job()
    _run(main._self_review_edl("srev"))
    assert "self_review" not in job
    main._clip_jobs.pop("srev", None)


def test_self_review_skips_on_rerender(monkeypatch):
    monkeypatch.setattr(main, "SELF_REVIEW", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    job = _seed_job()
    _run(main._self_review_edl("srev", is_rerender=True))
    assert "self_review" not in job
    main._clip_jobs.pop("srev", None)


def test_self_review_high_score_no_revision(monkeypatch):
    monkeypatch.setattr(main, "SELF_REVIEW", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")

    async def fake_submit(url, edl, fmt, style, preview=False):
        return {"render_id": "r", "bucket_name": "b"}
    async def fake_poll(rid, bkt):
        return "https://cdn/preview.mp4"
    async def fake_frames(url, n=6):
        return [b"jpeg1", b"jpeg2"]
    async def fake_score(frames, plan):
        return {"score_0_100": 88, "issues": []}
    monkeypatch.setattr(main, "_submit_remotion_render", fake_submit)
    monkeypatch.setattr(main, "_poll_remotion_render", fake_poll)
    monkeypatch.setattr(main, "_sample_render_frames", fake_frames)
    monkeypatch.setattr(main, "_score_edl_vision", fake_score)

    job = _seed_job()
    before = job["edl"]
    _run(main._self_review_edl("srev"))
    assert job["self_review"]["score"] == 88
    assert job["edl"] is before          # no revision when >= threshold
    main._clip_jobs.pop("srev", None)


def test_self_review_low_score_applies_fix_ops(monkeypatch):
    monkeypatch.setattr(main, "SELF_REVIEW", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    monkeypatch.setattr(main, "SELF_REVIEW_THRESHOLD", 70)

    async def fake_submit(url, edl, fmt, style, preview=False):
        return {"render_id": "r", "bucket_name": "b"}
    async def fake_poll(rid, bkt):
        return "https://cdn/preview.mp4"
    async def fake_frames(url, n=6):
        return [b"jpeg1", b"jpeg2"]
    async def fake_score(frames, plan):
        return {"score_0_100": 55, "issues": [
            {"code": "caption_style", "frame": 0,
             "fix_op": {"type": "set_caption_style", "style": "karaoke"}}]}
    monkeypatch.setattr(main, "_submit_remotion_render", fake_submit)
    monkeypatch.setattr(main, "_poll_remotion_render", fake_poll)
    monkeypatch.setattr(main, "_sample_render_frames", fake_frames)
    monkeypatch.setattr(main, "_score_edl_vision", fake_score)

    job = _seed_job()
    _run(main._self_review_edl("srev"))
    assert job["self_review"]["score"] == 55
    assert job["edl"]["caption_style"] == "karaoke"   # fix op applied
    assert job["self_review"].get("applied")
    main._clip_jobs.pop("srev", None)


def test_self_review_ignores_non_tweak_ops(monkeypatch):
    monkeypatch.setattr(main, "SELF_REVIEW", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")

    async def fake_submit(url, edl, fmt, style, preview=False):
        return {"render_id": "r", "bucket_name": "b"}
    async def fake_poll(rid, bkt):
        return "https://cdn/preview.mp4"
    async def fake_frames(url, n=6):
        return [b"jpeg"]
    async def fake_score(frames, plan):
        return {"score_0_100": 40, "issues": [
            {"code": "x", "frame": 0, "fix_op": {"type": "definitely_not_a_real_op"}}]}
    monkeypatch.setattr(main, "_submit_remotion_render", fake_submit)
    monkeypatch.setattr(main, "_poll_remotion_render", fake_poll)
    monkeypatch.setattr(main, "_sample_render_frames", fake_frames)
    monkeypatch.setattr(main, "_score_edl_vision", fake_score)

    job = _seed_job()
    before = dict(job["edl"])
    _run(main._self_review_edl("srev"))
    assert job["edl"] == before   # unknown op ignored, EDL unchanged
    main._clip_jobs.pop("srev", None)


def test_self_review_no_frames_failsoft(monkeypatch):
    monkeypatch.setattr(main, "SELF_REVIEW", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")

    async def fake_submit(url, edl, fmt, style, preview=False):
        return {"render_id": "r", "bucket_name": "b"}
    async def fake_poll(rid, bkt):
        return "https://cdn/preview.mp4"
    async def no_frames(url, n=6):
        return []
    monkeypatch.setattr(main, "_submit_remotion_render", fake_submit)
    monkeypatch.setattr(main, "_poll_remotion_render", fake_poll)
    monkeypatch.setattr(main, "_sample_render_frames", no_frames)

    job = _seed_job()
    _run(main._self_review_edl("srev"))
    assert "self_review" not in job   # bailed before scoring, EDL untouched
    main._clip_jobs.pop("srev", None)
