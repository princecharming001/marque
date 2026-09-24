"""Long-video matrix runner against PRODUCTION (deliberate real-render script).

NOT a test: it never imports main.py and needs no local keys. It drives the public API
exactly like the iOS app (mint -> PUT -> POST /v1/clips analyze_first+auto_confirm ->
poll) with creator ids prefixed ``qa-editor-`` and upload names prefixed ``qa-editor-`` so
every row/object it creates is findable for cleanup.

Guard rails (owner rules for a live 512 MiB box):
  * at most 2 heavy jobs in flight, enforced ACROSS processes with flock'd slot files;
  * before every submit and on every poll it checks Render events for a new
    ``oomKilled``; if one appears after the run started it aborts every job it owns
    and exits 3 (the operator must stop and report);
  * every created storage key / job id / creator id is appended to the ledger
    (``eval/out/long_video/ledger.jsonl``) for the cleanup SQL.

Usage (from backend/):
  .venv/bin/python -m eval.long_video_prod upload FILE [--name qa-editor-x.mov]
  .venv/bin/python -m eval.long_video_prod run CASE --source-url URL [--source-file F] [--style S]
  .venv/bin/python -m eval.long_video_prod batch plan.json
Env: RENDER_API_KEY (read-only use: events + memory metrics), MARQUE_API (default prod).
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

API = os.environ.get("MARQUE_API", "https://marque-api.onrender.com").rstrip("/")
SERVICE = "srv-d94rk95ckfvc73ag4990"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "long_video")
LEDGER = os.path.join(OUT, "ledger.jsonl")
SLOT_DIR = os.environ.get("QA_SLOT_DIR", os.path.join(tempfile.gettempdir(), "marque-qa-slots"))
MAX_SLOTS = 2
POLL_S = 10
FPS = 30
RUN_STARTED = datetime.now(timezone.utc)
_ledger_lock = threading.Lock()


class OOMAbort(RuntimeError):
    pass


def _now() -> float:
    return time.time()


def ledger(kind: str, **kw) -> None:
    os.makedirs(OUT, exist_ok=True)
    with _ledger_lock, open(LEDGER, "a") as f:
        f.write(json.dumps({"ts": _now(), "kind": kind, **kw}) + "\n")


# ---------------------------------------------------------------- Render (read-only)
def _render_get(path: str, **params):
    key = os.environ.get("RENDER_API_KEY", "")
    if not key:
        return None
    try:
        r = httpx.get(f"https://api.render.com/v1{path}", params=params,
                      headers={"Authorization": f"Bearer {key}"}, timeout=20)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def oom_since(start: datetime) -> list[dict]:
    """Every server_failed/oomKilled event after `start` (UTC)."""
    evs = _render_get(f"/services/{SERVICE}/events", limit=20) or []
    hits = []
    for e in evs:
        ev = e.get("event", e)
        ts = ev.get("timestamp", "")
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when <= start:
            continue
        reason = (ev.get("details") or {}).get("reason") or {}
        if ev.get("type") == "server_failed" or "oomKilled" in json.dumps(reason):
            hits.append({"timestamp": ts, "type": ev.get("type"), "reason": reason})
    return hits


def memory_window(t0: float, t1: float) -> dict:
    """Render memory metric (60s resolution, whole instance incl. other traffic)."""
    fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _render_get("/metrics/memory", resource=SERVICE, startTime=fmt(t0 - 60),
                       endTime=fmt(t1 + 60), resolutionSeconds=60) or []
    vals = [v.get("value", 0) for s in data for v in s.get("values", [])]
    return {"mem_peak_mb": round(max(vals) / 1e6, 1) if vals else None,
            "mem_min_mb": round(min(vals) / 1e6, 1) if vals else None, "samples": len(vals)}


def assert_no_oom() -> None:
    hits = oom_since(RUN_STARTED)
    if hits:
        ledger("oom_abort", events=hits)
        raise OOMAbort(json.dumps(hits))


# ---------------------------------------------------------------- slots (<=2 heavy jobs)
@contextlib.contextmanager
def heavy_slot(label: str):
    os.makedirs(SLOT_DIR, exist_ok=True)
    while True:
        for i in range(MAX_SLOTS):
            fh = open(os.path.join(SLOT_DIR, f"slot{i}.lock"), "w")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fh.close()
                continue
            fh.write(f"{label} {os.getpid()} {_now()}\n")
            fh.flush()
            try:
                yield i
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
            return
        time.sleep(5)


# ---------------------------------------------------------------- upload
def upload(path: str, name: str | None = None, content_type: str | None = None) -> dict:
    name = name or os.path.basename(path)
    if not name.startswith("qa-editor-"):
        name = "qa-editor-" + name
    ct = content_type or ("video/mp4" if name.endswith(".mp4") else "video/quicktime")
    size = os.path.getsize(path)
    t0 = _now()
    m = httpx.post(f"{API}/v1/uploads/mint", json={"filename": name, "content_type": ct}, timeout=30).json()
    if m.get("mode") != "live":
        raise RuntimeError(f"mint not live: {m}")
    ledger("storage_object", key=m["key"], public_url=m["public_url"], bytes=size)
    if size > int(m.get("max_upload_bytes") or 0):
        # Mirror the client: over-cap files are never PUT (the app would refuse / compress).
        return {"ok": False, "reason": "over_max_upload_bytes", "size": size,
                "max_upload_bytes": m.get("max_upload_bytes"), "key": m["key"]}
    with open(path, "rb") as fh:
        r = httpx.put(m["upload_url"], content=fh, headers={"Content-Type": ct},
                      timeout=httpx.Timeout(60, write=1800))
    dt = _now() - t0
    return {"ok": r.status_code in (200, 201), "status": r.status_code, "body": r.text[:200],
            "public_url": m["public_url"], "key": m["key"], "bytes": size, "upload_s": round(dt, 1),
            "mbps": round(size * 8 / 1e6 / max(dt, 0.001), 1)}


# ---------------------------------------------------------------- output probes
def ffprobe(url_or_path: str) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,"
                        "r_frame_rate,avg_frame_rate,nb_frames,sample_rate,channels",
                        "-of", "json", url_or_path], capture_output=True, text=True, timeout=120)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"error": p.stderr[-400:]}


def _ff_filter_log(path: str, af: str | None = None, vf: str | None = None, timeout=900) -> str:
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path]
    if vf:
        cmd += ["-vf", vf, "-an"]
    if af:
        cmd += ["-af", af, "-vn"]
    cmd += ["-f", "null", "-"]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stderr


def output_checks(path: str) -> dict:
    out: dict = {}
    log = _ff_filter_log(path, vf="blackdetect=d=0.4:pix_th=0.08")
    out["black_spans"] = [l.split("black_start:")[1].split()[0] + "-" + l.split("black_end:")[1].split()[0]
                          for l in log.splitlines() if "black_start:" in l][:20]
    log = _ff_filter_log(path, af="ebur128=peak=true")
    for l in log.splitlines()[::-1]:
        if "I:" in l and "LUFS" in l and "integrated_lufs" not in out:
            try:
                out["integrated_lufs"] = float(l.split("I:")[1].split("LUFS")[0])
            except (ValueError, IndexError):
                pass
        if "Peak:" in l and "true_peak_dbfs" not in out:
            try:
                out["true_peak_dbfs"] = float(l.split("Peak:")[1].split("dBFS")[0])
            except (ValueError, IndexError):
                pass
    log = _ff_filter_log(path, af="silencedetect=noise=-45dB:d=2.0")
    out["silences_over_2s"] = [l.split("silence_start:")[1].strip() for l in log.splitlines()
                               if "silence_start:" in l][:20]
    return out


def save_frames(path: str, dur: float, tag: str) -> list[str]:
    frames = []
    for label, t in (("start", 0.5), ("mid", dur / 2), ("end", max(0.0, dur - 1.0))):
        dst = os.path.join(OUT, "frames", f"{tag}-{label}.jpg")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", path,
                        "-frames:v", "1", "-vf", "scale=360:-2", dst], timeout=60)
        if os.path.exists(dst):
            frames.append(dst)
    return frames


def edl_checks(edl: dict, words: list[dict]) -> dict:
    """Truncation, mid-word cuts, expected duration (via the same build_render_plan the
    backend renders from), caption coverage and b-roll bounds."""
    from app.edl import build_render_plan  # pure module, no main.py, no keys
    res: dict = {}
    try:
        plan = build_render_plan(edl)
        res["plan_total_frames"] = plan.get("total_frames")
        res["expected_s"] = round((plan.get("total_frames") or 0) / FPS, 2)
        caps = plan.get("captions") or []
        res["captions"] = len(caps)
        if caps:
            last = max((c.get("end_frame") or c.get("frame") or 0) for c in caps)
            res["last_caption_s"] = round(last / FPS, 2)
        br = plan.get("broll") or []
        res["broll"] = len(br)
        tf = plan.get("total_frames") or 0
        res["broll_past_end"] = sum(1 for b in br if (b.get("frame_in", 0) + b.get("frames", b.get("duration_frames", 0))) > tf + 1)
    except Exception as e:  # the check must never kill the run
        res["plan_error"] = repr(e)[:300]
    segs = edl.get("segments") or []
    res["segments"] = len(segs)
    res["drops"] = len(edl.get("drops") or [])
    if words and segs:
        last_word_end_f = max(int(w.get("end_ms", 0)) for w in words) * FPS / 1000
        max_out = max(int(s.get("src_out", 0)) for s in segs)
        res["source_last_word_s"] = round(last_word_end_f / FPS, 2)
        res["kept_until_s"] = round(max_out / FPS, 2)
        res["tail_gap_s"] = round((last_word_end_f - max_out) / FPS, 2)
        mid = 0
        for s in segs:
            for edge in (int(s.get("src_in", 0)), int(s.get("src_out", 0))):
                t_ms = edge * 1000 / FPS
                if any(int(w.get("start_ms", 0)) + 40 < t_ms < int(w.get("end_ms", 0)) - 40 for w in words):
                    mid += 1
        res["mid_word_cuts"] = mid
    return res


# ---------------------------------------------------------------- one job
TERMINAL = {"ready", "failed", "mock_ready", "brief_ready"}


def run_case(case: str, source_url: str, style: str = "talking_head", edit_format: str = "",
             toggles: dict | None = None, creator: str | None = None, source_file: str | None = None,
             budget_s: int = 60 * 60, keep_render: bool = False, extra: dict | None = None) -> dict:
    creator = creator or f"qa-editor-{case}"
    rec: dict = {"case": case, "source_url": source_url, "creator_id": creator, "style": style,
                 "edit_format": edit_format, "toggles": toggles}
    if source_file:
        rec["source_probe"] = ffprobe(source_file).get("format", {})
    with heavy_slot(case):
        assert_no_oom()
        body = {"source_url": source_url, "style": style, "analyze_first": True, "auto_confirm": True,
                "creator_id": creator, "edit_format": edit_format, "script": {}}
        if toggles is not None:
            body["toggles"] = toggles
        if extra:
            body.update(extra)
        t0 = _now()
        r = httpx.post(f"{API}/v1/clips", json=body, timeout=60)
        rec["submit_status"] = r.status_code
        try:
            j = r.json()
        except ValueError:
            j = {"raw": r.text[:300]}
        job_id = j.get("job_id")
        rec["job_id"] = job_id
        ledger("clip_job", job_id=job_id, creator_id=creator, case=case)
        if not job_id:
            rec["error"] = f"submit failed: {j}"
            return rec
        return _watch_and_collect(rec, job_id, t0, budget_s, keep_render, source_url)


def collect(case: str, job_id: str, source_url: str = "", budget_s: int = 60 * 60,
            keep_render: bool = False) -> dict:
    """Finish measuring a job that is already running server-side (e.g. after the local
    runner was stopped). Stage timings start from when collection starts."""
    rec: dict = {"case": case, "job_id": job_id, "source_url": source_url, "collected": True}
    return _watch_and_collect(rec, job_id, _now(), budget_s, keep_render, source_url)


def _watch_and_collect(rec: dict, job_id: str, t0: float, budget_s: int, keep_render: bool,
                       source_url: str) -> dict:
    case = rec["case"]
    if True:
        stages: dict[str, float] = {}
        last = None
        status = None
        while _now() - t0 < budget_s:
            try:
                g = httpx.get(f"{API}/v1/clips/{job_id}", timeout=30)
                gj = g.json() if g.status_code == 200 else {"status": f"http_{g.status_code}"}
            except Exception as e:
                gj = {"status": f"poll_error:{type(e).__name__}"}
            status = gj.get("status")
            clip_states = ",".join(str(c.get("status")) for c in (gj.get("clips") or []))
            key = f"{status}|{clip_states}"
            if key != last:
                stages[key] = round(_now() - t0, 1)
                print(f"[{case}] +{stages[key]:7.1f}s {key} eta={gj.get('eta_seconds')}", flush=True)
                last = key
            if status in TERMINAL and not any(c.get("status") in ("queued", "rendering", "editing")
                                              for c in (gj.get("clips") or [])):
                break
            try:
                assert_no_oom()
            except OOMAbort:
                rec["oom_abort"] = True
                rec["stages"] = stages
                raise
            time.sleep(POLL_S)
        t1 = _now()
    rec["stages"] = stages
    rec["final_status"] = status
    rec["total_s"] = round(t1 - t0, 1)
    rec["memory"] = memory_window(t0, t1)
    rec["oom_events_since_start"] = oom_since(RUN_STARTED)
    try:
        full = httpx.get(f"{API}/v1/clips/{job_id}", params={"include_words": 1}, timeout=60).json()
    except Exception as e:
        full = {"error": repr(e)}
    rec["error"] = full.get("error")
    rec["error_detail"] = full.get("error_detail")
    words = full.get("words") or []
    edl = full.get("edl") or {}
    rec["words"] = len(words)
    os.makedirs(os.path.join(OUT, "jobs"), exist_ok=True)
    json.dump({"words": words, "edl": edl, "clips": full.get("clips"), "broll_log": full.get("broll_log"),
               "lint": full.get("lint"), "self_review": full.get("self_review")},
              open(os.path.join(OUT, "jobs", f"{case}-{job_id}.json"), "w"))
    rec["edl_checks"] = edl_checks(edl, words) if edl else {}
    clips = full.get("clips") or []
    url = next((c.get("render_url") for c in clips if c.get("render_url")), None)
    rec["render_url"] = url
    rec["clip_statuses"] = [(c.get("status"), c.get("last_error") or c.get("error")) for c in clips]
    if url and url != source_url:
        with tempfile.TemporaryDirectory() as td:
            dst = os.path.join(td, "render.mp4")
            t2 = _now()
            with httpx.stream("GET", url, timeout=httpx.Timeout(60, read=600), follow_redirects=True) as s:
                with open(dst, "wb") as fh:
                    for chunk in s.iter_bytes(1 << 20):
                        fh.write(chunk)
            rec["download_s"] = round(_now() - t2, 1)
            pr = ffprobe(dst)
            rec["output_probe"] = pr
            fmt = pr.get("format", {})
            dur = float(fmt.get("duration") or 0)
            rec["output_s"] = round(dur, 2)
            exp = rec["edl_checks"].get("expected_s")
            if exp:
                rec["duration_delta_s"] = round(dur - exp, 2)
            rec["output_checks"] = output_checks(dst)
            rec["frames"] = save_frames(dst, dur, f"{case}-{job_id[:8]}")
            if keep_render:
                keep = os.path.join(OUT, "renders", f"{case}-{job_id[:8]}.mp4")
                os.makedirs(os.path.dirname(keep), exist_ok=True)
                os.replace(dst, keep)
                rec["render_local"] = keep
    elif url == source_url:
        rec["render_is_source"] = True       # the pipeline fell back to the raw take
    os.makedirs(os.path.join(OUT, "results"), exist_ok=True)
    json.dump(rec, open(os.path.join(OUT, "results", f"{case}-{job_id}.json"), "w"), indent=1)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("upload"); u.add_argument("file"); u.add_argument("--name")
    r = sub.add_parser("run"); r.add_argument("case"); r.add_argument("--source-url", required=True)
    r.add_argument("--source-file"); r.add_argument("--style", default="talking_head")
    r.add_argument("--edit-format", default=""); r.add_argument("--keep-render", action="store_true")
    b = sub.add_parser("batch"); b.add_argument("plan")
    c = sub.add_parser("collect"); c.add_argument("case"); c.add_argument("job_id")
    c.add_argument("--source-url", default="")
    sub.add_parser("oomcheck")
    a = ap.parse_args(argv)
    if a.cmd == "upload":
        print(json.dumps(upload(a.file, a.name), indent=1))
        return 0
    if a.cmd == "oomcheck":
        print(json.dumps(oom_since(datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc)), indent=1))
        return 0
    try:
        if a.cmd == "collect":
            print(json.dumps(collect(a.case, a.job_id, a.source_url), indent=1)[:4000])
            return 0
        if a.cmd == "run":
            print(json.dumps(run_case(a.case, a.source_url, a.style, a.edit_format,
                                      source_file=a.source_file, keep_render=a.keep_render), indent=1)[:4000])
            return 0
        plan = json.load(open(a.plan))
        with ThreadPoolExecutor(MAX_SLOTS) as ex:
            futs = [ex.submit(run_case, **c) for c in plan]
            for f in futs:
                res = f.result()
                print(json.dumps({k: res.get(k) for k in ("case", "final_status", "total_s", "output_s",
                                                          "duration_delta_s", "error", "memory")}))
        return 0
    except OOMAbort as e:
        print(f"OOM ABORT: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
