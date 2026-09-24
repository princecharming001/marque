"""Honesty guard + shared finalize step (content-engine pass, 2026-09-23)."""
import asyncio

import pytest

import main
from app import honesty


@pytest.mark.parametrize("text,claim", [
    ("I tracked my lifts for 90 days and my deadlift went from 185 to 225.", True),
    ("We killed custom pricing and our revenue went up.", True),
    ("My client lost 20 pounds in a month.", True),
    ("We spent three months building a feature nobody used.", True),
    ("Here's what I'd do instead: cut the second session.", False),
    ("I think most people quit because the plan is too big.", False),
    ("You open the fridge at nine and there's nothing easy.", False),
    ("I always tell people to start with three sessions.", False),
    ("If I tried to train six days a week, I'd burn out.", False),
    ("Most people went from zero to burnout in a month.", False),
])
def test_first_person_claim_detector(text, claim):
    assert bool(honesty.flag_first_person_claim(text)) is claim


def _req(**kw):
    base = dict(niche="Startups", audience="Founders", creator_id="qa-honesty")
    base.update(kw)
    return main.ScriptRequest(**base)


def test_thin_profile_means_no_posts_and_no_told_facts():
    assert main._thin_profile(_req()) is True
    assert main._thin_profile(_req(memory={"ideas": ["x"]})) is True          # ideas aren't lived facts
    assert main._thin_profile(_req(memory={"facts": ["I run a 6-person team"]})) is False
    assert main._thin_profile(_req(posts=[{"caption": "real post"}])) is False


def _script(body):
    return {"title": "t", "hook": "Everyone says sell first.", "body": body,
            "cta": "Tell me what you'd cut.", "style": "talking_head", "formatId": "myth-buster"}


def test_honesty_guard_repairs_a_claim(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def repair(sys, usr, schema, model, max_tokens, array_key=None):
        return {"hook": "Everyone says sell first.",
                "body": "Most teams kill custom pricing too late, and the ones who cut it early see deals close faster.",
                "cta": "Tell me what you'd cut."}

    monkeypatch.setattr(main, "anthropic_json", repair)
    clean = _script("Pricing pages confuse buyers when every deal is custom.")
    bad = _script("We killed custom pricing and our revenue went up.")
    out = asyncio.run(main._ensure_honest([clean, bad]))
    assert len(out) == 2 and out[0] is clean
    assert "revenue went up" not in out[1]["body"] and out[1]["targetSeconds"] == main._est_seconds(out[1])


def test_honesty_guard_drops_what_it_cannot_fix(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def still_bad(*a, **k):
        return {"hook": "h", "body": "We killed custom pricing and our revenue went up.", "cta": "c"}

    monkeypatch.setattr(main, "anthropic_json", still_bad)
    out = asyncio.run(main._ensure_honest([_script("We killed custom pricing and our revenue went up.")]))
    assert out == []


def test_finalize_measures_seconds_and_tidies_title_style_format():
    s = main._finalize_script({"title": "why you should never train your muscles twice a week anymore at all",
                               "hook": "w " * 10, "body": "w " * 120, "cta": "w " * 8,
                               "targetSeconds": 30, "style": "faceless", "formatId": "broll-hook"})
    assert s["targetSeconds"] == s["durationSeconds"] == round(138 / 2.75)
    assert len(s["title"].split()) <= 8 and not s["title"].endswith((" a", " the", " twice a"))
    assert s["style"] == "talking_head" and s["formatId"] in main.prompts.STYLES["talking_head"]["formats"]


@pytest.mark.parametrize("title", ["you don't need to train muscles twice a week anymore",
                                   "the one thing every founder gets wrong about the"])
def test_display_clamp_never_ends_on_a_function_word(title):
    out = main._clamp_title(title, limit=42)
    assert out.split()[-1].lower() not in main._TITLE_TAIL_WORDS
