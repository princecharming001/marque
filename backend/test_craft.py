"""Build 57 — Craft Engine: registry, prompt blocks, lints, end-card read scaling."""
from app import craft
from app.edit_lint import lint_edl


def _bare(total=600):
    return {"style": "talking_head", "format_id": "x",
            "segments": [{"src_in": 0, "src_out": total}], "drops": [],
            "overlays": [], "broll": [], "captions": [{"word": "hi", "frame": 0}]}


def _words(n=40, ms_per=400, final="done."):
    out = [{"word": f"w{i}", "start_ms": i * ms_per, "end_ms": (i + 1) * ms_per}
           for i in range(n - 1)]
    out.append({"word": final, "start_ms": (n - 1) * ms_per, "end_ms": n * ms_per})
    return out


# ---------------------------------------------------------------- registry

def test_registry_loads_sourced_rules():
    rs = craft.rules()
    assert len(rs) >= 15                        # all seven doctrine files contribute
    assert all(r.get("source") for r in rs)     # NOTHING unsourced (the owner's rule)
    ids = {r["id"] for r in rs}
    for expected in ("cut.murch_priority", "typ.reading_rate",
                     "pace.breathe_after_peak", "story.ending_complete",
                     "pack.promise_contract", "snd.dialogue_first"):
        assert expected in ids, expected


def test_rule_params_and_version():
    p = craft.rule_params("typ.reading_rate")
    assert p["sec_per_word"] == 0.3 and p["chars_per_sec"] == 20
    assert craft.craft_version().startswith("craft-")
    # unknown id → caller default
    assert craft.rule_params("nope", {"a": 1}) == {"a": 1}


def test_prompt_blocks_are_compact_and_targeted(monkeypatch):
    # 57.4: cut-deciding blocks are flag-armed (default OFF — restores the
    # build-51 planner prompt exactly; owner judged its keep/drop choices better).
    assert craft.prompt_block("edit_plan") == ""
    assert craft.prompt_block("brief") == ""
    monkeypatch.setenv("CRAFT_PROMPTS", "1")
    for call in ("edit_plan", "brief", "review"):
        block = craft.prompt_block(call)
        assert block and len(block) < 1600      # never crowds the KB digest budget
    assert "Murch" in craft.prompt_block("edit_plan")
    assert "CONTRACT" in craft.prompt_block("brief").upper()
    assert craft.prompt_block("unknown") == ""


def test_edit_plan_prompt_matches_build51_cut_guidance():
    # The regression restore's actual contract: with the flag off, the planner's
    # system prompt must carry NO craft cut doctrine (byte-parity with build 51).
    import prompts
    words = [{"word": f"w{i}", "start_ms": i * 400, "end_ms": (i + 1) * 400}
             for i in range(40)]
    sys_p, _ = prompts.edit_plan_prompt("talking_head", words, {"title": "t", "hook": "h"},
                                        {"niche": "x"})
    assert "cut LONG" not in sys_p and "Murch" not in sys_p


# ---------------------------------------------------------------- lints

def test_reading_rate_flags_short_text_card():
    edl = _bare()
    # 12 words in 30 frames (1s) — needs >= 3.6s at 0.3s/word.
    edl["overlays"] = [{"type": "text_card", "src_in": 100, "src_out": 130,
                        "text": "twelve words of copy that cannot be read this fast at all"}]
    codes = [f["code"] for f in lint_edl(edl, _words())]
    assert "reading_rate" in codes


def test_reading_rate_passes_generous_hold():
    edl = _bare()
    edl["overlays"] = [{"type": "text_card", "src_in": 100, "src_out": 190,
                        "text": "Three word card"}]   # 3s for 3 words
    codes = [f["code"] for f in lint_edl(edl, _words())]
    assert "reading_rate" not in codes


def test_reading_rate_warns_on_underheld_end_card():
    edl = _bare()
    edl["end_card"] = {"text": "Follow for the full breakdown every single week",
                       "frames": 40, "show_handle": True}
    fs = [f for f in lint_edl(edl, _words()) if f["code"] == "reading_rate"]
    assert fs and fs[0]["severity"] == "warn"


def test_ending_incomplete_flags_trailing_thought():
    edl = _bare()
    codes = [f["code"] for f in lint_edl(edl, _words(final="because"))]
    assert "ending_incomplete" in codes


def test_ending_complete_with_period_or_end_card():
    edl = _bare()
    assert "ending_incomplete" not in [f["code"] for f in lint_edl(edl, _words(final="done."))]
    edl2 = _bare()
    edl2["end_card"] = {"text": "Follow", "frames": 75, "show_handle": True}
    assert "ending_incomplete" not in [f["code"] for f in lint_edl(edl2, _words(final="because"))]


def test_breathe_after_peak_flags_relentless_cluster():
    edl = _bare(900)
    # Metronome-free dense cluster then continued density — no release hold.
    ins = [30, 60, 95, 125, 160, 190, 225, 255, 290]
    edl["overlays"] = [{"type": "punch_in", "src_in": f, "src_out": f + 20, "scale": 1.06}
                      for f in ins]
    codes = [f["code"] for f in lint_edl(edl, _words(60))]
    assert "no_breath_after_peak" in codes


# ------------------------------------------------- end-card read scaling

def test_end_card_hold_scales_with_text():
    from app.retention import place_end_card
    edl = _bare()
    hints = {"end_card": {"wanted": True,
                          "text": "Follow for the full playbook broken down step by step"}}
    out = place_end_card(edl, _words(), style="talking_head", hints=hints)
    frames = out["end_card"]["frames"]
    # 10 words -> 2 reads * 3s = 180f, clamped to 150.
    assert frames == 150
    short = place_end_card(_bare(), _words(), style="talking_head",
                           hints={"end_card": {"wanted": True, "text": "Follow"}})
    assert short["end_card"]["frames"] == 75    # floor holds for short CTAs


# ------------------------------------------------- knob registration

def test_interrupt_density_knob_registered():
    import main
    assert main.EDIT_KNOBS["interrupt_density"] == ["calm", "standard", "dense"]
    out = main._select_edit_knobs("cr-craft", {}, "")
    k = out["knobs"]["interrupt_density"]
    assert k["chosen_by"] == "default" and k["value"] == "standard"
    assert abs(sum(k["propensities"].values()) - 1.0) < 0.05


# ------------------------------------------------- trending native sounds

def test_vibe_canon_expands_to_catalog_tags():
    import main
    # A "motivational" plan must land on a driving/upbeat bed, not the seed pick.
    t = main._select_music_track(vibe="motivational", seed=0)
    assert t.get("vibe") in ("driving", "upbeat")
    n = main._select_music_track(vibe="nostalgic", seed=0)
    assert n.get("vibe") == "chill"
    # Legacy exact tags keep working untouched.
    assert main._select_music_track(vibe="chill", seed=0).get("vibe") == "chill"


def test_trending_suggestion_covers_all_canon_vibes():
    import main
    from app import craft
    for v in craft.rule_params("snd.vibe_canon")["vibes"]:
        s = main._suggest_trending_sound(vibe=v)
        assert s and s["title"] and s["artist"] and s["vibe"] == v, v
        assert s["as_of"], "map must be dated — staleness is a doctrine edit"
        assert "native" in s["how"].lower() or "Instagram app" in s["how"]


def test_trending_suggestion_infers_from_coarse_signals():
    import main
    # Legacy plan vibes fold to the nearest mood.
    assert main._suggest_trending_sound(vibe="driving")["vibe"] == "motivational"
    assert main._suggest_trending_sound(vibe="chill")["vibe"] == "nostalgic"
    # No vibe at all: meme dial beats energy beats tone.
    assert main._suggest_trending_sound(meme_level="3")["vibe"] == "chaotic"
    assert main._suggest_trending_sound(energy="high")["vibe"] == "motivational"
    assert main._suggest_trending_sound(tone="calm")["vibe"] == "cinematic"
    # Verified attributions (the reel's list, web-checked).
    assert main._suggest_trending_sound(vibe="powerful")["artist"] == "The Weeknd & Playboi Carti"
    assert main._suggest_trending_sound(vibe="nostalgic")["title"] == "snowfall"


def test_trending_suggestion_never_raises_without_doctrine(monkeypatch):
    import main
    from app import craft
    monkeypatch.setattr(craft, "rule_params", lambda *a, **k: {})
    assert main._suggest_trending_sound(vibe="motivational") is None


# ------------------------------------------------- SFX lexicon (build 57.2)

def _sfx_all():
    import main
    return {k: f"https://x/{k}.mp3" for k in main.SFX_ASSETS}


def _edl_600():
    return {"style": "talking_head", "format_id": "x",
            "segments": [{"src_in": 0, "src_out": 600}], "drops": [],
            "overlays": [], "broll": [], "captions": []}


def test_sfx_assets_lexicon_registered():
    import main
    from app import craft
    kinds = set(craft.rule_params("snd.sfx_lexicon")["kinds"])
    assert kinds == set(main.SFX_ASSETS)
    # Meme-tier sounds with proprietary provenance ship UNARMED (env-only).
    assert main.SFX_ASSETS["fahh"] is None and main.SFX_ASSETS["sus"] is None


def test_text_card_gets_typing_and_sticker_gets_click():
    from app.retention import synthesize_sfx
    edl = _edl_600()
    edl["overlays"] = [
        {"type": "text_card", "src_in": 60, "src_out": 150, "text": "a"},
        {"type": "text_sticker", "src_in": 200, "src_out": 280, "text": "b"},
    ]
    out = synthesize_sfx(edl, [], sfx_assets=_sfx_all())
    kinds = {c["kind"] for c in out["audio"]["sfx"]}
    assert "typing" in kinds and "click" in kinds


def test_back_half_reveal_overlay_gets_sparkle():
    from app.retention import synthesize_sfx
    edl = _edl_600()
    edl["overlays"] = [
        {"type": "text_card", "src_in": 60, "src_out": 150, "text": "setup"},
        {"type": "text_card", "src_in": 450, "src_out": 560, "text": "reveal"},
    ]
    out = synthesize_sfx(edl, [], sfx_assets=_sfx_all())
    by_frame = {c["src_in"]: c["kind"] for c in out["audio"]["sfx"]}
    assert by_frame.get(60) == "typing"
    assert by_frame.get(450) == "sparkle"      # last text overlay past midpoint


def test_hero_earns_riser_lead_in():
    from app.retention import couple_broll_sfx
    edl = _edl_600()
    edl["broll"] = [{"src_in": 300, "src_out": 380, "hero": True,
                    "resolved_url": "https://x/v.mp4"}]
    out = couple_broll_sfx(edl, sfx_assets=_sfx_all())
    cues = {(c["src_in"], c["kind"]) for c in out["audio"]["sfx"]}
    assert (300, "whoosh") in cues and (270, "riser") in cues


def test_commons_still_gets_shutter():
    from app.retention import couple_broll_sfx
    edl = _edl_600()
    edl["broll"] = [{"src_in": 240, "src_out": 300, "source": "commons",
                    "resolved_url": "https://x/i.jpg"}]
    out = couple_broll_sfx(edl, sfx_assets=_sfx_all())
    assert any(c["kind"] == "shutter" and c["src_in"] == 240
               for c in out["audio"]["sfx"])


def test_fahh_replaces_pop_only_when_armed_and_only_last_meme():
    from app.retention import couple_broll_sfx
    def _memes():
        e = _edl_600()
        e["broll"] = [
            {"src_in": 200, "src_out": 240, "need": "meme", "resolved_url": "https://x/1.gif"},
            {"src_in": 400, "src_out": 440, "need": "meme", "resolved_url": "https://x/2.gif"},
        ]
        return e
    armed = couple_broll_sfx(_memes(), sfx_assets=_sfx_all(),
                             video_type="entertainment", energy="high")
    kinds = {c["src_in"]: c["kind"] for c in armed["audio"]["sfx"]}
    assert kinds.get(200) == "pop" and kinds.get(400) == "fahh"
    unarmed_assets = {**_sfx_all(), "fahh": None}
    plain = couple_broll_sfx(_memes(), sfx_assets=unarmed_assets,
                             video_type="entertainment", energy="high")
    assert all(c["kind"] == "pop" for c in plain["audio"]["sfx"])


# ------------------------------------------------- 57.3 placement audit fixes

def test_text_cues_never_displace_old_accents():
    from app.retention import synthesize_sfx
    # 300 kept frames -> budget 1. A mid-video transition whoosh must win that
    # slot over an early typing cue (pre-57.2 behavior preserved; text cues only
    # consume leftover budget).
    edl = {"style": "talking_head", "format_id": "x",
           "segments": [{"src_in": 0, "src_out": 150}, {"src_in": 150, "src_out": 300}],
           "drops": [],
           "overlays": [{"type": "text_card", "src_in": 60, "src_out": 120, "text": "a"}],
           "transitions": [{"after_segment": 0}], "broll": [], "captions": []}
    out = synthesize_sfx(edl, [], sfx_assets=_sfx_all())
    kinds = [c["kind"] for c in out["audio"]["sfx"]]
    assert kinds == ["whoosh"]


def test_reveal_midpoint_uses_kept_timeline_not_source_span():
    from app.retention import synthesize_sfx
    # 900 source frames but [300,840) dropped -> kept = [0,300)+[840,900) = 360f,
    # kept-midpoint = source frame 180. An overlay at 250 IS past the kept
    # midpoint (it sits at 250/360 of kept content) -> reveal sparkle. The old
    # source-span math (mid = 0+180... wait both agree here) — use the inverse:
    # overlay at 170 is BEFORE the kept midpoint (170/360) but the naive
    # kept[0]+total//2 = 180 also says before. Assert the drop-heavy case:
    # kept = [0,60)+[600,900) = 360f, kept-mid = source 600+120=720. Overlay at
    # 650 is only 110/360 in -> NOT the back half, must stay typing (the naive
    # source math mid=0+180 would have called 650 a reveal).
    edl = {"style": "talking_head", "format_id": "x",
           "segments": [{"src_in": 0, "src_out": 900}],
           "drops": [{"src_in": 60, "src_out": 600}],
           "overlays": [{"type": "text_card", "src_in": 650, "src_out": 720, "text": "x"}],
           "broll": [], "captions": []}
    out = synthesize_sfx(edl, [], sfx_assets=_sfx_all())
    kinds = {c["kind"] for c in out["audio"]["sfx"]}
    assert kinds == {"typing"}


def test_riser_requires_uncut_run_into_hero():
    from app.retention import couple_broll_sfx
    # A drop sits inside the [hero-30, hero] run -> the output gap shrinks and
    # the riser climax would land after the cut. No riser; whoosh stays.
    edl = _edl_600()
    edl["drops"] = [{"src_in": 280, "src_out": 290}]
    edl["broll"] = [{"src_in": 300, "src_out": 380, "hero": True,
                    "resolved_url": "https://x/v.mp4"}]
    out = couple_broll_sfx(edl, sfx_assets=_sfx_all())
    kinds = [c["kind"] for c in out["audio"]["sfx"]]
    assert kinds == ["whoosh"]


def test_meme_pop_outranks_second_shutter():
    from app.retention import couple_broll_sfx, _SFX_BUDGET_PER_30S
    assert _SFX_BUDGET_PER_30S == 3
    # 600 kept frames -> budget 2. Two stills + one meme: the meme's punchline
    # pop must take a slot ahead of the second shutter.
    edl = _edl_600()
    edl["broll"] = [
        {"src_in": 100, "src_out": 160, "source": "commons", "resolved_url": "https://x/1.jpg"},
        {"src_in": 250, "src_out": 310, "source": "commons", "resolved_url": "https://x/2.jpg"},
        {"src_in": 450, "src_out": 490, "need": "meme", "resolved_url": "https://x/m.gif"},
    ]
    out = couple_broll_sfx(edl, sfx_assets={**_sfx_all(), "fahh": None},
                           video_type="entertainment", energy="high")
    kinds = sorted(c["kind"] for c in out["audio"]["sfx"])
    assert kinds == ["pop", "shutter"]


# ------------------------------------------------- 57.5 one-title contract

def test_plan_text_cards_dropped_for_talking_head_kept_for_react():
    from app.edl import assemble_edl
    plan = {"cuts": [], "text_cards": [{"frame": 120, "text": "Big claim"}],
            "captions": True, "punch_ins": [], "music": {"wanted": False, "vibe": ""}}
    words = [{"word": f"w{i}", "start_ms": i * 400, "end_ms": (i + 1) * 400}
             for i in range(30)]
    th = assemble_edl(plan, words, "talking_head", "x", prefs={}).model_dump()
    assert not [o for o in th["overlays"] if o["type"] == "text_card"]
    gs = assemble_edl(plan, words, "green_screen", "x", prefs={}).model_dump()
    assert [o for o in gs["overlays"] if o["type"] == "text_card"]


def test_manual_text_card_op_still_works_in_talking_head():
    from app.edl import assemble_edl, apply_edl_ops
    words = [{"word": f"w{i}", "start_ms": i * 400, "end_ms": (i + 1) * 400}
             for i in range(30)]
    edl = assemble_edl({"cuts": []}, words, "talking_head", "x", prefs={}).model_dump()
    out, results = apply_edl_ops(
        edl, [{"type": "add_text_card", "start_frame": 60, "end_frame": 150,
               "text": "My card"}], words)
    assert results[0]["applied"]
    assert [o for o in out["overlays"] if o["type"] == "text_card"]
