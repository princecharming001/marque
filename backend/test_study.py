"""Unit tests for the study-the-winners harness (eval/study/*) — canned
fixtures only, no network, fake OCR engine injectable."""
import json

import pytest

from eval.study import broll as broll_mod
from eval.study import cards as cards_mod
from eval.study import ocr_track
from eval.study.common import norm_token, reel_id
from eval.study.ocr_track import FrameRec


def _line(text, y0=0.58, y1=0.66, conf=0.95, x0=0.1, x1=0.9):
    return {"text": text, "conf": conf, "bbox": [x0, y0, x1, y1]}


def _frames(specs, fps=5.0):
    """specs: list of (list_of_lines) per frame index."""
    return [FrameRec(t=(i + 0.5) / fps, lines=lines) for i, lines in enumerate(specs)]


# --- chunker -----------------------------------------------------------------

def test_chunker_merges_stable_text_and_splits_on_change():
    fr = _frames([[_line("THIS ONE MISTAKE")]] * 4
                 + [[_line("KILLS YOUR REACH")]] * 5
                 + [[]] * 2)
    chunks = ocr_track.chunk_caption_track(fr, 5.0)
    assert len(chunks) == 2
    assert chunks[0]["text"] == "THIS ONE MISTAKE" and chunks[0]["n_words"] == 3
    assert chunks[1]["text"] == "KILLS YOUR REACH"
    assert chunks[0]["case"] == "upper"
    assert 0.55 < chunks[0]["y_center"] < 0.68


def test_chunker_karaoke_reveal_grows_one_chunk():
    # karaoke reveals grow the visible text; similarity stays high frame-to-frame
    fr = _frames([[_line("nobody tells")], [_line("nobody tells")],
                  [_line("nobody tells you")], [_line("nobody tells you this")],
                  [_line("nobody tells you this")], [_line("nobody tells you this")]])
    chunks = ocr_track.chunk_caption_track(fr, 5.0)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "nobody tells you this"   # longest reveal kept
    assert chunks[0]["case"] == "lower"


def test_chunker_routes_out_of_band_text_away():
    # headline at y~0.2 must not join the caption band chunks
    fr = _frames([[_line("3 MONEY RULES", y0=0.18, y1=0.30), _line("first rule here")]] * 6)
    chunks = ocr_track.chunk_caption_track(fr, 5.0)
    assert len(chunks) == 1
    assert chunks[0]["text"] == "first rule here"


def test_alignment_duplicate_words_stay_local():
    chunks = [{"text": "the fix", "t0": 5.0, "t1": 5.6},
              {"text": "the trap", "t0": 9.0, "t1": 9.7}]
    words = [{"word": "the", "start_ms": 4900}, {"word": "fix", "start_ms": 5150},
             {"word": "the", "start_ms": 8950}, {"word": "trap", "start_ms": 9200}]
    rate = ocr_track.align_captions_to_speech(chunks, words)
    assert rate == 1.0
    assert chunks[0]["lead_ms"] == 100     # 5000 - 4900: matched the NEAR "the"
    assert chunks[1]["lead_ms"] == 50      # 9000 - 8950, not the t=4.9s "the"


# --- broll -------------------------------------------------------------------

def test_label_decision_table():
    assert broll_mod.label_shot("face", True) == "face"
    assert broll_mod.label_shot("broll", False) == "broll_fullscreen"
    assert broll_mod.label_shot("broll", True) == "broll_overlay"
    assert broll_mod.label_shot("graphic text card", False) == "graphic"
    assert broll_mod.label_shot("screen recording", True) == "screen"
    assert broll_mod.label_shot("???", False) == "face"     # conservative default


def test_segment_merge_flash_bridge_and_stats():
    shots = [
        {"t0": 0.0, "t1": 4.0, "label": "face"},
        {"t0": 4.0, "t1": 6.0, "label": "broll_fullscreen"},
        {"t0": 6.0, "t1": 6.2, "label": "face"},              # flash — bridged
        {"t0": 6.2, "t1": 8.0, "label": "broll_fullscreen"},
        {"t0": 8.0, "t1": 20.0, "label": "face"},
        {"t0": 20.0, "t1": 22.5, "label": "broll_overlay"},
        {"t0": 22.5, "t1": 30.0, "label": "face"},
    ]
    segs = broll_mod.merge_segments(shots, 30.0)
    assert len(segs) == 2, segs
    assert segs[0]["t0"] == 4.0 and segs[0]["t1"] == 8.0      # bridged across flash
    assert segs[0]["mode"] == "fullscreen"
    assert segs[1]["mode"] == "overlay"
    stats = broll_mod.segment_stats(segs, 30.0)
    assert stats["count"] == 2 and stats["per_30s"] == 2.0
    assert stats["first_onset_s"] == 4.0
    assert stats["pct_overlay"] == 0.5


# --- cards -------------------------------------------------------------------

def test_title_card_needs_heuristic_and_vision_agreement():
    fr = _frames([[_line("STOP DOING THIS", y0=0.15, y1=0.28)]] * 6
                 + [[_line("normal caption")]] * 10)
    shots = [{"t0": 0.0, "t1": 3.2, "label": "face"}]
    band = (0.55, 0.70)
    yes = cards_mod.detect_title_card(fr, shots, band, {"has_title_card": True,
                                                        "text": "STOP DOING THIS"})
    assert yes["present"] and yes["style"] == "overlay_headline"
    refuted = cards_mod.detect_title_card(fr, shots, band, {"has_title_card": False})
    assert not refuted["present"]


def test_cta_channels_and_pattern():
    words = ([{"word": w, "start_ms": 1000 * i, "end_ms": 1000 * i + 300}
              for i, w in enumerate("here is the whole story about the thing".split())]
             + [{"word": "follow", "start_ms": 26_000, "end_ms": 26_400},
                {"word": "for", "start_ms": 26_450, "end_ms": 26_600},
                {"word": "part", "start_ms": 26_650, "end_ms": 26_900},
                {"word": "2", "start_ms": 26_950, "end_ms": 27_100}])
    shots = [{"t0": 0.0, "t1": 25.0, "label": "face"},
             {"t0": 25.0, "t1": 28.0, "label": "graphic"}]
    cta = cards_mod.detect_cta(words, [], None, "comment 'GUIDE' for the link",
                               shots, 28.0, {"has_end_card": True,
                                             "end_card_text": "FOLLOW FOR PART 2"})
    assert cta["spoken"]["present"] and cta["spoken"]["phrase_class"] in ("follow", "part_2")
    assert cta["end_card"]["present"] and cta["end_card"]["text"] == "FOLLOW FOR PART 2"
    assert cta["post_caption_cta"]["phrase_class"] == "comment_keyword"
    assert cards_mod.cta_pattern(cta) == "end_card+spoken"


def test_cta_spoken_only_pattern():
    cta = {"spoken": {"present": True}, "text_overlay": {"present": False},
           "end_card": {"present": False}, "post_caption_cta": {"present": False}}
    assert cards_mod.cta_pattern(cta) == "spoken_only"


# --- fake engine + track read ------------------------------------------------

def test_fake_engine_track(tmp_path):
    eng = ocr_track.get_engine("fake", canned={0: [_line("hello world")]})
    out = eng.read_batch(["a.jpg", "b.jpg"])
    assert out[0][0]["text"] == "hello world" and out[1] == []


# --- aggregate + verify ------------------------------------------------------

def _mini_anatomy(rid, wpc, y, views=100_000, platform="instagram"):
    return {"schema_version": 1, "reel_id": rid, "platform": platform,
            "niche": "fitness", "views": views, "likes": 10, "duration_s": 30.0,
            "transcript": {"n_words": 80, "wpm": 160},
            "captions": {"present": True, "coverage_pct": 0.9,
                          "words_per_chunk_median": wpc, "y_center_median": y,
                          "pct_all_caps": 1.0, "lead_ms_median": -80,
                          "speech_match_rate": 0.9, "chunks": []},
            "caption_style": {"karaoke_highlight": True, "stroke": True,
                               "boxed": False, "font_weight": "heavy",
                               "emoji_in_captions": False},
            "content_type": "how_to",
            "cut_stats": {"n_cuts": 10, "asl_s": 2.7, "cuts_per_30s": 10.0},
            "broll": {"segments": [{"t0": 5, "t1": 7.5, "dur_s": 2.5,
                                     "mode": "fullscreen", "kind": "stock",
                                     "zone_pct": 0.17, "n_shots": 1}],
                       "count": 1, "per_30s": 1.0, "dur_median_s": 2.5,
                       "gap_median_s": None, "first_onset_s": 5.0,
                       "pct_overlay": 0.0, "share_of_runtime": 0.08},
            "title_card": {"present": rid.endswith("1")},
            "cta": {"spoken": {"present": True}, "text_overlay": {"present": False},
                     "end_card": {"present": False},
                     "post_caption_cta": {"present": False}, "pattern": "spoken_only"},
            "failures": []}


def test_aggregate_medians_and_exemplars(tmp_path, monkeypatch):
    from eval.study import aggregate as agg_mod
    reels = [_mini_anatomy("ig_1", 3, 0.60), _mini_anatomy("ig_2", 4, 0.62),
             _mini_anatomy("ig_3", 5, 0.64)]
    agg = agg_mod.compute(reels)
    m = agg["metrics"]["words_per_chunk"]
    assert m["median"] == 4 and m["n"] == 3
    assert "ig_2" in m["exemplars"]
    assert m["directional"] is True        # n=3 < CORPUS_FLOOR
    assert agg["cta_patterns"] == {"spoken_only": 3}
    assert agg["title_card"]["overall"]["pct"] == pytest.approx(1 / 3, abs=0.01)


def test_verify_catches_poisoned_aggregate(tmp_path, monkeypatch):
    from eval.study import verify as verify_mod
    from eval.study import common as common_mod
    anat = tmp_path / "anatomy"
    outd = tmp_path / "out"
    anat.mkdir(); outd.mkdir()
    for rid, wpc in (("ig_1", 3), ("ig_2", 4), ("ig_3", 5)):
        (anat / f"{rid}.json").write_text(json.dumps(_mini_anatomy(rid, wpc, 0.6)))
    poisoned = {"n_total": 3, "n_ig": 3,
                "metrics": {"words_per_chunk": {
                    "median": 9, "iqr": [8, 10], "n": 3,      # lies
                    "exemplars": ["ig_1"], "directional": False}},
                "title_card": {}, "cta_patterns": {}, "caption_style": {}}
    (outd / "aggregates.json").write_text(json.dumps(poisoned))
    monkeypatch.setattr(verify_mod, "ANATOMY_DIR", anat)
    monkeypatch.setattr(verify_mod, "OUT_DIR", outd)
    rc = verify_mod.run()
    assert rc == 1
    report = (outd / "verify_report.md").read_text()
    assert "verify_mismatch" in report and "verify_exemplar" in report


def test_reel_id_stable_and_norm_token():
    assert reel_id("instagram", "https://x/reel/A") == reel_id("instagram", "https://x/reel/A")
    assert norm_token("Hello!!") == "hello"
