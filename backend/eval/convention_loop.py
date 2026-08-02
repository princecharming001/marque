"""Wave-3 convention-convergence Ralph loop: author each corpus video via the
live local server (edl_only fast path), grade with the convention graders PLUS
the proven cut-grammar stack. Gate: 0 P0, <=2 P1, two consecutive rounds.

The server MUST be started with the production pass set — the retention passes are
env-gated and a bare server silently returns the pre-retention EDL, which makes every
end_card / CTA / title grader vacuously green:

    cd backend && SELF_REVIEW=1 EDIT_LINT=observe EDL_AUTHOR=plan \\
      RETENTION_PASSES=all,framing,hook_pack,jitter,cold_open,dropout,beat_snap \\
      EDIT_BANDIT=0 TRANSCRIPT_CACHE=1 uvicorn main:app --port 8000

EDL_AUTHOR=plan matters as much as the passes: it defaults to `legacy`, whose author
emits no caption conventions layer, so a bare server reports stroke/case findings on
every video that say nothing about the code under test.

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

_BROLL_BODY = {"style": "broll_cutaway", "edit_format": "talking_head_broll",
               "toggles": {"broll": True, "music": False, "punch_ins": True}}

# v8 taste surfaces. These graders read the config the creator SENT, so they only
# mean anything if some case actually sends one — without these three the CTA and
# profile checks in convention_qc were unreachable code.
_LOUD_PROFILE = {"pace": 0.9, "energy": 0.9, "caption_boldness": 0.9,
                 "caption_chunking": 0.9, "broll_density": 0.5, "broll_share": 0.3,
                 "broll_overlay_bias": 0.5, "title_cta_flair": 0.9}
_TASTE_CASES = [
    # an explicit overlay template + outro copy must be stamped verbatim
    {"id": "cta-overlay", "on": "take-40s",
     "config": {"cta_style_id": "bar_sweep", "outro_text": "Follow for more",
                "outro_handle": "@yunicorn"}},
    # "no CTA" is a first-class pick: it must beat a plan-authored card
    {"id": "cta-none", "on": "take-41s",
     "config": {"cta_style_id": "none", "outro_text": "Follow for more"}},
    # a learned profile must move the pipeline, not just get logged
    {"id": "profile-loud", "on": "cook-a", "config": {}},
]


def _cases(only: set[str] | None) -> list[dict]:
    """One case = one authored job + the exact config it was submitted with."""
    by_id = {v["id"]: v for v in VIDEOS}
    out = []
    for v in VIDEOS:
        out.append({"id": v["id"], "src": v["src"],
                    "tag": "broll" if v["id"] in BROLL_ON else "plain",
                    "body": dict(_BROLL_BODY) if v["id"] in BROLL_ON else {}})
    for tc in _TASTE_CASES:
        base = by_id.get(tc["on"])
        if not base:
            continue
        cfg = dict(tc["config"])
        if tc["id"] == "profile-loud":
            cfg["style_profile"] = json.dumps(_LOUD_PROFILE)
        body = dict(_BROLL_BODY) if tc["on"] in BROLL_ON else {}
        body["config"] = cfg
        out.append({"id": tc["id"], "src": base["src"], "tag": tc["id"], "body": body,
                    "taste": True})
    return [c for c in out if not only or c["id"] in only]


async def run_round(n: int, only: set[str] | None) -> int:
    rdir = WORK / f"round_{n}"
    rdir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(4)
    cases = _cases(only)
    findings: list[dict] = []
    async with httpx.AsyncClient() as client:
        jobs = await asyncio.gather(*(
            author_once(client, c["src"], c["id"], c["tag"], sem,
                        body_overrides=c["body"] or None)
            for c in cases))
    for c, j in zip(cases, jobs):
        vid = c["id"]
        if j is None or j.get("failed"):
            findings.append(finding(vid, (j or {}).get("job_id", ""), "job_failed",
                evidence=str((j or {}).get("failed", "timeout/none")), source="conv_loop"))
            continue
        (rdir / f"{vid}.json").write_text(json.dumps(j))
        edl, words = j["edl"], j.get("words") or []
        findings += grade_conventions(j, video=vid, config=c["body"].get("config") or {})
        # A taste case is a SECOND authoring of a source the corpus already grades, so
        # running the cut graders on it double-counts one defect against the gate (and
        # pays for a second judge call). Cuts are the cut loop's job; these cases exist
        # to prove the CTA/profile contract.
        if c.get("taste"):
            continue
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
