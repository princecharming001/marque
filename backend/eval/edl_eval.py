"""EDL invariant + scorecard harness — the edit-side twin of eval/run_eval.py.

    cd backend && python3 -m eval.edl_eval             # keyless invariant self-check
    python3 -m eval.edl_eval --author                  # + fresh-authored tier (keyless)
    ANTHROPIC_API_KEY=... python3 -m eval.edl_eval --live   # + live scorecard (LLM judge)
    ANTHROPIC_API_KEY=... python3 -m eval.edl_eval --author --live  # fresh-authored + judge

Keyless mode (CI-safe, no API cost) proves two things:
  1. every fixture's reference EDL passes ALL invariants clean (known-good), and
  2. every crafted defect is caught by its invariant (known-bad tripwire).
These invariants gate whatever authors the EDL — the current path today, `assemble_edl`
after Phase 3 — because they assert on the render PLAN, not on who wrote it.

`--author` closes the one gap in that story: self_check() above only ever grades a
FROZEN golden (edit_golden.reference_edl), so a regression in assemble_edl itself (or
the retention passes layered on top of it) never moves that score. `--author`
re-authors each fixture fresh through the REAL deterministic path (assemble_edl with a
mostly-empty plan — the whole-take fallback) and runs it through the same invariants,
printing a per-fixture pass/fail table. Still fully keyless — assemble_edl is a pure
function of (plan, words), no network/model call.

Live mode additionally runs the full stack per fixture and scores each output with an
independent LLM judge against the KB review rubric, reporting hook-time / kept-ratio /
cut-cadence / judge-score per knowledge_version + prompt version, and gates on regression
thresholds (pattern: MIN_GATE_PASS_RATE). Live mode is a no-op (clean exit) without a key.
Combined with --author, the judge scores the fresh-authored plan instead of the golden.

Exit code is non-zero on any regression so CI can gate the deploy.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.edl import (
    ms_to_frame, strip_fillers, build_render_plan, EDL, MIN_CLIP_OUTPUT_FRAMES,
    ALWAYS_FILLERS, _norm_word, assemble_edl,
)
from eval import edit_golden
from eval.edit_fixtures import FIXTURES, take_total_frames

# --- Invariant thresholds (the ONLY place these live) -------------------------
HOOK_MAX_OUT_FRAMES = 90          # the hook must land within 3s of the output start
BROLL_MIN_HOLD, BROLL_MAX_HOLD = 45, 105   # 1.5–3.5s (2–3s target ± tolerance)
BROLL_MIN_SPACING = 90            # ≥3s between b-roll cutaways
HOOK_PROTECT_FRAMES = 90          # no b-roll over the speaker's hook (first 3s)
CTA_PROTECT_FRAMES = 60           # …or the CTA (last 2s)
CAPTION_COVERAGE_MIN = 0.90       # ≥90% of mappable kept words captioned
MIN_GATE_PASS_RATE = 0.90


def _map_source_to_output(plan: dict, f: int) -> int | None:
    """Source frame → output frame using the plan's clips (same math as edl.map_point)."""
    out_start = 0
    for c in plan.get("clips") or []:
        s_in, s_out = c["src_in"], c["src_out"]
        speed = c.get("speed") or 1.0
        out_len = max(1, round((s_out - s_in) / speed))
        if s_in <= f < s_out:
            return out_start + round((f - s_in) / speed)
        out_start += out_len
    return None


def _clip_out_len(c: dict) -> int:
    return max(1, round((c["src_out"] - c["src_in"]) / (c.get("speed") or 1.0)))


# --- Individual invariant checkers (each returns a list of failure strings) ----

def check_no_slivers(plan: dict) -> list[str]:
    clips = plan.get("clips") or []
    if len(clips) <= 1:
        return []   # a lone clip is the all-slivers fallback — legitimately allowed
    bad = [i for i, c in enumerate(clips) if _clip_out_len(c) < MIN_CLIP_OUTPUT_FRAMES]
    return [f"sliver: clip {i} is {_clip_out_len(clips[i])}f (< {MIN_CLIP_OUTPUT_FRAMES})" for i in bad]


def check_hook_timing(plan: dict, hook_ms: int, words: list[dict]) -> list[str]:
    # The "hook" is where the payoff lands. When a fixture marks it (buried-hook), use
    # that; otherwise the hook is the first KEPT word (the opening throat-clearing fillers
    # at source 0 are dropped, so literal frame 0 isn't meaningful).
    if hook_ms and hook_ms > 0:
        hook_frame = ms_to_frame(hook_ms)
    else:
        kept, _ = strip_fillers(words)
        hook_frame = ms_to_frame(kept[0]["start_ms"]) if kept else 0
    out = _map_source_to_output(plan, hook_frame)
    if out is None:
        return [f"hook_late: hook (source f{hook_frame}) was cut entirely"]
    if out > HOOK_MAX_OUT_FRAMES:
        return [f"hook_late: hook lands at output f{out} (> {HOOK_MAX_OUT_FRAMES})"]
    return []


def check_caption_coverage(plan: dict, words: list[dict]) -> list[str]:
    kept, _ = strip_fillers(words)
    cap_frames = {c["frame"] for c in plan.get("captions") or []}
    mappable = 0
    covered = 0
    for w in kept:
        of = _map_source_to_output(plan, ms_to_frame(w["start_ms"]))
        if of is None:
            continue   # this word was cut — not expected to be captioned
        mappable += 1
        if of in cap_frames or (of - 1) in cap_frames or (of + 1) in cap_frames:
            covered += 1
    if mappable == 0:
        return []
    ratio = covered / mappable
    if ratio < CAPTION_COVERAGE_MIN:
        return [f"caption_gap: {covered}/{mappable} kept words captioned ({ratio:.0%} < {CAPTION_COVERAGE_MIN:.0%})"]
    return []


def check_broll_grammar(plan: dict, total_out: int) -> list[str]:
    fails: list[str] = []
    brolls = sorted((b for b in plan.get("broll") or []), key=lambda b: b.get("frame_in", 0))
    prev_out = None
    for b in brolls:
        fi, fo = b.get("frame_in", 0), b.get("frame_out", 0)
        hold = fo - fi
        if hold < BROLL_MIN_HOLD or hold > BROLL_MAX_HOLD:
            fails.append(f"broll_hold: {hold}f hold outside [{BROLL_MIN_HOLD},{BROLL_MAX_HOLD}]")
        if fi < HOOK_PROTECT_FRAMES:
            fails.append(f"broll_hook: b-roll at f{fi} covers the hook (< {HOOK_PROTECT_FRAMES})")
        if total_out and fo > total_out - CTA_PROTECT_FRAMES:
            fails.append(f"broll_cta: b-roll ends at f{fo} inside the CTA (> {total_out - CTA_PROTECT_FRAMES})")
        if prev_out is not None and fi - prev_out < BROLL_MIN_SPACING:
            fails.append(f"broll_spacing: {fi - prev_out}f gap (< {BROLL_MIN_SPACING})")
        prev_out = fo
    return fails


def check_drops_within_take(edl: dict, total_source: int) -> list[str]:
    fails: list[str] = []
    for d in edl.get("drops") or []:
        if d["src_in"] < 0 or d["src_out"] > total_source or d["src_in"] >= d["src_out"]:
            fails.append(f"drop_out_of_take: drop [{d['src_in']},{d['src_out']}) outside [0,{total_source})")
    for s in edl.get("segments") or []:
        if s["src_in"] < 0 or s["src_out"] > total_source or s["src_in"] >= s["src_out"]:
            fails.append(f"drop_out_of_take: segment [{s['src_in']},{s['src_out']}) outside [0,{total_source})")
    return fails


def check_edl_valid(edl: dict) -> list[str]:
    try:
        EDL(**edl)
        return []
    except Exception as e:  # pydantic ValidationError etc.
        return [f"edl_invalid: {type(e).__name__}: {str(e).splitlines()[0][:120]}"]


def check_residual_filler(plan: dict, words: list[dict]) -> list[str]:
    """The final CAPTIONS (plan["captions"] — the output-coordinate word list
    Remotion actually burns in) must never carry a residual filler. This is the
    tripwire for retention.sweep_residual_fillers' whole job: an author that keeps
    (or a later pass that un-drops) an "um"/"uh" or a transcript-tagged filler word.

    Two independent conditions, matching strip_fillers' own unconditional
    guarantees so a clean assemble_edl/reference_edl run can never trip this:
      1. ALWAYS_FILLERS (um/uh) are unambiguous filler wherever they land as a
         caption — no cross-referencing needed, a direct text scan suffices.
      2. a SOURCE word the transcript tagged type=="filler" that still lands on a
         captioned output frame — cross-referenced by mapping that word's source
         frame to output (the same _map_source_to_output used by
         check_caption_coverage/check_hook_timing) and checking whether a caption
         sits at that exact output frame.
    """
    fails: list[str] = []
    captions = plan.get("captions") or []

    for c in captions:
        norm = _norm_word(c.get("word", ""))
        if norm in ALWAYS_FILLERS:
            fails.append(
                f"residual_filler: always-filler '{c.get('word')}' captioned at output f{c.get('frame')}")

    cap_frames = {c["frame"] for c in captions}
    for w in words:
        if w.get("type") != "filler":
            continue
        src_frame = ms_to_frame(w.get("start_ms", 0))
        out_frame = _map_source_to_output(plan, src_frame)
        if out_frame is not None and out_frame in cap_frames:
            fails.append(
                f"residual_filler: tagged-filler '{w.get('word')}' still captioned "
                f"at output f{out_frame} (source f{src_frame})")

    return fails


# --- Aggregate over a full EDL case -------------------------------------------

def evaluate_edl(edl: dict, words: list[dict], hook_ms: int, total_source: int | None = None) -> dict:
    total_source = total_source if total_source is not None else take_total_frames(words)
    validity = check_edl_valid(edl)
    if validity:
        # Can't build a plan from an invalid EDL; report the validity failure only.
        return {"failures": validity, "plan": None}
    plan = build_render_plan(edl)
    total_out = plan.get("total_frames", 0)
    failures = (
        check_no_slivers(plan)
        + check_hook_timing(plan, hook_ms, words)
        + check_caption_coverage(plan, words)
        + check_broll_grammar(plan, total_out)
        + check_drops_within_take(edl, total_source)
        + check_residual_filler(plan, words)
    )
    return {"failures": failures, "plan": plan}


# --- Keyless self-check (known-good pass clean, known-bad each caught) ---------

def self_check() -> tuple[bool, list[str]]:
    errs: list[str] = []

    for g in edit_golden.known_good():
        r = evaluate_edl(g["edl"], g["words"], g["hook_ms"])
        if r["failures"]:
            errs.append(f"KNOWN_GOOD[{g['id']}] should pass but failed: {r['failures']}")

    for b in edit_golden.known_bad():
        code = b["code"]
        if "plan" in b:
            # crafted plan → run the plan-level checkers directly
            plan = b["plan"]
            total_out = plan.get("total_frames", 0)
            fails = (check_no_slivers(plan)
                     + check_broll_grammar(plan, total_out))
        else:
            r = evaluate_edl(b["edl"], b["words"], b.get("hook_ms", 0), b.get("total_override"))
            fails = r["failures"]
        if not any(code in f for f in fails):
            errs.append(f"KNOWN_BAD[{code}] ({b['why']}) NOT caught — failures={fails}")

    return (not errs), errs


# --- Author tier: re-author FRESH through the real deterministic path ---------
#
# self_check() above only proves the invariants gate a FROZEN golden (edit_golden's
# hand-authored reference_edl). It never actually calls the code that authors an EDL
# from raw word-timings — so a regression in assemble_edl (or the retention passes
# layered on top of it) doesn't move that score at all. `--author` closes that gap:
# it calls assemble_edl fresh, per fixture, and runs the SAME invariant checks
# against the result.

def author_fresh(fx: dict) -> dict:
    """Author a FRESH EDL for one fixture through the REAL deterministic path —
    assemble_edl (the Phase 3 typed-plan assembler), called with a mostly-empty
    plan (no cuts/keeps/broll/open_on decisions — the documented whole-take
    fallback) — then build_render_plan on the result. Pure and keyless: assemble_edl
    only consumes the transcript words + a plan dict, never a network/model call."""
    edl_obj = assemble_edl({}, fx["words"], fx["style"], fx.get("format_id", "myth-buster"))
    return edl_obj.model_dump()


def run_author_tier() -> dict:
    """Re-author every fixture fresh (author_fresh) and run it through the SAME
    invariant checks self_check() runs against the frozen goldens — proving the
    checks actually gate the LIVE authoring code path, not just a fixed golden.

    hook_ms is deliberately NOT the fixture's annotated "real payoff" timestamp.
    That annotation encodes a semantic judgment (which line is the true hook) that
    only plan-level guidance (a real typed plan's `open_on`) can act on — see
    assemble_edl's open_on handling and test_assemble_edl.py's
    test_assembled_edls_pass_edl_eval_invariants, which makes the identical call
    with the identical comment: "The empty-plan default is a safe whole-take cut
    and legitimately can't rescue a buried hook — that's the LLM's editorial job."
    An empty plan gives assemble_edl no reorder/pull signal, so the fair structural
    claim to hold a bare authoring run to is check_hook_timing's own fallback: the
    earliest KEPT (non-filler) word lands up front — i.e. nothing in the
    deterministic scaffolding itself buries or reorders the opening. hook_ms=0
    asks exactly that question for every fixture, buried-hook included.
    """
    rows = []
    all_ok = True
    for fx in FIXTURES:
        edl_dict = author_fresh(fx)
        r = evaluate_edl(edl_dict, fx["words"], 0)
        ok = not r["failures"]
        all_ok = all_ok and ok
        rows.append({"id": fx["id"], "category": fx["category"], "ok": ok, "failures": r["failures"]})
    return {"ok": all_ok, "rows": rows}


# --- Live scorecard (no-op keyless) -------------------------------------------

async def _live_scorecard(source: str = "reference") -> dict:
    """Full-stack per-fixture scorecard with an independent LLM judge.

    Requires ANTHROPIC_API_KEY (+ Supabase eval bucket for source video). Without a key
    this returns a clean no-op so CI stays green. The judge scores each rendered plan
    against knowledge/review_rubric.md and reports metrics per knowledge_version +
    prompt version so KB/prompt changes are A/B-able (regression gate: MIN_GATE_PASS_RATE).

    `source="reference"` (default) scores the frozen edit_golden.reference_edl golden,
    same as before. `source="author"` (the `--author --live` combination) instead
    scores a FRESH assemble_edl authoring run (author_fresh) — same tier as the
    keyless --author check, plus the live LLM judge on top. Since author_fresh's
    empty plan can't carry a semantic hook decision, hook_ms is 0 (see
    run_author_tier's docstring) rather than the fixture's annotated payoff.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"skipped": True, "reason": "no ANTHROPIC_API_KEY — keyless no-op"}

    use_author = source == "author"
    rows = []
    for fx in FIXTURES:
        edl = author_fresh(fx) if use_author else edit_golden.reference_edl(fx)
        hook_ms = 0 if use_author else (fx.get("hook_ms") or 0)
        r = evaluate_edl(edl, fx["words"], hook_ms)
        plan = r["plan"] or {}
        clips = plan.get("clips") or []
        kept_ratio = round(plan.get("total_frames", 0) / max(1, take_total_frames(fx["words"])), 3)
        cadence = round(plan.get("total_frames", 0) / max(1, len(clips)), 1)
        hook_out = _map_source_to_output(plan, ms_to_frame(hook_ms))
        row = {
            "id": fx["id"], "category": fx["category"],
            "invariant_failures": r["failures"],
            "hook_out_frame": hook_out, "kept_ratio": kept_ratio,
            "cut_cadence_frames": cadence, "clips": len(clips),
        }
        # Independent LLM judge against the rubric, if the KB rubric exists.
        try:
            row["judge"] = await _judge_plan(fx, plan)
        except Exception as e:
            row["judge"] = {"error": str(e)[:120]}
        rows.append(row)

    knowledge_version = None
    try:
        import json as _json, pathlib
        mf = pathlib.Path(__file__).resolve().parents[1] / "knowledge" / "MANIFEST.json"
        if mf.exists():
            knowledge_version = _json.loads(mf.read_text()).get("version")
    except Exception:
        pass

    passed = sum(1 for r in rows if not r["invariant_failures"])
    pass_rate = passed / (len(rows) or 1)
    return {"skipped": False, "knowledge_version": knowledge_version,
            "pass_rate": round(pass_rate, 3), "rows": rows,
            "regressed": pass_rate < MIN_GATE_PASS_RATE}


async def _judge_plan(fx: dict, plan: dict) -> dict:
    """One independent LLM judge call scoring a plan against the review rubric."""
    import json, pathlib
    try:
        from main import anthropic_json, SONNET  # reuse the structured-output helper
    except Exception as e:
        return {"error": f"main import failed: {e}"}
    rubric_path = pathlib.Path(__file__).resolve().parents[1] / "knowledge" / "review_rubric.md"
    rubric = rubric_path.read_text() if rubric_path.exists() else "hook lands 0-3s; captions cover speech; cadence matches energy; no slivers."
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["score_0_100", "notes"],
        "properties": {
            "score_0_100": {"type": "integer"},
            "notes": {"type": "string"},
        },
    }
    system = "You are a strict short-form video editor grading an edit plan against a rubric. Score 0-100."
    user = (f"RUBRIC:\n{rubric}\n\nFIXTURE category: {fx['category']}\n"
            f"PLAN (render plan, output frames):\n{json.dumps(plan, default=str)[:6000]}\n\n"
            "Score this plan against the rubric.")
    return await anthropic_json(system, user, schema, SONNET, 500, temperature=0.0)


def main(argv: list[str]) -> int:
    live = "--live" in argv
    author = "--author" in argv
    ok, errs = self_check()
    print(f"[edl_eval] keyless self-check: {'PASS' if ok else 'FAIL'} "
          f"({len(edit_golden.known_good())} good, {len(edit_golden.known_bad())} bad)")
    for e in errs:
        print("  ✗", e)
    if not ok:
        return 1

    if author:
        report = run_author_tier()
        n_pass = sum(1 for r in report["rows"] if r["ok"])
        print(f"[edl_eval] author tier (fresh assemble_edl, keyless): "
              f"{'PASS' if report['ok'] else 'FAIL'} ({n_pass}/{len(report['rows'])} fixtures)")
        for r in report["rows"]:
            status = "PASS" if r["ok"] else "FAIL"
            line = f"  {status:4} {r['id']:18} ({r['category']})"
            if not r["ok"]:
                line += f" — {r['failures']}"
            print(line)
        if not report["ok"]:
            return 1

    if live:
        import asyncio
        report = asyncio.run(_live_scorecard(source="author" if author else "reference"))
        if report.get("skipped"):
            print(f"[edl_eval] live scorecard skipped: {report['reason']}")
        else:
            print(f"[edl_eval] live scorecard ({'fresh author' if author else 'reference golden'}): "
                  f"pass_rate={report['pass_rate']} knowledge_version={report['knowledge_version']}")
            for r in report["rows"]:
                j = r.get("judge", {})
                print(f"  {r['id']:16} hook_out={r['hook_out_frame']} kept={r['kept_ratio']} "
                      f"cadence={r['cut_cadence_frames']}f judge={j.get('score_0_100', j.get('error'))}")
            if report["regressed"]:
                print(f"  ✗ pass_rate {report['pass_rate']} < {MIN_GATE_PASS_RATE}")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
