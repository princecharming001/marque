# GAPS — SCRIPT QUALITY lens (idea → finished spoken script)

Scope: outline stages, voice/exemplar injection, format decisions, judge/critique passes, output
contracts, banned-phrase lists, duration control, hooks. Sources: all nine palo_analysis reports +
`ld_flags_all.json`. Marque baselines from `reports/marque-current.md` (file:line refs are to
`/Users/home/Marque/backend/prompts.py` and `main.py` unless noted). Idea-generation/suggestion
items are deliberately excluded (other lens); only the writing chain is covered here.

**The one-sentence diagnosis:** Marque's writer is a single-shot Opus call (brand → finished
12-field JSON script) with a good after-the-fact judge; Palo's writer is a staged chain
(structure decided → words written) with the creator's best videos as an in-prompt voiceprint,
plain-text output contracts, and worked right/wrong few-shots — and every one of those stages is
a self-contained prompt Marque can lift verbatim.

**Where Marque is already at parity or better (do NOT port):** the deterministic speakability
lint + HAIKU repair (prompts.py:2697–2796, `_SPEAKABLE_REPAIR_SYS` main.py:10766) is better than
anything in Palo; `GROUNDING_BLOCK` + `fabricated=true` judge detection is at parity with Palo's
grounding rules (Palo's phrasing is prettier, the mechanism is the same); best-of-N hooks with a
judge re-score has no Palo equivalent (Palo bets on the exemplar bank instead — keep both);
`script_revise_prompt`'s "fix only the named axis, do not blandify" is sound.

---

## Ranked portable upgrades

### 1. The a5 script-writer prompt — replace the core writing wording
- **What Palo does:** `pulse-script-prompt` variation **"stage"** = `a5-script-generator v1`
  (SERVED in prod per ld-map; full verbatim in `reports/palo-pulse.md` Appendix A and
  `prompts/pulse-script-prompt__stage.txt`). The 10 rules are the best tested statement of
  spoken-script craft in the codebase: THE FIRST LINE IS THE VIDEO (mid-action/consequence-first,
  never setup, never the payoff), EVERY LINE EARNS THE NEXT (re-hooks planted through the body; "a
  line that could end the video before the end is a line to rewrite"), THE PAYOFF LANDS LAST
  (callback close, "then stop — no recap"), WRITE WITH THE CREATOR'S MOUTH (the read-aloud test:
  "if any line sounds like writing instead of this creator talking, it fails"), SHOW DON'T NARRATE,
  EXPLAIN THE PROP, GROUNDED STILL, DURATION IS A MEASUREMENT, vocabulary firewall — plus a full
  worked RIGHT script (NBA Finals ladder) and a WRONG script annotated failure-by-failure ("Little
  did I know… is written language no creator speaks"; "'Over 20,000 passionate fans' is printed
  nowhere in the package — an invented specific, the worst failure").
- **Marque today:** `scripts_prompt` (prompts.py:1676) — strong context assembly but the craft
  instruction is VIRALITY_BLOCK bullets; no read-aloud test, no worked wrong-example, no
  first-line/payoff-last rules at this sharpness. `SCRIPT_FROM_BRIEF_SYSTEM`
  (app/palo_prompts.py:339) is ~10 lines.
- **Asset to copy:** flag `pulse-script-prompt` / variation `stage`, verbatim — role, objective,
  rules 2–10, both examples, the reminder block.
- **Adaptation:** swap the `<inputs>` package for Marque's blocks (brand_block, real posts,
  emulation, memory, niche priors). Cold start: keep Palo's own degrade — with no posts, the
  voiceprint slot becomes an instruction ("lean on the brand voice sliders + niche priors"), rules
  still apply. No LD: put it behind Marque's `prompt_store` key so it iterates without deploys
  (mainline prompts currently have NO override hook — fix that while porting, see #12).
- **Effort:** S–M (prompt swap + input-slot mapping). **Impact: HIGH** — this is the tested
  wording for exactly Yunicorn's product (talking-head spoken scripts), and the wrong-example
  few-shot targets Marque's observed failure classes (stage directions, invented specifics,
  essay-register lines) at generation time instead of only at lint time.

### 2. A structure stage before words — outline/moment-board (or at minimum the `<planning>` block)
- **What Palo does:** structure and words are SEPARATE passes everywhere. Offline/pulse: brief →
  outline (`pulse-outline-prompt` "stage" = `a6a-outline-generator v2.2`: 5–8 MOMENTs with earned
  labels Hook·Setup·Tension·Escalation·Conflict·Twist·Payoff, "PLAN BACKWARDS FROM THE PAYOFF",
  "NO SATISFYING EXITS — a beat that feels complete is a scroll-away point", "three beats of the
  same stakes is progression, and progression loses people", moments-are-beats split/merge
  doctrine) → script (rule 1: "THE RECORD IS THE MAP… you write them, you don't restructure
  them"). Interactive write agent: the mandatory `<planning>` block (write-agent v3.3, LD
  `agent-write-prompt` / "Treatment 1"): ~150 words, THE READ (what the video is + the question the
  hook opens) → THE RUNGS (one line per beat) → THE SELF-CHECK ("walk the rungs as a viewer. After
  each reveal, what are they still waiting for? If the answer is ever 'nothing'… fix the structure
  NOW. Structure problems get solved here, never mid-write") → the yardstick line (target length +
  register). Rendered as a user-visible thinking dropdown — reasoning becomes UX, with API thinking
  pinned LOW (measured 11.5s → 2.4s to first output).
- **Marque today:** NO beat stage anywhere in the mainline. `scripts_prompt` goes brand→finished
  script in one shot (marque-current.md weak point #1). The idea-bank brief
  (beginning/middle/end) exists but is flag-dark and not what /v1/scripts or the feed run.
- **Asset to copy:** minimum viable = the `<planning>` contract paragraph from
  `prompts/agent-write-prompt__Treatment_1.txt` inserted into `scripts_prompt` (emit `<planning>`
  before the JSON/script, strip or surface it). Full version = the a6a outline prompt as an
  internal stage for the background quality pass (`_refresh_feed_page` and `/v1/scripts`), with
  the moment-board plain-text parse regexes from `pulse/outline.py:36-45`.
- **Adaptation:** planning-block version costs one prompt edit and ~150 output tokens; zero new
  calls. Outline-stage version adds one Sonnet call — do it only on the background/full pipeline,
  never the HAIKU fast paint. Cold start: both work from brand + niche priors alone (the a6a
  prompt's own rule: concept settled, structure derived from craft rules, no channel data needed).
- **Effort:** S (planning block) / M (outline stage). **Impact: HIGH.** This is the single
  biggest structural gap: Marque's judge catches slop after the fact; nothing forces
  open-loop → escalation → decisive-payoff shape up front. Palo litigated this exact question and
  landed on "shape first, material second, words last" in three independent prompts.

### 3. Voice exemplars as an in-prompt voiceprint (not just opening lines)
- **What Palo does:** top-6 best videos rendered as compact per-video style notes — bucket, theme,
  hook, summary, structure, payoff, verbatim opener, visual_composition, audio_profile — cached as
  their own system block (`pulse/script.py::_render_exemplars`, format in palo-pulse.md §3), with
  the framing "THE PLAYBOOK's member video is the voiceprint: study its verbatim opener, its
  sentence lengths, its lexicon, how its closer actually lands, then write THIS script in that
  hand. Mimic the shape, never lift the line." Two hard-won lessons attached: (a) a
  Pinecone-centroid voice *scorer* was retired — "the few-shot conditioning at gen-time is what
  actually transfers the creator's voice"; (b) transcripts teach VOICE, never content — "a new
  script that retells an old video's story is a failure even when every sentence sounds like them"
  (write v3.3).
- **Marque today:** `_voice_exemplars` (prompts.py:1551) quotes literal opening lines of best
  posts — good but one-dimensional; up to 20 raw posts also ride in the user message
  (undistilled, expensive, and content-contamination-prone — the RETELL failure has no guard).
- **Asset to copy:** the `_render_exemplars` block shape + the rule-5 voiceprint paragraph + the
  transcripts-are-voice-not-content warning + the DIALOGUE-vs-SHOT format decision delegated to
  the model from exemplar field density (`_SCRIPT_SYSTEM` in `pulse/script.py:38-70`).
- **Adaptation:** Marque already stores per-post transcripts and captions; a ~40-line adapter
  builds the block from settled posts sorted by views. Cold start: block omitted entirely (Palo's
  behavior) + the instruction fill "No example scripts available — lean on the brand voice and
  niche priors" (write.py:1172-1188 pattern). Zero accounts ⇒ voice comes from the ElevenLabs
  interview/brand sliders as today.
- **Effort:** S–M. **Impact: HIGH** — voice fidelity is Marque's judge axis with the least
  generation-side support, and this is the cheapest, empirically-validated way to move it.

### 4. Section edit-ops + a real steer prompt (the highest-frequency user path)
- **What Palo does:** small battle-tested selection-op prompts keyed on `<START>`/`<END>` markers:
  `build-tensions` (main, ON — "delay the payoff of the section to the end… key details come at
  the end of each line", rules 0–4 verbatim in ld-map §12-15), `improve-hook-prompt`, `rephrase`,
  `remove-fluff-prompt`, `shorten-prompt2` ("target fluff that doesn't add anything to the
  narrative first"), and the striking `mid-hook-prompt` "Model output 1" ([BEFORE]/[PIVOT]/[IMPACT]
  pivot-moment micro-format with YES/NO pairs, `quote_dump/mid-hook-prompt__Model_output_1.txt`).
  All share rule 0: "identify the essence of what the writer is trying to say and preserve that
  intent" and rule 3's natural-spoken-language guard ("keeping the current language is better than
  changing to a less natural or cringe phrasing").
- **Marque today:** `steer_prompt` (prompts.py:1904) is 2 lines — no virality block, no grounding,
  no judge; only the speakable floor. Explicitly the weakest generation prompt in the codebase
  (marque-current.md §2 + weak point #6).
- **Asset to copy:** the shared 0–4 rule frame + per-op mission lines, verbatim; wire the
  `{START_TOKEN}`/`{END_TOKEN}` selection convention into /v1/steer (whole-script steer = section
  is the whole body) and optionally expose the ops as one-tap actions in the editor.
- **Adaptation:** trivial — these prompts are self-contained, need zero channel data (perfect cold
  start), and run fine on Sonnet/Haiku.
- **Effort:** S. **Impact: HIGH** relative to cost — steering is what users do most after
  generation, and today it's the least protected path.

### 5. write-agent v3.3 for the co-writing surface (/v1/write/turn)
- **What Palo does:** LD `agent-write-prompt` / "Treatment 1" (34,981 chars; full text
  `prompts/agent-write-prompt__Treatment_1.txt`): mode detection (empty script ⇒ FILL, else
  EDIT/ANSWER/DISCOVERY — "revision language on an empty canvas = a fresh FILL, never an edit"),
  the reply envelope (read → script → conclusion, "two or three lines at most"), the reasoning
  chain (doctrine → strategy → grounded-in-real → this video), payoff-first grounding ("you cannot
  write a script whose resolution you don't know"), the 8-item self_audit (FABRICATED SPECIFICS /
  VOCAB LEAK / WRONG old_text / VOICE FROM NOWHERE / THE RETELL / SHAPE DECISIONS MID-WRITE /
  LENGTH DRIFT / HANDING THE WORK BACK), the opener-dedup rule backed by code (last-5 opening
  lines injected: "your draft's first line never duplicates one of these openers"), and full
  worked FILL + EDIT examples that set the register.
- **Marque today:** `WRITE_AGENT_SYSTEM` (app/palo_prompts.py:324) — a correct but bare ~15-line
  contract (actions + exact-substring + ≤250 words). It descends from the pre-v3 generation.
- **Asset to copy:** the whole Treatment 1 file; keep Marque's typed `<fill>/<edit>/<add>/<answer>`
  action grammar (it matches), drop the tool-calling sections, keep `_guard_write_actions` and add
  the corrective-error retry pattern (#9).
- **Adaptation:** the {STRATEGY_*}/{EXEMPLAR_BANK}/{ANALYTICS_SNAPSHOT} slots must degrade to
  Marque's honest markers — Palo's own cold-start fills are quoted in palo-writers.md §6 and are
  the right strings ("(No compiled strategy doc yet for this creator.)" etc.). Note WRITE_AGENT is
  flag-dark today — porting the prompt is only worth it alongside deciding to arm the flag.
- **Effort:** M. **Impact: HIGH where armed / med while dark.**

### 6. Plain-text output contracts + DURATION_SECONDS (kill the script-in-JSON failure class)
- **What Palo does:** long creative output NEVER travels as JSON. Script pass emits
  `DURATION_SECONDS: <int>` + blank line + markdown script (regex
  `^\s*DURATION_SECONDS:\s*(\d+)\s*$`, JSON only as legacy fallback, "a format slip degrades to a
  usable draft rather than an empty one" — `pulse/script.py:119-155`). Outline emits the labeled
  MOMENT layout. Explicit rationale in-prompt: "This avoids escaping a long script inside JSON."
  Rule 9 makes duration an honest measurement "from the spoken words at this channel's real pace…
  the playbook's member videos are the yardstick." Plus the two Sonnet-5 emission gotchas
  live-debugged in Palo: `thinking={"type":"disabled"}` on fixed-format emissions (it burned the
  ENTIRE output budget on thinking, zero text, at 768 AND 2048 max_tokens) and text-block-not-
  `content[0]` extraction.
- **Marque today:** 12-field `SCRIPT_JSON_ELEMENT` JSON for every script; no duration estimate
  anywhere (users film blind on length; the judge can't check length fit beyond word count).
- **Asset to copy:** the sentinel-line contract + parser + fallback ladder; the duration rule 9
  sentence; the thinking/extraction gotchas into `palo_llm.py` before any model bump.
- **Adaptation:** keep JSON at the API boundary (client contract) — generate script body as
  sentinel-plain-text, wrap server-side. Duration: display as "≈45s as written" on the script
  card; feed it back to the judge as a length-fit signal. Cold start: pace yardstick falls back to
  a WPM constant per niche prior.
- **Effort:** S. **Impact: MED-HIGH** — eliminates an entire escaping/truncation failure class,
  adds a user-visible duration feature, and future-proofs the model upgrade.

### 7. Format decision by verbal/visual primacy (the "single most important formatting rule")
- **What Palo does:** `onboarding-prompt-script-generation` (LD main, live) selects among four
  concrete formats from `macro_style`: PURE VOICEOVER / PURE VISUAL (zero spoken lines, bold
  overlays) / MIXED / DIALOGUE — each with a full worked tiptap example — and states: "If
  verbal_primacy is 'low,' the script must contain zero spoken lines. A voiceover script for a
  visual-only creator makes it unusable. This is the single most important formatting rule."
  The runtime writer makes the same call from exemplar field density (§3).
- **Marque today:** 7 STYLE rubrics with exemplars — good, but nothing gates spoken-vs-visual on
  the creator's actual mode; a faceless/visual creator gets a talking-head script.
- **Asset to copy:** the format-selection section + the 4 worked format examples
  (script_generation.py:71-116); add a `verbal_primacy`/`on_camera` dial to Marque's brand block
  (one onboarding question or derived from posts).
- **Adaptation:** Yunicorn is talking-head-first, so default `verbal_primacy: high` — the win is
  the minority of faceless/voiceover/visual creators who today get unusable scripts. Cold start:
  ask in onboarding (one tap).
- **Effort:** S–M. **Impact: MED** (high for the affected segment).

### 8. Judge upgrades: anchored examples, axis-caps, and a pre-LLM banned-phrase gate
- **What Palo does:** (a) every serious Palo judge/writer ships worked right/wrong examples — the
  a5 wrong-example, the o2 judge's "the load-bearing number appears nowhere in its spans"
  canonical fail, the pulse judge's axis-CAP rejections ("hedging → cap specificity at 1";
  "restatement → cap non_obvious at 1"); (b) the cheapest gate is a code regex BEFORE any LLM
  (gate.go: `consider|might want to|have you thought`, `great|amazing|awesome (job|work)`,
  `keep it up`, `could be worth`, `interestingly|notably,`) plus the script prompt's own banned
  phrasings ("buckle up", "in this video we'll", "without further ado", "this is going to blow
  your mind"); (c) `all_scores` (main, ON) is a consistency-first 3-axis rubric Marque already
  ported.
- **Marque today:** `script_judge_prompt` (prompts.py:1751) has thresholds + slop lists but ZERO
  worked scoring examples (weak point #8 — HAIKU with no anchors drifts); slop detection is
  LLM-only; the pre-film scorer and the pipeline judge use different vocabularies.
- **Asset to copy:** 2–3 scored example scripts (one keep / one revise-for-hook / one fabricated)
  appended to `script_judge_prompt`; the axis-cap phrasing style; the banned-phrase regex list
  merged into Marque's speakability lint families (deterministic, runs first, free).
- **Adaptation:** write the anchor examples once from Marque's own judged corpus (it has
  `_calibration_signal` data to pick real boundary cases). Cold start: unaffected.
- **Effort:** S. **Impact: MED-HIGH** — judge consistency is the load-bearing wall of Marque's
  whole quality-gate architecture; anchoring is the known fix.

### 9. Corrective-error self-repair + emit lints (invisible retries)
- **What Palo does:** tool/op validation failures return `"Error: …"` messages the MODEL sees but
  the user never does — the stream filter drops them and the model retries correctly
  (`write_pyro/tools/script_tools.py`: `_EMPTY_SCRIPT_ERROR`, `_NESTED_FILL_ERROR` — "the tool
  result IS the repair prompt"). Plus emit lints with one bounded retry: vocab-leak firewall in
  CODE (multipliers always; baseline/median/lift only with a digit within 30 chars — bare-word
  matching false-positived on "lift the pallet"), scaffold detection (`</antml`, `<parameter`,
  "Let me redo th" — discard, never repair), pick-the-LAST-clean-call among multiple emits.
- **Marque today:** `_guard_write_actions` converts a dirty `<fill>` into an `<answer>` asking the
  user for the exact line — i.e., the failure is surfaced to the USER instead of self-healed.
  `check_invariants` exists but no retry loop, no digit-window firewall, no scaffold guard.
- **Asset to copy:** the corrective-error strings + one-retry loop into write_agent op validation;
  `_vocab_leaks`/`_scrub_vocab_leaks`/`_SCAFFOLD_RE` from `offline/generators.py:751-827` into the
  script pipeline (scripts are creator-facing; a leaked "2.3x baseline" in a body line is a trust
  break Marque currently has no guard against once the brain flags arm).
- **Effort:** S. **Impact: MED** now, HIGH the day strategy/exemplar injection arms (that's when
  internal vocabulary starts existing in-context to leak).

### 10. First-script moment: {script, reasoning} contract + tutorial walkthrough + the quality bar
- **What Palo does:** the onboarding script is framed as "the single most important output… If
  it's good, they pay $80/month. If it's generic, they leave." Contract = exactly two fields:
  the script + a `reasoning` field ("Write it like you're briefing a colleague… This gets passed
  to the tutorial so it can teach the creator WHY each part was built this way"), which powers a
  deterministic step-by-step walkthrough with exact-substring highlights (tutorial_pregen — hook +
  payoff always taught, "escalation ≠ twist", shared-ownership language "the script, not your
  script"). `write-tutorial-fill-prompt` (ON, 16k) adds a `<script_quality>` bar of THREE complete
  reference scripts (chat story / explainer / skit) with "Every line IS the content… A creator can
  open their editor and build the video using ONLY the script. No guessing."
- **Marque today:** first script = the same generic pipeline output; no reasoning capture, no
  teach-back moment; onboarding ideas → brief → script has no "why this works" layer.
- **Asset to copy:** the 2-field output contract + the 3-check self-critique (VOICE MATCH /
  VIEWER SEAT / SECTION SCORING — "read the script as a random viewer scrolling their feed at
  2am") + the `<script_quality>` reference scripts + `tutorial_pregen.py`'s highlight contract
  (highlight must be a character-for-character substring; "highlight fewer lines rather than risk
  a mismatch").
- **Adaptation:** self-contained; works at true zero-state (Palo runs it from niche-derived
  identity + best practices only). The VIEWER SEAT self-critique paragraph alone is a free S-size
  insert into `scripts_prompt` today.
- **Effort:** M (full tutorial) / S (self-critique + quality-bar inserts). **Impact: MED-HIGH**
  for conversion; the self-critique insert is high value-per-line for all scripts.

### 11. Anti-repetition: opener dedup + don't-collide block
- **What Palo does:** code derives the opening line of the last 5 videos/scripts and injects
  `OPENING LINES of the most recent scripts:` with the rule "Your draft's first line never
  duplicates one of these openers" (write_pyro/main.py:452-469); every offline pass carries a
  DON'T-COLLIDE input (recent uploads + open projects).
- **Marque today:** anti-repetition exists only on the insight feed (≤50 titles). Scripts see up
  to 20 posts but with no explicit opener-dedup instruction — nothing stops the writer converging
  on one winning hook shape verbatim across a user's feed page.
- **Asset to copy:** the derived-openers block + one rule line into `scripts_prompt` and the feed
  pipeline (dedup across the page's own scripts too — best-of-N hooks makes intra-page collision
  likely).
- **Effort:** S. **Impact: MED** — repetition is the most user-visible "it's a template" tell.

### 12. Prompt iteration + caching plumbing for the mainline writer
- **What Palo does:** every writing prompt is override-served (LD) with an in-code fallback;
  two-tier CACHE A (stable per channel) / CACHE B (volatile last) layout with the 4096-char
  min-cache gate, stable-prefix block ordering (instructions → identity → strategy → exemplars),
  and the placeholder rules learned from incidents: append-if-absent for feature-critical
  placeholders, never `str.format` on override text, parse the live contract AND the previous one.
- **Marque today:** `prompt_store` overrides exist for the 11 `palo.*` keys only — every mainline
  script prompt is redeploy-to-change (weak point #7); `palo_llm.build_system` has the breakpoint
  but the native stack doesn't use it and there's no min-size gate.
- **Asset to copy:** register `scripts`, `script_judge`, `steer`, `hooks`, `hook_judge` in
  prompt_store; adopt the CACHE A/B ordering in `scripts_prompt` assembly; port
  `ensure_json_word`-style guards for structured callers.
- **Effort:** S–M. **Impact: MED directly, HIGH as an enabler** — items 1–8 are all prompt
  wording; without an override path each iteration costs a deploy, which is exactly why Palo's
  wording is 5 generations ahead.

### 13. Exemplar-bank v2.5 stance (bank as TARGET, not mirror) — larger scope, flag-dark today
- **What Palo does:** the bank discovery prompt (`exemplar-bank-discovery-prompt` "v2.5"
  variation) assumes a sub-breakout creator: "assume their own {primitive}s are WEAK — raw voice
  material, never the quality bar… the star of each pattern is Palo's improved or proposed
  version"; proven floor (own line below 30% of niche bar never counts as "observed");
  reshoot-grade example content; per-card 0–100 confidence; retrieval orders PRESCRIBED patterns
  at neutral 1.0 so synthesized golden craft outranks the creator's own below-baseline lines.
  Bank-PRIMARY beat descriptive clusters 20–4 in a views-grounded A/B. FILL mode must call the
  bank first.
- **Marque today:** `exemplar.py` from the July port predates all of this (and contradicts house
  doctrine — its template bank seeds question-openers while VIRALITY_BLOCK bans them, weak point
  #9); flag-dark, Opus compile allowlisted-empty.
- **Asset to copy:** the v2.5 discovery + v2.3 element prompt texts
  (`flags/exemplar-bank-discovery-prompt__v2.5*.txt`), the proven-floor + `_lift` ordering code
  comments, the "hold them on a pedestal when scripting" injection framing.
- **Adaptation:** this is the deep personalization play — only worth it as a project alongside
  arming STRATEGY/EXEMPLAR flags. Cold start is actually its strong suit: a zero-post creator
  gets an all-experiment bank of modeled craft grounded in niche priors, which is exactly
  Yunicorn's day-1 population. Fix the question-opener template contradiction regardless (one
  line, do now).
- **Effort:** L. **Impact: HIGH long-term, gated on strategy-brain arming.**

---

## Cold-start ledger (zero connected accounts) for every item
1/2/6/7/8/9/11/12 — no channel data needed at all. 3 — block omitted + instruction fill; voice
from brand sliders. 4 — fully cold-safe. 5 — honest markers per slot (Palo's exact degrade
strings quoted in palo-writers.md §6). 10 — designed for zero-state (Palo runs it pre-connection
from niche identity). 13 — modeled/exemplar-only bank is the designed cold path.

## Suggested sequencing
Week-1 (all S, no new calls): #4 steer/section ops, #6 output contract + duration, #8 judge
anchors + regex gate, #11 opener dedup, #10's VIEWER-SEAT self-critique insert, exemplar template
contradiction fix. Next: #1 writer prompt swap + #3 voiceprint block + #2 planning block (one
combined prompt revision behind prompt_store, #12). Then: #2 full outline stage on the background
pass, #5 write-agent v3.3, #10 tutorial. Later/strategic: #13 bank.
