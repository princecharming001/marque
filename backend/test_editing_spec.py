"""Editing-quality spec regression tests — the correctness fixes for the two owner-
reported bugs (content silently cut; flubbed first take kept) plus spec-alignment
(sign-offs, framing cap, caption safe-zone). See the short-form talking-head spec."""
import app.edl as edl
import app.retention as ret


def _w(word, s, e, **kw):
    return {"word": word, "start_ms": s, "end_ms": e, "confidence": kw.get("conf", 0.99),
            "type": kw.get("type"), "is_emphasized": kw.get("emph", False)}


def _sentence(tokens, start_ms, wps=400):
    """Build a run of words at `wps` ms/word starting at start_ms."""
    out, t = [], start_ms
    for tok in tokens:
        out.append(_w(tok, t, t + wps - 60))
        t += wps
    return out


# ── Bug #1: keeps is no longer an exhaustive whitelist ───────────────────────

def test_omitted_content_is_kept_not_dropped():
    # 10s take; plan cuts ONE middle second and lists NOTHING in keeps. Everything
    # except the cut must survive (old behavior: only the empty keeps → whole take,
    # but a NON-empty keeps used to delete everything unlisted).
    words = _sentence(["a"] * 30, 0, wps=300)          # ~9s
    plan = {"cuts": [{"range": [30, 60], "reason": "ramble", "quote": "a a"}], "keeps": []}
    e = edl.assemble_edl(plan, words, "talking_head", "myth-buster")
    kept = edl._kept_intervals(e.segments if hasattr(e, "segments") else e["segments"],
                               e.drops if hasattr(e, "drops") else e["drops"]) \
        if False else None
    d = e.model_dump()
    total = edl.ms_to_frame(words[-1]["end_ms"])
    kept_frames = edl._kept_frames(d)
    # We cut ~30 frames out of ~total; the vast majority must remain (NOT collapsed).
    assert kept_frames > total * 0.7, f"kept {kept_frames}/{total} — content was silently dropped"


def test_keeps_whitelist_does_not_delete_unlisted():
    # A plan that lists a SMALL keeps range but no cuts must still keep the whole take
    # (keeps is protection now, never a whitelist).
    words = _sentence(["word"] * 40, 0, wps=300)
    plan = {"keeps": [[0, 30]], "cuts": []}
    d = edl.assemble_edl(plan, words, "talking_head", "myth-buster").model_dump()
    total = edl.ms_to_frame(words[-1]["end_ms"])
    assert edl._kept_frames(d) > total * 0.8


def test_keeps_protects_from_filler_trim():
    # A dead-air gap inside a protected keep range is NOT trimmed.
    words = _sentence(["one"] * 5, 0) + _sentence(["two"] * 5, 5000)   # 2s gap between
    protected = edl.assemble_edl({"keeps": [[0, 300]], "cuts": []}, words,
                                 "talking_head", "myth-buster").model_dump()
    unprotected = edl.assemble_edl({"keeps": [], "cuts": []}, words,
                                   "talking_head", "myth-buster").model_dump()
    # protection keeps at least as much as no-protection (the gap survives)
    assert edl._kept_frames(protected) >= edl._kept_frames(unprotected)


# ── Bug #2: retake dedup catches the flubbed FIRST take ──────────────────────

def test_retake_drops_flubbed_first_utterance():
    # Utterance A (flub) then a >500ms pause then utterance B (clean re-delivery of the
    # SAME line). The earlier take must be dropped even though it's the first utterance.
    a = _sentence(["the", "best", "way", "to", "start", "is", "this"], 0)
    b = _sentence(["the", "best", "way", "to", "start", "is", "this"], 6000)  # 6s gap
    words = a + b
    total = edl.ms_to_frame(words[-1]["end_ms"])
    e = {"segments": [{"src_in": 0, "src_out": total}], "drops": []}
    out = ret.dedupe_retakes(e, words)
    dropped = sum(d["src_out"] - d["src_in"] for d in out["drops"])
    assert dropped > 0, "flubbed first take was not dropped"
    # the drop should cover the EARLIER take (starts at frame 0)
    assert any(d["src_in"] <= edl.ms_to_frame(a[0]["start_ms"]) + 5 for d in out["drops"])


def test_retake_bridged_by_short_aside():
    # Take A, a short bridge ("ugh let me redo that"), then Take B (re-delivery).
    a = _sentence(["here", "is", "the", "one", "thing", "nobody", "tells", "you"], 0)
    bridge = _sentence(["ugh", "wait"], 4000)
    b = _sentence(["here", "is", "the", "one", "thing", "nobody", "tells", "you"], 6000)
    words = a + bridge + b
    total = edl.ms_to_frame(words[-1]["end_ms"])
    out = ret.dedupe_retakes({"segments": [{"src_in": 0, "src_out": total}], "drops": []}, words)
    assert sum(d["src_out"] - d["src_in"] for d in out["drops"]) > 0


def test_retake_similarity_containment():
    # containment: a fragment "the best way to" vs the full "the best way to start today"
    assert ret._shingle_sim(["the", "best", "way", "to"],
                            ["the", "best", "way", "to", "start", "today"]) >= ret._RETAKE_SIM


# ── Sign-offs (spec §9) ──────────────────────────────────────────────────────

def test_multiword_signoff_cut():
    words = _sentence(["this", "is", "the", "real", "point"], 0) + \
            _sentence(["hope", "this", "helped"], 3000)
    drops = edl.detect_disfluencies(words)
    last_start = edl.ms_to_frame(words[-3]["start_ms"])
    assert any(d.src_in <= last_start + 3 for d in drops), "sign-off not cut"


# ── Framing cap (spec §6.1 / Hard Constraint 7) ──────────────────────────────

def test_framing_scales_within_120():
    assert max(ret._FRAMING_SCALES.values()) <= 1.20, "framing breaches the 120% ceiling"
    assert ret._FRAMING_SCALES["mid"] <= 1.12


def test_combined_framing_punch_capped():
    # A close-framed segment (1.18) under a 1.12 punch would render ~1.32 — the final
    # clamp must lower the segment's tx_scale so the product stays <= 1.20.
    e = {"segments": [{"src_in": 0, "src_out": 300, "tx_scale": 1.18}],
         "overlays": [{"type": "punch_in", "src_in": 100, "src_out": 130, "scale": 1.12}]}
    out = ret._clamp_combined_scale(e)
    tx = out["segments"][0]["tx_scale"]
    assert tx * 1.12 <= 1.20 + 1e-6, f"combined {tx * 1.12:.3f} exceeds 120% cap"
    # a segment with no overlapping punch is untouched
    e2 = {"segments": [{"src_in": 0, "src_out": 300, "tx_scale": 1.18}],
          "overlays": [{"type": "punch_in", "src_in": 400, "src_out": 430, "scale": 1.12}]}
    assert ret._clamp_combined_scale(e2)["segments"][0]["tx_scale"] == 1.18


# ── Caption safe zone (spec §6.3) ────────────────────────────────────────────

def test_caption_default_in_safe_zone():
    words = _sentence(["hello"] * 10, 0)
    d = edl.assemble_edl({"cuts": [], "keeps": [], "caption_plan": {"style": "clean"}},
                         words, "talking_head", "myth-buster").model_dump()
    pos = (d.get("caption_options") or {}).get("pos_y")
    assert pos is not None and 0.55 <= pos <= 0.65, f"caption pos_y {pos} outside safe zone"


# ── Addendum Part 4A: b-roll selection (text card beats a wrong clip) ─────────
import asyncio
import main


def test_entity_need_no_ownmedia_becomes_text_card(monkeypatch):
    # An ENTITY need with only stock available must NOT show generic stock — it becomes a
    # text card. (No PEXELS/own-media configured → nothing resolves.)
    monkeypatch.setattr(main, "PEXELS_KEY", "")
    monkeypatch.setattr(main, "higgsfield_mod", type("H", (), {"CONFIGURED": False})())
    e = {"broll": [{"src_in": 200, "src_out": 260, "cue_text": "Notion app",
                    "broll_query": "notion app", "source": "stock",
                    "need": "entity", "fallback_text": "NOTION"}],
         "overlays": []}
    out = asyncio.run(main._resolve_broll(e))
    assert out["broll"] == []                                  # no wrong stock clip
    cards = [o for o in out["overlays"] if o["type"] == "text_card"]
    assert cards and cards[0]["text"] == "NOTION"              # text card instead
    assert any(x["action"] == "text_card" and x["need"] == "entity" for x in out["_broll_log"])


def test_action_need_keeps_stock(monkeypatch):
    monkeypatch.setattr(main, "PEXELS_KEY", "k")
    async def fake_pexels(*a, **k): return [{"url": "https://x/v.mp4", "description": "driving"}]
    async def fake_rerank(cue, cands, dossier): return "https://x/v.mp4"
    monkeypatch.setattr(main, "_fetch_pexels_candidates", fake_pexels)
    monkeypatch.setattr(main, "_rerank_broll", fake_rerank)
    e = {"broll": [{"src_in": 200, "src_out": 260, "cue_text": "driving west",
                    "broll_query": "driving highway", "source": "stock",
                    "need": "action", "fallback_text": "driving"}], "overlays": []}
    out = asyncio.run(main._resolve_broll(e))
    assert len(out["broll"]) == 1 and out["broll"][0]["resolved_url"] == "https://x/v.mp4"


def test_broll_no_repeat_within_15s(monkeypatch):
    monkeypatch.setattr(main, "PEXELS_KEY", "k")
    monkeypatch.setattr(main, "_broll_url_cache", {})
    async def fake_pexels(*a, **k): return [{"url": "https://x/same.mp4", "description": "d"}]
    async def fake_rerank(cue, cands, dossier): return "https://x/same.mp4"
    monkeypatch.setattr(main, "_fetch_pexels_candidates", fake_pexels)
    monkeypatch.setattr(main, "_rerank_broll", fake_rerank)
    e = {"broll": [{"src_in": 100, "src_out": 160, "cue_text": "a", "broll_query": "q1",
                    "source": "stock", "need": "action", "fallback_text": "a"},
                   {"src_in": 300, "src_out": 360, "cue_text": "b", "broll_query": "q2",
                    "source": "stock", "need": "action", "fallback_text": "b"}], "overlays": []}
    out = asyncio.run(main._resolve_broll(e))
    # both resolve to the SAME url within 200f (<450) → the second is dropped as a repeat
    assert len(out["broll"]) == 1
    assert any(x["action"] == "skipped_repeat" for x in out["_broll_log"])


# ── Addendum composition modes (schema v5) ────────────────────────────────────

def test_broll_mode_and_need_survive_edl_roundtrip():
    # The pydantic model must DECLARE mode/need/fallback_text — undeclared keys are
    # silently dropped on EDL(**data).model_dump() (the known loose-keys gotcha), which
    # is exactly how the Part 4A tier rule went dark the first time.
    words = _sentence(["hello"] * 40, 0)
    plan = {"cuts": [], "keeps": [],
            "broll": [{"range": [120, 240], "cue": "notion app", "query": "notion",
                       "source": "stock", "need": "entity", "text": "NOTION", "mode": "panel"}]}
    d = edl.assemble_edl(plan, words, "broll_cutaway", "myth-buster").model_dump()
    assert d["broll"], "b-roll entry dropped"
    b = d["broll"][0]
    assert b["mode"] == "panel" and b["need"] == "entity" and b["fallback_text"] == "NOTION"


def test_broll_panel_allows_longer_hold_and_hook_overlap():
    words = _sentence(["hello"] * 60, 0)
    # panel over the hook (frame 30) — allowed because the face stays visible; and it may breathe
    # PAST the full-frame cap (75f) up to the panel ceiling (90f). v2: a 100f phrase clamps into
    # the [36,90] action-panel band (±6f jitter stays inside it).
    plan = {"cuts": [], "keeps": [],
            "broll": [{"range": [30, 130], "cue": "c", "query": "q", "source": "stock",
                       "need": "action", "text": "", "mode": "panel"}]}
    d = edl.assemble_edl(plan, words, "broll_cutaway", "myth-buster").model_dump()
    assert d["broll"], "panel insert rejected"
    b = d["broll"][0]
    hold = b["src_out"] - b["src_in"]
    assert 75 < hold <= 90         # breathes past full's 75f cap, but ≤ the 3s panel ceiling
    # same range as mode "full" is rejected (hook protection)
    plan2 = {"cuts": [], "keeps": [],
             "broll": [{"range": [30, 130], "cue": "c", "query": "q", "source": "stock",
                        "need": "action", "text": "", "mode": "full"}]}
    d2 = edl.assemble_edl(plan2, words, "broll_cutaway", "myth-buster").model_dump()
    assert not d2["broll"]


def test_render_plan_carries_mode_layout_montage():
    words = _sentence(["hello"] * 40, 0)
    plan = {"cuts": [], "keeps": [],
            "broll": [{"range": [120, 240], "cue": "c", "query": "q", "source": "stock",
                       "need": "action", "text": "", "mode": "card"}]}
    d = edl.assemble_edl(plan, words, "talking_head", "myth-buster").model_dump()
    d["broll"][0]["resolved_url"] = "http://x/v.mp4"
    d["layout"]["speaker_treatment"] = "pip_circle"
    d["montage"] = {"frame_in": 75, "frames_per": 12, "items": ["http://x/a.jpg"] * 4}
    rp = edl.build_render_plan(d)
    assert rp["broll"][0]["mode"] == "card"
    assert rp["layout"]["speaker_treatment"] == "pip_circle"
    assert rp["montage"]["items"] and rp["schema_version"] == edl.PLAN_SCHEMA_VERSION == 7
