"""Cheap, independent checks on finished scripts (helper model).

v1's judge shared the writer's rubric and scored ten axes; single-item rating judges are
the weakest method in the research (everything gets the same score). v2 asks only the
questions that matter, as yes/no, per page in one call:

  generic_hook   could this first line sit on a different video in this niche?
  payoff_landed  does the last line actually deliver what the first line promised?

and, for best-of-2, a pairwise "which would a viewer in this niche keep watching?" run in
both orders (position bias), ties broken by fewer lint tells.
"""
from __future__ import annotations

from app.scripts_v2 import flags, llm

REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdicts"],
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["index", "generic_hook", "specific_word", "payoff_landed", "note"],
        "properties": {"index": {"type": "integer"}, "generic_hook": {"type": "boolean"},
                       "specific_word": {"type": "string"}, "payoff_landed": {"type": "boolean"},
                       "note": {"type": "string"}}}}},
}

REVIEW_SYSTEM = (
    "You check short talking-head scripts before a creator films them. For each one answer two "
    "questions honestly.\n"
    "generic_hook: true if the first line could sit on top of a different video in this niche "
    "without changing a word (it names nothing this video specifically is about). specific_word: "
    "the one word or phrase in the first line that makes it specific, or \"\" if none.\n"
    "payoff_landed: true if the last line delivers what the first line promised, plainly.\n"
    "note: when either check fails, one short plain sentence saying what to change; else \"\"."
)

PICK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["keep", "why"],
    "properties": {"keep": {"type": "string", "enum": ["A", "B"]}, "why": {"type": "string"}},
}


def _show(i: int, s: dict) -> str:
    return (f"SCRIPT {i}\nfirst line: {s.get('hook', '')}\nrest: {s.get('body', '')}\n"
            f"promised payoff: {s.get('payoff', '')}")


async def review(scripts: list[dict], *, niche: str, model: str | None = None) -> dict[int, dict]:
    """{index: verdict}. Empty on failure (callers treat a missing verdict as a pass)."""
    if not scripts:
        return {}
    user = f"NICHE: {niche or 'general'}\n\n" + "\n\n".join(_show(i, s) for i, s in enumerate(scripts))
    out, _u = await llm.call(model=model or flags.helper_model(), system=REVIEW_SYSTEM,
                             messages=[llm.text_turn("user", user)], schema=REVIEW_SCHEMA,
                             max_tokens=900, route="scripts_v2.judge")
    res: dict[int, dict] = {}
    for v in ((out or {}).get("verdicts") or []) if isinstance(out, dict) else []:
        if isinstance(v, dict) and isinstance(v.get("index"), int) and 0 <= v["index"] < len(scripts) \
                and v["index"] not in res:
            res[v["index"]] = v
    return res


def problems_from(verdict: dict | None) -> list[str]:
    if not verdict:
        return []
    out = []
    if verdict.get("generic_hook"):
        out.append("The first line is generic: it could open any video in this niche. Make it name the "
                   "specific thing this video is about.")
    if verdict.get("payoff_landed") is False:
        out.append("The last line doesn't deliver what the first line promised. Make it say the payoff plainly.")
    note = str(verdict.get("note") or "").strip()
    if note and out:
        out.append(note[:240])
    return out


async def pick(a: dict, b: dict, *, niche: str, model: str | None = None) -> int | None:
    """0 keeps a, 1 keeps b, None when the two orders disagree or a call fails."""
    sys = ("Two drafts of the same short talking-head video. Pick the one a viewer in this niche would "
           "keep watching and believe the creator really said. One short reason.")
    m = model or flags.helper_model()

    async def _ask(x: dict, y: dict) -> str | None:
        user = f"NICHE: {niche or 'general'}\n\nA:\n{x.get('hook', '')}\n{x.get('body', '')}\n\nB:\n" \
               f"{y.get('hook', '')}\n{y.get('body', '')}"
        out, _u = await llm.call(model=m, system=sys, messages=[llm.text_turn("user", user)],
                                 schema=PICK_SCHEMA, max_tokens=200, route="scripts_v2.pick")
        return (out or {}).get("keep") if isinstance(out, dict) else None

    first, second = await _ask(a, b), await _ask(b, a)
    if first == "A" and second == "B":
        return 0
    if first == "B" and second == "A":
        return 1
    return None
