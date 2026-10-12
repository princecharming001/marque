"""The planner's prompt text: the condensed doctrine checklist (cached prefix), the schema framing, the brief/style
hints and the user turns (digest, retry).

Why a condensed checklist and not the doctrine itself: the Director reads ``skills/editing/SKILL.md`` (4.8k tokens)
plus every topic file it loads (219-652k chars on the real jobs) because it drives 36 tools over nine stages. The
standard tier's planner answers once, by ID, from a measured digest, so it needs only what changes a story cut or a
caption accent: the eight core directives, the style picker and the meaning of each :class:`PlanDecision` field.
Everything mechanical (pauses, seams, placement, loudness) is code and stays out of the prompt. The checklist is
kept under 1k estimated tokens (:func:`studio.plan.digest.estimate_tokens`) because the prefix is paid on every
call and cached only for the retry seconds later (``PLANNER_CACHE_TTL`` 5m); a test holds the size.

Why the schema is in the prefix as well as in the native structured-output request: the schema carries no
descriptions (Anthropic's grammar compiler budget, see :mod:`studio.plan.decision`), so the checklist's "Fields"
block is the only place a field's meaning is stated, and showing the compact schema next to it lets the model see
the exact key names and the reason enum while it reads the digest.
"""

from __future__ import annotations

from studio.plan.decision import PLAN_SCHEMA_JSON

__all__ = [
    "CHECKLIST_MAX_TOKENS", "DOCTRINE_CHECKLIST", "SCHEMA_INTRO", "build_prefix", "digest_prompt", "retry_prompt",
]

#: The checklist's budget (estimated tokens, ``len(text) / 3.5``): the interface's "<= 1k tokens of doctrine".
CHECKLIST_MAX_TOKENS = 1000

#: SKILL.md core directives + the story-cut rules + the style picker + the field meanings, condensed for one call.
DOCTRINE_CHECKLIST: str = """\
You plan a one-pass edit of a creator's talking-head take for TikTok, Reels or Shorts: read the measured digest, \
answer once with a PlanDecision JSON naming IDs from the digest only (sentence sNNN, cluster cNN, word wNNNN, pause \
gNNNN). Code applies it. Never write times.

Doctrine:
1. Story before polish: the kept words must pass the radio test (picture off, beat to beat, paying off the hook).
2. Every addition needs a one-line job; try nothing first. If the video is no worse without it, leave it out.
3. Cut where a thought ends. Pauses are punctuation: keep the speaker's rhythm.
4. Match the measured energy; calm is a legitimate style; never fake energy.
5. Protect what only this creator could say: the spoken CTA, catchphrases, laughs, qualifiers, flubs. A line is a \
false start only if a later re-delivery exists. Never invent a CTA; never drop the one they say.
6. Restraint: none is a valid answer for drops, punch-in, emphasis, markers and text fixes. Over-editing is the \
failure to avoid; a clean take may need no drops.
7. Keep the creator's order. The first kept line is content, never preamble. End on the last line that adds \
something, then the spoken CTA.

drop_sentences, each with a reason: preamble (greetings, "today I want to talk about"), tangent (the payoff lands \
without it and it carries no emotion or identity), after_payoff (after the last line that adds something, except \
the CTA), unfinished (an abandoned fragment with no re-delivery), repeat (a weaker statement whose kept re-delivery \
exists in its cluster), repeats_hook (a later line restating the kept hook). Drops remove at most half the spoken \
words. Code keeps each cluster's recommended take; choose_take only when another take is the better complete \
delivery.

Style: classify by what the viewer should do afterwards: understand (explainer), do (tutorial), choose (listicle), \
feel a story (storytime), laugh (comedy), argue (hot_take), hear an exchange (podcast_clip), buy (sales_ugc), trust \
a company (founder_brand); signals are enumerators, imperatives, past-tense narration with a turn, setup-punch \
pairs, an early verdict, a product plus an offer. One primary, at most one secondary ("3 mistakes" = listicle + \
explainer; "5 steps" = tutorial + listicle, never reorder steps; rant = hot_take + storytime). When dials conflict, \
take the quieter one. High energy allows a punch-in; calm keeps long pauses and none; comedy keeps its own timing.

Fields: goal = the one idea, at most 25 words. hook_promise = what the opening commits to; payoff = the word range \
that delivers it. cta = the spoken call to action's words. must_keep = ranges code must never trim. \
deliberate_beat_gap_ids = pauses kept at measured length (a beat before a reveal); others are trimmed to the \
speaker's median by code. cut_filler_word_ids = only {um} words, in silence or opening a sentence. punch_in = at \
most one, on the payoff or turn, with a reason. caption_emphasis_word_ids = at most 8, one per phrase, from the \
measured emphasis list. caption_text_fixes = display text for a mis-heard word (names, numbers), never the audio. \
structure_markers = short labels ("1/3", "Step 2") on the words opening each item, listicle or tutorial only. \
notes = one or two lines for the report.\
"""

#: Framing for the compact schema that follows it in the prefix.
SCHEMA_INTRO = ("Answer with exactly one JSON object matching this schema (keys as named, no extra keys, no "
                "prose around it):")


def build_prefix(*, brief: str | None = None, style: str | None = None) -> str:
    """The cached prefix (the agent's instructions): checklist, schema, then the creator's brief and style hint when
    given. The hints are last so the checklist and schema bytes are identical across jobs."""
    parts = [DOCTRINE_CHECKLIST, SCHEMA_INTRO, PLAN_SCHEMA_JSON]
    if brief and brief.strip():
        parts.append("Creator brief (their goal and protected material win over doctrine priors, never over the "
                     "rules above): " + " ".join(brief.split()))
    if style and style.strip():
        parts.append("Requested style (use it as style_primary unless the transcript plainly contradicts it): "
                     + " ".join(style.split()))
    return "\n\n".join(parts)


def digest_prompt(digest_text: str) -> str:
    """The first user turn: the digest, then the ask."""
    return digest_text.rstrip() + "\n\nRead the whole transcript, then return the PlanDecision for this take."


def retry_prompt(rejections: list[str]) -> str:
    """The one retry's user turn: every rejection line, verbatim, and what to do with them."""
    lines = "\n".join(f"- {r}" for r in rejections)
    return ("Your decision was rejected by the validator for these reasons:\n" + lines
            + "\n\nReturn a corrected PlanDecision that fixes every line above and keeps the rest of your decision. "
              "Name only IDs that appear in the digest.")
