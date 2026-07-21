"""A1: deterministic pre-render "amateur tell" lint. Runs AFTER apply_retention_passes,
before render, reasoning entirely in OUTPUT frames (what the viewer actually sees) so a
speed-ramped or heavily-cut take is graded on real screen time, not source duration.

Pure + read-only: lint_edl NEVER mutates the EDL. Findings carry an optional `fix_op` —
a valid TWEAK_OP_TYPES op (app/edl.py) the CALLER may apply via apply_edl_ops; this
module never applies anything itself. Import surface is intentionally narrow: only
app.edl (public) and a few module-private helpers from app.retention that already do
this exact source<->output bookkeeping — no import of main.py (would be circular).
"""
from __future__ import annotations

import statistics
from typing import TypedDict

from app.edl import ms_to_frame
from app.retention import (
    _play_order, _segment_kept_ranges, _src_to_out, _out_to_src, _build_output_index,
)


class LintFinding(TypedDict):
    code: str
    severity: str              # "error" | "warn"
    at_out_frame: int | None
    detail: str
    fix_op: dict | None


# --- thresholds (the only place these live) -----------------------------------
STATIC_WINDOW_FRAMES = 150        # ~5s @ 30fps with no visual event
STATIC_OPEN_FRAMES = 45           # ~1.5s — the video must open with SOME motion/overlay
FRAMING_DELTA_FLOOR = 0.08        # <8% tx_scale change reads as a glitch; the spec's 100/110/118 ladder (8-10% steps) is legit
METRONOME_GAP_FLOOR = 8           # stddev of event gaps below this reads as machine-timed
METRONOME_MIN_EVENTS = 4
ANCHOR_DRIFT_FRAMES = 3           # overlay/sfx more than this many frames from any word start
LONG_DISSOLVE_FRAMES = 15
EMPHASIS_PUNCH_SCALE = 1.1
EMPHASIS_PUNCH_HOLD_FRAMES = 45
_TAIL_SKIP_STYLES = {"fast_cuts", "duet_split"}


def _segment_events(edl: dict) -> tuple[list[dict], int]:
    """[(seg_idx, src_in, src_out, out_start, out_end, tx_scale, tx_x, tx_y), ...] in
    PLAY order, plus total output frames. Mirrors app.retention._build_output_index but
    also carries the originating segment index + its canvas transform, which the
    framing-coherence checks need and the shared helper doesn't expose."""
    segments = edl.get("segments") or []
    drops = edl.get("drops") or []
    play_order = _play_order(edl)
    events: list[dict] = []
    out_cursor = 0
    for seg_idx in play_order:
        if seg_idx >= len(segments):
            continue
        seg = segments[seg_idx]
        speed = float(seg.get("speed") or 1.0)
        for lo, hi in _segment_kept_ranges(seg, drops):
            out_len = max(1, round((hi - lo) / speed))
            events.append({
                "seg_idx": seg_idx, "src_in": lo, "src_out": hi,
                "out_start": out_cursor, "out_end": out_cursor + out_len,
                "tx_scale": float(seg.get("tx_scale") or 1.0),
                "tx_x": float(seg.get("tx_x") or 0.0),
                "tx_y": float(seg.get("tx_y") or 0.0),
            })
            out_cursor += out_len
    return events, out_cursor


def _overlay_out_span(overlay: dict, index) -> tuple[int, int] | None:
    a = _src_to_out(index, overlay["src_in"])
    if a is None:
        return None
    b = _src_to_out(index, max(overlay["src_in"], overlay.get("src_out", overlay["src_in"] + 1) - 1))
    return (a, (b if b is not None else a) + 1)


def _event_points(edl: dict, seg_events: list[dict], index) -> list[int]:
    """Every OUTPUT frame where something visually changes: a cut boundary (a new kept
    sub-range begins, skipping the very first — that's the video's own start, not a
    cut), every overlay/broll window's start, and every transition's midpoint."""
    points: list[int] = [e["out_start"] for e in seg_events[1:]]
    for o in (edl.get("overlays") or []) + (edl.get("broll") or []):
        span = _overlay_out_span(o, index)
        if span:
            points.append(span[0])
    segments = edl.get("segments") or []
    for t in (edl.get("transitions") or []):
        seg_idx = t.get("after_segment")
        if seg_idx is None or seg_idx >= len(segments):
            continue
        src_frame = segments[seg_idx]["src_out"] - 1
        out_f = _src_to_out(index, src_frame)
        if out_f is not None:
            points.append(out_f)
    return sorted(set(points))


def _check_static_windows(total_out: int, points: list[int], index) -> list[LintFinding]:
    """`index` is the same (src_in, src_out, out_start, speed) table build_output_index
    returns — used to convert the fix_op's punch window back to SOURCE coords, since
    add_punch_in's start_frame/end_frame are source-frame (Overlay.src_in/src_out)."""
    findings: list[LintFinding] = []
    bounds = [0] + points + [total_out]
    for a, b in zip(bounds, bounds[1:]):
        if b - a > STATIC_WINDOW_FRAMES:
            mid = (a + b) // 2
            src_mid = _out_to_src(index, min(mid, total_out - 1))
            fix_op = None
            if src_mid is not None:
                fix_op = {"type": "add_punch_in", "start_frame": src_mid, "end_frame": src_mid + 30,
                         "scale": 1.08}
            findings.append({"code": "static_window", "severity": "error", "at_out_frame": mid,
                             "detail": f"no visual event for {b - a} output frames (f{a}-f{b})",
                             "fix_op": fix_op})
    return findings


def _check_static_open(points: list[int]) -> list[LintFinding]:
    if not points or points[0] > STATIC_OPEN_FRAMES:
        first = points[0] if points else None
        return [{"code": "static_open", "severity": "error", "at_out_frame": 0,
                 "detail": f"no motion/overlay event in the opening {STATIC_OPEN_FRAMES} frames"
                           + (f" (first event at f{first})" if first else " (no events at all)"),
                 "fix_op": None}]
    return []


def _check_same_framing_adjacent(seg_events: list[dict]) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for prev, cur in zip(seg_events, seg_events[1:]):
        if prev["seg_idx"] == cur["seg_idx"]:
            continue   # same segment split by a drop, not a cut to a new shot
        scale = max(0.01, prev["tx_scale"])
        delta = abs(cur["tx_scale"] - prev["tx_scale"]) / scale
        if delta < FRAMING_DELTA_FLOOR:
            findings.append({"code": "same_framing_adjacent", "severity": "error",
                             "at_out_frame": cur["out_start"],
                             "detail": f"cut at f{cur['out_start']} has only {delta:.0%} framing "
                                       f"delta — reads as a glitch, not an intentional cut",
                             # v2 (E3): cap at the 1.20 spec ceiling — lint-fix runs AFTER
                             # _clamp_combined_scale, so an uncapped bump here could
                             # reintroduce a >120% frame under EDIT_LINT=fix.
                             "fix_op": {"type": "set_segment_transform", "index": cur["seg_idx"],
                                        "scale": round(min(1.20, prev["tx_scale"] * 1.18), 3)}})
    return findings


def _check_metronomic(points: list[int]) -> list[LintFinding]:
    if len(points) < METRONOME_MIN_EVENTS + 1:
        return []
    gaps = [b - a for a, b in zip(points, points[1:])]
    if len(gaps) < METRONOME_MIN_EVENTS:
        return []
    spread = statistics.pstdev(gaps)
    if spread < METRONOME_GAP_FLOOR:
        return [{"code": "metronomic_intervals", "severity": "warn", "at_out_frame": None,
                 "detail": f"event gaps are suspiciously regular (stddev={spread:.1f}f "
                           f"across {len(gaps)} gaps) — reads as machine-timed",
                 "fix_op": None}]
    return []


def _check_repeated_interrupt_type(edl: dict, index) -> list[LintFinding]:
    overlays = [o for o in (edl.get("overlays") or []) if o.get("src_in") is not None]
    dated = []
    for o in overlays:
        span = _overlay_out_span(o, index)
        if span:
            dated.append((span[0], o.get("type", "")))
    dated.sort()
    findings: list[LintFinding] = []
    run_type, run_len, run_start = None, 0, None
    for out_f, otype in dated:
        if otype == run_type:
            run_len += 1
        else:
            run_type, run_len, run_start = otype, 1, out_f
        if run_len == 3:
            findings.append({"code": "repeated_interrupt_type", "severity": "warn",
                             "at_out_frame": run_start,
                             "detail": f"3+ consecutive '{otype}' overlays starting at f{run_start} "
                                       f"— vary the interrupt type", "fix_op": None})
    return findings


def _check_anchor_drift(edl: dict, words: list[dict]) -> list[LintFinding]:
    word_starts = sorted(ms_to_frame(w.get("start_ms", 0)) for w in (words or []) if w.get("word"))
    if not word_starts:
        return []

    def _nearest_dist(frame: int) -> int:
        import bisect
        i = bisect.bisect_left(word_starts, frame)
        cands = [word_starts[j] for j in (i - 1, i) if 0 <= j < len(word_starts)]
        return min((abs(frame - c) for c in cands), default=10**9)

    findings: list[LintFinding] = []
    for o in (edl.get("overlays") or []):
        if o.get("type") == "text_card":
            continue   # text cards aren't word-anchored (a standalone slab, GreenScreen/duet)
        d = _nearest_dist(o.get("src_in", 0))
        if d > ANCHOR_DRIFT_FRAMES:
            findings.append({"code": "anchor_drift", "severity": "warn",
                             "at_out_frame": None,
                             "detail": f"{o.get('type','overlay')} at source f{o.get('src_in')} is "
                                       f"{d}f from the nearest word start", "fix_op": None})
    for s in ((edl.get("audio") or {}).get("sfx") or []):
        d = _nearest_dist(s.get("src_in", 0))
        if d > ANCHOR_DRIFT_FRAMES:
            findings.append({"code": "anchor_drift", "severity": "warn", "at_out_frame": None,
                             "detail": f"sfx '{s.get('kind')}' at source f{s.get('src_in')} is "
                                       f"{d}f from the nearest word start", "fix_op": None})
    return findings


def _check_long_dissolve(edl: dict) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for t in (edl.get("transitions") or []):
        frames = t.get("frames", 0)
        if frames > LONG_DISSOLVE_FRAMES:
            findings.append({"code": "long_dissolve", "severity": "error", "at_out_frame": None,
                             "detail": f"transition after segment {t.get('after_segment')} is "
                                       f"{frames}f (>{LONG_DISSOLVE_FRAMES}f cap)",
                             "fix_op": {"type": "set_transition", "after_segment": t.get("after_segment"),
                                        "style": t.get("style", "fade_black"), "frames": 12}})
    return findings


def _check_effect_off_emphasis(edl: dict, emphasis_spans) -> list[LintFinding]:
    if not emphasis_spans:
        return []   # nothing to compare against — never a false positive
    findings: list[LintFinding] = []
    for o in (edl.get("overlays") or []):
        if o.get("type") != "punch_in" or o.get("scale", 1.0) < EMPHASIS_PUNCH_SCALE:
            continue
        src_in, src_out = o.get("src_in", 0), o.get("src_out", 0)
        if src_out - src_in < EMPHASIS_PUNCH_HOLD_FRAMES:
            continue
        overlaps = any(src_in < e_out and src_out > e_in for e_in, e_out in emphasis_spans)
        if not overlaps:
            findings.append({"code": "effect_off_emphasis", "severity": "warn", "at_out_frame": None,
                             "detail": f"punch_in at source f{src_in}-{src_out} (scale={o.get('scale')}) "
                                       f"doesn't cover any emphasis span", "fix_op": None})
    return findings


def _check_tail_rules(edl: dict, style: str) -> list[LintFinding]:
    if edl.get("end_card") and style in _TAIL_SKIP_STYLES:
        # No dedicated "remove end_card" tweak-op exists — surface the finding for a
        # manual/self-review fix rather than a mechanical one.
        return [{"code": "tail_rules", "severity": "error", "at_out_frame": None,
                 "detail": f"end_card present on '{style}', which never places one",
                 "fix_op": None}]
    return []


def _check_ungraded(edl: dict, theme) -> list[LintFinding]:
    if theme is None:
        return []   # no active theme (pre-A7) — nothing to compare against
    wants_grade = bool(getattr(theme, "grade", None) or {})
    if wants_grade and not edl.get("look"):
        return [{"code": "ungraded", "severity": "warn", "at_out_frame": None,
                 "detail": "theme expects a grade but edl.look is unset", "fix_op": None}]
    return []


def _check_bundle_coherence(edl: dict) -> list[LintFinding]:
    fonts = set()
    co = edl.get("caption_options") or {}
    if co.get("font"):
        fonts.add(co["font"])
    colors = set()
    if co.get("accent"):
        colors.add(co["accent"])
    for o in (edl.get("overlays") or []):
        if o.get("type") == "text_sticker":
            if o.get("font"):
                fonts.add(o["font"])
            if o.get("color"):
                colors.add(o["color"])
    styles = {t.get("style") for t in (edl.get("transitions") or []) if t.get("style")}
    findings: list[LintFinding] = []
    if len(fonts) > 1:
        findings.append({"code": "bundle_coherence", "severity": "error", "at_out_frame": None,
                         "detail": f"mixed caption/sticker fonts in one EDL: {sorted(fonts)}",
                         "fix_op": None})
    if len(colors) > 1:
        findings.append({"code": "bundle_coherence", "severity": "error", "at_out_frame": None,
                         "detail": f"mixed accent colors in one EDL: {sorted(colors)}",
                         "fix_op": None})
    if len(styles) > 1:
        findings.append({"code": "bundle_coherence", "severity": "error", "at_out_frame": None,
                         "detail": f"mixed transition styles in one EDL: {sorted(styles)}",
                         "fix_op": None})
    return findings


def _check_caption_coverage(edl: dict, words: list[dict]) -> list[LintFinding]:
    """Captions are derived directly from AssemblyAI's speech-to-text of the real audio,
    so they're audio-verified by construction (spec HC6) — this check just guards the
    failure where the caption track came back EMPTY over genuine speech, which breaks
    comprehension-with-sound-off (spec priority 3 / §10 'a caption is visible at frame 1')."""
    caps = edl.get("captions") or []
    if words and not caps and edl.get("caption_style") not in (None, "", "off"):
        return [{"code": "captions_missing", "severity": "error", "at_out_frame": 0,
                 "detail": "no captions over spoken audio — sound-off viewers get nothing",
                 "fix_op": None}]
    return []


# --- Craft Engine checks (build 57) — thresholds live in knowledge/craft YAML ------

def _check_reading_rate(edl: dict) -> list[LintFinding]:
    """typ.reading_rate — BBC 160-180wpm (0.3s/word) + Netflix 20 chars/sec: every
    burned-in text element must be displayable in the time it holds. Captions are
    word-timed by construction and exempt; this covers text cards, text stickers,
    and the end card (which was a FIXED hold regardless of text length)."""
    from app import craft
    p = craft.rule_params("typ.reading_rate", {"sec_per_word": 0.3, "chars_per_sec": 20})
    spw, cps = float(p["sec_per_word"]), float(p["chars_per_sec"])
    out: list[LintFinding] = []

    def need_frames(text: str) -> int:
        words = max(1, len(text.split()))
        return int(max(words * spw, len(text) / cps) * 30)

    for o in (edl.get("overlays") or []):
        text = (o.get("text") or "").strip()
        if o.get("type") not in ("text_card", "text_sticker") or not text:
            continue
        hold = int(o.get("src_out", 0)) - int(o.get("src_in", 0))
        need = need_frames(text)
        if hold < need:
            out.append(LintFinding(
                code="reading_rate", severity="error",
                at_out_frame=None,
                detail=f"{o.get('type')} '{text[:40]}' holds {hold}f but needs {need}f "
                       f"to read (BBC 0.3s/word, Netflix 20cps)",
                fix_op={"type": "edit_overlay",
                        "frame_in": int(o.get("src_in", 0)),
                        "frame_out": int(o.get("src_in", 0)) + need}))
    ec = edl.get("end_card") or {}
    ec_text = (ec.get("text") or "").strip()
    if ec_text:
        # Full-screen title wants ~2x a single read (GoE #36 band), inside the 30-150 clamp.
        need = min(150, max(30, 2 * need_frames(ec_text)))
        if int(ec.get("frames") or 75) < need:
            out.append(LintFinding(
                code="reading_rate", severity="warn", at_out_frame=None,
                detail=f"end card '{ec_text[:40]}' holds {ec.get('frames')}f; "
                       f"~{need}f gives two clean reads (GoE #36)",
                fix_op=None))
    return out


def _check_breathe_after_peak(points: list[int], total_out: int) -> list[LintFinding]:
    """pace.breathe_after_peak — Pearlman's tension/release: an event-dense cluster
    must be followed by a genuine hold before the next build. Sustained maximum
    intensity is fatigue, not energy (the temporal form of GoE #23)."""
    from app import craft
    p = craft.rule_params("pace.breathe_after_peak",
                          {"cluster_events": 3, "cluster_window_f": 90,
                           "release_window_f": 240, "release_gap_mult": 1.5})
    k = int(p["cluster_events"]); win = int(p["cluster_window_f"])
    rel_win = int(p["release_window_f"]); mult = float(p["release_gap_mult"])
    pts = sorted(points)
    if len(pts) < k + 1:
        return []
    gaps = [b - a for a, b in zip(pts, pts[1:])]
    if not gaps:
        return []
    med = sorted(gaps)[len(gaps) // 2] or 1
    out: list[LintFinding] = []
    for i in range(len(pts) - k + 1):
        if pts[i + k - 1] - pts[i] <= win:                     # dense cluster
            end = pts[i + k - 1]
            later = [g for a, g in zip(pts[i + k - 1:], gaps[i + k - 1:])
                     if a < end + rel_win]
            if later and max(later) < med * mult and end + rel_win < total_out:
                out.append(LintFinding(
                    code="no_breath_after_peak", severity="warn",
                    at_out_frame=end,
                    detail=f"{k} events inside {win}f with no release hold >= "
                           f"{mult}x median gap within {rel_win}f (Pearlman "
                           f"tension/release)",
                    fix_op=None))
                break                                           # one finding is enough
    return out


def _check_ending_complete(edl: dict, words: list[dict]) -> list[LintFinding]:
    """story.ending_complete — the industry's dedicated ending judgment (Vizard's
    Clip Editor exists ONLY for this): the last kept sentence must complete.
    A final kept word without sentence-final punctuation and no end card reads
    as a mid-thought trail-off."""
    if edl.get("end_card"):
        return []
    kept = _kept_ranges(edl)
    if not kept or not words:
        return []
    last_keep = kept[-1][1]
    last_word = ""
    for w in words:
        wf = int(w.get("start_ms", 0) * 30 / 1000)
        if wf < last_keep and (w.get("word") or "").strip():
            last_word = w["word"].strip()
    if last_word and not last_word[-1] in ".?!":
        return [LintFinding(
            code="ending_incomplete", severity="warn", at_out_frame=None,
            detail=f"last kept word '{last_word}' does not end a sentence and no "
                   f"end card closes the video (ending-completeness doctrine)",
            fix_op=None)]
    return []


def _kept_ranges(edl: dict) -> list[tuple[int, int]]:
    """Kept (src_in, src_out) spans after drops, in source coords, sorted."""
    segs = sorted((int(s.get("src_in", 0)), int(s.get("src_out", 0)))
                  for s in (edl.get("segments") or []))
    drops = sorted((int(d.get("src_in", 0)), int(d.get("src_out", 0)))
                   for d in (edl.get("drops") or []))
    out: list[tuple[int, int]] = []
    for a, b in segs:
        cur = a
        for da, db in drops:
            if db <= cur or da >= b:
                continue
            if da > cur:
                out.append((cur, min(da, b)))
            cur = max(cur, db)
        if cur < b:
            out.append((cur, b))
    return out



def lint_edl(edl: dict, words: list[dict], *, style: str = "",
             emphasis_spans: list | None = None, theme=None) -> list[LintFinding]:
    """The deterministic "amateur tell" lint (12 core + 3 craft checks). Pure — never mutates `edl`.
    Reasons entirely in OUTPUT frames via the same source<->output bookkeeping the
    retention passes use, so results reflect what the viewer actually sees."""
    style = style or edl.get("style", "")
    segments = edl.get("segments") or []
    if not segments:
        return []
    seg_events, total_out = _segment_events(edl)
    if not seg_events:
        return []
    play_order = _play_order(edl)
    index, _ = _build_output_index(segments, edl.get("drops") or [], play_order)
    points = _event_points(edl, seg_events, index)

    findings: list[LintFinding] = []
    findings += _check_static_windows(total_out, points, index)
    findings += _check_static_open(points)
    findings += _check_same_framing_adjacent(seg_events)
    findings += _check_metronomic(points)
    findings += _check_repeated_interrupt_type(edl, index)
    findings += _check_anchor_drift(edl, words)
    findings += _check_long_dissolve(edl)
    findings += _check_effect_off_emphasis(edl, emphasis_spans)
    findings += _check_tail_rules(edl, style)
    findings += _check_ungraded(edl, theme)
    findings += _check_bundle_coherence(edl)
    findings += _check_caption_coverage(edl, words)
    # Craft Engine (build 57): research-sourced checks, thresholds from knowledge/craft.
    findings += _check_reading_rate(edl)
    findings += _check_breathe_after_peak(points, total_out)
    findings += _check_ending_complete(edl, words)
    return findings


def lint_summary(findings: list[LintFinding]) -> dict:
    return {
        "errors": sum(1 for f in findings if f["severity"] == "error"),
        "warns": sum(1 for f in findings if f["severity"] == "warn"),
        "codes": [f["code"] for f in findings],
    }
