"""Regression: _rehost_media must actually upload.

From 2026-08-22 to 2026-09-23 every reel rehost failed silently: the disk-streamed
upload body was a SYNC generator, httpx.AsyncClient raised "Attempted to send an sync
request with an AsyncClient instance", the broad except swallowed it, and reels were
served with video_url='' once their raw CDN links aged out. The keyless suite never
reached the upload (no Supabase key → early return), so these tests drive the real
code path through an in-process transport.
"""
import asyncio

import httpx
import pytest

import main


def _patched_client(monkeypatch, handler):
    real = httpx.AsyncClient

    class _Client(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(main.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(main, "SUPABASE_URL", "https://sb.example.co")
    monkeypatch.setattr(main, "SUPABASE_KEY", "service-key")


def test_rehost_uploads_streamed_body_and_returns_public_url(monkeypatch):
    payload = b"\x00\x01" * 150_000          # 300KB "video"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=payload)
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        seen["upsert"] = request.headers.get("x-upsert")
        return httpx.Response(200, json={"Key": "ok"})

    _patched_client(monkeypatch, handler)
    out = asyncio.run(main._rehost_media("https://cdn.example/v.mp4", "reels/abc.mp4",
                                         "video/mp4", 25_000_000, min_bytes=100_000))
    assert out == f"https://sb.example.co/storage/v1/object/public/{main.SUPABASE_STORAGE_BUCKET}/reels/abc.mp4"
    assert seen["body"] == payload            # the full streamed body reached storage
    assert seen["upsert"] == "true"
    assert seen["url"].endswith(f"/storage/v1/object/{main.SUPABASE_STORAGE_BUCKET}/reels/abc.mp4")


@pytest.mark.parametrize("status,size,max_bytes,min_bytes", [
    (403, 300_000, 25_000_000, 100_000),      # CDN refused
    (200, 50, 25_000_000, 100_000),           # degenerate payload under min_bytes
    (200, 300_000, 200_000, 0),               # over the size cap
])
def test_rehost_returns_none_without_uploading_on_bad_source(monkeypatch, status, size, max_bytes, min_bytes):
    uploads = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(status, content=b"x" * size)
        uploads.append(request)
        return httpx.Response(200)

    _patched_client(monkeypatch, handler)
    out = asyncio.run(main._rehost_media("https://cdn.example/v.mp4", "reels/x.mp4",
                                         "video/mp4", max_bytes, min_bytes=min_bytes))
    assert out is None and uploads == []


def test_rehost_reel_media_swaps_to_durable_urls(monkeypatch):
    """End to end through the per-post worker: thumbnail + (top-N) video become durable."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, content=b"y" * 200_000)
        return httpx.Response(200, json={})

    _patched_client(monkeypatch, handler)
    posts = [{"platform": "tiktok", "author": "a", "id": "1",
              "thumbnail_url": "https://cdn.example/t.jpg", "video_url": "https://cdn.example/v.mp4"}]
    asyncio.run(main._rehost_reel_media(posts))
    assert posts[0]["thumbnail_url"].startswith("https://sb.example.co/storage/v1/object/public/")
    assert posts[0]["video_url"].startswith("https://sb.example.co/storage/v1/object/public/")
