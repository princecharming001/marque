"""Ported Palo prompt text (verbatim) + deterministic mock fallbacks, as (system, user)
builders per Marque convention. Grouped here (not in the 2600-line prompts.py) so the
port's prompts stay together; the hot ones are overridable via prompt_store keys
`palo.memory.extract` / `palo.ledger.extract`.

Source: Palo_Server/palo_python/memory/extractor.py + recall/ledger.py.
"""
from __future__ import annotations

# --- memory extraction (memory/extractor.py EXTRACTION_PROMPT, verbatim) ------
MEMORY_EXTRACTION_SYSTEM = """Extract ONLY specific, stable, ACTIONABLE memories — facts that should change how the assistant behaves on a future turn. When in doubt, do NOT extract.

EXTRACT ONLY IF:
- Explicit user preference stated ("I prefer X", "I want Y", "I don't like Z")
- Explicit memory instruction ("Remember that...", "Keep in mind...", "Note that...", "FYI...")
- A specific, durable creative/format constraint ("scripts in bullet points", "no emojis")
- Personal information the user wants remembered (name, location, goals, plans)

NEVER EXTRACT:
- Insights, inferred patterns, or observations about their content/performance/workflow — these change over time and Strategy already owns them
- Performance or analytics facts (views, what "worked", trends) — they go stale fast
- Summaries of what was generated this turn (ideas/scripts/outlines)
- Generic responses or pleasantries
- One-time requests (unless an explicit memory instruction)
- Obvious facts anyone would know
- Temporary context that won't be useful later

Memory Types (actionable only):
- content_context: Durable personal/identity facts the user states (name, location, goals, plans)
- creative_preference: Tone, style, format, or content constraints they want applied

Memory Scope:
- "user": Personal info that applies everywhere (name, location, general preferences, work style)
- "channel": Specific to one channel/account's content

CRITICAL:
- Personal information (name, location, timezone, general preferences) is ALWAYS "user" scope
- If unclear or applies to all channels, default to "user" scope

Return ONLY a JSON array (empty [] if nothing memorable):
[{"type": "content_context", "key": "short_description", "value": "detailed fact", "confidence": 0.7-1.0, "scope": "user"}]

Quality bar:
- confidence=1.0 for explicit statements, 0.8-0.9 for strong implications, 0.7 for weak signals
- Keys under 50 chars; values under 200 chars, actionable and specific
- If unsure, DON'T extract (fewer high-quality memories > many noisy ones)"""


def memory_extract_prompt(user_msg: str, assistant_msg: str) -> tuple[str, str]:
    user = f"User:\n{user_msg}\n\nAssistant:\n{assistant_msg}\n\nExtract memories as a JSON array."
    return MEMORY_EXTRACTION_SYSTEM, user


# --- recall ledger extraction (recall/ledger.py EXTRACTION_PROMPT, verbatim) --
LEDGER_EXTRACTION_SYSTEM = """Extract what the ASSISTANT proposed, decided, or judged this turn — for a ledger the assistant can recall later ("you suggested X 2 days ago") and to avoid re-pitching duplicates.

EXTRACT each distinct:
- IDEA / ANGLE / SCRIPT / OUTLINE the assistant proposed → kind: "idea" | "script" | "outline"; summary = the concept in one line.
- VERDICT the assistant gave on an idea / video / hook → kind: "verdict"; summary = what was judged; verdict: "good" | "bad" | "mixed"; score: 1-5 if stated.
- DECISION locked with the creator (content pillars, a named series, a signature, a cadence) → kind: "decision"; summary = the decision in one line.

NEVER extract: the creator's own statements or questions, analytics facts, generic chit-chat, or the assistant merely ASKING a question. Only concrete things the assistant put forward or the two of you locked in.

Return ONLY a JSON array (empty [] if nothing). Each item:
{"kind":"idea|script|outline|verdict|decision","summary":"<=200 chars","verdict":"good|bad|mixed (optional)","score":1-5 (optional)}"""


def ledger_extract_prompt(user_msg: str, assistant_msg: str) -> tuple[str, str]:
    user = f"User:\n{user_msg}\n\nAssistant:\n{assistant_msg}\n\nExtract the assistant's proposals/decisions/verdicts as a JSON array."
    return LEDGER_EXTRACTION_SYSTEM, user
