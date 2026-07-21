"""The Craft Engine registry (build 57) — professional editor + content-expert
judgment as INFRASTRUCTURE, not scattered constants.

Doctrine lives in knowledge/craft/*.md: prose for humans/LLMs plus a fenced
```yaml rules:``` block per file with machine-readable entries
{id, principle, source, enforce: prompt|lint|critic|knob|advise, params}.
Every rule is research-sourced (build-56/57 research reports: Murch, Grammar of
the Edit, Netflix/BBC/SMPTE/WCAG/AES standards, platform-published ranking
docs, Cutting et al. 2010, Pearlman) — nothing here is taste.

Consumers:
  • prompt_block(call)  → a compact, curated doctrine block appended to the
    brief / edit_plan prompts (separate from knowledge.digest(), so the KB
    token-headroom contract is untouched).
  • rule_params(id)     → thresholds for edit_lint's craft checks (single
    source of truth: the YAML params, not lint-local constants).
  • rules()/by_enforce()→ the full registry (critic axes, future knobs, report).
  • craft_version()     → stamped into job["craft_report"]; changes when any
    doctrine file changes (content hash), so outcomes can segment by doctrine
    revision exactly like knowledge_version.

Fail-soft everywhere: a missing/corrupt doctrine file yields an empty registry
and empty prompt blocks — the pipeline never fails on craft infrastructure.
"""
from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache

_CRAFT_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge", "craft")

_YAML_BLOCK = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)


@lru_cache(maxsize=1)
def _load() -> tuple[list[dict], str]:
    """Parse every craft file's YAML rules block. Returns (rules, content_hash)."""
    rules: list[dict] = []
    hasher = hashlib.sha1()
    try:
        names = sorted(f for f in os.listdir(_CRAFT_DIR) if f.endswith(".md"))
    except OSError:
        return [], "none"
    for name in names:
        try:
            with open(os.path.join(_CRAFT_DIR, name)) as f:
                text = f.read()
        except OSError:
            continue
        hasher.update(text.encode())
        for block in _YAML_BLOCK.findall(text):
            try:
                import yaml
                data = yaml.safe_load(block) or {}
            except Exception:
                continue
            for r in data.get("rules") or []:
                if isinstance(r, dict) and r.get("id"):
                    r.setdefault("params", {})
                    r["file"] = name
                    rules.append(r)
    return rules, hasher.hexdigest()[:10]


def rules() -> list[dict]:
    return list(_load()[0])


def by_enforce(kind: str) -> list[dict]:
    return [r for r in _load()[0] if r.get("enforce") == kind]


def rule_params(rule_id: str, default: dict | None = None) -> dict:
    for r in _load()[0]:
        if r.get("id") == rule_id:
            return dict(r.get("params") or {})
    return dict(default or {})


def craft_version() -> str:
    return f"craft-{_load()[1]}"


# ---------------------------------------------------------------------------
# Prompt injection — curated compact blocks (NOT the full files: the KB digest
# has a guarded token budget, and the planner already carries operational
# doctrine; these are the judgment PRIORITIES a pro holds while deciding).
# ---------------------------------------------------------------------------

_EDIT_BLOCK = """CRAFT PRIORITIES (the professional editor's judgment order):
- Conflicts between cut criteria resolve by Murch's order: emotion > story >
  rhythm > eye-trace > geometry. Never trade emotional truth for a cleaner match.
- Every cut needs new information AND a motivation (a movement, a beat, an
  off-screen sound). When unsure of the exact frame, cut LONG, not short.
- Shot lengths must vary and cluster (runs of short, runs of long) — after any
  fast/effect-dense stretch, the next beat must BREATHE with a longer hold.
- Each filler/pause seam: cover it (b-roll), bridge it (punch-in), or expose it
  at a consistent cadence — never a lone naked jump.
- The last kept sentence must COMPLETE the thought — end on the argument
  closing or the CTA, never a mid-thought trail-off.
- Inserts/reactions enter mid-clause, not after the period."""

_BRIEF_BLOCK = """CONTENT-EXPERT PRIORITIES (platform-published, not folklore):
- Completion is what platforms rank: prefer the shortest cut that preserves the
  payoff. The promise must land inside 3s; most recall value sits in the first 2.5s.
- The hook is a CONTRACT: whatever it promises must be verifiably paid off in
  the body — an unpaid promise is the highest-order failure.
- Exactly ONE CTA, imperative + specific, <=7 words, after the first value
  payoff. Never bait forms ("like if", "tag a friend") — platforms demote them.
- Videos >30s need a mid-video re-hook beat. Loop-seam endings farm replays."""

_REVIEW_BLOCK = """CRAFT REVIEW AXES (score with reasons, not bare numbers):
- HOOK: grabs inside 3s and relates to the actual topic.
- FLOW: logical progression, complete final sentence, satisfying close.
- VALUE: the promise is paid off; every 5s block earns its place.
- TEXT TIMING: any on-screen text must be readable — duration >= max(words x
  0.3s, chars / 20 per second); flag any text block that fails."""


def prompt_block(call: str) -> str:
    return {"edit_plan": _EDIT_BLOCK, "brief": _BRIEF_BLOCK,
            "review": _REVIEW_BLOCK}.get(call, "")
