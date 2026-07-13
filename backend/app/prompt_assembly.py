"""Shared prompt-assembly helpers — ported from Palo_Server/palo_python/prompt_assembly.py.

The write agent, converse, and idea/insight prompts all consume the same strategy +
doctrine backbone; keeping the substitution here stops the rendered prompts drifting
apart. Two families of placeholder:

  {STRATEGY_*}  sliced from the compiled channel_strategies.strategy_markdown
  {DOCTRINE_*}  named craft blocks from the knowledge base (app/knowledge.py)

Renamed Palo's `infer_creator_tier` -> `infer_craft_regime` so it is never confused
with the paid `creator_tier` in app/tiers.py — this one classifies the creator's
GROWTH regime (sub-breakout / breakout / scaling) from the strategy, not their plan.
Everything is best-effort: a missing strategy or doctrine renders an inline marker,
never crashes the agent.
"""
from __future__ import annotations

import re

_SECTION_HEADER_TO_PLACEHOLDER = {
    "Insights": "{STRATEGY_INSIGHTS}",
    "Plan": "{STRATEGY_DIRECTIVE}",
    "Buckets": "{STRATEGY_BUCKETS}",
    "Brand Bets": "{STRATEGY_BRAND_BETS}",
    "Not-Doing": "{STRATEGY_NOT_DOING}",
}

_DOCTRINE_PLACEHOLDERS = (
    "{DOCTRINE_CORE}", "{DOCTRINE_CONCEPT}", "{DOCTRINE_SPINE_MAP}",
    "{DOCTRINE_PLATFORM}", "{DOCTRINE_VOCABULARY}",
)


def _slice_section(strategy_md: str, name: str) -> str:
    if not strategy_md:
        return f"(strategy '{name}' not available — no compiled strategy yet)"
    m = re.search(rf"(?ms)^##\s+{re.escape(name)}\b.*?(?=^##\s|\Z)", strategy_md)
    return m.group(0).strip() if m else f"(strategy '{name}' not available)"


def replace_strategy_sections(prompt: str, strategy_md: str) -> str:
    out = prompt
    for header, placeholder in _SECTION_HEADER_TO_PLACEHOLDER.items():
        out = out.replace(placeholder, _slice_section(strategy_md, header))
    return out


def replace_doctrine_blocks(prompt: str) -> str:
    """Fill {DOCTRINE_*} from the craft knowledge base. Uses app.knowledge.doctrine_block
    when available; otherwise renders inline markers so the agent still runs."""
    try:
        from app.doctrine import doctrine_block  # type: ignore
    except Exception:
        for k in _DOCTRINE_PLACEHOLDERS:
            prompt = prompt.replace(k, "(doctrine unavailable)")
        return prompt

    def safe(name: str) -> str:
        try:
            return doctrine_block(name) or "(doctrine block unavailable)"
        except Exception:
            return "(doctrine block unavailable)"

    return (
        prompt
        .replace("{DOCTRINE_CORE}", safe("core"))
        .replace("{DOCTRINE_CONCEPT}", safe("concept"))
        .replace("{DOCTRINE_SPINE_MAP}", safe("spine_map"))
        .replace("{DOCTRINE_PLATFORM}", safe("platform"))
        .replace("{DOCTRINE_VOCABULARY}", safe("vocabulary"))
    )


_REGIME_RE = re.compile(r"REGIME:\s*(?P<regime>sub-breakout|breakout|scaling)\b", re.IGNORECASE)
_LEVER_RE = re.compile(r"LEVER:\s*(?P<lever>[^\n]+)", re.IGNORECASE)
_LEVER_BY_REGIME = {
    "sub-breakout": "ESCAPE the view-band",
    "breakout": "EXTEND territory",
    "scaling": "EXTEND territory",
}


def infer_craft_regime(strategy_md: str) -> str:
    """Read REGIME:/LEVER: from the strategy `## Plan` block into a one-line label for
    the {CREATOR_TIER} placeholder in the write/idea prompts. 'unknown' if absent."""
    if not strategy_md:
        return "unknown"
    rm = _REGIME_RE.search(strategy_md)
    if not rm:
        return "unknown"
    regime = rm.group("regime").strip().lower()
    lm = _LEVER_RE.search(strategy_md)
    lever_text = lm.group("lever").strip() if lm else ""
    if lever_text:
        if len(lever_text) > 200:
            lever_text = lever_text[:200].rstrip() + "…"
        return f"{regime} (lever: {lever_text})"
    return f"{regime} (lever: {_LEVER_BY_REGIME.get(regime, 'unknown')})"
