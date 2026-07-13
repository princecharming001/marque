"""Deterministic retention-editing passes — applied to the EDL AFTER authoring
(legacy direct-EDL Sonnet author OR the plan/assemble_edl path), so BOTH paths
benefit identically. This is where "make the edit read like a top-tier human
editor" lives: silence/filler discipline the author can't be trusted to fully
honor, pacing, pattern interrupts, hook/structure polish, and sound design.

Architecture (see HANDOFF/plan doc WS0): retention passes are NOT inside
assemble_edl — that stays a pure plan->EDL constructor with no signature bloat.
Instead every pass here is a pure `(edl_dict, ...) -> edl_dict` function, called
in a fixed order from `apply_retention_passes`, each independently gated by the
`RETENTION_PASSES` env csv and individually fail-soft: an exception or a new HARD
`check_edl_invariants` issue introduced by a pass reverts that pass's output to
its input rather than ever failing the pipeline. "Degrade, never break."

Pass order (each a separate task in the upgrade — filled in incrementally):
  1. sweep_residual_fillers  (WS1 — implemented)
  2. plan_pacing             (WS2 — TODO)
  3. align_emphasis          (WS4 — TODO)
  4. place_hook_overlay / trim_loop_tail / place_end_card  (trim_loop_tail: WS1,
     implemented; hook/end_card: WS4, TODO)
  5. schedule_interrupts     (WS3 — TODO, runs last so it sees all prior events)
  6. synthesize_sfx          (WS4 — TODO)
"""
from __future__ import annotations
import copy
import os

from app.edl import (
    ALWAYS_FILLERS, TRIM_LEVELS, ms_to_frame, detect_disfluencies,
    _kept_intervals, _kept_frames, _coalesce_drops, _norm_word,
    _MIN_DURATION_FRAMES, check_edl_invariants,
)

# csv of pass names to run; "" (default) = everything off = today's live behavior.
# "all" enables every implemented pass. Individual names: filler, pacing, interrupts,
# sfx, structure (hook/end_card/loop_tail).
_ENV_PASSES = os.environ.get("RETENTION_PASSES", "")


def _enabled_passes() -> set[str]:
    raw = _ENV_PASSES.strip()
    if not raw:
        return set()
    if raw == "all":
        return {"filler", "pacing", "interrupts", "sfx", "structure"}
    return {p.strip() for p in raw.split(",") if p.strip()}


def _play_order(edl: dict) -> list[int]:
    segs = edl.get("segments") or []
    order = edl.get("segment_order")
    if order is not None and sorted(order) == list(range(len(segs))):
        return list(order)
    return list(range(len(segs)))


def _safe_pass(name: str, edl: dict, fn, *args, **kwargs) -> dict:
    """Run one retention pass; revert to the input EDL on any exception OR if the
    pass's output introduces a NEW hard invariant violation the input didn't have.
    Never lets a retention pass turn a working pipeline run into a failure."""
    before_issues = set(check_edl_invariants(edl))
    try:
        out = fn(copy.deepcopy(edl), *args, **kwargs)
    except Exception:
        return edl
    try:
        after_issues = set(check_edl_invariants(out))
    except Exception:
        return edl
    new_issues = after_issues - before_issues
    if new_issues:
        return edl
    return out


# ---------------------------------------------------------------------------
# WS1 — residual filler sweep: never trust the author. Scans the FINAL kept
# footage for anything that reads as filler by our own detectors and force-drops
# it, regardless of which author path produced the EDL. This is the main backstop
# for the legacy direct-EDL author (which sometimes just... keeps the "um").
# ---------------------------------------------------------------------------

def sweep_residual_fillers(edl: dict, words: list[dict], level: str = "default") -> dict:
    edl = copy.deepcopy(edl)   # never mutate the caller's dict (apply_edl_ops convention)
    segments = edl.get("segments") or []
    drops = edl.get("drops") or []
    if not segments or not words:
        return edl
    kept = _kept_intervals(segments, drops)
    if not kept:
        return edl

    def _inside_kept(lo: int, hi: int) -> bool:
        return any(k_in <= lo and hi <= k_out for k_in, k_out in kept)

    # Build the same disfluency drop set the author SHOULD have honored, plus a
    # direct lexicon/type scan — anything that lands inside currently-kept
    # footage is residual and gets force-dropped.
    extra = detect_disfluencies(words, level)
    residual: list[dict] = []
    for d in extra:
        if _inside_kept(d.src_in, d.src_out):
            residual.append(d.model_dump())
    for w in words:
        norm = _norm_word(w.get("word", ""))
        is_filler = w.get("type") == "filler" or norm in ALWAYS_FILLERS
        if not is_filler:
            continue
        lo, hi = ms_to_frame(w.get("start_ms", 0)), ms_to_frame(w.get("end_ms", w.get("start_ms", 0) + 100))
        if hi > lo and _inside_kept(lo, hi):
            residual.append({"src_in": lo, "src_out": hi, "reason": "filler"})

    if not residual:
        return edl

    merged_drops = _coalesce_drops(drops + residual)
    # Guard: never let the sweep push kept footage below the floor — a heavily
    # filler-laden take could otherwise get swept into nothing.
    trial = {**edl, "drops": merged_drops}
    if _kept_frames(trial) < _MIN_DURATION_FRAMES:
        return edl
    edl["drops"] = merged_drops
    return edl


# ---------------------------------------------------------------------------
# WS1 — loop-friendly ending, ported from assemble_edl's P4.2 so the LEGACY
# author path (which never ran assemble_edl) gets the same trailing-dead-air
# trim. Generalized to respect segment_order (assemble_edl's original never
# needed to — it runs before any reorder exists).
# ---------------------------------------------------------------------------

def trim_loop_tail(edl: dict, words: list[dict], max_tail_frames: int = 10) -> dict:
    edl = copy.deepcopy(edl)   # never mutate the caller's dict
    segments = edl.get("segments") or []
    if not segments or not words:
        return edl
    last = words[-1]
    last_word_end = ms_to_frame(last.get("end_ms") or last.get("start_ms", 0))
    tail_idx = _play_order(edl)[-1]
    tail = segments[tail_idx]
    if tail["src_out"] - last_word_end > max_tail_frames and last_word_end + max_tail_frames > tail["src_in"]:
        tail["src_out"] = min(tail["src_out"], last_word_end + max_tail_frames)
        edl["segments"] = segments
    return edl


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def apply_retention_passes(edl: dict, words: list[dict], *, style: str,
                           prefs: dict | None = None, emphasis_spans: list | None = None,
                           dossier: dict | None = None, hints: dict | None = None,
                           script: dict | None = None, level: str = "default") -> dict:
    """Entry point called once from `_run_edit`, after EITHER author path builds
    its EDL and before `_resolve_broll`/`build_render_plan`. `hints` carries the
    plan author's typed decisions (pacing/interrupt_density/hook_text/end_card/
    music) when available, or {} from the legacy path / safe-default (every pass
    has a style-driven default so an empty hints dict is a fully valid input)."""
    prefs = prefs or {}
    hints = hints or {}
    enabled = _enabled_passes()
    if not enabled or not words:
        return edl

    if "filler" in enabled and prefs.get("filler_trim") != "off":
        edl = _safe_pass("sweep_residual_fillers", edl, sweep_residual_fillers, words, level)

    # WS2 pacing, WS4 align_emphasis/hook/end_card, WS3 interrupts, WS4 sfx land
    # here in that order as their own tasks — each gated on its own `enabled` name.

    if "structure" in enabled:
        edl = _safe_pass("trim_loop_tail", edl, trim_loop_tail, words)

    return edl
