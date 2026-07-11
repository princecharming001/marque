"""Tests for the editing knowledge base loader (app/knowledge.py) — Phase 2."""
from __future__ import annotations

import prompts
from app import knowledge as kb


def test_manifest_version_loads():
    v = kb.knowledge_version()
    assert v and v.startswith("kb-")


def test_pacing_row_selects_by_video_type():
    edu = kb._pacing_row("education")
    ent = kb._pacing_row("entertainment")
    assert "education" in edu and "60" in edu
    assert "entertainment" in ent and "30" in ent
    # unknown falls back to default row, never empty
    assert kb._pacing_row("nonsense").startswith("default")


def test_style_note_matches_style():
    assert kb._style_note("faceless").startswith("faceless")
    assert kb._style_note("fast_cuts").startswith("fast_cuts")
    assert kb._style_note("nonexistent") == ""


def test_digest_within_token_budget_and_call_scoped():
    for call, must in (("brief", "retention"), ("edit_plan", "pacing"), ("review", "rubric")):
        d = kb.digest("talking_head", "education", call)
        assert must.lower() in d.lower(), f"{call} digest missing {must}"
        assert len(d) // 4 <= kb._MAX_TOKENS + 50  # ~token budget
    # craft numbers present (they live ONLY in the KB)
    assert "-14 LUFS" in kb.digest("talking_head", "education", "brief") or \
           "LUFS" in kb._read("audio")


def test_edit_plan_digest_includes_pacing_for_video_type():
    d = kb.digest("faceless", "entertainment", "edit_plan")
    assert "PACING for video_type=entertainment" in d
    assert "STYLE pacing for faceless" in d


def test_edl_prompt_embeds_kb_block():
    words = [{"word": "hello", "start_ms": 0, "end_ms": 300}]
    script = {"hook": "h", "body": "b", "cta": "c", "formatId": "myth-buster", "shotPlan": []}
    system, _ = prompts.edl_prompt("talking_head", words, script, {}, brief={"video_type": "education"})
    assert "EDITING KNOWLEDGE BASE" in system
    assert "PACING for video_type=education" in system


def test_edit_brief_prompt_embeds_kb_block():
    words = [{"word": "hi", "start_ms": 0, "end_ms": 300}]
    system, _ = prompts.edit_brief_prompt(words)
    assert "EDITING KNOWLEDGE BASE" in system
    # brief call pulls retention + hooks
    assert "retention" in system.lower() and "hook" in system.lower()
