"""Ralph campaign shared vocabulary: the Finding record, severity map, and
source↔output frame mapping helpers every grader uses.

A Finding is a plain dict (JSON-serializable, no classes):
    {"video", "job_id", "class", "severity", "t_seconds", "evidence",
     "source_grader", "extra"}
Severity is assigned HERE from the class key so graders never disagree about
what counts as P0 — one table, one bar (the owner's: P0 = broken output,
P1 = noticeably off, P2 = taste).
"""
from __future__ import annotations

FPS = 30

# class -> severity. Anything unlisted defaults to P2 (taste) so a new grader
# can't accidentally mint silent P0s without touching this table.
CLASS_SEVERITY: dict[str, str] = {
    # pipeline
    "render_failed": "P0",
    "job_failed": "P0",
    # audio_qc
    "audio_pop": "P0",
    "lufs_drift": "P1",
    "lufs_broken": "P0",
    "music_over_speech": "P1",
    "music_missing": "P2",
    # layout_qc
    "layout_collision": "P0",
    "render_safe_breach": "P0",     # the render's own clamp failed
    "safe_zone_breach": "P1",       # platform-chrome tier
    "pos_y_unclamped": "P0",
    # broll_semantics
    "broll_wrong_subject": "P0",
    "broll_distracting": "P1",
    "broll_generic": "P2",
    # broll_timing
    "midword_cut": "P0",
    "broll_timing_off": "P1",
    "broll_linger": "P1",
    "broll_clipped": "P1",
    "broll_overrun_phrase": "P1",
    "broll_crowded": "P1",
    # lint carry-through (edit_lint severities mapped by the runner)
    "lint_error": "P0",
    "lint_warn": "P1",
    # invariants
    "hook_late": "P1",
    "caption_coverage": "P0",
    "broll_over_hook": "P0",
    "broll_over_cta": "P1",
    # meta
    "grader_crashed": "P1",
}


def finding(video: str, job_id: str, cls: str, *, t: float | None = None,
            evidence: str = "", source: str = "", extra: dict | None = None) -> dict:
    return {
        "video": video, "job_id": job_id, "class": cls,
        "severity": CLASS_SEVERITY.get(cls, "P2"),
        "t_seconds": round(t, 2) if t is not None else None,
        "evidence": evidence[:400], "source_grader": source,
        "extra": extra or {},
    }


def clip_out_len(c: dict) -> int:
    return max(1, round((c["src_out"] - c["src_in"]) / (c.get("speed") or 1.0)))


def src_to_out(plan: dict, f: int) -> int | None:
    """Source frame -> output frame via the plan's clips (edl.map_point math)."""
    out_start = 0
    for c in plan.get("clips") or []:
        if c["src_in"] <= f < c["src_out"]:
            return out_start + round((f - c["src_in"]) / (c.get("speed") or 1.0))
        out_start += clip_out_len(c)
    return None


def out_to_src(plan: dict, of: int) -> int | None:
    """Output frame -> source frame (inverse of src_to_out)."""
    out_start = 0
    for c in plan.get("clips") or []:
        ol = clip_out_len(c)
        if out_start <= of < out_start + ol:
            return c["src_in"] + round((of - out_start) * (c.get("speed") or 1.0))
        out_start += ol
    return None


def seam_out_frames(plan: dict) -> list[int]:
    """Output frames where one clip butts the next (excludes 0 and total)."""
    seams, acc = [], 0
    clips = plan.get("clips") or []
    for c in clips[:-1]:
        acc += clip_out_len(c)
        seams.append(acc)
    return seams


def total_out_frames(plan: dict) -> int:
    return sum(clip_out_len(c) for c in (plan.get("clips") or []))
