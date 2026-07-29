"""Cuts-only Ralph loop: fast rounds over raw-take sources, no renders.

Each round, per video: author the EDL TWICE (config.edl_only stops the pipeline
after authoring+guards+retention — ~30-60s/video), grade run A with cut_qc +
the text judge, and compare A vs B kept-sets for determinism. Gate: 0 P0 and
<=2 P1 total, two consecutive rounds.

Usage:  python3 -m eval.cut_loop run --round N [--only id,id]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

from app.edl import ms_to_frame
from eval.campaign_common import finding
from eval.cut_judge import judge
from eval.cut_qc import consistency, grade_cuts, kept_word_set

BASE = "http://127.0.0.1:8000"
WORK = Path(__file__).parent / "cutloop"
POLL_S, BUDGET_S = 6, 600
GATE = {"max_p0": 0, "max_p1": 2, "streak": 2}

# Raw-take sources only (reels are pre-edited content — wrong material for
# grading rough-cut decisions). Includes the owner's real incident source.
VIDEOS = [
    {"id": "cook-a",    "src": "uploads/4af8c948-1d9a-47f4-a2f3-154b1e6408d0/footage.mov"},
    {"id": "procut",    "src": "uploads/77b36bc2-c62e-457d-98ae-4087d87c371e/procut-raw.mp4"},
    {"id": "take-41s",  "src": "uploads/eb2ea688-4a24-45a5-accb-867798570c7c/footage.mov"},
    {"id": "take-47s",  "src": "uploads/a85170f6-affd-491c-aa6d-d03baf2a0de8/footage.mov"},
    {"id": "take-40s",  "src": "uploads/281ed0ab-53e0-40df-a86b-46e245e6d031/footage.mov"},
    {"id": "take-42s",  "src": "uploads/ccc86d4d-89c7-4127-bb67-a608d287abba/footage.mov"},
    {"id": "owner-fusion", "src": "uploads/ba078de9-990a-41b5-a878-08ac5699c82b/footage.mov"},
]
_SUPA = "https://nxibeiykcgxpbmkeadth.supabase.co/storage/v1/object/public/marque-clips/"


async def author_once(client: httpx.AsyncClient, src: str, vid: str, tag: str,
                      sem: asyncio.Semaphore) -> dict | None:
    # One retry after a pause for network-blip failures (this Mac's Wi-Fi
    # drops mid-round; twice on 2026-07-29) — real failures return unchanged.
    j = await _author_attempt(client, src, vid, tag, sem)
    if j and str(j.get("failed", "")).startswith(("source_unreachable", "author_fallback")):
        await asyncio.sleep(30)
        j = await _author_attempt(client, src, vid, tag, sem)
    return j


async def _author_attempt(client: httpx.AsyncClient, src: str, vid: str, tag: str,
                          sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        body = {"source_url": _SUPA + src, "style": "talking_head",
                "edit_format": "talking_head", "analyze_first": True,
                "auto_confirm": True, "creator_id": f"ralphcut-{vid}-{tag}",
                "toggles": {"broll": False, "music": False, "punch_ins": True},
                "config": {"edl_only": True}}
        r = await client.post(f"{BASE}/v1/clips", json=body, timeout=180)
        if r.status_code != 200:
            return None
        jid = r.json().get("job_id", "")
        deadline = time.time() + BUDGET_S
        while time.time() < deadline:
            await asyncio.sleep(POLL_S)
            pr = await client.get(f"{BASE}/v1/clips/{jid}?include_words=1", timeout=60)
            if pr.status_code != 200:
                continue
            j = pr.json()
            st = (j.get("clips") or [{}])[0].get("status") or j.get("status")
            if st == "ready" and j.get("edl"):
                # Network-degraded authoring is surfaced, never graded as real:
                # the fallback stamps ai_edit_unavailable on every clip (F13).
                warns = " ".join(w for c in j.get("clips") or []
                                 for w in c.get("warnings") or [])
                if "ai_edit_unavailable" in warns:
                    return {"failed": "author_fallback (network)", "job_id": jid}
                return j
            if st == "failed":
                return {"failed": j.get("error") or "unknown", "job_id": jid}
        return None


async def run_round(n: int, only: set[str] | None) -> int:
    rdir = WORK / f"round_{n}"
    rdir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(4)
    vids = [v for v in VIDEOS if not only or v["id"] in only]
    findings: list[dict] = []
    async with httpx.AsyncClient() as client:
        pairs = await asyncio.gather(*(
            asyncio.gather(author_once(client, v["src"], v["id"], "a", sem),
                           author_once(client, v["src"], v["id"], "b", sem))
            for v in vids))
    for v, (ja, jb) in zip(vids, pairs):
        vid = v["id"]
        for tag, j in (("a", ja), ("b", jb)):
            if j is None or j.get("failed"):
                findings.append(finding(vid, (j or {}).get("job_id", ""), "job_failed",
                    evidence=str((j or {}).get("failed", "timeout/none")), source="cut_loop"))
        if not ja or ja.get("failed"):
            continue
        (rdir / f"{vid}.json").write_text(json.dumps(ja))
        edl, words = ja["edl"], ja.get("words") or []
        findings += grade_cuts(edl, words, video=vid, job_id=ja.get("job_id", ""))
        findings += await judge(edl, words, video=vid, job_id=ja.get("job_id", ""))
        if jb and not jb.get("failed"):
            sim = consistency(kept_word_set(edl, words),
                              kept_word_set(jb["edl"], jb.get("words") or []))
            if sim < 0.90:
                findings.append(finding(vid, ja.get("job_id", ""), "inconsistent_cuts",
                    evidence=f"kept-set similarity {sim:.2f} across identical runs",
                    source="cut_loop", extra={"sim": round(sim, 3)}))

    p0 = [f for f in findings if f["severity"] == "P0"]
    p1 = [f for f in findings if f["severity"] == "P1"]
    by_cls: dict[str, int] = {}
    for f in findings:
        by_cls[f["class"]] = by_cls.get(f["class"], 0) + 1
    # streak
    state_p = WORK / "state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {"streak": 0}
    ok = len(p0) <= GATE["max_p0"] and len(p1) <= GATE["max_p1"]
    state["streak"] = state.get("streak", 0) + 1 if ok else 0
    state["last_round"] = n
    state_p.write_text(json.dumps(state))
    lines = [f"# Cut loop — round {n}",
             f"P0={len(p0)} P1={len(p1)} | gate {'MET' if ok else 'NOT MET'} | streak={state['streak']}/{GATE['streak']}",
             "", "## By class"]
    lines += [f"- {c}: {k}" for c, k in sorted(by_cls.items(), key=lambda x: -x[1])]
    lines += ["", "## Findings"]
    lines += [f"- [{f['severity']}] {f['video']} {f['class']} @{f['t_seconds']}s: {f['evidence']}"
              for f in sorted(findings, key=lambda f: (f['severity'], f['video']))]
    (rdir / "report.md").write_text("\n".join(lines))
    print("\n".join(lines[:40]))
    print(f"\nreport: {rdir/'report.md'}")
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
