"""Aggregate anatomy JSONs -> aggregates.json + ig_th_conventions.draft.md.

Two-level aggregation: per-reel value first (one caption-dense reel can't
dominate), then median+IQR across reels. Every finding carries n + the top-3
exemplar reel ids (nearest the median, highest views). Headline numbers come
from the IG subset; TikTok is a comparison column. The draft STAYS in
data/out/ until the owner approves (nothing touches knowledge/).

CLI: cd backend && python3 -m eval.study.aggregate [--include-local]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from eval.study.common import (ANATOMY_DIR, CORPUS_FLOOR, OUT_DIR,
                               PER_NICHE_FLOOR, ensure_dirs)

# Current shipped constants for the measured-vs-shipped table (import the real
# values so the doc can't drift from the code).
from app.layout_constants import PHRASE_LEN, SAFE_BOTTOM_PX  # noqa: F401


def _median_iqr(vals: list) -> dict | None:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    return {"median": vals[n // 2],
            "iqr": [vals[n // 4], vals[(3 * n) // 4]],
            "n": n}


def _exemplars(reels: list[dict], key, median) -> list[str]:
    scored = []
    for r in reels:
        v = key(r)
        if v is None or median is None:
            continue
        scored.append((abs(v - median), -(r.get("views") or 0), r["reel_id"]))
    scored.sort()
    return [rid for _, _, rid in scored[:3]]


def _metric(reels: list[dict], name: str, key) -> dict | None:
    vals = [key(r) for r in reels]
    stat = _median_iqr(vals)
    if stat is None:
        return None
    stat["metric"] = name
    stat["exemplars"] = _exemplars(reels, key, stat["median"])
    stat["directional"] = stat["n"] < CORPUS_FLOOR
    return stat


def load_reels(include_local: bool = False) -> list[dict]:
    out = []
    for f in sorted(ANATOMY_DIR.glob("*.json")):
        a = json.loads(f.read_text())
        if a.get("excluded"):
            continue
        if not include_local and a.get("platform") == "local":
            continue
        out.append(a)
    return out


def compute(reels: list[dict]) -> dict:
    ig = [r for r in reels if r.get("platform") == "instagram"] or reels
    # Two-tier corpus: caption/CTA/title conventions hold across all spoken
    # reels; b-roll density/duration norms only over TRUE talking-head reels
    # (tier "th") — voiceover-over-b-roll is a different format.
    th_only = [r for r in ig if (r.get("tier") or "th") == "th"]
    caps = lambda r: r.get("captions") or {}
    br = lambda r: r.get("broll") or {}
    agg = {
        "computed_at": time.time(),
        "n_total": len(reels), "n_th": len([r for r in reels if (r.get("tier") or "th") == "th"]), "n_ig": len([r for r in reels if r.get("platform") == "instagram"]),
        "metrics": {},
        "title_card": {}, "cta_patterns": {}, "caption_style": {},
    }
    M = agg["metrics"]
    M["words_per_chunk"] = _metric(ig, "words_per_chunk",
                                   lambda r: caps(r).get("words_per_chunk_median"))
    M["caption_y_center"] = _metric(ig, "caption_y_center",
                                    lambda r: caps(r).get("y_center_median"))
    M["pct_all_caps"] = _metric(ig, "pct_all_caps", lambda r: caps(r).get("pct_all_caps"))
    M["caption_lead_ms"] = _metric(ig, "caption_lead_ms",
                                   lambda r: caps(r).get("lead_ms_median"))
    M["caption_coverage"] = _metric(ig, "caption_coverage",
                                    lambda r: caps(r).get("coverage_pct"))
    M["broll_dur_s"] = _metric(
        [r for r in th_only if br(r).get("dur_median_s") is not None],
        "broll_dur_s", lambda r: br(r).get("dur_median_s"))
    # fullscreen-only durations (headline per the stated overlay limitation)
    fs = []
    for r in th_only:
        ds = [s["dur_s"] for s in br(r).get("segments", []) if s["mode"] == "fullscreen"]
        if ds:
            fs.append({"reel_id": r["reel_id"], "views": r.get("views"),
                       "v": sorted(ds)[len(ds) // 2]})
    M["broll_fullscreen_dur_s"] = _metric(
        [{"reel_id": x["reel_id"], "views": x["views"], "_v": x["v"]} for x in fs],
        "broll_fullscreen_dur_s", lambda r: r["_v"])
    M["broll_per_30s"] = _metric(th_only, "broll_per_30s", lambda r: br(r).get("per_30s"))
    M["broll_first_onset_s"] = _metric(
        [r for r in th_only if br(r).get("first_onset_s") is not None],
        "broll_first_onset_s", lambda r: br(r).get("first_onset_s"))
    M["broll_share_runtime"] = _metric(th_only, "broll_share_runtime",
                                       lambda r: br(r).get("share_of_runtime"))
    M["cuts_per_30s"] = _metric(th_only, "cuts_per_30s",
                                lambda r: (r.get("cut_stats") or {}).get("cuts_per_30s"))
    M["wpm"] = _metric(ig, "wpm", lambda r: (r.get("transcript") or {}).get("wpm"))
    agg["metrics"] = {k: v for k, v in M.items() if v is not None}

    # prevalences
    def prev(pred, pool) -> dict:
        hits = [r["reel_id"] for r in pool if pred(r)]
        return {"pct": round(len(hits) / len(pool), 2) if pool else None,
                "n": len(pool), "exemplars": hits[:3]}
    agg["broll_overlay_prevalence"] = prev(
        lambda r: (br(r).get("pct_overlay") or 0) > 0, ig)
    agg["captions_prevalence"] = prev(lambda r: caps(r).get("present"), ig)
    tc_all = prev(lambda r: (r.get("title_card") or {}).get("present"), ig)
    agg["title_card"] = {"overall": tc_all, "by_content_type": {}}
    for ct in sorted({r.get("content_type") or "other" for r in ig}):
        pool = [r for r in ig if (r.get("content_type") or "other") == ct]
        agg["title_card"]["by_content_type"][ct] = prev(
            lambda r: (r.get("title_card") or {}).get("present"), pool)
    tc_durs = [r["title_card"].get("dur_s") for r in ig
               if (r.get("title_card") or {}).get("present")]
    agg["title_card"]["dur_s"] = _median_iqr(tc_durs)

    for r in ig:
        p = (r.get("cta") or {}).get("pattern") or "none"
        agg["cta_patterns"][p] = agg["cta_patterns"].get(p, 0) + 1

    for k in ("karaoke_highlight", "stroke", "boxed", "emoji_in_captions"):
        pool = [r for r in ig if r.get("caption_style")]
        agg["caption_style"][k] = prev(
            lambda r, k=k: (r.get("caption_style") or {}).get(k), pool)
    weights = [(r.get("caption_style") or {}).get("font_weight") for r in ig
               if r.get("caption_style")]
    agg["caption_style"]["font_weight_mode"] = (
        max(set(w for w in weights if w), key=weights.count) if any(weights) else None)
    return agg


def render_doc(agg: dict, reels: list[dict]) -> str:
    shipped = {
        "words_per_chunk": f"PHRASE_LEN={PHRASE_LEN} (layout.json)",
        "caption_y_center": "pos_y default 0.62 (edl.py caption plan)",
        "caption_lead_ms": "sync_lead_frames=0 (CaptionOptions default)",
        "broll_dur_s": "hold policy table (edl.py _BROLL_HOLD_POLICY)",
        "broll_per_30s": "density floor/caps (edl.py b-roll floor)",
        "cuts_per_30s": "pacing passes (retention.py)",
    }
    L = ["# IG talking-head conventions — measured",
         f"\nCorpus: n={agg['n_total']} analyzed (IG {agg['n_ig']}), "
         f"computed {time.strftime('%Y-%m-%d %H:%M')}. "
         "Findings below the corpus floor are marked DIRECTIONAL.",
         "\n## Measured metrics\n",
         "| metric | median | IQR | n | shipped today | exemplars |",
         "|---|---|---|---|---|---|"]
    for name, m in agg["metrics"].items():
        d = " (DIRECTIONAL)" if m.get("directional") else ""
        L.append(f"| {name}{d} | {m['median']} | {m['iqr'][0]}–{m['iqr'][1]} | {m['n']} "
                 f"| {shipped.get(name, '—')} | {', '.join(m['exemplars'])} |")
    tc = agg["title_card"]
    L += ["\n## Title card",
          f"- Opening title card overall: {tc['overall']['pct']} of n={tc['overall']['n']}"]
    for ct, p in tc["by_content_type"].items():
        note = " (below per-type floor — directional)" if p["n"] < PER_NICHE_FLOOR else ""
        L.append(f"  - {ct}: {p['pct']} (n={p['n']}){note}")
    if tc.get("dur_s"):
        L.append(f"- Duration when present: median {tc['dur_s']['median']}s "
                 f"IQR {tc['dur_s']['iqr'][0]}–{tc['dur_s']['iqr'][1]}s")
    L += ["\n## CTA patterns (frequency)"]
    total = sum(agg["cta_patterns"].values()) or 1
    for p, k in sorted(agg["cta_patterns"].items(), key=lambda x: -x[1]):
        L.append(f"- {p}: {k} ({k / total:.0%})")
    L += ["\n## Caption style prevalence"]
    for k, v in agg["caption_style"].items():
        L.append(f"- {k}: {v if not isinstance(v, dict) else v['pct']}")
    L += ["\n## Known limitations",
          "- Overlay-inset b-roll boundaries are approximate under scene detection — "
          "fullscreen-cutaway durations are the headline; overlay is prevalence-only.",
          "- lead_ms resolution is ±200ms at 5fps sampling.",
          "\n*(Draft. Nothing here is applied to the pipeline until owner approval — "
          "see gap_analysis.md for the measured us-vs-them deltas.)*"]
    return "\n".join(L)


def main_cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-local", action="store_true")
    a = ap.parse_args()
    ensure_dirs()
    reels = load_reels(include_local=a.include_local)
    if not reels:
        print("no analyzed reels")
        sys.exit(1)
    agg = compute(reels)
    (OUT_DIR / "aggregates.json").write_text(json.dumps(agg, indent=1))
    (OUT_DIR / "ig_th_conventions.draft.md").write_text(render_doc(agg, reels))
    print(f"aggregated {len(reels)} reels -> {OUT_DIR}")
    sys.exit(0)


if __name__ == "__main__":
    main_cli()
