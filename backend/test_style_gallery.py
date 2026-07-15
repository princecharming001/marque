"""Item 3+4: the 'match a vibe' style gallery drives the edit via theme_id, and
recommended reels are hard-filtered to talking-head only."""
import main
from fastapi.testclient import TestClient

client = TestClient(main.app)


def test_is_talking_head_reel_hard_filter():
    assert main._is_talking_head_reel({"edit_format": "talking_head"})
    assert main._is_talking_head_reel({"edit_format": "talking_head_broll"})
    assert not main._is_talking_head_reel({"edit_format": "recap_music"})
    assert not main._is_talking_head_reel({"edit_format": "recap_voiceover"})
    # unclassified montage: caption + short duration → heuristic says recap_music → excluded
    assert not main._is_talking_head_reel(
        {"caption": "#montage sound on 🎵", "duration_s": 12, "transcript": ""})
    # unclassified spoken take → heuristic says talking_head → kept
    assert main._is_talking_head_reel(
        {"caption": "here is the one thing nobody tells you about starting out honestly",
         "duration_s": 40, "transcript": "here is the one thing nobody tells you " * 6})


def test_styles_endpoint_returns_theme_driven_options():
    r = client.get("/v1/styles", params={"niche": "fitness"})
    assert r.status_code == 200
    body = r.json()
    styles = body["styles"]
    # every style option carries a real theme_id the edit pipeline consumes + human copy
    assert len(styles) == len(main._STYLE_GALLERY_ORDER)
    ids = [s["theme_id"] for s in styles]
    assert ids == main._STYLE_GALLERY_ORDER
    for s in styles:
        assert s["label"] and s["blurb"]
        assert "video_url" in s and "sample" in s
    # faceless_explainer (voiceover treatment) is deliberately not a talking-head style
    assert "faceless_explainer" not in ids


def test_style_theme_ids_are_real_themes():
    for tid in main._STYLE_GALLERY_ORDER:
        th = main.themes_mod.get_theme(tid)
        assert th.id == tid   # get_theme falls back to default for unknown ids


def _th_reel(rid, handle, url, views=1000):
    return {"id": rid, "creator_handle": handle, "video_url": url,
            "thumbnail_url": url + "-t", "edit_format": "talking_head", "views": views}


def test_style_demos_are_distinct_and_sourced_cross_niche(monkeypatch):
    # Two DIFFERENT niches in the cache — demos must pool across both, and each style
    # must get a DISTINCT video (no modulo repeat).
    monkeypatch.setattr(main, "_niche_reels_cache", {
        "niche:cooking": {"reels": [_th_reel("a", "chef1", "http://x/a", 5000),
                                    _th_reel("b", "chef2", "http://x/b", 4000)], "ts": 9e18},
        "niche:finance": {"reels": [_th_reel("c", "fin1", "http://x/c", 3000),
                                    _th_reel("d", "fin2", "http://x/d", 2000),
                                    _th_reel("e", "fin3", "http://x/e", 1000)], "ts": 9e18},
    })
    monkeypatch.setattr(main, "_watched_reels_cache", {})
    r = client.get("/v1/styles", params={"niche": "gardening"})   # niche NOT in cache
    styles = r.json()["styles"]
    vids = [s["video_url"] for s in styles if not s["sample"]]
    assert len(vids) == 5                      # 5 styles, 5 distinct demos from the pool
    assert len(set(vids)) == len(vids)         # all distinct — no repeats across styles


def test_style_demos_dedupe_by_creator(monkeypatch):
    # Same creator posting two reels must not occupy two style slots.
    monkeypatch.setattr(main, "_niche_reels_cache", {
        "niche:x": {"reels": [_th_reel("a", "same", "http://x/a", 9),
                              _th_reel("b", "same", "http://x/b", 8),
                              _th_reel("c", "other", "http://x/c", 7)], "ts": 9e18}})
    monkeypatch.setattr(main, "_watched_reels_cache", {})
    styles = client.get("/v1/styles", params={"niche": "x"}).json()["styles"]
    handles = [s["handle"] for s in styles if not s["sample"]]
    assert len(handles) == len(set(handles))   # each creator appears at most once


def test_reels_never_serve_unplayable_cards(monkeypatch):
    # A talking-head reel with NO video_url must be dropped (static-card bug), even though
    # it passes the talking-head filter.
    monkeypatch.setattr(main, "APIFY_KEY", "fake")
    async def _hydrate(*a, **k): return None
    monkeypatch.setattr(main, "_hydrate_reels_caches", _hydrate)
    monkeypatch.setattr(main, "_watched_real_reels", lambda parsed: [])
    monkeypatch.setattr(main, "_niche_real_reels", lambda niche: [
        {**_th_reel("p", "has", "http://x/p")},
        {"id": "q", "creator_handle": "none", "video_url": "", "edit_format": "talking_head"},
    ])
    body = client.get("/v1/reels", params={"niche": "x"}).json()
    assert body["mode"] == "live"
    assert all(r.get("video_url") for r in body["reels"])
    assert "q" not in [r["id"] for r in body["reels"]]
