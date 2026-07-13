"""Craft-doctrine block loader — ported from Palo's doctrine/loader.py.

The doctrine (knowledge/craft_doctrine.md, copied from Palo v1.4) is one XML-tagged
document of short-form storytelling craft: worldview principles, diagnostics/guards,
the concept + spine map, platform mechanics, and Palo's vocabulary. The write/idea/
insight prompts reference it via {DOCTRINE_*} placeholders (see prompt_assembly.py).

`doctrine_block(name)` returns the concatenated content for one named block. Byte-stable
output (fixed tag order, stripped consistently) is deliberate: these blocks sit in the
cached prefix of every call, so a stable render keeps the Anthropic cache warm. All
fail-soft: a missing file or tag yields "" and the agent runs without that block.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "knowledge", "craft_doctrine.md")

# Named block -> the doctrine tags it renders, in fixed order (stable for caching).
_BLOCKS = {
    "core": ("about", "reading_rules", "core_rule", "worldview", "guards"),
    "concept": ("concept", "spine"),
    "spine_map": ("spine_map",),
    "platform": ("platform_mechanics",),
    "vocabulary": ("vocabulary",),
}


@lru_cache(maxsize=1)
def _doc() -> str:
    try:
        with open(_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _extract(text: str, tag: str) -> str:
    m = re.search(rf"(?s)<{tag}\b[^>]*>(.*?)</{tag}>", text)
    return m.group(1).strip() if m else ""


@lru_cache(maxsize=8)
def doctrine_block(name: str) -> str:
    text = _doc()
    if not text:
        return ""
    parts = [c for tag in _BLOCKS.get(name, ()) if (c := _extract(text, tag))]
    return "\n\n".join(parts)
