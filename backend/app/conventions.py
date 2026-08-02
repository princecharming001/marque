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

# Wave 3 values from the 2026-07-29 study (n=64 spoken winners, verify-gate
# CLEAN; see eval/study/data/out/). Each number cites its measurement.
CAPTION_CONVENTIONS = {
    "default_style": "clean",          # winners: sentence-case short phrases
    "default_grouping": "phrase",      # winners words/chunk median 3 (IQR 2-6) = PHRASE_LEN 3
    "pos_y_default": 0.62,             # VALIDATED: winners y-center median 0.627
    "highlight_cap": 12,
    "sync_lead_frames": 0,             # winners lead +40ms ~ within a frame; noise-level
    # Winners: 95% stroke, font-weight mode "regular" — the default look gets a
    # modest outline instead of ultra-heavy weight. Applied ONLY when the
    # creator/plan didn't set stroke themselves.
    "stroke_px_default": {"clean": 3.0},
    "uppercase_default": {},           # winners all-caps median 2% — sentence case stands
    # Winners all-caps 2% + words/chunk 3: the giant single-word caps treatment
    # is an EXPLICIT-pick style, not an auto-path default. A plan-authored
    # "bold-word" (creator silent) maps to "clean"; a creator pick is honored.
    "auto_style_allowed": ("clean", "karaoke"),
}

# record-screen style -> font map (moved from the main.py inline dict)
CAPTION_STYLE_FONT = {"bold-word": "archivo", "karaoke": "montserrat"}

# --- title card --------------------------------------------------------------

# rate: probability the opening hook title fires, per content type ("default"
# covers unknown). suppress: named predicates evaluated in place_hook_overlay.
# Identity: always-on, nothing suppressed (today's behavior).
TITLE_CARD_POLICY = {
    # Winners open with a title card on 17% of reels overall (how_to 18% n=45,
    # opinion 12% n=8, story 0% n=6); duration when present: 2.2s median
    # (IQR 2.2-2.4). Identity was 1.0 (always-on) — the owner's "only when
    # appropriate" instinct, measured.
    "rate": {"default": 0.2, "how_to": 0.2, "opinion": 0.15,
             "story": 0.0, "storytime": 0.0},
    "suppress": ["captions_top"],
    "hold_max_frames": 72,             # 2.4s — winners' IQR ceiling
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
    # Winners: 86% NO visual CTA, 8% spoken-only, 5% small overlay, 2% end card.
    # Among plan-WANTED CTAs that maps to spoken-dominant. A creator-built outro
    # always forces the hard card regardless (explicit pick beats convention).
    "default": {"spoken_only": 0.85, "text_overlay": 0.13, "hard_end_card": 0.02},
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
