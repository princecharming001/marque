"""B2: fail-closed speakability enforcement. Every script-generation path must NEVER
return a lint-dirty body — repair, fallback, or drop, but never ship a description."""
import asyncio

from fastapi.testclient import TestClient

import main
import prompts

client = TestClient(main.app)


DIRTY_BODY = "Demonstrate the move on camera. Highlight the key number."
STILL_DIRTY_REPAIR = "Talk about how this works, then show the result."   # repair that's still dirty
CLEAN_REPAIR = "This move works because of leverage, not effort. Watch the number climb."


def _script(body=DIRTY_BODY, **extra):
    return {"title": "x", "hook": "fine hook", "body": body, "style": "talking_head", **extra}


# ---------------------------------------------------------------------------
# Core _ensure_speakable policies
# ---------------------------------------------------------------------------

def test_clean_body_passes_through_untouched(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    called = False
    async def fake(*a, **k):
        nonlocal called
        called = True
        return "should never be called"
    monkeypatch.setattr(main, "anthropic", fake)
    sc = _script(body="This is a perfectly normal spoken line about my morning routine.")
    out = asyncio.run(main._ensure_speakable([sc]))
    assert out == [sc]
    assert called is False


def test_repair_succeeds_and_is_relinted(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    async def fake(system, user, model=main.HAIKU, max_tokens=600, **k):
        return CLEAN_REPAIR
    monkeypatch.setattr(main, "anthropic", fake)
    out = asyncio.run(main._ensure_speakable([_script()], policy="repair_or_drop"))
    assert len(out) == 1
    assert out[0]["body"] == CLEAN_REPAIR
    assert prompts.flag_stage_direction(out[0]["body"]) is None


def test_repair_still_dirty_triggers_policy_not_shipped(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    async def fake(system, user, model=main.HAIKU, max_tokens=600, **k):
        return STILL_DIRTY_REPAIR   # the "repair" is itself still a description
    monkeypatch.setattr(main, "anthropic", fake)
    out = asyncio.run(main._ensure_speakable([_script()], policy="repair_or_drop"))
    assert out == []   # NEVER ships the still-dirty repair


def test_policy_repair_or_drop_removes_unrepairable(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")   # keyless: no repair attempt at all
    out = asyncio.run(main._ensure_speakable([_script(), _script(body="also dirty. Demonstrate this.")],
                                             policy="repair_or_drop"))
    assert out == []


def test_policy_repair_or_fallback_substitutes(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")   # keyless: no repair
    fallback_script = _script(body="A totally different, clean, speakable fallback line.")
    out = asyncio.run(main._ensure_speakable(
        [_script()], policy="repair_or_fallback", fallback=lambda i: fallback_script))
    assert out == [fallback_script]


def test_policy_repair_or_keep_input_returns_pre_edit_floor(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    original = _script(body="The creator's own untouched pre-edit script.")
    out = asyncio.run(main._ensure_speakable(
        [_script()], policy="repair_or_keep_input", fallback=lambda i: original))
    assert out == [original]


def test_fallback_none_sentinel_is_returned_verbatim(monkeypatch):
    # Used by _guard_write_actions to signal "convert to an answer" upstream.
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    out = asyncio.run(main._ensure_speakable(
        [_script()], policy="repair_or_fallback", fallback=lambda i: None))
    assert out == [None]


def test_never_returns_dirty_body_regardless_of_policy(monkeypatch):
    # Exhaustive: no matter the policy/keyless state, a returned script (if any) is clean.
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    for policy, fallback in [
        ("repair_or_drop", None),
        ("repair_or_fallback", lambda i: _script(body="clean fallback body here")),
        ("repair_or_keep_input", lambda i: _script(body="clean kept input body")),
    ]:
        out = asyncio.run(main._ensure_speakable([_script()], policy=policy, fallback=fallback))
        for s in out:
            assert s is None or prompts.flag_stage_direction(s.get("body", "")) is None


# ---------------------------------------------------------------------------
# quality_scripts revise-output guard (dirty OPUS rewrite must not overwrite a clean draft)
# ---------------------------------------------------------------------------

def test_quality_scripts_revise_skips_dirty_rewrite(monkeypatch):
    monkeypatch.setattr(main, "AI_QUALITY", True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    draft = _script(body="A clean pre-revise draft that the judge flagged for other reasons.")
    scripts = [draft]

    async def fake(system, user, schema, model=main.HAIKU, max_tokens=1400,
                   temperature=None, array_key=None):
        if array_key == "verdicts":
            return [{"index": 0, "verdict": "revise", "best_hook": 0,
                     "hook_strength": 5, "specificity": 5, "format_fit": 5, "voice_match": 5}]
        if array_key == "scripts":   # the revise call — returns a STILL-DIRTY rewrite
            return [{"hook": "fine hook", "body": DIRTY_BODY, "style": "talking_head"}]
        return []
    monkeypatch.setattr(main, "anthropic_json", fake)
    out = asyncio.run(main.quality_scripts({}, "talking_head", scripts, creator_id="default"))
    assert out[0]["body"] == draft["body"]   # dirty revise was skipped; pre-revise draft kept


# ---------------------------------------------------------------------------
# write-turn <fill> guard
# ---------------------------------------------------------------------------

def test_guard_write_actions_repairs_dirty_fill(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "k")
    async def fake(system, user, model=main.HAIKU, max_tokens=600, **k):
        return CLEAN_REPAIR
    monkeypatch.setattr(main, "anthropic", fake)
    actions = [{"op": "fill", "content": DIRTY_BODY}]
    out = asyncio.run(main._guard_write_actions(actions))
    assert out[0]["op"] == "fill"
    assert out[0]["content"] == CLEAN_REPAIR


def test_guard_write_actions_converts_unrepairable_fill_to_answer(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")   # keyless: no repair possible
    actions = [{"op": "fill", "content": DIRTY_BODY}]
    out = asyncio.run(main._guard_write_actions(actions))
    assert out[0]["op"] == "answer"
    assert "tell me the exact line" in out[0]["text"]


def test_guard_write_actions_passes_through_non_fill_ops(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    actions = [{"op": "edit", "old": "x", "new": "y"}, {"op": "answer", "text": "sure"}]
    out = asyncio.run(main._guard_write_actions(actions))
    assert out == actions


def test_write_turn_route_never_applies_dirty_fill(monkeypatch):
    monkeypatch.setattr(main.palo_flags, "enabled", lambda flag: True)
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")   # keyless: repair impossible -> must convert

    async def fake_write_turn(store, creator_id, body, instruction, brand=None):
        return {"actions": [{"op": "fill", "content": DIRTY_BODY}], "mode": "live"}
    monkeypatch.setattr(main.write_agent, "write_turn", fake_write_turn)

    resp = client.post("/v1/write/turn", json={
        "creator_id": "default", "script": {"body": "original clean body"},
        "instruction": "rewrite it",
    })
    assert resp.status_code == 200
    data = resp.json()
    # The fill was converted to an answer, never applied -> preview body is UNCHANGED.
    assert data["preview"]["body"] == "original clean body"
    assert prompts.flag_stage_direction(data["preview"]["body"]) is None


# ---------------------------------------------------------------------------
# Mock fallbacks used across paths must themselves always be speakability-clean —
# otherwise a policy=repair_or_fallback path could silently ship a dirty fallback.
# ---------------------------------------------------------------------------

def test_mock_scripts_are_always_speakable():
    req = main.ScriptRequest(niche="fitness for busy parents", count=3)
    for s in main.mock_scripts(req):
        assert prompts.flag_stage_direction(s["body"]) is None


def test_mock_mimic_is_always_speakable():
    reel = {"hook_text": "The fitness myth everyone believes", "title": "y", "format_id": "myth-buster"}
    out = main._mock_mimic(reel, {"niche": "fitness"})
    assert prompts.flag_stage_direction(out["body"]) is None
