"""Suite-wide test hygiene.

main._resume_log (LV-26) is a deliberately PROCESS-WIDE rate limit on orphan resumes —
in production every GET poll and the liveness loop share it. In a test process that
means one test's resumes would silently defer another test's expected resume, so each
test starts with an empty window. Lazy on purpose: never imports main itself.
"""
import sys

import pytest


@pytest.fixture(autouse=True)
def _fresh_resume_rate_window():
    m = sys.modules.get("main")
    log = getattr(m, "_resume_log", None) if m is not None else None
    if log is not None:
        log.clear()
    yield


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Tests never reach a real host. Several tests set a vendor key ("test-key") without
    stubbing every call behind it, so the pipeline made REAL HTTPS requests (with an empty
    API key) to whatever ANTHROPIC_BASE_URL the shell had; on a stalled network each call
    retried for minutes and the full suite hung (QA 2026-10-13). The real transport now
    fails fast with ConnectError, which every vendor path already treats as "degrade".
    httpx.MockTransport (used by tests that fake a server) is unaffected."""
    import httpx

    async def _refuse(self, request):
        raise httpx.ConnectError(f"network disabled in tests: {request.url.host}", request=request)

    def _refuse_sync(self, request):
        raise httpx.ConnectError(f"network disabled in tests: {request.url.host}", request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _refuse)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse_sync)
    yield


@pytest.fixture(autouse=True)
def _drop_stale_background_tasks():
    """main._bg_tasks is process-wide. TestClient requests in an earlier test leave finished
    tasks bound to the client portal's (now closed) event loop; a later test that gathers
    main._bg_tasks then raised "The future belongs to a different loop" — the order-dependent
    test_editor_audit failures (QA 2026-10-13)."""
    m = sys.modules.get("main")
    tasks = getattr(m, "_bg_tasks", None) if m is not None else None
    if tasks:
        for t in list(tasks):
            try:
                stale = t.done() or t.get_loop().is_closed()
            except Exception:
                stale = True
            if stale:
                tasks.discard(t)
    yield
