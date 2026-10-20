"""The v2 writer: one dumb call per script, payoff first.

The call writes the script and nothing else (no titles, scores, alt hooks or "why"):
metadata in the same call pulls the model back into assistant mode mid-sentence.

Prompt order (stable first, so the prefix caches):
  system   doctrine (identical for every creator)
  turns    the creator's real reels as earlier ASSISTANT turns (identical for every slot
           of a page; cache breakpoint on the last one)
  user     who they are, how they talk (voice profile), and THIS video: payoff, register,
           first-line shape, material, length, openers to avoid.

Output: {last_line, hook, body} in that order, so the ending is decided first and the hook
is written as its promise. The body ends with last_line.
"""
from __future__ import annotations

import json
import re

from app.scripts_v2 import doctrine, flags, llm

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["last_line", "hook", "body"],
    "properties": {"last_line": {"type": "string"}, "hook": {"type": "string"},
                   "body": {"type": "string"}},
}

_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def exemplar_turns(exemplars: list[dict]) -> list[dict]:
    """Real reels as earlier assistant turns. Cache breakpoint on the last one: the turns
    are the same for every slot of a page (and every page of a session)."""
    turns: list[dict] = []
    for i, ex in enumerate(exemplars or []):
        ask = ("Say one of your real reels again, word for word, the way you said it on camera."
               if i == 0 else "Another one, word for word.")
        turns.append(llm.text_turn("user", ask))
        turns.append(llm.text_turn("assistant", str(ex.get("text") or "").strip(),
                                   cache=(i == len(exemplars) - 1)))
    return turns


def creator_block(brand: dict) -> str:
    bits = []
    for label, key in (("niche", "niche"), ("what they do", "what_you_do"), ("known for", "known_for"),
                       ("talking to", "audience"), ("goal", "goal")):
        v = str(brand.get(key) or "").strip()
        if v:
            bits.append(f"{label}: {v}")
    never = [x for x in (brand.get("non_negotiables") or []) if isinstance(x, str) and x.strip()]
    line = "THE CREATOR: " + ("; ".join(bits) if bits else "a creator in their niche")
    if never:
        line += "\nTheir non-negotiables: " + "; ".join(never[:6])
    return line


def slot_brief(slot: dict, *, material_text: str, length: dict, avoid_openers: list[str] | None,
               register_desc: str = "", frame_desc: str = "", extra: str = "") -> str:
    lines = ["THIS VIDEO:",
             f"- topic: {slot.get('topic', '')}" + (f" ({slot['angle']})" if slot.get("angle") else "")]
    if slot.get("payoff"):
        lines.append(f"- the last line delivers this, once, in under 15 of their words (no recap): {slot['payoff']}")
    if register_desc:
        lines.append(f"- how it feels: {register_desc}")
    if frame_desc:
        lines.append(f"- the first line's shape: {frame_desc}")
    lines.append(f"- length: {length.get('lo', 90)} to {length.get('hi', 130)} words in total, "
                 f"paragraph breaks where they'd breathe")
    lines.append(material_text)
    av = [o for o in (avoid_openers or []) if o][:12]
    if av:
        lines.append("Openers already in front of this audience (don't share their shape or wording):\n"
                     + "\n".join(f'  "{o}"' for o in av))
    if extra:
        lines.append(extra)
    lines.append("Write last_line first, then hook (the opening line or two, a promise of last_line), "
                 "then body: everything after the hook, ending with last_line word for word.")
    return "\n".join(lines)


def build(*, brand: dict, voice_block: str, exemplars: list[dict], brief: str) -> tuple[list[dict], list[dict]]:
    system = llm.system_block(doctrine.text())
    who = creator_block(brand)
    talk = voice_block.strip() if voice_block else ""
    user = who + ("\n\nHOW THEY TALK:\n" + talk if talk else "") + "\n\n" + brief
    return system, exemplar_turns(exemplars) + [llm.text_turn("user", user)]


def _clean(s: str) -> str:
    s = (s or "").replace("\r", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def normalize(out) -> dict | None:
    """Validate the model's {last_line, hook, body}; make the body end with last_line."""
    if not isinstance(out, dict):
        return None
    hook, body, last = _clean(out.get("hook")), _clean(out.get("body")), _clean(out.get("last_line"))
    if not hook or not body:
        return None
    if body.lower().startswith(hook.lower()):
        body = body[len(hook):].lstrip(" \n")
    if last and not body.rstrip(" .!?").lower().endswith(last.rstrip(" .!?").lower()[-40:]):
        tail = _SENT_RE.split(body.strip())[-1] if body.strip() else ""
        if last.lower() not in body.lower() and last.lower() != tail.lower():
            body = f"{body.rstrip()} {last}".strip()
    return {"hook": hook, "body": body, "last_line": last} if body else None


def split_cta(body: str) -> tuple[str, str]:
    """(body_without_last_sentence, last_sentence). The app renders the CTA as its own
    editable line, so the last sentence always becomes `cta` (never empty) when the body
    has at least two sentences; paragraph breaks above it are kept."""
    b = body.rstrip()
    m = list(re.finditer(r"[.!?](?=[\s\"')\]]|$)", b))
    if len(m) < 2:
        return b, ""
    cut = m[-2].end()
    return b[:cut].rstrip(), b[cut:].strip()


async def write(*, brand: dict, voice_block: str, exemplars: list[dict], brief: str,
                model: str | None = None, effort: str | None = None,
                timeout: float | None = None, route: str = "scripts_v2.write") -> dict | None:
    system, messages = build(brand=brand, voice_block=voice_block, exemplars=exemplars, brief=brief)
    out, _u = await llm.call(model=model or flags.writer_model(), system=system, messages=messages,
                             schema=SCHEMA, max_tokens=1500, effort=effort or flags.effort(),
                             route=route, timeout=timeout)
    return normalize(out)


async def rewrite(draft: dict, problems: list[str], *, brand: dict, voice_block: str,
                  exemplars: list[dict], brief: str, model: str | None = None,
                  timeout: float | None = None) -> dict | None:
    """One targeted fix on the SAME conversation (cached prefix reused): the draft rides
    as the assistant's previous answer, then the named problems. Cut, don't add."""
    system, messages = build(brand=brand, voice_block=voice_block, exemplars=exemplars, brief=brief)
    messages.append(llm.text_turn("assistant", json.dumps(
        {"last_line": draft.get("last_line", ""), "hook": draft.get("hook", ""),
         "body": draft.get("body", "")}, ensure_ascii=False)))
    messages.append(llm.text_turn("user", "That reads like written copy in places. Fix exactly these, by cutting "
                                  "or saying it the way they would, never by adding new claims:\n- "
                                  + "\n- ".join(problems[:6])))
    out, _u = await llm.call(model=model or flags.writer_model(), system=system, messages=messages,
                             schema=SCHEMA, max_tokens=1500, effort=flags.effort(),
                             route="scripts_v2.rewrite", timeout=timeout)
    return normalize(out)


async def steer(script: dict, instruction: str, *, brand: dict, voice_block: str,
                exemplars: list[dict], material_text: str = "", model: str | None = None) -> dict | None:
    """The creator's refine request ("shorter", "funnier", free text) on an existing script."""
    current = "\n\n".join(x for x in (script.get("hook"), script.get("body"), script.get("cta")) if x)
    brief = ("THE SCRIPT AS IT STANDS:\n" + current + "\n\n"
             f"WHAT THEY WANT CHANGED: {instruction.strip()[:500]}\n"
             "Change only that. Keep the opening line and the last line unless the change is about them. "
             "'Shorter' means cut the weakest part, never the example. Keep every fact that's already there "
             "and add none." + (f"\n{material_text}" if material_text else "") +
             "\nReturn last_line, hook and body (body ends with last_line).")
    return await write(brand=brand, voice_block=voice_block, exemplars=exemplars, brief=brief,
                       model=model, route="scripts_v2.steer")
