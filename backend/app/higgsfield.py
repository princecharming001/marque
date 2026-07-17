"""Higgsfield generative b-roll adapter (fail-soft, keyless no-op).

When Pexels has nothing for a b-roll cue, this generates a clip instead of silently
dropping the cutaway: Soul (text→image, 9:16) → DoP (image→video, ~5s) on the official
Higgsfield Platform API (https://docs.higgsfield.ai — base https://platform.higgsfield.ai,
auth header `Authorization: Key {key_id}:{key_secret}`, async submit → poll
/requests/{id}/status until completed|failed|nsfw).

Doctrine: same as every vendor adapter here — `CONFIGURED` gates everything; absent key /
timeout / any error returns None and the pipeline continues without the cutaway. Credits
cost real money, so callers must bound generations per job (see _resolve_broll's cap) and
cache results per query. Transport seams `_submit` / `_poll_request` are monkeypatchable.

Env: HIGGSFIELD_KEY="key_id:key_secret" (matches the SDK's HF_CREDENTIALS convention; the
split HIGGSFIELD_KEY_ID/HIGGSFIELD_KEY_SECRET pair also works), HIGGSFIELD_BASE,
HIGGSFIELD_TIMEOUT_S (whole-chain budget, default 150s — b-roll is a nicety, never let it
stall the edit), HIGGSFIELD_BROLL=0 to disable without removing the key.
"""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("higgsfield")

HIGGSFIELD_BASE = os.environ.get("HIGGSFIELD_BASE", "https://platform.higgsfield.ai")
_RAW_KEY = os.environ.get("HIGGSFIELD_KEY", "")
_KEY_ID = os.environ.get("HIGGSFIELD_KEY_ID", "") or (_RAW_KEY.split(":", 1)[0] if ":" in _RAW_KEY else "")
_KEY_SECRET = os.environ.get("HIGGSFIELD_KEY_SECRET", "") or (_RAW_KEY.split(":", 1)[1] if ":" in _RAW_KEY else "")
HIGGSFIELD_TIMEOUT_S = float(os.environ.get("HIGGSFIELD_TIMEOUT_S", "150"))
_ENABLED = os.environ.get("HIGGSFIELD_BROLL", "1").lower() not in ("0", "false", "no")
CONFIGURED = bool(_KEY_ID and _KEY_SECRET and _ENABLED)

_T2I_MODEL = os.environ.get("HIGGSFIELD_T2I_MODEL", "higgsfield-ai/soul/standard")
_I2V_MODEL = os.environ.get("HIGGSFIELD_I2V_MODEL", "higgsfield-ai/dop/standard")


def _auth_headers() -> dict:
    return {"Authorization": f"Key {_KEY_ID}:{_KEY_SECRET}", "Content-Type": "application/json"}


async def _submit(model_id: str, body: dict) -> str | None:
    """Submit a generation; returns the request id. Monkeypatched in tests."""
    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{HIGGSFIELD_BASE}/{model_id}", headers=_auth_headers(), json=body)
        if r.status_code >= 300:
            log.warning("higgsfield submit %s → %s %s", model_id, r.status_code, r.text[:150])
            return None
        data = r.json()
    return data.get("request_id") or data.get("id")


async def _poll_request(request_id: str, deadline: float) -> dict | None:
    """Poll a request to completion within `deadline` (monotonic). Monkeypatched in tests."""
    import httpx
    loop = asyncio.get_event_loop()
    async with httpx.AsyncClient(timeout=30) as client:
        while loop.time() < deadline:
            r = await client.get(f"{HIGGSFIELD_BASE}/requests/{request_id}/status",
                                 headers=_auth_headers())
            if r.status_code == 429 or r.status_code >= 500:
                await asyncio.sleep(6)           # transient — keep polling until the deadline
                continue
            if r.status_code >= 300:
                return None                       # genuine 4xx: the request is gone
            data = r.json()
            status = (data.get("status") or "").lower()
            if status == "completed":
                return data
            if status in ("failed", "nsfw", "canceled"):
                log.info("higgsfield request %s ended %s", request_id[:12], status)
                return None
            await asyncio.sleep(4)
    log.info("higgsfield request %s timed out", request_id[:12])
    return None


def _first_image_url(payload: dict) -> str | None:
    imgs = payload.get("images") or []
    if imgs and isinstance(imgs[0], dict):
        return imgs[0].get("url")
    return (payload.get("image") or {}).get("url") if isinstance(payload.get("image"), dict) else None


def _video_url(payload: dict) -> str | None:
    v = payload.get("video")
    if isinstance(v, dict):
        return v.get("url")
    vids = payload.get("videos") or []
    if vids and isinstance(vids[0], dict):
        return vids[0].get("url")
    return None


async def generate_still(cue: str) -> str | None:
    """v7 P2 still tier: Soul text→image ONLY (no DoP i2v step) — a 9:16 photoreal
    frame the render then Ken-Burns'es. This is the cheap default generated tier
    (one image call, no video generation): equivalent job to fal.ai Flux, on the
    Higgsfield key we already have. Returns an image URL or None; never raises.
    Caller MUST vision-gate the result before use (generation can miss too)."""
    if not CONFIGURED or not (cue or "").strip():
        return None
    loop = asyncio.get_event_loop()
    deadline = loop.time() + min(HIGGSFIELD_TIMEOUT_S, 60)   # a still shouldn't need 150s
    try:
        img_req = await _submit(_T2I_MODEL, {
            "prompt": f"{cue.strip()} — extreme closeup macro, warm natural side lighting, "
                      f"shallow depth of field, photorealistic, appetizing, no text, no watermark",
            "aspect_ratio": "9:16", "resolution": "720p"})
        if not img_req:
            return None
        img_done = await _poll_request(img_req, deadline)
        return _first_image_url(img_done or {})
    except Exception as e:
        log.warning("higgsfield generate_still failed: %s", e)
        return None


async def generate_broll(cue: str, duration_s: int = 5) -> str | None:
    """Generate one 9:16 b-roll clip for `cue`: Soul t2i → DoP i2v. Returns a playable
    mp4 URL or None (keyless / disabled / any failure / timeout). Never raises."""
    if not CONFIGURED or not (cue or "").strip():
        return None
    loop = asyncio.get_event_loop()
    deadline = loop.time() + HIGGSFIELD_TIMEOUT_S
    try:
        # 1) still frame in the reel's aspect
        img_req = await _submit(_T2I_MODEL, {
            "prompt": f"{cue.strip()} — cinematic b-roll frame, natural light, no text, no watermark",
            "aspect_ratio": "9:16", "resolution": "720p"})
        if not img_req:
            return None
        img_done = await _poll_request(img_req, deadline)
        image_url = _first_image_url(img_done or {})
        if not image_url:
            return None
        # 2) animate it — but never SUBMIT (and get billed) into a deadline that can't
        # possibly finish; ~30s is the floor for any DoP job to come back.
        if loop.time() > deadline - 30:
            log.info("higgsfield: deadline nearly exhausted after t2i — skipping i2v")
            return None
        vid_req = await _submit(_I2V_MODEL, {
            "image_url": image_url,
            "prompt": f"subtle cinematic motion, {cue.strip()}",
            "duration": max(3, min(10, int(duration_s)))})
        if not vid_req:
            return None
        vid_done = await _poll_request(vid_req, deadline)
        return _video_url(vid_done or {})
    except Exception as e:                       # transport / parse / anything — b-roll is a nicety
        log.warning("higgsfield generate_broll failed: %s", e)
        return None
