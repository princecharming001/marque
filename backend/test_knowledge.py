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


# P5 regression guard: a KB edit that pushes a digest right up against the trim
# boundary fails SILENTLY — whatever file is LAST in that call's _CALL_FILES list
# just loses content, with no error, and the only way it surfaces is by accident
# (test_brief_digest_routes_hook_visual happened to assert specific tail content).
# This asserts real headroom directly so the NEXT KB edit that eats into it fails
# here, with a clear message, instead of as a confusing "hook_visual content
# missing" mystery two files removed from the one actually edited.
_MIN_DIGEST_MARGIN_CHARS = 200


def test_digest_has_headroom_before_trim_boundary():
    styles = ["talking_head", "green_screen", "broll_cutaway", "split_three",
             "duet_split", "faceless", "fast_cuts"]
    budget_chars = kb._MAX_TOKENS * kb._CHARS_PER_TOKEN
    tight = []
    for call in ("brief", "edit_plan", "review"):
        for style in styles:
            d = kb.digest(style, "", call)
            margin = budget_chars - len(d)
            if margin < _MIN_DIGEST_MARGIN_CHARS:
                tight.append(f"{call}/{style}: margin={margin}")
    assert not tight, f"digest(s) within {_MIN_DIGEST_MARGIN_CHARS} chars of the trim boundary: {tight}"


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


# --- UX-E2/E3: KB v2 routing + budgets ------------------------------------------

def test_kb_v2_version():
    kb._read.cache_clear(); kb.knowledge_version.cache_clear()
    assert kb.knowledge_version() == "kb-2026.09"


def test_edit_plan_digest_routes_playbook_and_transitions():
    for style, key in kb._STYLE_TO_FORMAT.items():
        d = kb.digest(style, "entertainment", "edit_plan")
        assert f"FORMAT PLAYBOOK ({key})" in d, f"{style} missing its playbook"
    assert "transition" in kb.digest("talking_head", "", "edit_plan").lower()


def test_brief_digest_routes_hook_visual():
    d = kb.digest("talking_head", "", "brief")
    assert "hook" in d.lower() and "Frame 1" in d


def test_review_digest_routes_sound_design():
    d = kb.digest("talking_head", "", "review")
    assert "SFX" in d and "rubric" in d.lower()


def test_v2_token_budgets_hold_all_calls():
    for call in ("brief", "edit_plan", "review"):
        for style in kb._STYLE_TO_FORMAT:
            d = kb.digest(style, "education", call)
            assert len(d) // 4 <= kb._MAX_TOKENS + 50, f"{call}/{style} over budget"
