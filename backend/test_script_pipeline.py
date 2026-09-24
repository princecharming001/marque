"""Planned-page script pipeline (script-realism fix, 2026-09-23).

Covers the pieces the realism eval changed: per-slot parallel writes with a bounded
retry, no mock padding on a partial page, planner fallback, format rotation, and the
spoken-length estimate that replaced the hard-coded 30s. Keyless: every model call is
monkeypatched, so nothing here touches the network.
"""
import asyncio

import pytest
from fastapi import HTTPException

import main
import prompts
from app import palo_llm


def _sreq(**kw):
    base = dict(niche="Fitness", audience="Busy professionals", goal="Get clients",
                creator_id="qa-test-pipeline", count=3)
    base.update(kw)
    return main.ScriptRequest(**base)


def _script(topic: str, body: str | None = None) -> dict:
    return {"scripts": [{
        "title": f"about {topic}", "summary": "s", "hook": f"Here's the truth about {topic}.",
        "hookSignal": prompts.SIGNAL_LIST[0], "formatId": "listicle",
        "body": body if body is not None else (
            f"Most people get {topic} wrong because they copy what works for someone with a "
            "different schedule. If you sit at a desk all day, your hips are tight and your "
            "upper back is rounded, so the fix is boring: two short sessions that open those up "
            "before you load anything heavy."),
        "cta": "Try it this week and tell me how it went.", "style": "talking_head"}]}


SLOT = {"formatId": "myth-buster", "topic": "desk posture", "subarea": "mobility", "angle": "a"}


# --- _write_slot: bounded retry ------------------------------------------------

def test_write_slot_retries_a_fast_failure(monkeypatch):
    calls = []

    async def fake(system, user, schema, model, max_tokens=4000, temperature=None):
        calls.append(1)
        return None if len(calls) == 1 else _script("desk posture")   # 1st: content-filter 400

    monkeypatch.setattr(palo_llm, "anthropic_cached_json", fake)
    s = asyncio.run(main._write_slot(_sreq(), SLOT, model=prompts.HAIKU,
                                     schema=prompts.FAST_SCRIPT_JSON_ELEMENT, max_tokens=1100,
                                     sys_suffix="", stats=[], timeout=5.0, tag="t"))
    assert len(calls) == 2
    assert s and s["formatId"] == "myth-buster"      # the plan's format wins over the model's


def test_write_slot_never_retries_a_timeout(monkeypatch):
    calls = []

    async def slow(*a, **kw):
        calls.append(1)
        await asyncio.sleep(1.0)
        return _script("x")

    monkeypatch.setattr(palo_llm, "anthropic_cached_json", slow)
    s = asyncio.run(main._write_slot(_sreq(), SLOT, model=prompts.HAIKU,
                                     schema=prompts.FAST_SCRIPT_JSON_ELEMENT, max_tokens=1100,
                                     sys_suffix="", stats=[], timeout=0.2, tag="t"))
    assert s is None and len(calls) == 1


def test_write_slot_skips_retry_when_deadline_is_nearly_spent(monkeypatch):
    calls = []

    async def fail_late(*a, **kw):
        calls.append(1)
        await asyncio.sleep(0.2)
        return None

    monkeypatch.setattr(palo_llm, "anthropic_cached_json", fail_late)
    monkeypatch.setattr(main, "_SLOT_RETRY_MIN_S", 0.5)
    s = asyncio.run(main._write_slot(_sreq(), SLOT, model=prompts.HAIKU,
                                     schema=prompts.FAST_SCRIPT_JSON_ELEMENT, max_tokens=1100,
                                     sys_suffix="", stats=[], timeout=0.6, tag="t"))
    assert s is None and len(calls) == 1              # 0.4s left < 0.5s floor → no retry


def test_write_slot_drops_empty_body_after_retry(monkeypatch):
    async def empty(*a, **kw):
        return _script("x", body="   ")

    monkeypatch.setattr(palo_llm, "anthropic_cached_json", empty)
    s = asyncio.run(main._write_slot(_sreq(), SLOT, model=prompts.HAIKU,
                                     schema=prompts.FAST_SCRIPT_JSON_ELEMENT, max_tokens=1100,
                                     sys_suffix="", stats=[], timeout=5.0, tag="t"))
    assert s is None


# --- _fast_feed_scripts: planned + parallel, no mock padding -------------------

def _wire_fast(monkeypatch, slots, writer):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "test-key")

    async def plan(sreq, cursor=0, context="", avoid=None):
        return slots

    async def none_str(*a, **kw):
        return ""

    async def no_arms(*a, **kw):
        return []

    monkeypatch.setattr(main, "_plan_slots", plan)
    monkeypatch.setattr(main, "_account_context", none_str)
    monkeypatch.setattr(main, "_inject_strategy", none_str)
    monkeypatch.setattr(main, "_arms_for_prompt", no_arms)
    monkeypatch.setattr(palo_llm, "anthropic_cached_json", writer)


SLOTS3 = [{"formatId": f, "topic": t, "subarea": t, "angle": ""} for f, t in
          (("myth-buster", "cardio"), ("pov-story", "lunch breaks"), ("listicle", "grip strength"))]


def test_fast_feed_writes_one_script_per_planned_slot(monkeypatch):
    async def writer(system, user, schema, model, max_tokens=4000, temperature=None):
        topic = next(s["topic"] for s in SLOTS3 if s["topic"] in user)
        return _script(topic)

    _wire_fast(monkeypatch, SLOTS3, writer)
    res = asyncio.run(main._fast_feed_scripts(_sreq()))
    assert res["mode"] == "live_fast"
    assert [s["formatId"] for s in res["scripts"]] == ["myth-buster", "pov-story", "listicle"]
    for s in res["scripts"]:
        assert s["targetSeconds"] == main._est_seconds(s)       # measured, not a flat 30
        assert s["altHooks"] == [] and s["shotPlan"] == []


def test_fast_feed_drops_a_failed_slot_instead_of_padding_with_mock(monkeypatch):
    async def writer(system, user, schema, model, max_tokens=4000, temperature=None):
        if "lunch breaks" in user:
            return None                                          # fails both attempts
        topic = next(s["topic"] for s in SLOTS3 if s["topic"] in user)
        return _script(topic)

    _wire_fast(monkeypatch, SLOTS3, writer)
    res = asyncio.run(main._fast_feed_scripts(_sreq()))
    assert res["mode"] == "live_fast"
    assert len(res["scripts"]) == 2
    mock_titles = {m["title"] for m in main.mock_scripts(_sreq())}
    assert not mock_titles & {s["title"] for s in res["scripts"]}


def test_fast_feed_all_slots_failed_serves_mock(monkeypatch):
    async def writer(*a, **kw):
        return None

    _wire_fast(monkeypatch, SLOTS3, writer)
    res = asyncio.run(main._fast_feed_scripts(_sreq()))
    assert res["mode"] == "mock" and res["scripts"]


# --- planner -------------------------------------------------------------------

def test_plan_slots_keyless_falls_back_to_format_varied_pillar_slots(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    slots = asyncio.run(main._plan_slots(_sreq(pillar="Myth-bust the common advice"), cursor=0))
    assert [s["formatId"] for s in slots] == ["myth-buster", "pov-story", "listicle"]
    assert all(s["topic"] == "Myth-bust the common advice" for s in slots)


def test_plan_slots_uses_model_topics(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "test-key")

    async def planned(system, user, schema, model, max_tokens=4000):
        return {"slots": [{"slot": i, "formatId": "x", "subarea": f"area {i}",
                           "topic": f"topic {i}", "angle": f"angle {i}"} for i in range(3)]}

    monkeypatch.setattr(main, "anthropic_json", planned)
    slots = asyncio.run(main._plan_slots(_sreq(), cursor=1))
    assert [s["topic"] for s in slots] == ["topic 0", "topic 1", "topic 2"]
    # formats come from code (rotated by cursor), never from the model's "x"
    assert [s["formatId"] for s in slots] == ["pov-story", "listicle", "green-screen"]


@pytest.mark.parametrize("failure", ["http", "empty_topic"])
def test_plan_slots_failure_falls_back(monkeypatch, failure):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "test-key")

    async def bad(*a, **kw):
        if failure == "http":
            raise HTTPException(502, "upstream")
        return {"slots": [{"topic": ""}, {"topic": "b"}, {"topic": "c"}]}

    monkeypatch.setattr(main, "anthropic_json", bad)
    slots = asyncio.run(main._plan_slots(_sreq(pillar="P"), cursor=0))
    assert [s["topic"] for s in slots] == ["P", "P", "P"]


# --- small helpers ---------------------------------------------------------------

def test_page_formats_rotate_by_cursor():
    assert main._page_formats("talking_head", 0, 3) == ["myth-buster", "pov-story", "listicle"]
    assert main._page_formats("talking_head", 1, 3) == ["pov-story", "listicle", "green-screen"]
    assert main._page_formats("talking_head", 3, 2) == ["green-screen", "myth-buster"]


def test_est_seconds_counts_spoken_words_and_clamps():
    assert main._est_seconds({"hook": "a " * 10, "body": "b " * 100, "cta": "c " * 5}) == 42   # 165 wpm
    assert main._est_seconds({"body": "short"}) == 10
    assert main._est_seconds({"body": "w " * 1000}) == 120


def test_talking_head_rubric_asks_for_real_length():
    rubric = prompts.STYLES["talking_head"]["rubric"]
    assert "35 to 55 seconds" in rubric and "18 to 40" not in rubric


# --- voice doctrine backstops the prod smoke test found missing ----------------------

def test_pillar_prose_is_dash_free_on_every_path(monkeypatch):
    async def raw(brand, pillars, posts):
        return pillars
    monkeypatch.setattr(main, "_judge_and_fix_pillars_raw", raw)
    dashed = [{"name": "Desk strength", "summary": "Lift heavy — twice a week",
               "angle": "Consistency — not intensity", "weight": 0.3,
               "exampleTopics": ["Why 3 days beats 6 — for desk workers", 7]}]
    out = asyncio.run(main.judge_and_fix_pillars({"niche": "fitness"}, dashed, None))
    blob = str(out)
    assert "—" not in blob and "–" not in blob
    assert out[0]["weight"] == 0.3 and out[0]["exampleTopics"][1] == 7      # non-prose untouched


def test_no_hardcoded_dash_in_baseline_reason():
    import inspect
    assert "niche baseline —" not in inspect.getsource(main)


# --- planner topic memory -------------------------------------------------------------

def test_avoid_topics_puts_dislikes_first_then_recent_pitches(monkeypatch):
    cid = "c-topics"
    main._pitched_titles.pop(cid, None); main._disliked_titles.pop(cid, None)
    main._remember_titles(main._pitched_titles, cid, ["desk stretches", "posture myths", "grip"])
    main._remember_titles(main._disliked_titles, cid, ["posture myths"])
    assert main._avoid_topics(cid) == ["posture myths", "grip", "desk stretches"]
    main._remember_titles(main._pitched_titles, "default", ["pooled"])     # shared bucket: never
    assert "default" not in main._pitched_titles


def test_first_paint_plans_around_what_was_already_pitched(monkeypatch):
    cid = "qa-test-pipeline"
    main._pitched_titles.pop(cid, None)
    main._remember_titles(main._pitched_titles, cid, ["why desk workers skip leg day"])
    seen = {}

    async def plan(sreq, cursor=0, context="", avoid=None):
        seen["avoid"] = avoid
        return SLOTS3

    async def writer(system, user, schema, model, max_tokens=4000, temperature=None):
        return _script(next(s["topic"] for s in SLOTS3 if s["topic"] in user))

    _wire_fast(monkeypatch, SLOTS3, writer)
    monkeypatch.setattr(main, "_plan_slots", plan)
    asyncio.run(main._fast_feed_scripts(_sreq(creator_id=cid)))
    assert seen["avoid"] == ["why desk workers skip leg day"]
    main._pitched_titles.pop(cid, None)


# --- orb chat scripts ------------------------------------------------------------------

def test_chat_slots_keep_the_requested_topic_on_every_script():
    slots = main._chat_slots("3 mistakes with meal prep", "talking_head", 3)
    assert [s["topic"] for s in slots] == ["3 mistakes with meal prep"] * 3
    assert len({s["formatId"] for s in slots}) == 3 and slots[0]["formatId"] == "myth-buster"
    assert main._chat_slots("my morning routine story", "talking_head", 1)[0]["formatId"] == "pov-story"


def test_chat_scripts_write_the_topic_and_never_ship_template_copy(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    seen = {}

    async def fake_fast(sreq, cursor=0):
        seen["slots"] = sreq.slots
        return {"mode": "mock", "scripts": main.mock_scripts(sreq)}     # a real failure

    async def no_posts(cid):
        return []

    monkeypatch.setattr(main, "_fast_feed_scripts", fake_fast)
    monkeypatch.setattr(main, "_creator_posts", no_posts)
    req = main.ConverseRequest(creator_id="c-chat", brand={"niche": "Cooking"}, messages=[])
    out = asyncio.run(main._chain_scripts(req, {"topic": "meal prep", "count": 2}))
    assert out == [] and [s["topic"] for s in seen["slots"]] == ["meal prep", "meal prep"]
