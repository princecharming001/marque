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
from app.edl import check_edl_invariants, ms_to_frame


@pytest.fixture(autouse=True)
def _reset_passes_flag():
    before = retention._ENV_PASSES
    yield
    retention._ENV_PASSES = before


def _words(*specs):
    return [{"word": w, "start_ms": s, "end_ms": e, **extra} for (w, s, e, *rest) in specs
            for extra in (rest[0] if rest else {},)]


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
