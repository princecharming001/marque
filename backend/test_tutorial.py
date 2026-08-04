"""R2 tutorial pregen — keyless deterministic tests: exact-substring validation,
the honest flag-off template, hook pattern detection, the monkeypatched-LLM happy
path, and empty-script robustness."""
from __future__ import annotations

import asyncio

from app import palo_flags, tutorial

SCRIPT = {
    "title": "I quit caffeine for 30 days",
    "hook": "I gave up caffeine for 30 days and day 12 nearly broke me.",
    "body": ("Day one was easy. By day four the headaches started.\n"
             "Day twelve I almost quit, but then something flipped."),
    "cta": "Follow for day 60, because this gets weirder.",
}
TEXT = SCRIPT["hook"] + "\n" + SCRIPT["body"] + "\n" + SCRIPT["cta"]


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self):
        self.usage = []

    async def load_prompt_override(self, key):
        return None

    async def record_ai_usage(self, row):
        self.usage.append(row)
        return True


def _arm(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "TUTORIAL", True)


# --- validate_steps: the exact-substring guard ---------------------------------

def test_validate_keeps_exact_and_drops_mismatch():
    steps = [
        {"title": "Hook", "explanation": "e1",
         "highlight_text": "day 12 nearly broke me."},          # exact -> kept
        {"title": "Tension", "explanation": "e2",
         "highlight_text": "Day 12 Nearly Broke Me."},          # case drift -> dropped
        {"title": "Payoff", "explanation": "e3",
         "highlight_text": "a line the model invented"},        # fabricated -> dropped
        {"title": "Empty", "explanation": "e4", "highlight_text": ""},  # empty -> dropped
        "not-a-dict",                                            # garbage -> dropped
    ]
    out = tutorial.validate_steps(steps, TEXT)
    assert len(out) == 1
    assert out[0] == {"title": "Hook", "explanation": "e1",
                      "highlight_text": "day 12 nearly broke me."}


def test_validate_drops_step_with_no_explanation():
    steps = [{"title": "Hook", "explanation": "", "highlight_text": SCRIPT["hook"]}]
    assert tutorial.validate_steps(steps, TEXT) == []


def test_validate_empty_script_text_drops_everything():
    steps = [{"title": "Hook", "explanation": "e", "highlight_text": "anything"}]
    assert tutorial.validate_steps(steps, "") == []
    assert tutorial.validate_steps([], TEXT) == []
    assert tutorial.validate_steps(None, TEXT) == []


def test_validate_strips_but_stays_verbatim():
    steps = [{"title": " Hook ", "explanation": " e ",
              "highlight_text": "  day 12 nearly broke me.  "}]
    out = tutorial.validate_steps(steps, TEXT)
    assert out[0]["highlight_text"] == "day 12 nearly broke me."
    assert out[0]["highlight_text"] in TEXT


# --- hook pattern detector ------------------------------------------------------

def test_detect_hook_pattern_cases():
    cases = {
        "What if everything you know about sleep is fake?": "question",
        "I tried waking up at 5am for 30 days": "number_claim",
        "Stop stretching before workouts": "contrarian",
        "Nobody talks about the real cost of vanlife": "contrarian",
        "Running on two hours of sleep, again": "mid_action",
        "POV: the barista knows the order by heart": "mid_action",
        "I'm about to lose the biggest client we have": "mid_action",
        "Here is a video about my day": "other",
        "": "other",
    }
    for hook, want in cases.items():
        assert tutorial.detect_hook_pattern(hook) == want, hook


def test_detect_hook_pattern_never_raises_on_junk():
    assert tutorial.detect_hook_pattern(None) == "other"


# --- keyless / flag-off template (teaching works cold) --------------------------

def test_flag_off_returns_honest_template_without_llm(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", False)

    async def boom(*a, **k):                        # any LLM touch = test failure
        raise AssertionError("LLM called with flag off")
    monkeypatch.setattr(tutorial, "anthropic_cached_json", boom)

    out = _run(tutorial.pregen_tutorial(None, "c1", SCRIPT))
    assert out["mode"] == "mock"
    assert [s["title"] for s in out["steps"]] == ["Hook", "Payoff"]
    for s in out["steps"]:
        assert s["highlight_text"] in TEXT           # exact-substring contract holds
    assert out["steps"][0]["highlight_text"] == SCRIPT["hook"]
    assert out["steps"][1]["highlight_text"] == SCRIPT["cta"]
    # shared ownership: "the script", never "your"
    for s in out["steps"]:
        assert " your " not in f" {s['explanation'].lower()} "


def test_template_knowledge_calibration():
    basic = tutorial._mock_tutorial(SCRIPT, "basic")
    advanced = tutorial._mock_tutorial(SCRIPT, "advanced")
    b0, a0 = basic["steps"][0]["explanation"], advanced["steps"][0]["explanation"]
    assert b0 != a0 and a0.startswith(b0)            # advanced adds the named mechanic
    assert "Structurally" not in b0                  # none/basic: no jargon
    assert "Structurally" in a0


def test_template_teaches_hook_by_detected_pattern():
    q = dict(SCRIPT, hook="Why does nobody warn you about day 12?")
    out = tutorial._mock_tutorial(q, "basic")
    assert "question" in out["steps"][0]["explanation"] or \
        out["steps"][0]["explanation"] == tutorial._HOOK_TEACH["question"]


def test_template_payoff_falls_back_to_last_body_line():
    s = {"hook": "The hook line.", "body": "First beat.\nThe closing beat.", "cta": ""}
    out = tutorial._mock_tutorial(s, "basic")
    assert out["steps"][-1]["title"] == "Payoff"
    assert out["steps"][-1]["highlight_text"] == "The closing beat."
    assert out["steps"][-1]["highlight_text"] in tutorial._script_text(s)


# --- empty-field robustness -----------------------------------------------------

def test_never_raises_on_empty_script_fields():
    for s in ({}, {"hook": "", "body": "", "cta": ""}, {"title": "only a title"}, None):
        out = _run(tutorial.pregen_tutorial(None, "c1", s))
        assert out["mode"] == "mock" and isinstance(out["steps"], list)
        text = tutorial._script_text(s)
        assert all(step["highlight_text"] in text for step in out["steps"])


def test_body_only_script_still_teaches_payoff():
    s = {"hook": "", "body": "Just one honest line.", "cta": ""}
    out = _run(tutorial.pregen_tutorial(None, "c1", s))
    assert out["steps"] and out["steps"][-1]["title"] == "Payoff"
    assert out["steps"][-1]["highlight_text"] == "Just one honest line."


# --- live path (monkeypatched LLM) ----------------------------------------------

def test_live_happy_path_validates_and_records(monkeypatch):
    _arm(monkeypatch)

    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        assert "steps" in schema["required"]
        assert SCRIPT["hook"] in user                # the script reached the prompt
        assert "briefing-colleague reasoning" in user  # reasoning flowed downstream
        return {"steps": [
            {"title": "Hook", "explanation": "why the open works",
             "highlight_text": SCRIPT["hook"]},
            {"title": "Tension", "explanation": "invented quote",
             "highlight_text": "not in the script at all"},
            {"title": "Payoff", "explanation": "why the close works",
             "highlight_text": "this gets weirder."},
        ]}
    monkeypatch.setattr(tutorial, "anthropic_cached_json", fake_json)

    store = FakeStore()
    out = _run(tutorial.pregen_tutorial(store, "c1", SCRIPT,
                                        reasoning="briefing-colleague reasoning"))
    assert out["mode"] == "live"
    assert [s["title"] for s in out["steps"]] == ["Hook", "Payoff"]  # mismatch dropped
    for s in out["steps"]:
        assert set(s) == {"title", "explanation", "highlight_text"}
        assert s["highlight_text"] in TEXT
    assert len(store.usage) == 1 and store.usage[0]["operation"] == "tutorial.pregen"


def test_live_all_mismatch_falls_back_to_template(monkeypatch):
    _arm(monkeypatch)

    async def fake_json(*a, **k):
        return {"steps": [{"title": "Hook", "explanation": "e",
                           "highlight_text": "hallucinated line"}]}
    monkeypatch.setattr(tutorial, "anthropic_cached_json", fake_json)

    out = _run(tutorial.pregen_tutorial(FakeStore(), "c1", SCRIPT))
    assert out["mode"] == "mock"                      # guard dropped everything
    assert out["steps"] and all(s["highlight_text"] in TEXT for s in out["steps"])


def test_live_keyless_returns_template_and_no_usage(monkeypatch):
    _arm(monkeypatch)

    async def fake_json(*a, **k):                     # keyless anthropic_cached_json -> None
        return None
    monkeypatch.setattr(tutorial, "anthropic_cached_json", fake_json)

    store = FakeStore()
    out = _run(tutorial.pregen_tutorial(store, "c1", SCRIPT))
    assert out["mode"] == "mock"
    assert store.usage == []                          # no phantom spend recorded


def test_live_exception_never_escapes(monkeypatch):
    _arm(monkeypatch)

    async def fake_json(*a, **k):
        raise RuntimeError("vendor down")
    monkeypatch.setattr(tutorial, "anthropic_cached_json", fake_json)

    out = _run(tutorial.pregen_tutorial(FakeStore(), "c1", SCRIPT))
    assert out["mode"] == "mock" and out["steps"]
