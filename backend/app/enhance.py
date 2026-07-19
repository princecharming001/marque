"""WS1 (build 49) — speech enhancement via fal.ai DeepFilterNet3, KEYLESS-ARMED.

Runs only when the owner sets FAL_KEY (fal.ai dashboard → API keys) in the Render env —
until then every entry point returns None and the pipeline behaves byte-identically.
Cost: $0.001/sec of audio (fal model page), and the caller gates on a measured SNR proxy
so it fires on the noisy minority of takes, not every job.

Flow (caller = main._finalize_audio_loudness): extract wav → host it (Supabase, so fal
can fetch a URL) → submit to the fal QUEUE API → poll → download the denoised audio.
The caller then remuxes onto the stream-copied video, re-probes SNR, and adopts the
result ONLY if it measurably improved — a denoiser must never be allowed to smear a
clean take. Every failure path returns None; enhancement is never load-bearing.
"""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

FAL_KEY = os.environ.get("FAL_KEY", "")
# fal queue API for the DeepFilterNet3 model (denoise + 48k upsample).
_FAL_SUBMIT_URL = "https://queue.fal.run/fal-ai/deepfilternet3"
_POLL_INTERVAL_S = 2.0
_DEADLINE_S = 120.0


def armed() -> bool:
    return bool(FAL_KEY)


async def denoise_audio_url(audio_url: str) -> bytes | None:
    """Submit a hosted audio file to DeepFilterNet3 and return the enhanced audio
    bytes, or None on any failure/keyless. Bounded by _DEADLINE_S end to end."""
    if not FAL_KEY or not audio_url:
        return None
    headers = {"Authorization": f"Key {FAL_KEY}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(_FAL_SUBMIT_URL, headers=headers,
                                  json={"audio_url": audio_url})
            if r.status_code not in (200, 201, 202):
                logging.warning("[enhance] fal submit failed: %s %s", r.status_code, r.text[:200])
                return None
            sub = r.json() or {}
            status_url = sub.get("status_url")
            response_url = sub.get("response_url")
            # Synchronous responses carry the result directly.
            result = sub if sub.get("audio") else None
            if not result and status_url and response_url:
                deadline = asyncio.get_event_loop().time() + _DEADLINE_S
                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(_POLL_INTERVAL_S)
                    s = await client.get(status_url, headers=headers)
                    if s.status_code != 200:
                        continue
                    if (s.json() or {}).get("status") == "COMPLETED":
                        rr = await client.get(response_url, headers=headers)
                        if rr.status_code == 200:
                            result = rr.json() or {}
                        break
            if not result:
                return None
            out = result.get("audio") or {}
            out_url = out.get("url") if isinstance(out, dict) else None
            if not out_url:
                return None
            dl = await client.get(out_url, timeout=60)
            if dl.status_code == 200 and dl.content:
                return dl.content
    except (httpx.HTTPError, ValueError, KeyError, OSError) as e:
        logging.warning("[enhance] fal denoise failed: %s", e)
    return None
