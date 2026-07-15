"""B5: predictedScore honesty on the unjudged/mock paths. _draft_score replaces the
old hardcoded 78 with cheap deterministic craft signals, blended toward the creator's
real arm outcomes via the same _calibration_signal machinery _final_score uses."""
import main


def _sc(hook="A solid, specific hook about your niche", body="Some body text.\n\nWith a beat.",
       style="talking_head"):
    return {"hook": hook, "body": body, "style": style}


def test_clamped_between_40_and_74():
    lo = main._draft_score("default", _sc(hook="in this video i'll show you what's up",
                                          body="one line no beats"))
    hi = main._draft_score("default", _sc(hook="7 mistakes that quietly cost you $400 a month",
                                          body="Beat one.\n\nBeat two."))
    assert 40 <= lo <= 74
    assert 40 <= hi <= 74


def test_slop_opener_scores_lower_than_clean_hook():
    slop = main._draft_score("default", _sc(hook="in this video i'll show you what's up"))
    clean = main._draft_score("default", _sc(hook="The 3 numbers that decide your 30s"))
    assert slop < clean


def test_question_opener_scores_lower_than_statement():
    question = main._draft_score("default", _sc(hook="Why does everyone get this backwards?"))
    statement = main._draft_score("default", _sc(hook="Everyone gets this backwards — here's why"))
    assert question < statement


def test_hook_with_digit_scores_higher_than_without():
    with_digit = main._draft_score("default", _sc(hook="The 3 habits that fixed my mornings"))
    without = main._draft_score("default", _sc(hook="The habits that fixed my mornings for good"))
    assert with_digit > without


def test_stage_direction_body_scores_lower_than_spoken_body():
    dirty = main._draft_score("default", _sc(body="Talk about how this works, then show the result."))
    clean = main._draft_score("default", _sc(body="Here's exactly how this works, step by step."))
    assert dirty < clean


def test_multi_beat_body_scores_higher_than_wall_of_text():
    beats = main._draft_score("default", _sc(body="First beat here.\n\nSecond beat here."))
    wall = main._draft_score("default", _sc(body="First beat here. Second beat here."))
    assert beats > wall


def test_dict_shaped_hook_supported():
    sc = {"hook": {"text": "The 3 numbers that decide your 30s"}, "body": "Some body.", "style": "talking_head"}
    score = main._draft_score("default", sc)
    assert 40 <= score <= 74


def test_calibration_blend_pulls_toward_real_arm_outcomes(monkeypatch):
    # Strong positive real-world signal for this style should pull the score UP
    # relative to the same script with no calibration data at all.
    monkeypatch.setattr(main, "_arm_stats", {
        "creator-with-data": {"style:talking_head": {"n": 20, "effect": 0.95}},
    })
    sc = _sc()
    with_data = main._draft_score("creator-with-data", sc)
    without_data = main._draft_score("creator-with-no-data", sc)
    assert with_data > without_data
    assert 40 <= with_data <= 74


def test_calibration_blend_pulls_toward_low_real_arm_outcomes(monkeypatch):
    monkeypatch.setattr(main, "_arm_stats", {
        "creator-with-data": {"style:talking_head": {"n": 20, "effect": 0.05}},
    })
    sc = _sc()
    with_data = main._draft_score("creator-with-data", sc)
    without_data = main._draft_score("creator-with-no-data", sc)
    assert with_data < without_data


def test_no_calibration_data_falls_back_to_base_cleanly(monkeypatch):
    monkeypatch.setattr(main, "_arm_stats", {})
    score = main._draft_score("anyone", _sc())
    assert 40 <= score <= 74


# ---------------------------------------------------------------------------
# Regression: no fast-path response ever contains the old fabricated 78.
# ---------------------------------------------------------------------------

def test_mock_scripts_never_hardcode_78():
    req = main.ScriptRequest(niche="fitness for busy parents", count=5, creator_id="default")
    scripts = main.mock_scripts(req)
    assert len(scripts) == 5
    for s in scripts:
        assert 40 <= s["predictedScore"] <= 74


def test_mock_scripts_scores_vary_by_content(monkeypatch):
    monkeypatch.setattr(main, "_arm_stats", {})
    req = main.ScriptRequest(niche="personal finance", count=3, creator_id="default")
    scripts = main.mock_scripts(req)
    scores = {s["predictedScore"] for s in scripts}
    # Real craft signals (word count, digits, structure) differ across the angle
    # templates — a hardcoded constant would collapse this set to size 1.
    assert len(scores) > 1
