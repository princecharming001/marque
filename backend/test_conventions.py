"""Wave-2 golden-identity tests: the convention mechanism at identity values
must be byte-identical to pre-conventions behavior, and every gate must be
deterministic on job_seed."""
import copy

from app import cta_styles
from app.conventions import (CAPTION_CONVENTIONS, CTA_PATTERN_WEIGHTS,
                             TITLE_CARD_POLICY, pick_weighted, seed_fraction)
from app.retention import (apply_retention_passes, place_cta_overlay,
                           place_hook_overlay)


def _words(text="The one mistake everyone makes. Here is the fix for it now."):
    out, t = [], 0
    for w in text.split():
        out.append({"word": w, "start_ms": t, "end_ms": t + 300})
        t += 350
    return out


def _edl(frames=900):
    return {"style": "talking_head", "segments": [{"src_in": 0, "src_out": frames}],
            "drops": [], "overlays": [], "captions": []}


def test_wave3_values_pin_the_study():
    # Wave 3: values pin the 2026-07-29 study medians (verify-gate CLEAN).
    assert CAPTION_CONVENTIONS["default_style"] == "clean"
    assert CAPTION_CONVENTIONS["default_grouping"] == "phrase"
    assert CAPTION_CONVENTIONS["pos_y_default"] == 0.62      # winners 0.627
    assert CAPTION_CONVENTIONS["stroke_px_default"] == {"clean": 3.0}  # 95% stroked
    assert CAPTION_CONVENTIONS["auto_style_allowed"] == ("clean", "karaoke")
    assert TITLE_CARD_POLICY["rate"]["default"] == 0.2       # winners 17% overall
    assert TITLE_CARD_POLICY["rate"]["story"] == 0.0         # winners 0% (n=6)
    assert TITLE_CARD_POLICY["hold_max_frames"] == 72        # winners 2.2s median
    w = CTA_PATTERN_WEIGHTS["default"]
    assert w["spoken_only"] > w["text_overlay"] > w["hard_end_card"]
    assert abs(sum(w.values()) - 1.0) < 1e-9


def test_seed_fraction_deterministic_and_spread():
    a = seed_fraction("job-1", "title_card")
    assert a == seed_fraction("job-1", "title_card")
    assert 0.0 <= a < 1.0
    assert seed_fraction("job-2", "title_card") != a


def test_pick_weighted_identity_and_split():
    assert pick_weighted({"hard_end_card": 1.0, "text_overlay": 0.0,
                          "spoken_only": 0.0}, 0.99) == "hard_end_card"
    w = {"hard_end_card": 0.5, "text_overlay": 0.5}
    assert pick_weighted(w, 0.25) == "hard_end_card"
    assert pick_weighted(w, 0.75) == "text_overlay"


def test_title_card_rate_1_matches_no_policy_exactly():
    words = _words()
    hints_base = {"hook_text": "The one mistake"}
    legacy = place_hook_overlay(copy.deepcopy(_edl()), words, style="talking_head",
                                hints=dict(hints_base), job_seed="seed-x")
    with_policy = place_hook_overlay(
        copy.deepcopy(_edl()), words, style="talking_head",
        hints={**hints_base, "title_card_policy": {"rate": {"default": 1.0}, "suppress": []}},
        job_seed="seed-x")
    assert legacy == with_policy, "identity policy must be byte-identical"
    assert any(o["type"] == "text_sticker" for o in with_policy["overlays"])


def test_title_card_rate_0_suppresses_and_is_seed_stable():
    words = _words()
    hints = {"hook_text": "The one mistake",
             "title_card_policy": {"rate": {"default": 0.0}, "suppress": []}}
    for _ in range(3):
        out = place_hook_overlay(copy.deepcopy(_edl()), words, style="talking_head",
                                 hints=dict(hints), job_seed="seed-x")
        assert not out.get("overlays"), "rate 0 must never place the title"


def test_title_card_content_type_rate_and_suppress_predicates():
    words = _words()
    pol = {"rate": {"default": 1.0, "storytime": 0.0}, "suppress": []}
    out = place_hook_overlay(copy.deepcopy(_edl()), words, style="talking_head",
                             hints={"hook_text": "x y z", "title_card_policy": pol,
                                    "content_type": "storytime"}, job_seed="s")
    assert not out.get("overlays")
    # captions_top suppress
    e = _edl()
    e["caption_options"] = {"position": "top"}
    out2 = place_hook_overlay(e, words, style="talking_head",
                              hints={"hook_text": "x y z",
                                     "title_card_policy": {"rate": {"default": 1.0},
                                                           "suppress": ["captions_top"]}},
                              job_seed="s")
    assert not out2.get("overlays")
    # under_8s suppress (900f = 30s does NOT suppress; 180f = 6s does)
    out3 = place_hook_overlay(_edl(180), words, style="talking_head",
                              hints={"hook_text": "x y z",
                                     "title_card_policy": {"rate": {"default": 1.0},
                                                           "suppress": ["under_8s"]}},
                              job_seed="s")
    assert not out3.get("overlays")


def test_three_way_close_exactly_one_treatment(monkeypatch):
    import app.retention as retention_mod
    monkeypatch.setattr(retention_mod, "_ENV_PASSES", "structure")
    words = _words()

    def run(pattern):
        hints = {"end_card": {"wanted": True, "text": "FOLLOW FOR PART 2",
                              "pattern": pattern}}
        return apply_retention_passes(
            _edl(), words, style="talking_head", prefs={}, emphasis_spans=[],
            dossier=None, hints=hints, script=None, job_seed="s")

    hard = run("hard_end_card")
    overlay = run("text_overlay")
    spoken = run("spoken_only")

    # v8: BOTH visual patterns now ride the one end_card carrier; the template's
    # layout class is what distinguishes them (tail card vs overlay-over-speaker),
    # and build_render_plan does the mount math. The XOR contract is unchanged:
    # exactly one close treatment per edit.
    def close_treatments(edl):
        ec = edl.get("end_card") or {}
        style = ec.get("style_id") or ""
        card = bool(ec) and not cta_styles.is_overlay(style)
        overlay_cta = bool(ec) and cta_styles.is_overlay(style)
        return card, overlay_cta

    assert close_treatments(hard) == (True, False)
    assert close_treatments(overlay) == (False, True)
    assert close_treatments(spoken) == (False, False)


def test_cta_overlay_respects_skip_matrix_and_read_time():
    words = _words()
    hints = {"end_card": {"wanted": True, "text": "COMMENT 'GUIDE' AND FOLLOW"}}
    out = place_cta_overlay(_edl(), words, style="talking_head", hints=dict(hints))
    ec = out.get("end_card") or {}
    assert ec, "overlay CTA stamps the end_card carrier (v8)"
    assert cta_styles.is_overlay(ec["style_id"]), "overlay pattern picks an overlay template"
    assert 75 <= ec["frames"] <= 150, "read-time hold unchanged"
    skipped = place_cta_overlay(_edl(), words, style="fast_cuts", hints=dict(hints))
    assert not skipped.get("end_card"), "skip matrix applies to the overlay pattern too"
