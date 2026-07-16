"""Script-aware cutting + b-roll guarantee (the edit-quality batch)."""
import prompts
from app import edl as E
from app import retention as R


# ── Fix 1: script-aware cutting ──────────────────────────────────────────────

def test_edit_plan_prompt_includes_script_when_scripted():
    words = [{"word": "hello", "start_ms": 0, "end_ms": 300}]
    sys_s, usr_s = prompts.edit_plan_prompt(
        "talking_head", words, {"hook": "stop scrolling", "body": "the one thing about X"}, {})
    assert "INTENDED SCRIPT" in usr_s
    assert "the one thing about X" in usr_s
    assert "IF AN INTENDED SCRIPT IS PROVIDED" in sys_s   # soft-reference guidance always present


def test_edit_plan_prompt_handles_dict_hook():
    # hook may arrive as a {text, signal, strength} dict (not just a string) — must not crash.
    words = [{"word": "hello", "start_ms": 0, "end_ms": 300}]
    _sys, usr = prompts.edit_plan_prompt(
        "talking_head", words,
        {"hook": {"text": "the one metric that matters", "signal": "curiosity"}, "body": "b"}, {})
    assert "the one metric that matters" in usr


def test_edit_plan_prompt_omits_script_when_freestyle():
    words = [{"word": "hello", "start_ms": 0, "end_ms": 300}]
    _sys, usr = prompts.edit_plan_prompt("talking_head", words, {}, {})   # freestyle → {}
    assert "INTENDED SCRIPT" not in usr


def test_dedupe_retakes_script_gray_zone_catches_reworded_redo():
    # Two deliveries of the same scripted line, reworded enough that mutual similarity dips
    # into the gray zone (0.45–0.62) — only the script corroboration catches it.
    script = "this is the single most important growth lever for founders today"
    take1 = "so this is the single most important growth lever for founders today".split()
    take2 = "okay this is really the single biggest growth lever for founders right now".split()
    sim = R._shingle_sim(take1, take2)
    assert R._RETAKE_GRAY_SIM <= sim < R._RETAKE_SIM, f"fixture sim {sim} not in gray zone"
    assert R._same_script_line(take1, take2, R._script_sentences(script))

    # Build words: take1, a pause, take2, then more content (so the dropped take1 stays under
    # the 40% max-drop guard) → dedupe should drop the EARLIER take once the script corroborates.
    words = []
    t = 0
    def _push(seq):
        nonlocal t
        for w in seq:
            words.append({"word": w, "start_ms": t, "end_ms": t + 250}); t += 300
    _push(take1)
    t += 800   # pause splits utterances
    _push(take2)
    t += 800
    _push("then here is a completely different second point that adds real content to the take today".split())
    total = E.ms_to_frame(words[-1]["end_ms"])
    edl = {"segments": [{"src_in": 0, "src_out": total}], "drops": []}
    out_no_script = R.dedupe_retakes(edl, words, "")
    out_script = R.dedupe_retakes(edl, words, script)
    assert not out_no_script.get("drops")           # transcript-only misses it (gray zone)
    assert out_script.get("drops")                  # script corroboration catches the redo


# ── Fix 3: b-roll guarantee ──────────────────────────────────────────────────

def _concrete_words(n=60):
    sentence = "our product dashboard shows revenue growth metrics customers love the interface".split()
    return [{"word": sentence[i % len(sentence)], "start_ms": i * 350, "end_ms": i * 350 + 300,
             "is_emphasized": (i % 9 == 0)} for i in range(n)]


def test_broll_floor_synthesizes_when_opted_in_and_plan_empty():
    words = _concrete_words()
    out = E.assemble_edl({"broll": []}, words, "broll_cutaway", "myth-buster",
                         prefs={"broll": True, "broll_coverage": "full", "broll_mode": "full"})
    assert len(out.broll) >= 2                       # guarantee: at least a couple cutaways
    assert all(b.mode == "full" for b in out.broll)


def test_no_broll_floor_without_opt_in():
    words = _concrete_words()
    out = E.assemble_edl({"broll": []}, words, "broll_cutaway", "myth-buster", prefs={"broll": True})
    assert out.broll == []                           # best-effort default: no forced filler


def test_broll_floor_respects_face_protection_and_budget():
    words = _concrete_words(80)
    total = E.ms_to_frame(words[-1]["end_ms"])
    out = E.assemble_edl({"broll": []}, words, "broll_cutaway", "myth-buster",
                         prefs={"broll": True, "broll_coverage": "full", "broll_mode": "full"})
    for b in out.broll:
        assert b.src_in >= E._BROLL_HOOK_PROTECT      # never over the hook
        assert b.src_out <= total - E._BROLL_CTA_PROTECT
    used = sum(b.src_out - b.src_in for b in out.broll if b.mode == "full")
    assert used <= E._BROLL_RUNTIME_BUDGET * total + 1   # ≤40% budget still enforced


# ── Fix 3b: own_media cue falls back to stock when b-roll is forced ───────────

def test_forced_broll_own_media_cue_falls_back_to_stock(monkeypatch):
    import asyncio, main
    monkeypatch.setattr(main, "PEXELS_KEY", "fake")
    async def _cands(q, n): return [{"url": "http://stock/clip.mp4"}]
    async def _rerank(cue, cands, dossier): return "http://stock/clip.mp4"
    monkeypatch.setattr(main, "_fetch_pexels_candidates", _cands)
    monkeypatch.setattr(main, "_rerank_broll", _rerank)
    # An own_media cue with NO corpus: without force it stays unresolved (invisible); WITH
    # force it falls back to stock and becomes a real, kept cutaway.
    edl = {"broll": [{"src_in": 100, "src_out": 160, "cue_text": "gym equipment",
                      "broll_query": "gym equipment", "source": "own_media", "need": "entity"}],
           "overlays": []}
    out_no = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        main._resolve_broll({"broll": [dict(edl["broll"][0])], "overlays": []},
                            allow_generation=False, force_broll=False))
    assert not (out_no["broll"] and out_no["broll"][0].get("resolved_url"))   # unresolved w/o force
    out_yes = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        main._resolve_broll({"broll": [dict(edl["broll"][0])], "overlays": []},
                            allow_generation=False, force_broll=True))
    assert out_yes["broll"], "forced own_media cue must survive as a stock cutaway"
    assert out_yes["broll"][0].get("resolved_url") == "http://stock/clip.mp4"
    assert out_yes["broll"][0].get("source") == "stock"
