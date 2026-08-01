"""B-roll segmentation for the study: shot boundaries (scene detection, reused
from eval/broll_cadence_probe) × per-shot labels (YuNet face presence + a Haiku
midframe read) -> merged b-roll segments with the DURATION distribution the
owner asked for.

Label decision table (design §5):
  haiku=face                       -> face
  haiku=broll   + no YuNet face    -> broll_fullscreen
  haiku=broll   + YuNet face seen  -> broll_overlay   (inset/PIP over the head)
  haiku=graphic                    -> graphic
  haiku=screen                     -> screen

Known limitation (stated in the findings doc): overlay insets over a static
head may not trip scene>threshold, so overlay BOUNDARIES are approximate —
headline duration stats are computed on fullscreen cutaways only; overlay is
reported as prevalence.
"""
from __future__ import annotations

FLASH_MERGE_S = 0.3      # face-run shorter than this between b-roll runs merges across
BROLL_LABELS = {"broll_fullscreen", "broll_overlay", "screen"}


def label_shot(haiku_label: str, face_present: bool) -> str:
    h = (haiku_label or "").strip().lower()
    if h.startswith("face"):
        return "face"
    if h.startswith("graphic") or "card" in h:
        return "graphic"
    if h.startswith("screen"):
        return "screen"
    if h.startswith("broll") or h.startswith("b-roll"):
        return "broll_overlay" if face_present else "broll_fullscreen"
    return "face"            # conservative default: unknown reads as the speaker


def merge_segments(shots: list[dict], duration_s: float) -> list[dict]:
    """shots: [{t0, t1, label, kind?}] -> b-roll segments [{t0, t1, dur_s, mode,
    kind, zone_pct, n_shots}]. graphic shots participate ONLY mid-reel (the
    first/last-window graphics belong to the title/CTA detectors — the caller
    filters those out before merging)."""
    segs: list[dict] = []
    run: list[dict] = []

    def close() -> None:
        if not run:
            return
        t0, t1 = run[0]["t0"], run[-1]["t1"]
        overlay_t = sum(s["t1"] - s["t0"] for s in run if s["label"] == "broll_overlay")
        kinds = [s.get("kind") or ("screen" if s["label"] == "screen" else "stock") for s in run]
        segs.append({"t0": round(t0, 2), "t1": round(t1, 2),
                     "dur_s": round(t1 - t0, 2),
                     "mode": "overlay" if overlay_t > (t1 - t0) / 2 else "fullscreen",
                     "kind": max(set(kinds), key=kinds.count),
                     "zone_pct": round(t0 / duration_s, 3) if duration_s else 0.0,
                     "n_shots": len(run)})
        run.clear()

    i = 0
    while i < len(shots):
        s = shots[i]
        if s["label"] in BROLL_LABELS or (s["label"] == "graphic" and run):
            run.append(s)
        elif s["label"] == "graphic":
            run.append(s)
        elif run and s["label"] == "face" and (s["t1"] - s["t0"]) < FLASH_MERGE_S \
                and i + 1 < len(shots) and shots[i + 1]["label"] in BROLL_LABELS:
            pass                     # flash face frame between b-roll runs: bridge it
        else:
            close()
        i += 1
    close()
    return segs


def segment_stats(segs: list[dict], duration_s: float) -> dict:
    if not segs:
        return {"segments": [], "count": 0, "per_30s": 0.0, "dur_median_s": None,
                "gap_median_s": None, "first_onset_s": None, "pct_overlay": 0.0,
                "share_of_runtime": 0.0}
    durs = sorted(s["dur_s"] for s in segs)
    gaps = sorted(max(0.0, b["t0"] - a["t1"]) for a, b in zip(segs, segs[1:]))
    total = sum(s["dur_s"] for s in segs)
    return {
        "segments": segs,
        "count": len(segs),
        "per_30s": round(len(segs) * 30 / duration_s, 2) if duration_s else 0.0,
        "dur_median_s": durs[len(durs) // 2],
        "gap_median_s": gaps[len(gaps) // 2] if gaps else None,
        "first_onset_s": segs[0]["t0"],
        "pct_overlay": round(sum(1 for s in segs if s["mode"] == "overlay") / len(segs), 2),
        "share_of_runtime": round(total / duration_s, 3) if duration_s else 0.0,
    }
