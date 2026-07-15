"""A1: the deterministic pre-render edit lint (app/edit_lint.py). One good/bad pair per
check. The 6 ERROR checks are also covered end-to-end by eval.edl_eval.self_check_lint
(eval/edit_golden.known_bad_lint) — this file adds the 5 WARN checks + a few edge cases
and gives fast, standalone pytest coverage for all 11."""
from app.edit_lint import lint_edl, lint_summary
from eval.edit_golden import _lint_base_edl, _lint_words, known_bad_lint, known_good_lint


def _codes(findings, severity=None):
    return [f["code"] for f in findings if severity is None or f["severity"] == severity]


# ---------------------------------------------------------------------------
# The 6 ERROR checks (delegated to the shared eval fixtures — one assertion each
# so a regression here fails fast with a readable name, not just eval's summary).
# ---------------------------------------------------------------------------

def test_known_good_lint_fixture_is_error_clean():
    good = known_good_lint()
    findings = lint_edl(good["edl"], good["words"], style="talking_head")
    assert _codes(findings, "error") == []


def test_every_known_bad_lint_case_is_caught():
    for case in known_bad_lint():
        style = case["edl"].get("style", "talking_head")
        findings = lint_edl(case["edl"], case["words"], style=style)
        assert case["code"] in _codes(findings, "error"), \
            f"{case['code']} ({case['why']}) not caught: {lint_summary(findings)}"


# ---------------------------------------------------------------------------
# WARN checks (not covered by the error-only eval tier)
# ---------------------------------------------------------------------------

def test_metronomic_intervals_flags_suspiciously_regular_gaps():
    words = _lint_words()
    edl = _lint_base_edl(words)
    # Isolate the metronome signal: a single segment (no cut-boundary events) and no
    # transitions, so the only "points" are these perfectly evenly-spaced overlays.
    edl["segments"] = [{"src_in": 0, "src_out": 460, "tx_scale": 1.0, "tx_x": 0.0, "tx_y": 0.0}]
    edl["transitions"] = []
    edl["overlays"] = [{"type": "punch_in", "src_in": f, "src_out": f + 10, "scale": 1.08,
                        "text": "", "font": "inter"} for f in (40, 100, 160, 220, 280, 340)]
    findings = lint_edl(edl, words, style="talking_head")
    assert "metronomic_intervals" in _codes(findings, "warn")


def test_metronomic_intervals_clean_on_jittered_gaps():
    words = _lint_words(n=200, step_ms=80)
    edl = _lint_base_edl(words)
    # Jitter the overlay starts so gaps vary widely.
    jitter = [30, 250, 340, 700, 760, 1400]
    edl["overlays"] = [{"type": "punch_in", "src_in": f, "src_out": f + 20, "scale": 1.08,
                        "text": "", "font": "inter"} for f in jitter]
    findings = lint_edl(edl, words, style="talking_head")
    assert "metronomic_intervals" not in _codes(findings, "warn")


def test_repeated_interrupt_type_flags_three_in_a_row():
    words = _lint_words()
    edl = _lint_base_edl(words)   # total source frames ~491 — keep anchors well inside that
    edl["overlays"] = [{"type": "punch_in", "src_in": f, "src_out": f + 20, "scale": 1.08,
                        "text": "", "font": "inter"} for f in (40, 120, 200, 280)]
    findings = lint_edl(edl, words, style="talking_head")
    assert "repeated_interrupt_type" in _codes(findings, "warn")


def test_repeated_interrupt_type_clean_when_alternating():
    words = _lint_words()
    edl = _lint_base_edl(words)  # already alternates punch_in/text_sticker
    findings = lint_edl(edl, words, style="talking_head")
    assert "repeated_interrupt_type" not in _codes(findings, "warn")


def test_anchor_drift_flags_overlay_far_from_any_word():
    words = _lint_words(n=50, step_ms=100)   # words only span source frames 0-~150
    edl = _lint_base_edl(words)
    edl["overlays"] = [{"type": "punch_in", "src_in": 900, "src_out": 930, "scale": 1.08,
                        "text": "", "font": "inter"}]
    findings = lint_edl(edl, words, style="talking_head")
    assert "anchor_drift" in _codes(findings, "warn")


def test_anchor_drift_clean_when_word_anchored():
    words = _lint_words()
    edl = _lint_base_edl(words)   # overlays are built by snapping to the nearest word
    findings = lint_edl(edl, words, style="talking_head")
    assert "anchor_drift" not in _codes(findings, "warn")


def test_effect_off_emphasis_flags_uncovered_big_punch():
    words = _lint_words()
    edl = _lint_base_edl(words)
    edl["overlays"] = [{"type": "punch_in", "src_in": 500, "src_out": 560, "scale": 1.15,
                        "text": "", "font": "inter"}]
    findings = lint_edl(edl, words, style="talking_head", emphasis_spans=[(10, 40)])
    assert "effect_off_emphasis" in _codes(findings, "warn")


def test_effect_off_emphasis_clean_when_covering_a_span():
    words = _lint_words()
    edl = _lint_base_edl(words)
    edl["overlays"] = [{"type": "punch_in", "src_in": 500, "src_out": 560, "scale": 1.15,
                        "text": "", "font": "inter"}]
    findings = lint_edl(edl, words, style="talking_head", emphasis_spans=[(510, 540)])
    assert "effect_off_emphasis" not in _codes(findings, "warn")


def test_effect_off_emphasis_never_flags_without_spans_provided():
    # No emphasis_spans passed at all -> nothing to compare against -> never a false positive.
    words = _lint_words()
    edl = _lint_base_edl(words)
    edl["overlays"] = [{"type": "punch_in", "src_in": 500, "src_out": 560, "scale": 1.15,
                        "text": "", "font": "inter"}]
    findings = lint_edl(edl, words, style="talking_head")
    assert "effect_off_emphasis" not in _codes(findings, "warn")


def test_ungraded_is_a_noop_without_an_active_theme():
    words = _lint_words()
    edl = _lint_base_edl(words)
    edl["look"] = None
    findings = lint_edl(edl, words, style="talking_head", theme=None)
    assert "ungraded" not in _codes(findings, "warn")


def test_ungraded_flags_missing_look_when_theme_wants_a_grade():
    class _FakeTheme:
        grade = {"filter": "film", "intensity": 0.8}
    words = _lint_words()
    edl = _lint_base_edl(words)
    edl["look"] = None
    findings = lint_edl(edl, words, style="talking_head", theme=_FakeTheme())
    assert "ungraded" in _codes(findings, "warn")


def test_ungraded_clean_when_look_is_set():
    class _FakeTheme:
        grade = {"filter": "film", "intensity": 0.8}
    words = _lint_words()
    edl = _lint_base_edl(words)
    edl["look"] = {"filter": "film", "intensity": 0.8, "adjust": {}}
    findings = lint_edl(edl, words, style="talking_head", theme=_FakeTheme())
    assert "ungraded" not in _codes(findings, "warn")


# ---------------------------------------------------------------------------
# Purity / edge cases
# ---------------------------------------------------------------------------

def test_lint_never_mutates_the_edl():
    words = _lint_words()
    edl = _lint_base_edl(words)
    import copy
    before = copy.deepcopy(edl)
    lint_edl(edl, words, style="talking_head")
    assert edl == before


def test_empty_segments_returns_no_findings():
    assert lint_edl({"segments": []}, [], style="talking_head") == []


def test_lint_summary_shape():
    words = _lint_words()
    edl = _lint_base_edl(words)
    edl["overlays"] = []   # forces static_window + static_open
    findings = lint_edl(edl, words, style="talking_head")
    summary = lint_summary(findings)
    assert summary["errors"] == len(_codes(findings, "error"))
    assert summary["warns"] == len(_codes(findings, "warn"))
    assert summary["codes"] == [f["code"] for f in findings]
