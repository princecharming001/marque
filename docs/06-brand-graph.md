# 06 — The Brand Graph (Context Layer): Data Model & Lifecycle

> **Status:** Build spec, v1.
> **Owners:** Backend / Data (schema, RLS, versioning), AI (context assembly, prompt caching), iOS (the "What Marque knows" view).
> **Sibling docs:** `01-information-architecture.md` (adapters, stack), `06-brand-graph.md` (observed-identity source), `07-ai-system.md` (script writer, voice check, Claude wiring), `08-format-virality.md` (render-recipes & format affinity), `08-format-virality.md` (cultural memory source), `05-screens-produce.md` (performance memory writer), `10-social-publishing.md` (Ayrshare/Phyllo pullback), `14-appstore-compliance-legal.md` (Apple/GDPR posture), `02-design-system.md` (cream/serif aesthetic tokens).

---

## 1. What the Brand Graph is (and what it is not)

The **Brand Graph** is Marque's persistent **context layer** — the single source of truth for everything the product believes about a creator. It is the substrate that makes the core loop coherent: it is read by the script writer so scripts sound like *them*, read by the format selector so recipes fit *their* audience, and written back to by the performance loop so the system gets sharper every week. It is also the differentiator the whole product rests on: a competitor can call an LLM; only Marque accumulates a durable, versioned, user-owned model of the creator.

It is **four input streams compiled into one fact store**:

| Layer | `source_layer` | What it captures | Written by | Authority |
|---|---|---|---|---|
| **Stated identity** | `stated` | What the creator *says* they're about — onboarding answers, chat corrections, hard rules ("never swears"), goals, "what do you want to be known for?" | Onboarding, chat, the editable view | User-asserted ground truth |
| **Observed identity** | `observed` | What their existing page *shows* — niche, posting patterns, and a structured **Voice Fingerprint** (measured, enforceable) | Page ingestion (`06-brand-graph.md`) | Measured / computed |
| **Performance memory** | `performance` | What *works for this specific creator* — format affinity, hook affinity, best post time, pillar performance | Insights loop (`05-screens-produce.md`) | Derived, with lineage |
| **Cultural memory** | `cultural` | Which trends fit *this* creator; whether they rode a trend | Trend Radar (`08-format-virality.md`) | Derived link onto shared knowledge |

It is **not** a JSON blob on the user row. The single most load-bearing architectural decision in this document is that the Brand Graph is a **bitemporal, append-only fact store** in Supabase Postgres, with a single canonical "current view" materialized for fast reads and for injection into Claude. A blob loses history; this store keeps it — which is exactly what the learning loop, the audit trail, GDPR disclosure, and stated-vs-observed conflict resolution all require ([CortexDB bi-temporal](https://cortexdb.ai/docs/concepts/bi-temporal); [Bitemporal Versioning — MATIH](https://docs.matih.ai/14-context-graph/storage/bitemporal/)).

### Anti-clutter note
None of this surfaces on **Today**. The Brand Graph powers Today's single directive silently. Its only first-class UI is the calm, one-layer-deep **"What Marque knows about you"** screen (§8), reached from Profile — never bolted onto the home screen.

---

## 2. The spine: bitemporal, append-only, three clocks

Every fact carries **three independent clocks**. This is non-negotiable and underpins §5 (versioning) and §7 (conflict resolution).

1. **Valid time** — `valid_from` / `valid_to`: *when the claim is true about the creator in the real world.* (Their niche changed in March → the old niche fact's `valid_to` is March.)
2. **System time** — `recorded_at` / `superseded_at`: *when Marque learned or stopped believing it.* `superseded_at IS NULL` means the row is live.
3. **Evidentiary time** — `last_verified_at`: *when the claim was last re-confirmed* by fresh posts or by the user. A fact re-confirmed yesterday is more trustworthy than a stale one with the same `recorded_at`; this drives confidence decay ([Temporal Reasoning & Provenance — Bansal](https://jatinbansal.com/ai-engineering/temporal-reasoning-provenance/)).

**Rule: never overwrite — supersede.** On update, set `superseded_at = now()` on the old row and insert a new row with the same `fact_id`, incremented `version`. This is Slowly-Changing-Dimension Type 2, done atomically in one transaction via a Postgres trigger ([Temporal Versioning — Z3rno](https://astron-bb4261fd.mintlify.app/concepts/temporal-versioning)).

**Why bitemporal and not single-axis:** a single-axis store cannot answer "what did Marque believe about this creator last month," which is required for (a) the performance-memory learning loop, (b) auditing *why* a given script was written, (c) GDPR "what do you hold and when did you learn it," and (d) reconciling stated vs observed claims ([CortexDB](https://cortexdb.ai/docs/concepts/bi-temporal); [Bitemporal Edges — javatask.dev](https://javatask.dev/blog/bitemporal-edges-agent-memory/)).

**Recall modes** the store must support ([MATIH](https://docs.matih.ai/14-context-graph/storage/bitemporal/)):
- `CURRENT` — the live view (default; powers every AI read). Selects rows that are live in system time **and** valid in real-world time *right now*: `superseded_at is null and valid_from <= now() and (valid_to is null or valid_to > now())`. Both validity bounds are required — see §5.1; a fact with a future `valid_from` is not yet `CURRENT`.
- `AS_OF(t)` — time-travel to a past (or future) system or valid time (audit, learning, "why did you write this"). The valid-time interval test parameterizes `now()` to `t`: `valid_from <= t and (valid_to is null or valid_to > t)`, combined with the system-time test for the chosen transaction instant. The same closed-open `[valid_from, valid_to)` interval semantics used by `CURRENT` apply at every `t`.
- `HISTORY(fact_id)` — the full supersession chain for one fact (the edit history shown in §8).

---

## 3. Data model

We use the Supabase-recommended **hybrid structured + `jsonb`** pattern: queryable/RLS/indexable fields get real columns; open, evolving structure lives in `jsonb` ([Structured vs Unstructured — Supabase](https://github.com/supabase/supabase/blob/master/apps/docs/content/guides/ai/structured-unstructured.mdx)). All four layers live in **one** table, discriminated by `source_layer`, so there is one merge path, one versioning path, one RLS policy, one read.

### 3.1 `brand_facts` — the core bitemporal fact store

```sql
create type source_layer as enum ('stated', 'observed', 'performance', 'cultural');
create type fact_status  as enum ('live', 'superseded', 'stale', 'needs_reverify');

create table public.brand_facts (
  row_id         uuid primary key default gen_random_uuid(),   -- unique per VERSION
  fact_id        uuid not null default gen_random_uuid(),      -- stable LOGICAL identity
  version        int  not null default 1,                      -- increments per fact_id
  creator_id     uuid not null references auth.users(id) on delete cascade,

  source_layer   source_layer not null,
  predicate      text not null,        -- e.g. 'niche', 'known_for', 'voice.cadence', 'format_affinity'
  object         jsonb not null,       -- the value: scalar or structured

  confidence     numeric(3,2) not null default 0.50 check (confidence between 0 and 1),
  provenance     jsonb not null default '{}'::jsonb,  -- see §3.4
  conflict_policy text,                -- per-predicate override of §7 default precedence

  -- valid time (real world)
  valid_from     timestamptz not null default now(),
  valid_to       timestamptz,                              -- null = currently true

  -- system time (when Marque believed it)
  recorded_at    timestamptz not null default now(),
  superseded_at  timestamptz,                              -- null = live row

  -- evidentiary time
  last_verified_at timestamptz not null default now(),

  status         fact_status not null default 'live',
  embedding      vector(1024),                             -- canonical: Voyage voyage-3.5 @ 1024 (owned by 07-ai-system.md §6.3)

  constraint uq_fact_version unique (fact_id, version)
);

alter table public.brand_facts enable row level security;
```

### 3.2 Indexes

```sql
-- Hot path: the "current state" read. Partial index keeps it tiny & fast.
create index idx_bf_current
  on public.brand_facts (creator_id, source_layer, predicate)
  where superseded_at is null;

-- Time-travel (AS_OF / HISTORY) over real-world validity.
create index idx_bf_validrange
  on public.brand_facts using gist (tstzrange(valid_from, valid_to));   -- [Z3rno]

-- Semantic recall of facts/exemplars into a prompt.
create index idx_bf_embedding
  on public.brand_facts using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);                                  -- [Supabase pgvector]

-- Provenance walk-up (mark dependent facts stale on correction, §5).
create index idx_bf_provenance_episodes
  on public.brand_facts using gin ((provenance -> 'source_episode_ids'));
```

> **pgvector pitfall (must respect):** filtering an HNSW search by a column (e.g. `where source_layer = 'observed'`) can return **fewer rows than `LIMIT`**, because the index returns N then the filter prunes. Over-fetch then filter, or use iterative search ([Supabase pgvector](https://supabase.com/docs/guides/ai/vector-columns)).

### 3.3 `object` shape per layer (selected predicates)

`object` is `jsonb`; here is the contract per layer. Full predicate registry lives in `src/brand-graph/predicates.ts`.

**Stated** — `confidence` ≈ 0.90–1.00 (user-asserted):
```jsonc
// predicate: 'known_for'
{ "value": "honest fitness advice for busy parents" }
// predicate: 'non_negotiables'  (hard rules — these become Voice Fingerprint constraints too)
{ "rules": ["never swears", "no hashtags in caption", "never uses engagement-bait CTAs"] }
// predicate: 'goal'
{ "value": "10k IG followers by Q4", "horizon": "2026-Q4" }
```

**Observed** — see §4 for the full Voice Fingerprint:
```jsonc
// predicate: 'niche'
{ "value": "parent-fitness", "labels": ["fitness","parenting","wellness"] }
// predicate: 'posting_cadence'
{ "per_week": 4.2, "by_dow": {"mon":0.8,"tue":1.1,"...":0} }
```

**Performance** — derived, lineage-bearing:
```jsonc
// predicate: 'format_affinity'   (which render-recipes overperform for THIS creator)
{ "format_id": "split_screen_react", "lift": 1.34, "n": 9, "metric": "median_3s_retention" }
// predicate: 'best_post_time'
{ "platform": "instagram", "windows": [{"dow":"tue","hour":18}], "lift": 1.2 }
```

**Cultural** — a *creator-specific link* onto a shared trend (the trend itself lives elsewhere, §3.5):
```jsonc
// predicate: 'trend_fit'
{ "trend_id": "trnd_8f...", "fit_score": 0.82, "rationale": "matches parent-fitness niche" }
// predicate: 'trend_ridden'
{ "trend_id": "trnd_8f...", "published_episode_id": "ep_...", "outcome_ref": "perf_..." }
```

### 3.4 `provenance` contract

Every fact must be able to explain itself. This powers the "where this came from" chip in §8, the lineage walk-up in §5, and GDPR disclosure.

```jsonc
{
  "extracted_by": "page-ingestion@1.4.0",          // service + version
  "model": "claude-opus-4-8",                        // or 'claude-haiku-4-5', or 'user'
  "prompt_version": "voice-extract-v3",
  "source_episode_ids": ["ep_a1", "ep_b2"],          // posts/clips this was derived from
  "source_permalinks": ["https://instagram.com/p/..."],
  "asserted_by_user": false                          // true for stated/edited facts
}
```

For **stated** facts from the user, `provenance.asserted_by_user = true`, `model = "user"`. For **performance** facts, `source_episode_ids[]` is **mandatory** — it is the reverse index that lets a corrected metric invalidate dependent learned facts ([Bansal — provenance walk-up](https://jatinbansal.com/ai-engineering/temporal-reasoning-provenance/)).

### 3.5 `trends` — shared knowledge (deliberately NOT per-creator)

The key design distinction: **knowledge is true about the world (shareable, no per-user clock); memory is true about a specific creator (per-user, constantly changing)** ([CortexDB](https://cortexdb.ai/docs/concepts/bi-temporal)). Trends are mostly shared knowledge. Storing the full trend graph per creator would bloat the store badly. So:

- The **trend catalog** lives in a global, deduplicated `public.trends` table, readable by all authenticated users (the *only* deliberate RLS exception, §8.5).
- Only the **creator-specific link** ("this trend fits creator X", "creator X rode it") lives in `brand_facts` as a `source_layer = 'cultural'` fact pointing at `trends.trend_id`.

```sql
create table public.trends (
  trend_id     uuid primary key default gen_random_uuid(),
  slug         text unique not null,
  title        text not null,
  surface      text not null,                 -- 'instagram' | 'tiktok' | 'cross'
  format_hints jsonb default '[]'::jsonb,      -- maps to render-recipes in 08-format-virality.md
  momentum     numeric,                        -- velocity score
  embedding    vector(1024),                   -- canonical dim — see 07-ai-system.md §6.3
  first_seen_at timestamptz default now(),
  last_seen_at  timestamptz default now()
);
alter table public.trends enable row level security;
-- shared-read policy in §8.5
```

### 3.6 The supersession trigger (atomic SCD-2)

```sql
create or replace function public.brand_facts_supersede()
returns trigger language plpgsql as $$
begin
  -- For an UPDATE issued as "supersede": close the prior live row and stamp a new version.
  update public.brand_facts
     set superseded_at = now(), status = 'superseded'
   where fact_id = new.fact_id
     and superseded_at is null
     and row_id <> new.row_id;

  new.version := coalesce(
    (select max(version) from public.brand_facts where fact_id = new.fact_id), 0
  ) + 1;
  return new;
end $$;

-- Application inserts a NEW row reusing fact_id; the trigger closes the old one.
create trigger trg_bf_supersede
  before insert on public.brand_facts
  for each row when (new.version is distinct from 1 or
                     exists (select 1 from public.brand_facts b
                             where b.fact_id = new.fact_id and b.superseded_at is null))
  execute function public.brand_facts_supersede();
```

> **GDPR caveat:** a hard delete must remove **all versions** of a `fact_id`, not just the live row. Soft-delete only when there's a compliance reason to retain ([Z3rno](https://astron-bb4261fd.mintlify.app/concepts/temporal-versioning)). See §8.4.

---

## 4. The Voice Fingerprint (the heart of `observed`)

The Voice Fingerprint is the structured object that makes scripts sound like the creator. The cardinal rule, shared by every serious 2025–2026 voice tool: it is **explicit, measured, and enforceable — never a vibes paragraph.** A free-text "tone = witty and bold" is not enforceable and not driftable; a scored axis with cited evidence is both ([GhostLoop](https://ghostloop.io/blog/how-ghostloop-learns-your-voice); [whystrohm voice-extract](https://whystrohm.com/resources/skills/whystrohm-voice-extract)).

It is written by page ingestion (`06-brand-graph.md`) as a set of `observed` facts, and read by the script writer and the Haiku voice check (`07-ai-system.md`).

### 4.1 Schema

Stored as `observed` facts under `voice.*` predicates, plus one composite `voice.fingerprint` fact carrying the whole object for convenient injection.

```jsonc
{
  "channel": "core",                 // 'core' | 'instagram' | 'tiktok' — see §4.2
  "axes": {                          // 0–100, EACH with cited evidence
    "authority":        { "score": 72, "evidence": ["ep_a1","ep_c3"] },
    "emotional_temp":   { "score": 64, "evidence": ["ep_b2"] },   // warm↔cold
    "proof_density":    { "score": 81, "evidence": ["ep_a1"] },   // specifics vs vague
    "cadence":          { "score": 38, "evidence": ["ep_d4"] },   // punchy↔flowing
    "vocabulary_range": { "score": 45, "evidence": ["ep_b2"] },   // accessible↔technical
    "rhetoric":         { "score": 55, "evidence": ["ep_c3"] },   // narrative↔argumentative
    "humor":            { "score": 60, "evidence": ["ep_e5"] },
    "provocativeness":  { "score": 48, "evidence": ["ep_e5"] }
  },
  "hard_constraints": {              // the "never do" rules — as load-bearing as the "always" rules
    "banned_words":    ["leverage","unlock","game-changer","crush it"],  // 15+ typical
    "banned_openers":  ["In today's video","Hey guys so"],
    "banned_closers":  ["Smash that like button"],
    "signature_phrases": ["here's the honest version","do the boring thing"],
    "hook_types":      ["contrarian-claim","numbered-payoff","myth-bust"],
    "anti_patterns":   ["no hashtags","no emojis mid-sentence","no engagement-bait CTAs"]
  },
  "stylometry": {                    // the QUANTITATIVE drift detector
    "sentence_len": { "mean": 11.4, "cv": 0.62 },   // burstiness = coefficient of variation
    "punctuation":  { "em_dash_rate": 0.08, "question_rate": 0.21 },
    "function_word_z": { "i": 1.2, "you": 2.1, "the": -0.4 },
    "contraction_rate": 0.74
  },
  "summary": "Direct, evidence-first, warm but not soft. Short punchy sentences, contrarian hooks, never sells.",
  "source_episode_ids": ["ep_a1","ep_b2","ep_c3","ep_d4","ep_e5"]
}
```

- **Axes** (0–100) with cited evidence per score — grounds every claim in real posts ([whystrohm](https://whystrohm.com/resources/skills/whystrohm-voice-extract); [GhostLoop](https://ghostloop.io/blog/how-ghostloop-learns-your-voice)).
- **Hard constraints** — the "never do" rules are **as important as** the "always do" rules; generic AI fails precisely by ignoring what a creator avoids ([GhostLoop](https://ghostloop.io/blog/how-ghostloop-learns-your-voice)). The creator's stated `non_negotiables` (§3.3) merge into `hard_constraints` (stated wins, §7).
- **Stylometry** — sentence-length distribution, burstiness, function-word z-scores, punctuation signature give a *quantitative* drift detector and a deviation report ([stylometric-transfer](https://github.com/ngpepin/stylometric-transfer)).

### 4.2 Per-channel fingerprints

Voice differs by surface, so Marque keeps **separate fingerprints per platform** — an Instagram fingerprint, a TikTok fingerprint, and a merged **`core`** voice ([GraceAI Fingerprint](https://getgrace.ai/features/fingerprint); [whystrohm](https://whystrohm.com/resources/skills/whystrohm-voice-extract)). The format/recipe selector and script writer pick the channel matching the clip's target platform; `core` is the fallback.

> **TikTok ingestion caveat (from `06-brand-graph.md`):** there is no official commercial read path to ingest a creator's own TikTok page (the Research API is academic/non-commercial; Display & Content Posting are write/display only). The TikTok fingerprint therefore leans on Instagram observation + user-authorized scopes + the repurpose-upload path, not on scraping ([TikTok scraping guide 2026](https://scrapebadger.com/blog/tiktok-scraping-apis-in-2026-the-complete-deep-guide)).

### 4.3 Dual representation
The fingerprint is stored **twice in spirit**: a human-readable `summary` (injected into prompts, shown in §8) and machine-searchable `embedding`s for RAG/few-shot exemplar recall ([GraceAI](https://getgrace.ai/features/fingerprint)).

---

## 5. Lifecycle: assemble → merge → update → version

### 5.1 Assembly — the Brand Graph Context Pack

What every AI feature reads is **not** raw rows — it's a compiled, deterministic **Context Pack**. It is produced by `brand_context_view`, a Postgres view that selects live, currently-valid facts and resolves conflicts (§7):

```sql
create view public.brand_context_view
  with (security_invoker = true) as          -- ⚠ MANDATORY on PG15+, see warning below
select creator_id, source_layer, predicate, object, confidence, provenance, last_verified_at
from public.brand_facts
where superseded_at is null
  and valid_from <= now()                       -- ⚠ lower bound: a future-dated fact is not yet true
  and (valid_to is null or valid_to > now())     -- upper bound: still true in the real world
  and status in ('live');
```

> **⚠ View-bypasses-RLS gotcha (easy, dangerous miss):** Postgres views run with the view-owner's permissions and **bypass the underlying table's RLS by default.** On Postgres 15+ the view **MUST** be created with `security_invoker = true` so it obeys `brand_facts`'s RLS for the `authenticated` role ([RLS — Supabase](https://supabase.com/docs/guides/database/postgres/row-level-security)).

The serialized Context Pack:
- selects live + **currently-valid** facts — the full validity-interval test `valid_from <= now() and (valid_to is null or valid_to > now())`, **both bounds**. The lower bound is not optional: §2 defines `valid_from` as *when the claim is true in the real world*, so future-dated facts are an expected, first-class case (a stated future goal like `goal.horizon = '2026-Q4'`, or any fact inserted with forward-dated validity). Omitting `valid_from <= now()` would leak a not-yet-true fact into the "current" pack and serialize it into Claude as a present-tense belief — e.g. injecting an unmet Q4 goal as a fact already true today. Both bounds are checked everywhere a "current" state is read;
- applies the §7 precedence table, drops/down-weights low-confidence stale facts (§5.4);
- is **serialized deterministically** — facts sorted by `predicate`, stable key order — so the cache key is stable;
- carries a `context_version` hash that bumps **only on real change** (so Claude's prompt cache isn't needlessly invalidated, §6).

> **Realtime caveat:** Supabase Realtime caps a broadcast row at **1 MB** — do not shove a full Context Pack into a single realtime row ([Supabase + pgvector for AI Agents](https://callsphere.ai/blog/vw7h-supabase-pgvector-ai-agents-2026)). Push a `context_version` change notification; clients refetch.

### 5.2 Semantic recall RPC
PostgREST can't call pgvector operators directly, so recall of relevant facts/exemplars into a prompt is wrapped in a `plpgsql STABLE` function called via `.rpc()` ([Supabase pgvector](https://supabase.com/docs/guides/ai/vector-columns)):

```sql
create or replace function public.match_brand_facts(
  query_embedding vector(1024),                  -- canonical dim — see 07-ai-system.md §6.3
  match_creator   uuid,
  match_count     int default 8,
  layer_filter    source_layer default null
) returns table (predicate text, object jsonb, similarity float)
language sql stable as $$
  select predicate, object, 1 - (embedding <=> query_embedding) as similarity
  from public.brand_facts
  where creator_id = match_creator
    and superseded_at is null
    and valid_from <= now()                          -- same current-interval test as brand_context_view
    and (valid_to is null or valid_to > now())        -- never recall a future-dated or expired fact
    and (layer_filter is null or source_layer = layer_filter)
  order by embedding <=> query_embedding
  limit greatest(match_count, match_count * 3)   -- over-fetch to dodge the post-filter under-fetch pitfall
$$;
```

### 5.3 Update / supersession state machine

```
[new evidence] ──► extract fact (Opus/Haiku, structured tool output)
                      │
                      ▼
            does a live fact exist for (creator_id, predicate, channel?)
              │ no                                   │ yes
              ▼                                       ▼
        INSERT v1 (live)                  changed materially?  ── no ──► UPDATE last_verified_at,
                                              │ yes                       bump confidence (re-confirmed)
                                              ▼
                                   INSERT new version (trigger closes old)
                                   old row → status='superseded', superseded_at=now()
```

### 5.4 Provenance walk-up & confidence decay
- **Walk-up on correction:** when an upstream episode is invalidated — a post deleted, a metric corrected by the Insights pull (`05-screens-produce.md`) — reverse-index via `provenance.source_episode_ids` and mark dependent derived (`performance`, `voice`) facts `stale` / `needs_reverify` rather than leaving a confidently-wrong belief live. This prevents "context rot" and "context collision" ([Bansal](https://jatinbansal.com/ai-engineering/temporal-reasoning-provenance/); [javatask.dev](https://javatask.dev/blog/bitemporal-edges-agent-memory/)).
- **Confidence decay:** facts not re-observed lose confidence over time, keyed off `last_verified_at` (the evidentiary clock). The Context Pack down-weights or drops low-confidence stale facts. Half-life is configurable (Open Q #3) ([Bansal](https://jatinbansal.com/ai-engineering/temporal-reasoning-provenance/)).

---

## 6. Feeding the Brand Graph into Claude

The Context Pack is injected into Claude (Opus 4.8 for scripts/teardowns; Haiku 4.5 for voice checks) via the AI adapter in `07-ai-system.md`. Prompt-cache correctness is frequently botched; these rules are mandatory.

- **Render order is `tools` → `system` → `messages`.** The cache is a prefix; any change before a token invalidates everything after it ([Prompt caching — Claude](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).
- **Put the stable Context Pack high, behind a cache breakpoint; put volatile content at the END** as message content (today's directive, timestamp, the specific clip). Caching saves up to **90% cost / 85% latency** on long prefixes ([Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).
- **Do NOT interpolate `current date`, creator name, or the day's directive into the top-level `system` prompt** — that sits at the front of the prefix and nukes the whole cache. Inject per-turn/per-session dynamic context as a later `{"role":"system",...}` message in `messages[]`, or as user-message text ([anthropics/skills caching guide](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/prompt-caching.md)).
- **Max 4 `cache_control` breakpoints.** Tier them by change frequency: global static (tool defs, base instructions) → per-creator Context Pack (changes only when a fact is superseded) → per-session → per-turn ([anthropics/skills](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/prompt-caching.md)).
- **Serialize deterministically** (§5.1) and bump `context_version` only on real change. Tool definitions render at position 0 — don't reorder or add tools mid-conversation or the cache dies ([anthropics/skills](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/prompt-caching.md)).
- **Structured outputs:** fact extraction (Voice Fingerprint, observed facts) comes back as a **validated JSON tool result**, not freeform. Use **Tool Use Examples** (1–5 realistic examples per tool) to lock parameter shape — JSON Schema alone can't express usage conventions ([Advanced tool use — Anthropic](https://www.anthropic.com/engineering/advanced-tool-use)).

> **Third-party-AI disclosure (Apple 5.1.2(i)):** sending creator content + brand data to Claude (and AssemblyAI, Shotstack) requires explicit user consent, see §8.4.

---

## 7. Conflict resolution: stated vs observed

A genuine product decision. The defensible default precedence:

| Predicate class | Examples | Winner | Rationale |
|---|---|---|---|
| **Identity & non-negotiables** | `known_for`, `niche` (intent), `goal`, `non_negotiables` ("never swears"), banned words | **stated > observed** | People are authoritative about their *intent* and *hard rules*. |
| **Descriptive style measurement** | `voice.cadence` CV, burstiness, actual hook distribution, posting cadence | **observed > stated** | People misjudge their own style; measured stylometrics are ground truth ([GhostLoop](https://ghostloop.io/blog/how-ghostloop-learns-your-voice); [whystrohm](https://whystrohm.com/resources/skills/whystrohm-voice-extract)). |

Rules:
1. **Surface high-salience conflicts; don't silently resolve them.** When observed strongly contradicts stated (e.g. user says "I'm formal" but measured authority/formality reads casual), raise a **reconcile card** in "What Marque knows" (§8): *"You said formal, but your last 30 posts read casual — which should I write in?"* The user's pick writes a **new high-confidence stated fact that supersedes**. This mirrors the bitemporal "contradiction event → operator picks a winner" pattern ([TOKI](https://arxiv.org/html/2606.06240)).
2. **Keep the loser — never delete it.** It goes to the superseded chain / audit, preserving the evidentiary trail ([TOKI](https://arxiv.org/html/2606.06240)).
3. **The policy is data, not code.** Per-predicate precedence lives in `brand_facts.conflict_policy` (and a `conflict_policy` defaults table), so it's tunable without a deploy. The exact full precedence table needs product sign-off (Open Q #4).

---

## 8. "What Marque knows about you" — the editable view

The only first-class Brand Graph UI. One calm screen, one layer deep from Profile, in the locked cream/serif aesthetic (`02-design-system.md`). It is the compliant, honest posture: the profile Marque builds is **overt and user-visible**, never surreptitious ([App Review Guidelines — Apple](https://developer.apple.com/app-store/review/guidelines/)).

### 8.1 Layout (one idea per section, generous whitespace)
1. **Voice in one paragraph** — the `summary` (§4.3), serif, large. ([whystrohm](https://whystrohm.com/resources/skills/whystrohm-voice-extract); [GraceAI](https://getgrace.ai/features/fingerprint))
2. **What you're known for** — stated `known_for`, inline-editable.
3. **Your voice, measured** — the 8 axes as quiet horizontal bars with the single gold accent at the score; tap an axis to see cited posts.
4. **Words you'd never use** — the `banned_words` / anti-patterns list, editable as chips.
5. **What's working** — a calm read of top `format_affinity` / `best_post_time` (no dashboards).
6. **Reconcile cards** — surfaced only when a §7 conflict exists.

Every fact shows a small **provenance chip**: *"you told me this"* vs *"learned from your last 30 IG posts."* Trust-building and honest.

### 8.2 Editing semantics
A user edit is **not** an in-place mutation. It writes a **new high-confidence `stated` fact that supersedes** the prior value (§3.6). This preserves history, lets the user revert via `HISTORY(fact_id)`, and bumps `context_version` so Claude picks up the change.

### 8.3 States

| State | Behavior |
|---|---|
| **Loading** | Skeleton of the section rails; serif title visible immediately; slow eased fade-in (no spinner-spam). |
| **Empty** (pre-ingestion) | "Marque is still getting to know you." Single CTA to connect a page or record. No fabricated facts. |
| **Error** (Context Pack fetch fails) | Last-good cached pack shown read-only with a quiet "couldn't refresh" line + retry. Never a blank screen. |
| **Offline** | Read-only cached pack; edits queue locally and supersede on reconnect (optimistic, reconciled against `context_version`). |
| **Permission-denied** (consent withdrawn / third-party AI declined, §8.4) | View renders from stated facts only; observed/derived sections show "paused — third-party analysis is off" with a re-enable toggle. Paid features are **not** gated on consent (Apple 5.1.1(ii)). |
| **Conflict pending** | Reconcile card pinned to top until resolved; AI reads fall back to stated for the disputed predicate. |

### 8.4 Privacy, consent & ownership
- **NEW Apple 5.1.2(i) (Nov 13 2025):** apps must **clearly disclose** where personal data is shared with third parties **including third-party AI**, and get **explicit permission before doing so.** Marque sends creator content + brand data to Anthropic (Claude), AssemblyAI, and Shotstack — all third-party processors that **must be disclosed and consented** at onboarding ([Apple Developer News](https://developer.apple.com/news/?id=ey6d8onl); [TechCrunch](https://techcrunch.com/2025/11/13/apples-new-app-review-guidelines-clamp-down-on-apps-sharing-personal-data-with-third-party-ai/)).
- **5.1.1:** privacy policy states retention + deletion; consent required even for "anonymous" data; easy consent-withdrawal path; **in-app account deletion mandatory**; paid functionality not gated on granting data access ([Apple guidelines](https://developer.apple.com/app-store/review/guidelines/)).
- **Data minimization & purpose limitation (5.1.1(iii) / 5.1.2(i)):** don't repurpose Brand Graph data. **If Marque ever trains/fine-tunes on creator content, that is a separate explicit opt-in** (Open Q #2).
- **Ownership stance:** the creator **owns** their Brand Graph. It is **portable** (export all live facts as JSON) and **deletable** — GDPR/CCPA erasure cascade-deletes **all versions** of every `fact_id` (`ON DELETE CASCADE` from `auth.users`, plus an explicit erasure routine for the trends-link facts) ([Z3rno](https://astron-bb4261fd.mintlify.app/concepts/temporal-versioning)). See `14-appstore-compliance-legal.md`.

### 8.5 RLS — defense in depth
Every `brand_facts` row is scoped to `creator_id = auth.uid()`:

```sql
create policy "own facts" on public.brand_facts
  for all to authenticated
  using  ( (select auth.uid()) = creator_id )     -- wrap in select → optimizer caches per-statement
  with check ( (select auth.uid()) = creator_id );

-- The deliberate exception: trends are shared world-knowledge, readable by all authenticated users.
create policy "trends are shared" on public.trends
  for select to authenticated using ( true );
```

Supabase RLS musts ([RLS — Supabase](https://supabase.com/docs/guides/database/postgres/row-level-security); [Supabase + pgvector for AI Agents](https://callsphere.ai/blog/vw7h-supabase-pgvector-ai-agents-2026)):
- Enable RLS on **every** table in the exposed `public` schema (Supabase won't auto-enable it for raw-SQL tables).
- Never ship the **service-role key** to the client; the client uses the anon/publishable key only.
- Wrap `auth.uid()` in `(select auth.uid())` for a large perf win on row-scanning policies.
- Index every column used in a policy (here, `creator_id` — covered by `idx_bf_current`).

---

## 9. Read/write matrix (per AI feature)

| Feature | Reads | Writes |
|---|---|---|
| Onboarding / chat | — | `stated` facts |
| Page ingestion (`05`) | — | `observed` facts + Voice Fingerprint |
| Script writer · Opus 4.8 (`07`) | full Context Pack + RAG exemplars (`match_brand_facts`) | nothing live (may propose "learned preference" facts as `needs_reverify`) |
| Voice check · Haiku 4.5 (`07`) | Voice Fingerprint hard constraints + stylometry | drift flags (no facts) |
| Format / recipe selector (`08`) | `format_affinity` | — |
| Trend Radar (`09`) | shared `trends` + `cultural` facts | `cultural` link facts |
| Insights teardown (`10`) | `performance` facts + published-clip metrics | `performance` facts (derived, lineage-bearing) |
| "What Marque knows" view | Context Pack | user edits → high-confidence `stated` facts (supersede) |

---

## 10. Acceptance criteria

1. **No overwrite.** Any update to a fact produces a new `version`, closes the old row (`superseded_at`), and leaves `HISTORY(fact_id)` retrievable. Verified by a trigger test asserting `count(*) where fact_id = X` increases and exactly one row is live.
2. **Time-travel works, both bounds.** `AS_OF(t)` returns the fact state as of `t` using the full closed-open interval `valid_from <= t and (valid_to is null or valid_to > t)` for valid time, combined with the system-time test; `idx_bf_validrange` (GiST) is used (confirmed via `EXPLAIN`). Specifically tested: a fact with `valid_from` in the future is **absent** from `CURRENT`/`brand_context_view` and from `match_brand_facts`, but **present** in `AS_OF(t)` once `t >= valid_from`.
3. **RLS holds through views.** A second authenticated user reading `brand_context_view` returns zero rows for another creator (proves `security_invoker = true`).
4. **Trends are shared, facts are scoped.** Any authenticated user can `select` from `trends`; no user can read another's `brand_facts`.
5. **Provenance walk-up.** Invalidating an episode marks all dependent `performance`/`voice` facts `stale`/`needs_reverify` within one job run.
6. **Deterministic Context Pack.** Two assemblies of an unchanged graph produce byte-identical serialization and identical `context_version`.
7. **Cache discipline.** The system prompt contains no interpolated date/name/directive; dynamic context appears only in `messages[]`; ≤ 4 cache breakpoints (asserted in the AI adapter's request builder).
8. **Voice Fingerprint is enforceable.** Every axis score carries ≥1 evidence episode id; `banned_words` ≥ 15; Haiku voice check rejects a script containing any `banned_word`.
9. **Conflict surfacing.** A seeded stated/observed contradiction renders a reconcile card; resolving it writes a superseding `stated` fact and clears the card.
10. **Erasure is total.** Account deletion removes **all versions** of every `fact_id` for the creator and their `cultural` trend-link facts; a post-delete query returns zero rows.
11. **pgvector under-fetch handled.** `match_brand_facts` with a `layer_filter` still returns up to `match_count` rows (over-fetch verified).
12. **The view is calm.** "What Marque knows" renders one idea per section, shows a provenance chip per fact, and never appears on Today.

---

## Open questions
1. **Embedding model + dimension — RESOLVED.** Owned by `07-ai-system.md` §6.3: **Voyage AI `voyage-3.5` at `vector(1024)`, cosine**, behind the `Embedder` adapter. The columns, the `idx_bf_embedding` HNSW index, and the `match_brand_facts` RPC above are all set to `vector(1024)` accordingly — safe to author the first migration. Embeddings from different models are not comparable ([Supabase pgvector](https://supabase.com/docs/guides/ai/vector-columns)), so any later provider change is a full re-embed + index rebuild, not a hot swap.
2. **Do we ever train/fine-tune on creator content?** If yes, it requires a *separate* explicit opt-in flow per Apple 5.1.2(i) — distinct from the third-party-AI inference consent. Product + legal sign-off.
3. **Confidence-decay half-life** for unverified facts (drives §5.4 down-weighting). Needs a default + per-layer overrides.
4. **Exact per-predicate stated-vs-observed precedence table** (§7) — the two-class default is defensible but the full registry needs product sign-off and lives in `conflict_policy` data.
5. **Cultural memory storage vs recompute.** How much per-creator trend-fit do we persist as `cultural` facts vs recompute on the fly each Trend Radar refresh? Storage/bloat vs latency tradeoff; coordinate with `08-format-virality.md`.

## Sources
1. [Bitemporal Versioning — MATIH Docs](https://docs.matih.ai/14-context-graph/storage/bitemporal/) — Postgres bitemporal context-graph store; valid vs transaction time; CURRENT/AS_OF/HISTORY recall modes; supersession-on-update.
2. [Temporal Versioning — Z3rno](https://astron-bb4261fd.mintlify.app/concepts/temporal-versioning) — SCD Type 2 for agent memory: atomic trigger supersession, stable id, GiST range index, point-in-time, GDPR delete-all-versions.
3. [Bi-temporal Model — CortexDB](https://cortexdb.ai/docs/concepts/bi-temporal) — shared-knowledge vs per-user-memory distinction (basis for trend scoping); why single-axis overwrite destroys historical belief.
4. [Bi-Temporal Edges — javatask.dev](https://javatask.dev/blog/bitemporal-edges-agent-memory/) — auditability via two clocks; context rot / context collision.
5. [TOKI: Bitemporal Operator Algebra for Contradiction Resolution (arXiv)](https://arxiv.org/html/2606.06240) — dual current/audit schema; contradiction event → winner chosen, loser preserved (stated-vs-observed model).
6. [Temporal Reasoning & Memory Provenance — Jatin Bansal](https://jatinbansal.com/ai-engineering/temporal-reasoning-provenance/) — three-clock model (valid/transaction/last-verified); provenance walk-up; confidence decay.
7. [Supabase + pgvector for AI Agents (CallSphere)](https://callsphere.ai/blog/vw7h-supabase-pgvector-ai-agents-2026) — per-user memory tables, HNSW params, RLS to auth.uid(), anon-not-service-key, Realtime 1MB cap.
8. [Vector columns / pgvector — Supabase Docs](https://supabase.com/docs/guides/ai/vector-columns) — RPC wrapping, distance operators, post-filter under-fetch pitfall, same-embedding-model rule.
9. [Structured vs Unstructured metadata — Supabase Docs](https://github.com/supabase/supabase/blob/master/apps/docs/content/guides/ai/structured-unstructured.mdx) — hybrid columns + jsonb recommendation.
10. [Row Level Security — Supabase Docs](https://supabase.com/docs/guides/database/postgres/row-level-security) — RLS enablement; `security_invoker=true` on views (PG15+); wrap auth.uid() in select; index policy columns.
11. [How GhostLoop Learns Your Voice](https://ghostloop.io/blog/how-ghostloop-learns-your-voice) — multi-dimension scored voice profile with cited evidence; banned words/openers/closers; "never-do rules matter as much as do-rules."
12. [whystrohm voice-extract](https://whystrohm.com/resources/skills/whystrohm-voice-extract) — scored axis voice profile + enforceable guardrails + exemplar sentences + one-paragraph summary (editable-view model).
13. [GraceAI Fingerprint (Brand DNA)](https://getgrace.ai/features/fingerprint) — dual representation (human-readable signature + searchable embeddings); per-channel fingerprints; editable signature.
14. [stylometric-transfer (GitHub)](https://github.com/ngpepin/stylometric-transfer) — interpretable JSON stylometric fingerprint (sentence-length dist, punctuation, function-word profile, burstiness) + deviation reports.
15. [Advanced tool use — Anthropic Engineering](https://www.anthropic.com/engineering/advanced-tool-use) — Tool Use Examples for parameter accuracy; structured tool outputs.
16. [Prompt caching — Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — render order tools→system→messages; static-first; breakpoint placement; up-to-90%/85% savings.
17. [anthropics/skills prompt-caching guide](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/prompt-caching.md) — don't interpolate date/name/mode into system; inject dynamic context as later role:system message; max 4 breakpoints; deterministic serialization.
18. [App Review Guidelines — Apple](https://developer.apple.com/app-store/review/guidelines/) — 5.1.1 data collection, consent, minimization, no surreptitious profiling, in-app account deletion.
19. [Apple clamps down on sharing data with third-party AI — TechCrunch (Nov 13 2025)](https://techcrunch.com/2025/11/13/apples-new-app-review-guidelines-clamp-down-on-apps-sharing-personal-data-with-third-party-ai/) — new 5.1.2(i) disclose + explicit-permission-before-sharing rule.
20. [Updated App Review Guidelines — Apple Developer News (Nov 13 2025)](https://developer.apple.com/news/?id=ey6d8onl) — primary source confirming 5.1.2(i).
21. [IG User Business Discovery — Meta](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/business_discovery/) — observed-identity ingestion path (sibling `06-brand-graph.md`).
22. [TikTok scraping APIs in 2026 — ScrapeBadger](https://scrapebadger.com/blog/tiktok-scraping-apis-in-2026-the-complete-deep-guide) — no official commercial TikTok read path (drives §4.2 caveat).
