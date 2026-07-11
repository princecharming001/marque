"""Tests for the EDL invariant harness (eval/edl_eval.py) — Phase 5a.

Gates the harness itself the same way test_eval.py gates the script harness: every
fixture's reference EDL must pass all invariants clean, and every crafted defect must be
caught by its named invariant. If a future assembler regresses, these fire in CI.
"""
from __future__ import annotations

import pytest

from eval import edl_eval, edit_golden
from eval.edit_fixtures import FIXTURES


def test_fixtures_cover_all_five_categories():
    cats = {f["category"] for f in FIXTURES}
    assert cats == {"scripted", "rambling", "listicle", "low-energy", "buried-hook"}
    assert 5 <= len(FIXTURES) <= 12


def test_self_check_passes_keyless():
    ok, errs = edl_eval.self_check()
    assert ok, f"edl_eval self-check failed: {errs}"


@pytest.mark.parametrize("g", edit_golden.known_good(), ids=lambda g: g["id"])
def test_reference_edls_pass_all_invariants(g):
    r = edl_eval.evaluate_edl(g["edl"], g["words"], g["hook_ms"])
    assert r["failures"] == [], f"{g['id']} reference EDL failed invariants: {r['failures']}"


@pytest.mark.parametrize("b", edit_golden.known_bad(), ids=lambda b: b["code"])
def test_known_bad_each_caught(b):
    code = b["code"]
    if "plan" in b:
        plan = b["plan"]
        total_out = plan.get("total_frames", 0)
        fails = edl_eval.check_no_slivers(plan) + edl_eval.check_broll_grammar(plan, total_out)
    else:
        r = edl_eval.evaluate_edl(b["edl"], b["words"], b.get("hook_ms", 0), b.get("total_override"))
        fails = r["failures"]
    assert any(code in f for f in fails), f"{code} ({b['why']}) not caught — {fails}"


def test_buried_hook_reference_pulls_hook_forward():
    """The reference edit for the buried-hook take must land the hook within 3s."""
    fx = next(f for f in FIXTURES if f["category"] == "buried-hook")
    edl = edit_golden.reference_edl(fx)
    plan = edl_eval.build_render_plan(edl)
    from app.edl import ms_to_frame
    out = edl_eval._map_source_to_output(plan, ms_to_frame(fx["hook_ms"]))
    assert out is not None and out <= edl_eval.HOOK_MAX_OUT_FRAMES


def test_live_scorecard_noop_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import asyncio
    report = asyncio.run(edl_eval._live_scorecard())
    assert report.get("skipped") is True
