"""Talking-head gate: speech checks + the model speech label (2026-09-23).

A prod sweep of 140 cached, transcribed reels found song lyrics, "um um um" filler,
workout countdowns and non-English takes passing the talking-head gate. The strings
below are shortened versions of those real transcripts.
"""
import asyncio

import pytest

import main

TH = ("If you sit at a desk all day, your hips get tight and your back starts to round, and "
      "that's why your squat feels awful the first time you try it. Here's what I'd do instead. "
      "Before you load anything heavy, spend five minutes opening your hips up with a couple of "
      "slow lunges and a deep squat hold. Try it for two weeks and tell me how it feels.")


def _reel(transcript, **kw):
    return {"id": "r", "video_url": "https://cdn/v.mp4", "transcribed": True,
            "edit_format": "talking_head", "duration_s": 30, "transcript": transcript, **kw}


@pytest.mark.parametrize("name,transcript", [
    ("chorus", "I hit him with, I hit him with, I hit him with, I hit him with, I hit him with, "
               "I hit him with, I hit him with a left and a right and a left and a right."),
    ("filler", " ".join(["um"] * 37)),
    ("portuguese_lyrics", "No baile é cagão, mina linda perigosa, caga meu coração. Vai peidando, "
                          "vai cagando, no baile é cagão, mina linda perigosa, caga meu coração."),
    ("spanish_take", "Hola, amores. Hoy quiero compartir con vosotros cómo me preparo para ir al "
                     "gimnasio por la mañana antes de trabajar, con un desayuno rápido y fácil."),
    ("countdown", "You ready? 5, 4, 3, 2, 1, go! Round 1. 3, 2, 1, stop! Round 2. 5, 4, 3, 2, 1, "
                  "go! Round 3. 3, 2, 1, stop! Round 4. 5, 4, 3, 2, 1, go! Halfway there."),
])
def test_non_speech_transcripts_fail_the_gate(name, transcript):
    assert main._speech_red_flag(transcript), name
    assert main._is_talking_head_reel(_reel(transcript)) is False, name


def test_a_real_talking_head_passes():
    assert main._speech_red_flag(TH) is None
    assert main._is_talking_head_reel(_reel(TH)) is True


@pytest.mark.parametrize("kind,expected", [("talking", True), ("lyrics", False),
                                           ("skit", False), ("other", False), ("", True)])
def test_model_speech_label_overrides(kind, expected):
    assert main._is_talking_head_reel(_reel(TH, speech_kind=kind)) is expected


def test_classifier_labels_in_place_and_skips_labeled(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    from app import palo_llm
    seen = []

    async def fake(sys, user, schema, model, max_tokens=4000, temperature=None):
        assert temperature == 0                                # labels must be stable
        seen.append(user)
        return {"items": [{"i": 0, "kind": "lyrics"}, {"i": 1, "kind": "talking"},
                          {"i": 7, "kind": "skit"}]}          # out-of-range index ignored

    monkeypatch.setattr(palo_llm, "anthropic_cached_json", fake)
    posts = [{"transcript": "some say you will love me one day"}, {"transcript": TH},
             {"transcript": TH, "speech_kind": "talking"},     # already labeled: not re-sent
             {"transcript": ""}]                               # nothing to judge
    asyncio.run(main._classify_reel_speech(posts))
    assert [p.get("speech_kind") for p in posts] == ["lyrics", "talking", "talking", None]
    assert len(seen) == 1 and "[0]" in seen[0] and "[1]" in seen[0] and "[2]" not in seen[0]


def test_classifier_failure_leaves_posts_unlabeled(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    from app import palo_llm

    async def boom(*a, **k):
        return None                                            # the helper never raises

    monkeypatch.setattr(palo_llm, "anthropic_cached_json", boom)
    posts = [{"transcript": TH}]
    asyncio.run(main._classify_reel_speech(posts))
    assert "speech_kind" not in posts[0]


def test_classifier_keyless_is_a_noop(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    posts = [{"transcript": TH}]
    asyncio.run(main._classify_reel_speech(posts))
    assert "speech_kind" not in posts[0]


def test_label_carries_forward_with_the_transcript(monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_URL", "https://sb.example.co")
    post = {"platform": "instagram", "author": "a", "id": "1"}
    rid = main._reel_public_id(post, "a", "instagram", 0)
    prev = [{"id": rid, "transcribed": True, "transcript": TH, "speech_kind": "lyrics"}]
    main._merge_prev_reel_work([post], prev)
    assert post["transcript"] == TH and post["speech_kind"] == "lyrics"
    served = main._reel_from_post(post, "a", "instagram", 0, False)
    assert served["speech_kind"] == "lyrics" and main._is_talking_head_reel(served) is False


def test_niche_refresh_drops_video_less_posts_before_ranking(monkeypatch):
    """Prod 'fitness' was 11 TikTok photo slideshows (millions of views, no video) and 0
    playable reels: ranking by views let them crowd every real video out."""
    niche = "slideshow-niche"
    key = main._niche_cache_key(niche)
    main._niche_reels_cache.pop(key, None)
    slides = [{"author": f"s{i}", "platform": "tiktok", "views": 5_000_000, "likes": 1,
               "caption": "summer body", "video_url": "", "thumbnail_url": "https://cdn/s.jpg",
               "id": f"s{i}"} for i in range(20)]
    videos = [{"author": f"v{i}", "platform": "tiktok", "views": 40_000, "likes": 1,
               "caption": "desk posture fix", "video_url": f"https://cdn/v{i}.mp4",
               "thumbnail_url": "https://cdn/v.jpg", "id": f"v{i}"} for i in range(3)]

    async def fake_scrape(n, limit=20):
        return slides + videos

    async def passthrough(ps, top_n=4, max_wait_s=180):
        return ps

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(main, "_supabase_client", None)
    monkeypatch.setattr(main, "scrape_niche_posts", fake_scrape)
    monkeypatch.setattr(main, "_transcribe_top_posts", passthrough)
    monkeypatch.setattr(main, "_rehost_reel_media", noop)
    monkeypatch.setattr(main, "_classify_reel_speech", noop)
    monkeypatch.setattr(main, "_dossier_classify_reels", noop)
    asyncio.run(main._refresh_niche_reels(niche))
    reels = main._niche_reels_cache[key]["reels"]
    assert len(reels) == 3 and all(r["video_url"] for r in reels)
    main._niche_reels_cache.pop(key, None)


def test_instagram_niche_scrape_asks_for_reels(monkeypatch):
    """The hashtag scraper's default "posts" mode returned image posts with no video and
    no play count, so Instagram never contributed a single reel."""
    seen = {}

    async def fake_actor(actor, payload, timeout_s=110):
        seen[actor] = payload
        return []

    monkeypatch.setattr(main, "APIFY_KEY", "k")
    monkeypatch.setattr(main, "_run_apify_actor", fake_actor)
    asyncio.run(main.scrape_niche_posts("fitness"))
    assert seen["apify~instagram-hashtag-scraper"]["resultsType"] == "reels"


# --- analyze-video link resolution (2026-09-23) --------------------------------------

@pytest.mark.parametrize("url,direct", [
    ("https://cdn.example.com/clip.mp4", True),
    ("https://cdn.example.com/clip.MOV?token=1", True),
    ("https://www.tiktok.com/@a/video/123", False),
    ("https://www.instagram.com/reel/abc/", False),
])
def test_direct_media_links_skip_the_scraper(monkeypatch, url, direct):
    monkeypatch.setattr(main, "SUPABASE_URL", "https://sb.example.co")
    assert main._is_direct_media_url(url) is direct
    assert main._is_direct_media_url("https://sb.example.co/storage/v1/object/public/marque-clips/reels/x") is True


def test_tiktok_links_resolve_through_nested_media_fields(monkeypatch):
    """TikTok items carry the video under videoMeta.downloadAddr / mediaUrls; the old
    resolver only read top-level keys, so every pasted TikTok link fell back to the
    canned structure."""
    async def fake_actor(actor, payload, timeout_s=110):
        assert actor == "clockworks~tiktok-scraper" and payload["postURLs"]
        return [{"text": "cap", "videoMeta": {"duration": 30},
                 "mediaUrls": ["https://api.apify.com/v2/key-value-stores/k/records/v.mp4"]}]

    monkeypatch.setattr(main, "APIFY_KEY", "k")
    monkeypatch.setattr(main, "_run_apify_actor", fake_actor)
    got = asyncio.run(main._resolve_post_media("https://www.tiktok.com/@a/video/123"))
    assert got == "https://api.apify.com/v2/key-value-stores/k/records/v.mp4"


def test_direct_link_resolves_without_apify(monkeypatch):
    monkeypatch.setattr(main, "APIFY_KEY", "")
    assert asyncio.run(main._resolve_post_media("https://cdn.example.com/c.mp4")) == "https://cdn.example.com/c.mp4"
    assert asyncio.run(main._resolve_post_media("https://youtube.com/watch?v=1")) is None
