"""Scripts v2, phase-1 slice (2026-10-19): the four cheap changes an owner can feel.

Each test fails on the pre-slice code:
  (a) the voiceprint read `caption or transcript` and 120 characters of it;
  (b) slots had no payoff / register / opener frame, so a page varied by formatId only;
  (c) the first paint pinned every creator to "about 115 words, exactly THREE paragraphs";
  (d) nothing told the writer which first lines the niche's feed already carries;
  (e) no deterministic AI-tell gate ran on the full path.
"""
from __future__ import annotations

import asyncio

import pytest

import main
import prompts
from app.scripts_v2 import lint as slint


# --- (a) voice exemplars ------------------------------------------------------------

def test_voice_exemplars_prefer_spoken_transcript_and_send_two_sentences():
    posts = [{"caption": "Link in bio!! #fitness #gym #grind. New program out now.",
              "transcript": "Okay so I stalled on bench for four months. Same weight every week, "
                            "and I was doing everything right. Turns out I was adding five pounds "
                            "whenever I felt good. Which was never.",
              "likes": 900, "comments": 40}]
    block = prompts._voice_exemplars(posts)
    assert "Okay so I stalled on bench for four months. Same weight every week," in block
    assert "Link in bio" not in block
    assert "(caption)" not in block
    assert "then:" in block                      # a cadence sample from the middle


def test_voice_exemplars_fall_back_to_caption_and_say_so():
    posts = [{"caption": "Stop doing cardio to lose fat. Lift three times a week and walk. That's it.",
              "transcript": "", "likes": 5}]
    block = prompts._voice_exemplars(posts)
    assert "opener (caption):" in block
    assert "Stop doing cardio to lose fat. Lift three times a week and walk." in block


# --- (b) planner contract ------------------------------------------------------------

def test_slot_plan_json_requires_payoff_register_and_frame():
    req = prompts.SLOT_PLAN_JSON["properties"]["slots"]["items"]["required"]
    assert {"payoff", "register", "opener_frame"} <= set(req)
    props = prompts.SLOT_PLAN_JSON["properties"]["slots"]["items"]["properties"]
    assert set(props["register"]["enum"]) == set(prompts.SLOT_REGISTERS)
    assert set(props["opener_frame"]["enum"]) == set(prompts.OPENER_FRAMES)


def test_slot_plan_prompt_carries_rules_and_recent_openers():
    sys, usr = prompts.slot_plan_prompt({"niche": "Fitness"}, {"name": "x"}, ["myth-buster", "listicle"],
                                        avoid_openers=["Your back hurts because of where you sit.",
                                                       "Sitting up straight isn't fixing your back."])
    assert "PAYOFF FIRST" in sys and "register" in sys and "opener_frame" in sys
    assert "RECENT OPENING LINES" in usr
    assert "Your back hurts because of where you sit." in usr


def test_slot_line_renders_payoff_register_and_frame():
    line = prompts._slot_line(0, {"formatId": "listicle", "topic": "t", "angle": "a",
                                  "payoff": "Buy the little plates.", "register": "confession",
                                  "opener_frame": "number-first"})
    assert "PAYOFF (the last line says this): Buy the little plates." in line
    assert "REGISTER: confession" in line and "OPENER FRAME: number-first" in line
    # Old-shape slots (no v2 fields) render exactly as before.
    plain = prompts._slot_line(1, {"formatId": "listicle", "topic": "t", "angle": "a"})
    assert "PAYOFF" not in plain and "REGISTER" not in plain


def test_scripts_prompt_includes_niche_openers_block():
    brand = {"niche": "Fitness", "voice": {}}
    sys, _ = prompts.scripts_prompt(brand, {"name": "p"}, "talking_head", 1,
                                    avoid_openers=["Stop stretching before you lift.",
                                                   "Cardio is not the problem.",
                                                   "Your desk is wrecking your back."])
    assert "OPENING LINES ALREADY IN THIS NICHE'S FEED" in sys
    assert "Cardio is not the problem." in sys


def _sreq(**kw) -> "main.ScriptRequest":
    base = {"niche": "Fitness", "audience": "Busy professionals", "goal": "Get clients",
            "pillar": "Myth-bust the common advice", "style": "talking_head", "count": 3,
            "creator_id": "c-slice"}
    base.update(kw)
    return main.ScriptRequest(**base)


def test_fallback_slots_are_distinct_in_register_and_frame():
    slots = main._fallback_slots(_sreq(), ["myth-buster", "listicle", "pov-story"], cursor=2)
    assert len({s["register"] for s in slots}) == 3
    assert len({s["opener_frame"] for s in slots}) == 3
    assert all("payoff" in s for s in slots)
    # A different cursor leads with a different register/frame (pages don't repeat).
    other = main._fallback_slots(_sreq(), ["myth-buster", "listicle", "pov-story"], cursor=3)
    assert other[0]["opener_frame"] != slots[0]["opener_frame"]


def test_plan_slots_keeps_payoff_and_enforces_distinct_register_and_frame(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "_account_context", lambda cid: asyncio.sleep(0, result=""))
    captured = {}

    async def fake_json(sys, usr, schema, model, max_tokens=3000, **kw):
        captured["usr"] = usr
        # The model collapses the page: three flat-claim rants.
        return {"slots": [
            {"slot": 1, "formatId": "myth-buster", "subarea": "training", "topic": "t1",
             "angle": "a1", "payoff": "Buy the little plates.", "register": "rant",
             "opener_frame": "flat-claim"},
            {"slot": 2, "formatId": "listicle", "subarea": "food", "topic": "t2",
             "angle": "a2", "payoff": "Eat before you lift.", "register": "rant",
             "opener_frame": "flat-claim"},
            {"slot": 3, "formatId": "pov-story", "subarea": "sleep", "topic": "t3",
             "angle": "a3", "payoff": "Go to bed.", "register": "not-a-register",
             "opener_frame": "flat-claim"},
        ]}
    monkeypatch.setattr(main, "anthropic_json", fake_json)
    slots = asyncio.run(main._plan_slots(_sreq(), 0, "", avoid_openers=["Your back hurts because of where you sit."]))
    assert [s["payoff"] for s in slots] == ["Buy the little plates.", "Eat before you lift.", "Go to bed."]
    assert slots[0]["register"] == "rant" and slots[0]["opener_frame"] == "flat-claim"   # first keeps its ask
    assert len({s["register"] for s in slots}) == 3
    assert len({s["opener_frame"] for s in slots}) == 3
    assert all(s["register"] in prompts.SLOT_REGISTERS for s in slots)
    assert all(s["opener_frame"] in prompts.OPENER_FRAMES for s in slots)
    assert "Your back hurts because of where you sit." in captured["usr"]


# --- (c) first-paint length from the creator's own reels -----------------------------

def test_length_target_from_own_transcripts_and_default():
    posts = [{"transcript": " ".join(["w"] * 60)}, {"transcript": " ".join(["w"] * 70)},
             {"transcript": " ".join(["w"] * 80)}, {"transcript": "too short"}]
    lt = main._length_target(posts)
    assert lt["lo"] == 60 and lt["hi"] == 84 and lt["cap"] == 104
    assert "3 of this creator's own reels" in lt["basis"]
    d = main._length_target([])
    assert (d["lo"], d["hi"], d["cap"]) == (90, 130, 150) and "default" in d["basis"]
    long = main._length_target([{"transcript": " ".join(["w"] * 240)}] * 3)
    assert long["hi"] == 170 and long["cap"] == 190           # clamped, never a two-minute ask


def test_fast_paint_suffix_no_longer_pins_three_paragraphs(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setattr(main, "_account_context", lambda cid: asyncio.sleep(0, result=""))
    monkeypatch.setattr(main, "_arms_for_prompt", lambda cid: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(main, "_inject_strategy", lambda q, cid: asyncio.sleep(0, result=""))
    seen = {}

    async def fake_write(sreq, slot, **kw):
        seen["suffix"] = kw.get("sys_suffix", "")
        seen["avoid"] = kw.get("avoid_openers")
        return {"hook": "Four months. Same bench.", "body": "I was adding weight on vibes. Now I write the number down first.",
                "cta": "Go buy the little plates.", "formatId": slot["formatId"]}

    async def fake_speakable(out, policy=""):
        return out

    monkeypatch.setattr(main, "_write_slot", fake_write)
    monkeypatch.setattr(main, "_ensure_speakable", fake_speakable)
    monkeypatch.setattr(main, "_ensure_honest", fake_speakable)
    posts = [{"transcript": " ".join(["w"] * 70), "caption": ""}, {"transcript": " ".join(["w"] * 72)}]
    sreq = _sreq(posts=posts, slots=[{"formatId": "listicle", "topic": "t", "angle": "a"}])
    out = asyncio.run(main._fast_feed_scripts(sreq))
    assert out["mode"] == "live_fast"
    assert "exactly THREE" not in seen["suffix"]
    assert "between 60 and 86 words" in seen["suffix"]      # median 72 words, ±20%, floor 60
    assert "measured from 2 of this creator's own reels" in seen["suffix"]


# --- (d) niche openers from the warmed reels cache -------------------------------------

def test_niche_openers_use_transcribed_reels_only(monkeypatch):
    key = main._niche_cache_key("Fitness")
    monkeypatch.setitem(main._niche_reels_cache, key, {"reels": [
        {"transcribed": True, "transcript": "Stop stretching before you lift. It loosens you.", "views": 50000},
        {"transcribed": True, "transcript": "Four months on the same bench weight. Here's the fix.", "views": 90000},
        {"transcribed": False, "transcript": "new program out now #fitness #gym", "views": 900000},
        {"transcribed": True, "transcript": "Your desk chair is the problem, not your back.", "views": 10000},
        {"transcribed": True, "transcript": "", "views": 99999},
    ]})
    got = main._niche_openers("Fitness")
    assert got == ["Four months on the same bench weight.",
                   "Stop stretching before you lift.",
                   "Your desk chair is the problem, not your back."]
    assert all("#fitness" not in o for o in got)


def test_niche_openers_need_three_lines_and_never_scrape(monkeypatch):
    key = main._niche_cache_key("Knitting")
    monkeypatch.setitem(main._niche_reels_cache, key, {"reels": [
        {"transcribed": True, "transcript": "Cast on loosely or regret it.", "views": 1}]})
    assert main._niche_openers("Knitting") == []
    assert main._niche_openers("Niche nobody warmed") == []
    assert main._niche_openers("") == []


def test_creator_recent_openers_prefer_transcript():
    posts = [{"caption": "Link in bio", "transcript": "Okay so I stalled on bench. For months."},
             {"caption": "Stop doing cardio to lose fat. Walk."}]
    assert main._creator_recent_openers(posts) == ["Okay so I stalled on bench.",
                                                   "Stop doing cardio to lose fat."]


def test_list_markers_are_soft_inside_a_listicle():
    body = ("Number one, you sit for nine hours. Get up every forty-five minutes.\n\n"
            "Number two, you're sleeping five hours. Seven, same bedtime.\n\n"
            "Number three, you skip rest days. Two a week.")
    as_list = slint.lint_script({"hook": "Three things wreck your recovery.", "body": body,
                                 "cta": "Pick one.", "formatId": "listicle"})
    as_story = slint.lint_script({"hook": "Three things wreck your recovery.", "body": body,
                                  "cta": "Pick one.", "formatId": "pov-story"})
    assert any(f.code == "list_markers" and f.severity == slint.SOFT for f in as_list)
    assert any(f.code == "list_markers" and f.severity == slint.HARD for f in as_story)


# --- (e) lint-triggered rewrite on the full path ----------------------------------------

ROBOTIC = {"hook": "Ever wondered why your progress has stalled despite your dedication?",
           "body": "Here's the thing: it's not about working harder, it's about working smarter. "
                   "First, prioritize progressive overload. Second, optimize your recovery. "
                   "Additionally, consistency is crucial.",
           "cta": "Remember, fitness is a journey. Follow for more tips like this!",
           "formatId": "listicle", "style": "talking_head", "predictedScore": 61}

CLEAN = {"hook": "I stalled on bench for four months.",
         "body": "One eighty-five, every week, same thing. I was adding five pounds whenever I felt good, "
                 "which was basically never.\n\nMy coach made me write the number down before I walked in. "
                 "Two and a half pounds, every session. Took six weeks to get to two-oh-five.",
         "cta": "Go buy the little plates.", "formatId": "listicle", "style": "talking_head"}


def test_lint_revise_replaces_a_robotic_draft_with_a_cleaner_rewrite(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    calls = {}

    async def fake_json(sys, usr, schema, model, max_tokens=3000, **kw):
        calls["usr"] = usr
        return [dict(CLEAN)]
    monkeypatch.setattr(main, "anthropic_json", fake_json)
    scripts = [dict(ROBOTIC), dict(CLEAN)]
    out = asyncio.run(main._lint_revise({"niche": "Fitness", "voice": {}}, "talking_head", scripts))
    assert out[0]["hook"] == CLEAN["hook"]
    assert out[0]["predictedScore"] == 61                     # the draft's calibrated score survives
    assert "opener_cliche" in calls["usr"] or "not_x_its_y" in calls["usr"]   # the tells were named
    assert out[1]["hook"] == CLEAN["hook"] and "lintCodes" in out[1]
    assert slint.summarize(slint.lint_script(out[0]))["hard"] == 0


def test_lint_revise_keeps_the_draft_when_the_rewrite_is_not_cleaner(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def still_bad(sys, usr, schema, model, max_tokens=3000, **kw):
        return [dict(ROBOTIC, hook="Here's the thing about your stalled progress.")]
    monkeypatch.setattr(main, "anthropic_json", still_bad)
    scripts = [dict(ROBOTIC)]
    out = asyncio.run(main._lint_revise({"niche": "Fitness", "voice": {}}, "talking_head", scripts))
    assert out[0]["hook"] == ROBOTIC["hook"]
    assert out[0]["lintCodes"]                                # stamped even when kept


def test_lint_revise_is_a_noop_for_clean_scripts_and_keyless(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def boom(*a, **k):
        raise AssertionError("no rewrite should be requested")
    monkeypatch.setattr(main, "anthropic_json", boom)
    out = asyncio.run(main._lint_revise({"niche": "Fitness"}, "talking_head", [dict(CLEAN)]))
    # Soft findings (rhythm, a tricolon) are stamped but never trigger a rewrite.
    assert slint.summarize(slint.lint_script(out[0]))["hard"] == 0
    assert out[0]["hook"] == CLEAN["hook"]
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    out = asyncio.run(main._lint_revise({"niche": "Fitness"}, "talking_head", [dict(ROBOTIC)]))
    assert out[0]["hook"] == ROBOTIC["hook"]


def test_lint_revise_survives_an_upstream_error(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def fail(*a, **k):
        raise main.HTTPException(status_code=502, detail="upstream")
    monkeypatch.setattr(main, "anthropic_json", fail)
    out = asyncio.run(main._lint_revise({"niche": "Fitness"}, "talking_head", [dict(ROBOTIC)]))
    assert out[0]["hook"] == ROBOTIC["hook"]
