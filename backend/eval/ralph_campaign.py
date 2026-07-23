"""Ralph campaign round runner — edit → grade → report, against a LOCAL backend.

Usage (server started separately, NO --reload — a reload drops in-memory jobs):
    cd backend && SELF_REVIEW=1 EDIT_LINT=observe RETENTION_PASSES=all \
        EDIT_BANDIT=0 TRANSCRIPT_CACHE=1 .venv/bin/uvicorn main:app --port 8000
    python3 -m eval.ralph_campaign run --round 1 [--only v01,v05] [--regrade-only]
    python3 -m eval.ralph_campaign report --round 1

Each round: submit every corpus video (analyze_first + auto_confirm) → poll →
download ALL artifacts immediately (jobs are in-memory w/ 24h TTL; rounds must
be disk-self-contained and re-gradable) → run 8 graders → normalized findings
→ round_N.json + scoreboard.md + state.json convergence tracking.

The runner NEVER edits pipeline code — the fix cycle is the operator's job.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from app.edit_lint import lint_edl
from app.edl import build_render_plan, ms_to_frame
from eval.campaign_common import FPS, finding, src_to_out, total_out_frames
from eval.edl_eval import (CAPTION_COVERAGE_MIN, CTA_PROTECT_FRAMES,
                           HOOK_PROTECT_FRAMES)
from eval import audio_qc, broll_semantics, broll_timing, layout_qc

POLL_INTERVAL_S = 10
JOB_BUDGET_S = 25 * 60
ENV_SNAPSHOT_KEYS = ("SELF_REVIEW", "SELF_REVIEW_THRESHOLD", "EDIT_LINT",
                     "RETENTION_PASSES", "EDIT_THEMES", "CRAFT_PROMPTS",
                     "AUDIO_FINALIZE", "VOICE_POLISH", "VIDEO_UNDERSTANDING",
                     "EDIT_BANDIT", "TRANSCRIPT_CACHE")
GATE = {"max_p0": 0, "max_p1": 2, "required_consecutive": 2}


# ---------------------------------------------------------------- invariants

def invariant_findings(plan: dict, edl: dict, words: list[dict],
                       video: str, job_id: str) -> list[dict]:
    out: list[dict] = []
    total = total_out_frames(plan)
    # Caption coverage ≥ 0.90 over mappable kept words.
    mappable = captioned = 0
    cap_words = {c.get("word", "").lower() for c in plan.get("captions") or []}
    for w in words or []:
        of = src_to_out(plan, ms_to_frame(w.get("start_ms", 0)))
        if of is None:
            continue
        mappable += 1
        if str(w.get("word", "")).lower().strip(".,!?") in cap_words or \
           str(w.get("word", "")).lower() in cap_words:
            captioned += 1
    if mappable and captioned / mappable < CAPTION_COVERAGE_MIN:
        out.append(finding(video, job_id, "caption_coverage",
            evidence=f"{captioned}/{mappable} kept words captioned "
                     f"({captioned / mappable:.0%} < {CAPTION_COVERAGE_MIN:.0%})",
            source="edl_invariants"))
    # Full-frame b-roll over hook/CTA.
    for b in plan.get("broll") or []:
        if (b.get("mode") or "full") != "full":
            continue
        fi, fo = int(b.get("frame_in", 0)), int(b.get("frame_out", 0))
        if fi < HOOK_PROTECT_FRAMES:
            out.append(finding(video, job_id, "broll_over_hook", t=fi / FPS,
                evidence=f"full-frame b-roll [{fi},{fo}] inside the hook window "
                         f"(<{HOOK_PROTECT_FRAMES}f)", source="edl_invariants"))
        if fo > total - CTA_PROTECT_FRAMES:
            out.append(finding(video, job_id, "broll_over_cta", t=fi / FPS,
                evidence=f"full-frame b-roll [{fi},{fo}] inside the CTA window "
                         f"(last {CTA_PROTECT_FRAMES}f of {total})", source="edl_invariants"))
    return out


def lint_findings(edl: dict, words: list[dict], style: str,
                  video: str, job_id: str) -> list[dict]:
    out = []
    try:
        for f in lint_edl(edl, words):
            # Taste-tier warns (a 4f anchor drift, a missing breath-hold) are
            # not "noticeably off" — P2, not P1 (round-6: they dominated the
            # P1 count and drowned the actionable classes).
            _taste = {"anchor_drift", "no_breath_after_peak", "metronomic_intervals"}
            if f.get("severity") == "error":
                sev_cls = "lint_error"
            elif f.get("code") in _taste:
                sev_cls = "lint_taste"
            else:
                sev_cls = "lint_warn"
            fnd = finding(video, job_id, sev_cls,
                          t=(f.get("at_out_frame") or 0) / FPS,
                          evidence=f"[{f.get('code')}] {f.get('detail', '')}",
                          source="edit_lint", extra={"code": f.get("code")})
            out.append(fnd)
    except Exception as e:                                   # grader never kills the round
        out.append(finding(video, job_id, "grader_crashed",
                           evidence=f"edit_lint: {e}", source="edit_lint"))
    return out


# ---------------------------------------------------------------- per video

_TRANSIENT_RENDER_SIGNS = ("failed to fetch", "enotfound", "disk space",
                           "econnreset", "timed out", "socket hang up",
                           "render download", "nodename", "source_unreachable", "delayrender", "fetching inter font", "timeout (30000ms)")


async def run_one_video(client: httpx.AsyncClient, base: str, video: dict,
                        vdir: Path, sem: asyncio.Semaphore) -> dict:
    """One retry ONLY when the failure signature is render-infra transient
    (Lambda Chrome asset-fetch/disk/DNS — round-3 lost 3 videos to a Pexels
    fetch blip). Deterministic pipeline failures stay unretried: they ARE the
    finding."""
    res = await _run_one_video_once(client, base, video, vdir, sem)
    if res.get("status") == "failed" and any(
            s in str(res.get("error", "")).lower() for s in _TRANSIENT_RENDER_SIGNS):
        res2 = await _run_one_video_once(client, base, video, vdir, sem)
        res2["retried_transient"] = True
        return res2
    return res


async def _run_one_video_once(client: httpx.AsyncClient, base: str, video: dict,
                              vdir: Path, sem: asyncio.Semaphore) -> dict:
    async with sem:
        vdir.mkdir(parents=True, exist_ok=True)
        body = {"source_url": video["source_url"], "style": video.get("style", "talking_head"),
                "edit_format": video.get("edit_format", ""),
                "analyze_first": True, "auto_confirm": True,
                "creator_id": f"ralph-{video['id']}",
                "toggles": video.get("toggles"), "config": video.get("config") or {}}
        r = await client.post(f"{base}/v1/clips", json=body, timeout=300)
        if r.status_code != 200:
            return {"id": video["id"], "status": "failed",
                    "error": f"submit {r.status_code}: {r.text[:200]}"}
        sub = r.json()
        if sub.get("mode") == "mock":
            return {"id": video["id"], "status": "failed", "error": "server in MOCK mode — keys missing"}
        job_id = sub.get("job_id", "")

        deadline = time.time() + JOB_BUDGET_S
        job: dict = {}
        while time.time() < deadline:
            await asyncio.sleep(POLL_INTERVAL_S)
            pr = await client.get(f"{base}/v1/clips/{job_id}", timeout=60)
            if pr.status_code != 200:
                continue
            job = pr.json()
            st = (job.get("clips") or [{}])[0].get("status") or job.get("status")
            if st in ("ready", "failed"):
                break
        else:
            return {"id": video["id"], "job_id": job_id, "status": "failed", "error": "job budget exceeded"}

        # Final fetch WITH words (once — thousands of entries).
        fr = await client.get(f"{base}/v1/clips/{job_id}?include_words=1", timeout=120)
        job = fr.json() if fr.status_code == 200 else job
        (vdir / "job.json").write_text(json.dumps(job))

        clip = (job.get("clips") or [{}])[0]
        if clip.get("status") != "ready":
            return {"id": video["id"], "job_id": job_id, "status": "failed",
                    "error": " ".join(str(x) for x in (
                        clip.get("error") or job.get("error") or "unknown",
                        job.get("error_detail") or ""))}

        # Artifacts — download the render IMMEDIATELY (external S3 URL).
        render_url = clip.get("render_url", "")
        rp = vdir / "render.mp4"
        try:
            async with client.stream("GET", render_url, timeout=300) as resp:
                with open(rp, "wb") as fh:
                    async for chunk in resp.aiter_bytes(1 << 16):
                        fh.write(chunk)
        except Exception as e:
            return {"id": video["id"], "job_id": job_id, "status": "failed",
                    "error": f"render download: {e}"}
        plan = build_render_plan(job["edl"])
        (vdir / "plan.json").write_text(json.dumps(plan))
        (vdir / "words.json").write_text(json.dumps(job.get("words") or []))
        return {"id": video["id"], "job_id": job_id, "status": "ready",
                "render_path": str(rp), "render_url": render_url,
                "self_review_score": (job.get("self_review") or {}).get("score"),
                "theme_id": job.get("theme_id")}


async def grade_video(video_id: str, vdir: Path) -> list[dict]:
    job = json.loads((vdir / "job.json").read_text())
    plan = json.loads((vdir / "plan.json").read_text())
    words = json.loads((vdir / "words.json").read_text())
    edl = job.get("edl") or {}
    job_id = job.get("job_id", "")
    style = edl.get("style", "talking_head")
    findings: list[dict] = []

    def safe(name, fn):
        try:
            findings.extend(fn())
        except Exception as e:
            findings.append(finding(video_id, job_id, "grader_crashed",
                                    evidence=f"{name}: {e}", source=name))

    safe("edit_lint", lambda: lint_findings(edl, words, style, video_id, job_id))
    safe("edl_invariants", lambda: invariant_findings(plan, edl, words, video_id, job_id))
    safe("broll_timing", lambda: broll_timing.verify(edl, words, plan,
                                                     video=video_id, job_id=job_id))
    safe("layout_qc", lambda: layout_qc.grade_layout(plan, video=video_id, job_id=job_id))
    try:
        findings.extend(await audio_qc.grade_audio(str(vdir / "render.mp4"), plan, words,
                                                   video=video_id, job_id=job_id))
    except Exception as e:
        findings.append(finding(video_id, job_id, "grader_crashed",
                                evidence=f"audio_qc: {e}", source="audio_qc"))
    try:
        findings.extend(await broll_semantics.grade(str(vdir / "render.mp4"), plan, words,
                                                    video=video_id, job_id=job_id))
    except Exception as e:
        findings.append(finding(video_id, job_id, "grader_crashed",
                                evidence=f"broll_semantics: {e}", source="broll_semantics"))
    # Carry-through: sub-threshold self-review issues as P2 context.
    sr = job.get("self_review") or {}
    for issue in (sr.get("issues") or [])[:6]:
        findings.append(finding(video_id, job_id, "self_review_issue",
                                t=(issue.get("frame") or 0) / FPS,
                                evidence=str(issue.get("code") or issue)[:200],
                                source="self_review"))
    (vdir / "findings.json").write_text(json.dumps(findings, indent=1))
    return findings


# ---------------------------------------------------------------- reporting

def _counts(findings: list[dict]) -> dict:
    c = {"P0": 0, "P1": 0, "P2": 0}
    for f in findings:
        c[f.get("severity", "P2")] = c.get(f.get("severity", "P2"), 0) + 1
    return c


def _by_class(findings: list[dict]) -> dict:
    by: dict[str, dict] = {}
    for f in findings:
        e = by.setdefault(f["class"], {"severity": f["severity"], "count": 0,
                                       "videos": [], "example": f["evidence"]})
        e["count"] += 1
        if f["video"] not in e["videos"]:
            e["videos"].append(f["video"])
    return dict(sorted(by.items(), key=lambda kv: -(
        {"P0": 100, "P1": 10, "P2": 1}[kv[1]["severity"]] * kv[1]["count"])))


def write_round_report(round_n: int, per_video: list[dict], findings: list[dict],
                       work: Path, corpus: dict) -> dict:
    prev = None
    prev_path = work / f"round_{round_n - 1}.json"
    if prev_path.exists():
        prev = json.loads(prev_path.read_text())
    env = {k: os.environ.get(k, "") for k in ENV_SNAPSHOT_KEYS}
    try:
        env["git_sha"] = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                        text=True, cwd=Path(__file__).parent.parent).stdout.strip()[:12]
    except Exception:
        env["git_sha"] = "?"
    if prev and prev.get("env"):
        drift = {k for k in ENV_SNAPSHOT_KEYS if prev["env"].get(k, "") != env.get(k, "")}
        if drift:
            print(f"WARNING: env drift vs round {round_n - 1}: {sorted(drift)} — "
                  f"diff_vs_prev may be misleading", file=sys.stderr)
    by_class = _by_class(findings)
    prev_classes = set((prev or {}).get("by_class") or {})
    cur_classes = set(by_class)
    report = {
        "round": round_n, "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "env": env,
        "videos": [{**v, "counts": _counts([f for f in findings if f["video"] == v["id"]])}
                   for v in per_video],
        "findings": findings,
        "totals": _counts(findings),
        "by_class": by_class,
        "diff_vs_prev": {
            "fixed": sorted(prev_classes - cur_classes),
            "new": sorted(cur_classes - prev_classes),
            "persisting": sorted(cur_classes & prev_classes),
        } if prev else None,
    }
    (work / f"round_{round_n}.json").write_text(json.dumps(report, indent=1))
    return report


def write_scoreboard(report: dict, state: dict, path: Path) -> None:
    t = report["totals"]
    gate = state["gate"]
    lines = [
        f"# Ralph campaign — Round {report['round']} — {report['finished_at']}",
        f"Gate: 0 P0 and <={GATE['max_p1']} P1, {GATE['required_consecutive']} consecutive rounds. "
        f"Status: {'MET' if gate['status'] == 'converged' else 'NOT MET'} "
        f"(P0={t['P0']}, P1={t['P1']}). Streak: {gate['streak']}.",
        "",
        "| video | status | P0 | P1 | P2 | self_review |",
        "|---|---|---|---|---|---|",
    ]
    for v in report["videos"]:
        c = v.get("counts", {})
        lines.append(f"| {v['id']} | {v['status']} | {c.get('P0', 0)} | {c.get('P1', 0)} "
                     f"| {c.get('P2', 0)} | {v.get('self_review_score', '-')} |")
    lines += ["", "## Top error classes"]
    for i, (cls, e) in enumerate(report["by_class"].items(), 1):
        lines.append(f"{i}. {cls:24s} {e['severity']} x{e['count']:<3} videos: "
                     f"{','.join(e['videos'])}")
        lines.append(f"   e.g. {e['example'][:160]}")
    if report.get("diff_vs_prev"):
        d = report["diff_vs_prev"]
        lines += ["", f"## Diff vs round {report['round'] - 1}: "
                      f"fixed: {d['fixed'] or '-'} | new: {d['new'] or '-'} | "
                      f"persisting: {d['persisting'] or '-'}"]
    lines += ["", "## Findings detail"]
    for f in report["findings"]:
        lines.append(f"- [{f['severity']}] {f['video']} {f['class']} @{f['t_seconds']}s "
                     f"({f['source_grader']}): {f['evidence'][:180]}")
    path.write_text("\n".join(lines))


def update_state(work: Path, report: dict) -> dict:
    sp = work / "state.json"
    state = json.loads(sp.read_text()) if sp.exists() else \
        {"round": 0, "gate": {**GATE, "streak": 0, "status": "in_progress",
                              "converged_at_round": None}, "history": []}
    t = report["totals"]
    state["round"] = report["round"]
    state["history"] = [h for h in state["history"] if h["round"] != report["round"]]
    state["history"].append({"round": report["round"], **t,
                             "finished_at": report["finished_at"],
                             "classes": {k: v["count"] for k, v in report["by_class"].items()}})
    state["history"].sort(key=lambda h: h["round"])
    streak = 0
    for h in reversed(state["history"]):
        if h["P0"] <= GATE["max_p0"] and h["P1"] <= GATE["max_p1"]:
            streak += 1
        else:
            break
    state["gate"]["streak"] = streak
    if streak >= GATE["required_consecutive"] and state["gate"]["status"] != "converged":
        state["gate"]["status"] = "converged"
        state["gate"]["converged_at_round"] = report["round"]
    sp.write_text(json.dumps(state, indent=1))
    return state


# ---------------------------------------------------------------- main

async def main_run(round_n: int, corpus_path: str, work_dir: str,
                   only: set[str] | None, concurrency: int, regrade_only: bool) -> int:
    corpus = json.loads(Path(corpus_path).read_text())
    base = corpus.get("api_base", "http://127.0.0.1:8000")
    work = Path(work_dir)
    rdir = work / f"round_{round_n}"
    rdir.mkdir(parents=True, exist_ok=True)
    videos = [v for v in corpus["videos"] if not only or v["id"] in only]

    per_video: list[dict] = []
    if not regrade_only:
        if "127.0.0.1" not in base and "localhost" not in base:
            print(f"refusing non-local api_base {base}", file=sys.stderr)
            return 2
        async with httpx.AsyncClient() as client:
            try:
                hz = await client.get(f"{base}/healthz", timeout=10)
                rz = await client.get(f"{base}/readyz", timeout=10)
                assert hz.status_code == 200
                if '"mock"' in rz.text or "'mock'" in rz.text:
                    print("server is in MOCK mode — check .env keys", file=sys.stderr)
                    return 2
            except Exception:
                print("server not reachable — start it first:\n  cd backend && SELF_REVIEW=1 "
                      "EDIT_LINT=observe RETENTION_PASSES=all,framing,hook_pack,jitter,cold_open,dropout,beat_snap "
                      "EDIT_BANDIT=0 TRANSCRIPT_CACHE=1 "
                      ".venv/bin/uvicorn main:app --port 8000", file=sys.stderr)
                return 2
            sem = asyncio.Semaphore(concurrency)
            per_video = list(await asyncio.gather(*(
                run_one_video(client, base, v, rdir / v["id"], sem) for v in videos)))
    else:
        for v in videos:
            vdir = rdir / v["id"]
            if (vdir / "job.json").exists():
                job = json.loads((vdir / "job.json").read_text())
                per_video.append({"id": v["id"], "job_id": job.get("job_id", ""),
                                  "status": "ready", "render_path": str(vdir / "render.mp4"),
                                  "self_review_score": (job.get("self_review") or {}).get("score")})
            else:
                per_video.append({"id": v["id"], "status": "failed", "error": "no artifacts"})

    findings: list[dict] = []
    for v in per_video:
        if v["status"] != "ready":
            findings.append(finding(v["id"], v.get("job_id", ""), "job_failed",
                                    evidence=v.get("error", ""), source="pipeline"))
            continue
        findings.extend(await grade_video(v["id"], rdir / v["id"]))

    report = write_round_report(round_n, per_video, findings, work, corpus)
    state = update_state(work, report)
    write_scoreboard(report, state, rdir / "scoreboard.md")
    print(f"Round {round_n}: P0={report['totals']['P0']} P1={report['totals']['P1']} "
          f"P2={report['totals']['P2']} | streak={state['gate']['streak']} "
          f"| {'CONVERGED' if state['gate']['status'] == 'converged' else 'in progress'}")
    print(f"scoreboard: {rdir / 'scoreboard.md'}")
    return 0 if state["gate"]["status"] == "converged" else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    runp = sub.add_parser("run")
    runp.add_argument("--round", type=int, required=True)
    runp.add_argument("--corpus", default="eval/corpus.json")
    runp.add_argument("--work-dir", default="eval/campaign")
    runp.add_argument("--only", default="")
    runp.add_argument("--concurrency", type=int, default=2)
    runp.add_argument("--regrade-only", action="store_true")
    repp = sub.add_parser("report")
    repp.add_argument("--round", type=int, required=True)
    repp.add_argument("--work-dir", default="eval/campaign")
    args = ap.parse_args()
    if args.cmd == "run":
        only = {s.strip() for s in args.only.split(",") if s.strip()} or None
        sys.exit(asyncio.run(main_run(args.round, args.corpus, args.work_dir,
                                      only, args.concurrency, args.regrade_only)))
    else:
        work = Path(args.work_dir)
        report = json.loads((work / f"round_{args.round}.json").read_text())
        state = json.loads((work / "state.json").read_text())
        write_scoreboard(report, state, work / f"round_{args.round}" / "scoreboard.md")
        print(f"rebuilt {work / f'round_{args.round}' / 'scoreboard.md'}")


if __name__ == "__main__":
    main()
