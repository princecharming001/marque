"""UX-A1 + UX-A2: reel edit-format classification + honest /v1/reels/examples."""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import main
from app import dossier as D
from main import app

client = TestClient(app)


def _run(coro):
    return asyncio.run(coro)


# --- UX-A1 tier-1 heuristic ------------------------------------------------------

def test_classify_recap_music_short_musicish():
    fmt, style = main._classify_edit_format(
        {"caption": "30 days of progress #montage #fyp", "duration_s": 14, "transcript": ""})
    assert (fmt, style) == ("recap_music", "fast_cuts")


def test_classify_voiceover_from_bare_caption_with_narration():
    fmt, style = main._classify_edit_format(
        {"caption": "🔥", "duration_s": 30,
         "transcript": " ".join(["word"] * 40)})
    assert (fmt, style) == ("recap_voiceover", "faceless")


def test_classify_broll_from_visual_nouns():
    fmt, style = main._classify_edit_format(
        {"caption": "How I organize my desk setup for deep work", "duration_s": 45,
         "transcript": " ".join(["word"] * 60)})
    assert (fmt, style) == ("talking_head_broll", "broll_cutaway")


def test_classify_default_talking_head():
    fmt, style = main._classify_edit_format(
        {"caption": "My honest opinion on this trend", "duration_s": 40,
         "transcript": " ".join(["word"] * 60)})
    assert (fmt, style) == ("talking_head", "talking_head")


def test_reel_from_post_gains_additive_keys():
    r = main._reel_from_post({"caption": "quick montage #edit", "duration_s": 12,
                              "views": 50_000}, "handle", "instagram", 0, False)
    assert r["edit_format"] == "recap_music"
    assert r["fmt_source"] == "heuristic"
    assert r["why_match"]


def test_merge_prev_carries_dossier_classification():
    post = {"caption": "quick montage #edit", "duration_s": 12, "views": 1, "platform": "instagram"}
    rid = main._reel_public_id(post, "h", "instagram", 0)
    prev = [{"id": rid, "fmt_source": "dossier", "edit_format": "talking_head_broll",
             "why_match": "Watched it: cutaways."}]
    main._merge_prev_reel_work([post], prev, handle="h")
    assert post["fmt_source"] == "dossier"
    assert post["edit_format"] == "talking_head_broll"


def test_dossier_classify_fail_soft_when_off(monkeypatch):
    monkeypatch.setattr(D, "VIDEO_UNDERSTANDING", "off")
    posts = [{"video_url": "https://x/v.mp4", "duration_s": 10}]
    _run(main._dossier_classify_reels(posts))
    assert "fmt_source" not in posts[0]            # untouched


def test_dossier_classify_sets_fields(monkeypatch):
    monkeypatch.setattr(D, "VIDEO_UNDERSTANDING", "claude_frames")
    D._reference_dossier_cache.clear()

    async def fake_gen(url, dur, provider=None):
        d = D.mock_dossier(dur or 10000)
        d["framing"]["eye_contact"] = False        # no face + no narration → recap_music
        return d
    monkeypatch.setattr(D, "generate_dossier", fake_gen)
    posts = [{"video_url": "https://x/v.mp4", "duration_s": 10, "transcript": ""}]
    _run(main._dossier_classify_reels(posts))
    assert posts[0]["fmt_source"] == "dossier"
    assert posts[0]["edit_format"] == "recap_music"


# --- UX-A2 endpoint ---------------------------------------------------------------

def _seed_niche_cache(reels: list[dict], niche: str = "fitness"):
    key = main._niche_cache_key(niche)
    main._niche_reels_cache[key] = {"reels": reels, "ts": time.time()}
    return key


def _mk_reel(i: int, **kw) -> dict:
    base = {"id": f"r{i}", "creator_handle": f"c{i}", "platform": "instagram",
            "title": f"Reel {i}", "hook_text": "h", "transcript": "t", "transcribed": False,
            "thumbnail_url": "https://cdn/t.jpg", "video_url": "https://cdn/v.mp4",
            "views": 100_000, "likes": 1_000, "why_trending": "w",
            "format_id": "pov-story", "style": "talking_head", "from_watched": False,
            "edit_format": "talking_head", "fmt_source": "heuristic", "why_match": "m"}
    base.update(kw)
    return base


def test_keyless_examples_are_flagged_sample():
    main._niche_reels_cache.clear()
    body = client.get("/v1/reels/examples?format=recap_music&niche=cooking").json()
    assert body["mode"] == "mock"
    assert body["reels"] and all(r["sample"] is True for r in body["reels"])
    assert all(r["selection_reason"] for r in body["reels"])


def test_live_match_never_pads_with_fabricated():
    _seed_niche_cache([_mk_reel(1, edit_format="recap_music", style="fast_cuts")])
    body = client.get("/v1/reels/examples?format=recap_music&niche=fitness").json()
    assert body["mode"] == "live"
    assert len(body["reels"]) == 1                 # fewer real > fake — NO padding
    r = body["reels"][0]
    assert r["sample"] is False and r["selection_reason"]
    main._niche_reels_cache.clear()


def test_ranking_prefers_rehosted_and_dossier(monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_URL", "https://sb.example.com")
    _seed_niche_cache([
        _mk_reel(1, edit_format="recap_music", views=500_000,
                 video_url="https://cdn-expiring/v.mp4"),
        _mk_reel(2, edit_format="recap_music", views=400_000,
                 video_url="https://sb.example.com/storage/v.mp4"),     # rehosted wins tie-ish
        _mk_reel(3, edit_format="recap_music", fmt_source="dossier", views=50_000,
                 video_url="https://cdn/v3.mp4"),                        # dossier tier leads all
    ])
    body = client.get("/v1/reels/examples?format=recap_music&niche=fitness").json()
    ids = [r["id"] for r in body["reels"]]
    assert ids[0] == "r3"                          # dossier classification outranks views
    assert ids.index("r2") < ids.index("r1")       # rehosted durable URL beats raw engagement
    main._niche_reels_cache.clear()


def test_unplayable_cards_never_lead():
    _seed_niche_cache([
        _mk_reel(1, edit_format="recap_music", views=9_000_000, video_url=""),
        _mk_reel(2, edit_format="recap_music", views=10_000),
    ])
    body = client.get("/v1/reels/examples?format=recap_music&niche=fitness").json()
    assert body["reels"][0]["id"] == "r2"          # playable takes the top slot
    main._niche_reels_cache.clear()
