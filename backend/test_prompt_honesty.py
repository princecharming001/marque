"""Honesty + talking-head contract for every writer prompt and shipped template (2026-09-23).

The creator reads every script on camera, alone, in one take. A prompt that asks for
"receipts (a 90-day log, an exact number)", a demo, "point the camera at the part", a
faceless voiceover or before/after footage can only be satisfied by lying on camera or by
filming something else. Examples are the strongest teacher: one few-shot line with an
invented first-person event teaches invention whatever the rules around it say.

These tests render each writer with a THIN profile (niche, audience, goal, nothing else:
exactly where writers used to invent) and scan what the model is TAUGHT. The canonical
prohibition blocks (GROUNDING / TALKING-HEAD / SHOTPLAN and palo's local copies) name the
banned things in order to forbid them, so they're stripped before the scan and asserted
PRESENT where required, so the strip can never hide a missing rule. Keyless and pure.
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

import prompts
from app import channel_identity as ci
from app import ideas, palo_flags
from app import palo_prompts as pp

THIN = {"niche": "Fitness", "audience": "Busy professionals", "goal": "Get clients"}

BANNED = re.compile(
    r"receipt|90-day|exact number|faceless|point the camera|I tracked|my client|"
    r"before/after footage|screen record", re.I)
DASH = re.compile("[—–]")              # em dash, en dash

PROHIBITIONS = (
    prompts.GROUNDING_BLOCK, prompts.TALKING_HEAD_MANDATE, prompts.SHOTPLAN_RULE,
    pp._GROUNDING_RULES, pp._TALKING_HEAD_RULES, pp._TALKING_HEAD_IDEAS, pp._IDEA_GROUNDING,
)

SCRIPT = {"title": "you're eating protein wrong", "summary": "A myth-buster on protein timing.",
          "hook": "Most people save their protein for dinner. That's the mistake.",
          "body": "Your body can't bank protein for later.\n\nSpread it across the day.",
          "cta": "Add protein to breakfast tomorrow.", "style": "talking_head",
          "formatId": "myth-buster",
          "shotPlan": ["hold on face for the hook", "punch in on 'spread it across the day'"]}
PILLAR = {"name": "Protein myths", "summary": "Busting protein myths", "angle": "mechanism first",
          "exampleTopics": ["protein at breakfast"]}
REEL = {"creator_handle": "someone", "platform": "tiktok", "title": "i ate 200g of protein",
        "hook_text": "I ate 200g of protein a day", "transcript": "t", "why_trending": "w"}
SLOT = {"formatId": "myth-buster", "topic": "why protein at dinner only is a mistake",
        "angle": "spacing beats totals"}


def _run(coro):
    return asyncio.run(coro)


def _teaching(text: str) -> str:
    for block in PROHIBITIONS:
        text = text.replace(block, "")
    return text


def _assert_clean(name: str, text: str) -> None:
    taught = _teaching(text)
    m = BANNED.search(taught)
    assert not m, (f"{name} teaches a banned pattern {m.group(0)!r}: "
                   f"...{taught[max(0, m.start() - 80):m.end() + 40]}...")
    d = DASH.search(text)
    assert not d, f"{name} carries an em/en dash: ...{text[max(0, d.start() - 60):d.end() + 20]}..."


def _writers() -> dict[str, str]:
    """Every writer prompt touched by the honesty pass, rendered for a thin profile."""
    flagged = [{"pos": 0, "script": SCRIPT, "slot": SLOT,
                "verdict": {"weakest": "specificity", "note": "add the mechanism"}}]
    signals, identity, _t, _f = ideas._context_from_brand(THIN)
    out = {
        "scripts_prompt": prompts.scripts_prompt(THIN, PILLAR, "talking_head", 2),
        "scripts_prompt.slots": prompts.scripts_prompt(THIN, PILLAR, "talking_head", 1, slots=[SLOT]),
        "hooks_prompt": prompts.hooks_prompt(THIN, "protein timing"),
        "steer_prompt": prompts.steer_prompt(THIN, SCRIPT, "make it shorter"),
        "mimic_prompt": prompts.mimic_prompt(REEL, THIN),
        "script_revise_prompt": prompts.script_revise_prompt(THIN, "talking_head", flagged),
        "social_caption.instagram": prompts.social_caption_prompt(
            SCRIPT["hook"], SCRIPT["body"], SCRIPT["cta"], THIN["niche"], THIN["audience"]),
        "social_caption.tiktok": prompts.social_caption_prompt(
            SCRIPT["hook"], SCRIPT["body"], SCRIPT["cta"], THIN["niche"], THIN["audience"], "tiktok"),
        "next_idea_prompt": prompts.next_idea_prompt(THIN["niche"], None),
        "analyze_video_prompt": prompts.analyze_video_prompt("https://x/v", "transcript", THIN),
        "pillar_judge_prompt": prompts.pillar_judge_prompt(THIN["niche"], [PILLAR]),
        "pillar_judge_prompt.brand": prompts.pillar_judge_prompt(THIN["niche"], [PILLAR], brand=THIN),
        # exactly how ideas.generate_ideas renders it on a cold start
        "idea_generation_prompt": pp.idea_generation_prompt(
            signals, identity, structural_patterns=prompts.niche_prior_block(THIN["niche"])),
        "idea_generation_prompt.fallbacks": pp.idea_generation_prompt(signals, identity),
        "script_from_brief_prompt": pp.script_from_brief_prompt(
            {"title": "why protein at dinner backfires", "beginning": "b", "middle": "m",
             "ending": "e"}, THIN),
        "write_agent_prompt": pp.write_agent_prompt("", "write me a script about protein", brand=THIN),
    }
    return {k: f"{s}\n{u}" for k, (s, u) in out.items()}


# --- the scan -----------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(_writers()))
def test_writer_prompt_teaches_no_invention_and_no_dash(name):
    _assert_clean(name, _writers()[name])


@pytest.mark.parametrize("slug", sorted(prompts.NICHE_PRIORS))
def test_every_niche_prior_is_honest_and_filmable(slug):
    p = prompts.NICHE_PRIORS[slug]
    _assert_clean(f"niche_prior_block[{slug}]", prompts.niche_prior_block(slug))
    for word in ("demo", "log,", "play it", "on screen", "transformation", "before-after",
                 "before/after", "voiceover"):
        assert word not in p["note"].lower(), f"{slug} note asks for {word!r}"
    # talking-head-filmable arms only (the data SHAPE is unchanged; consumers read these keys)
    assert set(p["formats"]) <= set(prompts.TREND_FORMAT_IDS), slug
    assert set(p["styles"]) <= set(prompts.ACTIVE_STYLES), slug
    assert p["signals"] and set(p["signals"]) <= set(prompts.SIGNAL_LIST), slug
    # the first clause stands alone as mock_next_idea's structure beat
    beat = prompts.mock_next_idea(slug, None)["beats"][1]
    assert ".." not in beat and beat.endswith("."), beat


def test_shipped_templates_are_honest():
    for niche in ("Fitness", "Personal finance", "AI tools", "", "chess"):
        brand = {**THIN, "niche": niche}
        for idea in ideas.mock_ideas(brand):
            _assert_clean(f"mock_ideas[{niche}]", json.dumps(idea, ensure_ascii=False))
            text = f"{idea['title']} {idea['content']}".lower()
            for claim in ("i tried", "i tested", "what you tested", "taught me", "hours of",
                          "for a week", "day 7"):
                assert claim not in text, f"mock idea claims an experiment: {idea}"
        assert all("talking to camera" in i["content"] for i in ideas.mock_ideas(brand))
    assert ideas.mock_ideas(THIN)[0]["title"] == "the fitness advice most people get backwards"
    assert "AI tools" in ideas.mock_ideas({"niche": "AI tools"})[0]["title"]      # casing kept

    insight = {"value": "contrarian", "dimension": "hook_signal", "label": "contrarian hooks",
               "n": 6, "confidence": "early_read"}
    for idea in (prompts.mock_next_idea("Fitness", None),
                 prompts.mock_next_idea("Fitness", None, pillar="protein myths"),
                 prompts.mock_next_idea("Fitness", insight)):
        _assert_clean("mock_next_idea", json.dumps(idea, ensure_ascii=False))
        blob = json.dumps(idea).lower()
        assert "demo" not in blob and "provable" not in blob
    for signal, hook in prompts._SIGNAL_HOOK_TEMPLATES.items():
        _assert_clean(f"_SIGNAL_HOOK_TEMPLATES[{signal}]", hook)
        for tell in ("i've done this", "i realized", "exact numbers", "long enough"):
            assert tell not in hook.lower(), f"{signal}: {hook}"


def test_fallback_identity_claims_nothing():
    for brand in (THIN, {}, {"niche": "Personal finance", "voice": {"funnyToSerious": 0.9}}):
        doc = ci._fallback_identity(brand)
        _assert_clean("_fallback_identity", json.dumps(doc, ensure_ascii=False))
        for anchor in doc["voice_anchors"]:
            assert "every day" not in anchor and not anchor.startswith("I do "), anchor
            assert ":" not in anchor, anchor                    # no colon constructions
    doc = ci._fallback_identity(THIN)
    assert doc["voice_anchors"][0] == "Here's the fitness advice I'd actually give a friend."


# --- the rules each writer must carry -------------------------------------------

def test_feed_contract_rides_every_script_writer():
    w = _writers()
    for name in ("scripts_prompt", "scripts_prompt.slots", "steer_prompt", "script_revise_prompt",
                 "mimic_prompt", "analyze_video_prompt"):
        text = w[name]
        assert prompts.TALKING_HEAD_MANDATE in text, name
        assert prompts.SHOTPLAN_RULE in text, name
        assert prompts.GROUNDING_BLOCK in text, name
        assert prompts.VOICE_DOCTRINE in text, name
        assert "90 to 140 words" in text and "35 to 55 seconds" in text, name
    for name in ("scripts_prompt", "scripts_prompt.slots", "script_revise_prompt", "mimic_prompt",
                 "analyze_video_prompt"):
        assert prompts.LENGTH_BUDGET in w[name], name
    for name in ("steer_prompt", "script_revise_prompt", "mimic_prompt", "scripts_prompt"):
        assert prompts.TITLE_DOCTRINE in w[name], name
    for name in ("write_agent_prompt", "script_from_brief_prompt"):
        text = w[name]
        for block in (pp._TALKING_HEAD_RULES, pp._GROUNDING_RULES, pp._LENGTH_RULE, pp._VOICE_RULES):
            assert block in text, name
        assert "TALKING-HEAD ONLY" in text and "90 to 140 words" in text, name
    for name in ("idea_generation_prompt", "idea_generation_prompt.fallbacks"):
        assert pp._TALKING_HEAD_IDEAS in w[name] and pp._IDEA_GROUNDING in w[name], name
    assert prompts.TALKING_HEAD_MANDATE in w["next_idea_prompt"]


def test_palo_local_copies_stay_in_step_with_prompts():
    # palo_prompts can't import prompts at load time (circular), so it keeps copies.
    assert pp._LENGTH_RULE == prompts.LENGTH_BUDGET
    for clause in ("GROUNDING (do not put words in the creator's mouth, they film this themselves):",
                   "NEVER invent personal history, credentials, client stories, testimonials, "
                   "experiments they ran, or specific numbers",
                   "First person is FINE for opinions, habits and methods",
                   "Stories default to",
                   "the mechanism (why it works)",
                   "AT MOST ONE bracketed fill-in per script",
                   "[your result]"):
        assert clause in prompts.GROUNDING_BLOCK, clause
        assert clause in pp._GROUNDING_RULES, clause
    assert "TALKING-HEAD ONLY" in pp._TALKING_HEAD_RULES
    assert "TALKING-HEAD ONLY" in prompts.TALKING_HEAD_MANDATE


def test_steer_keeps_title_and_shotplan_unless_asked():
    sysp, user = prompts.steer_prompt(THIN, SCRIPT, "make it shorter")
    assert "copy the current title, summary, formatId and shotPlan through UNCHANGED" in sysp
    assert "Never ADD a new personal event" in sysp
    # real-model spot check (2026-09-23): "make it more personal" invented "I used to do this
    # too" / "when I moved out" until the rule said what personal means
    assert "asks for it to feel more personal" in sysp and "Never invent a memory" in sysp
    assert "'I used to do this too'" in prompts.GROUNDING_BLOCK
    # ...and a from-brief CTA promised "Comment PROTEIN and I'll send it": no promises
    for block in (prompts.GROUNDING_BLOCK, pp._GROUNDING_RULES):
        assert "Never promise the viewer something the creator would have to deliver" in block
    # the model can only pass them through if it can see them
    assert "- title: you're eating protein wrong" in user
    assert "- shotPlan: " in user and "punch in on 'spread it across the day'" in user
    # memory is optional and additive
    _s2, user2 = prompts.steer_prompt(THIN, SCRIPT, "shorter", memory={"facts": ["Coaches nurses"]})
    assert "Coaches nurses" in user2 and "CREATOR MEMORY" not in user


def test_revise_keeps_topic_and_never_asks_for_invention():
    flagged = [{"pos": 0, "script": SCRIPT, "verdict": {"weakest": "specificity", "note": "n"}}]
    sysp, user = prompts.script_revise_prompt(THIN, "talking_head", flagged, slots=[SLOT])
    assert "sharper and more specific, not safer" not in sysp
    assert "never by inventing" in sysp
    assert f"Assigned topic (keep it): {SLOT['topic']}" in user          # slots= by pos
    _s, user_b = prompts.script_revise_prompt(THIN, "talking_head", [{**flagged[0], "slot": SLOT}])
    assert f"Assigned topic (keep it): {SLOT['topic']}" in user_b        # slot on the item
    _s, user_c = prompts.script_revise_prompt(THIN, "talking_head", flagged)
    assert "Assigned topic" not in user_c                                # absent -> nothing
    _s, user_m = prompts.script_revise_prompt(THIN, "talking_head", flagged,
                                              memory={"facts": ["Ran a gym for ten years"]})
    assert "Ran a gym for ten years" in user_m


def test_mimic_budget_replaces_match_the_originals_length():
    sysp, _ = prompts.mimic_prompt(REEL, THIN)
    assert "Match the original's energy and length" not in sysp
    assert "not its length" in sysp and prompts.LENGTH_BUDGET in sysp
    assert "tracked every dollar" not in sysp.lower()
    # it returned formatId "talking-head" when the allowed ids weren't named
    assert "formatId is one of: " + ", ".join(prompts.TREND_FORMAT_IDS) in sysp


def test_brief_is_a_pitch_and_creator_context_is_optional():
    sysp, user = pp.script_from_brief_prompt({"title": "t", "beginning": "b"}, THIN)
    assert "THE BRIEF IS A PITCH, NOT FACTS" in sysp
    assert "Real names, real details from the brief" not in sysp
    assert "day 7" not in sysp.lower()
    block = "<creator>\nWho this creator is"
    assert block in user and "- audience: Busy professionals" in user
    _s, bare = pp.script_from_brief_prompt({"title": "t", "beginning": "b"})
    assert block not in bare                                             # no brand, no block
    wsys, wuser = pp.write_agent_prompt("x", "y", brand=THIN)
    assert block in wsys and block not in wuser                          # context, not editable text
    wsys2, _ = pp.write_agent_prompt("x", "y")
    assert block not in wsys2 and "{CREATOR}" not in wsys2


def test_idea_prompt_examples_are_honest():
    sysp = _writers()["idea_generation_prompt"]
    for old in ("The One Question That Made My Biggest Client Double His Budget",
                "my neighbor pressure washed my driveway", "the client who fired me on a Tuesday",
                "show whether it worked", "first person when the creator is on camera",
                "challenge-with-stakes"):
        assert old not in sysp, old
    assert "Specific never means an invented personal result" in sysp


def test_exemplars_no_longer_model_a_first_person_event():
    # CRAFT_EXAMPLES "RIGHT" script: the viewer's story, not the creator's, and on budget.
    right = prompts.CRAFT_EXAMPLES_BLOCK.split("A WRONG script")[0]
    spoken = re.findall(r'(?:hook|body|cta): "(.*?)"\n', right, re.S)
    assert len(spoken) == 3
    words = sum(len(s.replace("\\n", " ").split()) for s in spoken)
    assert 90 <= words <= 140, words
    assert not re.search(r"\b(?:I|my|me|I'm|I've|I'd)\b", " ".join(spoken)), spoken
    # ...and the WRONG script now names the invented event as the worst failure
    assert "invented personal event" in prompts.CRAFT_EXAMPLES_BLOCK
    assert "fired me" not in prompts.TITLE_DOCTRINE
    assert "I see most" not in prompts.hooks_prompt(THIN, "protein")[0]
    assert "two million likes" not in prompts.STYLES["green_screen"]["exemplar"]
    assert "caption card, prop" not in prompts.VIRALITY_BLOCK
    assert "EXPLAIN THE PROP" not in prompts.CRAFT_RULES_BLOCK


def test_social_caption_promises_and_claims_nothing():
    sysp = prompts.SOCIAL_CAPTION_SYSTEM
    for gone in ("Comment WORD", "I'll send you X", "first person, specific numbers",
                 "named outcomes"):
        assert gone not in sysp, gone
    assert "HONESTY (hard rule)" in sysp and "Say only what the video itself says" in sysp
    assert "comment-trade" not in prompts.social_caption_prompt("h", "b", "c", "n", "a", "tiktok")[1]


def test_judges_stop_pushing_thin_profiles_to_invent():
    sysp, _ = prompts.script_judge_prompt([SCRIPT], "talking_head", brand=THIN)
    assert "THIN PROFILE CALIBRATION" in sysp
    assert "must never ask for personal experience" in sysp
    assert not DASH.search(sysp)
    # the old KEEP anchor was an invented first-person event; it now calibrates FABRICATED
    anchors = sysp.split("ANCHORED EXAMPLES")[1].split("SHORT-FORM MASTERY")[0]
    for line in anchors.splitlines():
        if "I fired my biggest client" in line:
            assert "fabricated=true" in line and "verdict revise" in line, line
        if "verdict keep" in line:
            assert not re.search(r"\bI\b", line), line
    # pillar judge: a fabrication axis, with the creator context when given
    psys, puser = prompts.pillar_judge_prompt("Fitness", [PILLAR], brand=THIN)
    assert "HONESTY" in psys and "invented client story" in psys
    assert "CREATOR CONTEXT (the ONLY things true" in puser and "Busy professionals" in puser
    _p, bare = prompts.pillar_judge_prompt("Fitness", [PILLAR])
    assert "none beyond the niche" in bare
    # idea judge: the grounding axis says what 0/1/2 mean
    jsys, _ = pp.idea_judge_prompt({"title": "t", "content": "c"})
    assert "0 = invents a personal event" in jsys


# --- idea judge: score from the axes, promote only when fully grounded ----------

class _Store:
    def __init__(self):
        self.upserts: list = []

    async def load_prompt_override(self, key):
        return None

    async def upsert_briefs(self, briefs):
        self.upserts.extend(briefs)
        return True

    async def load_briefs(self, creator_id, status="", limit=30):
        return []

    async def record_ai_usage(self, row):
        return True


def _suggest(monkeypatch, judge):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "IDEA_BANK", True)

    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        req = schema.get("required", [])
        if "ideas" in req:
            return {"ideas": [{"title": f"protein idea {i}", "content": "specific"} for i in range(3)]}
        if "results" in req:
            return {"results": [{"idea_index": i, "pass": True} for i in (1, 2, 3)]}
        return judge
    monkeypatch.setattr(ideas, "anthropic_cached_json", fake_json)
    return _run(ideas.suggest_ideas(_Store(), "c1", THIN))


def test_judge_score_is_the_axis_sum_not_the_models_claim(monkeypatch):
    briefs = _suggest(monkeypatch, {"specificity": 3, "non_obvious": 2, "evidence_grounded": 2,
                                    "actionable": 1, "score": 10, "notes": "self-inflated"})
    assert all(b["score"] == 0.8 for b in briefs)          # 3+2+2+1, never the claimed 10
    assert all(b["promoted"] for b in briefs)              # 8 >= threshold AND grounded


def test_ungrounded_idea_is_never_promoted(monkeypatch):
    briefs = _suggest(monkeypatch, {"specificity": 3, "non_obvious": 3, "evidence_grounded": 1,
                                    "actionable": 2, "score": 9, "notes": "leans on a track record"})
    assert all(b["score"] == 0.9 for b in briefs)
    assert not any(b.get("promoted") for b in briefs)


def test_missing_axis_keeps_positional_score_and_never_promotes(monkeypatch):
    briefs = _suggest(monkeypatch, {"specificity": 3, "non_obvious": 3, "actionable": 2,
                                    "score": 10, "notes": "no grounding axis"})
    assert sorted(b["score"] for b in briefs) == [0.8, 0.9, 1.0]     # to_briefs positional
    assert not any(b.get("promoted") for b in briefs)


def test_judge_verdict_clamps_and_rejects_garbage():
    v = ideas._judge_verdict({"specificity": 7, "non_obvious": -2, "evidence_grounded": 5,
                              "actionable": 2.0})
    assert v == {"score": 7.0, "grounded": True,
                 "axes": {"specificity": 3, "non_obvious": 0, "evidence_grounded": 2, "actionable": 2}}
    assert ideas._judge_verdict(None) is None
    assert ideas._judge_verdict({"specificity": 3, "non_obvious": 3, "evidence_grounded": True,
                                 "actionable": 2}) is None                   # bool is not a score
    assert ideas._judge_verdict({"specificity": "3", "non_obvious": 3, "evidence_grounded": 2,
                                 "actionable": 2}) is None
    # judge_ideas keeps its float contract: axis sums, -1.0 sentinel when unjudged
    assert _run(ideas.judge_ideas(None, ideas.mock_ideas(THIN), THIN)) == [-1.0, -1.0, -1.0]
