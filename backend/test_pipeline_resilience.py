"""Pipeline resilience: transient failures at the no-retry hard-fail stages (source probe,
AssemblyAI submit + poll) must NOT permanently fail an otherwise-good edit. A rotated Remotion
secret must give a clean render_misconfigured, not an opaque bridge_error."""
from __future__ import annotations

import asyncio

import main


def _run(coro):
    return asyncio.run(coro)


async def _no_sleep(*_a, **_k):
    return None


def _patch_sleep(monkeypatch):
    """Make retry backoff instant (patch the sleep the module actually calls)."""
    monkeypatch.setattr(main.asyncio, "sleep", _no_sleep)


class _Resp:
    def __init__(self, status=200, payload=None, raise_json=False):
        self.status_code = status
        self._payload = payload or {}
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not json (transient 5xx HTML body)")
        return self._payload


def _client_factory(responses):
    """Return an httpx.AsyncClient stand-in whose POST/GET pop sequential responses.
    A response that is an Exception instance is raised (network blip)."""
    seq = list(responses)

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def _next(self, *a, **k):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        post = _next
        get = _next
        head = _next
    return lambda *a, **k: _Client()


# --- AssemblyAI submit retry -------------------------------------------------

def test_transcription_submit_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "k")
    _patch_sleep(monkeypatch)
    # 429, then 503, then 200-with-id → should return the id (not give up on the first blip)
    monkeypatch.setattr(main.httpx, "AsyncClient", _client_factory([
        _Resp(429), _Resp(503), _Resp(200, {"id": "tid-123"}),
    ]))
    assert _run(main._submit_transcription("https://x/v.mp4")) == "tid-123"


def test_transcription_submit_fails_fast_on_bad_key(monkeypatch):
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "k")
    calls = {"n": 0}
    def factory(*a, **k):
        calls["n"] += 1
        class _C:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **k): return _Resp(401)
        return _C()
    monkeypatch.setattr(main.httpx, "AsyncClient", factory)
    assert _run(main._submit_transcription("https://x/v.mp4")) is None
    assert calls["n"] == 1                      # 401 = bad key → no wasted retries


def test_transcription_submit_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "k")
    _patch_sleep(monkeypatch)
    monkeypatch.setattr(main.httpx, "AsyncClient", _client_factory([_Resp(503), _Resp(503), _Resp(503)]))
    assert _run(main._submit_transcription("https://x/v.mp4")) is None


# --- AssemblyAI poll tolerance ----------------------------------------------

def test_transcription_poll_tolerates_transient_blip(monkeypatch):
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "k")
    _patch_sleep(monkeypatch)
    # network error, then a 5xx-HTML (json raises), then completed — must NOT crash the loop
    monkeypatch.setattr(main.httpx, "AsyncClient", _client_factory([
        main.httpx.ConnectError("reset"),
        _Resp(503, raise_json=True),
        _Resp(200, {"status": "completed",
                    "words": [{"text": "hi", "start": 0, "end": 300, "confidence": 1.0}],
                    "auto_highlights_result": {"results": []}}),
    ]))
    out = _run(main._poll_transcription("tid", max_wait_s=60))
    assert out["words"] and out["words"][0]["word"] == "hi"


def test_transcription_poll_gives_up_after_sustained_failure(monkeypatch):
    import pytest
    monkeypatch.setattr(main, "ASSEMBLY_KEY", "k")
    _patch_sleep(monkeypatch)
    monkeypatch.setattr(main.httpx, "AsyncClient",
                        _client_factory([main.httpx.ConnectError("reset")] * 8))
    with pytest.raises(main.PipelineError) as ei:
        _run(main._poll_transcription("tid", max_wait_s=60))
    assert ei.value.code == "transcribe_failed"


# --- Source-URL probe retry --------------------------------------------------

def test_source_probe_retries_transient_then_ok(monkeypatch):
    _patch_sleep(monkeypatch)
    monkeypatch.setattr(main.httpx, "AsyncClient", _client_factory([
        main.httpx.ConnectError("blip"), _Resp(503), _Resp(200),
    ]))
    _run(main._validate_source_url("https://cdn/v.mp4"))   # returns without raising


def test_source_probe_fails_fast_on_404(monkeypatch):
    import pytest
    calls = {"n": 0}
    def factory(*a, **k):
        calls["n"] += 1
        class _C:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def head(self, *a, **k): return _Resp(404)
        return _C()
    monkeypatch.setattr(main.httpx, "AsyncClient", factory)
    with pytest.raises(main.PipelineError) as ei:
        _run(main._validate_source_url("https://cdn/gone.mp4"))
    assert ei.value.code == "source_unreachable" and calls["n"] == 1   # real 404 → no retries
