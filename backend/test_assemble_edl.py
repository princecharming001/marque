"""Phase 3: assemble_edl + check_edl_invariants + EDIT_PLAN_JSON_SCHEMA (keyless)."""
from __future__ import annotations

import asyncio

import prompts
from app.edl import (assemble_edl, check_edl_invariants, build_render_plan, ms_to_frame,
                     strip_fillers)
from eval.edit_fixtures import FIXTURES, fixture
from eval import edl_eval


def _run(coro):
    return asyncio.run(coro)


def _words(fid):
    return fixture(fid)["words"]


# --- assembler guarantees -----------------------------------------------------

def test_empty_plan_yields_whole_take_default():
    w = _words("scripted-01")
    edl = assemble_edl({}, w, "talking_head", "myth-buster")
    d = edl.model_dump()
    assert d["segments"] and d["segments"][0]["src_in"] == 0
    # captions derived from cleaned words, NOT the (empty) plan
    kept, _ = strip_fillers(w)
    assert len(d["captions"]) == len([x for x in kept if x.get("word")])


def test_captions_always_from_clean_words_even_if_plan_has_none():
    w = _words("rambling-01")
    plan = {"caption_plan": {"style": "karaoke", "grouping": "phrase", "highlight_words": ["secret"]}}
    edl = assemble_edl(plan, w, "talking_head", "myth-buster")
    d = edl.model_dump()
    kept, _ = strip_fillers(w)
    assert len(d["captions"]) == len([x for x in kept if x.get("word")])
    assert d["caption_style"] == "karaoke"
    assert d["caption_options"]["highlight_words"] == ["secret"]


def test_filler_drops_always_present():
    w = _words("rambling-01")
    edl = assemble_edl({"cuts": []}, w, "talking_head", "myth-buster")
    d = edl.model_dump()
    assert any(dr["reason"] == "filler" for dr in d["drops"])  # deterministic fillers win


def test_broll_grammar_enforced():
    w = _words("listicle-01")
    total = ms_to_frame(w[-1]["end_ms"])
    # ask for a b-roll over the hook (f10) and an over-long hold — assembler must fix/drop
    plan = {"broll": [
        {"range": [10, 40], "cue": "hook", "query": "x", "source": "stock"},         # over hook → dropped
        {"range": [200, 400], "cue": "tool", "query": "laptop", "source": "stock"},  # 200f hold → clamped
    ]}
    edl = assemble_edl(plan, w, "faceless", "listicle")
    d = edl.model_dump()
    for b in d["broll"]:
        hold = b["src_out"] - b["src_in"]
        assert 60 <= hold <= 90, f"hold {hold} out of grammar"


def test_buried_hook_pulled_forward_passes_invariant():
    fx = fixture("buried-hook-01")
    hook_f = ms_to_frame(fx["hook_ms"])
    plan = {"open_on": {"start": hook_f, "end": hook_f + 60, "why": "the real payoff"}}
    edl = assemble_edl(plan, fx["words"], "talking_head", "myth-buster")
    plan_out = build_render_plan(edl.model_dump())
    out = edl_eval._map_source_to_output(plan_out, hook_f)
    assert out is not None and out <= edl_eval.HOOK_MAX_OUT_FRAMES


def test_loop_friendly_trailing_trim():
    # a keep that extends well past the last spoken word must be trimmed to ≤10 trailing frames
    w = [{"word": f"w{i}", "start_ms": i * 300, "end_ms": i * 300 + 250} for i in range(15)]
    last_end = ms_to_frame(w[-1]["end_ms"])
    edl = assemble_edl({"keeps": [[0, last_end + 200]]}, w, "talking_head", "x")
    tail = edl.model_dump()["segments"][-1]
    assert tail["src_out"] - last_end <= 10


def test_prefs_disable_broll_and_punchins():
    w = _words("scripted-01")
    plan = {"broll": [{"range": [200, 260], "cue": "c", "query": "q", "source": "stock"}],
            "punch_ins": [{"frame": 150, "scale": 1.08, "why": "key"}]}
    edl = assemble_edl(plan, w, "talking_head", "myth-buster",
                       prefs={"broll": False, "punch_ins": False})
    d = edl.model_dump()
    assert d["broll"] == []
    assert not any(o["type"] == "punch_in" for o in d["overlays"])


def test_assembled_edls_pass_edl_eval_invariants():
    # A competent plan opens on the hook (identity for hook-at-0 takes; pulls forward the
    # buried hook). The empty-plan default is a safe whole-take cut and legitimately can't
    # rescue a buried hook — that's the LLM's editorial job, covered by the test above.
    for fx in FIXTURES:
        hook_f = ms_to_frame(fx["hook_ms"]) if fx.get("hook_ms") else 0
        plan = {"open_on": {"start": hook_f, "end": hook_f + 60, "why": "hook"}} if hook_f else {}
        edl = assemble_edl(plan, fx["words"], fx["style"], "reel")
        r = edl_eval.evaluate_edl(edl.model_dump(), fx["words"], fx.get("hook_ms") or 0)
        assert r["failures"] == [], f"{fx['id']}: {r['failures']}"


# --- invariant checker --------------------------------------------------------

def test_check_edl_invariants_clean_edl():
    w = _words("scripted-01")
    edl = assemble_edl({}, w, "talking_head", "myth-buster")
    assert check_edl_invariants(edl.model_dump(), w) == []


def test_check_edl_invariants_catches_bad_segment():
    w = _words("scripted-01")
    bad = {"style": "talking_head", "segments": [{"src_in": 100, "src_out": 50}],
           "layout": {"style": "talking_head"}}
    issues = check_edl_invariants(bad, w)
    assert any("src_out<=src_in" in i for i in issues)


def test_check_edl_invariants_catches_react_on_nonduet():
    w = _words("scripted-01")
    edl = assemble_edl({}, w, "talking_head", "myth-buster").model_dump()
    edl["react_schedule"] = [{"state": "play", "src_in": 0, "src_out": 30, "clip_from": 0, "audio_gain": 1.0}]
    assert any("react" in i for i in check_edl_invariants(edl, w))


# --- schema strictness --------------------------------------------------------

def test_edit_plan_schema_strict():
    def _strict(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                props = node.get("properties", {})
                assert set(node.get("required", [])) == set(props.keys())
                for v in props.values():
                    _strict(v)
            if node.get("type") == "array":
                _strict(node.get("items", {}))
    _strict(prompts.EDIT_PLAN_JSON_SCHEMA)


def test_edit_plan_prompt_builds():
    w = _words("scripted-01")
    script = {"hook": "h", "body": "b", "cta": "c", "formatId": "myth-buster", "shotPlan": []}
    system, user = prompts.edit_plan_prompt("talking_head", w, script, {}, brief={"video_type": "education"})
    assert "EDIT PLAN" in system and "EDITING KNOWLEDGE BASE" in system
    assert "transcript" in user.lower()


# --- authoring path -----------------------------------------------------------

def test_author_via_plan_keyless_produces_valid_edl(monkeypatch):
    import main
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")   # keyless → empty plan → whole-take default
    job = {"brand": {}, "edit_brief": None, "dossier": None, "reference_reel": None,
           "custom_instructions": ""}
    w = _words("scripted-01")
    script = {"formatId": "myth-buster"}
    edl_data, llm_contributed = _run(main._author_edl_via_plan(job, "talking_head", script, w, {}, None))
    assert edl_data is not None
    assert llm_contributed is False   # keyless → empty plan → generic whole-take cut
    assert check_edl_invariants(edl_data, w) == []
