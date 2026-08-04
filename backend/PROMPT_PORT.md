# PROMPT_PORT — LOOP P contract (Palo → Yunicorn backend port)

You are grinding the Palo → Yunicorn AI-backend port. Work one unit at a time from
`BACKLOG_PORT.md`. Reference architecture: `../HANDOFF_PALO_PORT_PLAN.md`. Source of
truth to COPY FROM (read-only, never edit): `/Users/home/Palo_Server`.

## Loop

1. Open `BACKLOG_PORT.md`; pick the FIRST unchecked `[ ]` item.
2. Write the failing test FIRST (keyless — monkeypatch every external boundary; assert
   `mode:"mock"` / deterministic fallback with no keys).
3. Implement in `app/*.py`, behind the item's `app/palo_flags.py` flag (default OFF).
   Copy Palo prompt text VERBATIM into `prompts.py` builders (adapt IG-reel vocabulary
   second). Persist via `PaloStore` methods + `migrations.sql` idempotent blocks.
4. Run `scripts/gate.sh --fast` (keyless). It MUST stay green: full pytest + `eval/port_eval`
   + render checks + secret-scan. Add your new golden checks to `eval/port_eval.py`.
5. Check the item off with a one-line evidence note (`[x] … — <test names>, keyless green`).
6. `git add` ONLY your new/changed files (never `-A`; the tree has unrelated `.shots/`),
   commit locally with a `palo-port:` prefix. Do NOT push or deploy.
7. Repeat. When every box is checked, print exactly: **YUNICORN PORT GREEN**.

## Hard rules

- Keyless-mock everywhere; a missing key degrades, never 500s (matches `if not ANTHROPIC_KEY`).
- No LangChain. Text: `anthropic()` (request path, raises→route mocks) or
  `app.palo_llm.anthropic_cached()` (background, returns None→mock). JSON: `anthropic_json` /
  `anthropic_cached_json`. Models: `OPUS/SONNET/HAIKU` from `prompts.py`.
- Every LLM op records `ai_usage` and has a call-budget test (compile ≤2 heavy, ideate ≤4,
  judge ≤1). Strategy compile stays behind `ai_usage.compile_allowed` (allowlist default empty).
- NEVER copy a Palo secret. LOOP C (`secrets:scan` in gate.sh) fails on Moonshot keys,
  `sk-…`, RapidAPI keys, or `postgres://user:pass@` in the diff.
- Tier gating via `app.tiers` only (`has_feature`, `cadence`, `metrics_sources`, `at_least`).
- iOS: ship typed request/response models + a contract note in `docs/api/PALO_PORT.md`; do
  not hand-edit Swift in this loop unless the item is a P7.x UI unit.

## Definition of done (a unit)

Failing→passing keyless test committed · flag default OFF · `gate.sh --fast` green ·
`port_eval` golden added · `ai_usage` + budget test · backlog box checked with evidence.

---

## Round 2 — 2026-08-04 (scriptwriting + suggestions upgrade from Palo HEAD + live LD prompts)

Source: Palo_Server @ 2f7edd682 (2026-08-03, 394 commits past the July port) + the LIVE
LaunchDarkly serving variations (37 flags fetched with full targeting trees — the "main"
variation is often NOT what production serves; e.g. pulse-script-prompt serves the
a5-script-generator "stage" variation). Full analysis: docs/research/palo-round2/
(PORT_PLAN.md + three gap-lens reports). Everything self-contained: no LD, no Palo server.

Shipped in this round:
- **a5 craft core** (`prompts.CRAFT_RULES_BLOCK` + `CRAFT_EXAMPLES_BLOCK`): first-line-is-
  the-video / every-line-earns-the-next / payoff-lands-last / read-aloud test / banned
  phrasings / vocabulary firewall + the annotated RIGHT/WRONG worked pair, injected into
  scripts_prompt with a viewer-seat self-check.
- **Plan-first JSON contract**: SCRIPT_JSON_ELEMENT defines `plan` FIRST (generation walks
  definition order → structure decided before words; Palo's <planning> stage folded into
  the schema) + `durationSeconds` honest length estimate. `_ensure_speakable` strips plan
  and clamps duration on every script path. Fast paint untouched.
- **Voiceprint + opener dedup**: `_voice_exemplars` upgraded to Palo's shape-not-lines /
  voice-not-content framing; `_opener_dedup_block` injects the last 5 real openers with a
  hard no-duplicate rule.
- **Steer rebuild**: rules 0-4 from the LD section-op family (build-tensions/rephrase/
  shorten) + GROUNDING + SPEAKABLE on the highest-frequency post-generation path.
- **Judge anchors + axis caps** in script_judge_prompt; `_BANNED_PHRASE_RE` deterministic
  lint family (a5 + gate.go lists, narrow on purpose).
- **Idea generation v2** (`IDEA_GENERATION_SYSTEM` = LD main, live prod text): structural
  adaptation doctrine, viewer-desire titling, radical simplification + real sauce,
  justification-as-niche-insight, anti-target recent_catalog, PROOF LINE **honesty-gated**
  (omitted whenever no real exemplar view counts are in-context — the never-fabricate rule
  enforced in the prompt fallbacks).
- **Scored idea judge** (pulse/judge.py verbatim: 4 axes + axis-cap rejections) wired into
  suggest_ideas after the binary eval gate; judge 0-10 → brief score (0..1), `promoted` at
  Palo's 8.0 banger threshold; keyless → -1 sentinel keeps positional scores (never a
  fabricated judged score). Plus `hedges()` pre-LLM banned-phrase gate on idea copy.
- **THE MIX** (`prompts.mix_block` + `main._mix_for`): code-computed rotation prior
  (per-pillar recent counts + queued unbuilt briefs) injected into next-idea; the
  lane-inventory rule ("a queued unbuilt idea SERVES its lane").
- **a4 insight-card rules** in INSIGHT_DISCOVERY_SYSTEM (mechanism-not-plot headlines,
  claim-wears-its-sample-size, zero-context test, numbers-read-like-speech, machinery
  invisible) + hedging lint with the deterministic template as the floor.
- **Identity-only mode** on /v1/converse when settled==0 (in-your-space-not-your-content
  register; forbids helpless deflection AND fabricated performance talk).
- **First channel read**: POST /v1/connect/channel-read (text_onboard port — metadata-only
  Sonnet read right after connect; keyless/thin → honest empty). iOS wiring pending.
- **SCRIPT_FROM_BRIEF retention structure** (PROMISE/CONFIRMATION/CONTINUATION/PAYOFF +
  filler cut + read-aloud test) and the anti-horoscope test in pillar_judge.

Deferred (documented in PORT_PLAN.md, ranked): channel-identity doc w/ data_confidence +
macro_style (M), sketch→idea bake-off replacing spitfire (M-L), write-agent v3.3 (arm the
flag first), decider/Today briefing, engagement feedback loop + soft-no, outcome predictor
(L — Palo measured LLM zero-shot ranking at coin-flip 49.3%; own-history 68.2%), first-
script teach-back tutorial, MODE 2 direction cards, nightly factory chassis.

---

## Round 3 — 2026-08-04 (full parity: the suggestion organism)

Owner directive: "our system should mimic Palo's as closely as possible — implement all
the suggestions and go further." Seven new modules + shared-file wiring, ALL flag-gated
default OFF (deploy-safe dark): CHANNEL_IDENTITY, SKETCH_IDEAS, DECIDER, ENGAGEMENT,
OUTCOME_RANKER, MORNING_BRIEF, TUTORIAL.

New modules (each with its own keyless test suite):
- **app/channel_identity.py** — the identity DOC every Palo generation consumes: Path B
  cold recipe (voice from how the user writes, anchors, anti-horoscope THE TEST),
  STRONG/PARTIAL/THIN synthesis ladder with code-clamped data_confidence, macro_style
  six dials, Layer1/Layer2 established recipe + ghostwriter-brief test. Stored on
  creators.channel_identity; injected via _inject_brain + the idea-gen identity slot;
  built at onboarding completion.
- **app/sketch_ideas.py** — the a5a sketch → a5b bake-off funnel (supersedes spitfire
  when armed): 8 engine-level sketches → 3 rivals → judge → winner(s) with the
  pitch/brief two-reader card anatomy; vocab firewall (digit-window), scaffold
  discard-never-repair, NO-FALLBACK-COPY ([] on failure → spitfire floor), ≥0.6
  containment dedup. Wired into run_ideate_for.
- **app/decider.py** — pulse/decide port: diagnosis → the RIGHT response type
  (OBSERVE_REVIEW / GENERATE_IDEAS / GENERATE_ALT_IDEA / REVIVE_PROJECT / NUDGE_ONLY),
  ≤3/day, destination FORCED in code (_DEST), pills number-scrubbed against evidence,
  silent day on any failure; vitals thresholds (decisive-negative 0-of-n≥5, weakest n≥3,
  posting_gap max(4, usual×2)); briefing shape w/ hero preference + provenance.
  Route: GET /v1/today.
- **app/engagement.py** — suggestion outbox (suggestion_outbox table) + Palo's tier
  policy (engaged / ignoring / skimming over last 10 shown) + the soft-no with the
  undelivered-mail nuance (zero opens ⇒ no soft-nos). Injected into idea generation +
  judge. Route: POST /v1/suggestions/track.
- **app/outcome_ranker.py** — the measured-signal ranker (Palo: LLM zero-shot = 49.3%
  coin-flip; own-history 68.2%): 30-dim featurizer, pairwise logistic (MIN_RATIO 2.0,
  censor 30d, ≥8 pairs or no model), stable rerank in brief_feed_items, 21-statement
  anchor brief into _inject_brain, retrain spawned on settle. creators.outcome_model.
- **app/tutorial.py** — first-script teach-back pregen: exact-substring highlights
  (drop-never-repair), hook/payoff always taught, shared-ownership voice; honest
  2-step template keyless. Route: POST /v1/scripts/tutorial. Feeds on the new
  {script, reasoning} 2-field contract from write_agent.script_from_brief.
- **app/comms.py** — morning brief (proactive-daily "stage" rules): selects, never
  writes; exact-title quotes code-validated (hallucinated title ⇒ discarded);
  empty body = honest silence; perf_phrase tone bible (never raw multipliers).
  Route: GET /v1/morning-brief.

Shared-file upgrades in the same round:
- **WRITE_AGENT_SYSTEM → v3.3** (agent-write-prompt "Treatment 1" adapted tool-less):
  mode-detection ladder, reply envelope, <planning> contract (parsed + returned
  separately), payoff-first grounding, 8-item self-audit; corrective-error invisible
  retry on bad edit anchors ("the error message IS the repair prompt");
  script_from_brief emits {script, reasoning}.
- **Direction options MODE 2** (POST /v1/onboarding/direction-options): format-lane
  cards written as "a video you'd recognize while scrolling", honestly framed.
- **Conversation summarizer v4.1**: /v1/converse truncation now summarizes the dropped
  prefix into the recall ledger (decision rows w/ verbatim quotes) in the background.
- **STEER_OPS** named one-tap ops (build_tension/improve_hook/rephrase/remove_fluff/
  shorten) from the LD section-op family.
- **palo_llm**: wcut word-boundary cap + the recorded Sonnet-5 emission armor notes.
- Migration `palo_round3_parity_additive` APPLIED (additive only): creators.channel_identity,
  creators.outcome_model, briefs.pitch/brief/concept/promoted, suggestion_outbox table.

Arming order suggestion: CHANNEL_IDENTITY → ENGAGEMENT → SKETCH_IDEAS (with IDEA_BANK)
→ DECIDER → MORNING_BRIEF → TUTORIAL → OUTCOME_RANKER (needs settled volume).
