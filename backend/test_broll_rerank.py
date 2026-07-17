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


def _thumb_client():
    class _Resp:
        status_code = 200
        content = b"jpeg"
    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _Resp()
    return lambda *a, **k: _Client()


def test_rerank_vision_none_rejects_when_keyed(monkeypatch):
    # Realism pass: keyed vision failure/reject NO LONGER falls back to top-1 — it returns None
    # so the caller retries a simpler query and then degrades to a punch-in (never a wrong clip).
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    monkeypatch.setattr(main.httpx, "AsyncClient", _thumb_client())

    async def fake_pick(cue, thumbs, dossier):
        return None
    monkeypatch.setattr(main, "_broll_vision_pick", fake_pick)
    cands = [{"link": "a.mp4", "thumb": "t1"}, {"link": "b.mp4", "thumb": "t2"}]
    assert _run(main._rerank_broll("cue", cands)) is None


def test_rerank_vision_reject_returns_none(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    monkeypatch.setattr(main.httpx, "AsyncClient", _thumb_client())

    async def fake_pick(cue, thumbs, dossier):
        return -1                                    # judge rejected all as unrelated
    monkeypatch.setattr(main, "_broll_vision_pick", fake_pick)
    cands = [{"link": "a.mp4", "thumb": "t1"}, {"link": "b.mp4", "thumb": "t2"}]
    assert _run(main._rerank_broll("cue", cands)) is None


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


# --- Part 5.2: GIPHY meme source ------------------------------------------------------------

def test_fetch_giphy_keyless_empty(monkeypatch):
    monkeypatch.setattr(main, "GIPHY_KEY", "")
    assert _run(main._fetch_giphy_candidates("mind blown", 6)) == []


def test_giphy_prefers_mp4_rendition(monkeypatch):
    monkeypatch.setattr(main, "GIPHY_KEY", "gk")

    class _Resp:
        status_code = 200
        def json(self):
            return {"data": [{"images": {
                "original": {"mp4": "https://giphy/x.mp4", "url": "https://giphy/x.gif"},
                "original_still": {"url": "https://giphy/x_still.gif"}}}]}
    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None): return _Resp()
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: _Client())

    out = _run(main._fetch_giphy_candidates("mind blown", 3))
    assert out == [{"link": "https://giphy/x.mp4", "thumb": "https://giphy/x_still.gif",
                    "provider": "giphy"}]  # mp4 wins, not gif


def test_resolve_broll_routes_meme_to_giphy(monkeypatch):
    monkeypatch.setattr(main, "GIPHY_KEY", "gk")
    monkeypatch.setattr(main, "BROLL_MEMES", True)
    monkeypatch.setattr(main, "PEXELS_KEY", "")           # no stock — prove it used the GIF ladder
    main._broll_url_cache.clear()

    called = {"giphy": 0, "pexels": 0}
    async def fake_giphy(query, n):
        called["giphy"] += 1
        return [{"link": "meme.mp4", "thumb": "t"}]
    async def fake_pexels(query, n):
        called["pexels"] += 1
        return [{"link": "stock.mp4", "thumb": "t"}]
    async def fake_rerank(cue, cands, dossier):
        return cands[0]["link"] if cands else None
    monkeypatch.setattr(main, "_fetch_giphy_candidates", fake_giphy)
    monkeypatch.setattr(main, "_fetch_pexels_candidates", fake_pexels)
    monkeypatch.setattr(main, "_rerank_broll", fake_rerank)

    edl = {"broll": [{"broll_query": "side eye", "cue_text": "wait what", "need": "meme", "source": "stock"}]}
    out = _run(main._resolve_broll(edl))
    assert out["broll"][0]["resolved_url"] == "meme.mp4"
    assert out["broll"][0]["source"] == "giphy"
    assert called["giphy"] == 1 and called["pexels"] == 0   # memes never touch stock


def test_meme_unresolved_drops_not_stock_or_card(monkeypatch):
    monkeypatch.setattr(main, "GIPHY_KEY", "gk")
    monkeypatch.setattr(main, "BROLL_MEMES", True)
    monkeypatch.setattr(main, "PEXELS_KEY", "px")          # stock available…
    main._broll_url_cache.clear()

    async def empty_giphy(query, n):
        return []                                          # …but the GIF ladder finds nothing
    async def fake_pexels(query, n):
        return [{"link": "stock.mp4", "thumb": "t"}]
    async def fake_rerank(cue, cands, dossier):
        return cands[0]["link"] if cands else None
    async def empty_klipy(query, n, kind="clips"):
        return []
    monkeypatch.setattr(main, "_fetch_giphy_candidates", empty_giphy)
    monkeypatch.setattr(main, "_fetch_klipy_candidates", empty_klipy)
    monkeypatch.setattr(main, "_fetch_pexels_candidates", fake_pexels)
    monkeypatch.setattr(main, "_rerank_broll", fake_rerank)

    edl = {"broll": [{"broll_query": "side eye", "cue_text": "wait", "need": "meme",
                      "src_in": 100, "src_out": 160, "fallback_text": "wait", "source": "stock"}]}
    out = _run(main._resolve_broll(edl))
    assert out["broll"] == []                              # dropped — no stock stand-in
    assert not out.get("overlays")                         # and no text card
    assert any(e["action"] == "dropped" for e in out.get("_broll_log", []))


def test_giphy_mp4_only_no_gif_fallback(monkeypatch):
    # Realism pass: a GIPHY result with NO mp4 rendition is OMITTED (a raw .gif renders frozen
    # on Lambda — no candidate beats a frozen one). Only the mp4 result survives.
    monkeypatch.setattr(main, "GIPHY_KEY", "gk")

    class _Resp:
        status_code = 200
        def json(self):
            return {"data": [
                {"images": {"original": {"url": "https://giphy/gifonly.gif"},   # no mp4 → dropped
                            "original_still": {"url": "s1"}}},
                {"images": {"original": {"mp4": "https://giphy/ok.mp4", "url": "https://giphy/ok.gif"},
                            "original_still": {"url": "s2"}}},
            ]}
    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None): return _Resp()
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: _Client())

    out = _run(main._fetch_giphy_candidates("mind blown", 5))
    assert out == [{"link": "https://giphy/ok.mp4", "thumb": "s2", "provider": "giphy"}]   # gif-only omitted


def test_klipy_clips_flat_mp4_parse(monkeypatch):
    # KLIPY clips vertical: file.mp4 is a FLAT URL string (docs.klipy.com verified 2026-07-17);
    # items without an mp4 are omitted (frozen-.gif Lambda constraint).
    monkeypatch.setattr(main, "KLIPY_KEY", "kk")

    class _Resp:
        status_code = 200
        def json(self):
            return {"result": True, "data": {"data": [
                {"title": "no mp4", "file": {"gif": "https://k/x.gif", "webp": "https://k/x.webp"}},
                {"title": "ok", "file": {"mp4": "https://k/ok.mp4", "webp": "https://k/ok.webp"},
                 "file_meta": {"mp4": {"width": 720, "height": 1280}}},
            ]}}
    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            assert "/clips/search" in url and params["per_page"] >= 8   # per_page min 8
            return _Resp()
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: _Client())

    out = _run(main._fetch_klipy_candidates("side eye", 2, kind="clips"))
    assert out == [{"link": "https://k/ok.mp4", "thumb": "https://k/ok.webp", "provider": "klipy"}]


def test_klipy_gifs_nested_mp4_parse(monkeypatch):
    # KLIPY gifs vertical: file.{hd,md,sm}.mp4.url nested renditions; hd preferred.
    monkeypatch.setattr(main, "KLIPY_KEY", "kk")

    class _Resp:
        status_code = 200
        def json(self):
            return {"result": True, "data": {"data": [
                {"title": "g", "file": {
                    "hd": {"mp4": {"url": "https://k/hd.mp4"}, "webp": {"url": "https://k/hd.webp"}},
                    "md": {"mp4": {"url": "https://k/md.mp4"}}}},
            ]}}
    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            assert "/gifs/search" in url
            return _Resp()
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: _Client())

    out = _run(main._fetch_klipy_candidates("mind blown", 1, kind="gifs"))
    assert out == [{"link": "https://k/hd.mp4", "thumb": "https://k/hd.webp", "provider": "klipy"}]


def test_klipy_keyless_empty():
    # No KLIPY_KEY → fail-soft [] and the meme ladder is byte-identical GIPHY-only.
    assert _run(main._fetch_klipy_candidates("x", 3)) == []


def test_meme_ladder_klipy_first_giphy_fallback(monkeypatch):
    monkeypatch.setattr(main, "KLIPY_KEY", "kk")
    calls = {"klipy": 0, "giphy": 0}

    async def fake_klipy(query, n, kind="clips"):
        calls["klipy"] += 1
        return [{"link": f"https://k/{kind}.mp4", "thumb": "t", "provider": "klipy"}]
    async def fake_giphy(query, n):
        calls["giphy"] += 1
        return [{"link": "https://g/x.mp4", "thumb": "t", "provider": "giphy"}]
    monkeypatch.setattr(main, "_fetch_klipy_candidates", fake_klipy)
    monkeypatch.setattr(main, "_fetch_giphy_candidates", fake_giphy)

    out = _run(main._fetch_meme_candidates("side eye", 4))
    assert out and all(c["provider"] == "klipy" for c in out)
    assert calls["giphy"] == 0, "GIPHY only fires when KLIPY is empty"

    async def empty_klipy(query, n, kind="clips"):
        return []
    monkeypatch.setattr(main, "_fetch_klipy_candidates", empty_klipy)
    out2 = _run(main._fetch_meme_candidates("side eye", 4))
    assert out2 and all(c["provider"] == "giphy" for c in out2)


def test_meme_resolution_tags_klipy_source_and_caches_it(monkeypatch):
    monkeypatch.setattr(main, "KLIPY_KEY", "kk")
    monkeypatch.setattr(main, "BROLL_MEMES", True)
    main._broll_url_cache.clear()
    main._meme_source_cache.clear()

    async def fake_memes(query, n):
        return [{"link": "https://k/clip.mp4", "thumb": "t", "provider": "klipy"}]
    async def fake_rerank(cue, cands, dossier):
        return cands[0]["link"] if cands else None
    monkeypatch.setattr(main, "_fetch_meme_candidates", fake_memes)
    monkeypatch.setattr(main, "_rerank_broll", fake_rerank)

    edl = {"broll": [{"broll_query": "side eye", "cue_text": "wait", "need": "meme",
                      "src_in": 100, "src_out": 130, "fallback_text": "", "source": "stock"}]}
    out = _run(main._resolve_broll(edl))
    assert out["broll"][0]["source"] == "klipy"

    # Second resolve hits the cache — provenance must survive (tweak re-render path).
    async def boom(query, n):
        raise AssertionError("cache hit must not refetch")
    monkeypatch.setattr(main, "_fetch_meme_candidates", boom)
    edl2 = {"broll": [{"broll_query": "side eye", "cue_text": "wait", "need": "meme",
                       "src_in": 100, "src_out": 130, "fallback_text": "", "source": "stock"}]}
    out2 = _run(main._resolve_broll(edl2))
    assert out2["broll"][0]["source"] == "klipy"


def test_simplify_broll_query():
    assert main._simplify_broll_query("the barbell deadlift form is wrong") == "barbell deadlift form"
    assert main._simplify_broll_query("a big one") == ""            # all stopwords / short


def test_resolve_broll_retries_simplified_query(monkeypatch):
    # Vision rejects the (cinematic) query → retry ONCE with the short literal query; resolve on retry.
    monkeypatch.setattr(main, "PEXELS_KEY", "px")
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    main._broll_url_cache.clear(); main._broll_rejected.clear()
    fetches = []
    async def fake_fetch(q, n):
        fetches.append(q)
        return [{"link": "clip.mp4", "thumb": "t"}]
    calls = {"n": 0}
    async def fake_rerank(cue, cands, dossier):
        calls["n"] += 1
        return None if calls["n"] == 1 else "clip.mp4"   # reject first, accept the simplified retry
    monkeypatch.setattr(main, "_fetch_pexels_candidates", fake_fetch)
    monkeypatch.setattr(main, "_rerank_broll", fake_rerank)

    edl = {"broll": [{"broll_query": "founder typing in a dark neon office",
                      "cue_text": "the founder ships code", "need": "action",
                      "src_in": 100, "src_out": 190, "source": "stock"}]}
    out = _run(main._resolve_broll(edl))
    assert out["broll"] and out["broll"][0]["resolved_url"] == "clip.mp4"
    assert len(fetches) == 2                              # original + one simplified retry
    assert fetches[1] == "founder ships code"            # the literal fallback query


def test_unresolved_action_degrades_to_punch_in(monkeypatch):
    # action cue, vision rejects everything, retry also fails → face-keeping PUNCH-IN, not a wrong clip.
    monkeypatch.setattr(main, "PEXELS_KEY", "px")
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    main._broll_url_cache.clear(); main._broll_rejected.clear()
    async def fake_fetch(q, n):
        return [{"link": "wrong.mp4", "thumb": "t"}]
    async def always_reject(cue, cands, dossier):
        return None
    monkeypatch.setattr(main, "_fetch_pexels_candidates", fake_fetch)
    monkeypatch.setattr(main, "_rerank_broll", always_reject)

    edl = {"style": "broll_cutaway",
           "broll": [{"broll_query": "abstract momentum", "cue_text": "the whole process moves fast",
                      "need": "action", "src_in": 200, "src_out": 290, "source": "stock"}]}
    out = _run(main._resolve_broll(edl))
    assert out["broll"] == []                             # no wrong stock clip kept
    punch = [o for o in out.get("overlays", []) if o["type"] == "punch_in"]
    assert len(punch) == 1 and punch[0]["src_in"] == 200  # degraded to a punch-in at the cue
    assert any(e["action"] == "punch_in" for e in out["_broll_log"])
