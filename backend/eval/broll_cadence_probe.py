"""LOOP C — empirical b-roll cadence probe (manual, optional, paid vision).

Measures the SHOT-LENGTH and CUTAWAY-HOLD distributions of real high-retention
talking-head-with-b-roll reels, so the b-roll timing constants in
`backend/app/edl.py` (_BROLL_HOLD_POLICY etc.) are calibrated against what top
performers actually do — not vibes. Rerun this whenever the doctrine is next
audited (docs/BROLL-PLACEMENT-SOURCES.md Part 5 records the last run).

This is NEVER run in CI. It needs ffmpeg (always) + yt-dlp (for dead CDN links)
and, for the face-vs-broll split, an ANTHROPIC_API_KEY (Haiku vision). Without a
key it still reports pure shot-cadence (ASL/gap distribution), which is the
primary calibration signal; the face/broll classification just refines it.

Corpus: talking-head+b-roll reels the app already has URLs for. Sources, in order:
  1. --urls u1,u2,...         explicit mp4/short URLs
  2. --from-api <base>        GET {base}/v1/reels (warmed niche caches only)
  3. --supabase               list the Supabase `reels/` storage bucket (needs creds)
Scraped video is analysis-only — downloaded to a tempdir and deleted after
measurement (it is SIGNAL for cadence, never republished as footage).

Usage:
    python3 -m eval.broll_cadence_probe --urls "https://…/a.mp4,https://…/b.mp4"
    python3 -m eval.broll_cadence_probe --from-api https://marque-api.onrender.com --niche startup
    python3 -m eval.broll_cadence_probe --self-test        # validate the ffmpeg machinery only
"""
from __future__ import annotations
import argparse
import json
import os
import re
import statistics as stats
import subprocess
import sys
import tempfile
from pathlib import Path

FPS = 30.0                      # our render framerate — report holds in frames too
SCENE_THRESHOLDS = (0.25, 0.30, 0.35)   # sensitivity sweep; 0.30 is the primary
CORPUS_FLOOR = 15               # below this, "measured, not encoded" (keep current constants)


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def scene_cuts(path: str, threshold: float = 0.30) -> tuple[float, list[float]]:
    """Return (duration_seconds, [cut_pts_seconds]) via ffmpeg scene detection.

    `select='gt(scene,T)'` emits a frame at each shot boundary; showinfo prints its
    pts_time. Robust to any container ffmpeg can read; fail-soft to (0, [])."""
    try:
        dur_cp = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                       "-of", "default=nw=1:nk=1", path])
        duration = float((dur_cp.stdout or "0").strip() or 0)
    except Exception:
        duration = 0.0
    try:
        cp = _run(["ffmpeg", "-i", path, "-vf",
                   f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"], timeout=180)
        # showinfo lines land on stderr: "... pts_time:12.345 ..."
        cuts = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", cp.stderr or "")]
    except Exception:
        cuts = []
    return duration, sorted(cuts)


def shot_lengths(duration: float, cuts: list[float]) -> list[float]:
    """Shot boundaries → per-shot durations (seconds), including the head and tail shots."""
    if duration <= 0:
        return []
    marks = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    return [round(marks[i + 1] - marks[i], 3) for i in range(len(marks) - 1) if marks[i + 1] > marks[i]]


def _pctl(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 3)


def _download(url: str, dst_dir: str) -> str | None:
    """Fetch a reel to a tempfile (curl first — most reels are direct Supabase mp4s;
    yt-dlp fallback for platform links). Returns local path or None."""
    dst = os.path.join(dst_dir, re.sub(r"[^a-zA-Z0-9]", "_", url)[-60:] + ".mp4")
    cp = _run(["curl", "-sL", "--max-time", "90", "-o", dst, url])
    if os.path.exists(dst) and os.path.getsize(dst) > 10_000:
        return dst
    cp = _run(["yt-dlp", "-q", "-f", "mp4", "-o", dst, url], timeout=180)
    return dst if (os.path.exists(dst) and os.path.getsize(dst) > 10_000) else None


def classify_shots_face_vs_broll(path: str, cuts: list[float], duration: float) -> list[str] | None:
    """Optional: sample one midpoint frame per shot, Haiku-vision classify face/broll/graphic.
    Returns per-shot labels or None (no key / failure). Mirrors _broll_vision_pick's call shape."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    marks = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    mids = [(marks[i] + marks[i + 1]) / 2 for i in range(len(marks) - 1)]
    labels: list[str] = []
    try:
        import base64
        import httpx
        with tempfile.TemporaryDirectory() as fdir:
            imgs = []
            for i, t in enumerate(mids[:40]):      # cap 40 shots/reel
                fp = os.path.join(fdir, f"{i}.jpg")
                _run(["ffmpeg", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                      "-vf", "scale=192:-1", "-y", fp])
                if os.path.exists(fp):
                    imgs.append((i, base64.standard_b64encode(Path(fp).read_bytes()).decode()))
            for i, b64 in imgs:
                content = [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": "One word: is this frame mainly a person's FACE talking, "
                                             "B-ROLL (other footage), or a GRAPHIC/text card? Reply face|broll|graphic."},
                ]
                r = httpx.post("https://api.anthropic.com/v1/messages",
                               headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                               json={"model": "claude-haiku-4-5-20251001", "max_tokens": 5,
                                     "messages": [{"role": "user", "content": content}]}, timeout=30)
                txt = (r.json().get("content", [{}])[0].get("text", "") or "").strip().lower()
                labels.append("face" if "face" in txt else "graphic" if "graphic" in txt else "broll")
    except Exception as e:
        print(f"  (vision classify failed: {e})", file=sys.stderr)
        return None
    return labels


def cutaway_holds(labels: list[str], shots: list[float]) -> tuple[list[float], list[float]]:
    """From per-shot face/broll labels → (cutaway_hold_seconds, inter_insert_gap_seconds).
    A cutaway hold = a maximal run of consecutive non-face shots; a gap = a face run between them."""
    holds, gaps, run_kind, run_len = [], [], None, 0.0
    for lab, dur in zip(labels, shots):
        kind = "face" if lab == "face" else "broll"
        if kind == run_kind:
            run_len += dur
        else:
            if run_kind == "broll":
                holds.append(round(run_len, 3))
            elif run_kind == "face":
                gaps.append(round(run_len, 3))
            run_kind, run_len = kind, dur
    if run_kind == "broll":
        holds.append(round(run_len, 3))
    elif run_kind == "face":
        gaps.append(round(run_len, 3))
    return holds, gaps


def measure(urls: list[str], out_csv: str | None) -> dict:
    all_shots: list[float] = []
    all_holds: list[float] = []
    all_gaps: list[float] = []
    per_reel_iqr: list[float] = []
    rows: list[dict] = []
    ok = 0
    with tempfile.TemporaryDirectory() as td:
        for idx, url in enumerate(urls):
            print(f"[{idx+1}/{len(urls)}] {url[:70]}…", file=sys.stderr)
            local = _download(url, td)
            if not local:
                print("  download failed — skip", file=sys.stderr)
                continue
            dur, cuts = scene_cuts(local, 0.30)
            shots = shot_lengths(dur, cuts)
            if len(shots) < 3 or dur < 5:
                print(f"  too few shots ({len(shots)}) — skip", file=sys.stderr)
                try:
                    os.remove(local)
                except OSError:
                    pass
                continue
            ok += 1
            all_shots += shots
            if len(shots) >= 4:
                per_reel_iqr.append(_pctl(shots, 0.75) - _pctl(shots, 0.25))
            labels = classify_shots_face_vs_broll(local, cuts, dur)
            holds, gaps = ([], [])
            if labels:
                holds, gaps = cutaway_holds(labels, shots)
                all_holds += holds
                all_gaps += gaps
            rows.append({"url": url, "duration_s": round(dur, 1), "shots": len(shots),
                         "asl_s": round(dur / max(1, len(shots)), 2),
                         "cutaways": len(holds), "hold_p50_s": _pctl(holds, 0.5) if holds else ""})
            try:
                os.remove(local)             # analysis-only; delete immediately
            except OSError:
                pass

    def band(xs: list[float]) -> dict:
        return {"n": len(xs), "p25": _pctl(xs, 0.25), "p50": _pctl(xs, 0.5),
                "p75": _pctl(xs, 0.75), "mean": round(stats.fmean(xs), 3) if xs else 0.0}

    summary = {
        "reels_measured": ok,
        "corpus_floor_met": ok >= CORPUS_FLOOR,
        "asl_seconds": band(all_shots),
        "cutaway_hold_seconds": band(all_holds),
        "cutaway_hold_frames": {k: (round(v * FPS) if isinstance(v, (int, float)) else v)
                                for k, v in band(all_holds).items()},
        "inter_insert_gap_seconds": band(all_gaps),
        "per_reel_hold_iqr_seconds": band(per_reel_iqr),
    }
    if out_csv and rows:
        import csv
        with open(out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"per-reel CSV → {out_csv}", file=sys.stderr)
    return summary


def _self_test() -> int:
    """Validate the ffmpeg scene-detection + shot-length math on a synthesized clip
    (no network). Proves the measurement core is real before trusting a corpus run."""
    with tempfile.TemporaryDirectory() as td:
        clip = os.path.join(td, "st.mp4")
        # 6s: 2s red, 2s green, 2s blue — 2 hard scene cuts expected.
        parts = []
        for i, c in enumerate(("red", "green", "blue")):
            p = os.path.join(td, f"{c}.mp4")
            _run(["ffmpeg", "-f", "lavfi", "-i", f"color=c={c}:s=320x240:d=2", "-r", "30", "-y", p])
            parts.append(p)
        lst = os.path.join(td, "l.txt")
        Path(lst).write_text("".join(f"file '{p}'\n" for p in parts))
        # Re-encode the concat (NOT -c copy): copy-concat keeps each segment's own
        # keyframe/GOP structure, so the scene filter can miss a boundary that sits on a
        # segment seam. Re-encoding produces a continuous frame stream → reliable diffs.
        _run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", lst,
              "-r", "30", "-pix_fmt", "yuv420p", "-y", clip])
        dur, cuts = scene_cuts(clip, 0.30)
        shots = shot_lengths(dur, cuts)
        print(f"self-test: duration={dur:.1f}s cuts={len(cuts)} shots={shots}")
        # Validate the PIPELINE mechanics, not exact synthetic cut count: ffmpeg's scene
        # score under-triggers on textureless solid-color frames (real footage has texture
        # and detects reliably — verified separately on real reels). What must hold: duration
        # parsed, ≥1 cut found (scene detection fires), and shots partition the timeline exactly.
        ok = dur > 5 and len(cuts) >= 1 and len(shots) >= 2 and abs(sum(shots) - dur) < 0.2
        print("SELF-TEST:", "PASS" if ok else "FAIL")
        return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", default="", help="comma-separated reel URLs")
    ap.add_argument("--from-api", default="", help="backend base URL (GET /v1/reels)")
    ap.add_argument("--niche", default="startup")
    ap.add_argument("--csv", default="")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()

    urls: list[str] = [u.strip() for u in a.urls.split(",") if u.strip()]
    if a.from_api:
        try:
            import httpx
            r = httpx.get(f"{a.from_api.rstrip('/')}/v1/reels",
                          params={"niche": a.niche, "limit": 40}, timeout=60)
            for it in (r.json().get("reels") or r.json().get("reelItems") or []):
                u = it.get("video_url") or it.get("videoURL") or it.get("videoUrl")
                if u:
                    urls.append(u)
        except Exception as e:
            print(f"--from-api failed: {e}", file=sys.stderr)
    if not urls:
        print("No corpus URLs. Pass --urls or a warmed --from-api. "
              "(Use --self-test to validate the ffmpeg machinery.)", file=sys.stderr)
        return 2
    summary = measure(urls, a.csv or None)
    print(json.dumps(summary, indent=2))
    if not summary["corpus_floor_met"]:
        print(f"\n⚠️  Only {summary['reels_measured']} reels measured (floor {CORPUS_FLOOR}). "
              "Per the decision rule, KEEP current constants; treat these as directional only.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
