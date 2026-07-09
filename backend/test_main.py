import json
import copy
import time
import uuid

from fastapi.testclient import TestClient

import main
import prompts
from main import app

client = TestClient(app)


def seed_clip_job(source_url="https://example.com/video.mov", script=None, style="talking_head",
                  formats=("myth-buster",), edit_prefs=None, **extra):
    """Seed a keyless MOCK-READY clip job directly (the /v1/clips endpoint is analyze-first
    now; these tweak/edit-prefs tests exercise the editor on a ready job). Mirrors the
    old keyless create: edl built from _mock_edl + prefs, words from _mock_words."""
    script = script if script is not None else {"hook": "Test hook", "body": "Body text here",
                                                "cta": "Follow", "formatId": "myth-buster"}
    edit_prefs = edit_prefs or {}
    job_id = str(uuid.uuid4())
    clips = [{"clip_id": str(uuid.uuid4()), "format": f, "status": "ready",
              "render_url": source_url} for f in formats]
    job = {
        "job_id": job_id, "source_id": "src1", "status": "mock_ready", "clips": clips,
        "script": script, "style": style, "brand": {}, "media_context": "",
        "source_url": source_url, "error": None, "edit_prefs": edit_prefs,
        "react_source_url": "", "react_credit_label": "",
        "edl": main._apply_edit_prefs(main._mock_edl(style, script), edit_prefs),
        "words": main._mock_words(script), "edl_history": [], "tweaks": [],
        "custom_instructions": "", "created_at": time.time(),
    }
    job.update(extra)
    main._clip_jobs[job_id] = job
    return job_id


class SupabaseClientStub:
    """Truthy stand-in for the Supabase client; tests attach AsyncMocks per method."""
    async def upsert_arm_stat(self, *a, **k): return True
    async def load_arm_stats(self, *a, **k): return {}
    async def upsert_post(self, *a, **k): return True
    async def load_post(self, *a, **k): return None
    async def load_all_posts(self, *a, **k): return []
    async def settle_post_conditional(self, *a, **k): return True
    async def upsert_creator(self, *a, **k): return True
    async def load_creator(self, *a, **k): return None
    async def load_all_creators(self, *a, **k): return []


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


def test_publish_mock_when_no_target_accounts():
    # Even with a key, an empty social_account_ids means nothing to post to -> mock.
    b = client.post("/v1/publish", json={"caption": "hi", "social_account_ids": []}).json()
    assert b["ok"] is True and b["mode"] == "mock"


def test_converse_edit_video_intent_keyless():
    # W5-1: attachments + an edit ask → edit_video intent with the instructions echoed.
    r = client.post("/v1/converse", json={"messages": [{"role": "user", "content": "stitch these and cut the pauses"}],
                                          "attachments": [{"type": "video_upload"}, {"type": "video_upload"}]}).json()
    assert r["intent"] == "edit_video"
    assert "cut the pauses" in r["payload"]["edit_instructions"]


def test_converse_edit_video_needs_attachments():
    r = client.post("/v1/converse", json={"messages": [{"role": "user", "content": "cut the pauses"}]}).json()
    assert r["intent"] != "edit_video"     # no attachments → not an edit


def test_perf_summary_no_data_flag():
    # C-04: keyless / no settled posts → honest no_data so the client shows an empty state.
    r = client.get("/v1/performance/summary", params={"creator_id": "c_nodata"}).json()
    assert r["mode"] == "mock" and r.get("no_data") is True


def test_insights_persona_in_prompt():
    # C-09: coach persona threads into the prompt voice.
    sysp, _ = prompts.insights_prompt({"niche": "fitness"}, "great week", persona="sergeant")
    assert "Sergeant" in sysp or "sergeant" in sysp.lower()


def test_eval_flags_ungrounded_receipt():
    from eval.invariants import _flag_ungrounded_receipt
    bad = {"hook": "I made $4,200 in three weeks doing this.", "body": "here's how"}
    good = {"hook": "Most people leave [your number] on the table.", "body": "here's the fix"}
    assert _flag_ungrounded_receipt(bad, {}) == "ungrounded receipt"
    assert _flag_ungrounded_receipt(good, {}) is None     # bracketed fill-in suppresses it


def test_rehost_media_keyless_noop(monkeypatch):
    # W2-2: no Supabase config → returns None (keeps CDN url), suite stays keyless-green.
    monkeypatch.setattr(main, "SUPABASE_URL", "")
    monkeypatch.setattr(main, "SUPABASE_KEY", "")
    r = asyncio.run(main._rehost_media("https://cdn/x.mp4", "reels/a.mp4", "video/mp4", 60_000_000))
    assert r is None


def test_reel_storage_stem_deterministic():
    p = {"platform": "instagram", "author": "coach", "timestamp": "2026-01-01"}
    assert main._reel_storage_stem(p) == main._reel_storage_stem(p)   # stable → overwrite, not accumulate


def test_refresh_niche_reels_transcribes_before_mapping(monkeypatch):
    # W2-1: _transcribe_top_posts runs before _reel_from_post so the reel carries the spoken transcript.
    async def fake_scrape(niche, limit=20):
        return [{"author": "c", "platform": "instagram", "views": 50000, "likes": 100,
                 "caption": "cap", "video_url": "https://cdn/v.mp4", "timestamp": "t"}]
    captured = {}
    async def fake_transcribe(posts, top_n=4):
        captured["top_n"] = top_n
        for p in posts: p["transcript"] = "the real spoken words"
        return posts
    monkeypatch.setattr(main, "scrape_niche_posts", fake_scrape)
    monkeypatch.setattr(main, "_transcribe_top_posts", fake_transcribe)
    monkeypatch.setattr(main, "SUPABASE_URL", "")   # skip rehost
    monkeypatch.setattr(main, "_niche_reels_cache", {})
    asyncio.run(main._refresh_niche_reels("fitness"))
    key = main._niche_cache_key("fitness")
    reels = main._niche_reels_cache[key]["reels"]
    assert reels and reels[0]["transcript"] == "the real spoken words"
    assert captured["top_n"] == main._REEL_TRANSCRIBE_TOP_N


def test_tiktok_scrapes_request_video_downloads(monkeypatch):
    """Actor-schema drift: with shouldDownloadVideos:false the TikTok scraper now
    returns NO direct video URL at all (downloadAddr gone, mediaUrls empty) — which
    silently killed reel previews AND transcription (both need a fetchable URL).
    Every TikTok actor call must request downloads (KV-store mp4s are public)."""
    calls = []
    async def fake_actor(actor, payload):
        calls.append((actor, payload))
        return []
    monkeypatch.setattr(main, "_run_apify_actor", fake_actor)
    monkeypatch.setattr(main, "APIFY_KEY", "k")
    asyncio.run(main.scrape_niche_posts("fitness"))
    asyncio.run(main.scrape_posts("somehandle", "tiktok"))
    asyncio.run(main._resolve_post_media("https://www.tiktok.com/@x/video/123"))
    tiktok_calls = [p for a, p in calls if "tiktok" in a]
    assert tiktok_calls, "expected TikTok actor calls"
    assert all(p.get("shouldDownloadVideos") is True for p in tiktok_calls)


def test_clamp_title_word_boundary():
    """Pick-card titles must be short enough to render un-truncated: ≤42 chars,
    cut at a word boundary, no trailing punctuation fragments."""
    long = "upper body home workout featuring push ups (12 reps), pike push ups (8 reps)"
    out = main._clamp_title(long)
    assert len(out) <= 42
    assert not out.endswith((" ", ",", "(", "-"))
    assert out == "upper body home workout featuring push ups"
    assert main._clamp_title("Short title") == "Short title"
    assert main._clamp_title("") == ""


def test_feed_scripts_titles_clamped(monkeypatch):
    """Every script entry the feed serves carries a display-safe title, even when
    the (cached/LLM) script arrived with a runaway one."""
    async def fake_reels(niche="", creator_id="default", watched="", cursor=0):
        return {"mode": "mock", "reels": [], "next_cursor": None}
    monkeypatch.setattr(main, "reels", fake_reels)
    long_title = "the seven strength training mistakes that are quietly killing your gains after thirty"
    result = {"mode": "live", "scripts": [{"title": long_title, "hook": {"text": "h"}}]}
    items, _ = asyncio.run(main._compose_feed_items(result, "fitness", "c1", "", 0))
    script_items = [i for i in items if i["type"] == "script"]
    assert script_items and all(len(i["script"]["title"]) <= 42 for i in script_items)


class _FakeReelsPersistence:
    """Stand-in for SupabaseClient in the reels-durability tests."""
    def __init__(self, preload=None):
        self.rows = dict(preload or {})
        self.upserts = []
    async def upsert_reels_cache(self, cache_key, entry):
        self.rows[cache_key] = entry
        self.upserts.append(cache_key)
        return True
    async def load_reels_cache(self, cache_key):
        return self.rows.get(cache_key)


def test_refresh_niche_reels_writes_through_to_supabase(monkeypatch):
    """Durability: a completed refresh mirrors the cache entry to Supabase so a
    deploy no longer wipes transcripts + re-hosted media."""
    async def fake_scrape(niche, limit=20):
        return [{"author": "c", "platform": "instagram", "views": 50000, "likes": 100,
                 "caption": "cap", "video_url": "https://cdn/v.mp4", "posted_at": "t1"}]
    fake = _FakeReelsPersistence()
    monkeypatch.setattr(main, "scrape_niche_posts", fake_scrape)
    monkeypatch.setattr(main, "SUPABASE_URL", "")            # skip rehost
    monkeypatch.setattr(main, "_supabase_client", fake)
    monkeypatch.setattr(main, "_niche_reels_cache", {})
    asyncio.run(main._refresh_niche_reels("fitness"))
    key = main._niche_cache_key("fitness")
    assert key in fake.rows
    assert fake.rows[key]["reels"][0]["creator_handle"] == "c"


def test_refresh_niche_reels_carries_prev_transcript_and_durable_media(monkeypatch):
    """Accumulation: a re-scrape of the same post must NOT lose the transcript or
    the re-hosted Supabase video URL earned on a previous cycle (in-memory cache
    empty → previous entry comes from Supabase)."""
    post = {"author": "c", "platform": "instagram", "views": 50000, "likes": 100,
            "caption": "cap", "video_url": "https://cdn/expired.mp4", "posted_at": "t1"}
    async def fake_scrape(niche, limit=20):
        return [dict(post)]
    key = main._niche_cache_key("fitness")
    prev_reel = main._reel_from_post(
        {**post, "transcript": "the real spoken words",
         "video_url": "https://nxi.supabase.co/storage/v1/object/public/marque-clips/reels/x.mp4"},
        "c", "instagram", 0, False)
    fake = _FakeReelsPersistence({key: {"reels": [prev_reel], "ts": 1.0}})
    monkeypatch.setattr(main, "scrape_niche_posts", fake_scrape)
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "")            # no live transcription
    monkeypatch.setattr(main, "SUPABASE_URL", "https://nxi.supabase.co")
    monkeypatch.setattr(main, "SUPABASE_KEY", "")            # rehost disabled (no key)
    monkeypatch.setattr(main, "_supabase_client", fake)
    monkeypatch.setattr(main, "_niche_reels_cache", {})
    asyncio.run(main._refresh_niche_reels("fitness"))
    got = main._niche_reels_cache[key]["reels"][0]
    assert got["transcript"] == "the real spoken words"      # carried, not reset to caption
    assert got["video_url"].startswith("https://nxi.supabase.co/")  # durable URL kept
    assert got["transcribed"] is True


def test_reels_endpoint_hydrates_from_supabase_on_cold_miss(monkeypatch):
    """A deploy wipes the in-memory cache; the first /v1/reels call must serve the
    durable Supabase copy instead of an empty list."""
    key = main._niche_cache_key("fitness")
    reel = main._reel_from_post(
        {"author": "c", "platform": "instagram", "views": 50000, "likes": 9,
         "caption": "cap", "transcript": "spoken", "posted_at": "t1"},
        "c", "instagram", 0, False)
    fake = _FakeReelsPersistence({key: {"reels": [reel], "ts": main.time.time()}})
    monkeypatch.setattr(main, "APIFY_KEY", "apify-test")
    monkeypatch.setattr(main, "_supabase_client", fake)
    monkeypatch.setattr(main, "_niche_reels_cache", {})
    monkeypatch.setattr(main, "_watched_reels_cache", {})
    monkeypatch.setattr(main, "_reels_refreshing", set())
    r = client.get("/v1/reels", params={"niche": "fitness"}).json()
    assert r["mode"] == "live"
    assert len(r["reels"]) == 1 and r["reels"][0]["transcript"] == "spoken"


def test_trends_mock_rotates_by_bucket(monkeypatch):
    # W1-2: the mock trend list rotates by the 6h time bucket so the ticker isn't frozen.
    monkeypatch.setattr(main, "_niche_trends_cache", {})
    monkeypatch.setattr(main.time, "time", lambda: 0.0)
    a = client.get("/v1/trends", params={"niche": "fitness"}).json()
    monkeypatch.setattr(main.time, "time", lambda: 6 * 3600.0 + 1)
    b = client.get("/v1/trends", params={"niche": "fitness"}).json()
    assert a["mode"] == "mock" and b["mode"] == "mock"
    assert len(a["trends"]) == len(b["trends"]) == 6
    assert a["trends"][0]["title"] != b["trends"][0]["title"]   # rotated


def test_heuristic_niche_trends_computed_stats():
    posts = [{"caption": "5 mistakes killing your gains", "views": 90000},
             {"caption": "5 rules for protein", "views": 40000},
             {"caption": "the truth about cardio", "views": 10000}]
    trends = main._heuristic_niche_trends("fitness", posts)
    assert trends and all("formatId" in t and t["formatId"] in main.FORMAT_IDS for t in trends)
    assert "views" in trends[0]["why"]                          # real computed stat, not invented
    assert trends[0]["formatId"] == "listicle"                  # highest-view cluster


def test_trends_live_swr(monkeypatch):
    key = main._niche_cache_key("fitness")
    monkeypatch.setattr(main, "_niche_trends_cache",
                        {key: {"trends": [{"title": "Live one", "why": "w", "formatId": "listicle"}], "ts": 9e18}})
    r = client.get("/v1/trends", params={"niche": "fitness"}).json()
    assert r["mode"] == "live" and r["trends"][0]["title"] == "Live one"


def test_niche_trends_prompt_shape():
    sysp, usr = prompts.niche_trends_prompt("fitness", [{"caption": "5 rules"}])
    assert "fitness" in sysp and "JSON array" in sysp


def test_grounding_block_in_generation_prompts():
    # W3: no-fabrication rule injected into scripts/hooks/mimic/revise/analyze systems.
    assert "GROUNDING" in prompts.scripts_prompt({"niche": "fitness"}, {"name": "P"}, "talking_head", 2)[0]
    assert "[your result]" in prompts.scripts_prompt({"niche": "fitness"}, {"name": "P"}, "talking_head", 2)[0]
    assert "GROUNDING" in prompts.hooks_prompt({"niche": "fitness"}, "protein")[0]
    assert "GROUNDING" in prompts.mimic_prompt({"hook_text": "h", "transcript": "t"}, {"niche": "fitness"})[0]


def test_virality_and_hooks_example_not_fabricated():
    # W3-2: the "$3,180" invented-specific reward and the fabricated hooks example are gone.
    assert "$3,180" not in prompts.VIRALITY_BLOCK
    hooks_sys = prompts.hooks_prompt({"niche": "fitness"}, "protein")[0]
    assert "tracked every gram for 90 days" not in hooks_sys


def test_judge_schema_has_fabricated():
    # W3-3: fabricated flag in both the prose schema and the structured-output element.
    assert "fabricated" in prompts.SCRIPT_JUDGE_SCHEMA
    assert "fabricated" in prompts.SCRIPT_JUDGE_JSON_ELEMENT["required"]
    assert "fabricated" in prompts.SCRIPT_JUDGE_JSON_ELEMENT["properties"]
    # judge gets creator context when a brand is passed
    sysp, usr = prompts.script_judge_prompt([{"hook": "h", "body": "b", "cta": "c"}], "talking_head",
                                            brand={"niche": "fitness", "known_for": "form checks"})
    assert "CREATOR CONTEXT" in usr and "fitness" in usr


def test_blend_score_fabricated_penalty():
    base = {"hook_strength": 80, "specificity": 70, "format_fit": 70, "voice_match": 70}
    clean = main._blend_score(base)
    dirty = main._blend_score({**base, "fabricated": True})
    assert clean - dirty == 15


def test_mock_scripts_no_invented_receipts():
    from types import SimpleNamespace
    req = main.ScriptRequest(niche="fitness coaching", style="talking_head", count=3)
    for s in main.mock_scripts(req):
        blob = (s["hook"] + " " + s["body"]).lower()
        assert "i tracked my" not in blob and "after years in" not in blob


def test_publish_honest_posted_flag(monkeypatch):
    # C-01: additive posted/reason so the client stops showing "Posted" for nothing.
    # ok semantics are FROZEN (build 9 reads only ok) — mock stays ok:true.
    monkeypatch.setattr(main, "POSTFORME_KEY", "")
    b = client.post("/v1/publish", json={"caption": "hi", "social_account_ids": ["acc1"]}).json()
    assert b["ok"] is True and b["mode"] == "mock"          # frozen
    assert b["posted"] is False and b["reason"] == "no_key"

    monkeypatch.setattr(main, "POSTFORME_KEY", "k")
    b2 = client.post("/v1/publish", json={"caption": "hi", "social_account_ids": []}).json()
    assert b2["ok"] is True and b2["posted"] is False and b2["reason"] == "no_accounts"


def test_publish_live_posted_true(monkeypatch):
    monkeypatch.setattr(main, "POSTFORME_KEY", "k")

    async def fake_pfm(method, path, json_body=None):
        return 201, {"id": "pfm_1", "status": "scheduled"}
    monkeypatch.setattr(main, "_pfm_request", fake_pfm)
    b = client.post("/v1/publish", json={"caption": "hi", "media_url": "https://x/y.mp4",
                                         "social_account_ids": ["acc1"]}).json()
    assert b["ok"] is True and b["posted"] is True and b["reason"] is None and b["mode"] == "live"


def test_social_auth_url_mock_returns_empty_url():
    # Keyless: the client must be able to detect "linking unavailable" (empty url).
    b = client.post("/v1/social/auth-url", json={"platform": "instagram",
                                                 "external_id": "creator_1"}).json()
    assert b["platform"] == "instagram"
    assert b["url"] == ""
    assert b["mode"] == "mock"


def test_social_accounts_mock_returns_empty_list():
    b = client.get("/v1/social/accounts", params={"external_id": "creator_1"}).json()
    assert b["accounts"] == []
    assert b["mode"] == "mock"


def test_social_disconnect_mock_ok():
    b = client.post("/v1/social/disconnect", json={"account_id": "spc_x"}).json()
    assert b["ok"] is True and b["mode"] == "mock"


def test_mint_upload_url():
    r = client.post("/v1/uploads/mint", json={"filename": "test.mov", "content_type": "video/quicktime"})
    assert r.status_code == 200
    b = r.json()
    assert "upload_url" in b
    assert "key" in b
    assert "public_url" in b
    assert b["mode"] in ("live", "mock")


def test_mint_upload_url_mock_returns_empty_urls_when_no_storage(monkeypatch):
    # With no storage backend configured, mint must NOT hand back a real-looking
    # (but dead) public_url — that's exactly the prod bug where every upload got a
    # media.marque.app/mock/... URL that no fetcher could resolve. Empty strings
    # let the client detect "no storage" and fall back to local/mock clips instead
    # of creating a live job doomed to fail at transcribe/render.
    monkeypatch.setattr(main, "SUPABASE_URL", "")
    monkeypatch.setattr(main, "SUPABASE_KEY", "")
    monkeypatch.setattr(main, "R2_ACCESS_KEY", "")
    b = client.post("/v1/uploads/mint",
                    json={"filename": "test.mov", "content_type": "video/quicktime"}).json()
    assert b["mode"] == "mock"
    assert b["upload_url"] == "" and b["public_url"] == ""
    assert "media.marque.app" not in b["public_url"]


def test_readyz_reports_storage_status():
    b = client.get("/readyz").json()
    assert b["storage"] in ("live", "mock")


def test_create_clip_job_analyze_first():
    r = client.post("/v1/clips", json={
        "source_url": "https://example.com/footage.mov", "source_id": "test-123",
        "analyze_first": True,
        "script": {"hook": "Stop doing this", "body": "Here is why.", "cta": "Follow me.", "formatId": "myth-buster"},
    })
    assert r.status_code == 200
    b = r.json()
    assert "job_id" in b and b["status"] == "brief_ready" and b["mode"] == "mock"
    assert "edit_brief" in b


def test_old_shape_clip_request_requires_update():
    # Cutover: a stale client (no analyze_first) must get a clear 426, not a 500.
    r = client.post("/v1/clips", json={
        "source_url": "https://example.com/footage.mov", "formats": ["myth-buster"],
        "style": "talking_head", "script": {"hook": "x", "body": "y", "cta": "z"}})
    assert r.status_code == 426
    assert r.json()["detail"]["error"] == "update_required"


def test_get_clip_job():
    # Create a job first
    job_id = seed_clip_job(source_url="https://example.com/footage.mov", style="fast_cuts", formats=["listicle"])
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


def test_demo_editor_job_synthesized_keyless():
    """E-12: a `demo-` job id is synthesized keyless with a 3-segment EDL so the
    manual editor is drivable in the sim without running the record→makeClips
    flow. A non-demo unknown id still 404s (test_get_clip_job_not_found)."""
    r = client.get("/v1/clips/demo-clip-job?include_words=1")
    assert r.status_code == 200
    b = r.json()
    assert b["mode"] == "mock"
    edl = b["edl"]
    assert edl is not None
    assert len(edl["segments"]) == 3          # sim-drivable reorder needs ≥2
    assert b["source_url"] is None            # keyless ⇒ placeholder player
    assert len(b["words"]) > 0                # word editor has content
    # idempotent: polling again returns the same job, still 3 segments
    r2 = client.get("/v1/clips/demo-clip-job")
    assert r2.status_code == 200 and len(r2.json()["edl"]["segments"]) == 3


def test_demo_editor_save_roundtrips_with_arbitrary_clip_id():
    """The editor's Save posts ops with the iOS clip's random UUID — which the
    backend never issued for the synthesized demo job. A keyless demo job must
    adopt that clip_id and apply the ops (defer_render) rather than 404."""
    client.get("/v1/clips/demo-editor-save?include_words=1")   # synthesize
    ios_clip_id = "DEADBEEF-0000-0000-0000-000000000001"       # not issued by the backend
    r = client.post("/v1/clips/demo-editor-save/tweak?defer_render=1",
                    json={"clip_id": ios_clip_id,
                          "ops": [{"type": "split_segment", "index": 0, "at_frame": 120}]})
    assert r.status_code == 200
    b = r.json()
    assert b.get("error") is not True
    assert b["changed"] is True and b["needs_render"] is False
    # the split landed: 3 segments → 4
    assert len(main._clip_jobs["demo-editor-save"]["edl"]["segments"]) == 4


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
    job_id = seed_clip_job(source_url="https://example.com/f.mov", edit_prefs={"auto_captions": False, "filler_trim": "off", "caption_style": "karaoke"})
    job = client.get(f"/v1/clips/{job_id}").json()
    edl = job["edl"]
    assert edl["captions"] == []          # captions off honored
    assert edl["drops"] == []             # filler trim off honored


def test_edit_prefs_defaults_preserved():
    job_id = seed_clip_job(source_url="https://example.com/f.mov",
                           script={"hook": "Stop doing this now friends", "body": "Here is why.",
                                   "cta": "Follow.", "formatId": "myth-buster"})
    edl = client.get(f"/v1/clips/{job_id}").json()["edl"]
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


def test_merge_drops_unions_overlaps_instead_of_skipping():
    # F11: an overlapping new (filler) drop must be MERGED into the existing (LLM)
    # drop — extending its boundaries — not silently discarded outright, which used
    # to leave the non-overlapping remainder of the filler word un-cut.
    existing = [{"src_in": 100, "src_out": 150, "reason": "dead_air"}]
    new = [{"src_in": 140, "src_out": 160, "reason": "filler"},               # overlaps → merge
           {"src_in": 200, "src_out": 210, "reason": "filler"}]               # clean → add
    out = main._merge_drops(existing, new)
    assert len(out) == 2
    assert out[0]["src_in"] == 100 and out[0]["src_out"] == 160   # extended to cover both
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
        "style:faceless": {"n": 12, "n_raw": 12, "sum_raw": 18.5, "effect": 0.75, "confidence": "confirmed"}})
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._arm_stats.pop("c_lazy", None)                   # cache miss → must load
    main._arms_loaded.discard("c_lazy")
    _seed_baseline("c_lazy", mean=1.0)                    # personal baseline 1.0
    arms = asyncio.run(main._arms_for_prompt("c_lazy"))
    fake.load_arm_stats.assert_awaited_once_with("c_lazy")
    assert main._arm_stats["c_lazy"]["style:faceless"]["n"] == 12
    assert arms and arms[0]["lift_pct"] == 50             # (18.5+1)/13 = 1.5× the 1.0 mean


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
    main._arms_loaded.discard("learner")
    _seed_baseline("learner", mean=1.0)                # personal baseline 1.0
    main._arm_stats["learner"] = {
        "style:talking_head": {"n": 10, "n_raw": 10, "sum_raw": 16.6, "confidence": "confirmed"},   # +60%
        "hook_signal:contrarian": {"n": 5, "n_raw": 5, "sum_raw": 3.2, "confidence": "early_read"},  # -30%
        "format_id:myth-buster": {"n": 2, "n_raw": 2, "sum_raw": 4.0},   # too few samples → dropped
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
    job_id = seed_clip_job(source_url="https://example.com/take.mov",
                           script={"hook": "Stop overthinking", "body": "Do the simple thing daily.",
                                   "cta": "Follow.", "formatId": "myth-buster"})
    return job_id, main._clip_jobs[job_id]["clips"][0]["clip_id"]


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


def test_emulate_failed_live_not_cached_or_persisted(monkeypatch):
    # B-07: a live attempt that falls back to mock (empty scrape / LLM error) must NOT
    # be cached or persisted — else a transient failure poisons the handle forever.
    from unittest.mock import AsyncMock
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    fake = SupabaseClientStub()
    fake.load_emulation_profile = AsyncMock(return_value=None)
    fake.upsert_emulation_profile = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "_supabase_client", fake)

    async def some_posts(h, p):
        return [{"caption": "a post"}]

    async def passthrough(posts):
        return posts
    monkeypatch.setattr(main, "scrape_posts", some_posts)
    monkeypatch.setattr(main, "_transcribe_top_posts", passthrough)

    async def boom(*a, **k):
        raise main.HTTPException(status_code=502, detail="down")
    monkeypatch.setattr(main, "anthropic", boom)
    main._emulation_cache.pop("failedcreator", None)
    r = client.post("/v1/emulate/analyze",
                    json={"handle": "failedcreator", "platform": "instagram"}).json()
    assert r["mode"] == "mock"
    assert "failedcreator" not in main._emulation_cache        # retryable, not poisoned
    fake.upsert_emulation_profile.assert_not_awaited()         # no durable poison


# ---------------------------------------------------------------------------
# B-08 · Background tasks keep a strong reference (no GC-mid-flight).
# ---------------------------------------------------------------------------

def test_spawn_retains_then_discards_task():
    async def _run():
        async def work():
            return 42
        t = main._spawn(work())
        assert t in main._bg_tasks              # strongly referenced while in flight
        await t
        await asyncio.sleep(0)                   # let the done-callback run
        assert t not in main._bg_tasks           # discarded on completion
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# B-09 · Direct scrape endpoints are time-bounded — a slow Apify run degrades to
# mock inside a proxy timeout instead of hanging for minutes.
# ---------------------------------------------------------------------------

def test_emulate_scrape_bounded_degrades_to_mock(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "_supabase_client", None)

    async def slow_scrape(handle, platform):
        await asyncio.sleep(5)                    # longer than our tiny test budget
        return [{"caption": "late"}]
    monkeypatch.setattr(main, "scrape_posts", slow_scrape)
    main._emulation_cache.pop("slowcreator", None)
    req = main.EmulateAnalyzeRequest(handle="slowcreator", platform="instagram")
    r = asyncio.run(main.emulate_analyze(req, _budget_s=0.05))
    assert r["mode"] == "mock"                    # timed out → mock
    assert "slowcreator" not in main._emulation_cache   # not cached → retryable


def test_brand_scan_scrape_bounded(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    monkeypatch.setattr(main, "_SCRAPE_BUDGET_S", 0.05)

    async def slow_scrape(handle, platform):
        await asyncio.sleep(5)
        return [{"caption": "late"}]
    monkeypatch.setattr(main, "scrape_posts", slow_scrape)
    r = client.post("/v1/brand-scan/handle", json={"handle": "slowbrand", "platform": "instagram"}).json()
    assert r["mode"] == "mock" and r["scanned_posts"] == 0   # degraded, didn't hang


# ---------------------------------------------------------------------------
# B-10 · Voice-session + trends + digest honesty.
# ---------------------------------------------------------------------------

def test_voice_session_reports_mock_without_real_mint(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent-1")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-key")
    r = client.post("/v1/voice-onboarding/session", json={}).json()
    # keys present but the get-signed-url mint isn't implemented → must NOT claim live
    assert r["mode"] == "mock" and r["conversation_token"] == ""


def test_converse_live_omits_fabricated_trends(monkeypatch):
    cap = {}

    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
        cap["user"] = user
        return '{"reply":"hi","memory_updates":[],"intent":"none","chips":[]}'
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "anthropic", fake)
    client.post("/v1/converse", json={"messages": [{"role": "user", "content": "hey"}],
                                      "brand": {"niche": "fitness"}})
    assert "Trending in their niche right now" not in cap.get("user", "")


def test_digest_reads_top_level_catchphrases_and_banned():
    scan = {"pillars": [{"name": "P"}], "voice": {"tone": 0.5},
            "catchphrases": ["let's get into it"], "bannedWords": ["literally"], "niche": "fitness"}
    req = main.DigestRequest(non_negotiables=["no swearing"])
    sreq = main._digest_script_request(req, scan)
    assert "let's get into it" in sreq.catchphrases           # top-level, not voice.catchphrases
    assert "literally" in sreq.non_negotiables                # bannedWords folded in
    assert "no swearing" in sreq.non_negotiables


# ---------------------------------------------------------------------------
# F-01 · Edit-brief deterministic mock: schema-shaped, filler/dead-air cuts come
# ONLY from strip_fillers, works with no script.
# ---------------------------------------------------------------------------

def test_mock_edit_brief_shape_and_deterministic_cuts():
    import prompts
    words = [{"word": "So", "start_ms": 0, "end_ms": 200, "type": "filler"},
             {"word": "here", "start_ms": 220, "end_ms": 500},
             {"word": "is", "start_ms": 520, "end_ms": 700},
             {"word": "the", "start_ms": 720, "end_ms": 900},
             {"word": "point", "start_ms": 2000, "end_ms": 2300}]   # big gap → dead_air
    b = main._mock_edit_brief(words, transcript="Here is the point. And more.")
    for k in prompts.EDIT_BRIEF_SCHEMA["required"]:
        assert k in b                                          # schema-shaped
    assert b["strategy"] == "trim_only" and b["restructure_order"] == []
    assert b["inferred"]["style"] in prompts.STYLES
    assert b["inferred"]["format_id"] in prompts.FORMAT_IDS
    assert b["hook_candidates"][0]["quote"]
    reasons = {c["reason"] for c in b["cut_regions"]}
    assert reasons <= set(prompts.CUT_REASONS)
    assert "filler" in reasons and "dead_air" in reasons      # both deterministic cuts present


def test_mock_edit_brief_works_without_script():
    b = main._mock_edit_brief([], transcript="")
    assert b["is_scripted"] is False and b["hook_candidates"][0]["quote"]
    assert b["through_line"]                                   # never empty


# ---------------------------------------------------------------------------
# F-02 · Edit-brief LLM prompt + live helper (validate inferred, re-merge cuts).
# ---------------------------------------------------------------------------

def test_generate_edit_brief_keyless_returns_mock(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    b = asyncio.run(main._generate_edit_brief(
        [{"word": "hi", "start_ms": 0, "end_ms": 200}], transcript="Hi there."))
    assert b["strategy"] == "trim_only" and b["inferred"]["style"] == "talking_head"


def test_generate_edit_brief_validates_inferred(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    bad = {**main._mock_edit_brief([], ""),
           "inferred": {"style": "junk", "format_id": "junk", "hook_signal": "junk", "pillar": "P"}}
    monkeypatch.setattr(main, "anthropic_json", AsyncMock(return_value=bad))
    b = asyncio.run(main._generate_edit_brief([], transcript="x"))
    assert b["inferred"]["style"] in prompts.STYLES
    assert b["inferred"]["format_id"] in prompts.FORMAT_IDS
    assert b["inferred"]["hook_signal"] in prompts.SIGNAL_LIST
    assert b["inferred"]["pillar"] == "P"                     # valid field preserved


def test_generate_edit_brief_remerges_deterministic_cuts(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    words = [{"word": "So", "start_ms": 0, "end_ms": 200, "type": "filler"},
             {"word": "go", "start_ms": 220, "end_ms": 400}]
    llm_brief = {**main._mock_edit_brief(words, ""),
                 "cut_regions": [{"start_frame": 100, "end_frame": 110, "reason": "flub",
                                  "severity": "med", "quote": "oops"}]}
    monkeypatch.setattr(main, "anthropic_json", AsyncMock(return_value=llm_brief))
    b = asyncio.run(main._generate_edit_brief(words, transcript="x"))
    reasons = {c["reason"] for c in b["cut_regions"]}
    assert "filler" in reasons                                # deterministic re-merged
    assert "flub" in reasons                                  # LLM's editorial cut kept


def test_edit_brief_prompt_includes_custom_instructions():
    import prompts
    _, usr = prompts.edit_brief_prompt([{"word": "hi", "start_ms": 0, "end_ms": 200}],
                                       custom_instructions="make it punchy and funny",
                                       brand={"niche": "fitness"})
    assert "make it punchy and funny" in usr
    assert "[f0]" in usr                                      # frame-anchored transcript


# ---------------------------------------------------------------------------
# F-03 · Type→strategy guardrails.
# ---------------------------------------------------------------------------

def _brief(video_type, strategy="trim_only", order=None, hook_start=0):
    return {"video_type": video_type, "strategy": strategy, "restructure_order": order or [],
            "hook_candidates": [{"start_frame": hook_start, "end_frame": hook_start + 10,
                                 "quote": "x", "reason": "y", "signal": "curiosity"}]}


def test_strategy_listicle_never_restructures_even_if_asked():
    b = main._resolve_strategy(_brief("listicle", "restructure", [2, 0, 1], hook_start=900), total_frames=1000)
    assert b["strategy"] == "trim_only" and b["restructure_order"] == []
    assert b["pull_hook_forward"] is False                    # sequential structure preserved


def test_strategy_scripted_gets_hook_pull_forward_when_buried():
    b = main._resolve_strategy(_brief("scripted_talking_head", hook_start=900), total_frames=1000)
    assert b["strategy"] == "trim_only" and b["pull_hook_forward"] is True


def test_strategy_freestyle_restructures_only_when_hook_buried():
    buried = main._resolve_strategy(
        _brief("freestyle_rant", "restructure", [2, 0, 1], hook_start=900), total_frames=1000)
    assert buried["strategy"] == "restructure" and buried["restructure_order"] == [2, 0, 1]
    early = main._resolve_strategy(
        _brief("freestyle_rant", "restructure", [2, 0, 1], hook_start=50), total_frames=1000)
    assert early["strategy"] == "trim_only"                   # hook already up front → no reorder


def test_strategy_restructure_without_order_downgrades():
    b = main._resolve_strategy(_brief("story", "restructure", [], hook_start=900), total_frames=1000)
    assert b["strategy"] == "trim_only"                       # nothing to reorder


# ---------------------------------------------------------------------------
# F-04 · Two-phase clip job: analyze-first → brief_ready (keyless E2E).
# ---------------------------------------------------------------------------

def test_analyze_first_clip_reaches_brief_ready_keyless():
    r = client.post("/v1/clips", json={
        "source_url": "mock://take.mov", "analyze_first": True,
        "custom_instructions": "keep it high energy",
        "script": {"hook": "You're wrong about protein", "body": "Here's why.", "cta": "Follow."}})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "brief_ready" and body["mode"] == "mock"
    brief = body["edit_brief"]
    for k in prompts.EDIT_BRIEF_SCHEMA["required"]:
        assert k in brief
    assert body["toggles"].keys() >= {"broll", "punch_ins", "music"}
    # and the GET returns the same brief
    got = client.get(f"/v1/clips/{body['job_id']}").json()
    assert got["status"] == "brief_ready" and got["edit_brief"]["strategy"] == "trim_only"
    assert "toggles" in got


# ---------------------------------------------------------------------------
# F-06 · Confirm stage → edit (keyless E2E) + brief cuts fold into drops.
# ---------------------------------------------------------------------------

def test_analyze_confirm_reaches_mock_ready_and_uses_inferred_format():
    created = client.post("/v1/clips", json={
        "source_url": "mock://take.mov", "analyze_first": True,
        "script": {"hook": "Big claim", "body": "Proof here.", "cta": "Follow."}}).json()
    job_id = created["job_id"]
    r = client.post(f"/v1/clips/{job_id}/confirm",
                    json={"toggles": {"broll": False, "punch_ins": True, "music": False}}).json()
    assert r["status"] == "mock_ready" and len(r["clips"]) == 1        # ONE render, not N formats
    assert r["clips"][0]["format"] in prompts.FORMAT_IDS
    assert main._clip_jobs[job_id]["style"] in prompts.STYLES         # from brief.inferred


def test_confirm_without_brief_409():
    # a job that never analyzed can't be confirmed
    main._clip_jobs["no_brief_job"] = {"job_id": "no_brief_job", "status": "transcribing",
                                       "clips": [], "created_at": 0.0}
    r = client.post("/v1/clips/no_brief_job/confirm", json={"toggles": {}})
    assert r.status_code == 409


def test_run_edit_folds_brief_flub_cut_into_drops(monkeypatch):
    # A brief flub cut_region must become a drop in the confirmed EDL.
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")     # safe-default edl path
    words = [{"word": w, "start_ms": i * 300, "end_ms": i * 300 + 250}
             for i, w in enumerate("one two three four five six".split())]
    job = {"job_id": "edit1", "status": "editing", "style": "talking_head",
           "script": {"hook": "h", "body": "b", "cta": "c", "formatId": "myth-buster"},
           "brand": {}, "media_context": "", "source_url": "mock://x", "edit_prefs": {},
           "clips": [{"clip_id": "c1", "format": "myth-buster", "status": "queued"}],
           "words": words, "edl_history": [], "tweaks": [], "created_at": 0.0,
           "edit_brief": {"cut_regions": [{"start_frame": 15, "end_frame": 22, "reason": "flub",
                                           "severity": "high", "quote": "oops"}]}}
    main._clip_jobs["edit1"] = job
    asyncio.run(main._run_edit("edit1", words))
    drops = main._clip_jobs["edit1"]["edl"]["drops"]
    assert any(d["src_in"] == 15 and d["src_out"] == 22 for d in drops)   # flub cut applied


# ---------------------------------------------------------------------------
# F-08 · edl_prompt honors custom instructions + the brief; judge checks reorders.
# ---------------------------------------------------------------------------

def test_edl_prompt_includes_custom_instructions_and_brief():
    import prompts
    brief = {"hook_candidates": [{"quote": "open on THIS line", "start_frame": 100, "end_frame": 120,
                                  "reason": "x", "signal": "curiosity"}],
             "cut_regions": [{"start_frame": 50, "end_frame": 60, "reason": "flub",
                              "severity": "high", "quote": "oops"}],
             "strategy": "trim_only", "restructure_order": []}
    _, usr = prompts.edl_prompt("talking_head", [{"word": "hi", "start_ms": 0, "end_ms": 200}],
                                {"hook": "h", "body": "b", "cta": "c", "formatId": "myth-buster"},
                                {}, custom_instructions="cut all the ums and speed it up", brief=brief)
    assert "cut all the ums and speed it up" in usr
    assert "open on THIS line" in usr                        # hook to open on
    assert "50-60 (flub)" in usr                             # editorial cut surfaced


def test_edl_prompt_restructure_instruction_when_strategy_restructure():
    import prompts
    brief = {"hook_candidates": [{"quote": "q", "start_frame": 0, "end_frame": 5, "reason": "r",
                                  "signal": "curiosity"}],
             "cut_regions": [], "strategy": "restructure", "restructure_order": [2, 0, 1]}
    _, usr = prompts.edl_prompt("talking_head", [], {"formatId": "myth-buster"}, {}, brief=brief)
    assert "segment_order" in usr and "[2, 0, 1]" in usr


def test_edl_verify_prompt_has_reorder_coherence_check():
    import prompts
    sys, _ = prompts.edl_verify_prompt("talking_head", {"segments": []}, [])
    assert "through-line" in sys and "segment_order" in sys


# ---------------------------------------------------------------------------
# F-09 · Real analyze-video: 'live' ONLY when the real video was transcribed;
# otherwise 'live_structure' (honest pattern analysis), never a fake 'live'.
# ---------------------------------------------------------------------------

_YOUR_VERSION_JSON = ('{"hook_analysis":"a","structure_beats":["b"],"why_it_works":"c",'
                      '"suggestions":["d"],"your_version":{"title":"t","summary":"s","hook":"h",'
                      '"hookSignal":"curiosity","formatId":"myth-buster","body":"b","cta":"Follow.",'
                      '"shotPlan":[],"targetSeconds":30,"predictedScore":80}}')


def test_analyze_video_no_apify_is_live_structure(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "APIFY_KEY", "")           # can't fetch the real video

    async def fake(*a, **k):
        return _YOUR_VERSION_JSON
    monkeypatch.setattr(main, "anthropic", fake)
    r = client.post("/v1/analyze-video", json={"url": "https://tiktok.com/@x/video/1"}).json()
    assert r["mode"] == "live_structure"                 # honest: not this exact video


def test_analyze_video_real_transcript_is_live(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "APIFY_KEY", "ak")
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "sk")

    async def resolve(url):
        return "https://cdn/media.mp4"

    async def submit(u):
        return "tid"

    async def poll(tid, max_wait_s=None):
        return {"words": [{"word": "real", "start_ms": 0, "end_ms": 200},
                          {"word": "transcript", "start_ms": 220, "end_ms": 500}]}
    monkeypatch.setattr(main, "_resolve_post_media", resolve)
    monkeypatch.setattr(main, "_submit_transcription", submit)
    monkeypatch.setattr(main, "_poll_transcription", poll)
    cap = {}

    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
        cap["user"] = user
        return _YOUR_VERSION_JSON
    monkeypatch.setattr(main, "anthropic", fake)
    r = client.post("/v1/analyze-video", json={"url": "https://tiktok.com/@x/video/1"}).json()
    assert r["mode"] == "live"                            # real video was transcribed
    assert "real transcript" in cap["user"]              # analyzed the ACTUAL transcript


def test_analyze_video_fetch_failure_falls_back_to_structure(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "APIFY_KEY", "ak")
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "sk")

    async def resolve(url):
        return None                                       # couldn't resolve the media
    monkeypatch.setattr(main, "_resolve_post_media", resolve)

    async def fake(*a, **k):
        return _YOUR_VERSION_JSON
    monkeypatch.setattr(main, "anthropic", fake)
    r = client.post("/v1/analyze-video", json={"url": "https://instagram.com/reel/abc"}).json()
    assert r["mode"] == "live_structure"                 # fetch failed → NOT fake 'live'


# ---------------------------------------------------------------------------
# F-10 · EXIT GATE — full analyze-first walk + inferred dims are registerable.
# ---------------------------------------------------------------------------

def test_analyze_first_end_to_end_and_inferred_dims_register():
    # create → brief_ready → confirm → mock_ready
    created = client.post("/v1/clips", json={
        "source_url": "mock://take.mov", "analyze_first": True,
        "script": {"hook": "You're wrong about X", "body": "Here's the truth.", "cta": "Follow."}}).json()
    job_id = created["job_id"]
    brief = created["edit_brief"]
    confirmed = client.post(f"/v1/clips/{job_id}/confirm", json={"toggles": {}}).json()
    assert confirmed["status"] == "mock_ready"
    # the inferred dims are valid taxonomy values → registerable into the bandit
    inf = brief["inferred"]
    r = client.post("/v1/posts/register", json={
        "post_id": "f10-post", "creator_id": "f10", "style": inf["style"],
        "format_id": inf["format_id"], "hook_signal": inf["hook_signal"], "pillar": "P"}).json()
    assert r["status"] == "registered" and not r.get("dropped")   # nothing rejected as off-taxonomy
    main._post_registry.pop("f10-post", None)


# ---------------------------------------------------------------------------
# G-04 · Per-style capability map endpoint.
# ---------------------------------------------------------------------------

def _seed_coach_creator(cid, arm_key="hook_signal:contrarian", arm_sum_raw=12.0, arm_n=4,
                        baseline_raw=1.0, baseline_count=16):
    """Baseline settled posts + one arm — the shape _coach_insight reads."""
    for k in list(main._post_registry):
        if k.startswith(cid):
            main._post_registry.pop(k)
    for i in range(baseline_count):
        main._post_registry[f"{cid}-b{i}"] = {"creator_id": cid, "settled": True, "outcome_raw": baseline_raw}
    main._invalidate_creator_mean(cid)
    main._arm_stats[cid] = {arm_key: {"n": arm_n, "n_raw": arm_n, "sum_raw": arm_sum_raw, "alpha": 3.0, "beta": 1.5,
                                      "confidence": "early_read" if arm_n < 8 else "confirmed"}}
    main._arms_loaded.add(cid)


# ---------------------------------------------------------------------------
# P-01 · Coach insight: strongest grounded non-noise arm, else None (NO-INSIGHT gate).
# ---------------------------------------------------------------------------

def test_coach_insight_returns_driver(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    _seed_coach_creator("c_coach1", arm_sum_raw=12.0)     # 4 posts avg raw 3.0 vs baseline 1.0 → driver
    ins = asyncio.run(main._coach_insight("c_coach1"))
    assert ins and prompts.classify_arm_lift(ins["lift_pct"]) == "driver"
    assert ins["value"] == "contrarian"


def test_coach_insight_none_when_noise(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    _seed_coach_creator("c_coach2", arm_sum_raw=4.4)      # avg ~1.1 vs baseline 1.0 → noise band
    assert asyncio.run(main._coach_insight("c_coach2")) is None


def test_coach_insight_none_when_too_few_settled(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    _seed_coach_creator("c_coach3", baseline_count=1)     # only 1 settled post < COACH_MIN_SETTLED
    assert asyncio.run(main._coach_insight("c_coach3")) is None


# ---------------------------------------------------------------------------
# P-02 · GET /v1/coach/today — ≤1 honest card/day, LLM phrases handed numbers only.
# ---------------------------------------------------------------------------

def test_coach_today_keyless_driver_card_cites_real_lift(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._coach_shown.clear()
    _seed_coach_creator("c_coach4", arm_sum_raw=12.0)
    card = client.get("/v1/coach/today", params={"creator_id": "c_coach4"}).json()["card"]
    assert card and card["kind"] == "insight" and card["mode"] == "mock"
    ins = card["insight"]
    assert prompts.classify_arm_lift(ins["lift_pct"]) == "driver"
    assert f"{ins['lift_pct']:+d}%" in card["body"]        # the REAL lift, verbatim
    assert "contrarian" in (card["headline"] + card["body"]).lower()


def test_coach_today_second_call_same_day_gated(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._coach_shown.clear()
    _seed_coach_creator("c_coach5", arm_sum_raw=12.0)
    assert client.get("/v1/coach/today", params={"creator_id": "c_coach5"}).json()["card"]
    assert client.get("/v1/coach/today", params={"creator_id": "c_coach5"}).json()["card"] is None


def test_coach_today_zero_settled_setup_nudge_no_numbers(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._coach_shown.clear()
    for k in list(main._post_registry):                    # a truly cold creator
        if k.startswith("c_coach_cold"):
            main._post_registry.pop(k)
    main._arm_stats.pop("c_coach_cold", None)
    main._arms_loaded.add("c_coach_cold")
    card = client.get("/v1/coach/today", params={"creator_id": "c_coach_cold"}).json()["card"]
    assert card and card["kind"] == "setup"
    assert "%" not in card["body"] and not any(ch.isdigit() for ch in card["body"])


def test_coach_today_some_settled_but_no_signal_is_null(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._coach_shown.clear()
    _seed_coach_creator("c_coach6", arm_sum_raw=4.4)       # noise band → silence, not a nudge
    assert client.get("/v1/coach/today", params={"creator_id": "c_coach6"}).json()["card"] is None


# ---------------------------------------------------------------------------
# P-03 · coach_last_shown persistence: mark → gate; stale stamp re-opens; DB rehydrate.
# ---------------------------------------------------------------------------

def test_coach_mark_shown_persists_and_gates(monkeypatch):
    from datetime import datetime, timezone
    calls = []

    class FakeSB:
        async def upsert_creator(self, cid, fields):
            calls.append((cid, dict(fields)))
            return True
    monkeypatch.setattr(main, "_supabase_client", FakeSB())
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._coach_shown.clear()
    asyncio.run(main._coach_mark_shown("c_coach8"))
    assert calls and "coach_last_shown" in calls[0][1]     # best-effort durable stamp
    assert client.get("/v1/coach/today", params={"creator_id": "c_coach8"}).json()["card"] is None


def test_coach_stale_stamp_reopens_card(monkeypatch):
    from datetime import datetime, timezone, timedelta
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._coach_shown.clear()
    _seed_coach_creator("c_coach9", arm_sum_raw=12.0)
    main._coach_shown["c_coach9"] = datetime.now(timezone.utc) - timedelta(hours=25)
    assert client.get("/v1/coach/today", params={"creator_id": "c_coach9"}).json()["card"]


def test_coach_gate_rehydrates_from_db_on_memory_miss(monkeypatch):
    from datetime import datetime, timezone

    class FakeSB:
        async def load_creator(self, cid):
            return {"creator_id": cid,
                    "coach_last_shown": datetime.now(timezone.utc).isoformat()}

        async def upsert_creator(self, cid, fields):
            return True
    monkeypatch.setattr(main, "_supabase_client", FakeSB())
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._coach_shown.clear()                              # fresh process, DB has the stamp
    assert client.get("/v1/coach/today", params={"creator_id": "c_coach10"}).json()["card"] is None
    assert "c_coach10" in main._coach_shown                # rehydrated into memory


def test_coach_today_llm_failure_degrades_to_template(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    main._coach_shown.clear()
    _seed_coach_creator("c_coach7", arm_sum_raw=12.0)

    async def boom(*a, **k):
        raise main.HTTPException(502, "llm down")
    monkeypatch.setattr(main, "anthropic", boom)
    card = client.get("/v1/coach/today", params={"creator_id": "c_coach7"}).json()["card"]
    assert card and card["kind"] == "insight" and card["mode"] == "mock"
    assert f"{card['insight']['lift_pct']:+d}%" in card["body"]


# ---------------------------------------------------------------------------
# AF (audit fixes) · numbers shown to creators are computed, never LLM-invented.
# ---------------------------------------------------------------------------

def test_attribution_overwrites_llm_numbers_with_computed_lift(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "AI_QUALITY", True)
    _seed_coach_creator("c_attr1", arm_sum_raw=12.0)      # real computed driver

    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
        return json.dumps({"dimension": "hook_signal", "arm_value": "contrarian",
                           "lift_pct": 999, "band": "driver", "confidence": "confirmed",
                           "verdict": "Contrarian ran +999% above your average."})
    monkeypatch.setattr(main, "anthropic", fake)
    out = asyncio.run(main._attribute_settled_post("c_attr1", {"hook_signal": "contrarian"}))
    real = next(a for a in asyncio.run(main._arms_for_prompt("c_attr1"))
                if a["value"] == "contrarian")
    assert out["lift_pct"] == int(real["lift_pct"]) != 999   # Python owns the number
    assert "999" not in out["verdict"]                        # drifted verdict replaced
    assert out["band"] == prompts.classify_arm_lift(int(real["lift_pct"]))


def test_coach_today_concurrent_requests_get_one_card(monkeypatch):
    """AF-7: the daily slot is claimed synchronously — parallel requests during the
    insight awaits must not each get a card."""
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._coach_shown.clear()
    _seed_coach_creator("c_race1", arm_sum_raw=12.0)

    real_insight = main._coach_insight

    async def yielding_insight(cid):
        await asyncio.sleep(0)                              # force interleaving
        return await real_insight(cid)
    monkeypatch.setattr(main, "_coach_insight", yielding_insight)

    async def both():
        return await asyncio.gather(main.coach_today("c_race1"), main.coach_today("c_race1"))
    a, b = asyncio.run(both())
    assert sum(1 for r in (a, b) if r["card"]) == 1


def test_coach_today_silence_does_not_burn_the_daily_slot(monkeypatch):
    """AF-7: a no-claim (noise-only) day releases the claim — a real signal later the
    same day can still produce the card."""
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._coach_shown.clear()
    _seed_coach_creator("c_race2", arm_sum_raw=4.4)         # noise -> silence
    assert client.get("/v1/coach/today", params={"creator_id": "c_race2"}).json()["card"] is None
    assert "c_race2" not in main._coach_shown               # claim released


def test_remove_broll_invalid_range_rejected_not_wipe_all():
    """AF-5: a provided-but-invalid range must not silently remove every b-roll cue."""
    from app.edl import apply_edl_ops
    edl = {"style": "broll_cutaway", "format_id": "myth-buster",
           "segments": [{"src_in": 0, "src_out": 720}], "drops": [], "captions": [],
           "overlays": [], "layout": {"style": "broll_cutaway"},
           "broll": [{"src_in": 10, "src_out": 40, "query": "gym"},
                     {"src_in": 300, "src_out": 360, "query": "meal"}]}
    out, res = apply_edl_ops(edl, [{"type": "remove_broll",
                                    "start_frame": 500, "end_frame": 100}], [])
    assert not res[0]["applied"] and "range" in res[0]["reason"]
    assert len(out["broll"]) == 2                          # nothing was wiped
    # the no-range form still removes all (unchanged semantics)
    out2, res2 = apply_edl_ops(edl, [{"type": "remove_broll"}], [])
    assert res2[0]["applied"] and out2["broll"] == []


def test_preview_tweak_does_not_commit_the_edl(monkeypatch):
    """AF-6: preview=1 renders the CANDIDATE, never installs it — Preview-then-Apply
    used to apply everything twice, and Cancel-after-preview silently kept the edits."""
    job_id = seed_clip_job(script=_FLUFFY_SCRIPT)
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    before = copy.deepcopy(main._clip_jobs[job_id]["edl"])
    r = client.post(f"/v1/clips/{job_id}/tweak?preview=1",
                    json={"clip_id": clip_id,
                          "ops": [{"type": "trim_start", "frames": 30}]}).json()
    assert r["changed"] is True                            # the preview has content...
    assert main._clip_jobs[job_id]["edl"] == before        # ...but NOTHING committed
    assert main._clip_jobs[job_id]["tweaks"] == []         # and no history pollution
    # the same ops applied for real afterwards land exactly once
    r2 = client.post(f"/v1/clips/{job_id}/tweak",
                     json={"clip_id": clip_id,
                           "ops": [{"type": "trim_start", "frames": 30}]}).json()
    assert r2["changed"] is True
    assert main._clip_jobs[job_id]["edl"]["segments"][0]["src_in"] == before["segments"][0]["src_in"] + 30


def test_defer_render_commits_without_render(monkeypatch):
    """AF-6: defer_render=1 (the editor's split) commits the EDL but spends no render."""
    job_id = seed_clip_job(script=_FLUFFY_SCRIPT)
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    main._clip_jobs[job_id]["status"] = "ready"
    monkeypatch.setattr(main, "REMOTION_SERVE_URL", "https://serve.example")
    monkeypatch.setattr(main, "REMOTION_ACCESS_KEY", "ak")
    monkeypatch.setattr(main, "REMOTION_FUNCTION_NAME", "fn")
    seg0 = main._clip_jobs[job_id]["edl"]["segments"][0]
    mid = (seg0["src_in"] + seg0["src_out"]) // 2
    r = client.post(f"/v1/clips/{job_id}/tweak?defer_render=1",
                    json={"clip_id": clip_id,
                          "ops": [{"type": "split_segment", "index": 0, "at_frame": mid}]}).json()
    assert r["changed"] is True and r["needs_render"] is False
    assert len(main._clip_jobs[job_id]["edl"]["segments"]) == 2   # committed
    assert main._clip_jobs[job_id]["clips"][0]["status"] == "ready"  # no render started
    main._clip_jobs[job_id]["status"] = "mock_ready"


def test_restore_clip_job_concurrent_restorers_share_one_dict(monkeypatch):
    """AF-3: two coroutines both missing memory must end up mutating ONE job dict."""
    state1, state2 = {"job_id": "j-af3", "status": "ready", "clips": []}, \
                     {"job_id": "j-af3", "status": "ready", "clips": []}
    served = [state1, state2]

    class FakeSB:
        async def load_clip_job(self, jid):
            await asyncio.sleep(0)          # yield → interleave the two restorers
            return served.pop(0)
    monkeypatch.setattr(main, "_supabase_client", FakeSB())
    main._clip_jobs.pop("j-af3", None)

    async def both():
        return await asyncio.gather(main._restore_clip_job("j-af3"),
                                    main._restore_clip_job("j-af3"))
    a, b = asyncio.run(both())
    assert a is b and main._clip_jobs["j-af3"] is a


def test_confirm_409_while_editing():
    """AF-4: a second confirm during an in-flight edit is rejected, not double-run."""
    job_id = seed_clip_job(script=_FLUFFY_SCRIPT)
    job = main._clip_jobs[job_id]
    job["edit_brief"] = {"video_type": "scripted_talking_head", "inferred": {}}
    job["status"] = "editing"
    r = client.post(f"/v1/clips/{job_id}/confirm", json={"toggles": {}})
    assert r.status_code == 409 and r.json()["detail"] == "confirm_in_progress"
    job["status"] = "mock_ready"


def test_arm_lift_partial_raw_history_is_ungrounded():
    """AF-2: an arm whose sum_raw covers FEWER settles than n (legacy rows backfilled by
    sum_raw's DEFAULT 0.0) must not divide a partial sum by the full n — that fabricated
    large negative 'grounded' lifts. n_raw is the honest denominator."""
    # 6 historic settles the composite never saw + 1 real settle exactly at the mean:
    legacy = {"n": 7, "sum_raw": 1.0, "n_raw": 0, "confidence": "early_read"}
    lift, grounded = main._arm_lift(legacy, 1.0)
    assert not grounded and lift == 0                      # no n_raw → no claim
    honest = {"n": 7, "sum_raw": 1.0, "n_raw": 1, "confidence": "early_read"}
    lift, grounded = main._arm_lift(honest, 1.0)
    assert grounded and lift == 0                          # 1 settle at the mean → 0%


def test_update_arm_accumulates_n_raw(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    cid = "c_nraw"
    main._arm_stats.pop(cid, None)
    main._arms_loaded.add(cid)
    asyncio.run(main._update_arm(cid, "style:talking_head", 1.0, raw=2.0))
    asyncio.run(main._update_arm(cid, "style:talking_head", 0.0, raw=None))  # no raw settle
    s = main._arm_stats[cid]["style:talking_head"]
    assert s["n"] == 2 and s["n_raw"] == 1 and s["sum_raw"] == 2.0


def test_attribution_llm_none_never_echoes_raw_fields(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "AI_QUALITY", True)
    _seed_coach_creator("c_attr2", arm_sum_raw=12.0)

    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
        return json.dumps({"dimension": "none", "arm_value": "", "lift_pct": 777,
                           "band": "driver", "verdict": "hmm", "extra_key": "leak"})
    monkeypatch.setattr(main, "anthropic", fake)
    out = asyncio.run(main._attribute_settled_post("c_attr2", {"hook_signal": "contrarian"}))
    assert out["lift_pct"] == 0 and out["band"] == "noise" and "extra_key" not in out


# ---------------------------------------------------------------------------
# P-06 · Completeness sweep nits.
# ---------------------------------------------------------------------------

def test_every_route_survives_keyless(monkeypatch):
    """P-07 exit gate: every /v1 route answers keyless without a 500 — a missing
    keyless-mock fallback is a bug by contract (PLAN §0)."""
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    for route in main.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith(("/v1", "/healthz", "/readyz")):
            continue
        url = path.replace("{job_id}", "sweep-nonexistent")
        for m in methods & {"GET", "POST"}:
            r = client.get(url) if m == "GET" else client.post(url, json={})
            assert r.status_code < 500, f"{m} {path} -> {r.status_code} keyless"


def test_digest_swept_job_is_410_not_404():
    main._expired_job_ids["digest-swept-1"] = time.time()
    assert client.get("/v1/onboarding/digest/digest-swept-1").status_code == 410
    assert client.get("/v1/onboarding/digest/digest-never-existed").status_code == 404


def test_creator_from_bearer_dead_code_removed():
    assert not hasattr(main, "_creator_from_bearer")


def test_learned_insights_rank_by_lift_magnitude(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    cid = "c_maglift"
    for k in list(main._post_registry):
        if k.startswith(cid):
            main._post_registry.pop(k)
    for i in range(16):
        main._post_registry[f"{cid}-b{i}"] = {"creator_id": cid, "settled": True, "outcome_raw": 1.0}
    main._invalidate_creator_mean(cid)
    main._arm_stats[cid] = {
        # a weak winner (+~10%) and a strong error (-~60%): the error is the bigger insight
        "style:talking_head": {"n": 6, "n_raw": 6, "sum_raw": 6.6, "alpha": 3.0, "beta": 1.5,
                               "confidence": "early_read"},
        "hook_signal:hype": {"n": 6, "n_raw": 6, "sum_raw": 2.4, "alpha": 1.0, "beta": 3.0,
                             "confidence": "early_read"},
    }
    main._arms_loaded.add(cid)
    ins = client.get(f"/v1/insights/learned?creator_id={cid}").json()["insights"]
    assert ins and ins[0]["lift_pct"] < 0                   # the -60% error ranks first


# ---------------------------------------------------------------------------
# P-05 · GET /v1/clips/{id}/suggested-edits — one-tap chips, style-gated, honest.
# ---------------------------------------------------------------------------

_FLUFFY_SCRIPT = {"hook": "Um so basically stop overthinking",
                  "body": "Um you know do the like simple thing daily",
                  "cta": "Follow", "formatId": "myth-buster"}


def test_suggested_edits_chips_for_talking_head():
    job_id = seed_clip_job(script=_FLUFFY_SCRIPT)
    chips = client.get(f"/v1/clips/{job_id}/suggested-edits").json()["chips"]
    assert 2 <= len(chips) <= 4
    kinds = {c["kind"] for c in chips}
    assert "remove_fluff" in kinds and "punch_in" in kinds
    for c in chips:
        assert c["label"] and c["ops"] and all(o["type"] in main.TWEAK_OP_TYPES for o in c["ops"])


def test_suggested_edits_no_punch_in_chip_for_fast_cuts():
    job_id = seed_clip_job(script=_FLUFFY_SCRIPT, style="fast_cuts")
    chips = client.get(f"/v1/clips/{job_id}/suggested-edits").json()["chips"]
    kinds = {c["kind"] for c in chips}
    assert "punch_in" not in kinds                          # style can't render it
    assert "remove_fluff" in kinds                          # style-agnostic chip survives


def test_suggested_edit_chip_round_trips_and_is_not_reoffered():
    job_id = seed_clip_job(script=_FLUFFY_SCRIPT)
    clip_id = main._clip_jobs[job_id]["clips"][0]["clip_id"]
    chip = next(c for c in client.get(f"/v1/clips/{job_id}/suggested-edits").json()["chips"]
                if c["kind"] == "remove_fluff")
    r = client.post(f"/v1/clips/{job_id}/tweak",
                    json={"clip_id": clip_id, "instruction": "", "ops": chip["ops"]})
    assert r.status_code == 200 and r.json()["changed"] is True
    assert not r.json()["skipped"]                          # every chip op applied cleanly
    chips2 = client.get(f"/v1/clips/{job_id}/suggested-edits").json()["chips"]
    assert all(c["ops"] != chip["ops"] for c in chips2)     # applied chip isn't re-offered


def test_suggested_edits_unknown_job_404_and_no_edl_empty():
    assert client.get("/v1/clips/nope/suggested-edits").status_code == 404
    job_id = seed_clip_job(script=_FLUFFY_SCRIPT, edl=None)
    assert client.get(f"/v1/clips/{job_id}/suggested-edits").json()["chips"] == []


# ---------------------------------------------------------------------------
# P-04 · GET /v1/suggestions/next-idea — one idea brief, grounded or niche-framed.
# ---------------------------------------------------------------------------

def test_next_idea_keyless_is_shaped(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    idea = client.get("/v1/suggestions/next-idea",
                      params={"creator_id": "c_idea1", "niche": "fitness"}).json()["idea"]
    assert idea["title"] and idea["hook"] and len(idea["beats"]) >= 3
    assert idea["mode"] == "mock" and idea["grounding"]


def test_next_idea_cites_grounded_driver_verbatim(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    _seed_coach_creator("c_idea2", arm_sum_raw=12.0)
    idea = client.get("/v1/suggestions/next-idea",
                      params={"creator_id": "c_idea2"}).json()["idea"]
    ins = asyncio.run(main._coach_insight("c_idea2"))
    assert "contrarian" in idea["grounding"].lower()       # references the real strength...
    assert f"{ins['lift_pct']:+d}%" in idea["grounding"]   # ...with the REAL lift, verbatim


def test_next_idea_cold_creator_uses_niche_framing_no_perf_claim(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    main._arm_stats.pop("c_idea3", None)
    main._arms_loaded.add("c_idea3")
    idea = client.get("/v1/suggestions/next-idea",
                      params={"creator_id": "c_idea3", "niche": "finance"}).json()["idea"]
    assert "niche" in idea["grounding"].lower()            # framed as a prior, not their data
    assert "vs your average" not in idea["grounding"]      # no fabricated personal claim


def test_next_idea_llm_keeps_deterministic_grounding(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    _seed_coach_creator("c_idea4", arm_sum_raw=12.0)

    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None, schema=None):
        return json.dumps({"title": "LLM title", "hook": "LLM hook",
                           "beats": ["b1", "b2", "b3"],
                           "grounding": "your posts run +900% (invented)"})
    monkeypatch.setattr(main, "anthropic", fake)
    idea = client.get("/v1/suggestions/next-idea",
                      params={"creator_id": "c_idea4"}).json()["idea"]
    assert idea["mode"] == "live" and idea["title"] == "LLM title"
    assert "+900%" not in idea["grounding"]                # LLM cannot rewrite the grounding


def test_editor_capabilities_endpoint():
    caps = client.get("/v1/editor/capabilities").json()["capabilities"]
    assert caps["talking_head"]["punch_ins"] is True and caps["fast_cuts"]["punch_ins"] is False
    assert caps["broll_cutaway"]["broll"] is True and caps["talking_head"]["broll"] is False
    assert caps["green_screen"]["text_cards"] is True
    assert all(c["music"] and c["captions"] for c in caps.values())   # style-agnostic ops


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


# ---------------------------------------------------------------------------
# A1 · Cold-start niche priors
# ---------------------------------------------------------------------------

def test_match_niche_maps_freeform_to_slug():
    assert prompts.match_niche("fitness coaching for busy dads") == "fitness"
    assert prompts.match_niche("personal finance & investing") == "finance"
    assert prompts.match_niche("AI tools for developers") == "tech"
    assert prompts.match_niche("skincare & derm tips") == "beauty"
    assert prompts.match_niche("") == "default"
    assert prompts.match_niche("underwater basket weaving") == "default"


def test_niche_prior_block_is_keyless_and_niche_specific():
    blk = prompts.niche_prior_block("fitness")
    assert "NICHE BASELINE" in blk
    # names real hook signals / formats from the fitness prior
    p = prompts.niche_priors_for("fitness")
    assert p["signals"][0] in blk and p["formats"][0] in blk
    # frames as a bias that yields to real data, never as a fact about the creator
    assert "override" in blk.lower()
    # unknown niche still returns a usable default block, never empty/crash
    assert "NICHE BASELINE" in prompts.niche_prior_block("")


def test_niche_priors_reference_only_valid_arm_values():
    valid_sig = set(prompts.SIGNAL_LIST)
    valid_fmt = set(prompts.FORMAT_IDS)
    valid_sty = set(prompts.STYLES.keys())
    for slug, p in prompts.NICHE_PRIORS.items():
        assert set(p["signals"]) <= valid_sig, f"{slug} bad signal"
        assert set(p["formats"]) <= valid_fmt, f"{slug} bad format"
        assert set(p["styles"]) <= valid_sty, f"{slug} bad style"
        assert p["note"].strip()


def test_scripts_prompt_uses_niche_prior_when_cold_then_yields_to_learning():
    brand = {"niche": "fitness", "what_you_do": "coach", "audience": "busy pros", "known_for": "form"}
    pillar = {"name": "Myth-busting", "summary": "bust fitness myths", "angle": "no-nonsense"}
    # cold: no arm data -> niche baseline present
    _sys, user_cold = prompts.scripts_prompt(brand, pillar, "talking_head", 2, arm_stats=[])
    assert "NICHE BASELINE" in user_cold
    # warm: real arm data present -> learning_block wins, no niche baseline
    arms = [{"lift_pct": 40, "label": "contrarian hook: +40% vs your average", "confidence": "confirmed"}]
    _sys2, user_warm = prompts.scripts_prompt(brand, pillar, "talking_head", 2, arm_stats=arms)
    assert "CREATOR PERFORMANCE DATA" in user_warm
    assert "NICHE BASELINE" not in user_warm


def test_recommendations_cold_path_is_niche_aware():
    b = client.get("/v1/recommendations", params={"niche": "personal finance", "creator_id": "cold_fin_user"}).json()
    assert b["mode"] == "mock" and len(b["arms"]) == 3
    blob = json.dumps(b["arms"]).lower()
    # niche-aware reason mentions the niche + is honest about being a baseline
    assert "personal finance" in blob and "baseline" in blob
    # styles come from the finance prior (talking_head/green_screen/faceless), not the old
    # hardcoded fast_cuts mock
    fin_styles = set(prompts.niche_priors_for("personal finance")["styles"]) | {"talking_head", "green_screen"}
    assert all(a["style"] in fin_styles for a in b["arms"])


# ---------------------------------------------------------------------------
# A1 · Beta-seeding (niche prior into the bandit) — additive, default-preserving
# ---------------------------------------------------------------------------

def test_update_arm_default_prior_matches_old_math():
    # No niche / no remembered niche -> exactly the pre-seeding uniform Beta(1,1) math.
    main._arm_stats.pop("regr_user", None)
    main._creator_niche.pop("regr_user", None)
    asyncio.run(main._update_arm("regr_user", "style:talking_head", 0.8))
    s = main._arm_stats["regr_user"]["style:talking_head"]
    assert s["n"] == 1 and abs(s["sum_y"] - 0.8) < 1e-9
    assert abs(s["alpha"] - 1.8) < 1e-9 and abs(s["beta"] - 1.2) < 1e-9
    assert abs(s["effect"] - (0.8 + main.KAPPA * 0.5) / (1 + main.KAPPA)) < 1e-9


def test_niche_prior_for_arm_seeds_preferred_only():
    a, b = main._niche_prior_for_arm("hook_signal:contrarian", "fitness")  # fitness signal #1
    assert a > b > 1.0                                                     # optimistic prior
    assert main._niche_prior_for_arm("hook_signal:narrative", "fitness") == (1.0, 1.0)  # not preferred
    assert main._niche_prior_for_arm("pillar:whatever", "fitness") == (1.0, 1.0)        # pillars neutral
    assert main._niche_prior_for_arm("style:talking_head", "") == (1.0, 1.0)            # unknown niche


def test_update_arm_niche_seeds_fresh_arm():
    main._arm_stats.pop("fit_user", None)
    main._creator_niche.pop("fit_user", None)
    asyncio.run(main._update_arm("fit_user", "hook_signal:contrarian", 0.5, niche="fitness"))
    s = main._arm_stats["fit_user"]["hook_signal:contrarian"]
    assert s["prior_alpha"] > s["prior_beta"]                       # niche-optimistic
    assert abs(s["alpha"] - (s["prior_alpha"] + s["sum_y"])) < 1e-9  # alpha = prior + observed
    assert abs(s["beta"] - (s["prior_beta"] + (s["n"] - s["sum_y"]))) < 1e-9


def test_update_arm_uses_remembered_niche():
    main._arm_stats.pop("rem_user", None)
    main._creator_niche["rem_user"] = "personal finance"           # remembered, not passed
    asyncio.run(main._update_arm("rem_user", "format_id:listicle", 0.5))
    assert main._arm_stats["rem_user"]["format_id:listicle"]["prior_alpha"] > 1.0
    main._creator_niche.pop("rem_user", None)


def test_thompson_sample_unseen_arm_niche_seeded_and_neutral_default():
    # Unknown niche: unseen arms fall back to neutral (1,1) — unchanged behavior.
    main._arm_stats.pop("ts_user", None)
    scored = main._thompson_sample("ts_user", ["style:talking_head", "style:faceless"])
    assert len(scored) == 2 and all(0.0 <= v <= 1.0 for _, v in scored)
    # Known niche seeds the preferred arm's prior above the non-preferred one (mean check,
    # not a sampled-value check, to stay deterministic).
    a_pref, b_pref = main._niche_prior_for_arm("style:talking_head", "fitness")
    a_neu, b_neu = main._niche_prior_for_arm("style:faceless", "fitness")
    assert a_pref / (a_pref + b_pref) > a_neu / (a_neu + b_neu)


# ---------------------------------------------------------------------------
# A2 · Script scorer (all_scores.txt) — creator-facing, NOT a bandit reward
# ---------------------------------------------------------------------------

def test_score_prompt_covers_all_three_axes():
    sysm, usr = prompts.score_script_prompt("You're doing cardio wrong.", "Here's the fix...", "talking_head")
    for axis in ("HOOK", "FLUFF", "SATISFACTION"):
        assert axis in sysm
    assert "cardio" in usr


def test_score_keyless_is_deterministic_and_bounded():
    payload = {"hook": "You're eating protein wrong. Here's the 30g rule.",
               "body": "Everyone maxes protein at dinner. Wrong. Spread 30g across 4 meals and you actually "
                       "absorb it. I tested it for 42 days. Try it and save this."}
    a = client.post("/v1/score", json=payload).json()
    b = client.post("/v1/score", json=payload).json()
    assert a == b                                        # determinism
    assert a["mode"] == "mock"
    assert a["hook"] in ("High", "Mid", "Low") and a["fluff"] in ("High", "Mid", "Low")
    assert 0 <= a["overall"] <= 100 and a["fix"]


def test_score_rewards_strong_script_over_weak():
    strong = client.post("/v1/score", json={
        "hook": "Nobody tells you this about sleep. It's costing you 2 hours a day.",
        "body": "Here's the fix: cut caffeine after 2pm and your deep sleep doubles. I tracked 30 nights. "
                "Try it tonight and save this."}).json()
    weak = client.post("/v1/score", json={
        "hook": "Hey guys so today I wanted to talk a little bit about sleep and stuff?",
        "body": ("So basically sleep is really important and there are a lot of things you can do and I've "
                 "been thinking about it a lot lately and honestly it's just something that matters and you "
                 "should probably try to get more of it if you can because it helps with a lot of things in "
                 "your day and your mood and your energy and just generally feeling good overall you know.")}).json()
    assert strong["overall"] > weak["overall"]


# ---------------------------------------------------------------------------
# A3 · Un-cosmetic the loop + confidence bands + attribution
# ---------------------------------------------------------------------------

def test_classify_arm_lift_bands():
    assert prompts.classify_arm_lift(80) == "driver"     # 1.8x baseline
    assert prompts.classify_arm_lift(120) == "driver"
    assert prompts.classify_arm_lift(79) == "noise"
    assert prompts.classify_arm_lift(0) == "noise"
    assert prompts.classify_arm_lift(-35) == "error"     # 0.65x baseline
    assert prompts.classify_arm_lift(-34) == "noise"


def test_learning_block_renders_counts_confidence_and_exploration():
    confirmed = [{"lift_pct": 40, "label": "contrarian hook: +40% vs your average",
                  "confidence": "confirmed", "n": 10}]
    blk = prompts.learning_block(confirmed)
    assert "n=10 settled" in blk and "confirmed" in blk and "Exploit the confirmed" in blk
    early = [{"lift_pct": 22, "label": "listicle format: +22% vs your average",
              "confidence": "early_read", "n": 5}]
    assert "EARLY READS" in prompts.learning_block(early)
    assert prompts.learning_block([]) == ""


def test_settled_arm_reaches_learning_block_end_to_end():
    # The loop is only "cosmetic" below n>=4; prove that at n>=4 a creator's own arm
    # is shaped and actually reaches the generation context block.
    main._arm_stats.pop("loop_user", None)
    main._creator_niche.pop("loop_user", None)
    main._arms_loaded.discard("loop_user")
    _seed_baseline("loop_user", mean=1.0)               # personal baseline 1.0
    for _ in range(5):                                   # 5 settled contrarian posts at raw 2.4
        asyncio.run(main._update_arm("loop_user", "hook_signal:contrarian", 0.9, 2.4))
    shaped = asyncio.run(main._arms_for_prompt("loop_user"))
    assert shaped and shaped[0]["dimension"] == "hook_signal" and shaped[0]["value"] == "contrarian"
    assert shaped[0]["confidence"] in ("early_read", "confirmed") and shaped[0]["n"] == 5
    assert shaped[0]["has_lift"] is True                 # grounded in the raw baseline
    blk = prompts.learning_block(shaped)
    assert "contrarian hook" in blk and "n=5 settled" in blk


def test_attribute_from_arms_picks_driver_or_none():
    driver = [{"dimension": "hook_signal", "value": "contrarian", "lift_pct": 90,
               "label": "contrarian hook: +90% vs your average", "confidence": "confirmed"}]
    a = prompts.attribute_from_arms(driver)
    assert a["dimension"] == "hook_signal" and a["band"] == "driver" and a["lift_pct"] == 90
    noise = [{"dimension": "style", "value": "faceless", "lift_pct": 10,
              "label": "faceless style: +10%", "confidence": "early_read"}]
    assert prompts.attribute_from_arms(noise)["dimension"] == "none"
    assert prompts.attribute_from_arms([])["dimension"] == "none"


def test_insights_learned_carries_band():
    main._arm_stats.pop("band_user", None)
    main._creator_niche.pop("band_user", None)
    main._arms_loaded.discard("band_user")
    _seed_baseline("band_user", mean=1.0)               # personal baseline 1.0
    for _ in range(5):                                   # listicle far above baseline → a driver
        asyncio.run(main._update_arm("band_user", "format_id:listicle", 0.9, 2.4))
    b = client.get("/v1/insights/learned", params={"creator_id": "band_user"}).json()
    assert b["insights"] and all("band" in ins for ins in b["insights"])


# ---------------------------------------------------------------------------
# B1 · Real-reward ingest (creator-confirmed metrics, per-goal weighting)
# ---------------------------------------------------------------------------

def test_compute_y_respects_goal_weighting():
    m = {"reach": 1000, "likes": 100, "comments": 300, "shares": 10, "saves": 20,
         "follows_gained": 5, "avg_watch_pct": 0.3}
    # a comment-heavy post scores higher for a "clients" goal than for "grow"
    assert main._compute_y(m, "clients") > main._compute_y(m, "grow")
    # unknown goal falls back to grow exactly
    assert main._compute_y(m, "bogus") == main._compute_y(m, "grow")


def test_ingest_uses_goal_niche_and_returns_attribution():
    main._arm_stats.pop("b1_user", None)
    main._creator_niche.pop("b1_user", None)
    client.post("/v1/posts/register", json={
        "post_id": "b1p1", "creator_id": "b1_user", "pillar": "Myth-busting",
        "style": "talking_head", "format_id": "myth-buster", "hook_signal": "contrarian",
        "niche": "fitness"})
    r = client.post("/v1/metrics/ingest", json={
        "post_id": "b1p1", "creator_id": "b1_user", "reach": 1000, "likes": 100,
        "comments": 300, "saves": 40, "shares": 20, "follows_gained": 8,
        "avg_watch_pct": 0.5, "goal": "clients"}).json()
    assert r["status"] == "ingested" and r["goal"] == "clients"
    assert r["attribution"]["dimension"] in ("hook_signal", "style", "format_id", "pillar", "none")
    s = main._arm_stats["b1_user"]["hook_signal:contrarian"]
    assert s["n"] == 1                          # the settled post moved the arm
    assert s["prior_alpha"] > 1.0               # and it was niche-seeded (contrarian is a fitness prior)


# ---------------------------------------------------------------------------
# A-01 · Unregistered-post ingest honesty: never fabricate a settled ghost row,
# never claim "ingested" when zero arms moved, and never guess when the DB
# couldn't answer (absent row != failed lookup).
# ---------------------------------------------------------------------------

def test_ingest_unregistered_post_updates_nothing(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    main._post_registry.pop("p_ghost", None)
    main._arm_stats.pop("c_ghost", None)
    r = client.post("/v1/metrics/ingest", json={
        "post_id": "p_ghost", "creator_id": "c_ghost", "reach": 500, "likes": 50,
        "saves": 10, "shares": 5, "avg_watch_pct": 0.6, "follows_gained": 8})
    body = r.json()
    assert body["status"] == "unregistered"
    assert "p_ghost" not in main._post_registry            # no ghost registry row
    assert "c_ghost" not in main._arm_stats                # zero arm updates


def test_ingest_distinguishes_db_absent_from_db_failure(monkeypatch):
    from unittest.mock import AsyncMock
    from supabase_persistence import UNAVAILABLE
    fake = SupabaseClientStub()
    fake.load_post = AsyncMock(return_value=None)          # DB answered: no such row
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._post_registry.pop("p_absent", None)
    r = client.post("/v1/metrics/ingest", json={
        "post_id": "p_absent", "creator_id": "c_a01", "reach": 100})
    assert r.json()["status"] == "unregistered"
    fake.load_post = AsyncMock(return_value=UNAVAILABLE)   # DB could not answer
    main._post_registry.pop("p_unavail", None)
    r = client.post("/v1/metrics/ingest", json={
        "post_id": "p_unavail", "creator_id": "c_a01", "reach": 100})
    assert r.json()["status"] == "retry_later"
    assert "p_unavail" not in main._post_registry          # no state change on failure


def test_ingest_db_exception_returns_retry_later(monkeypatch):
    from unittest.mock import AsyncMock
    fake = SupabaseClientStub()
    fake.load_post = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._post_registry.pop("p_boom", None)
    r = client.post("/v1/metrics/ingest", json={
        "post_id": "p_boom", "creator_id": "c_a01", "reach": 100})
    assert r.json()["status"] == "retry_later"
    assert "p_boom" not in main._post_registry


def test_load_post_unavailable_vs_absent(monkeypatch):
    """load_post's contract: UNAVAILABLE when the DB couldn't answer, None only
    when the DB answered and the row is genuinely absent."""
    from unittest.mock import AsyncMock
    from supabase_persistence import SupabaseClient, UNAVAILABLE
    c = SupabaseClient("https://x.supabase.co", "k")
    c._request = AsyncMock(return_value=None)              # transport gave up
    assert asyncio.run(c.load_post("p1")) is UNAVAILABLE

    class _Absent:                                          # 200 with empty rows
        status_code = 200
        def json(self): return []
    c._request = AsyncMock(return_value=_Absent())
    assert asyncio.run(c.load_post("p1")) is None

    class _Denied:                                          # 4xx — DB didn't answer the question
        status_code = 401
        def json(self): return {"message": "denied"}
    c._request = AsyncMock(return_value=_Denied())
    assert asyncio.run(c.load_post("p1")) is UNAVAILABLE


# ---------------------------------------------------------------------------
# A-02 · Settle idempotency + race: a post settles exactly once, arms move once,
# even under concurrent or cross-instance ingests.
# ---------------------------------------------------------------------------

def _register_and_body(pid, cid, client_obj=None):
    if client_obj is None:
        client.post("/v1/posts/register", json={
            "post_id": pid, "creator_id": cid, "style": "talking_head",
            "format_id": "myth-buster", "hook_signal": "contrarian", "pillar": "Hot takes"})
    return {"post_id": pid, "creator_id": cid, "reach": 500, "likes": 50, "saves": 10,
            "shares": 5, "avg_watch_pct": 0.6, "follows_gained": 8}


def test_ingest_sequential_double_settle_is_idempotent(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    main._post_registry.pop("p_seq", None)
    main._arm_stats.pop("c_seq", None)
    body = _register_and_body("p_seq", "c_seq")
    assert client.post("/v1/metrics/ingest", json=body).json()["status"] == "ingested"
    assert client.post("/v1/metrics/ingest", json=body).json()["status"] == "already_settled"
    assert main._arm_stats["c_seq"]["style:talking_head"]["n"] == 1   # moved exactly once


def test_ingest_concurrent_settles_arms_once(monkeypatch):
    """Two ingests interleaving across the DB-latch await must not double-count."""
    from unittest.mock import AsyncMock
    fake = SupabaseClientStub()
    won = {"n": 0}

    async def _latch(post_id, payload):
        await asyncio.sleep(0)                 # force a real suspension point (interleave)
        won["n"] += 1
        return won["n"] == 1                    # first caller wins, rest lose
    fake.settle_post_conditional = AsyncMock(side_effect=_latch)
    fake.upsert_post = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._post_registry["p_cc"] = {"creator_id": "c_cc", "style": "talking_head",
                                    "format_id": "myth-buster", "hook_signal": "contrarian",
                                    "pillar": "Hot takes", "settled": False}
    main._arm_stats.pop("c_cc", None)
    req = main.MetricsIngestRequest(post_id="p_cc", creator_id="c_cc", reach=500, likes=50,
                                    saves=10, shares=5, avg_watch_pct=0.6, follows_gained=8)

    async def _both():
        return await asyncio.gather(main.ingest_metrics(req), main.ingest_metrics(req))
    results = asyncio.run(_both())
    assert sorted(r["status"] for r in results) == ["already_settled", "ingested"]
    assert main._arm_stats["c_cc"]["style:talking_head"]["n"] == 1


def test_ingest_lost_latch_does_not_update_arms(monkeypatch):
    from unittest.mock import AsyncMock
    fake = SupabaseClientStub()
    fake.settle_post_conditional = AsyncMock(return_value=False)   # another instance won
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._post_registry["p_lost"] = {"creator_id": "c_lost", "style": "talking_head",
                                     "format_id": "myth-buster", "hook_signal": "contrarian",
                                     "pillar": "Hot takes", "settled": False}
    main._arm_stats.pop("c_lost", None)
    r = client.post("/v1/metrics/ingest", json={
        "post_id": "p_lost", "creator_id": "c_lost", "reach": 500, "avg_watch_pct": 0.6}).json()
    assert r["status"] == "already_settled"
    assert "c_lost" not in main._arm_stats            # lost the latch → arms untouched


def test_ingest_latch_unavailable_returns_retry_and_restores(monkeypatch):
    from unittest.mock import AsyncMock
    from supabase_persistence import UNAVAILABLE
    fake = SupabaseClientStub()
    fake.settle_post_conditional = AsyncMock(return_value=UNAVAILABLE)
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._post_registry["p_un"] = {"creator_id": "c_un", "style": "talking_head",
                                   "format_id": "myth-buster", "hook_signal": "contrarian",
                                   "pillar": "Hot takes", "settled": False}
    main._arm_stats.pop("c_un", None)
    r = client.post("/v1/metrics/ingest", json={
        "post_id": "p_un", "creator_id": "c_un", "reach": 500, "avg_watch_pct": 0.6}).json()
    assert r["status"] == "retry_later"
    assert main._post_registry["p_un"]["settled"] is False   # latch restored, retryable
    assert "c_un" not in main._arm_stats


def test_settle_post_conditional_builds_guarded_patch():
    from unittest.mock import AsyncMock
    from supabase_persistence import SupabaseClient, UNAVAILABLE
    c = SupabaseClient("https://x.supabase.co", "k")

    class _Won:
        status_code = 200
        def json(self): return [{"post_id": "p1", "settled": True}]
    c._request = AsyncMock(return_value=_Won())
    assert asyncio.run(c.settle_post_conditional("p1", {
        "creator_id": "c1", "outcome_y": 0.7, "metrics": {}, "settled": True})) is True
    method, path = c._request.await_args[0][0], c._request.await_args[0][1]
    kw = c._request.await_args[1]
    assert method == "PATCH" and path == "/post_registry"
    assert kw["params"]["post_id"] == "eq.p1" and kw["params"]["settled"] == "eq.false"
    assert "return=representation" in kw["headers"]["Prefer"]

    class _Lost:
        status_code = 200
        def json(self): return []                 # 0 rows matched settled=false → lost
    c._request = AsyncMock(return_value=_Lost())
    assert asyncio.run(c.settle_post_conditional("p1", {"settled": True})) is False
    c._request = AsyncMock(return_value=None)      # transport gave up
    assert asyncio.run(c.settle_post_conditional("p1", {"settled": True})) is UNAVAILABLE


# ---------------------------------------------------------------------------
# A-03 · register_post replay must never un-settle a post already settled in the DB.
# ---------------------------------------------------------------------------

def test_register_replay_does_not_unsettle_db(monkeypatch):
    from unittest.mock import AsyncMock
    fake = SupabaseClientStub()
    fake.load_post = AsyncMock(return_value={
        "creator_id": "c_re", "style": "talking_head", "settled": True, "outcome_y": 0.8})
    fake.upsert_post = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._post_registry.pop("p_re", None)
    r = client.post("/v1/posts/register", json={
        "post_id": "p_re", "creator_id": "c_re", "style": "talking_head",
        "format_id": "myth-buster", "hook_signal": "contrarian", "pillar": "X"}).json()
    assert r["status"] == "already_registered"
    fake.upsert_post.assert_not_awaited()                   # no regressive write at all
    assert main._post_registry["p_re"]["settled"] is True   # cached the real settled row


def test_register_fresh_insert_ignores_duplicates(monkeypatch):
    from unittest.mock import AsyncMock
    fake = SupabaseClientStub()
    fake.load_post = AsyncMock(return_value=None)           # DB says genuinely absent
    fake.upsert_post = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._post_registry.pop("p_fresh", None)
    r = client.post("/v1/posts/register", json={
        "post_id": "p_fresh", "creator_id": "c_fresh", "style": "talking_head",
        "format_id": "myth-buster", "hook_signal": "contrarian", "pillar": "X"}).json()
    assert r["status"] == "registered"
    # defense-in-depth: never overwrite an existing row (a racing settle) on insert
    assert fake.upsert_post.await_args.kwargs.get("resolution") == "ignore-duplicates"
    sent = fake.upsert_post.await_args[0][1]
    assert "outcome_y" not in sent and "metrics" not in sent   # no regressive fields


def test_upsert_post_ignore_duplicates_header():
    from unittest.mock import AsyncMock
    from supabase_persistence import SupabaseClient

    class _Ok:
        status_code = 201
    c = SupabaseClient("https://x.supabase.co", "k")
    c._request = AsyncMock(return_value=_Ok())
    asyncio.run(c.upsert_post("p1", {"creator_id": "c"}, resolution="ignore-duplicates"))
    assert "resolution=ignore-duplicates" in c._request.await_args[1]["headers"]["Prefer"]


# ---------------------------------------------------------------------------
# A-04 · Arm lazy-load before create-on-miss: a fresh instance must MERGE the
# creator's DB arm history before incrementing, never clobber it with n=1.
# ---------------------------------------------------------------------------

def test_ensure_arms_loaded_loads_despite_local_entry(monkeypatch):
    from unittest.mock import AsyncMock
    fake = SupabaseClientStub()
    fake.load_arm_stats = AsyncMock(return_value={"format_id:myth-buster": {"n": 3}})
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._arm_stats["c_local"] = {"style:faceless": {"n": 1}}   # a local arm already present
    main._arms_loaded.discard("c_local")
    asyncio.run(main._ensure_arms_loaded("c_local"))
    fake.load_arm_stats.assert_awaited_once()                    # loaded DESPITE local presence
    assert main._arm_stats["c_local"]["format_id:myth-buster"]["n"] == 3   # DB merged in
    assert main._arm_stats["c_local"]["style:faceless"]["n"] == 1          # local preserved
    fake.load_arm_stats.reset_mock()
    asyncio.run(main._ensure_arms_loaded("c_local"))
    fake.load_arm_stats.assert_not_awaited()                     # loaded once, then cached


def test_update_arm_merges_db_history_before_incrementing(monkeypatch):
    from unittest.mock import AsyncMock
    fake = SupabaseClientStub()
    fake.load_arm_stats = AsyncMock(return_value={
        "style:talking_head": {"n": 6, "sum_y": 4.2, "alpha": 5.2, "beta": 2.8,
                               "effect": 0.7, "confidence": "early_read"}})
    fake.upsert_arm_stat = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._arm_stats.pop("c_merge", None)
    main._arms_loaded.discard("c_merge")
    asyncio.run(main._update_arm("c_merge", "style:talking_head", 0.8))
    s = main._arm_stats["c_merge"]["style:talking_head"]
    assert s["n"] == 7                        # 6 loaded + 1, NOT a clobbering n=1
    _, _, stat = fake.upsert_arm_stat.await_args[0]
    assert stat["n"] == 7                      # write-through carries the merged count


# ---------------------------------------------------------------------------
# A-05 · Honest lift scale: lift/labels/bands measure an arm against the
# creator's OWN mean (raw engagement composite), not a fixed 0.5 prior — so
# DRIVER/ERROR bands are finally reachable and "vs your average" is truthful.
# ---------------------------------------------------------------------------

def _seed_baseline(cid, mean=1.0, count=12):
    """Give a creator `count` settled posts all at raw==mean, so their personal
    baseline is exactly `mean` — lets a directly-seeded arm's lift be computed."""
    for k in list(main._post_registry):
        if k.startswith(f"__base_{cid}"):
            main._post_registry.pop(k)
    for i in range(count):
        main._post_registry[f"__base_{cid}_{i}"] = {
            "creator_id": cid, "settled": True, "outcome_raw": mean}
    main._invalidate_creator_mean(cid)


def test_compute_y_is_sigmoid_of_compute_raw():
    m = {"reach": 1000, "saves": 40, "shares": 20, "follows_gained": 8, "avg_watch_pct": 0.5}
    import math
    raw = main._compute_raw(m, "grow")
    assert abs(main._compute_y(m, "grow") - 1 / (1 + math.exp(-0.5 * (raw - 2.0)))) < 1e-9
    # raw rises with engagement (monotone) and differs by goal
    hotter = {**m, "follows_gained": 40}
    assert main._compute_raw(hotter, "grow") > raw


def test_arms_for_prompt_lift_vs_creator_baseline_reaches_driver(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    cid = "c_base"
    main._arm_stats.pop(cid, None)
    main._arms_loaded.add(cid)
    for k in list(main._post_registry):
        if k.startswith(cid):
            main._post_registry.pop(k)
    # 8 settled posts, overall mean raw = 1.0
    for i, r in enumerate([0.5, 0.5, 0.5, 0.5, 2.0, 2.0, 2.0, 2.0]):
        main._post_registry[f"{cid}-p{i}"] = {"creator_id": cid, "settled": True, "outcome_raw": r}
    # baseline of 16 ordinary posts at raw 1.0 → personal mean 1.0
    for i, r in enumerate([1.0] * 16):
        main._post_registry[f"{cid}-p{i}"] = {"creator_id": cid, "settled": True, "outcome_raw": r}
    main._invalidate_creator_mean(cid)
    # the contrarian arm: 4 posts averaging raw 3.0 — a genuine outlier vs the 1.0 mean
    main._arm_stats[cid] = {"hook_signal:contrarian": {
        "n": 4, "sum_y": 3.2, "n_raw": 4, "sum_raw": 12.0, "alpha": 1, "beta": 1,
        "effect": 0.7, "confidence": "early_read"}}
    arms = asyncio.run(main._arms_for_prompt(cid))
    a = next(x for x in arms if x["value"] == "contrarian")
    assert a["lift_pct"] >= 80                               # DRIVER band is reachable now
    assert prompts.classify_arm_lift(a["lift_pct"]) == "driver"
    assert "vs your average" in a["label"] and a["has_lift"] is True


def test_arms_for_prompt_no_raw_makes_no_lift_claim(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    cid = "c_noraw"
    main._arm_stats.pop(cid, None)
    main._arms_loaded.add(cid)
    for k in list(main._post_registry):
        if k.startswith(cid):
            main._post_registry.pop(k)
    main._invalidate_creator_mean(cid)
    # arm has samples but NO sum_raw (pre-migration) and no baseline => no % claim
    main._arm_stats[cid] = {"style:faceless": {
        "n": 6, "sum_y": 4.0, "effect": 0.72, "confidence": "early_read"}}
    arms = asyncio.run(main._arms_for_prompt(cid))
    a = arms[0]
    assert a["lift_pct"] == 0 and a.get("has_lift") is False
    assert "%" not in a["label"]              # never fabricate performance without data


def test_insights_learned_no_negative_outperform_copy(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    cid = "c_under"
    main._arm_stats.pop(cid, None)
    main._arms_loaded.add(cid)
    for k in list(main._post_registry):
        if k.startswith(cid):
            main._post_registry.pop(k)
    for i, r in enumerate([2.0, 2.0, 2.0, 2.0, 0.4, 0.4, 0.4, 0.4]):
        main._post_registry[f"{cid}-p{i}"] = {"creator_id": cid, "settled": True, "outcome_raw": r}
    main._invalidate_creator_mean(cid)
    # an UNDERperforming arm: 4 posts all at raw 0.4 vs baseline 1.2
    main._arm_stats[cid] = {"style:faceless": {
        "n": 4, "sum_y": 1.0, "n_raw": 4, "sum_raw": 1.6, "alpha": 1, "beta": 1,
        "effect": 0.3, "confidence": "confirmed"}}
    b = client.get(f"/v1/insights/learned?creator_id={cid}").json()
    assert b["insights"] and b["insights"][0]["lift_pct"] < 0
    # winning_formula must not claim a negative number "outperforms"
    if b["winning_formula"]:
        assert "outperforms your average by -" not in b["winning_formula"]


# ---------------------------------------------------------------------------
# A-06 · Attribution is scoped to the SETTLED POST'S OWN dimensions, never the
# creator's globally-strongest arm.
# ---------------------------------------------------------------------------

def test_attribution_scoped_to_settled_post_dims(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    cid = "c_attr"
    main._arm_stats.pop(cid, None)
    main._arms_loaded.add(cid)
    for k in list(main._post_registry):
        if k.startswith(cid):
            main._post_registry.pop(k)
    for i in range(16):
        main._post_registry[f"{cid}-b{i}"] = {"creator_id": cid, "settled": True, "outcome_raw": 1.0}
    main._invalidate_creator_mean(cid)
    # style:faceless is the creator's biggest driver, but THIS post is style:talking_head
    main._arm_stats[cid] = {
        "style:faceless": {"n": 8, "n_raw": 8, "sum_raw": 40.0, "confidence": "confirmed"},        # huge lift
        "style:talking_head": {"n": 6, "n_raw": 6, "sum_raw": 5.0, "confidence": "early_read"},     # this post's style
        "hook_signal:contrarian": {"n": 6, "n_raw": 6, "sum_raw": 6.6, "confidence": "early_read"}, # this post's hook
    }
    post = {"pillar": "", "style": "talking_head", "format_id": "", "hook_signal": "contrarian"}
    attribution = asyncio.run(main._attribute_settled_post(cid, post))
    assert attribution["dimension"] in ("style", "hook_signal", "none")
    assert attribution["arm_value"] != "faceless"          # never attribute to a dim the post didn't use


def test_attribution_live_path_falls_back_to_deterministic(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk-test")

    async def boom(*a, **k):
        raise main.HTTPException(status_code=502, detail="down")
    monkeypatch.setattr(main, "anthropic", boom)
    monkeypatch.setattr(main, "_supabase_client", None)
    cid = "c_attr_live"
    main._arm_stats.pop(cid, None)
    main._arms_loaded.add(cid)
    for k in list(main._post_registry):
        if k.startswith(cid):
            main._post_registry.pop(k)
    for i in range(16):
        main._post_registry[f"{cid}-b{i}"] = {"creator_id": cid, "settled": True, "outcome_raw": 1.0}
    main._invalidate_creator_mean(cid)
    main._arm_stats[cid] = {"hook_signal:contrarian": {"n": 6, "n_raw": 6, "sum_raw": 12.0, "confidence": "confirmed"}}
    post = {"pillar": "", "style": "", "format_id": "", "hook_signal": "contrarian"}
    # LLM raises → deterministic attribution still returned (keyless-mock discipline)
    attribution = asyncio.run(main._attribute_settled_post(cid, post))
    assert attribution["dimension"] == "hook_signal" and attribution["arm_value"] == "contrarian"


# ---------------------------------------------------------------------------
# A-07 · Persistence hardening: never raise into the hot path (catch the httpx
# base error), log non-2xx once, and report mode from the client, not env vars.
# ---------------------------------------------------------------------------

def test_request_catches_httpx_base_error(monkeypatch):
    import httpx
    from supabase_persistence import SupabaseClient
    c = SupabaseClient("https://x.supabase.co", "k")

    class _Boom:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **k):
            raise httpx.ReadError("mid-stream reset")   # NOT Timeout/ConnectError
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Boom())

    async def _fast_sleep(_):
        return None
    monkeypatch.setattr("supabase_persistence.asyncio.sleep", _fast_sleep)   # skip backoff waits
    # must return None after retries, never propagate
    assert asyncio.run(c._request("GET", "/arm_stats")) is None


def test_request_logs_non_2xx(monkeypatch, caplog):
    import httpx
    from supabase_persistence import SupabaseClient
    c = SupabaseClient("https://x.supabase.co", "k")

    class _Resp:
        status_code = 401
        text = '{"message":"JWT expired"}'
        def json(self): return {"message": "JWT expired"}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **k): return _Resp()
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Client())
    with caplog.at_level("WARNING"):
        r = asyncio.run(c._request("GET", "/arm_stats"))
    assert r is not None and r.status_code == 401
    assert any("401" in rec.message for rec in caplog.records)


def test_learning_endpoints_mode_follows_client_not_env(monkeypatch):
    # URL set but NO key → no client → every learning response must say mock, not live
    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "SUPABASE_URL", "https://x.supabase.co")
    main._arm_stats.pop("c_mode", None)
    for i in range(5):
        asyncio.run(main._update_arm("c_mode", "style:talking_head", 0.8, 1.5))
    assert client.get("/v1/insights/learned?creator_id=c_mode").json()["mode"] == "mock"
    assert client.get("/v1/recommendations?creator_id=c_mode").json()["mode"] == "mock"


# ---------------------------------------------------------------------------
# A-08 · load_all_posts paginates — no silent 1000-row truncation on boot.
# ---------------------------------------------------------------------------

def test_load_all_posts_paginates(monkeypatch):
    import supabase_persistence as sp_mod
    from supabase_persistence import SupabaseClient
    monkeypatch.setattr(sp_mod, "_PAGE_SIZE", 2)
    c = SupabaseClient("https://x.supabase.co", "k")

    class _Resp:
        def __init__(self, rows): self._rows, self.status_code = rows, 200
        def json(self): return self._rows
    pages = [_Resp([{"post_id": "p1"}, {"post_id": "p2"}]),   # full page → keep paging
             _Resp([{"post_id": "p3"}])]                       # short page → stop
    seen = {"offsets": []}

    async def _req(method, path, *, params=None, **k):
        seen["offsets"].append(params.get("offset"))
        return pages[len(seen["offsets"]) - 1]
    c._request = _req
    rows = asyncio.run(c.load_all_posts())
    assert [r["post_id"] for r in rows] == ["p1", "p2", "p3"]
    assert seen["offsets"] == ["0", "2"]                       # advanced by page size


# ---------------------------------------------------------------------------
# A-09 · Taxonomy validation at the bandit's doors: only valid dim values ever
# become arms; the LLM schema constrains formatId/altHook signal.
# ---------------------------------------------------------------------------

def test_script_schema_constrains_format_and_alt_signal():
    import prompts
    props = prompts.SCRIPT_JSON_ELEMENT["properties"]
    assert props["formatId"].get("enum") == prompts.FORMAT_IDS
    assert props["altHooks"]["items"]["properties"]["signal"].get("enum") == prompts.SIGNAL_LIST


def test_quality_scripts_rejects_offtaxonomy_alt_signal(monkeypatch):
    # A revised main-hook that pulls an alt whose signal is junk must NOT poison hookSignal.
    from unittest.mock import AsyncMock
    monkeypatch.setattr(main, "AI_QUALITY", True)
    # judge keeps the script but crowns altHook #1 (best_hook=1)
    monkeypatch.setattr(main, "anthropic_json",
                        AsyncMock(return_value=[{"index": 0, "verdict": "keep", "best_hook": 1,
                                                 "overall": 80, "hook_strength": 8, "fluff": 2,
                                                 "format_fit": 9, "voice_match": 9}]))
    scr = [_script(hook="orig", alts=[{"text": "better opener", "signal": "not_a_signal", "strength": 9}])]
    scr[0]["hookSignal"] = "contrarian"
    out = asyncio.run(main.quality_scripts({}, "talking_head", scr))
    assert out[0]["hook"] == "better opener"                # alt was adopted
    assert out[0]["hookSignal"] in prompts.SIGNAL_LIST      # but its junk signal was rejected


def test_register_post_drops_offtaxonomy_dims(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    main._post_registry.pop("p_tax", None)
    r = client.post("/v1/posts/register", json={
        "post_id": "p_tax", "creator_id": "c_tax", "style": "not_a_style",
        "format_id": "bogus", "hook_signal": "nope", "pillar": "My Custom Pillar"}).json()
    assert r["status"] == "registered"
    entry = main._post_registry["p_tax"]
    assert entry["style"] == "" and entry["format_id"] == "" and entry["hook_signal"] == ""
    assert entry["pillar"] == "My Custom Pillar"           # pillar stays freeform
    assert set(r.get("dropped", [])) == {"style", "format_id", "hook_signal"}


def test_recommendations_candidate_styles_are_active(monkeypatch):
    # a creator with data → recommendation styles must be drawn from ACTIVE_STYLES
    monkeypatch.setattr(main, "_supabase_client", None)
    cid = "c_recstyle"
    main._arm_stats[cid] = {"pillar:Hot takes": {
        "n": 5, "n_raw": 5, "sum_raw": 6.0, "alpha": 4.0, "beta": 2.0, "confidence": "early_read"}}
    main._arms_loaded.add(cid)
    b = client.get(f"/v1/recommendations?creator_id={cid}").json()
    for arm in b["arms"]:
        assert arm["style"] in prompts.ACTIVE_STYLES


# ---------------------------------------------------------------------------
# A-10 · Niche integrity: token-boundary matching, persisted priors + niche.
# ---------------------------------------------------------------------------

def test_match_niche_uses_token_boundaries():
    import prompts
    assert prompts.match_niche("brunch recipes") == "food"       # NOT fitness via 'run'
    assert prompts.match_niche("lifestyle vlogs") != "fashion"   # 'style' inside 'lifestyle'
    assert prompts.match_niche("wheelchair basketball") != "beauty"   # 'hair' inside 'wheelchair'
    assert prompts.match_niche("ai tools for creators") == "tech"     # bare 'ai' whole word
    assert prompts.match_niche("airbnb hosting") == "real_estate"     # NOT tech via 'ai'
    assert prompts.match_niche("running coach") == "fitness"     # stem prefix still matches
    assert prompts.match_niche("investing for beginners") == "finance"


def test_arm_cols_persist_priors():
    import supabase_persistence as sp_mod
    assert "prior_alpha" in sp_mod._ARM_COLS and "prior_beta" in sp_mod._ARM_COLS


def test_register_persists_creator_niche(monkeypatch):
    from unittest.mock import AsyncMock
    fake = SupabaseClientStub()
    fake.upsert_creator = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._post_registry.pop("p_cn", None)
    client.post("/v1/posts/register", json={
        "post_id": "p_cn", "creator_id": "c_cn", "niche": "fitness coaching",
        "style": "talking_head", "format_id": "myth-buster", "hook_signal": "contrarian"})
    fake.upsert_creator.assert_awaited()
    assert fake.upsert_creator.await_args[0][0] == "c_cn"


def test_load_learning_state_rehydrates_niche(monkeypatch):
    from unittest.mock import AsyncMock
    fake = SupabaseClientStub()
    fake.load_all_posts = AsyncMock(return_value=[])
    fake.load_all_creators = AsyncMock(return_value=[{"creator_id": "c_reh", "niche": "finance"}])
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._creator_niche.pop("c_reh", None)
    asyncio.run(main._load_learning_state())
    assert main._creator_niche.get("c_reh") == "finance"


def test_upsert_creator_and_load_creator_shape():
    from unittest.mock import AsyncMock
    from supabase_persistence import SupabaseClient

    class _Ok:
        status_code = 201
    c = SupabaseClient("https://x.supabase.co", "k")
    c._request = AsyncMock(return_value=_Ok())
    assert asyncio.run(c.upsert_creator("c1", {"niche": "food", "goal": "grow"})) is True
    row = c._request.await_args[1]["json"]
    assert row["creator_id"] == "c1" and row["niche"] == "food"


# ---------------------------------------------------------------------------
# A-11 · Moat wiring: learning/memory/emulation/niche reach every generation surface.
# ---------------------------------------------------------------------------

def test_best_hooks_threads_memory_and_emulation(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "BEST_OF_N_HOOKS", True)
    monkeypatch.setattr(main, "AI_QUALITY", True)
    cap = {}

    def fake_hooks_prompt(brand, topic, style, arm_stats=None, memory=None, emulation=None):
        cap["memory"], cap["emulation"] = memory, emulation
        return ("sys", "usr")
    monkeypatch.setattr(main.prompts, "hooks_prompt", fake_hooks_prompt)
    monkeypatch.setattr(main, "anthropic_json", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "quality_hooks", AsyncMock(return_value=[]))
    asyncio.run(main.best_hooks({}, "topic", "talking_head", "c_bh",
                                memory={"facts": ["x"]}, emulation=[{"handle": "@x"}]))
    assert cap["memory"] == {"facts": ["x"]} and cap["emulation"] == [{"handle": "@x"}]


def test_fast_feed_passes_arm_stats(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "_supabase_client", None)
    cid = "c_ff"
    main._arm_stats.pop(cid, None)
    main._arms_loaded.add(cid)
    _seed_baseline(cid, 1.0)
    main._arm_stats[cid] = {"style:talking_head": {
        "n": 6, "n_raw": 6, "sum_raw": 12.0, "alpha": 1, "beta": 1, "confidence": "early_read"}}
    cap = {}

    def fake_scripts_prompt(brand, pillar, style, count, *a, **k):
        cap["arm_stats"] = k.get("arm_stats")
        return ("sys", "usr")
    monkeypatch.setattr(main.prompts, "scripts_prompt", fake_scripts_prompt)
    monkeypatch.setattr(main, "anthropic_json", AsyncMock(return_value=[_script()]))
    sreq = main.ScriptRequest(creator_id=cid, pillar="Hot takes", style="talking_head", count=2)
    asyncio.run(main._fast_feed_scripts(sreq))
    assert cap["arm_stats"]                      # data-rich creator's fast feed is learning-aware


def test_steer_prompt_accepts_learning_block():
    import prompts
    sys, usr = prompts.steer_prompt({"niche": "fitness"}, {"hook": "h", "body": "b", "cta": "c"},
                                    "make it punchier", arm_stats=[])
    assert "NICHE BASELINE" in usr               # cold-start niche baseline reaches steer


def test_converse_user_niche_fallback_when_no_arms():
    import prompts
    usr = prompts.converse_user({"niche": "finance"}, None, [{"role": "user", "content": "hi"}],
                                arm_stats=[])
    assert "NICHE BASELINE" in usr


def test_mimic_and_analyze_prompts_accept_arm_stats():
    import prompts
    _, u1 = prompts.mimic_prompt({"caption": "x"}, {"niche": "beauty"}, arm_stats=[])
    _, u2 = prompts.analyze_video_prompt("http://x", "transcript", {"niche": "beauty"}, arm_stats=[])
    assert "NICHE BASELINE" in u1 and "NICHE BASELINE" in u2


# ---------------------------------------------------------------------------
# A-12 · Registry forward schema: clip_id/permalink persist, settled_at stamped,
# performance summary filters by settled_at within the window.
# ---------------------------------------------------------------------------

def test_register_persists_clip_id_and_permalink(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    main._post_registry.pop("p_link", None)
    client.post("/v1/posts/register", json={
        "post_id": "p_link", "creator_id": "c_link", "clip_id": "clip-9",
        "permalink": "https://instagram.com/reel/abc", "style": "talking_head",
        "format_id": "myth-buster", "hook_signal": "contrarian"})
    e = main._post_registry["p_link"]
    assert e["clip_id"] == "clip-9" and e["permalink"] == "https://instagram.com/reel/abc"
    import supabase_persistence as sp_mod
    assert {"clip_id", "permalink", "settled_at"} <= set(sp_mod._POST_COLS)


def test_ingest_stamps_settled_at(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    main._post_registry.pop("p_sa", None)
    main._arm_stats.pop("c_sa", None)
    client.post("/v1/posts/register", json={
        "post_id": "p_sa", "creator_id": "c_sa", "style": "talking_head"})
    client.post("/v1/metrics/ingest", json={
        "post_id": "p_sa", "creator_id": "c_sa", "reach": 500, "avg_watch_pct": 0.6, "saves": 10})
    assert main._post_registry["p_sa"].get("settled_at")     # ISO timestamp stamped on settle


def test_performance_summary_filters_by_window(monkeypatch):
    monkeypatch.setattr(main, "_supabase_client", None)
    cid = "c_perf_win"
    for k in list(main._post_registry):
        if k.startswith(cid):
            main._post_registry.pop(k)
    # one recent settled post, one settled 100 days ago → only the recent one counts in a 30d window
    main._post_registry[f"{cid}-new"] = {
        "creator_id": cid, "settled": True, "settled_at": "2026-07-05T00:00:00+00:00",
        "metrics": {"views": 100, "likes": 10, "follows_gained": 2}, "format_id": "listicle"}
    main._post_registry[f"{cid}-old"] = {
        "creator_id": cid, "settled": True, "settled_at": "2026-03-01T00:00:00+00:00",
        "metrics": {"views": 9999, "likes": 1, "follows_gained": 0}, "format_id": "myth-buster"}
    b = client.get(f"/v1/performance/summary?creator_id={cid}&days=30&now=2026-07-07T00:00:00+00:00").json()
    assert b["totals"]["posts"] == 1 and b["totals"]["views"] == 100   # old post excluded


# ---------------------------------------------------------------------------
# A-13 · Number discipline residue: teardown never fabricates a lift without
# real metrics; /v1/score is deterministic (temperature 0).
# ---------------------------------------------------------------------------

def test_teardown_no_metrics_makes_no_lift_claim(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    r = client.post("/v1/teardown", json={"clip": {"predictedScore": 88}}).json()
    assert r["liftPercent"] is None                     # no data → no performance number
    assert "%" not in r["headline"]                     # and no "beat N%" headline


def test_teardown_prompt_forbids_claims_without_metrics():
    import prompts
    _, u = prompts.teardown_prompt({"predictedScore": 70})       # no metrics block
    assert "null" in u.lower() and "no performance" in u.lower()


def test_teardown_live_nulls_lift_without_metrics(monkeypatch):
    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None):
        return '{"headline":"Strong hook","detail":"Tight open.","liftPercent":73}'
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "anthropic", fake)
    r = client.post("/v1/teardown", json={"clip": {"predictedScore": 80}}).json()
    assert r["liftPercent"] is None                     # model's fabricated 73 is discarded (no metrics)


def test_score_pins_temperature_zero(monkeypatch):
    cap = {}

    async def fake(system, user, model=main.OPUS, max_tokens=3000, temperature=None):
        cap["temperature"] = temperature
        return ('{"hook":"High","fluff":"Low","satisfaction":"High","overall":80,'
                '"strongest":"x","weakest":"y","fix":"z"}')
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "anthropic", fake)
    client.post("/v1/score", json={"hook": "h", "body": "b"})
    assert cap["temperature"] == 0.0


# ---------------------------------------------------------------------------
# B-01 · Live-mode transport hygiene: a mid-stream httpx.ReadError (NOT Timeout/
# Connect) must surface as HTTPException and every AI route must degrade, not 500.
# ---------------------------------------------------------------------------

def test_anthropic_read_error_becomes_httpexception(monkeypatch):
    import httpx
    import pytest
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def _fast_sleep(_):
        return None
    monkeypatch.setattr(main.asyncio, "sleep", _fast_sleep)

    class _Client:
        async def post(self, *a, **k):
            raise httpx.ReadError("mid-stream reset")
    monkeypatch.setattr(main, "_get_anthropic_client", lambda: _Client())
    with pytest.raises(main.HTTPException):
        asyncio.run(main.anthropic("s", "u"))


def test_ai_route_degrades_on_read_error_not_500(monkeypatch):
    import httpx
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def _fast_sleep(_):
        return None
    monkeypatch.setattr(main.asyncio, "sleep", _fast_sleep)

    class _Client:
        async def post(self, *a, **k):
            raise httpx.ReadError("mid-stream reset")
    monkeypatch.setattr(main, "_get_anthropic_client", lambda: _Client())
    r = client.post("/v1/captions", json={"hook": "h", "body": "b"})
    assert r.status_code == 200 and r.json()["mode"] == "mock"


def test_anthropic_guards_malformed_200_body(monkeypatch):
    import pytest
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def _fast_sleep(_):
        return None
    monkeypatch.setattr(main.asyncio, "sleep", _fast_sleep)

    class _Resp:
        status_code = 200
        def json(self): raise ValueError("not json")

    class _Client:
        async def post(self, *a, **k): return _Resp()
    monkeypatch.setattr(main, "_get_anthropic_client", lambda: _Client())
    with pytest.raises(main.HTTPException):        # malformed 200 → degradeable, not a raw ValueError
        asyncio.run(main.anthropic("s", "u"))


# ---------------------------------------------------------------------------
# B-02 · Live-degrade payloads are populated, not blank — only live users during
# a vendor blip hit these branches; a blank screen there is the bug.
# ---------------------------------------------------------------------------

def _raise_httpexc(*a, **k):
    async def _boom(*a, **k):
        raise main.HTTPException(status_code=502, detail="down")
    return _boom


def test_hooks_live_degrade_is_populated(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "anthropic_json", _raise_httpexc())
    r = client.post("/v1/hooks", json={"topic": "protein", "creator_id": "c_h"}).json()
    assert r["mode"] == "mock" and r["hooks"] and r["hooks"][0]["text"]


def test_teardown_live_degrade_is_populated(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "anthropic", _raise_httpexc())
    r = client.post("/v1/teardown", json={"clip": {"predictedScore": 70}}).json()
    assert r["mode"] == "mock" and r["headline"] and r["detail"]


def test_insights_live_degrade_is_populated(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "anthropic", _raise_httpexc())
    r = client.post("/v1/insights", json={"niche": "fitness", "summary": "x"}).json()
    assert r["mode"] == "mock" and r["coaching"]


# ---------------------------------------------------------------------------
# B-03 · /v1/media/analyze: vision failure degrades to the full mock, never 500;
# a failed/malformed analysis is never cached.
# ---------------------------------------------------------------------------

def test_media_analyze_vision_error_degrades_to_full_mock(monkeypatch):
    import httpx
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    main._media_cache.pop("h_err", None)

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise httpx.ReadError("mid-stream")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _C())
    r = client.post("/v1/media/analyze",
                    json={"content_hash": "h_err", "public_url": "http://x", "kind": "image"}).json()
    assert r["mode"] == "mock" and r.get("broll_suitability")   # full analysis shape
    assert "h_err" not in main._media_cache                     # failure NOT cached


def test_media_analyze_malformed_not_cached(monkeypatch):
    import httpx
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    main._media_cache.pop("h_bad", None)

    class _Resp:
        status_code = 200
        def json(self): return {"content": [{"text": "not json at all"}]}

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _Resp()
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _C())
    r = client.post("/v1/media/analyze",
                    json={"content_hash": "h_bad", "public_url": "http://x", "kind": "image"}).json()
    assert r["mode"] == "mock" and "h_bad" not in main._media_cache


# ---------------------------------------------------------------------------
# B-04 · /v1/broll/match: Pexels fetch guarded; tie-break resolves by candidate
# position, not the ambiguous corpus index.
# ---------------------------------------------------------------------------

def test_fetch_pexels_guarded(monkeypatch):
    import httpx
    monkeypatch.setattr(main, "PEXELS_KEY", "k")

    class _C:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise httpx.ReadError("mid-stream")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _C())
    assert asyncio.run(main._fetch_pexels("dog")) is None      # guarded — no raise, no 500


def test_broll_match_tiebreak_resolves_by_position(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def fake(system, user, model=main.HAIKU, max_tokens=100, temperature=None):
        return '{"chosen_index": 1, "reason": "fits"}'
    monkeypatch.setattr(main, "anthropic", fake)
    corpus = [
        {"asset_id": "a", "description": "dog running", "tags": ["dog"], "broll_suitability": 80},
        {"asset_id": "b", "description": "dog running", "tags": ["dog"], "broll_suitability": 80},
    ]
    r = client.post("/v1/broll/match",
                    json={"cue_text": "dog running", "corpus": corpus, "top_k": 3}).json()
    # chosen_index 1 = the SECOND top candidate (asset b), promoted to front
    assert r["matches"][0]["asset_id"] == "b"


# ---------------------------------------------------------------------------
# B-06 · Onboarding digest degrades per-stage: a transient LLM error must not
# throw away a successful scrape+transcription.
# ---------------------------------------------------------------------------

def test_digest_degrades_on_llm_failure(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def boom(*a, **k):
        raise main.HTTPException(status_code=502, detail="down")
    monkeypatch.setattr(main, "anthropic", boom)

    async def passthrough(posts):
        return posts
    monkeypatch.setattr(main, "_transcribe_top_posts", passthrough)
    req = main.DigestRequest(posts=[{"caption": "a real post", "likes": 20}], niche="fitness")
    main._digest_jobs["d_deg"] = {"req": req, "status": "running", "stage": "init"}
    asyncio.run(main._run_digest("d_deg"))
    job = main._digest_jobs["d_deg"]
    assert job["status"] == "ready"                     # degraded, NOT failed
    assert job["result"]["scan"]                        # mock-derived brand present


# ---------------------------------------------------------------------------
# Quiz-context fields must survive Brand validation. Pydantic's default
# extra='ignore' silently dropped biggest_blocker / camera_comfort / stage /
# posting_frequency / weekly_target / primary_platform / why_now for months,
# so brand_block()'s strategy hints never fired in production.
# ---------------------------------------------------------------------------

def test_brand_keeps_quiz_context_fields():
    b = main.Brand.model_validate({
        "niche": "fitness",
        "primary_platform": "instagram",
        "stage": "1K–10K followers",
        "posting_frequency": "2–3x a week",
        "biggest_blocker": "ideas",
        "camera_comfort": "prefer_off",
        "weekly_target": 5,
        "why_now": "launch",
    }).d()
    block = prompts.brand_block(b)
    assert "generate hooks and topics generously" in block      # blocker hint fires
    assert "faceless voiceover and fast-cuts preferred" in block  # comfort hint fires
    assert "weekly post target: 5" in block
    assert "2–3x a week" in block
    assert "instagram" in block
    assert "1K–10K followers" in block
    assert "tie scripts to their offer" in block                # why_now hint fires
