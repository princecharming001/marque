"""The CTA template catalog — the backend mirror of
`render/src/components/cta/cta_styles.json`.

That JSON is the single source of truth (the render bundle imports it directly); this
module loads it so the backend, the /v1/cta-styles route and the iOS picker all speak
exactly the same 20 ids. `test_cta_styles.py` asserts the two stay in lockstep.

Layout class decides HOW a CTA plays:
  tail_card — appended after the last clip (build_render_plan extends total_frames)
  overlay   — rides over the final seconds of live video (no tail extension)
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

_JSON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "render", "src", "components", "cta", "cta_styles.json")

DEFAULT_TAIL_STYLE = "classic"
DEFAULT_OVERLAY_STYLE = "pill"
NONE_STYLE = "none"          # first-class "no visual CTA" pick (never a template)


@lru_cache(maxsize=1)
def _catalog() -> list[dict]:
    try:
        with open(_JSON_PATH) as f:
            return list(json.load(f).get("styles") or [])
    except Exception:
        # Fail-soft: an unreadable catalog must never take the pipeline down — the
        # classic card still renders because clamp_style_id falls back to it.
        return []


def styles() -> list[dict]:
    """The full catalog (ordered as authored — restrained styles first)."""
    return list(_catalog())


def style_ids() -> set[str]:
    return {s["id"] for s in _catalog()}


def is_known(style_id: str | None) -> bool:
    return bool(style_id) and style_id in style_ids()


def layout_class(style_id: str | None) -> str:
    for s in _catalog():
        if s["id"] == style_id:
            return s.get("layout_class", "tail_card")
    return "tail_card"


def mount_for(style_id: str | None) -> str:
    """The render-plan `mount` a style implies: "overlay" or "tail"."""
    return "overlay" if layout_class(style_id) == "overlay" else "tail"


def is_overlay(style_id: str | None) -> bool:
    return mount_for(style_id) == "overlay"


def clamp_style_id(style_id: str | None) -> str:
    """Any unknown/absent id becomes the classic card — the render bundle does the
    same on its side, so a version skew degrades instead of failing."""
    return style_id if is_known(style_id) else DEFAULT_TAIL_STYLE


def pattern_for(style_id: str | None) -> str:
    """The retention CTA pattern a creator's template choice forces."""
    return "text_overlay" if is_overlay(style_id) else "hard_end_card"
