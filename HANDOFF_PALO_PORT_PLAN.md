# HANDOFF: Palo → Yunicorn Backend Port Plan

**Goal:** Power Yunicorn's entire AI backend with Palo's logic, infrastructure, and prompts — scripts, reel suggestions, self-learning, post-performance learning, plus new Palo-derived features. Palo owns Yunicorn; copy-paste from `/Users/home/Palo_Server` is encouraged (READ from it only — never modify that repo).

**Written:** 2026-07-14. Based on an 8-agent code mapping of `Palo_Server` (Go+Python), `Marque/backend` (FastAPI+Supabase), the PALO frontend Ralph harnesses, and owner decisions below.

---

## 0. Locked decisions (owner-confirmed)

| Question | Decision |
|---|---|
| Architecture | **Merge into Marque's FastAPI** — Palo Python lands as sibling `backend/app/*.py` modules (the `app/dossier.py` / `app/retention.py` precedent). One Render service. |
| Scope | **All four pillars** (script gen, reel suggestions, post-performance learning, self-learning memory) **+ new Palo features** (§6). |
| Metrics sources | **All three, layered:** Post for Me analytics → Apify own-profile scrape → IG Graph API (staged in that order). |
| Prompts | **Code defaults + Supabase overrides** — every Palo prompt flattened into `prompts.py` builders; `prompt_overrides` table checked first (mirrors Palo's `get_darkly_prompt_with_fallback`, no LaunchDarkly dependency). |
| Models | **Match Palo exactly** — provision Palo's providers so copied code runs unmodified (§2). |
| Workers | **In-process** — `_spawn()` asyncio tasks + Render cron hitting internal routes. No new services. |
| iOS | **Included** — per-feature SwiftUI phases (§7). |
| Rollout | **Usual pattern** — `palo-port` branch, every capability flag-gated OFF, Ralph gates green before merge, owner deploys manually. |

**Marque conventions every ported module must obey:** keyless-mock everywhere (no 500s; `{"mode":"mock"}` fallbacks), prompts in git returning `(system, user)` tuples, no LangChain (direct `anthropic()`/`anthropic_json()` httpx helpers), persistence via `supabase_persistence.py` `upsert_*/load_*` + idempotent `migrations.sql` blocks, flags as env vars (`AI_QUALITY`/`EDL_AUTHOR` pattern).

---

## 1. Port matrix (what moves, what stays, what's skipped)

| Capability | Palo source | Marque today | Verdict | Effort |
|---|---|---|---|---|
| Script gen (interactive write agent) | `palo_python/write_pyro/agents/write.py` (DEFAULT_WRITE_PROMPT ~460 lines, SCRIPT_GENERATION_PROMPT, TUTORIAL_PREGEN_PROMPT), `write_pyro/main.py`, `write_pyro/tools/script_tools.py`, `llm/process.py:3588-3990`, `onboarding_agent/script_generation.py` | `/v1/scripts` best_hooks→OPUS→quality gate; tweak-chat typed EDL ops | **ADAPT** — prompts copy verbatim; LangGraph react loop → plain Anthropic tool-use loop | M |
| Reel/idea suggestion | `overnight_ideate/` (Generator→Critic→Editor→Ranker "spitfire"), `onboarding_agent/{idea_generation,established_idea_generation,idea_eval}.py`, `pulse/{judge,ideate_rank}.py` | Reasoned feed + bandit arms + Apify reels intel; no idea bank/judge | **COPY prompts + ADAPT harness** | M |
| Exemplar bank (golden craft patterns) | `exemplar_bank/{extract,analytics,retrieval,prompts,build,refresh}.py`, `interaction_agent/tools/exemplar_tool.py` | Nothing equivalent; dossier + `_FORMAT_EXAMPLES` are the substrate | **ADAPT core, DEFER build/refresh** | M-L |
| Niche context (10k-exemplar corpus) | `strategy/niche.py`, `niche_pipeline/`, Pinecone indexes | `NICHE_PRIORS` + Apify trends (thin) | **SKIP v1** (degrades cleanly) / small pgvector rebuild later | S |
| Self-learning memory | `memory/{extractor,retriever,cue_detector}.py`, `vector_service/main.py` (mem0-style reconcile), `recall/ledger.py`, `strategy/live_updates.py` | `/v1/memory/distill` (flat, no vector/dedup/ledger) | **COPY near-verbatim** — cleanest win | S |
| Strategy compiler (brain) | `strategy/{compiler,loader,refresh_cron}.py` (Sonnet digest → Opus synthesis), `llm/channel_analysis_v2_operations.py` | Brand Graph + brand digest; no compiled strategy artifact | **ADAPT compiler; SKIP Go brain-graph UI** | M |
| Post-performance insights | Go `src/application/track_insights/` + `metrics_truth/` + `pulse/triggers/breakout_video.go`; python `generate-track-feed` prompts | Thompson bandit + `/v1/metrics/ingest`; no timeseries/spikes/milestones/cards | **REBUILD in Python** (transliterate math + copy prompts) | M (+L pollers) |
| Proactive loop (pulse) | `pulse/{decide_cron,generate,briefing,judge}.py` | APNs push + coach cards exist | **ADAPT thin slice** (daily decide→generate→push); SKIP outbox machine | S-M |
| LLM/prompt infra | `llm/{retry,anthropic_cache}.py`, `prompt_assembly.py`, `python_utils/usage_tracker.py` | Marque already has the right shape | **KEEP-MARQUE**; copy retry, cache splitter, `{STRATEGY_*}` slicer, usage accounting | S |
| Doctrine (craft spine) | `doctrine/loader.py` + `/Users/home/Palo_Server/doctrine/v1.4/palo-doctrine-FINAL.md` (418 lines; repo root, NOT palo_python) + `annotated-canon.md` | `backend/knowledge/` KB v2 — structurally the same idea | **ADAPT/merge** into `app/knowledge.py` (keep byte-stable render for cache hits) | S |

---

## 2. Providers & models — "match Palo exactly"

Provision these so copied code runs unmodified. (Marque today is Anthropic-only via direct httpx.)

| Provider | Models Palo uses | Used for | Env vars |
|---|---|---|---|
| Anthropic (have) | `claude-sonnet-4-6` (write/outline default, strategy digest, pulse judge), `claude-sonnet-4-5-20250929` (module default, track proactive), `claude-opus-4-8` (strategy synthesis ~$1.60/compile, exemplar-bank build), `claude-haiku-4-5-20251001` (vision eval, judges) | Everything core | `ANTHROPIC_API_KEY` ✅ |
| Azure OpenAI | `gpt-4o` (track-feed cards, ranker fallback), `gpt-4o-mini` (memory extraction, ledger extraction, reconcile, exemplar profiling), `gpt-4.1`, `gpt-5`→`gpt-5.4` deployment alias | Cheap extraction + fallbacks | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION` (2025-01-01-preview), `AZURE_OPENAI_DEPLOYMENT` |
| Google Vertex | Gemini controlled-generation (video-analysis v7 schema), `gemini-3-flash-preview` (idea eval); `ChatAnthropicVertex` fallback | Watching reels (Brain input) | `GOOGLE_PROJECT_ID`, `GOOGLE_API_KEY` (location=`global`) |
| OpenAI | `text-embedding-3-small` | pgvector embeddings (memories, exemplar search) | `OPENAI_API_KEY` |
| Perplexity | web search tool | Write-agent web search | `PERPLEXITY_API_KEY` (optional — can drop v1) |

**Pragmatic note:** even "matching exactly," each swap point is one line in our port (model constants), so any provider can be collapsed to Anthropic later. Keyless-mock rule applies to ALL of these — every module must run green with zero keys.

**NOT needed:** `LAUNCHDARKLY_SDK_KEY` (prompts flattened), Pinecone (→ Supabase pgvector), AWS DynamoDB/SQS (→ Supabase tables + `_spawn()`), Redis (→ in-proc TTL cache).

---

## 3. Prompt system

New `backend/app/prompt_store.py`:

```python
def get_prompt(key: str, fallback_builder) -> str:
    """Supabase prompt_overrides row (key, prompt_text, updated_at) if present,
    else the code default. Mirrors Palo get_darkly_prompt_with_fallback. Never raises.
    In-proc TTL cache (60s) so per-request cost is ~zero."""
```

Prompts to flatten (Palo source → `prompts.py` builder), copy text **verbatim** first, adapt for IG-reels vocabulary second:

- `write_pyro/agents/write.py`: `DEFAULT_WRITE_PROMPT`, `SCRIPT_GENERATION_PROMPT`, `TUTORIAL_PREGEN_PROMPT`
- `onboarding_agent/script_generation.py` prompt; `onboarding_agent/{idea_generation,established_idea_generation,idea_eval}.py`
- `overnight_ideate/components/prompts.py`: spitfire generator/critic/editor/ranker (LD keys `spitfire-*`)
- `memory/extractor.py` `EXTRACTION_PROMPT`; `recall/ledger.py` `EXTRACTION_PROMPT`; vector_service `_RECONCILE_PROMPT`
- `strategy/compiler.py` digest + synthesis prompts (LD `strategy-digest-prompt`/`strategy-synthesis-prompt`)
- Track insights: Go `src/application/track_insights/prompts.go` `AnalysisProactiveInsightSystemPrompt` ("Insight Discovery Engine") + python `llm/prompts.py` DARKLY_DEFAULT_PROMPTS lines ~775-900 (`generate-track-feed`, `analysis-proactive-insight`, `proactive-general`, `cluster-videos*`)
- `pulse/{judge,decide,generate}` prompts
- Doctrine: `doctrine/v1.4/palo-doctrine-FINAL.md` + `annotated-canon.md` → `backend/knowledge/` (merge with KB v2 via `app/knowledge.py`)

Seed `prompt_overrides` empty; it exists so prompts can be tuned in prod without deploys (the 3-4 hottest: write, strategy synthesis, insight discovery, spitfire generator).

---

## 4. Supabase schema additions

One idempotent block each in `migrations.sql` (RLS-on-no-policies, service-role access), matching `upsert_*/load_*` + `_*_COLS` in `supabase_persistence.py`. Requires enabling the **pgvector** extension on the Supabase project.

```sql
-- memories: pgvector, mem0-style
memories(id uuid pk, creator_id text, type text, key text, value text,
         confidence real, scope text, embedding vector(1536),
         created_at, updated_at, deleted boolean default false)
-- recommendation_ledger: append-only "never re-pitch" (SQL from Palo m0261)
recommendation_ledger(id ulid pk, creator_id, conversation_id, kind text,
                      summary text, created_at)
-- strategy_updates: fast-loop overlay between compiles (m0262)
strategy_updates(id, creator_id, update_text, source, created_at, applied boolean)
-- channel_strategies: the compiled strategy artifact (Palo m0267 shape)
channel_strategies(creator_id pk, strategy_markdown text, strategy_playbooks jsonb,
                   strategy_footnotes jsonb, strategy_revision int, strategy_updated_at,
                   exemplar_bank jsonb, element_inventory jsonb,
                   exemplar_bank_revision int, exemplar_bank_built_at)
-- briefs: idea bank (replaces Palo DynamoDB briefs)
briefs(id pk, creator_id, source text check (spitfire|onboarding|chat|insight),
       title, summary, beginning, middle, ending, score real, status text, created_at)
-- insight_feed: track insights + pulse cards (replaces DynamoDB track_feed + pulse_outbox)
insight_feed(id pk, creator_id, type text, category text, title text, description text,
             content jsonb, chips jsonb, dedup_hash text unique, delivered boolean,
             conversation_seed jsonb, created_at)
-- metrics_ts: post/account metric timeseries (the genuinely new build)
metrics_ts(id, creator_id, entity_type text check (post|account), entity_id text,
           metric text, value numeric, source text check (postforme|apify|ig_graph),
           captured_at timestamptz)  -- index (creator_id, entity_id, metric, captured_at)
-- watermarks: first-run baseline discipline (Palo recent_channel_metrics pattern)
metric_watermarks(creator_id, key text, value numeric, updated_at, pk(creator_id, key))
-- ai_usage: per-call cost accounting (port BEFORE features; Palo gates live here)
ai_usage(id, creator_id, operation text, model text, input_tokens int, output_tokens int,
         cost_usd numeric, created_at)
-- prompt_overrides: §3
prompt_overrides(key text pk, prompt_text text, updated_at)
```

---

## 5. Phases (dependency-ordered)

Branch: `palo-port`. Every phase = flag default-OFF, failing-test-first, keyless-green, Ralph-gated (§8), local commits only, owner deploys.

### Phase 0 — Shared infra seams (~2-3 days) `PALO_PORT=off master flag`
1. Copy `llm/retry.py` + `llm/anthropic_cache.py` (`<<<CACHE_BREAKPOINT>>>` splitter) into `backend/app/`; add cache_control block support to the `anthropic()` helper.
2. Copy `prompt_assembly.py` (`{STRATEGY_*}` slicer + `infer_creator_tier`) → `app/prompt_assembly.py`; extend `app/knowledge.py` with doctrine loader's named-block byte-stable assembly; drop doctrine v1.4 md files into `backend/knowledge/`.
3. All §4 migrations + persistence helpers. Enable pgvector.
4. `app/prompt_store.py` (§3). 5. `ai_usage` accounting wrapper around `anthropic()` + per-creator allowlist kill-switch env (`STRATEGY_ALLOWLIST`, default empty = compiles OFF) — port Palo's cost gates FIRST, not as an afterthought.

### Phase 1 — Memory + ledger + overlay (~2-3 days) `MEMORY_V2=off`
Copy near-verbatim: `memory/extractor.py`, `memory/retriever.py`, `memory/cue_detector.py`, inline vector_service reconcile (ADD/UPDATE/DELETE/NOOP), `recall/ledger.py` (stdlib ULID), `strategy/live_updates.py` → `app/memory_v2.py`, `app/recall_ledger.py`. Pinecone→pgvector; gpt-4o-mini per §2 (or HAIKU behind the same constant). Wire fire-and-forget post-turn hooks into `/v1/converse` + script gen (pattern: `interaction_agent/handler.py:655-745`). Keep: weighted ranking (0.55 sim + 0.25 conf + 0.20 recency), channel-scope filter, insights-banned-from-memory rule ("Strategy owns them"). Replaces flat `/v1/memory/distill`.

### Phase 2 — Idea layer / reel suggestions (~3-4 days) `IDEA_BANK=off`
`app/ideas.py`: onboarding idea prompts + IdeaSet + eval gate; spitfire Generator→Critic→Editor→Ranker (copy `components/prompts.py` + `nightly_utils.py` parser verbatim; strip Vertex/Azure zoo → §2 constants) writing to `briefs`; pulse `judge.py` + `ideate_rank.py` as internal functions feeding `/v1/feed` (upgrades reasoned feed: bandit arms rank, briefs supply substance, judge filters). Run via `_spawn()` after dossier/scan events + nightly Render cron route `/internal/cron/ideate`.

### Phase 3 — Post-performance learning (~1 wk + pollers) `TRACK_INSIGHTS=off`
`app/metrics_pollers.py` (the genuinely new build): 
- **3a Post for Me:** poll per-account post analytics for everything published through it → `metrics_ts` (verify actual granularity/latency against their API early — scoping risk).
- **3b Apify:** daily own-profile scrape (reuse existing Apify client) for per-post views on posts NOT published via Post for Me.
- **3c IG Graph API:** official insights via creator's connected business account (needs FB app review — longest lead time; start the review in week 1, land last).

`app/track_insights.py` (transliterate Go → Python; the IP is prompts + thresholds + dedup discipline): milestone ladders, record-baseline-first-run watermarks (`metric_watermarks`), median+MAD / two-confirmed-reads ≥2.5x spike math (`metrics_truth`), underperformer skip (<0.05/0.10 ratio short-circuits before any LLM call), Insight Discovery Engine prompt → cards into `insight_feed`, byte-identical dedup_hash, ≤50-recent anti-repetition context. Daily cron. Feeds: (a) APNs via `app/push.py`, (b) bandit `outcome_y` settlement (replaces manual `MetricsEntrySheet` as primary source — keep manual entry as fallback), (c) insight→chat seeding (§6).

### Phase 4 — Strategy compiler / self-learning brain (~1-2 wks) `STRATEGY_COMPILER=off`
`app/strategy_compiler.py`: two-pass Sonnet EVIDENCE_PACK digest → Opus synthesis (copy `strategy/compiler.py` design: section splitter, cache boundary, UPSERT-revision loader). **Feed it Marque dossiers** via one adapter: `dossier_to_analysis_block()` producing the text shape `refresh_cron._raw_video_block` expects — this adapter is the #1-risk mitigation (§9.1) — plus `metrics_ts` lift data + Brand Graph identity. Weekly Render cron, gated: allowlist + paying + freshness + per-stage `ai_usage` rows. Then inject `{STRATEGY_*}` into script gen + converse (Phase 0 slicer). `strategy_updates` overlay keeps it current between compiles.

### Phase 5 — Interactive write agent (~1 wk) `WRITE_AGENT=off`
`app/write_agent.py`: WritePyro re-expressed as a plain Anthropic tool-use loop (~1-2 days; single agent + tools, no graph): DEFAULT_WRITE_PROMPT with fill/edit/answer branching, `<edit>/<add>/<highlight>` XML contract (exact-substring old_text; client applies diffs — maps directly onto TweakChatSheet's typed-ops precedent), `<fill>/<reasoning>` incremental stream parsing, discovery tools stubbed (prompt degrades to "Not available"). Deliberately AFTER Phases 1+4: without strategy_markdown + memories + exemplars the prompt yields generic output. Onboarding `script_generation.py` (~240 lines, near drop-in once brief/channel_identity → Brand Graph fields) can land early in this phase to upgrade `/v1/scripts`.

### Phase 6 — Exemplar bank (optional, after everything) `EXEMPLAR_BANK=off`
Retrieval/index first (`exemplar_bank/retrieval.py` + `exemplar_tool.py` are framework-free — copy; works with a hand-seeded bank), then rewrite `extract.py` against Marque's dossier schema (its regexes assume Palo's td-YAML — the biggest semantic dependency), then 5-Opus build + daily SCAN→DECIDE→APPLY refresh. Compiler runs fine with `pattern_library=""` until this lands.

---

## 6. New features (beyond the four pillars — Palo capabilities Yunicorn lacks)

1. **Insight→Chat deep links** (Palo's proactive track feed): every `insight_feed` card carries a `conversation_seed`; tapping it (or the push) opens ChatView pre-seeded ("Your reel about X just 3x'd your average — want 3 follow-up ideas?"). Phase 3 backend + P7 iOS. *This is Palo's flagship loop; highest leverage per effort.*
2. **Weekly Pulse briefing** (`pulse/briefing.py`): Sunday digest push — what worked, what's next, one strategic bet. Phase 3.5, S effort.
3. **Reel Review** (Palo Creative Review, `palo_python/creative_review/`): AI critique of a specific reel (hook, pacing, payoff, retention risks) — Palo's onboarding "wow moment," here as a Library/detail action on any analyzed reel. M effort, reuses dossier.
4. **"Your Strategy" surface**: render `strategy_markdown` in-app (evolve PlanBuildingView) — the creator SEES the compounding brain, Palo's stickiest retention artifact. Phase 4 + P7.
5. **Recommendation ledger** (invisible quality): Yunicorn never re-pitches the same idea twice. Phase 1.
6. **Doctrine-grounded scripts**: retention model (Promise/Confirmation/Continuation/Payoff), CRAFT-01/HOOK-01 diagnostics, annotated canon — merged into KB v2 so ALL generation shares one craft spine. Phase 0.
7. **Onboarding niche/vision chat** (later): Palo's `onboarding-prompt-*` conversational steps (niche discovery → vision selection → confirmation) to upgrade Yunicorn onboarding v2 with the "friend with good taste" persona.

## 7. iOS phases (P7, per backend phase)

- **P7.2 (feed v2):** HomeView idea cards render brief beginning/middle/end + "why picked" (judge rationale); ReelDetailSheet "Make this mine" → brief→script.
- **P7.3 (insights):** PerformanceView gets an insight-card feed (type-colored, chips); push taps deep-link ChatView pre-seeded from `conversation_seed`; MetricsEntrySheet demoted to fallback with "auto-tracked" states.
- **P7.4 (strategy):** PlanBuildingView → "Your Strategy" (rendered markdown sections, revision history, "what changed this week").
- **P7.5 (write agent):** ScriptReaderView gains edit-chat (TweakChatSheet pattern) applying `<edit>/<add>` ops with accept/reject, streaming fills.
- **P7.6 (reel review):** Library/ReelDetailSheet "Get Palo's review" card.

API contracts: each backend phase ships typed request/response models in `main.py` + a `docs/api/PALO_PORT.md` contract file so iOS work can proceed against mocks.

## 8. Ralph loops (gating the port)

Reuse Marque's native substrate (`backend/PROMPT_FIX.md` contract + `scripts/gate.sh` + `eval/`). Do NOT port the goose/Docker ralph-harness.

- **LOOP P — Port Backlog Loop:** `backend/PROMPT_PORT.md` + `BACKLOG_PORT.md` cloning PROMPT_FIX.md's contract: pick first unchecked item (matrix rows broken into single-module units), failing test first, keyless `python -m pytest -q`, check off with evidence, local commit. Completion promise: **"YUNICORN PORT GREEN"**. Pass: all 844 existing + new tests green keyless.
- **LOOP G — Golden Parity Loop** (`backend/eval/port_eval.py`, modeled on `eval/edl_eval.py`): deterministic fixtures recorded from Palo behaviors (memory reconcile decisions, ledger extraction, spitfire parser, milestone/watermark math, metrics_truth spike verdicts, strategy section-splitter) replayed through ported code, asserting parity. `--live` tier: Sonnet judge (steal `palo-ralph-bot/palo_ralph_bot/judge.py`'s 6-binary-item swap-and-average rubric → `eval/port_judge.py`) scoring ported-prompt outputs ≥0.90; exit 2 when key missing (never silently downgrade). Wire as `port` stage in `gate.sh --fast/--paid`.
- **LOOP W — Writer Quality Loop** (extend `eval/run_eval.py`): XML contract invariants (exact-substring applies cleanly, ≤250 words, exactly-one-branch, no doctrine-vocabulary leakage regex firewall) + paid Sonnet judge vs retention rubric + voice-match. Pass: 100% deterministic, ≥0.90 judged. Lands with Phase 5.
- **LOOP I — Insight Discipline Loop** (`eval/insight_eval.py`): the three gotchas Palo shipped bugs on — first-run baseline fires ZERO insights on day-1 synthetic history; byte-identical dedup blocks repeat cards; underperformer skip short-circuits before any LLM call (mock-call counter). Plus median+MAD spike fixtures. Keyless, `--fast`. Lands with Phase 3.
- **LOOP C — Cost & Mock Loop:** every ported module gets (a) zero-key test asserting `mode:"mock"` + no vendor call, (b) call-budget test (compile ≤2 heavy calls, ideate chain ≤4, judge ≤1) via mocked-client counter, (c) gate.sh secret-scan failing on any Palo landmine credential in the diff. Lands Phase 0.

Wiring order: C+P (Phase 0) → G grows per phase → I (Phase 3) → W (Phase 5).

## 9. Risks (ranked)

1. **Quality inputs don't exist yet (#1).** Palo's writer/compiler/bank feed on rich per-video analyses + strategy_markdown. Yunicorn's dossiers are sparser + schema-different. Porting code is days; producing equivalent inputs is the real project. **Mitigation: the `dossier_to_analysis_block()` adapter in Phase 4, built early, tested in LOOP G.**
2. **Go→Python transliteration** (track_insights, metrics_truth, pulse router): subtle behaviors (watermark-first-run, settle latch, content dedup) are exactly where Palo shipped bugs — failing-test-first mandatory (LOOP I exists for this).
3. **IG-only metrics reality.** Post for Me granularity/latency unproven; Apify rate/cost-bounded; IG Graph needs FB app review. Some Palo insight classes (hour-granular deltas, subscriber milestones) unbuildable v1 — scope insight types to one-daily-read.
4. **Render + in-process jobs:** Opus compiles (~minutes) and nightly ideate must be chunked + resumable from tables; a deploy mid-job must not lose work.
5. **Cost:** compile ~$1.60-2.40/channel/week, bank build ~5 Opus calls, nightly 4-LLM ideate. Gates (allowlist default-empty, paying, freshness, `ai_usage`) land in Phase 0, LOOP C enforces budgets.
6. **De-LangChaining changes behavior:** stream framing, mid-stream retry, `<fill>` parsing each need explicit tests or iOS sees malformed frames.
7. **Prompt-governance regression:** LD live-tuning lost; `prompt_overrides` shim covers the hot prompts.
8. **Secrets landmines — NEVER copy:** hardcoded Moonshot/Kimi key in `src/application/track_insights/proactive_service.go`; staging DATABASE_URL + RapidAPI key defaults in `niche_pipeline/*.py`. LOOP C greps for these.

## 10. Rough timeline

P0 2-3d → P1 2-3d → P2 3-4d → P3 ~1wk (+pollers trailing) → P4 1-2wk → P5 ~1wk → P6 optional. Phases 2/3 parallelizable after P0-P1; iOS P7.x trails each backend phase by its flag-ON. **~6-8 weeks solo to P5 complete; first user-visible value (memory + idea bank behind flags) inside week 2.**

## 11. Immediate next steps

1. Owner: provision §2 keys (Azure OpenAI, Vertex, OpenAI embeddings; Perplexity optional) into `backend/.env` + Render env; enable pgvector on Supabase; start FB app review for IG Graph.
2. Create `palo-port` branch; land Phase 0 (migrations + infra copies + LOOP C/P harnesses).
3. Kick LOOP P with `BACKLOG_PORT.md` seeded from §1's matrix rows.

## 12. Status + go-live checklist (updated post-hardening)

**Done:** Phases 0–6 built + a 7-commit production-hardening pass (`palo-port`, ~28 commits,
all flags OFF, keyless-green, 984 tests, NOT deployed). See `backend/BACKLOG_PORT.md`
"Production hardening" for the audit-driven fixes. Local venv is Python 3.14 vs the
Dockerfile's 3.12 — the ported code is 3.9+-safe (no Dockerfile change).

**To go live (owner, one capability at a time):**
1. Apply the `migrations.sql` PALO PORT block; **enable the pgvector extension** on Supabase first.
2. Provision §2 keys into Render env; set `INTERNAL_CRON_TOKEN` + (per capability) the flags.
3. The Render cron services (`render.yaml`) POST the sweep endpoints — set `MARQUE_API_URL`
   + `INTERNAL_CRON_TOKEN` on each cron; they no-op until flags are on.
4. Populate `creators.handle` (the app now persists it at `/v1/posts/register`) before
   `TRACK_INSIGHTS` can collect metrics; keep `STRATEGY_ALLOWLIST` empty until you opt creators in.
5. Flip `PALO_PORT=1` + one capability flag; deploy manually. Watch `ai_usage`.
6. iOS P7.2–P7.6 build against `backend/docs/api/PALO_PORT.md` (incl. `GET /v1/insights`,
   `GET /v1/strategy`).
