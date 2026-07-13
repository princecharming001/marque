# BACKLOG_PORT — Palo → Yunicorn port units (LOOP P)

One checkbox = one module-sized unit: failing test first, keyless-green, then check off
with a one-line evidence note. Grind top-to-bottom. Full plan: `../HANDOFF_PALO_PORT_PLAN.md`.
Completion promise when every box is checked: **YUNICORN PORT GREEN**.

Conventions (non-negotiable, enforced by `scripts/gate.sh`):
- keyless-mock everywhere (no key ⇒ deterministic mock, never a 500)
- prompts in git as builders returning `(system, user)`; hot ones overridable via `prompt_overrides`
- direct `anthropic()` / `app.palo_llm.anthropic_cached()` — no LangChain
- new code in `app/*.py`, gated by `app/palo_flags.py` (default OFF)
- every LLM op: `ai_usage.record(...)` + a call-budget test
- NEVER copy a Palo secret (Moonshot key, staging DB creds, RapidAPI key) — LOOP C greps for them

## Phase 0 — shared infra seams  ✅ COMPLETE
- [x] `app/palo_flags.py` — master + per-capability flags, default OFF
- [x] `app/tiers.py` — 3 full-package tiers + `creator_tier` seam
- [x] `app/palo_persistence.py` — PaloStore (memories/ledger/strategy/briefs/insight/metrics/…), keyless-green
- [x] `app/prompt_store.py` — get_prompt() override+code-fallback, TTL cache
- [x] `app/prompt_assembly.py` — {STRATEGY_*} slicer + {DOCTRINE_*} + infer_craft_regime
- [x] `app/doctrine.py` + `knowledge/craft_doctrine.md` — craft spine, cache-stable blocks
- [x] `app/palo_llm.py` — cache-breakpoint Anthropic helper, never-raises
- [x] `app/ai_usage.py` — cost accounting + compile allowlist kill-switch (default OFF)
- [x] `migrations.sql` — PALO PORT block (pgvector, match_memories RPC, 10 tables, tier column)
- [x] `test_palo_phase0.py` (14 green) + `eval/port_eval.py` (LOOP G) + gate.sh port+secret stages

## Phase 1 — memory + ledger + overlay  (flag: MEMORY_V2)  ✅ COMPLETE
- [x] `app/memory_v2.py` — extractor (facts only, insights banned in code), embed (OpenAI, keyless→recency), deterministic reconcile ADD/UPDATE/NOOP
- [x] memory retrieve: cue-gate + `match_memories` + weighted rank (0.55 sim + 0.25 conf + 0.20 recency) + scope hard-filter
- [x] `app/recall_ledger.py` — per-turn extraction of assistant proposals; `<prior_recommendations>` block; stdlib ULID
- [x] wire hooks into `/v1/converse` — read-path inject `memory_block` + `ledger_block`, write-path `_spawn(remember)` / `_spawn(record)`, flag-gated OFF (test_palo_wiring.py 2 green; full suite 869; flag-off = byte-identical). Script-gen 5-block injection folds into Phase 5.
- [x] tests: reconcile golden, ledger, drop-insight, flag/keyless guards (test_palo_memory.py 9 green; +4 port_eval golden)

## Phase 2 — idea bank / reel suggestions  (flag: IDEA_BANK)
- [ ] `app/ideas.py` — onboarding idea prompts + IdeaSet + HAIKU eval gate → `briefs`
- [ ] spitfire Generator→Critic→Editor→Ranker (Anthropic-only) + `parse_thinking_output`
- [ ] `_spawn()` on dossier/scan events + `/internal/cron/ideate` route (tier cadence)
- [ ] feed integration: pulse judge + ideate-rank into `/v1/feed`
- [ ] tests: parser golden, eval gate, budget ≤4, tier gating

## Phase 3 — post-performance insights  (flag: TRACK_INSIGHTS)
- [ ] `app/metrics_pollers.py` — Apify(3a) → Post for Me(3b) → IG Graph(3c) → `metrics_ts`, source per tier chain
- [ ] `app/track_insights.py` — milestone ladders, watermark-first-run, median+MAD spike, underperformer skip
- [ ] Insight Discovery Engine prompt → `insight_feed` (dedup_hash), ≤50 anti-repetition context
- [ ] deliver: APNs via `app/push.py` + bandit outcome settle + insight→converse seed
- [ ] tests (LOOP I): day-1 zero insights, dedup blocks repeat, underperformer skips before LLM

## Phase 4 — strategy compiler / brain  (flag: STRATEGY_COMPILER)
- [ ] `app/dossier_adapter.py` — dossier → Palo-shaped analysis block (RISK #1 mitigation)
- [ ] `app/strategy_compiler.py` — Sonnet digest → Opus synthesis, section splitter, UPSERT revision
- [ ] gates: `compile_allowed` + freshness + per-stage `ai_usage`; weekly `/internal/cron/compile`
- [ ] inject `{STRATEGY_*}` into script gen + converse
- [ ] tests: section splitter parity, gate math, budget ≤2 heavy calls

## Phase 5 — interactive write agent  (flag: WRITE_AGENT)
- [ ] `app/write_agent.py` — plain Anthropic tool-use loop; DEFAULT_WRITE_PROMPT fill/edit/answer
- [ ] `<edit>/<add>/<highlight>` XML contract (exact-substring) mapped to iOS tweak-ops
- [ ] onboarding `script_generation.py` → upgrade `/v1/scripts`
- [ ] tests (LOOP W): XML invariants, ≤250 words, one-branch, no doctrine-vocab leak

## Phase 6 — exemplar bank  (flag: EXEMPLAR_BANK)
- [ ] `app/exemplar.py` — retrieval/index (works with hand-seeded bank)
- [ ] `extract.py` rewrite against dossier schema; Opus build + daily refresh decider
- [ ] tests: retrieval golden, budget

## iOS (P7.x — per backend phase, contracts in `docs/api/PALO_PORT.md`)
- [ ] P7.2 feed v2 · P7.3 insights inbox + deep-link · P7.4 Your Strategy · P7.5 write edit-chat · P7.6 reel review
