"""R2 comms (morning brief + tone bible) — keyless deterministic tests.

Covers: empty artifacts ⇒ no LLM call + empty body; keyless ⇒ honest empty; happy
path quotes an exact provided title and passes code validation; fabricated-title
body discarded ⇒ empty; >300-char truncation at a sentence boundary; perf_phrase
banding (incl. below-average and never-a-raw-multiplier); PERF_TONE_RULES carries
the BAD/GOOD worked pair.
"""
from __future__ import annotations

import asyncio
import re

from app import comms, palo_flags

TITLE = "Sleeping in a Mall Overnight"

ARTS = {
    "promoted_ideas": [{"title": TITLE, "pitch": "A full concept grown from one you saved."}],
    "day_header": "Your firealarm video is still carrying the week.",
    "overnight_scripts": [{"title": "Egg Speedrun Script"}],
    "insights": [{"title": "Question openers keep fading"}],
}


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self):
        self.usage = []

    async def record_ai_usage(self, row):
        self.usage.append(row)
        return True


def _arm(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "MORNING_BRIEF", True)


def _fake_llm(monkeypatch, result, calls=None):
    async def fake(system, user, schema, model, max_tokens=300, temperature=None):
        if calls is not None:
            calls.append({"system": system, "user": user, "schema": schema, "model": model})
        return result
    monkeypatch.setattr(comms, "anthropic_cached_json", fake)


def _forbid_llm(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("LLM must not be called")
    monkeypatch.setattr(comms, "anthropic_cached_json", boom)


# --- gating + empty inputs ------------------------------------------------------

def test_flag_off_is_honest_empty(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", False)
    _forbid_llm(monkeypatch)                        # flag off must never reach the LLM
    assert _run(comms.morning_brief(FakeStore(), "c1", ARTS)) == \
        {"body": "", "mentioned": []}


def test_empty_artifacts_no_llm_call(monkeypatch):
    _arm(monkeypatch)
    _forbid_llm(monkeypatch)                        # would raise if reached
    for arts in ({}, None,
                 {"promoted_ideas": [], "overnight_scripts": [], "insights": []},
                 {"day_header": "steady day"},      # context alone is not content
                 {"promoted_ideas": [{"pitch": "no title"}]}):
        out = _run(comms.morning_brief(FakeStore(), "c1", arts))
        assert out == {"body": "", "mentioned": []}


def test_keyless_is_honest_empty(monkeypatch):
    _arm(monkeypatch)
    _fake_llm(monkeypatch, None)                    # anthropic_cached_json keyless contract
    store = FakeStore()
    out = _run(comms.morning_brief(store, "c1", ARTS))
    assert out == {"body": "", "mentioned": []}
    assert store.usage == []                        # no live call ⇒ no usage row


def test_model_empty_body_means_send_nothing(monkeypatch):
    _arm(monkeypatch)
    _fake_llm(monkeypatch, {"body": "  "})          # the model's own no-signal verdict
    out = _run(comms.morning_brief(FakeStore(), "c1", ARTS))
    assert out == {"body": "", "mentioned": []}


# --- happy path + validation ----------------------------------------------------

def test_happy_path_quotes_exact_title(monkeypatch):
    _arm(monkeypatch)
    calls = []
    body = (f'I built you a new idea overnight, "{TITLE}". '
            f'It grew out of one you saved. It is waiting in Yunicorn.')
    _fake_llm(monkeypatch, {"body": body}, calls)
    store = FakeStore()
    out = _run(comms.morning_brief(store, "c1", ARTS))
    assert out["body"] == body
    assert out["mentioned"] == [TITLE]
    # usage recorded after the live call
    assert len(store.usage) == 1 and store.usage[0]["operation"] == "comms.morning_brief"
    # explicit kind flags rode in the payload — never inferred from content
    payload = calls[0]["user"]
    assert '"kind": "idea"' in payload and '"kind": "script"' in payload \
        and '"kind": "insight"' in payload
    assert '"Egg Speedrun Script"' in payload and TITLE in payload


def test_fabricated_title_discarded(monkeypatch):
    _arm(monkeypatch)
    _fake_llm(monkeypatch, {"body": 'Tonight I made you "Totally Invented Title". It is waiting.'})
    out = _run(comms.morning_brief(FakeStore(), "c1", ARTS))
    assert out == {"body": "", "mentioned": []}     # never a hallucinated brief


def test_fabricated_title_anywhere_discards_whole_brief(monkeypatch):
    _arm(monkeypatch)                               # one real + one invented ⇒ still discarded
    _fake_llm(monkeypatch, {"body": f'"{TITLE}" is ready, and so is "Ghost Artifact".'})
    out = _run(comms.morning_brief(FakeStore(), "c1", ARTS))
    assert out == {"body": "", "mentioned": []}


def test_truncation_at_sentence_boundary(monkeypatch):
    _arm(monkeypatch)
    s1 = f'Your new idea "{TITLE}" is waiting for you in the app this morning.'
    s2 = "It grew out of a concept you saved a while back and never used at all."
    s3 = ("The rest of your channel held its usual pace overnight with the top "
          "videos still pulling steady views across the board, so this morning "
          "is a clean window to look the new concept over and decide.")
    body = f"{s1} {s2} {s3}"
    assert len(body) > 300 and len(f"{s1} {s2}") <= 300
    _fake_llm(monkeypatch, {"body": body})
    out = _run(comms.morning_brief(FakeStore(), "c1", ARTS))
    assert 0 < len(out["body"]) <= 300
    assert out["body"] == f"{s1} {s2}"              # cut exactly at a sentence boundary
    assert out["body"].endswith(".")
    assert out["mentioned"] == [TITLE]              # the quote survived the cut


# --- perf_phrase banding --------------------------------------------------------

def test_perf_phrase_bands():
    far = "performed far above your usual average"
    inline = "performed roughly in line with your usual"
    below = "performed well below your usual"
    assert comms.perf_phrase(5.0) == far
    assert comms.perf_phrase(2.0) == far            # breakout threshold inclusive
    assert comms.perf_phrase(1.9) == inline
    assert comms.perf_phrase(1.0) == inline
    assert comms.perf_phrase(0.61) == inline
    assert comms.perf_phrase(0.6) == below          # weak threshold inclusive
    assert comms.perf_phrase(0.2) == below          # below-average band
    assert comms.perf_phrase(0.0) == "too early to assess"
    assert comms.perf_phrase(-1.0) == "too early to assess"
    assert comms.perf_phrase(float("nan")) == "too early to assess"
    assert comms.perf_phrase("junk") == "too early to assess"


def test_perf_phrase_never_raw_multiplier():
    for m in (0.1, 0.44, 1.0, 2.3, 38.0):
        phrase = comms.perf_phrase(m)
        assert not re.search(r"\d", phrase)          # no digits, so no "2.3x" ever
        assert "x" != phrase[-1:]


# --- tone bible -----------------------------------------------------------------

def test_perf_tone_rules_carries_the_bible():
    rules = comms.PERF_TONE_RULES
    # the BAD/GOOD worked pair, verbatim
    assert "BAD example:" in rules and "GOOD example:" in rules
    assert "3.0x typical" in rules and "bayashi_tv" in rules
    assert "Making Ramen in 60 Seconds" in rules
    # never-raw-multipliers / jargon / hedging
    assert 'never write "2.3x"' in rules
    assert '"algorithmically alive"' in rules
    assert '"consider"' in rules and '"might want to"' in rules
    # plain-performance phrase map + narrative-first grounding
    assert "performed far above your usual average" in rules
    assert "narrative FIRST" in rules
    assert "based ONLY on what you wrote in the narrative" in rules
