# COLD START — ranked portable upgrades (Palo → Marque/Yunicorn)

**Lens:** quality for a brand-new user with NO connected accounts and no history — onboarding-derived identity, first ideas, first scripts, niche discovery from quiz answers, the established-identity path, and honesty without fabrication.

**Sources:** palo-onboarding.md, palo-pulse.md, palo-writers.md, palo-grounding.md, palo-interaction.md, palo-offline.md, palo-llm-infra.md, ld-map.md, marque-current.md (all in `scratchpad/palo_analysis/reports/`), plus `ld_flags_all.json` for full flag texts. Palo_Server is read-only; Yunicorn has full rights to copy prompts verbatim.

---

## 0. Executive read

Palo solved cold start by **converting it into a retrieval + adaptation problem**: extract a *searchable visual description* of what the creator wants to make (not a category), retrieve real view-counted exemplar videos, LLM-filter the hits, degrade honestly to format-priors when the niche isn't in the bank, adapt proven structural skeletons with the creator's content swapped in, gate with a cheap adversarial judge, and squeeze every scarce personal signal (how they type, what they name-drop) into an identity document that drives everything downstream.

Marque's cold start today (marque-current.md §1 "Cold-start behavior") is: 17 hand-authored static `NICHE_PRIORS` (prompts.py:2453), `_feed_topics` mad-lib templates (main.py:10632), a HAIKU ≤80-word first-paint feed, `mock_next_idea` canned beats, and honest-but-empty coach/strategy gates. The honesty discipline is excellent (GROUNDING_BLOCK, verbatim-lift checks, `is_template` sentinels) — **but the day-1 content quality is template-grade**, which is exactly the moment Palo treats as "the single highest-stakes output."

Crucially, everything below is compatible with the Yunicorn hard rule (**never fabricate pillars/data without a real account**): Palo's cold-start prompts are themselves built around honest degradation — MODE 2's "be honest about it," the identity stage-variant's `data_confidence: low`, the text-onboard read's "never claim you watched," the mobile bouncer's "your knowledge comes from their NICHE, not their catalog." Port the wording and the honesty comes with it.

Constraint respected throughout: Yunicorn is short-form talking-head video only, must work with zero connected accounts, and must be self-contained (no LaunchDarkly, no Palo server — prompt text goes into `prompts.py`/`app/palo_prompts.py` with `prompt_store.get_prompt` Supabase overrides, which Marque already has for `palo.*` keys).

---

## 1. RANKED UPGRADES

### #1 — Swap in the LD-main idea-generation prompt (PROOF LINE, structural_patterns, radical simplification, justification-as-niche-insight)

**What Palo does:** LD flag `onboarding-prompt-idea-generation`, variation `main` (10,594 chars; NEWER than the code fallback Marque ported). Adds over the code constant: a `structural_patterns` input, an output-LANGUAGE rule, viewer-desire titling, principles 8–9 (radical simplification + "real sauce"), the per-idea PROOF LINE backed by real exemplar view counts, and the reframed justification ("strategic insight, not process"). Runs Sonnet **temp 0.8** (ideas hot, judges cold — the model/temp ledger in palo-onboarding.md §1). Code wrapper: idea_generation.py:235-292 (exemplars capped at 15, Pydantic `IdeaSet` of exactly 3 `{title, content}` + justification).

**What Marque does today:** `app/palo_prompts.py` `IDEA_GENERATION_SYSTEM` (lines ~95-152) is a **condensed port of the older code fallback** — no structural_patterns slot, no proof line, no simplification principles, no justification spec. It is also flag-dark (IDEA_BANK off) so users see `next_idea_prompt` (HAIKU, 3 generic beats) instead. marque-current.md §2 rates `IDEA_GENERATION_SYSTEM` "the strongest ideation prompt in the codebase" — and it's a generation behind.

**Exact asset:** flag `onboarding-prompt-idea-generation`, variation `main` — full text below (this is the deliverable; port it whole):

```
<!-- 04c-idea-generation -->
<prompt>

<context>

<creator_signals>
{creator_signals}
</creator_signals>

<channel_identity>
{channel_identity}
</channel_identity>

<structural_patterns>
{structural_patterns}
</structural_patterns>

<exemplar_video_analyses>
{exemplar_video_analyses}
</exemplar_video_analyses>

</context>

<role>
Your task: produce 3 SHORT-FORM VERTICAL video ideas (TikTok, YouTube Shorts, Instagram Reels, 15-90 seconds) that make this creator stop and think "this thing actually gets what I do." These are the first ideas the creator will ever see from Palo. If they're generic, the creator dismisses the product and never pays. If they're specific, surprising, and obviously filmable, the creator converts.

This is the single highest-stakes output in the onboarding pipeline. Every idea must be filmable as a short-form vertical video. No long-form, no horizontal, no multi-part series.

LANGUAGE: All output (titles, content, justification) MUST be in the creator's language, which you infer from the conversation_history and creator_signals. Do NOT match the language of the exemplar data. Exemplars may be in any language — they are structural references only. The creator's language is the ONLY language you output in.
</role>

<core_principle>
ADAPT PROVEN STRUCTURE. CHANGE THE CONTENT.

The exemplar video analyses contain real videos that earned real views. Each has a structural outline: how it opens, how it builds, how it pays off. Your job is NOT to invent from scratch. Your job is to take a proven structural formula and adapt the CONTENT to match THIS creator's identity, niche, and voice.

The structure is proven. The only variable you're changing is the topic and creator-specific details. This minimizes risk. This is how the best content strategists work.

For each idea:
1. Pick a structural pattern from the structural_patterns list (extracted from the exemplar data). Each idea should use a DIFFERENT pattern.
2. Use the pattern's skeleton as your blueprint: the hook_template, the beat structure, the payoff mechanic.
3. SWAP the content: Fill in the pattern with THIS creator's niche. Keep the skeleton intact.
4. Make it hyper-specific to THIS creator using details from creator_signals and channel_identity (brand names, catchphrases, specific focus areas)
5. Write it in THEIR energy
6. Verify it's filmable given their setup
</core_principle>

<idea_quality>
The structural adaptation gives you the SKELETON. These principles ensure the IDEA itself is compelling:

1. THE TITLE IS THE PITCH. In a feed of infinite content, the title is the only thing that earns attention. Great titles create an open loop the viewer NEEDS closed. "I Pressure Washed My Neighbor's Driveway Without Asking" makes you need to see what happens. Weak titles describe content. Strong titles create desire to watch. Frame the hook around what the VIEWER desires, not what the creator makes. "Content strategy" is what the creator does. "How to go viral" is what the viewer wants. Always choose the viewer's desire.

2. SPECIFICITY IS EVERYTHING. "I Made Gordon Ramsay's 'Impossible' Scrambled Eggs" hits harder than "Trying a Famous Chef's Recipe." Every idea needs at least one hyper-specific detail that makes it feel like a real video, not a template.

3. BUILT-IN MOMENTUM. The structure should create forward motion at every second: escalation (raising stakes), uncertainty (genuinely unknown outcome), transformation (something visibly changing), or conflict (something at risk). If you can pause at any beat and the viewer wouldn't care what happens next, the idea lacks momentum.

4. THE PAYOFF EARNS THE WATCH. If the hook says "will it work?" show whether it worked. Resolve decisively in THIS video. No cliffhangers.

5. FILMABILITY. The creator must be able to make this with what they have. The best first idea is one they can film tomorrow.

6. SHAREABILITY. The strongest viral driver is "I need to send this to someone." Ideas that tap shared experiences, surprising results, or strong opinions have built-in distribution.

7. VIEW CEILING. At least one idea should have broad appeal beyond the core niche. The best viral ideas use the niche as the SETTING, not the SUBJECT.

8. RADICAL SIMPLIFICATION. Short-form content demands radical clarity. If the hook requires background knowledge to understand, it's too complex. The strongest viral videos take something that SOUNDS complex and promise to make it simple. "Explained so simply a 5-year-old could understand" is a format because it works. Especially for educational or how-to niches: simplify aggressively. The creator's instinct is to overcomplicate to prove expertise. Fight that.

9. SIMPLICITY IS NOT ENOUGH WITHOUT VALUE. Radical simplification doesn't mean shallow. The video still needs "real sauce," something the viewer walks away with that they didn't know before. Every idea must deliver at least one genuinely useful insight, not just a dumbed-down process. The test: would a viewer screenshot this or save it? If not, it needs more substance.

KNOWLEDGE CALIBRATION:
- none/basic: Ideas teach great structure by demonstration. No jargon. Keep beat labels intuitive ("The twist:", "The reveal:").
- intermediate/advanced: Can reference mechanics directly. Ideas can push creative boundaries or layer multiple techniques.
</idea_quality>

<the_three_ideas>
Each idea adapts a DIFFERENT exemplar video's structure.

1. SAFEST BET — Adapt the highest-performing exemplar video's structure. Most proven formula, most views. Change the content to fit this creator. "If you only film one thing, film this."

2. CREATIVE STRETCH — Adapt a structural formula but apply it to an unexpected angle within the creator's niche. Take a proven mechanic and apply it where nobody in this space has used it yet.

3. HIGH CEILING — Adapt the structure with the broadest appeal potential. The idea that could break out of the niche. Swap in content that connects the creator's world to a wider audience.
</the_three_ideas>

<idea_format>
TITLES:
- Must work as actual YouTube/TikTok/Reels titles or spoken hooks
- Literal and specific ("I Tried the Viral 100 Rep Challenge and Here's What Happened to My Total" not "Rep Challenge")
- Create a curiosity gap or a specific promise
- Match the creator's tone and energy
- First person when the creator is in the video

CONTENT:
- 2-4 SHORT sentences. This is a pitch, not a production brief.
- First sentence: the opening visual or hook
- Second: the build mechanic that creates momentum
- Third: the payoff (if not obvious from title)
- Every sentence must be specific enough to film from

PROOF LINE:
- Each idea ends with a single proof line INSIDE the <content> block, formatted in italic markdown.
- Format: name the specific structural element adapted, then back it with view count data from the exemplar.
- Example: *Adapted from the "detail-to-reveal" format — videos using this structure are pulling 5-37M views in your niche.*
- Example: *This uses the "hidden danger hook" structure — creators in your space are hitting 3-8M views with it.*
- Example: *Built on the "transformation holdback" format — the delayed payoff is what's driving 10M+ views in this lane.*
- Name the STRUCTURAL ELEMENT that was borrowed, not just "this format." The creator should learn what specifically makes it work.
- Use real exemplar view counts. Don't inflate.
- NEVER name specific creators or channels.

FORMAT MATCH:
- Read verbal_primacy and visual_primacy from channel_identity
- If verbal_primacy is low: describe ideas as visual sequences, not spoken premises
- If the creator doesn't appear on camera: no first-person filming references
</idea_format>

<validation>
BEFORE OUTPUTTING, CHECK EACH IDEA:
1. Does it reference this creator's specific niche? If the idea could work for any creator, it fails.
2. Does at least one idea use something from creator_signals (catchphrase, brand, specific focus)?
3. Can you trace the structural skeleton back to a specific exemplar video?
4. Could a viewer of this creator's content picture them making this video?
5. If the ideas use copyrighted material (movie clips, TV clips, music), include one brief note: "Keep clips short and transformative for fair use."

ANTI-PATTERN: A Minecraft PvP creator getting "I Tried Every Morning Routine Tip for 7 Days." Zero niche connection. This is a critical failure that will cause the creator to abandon the product.
</validation>

<output_format>
Output exactly: 1 framing bubble, 3 idea blocks, 1 justification bubble, 1 CTA bubble.

<text>Here are 3 video ideas built from what's actually performing in your space.</text>

<idea>
<title>Specific, Literal, Compelling Title</title>
<content>[2-4 short sentences. Hook. Build. Payoff. Filmability note.]</content>
</idea>

[repeat for 3 total]

<text>[1-2 sentences: a niche-specific insight about WHY this type of content works for viewers. NOT a description of the structural pattern you used. The creator should learn something about their audience or their niche they didn't already know.]</text>

THE JUSTIFICATION IS A MOMENT OF STRATEGIC INSIGHT. It should make the creator think "oh, this thing actually understands my space." It is NOT:
- A description of Palo's process ("All three use a detail-to-reveal structure")
- A summary of what the ideas have in common ("Each idea builds through close-ups")
- A reference to exemplar data, structural patterns, or any internal system concept

It IS:
- A piece of niche intelligence about why THIS type of content connects with viewers
- Something the creator can internalize and carry forward beyond these 3 ideas
- Written like a strategist who knows the space, not a system explaining its methodology

GOOD: "Car content that goes viral almost always controls when the viewer sees the full picture. The audience already loves cars. You don't need to convince them to care, you just need to hold back the payoff long enough for them to lean in."
GOOD: "The reason cooking videos outperform recipes is the same reason these ideas work: people don't watch to learn, they watch to feel the transformation happen in real time."
BAD: "All three ideas use the detail-to-reveal escalation structure proven across exemplar videos."
BAD: "Each idea follows a hook-build-payoff format that drives engagement in this niche."

<text>Pick one and we'll write the full script.</text>

RULES:
- ALL text in <text> tags
- <idea> blocks NOT inside <text>
- Exactly 3 ideas
- No em dashes
- Collaborative language: "we'll" not "I'll write for you"
</output_format>

</prompt>
```

**Adaptation for Yunicorn (zero accounts, honesty):**
- Yunicorn is talking-head only → simplify the FORMAT MATCH block (verbal_primacy is always high; keep the "no first-person filming references" branch only if the user says they won't appear on camera).
- **PROOF LINE honesty gate:** Yunicorn has no niche exemplar DB with per-video view counts yet, and the rule "Use real exemplar view counts. Don't inflate" must be code-enforced, not just prompted. Cold start: OMIT the PROOF LINE section entirely (or replace with a niche-prior line clearly framed as pattern knowledge — "this delayed-payoff shape is a proven short-form format," no numbers). Only re-enable proof lines if/when a real exemplar corpus with view counts exists (item #3). This is the Yunicorn never-fabricate rule applied verbatim.
- Feed `{structural_patterns}` from Yunicorn's existing NICHE_PRIORS formats/signals when no exemplars exist — the prompt already handles "(no exemplars available)" gracefully in Marque's port; keep that line.
- Wire it as the `palo.idea.generate` prompt_store override so it ships without redeploy, and run at temp 0.8 (Marque currently doesn't distinguish per-stage temps — copy Palo's ledger: ideas 0.8, eval 0, identity 0.3, script 0.7).
- Surface it: the 3-lane "safest bet / creative stretch / high ceiling" set should be what a day-1 user sees on feed page 0 (via `_merge_briefs`), not `_feed_topics` mad-libs. That means arming IDEA_BANK for new users or moving this chain into the mainline feed path.

**Effort:** S (text swap + one code gate). **Impact:** HIGH — this is Palo's declared "single highest-stakes output," and Marque's current day-1 ideas are its weakest surface (marque-current.md §4.3-4.4).

---

### #2 — Channel Identity document from onboarding-only signals (macro_style dials, voice anchors, anti-horoscope test, data_confidence)

**What Palo does:** `onboarding-prompt-identity-generation` (identity_generation.py:29-218; Sonnet temp 0.3) synthesizes a **channel identity doc** that drives every downstream generation: niche-role assignment (PRIMARY = structural skeleton chosen by FORMAT fit, not topic overlap — "a Geometry Dash creator doing challenge progressions might draw their skeleton from a Madden challenge niche"), the 6 narrative elements, hard word budgets, and two money fields:

`voice_and_tone` spec (verbatim, identity_generation.py / LD main):

```
VOICE AND TONE:
The full communicative texture of this channel.
This is NOT just the literal voice or speech patterns. Capture: the humor style (dry, chaotic, self-deprecating, none), level of irony or sincerity, slang or language register, emoji and caption personality, text overlay voice, how authentic vs. polished the persona feels, the demographic energy (gen z, millennial, niche subculture), what kind of person this content makes you feel like you're talking to, and what cultural or emotional frequency it operates on.
...
Embed 2-3 verbatim examples drawn from the PRIMARY niche's identity.language_examples — real titles, hook lines, or phrases that are characteristic of how this niche sounds. Adapt them slightly for this creator's specific angle if needed, but keep them close to the source. These anchors prevent the voice description from drifting into generic inference.
A downstream LLM reading this should be able to write a caption, script a line, draft a comment reply, or make a joke that lands for this channel - without ever watching a video.
60-120 words. This must be rich enough to generate from.
```

`macro_style` — six low/mid/high dials that select script FORMAT downstream:

```
- verbal_primacy: How much the content relies on words/voice (low = visual-driven, high = narration-heavy)
- visual_primacy: How much the visuals carry the content (low = talking head, high = cinematography/editing-driven)
- content_originality: Original concepts vs. reactive/remix content
- production_level: Polished and produced vs. raw/handheld/lo-fi
- methodical_planning: Scripted/structured vs. improvisational/spontaneous
- factuality_level: Educational/factual vs. entertainment/creative
```

The **Path B variant** (`onboarding-prompt-creator-identity`, LD main, 7,343 chars) is the pure cold-start version — built from conversation history alone, with voice inferred from *how the user types*: "Pull from conversation_history to infer their actual communication style (did they type in lowercase? use slang? brief or verbose?). Include 2-3 example hook lines or captions that demonstrate how this voice sounds." Its anti-pattern block (creator_identity_generation.py:184-194):

```
FAILED IDENTITY MARKERS (if your output resembles any of these, rewrite):
- Primary function is a category label: "Deliver short-form content that entertains and engages"
- Voice section reads like a horoscope: "authentic, relatable, and engaging"
- Creator context is empty: "a content creator in the fitness space"
- Content type is abstract: "educational content" instead of "quick 30-second breakdowns filmed on phone between shifts"

THE TEST: Read each field and ask, "Could this describe a different creator in the same niche?" If yes, it's too generic.
```

The **stage variant** adds the honesty spine for cold start — a relevance assessment FIRST, with an explicit confidence output:

```
Then determine synthesis mode:
- STRONG DATA: At least one RELEVANT niche. Build identity primarily from niche patterns.
- PARTIAL DATA: No RELEVANT, but STRUCTURAL MATCH exists. Extract format patterns only. Build topic-specific fields (voice, content type, elements) from the creator's direction.
- THIN DATA: All OFF-TOPIC. Build entirely from creator's direction and general knowledge. Do NOT import content patterns, voice, visual language, or topic-specific elements from irrelevant niches.

CREATOR PROFILE CHECK: Even when niche data is structurally useful, check that imported patterns make sense for THIS creator. Read the personality_notes and creator_archetype from the selected direction. If they indicate a male college-age record label owner, do not import content patterns from female beauty influencers, even if the format overlaps. The viral MECHANICS may transfer (bait-and-switch, comedic reveals). The CONTENT and TONE must match the actual creator's world.

Output data_confidence: "high" (STRONG), "medium" (PARTIAL), or "low" (THIN).
```

Grounding rule (identity main): "Every claim must trace to the niche data. The niche is the authority. The creator's vision personalizes and narrows; it does not override proven niche patterns. Exception: when the creator's stated direction explicitly conflicts with a niche pattern, the creator wins on direction, the niche wins on execution." Plus the source-tracing `<reasoning>` block: "A field without a source is a hallucination."

**What Marque does today:** the Brand dict (5-ish fields: niche/what_you_do/audience/known_for/catchphrases + sliders) built by `pillars_prompt`/`voice_finalize_prompt`/quiz (prompts.py:1632, 2152). marque-current.md §3.7 note: "Marque's Brand dict is far thinner than this; porting the identity generator would upgrade every downstream write." There is no macro_style, no verbatim voice anchors, no data-confidence, no anti-horoscope validation.

**Exact asset:** flag `onboarding-prompt-creator-identity` variation `main` (Path B — the zero-data recipe) as the base; graft in `onboarding-prompt-identity-generation` `stage`'s relevance_assessment + `data_confidence` and `main`'s voice_and_tone/macro_style field specs + word budgets. Full texts: `scratchpad/palo_analysis/onboarding_flags/` (one file per flag, all variations).

**Adaptation for Yunicorn:**
- Inputs are quiz answers + onboarding chat history + optional pasted references — no niche DB required; run in THIN/PARTIAL mode by default and **persist `data_confidence`** so every downstream prompt (scripts, ideas, converse) can calibrate its claims. This is the structural fix for the honesty requirement: pillars/voice are generated *from the creator's own words*, labeled low-confidence, and never presented as observed data.
- Talking-head only → keep macro_style but expect verbal_primacy high; the dials still matter for `methodical_planning` (scripted vs improv delivery) and `factuality_level` (educational vs entertainment register), which should steer Marque's STYLES selection.
- Store as a new `channel_identity` JSON on the brand row; inject into `scripts_prompt`, `hooks_prompt`, `converse_system`, and `IDEA_GENERATION_SYSTEM` where the thin brand_block goes today. Add the anti-horoscope THE TEST line to Marque's `pillar_judge_prompt` (it's the same idea, sharper).
- When the user later connects an account, re-run in STRONG mode (see #7).

**Effort:** M. **Impact:** HIGH — it upgrades every write for every user, and it's the substrate items #1, #4, #6 consume.

---

### #3 — The graceful-degradation ladder: honest format-fallback direction options (MODE 2) + vision-as-search-query QUALITY CHECK

**What Palo does:** the retrieval ladder (palo-onboarding.md §2): vision_description engineered as a semantic search query → hybrid search → `relevance_filter` with a broader_query rewrite → **MODE 2 format-based fallback** when the DB has nothing. Even without any exemplar corpus, two pieces port directly:

MODE 2 (LD `onboarding-prompt-direction-options` main — 6,327 chars, much newer than code):

```
MODE 2 — LOW CONFIDENCE (fewer than 2 RELEVANT matches, OR all results are structural matches from unrelated niches):
The database doesn't have strong matches for this specific niche. Don't force-fit unrelated exemplars.

Instead, present FORMAT-BASED options that are proven across many niches for this TYPE of content. Frame it honestly:
- For cultural/historical topics → commentary, aesthetic montages, educational breakdowns, documentary-style edits
- For hobby/craft topics → tutorials, process videos, reviews, collection showcases
- For opinion/philosophy topics → talking head with strong hooks, reaction content, debate/ranking formats

Each option should still describe what the VIEWER SEES. But the lane is defined by FORMAT, not by niche exemplar data.
Set exemplar_ids to empty arrays for format-based options. ...
Be honest about it in the recommendation_reason: "Your niche is specific enough that I'm recommending based on what formats work for this type of content, rather than specific creators in your space."
```

The "a video you'd recognize while scrolling" label spec (same flag):

```
3. Write each option as a video you'd recognize while scrolling. Not a format description. Not a category label. A real video.
   The label should make the creator think "oh yeah, I've seen videos like that." If it sounds like a marketing deck or a brainstorm doc, rewrite it.

   GOOD: "Quick tips to camera, one concept per video, casual proof that it works"
   GOOD: "Screen recordings of you building something, sped up, with the final result at the end"
   GOOD: "Close-up shots of the process with satisfying audio and no talking"
   GOOD: "Funny commentary over clips, reacting to stuff in your niche"

   BAD: "Complex code logic explained through dynamic flowcharts, data visualizations, and high-energy narration" — nobody is making this video. Too abstract.
   BAD: "Demonstrating your AI tools while critiquing existing solutions to highlight your project's unique value" — reads like a pitch deck, not a video.
   BAD: "Educational content featuring step-by-step breakdowns" — category label, not a video.

   Keep labels under 15 words. If you can't picture the exact video from the label, it's too abstract.
```

And the vision-description QUALITY CHECK (LD `onboarding-prompt-step-niche-discovery-new`, `stage` variation — "the cheapest fix for bad retrieval"):

```
  QUALITY CHECK before completing: Read your vision_description and ask:
  - Does it describe the specific TYPE of content, not just the topic? ("Minecraft PvP commentary" not "Minecraft content")
  - Does it include the FORMAT? (gameplay + voiceover, talking to camera, visual montage, etc.)
  - Could you picture the actual videos from this description?
  - Would searching this find creators who make SIMILAR videos, or just creators in the same broad category?
  If the description is too broad, you likely need one more question to sharpen it. Ask about format, sub-niche, or what the viewer actually sees.
```

Related: `onboarding-prompt-relevance-filter` main's broader_query recovery ("beat generation lifestyle" → "cultural history commentary and aesthetic content") and the `creator-reranking` stage 3-angle search doctrine (NEVER CUT FOR TOPIC MISMATCH ALONE — cross-niche format matches are load-bearing).

**What Marque does today:** no direction-options step at all. Cold niche handling = word-boundary match into 17 static `NICHE_PRIORS` else `default` (prompts.py:2453/2615). The prior block is honest ("a starting bias, not a rule") but it's one hand-authored table with no refresh path and no user choice.

**Exact asset:** flag `onboarding-prompt-direction-options` variation `main` (full text in `onboarding_flags/`); the QUALITY CHECK block from `onboarding-prompt-step-niche-discovery-new` variation `stage`; `onboarding-prompt-relevance-filter` variation `main` (broader_query ladder) if/when a corpus exists.

**Adaptation for Yunicorn:**
- **Phase 1 (no corpus, S):** run direction_options in permanent MODE 2 — after the quiz, present 3-4 format-based lanes for their topic, written to the "video you'd recognize while scrolling" spec, with the honest recommendation_reason wording. The chosen lane becomes `channel_identity.selected_direction` and steers idea generation + STYLES. This alone replaces the "pick a pillar" abstraction with something a creator can *picture filming*, with zero fabrication — the lanes are format knowledge, not fake niche data, and the prompt says so out loud.
- **Phase 2 (corpus, L, optional):** Marque already scrapes real top posts via Apify for `niche_trends_prompt` — persisting those analyses into a small vector index (Supabase pgvector, which memory_v2 already uses) gives the retrieval ladder real exemplars over time, unlocking MODE 1 and #1's proof lines. Port `search_terms`'s controlled tag vocabulary (~150 tags; "2-5 tags. Quality over quantity") and the relevance filter when this lands.
- The QUALITY CHECK goes into whatever captures the user's self-description (quiz free-text or chat): a cheap self-check that the captured description names TYPE + FORMAT, not a category.

**Effort:** S (phase 1) / L (phase 2 corpus). **Impact:** HIGH — it's the difference between "we guessed your niche from a table" and "here are 3 lanes you can picture yourself filming, honestly framed."

---

### #4 — First-script `{script, reasoning}` contract + tutorial pregen (the day-1 wow moment)

**What Palo does:** the onboarding first script (`onboarding-prompt-script-generation`, script_generation.py:34-197, claude-sonnet-4-5 temp 0.7) emits TWO fields — the script and a `reasoning` field explicitly written for the teach-back step:

```
{
  "script": "<p>the full script in tiptap HTML</p><p></p><p>with proper spacing</p>",
  "reasoning": "2-4 sentences explaining the structural decisions. Which exemplar pattern informed the hook. Why the escalation builds the way it does. What makes the payoff work. This gets passed to the tutorial so it can teach the creator WHY each part was built this way."
}

The reasoning field is internal, the creator never sees it directly. The tutorial generation step uses it to explain Palo's creative process. Write it like you're briefing a colleague: "I adapted the hook from [pattern type] because it creates immediate tension. The escalation follows [mechanic] to keep stakes rising. The payoff callbacks to the opening because [reason]."
```

`tutorial_pregen.py:51-204` turns script + reasoning into a step-by-step "why it works" walkthrough with **exact-substring highlights** (`highlight_text MUST be an exact substring of the script (the frontend uses it for highlighting)... Most of the script will NOT be highlighted. Gaps are expected.`), knowledge-level-calibrated teaching, no fabricated scroll-stoppers ("Don't fabricate a scroll stopper"), privacy ("NEVER reference specific creators... Present all knowledge as Palo's understanding of the niche"), and shared ownership ("Use 'the' or 'this' when referencing the script, not 'your.' Palo helped write this."). Steps 1..N then replay **deterministically** from a stored shadow blob — zero LLM cost, zero drift (write_pyro main.py:904). The write-tutorial-fill LD prompt (16,193 chars, ON) also carries a `<script_quality>` section with **three complete reference scripts** as the quality bar, closing with:

```
WHAT MAKES THESE WORK:
- Every line IS the content. The chat story is the actual conversation. The explainer is the actual voiceover. The skit is the actual scene. None of them describe the video from the outside.
- Real names, real numbers, real details. "Marilyn vos Savant, at around 190." "Cedar offcuts from the site." Not "a famous person" or "building materials."
- A creator can open their editor and build the video using ONLY the script. No guessing.
```

Retention structure carried by the same prompt (identical in Pulse and write stacks — Palo's tested scriptwriting doctrine, script_generation.py:311-333):

```
RETENTION STRUCTURE:
1. PROMISE (hook, 0-3 seconds): Specific cognitive gap. Start mid-action or mid-revelation, never with setup or context. Create an immediate question the viewer needs answered.
2. CONFIRMATION WINDOW (first 10-20%): The highest-leverage segment. The viewer is deciding if this delivers on the promise. Deliver immediate proof or progression. Don't delay with backstory or context-setting. Primary failure mode: delayed validation. Strong videos lose 5-10% here. Weak ones lose 20-30%.
3. CONTINUATION (body): ESCALATION, not just progression. Increasing stakes, not just forward motion. Constantly reinforce what the viewer is waiting for. "Nothing worked yet... but day 7 changes everything" holds viewers. "Day 1... Day 2... Day 3..." without escalation loses them. New significant information every 3-5 seconds. Progress alone does not retain viewers. Anticipation does.
4. PAYOFF (final moments): Deliver the most satisfying information last. Create a callback to the opening that reframes the entire video. Emotional AND informational closure. End decisively. When the payoff hits, the video is over. No epilogue, no recap.

ANTI-PATTERN: The flat progression trap. ...
THE FILLER CUT: Read every line. Does it create tension, deliver information, or advance the payoff? If a line is pure transition with no tension or novelty, cut it. A 25-second script where every line hits is better than 45 seconds with filler.
```

**What Marque does today:** `SCRIPT_FROM_BRIEF_SYSTEM` is ~10 lines (marque-current.md §3.10, rated "thin vs. scripts_prompt"); `scripts_prompt` emits no reasoning; there is no tutorial/teach-back anywhere. palo-onboarding.md §6.10: "Marque's write_agent doesn't emit reasoning today; adding the 2-field `{script, reasoning}` contract unlocks the same teach-back UX."

**Exact asset:** flag `onboarding-prompt-script-generation` variation `main` (= code + `<best_practices>` block; critical sections quoted above and in palo-onboarding.md §3.3); `write-tutorial-fill-prompt` variation `main` (the `<script_quality>` reference scripts + VOICE MATCH / VIEWER SEAT / SECTION SCORING self-critique); tutorial pregen contract from write.py:1022 + tutorial_pregen.py.

**Adaptation for Yunicorn:**
- Yunicorn's first script comes from a chosen idea (#1) + channel_identity (#2) — exactly Palo's inputs, no account needed. Replace tiptap-HTML with Marque's SCRIPT_SCHEMA body (keep the `\n\n` paragraph contract and the speakability lint as-is; drop Palo's four-format selection since talking-head = PURE VOICEOVER always, keeping only the "no first-person filming references if not on camera" branch).
- Add `reasoning` as a 13th field on SCRIPT_SCHEMA (internal, judge/tutorial-facing). Cheap; also improves the judge's job.
- The tutorial is the conversion moment: Yunicorn already has a script editor — highlight-anchored "why this line works" steps map onto it directly, and the exact-substring contract is the whole engineering trick. Grounding stays honest automatically because reasoning explains *structure*, not fake performance data.
- Reference-scripts quality bar: swap Palo's three for three Yunicorn-authored talking-head references (one story, one educational, one contrarian take) written to the same standard.

**Effort:** M (prompt swap S; tutorial UI M). **Impact:** HIGH — it converts the first script from an artifact into a taught experience, and the 2-field contract costs nothing.

---

### #5 — Conversational niche discovery + field check (the tap-not-type onboarding persona)

**What Palo does:** the whole conversational front end (palo-onboarding.md §3.6, §3.9, §3.10, §4). The tested pieces:

- **Persona base** (`onboarding-prompt-base`): "You're the friend who's watched thousands of videos in every niche and has genuinely good taste... Not a bot, not a form, not an interviewer." Acknowledgment taxonomy (GOOD: "Nursing, got it" / BETTER: "Horror Minecraft, that's a good space" / BAD: "That's a solid foundation"), bubble rules ("Each <text> bubble is ONE thought. Max ~15 words... ONE question per turn"), "Never ask a bare question. Always include examples or options," "read, tap, done" cognitive-load rule, fourth wall ("Never mention niche context, creator database, exemplars, search... Niche knowledge is presented as your understanding of the space"), affirmative-detection ("sure/yeah/yep = confirmation. Do NOT ask what they'd change").
- **Routing rules** (LD main): "THE GOLDEN RULE: NEVER ask the same question twice in different words," "Complete in 1-2 turns... It is better to search early and refine later... than to keep asking and lose someone," bolded questions, and the stage additions: NICHE VALIDATION one-liner ("Nice, real estate is actually huge on TikTok right now" — skip if you don't actually know), and **identity-not-strategy questioning**:

```
CRITICAL — NARROW ON IDENTITY/CONTEXT, NOT ON CONTENT STRATEGY: The clarifying question should narrow WHAT they do or WHAT kind of [topic], not HOW they should present it. "What kind of design?" is identity. "Do you want to show process or give tips?" is content strategy. The creator doesn't know what formats work — that's what the search and direction cards are for. Asking them to choose a format pre-search artificially constrains the results and makes the creator pick something uninformed.
```

- The "I don't know" reframes (stage): "What do you spend most of your time doing day-to-day?" / "What's something people always come to you for advice on?" / "What's the thing you could talk about forever without getting bored?"
- The PERSPECTIVE beat (main): "Some topics are perspective-driven... Ask naturally: 'What's your angle on this?'... NOT every topic needs this. 'I'm a nurse' or 'my dog' has a clear enough angle. Use judgment."
- **Field check** (LD main, ≤3 human turns): the research-payoff framing ("The creator just waited ~10 seconds for Palo to study their space. This is the payoff."), NICHE-AWARE FRAMING (mainstream vs unusual topic — "If claiming 'millions of views in [niche]' would sound fake, use the second framing... Palo's value for niche creators is cross-pollination"), and the "paint the video" confirmation:

```
Describe what a typical video in this lane actually looks like. The creator should be able to picture themselves filming it.
<text>Nice. Videos in this lane typically look like close-ups of your hands doing the work while you talk through what you're doing.</text>
<text>Quick, casual, usually 30-60 seconds. Think "here's a trick most people don't know" type of content.</text>
<text>**Can you see yourself filming something like that?**</text>
The goal is to make the video feel REAL and filmable. The creator should think "I could shoot that tomorrow."
```

- The vision_selection stage close (the best single line for identity capture): "Anything I should adjust? And if there's anything else that's part of your world, like inside jokes, characters, catchphrases, or things your audience would recognize, let me know. The more I know, the better everything I make for you will feel."
- `personality_notes` inferred, never asked: "gender, age range, cultural signals, energy, slang. Do NOT ask for personality signals. Observe." Brand references are high-signal: "('like Nude Project,' 'similar to MrBeast') ... Treat them as equivalent to a detailed topic + format description. Complete immediately."

**What Marque does today:** a 17-step quiz onboarding (marque_onboarding_overhaul memory) + optional ElevenLabs voice interview; converse chat has chips-must-answer discipline but onboarding is form-shaped, not conversational, and none of the above tested wording exists.

**Exact asset:** flags `onboarding-prompt-base` (main + the stage blocks: dialect matching, PACING, SKIP-AHEAD, NONSENSE INPUT, BUBBLE LENGTH), `onboarding-prompt-routing` (main + stage), `onboarding-prompt-field-check` (main), `onboarding-prompt-step-niche-discovery-new` (main + stage QUALITY CHECK), `onboarding-prompt-step-vision-selection` (main + stage). Full dumps: `scratchpad/palo_analysis/onboarding_flags/`.

**Adaptation for Yunicorn:**
- Don't rebuild the whole 17-step onboarding; graft the conversational niche-discovery + field-check as the *content* segment (topic → angle → direction cards → confirm), keeping Marque's existing paywall/permissions steps. The step machine (`<step_result>` trailer + typed bubbles) is small; Marque's converse envelope already parses structured output and chips — the option-select bubbles map onto chips.
- Port `agent.py::_clean_chat_history`'s `[selected_option: X]` → `[selected: Label]` rewrite (palo-onboarding.md §6 gotcha) — Marque's chip chat likely has the same "model forgets what it offered" bug.
- Honesty is built in: NICHE VALIDATION says skip when unsure; niche-aware framing forbids fake "millions of views" claims; fourth wall keeps machinery invisible without inventing data.

**Effort:** L. **Impact:** HIGH for conversion and identity quality (it's what makes #2's inputs rich), MED for pure script quality — do after #1-#4 if sequencing.

---

### #6 — Best-practices named pattern library (the cold "insider knowledge" bank)

**What Palo does:** `onboarding-prompt-best-practices` (best_practices.py:28-172, Sonnet 0.3-0.4, max_tokens up to 16000) generates a per-creator library of NAMED, reusable mechanics — before any of the creator's own data exists (cold recipe = from niche exemplars; established recipe = from their own catalog):

```
Each practice is a named, reusable content mechanic or principle. Not generic advice - a specific, recognizable thing that works in this space.
"Make your hooks attention-grabbing" is NOT a practice.
"The Impossible State Opening - Start with the most extreme or absurd version of the 'before' state" IS a practice.

TWO CATEGORIES:
NARRATIVE PRACTICES - HOW videos work structurally. [2-4 named practices per narrative element present in the identity]
CONTENT PRACTICES - WHAT videos are about... These are the content "plays."
[3-6; examples:]
- "The Authority Test" - challenging people in positions of power to see how they react
- "The Bad Decision Autopsy" - breaking down a famous decision to show the cognitive bias behind it
- "The Everyday Trap" - revealing a psychological trick hidden in a common daily situation

EACH PRACTICE HAS TWO VERSIONS:
brief - One sentence. What the agent sees in its context window. Clear enough to reference by name in conversation.
full - The complete playbook entry:
- description: What this practice is and how it works (2-3 sentences)
- why_it_works: The viewer psychology - why this mechanic creates engagement (1-2 sentences)
- examples_for_creator: 2-3 concrete video concepts personalized to this creator's specific topic and angle. These should feel ready to film, not generic placeholders.
- execution_notes: How to do it well and what to avoid (2-3 bullets)
```

Minimums: ≥2 practices per narrative element, ≥3 content practices, ≥10 total. Usage doctrine: "When a creator says 'give me ideas' - the agent reaches for content practices. When it writes a script - it uses narrative practices to structure the flow. When it gives feedback - it measures against these mechanics." This is the resident brief/full + dereference pattern that later becomes the exemplar bank (interaction agent binds `exemplar_tool` and holds cards "on a pedestal when scripting" — palo-interaction.md §2.6).

**What Marque does today:** doctrine/strategy principles exist (flag-dark), but there is **no named per-user pattern bank** — the closest is `exemplar._template_bank` keyless seed, which contradicts house doctrine (question-opener hook vs "Question-openers underperform statements," marque-current.md §4.9 — fix that line while here).

**Exact asset:** flag `onboarding-prompt-best-practices` variation `main` (≡ code, best_practices.py:28-172); the brief/full schema; plus best_practices.py:280-319's defensive validators for Claude double-encoding nested JSON. For warm users later: `established_best_practices.py` (fill-in-the-blank hook/payoff formulas, "Every pattern must trace to evidence in the video analyses").

**Adaptation for Yunicorn:**
- Cold input = channel_identity (#2) + selected direction (#3) + general niche knowledge. Honesty framing: practices are *format knowledge for this type of content* — never claim "creators in your space are doing X views" without data; strip any view-count language from the cold variant (the prompt's examples don't carry numbers, so this is nearly free).
- Store per-user on the brand row; inject the `brief` list into `scripts_prompt`/`hooks_prompt`/`converse_system` as a compact block; `full` entries dereference on demand (Marque's converse intent system can carry a `get_practice` action, or just inject top-3 fulls into scripts).
- The `examples_for_creator` field doubles as a standing idea reservoir — the feed can surface one "play" per day with its ready-to-film examples, which fixes "next-idea never gets more than one HAIKU call" (marque-current.md §4.3).

**Effort:** M. **Impact:** MED-HIGH — it's what makes chat and suggestions feel like insider knowledge instead of generic advice, and it's the cold precursor to the exemplar bank.

---

### #7 — Established-identity path: metadata-only first read + patterns-vs-content abstraction + anti-target ideation

**What Palo does (for the user who HAS an account to point at):** three assets:

(a) **`text_onboard/read.py`** — the newest cold-start asset (Aug 3): ONE Sonnet call over metadata only (≤40 title/views/date rows), full prompt verbatim:

```
You are Palo, an AI content strategist, texting a creator who just onboarded over iMessage. You've been given METADATA about their channel: platform, follower/subscriber count, recent video titles with view counts and publish dates, and platform totals. You have NOT watched any videos — never claim you did ("watched", "saw the video"). You "went through" their channel.

Write the first channel read: 2-4 separate text bubbles. Voice: sharp friend, lowercase-casual, direct, zero corporate. Numbers exactly as given (you may compact: 64518968 -> 64.5M). Rules:

- Bubble 1: what their channel IS (infer the niche/formula from titles) + scale, in one natural line.
- Middle bubble(s): the sharpest pattern you can defend from titles+views — what their audience clearly wants more of. Name specific videos in quotes. Median vs top gap. Cadence if notable (posting streak or drought).
- Last bubble: ONE concrete, filmable suggestion derived from that pattern ("your next video should...").
- Platform vocabulary: YouTube = views/subscribers; TikTok/Instagram = plays/followers.
- Never invent numbers, videos, or facts not in the input. If the catalog is thin (<5 videos), say what you can honestly and keep it to 2 bubbles.
- <= 300 chars per bubble. At most one emoji total.

Return ONLY JSON: {"lines": ["bubble 1", "bubble 2", ...]}
```

(b) **The abstraction principle** (`established_identity_generation.py`, LD ≡ code) — the spec for deriving a reusable identity from real posts without codifying one-off content:

```
LAYER 1 — SPECIFIC CONTENT (do NOT codify): character names, specific scenarios...
LAYER 2 — REUSABLE PATTERNS (DO codify): structural skeleton, voice mechanics, visual grammar, audience contract, content axes
Example of WRONG identity output: "Derek is the antagonist who always gets fired in an ironic way" → This describes one storyline, not a pattern.
Example of RIGHT identity output: "A named antagonist whose downfall is self-inflicted — institutional justice delivered through formal process language, never personal revenge" → This is a reusable pattern that generates infinite new stories.
...
Think of it like this: if you watched 10 of their videos and then had to brief a ghostwriter to create an 11th that the audience wouldn't question, what would that brief contain? That's the identity.
```
Test: "Could a writer use this to create a NEW video the audience would recognize as this creator's? If not, it's too specific."

(c) **Anti-target ideation** (`established_idea_generation.py`): "The creator has already published these videos — DO NOT REPLICATE THEM... Use these as anti-targets. You know their territory. Now push beyond it." / "Think: same engine, new destination." / "If the ideas just rehash what they've already published, they'll dismiss Palo as a parrot." Plus warm-start 3-lane respec (SAFEST BET = proven format at uncovered subject; CREATIVE STRETCH = "combines two of their signature moves"; HIGH CEILING = format × broader cultural moment). Ops pattern: `fast_video_analysis.py` — analyze 10 videos in parallel, return at 4 successes, cancel the rest.

**What Marque does today:** `derive_from_posts_prompt` (posts → brand, good but no layer-1/layer-2 discipline), style_profile/dossier work; no metadata-only first-read moment; ideation has no catalog anti-target rule in the mainline path (the interaction-agent version — "NEVER pitch a video the creator has already made. Check every idea against the CATALOG SHEET" — exists in Palo's `generate_ideas` skill, not in Marque).

**Exact asset:** `text_onboard/read.py::_PROMPT` (above, whole); `established_identity_generation.py` abstraction section; `established_idea_generation.py` anti-target sections; the `generate_ideas` skill's catalog-dedup paragraph (palo-interaction.md §5).

**Adaptation for Yunicorn:**
- The metadata read is a perfect fit for the moment right after PFM/Apify account connect, **before** any video analysis finishes: cheap, honest by construction ("went through," never "watched"), and it makes connection feel instantly worth it. Render as chat bubbles or a card; thin-catalog branch included.
- Feed the abstraction-principle layer-1/layer-2 rules into `derive_from_posts_prompt` and the ghostwriter-brief test into the style_profile spec — it is exactly the spec Marque's dossier wants.
- Add the anti-target block + a last-50-titles "catalog sheet" (3KB resident, palo-interaction.md §2.8) to `IDEA_GENERATION_SYSTEM` and `next_idea_prompt` for any user with ≥1 real post — "repeating a creator's own catalog back to them as a fresh idea destroys trust instantly."

**Effort:** S (read prompt) + S (anti-targets) + M (abstraction rework). **Impact:** MED-HIGH — the read is a cheap wow; the anti-target rule prevents the single fastest trust-killer for connected users.

---

### #8 — Identity-only mode injection + honest-boundary rules (kill the "I lack context" failure)

**What Palo does:** when a channel has identity but no patterns/analytics/videos, the agent appends (agent.py:62-69, 1362-1387; LD flag `interaction-agent-identity-only-mode`):

```
This channel was recently set up. ... USE the identity to fulfill requests — brainstorm ideas that match their style, voice, and content type. Do NOT say you lack context or ask what they make. You know who they are from the identity above.
```

Paired with the mobile-onboarding bouncer's epistemic-boundary rules — the exact honest framing for a user with an identity doc but no analyzed data:

```
- You can't reference "your videos" or "your performance" unless they've told you about specific videos
- Your knowledge comes from their NICHE, not their catalog. Frame it that way: "in your space" not "in your content"
- Structural patterns are from exemplar analysis. Present them as "what works in your space" not "your patterns"
- If they ask about their analytics or specific video performance: be honest. You haven't analyzed your content yet. ... Don't say "still processing" or "give me a bit" as if it's happening in the background. It's not.
```

**What Marque does today:** cold `converse` gets "CREATOR MEMORY: (empty — this is a new relationship; start learning who they are)" — honest but passive; nothing prevents "tell me more about what you make" deflection, and nothing codifies the in-your-space-not-your-content register.

**Exact asset:** the two blocks above (flag `interaction-agent-identity-only-mode`; `mobile-onboarding-interaction-bouncer` main, honest-boundary section — full dump in `onboarding_flags/`).

**Adaptation:** append the identity-only block to `converse_system` and `scripts_prompt` whenever `settled_posts == 0` and channel_identity (#2) exists; add the in-your-space register rules to `converse_system` permanently for pre-connection users. Pure honesty upgrade — it *forbids* fabricated performance talk while forbidding helplessness. **Effort:** S. **Impact:** MED — small text, fixes a whole class of day-1 chat failures.

---

### #9 — Personalized loading theater (`strategy_loading_steps`)

**What Palo does:** `onboarding-prompt-strategy-loading-steps` (LD `prod`, gpt-5.4-mini temp 0.7) generates 12-15 personalized loading steps shown while the batch pipeline runs — first-person Palo inner monologue:

```
Palo is not a base model. It has deep knowledge of narrative structure, storytelling mechanics, and what makes short-form content perform. The loading steps should communicate that depth, but in language anyone can understand (even a 90 year old man). Say "looking at hooks" not "analyzing rest and reveal hook architectures."

BODY VOICE: Every body line is written from Palo's perspective, first person. Palo is talking to itself, thinking out loud. "I need to see what's actually working here." "I'm breaking down how these videos are structured."
```

Plus: personalization by topic ("Scanning gardening content hooks" not "Analyzing your niche"), 4-phase progression (research → strategy → creative → assembly), and hard bans (no internal system language, no progress-bar talk, no overpromising, no exemplar references). Worked 13-step gardening example at strategy_loading_steps.py:79-94.

**What Marque does today:** nothing — waits are spinners (strategy compile, first analysis, feed page upgrade from HAIKU to OPUS).

**Exact asset:** flag `onboarding-prompt-strategy-loading-steps` variation `prod`; code rules strategy_loading_steps.py:35-94.

**Adaptation:** one Haiku call generating steps for (a) onboarding idea/script generation wait, (b) post-connect analysis wait, (c) feed quality-pass upgrade. Honesty note: the bans already prevent overpromising; add "never claim to be analyzing videos that aren't being analyzed" for the zero-account path (steps describe *thinking about the niche*, which is what's actually happening). **Effort:** S. **Impact:** MED — pure perceived-quality/conversion; makes the pipeline's real work legible.

---

### #10 — Idea-eval + cold judge calibration (parity check, one addition)

**What Palo does:** `onboarding-prompt-idea-eval` (Gemini flash temp 0) — the niche-connection kill gate. **Marque already has this** (`IDEA_EVAL_SYSTEM`, ported unchanged — parity, skip).

The one addition worth taking: Palo's stage identity prompt outputs `data_confidence`, and the interaction agent's honesty rules carry it into claims ("a niche-proven mechanism that's untested on them is a real bet, not a sure thing; carry that uncertainty into how you pitch it"). Marque's script judge has a documented cold-start groundedness gap (marque-current.md §4.10: with zero posts the judge has "almost nothing to check fabrication against beyond the 5-field brand block"). Injecting channel_identity (#2) + its `data_confidence` into `script_judge_prompt`'s CREATOR CONTEXT materially widens what the judge can verify against on day 1 — the identity's verbatim voice anchors give voice_match something to measure, and macro_style gives format_fit a target.

**Effort:** S (once #2 exists). **Impact:** MED.

---

## 2. Items deliberately SKIPPED (parity or wrong lens)

- **Idea eval gate** — already at parity (above).
- **GROUNDING_BLOCK / bracketed fill-ins / speakability lint** — Marque's honesty machinery is already best-in-class; Palo has nothing better for the zero-data case. Keep.
- **Coach/next-idea deterministic honesty gates** — Marque's verbatim-lift acceptance check and silence-on-no-signal are as good as Palo's equivalents.
- **Sketch→idea bake-off funnel, exemplar bank v2.5/v2.6, strategy v2.7, outcome predictor, Pulse decider/judge/vitals, write-agent v3.3, conversation summarizer v4.1** — all major, all covered by the offline/grounding/writers/pulse/interaction lenses; their cold-start behaviors are noted there (e.g. the idea pass drafts its own rivals when the sketchbook is absent; the bank builds an all-`experiment` modeled library for a videoless channel — that pattern matters once Yunicorn has a niche corpus, i.e. #3 phase 2).
- **Bouncer sales agents** — Yunicorn's paywall model differs (trial = full access, maxapp memory analog); the honest-boundary rules were extracted into #8 instead.
- **Mobile/iMessage delivery layer** — not Yunicorn's surface.

## 3. Sequencing recommendation

S-effort quick wins first: **#1 (idea prompt swap + proof-line gate) → #8 (identity-only mode) → #7a (metadata read) → #9 (loading steps)** — four prompt-level changes, no schema work, all honest by construction. Then **#2 (identity doc)**, which unlocks **#4 (first script + tutorial)**, **#6 (practice library)**, **#10 (judge context)**, and **#3 phase 1 (MODE 2 direction cards)**. **#5 (conversational onboarding)** and **#3 phase 2 (exemplar corpus)** are the L-effort bets that make everything upstream richer.

Full verbatim texts for every flag referenced: `scratchpad/palo_analysis/onboarding_flags/*.txt` (per-flag, all variations), `scratchpad/palo_analysis/prompts/*.txt` (write/outline/offline), `scratchpad/palo_analysis/ld_flags_all.json` (everything). Code paths are under `/Users/home/Palo_Server/palo_python/` (read-only).
