"""A7 (superintelligence epic) — coherent STYLE BUNDLES. Research finding #1: the
single most recognizable "amateur" tell isn't any one mistake, it's a MIXED
grammar — Hormozi captions over a docu-calm grade with karaoke-density
interrupts. A theme is one named bundle that pins captions + transitions +
framing + interrupts + sfx + music + grade + hook style together so they never
clash. `apply_theme` stamps those choices onto an EDL as DEFAULTS (creator
prefs and an explicit LLM plan choice both still win — see its docstring);
the other retention passes read the same `theme` object for their own
behavior knobs (framing scales/rotation, interrupt density/jitter/types, sfx
gain) so downstream output actually reflects the bundle, not just its labels.

Flag-gated via EDIT_THEMES (main.py) — until it's on, `job["_theme"]` is
always None and every pass's `theme=None` default keeps today's behavior
byte-identical (see each pass's own theme handling)."""
from __future__ import annotations
import copy
from pydantic import BaseModel


class Theme(BaseModel):
    id: str
    label: str
    blurb: str
    caption: dict     # {style, options: {font, uppercase, accent, grouping, pos_y, stroke_px, ...}}
    transitions: dict # {styles: [...], frames, at}
    framing: dict      # {enabled, scales: {wide,mid,close}, rotate_s: [lo,hi], origin_y}
    interrupts: dict  # {density, jitter_frames: [lo,hi], types: [...], max_types}
    sfx: dict          # {gain_db}
    music: dict        # {vibe, volume, duck: {factor, window_f, ramp_f}}
    grade: dict        # Look-shaped dict {filter, intensity, adjust: {...}}
    hook: dict         # {sticker_bg, sticker_font, impact_sfx}


# clean_creator MUST compile to today's exact default output (golden-diff
# enforced by test_themes.py) — every value here matches what an untouched
# EDL/CaptionOptions/Look/MusicTrack already defaults to.
THEMES: dict[str, Theme] = {
    "clean_creator": Theme(
        id="clean_creator", label="Clean Creator",
        blurb="The reliable default: quiet captions, natural pacing, no gimmicks.",
        caption={"style": "clean", "options": {"font": "inter", "uppercase": False,
                                               "grouping": "phrase"}},
        transitions={"styles": ["fade_black"], "frames": 8, "at": "section_breaks"},
        framing={"enabled": False, "scales": {"wide": 1.0, "mid": 1.18, "close": 1.35},
                "rotate_s": [5, 8], "origin_y": 0.38},
        interrupts={"density": "standard", "jitter_frames": [90, 150],
                   "types": ["punch", "text_sticker"], "max_types": 2},
        sfx={"gain_db": -14},
        music={"vibe": "", "volume": 0.12, "duck": {"factor": 0.35, "window_f": 15, "ramp_f": 8}},
        grade={"filter": None, "intensity": 1.0,
              "adjust": {"brightness": 0.0, "contrast": 0.0, "saturation": 0.0,
                        "temperature": 0.0, "vignette": 0.0}},
        hook={"sticker_bg": "box", "sticker_font": "inter", "impact_sfx": False},
    ),
    "hormozi_punch": Theme(
        id="hormozi_punch", label="Hormozi Punch",
        blurb="Anton caps, thick gold outline, dense interrupts — maximum retention pressure.",
        caption={"style": "bold-word",
                "options": {"font": "anton", "uppercase": True, "accent": "#FFD93D",
                           "grouping": "word", "stroke_px": 10, "sync_lead_frames": 4}},
        transitions={"styles": ["flash", "zoom_punch"], "frames": 6, "at": "all_cuts"},
        framing={"enabled": True, "scales": {"wide": 1.0, "mid": 1.2, "close": 1.4},
                "rotate_s": [4, 6], "origin_y": 0.38},
        interrupts={"density": "dense", "jitter_frames": [60, 100],
                   "types": ["punch", "framing_pop", "text_sticker"], "max_types": 3},
        sfx={"gain_db": -12},
        music={"vibe": "driving", "volume": 0.16, "duck": {"factor": 0.25, "window_f": 12, "ramp_f": 4}},
        grade={"filter": "vivid", "intensity": 0.7,
              "adjust": {"brightness": 0.0, "contrast": 0.1, "saturation": 0.08,
                        "temperature": 0.0, "vignette": 0.0}},
        hook={"sticker_bg": "box", "sticker_font": "anton", "impact_sfx": True},
    ),
    "docu_calm": Theme(
        id="docu_calm", label="Docu Calm",
        blurb="Clean captions, minimal interrupts, a warm grade — lets the story breathe.",
        caption={"style": "clean", "options": {"font": "inter", "uppercase": False,
                                               "grouping": "phrase"}},
        transitions={"styles": ["fade_black"], "frames": 10, "at": "section_breaks"},
        framing={"enabled": False, "scales": {"wide": 1.0, "mid": 1.12, "close": 1.22},
                "rotate_s": [7, 10], "origin_y": 0.38},
        interrupts={"density": "calm", "jitter_frames": [150, 220],
                   "types": ["punch"], "max_types": 1},
        sfx={"gain_db": -18},
        music={"vibe": "chill", "volume": 0.10, "duck": {"factor": 0.4, "window_f": 15, "ramp_f": 10}},
        grade={"filter": "warm", "intensity": 0.6,
              "adjust": {"brightness": 0.0, "contrast": 0.0, "saturation": -0.03,
                        "temperature": 0.08, "vignette": 0.05}},
        hook={"sticker_bg": "none", "sticker_font": "inter", "impact_sfx": False},
    ),
    "energetic_pop": Theme(
        id="energetic_pop", label="Energetic Pop",
        blurb="Karaoke captions, dense jitter, flash transitions, upbeat bed — recap energy.",
        caption={"style": "karaoke", "options": {"font": "baloo", "uppercase": False,
                                                 "grouping": "phrase", "highlight_persist_frames": 6}},
        transitions={"styles": ["flash"], "frames": 6, "at": "all_cuts"},
        framing={"enabled": True, "scales": {"wide": 1.0, "mid": 1.15, "close": 1.3},
                "rotate_s": [5, 7], "origin_y": 0.38},
        interrupts={"density": "dense", "jitter_frames": [70, 110],
                   "types": ["punch", "framing_pop"], "max_types": 2},
        sfx={"gain_db": -13},
        music={"vibe": "upbeat", "volume": 0.22, "duck": {"factor": 0.3, "window_f": 12, "ramp_f": 5}},
        grade={"filter": "vivid", "intensity": 0.5,
              "adjust": {"brightness": 0.02, "contrast": 0.06, "saturation": 0.1,
                        "temperature": 0.0, "vignette": 0.0}},
        hook={"sticker_bg": "box", "sticker_font": "baloo", "impact_sfx": True},
    ),
    "faceless_explainer": Theme(
        id="faceless_explainer", label="Faceless Explainer",
        blurb="B-roll-forward, bold-word captions, a ducked bed — built for voiceover recaps.",
        caption={"style": "bold-word", "options": {"font": "archivo", "uppercase": True,
                                                    "grouping": "word"}},
        transitions={"styles": ["fade_black"], "frames": 8, "at": "section_breaks"},
        framing={"enabled": False, "scales": {"wide": 1.0, "mid": 1.15, "close": 1.3},
                "rotate_s": [6, 9], "origin_y": 0.38},
        interrupts={"density": "standard", "jitter_frames": [90, 150],
                   "types": ["text_sticker"], "max_types": 1},
        sfx={"gain_db": -15},
        music={"vibe": "steady", "volume": 0.14, "duck": {"factor": 0.3, "window_f": 15, "ramp_f": 8}},
        grade={"filter": "film", "intensity": 0.6,
              "adjust": {"brightness": 0.0, "contrast": 0.05, "saturation": 0.0,
                        "temperature": -0.03, "vignette": 0.03}},
        hook={"sticker_bg": "box", "sticker_font": "archivo", "impact_sfx": False},
    ),
    "premium_brand": Theme(
        id="premium_brand", label="Premium Brand",
        blurb="Minimal captions, a film grade, sparse whip transitions — no emoji, no clutter.",
        caption={"style": "clean", "options": {"font": "inter", "uppercase": False,
                                               "grouping": "line"}},
        transitions={"styles": ["whip"], "frames": 6, "at": "section_breaks"},
        framing={"enabled": False, "scales": {"wide": 1.0, "mid": 1.1, "close": 1.2},
                "rotate_s": [8, 12], "origin_y": 0.38},
        interrupts={"density": "calm", "jitter_frames": [150, 220],
                   "types": ["punch"], "max_types": 1},
        sfx={"gain_db": -18},
        music={"vibe": "steady", "volume": 0.08, "duck": {"factor": 0.4, "window_f": 15, "ramp_f": 10}},
        grade={"filter": "film", "intensity": 0.5,
              "adjust": {"brightness": 0.0, "contrast": 0.04, "saturation": -0.05,
                        "temperature": -0.02, "vignette": 0.04}},
        hook={"sticker_bg": "none", "sticker_font": "inter", "impact_sfx": False},
    ),
}

DEFAULT_THEME_ID = "clean_creator"


def get_theme(theme_id: str | None) -> Theme:
    """Never raises — an unknown/empty id falls back to clean_creator (the
    golden-diff no-op theme), same fail-soft philosophy as every other lookup
    in this codebase."""
    return THEMES.get(theme_id or "", THEMES[DEFAULT_THEME_ID])


def apply_theme(edl: dict, theme: Theme, prefs: dict | None = None) -> dict:
    """A7: stamp the theme's caption/grade/music choices onto the EDL as
    DEFAULTS. Precedence: creator prefs (explicit per-request toggles in
    `prefs`) > theme > whatever the author path already defaulted to (e.g.
    assemble_edl's own "clean"/no-grade/no-music fallbacks) — those fall
    under "style defaults" in the plan's own precedence language, which a
    theme is allowed to override. Writes `edl["theme_id"]` so downstream
    passes/the report card know which bundle produced this take."""
    edl = copy.deepcopy(edl)
    prefs = prefs or {}
    edl["theme_id"] = theme.id

    if prefs.get("caption_style") is None:
        edl["caption_style"] = theme.caption.get("style", edl.get("caption_style", "clean"))
    cur_opts = dict(edl.get("caption_options") or {})
    for k, v in (theme.caption.get("options") or {}).items():
        if cur_opts.get(k) is None:
            cur_opts[k] = v
    edl["caption_options"] = cur_opts

    if not (edl.get("look") or {}).get("filter"):
        edl["look"] = theme.grade

    # NOTE: theme.music's vibe/volume are NOT applied here — actual track
    # resolution (main._select_music_track) needs a live import of main.py's
    # catalog, which would be circular (main.py already imports this module).
    # The theme's vibe/volume are instead threaded into the EXISTING
    # _apply_plan_music_vibe() call site in main.py's _run_edit, as a fallback
    # under the LLM plan's own music.vibe hint — see that call site.
    # audio.duck IS safe to stamp here: it's a brand-new field with no other
    # writer anywhere in the authoring pipeline, independent of which track
    # (if any) _apply_plan_music_vibe ends up selecting.
    audio = dict(edl.get("audio") or {"lufs_target": -14.0})
    if audio.get("duck") is None and theme.music.get("duck"):
        audio["duck"] = theme.music["duck"]
        edl["audio"] = audio

    return edl
