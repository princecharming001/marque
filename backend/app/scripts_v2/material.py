"""Material before words: 2-3 real particulars per slot, each tagged with where it came from.

Humans write from particulars (a price, a client, the thing they got wrong); the v1 writer
got {topic, angle} and invented the rest, which is where "your calorie math" came from.
One cheap helper-model call per page pulls particulars ONLY from what we actually have:

  own_reel     a thing the creator said in one of their real reels (ref R1..Rn)
  memory       something the creator told the app (memory facts / angle)
  profile      their own profile text (what they do, known for)
  niche_trend  what the niche is being promised this week (ref A1..An, from niche angles)
  mechanism    how or why something works, general knowledge, NO numbers

Every non-mechanism particular is checked in code against its source text; anything that
isn't actually there is dropped. Numbers survive only inside grounded particulars, and they
become the lint's `allowed_claims`, so any other number in the script is flagged.
"""
from __future__ import annotations

import logging
import re

from app.scripts_v2 import flags, llm

SOURCES = ("own_reel", "memory", "profile", "niche_trend", "situation", "mechanism")
MAX_PER_SLOT = 3

SYSTEM = (
    "You gather the raw material a creator will talk from. You never write the script. For each "
    "planned video, pick two or three particulars: concrete things the video can stand on. A "
    "particular is short (under 25 words) and specific: a number they actually said, a thing they "
    "do, a mistake they named, a situation their viewer is in, a promise the niche is making this "
    "week, or how something works.\n"
    "Sources, use the tag exactly:\n"
    "own_reel = something said in one of the creator's real reels below; set ref to its id (R1, R2...) "
    "and keep their words.\n"
    "memory = something the creator told us (THEY TOLD US).\n"
    "profile = their own profile text.\n"
    "niche_trend = one of THIS WEEK IN THE NICHE; set ref to its id (A1, A2...).\n"
    "situation = a concrete everyday moment this creator's viewer is in: a time, a place, an object, "
    "a thing they do (\"the gym bag that rides in the trunk all week\"). Second person or plain "
    "description, never 'I' or 'my', no statistics, no study, no result.\n"
    "mechanism = how or why something works, true for anyone in the niche. A mechanism never "
    "contains a number, a percent, a study, a name or a result.\n"
    "Every video needs at least one particular that is concrete enough to picture; when we know "
    "nothing personal, use a situation.\n"
    "Never invent a story, a client, a result or a number. If a video needs a personal detail we "
    "don't have, leave it out and put one short question for the creator in ask_user (or leave "
    "ask_user empty). Prefer own_reel and memory when they fit; a video with only mechanisms is fine."
)

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["slots"],
    "properties": {"slots": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["slot", "particulars", "ask_user"],
        "properties": {
            "slot": {"type": "integer"},
            "particulars": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["text", "source", "ref"],
                "properties": {"text": {"type": "string"},
                               "source": {"type": "string", "enum": list(SOURCES)},
                               "ref": {"type": "string"}}}},
            "ask_user": {"type": "string"}}}}},
}

_STOP = set("the a an and or but to of in on for with is are was be it that this you your i my me we they "
            "them their not no do does at as by so if just than then from what when how why who".split())
# Longest units first, whole-word, so "4 months" is one claim (not "4 m").
_NUM_RE = re.compile(r"\$?\d[\d,.]*(?:\s?(?:%|percent\b|minutes?\b|hours?\b|days?\b|weeks?\b|months?\b|"
                     r"years?\b|pounds?\b|lbs?\b|kg\b|k\b|m\b))?", re.I)


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (s or "").lower()) if len(w) > 2 and w not in _STOP}


def grounded(text: str, source: str, *, need: float = 0.6) -> bool:
    """True when most of the particular's content words appear in `source`."""
    w = _words(text)
    if not w:
        return False
    src = _words(source)
    return len(w & src) / len(w) >= need


def allowed_claims(particulars: list[dict]) -> list[str]:
    """Numbers the script may say: only those inside grounded, non-mechanism particulars."""
    out: list[str] = []
    for p in particulars or []:
        if p.get("source") == "mechanism":
            continue
        for m in _NUM_RE.finditer(p.get("text") or ""):
            tok = m.group(0).strip()
            if tok and tok not in out:
                out.append(tok)
    return out


def _brand_text(brand: dict) -> str:
    return " ".join(str(brand.get(k) or "") for k in ("what_you_do", "known_for", "niche", "audience")) + " " + \
        " ".join(str(x) for x in (brand.get("catchphrases") or []))


def _memory_text(memory: dict | None) -> str:
    if not memory:
        return ""
    parts: list[str] = []
    for k, v in memory.items():
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif isinstance(v, str):
            parts.append(v)
    return " ".join(parts)


def validate(raw: list[dict], *, samples: list[dict], brand: dict, memory: dict | None,
             angles: list[dict]) -> list[dict]:
    """Keep only particulars that are actually supported by their claimed source."""
    out: list[dict] = []
    mem_text, brand_text = _memory_text(memory), _brand_text(brand)
    for p in raw or []:
        if not isinstance(p, dict):
            continue
        text = re.sub(r"\s+", " ", str(p.get("text") or "")).strip()[:220]
        src, ref = p.get("source"), str(p.get("ref") or "").strip().upper()
        if not text or src not in SOURCES:
            continue
        ok = False
        if src == "own_reel":
            m = re.fullmatch(r"R(\d+)", ref)
            i = int(m.group(1)) - 1 if m else -1
            ok = 0 <= i < len(samples) and grounded(text, samples[i].get("text", ""))
        elif src == "memory":
            ok = bool(mem_text) and grounded(text, mem_text)
        elif src == "profile":
            ok = bool(brand_text.strip()) and grounded(text, brand_text)
        elif src == "niche_trend":
            m = re.fullmatch(r"A(\d+)", ref)
            i = int(m.group(1)) - 1 if m else -1
            ok = 0 <= i < len(angles)
            if ok:
                ref = str(angles[i].get("angle_id") or ref)
        elif src == "mechanism":
            ok = not re.search(r"\d|%|percent|study|studies|research|according to", text, re.I)
        elif src == "situation":
            ok = not re.search(r"%|percent|study|studies|research|according to|\b(i|i'm|i've|my|me|we|our)\b",
                               text, re.I)
        if ok:
            out.append({"text": text, "source": src, "ref": ref})
        if len(out) >= MAX_PER_SLOT:
            break
    return out


def render(particulars: list[dict], ask_user: str = "") -> str:
    """The MATERIAL section of the writer's brief."""
    if not particulars:
        return ("MATERIAL: nothing personal is known for this one. Talk about a concrete moment the viewer "
                "is in and why it works; use no statistics, no brackets, and no past events of the creator's.")
    lines = ["MATERIAL (use these and nothing else that could be checked):"]
    label = {"own_reel": "they said this in a real reel", "memory": "they told us",
             "profile": "from their profile", "niche_trend": "what the niche is talking about",
             "situation": "a moment their viewer knows", "mechanism": "how it works"}
    for p in particulars:
        lines.append(f"- {p['text']} ({label.get(p['source'], p['source'])})")
    # No [fill-in] placeholders in v2 (eval 2026-10-19: the judge read "[your number]" as an
    # unfinished script, and it is). `ask_user` is kept on the result for a future "answer
    # this to make it yours" prompt in the app; the writer talks about the viewer instead.
    lines.append("- No brackets or placeholders. Anything personal that isn't here, say about the viewer.")
    return "\n".join(lines)


def _user(slots: list[dict], *, samples: list[dict], brand: dict, memory: dict | None,
          angles: list[dict]) -> str:
    reels = "\n".join(f"R{i + 1}: {s.get('text', '')[:600]}" for i, s in enumerate(samples[:6])) or "(none)"
    told = _memory_text(memory)[:900] or "(nothing)"
    week = "\n".join(f"A{i + 1}: {a.get('title', '')}. Viewers are promised: {a.get('promise', '')}"
                     for i, a in enumerate(angles[:8])) or "(nothing fresh)"
    plan = "\n".join(
        f"slot {i + 1}: topic: {s.get('topic', '')}. angle: {s.get('angle', '')}. "
        f"payoff (last line): {s.get('payoff', '')}" for i, s in enumerate(slots))
    prof = _brand_text(brand).strip() or "(empty)"
    return (f"PROFILE: {prof}\n\nTHEIR REAL REELS:\n{reels}\n\nTHEY TOLD US: {told}\n\n"
            f"THIS WEEK IN THE NICHE:\n{week}\n\nPLANNED VIDEOS:\n{plan}\n\n"
            "Return {\"slots\": [...]} with one entry per planned video, in order.")


async def gather(slots: list[dict], *, brand: dict, samples: list[dict], memory: dict | None,
                 angles: list[dict] | None = None, model: str | None = None,
                 timeout: float | None = None) -> list[dict]:
    """Per slot: {"particulars": [...], "ask_user": str}. Empty material on any failure."""
    angles = angles or []
    empty = [{"particulars": [], "ask_user": ""} for _ in slots]
    if not slots:
        return []
    out, _usage = await llm.call(
        model=model or flags.helper_model(), system=SYSTEM,
        messages=[llm.text_turn("user", _user(slots, samples=samples, brand=brand, memory=memory,
                                              angles=angles))],
        schema=SCHEMA, max_tokens=1400, route="scripts_v2.material", timeout=timeout)
    got = (out or {}).get("slots") if isinstance(out, dict) else None
    if not isinstance(got, list):
        return empty
    res = []
    for i, _ in enumerate(slots):
        g = got[i] if i < len(got) and isinstance(got[i], dict) else {}
        parts = validate(g.get("particulars") or [], samples=samples, brand=brand, memory=memory, angles=angles)
        ask = str(g.get("ask_user") or "").strip()[:160]
        res.append({"particulars": parts, "ask_user": ask})
    dropped = sum(len((got[i] or {}).get("particulars") or []) for i in range(min(len(got), len(slots)))
                  if isinstance(got[i], dict)) - sum(len(r["particulars"]) for r in res)
    if dropped > 0:
        logging.info("[v2.material] dropped %d ungrounded particulars", dropped)
    return res
