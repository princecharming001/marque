"""Scripts v2 phase 2-4: flag, LLM helper, doctrine, material, writer, judge, pipeline,
angles, Hook Lab, beats, and the main.py wiring. Keyless + fake transports only."""
from __future__ import annotations

import asyncio
import json

import pytest

import main
import prompts
from app import llm_usage, palo_llm
from app.scripts_v2 import angles, beats, doctrine, flags, hooklab, judge, lint, llm, material, pipeline, writer


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("SCRIPTS_V2", "SCRIPTS_V2_ALLOWLIST", "SCRIPTS_V2_BEST_OF", "SCRIPTS_V2_WRITER_MODEL",
              "SCRIPTS_V2_FIRST_PAINT_MODEL", "BANDIT_OPENER_FRAME", "NICHE_TRANSCRIPT_PASS"):
        monkeypatch.delenv(k, raising=False)
    llm_usage.reset_totals()
    llm_usage.set_sink(None)
    yield


# --- flags ------------------------------------------------------------------------------

def test_flag_modes(monkeypatch):
    assert flags.mode() == "off" and not flags.enabled_for("c1")
    monkeypatch.setenv("SCRIPTS_V2", "allowlist")
    monkeypatch.setenv("SCRIPTS_V2_ALLOWLIST", "c1, c2")
    assert flags.enabled_for("c1") and not flags.enabled_for("c3") and not flags.enabled_for(None)
    monkeypatch.setenv("SCRIPTS_V2_ALLOWLIST", "*")
    assert flags.enabled_for("anyone")
    monkeypatch.setenv("SCRIPTS_V2", "on")
    assert flags.enabled_for("x")
    monkeypatch.setenv("SCRIPTS_V2", "bogus")
    assert flags.mode() == "off"
    assert flags.writer_model() == "claude-sonnet-5-5" and flags.first_paint_model() == "claude-haiku-5-5"
    monkeypatch.setenv("SCRIPTS_V2_BEST_OF", "2")
    assert flags.best_of() == 2
    monkeypatch.setenv("SCRIPTS_V2_BEST_OF", "x")
    assert flags.best_of() == 1


# --- llm.call -----------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self._p


class _Client:
    def __init__(self, responses):
        self.responses, self.sent = list(responses), []

    async def post(self, url, **kw):
        self.sent.append(kw)
        return self.responses.pop(0)


def _ok(obj, model="claude-sonnet-5-5", stop="end_turn"):
    return _Resp({"model": model, "stop_reason": stop,
                  "content": [{"type": "text", "text": json.dumps(obj) if not isinstance(obj, str) else obj}],
                  "usage": {"input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 50}})


def test_llm_call_keyless_returns_none(monkeypatch):
    monkeypatch.setattr(palo_llm, "_KEY", "")
    assert asyncio.run(llm.call(model="claude-haiku-5-5", system="s", messages=[])) == (None, {})


def test_llm_call_body_shape_retry_and_metering(monkeypatch):
    monkeypatch.setattr(palo_llm, "_KEY", "k")
    client = _Client([_Resp({}, 529), _ok({"a": 1})])
    monkeypatch.setattr(palo_llm, "_get_client", lambda: client)

    async def _nosleep(_):
        return None
    monkeypatch.setattr(llm.asyncio, "sleep", _nosleep)
    out, usage = asyncio.run(llm.call(model="claude-sonnet-5-5", system="doc",
                                      messages=[llm.text_turn("user", "hi")], schema={"type": "object"},
                                      effort="low", route="t.route"))
    assert out == {"a": 1} and usage["cache_read"] == 900
    body, headers = client.sent[-1]["json"], client.sent[-1]["headers"]
    assert body["output_config"]["effort"] == "low" and body["output_config"]["format"]["type"] == "json_schema"
    assert body["thinking"] == {"type": "between_tools"} and "temperature" not in body
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["fallbacks"] == "default" and "server-side-fallback" in headers["anthropic-beta"]
    assert llm_usage.totals()["t.route"]["calls"] == 1


def test_llm_call_refusal_and_bad_request_return_none(monkeypatch):
    monkeypatch.setattr(palo_llm, "_KEY", "k")
    client = _Client([_ok({"a": 1}, stop="refusal"), _Resp({"error": "x"}, 400)])
    monkeypatch.setattr(palo_llm, "_get_client", lambda: client)
    out, _ = asyncio.run(llm.call(model="claude-haiku-5-5", system="s", messages=[], schema={}))
    assert out is None
    out, _ = asyncio.run(llm.call(model="claude-haiku-5-5", system="s", messages=[], schema={}))
    assert out is None
    assert "fallbacks" not in client.sent[0]["json"]          # Haiku: no server-side fallback


# --- doctrine -----------------------------------------------------------------------------

def test_doctrine_is_short_plain_and_versioned():
    t = doctrine.text()
    assert doctrine.approx_tokens(t) <= doctrine.MAX_TOKENS
    assert "—" not in t and "–" not in t and "NEVER" not in t and "\n- " not in t
    assert doctrine.version() == doctrine.version() and doctrine.version().startswith("wd-")


# --- material -----------------------------------------------------------------------------

SAMPLES = [{"text": "Okay so I stalled on bench for four months at one eighty-five. My coach made me write "
                    "the number down before I walked in. Two and a half pounds every session.", "views": 9000},
           {"text": "You guys sit for nine hours and walk for six minutes. Set a timer for forty-five minutes "
                    "and walk to the far bathroom.", "views": 4000}]
BRAND = {"niche": "Fitness", "what_you_do": "I coach office workers to get strong in three short sessions a week",
         "known_for": "no-nonsense strength training for desk jobs", "audience": "Busy professionals"}
ANGLES = [{"angle_id": "abc123", "title": "Protein timing barely matters", "promise": "Hit your daily total and relax"}]


def test_material_validate_keeps_grounded_and_drops_the_rest():
    raw = [
        {"text": "my coach made me write the number down before I walked in", "source": "own_reel", "ref": "R1"},
        {"text": "I won a national powerlifting title", "source": "own_reel", "ref": "R1"},        # not said
        {"text": "three short sessions a week for office workers", "source": "profile", "ref": ""},
        {"text": "73% of lifters plateau by month three", "source": "mechanism", "ref": ""},       # number
        {"text": "progressive overload works because the body adapts to stress", "source": "mechanism", "ref": ""},
        {"text": "hit your daily total", "source": "niche_trend", "ref": "A1"},
    ]
    got = material.validate(raw, samples=SAMPLES, brand=BRAND, memory=None, angles=ANGLES)
    texts = [g["text"] for g in got]
    assert len(got) == 3                                       # capped at MAX_PER_SLOT, in order
    assert "I won a national powerlifting title" not in texts
    assert not any("73%" in t for t in texts)
    assert got[0]["source"] == "own_reel" and got[1]["source"] == "profile"


def test_material_allowed_claims_and_render():
    parts = [{"text": "Two and a half pounds every session", "source": "own_reel", "ref": "R1"},
             {"text": "forty-five minutes then walk", "source": "own_reel", "ref": "R2"},
             {"text": "adapts to stress", "source": "mechanism", "ref": ""}]
    assert material.allowed_claims([{"text": "185 lbs for 4 months", "source": "own_reel"}]) == ["185 lbs", "4 months"]
    out = material.render(parts, "how long it took you")
    assert "they said this in a real reel" in out and "[fill-in]" not in out and "No brackets" in out
    assert "nothing personal" in material.render([], "")


def test_material_gather_maps_slots_and_survives_failure(monkeypatch):
    async def fake(**kw):
        return ({"slots": [{"slot": 1, "particulars": [
            {"text": "write the number down before I walked in", "source": "own_reel", "ref": "R1"}],
            "ask_user": "how long it took"}]}, {})
    monkeypatch.setattr(material.llm, "call", fake)
    got = asyncio.run(material.gather([{"topic": "t"}, {"topic": "u"}], brand=BRAND, samples=SAMPLES, memory=None))
    assert got[0]["particulars"] and got[0]["ask_user"] == "how long it took" and got[1]["particulars"] == []

    async def boom(**kw):
        return (None, {})
    monkeypatch.setattr(material.llm, "call", boom)
    got = asyncio.run(material.gather([{"topic": "t"}], brand=BRAND, samples=SAMPLES, memory=None))
    assert got == [{"particulars": [], "ask_user": ""}]


# --- writer -------------------------------------------------------------------------------

def test_writer_normalize_and_split():
    w = writer.normalize({"last_line": "Go buy the little plates.", "hook": "I stalled on bench for four months.",
                          "body": "I stalled on bench for four months. One eighty-five every week."})
    assert not w["body"].startswith("I stalled")
    assert w["body"].endswith("Go buy the little plates.")
    assert writer.normalize({"hook": "", "body": "x", "last_line": ""}) is None
    body, cta = writer.split_cta("First line.\n\nSecond line here. Go do it.")
    assert body == "First line.\n\nSecond line here." and cta == "Go do it."
    assert writer.split_cta("Only one sentence.") == ("Only one sentence.", "")


def test_writer_build_orders_turns_and_caches_last_exemplar():
    system, messages = writer.build(brand=BRAND, voice_block="You call people 'you guys'.",
                                    exemplars=SAMPLES, brief="THIS VIDEO: x")
    assert system[0]["cache_control"]["type"] == "ephemeral" and doctrine.text() in system[0]["text"]
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "user", "assistant", "user"]
    assert "cache_control" in messages[3]["content"][0] and "cache_control" not in messages[1]["content"][0]
    assert messages[1]["content"][0]["text"] == SAMPLES[0]["text"]
    assert "HOW THEY TALK" in messages[-1]["content"][0]["text"]


def test_slot_brief_carries_payoff_register_frame_material():
    b = writer.slot_brief({"topic": "bench plateau", "angle": "small jumps", "payoff": "Buy the little plates."},
                          material_text="MATERIAL: x", length={"lo": 80, "hi": 110},
                          avoid_openers=["Stop stretching before you lift."], register_desc="calm",
                          frame_desc="opens on a number", extra="STRUCTURE: y")
    for needle in ("Buy the little plates.", "calm", "opens on a number", "80 to 110", "MATERIAL: x",
                   "Stop stretching before you lift.", "STRUCTURE: y", "Write last_line first"):
        assert needle in b


# --- judge --------------------------------------------------------------------------------

def test_judge_review_problems_and_pick(monkeypatch):
    async def fake(**kw):
        if kw.get("route") == "scripts_v2.judge":
            return ({"verdicts": [{"index": 0, "generic_hook": True, "specific_word": "",
                                   "payoff_landed": False, "note": "Name the bench."},
                                  {"index": 0, "generic_hook": False, "specific_word": "x",
                                   "payoff_landed": True, "note": ""}]}, {})
        return ({"keep": "A", "why": "w"}, {})
    monkeypatch.setattr(judge.llm, "call", fake)
    v = asyncio.run(judge.review([{"hook": "h", "body": "b"}], niche="Fitness"))
    assert list(v) == [0] and v[0]["generic_hook"] is True        # first verdict per index wins
    probs = judge.problems_from(v[0])
    assert len(probs) == 3 and "Name the bench." in probs[-1]
    assert judge.problems_from({"generic_hook": False, "payoff_landed": True}) == []
    # Same letter both orders = position bias, no decision.
    assert asyncio.run(judge.pick({"hook": "a"}, {"hook": "b"}, niche="F")) is None


# --- pipeline -----------------------------------------------------------------------------

GOOD = {"last_line": "Go buy the little plates.", "hook": "I stalled on bench for four months.",
        "body": "One eighty-five, every week. My coach made me write the number down before I walked in.\n\n"
                "Took six weeks to move. Go buy the little plates."}
ROBOT = {"last_line": "Follow for more tips like this!", "hook": "Ever wondered why your progress has stalled?",
         "body": "Here's the thing: it's not about working harder, it's about working smarter. "
                 "Additionally, consistency is crucial. Follow for more tips like this!"}
SLOTS = [{"formatId": "pov-story", "topic": "bench plateau", "angle": "small jumps",
          "payoff": "Buy the little plates.", "register": "confession", "opener_frame": "confession"},
         {"formatId": "listicle", "topic": "desk sitting", "angle": "walk breaks",
          "payoff": "Walk to the far bathroom.", "register": "how-to", "opener_frame": "you-situation"}]


def _fake_llm(log, writes):
    async def fake(**kw):
        route = kw.get("route")
        log.append(route)
        if route == "scripts_v2.material":
            return ({"slots": []}, {})
        if route in ("scripts_v2.write", "scripts_v2.write_fast"):
            return (writes.pop(0) if writes else GOOD, {})
        if route == "scripts_v2.judge":
            return ({"verdicts": []}, {})
        if route == "scripts_v2.rewrite":
            return (GOOD, {})
        if route == "scripts_v2.pick":
            text = kw["messages"][-1]["content"][0]["text"]
            return ({"keep": "B" if "B:\nFour months" in text else "A", "why": ""}, {})
        return (None, {})
    return fake


def _page(tier, **kw):
    return asyncio.run(pipeline.write_page(
        brand=BRAND, slots=SLOTS, samples=SAMPLES, memory=None, angles=None, avoid_openers=None,
        length={"lo": 40, "hi": 120}, voice_block="", exemplars=SAMPLES[:1], exemplars_alt=kw.get("alt"),
        lint_profile={}, voice_confidence="medium", tier=tier, niche="Fitness"))


def test_pipeline_fast_tier_writes_lints_no_judge(monkeypatch):
    log = []
    monkeypatch.setattr(llm, "call", _fake_llm(log, [dict(ROBOT), dict(GOOD)]))
    res = _page("fast")
    assert len(res) == 2 and "scripts_v2.judge" not in log and "scripts_v2.rewrite" not in log
    assert res[0]["lint_hard"] > 0 and res[1]["lint_hard"] == 0          # stamped, not fixed, on first paint
    assert res[0]["model"] == flags.first_paint_model()
    assert res[0]["cta"] and res[0]["doctrine_version"] == doctrine.version()


def test_pipeline_full_tier_warms_cache_then_fixes_robotic_draft(monkeypatch):
    log = []
    monkeypatch.setattr(llm, "call", _fake_llm(log, [dict(ROBOT), dict(GOOD)]))
    res = _page("full")
    writes = [i for i, r in enumerate(log) if r == "scripts_v2.write"]
    assert log.index("scripts_v2.material") < writes[0]
    assert log.count("scripts_v2.rewrite") == 1                        # only the robotic one
    assert res[0]["rewritten"] and res[0]["lint_hard"] == 0 and res[0]["hook"] == GOOD["hook"]
    assert res[0]["model"] == flags.writer_model()


def test_pipeline_keeps_draft_when_rewrite_is_not_cleaner(monkeypatch):
    log = []
    fake = _fake_llm(log, [dict(ROBOT), dict(GOOD)])

    async def worse(**kw):
        if kw.get("route") == "scripts_v2.rewrite":
            return (dict(ROBOT), {})
        return await fake(**kw)
    monkeypatch.setattr(llm, "call", worse)
    res = _page("full")
    assert not res[0]["rewritten"] and res[0]["hook"] == ROBOT["hook"]


def test_pipeline_best_of_two_uses_alt_exemplars_and_pick(monkeypatch):
    monkeypatch.setenv("SCRIPTS_V2_BEST_OF", "2")
    log = []
    monkeypatch.setattr(llm, "call", _fake_llm(log, [dict(GOOD), dict(GOOD),
                                                     dict(GOOD, hook="Four months on one eighty-five."),
                                                     dict(GOOD, hook="Four months on one eighty-five.")]))
    res = _page("full", alt=SAMPLES[1:])
    assert log.count("scripts_v2.write") == 4 and "scripts_v2.pick" in log
    assert res[0]["hook"] == "Four months on one eighty-five."          # preferred in both orders


def test_pipeline_drops_failed_slots(monkeypatch):
    async def fake(**kw):
        if kw.get("route") == "scripts_v2.write_fast":
            return (None, {})
        return ({"slots": []}, {})
    monkeypatch.setattr(llm, "call", fake)
    assert _page("fast") == []


# --- angles -------------------------------------------------------------------------------

def test_angles_build_velocity_and_trend_cards(monkeypatch):
    reels = [{"transcript": f"Protein timing doesn't matter much. Total protein does. reel {i}", "views": 1000 * (i + 1)}
             for i in range(4)]

    async def fake(**kw):
        return ({"angles": [{"title": "Protein timing barely matters", "promise": "Hit your daily total",
                             "reels": [0, 1, 9], "format": "myth-buster"},
                            {"title": "", "promise": "x", "reels": [2], "format": "listicle"}]}, {})
    monkeypatch.setattr(angles.llm, "call", fake)
    prev = [{"title": "protein timing barely matters", "views": 1000}]
    got = asyncio.run(angles.build("Fitness", reels, prev))
    assert len(got) == 1 and got[0]["reels"] == 2 and got[0]["views"] == 3000
    assert got[0]["velocity"] == "rising" and got[0]["openers"][0] == "Protein timing doesn't matter much."
    cards = angles.as_trends(got, "fitness", 18)
    assert cards[0]["angleId"] == got[0]["angle_id"] and cards[0]["asOf"]
    assert "2 of the top 18 fitness reels" in cards[0]["why"] and "3K views" in cards[0]["why"]
    assert asyncio.run(angles.build("Fitness", reels[:2], None)) == []
    assert angles.velocity("brand new idea", 5, prev) == "new"


# --- hook lab -----------------------------------------------------------------------------

def test_hooklab_distinct_frames_checks_and_legacy_fields(monkeypatch):
    async def fake(**kw):
        if kw.get("route") == "scripts_v2.hooklab":
            return ({"hooks": [
                {"frame": "number-first", "text": "Four months on one eighty-five.", "why": "w", "modeled_on": "your_reel"},
                {"frame": "number-first", "text": "dup frame", "why": "w", "modeled_on": ""},
                {"frame": "command", "text": "Write the number down before you walk in.", "why": "w", "modeled_on": "niche"},
                {"frame": "question", "text": "Ever wondered why you stall?", "why": "w", "modeled_on": "bogus"},
            ]}, {})
        return ({"checks": [{"index": 0, "specific": True, "promises_payoff": True},
                            {"index": 2, "specific": True, "promises_payoff": True}]}, {})
    monkeypatch.setattr(llm, "call", fake)
    out = asyncio.run(hooklab.generate("bench plateau", brand=BRAND, voice_block="", exemplars=SAMPLES,
                                       own_openers=[], niche_openers=[], voice_confidence="medium"))
    assert [h["frame"] for h in out].count("number-first") == 1 and len(out) == 3
    top = out[0]
    assert top["signal"] in prompts.SIGNAL_LIST and top["label"] and top["checks"]["specific"]
    q = next(h for h in out if h["frame"] == "question")
    assert q["checks"]["specific"] is False and "opener_cliche" in q["lintCodes"] and q["modeledOn"] == ""


# --- beats --------------------------------------------------------------------------------

def test_beats_skeleton(monkeypatch):
    async def fake(**kw):
        return ({"beats": [{"role": "hook", "does": "names a habit the viewer has"},
                           {"role": "payoff", "does": "gives the one fix"}, {"role": "bogus", "does": "x"}],
                 "topic_for_creator": "the stretch before lifting", "payoff_for_creator": "warm up with the bar",
                 "format": "myth-buster"}, {})
    monkeypatch.setattr(beats.llm, "call", fake)
    sk = asyncio.run(beats.skeleton({"transcript": "word " * 30}, niche="Fitness"))
    assert [b["role"] for b in sk["beats"]] == ["hook", "payoff"] and sk["topic"]
    assert "never its words" in beats.beats_block(sk)
    assert asyncio.run(beats.skeleton({"transcript": "too short"}, niche="F")) is None


# --- main wiring --------------------------------------------------------------------------

def _sreq(**kw):
    base = dict(niche="Fitness", audience="Busy professionals", pillar="Myth-bust", style="talking_head",
                count=2, creator_id="qa-editor-v2")
    base.update(kw)
    return main.ScriptRequest(**base)


V2_RESULT = [{"hook": GOOD["hook"], "body": "One eighty-five, every week.", "cta": "Go buy the little plates.",
              "slot": SLOTS[0], "particulars": [{"text": "x", "source": "own_reel", "ref": "R1"}],
              "lint_codes": [], "lint_hard": 0, "voice_confidence": "medium",
              "doctrine_version": "wd-x", "model": "m", "tier": "fast"}]


def _stub_inputs(monkeypatch):
    async def inputs(sreq):
        return {"posts": [], "samples": [], "profile": None, "voice_block": "", "exemplars": [],
                "exemplars_alt": [], "lint_profile": {}, "length": {"lo": 90, "hi": 130}, "confidence": "low"}
    monkeypatch.setattr(main, "_v2_inputs", inputs)
    monkeypatch.setattr(main, "_niche_angles", lambda n: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(main, "_account_context", lambda c: asyncio.sleep(0, result=""))


def test_flag_off_never_touches_v2(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def boom(*a, **k):
        raise AssertionError("v2 must not run with the flag off")

    async def v1_fails(*a, **k):
        raise main.HTTPException(status_code=502, detail="x")     # v1 then fails fast into mock
    monkeypatch.setattr(main, "_v2_page", boom)
    monkeypatch.setattr(main, "_account_context", v1_fails)
    out = asyncio.run(main._fast_feed_scripts(_sreq()))
    assert out["mode"] in ("mock", "live_fast")


def test_flag_on_serves_v2_dto(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setenv("SCRIPTS_V2", "on")
    _stub_inputs(monkeypatch)
    monkeypatch.setattr(main, "_plan_slots", lambda *a, **k: asyncio.sleep(0, result=[SLOTS[0]]))

    async def page(**kw):
        assert kw["tier"] == "fast"
        return V2_RESULT
    monkeypatch.setattr(pipeline, "write_page", page)
    out = asyncio.run(main._fast_feed_scripts(_sreq()))
    assert out["mode"] == "live_fast" and out["engine"] == "v2"
    s = out["scripts"][0]
    assert s["hook"] and s["body"] and s["cta"] and s["hookSignal"] in prompts.SIGNAL_LIST
    assert s["openerFrame"] == "confession" and s["payoff"] and s["particularSources"] == ["own_reel"]
    assert s["formatId"] in prompts.FORMAT_IDS and s["title"] == "bench plateau" and 40 <= s["predictedScore"] <= 74


def test_v2_failure_falls_back_to_v1(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    monkeypatch.setenv("SCRIPTS_V2", "on")
    _stub_inputs(monkeypatch)
    monkeypatch.setattr(main, "_plan_slots", lambda *a, **k: asyncio.sleep(0, result=[SLOTS[0]]))

    async def page(**kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(pipeline, "write_page", page)
    out = asyncio.run(main._v2_page(_sreq(), 0, tier="fast"))
    assert out is None


def test_voice_feedback_never_touches_arms_and_topic_dislike_is_remembered(monkeypatch):
    monkeypatch.setattr(main, "_v2_voice", lambda cid, fresh=False: asyncio.sleep(0, result=None))

    async def no_arms(*a, **k):
        raise AssertionError("voice feedback must not update arms")
    monkeypatch.setattr(main, "_update_arm_feedback", no_arms)
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    j = c.post("/v1/feed/feedback", json={"creator_id": "qa-editor-v2", "voice_verdict": "not_me",
                                          "script": {"title": "t", "hook": "h"}}).json()
    assert j["status"] in ("recorded", "ignored") and j["arms_updated"] == 0
    monkeypatch.setattr(main, "_update_arm_feedback", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(main, "_flush_arms", lambda *a, **k: asyncio.sleep(0))
    c.post("/v1/feed/feedback", json={"creator_id": "qa-editor-v2", "verdict": "dislike", "reason": "topic",
                                      "script": {"title": "desk sitting", "summary": "walk breaks every hour",
                                                 "hook": "You sat nine hours today."}})
    avoid = main._avoid_topics("qa-editor-v2")
    assert "desk sitting" in avoid and "walk breaks every hour" in avoid


def test_voice_routes_keyless_and_store(monkeypatch):
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    assert c.get("/v1/voice", params={"creator_id": "default"}).json() == {"mode": "empty", "voice": {}}
    assert c.post("/v1/voice/build", json={"creator_id": "qa-editor-v2"}).json()["status"] == "unavailable"
    j = c.post("/v1/voice/preview", json={"creator_id": "qa-editor-v2", "niche": "Fitness"}).json()
    assert j["mode"] == "mock" and j["with_voice"]["hook"]

    class Store:
        def __init__(self):
            self.rows = {}

        async def load_reels_cache(self, k):
            return self.rows.get(k)

        async def upsert_reels_cache(self, k, v):
            self.rows[k] = v
            return True
    st = Store()
    monkeypatch.setattr(main, "_supabase_client", st)
    monkeypatch.setattr(main, "_hydrate_creator_profile", lambda cid: asyncio.sleep(0, result={"niche": "Fitness"}))
    main._v2_voice_cache.clear()
    j = c.put("/v1/voice", json={"creator_id": "qa-editor-v2", "never_say": ["synergy"],
                                 "correction": "I never say 'here's why'."}).json()
    assert j["mode"] == "live" and j["voice"]["never_say"] == ["synergy"]
    assert "I never say 'here's why'." in j["voice"]["corrections"]
    assert "voice:qa-editor-v2" in st.rows
    main._v2_voice_cache.clear()
    j2 = c.get("/v1/voice", params={"creator_id": "qa-editor-v2"}).json()
    assert j2["mode"] == "live" and j2["voice"]["never_say"] == ["synergy"]


def test_opener_frame_dimension_is_env_gated(monkeypatch):
    import supabase_persistence as sp
    assert "opener_frame" not in main._dims() and "opener_frame" not in sp._post_cols()
    monkeypatch.setenv("BANDIT_OPENER_FRAME", "1")
    assert main._dims()[-1] == "opener_frame" and "opener_frame" in sp._post_cols()


def test_trends_route_serves_angles_when_v2_on(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SCRIPTS_V2", "on")
    key = main._v2_trends_key("Fitness")
    import time as _t
    monkeypatch.setitem(main._v2_angles_cache, key, {"angles": [
        {"angle_id": "a1", "title": "Protein timing barely matters", "promise": "Hit your total", "format": "myth-buster",
         "openers": ["Protein timing doesn't matter."], "reels": 3, "views": 42000, "velocity": "new",
         "as_of": "2026-10-19"}], "ts": _t.time(), "total_reels": 18})
    j = TestClient(main.app).get("/v1/trends", params={"niche": "Fitness"}).json()
    assert j["mode"] == "live" and j["trends"][0]["angleId"] == "a1" and j["trends"][0]["asOf"] == "2026-10-19"
    monkeypatch.setenv("SCRIPTS_V2", "off")
    j = TestClient(main.app).get("/v1/trends", params={"niche": "Fitness"}).json()
    assert "angleId" not in (j["trends"][0] if j["trends"] else {})


def test_plan_slots_maps_trend_angle_ids(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def fake_json(sys, usr, schema, model, max_tokens=3000, **kw):
        assert "THIS WEEK IN THE NICHE" in usr
        return {"slots": [{"slot": 1, "formatId": "myth-buster", "subarea": "food", "topic": "t1", "angle": "a",
                           "payoff": "p", "register": "how-to", "opener_frame": "command", "trend_angle": "A1"}]}
    monkeypatch.setattr(main, "anthropic_json", fake_json)
    slots = asyncio.run(main._plan_slots(_sreq(count=1), 0, "", angles=ANGLES))
    assert slots[0]["trend_angle"] == "abc123"


def test_register_post_whitelists_opener_frame(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("BANDIT_OPENER_FRAME", "1")
    c = TestClient(main.app)
    c.post("/v1/posts/register", json={"post_id": "qa-editor-p1", "creator_id": "qa-editor-v2",
                                       "opener_frame": "confession", "hook_signal": "narrative"})
    c.post("/v1/posts/register", json={"post_id": "qa-editor-p2", "creator_id": "qa-editor-v2",
                                       "opener_frame": "not-a-frame"})
    assert main._post_registry["qa-editor-p1"].get("opener_frame") == "confession"
    assert main._post_registry["qa-editor-p2"].get("opener_frame") == ""


def test_own_reel_words_are_not_flagged_as_exemplar_copying():
    # Smoke 2026-10-19: the material step handed the writer the creator's own line ("Three
    # days a week, squat, press, pull, for three months") and the lint flagged reusing it.
    reel = "Three days a week squat press pull for three months and then we talk honestly"
    s = {"hook": "Run one plan before you judge it.",
         "body": "Three days a week squat press pull for three months and then we talk. That's it.",
         "cta": "Start Monday."}
    prof = {"exemplar_texts": [reel]}
    plain = pipeline._lint(s, lint_profile=prof, allowed=[], avoid=None, length={"lo": 5, "hi": 60})
    assert "exemplar_overlap" in {f.code for f in plain}
    quoted = pipeline._lint(s, lint_profile=prof, allowed=[], avoid=None, length={"lo": 5, "hi": 60},
                            quoted=["Three days a week squat press pull for three months and then we talk"])
    assert "exemplar_overlap" not in {f.code for f in quoted}


def test_dto_title_prefers_the_planner_title():
    r = dict(V2_RESULT[0], slot=dict(SLOTS[0], title="why your bench stalls",
                                     topic="Why your bench stalls when you add weight on feel instead of a plan"))
    assert main._v2_to_dto(r, _sreq())["title"] == "why your bench stalls"


def test_situation_particulars_are_concrete_and_never_first_person():
    raw = [{"text": "the gym bag that rides in the trunk all week", "source": "situation", "ref": ""},
           {"text": "I keep my gym bag in the trunk", "source": "situation", "ref": ""},
           {"text": "40% of people skip the gym after work", "source": "situation", "ref": ""}]
    got = material.validate(raw, samples=[], brand=BRAND, memory=None, angles=[])
    assert [g["text"] for g in got] == ["the gym bag that rides in the trunk all week"]


def test_cold_start_plan_never_assigns_confession(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")

    async def fake_json(sys, usr, schema, model, max_tokens=3000, **kw):
        return {"slots": [{"slot": i + 1, "formatId": "x", "subarea": f"s{i}", "topic": f"t{i}", "title": "",
                           "angle": "a", "payoff": "p", "register": "confession", "opener_frame": "confession",
                           "trend_angle": ""} for i in range(3)]}
    monkeypatch.setattr(main, "anthropic_json", fake_json)
    cold = asyncio.run(main._plan_slots(_sreq(count=3), 0, "", personal_ok=False))
    assert all(s["register"] != "confession" and s["opener_frame"] != "confession" for s in cold)
    warm = asyncio.run(main._plan_slots(_sreq(count=3), 0, ""))
    assert warm[0]["register"] == "confession"


def test_min_samples_gate_sends_cold_start_creators_to_v1(monkeypatch):
    _stub_inputs(monkeypatch)                                   # no samples
    monkeypatch.setenv("SCRIPTS_V2_MIN_SAMPLES", "2")

    async def page(**kw):
        raise AssertionError("gated creators never reach the v2 writer")
    monkeypatch.setattr(pipeline, "write_page", page)
    assert asyncio.run(main._v2_page(_sreq(), 0, tier="fast")) is None


def test_hooklab_never_offers_a_confession_without_real_material(monkeypatch):
    seen = {}

    async def fake(**kw):
        if kw.get("route") == "scripts_v2.hooklab":
            seen["brief"] = kw["messages"][-1]["content"][0]["text"]
            return ({"hooks": [
                {"frame": "confession", "text": "I benched the same weight for four months.", "why": "w", "modeled_on": ""},
                {"frame": "command", "text": "Write the number down before you walk in.", "why": "w", "modeled_on": ""}]}, {})
        return ({"checks": []}, {})
    monkeypatch.setattr(llm, "call", fake)
    out = asyncio.run(hooklab.generate("bench", brand=BRAND, voice_block="", exemplars=[], own_openers=[],
                                       niche_openers=[], personal_ok=False))
    assert [h["frame"] for h in out] == ["command"] and "confession:" not in seen["brief"]
    out = asyncio.run(hooklab.generate("bench", brand=BRAND, voice_block="", exemplars=[], own_openers=[],
                                       niche_openers=[], personal_ok=True))
    assert "confession" in [h["frame"] for h in out]
