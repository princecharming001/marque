# GAPS — SUGGESTIONS LENS (what the creator should make next)

Scope: ideation chains, offline factory loop, pulse/proactive layer, outcome prediction & ranking,
proactive insights, feed briefs, judge gates on ideas. Ranked list of concrete portable upgrades for
Marque/Yunicorn. Sources: palo-pulse.md, palo-offline.md, palo-grounding.md, palo-writers.md,
palo-interaction.md, palo-onboarding.md, palo-llm-infra.md, marque-current.md, ld-map.md, plus
`ld_flags_all.json` (full LD variation texts) and the per-flag dumps under
`scratchpad/palo_analysis/{prompts,flags,flags_dump,onboarding_flags}/`.

**Framing fact:** Marque's suggestion surfaces today are (a) the feed's bandit pillar rotation +
`_feed_topics` mad-libs (main.py:11062, 10632), (b) one HAIKU `next_idea_prompt` (main.py:966),
(c) a deterministic coach card (main.py:~930), (d) flag-dark IDEA_BANK running the ported spitfire
chain (app/ideas.py), and (e) TRACK_INSIGHTS deterministic detection + a small HAIKU card
(app/track_insights.py). Palo since the 2026-07-13 port built an entirely new suggestions organism:
sketch→idea bake-off, a nightly orchestrated factory, a scored judge with a promotion gate, a decider
that picks the RIGHT response type, THE MIX rotation prior, an engagement feedback loop, and a trained
outcome ranker. Marque has none of it. Everything below is un-ported.

---

## 1. Sketch → Idea bake-off funnel (replace the spitfire chain wholesale)

**What Palo does:** Two-pass ideation. Pass 1 (`offline/sketch.py`, LD `offline-sketch-prompt`
variation **stage** = `a5a-sketch-ideator v3.0`): tool-less Sonnet with adaptive thinking, 20k tokens,
emits ~10 concept sketches that must be "genuinely different AT THE ENGINE LEVEL" plus ≤3 web queries
(code executes them via Perplexity; results advisory-never-citable), RUBRIC OFF ("judging is not your
pass"), one mandatory longshot, payoff test at sketch grain. Never blocks — every failure returns None.
Pass 2 (`offline/generators.py:_idea_copy`, LD `offline-idea-prompt` variation **stage** =
`a5b-idea-generator v3.4`, 101KB incl. doctrine): mix-check → pick 3 ENGINE-level rivals from the
sketchbook → judge them (payoff test, collision check, web check as judgment-never-citation) → flesh
out ONLY the winner, emitting `emit_idea{title, concept, pitch, sources, brief}` with the bake-off
receipt in the brief. The sketch pass exists because "breadth and craft were competing for the same
tokens" in one forced call, and because forced tool_choice disables extended thinking on the Anthropic
API — reason in a tool-less pass, emit in a strict pass.

**What Marque does:** app/ideas.py spitfire Generator→Critic→Editor→Ranker — the chain Palo itself
retired ("all new investment went to offline/"; overnight_ideate untouched since June). Critic/editor/
ranker prompts are one-liners. No sketch, no bake-off, no engine-level collision test, no verification
queries. And it's flag-dark anyway.

**Copy:** LD `offline-sketch-prompt` var `stage` + `offline-idea-prompt` var `stage` (full texts at
`prompts/offline-sketch-prompt__stage.txt`, `prompts/offline-idea-prompt__stage.txt`); code armor from
`offline/generators.py:714-1030` (vocab-leak digit-window regex, `_SCAFFOLD_RE` discard-not-repair,
`_pick_idea_call` last-of-cleanest, NO-FALLBACK-COPY rule at generators.py:1868) and `offline/sketch.py`
parse salvage (unclosed-tag tolerance, both-contract parsing).

**Yunicorn adaptation:** Replace Palo's "package" inputs with Marque's brand block + strategy doc +
exemplar bank + NICHE_PRIORS; swap Perplexity for whatever search Marque arms (or run zero queries —
the prompt degrades: sketchbook absent → idea pass drafts its own 3 rivals, which is also the
**cold-start lane**: a zero-account creator still gets a real bake-off from identity + niche priors).
Embed Marque's already-ported doctrine (byte-identical v1.4) at the prompt tail as Palo does. No LD:
serve via `prompt_store` keys.

**Effort:** M. **Impact: HIGH** — this is the single biggest quality delta for "what to post next";
suggestions stop being first-thoughts, and the bake-off receipt gives every card a defensible "why
this, not that."

---

## 2. Scored idea judge + promotion gate (generate many, promote ≥8, keep the rest browsable)

**What Palo does:** `pulse/judge.py` `_JUDGE_SYSTEM_PROMPT` — 4-axis 0-10 rubric
(specificity 0-3 / non_obvious 0-3 / evidence_grounded 0-2 / actionable 0-2) with axis-cap rejection
rules ("hedging → cap specificity at 1"; "overlaps recent_brief_titles → cap non_obvious at 0") and
"non-obvious for THIS creator" judged against identity/strategy/recent briefs. `pulse/ideate_rank.py`
scores every overnight brief and partitions at **8.0**: promoted → proactive push (`pulse_outbox`
IdeateBanger); rejected → stay in the briefs table as passive in-app discovery. Wrapped in the Go gate
order, cheapest first: banned-phrase regexes ("consider|might want to|have you thought", "great job",
"keep it up", "could be worth", "interestingly,") → evidence refs required → 30d dedup → budget
3/day → quiet hours 21:00–08:00 user-local → semantic dedup → judge. Judge fails OPEN at 7.0 on vendor
error (bounded by dedup+budget), fails CLOSED at 0 on parse garbage.

**What Marque does:** `IDEA_EVAL_SYSTEM` is a binary niche-connection gate (pass/fail) — no scoring,
no promotion split, no proactive lane at all. track_insights pushes fire on deterministic thresholds
with no quality judge, no daily budget concept beyond dedup_hash.

**Copy:** `pulse/judge.py:51-88` verbatim (it's static so it prompt-caches); the partition logic from
`pulse/ideate_rank.py`; the banned-phrase regex list from `gate/gate.go` (quoted in palo-pulse.md §4.4)
as a pre-LLM Python lint.

**Yunicorn adaptation:** Run it over the idea bank's nightly output; promoted ideas become the feed's
page-0 briefs + an APNs push (Marque's `conversation_seed` bridge already exists — perfect delivery
vehicle); rejected ideas stay in `/v1/ideas`. Budget 3/day + quiet hours in user TZ are simple columns.
Cold start: judge context degrades to brand block + niche priors — still works (its job is mostly
killing hedged/obvious/duplicate cards, which needs no history).

**Effort:** S–M. **Impact: HIGH** — it converts "we generated stuff" into "we only interrupt with
bangers," the exact trust mechanic Marque's proactive surface lacks.

---

## 3. The Decider + Today briefing (diagnosis → the RIGHT response type, not always "more ideas")

**What Palo does:** `pulse/decide.py` (`pulse-decider-prompt`; LD flag absent → **code fallback IS
prod**, decide.py:42-105): once a day, reads grounded candidate signals + strategy + vitals + send
history, picks ≤3, and chooses response type by diagnosis — the judgment table: weak video in a proven
bucket → the EXECUTION leaked → OBSERVE_REVIEW, not more ideas; format itself failing → GENERATE_IDEAS
back in a proven format; cooling subject + strong format → GENERATE_ALT_IDEA (keep format, swap
subject); overperformer → ride it now (or REVIVE a fitting saved idea). Emits `day_header`/`day_summary`
plus a per-decision `generator_instruction`, `pills` (["📉 1,100 views", "✦ 3 ideas"]), and
`confidence: hypothesis|likely|validated`. Code FORCES `destination` from `response_type` (`_DEST` map)
so a model slip can't misroute a card. Any failure → `{"decisions": []}` — "a silent day is always a
safe degrade." `pulse/briefing.py` then shapes the outbox into the Today surface: hero card ranked by
`_HERO_PREFERENCE` ("a concrete artifact beats a pure nudge"), ideas/reviews lanes, and a per-card
`provenance` trace {noticed → diagnosis → action} so the creator sees WHY.

**What Marque does:** The feed picks pillar/style by Thompson arm and writes a `why_picked` line;
coach card is a fixed template family. There is no diagnosis layer — every signal becomes either a
script or a stat card; nothing ever says "your concept is fine, the execution leaked, review it" vs
"the format is dead, here are ideas in a proven one."

**Copy:** decide.py:42-105 system prompt verbatim + the `_DEST` forced-destination pattern +
briefing.py's `_HERO_PREFERENCE` ordering + provenance shape. Sensors: Marque already computes arm
lifts, spike detection (median+MAD), posting cadence — map them to Palo's `CandidateSignal` shape
(vitals.py thresholds worth copying: decisive_negative "0 of n≥5 beat baseline — retire, don't retest";
weakest needs ≥3 samples; L10 **one-median rule**; posting_gap = max(4, usual_gap×2)).

**Yunicorn adaptation:** Response types map to Marque surfaces: OBSERVE_REVIEW → /v1/teardown of the
named post; GENERATE_IDEAS/ALT_IDEA → idea bank runs with the decider's `generator_instruction` as the
brief; REVIVE_PROJECT → resurface a saved brief; NUDGE_ONLY → coach card. Cold start: empty candidates
→ early-return silent day, no LLM call (decide.py behavior) — and Marque's honest setup card stays the
day-0 answer.

**Effort:** M. **Impact: HIGH** — this is what makes a suggestions engine read as a strategist rather
than a slot machine; it also naturally rations LLM spend (≤3 decisions/day).

---

## 4. THE MIX — a programming rotation prior that every suggestion surface reads

**What Palo does:** Strategy synthesis v2.7 (`strategy-synthesis-prompt` variation "use code default";
full text `flags/strategy-synthesis-prompt__use_code_default.txt`) mandates that "Right now ALWAYS ENDS
WITH THE MIX — a rough programming guide for whoever is picking the next video… naming what the channel
leans into hardest this cycle and what stays in rotation for variety — roughly how often each earns a
rep, in words, never quotas… read the mix against the recent uploads — if the last few videos were one
type, the next one leans to whatever is under-served." The idea pass's step 1 is the mix-check; the
orchestrator's rule 4 adds the anti-monoculture inventory rule: "a fresh loop-created idea already
queued in a lane serves that lane until it ships or dies — commissioning a second entrant into a lane
whose first entrant sits unbuilt is the monoculture failure wearing variety's clothes."

**What Marque does:** Bandit arm rotation approximates this implicitly, but nothing textual tells the
idea generator, feed, or converse what type is DUE; the flag-dark strategy compiler (v1.9-era) has no
MIX section; queued-but-unbuilt briefs don't suppress same-lane generation.

**Copy:** The MIX paragraph into `_STRATEGY_SYNTH_INSTRUCTIONS` (app/palo_prompts.py:258); the
mix-check step from `offline-idea-prompt` §1; the lane-inventory rule from
`offline-orchestrator-prompt` rule 4 (`prompts/offline-orchestrator-prompt__stage.txt`).

**Yunicorn adaptation:** Marque's pillars + styles are the lanes. Even without the full strategy
compiler armed, a code-computed mini-MIX (pillar × recent posts × unconsumed briefs → "what's
under-served") injected into `next_idea_prompt`, the feed picker, and idea generation gets 80% of the
value. Cold start: the MIX degrades to pillar rotation over the onboarding pillars — exactly what the
feed does today, but now stated to the model.

**Effort:** S. **Impact: HIGH for its size** — it's the connective tissue between strategy and
suggestions and directly kills the "5 variants of the same video" failure mode.

---

## 5. Proactive insight card engine — the a4-insights prompt (replace INSIGHT_DISCOVERY_SYSTEM)

**What Palo does:** LD `offline-publication-prompt` variation **stage** = `a4-insights` (35KB; served
stage per ld-map) — card = {kind, headline, body, move, evidence[{ref, excerpt, why}]}, kinds
driver·drag·shift·resurgence·fatigue·cadence·opportunity. The tested rules: headline = "THE MECHANISM,
THE NUMBER, THE LEAN… ≤155 chars… names the LEVER, not the video's plot"; body proves the headline
"comparison-forward" with a real mechanism ("because the ending stays unresolved, so people hold on");
EXACTLY ONE MOVE, "physically specific, about 30 words… one thing they can do with a camera"; NO COINED
NAMES (zero-context test); "THE CLAIM WEARS ITS SAMPLE SIZE. Two videos prove a story about two videos…
never a law"; numbers read like speech ("7x", never "7.39x"; "12.2M", never an odometer copy); "TELL
HER SOMETHING SHE CANNOT ALREADY SEE… a card that hands her own actions back to her is dead on
arrival"; machinery invisible ("Palo talking about its own paperwork is the strangest sentence a
creator can read"). Plus a 10-headline gallery AND a 7-failure anti-gallery from live runs — ready-made
few-shots. Related: LD `analysis-proactive-insight` **stage** is an 18.4KB Insight Discovery Engine,
"much richer than the port" (ld-map §note).

**What Marque does:** `INSIGHT_DISCOVERY_SYSTEM` — a good but small "non-obvious truth" contract;
track_insights cards are short HAIKU copy over deterministic detections. No move field, no evidence
excerpts, no kind taxonomy, no galleries.

**Copy:** the a4 stage text (`prompts/offline-publication-prompt__stage.txt`) including both galleries;
the code guards from `generators.py::_gen_insight` (hidden-kind evidence filtered — `find:/trig:/
vitals:` never creator-facing; refs validated against the finding's own evidence; internal-ref-tag
scrub of headline/body). Also the judge's number-discipline pairs well with Marque's existing
verbatim-lift acceptance check (keep that — it's a code-level guard Palo enforces by prompt only).

**Yunicorn adaptation:** Detection stays Marque's deterministic layer (it's already the right
architecture); only the CARD WRITER upgrades. Evidence refs = Marque post IDs, rendered as tappable
receipts. Cold start: no posts → no detections → no cards (already Marque's honest behavior; keep it).

**Effort:** S–M. **Impact: HIGH** — insight copy quality is the whole product on this surface, and this
is ~6 weeks of nightly-run QA embedded in wording Marque can drop in.

---

## 6. Engagement feedback loop + the soft-no (suggestions that notice they're being ignored)

**What Palo does:** Three interlocking pieces. (1) Chat/loop context carries the last 10 delivered
proactive nudges with opened/dismissed/saved flags + 4-week engagement rates ("high dismiss rate means
you've been off the mark; tune accordingly" — interaction context.py:532-577). (2) The judge's POLICY
carries a code-computed engagement tier — `engaged` (any save or ≥50% opens) / `ignoring` (≥4 sent,
0 opens) / `skimming` — "engaged earns more ideas; ignoring earns fewer, better ones" (judge.py:136-147).
(3) The soft-no: "an idea Palo already suggested that the creator saw and never acted on is a SOFT NO —
their inaction is an answer… THE COLLISION TEST RUNS ON THE ENGINE, NOT THE TITLE: a different store is
the same video" — but only from a creator who is LOOKING: "when ENGAGEMENT prints zero card opens…
untouched loop-created projects are undelivered mail, not declined ideas" (o1 rule 6).

**What Marque does:** Nothing closes the loop. `recall_ledger` prevents chat re-pitching, but the feed,
next-idea, idea bank, and insight pushes have no knowledge of what was shown, opened, or ignored; the
50-title catalog dedup exists only in the LLM prompt as "recent titles."

**Copy:** the engagement-tier computation (3 buckets from outbox outcomes), the soft-no + engine-level
collision paragraphs from `offline-idea-prompt` rule 5, and the pulse-feedback context block wording
from interaction handler.py:126-243.

**Yunicorn adaptation:** Marque already logs feed impressions/opens client-side for briefs; add an
`outbox`-style table (suggestion_id, shown_at, opened, saved, dismissed) and inject the tier + last-10
digest into idea generation and the judge. Cold start: tier defaults to `skimming`; soft-no is inert
until there are shown-and-seen cards — correct by construction.

**Effort:** M. **Impact: HIGH** — it's the difference between a suggestion engine and a spam engine,
and it compounds every other item on this list.

---

## 7. Outcome predictor — silent best-of-N reranking + per-creator anchor briefs

**What Palo does:** `outcome_predictor/` (never ported; `docs/OUTCOME_PREDICTOR.md` is the spec).
Linear pairwise within-channel ranker P(A beats B)=σ(w·(x_A−x_B)); own-history 68.2% vs **Claude
zero-shot 49.3% = coin flip** on the same docs. Crucial for suggestions: **idea-text-only scoring hits
59.4%** — a pre-production idea can be ranked before anything is filmed. The anchor probe (anchors.py)
scores ~50 plain-English mechanism statements against a channel's model → a readable "hooks that win
here / lose here" brief. Tested extensions: 24h-velocity breakout early-warning at 95.3% (powers "post
the follow-up NOW") and score-percentile calibration ("80th-percentile idea; that percentile
historically did 40k–200k"). View-magnitude prediction is a tested NEGATIVE.

**What Marque does:** `_final_score` = HAIKU critic blend pulled toward settled outcomes by
`_calibration_signal` — i.e., an LLM-judged score lightly calibrated; the bandit ranks pillars, not
candidate ideas. Palo's 49.3% result says the LLM-scored component adds ~zero outcome signal.

**Copy:** the featurizer pattern (features.py — 22 dense features + field-separated hook/structure
embeddings), dataset hygiene constants (MIN_RATIO=2.0, RIGHT_CENSOR_DAYS=30, time splits), the numpy
ranker, anchors.py's statement list, and the doc itself.

**Yunicorn adaptation:** Marque has per-video analyses + metrics auto-sync (b68) — the training
substrate exists. Phase it: (1) pooled cold-start model (54.8% — weak but >random) as a silent
reranker over idea-bank candidates via idea-text scoring; (2) per-channel once ≥50 pairs; (3) anchor
brief injected into idea generation ("what wins for YOU"); (4) 24h breakout trigger feeding the
decider (#3) as a high-salience candidate. Cold start: pooled model only, or skip the reranker
entirely — the system degrades to today's ordering.

**Effort:** L. **Impact: MED-HIGH** — the only item on this list with measured outcome signal; also
the strongest argument to STOP investing in LLM-score ranking refinements.

---

## 8. Idea card anatomy + no-fallback-copy + vocabulary firewall (the card contract)

**What Palo does:** `emit_idea{title, concept, pitch, sources, brief}` — pitch (≤30 words, creator-
facing, "sentence one is Palo's read — why this idea, NOW, with their own catalog doing the arguing,
NO numbers-speak; sentence two is the gist") and brief (≤60 words, internal handoff: what must hold ·
what to verify · what stays open · bake-off receipt) are separate fields **because they have two
readers**. Concept = PRIMER (the engine + the charge) then arrow-led beats, each = channel-native
anchor + what the viewer sees/hears + terse function note; "PAINT, DON'T THEORIZE"; hook and payoff
are always real (an open middle slot is legal only with a precise story role). Code: vocab firewall
fires on multipliers always, stat-words only with a digit within 30 chars (bare-word matching cost
real retries on "lift the pallet"); retry once with "REJECTED — rewrite in plain creator language…
keep everything else identical"; **NO FALLBACK COPY** — if the idea pass fails, refuse to mint ("a
claim can never be card copy… a missing pick is a quiet miss; internal prose on the card is a trust
break"). Dedup-before-create: token containment ≥0.6 against projects touched in last 45d.

**What Marque does:** Brief = title + beginning/middle/end strings; no pitch/brief split, no
verification instructions, no engine dedup (dedup_hash is exact-ish), and several surfaces fall back
to template copy on failure (mock_ideas, `_feed_topics`) — acceptable for scripts, corrosive for
"insight" cards.

**Copy:** rule 8 + rule 10 of `offline-idea-prompt` stage; generators.py:751-827 (firewall) and
:1868-1888 (no-fallback) and `_find_duplicate_project`.

**Yunicorn adaptation:** Extend the `briefs` schema with pitch/brief/sources; feed renders pitch,
write-from-brief consumes brief + beats. Cold start: unchanged — the anatomy is data-independent.

**Effort:** S. **Impact: MED** — card trust + a clean handoff contract that item #1 needs anyway.

---

## 9. Onboarding idea-gen LD upgrade + PROOF LINE (the cold-start first impression)

**What Palo does:** LD `onboarding-prompt-idea-generation` variation **main** (served; 10,594 chars —
NEWER than the code fallback Marque ported): adds `structural_patterns` input, viewer-desire titling
("'Content strategy' is what the creator does. 'How to go viral' is what the viewer wants"), radical
simplification + "real sauce" principles, output-language rule, the **PROOF LINE** ("*Adapted from the
'detail-to-reveal' format — videos using this structure are pulling 5-37M views in your niche.*" —
name the structural element, real view counts, never creator names), and justification-as-niche-
insight with GOOD/BAD examples ("a moment of strategic insight… NOT a description of Palo's process").
Also: MODE 2 direction-options fallback for unrepresented niches (format-based lanes, honest framing)
and the 3-angle search doctrine (topic-stripped format_query for cross-niche pollination).

**What Marque does:** app/palo_prompts.py `IDEA_GENERATION_SYSTEM` is the condensed CODE fallback —
no structural_patterns, no proof line, no language rule, no niche-insight justification. It IS the
strongest ideation prompt Marque has, but it's the previous generation.

**Copy:** the LD main text verbatim (quoted in full in palo-onboarding.md §3.1; dump in
`onboarding_flags/`). Secondarily the relevance-filter broader_query ladder + MODE 2
(`onboarding-prompt-direction-options` main) if/when Marque builds an exemplar retrieval index.

**Yunicorn adaptation:** structural_patterns can be seeded from NICHE_PRIORS formats until an exemplar
DB exists; proof-line view counts must come from real scraped/retrieved data or be omitted (never
invent — Marque's grounding doctrine already forbids it; when no exemplar data, drop the proof line
rather than fake it). This is THE zero-connected-accounts item: Palo's whole cold-start answer is
"retrieval + adaptation, not creativity" and this prompt is its tip.

**Effort:** S (text swap) / M (with retrieval). **Impact: HIGH for day-0** — first ideas are the
conversion moment, and Marque's feed first-paint is currently its weakest output.

---

## 10. Morning brief + daily comms voice (proactive-daily + weekly tone bible)

**What Palo does:** `offline/comms.py` — comms is "NEVER a rewriter: it reuses insight/artifact copy
verbatim and only decides WHAT the user gets." LD `proactive-daily-prompt` (stage, 15.6KB): "Lead with
the single most useful thing: a made artifact by name… Mention an artifact by its exact title in
quotes… 1-3 short sentences, ≤300 chars, at most one emoji… If the input has no insight, no artifacts,
and no other_actions, return {\"body\": \"\"}" — empty body is the model's own "nothing worth texting"
signal. Artifact kind read from explicit flags, never inferred ("every outline… was announced as an
'idea'"). The weekly brief (`pulse/weekly_pulse_batch.py`) carries the tone bible: NEVER raw
multipliers/jargon/hedging; plain performance phrases ("performed far above your usual average");
narrative-before-recommendations write-order; recommendations "based ONLY on what you wrote in the
narrative"; BAD/GOOD worked pair.

**What Marque does:** `/v1/insights` = two sentences; pushes are per-insight with conversation_seed.
No daily digest, no "here's what got made overnight" moment, no tone bible for performance language.

**Copy:** `proactive-daily-prompt` stage + `offline-comms-email-prompt`; `_SYSTEM_PROMPT` +
`_PERF_LABELS` + the BAD/GOOD pair from weekly_pulse_batch.py:25-53.

**Yunicorn adaptation:** One morning push assembling {top promoted idea (item #2), decider day_header
(item #3), any overnight scripts} — copy reused verbatim from the cards. Cold start: empty inputs →
empty body → no push; the honest silence IS the design.

**Effort:** S–M. **Impact: MED** — retention mechanics; cheap because it only selects, never writes
facts.

---

## 11. Nightly factory harness (scaled down): artifact floor, findings ledger, commission-don't-compose

**What Palo does:** `offline/run.py` — the orchestrated night: sense window with manifest-validated
refs → o1 orchestrator (hard/soft findings with TTL + verdicts; actions as ≤60-word briefs — "you
commission, never compose… a brief that contains the answer has stolen the job") → code gates
(grade×source matrix; **artifact floor**: "this run must carry at least one artifact action —
unconditionally… fresh work, never an old project reheated"; if nothing survives, code synthesizes a
modest idea from the best finding) → generators in dependency order → comms. Notably the **LLM judge
is OFF in prod** ("4o mini doing more harm than good") — quality is held by o1 + code invariants.
Provenance taxonomy: EVIDENCE (openable) / SIGNAL (a number — "fine as support, never the whole
citation") / CONTEXT (strategy doc is never a source); "if openable evidence exists anywhere in the
chain, it reaches the artifact."

**What Marque does:** Cron ideation exists (tier-cadence spitfire) but there is no findings memory, no
floor guarantee ("creator wakes up to something new" is not guaranteed), no evidence-ref discipline on
suggestion cards, no run choreography.

**Copy:** the artifact-floor + gate code patterns from `offline/run.py`; `provenance.py`'s three-class
table; findings `store.py` lifecycle (TTL 30/45/90d, confirm-extends-life, "inconclusive is NOT a
confirm"); o1 rules 4/6/13/16 text. Skip the judge (Palo ships without it) and skip the full sense
window on day one — Marque's dossier_adapter is a proto-window.

**Yunicorn adaptation:** A single nightly Sonnet pass per active creator over a lean window (brand +
arms + recent posts + unconsumed briefs + insight history), emitting findings + ≤2 actions, with the
artifact floor in code. Cold start: no posts → floor still mints one idea from niche priors (Palo's
bootstrap explicitly supports founding findings from the strategy doc / an empty ledger being "the
loudest mandate to publish").

**Effort:** L. **Impact: MED-HIGH** — this is the chassis items #1-#3 ride on; do it after them, not
before. The measured cost anchor: Palo's full night runs 177s avg/channel.

---

## 12. Structured-emit + prompt-ops armor (do this alongside whichever item ships first)

**What Palo does / what to copy (all bug-derived, suggestions-pipeline specific):**
- Forced-tool emits: `strict:true` + `parallel_tool_calls=False` + beta header
  `structured-outputs-2025-11-13`; **`brief` defined LAST in the schema** ("constrained decoding walks
  properties in definition order — brief-first burned the slot with filler"); truncation-is-a-reject
  retry with the feedback message (orchestrator.py:497-529).
- Sonnet 5 gotchas Marque WILL hit on model bump: thinking `disabled` for fixed-format emissions (it
  burned the entire output budget on thinking, zero text, at 768 AND 2048 tokens); text-block-not-
  `content[0]` extraction; `anthropic_thinking_kwargs` gating.
- Plain-text output contracts for long creative output (DURATION_SECONDS sentinel; moment-board
  labeled layout) — "long script escaped inside JSON is a failure magnet"; parse the live contract
  AND the previous one (sketch parser incident).
- `_fit_package` degrade-never-slice for capped context blocks; word-boundary cuts (`_wcut`);
  never [:N]-slice JSON.
- Every prompt_store override that feeds a parser needs append-if-absent placeholder guards
  (`ensure_json_word` class of bug).

**What Marque does:** naive brace-scan JSON extraction, no strict-tool usage, str-slicing of context
blocks, no thinking config (still on Opus 4-8/Sonnet 4-6 so it hasn't bitten yet).

**Effort:** S per pattern. **Impact: MED** — prevents whole silent-degradation classes; the
provenance-vocab-drift and int-video-id bugs in Palo's history are exactly what a ported-then-evolved
prompt stack hits.

---

## Deliberately skipped (parity or out-of-lens)

- **IDEA_EVAL niche gate** — ported, unchanged upstream; superseded by item #2 anyway.
- **Memory/ledger/conversation-summary upgrades, write/outline agents, tutorial pipeline** — real
  gaps but belong to the interaction/scriptwriting lenses.
- **Doctrine content** — byte-identical already ported (only the loader's resident renders are worth
  taking, covered in the llm-infra lens).
- **generate_ideas skill triad (safest bet / creative stretch / high ceiling)** — Marque's port
  already carries the 3-lane framing; the marginal delta (catalog-sheet dedup, produce-NOW directive)
  is folded into items #6 and #9.
- **Spitfire prompt upgrades** — don't polish the chain Palo abandoned; replace it (item #1).
- **Trends** — Marque's deliberate no-mock-trends stance is correct; Palo's NICHE-RIGHT-NOW needs an
  exemplar-channel index Marque doesn't have yet (revisit after item #9's retrieval work).

## Cold-start summary (zero connected accounts — the Yunicorn constraint)

Every ranked item degrades honestly: sketch/idea funnel self-drafts rivals from identity + niche
priors (#1); judge runs on brand-block context (#2); decider goes silent with no candidates (#3);
MIX degrades to pillar rotation (#4); insight cards correctly don't exist without posts (#5);
engagement tier defaults to skimming and the soft-no is inert (#6); outcome ranker uses the pooled
model or is skipped (#7); onboarding idea-gen + MODE 2 format-fallback IS the cold-start product (#9);
morning brief returns empty body (#10); the nightly floor still mints one idea from priors (#11).
Palo's core cold-start insight to adopt everywhere: **convert "no data" into a retrieval + adaptation
problem (someone else's proven structure, this creator's content), and when even retrieval is thin,
fall back to format-priors and say so honestly.**
