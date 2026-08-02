"""The editing-style taste profile: a small, fixed, normalized vector that says how a
creator likes their videos cut — learned from swiping real reels, then mapped
deterministically onto the pipeline knobs that already exist.

WHY A VECTOR AND NOT A PREFERENCE LIST
The swiper shows real reels whose editing we have ALREADY measured (the 2026-07 winner
study wrote a per-reel `anatomy` JSON: cut density, caption case/chunking, b-roll density
and holds, wpm, title card, CTA pattern). So a like/dislike is a labelled observation of
ATTRIBUTES, not of an opaque item — which is exactly the case where attribute-based
preference elicitation beats collaborative filtering (it needs no other users, works from
~12 swipes, and stays explainable: "you liked fast, caption-heavy edits").

THE MATH (Rocchio relevance feedback — the standard for this shape of problem)
    p = clamp01( a*p0 + b*mean(liked) - g*mean(disliked) )
`p0` is the cold-start profile: the measured-winner defaults we already ship, so a
creator who skips the quiz gets EXACTLY today's behavior.

Everything here is pure and deterministic; the iOS client mirrors these formulas
(StyleProfileMapper.swift) and `assets/style_mapping_cases.json` pins both to the same
golden cases so they can never drift.
"""
from __future__ import annotations

import json
import os
from typing import Iterable

SCHEMA_VERSION = 1

# The 8 dimensions, in canonical order. Each is normalized 0..1 by a clamped linear
# ramp between the two anchors below — anchors chosen from the study's observed spread
# so a real reel lands mid-scale rather than pinned at an end.
DIMS: tuple[str, ...] = (
    "pace",                 # cuts per 30s
    "energy",               # words per minute
    "broll_density",        # b-roll segments per 30s
    "broll_share",          # fraction of runtime covered by b-roll
    "broll_overlay_bias",   # of the b-roll used, how much is overlay vs fullscreen
    "caption_boldness",     # caps + weight + box + stroke, composited
    "caption_chunking",     # 1.0 = one word at a time, 0.0 = long phrases
    "title_cta_flair",      # appetite for opening title cards + visible CTAs
)

ANCHORS: dict[str, tuple[float, float]] = {
    "pace": (2.0, 12.0),          # cuts/30s
    "energy": (120.0, 220.0),     # wpm
    "broll_density": (0.0, 5.0),  # segments/30s
    "broll_share": (0.0, 0.8),    # share of runtime
}

# Cold start = the Wave-3 measured-winner conventions expressed as a vector. A creator
# who skips the quiz maps to clean_creator + standard knobs, i.e. today's pipeline.
COLD_START: dict[str, float] = {
    "pace": 0.35, "energy": 0.45, "broll_density": 0.20, "broll_share": 0.15,
    "broll_overlay_bias": 0.30, "caption_boldness": 0.15, "caption_chunking": 0.30,
    "title_cta_flair": 0.15,
}

# Rocchio weights. beta > alpha so a decisive swipe session actually moves the profile;
# gamma < beta because a dislike is weaker evidence than a like (a creator may pass on a
# reel for its content, not its edit).
ALPHA, BETA, GAMMA = 0.3, 0.8, 0.3
MIN_SWIPES = 12          # non-skip swipes before the profile is trusted
MIN_LIKES = 3            # below this we keep the cold start (confidence "low")


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _ramp(v: float | None, lo: float, hi: float) -> float:
    if v is None:
        return 0.0
    return _clamp01((float(v) - lo) / (hi - lo)) if hi > lo else 0.0


def vector_from_anatomy(a: dict) -> dict[str, float]:
    """Map one measured reel (a study anatomy record) onto the style vector."""
    caps = a.get("captions") or {}
    style = a.get("caption_style") or {}
    br = a.get("broll") or {}
    cuts = (a.get("cut_stats") or {}).get("cuts_per_30s")
    wpm = (a.get("transcript") or {}).get("wpm")

    boldness = (
        0.40 * float(caps.get("pct_all_caps") or 0.0)
        + 0.30 * (1.0 if style.get("font_weight") == "heavy" else 0.0)
        + 0.15 * (1.0 if style.get("boxed") else 0.0)
        + 0.15 * (1.0 if style.get("stroke") else 0.0)
    )
    wpc = caps.get("words_per_chunk_median")
    chunking = _clamp01((4.0 - float(wpc)) / 3.0) if wpc is not None else 0.3

    # No b-roll at all tells us nothing about overlay taste — impute the corpus norm
    # rather than scoring it 0 (which would read as "hates overlays").
    overlay = br.get("pct_overlay")
    overlay_bias = float(overlay) if (overlay is not None and (br.get("count") or 0) > 0) else 0.30

    cta = ((a.get("cta") or {}).get("pattern") or "").lower()
    cta_visual = 1.0 if "end_card" in cta else (0.5 if "overlay" in cta else 0.0)
    flair = 0.5 * (1.0 if (a.get("title_card") or {}).get("present") else 0.0) + 0.5 * cta_visual

    return {
        "pace": _ramp(cuts, *ANCHORS["pace"]),
        "energy": _ramp(wpm, *ANCHORS["energy"]),
        "broll_density": _ramp(br.get("per_30s"), *ANCHORS["broll_density"]),
        "broll_share": _ramp(br.get("share_of_runtime"), *ANCHORS["broll_share"]),
        "broll_overlay_bias": _clamp01(overlay_bias),
        "caption_boldness": _clamp01(boldness),
        "caption_chunking": _clamp01(chunking),
        "title_cta_flair": _clamp01(flair),
    }


def _mean(vectors: list[dict[str, float]], weights: list[float] | None = None) -> dict[str, float]:
    if not vectors:
        return {d: 0.0 for d in DIMS}
    w = weights or [1.0] * len(vectors)
    tot = sum(w) or 1.0
    return {d: sum(v.get(d, 0.0) * wi for v, wi in zip(vectors, w)) / tot for d in DIMS}


def rocchio(liked: list[dict[str, float]], disliked: list[dict[str, float]],
            *, like_weights: list[float] | None = None,
            base: dict[str, float] | None = None) -> dict[str, float]:
    """Fold a swipe session into a profile. `like_weights` carries super-likes (2.0)."""
    p0 = base or COLD_START
    if len(liked) < MIN_LIKES:
        # Not enough positive evidence to move off the measured-winner default.
        return dict(p0)
    lm = _mean(liked, like_weights)
    dm = _mean(disliked)
    return {d: _clamp01(ALPHA * p0.get(d, 0.0) + BETA * lm[d] - GAMMA * dm[d]) for d in DIMS}


def distance(a: dict[str, float], b: dict[str, float]) -> float:
    return sum((a.get(d, 0.0) - b.get(d, 0.0)) ** 2 for d in DIMS) ** 0.5


_ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def _load(name: str) -> dict:
    try:
        with open(os.path.join(_ASSETS, name)) as f:
            return json.load(f)
    except Exception:
        return {}


def archetypes() -> list[dict]:
    return list(_load("style_archetypes.json").get("archetypes") or [])


def nearest_archetype(profile: dict[str, float]) -> dict | None:
    """The pre-rendered sample closest to this profile (settings-page preview)."""
    arcs = archetypes()
    if not arcs:
        return None
    return min(arcs, key=lambda a: distance(profile, a.get("vector") or {}))


def intensity(p: dict[str, float]) -> float:
    """One scalar for 'how loud is this edit' — drives the meme/energy dials."""
    return 0.45 * p.get("pace", 0.0) + 0.35 * p.get("caption_boldness", 0.0) \
        + 0.20 * p.get("energy", 0.0)


def map_profile_to_config(p: dict[str, float]) -> dict[str, str]:
    """Profile -> the per-job config keys the pipeline already understands.

    Only keys the profile has an OPINION about are emitted; anything omitted keeps the
    theme/plan default. Explicit per-job picks always override these (the caller merges
    this UNDER the creator's own choices).
    """
    out: dict[str, str] = {}
    arc = nearest_archetype(p)
    if arc and arc.get("theme_id"):
        out["theme_id"] = str(arc["theme_id"])

    boldness, chunking = p.get("caption_boldness", 0.0), p.get("caption_chunking", 0.0)
    if boldness >= 0.60 and chunking >= 0.60:
        out["caption_style"] = "bold-word"
    else:
        out["caption_style"] = "clean"
    # The cold-start profile must claim NOTHING it doesn't have evidence for: at
    # COLD_START boldness (0.15) we emit no size at all, so an un-quizzed creator keeps
    # today's Auto sizing. Only a profile pushed clear of the default speaks up.
    if boldness >= 0.70:
        out["caption_size"] = "large"
    elif boldness < COLD_START["caption_boldness"]:
        out["caption_size"] = "small"

    i = intensity(p)
    out["meme_intensity"] = "0" if i < 0.25 else "1" if i < 0.50 else "2" if i < 0.75 else "3"

    pace = p.get("pace", 0.0)
    out["interrupt_density"] = "calm" if pace < 0.33 else "standard" if pace < 0.66 else "dense"
    out["filler_trim"] = "aggressive" if pace >= 0.60 else "standard"

    ob = p.get("broll_overlay_bias", 0.0)
    out["broll_mode"] = "panel" if ob >= 0.60 else "cutaway" if ob <= 0.35 else "smart"
    if p.get("broll_share", 0.0) >= 0.50:
        out["broll_coverage"] = "full"

    e = p.get("energy", 0.0)
    if e >= 0.66:
        out["energy"] = "high"
    elif e <= 0.33:
        out["energy"] = "low"
    return out


def normalize(raw: dict | None) -> dict[str, float]:
    """Coerce whatever the client sent into a valid vector (missing dims -> cold start)."""
    raw = raw or {}
    dims = raw.get("dims") if isinstance(raw.get("dims"), dict) else raw
    return {d: _clamp01(float(dims.get(d, COLD_START[d]))) for d in DIMS}
