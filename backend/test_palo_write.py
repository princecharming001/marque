"""Phase 5 box 1 — write agent: action parsing + turn (fill/edit/answer), keyless."""
from __future__ import annotations

import asyncio

import pytest

from app import palo_flags
from app import write_agent as wa


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "WRITE_AGENT", True)


# --- parse_write_actions ------------------------------------------------------

def test_parse_all_action_types_in_order():
    text = ('<answer>here is why</answer>'
            '<edit><old>old phrase</old><new>new phrase</new></edit>'
            '<add position="after" ref="the hook">extra line</add>'
            '<fill>a whole new script</fill>')
    acts = wa.parse_write_actions(text)
    assert [a["op"] for a in acts] == ["answer", "edit", "add", "fill"]   # document order
    edit = acts[1]
    assert edit["old"] == "old phrase" and edit["new"] == "new phrase"
    add = acts[2]
    assert add["position"] == "after" and add["ref"] == "the hook" and add["text"] == "extra line"
    assert acts[3]["content"] == "a whole new script"


def test_parse_empty():
    assert wa.parse_write_actions("") == []
    assert wa.parse_write_actions("just prose, no tags") == []


def test_prompt_caps_large_inputs():
    from app import palo_prompts
    _sys, user = palo_prompts.write_agent_prompt("x" * 50000, "y" * 5000)
    assert user.count("x") == 20000 and user.count("y") == 2000       # capped, not unbounded
    _sys2, user2 = palo_prompts.script_from_brief_prompt({"title": "t", "beginning": "z" * 9000})
    assert user2.count("z") == 1500


# --- write_turn ---------------------------------------------------------------

def test_write_turn_flag_off():
    assert _run(wa.write_turn(None, "c1", "body", "punch it up")) == {"actions": [], "mode": "off"}


def test_write_turn_keyless_mock_answer(on):
    out = _run(wa.write_turn(None, "c1", "my script body", "make the hook stronger"))
    assert out["mode"] == "mock" and out["actions"][0]["op"] == "answer"


def test_write_turn_parses_llm_actions(on, monkeypatch):
    async def fake_llm(system, user, model, max_tokens=0, temperature=None):
        return "<edit><old>hello</old><new>hey</new></edit>"
    monkeypatch.setattr(wa, "anthropic_cached", fake_llm)
    out = _run(wa.write_turn(None, "c1", "hello world", "casual it up"))
    assert out["mode"] == "live" and out["actions"] == [{"op": "edit", "old": "hello", "new": "hey"}]


def test_write_turn_prose_becomes_answer(on, monkeypatch):
    async def fake_llm(system, user, model, max_tokens=0, temperature=None):
        return "I think the hook is already strong."
    monkeypatch.setattr(wa, "anthropic_cached", fake_llm)
    out = _run(wa.write_turn(None, "c1", "body", "thoughts?"))
    assert out["actions"][0]["op"] == "answer" and "hook is already strong" in out["actions"][0]["text"]
