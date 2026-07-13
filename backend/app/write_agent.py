"""Phase 5 (box 1) — interactive write agent (ported from Palo write_pyro).

Re-expressed as a plain Anthropic call (no LangGraph): given the current script + the
creator's request, the agent proposes precise actions the creator accepts/rejects —
never a silent rewrite. Four action types (Palo's contract): <fill> (full rewrite),
<edit> (exact-substring replace), <add> (insert relative to a phrase), <answer> (chat,
no doc change). The exact-substring APPLY + invariants + iOS mapping land in box 2; this
box is the loop + prompt + fill/edit/answer branching + parsing.

Strategy + memory are injected so the write agent draws on the brain. Keyless-green: no
key ⇒ a deterministic <answer> mock. Flag WRITE_AGENT.
"""
from __future__ import annotations

import logging
import re

from app import palo_flags, palo_prompts
from app.palo_llm import anthropic_cached
from prompts import OPUS

_FILL_RE = re.compile(r"<fill>(?P<content>.*?)</fill>", re.DOTALL)
_EDIT_RE = re.compile(r"<edit>\s*<old>(?P<old>.*?)</old>\s*<new>(?P<new>.*?)</new>\s*</edit>", re.DOTALL)
_ADD_RE = re.compile(r'<add\s+position="(?P<pos>after|before)"\s+ref="(?P<ref>.*?)">(?P<text>.*?)</add>', re.DOTALL)
_ANSWER_RE = re.compile(r"<answer>(?P<text>.*?)</answer>", re.DOTALL)


def parse_write_actions(text: str) -> list[dict]:
    """Extract actions in document order. Each: {op, ...}. Unrecognized text is ignored
    (the model is instructed to speak only in tags)."""
    if not text:
        return []
    spans: list[tuple[int, dict]] = []
    for m in _FILL_RE.finditer(text):
        spans.append((m.start(), {"op": "fill", "content": m.group("content").strip()}))
    for m in _EDIT_RE.finditer(text):
        spans.append((m.start(), {"op": "edit", "old": m.group("old").strip(), "new": m.group("new").strip()}))
    for m in _ADD_RE.finditer(text):
        spans.append((m.start(), {"op": "add", "position": m.group("pos"),
                                  "ref": m.group("ref").strip(), "text": m.group("text").strip()}))
    for m in _ANSWER_RE.finditer(text):
        spans.append((m.start(), {"op": "answer", "text": m.group("text").strip()}))
    return [a for _, a in sorted(spans, key=lambda s: s[0])]


async def _context_blocks(store, creator_id: str, instruction: str, brand: dict | None) -> tuple[str, str]:
    """(strategy_block, memory_block) — best-effort; each no-ops if its own flag is off."""
    strat = mem = ""
    try:
        from app import strategy_compiler
        strat = await strategy_compiler.strategy_block(store, creator_id)
    except Exception:
        pass
    try:
        from app import memory_v2
        mems = await memory_v2.retrieve(store, creator_id, instruction)
        mem = memory_v2.memory_block(mems)
    except Exception:
        pass
    return strat, mem


async def write_turn(store, creator_id: str, script_body: str, instruction: str,
                     brand: dict | None = None) -> dict:
    """One write-agent turn. Returns {"actions": [...], "raw": str}. Keyless ⇒ a mock
    <answer>. Flag-gated (off ⇒ {"actions": [], "mode": "off"})."""
    if not palo_flags.enabled(palo_flags.WRITE_AGENT):
        return {"actions": [], "mode": "off"}
    strat, mem = await _context_blocks(store, creator_id, instruction, brand)
    system, user = palo_prompts.write_agent_prompt(script_body, instruction, strat, mem)
    from app.prompt_store import get_prompt
    system = await get_prompt("palo.write.agent", system, store=store)
    raw = await anthropic_cached(system, user, OPUS, max_tokens=1500)
    if not raw:
        return {"actions": [{"op": "answer",
                             "text": "Tell me what to change and I'll suggest a precise edit."}],
                "raw": "", "mode": "mock"}
    actions = parse_write_actions(raw)
    if not actions:                          # model spoke prose -> treat as an answer
        actions = [{"op": "answer", "text": raw.strip()[:500]}]
    try:
        from app import ai_usage
        await ai_usage.record(store, creator_id, "write.turn", OPUS, 3000, 800)
    except Exception as e:
        logging.warning("[write_agent] usage record failed: %s", e)
    return {"actions": actions, "raw": raw, "mode": "live"}
