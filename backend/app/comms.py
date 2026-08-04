"""R2 — comms layer: the morning brief + the performance tone bible.

Ported from Palo offline/comms.py (§5.6 COMMUNICATION PASS) + pulse/weekly_pulse_batch.py.
Comms is "NEVER a rewriter: it reuses insight/artifact copy verbatim and only decides
WHAT the user gets" — it runs LAST, over the finished overnight products, and either
selects one honest morning message or stays silent. Yunicorn surfaces it as the in-app
morning card / push copy (Palo's lane was iMessage).

The two load-bearing honesty contracts, both Palo bug-derived:

  1. EMPTY BODY IS A SIGNAL, NOT A FAILURE. "If the input has no insight, no artifacts,
     and no other_actions, return {"body": ""}" — the empty body is the model's own
     "nothing worth texting" verdict and the caller must send nothing. Cold start with
     zero artifacts never even reaches the LLM; the honest silence IS the design.
  2. ARTIFACT KIND COMES FROM EXPLICIT INPUT FLAGS, NEVER INFERENCE. Palo inferred kind
     from project state (WRITING→script, else→idea), but an outline deliberately stays
     in IDEA state, so every outline the loop commissioned was announced to the creator
     as an "idea" — krishna and lisa both hit it. Here the input list an item arrives in
     (promoted_ideas / overnight_scripts / insights) IS its kind flag.

Plus a code validation Palo lacked: every quoted "Title" in the returned body must be an
exact input title, or the whole brief is discarded — a hallucinated brief never ships.

Keyless-green: no key ⇒ anthropic_cached_json returns None ⇒ honest empty (no mock copy
— a fabricated "your channel did X" morning text is worse than silence). Flag-gated by
MORNING_BRIEF. Recommended route: GET /v1/morning-brief.
"""
from __future__ import annotations

import json
import logging
import re

from app import ai_usage, palo_flags
from app.palo_llm import anthropic_cached_json
from prompts import HAIKU

MAX_BODY_CHARS = 300

_BRIEF_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["body"],
    "properties": {"body": {"type": "string"}},
}

# System prompt. Sources, operative rules ported VERBATIM:
#   - Palo offline/comms.py `_PROACTIVE_PROMPT` (the code default for LD flag
#     `proactive-daily-prompt`): the voice paragraph — "Lead with the single most
#     useful thing … Mention an artifact by its exact title in quotes … 1-3 short
#     sentences, <= 300 chars, at most one emoji … return {"body": ""}".
#   - The LIVE-served LD "stage" variation of `proactive-daily-prompt` (comms v1.1,
#     15.6KB, fetched 2026-08-03 — banked at scratchpad/palo_analysis/ld_served/
#     proactive-daily-prompt.md): rule 6's specificity/banned list, rule 8 (never nag,
#     never doom), rule 9 (nothing invented), rule 4's absence-of-news ban, and rule 2
#     (the machinery is invisible).
# Adaptation: retargeted from Palo's iMessage lane to Yunicorn's in-app morning card /
# push copy; day_read → day_header. The rules themselves are unchanged.
_BRIEF_PROMPT = """You write Yunicorn's proactive MORNING CARD to a creator — the one short in-app card (and its push copy) their channel greets them with each morning after the overnight pass. It runs LAST, over everything the pass did. Input JSON: {"day_header": "<the day's one-line read>", "artifacts": [{"title", "kind": "idea" | "script" | "insight", "pitch"?}], "other_actions": [...]}.

Voice: a sharp friend who watched their channel overnight — casual, direct, zero corporate. Lead with the single most useful thing: a made artifact by name, or the top insight's move. Mention an artifact by its exact title in quotes so they can find it in their library. Reuse the artifact's own title and pitch verbatim — you select, you never rewrite. The kind you announce is the kind the input declares — never guess it from the content. other_actions are background upkeep — at most a passing clause ("tuned your game plan too"), never the lead, never machinery words like bank/identity/pipeline. 1-3 short sentences, <= 300 chars, at most one emoji, no hashtags. No new facts beyond the input — never invent numbers. If the input has no insight, no artifacts, and no other_actions, return {"body": ""}.

Specificity IS the draw. Banned: withheld information ("you won't believe what I found"), manufactured urgency, "quick question" gambits, emoji unless the creator's own register runs on them. NEVER NAG, NEVER DOOM. A dormant or declining channel gets what still works and what tonight's work offers — never guilt about not posting, never alarm. NOTHING INVENTED. Every claim traces to the package. A thin night gets fewer, shorter sentences — brevity reads as confidence; padding reads as a product talking to hear itself. NEVER DESCRIBE THE ABSENCE OF NEWS. THE MACHINERY IS INVISIBLE: the creator has never heard of findings, verdicts, ledgers, commissions, briefs, banks, hydration, multipliers, medians, or the loop — and never will.

Return ONLY JSON: {"body": "<= 300 chars"}"""


# The performance tone bible, for any surface that talks numbers to a creator.
# Source: Palo pulse/weekly_pulse_batch.py — `_SYSTEM_PROMPT`'s CRITICAL language
# block, `_build_prompt`'s narrative-before-recommendations write-order + the
# "based ONLY on what you wrote in the narrative" grounding rule, and the BAD/GOOD
# worked pair — all verbatim. Inject into prompts that render performance copy.
PERF_TONE_RULES = """\
CRITICAL — language you must NEVER use:
- No raw performance multipliers: never write "2.3x", "38x", "3.0x typical", etc.
- No jargon: never write "algorithmically alive", "distribution signal", "performance ratio", "multiplier", "baseline", "P50", "median benchmark".
- No hedging verbs: never write "consider", "might want to", "you could", "perhaps try", "have you thought about". Recommendations must be direct imperatives: "Do X", "Lead with Y", "Double down on Z".
- Instead, describe performance in plain creator language:
  "performed far above your usual average"
  "became one of your strongest uploads recently"
  "continued attracting strong engagement"
  "stood out immediately compared to the rest of the week"
  "didn't get the traction your other posts usually do"

Write the narrative FIRST, before even thinking about recommendations. This matters because your recommendations must flow directly from what you wrote in the narrative. Then, based ONLY on what you wrote in the narrative above, give the recommendations. Do not introduce new observations that aren't already in the narrative.

BAD example: "This week's 26.5% view jump on roughly half the upload volume is entirely a bayashi_tv story. [abc123] alone punches at 3.0x typical — the highest multiplier."
GOOD example: "This week brought a strong jump in views even though you uploaded fewer videos. The biggest momentum came from 'Making Ramen in 60 Seconds', which immediately grabbed attention with a simple but visually satisfying food moment."
"""

# Palo's deterministic performance bands (pulse/vitals.py — "Shared with decide_cron's
# placeholder sensors so the two layers agree on what 'weak' / 'breakout' means").
_WEAK_LIFT_MAX = 0.6        # vitals._WEAK_LIFT_MAX
_BREAKOUT_LIFT_MIN = 2.0    # vitals._BREAKOUT_LIFT_MIN (== _VIEW_SPIKE_MULT)

# Phrases from the tone bible (weekly_pulse_batch._SYSTEM_PROMPT plain-language list;
# "too early" from _PERF_LABELS["unknown"]). NEVER a raw multiplier string.
_PHRASE_FAR_ABOVE = "performed far above your usual average"
_PHRASE_IN_LINE = "performed roughly in line with your usual"
_PHRASE_BELOW = "performed well below your usual"
_PHRASE_UNKNOWN = "too early to assess"


def perf_phrase(multiplier: float) -> str:
    """Plain-English performance phrase for a views-vs-baseline multiplier. The tone
    bible's hard rule: a creator never sees '2.3x' — code translates the band, the
    copy speaks creator language. Non-positive / NaN / junk ⇒ 'too early to assess'
    (no baseline is a question, not a verdict)."""
    try:
        m = float(multiplier)
    except (TypeError, ValueError):
        return _PHRASE_UNKNOWN
    if m != m or m <= 0:            # NaN or no signal
        return _PHRASE_UNKNOWN
    if m >= _BREAKOUT_LIFT_MIN:     # breakout band
        return _PHRASE_FAR_ABOVE
    if m <= _WEAK_LIFT_MAX:         # weak band
        return _PHRASE_BELOW
    return _PHRASE_IN_LINE          # typical band


# --- morning brief -------------------------------------------------------------

_QUOTED = re.compile(r'"([^"\n]{1,200})"|“([^”\n]{1,200})”')
_SENTENCE_END = re.compile(r'[.!?]["”]?(?=\s|$)')


def _wcut(text: str, n: int) -> str:
    """Cut at a word boundary (port of offline/comms.py _wcut). A hard [:600] slice
    ended gabe's day_read mid-word, and the copy pass reads that as corrupted input
    rather than a deliberate ellipsis."""
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",;:—-·") + " …"


def _sentence_cut(text: str, cap: int) -> str:
    """Truncate to <= cap chars at the last complete sentence boundary. Degrades to a
    word-boundary cut only when not even one sentence fits."""
    t = (text or "").strip()
    if len(t) <= cap:
        return t
    best = 0
    for m in _SENTENCE_END.finditer(t):
        if m.end() <= cap:
            best = m.end()
        else:
            break
    if best:
        return t[:best].rstrip()
    return _wcut(t, cap - 2)


def _quoted_titles(body: str) -> list[str]:
    """Every straight- or curly-quoted span in the body, in order of appearance."""
    out = []
    for m in _QUOTED.finditer(body or ""):
        s = (m.group(1) or m.group(2) or "").strip()
        if s:
            out.append(s)
    return out


def _payload_artifacts(artifacts: dict) -> list[dict]:
    """Flatten the caller's typed lists into [{title, kind, pitch?}]. The list an item
    arrived in IS its kind flag — explicit from the caller, never inferred from content
    (the Palo every-outline-announced-as-an-"idea" bug)."""
    out: list[dict] = []
    for source_key, kind in (("promoted_ideas", "idea"),
                             ("overnight_scripts", "script"),
                             ("insights", "insight")):
        for item in artifacts.get(source_key) or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            row: dict = {"title": title, "kind": kind}
            pitch = str(item.get("pitch") or "").strip()
            if pitch:
                row["pitch"] = _wcut(pitch, 300)
            out.append(row)
    return out


async def morning_brief(store, creator_id: str, artifacts: dict) -> dict:
    """One morning card's copy from the night's finished products.

    artifacts: {"promoted_ideas": [{"title", "pitch"}], "day_header": str,
    "overnight_scripts": [{"title"}], "insights": [{"title"}]} — all optional.
    Returns {"body": str, "mentioned": [titles actually quoted in the body]}.

    Honest-empty ({"body": "", "mentioned": []}) on: flag off; no titled artifacts at
    all (NO LLM call — day_header alone is context, not content, exactly as Palo's
    day_read never fired the text by itself); keyless / vendor failure; the model's
    own empty-body verdict; or a body that quotes a title we never provided (a
    hallucinated brief never ships). Never raises.
    """
    empty = {"body": "", "mentioned": []}
    if not palo_flags.enabled(palo_flags.MORNING_BRIEF):
        return empty
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    payload_artifacts = _payload_artifacts(artifacts)
    if not payload_artifacts:
        return empty                      # nothing worth texting — no LLM spend
    payload = json.dumps({
        "day_header": _wcut(str(artifacts.get("day_header") or ""), 600),
        "artifacts": payload_artifacts,
        "other_actions": [],              # reserved (Palo's strategy/identity tokens)
    })
    try:
        data = await anthropic_cached_json(
            _BRIEF_PROMPT, payload, _BRIEF_SCHEMA, HAIKU, max_tokens=300)
    except Exception as e:
        logging.warning("[comms] morning_brief LLM failed: %s", e)
        return empty
    if not isinstance(data, dict):
        return empty                      # keyless / exhausted retries — honest empty
    await ai_usage.record(store, creator_id, "comms.morning_brief", HAIKU, 900, 120)
    body = str(data.get("body") or "").strip()
    if not body:
        return empty                      # the model's "nothing worth texting" verdict
    known = {a["title"] for a in payload_artifacts}
    if any(q not in known for q in _quoted_titles(body)):
        logging.warning("[comms] morning_brief discarded: quoted a title not in input")
        return empty                      # never a hallucinated brief
    body = _sentence_cut(body, MAX_BODY_CHARS)
    mentioned, seen = [], set()
    for q in _quoted_titles(body):        # what actually survived truncation
        if q in known and q not in seen:
            mentioned.append(q)
            seen.add(q)
    return {"body": body, "mentioned": mentioned}
