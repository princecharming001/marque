"""Upload Phase 2 (build 49) — multipart keyless-armed contract."""
import asyncio

import main
from app import multipart


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_keyless_module_is_inert():
    assert not multipart.armed()
    assert _run(multipart.create("a.mov", 100_000_000, "video/quicktime")) is None
    assert _run(multipart.complete("k", "u", [])) is False
    assert _run(multipart.status("k", "u")) is None
    assert _run(multipart.gc_sweep()) == 0


def test_keyless_routes_report_unsupported():
    out = _run(main.create_multipart_upload(main.MultipartCreateRequest(
        filename="a.mov", size_bytes=100_000_000)))
    assert out == {"supported": False}


def test_armed_create_presigns_every_part(monkeypatch):
    monkeypatch.setattr(multipart, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(multipart, "S3_ACCESS_KEY", "ak")
    monkeypatch.setattr(multipart, "S3_SECRET_KEY", "sk")

    class FakeClient:
        def create_multipart_upload(self, **kw):
            assert kw["Key"].startswith("uploads/")
            return {"UploadId": "UP1"}
        def generate_presigned_url(self, op, Params=None, ExpiresIn=0):
            assert op == "upload_part"
            return f"https://signed/part{Params['PartNumber']}"
    monkeypatch.setattr(multipart, "_client", lambda: FakeClient())
    async def no_gc():
        return 0
    monkeypatch.setattr(multipart, "gc_sweep", no_gc)

    out = _run(multipart.create("a.mov", 20 * 1024 * 1024, "video/quicktime"))
    assert out["upload_id"] == "UP1"
    assert out["part_size"] == multipart.PART_SIZE
    assert [p["n"] for p in out["parts"]] == [1, 2, 3]          # ceil(20MB/8MB)
    assert out["public_url"].endswith(out["key"])


def test_complete_idempotent_on_nosuchupload(monkeypatch):
    monkeypatch.setattr(multipart, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(multipart, "S3_ACCESS_KEY", "ak")
    monkeypatch.setattr(multipart, "S3_SECRET_KEY", "sk")

    class FakeClient:
        def complete_multipart_upload(self, **kw):
            raise Exception("NoSuchUpload: gone")
        def head_object(self, **kw):
            return {"ContentLength": 1}                          # object already landed
    monkeypatch.setattr(multipart, "_client", lambda: FakeClient())
    assert _run(multipart.complete("k", "u", [{"n": 1, "etag": "e"}])) is True
