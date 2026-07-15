"""T2 (superintelligence epic) — eval/path_eval.py's own keyless contract. Asserts
every PATHS entry returns the script shape evaluate_batch expects, in mock mode —
catches route-signature drift (a renamed field, a changed request model) on every
commit for free, exactly like the harness it extends.
"""
import asyncio

from eval import path_eval, golden
from eval.invariants import evaluate_batch


def test_keyless_shape_check_passes():
    ok, errs = path_eval._keyless_shape_check()
    assert ok, "path_eval keyless shape check failed:\n" + "\n".join(errs)


def test_every_path_returns_a_list_of_script_dicts():
    for name, fn in path_eval.PATHS.items():
        scripts = asyncio.run(fn())
        assert isinstance(scripts, list), name
        for s in scripts:
            assert isinstance(s, dict), name
            assert "hook" in s and "body" in s, f"{name}: missing hook/body in {s!r}"


def test_every_path_output_is_evaluate_batch_compatible():
    # Mock-mode output must at minimum survive evaluate_batch without raising —
    # the real correctness bar (gate_pass_rate, speakability==0) is the LIVE
    # scorecard's job; this is the free keyless floor.
    for name, fn in path_eval.PATHS.items():
        scripts = asyncio.run(fn())
        card = evaluate_batch(scripts, golden.EVAL_BRAND)
        assert card["n"] == len(scripts), name
        assert 0.0 <= card["gate_pass_rate"] <= 1.0, name


def test_main_entry_exits_zero_keyless():
    assert path_eval.main_entry() == 0


def test_eval_brand_and_posts_fixtures_are_well_formed():
    assert golden.EVAL_BRAND.get("niche")
    assert len(golden.EVAL_BRAND.get("catchphrases") or []) >= 3
    assert len(golden.EVAL_BRAND.get("non_negotiables") or []) >= 2
    assert len(golden.EVAL_POSTS) >= 6
    for p in golden.EVAL_POSTS:
        assert p.get("caption") and p.get("transcript")
        assert isinstance(p.get("likes"), int) and isinstance(p.get("comments"), int)
