# PORT PLAN — Palo → Marque/Yunicorn (merged, ranked)

Merged from `gaps-scriptwriting.md`, `gaps-suggestions.md`, `gaps-coldstart.md` (dedup applied; each
item notes which lenses claimed it). Ranked by **impact-per-effort for a short-form talking-head
video app whose users may have ZERO connected accounts** — cold-start-safe items are boosted,
analytics-dependent items demoted.

**Ground rules honored:** `/Users/home/Palo_Server` read-only (all code refs are read-and-copy).
Target backend: `/Users/home/Marque/backend` (FastAPI, Supabase). No LaunchDarkly — every ported
prompt is served via Marque's existing `prompt_store` Supabase override mechanism (register new
keys; mainline script prompts currently have NO override hook — fixing that rides along with item 1).
Verbatim texts live in this scratchpad:
- `prompts/*.txt` (write/outline/pulse/offline flags, per-variation)
- `onboarding_flags/*.txt` (all onboarding flags, all variations)
- `flags/*.txt`, `quote_dump/*.txt` (edit-ops, judges, exemplar bank, strategy)
- `ld_flags_all.json` (everything, 189 flags, full variation values)
Palo code paths: `/Users/home/Palo_Server/palo_python/…` (read-only).

**Dedup ledger:** SW = gaps-scriptwriting item #, SG = gaps-suggestions, CS = gaps-coldstart.
- SW10 ≡ CS4 (first-script reasoning + tutorial) → merged as item 9.
- SG9 ≡ CS1 (idea-gen LD main + PROOF LINE) → merged as item 2.
- SW8's banned-phrase regex ≡ SG2's Go-gate regex → merged into item 5.
- SW6 ≡ SG12 (output contracts + emission armor) → merged as item 8.
- SW7 (verbal_primacy format gate) folded into item 3 (macro_style is its data source).
- CS10 (judge context from identity + data_confidence) folded into item 3.
- SG8 (card anatomy) folded into item 7. SW11 (opener dedup) folded into item 1.
- SW12 (prompt_store registration + CACHE A/B) folded into item 1 as the enabler.

---

## TOP 10

### 1. Core writer upgrade — a5 script prompt + `<planning>` block + voiceprint exemplars + opener dedup, behind prompt_store
*(merges SW1 + SW2-minimum + SW3 + SW11 + SW12)*

**Assets to copy (verbatim):**
- LD flag `pulse-script-prompt`, variation **`stage`** (= `a5-script-generator v1`, SERVED in prod)
  → `prompts/pulse-script-prompt__stage.txt`. The 10 rules are the tested statement of spoken-script
  craft: "THE FIRST LINE IS THE VIDEO" (mid-action/consequence-first, never setup, never the payoff);
  "EVERY LINE EARNS THE NEXT" ("a line that could end the video before the end is a line to
  rewrite"); "THE PAYOFF LANDS LAST" (callback close, "then stop — no recap"); "WRITE WITH THE
  CREATOR'S MOUTH" (read-aloud test: "if any line sounds like writing instead of this creator
  talking, it fails"); SHOW DON'T NARRATE; GROUNDED STILL; DURATION IS A MEASUREMENT; vocabulary
  firewall — plus the worked RIGHT script (NBA Finals ladder) and the annotated WRONG script
  ("'Little did I know…' is written language no creator speaks"; "'Over 20,000 passionate fans' is
  printed nowhere in the package — an invented specific, the worst failure").
- The mandatory `<planning>` contract paragraph from `prompts/agent-write-prompt__Treatment_1.txt`
  (write-agent v3.3): ~150 words, THE READ → THE RUNGS (one line per beat) → THE SELF-CHECK ("walk
  the rungs as a viewer. After each reveal, what are they still waiting for? If the answer is ever
  'nothing'… fix the structure NOW. Structure problems get solved here, never mid-write") → the
  yardstick line. Costs ~150 output tokens, zero extra calls.
- Voiceprint block: `pulse/script.py::_render_exemplars` shape (top-6 best videos as compact
  per-video style notes: bucket, theme, hook, summary, structure, payoff, verbatim opener) + rule-5
  framing "study its verbatim opener, its sentence lengths, its lexicon… write THIS script in that
  hand. Mimic the shape, never lift the line" + the RETELL warning ("transcripts teach VOICE, never
  content — a new script that retells an old video's story is a failure even when every sentence
  sounds like them"). Note Palo RETIRED a Pinecone voice-centroid scorer in favor of this:
  "the few-shot conditioning at gen-time is what actually transfers the creator's voice."
- Opener dedup: derived opening lines of last 5 scripts injected with "Your draft's first line
  never duplicates one of these openers" (`write_pyro/main.py:452-469`); apply intra-feed-page too.

**Target:** `prompts.py::scripts_prompt` (line ~1676) + `_voice_exemplars` (:1551); register
`scripts`, `script_judge`, `steer`, `hooks`, `hook_judge` in `prompt_store`; adopt Palo's CACHE A
(stable per-channel) / CACHE B (volatile) block ordering (instructions → identity → strategy →
exemplars) with the 4096-char min-cache gate; never `str.format` on override text, append-if-absent
placeholder guards.

**Adaptation:** swap `<inputs>` package for Marque's blocks (brand_block, real posts, emulation,
memory, niche priors). Keep Marque's judge, best-of-N hooks, speakability lint + HAIKU repair (all
better than Palo's — do not replace). **Cold start:** Palo's own degrade — no posts ⇒ voiceprint
block omitted + "No example scripts available — lean on the brand voice and niche priors"
(write.py:1172-1188 pattern); rules apply regardless.
**Effort:** M (one combined prompt revision + adapter + prompt_store keys). **Impact:** HIGH — the
core product surface, tested wording for exactly Yunicorn's format.
**Risk:** low; watch prompt length vs. cost (drop the 20 raw posts currently riding the user
message once the distilled voiceprint lands — net token WIN); planning block must be stripped or
surfaced deliberately in the client.

### 2. Day-0 idea generation — `onboarding-prompt-idea-generation` main + PROOF-LINE honesty gate + 3-lane feed surfacing
*(SG9 ≡ CS1 — THE cold-start item)*

**Assets:** LD `onboarding-prompt-idea-generation` variation **`main`** (10,594 chars, NEWER than
the code fallback Marque ported 2026-07-13) → `onboarding_flags/onboarding-prompt-idea-generation.txt`;
full text also quoted whole in `gaps-coldstart.md` §1. Key deltas over Marque's
`IDEA_GENERATION_SYSTEM`: `structural_patterns` input; core principle "ADAPT PROVEN STRUCTURE.
CHANGE THE CONTENT… The structure is proven. The only variable you're changing is the topic";
viewer-desire titling ("'Content strategy' is what the creator does. 'How to go viral' is what the
viewer wants"); principles 8–9 (RADICAL SIMPLIFICATION + "real sauce": "would a viewer screenshot
this or save it?"); the PROOF LINE spec; justification-as-niche-insight with GOOD/BAD pairs
("a moment of strategic insight… NOT a description of Palo's process"); the anti-pattern
("A Minecraft PvP creator getting 'I Tried Every Morning Routine Tip for 7 Days'… critical
failure"). 3-lane set: SAFEST BET / CREATIVE STRETCH / HIGH CEILING. Run temp 0.8 (Palo's ledger:
ideas 0.8, eval 0, identity 0.3, script 0.7 — Marque currently has no per-stage temps).

**Target:** `app/palo_prompts.py::IDEA_GENERATION_SYSTEM` (~:95-152), served as `palo.idea.generate`
prompt_store override. Surface the 3-lane set as feed page-0 briefs via `_merge_briefs` (replacing
`_feed_topics` mad-libs for new users) — which means arming IDEA_BANK for new users or moving this
chain into the mainline feed path.

**Adaptation / honesty:** **PROOF LINE code gate** — Yunicorn has no exemplar DB with real view
counts; "Use real exemplar view counts. Don't inflate" must be enforced in code: OMIT the proof-line
section entirely at cold start (or emit a numbers-free pattern line: "this delayed-payoff shape is a
proven short-form format"). Re-enable only when a real corpus exists (item 10/open Q2). Feed
`{structural_patterns}` from NICHE_PRIORS formats until then. Talking-head ⇒ simplify FORMAT MATCH
(keep only the not-on-camera branch).
**Effort:** S (text swap + one code gate) / M with feed rewiring. **Impact:** HIGH — Palo calls this
"the single highest-stakes output"; Marque's day-1 feed is its weakest surface.
**Risk:** honesty-rule conflict is real if the proof-line gate is skipped — this is the one place
the ported wording actively invites numbers; the gate is non-optional.

### 3. Channel Identity document — macro_style dials, verbatim voice anchors, anti-horoscope test, `data_confidence`
*(CS2, + CS10 judge context, + SW7 format gate)*

**Assets:** `onboarding-prompt-creator-identity` var `main` (Path B, 7,343 chars — the zero-data
recipe: voice inferred from HOW the user types, "did they type in lowercase? use slang?… Include 2-3
example hook lines") as base; graft `onboarding-prompt-identity-generation` `stage`'s
relevance-assessment + `data_confidence: high|medium|low` (STRONG/PARTIAL/THIN modes, "Do NOT import
content patterns… from irrelevant niches") and `main`'s field specs: `voice_and_tone` ("A downstream
LLM reading this should be able to write a caption, script a line… without ever watching a video.
60-120 words") and the six **macro_style** dials (verbal_primacy, visual_primacy,
content_originality, production_level, methodical_planning, factuality_level). The FAILED IDENTITY
MARKERS block ("Voice section reads like a horoscope: 'authentic, relatable, and engaging'…
THE TEST: Could this describe a different creator in the same niche? If yes, it's too generic.").
Files: `onboarding_flags/onboarding-prompt-creator-identity.txt`,
`onboarding-prompt-identity-generation.txt`; code `identity_generation.py:29-218`,
`creator_identity_generation.py:184-194`.

**Target:** new `channel_identity` JSON on the brand row; inject where the thin brand_block goes
today: `scripts_prompt`, `hooks_prompt`, `converse_system`, `IDEA_GENERATION_SYSTEM`, and
**`script_judge_prompt` CREATOR CONTEXT** (fixes the documented cold-start groundedness gap — the
verbatim voice anchors give voice_match something to measure; macro_style gives format_fit a
target). Add the anti-horoscope TEST line to `pillar_judge_prompt`. SW7's format rule rides here:
"If verbal_primacy is 'low,' the script must contain zero spoken lines… the single most important
formatting rule" + the 4 worked format examples (`script_generation.py:71-116`) — Yunicorn defaults
verbal_primacy high; the win is the faceless/voiceover minority who today get unusable scripts.

**Adaptation:** inputs = quiz answers + onboarding chat + optional pasted references; run
THIN/PARTIAL by default, **persist data_confidence** so every downstream prompt calibrates claims
("a niche-proven mechanism that's untested on them is a real bet, not a sure thing"). Re-run STRONG
on account connect. **Cold start:** this IS the cold-start substrate; honesty is structural
(identity generated from the creator's own words, labeled low-confidence).
**Effort:** M. **Impact:** HIGH — upgrades every write for every user; substrate for items 2, 5, 9.
**Risk:** schema addition + migration; keep the old brand_block as fallback until backfilled.

### 4. Section edit-ops + real steer prompt (highest-frequency user path)
*(SW4)*

**Assets:** the selection-op prompt family keyed on `<START>`/`<END>` markers — all verbatim in
`quote_dump/`: `build-tensions__main.txt` (ON in prod: "delay the payoff of the section to the
end… key details come at the end of each line"), `improve-hook-prompt__main.txt`,
`rephrase__main.txt`, `remove-fluff-prompt__main.txt`, `shorten-prompt2__*.txt` ("target fluff that
doesn't add anything to the narrative first"), `mid-hook-prompt__Model_output_1.txt`
([BEFORE]/[PIVOT]/[IMPACT] pivot-moment micro-format with YES/NO pairs). Shared frame: rule 0
"identify the essence of what the writer is trying to say and preserve that intent" + rule 3
"keeping the current language is better than changing to a less natural or cringe phrasing."

**Target:** `prompts.py::steer_prompt` (:1904 — currently 2 lines, the weakest generation prompt in
the codebase) + `/v1/steer`; wire `{START_TOKEN}`/`{END_TOKEN}` (whole-script steer = section is the
whole body); optionally expose ops as one-tap editor actions (tighten / build tension / better hook /
remove fluff / rephrase).
**Adaptation:** trivial — self-contained, zero channel data (perfect cold start), runs on
Sonnet/Haiku. **Effort:** S. **Impact:** HIGH per cost — steering is what users do most
post-generation and is today the least protected path. **Risk:** none material.

### 5. Scored idea judge + promotion gate + pre-LLM banned-phrase lint + judge anchor examples
*(SG2 + SW8)*

**Assets:**
- `pulse/judge.py:51-88` `_JUDGE_SYSTEM_PROMPT` verbatim (static ⇒ prompt-caches): 4-axis 0-10
  rubric (specificity 0-3 / non_obvious 0-3 / evidence_grounded 0-2 / actionable 0-2) with axis-CAP
  rejections ("hedging → cap specificity at 1"; "overlaps recent_brief_titles → cap non_obvious
  at 0"); "non-obvious for THIS creator."
- Partition at **8.0** from `pulse/ideate_rank.py`: promoted → proactive push; rejected → stay
  browsable in the briefs table. Gate order cheapest-first (from `gate/gate.go`, quoted
  palo-pulse.md §4.4): banned-phrase regexes (`consider|might want to|have you thought`,
  `great|amazing|awesome (job|work)`, `keep it up`, `could be worth`, `interestingly|notably,`) →
  evidence refs → 30d dedup → budget 3/day → quiet hours 21:00–08:00 user-local → semantic dedup →
  LLM judge. Fail OPEN at 7.0 on vendor error; fail CLOSED at 0 on parse garbage.
- SW8: append 2–3 worked scored examples to `script_judge_prompt` (one keep / one revise-for-hook /
  one fabricated) — author them once from Marque's own `_calibration_signal` boundary cases; merge
  the banned-phrase regexes (plus the script prompt's own list: "buckle up", "in this video we'll",
  "without further ado", "this is going to blow your mind") into the deterministic speakability
  lint families (runs first, free).

**Target:** new judge pass over idea-bank nightly output; promoted → feed page-0 + APNs via the
existing `conversation_seed` bridge; budget/quiet-hours as simple columns.
`prompts.py::script_judge_prompt` (:1751) gets the anchors.
**Cold start:** judge context degrades to brand block + niche priors — its main job (killing
hedged/obvious/duplicate cards) needs no history.
**Effort:** S–M. **Impact:** HIGH — converts "we generated stuff" into "we only interrupt with
bangers"; judge anchoring is the known fix for HAIKU drift on the load-bearing quality gate.
**Risk:** push mechanics touch APNs (keys reportedly not on Render — verify before arming the
proactive lane; the in-app promoted lane works regardless).

### 6. THE MIX — programming-rotation prior read by every suggestion surface
*(SG4)*

**Assets:** the MIX paragraph from `strategy-synthesis-prompt` var `use_code_default` (v2.7) →
`flags/strategy-synthesis-prompt__use_code_default.txt`: "Right now ALWAYS ENDS WITH THE MIX — a
rough programming guide for whoever is picking the next video… roughly how often each earns a rep,
in words, never quotas… if the last few videos were one type, the next one leans to whatever is
under-served." Plus the mix-check step (§1 of `prompts/offline-idea-prompt__stage.txt`) and the
lane-inventory anti-monoculture rule (`prompts/offline-orchestrator-prompt__stage.txt` rule 4:
"commissioning a second entrant into a lane whose first entrant sits unbuilt is the monoculture
failure wearing variety's clothes").

**Target:** `app/palo_prompts.py::_STRATEGY_SYNTH_INSTRUCTIONS` (:258); plus a code-computed
mini-MIX (pillar × recent posts × unconsumed briefs → "what's under-served") injected into
`next_idea_prompt`, the feed picker, and idea generation — 80% of the value with no strategy
compiler armed. **Cold start:** degrades to pillar rotation over onboarding pillars — what the feed
does today, but now STATED to the model. **Effort:** S. **Impact:** HIGH for its size — kills the
"5 variants of the same video" failure directly. **Risk:** none.

### 7. Sketch → Idea bake-off funnel (replace the spitfire chain) + card anatomy
*(SG1 + SG8)*

**Assets:**
- LD `offline-sketch-prompt` var **`stage`** (`a5a-sketch-ideator v3.0`) →
  `prompts/offline-sketch-prompt__stage.txt`: tool-less Sonnet, ~10 sketches "genuinely different AT
  THE ENGINE LEVEL", RUBRIC OFF ("judging is not your pass"), one mandatory longshot, never blocks.
- LD `offline-idea-prompt` var **`stage`** (`a5b-idea-generator v3.4`) →
  `prompts/offline-idea-prompt__stage.txt`: mix-check → 3 ENGINE-level rivals → judge (payoff test,
  collision check) → flesh out ONLY the winner; `emit_idea{title, concept, pitch, sources, brief}`
  with the bake-off receipt in the brief. Two-reader anatomy: pitch ≤30 words creator-facing
  ("sentence one is Palo's read — why this idea, NOW… NO numbers-speak"), brief ≤60 words internal
  handoff. "PAINT, DON'T THEORIZE"; hook and payoff always real.
- Code armor from `offline/generators.py:714-1030` + :1868-1888: vocab-leak firewall (multipliers
  always; baseline/median/lift only with a digit within 30 chars — bare-word matching
  false-positived on "lift the pallet"); `_SCAFFOLD_RE` discard-never-repair; retry-once "REJECTED —
  rewrite in plain creator language… keep everything else identical"; **NO FALLBACK COPY** ("a
  missing pick is a quiet miss; internal prose on the card is a trust break");
  `_find_duplicate_project` token-containment ≥0.6 dedup; `offline/sketch.py` parse salvage.

**Target:** replaces `app/ideas.py` spitfire Generator→Critic→Editor→Ranker (the chain Palo itself
retired — do NOT polish it). Extend `briefs` schema with pitch/brief/sources; feed renders pitch,
write-from-brief consumes brief+beats. Embed Marque's already-ported doctrine v1.4 at the prompt
tail as Palo does. Serve via prompt_store keys.
**Adaptation:** run zero web queries (the prompt degrades; Perplexity optional later). **Cold
start:** sketchbook absent ⇒ idea pass drafts its own 3 rivals from identity + niche priors — the
designed lane, and exactly Yunicorn's day-1 population.
**Effort:** M. **Impact:** HIGH — the single biggest quality delta for "what to post next"; every
card gets a defensible "why this, not that." **Risk:** more tokens per idea run (two passes) —
bound with per-tier cadence; the no-fallback rule means occasional empty runs (by design; keep
`mock_ideas` only for the never-break feed paint, clearly `is_template`).

### 8. Output contract + emission armor — DURATION_SECONDS sentinel, plain-text scripts, Sonnet-5 gotchas
*(SW6 ≡ SG12)*

**Assets:** `pulse/script.py:119-155` — long creative output NEVER travels as JSON:
`DURATION_SECONDS: <int>` + blank line + markdown script (regex
`^\s*DURATION_SECONDS:\s*(\d+)\s*$`), JSON only as legacy fallback ("a format slip degrades to a
usable draft rather than an empty one"). Rule 9: duration is an honest measurement "from the spoken
words at this channel's real pace." Armor (all bug-derived): `thinking={"type":"disabled"}` on
fixed-format emissions (burned the ENTIRE output budget on thinking, zero text, at 768 AND 2048
max_tokens); text-block-not-`content[0]` extraction; forced-tool emits with `strict:true` +
`parallel_tool_calls=False` + beta header `structured-outputs-2025-11-13`; **`brief` defined LAST in
schema** (constrained decoding walks definition order — brief-first burned the slot with filler);
truncation-is-a-reject retry (`orchestrator.py:497-529`); `_fit_package` degrade-never-slice;
word-boundary cuts; parse the live contract AND the previous one.

**Target:** script generation path in `main.py` + `palo_llm.py` (put the thinking/extraction gotchas
in BEFORE any model bump — Marque is still on Opus 4-8/Sonnet 4-6 so it hasn't bitten yet). Keep
JSON at the API boundary: generate sentinel-plain-text, wrap server-side. Display "≈45s as written"
on the script card; feed duration to the judge as a length-fit signal.
**Cold start:** pace yardstick falls back to per-niche WPM constant. **Effort:** S. **Impact:**
MED-HIGH — kills an entire escaping/truncation failure class, ships a user-visible duration feature,
future-proofs the model upgrade. **Risk:** none; pure hardening.

### 9. First-script teach-back — `{script, reasoning}` contract + tutorial pregen + `<script_quality>` bar + VIEWER-SEAT insert
*(SW10 ≡ CS4)*

**Assets:** `onboarding-prompt-script-generation` var `main` →
`quote_dump/onboarding-prompt-script-generation__main.txt`: two-field output — script + `reasoning`
("Write it like you're briefing a colleague… This gets passed to the tutorial so it can teach the
creator WHY each part was built this way"); the RETENTION STRUCTURE block
(PROMISE / CONFIRMATION WINDOW "the highest-leverage segment… Strong videos lose 5-10% here. Weak
ones lose 20-30%" / CONTINUATION "ESCALATION, not just progression… Progress alone does not retain
viewers. Anticipation does" / PAYOFF "End decisively… No epilogue, no recap") + THE FILLER CUT
("A 25-second script where every line hits is better than 45 seconds with filler").
`prompts/write-tutorial-fill-prompt__main.txt` (16k, ON): the `<script_quality>` bar of THREE
complete reference scripts ("Every line IS the content… A creator can open their editor and build
the video using ONLY the script. No guessing") + the 3-check self-critique (VOICE MATCH / VIEWER
SEAT — "read the script as a random viewer scrolling their feed at 2am" / SECTION SCORING).
`tutorial_pregen.py:51-204`: exact-substring highlight contract ("highlight_text MUST be an exact
substring… highlight fewer lines rather than risk a mismatch"), deterministic replay from a stored
shadow blob (zero LLM cost, zero drift), shared-ownership language ("the script, not your script").

**Target:** `app/palo_prompts.py::SCRIPT_FROM_BRIEF_SYSTEM` (:339, ~10 lines today); add `reasoning`
as a 13th internal field on SCRIPT_SCHEMA; tutorial steps map onto Marque's existing script editor
via the substring-highlight trick. **Quick win inside this item:** the VIEWER-SEAT self-critique
paragraph is a free S-size insert into `scripts_prompt` TODAY for all scripts.
**Adaptation:** swap tiptap-HTML for SCRIPT_SCHEMA body; drop Palo's 4-format selection
(talking-head = voiceover always, keep the not-on-camera branch); author three Yunicorn talking-head
reference scripts (story / educational / contrarian) to the same standard.
**Cold start:** designed for zero-state — inputs are chosen idea (item 2) + channel_identity
(item 3). **Effort:** S (prompt + reasoning field) / M (tutorial UI). **Impact:** MED-HIGH for
conversion — the first script becomes a taught experience. **Risk:** tutorial is client work
(TestFlight-gated for native surface — schedule accordingly).

### 10. Cold-chat honesty + wow bundle — identity-only mode, MODE 2 direction cards, metadata first-read, anti-target catalog sheet
*(CS8 + CS3-phase-1 + CS7)*

**Assets (all S-effort, one theme: honest and confident at zero data):**
- **Identity-only mode** (`interaction-agent-identity-only-mode`; agent.py:62-69, 1362-1387):
  "USE the identity to fulfill requests… Do NOT say you lack context or ask what they make." +
  the bouncer's epistemic boundary (`onboarding_flags/mobile-onboarding-interaction-bouncer.txt`):
  "Your knowledge comes from their NICHE, not their catalog. Frame it that way: 'in your space' not
  'in your content'… If they ask about their analytics: be honest. You haven't analyzed your
  content yet… Don't say 'still processing' as if it's happening in the background. It's not."
  → append to `converse_system` + `scripts_prompt` when `settled_posts == 0` and channel_identity
  exists; the in-your-space register permanently for pre-connection users.
- **MODE 2 direction cards** (`onboarding_flags/onboarding-prompt-direction-options.txt` main): run
  in permanent MODE 2 (no corpus) — 3-4 format-based lanes post-quiz, written to the
  "video you'd recognize while scrolling" spec (GOOD: "Quick tips to camera, one concept per video,
  casual proof that it works"; BAD: "Educational content featuring step-by-step breakdowns —
  category label, not a video"; labels <15 words), with the honest recommendation_reason ("I'm
  recommending based on what formats work for this type of content, rather than specific creators
  in your space"). Chosen lane → `channel_identity.selected_direction`, steers ideas + STYLES.
- **Metadata-only first read** (`text_onboard/read.py::_PROMPT`, whole, quoted in gaps-coldstart §7):
  ONE Sonnet call over ≤40 title/views/date rows the moment PFM/Apify connect lands, BEFORE analysis
  finishes — "You have NOT watched any videos — never claim you did… You 'went through' their
  channel"; thin-catalog branch included. Cheap honest wow.
- **Anti-target rule + catalog sheet** (`established_idea_generation.py` + the `generate_ideas`
  skill paragraph): "The creator has already published these videos — DO NOT REPLICATE THEM…
  same engine, new destination"; last-50-titles resident sheet (~3KB) into
  `IDEA_GENERATION_SYSTEM` + `next_idea_prompt` for any user with ≥1 real post — "repeating a
  creator's own catalog back to them as a fresh idea destroys trust instantly."

**Effort:** S + S + S + S. **Impact:** MED-HIGH aggregate — fixes the whole class of day-1 chat
failures ("I lack context" deflection), replaces "pick a pillar" abstraction with lanes a creator
can picture filming, and makes account-connect feel instantly worth it. **Risk:** none; every piece
is honesty-by-construction.

---

## NEXT TIER (do after the ten; ordered)

11. **a4-insights card writer** (SG5): `prompts/offline-publication-prompt__stage.txt` (35KB,
    served) replaces `INSIGHT_DISCOVERY_SYSTEM` copy layer — headline "THE MECHANISM, THE NUMBER,
    THE LEAN"; EXACTLY ONE MOVE "about 30 words… one thing they can do with a camera"; "THE CLAIM
    WEARS ITS SAMPLE SIZE"; numbers-as-speech ("7x", never "7.39x"); 10-headline gallery + 7-failure
    anti-gallery; `_gen_insight` guards (hidden-kind evidence never creator-facing). Detection stays
    Marque's deterministic layer. S–M, HIGH — but only fires for users WITH posts, hence below the
    cold-start ten.
12. **Decider + Today briefing** (SG3): `pulse/decide.py:42-105` (code fallback IS prod) — diagnosis
    → RIGHT response type (execution leaked → review, not more ideas), `_DEST` forced routing,
    `{"decisions": []}` silent-day degrade, provenance {noticed → diagnosis → action}; vitals
    thresholds (decisive_negative "0 of n≥5 beat baseline — retire, don't retest"; posting_gap =
    max(4, usual_gap×2)). M, HIGH once users have signal.
13. **Engagement feedback loop + soft-no** (SG6): outbox table (shown/opened/saved/dismissed),
    3-tier engagement policy ("engaged earns more ideas; ignoring earns fewer, better ones"),
    soft-no with the looking-guard ("zero card opens ⇒ untouched projects are undelivered mail, not
    declined ideas"). M, HIGH — compounds everything.
14. **Corrective-error self-repair** (SW9): tool-error strings the model sees but the user never
    does ("the tool result IS the repair prompt"), one bounded retry — replaces `_guard_write_actions`
    surfacing failures to the USER. S, MED now / HIGH when strategy-injection arms.
15. **Best-practices named pattern library** (CS6): `onboarding_flags/onboarding-prompt-best-practices.txt`
    — ≥10 named mechanics ("The Impossible State Opening"), brief/full two-tier, examples_for_creator
    doubles as an idea reservoir. M, MED-HIGH.
16. **Morning brief + comms tone bible** (SG10): `prompts/proactive-daily-prompt__stage.txt` —
    comms "NEVER a rewriter… reuses copy verbatim, only decides WHAT the user gets"; empty body =
    honest silence; weekly `_PERF_LABELS` + BAD/GOOD pair. S–M, MED.
17. **Loading theater** (CS9): `onboarding-prompt-strategy-loading-steps` prod — 12-15 first-person
    personalized steps for the three wait moments; add "never claim to be analyzing videos that
    aren't being analyzed." S, MED.
18. **write-agent v3.3 full port** (SW5): `prompts/agent-write-prompt__Treatment_1.txt` (35KB) into
    `WRITE_AGENT_SYSTEM` — mode detection, reply envelope, 8-item self_audit, honest degrade strings
    per slot. M — only worth it alongside deciding to ARM the flag (open Q1).
19. **Conversational onboarding graft** (CS5): base persona + routing + field-check + niche-discovery
    QUALITY CHECK flags (all in `onboarding_flags/`); `_clean_chat_history` `[selected: Label]`
    rewrite (Marque's chip chat likely has the same bug). L, HIGH for identity richness.
20. **Nightly factory harness, scaled down** (SG11): artifact floor ("this run must carry at least
    one artifact action — unconditionally"), findings ledger with TTL, commission-don't-compose,
    EVIDENCE/SIGNAL/CONTEXT provenance. L — the chassis; do after 5/7/12. Cost anchor: 177s
    avg/channel/night.
21. **Outcome predictor** (SG7): own-history pairwise ranker 68.2% vs Claude zero-shot 49.3% (coin
    flip); idea-text-only 59.4% ⇒ pre-production reranking; 24h-velocity breakout 95.3%. L,
    MED-HIGH — the only item with measured outcome signal; phase pooled → per-channel at ≥50 pairs.
22. **Exemplar bank v2.5** (SW13): bank as TARGET not mirror ("assume their own {primitive}s are
    WEAK… the star of each pattern is the improved version"); all-experiment modeled bank is the
    designed cold path. L, HIGH long-term — gated on arming the strategy brain. **Do now regardless
    (one line):** fix `exemplar.py`'s question-opener template seed that contradicts
    VIRALITY_BLOCK's "question-openers underperform statements."

---

## DO NOT PORT

- **Spitfire chain polish** (Critic/Editor/Ranker prompt upgrades) — Palo abandoned the chain;
  replace it (item 7), don't improve it.
- **Pinecone voice-centroid scorer** — Palo retired it; gen-time few-shot conditioning transfers
  voice better (item 1).
- **Palo's offline LLM judge** — OFF in Palo prod ("4o mini doing more harm than good"); quality is
  held by orchestrator + code invariants. Use item 5's judge on ideas only.
- **View-magnitude prediction** — tested NEGATIVE in Palo's own predictor work; only pairwise
  ranking carries signal.
- **PROOF LINE with view counts, as-is** — direct honesty-rule conflict until a real exemplar corpus
  with per-video counts exists; ship the numbers-free variant (item 2 gate).
- **Trends / NICHE-RIGHT-NOW surface** — needs an exemplar-channel index Marque doesn't have;
  Marque's no-mock-trends stance is correct. Revisit after a corpus exists.
- **LaunchDarkly plumbing** — prompt_store already covers override-without-deploy; porting LD adds
  a vendor for nothing.
- **Mobile/iMessage delivery layer** (text_onboard transport, comms-imessage) — wrong surface; only
  the read.py PROMPT ports (item 10).
- **Bouncer sales agents** — Yunicorn's trial-equals-full-access model differs; only the epistemic
  boundary rules were extracted (item 10).
- **Four-format selection wholesale** — Yunicorn is talking-head-first; keep only the
  verbal_primacy gate + not-on-camera branch (item 3).
- **Tiptap-HTML script format** — Marque's SCRIPT_SCHEMA + speakability lint stay; port wording,
  not the format.
- **Palo replacements for Marque's parity-or-better machinery** — speakability lint + HAIKU repair,
  GROUNDING_BLOCK + fabricated=true detection, best-of-N hooks with judge re-score, verbatim-lift
  acceptance checks, silence-on-no-signal coach gates, IDEA_EVAL niche gate. Keep all.
- **Perplexity query execution** — no key armed; both ideation prompts degrade to zero queries by
  design.
- **Full sense-window + o1 orchestrator on day one** — the factory chassis (item 20) before items
  5/7/12 is cart-before-horse; Marque's dossier_adapter is the proto-window.

---

## OPEN QUESTIONS FOR OWNER

1. **Arm IDEA_BANK and WRITE_AGENT?** Items 2, 7, 18 only reach users if these flags arm (or their
   chains move into the mainline feed path). Which is the intent — arm for new users first, or
   mainline the code paths and delete the flags?
2. **Build the exemplar corpus?** Persisting Apify-scraped top-post analyses into pgvector
   (memory_v2 already uses it) unlocks MODE 1 retrieval, real PROOF LINES, and eventually the trends
   surface. It's the single decision gating three "do not port (yet)" items. Appetite for the L?
3. **Proactive push lane:** promoted-idea APNs needs APNS_* keys on Render (memory says missing) and
   a decision on defaults — Palo's are 3/day budget, 21:00–08:00 user-local quiet hours. Approve
   those defaults?
4. **Outcome predictor (L):** its 49.3% zero-shot result argues for STOPPING further LLM-score
   ranking refinement. Invest in the pairwise ranker now, or park and just freeze `_final_score`
   work?
5. **Onboarding rebuild appetite:** item 19 (conversational graft) is L and touches the 17-step
   flow + paywall sequencing. In or out for this cycle?
6. **Model bump timing:** item 8's Sonnet-5 armor is prophylactic today (Marque on Opus 4-8 /
   Sonnet 4-6). If a bump is planned soon, pull item 8 to the front of week 1.
7. **Tutorial surface:** item 9's teach-back needs native editor work ⇒ TestFlight build. Bundle
   with the next planned iOS build (b71+?) or hold server-side `reasoning` capture until then?

## SEQUENCING (suggested)

- **Week 1 (all S, no schema, no new calls):** item 4 (steer/edit-ops), item 8 (output contract +
  armor), item 5's regex lint + judge anchors, item 10's identity-only mode + anti-target,
  item 9's VIEWER-SEAT insert, the exemplar template-contradiction one-liner.
- **Week 2–3:** item 1 (combined writer revision behind prompt_store), item 2 (idea prompt swap +
  proof gate), item 6 (MIX), item 10's MODE 2 cards + metadata read.
- **Then:** item 3 (identity doc — unlocks judge context + format gate), item 5's promotion
  gate/push lane, item 7 (bake-off), item 9 full tutorial.
- **Strategic (needs owner sign-off):** items 11–22 per the open questions.
