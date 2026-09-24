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
