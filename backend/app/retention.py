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

Pass order (all implemented as of P4):
  1. sweep_residual_fillers  (WS1)
  2. plan_pacing             (WS2)
  3. align_emphasis          (WS4b — ranks emphasis spans, highlight_words + punch_in)
  4. place_hook_overlay / trim_loop_tail (XOR) place_end_card  (WS1 loop-tail;
     WS4a/4c hook + end-card)
  5. schedule_interrupts     (WS3 — after 3/4 so it never double-covers their overlays)
  6. synthesize_sfx          (WS4d — last of all, sees every overlay/transition)
"""
from __future__ import annotations
import copy
import hashlib
import os
import random

from app.edl import (
    ALWAYS_FILLERS, TRIM_LEVELS, ms_to_frame, _frame_to_ms, snap_to_word,
    detect_disfluencies, split_segment_in_place, _PUNCH_STYLES,
    _kept_intervals, _kept_frames, _coalesce_drops, _norm_word,
    _MIN_DURATION_FRAMES, MIN_CLIP_OUTPUT_FRAMES, check_edl_invariants, SFX_GAIN_DEFAULT,
)
from app import themes as themes_mod

# csv of pass names to run; "" (default) = everything off = today's live behavior.
# "all" enables every implemented pass. Individual names: filler, pacing, emphasis,
# interrupts, sfx, structure (hook/end_card/loop_tail).
_ENV_PASSES = os.environ.get("RETENTION_PASSES", "")


def _enabled_passes() -> set[str]:
    raw = _ENV_PASSES.strip()
    if not raw:
        return set()
    if raw == "all":
        return {"filler", "retake", "pacing", "emphasis", "interrupts", "sfx", "structure"}
    return {p.strip() for p in raw.split(",") if p.strip()}


def _play_order(edl: dict) -> list[int]:
    segs = edl.get("segments") or []
    order = edl.get("segment_order")
    if order is not None and sorted(order) == list(range(len(segs))):
        return list(order)
    return list(range(len(segs)))


def _seeded_rng(*parts: str) -> random.Random:
    """A random.Random seeded deterministically from arbitrary string parts, via
    sha1 rather than Python's built-in hash() (which is salted per-process by
    default) — so "same job_seed -> identical output" holds across process
    restarts / redeploys, not just within one run."""
    digest = hashlib.sha1(":".join(parts).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


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
# RETAKE DEDUP — the creator flubs a line, pauses, and re-delivers it. The
# micro-scale disfluency detectors cap at ~1.2s / 4 words and match lexically, so
# a whole re-spoken SENTENCE is invisible to them. This pass segments the kept
# footage into pause-delimited utterances, finds adjacent near-duplicates by
# token-shingle overlap, and drops the EARLIER take (keeps the re-delivery) — a
# freestyle edit is allowed to omit the botched take for a cohesive cut.
# ---------------------------------------------------------------------------

_RETAKE_PAUSE_MS = 500          # a gap this long delimits one spoken attempt from the next
_RETAKE_SIM = 0.62             # token-set Jaccard at/above which two utterances are "the same line"
_RETAKE_MIN_WORDS = 4          # ignore tiny utterances (a repeated "okay" is filler, not a retake)
_RETAKE_MAX_DROP_FRAC = 0.40   # never dedup away more than this share of the take


def _utterances(words: list[dict]) -> list[tuple[int, int]]:
    """Group word indices into [start_i, end_i) runs split on ≥_RETAKE_PAUSE_MS gaps."""
    runs: list[tuple[int, int]] = []
    if not words:
        return runs
    start = 0
    for i in range(1, len(words)):
        gap = words[i].get("start_ms", 0) - words[i - 1].get("end_ms", words[i - 1].get("start_ms", 0))
        if gap >= _RETAKE_PAUSE_MS:
            runs.append((start, i))
            start = i
    runs.append((start, len(words)))
    return runs


_RETAKE_CONTAIN = 0.72          # min |a∩b|/min(|a|,|b|) to call a fragment a retake of a fuller take
_RETAKE_BRIDGE_MAX_WORDS = 6   # a short utterance between two takes ("ugh, let me redo that") is a bridge


def _shingle_sim(a: list[str], b: list[str]) -> float:
    """Similarity of two token lists = max(Jaccard, containment). Jaccard is robust to a
    stumble adding/dropping a word; containment catches the common retake shape where the
    flubbed take is a FRAGMENT of (or contains) the clean take — Jaccard alone underrates
    those because the size mismatch inflates the union."""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    jaccard = inter / len(sa | sb)
    containment = inter / min(len(sa), len(sb))
    return max(jaccard, containment if containment >= _RETAKE_CONTAIN else 0.0)


def dedupe_retakes(edl: dict, words: list[dict]) -> dict:
    """Drop the earlier of two near-duplicate utterances (a flubbed take re-delivered),
    keeping the LAST clean delivery. Compares each utterance to the next one AND the one
    after (when a short bridge like 'ugh, let me redo that' sits between the takes).
    Frame-based drops, coalesced; fail-soft and floor-guarded like every other pass."""
    edl = copy.deepcopy(edl)
    segments = edl.get("segments") or []
    drops = edl.get("drops") or []
    if not segments or len(words) < 2 * _RETAKE_MIN_WORDS:
        return edl
    kept = _kept_intervals(segments, drops)
    if not kept:
        return edl

    def _inside_kept(lo: int, hi: int) -> bool:
        return any(k_in <= lo and hi <= k_out for k_in, k_out in kept)

    runs = _utterances(words)
    norms = [_norm_word(w.get("word", "")) for w in words]
    toks = [[norms[k] for k in range(a, b) if norms[k]] for a, b in runs]
    total_frames = max(1, sum(hi - lo for lo, hi in kept))
    dropped_idx: set = set()
    retake_drops: list[dict] = []
    dropped_frames = 0
    # Walk pairs; for each earlier utterance i, its retake may be the very next utterance
    # (j=i+1) or the one after a short bridge (j=i+2 when i+1 is a brief aside). Drop the
    # EARLIER take. No first-utterance exemption — a flubbed OPENING line kept in is exactly
    # the bug we're fixing; the similarity gate is what protects a genuine hook.
    for i in range(len(runs) - 1):
        if i in dropped_idx or len(toks[i]) < _RETAKE_MIN_WORDS:
            continue
        for j in (i + 1, i + 2):
            if j >= len(runs) or j in dropped_idx:
                continue
            if j == i + 2 and len(toks[i + 1]) > _RETAKE_BRIDGE_MAX_WORDS:
                break     # the thing between them is real content, not a bridge — not a retake
            if len(toks[j]) < _RETAKE_MIN_WORDS or _shingle_sim(toks[i], toks[j]) < _RETAKE_SIM:
                continue
            lo = ms_to_frame(words[runs[i][0]].get("start_ms", 0))
            hi = ms_to_frame(words[runs[i][1] - 1].get("end_ms",
                             words[runs[i][1] - 1].get("start_ms", 0) + 100))
            if hi <= lo or not _inside_kept(lo, hi):
                continue
            if dropped_frames + (hi - lo) > _RETAKE_MAX_DROP_FRAC * total_frames:
                break
            retake_drops.append({"src_in": lo, "src_out": hi, "reason": "false_start"})
            dropped_frames += hi - lo
            dropped_idx.add(i)
            break

    if not retake_drops:
        return edl
    merged = _coalesce_drops(drops + retake_drops)
    trial = {**edl, "drops": merged}
    if _kept_frames(trial) < _MIN_DURATION_FRAMES:
        return edl
    edl["drops"] = merged
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
# A3 (superintelligence epic) — simulated multicam framing. A static single
# framing for the whole take is the single most recognizable "un-edited" tell;
# this pass rotates the canvas transform (Segment.tx_scale/tx_x/tx_y — already
# rendered end to end, see build_render_plan and CutVideo.tsx) through a
# WIDE/MID/WIDE/CLOSE pattern at jittered 5-8s intervals, simulating a 2-3
# camera setup with zero render-side schema change. Runs AFTER plan_pacing
# (so it walks the post-speed-change output timeline) and BEFORE align_emphasis
# (so a hook/emphasis punch synthesized later can react to whatever framing is
# already there rather than the reverse).
# ---------------------------------------------------------------------------

_FRAMING_SKIP_STYLES = {"duet_split", "split_three"}     # reaction/multi-pane grammars own their own framing
# Spec §6.1 punch-in ladder: 100 / 110 / 118, capped at 118% so the framing transform
# never breaches the 120% ceiling on a 1080 source (the old 135% "close" blew past it).
_FRAMING_SCALES = {"wide": 1.0, "mid": 1.10, "close": 1.18}
# Cyclic pattern crossing through "wide" so adjacent deltas are 10% / 18% (never 0);
# the lint's same_framing_adjacent floor is relaxed to 0.08 to accept the spec's steps.
_FRAMING_PATTERN = ("wide", "mid", "wide", "close")
_FRAMING_ROTATE_S = (5.0, 8.0)          # jittered rotation period bounds, seconds
_FRAMING_SPLIT_BUDGET = 12
_FRAMING_MIN_TOTAL_OUT_FRAMES = 150     # ~5s — too short a take for framing changes to read as intentional
_FRAMING_TX_X_STEP = 0.03
_FRAMING_OVERLAY_OVERLAP_GUARD = 0.5    # skip stamping a piece >50% covered by an existing punch_in


def plan_framing(edl: dict, words: list[dict], *, style: str, theme=None,
                 job_seed: str = "") -> dict:
    """Assign tx_scale/tx_x/tx_y per segment to simulate a 2-3 camera setup.
    Deterministic per (job_seed, take) via `_seeded_rng` — re-renders of the
    same job produce an identical framing schedule. Never touches segments
    already carrying a punch_in overlay covering more than half their span
    (avoids a combined punch-on-punch scale spike)."""
    edl = copy.deepcopy(edl)
    if style in _FRAMING_SKIP_STYLES or not words or not (edl.get("segments") or []):
        return edl
    # A7: a theme can disable framing outright (docu_calm/premium_brand/
    # faceless_explainer all favor a still camera) or override the rotate
    # period; scales are read later, at the per-piece stamping step.
    if theme is not None and theme.framing.get("enabled") is False:
        return edl
    segments = edl["segments"]
    drops = edl.get("drops") or []
    play_order = _play_order(edl)
    index, total_out = _build_output_index(segments, drops, play_order)
    if total_out < _FRAMING_MIN_TOTAL_OUT_FRAMES:
        return edl

    rng = _seeded_rng("framing", job_seed)
    theme_rotate = (theme.framing.get("rotate_s") if theme is not None else None) or None
    lo_s, hi_s = tuple(theme_rotate) if theme_rotate else _FRAMING_ROTATE_S
    boundaries_out: list[int] = []
    cursor = 0.0
    while True:
        cursor += rng.uniform(lo_s, hi_s) * 30.0
        if cursor >= total_out - 45 or len(boundaries_out) >= _FRAMING_SPLIT_BUDGET:
            break
        boundaries_out.append(round(cursor))

    if not boundaries_out:
        return edl

    # Map each output-frame boundary back to a source frame and snap to the
    # nearest word start, so a framing change never lands mid-word.
    snapped_src: set[int] = set()
    for b_out in boundaries_out:
        src = _out_to_src(index, b_out)
        if src is None:
            continue
        snapped = snap_to_word(_frame_to_ms(src), words, "start")
        if isinstance(snapped, int) and snapped > 0:
            snapped_src.add(snapped)

    if not snapped_src:
        return edl

    # Split from LAST boundary to FIRST: split_segment_in_place only ever
    # shifts indices AFTER the split point (mirrors plan_pacing's own
    # last-to-first discipline), so processing highest-frame-first keeps
    # earlier boundaries' target segments valid throughout the loop.
    splits_used = 0
    for b_src in sorted(snapped_src, reverse=True):
        if splits_used >= _FRAMING_SPLIT_BUDGET:
            break
        segments = edl["segments"]
        target_idx = next((i for i, seg in enumerate(segments)
                           if seg["src_in"] < b_src < seg["src_out"]), None)
        if target_idx is None:
            continue
        if split_segment_in_place(edl, target_idx, b_src):
            splits_used += 1

    # Stamp framing onto every piece in PLAY order following the cyclic
    # pattern, so consecutive played pieces (not consecutive source indices)
    # alternate scale — what actually plays back-to-back is what must read as
    # a framing change.
    segments = edl["segments"]
    overlays = edl.get("overlays") or []

    def _overlay_overlap_frac(seg_lo: int, seg_hi: int) -> float:
        span = seg_hi - seg_lo
        if span <= 0:
            return 0.0
        covered = 0
        for o in overlays:
            if o.get("type") != "punch_in":
                continue
            a, b = max(seg_lo, o["src_in"]), min(seg_hi, o.get("src_out", o["src_in"]))
            if b > a:
                covered += b - a
        return covered / span

    theme_scales = (theme.framing.get("scales") if theme is not None else None) or None
    scales = theme_scales if theme_scales else _FRAMING_SCALES
    tx_x_sign = 1
    for step, seg_i in enumerate(_play_order(edl)):
        seg = segments[seg_i]
        if _overlay_overlap_frac(seg["src_in"], seg["src_out"]) > _FRAMING_OVERLAY_OVERLAP_GUARD:
            continue
        kind = _FRAMING_PATTERN[step % len(_FRAMING_PATTERN)]
        scale = scales.get(kind, _FRAMING_SCALES[kind])
        seg["tx_scale"] = scale
        if kind == "wide":
            seg["tx_x"] = 0.0
            seg["tx_y"] = 0.0
        else:
            seg["tx_x"] = _FRAMING_TX_X_STEP * tx_x_sign
            tx_x_sign *= -1
            seg["tx_y"] = -0.02 if scale >= 1.18 else 0.0

    edl["segments"] = segments
    return edl


# ---------------------------------------------------------------------------
# WS4a/4b — hook overlay + emphasis alignment. Both reuse EXISTING overlay
# types (text_sticker, punch_in) and caption_options.highlight_words — no
# schema change. Run BEFORE schedule_interrupts (WS3) so these overlays count
# as existing events the interrupt scheduler must not double-cover, and
# align_emphasis runs before place_hook_overlay/place_end_card so a hook
# overlay synthesized moments later can't collide with an emphasis punch that
# happens to land at the very start of the take.
# ---------------------------------------------------------------------------

_EMPHASIS_TOP_N = 3
_EMPHASIS_PUNCH_GUARD_FRAMES = 30   # skip inserting a punch within this many frames of an existing overlay
_EMPHASIS_HIGHLIGHT_CAP = 12
_HOOK_OVERLAY_HOLD_FRAMES = 45
_HOOK_SKIP_STYLES = {"duet_split"}   # the reacted-to clip owns the open


def align_emphasis(edl: dict, words: list[dict], *, style: str,
                   emphasis_spans: list[tuple[int, int]] | None = None) -> dict:
    """WS4b: rank emphasis spans and give the strongest few real visual/caption
    weight instead of just the single span `_apply_edit_prefs` already
    fallback-punches. Ranked by SPAN LENGTH — `_extract_emphasis_regions`
    (main.py) merges overlapping is_emphasized/auto-highlight spans before this
    ever sees them, which loses which signal produced each span, so length is
    the only honest ranking signal left; a documented simplification, not an
    oversight. Top 3 each get: (a) their longest word appended to
    caption_options.highlight_words (capped), (b) a punch_in over the span if
    the style renders punch-ins and nothing already occupies it within
    _EMPHASIS_PUNCH_GUARD_FRAMES (avoids duplicating _apply_edit_prefs' own
    single-span fallback punch, and avoids stacking two punches on one beat)."""
    edl = copy.deepcopy(edl)
    emphasis_spans = emphasis_spans or []
    if not emphasis_spans or not words:
        return edl

    ranked = sorted(emphasis_spans, key=lambda s: s[1] - s[0], reverse=True)[:_EMPHASIS_TOP_N]
    caption_opts = dict(edl.get("caption_options") or {})
    highlight_words = list(caption_opts.get("highlight_words") or [])
    overlays = list(edl.get("overlays") or [])
    can_punch = style in _PUNCH_STYLES

    def _occupied_near(lo: int, hi: int) -> bool:
        return any(not (o["src_out"] <= lo - _EMPHASIS_PUNCH_GUARD_FRAMES or
                       o["src_in"] >= hi + _EMPHASIS_PUNCH_GUARD_FRAMES) for o in overlays)

    for s_in, s_out in ranked:
        span_words = [w for w in words if s_in <= ms_to_frame(w.get("start_ms", 0))
                      and ms_to_frame(w.get("end_ms", 0)) <= s_out]
        if span_words:
            longest = max(span_words, key=lambda w: len(w.get("word") or ""))
            norm = _norm_word(longest.get("word") or "")
            if norm and norm not in highlight_words and len(highlight_words) < _EMPHASIS_HIGHLIGHT_CAP:
                highlight_words.append(norm)
        if can_punch and not _occupied_near(s_in, s_out):
            overlays.append({"type": "punch_in", "src_in": s_in,
                             "src_out": min(s_out, s_in + 60), "scale": 1.08, "text": ""})

    if highlight_words != (caption_opts.get("highlight_words") or []):
        caption_opts["highlight_words"] = highlight_words
        edl["caption_options"] = caption_opts
    if len(overlays) > len(edl.get("overlays") or []):
        edl["overlays"] = overlays
    return edl


def place_hook_overlay(edl: dict, words: list[dict], *, style: str,
                       hints: dict | None = None, script: dict | None = None) -> dict:
    """WS4a: synthesize a hook-text text_sticker over the first ~1.5s so the
    promise restates visually the instant the video opens (Hormozi/Submagic
    pattern) — text_sticker renders in ALL 7 compositions (text_card only
    renders in 2), so this is the one overlay type guaranteed to work
    everywhere. Skipped for duet_split (the reacted-to clip owns the open),
    when there's no candidate hook text, or when another overlay already
    occupies the first 60 source-adjacent frames (never stack two competing
    opens)."""
    if style in _HOOK_SKIP_STYLES:
        return edl
    hints = hints or {}
    hook_text = (hints.get("hook_text") or "").strip()
    if not hook_text:
        script_hook = ((script or {}).get("hook") or "").strip()
        if script_hook:
            hook_text = " ".join(script_hook.split()[:6])
    if not hook_text:
        return edl

    edl = copy.deepcopy(edl)
    segments = edl.get("segments") or []
    if not segments:
        return edl
    kept = _kept_intervals(segments, edl.get("drops") or [])
    if not kept:
        return edl
    first_kept = kept[0][0]

    overlays = edl.get("overlays") or []
    if any(o["src_in"] < first_kept + 60 for o in overlays):
        return edl

    caption_opts = edl.get("caption_options") or {}
    pos_y = 0.62 if caption_opts.get("position") == "top" else 0.30
    edl["overlays"] = overlays + [{
        "type": "text_sticker", "src_in": first_kept, "src_out": first_kept + _HOOK_OVERLAY_HOLD_FRAMES,
        "text": hook_text[:42], "scale": 1.05, "pos_x": 0.5, "pos_y": pos_y,
        "rotation": 0.0, "color": None, "bg": "box", "font": "inter",
    }]
    return edl


# ---------------------------------------------------------------------------
# A6 (superintelligence epic) — first-1.5s hook PACKAGE, layered on top of
# place_hook_overlay's sticker: (1) frame-1 motion so the video opens
# mid-zoom instead of a static settle, and (2) the "first cut by 3s" rule —
# every take must have SOME visual event (a cut or an overlay) before output
# frame 90, or one gets synthesized. Runs as its OWN opt-in pass name
# ("hook_pack", not folded into "structure"/"all") so it bakes independently.
# ---------------------------------------------------------------------------

_HOOK_PACK_OPEN_PUNCH_FRAMES = 15
_HOOK_PACK_OPEN_SCALE = 1.06
_HOOK_PACK_FIRST_CUT_CEILING_OUT = 90    # ~3s
_HOOK_PACK_FIRST_CUT_MIN_OUT = 45        # ~1.5s — never insert earlier than this


def apply_hook_package(edl: dict, words: list[dict], *, style: str,
                       hints: dict | None = None, theme=None) -> dict:
    """A6: (1) a short eased punch_in over the opening frames so frame-1 is
    already in motion; (2) if nothing (cut boundary or overlay) creates a
    visual event before output frame 90, synthesize a framing bump at the
    first word boundary after frame 45. Skips duet_split (the reacted-to clip
    owns the open, same as place_hook_overlay)."""
    if style in _HOOK_SKIP_STYLES or not words or not (edl.get("segments") or []):
        return edl
    edl = copy.deepcopy(edl)
    segments = edl["segments"]
    drops = edl.get("drops") or []
    play_order = _play_order(edl)
    index, total_out = _build_output_index(segments, drops, play_order)
    if total_out < _HOOK_PACK_FIRST_CUT_CEILING_OUT:
        return edl
    kept = _kept_intervals(segments, drops)
    if not kept:
        return edl
    first_kept = kept[0][0]
    overlays = edl.get("overlays") or []

    # (1) frame-1 motion — skip if something already occupies the very open
    # (e.g. a hand-authored overlay), never stacking two competing opens.
    if not any(o["src_in"] <= first_kept < o.get("src_out", o["src_in"]) for o in overlays):
        open_src_hi = _out_to_src(index, _HOOK_PACK_OPEN_PUNCH_FRAMES)
        if open_src_hi is None or open_src_hi <= first_kept:
            open_src_hi = first_kept + _HOOK_PACK_OPEN_PUNCH_FRAMES
        overlays = overlays + [{"type": "punch_in", "src_in": first_kept, "src_out": open_src_hi,
                                "scale": _HOOK_PACK_OPEN_SCALE, "text": ""}]

    # (2) first-cut-by-3s rule.
    cut_points = {out_start for _, _, out_start, _ in index}
    has_early_cut = any(0 < p < _HOOK_PACK_FIRST_CUT_CEILING_OUT for p in cut_points)
    has_early_overlay = False
    for o in overlays:
        a = _src_to_out(index, o["src_in"])
        if a is not None and 0 < a < _HOOK_PACK_FIRST_CUT_CEILING_OUT:
            has_early_overlay = True
            break
    if not has_early_cut and not has_early_overlay:
        anchor_src = _out_to_src(index, _HOOK_PACK_FIRST_CUT_MIN_OUT)
        if anchor_src is not None:
            anchor_word = snap_to_word(_frame_to_ms(anchor_src), words, "start")
            if isinstance(anchor_word, int) and anchor_word > first_kept:
                target_idx = next((i for i, seg in enumerate(segments)
                                   if seg["src_in"] < anchor_word < seg["src_out"]), None)
                if target_idx is not None and split_segment_in_place(edl, target_idx, anchor_word):
                    for seg in edl["segments"]:
                        if seg["src_in"] == anchor_word:
                            seg["tx_scale"] = max(seg.get("tx_scale", 1.0), 1.12)
                            break

    edl["overlays"] = overlays
    return edl


_END_CARD_SKIP_STYLES = {"fast_cuts", "duet_split"}   # WS5 matrix: loop-friendly / play-freeze own the close


def place_end_card(edl: dict, words: list[dict], *, style: str, hints: dict | None = None) -> dict:
    """WS4c: stamp the plan author's end_card{wanted,text} hint onto the EDL's
    end_card field. build_render_plan does the actual tail-frame-extension
    arithmetic (it owns total_frames); this pass only decides WHETHER one is
    wanted and what it says. Mutually exclusive with trim_loop_tail — enforced
    by apply_retention_passes choosing one or the other, not by this function,
    since that decision needs to see both hints at once. Skipped for fast_cuts
    (WS5: loop-friendly by design, an end-card breaks the loop) and duet_split
    (the play/freeze payoff punch owns the close, per the same matrix)."""
    if style in _END_CARD_SKIP_STYLES:
        return edl
    hints = hints or {}
    end_card_hint = hints.get("end_card") or {}
    text = (end_card_hint.get("text") or "").strip()
    if not end_card_hint.get("wanted") or not text:
        return edl
    edl = copy.deepcopy(edl)
    edl["end_card"] = {"text": text, "frames": 75, "show_handle": True}
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

# A4 (superintelligence epic) — jittered cadence + type variety, opt-in via the
# "jitter" pass token (never folded into the fixed-cadence default so prod's
# existing RETENTION_PASSES=all baseline is untouched until this bakes on its
# own). A perfectly metronomic interrupt cadence is itself an amateur tell;
# jitter draws each gap's target independently instead of using one constant.
_INTERRUPT_JITTER_S = (3.0, 5.0)                   # per-gap cadence target range, seconds
_INTERRUPT_JITTER_HOLD_FRAMES = (9, 15)            # short snap-zoom window vs. the fixed 75f hold
_INTERRUPT_JITTER_TYPES = ("punch", "framing_pop", "text_sticker")
_INTERRUPT_FRAMING_POP_BUMP = 0.10
_INTERRUPT_FRAMING_POP_SPLIT_BUDGET = 2 * _INTERRUPT_MAX_PER_CLIP


def _insert_framing_pop(edl: dict, src_lo: int, src_hi: int, splits_left: list[int]) -> bool:
    """A4: one 'framing_pop' interrupt type — a one-clip tx_scale bump over
    [src_lo, src_hi) instead of a punch_in overlay, so consecutive interrupts
    can read as a genuine camera change rather than a repeated zoom. Splits
    `edl["segments"]` IN PLACE (mirrors plan_pacing's own split discipline);
    returns False without mutating if the window doesn't sit inside a single
    existing segment, a split fails, or the split budget is spent — the
    caller falls back to a punch_in in that case."""
    if splits_left[0] <= 0:
        return False
    segments = edl["segments"]
    target_idx = next((i for i, seg in enumerate(segments)
                       if seg["src_in"] <= src_lo and src_hi <= seg["src_out"]), None)
    if target_idx is None:
        return False
    if segments[target_idx]["src_in"] < src_lo:
        if not split_segment_in_place(edl, target_idx, src_lo):
            return False
        splits_left[0] -= 1
        target_idx += 1
    segments = edl["segments"]
    if splits_left[0] > 0 and src_hi < segments[target_idx]["src_out"]:
        if not split_segment_in_place(edl, target_idx, src_hi):
            return False
        splits_left[0] -= 1
    segments = edl["segments"]
    seg = segments[target_idx]
    seg["tx_scale"] = min(3.0, (seg.get("tx_scale") or 1.0) + _INTERRUPT_FRAMING_POP_BUMP)
    return True


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
                        prefs: dict | None = None, hints: dict | None = None,
                        jitter: bool = False, job_seed: str = "", theme=None,
                        genre_density: str = "") -> dict:
    """Guarantee a visual change at least every N output frames (N by style x
    density — or, with `jitter` on, a per-gap target independently drawn from
    _INTERRUPT_JITTER_S, since a perfectly regular cadence is itself an
    amateur tell) by inserting punch_in overlays, a one-clip framing_pop
    (jitter only), or text_sticker keyword pops (faceless, which has no face
    to zoom) into gaps left uncovered by cuts, speed changes, or any
    overlay/b-roll already placed. Skips fast_cuts and duet_split entirely
    (native cut cadence / play-freeze rhythm already carries this). Without
    jitter, alternates punch scale between two values (today's live
    behavior, byte-identical). With jitter, rotates among the allowed types
    per style, never repeating the previous insertion's type, and uses a
    short 9-15f snap-zoom window instead of the fixed 75f hold — deterministic
    per job_seed via `_seeded_rng` so re-renders of the same job match."""
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

    # A7/A9 precedence: the LLM plan's own hint wins, then the theme's density,
    # then the genre profile's density (A9 — a pre-resolved plain string from
    # main.py, so this module never needs to import prompts.GENRE_PROFILES),
    # then the style default.
    theme_density = (theme.interrupts.get("density") if theme is not None else None)
    density = hints.get("interrupt_density") or theme_density or genre_density or "standard"
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
    rng = _seeded_rng("interrupts", job_seed) if jitter else None
    if not can_punch:
        allowed_types = ("text_sticker",)
    elif theme is not None and theme.interrupts.get("types"):
        # theme.max_types caps how many DISTINCT types this take rotates
        # through (a calmer theme wants less variety, not just less frequency).
        max_types = theme.interrupts.get("max_types") or len(theme.interrupts["types"])
        allowed_types = tuple(theme.interrupts["types"][:max_types]) or _INTERRUPT_JITTER_TYPES
    else:
        allowed_types = _INTERRUPT_JITTER_TYPES
    framing_splits_left = [_INTERRUPT_FRAMING_POP_SPLIT_BUDGET]
    inserted = 0
    scale_i = 0
    last_event_out = 0
    last_type: str | None = None
    new_overlays: list[dict] = []

    def _try_insert(anchor_target: int, ceiling: int) -> int | None:
        """Insert one interrupt anchored at the next caption word on/after
        `anchor_target`, provided it stays clear of `ceiling` (the next real
        event) and the guards. Returns the new `last_event_out` on success."""
        nonlocal inserted, scale_i, last_type
        anchor = next((out for out, _ in words_by_out if out >= anchor_target), None)
        if anchor is None or anchor >= ceiling - 6 or anchor - last_event_out < _INTERRUPT_MIN_SPACING:
            return None
        hold = rng.randint(*_INTERRUPT_JITTER_HOLD_FRAMES) if jitter else _INTERRUPT_HOLD_FRAMES
        out_hi = min(anchor + hold, ceiling - 6)
        src_lo, src_hi = _out_to_src(index, anchor), _out_to_src(index, max(anchor + 1, out_hi))
        if src_lo is None or src_hi is None or src_hi <= src_lo:
            return None

        it_type = "punch" if can_punch else "text_sticker"
        if jitter:
            pool = [t for t in allowed_types if t != last_type] or list(allowed_types)
            it_type = rng.choice(pool)
            if it_type == "framing_pop":
                if _insert_framing_pop(edl, src_lo, src_hi, framing_splits_left):
                    inserted += 1
                    last_type = "framing_pop"
                    return out_hi
                it_type = "punch" if can_punch else "text_sticker"   # split failed — fall back

        if it_type == "punch" and can_punch:
            new_overlays.append({"type": "punch_in", "src_in": src_lo, "src_out": src_hi,
                                 "scale": _INTERRUPT_SCALES[scale_i % 2], "text": ""})
            scale_i += 1
        else:
            word_text = next((w["word"] for out, w in words_by_out if out == anchor), "")
            if not word_text:
                return None
            new_overlays.append({"type": "text_sticker", "src_in": src_lo, "src_out": src_hi,
                                 "scale": 1.0, "text": word_text[:24],
                                 "pos_x": 0.5, "pos_y": 0.3, "rotation": 0.0,
                                 "color": None, "bg": "box", "font": "inter"})
            it_type = "text_sticker"
        inserted += 1
        last_type = it_type
        return out_hi

    # Forward-progress state machine: at every step, either an existing event
    # (cut point or occupied overlay/b-roll window) arrives before cadence would
    # be exceeded — consume it and move on — or cadence IS exceeded with nothing
    # else covering that stretch, so insert a new punch/pop there. `_ITER_CAP`
    # is a hard backstop (never hit in practice — every branch strictly advances
    # `last_event_out`) so a future edit here can't ever infinite-loop.
    # A7: theme.interrupts.jitter_frames is already in FRAMES (unlike the
    # module default _INTERRUPT_JITTER_S, in seconds) — used as-is when present.
    theme_jitter_frames = (theme.interrupts.get("jitter_frames") if theme is not None else None)
    _ITER_CAP = 4 * _INTERRUPT_MAX_PER_CLIP + len(cut_points) + len(occupied) + 4
    for _ in range(_ITER_CAP):
        if last_event_out >= total_out - _INTERRUPT_CTA_GUARD or inserted >= _INTERRUPT_MAX_PER_CLIP:
            break
        if jitter and theme_jitter_frames:
            gap_cadence = max(_INTERRUPT_CADENCE_FLOOR, rng.randint(*theme_jitter_frames))
        elif jitter:
            gap_cadence = max(_INTERRUPT_CADENCE_FLOOR, round(rng.uniform(*_INTERRUPT_JITTER_S) * 30))
        else:
            gap_cadence = cadence
        next_cut = next((p for p in cut_points if p > last_event_out), total_out)
        next_occ = next(((a, b) for a, b in occupied if b > last_event_out), None)
        if next_occ is not None and next_occ[0] <= last_event_out + gap_cadence:
            # an overlay/b-roll window arrives before cadence is exceeded — it
            # counts as the visual event; jump past its far edge.
            last_event_out = max(last_event_out, next_occ[1])
            continue
        if next_cut - last_event_out <= gap_cadence:
            last_event_out = next_cut   # a cut arrives in time — it counts as the event
            continue
        # cadence exceeded with nothing else covering it — insert here. The
        # ceiling is whichever comes first: the next cut or the next occupied
        # window's start (never insert past either).
        ceiling = min(next_cut, next_occ[0] if next_occ else total_out)
        anchor_target = max(last_event_out + gap_cadence, _INTERRUPT_HOOK_GUARD)
        new_last = _try_insert(anchor_target, ceiling)
        last_event_out = new_last if new_last is not None else ceiling

    if new_overlays:
        edl["overlays"] = (edl.get("overlays") or []) + new_overlays
    return edl


# ---------------------------------------------------------------------------
# WS4d — deterministic SFX placement [audio.sfx; schema v2]. Runs LAST of all
# retention passes (after schedule_interrupts) so it sees every punch_in/hook
# overlay everything else already placed.
# ---------------------------------------------------------------------------

_SFX_BUDGET_PER_30S = 5
_SFX_MIN_SPACING_FRAMES = 15   # ~0.5s @ 30fps
_SFX_END_GUARD_FRAMES = 15     # none in the last 15 source frames


def synthesize_sfx(edl: dict, words: list[dict], *,
                   sfx_assets: dict[str, str | None] | None = None, theme=None) -> dict:
    """WS4d: deterministically place SFX one-shots at transitions and at every
    punch_in overlay (whichever pass placed it — align_emphasis, the interrupt
    scheduler, or a hand-authored one), budget-capped at ~5 per 30s of KEPT
    source, >=15f apart, none in the last 15f. Only emits a cue for a `kind`
    that actually has a resolved URL in `sfx_assets` (main.py's SFX_ASSETS) —
    build_render_plan drops any cue whose url is falsy too, so this is
    belt-and-suspenders, not the only guard.

    Simplification: every punch_in overlay is tagged "pop" regardless of which
    pass created it (there's no provenance field on Overlay to distinguish
    "emphasis punch" from "interrupt punch", and adding one purely for SFX
    tagging isn't worth the schema surface) — transitions get "whoosh". The
    plan's original hook/hit + emphasis/pop + interrupt/whoosh three-way split
    is a sound-design nicety on top of this, not implemented here."""
    edl = copy.deepcopy(edl)
    sfx_assets = sfx_assets or {}
    segments = edl.get("segments") or []
    if not segments:
        return edl
    kept = _kept_intervals(segments, edl.get("drops") or [])
    if not kept:
        return edl

    def _inside(f: int) -> bool:
        return any(lo <= f < hi for lo, hi in kept)

    total_kept_frames = sum(hi - lo for lo, hi in kept)
    budget = max(1, round(_SFX_BUDGET_PER_30S * total_kept_frames / (30 * 30)))
    last_frame = kept[-1][1]

    candidates: set[tuple[int, str]] = set()
    for t in edl.get("transitions") or []:
        si = t.get("after_segment", -1)
        if 0 <= si < len(segments):
            candidates.add((segments[si]["src_out"], "whoosh"))
    for o in edl.get("overlays") or []:
        if o.get("type") == "punch_in":
            candidates.add((o["src_in"], "pop"))

    # A7: a theme's gain_db (dB) overrides the module SFX_GAIN_DEFAULT (already
    # -14dB) when the bundle wants its one-shots hotter/quieter than the default.
    theme_gain_db = (theme.sfx.get("gain_db") if theme is not None else None)
    gain = (10 ** (theme_gain_db / 20)) if theme_gain_db is not None else SFX_GAIN_DEFAULT

    ordered = sorted(c for c in candidates if _inside(c[0]))
    sfx: list[dict] = []
    last_placed = -_SFX_MIN_SPACING_FRAMES
    for f, kind in ordered:
        if len(sfx) >= budget:
            break
        if f - last_placed < _SFX_MIN_SPACING_FRAMES or f >= last_frame - _SFX_END_GUARD_FRAMES:
            continue
        url = sfx_assets.get(kind)
        if not url:
            continue
        sfx.append({"src_in": f, "kind": kind, "gain": gain, "url": url})
        last_placed = f

    if sfx:
        audio = dict(edl.get("audio") or {})
        audio["sfx"] = (audio.get("sfx") or []) + sfx
        edl["audio"] = audio
    return edl


def apply_retention_passes(edl: dict, words: list[dict], *, style: str,
                           prefs: dict | None = None, emphasis_spans: list | None = None,
                           dossier: dict | None = None, hints: dict | None = None,
                           script: dict | None = None, level: str = "default",
                           sfx_assets: dict[str, str | None] | None = None,
                           job_seed: str = "", theme=None, genre_density: str = "") -> dict:
    """Entry point called once from `_run_edit`, after EITHER author path builds
    its EDL and before `_resolve_broll`/`build_render_plan`. `hints` carries the
    plan author's typed decisions (pacing/interrupt_density/hook_text/end_card/
    music) when available, or {} from the legacy path / safe-default (every pass
    has a style-driven default so an empty hints dict is a fully valid input).
    `sfx_assets` is main.py's SFX_ASSETS dict (kind -> hosted URL or None);
    passed as a parameter rather than imported, since main.py already imports
    THIS module and a reverse import would be circular. `job_seed` (the job id)
    drives every deterministic-jitter pass (framing, interrupt jitter) so
    re-renders of the same job produce identical output; `theme` (A7 style
    bundles) is gated purely by its own presence (None = off), independent of
    the RETENTION_PASSES csv below — a theme's caption/grade/duck defaults are
    a separate feature from the deterministic editing passes."""
    prefs = prefs or {}
    hints = hints or {}

    # A7: theme application runs FIRST and independently of RETENTION_PASSES
    # (a theme is a caption/grade/audio-duck DEFAULT, not an editing pass) —
    # None (EDIT_THEMES off, or no theme resolved) is a total no-op.
    if theme is not None:
        edl = _safe_pass("apply_theme", edl, themes_mod.apply_theme, theme, prefs=prefs)

    enabled = _enabled_passes()
    if not enabled or not words:
        return edl

    if "filler" in enabled and prefs.get("filler_trim") != "off":
        edl = _safe_pass("sweep_residual_fillers", edl, sweep_residual_fillers, words, level)

    # Retake dedup runs AFTER the filler sweep (so utterance token sets are already
    # filler-light) and BEFORE pacing (so a dropped take isn't first sped up then cut).
    if "retake" in enabled:
        edl = _safe_pass("dedupe_retakes", edl, dedupe_retakes, words)

    if "pacing" in enabled and prefs.get("pacing") is not False:
        edl = _safe_pass("plan_pacing", edl, plan_pacing, words, style=style,
                         emphasis_spans=emphasis_spans, dossier=dossier, hints=hints)

    # A3: simulated multicam framing — opt-in via the "framing" token (never
    # folded into "all"; bakes independently). Runs after pacing (so it walks
    # the post-speed-change timeline) and before emphasis (so a later
    # emphasis/hook punch can react to whatever framing is already there).
    if "framing" in enabled and prefs.get("framing") is not False:
        edl = _safe_pass("plan_framing", edl, plan_framing, words,
                         style=style, theme=theme, job_seed=job_seed)

    if "emphasis" in enabled:
        edl = _safe_pass("align_emphasis", edl, align_emphasis, words,
                         style=style, emphasis_spans=emphasis_spans)

    if "structure" in enabled:
        # end_card XOR loop-tail: a wanted end_card means "hold on a final beat
        # with a CTA", the opposite intent of a loop-friendly trimmed tail — the
        # hint decides which one runs, never both. Styles place_end_card itself
        # would skip (fast_cuts/duet_split, WS5) must still get their loop-tail
        # trim — checking the style here too, not just the hint, so those
        # styles never fall through to NEITHER pass firing.
        end_card_hint = hints.get("end_card") or {}
        wants_end_card = (bool(end_card_hint.get("wanted"))
                         and bool((end_card_hint.get("text") or "").strip())
                         and style not in _END_CARD_SKIP_STYLES)
        if wants_end_card:
            edl = _safe_pass("place_end_card", edl, place_end_card, words, style=style, hints=hints)
        else:
            edl = _safe_pass("trim_loop_tail", edl, trim_loop_tail, words)
        edl = _safe_pass("place_hook_overlay", edl, place_hook_overlay, words,
                         style=style, hints=hints, script=script)

    # A6: hook PACKAGE (frame-1 motion + first-cut-by-3s) — opt-in via
    # "hook_pack", layered on top of place_hook_overlay's sticker above, so it
    # can see whether the sticker already occupies the open.
    if "hook_pack" in enabled:
        edl = _safe_pass("apply_hook_package", edl, apply_hook_package, words,
                         style=style, hints=hints, theme=theme)

    # interrupts runs after emphasis/structure (per WS3): it needs to see every
    # event they already placed so it never double-covers them. "jitter" is an
    # opt-in modifier on the SAME pass (not a separate token) — off by default,
    # so the live RETENTION_PASSES=all baseline is byte-identical until baked.
    if "interrupts" in enabled:
        edl = _safe_pass("schedule_interrupts", edl, schedule_interrupts, words,
                         style=style, prefs=prefs, hints=hints,
                         jitter=("jitter" in enabled), job_seed=job_seed, theme=theme,
                         genre_density=genre_density)

    # sfx runs LAST of all: it sees every transition/overlay every prior pass
    # (including interrupts) placed.
    if "sfx" in enabled:
        edl = _safe_pass("synthesize_sfx", edl, synthesize_sfx, words,
                         sfx_assets=sfx_assets, theme=theme)

    return edl
