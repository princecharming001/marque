import json

from fastapi.testclient import TestClient

import main
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


def test_tts_provider_selection(monkeypatch):
    # Keyless → mock; Cartesia wins on cost when both keys present; explicit
    # TTS_PROVIDER always overrides the auto-pick.
    monkeypatch.setattr(main, "TTS_PROVIDER", "")
    monkeypatch.setattr(main, "CARTESIA_KEY", "")
    monkeypatch.setattr(main, "ELEVENLABS_KEY", "")
    assert main._tts_provider() == "mock"
    monkeypatch.setattr(main, "ELEVENLABS_KEY", "el-key")
    assert main._tts_provider() == "elevenlabs"
    monkeypatch.setattr(main, "CARTESIA_KEY", "ca-key")
    assert main._tts_provider() == "cartesia"
    monkeypatch.setattr(main, "TTS_PROVIDER", "elevenlabs")
    assert main._tts_provider() == "elevenlabs"


# ---------------------------------------------------------------------------
# V3: feed / reels / mimic / analyze-video / summaries / edit prefs
# ---------------------------------------------------------------------------

def test_reels_niche_and_pagination():
    b = client.get("/v1/reels", params={"niche": "sourdough baking", "cursor": 0}).json()
    assert b["reels"] and len(b["reels"]) <= 6
    assert all({"id", "creator_handle", "hook_text", "transcript", "why_trending", "format_id"} <= set(r)
               for r in b["reels"])
    assert "sourdough" in json.dumps(b["reels"]).lower()
    # paginate to exhaustion
    cursor, pages = b["next_cursor"], 1
    while cursor is not None and pages < 10:
        b = client.get("/v1/reels", params={"niche": "sourdough baking", "cursor": cursor}).json()
        cursor = b["next_cursor"]
        pages += 1
    assert cursor is None


def test_reels_watched_creators_first():
    b = client.get("/v1/reels", params={"niche": "fitness", "watched": "@bigcoach,@lift_lisa"}).json()
    handles = [r["creator_handle"] for r in b["reels"]]
    assert "bigcoach" in handles[:4]
    assert b["reels"][0]["from_watched"] is True


def test_feed_mixed_items():
    b = client.get("/v1/feed", params={"niche": "fitness", "styles": "talking_head,faceless", "cursor": 0}).json()
    types = [i["type"] for i in b["items"]]
    assert "script" in types and "reel" in types and "trend" in types
    # scripts respect the styles filter
    for i in b["items"]:
        if i["type"] == "script":
            assert i["script"]["style"] in ("talking_head", "faceless")
    assert b["next_cursor"] == 1


def test_mimic_returns_script_with_provenance():
    reel = client.get("/v1/reels", params={"niche": "fitness"}).json()["reels"][0]
    b = client.post("/v1/mimic", json={"reel": reel, "brand": {"niche": "personal finance"}}).json()
    s = b["script"]
    assert {"hook", "body", "cta", "formatId", "style"} <= set(s)
    assert "finance" in json.dumps(s).lower()
    assert b["mimicked_from"]["creator_handle"] == reel["creator_handle"]


def test_analyze_video_link():
    b = client.post("/v1/analyze-video", json={
        "url": "https://www.tiktok.com/@someone/video/123", "brand": {"niche": "fitness"}}).json()
    assert b["platform"] == "tiktok"
    assert b["hook_analysis"] and b["why_it_works"]
    assert len(b["structure_beats"]) >= 3
    assert {"hook", "body"} <= set(b["your_version"])


def test_brand_summary():
    b = client.post("/v1/brand-summary", json={
        "brand": {"niche": "fitness coaching", "audience": "busy dads", "known_for": "no-nonsense plans"},
        "memory": {"angle": "Tough love for busy dads"}}).json()
    assert b["summary"] and "fitness" in b["summary"].lower()
    assert len(b["traits"]) >= 3
    assert "tough love" in b["working_on"].lower()


def test_performance_summary_mock_series():
    b = client.get("/v1/performance/summary", params={"creator_id": "fresh-creator", "days": 30}).json()
    assert b["days"] == 30
    assert len(b["daily"]) == 30
    assert {"instagram", "tiktok"} <= set(b["platforms"])
    assert b["totals"]["views"] > 0
    # deterministic: same creator seeds the same series
    b2 = client.get("/v1/performance/summary", params={"creator_id": "fresh-creator", "days": 30}).json()
    assert b2["totals"]["views"] == b["totals"]["views"]


def test_performance_summary_real_aggregation():
    client.post("/v1/posts/register", json={"post_id": "perf-1", "creator_id": "perf-tester",
                                            "platform": "tiktok", "format_id": "listicle"})
    client.post("/v1/metrics/ingest", json={"post_id": "perf-1", "creator_id": "perf-tester",
                                            "views": 9000, "likes": 700, "reach": 8000,
                                            "avg_watch_pct": 0.6, "follows_gained": 45})
    b = client.get("/v1/performance/summary", params={"creator_id": "perf-tester"}).json()
    assert b["mode"] == "live"
    assert b["totals"]["views"] == 9000
    assert b["platforms"]["tiktok"]["posts"] == 1
    assert b["best_post"]["views"] == 9000


def test_edit_prefs_threading():
    r = client.post("/v1/clips", json={
        "source_url": "https://example.com/f.mov", "formats": ["myth-buster"], "style": "talking_head",
        "script": {"hook": "Stop doing this", "body": "Here is why.", "cta": "Follow.", "formatId": "myth-buster"},
        "edit_prefs": {"auto_captions": False, "filler_trim": "off", "caption_style": "karaoke"},
    })
    job_id = r.json()["job_id"]
    job = client.get(f"/v1/clips/{job_id}").json()
    edl = job["edl"]
    assert edl["captions"] == []          # captions off honored
    assert edl["drops"] == []             # filler trim off honored


def test_edit_prefs_defaults_preserved():
    r = client.post("/v1/clips", json={
        "source_url": "https://example.com/f.mov", "formats": ["myth-buster"], "style": "talking_head",
        "script": {"hook": "Stop doing this now friends", "body": "Here is why.", "cta": "Follow.",
                   "formatId": "myth-buster"},
    })
    edl = client.get(f"/v1/clips/{r.json()['job_id']}").json()["edl"]
    assert edl["captions"]                # defaults keep captions
    assert edl["drops"]                   # defaults keep filler trimming


def test_trends_richer():
    t = client.get("/v1/trends", params={"niche": "fitness"}).json()["trends"]
    assert len(t) >= 5


# ---------------------------------------------------------------------------
# Render plan — source→output coordinate remap after cutting (app.edl)
# ---------------------------------------------------------------------------
from app.edl import build_render_plan


def test_render_plan_no_cuts_is_identity():
    # One full segment, no drops → clips unchanged, captions keep their frames.
    edl = {
        "style": "talking_head", "format_id": "x",
        "segments": [{"src_in": 0, "src_out": 300}], "drops": [],
        "captions": [{"word": "a", "frame": 0}, {"word": "b", "frame": 150}],
        "overlays": [], "broll": [], "layout": {"style": "talking_head"},
    }
    p = build_render_plan(edl)
    assert p["clips"] == [{"src_in": 0, "src_out": 300}]
    assert p["total_frames"] == 300
    assert [c["frame"] for c in p["captions"]] == [0, 150]


def test_render_plan_drop_shifts_later_captions():
    # Drop source frames [100,150) (50 frames). Captions after the drop shift back 50;
    # a caption INSIDE the drop is removed entirely.
    edl = {
        "style": "talking_head", "format_id": "x",
        "segments": [{"src_in": 0, "src_out": 300}],
        "drops": [{"src_in": 100, "src_out": 150, "reason": "filler"}],
        "captions": [
            {"word": "before", "frame": 50},   # kept, stays at 50
            {"word": "inside", "frame": 120},   # dropped (falls in cut)
            {"word": "after", "frame": 200},    # kept, shifts to 200-50=150
        ],
        "overlays": [], "broll": [], "layout": {"style": "talking_head"},
    }
    p = build_render_plan(edl)
    assert p["total_frames"] == 250   # 300 - 50
    words = {c["word"]: c["frame"] for c in p["captions"]}
    assert words == {"before": 50, "after": 150}
    # clips reflect the two kept intervals around the drop
    assert p["clips"] == [{"src_in": 0, "src_out": 100}, {"src_in": 150, "src_out": 300}]


def test_render_plan_overlay_remapped_and_clamped():
    # Punch-in overlay [60,120) straddles a drop [80,100); output span covers the
    # surviving pieces mapped into output coords.
    edl = {
        "style": "talking_head", "format_id": "x",
        "segments": [{"src_in": 0, "src_out": 200}],
        "drops": [{"src_in": 80, "src_out": 100, "reason": "dead_air"}],
        "captions": [],
        "overlays": [{"type": "punch_in", "src_in": 60, "src_out": 120, "scale": 1.1, "text": ""}],
        "broll": [], "layout": {"style": "talking_head"},
    }
    p = build_render_plan(edl)
    o = p["overlays"][0]
    # 60 maps to 60 (before drop); 120 maps to 120-20=100 (after 20-frame drop)
    assert o["frame_in"] == 60 and o["frame_out"] == 100
    assert o["scale"] == 1.1


def test_render_plan_caption_style_flows_through():
    edl = {
        "style": "talking_head", "format_id": "x",
        "segments": [{"src_in": 0, "src_out": 100}], "drops": [],
        "captions": [], "overlays": [], "broll": [],
        "layout": {"style": "talking_head"}, "caption_style": "karaoke",
    }
    assert build_render_plan(edl)["caption_style"] == "karaoke"


def test_render_plan_all_cut_stays_valid():
    # Degenerate: everything dropped. total_frames clamps to >=1 so Remotion accepts it.
    edl = {
        "style": "talking_head", "format_id": "x",
        "segments": [{"src_in": 0, "src_out": 50}],
        "drops": [{"src_in": 0, "src_out": 50, "reason": "filler"}],
        "captions": [{"word": "gone", "frame": 10}],
        "overlays": [], "broll": [], "layout": {"style": "talking_head"},
    }
    p = build_render_plan(edl)
    assert p["clips"] == []
    assert p["total_frames"] == 1
    assert p["captions"] == []


# ---------------------------------------------------------------------------
# New format pipeline steps: b-roll resolve, react-source attach, plan fields
# ---------------------------------------------------------------------------
import asyncio


def test_resolve_broll_noop_without_key(monkeypatch):
    monkeypatch.setattr(main, "PEXELS_KEY", "")
    edl = {"broll": [{"src_in": 0, "src_out": 60, "broll_query": "gym", "source": "stock"}]}
    out = asyncio.run(main._resolve_broll(edl))
    assert out["broll"][0].get("resolved_url") is None


def test_resolve_broll_caches_and_skips(monkeypatch):
    calls = []

    async def fake_fetch(q):
        calls.append(q)
        return f"https://cdn/{q}.mp4"

    monkeypatch.setattr(main, "PEXELS_KEY", "k")
    monkeypatch.setattr(main, "_fetch_pexels", fake_fetch)
    main._broll_url_cache.clear()
    edl = {"broll": [
        {"src_in": 0, "src_out": 60, "broll_query": "barbell", "source": "stock"},
        {"src_in": 60, "src_out": 120, "broll_query": "barbell", "source": "stock"},   # same query → cache hit
        {"src_in": 120, "src_out": 180, "broll_query": "mine", "source": "own_media"},  # skipped
        {"src_in": 180, "src_out": 240, "resolved_url": "https://x.mp4", "source": "stock"},  # already resolved → skipped
    ]}
    out = asyncio.run(main._resolve_broll(edl))
    assert calls == ["barbell"]   # fetched once, second was cached
    assert out["broll"][0]["resolved_url"] == "https://cdn/barbell.mp4"
    assert out["broll"][1]["resolved_url"] == "https://cdn/barbell.mp4"
    assert out["broll"][2].get("resolved_url") is None   # own_media untouched
    assert out["broll"][3]["resolved_url"] == "https://x.mp4"   # preserved


def test_attach_react_source_only_for_duet():
    # Non-duet style → no react_source attached.
    edl = main._attach_react_source({}, {"style": "talking_head", "react_source_url": "https://v.mp4"})
    assert edl.get("react_source") is None
    # Empty url → no-op even for duet.
    edl = main._attach_react_source({}, {"style": "duet_split", "react_source_url": ""})
    assert edl.get("react_source") is None
    # Video url.
    edl = main._attach_react_source({}, {"style": "duet_split", "react_source_url": "https://v.mp4", "react_credit_label": "@x"})
    assert edl["react_source"]["kind"] == "video"
    assert edl["react_source"]["credit_label"] == "@x"
    # Image url (even with a query string) → kind image.
    edl = main._attach_react_source({}, {"style": "duet_split", "react_source_url": "https://s.png?token=abc"})
    assert edl["react_source"]["kind"] == "image"


def test_render_plan_carries_broll_and_react_fields():
    edl = {
        "style": "duet_split", "format_id": "green-screen",
        "segments": [{"src_in": 0, "src_out": 300}], "drops": [],
        "captions": [], "overlays": [],
        "broll": [{"src_in": 60, "src_out": 120, "cue_text": "x", "broll_query": "x",
                   "source": "stock", "resolved_url": "https://b.mp4"}],
        "react_schedule": [{"state": "play", "src_in": 0, "src_out": 55, "clip_from": 0, "audio_gain": 1.0}],
        "react_source": {"resolved_url": "https://r.mp4", "kind": "video", "credit_label": "@s"},
        "layout": {"style": "duet_split", "panels": 2, "split_fraction": 0.58},
    }
    p = build_render_plan(edl)
    assert p["broll"][0]["resolved_url"] == "https://b.mp4"
    assert p["broll"][0]["source"] == "stock"
    assert p["broll"][0]["frame_in"] == 60 and p["broll"][0]["frame_out"] == 120
    assert p["react_source"]["resolved_url"] == "https://r.mp4"
    assert p["react_schedule"][0]["frame_in"] == 0 and p["react_schedule"][0]["frame_out"] == 55
    assert p["layout"]["split_fraction"] == 0.58


def test_render_plan_drops_react_window_straddling_a_cut():
    # A drop inside a play window would shrink its output length while clip_from stays
    # fixed → the window is dropped rather than desyncing the source.
    edl = {
        "style": "duet_split", "format_id": "green-screen",
        "segments": [{"src_in": 0, "src_out": 300}],
        "drops": [{"src_in": 80, "src_out": 100, "reason": "filler"}],
        "captions": [], "overlays": [], "broll": [],
        "react_schedule": [
            {"state": "play", "src_in": 0, "src_out": 55, "clip_from": 0, "audio_gain": 1.0},   # clean, kept
            {"state": "freeze", "src_in": 70, "src_out": 130, "clip_from": 55, "audio_gain": 0.15},  # straddles the 80-100 drop → dropped
        ],
        "layout": {"style": "duet_split", "panels": 2},
    }
    p = build_render_plan(edl)
    assert len(p["react_schedule"]) == 1
    assert p["react_schedule"][0]["state"] == "play"


# ---------------------------------------------------------------------------
# Inference-time quality gate: generate -> judge -> targeted self-repair
# ---------------------------------------------------------------------------

def _script(hook="Old weak hook", alts=None, fmt="talking-point"):
    return {"title": "T", "summary": "s", "hook": hook, "hookSignal": "curiosity",
            "formatId": fmt, "body": "b", "cta": "Follow.", "shotPlan": [],
            "targetSeconds": 30, "predictedScore": 99,
            "altHooks": alts or [], "style": "talking_head"}


def _judge_fake(verdicts):
    """Return a fake `anthropic` that answers the script judge with `verdicts`
    and any revise call with a canned rewritten array."""
    async def fake(system, user, model=main.OPUS, max_tokens=3000):
        if "harshest short-form editor" in system:
            return json.dumps(verdicts)
        if "senior script editor rewriting" in system:
            return json.dumps([_script(hook="Rewritten sharp hook: $3,180 in 42 days")])
        return "[]"
    return fake


def test_blend_score_weights_and_slop_penalty():
    hi = main._blend_score({"hook_strength": 100, "specificity": 100,
                            "format_fit": 100, "voice_match": 100})
    assert hi == 100
    penalized = main._blend_score({"hook_strength": 80, "specificity": 80,
                                   "format_fit": 80, "voice_match": 80, "slop": True})
    assert penalized == 68   # 80 - 12
    assert main._blend_score({}) == 0


def test_quality_scripts_swaps_best_hook_and_grounds_score(monkeypatch):
    monkeypatch.setattr(main, "AI_QUALITY", True)
    verdicts = [{"index": 0, "hook_strength": 90, "specificity": 80, "format_fit": 80,
                 "voice_match": 80, "slop": False, "best_hook": 1, "verdict": "keep"}]
    monkeypatch.setattr(main, "anthropic", _judge_fake(verdicts))
    scr = [_script(alts=[{"text": "Stronger alt hook", "signal": "contrarian", "strength": 95}])]
    out = asyncio.run(main.quality_scripts({}, "talking_head", scr))
    assert out[0]["hook"] == "Stronger alt hook"        # best_hook=1 swapped in
    assert out[0]["hookSignal"] == "contrarian"
    assert out[0]["predictedScore"] == 85               # grounded on critic, not the 99 guess


def test_quality_scripts_revises_flagged(monkeypatch):
    monkeypatch.setattr(main, "AI_QUALITY", True)
    verdicts = [{"index": 0, "hook_strength": 40, "specificity": 30, "format_fit": 50,
                 "voice_match": 60, "slop": True, "best_hook": 0, "verdict": "revise",
                 "weakest": "specificity", "note": "add a number"}]
    monkeypatch.setattr(main, "anthropic", _judge_fake(verdicts))
    out = asyncio.run(main.quality_scripts({}, "talking_head", [_script()]))
    assert "Rewritten sharp hook" in out[0]["hook"]     # flagged script was rewritten
    assert out[0]["style"] == "talking_head"


def test_quality_scripts_fallback_on_empty_judge(monkeypatch):
    monkeypatch.setattr(main, "AI_QUALITY", True)

    async def empty(system, user, model=main.OPUS, max_tokens=3000):
        return "[]"
    monkeypatch.setattr(main, "anthropic", empty)
    scr = [_script(hook="unchanged")]
    out = asyncio.run(main.quality_scripts({}, "talking_head", scr))
    assert out[0]["hook"] == "unchanged"                # no verdicts → untouched


def test_quality_scripts_disabled_is_passthrough(monkeypatch):
    monkeypatch.setattr(main, "AI_QUALITY", False)
    called = []

    async def boom(*a, **k):
        called.append(1)
        return "[]"
    monkeypatch.setattr(main, "anthropic", boom)
    scr = [_script(hook="raw")]
    out = asyncio.run(main.quality_scripts({}, "talking_head", scr))
    assert out[0]["hook"] == "raw"
    assert not called                                   # gate off → no LLM call


def test_quality_hooks_drops_slop_and_reranks(monkeypatch):
    monkeypatch.setattr(main, "AI_QUALITY", True)
    hooks = [
        {"text": "In this video I'll show you", "signal": "curiosity", "strength": 70},  # slop
        {"text": "Everyone does this backwards", "signal": "contrarian", "strength": 60},
        {"text": "$3,180 in 42 days — here's how", "signal": "authority", "strength": 55},
    ]

    async def fake(system, user, model=main.OPUS, max_tokens=3000):
        assert "ruthless short-form hook critic" in system
        return json.dumps([
            {"index": 0, "strength": 20, "slop": True},
            {"index": 1, "strength": 78, "slop": False},
            {"index": 2, "strength": 92, "slop": False},
        ])
    monkeypatch.setattr(main, "anthropic", fake)
    out = asyncio.run(main.quality_hooks("money", hooks))
    assert [h["text"] for h in out] == [
        "$3,180 in 42 days — here's how",               # 92, ranked first
        "Everyone does this backwards",                 # 78
    ]                                                   # slop hook dropped
