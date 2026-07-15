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


# ── /v1/broll-styles: the composition-style picker ───────────────────────────
# WHICH treatment (cutaway/panel/floating-card/green-screen/split-screen), not how much.
# Demos are self-rendered through the real pipeline (durable Supabase URLs), not matched
# from the scraped-reel corpus — the classifier can't reliably tag these treatments.

def test_broll_styles_options_are_composition_treatments():
    body = client.get("/v1/broll-styles").json()
    styles = body["styles"]
    assert body["mode"] == "live"
    assert [s["id"] for s in styles] == ["cutaway", "panel", "card", "green_screen", "split_screen"]
    assert all(s["label"] and s["blurb"] for s in styles)
    assert all(not s["sample"] for s in styles)
    # every option carries a distinct, durably-hosted demo
    vids = [s["video_url"] for s in styles]
    assert len(set(vids)) == len(vids)
    assert all(v.startswith("https://") and v.endswith(".mp4") for v in vids)


def test_broll_styles_config_keys_map_to_the_right_override():
    ids_by_key = {}
    for opt in main._COMPOSITION_STYLE_OPTIONS:
        ids_by_key.setdefault(opt["config_key"], []).append(opt["id"])
    assert ids_by_key["broll_mode"] == ["cutaway", "panel", "card"]
    assert ids_by_key["composition_style"] == ["green_screen", "split_screen"]


def test_composition_style_config_overrides_job_style(monkeypatch):
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "")   # keyless: job created without real work
    r = client.post("/v1/clips", json={
        "source_id": "s1", "formats": ["myth-buster"],
        "script": {"title": "t", "body": "hello"},
        "config": {"composition_style": "green_screen"},
        "analyze_first": True,
    })
    job_id = r.json()["job_id"]
    assert main._clip_jobs[job_id]["style"] == "green_screen"

    r2 = client.post("/v1/clips", json={
        "source_id": "s2", "formats": ["myth-buster"],
        "script": {"title": "t", "body": "hello"},
        "config": {"composition_style": "split_screen"},
        "react_source_url": "http://x/react.mp4",
        "analyze_first": True,
    })
    job_id2 = r2.json()["job_id"]
    assert main._clip_jobs[job_id2]["style"] == "duet_split"


def test_split_screen_without_react_source_warns(monkeypatch):
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "")
    r = client.post("/v1/clips", json={
        "source_id": "s3", "formats": ["myth-buster"],
        "script": {"title": "t", "body": "hello"},
        "config": {"composition_style": "split_screen"},
        "analyze_first": True,
    })
    job_id = r.json()["job_id"]
    warnings = main._clip_jobs[job_id]["clips"][0].get("warnings", [])
    assert any(w.startswith("react_source_missing") for w in warnings)


def test_broll_mode_forces_every_broll_items_mode():
    from app import edl as edl_mod
    words = [{"word": "hi", "start_ms": 0, "end_ms": 10000}]
    plan = {"broll": [{"range": [50, 100], "cue": "x", "mode": "full"},
                      {"range": [200, 250], "cue": "y"}]}
    out = edl_mod.assemble_edl(plan, words, "broll_cutaway", "myth-buster",
                               prefs={"broll_mode": "panel"})
    assert out.broll   # sanity: the fixture actually produced items
    modes = {b.mode for b in out.broll}
    assert modes == {"panel"}


def test_client_telemetry_endpoint_logs_and_never_fails():
    r = client.post("/v1/telemetry/client", json={"event": "upload_failed",
                                                  "detail": "http 413 | 92MB",
                                                  "creator_id": "c1", "build": "29"})
    assert r.status_code == 200 and r.json()["ok"] is True
    # junk-tolerant: missing fields still 200 (breadcrumbs must never error)
    assert client.post("/v1/telemetry/client", json={}).status_code == 200
