"""Phase 2 (box 1) — idea bank: onboarding idea generation + eval gate → briefs.

Ported from Palo onboarding_agent/idea_generation.py + idea_eval.py. Generates 3
niche-specific video ideas (safest bet / creative stretch / high ceiling) by adapting
proven exemplar structures, then a cheap HAIKU eval gate drops any idea with zero
niche connection (Palo's guard against the "Minecraft creator gets a morning-routine
idea" failure). Survivors become `briefs` rows the feed reads from.

Keyless-green: no key ⇒ deterministic mock ideas + pass-through eval; no store ⇒ ideas
returned but not persisted. Flag IDEA_BANK gates the on-demand entry point.
"""
from __future__ import annotations

import logging

from app import ai_usage, palo_flags, palo_prompts
from app.palo_llm import anthropic_cached, anthropic_cached_json
from app.prompt_store import get_prompt
from app.recall_ledger import new_ulid
from prompts import HAIKU, SONNET

_IDEASET_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ideas"],
    "properties": {
        "ideas": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["title", "content"],
            "properties": {"title": {"type": "string"}, "content": {"type": "string"}}}},
        "justification": {"type": "string"}},
}

_EVAL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["results"],
    "properties": {"results": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["idea_index", "pass"],
        "properties": {"idea_index": {"type": "integer"}, "pass": {"type": "boolean"},
                       "reason": {"type": "string"}}}}},
}


def _context_from_brand(brand: dict) -> tuple[str, str, str, str]:
    """(creator_signals, channel_identity, topic, format) from Marque's Brand dict."""
    niche = (brand.get("niche") or "").strip()
    signals = "; ".join(x for x in [
        f"niche: {niche}" if niche else "",
        f"known for: {brand.get('known_for', '')}" if brand.get("known_for") else "",
        f"catchphrases: {', '.join(brand.get('catchphrases', []))}" if brand.get("catchphrases") else "",
    ] if x)
    identity = "; ".join(x for x in [
        f"audience: {brand.get('audience', '')}" if brand.get("audience") else "",
        f"voice: {brand.get('voice', '')}" if brand.get("voice") else "",
        f"platform: {brand.get('primary_platform', '')}" if brand.get("primary_platform") else "",
    ] if x)
    fmt = brand.get("primary_platform") or brand.get("camera_comfort") or "short-form"
    return signals or "(none)", identity or "(none)", niche or "content", fmt


def mock_ideas(brand: dict) -> list[dict]:
    niche = (brand.get("niche") or "your niche").strip()
    return [
        {"title": f"I Tried the Most-Watched {niche} Format for 7 Days",
         "content": f"Open on the setup every {niche} viewer recognizes. Escalate one constraint each day. End on the before/after. Film with your phone."},
        {"title": f"The {niche} Mistake Everyone Makes (I Tested It)",
         "content": f"Hook with the common belief. Run the experiment on camera. Reveal what actually happened. One take, talking to camera."},
        {"title": f"What 100 Hours of {niche} Taught Me",
         "content": f"Fast montage of the grind. Land three counterintuitive lessons. Close on the one that breaks out of {niche}. B-roll heavy."},
    ]


async def generate_ideas(store, brand: dict, exemplars: str = "",
                         knowledge: str = "basic", creator_id: str = "") -> list[dict]:
    signals, identity, _topic, _fmt = _context_from_brand(brand)
    base_sys, user = palo_prompts.idea_generation_prompt(signals, identity, exemplars, knowledge)
    system = await get_prompt("palo.idea.generate", base_sys, store=store)
    data = await anthropic_cached_json(system, user, _IDEASET_SCHEMA, SONNET, max_tokens=1400)
    if not isinstance(data, dict) or not data.get("ideas"):
        return mock_ideas(brand)                       # keyless / failure fallback
    ideas = [{"title": i.get("title", ""), "content": i.get("content", "")}
             for i in data["ideas"] if i.get("title")][:3]
    if len(ideas) < 3:
        return mock_ideas(brand)
    await ai_usage.record(store, creator_id, "idea.generate", SONNET, 3000, 900)
    return ideas


async def eval_ideas(store, ideas: list[dict], topic: str, fmt: str,
                     creator_id: str = "") -> list[bool]:
    """Per-idea pass flags. Keyless ⇒ all pass (never drop ideas we can't judge)."""
    if not ideas:
        return []
    base_sys, user = palo_prompts.idea_eval_prompt(topic, fmt, ideas)
    system = await get_prompt("palo.idea.eval", base_sys, store=store)
    data = await anthropic_cached_json(system, user, _EVAL_SCHEMA, HAIKU, max_tokens=500)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return [True] * len(ideas)
    verdict = {r.get("idea_index"): bool(r.get("pass", True)) for r in data["results"]}
    await ai_usage.record(store, creator_id, "idea.eval", HAIKU, 700, 200)
    # idea_index is 1-based in the prompt; default to pass if the judge omitted one.
    return [verdict.get(i + 1, verdict.get(i, True)) for i in range(len(ideas))]


def to_briefs(creator_id: str, ideas: list[dict], source: str = "onboarding") -> list[dict]:
    """Handles both idea shapes: onboarding ideas (title + content) and spitfire briefs
    (title + summary + beginning/middle/end)."""
    briefs = []
    for i, idea in enumerate(ideas):
        briefs.append({
            "id": new_ulid(), "creator_id": creator_id, "source": source,
            "title": idea.get("title", ""),
            "summary": idea.get("summary") or idea.get("content", ""),
            "beginning": idea.get("beginning", ""),
            "middle": idea.get("middle", ""),
            "ending": idea.get("ending") or idea.get("end", ""),
            "score": round(1.0 - i * 0.1, 3), "status": "new",
        })
    return briefs


# --- spitfire chain (overnight ideation) --------------------------------------
import re  # noqa: E402

_OPEN, _CLOSE = "<OPEN>", "<CLOSE>"
_NEW_RE = re.compile(r"^\s*TITLE:\s*(?P<title>.+?)\s*\nCONTENT:\s*(?P<content>.+?)\s*$", re.DOTALL)
_LEGACY_RE = re.compile(
    r"^\s*TITLE:\s*(?P<title>.+?)\s*\nSUMMARY:\s*(?P<summary>.+?)\s*\n"
    r"BEGINNING:\s*(?P<beginning>.+?)\s*\nMIDDLE:\s*(?P<middle>.+?)\s*\nEND:\s*(?P<end>.+?)\s*$",
    re.DOTALL)


def parse_thinking_output(output) -> dict | None:
    """Port of Palo nightly_utils.parse_thinking_output. Parses one <OPEN>…<CLOSE>
    block in either the new (TITLE+CONTENT) or legacy (TITLE/SUMMARY/BEGINNING/MIDDLE/
    END) format. Returns a normalized idea dict or None."""
    if not isinstance(output, str):
        return None
    s, e = output.find(_OPEN), output.find(_CLOSE)
    if s == -1 or e == -1 or s > e:
        return None
    content = output[s + len(_OPEN):e].strip()
    m = _NEW_RE.match(content)
    if m:
        p = m.groupdict()
        return {"title": p["title"].strip(), "content": p["content"].strip()}
    m = _LEGACY_RE.match(content)
    if not m:
        return None
    p = m.groupdict()
    return {"title": p["title"].strip(), "summary": p["summary"].strip(),
            "beginning": p["beginning"].strip(), "middle": p["middle"].strip(),
            "ending": p["end"].strip()}


def parse_all(output: str) -> list[dict]:
    """Parse every <OPEN>…<CLOSE> block in a multi-idea generation."""
    out = []
    for chunk in (output or "").split(_OPEN)[1:]:
        parsed = parse_thinking_output(_OPEN + chunk)
        if parsed and parsed.get("title"):
            out.append(parsed)
    return out


def _parse_ranking(text: str, n: int) -> list[int]:
    """'[3] > [1] > [2]' -> [2,0,1] (0-based, valid, deduped). Missing indices appended
    in original order so nothing is dropped."""
    order, seen = [], set()
    for m in re.finditer(r"\[(\d+)\]", text or ""):
        idx = int(m.group(1)) - 1
        if 0 <= idx < n and idx not in seen:
            order.append(idx)
            seen.add(idx)
    for i in range(n):
        if i not in seen:
            order.append(i)
    return order


def _channel_analysis(brand: dict) -> str:
    signals, identity, topic, fmt = _context_from_brand(brand)
    return f"topic: {topic}; format: {fmt}; {signals}; {identity}"


def _candidates_text(cands: list[dict]) -> str:
    return "".join(f"\n[{i + 1}] {c.get('title', '')}: {c.get('summary') or c.get('content', '')}"
                   for i, c in enumerate(cands))


async def spitfire(store, creator_id: str, brand: dict, exemplar: str = "",
                   n: int = 3) -> list[dict]:
    """Generator -> Critic -> Editor -> Ranker (<=4 LLM calls). Returns ranked brief
    dicts. Keyless / any-failure ⇒ deterministic mock. Never raises."""
    try:
        ca = _channel_analysis(brand)
        gsys, guser = palo_prompts.spitfire_generator_prompt(ca, exemplar, n)
        gen = await anthropic_cached(gsys, guser, SONNET, max_tokens=1600, temperature=1.0)
        cands = parse_all(gen) if gen else []
        if len(cands) < n:                                # keyless / parse-thin fallback
            cands = mock_ideas(brand)[:n]
            await ai_usage.record(store, creator_id, "spitfire.mock", SONNET, 0, 0)
            return to_briefs(creator_id, cands, source="spitfire")
        await ai_usage.record(store, creator_id, "spitfire.generate", SONNET, 3000, 1200)

        ctext = _candidates_text(cands)
        csys, cuser = palo_prompts.spitfire_critic_prompt(ctext, ca)
        crit = await anthropic_cached(csys, cuser, SONNET, max_tokens=800) or ""
        if crit:
            await ai_usage.record(store, creator_id, "spitfire.critic", SONNET, 1500, 400)

        esys, euser = palo_prompts.spitfire_editor_prompt(gen, crit, ca)
        edited = await anthropic_cached(esys, euser, SONNET, max_tokens=1600)
        edited_cands = parse_all(edited) if edited else []
        if len(edited_cands) == len(cands):
            cands = edited_cands
            await ai_usage.record(store, creator_id, "spitfire.editor", SONNET, 2000, 1200)

        rsys, ruser = palo_prompts.spitfire_ranker_prompt(_candidates_text(cands), ca, crit)
        rank_txt = await anthropic_cached(rsys, ruser, HAIKU, max_tokens=100) or ""
        order = _parse_ranking(rank_txt, len(cands))
        if rank_txt:
            await ai_usage.record(store, creator_id, "spitfire.rank", HAIKU, 600, 40)
        ranked = [cands[i] for i in order]
        return to_briefs(creator_id, ranked, source="spitfire")
    except Exception as e:
        logging.warning("[ideas] spitfire failed: %s", e)
        return to_briefs(creator_id, mock_ideas(brand)[:n], source="spitfire")


async def suggest_ideas(store, creator_id: str, brand: dict, source: str = "onboarding",
                        exemplars: str = "") -> list[dict]:
    """Full pipeline: generate → eval-filter → briefs → persist → return. Flag-gated.
    Never returns empty when generation produced ideas (keeps the top idea if the gate
    would drop them all). Swallows persistence errors."""
    if not palo_flags.enabled(palo_flags.IDEA_BANK):
        return []
    try:
        _, _, topic, fmt = _context_from_brand(brand)
        ideas = await generate_ideas(store, brand, exemplars, creator_id=creator_id)
        passes = await eval_ideas(store, ideas, topic, fmt, creator_id=creator_id)
        kept = [idea for idea, ok in zip(ideas, passes) if ok] or ideas[:1]
        briefs = to_briefs(creator_id, kept, source)
        if store is not None:
            for b in briefs:
                try:
                    await store.upsert_brief(b)
                except Exception as e:
                    logging.warning("[ideas] upsert_brief failed: %s", e)
        return briefs
    except Exception as e:
        logging.warning("[ideas] suggest_ideas failed: %s", e)
        return []
