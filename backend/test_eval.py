"""CI enforcement of the offline eval spine (docs/07-ai-system.md §8.5).

Keyless-green: exercises the deterministic invariants + golden set. The live
generation scorecard (which needs an API key) is exercised by eval/run_eval.py,
not here.
"""
from eval import golden
from eval.invariants import evaluate_script, evaluate_batch
from eval.run_eval import _self_check


def test_golden_self_check_passes():
    ok, errs = _self_check()
    assert ok, "golden-set self-check failed:\n" + "\n".join(errs)


def test_known_good_scripts_pass_clean():
    for g in golden.KNOWN_GOOD:
        r = evaluate_script(g["script"], g["brand"])
        assert r["gate_passed"], (g["script"]["title"], r["failures"])
        # B4: _flag_offbrand is a soft, lossy naive-term-overlap heuristic — excluded
        # from this hard tripwire for the same reason eval/run_eval.py excludes it
        # (a legitimately on-brand script can share zero literal niche terms).
        other_flags = [f for f in r["quality_flags"] if not f.startswith("offbrand")]
        assert not other_flags, (g["script"]["title"], other_flags)


def test_known_bad_scripts_are_caught():
    for b in golden.KNOWN_BAD:
        r = evaluate_script(b["script"], b["brand"])
        hit_gate = b.get("expect_gate") and any(b["expect_gate"] in f for f in r["failures"])
        hit_flag = b.get("expect_flag") and any(b["expect_flag"] in f for f in r["quality_flags"])
        assert hit_gate or hit_flag, f"{b['why']} not caught: {r}"


def test_banned_phrase_is_case_insensitive():
    r = evaluate_script(
        {"hook": "The one move to fix posture.", "body": "You will get SHREDDED fast.",
         "cta": "Follow.", "formatId": "myth-buster", "style": "talking_head",
         "hookSignal": "stakes", "predictedScore": 70},
        {"non_negotiables": ["shredded"]})
    assert not r["gate_passed"]
    assert any("no_banned_phrase" in f for f in r["failures"])


def test_slop_and_question_openers_flagged():
    r = evaluate_script(
        {"hook": "Ever wondered why you're tired?", "body": "A sufficiently long body here.",
         "cta": "Follow.", "formatId": "myth-buster", "style": "talking_head",
         "hookSignal": "curiosity", "predictedScore": 70}, {})
    assert r["gate_passed"]                         # structurally fine...
    assert any("slop opener" in f for f in r["quality_flags"])       # ...but slop
    assert any("question" in f for f in r["quality_flags"])          # ...and a question


def test_evaluate_batch_scorecard():
    scripts = [g["script"] for g in golden.KNOWN_GOOD] + [golden.KNOWN_BAD[0]["script"]]
    card = evaluate_batch(scripts, {})
    assert card["n"] == len(golden.KNOWN_GOOD) + 1
    assert 0.0 <= card["gate_pass_rate"] <= 1.0
    assert card["quality_flag_rate"] > 0            # the stage-direction known-bad flags
