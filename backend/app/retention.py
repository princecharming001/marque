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
  2. plan_pacing             (WS2 — implemented)
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
    ALWAYS_FILLERS, TRIM_LEVELS, ms_to_frame, _frame_to_ms, snap_to_word,
    detect_disfluencies, split_segment_in_place, _PUNCH_STYLES,
    _kept_intervals, _kept_frames, _coalesce_drops, _norm_word,
    _MIN_DURATION_FRAMES, MIN_CLIP_OUTPUT_FRAMES, check_edl_invariants,
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
# WS2 — pacing engine. Expressed ENTIRELY through existing Segment.speed +
# segment splitting — no schema change. The renderer already honors per-segment
# speed end to end (build_render_plan's per-clip speed math, CutVideo's
# playbackRate + pyRound) and Remotion's Lambda audio pipeline applies
# `playbackRate` via FFmpeg `atempo` (pitch-preserving time-stretch) — see
# render/src/components/CutVideo.tsx — so the 1.35x spoken-speed cap below is a
# comprehension/artifact bound, not a pitch one. Silence has no speech to
# distort, so speed-through-silence goes much higher (SILENCE_SPEED_CAP).
# ---------------------------------------------------------------------------

_STYLE_PACE_LIFT_DEFAULT = {
    "talking_head": "subtle", "broll_cutaway": "subtle", "green_screen": "subtle",
    "split_three": "subtle", "fast_cuts": "none", "faceless": "none", "duet_split": "none",
}
PACING_LIFT_MULT = {"none": 1.0, "subtle": 1.03, "medium": 1.06}
SPOKEN_SPEED_CAP = 1.35          # comprehension/artifact bound for SPOKEN stretches
SILENCE_SPEED_CAP = 3.0          # silence has no speech to distort — much higher cap
LOW_INFO_MIN_FRAMES = 60          # 2s — never speed up a stretch shorter than this
HOOK_PROTECT_OUT_FRAMES = 90      # ~3s — never speed the cold-open
CTA_PROTECT_OUT_FRAMES = 60       # ~2s — never speed the payoff/CTA
COMPRESSION_CAP_FRACTION = 0.25   # stop once pacing has saved > 25% of kept output
PACING_SPLIT_BUDGET = 24          # bounds Lambda seek cost + rounding accumulation
SILENCE_FF_MIN_FRAMES = 36        # ~1.2s — below this, strip_fillers' own tightening covers it
SILENCE_FF_MAX_FRAMES = 75        # ~2.5s — above this, a hard cut reads better than a 3x blur
SILENCE_FF_DIVISOR = 14           # gap_frames / 14 -> perceived pause of ~460ms at 1x


def _segment_kept_ranges(seg: dict, drops: list[dict]) -> list[tuple[int, int]]:
    """Kept sub-ranges (source coords) within a single segment, drops subtracted.
    Mirrors _kept_intervals but keyed to ONE segment so a candidate zone maps
    back to a segment index for splitting."""
    lo, hi = seg["src_in"], seg["src_out"]
    cuts = sorted((d["src_in"], d["src_out"]) for d in drops if d["src_out"] > d["src_in"])
    out: list[tuple[int, int]] = []
    cur = lo
    for d_in, d_out in cuts:
        if d_out <= cur or d_in >= hi:
            continue
        if d_in > cur:
            out.append((cur, min(d_in, hi)))
        cur = max(cur, d_out)
        if cur >= hi:
            break
    if cur < hi:
        out.append((cur, hi))
    return out


def _words_in_range(words: list[dict], lo_ms: float, hi_ms: float) -> int:
    return sum(1 for w in words if lo_ms <= w.get("start_ms", 0) < hi_ms)


def _wpm(word_count: int, frames: int) -> float:
    minutes = (frames / 30.0) / 60.0
    return (word_count / minutes) if minutes > 0 else 0.0


def _dossier_energy(dossier: dict | None, lo_frame: int, hi_frame: int) -> float | None:
    """Minimum delivery_curve energy overlapping [lo_frame, hi_frame), or None
    when no dossier / no overlapping entries — callers treat None as "no signal,
    skip the bonus" rather than a hard zero."""
    if not dossier:
        return None
    curve = dossier.get("delivery_curve") or []
    vals = [c.get("energy") for c in curve
            if isinstance(c, dict) and c.get("f1", 0) > lo_frame and c.get("f0", 0) < hi_frame
            and isinstance(c.get("energy"), (int, float))]
    return min(vals) if vals else None


def plan_pacing(edl: dict, words: list[dict], *, style: str,
                emphasis_spans: list[tuple[int, int]] | None = None,
                dossier: dict | None = None, hints: dict | None = None) -> dict:
    """A global speed "lift" (style-defaulted, or from the plan author's
    pacing.lift hint) plus, per ORIGINAL segment, a speed-up over its single
    most-qualifying low-information stretch — a word-run with below-take-median
    speech rate, no emphasis-span overlap, clear of the hook/CTA protection
    zones. duet_split is hard-excluded (its react-window length-preservation
    guard in build_render_plan forbids ANY speed change on that style).

    Documented simplification: AT MOST ONE speed-up action per original
    segment (the lowest-wpm-ratio candidate, ties broken by longest) — covers
    the common single/few-segment take correctly and simply; a segment with
    several draggy stretches gets its worst one tightened rather than all of
    them. Lifting this to multiple actions per segment is a straightforward
    follow-up if evidence shows it's worth the added indexing complexity.

    Granularity note: a "candidate" is a full hook/CTA-clipped KEPT RANGE (i.e.
    per_seg_kept, clipped) evaluated as ONE unit — this does NOT hunt for a
    localized low-density sub-burst within a longer, otherwise-normal kept
    range; a drop's worth of filler/dead-air already splits segments into
    multiple kept ranges in the common case, which is what gives this per-range
    evaluation real reach. Scanning for sub-bursts within one long unbroken
    range is a further refinement, not implemented here."""
    edl = copy.deepcopy(edl)
    if style == "duet_split" or not words or not (edl.get("segments") or []):
        return edl
    segments = edl["segments"]
    drops = edl.get("drops") or []
    hints = hints or {}
    pacing_hints = hints.get("pacing") or {}
    emphasis_spans = emphasis_spans or []

    lift_name = pacing_hints.get("lift") or _STYLE_PACE_LIFT_DEFAULT.get(style, "none")
    lift_mult = PACING_LIFT_MULT.get(lift_name, 1.0)
    for seg in segments:
        seg["speed"] = lift_mult

    play_order = _play_order(edl)
    per_seg_kept = {i: _segment_kept_ranges(seg, drops) for i, seg in enumerate(segments)}
    total_kept_frames = sum(hi - lo for kept in per_seg_kept.values() for lo, hi in kept)
    total_kept_words = sum(
        _words_in_range(words, _frame_to_ms(lo), _frame_to_ms(hi))
        for kept in per_seg_kept.values() for lo, hi in kept)
    take_median_wpm = _wpm(total_kept_words, total_kept_frames) if total_kept_frames else 0.0
    if take_median_wpm <= 0 or total_kept_frames < LOW_INFO_MIN_FRAMES:
        edl["segments"] = segments
        return edl   # no reliable speech-rate signal — lift-only, nothing to compare against

    # Hook/CTA protection: walk kept ranges in PLAY order, per segment recording
    # the source frame up to which the first ~90 output-equivalent frames extend
    # (and, symmetrically, the frame after which the last ~60 do).
    hook_bound: dict[int, int] = {}
    remaining = HOOK_PROTECT_OUT_FRAMES
    for i in play_order:
        if remaining <= 0:
            break
        for lo, hi in per_seg_kept.get(i, []):
            if remaining <= 0:
                break
            take = min(remaining, hi - lo)
            hook_bound[i] = lo + take
            remaining -= take
    cta_bound: dict[int, int] = {}
    remaining = CTA_PROTECT_OUT_FRAMES
    for i in reversed(play_order):
        if remaining <= 0:
            break
        for lo, hi in reversed(per_seg_kept.get(i, [])):
            if remaining <= 0:
                break
            take = min(remaining, hi - lo)
            cta_bound[i] = hi - take
            remaining -= take

    fast_forward_silences = bool(pacing_hints.get("fast_forward_silences"))

    splits_used = 0
    frames_saved = 0
    # Process ORIGINAL segments from LAST to FIRST: split_segment_in_place only
    # ever shifts indices > the split point, so processing highest-index-first
    # guarantees every not-yet-processed (lower) index is still exactly where
    # per_seg_kept/hook_bound/cta_bound computed it to be.
    for i in range(len(segments) - 1, -1, -1):
        if splits_used >= PACING_SPLIT_BUDGET - 1:
            break
        seg_lo, seg_hi = segments[i]["src_in"], segments[i]["src_out"]
        safe_lo, safe_hi = hook_bound.get(i, seg_lo), cta_bound.get(i, seg_hi)

        # --- priority 1: speed-through-silence — a genuine mid-take pause becomes
        # a fast-forward instead of a hard cut. Only the single longest qualifying
        # dead_air drop per segment (same one-action-per-segment simplification). ---
        silence_action = None   # (drop, speed)
        if fast_forward_silences:
            candidates = [d for d in drops
                         if d.get("reason") == "dead_air" and seg_lo <= d["src_in"]
                         and d["src_out"] <= seg_hi and safe_lo <= d["src_in"]
                         and d["src_out"] <= safe_hi
                         and SILENCE_FF_MIN_FRAMES <= d["src_out"] - d["src_in"] <= SILENCE_FF_MAX_FRAMES]
            if candidates:
                d = max(candidates, key=lambda d: d["src_out"] - d["src_in"])
                gap = d["src_out"] - d["src_in"]
                silence_action = (d, min(SILENCE_SPEED_CAP, gap / SILENCE_FF_DIVISOR))

        if silence_action is not None:
            drop, silence_speed = silence_action
            drop_lo, drop_hi = drop["src_in"], drop["src_out"]
            projected_out = round((drop_hi - drop_lo) / silence_speed)
            if projected_out < MIN_CLIP_OUTPUT_FRAMES or splits_used + 2 > PACING_SPLIT_BUDGET:
                pass   # can't safely action this one — fall through to low-info below
            else:
                drops = edl["drops"] = [d for d in edl["drops"] if d is not drop]
                target_idx = i
                if drop_lo > segments[target_idx]["src_in"]:
                    split_segment_in_place(edl, target_idx, drop_lo)
                    segments = edl["segments"]
                    splits_used += 1
                    target_idx += 1
                if drop_hi < segments[target_idx]["src_out"]:
                    split_segment_in_place(edl, target_idx, drop_hi)
                    segments = edl["segments"]
                    splits_used += 1
                segments[target_idx]["speed"] = silence_speed
                continue   # this segment's one action is spent

        # --- priority 2: low-info speed-up (only reached if no silence action ran) ---
        best: tuple[int, int, float] | None = None   # (zone_lo, zone_hi, ratio)
        for lo, hi in per_seg_kept.get(i, []):
            zone_lo = max(lo, safe_lo)
            zone_hi = min(hi, safe_hi)
            if zone_hi - zone_lo < LOW_INFO_MIN_FRAMES:
                continue
            if any(not (e_hi <= zone_lo or e_lo >= zone_hi) for e_lo, e_hi in emphasis_spans):
                continue   # never speed a stretch overlapping an emphasis span
            word_count = _words_in_range(words, _frame_to_ms(zone_lo), _frame_to_ms(zone_hi))
            local_wpm = _wpm(word_count, zone_hi - zone_lo)
            ratio = (local_wpm / take_median_wpm) if take_median_wpm else 1.0
            if ratio >= 1.0:
                continue   # not low-info
            if best is None or ratio < best[2] or (ratio == best[2] and zone_hi - zone_lo > best[1] - best[0]):
                best = (zone_lo, zone_hi, ratio)
        if best is None:
            continue
        zone_lo, zone_hi, ratio = best
        speed_mult = 1.25 if ratio < 0.85 else 1.15
        energy = _dossier_energy(dossier, zone_lo, zone_hi)
        if energy is not None and energy <= 0.3:
            speed_mult += 0.05
        combined = min(SPOKEN_SPEED_CAP, speed_mult * lift_mult)

        # snap to word boundaries so the speed change never lands mid-word
        snap_lo = max(zone_lo, snap_to_word(_frame_to_ms(zone_lo), words, "start"))
        snap_hi = min(zone_hi, snap_to_word(_frame_to_ms(zone_hi), words, "end"))
        if snap_hi - snap_lo < LOW_INFO_MIN_FRAMES:
            continue
        projected_out = round((snap_hi - snap_lo) / combined)
        if projected_out < MIN_CLIP_OUTPUT_FRAMES:
            continue
        projected_save = (snap_hi - snap_lo) - projected_out
        if total_kept_frames and (frames_saved + projected_save) / total_kept_frames > COMPRESSION_CAP_FRACTION:
            continue
        if splits_used + 2 > PACING_SPLIT_BUDGET:
            continue

        target_idx = i
        if snap_lo > segments[target_idx]["src_in"]:
            split_segment_in_place(edl, target_idx, snap_lo)
            segments = edl["segments"]
            splits_used += 1
            target_idx += 1
        if snap_hi < segments[target_idx]["src_out"]:
            split_segment_in_place(edl, target_idx, snap_hi)
            segments = edl["segments"]
            splits_used += 1
        segments[target_idx]["speed"] = combined
        frames_saved += projected_save

    edl["segments"] = segments
    return edl


# ---------------------------------------------------------------------------
# WS3 — pattern-interrupt scheduler. Reuses the existing `punch_in` Overlay —
# no schema change. Guarantees a visual event at least every N OUTPUT frames
# (style/density-dependent) by inserting a punch_in (or, for faceless — no face
# to zoom — a text_sticker keyword pop) into any gap that exceeds it. Runs LAST
# in apply_retention_passes so it sees every event pacing/hook/sfx already
# placed and never double-covers them.
# ---------------------------------------------------------------------------

_INTERRUPT_CADENCE = {
    "talking_head": 120, "green_screen": 120, "split_three": 120,
    "broll_cutaway": 150, "faceless": 90,
    # fast_cuts / duet_split: native cadence already high enough — skip entirely.
}
_DENSITY_MULT = {"calm": 1.5, "standard": 1.0, "dense": 0.75}
_INTERRUPT_CADENCE_FLOOR = 60
_INTERRUPT_HOOK_GUARD = 45     # never insert in the first ~1.5s of OUTPUT
_INTERRUPT_CTA_GUARD = 60      # never insert in the last ~2s of OUTPUT
_INTERRUPT_MIN_SPACING = 60    # from any existing overlay/broll edge
_INTERRUPT_MAX_PER_CLIP = 12
_INTERRUPT_HOLD_FRAMES = 75    # max width of a synthesized punch/pop window
_INTERRUPT_SCALES = (1.06, 1.10)   # alternates, so consecutive cuts read as multi-cam


def _build_output_index(segments: list[dict], drops: list[dict],
                        play_order: list[int]) -> tuple[list[tuple[int, int, int, float]], int]:
    """[(src_in, src_out, out_start, speed), ...] in PLAY order — a standalone
    source<->output mapping (mirrors the index build_render_plan constructs
    internally) so the interrupt scheduler can reason about OUTPUT-frame gaps
    without needing build_render_plan's full remap of captions/overlays/broll."""
    index: list[tuple[int, int, int, float]] = []
    out_cursor = 0
    for i in play_order:
        seg = segments[i]
        speed = float(seg.get("speed") or 1.0)
        for lo, hi in _segment_kept_ranges(seg, drops):
            index.append((lo, hi, out_cursor, speed))
            out_cursor += max(1, round((hi - lo) / speed))
    return index, out_cursor


def _src_to_out(index: list[tuple[int, int, int, float]], src_frame: int) -> int | None:
    for lo, hi, out_start, speed in index:
        if lo <= src_frame < hi:
            return out_start + round((src_frame - lo) / speed)
    return None


def _out_to_src(index: list[tuple[int, int, int, float]], out_frame: int) -> int | None:
    for lo, hi, out_start, speed in index:
        out_len = max(1, round((hi - lo) / speed))
        if out_start <= out_frame < out_start + out_len:
            return lo + round((out_frame - out_start) * speed)
    return None


def _overlay_out_windows(edl: dict, index: list[tuple[int, int, int, float]]) -> list[tuple[int, int]]:
    """Existing overlay/b-roll windows mapped to OUTPUT coords — these already
    count as "a visual event is happening here" and must never be double-covered
    by a synthesized punch/pop landing inside or too close to one."""
    windows: list[tuple[int, int]] = []
    for o in (edl.get("overlays") or []) + (edl.get("broll") or []):
        a, b = _src_to_out(index, o["src_in"]), _src_to_out(index, o.get("src_out", o["src_in"] + 1) - 1)
        if a is not None:
            windows.append((a, (b if b is not None else a) + 1))
    return sorted(windows)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union-merge overlapping/adjacent (a, b) ranges — so two overlays that
    happen to touch or overlap read as ONE occupied stretch, not two separate
    windows with a (nonexistent) gap between them."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def schedule_interrupts(edl: dict, words: list[dict], *, style: str,
                        prefs: dict | None = None, hints: dict | None = None) -> dict:
    """Guarantee a visual change at least every N output frames (N by style x
    density) by inserting punch_in overlays (text_sticker keyword pops for
    faceless, which has no face to zoom) into gaps left uncovered by cuts,
    speed changes, or any overlay/b-roll already placed. Skips fast_cuts and
    duet_split entirely (native cut cadence / play-freeze rhythm already carries
    this). Alternates punch scale between two values so consecutive insertions
    read as a "multi-cam" cut rather than a repeated zoom."""
    edl = copy.deepcopy(edl)
    prefs = prefs or {}
    hints = hints or {}
    if style not in _INTERRUPT_CADENCE or prefs.get("punch_ins") is False:
        return edl
    segments = edl.get("segments") or []
    if not segments:
        return edl
    drops = edl.get("drops") or []
    play_order = _play_order(edl)
    index, total_out = _build_output_index(segments, drops, play_order)
    if total_out <= _INTERRUPT_HOOK_GUARD + _INTERRUPT_CTA_GUARD:
        return edl   # too short a take to meaningfully schedule interrupts

    density = hints.get("interrupt_density") or "standard"
    cadence = max(_INTERRUPT_CADENCE_FLOOR,
                 int(_INTERRUPT_CADENCE[style] * _DENSITY_MULT.get(density, 1.0)))

    # "Events" = cut boundaries (every kept-range's own out_start — a new piece
    # starting IS a visual change; instantaneous) plus existing overlay/b-roll
    # windows (these have DURATION — the entire window counts as covered, not
    # just its two endpoints, so they're kept as ranges, never flattened to
    # loose points that a naive gap-walk could schedule an insert BETWEEN).
    cut_points = sorted({out_start for _, _, out_start, _ in index} | {total_out})
    occupied = _merge_ranges(_overlay_out_windows(edl, index))

    words_by_out = sorted(
        (_src_to_out(index, ms_to_frame(w["start_ms"])), w) for w in words
        if _src_to_out(index, ms_to_frame(w["start_ms"])) is not None)

    can_punch = style in _PUNCH_STYLES
    inserted = 0
    scale_i = 0
    last_event_out = 0
    new_overlays: list[dict] = []

    def _try_insert(anchor_target: int, ceiling: int) -> int | None:
        """Insert one punch/pop anchored at the next caption word on/after
        `anchor_target`, provided it stays clear of `ceiling` (the next real
        event) and the guards. Returns the new `last_event_out` on success."""
        nonlocal inserted, scale_i
        anchor = next((out for out, _ in words_by_out if out >= anchor_target), None)
        if anchor is None or anchor >= ceiling - 6 or anchor - last_event_out < _INTERRUPT_MIN_SPACING:
            return None
        out_hi = min(anchor + _INTERRUPT_HOLD_FRAMES, ceiling - 6)
        src_lo, src_hi = _out_to_src(index, anchor), _out_to_src(index, max(anchor + 1, out_hi))
        if src_lo is None or src_hi is None or src_hi <= src_lo:
            return None
        if can_punch:
            new_overlays.append({"type": "punch_in", "src_in": src_lo, "src_out": src_hi,
                                 "scale": _INTERRUPT_SCALES[scale_i % 2], "text": ""})
        else:
            word_text = next((w["word"] for out, w in words_by_out if out == anchor), "")
            if not word_text:
                return None
            new_overlays.append({"type": "text_sticker", "src_in": src_lo, "src_out": src_hi,
                                 "scale": 1.0, "text": word_text[:24],
                                 "pos_x": 0.5, "pos_y": 0.3, "rotation": 0.0,
                                 "color": None, "bg": "box", "font": "inter"})
        inserted += 1
        scale_i += 1
        return out_hi

    # Forward-progress state machine: at every step, either an existing event
    # (cut point or occupied overlay/b-roll window) arrives before cadence would
    # be exceeded — consume it and move on — or cadence IS exceeded with nothing
    # else covering that stretch, so insert a new punch/pop there. `_ITER_CAP`
    # is a hard backstop (never hit in practice — every branch strictly advances
    # `last_event_out`) so a future edit here can't ever infinite-loop.
    _ITER_CAP = 4 * _INTERRUPT_MAX_PER_CLIP + len(cut_points) + len(occupied) + 4
    for _ in range(_ITER_CAP):
        if last_event_out >= total_out - _INTERRUPT_CTA_GUARD or inserted >= _INTERRUPT_MAX_PER_CLIP:
            break
        next_cut = next((p for p in cut_points if p > last_event_out), total_out)
        next_occ = next(((a, b) for a, b in occupied if b > last_event_out), None)
        if next_occ is not None and next_occ[0] <= last_event_out + cadence:
            # an overlay/b-roll window arrives before cadence is exceeded — it
            # counts as the visual event; jump past its far edge.
            last_event_out = max(last_event_out, next_occ[1])
            continue
        if next_cut - last_event_out <= cadence:
            last_event_out = next_cut   # a cut arrives in time — it counts as the event
            continue
        # cadence exceeded with nothing else covering it — insert here. The
        # ceiling is whichever comes first: the next cut or the next occupied
        # window's start (never insert past either).
        ceiling = min(next_cut, next_occ[0] if next_occ else total_out)
        anchor_target = max(last_event_out + cadence, _INTERRUPT_HOOK_GUARD)
        new_last = _try_insert(anchor_target, ceiling)
        last_event_out = new_last if new_last is not None else ceiling

    if new_overlays:
        edl["overlays"] = (edl.get("overlays") or []) + new_overlays
    return edl

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

    if "pacing" in enabled and prefs.get("pacing") is not False:
        edl = _safe_pass("plan_pacing", edl, plan_pacing, words, style=style,
                         emphasis_spans=emphasis_spans, dossier=dossier, hints=hints)

    # WS4 align_emphasis/hook/end_card land here as their own task — gated on
    # its own `enabled` name, between pacing and structure.

    if "structure" in enabled:
        edl = _safe_pass("trim_loop_tail", edl, trim_loop_tail, words)

    # interrupts runs LAST (per WS3): it needs to see every event pacing/
    # structure/sfx already placed so it never double-covers them.
    if "interrupts" in enabled:
        edl = _safe_pass("schedule_interrupts", edl, schedule_interrupts, words,
                         style=style, prefs=prefs, hints=hints)

    return edl
