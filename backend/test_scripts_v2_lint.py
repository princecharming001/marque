"""scripts_v2.lint: one trigger + one near-miss per tell code, plus whole-script checks."""
from __future__ import annotations

import pytest

from app.scripts_v2.lint import (TELL_CODES, Finding, lint_script, spoken_text, summarize,
                                 tells_count)


def _s(hook="I tried this for a week.",
       body="Day one was rough, and I almost quit before lunch. Then it clicked.",
       cta="Try it tomorrow."):
    return {"hook": hook, "body": body, "cta": cta}


def _codes(script, **kw):
    return {f.code for f in lint_script(script, **kw)}


# (code, trigger script, trigger kwargs, near-miss script, near-miss kwargs)
CASES = [
    ("opener_cliche",
     _s(hook="Here's the thing about sleep."), {},
     _s(hook="Here's what my sleep tracker found."), {}),
    ("question_hook_generic",
     _s(hook="Why do your workouts stop working?"), {},
     _s(hook="Why did my Garmin flag my sleep in March?"), {}),
    ("not_x_its_y",
     _s(body="Sleep isn't about hours. It's about timing."), {},
     _s(body="This isn't easy. I know that."), {}),
    ("wrapup_ending",
     _s(cta="Remember, small steps add up."), {},
     _s(body="I still remember the first week.", cta="Try it tomorrow."), {}),
    ("template_cta",
     _s(cta="Follow for more tips."), {},
     _s(cta="Follow the plan for two weeks and message me."), {}),
    ("list_markers",
     _s(body="First, cut caffeine after noon. Second, dim the lights by nine."), {},
     _s(body="First impressions last. The second week got easier."), {}),
    ("stage_direction",
     _s(body="[B-roll of the gym] Day one was rough."), {},
     _s(body="I wrote the whole plan in a (beat-up) notebook."), {}),
    ("invented_stat",
     _s(body="Studies show 40% of people quit in week one."), {},
     _s(body="I quit 40% of the way through."), {"profile": {"allowed_claims": ["40%"]}}),
    ("ai_word",
     _s(body="You need to leverage your mornings."), {},
     _s(body="You need to use your mornings."), {}),
    ("exemplar_overlap",
     _s(body="I started drinking water before coffee every single morning and it helped."),
     {"profile": {"exemplar_texts": ["I started drinking water before coffee every single morning last year"]}},
     _s(body="I started drinking water before my coffee and it helped."),
     {"profile": {"exemplar_texts": ["I started drinking water before coffee every single morning last year"]}}),
    ("duplicate_opener",
     _s(hook="I deleted Instagram for thirty days straight."),
     {"recent_openers": ["I deleted Instagram for thirty days and here is what happened"]},
     _s(hook="I deleted Instagram for thirty days straight."),
     {"recent_openers": ["My coach banned phones at dinner"]}),
    ("tricolon",
     _s(body="It was faster, cheaper, better."), {},
     _s(body="Look, I know this sounds weird, but it works."), {}),
    ("aphorism_tic",
     _s(body="Read that again."), {},
     _s(body="Read that chapter again tonight."), {}),
    ("low_contractions",
     _s(body="It is hard, and I do not like it, and you are going to hate the first week more "
             "than anything. That is fine."), {},
     _s(body="It's hard, I don't like it, and you're going to hate it, but I'm doing it anyway. "
             "That is fine."), {}),
    ("uniform_rhythm",
     _s(hook="I tried cold showers.",
        body="They were brutal at first. Then they got easier. Now I like them a lot. My skin looks better.",
        cta="Try it this week."), {},
     _s(hook="I tried cold showers.",
        body="Brutal. The first one lasted maybe ten seconds and I screamed loud enough that my "
             "roommate checked on me. Then it got easier. Now I actually look forward to them.",
        cta="Try it."), {}),
    ("address_mismatch",
     _s(body="You guys need to hear this."), {"profile": {"address_form": "bro"}},
     _s(body="Bro, you need to hear this."), {"profile": {"address_form": "bro"}}),
    ("length_out_of_range",
     _s(), {"target_words": (100, 140)},
     _s(), {"target_words": (10, 40)}),
    ("too_long",
     _s(), {"target_words": (5, 10)},
     _s(), {"target_words": (10, 40)}),
    ("fill_in_question",
     _s(body="Day one was rough. [fill-in: what did you do on day one to get through it?]"), {},
     _s(body="Day one was rough, and it took me [your number] tries."), {}),
    ("long_close",
     _s(cta="Protect your sleep, drink water before your first meeting, walk after lunch, and keep sessions "
            "short enough to repeat every single week on your busiest days."), {},
     _s(cta="Try it tomorrow and tell me how it went."), {}),
    ("em_dash",
     _s(body="Day one was rough — I almost quit."), {},
     _s(body="Day one was rough - I almost quit."), {}),
    ("hedge_stack",
     _s(body="This could potentially help."), {},
     _s(body="This could help."), {}),
]


def test_every_code_has_a_case():
    assert {c[0] for c in CASES} == set(TELL_CODES)
    assert len(TELL_CODES) == len(set(TELL_CODES))


@pytest.mark.parametrize("code,script,kw,_m,_mk", CASES, ids=[c[0] for c in CASES])
def test_trigger_fires_exactly_that_code(code, script, kw, _m, _mk):
    assert _codes(script, **kw) == {code}


@pytest.mark.parametrize("code,_s_,_k,script,kw", CASES, ids=[c[0] for c in CASES])
def test_near_miss_stays_quiet(code, _s_, _k, script, kw):
    assert code not in _codes(script, **kw)


def test_findings_carry_span_and_severity():
    f = lint_script(_s(body="You need to leverage your mornings."))
    assert f == [Finding("ai_word", "hard", "AI-flavored word: 'leverage'", "leverage")]
    assert {x.severity for x in lint_script(_s(body="This could potentially help."))} == {"soft"}


def test_ai_word_one_finding_per_distinct_word():
    f = lint_script(_s(body="Leverage this. Then leverage that. Then unlock it."))
    assert [x.span.lower() for x in f if x.code == "ai_word"] == ["leverage", "unlock"]


def test_spoken_text_joins_and_flattens_paragraphs():
    assert spoken_text({"hook": "A.", "body": "B one.\n\nB two.", "cta": "C."}) == "A. B one. B two. C."
    assert spoken_text({"hook": "A.", "body": None}) == "A."


GOOD = {
    "hook": "I deleted Instagram off my phone for 30 days and my sleep tracker noticed before I did.",
    "body": ("Night one, I'm lying there bored out of my mind. Night four, I'm asleep by eleven "
             "without trying. By week two my average went from six hours ten to seven hours forty, "
             "and I didn't change anything else. Same bed. Same coffee. What changed is I stopped "
             "scrolling for twenty minutes after I turned the lights off, because the app wasn't "
             "there to open. That's 20% more sleep for doing less.\n\n"
             "I'm not telling you to quit social media forever. I lasted 30 days and I'm back on it "
             "now, with a timer."),
    "cta": "Tonight, move the app off your home screen and see what your tracker says in a week.",
}

ROBOTIC = {
    "hook": "Ever wondered why your progress has stalled despite your dedication?",
    "body": ("Here's the thing: it's not about working harder, it's about working smarter. Today I'm "
             "going to share three game-changing strategies. First, prioritize progressive overload. "
             "Second, optimize your recovery. Third, track your nutrition meticulously. Additionally, "
             "consistency is crucial. Studies show 73% of lifters plateau."),
    "cta": "Remember, fitness is a journey, not a destination. Follow for more tips like this!",
}


def test_good_human_script_is_clean():
    findings = lint_script(GOOD, profile={"allowed_claims": ["20%"]}, target_words=(90, 130))
    s = summarize(findings)
    assert s["hard"] == 0, findings
    assert s["soft"] <= 1, findings
    assert 100 <= len(spoken_text(GOOD).split()) <= 140


def test_good_script_stat_is_flagged_without_allowed_claims():
    assert "invented_stat" in _codes(GOOD)


def test_robotic_script_lights_up():
    findings = lint_script(ROBOTIC)
    s = summarize(findings)
    assert len(s["codes"]) >= 6, s
    assert {"opener_cliche", "not_x_its_y", "template_cta", "list_markers", "invented_stat",
            "ai_word", "wrapup_ending"} <= set(s["codes"])
    assert tells_count(findings) == s["hard"] + s["soft"] == len(findings)
    assert s["hard"] >= 6


def test_summarize_and_tells_count_shapes():
    assert summarize([]) == {"hard": 0, "soft": 0, "codes": []}
    assert tells_count(iter(())) == 0
    assert lint_script({"hook": "", "body": "", "cta": ""}) == []
