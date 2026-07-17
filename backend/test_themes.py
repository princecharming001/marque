"""A7 (superintelligence epic) — style bundles. `apply_theme` is a pure, keyless
function (app/themes.py); the golden-diff contract (clean_creator == today's
exact default output) is the safety net that lets EDIT_THEMES flip on without
silently changing every existing take's look."""
from app import themes as themes_mod
from app.edl import EDL, CaptionOptions, Audio, build_render_plan


def _base_edl(**over) -> dict:
    edl = {"style": "talking_head", "format_id": "myth-buster",
          "segments": [{"src_in": 0, "src_out": 300}], "drops": [],
          "captions": [], "overlays": [], "broll": [],
          "layout": {"style": "talking_head", "panels": 1, "panel_boundaries": []},
          "audio": {"lufs_target": -14.0}}
    edl.update(over)
    return edl


# ---------------------------------------------------------------------------
# get_theme
# ---------------------------------------------------------------------------

def test_get_theme_known_id():
    t = themes_mod.get_theme("hormozi_punch")
    assert t.id == "hormozi_punch"


def test_get_theme_unknown_or_empty_falls_back_to_clean_creator():
    assert themes_mod.get_theme("").id == "clean_creator"
    assert themes_mod.get_theme(None).id == "clean_creator"
    assert themes_mod.get_theme("not-a-real-theme").id == "clean_creator"


def test_all_six_themes_present():
    ids = set(themes_mod.THEMES.keys())
    assert ids == {"clean_creator", "hormozi_punch", "docu_calm",
                   "energetic_pop", "faceless_explainer", "premium_brand"}


# ---------------------------------------------------------------------------
# clean_creator golden-diff: applying it is a total no-op relative to what an
# untouched EDL already defaults to (the safety proof for flipping EDIT_THEMES).
# ---------------------------------------------------------------------------

def test_clean_creator_is_a_noop_on_a_bare_edl():
    theme = themes_mod.get_theme("clean_creator")
    edl = _base_edl()
    out = themes_mod.apply_theme(edl, theme)
    assert out["caption_style"] == "clean"
    assert out["caption_options"]["font"] == "inter"
    assert out["caption_options"]["uppercase"] is False
    assert out["caption_options"]["grouping"] == "phrase"
    # v2 (E1): clean_creator is a no-op EXCEPT the "invisible polish" look — a subtle
    # finishing filter + hairline vignette (research: imperceptible default polish).
    assert out["look"] == {"filter": "finishing", "intensity": 0.55,
                           "adjust": {"brightness": 0.0, "contrast": 0.0, "saturation": 0.0,
                                     "temperature": 0.0, "vignette": 0.04}}


def test_clean_creator_render_plan_matches_no_theme_render_plan():
    """The strongest form of the golden-diff guarantee: build_render_plan's
    OUTPUT is identical whether or not clean_creator was applied first —
    EXCEPT audio.duck, which apply_theme always stamps EXPLICITLY (None ->
    a dict) even for clean_creator. That's a wire-format difference, not a
    functional one: clean_creator's duck values are defined to exactly match
    AudioMix.tsx's own fallback constants (DUCK_FACTOR/DUCK_WINDOW/DUCK_RAMP),
    so the RENDERED behavior is identical either way — asserted explicitly
    below rather than assumed."""
    theme = themes_mod.get_theme("clean_creator")
    edl_a = EDL(**_base_edl()).model_dump()
    edl_b = EDL(**themes_mod.apply_theme(_base_edl(), theme)).model_dump()
    plan_a = build_render_plan(edl_a)
    plan_b = build_render_plan(edl_b)
    plan_a.pop("schema_version", None); plan_b.pop("schema_version", None)
    assert plan_b["audio"]["duck"] == {"factor": 0.35, "window_f": 15, "ramp_f": 8}
    plan_a["audio"] = dict(plan_a["audio"]); plan_a["audio"]["duck"] = plan_b["audio"]["duck"]
    # v2 (E1): the ONLY other permitted difference is the invisible-polish look.
    assert plan_b["look"] == {"filter": "finishing", "intensity": 0.55, "grain": 0.0,
                              "adjust": {"brightness": 0.0, "contrast": 0.0, "saturation": 0.0,
                                        "temperature": 0.0, "vignette": 0.04}}
    plan_a["look"] = plan_b["look"]
    assert plan_a == plan_b


# ---------------------------------------------------------------------------
# apply_theme: precedence (creator prefs > theme > style default) + provenance
# ---------------------------------------------------------------------------

def test_apply_theme_stamps_theme_id():
    out = themes_mod.apply_theme(_base_edl(), themes_mod.get_theme("hormozi_punch"))
    assert out["theme_id"] == "hormozi_punch"


def test_apply_theme_overrides_caption_style_when_prefs_silent():
    out = themes_mod.apply_theme(_base_edl(caption_style="clean"), themes_mod.get_theme("hormozi_punch"))
    assert out["caption_style"] == "bold-word"
    assert out["caption_options"]["font"] == "anton"
    assert out["caption_options"]["uppercase"] is True
    assert out["caption_options"]["stroke_px"] == 10


def test_apply_theme_never_overrides_explicit_creator_prefs():
    out = themes_mod.apply_theme(_base_edl(caption_style="clean"), themes_mod.get_theme("hormozi_punch"),
                                 prefs={"caption_style": "karaoke"})
    assert out["caption_style"] == "clean"   # untouched — creator prefs win


def test_apply_theme_does_not_clobber_already_set_caption_option_fields():
    edl = _base_edl(caption_options={"font": "baloo"})
    out = themes_mod.apply_theme(edl, themes_mod.get_theme("hormozi_punch"))
    assert out["caption_options"]["font"] == "baloo"       # already set — theme doesn't touch it
    assert out["caption_options"]["stroke_px"] == 10        # unset — theme fills it in


# --- force=True (the "Change theme" retheme action on an ALREADY-finished clip) ---

def test_apply_theme_force_overwrites_previously_stamped_fields():
    # Simulate a clip already carrying hormozi_punch's stamped values.
    edl = _base_edl(theme_id="hormozi_punch", caption_style="bold-word",
                    caption_options={"font": "anton", "uppercase": True, "stroke_px": 10},
                    look={"filter": "vivid", "intensity": 0.7, "adjust": {}})
    out = themes_mod.apply_theme(edl, themes_mod.get_theme("docu_calm"), force=True)
    assert out["theme_id"] == "docu_calm"
    assert out["caption_style"] == "clean"
    assert out["caption_options"]["font"] == "inter"
    assert out["look"]["filter"] == "warm"


def test_apply_theme_force_still_ignores_creator_prefs_by_design():
    # force is for an EXPLICIT theme-switch action — it intentionally does NOT
    # consult prefs (the whole point is the creator just asked for this).
    out = themes_mod.apply_theme(_base_edl(caption_style="clean"), themes_mod.get_theme("hormozi_punch"),
                                 prefs={"caption_style": "karaoke"}, force=True)
    assert out["caption_style"] == "bold-word"


def test_apply_theme_stamps_look_when_absent():
    out = themes_mod.apply_theme(_base_edl(), themes_mod.get_theme("hormozi_punch"))
    assert out["look"]["filter"] == "vivid"


def test_apply_theme_does_not_clobber_an_existing_filter():
    edl = _base_edl(look={"filter": "mono", "intensity": 0.5, "adjust": {}})
    out = themes_mod.apply_theme(edl, themes_mod.get_theme("hormozi_punch"))
    assert out["look"]["filter"] == "mono"


def test_apply_theme_stamps_duck_only_when_absent():
    out = themes_mod.apply_theme(_base_edl(), themes_mod.get_theme("hormozi_punch"))
    assert out["audio"]["duck"] == {"factor": 0.25, "window_f": 12, "ramp_f": 4}

    edl = _base_edl(audio={"lufs_target": -14.0, "duck": {"factor": 0.9, "window_f": 1, "ramp_f": 1}})
    out2 = themes_mod.apply_theme(edl, themes_mod.get_theme("hormozi_punch"))
    assert out2["audio"]["duck"] == {"factor": 0.9, "window_f": 1, "ramp_f": 1}   # untouched


def test_apply_theme_never_sets_audio_music_url_directly():
    # A5b/A7 ordering bug guard: apply_theme must NEVER stamp a dead
    # audio.music placeholder — that would block _apply_plan_music_vibe's
    # real track resolution (see themes.py's own note on this).
    out = themes_mod.apply_theme(_base_edl(), themes_mod.get_theme("energetic_pop"))
    assert out["audio"].get("music") is None


def test_apply_theme_does_not_mutate_input():
    edl = _base_edl()
    before = dict(edl)
    themes_mod.apply_theme(edl, themes_mod.get_theme("hormozi_punch"))
    assert edl == before


def test_apply_theme_output_round_trips_through_edl_model():
    out = themes_mod.apply_theme(_base_edl(), themes_mod.get_theme("hormozi_punch"))
    dumped = EDL(**out).model_dump()
    assert dumped["theme_id"] == "hormozi_punch"
    assert dumped["caption_options"]["stroke_px"] == 10
