"""R2 (CHANNEL_IDENTITY) — the channel-identity document: the substrate every
generation consumes.

Ported from Palo's onboarding identity organism:
  * Path B cold start — LD flag `onboarding-prompt-creator-identity` variation "main"
    (fetched 2026-08-03) == onboarding_agent/creator_identity_generation.py
    CREATOR_IDENTITY_PROMPT: identity from conversation/quiz signals alone, voice
    inferred from HOW the user writes, 2-3 hook lines as voice anchors, the FAILED
    IDENTITY MARKERS anti-horoscope block + THE TEST.
  * Synthesis-mode ladder — LD flag `onboarding-prompt-identity-generation` variation
    "stage" (onboarding_agent/identity_generation.py's leaner V2 rework):
    STRONG/PARTIAL/THIN data modes + CREATOR PROFILE CHECK + data_confidence output.
  * Established path — onboarding_agent/established_identity_generation.py
    ESTABLISHED_IDENTITY_PROMPT (LD `onboarding-prompt-established-identity`, LD ≡
    code): Layer 1 vs Layer 2 abstraction + the ghostwriter-brief test, for re-runs
    once real posts exist.

Yunicorn adaptations: talking-head-first product (verbal_primacy usually high, dial
kept); the doc is HONEST — built from quiz/chat only ⇒ data_confidence "low" (code
clamps it — see _normalize) and nothing is ever presented as observed performance.

Keyless-green: no key ⇒ deterministic doc derived from the creator's own Brand fields
(specific, never horoscope filler); no store ⇒ no persistence. Persistence targets a
`channel_identity` JSONB column on `creators` (integrator adds it; every helper
degrades if it doesn't exist yet). Flag CHANNEL_IDENTITY gates the entry points;
real_creator() gates every read/write so demo/default traffic never lands in the DB.
"""
from __future__ import annotations

import json
import logging

from app import ai_usage, palo_flags
from app.palo_llm import anthropic_cached_json
from app.prompt_store import get_prompt
from prompts import SONNET

_MACRO_DIALS = ("verbal_primacy", "visual_primacy", "content_originality",
                "production_level", "methodical_planning", "factuality_level")
_LEVELS = {"low", "mid", "high"}
_CONFIDENCE = {"high", "medium", "low"}

_IDENTITY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["niche_role", "primary_function", "content_type", "voice_and_tone",
                 "voice_anchors", "macro_style", "creator_context",
                 "durable_constraints", "data_confidence"],
    "properties": {
        "niche_role": {"type": "string"},
        "primary_function": {"type": "string"},
        "content_type": {"type": "string"},
        "voice_and_tone": {"type": "string"},
        "voice_anchors": {"type": "array", "items": {"type": "string"}},
        "macro_style": {
            "type": "object", "additionalProperties": False,
            "required": list(_MACRO_DIALS),
            "properties": {d: {"type": "string"} for d in _MACRO_DIALS}},
        "creator_context": {"type": "string"},
        "durable_constraints": {"type": "array", "items": {"type": "string"}},
        "data_confidence": {"type": "string"},
    },
}


# --- cold-start identity (Path B) ----------------------------------------------
# VERBATIM sources, section by section:
#   <role>/<identity_fields>/<anti_patterns>: LD `onboarding-prompt-creator-identity`
#     variation "main" == creator_identity_generation.py CREATOR_IDENTITY_PROMPT
#     (the LD text adds the LANGUAGE rule; otherwise identical to code).
#   <relevance_assessment>: LD `onboarding-prompt-identity-generation` variation
#     "stage" — the synthesis-mode ladder + CREATOR PROFILE CHECK + data_confidence.
# Marked Yunicorn adaptations:
#  (Y1) Output is ONE JSON object matching _IDENTITY_SCHEMA (structured output)
#       instead of Palo's two-block <identity>/<reasoning> format.
#  (Y2) Field set trimmed to the doc Marque consumes: image / creator_summary /
#       visual_language / length_and_formats dropped (talking-head product derives
#       visuals from its own STYLES system); niche_role + durable_constraints added
#       (Marque's non_negotiables need a first-class home). VOICE ANCHORS promoted
#       from inside voice_and_tone to its own array (merges main's "2-3 example hook
#       lines" with identity-generation main's "anchors prevent the voice description
#       from drifting into generic inference" and stage's PARTIAL/THIN example rule).
#  (Y3) STRUCTURAL PATTERNS section omitted — that asset lives in ideas.py's
#       structural_patterns slot / the exemplar bank, not the identity doc.
#  (Y4) The empty-exemplar paragraph closing <relevance_assessment> and the whole
#       <honesty_rules> block are Yunicorn additions: no niche exemplar DB exists at
#       cold start, so the ladder must land on THIN honestly instead of inventing.
#  (Y5) Talking-head-first note in MACRO STYLE.
COLD_IDENTITY_SYSTEM = """\
<context>

<creator_signals>
{creator_signals}
</creator_signals>

<conversation_history>
{conversation_history}
</conversation_history>

<exemplar_context>
{exemplar_context}
</exemplar_context>

</context>

<role>
Your task: produce a Channel Identity that will power every downstream interaction this creator has with Yunicorn. A downstream LLM reading this must be able to write captions, scripts, and ideas that feel native to this creator's channel. If the identity is generic, everything downstream is generic.

You have: the creator's stated signals from onboarding, the raw conversation history (for inferring communication style and personality), and optionally exemplar/niche context from proven creators in their lane.

LANGUAGE: The identity (including example hooks, voice descriptions, and all text) MUST be in the creator's language, inferred from the conversation_history. Exemplar data may be in any language — use it for structure and mechanics only, not language. If the creator spoke English, the identity is in English. No exceptions.
</role>

<relevance_assessment>
BEFORE building the identity, assess whether the exemplar data (if any) matches the creator's stated direction.

For each exemplar or niche document, ask: is this creator making content in the same space as what MY creator described?

Sort each as:
- RELEVANT: Same topic or closely adjacent. Use fully.
- STRUCTURAL MATCH: Different topic but similar content format. Use for format, pacing, visual language. Do NOT transfer topic-specific vocabulary or domain terminology.
- OFF-TOPIC: Neither topic nor format applies. Discard.

Then determine synthesis mode:
- STRONG DATA: At least one RELEVANT niche. Build identity primarily from niche patterns.
- PARTIAL DATA: No RELEVANT, but STRUCTURAL MATCH exists. Extract format patterns only. Build topic-specific fields (voice, content type, elements) from the creator's direction.
- THIN DATA: All OFF-TOPIC. Build entirely from creator's direction and general knowledge. Do NOT import content patterns, voice, visual language, or topic-specific elements from irrelevant niches.

CREATOR PROFILE CHECK: Even when niche data is structurally useful, check that imported patterns make sense for THIS creator. Read the personality signals in the creator's own words. If they indicate a male college-age record label owner, do not import content patterns from female beauty influencers, even if the format overlaps. The viral MECHANICS may transfer (bait-and-switch, comedic reveals). The CONTENT and TONE must match the actual creator's world.

Output data_confidence: "high" (STRONG), "medium" (PARTIAL), or "low" (THIN).

If <exemplar_context> is empty, there is nothing to assess: you are in THIN DATA mode by definition. data_confidence is "low", and every field is built from the creator's own words. Do not invent exemplar evidence.
</relevance_assessment>

<identity_fields>

NICHE ROLE:
The lane this creator occupies in their niche, in one line. Under 14 words. "The nurse who explains ER shifts in 30 seconds," not "a healthcare creator."

PRIMARY FUNCTION:
What this channel fundamentally does. Under 14 words. Must be specific enough to distinguish from other creators in the same broad niche.

CONTENT TYPE:
The specific format and delivery. Under 24 words. Concrete enough to picture the content immediately.

CREATOR CONTEXT:
Who this creator is beyond their content. Pull from creator_signals: brand names, role, catchphrases, specific projects, demographic signals. Include details that make downstream ideas feel personalized. 1-3 sentences.

VOICE AND TONE:
The full communicative texture. 60-120 words.
Capture: humor style, irony vs sincerity, slang/register, energy level, what kind of person this feels like talking to. Pull from conversation_history to infer their actual communication style (did they type in lowercase? use slang? brief or verbose?).
A downstream LLM reading this should be able to write a caption that lands for this channel.
This must be rich enough to generate from.

VOICE ANCHORS:
2-3 example hook lines or captions that demonstrate how this voice sounds. These anchors prevent the voice description from drifting into generic inference. When exemplar data is RELEVANT, adapt from its language. When data is PARTIAL or THIN, generate original examples that match the creator's tone and topic, reusing their catchphrases verbatim where they fit.

MACRO STYLE:
- verbal_primacy: low (visual-driven) | mid | high (narration-heavy)
- visual_primacy: low (talking head) | mid | high (editing-driven)
- content_originality: original concepts | mid | reactive/remix
- production_level: lo-fi/raw | mid | polished
- methodical_planning: scripted | mid | improvisational
- factuality_level: educational | mid | entertainment
This is a talking-head-first product, so verbal_primacy will usually be high and visual_primacy low — but calibrate from the creator's actual signals, don't assume.

DURABLE CONSTRAINTS:
The creator's hard rules, verbatim where stated: non-negotiables, banned words or topics, format constraints. Empty list if none stated. Never invent constraints.

DATA CONFIDENCE: high (strong exemplar match) | medium (structural match) | low (thin data)
</identity_fields>

<anti_patterns>
FAILED IDENTITY MARKERS (if your output resembles any of these, rewrite):
- Primary function is a category label: "Deliver short-form content that entertains and engages"
- Voice section reads like a horoscope: "authentic, relatable, and engaging"
- Creator context is empty: "a content creator in the fitness space"
- Content type is abstract: "educational content" instead of "quick 30-second breakdowns filmed on phone between shifts"

THE TEST: Read each field and ask, "Could this describe a different creator in the same niche?" If yes, it's too generic.
</anti_patterns>

<honesty_rules>
This identity may be built from quiz answers and chat alone. When it is:
- data_confidence is "low", and the doc must read as the creator's self-description, sharpened — never as observed data.
- Never reference "your videos", "your audience data", or performance you have not seen.
- Voice inferred from HOW the creator writes (lowercase? slang? brief or verbose?) is real signal — use it confidently, but never claim analysis that didn't happen.
</honesty_rules>

Return ONLY the JSON object matching the schema you were given. No prose outside it.
"""


# --- established identity (re-run once real posts exist) -----------------------
# VERBATIM source: onboarding_agent/established_identity_generation.py
# ESTABLISHED_IDENTITY_PROMPT (== LD `onboarding-prompt-established-identity`; the
# analysis report confirms LD ≡ code): the <role> ghostwriter-brief framing, the
# <abstraction_principle> Layer 1 vs Layer 2 block, and the hard-rules test line.
# Yunicorn adaptations: output is _IDENTITY_SCHEMA (same doc shape as the cold path;
# narrative/elements schemas + primers not ported — Marque's doctrine/exemplar bank
# own structure); a data_confidence rule is added (Palo's established path had none);
# reference_creators framing for optional exemplar context is kept from the source's
# Path A branch.
ESTABLISHED_IDENTITY_SYSTEM = """\
<context>
Channel: {channel_name}

<video_analyses>
{video_analyses}
</video_analyses>

<exemplar_context>
{exemplar_context}
</exemplar_context>
</context>

<role>
You are the Channel Identity Synthesizer for established creators.

You receive the creator's own video analyses (their top-performing videos).
Your job is to produce a Channel Identity that captures the creator's
REUSABLE PATTERNS — their voice, structural instincts, visual language,
and creative machinery — abstracted from specific video content.

This identity is NOT a description of what they've already made.
It IS a blueprint for what they should make NEXT.

Think of it like this: if you watched 10 of their videos and then had to
brief a ghostwriter to create an 11th that the audience wouldn't question,
what would that brief contain? That's the identity.
</role>

<abstraction_principle>
THE CRITICAL DISTINCTION: PATTERNS vs CONTENT

When you see the creator's videos, you'll notice two layers:

LAYER 1 — SPECIFIC CONTENT (do NOT codify):
- Character names, specific scenarios, specific plot points
- The exact topics or subjects of individual videos
- Specific jokes, catchphrases tied to one video

LAYER 2 — REUSABLE PATTERNS (DO codify):
- Structural skeleton: how they open, build, and resolve
- Voice mechanics: tone, pacing, vocabulary register, energy arc
- Visual grammar: shot types, editing rhythm, lighting philosophy
- Audience contract: what the viewer expects and how it's delivered
- Content axes: the DIMENSIONS along which their videos vary

Example of WRONG identity output:
"Derek is the antagonist who always gets fired in an ironic way"
→ This describes one storyline, not a pattern.

Example of RIGHT identity output:
"A named antagonist whose downfall is self-inflicted — institutional
justice delivered through formal process language, never personal revenge"
→ This is a reusable pattern that generates infinite new stories.

When writing any field, NEVER reference specific video titles,
character names, or plot details. Always abstract to the pattern.
The one exception: voice_anchors are example lines in the creator's register —
adapt their real language into NEW lines, don't quote one video verbatim.
</abstraction_principle>

<instructions>
ANALYSE each video for:
- Hook and opening MECHANICS (not specific hooks)
- Narrative STRUCTURE and pacing patterns
- Voice, tone, and energy arc
- What STRUCTURAL choices correlate with top performance

SYNTHESISE across videos to extract:
- Signature MOVES (reusable structural patterns)
- Format tendencies (verbal vs visual dominant, production level)
- The AUDIENCE CONTRACT (what viewers expect from this creator)

OUTPUT the identity as one JSON object:
- niche_role, primary_function, content_type — the creator's lane, abstracted
- creator_context — who they are beyond the content, from evidence only
- voice_and_tone / voice_anchors — described as reusable patterns + example lines
- macro_style dimensions — calibrated from actual content
- durable_constraints — hard rules the catalog proves they keep
- data_confidence — "high" when 4+ analyzed posts ground every field, "medium"
  when the catalog is thinner; never claim more than the evidence supports
</instructions>

<hard_rules>
- Every field must be grounded in patterns observed across multiple videos
- NEVER reference specific video titles, character names, or plot points
- primary_function: <14 words; niche_role: <14 words; content_type: <24 words
- voice_and_tone: 60-120 words
- Test each field: "Could a writer use this to create a NEW video
  the audience would recognize as this creator's?" If not, it's too specific.
- THE TEST still applies: "Could this describe a different creator in the same
  niche?" If yes, it's too generic.
</hard_rules>

Return ONLY the JSON object matching the schema you were given. No prose outside it.
"""


# --- context builders ----------------------------------------------------------

def _creator_signals(brand: dict) -> str:
    b = brand if isinstance(brand, dict) else {}
    keep = ("niche", "what_you_do", "audience", "known_for", "goal", "catchphrases",
            "voice", "non_negotiables", "primary_platform", "stage",
            "posting_frequency", "biggest_blocker", "camera_comfort", "why_now")
    signals = {k: b[k] for k in keep if b.get(k)}
    return json.dumps(signals, indent=2, default=str) if signals else "(none)"


def _chat_text(chat_history) -> str:
    if isinstance(chat_history, str):
        return chat_history.strip() or "No conversation history available."
    lines = []
    for m in (chat_history or [])[-40:]:
        if isinstance(m, dict):
            lines.append(f"{m.get('role', 'user')}: {m.get('content') or m.get('message', '')}")
    return "\n".join(lines)[-8000:] or "No conversation history available."


def _posts_text(posts: list[dict]) -> str:
    """Modeled on established_identity_generation.py's analysis formatting:
    title (+views) / summary / transcript excerpt per post, '---' separated."""
    parts = []
    for p in posts[:20]:
        title = str(p.get("title") or p.get("caption") or "").strip()
        header = f"Post: {title or '(untitled)'}"
        try:
            views = int(p.get("views") or p.get("plays") or 0)
            if views:
                header += f" ({views:,} views)"
        except (TypeError, ValueError):
            pass
        lines = [header]
        summary = str(p.get("summary") or p.get("dossier_summary") or "").strip()
        if summary:
            lines.append(f"Summary: {summary[:600]}")
        transcript = str(p.get("transcript") or "").strip()
        if transcript:
            lines.append(f"Transcript excerpt: {transcript[:500]}")
        parts.append("\n".join(lines))
    return "\n\n---\n\n".join(parts) or "No post analyses provided."


_NO_EXEMPLAR = "(none — no exemplar or niche data for this creator yet)"


def cold_identity_prompt(brand: dict, chat_history=None,
                         exemplar_context: str = "") -> tuple[str, str]:
    system = (COLD_IDENTITY_SYSTEM
              .replace("{creator_signals}", _creator_signals(brand))
              .replace("{conversation_history}", _chat_text(chat_history))
              .replace("{exemplar_context}", (exemplar_context or "").strip() or _NO_EXEMPLAR))
    return system, ("Generate the channel identity from the context above. "
                    "Return ONLY the JSON object.")


def established_identity_prompt(brand: dict, posts: list[dict],
                                exemplar_context: str = "") -> tuple[str, str]:
    b = brand if isinstance(brand, dict) else {}
    name = str(b.get("known_for") or b.get("niche") or "Unknown").strip() or "Unknown"
    system = (ESTABLISHED_IDENTITY_SYSTEM
              .replace("{channel_name}", name)
              .replace("{video_analyses}", _posts_text(posts))
              .replace("{exemplar_context}", (exemplar_context or "").strip() or _NO_EXEMPLAR))
    return system, ("Generate the channel identity based on this creator's own posts "
                    "above. Return ONLY the JSON object.")


# --- deterministic fallback (keyless / flag-off / LLM failure) ------------------

def _slider(sliders: dict, key: str, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(sliders.get(key, default))))
    except (TypeError, ValueError):
        return default


def _fallback_identity(brand: dict, posts: list | None = None) -> dict:
    """Deterministic doc — every field derived from the creator's OWN brand fields
    (niche / what_you_do / audience / known_for / catchphrases / voice sliders /
    non_negotiables), so it's specific by construction, never horoscope filler.
    data_confidence is 'low' always: no LLM analysis happened."""
    b = brand if isinstance(brand, dict) else {}
    posts = [p for p in (posts or []) if isinstance(p, dict)]
    niche = str(b.get("niche") or "").strip()
    what = str(b.get("what_you_do") or "").strip()
    audience = str(b.get("audience") or "").strip()
    known = str(b.get("known_for") or "").strip()
    platform = str(b.get("primary_platform") or "").strip() or "short-form"
    phrases = [str(p).strip() for p in (b.get("catchphrases") or []) if str(p).strip()]
    constraints = [str(x).strip() for x in (b.get("non_negotiables") or []) if str(x).strip()]
    sliders = b.get("voice") if isinstance(b.get("voice"), dict) else {}
    topic = niche or what or "their subject"

    funny = _slider(sliders, "funnyToSerious")
    raw = _slider(sliders, "polishedToRaw")
    teach = _slider(sliders, "teacherToPeer")
    humor = ("jokes first, the lesson sneaks in" if funny <= 0.35 else
             "serious and direct — the point is the point" if funny >= 0.65 else
             "straight talk with room for a joke")
    finish = ("raw single-take energy, phone in hand" if raw >= 0.65 else
              "clean, deliberate delivery" if raw <= 0.35 else
              "casual but controlled delivery")
    register = ("explains like a teacher who hates wasted minutes" if teach <= 0.35 else
                "talks like a peer trading notes" if teach >= 0.65 else
                "part explainer, part peer")

    voice = f"Talking-head delivery on {topic}: {humor}; {finish}; {register}."
    if known:
        voice += f" Known for {known}, and it shows in how they frame things."
    if phrases:
        voice += (" Signature phrases, verbatim: "
                  + "; ".join(f'"{p}"' for p in phrases[:3]) + ".")
    voice += " Inferred from the creator's own onboarding words, not analyzed footage."

    # Established fallback: the creator's REAL titles are the honest voice anchors.
    titles = [str(p.get("title") or p.get("caption") or "").strip() for p in posts]
    titles = [t for t in titles if t][:3]
    anchors = titles or phrases[:3] or [
        f"I do {what or topic} every day — here's the part nobody shows you.",
        f"{(known or topic)}: 30 seconds, no fluff.",
    ]

    if what and audience:
        primary_function = f"Help {audience} with {what}"
    elif what:
        primary_function = f"Show {what}, straight to camera"
    else:
        primary_function = (f"Talking-head takes on {topic} for "
                            f"{audience or 'people who want the real version'}")

    content_type = f"Talking-head {platform} clips on {topic}, filmed straight to camera"
    if raw >= 0.5:
        content_type += ", phone-level and unpolished on purpose"
    if known:
        content_type += f", anchored in {known}"

    ctx = []
    if what:
        ctx.append(f"What they do: {what}")
    if known:
        ctx.append(f"known for {known}")
    if audience:
        ctx.append(f"audience: {audience}")
    if b.get("why_now"):
        ctx.append(f"why now: {b['why_now']}")
    if posts:
        ctx.append(f"{len(posts)} published posts on record")
    creator_context = "; ".join(ctx) or (
        f"A {topic} creator at the start of the journey — "
        "details pending their first real posts.")

    return {
        "niche_role": (f"The {topic} voice known for {known}" if known
                       else f"The {topic} creator who talks straight to camera"),
        "primary_function": primary_function,
        "content_type": content_type,
        "voice_and_tone": voice,
        "voice_anchors": anchors,
        "macro_style": {
            "verbal_primacy": "high",          # talking-head-first product
            "visual_primacy": "low",
            "content_originality": "mid",
            "production_level": ("high" if raw <= 0.35 else
                                 "low" if raw >= 0.65 else "mid"),
            "methodical_planning": "mid",
            "factuality_level": "high" if teach <= 0.35 else "mid",
        },
        "creator_context": creator_context,
        "durable_constraints": constraints,
        "data_confidence": "low",
        "built_from": "established" if posts else "cold",
    }


# --- normalization (honesty clamp lives here) -----------------------------------

def _normalize(data, built_from: str, allow_confident: bool) -> dict | None:
    """Validate/coerce an LLM doc; None ⇒ too thin, caller uses the deterministic
    fallback. `allow_confident=False` (quiz/chat-only build) clamps data_confidence
    to 'low' regardless of what the model claimed — nothing built without posts or
    exemplar data may present itself as data-backed."""
    if not isinstance(data, dict):
        return None
    core = {k: str(data.get(k) or "").strip()
            for k in ("niche_role", "primary_function", "content_type",
                      "voice_and_tone", "creator_context")}
    if not (core["primary_function"] and core["content_type"] and core["voice_and_tone"]):
        return None
    raw_macro = data.get("macro_style") if isinstance(data.get("macro_style"), dict) else {}
    macro = {}
    for d in _MACRO_DIALS:
        lvl = str(raw_macro.get(d) or "").strip().lower()
        macro[d] = lvl if lvl in _LEVELS else "mid"
    anchors = [str(a).strip() for a in (data.get("voice_anchors") or [])
               if str(a).strip()][:4]
    cons = [str(c).strip() for c in (data.get("durable_constraints") or [])
            if str(c).strip()][:8]
    conf = str(data.get("data_confidence") or "").strip().lower()
    if conf not in _CONFIDENCE:
        conf = "low"
    if not allow_confident:
        conf = "low"
    return {**core, "voice_anchors": anchors, "macro_style": macro,
            "durable_constraints": cons, "data_confidence": conf,
            "built_from": built_from}


# --- public API -----------------------------------------------------------------

async def build_identity(store, creator_id: str, brand: dict,
                         chat_history: list[dict] | None = None,
                         posts: list[dict] | None = None,
                         exemplar_context: str = "") -> dict:
    """Build the identity doc. posts present ⇒ established recipe (Layer 1/Layer 2
    abstraction), else cold recipe (Path B + synthesis ladder). Flag off / keyless /
    any failure ⇒ deterministic brand-derived doc. Never raises."""
    posts = [p for p in (posts or []) if isinstance(p, dict)]
    if not palo_flags.enabled(palo_flags.CHANNEL_IDENTITY):
        return _fallback_identity(brand, posts)
    try:
        if posts:
            base_sys, user = established_identity_prompt(brand, posts, exemplar_context)
            key, built_from = "palo.identity.established", "established"
        else:
            base_sys, user = cold_identity_prompt(brand, chat_history, exemplar_context)
            key, built_from = "palo.identity.cold", "cold"
        system = await get_prompt(key, base_sys, store=store)
        # Palo runs identity at temperature 0.3 (identity_generation.py IDENTITY_MODEL).
        data = await anthropic_cached_json(system, user, _IDENTITY_SCHEMA, SONNET,
                                           max_tokens=1800, temperature=0.3)
        doc = _normalize(data, built_from,
                         allow_confident=bool(posts) or bool((exemplar_context or "").strip()))
        if doc is None:
            return _fallback_identity(brand, posts)     # keyless / thin output
        await ai_usage.record(store, creator_id, "identity.build", SONNET, 4500, 800)
        return doc
    except Exception as e:
        logging.warning("[channel_identity] build failed: %s", e)
        return _fallback_identity(brand, posts)


_CONF_PHRASE = {
    "low": "low — built from the creator's own words, no analyzed posts",
    "medium": "medium — format patterns only; topic and voice come from the creator's own words",
    "high": "high — grounded in the creator's analyzed posts",
}


def identity_block(identity: dict | None) -> str:
    """Pure renderer for prompt injection. '' when None/empty — callers can always
    concatenate the result."""
    if not isinstance(identity, dict) or not identity:
        return ""
    conf = str(identity.get("data_confidence") or "low").strip().lower()
    lines = [f"CHANNEL IDENTITY (confidence: {_CONF_PHRASE.get(conf, _CONF_PHRASE['low'])}):"]
    for label, field_key in (("Niche role", "niche_role"),
                             ("Primary function", "primary_function"),
                             ("Content type", "content_type"),
                             ("Creator context", "creator_context"),
                             ("Voice and tone", "voice_and_tone")):
        val = str(identity.get(field_key) or "").strip()
        if val:
            lines.append(f"{label}: {val}")
    anchors = [str(a).strip() for a in (identity.get("voice_anchors") or [])
               if str(a).strip()]
    if anchors:
        lines.append("Voice anchors (match this register, don't reuse verbatim): "
                     + " | ".join(f'"{a}"' for a in anchors[:4]))
    macro = identity.get("macro_style")
    if isinstance(macro, dict):
        dials = ", ".join(f"{d}={macro[d]}" for d in _MACRO_DIALS if macro.get(d))
        if dials:
            lines.append(f"Style dials: {dials}")
    cons = [str(c).strip() for c in (identity.get("durable_constraints") or [])
            if str(c).strip()]
    if cons:
        lines.append("Durable constraints (hard rules): " + "; ".join(cons))
    if len(lines) == 1:
        return ""                                   # nothing substantive to inject
    if conf != "high":
        lines.append("Treat this as the creator's self-description, sharpened — "
                     "never cite it as observed performance.")
    return "\n".join(lines)


# --- persistence (channel_identity JSONB column on creators) --------------------
# Module-local helpers modeled on palo_persistence's _request usage so this module
# ships without touching PaloStore; the column may not exist yet — every failure
# (400 from PostgREST, transport error) degrades to None/False.

async def load_identity(store, creator_id: str) -> dict | None:
    if store is None or not palo_flags.real_creator(creator_id):
        return None
    try:
        r = await store._request(
            "GET", "/creators",
            params={"creator_id": f"eq.{creator_id}", "select": "channel_identity"})
        if not (r and r.status_code == 200):
            return None
        rows = r.json()
        doc = rows[0].get("channel_identity") if rows and isinstance(rows[0], dict) else None
        return doc if isinstance(doc, dict) and doc else None
    except Exception as e:
        logging.warning("[channel_identity] load failed: %s", e)
        return None


async def save_identity(store, creator_id: str, identity: dict) -> bool:
    if store is None or not palo_flags.real_creator(creator_id) \
            or not isinstance(identity, dict) or not identity:
        return False
    try:
        r = await store._request(
            "PATCH", "/creators",
            params={"creator_id": f"eq.{creator_id}"},
            json={"channel_identity": identity},
            headers={"Prefer": "return=minimal"})
        return bool(r and r.status_code < 300)
    except Exception as e:
        logging.warning("[channel_identity] save failed: %s", e)
        return False


async def ensure_identity(store, creator_id: str, brand: dict, **kw) -> dict:
    """Load-or-build-and-save convenience. Flag off ⇒ deterministic doc, DB never
    touched. Persist only for real creators (never 'default'/demo). Never raises."""
    if not palo_flags.enabled(palo_flags.CHANNEL_IDENTITY):
        return _fallback_identity(brand, kw.get("posts") or [])
    try:
        existing = await load_identity(store, creator_id)
        if existing:
            return existing
        doc = await build_identity(store, creator_id, brand, **kw)
        if store is not None and palo_flags.real_creator(creator_id):
            await save_identity(store, creator_id, doc)
        return doc
    except Exception as e:
        logging.warning("[channel_identity] ensure failed: %s", e)
        return _fallback_identity(brand, kw.get("posts") or [])
