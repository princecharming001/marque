"""B3: the feed (and mimic/analyze-video/converse) finally see the creator's real voice,
catchphrases, non_negotiables, and posts — not just niche/audience/known_for/goal."""
import asyncio

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


class _FakeProfileStore:
    """Minimal fake covering the creator_profiles + creator_posts + channel_strategies
    surface _persist_creator_profile / _hydrate_creator_profile / _creator_posts touch."""
    def __init__(self, profiles=None, posts=None, strategies=None):
        self.profiles = dict(profiles or {})
        self.posts = dict(posts or {})
        self.strategies = dict(strategies or {})

    async def upsert_creator_profile(self, creator_id, brand, brand_hash):
        self.profiles[creator_id] = {"brand": brand, "brand_hash": brand_hash}
        return True

    async def load_creator_profile(self, creator_id):
        return self.profiles.get(creator_id)

    async def upsert_creator_posts(self, creator_id, posts):
        self.posts[creator_id] = posts
        return True

    async def load_creator_posts(self, creator_id):
        return self.posts.get(creator_id)

    async def load_strategy(self, creator_id):
        return self.strategies.get(creator_id)


def _reset_feed_caches():
    main._feed_cache.clear()
    main._feed_refreshing.clear()
    main._feed_inflight.clear()
    main._creator_profile_cache.clear()
    main._creator_posts_cache.clear()
    main._last_persisted_brand_hash.clear()


# ---------------------------------------------------------------------------
# FeedRequest carries the full Brand shape
# ---------------------------------------------------------------------------

def test_feed_request_inherits_full_brand_fields():
    req = main.FeedRequest(creator_id="c1", niche="fitness", voice={"funnyToSerious": 0.8},
                           catchphrases=["small reps, big life"], non_negotiables=["no scams"],
                           what_you_do="coach busy parents")
    assert req.voice == {"funnyToSerious": 0.8}
    assert req.catchphrases == ["small reps, big life"]
    assert req.non_negotiables == ["no scams"]
    assert req.what_you_do == "coach busy parents"


def test_brand_only_narrows_to_brand_fields():
    d = {"niche": "fitness", "voice": {"x": 1}, "styles": "talking_head", "creator_id": "c1",
        "cursor": 5, "memory": {"facts": []}}
    narrowed = main._brand_only(d)
    assert narrowed == {"niche": "fitness", "voice": {"x": 1}}
    assert "styles" not in narrowed and "creator_id" not in narrowed


def test_feed_post_body_reaches_fast_feed_scripts(monkeypatch):
    """POST /v1/feed with catchphrases/voice must hand them to script generation —
    the audit's B1 root cause: previously only niche/audience/known_for/goal survived."""
    _reset_feed_caches()
    captured = {}

    async def fake_fast(sreq):
        captured["sreq"] = sreq
        return {"mode": "mock", "scripts": main.mock_scripts(sreq)}
    monkeypatch.setattr(main, "_fast_feed_scripts", fake_fast)
    monkeypatch.setattr(main, "_maybe_prefetch_next", lambda *a, **k: None)

    resp = client.post("/v1/feed", json={
        "creator_id": "c_ctx", "niche": "fitness for busy parents",
        "catchphrases": ["small reps, big life"], "non_negotiables": ["no pain no gain"],
        "voice": {"funnyToSerious": 0.9}, "what_you_do": "coach at-home fitness",
        "fresh": 1,
    })
    assert resp.status_code == 200
    sreq = captured["sreq"]
    assert sreq.catchphrases == ["small reps, big life"]
    assert sreq.non_negotiables == ["no pain no gain"]
    assert sreq.voice == {"funnyToSerious": 0.9}
    assert sreq.what_you_do == "coach at-home fitness"
    _reset_feed_caches()


# ---------------------------------------------------------------------------
# Cache key changes on a real profile edit (not just niche)
# ---------------------------------------------------------------------------

def test_cache_key_differs_when_voice_or_catchphrases_change():
    base = {"niche": "fitness", "goal": "g"}
    edited = {"niche": "fitness", "goal": "g", "voice": {"funnyToSerious": 0.9},
             "catchphrases": ["new phrase"]}
    k1 = main._feed_cache_key("c1", base, "", "", 0)
    k2 = main._feed_cache_key("c1", edited, "", "", 0)
    assert k1 != k2


def test_cache_key_stable_for_identical_brand():
    brand = {"niche": "fitness", "goal": "g", "voice": {"x": 1}}
    k1 = main._feed_cache_key("c1", dict(brand), "", "", 0)
    k2 = main._feed_cache_key("c1", dict(brand), "", "", 0)
    assert k1 == k2


def test_cache_key_differs_with_posts_token():
    brand = {"niche": "fitness"}
    k1 = main._feed_cache_key("c1", brand, "", "", 0, None, "0")
    k2 = main._feed_cache_key("c1", brand, "", "", 0, None, "3")
    assert k1 != k2


# ---------------------------------------------------------------------------
# Server-side profile + posts hydration/persistence
# ---------------------------------------------------------------------------

def test_persist_and_hydrate_creator_profile_roundtrip(monkeypatch):
    fake = _FakeProfileStore()
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._creator_profile_cache.clear()
    main._last_persisted_brand_hash.clear()
    brand = {"niche": "fitness", "voice": {"x": 1}, "catchphrases": ["c1"]}
    asyncio.run(main._persist_creator_profile("cX", brand))
    assert "cX" in fake.profiles
    main._creator_profile_cache.clear()   # force a real read, not the warm write-through cache
    hydrated = asyncio.run(main._hydrate_creator_profile("cX"))
    assert hydrated == brand


def test_persist_dedups_on_unchanged_hash(monkeypatch):
    fake = _FakeProfileStore()
    write_count = {"n": 0}
    orig_upsert = fake.upsert_creator_profile
    async def counting_upsert(creator_id, brand, brand_hash):
        write_count["n"] += 1
        return await orig_upsert(creator_id, brand, brand_hash)
    fake.upsert_creator_profile = counting_upsert
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._creator_profile_cache.clear()
    main._last_persisted_brand_hash.clear()
    brand = {"niche": "fitness"}
    asyncio.run(main._persist_creator_profile("cY", brand))
    asyncio.run(main._persist_creator_profile("cY", dict(brand)))   # identical brand again
    assert write_count["n"] == 1


def test_get_feed_hydrates_brand_from_stored_profile(monkeypatch):
    """GET /v1/feed (no body) must still see the creator's stored voice/catchphrases —
    written by a prior POST or /v1/scripts call."""
    _reset_feed_caches()
    fake = _FakeProfileStore(profiles={
        "c_hydrate": {"brand": {"niche": "fitness", "voice": {"funnyToSerious": 0.7},
                                "catchphrases": ["hydrated phrase"]}, "brand_hash": "abc"}
    })
    monkeypatch.setattr(main, "_supabase_client", fake)
    captured = {}

    async def fake_fast(sreq):
        captured["sreq"] = sreq
        return {"mode": "mock", "scripts": main.mock_scripts(sreq)}
    monkeypatch.setattr(main, "_fast_feed_scripts", fake_fast)
    monkeypatch.setattr(main, "_maybe_prefetch_next", lambda *a, **k: None)

    resp = client.get("/v1/feed", params={"creator_id": "c_hydrate", "fresh": 1})
    assert resp.status_code == 200
    sreq = captured["sreq"]
    assert sreq.catchphrases == ["hydrated phrase"]
    assert sreq.voice == {"funnyToSerious": 0.7}
    _reset_feed_caches()


def test_get_feed_query_niche_overrides_stored_profile(monkeypatch):
    _reset_feed_caches()
    fake = _FakeProfileStore(profiles={
        "c_override": {"brand": {"niche": "old-niche", "goal": "Grow my audience"}, "brand_hash": "x"}
    })
    monkeypatch.setattr(main, "_supabase_client", fake)
    captured = {}

    async def fake_fast(sreq):
        captured["sreq"] = sreq
        return {"mode": "mock", "scripts": main.mock_scripts(sreq)}
    monkeypatch.setattr(main, "_fast_feed_scripts", fake_fast)
    monkeypatch.setattr(main, "_maybe_prefetch_next", lambda *a, **k: None)

    resp = client.get("/v1/feed", params={"creator_id": "c_override", "niche": "new-niche", "fresh": 1})
    assert resp.status_code == 200
    assert captured["sreq"].niche == "new-niche"
    _reset_feed_caches()


# ---------------------------------------------------------------------------
# Posts hydration for prompt grounding
# ---------------------------------------------------------------------------

def test_creator_posts_hydrates_and_caches(monkeypatch):
    fake = _FakeProfileStore(posts={"c_posts": [{"caption": "real post"}]})
    monkeypatch.setattr(main, "_supabase_client", fake)
    main._creator_posts_cache.clear()
    out = asyncio.run(main._creator_posts("c_posts"))
    assert out == [{"caption": "real post"}]


def test_creator_posts_empty_keyless():
    main._creator_posts_cache.clear()
    out = asyncio.run(main._creator_posts("anyone"))
    assert out == []


def test_persist_creator_posts_caps_at_10_and_strips_fields(monkeypatch):
    fake = _FakeProfileStore()
    monkeypatch.setattr(main, "_supabase_client", fake)
    posts = [{"caption": f"p{i}", "video_url": "https://x/expiring.mp4", "likes": i}
             for i in range(15)]
    asyncio.run(main._persist_creator_posts("c_cap", posts))
    stored = fake.posts["c_cap"]
    assert len(stored) == 10
    assert "video_url" not in stored[0]
    assert stored[0]["caption"] == "p0"


def test_mimic_and_analyze_video_prompts_include_posts():
    posts = [{"caption": "Stop counting reps.", "likes": 900}]
    sys, usr = main.prompts.mimic_prompt(
        {"hook_text": "x", "title": "y", "creator_handle": "z"},
        {"niche": "fitness"}, posts=posts)
    assert "Stop counting reps" in usr

    sys2, usr2 = main.prompts.analyze_video_prompt(
        "https://x", "transcript text", {"niche": "fitness"}, posts=posts)
    assert "Stop counting reps" in usr2
