"""P4.1: b-roll multi-candidate + vision re-rank (keyless fail-soft)."""
from __future__ import annotations

import asyncio

import main


def _run(coro):
    return asyncio.run(coro)


def test_rerank_single_candidate_returns_it():
    cands = [{"link": "a.mp4", "thumb": None}]
    assert _run(main._rerank_broll("cue", cands)) == "a.mp4"


def test_rerank_empty_returns_none():
    assert _run(main._rerank_broll("cue", [])) is None


def test_rerank_keyless_falls_back_to_top1(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")
    cands = [{"link": "a.mp4", "thumb": "t1"}, {"link": "b.mp4", "thumb": "t2"}]
    assert _run(main._rerank_broll("cue", cands)) == "a.mp4"   # no key → top-1


def test_rerank_vision_pick_chooses_index(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")

    class _Resp:
        status_code = 200
        content = b"jpegbytes"
    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _Resp()
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: _Client())

    async def fake_pick(cue, thumbs, dossier):
        return 1   # pick the second candidate
    monkeypatch.setattr(main, "_broll_vision_pick", fake_pick)

    cands = [{"link": "a.mp4", "thumb": "t1"}, {"link": "b.mp4", "thumb": "t2"}]
    assert _run(main._rerank_broll("cue", cands)) == "b.mp4"


def test_rerank_vision_none_falls_back_top1(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")

    class _Resp:
        status_code = 200
        content = b"jpeg"
    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _Resp()
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: _Client())

    async def fake_pick(cue, thumbs, dossier):
        return None
    monkeypatch.setattr(main, "_broll_vision_pick", fake_pick)
    cands = [{"link": "a.mp4", "thumb": "t1"}, {"link": "b.mp4", "thumb": "t2"}]
    assert _run(main._rerank_broll("cue", cands)) == "a.mp4"


def test_fetch_candidates_keyless_empty(monkeypatch):
    monkeypatch.setattr(main, "PEXELS_KEY", "")
    assert _run(main._fetch_pexels_candidates("city", 6)) == []


def test_resolve_broll_uses_rerank(monkeypatch):
    monkeypatch.setattr(main, "PEXELS_KEY", "px")
    main._broll_url_cache.clear()

    async def fake_cands(query, n):
        return [{"link": "one.mp4", "thumb": "t1"}, {"link": "two.mp4", "thumb": "t2"}]
    async def fake_rerank(cue, cands, dossier):
        return "two.mp4"
    monkeypatch.setattr(main, "_fetch_pexels_candidates", fake_cands)
    monkeypatch.setattr(main, "_rerank_broll", fake_rerank)

    edl = {"broll": [{"broll_query": "city street", "cue_text": "the city", "source": "stock"}]}
    out = _run(main._resolve_broll(edl, dossier={"framing": {"lighting": "soft"}}))
    assert out["broll"][0]["resolved_url"] == "two.mp4"
