"""T2 (superintelligence epic) — live all-paths scorecard (docs/07-ai-system.md §8.5
extension). run_eval.py's `_live()` only exercises /v1/scripts; this generates through
every structurally script-shaped path DIRECTLY (no HTTP server — same pattern as
run_eval.py, calling the real handler functions so this is testing the ACTUAL production
code, not a re-implementation of it) and scores each one against a single, harder bar:
zero speakability violations, a real gate-pass floor, and judge relevance/voice means.

Paths covered: scripts (best-of-N quality pipeline), feed_fast (lean first-paint), steer
(refine-in-place), mimic (reel-inspired). All four return a script (or scripts) shaped
compatible with eval.invariants.evaluate_batch — {hook, body, cta, formatId, hookSignal,
style, predictedScore, altHooks}.

Deliberately NOT covered (documented scope decision, not an oversight): from_brief and
write_turn (app/write_agent.py) return prose-shaped output ({title, body} / typed edit
actions) that doesn't share evaluate_batch's script schema — forcing them into it would
mean synthesizing fake formatId/hookSignal/style values just to pass the gate check,
which risks silently masking real bugs rather than catching them. analyze_video/converse
are likewise deferred. A future pass can add a SEPARATE prose-scoring path (speakability_
report + judge only, no gate) for those three without touching this file's contract.

Run:
    cd backend && python3 -m eval.path_eval          # keyless: shape check only, no cost
    ANTHROPIC_API_KEY=... python3 -m eval.path_eval  # live: full scorecard + regression gate
"""
from __future__ import annotations

import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval import golden
from eval.invariants import evaluate_batch

N_PER_PATH = 3

# Hard exit thresholds (T2 spec) — stricter than run_eval.py's per-CASE thresholds
# because this pools every path together: a single path silently regressing must not
# get averaged away by three healthy ones.
MIN_GATE_PASS_RATE = 0.90
MIN_RELEVANCE_MEAN = 65.0
MIN_VOICE_MATCH_MEAN = 65.0


async def _path_scripts() -> list[dict]:
    import main
    req = main.ScriptRequest(**{k: golden.EVAL_BRAND.get(k) for k in
                                ("niche", "audience", "known_for", "what_you_do", "goal",
                                 "voice", "non_negotiables", "catchphrases") if golden.EVAL_BRAND.get(k) is not None},
                             pillar="Cost-per-wear breakdowns", pillar_summary="Make a piece's true cost visible",
                             pillar_angle="You do the cost-per-wear math nobody else bothers with",
                             style="talking_head", count=N_PER_PATH, posts=golden.EVAL_POSTS,
                             creator_id="patheval-scripts")
    res = await main.scripts(req)
    return res.get("scripts", [])


async def _path_feed_fast() -> list[dict]:
    import main
    req = main.ScriptRequest(**{k: golden.EVAL_BRAND.get(k) for k in
                                ("niche", "audience", "known_for", "what_you_do", "goal",
                                 "voice", "non_negotiables", "catchphrases") if golden.EVAL_BRAND.get(k) is not None},
                             pillar="Thrift finds", pillar_summary="Spotlight one great secondhand piece",
                             pillar_angle="You make one thrift find feel like a heist",
                             style="talking_head", count=N_PER_PATH, posts=golden.EVAL_POSTS,
                             creator_id="patheval-feedfast")
    res = await main._fast_feed_scripts(req)
    return res.get("scripts", [])


async def _path_steer() -> list[dict]:
    import main
    draft = {"hook": "You're buying too many clothes.", "hookSignal": "callOut",
            "formatId": "myth-buster", "body": "Fast fashion is designed to fall apart after a season.",
            "cta": "Follow.", "predictedScore": 60, "style": "talking_head", "altHooks": []}
    out: list[dict] = []
    for i in range(N_PER_PATH):
        req = main.SteerRequest(**golden.EVAL_BRAND, script=draft,
                                instruction="make the hook sharper and more specific",
                                creator_id=f"patheval-steer-{i}")
        res = await main.steer(req)
        if res.get("script"):
            out.append(res["script"])
    return out


async def _path_mimic() -> list[dict]:
    import main
    reel = {"id": "eval-reel-1", "creator_handle": "thriftqueen", "platform": "tiktok",
           "transcript": "This blazer cost twelve dollars and I've worn it forty times. "
                         "That's the real math fast fashion doesn't want you doing.",
           "hook_text": "The math fast fashion doesn't want you doing."}
    out: list[dict] = []
    for i in range(N_PER_PATH):
        req = main.MimicRequest(reel=reel, brand=golden.EVAL_BRAND, memory={},
                                creator_id=f"patheval-mimic-{i}")
        res = await main.mimic(req)
        if res.get("script"):
            out.append(res["script"])
    return out


PATHS: dict[str, "callable"] = {
    "scripts": _path_scripts,
    "feed_fast": _path_feed_fast,
    "steer": _path_steer,
    "mimic": _path_mimic,
}


def _keyless_shape_check() -> tuple[bool, list[str]]:
    """No API key: run every path in mock mode and assert it returns the expected
    list-of-script shape. Catches route-signature drift on every commit for free —
    the same value a live run gives, without the cost."""
    errs = []
    for name, fn in PATHS.items():
        try:
            scripts = asyncio.run(fn())
        except Exception as e:
            errs.append(f"{name}: raised {type(e).__name__}: {e}")
            continue
        if not isinstance(scripts, list):
            errs.append(f"{name}: expected a list, got {type(scripts).__name__}")
            continue
        for s in scripts:
            if not isinstance(s, dict) or "hook" not in s or "body" not in s:
                errs.append(f"{name}: script missing hook/body: {s!r}"[:200])
                break
    return (not errs), errs


async def _live_scorecard() -> dict:
    import main
    import prompts

    per_path: dict[str, dict] = {}
    all_scripts: list[dict] = []
    all_verdicts: list[dict] = []
    speakability_violations = 0

    for name, fn in PATHS.items():
        scripts = await fn()
        card = evaluate_batch(scripts, golden.EVAL_BRAND)
        for s in scripts:
            v = prompts.speakability_report(s)
            speakability_violations += len(v.get("violations", []))
        per_path[name] = card
        all_scripts.extend(scripts)

        if scripts and main.ANTHROPIC_KEY:
            try:
                jsys, jusr = prompts.script_judge_prompt(
                    scripts, "talking_head", brand=golden.EVAL_BRAND, posts=golden.EVAL_POSTS)
                verdicts = main.extract_json(await main.anthropic(jsys, jusr, main.HAIKU, 1600), array=True) or []
                all_verdicts.extend({**v, "_path": name} for v in verdicts if isinstance(v, dict))
            except Exception as e:
                per_path[name] = {**per_path[name], "judge_error": f"{type(e).__name__}: {e}"}

    judge: dict = {}
    if all_verdicts:
        for axis in ("hook_strength", "specificity", "voice_match", "relevance_to_creator"):
            vals = [float(v[axis]) for v in all_verdicts if axis in v]
            if vals:
                judge[axis] = round(sum(vals) / len(vals), 1)

    overall = evaluate_batch(all_scripts, {})
    return {"per_path": per_path, "overall": overall, "judge": judge,
           "speakability_violations": speakability_violations}


def main_entry() -> int:
    print("=" * 64)
    print("MARQUE AI EVAL — T2 all-paths scorecard")
    print("=" * 64)
    ok, errs = _keyless_shape_check()
    print(f"  paths: {list(PATHS.keys())}")
    print(f"  keyless shape check: {'PASS ✓' if ok else 'FAIL ✗'}")
    for e in errs:
        print("   -", e)

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    gate_ok = ok
    if not has_key:
        print("\n  (no ANTHROPIC_API_KEY — skipping live scorecard)")
    else:
        print("\n" + "=" * 64)
        print("LIVE all-paths scorecard")
        print("=" * 64)
        result = asyncio.run(_live_scorecard())
        for name, card in result["per_path"].items():
            print(f"  {name:12} gate={card['gate_pass_rate']:.2f} "
                 f"flags={card['quality_flag_rate']:.2f} (n={card['n']})")
        ov = result["overall"]
        print(f"\n  OVERALL gate_pass_rate={ov['gate_pass_rate']:.2f} (n={ov['n']})")
        print(f"  speakability_violations={result['speakability_violations']}")
        if result["judge"]:
            print(f"  JUDGE {result['judge']}")

        regress = []
        if result["speakability_violations"] > 0:
            regress.append(f"speakability_violations {result['speakability_violations']} > 0")
        if ov["gate_pass_rate"] < MIN_GATE_PASS_RATE:
            regress.append(f"gate_pass_rate {ov['gate_pass_rate']:.2f} < {MIN_GATE_PASS_RATE}")
        rel = result["judge"].get("relevance_to_creator")
        if rel is not None and rel < MIN_RELEVANCE_MEAN:
            regress.append(f"relevance_to_creator {rel} < {MIN_RELEVANCE_MEAN}")
        voice = result["judge"].get("voice_match")
        if voice is not None and voice < MIN_VOICE_MATCH_MEAN:
            regress.append(f"voice_match {voice} < {MIN_VOICE_MATCH_MEAN}")
        for r in regress:
            print("   REGRESSION:", r)
        gate_ok = gate_ok and not regress

    print("\n" + ("RESULT: PASS ✓" if gate_ok else "RESULT: FAIL ✗ (regression gate)"))
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main_entry())
