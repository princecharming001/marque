import json
import time

from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)


class SupabaseClientStub:
    """Truthy stand-in for the Supabase client; tests attach AsyncMocks per method."""
    async def upsert_arm_stat(self, *a, **k): return True
    async def load_arm_stats(self, *a, **k): return {}
    async def upsert_post(self, *a, **k): return True
    async def load_post(self, *a, **k): return None
    async def load_all_posts(self, *a, **k): return []


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


def test_mock_script_titles_are_sentence_cased():
    """Regression: mock_scripts built title from f'the {niche} mistake #N', leaking a
    lowercase 'the' into a title-display context (e.g. Film queue cards)."""
    r = client.post("/v1/scripts", json={"niche": "fitness", "style": "talking_head",
                                         "count": 2, "pillar": "Myth-busting"})
    titles = [s["title"] for s in r.json()["scripts"]]
    assert titles, "expected mock scripts"
    for t in titles:
        assert t[0] == t[0].upper(), f"title not sentence-cased: {t!r}"


def test_scripts_and_hooks_accept_memory_field():
    """Endpoints accept the client-held memory dict without breaking (backward-compat
    for old clients that omit it, forward path for new ones that send it)."""
    memory = {"angle": "Accessible fitness for busy parents",
              "facts": ["ex-personal-trainer"], "ideas": ["time-efficient home workouts"]}
    r = client.post("/v1/scripts", json={"niche": "fitness", "style": "talking_head",
                                         "count": 2, "pillar": "Myth-busting", "memory": memory})
    assert r.status_code == 200 and len(r.json()["scripts"]) == 2
    r = client.post("/v1/hooks", json={"niche": "fitness", "topic": "squats", "memory": memory})
    assert r.status_code == 200


def test_scripts_and_hooks_send_catchphrases():
    """Catchphrases on the brand flow through to generation without error (item 6)."""
    r = client.post("/v1/scripts", json={"niche": "fitness", "style": "talking_head",
                                         "count": 1, "pillar": "Hot takes",
                                         "catchphrases": ["let's get after it"]})
    assert r.status_code == 200


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
# AI editor (EDL) upgrade: disfluency-grounded cuts, emphasis punch-ins, verify gate
# ---------------------------------------------------------------------------

def test_strip_fillers_prefers_disfluency_type():
    from app.edl import strip_fillers
    words = [
        {"word": "So", "start_ms": 0, "end_ms": 200, "type": "filler"},       # tagged filler
        {"word": "muscles", "start_ms": 200, "end_ms": 600, "type": None},    # real word (not lexicon)
        {"word": "grow", "start_ms": 600, "end_ms": 900},                     # real, no type key
    ]
    kept, drops = strip_fillers(words)
    assert [w["word"] for w in kept] == ["muscles", "grow"]
    assert any(d.reason == "filler" for d in drops)                          # "So" dropped via type


def test_strip_fillers_no_overlap_with_fillers_before_gap():
    """Regression: a run of fillers before a dead-air gap must not produce a dead_air
    drop that overlaps the filler drops (prev_end must advance past fillers too)."""
    from app.edl import strip_fillers
    words = [
        {"word": "hello", "start_ms": 0, "end_ms": 500},
        {"word": "um", "start_ms": 500, "end_ms": 550, "type": "filler"},
        {"word": "uh", "start_ms": 550, "end_ms": 600, "type": "filler"},
        {"word": "world", "start_ms": 1000, "end_ms": 1200},
    ]
    _, drops = strip_fillers(words, gap_ms=300)
    spans = sorted((d.src_in, d.src_out) for d in drops)
    # No two drops may overlap.
    for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
        assert b1 <= a2, f"overlapping drops: {(a1, b1)} and {(a2, b2)}"
    # The dead-air drop must start at the last filler's end (frame 18), not frame 15.
    dead = [(d.src_in, d.src_out) for d in drops if d.reason == "dead_air"]
    assert dead == [(18, 30)]


def test_strip_fillers_text_fallback_without_type():
    from app.edl import strip_fillers
    words = [{"word": "um", "start_ms": 0, "end_ms": 100},                    # lexicon fallback
             {"word": "hello", "start_ms": 100, "end_ms": 400}]
    kept, drops = strip_fillers(words)
    assert [w["word"] for w in kept] == ["hello"]
    assert any(d.reason == "filler" for d in drops)


def test_normalize_words_maps_assemblyai_shape():
    raw = [{"text": "hey", "start": 100, "end": 400, "confidence": 0.9,
            "type": "filler", "is_emphasized": True}]
    out = main._normalize_words(raw)
    assert out[0] == {"word": "hey", "start_ms": 100, "end_ms": 400,
                      "confidence": 0.9, "type": "filler", "is_emphasized": True}


def test_extract_emphasis_regions_merges_words_and_highlights():
    words = [{"start_ms": 0, "end_ms": 300, "is_emphasized": True},           # frames 0..9
             {"start_ms": 1000, "end_ms": 1100, "is_emphasized": False}]
    highlights = [{"text": "key phrase", "timestamps": [{"start": 200, "end": 500}]}]  # 6..15 overlaps
    spans = main._extract_emphasis_regions(words, highlights)
    assert spans == [(0, 15)]                                                 # merged overlap


def test_merge_drops_skips_overlaps():
    existing = [{"src_in": 100, "src_out": 150, "reason": "dead_air"}]
    new = [{"src_in": 140, "src_out": 160, "reason": "filler"},               # overlaps → skip
           {"src_in": 200, "src_out": 210, "reason": "filler"}]               # clean → add
    out = main._merge_drops(existing, new)
    assert len(out) == 2
    assert out[-1]["src_in"] == 200


def test_edl_prompt_injects_grounded_spans():
    import prompts
    _, user = prompts.edl_prompt(
        "talking_head", [{"word": "hi", "start_ms": 0, "end_ms": 100}],
        {"hook": "h", "body": "b", "cta": "c", "formatId": "myth-buster"}, {},
        disfluency_spans=[(3, 6)], emphasis_spans=[(20, 40)])
    assert "[3-6]" in user and "FILLER/DISFLUENCY" in user
    assert "[20-40]" in user and "HIGH-EMPHASIS" in user


def test_verify_and_repair_edl_pass(monkeypatch):
    monkeypatch.setattr(main, "AI_QUALITY", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
        assert "QA gate" in system
        return json.dumps({"verdict": "pass", "issues": [], "fix": ""})
    monkeypatch.setattr(main, "anthropic", fake)
    edl = {"style": "talking_head", "format_id": "myth-buster",
           "segments": [{"src_in": 0, "src_out": 300}], "drops": [], "captions": [],
           "overlays": [], "broll": [], "layout": {"style": "talking_head"}}
    out = asyncio.run(main.verify_and_repair_edl("talking_head", edl, [], {"formatId": "myth-buster"}))
    assert out == edl                                                         # pass → unchanged


def test_verify_and_repair_edl_repairs_on_violation(monkeypatch):
    monkeypatch.setattr(main, "AI_QUALITY", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    fixed = {"style": "talking_head", "format_id": "myth-buster",
             "segments": [{"src_in": 0, "src_out": 300}], "drops": [], "captions": [],
             "overlays": [], "broll": [], "layout": {"style": "talking_head"}}

    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
        if "QA gate" in system:
            return json.dumps({"verdict": "revise", "issues": ["segment src_out<=src_in"], "fix": "reorder"})
        return json.dumps(fixed)                                             # repair pass
    monkeypatch.setattr(main, "anthropic", fake)
    broken = {"style": "talking_head", "format_id": "myth-buster",
              "segments": [{"src_in": 0, "src_out": 300}], "drops": [], "captions": [],
              "overlays": [], "broll": [], "layout": {"style": "talking_head"}}
    out = asyncio.run(main.verify_and_repair_edl("talking_head", broken, [], {"formatId": "myth-buster"}))
    assert out["segments"] == [{"src_in": 0, "src_out": 300}]                 # repaired EDL applied


def test_verify_and_repair_edl_noop_keyless(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    edl = {"style": "talking_head", "segments": [{"src_in": 0, "src_out": 5}]}
    out = asyncio.run(main.verify_and_repair_edl("talking_head", edl, [], {}))
    assert out is edl                                                         # no LLM call keyless


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
    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
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
    main._arm_stats.pop("fresh_creator", None)          # no learning data → pure critic score
    verdicts = [{"index": 0, "hook_strength": 90, "specificity": 80, "format_fit": 80,
                 "voice_match": 80, "slop": False, "best_hook": 1, "verdict": "keep"}]
    monkeypatch.setattr(main, "anthropic", _judge_fake(verdicts))
    scr = [_script(alts=[{"text": "Stronger alt hook", "signal": "contrarian", "strength": 95}])]
    out = asyncio.run(main.quality_scripts({}, "talking_head", scr, creator_id="fresh_creator"))
    assert out[0]["hook"] == "Stronger alt hook"        # best_hook=1 swapped in
    assert out[0]["hookSignal"] == "contrarian"
    assert out[0]["predictedScore"] == 85               # grounded on critic, not the 99 guess


def test_calibration_signal_none_without_evidence():
    main._arm_stats.pop("nodata", None)
    cal, w = main._calibration_signal("nodata", {"style": "talking_head", "formatId": "x"})
    assert cal is None and w == 0.0


def test_final_score_pulls_toward_real_outcomes(monkeypatch):
    # Critic likes it (blend=85) but the creator's real posts in this style flop
    # (effect 0.2 → cal 20). With enough evidence the score is pulled down.
    main._arm_stats["proven"] = {
        "style:talking_head": {"n": 10, "effect": 0.2, "alpha": 3, "beta": 9},
    }
    v = {"hook_strength": 90, "specificity": 80, "format_fit": 80, "voice_match": 80}
    critic = main._blend_score(v)                       # 85
    final = main._final_score("proven", {"style": "talking_head"}, v)
    assert final < critic                               # calibration dragged it down
    cal, w = main._calibration_signal("proven", {"style": "talking_head"})
    assert cal == 20 and w == 0.4                       # 0.04 * 10, capped under 0.5
    main._arm_stats.pop("proven", None)


def test_quality_scripts_keeps_mandated_hook(monkeypatch):
    """Regression: the script-judge must NOT swap away a hook that best_hooks already
    vetted + mandated, even when it recommends an altHook (best_hook=1)."""
    monkeypatch.setattr(main, "AI_QUALITY", True)
    main._arm_stats.pop("c_mand", None)
    verdicts = [{"index": 0, "hook_strength": 88, "specificity": 80, "format_fit": 80,
                 "voice_match": 80, "slop": False, "best_hook": 1, "verdict": "keep"}]
    monkeypatch.setattr(main, "anthropic", _judge_fake(verdicts))
    scr = [_script(hook="Gold vetted hook",
                   alts=[{"text": "Silver unvetted hook", "signal": "curiosity", "strength": 99}])]
    out = asyncio.run(main.quality_scripts({}, "talking_head", scr, creator_id="c_mand",
                                           mandated_hooks=[{"text": "Gold vetted hook"}]))
    assert out[0]["hook"] == "Gold vetted hook"          # mandated hook preserved despite best_hook=1


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

    async def empty(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
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


def test_learning_loop_keyless_green(monkeypatch):
    """No SUPABASE client → learning loop is pure in-memory, exactly as before."""
    monkeypatch.setattr(main, "_supabase_client", None)
    main._post_registry.pop("p_keyless", None)
    main._arm_stats.pop("c_keyless", None)
    r = client.post("/v1/posts/register", json={
        "post_id": "p_keyless", "creator_id": "c_keyless", "platform": "instagram",
        "style": "talking_head", "format_id": "myth-buster", "hook_signal": "contrarian",
        "predicted_score": 75})
    assert r.json()["mode"] == "mock"
    r = client.post("/v1/metrics/ingest", json={
        "post_id": "p_keyless", "creator_id": "c_keyless", "reach": 100, "likes": 10,
        "comments": 2, "saves": 5, "shares": 1, "avg_watch_pct": 0.68, "follows_gained": 3})
    body = r.json()
    assert body["status"] == "ingested" and "outcome_y" in body
    assert main._arm_stats["c_keyless"]["style:talking_head"]["n"] >= 1


def test_update_arm_write_through(monkeypatch):
    from unittest.mock import AsyncMock
    fake = SupabaseClientStub()
    fake.upsert_arm_stat = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._arm_stats.pop("c_wt", None)
    asyncio.run(main._update_arm("c_wt", "style:talking_head", 0.72))
    fake.upsert_arm_stat.assert_awaited_once()
    cid, arm, stat = fake.upsert_arm_stat.await_args[0]
    assert cid == "c_wt" and arm == "style:talking_head" and stat["n"] == 1


def test_arms_lazy_load_from_supabase(monkeypatch):
    from unittest.mock import AsyncMock
    fake = SupabaseClientStub()
    fake.load_arm_stats = AsyncMock(return_value={
        "style:faceless": {"n": 12, "effect": 0.75, "confidence": "confirmed"}})
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._arm_stats.pop("c_lazy", None)                   # cache miss → must load
    arms = asyncio.run(main._arms_for_prompt("c_lazy"))
    fake.load_arm_stats.assert_awaited_once_with("c_lazy")
    assert main._arm_stats["c_lazy"]["style:faceless"]["n"] == 12
    assert arms and arms[0]["lift_pct"] == 50             # (0.75-0.5)*200


def test_supabase_client_disabled_when_no_key():
    from supabase_persistence import SupabaseClient
    assert SupabaseClient("", "").enabled is False
    assert SupabaseClient("https://x.supabase.co", "").enabled is False
    assert SupabaseClient("https://x.supabase.co", "k").enabled is True


def test_supabase_upsert_filters_unknown_columns(monkeypatch):
    """upsert_arm_stat must drop stray in-memory keys (lift_pct/label) before POST."""
    from unittest.mock import AsyncMock
    from supabase_persistence import SupabaseClient
    c = SupabaseClient("https://x.supabase.co", "k")
    captured = {}

    async def fake_request(method, path, *, params=None, json=None, headers=None):
        captured.update({"method": method, "path": path, "params": params, "json": json})
        class R: status_code = 201
        return R()
    c._request = fake_request
    ok = asyncio.run(c.upsert_arm_stat("c1", "style:x", {
        "n": 3, "sum_y": 1.2, "alpha": 2.2, "beta": 1.8, "effect": 0.6,
        "confidence": "insufficient", "lift_pct": 20, "label": "junk"}))
    assert ok
    assert captured["path"] == "/arm_stats"
    assert captured["params"] == {"on_conflict": "creator_id,arm_key"}
    assert "lift_pct" not in captured["json"] and "label" not in captured["json"]
    assert captured["json"]["creator_id"] == "c1" and captured["json"]["arm_key"] == "style:x"


def test_best_hooks_generates_pool_and_returns_top(monkeypatch):
    monkeypatch.setattr(main, "AI_QUALITY", True)
    monkeypatch.setattr(main, "BEST_OF_N_HOOKS", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
        if "hook engine" in system:                 # generation pass (temp 1.0)
            assert temperature == 1.0                # best-of-N must diversify
            return json.dumps([
                {"text": "In this video, three tips.", "signal": "curiosity", "strength": 70},  # slop
                {"text": "You're training abs wrong. Here's the fix.", "signal": "contrarian", "strength": 80},
                {"text": "$0 gym, 15 minutes, real results.", "signal": "specificity", "strength": 75},
            ])
        if "ruthless short-form hook critic" in system:   # judge pass
            return json.dumps([
                {"index": 0, "strength": 15, "slop": True},
                {"index": 1, "strength": 88, "slop": False},
                {"index": 2, "strength": 91, "slop": False},
            ])
        return "[]"
    monkeypatch.setattr(main, "anthropic", fake)
    out = asyncio.run(main.best_hooks({}, "abs training", "talking_head", "c1", n=2))
    assert [h["text"] for h in out] == [
        "$0 gym, 15 minutes, real results.",         # 91, ranked first
        "You're training abs wrong. Here's the fix.",  # 88
    ]                                                # slop hook dropped, top-2 returned


def test_best_hooks_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(main, "BEST_OF_N_HOOKS", False)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    out = asyncio.run(main.best_hooks({}, "t", "talking_head", "c1"))
    assert out == []


def test_scripts_prompt_injects_mandated_hooks_and_memory():
    import prompts
    _, user = prompts.scripts_prompt(
        {"niche": "fitness"}, {"name": "Myth-busting"}, "talking_head", 2,
        memory={"angle": "harder stances on training myths", "facts": ["posts at 6am"]},
        mandated_hooks=[{"text": "Your warmup is the workout.", "signal": "contrarian"}])
    assert "Your warmup is the workout." in user
    assert "MUST open" in user
    assert "harder stances on training myths" in user     # memory angle injected
    assert "posts at 6am" in user                          # memory fact injected


def test_hooks_prompt_injects_memory():
    import prompts
    _, user = prompts.hooks_prompt({"niche": "finance"}, "index funds", "faceless",
                                   memory={"facts": ["audience is beginners"]})
    assert "audience is beginners" in user


# ---------------------------------------------------------------------------
# Native Structured Outputs (item 2): typed JSON helper + schema legality
# ---------------------------------------------------------------------------

def test_anthropic_passes_output_config_when_schema_given(monkeypatch):
    captured = {}

    async def fake_post(self, url, headers=None, json=None):
        captured["body"] = json
        class R:
            status_code = 200
            def json(self_): return {"content": [{"text": "{\"ok\": true}"}]}
        return R()
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main.httpx.AsyncClient, "post", fake_post)
    schema = {"type": "object", "additionalProperties": False, "required": ["ok"],
              "properties": {"ok": {"type": "boolean"}}}
    asyncio.run(main.anthropic("s", "u", main.HAIKU, 100, schema=schema))
    assert captured["body"]["output_config"] == {"format": {"type": "json_schema", "schema": schema}}


def test_anthropic_json_unwraps_array(monkeypatch):
    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
        return json.dumps({"scripts": [{"hook": "a"}, {"hook": "b"}]})
    monkeypatch.setattr(main, "anthropic", fake)
    out = asyncio.run(main.anthropic_json("s", "u", {"type": "object"}, array_key="scripts"))
    assert [x["hook"] for x in out] == ["a", "b"]


def test_anthropic_json_object_and_fallback(monkeypatch):
    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
        return 'here you go: {"reply": "hi"}'          # prose-wrapped → extract_json fallback
    monkeypatch.setattr(main, "anthropic", fake)
    out = asyncio.run(main.anthropic_json("s", "u", {"type": "object"}))
    assert out == {"reply": "hi"}


def test_parse_intent_args_handles_json_string_and_dict():
    assert main._parse_intent_args({"intent_args_json": '{"topic": "abs", "count": 2}'}) == {"topic": "abs", "count": 2}
    assert main._parse_intent_args({"intent_args": {"topic": "x"}}) == {"topic": "x"}   # back-compat
    assert main._parse_intent_args({"intent_args_json": "not json"}) == {}              # malformed → {}
    assert main._parse_intent_args({}) == {}


def test_all_json_schemas_are_structured_output_legal():
    """Guard the documented SO restrictions: additionalProperties:false on every
    object, required == all properties, and NO numeric/length constraints."""
    import prompts

    def check(schema):
        assert isinstance(schema, dict)
        for banned in ("minimum", "maximum", "minLength", "maxLength", "multipleOf", "pattern"):
            assert banned not in schema, f"{banned} not allowed by structured outputs"
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False, "objects need additionalProperties:false"
            props = schema.get("properties", {})
            assert set(schema.get("required", [])) == set(props), \
                f"required must list every property: {schema.get('required')} vs {list(props)}"
            for v in props.values():
                check(v)
        elif schema.get("type") == "array":
            check(schema["items"])

    for s in (prompts.SCRIPT_JSON_ELEMENT, prompts.HOOK_JSON_ELEMENT,
              prompts.SCRIPT_JUDGE_JSON_ELEMENT, prompts.HOOK_JUDGE_JSON_ELEMENT,
              prompts.CONVERSE_ENVELOPE_JSON_SCHEMA):
        check(s)
    # The array wrappers the call sites actually send must be legal too.
    check(main._array_schema("scripts", prompts.SCRIPT_JSON_ELEMENT))
    check(main._array_schema("verdicts", prompts.SCRIPT_JUDGE_JSON_ELEMENT))


def test_arms_for_prompt_shapes_arms_so_learning_block_fires():
    import prompts
    main._arm_stats["learner"] = {
        "style:talking_head": {"n": 10, "effect": 0.80, "confidence": "confirmed"},   # +60%
        "hook_signal:contrarian": {"n": 5, "effect": 0.35, "confidence": "early_read"},  # -30%
        "format_id:myth-buster": {"n": 2, "effect": 0.9},   # too few samples → dropped
    }
    arms = asyncio.run(main._arms_for_prompt("learner"))
    labels = [a["label"] for a in arms]
    assert len(arms) == 2                                      # n<4 arm excluded
    assert arms[0]["lift_pct"] == 60                           # strongest signal first
    assert "talking head style: +60% vs your average" in arms[0]["label"]
    assert any("contrarian hook: -30%" in l for l in labels)
    # The whole point: learning_block now produces a non-empty block from real data.
    block = prompts.learning_block(arms)
    assert "talking head style: +60%" in block and block.strip() != ""
    main._arm_stats.pop("learner", None)


def test_learning_block_empty_on_raw_unshaped_arms():
    import prompts
    # Raw arm dicts (pre-fix) lack lift_pct/label → block must be empty (the old bug).
    raw = [{"n": 10, "effect": 0.8, "confidence": "confirmed"}]
    assert prompts.learning_block(raw) == ""


def test_voice_exemplars_quotes_best_openers():
    import prompts
    posts = [
        {"caption": "Boring low-engagement post. Second sentence.", "likes": 1, "comments": 0},
        {"caption": "Everyone gets protein timing wrong. Here's the fix.", "likes": 500, "comments": 40},
        {"transcript": "I tracked 90 days of data and one thing shocked me.", "likes": 300, "comments": 10},
    ]
    ex = prompts._voice_exemplars(posts, k=2)
    assert "Everyone gets protein timing wrong" in ex        # top by engagement, first sentence
    assert "I tracked 90 days of data" in ex
    assert "Boring low-engagement" not in ex                 # ranked out at k=2
    assert prompts._voice_exemplars([]) == ""


def test_brand_block_emits_catchphrases():
    import prompts
    block = prompts.brand_block({"niche": "fitness", "catchphrases": ["let's get after it", "no excuses"]})
    assert "signature phrases" in block
    assert "let's get after it" in block


def test_quality_hooks_drops_slop_and_reranks(monkeypatch):
    monkeypatch.setattr(main, "AI_QUALITY", True)
    hooks = [
        {"text": "In this video I'll show you", "signal": "curiosity", "strength": 70},  # slop
        {"text": "Everyone does this backwards", "signal": "contrarian", "strength": 60},
        {"text": "$3,180 in 42 days — here's how", "signal": "authority", "strength": 55},
    ]

    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
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


# ---------------------------------------------------------------------------
# Conversational tweaks: apply_edl_ops + /v1/clips/{job_id}/tweak
# ---------------------------------------------------------------------------

def _tweak_edl(style="talking_head"):
    return {
        "style": style, "format_id": "myth-buster",
        "segments": [{"src_in": 0, "src_out": 900}],
        "drops": [{"src_in": 100, "src_out": 130, "reason": "filler"}],
        "captions": [{"word": "hey", "frame": 0}],
        "overlays": [{"type": "punch_in", "src_in": 200, "src_out": 260, "scale": 1.08, "text": ""}],
        "broll": [], "react_source": None, "react_schedule": [],
        "layout": {"style": style, "panels": 1, "panel_boundaries": [], "split_fraction": 0.58},
        "audio": {"lufs_target": -14.0},
        "caption_style": "clean", "trim_aggressiveness": None,
    }


def test_apply_ops_caption_style_and_enabled():
    from app.edl import apply_edl_ops
    words = [{"word": "hello", "start_ms": 0, "end_ms": 300},
             {"word": "um", "start_ms": 300, "end_ms": 400},
             {"word": "world", "start_ms": 400, "end_ms": 700}]
    edl, res = apply_edl_ops(_tweak_edl(), [{"type": "set_caption_style", "style": "karaoke"}], words)
    assert res[0]["applied"] and edl["caption_style"] == "karaoke"
    edl, res = apply_edl_ops(edl, [{"type": "set_captions_enabled", "enabled": False}], words)
    assert res[0]["applied"] and edl["captions"] == []
    edl, res = apply_edl_ops(edl, [{"type": "set_captions_enabled", "enabled": True}], words)
    assert res[0]["applied"]
    assert [c["word"] for c in edl["captions"]] == ["hello", "world"]   # filler stripped
    # No words → rebuild skipped with a reason
    _, res = apply_edl_ops(_tweak_edl(), [{"type": "set_captions_enabled", "enabled": True}], [])
    assert not res[0]["applied"] and "transcript" in res[0]["reason"]


def test_apply_ops_cut_restore_roundtrip():
    from app.edl import apply_edl_ops
    edl, res = apply_edl_ops(_tweak_edl(), [{"type": "cut_range", "start_frame": 300, "end_frame": 390}], [])
    assert res[0]["applied"]
    assert {"src_in": 300, "src_out": 390, "reason": "manual"} in edl["drops"]
    # Cut overlapping the existing filler drop coalesces into a union
    edl2, res2 = apply_edl_ops(edl, [{"type": "cut_range", "start_frame": 90, "end_frame": 120}], [])
    assert res2[0]["applied"]
    merged = [d for d in edl2["drops"] if d["src_in"] == 90]
    assert merged and merged[0]["src_out"] == 130 and merged[0]["reason"] == "manual"
    # Restore the middle of the manual cut → splits into two remainders
    edl3, res3 = apply_edl_ops(edl2, [{"type": "restore_range", "start_frame": 320, "end_frame": 360}], [])
    assert res3[0]["applied"]
    spans = [(d["src_in"], d["src_out"]) for d in edl3["drops"]]
    assert (300, 320) in spans and (360, 390) in spans and (300, 390) not in spans
    # Cut that would leave <2s is refused
    _, res4 = apply_edl_ops(_tweak_edl(), [{"type": "cut_range", "start_frame": 0, "end_frame": 880}], [])
    assert not res4[0]["applied"] and "2 seconds" in res4[0]["reason"]


def test_kept_frames_overlap_aware():
    """Regression: _kept_frames must subtract only drop∩segment overlap — drops
    outside segment bounds previously inflated the subtraction (even negative),
    wrongly blocking legitimate trims."""
    from app.edl import _kept_frames, apply_edl_ops
    edl = {"segments": [{"src_in": 100, "src_out": 500}],           # 400 real frames
           "drops": [{"src_in": 0, "src_out": 150, "reason": "dead_air"}]}  # only 50 overlap
    assert _kept_frames(edl) == 350                                 # was 250 pre-fix
    # fully-outside drop subtracts nothing (was negative-prone pre-fix)
    edl2 = {"segments": [{"src_in": 1000, "src_out": 1100}],
            "drops": [{"src_in": 0, "src_out": 900, "reason": "filler"}]}
    assert _kept_frames(edl2) == 100
    # overlapping drops union-merge instead of double-subtracting
    edl3 = {"segments": [{"src_in": 0, "src_out": 300}],
            "drops": [{"src_in": 50, "src_out": 150, "reason": "filler"},
                      {"src_in": 100, "src_out": 200, "reason": "manual"}]}
    assert _kept_frames(edl3) == 150
    # and a legitimate trim on edl-with-outside-drop is now allowed
    full = {**_tweak_edl(), "segments": [{"src_in": 100, "src_out": 500}],
            "drops": [{"src_in": 0, "src_out": 150, "reason": "dead_air"}]}
    _, res = apply_edl_ops(full, [{"type": "trim_end", "frames": 100}], [])
    assert res[0]["applied"], res[0]["reason"]


def test_apply_ops_overlays_broll_split_trims():
    from app.edl import apply_edl_ops
    # remove punch-ins
    edl, res = apply_edl_ops(_tweak_edl(), [{"type": "remove_overlays", "kind": "punch_in"}], [])
    assert res[0]["applied"] and edl["overlays"] == []
    _, res = apply_edl_ops(edl, [{"type": "remove_overlays", "kind": "punch_in"}], [])
    assert not res[0]["applied"]                     # nothing left to remove
    # add punch-in with clamped scale
    edl, res = apply_edl_ops(edl, [{"type": "add_punch_in", "start_frame": 60, "end_frame": 120, "scale": 9}], [])
    assert res[0]["applied"] and edl["overlays"][0]["scale"] == 1.35
    # b-roll refused on talking_head, allowed on faceless
    _, res = apply_edl_ops(_tweak_edl(), [{"type": "add_broll", "start_frame": 30, "end_frame": 90, "query": "city"}], [])
    assert not res[0]["applied"] and "style" in res[0]["reason"]
    edl_f, res = apply_edl_ops(_tweak_edl("faceless"), [{"type": "add_broll", "start_frame": 30, "end_frame": 90, "query": "city"}], [])
    assert res[0]["applied"] and edl_f["broll"][0]["broll_query"] == "city"
    # split fraction only for duet
    _, res = apply_edl_ops(_tweak_edl(), [{"type": "set_split_fraction", "value": 0.5}], [])
    assert not res[0]["applied"]
    edl_d, res = apply_edl_ops(_tweak_edl("duet_split"), [{"type": "set_split_fraction", "value": 0.9}], [])
    assert res[0]["applied"] and edl_d["layout"]["split_fraction"] == 0.75   # clamped
    # trims shrink from the right end and refuse below the floor
    edl_t, res = apply_edl_ops(_tweak_edl(), [{"type": "trim_end", "frames": 100}], [])
    assert res[0]["applied"] and edl_t["segments"][0]["src_out"] == 800
    _, res = apply_edl_ops(_tweak_edl(), [{"type": "trim_start", "frames": 900}], [])
    assert not res[0]["applied"]
    # unknown / undo ops are reported, not raised
    _, res = apply_edl_ops(_tweak_edl(), [{"type": "explode"}, {"type": "undo"}], [])
    assert not res[0]["applied"] and not res[1]["applied"]


def _make_mock_job():
    r = client.post("/v1/clips", json={
        "source_url": "https://example.com/take.mov", "source_id": "tw-1",
        "formats": ["myth-buster"], "style": "talking_head",
        "script": {"hook": "Stop overthinking", "body": "Do the simple thing daily.",
                   "cta": "Follow.", "formatId": "myth-buster"},
    })
    b = r.json()
    return b["job_id"], b["clips"][0]["clip_id"]


def test_tweak_endpoint_mock_caption_style_and_undo():
    job_id, clip_id = _make_mock_job()
    r = client.post(f"/v1/clips/{job_id}/tweak",
                    json={"clip_id": clip_id, "instruction": "make the captions karaoke"})
    assert r.status_code == 200
    b = r.json()
    assert b["mode"] == "mock" and b["changed"] is True and b["needs_render"] is False
    assert b["clip_status"] == "ready"                       # keyless: no re-render
    edl = client.get(f"/v1/clips/{job_id}").json()["edl"]
    assert edl["caption_style"] == "karaoke"
    # undo restores the previous style
    r2 = client.post(f"/v1/clips/{job_id}/tweak", json={"clip_id": clip_id, "instruction": "undo that"})
    assert r2.json()["changed"] is True
    edl2 = client.get(f"/v1/clips/{job_id}").json()["edl"]
    assert (edl2.get("caption_style") or "clean") != "karaoke"
    # nothing left to undo → reported, not an error
    r3 = client.post(f"/v1/clips/{job_id}/tweak", json={"clip_id": clip_id, "instruction": "undo"})
    assert r3.status_code == 200 and r3.json()["changed"] is False


def test_tweak_endpoint_mock_captions_rebuild_uses_mock_words():
    job_id, clip_id = _make_mock_job()
    client.post(f"/v1/clips/{job_id}/tweak", json={"clip_id": clip_id, "instruction": "captions off"})
    assert client.get(f"/v1/clips/{job_id}").json()["edl"]["captions"] == []
    r = client.post(f"/v1/clips/{job_id}/tweak", json={"clip_id": clip_id, "instruction": "captions on please"})
    assert r.json()["changed"] is True
    caps = client.get(f"/v1/clips/{job_id}").json()["edl"]["captions"]
    assert caps and caps[0]["word"] == "Stop"                # rebuilt from mock words


def test_tweak_endpoint_errors():
    job_id, clip_id = _make_mock_job()
    assert client.post("/v1/clips/nope/tweak",
                       json={"clip_id": clip_id, "instruction": "x"}).status_code == 404
    assert client.post(f"/v1/clips/{job_id}/tweak",
                       json={"clip_id": "nope", "instruction": "x"}).status_code == 404
    assert client.post(f"/v1/clips/{job_id}/tweak",
                       json={"clip_id": clip_id, "instruction": "  "}).status_code == 422
    # 409 while the clip is mid-render
    job = main._clip_jobs[job_id]
    job["clips"][0]["status"] = "rendering"
    assert client.post(f"/v1/clips/{job_id}/tweak",
                       json={"clip_id": clip_id, "instruction": "x"}).status_code == 409
    job["clips"][0]["status"] = "ready"
    # conversational turn (no keywords) → 200, no change, helpful reply
    r = client.post(f"/v1/clips/{job_id}/tweak",
                    json={"clip_id": clip_id, "instruction": "what can you do?"})
    assert r.status_code == 200 and r.json()["changed"] is False and r.json()["reply"]


def test_tweak_live_path_applies_llm_ops(monkeypatch):
    job_id, clip_id = _make_mock_job()
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def fake_json(system, user, schema, model=main.OPUS, max_tokens=3000,
                        temperature=None, array_key=None):
        assert "edit assistant" in system
        return {"reply": "Cutting that section now.",
                "ops": [{"type": "cut_range", "start_frame": 300, "end_frame": 390,
                         "style": None, "enabled": None, "scale": None, "text": None,
                         "query": None, "value": None, "kind": None, "frames": None}]}
    monkeypatch.setattr(main, "anthropic_json", fake_json)
    rendered = []

    async def fake_rerender(j, c):
        rendered.append((j, c))
    monkeypatch.setattr(main, "_rerender_clip", fake_rerender)

    r = client.post(f"/v1/clips/{job_id}/tweak",
                    json={"clip_id": clip_id, "instruction": "cut the boring part at 10s"})
    b = r.json()
    assert b["mode"] == "live" and b["changed"] is True
    assert b["applied"][0]["type"] == "cut_range"
    # mock_ready job → no real renderer → no re-render even on the live path
    assert b["needs_render"] is False and rendered == []
    job = main._clip_jobs[job_id]
    assert len(job["edl_history"]) == 1                      # undo stack pushed
    assert job["tweaks"][-1]["summary"] == "cut_range"


def test_tweak_schema_is_structured_output_legal():
    """The tweak envelope must obey the same SO restrictions as every other schema."""
    import prompts

    def check(schema):
        for banned in ("minimum", "maximum", "minLength", "maxLength", "multipleOf", "pattern"):
            assert banned not in schema
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False
            props = schema.get("properties", {})
            assert set(schema.get("required", [])) == set(props)
            for v in props.values():
                check(v)
        elif schema.get("type") == "array":
            check(schema["items"])
    check(prompts.TWEAK_ENVELOPE_JSON_SCHEMA)


def test_caption_style_survives_pydantic_roundtrip():
    """Regression: caption_style/trim_aggressiveness are real EDL fields now —
    the tweak flow's EDL(**data)→model_dump() must not lose them."""
    from app.edl import EDL, build_render_plan
    d = _tweak_edl()
    d["caption_style"] = "karaoke"
    d["trim_aggressiveness"] = "aggressive"
    out = EDL(**d).model_dump()
    assert out["caption_style"] == "karaoke"
    assert out["trim_aggressiveness"] == "aggressive"
    # And the unset case still renders as clean (key present but None)
    d2 = _tweak_edl(); d2["caption_style"] = None
    assert build_render_plan(EDL(**d2).model_dump())["caption_style"] == "clean"


# ---------- onboarding digest (async brand digest job) ----------

def test_digest_keyless_completes_immediately():
    r = client.post("/v1/onboarding/digest", json={"niche": "fitness coaching"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "mock"
    assert body["status"] == "ready"
    job = client.get(f"/v1/onboarding/digest/{body['job_id']}").json()
    assert job["status"] == "ready"
    assert job["stage"] == "ready"
    assert len(job["scan"]["pillars"]) == 5
    assert len(job["scripts"]) == 3
    assert all(s.get("hook") for s in job["scripts"])


def test_digest_with_posts_injection():
    posts = [{"caption": "Deadlift myth debunked", "hashtags": ["fitness"],
              "likes": 900, "comments": 40}]
    r = client.post("/v1/onboarding/digest",
                    json={"niche": "fitness", "posts": posts})
    job = client.get(f"/v1/onboarding/digest/{r.json()['job_id']}").json()
    assert job["scanned_posts"] == 1
    assert job["status"] == "ready"


def test_digest_with_voice_transcript():
    transcript = [{"role": "agent", "text": "What do you make videos about?"},
                  {"role": "user", "text": "Honest fitness for busy people"}]
    r = client.post("/v1/onboarding/digest",
                    json={"niche": "fitness", "voice_transcript": transcript})
    job = client.get(f"/v1/onboarding/digest/{r.json()['job_id']}").json()
    assert job["status"] == "ready"
    assert job["scan"]["pillars"]


def test_digest_job_not_found():
    assert client.get("/v1/onboarding/digest/nope").status_code == 404


def test_normalize_apify_post_instagram():
    item = {"caption": "My best hook yet #growth", "hashtags": ["growth"],
            "likesCount": 1200, "commentsCount": 88, "videoViewCount": 54000,
            "videoUrl": "https://cdn.example/v.mp4", "videoDuration": 31,
            "timestamp": "2026-06-30T12:00:00Z"}
    p = main._normalize_apify_post(item, "instagram")
    assert p["caption"].startswith("My best hook")
    assert p["likes"] == 1200 and p["comments"] == 88 and p["views"] == 54000
    assert p["video_url"].endswith(".mp4") and p["duration_s"] == 31


def test_normalize_apify_post_tiktok():
    item = {"text": "POV: your first client call", "hashtags": [{"name": "freelance"}],
            "diggCount": 3400, "commentCount": 120, "playCount": 99000,
            "videoMeta": {"downloadAddr": "https://cdn.example/t.mp4", "duration": 22},
            "createTimeISO": "2026-06-29T09:00:00Z"}
    p = main._normalize_apify_post(item, "tiktok")
    assert p["caption"].startswith("POV")
    assert p["hashtags"] == ["freelance"]
    assert p["views"] == 99000 and p["video_url"].endswith(".mp4")


def test_normalize_apify_post_drops_empty():
    assert main._normalize_apify_post({}, "instagram") is None
    assert main._normalize_apify_post({"likesCount": 5}, "instagram") is None


def test_scrape_posts_keyless_empty():
    import asyncio as _a
    assert _a.get_event_loop_policy().new_event_loop().run_until_complete(
        main.scrape_posts("someone", "instagram")) == []


def test_transcribe_top_posts_keyless_noop():
    import asyncio as _a
    posts = [{"caption": "x", "video_url": "https://cdn.example/v.mp4", "views": 10}]
    out = _a.get_event_loop_policy().new_event_loop().run_until_complete(
        main._transcribe_top_posts(posts))
    assert out == posts and "transcript" not in out[0]


def test_derive_prompt_includes_transcript():
    import prompts as P
    posts = [{"caption": "cap", "hashtags": [], "likes": 1, "comments": 0,
              "transcript": "okay so here's the thing about consistency"}]
    _, usr = P.derive_from_posts_prompt({"niche": "fitness"}, posts)
    assert "here's the thing about consistency" in usr
    assert "spoken:" in usr


# ---------------------------------------------------------------------------
# Round 2: instant feed cache + reel stills + emulate creators + hardening
# ---------------------------------------------------------------------------

def test_feed_thumbnails_are_populated():
    b = client.get("/v1/feed", params={"niche": "fitness", "cursor": 0}).json()
    reels_ = [i["reel"] for i in b["items"] if i["type"] == "reel"]
    assert reels_ and all(r["thumbnail_url"].startswith("https://") for r in reels_)


def test_feed_cache_hit_is_instant_and_stable():
    params = {"niche": "unique-cache-niche", "cursor": 0}
    first = client.get("/v1/feed", params=params).json()
    second = client.get("/v1/feed", params=params).json()
    # Same cache key → identical script hooks on the cached hit (no regeneration).
    assert first["items"] == second["items"]


def test_feed_fresh_param_bypasses_cache():
    params = {"niche": "fresh-bypass-niche", "cursor": 0}
    client.get("/v1/feed", params=params)
    key = main._feed_cache_key("default", "fresh-bypass-niche", "", "", "Grow my audience", "", "", 0)
    assert key in main._feed_cache
    r = client.get("/v1/feed", params={**params, "fresh": 1})
    assert r.status_code == 200  # fresh=1 recomputes rather than erroring


def test_feed_cache_key_changes_with_niche():
    k1 = main._feed_cache_key("c1", "fitness", "a", "k", "g", "s", "w", 0)
    k2 = main._feed_cache_key("c1", "finance", "a", "k", "g", "s", "w", 0)
    assert k1 != k2


def test_feed_cursor_clamped():
    b = client.get("/v1/feed", params={"niche": "fitness", "cursor": 9999}).json()
    assert b["items"]  # doesn't 500 or return empty from an out-of-range cursor


def test_cap_evict_bounds_dict_size():
    d = {str(i): i for i in range(10)}
    main._cap_evict(d, 5)
    assert len(d) == 5
    # FIFO: the earliest-inserted keys are the ones evicted.
    assert "0" not in d and "9" in d


def test_reels_cursor_clamped_no_error():
    r = client.get("/v1/reels", params={"niche": "fitness", "cursor": -5})
    assert r.status_code == 200
    r2 = client.get("/v1/reels", params={"niche": "fitness", "cursor": 99999})
    assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Emulate creators
# ---------------------------------------------------------------------------

def test_emulate_analyze_keyless_mock():
    r = client.post("/v1/emulate/analyze", json={"handle": "@SomeCreator", "platform": "instagram"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "mock" and body["ok"] is True
    assert "somecreator" in main._emulation_cache


def test_emulate_analyze_requires_handle():
    r = client.post("/v1/emulate/analyze", json={"handle": "", "platform": "instagram"})
    assert r.status_code == 422


def test_emulate_analyze_second_call_hits_cache():
    client.post("/v1/emulate/analyze", json={"handle": "cachedcreator", "platform": "tiktok"})
    r = client.post("/v1/emulate/analyze", json={"handle": "cachedcreator", "platform": "tiktok"})
    assert r.json()["mode"] == "cached"


def test_resolve_emulation_profiles_preset():
    import asyncio as _a
    targets = [{"name": "Alex Hormozi", "source": "preset"}]
    profiles = _a.get_event_loop_policy().new_event_loop().run_until_complete(
        main._resolve_emulation_profiles(targets))
    assert len(profiles) == 1
    assert profiles[0]["name"] == "Alex Hormozi"
    assert "top_hooks" in profiles[0] and "never_borrow" in profiles[0]


def test_resolve_emulation_profiles_unresolved_custom_omitted():
    import asyncio as _a
    targets = [{"name": "@neverseen", "handle": "neverseenhandleabc", "platform": "instagram", "source": "custom"}]
    profiles = _a.get_event_loop_policy().new_event_loop().run_until_complete(
        main._resolve_emulation_profiles(targets))
    assert profiles == []   # never analyzed → silently omitted, not an error


def test_resolve_emulation_profiles_empty_targets():
    import asyncio as _a
    profiles = _a.get_event_loop_policy().new_event_loop().run_until_complete(
        main._resolve_emulation_profiles([]))
    assert profiles == []


def test_scripts_thread_emulation_preset_keyless():
    # Keyless still returns mock scripts (the important thing: no crash on the
    # emulation_targets field, and the field round-trips through the request).
    r = client.post("/v1/scripts", json={
        "niche": "fitness", "pillar": "test",
        "emulation_targets": [{"name": "Andrew Tate", "source": "preset"}],
    })
    assert r.status_code == 200
    assert len(r.json()["scripts"]) == 3


def test_emulation_block_renders_never_borrow():
    import prompts as P
    profiles = [{"name": "Shelby Sapp", **P.PRESET_EMULATION["Shelby Sapp"]}]
    block = P.emulation_block(profiles)
    assert "Shelby Sapp" in block
    assert "NEVER borrow" in block


def test_emulation_block_empty_on_no_profiles():
    import prompts as P
    assert P.emulation_block([]) == ""


def test_scripts_prompt_includes_emulation_block():
    import prompts as P
    profiles = [{"name": "Alex Hormozi", **P.PRESET_EMULATION["Alex Hormozi"]}]
    _, usr = P.scripts_prompt({"niche": "fitness"}, {"name": "p"}, "talking_head", 3,
                              emulation=profiles)
    assert "STYLE INSPIRATION" in usr and "Alex Hormozi" in usr


def test_hooks_prompt_includes_emulation_block():
    import prompts as P
    profiles = [{"name": "Andrew Tate", **P.PRESET_EMULATION["Andrew Tate"]}]
    _, usr = P.hooks_prompt({"niche": "fitness"}, "topic", emulation=profiles)
    assert "STYLE INSPIRATION" in usr and "Andrew Tate" in usr


def test_digest_threads_emulation_targets():
    r = client.post("/v1/onboarding/digest", json={
        "niche": "fitness",
        "emulation_targets": [{"name": "Alex Hormozi", "source": "preset"}],
    })
    job = client.get(f"/v1/onboarding/digest/{r.json()['job_id']}").json()
    assert job["status"] == "ready"


# ---------------------------------------------------------------------------
# Hardening: chain_scripts guard, TTL sweep, clamps, shared client
# ---------------------------------------------------------------------------

def test_chain_scripts_survives_non_numeric_count():
    import asyncio as _a
    req = main.ConverseRequest(brand={"niche": "fitness"}, creator_id="x")
    out = _a.get_event_loop_policy().new_event_loop().run_until_complete(
        main._chain_scripts(req, {"topic": "abs", "count": "not-a-number"}))
    assert out == [] or isinstance(out, list)   # degrades, never raises


def test_chain_scripts_survives_malformed_brand():
    import asyncio as _a
    req = main.ConverseRequest(brand={"voice": "not-a-dict-should-be"}, creator_id="x")
    out = _a.get_event_loop_policy().new_event_loop().run_until_complete(
        main._chain_scripts(req, {}))
    assert isinstance(out, list)   # never a 500 / unhandled exception


def test_converse_clamps_long_message_history():
    long_history = [{"role": "user", "content": f"msg {i}"} for i in range(100)]
    r = client.post("/v1/converse", json={"creator_id": "x", "mode": "chat", "messages": long_history})
    assert r.status_code == 200   # doesn't choke on an oversized history


def test_sweep_ttl_jobs_evicts_old_entries():
    jobs = {"old": {"created_at": time.time() - 999999}, "new": {"created_at": time.time()}}
    main._sweep_ttl_jobs(jobs, ttl_s=100)
    assert "old" not in jobs and "new" in jobs


def test_generate_scripts_clamps_count():
    import asyncio as _a
    req = main.ScriptRequest(niche="fitness", pillar="p", count=999)
    _a.get_event_loop_policy().new_event_loop().run_until_complete(main._generate_scripts(req))
    assert req.count == 5


def test_timing_middleware_does_not_break_requests():
    r = client.get("/healthz")
    assert r.status_code == 200


def test_anthropic_client_recreated_across_event_loops(monkeypatch):
    """The loop-aware shared client must not raise 'Event loop is closed' when
    reused across the asyncio.run()-per-test pattern this suite already uses."""
    async def fake_post(self, url, headers=None, json=None):
        class R:
            status_code = 200
            def json(self_): return {"content": [{"text": "ok"}]}
        return R()
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main.httpx.AsyncClient, "post", fake_post)
    r1 = asyncio.run(main.anthropic("s", "u", main.HAIKU, 50))
    r2 = asyncio.run(main.anthropic("s", "u", main.HAIKU, 50))
    assert r1 == "ok" and r2 == "ok"


# ---------------------------------------------------------------------------
# Round 3: real reels (Apify) — parsing, mapping, warm, keyless invariance
# ---------------------------------------------------------------------------

def test_parse_watched_platform_prefix_and_backcompat():
    assert main._parse_watched("tiktok:mrbeast, hormozi, instagram:@gary") == [
        ("tiktok", "mrbeast"), ("instagram", "hormozi"), ("instagram", "gary")]
    assert main._parse_watched("") == []
    # unknown prefix falls back to treating the whole token as an IG handle
    assert main._parse_watched("bogus:name") == [("instagram", "bogus:name".replace(":", ""))] or \
        main._parse_watched("bogus:name")[0][0] == "instagram"


def test_niche_hashtags_slugging():
    assert main._niche_hashtags("strength training") == ["strengthtraining", "strength"]
    assert main._niche_hashtags("") == []
    assert main._niche_hashtags("SEO") == ["seo"]


def test_normalize_apify_post_thumbnail_instagram():
    p = main._normalize_apify_post({
        "caption": "hi", "likesCount": 10, "videoViewCount": 500,
        "displayUrl": "https://cdn/thumb.jpg", "videoUrl": "https://cdn/v.mp4",
        "ownerUsername": "coach"}, "instagram")
    assert p["thumbnail_url"] == "https://cdn/thumb.jpg"
    assert p["author"] == "coach"


def test_normalize_apify_post_thumbnail_tiktok():
    p = main._normalize_apify_post({
        "text": "hi", "diggCount": 10, "playCount": 900,
        "videoMeta": {"coverUrl": "https://cdn/cover.jpg", "duration": 20},
        "authorMeta": {"name": "lifter"}}, "tiktok")
    assert p["thumbnail_url"] == "https://cdn/cover.jpg"
    assert p["author"] == "lifter"


def test_reel_from_post_mapping_and_stable_id():
    post = {"caption": "3 mistakes killing your gains #x", "likes": 100, "views": 50_000,
            "thumbnail_url": "https://cdn/t.jpg", "video_url": "https://cdn/v.mp4",
            "author": "hormozi", "posted_at": "2026-01-01"}
    r1 = main._reel_from_post(post, "hormozi", "instagram", 0, True)
    r2 = main._reel_from_post(post, "hormozi", "instagram", 5, True)
    assert r1["id"] == r2["id"]                      # stable across index (posted_at seed)
    assert r1["id"].startswith("real-instagram-hormozi-")
    assert r1["title"] == "3 mistakes killing your gains"   # trailing hashtag stripped
    assert r1["format_id"] == "listicle" and r1["format_id"] in main._VALID_REEL_FORMATS
    assert r1["from_watched"] is True and r1["thumbnail_url"]


def test_heuristic_annotation_covers_format_cues():
    assert main._heuristic_reel_annotation({"caption": "the myth that won't die"})["format_id"] == "myth-buster"
    assert main._heuristic_reel_annotation({"caption": "stop doing this"})["format_id"] == "do-this-not-that"
    assert main._heuristic_reel_annotation({"caption": "before and after 30 days"})["format_id"] == "before-after"


def test_reels_warm_keyless():
    r = client.post("/v1/reels/warm", json={"handle": "@someone", "platform": "tiktok"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.post("/v1/reels/warm", json={"handle": ""}).status_code == 422


def test_reels_keyless_still_mock_corpus():
    # keyless path must be byte-identical to the pre-round-3 mock behavior
    b = client.get("/v1/reels", params={"niche": "fitness", "watched": "tiktok:mrbeast"}).json()
    assert b["mode"] == "mock" and len(b["reels"]) == main.REELS_PAGE
    assert all("id" in r and "thumbnail_url" in r for r in b["reels"])
