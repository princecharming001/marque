"""Hook Lab v2: five first lines for one video, each a DIFFERENT shape, in the creator's voice.

v1 generated six hooks with no posts in front of the model and ranked them with a judge
that capped questions; the winner was then forced verbatim into the script. v2:
  - written with the creator's real reels as earlier turns (same as the writer), their own
    openers and the niche's current openers in view;
  - one hook per opener frame, distinct frames enforced in code;
  - each carries a plain label, a one-line why, and whether it follows the shape of one of
    the creator's reels or a niche opener;
  - three plain checks instead of a number: specific to this video? promises a payoff?
    in your voice? (the first two from one helper call, the third from the lint).
Legacy fields (text, signal, strength) stay so older builds render unchanged.
"""
from __future__ import annotations

import prompts
from app.scripts_v2 import doctrine, flags, lint, llm, writer

N_HOOKS = 5

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["hooks"],
    "properties": {"hooks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["frame", "text", "why", "modeled_on"],
        "properties": {"frame": {"type": "string", "enum": list(prompts.OPENER_FRAMES)},
                       "text": {"type": "string"}, "why": {"type": "string"},
                       "modeled_on": {"type": "string", "enum": ["your_reel", "niche", ""]}}}}},
}

CHECK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["checks"],
    "properties": {"checks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["index", "specific", "promises_payoff"],
        "properties": {"index": {"type": "integer"}, "specific": {"type": "boolean"},
                       "promises_payoff": {"type": "boolean"}}}}},
}


def _brief(topic: str, own: list[str], niche: list[str]) -> str:
    frames = "\n".join(f"  {k}: {v}" for k, v in prompts.OPENER_FRAMES.items())
    lines = [f"THE VIDEO: {topic}",
             f"Write {N_HOOKS} different first lines for it, the way you'd actually open on camera. Each "
             "one a different shape from this list (set frame to its name):", frames,
             "why: one plain sentence on why that line makes someone stay.",
             "modeled_on: your_reel when the line follows the shape of one of YOUR OPENERS below, niche "
             "when it follows one of the NICHE OPENERS, else \"\". Follow a shape, never the words."]
    if own:
        lines.append("YOUR OPENERS:\n" + "\n".join(f'  "{o}"' for o in own[:8]))
    if niche:
        lines.append("NICHE OPENERS (other creators, this week):\n" + "\n".join(f'  "{o}"' for o in niche[:8]))
    lines.append("Each line is under 20 words. No greeting, no 'here's the thing', no 'ever wondered'.")
    return "\n".join(lines)


async def generate(topic: str, *, brand: dict, voice_block: str, exemplars: list[dict],
                   own_openers: list[str], niche_openers: list[str],
                   lint_profile: dict | None = None, voice_confidence: str = "low") -> list[dict]:
    system = llm.system_block(doctrine.text())
    who = writer.creator_block(brand)
    user = who + ("\n\nHOW THEY TALK:\n" + voice_block if voice_block else "") + "\n\n" + \
        _brief(topic, own_openers, niche_openers)
    messages = writer.exemplar_turns(exemplars) + [llm.text_turn("user", user)]
    out, _u = await llm.call(model=flags.writer_model(), system=system, messages=messages, schema=SCHEMA,
                             max_tokens=900, effort=flags.effort(), route="scripts_v2.hooklab")
    raw = (out or {}).get("hooks") if isinstance(out, dict) else None
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    hooks: list[dict] = []
    for h in raw:
        if not isinstance(h, dict):
            continue
        frame, text = h.get("frame"), " ".join(str(h.get("text") or "").split())
        if frame not in prompts.OPENER_FRAMES or frame in seen or not text:
            continue
        seen.add(frame)
        hooks.append({"frame": frame, "text": text[:200], "why": str(h.get("why") or "")[:200],
                      "modeled_on": h.get("modeled_on") if h.get("modeled_on") in ("your_reel", "niche") else ""})
        if len(hooks) >= N_HOOKS:
            break
    if not hooks:
        return []

    chk_user = f"VIDEO: {topic}\n\n" + "\n".join(f"[{i}] {h['text']}" for i, h in enumerate(hooks))
    chk, _u2 = await llm.call(
        model=flags.helper_model(),
        system=("For each first line of a short video answer: specific = it names something only THIS "
                "video is about (could not open a different video unchanged); promises_payoff = it "
                "makes a promise or opens a question the video then answers."),
        messages=[llm.text_turn("user", chk_user)], schema=CHECK_SCHEMA, max_tokens=400,
        route="scripts_v2.hooklab_check")
    checks = {c["index"]: c for c in ((chk or {}).get("checks") or [])
              if isinstance(c, dict) and isinstance(c.get("index"), int)} if isinstance(chk, dict) else {}

    res = []
    for i, h in enumerate(hooks):
        f = lint.lint_script({"hook": h["text"], "body": "", "cta": ""}, profile=lint_profile or None,
                             recent_openers=own_openers + niche_openers or None)
        hard = [x for x in f if x.severity == lint.HARD]
        c = checks.get(i, {})
        specific = bool(c.get("specific")) and not hard
        payoff = bool(c.get("promises_payoff"))
        in_voice = voice_confidence in ("medium", "high") and not hard
        res.append({
            "text": h["text"], "signal": prompts.FRAME_TO_SIGNAL.get(h["frame"], "curiosity"),
            "strength": 55 + 15 * specific + 15 * payoff + 10 * in_voice,
            "label": prompts.FRAME_LABELS.get(h["frame"], ""), "why": h["why"],
            "modeledOn": h["modeled_on"], "frame": h["frame"],
            "checks": {"specific": specific, "promisesPayoff": payoff, "inYourVoice": in_voice},
            "lintCodes": sorted({x.code for x in f}),
        })
    res.sort(key=lambda x: x["strength"], reverse=True)
    return res
