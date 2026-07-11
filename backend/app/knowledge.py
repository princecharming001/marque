"""Editing-craft knowledge base loader (Phase 2).

`digest(style, video_type, call)` returns a compact (~600–1000 token) string of the craft
rules relevant to one LLM call, assembled from `backend/knowledge/*.md`. Craft NUMBERS live
only in the KB markdown — prompts import this digest instead of hard-coding cadence/LUFS/etc.
`knowledge_version()` reads MANIFEST.json so a job can stamp which KB produced it (A/B +
revert, like a prompt version). All fail-soft: a missing KB dir yields an empty digest and the
pipeline runs unchanged.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache

_KB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge")

# Which KB domains each call needs (plan §Phase 2 loader).
_CALL_FILES = {
    "brief": ["retention", "hooks"],
    "edit_plan": ["pacing", "broll", "captions"],
    "review": ["review_rubric"],
}

# Rough token budget (chars ≈ 4/token). Trim the assembled digest to stay in band.
_MAX_TOKENS = 1000
_CHARS_PER_TOKEN = 4


@lru_cache(maxsize=1)
def knowledge_version() -> str | None:
    try:
        with open(os.path.join(_KB_DIR, "MANIFEST.json")) as f:
            return json.load(f).get("version")
    except Exception:
        return None


@lru_cache(maxsize=16)
def _read(name: str) -> str:
    try:
        with open(os.path.join(_KB_DIR, f"{name}.md")) as f:
            return f.read()
    except Exception:
        return ""


def _pacing_row(video_type: str) -> str:
    """Extract the cadence-by-video_type row matching video_type (fallback: default)."""
    text = _read("pacing")
    vt = (video_type or "default").strip().lower()
    rows: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or "cut cadence" in line or set(line) <= {"|", "-", " "}:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 4 and cells[0] and cells[0] not in ("video_type",):
            rows[cells[0].lower()] = f"{cells[0]}: {cells[1]} cuts ({cells[2]} frames) — {cells[3]}"
    return rows.get(vt) or rows.get("default", "")


def _style_note(style: str) -> str:
    """Extract the '## By style' bullet matching style (from pacing.md)."""
    text = _read("pacing")
    st = (style or "").strip().lower()
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*([a-z_]+):\s*(.+)$", line)
        if m and m.group(1).lower() == st:
            return f"{m.group(1)}: {m.group(2)}"
    return ""


def _trim(text: str, max_tokens: int = _MAX_TOKENS) -> str:
    max_chars = max_tokens * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    # trim to the last full line under budget
    cut = text[:max_chars]
    nl = cut.rfind("\n")
    return (cut[:nl] if nl > 0 else cut).rstrip() + "\n…(trimmed)"


def digest(style: str, video_type: str, call: str) -> str:
    """Assemble the craft digest for one call. call ∈ {brief, edit_plan, review}."""
    files = _CALL_FILES.get(call, _CALL_FILES["edit_plan"])
    parts: list[str] = [f"EDITING KNOWLEDGE BASE ({knowledge_version() or 'kb-unversioned'}), call={call}:"]
    # Selection-specific lines FIRST so they survive the token trim even when the domain
    # files are long (they're the most call-specific craft the prompt needs).
    if call in ("edit_plan", "brief"):
        row = _pacing_row(video_type)
        if row:
            parts.append(f"PACING for video_type={video_type or 'default'}: {row}")
        note = _style_note(style)
        if note:
            parts.append(f"STYLE pacing for {style}: {note}")
    for name in files:
        body = _read(name).strip()
        if body:
            parts.append(body)
    text = "\n\n".join(p for p in parts if p)
    return _trim(text)


def style_rules(style: str) -> str:
    """Thin KB-backed successor to EDIT_RUBRICS' style-mechanics note (pacing 'By style')."""
    return _style_note(style)
