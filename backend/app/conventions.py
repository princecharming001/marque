"""Convention layer: every study-fillable editing parameter as a NAMED value.

All values here are IDENTITY — byte-identical to the pre-conventions pipeline —
until the owner approves the measured findings (eval/study/data/out/). Wave 3
fills them from the findings doc; nothing else in this file should change
behavior on its own. Parameter homes (per the approved plan):
  - render-executed geometry/timing -> render/src/layout.json (3-way parity)
  - backend policy defaults          -> HERE
  - LLM doctrine                     -> knowledge/captions.md
  - lint thresholds                  -> knowledge/craft/*.md YAML

Deterministic gates only: any probabilistic choice keys off job_seed via
seed_fraction(), never random() — re-renders and tests must reproduce.
"""
from __future__ import annotations

import hashlib

# --- captions ----------------------------------------------------------------

CAPTION_CONVENTIONS = {
    "default_style": "clean",          # edl.py caption-plan fallback
    "default_grouping": "phrase",      # edl.py caption-plan fallback
    "pos_y_default": 0.62,             # spec §6.3 band; replaces the edl.py literal
    "highlight_cap": 12,               # max LLM highlight words
    "sync_lead_frames": 0,             # caption pre-empts speech by N frames (0-10)
    # per caption-style-family defaults applied ONLY when creator/plan silent:
    "stroke_px_default": {},           # e.g. {"bold-word": 8.0} post-findings
    "uppercase_default": {},           # e.g. {"bold-word": True} post-findings
}

# record-screen style -> font map (moved from the main.py inline dict)
CAPTION_STYLE_FONT = {"bold-word": "archivo", "karaoke": "montserrat"}

# --- title card --------------------------------------------------------------

# rate: probability the opening hook title fires, per content type ("default"
# covers unknown). suppress: named predicates evaluated in place_hook_overlay.
# Identity: always-on, nothing suppressed (today's behavior).
TITLE_CARD_POLICY = {
    "rate": {"default": 1.0},
    "suppress": [],                    # tokens: "captions_top", "under_8s", "hook_spoken_verbatim"
}

# sticker style tokens per caption family — resolved at the place_hook_overlay
# emit site with precedence: theme.hook explicit > family token > legacy default.
# Identity: empty (theme/legacy path unchanged).
STICKER_STYLE_TOKENS: dict = {}

# --- CTA / ending ------------------------------------------------------------

CTA_PATTERNS = ("hard_end_card", "text_overlay", "spoken_only")
# per content-type weights; must sum to 1.0 per row. Identity: hard card always
# (today's behavior when a card is wanted at all).
CTA_PATTERN_WEIGHTS = {
    "default": {"hard_end_card": 1.0, "text_overlay": 0.0, "spoken_only": 0.0},
}

# --- deterministic seeding ---------------------------------------------------


def seed_fraction(job_seed: str | None, salt: str = "") -> float:
    """[0,1) fraction derived from the job seed — NEVER random(): identical
    inputs must gate identically across re-renders and tests."""
    h = hashlib.sha1(f"{job_seed or ''}|{salt}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def pick_weighted(weights: dict[str, float], frac: float) -> str:
    """Deterministic weighted pick by a [0,1) fraction (stable key order)."""
    acc = 0.0
    items = sorted(weights.items())
    for name, w in items:
        acc += max(0.0, w)
        if frac < acc:
            return name
    return items[-1][0] if items else ""
