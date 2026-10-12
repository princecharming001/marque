"""The Studio worker: the Marque API (``backend/``) hands it one-tap clip edits over a private HTTP API.

Why a separate service: the API runs on a 512 MiB box and sends rendering to Remotion Lambda; a standard-tier edit
needs several cores, gigabytes of memory, a full ffmpeg, MediaPipe and a headless Chromium for the caption overlay.
The worker owns all of that; the API only submits, polls and maps the result onto the job the app already polls.

API (every route but ``/healthz`` needs ``Authorization: Bearer $STUDIO_WORKER_TOKEN``):

* ``POST /jobs`` ``{job_id, clip_id, source_url, platform?, brief?}``: queue an edit. Idempotent per ``job_id``: a
  queued, running or finished job is returned as it is; a failed one is queued again (the API's retry).
* ``GET /jobs/{job_id}``: ``{status, stage, render_url, thumbnail_url, error, error_detail, revision, chat}``.
  ``status``: ``queued`` -> ``downloading`` -> ``editing`` -> ``rendering`` -> ``uploading`` -> ``ready`` | ``failed``.
* ``POST /jobs/{job_id}/chat`` ``{instruction, request_id}``: re-edit a ready job in place (one planner call and a
  re-render); ``chat.status`` goes ``queued`` -> ``running`` -> ``ready`` | ``failed`` and a ready chat bumps
  ``revision`` and ``render_url``.
* ``DELETE /jobs/{job_id}``: cancel a queued job (the API falls back to its own pipeline after a long queue wait).

Jobs run one at a time by default (``STUDIO_WORKER_CONCURRENCY``); state is a JSON file per job under
``STUDIO_WORKER_DATA`` so a poll survives a worker restart, but a job that was running when the worker restarted
is marked failed (``worker_restarted``) and the API falls back. Job folders older than ``STUDIO_WORKER_TTL_H``
(24 h, the API's own job TTL) are deleted.

Outputs: the final MP4 goes to the S3 bucket the API's Lambda renders already use (``REMOTION_BUCKET``, key
``studio/<job>/<clip>-r<revision>.mp4``; Supabase caps uploads at 50 MB), the cover to Supabase Storage
``marque-clips/posters/``, exactly where the API puts its own posters. Without credentials (local tests) the
files are served from ``/files/...`` on this worker instead.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import re
import shutil
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("studio.worker")

TERMINAL = ("ready", "failed", "cancelled")
#: The engine's stage lines (``studio: <stage>``) mapped onto the worker's coarse status.
_STAGE_STATUS = (
    ("ingest", "editing"), ("perception", "editing"), ("digest", "editing"), ("planner", "editing"),
    ("code plan", "editing"), ("render", "rendering"), ("qa", "rendering"), ("repair", "rendering"),
    ("deliver", "rendering"),
)
MAX_SOURCE_BYTES = 600 * 1024 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


@dataclass
class Config:
    data: Path
    token: str
    concurrency: int
    ttl_s: float
    bucket: str
    aws_key: str
    aws_secret: str
    aws_region: str
    supabase_url: str
    supabase_key: str
    supabase_bucket: str
    public_base: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        e = dict(os.environ if env is None else env)
        return cls(
            data=Path(e.get("STUDIO_WORKER_DATA") or "/data/studio-worker"),
            token=e.get("STUDIO_WORKER_TOKEN", ""),
            concurrency=max(1, int(e.get("STUDIO_WORKER_CONCURRENCY") or 1)),
            ttl_s=float(e.get("STUDIO_WORKER_TTL_H") or 24) * 3600,
            bucket=e.get("REMOTION_BUCKET", ""),
            aws_key=e.get("REMOTION_AWS_ACCESS_KEY_ID", ""),
            aws_secret=e.get("REMOTION_AWS_SECRET_ACCESS_KEY", ""),
            aws_region=e.get("REMOTION_AWS_REGION") or "us-east-1",
            supabase_url=(e.get("SUPABASE_URL") or "").rstrip("/"),
            supabase_key=e.get("SUPABASE_SERVICE_KEY") or e.get("SUPABASE_KEY") or "",
            supabase_bucket=e.get("SUPABASE_STORAGE_BUCKET") or "marque-clips",
            public_base=(e.get("STUDIO_WORKER_PUBLIC_URL") or "").rstrip("/"),
        )


# ============================================================================================ state
class Store:
    """One JSON file per job; every write is atomic (write + rename) and serialised by one lock."""

    def __init__(self, root: Path):
        self.root = root
        (root / "jobs").mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, job_id: str) -> Path:
        return self.root / "jobs" / f"{job_id}.json"

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            p = self._path(job_id)
            if not p.exists():
                return None
            return json.loads(p.read_text())

    def put(self, rec: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            rec["updated_at"] = time.time()
            p = self._path(rec["job_id"])
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(rec))
            tmp.replace(p)
            return rec

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            rec = self.get(job_id) or {"job_id": job_id}
            rec.update(fields)
            return self.put(rec)

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for p in (self.root / "jobs").glob("*.json"):
                with contextlib.suppress(Exception):
                    out.append(json.loads(p.read_text()))
            return out

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._path(job_id).unlink(missing_ok=True)


# ============================================================================================ uploads
class Uploader:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def video(self, path: Path, key: str) -> str:
        c = self.cfg
        if c.bucket and c.aws_key and c.aws_secret:
            import boto3

            s3 = boto3.client("s3", region_name=c.aws_region, aws_access_key_id=c.aws_key,
                              aws_secret_access_key=c.aws_secret)
            s3.upload_file(str(path), c.bucket, key, ExtraArgs={"ContentType": "video/mp4"})
            return f"https://{c.bucket}.s3.{c.aws_region}.amazonaws.com/{key}"
        return self._local(path, key)

    def poster(self, path: Path, key: str) -> str | None:
        c = self.cfg
        if c.supabase_url and c.supabase_key:
            r = httpx.post(f"{c.supabase_url}/storage/v1/object/{c.supabase_bucket}/{key}", content=path.read_bytes(),
                           headers={"Authorization": f"Bearer {c.supabase_key}", "apikey": c.supabase_key,
                                    "Content-Type": "image/jpeg", "x-upsert": "true"}, timeout=45)
            if 200 <= r.status_code < 300:
                return f"{c.supabase_url}/storage/v1/object/public/{c.supabase_bucket}/{key}"
            log.warning("poster upload %s -> HTTP %s", key, r.status_code)
            return None
        return self._local(path, key)

    def _local(self, path: Path, key: str) -> str:
        dest = self.cfg.data / "files" / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        return f"{self.cfg.public_base}/files/{key}"


# ============================================================================================ the engine
class Engine:
    """Runs edits and chats; tests replace :meth:`edit` and :meth:`chat` with fakes."""

    def edit(self, src: Path, *, platform: str, brief: str | None, out: Path, log_stage) -> dict[str, Any]:
        from studio.pipeline import edit

        res = edit(src, platform=platform, brief=brief, tier="standard", out=out, log=log_stage)
        final = res.finals.get(platform) or next(iter(res.finals.values()), None)
        if final is None:
            raise EngineError("render_no_output", res.stop_reason or "the edit delivered no video")
        cover = res.extras.get("cover")
        return {"final": Path(final), "cover": Path(cover) if cover else None, "job_dir": str(res.job_dir),
                "cost": res.estimated_cost, "invariants_passed": res.invariants_passed,
                "compute_seconds": res.compute_seconds}

    def chat(self, job_dir: str, instruction: str, *, platform: str, out: Path) -> dict[str, Any]:
        from studio.pipeline import chat

        res = chat(job_dir, instruction, out=out)
        final = res.finals.get(platform) or next(iter(res.finals.values()), None)
        if final is None:
            raise EngineError("chat_no_change", res.summary or "the instruction changed nothing")
        return {"final": Path(final), "summary": res.summary, "cost": res.estimated_cost}


class EngineError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _classify(e: BaseException) -> tuple[str, str]:
    """An error code the API can act on (all of them make the API fall back to its own pipeline)."""
    if isinstance(e, EngineError):
        return e.code, e.detail
    msg = f"{type(e).__name__}: {e}"
    low = msg.lower()
    if "limit" in low and ("min" in low or "source" in low):
        return "studio_rejected", msg[:400]
    if "no words" in low or "no speech" in low or "every word" in low:
        return "studio_no_speech", msg[:400]
    return "studio_error", msg[:400]


# ============================================================================================ the worker
class Worker:
    def __init__(self, cfg: Config, engine: Engine | None = None, uploader: Uploader | None = None):
        self.cfg = cfg
        self.store = Store(cfg.data)
        self.engine = engine or Engine()
        self.uploader = uploader or Uploader(cfg)
        self.q: queue.Queue[tuple[str, str, dict[str, Any]]] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._recover()

    # ---- lifecycle
    def start(self) -> None:
        for i in range(self.cfg.concurrency):
            t = threading.Thread(target=self._loop, name=f"studio-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for _ in self._threads:
            self.q.put(("stop", "", {}))

    def _recover(self) -> None:
        """A job (or chat) that was running when the worker stopped can't be resumed mid-render: mark it failed
        (the API falls back); queued work is queued again."""
        for rec in sorted(self.store.all(), key=lambda r: r.get("created_at", 0)):
            st = rec.get("status")
            if st == "queued":
                self.q.put(("edit", rec["job_id"], {}))
            elif st not in TERMINAL:
                self.store.update(rec["job_id"], status="failed", error="worker_restarted",
                                  error_detail="the worker restarted during this edit")
            chat = rec.get("chat") or {}
            if chat.get("status") == "queued":
                self.q.put(("chat", rec["job_id"], {}))
            elif chat.get("status") == "running":
                chat.update(status="failed", error="worker_restarted")
                self.store.update(rec["job_id"], chat=chat)

    # ---- API operations
    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        job_id, clip_id = str(body.get("job_id") or ""), str(body.get("clip_id") or "")
        if not _SAFE_ID.match(job_id) or not _SAFE_ID.match(clip_id):
            raise ValueError("job_id and clip_id must be 1-80 characters of letters, digits, '-' or '_'")
        url = str(body.get("source_url") or "")
        if not url.startswith(("https://", "http://")):
            raise ValueError("source_url must be an http(s) URL")
        existing = self.store.get(job_id)
        if existing and existing.get("status") not in ("failed", "cancelled"):
            return existing
        rec = {"job_id": job_id, "clip_id": clip_id, "source_url": url,
               "platform": str(body.get("platform") or "tiktok"), "brief": body.get("brief") or None,
               "status": "queued", "stage": "queued", "render_url": None, "thumbnail_url": None, "error": None,
               "error_detail": None, "revision": 0, "chat": None, "created_at": time.time(),
               "queued_at": time.time()}
        self.store.put(rec)
        self.q.put(("edit", job_id, {}))
        return rec

    def chat(self, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        rec = self.store.get(job_id)
        if rec is None:
            raise KeyError(job_id)
        if rec.get("status") != "ready" or not rec.get("job_dir"):
            raise RuntimeError("not_ready")
        if (rec.get("chat") or {}).get("status") in ("queued", "running"):
            raise RuntimeError("chat_in_progress")
        instruction = " ".join(str(body.get("instruction") or "").split())
        if not instruction:
            raise ValueError("instruction is empty")
        rec["chat"] = {"request_id": str(body.get("request_id") or ""), "instruction": instruction[:1000],
                       "status": "queued", "summary": None, "error": None, "queued_at": time.time()}
        self.store.put(rec)
        self.q.put(("chat", job_id, {}))
        return rec

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        rec = self.store.get(job_id)
        if rec and rec.get("status") == "queued":
            rec = self.store.update(job_id, status="cancelled", stage="cancelled")
        return rec

    # ---- the queue
    def _loop(self) -> None:
        while not self._stop.is_set():
            kind, job_id, _ = self.q.get()
            if kind == "stop":
                return
            try:
                self._prune()
                if kind == "edit":
                    self._run_edit(job_id)
                elif kind == "chat":
                    self._run_chat(job_id)
            except Exception:  # never let one job kill the worker thread
                log.error("worker loop crashed on %s %s:\n%s", kind, job_id, traceback.format_exc())

    def _run_edit(self, job_id: str) -> None:
        rec = self.store.get(job_id)
        if rec is None or rec.get("status") != "queued":
            return  # cancelled, or already handled
        root = self.cfg.data / "work" / job_id
        out = root / "out"
        try:
            self.store.update(job_id, status="downloading", stage="downloading", started_at=time.time())
            src = self._download(rec["source_url"], root)

            def stage(line: str) -> None:
                m = re.match(r"studio:\s*([a-z ]+)", line.strip().lower())
                if not m:
                    return
                head = m.group(1).strip()
                for prefix, status in _STAGE_STATUS:
                    if head.startswith(prefix):
                        self.store.update(job_id, status=status, stage=head)
                        return

            self.store.update(job_id, status="editing", stage="ingest")
            res = self.engine.edit(src, platform=rec["platform"], brief=rec.get("brief"), out=out, log_stage=stage)
            self.store.update(job_id, status="uploading", stage="uploading")
            url, thumb = self._publish(rec, res, revision=0)
            self.store.update(job_id, status="ready", stage="ready", render_url=url, thumbnail_url=thumb,
                              job_dir=res["job_dir"], cost=res.get("cost"), finished_at=time.time(),
                              invariants_passed=res.get("invariants_passed"),
                              compute_seconds=res.get("compute_seconds"))
            with contextlib.suppress(OSError):
                src.unlink()
        except Exception as e:
            code, detail = _classify(e)
            log.warning("edit %s failed: %s %s", job_id, code, detail)
            self.store.update(job_id, status="failed", stage="failed", error=code, error_detail=detail,
                              finished_at=time.time())

    def _run_chat(self, job_id: str) -> None:
        rec = self.store.get(job_id)
        chat = (rec or {}).get("chat") or {}
        if rec is None or chat.get("status") != "queued":
            return
        chat.update(status="running", started_at=time.time())
        self.store.update(job_id, chat=chat)
        try:
            rev = int(rec.get("revision") or 0) + 1
            out = self.cfg.data / "work" / job_id / f"chat-{rev}"
            res = self.engine.chat(rec["job_dir"], chat["instruction"], platform=rec["platform"], out=out)
            url, thumb = self._publish(rec, res, revision=rev)
            chat.update(status="ready", summary=res.get("summary"), finished_at=time.time())
            self.store.update(job_id, chat=chat, revision=rev, render_url=url,
                              thumbnail_url=thumb or rec.get("thumbnail_url"))
        except Exception as e:
            code, detail = _classify(e)
            chat.update(status="failed", error=code, error_detail=detail, finished_at=time.time())
            self.store.update(job_id, chat=chat)

    def _download(self, url: str, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("?")[0].rsplit("/", 1)[-1])[-80:] or "source.mov"
        dest = root / f"source-{name}"
        got = 0
        try:
            with httpx.stream("GET", url, timeout=httpx.Timeout(60, read=120), follow_redirects=True) as r:
                if r.status_code != 200:
                    raise EngineError("source_unreachable", f"HTTP {r.status_code} fetching the source")
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        got += len(chunk)
                        if got > MAX_SOURCE_BYTES:
                            raise EngineError("studio_rejected", "the source is larger than 600 MB")
                        f.write(chunk)
        except httpx.HTTPError as e:
            raise EngineError("source_unreachable", f"{type(e).__name__}: {e}") from e
        if got == 0:
            raise EngineError("source_unreachable", "the source is empty")
        return dest

    def _publish(self, rec: dict[str, Any], res: dict[str, Any], *, revision: int) -> tuple[str, str | None]:
        job_id, clip_id = rec["job_id"], rec["clip_id"]
        url = self.uploader.video(res["final"], f"studio/{job_id}/{clip_id}-r{revision}.mp4")
        thumb = None
        cover = res.get("cover")
        if cover and Path(cover).exists():
            with contextlib.suppress(Exception):
                thumb = self.uploader.poster(Path(cover), f"posters/{job_id}-{clip_id}.jpg")
        return url, thumb

    def _prune(self) -> None:
        cutoff = time.time() - self.cfg.ttl_s
        for rec in self.store.all():
            if rec.get("status") in TERMINAL and (rec.get("updated_at") or 0) < cutoff:
                shutil.rmtree(self.cfg.data / "work" / rec["job_id"], ignore_errors=True)
                jd = rec.get("job_dir")
                if jd and Path(jd).is_dir() and str(Path(jd)).startswith(str(self.cfg.data)):
                    shutil.rmtree(jd, ignore_errors=True)
                self.store.delete(rec["job_id"])


def public_view(rec: dict[str, Any]) -> dict[str, Any]:
    keys = ("job_id", "clip_id", "status", "stage", "render_url", "thumbnail_url", "error", "error_detail",
            "revision", "chat", "queued_at", "started_at", "finished_at", "cost", "invariants_passed",
            "compute_seconds")
    return {k: rec.get(k) for k in keys}


# ============================================================================================ HTTP
def create_app(worker: Worker | None = None, cfg: Config | None = None):
    from fastapi import Depends, FastAPI, Header, HTTPException
    from fastapi.responses import FileResponse

    cfg = cfg or (worker.cfg if worker else Config.from_env())
    if worker is None:
        if cfg.data.as_posix() not in ("", "/") and "STUDIO_WORK_DIR" not in os.environ:
            os.environ["STUDIO_WORK_DIR"] = str(cfg.data / "jobs-engine")
        worker = Worker(cfg)
    @contextlib.asynccontextmanager
    async def lifespan(_app):
        worker.start()
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(title="studio-worker", lifespan=lifespan)

    def auth(authorization: str = Header(default="")) -> None:
        if not cfg.token or authorization != f"Bearer {cfg.token}":
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "queued": worker.q.qsize()}

    @app.post("/jobs", dependencies=[Depends(auth)])
    def post_job(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return public_view(worker.submit(body))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
    def get_job(job_id: str) -> dict[str, Any]:
        rec = worker.store.get(job_id) if _SAFE_ID.match(job_id) else None
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return public_view(rec)

    @app.delete("/jobs/{job_id}", dependencies=[Depends(auth)])
    def delete_job(job_id: str) -> dict[str, Any]:
        rec = worker.cancel(job_id) if _SAFE_ID.match(job_id) else None
        if rec is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return public_view(rec)

    @app.post("/jobs/{job_id}/chat", dependencies=[Depends(auth)])
    def post_chat(job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        if not _SAFE_ID.match(job_id):
            raise HTTPException(status_code=404, detail="unknown job")
        try:
            return public_view(worker.chat(job_id, body))
        except KeyError as e:
            raise HTTPException(status_code=404, detail="unknown job") from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

    @app.get("/files/{path:path}")
    def files(path: str):
        root = (cfg.data / "files").resolve()
        p = (root / path).resolve()
        if not str(p).startswith(str(root) + os.sep) or not p.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(p)

    app.state.worker = worker
    return app


def main() -> None:  # pragma: no cover - the container entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    cfg = Config.from_env()
    if not cfg.token:
        raise SystemExit("STUDIO_WORKER_TOKEN is required")
    uvicorn.run(create_app(cfg=cfg), host="0.0.0.0", port=int(os.environ.get("PORT") or 8080))


if __name__ == "__main__":  # pragma: no cover
    main()
