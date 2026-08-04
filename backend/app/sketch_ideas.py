"""R2 (SKETCH_IDEAS) — Palo's sketch→idea bake-off funnel, ported for Yunicorn.

Two passes replace the spitfire generate→critic→edit→rank chain (the chain Palo itself
retired: "breadth and craft were competing for the same tokens" in one forced emit, and
the reasoning that picked the winner was thrown away the moment the emit was written):

  1. SKETCH (a5a-sketch-ideator): a tool-less, high-temperature SONNET pass floods the
     zone with ~8 concept sketches that must be genuinely different AT THE ENGINE LEVEL
     (a different format/builder or subject family), one mandatory longshot, RUBRIC OFF
     — judging is not this pass. Yunicorn adaptation: no web tools, so the prompt's
     query-pitching sections are stripped and the prompt's own zero-query degrade
     applies (external anchors stay candidates the brief must flag for verification).
     Plain-text sketchbook, parsed tolerantly (unclosed tags salvaged, both historical
     contracts accepted); any failure yields an empty sketchbook, never a block.

  2. IDEA (a5b-idea-generator, operative sections): given the sketchbook + channel
     context (mix, identity, exemplars, recent titles), run the bake-off — mix-check,
     pick 3 ENGINE-level rivals, judge them (payoff test + engine-level collision:
     "a different store is the same video"), flesh out ONLY the winners. Emits
     {title, concept, pitch, brief}: pitch and brief are two fields because they have
     two readers (the card the creator sees vs. the internal handoff the next pass
     consumes). Sketchbook absent/thin → the idea pass drafts its own 3 rivals
     (Palo's own degrade — also the cold-start lane for a zero-history creator).

CODE ARMOR (ported from Palo offline/generators.py; each rule learned from a live
failure):
  - vocab firewall: multipliers always count as leaks; stat words (baseline/median/
    lift) only with a digit within 30 chars — bare-word matching false-positived on
    "lift the pallet". One bounded REJECTED retry, then scrub.
  - tool-call scaffolding in a field ⇒ the field is DISCARDED, never repaired
    (rescuing prose out of leaked markup yields debris that reads worse than nothing).
  - NO FALLBACK COPY: any failure returns [] — this path never mints template copy;
    the CALLER falls back to the old chain. Palo: "a missing pick is a quiet miss;
    internal prose on the card is a trust break."
  - dedup-before-create: significant-token containment >= 0.6 against recent titles.

Keyless-green: no key ⇒ both LLM calls return None ⇒ []. Flag SKETCH_IDEAS gates the
entry point; store=None is fine everywhere (prompt overrides and usage rows just skip).
"""
from __future__ import annotations

import json
import logging
import re

from app import ai_usage, palo_flags
from app.palo_llm import CACHE_BREAKPOINT, anthropic_cached, anthropic_cached_json
from app.prompt_store import get_prompt
from app.recall_ledger import new_ulid
from prompts import SONNET

# Breadth target. Palo runs 10 on a 20k-thinking budget; without extended thinking a
# smaller sketchbook keeps every sketch funded instead of padded.
SKETCH_COUNT = 8
# The rendered sketchbook block is capped so a runaway sketch pass can never crowd the
# idea pass's context (Palo's _BLOCK_CAP).
_BLOCK_CAP = 6000

_IDEASET_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["ideas"],
    "properties": {
        "ideas": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "concept", "pitch", "brief"],
            "properties": {
                "title": {"type": "string"}, "concept": {"type": "string"},
                "pitch": {"type": "string"}, "brief": {"type": "string"}}}}},
}


# --- pass 1: the sketch prompt (a5a, web-query sections stripped) ---------------

_SKETCH_SYSTEM = """<!-- a5a-sketch-ideator v3.0 / yunicorn port (no web tools) -->

<role>
You are the sketch pass, the wide end of the ideation funnel. Before any idea is
written, you flood the zone: a sketchbook of genuinely different candidate directions,
drafted fast, the way a working creator lists fifteen ideas to find the one worth
making.

You do not pick the winner. You do not build anything out. Your sketchbook IS the
commission the idea pass receives; it judges your candidates, picks a direction, and
gives it the complete treatment. A thin or samey sketchbook caps the whole run's
quality, because nothing downstream can pick an idea that was never sketched.

You have no tools and no web access; everything comes from the context printed below.
Reason as long as you need privately. Your visible output is ONLY the tagged structure
in the output contract — code reads it with exact tags and throws everything else away.

TALKING-HEAD ONLY (hard product rule): every sketch must be fully TELLABLE by the
creator talking to camera in one sitting — a story, a take, a myth-bust, a reaction,
a confession. The AI editor adds all other visuals automatically. A sketch that only
works if the viewer WATCHES the creator do something (a stunt, build, recipe, location,
demonstration, screen recording) is dead on arrival — sketch the STORY of that thing
told to camera instead.
</role>

<rules>
1. VOLUME WITH SPREAD. Aim for {n} sketches. No two may ride the same format on the
   same subject family; beyond that, variants are legal. Spread across what the context
   offers: the channel's own catalog decomposed, a proven format crossed with a subject
   that is hot right now, a format transfer across the channel's lanes, an uncashed
   brand bet, a concept the creator committed to. And include one longshot — the weird
   one a safe pass would cut. The idea pass can kill it; you cannot pre-kill it. If the
   context genuinely cannot fund {n} genuinely different sketches, return fewer;
   under-delivering is the rare exception, never a comfort.
2. A SKETCH IS A TITLE AND ITS CHARGE. First line: the working title, in the channel's
   title grammar. Then one or two freeform sentences: the thing people already feel or
   the question that needs answering, and what the viewer ends up waiting for. The
   payoff test runs even at this grain — if nothing bad, escalating, or surprising can
   be pictured, the sketch is dead at birth. "Everyone can name Genghis Khan, almost
   nobody knows the empire kept growing after he died" carries a sketch; "could be
   interesting" is a shrug.
3. NOTHING THAT COLLIDES, NOTHING OFF-VOICE. Sweep every sketch against the recent
   titles printed below; collision is judged on the ENGINE, not the title — the same
   format on the same subject family with a different venue IS the existing video. A
   collision in the sketchbook wastes a slot that could have held a real choice.
4. RUBRIC OFF. Do not rank, do not argue cases, do not kill your darlings, do not flag
   a favorite. Momentum is the method and judging is not your pass.
5. GROUNDED AT SKETCH GRAIN. You assert nothing external as fact. Numbers and
   specifics printed in the context are the only ones you may state flat; an anchor a
   sketch rides ("the war that lasted 335 years") is a candidate the idea pass must
   flag for verification, never a fact. Never state what the creator said or committed
   to unless the context prints it.
6. CLEAN REGISTER, AND THE CHANNEL'S PERSON. The idea pass writes creator-facing copy
   FROM your lines, so: plain language, no pattern ids, no "baseline"/"lift"/
   multiplier-speak, no em dashes. On a solo creator's channel the note speaks in
   "you" or neutral mission language, never "he" or "she" — a third-person sketchbook
   bleeds third person into everything downstream.
</rules>

<output_contract>
Emit EXACTLY this structure and nothing else. Code extracts these tags with regex: the
first line inside a <sketch> is the title, the rest is the note; an empty sketch is
dropped; text outside the tags is ignored. No JSON — a run that emits JSON silently
produces zero sketches.

<sketches>
<sketch n="1">
the working title, alone on the first line
One or two freeform sentences: the charge, the shape, what the viewer is waiting for.
</sketch>
<sketch n="2">
...
</sketch>
</sketches>
</output_contract>

<example note="map-history channel, trimmed">
<sketches>
<sketch n="1">
the history of Mongolia
Everyone can name Genghis Khan, almost nobody knows the empire kept growing after he
died. Modern Mongolia is a sliver of it, and the video rides that gap to the true peak.
</sketch>
<sketch n="2">
how big was Rome actually
The empire drawn over today's borders, modern country after modern country falling
inside it. The viewer waits for their own country to get swallowed.
</sketch>
<sketch n="3">
the war that lasted 335 years
A war with zero deaths that everyone forgot to end for three centuries. The payoff is
the peace treaty finally signed.
</sketch>
<sketch n="4">
the empire that never lost a war
The longshot: hold one unbeaten claim through war after war and let the viewer wait
for the loss that never comes, then land what finally ended it, and it was not a battle.
</sketch>
</sketches>
</example>
""" + CACHE_BREAKPOINT


# --- pass 2: the bake-off prompt (a5b operative sections) -----------------------

_IDEA_SYSTEM = """<!-- a5b-idea-generator v3.4 / yunicorn port (operative sections) -->

<role>
You are the ideation pass. A sketch pass may have run before you and laid out candidate
directions; the judging and the building are yours. Each concept you return must be one
a paid strategist would genuinely hand over: specific enough to shoot, honest enough to
trust, and unmistakably THIS channel's.

You have no tools and no second turn. Everything you assert must come from the context;
everything you can't verify must be flagged in the brief, never stated as fact.

The bar is the payoff test: if a viewer imagining this video can't picture something
bad, escalating, or surprising happening, the idea is dead, however clean the premise.

TALKING-HEAD ONLY (hard product rule): every winner must be fully TELLABLE by the
creator talking to camera in one sitting; the AI editor adds all other visuals
automatically. An idea that requires the creator to film anything else (a stunt,
build, demo, location, screen recording, prop) fails the bake-off no matter how
strong its engine — reframe it as the story of that thing, told to camera, or kill it.
</role>

<how_you_ideate>
The concept is never your first thought — it is the winner of a bake-off you actually
run, in this order:

1. RUN THE MIX-CHECK. Read the channel's mix (what it leans into and how the variety
   rotates) against the recent titles: if the last few videos were all one type, lean
   to what is under-served. The material can overrule the rotation with reason — say
   so in the brief when you do.
2. PICK THREE RIVALS FROM THE SKETCHBOOK — genuinely different from each other at the
   ENGINE level: a different format/builder or a different subject family, never three
   venues of one concept. Each already clear of every recent title. Give the longshot a
   real read before you pass on it — the sketch pass included it precisely so it could
   not be pre-killed. You may replace ONE pick with a rival of your own drafting when
   the sketchbook missed a move the context loudly argues for; say so in the brief.
   When the sketchbook is ABSENT or thin, draft the three rivals yourself from the
   context, same constraints. If the material cannot fund three real rivals, the lane
   is over-farmed — say so in the brief and return fewer; never pad with variants.
3. JUDGE THEM. The payoff test on every candidate. The mix: does it serve what is due?
   The collision check RUNS ON THE ENGINE, NOT THE TITLE: a concept that keeps an
   existing video's engine — the same format on the same subject family — and swaps the
   venue IS that video; a different store is the same video. The voice: when a proven
   move conflicts with the channel's identity, the voice wins, every time. Kill what
   fails. When two survive, the one the channel's own material supports harder wins.
4. FLESH OUT ONLY THE WINNERS, ranked best first — and fleshing out IS the ideation,
   not decoration of it. The beats are where the video gets good or stays generic. Hunt
   the subject's CHARGED material: the moment people already feel something about, the
   thing everyone knows meeting the thing nobody does — and make every beat survive
   "who cares?". Chronology and the format's template are ORDERING devices; they
   arrange the beats you chose, they never choose them. The winner's edge lives in the
   PRIMER (stated as this concept's strength, never the rival argued down — the rival
   stays live for a future video); the bake-off receipt (what it beat, why, one clause)
   rides the brief. A concept that cannot say what it beat has not been ideated — it
   has been defaulted.
</how_you_ideate>

<the_fields>
Each idea has exactly four fields, two readers:

- title: the literal working video title, sounding like it belongs in the channel's
  recent-titles list. Their words, their grammar, never a device the channel does not
  use.
- concept: the walkthrough. FIRST a one-or-two-sentence PRIMER: the engine in plain
  words and the gap or charge the whole video rides. THEN arrow-led beats separated by
  blank lines. Every beat: the anchor (the beat named the way this channel names
  beats), then one or two short lines of what the viewer literally sees or hears,
  concrete enough to picture cold, then a terse function note when the beat's job is
  not self-evident. PAINT, DON'T THEORIZE: "scattered colored territories pull into
  one solid color as the tribes merge" is a beat; "the spoken name becomes the reason
  this land explodes outward" is commentary ABOUT a beat — the creator must know what
  is literally happening on screen from every line. A middle beat may leave its content
  open only when its story role is cast precisely enough that the next pass could fill
  the slot from the role alone; the hook and the payoff never take this exception —
  they are the story's two ends and must be real. Address the creator directly
  ("you climb...") or write neutral direction, never third person.
- pitch: 30 words or fewer, creator-facing — what the card shows before the tap, so it
  must land alone and earn the tap. Sentence one is the read: why this idea, now, with
  the creator's own catalog or moment doing the arguing, in plain creator language and
  NO numbers-speak. Sentence two is the gist in one clause. Every name in the pitch
  must already mean something to the reader — the concept carries the real names, the
  pitch carries the charge. Convince, don't cram.
- brief: 60 words or fewer, INTERNAL handoff for the pass that builds the video: what
  must hold (structural constraints), what to verify (every external anchor the concept
  rides becomes a verification instruction here — "verify the real figure before
  building; if it fails, keep the structure and swap the subject"), what stays open,
  and the bake-off receipt in one clause (the rival the winner beat and why). Never a
  re-decision of the concept.
</the_fields>

<rules>
1. GROUNDED OR FLAGGED, NEVER FABRICATED. Every specific you state must be printed in
   the context. Never state what the creator said, promised, or committed to unless the
   context prints it. An external anchor (a named person, event, record) may still
   carry an idea, but it becomes a verify instruction in the brief and the concept
   treats it as a candidate; if the payoff collapses when the unverified detail fails,
   the idea leaned too hard on something you could not check. A blank payoff — "the
   surprising reveal" — is not an idea.
2. VOCABULARY FIREWALL. Title, pitch, and concept reach the creator: no pattern names,
   no "baseline"/"median"/"lift", no multipliers ("2.3x"), no hit rates, no internal
   machinery. Performance lives in plain words ("your strongest format", "climbing
   again this week"), never as a metric. Numbers-speak lives in the internal brief
   only. No em dashes in title, pitch, or concept — commas, periods, line breaks, and
   the beat arrow.
3. WINNERS ONLY. Return the bake-off's winners ranked best first, up to the count the
   request names. Fewer is legal when the material cannot fund real rivals; never pad
   with venue-swap variants of one concept.
</rules>
""" + CACHE_BREAKPOINT


# --- code armor: vocab firewall (generators.py:751-827) -------------------------

# A MULTIPLIER is always stat-speak — "7.39x", "1.65x", "7/7 hit". No creator-facing
# sentence needs one, and every live leak Palo saw was this shape.
_MULTIPLIER_RE = re.compile(
    r"\b\d+(?:\.\d+)?x\b|\b\d+\s*/\s*\d+\s+(?:hit|beat)s?\b", re.IGNORECASE)

# STAT WORDS are ordinary English until a number stands next to them. Matching them
# bare cost Palo real work ("lift the pallet a few feet" rejected, each rejection a
# full retry) — so these only count with a digit in view, or in their snake_case
# internal spelling.
_STAT_WORD_RE = re.compile(
    r"\bbaseline_multiplier\b|\bmedian_views\b|\bhit[ _]rate\b"
    r"|\bbaseline\b|\bmedian\b|\blift\b",
    re.IGNORECASE)
_STAT_DIGIT_WINDOW = 30


def vocab_leaks(text: str) -> list[str]:
    """Internal vocabulary in creator-facing copy. Multipliers always count; stat
    words only when a digit is within _STAT_DIGIT_WINDOW chars or the word is written
    in its internal snake_case form. Pure, deterministic."""
    t = text or ""
    leaks = [m.group(0) for m in _MULTIPLIER_RE.finditer(t)]
    for m in _STAT_WORD_RE.finditer(t):
        word = m.group(0)
        if "_" in word:
            leaks.append(word)
            continue
        window = t[max(0, m.start() - _STAT_DIGIT_WINDOW): m.end() + _STAT_DIGIT_WINDOW]
        if re.search(r"\d", window):
            leaks.append(word)
    return leaks


def scrub_vocab(text: str) -> str:
    """Fallback scrub when the retry still leaks: drop parentheticals that carry a
    NUMBER plus stat-speak (a bracket with "proven" but no digit is legitimate copy —
    Palo lost "(the proven way you always start)" to a looser rule), then blank the
    residual matches. Word-level surgery, never mid-word."""
    out = re.sub(
        r"\s*\((?=[^)]*\d)[^)]*(?:\d+(?:\.\d+)?x|baseline|median|lift|hit rate"
        r"|proven|breakout)[^)]*\)",
        "", text or "")
    for leak in vocab_leaks(out):
        out = out.replace(leak, "")
    return re.sub(r"  +", " ", out).strip()


# --- code armor: scaffold discard (generators.py:830-875) -----------------------

# Tool/markup scaffolding means the model broke out of its own emit and started
# narrating (seen live: a pitch field storing closing tool-markup tags plus "Let me
# redo this properly" plus a parameter tag). A scaffolded field is DISCARDED, never
# repaired — rescuing prose out of leaked markup yields debris that reads worse than
# nothing.
_SCAFFOLD_RE = re.compile(
    r"</?antml|<\s*/?\s*parameter\b|<\s*/?\s*invoke\b|<\s*/?\s*function"
    r"|\bLet me (redo|try) th",
    re.IGNORECASE)

_FILLER = {"placeholder", "todo", "tbd", "n/a", "none", "...", "..", "-"}


def is_scaffold(text: str) -> bool:
    """True when a string carries tool-call scaffolding instead of prose."""
    return bool(_SCAFFOLD_RE.search(text or ""))


def _field(v) -> str:
    """A cleaned field, or "" when it is unusable as written (non-string, scaffolded,
    or a filler literal standing in for real copy). Newlines survive — the concept's
    blank-line beat separation is load-bearing."""
    if not isinstance(v, str) or is_scaffold(v):
        return ""
    out = re.sub(r"[ \t]{2,}", " ", v).strip()
    return "" if out.lower() in _FILLER else out


# --- code armor: dedup-before-create (generators.py:650-707) --------------------

_TITLE_STOP = {
    "the", "a", "an", "at", "in", "on", "of", "to", "for", "and", "or", "my",
    "your", "you", "i", "is", "it", "this", "that", "with", "how", "why", "what",
    "up", "out", "into", "from", "by", "new", "ultimate", "crazy", "insane",
    "wild", "got", "get", "go", "went", "im", "ive", "gonna", "just", "really",
}


def _concept_tokens(t: str | None) -> set:
    norm = re.sub(r"[^a-z0-9 ]+", "", (t or "").lower()).strip()
    return {w for w in norm.split() if w not in _TITLE_STOP and len(w) > 1}


def too_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Significant-token containment >= threshold against the smaller title's token
    set ("same concept + extra descriptors"), gated on >= 2 shared significant tokens
    so a single shared subject word is never a duplicate — a false dedup silently
    drops a legitimate idea, so this errs toward distinct."""
    ta, tb = _concept_tokens(a), _concept_tokens(b)
    overlap = len(ta & tb)
    if overlap < 2:
        return False
    return overlap / min(len(ta), len(tb)) >= threshold


# --- sketchbook parser (sketch.py parse_sketch, salvage behavior ported) --------

def _tagged(block: str, tag: str) -> list[str]:
    """Contents of every <tag>…</tag>, plus a trailing open tag that never closed —
    the failure mode is a truncated stream, and a strict match dropped the block even
    after the sketch itself was salvaged."""
    out = re.findall(rf"<{tag}>(.*?)</{tag}>", block or "", re.DOTALL)
    if out:
        return out
    m = re.search(rf"<{tag}>(?!.*</{tag}>)(.*)\Z", block or "", re.DOTALL)
    return [m.group(1)] if m else []


def parse_sketchbook(text) -> list[dict]:
    """Tagged sketchbook text → [{title, note}]. Parses BOTH contracts: the live form
    (first non-empty line is the title, the rest is the note) and the legacy tagged
    form (<premise>/<hook>/<payoff>/<engine> children) so a prompt rollback never
    breaks the parser a second time. An unclosed trailing <sketch> still counts (the
    stream gets cut mid-block). Tolerant by contract: malformed input yields fewer
    sketches — or [] — never an exception. JSON output parses to nothing by design;
    code reads tags only."""
    try:
        if not isinstance(text, str) or not text:
            return []
        blocks = re.findall(r"<sketch\b[^>]*>(.*?)</sketch>", text, re.DOTALL)
        tail = re.split(r"<sketch\b[^>]*>", text)[-1] if "<sketch" in text else ""
        if tail and "</sketch>" not in tail:
            blocks.append(tail)
        out: list[dict] = []
        for raw in blocks:
            title, note = "", ""
            premise = (_tagged(raw, "premise") or [""])[0].strip()
            if premise:                       # legacy tagged contract
                title = premise
                note = " ".join(x for x in (
                    (_tagged(raw, "hook") or [""])[0].strip(),
                    (_tagged(raw, "payoff") or [""])[0].strip(),
                    (_tagged(raw, "engine") or [""])[0].strip()) if x)
            else:                             # live contract: title line + note
                lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
                if lines:
                    title = lines[0]
                    note = " ".join(lines[1:])
            if title:
                out.append({"title": title, "note": note})
        return out
    except Exception as e:                    # pragma: no cover — belt and braces
        logging.warning("[sketch_ideas] parse_sketchbook failed: %s", e)
        return []


# --- context assembly -----------------------------------------------------------

def _brand_lines(brand: dict) -> str:
    b = brand or {}
    parts = [x for x in (
        f"niche: {b.get('niche', '')}" if b.get("niche") else "",
        f"known for: {b.get('known_for', '')}" if b.get("known_for") else "",
        f"catchphrases: {', '.join(b.get('catchphrases', []))}" if b.get("catchphrases") else "",
        f"audience: {b.get('audience', '')}" if b.get("audience") else "",
        f"voice: {b.get('voice', '')}" if b.get("voice") else "",
        f"platform: {b.get('primary_platform', '')}" if b.get("primary_platform") else "",
        f"goal: {b.get('goal', '')}" if b.get("goal") else "",
    ) if x]
    return "\n".join(parts)


def _context_block(brand: dict, mix: str, identity_context: str, exemplars: str,
                   recent_titles: list[str] | None) -> str:
    parts = ["# THE CHANNEL", _brand_lines(brand) or "(no brand context)"]
    if identity_context:
        parts += ["", "## IDENTITY (the voice ceiling)", identity_context]
    if mix:
        parts += ["", "## THE MIX (rotation guide)", mix]
    if exemplars:
        parts += ["", "## EXEMPLARS (how the channel actually sounds and lands)", exemplars]
    titles = "\n".join(f"- {t}" for t in (recent_titles or [])[:10]) or "(none yet)"
    parts += ["", "## DON'T-COLLIDE — recent titles, verbatim (also the title register)",
              titles]
    return "\n".join(parts)


def _sketchbook_block(sketches: list[dict]) -> str:
    """The sketchbook as the idea pass reads it — a labelled block, capped at a line
    boundary so a truncated concept is never read as a complete one."""
    if not sketches:
        return ("SKETCHBOOK: (absent — the sketch pass produced nothing usable; draft "
                "the three rivals yourself from the context below, same constraints.)")
    lines = [
        "# SKETCH PASS — the shortlist, already filtered",
        "",
        "A wider pass ran before you and laid out the range this channel supports.",
        "These are CANDIDATES, not a decision: back one, combine two, or set them all",
        "aside for something the context argues for harder — but if you discard the",
        "shortlist, say what it missed in your brief.",
        "",
    ]
    for i, s in enumerate(sketches[:12], start=1):
        lines.append(f"{i}. {s.get('title', '')}")
        if s.get("note"):
            lines.append(f"   {s['note']}")
    out = "\n".join(lines)
    if len(out) > _BLOCK_CAP:
        out = out[:_BLOCK_CAP].rsplit("\n", 1)[0] + "\n(shortlist truncated)"
    return out


# --- idea-emit hygiene ----------------------------------------------------------

def _clean_idea(raw) -> dict | None:
    """One emitted idea, armored. Discard-never-repair: a scaffolded or empty title
    or concept kills the idea (there is no card without them); a scaffolded pitch or
    brief is blanked (an empty pitch leaves the card showing the concept, an empty
    brief just hands the next pass less)."""
    if not isinstance(raw, dict):
        return None
    title = _field(raw.get("title"))
    concept = _field(raw.get("concept"))
    if not title or not concept:
        return None
    return {"title": title[:200], "concept": concept[:4000],
            "pitch": _field(raw.get("pitch"))[:600],
            "brief": _field(raw.get("brief"))[:600]}


def _batch_leaks(ideas: list[dict]) -> list[str]:
    """Creator-facing leaks across the batch. The brief keeps its numbers by design —
    it is the internal register."""
    leaks: list[str] = []
    for i in ideas:
        leaks += vocab_leaks(f"{i['title']} {i['concept']} {i['pitch']}")
    return leaks


def _scrub_ideas(ideas: list[dict]) -> list[dict]:
    out = []
    for i in ideas:
        scrubbed = dict(i, title=scrub_vocab(i["title"]),
                        concept=scrub_vocab(i["concept"]), pitch=scrub_vocab(i["pitch"]))
        if scrubbed["title"] and scrubbed["concept"]:
            out.append(scrubbed)
    return out


# --- the funnel -----------------------------------------------------------------

async def bake_ideas(store, creator_id: str, brand: dict, mix: str = "",
                     identity_context: str = "", exemplars: str = "",
                     recent_titles: list[str] | None = None, n: int = 3) -> list[dict]:
    """Sketch → bake-off → up to n winner briefs (brief-shaped dicts compatible with
    ideas.to_briefs output, plus concept/pitch/brief). Flag off, keyless, or ANY
    failure ⇒ [] — NO FALLBACK COPY from this path; the caller falls back to the old
    chain. Never raises."""
    if not palo_flags.enabled(palo_flags.SKETCH_IDEAS):
        return []
    try:
        n = max(1, int(n or 3))
        context = _context_block(brand, mix, identity_context, exemplars, recent_titles)

        # Pass 1 — SKETCH: tool-less, high temperature, plain tagged text. Every
        # failure lane (keyless None, unparseable output) degrades to an absent
        # sketchbook; the idea pass then drafts its own rivals (the cold-start lane).
        sketch_sys = await get_prompt(
            "palo.sketch.generate",
            _SKETCH_SYSTEM.replace("{n}", str(SKETCH_COUNT)), store=store)
        sketch_user = (context + f"\n\nSketch the range. Aim for {SKETCH_COUNT} "
                       "genuinely different sketches, one longshot always.")
        raw = await anthropic_cached(sketch_sys, sketch_user, SONNET,
                                     max_tokens=2000, temperature=1.0)
        sketches = parse_sketchbook(raw) if raw else []
        if raw:
            await ai_usage.record(store, creator_id, "sketch.generate", SONNET, 2600, 700)

        # Pass 2 — IDEA: the bake-off, JSON via schema (not forced tools — reasoning
        # already happened in pass 1; structure lands here).
        idea_sys = await get_prompt("palo.sketch.idea", _IDEA_SYSTEM, store=store)
        idea_user = (_sketchbook_block(sketches) + "\n\n" + context
                     + f"\n\nRun the bake-off and return up to {n} winners, ranked "
                     "best first. Fewer is legal; never pad with venue swaps.")
        data = await anthropic_cached_json(idea_sys, idea_user, _IDEASET_SCHEMA,
                                           SONNET, max_tokens=2000)
        if not isinstance(data, dict) or not isinstance(data.get("ideas"), list):
            return []                                   # keyless / vendor failure
        await ai_usage.record(store, creator_id, "sketch.idea", SONNET, 3500, 1200)
        ideas = [x for x in (_clean_idea(i) for i in data["ideas"]) if x]
        if not ideas:
            return []

        # Firewall: one bounded retry with the REJECTED feedback, then scrub. The
        # retry re-sends the accepted ideas so "keep everything else identical" is
        # actionable in a stateless call.
        leaks = _batch_leaks(ideas)
        if leaks:
            retry_user = (
                idea_user + "\n\nYOUR PREVIOUS IDEAS (JSON):\n"
                + json.dumps({"ideas": ideas})
                + "\n\nREJECTED — the title/concept/pitch contain internal vocabulary "
                "the creator must never see: " + ", ".join(sorted(set(leaks))[:5])
                + ". Rewrite them in plain creator language (no multipliers, no "
                "baseline/median/lift, no hit rates — numbers-speak lives in the "
                "internal brief only). Keep everything else about each idea "
                "identical; the ideas were accepted.")
            retry = await anthropic_cached_json(idea_sys, retry_user, _IDEASET_SCHEMA,
                                                SONNET, max_tokens=2000)
            retried: list[dict] = []
            if isinstance(retry, dict) and isinstance(retry.get("ideas"), list):
                await ai_usage.record(store, creator_id, "sketch.idea_retry", SONNET,
                                      3800, 1200)
                retried = [x for x in (_clean_idea(i) for i in retry["ideas"]) if x]
            if retried and not _batch_leaks(retried):
                ideas = retried
            else:                                       # retry failed or still leaked
                logging.warning("[sketch_ideas] vocab leak after retry (%s) — scrubbed",
                                sorted(set(leaks))[:4])
                ideas = _scrub_ideas(retried or ideas)
        if not ideas:
            return []

        # Dedup-before-create: against the channel's recent titles AND within the
        # batch (a venue swap of winner #1 is not a second winner).
        seen = list(recent_titles or [])
        kept: list[dict] = []
        for i in ideas:
            if any(too_similar(i["title"], t) for t in seen):
                logging.info("[sketch_ideas] deduped against existing title: %.60s",
                             i["title"])
                continue
            kept.append(i)
            seen.append(i["title"])
        kept = kept[:n]
        if not kept:
            return []

        return [{
            "id": new_ulid(), "creator_id": creator_id, "source": "sketch",
            "title": i["title"],
            "summary": i["pitch"] or i["concept"].split("\n", 1)[0].strip(),
            "beginning": "", "middle": "", "ending": "",
            "concept": i["concept"], "pitch": i["pitch"], "brief": i["brief"],
            "score": round(1.0 - idx * 0.1, 3), "status": "new",
        } for idx, i in enumerate(kept)]
    except Exception as e:
        logging.warning("[sketch_ideas] bake_ideas failed: %s", e)
        return []
