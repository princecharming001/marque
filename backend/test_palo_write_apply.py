"""Phase 5 box 2 — exact-substring apply + LOOP W invariants + route. Keyless."""
from __future__ import annotations

import asyncio

import pytest

import main
from app import palo_flags
from app import write_agent as wa


def _run(coro):
    return asyncio.run(coro)


# --- apply_actions (exact-substring contract) ---------------------------------

def test_edit_exact_substring_applies():
    body = "hello world, this is my hook"
    new, out = wa.apply_actions(body, [{"op": "edit", "old": "hello world", "new": "hey folks"}])
    assert new == "hey folks, this is my hook" and out[0]["applied"] is True


def test_edit_non_substring_skipped_not_corrupted():
    body = "hello world"
    new, out = wa.apply_actions(body, [{"op": "edit", "old": "goodbye", "new": "x"}])
    assert new == "hello world"                              # untouched
    assert out[0]["applied"] is False and "exact substring" in out[0]["reason"]


def test_add_after_and_before():
    body = "the hook"
    after, _ = wa.apply_actions(body, [{"op": "add", "position": "after", "ref": "the hook", "text": "the build"}])
    assert after == "the hook\nthe build"
    before, _ = wa.apply_actions(body, [{"op": "add", "position": "before", "ref": "the hook", "text": "cold open"}])
    assert before == "cold open\nthe hook"


def test_fill_replaces_whole():
    new, out = wa.apply_actions("old", [{"op": "fill", "content": "brand new script"}])
    assert new == "brand new script" and out[0]["applied"] is True


# --- LOOP W invariants --------------------------------------------------------

def test_invariants_clean_and_violations():
    body = "hello world"
    assert wa.check_invariants(body, [{"op": "edit", "old": "hello", "new": "hey"}]) == []
    assert "exact substring" in " ".join(wa.check_invariants(body, [{"op": "edit", "old": "nope", "new": "x"}]))
    long = " ".join(["word"] * 300)
    assert any("250 words" in i for i in wa.check_invariants("", [{"op": "fill", "content": long}]))
    assert any("leaked" in i for i in wa.check_invariants("", [{"op": "fill", "content": "REGIME: breakout is the plan"}]))


def test_check_invariants_uses_applied_body_no_reapply(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not re-apply when applied_body is given")
    monkeypatch.setattr(wa, "apply_actions", boom)
    issues = wa.check_invariants("hello world", [{"op": "edit", "old": "hello", "new": "hi"}],
                                 applied_body="hi world")
    assert issues == []


# --- route --------------------------------------------------------------------

def test_write_route_flag_off():
    out = _run(main.write_turn_route(main._WriteRequest(creator_id="c1", script={"body": "x"})))
    assert out["mode"] == "off" and out["actions"] == []


def test_write_route_applies(monkeypatch):
    monkeypatch.setattr(main.palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(main.palo_flags, "WRITE_AGENT", True)

    async def fake_turn(store, cid, body, instruction, brand=None):
        return {"actions": [{"op": "edit", "old": "hello", "new": "hey"}], "mode": "live"}
    monkeypatch.setattr(main.write_agent, "write_turn", fake_turn)

    out = _run(main.write_turn_route(main._WriteRequest(
        creator_id="c1", script={"title": "T", "body": "hello world"}, instruction="casual")))
    assert out["preview"]["body"] == "hey world"
    assert out["preview"]["title"] == "T" and out["actions"][0]["applied"] is True
    assert out["invariants"] == []
