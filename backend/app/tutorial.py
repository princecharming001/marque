"""R2 — first-script teach-back (tutorial pregen): the conversion moment where the
first script becomes a taught experience.

Ported from Palo onboarding_agent/tutorial_pregen.py (the pregen prompt + the
deterministic-replay design) and onboarding_agent/script_generation.py's two-field
{script, reasoning} contract: script generation emits a `reasoning` block written
"like you're briefing a colleague", never shown to the creator directly, and this
module uses it so the tutorial can teach the creator WHY each part was built this way.

Design (Palo write_pyro main.py:904 `_stream_pregenerated_step`): pregen ONCE per
script, store the returned steps blob, replay it deterministically step-by-step —
zero LLM cost and zero drift on replay. Persistence is caller-owned; this module
only returns the steps.

Keyless-green: no key / vendor failure / flag off ⇒ an honest deterministic 2-step
template that still teaches the hook (from which structural pattern it matches) and
the payoff, with highlights taken verbatim from the script itself. Never raises.
"""
from __future__ import annotations

import logging
import re

from app import ai_usage, palo_flags
from app.palo_llm import anthropic_cached_json
from app.prompt_store import get_prompt
from prompts import SONNET

_TUTORIAL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["steps"],
    "properties": {
        "steps": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "explanation", "highlight_text"],
            "properties": {"title": {"type": "string"},
                           "explanation": {"type": "string"},
                           "highlight_text": {"type": "string"}}}}},
}

# Pregen system prompt. The rule blocks below are ported VERBATIM from Palo
# onboarding_agent/tutorial_pregen.py:92-203 (selection rules, teaching calibration,
# privacy, script ownership, highlight rules) plus the VERIFICATION line from the
# served write-tutorial-pregen prompt variant ("highlight fewer lines rather than
# risk a mismatch"). Adapted only where Palo-specific: Palo's tiptap-HTML scripts
# become Marque's plain hook/body/cta text, Palo-as-"I" becomes the app's voice, and
# the {type,label,teaching} step shape becomes {title,explanation,highlight_text}.
_PREGEN_SYSTEM = """\
You are the app's teaching voice inside a short-form video tool. A script was just written \
for this creator's first video. Produce a complete tutorial that walks the creator through \
WHY the script works. The entire tutorial is pre-generated here and replayed step by step \
by the UI. There is no live agent and no follow-up call. Everything must be in this one output.

You are given script_reasoning, the writer's own explanation of the structural decisions \
made while writing this script. Use it. When explaining why the hook works, reference the \
actual reasoning behind WHY that hook structure was chosen. When explaining tension, \
reference the deliberate escalation decisions. The tutorial should feel like genuine insight \
into how the content was built, not a textbook overlay.

STEP 1: IDENTIFY THE NARRATIVE ELEMENTS PRESENT IN THE SCRIPT
- hook: the opening that creates investment
- tension: building stakes or anticipation
- re-engager: renews attention mid-video (pattern interrupt, new question, tonal shift)
- twist: an unexpected turn (ONLY if genuinely present, never force it)
- payoff: the satisfying conclusion

SELECTION RULES:
- hook and payoff are always present
- scroll stopper: only if there is a distinct visual beat BEFORE the verbal hook. In pure \
voiceover scripts, the hook IS the opening. Don't fabricate a scroll stopper.
- tension: in most scripts over 15 seconds
- re-engager: in most scripts over 20 seconds
- twist: only if there is a genuine reversal. Tension escalation is NOT a twist. \
Escalation ≠ twist.
- each element appears at most once, and order must match the script top-to-bottom

STEP 2: BUILD EACH TUTORIAL STEP
For each element produce a short title (the element name), the exact text to highlight \
(verbatim from the script), and an explanation: 2-3 sentences on what this element does and \
why it works HERE, plus one connection to the creator's specific niche.

TEACHING CALIBRATION based on creator_knowledge_level:
- none/basic: explain the concept from first principles, no jargon; teach by demonstration. \
"A hook is the opening that creates investment. Here's why this one works..."
- intermediate: skip definitions, name the mechanism. "This hook works because it creates a \
specific cognitive gap..."
- advanced: focus on the structural decision. "A claim-based hook instead of a question. \
Smart move because..."

PRIVACY (CRITICAL):
- NEVER reference specific creators, channel names, video titles, or view counts.
- NEVER reference internal system concepts (exemplars, dossiers, prompts, feature flags).
- Niche-level observations are fine: "In this space, the content that holds attention \
longest tends to..."
- Present all knowledge as the app's understanding of the niche, not sourced from \
individuals.

SCRIPT OWNERSHIP:
Use "the" or "this" when referencing the script, not "your." The app helped write this. It \
is a shared artifact. "The opening line works because..." not "Your opening line works \
because..."

Be specific to THIS script, not generic advice. No em dashes. 2 short paragraphs max per \
step. Scannable in 10 seconds.

HIGHLIGHT RULES:
- highlight_text MUST be an exact substring of the script (the frontend uses it for \
highlighting).
- Highlight exactly 1-2 lines per step: the SINGLE most powerful moment of that element.
- Quote the script VERBATIM. Sections should not overlap.
- Most of the script will NOT be highlighted. Gaps are expected.

VERIFICATION: Before outputting, confirm each highlight_text appears as an exact substring \
in the script. If you are unsure about the exact text of a line, highlight fewer lines \
rather than risk a mismatch.

Output JSON matching the schema exactly.
"""


def _script_text(script: dict | None) -> str:
    """The exact text the frontend highlights against: hook + "\\n" + body + "\\n" + cta."""
    s = script or {}
    return "\n".join(str(s.get(k) or "") for k in ("hook", "body", "cta"))


def _pregen_user(script: dict, reasoning: str, knowledge: str) -> str:
    s = script or {}
    return (f"<script_title>{s.get('title') or ''}</script_title>\n"
            f"<script>\n{_script_text(s)}\n</script>\n"
            f"<script_reasoning>\n{reasoning or 'Not available'}\n</script_reasoning>\n"
            f"<creator_knowledge_level>{knowledge or 'unknown'}</creator_knowledge_level>\n"
            "Generate the tutorial walkthrough.")


def validate_steps(steps: list[dict], script_text: str) -> list[dict]:
    """Code-side port of the highlight guard: highlight_text MUST be a character-for-
    character substring of the script (the frontend uses it for highlighting). Steps
    whose highlight doesn't match exactly are DROPPED, not repaired — Palo's rule is
    "highlight fewer lines rather than risk a mismatch". Also drops steps with no
    explanation (a step that teaches nothing is not a step). Pure."""
    out: list[dict] = []
    for i, s in enumerate(steps or []):
        if not isinstance(s, dict):
            continue
        highlight = str(s.get("highlight_text") or "").strip()
        explanation = str(s.get("explanation") or "").strip()
        if not highlight or not explanation or highlight not in script_text:
            continue
        out.append({"title": str(s.get("title") or "").strip() or f"Step {i + 1}",
                    "explanation": explanation, "highlight_text": highlight})
    return out


# --- hook pattern detection (mock path) ----------------------------------------
_QUESTION_RE = re.compile(r"\?\s*$")
_NUMBER_RE = re.compile(r"\d")
_CONTRARIAN_RE = re.compile(
    r"(?i)\b(stop|never|wrong|myth|lies?|lying|overrated|unpopular opinion"
    r"|nobody|no one|everyone (?:is|does|thinks|gets))\b")
_MID_ACTION_RE = re.compile(
    r"(?i)^(pov\b|so |and |okay,? so |watch |[a-z]+ing\b|i(?:'m| am| just| was)\b)")


def detect_hook_pattern(hook: str) -> str:
    """Cheap-regex classification of the hook's structural pattern, used by the mock
    path to teach WHY the first line works. Precedence (deterministic): question
    (ends with ?) > number_claim (contains a digit) > contrarian (pushes against a
    common belief) > mid_action (starts mid-scene: -ing open, POV, "so", first-person
    present) > other."""
    h = str(hook or "").strip()
    if not h:
        return "other"
    if _QUESTION_RE.search(h):
        return "question"
    if _NUMBER_RE.search(h):
        return "number_claim"
    if _CONTRARIAN_RE.search(h):
        return "contrarian"
    if _MID_ACTION_RE.match(h):
        return "mid_action"
    return "other"


# --- deterministic mock tutorial ------------------------------------------------
# Copy rules inherited from the pregen prompt: shared ownership ("the"/"this", never
# "your"), no em dashes, no creator references, teach by demonstration at none/basic
# and name the mechanic at intermediate/advanced.
_HOOK_TEACH = {
    "question": (
        "The first line asks something the viewer cannot answer yet. An open question is "
        "an itch, and the video is the only place to scratch it. Everything after this "
        "line is working toward that answer."),
    "number_claim": (
        "The first line stakes a specific, countable claim. A concrete number reads as "
        "evidence rather than opinion, and it quietly promises exactly what the rest of "
        "the video has to deliver."),
    "contrarian": (
        "The first line pushes against something most viewers assume is true. That "
        "friction does the work: a viewer who disagrees stays to see the case, and a "
        "viewer who agrees stays to feel proven right."),
    "mid_action": (
        "The first line starts in the middle of something already happening, with zero "
        "setup. Skipping the context is the point. The only way for a viewer to fill in "
        "what they missed is to keep watching."),
    "other": (
        "The first line implies something the viewer needs resolved before it makes "
        "sense. That gap between what is said and what is explained is what stops the "
        "scroll."),
}
_HOOK_MECHANIC = {
    "question": (" Structurally this is an open-loop question hook: the answer is "
                 "deliberately deferred to the payoff."),
    "number_claim": (" Structurally this is a quantified-claim hook: the number sets a "
                     "concrete expectation the payoff can close against."),
    "contrarian": (" Structurally this is a contrarian hook: stating the counter-position "
                   "up front opens a loop the argument then closes."),
    "mid_action": (" Structurally this is an in-medias-res open: it withholds context to "
                   "create an immediate curiosity gap, then spends the confirmation "
                   "window paying it off."),
    "other": (" Structurally this is a curiosity-gap open: the missing context is the "
              "retention engine."),
}
_PAYOFF_TEACH = (
    "The ending lands the answer the opening promised and then stops. No recap, no "
    "trailing outro. Ending exactly when the loop closes is what makes the video feel "
    "complete instead of fading out.")
_PAYOFF_TEACH_CTA = (
    "The close resolves the loop the first line opened, then asks for exactly one "
    "action. The ask works because it arrives after the value has landed, not before.")
_PAYOFF_MECHANIC = (
    " Payoff placement follows the retention doctrine: deliver the most satisfying "
    "information last, and end decisively.")


def _payoff_highlight(script: dict) -> str:
    """Verbatim payoff text: the cta if present, else the last non-empty body line
    (trimmed to its final sentence when long) — always an exact substring."""
    s = script or {}
    cta = str(s.get("cta") or "").strip()
    if cta:
        return cta
    lines = [ln.strip() for ln in str(s.get("body") or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    last = lines[-1]
    if len(last) > 160 and ". " in last:
        last = last.rsplit(". ", 1)[-1]
    return last


def _mock_tutorial(script: dict, knowledge: str = "basic") -> dict:
    """Keyless / flag-off template: hook and payoff are ALWAYS taught (the two
    elements Palo's selection rules mark always-present), with exact-substring
    highlights taken verbatim from the script itself. Deterministic, zero cost."""
    s = script or {}
    text = _script_text(s)
    named = (knowledge or "basic").strip().lower() not in ("", "none", "basic")
    steps: list[dict] = []
    hook = str(s.get("hook") or "").strip()
    if hook:
        pattern = detect_hook_pattern(hook)
        steps.append({
            "title": "Hook",
            "explanation": _HOOK_TEACH[pattern] + (_HOOK_MECHANIC[pattern] if named else ""),
            "highlight_text": hook,
        })
    payoff = _payoff_highlight(s)
    if payoff:
        has_cta = bool(str(s.get("cta") or "").strip())
        base = _PAYOFF_TEACH_CTA if has_cta else _PAYOFF_TEACH
        steps.append({
            "title": "Payoff",
            "explanation": base + (_PAYOFF_MECHANIC if named else ""),
            "highlight_text": payoff,
        })
    # Belt-and-suspenders: the template obeys the same exact-substring contract.
    return {"steps": validate_steps(steps, text), "mode": "mock"}


async def pregen_tutorial(store, creator_id: str, script: dict,
                          reasoning: str = "", knowledge: str = "basic") -> dict:
    """Pre-generate the first-script teach-back once. `script` is Marque's shape
    ({hook, body, cta, title, ...}); `reasoning` is the script generator's colleague
    briefing (script_generation's second output field). Returns
    {"steps": [{title, explanation, highlight_text}], "mode": "live"|"mock"}.

    Every LLM step is code-validated: highlight_text must be an exact substring of
    hook+"\\n"+body+"\\n"+cta or the step is dropped. The caller persists the blob and
    replays it deterministically — this function is never called on replay. Flag-off /
    keyless / failure ⇒ the deterministic template. Never raises."""
    s = script or {}
    if not palo_flags.enabled(palo_flags.TUTORIAL):
        return _mock_tutorial(s, knowledge)
    try:
        text = _script_text(s)
        if not text.strip():
            return _mock_tutorial(s, knowledge)
        system = await get_prompt("palo.tutorial.pregen", _PREGEN_SYSTEM, store=store)
        user = _pregen_user(s, reasoning, knowledge)
        data = await anthropic_cached_json(system, user, _TUTORIAL_SCHEMA, SONNET,
                                           max_tokens=1500)
        if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
            return _mock_tutorial(s, knowledge)          # keyless / vendor failure
        await ai_usage.record(store, creator_id, "tutorial.pregen", SONNET, 2600, 700)
        steps = validate_steps(data["steps"], text)
        if not steps:                                    # every highlight mismatched
            return _mock_tutorial(s, knowledge)
        return {"steps": steps, "mode": "live"}
    except Exception as e:
        logging.warning("[tutorial] pregen failed: %s", e)
        return _mock_tutorial(s, knowledge)
