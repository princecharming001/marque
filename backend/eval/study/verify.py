"""Adversarial verification gate for the study findings (CP-4). Exit code != 0
blocks the findings from reaching the owner.

Checks (independent recompute — shares only common.py with the aggregator):
  1. recompute: every headline metric re-derived from raw anatomy JSONs; any
     mismatch beyond rounding -> P0 verify_mismatch.
  2. exemplar audit: each finding's exemplar reels must themselves exhibit the
     claimed property (their per-reel value inside the claimed IQR) -> P0.
  3. jackknife: drop the single most-influential reel; a median that leaves the
     reported IQR -> P1 verify_fragile (doc must mark it directional).
  4. floor check: any non-directional finding with n < CORPUS_FLOOR -> P0.

CLI: cd backend && python3 -m eval.study.verify
"""
from __future__ import annotations

import json
import sys

from eval.study.common import ANATOMY_DIR, CORPUS_FLOOR, OUT_DIR

# metric name -> extractor over an anatomy dict (deliberately re-written, not
# imported from aggregate.py, so a shared bug can't self-confirm)
EXTRACTORS = {
    "words_per_chunk": lambda a: (a.get("captions") or {}).get("words_per_chunk_median"),
    "caption_y_center": lambda a: (a.get("captions") or {}).get("y_center_median"),
    "pct_all_caps": lambda a: (a.get("captions") or {}).get("pct_all_caps"),
    "caption_lead_ms": lambda a: (a.get("captions") or {}).get("lead_ms_median"),
    "caption_coverage": lambda a: (a.get("captions") or {}).get("coverage_pct"),
    "broll_dur_s": lambda a: (a.get("broll") or {}).get("dur_median_s"),
    "broll_per_30s": lambda a: (a.get("broll") or {}).get("per_30s"),
    "broll_first_onset_s": lambda a: (a.get("broll") or {}).get("first_onset_s"),
    "broll_share_runtime": lambda a: (a.get("broll") or {}).get("share_of_runtime"),
    "cuts_per_30s": lambda a: (a.get("cut_stats") or {}).get("cuts_per_30s"),
    "wpm": lambda a: (a.get("transcript") or {}).get("wpm"),
}


def _median(vals: list) -> float | None:
    vals = sorted(v for v in vals if v is not None)
    return vals[len(vals) // 2] if vals else None


def run() -> int:
    agg_path = OUT_DIR / "aggregates.json"
    if not agg_path.exists():
        print("no aggregates.json — run aggregate first")
        return 1
    agg = json.loads(agg_path.read_text())
    reels = {}
    for f in ANATOMY_DIR.glob("*.json"):
        a = json.loads(f.read_text())
        if not a.get("excluded") and a.get("platform") != "local":
            reels[a["reel_id"]] = a
    ig = [a for a in reels.values() if a.get("platform") == "instagram"] or list(reels.values())
    # Gate v2 population spec: b-roll/cut norms are computed over TRUE talking-head
    # reels only (tier "th"); caption/CTA/title metrics over all spoken reels.
    th_only = [a for a in ig if (a.get("tier") or "th") == "th"]
    TH_METRICS = {"broll_dur_s", "broll_per_30s", "broll_first_onset_s",
                  "broll_share_runtime", "cuts_per_30s"}

    findings: list[str] = []
    for name, m in (agg.get("metrics") or {}).items():
        fn = EXTRACTORS.get(name)
        if fn is None:
            continue
        pool = th_only if name in TH_METRICS else ig
        vals = [fn(a) for a in pool]
        med = _median(vals)
        # 1. recompute
        if med is None or abs(float(med) - float(m["median"])) > max(0.011, abs(float(m["median"])) * 0.02):
            findings.append(f"P0 verify_mismatch {name}: recomputed {med} vs reported {m['median']}")
        # 2. exemplar audit
        lo, hi = m["iqr"]
        for rid in m.get("exemplars", []):
            a = reels.get(rid)
            v = fn(a) if a else None
            if v is None or not (float(lo) <= float(v) <= float(hi)):
                findings.append(f"P0 verify_exemplar {name}: {rid} value {v} outside IQR [{lo},{hi}]")
        # 3. jackknife
        present = [(a["reel_id"], fn(a)) for a in pool if fn(a) is not None]
        if len(present) >= 3 and not m.get("directional"):
            worst = None
            for rid, _ in present:
                jk = _median([v for r, v in present if r != rid])
                if jk is not None and not (float(lo) <= float(jk) <= float(hi)):
                    worst = (rid, jk)
                    break
            if worst:
                findings.append(f"P1 verify_fragile {name}: dropping {worst[0]} moves median "
                                f"to {worst[1]} outside IQR — mark directional")
        # 4. floor
        if not m.get("directional") and m["n"] < CORPUS_FLOOR:
            findings.append(f"P0 verify_floor {name}: n={m['n']} < {CORPUS_FLOOR} but not directional")

    report = ["# Study verify report",
              f"reels: {len(ig)} (IG subset) | metrics checked: {len(agg.get('metrics') or {})}",
              ""]
    report += [f"- {f}" for f in findings] or ["- CLEAN — all checks passed"]
    (OUT_DIR / "verify_report.md").write_text("\n".join(report))
    print("\n".join(report))
    p0 = sum(1 for f in findings if f.startswith("P0"))
    return 1 if p0 else 0


if __name__ == "__main__":
    sys.exit(run())
