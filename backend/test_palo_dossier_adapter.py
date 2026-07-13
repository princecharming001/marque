"""Phase 4 box 1 — dossier -> analysis-block adapter (RISK #1), pure/keyless."""
from __future__ import annotations

from app import dossier_adapter as da

FULL = {
    "id": "v1", "title": "How I edit fast", "views": 120000,
    "transcript": "here is the thing nobody tells you about editing",
    "dossier": {
        "first_frame": {"desc": "close-up on the timeline", "pattern_interrupt": True, "score": 0.8},
        "delivery_curve": [{"energy": 0.6}, {"energy": 0.95}, {"energy": 0.5}],
        "visual_events": [{"kind": "cut", "desc": "hard cut to b-roll"},
                          {"kind": "zoom", "desc": "punch-in on face"}],
        "gaffes": [{"desc": "audio dips at 0:12"}],
    },
}


def test_full_block_has_signal():
    b = da.dossier_to_analysis_block(FULL)
    assert "How I edit fast (120,000 views)" in b
    assert "pattern interrupt" in b
    assert "opens 0.6, peaks 0.9, ends 0.5" in b            # energy summary (0.95 -> 0.9)
    assert "cut: hard cut to b-roll" in b
    assert "nobody tells you" in b
    assert "audio dips" in b


def test_thin_video_degrades_not_fails():
    b = da.dossier_to_analysis_block({"title": "Raw clip"})
    assert "Raw clip (views n/a)" in b
    assert "not analyzed" in b                              # no dossier -> graceful


def test_transcript_as_word_list():
    b = da.dossier_to_analysis_block({"title": "T", "transcript": [{"word": "hello"}, {"word": "world"}]})
    assert "hello world" in b


def test_catalog_ranks_by_views_and_limits():
    vids = [{"title": "lo", "views": 10}, {"title": "hi", "views": 9000}, {"title": "mid", "views": 500}]
    block = da.catalog_block(vids, limit=2)
    assert block.index("hi") < block.index("mid")           # best-performing first
    assert "lo" not in block                                # limited to top 2
    assert da.catalog_block([]) == ""
