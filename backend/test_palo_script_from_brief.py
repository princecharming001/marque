"""Phase 5 box 3 — brief -> first script (onboarding script_generation port). Keyless."""
from __future__ import annotations

import asyncio

import pytest

import main
from app import palo_flags
from app import write_agent as wa

BRIEF = {"title": "5 chess traps", "beginning": "open on the board",
         "middle": "show trap 1", "ending": "checkmate reveal"}


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(palo_flags, "PALO_PORT", True)
    monkeypatch.setattr(palo_flags, "WRITE_AGENT", True)


def test_flag_off_assembles_from_beats():
    out = _run(wa.script_from_brief(None, "c1", BRIEF))
    assert out["mode"] == "off" and out["title"] == "5 chess traps"
    assert out["body"] == "open on the board\nshow trap 1\ncheckmate reveal"


def test_keyless_mock_from_beats(on):
    out = _run(wa.script_from_brief(None, "c1", BRIEF))
    assert out["mode"] == "mock" and "checkmate reveal" in out["body"]


def test_summary_only_brief():
    out = _run(wa.script_from_brief(None, "c1", {"title": "T", "summary": "just a summary"}))
    assert out["body"] == "just a summary"


def test_live_uses_llm_script(on, monkeypatch):
    async def fake_json(system, user, schema, model, max_tokens=0, temperature=None):
        return {"title": "5 Chess Traps That Always Work", "script": "Here is the full script."}
    monkeypatch.setattr(wa, "anthropic_cached_json", fake_json)
    out = _run(wa.script_from_brief(None, "c1", BRIEF))
    assert out["mode"] == "live" and out["body"] == "Here is the full script."
    assert out["title"] == "5 Chess Traps That Always Work"


def test_route_returns_script():
    out = _run(main.script_from_brief_route(main._BriefScriptRequest(creator_id="c1", brief=BRIEF)))
    assert "checkmate reveal" in out["body"]                # flag off -> assembled, still usable
