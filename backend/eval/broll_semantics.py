"""Ralph campaign grader: b-roll ↔ speech semantic alignment.

Judges what resolve-time scoring cannot: the PLACED window in the FINAL render
against the words ACTUALLY spoken while it is on screen. One HAIKU call per
placed item (describe-then-judge, the POPE-bias guard kept from
_broll_vision_score_one): frame extracted at the window midpoint + the spoken
text + the editor's cue → {shows, matches_speech, distracting, score}.

Keyless → returns [] (fail-soft, same convention as the pipeline's vision
steps). ~60-80 HAIKU calls ≈ $0.10-0.20 per 10-video round.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil

import httpx

from app.edl import ms_to_frame
from eval.campaign_common import FPS, clip_out_len, finding

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
# Round-4: HAIKU-as-judge produced harshness noise (score 15 for a hand
# stirring gochujang under a literal gochujang line). The GRADER is the
# arbiter of P0s — it gets the stronger model; resolve-time keeps HAIKU.
_JUDGE = "claude-sonnet-5"
_SEM = asyncio.Semaphore(4)


def words_in_output_window(plan: dict, words: list[dict],
                           frame_in: int, frame_out: int, pad_f: int = 15) -> str:
    """Words whose (output-mapped) start falls inside [frame_in-pad, frame_out+pad]."""
    lo, hi = frame_in - pad_f, frame_out + pad_f
    picked: list[tuple[int, str]] = []
    out_start = 0
    for c in plan.get("clips") or []:
        ol = clip_out_len(c)
        speed = c.get("speed") or 1.0
        for w in words or []:
            sf = ms_to_frame(w.get("start_ms", 0))
            if c["src_in"] <= sf < c["src_out"]:
                of = out_start + round((sf - c["src_in"]) / speed)
                if lo <= of <= hi:
                    picked.append((of, str(w.get("word", ""))))
        out_start += ol
    picked.sort()
    return " ".join(w for _, w in picked)


# Inset rects per mode (normalized) — mirror of eval/layout_qc geometry. For
# non-full modes the render frame is MOSTLY the speaker; judging the whole frame
# flags every card/panel as "wrong subject" (smoke-test false positive). Crop to
# the inset so the judge sees the CUTAWAY content, not the face around it.
_PANEL_RECT = (40 / 1080, 130 / 1920, 1000 / 1080, (0.46 * 1920) / 1920)   # x,y,w,h
_CARD_RECT = ((1080 - 48 - 0.44 * 1080) / 1080, 220 / 1920, 0.44, (0.35 * 1920 * 0.72) / 1920)


def _crop_expr(item: dict) -> str | None:
    mode = item.get("mode") or "full"
    if mode == "full":
        return None
    if mode == "smart" and item.get("inset_rect"):
        r = item["inset_rect"]
        x, y, w, h = r["x"], r["y"], r["w"], r["h"]
    elif mode == "panel":
        x, y, w, h = _PANEL_RECT
    else:
        x, y, w, h = _CARD_RECT
    return f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f}"


async def _midpoint_frame(render_path: str, frame_in: int, frame_out: int,
                          item: dict | None = None) -> bytes | None:
    if shutil.which("ffmpeg") is None:
        return None
    t = ((frame_in + frame_out) / 2) / FPS
    vf = "scale=360:-1"
    crop = _crop_expr(item or {})
    if crop:
        vf = f"{crop},{vf}"
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", render_path,
        "-frames:v", "1", "-vf", vf, "-q:v", "5", "-f", "image2", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    return out or None


async def _judge_one(client: httpx.AsyncClient, key: str, spoken: str, cue: str,
                     thumb: bytes, is_meme: bool = False) -> dict | None:
    if is_meme:
        # Meme grammar: a reaction GIF is judged on COMEDIC-REACTION fit to the
        # line's emotional beat, never literal depiction of the nouns.
        step2 = (f"Step 2 — this is a MEME/reaction insert. The viewer HEARS: "
                 f"\"{spoken[:300]}\". matches_speech: does the image WORK as a comedic "
                 "reaction/emphasis to that line's emotional beat (exaggeration, irony, "
                 "recognition)? It does NOT need to depict the literal subject — but a "
                 "reaction that lands on the wrong emotion or is pure noise is false.\n")
    else:
        step2 = (f"Step 2 — the viewer HEARS this while it is on screen: \"{spoken[:300]}\". "
                 f"The editor's cue was: \"{cue[:120]}\". matches_speech: does the visual "
                 "depict/support what is being SAID in this window (not merely the cue)?\n")
    body = {
        "model": _JUDGE, "max_tokens": 200,
        "system": "You are a ruthless edit reviewer. A cutaway must support what is "
                  "being SAID while it is on screen — a related-but-different subject is a miss.",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text":
                "B-roll window audit.\n"
                "Step 1 — state what this image LITERALLY shows in 3-6 words (it is the CUTAWAY insert cropped from the video frame).\n"
                + step2 +
                "Step 3 — distracting: does the visual contradict or upstage the sentence?\n"
                "Step 4 — score 0-100. Wrong subject caps at 20; supports-speech-but-generic caps at 60."},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(thumb).decode("ascii")}}]}],
        "output_config": {"format": {"type": "json_schema", "schema": {
            "type": "object", "additionalProperties": False,
            "required": ["shows", "matches_speech", "distracting", "score"],
            "properties": {"shows": {"type": "string"},
                           "matches_speech": {"type": "boolean"},
                           "distracting": {"type": "boolean"},
                           "score": {"type": "integer"}}}}},
    }
    async with _SEM:
        r = await client.post(_ANTHROPIC_URL, json=body, timeout=60,
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"})
    if r.status_code != 200:
        return None
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    try:
        return json.loads(text)
    except ValueError:
        return None


async def grade(render_path: str, plan: dict, words: list[dict], *,
                video: str = "", job_id: str = "") -> list[dict]:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return []
    items = [b for b in (plan.get("broll") or []) if b.get("resolved_url")]
    if not items:
        return []
    findings: list[dict] = []

    async def one(b: dict) -> None:
        fi, fo = int(b.get("frame_in", 0)), int(b.get("frame_out", 0))
        spoken = words_in_output_window(plan, words, fi, fo)
        cue = b.get("cue_text") or ""
        thumb = await _midpoint_frame(render_path, fi, fo, b)
        if thumb is None:
            return
        async with httpx.AsyncClient() as client:
            v = await _judge_one(client, key, spoken, cue, thumb,
                                 is_meme=(b.get('need') == 'meme' or b.get('source') in ('giphy', 'klipy')))
        if v is None:
            return
        t = fi / FPS
        if not v.get("matches_speech") or v.get("score", 0) < 40:
            findings.append(finding(video, job_id, "broll_wrong_subject", t=t,
                evidence=f"shows '{v.get('shows','')}' while speech is '{spoken[:80]}' "
                         f"(score {v.get('score')}) — cue '{cue[:50]}'",
                source="broll_semantics", extra=v))
        elif v.get("distracting"):
            findings.append(finding(video, job_id, "broll_distracting", t=t,
                evidence=f"shows '{v.get('shows','')}' — judged distracting vs '{spoken[:80]}'",
                source="broll_semantics", extra=v))
        elif v.get("score", 100) < 60:
            findings.append(finding(video, job_id, "broll_generic", t=t,
                evidence=f"shows '{v.get('shows','')}' (score {v.get('score')}) — "
                         f"supports speech but generic", source="broll_semantics", extra=v))

    await asyncio.gather(*(one(b) for b in items))
    return findings
