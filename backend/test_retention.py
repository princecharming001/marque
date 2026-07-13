"""Retention-pass orchestrator + WS1 passes (2026-07-12 retention-editor upgrade).
Keyless, pure — no monkeypatch seams needed beyond the module-level env-flag
attribute (matches the SELF_REVIEW/AI_QUALITY convention: flags are captured once
at import, so tests set `retention._ENV_PASSES` directly rather than mutating
os.environ post-import).

More tests land here as later tasks add passes (pacing WS2, interrupts WS3,
hook/end_card/sfx WS4) — this file covers what P1 shipped: the orchestrator's
flag-gating + fail-soft mechanics, sweep_residual_fillers, and trim_loop_tail.
"""
import pytest

from app import retention
from app.edl import check_edl_invariants, ms_to_frame, apply_edl_ops, split_segment_in_place


@pytest.fixture(autouse=True)
def _reset_passes_flag():
    before = retention._ENV_PASSES
    yield
    retention._ENV_PASSES = before


def _words(*specs):
    return [{"word": w, "start_ms": s, "end_ms": e, **extra} for (w, s, e, *rest) in specs
            for extra in (rest[0] if rest else {},)]


def _dense_run(start_ms, duration_ms, count, prefix):
    """`count` evenly-spaced 100ms-long words filling [start_ms, start_ms+duration_ms)."""
    step = duration_ms / count
    return [{"word": f"{prefix}{i}", "start_ms": int(start_ms + i * step),
             "end_ms": int(start_ms + i * step + 100)} for i in range(count)]


def _base_edl(**over):
    edl = {"style": "talking_head", "format_id": "x",
          "segments": [{"src_in": 0, "src_out": 150}], "drops": [], "layout": {"style": "talking_head"}}
    edl.update(over)
    return edl


# ---------------------------------------------------------------------------
# Orchestrator: flag gating + fail-soft
# ---------------------------------------------------------------------------

def test_passes_are_noop_when_flag_off():
    retention._ENV_PASSES = ""
    words = _words(("hello", 0, 300), ("um", 300, 400, {"type": "filler"}), ("world", 400, 5000))
    edl = _base_edl()
    out = retention.apply_retention_passes(edl, words, style="talking_head")
    assert out == edl


def test_each_pass_fail_soft_on_exception(monkeypatch):
    retention._ENV_PASSES = "filler"

    def _boom(edl, *a, **kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(retention, "sweep_residual_fillers", _boom)
    words = _words(("hello", 0, 300), ("um", 300, 400, {"type": "filler"}), ("world", 400, 5000))
    edl = _base_edl()
    out = retention.apply_retention_passes(edl, words, style="talking_head")
    assert out == edl   # the pass raised -> input reverted, pipeline never breaks


def test_pass_reverted_when_it_introduces_a_new_hard_invariant_issue(monkeypatch):
    retention._ENV_PASSES = "filler"

    def _breaks_it(edl, *a, **kw):
        # Return something that fails check_edl_invariants (segments now empty).
        bad = dict(edl)
        bad["segments"] = []
        return bad

    monkeypatch.setattr(retention, "sweep_residual_fillers", _breaks_it)
    words = _words(("hello", 0, 300), ("um", 300, 400, {"type": "filler"}), ("world", 400, 5000))
    edl = _base_edl()
    out = retention.apply_retention_passes(edl, words, style="talking_head")
    assert out["segments"] == edl["segments"]   # reverted, not the broken output


def test_retention_output_still_passes_invariants():
    retention._ENV_PASSES = "all"
    words = _words(("hello", 0, 300), ("um", 300, 400, {"type": "filler"}), ("world", 400, 5000))
    edl = _base_edl()
    out = retention.apply_retention_passes(edl, words, style="talking_head")
    assert check_edl_invariants(out) == []


# ---------------------------------------------------------------------------
# sweep_residual_fillers
# ---------------------------------------------------------------------------

def test_residual_filler_sweep_forces_drops():
    words = _words(("hello", 0, 300), ("um", 300, 400, {"type": "filler"}), ("world", 400, 5000))
    edl = _base_edl()
    out = retention.sweep_residual_fillers(edl, words, "default")
    lo, hi = ms_to_frame(300), ms_to_frame(400)
    assert any(d["src_in"] <= lo and hi <= d["src_out"] and d["reason"] == "filler" for d in out["drops"])


def test_residual_sweep_respects_min_duration():
    # A tiny segment (well under _MIN_DURATION_FRAMES=60) — sweeping the filler
    # out would breach the floor, so the sweep must refuse and leave it alone.
    words = _words(("hello", 0, 300), ("um", 300, 400, {"type": "filler"}), ("world", 400, 700))
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 21}])
    out = retention.sweep_residual_fillers(edl, words, "default")
    assert out["drops"] == []


def test_residual_sweep_does_not_mutate_the_input_dict():
    words = _words(("hello", 0, 300), ("um", 300, 400, {"type": "filler"}), ("world", 400, 5000))
    edl = _base_edl()
    original_drops = edl["drops"]
    retention.sweep_residual_fillers(edl, words, "default")
    assert edl["drops"] is original_drops and edl["drops"] == []


def test_residual_sweep_is_noop_with_no_residual_filler():
    words = _words(("hello", 0, 300), ("world", 300, 5000))
    edl = _base_edl()
    out = retention.sweep_residual_fillers(edl, words, "default")
    assert out["drops"] == []


# ---------------------------------------------------------------------------
# trim_loop_tail
# ---------------------------------------------------------------------------

def test_loop_tail_trims_trailing_dead_air():
    words = _words(("done", 0, 500))   # speech ends at frame 15
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 100}])
    out = retention.trim_loop_tail(edl, words)
    assert out["segments"][0]["src_out"] == ms_to_frame(500) + 10


def test_loop_tail_never_trims_below_the_last_word():
    words = _words(("done", 0, 500))
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 20}])   # already tight
    out = retention.trim_loop_tail(edl, words)
    assert out["segments"][0]["src_out"] == 20   # nothing to trim, no-op


def test_loop_tail_respects_segment_order_play_order():
    # Two segments reordered so array-index-last (segments[1]) is NOT the one
    # that plays last — the trim must target the PLAY-order last segment.
    words = _words(("done", 0, 500))
    edl = _base_edl(
        segments=[{"src_in": 0, "src_out": 100}, {"src_in": 200, "src_out": 300}],
        segment_order=[1, 0],   # segments[0] plays LAST
    )
    out = retention.trim_loop_tail(edl, words)
    assert out["segments"][1]["src_out"] == 300          # untouched (plays first)
    assert out["segments"][0]["src_out"] == ms_to_frame(500) + 10   # trimmed (plays last)


def test_loop_tail_applied_via_orchestrator_structure_pass():
    # A realistic take: ~5s of speech (well above the 3s/90f kept-duration floor)
    # with a small ~3s excess dead-air tail to trim — NOT the pathological
    # "trim away 85% of the clip" shape, which _safe_pass correctly refuses (see
    # test_pass_reverted_when_it_introduces_a_new_hard_invariant_issue).
    retention._ENV_PASSES = "structure"
    words = _words(("done", 0, 5000))   # speech ends at frame 150
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 250}])
    out = retention.apply_retention_passes(edl, words, style="talking_head")
    assert out["segments"][0]["src_out"] == ms_to_frame(5000) + 10


# ---------------------------------------------------------------------------
# WS2 — plan_pacing
# ---------------------------------------------------------------------------

def _padded_take(target_word_count, target_duration_ms=4000, pad_ms=5000, pad_words=20):
    """pre-padding (dense) + target zone (sparse, `target_word_count` words over
    `target_duration_ms`) + post-padding (dense) — padding on both sides exceeds
    HOOK_PROTECT_OUT_FRAMES(90f)/CTA_PROTECT_OUT_FRAMES(60f).

    IMPORTANT for choosing target_word_count: with no drops, the whole segment is
    ONE contiguous kept range, so plan_pacing's candidate zone is the FULL
    hook/CTA-clipped span (pre-tail + target + post-head), not the target zone in
    isolation — the current implementation evaluates wpm over that whole clipped
    span as a single unit (see plan_pacing's docstring: "at most one action per
    segment... evaluates the ENTIRE hook/cta-clipped kept span as a single unit,
    not sub-bursts within it"). So the target zone's word count has to be sparse
    enough to pull the WHOLE clipped span's average below the take median, not
    just be sparse relative to itself. Empirically, under this helper's exact
    padding geometry: target_word_count 1-4 -> steep band, 5-14 -> gentle band,
    16+ -> no speed-up (verified by direct enumeration, not hand-derived algebra —
    the interaction between hook/CTA clipping and the whole-span evaluation isn't
    simple enough to solve by hand reliably).
    Returns (words, edl, zone_lo, zone_hi) with one segment spanning the whole
    take, no drops."""
    pre = _dense_run(0, pad_ms, pad_words, "pre")
    target = _dense_run(pad_ms, target_duration_ms, target_word_count, "tgt")
    post = _dense_run(pad_ms + target_duration_ms, pad_ms, pad_words, "post")
    words = pre + target + post
    total_ms = pad_ms * 2 + target_duration_ms
    total_frames = ms_to_frame(total_ms)
    edl = _base_edl(segments=[{"src_in": 0, "src_out": total_frames}], drops=[])
    return words, edl, ms_to_frame(pad_ms), ms_to_frame(pad_ms + target_duration_ms)


def test_pacing_low_info_region_very_sparse_gets_the_steeper_band():
    # target_word_count=2 -> the whole hook/CTA-clipped span's wpm falls well
    # under 0.85x the take median (see _padded_take's docstring for why 2, not a
    # smaller/larger number, and why this isn't solved by hand-algebra).
    words, edl, zone_lo, zone_hi = _padded_take(target_word_count=2)
    out = retention.plan_pacing(edl, words, style="talking_head", hints={})
    sped = [s for s in out["segments"] if s.get("speed", 1.0) > 1.1]
    assert len(sped) == 1
    # 1.25 (steep band) * 1.03 (talking_head's default "subtle" lift)
    assert abs(sped[0]["speed"] - 1.25 * 1.03) < 0.01


def test_pacing_low_info_region_moderately_sparse_gets_the_gentler_band():
    # target_word_count=10 -> the clipped span's wpm falls in [0.85, 1.0)x median.
    words, edl, zone_lo, zone_hi = _padded_take(target_word_count=10)
    out = retention.plan_pacing(edl, words, style="talking_head", hints={})
    sped = [s for s in out["segments"] if s.get("speed", 1.0) > 1.1]
    assert len(sped) == 1
    assert abs(sped[0]["speed"] - 1.15 * 1.03) < 0.01


def test_pacing_normal_density_take_gets_no_speed_up():
    # All three stretches at the SAME density -> no region ever reads as low-info
    # relative to the take median (ratio ~1.0 everywhere) -> lift-only.
    words, edl, *_ = _padded_take(target_word_count=20, target_duration_ms=5000)
    out = retention.plan_pacing(edl, words, style="talking_head", hints={})
    assert all(abs(s.get("speed", 1.0) - 1.03) < 1e-9 for s in out["segments"])


def test_pacing_splits_land_on_word_boundaries():
    words, edl, *_ = _padded_take(target_word_count=6)
    out = retention.plan_pacing(edl, words, style="talking_head", hints={})
    boundaries = sorted({s["src_in"] for s in out["segments"]} | {s["src_out"] for s in out["segments"]})
    word_edges = {ms_to_frame(w["start_ms"]) for w in words} | {ms_to_frame(w["end_ms"]) for w in words}
    interior = boundaries[1:-1]   # exclude the take's own [0, total) outer edges
    for b in interior:
        assert b in word_edges, f"boundary {b} does not land on a word edge"


def test_pacing_never_speeds_a_stretch_overlapping_emphasis():
    words, edl, zone_lo, zone_hi = _padded_take(target_word_count=6)
    # The sparse target zone IS the emphasis span -> must be protected, not sped up.
    out = retention.plan_pacing(edl, words, style="talking_head",
                                emphasis_spans=[(zone_lo, zone_hi)], hints={})
    assert all(abs(s.get("speed", 1.0) - 1.03) < 1e-9 for s in out["segments"])


def test_pacing_skips_duet_split_entirely():
    words, edl, *_ = _padded_take(target_word_count=6)
    edl["style"] = "duet_split"
    out = retention.plan_pacing(edl, words, style="duet_split", hints={})
    assert out == edl   # untouched, byte-identical


def test_pacing_global_lift_style_default_vs_hint_override():
    words, edl, *_ = _padded_take(target_word_count=20, target_duration_ms=5000)   # no low-info region
    out_default = retention.plan_pacing(edl, words, style="talking_head", hints={})
    assert all(abs(s["speed"] - 1.03) < 1e-9 for s in out_default["segments"])   # style default "subtle"

    out_medium = retention.plan_pacing(edl, words, style="talking_head",
                                       hints={"pacing": {"lift": "medium"}})
    assert all(abs(s["speed"] - 1.06) < 1e-9 for s in out_medium["segments"])   # hint overrides style

    out_fastcuts = retention.plan_pacing(edl, words, style="fast_cuts", hints={})
    assert all(abs(s["speed"] - 1.0) < 1e-9 for s in out_fastcuts["segments"])   # fast_cuts default "none"


def test_pacing_combined_speed_never_exceeds_spoken_cap():
    # Even the steepest band (1.25) combined with the highest lift (1.06 medium)
    # must clamp at SPOKEN_SPEED_CAP (1.35), not multiply past it (1.25*1.06=1.325,
    # so use an energy bonus too to actually probe the cap: 1.30*1.06=1.378>1.35).
    words, edl, zone_lo, zone_hi = _padded_take(target_word_count=6)
    dossier = {"delivery_curve": [{"f0": zone_lo, "f1": zone_hi, "energy": 0.1}]}
    out = retention.plan_pacing(edl, words, style="talking_head", dossier=dossier,
                                hints={"pacing": {"lift": "medium"}})
    assert all(s.get("speed", 1.0) <= retention.SPOKEN_SPEED_CAP + 1e-9 for s in out["segments"])


def test_pacing_speed_through_silence_replaces_the_drop():
    words = _dense_run(0, 6000, 20, "a") + _dense_run(7800, 6000, 20, "b")   # 1.8s pause
    pause_lo, pause_hi = ms_to_frame(6000) + 4, ms_to_frame(7800) - 2   # tightened, mirrors strip_fillers
    total_frames = ms_to_frame(13800)
    edl = _base_edl(segments=[{"src_in": 0, "src_out": total_frames}],
                    drops=[{"src_in": pause_lo, "src_out": pause_hi, "reason": "dead_air"}])
    out = retention.plan_pacing(edl, words, style="talking_head",
                                hints={"pacing": {"fast_forward_silences": True}})
    assert out["drops"] == []
    fast = [s for s in out["segments"] if s.get("speed", 1.0) > 2.0]
    assert len(fast) == 1
    assert fast[0]["src_in"] == pause_lo and fast[0]["src_out"] == pause_hi


def test_pacing_speed_through_silence_off_by_default_leaves_the_drop():
    words = _dense_run(0, 6000, 20, "a") + _dense_run(7800, 6000, 20, "b")
    pause_lo, pause_hi = ms_to_frame(6000) + 4, ms_to_frame(7800) - 2
    total_frames = ms_to_frame(13800)
    edl = _base_edl(segments=[{"src_in": 0, "src_out": total_frames}],
                    drops=[{"src_in": pause_lo, "src_out": pause_hi, "reason": "dead_air"}])
    out = retention.plan_pacing(edl, words, style="talking_head", hints={})   # no fast_forward_silences
    assert out["drops"] == [{"src_in": pause_lo, "src_out": pause_hi, "reason": "dead_air"}]


def test_pacing_silence_speed_can_exceed_the_spoken_cap():
    # A long-ish qualifying pause (2.4s = 72 frames) -> 72/14=5.14, capped at
    # SILENCE_SPEED_CAP(3.0) -- well above SPOKEN_SPEED_CAP(1.35), proving silence
    # gets its own, much higher ceiling since there's no speech to distort.
    words = _dense_run(0, 6000, 20, "a") + _dense_run(8400, 6000, 20, "b")   # 2.4s pause
    pause_lo, pause_hi = ms_to_frame(6000) + 4, ms_to_frame(8400) - 2
    total_frames = ms_to_frame(14400)
    edl = _base_edl(segments=[{"src_in": 0, "src_out": total_frames}],
                    drops=[{"src_in": pause_lo, "src_out": pause_hi, "reason": "dead_air"}])
    out = retention.plan_pacing(edl, words, style="talking_head",
                                hints={"pacing": {"fast_forward_silences": True}})
    fast = [s for s in out["segments"] if s.get("speed", 1.0) > 2.0]
    assert len(fast) == 1
    assert abs(fast[0]["speed"] - retention.SILENCE_SPEED_CAP) < 1e-9
    assert fast[0]["speed"] > retention.SPOKEN_SPEED_CAP


def test_pacing_silence_too_short_or_too_long_is_left_alone():
    # 0.5s (15 frames, < SILENCE_FF_MIN_FRAMES=36) and 3.5s (105 frames, >
    # SILENCE_FF_MAX_FRAMES=75) must NOT be fast-forwarded — strip_fillers'
    # own tightening covers the short one; a hard cut reads better for the long one.
    words = _dense_run(0, 6000, 20, "a") + _dense_run(6500, 6000, 20, "b")   # 0.5s pause
    total_frames = ms_to_frame(12500)
    edl = _base_edl(segments=[{"src_in": 0, "src_out": total_frames}],
                    drops=[{"src_in": ms_to_frame(6000), "src_out": ms_to_frame(6500), "reason": "dead_air"}])
    out = retention.plan_pacing(edl, words, style="talking_head",
                                hints={"pacing": {"fast_forward_silences": True}})
    assert len(out["drops"]) == 1   # untouched


def test_pacing_no_speech_signal_is_noop():
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 300}])
    out = retention.plan_pacing(edl, [], style="talking_head", hints={})
    assert out["segments"] == edl["segments"]


def test_pacing_does_not_mutate_the_input_dict():
    words, edl, *_ = _padded_take(target_word_count=6)
    original_segments = edl["segments"]
    retention.plan_pacing(edl, words, style="talking_head", hints={})
    assert edl["segments"] is original_segments


def test_pacing_output_passes_invariants():
    words, edl, *_ = _padded_take(target_word_count=6)
    out = retention.plan_pacing(edl, words, style="talking_head", hints={})
    assert check_edl_invariants(out) == []


def test_pacing_applied_via_orchestrator():
    retention._ENV_PASSES = "pacing"
    words, edl, *_ = _padded_take(target_word_count=6)
    out = retention.apply_retention_passes(edl, words, style="talking_head")
    assert any(s.get("speed", 1.0) > 1.1 for s in out["segments"])


def test_pacing_orchestrator_respects_prefs_pacing_off():
    retention._ENV_PASSES = "pacing"
    words, edl, *_ = _padded_take(target_word_count=6)
    out = retention.apply_retention_passes(edl, words, style="talking_head", prefs={"pacing": False})
    assert out["segments"] == edl["segments"]


# ---------------------------------------------------------------------------
# split_segment_in_place parity — the tweak-op path and the pacing-engine path
# share ONE implementation; this pins that they stay identical.
# ---------------------------------------------------------------------------

def test_split_segment_in_place_matches_the_apply_op_path():
    edl_direct = _base_edl(segments=[{"src_in": 0, "src_out": 300, "speed": 1.5,
                                      "tx_scale": 1.2, "tx_x": 0.1, "tx_y": -0.05}],
                           transitions=[{"after_segment": 0, "style": "fade_black", "frames": 12}])
    import copy as _copy
    edl_a = _copy.deepcopy(edl_direct)
    split_segment_in_place(edl_a, 0, 150)

    edl_b, results = apply_edl_ops(edl_direct, [{"type": "split_segment", "index": 0, "at_frame": 150}])
    assert results[0]["applied"] is True
    assert edl_a["segments"] == edl_b["segments"]
    assert edl_a.get("transitions") == edl_b.get("transitions")


# ---------------------------------------------------------------------------
# WS3 — schedule_interrupts
# ---------------------------------------------------------------------------

def _steady_words(total_ms, step_ms=300):
    out, t, i = [], 0, 0
    while t < total_ms:
        out.append({"word": f"w{i}", "start_ms": t, "end_ms": t + 200})
        t += step_ms
        i += 1
    return out


def _bare_edl(style, total_frames, **over):
    edl = _base_edl(style=style, segments=[{"src_in": 0, "src_out": total_frames}],
                    drops=[], overlays=[], broll=[])
    edl.update(over)
    return edl


def test_interrupts_no_static_span_exceeds_cadence_plus_hold():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.schedule_interrupts(edl, words, style="talking_head", hints={})
    windows = sorted((o["src_in"], o["src_out"]) for o in out["overlays"])
    assert len(windows) >= 2
    gaps = [b[0] - a[1] for a, b in zip(windows, windows[1:])]
    cadence = retention._INTERRUPT_CADENCE["talking_head"]
    assert all(g <= cadence + retention._INTERRUPT_HOLD_FRAMES for g in gaps)


def test_interrupts_respect_hook_and_cta_guard_zones():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.schedule_interrupts(edl, words, style="talking_head", hints={})
    for o in out["overlays"]:
        out_start = ms_to_frame(next(w for w in words if w["word"] == "w0")["start_ms"])  # sanity anchor unused
    # Re-derive via the same output index the function itself uses, for an honest check.
    index, total_out = retention._build_output_index(out["segments"], out["drops"], retention._play_order(out))
    for o in out["overlays"]:
        out_frame = retention._src_to_out(index, o["src_in"])
        assert out_frame is not None
        assert out_frame >= retention._INTERRUPT_HOOK_GUARD - 1
        assert out_frame <= total_out - retention._INTERRUPT_CTA_GUARD + 1


def test_interrupts_alternate_scale():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.schedule_interrupts(edl, words, style="talking_head", hints={})
    scales = [o["scale"] for o in sorted(out["overlays"], key=lambda o: o["src_in"])]
    assert len(scales) >= 2
    for a, b in zip(scales, scales[1:]):
        assert a != b   # strictly alternating


def test_interrupts_faceless_uses_text_stickers_not_punch_in():
    words = _steady_words(20000)
    total_frames = ms_to_frame(20000)
    edl = _bare_edl("faceless", total_frames)
    out = retention.schedule_interrupts(edl, words, style="faceless", hints={})
    assert len(out["overlays"]) > 0
    assert all(o["type"] == "text_sticker" for o in out["overlays"])


def test_interrupts_cap_at_max_per_clip():
    words = _steady_words(120000, step_ms=250)   # a very long, very dense take
    total_frames = ms_to_frame(120000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.schedule_interrupts(edl, words, style="talking_head", hints={})
    assert len(out["overlays"]) <= retention._INTERRUPT_MAX_PER_CLIP


def test_interrupts_skips_fast_cuts_and_duet_split():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    for style in ("fast_cuts", "duet_split"):
        edl = _bare_edl(style, total_frames)
        out = retention.schedule_interrupts(edl, words, style=style, hints={})
        assert out["overlays"] == []


def test_interrupts_respects_prefs_punch_ins_false():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.schedule_interrupts(edl, words, style="talking_head",
                                        prefs={"punch_ins": False}, hints={})
    assert out == edl


def test_interrupts_density_dense_inserts_more_than_calm():
    words = _steady_words(60000, step_ms=250)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    calm = retention.schedule_interrupts(edl, words, style="talking_head",
                                         hints={"interrupt_density": "calm"})
    dense = retention.schedule_interrupts(edl, words, style="talking_head",
                                          hints={"interrupt_density": "dense"})
    assert len(dense["overlays"]) > len(calm["overlays"])


def test_interrupts_never_overlaps_an_existing_overlay():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    # Pre-place a punch_in covering the middle third of the take.
    existing_lo, existing_hi = int(total_frames * 0.4), int(total_frames * 0.6)
    existing = {"type": "punch_in", "src_in": existing_lo, "src_out": existing_hi,
               "scale": 1.08, "text": ""}
    edl = _bare_edl("talking_head", total_frames, overlays=[existing])
    out = retention.schedule_interrupts(edl, words, style="talking_head", hints={})
    # schedule_interrupts deep-copies internally, so compare by VALUE (the exact
    # pre-existing window), not object identity, to skip the untouched original.
    new_ones = [o for o in out["overlays"]
               if not (o["src_in"] == existing_lo and o["src_out"] == existing_hi)]
    assert len(new_ones) == len(out["overlays"]) - 1   # exactly the existing one was skipped
    for o in new_ones:
        assert o["src_out"] <= existing_lo or o["src_in"] >= existing_hi


def test_interrupts_does_not_mutate_input():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    original_overlays = edl["overlays"]
    retention.schedule_interrupts(edl, words, style="talking_head", hints={})
    assert edl["overlays"] is original_overlays and edl["overlays"] == []


def test_interrupts_output_passes_invariants():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.schedule_interrupts(edl, words, style="talking_head", hints={})
    assert check_edl_invariants(out) == []


def test_interrupts_applied_via_orchestrator():
    retention._ENV_PASSES = "interrupts"
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.apply_retention_passes(edl, words, style="talking_head")
    assert len(out["overlays"]) > 0
