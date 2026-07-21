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
import os

from app.edl import (
    ALWAYS_FILLERS, ms_to_frame, _frame_to_ms, snap_to_word,
    detect_disfluencies, split_segment_in_place, _PUNCH_STYLES,
    _kept_intervals, _kept_frames, _coalesce_drops, _norm_word,
    _MIN_DURATION_FRAMES, MIN_CLIP_OUTPUT_FRAMES, check_edl_invariants, SFX_GAIN_DEFAULT,
    _seeded_rng,
)
from app import themes as themes_mod

# csv of pass names to run; "" (default) = everything off. "all" (standalone OR as a
# csv member) expands to the core set; framing/hook_pack/jitter/cold_open/dropout are
# opt-in extras that must be listed explicitly (e.g. "all,framing,hook_pack,jitter").
# Individual names: filler, retake, pacing, emphasis, interrupts, sfx,
# structure (hook/end_card/loop_tail), cold_open, dropout, framing, hook_pack, jitter,
# beat_snap (WS3 — needs catalog beat grids; inert without them).
_ENV_PASSES = os.environ.get("RETENTION_PASSES", "")
_ALL_PASSES = {"filler", "retake", "pacing", "emphasis", "interrupts", "sfx", "structure"}


def _enabled_passes() -> set[str]:
    raw = _ENV_PASSES.strip()
    if not raw:
        return set()
    tokens = {p.strip() for p in raw.split(",") if p.strip()}
    # v2 FIX (live prod bug): "all" was only expanded when it was the ENTIRE value —
    # "all,framing,hook_pack,jitter" left a literal "all" token and silently disabled
    # filler/retake/pacing/emphasis/interrupts/sfx/structure. Expand it as a member too.
    if "all" in tokens:
        tokens = (tokens - {"all"}) | _ALL_PASSES
    return tokens


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


# Script-aware retake detection: when both utterances strongly match the SAME intended
# script sentence, they're deliveries of that line even if their mutual token similarity
# dips into this gray zone (a stumble reworded half the line). Conservative — only fires
# with script corroboration, never on transcript-only similarity below _RETAKE_SIM.
_RETAKE_GRAY_SIM = 0.45        # floor for the script-corroborated path (below _RETAKE_SIM=0.62)
_SCRIPT_MATCH_CONTAIN = 0.60   # min containment of an utterance's tokens in a script sentence


def _script_sentences(script_text: str) -> list[set]:
    """Token sets for each script sentence (split on . ! ? and newlines), filler-normalized."""
    import re
    out: list[set] = []
    for chunk in re.split(r"[.!?\n]+", script_text or ""):
        toks = {_norm_word(w) for w in chunk.split() if _norm_word(w)}
        if len(toks) >= _RETAKE_MIN_WORDS:
            out.append(toks)
    return out


def _same_script_line(a: list[str], b: list[str], sentences: list[set]) -> bool:
    """True when a and b each contain (≥_SCRIPT_MATCH_CONTAIN) the SAME script sentence —
    i.e. both are deliveries of one intended line."""
    if not sentences:
        return False
    sa, sb = set(a), set(b)
    for s in sentences:
        if not s:
            continue
        if len(sa & s) / len(s) >= _SCRIPT_MATCH_CONTAIN and len(sb & s) / len(s) >= _SCRIPT_MATCH_CONTAIN:
            return True
    return False


def dedupe_retakes(edl: dict, words: list[dict], script_text: str = "") -> dict:
    """Drop the earlier of two near-duplicate utterances (a flubbed take re-delivered),
    keeping the LAST clean delivery. Compares each utterance to the next one AND the one
    after (when a short bridge like 'ugh, let me redo that' sits between the takes).
    Frame-based drops, coalesced; fail-soft and floor-guarded like every other pass.

    Script-aware (when `script_text` is a real read script): two utterances that both
    deliver the SAME script sentence are treated as retakes even if their mutual similarity
    dips into the gray zone — catching a reworded redo that transcript-only matching misses.
    Never fires below the gray floor, and always drops the EARLIER take."""
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
    script_sents = _script_sentences(script_text)
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
            if len(toks[j]) < _RETAKE_MIN_WORDS:
                continue
            sim = _shingle_sim(toks[i], toks[j])
            # Standard gate OR the script-corroborated gray-zone gate.
            if sim < _RETAKE_SIM and not (
                    sim >= _RETAKE_GRAY_SIM and _same_script_line(toks[i], toks[j], script_sents)):
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

# ---------------------------------------------------------------------------
# Cold open (v2) — 50-60% of short-form drop-off happens in the first 3s and the
# first spoken word must land within ~0.3-0.5s of frame 0 (TikTok/creator-guide
# consensus). Nothing else trims leading pre-speech (strip_fillers skips the gap
# before the first word by design; trim_loop_tail is tail-only), so this pass
# tightens the head of the FIRST PLAYED segment up to the first kept word, minus
# a natural-padding guard that also guarantees the word onset is never clipped.
# Runs AFTER retake dedupe (a dropped flubbed opener changes which word is
# first) and BEFORE pacing/framing (their hook-protect zones then cover the real
# content open instead of dead lead). Own opt-in token "cold_open".
# ---------------------------------------------------------------------------

_COLD_OPEN_MAX_ONSET_OUT = 12   # ~0.4s: onset later than this output frame → trim
_COLD_OPEN_PAD_FRAMES = 8       # natural padding kept before the word (~0.27s, in 3-9f band)
_COLD_OPEN_MIN_TRIM = 4         # don't bother shaving fewer frames than this


def trim_cold_open(edl: dict, words: list[dict]) -> dict:
    edl = copy.deepcopy(edl)
    segments = edl.get("segments") or []
    if not segments or not words:
        return edl
    drops = edl.get("drops") or []
    kept = _kept_intervals(segments, drops)
    if not kept:
        return edl
    first_kept = kept[0][0]
    # First word whose start lands in kept footage at/after the open.
    onset: int | None = None
    for w in words:
        wf = ms_to_frame(w.get("start_ms", 0))
        if wf >= first_kept and any(lo <= wf < hi for lo, hi in kept):
            onset = wf
            break
    if onset is None:
        return edl
    # Output lead before the onset (speeds are 1.0 this early — pacing runs later).
    lead_out = sum(min(hi, onset) - lo for lo, hi in kept if lo < onset)
    if lead_out <= _COLD_OPEN_MAX_ONSET_OUT:
        return edl                                 # already starts hot
    head_idx = _play_order(edl)[0]
    head = segments[head_idx]
    new_src_in = max(head["src_in"], onset - _COLD_OPEN_PAD_FRAMES)
    if new_src_in - head["src_in"] < _COLD_OPEN_MIN_TRIM or new_src_in >= head["src_out"]:
        return edl
    trimmed = dict(head)
    trimmed["src_in"] = new_src_in
    new_segments = list(segments)
    new_segments[head_idx] = trimmed
    # Floor guard: never trim the take below the minimum output duration.
    if _kept_frames({"segments": new_segments, "drops": drops}) < _MIN_DURATION_FRAMES:
        return edl
    edl["segments"] = new_segments
    return edl


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
# v7 pacing research: global baseline 1.05–1.15x is the community norm and 1.05 sits
# exactly at the speech-tempo JND (Weber fraction ~5%, Quené 2007) — imperceptible
# even in A/B comparison. "subtle" 1.03→1.05.
PACING_LIFT_MULT = {"none": 1.0, "subtle": 1.05, "medium": 1.08}
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

# v7 SENTENCE-RATE NORMALIZATION (the "when to speed up" engine, research-grounded):
# normalize slow sentences toward a target words-per-minute instead of hunting one
# low-info stretch per segment. Sources: conversational baseline 120-150wpm vs
# short-form target 160-185wpm; tempo JND ~5% (adjacent-step cap 0.05 in continuous
# speech, 0.10 across a ≥300ms pause — the pause resets the tempo anchor); 1.25x is
# the comfort ceiling, 1.30 our per-sentence cap under the 1.35 spoken hard cap;
# silence compressed FIRST (Overcast/TimeBolt principle — silence FF now defaults ON).
RATE_TARGET_WPM_MIN = 162         # normalize up toward at least this...
RATE_TARGET_WPM_MAX = 185         # ...but never chase a rate above this
RATE_SENTENCE_GAP_MS = 350        # sentence boundary when no terminal punctuation
RATE_MIN_ACTION_FRAMES = 45       # ~1.5s — shortest run worth a split
RATE_SENT_SPEED_CAP = 1.30        # per-sentence ceiling (1.25 comfort + headroom, < 1.35 hard cap)
RATE_DELTA_CONTIGUOUS = 0.05      # max step vs previous sentence, continuous speech (1 JND)
RATE_DELTA_ACROSS_PAUSE = 0.10    # max step across a ≥300ms pause
RATE_JND_DEADBAND = 0.95          # sentences within 5% of target are left alone


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


def _sentences_from_words(words: list[dict]) -> list[tuple[float, float, int]]:
    """[(start_ms, end_ms, word_count)] — split on terminal punctuation (. ! ? …) or a
    ≥RATE_SENTENCE_GAP_MS silence (covers punctuation-free ASR output)."""
    sents: list[tuple[float, float, int]] = []
    cur_start: float | None = None
    cur_n = 0
    last_end: float | None = None
    for w in words:
        if not w.get("word"):
            continue
        s, e = w.get("start_ms", 0), w.get("end_ms", 0)
        if cur_start is not None and last_end is not None and s - last_end >= RATE_SENTENCE_GAP_MS:
            sents.append((cur_start, last_end, cur_n))
            cur_start, cur_n = None, 0
        if cur_start is None:
            cur_start = s
        cur_n += 1
        last_end = e
        if (w.get("word") or "").rstrip("\"'’”)").endswith((".", "!", "?", "…")):
            sents.append((cur_start, e, cur_n))
            cur_start, cur_n = None, 0
    if cur_start is not None and cur_n and last_end is not None:
        sents.append((cur_start, last_end, cur_n))
    return sents


def _protect_source_zones(edl: dict) -> list[tuple[int, int]]:
    """Hook (first ~3s) + CTA (last ~2s) of KEPT footage as source-coord ranges,
    walked in play order — index-free, so it stays correct after any splits."""
    segs = edl.get("segments") or []
    drops = edl.get("drops") or []
    order = _play_order(edl)
    kept_by_seg = {i: _segment_kept_ranges(segs[i], drops) for i in range(len(segs))}
    zones: list[tuple[int, int]] = []
    remaining = HOOK_PROTECT_OUT_FRAMES
    for i in order:
        if remaining <= 0:
            break
        for lo, hi in kept_by_seg.get(i, []):
            if remaining <= 0:
                break
            take = min(remaining, hi - lo)
            zones.append((lo, lo + take))
            remaining -= take
    remaining = CTA_PROTECT_OUT_FRAMES
    for i in reversed(order):
        if remaining <= 0:
            break
        for lo, hi in reversed(kept_by_seg.get(i, [])):
            if remaining <= 0:
                break
            take = min(remaining, hi - lo)
            zones.append((hi - take, hi))
            remaining -= take
    return zones


def _subtract_zone(rng: tuple[int, int], zone: tuple[int, int]) -> list[tuple[int, int]]:
    lo, hi = rng
    z_lo, z_hi = zone
    if z_hi <= lo or z_lo >= hi:
        return [rng]
    out = []
    if z_lo > lo:
        out.append((lo, z_lo))
    if z_hi < hi:
        out.append((z_hi, hi))
    return out


def _normalize_sentence_rates(edl: dict, words: list[dict], *,
                              emphasis_spans: list[tuple[int, int]],
                              lift_mult: float, splits_used: int, frames_saved: int,
                              total_kept_frames: int, take_wpm: float) -> tuple[int, int]:
    """v7 sentence-rate normalization: every sentence slower than the WPM target gets
    its own speed toward the target — clamped to the 1.30 sentence cap, quantized to
    0.05 steps, and SMOOTHED so adjacent sentences never differ by more than one tempo
    JND (0.05 in continuous speech, 0.10 across a ≥300ms pause — the pause resets the
    listener's tempo anchor). Speed changes therefore only ever land AT sentence
    boundaries, never mid-phrase — the tempo-lurch fluidity artifact of the old
    engine. Hook/CTA zones and emphasis spans are excluded (never speed the payoff).
    Mutates edl in place; returns updated (splits_used, frames_saved)."""
    if take_wpm <= 0 or not words:
        return splits_used, frames_saved
    target = min(RATE_TARGET_WPM_MAX, max(RATE_TARGET_WPM_MIN, take_wpm))
    protect = _protect_source_zones(edl)
    actions: list[tuple[int, int, float]] = []
    prev_speed, prev_end_ms = lift_mult, None
    for s_ms, e_ms, n in _sentences_from_words(words):
        lo, hi = int(round(s_ms * 30 / 1000)), int(round(e_ms * 30 / 1000))
        if hi - lo <= 0 or n == 0:
            continue
        wpm = _wpm(n, hi - lo)
        gap_ms = (s_ms - prev_end_ms) if prev_end_ms is not None else 10_000.0
        want = lift_mult
        if 0 < wpm < target * RATE_JND_DEADBAND:
            want = min(RATE_SENT_SPEED_CAP, target / wpm)
        cap = RATE_DELTA_ACROSS_PAUSE if gap_ms >= 300 else RATE_DELTA_CONTIGUOUS
        want = max(prev_speed - cap, min(prev_speed + cap, want))
        want = max(lift_mult, min(SPOKEN_SPEED_CAP, int(want * 20 + 1e-6) / 20))
        prev_speed, prev_end_ms = want, e_ms
        if want <= lift_mult + 0.01:
            continue
        ranges = [(lo, hi)]
        for zone in protect + list(emphasis_spans or []):
            ranges = [r for rng in ranges for r in _subtract_zone(rng, zone)]
        for r_lo, r_hi in ranges:
            if r_hi - r_lo >= RATE_MIN_ACTION_FRAMES:
                actions.append((r_lo, r_hi, want))
    # Merge adjacent same-speed sentences into one run — fewer splits, steadier tempo.
    actions.sort(key=lambda a: a[0])
    merged: list[list] = []
    for lo, hi, spd in actions:
        if merged and abs(merged[-1][2] - spd) < 1e-6 and lo - merged[-1][1] <= 10:
            merged[-1][1] = hi
        else:
            merged.append([lo, hi, spd])
    # Apply last→first so split indices stay valid for the not-yet-processed runs.
    for lo, hi, spd in sorted(merged, key=lambda a: -a[0]):
        if splits_used + 2 > PACING_SPLIT_BUDGET:
            break
        segs = edl["segments"]
        idx = next((i for i, sg in enumerate(segs)
                    if sg["src_in"] <= lo < sg["src_out"]), None)
        if idx is None:
            continue
        sg = segs[idx]
        if abs(sg.get("speed", 1.0) - lift_mult) > 1e-6:
            continue                        # already carries a silence-FF speed — don't stack
        a, b = max(lo, sg["src_in"]), min(hi, sg["src_out"])
        if b - a < RATE_MIN_ACTION_FRAMES:
            continue
        projected_out = round((b - a) / spd)
        if projected_out < MIN_CLIP_OUTPUT_FRAMES:
            continue
        save = (b - a) - projected_out
        if total_kept_frames and (frames_saved + save) / total_kept_frames > COMPRESSION_CAP_FRACTION:
            continue
        if a > sg["src_in"]:
            split_segment_in_place(edl, idx, a)
            splits_used += 1
            idx += 1
        if b < edl["segments"][idx]["src_out"]:
            split_segment_in_place(edl, idx, b)
            splits_used += 1
        edl["segments"][idx]["speed"] = spd
        frames_saved += save
    return splits_used, frames_saved


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

    # v7: defaults ON (Overcast/TimeBolt principle — compress silence before touching
    # speech rate); the plan author can still disable via the hint.
    fast_forward_silences = bool(pacing_hints.get("fast_forward_silences", True))

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

        # v7: the old one-low-info-stretch-per-segment branch is replaced by
        # sentence-level rate normalization AFTER this loop (it covers every slow
        # sentence in the take, not one stretch per original segment).

    splits_used, frames_saved = _normalize_sentence_rates(
        edl, words, emphasis_spans=emphasis_spans, lift_mult=lift_mult,
        splits_used=splits_used, frames_saved=frames_saved,
        total_kept_frames=total_kept_frames, take_wpm=take_median_wpm)
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
# Build 54 tone-down (was 1.10/1.18) — bounded below by the ≥8% adjacent-delta floor:
# a framing change under 8% reads as a glitch, not an intentional camera change (the
# same_framing_adjacent lint enforces exactly this), so "subtle" can't go below ~1.09.
_FRAMING_SCALES = {"wide": 1.0, "mid": 1.09, "close": 1.14}
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
    # Build 55 audit: theme scales bypassed the owner's zoom tone-down (gen_z shipped
    # mid 1.15/close 1.2 — the exact look that was rejected). Themes keep their relative
    # character but are capped element-wise at the global ladder's ceiling.
    if theme_scales:
        _cap = {"wide": 1.0, "mid": _FRAMING_SCALES["mid"], "close": _FRAMING_SCALES["close"]}
        theme_scales = {k: min(float(v), _cap.get(k, float(v))) for k, v in theme_scales.items()}
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
# Title v2 (2026-07-17, research-verified): the hook title stays up until the first spoken
# sentence ENDS, clamped 2–5s in OUTPUT frames (the old fixed 45 src frames shrank below the
# ~2s readability floor whenever the open had filler drops or a pacing lift). Fallback 3s
# when no sentence boundary is derivable (punctuation-free transcripts).
_HOOK_HOLD_MIN_OUT = 60      # 2s — readability floor ("read it twice")
_HOOK_HOLD_MAX_OUT = 150     # 5s — TikTok talking-head guidance ceiling
_HOOK_HOLD_FALLBACK_OUT = 90  # 3s
_HOOK_SENTENCE_SCAN_MS = 8000  # look for the first sentence end within ~8s of speech
_HOOK_TEXT_MAX_CHARS = 60    # word-boundary clamp (~8 words; ≤2 rendered lines at 67px/86%)
_HOOK_SKIP_STYLES = {"duet_split"}   # the reacted-to clip owns the open

_HOOK_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "e.g.", "i.e.",
                       "u.s.", "a.m.", "p.m.", "no.", "inc.", "co.", "jr.", "sr."}


def _normalize_hook_text(text: str, uppercase: bool = False) -> str:
    """Deterministic title copy normalization (never trust the LLM's formatting):
    collapse whitespace; strip terminal '.'/'…' (keep '?'/'!' — question/claim phrasing
    is the convention); clamp to _HOOK_TEXT_MAX_CHARS on a WORD boundary (no ellipsis,
    no mid-word chops); sentence case is preserved AS AUTHORED (Title Case was refuted —
    "looks like PowerPoint"; the 2026 native idiom is sentence-case questions/claims);
    ALL CAPS only when the caption grammar is uppercase (high-energy themes) so the
    title and captions never disagree."""
    t = " ".join((text or "").split())
    while t and t[-1] in ".…":
        t = t[:-1].rstrip()
    if len(t) > _HOOK_TEXT_MAX_CHARS:
        clipped = t[:_HOOK_TEXT_MAX_CHARS + 1]
        cut = clipped.rfind(" ")
        t = (clipped[:cut] if cut > 0 else t[:_HOOK_TEXT_MAX_CHARS]).rstrip(" ,;:—-")
    if uppercase:
        t = t.upper()
    return t


def _first_sentence_end_frame(words: list[dict], first_kept: int) -> int | None:
    """Source frame where the first kept sentence ends (word whose text ends in ./!/?,
    abbreviation-guarded), scanning ≤ _HOOK_SENTENCE_SCAN_MS past the first kept word.
    AssemblyAI keeps punctuation (format_text default on); fixtures without punctuation
    return None and the caller uses the fallback hold."""
    started = False
    t0: int | None = None
    for w in words:
        wf = ms_to_frame(w.get("start_ms", 0))
        if not started:
            if wf < first_kept:
                continue
            started = True
            t0 = int(w.get("start_ms", 0))
        if t0 is not None and int(w.get("start_ms", 0)) - t0 > _HOOK_SENTENCE_SCAN_MS:
            return None
        token = str(w.get("word") or "").strip()
        if token and token[-1] in ".!?" and token.lower() not in _HOOK_ABBREVIATIONS:
            end_ms = w.get("end_ms") or w.get("start_ms", 0)
            return ms_to_frame(end_ms)
    return None


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
                       hints: dict | None = None, script: dict | None = None,
                       theme=None) -> dict:
    """WS4a → Title v2: synthesize the hook-title text_sticker over the video's open.
    Duration = end of the first spoken sentence, computed in OUTPUT frames and clamped
    [2s, 5s] (fallback 3s) — the sticker window is stored in source coords but sized so
    its OUTPUT hold hits the target after drops/speed remapping. Copy is normalized by
    _normalize_hook_text (sentence case as-authored, CAPS iff captions are uppercase).
    Theme styling: sticker_font/sticker_bg come from theme.hook (previously dead config —
    also fixes the live mixed-fonts lint error where themed captions got anton/baloo but
    the sticker hardcoded inter). Skipped for duet_split, when there's no candidate hook
    text, or when another overlay already occupies the first 60 source-adjacent frames."""
    if style in _HOOK_SKIP_STYLES:
        return edl
    hints = hints or {}
    hook_text = (hints.get("hook_text") or "").strip()
    if not hook_text:
        _hk = (script or {}).get("hook")   # string OR {text, ...} dict
        script_hook = str((_hk.get("text") if isinstance(_hk, dict) else _hk) or "").strip()
        if script_hook:
            hook_text = " ".join(script_hook.split()[:8])
    if not hook_text:
        return edl

    edl = copy.deepcopy(edl)
    segments = edl.get("segments") or []
    if not segments:
        return edl
    drops = edl.get("drops") or []
    kept = _kept_intervals(segments, drops)
    if not kept:
        return edl
    first_kept = kept[0][0]

    overlays = edl.get("overlays") or []
    if any(o["src_in"] < first_kept + 60 for o in overlays):
        return edl

    caption_opts = edl.get("caption_options") or {}
    hook_text = _normalize_hook_text(hook_text, uppercase=bool(caption_opts.get("uppercase")))
    if not hook_text:
        return edl

    # Output-frame hold: end of the first kept sentence, clamped [60,150]f; fallback 90f.
    index, total_out = _build_output_index(segments, drops, _play_order(edl))
    start_out = _src_to_out(index, first_kept) or 0
    sent_end_src = _first_sentence_end_frame(words, first_kept)
    hold_out = _HOOK_HOLD_FALLBACK_OUT
    if sent_end_src is not None:
        sent_end_out = _src_to_out(index, sent_end_src)
        if sent_end_out is not None and sent_end_out > start_out:
            hold_out = sent_end_out - start_out
    hold_out = max(_HOOK_HOLD_MIN_OUT, min(_HOOK_HOLD_MAX_OUT, hold_out))
    # Map the output end back to a source frame (clamped to kept footage) so the sticker
    # window survives build_render_plan's source→output remap at the right size.
    end_src = _out_to_src(index, min(start_out + hold_out, max(0, total_out - 1)))
    if end_src is None or end_src <= first_kept:
        end_src = first_kept + hold_out
    end_src = min(end_src, kept[-1][1])

    # Face-aware-ish placement: eyes sit in the upper third (~y 0.33), so the old 0.30
    # center put the box on the face — 0.24 clears both the eye line and every platform's
    # top dead zone (Reels top 14% is the binding one). Captions-on-top variant unchanged.
    pos_y = 0.62 if caption_opts.get("position") == "top" else 0.24
    hook_cfg = (theme.hook if theme is not None else {}) or {}
    # Build 54/55: when the creator EXPLICITLY picked a caption treatment on the record
    # screen, the hook TITLE adopts the same font — one typographic voice across captions
    # and title blocks. Keyed on the explicit hint (main.py threads prefs.caption_font),
    # NOT on caption_options.font — apply_theme fills that field from the theme first, so
    # reading it here would let a theme's caption font silently override its own
    # sticker_font whenever the two differ.
    _cap_font = str((hints or {}).get("caption_font") or "").strip()
    edl["overlays"] = overlays + [{
        "type": "text_sticker", "src_in": first_kept, "src_out": end_src,
        "text": hook_text, "scale": 1.05, "pos_x": 0.5, "pos_y": pos_y,
        "rotation": 0.0, "color": None,
        "bg": hook_cfg.get("sticker_bg") or "box",
        "font": _cap_font or hook_cfg.get("sticker_font") or "inter",
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

    # (1) frame-1 motion — skip only if another PUNCH already occupies the very open
    # (never stack two zooms). C3 fix: the hook TITLE sticker sits at exactly first_kept
    # (structure runs before hook_pack), and the old any-overlay guard meant the open
    # punch never fired in the common prod path — but stacked title + frame-1 zoom
    # coexisting IS the mandated stacked-hook pattern.
    if not any(o.get("type") == "punch_in"
               and o["src_in"] <= first_kept < o.get("src_out", o["src_in"]) for o in overlays):
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
                            seg["tx_scale"] = max(seg.get("tx_scale", 1.0), 1.08)   # build 54 tone-down (was 1.12)
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
    hints = hints or {}
    end_card_hint = hints.get("end_card") or {}
    # Build 55 audit: the skip matrix (fast_cuts loops, duet's payoff punch) applies to
    # PLAN-authored cards; a creator's explicit outro from the record screen wins.
    if style in _END_CARD_SKIP_STYLES and not end_card_hint.get("creator"):
        return edl
    text = (end_card_hint.get("text") or "").strip()
    if not end_card_hint.get("wanted") or not text:
        return edl
    edl = copy.deepcopy(edl)
    ec = {"text": text, "frames": 75, "show_handle": True}
    # Build 54 (outro builder): the creator's @handle + uploaded logo ride the hint.
    handle = str(end_card_hint.get("handle") or "").strip()
    if handle:
        ec["handle"] = handle[:40]
    logo = str(end_card_hint.get("logo_url") or "").strip()
    if logo.startswith("http"):
        ec["logo_url"] = logo
        ec["frames"] = 90        # a logo needs a beat more read time (still within the 30-150 clamp)
    edl["end_card"] = ec
    return edl


# ---------------------------------------------------------------------------
# WS3 — pattern-interrupt scheduler. Reuses the existing `punch_in` Overlay —
# no schema change. Guarantees a visual event at least every N OUTPUT frames
# (style/density-dependent) by inserting a punch_in (or, for faceless — no face
# to zoom — a text_sticker keyword pop) into any gap that exceeds it. Runs LAST
# in apply_retention_passes so it sees every event pacing/hook/sfx already
# placed and never double-covers them.
# ---------------------------------------------------------------------------

# Build 54 tone-down: fewer synthesized zooms (cadence 120→150 core styles) — the owner
# read the old density as "doesn't look good"; b-roll + real cuts carry the interrupts.
_INTERRUPT_CADENCE = {
    "talking_head": 150, "green_screen": 150, "split_three": 150,
    "broll_cutaway": 180, "faceless": 110,
    # fast_cuts / duet_split: native cadence already high enough — skip entirely.
}
_DENSITY_MULT = {"calm": 1.5, "standard": 1.0, "dense": 0.75}
_INTERRUPT_CADENCE_FLOOR = 60
_INTERRUPT_HOOK_GUARD = 45     # never insert in the first ~1.5s of OUTPUT
_INTERRUPT_CTA_GUARD = 60      # never insert in the last ~2s of OUTPUT
_INTERRUPT_MIN_SPACING = 60    # from any existing overlay/broll edge
_INTERRUPT_MAX_PER_CLIP = 8    # build 54 tone-down (was 12)
_INTERRUPT_HOLD_FRAMES = 75    # max width of a synthesized punch/pop window
_INTERRUPT_SCALES = (1.04, 1.07)   # alternates; build 54 tone-down (was 1.06/1.10)

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
            # B3: keyword-pop stickers take the theme's hook font/bg too — hardcoded
            # "inter" fired edit_lint's mixed-fonts ERROR under any non-inter theme.
            _hcfg = (theme.hook if theme is not None else {}) or {}
            new_overlays.append({"type": "text_sticker", "src_in": src_lo, "src_out": src_hi,
                                 "scale": 1.0, "text": word_text[:24],
                                 "pos_x": 0.5, "pos_y": 0.3, "rotation": 0.0,
                                 "color": None, "bg": _hcfg.get("sticker_bg") or "box",
                                 "font": _hcfg.get("sticker_font") or "inter"})
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

# v2 (D1): 5 → 3. "Two or three well-placed sounds beat ten poorly-timed ones" is the
# 2026 consensus; whoosh/pop oversaturation now reads as "marketing-guru content" and
# triggers production blindness (Hormozi's own team stripped most SFX).
_SFX_BUDGET_PER_30S = 3
_SFX_MIN_SPACING_FRAMES = 15   # ~0.5s @ 30fps
_SFX_END_GUARD_FRAMES = 15     # none in the last 15 source frames


def synthesize_sfx(edl: dict, words: list[dict], *,
                   sfx_assets: dict[str, str | None] | None = None, theme=None,
                   emphasis_spans: list | None = None) -> dict:
    """WS4d → v2: deterministically place SFX one-shots, budget-capped at ~3 per 30s of
    KEPT source, >=15f apart (bidirectional), none in the last 15f. Only emits a cue for
    a `kind` with a resolved URL in `sfx_assets` (build_render_plan double-guards).

    v2 additions:
    - "hit" (previously a dead catalog asset) is RESERVED first for the start of the
      single strongest emphasis span (longest — length is the only honest ranking signal
      left after _extract_emphasis_regions merges; tie → earliest). Reservation, not
      candidate competition: with budget 3 a mid-take hit would otherwise be crowded out
      by earlier pops.
    - the hook title sticker gets an entrance "pop" when the theme's hook.impact_sfx is
      set (previously dead theme config) — the sticker sits at kept[0][0] exactly.

    Every punch_in overlay is tagged "pop" (no provenance field); transitions "whoosh"."""
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
        elif (o.get("type") == "text_sticker" and o.get("src_in") == kept[0][0]
              and theme is not None and (theme.hook or {}).get("impact_sfx")):
            # D5: hook-title entrance pop (earliest frame → placed first below).
            candidates.add((o["src_in"], "pop"))

    # A7: a theme's gain_db (dB) overrides the module SFX_GAIN_DEFAULT (already
    # -14dB) when the bundle wants its one-shots hotter/quieter than the default.
    theme_gain_db = (theme.sfx.get("gain_db") if theme is not None else None)
    gain = (10 ** (theme_gain_db / 20)) if theme_gain_db is not None else SFX_GAIN_DEFAULT

    sfx: list[dict] = []
    placed_frames: list[int] = []

    def _try_place(f: int, kind: str) -> None:
        if len(sfx) >= budget or not _inside(f) or f >= last_frame - _SFX_END_GUARD_FRAMES:
            return
        if any(abs(f - p) < _SFX_MIN_SPACING_FRAMES for p in placed_frames):
            return
        url = sfx_assets.get(kind)
        if not url:
            return
        sfx.append({"src_in": f, "kind": kind, "gain": gain, "url": url})
        placed_frames.append(f)

    # D2: reserve the reveal hit at the top emphasis span BEFORE the main loop.
    spans = [s for s in (emphasis_spans or []) if isinstance(s, (list, tuple)) and len(s) == 2]
    if spans and sfx_assets.get("hit"):
        top = max(spans, key=lambda s: (s[1] - s[0], -s[0]))
        _try_place(int(top[0]), "hit")

    for f, kind in sorted(candidates):
        _try_place(f, kind)

    if sfx:
        sfx.sort(key=lambda c: c["src_in"])
        audio = dict(edl.get("audio") or {})
        audio["sfx"] = (audio.get("sfx") or []) + sfx
        edl["audio"] = audio
    return edl


# ---------------------------------------------------------------------------
# v2 (D3) — punchline music dropout ("silence as a tool"): cut the bed to zero
# under the single biggest claim so it lands over clean voice. OUTPUT-frame
# coords (the one deviation from the source-coord norm, like end_card) — this
# pass MUST run after every timeline-mutating pass (drops, speeds, tail trim);
# the orchestrator slots it last, just before _clamp_combined_scale. A tweak
# re-edit that changes timing does NOT recompute dropouts (same staleness class
# as speech_frames — acceptable, documented).
# ---------------------------------------------------------------------------

_MUSIC_DROPOUT_MIN_SPAN_F = 15   # spans shorter than this aren't a "biggest claim"
_MUSIC_DROPOUT_PRE_F = 6         # bed cuts just before the line…
_MUSIC_DROPOUT_POST_F = 9        # …and breathes back in just after


def plan_music_dropout(edl: dict, words: list[dict], *, style: str,
                       emphasis_spans: list | None = None) -> dict:
    if style == "fast_cuts":                       # music-forward montage — never dropout
        return edl
    music = ((edl.get("audio") or {}).get("music")) or {}
    if not music.get("url") or music.get("dropouts"):
        return edl
    spans = [s for s in (emphasis_spans or []) if isinstance(s, (list, tuple)) and len(s) == 2
             and (s[1] - s[0]) >= _MUSIC_DROPOUT_MIN_SPAN_F]
    if not spans:
        return edl
    segments = edl.get("segments") or []
    if not segments:
        return edl
    edl = copy.deepcopy(edl)
    index, total_out = _build_output_index(segments, edl.get("drops") or [], _play_order(edl))
    top = max(spans, key=lambda s: (s[1] - s[0], -s[0]))
    a_out = _src_to_out(index, int(top[0]))
    b_out = _src_to_out(index, int(top[1]))
    if a_out is None or b_out is None or b_out <= a_out:
        return edl
    win_in = a_out - _MUSIC_DROPOUT_PRE_F
    win_out = b_out + _MUSIC_DROPOUT_POST_F
    # Whole window must clear the hook (also clears MUSIC_LEAD+FADE ramp-in) and the CTA.
    if win_in < HOOK_PROTECT_OUT_FRAMES or win_out > total_out - CTA_PROTECT_OUT_FRAMES:
        return edl
    audio = dict(edl.get("audio") or {})
    music = dict(audio.get("music") or {})
    music["dropouts"] = [{"frame_in": int(win_in), "frame_out": int(win_out)}]
    audio["music"] = music
    edl["audio"] = audio
    return edl


# ---------------------------------------------------------------------------
# v2 (A5) — b-roll SFX coupling, RESTRAINED: memes get one entrance "pop"
# (a silent meme pop-in reads AI-assembled), everything else stays silent
# (over-coupling is the "marketing-guru" tell). Runs POST-_resolve_broll from
# main.py (NOT inside apply_retention_passes): synthesize_sfx runs pre-resolve,
# and an unresolved-then-dropped insert would orphan its cue. The bidirectional
# 15f proximity check against ALL existing cues doubles as idempotency for
# tweak re-renders.
# ---------------------------------------------------------------------------

def couple_broll_sfx(edl: dict, *, sfx_assets: dict[str, str | None] | None = None,
                     video_type: str = "", energy: str = "", theme=None) -> dict:
    from app.edl import _ENTERTAINMENT_VIDEO_TYPES as _ENT
    broll = edl.get("broll") or []
    # v7 P1: HERO full-frame takeovers get a whoosh entry regardless of genre —
    # a hero cut with no sound reads unfinished (foley-under-inserts research).
    # Meme pops stay entertainment/high-energy-only (restraint doctrine).
    sfx_assets = sfx_assets or {}
    heroes = sorted((b for b in broll if b.get("hero") and b.get("resolved_url")),
                    key=lambda b: int(b.get("src_in", 0))) if sfx_assets.get("whoosh") else []
    if not (video_type in _ENT or energy == "high"):
        memes: list[dict] = []
    else:
        pop_url = sfx_assets.get("pop")
        memes = sorted((b for b in broll
                        if pop_url and b.get("resolved_url")
                        and (b.get("need") == "meme" or b.get("source") in ("giphy", "klipy"))),
                       key=lambda b: int(b.get("src_in", 0)))
    if not memes and not heroes:
        return edl
    edl = copy.deepcopy(edl)
    audio = dict(edl.get("audio") or {})
    existing = list(audio.get("sfx") or [])
    placed = [int(c.get("src_in", 0)) for c in existing]
    segments = edl.get("segments") or []
    kept = _kept_intervals(segments, edl.get("drops") or []) if segments else []
    total_kept = sum(hi - lo for lo, hi in kept) if kept else 0
    budget = max(0, max(1, round(_SFX_BUDGET_PER_30S * total_kept / (30 * 30))) - len(existing)) \
        if total_kept else 0
    theme_gain_db = (theme.sfx.get("gain_db") if theme is not None else None)
    gain = (10 ** (theme_gain_db / 20)) if theme_gain_db is not None else SFX_GAIN_DEFAULT
    added = []
    # Heroes first (they're 1-2 per video and the priority accents), then meme pops.
    for b in heroes:
        if len(added) >= budget:
            break
        f = int(b.get("src_in", 0))
        if any(abs(f - p) < _SFX_MIN_SPACING_FRAMES for p in placed):
            continue
        added.append({"src_in": f, "kind": "whoosh", "gain": gain,
                      "url": sfx_assets["whoosh"]})
        placed.append(f)
    for b in memes:
        if len(added) >= budget:
            break
        f = int(b.get("src_in", 0))
        if any(abs(f - p) < _SFX_MIN_SPACING_FRAMES for p in placed):
            continue
        added.append({"src_in": f, "kind": "pop", "gain": gain, "url": pop_url})
        placed.append(f)
    if added:
        audio["sfx"] = sorted(existing + added, key=lambda c: c["src_in"])
        edl["audio"] = audio
    return edl


def apply_retention_passes(edl: dict, words: list[dict], *, style: str,
                           prefs: dict | None = None, emphasis_spans: list | None = None,
                           dossier: dict | None = None, hints: dict | None = None,
                           script: dict | None = None, level: str = "default",
                           sfx_assets: dict[str, str | None] | None = None,
                           job_seed: str = "", theme=None, genre_density: str = "",
                           beat_grid: list | None = None,
                           beat_conf: float | None = None) -> dict:
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
        # Script-aware: feed the intended script text (empty for freestyle) so a reworded
        # redo of a scripted line is caught even when transcript-only similarity misses it.
        # hook may be a plain string or a {text,...} dict — extract text either way.
        _s = script or {}
        _hk = _s.get("hook")
        _hook_txt = _hk.get("text") if isinstance(_hk, dict) else _hk
        _script_text = " ".join(str(x or "") for x in (_hook_txt, _s.get("body"))).strip()
        edl = _safe_pass("dedupe_retakes", edl, dedupe_retakes, words, _script_text)

    # v2 cold open — opt-in token "cold_open" (bakes independently, like framing/
    # hook_pack). Slotted after retake (a dropped flubbed opener changes which word is
    # first) and before pacing/framing so their hook-protect zones cover the REAL
    # content open instead of up to a second of dead lead.
    if "cold_open" in enabled:
        edl = _safe_pass("trim_cold_open", edl, trim_cold_open, words)

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
                         # Build 55 audit: a creator's EXPLICIT outro (record screen) beats
                         # the style skip matrix; plan-authored cards still respect it.
                         and (style not in _END_CARD_SKIP_STYLES
                              or bool(end_card_hint.get("creator"))))
        if wants_end_card:
            edl = _safe_pass("place_end_card", edl, place_end_card, words, style=style, hints=hints)
        else:
            edl = _safe_pass("trim_loop_tail", edl, trim_loop_tail, words)
        edl = _safe_pass("place_hook_overlay", edl, place_hook_overlay, words,
                         style=style, hints=hints, script=script, theme=theme)

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
    # (including interrupts) placed. v2: also receives emphasis_spans so the reveal
    # "hit" reservation can anchor on the strongest span.
    if "sfx" in enabled:
        edl = _safe_pass("synthesize_sfx", edl, synthesize_sfx, words,
                         sfx_assets=sfx_assets, theme=theme,
                         emphasis_spans=emphasis_spans)

    # v2 dropout — opt-in token "dropout". MUST run after every timeline-mutating pass
    # (drops/speeds/tail-trim all precede it here); its windows are OUTPUT-frame coords
    # read verbatim by AudioMix, so anything that changes output timing after this point
    # would silently desync them.
    if "dropout" in enabled:
        edl = _safe_pass("plan_music_dropout", edl, plan_music_dropout, words,
                         style=style, emphasis_spans=emphasis_spans)

    # WS3 (build 49) beat_snap — opt-in token "beat_snap". Same ordering contract as
    # dropout (after every timeline-mutating pass: it maps source anchors through the
    # final output index). Inert without a catalog beat grid (main threads it from the
    # selected track's offline-computed metadata) — so enabling the token before the
    # music catalog carries grids is a safe no-op.
    if "beat_snap" in enabled:
        edl = _safe_pass("beat_snap", edl, beat_snap, words,
                         beat_grid=beat_grid, beat_conf=beat_conf)

    # FINAL: cap combined framing×punch scale to the 120% ceiling (spec §6.1 / HC7).
    # A punch overlay multiplies on top of the playing segment's tx_scale in the renderer,
    # so a close-framed (1.18) segment under a 1.12 punch would hit ~1.32. Runs after every
    # pass so it catches punches added by interrupts/emphasis/hook_pack too. Deterministic,
    # never raises — a plain clamp, not a _safe_pass (it can only lower a scale).
    edl = _clamp_combined_scale(edl)
    return edl


# ---------------------------------------------------------------------------
# WS4 (build 49) — face-aware framing (AutoFlip's static-crop rule). The generic
# framing pass punches in about the frame CENTER with a fixed -0.02 nudge; with a
# real YuNet face box we can place the EYE LINE at ~1/3 of the frame instead —
# the single strongest "a human framed this" signal. STATIC by design: one
# vertical offset per segment scale, no per-frame tracking (AutoFlip's own
# finding: static beats tracking for a single speaker; tracking reads as AI).
# Gated by main's FRAMING_FACE_AWARE env (default OFF until the owner eyeballs a
# render) — this function is pure and deterministic.
# ---------------------------------------------------------------------------

_FACE_TARGET_EYE_Y = 0.33     # rule of thirds: eyes at the top third line
_FACE_MAX_SHIFT = 0.08        # never pan more than 8% of frame height
_FACE_MIN_SHIFT = 0.005       # sub-noise adjustments round to zero


def face_aware_reframe(edl: dict, face_box: dict | None) -> dict:
    """Set each PUNCHED-IN segment's tx_y so the speaker's eye line sits at the top-third
    line under that segment's own scale. Wide (scale≈1) segments are left alone — the
    full frame is the framing. No-op without a face box."""
    if not face_box:
        return edl
    try:
        eye_y = float(face_box["y"]) + 0.35 * float(face_box["h"])   # eyes ≈ 35% into the box
    except (KeyError, TypeError, ValueError):
        return edl
    if not (0.0 < eye_y < 1.0):
        return edl
    segments = edl.get("segments") or []
    for seg in segments:
        s = float(seg.get("tx_scale") or 1.0)
        if s <= 1.02:
            continue                       # wide shot: never pan the base frame
        # Point eye_y maps to 0.5 + (eye_y - 0.5)·s after a center-scale; tx_y closes
        # the gap to the target line.
        shift = _FACE_TARGET_EYE_Y - (0.5 + (eye_y - 0.5) * s)
        shift = max(-_FACE_MAX_SHIFT, min(_FACE_MAX_SHIFT, shift))
        if abs(shift) < _FACE_MIN_SHIFT:
            shift = 0.0
        seg["tx_y"] = round(shift, 4)
    edl["segments"] = segments
    return edl


# ---------------------------------------------------------------------------
# WS3 (build 49) — beat_snap: align visual events to the music's beat grid.
# Music that visibly ignores the cut rhythm is a strong "AI-assembled" tell;
# editors snap INSERT/pop events to beats but deliberately NOT dialogue cuts.
# The grid comes from the offline catalog pipeline (scripts/build_music_catalog.py:
# librosa beat_track per track → beat times in seconds + a confidence proxy);
# low-confidence grids (lo-fi/ambient where trackers guess) disable snapping
# entirely — a wrong grid is worse than none. madmom research (Böck ISMIR'16):
# F1 0.86-0.94 on produced western music, 0.52 on expressive material — hence
# the gate.
#
# Mechanics: the music track starts at OUTPUT frame 0 (AudioMix plays it from
# composition start; the MUSIC_LEAD gate only shapes volume), so beat time t
# lands at output frame round(t*fps). Events are stored in SOURCE coords →
# convert via the same _build_output_index/_src_to_out machinery the interrupt
# scheduler uses, shift by the small output delta (divided by the segment's
# speed back to source frames), and verify the shifted position still maps into
# kept footage. Events land ONE frame BEFORE the beat (the craft rule: the cut
# reads as "on" the beat when the new image is already up as it hits). Runs in
# the dropout slot (after every timeline-mutating pass) — same ordering
# contract as plan_music_dropout.
# ---------------------------------------------------------------------------

_BEAT_SNAP_MIN_CONF = 0.5      # below this the grid is a guess — never snap
_BEAT_SNAP_TOLERANCE_OUT = 4   # only move events already within ~130ms of a beat
_BEAT_SNAP_LAND_BEFORE = 1     # land this many frames BEFORE the beat
_BEAT_SNAP_HOOK_GUARD_OUT = 30 # never touch the first second (hook open is sacred)


def beat_snap(edl: dict, words: list[dict], *, beat_grid: list | None = None,
              beat_conf: float | None = None, fps: int = 30) -> dict:
    """Snap b-roll IN points, interrupt text-sticker pops, and punch-in overlays to the
    nearest beat within ±_BEAT_SNAP_TOLERANCE_OUT output frames. Never moves segments
    (dialogue cuts), the hook open, or anything when the grid is absent/low-confidence.
    Holds are preserved (src_out shifts with src_in)."""
    if not beat_grid or (beat_conf is not None and beat_conf < _BEAT_SNAP_MIN_CONF):
        return edl
    music = (edl.get("audio") or {}).get("music") or {}
    if not music.get("url"):
        return edl
    segments = edl.get("segments") or []
    if not segments:
        return edl
    edl = copy.deepcopy(edl)
    index, total_out = _build_output_index(edl.get("segments") or [],
                                           edl.get("drops") or [], _play_order(edl))
    beat_frames = sorted({round(float(t) * fps) for t in beat_grid
                          if isinstance(t, (int, float)) and t >= 0})
    beat_frames = [b for b in beat_frames if b < total_out]
    if not beat_frames:
        return edl

    def _snap_delta_out(out_pos: int) -> int | None:
        """Output-frame delta to land 1f before the nearest beat, or None if no beat
        is within tolerance / the event is inside the hook guard."""
        if out_pos < _BEAT_SNAP_HOOK_GUARD_OUT:
            return None
        nearest = min(beat_frames, key=lambda b: abs(b - out_pos))
        if abs(nearest - out_pos) > _BEAT_SNAP_TOLERANCE_OUT:
            return None
        target = max(0, nearest - _BEAT_SNAP_LAND_BEFORE)
        return target - out_pos

    def _seg_speed_at(src: int) -> float:
        for lo, hi, _, speed in index:
            if lo <= src < hi:
                return speed
        return 1.0

    def _shift(item: dict) -> bool:
        src_in = item.get("src_in")
        if not isinstance(src_in, int):
            return False
        out_pos = _src_to_out(index, src_in)
        if out_pos is None:
            return False
        delta_out = _snap_delta_out(out_pos)
        if delta_out is None or delta_out == 0:
            return False
        delta_src = round(delta_out * _seg_speed_at(src_in))
        if delta_src == 0:
            return False
        new_in = src_in + delta_src
        # The shifted anchor must still map into kept footage near the target —
        # crossing a drop/segment boundary would teleport the event; skip instead.
        new_out = _src_to_out(index, new_in)
        if new_out is None or abs(new_out - (out_pos + delta_out)) > 1:
            return False
        item["src_in"] = new_in
        if isinstance(item.get("src_out"), int):
            item["src_out"] = item["src_out"] + delta_src   # preserve the hold
        return True

    snapped = 0
    for b in edl.get("broll") or []:
        if _shift(b):
            snapped += 1
    for o in edl.get("overlays") or []:
        # Interrupt stickers + punch-ins snap; the hook sticker (inside the hook guard)
        # and every other overlay type are left alone.
        if o.get("type") in ("text_sticker", "punch_in") and _shift(o):
            snapped += 1
    if snapped:
        edl.setdefault("_beat_snap", {})["snapped"] = snapped
    return edl


_COMBINED_SCALE_CAP = 1.20     # 1080-source ceiling; punch × framing must not exceed this


def _clamp_combined_scale(edl: dict, cap: float = _COMBINED_SCALE_CAP) -> dict:
    """For every punch_in overlay, lower any segment it overlaps (in source frames) so that
    segment.tx_scale × punch.scale <= cap. v2 (E3): ALSO cap BARE framing scales — theme
    close-frames (hormozi 1.4 / energetic 1.3) previously shipped >1.2 whenever no punch
    overlapped them, breaching the 120% spec ceiling. Only ever reduces (floor 1.0)."""
    try:
        segments = edl.get("segments") or []
        if not segments:
            return edl
        punches = [o for o in (edl.get("overlays") or []) if o.get("type") == "punch_in"]
        for o in punches:
            ps = float(o.get("scale") or 1.0)
            if ps <= 1.0:
                continue
            oa, ob = o.get("src_in", 0), o.get("src_out", o.get("src_in", 0))
            for seg in segments:
                if min(seg.get("src_out", 0), ob) <= max(seg.get("src_in", 0), oa):
                    continue                       # no source overlap
                cur = float(seg.get("tx_scale") or 1.0)
                if cur * ps > cap:
                    seg["tx_scale"] = max(1.0, round(cap / ps, 4))
        # E3: bare-framing sweep — catches theme close scales, _insert_framing_pop bumps,
        # and hook_pack's 1.12 first-cut bump when nothing punches over them.
        for seg in segments:
            if float(seg.get("tx_scale") or 1.0) > cap:
                seg["tx_scale"] = cap
        edl["segments"] = segments
    except Exception:
        pass
    return edl
