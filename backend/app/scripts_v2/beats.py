"""Reference-reel mode ("make my version of this reel"), the TokScript way.

v1 handed the whole reel to the writer and said "never copy". v2 keeps what actually
transfers, the reel's beat structure, and throws away its words and story:
  1. one helper call turns the reel's transcript into a beat skeleton (what each beat DOES,
     e.g. "names a mistake the viewer is making", never what it says), plus a topic and a
     payoff for THIS creator's niche;
  2. the normal v2 writer writes those beats with the creator's own voice and material.
"""
from __future__ import annotations

import re

from app.scripts_v2 import flags, llm

ROLES = ["hook", "setup", "turn", "proof", "payoff", "exit"]

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["beats", "topic_for_creator", "payoff_for_creator", "format"],
    "properties": {
        "beats": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["role", "does"],
            "properties": {"role": {"type": "string", "enum": ROLES}, "does": {"type": "string"}}}},
        "topic_for_creator": {"type": "string"}, "payoff_for_creator": {"type": "string"},
        "format": {"type": "string"}},
}

SYSTEM = (
    "You take apart a short talking-head reel so another creator can use its STRUCTURE. List its "
    "beats in order (3 to 6). For each beat say what it DOES for the viewer in plain words, never "
    "what it says: 'names a habit the viewer has', 'admits a mistake', 'gives the one fix', 'ends on "
    "the action'. Then, for the other creator's niche, a topic that this structure fits and the "
    "payoff its last line would deliver. Never reuse the reel's story, numbers or wording."
)


async def skeleton(reel: dict, *, niche: str, audience: str = "") -> dict | None:
    text = str(reel.get("transcript") or reel.get("hook_text") or reel.get("caption") or "").strip()
    if len(text.split()) < 8:
        return None
    user = (f"THE REEL ({reel.get('platform', '')}, {int(reel.get('views') or 0)} views):\n{text[:1800]}\n\n"
            f"THE OTHER CREATOR'S NICHE: {niche or 'general'}" + (f"; talking to {audience}" if audience else ""))
    out, _u = await llm.call(model=flags.helper_model(), system=SYSTEM,
                             messages=[llm.text_turn("user", user)], schema=SCHEMA, max_tokens=700,
                             route="scripts_v2.beats")
    if not isinstance(out, dict):
        return None
    beats = [{"role": b["role"], "does": re.sub(r"\s+", " ", str(b.get("does") or "")).strip()[:160]}
             for b in (out.get("beats") or []) if isinstance(b, dict) and b.get("role") in ROLES and b.get("does")]
    topic = str(out.get("topic_for_creator") or "").strip()[:160]
    if len(beats) < 2 or not topic:
        return None
    return {"beats": beats[:6], "topic": topic, "payoff": str(out.get("payoff_for_creator") or "").strip()[:220],
            "format": str(out.get("format") or "")}


def beats_block(sk: dict) -> str:
    lines = ["STRUCTURE TO FOLLOW, beat by beat (this is another reel's shape; never its words, story or numbers):"]
    for i, b in enumerate(sk.get("beats") or []):
        lines.append(f"  {i + 1}. {b['role']}: {b['does']}")
    return "\n".join(lines)
