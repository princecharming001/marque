"""Palo port round 2 (2026-08-04) — keyless tests for the scriptwriting/suggestions
upgrades lifted from Palo_Server + the live LaunchDarkly prompt bank:

a5 craft rules + worked examples in scripts_prompt · plan-first/durationSeconds JSON
contract (+ the _ensure_speakable strip/clamp) · voiceprint + opener-dedup blocks ·
banned-phrase lint family · steer rules 0-4 · THE MIX rotation prior · pulse idea judge
(keyless sentinel + scored path) · hedging pre-gate · a4 insight-card lint · the first
channel read prompt/route.
"""
from __future__ import annotations

import asyncio

import prompts
from app import ideas, palo_flags, palo_prompts

BRAND = {"niche": "chess", "known_for": "speedruns", "audience": "beginners"}


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self):
        self.upserts = []

    async def load_prompt_override(self, key):
        return None

    async def upsert_brief(self, b):
        self.upserts.append(b)
        return True

    async def load_briefs(self, creator_id, status="", limit=30):
        return []

    async def record_ai_usage(self, row):
        return True


# --- deterministic lint: banned-phrase family ---------------------------------

def test_banned_phrase_family_flags():
    assert prompts.flag_stage_direction("Without further ado, let's get into it")
    assert prompts.flag_stage_direction("This trick is going to blow your mind")
    assert prompts.flag_stage_direction("In this video we'll cover three things")
    assert prompts.flag_stage_direction("Little did I know the steward was watching")
    # real spoken copy stays clean
    assert prompts.flag_stage_direction("I fired my biggest client on a Tuesday.") is None
    assert prompts.flag_stage_direction("Buckle your kid into the seat first.") is None


# --- plan-first / durationSeconds contract ------------------------------------

def test_script_schema_plan_first_duration_present():
    props = list(prompts.SCRIPT_JSON_ELEMENT["properties"])
    assert props[0] == "plan"                       # definition order == generation order
    req = prompts.SCRIPT_JSON_ELEMENT["required"]
    assert "plan" in req and "durationSeconds" in req
    assert '"plan"' in prompts.SCRIPT_SCHEMA and "durationSeconds" in prompts.SCRIPT_SCHEMA
    # the fast first-paint schema stays lean — no plan tax on the latency path
    assert "plan" not in prompts.FAST_SCRIPT_JSON_ELEMENT["properties"]


def test_ensure_speakable_strips_plan_and_clamps_duration():
    import main
    s = {"body": "I tried this opening and it worked in nine moves.", "style": "talking_head",
         "plan": "internal", "durationSeconds": 10000, "targetSeconds": 30}
    (out,) = _run(main._ensure_speakable([s]))
    assert "plan" not in out
    assert out["durationSeconds"] == 600            # clamped, never a wish
    s2 = {"body": "I tried this opening and it worked in nine moves.", "style": "talking_head",
          "targetSeconds": 30}
    (out2,) = _run(main._ensure_speakable([s2]))
    assert out2["durationSeconds"] == 30            # absent -> honest targetSeconds floor


# --- voiceprint + opener dedup --------------------------------------------------

_POSTS = [
    {"caption": "Security is 10 feet away and I don't have a ticket. Watch this whole thing.",
     "likes": 10, "timestamp": "2026-08-01"},
    {"caption": "Nobody checks the concourse. Here's why that matters for you today.",
     "likes": 5, "timestamp": "2026-08-02"},
]


def test_voiceprint_block_shape_not_lines():
    vp = prompts._voice_exemplars(_POSTS)
    assert "VOICEPRINT" in vp and "never lift the line" in vp and "never content" in vp
    assert prompts._voice_exemplars([]) == ""


def test_opener_dedup_block():
    dd = prompts._opener_dedup_block(_POSTS)
    assert "OPENING LINES" in dd and "must NOT duplicate" in dd
    assert "Nobody checks the concourse" in dd      # most recent first
    assert prompts._opener_dedup_block(None) == ""


def test_scripts_prompt_carries_craft_examples_dedup():
    sys, _ = prompts.scripts_prompt(BRAND, {"name": "Openings"}, "talking_head", 2,
                                    posts=_POSTS)
    assert "SCRIPT CRAFT" in sys                    # a5 rules
    assert "A WRONG script" in sys                  # the annotated failure example
    assert "OPENING LINES" in sys                   # dedup rides along
    assert "viewer-seat" in sys or "2am" in sys     # self-check insert


def test_steer_prompt_carries_rules_and_grounding():
    sys, _ = prompts.steer_prompt(BRAND, {"hook": "h", "body": "b", "cta": "c"}, "shorter")
    assert "PRESERVE that intent" in sys
    assert "less natural or cringe" in sys          # rule 3's guard, verbatim
    assert "GROUNDING" in sys and "SPEAKABLE" in sys


def test_judge_prompt_has_anchors_and_caps():
    sys, _ = prompts.script_judge_prompt([{"hook": "h", "body": "b", "cta": "c"}], "talking_head")
    assert "AXIS CAPS" in sys and "ANCHORED EXAMPLES" in sys


# --- THE MIX --------------------------------------------------------------------

def test_mix_block_rotation_and_inventory():
    lanes = [{"name": "Openings", "recent": 3, "queued": 1},
             {"name": "Blunders", "recent": 0, "queued": 0}]
    out = prompts.mix_block(lanes, ["Queued idea title"])
    assert "THE MIX" in out and "Under-served" in out and "Blunders" in out
    assert "Queued idea title" in out and "second\nentrant" in out or "second " in out
    assert prompts.mix_block([{"name": "A", "recent": 1}]) == ""       # one lane = no signal
    assert prompts.mix_block([{"name": "A", "recent": 0},
                              {"name": "B", "recent": 0}]) == ""       # no activity


# --- idea judge + hedging gate --------------------------------------------------

def test_hedges_gate():
    assert palo_prompts.hedges("You might want to consider trying a new hook")
    assert palo_prompts.hedges("Great job this week, keep it up!")
    assert not palo_prompts.hedges("Lift the pallet onto the truck in one take")
    assert not palo_prompts.hedges("I fired my biggest client on a Tuesday")


def test_idea_judge_keyless_sentinel():
    scores = _run(ideas.judge_ideas(None, ideas.mock_ideas(BRAND), BRAND))
    assert scores == [-1.0, -1.0, -1.0]             # never fabricate a judged score


def test_suggest_pipeline_judge_scores_and_promotes(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "IDEA_BANK", True)

    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        req = schema.get("required", [])
        if "ideas" in req:
            return {"ideas": [{"title": f"Chess idea {i}", "content": "specific"} for i in range(3)]}
        if "results" in req:
            return {"results": [{"idea_index": i, "pass": True} for i in (1, 2, 3)]}
        # judge shape
        return {"specificity": 3, "non_obvious": 3, "evidence_grounded": 2,
                "actionable": 1, "score": 9, "notes": "sharp"}
    monkeypatch.setattr(ideas, "anthropic_cached_json", fake_json)

    briefs = _run(ideas.suggest_ideas(FakeStore(), "c1", BRAND))
    assert len(briefs) == 3
    assert all(b["score"] == 0.9 for b in briefs)   # judge 9/10 -> 0.9 on the brief scale
    assert all(b.get("promoted") for b in briefs)   # >= 8.0 = banger


def test_suggest_pipeline_hedged_ideas_dropped(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "IDEA_BANK", True)

    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        req = schema.get("required", [])
        if "ideas" in req:
            return {"ideas": [
                {"title": "You might want to consider trying vlogs", "content": "hedged"},
                {"title": "The 9-move trap that wins in blitz", "content": "sharp"},
                {"title": "I blundered my queen on purpose", "content": "sharp"}]}
        if "results" in req:
            return {"results": [{"idea_index": i, "pass": True} for i in (1, 2, 3)]}
        return None                                  # judge keyless-ish -> sentinel
    monkeypatch.setattr(ideas, "anthropic_cached_json", fake_json)

    briefs = _run(ideas.suggest_ideas(FakeStore(), "c1", BRAND))
    titles = {b["title"] for b in briefs}
    assert "You might want to consider trying vlogs" not in titles
    assert len(briefs) == 2


# --- idea generation v2: honesty fallbacks --------------------------------------

def test_idea_generation_prompt_honesty_fallbacks():
    sys, _ = palo_prompts.idea_generation_prompt("signals", "identity")
    assert "omit proof lines" in sys                # no exemplars -> no view-count claims
    assert "structural formulas" in sys             # cold structural fallback text
    sys2, _ = palo_prompts.idea_generation_prompt("s", "i", recent_catalog="- Old video title")
    assert "Old video title" in sys2                # anti-target catalog rides in
    assert "anti-targets" in sys2


# --- insight card: a4 rules + hedging floor -------------------------------------

def test_insight_system_carries_a4_rules():
    assert "CANNOT ALREADY SEE" in palo_prompts.INSIGHT_DISCOVERY_SYSTEM
    assert "SAMPLE SIZE" in palo_prompts.INSIGHT_DISCOVERY_SYSTEM
    assert "NUMBERS READ LIKE SPEECH" in palo_prompts.INSIGHT_DISCOVERY_SYSTEM


def test_insight_card_hedged_copy_falls_back(monkeypatch):
    from app import track_insights

    async def fake_json(system, user, schema, model, max_tokens=0):
        return {"title": "You might want to consider trying reels",
                "description": "Great job, keep it up"}
    monkeypatch.setattr(track_insights, "anthropic_cached_json", fake_json)
    card = _run(track_insights._card(None, "c1",
                                     {"type": "video_spike", "multiplier": 4.0},
                                     [], None))
    # hedged LLM copy is rejected; the deterministic template is the floor
    assert not palo_prompts.hedges(f"{card['title']} {card['description']}")


# --- channel read ----------------------------------------------------------------

def test_channel_read_prompt_rows_and_honesty():
    sys, usr = palo_prompts.channel_read_prompt(
        "instagram", "handle", 1200,
        [{"title": "How I won the open", "views": 900, "date": "2026-08-01"}])
    assert "NOT watched" in sys and "Never invent numbers" in sys
    assert '"How I won the open" | 900 | 2026-08-01' in usr


def test_channel_read_route_keyless_empty():
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        r = client.post("/v1/connect/channel-read",
                        json={"handle": "someone", "platform": "instagram"})
        assert r.status_code == 200
        body = r.json()
        assert body["lines"] == [] and body["mode"] == "mock"   # keyless -> honest silence


# --- OWNER MANDATE (2026-08-04): talking-head only, everywhere ---------------------

def test_active_styles_are_talking_head_only():
    # Every offered style films identically: the creator's face to camera, one take.
    assert prompts.ACTIVE_STYLES == ["talking_head", "green_screen", "broll_cutaway",
                                     "duet_split"]
    for retired in ("faceless", "fast_cuts", "split_three"):
        assert retired in prompts.STYLES          # legacy clips still decode
        assert retired not in prompts.ACTIVE_STYLES


def test_mandate_reaches_every_generation_surface():
    assert "TALKING-HEAD ONLY" in prompts.TALKING_HEAD_MANDATE
    sys_s, _ = prompts.scripts_prompt(BRAND, {"name": "Openings"}, "talking_head", 1)
    assert "TALKING-HEAD ONLY" in sys_s
    sys_i, _ = palo_prompts.idea_generation_prompt("s", "i")
    assert "TALKING-HEAD ONLY" in sys_i
    assert "TALKING-HEAD ONLY" in palo_prompts.DIRECTION_OPTIONS_SYSTEM
    assert "TALKING-HEAD ONLY" in palo_prompts.SCRIPT_FROM_BRIEF_SYSTEM
    assert "TALKING-HEAD ONLY" in palo_prompts.WRITE_AGENT_SYSTEM
    from app import sketch_ideas as _sk
    assert "TALKING-HEAD ONLY" in _sk._SKETCH_SYSTEM
    assert "TALKING-HEAD ONLY" in _sk._IDEA_SYSTEM


def test_mock_ideas_never_demand_extra_filming():
    for idea in ideas.mock_ideas(BRAND):
        text = f"{idea['title']} {idea['content']}".lower()
        assert "talking to camera" in text
        for banned in ("montage", "b-roll heavy", "film with your phone"):
            assert banned not in text
