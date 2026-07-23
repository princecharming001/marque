"""Ralph campaign grader: phrase-timing verifier for b-roll windows (pure math).

Codifies the 57.9 contract that was verified frame-level on a live render:
  - ENTRY: src_in == (cue word's start frame) − _BROLL_JCUT_LEAD, ±tolerance.
  - HOLD: within the _BROLL_HOLD_POLICY band for its need/mode (±jitter).
  - EXIT (phrase-covering needs): never runs meaningfully past the cue phrase's
    end — the "lingering" class the owner reported.
  - SPACING: windows don't crowd below the tight floor.
  - MIDWORD: no plan SEAM lands strictly inside a spoken word (butt-splicing a
    word in half is the single most audible edit error).

Inputs are the job's EDL (source coords) + words + the computed render plan.
No network, no ffmpeg — safe in unit tests with handcrafted fixtures.
"""
from __future__ import annotations

from app.edl import (_BROLL_HOLD_JITTER, _BROLL_JCUT_LEAD, _hold_bounds,
                     ms_to_frame)
from eval.campaign_common import FPS, finding, seam_out_frames, src_to_out

_ENTRY_TOL_F = 5          # owner bar: entry >5f off its word = P1
_EXIT_GRACE_F = 9         # phrase end + breath(3) + jitter(6)
_SPACING_FLOOR_F = 20     # below the halved glimpse floor = genuinely crowded
_PHRASE_NEEDS = ("evidence", "action", "concept")


def _word_frames(words: list[dict]) -> list[tuple[int, int, str]]:
    out = []
    for w in words or []:
        s, e = w.get("start_ms"), w.get("end_ms")
        if s is None or e is None or e <= s:
            continue
        out.append((ms_to_frame(s), ms_to_frame(e), str(w.get("word", ""))))
    return out


def verify(edl: dict, words: list[dict], plan: dict, *,
           video: str = "", job_id: str = "") -> list[dict]:
    findings: list[dict] = []
    wf = _word_frames(words)
    broll = sorted(edl.get("broll") or [], key=lambda b: int(b.get("src_in", 0)))

    prev_out_end: int | None = None
    for b in broll:
        si, so = int(b.get("src_in", 0)), int(b.get("src_out", 0))
        need = b.get("need") or "action"
        mode = b.get("mode") or "full"
        cue = (b.get("cue_text") or b.get("cue") or "")[:40]
        t_out = src_to_out(plan, si)
        t = (t_out / FPS) if t_out is not None else None

        # ENTRY: nearest word start to the implied cue point.
        if wf:
            implied_cue = si + _BROLL_JCUT_LEAD
            nearest = min((abs(s - implied_cue), s) for s, _, _ in wf)
            if nearest[0] > _ENTRY_TOL_F:
                findings.append(finding(video, job_id, "broll_timing_off", t=t,
                    evidence=f"entry src {si} is {nearest[0]}f off the nearest word start "
                             f"(cue point {implied_cue}, nearest {nearest[1]}) — '{cue}'",
                    source="broll_timing", extra={"src_in": si, "delta_f": nearest[0]}))

        # HOLD band.
        hold = so - si
        lo, hi = _hold_bounds(need, mode)
        if hold > hi + _BROLL_HOLD_JITTER:
            findings.append(finding(video, job_id, "broll_linger", t=t,
                evidence=f"hold {hold}f > band max {hi}f (+{_BROLL_HOLD_JITTER} jitter) "
                         f"for {need}/{mode} — '{cue}'", source="broll_timing",
                extra={"hold": hold, "band": [lo, hi]}))
        elif hold < lo - _BROLL_HOLD_JITTER:
            findings.append(finding(video, job_id, "broll_clipped", t=t,
                evidence=f"hold {hold}f < band min {lo}f for {need}/{mode} — '{cue}'",
                source="broll_timing", extra={"hold": hold, "band": [lo, hi]}))

        # EXIT vs phrase end for phrase-covering needs: the window should stop at
        # a word end near its phrase, not run long past all speech it covers.
        if need in _PHRASE_NEEDS and wf:
            ends_before = [e for _, e, _ in wf if e <= so]
            if ends_before:
                last_covered_end = max(ends_before)
                if so - last_covered_end > _EXIT_GRACE_F:
                    findings.append(finding(video, job_id, "broll_overrun_phrase", t=t,
                        evidence=f"exit src {so} runs {so - last_covered_end}f past the last "
                                 f"covered word end ({last_covered_end}) — '{cue}'",
                        source="broll_timing", extra={"src_out": so}))

        # SPACING vs previous window.
        if prev_out_end is not None and si - prev_out_end < _SPACING_FLOOR_F and si >= prev_out_end:
            findings.append(finding(video, job_id, "broll_crowded", t=t,
                evidence=f"gap {si - prev_out_end}f to previous b-roll (< {_SPACING_FLOOR_F}f floor)",
                source="broll_timing"))
        prev_out_end = max(prev_out_end or 0, so)

    # MIDWORD: any seam strictly inside a word's span (checked in OUTPUT time —
    # a source-coord check would miss reordered clips).
    word_out: list[tuple[int, int, str]] = []
    for s, e, wtext in wf:
        os_, oe = src_to_out(plan, s), src_to_out(plan, max(s, e - 1))
        if os_ is not None and oe is not None and oe > os_:
            word_out.append((os_, oe, wtext))
    for seam in seam_out_frames(plan):
        for os_, oe, wtext in word_out:
            if os_ + 1 <= seam <= oe - 1:
                findings.append(finding(video, job_id, "midword_cut", t=seam / FPS,
                    evidence=f"seam at out {seam} splits the word '{wtext}' [{os_},{oe}]",
                    source="broll_timing", extra={"seam": seam, "word": wtext}))
                break
    return findings
