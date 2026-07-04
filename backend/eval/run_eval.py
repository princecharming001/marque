"""Offline eval harness + regression gate (docs/07-ai-system.md §8.5).

Run before shipping a prompt or schema change:

    cd backend && python3 -m eval.run_eval          # keyless self-check
    ANTHROPIC_API_KEY=... python3 -m eval.run_eval  # live generation scorecard

Keyless mode validates the harness itself against the golden set (known-good must
pass every gate, known-bad must be caught) — a fast CI-safe tripwire with no API
cost. With a key it also generates against the CASES fixtures through the REAL
pipeline (scripts() → quality gate), runs the deterministic invariants, and adds
an independent LLM judge (§8.2: hook / specificity / voice, separate criteria).

Exit code is non-zero on regression so CI can gate the deploy.
"""
from __future__ import annotations

import os
import sys
import asyncio

# Allow both `python3 -m eval.run_eval` and `python3 eval/run_eval.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import golden
from eval.invariants import evaluate_script, evaluate_batch

# Regression thresholds — a change that pushes below these fails the gate.
MIN_GATE_PASS_RATE = 0.90
MAX_QUALITY_FLAG_RATE = 0.20
MIN_KNOWN_BAD_CATCH = 1.0        # every known-bad must be caught


def _self_check() -> tuple[bool, list[str]]:
    """Golden-set tripwire: known-good all pass clean, known-bad all get caught."""
    errs = []
    for i, g in enumerate(golden.KNOWN_GOOD):
        r = evaluate_script(g["script"], g["brand"])
        if not r["gate_passed"]:
            errs.append(f"KNOWN_GOOD[{i}] should pass but failed: {r['failures']}")
        if r["quality_flags"]:
            errs.append(f"KNOWN_GOOD[{i}] should be clean but flagged: {r['quality_flags']}")
    caught = 0
    for i, b in enumerate(golden.KNOWN_BAD):
        r = evaluate_script(b["script"], b["brand"])
        hit_gate = b.get("expect_gate") and any(b["expect_gate"] in f for f in r["failures"])
        hit_flag = b.get("expect_flag") and any(b["expect_flag"] in f for f in r["quality_flags"])
        if hit_gate or hit_flag:
            caught += 1
        else:
            errs.append(f"KNOWN_BAD[{i}] ({b['why']}) NOT caught — failures={r['failures']} flags={r['quality_flags']}")
    catch_rate = caught / (len(golden.KNOWN_BAD) or 1)
    if catch_rate < MIN_KNOWN_BAD_CATCH:
        errs.append(f"known-bad catch rate {catch_rate:.2f} < {MIN_KNOWN_BAD_CATCH}")
    return (not errs), errs


async def _live() -> dict:
    """Generate against the CASES fixtures through the real pipeline + judge."""
    import main
    import prompts

    all_scripts, per_case = [], []
    for c in golden.CASES:
        req = main.ScriptRequest(
            **{k: c["brand"].get(k) for k in (
                "niche", "audience", "known_for", "what_you_do", "goal",
                "voice", "non_negotiables", "catchphrases") if c["brand"].get(k) is not None},
            pillar=c["pillar"], pillar_summary=c["pillar_summary"],
            pillar_angle=c["pillar_angle"], style=c["style"], count=c["count"],
            posts=c.get("posts") or [], creator_id=f"eval-{c['id']}",
        )
        res = await main.scripts(req)
        scripts = res.get("scripts", [])
        card = evaluate_batch(scripts, c["brand"])
        per_case.append((c["id"], res.get("mode"), card))
        all_scripts.extend(scripts)

    # Independent judge pass (§8.2) — mean hook/specificity/voice across everything.
    judge = {}
    if all_scripts and main.ANTHROPIC_KEY:
        try:
            jsys, jusr = prompts.script_judge_prompt(all_scripts, "talking_head")
            verdicts = main.extract_json(await main.anthropic(jsys, jusr, main.HAIKU, 1600), array=True) or []
            for axis in ("hook_strength", "specificity", "voice_match"):
                vals = [float(v[axis]) for v in verdicts if isinstance(v, dict) and axis in v]
                if vals:
                    judge[axis] = round(sum(vals) / len(vals), 1)
            slop = [1 for v in verdicts if isinstance(v, dict) and v.get("slop")]
            judge["slop_rate"] = round(len(slop) / (len(verdicts) or 1), 3)
        except Exception as e:                      # judging is best-effort; never crash the gate
            judge["error"] = f"{type(e).__name__}: {e}"

    overall = evaluate_batch(all_scripts, {})       # gate rate ignores per-brand banned words
    return {"per_case": per_case, "overall": overall, "judge": judge}


def main_entry() -> int:
    ok, errs = _self_check()
    print("=" * 64)
    print("MARQUE AI EVAL — golden-set self-check")
    print("=" * 64)
    print(f"  known-good: {len(golden.KNOWN_GOOD)}   known-bad: {len(golden.KNOWN_BAD)}")
    print(f"  self-check: {'PASS ✓' if ok else 'FAIL ✗'}")
    for e in errs:
        print("   -", e)

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    gate_ok = ok
    if not has_key:
        print("\n  (no ANTHROPIC_API_KEY — skipping live generation scorecard)")
    else:
        print("\n" + "=" * 64)
        print("LIVE generation scorecard")
        print("=" * 64)
        live = asyncio.run(_live())
        for cid, mode, card in live["per_case"]:
            print(f"  {cid:28} [{mode:5}] gate={card['gate_pass_rate']:.2f} "
                  f"flags={card['quality_flag_rate']:.2f} (n={card['n']})")
        ov = live["overall"]
        print(f"\n  OVERALL gate_pass_rate={ov['gate_pass_rate']:.2f} "
              f"quality_flag_rate={ov['quality_flag_rate']:.2f} (n={ov['n']})")
        if live["judge"]:
            print(f"  JUDGE {live['judge']}")
        regress = []
        if ov["gate_pass_rate"] < MIN_GATE_PASS_RATE:
            regress.append(f"gate_pass_rate {ov['gate_pass_rate']:.2f} < {MIN_GATE_PASS_RATE}")
        if ov["quality_flag_rate"] > MAX_QUALITY_FLAG_RATE:
            regress.append(f"quality_flag_rate {ov['quality_flag_rate']:.2f} > {MAX_QUALITY_FLAG_RATE}")
        for r in regress:
            print("   REGRESSION:", r)
        gate_ok = gate_ok and not regress

    print("\n" + ("RESULT: PASS ✓" if gate_ok else "RESULT: FAIL ✗ (regression gate)"))
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main_entry())
