"""B4: relevance-to-creator — the judge's new axis + the deterministic invariant floor
+ predictedScore blending. There was previously ZERO relevance-to-creator coverage
anywhere: the judge's 4 axes were hook/specificity/format_fit/voice_match, none of
which check whether a script actually fits THIS creator's niche."""
import main
import prompts
from eval.invariants import _flag_offbrand, evaluate_script


_POWERLIFT_BRAND = {"niche": "powerlifting coaching for busy dads",
                    "known_for": "strength training programming", "what_you_do": "coach powerlifters"}


# ---------------------------------------------------------------------------
# _flag_offbrand polarity
# ---------------------------------------------------------------------------

def test_flag_offbrand_catches_no_term_overlap():
    sc = {"hook": "The best skincare routine for glowing skin.",
         "body": "Use this serum every morning for radiant results."}
    assert _flag_offbrand(sc, _POWERLIFT_BRAND) is not None


def test_flag_offbrand_clean_on_literal_overlap():
    sc = {"hook": "Every powerlifting coach says this and it's backwards.",
         "body": "I coach powerlifters who thought more gym days meant more strength."}
    assert _flag_offbrand(sc, _POWERLIFT_BRAND) is None


def test_flag_offbrand_never_fires_without_brand_terms():
    # An empty/unset brand has nothing to compare against -> never a false positive.
    sc = {"hook": "Anything goes here.", "body": "No niche context at all."}
    assert _flag_offbrand(sc, {}) is None


def test_flag_offbrand_is_excluded_from_gate():
    # It's a QUALITY flag (rate-tracked), never a hard GATE failure.
    sc = {"hook": "The best skincare routine for glowing skin.", "hookSignal": "specificity",
         "body": "Use this serum every morning for radiant results.", "formatId": "listicle",
         "cta": "Follow.", "predictedScore": 80, "style": "talking_head"}
    result = evaluate_script(sc, _POWERLIFT_BRAND)
    assert result["gate_passed"] is True
    assert any(f.startswith("offbrand") for f in result["quality_flags"])


# ---------------------------------------------------------------------------
# Judge schema round-trip
# ---------------------------------------------------------------------------

def test_judge_schema_includes_relevance_to_creator():
    assert "relevance_to_creator" in prompts.SCRIPT_JUDGE_JSON_ELEMENT["required"]
    assert "relevance_to_creator" in prompts.SCRIPT_JUDGE_JSON_ELEMENT["properties"]
    assert "relevance_to_creator" in prompts.SCRIPT_JUDGE_SCHEMA


def test_judge_prompt_mentions_relevance_rubric():
    sys, usr = prompts.script_judge_prompt(
        [{"hook": "x", "body": "y", "formatId": "myth-buster", "cta": "z", "altHooks": []}],
        "talking_head", brand=_POWERLIFT_BRAND)
    assert "relevance_to_creator" in sys
    assert "relevance_to_creator<60" in sys


def test_by_index_loop_tolerates_missing_relevance_field():
    # An older cached judge response (pre-B4 shape) must not crash the by_index handling.
    verdict = {"index": 0, "hook_strength": 80, "specificity": 70, "format_fit": 75,
              "voice_match": 60, "slop": False, "fabricated": False, "best_hook": 0,
              "verdict": "keep", "weakest": "", "note": ""}
    score = main._blend_score(verdict)
    assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# _blend_score weighting
# ---------------------------------------------------------------------------

def test_blend_score_with_relevance_weights_sum_correctly():
    perfect = {"hook_strength": 100, "specificity": 100, "format_fit": 100,
              "voice_match": 100, "relevance_to_creator": 100}
    assert main._blend_score(perfect) == 100


def test_blend_score_low_relevance_drags_score_down():
    base = {"hook_strength": 90, "specificity": 90, "format_fit": 90, "voice_match": 90}
    high_relevance = main._blend_score({**base, "relevance_to_creator": 95})
    low_relevance = main._blend_score({**base, "relevance_to_creator": 10})
    assert low_relevance < high_relevance


def test_blend_score_falls_back_to_pre_b4_weights_when_relevance_absent():
    # No relevance_to_creator key at all (not even 0) -> the pre-B4 weighted formula,
    # so an old cached verdict shape doesn't silently zero out the score.
    old_shape = {"hook_strength": 80, "specificity": 70, "format_fit": 75, "voice_match": 60}
    score = main._blend_score(old_shape)
    expected = round(0.50 * 80 + 0.25 * 70 + 0.15 * 75 + 0.10 * 60)
    assert score == expected


# ---------------------------------------------------------------------------
# quality_scripts end-to-end with a low-relevance verdict (the LLM judge decides
# verdict='revise' per the prompt's rule; this verifies the pipeline CODE correctly
# acts on that verdict and folds relevance_to_creator into the resulting score).
# ---------------------------------------------------------------------------

def test_quality_scripts_revises_and_scores_low_relevance_verdict(monkeypatch):
    import asyncio
    monkeypatch.setattr(main, "AI_QUALITY", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    draft = {"hook": "fine hook", "body": "fine body text here", "style": "talking_head",
            "altHooks": []}

    async def fake(system, user, schema, model=main.HAIKU, max_tokens=1400,
                   temperature=None, array_key=None):
        if array_key == "verdicts":
            # The judge decided 'revise' because relevance_to_creator is low, per its
            # own prompt-embedded rule — the pipeline code just has to act on it.
            return [{"index": 0, "verdict": "revise", "best_hook": 0, "hook_strength": 90,
                     "specificity": 90, "format_fit": 90, "voice_match": 90,
                     "relevance_to_creator": 20, "slop": False, "fabricated": False}]
        if array_key == "scripts":
            return [{"hook": "revised on-brand hook", "body": "revised on-brand body text",
                    "style": "talking_head"}]
        return []
    monkeypatch.setattr(main, "anthropic_json", fake)
    out = asyncio.run(main.quality_scripts({}, "talking_head", [draft], creator_id="default"))
    assert out[0]["body"] == "revised on-brand body text"   # the revise pass actually ran
    assert 0 <= out[0]["predictedScore"] <= 100
