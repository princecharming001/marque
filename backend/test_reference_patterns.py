"""P2.3: reference-reel patterns via the dossier adapter (keyless)."""
from __future__ import annotations

import asyncio

import prompts
from app import dossier as D


def _run(coro):
    return asyncio.run(coro)


def test_reference_patterns_derived_from_dossier():
    pat = D.reference_patterns(D.mock_dossier(30000), 30000)
    assert pat is not None
    assert pat["cut_density_per_s"] >= 0
    assert pat["caption_style"] in ("heavy", "some", "none")
    assert isinstance(pat["hook_layers"], int) and 0 <= pat["hook_layers"] <= 3
    assert isinstance(pat["energy_curve"], list)


def test_reference_patterns_none_without_dossier():
    assert D.reference_patterns(None) is None


def test_reference_dossier_cached_per_url(monkeypatch):
    D._reference_dossier_cache.clear()
    calls = {"n": 0}

    async def fake_gen(url, dur, provider=None):
        calls["n"] += 1
        return D.mock_dossier(dur)
    monkeypatch.setattr(D, "generate_dossier", fake_gen)

    d1 = _run(D.dossier_for_reference("http://x/reel.mp4", 30000))
    d2 = _run(D.dossier_for_reference("http://x/reel.mp4", 30000))
    assert d1 is d2 and calls["n"] == 1  # second call served from cache


def test_reference_block_renders_measured_patterns():
    reel = {"creator_handle": "@x", "title": "t",
            "patterns": {"cut_density_per_s": 1.5, "cuts": 6, "caption_style": "heavy",
                         "overlay_usage": 4, "hook_layers": 3, "hook_pattern_interrupt": True,
                         "energy_curve": [0.9, 0.6]}}
    block = prompts._reference_reel_block(reel)
    assert "MEASURED patterns" in block
    assert "1.5/s" in block and "heavy" in block


def test_clean_reference_reel_whitelists_video_url():
    import main
    clean = main._clean_reference_reel({"title": "t", "video_url": "https://cdn/x.mp4", "evil": "drop"})
    assert clean.get("video_url") == "https://cdn/x.mp4"
    assert "evil" not in clean
    # non-http rejected
    clean2 = main._clean_reference_reel({"title": "t", "video_url": "javascript:alert(1)"})
    assert "video_url" not in clean2


def test_resolve_reference_patterns_off_is_noop(monkeypatch):
    import main
    monkeypatch.setattr(D, "VIDEO_UNDERSTANDING", "off")
    job = {"reference_reel": {"title": "t", "video_url": "https://cdn/x.mp4"}}
    _run(main._resolve_reference_patterns(job))
    assert "patterns" not in job["reference_reel"]


def test_resolve_reference_patterns_populates(monkeypatch):
    import main
    monkeypatch.setattr(D, "VIDEO_UNDERSTANDING", "claude_frames")
    D._reference_dossier_cache.clear()

    async def fake_gen(url, dur, provider=None):
        return D.mock_dossier(dur or 30000)
    monkeypatch.setattr(D, "generate_dossier", fake_gen)

    job = {"reference_reel": {"title": "t", "video_url": "https://cdn/x.mp4"},
           "reference_duration_ms": 30000}
    _run(main._resolve_reference_patterns(job))
    assert "patterns" in job["reference_reel"]
    assert job["reference_reel"]["patterns"]["cuts"] >= 0
