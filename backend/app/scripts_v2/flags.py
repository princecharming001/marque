"""SCRIPTS_V2 switches (read per call, so an env change + restart is the whole rollout).

  SCRIPTS_V2            off (default) | allowlist | on. The kill switch: off sends every
                        route through the v1 writer exactly as before.
  SCRIPTS_V2_ALLOWLIST  comma-separated creator ids for `allowlist` mode ("*" = everyone).
  SCRIPTS_V2_WRITER_MODEL       background / full-quality writer (default Sonnet 5.5).
  SCRIPTS_V2_FIRST_PAINT_MODEL  first-paint writer (default Haiku 5.5; Sonnet is an A/B).
  SCRIPTS_V2_HELPER_MODEL       material / judge / hook lab / beats (default Haiku 5.5).
  SCRIPTS_V2_BEST_OF    1 (default) | 2. Two drafts per slot on the full path, the second
                        from a DIFFERENT exemplar set (sampling is fixed on 5.x models, so
                        identical inputs give near-twins); the judge keeps one.
  SCRIPTS_V2_EFFORT     writer effort (default low).
"""
from __future__ import annotations

import os

SONNET_55 = "claude-sonnet-5-5"
HAIKU_55 = "claude-haiku-5-5"


def mode() -> str:
    m = (os.environ.get("SCRIPTS_V2") or "off").strip().lower()
    return m if m in ("off", "allowlist", "on") else "off"


def _allowlist() -> set[str]:
    return {x.strip() for x in (os.environ.get("SCRIPTS_V2_ALLOWLIST") or "").split(",") if x.strip()}


def enabled_for(creator_id: str | None) -> bool:
    m = mode()
    if m == "on":
        return True
    if m == "allowlist":
        allow = _allowlist()
        return "*" in allow or bool(creator_id and creator_id in allow)
    return False


def writer_model() -> str:
    return (os.environ.get("SCRIPTS_V2_WRITER_MODEL") or SONNET_55).strip()


def first_paint_model() -> str:
    return (os.environ.get("SCRIPTS_V2_FIRST_PAINT_MODEL") or HAIKU_55).strip()


def helper_model() -> str:
    return (os.environ.get("SCRIPTS_V2_HELPER_MODEL") or HAIKU_55).strip()


def best_of() -> int:
    try:
        return 2 if int(os.environ.get("SCRIPTS_V2_BEST_OF", "1")) >= 2 else 1
    except ValueError:
        return 1


def effort() -> str:
    e = (os.environ.get("SCRIPTS_V2_EFFORT") or "low").strip().lower()
    return e if e in ("low", "medium", "high") else "low"
