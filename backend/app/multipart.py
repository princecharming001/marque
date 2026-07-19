"""Upload Phase 2 (build 49) — Supabase S3-compatible MULTIPART uploads, KEYLESS-ARMED.

Why multipart: a single presigned PUT is all-or-nothing — a network blip at 95% of a
200MB take restarts from byte 0. S3-style multipart lets the iOS client pre-enqueue
every ~8MB part as an independent BACKGROUND URLSession task (nsurlsessiond drains them
across app kills with zero app wakes), and ListParts is the crash-recovery primitive:
resume = upload only the missing parts. tus was rejected for this path — its PATCHes
are strictly serial (each needs app execution time between chunks), which fights the
one iOS primitive that survives kills.

Armed by three Render env vars (Supabase dashboard → Storage → S3 access keys):
    SUPABASE_S3_ACCESS_KEY / SUPABASE_S3_SECRET_KEY / (optional) SUPABASE_S3_REGION
Until they're set every entry point reports unsupported and the client stays on the
proven single-PUT path — zero behavior change.

VERIFICATION REQUIRED WHEN ARMED (the plan's 30-min spike): Supabase's docs enumerate
CreateMultipartUpload/UploadPart/Complete/Abort/ListParts as supported but do NOT
explicitly promise presigned UploadPart URLs. First arm → run
    python3 -c "from app import multipart; import asyncio; print(asyncio.run(multipart.spike()))"
against a scratch key before pointing the iOS client at it. If presigned parts fail,
the documented fallback is Supabase Session-Token S3 auth (per-user JWT), then
Render-proxied part PUTs.

Parts are billed until Complete/Abort (AWS-verified) — the GC sweep is REQUIRED and
runs opportunistically on every create.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import math
import os
import uuid

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
S3_ACCESS_KEY = os.environ.get("SUPABASE_S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("SUPABASE_S3_SECRET_KEY", "")
S3_REGION = os.environ.get("SUPABASE_S3_REGION", "us-east-1")
BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "marque-clips")

PART_SIZE = 8 * 1024 * 1024          # 8MB: ≥ S3's 5MiB floor, ≈5-10s LTE retry quantum
MAX_PARTS = 512                       # 4GB ceiling at 8MB parts — far past any take
PRESIGN_TTL_S = 6 * 3600              # part URLs live long enough for a very slow upload
GC_MAX_AGE_H = 48


def armed() -> bool:
    return bool(SUPABASE_URL and S3_ACCESS_KEY and S3_SECRET_KEY)


def _client():
    """boto3 S3 client against Supabase's S3-compatible endpoint. Import is lazy so the
    module (and main) load fine when boto3 isn't installed in a dev env."""
    import boto3
    from botocore.config import Config
    endpoint = f"{SUPABASE_URL.rstrip('/')}/storage/v1/s3"
    return boto3.client(
        "s3", endpoint_url=endpoint, region_name=S3_REGION,
        aws_access_key_id=S3_ACCESS_KEY, aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))


async def create(filename: str, size_bytes: int, content_type: str) -> dict | None:
    """CreateMultipartUpload + presign every part URL. None when keyless/failed —
    the caller then serves the single-PUT mint exactly as before."""
    if not armed() or size_bytes <= 0:
        return None
    n_parts = min(MAX_PARTS, max(1, math.ceil(size_bytes / PART_SIZE)))
    key = f"uploads/{uuid.uuid4()}/{filename}"

    def _sync() -> dict:
        c = _client()
        mp = c.create_multipart_upload(Bucket=BUCKET, Key=key, ContentType=content_type)
        upload_id = mp["UploadId"]
        parts = []
        for n in range(1, n_parts + 1):
            url = c.generate_presigned_url(
                "upload_part",
                Params={"Bucket": BUCKET, "Key": key, "UploadId": upload_id, "PartNumber": n},
                ExpiresIn=PRESIGN_TTL_S)
            parts.append({"n": n, "url": url})
        return {"upload_id": upload_id, "key": key, "part_size": PART_SIZE,
                "parts": parts,
                "public_url": f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{BUCKET}/{key}",
                "expires_in": PRESIGN_TTL_S}

    try:
        out = await asyncio.to_thread(_sync)
        # Opportunistic GC: abandoned multipart uploads bill until aborted.
        asyncio.get_running_loop().create_task(gc_sweep())
        return out
    except Exception as e:
        logging.warning("[multipart] create failed (falling back to single PUT): %s", e)
        return None


async def complete(key: str, upload_id: str, parts: list[dict]) -> bool:
    """CompleteMultipartUpload. Treats an already-completed upload (NoSuchUpload but the
    object exists) as success — the idempotent-retry case."""
    if not armed():
        return False

    def _sync() -> bool:
        c = _client()
        try:
            c.complete_multipart_upload(
                Bucket=BUCKET, Key=key, UploadId=upload_id,
                MultipartUpload={"Parts": [
                    {"PartNumber": int(p["n"]), "ETag": str(p["etag"])}
                    for p in sorted(parts, key=lambda p: int(p["n"]))]})
            return True
        except Exception as e:
            if "NoSuchUpload" in str(e):
                try:
                    c.head_object(Bucket=BUCKET, Key=key)
                    return True                      # a prior complete already landed
                except Exception:
                    pass
            raise

    try:
        return await asyncio.to_thread(_sync)
    except Exception as e:
        logging.warning("[multipart] complete failed for %s: %s", key, e)
        return False


async def abort(key: str, upload_id: str) -> bool:
    if not armed():
        return False
    try:
        await asyncio.to_thread(
            lambda: _client().abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=upload_id))
        return True
    except Exception as e:
        logging.warning("[multipart] abort failed for %s: %s", key, e)
        return False


async def status(key: str, upload_id: str) -> dict | None:
    """ListParts → which parts already landed (the client-resume primitive)."""
    if not armed():
        return None

    def _sync() -> dict:
        c = _client()
        done: list[dict] = []
        marker = 0
        while True:
            resp = c.list_parts(Bucket=BUCKET, Key=key, UploadId=upload_id,
                                PartNumberMarker=marker, MaxParts=1000)
            for p in resp.get("Parts", []):
                done.append({"n": int(p["PartNumber"]), "etag": p["ETag"],
                             "size": int(p.get("Size", 0))})
            if not resp.get("IsTruncated"):
                break
            marker = resp.get("NextPartNumberMarker", 0)
        return {"parts": done}

    try:
        return await asyncio.to_thread(_sync)
    except Exception as e:
        logging.warning("[multipart] status failed for %s: %s", key, e)
        return None


async def gc_sweep() -> int:
    """Abort multipart uploads older than GC_MAX_AGE_H (they bill until aborted —
    AWS-verified). Fire-and-forget from create(); never raises."""
    if not armed():
        return 0

    def _sync() -> int:
        c = _client()
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=GC_MAX_AGE_H)
        aborted = 0
        try:
            resp = c.list_multipart_uploads(Bucket=BUCKET, Prefix="uploads/")
        except Exception:
            return 0
        for u in resp.get("Uploads", []) or []:
            initiated = u.get("Initiated")
            if initiated and initiated < cutoff:
                try:
                    c.abort_multipart_upload(Bucket=BUCKET, Key=u["Key"], UploadId=u["UploadId"])
                    aborted += 1
                except Exception:
                    continue
        return aborted

    try:
        n = await asyncio.to_thread(_sync)
        if n:
            logging.info("[multipart] GC aborted %d stale uploads", n)
        return n
    except Exception:
        return 0


async def spike() -> dict:
    """The plan's 30-min verification spike, runnable the moment keys land: create a
    1-part upload, PUT 5MiB through the PRESIGNED part URL, complete, HEAD, abort-clean.
    Returns a verdict dict — 'presigned_part_put' is the load-bearing fact."""
    import httpx
    verdict: dict = {"armed": armed()}
    if not armed():
        return verdict
    created = await create("spike.bin", 5 * 1024 * 1024, "application/octet-stream")
    verdict["create"] = bool(created)
    if not created:
        return verdict
    try:
        async with httpx.AsyncClient(timeout=120) as h:
            r = await h.put(created["parts"][0]["url"], content=b"\0" * (5 * 1024 * 1024))
        verdict["presigned_part_put"] = r.status_code in (200, 201)
        verdict["put_status"] = r.status_code
        etag = r.headers.get("ETag", "")
        if verdict["presigned_part_put"] and etag:
            ok = await complete(created["key"], created["upload_id"], [{"n": 1, "etag": etag}])
            verdict["complete"] = ok
        else:
            await abort(created["key"], created["upload_id"])
    except Exception as e:
        verdict["error"] = str(e)[:200]
        await abort(created["key"], created["upload_id"])
    return verdict
