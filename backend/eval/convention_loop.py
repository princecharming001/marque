"""Wave-3 convention-convergence Ralph loop: author each corpus video via the
live local server (edl_only fast path), grade with the convention graders PLUS
the proven cut-grammar stack. Gate: 0 P0, <=2 P1, two consecutive rounds.

Usage:  python3 -m eval.convention_loop run --round N [--only id,...]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

from eval.campaign_common import finding
from eval.convention_qc import grade_conventions
from eval.cut_judge import judge
from eval.cut_qc import grade_cuts
from eval.cut_loop import VIDEOS, _SUPA, author_once

WORK = Path(__file__).parent / "convloop"
GATE = {"max_p0": 0, "max_p1": 2, "streak": 2}
# b-roll surfaces need cues: these three author with broll ON (talking_head_broll)
BROLL_ON = {"cook-a", "take-47s", "owner-fusion"}


async def run_round(n: int, only: set[str] | None) -> int:
    rdir = WORK / f"round_{n}"
    rdir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(4)
    vids = [v for v in VIDEOS if not only or v["id"] in only]
    findings: list[dict] = []
    async with httpx.AsyncClient() as client:
        jobs = await asyncio.gather(*(
            author_once(client, v["src"], v["id"],
                        "broll" if v["id"] in BROLL_ON else "plain", sem,
                        body_overrides=({"style": "broll_cutaway",
                                         "edit_format": "talking_head_broll",
                                         "toggles": {"broll": True, "music": False,
                                                     "punch_ins": True}}
                                        if v["id"] in BROLL_ON else None))
            for v in vids))
    for v, j in zip(vids, jobs):
        vid = v["id"]
        if j is None or j.get("failed"):
            findings.append(finding(vid, (j or {}).get("job_id", ""), "job_failed",
                evidence=str((j or {}).get("failed", "timeout/none")), source="conv_loop"))
            continue
        (rdir / f"{vid}.json").write_text(json.dumps(j))
        edl, words = j["edl"], j.get("words") or []
        findings += grade_conventions(j, video=vid)
        findings += grade_cuts(edl, words, video=vid, job_id=j.get("job_id", ""))
        findings += await judge(edl, words, video=vid, job_id=j.get("job_id", ""))

    p0 = [f for f in findings if f["severity"] == "P0"]
    p1 = [f for f in findings if f["severity"] == "P1"]
    by_cls: dict[str, int] = {}
    for f in findings:
        by_cls[f["class"]] = by_cls.get(f["class"], 0) + 1
    state_p = WORK / "state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {"streak": 0}
    ok = len(p0) <= GATE["max_p0"] and len(p1) <= GATE["max_p1"]
    state["streak"] = state.get("streak", 0) + 1 if ok else 0
    state["last_round"] = n
    state_p.write_text(json.dumps(state))
    lines = [f"# Convention loop — round {n}",
             f"P0={len(p0)} P1={len(p1)} | gate {'MET' if ok else 'NOT MET'} | "
             f"streak={state['streak']}/{GATE['streak']}",
             "", "## By class"]
    lines += [f"- {c}: {k}" for c, k in sorted(by_cls.items(), key=lambda x: -x[1])]
    lines += ["", "## Findings"]
    lines += [f"- [{f['severity']}] {f['video']} {f['class']}: {f['evidence']}"
              for f in sorted(findings, key=lambda f: (f['severity'], f['video']))]
    (rdir / "report.md").write_text("\n".join(lines))
    print("\n".join(lines[:44]))
    return 0 if state["streak"] >= GATE["streak"] else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--only", type=str, default="")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    sys.exit(asyncio.run(run_round(a.round, only)))


if __name__ == "__main__":
    main()
