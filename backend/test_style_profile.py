"""Editing-style taste profile: vector derivation, Rocchio, and the profile->config map."""
import json
import os

import pytest

from app import style_profile as sp

_CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "assets", "style_mapping_cases.json")


def _anatomy(**over):
    """A measured reel record shaped like the study's anatomy JSON."""
    base = {
        "cut_stats": {"cuts_per_30s": 7.0},
        "transcript": {"wpm": 170},
        "captions": {"words_per_chunk_median": 3, "pct_all_caps": 0.0},
        "caption_style": {"font_weight": "regular", "boxed": False, "stroke": True},
        "broll": {"per_30s": 1.0, "share_of_runtime": 0.1, "pct_overlay": 0.2, "count": 2},
        "title_card": {"present": False},
        "cta": {"pattern": "spoken_only"},
    }
    base.update(over)
    return base


# --- vector derivation ------------------------------------------------------

def test_vector_is_normalized_and_complete():
    v = sp.vector_from_anatomy(_anatomy())
    assert set(v) == set(sp.DIMS)
    assert all(0.0 <= x <= 1.0 for x in v.values())


def test_anchors_place_a_mid_reel_mid_scale():
    v = sp.vector_from_anatomy(_anatomy())
    assert 0.3 < v["pace"] < 0.7, "7 cuts/30s should sit mid-scale, not pinned"
    assert 0.3 < v["energy"] < 0.7


def test_caption_boldness_composites_the_four_signals():
    plain = sp.vector_from_anatomy(_anatomy(
        captions={"words_per_chunk_median": 5, "pct_all_caps": 0.0},
        caption_style={"font_weight": "regular", "boxed": False, "stroke": False}))
    loud = sp.vector_from_anatomy(_anatomy(
        captions={"words_per_chunk_median": 1, "pct_all_caps": 1.0},
        caption_style={"font_weight": "heavy", "boxed": True, "stroke": True}))
    assert plain["caption_boldness"] == 0.0
    assert loud["caption_boldness"] == 1.0
    assert loud["caption_chunking"] == 1.0, "1 word/chunk = maximum chunking"
    assert plain["caption_chunking"] < 0.4


def test_no_broll_imputes_the_norm_rather_than_scoring_zero():
    """A reel with no b-roll says nothing about overlay taste; scoring 0 would read as
    'hates overlays' and drag the profile."""
    v = sp.vector_from_anatomy(_anatomy(
        broll={"per_30s": 0.0, "share_of_runtime": 0.0, "pct_overlay": 0.0, "count": 0}))
    assert v["broll_overlay_bias"] == pytest.approx(0.30)
    assert v["broll_density"] == 0.0


def test_flair_reads_title_card_and_cta_pattern():
    none = sp.vector_from_anatomy(_anatomy())
    both = sp.vector_from_anatomy(_anatomy(title_card={"present": True},
                                           cta={"pattern": "end_card+spoken"}))
    assert none["title_cta_flair"] == 0.0
    assert both["title_cta_flair"] == 1.0


# --- Rocchio ----------------------------------------------------------------

def test_too_few_likes_keeps_the_cold_start_exactly():
    hot = {d: 1.0 for d in sp.DIMS}
    assert sp.rocchio([hot, hot], [], base=sp.COLD_START) == sp.COLD_START


def test_likes_pull_toward_liked_attributes_dislikes_push_away():
    hot = {d: 1.0 for d in sp.DIMS}
    cold = {d: 0.0 for d in sp.DIMS}
    up = sp.rocchio([hot] * 3, [], base=sp.COLD_START)
    down = sp.rocchio([cold] * 3, [hot], base=sp.COLD_START)
    assert up["pace"] > sp.COLD_START["pace"]
    assert down["pace"] < sp.COLD_START["pace"]


def test_super_likes_count_double():
    a = {**{d: 0.0 for d in sp.DIMS}, "pace": 1.0}
    b = {**{d: 0.0 for d in sp.DIMS}, "pace": 0.0}
    plain = sp.rocchio([a, b, b], [], base=sp.COLD_START)
    supered = sp.rocchio([a, b, b], [], like_weights=[2.0, 1.0, 1.0], base=sp.COLD_START)
    assert supered["pace"] > plain["pace"]


def test_output_always_stays_in_range():
    hot = {d: 1.0 for d in sp.DIMS}
    cold = {d: 0.0 for d in sp.DIMS}
    for p in (sp.rocchio([hot] * 5, [], base=hot), sp.rocchio([cold] * 5, [hot] * 5, base=cold)):
        assert all(0.0 <= v <= 1.0 for v in p.values())


# --- profile -> config ------------------------------------------------------

def test_cold_start_maps_to_todays_defaults():
    """A creator who skips the taste quiz must get EXACTLY the current pipeline: no
    caption size claim, meme 1, standard density/trim."""
    cfg = sp.map_profile_to_config(sp.COLD_START)
    assert cfg == {"theme_id": "clean_creator", "caption_style": "clean",
                   "meme_intensity": "1", "interrupt_density": "standard",
                   "filler_trim": "standard", "broll_mode": "cutaway"}
    # clean_creator IS the shipped default theme (themes.py) and these knob values ARE
    # _KNOB_DEFAULTS, so an un-quizzed creator still gets exactly today's pipeline.


def test_golden_mapping_cases_hold():
    """These same cases are asserted by the Swift mirror — if either side drifts, the
    app would preview a style the backend wouldn't render."""
    with open(_CASES) as f:
        data = json.load(f)
    assert data["schema_version"] == sp.SCHEMA_VERSION
    for case in data["cases"]:
        got = sp.map_profile_to_config(case["profile"])
        assert got == case["expect"], f"{case['name']}: mapping drifted"


def test_normalize_fills_missing_dims_from_cold_start():
    v = sp.normalize({"dims": {"pace": 0.9}})
    assert v["pace"] == 0.9
    assert v["energy"] == sp.COLD_START["energy"]
    assert set(v) == set(sp.DIMS)
    assert sp.normalize(None) == sp.COLD_START


def test_normalize_clamps_hostile_input():
    v = sp.normalize({"pace": 9.0, "energy": -3.0})
    assert v["pace"] == 1.0 and v["energy"] == 0.0


def test_intensity_is_monotonic_in_its_inputs():
    low = {d: 0.0 for d in sp.DIMS}
    assert sp.intensity(low) == 0.0
    assert sp.intensity({**low, "pace": 1.0}) > sp.intensity(low)
    assert sp.intensity({d: 1.0 for d in sp.DIMS}) == pytest.approx(1.0)
