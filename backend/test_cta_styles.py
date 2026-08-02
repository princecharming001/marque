"""CTA template library (v8): catalog parity, plan math, and selection precedence."""
import json
import os

import pytest

from app import cta_styles, conventions
from app.edl import build_render_plan
from app.retention import place_cta_overlay, place_end_card

_RENDER_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "render", "src", "components", "cta", "cta_styles.json")


def _edl(total=900):
    return {"segments": [{"src_in": 0, "src_out": total}], "drops": [],
            "captions": [], "overlays": [], "broll": []}


def _words(n=60):
    return [{"word": f"w{i}", "start_ms": i * 500, "end_ms": i * 500 + 400} for i in range(n)]


# --- catalog parity ---------------------------------------------------------

def test_backend_catalog_matches_the_render_json():
    """The render bundle imports cta_styles.json directly. If the backend's view of
    the catalog ever drifts, we ship ids the renderer can't draw (or hide ids it can)."""
    with open(_RENDER_JSON) as f:
        raw = json.load(f)["styles"]
    assert {s["id"] for s in raw} == cta_styles.style_ids()
    for s in raw:
        assert cta_styles.layout_class(s["id"]) == s["layout_class"]


def test_catalog_shape():
    ids = cta_styles.style_ids()
    assert len(ids) == 20
    assert sum(1 for i in ids if cta_styles.is_overlay(i)) == 14
    assert cta_styles.mount_for("classic") == "tail"
    assert cta_styles.mount_for("pill") == "overlay"


def test_unknown_ids_clamp_to_classic():
    assert cta_styles.clamp_style_id("nope") == "classic"
    assert cta_styles.clamp_style_id(None) == "classic"
    assert cta_styles.clamp_style_id("") == "classic"
    assert cta_styles.clamp_style_id("bar_sweep") == "bar_sweep"


def test_style_weight_rows_are_valid():
    for pattern, row in conventions.CTA_STYLE_WEIGHTS.items():
        assert pattern in conventions.CTA_PATTERNS
        assert abs(sum(row.values()) - 1.0) < 1e-9, f"{pattern} weights must sum to 1"
        for sid in row:
            assert cta_styles.is_known(sid), f"{pattern}: unknown style {sid}"
        # a pattern's templates must all belong to that pattern's layout class
        for sid in row:
            assert cta_styles.pattern_for(sid) == pattern


# --- plan math: tail extends the timeline, overlay does not -----------------

def test_tail_card_extends_total_frames_overlay_does_not():
    base = build_render_plan(_edl())["total_frames"]

    tail = build_render_plan({**_edl(), "end_card": {"text": "Follow for more",
                                                     "frames": 90, "style_id": "classic"}})
    assert tail["total_frames"] == base + 90
    assert tail["end_card"]["start_frame"] == base
    assert tail["end_card"]["mount"] == "tail"

    ov = build_render_plan({**_edl(), "end_card": {"text": "Follow for more",
                                                   "frames": 90, "style_id": "bar_sweep"}})
    assert ov["total_frames"] == base, "an overlay CTA must NOT extend the video"
    assert ov["end_card"]["mount"] == "overlay"
    assert ov["end_card"]["start_frame"] + ov["end_card"]["frames"] == base
    assert ov["end_card"]["start_frame"] >= base * 0.6, "overlay may not eat the whole video"


def test_overlay_never_starts_before_60_percent_even_when_hold_is_long():
    # A 150-frame hold on a 200-frame video would otherwise cover 75% of it.
    plan = build_render_plan({**_edl(total=200),
                              "end_card": {"text": "x", "frames": 150, "style_id": "pill"}})
    base = build_render_plan(_edl(total=200))["total_frames"]
    assert plan["end_card"]["start_frame"] >= int(base * 0.6)


def test_unknown_style_in_a_plan_renders_as_the_classic_tail_card():
    plan = build_render_plan({**_edl(), "end_card": {"text": "hi", "frames": 60,
                                                     "style_id": "from_the_future"}})
    assert plan["end_card"]["style_id"] == "classic"
    assert plan["end_card"]["mount"] == "tail"


def test_pre_v8_plan_round_trips_identically():
    """A stored EDL with no style_id must render exactly as it did before v8."""
    plan = build_render_plan({**_edl(), "end_card": {"text": "Follow", "frames": 75}})
    assert plan["end_card"]["style_id"] == "classic"
    assert plan["end_card"]["mount"] == "tail"
    assert plan["end_card"]["start_frame"] == build_render_plan(_edl())["total_frames"]


# --- retention: which pass stamps what --------------------------------------

def test_place_end_card_carries_the_creators_template():
    out = place_end_card(_edl(), _words(), style="talking_head",
                         hints={"end_card": {"wanted": True, "text": "Follow for more",
                                             "style_id": "paper_press"}})
    assert out["end_card"]["style_id"] == "paper_press"


def test_place_cta_overlay_stamps_an_overlay_template_not_a_sticker():
    out = place_cta_overlay(_edl(), _words(), style="talking_head",
                            hints={"end_card": {"wanted": True, "text": "Follow for more",
                                                "style_id": "bar_sweep"}})
    assert out["end_card"]["style_id"] == "bar_sweep"
    assert not [o for o in (out.get("overlays") or []) if o.get("type") == "text_sticker"], \
        "v8: the overlay CTA rides end_card, not a bespoke sticker"


def test_place_cta_overlay_coerces_a_tail_style_to_an_overlay_one():
    out = place_cta_overlay(_edl(), _words(), style="talking_head",
                            hints={"end_card": {"wanted": True, "text": "hi",
                                                "style_id": "classic"}})
    assert cta_styles.is_overlay(out["end_card"]["style_id"])


def test_skip_matrix_still_applies_but_a_creator_pick_beats_it():
    hint = {"end_card": {"wanted": True, "text": "Follow"}}
    assert not place_end_card(_edl(), _words(), style="fast_cuts",
                              hints=hint).get("end_card")
    creator = {"end_card": {**hint["end_card"], "creator": True, "style_id": "credits"}}
    out = place_end_card(_edl(), _words(), style="fast_cuts", hints=creator)
    assert out["end_card"]["style_id"] == "credits", \
        "an explicit creator ending must survive the skip matrix"


def test_no_visual_cta_leaves_the_edl_clean():
    """The 'none' pick is resolved upstream (the hint is dropped), so neither visual
    pass has anything to stamp — which is what 86% of measured winners do."""
    for fn in (place_end_card, place_cta_overlay):
        out = fn(_edl(), _words(), style="talking_head", hints={})
        assert not out.get("end_card")
