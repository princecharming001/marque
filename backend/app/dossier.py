"""Video understanding layer — the Twelve Labs / Claude-frames dossier (Phase 1).

`generate_dossier(source_url, duration_ms)` returns a structured visual dossier (or None),
choosing a provider by env `VIDEO_UNDERSTANDING` (twelvelabs | claude_frames | off) and
failing DOWN the chain twelvelabs → claude_frames → none per the repo's fail-soft doctrine
(a missing key / timeout / error never breaks the edit — it just yields a thinner or absent
dossier and the transcript-only path still runs).

All timestamps are normalized to `[fN]` frame anchors (30fps, via `ms_to_frame`) — the same
convention as the transcript — so the fusion prompt cites frames uniformly.

Testability: every external boundary is a small module-level function the tests monkeypatch —
`_tl_request` (Twelve Labs HTTP), `_extract_keyframes` (ffmpeg), `_vision_json` (Claude
vision). Keyless, all three no-op to None so `generate_dossier` returns None cleanly.
"""
from __future__ import annotations

import base64
import json
import os
import asyncio
import logging

from app.edl import ms_to_frame

log = logging.getLogger("dossier")

DOSSIER_VERSION = "dossier-v1"
VIDEO_UNDERSTANDING = os.environ.get("VIDEO_UNDERSTANDING", "off")   # twelvelabs|claude_frames|claude_frames_sparse|off
TWELVELABS_KEY = os.environ.get("TWELVELABS_KEY", "")
TWELVELABS_INDEX_ID = os.environ.get("TWELVELABS_INDEX_ID", "")
TWELVELABS_BASE = os.environ.get("TWELVELABS_BASE", "https://api.twelvelabs.io/v1.3")
DOSSIER_TIMEOUT_S = int(os.environ.get("DOSSIER_TIMEOUT_S", "240"))
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# The dossier the model is asked to produce (seconds in; we convert to frames on the way out).
# Kept permissive on the wire — we normalize + clamp ourselves rather than rely on the model.
_GENERATE_INSTRUCTIONS = (
    "You are a short-form video analyst. Watch the take and report ONLY what you can see, as JSON. "
    "Times are in SECONDS from the start. Fields: first_frame{desc, pattern_interrupt(bool), score(0-1)}, "
    "delivery_curve[{t0,t1,energy(0-1),note}], visual_events[{t0,t1,kind,desc}] where kind is one of "
    "gesture|prop|demo|framing_change|glance_away|flub_visual, scenes[{t0,t1,desc}], "
    "on_screen_text[{t0,t1,text}], framing{shot,eye_contact(bool),headroom_ok(bool),stability,lighting,"
    "quality_flags[]}, broll_visual_opportunities[{t0,t1,cue,why}], gaffes[{t0,t1,desc}]. "
    "Never invent detail you cannot see; empty arrays are valid."
)


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

async def generate_dossier(source_url: str, duration_ms: int,
                           provider: str | None = None) -> dict | None:
    """Produce a visual dossier for a take, or None. Fails down the provider chain."""
    provider = (provider or VIDEO_UNDERSTANDING or "off").lower()
    if provider == "off" or not source_url:
        return None

    if provider == "twelvelabs":
        try:
            d = await _twelvelabs_dossier(source_url, duration_ms)
            if d:
                return d
            log.info("dossier: twelvelabs yielded nothing, failing down to claude_frames")
        except Exception as e:
            log.warning("dossier: twelvelabs error (%s), failing down to claude_frames", e)
        provider = "claude_frames"   # fall through

    if provider == "claude_frames":
        try:
            return await _claude_frames_dossier(source_url, duration_ms)
        except Exception as e:
            log.warning("dossier: claude_frames error (%s) → no dossier", e)
            return None

    # WS4 (build 49): sparse decision-point sampling — scene-change frames (ffmpeg
    # scene filter) + first frame, cap 20, LONG EDGE ≤730px (~500 vision tokens/frame
    # on 9:16; a 720-WIDE portrait frame costs ~1,200 — the sizing is the cost lever).
    # Cheaper and better-targeted than uniform 0.5fps for the PLANNER use case: the
    # planner needs eyes at the moments something changes, not a flat filmstrip.
    if provider == "claude_frames_sparse":
        try:
            return await _claude_frames_sparse_dossier(source_url, duration_ms)
        except Exception as e:
            log.warning("dossier: claude_frames_sparse error (%s) → no dossier", e)
            return None

    return None


def _normalize(raw: dict, provider: str) -> dict:
    """Convert a model dossier (seconds) into the stored v1 shape (frame anchors)."""
    def f(sec) -> int:
        try:
            return ms_to_frame(int(float(sec) * 1000))
        except (TypeError, ValueError):
            return 0

    def span_list(items, extra_keys):
        out = []
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            row = {"f0": f(it.get("t0", 0)), "f1": f(it.get("t1", it.get("t0", 0)))}
            for k in extra_keys:
                row[k] = it.get(k)
            out.append(row)
        return out

    ff = raw.get("first_frame") or {}
    framing = raw.get("framing") or {}
    return {
        "version": DOSSIER_VERSION,
        "provider": provider,
        "first_frame": {
            "desc": ff.get("desc", ""),
            "pattern_interrupt": bool(ff.get("pattern_interrupt", False)),
            "score": float(ff.get("score") or 0.0),
        },
        "delivery_curve": span_list(raw.get("delivery_curve"), ["energy", "note"]),
        "visual_events": span_list(raw.get("visual_events"), ["kind", "desc"]),
        "scenes": span_list(raw.get("scenes"), ["desc"]),
        "on_screen_text": span_list(raw.get("on_screen_text"), ["text"]),
        "framing": {
            "shot": framing.get("shot", ""),
            "eye_contact": bool(framing.get("eye_contact", False)),
            "headroom_ok": bool(framing.get("headroom_ok", True)),
            "stability": framing.get("stability", ""),
            "lighting": framing.get("lighting", ""),
            "quality_flags": list(framing.get("quality_flags") or []),
        },
        "broll_visual_opportunities": span_list(raw.get("broll_visual_opportunities"), ["cue", "why"]),
        "gaffes": span_list(raw.get("gaffes"), ["desc"]),
    }


# ---------------------------------------------------------------------------
# Twelve Labs path
# ---------------------------------------------------------------------------

async def _tl_request(method: str, path: str, *, json_body: dict | None = None,
                      files: dict | None = None, data: dict | None = None,
                      timeout: float = 30.0) -> dict:
    """The single Twelve Labs HTTP boundary (monkeypatched in tests)."""
    import httpx
    url = f"{TWELVELABS_BASE}{path}"
    headers = {"x-api-key": TWELVELABS_KEY}
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.request(method, url, headers=headers,
                                 json=json_body, files=files, data=data)
        r.raise_for_status()
        return r.json() if r.content else {}


async def _ensure_index() -> str | None:
    """Reuse TWELVELABS_INDEX_ID; else create the app index once (Pegasus + Marengo)."""
    if TWELVELABS_INDEX_ID:
        return TWELVELABS_INDEX_ID
    resp = await _tl_request("POST", "/indexes", json_body={
        "index_name": "marque-takes",
        "models": [{"model_name": "pegasus1.2", "model_options": ["visual", "audio"]},
                   {"model_name": "marengo2.7", "model_options": ["visual", "audio"]}],
    })
    return resp.get("_id") or resp.get("id")


async def _twelvelabs_dossier(source_url: str, duration_ms: int) -> dict | None:
    if not TWELVELABS_KEY:
        return None
    index_id = await _ensure_index()
    if not index_id:
        return None

    # 1. kick off indexing from the source URL
    task = await _tl_request("POST", "/tasks", data={"index_id": index_id, "video_url": source_url})
    task_id = task.get("_id") or task.get("id")
    if not task_id:
        return None

    # 2. poll until ready or DOSSIER_TIMEOUT_S
    video_id = await _poll_task(task_id)
    if not video_id:
        log.info("dossier: TL task %s not ready within %ss", task_id, DOSSIER_TIMEOUT_S)
        return None

    # 3. one Pegasus generate call with the dossier instructions
    gen = await _tl_request("POST", "/generate", json_body={
        "video_id": video_id, "prompt": _GENERATE_INSTRUCTIONS, "response_format": "json",
    }, timeout=90.0)
    raw = _coerce_json(gen.get("data") or gen.get("text") or gen)
    if not isinstance(raw, dict):
        return None
    return _normalize(raw, "twelvelabs")


async def _poll_task(task_id: str) -> str | None:
    """Poll a TL indexing task until ready; returns video_id or None on timeout/fail.
    Sleeps are injected via `_sleep` so tests run instantly."""
    waited = 0.0
    interval = 5.0
    while waited < DOSSIER_TIMEOUT_S:
        st = await _tl_request("GET", f"/tasks/{task_id}")
        status = (st.get("status") or "").lower()
        if status == "ready":
            return st.get("video_id") or st.get("_id") or st.get("id")
        if status in ("failed", "error"):
            return None
        await _sleep(interval)
        waited += interval
    return None


async def _sleep(sec: float) -> None:   # seam so tests don't actually wait
    await asyncio.sleep(sec)


def _coerce_json(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return None
    return None


# ---------------------------------------------------------------------------
# Claude-frames fallback
# ---------------------------------------------------------------------------

async def _extract_keyframes(source_url: str, duration_ms: int) -> list[tuple[int, bytes]]:
    """ffmpeg ~0.5fps keyframes + a full-res first frame → [(ms, jpeg_bytes)]. Fails soft
    to [] (no ffmpeg / unreadable). Boundary is monkeypatched in tests."""
    import shutil, tempfile, subprocess, glob
    if not shutil.which("ffmpeg"):
        return []
    frames: list[tuple[int, bytes]] = []
    with tempfile.TemporaryDirectory() as td:
        # first frame full-res
        first = os.path.join(td, "first.jpg")
        try:
            subprocess.run(["ffmpeg", "-y", "-i", source_url, "-frames:v", "1", "-q:v", "2", first],
                           capture_output=True, timeout=60)
            if os.path.exists(first):
                with open(first, "rb") as fh:
                    frames.append((0, fh.read()))
        except (subprocess.SubprocessError, OSError):
            pass
        # sampled keyframes at 0.5fps, downscaled
        pat = os.path.join(td, "kf_%04d.jpg")
        try:
            subprocess.run(["ffmpeg", "-y", "-i", source_url, "-vf", "fps=1/2,scale=512:-1",
                            "-q:v", "5", pat], capture_output=True, timeout=120)
        except (subprocess.SubprocessError, OSError):
            pass
        for i, p in enumerate(sorted(glob.glob(os.path.join(td, "kf_*.jpg")))):
            try:
                with open(p, "rb") as fh:
                    frames.append((int(i * 2000), fh.read()))   # 0.5fps → 2000ms apart
            except OSError:
                continue
    return frames


async def _vision_json(system: str, user: str, images: list[bytes], schema: dict) -> dict | None:
    """One Claude vision call over the sampled frames (monkeypatched in tests).
    Returns parsed JSON or None. Keyless → None."""
    if not ANTHROPIC_KEY or not images:
        return None
    import httpx
    content: list[dict] = [{"type": "text", "text": user}]
    for img in images[:20]:   # cap tokens
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.b64encode(img).decode("ascii")}})
    # NOTE: no `output_config` structured-output here — the dossier schema is intentionally
    # PERMISSIVE (additionalProperties:true, variable vision fields), which Anthropic's
    # strict json_schema mode rejects with a 400 (the claude_frames 400 storm that forced
    # VIDEO_UNDERSTANDING off). We instruct JSON-only + parse the text with _coerce_json,
    # which is exactly what a permissive vision extraction needs.
    system = system + " Return ONLY a single valid JSON object matching the requested fields — no prose, no markdown fences."
    body = {"model": os.environ.get("DOSSIER_VISION_MODEL", "claude-haiku-4-5-20251001"),
            "max_tokens": 2000, "system": system,
            "messages": [{"role": "user", "content": content}]}
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
                              headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"}, json=body)
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
    return _coerce_json(text)


async def _extract_sparse_frames(source_url: str, duration_ms: int,
                                 cap: int = 20) -> list[tuple[int, bytes]]:
    """WS4: decision-point frames — see `_extract_sparse_frames_sync`. Audit (build 53):
    this is three blocking ffmpeg subprocess.run calls (up to 60+120+120s) — running them
    directly on the event loop stalled every other coroutine (health checks, other jobs)
    for the duration. Offload to a worker thread."""
    return await asyncio.to_thread(_extract_sparse_frames_sync, source_url, duration_ms, cap)


def _extract_sparse_frames_sync(source_url: str, duration_ms: int,
                                cap: int = 20) -> list[tuple[int, bytes]]:
    """The full-res first frame + ffmpeg scene-change frames (select='gt(scene,0.3)'),
    long edge ≤730px, capped. Falls back to a coarse even sample when the scene filter
    finds nothing (a static talking head has few scene changes — that's fine, the planner
    mostly needs the open + a few mid anchors there). [] on any failure (caller fails the
    provider down). Blocking — call only via _extract_sparse_frames / asyncio.to_thread."""
    import shutil, tempfile, subprocess, glob
    if not shutil.which("ffmpeg"):
        return []
    scale = "scale=w='if(gte(iw,ih),730,-2)':h='if(gte(iw,ih),-2,730)'"
    frames: list[tuple[int, bytes]] = []
    with tempfile.TemporaryDirectory() as td:
        first = os.path.join(td, "first.jpg")
        try:
            subprocess.run(["ffmpeg", "-y", "-i", source_url, "-frames:v", "1",
                            "-vf", scale, "-q:v", "3", first],
                           capture_output=True, timeout=60)
            if os.path.exists(first):
                with open(first, "rb") as fh:
                    frames.append((0, fh.read()))
        except (subprocess.SubprocessError, OSError):
            pass
        pat = os.path.join(td, "sc_%04d.jpg")
        try:
            subprocess.run(["ffmpeg", "-y", "-i", source_url,
                            "-vf", f"select='gt(scene,0.3)',{scale}", "-vsync", "vfr",
                            "-q:v", "4", pat], capture_output=True, timeout=120)
        except (subprocess.SubprocessError, OSError):
            pass
        scene_paths = sorted(glob.glob(os.path.join(td, "sc_*.jpg")))
        if not scene_paths:
            # Static take: even anchors every ~5s instead.
            try:
                subprocess.run(["ffmpeg", "-y", "-i", source_url,
                                "-vf", f"fps=1/5,{scale}", "-q:v", "4", pat],
                               capture_output=True, timeout=120)
                scene_paths = sorted(glob.glob(os.path.join(td, "sc_*.jpg")))
            except (subprocess.SubprocessError, OSError):
                pass
        step = max(1, len(scene_paths) // max(1, cap - len(frames)))
        for i, p in enumerate(scene_paths[::step][:cap - len(frames)]):
            try:
                with open(p, "rb") as fh:
                    # Timestamps unknown for scene frames; approximate by even spread.
                    approx_ms = int((i + 1) * (duration_ms or 60000) / (len(scene_paths[::step][:cap]) + 1))
                    frames.append((approx_ms, fh.read()))
            except OSError:
                continue
    return frames


async def _claude_frames_sparse_dossier(source_url: str, duration_ms: int) -> dict | None:
    frames = await _extract_sparse_frames(source_url, duration_ms)
    if not frames:
        return None
    user = (f"{_GENERATE_INSTRUCTIONS}\n\nThe {len(frames)} images are DECISION-POINT frames "
            f"of a {duration_ms//1000}s take: the opening frame first, then frames where the "
            f"visual content changed. Times you report are approximate — anchor observations "
            f"to what you SEE, and focus on framing quality, gaffes, energy shifts, and any "
            f"on-screen text.")
    raw = await _vision_json("You analyze short-form video frames into a visual dossier.",
                             user, [b for _, b in frames], _CLAUDE_FRAMES_SCHEMA)
    if not isinstance(raw, dict):
        return None
    return _normalize(raw, "claude_frames_sparse")


async def _claude_frames_dossier(source_url: str, duration_ms: int) -> dict | None:
    frames = await _extract_keyframes(source_url, duration_ms)
    if not frames:
        return None
    user = (f"{_GENERATE_INSTRUCTIONS}\n\nThe {len(frames)} images are sampled ~0.5fps from a "
            f"{duration_ms//1000}s take (first image = the opening frame). Report the dossier JSON.")
    raw = await _vision_json("You analyze short-form video frames into a visual dossier.",
                             user, [b for _, b in frames], _CLAUDE_FRAMES_SCHEMA)
    if not isinstance(raw, dict):
        return None
    return _normalize(raw, "claude_frames")


# Permissive object schema for the vision structured-output call (seconds in).
_SPAN = {"type": "object", "additionalProperties": True, "properties": {
    "t0": {"type": "number"}, "t1": {"type": "number"}}}
_CLAUDE_FRAMES_SCHEMA = {
    "type": "object", "additionalProperties": True,
    "properties": {
        "first_frame": {"type": "object", "additionalProperties": True},
        "delivery_curve": {"type": "array", "items": _SPAN},
        "visual_events": {"type": "array", "items": _SPAN},
        "scenes": {"type": "array", "items": _SPAN},
        "on_screen_text": {"type": "array", "items": _SPAN},
        "framing": {"type": "object", "additionalProperties": True},
        "broll_visual_opportunities": {"type": "array", "items": _SPAN},
        "gaffes": {"type": "array", "items": _SPAN},
    },
}


# ---------------------------------------------------------------------------
# Mock (keyless pipeline path + tests)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Reference-reel patterns (P2.3) — run a trending reel through the SAME adapter,
# cached per URL (TL indexing latency is fine here: it's background/async), and
# distill MEASURED patterns the brief + edit-plan can be told to match.
# ---------------------------------------------------------------------------

_reference_dossier_cache: dict[str, dict | None] = {}


async def dossier_for_reference(url: str, duration_ms: int = 0) -> dict | None:
    """generate_dossier for a reference reel, cached per URL (one-time cost)."""
    if not url:
        return None
    if url in _reference_dossier_cache:
        return _reference_dossier_cache[url]
    d = await generate_dossier(url, duration_ms)
    _reference_dossier_cache[url] = d
    return d


def reference_patterns(dossier: dict | None, duration_ms: int = 0) -> dict | None:
    """Distill a reel's dossier into measured patterns (cut density, caption style,
    overlay usage, hook layers, energy curve). Pure function → keyless-testable."""
    if not dossier:
        return None
    spans = (dossier.get("scenes") or []) + (dossier.get("visual_events") or [])
    max_f = max((s.get("f1", 0) for s in spans), default=0)
    dur_f = ms_to_frame(duration_ms) if duration_ms else max_f
    dur_f = max(1, dur_f)
    cuts = len(dossier.get("scenes") or []) or len(dossier.get("visual_events") or [])
    cut_density = round(cuts / (dur_f / 30.0), 2) if dur_f else 0.0   # cuts per second
    ost = dossier.get("on_screen_text") or []
    curve = dossier.get("delivery_curve") or []
    energies = [c.get("energy") for c in curve if isinstance(c.get("energy"), (int, float))]
    ff = dossier.get("first_frame") or {}
    hook_layers = sum([
        bool(ff.get("pattern_interrupt")),                 # visual layer
        bool(ost),                                         # text overlay layer
        (energies[0] >= 0.6 if energies else False),       # energetic vocal open
    ])
    return {
        "cut_density_per_s": cut_density,
        "cuts": cuts,
        "caption_style": "heavy" if len(ost) >= 3 else ("some" if ost else "none"),
        "overlay_usage": len(dossier.get("visual_events") or []),
        "hook_layers": hook_layers,
        "hook_pattern_interrupt": bool(ff.get("pattern_interrupt")),
        "energy_open": energies[0] if energies else None,
        "energy_curve": [round(e, 2) for e in energies][:8],
    }


def mock_dossier(duration_ms: int) -> dict:
    """A deterministic dossier for the keyless mock pipeline (no vendor calls)."""
    return _normalize({
        "first_frame": {"desc": "creator centered, mid-shot, speaking to camera",
                        "pattern_interrupt": True, "score": 0.7},
        "delivery_curve": [{"t0": 0, "t1": 3, "energy": 0.8, "note": "strong open"},
                           {"t0": 3, "t1": max(4, duration_ms // 1000), "energy": 0.5, "note": "steady"}],
        "visual_events": [{"t0": 1.0, "t1": 1.5, "kind": "gesture", "desc": "hand emphasis on key word"}],
        "scenes": [{"t0": 0, "t1": max(1, duration_ms // 1000), "desc": "single indoor scene"}],
        "on_screen_text": [],
        "framing": {"shot": "mid", "eye_contact": True, "headroom_ok": True,
                    "stability": "handheld-stable", "lighting": "soft key", "quality_flags": []},
        "broll_visual_opportunities": [{"t0": 2.0, "t1": 4.0, "cue": "the result", "why": "concrete outcome named"}],
        "gaffes": [],
    }, "mock")
