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


def test_prompt_blocks_are_compact_and_targeted():
    for call in ("edit_plan", "brief", "review"):
        block = craft.prompt_block(call)
        assert block and len(block) < 1600      # never crowds the KB digest budget
    assert "Murch" in craft.prompt_block("edit_plan")
    assert "CONTRACT" in craft.prompt_block("brief").upper()
    assert craft.prompt_block("unknown") == ""


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
