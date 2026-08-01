"""Per-reel anatomy pipeline: download -> TH gate -> transcript -> shots ->
labels -> OCR captions -> vision style -> title/CTA detectors -> anatomy JSON,
then DELETE the video (analysis-only boundary, enforced in a `finally`).

CLI:
  cd backend && python3 -m eval.study.anatomy run [--only id,...] [--resume] [--concurrency 4]
  cd backend && python3 -m eval.study.anatomy audit                # CP-3 instrument audit
  cd backend && python3 -m eval.study.anatomy one --path X.mp4     # local file (gap analysis)
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

import main  # noqa — established eval/ pattern (AssemblyAI key + helpers)
from app import faces as faces_mod
from eval.broll_cadence_probe import _download, scene_cuts
from eval.study import broll as broll_mod
from eval.study import cards as cards_mod
from eval.study import ocr_track
from eval.study.common import (ANATOMY_DIR, OCR_DIR, SCHEMA_VERSION, StudyConfig,
                               TRANSCRIPTS_DIR, ensure_dirs, load_manifest,
                               mark_stage, save_manifest)

HAIKU = "claude-haiku-4-5-20251001"
TH_FACE_RATIO = 0.5
TH_MIN_WORDS = 12


# --- transcript (AssemblyAI local-file upload; CDN URLs 403 their fetcher) ----

async def _transcribe_local(path: str, rid: str) -> list[dict]:
    cache = TRANSCRIPTS_DIR / f"{rid}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    key = main.ASSEMBLY_KEY          # main.py:2580 — env var is ASSEMBLYAI_KEY
    if not key:
        raise RuntimeError("ASSEMBLYAI_KEY missing")
    async with httpx.AsyncClient(timeout=300) as cl:
        up = await cl.post("https://api.assemblyai.com/v2/upload",
                           headers={"authorization": key},
                           content=Path(path).read_bytes())
        up.raise_for_status()
        url = up.json()["upload_url"]
        sub = await cl.post("https://api.assemblyai.com/v2/transcript",
                            headers={"authorization": key},
                            json={"audio_url": url, "disfluencies": True})
        sub.raise_for_status()
        tid = sub.json()["id"]
        for _ in range(120):
            await asyncio.sleep(3)
            st = await cl.get(f"https://api.assemblyai.com/v2/transcript/{tid}",
                              headers={"authorization": key})
            j = st.json()
            if j.get("status") == "completed":
                words = [{"word": w.get("text", ""), "start_ms": w.get("start", 0),
                          "end_ms": w.get("end", 0), "confidence": w.get("confidence")}
                         for w in j.get("words") or []]
                cache.write_text(json.dumps(words))
                return words
            if j.get("status") == "error":
                raise RuntimeError(f"assemblyai: {j.get('error')}")
    raise RuntimeError("assemblyai timeout")


# --- vision (Haiku; labels only, never geometry) ------------------------------

def _grab_frame(path: str, t: float, workdir: str, name: str, width: int = 512) -> str | None:
    out = str(Path(workdir) / f"{name}.jpg")
    r = subprocess.run(["ffmpeg", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", path,
                        "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", out],
                       capture_output=True, timeout=60)
    return out if r.returncode == 0 and Path(out).exists() else None


async def _haiku_json(prompt: str, image_paths: list[str]) -> dict | None:
    key = main.ANTHROPIC_API_KEY if hasattr(main, "ANTHROPIC_API_KEY") else None
    import os
    key = key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    content = []
    for p in image_paths[:10]:
        try:
            b64 = base64.b64encode(Path(p).read_bytes()).decode()
        except Exception:
            continue
        content.append({"type": "image", "source": {"type": "base64",
                        "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": prompt})
    try:
        async with httpx.AsyncClient(timeout=90) as cl:
            r = await cl.post("https://api.anthropic.com/v1/messages",
                              headers={"x-api-key": key,
                                       "anthropic-version": "2023-06-01"},
                              json={"model": HAIKU, "max_tokens": 700,
                                    "messages": [{"role": "user", "content": content}]})
            r.raise_for_status()
            txt = "".join(b.get("text", "") for b in r.json().get("content", []))
            s, e = txt.find("{"), txt.rfind("}")
            return json.loads(txt[s:e + 1]) if s >= 0 else None
    except Exception:
        return None


_yunet_det = None


def _face_in_jpg(jpg_path: str) -> bool:
    """Direct per-image YuNet check. detect_face_box() can't be used here — it
    ffmpeg-time-samples a VIDEO and requires >=2 frame hits, so a single still
    can never pass (smoke-test finding, face_ratio=0.00)."""
    global _yunet_det
    try:
        import cv2
    except ImportError:
        return False
    img = cv2.imread(jpg_path)
    if img is None:
        return False
    if _yunet_det is None:
        _yunet_det = cv2.FaceDetectorYN.create(faces_mod._MODEL_PATH, "", (480, 854), 0.6)
    h, w = img.shape[:2]
    _yunet_det.setInputSize((w, h))
    _, found = _yunet_det.detect(img)
    return found is not None and len(found) > 0


async def _classify_shots(path: str, cuts: list[float], duration: float,
                          workdir: str) -> list[dict]:
    marks = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    shots = [{"t0": marks[i], "t1": marks[i + 1]} for i in range(len(marks) - 1)
             if marks[i + 1] - marks[i] > 0.08]
    def _grab_and_face(s):
        mid = (s["t0"] + s["t1"]) / 2
        frame = _grab_frame(path, mid, workdir, f"shot{int(mid * 100)}", width=480)
        face = False
        if frame:
            try:
                face = _face_in_jpg(frame)
            except Exception:
                pass
        return frame, face

    results = await asyncio.gather(*(asyncio.to_thread(_grab_and_face, s) for s in shots))
    for s, (frame, face) in zip(shots, results):
        s["_mid_frame"] = frame
        s["face_present"] = face
    # ONE Haiku call for ALL midframes (batched, indexed)
    frames = [s["_mid_frame"] for s in shots if s.get("_mid_frame")]
    labels = None
    if frames:
        idx = [i for i, s in enumerate(shots) if s.get("_mid_frame")]
        resp = await _haiku_json(
            "Each image is the midpoint frame of one shot from a short-form talking-head "
            "video, in order. For EACH image output one label:\n"
            "- face: a person talking to camera is the main subject\n"
            "- broll: cutaway footage/photo (person may appear small/inset)\n"
            "- graphic: designed text card / title graphic dominates\n"
            "- screen: screen recording / app UI\n"
            'Reply JSON only: {"labels": ["face", ...]} with exactly '
            f"{len(frames)} entries.", frames)
        if resp and isinstance(resp.get("labels"), list) and len(resp["labels"]) == len(frames):
            labels = {}
            for j, i in enumerate(idx):
                labels[i] = str(resp["labels"][j])
    for i, s in enumerate(shots):
        haiku = (labels or {}).get(i, "face")
        s["label"] = broll_mod.label_shot(haiku, s["face_present"])
        s.pop("_mid_frame", None)
    return shots


# --- the per-reel pipeline ----------------------------------------------------

async def analyze_reel(entry: dict, cfg: StudyConfig,
                       local_path: str | None = None) -> dict | None:
    rid = entry["reel_id"]
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        try:
            # 1. download (skipped when a local path is supplied — gap analysis)
            path = local_path
            if not path:
                path = await asyncio.to_thread(
                    lambda: _download(entry.get("video_url_cdn") or "", td)
                    or _download(entry["permalink"], td))
            if not path:
                mark_stage(rid, "download", False, "no source reachable")
                return None
            mark_stage(rid, "download", True)

            # 2+4. scene cuts first (cheap) then the TH hard gate needs faces+words
            duration, cuts = await asyncio.to_thread(scene_cuts, path, cfg.scene_threshold)
            if duration <= 0:
                mark_stage(rid, "scenes", False, "ffprobe duration 0")
                return None
            mark_stage(rid, "scenes", True)

            # 3. transcript
            try:
                words = await _transcribe_local(path, rid)
                mark_stage(rid, "transcript", True)
            except Exception as e:
                words = []
                failures.append(f"transcript: {e}")
                mark_stage(rid, "transcript", False, str(e))

            # 5. shot classification (YuNet × Haiku)
            try:
                shots = await _classify_shots(path, cuts, duration, td)
                mark_stage(rid, "shots", True)
            except Exception as e:
                shots = []
                failures.append(f"shots: {e}")
                mark_stage(rid, "shots", False, str(e))

            # Tiered gate (CP-3 finding: a per-SHOT face ratio excluded 64% of the
            # corpus — talking-head reels WITH dense b-roll legitimately show the
            # face in a minority of shots). Duration-weighted face time instead:
            #   th           face_time >= 30%  -> all metrics
            #   spoken_broll words >= 12       -> caption/CTA/title metrics only
            #                                     (their b-roll norms are a
            #                                      different format — excluded
            #                                      from b-roll aggregates)
            #   else         excluded (true montage / no speech)
            tier = "th"
            if not local_path and shots:
                face_t = sum(s["t1"] - s["t0"] for s in shots if s["face_present"])
                face_time_ratio = face_t / duration if duration else 0.0
                if face_time_ratio >= 0.30 and len(words) >= TH_MIN_WORDS:
                    tier = "th"
                elif len(words) >= TH_MIN_WORDS:
                    tier = "spoken_broll"
                else:
                    mark_stage(rid, "th_gate", False,
                               f"face_time={face_time_ratio:.2f} words={len(words)}")
                    return {"excluded": "not_th", "reel_id": rid,
                            "face_time_ratio": round(face_time_ratio, 2),
                            "n_words": len(words)}
                mark_stage(rid, "th_gate", True)

            # 6-7. OCR caption track + chunks + speech alignment
            captions: dict = {"present": False, "chunks": []}
            band = None
            frames: list = []
            try:
                engine = ocr_track.get_engine(cfg.ocr_engine)
                frames = await asyncio.to_thread(
                    ocr_track.read_caption_track, path, cfg.ocr_fps, engine)
                (OCR_DIR / f"{rid}.json").write_text(json.dumps(
                    [{"t": f.t, "lines": f.lines} for f in frames]))
                band = ocr_track.caption_band(frames)
                chunks = ocr_track.chunk_caption_track(frames, cfg.ocr_fps, band)
                match_rate = ocr_track.align_captions_to_speech(chunks, words)
                spoken_s = (words[-1]["end_ms"] - words[0]["start_ms"]) / 1000 if words else 0
                covered = sum(c["t1"] - c["t0"] for c in chunks)
                wpcs = sorted(c["n_words"] for c in chunks)
                ycs = sorted(c["y_center"] for c in chunks)
                leads = sorted(c["lead_ms"] for c in chunks if c.get("lead_ms") is not None)
                captions = {
                    "present": len(chunks) >= 3,
                    "coverage_pct": round(min(1.0, covered / spoken_s), 2) if spoken_s else 0.0,
                    "chunks": chunks,
                    "words_per_chunk_median": wpcs[len(wpcs) // 2] if wpcs else None,
                    "y_center_median": round(ycs[len(ycs) // 2], 3) if ycs else None,
                    "pct_all_caps": round(sum(1 for c in chunks if c["case"] == "upper")
                                          / len(chunks), 2) if chunks else None,
                    "lead_ms_median": leads[len(leads) // 2] if leads else None,
                    "speech_match_rate": round(match_rate, 2),
                }
                mark_stage(rid, "ocr", True)
            except Exception as e:
                failures.append(f"ocr: {e}")
                mark_stage(rid, "ocr", False, str(e))

            # 8. vision style read (caption-band crops + first/last frames)
            style = None
            try:
                mids = [duration * f for f in (0.25, 0.5, 0.75)]
                _grabs = await asyncio.gather(*(
                    asyncio.to_thread(_grab_frame, path, tt, td, f"style{i}")
                    for i, tt in enumerate(mids)))
                imgs = [p for p in _grabs if p]
                first = await asyncio.to_thread(_grab_frame, path, 0.3, td, "first")
                last = await asyncio.to_thread(
                    _grab_frame, path, max(0.0, duration - 0.8), td, "last")
                imgs += [p for p in (first, last) if p]
                style = await _haiku_json(
                    "Frames from ONE short-form talking-head video (3 mid-video, then the "
                    "first ~0.3s frame, then a final-second frame). Describe the burned-in "
                    "caption STYLE and endings. JSON only:\n"
                    '{"karaoke_highlight": bool, "stroke": bool, "boxed": bool,'
                    ' "font_weight": "light|regular|heavy", "text_color": str,'
                    ' "highlight_color": str|null, "emoji_in_captions": bool,'
                    ' "has_title_card": bool, "title_card_text": str|null,'
                    ' "has_end_card": bool, "end_card_text": str|null,'
                    ' "broll_kinds": ["stock|screen|product|graphic|photo"],'
                    ' "content_type": "listicle|story|how_to|opinion|other"}', imgs)
                mark_stage(rid, "vision", style is not None,
                           "" if style else "no key or parse fail")
            except Exception as e:
                failures.append(f"vision: {e}")
                mark_stage(rid, "vision", False, str(e))

            # 9. title card + CTA
            tc_confirm = None
            if style is not None:
                tc_confirm = {"has_title_card": style.get("has_title_card"),
                              "text": style.get("title_card_text")}
            title_card = cards_mod.detect_title_card(frames, shots, band, tc_confirm)
            cta_confirm = None
            if style is not None:
                cta_confirm = {"has_end_card": style.get("has_end_card"),
                               "end_card_text": style.get("end_card_text")}
            cta = cards_mod.detect_cta(words, frames, band,
                                       entry.get("caption", ""), shots, duration,
                                       cta_confirm)

            # b-roll segments (title/CTA window graphics excluded from mid-reel merge)
            mid_shots = [s for s in shots
                         if not (s["label"] == "graphic" and
                                 (s["t0"] < cards_mod.TITLE_WINDOW_S or
                                  s["t1"] > duration - cards_mod.CTA_WINDOW_S))]
            segs = broll_mod.merge_segments(mid_shots, duration)
            if style and style.get("broll_kinds"):
                for s in segs:
                    if s["kind"] == "stock" and style["broll_kinds"]:
                        s["kind"] = style["broll_kinds"][0]

            wpm = None
            if words:
                spoken_min = (words[-1]["end_ms"] - words[0]["start_ms"]) / 60000
                wpm = round(len(words) / spoken_min) if spoken_min > 0 else None
            anatomy = {
                "schema_version": SCHEMA_VERSION,
                "tier": tier,
                "reel_id": rid, "platform": entry.get("platform"),
                "niche": entry.get("niche"), "permalink": entry.get("permalink"),
                "views": entry.get("views"), "likes": entry.get("likes"),
                "duration_s": round(duration, 2),
                "engines": {"ocr": cfg.ocr_engine, "vision_model": HAIKU,
                            "scene_threshold": cfg.scene_threshold,
                            "ocr_fps": cfg.ocr_fps, "analyzed_at": time.time()},
                "transcript": {"n_words": len(words), "wpm": wpm},
                "shots": [{k: (round(v, 2) if isinstance(v, float) else v)
                           for k, v in s.items()} for s in shots],
                "cut_stats": {"n_cuts": len(cuts),
                              "asl_s": round(duration / max(1, len(cuts) + 1), 2),
                              "cuts_per_30s": round(len(cuts) * 30 / duration, 1)},
                "captions": captions,
                "caption_style": {k: style.get(k) for k in
                                  ("karaoke_highlight", "stroke", "boxed", "font_weight",
                                   "text_color", "highlight_color", "emoji_in_captions")}
                                 if style else None,
                "content_type": (style or {}).get("content_type") or "other",
                "broll": broll_mod.segment_stats(segs, duration),
                "title_card": title_card,
                "cta": {**cta, "pattern": cards_mod.cta_pattern(cta)},
                "failures": failures,
            }
            ensure_dirs()
            (ANATOMY_DIR / f"{rid}.json").write_text(json.dumps(anatomy))
            return anatomy
        finally:
            pass  # tempdir teardown deletes the footage — analysis-only boundary


async def run(only: set[str] | None, resume: bool, concurrency: int) -> int:
    m = load_manifest()
    cfg = StudyConfig(**{k: v for k, v in m.get("config", {}).items()
                         if k in StudyConfig.__dataclass_fields__})
    cfg.platforms = tuple(cfg.platforms)
    sem = asyncio.Semaphore(concurrency)
    todo = [r for r in m["reels"]
            if (not only or r["reel_id"] in only)
            and r["role"] == "primary"
            and not (resume and r["status"] == "analyzed")]

    async def _one(entry: dict) -> None:
        async with sem:
            # File-level resume: an existing anatomy JSON is the durable ground
            # truth (the manifest only saves at END, so a killed run leaves
            # statuses stale while the per-reel work is already banked).
            done = ANATOMY_DIR / f"{entry['reel_id']}.json"
            if resume and done.exists():
                entry["status"] = "analyzed"
                return
            try:
                res = await analyze_reel(entry, cfg)
            except Exception as e:
                entry["status"] = f"failed:{type(e).__name__}"
                print(f"[anatomy] {entry['reel_id']} FAILED: {e}")
                return
            if res is None:
                entry["status"] = "failed:download"
            elif res.get("excluded"):
                entry["status"] = "excluded_not_th"
            else:
                entry["status"] = "analyzed"
            print(f"[anatomy] {entry['reel_id']}: {entry['status']}")

    await asyncio.gather(*(_one(r) for r in todo))
    # spares backfill: for each niche, analyze spares until primaries quota met
    for niche in {r["niche"] for r in m["reels"]}:
        good = [r for r in m["reels"] if r["niche"] == niche and r["status"] == "analyzed"]
        spares = [r for r in m["reels"] if r["niche"] == niche and r["role"] == "spare"
                  and r["status"] == "pending"]
        need = cfg.per_niche - len(good)
        for sp in spares[:max(0, need)]:
            await _one(sp)
    save_manifest(m)
    analyzed = sum(1 for r in m["reels"] if r["status"] == "analyzed")
    print(f"[anatomy] done: {analyzed} analyzed")
    return 0 if analyzed else 1


def audit() -> int:
    """CP-3: instrument-failure audit across anatomy JSONs."""
    files = sorted(ANATOMY_DIR.glob("*.json"))
    if not files:
        print("no anatomy files")
        return 1
    stage_fail: dict[str, int] = {}
    match_rates = []
    for f in files:
        a = json.loads(f.read_text())
        for msg in a.get("failures", []):
            stage_fail[msg.split(":")[0]] = stage_fail.get(msg.split(":")[0], 0) + 1
        mr = (a.get("captions") or {}).get("speech_match_rate")
        if mr is not None:
            match_rates.append(mr)
    n = len(files)
    print(f"{n} anatomy files")
    bad = False
    for stage, k in sorted(stage_fail.items(), key=lambda x: -x[1]):
        pct = k / n
        flag = " <-- INSTRUMENT BUG (>20%)" if pct > 0.2 else ""
        if pct > 0.2:
            bad = True
        print(f"  {stage}: {k}/{n} ({pct:.0%}){flag}")
    if match_rates:
        match_rates.sort()
        print(f"  ocr->speech match rate median {match_rates[len(match_rates)//2]:.2f} "
              f"(min {match_rates[0]:.2f})")
    return 1 if bad else 0


def main_cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "audit", "one"])
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--path", type=str, default="")
    ap.add_argument("--rid", type=str, default="")
    a = ap.parse_args()
    if a.cmd == "run":
        only = set(a.only.split(",")) if a.only else None
        sys.exit(asyncio.run(run(only, a.resume, a.concurrency)))
    if a.cmd == "one":
        entry = {"reel_id": a.rid or f"local_{Path(a.path).stem}", "platform": "local",
                 "niche": "ours", "permalink": a.path, "caption": ""}
        res = asyncio.run(analyze_reel(entry, StudyConfig(), local_path=a.path))
        print(json.dumps(res, indent=1)[:2000] if res else "FAILED")
        sys.exit(0 if res else 1)
    sys.exit(audit())


if __name__ == "__main__":
    main_cli()
