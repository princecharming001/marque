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
from app import themes as _themes
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


def _cadence_run(start_ms, n, word_ms, gap_ms, tag):
    """n words at a steady cadence; inter-word gap < 350ms keeps them ONE sentence."""
    words, t = [], start_ms
    for i in range(n):
        words.append({"word": f"{tag}{i}", "start_ms": t, "end_ms": t + word_ms})
        t += word_ms + gap_ms
    return words, t


def test_pacing_v7_slow_sentence_gets_normalized_toward_target():
    # v7: fast sentence (~200wpm) . SLOW sentence (~100wpm) . fast sentence — the
    # slow one gets its own speed toward the WPM target (JND-smoothed from its
    # neighbor, quantized to 0.05); the fast ones keep the bare lift.
    a, t = _cadence_run(0, 20, 200, 100, "a"); a[-1]["word"] += "."
    b, t = _cadence_run(t + 400, 12, 300, 300, "b"); b[-1]["word"] += "."
    c, t = _cadence_run(t + 400, 20, 200, 100, "c"); c[-1]["word"] += "."
    words = a + b + c
    edl = _base_edl(segments=[{"src_in": 0, "src_out": ms_to_frame(t)}], drops=[])
    out = retention.plan_pacing(edl, words, style="talking_head", hints={})
    sped = [s for s in out["segments"] if s.get("speed", 1.0) > 1.06]
    assert sped, f"the slow sentence must be sped up: {[(s['src_in'], s['src_out'], s.get('speed')) for s in out['segments']]}"
    for s in sped:
        assert s["speed"] <= retention.SPOKEN_SPEED_CAP + 1e-9
        assert abs(s["speed"] * 20 - round(s["speed"] * 20)) < 1e-6   # 0.05 quantization


def test_pacing_v7_adjacent_speeds_within_jnd():
    # Tempo JND: adjacent PLAYED segments never differ by more than 0.10 (the
    # across-pause cap; contiguous speech caps tighter at 0.05).
    words, edl, *_ = _padded_take(target_word_count=2)
    out = retention.plan_pacing(edl, words, style="talking_head", hints={})
    speeds = [s.get("speed", 1.0) for s in out["segments"] if s.get("speed", 1.0) <= 1.35]
    for a, b in zip(speeds, speeds[1:]):
        assert abs(a - b) <= retention.RATE_DELTA_ACROSS_PAUSE + 1e-6, (a, b)


def test_pacing_normal_density_take_gets_no_speed_up():
    # All three stretches at the SAME density -> no region ever reads as low-info
    # relative to the take median (ratio ~1.0 everywhere) -> lift-only.
    words, edl, *_ = _padded_take(target_word_count=20, target_duration_ms=5000)
    out = retention.plan_pacing(edl, words, style="talking_head", hints={})
    assert all(abs(s.get("speed", 1.0) - 1.05) < 1e-9 for s in out["segments"])


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
    assert all(abs(s.get("speed", 1.0) - 1.05) < 1e-9 for s in out["segments"])


def test_pacing_skips_duet_split_entirely():
    words, edl, *_ = _padded_take(target_word_count=6)
    edl["style"] = "duet_split"
    out = retention.plan_pacing(edl, words, style="duet_split", hints={})
    assert out == edl   # untouched, byte-identical


def test_pacing_global_lift_style_default_vs_hint_override():
    words, edl, *_ = _padded_take(target_word_count=20, target_duration_ms=5000)   # no low-info region
    out_default = retention.plan_pacing(edl, words, style="talking_head", hints={})
    assert all(abs(s["speed"] - 1.05) < 1e-9 for s in out_default["segments"])   # style default "subtle"

    out_medium = retention.plan_pacing(edl, words, style="talking_head",
                                       hints={"pacing": {"lift": "medium"}})
    assert all(abs(s["speed"] - 1.08) < 1e-9 for s in out_medium["segments"])   # hint overrides style

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


def test_pacing_speed_through_silence_on_by_default_and_hint_disables():
    # v7 (Overcast/TimeBolt principle): silence fast-forward defaults ON — compress
    # silence before ever touching speech rate. The hint can still disable it.
    words = _dense_run(0, 6000, 20, "a") + _dense_run(7800, 6000, 20, "b")
    pause_lo, pause_hi = ms_to_frame(6000) + 4, ms_to_frame(7800) - 2
    total_frames = ms_to_frame(13800)
    edl = _base_edl(segments=[{"src_in": 0, "src_out": total_frames}],
                    drops=[{"src_in": pause_lo, "src_out": pause_hi, "reason": "dead_air"}])
    out = retention.plan_pacing(edl, words, style="talking_head", hints={})   # defaults ON
    assert out["drops"] == [], "silence FF must run by default"
    out_off = retention.plan_pacing(edl, words, style="talking_head",
                                    hints={"pacing": {"fast_forward_silences": False}})
    assert out_off["drops"] == [{"src_in": pause_lo, "src_out": pause_hi, "reason": "dead_air"}]


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
    a, t = _cadence_run(0, 20, 200, 100, "a"); a[-1]["word"] += "."
    b, t = _cadence_run(t + 400, 12, 300, 300, "b"); b[-1]["word"] += "."
    c, t = _cadence_run(t + 400, 20, 200, 100, "c"); c[-1]["word"] += "."
    words = a + b + c
    edl = _base_edl(segments=[{"src_in": 0, "src_out": ms_to_frame(t)}], drops=[])
    out = retention.apply_retention_passes(edl, words, style="talking_head")
    assert any(s.get("speed", 1.0) > 1.06 for s in out["segments"])


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


# ---------------------------------------------------------------------------
# WS4b — align_emphasis
# ---------------------------------------------------------------------------

def test_align_emphasis_ranks_top_n_by_span_length():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000))
    spans = [(50, 60), (150, 170), (300, 350), (450, 530), (600, 750)]   # lens: 10,20,50,80,150
    out = retention.align_emphasis(edl, words, style="talking_head", emphasis_spans=spans)
    punch_starts = {o["src_in"] for o in out["overlays"] if o["type"] == "punch_in"}
    assert punch_starts == {600, 450, 300}   # top 3 by length
    assert 150 not in punch_starts and 50 not in punch_starts


def test_align_emphasis_highlight_words_uses_longest_word_in_span():
    words = _words(("a", 20000, 20100), ("supercalifragilistic", 20100, 20400), ("ok", 20400, 20500))
    edl = _bare_edl("talking_head", ms_to_frame(30000))
    span = (ms_to_frame(20000), ms_to_frame(20500))
    out = retention.align_emphasis(edl, words, style="talking_head", emphasis_spans=[span])
    assert "supercalifragilistic" in out["caption_options"]["highlight_words"]


def test_align_emphasis_skips_punch_for_non_punch_style():
    words = _steady_words(30000)
    edl = _bare_edl("fast_cuts", ms_to_frame(30000))   # not in _PUNCH_STYLES
    out = retention.align_emphasis(edl, words, style="fast_cuts", emphasis_spans=[(300, 350)])
    assert out["overlays"] == []


def test_align_emphasis_skips_punch_when_overlay_already_near():
    words = _steady_words(30000)
    existing = {"type": "punch_in", "src_in": 310, "src_out": 340, "scale": 1.08, "text": ""}
    edl = _bare_edl("talking_head", ms_to_frame(30000), overlays=[existing])
    out = retention.align_emphasis(edl, words, style="talking_head", emphasis_spans=[(300, 350)])
    assert out["overlays"] == [existing]


def test_align_emphasis_noop_with_no_spans():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000))
    out = retention.align_emphasis(edl, words, style="talking_head", emphasis_spans=[])
    assert out == edl


def test_align_emphasis_does_not_mutate_input():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000))
    original_overlays = edl["overlays"]
    retention.align_emphasis(edl, words, style="talking_head", emphasis_spans=[(300, 350)])
    assert edl["overlays"] is original_overlays and edl["overlays"] == []


def test_align_emphasis_output_passes_invariants():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000))
    out = retention.align_emphasis(edl, words, style="talking_head", emphasis_spans=[(300, 350)])
    assert check_edl_invariants(out) == []


def test_align_emphasis_highlight_words_capped():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000),
                    caption_options={"highlight_words": [f"w{i}" for i in range(12)]})
    out = retention.align_emphasis(edl, words, style="talking_head", emphasis_spans=[(300, 350)])
    assert len(out["caption_options"]["highlight_words"]) == 12   # already at cap


def test_align_emphasis_applied_via_orchestrator():
    retention._ENV_PASSES = "emphasis"
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000))
    out = retention.apply_retention_passes(edl, words, style="talking_head",
                                           emphasis_spans=[(300, 350)])
    assert any(o["type"] == "punch_in" for o in out["overlays"])


# ---------------------------------------------------------------------------
# WS4a — place_hook_overlay
# ---------------------------------------------------------------------------

def test_hook_overlay_uses_hint_text():
    words = _steady_words(10000)
    edl = _bare_edl("talking_head", ms_to_frame(10000))
    out = retention.place_hook_overlay(edl, words, style="talking_head",
                                       hints={"hook_text": "This changes everything"})
    hook = next(o for o in out["overlays"] if o["type"] == "text_sticker")
    assert hook["text"] == "This changes everything"
    assert hook["src_in"] == 0   # first kept frame (no drops)


def test_hook_overlay_falls_back_to_script_hook():
    words = _steady_words(10000)
    edl = _bare_edl("talking_head", ms_to_frame(10000))
    script = {"hook": "You will not believe what happened next today"}
    out = retention.place_hook_overlay(edl, words, style="talking_head", hints={}, script=script)
    hook = next(o for o in out["overlays"] if o["type"] == "text_sticker")
    assert hook["text"] == "You will not believe what happened next today"   # first 8 words (v2)


def test_hook_overlay_noop_with_no_text():
    words = _steady_words(10000)
    edl = _bare_edl("talking_head", ms_to_frame(10000))
    out = retention.place_hook_overlay(edl, words, style="talking_head", hints={}, script={})
    assert out["overlays"] == []


def test_hook_overlay_skips_duet_split():
    words = _steady_words(10000)
    edl = _bare_edl("duet_split", ms_to_frame(10000))
    out = retention.place_hook_overlay(edl, words, style="duet_split",
                                       hints={"hook_text": "Should never appear"})
    assert out["overlays"] == []


def test_hook_overlay_skips_when_overlay_already_occupies_open():
    words = _steady_words(10000)
    existing = {"type": "punch_in", "src_in": 10, "src_out": 40, "scale": 1.06, "text": ""}
    edl = _bare_edl("talking_head", ms_to_frame(10000), overlays=[existing])
    out = retention.place_hook_overlay(edl, words, style="talking_head",
                                       hints={"hook_text": "Should never appear"})
    assert out["overlays"] == [existing]


def test_hook_overlay_positions_below_when_captions_on_top():
    words = _steady_words(10000)
    edl = _bare_edl("talking_head", ms_to_frame(10000), caption_options={"position": "top"})
    out = retention.place_hook_overlay(edl, words, style="talking_head",
                                       hints={"hook_text": "Hook text here"})
    hook = next(o for o in out["overlays"] if o["type"] == "text_sticker")
    assert hook["pos_y"] == 0.62


def test_hook_overlay_does_not_mutate_input():
    words = _steady_words(10000)
    edl = _bare_edl("talking_head", ms_to_frame(10000))
    original_overlays = edl["overlays"]
    retention.place_hook_overlay(edl, words, style="talking_head", hints={"hook_text": "Hi"})
    assert edl["overlays"] is original_overlays and edl["overlays"] == []


def test_hook_overlay_output_passes_invariants():
    words = _steady_words(10000)
    edl = _bare_edl("talking_head", ms_to_frame(10000))
    out = retention.place_hook_overlay(edl, words, style="talking_head", hints={"hook_text": "Hi there"})
    assert check_edl_invariants(out) == []


def test_hook_overlay_applied_via_orchestrator_with_script_hook():
    retention._ENV_PASSES = "structure"
    words = _steady_words(10000)
    edl = _bare_edl("talking_head", ms_to_frame(10000))
    out = retention.apply_retention_passes(edl, words, style="talking_head",
                                           script={"hook": "This one trick changes everything"})
    assert any(o["type"] == "text_sticker" for o in out["overlays"])


def test_hook_overlay_skipped_if_emphasis_punch_already_in_the_open_via_orchestrator():
    # Ordering guarantee: align_emphasis runs BEFORE place_hook_overlay, so a punch
    # it placed right at the start of the take correctly blocks the hook overlay
    # from stacking a second competing "open" on top of it.
    retention._ENV_PASSES = "emphasis,structure"
    words = _steady_words(10000)
    edl = _bare_edl("talking_head", ms_to_frame(10000))
    out = retention.apply_retention_passes(
        edl, words, style="talking_head", emphasis_spans=[(5, 40)],
        hints={"hook_text": "Should be skipped"})
    assert not any(o.get("text") == "Should be skipped" for o in out["overlays"])


# ---------------------------------------------------------------------------
# WS4c — place_end_card (+ orchestrator XOR with trim_loop_tail)
# ---------------------------------------------------------------------------

def test_end_card_stamped_when_hint_wanted_and_has_text():
    words = _steady_words(10000)
    edl = _bare_edl("talking_head", ms_to_frame(10000))
    out = retention.place_end_card(edl, words, style="talking_head",
                                   hints={"end_card": {"wanted": True, "text": "Follow for more"}})
    assert out["end_card"] == {"text": "Follow for more", "frames": 75, "show_handle": True}


def test_end_card_noop_when_not_wanted():
    words = _steady_words(10000)
    edl = _bare_edl("talking_head", ms_to_frame(10000))
    out = retention.place_end_card(edl, words, style="talking_head",
                                   hints={"end_card": {"wanted": False, "text": "Follow"}})
    assert out.get("end_card") is None


def test_end_card_noop_when_wanted_but_blank_text():
    words = _steady_words(10000)
    edl = _bare_edl("talking_head", ms_to_frame(10000))
    out = retention.place_end_card(edl, words, style="talking_head",
                                   hints={"end_card": {"wanted": True, "text": "   "}})
    assert out.get("end_card") is None


def test_end_card_skipped_for_fast_cuts_and_duet_split():
    words = _steady_words(10000)
    for style in ("fast_cuts", "duet_split"):
        edl = _bare_edl(style, ms_to_frame(10000))
        out = retention.place_end_card(edl, words, style=style,
                                       hints={"end_card": {"wanted": True, "text": "Follow for more"}})
        assert out.get("end_card") is None, style


def test_end_card_xor_loop_tail_via_orchestrator():
    retention._ENV_PASSES = "structure"
    words = _words(("done", 0, 5000))
    edl = _base_edl(segments=[{"src_in": 0, "src_out": 250}])
    out = retention.apply_retention_passes(
        edl, words, style="talking_head",
        hints={"end_card": {"wanted": True, "text": "Follow for more"}})
    assert out["end_card"] == {"text": "Follow for more", "frames": 75, "show_handle": True}
    assert out["segments"][0]["src_out"] == 250   # trim_loop_tail did NOT run


def test_end_card_skip_style_still_gets_loop_tail_via_orchestrator():
    # fast_cuts wants an end_card, but place_end_card itself skips fast_cuts (WS5:
    # loop-friendly by design) — the orchestrator must fall through to
    # trim_loop_tail instead of leaving NEITHER pass applied.
    retention._ENV_PASSES = "structure"
    words = _words(("done", 0, 5000))
    edl = _base_edl(style="fast_cuts", segments=[{"src_in": 0, "src_out": 250}])
    out = retention.apply_retention_passes(
        edl, words, style="fast_cuts",
        hints={"end_card": {"wanted": True, "text": "Follow for more"}})
    assert out.get("end_card") is None
    assert out["segments"][0]["src_out"] == ms_to_frame(5000) + 10   # trim_loop_tail DID run


# ---------------------------------------------------------------------------
# WS4d — synthesize_sfx
# ---------------------------------------------------------------------------

def test_sfx_places_whoosh_at_transitions():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000),
                    segments=[{"src_in": 0, "src_out": 450}, {"src_in": 450, "src_out": 900}],
                    transitions=[{"after_segment": 0, "style": "fade_black", "frames": 12}])
    out = retention.synthesize_sfx(edl, words, sfx_assets={"whoosh": "https://cdn/w.mp3"})
    assert out["audio"]["sfx"] == [{"src_in": 450, "kind": "whoosh", "gain": retention.SFX_GAIN_DEFAULT, "url": "https://cdn/w.mp3"}]


def test_sfx_places_pop_at_punch_ins():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000),
                    overlays=[{"type": "punch_in", "src_in": 300, "src_out": 340, "scale": 1.08, "text": ""}])
    out = retention.synthesize_sfx(edl, words, sfx_assets={"pop": "https://cdn/p.mp3"})
    assert out["audio"]["sfx"] == [{"src_in": 300, "kind": "pop", "gain": retention.SFX_GAIN_DEFAULT, "url": "https://cdn/p.mp3"}]


def test_sfx_skips_kind_with_no_resolved_url():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000),
                    overlays=[{"type": "punch_in", "src_in": 300, "src_out": 340, "scale": 1.08, "text": ""}])
    out = retention.synthesize_sfx(edl, words, sfx_assets={"pop": None})
    # No cue placed -> "audio" is left untouched entirely (not stamped with an
    # empty sfx list) — build_render_plan already treats an absent audio/sfx key
    # as [], so this is a valid no-op, not a missing key.
    assert (out.get("audio") or {}).get("sfx", []) == []


def test_sfx_noop_with_no_sfx_assets():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000),
                    overlays=[{"type": "punch_in", "src_in": 300, "src_out": 340, "scale": 1.08, "text": ""}])
    out = retention.synthesize_sfx(edl, words, sfx_assets=None)
    assert (out.get("audio") or {}).get("sfx", []) == []


def test_sfx_respects_min_spacing():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000),
                    overlays=[
                        {"type": "punch_in", "src_in": 300, "src_out": 320, "scale": 1.08, "text": ""},
                        {"type": "punch_in", "src_in": 305, "src_out": 325, "scale": 1.10, "text": ""},
                    ])
    out = retention.synthesize_sfx(edl, words, sfx_assets={"pop": "https://cdn/p.mp3"})
    assert len(out["audio"]["sfx"]) == 1   # 305 is within 15f of 300


def test_sfx_respects_budget_per_30s():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)   # 900 frames -> budget = round(3 * 900/900) = 3 (v2: restraint)
    overlays = [{"type": "punch_in", "src_in": f, "src_out": f + 10, "scale": 1.08, "text": ""}
               for f in range(50, total_frames - 50, 40)]   # far more than 3, well-spaced
    edl = _bare_edl("talking_head", total_frames, overlays=overlays)
    out = retention.synthesize_sfx(edl, words, sfx_assets={"pop": "https://cdn/p.mp3"})
    assert len(out["audio"]["sfx"]) == 3


def test_sfx_skips_last_15_frames():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames,
                    overlays=[{"type": "punch_in", "src_in": total_frames - 10,
                              "src_out": total_frames - 2, "scale": 1.08, "text": ""}])
    out = retention.synthesize_sfx(edl, words, sfx_assets={"pop": "https://cdn/p.mp3"})
    assert (out.get("audio") or {}).get("sfx", []) == []


def test_sfx_noop_with_no_candidates():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000))
    out = retention.synthesize_sfx(edl, words, sfx_assets={"whoosh": "u", "pop": "u", "hit": "u"})
    assert (out.get("audio") or {}).get("sfx", []) == []


def test_sfx_does_not_mutate_input():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000),
                    overlays=[{"type": "punch_in", "src_in": 300, "src_out": 340, "scale": 1.08, "text": ""}])
    original_audio = edl.get("audio")
    retention.synthesize_sfx(edl, words, sfx_assets={"pop": "https://cdn/p.mp3"})
    assert edl.get("audio") == original_audio


def test_sfx_output_passes_invariants():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000),
                    overlays=[{"type": "punch_in", "src_in": 300, "src_out": 340, "scale": 1.08, "text": ""}])
    out = retention.synthesize_sfx(edl, words, sfx_assets={"pop": "https://cdn/p.mp3"})
    assert check_edl_invariants(out) == []


def test_sfx_applied_via_orchestrator():
    retention._ENV_PASSES = "sfx"
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000),
                    overlays=[{"type": "punch_in", "src_in": 300, "src_out": 340, "scale": 1.08, "text": ""}])
    out = retention.apply_retention_passes(edl, words, style="talking_head",
                                           sfx_assets={"pop": "https://cdn/p.mp3"})
    assert out["audio"]["sfx"] == [{"src_in": 300, "kind": "pop", "gain": retention.SFX_GAIN_DEFAULT, "url": "https://cdn/p.mp3"}]


# --- WS1: retake dedup + word-eater hardening -----------------------------------
from app import retention as _ret
from app import edl as _edl


def _wds(pairs, gap_ms=600):
    """Build words: list of (text, dur_ms); insert gap_ms of silence between phrases
    marked by a None separator."""
    out = []
    t = 0
    for p in pairs:
        if p is None:
            t += gap_ms
            continue
        text, dur = p
        out.append({"word": text, "start_ms": t, "end_ms": t + dur, "confidence": 0.99})
        t += dur
    return out


def _edl_one_segment(words):
    lo = _edl.ms_to_frame(words[0]["start_ms"])
    hi = _edl.ms_to_frame(words[-1]["end_ms"])
    return {"segments": [{"src_in": lo, "src_out": hi}], "drops": [], "style": "talking_head"}


def test_dedupe_retakes_drops_earlier_take():
    # "the protein window is a myth" ... (pause, flub) ... re-delivered
    line = [("here", 200), ("is", 150), ("why", 200), ("the", 150), ("protein", 300),
            ("window", 300), ("is", 150), ("wrong", 300)]
    words = _wds([("intro", 300), ("everyone", 300), ("listen", 300), None]
                 + line + [None] + line)
    edl = _edl_one_segment(words)
    out = _ret.dedupe_retakes(edl, words)
    # the earlier duplicate take is dropped (a drop exists covering the first "here..wrong")
    assert out["drops"], "expected a retake drop"
    first_line_start = _edl.ms_to_frame(words[3]["start_ms"])
    assert any(d["src_in"] <= first_line_start < d["src_out"] for d in out["drops"])


def test_dedupe_retakes_keeps_distinct_lines():
    words = _wds([("today", 250), ("we", 150), ("talk", 250), ("protein", 300), None,
                  ("now", 200), ("about", 200), ("sleep", 250), ("and", 150), ("stress", 300)])
    edl = _edl_one_segment(words)
    out = _ret.dedupe_retakes(edl, words)
    assert out["drops"] == []          # different content — nothing deduped


def test_dedupe_retakes_never_drops_hook():
    line = [("protein", 300), ("timing", 300), ("is", 150), ("a", 120), ("myth", 300)]
    words = _wds(line + [None] + line)   # hook itself is repeated
    edl = _edl_one_segment(words)
    out = _ret.dedupe_retakes(edl, words)
    hook_start = _edl.ms_to_frame(words[0]["start_ms"])
    assert not any(d["src_in"] <= hook_start < d["src_out"] for d in out["drops"])


def test_confidence_cut_spares_stopwords():
    # a low-confidence "is" (real function word) must survive; a low-conf garble is cut
    words = [
        {"word": "protein", "start_ms": 0, "end_ms": 300, "confidence": 0.99},
        {"word": "is", "start_ms": 300, "end_ms": 450, "confidence": 0.20},        # protected
        {"word": "grbl", "start_ms": 450, "end_ms": 600, "confidence": 0.20},      # cut
        {"word": "everything", "start_ms": 600, "end_ms": 1000, "confidence": 0.99},
    ]
    drops = _edl.detect_disfluencies(words, "aggressive")
    is_lo, is_hi = _edl.ms_to_frame(300), _edl.ms_to_frame(450)
    grbl_lo = _edl.ms_to_frame(450)
    assert not any(d.src_in <= is_lo and is_hi <= d.src_out for d in drops)     # "is" kept
    assert any(d.src_in <= grbl_lo for d in drops)                             # garble cut


def test_false_start_needs_content_word_overlap():
    # stopword-only echo ("I the" ... "I the plan") must NOT be treated as a false start
    words = _wds([("i", 150), ("the", 150), None, ("i", 150), ("the", 150),
                  ("plan", 300), ("is", 150), ("simple", 300)])
    drops = _edl.detect_disfluencies(words, "default")
    # no drop should cover the opening "i the"
    frag_lo = _edl.ms_to_frame(words[0]["start_ms"])
    assert not any(d.src_in <= frag_lo < d.src_out and d.reason == "false_start" for d in drops)


# ---------------------------------------------------------------------------
# A3 (superintelligence epic) — plan_framing
# ---------------------------------------------------------------------------

def test_framing_rotates_and_never_puts_mid_next_to_close():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.plan_framing(edl, words, style="talking_head", job_seed="job-a")
    scales = [round(s["tx_scale"], 2) for s in out["segments"]]
    assert len(scales) > 3   # multiple framing changes happened
    wide, mid, close = (retention._FRAMING_SCALES[k] for k in ("wide", "mid", "close"))
    assert set(scales) <= {wide, mid, close}
    # mid is never adjacent to close (the pattern crosses through wide); adjacent deltas
    # are the spec's 100/110/118 ladder steps (>=8%), never a <8% near-miss glitch.
    for a, b in zip(scales, scales[1:]):
        if a != b:
            delta = abs(a - b) / max(a, b)
            assert delta >= 0.08
            assert not ({round(a, 2), round(b, 2)} == {mid, close})   # mid never touches close


def test_framing_deterministic_same_seed():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    a = retention.plan_framing(edl, words, style="talking_head", job_seed="job-x")
    b = retention.plan_framing(edl, words, style="talking_head", job_seed="job-x")
    assert a == b


def test_framing_different_seed_can_differ():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    a = retention.plan_framing(edl, words, style="talking_head", job_seed="job-x")
    b = retention.plan_framing(edl, words, style="talking_head", job_seed="job-y")
    scales_a = [s["tx_scale"] for s in a["segments"]]
    scales_b = [s["tx_scale"] for s in b["segments"]]
    assert scales_a != scales_b or a["segments"] != b["segments"]


def test_framing_skips_duet_split_and_split_three():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    for style in ("duet_split", "split_three"):
        edl = _bare_edl(style, total_frames)
        out = retention.plan_framing(edl, words, style=style, job_seed="job-a")
        assert out == edl


def test_framing_noop_on_short_take():
    words = _steady_words(2000)
    total_frames = ms_to_frame(2000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.plan_framing(edl, words, style="talking_head", job_seed="job-a")
    assert out == edl


def test_framing_respects_split_budget():
    words = _steady_words(300000, step_ms=250)   # a very long take -> many candidate boundaries
    total_frames = ms_to_frame(300000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.plan_framing(edl, words, style="talking_head", job_seed="job-a")
    assert len(out["segments"]) <= 1 + retention._FRAMING_SPLIT_BUDGET


def test_framing_respects_punch_overlay_guard():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    # A punch_in overlay covering the ENTIRE take must protect every piece.
    edl = _bare_edl("talking_head", total_frames,
                    overlays=[{"type": "punch_in", "src_in": 0, "src_out": total_frames, "scale": 1.1, "text": ""}])
    out = retention.plan_framing(edl, words, style="talking_head", job_seed="job-a")
    assert all(s.get("tx_scale", 1.0) == 1.0 for s in out["segments"])


def test_framing_output_passes_invariants():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.plan_framing(edl, words, style="talking_head", job_seed="job-a")
    assert check_edl_invariants(out) == []


def test_framing_does_not_mutate_input():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    before = [dict(s) for s in edl["segments"]]
    retention.plan_framing(edl, words, style="talking_head", job_seed="job-a")
    assert edl["segments"] == before


def test_framing_applied_via_orchestrator_when_named():
    retention._ENV_PASSES = "framing"
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.apply_retention_passes(edl, words, style="talking_head", job_seed="job-a")
    assert any(s["tx_scale"] != 1.0 for s in out["segments"])


def test_framing_not_applied_when_all_but_not_named():
    retention._ENV_PASSES = "all"
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.apply_retention_passes(edl, words, style="talking_head", job_seed="job-a")
    # "all" does not include "framing" (A1 discipline: new passes bake individually)
    assert all(s.get("tx_scale", 1.0) == 1.0 for s in out["segments"])


# ---------------------------------------------------------------------------
# A4 (superintelligence epic) — jittered interrupts
# ---------------------------------------------------------------------------

def test_interrupts_jitter_off_by_default_is_byte_identical():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    a = retention.schedule_interrupts(edl, words, style="talking_head", hints={})
    b = retention.schedule_interrupts(edl, words, style="talking_head", hints={}, jitter=False)
    assert a == b


def test_interrupts_jitter_deterministic_same_seed():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    a = retention.schedule_interrupts(edl, words, style="talking_head", hints={}, jitter=True, job_seed="job-a")
    b = retention.schedule_interrupts(edl, words, style="talking_head", hints={}, jitter=True, job_seed="job-a")
    assert a == b


def test_interrupts_jitter_gaps_are_not_metronomic():
    words = _steady_words(90000)
    total_frames = ms_to_frame(90000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.schedule_interrupts(edl, words, style="talking_head", hints={}, jitter=True, job_seed="job-jit")
    events = sorted(o["src_in"] for o in out["overlays"]) + \
        [s["src_in"] for s in out["segments"] if s.get("tx_scale", 1.0) != 1.0]
    events = sorted(set(events))
    assert len(events) >= 3
    gaps = [b - a for a, b in zip(events, events[1:])]
    mean = sum(gaps) / len(gaps)
    variance = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    assert variance ** 0.5 > 8   # anti-metronome: stddev of gaps exceeds the lint floor


def test_interrupts_jitter_never_same_type_twice_in_a_row():
    words = _steady_words(120000, step_ms=250)
    total_frames = ms_to_frame(120000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.schedule_interrupts(edl, words, style="talking_head", hints={}, jitter=True, job_seed="job-b")
    # Reconstruct the ordered type sequence: punch_in overlays are "punch", text_sticker
    # overlays are "text_sticker", and a segment split with a bumped tx_scale is "framing_pop".
    events = [(o["src_in"], "punch" if o["type"] == "punch_in" else "text_sticker") for o in out["overlays"]]
    base_edl = _bare_edl("talking_head", total_frames)
    base_ins = {s["src_in"] for s in base_edl["segments"]}
    for s in out["segments"]:
        if s.get("tx_scale", 1.0) != 1.0 and s["src_in"] not in base_ins:
            events.append((s["src_in"], "framing_pop"))
    events.sort()
    types = [t for _, t in events]
    for a, b in zip(types, types[1:]):
        assert a != b


def test_interrupts_jitter_uses_short_hold_window():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.schedule_interrupts(edl, words, style="talking_head", hints={}, jitter=True, job_seed="job-c")
    punch_overlays = [o for o in out["overlays"] if o["type"] == "punch_in"]
    assert punch_overlays   # at least one punch survived the type rotation
    lo, hi = retention._INTERRUPT_JITTER_HOLD_FRAMES
    for o in punch_overlays:
        assert o["src_out"] - o["src_in"] <= hi + 1   # short window, not the fixed 75f hold


def test_interrupts_jitter_output_passes_invariants():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.schedule_interrupts(edl, words, style="talking_head", hints={}, jitter=True, job_seed="job-d")
    assert check_edl_invariants(out) == []


def test_interrupts_jitter_applied_via_orchestrator_jitter_token():
    retention._ENV_PASSES = "interrupts,jitter"
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    a = retention.apply_retention_passes(edl, words, style="talking_head", job_seed="job-e")
    retention._ENV_PASSES = "interrupts,jitter"
    b = retention.apply_retention_passes(edl, words, style="talking_head", job_seed="job-e")
    assert a == b   # deterministic through the orchestrator too


# ---------------------------------------------------------------------------
# A6 (superintelligence epic) — apply_hook_package
# ---------------------------------------------------------------------------

def test_hook_package_adds_opening_punch():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.apply_hook_package(edl, words, style="talking_head")
    opens = [o for o in out["overlays"] if o["type"] == "punch_in" and o["src_in"] == 0]
    assert opens
    assert opens[0]["scale"] == retention._HOOK_PACK_OPEN_SCALE


def test_hook_package_open_punch_coexists_with_title_but_not_another_punch():
    # C3 (v2): the hook TITLE sticker and the frame-1 open punch COEXIST (stacked hook —
    # text + motion at frame 0); only another PUNCH occupying the open suppresses it.
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    sticker = {"type": "text_sticker", "src_in": 0, "src_out": 90, "text": "hook",
               "scale": 1.0, "pos_x": 0.5, "pos_y": 0.24, "rotation": 0.0, "color": None,
               "bg": "box", "font": "inter"}
    edl = _bare_edl("talking_head", total_frames, overlays=[sticker])
    out = retention.apply_hook_package(edl, words, style="talking_head")
    opens = [o for o in out["overlays"] if o["type"] == "punch_in" and o["src_in"] == 0]
    assert opens, "title sticker must NOT suppress the frame-1 open punch (stacked hook)"

    punch = {"type": "punch_in", "src_in": 0, "src_out": 30, "scale": 1.1, "text": ""}
    edl2 = _bare_edl("talking_head", total_frames, overlays=[punch])
    out2 = retention.apply_hook_package(edl2, words, style="talking_head")
    opens2 = [o for o in out2["overlays"] if o["type"] == "punch_in"
              and o["src_in"] == 0 and o.get("scale") == retention._HOOK_PACK_OPEN_SCALE]
    assert opens2 == [], "never stacks two zooms on the open"


def test_hook_package_first_cut_by_3s_rule_fires_on_a_static_open():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)   # one long unbroken segment, no early overlay
    out = retention.apply_hook_package(edl, words, style="talking_head")
    index, _ = retention._build_output_index(out["segments"], out["drops"], retention._play_order(out))
    early_events = [s["src_in"] for s in out["segments"] if s.get("tx_scale", 1.0) != 1.0]
    assert early_events
    out_frame = retention._src_to_out(index, early_events[0])
    assert out_frame is not None and out_frame < retention._HOOK_PACK_FIRST_CUT_CEILING_OUT


def test_hook_package_skips_when_early_cut_already_exists():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    # Two segments with a cut boundary well before output frame 90.
    edl = _bare_edl("talking_head", total_frames,
                    segments=[{"src_in": 0, "src_out": 60}, {"src_in": 60, "src_out": total_frames}])
    out = retention.apply_hook_package(edl, words, style="talking_head")
    # No synthesized framing bump should have been added (the cut already covers it).
    assert all(s.get("tx_scale", 1.0) == 1.0 or s["src_in"] == 0 for s in out["segments"])


def test_hook_package_skips_duet_split():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("duet_split", total_frames)
    out = retention.apply_hook_package(edl, words, style="duet_split")
    assert out == edl


def test_hook_package_output_passes_invariants():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.apply_hook_package(edl, words, style="talking_head")
    assert check_edl_invariants(out) == []


def test_hook_package_does_not_mutate_input():
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    before_overlays = list(edl["overlays"])
    before_segments = [dict(s) for s in edl["segments"]]
    retention.apply_hook_package(edl, words, style="talking_head")
    assert edl["overlays"] == before_overlays
    assert [dict(s) for s in edl["segments"]] == before_segments


def test_hook_package_applied_via_orchestrator_when_named():
    retention._ENV_PASSES = "structure,hook_pack"
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.apply_retention_passes(edl, words, style="talking_head")
    assert any(o["type"] == "punch_in" and o["src_in"] == 0 for o in out["overlays"])


# ---------------------------------------------------------------------------
# A7 — theme threading into apply_retention_passes / individual passes
# ---------------------------------------------------------------------------

def test_apply_theme_runs_first_independent_of_retention_passes_flag():
    retention._ENV_PASSES = ""   # every deterministic pass off
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    theme = _themes.get_theme("hormozi_punch")
    out = retention.apply_retention_passes(edl, words, style="talking_head", theme=theme)
    assert out["theme_id"] == "hormozi_punch"
    assert out["caption_style"] == "bold-word"


def test_theme_none_is_a_total_noop_for_apply_theme_step():
    retention._ENV_PASSES = ""
    words = _steady_words(30000)
    total_frames = ms_to_frame(30000)
    edl = _bare_edl("talking_head", total_frames)
    out = retention.apply_retention_passes(edl, words, style="talking_head", theme=None)
    assert out == edl


def test_plan_framing_disabled_by_theme():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    theme = _themes.get_theme("docu_calm")   # framing.enabled = False
    out = retention.plan_framing(edl, words, style="talking_head", theme=theme, job_seed="job-a")
    assert out == edl


def test_plan_framing_uses_theme_scales():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    theme = _themes.get_theme("hormozi_punch")   # v2 scales {wide:1.0, mid:1.15, close:1.2} (≤120% cap)
    out = retention.plan_framing(edl, words, style="talking_head", theme=theme, job_seed="job-a")
    scales = {round(s["tx_scale"], 2) for s in out["segments"]}
    assert scales <= {1.0, 1.15, 1.2}
    assert 1.2 in scales or 1.15 in scales   # a non-wide framing actually happened


def test_schedule_interrupts_density_falls_back_to_theme():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    theme = _themes.get_theme("docu_calm")   # interrupts.density = "calm"
    with_theme = retention.schedule_interrupts(edl, words, style="talking_head", hints={}, theme=theme)
    without_theme = retention.schedule_interrupts(edl, words, style="talking_head", hints={})
    # calm density means a longer cadence -> fewer or equal insertions
    assert len(with_theme["overlays"]) <= len(without_theme["overlays"])


def test_schedule_interrupts_llm_hint_beats_theme():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    theme = _themes.get_theme("docu_calm")   # interrupts.density = "calm"
    out = retention.schedule_interrupts(edl, words, style="talking_head",
                                        hints={"interrupt_density": "dense"}, theme=theme)
    dense_alone = retention.schedule_interrupts(edl, words, style="talking_head",
                                                hints={"interrupt_density": "dense"})
    assert len(out["overlays"]) == len(dense_alone["overlays"])


def test_schedule_interrupts_genre_density_is_lowest_precedence():
    words = _steady_words(60000)
    total_frames = ms_to_frame(60000)
    edl = _bare_edl("talking_head", total_frames)
    # No theme, no llm hint -> genre wins over "standard".
    genre_only = retention.schedule_interrupts(edl, words, style="talking_head", hints={},
                                               genre_density="calm")
    standard = retention.schedule_interrupts(edl, words, style="talking_head", hints={})
    assert len(genre_only["overlays"]) <= len(standard["overlays"])
    # theme (if present) still beats genre.
    theme = _themes.get_theme("hormozi_punch")   # interrupts.density = "dense"
    theme_wins = retention.schedule_interrupts(edl, words, style="talking_head", hints={},
                                               genre_density="calm", theme=theme)
    dense_alone = retention.schedule_interrupts(edl, words, style="talking_head", hints={},
                                                theme=theme)
    assert len(theme_wins["overlays"]) == len(dense_alone["overlays"])


def test_synthesize_sfx_uses_theme_gain_db():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000),
                    overlays=[{"type": "punch_in", "src_in": 300, "src_out": 340, "scale": 1.08, "text": ""}])
    theme = _themes.get_theme("hormozi_punch")   # sfx.gain_db = -12
    out = retention.synthesize_sfx(edl, words, sfx_assets={"pop": "https://cdn/p.mp3"}, theme=theme)
    expected_gain = 10 ** (-12 / 20)
    assert abs(out["audio"]["sfx"][0]["gain"] - expected_gain) < 1e-9


def test_synthesize_sfx_theme_none_uses_module_default():
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", ms_to_frame(30000),
                    overlays=[{"type": "punch_in", "src_in": 300, "src_out": 340, "scale": 1.08, "text": ""}])
    out = retention.synthesize_sfx(edl, words, sfx_assets={"pop": "https://cdn/p.mp3"})
    assert out["audio"]["sfx"][0]["gain"] == retention.SFX_GAIN_DEFAULT


# ---------------------------------------------------------------------------
# WS3 (build 49) — beat_snap
# ---------------------------------------------------------------------------

def _beat_edl(broll_in=118, sticker_in=208):
    return {
        "segments": [{"src_in": 0, "src_out": 600, "speed": 1.0}],
        "drops": [],
        "audio": {"music": {"url": "https://cdn/track.mp3", "volume": 0.12, "duck_voice": True}},
        "broll": [{"src_in": broll_in, "src_out": broll_in + 45, "need": "entity"}],
        "overlays": [{"type": "text_sticker", "src_in": sticker_in, "src_out": sticker_in + 60,
                      "text": "POP"}],
    }


def test_beat_snap_snaps_broll_one_frame_before_beat():
    # Beat every 0.5s (120 BPM) → beat frames 0,15,30,...  b-roll at 118 is 2f from
    # the beat at 120 → snaps to 119 (one frame BEFORE), hold preserved.
    grid = [i * 0.5 for i in range(40)]
    out = retention.beat_snap(_beat_edl(), [], beat_grid=grid, beat_conf=0.9)
    b = out["broll"][0]
    assert b["src_in"] == 119
    assert b["src_out"] == 119 + 45              # hold length unchanged
    st = [o for o in out["overlays"] if o["type"] == "text_sticker"][0]
    assert st["src_in"] == 209                    # 208 → beat 210 − 1


def test_beat_snap_low_confidence_and_missing_grid_are_noops():
    edl = _beat_edl()
    assert retention.beat_snap(edl, [], beat_grid=[0.5, 1.0], beat_conf=0.2) == edl
    assert retention.beat_snap(edl, [], beat_grid=None, beat_conf=0.9) == edl
    no_music = _beat_edl(); no_music["audio"] = {}
    assert retention.beat_snap(no_music, [], beat_grid=[0.5], beat_conf=0.9) == no_music


def test_beat_snap_never_moves_far_events_segments_or_hook_zone():
    grid = [i * 0.5 for i in range(40)]
    edl = _beat_edl(broll_in=127)                 # 7f from nearest beat — outside ±4 tolerance
    edl["broll"].append({"src_in": 10, "src_out": 40, "need": "entity"})   # hook guard zone
    out = retention.beat_snap(edl, [], beat_grid=grid, beat_conf=0.9)
    assert out["broll"][0]["src_in"] == 127       # too far → untouched
    assert out["broll"][1]["src_in"] == 10        # inside first second → untouched
    assert out["segments"] == edl["segments"]     # dialogue cuts NEVER move


def test_beat_snap_respects_speed_and_kept_mapping():
    # Segment at 2x speed: output frame = src/2. Beat at 2.0s = out 60 → src 120.
    # b-roll src 116 → out 58, delta_out +1 (target 59) → delta_src +2.
    edl = _beat_edl(broll_in=116)
    edl["segments"] = [{"src_in": 0, "src_out": 600, "speed": 2.0}]
    grid = [2.0]
    out = retention.beat_snap(edl, [], beat_grid=grid, beat_conf=0.9)
    assert out["broll"][0]["src_in"] == 118


def test_beat_snap_token_registered_and_inert_without_grid(monkeypatch):
    monkeypatch.setattr(retention, "_ENV_PASSES", "beat_snap")
    edl = _beat_edl()
    words = [{"text": "hi", "start": 0, "end": 400}]
    out = retention.apply_retention_passes(edl, words, style="talking_head",
                                           beat_grid=None, beat_conf=None)
    assert out["broll"][0]["src_in"] == edl["broll"][0]["src_in"]   # no grid → no-op
