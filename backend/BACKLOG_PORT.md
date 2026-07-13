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
- [x] `app/ideas.py` — onboarding idea prompts (verbatim) + IdeaSet + HAIKU eval gate → `briefs`; keyless mock + eval pass-through; flag IDEA_BANK (test_palo_ideas.py 6 green; +2 port_eval; full suite 875)
- [x] spitfire Generator→Critic→Editor→Ranker (Anthropic-only, ≤4 calls) + `parse_thinking_output` (verbatim port) + `parse_all` + `_parse_ranking`; keyless→mock (test_palo_ideas.py 10 green; +2 port_eval; suite 879)
- [x] `/internal/cron/ideate` route (token+flag guarded) + tier-cadence `is_ideate_due`/`run_ideate_for`/`run_ideate_cron` (watermark-tracked). `run_ideate_for` is the event-`_spawn` primitive (dossier-hook call folds into Phase 4). (test_palo_cron.py 7 green; +1 port_eval; suite 886)
- [x] feed integration: `brief_feed_items` (ideate-rank + pulse-judge-lite min-score) merged into `/v1/feed` first page (dedup, cap, no re-inject on pagination) + `/v1/ideas` bank route; flag-gated (test_palo_feed.py 7 green; +1 port_eval; suite 893)
- [x] tests: parser golden + eval gate + budget ≤4 + tier gating — covered by test_palo_ideas.py (10) + test_palo_cron.py (7) + test_palo_feed.py (7) + 7 port_eval goldens

## Phase 3 — post-performance insights  (flag: TRACK_INSIGHTS)
- [x] `app/metrics_pollers.py` — Apify(3a) / Post for Me(3b) / IG Graph(3c) fetchers → `metrics_ts`; `pick_source` walks the tier chain (first available), keyless→no-op (test_palo_metrics.py 7 green; +2 port_eval; suite 900)
- [x] `app/track_insights.py` — milestone ladders + watermark-first-run-ZERO + median+MAD ≥2.5x spike (≥2 reads) + underperformer-skip-before-work; LOOP I proves the 3 Palo bugs (test_palo_insights.py 8 green; +3 port_eval; suite 908)
- [x] Insight Discovery Engine prompt → `insight_feed` (dedup_hash content de-dup) + ≤50 anti-repetition context; keyless→template cards; `write_insights`/`scan_and_write` (test_palo_insight_cards.py 4 green; +2 port_eval; suite 912)
- [x] deliver: `push.send_insight` (APNs + deeplink/seed = insight→converse bridge) + `deliver_insights` (marks delivered even keyless) + `settle_candidates` (metrics→bandit outcome_y bridge) + `run_insights_cron` + `/internal/cron/insights` route (test_palo_deliver.py 6 green; +2 port_eval; suite 918)
- [x] tests (LOOP I): day-1 zero, dedup blocks repeat, underperformer skips before detect_spike — test_palo_insights.py 8 green (Phase 3 box2)

## Phase 4 — strategy compiler / brain  (flag: STRATEGY_COMPILER)
- [x] `app/dossier_adapter.py` — dossier+transcript+metrics → compiler analysis block (RISK #1); `catalog_block` = metrics-ranked evidence pack; thin dossier degrades gracefully (test_palo_dossier_adapter.py 4 green; +1 port_eval; suite 922)
- [x] `app/strategy_compiler.py` — Sonnet digest → Opus synthesis (doctrine cache-prefix), `split_sections`/`validate_sections`, UPSERT revision+1; keyless→template (all 5 sections + REGIME/LEVER, downstream-usable); gated by flag + `compile_allowed` (test_palo_strategy.py 6 green; +2 port_eval; suite 928)
- [x] gates: `compile_allowed` (allowlist) + `is_compile_due` freshness (per-tier cadence) + per-stage `ai_usage` (digest/synthesis) + `run_compile_cron` + `/internal/cron/compile` route (test_palo_compile_cron.py 4 green; +1 port_eval; suite 932)
- [x] inject compiled strategy into script gen (`_generate_scripts`) + `/v1/converse` via `strategy_block` + `_inject_strategy` (flag STRATEGY_COMPILER, OFF = unchanged) (test_palo_strategy_inject.py 6 green; suite 938)
- [x] tests: section splitter parity + gate math (allowlist+freshness) + downstream-usable template — test_palo_strategy.py (6) + test_palo_compile_cron.py (4) + test_palo_strategy_inject.py (6) + 4 port_eval goldens

## Phase 5 — interactive write agent  (flag: WRITE_AGENT)
- [x] `app/write_agent.py` — plain Anthropic write loop; WRITE_AGENT_SYSTEM (fill/edit/add/answer, ≤250w, exact-substring contract) + strategy/memory injection; `parse_write_actions` (document order); keyless→mock answer; flag WRITE_AGENT (test_palo_write.py 6 green; +1 port_eval; suite 944)
- [x] `apply_actions` exact-substring contract (non-substring edit/add SKIPPED, never fuzzy) + `check_invariants` (LOOP W: exact-substring, ≤250w, no scaffolding-vocab leak) + `/v1/write/turn` route (preview + accept/reject outcomes for iOS tweak-ops) (test_palo_write_apply.py 7 green; +2 port_eval; suite 951)
- [x] onboarding `script_generation.py` → `script_from_brief` (brief beats → full script, strategy-injected, keyless→assembled) + `/v1/write/from-brief` route (test_palo_script_from_brief.py 5 green; +1 port_eval; suite 956)
- [x] tests (LOOP W): XML invariants + ≤250 words + no scaffolding-vocab leak — test_palo_write_apply.py (7) + test_palo_write.py (6) + 4 port_eval goldens (write.parse/apply/apply_skip/leak_firewall)

## Phase 6 — exemplar bank  (flag: EXEMPLAR_BANK)
- [x] `app/exemplar.py` — retrieval/index over channel_strategies.exemplar_bank JSONB: lift-ordered `load_index`/`render_index`, injectable `exemplar_block`, `dereference` to full cards; works hand-seeded; flag EXEMPLAR_BANK (test_palo_exemplar.py 5 green; +2 port_eval; suite 961)
- [ ] `extract.py` rewrite against dossier schema; Opus build + daily refresh decider
- [ ] tests: retrieval golden, budget

## iOS (P7.x — per backend phase, contracts in `docs/api/PALO_PORT.md`)
- [ ] P7.2 feed v2 · P7.3 insights inbox + deep-link · P7.4 Your Strategy · P7.5 write edit-chat · P7.6 reel review
