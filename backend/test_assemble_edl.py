"""Phase 3: assemble_edl + check_edl_invariants + EDIT_PLAN_JSON_SCHEMA (keyless)."""
from __future__ import annotations

import asyncio

import prompts
from app.edl import (assemble_edl, check_edl_invariants, build_render_plan, ms_to_frame,
                     strip_fillers)
from eval.edit_fixtures import FIXTURES, fixture
from eval import edl_eval


def _run(coro):
    return asyncio.run(coro)


def _words(fid):
    return fixture(fid)["words"]


# --- assembler guarantees -----------------------------------------------------

def test_empty_plan_yields_whole_take_default():
    w = _words("scripted-01")
    edl = assemble_edl({}, w, "talking_head", "myth-buster")
    d = edl.model_dump()
    assert d["segments"] and d["segments"][0]["src_in"] == 0
    # captions derived from cleaned words, NOT the (empty) plan
    kept, _ = strip_fillers(w)
    assert len(d["captions"]) == len([x for x in kept if x.get("word")])


def test_captions_always_from_clean_words_even_if_plan_has_none():
    w = _words("rambling-01")
    plan = {"caption_plan": {"style": "karaoke", "grouping": "phrase", "highlight_words": ["secret"]}}
    edl = assemble_edl(plan, w, "talking_head", "myth-buster")
    d = edl.model_dump()
    kept, _ = strip_fillers(w)
    assert len(d["captions"]) == len([x for x in kept if x.get("word")])
    assert d["caption_style"] == "karaoke"
    assert d["caption_options"]["highlight_words"] == ["secret"]


def test_filler_drops_always_present():
    w = _words("rambling-01")
    edl = assemble_edl({"cuts": []}, w, "talking_head", "myth-buster")
    d = edl.model_dump()
    assert any(dr["reason"] == "filler" for dr in d["drops"])  # deterministic fillers win


def test_broll_grammar_enforced():
    w = _words("listicle-01")
    total = ms_to_frame(w[-1]["end_ms"])
    # ask for a b-roll over the hook (f10) and an over-long hold — assembler must fix/drop
    plan = {"broll": [
        {"range": [10, 40], "cue": "hook", "query": "x", "source": "stock"},         # over hook → dropped
        {"range": [200, 400], "cue": "tool", "query": "laptop", "source": "stock"},  # 200f hold → clamped
    ]}
    edl = assemble_edl(plan, w, "faceless", "listicle")
    d = edl.model_dump()
    for b in d["broll"]:
        hold = b["src_out"] - b["src_in"]
        assert 36 <= hold <= 75, f"hold {hold} out of grammar (v2 action band, jitter-clamped)"


def test_broll_runtime_budget_caps_face_hiding_coverage():
    # Sourced doctrine: full-frame b-roll hides the face; cap total face-hiding coverage at
    # ~40% of runtime so the creator's face still owns the video. Ask for WAY too much
    # full-frame b-roll on a face style → the assembler drops the overflow.
    from app import edl as edl_mod
    w = [{"word": f"w{i}", "start_ms": i * 400, "end_ms": i * 400 + 350} for i in range(60)]  # ~24s
    total = ms_to_frame(w[-1]["end_ms"])
    # 8 full-frame cutaways, each ~2.5s, well spaced — far more than 40% if all kept
    plan = {"broll": [{"range": [g, g + 60], "cue": "thing", "query": "q", "source": "stock",
                       "mode": "full", "need": "action"}
                      for g in range(150, total - 90, 90)]}
    edl = assemble_edl(plan, w, "talking_head", "myth-buster")
    d = edl.model_dump()
    full_frames = sum(b["src_out"] - b["src_in"] for b in d["broll"] if b.get("mode") == "full")
    assert full_frames <= edl_mod._BROLL_RUNTIME_BUDGET * total + 1, \
        f"{full_frames}/{total} = {full_frames/total:.0%} exceeds the 40% face-hiding budget"
    assert d["broll"], "budget cap must not drop ALL b-roll — the earliest inserts stay"


def test_combined_visual_budget_caps_all_modes():
    # Realism pass: panel/card keep the face visible (exempt from the 40% FACE-HIDING budget) but
    # still count toward the 50% TOTAL-visual budget — a wall of panels can't own the whole video.
    from app import edl as edl_mod
    w = [{"word": f"w{i}", "start_ms": i * 400, "end_ms": i * 400 + 350} for i in range(120)]  # ~48s
    total = ms_to_frame(w[-1]["end_ms"])
    # 8 panels, each clamps to action's 105f ceiling — 8×105 = 840f ≈ 58% of ~1450f, over the 50% cap.
    plan = {"broll": [{"range": [g, g + 150], "cue": "t", "query": "q", "source": "stock",
                       "mode": "panel", "need": "action"}
                      for g in range(150, total - 120, 150)]}
    kept = assemble_edl(plan, w, "talking_head", "myth-buster").model_dump()["broll"]
    panel_frames = sum(b["src_out"] - b["src_in"] for b in kept)
    assert all(b["src_out"] - b["src_in"] <= 105 for b in kept)      # panel ceiling now 3.5s
    assert panel_frames <= edl_mod._BROLL_VISUAL_BUDGET * total + 1   # total insert time ≤ 50%
    assert kept                                                       # but not all dropped


def test_broll_hold_policy_per_need():
    # v2: a named thing (entity) caps at 54f (1.8s); a process (action) may breathe to 75f.
    # Mid-length spans (~70f) show the split: entity clamps DOWN to ≤54, action keeps ~64-75.
    w = [{"word": f"w{i}", "start_ms": i * 400, "end_ms": i * 400 + 350} for i in range(120)]
    plan = {"broll": [
        {"range": [200, 270], "cue": "the app", "query": "app", "source": "stock", "mode": "full", "need": "entity"},
        {"range": [800, 870], "cue": "the process", "query": "steps", "source": "stock", "mode": "full", "need": "action"},
    ]}
    d = assemble_edl(plan, w, "talking_head", "myth-buster").model_dump()
    holds = {b["cue_text"]: b["src_out"] - b["src_in"] for b in d["broll"]}
    assert holds["the app"] <= 54, f"entity hold {holds['the app']} should be short (≤54f)"
    assert holds["the process"] > 54, f"action hold {holds['the process']} should breathe past entity's cap"


def test_spacing_tightens_on_high_energy_or_entertainment():
    # Part 5: entertainment video_type allows cutaways ≥2s apart (vs ≥3s default). Two cutaways
    # spaced ~2.2s survive on a freestyle_rant but the SECOND is dropped on a neutral tutorial.
    w = [{"word": f"w{i}", "start_ms": i * 400, "end_ms": i * 400 + 350} for i in range(60)]
    plan = {"broll": [
        {"range": [150, 210], "cue": "a", "query": "a", "source": "stock", "mode": "full", "need": "action"},
        {"range": [280, 340], "cue": "b", "query": "b", "source": "stock", "mode": "full", "need": "action"},
    ]}
    ent = assemble_edl(plan, w, "talking_head", "myth-buster",
                       brief={"video_type": "freestyle_rant"}).model_dump()
    neu = assemble_edl(plan, w, "talking_head", "myth-buster",
                       brief={"video_type": "tutorial"}).model_dump()
    assert len(ent["broll"]) == 2, "entertainment tolerates the tighter pair"
    assert len(neu["broll"]) == 1, "informational keeps ≥3s spacing → drops the close one"


def test_floor_denser_and_alternates_hold_lengths():
    # Part 5: coverage=full floor is denser (~1 per 9s) AND varies hold length (not metronomic).
    # Words must be ALPHA content words (the floor rejects digits/stopwords) — cycle real nouns.
    _nouns = ["interface", "product", "founder", "growth", "metric", "startup",
              "revenue", "customer", "platform", "strategy"]
    w = [{"word": _nouns[i % len(_nouns)], "start_ms": i * 400, "end_ms": i * 400 + 350}
         for i in range(120)]  # ~48s
    d = assemble_edl({"broll": []}, w, "broll_cutaway", "myth-buster",
                     prefs={"broll": True, "broll_coverage": "full", "broll_mode": "full"}).model_dump()
    holds = [b["src_out"] - b["src_in"] for b in d["broll"]]
    assert len(holds) >= 3, f"denser floor should yield ≥3 over ~48s, got {len(holds)}"
    assert len(set(holds)) >= 2, f"floor holds must vary, got {holds}"


def test_meme_forces_panel_and_short_hold():
    # Part 5.2: a meme need renders as a PANEL (face stays) with a short hold (≤75f), even when
    # the plan asked for full-frame and a long hold. Entertainment video_type required.
    w = [{"word": f"w{i}", "start_ms": i * 400, "end_ms": i * 400 + 350} for i in range(60)]
    plan = {"broll": [{"range": [400, 700], "cue": "wait what", "query": "side eye",
                       "source": "stock", "mode": "full", "need": "meme"}]}
    d = assemble_edl(plan, w, "talking_head", "myth-buster",
                     brief={"video_type": "freestyle_rant"}).model_dump()
    assert len(d["broll"]) == 1
    b = d["broll"][0]
    assert b["mode"] == "panel", "memes keep the face → panel, never full"
    assert b["src_out"] - b["src_in"] <= 75, "a joke held too long dies"


def test_meme_caps_per_type_and_hook_block():
    # v2: memes everywhere, per-type capped — entertainment ≤5, educational ≤2; hook-block holds.
    w = [{"word": f"w{i}", "start_ms": i * 400, "end_ms": i * 400 + 350} for i in range(200)]
    memes = [{"range": [g, g + 30], "cue": f"joke{i}", "query": "mind blown",
              "source": "stock", "mode": "panel", "need": "meme"}
             for i, g in enumerate((40, 400, 700, 1000, 1300, 1600, 1900))]  # 7; first ON the hook
    ent = assemble_edl({"broll": memes}, w, "talking_head", "myth-buster",
                       brief={"video_type": "story"}).model_dump()
    ent_memes = [b for b in ent["broll"] if b.get("need") == "meme"]
    assert len(ent_memes) <= 5, "entertainment memes cap at 5"
    assert all(b["src_in"] >= 90 for b in ent_memes), "no meme on the hook"
    # educational video_type → cap 2 (punchline beats only — placement is doctrine, cap is code)
    info = assemble_edl({"broll": memes}, w, "talking_head", "myth-buster",
                        brief={"video_type": "tutorial"}).model_dump()
    assert len([b for b in info["broll"] if b.get("need") == "meme"]) <= 2, "educational memes cap at 2"


def test_buried_hook_pulled_forward_passes_invariant():
    fx = fixture("buried-hook-01")
    hook_f = ms_to_frame(fx["hook_ms"])
    plan = {"open_on": {"start": hook_f, "end": hook_f + 60, "why": "the real payoff"}}
    edl = assemble_edl(plan, fx["words"], "talking_head", "myth-buster")
    plan_out = build_render_plan(edl.model_dump())
    out = edl_eval._map_source_to_output(plan_out, hook_f)
    assert out is not None and out <= edl_eval.HOOK_MAX_OUT_FRAMES


def test_loop_friendly_trailing_trim():
    # a keep that extends well past the last spoken word must be trimmed to ≤10 trailing frames
    w = [{"word": f"w{i}", "start_ms": i * 300, "end_ms": i * 300 + 250} for i in range(15)]
    last_end = ms_to_frame(w[-1]["end_ms"])
    edl = assemble_edl({"keeps": [[0, last_end + 200]]}, w, "talking_head", "x")
    tail = edl.model_dump()["segments"][-1]
    assert tail["src_out"] - last_end <= 10


def test_prefs_disable_broll_and_punchins():
    w = _words("scripted-01")
    plan = {"broll": [{"range": [200, 260], "cue": "c", "query": "q", "source": "stock"}],
            "punch_ins": [{"frame": 150, "scale": 1.08, "why": "key"}]}
    edl = assemble_edl(plan, w, "talking_head", "myth-buster",
                       prefs={"broll": False, "punch_ins": False})
    d = edl.model_dump()
    assert d["broll"] == []
    assert not any(o["type"] == "punch_in" for o in d["overlays"])


def test_assembled_edls_pass_edl_eval_invariants():
    # A competent plan opens on the hook (identity for hook-at-0 takes; pulls forward the
    # buried hook). The empty-plan default is a safe whole-take cut and legitimately can't
    # rescue a buried hook — that's the LLM's editorial job, covered by the test above.
    for fx in FIXTURES:
        hook_f = ms_to_frame(fx["hook_ms"]) if fx.get("hook_ms") else 0
        plan = {"open_on": {"start": hook_f, "end": hook_f + 60, "why": "hook"}} if hook_f else {}
        edl = assemble_edl(plan, fx["words"], fx["style"], "reel")
        r = edl_eval.evaluate_edl(edl.model_dump(), fx["words"], fx.get("hook_ms") or 0)
        assert r["failures"] == [], f"{fx['id']}: {r['failures']}"


# --- invariant checker --------------------------------------------------------

def test_check_edl_invariants_clean_edl():
    w = _words("scripted-01")
    edl = assemble_edl({}, w, "talking_head", "myth-buster")
    assert check_edl_invariants(edl.model_dump(), w) == []


def test_check_edl_invariants_catches_bad_segment():
    w = _words("scripted-01")
    bad = {"style": "talking_head", "segments": [{"src_in": 100, "src_out": 50}],
           "layout": {"style": "talking_head"}}
    issues = check_edl_invariants(bad, w)
    assert any("src_out<=src_in" in i for i in issues)


def test_check_edl_invariants_catches_react_on_nonduet():
    w = _words("scripted-01")
    edl = assemble_edl({}, w, "talking_head", "myth-buster").model_dump()
    edl["react_schedule"] = [{"state": "play", "src_in": 0, "src_out": 30, "clip_from": 0, "audio_gain": 1.0}]
    assert any("react" in i for i in check_edl_invariants(edl, w))


# --- schema strictness --------------------------------------------------------

def test_edit_plan_schema_strict():
    def _strict(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                props = node.get("properties", {})
                assert set(node.get("required", [])) == set(props.keys())
                for v in props.values():
                    _strict(v)
            if node.get("type") == "array":
                _strict(node.get("items", {}))
    _strict(prompts.EDIT_PLAN_JSON_SCHEMA)


def test_edit_plan_prompt_builds():
    w = _words("scripted-01")
    script = {"hook": "h", "body": "b", "cta": "c", "formatId": "myth-buster", "shotPlan": []}
    system, user = prompts.edit_plan_prompt("talking_head", w, script, {}, brief={"video_type": "education"})
    assert "EDIT PLAN" in system and "EDITING KNOWLEDGE BASE" in system
    assert "transcript" in user.lower()


# --- A7 theme line + A9 genre profile injection ---------------------------------

def test_edit_plan_prompt_omits_theme_line_when_no_theme():
    w = _words("scripted-01")
    script = {"hook": "h", "body": "b", "cta": "c", "formatId": "myth-buster", "shotPlan": []}
    _, user = prompts.edit_plan_prompt("talking_head", w, script, {})
    assert "THEME:" not in user


def test_edit_plan_prompt_includes_theme_line_when_theme_given():
    w = _words("scripted-01")
    script = {"hook": "h", "body": "b", "cta": "c", "formatId": "myth-buster", "shotPlan": []}
    _, user = prompts.edit_plan_prompt("talking_head", w, script, {},
                                       theme_label="Hormozi Punch", theme_blurb="Maximum retention pressure.")
    assert "THEME:" in user
    assert "Hormozi Punch" in user
    assert "Maximum retention pressure." in user


def test_edit_plan_prompt_includes_genre_line_for_known_video_type():
    w = _words("scripted-01")
    script = {"hook": "h", "body": "b", "cta": "c", "formatId": "myth-buster", "shotPlan": []}
    _, user = prompts.edit_plan_prompt("talking_head", w, script, {},
                                       brief={"video_type": "freestyle_rant"})
    assert "GENRE (freestyle_rant)" in user
    assert "dense" in user


def test_edit_plan_prompt_omits_genre_line_for_reaction_and_other():
    w = _words("scripted-01")
    script = {"hook": "h", "body": "b", "cta": "c", "formatId": "myth-buster", "shotPlan": []}
    for vt in ("reaction", "other", "unknown-type", ""):
        _, user = prompts.edit_plan_prompt("talking_head", w, script, {}, brief={"video_type": vt})
        assert "GENRE (" not in user


# --- A9 genre profile table -----------------------------------------------------

def test_every_video_type_maps_to_a_complete_genre_profile():
    for vt in prompts.VIDEO_TYPES:
        assert vt in prompts.GENRE_PROFILES, f"{vt} missing from GENRE_PROFILES"
        profile = prompts.GENRE_PROFILES[vt]
        assert isinstance(profile, dict)   # possibly empty (reaction/other), never absent


def test_reaction_and_other_are_intentionally_empty_profiles():
    assert prompts.GENRE_PROFILES["reaction"] == {}
    assert prompts.GENRE_PROFILES["other"] == {}


def test_genre_profiles_with_interrupt_density_use_valid_values():
    # Must match app/retention.py's _DENSITY_MULT vocabulary exactly — a value
    # outside this set would silently no-op (fall back to the 1.0 multiplier)
    # when schedule_interrupts consumes genre_density.
    valid = {"calm", "standard", "dense"}
    for vt, profile in prompts.GENRE_PROFILES.items():
        density = profile.get("interrupt_density")
        if density is not None:
            assert density in valid, f"{vt}: unexpected interrupt_density {density!r}"


# --- authoring path -----------------------------------------------------------

def test_author_via_plan_keyless_produces_valid_edl(monkeypatch):
    import main
    monkeypatch.setattr(main, "ANTHROPIC_KEY", "")   # keyless → empty plan → whole-take default
    job = {"brand": {}, "edit_brief": None, "dossier": None, "reference_reel": None,
           "custom_instructions": ""}
    w = _words("scripted-01")
    script = {"formatId": "myth-buster"}
    edl_data, llm_contributed, plan_data = _run(main._author_edl_via_plan(job, "talking_head", script, w, {}, None))
    assert edl_data is not None
    assert llm_contributed is False   # keyless → empty plan → generic whole-take cut
    assert plan_data == {}           # P5: no LLM contribution → nothing to extract hints from
    assert check_edl_invariants(edl_data, w) == []


def _alpha_words(n, ms_step=400):
    _nouns = ["interface", "product", "founder", "growth", "metric", "startup",
              "revenue", "customer", "platform", "strategy"]
    return [{"word": _nouns[i % len(_nouns)], "start_ms": i * ms_step, "end_ms": i * ms_step + 350}
            for i in range(n)]


def test_partial_holds_capped_per_need():
    # Realism pass: panel/card caps are now entity 75 / evidence 90 / action 105 (was 90/120/150).
    w = _alpha_words(120)
    for need, cap in (("entity", 75), ("evidence", 90), ("action", 105)):
        plan = {"broll": [{"range": [300, 540], "cue": "x", "query": "x", "source": "stock",
                           "mode": "panel", "need": need}]}
        d = assemble_edl(plan, w, "talking_head", "myth-buster").model_dump()
        assert d["broll"], f"{need} panel dropped"
        assert d["broll"][0]["src_out"] - d["broll"][0]["src_in"] <= cap, f"{need} exceeds {cap}"


def test_long_phrase_biases_short_not_max():
    # A 300f phrase (>> 2× action's 75f full cap) biases to lo+15 = 51 (±6f jitter), never
    # pinned at the 75f max.
    w = _alpha_words(120)
    plan = {"broll": [{"range": [300, 600], "cue": "x", "query": "x", "source": "stock",
                       "mode": "full", "need": "action"}]}
    d = assemble_edl(plan, w, "talking_head", "myth-buster").model_dump()
    assert d["broll"]
    hold = d["broll"][0]["src_out"] - d["broll"][0]["src_in"]
    assert 45 <= hold <= 57, f"long phrase must bias short (51±6), got {hold}"


def test_topup_fires_with_one_weak_plan_cue():
    # Realism pass: the floor is no longer only-on-empty — ONE weak plan cue no longer suppresses
    # density (old behavior: exactly 1 insert). coverage=full tops up the gaps toward ~1 per 9s
    # (best-effort, bounded by the 40% floor budget + spacing + available content words).
    w = _alpha_words(120)                       # ~48s
    plan = {"broll": [{"range": [150, 210], "cue": "one", "query": "one", "source": "stock",
                       "mode": "full", "need": "action"}]}
    d = assemble_edl(plan, w, "broll_cutaway", "myth-buster",
                     prefs={"broll": True, "broll_coverage": "full", "broll_mode": "full"}).model_dump()
    assert len(d["broll"]) >= 4, "one weak cue must not suppress the density top-up"


def test_topup_respects_occupied_windows_and_spacing():
    # No top-up cutaway lands within `spacing` (60f under coverage=full) of the plan's own cutaway.
    w = _alpha_words(120)
    plan = {"broll": [{"range": [300, 360], "cue": "one", "query": "one", "source": "stock",
                       "mode": "full", "need": "action"}]}
    d = assemble_edl(plan, w, "broll_cutaway", "myth-buster",
                     prefs={"broll": True, "broll_coverage": "full", "broll_mode": "full"}).model_dump()
    ins = sorted(d["broll"], key=lambda b: b["src_in"])
    for a, b in zip(ins, ins[1:]):
        assert b["src_in"] - a["src_out"] >= 60, "top-up violated spacing"


def test_entertainment_tightens_spacing_not_coverage():
    # v2 genre dial: ENTERTAINMENT tightens spacing to 45f (jitter 30..75); coverage=full alone
    # does NOT (educational keeps ≥90f, jitter 75..120). Gap ~80f: always survives entertainment,
    # always drops on the plain educational take.
    w = _alpha_words(60)
    plan = {"broll": [
        {"range": [150, 200], "cue": "a", "query": "a", "source": "stock", "mode": "full", "need": "action"},
        {"range": [300, 350], "cue": "b", "query": "b", "source": "stock", "mode": "full", "need": "action"},
    ]}
    ent = assemble_edl(plan, w, "broll_cutaway", "myth-buster",
                       brief={"video_type": "freestyle_rant"},
                       prefs={"broll": True}).model_dump()
    plain = assemble_edl(plan, w, "broll_cutaway", "myth-buster",
                         prefs={"broll": True, "broll_coverage": "full"}).model_dump()
    assert any(b["cue_text"] == "a" for b in ent["broll"]) and any(b["cue_text"] == "b" for b in ent["broll"]), \
        "entertainment spacing must admit the tight pair"
    assert len([b for b in plain["broll"] if b["cue_text"] in ("a", "b")]) == 1, \
        "educational spacing (≥90f even under coverage=full) drops one of the pair"
