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
