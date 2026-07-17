"""Viral/aesthetic v2 upgrade — new-behavior coverage.

Covers: cold-open gate, title v2 duration/normalization/theme, music dropout,
SFX hit-reservation + hook-pop + b-roll coupling, jitter determinism, genre
density, reaction reclassify guards, MusicDropout round-trip, theme volume,
RETENTION_PASSES "all"-in-csv expansion.
"""
from __future__ import annotations

import asyncio

from app import retention
from app import themes as themes_mod
from app.edl import EDL, assemble_edl, ms_to_frame
import main


def _run(coro):
    return asyncio.run(coro)


def _steady_words(total_ms: int, step: int = 400, start_ms: int = 0) -> list[dict]:
    return [{"word": f"word{i}", "start_ms": t, "end_ms": t + step - 50}
            for i, t in enumerate(range(start_ms, total_ms, step))]


def _bare_edl(style: str, total: int, overlays=None, segments=None) -> dict:
    return {"style": style, "segments": segments or [{"src_in": 0, "src_out": total}],
            "drops": [], "overlays": overlays or [], "broll": [], "captions": [],
            "caption_options": {}, "audio": None}


def _alpha_words(n: int, step_ms: int = 400) -> list[dict]:
    # pure-alpha long content words so the b-roll floor synth has candidates
    _nouns = ["interface", "product", "founder", "growth", "metric", "startup",
              "revenue", "customer", "platform", "strategy"]
    return [{"word": _nouns[i % len(_nouns)], "start_ms": i * step_ms, "end_ms": i * step_ms + 300}
            for i in range(n)]


# --- RETENTION_PASSES expansion (the live prod bug) --------------------------

def test_enabled_passes_expands_all_inside_csv(monkeypatch):
    monkeypatch.setattr(retention, "_ENV_PASSES", "all,framing,hook_pack,jitter,cold_open,dropout")
    enabled = retention._enabled_passes()
    for p in ("filler", "retake", "pacing", "emphasis", "interrupts", "sfx", "structure",
              "framing", "hook_pack", "jitter", "cold_open", "dropout"):
        assert p in enabled, f"{p} missing — the 'all' csv member must expand"
    assert "all" not in enabled


# --- Cold open ---------------------------------------------------------------

def test_cold_open_trims_long_lead_keeps_pad():
    total = ms_to_frame(20000)
    words = _steady_words(20000, start_ms=2000)          # first word at 2s = frame 60
    edl = _bare_edl("talking_head", total)
    out = retention.trim_cold_open(edl, words)
    head = out["segments"][0]
    onset = ms_to_frame(2000)
    assert head["src_in"] == onset - retention._COLD_OPEN_PAD_FRAMES
    # and the word itself is never clipped
    assert head["src_in"] < onset


def test_cold_open_noop_when_already_hot():
    total = ms_to_frame(20000)
    words = _steady_words(20000, start_ms=200)           # first word at frame 6 (≤12)
    edl = _bare_edl("talking_head", total)
    out = retention.trim_cold_open(edl, words)
    assert out["segments"][0]["src_in"] == 0


def test_cold_open_respects_min_duration_floor():
    words = _steady_words(3000, start_ms=2500)           # nearly all lead
    edl = _bare_edl("talking_head", ms_to_frame(3000))
    out = retention.trim_cold_open(edl, words)
    assert out["segments"][0]["src_in"] == 0             # trimming would starve the take


# --- Title v2 ----------------------------------------------------------------

def test_hook_title_holds_to_first_sentence_end_clamped():
    total = ms_to_frame(30000)
    words = _steady_words(30000)
    words[8]["word"] = "matters."                        # sentence ends at word 8 (~3.55s)
    edl = _bare_edl("talking_head", total)
    out = retention.place_hook_overlay(edl, words, style="talking_head",
                                       hints={"hook_text": "Stop doing this one thing"})
    hook = next(o for o in out["overlays"] if o["type"] == "text_sticker")
    hold = hook["src_out"] - hook["src_in"]
    assert retention._HOOK_HOLD_MIN_OUT <= hold <= retention._HOOK_HOLD_MAX_OUT
    end_f = ms_to_frame(words[8]["end_ms"])
    assert abs(hook["src_out"] - end_f) <= 2             # sentence-end anchored (1.0 speed)


def test_hook_title_fallback_hold_without_punctuation():
    total = ms_to_frame(30000)
    words = _steady_words(30000)                         # no punctuation anywhere
    edl = _bare_edl("talking_head", total)
    out = retention.place_hook_overlay(edl, words, style="talking_head",
                                       hints={"hook_text": "Stop doing this"})
    hook = next(o for o in out["overlays"] if o["type"] == "text_sticker")
    assert hook["src_out"] - hook["src_in"] == retention._HOOK_HOLD_FALLBACK_OUT


def test_normalize_hook_text_matrix():
    n = retention._normalize_hook_text
    assert n("This is  the   hook.") == "This is the hook"          # period + whitespace
    assert n("Why does this work?") == "Why does this work?"        # question mark kept
    assert n("Do it now!") == "Do it now!"                          # bang kept
    assert n("hello…") == "hello"
    assert n("Stop doing this", uppercase=True) == "STOP DOING THIS"
    long = "a" * 30 + " " + "b" * 30 + " " + "c" * 30
    out = n(long)
    assert len(out) <= retention._HOOK_TEXT_MAX_CHARS + 1 and not out.endswith(" ")
    assert " b" not in out or out.count(" ") <= 1                    # word-boundary clamp


def test_hook_title_uppercase_follows_caption_grammar():
    total = ms_to_frame(30000)
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", total)
    edl["caption_options"] = {"uppercase": True}
    out = retention.place_hook_overlay(edl, words, style="talking_head",
                                       hints={"hook_text": "Stop doing this"})
    hook = next(o for o in out["overlays"] if o["type"] == "text_sticker")
    assert hook["text"] == "STOP DOING THIS"
    assert hook["pos_y"] == 0.24                          # face-aware upper-center


def test_hook_title_takes_theme_font_and_bg():
    total = ms_to_frame(30000)
    words = _steady_words(30000)
    edl = _bare_edl("talking_head", total)
    theme = themes_mod.get_theme("hormozi_punch")         # hook: anton / box (per themes.py)
    out = retention.place_hook_overlay(edl, words, style="talking_head",
                                       hints={"hook_text": "Stop doing this"}, theme=theme)
    hook = next(o for o in out["overlays"] if o["type"] == "text_sticker")
    assert hook["font"] == theme.hook.get("sticker_font")
    assert hook["bg"] == theme.hook.get("sticker_bg")


# --- SFX: hit reservation + hook pop + coupling -------------------------------

def test_sfx_hit_reserved_at_top_emphasis_span():
    total = ms_to_frame(30000)
    words = _steady_words(30000)
    overlays = [{"type": "punch_in", "src_in": f, "src_out": f + 10, "scale": 1.08, "text": ""}
                for f in (60, 120, 180, 240)]             # enough pops to exhaust budget 3
    edl = _bare_edl("talking_head", total, overlays=overlays)
    out = retention.synthesize_sfx(edl, words,
                                   sfx_assets={"pop": "p.mp3", "hit": "h.mp3"},
                                   emphasis_spans=[(300, 330), (500, 560)])
    kinds = {c["kind"]: c for c in out["audio"]["sfx"]}
    assert "hit" in kinds, "the reveal hit must be RESERVED, not crowded out"
    assert kinds["hit"]["src_in"] == 500                  # longest span wins


def test_sfx_hook_pop_only_with_theme_impact():
    total = ms_to_frame(30000)
    words = _steady_words(30000)
    sticker = {"type": "text_sticker", "src_in": 0, "src_out": 90, "text": "hook",
               "scale": 1.0, "pos_x": 0.5, "pos_y": 0.24, "rotation": 0.0,
               "color": None, "bg": "box", "font": "anton"}
    theme = themes_mod.get_theme("hormozi_punch")
    assert theme.hook.get("impact_sfx"), "fixture assumes hormozi has impact_sfx on"
    edl = _bare_edl("talking_head", total, overlays=[sticker])
    out = retention.synthesize_sfx(edl, words, sfx_assets={"pop": "p.mp3"}, theme=theme)
    assert any(c["src_in"] == 0 and c["kind"] == "pop" for c in out["audio"]["sfx"])
    # without a theme (or impact off) → no hook pop
    out2 = retention.synthesize_sfx(_bare_edl("talking_head", total, overlays=[sticker]),
                                    words, sfx_assets={"pop": "p.mp3"})
    assert not any(c["src_in"] == 0 for c in (out2.get("audio") or {}).get("sfx", []))


def test_couple_broll_sfx_memes_only_entertainment_only_idempotent():
    total = 3000
    seg = [{"src_in": 0, "src_out": total}]
    edl = {"style": "talking_head", "segments": seg, "drops": [], "overlays": [],
           "captions": [], "audio": None,
           "broll": [
               {"src_in": 300, "src_out": 330, "need": "meme", "source": "klipy",
                "resolved_url": "https://k/x.mp4"},
               {"src_in": 600, "src_out": 660, "need": "action", "source": "stock",
                "resolved_url": "https://p/y.mp4"},
               {"src_in": 900, "src_out": 930, "need": "meme", "source": "giphy",
                "resolved_url": None},                     # unresolved → never coupled
           ]}
    out = retention.couple_broll_sfx(edl, sfx_assets={"pop": "p.mp3"},
                                     video_type="freestyle_rant")
    cues = out["audio"]["sfx"]
    assert [c["src_in"] for c in cues] == [300], "memes only; unresolved and non-meme skipped"
    # idempotent on re-run (tweak re-render)
    out2 = retention.couple_broll_sfx(out, sfx_assets={"pop": "p.mp3"},
                                      video_type="freestyle_rant")
    assert len(out2["audio"]["sfx"]) == 1
    # educational → gated off entirely
    out3 = retention.couple_broll_sfx(edl, sfx_assets={"pop": "p.mp3"}, video_type="tutorial")
    assert not (out3.get("audio") or {})


# --- Music dropout ------------------------------------------------------------

def _music_edl(total: int) -> dict:
    edl = _bare_edl("talking_head", total)
    edl["audio"] = {"lufs_target": -14.0,
                    "music": {"url": "https://m/track.mp3", "volume": 0.12,
                              "duck_voice": True, "dropouts": []}}
    return edl


def test_music_dropout_on_top_span_with_guards():
    total = 900
    words = _steady_words(30000)
    out = retention.plan_music_dropout(_music_edl(total), words, style="talking_head",
                                       emphasis_spans=[(300, 360), (500, 520)])
    d = out["audio"]["music"]["dropouts"]
    assert len(d) == 1
    assert d[0]["frame_in"] == 300 - retention._MUSIC_DROPOUT_PRE_F
    assert d[0]["frame_out"] == 360 + retention._MUSIC_DROPOUT_POST_F
    # hook-protected span → no dropout
    out2 = retention.plan_music_dropout(_music_edl(total), words, style="talking_head",
                                        emphasis_spans=[(30, 80)])
    assert not out2["audio"]["music"]["dropouts"]
    # no music → untouched
    out3 = retention.plan_music_dropout(_bare_edl("talking_head", total), words,
                                        style="talking_head", emphasis_spans=[(300, 360)])
    assert not (out3.get("audio") or {})
    # fast_cuts (music-forward) → never
    out4 = retention.plan_music_dropout(_music_edl(total), words, style="fast_cuts",
                                        emphasis_spans=[(300, 360)])
    assert not out4["audio"]["music"]["dropouts"]


def test_music_dropout_round_trips_through_edl_model():
    edl = _music_edl(900)
    edl["format_id"] = "myth-buster"
    edl["layout"] = {"style": "talking_head"}
    edl["audio"]["music"]["dropouts"] = [{"frame_in": 294, "frame_out": 369}]
    d = EDL(**edl).model_dump()
    assert d["audio"]["music"]["dropouts"] == [{"frame_in": 294, "frame_out": 369}]


# --- Jitter determinism + genre density ---------------------------------------

def _dense_plan(n: int, gap: int = 200, hold: int = 60) -> dict:
    return {"broll": [{"range": [200 + i * gap, 200 + i * gap + hold], "cue": f"c{i}",
                      "query": f"q{i}", "source": "stock", "mode": "full", "need": "action"}
                     for i in range(n)]}


def test_broll_jitter_deterministic_per_seed():
    w = _alpha_words(150)
    plan = _dense_plan(10)
    a = assemble_edl(plan, w, "talking_head", "myth-buster", job_seed="job-1").model_dump()
    b = assemble_edl(plan, w, "talking_head", "myth-buster", job_seed="job-1").model_dump()
    c = assemble_edl(plan, w, "talking_head", "myth-buster", job_seed="job-2").model_dump()
    assert a["broll"] == b["broll"], "same seed ⇒ identical EDL"
    assert a["broll"] != c["broll"], "different seed ⇒ different jitter pattern"


def test_entertainment_density_beats_educational():
    w = _alpha_words(200)                                  # ~80s of speech
    plan: dict = {"broll": []}                             # floor does all the work
    ent = assemble_edl(plan, w, "broll_cutaway", "myth-buster",
                       brief={"video_type": "freestyle_rant"},
                       prefs={"broll": True, "broll_coverage": "full"}).model_dump()
    edu = assemble_edl(plan, w, "broll_cutaway", "myth-buster",
                       brief={"video_type": "tutorial"},
                       prefs={"broll": True, "broll_coverage": "full"}).model_dump()
    assert len(ent["broll"]) > len(edu["broll"]), \
        f"entertainment ({len(ent['broll'])}) must out-dense educational ({len(edu['broll'])})"
    assert len(ent["broll"]) >= 4


# --- Reaction reclassify guards (culturalize) ---------------------------------

def test_reaction_reclassify_guards(monkeypatch):
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "sk")
    main._broll_query_cache.clear()

    async def fake_json(system, user, schema, model=None, temperature=None):
        return {"queries": [{"i": 0, "query": "side eye", "reaction": True},
                            {"i": 1, "query": "mind blown", "reaction": True},
                            {"i": 2, "query": "shocked", "reaction": True}]}
    monkeypatch.setattr(main, "anthropic_json", fake_json)

    async def no_trending():
        return []
    monkeypatch.setattr(main, "_klipy_trending_titles", no_trending)

    def _edl():
        return {"broll": [
            {"src_in": 300, "src_out": 400, "cue_text": "a", "broll_query": "a",
             "need": "action", "mode": "full"},
            {"src_in": 600, "src_out": 700, "cue_text": "b", "broll_query": "b",
             "need": "concept", "mode": "full"},
            {"src_in": 900, "src_out": 1000, "cue_text": "c", "broll_query": "c",
             "need": "concept", "mode": "full"},
        ]}

    # Educational: cap 2 — third reclassify refused; mode forced panel; window ≤45f
    out = _run(main._culturalize_broll_queries(
        _edl(), {"brand": {"niche": "startup"}, "edit_brief": {"video_type": "tutorial"},
                 "config": {}}))
    memes = [b for b in out["broll"] if b["need"] == "meme"]
    assert len(memes) == 2, "educational reclassify cap is 2"
    for b in memes:
        assert b["mode"] == "panel"
        assert b["src_out"] - b["src_in"] <= 45
    main._broll_query_cache.clear()
    # Entertainment: all 3 fit under cap 5
    out2 = _run(main._culturalize_broll_queries(
        _edl(), {"brand": {"niche": "startup"},
                 "edit_brief": {"video_type": "freestyle_rant"}, "config": {}}))
    assert len([b for b in out2["broll"] if b["need"] == "meme"]) == 3


# --- Theme music volume (D4) ---------------------------------------------------

def test_theme_music_volume_overrides_default():
    edl = {"style": "talking_head", "segments": [{"src_in": 0, "src_out": 900}],
           "audio": None}
    out = main._apply_plan_music_vibe(edl, {"music": True}, {"wanted": True},
                                      theme_volume=0.08)
    assert out["audio"]["music"]["volume"] == 0.08
    edl2 = {"style": "talking_head", "segments": [{"src_in": 0, "src_out": 900}],
            "audio": None}
    out2 = main._apply_plan_music_vibe(edl2, {"music": True}, {"wanted": True})
    assert out2["audio"]["music"]["volume"] == 0.12       # default unchanged


# --- v3: glimpse bands + smart placement -------------------------------------

def test_entity_glimpse_band():
    # A named thing flashes: full-mode entity holds clamp to 15-24f (0.5-0.8s).
    w = _alpha_words(80)
    plan = {"broll": [{"range": [300, 400], "cue": "gochujang", "query": "gochujang paste",
                       "source": "stock", "mode": "full", "need": "entity"}]}
    d = assemble_edl(plan, w, "talking_head", "myth-buster").model_dump()
    assert d["broll"], "entity glimpse dropped"
    hold = d["broll"][0]["src_out"] - d["broll"][0]["src_in"]
    assert 15 <= hold <= 24, f"entity glimpse must flash 0.5-0.8s, got {hold}f"


def test_smart_mode_admitted_and_carried():
    w = _alpha_words(80)
    plan = {"broll": [{"range": [300, 360], "cue": "app demo", "query": "app",
                       "source": "stock", "mode": "smart", "need": "action"}]}
    d = assemble_edl(plan, w, "talking_head", "myth-buster",
                     prefs={"broll": True, "broll_mode": "smart"}).model_dump()
    assert d["broll"] and d["broll"][0]["mode"] == "smart"


def test_smart_inset_rect_ots_rule():
    from app.faces import smart_inset_rect
    right = smart_inset_rect({"x": 0.05, "y": 0.3, "w": 0.35, "h": 0.25})  # face left
    left = smart_inset_rect({"x": 0.60, "y": 0.3, "w": 0.35, "h": 0.25})   # face right
    assert right and right["x"] > 0.4, "face-left → inset RIGHT"
    assert left and left["x"] < 0.1, "face-right → inset LEFT"
    for r in (right, left):
        assert r["y"] >= 140 / 1920 - 1e-3, "must clear the platform top UI (4dp rounding tol)"
        assert r["y"] + r["h"] < 0.53, "must clear the caption band"
    assert smart_inset_rect(None) is None


def test_smart_inset_degrades_when_no_clear_spot():
    from app.faces import smart_inset_rect
    # A huge centered face fills the top band → no clear spot at any shrink step.
    assert smart_inset_rect({"x": 0.05, "y": 0.02, "w": 0.9, "h": 0.6}) is None


def test_broll_render_plan_carries_inset_rect():
    from app.edl import build_render_plan
    w = _alpha_words(80)
    plan = {"broll": [{"range": [300, 360], "cue": "x", "query": "x",
                       "source": "stock", "mode": "smart", "need": "action"}]}
    edl = assemble_edl(plan, w, "talking_head", "myth-buster",
                       prefs={"broll": True, "broll_mode": "smart"}).model_dump()
    edl["broll"][0]["resolved_url"] = "https://x/clip.mp4"
    edl["broll"][0]["inset_rect"] = {"x": 0.05, "y": 0.08, "w": 0.42, "h": 0.13}
    rp = build_render_plan(edl)
    assert rp["broll"][0]["inset_rect"] == {"x": 0.05, "y": 0.08, "w": 0.42, "h": 0.13}


def test_talking_head_defaults_broll_on():
    import prompts as _p
    assert _p.EDIT_FORMATS["talking_head"]["toggles"]["broll"] is True


# ---------- v4: gen-z meme dial + glimpse density + own_media degradation ----------

def _meme_plan(n=12, gap=120):
    return {"broll": [
        {"range": [200 + i * gap, 200 + i * gap + 30], "cue": f"m{i}", "query": f"m{i}",
         "source": "giphy", "mode": "panel", "need": "meme"} for i in range(n)]}


def test_meme_intensity_zero_kills_memes():
    w = _alpha_words(160)
    out = assemble_edl(_meme_plan(), w, "broll_cutaway", "myth-buster",
                       brief={"video_type": "freestyle_rant"},
                       prefs={"broll": True, "meme_intensity": 0}).model_dump()
    assert not [b for b in out["broll"] if b["need"] == "meme"], "level 0 must emit no memes"


def test_meme_intensity_scales_caps():
    w = _alpha_words(160)
    def count(level):
        out = assemble_edl(_meme_plan(), w, "broll_cutaway", "myth-buster",
                           brief={"video_type": "freestyle_rant"},
                           prefs={"broll": True, "meme_intensity": level}).model_dump()
        return len([b for b in out["broll"] if b["need"] == "meme"])
    c1, c3 = count(1), count(3)
    assert c1 <= 5, f"level 1 keeps the v2 entertainment cap (got {c1})"
    assert c3 > c1, f"brainrot must admit more memes than subtle ({c3} vs {c1})"


def test_glimpse_pair_admitted_at_half_spacing():
    # Two entity glimpses ~66f apart: glimpse holds (≤24f) HALVE effective spacing
    # (max 52f after halving), so the pair is admitted for EVERY jitter draw — a
    # 2s-cutaway pair at this gap would sit inside the educational rejection band.
    w = _alpha_words(120)
    plan = {"broll": [
        {"range": [200, 218], "cue": "gochujang", "query": "gochujang", "source": "stock",
         "mode": "full", "need": "entity"},
        {"range": [290, 308], "cue": "carbonara", "query": "carbonara", "source": "stock",
         "mode": "full", "need": "entity"},
    ]}
    out = assemble_edl(plan, w, "broll_cutaway", "myth-buster",
                       prefs={"broll": True}).model_dump()
    names = [b["cue_text"] for b in out["broll"]]
    assert "gochujang" in names and "carbonara" in names, \
        f"glimpse spacing relief failed: {names}"


def test_floor_emits_entity_glimpses_for_emphasized_words():
    from app.edl import _synthesize_broll_floor
    words = []
    t = 0
    for i in range(80):
        words.append({"word": f"payload{i}" if i % 7 else "gochujang",
                      "start_ms": t, "end_ms": t + 350,
                      "is_emphasized": (i % 7 == 0)})
        t += 400
    cues = _synthesize_broll_floor(words, ms_to_frame(t), "full", 90, 60, step_divisor=90)
    assert any(c["need"] == "entity" for c in cues), \
        "emphasized (inflected) words must synthesize entity glimpses"


def test_unresolved_own_media_literal_degrades_to_text_card():
    # Prod job 90813e10: an own_media entity cue with no URL shipped as a blank b-roll item.
    # The tier pass must now degrade it exactly like any unresolved literal.
    edl = {"style": "broll_cutaway", "broll": [
        {"src_in": 300, "src_out": 322, "cue_text": "gochujang jar", "broll_query": "",
         "source": "own_media", "mode": "full", "need": "entity",
         "fallback_text": "gochujang", "resolved_url": None}]}
    out = asyncio.run(main._resolve_broll(dict(edl), force_broll=True))
    assert not out["broll"], "URL-less own_media literal must not survive as b-roll"
    cards = [o for o in (out.get("overlays") or []) if o["type"] == "text_card"]
    assert cards, "must degrade to a text card"
    acts = [e["action"] for e in out["_broll_log"]]
    assert "text_card" in acts, out["_broll_log"]


def test_density_mandate_3x_educational_floor():
    # v5 owner mandate ("at least 3x as frequent — very important"): a plain educational
    # coverage=full take with an EMPTY plan must land ≥1 b-roll insert per 4s of runtime
    # (3x the observed ~1/12s baseline), driven entirely by the deterministic floor.
    w = _alpha_words(150)                      # 150 words × 400ms = 60s take
    total = ms_to_frame(w[-1]["end_ms"])
    out = assemble_edl({}, w, "broll_cutaway", "myth-buster",
                       prefs={"broll": True, "broll_coverage": "full"}).model_dump()
    n = len(out["broll"])
    assert n >= total // 120, f"density mandate missed: {n} inserts on a {total}f take (need ≥{total // 120})"
