"""Tests for the EDL invariant harness (eval/edl_eval.py) — Phase 5a.

Gates the harness itself the same way test_eval.py gates the script harness: every
fixture's reference EDL must pass all invariants clean, and every crafted defect must be
caught by its named invariant. If a future assembler regresses, these fire in CI.
"""
from __future__ import annotations

import pytest

from eval import edl_eval, edit_golden
from eval.edit_fixtures import FIXTURES


def test_fixtures_cover_all_categories():
    cats = {f["category"] for f in FIXTURES}
    # original Phase 5a five, plus stutter-heavy (word-repeat + discourse phrase +
    # lexicon filler) and long-pause (2+ dead-air gaps of 2s+) added for the
    # residual-filler invariant / author tier.
    assert cats == {"scripted", "rambling", "listicle", "low-energy", "buried-hook",
                     "stutter-heavy", "long-pause"}
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


def test_stutter_heavy_reference_drops_repeat_filler_and_phrase():
    """The reference edit for stutter-heavy-01 must cut the word-repeat stutter
    (only the first "I" survives dropped, not both), the lexicon filler ("um"),
    and the "you know" discourse phrase — none of them may linger as captions."""
    fx = next(f for f in FIXTURES if f["category"] == "stutter-heavy")
    edl = edit_golden.reference_edl(fx)
    cap_words = [c["word"] for c in edl["captions"]]
    assert "um" not in cap_words
    assert "you" not in cap_words
    assert "know" not in cap_words
    assert cap_words.count("I") == 1   # the repeat collapses to a single "I"


def test_long_pause_reference_covers_both_long_gaps():
    """The reference edit for long-pause-01 must have dead_air drops tightening
    both 2s+ pauses (and nothing else — no fillers/stutters in this fixture)."""
    fx = next(f for f in FIXTURES if f["category"] == "long-pause")
    edl = edit_golden.reference_edl(fx)
    dead_air_drops = [d for d in edl["drops"] if d["reason"] == "dead_air"]
    assert len(dead_air_drops) >= 2
    assert all(d["reason"] == "dead_air" for d in edl["drops"])


def test_check_residual_filler_clean_on_every_golden():
    """check_residual_filler must never fire on a reference (known-good) EDL —
    strip_fillers/strip_fillers_v2 already unconditionally guarantee both
    conditions it checks, for every fixture."""
    for g in edit_golden.known_good():
        r = edl_eval.evaluate_edl(g["edl"], g["words"], g["hook_ms"])
        assert r["plan"] is not None, g["id"]
        assert edl_eval.check_residual_filler(r["plan"], g["words"]) == [], g["id"]


def test_check_residual_filler_catches_the_crafted_case():
    bad = next(b for b in edit_golden.known_bad() if b["code"] == "residual_filler")
    r = edl_eval.evaluate_edl(bad["edl"], bad["words"], bad["hook_ms"])
    assert any("residual_filler" in f for f in r["failures"])
    # and it's caught for the reason we crafted, not some unrelated invariant
    assert any("um" in f for f in r["failures"])


def test_author_tier_passes_keyless():
    """--author: assemble_edl called fresh (mostly-empty plan) per fixture must pass
    every invariant check — proves the invariants gate the LIVE authoring code path,
    not just the frozen edit_golden reference EDL."""
    report = edl_eval.run_author_tier()
    assert report["ok"], [r for r in report["rows"] if not r["ok"]]
    assert len(report["rows"]) == len(FIXTURES)


def test_author_fresh_is_deterministic():
    """author_fresh must be byte-stable across repeated calls (no wall-clock/random
    in assemble_edl) since it has to run keyless/reproducibly in CI."""
    fx = FIXTURES[0]
    assert edl_eval.author_fresh(fx) == edl_eval.author_fresh(fx)


def test_live_scorecard_noop_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import asyncio
    report = asyncio.run(edl_eval._live_scorecard())
    assert report.get("skipped") is True


def test_live_scorecard_author_source_noop_without_key(monkeypatch):
    """The --author --live combination must also no-op cleanly keyless."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import asyncio
    report = asyncio.run(edl_eval._live_scorecard(source="author"))
    assert report.get("skipped") is True
