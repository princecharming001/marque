#!/usr/bin/env python3
"""CP-2 — the style ARCHETYPE bank: 12 named editing styles + a pre-rendered
sample of each, for the settings-page "this is how your edits look" picker.

TWO ARTIFACTS, ONE SOURCE OF TRUTH
  backend/assets/style_archetypes.json   the 12 archetypes (vector + resolved config)
  backend/assets/style_samples.json      a ~10s rendered sample per archetype

Both are already wired into live code that is currently dead for want of the
files: app/style_profile.archetypes() reads style_archetypes.json (and returns
[] today, which silently disables nearest_archetype() and stops
map_profile_to_config() from ever emitting a theme_id), and GET /v1/style-deck
serves `archetypes` + `samples` straight out of these two files. Generating
them here ACTIVATES that path rather than adding a parallel one.

WHY 12 = 6 THEMES x 2 POLES
app/themes.py already owns the "coherent bundle" decision (captions +
transitions + grade + interrupts pinned together so they can't clash — the
mixed-grammar amateur tell). An archetype is a THEME AT AN INTENSITY: the same
bundle, dialled calm or loud. Poles rather than a continuous slider because the
swipe quiz produces a point in 8-space and `nearest_archetype` needs a small
set of well-separated landmarks to snap to; 12 landmarks over 6 bundles keeps
every theme reachable from either end of the intensity range.

The `lite` vectors are hand-set to READ each theme's own declared grammar
(hormozi_punch's anton/uppercase/stroke_px-10/word-grouping captions and dense
interrupts -> caption_boldness .95 / chunking 1.0 / pace .85; docu_calm's
clean captions, calm interrupts and section-break-only transitions -> low
everything). clean_creator_lite is style_profile.COLD_START *exactly*, which is
what makes the module's own promise true — "a creator who skips the quiz maps
to clean_creator" only holds if the cold-start vector sits at zero distance
from that archetype. The script asserts it.

The `max` pole adds MAX_BUMP to pace/energy/broll_density/title_cta_flair
(clamped) — the four dims that mean "louder", leaving caption grammar and
b-roll composition (the bundle's identity) untouched, so a pole is an
intensity change and never a different-looking style.

resolved_config is produced by style_profile.map_profile_to_config(), the
SAME formula a swipe-derived profile goes through — an archetype must not get
its knobs from a private second mapping or the settings preview would lie
about what the quiz will actually do. Two keys are then overridden:
  theme_id      the mapper resolves it via nearest_archetype(), which reads the
                very file being written (chicken-and-egg); we already know it.
  caption_style the mapper only ever emits clean|bold-word because it works
                from a vector; an archetype IS a named bundle, so the theme's
                own caption style (incl. karaoke) has to survive.

HOW A SAMPLE IS RENDERED (real pipeline path, no LLM, no network)
Base material is a REAL authored EDL from eval/cutloop — the production
pipeline's own output for a real raw take, with real cuts and real word-timed
captions. Synthetic testsrc2 (render/fixtures/source.mp4, what format_eval
uses) is right for a format regression check and wrong for a creator-facing
picker: nobody picks an editing style off colour bars.

Per archetype we then:
  1. re-cut that take's kept material into a ~10s window, with the CUT COUNT
     driven by the vector's pace dim through style_profile.ANCHORS' own
     definition (pace 0..1 <-> 2..12 cuts per 30s);
  2. insert b-roll per broll_density (segments/30s) and broll_share (fraction
     of runtime), composited full/card/panel per broll_overlay_bias;
  3. add the theme's hook sticker when title_cta_flair clears the bar;
  4. run themes.apply_theme(force=True) — the real function, which stamps the
     bundle's caption style/options and colour grade;
  5. build_render_plan() — the real EDL->plan contract; and
  6. `npx remotion render` locally against a local HTTP server for the source,
     reusing eval/format_eval's own `_start_source_server` harness.

So the difference a creator sees between two samples is produced by the same
code that will edit their video, not by a mockup.

Usage:
    python3 backend/scripts/build_style_samples.py --archetypes-only
    python3 backend/scripts/build_style_samples.py --only hormozi_punch_max
    python3 backend/scripts/build_style_samples.py --verify
    python3 backend/scripts/build_style_samples.py --upload --verify
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))          # backend/ — for app.* and eval.*
import asset_store as store  # noqa: E402

from app import style_profile, themes  # noqa: E402
from app.edl import build_render_plan  # noqa: E402
from eval.format_eval import COMPOSITION_ID, HTTP_PORT, _start_source_server  # noqa: E402

ARCHETYPES_PATH = store.ASSETS_DIR / "style_archetypes.json"
SAMPLES_PATH = store.ASSETS_DIR / "style_samples.json"
DEFAULT_OUT = store.RENDER_DIR / "out" / "style_samples"
STORAGE_PREFIX = "demo-assets/style-samples"

BANK_VERSION = 1
SCHEMA_VERSION = style_profile.SCHEMA_VERSION

FPS = 30
SAMPLE_SECONDS = 10.0
SAMPLE_FRAMES = int(SAMPLE_SECONDS * FPS)
# Verified band. The target is 10s; the plan's total_frames lands a little off
# it because segment lengths are integer frames divided by the take's own
# playback speed. 7.5-13.0 is "a real ~10s sample" and still fails loudly on a
# 0s stub or a full-take render.
MIN_DURATION_S, MAX_DURATION_S = 7.5, 13.0

# The take the samples are cut from. eval/cutloop/round_N/<id>.json is a real
# /v1/clips response (edl + words + source_url) captured by the cuts Ralph loop.
DEFAULT_TAKE = "procut"
CUTLOOP_DIR = SCRIPT_DIR.parent / "eval" / "cutloop"
# eval/cutloop/round_*/ is GITIGNORED (the loop's renders/jobs are disk bloat),
# so the take the shipped bank was cut from would vanish on a fresh checkout and
# the bank would be unreproducible. The first run pins the base response here,
# and every later run prefers this committed snapshot — so re-rendering the bank
# months from now reproduces the SAME samples, not whatever the latest Ralph
# round happened to author.
BASE_TAKE_SNAPSHOT = store.ASSETS_DIR / "style_sample_base_take.json"
# Downloaded once into render/fixtures/ and served over the local HTTP harness:
# a remote fetch repeated 12x is slow and makes the bank un-rebuildable offline.
LOCAL_SOURCE_NAME = "style_sample_source.mp4"
LOCAL_SOURCE = store.RENDER_DIR / "fixtures" / LOCAL_SOURCE_NAME

RENDER_TIMEOUT_S = 600

# Sample copy for the hook sticker — fixed across every archetype for the same
# reason the CTA bank uses one line of copy: the picker is comparing STYLES, and
# varying the words would make the difference read as a copy difference.
SAMPLE_HOOK_TEXT = "Watch this part"


# ---------------------------------------------------------------------------
# 1. The archetypes
# ---------------------------------------------------------------------------

# Per-theme `lite` vectors — see module docstring for how each was grounded in
# that theme's own declaration in app/themes.py.
LITE_VECTORS: dict[str, dict[str, float]] = {
    # The no-op default bundle. MUST equal COLD_START (asserted below).
    "clean_creator": dict(style_profile.COLD_START),

    # anton + uppercase + stroke_px 10 + word grouping -> maximum caption weight;
    # interrupts "dense" with flash/zoom_punch at ALL cuts -> top-end pace;
    # driving music -> high energy. Punch-in led, not b-roll led.
    "hormozi_punch": {
        "pace": 0.85, "energy": 0.80, "broll_density": 0.35, "broll_share": 0.30,
        "broll_overlay_bias": 0.35, "caption_boldness": 0.95, "caption_chunking": 1.00,
        "title_cta_flair": 0.75,
    },

    # "lets the story breathe": clean captions, calm interrupts, section-break
    # transitions only, sticker_bg none -> low on every dim.
    "docu_calm": {
        "pace": 0.12, "energy": 0.25, "broll_density": 0.20, "broll_share": 0.18,
        "broll_overlay_bias": 0.25, "caption_boldness": 0.10, "caption_chunking": 0.20,
        "title_cta_flair": 0.05,
    },

    # karaoke captions (colourful, not heavy — baloo, no caps, no stroke), dense
    # jitter, flash at all cuts, upbeat bed at the catalogue's loudest volume,
    # impact sfx on the hook -> high energy + flair, mid boldness.
    "energetic_pop": {
        "pace": 0.78, "energy": 0.90, "broll_density": 0.45, "broll_share": 0.35,
        "broll_overlay_bias": 0.45, "caption_boldness": 0.50, "caption_chunking": 0.40,
        "title_cta_flair": 0.85,
    },

    # "B-roll-forward ... built for voiceover recaps": the only bundle where
    # b-roll dominates the runtime, and it covers the frame (the face is not the
    # subject) -> high density/share, LOW overlay bias. archivo + uppercase +
    # word grouping -> high boldness/chunking.
    "faceless_explainer": {
        "pace": 0.45, "energy": 0.50, "broll_density": 0.85, "broll_share": 0.80,
        "broll_overlay_bias": 0.20, "caption_boldness": 0.70, "caption_chunking": 0.90,
        "title_cta_flair": 0.45,
    },

    # "Minimal captions ... no emoji, no clutter": line grouping is the longest
    # chunking there is, sticker_bg none, calm interrupts, sparse whips. What
    # b-roll it does use is a tasteful inset, not a cutaway -> high overlay bias.
    "premium_brand": {
        "pace": 0.25, "energy": 0.35, "broll_density": 0.30, "broll_share": 0.28,
        "broll_overlay_bias": 0.55, "caption_boldness": 0.08, "caption_chunking": 0.05,
        "title_cta_flair": 0.10,
    },
}

# The four "louder" dims and how far the max pole pushes them.
MAX_BUMP = 0.20
MAX_POLE_DIMS = ("pace", "energy", "broll_density", "title_cta_flair")

POLE_LABEL = {"lite": "Lite", "max": "Max"}


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, round(float(v), 4)))


def build_archetypes() -> list[dict]:
    """The 12 archetypes, derived (not hand-copied) from themes.py + style_profile."""
    out: list[dict] = []
    for theme_id, lite in LITE_VECTORS.items():
        theme = themes.get_theme(theme_id)
        for pole in ("lite", "max"):
            vector = {d: _clamp01(lite[d] + (MAX_BUMP if pole == "max" and d in MAX_POLE_DIMS else 0.0))
                      for d in style_profile.DIMS}
            cfg = dict(style_profile.map_profile_to_config(vector))
            cfg["theme_id"] = theme_id                              # see module docstring
            cfg["caption_style"] = theme.caption.get("style", cfg.get("caption_style", "clean"))
            out.append({
                "id": f"{theme_id}_{pole}",
                "label": f"{theme.label} — {POLE_LABEL[pole]}",
                "blurb": theme.blurb,
                "theme_id": theme_id,
                "pole": pole,
                "vector": vector,
                "resolved_config": cfg,
            })
    return out


def write_archetypes() -> list[dict]:
    arcs = build_archetypes()

    # Contract guard: style_profile's docstring promises an un-quizzed creator
    # lands on clean_creator. That is only true if COLD_START sits at distance 0
    # from clean_creator_lite — if someone retunes either vector, fail here
    # rather than silently changing what every no-quiz creator's edits look like.
    cold = style_profile.normalize(style_profile.COLD_START)
    nearest = min(arcs, key=lambda a: style_profile.distance(cold, a["vector"]))
    if nearest["id"] != "clean_creator_lite":
        raise SystemExit(
            f"[archetypes] COLD_START maps to {nearest['id']}, not clean_creator_lite — "
            "style_profile.COLD_START and LITE_VECTORS['clean_creator'] have drifted apart")

    store.write_manifest(ARCHETYPES_PATH, {
        "schema_version": SCHEMA_VERSION,
        "bank_version": BANK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dims": list(style_profile.DIMS),
        "archetypes": arcs,
    })
    print(f"[archetypes] wrote {ARCHETYPES_PATH} ({len(arcs)} archetypes)")
    return arcs


# ---------------------------------------------------------------------------
# 2. Vector -> EDL shape
#
# These invert style_profile.ANCHORS, which is what gives the numbers meaning:
# the ANCHORS table says a 0..1 `pace` is a clamped ramp over 2..12 cuts per
# 30s, so pace 0.85 literally means "about 10.5 cuts per 30s" and we cut the
# sample window to match. Nothing here is a free-floating magic constant.
# ---------------------------------------------------------------------------

MAX_CLIPS = 6          # a 10s window past ~6 cuts reads as a glitch, not a style
MAX_BROLL_INSERTS = 4
MIN_BROLL_FRAMES, MAX_BROLL_FRAMES = 18, 90   # 0.6s .. 3.0s per insert
BROLL_LEAD_IN = 8            # frames of the shot shown before a cutaway starts
HOOK_PROTECT_FRAMES = 45    # no insert over the first 1.5s — the pipeline's own
                            # b-roll grammar protects the hook the same way
# How full the ~10s window has to be before we stop adding shots (see _pick_segments).
WINDOW_FILL_MIN = 0.90
FLAIR_STICKER_MIN = 0.35    # below this the archetype doesn't want a title card
FLAIR_STICKER_FRAMES = 75   # 2.5s of hook sticker


def clips_for_pace(pace: float, window_frames: int) -> int:
    """pace -> how many cuts the sample window should contain."""
    lo, hi = style_profile.ANCHORS["pace"]           # cuts per 30s
    per_30s = lo + pace * (hi - lo)
    n = round(per_30s * window_frames / (30 * FPS))
    return max(1, min(MAX_CLIPS, n))


def broll_plan_for(vector: dict, window_frames: int) -> tuple[int, int]:
    """(insert_count, frames_per_insert) from broll_density + broll_share."""
    d_lo, d_hi = style_profile.ANCHORS["broll_density"]        # segments per 30s
    s_lo, s_hi = style_profile.ANCHORS["broll_share"]          # fraction of runtime
    per_30s = d_lo + vector.get("broll_density", 0.0) * (d_hi - d_lo)
    share = s_lo + vector.get("broll_share", 0.0) * (s_hi - s_lo)

    total_frames = int(share * window_frames)
    count = round(per_30s * window_frames / (30 * FPS))
    if total_frames < MIN_BROLL_FRAMES:
        return 0, 0
    # A high SHARE with a low density would ask for one enormous insert; split
    # it so no single cutaway runs past MAX_BROLL_FRAMES.
    count = max(count, math.ceil(total_frames / MAX_BROLL_FRAMES))
    count = max(1, min(MAX_BROLL_INSERTS, count))
    each = max(MIN_BROLL_FRAMES, min(MAX_BROLL_FRAMES, total_frames // count))
    return count, each


def broll_mode_for(overlay_bias: float) -> str:
    """overlay_bias -> BRoll.mode (full | card | panel).

    Same intent as map_profile_to_config's `broll_mode` pref, expressed in the
    EDL's own three-value vocabulary: more overlay appetite = more of the frame
    kept for the face.
    """
    if overlay_bias >= 0.55:
        return "panel"
    if overlay_bias >= 0.40:
        return "card"
    return "full"


# ---------------------------------------------------------------------------
# 3. Base take
# ---------------------------------------------------------------------------

def _describe(data: dict) -> str:
    edl = data.get("edl") or {}
    return (f"{len(edl.get('segments') or [])} segments, "
            f"{len(edl.get('captions') or [])} caption words")


def load_base_take(take_id: str) -> dict:
    """The real authored take the samples are cut from.

    Committed snapshot first (reproducible), newest eval/cutloop round second
    (and pinned to the snapshot on the way through).
    """
    snap = store.read_manifest(BASE_TAKE_SNAPSHOT)
    if snap and (snap.get("edl") or {}).get("segments") and snap.get("take_id") == take_id:
        print(f"[samples] base take: {BASE_TAKE_SNAPSHOT.name} (committed snapshot, "
              f"{_describe(snap)})")
        return snap

    if not CUTLOOP_DIR.is_dir():
        raise SystemExit(
            f"[samples] no committed snapshot at {BASE_TAKE_SNAPSHOT} and no {CUTLOOP_DIR} — "
            "run eval/cut_loop.py to author a take first")
    rounds = sorted((p for p in CUTLOOP_DIR.glob("round_*") if p.is_dir()),
                    key=lambda p: int(p.name.split("_")[1]), reverse=True)
    for rd in rounds:
        path = rd / f"{take_id}.json"
        if not path.is_file():
            continue
        data = json.loads(path.read_text())
        if not (data.get("edl") or {}).get("segments"):
            continue
        # Keep only what a sample render needs — the captured response also
        # carries the full word list, lint, briefs and per-clip status.
        snapshot = {
            "take_id": take_id,
            "captured_from": str(path.relative_to(SCRIPT_DIR.parent)),
            "source_url": data.get("source_url", ""),
            "edl": data["edl"],
        }
        store.write_manifest(BASE_TAKE_SNAPSHOT, snapshot)
        print(f"[samples] base take: {path} ({_describe(data)}) "
              f"-> pinned to {BASE_TAKE_SNAPSHOT.name}")
        return snapshot
    raise SystemExit(f"[samples] no usable EDL for take {take_id!r} under {CUTLOOP_DIR}")


def ensure_local_source(source_url: str) -> None:
    """Download the take once into render/fixtures/ for the local HTTP harness."""
    if LOCAL_SOURCE.is_file() and LOCAL_SOURCE.stat().st_size > store.MIN_ASSET_BYTES:
        return
    LOCAL_SOURCE.parent.mkdir(parents=True, exist_ok=True)
    print(f"[samples] downloading base take -> {LOCAL_SOURCE}")
    with urllib.request.urlopen(source_url, timeout=300) as resp:
        LOCAL_SOURCE.write_bytes(resp.read())
    ok, detail = store.check_video(str(LOCAL_SOURCE), 5.0, 3600.0)
    if not ok:
        raise SystemExit(f"[samples] downloaded base take is unusable: {detail}")
    print(f"[samples] base take ready: {detail}")


# ---------------------------------------------------------------------------
# 4. Archetype -> EDL -> render plan
# ---------------------------------------------------------------------------

def _kept_source_clips(base_edl: dict) -> list[dict]:
    """The take's real kept intervals, in playback order.

    Reuses build_render_plan itself rather than re-deriving segments-minus-drops
    — the plan's `clips` ARE the canonical kept list, so the sample is cut from
    exactly the material the production render would have shown.
    """
    plan = build_render_plan(copy.deepcopy(base_edl))
    return [c for c in (plan.get("clips") or []) if c["src_out"] > c["src_in"]]


def build_sample_edl(arc: dict, base: dict, source_url: str) -> tuple[dict, str]:
    """One archetype -> (render plan, composition id)."""
    base_edl = base["edl"]
    vector = arc["vector"]
    theme = themes.get_theme(arc["theme_id"])
    kept = _kept_source_clips(base_edl)
    if not kept:
        raise ValueError("base take has no kept clips")

    speed = float(kept[0].get("speed") or 1.0)
    # Ask for a SOURCE window long enough that, after the take's own playback
    # speed, the OUTPUT lands on ~SAMPLE_FRAMES.
    window_src = int(SAMPLE_FRAMES * speed)
    n_clips = clips_for_pace(vector.get("pace", 0.0), SAMPLE_FRAMES)
    per_clip = max(20, window_src // n_clips)

    segments = _pick_segments(kept, n_clips, per_clip, window_src)
    if not segments:
        raise ValueError("could not slice a sample window out of the base take")

    edl = copy.deepcopy(base_edl)
    edl["segments"] = segments
    edl["drops"] = []               # segments are already the kept material
    edl["segment_order"] = None
    # Everything below was computed for the FULL take's timeline and is stale
    # once we re-cut; a stale transition/end-card lands at the wrong frame.
    edl["transitions"] = []
    edl["end_card"] = None
    edl["watermark"] = None
    edl["montage"] = None
    edl["progress_bar"] = None
    edl["react_source"] = None
    edl["react_schedule"] = []
    edl["speech_frames"] = []
    edl["overlays"] = []
    audio = dict(edl.get("audio") or {})
    audio["volume_ranges"] = []
    audio["speech_frames"] = []
    edl["audio"] = audio
    # Captions stay in the take's SOURCE coordinates and are real word timings —
    # build_render_plan maps the ones that survive the re-cut and drops the rest.

    # --- b-roll, from the vector -------------------------------------------
    count, each = broll_plan_for(vector, SAMPLE_FRAMES)
    mode = broll_mode_for(vector.get("broll_overlay_bias", 0.0))
    broll: list[dict] = []
    if count:
        # An insert must fit INSIDE one chosen shot: a b-roll window is in source
        # coordinates, so anything straddling a cut maps to no output frame and
        # silently vanishes. Cap the length to the longest shot that could host
        # it (minus the lead-in below) before placing.
        longest_shot = max(s["src_out"] - s["src_in"] for s in segments)
        each = max(MIN_BROLL_FRAMES, min(each, longest_shot - BROLL_LEAD_IN - 6))
        # Placed inside the chosen segments, never over the hook.
        slots = _broll_slots(segments, count, each)
        for j, (b_in, b_out) in enumerate(slots):
            broll.append({
                "src_in": b_in, "src_out": b_out,
                "cue_text": f"style sample insert {j + 1}",
                # The take's own footage from a different moment: a style sample
                # demonstrates b-roll GRAMMAR (how often, how long, composited
                # how), and stock lookups would need a live Pexels/KLIPY key and
                # make the bank non-reproducible.
                "source": "own_media", "mode": mode,
                "resolved_url": source_url,
            })
    edl["broll"] = broll

    # --- hook sticker, from title_cta_flair ---------------------------------
    if vector.get("title_cta_flair", 0.0) >= FLAIR_STICKER_MIN:
        first = segments[0]
        edl["overlays"] = [{
            "type": "text_sticker",
            "src_in": first["src_in"] + 5,
            "src_out": min(first["src_out"], first["src_in"] + FLAIR_STICKER_FRAMES),
            "text": SAMPLE_HOOK_TEXT, "scale": 1.0, "pos_x": 0.5, "pos_y": 0.24,
            "rotation": 0.0, "color": None,
            # Straight off the bundle: themes.py owns the hook's plate + face.
            "bg": theme.hook.get("sticker_bg", "box"),
            "font": theme.hook.get("sticker_font", "inter"),
        }]

    # --- the theme itself ----------------------------------------------------
    # force=True: the base take was authored under its OWN theme, so every
    # theme-owned field is already populated. Without force, apply_theme's
    # "only fill what's empty" precedence would no-op and all 12 samples would
    # look identical. force is exactly the "creator explicitly switched bundle"
    # case the function documents.
    edl = themes.apply_theme(edl, theme, force=True)

    cfg = arc["resolved_config"]
    if cfg.get("caption_size") in ("small", "medium", "large"):
        # Stamped AFTER apply_theme: size comes from the vector (via
        # map_profile_to_config), not from the bundle, so it must not be
        # overwritten by the theme's caption options.
        opts = dict(edl.get("caption_options") or {})
        opts["size"] = cfg["caption_size"]
        edl["caption_options"] = opts

    plan = build_render_plan(edl)
    comp = COMPOSITION_ID["broll_cutaway" if broll else "talking_head"]
    return plan, comp


# A slice shorter than this is not a shot, it's a flash frame — and
# build_render_plan's own MIN_CLIP_OUTPUT_FRAMES would drop it anyway.
MIN_SLICE_FRAMES = 24


def _seg_from(clip: dict, start: int, end: int) -> dict:
    return {"src_in": start, "src_out": end,
            "speed": float(clip.get("speed") or 1.0),
            "tx_scale": float(clip.get("tx_scale") or 1.0),
            "tx_x": float(clip.get("tx_x") or 0.0),
            "tx_y": float(clip.get("tx_y") or 0.0)}


def _pick_segments(kept: list[dict], n_clips: int, per_clip: int,
                   window_src: int) -> list[dict]:
    """Choose `n_clips` slices totalling ~`window_src` source frames.

    The first slice always starts at the take's hook (a sample should open where
    the real edit opens); the rest are drawn from evenly-spaced points further
    in, so each cut is a real jump between moments rather than one continuous
    run chopped up.

    Every slice is clamped INSIDE one kept clip — spanning a boundary would
    silently re-join two moments the real edit deliberately cut apart. Because
    the take's kept clips vary wildly in length (this base take runs 15..228
    frames), a requested offset can land somewhere with no room; we then walk
    forward to the next clip that can host a full slice rather than dropping
    the segment and shipping a short sample.
    """
    total_kept = sum(c["src_out"] - c["src_in"] for c in kept)
    segments: list[dict] = []
    used_ranges: list[tuple[int, int]] = []
    for i in range(n_clips):
        # The last slice absorbs the rounding remainder so the window is filled.
        want = per_clip if i < n_clips - 1 else max(MIN_SLICE_FRAMES,
                                                    window_src - sum(s["src_out"] - s["src_in"]
                                                                     for s in segments))
        offset = 0 if i == 0 else int(total_kept * i / (n_clips + 1))
        seg = _slice_near(kept, offset, want, used_ranges)
        if seg:
            segments.append(seg)
            used_ranges.append((seg["src_in"], seg["src_out"]))

    # Top-up. A calm archetype asks for one long shot, but the take's real cuts
    # may all be shorter than that (this base take's kept clips run 15..228
    # frames), leaving the window badly underfilled — a 1.7s "sample" is worse
    # than an extra cut. So we add slices from the LONGEST unused material until
    # the window is essentially full, which keeps the added cuts to a minimum
    # and preserves the pace ordering between archetypes.
    while sum(s["src_out"] - s["src_in"] for s in segments) < window_src * WINDOW_FILL_MIN:
        need = window_src - sum(s["src_out"] - s["src_in"] for s in segments)
        extra = _longest_unused(kept, used_ranges, need)
        if not extra:
            break
        segments.append(extra)
        used_ranges.append((extra["src_in"], extra["src_out"]))

    segments.sort(key=lambda s: s["src_in"])
    return segments


def _longest_unused(kept: list[dict], used: list[tuple[int, int]],
                    need: int) -> dict | None:
    """The longest stretch of kept material not already in the sample, capped at
    `need` frames."""
    best: tuple[int, dict, int, int] | None = None
    for c in kept:
        cursor = c["src_in"]
        marks = sorted([(u_in, u_out) for u_in, u_out in used
                        if u_in < c["src_out"] and c["src_in"] < u_out])
        for u_in, u_out in marks + [(c["src_out"], c["src_out"])]:
            span = min(u_in, c["src_out"]) - cursor
            if span >= MIN_SLICE_FRAMES and (best is None or span > best[0]):
                best = (span, c, cursor, min(u_in, c["src_out"]))
            cursor = max(cursor, u_out)
    if not best:
        return None
    _, clip, start, end = best
    return _seg_from(clip, start, min(end, start + max(need, MIN_SLICE_FRAMES)))


def _slice_near(kept: list[dict], offset: int, length: int,
                used: list[tuple[int, int]]) -> dict | None:
    """A `length`-frame slice at (or after) `offset` frames into the kept material.

    Back-slides the start within a clip when the tail is too close to fit the
    full length, and skips forward past clips that are too short or already
    taken, so a spread of offsets over an unevenly-cut take still yields the
    requested number of real slices.
    """
    walked = 0
    ordered = list(kept)
    start_idx = 0
    for idx, c in enumerate(ordered):
        span = c["src_out"] - c["src_in"]
        if walked + span > offset:
            start_idx = idx
            break
        walked += span
    else:
        start_idx = 0

    # Try the clip the offset landed in first, then every later clip, then wrap.
    order = list(range(start_idx, len(ordered))) + list(range(0, start_idx))
    want_in_first = ordered[start_idx]["src_in"] + max(0, offset - walked)
    for n, idx in enumerate(order):
        c = ordered[idx]
        span = c["src_out"] - c["src_in"]
        if span < MIN_SLICE_FRAMES:
            continue
        desired = want_in_first if n == 0 else c["src_in"]
        # Back-slide so a full `length` fits when the clip is long enough.
        start = max(c["src_in"], min(desired, c["src_out"] - length))
        end = min(c["src_out"], start + length)
        if end - start < MIN_SLICE_FRAMES:
            continue
        if any(start < u_out and u_in < end for u_in, u_out in used):
            continue          # don't show the same moment twice
        return _seg_from(c, start, end)
    return None


def _broll_slots(segments: list[dict], count: int, each: int) -> list[tuple[int, int]]:
    """`count` non-overlapping windows of `each` frames inside the segments,
    skipping the hook and never straddling a segment boundary."""
    slots: list[tuple[int, int]] = []
    first_in = segments[0]["src_in"]
    for seg in segments:
        # Land the insert a beat into the shot so the cut to b-roll reads as a
        # deliberate cutaway rather than a second cut on the same frame.
        cursor = max(seg["src_in"] + BROLL_LEAD_IN, first_in + HOOK_PROTECT_FRAMES)
        while len(slots) < count and cursor + each <= seg["src_out"]:
            slots.append((cursor, cursor + each))
            cursor += each + 12
        if len(slots) >= count:
            break
    return slots


# ---------------------------------------------------------------------------
# 5. Render driver
# ---------------------------------------------------------------------------

def render_sample(arc_id: str, plan: dict, comp: str, source_url: str,
                  format_id: str, out_dir: Path) -> tuple[bool, str]:
    props = {"sourceUrl": source_url, "formatId": format_id, "edl": plan}
    props_path = out_dir / f"{arc_id}.props.json"
    out_path = out_dir / f"{arc_id}.mp4"
    props_path.write_text(json.dumps(props))
    try:
        result = subprocess.run(
            ["npx", "remotion", "render", "src/index.ts", comp, str(out_path),
             f"--props={props_path}", "--scale=0.5", "--crf=30", "--concurrency=2"],
            cwd=str(store.RENDER_DIR), capture_output=True, text=True,
            timeout=RENDER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, f"render timed out after {RENDER_TIMEOUT_S}s"
    except OSError as e:
        return False, f"could not run npx: {e}"
    if result.returncode != 0:
        return False, f"remotion rc={result.returncode}: {(result.stderr or result.stdout)[-400:]}"
    if not out_path.is_file() or out_path.stat().st_size < store.MIN_ASSET_BYTES:
        return False, "render produced no/undersized file"
    return True, str(out_path)


def run(only: str | None, out_dir: Path, do_upload: bool, do_verify: bool,
        take_id: str) -> int:
    arcs = write_archetypes()
    if only:
        arcs = [a for a in arcs if a["id"] == only]
        if not arcs:
            print(f"[samples] no archetype {only!r}")
            return 1

    base = load_base_take(take_id)
    remote_source = base.get("source_url") or ""
    if not remote_source:
        print("[samples] base take has no source_url")
        return 1
    ensure_local_source(remote_source)
    out_dir.mkdir(parents=True, exist_ok=True)

    format_id = (base["edl"].get("format_id") or "myth-buster")
    server = _start_source_server()          # serves render/fixtures on HTTP_PORT
    source_url = f"http://127.0.0.1:{HTTP_PORT}/{LOCAL_SOURCE_NAME}"

    entries: list[dict] = []
    failures: list[tuple[str, str]] = []
    started = time.time()
    try:
        for i, arc in enumerate(arcs, 1):
            aid = arc["id"]
            t0 = time.time()
            try:
                plan, comp = build_sample_edl(arc, base, source_url)
            except (ValueError, KeyError) as e:
                print(f"  FAIL  [{i:2}/{len(arcs)}] {aid:24} plan: {e}")
                failures.append((aid, f"plan: {e}"))
                continue
            ok, detail = render_sample(aid, plan, comp, source_url, format_id, out_dir)
            if not ok:
                # One bad archetype must not cost the other 11 renders.
                print(f"  FAIL  [{i:2}/{len(arcs)}] {aid:24} render: {detail}")
                failures.append((aid, f"render: {detail}"))
                continue
            local = Path(detail)
            probe = store.ffprobe(str(local))
            if do_verify:
                vok, vdetail = store.check_video(str(local), MIN_DURATION_S, MAX_DURATION_S)
                if not vok:
                    print(f"  FAIL  [{i:2}/{len(arcs)}] {aid:24} verify: {vdetail}")
                    failures.append((aid, f"verify: {vdetail}"))
                    continue

            entry = {"archetype_id": aid, "video_url": "",
                     "duration_s": probe.get("duration_s", 0.0),
                     "bytes": local.stat().st_size}
            if do_upload:
                url, up_detail = store.upload(local, f"{STORAGE_PREFIX}/{aid}.mp4")
                if not url:
                    print(f"  FAIL  [{i:2}/{len(arcs)}] {aid:24} upload: {up_detail}")
                    failures.append((aid, f"upload: {up_detail}"))
                    continue
                entry["video_url"] = url
            entries.append(entry)
            print(f"  ok    [{i:2}/{len(arcs)}] {aid:24} {comp.split('-')[1]:13} "
                  f"clips={len(plan.get('clips') or [])} broll={len(plan.get('broll') or [])} "
                  f"{entry['duration_s']}s {entry['bytes'] / 1024:.0f} kB {time.time() - t0:.0f}s")
    finally:
        server.shutdown()

    print("---")
    print(f"rendered {len(entries)}/{len(arcs)} samples in {time.time() - started:.0f}s")

    if do_upload:
        if failures:
            print("[samples] NOT writing manifest — the bank is incomplete")
        else:
            store.write_manifest(SAMPLES_PATH, {
                "bank_version": BANK_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "base_take": take_id,
                "samples": entries,
            })
            print(f"[samples] wrote {SAMPLES_PATH} ({len(entries)} samples)")

    if failures:
        print(f"STYLE SAMPLES: {len(failures)} FAILURES")
        for aid, why in failures:
            print(f"  - {aid}: {why}")
        return 1
    print("STYLE SAMPLES: PASS")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="", help="a single archetype id")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--upload", action="store_true",
                    help="upload each sample + write backend/assets/style_samples.json")
    ap.add_argument("--verify", action="store_true",
                    help="ffprobe every sample before it is uploaded/manifested")
    ap.add_argument("--take", default=DEFAULT_TAKE, help="eval/cutloop take id to cut from")
    ap.add_argument("--archetypes-only", action="store_true",
                    help="write style_archetypes.json and stop (no renders)")
    args = ap.parse_args(argv)

    if args.archetypes_only:
        write_archetypes()
        return 0
    return run(args.only or None, Path(args.out), args.upload, args.verify, args.take)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
