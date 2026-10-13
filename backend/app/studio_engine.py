"""Routing one-tap clip edits to the Studio worker (``studio/studio/server``), with the legacy pipeline as fallback.

Switches (env, read at import like ``palo_flags``):

* ``STUDIO_ENGINE``: ``off`` (default) | ``on`` | ``allowlist``. The kill switch: ``off`` sends every job down the
  legacy pipeline, and a job already routed to Studio falls back the moment Studio fails.
* ``STUDIO_ENGINE_ALLOWLIST``: comma-separated creator ids for ``allowlist`` mode.
* ``STUDIO_WORKER_URL`` / ``STUDIO_WORKER_TOKEN``: the worker (a Render private service) and its shared secret.
  Without both, nothing routes to Studio whatever ``STUDIO_ENGINE`` says.
* ``STUDIO_QUEUE_MAX_S`` (default 300): a job still waiting for a worker slot after this falls back.
* ``STUDIO_MAX_S`` (default 2400): a Studio edit running longer than this falls back.

Which jobs route: one-tap (``auto_confirm``) talking-head jobs only. Split screen / react jobs, the eval harness's
``edl_only`` jobs and analyze-first (brief review) jobs stay on the legacy pipeline, which owns those features.

The decision is stored on the job (``job["engine"]``) at create time so a resume after a restart takes the same
path; ``job["studio"]`` holds the worker's view (status, stage, revision) and ``job["engine_fallback"]`` the reason
a Studio job went back to legacy.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

MODE = (os.environ.get("STUDIO_ENGINE") or "off").strip().lower()
ALLOWLIST = {x.strip() for x in (os.environ.get("STUDIO_ENGINE_ALLOWLIST") or "").split(",") if x.strip()}
WORKER_URL = (os.environ.get("STUDIO_WORKER_URL") or "").rstrip("/")
WORKER_TOKEN = os.environ.get("STUDIO_WORKER_TOKEN") or ""
QUEUE_MAX_S = float(os.environ.get("STUDIO_QUEUE_MAX_S") or 300)
MAX_S = float(os.environ.get("STUDIO_MAX_S") or 2400)
POLL_S = float(os.environ.get("STUDIO_POLL_S") or 5)
#: The standard tier refuses sources over 10 minutes; longer ones never route.
MAX_SOURCE_S = 600.0


class StudioUnavailable(Exception):
    """Studio could not deliver this job; the caller falls back to the legacy pipeline."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


def configured() -> bool:
    return bool(WORKER_URL and WORKER_TOKEN)


def routing_enabled() -> bool:
    """The kill switch: False sends every job (new, resumed or retried) down the legacy path."""
    return MODE in ("on", "allowlist") and configured()


def choose_engine(*, creator_id: str | None, auto_confirm: bool, style: str | None, react_source_url: str | None,
                  config: dict[str, Any] | None, mode: str | None = None) -> str:
    """``"studio"`` or ``"legacy"`` for a new job (see the module docstring for the rules)."""
    mode = (mode or MODE).lower()
    if mode not in ("on", "allowlist") or not configured() or not auto_confirm:
        return "legacy"
    if str((config or {}).get("is_pro")) == "0":
        # Free tier: legacy renders carry the "powered by Yunicorn" watermark and Studio
        # renders don't have one yet, so free-tier jobs stay on legacy (revenue leak, QA
        # 2026-10-13). Older clients that send no is_pro are treated like legacy does: Pro.
        return "legacy"
    if react_source_url or (style or "") == "duet_split" or (config or {}).get("edl_only"):
        return "legacy"
    if mode == "allowlist" and (creator_id or "") not in ALLOWLIST:
        return "legacy"
    return "studio"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {WORKER_TOKEN}"}


async def _call(method: str, path: str, *, json: dict[str, Any] | None = None, timeout: float = 20) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.request(method, f"{WORKER_URL}{path}", json=json, headers=_headers())
    if r.status_code >= 400:
        raise StudioUnavailable(f"worker_http_{r.status_code}", r.text[:200])
    return r.json()


async def submit(job: dict) -> dict:
    clip = job["clips"][0]
    brief = " ".join(str(job.get("custom_instructions") or "").split()) or None
    try:
        return await _call("POST", "/jobs", json={
            "job_id": job["job_id"], "clip_id": clip["clip_id"], "source_url": job.get("source_url") or "",
            "platform": "tiktok", "brief": brief})
    except httpx.HTTPError as e:
        raise StudioUnavailable("worker_unreachable", f"{type(e).__name__}: {e}") from e


async def status(job_id: str) -> dict:
    try:
        return await _call("GET", f"/jobs/{job_id}")
    except httpx.HTTPError as e:
        raise StudioUnavailable("worker_unreachable", f"{type(e).__name__}: {e}") from e


async def cancel(job_id: str) -> None:
    try:
        await _call("DELETE", f"/jobs/{job_id}")
    except Exception as e:  # best effort: the worker skips a job nobody waits for anyway
        logging.info("[studio] cancel %s failed: %s", job_id, e)


async def run(job: dict, *, on_stage, owns) -> dict:
    """Submit (or re-attach to) the job's Studio edit and wait for it. ``on_stage(status)`` is called with the
    API job status to show ("processing" while queued, "editing", "rendering"); ``owns()`` returns False once a
    retry or the watchdog took the job over (then this returns quietly with ``{}``). Returns the worker's ready
    record (``render_url``, ``thumbnail_url``); raises :class:`StudioUnavailable` for anything else."""
    rec = await submit(job)
    t0 = time.monotonic()
    shown = None
    errors = 0
    while True:
        if not owns():
            return {}
        st = rec.get("status") or ""
        job["studio"] = {k: rec.get(k) for k in ("status", "stage", "revision", "error", "cost")}
        if st == "ready" and rec.get("render_url"):
            return rec
        if st in ("failed", "cancelled"):
            raise StudioUnavailable(rec.get("error") or "studio_failed", rec.get("error_detail") or "")
        want = "processing" if st in ("queued", "downloading") else ("rendering" if st in ("rendering", "uploading")
                                                                     else "editing")
        if want != shown:
            shown = want
            on_stage(want)
        elapsed = time.monotonic() - t0
        if st == "queued" and elapsed > QUEUE_MAX_S:
            await cancel(job["job_id"])
            raise StudioUnavailable("worker_busy", f"no worker slot within {int(QUEUE_MAX_S)} s")
        if elapsed > MAX_S:
            raise StudioUnavailable("studio_timeout", f"the edit ran past {int(MAX_S)} s")
        await asyncio.sleep(POLL_S)
        try:
            rec = await status(job["job_id"])
            errors = 0
        except StudioUnavailable:
            errors += 1
            if errors >= 6:  # about 30 s of a dead worker
                raise


async def chat(job: dict, clip: dict, instruction: str, *, request_id: str) -> dict:
    try:
        return await _call("POST", f"/jobs/{job['job_id']}/chat",
                           json={"instruction": instruction, "request_id": request_id})
    except httpx.HTTPError as e:
        raise StudioUnavailable("worker_unreachable", f"{type(e).__name__}: {e}") from e


async def wait_chat(job: dict, *, request_id: str, owns) -> dict:
    """Wait for a Studio chat re-edit; returns the worker record once the chat is ready or failed."""
    t0 = time.monotonic()
    errors = 0
    while time.monotonic() - t0 < MAX_S:
        if not owns():
            return {}
        await asyncio.sleep(POLL_S)
        try:
            rec = await status(job["job_id"])
            errors = 0
        except StudioUnavailable:
            errors += 1
            if errors >= 6:
                raise
            continue
        ch = rec.get("chat") or {}
        if ch.get("request_id") == request_id and ch.get("status") in ("ready", "failed"):
            return rec
    raise StudioUnavailable("studio_timeout", f"the re-edit ran past {int(MAX_S)} s")
