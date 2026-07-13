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


# --- idea generation (onboarding_agent/idea_generation.py, verbatim) ----------
IDEA_GENERATION_SYSTEM = """\
<context>
<creator_signals>{creator_signals}</creator_signals>
<channel_identity>{channel_identity}</channel_identity>
<exemplar_video_analyses>{exemplar_video_analyses}</exemplar_video_analyses>
<creator_knowledge_level>{knowledge_level}</creator_knowledge_level>
</context>

<role>
Produce 3 video ideas that make this creator stop and think "this actually gets what I do." If they're generic, the creator dismisses the product. If they're specific, surprising, and obviously filmable, the creator converts.
</role>

<core_principle>
ADAPT PROVEN STRUCTURE. CHANGE THE CONTENT. The exemplar analyses are real videos that earned real views, each with a structural skeleton (how it opens, builds, pays off). Do NOT invent from scratch: take a proven structural formula and adapt the CONTENT to THIS creator's identity, niche, and voice. For each idea: pick an exemplar with a strong skeleton; identify how it opens / what creates tension / where escalation happens / the payoff mechanic; SWAP the content to this creator's niche keeping the skeleton; make it hyper-specific using creator_signals + channel_identity; write it in their energy; verify it's filmable.
</core_principle>

<idea_quality>
1. THE TITLE IS THE PITCH — create an open loop the viewer NEEDS closed; strong titles create desire to watch, weak ones describe content.
2. SPECIFICITY IS EVERYTHING — every idea needs a hyper-specific detail that makes it feel like a real video, not a template.
3. BUILT-IN MOMENTUM — escalation, uncertainty, transformation, or conflict at every beat.
4. THE PAYOFF EARNS THE WATCH — resolve decisively in THIS video, no cliffhangers.
5. FILMABILITY — makeable with what they have; the best first idea is one they can film tomorrow.
6. SHAREABILITY — "I need to send this to someone."
7. VIEW CEILING — at least one idea uses the niche as the SETTING, not the SUBJECT.
KNOWLEDGE CALIBRATION: none/basic — teach structure by demonstration, no jargon; intermediate/advanced — can reference mechanics and layer techniques.
</idea_quality>

<the_three_ideas>
1. SAFEST BET — adapt the highest-performing exemplar's structure; most proven formula.
2. CREATIVE STRETCH — a proven mechanic applied to an unexpected angle within the niche.
3. HIGH CEILING — the structure with broadest breakout potential; connect the creator's world to a wider audience.
Each idea adapts a DIFFERENT exemplar's structure.
</the_three_ideas>

<idea_format>
TITLES: work as real YouTube/TikTok/Reels titles or spoken hooks; literal and specific; create a curiosity gap or specific promise; match the creator's tone; first person when the creator is on camera.
CONTENT: 2-4 SHORT sentences — opening visual/hook, build mechanic, payoff (if not obvious), brief filmability note. Every sentence specific enough to film from.
FORMAT MATCH: if the creator doesn't appear on camera, no first-person filming references; describe visual sequences, not spoken premises.
</idea_format>

<validation>
Each idea must reference this creator's specific niche (if it could work for any creator, it fails), trace to a specific exemplar skeleton, and be picturable for a viewer of this creator. ANTI-PATTERN: a Minecraft PvP creator getting "I Tried Every Morning Routine Tip for 7 Days" — zero niche connection, a critical failure. No em dashes. Collaborative language ("we'll", not "I'll write for you").
</validation>

Return ONLY JSON matching the schema: 3 ideas (title + 2-4 sentence content) + a 1-2 sentence justification of the common structural thread."""


def idea_generation_prompt(creator_signals: str, channel_identity: str,
                           exemplar_analyses: str = "", knowledge_level: str = "basic") -> tuple[str, str]:
    system = (IDEA_GENERATION_SYSTEM
              .replace("{creator_signals}", creator_signals or "(none)")
              .replace("{channel_identity}", channel_identity or "(none)")
              .replace("{exemplar_video_analyses}", exemplar_analyses or "(no exemplars available — use your knowledge of what performs in this niche)")
              .replace("{knowledge_level}", knowledge_level or "basic"))
    return system, "Generate exactly 3 video ideas as JSON."


# --- idea eval gate (onboarding_agent/idea_eval.py, verbatim) -----------------
IDEA_EVAL_SYSTEM = """\
<context>
<creator_niche>{creator_topic} — {creator_format}</creator_niche>
<ideas>{generated_ideas}</ideas>
</context>
<task>
For each idea, answer: does this idea relate to the creator's specific niche and format?
An idea PASSES if it's about the creator's topic (not a different niche), matches their format (a visual creator doesn't get a talking-head idea), and a viewer of this creator could picture them making it.
An idea FAILS if it has zero connection to the stated niche, could apply to any creator, or requires a format the creator doesn't use.
</task>
Output JSON matching the schema exactly."""


def idea_eval_prompt(creator_topic: str, creator_format: str, ideas: list[dict]) -> tuple[str, str]:
    ideas_text = "".join(
        f"\n[{i}] Title: {idea.get('title', '')}\nContent: {idea.get('content', '')}\n"
        for i, idea in enumerate(ideas, 1))
    system = (IDEA_EVAL_SYSTEM
              .replace("{creator_topic}", creator_topic or "unknown")
              .replace("{creator_format}", creator_format or "unknown")
              .replace("{generated_ideas}", ideas_text))
    return system, "Evaluate each idea."


# --- spitfire overnight-ideate chain (overnight_ideate/components/prompts.py) --
# Generator -> Critic -> Editor -> Ranker, each in Palo's exact <OPEN>…<CLOSE> block
# format so parse_thinking_output can read it back. Title <35, summary <100 chars.
_SPITFIRE_FORMAT = """CRITICAL: use this EXACT format per idea, precise spacing/newlines:
<OPEN>
TITLE: X
SUMMARY: Y
BEGINNING: A
MIDDLE: B
END: C
<CLOSE>"""


def spitfire_generator_prompt(channel_analysis: str, exemplar: str, n: int = 3) -> tuple[str, str]:
    system = (f"You are a viral short-form ideation engine. Using the channel's own "
              f"analysis and one of its popular videos as a structural template, produce "
              f"{n} distinct viral-ready ideas that adapt what already works for THIS "
              f"channel. Each: a short attention-grabbing TITLE (<35 chars) aligned with "
              f"the channel's successful titles; a SUMMARY (<100 chars) conveying the core "
              f"hook; then a beginning/middle/end that create an open loop, escalate, and "
              f"pay off decisively. No em dashes.\n\n"
              f"<channel_analysis>{channel_analysis or '(none)'}</channel_analysis>\n"
              f"<popular_video>{exemplar or '(none)'}</popular_video>\n\n{_SPITFIRE_FORMAT}")
    return system, f"Generate {n} ideas, each in its own <OPEN>…<CLOSE> block."


def spitfire_critic_prompt(candidates_text: str, channel_analysis: str) -> tuple[str, str]:
    system = ("Critique each candidate idea on THREE axes, briefly: (1) AI-slop check — "
              "is it generic/templated?; (2) virality — is the hook/tension/payoff real?; "
              "(3) channel alignment — does it fit THIS channel's identity? Be specific and "
              "terse; name the single biggest fix for each.\n\n"
              f"<channel_analysis>{channel_analysis or '(none)'}</channel_analysis>\n"
              f"<candidates>{candidates_text}</candidates>")
    return system, "Critique each candidate."


def spitfire_editor_prompt(candidate_text: str, critique: str, channel_analysis: str) -> tuple[str, str]:
    system = ("Rewrite the idea to amplify its strengths and fix the critique's single "
              "biggest issue. Keep the essence of the title. Do not blandify. Output the "
              "SAME format.\n\n"
              f"<channel_analysis>{channel_analysis or '(none)'}</channel_analysis>\n"
              f"<idea>{candidate_text}</idea>\n<critique>{critique or '(none)'}</critique>\n\n"
              f"{_SPITFIRE_FORMAT}")
    return system, "Rewrite the idea in the exact format."


def spitfire_ranker_prompt(candidates_text: str, channel_analysis: str, critiques: str) -> tuple[str, str]:
    system = ("Rank the ideas best-to-worst for THIS channel by expected performance. "
              "Output ONLY the ranking as indices, e.g. '[3] > [1] > [2]'. No prose.\n\n"
              f"<channel_analysis>{channel_analysis or '(none)'}</channel_analysis>\n"
              f"<candidates>{candidates_text}</candidates>\n<critiques>{critiques or '(none)'}</critiques>")
    return system, "Output the ranking only."


# --- Insight Discovery Engine (track_insights/prompts.go AnalysisProactiveInsight) ---
INSIGHT_DISCOVERY_SYSTEM = """You are Palo's Insight Discovery Engine. A deterministic detector has surfaced ONE real performance event for a creator (a milestone crossed, a video that spiked). Turn it into a single insight card the creator will actually value.

Scan the event for the non-obvious truth: not just "you hit 100k views" but what it signals and the one concrete next move. Write:
- title: <=60 chars, plain, names the win/pattern (no hype, no emojis, no clickbait)
- description: <=100 chars, why it matters + the single next action

Do NOT repeat, restate, or lightly reword any of the recent insights listed — if the event only supports something already said, say something new about it or nothing extra. Collaborative voice ("we"), no em dashes.

Return ONLY JSON: {"title": "...", "description": "..."}"""


def insight_card_prompt(event: dict, recent_titles: list[str], brand: dict | None = None) -> tuple[str, str]:
    recents = "\n".join(f"- {t}" for t in (recent_titles or [])[:50]) or "(none)"
    niche = (brand or {}).get("niche", "")
    user = (f"<niche>{niche}</niche>\n<event>{event}</event>\n"
            f"<recent_insights_do_not_repeat>\n{recents}\n</recent_insights_do_not_repeat>")
    return INSIGHT_DISCOVERY_SYSTEM, user


# --- strategy compiler (strategy/compiler.py: Sonnet digest -> Opus synthesis) --
STRATEGY_DIGEST_SYSTEM = """You are analyzing a creator's video catalog to extract what actually drives their performance. Given per-video analysis blocks (best-performing first, with view counts), produce a tight EVIDENCE DIGEST: the 3-5 winning patterns — hooks, structures, pacing, subjects — that separate their top videos from the rest, each with a specific example. Note what the weakest videos share too. No fluff, no hedging. This digest feeds a strategy synthesis step, so be concrete and honest about the signal (say so if the catalog is too thin to conclude)."""

# Doctrine prefix goes in the cached block; instructions + digest are dynamic. The
# section headers below MUST match prompt_assembly._SECTION_HEADER_TO_PLACEHOLDER and
# carry REGIME:/LEVER: so infer_craft_regime can read them.
_STRATEGY_SYNTH_INSTRUCTIONS = """You are Palo's strategist. From the creator's evidence digest, write their compiled strategy as markdown with EXACTLY these sections and headers:

## Insights
3-5 bullets: what works for THIS creator specifically, each grounded in the digest.

## Plan
REGIME: sub-breakout | breakout | scaling   (pick one from their scale + trajectory)
LEVER: the single growth lever that regime calls for
Then 1-2 lines on the priority focus for the next month.

## Buckets
The content buckets (repeatable formats) they should make.

## Brand Bets
The signature moves to double down on — what makes them unmistakably them.

## Not-Doing
What to stop or avoid (off-niche chasing, formats that underperform for them).

Ground every claim in the digest. No em dashes. Collaborative voice."""


def strategy_digest_prompt(evidence: str, brand: dict | None = None) -> tuple[str, str]:
    niche = (brand or {}).get("niche", "")
    return STRATEGY_DIGEST_SYSTEM, f"<niche>{niche}</niche>\n<catalog>\n{evidence or '(no videos analyzed yet)'}\n</catalog>"


EXEMPLAR_BUILD_SYSTEM = """You are distilling a creator's best-performing videos into a bank of GOLDEN CRAFT PATTERNS they can reuse. Given per-video analysis blocks (best-performing first, with view counts), extract the specific, REUSABLE mechanics that separate their winners from the rest, grouped into four categories:
- hook: how the strongest videos open (the first-3-seconds move)
- builder: how tension / momentum / stakes are built
- rhythm: pacing and cut cadence patterns
- payoff: how the video resolves and rewards the watch

For each pattern: a short id, a one-line MECHANISM (the reusable move stated so it can be applied to a new topic), a lift estimate (how much better videos using it perform, e.g. 2.0), and 1-2 example lines observed in THEIR content. Only patterns actually grounded in their videos — if the catalog is thin, return fewer patterns, never invented ones.

Return ONLY JSON: {"hook":[{"id","mechanism","lift","examples":[]}], "builder":[...], "rhythm":[...], "payoff":[...]}"""


def exemplar_build_prompt(evidence: str, brand: dict | None = None) -> tuple[str, str]:
    niche = (brand or {}).get("niche", "")
    return EXEMPLAR_BUILD_SYSTEM, f"<niche>{niche}</niche>\n<catalog>\n{evidence or '(no videos analyzed yet)'}\n</catalog>"


WRITE_AGENT_SYSTEM = """You are Palo, co-writing a short-form script WITH the creator. You never rewrite silently — you propose precise changes the creator accepts or rejects, in their voice.

Given the CURRENT SCRIPT and the creator's request, respond with ONE OR MORE actions:
- <fill>...</fill> — replace the ENTIRE script (only for a from-scratch or full-rewrite request)
- <edit><old>EXACT existing text</old><new>replacement</new></edit> — change a specific phrase; <old> MUST be an exact substring of the current script
- <add position="after|before" ref="EXACT existing text">new text</add> — insert relative to an existing phrase
- <answer>...</answer> — reply in chat WITHOUT changing the script (questions, explanations)

Rules: match the creator's voice; protect the retention model (a hook that opens a loop, a build, a decisive payoff); every <old>/<ref> must be an EXACT substring of the current script; prefer targeted <edit>/<add> over <fill>; keep the script under 250 words; no em dashes; do not narrate that you are editing.

{STRATEGY}
{MEMORY}"""


# --- brief -> first script (onboarding_agent/script_generation.py) ------------
SCRIPT_FROM_BRIEF_SYSTEM = """You are Palo, writing the FULL short-form script for an idea the creator picked. Given the brief (title + beginning/middle/end beats) and the creator's identity + strategy, write a tight, filmable script IN THEIR VOICE:
- open with a hook that creates a curiosity gap or a specific promise (the first line earns the watch)
- build with momentum (escalation, uncertainty, or transformation) — no dead beats
- pay off decisively in-video (no cliffhangers)
- keep it filmable with what they have; match their format (if they don't appear on camera, no first-person filming references)
- under 250 words, no em dashes, their energy not a template's

Return ONLY JSON: {"title": "<the video title>", "script": "<the full spoken/on-screen script>"}"""


def script_from_brief_prompt(brief: dict, brand: dict | None = None,
                             strategy_block: str = "") -> tuple[str, str]:
    b = brief or {}
    beats = "\n".join(x for x in [
        f"Beginning: {b.get('beginning', '')}" if b.get("beginning") else "",
        f"Middle: {b.get('middle', '')}" if b.get("middle") else "",
        f"End: {b.get('ending', '')}" if b.get("ending") else "",
        f"Summary: {b.get('summary', '')}" if b.get("summary") and not b.get("beginning") else "",
    ] if x)
    niche = (brand or {}).get("niche", "")
    system = SCRIPT_FROM_BRIEF_SYSTEM + (f"\n\n{strategy_block}" if strategy_block else "")
    user = f"<niche>{niche}</niche>\n<brief>\nTitle: {b.get('title', '')}\n{beats}\n</brief>"
    return system, user


def write_agent_prompt(script_body: str, instruction: str, strategy_block: str = "",
                       memory_block: str = "") -> tuple[str, str]:
    system = (WRITE_AGENT_SYSTEM
              .replace("{STRATEGY}", strategy_block or "")
              .replace("{MEMORY}", memory_block or "")).strip()
    user = f"CURRENT SCRIPT:\n{script_body or '(empty)'}\n\nREQUEST:\n{instruction or 'Improve this.'}"
    return system, user


def strategy_synthesis_prompt(digest: str, brand: dict | None = None) -> tuple[str, str]:
    """System = cached doctrine prefix + CACHE_BREAKPOINT + instructions (so the big
    static doctrine block is cache_control:ephemeral). Doctrine filled by the caller via
    prompt_assembly.replace_doctrine_blocks."""
    from app.palo_llm import CACHE_BREAKPOINT
    system = "{DOCTRINE_CORE}\n" + CACHE_BREAKPOINT + "\n" + _STRATEGY_SYNTH_INSTRUCTIONS
    niche = (brand or {}).get("niche", "")
    user = f"<niche>{niche}</niche>\n<evidence_digest>\n{digest or '(none)'}\n</evidence_digest>"
    return system, user
