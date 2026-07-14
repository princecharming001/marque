-- Marque learning-stack persistence (durable bandit + post registry).
-- Apply once to the Supabase project before enabling SUPABASE_URL in prod.
-- Safe to re-run (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS arm_stats (
    id          BIGSERIAL PRIMARY KEY,
    creator_id  TEXT NOT NULL,
    arm_key     TEXT NOT NULL,               -- e.g. "style:talking_head", "hook_signal:contrarian"
    n           INT   DEFAULT 0,
    sum_y       FLOAT DEFAULT 0.0,
    alpha       FLOAT DEFAULT 1.0,
    beta        FLOAT DEFAULT 1.0,
    effect      FLOAT DEFAULT 0.5,
    sum_raw     FLOAT DEFAULT 0.0,             -- A-05: accumulated raw engagement composite (honest lift)
    n_raw       INT DEFAULT 0,                 -- AF-2: settles actually IN sum_raw (the honest lift denominator)
    prior_alpha FLOAT DEFAULT 1.0,             -- A-10: niche-seeded Beta prior (survives reload)
    prior_beta  FLOAT DEFAULT 1.0,
    confidence  TEXT CHECK (confidence IN ('insufficient', 'early_read', 'confirmed')),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (creator_id, arm_key)             -- upsert target (on_conflict=creator_id,arm_key)
);

CREATE TABLE IF NOT EXISTS post_registry (
    post_id         TEXT PRIMARY KEY,
    creator_id      TEXT,                      -- A-12: should be NOT NULL once legacy rows are backfilled
    clip_id         TEXT,                      -- A-12: join back to the app clip that produced this post
    permalink       TEXT,                      -- A-12/B2: live post URL for public-metric scraping
    platform        TEXT,
    scheduled_at    TEXT,
    pillar          TEXT,
    style           TEXT,
    format_id       TEXT,
    hook_signal     TEXT,
    predicted_score INT,
    outcome_y       FLOAT,
    outcome_raw     FLOAT,                     -- A-05: raw engagement composite → per-creator baseline
    settled         BOOLEAN DEFAULT FALSE,
    settled_at      TIMESTAMPTZ,               -- A-12: when metrics settled (performance-window filter)
    metrics         JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- A-10: durable per-creator brand facts. niche keeps cold-arm seeding alive across a
-- deploy; goal drives reward weighting; coach_last_shown enforces the ≤1-nudge/day
-- Today-coach gate. Written best-effort by the backend; the code tolerates its absence.
CREATE TABLE IF NOT EXISTS creators (
    creator_id       TEXT PRIMARY KEY,
    niche            TEXT,
    goal             TEXT,
    coach_last_shown TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_arm_stats_creator     ON arm_stats(creator_id);
CREATE INDEX IF NOT EXISTS idx_post_registry_creator ON post_registry(creator_id);
CREATE INDEX IF NOT EXISTS idx_post_registry_settled ON post_registry(settled);

-- Analyzed style-DNA for a creator someone wants to emulate (hook mechanics,
-- format dominance, pacing, voice axes) — keyed by handle so linking the same
-- page from two different users hits the same cached analysis.
CREATE TABLE IF NOT EXISTS emulation_profiles (
    handle      TEXT PRIMARY KEY,            -- lowercase, no leading @
    platform    TEXT NOT NULL,
    profile     JSONB NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- F15: durable clip-editing sessions. The full in-memory job dict (words, edl,
-- edl_history, tweaks, clips w/ render_url+status+warnings, style, source_url)
-- is write-through'd here as a single JSONB blob keyed by job_id — kills the
-- "edit session expired" class: a 24h in-memory TTL sweep or a Render restart
-- no longer loses a creator's edit; it's lazily restored from here on the next
-- access. Kept as one blob (not columns) since the job shape is internal and
-- already evolves inside main.py — a schema-per-field table would need a
-- migration every time a new job key is added.
CREATE TABLE IF NOT EXISTS clip_edit_sessions (
    job_id      TEXT PRIMARY KEY,
    state       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Schema-drift guards: the tables above may ALREADY exist in the target project from
-- an earlier apply (CREATE TABLE IF NOT EXISTS silently skips new columns). Idempotent
-- column adds keep an existing deployment in sync with the DDL above.
ALTER TABLE arm_stats  ADD COLUMN IF NOT EXISTS sum_raw     FLOAT DEFAULT 0.0;
ALTER TABLE arm_stats  ADD COLUMN IF NOT EXISTS n_raw       INT   DEFAULT 0;
ALTER TABLE arm_stats  ADD COLUMN IF NOT EXISTS prior_alpha FLOAT DEFAULT 1.0;
ALTER TABLE arm_stats  ADD COLUMN IF NOT EXISTS prior_beta  FLOAT DEFAULT 1.0;
ALTER TABLE creators   ADD COLUMN IF NOT EXISTS coach_last_shown TIMESTAMPTZ;
ALTER TABLE post_registry ADD COLUMN IF NOT EXISTS outcome_raw FLOAT;

-- These tables are written ONLY by the backend using the Supabase service-role key,
-- which bypasses RLS. Enabling RLS with no policies denies the anon/authenticated
-- roles entirely — the correct, closed-by-default posture for internal learning state.
ALTER TABLE arm_stats           ENABLE ROW LEVEL SECURITY;
ALTER TABLE post_registry       ENABLE ROW LEVEL SECURITY;
ALTER TABLE emulation_profiles  ENABLE ROW LEVEL SECURITY;
ALTER TABLE clip_edit_sessions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE creators            ENABLE ROW LEVEL SECURITY;

-- Durable mirror of the in-memory "Steal these" reels caches (niche + watched).
-- One JSONB blob per cache key ("niche:fitness" / "instagram:handle") holding
-- {"reels": [...], "ts": <epoch>}. Deploys wipe the in-memory caches; without
-- this the expensive transcribe + re-host work was lost every release and users
-- saw caption-as-transcript and unplayable expired CDN URLs until a re-scrape.
CREATE TABLE IF NOT EXISTS reels_cache (
    cache_key   TEXT PRIMARY KEY,
    entry       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE reels_cache ENABLE ROW LEVEL SECURITY;

-- R6-7: decoupled feedback accumulators. Feed like/dislike taps fold into the
-- Thompson alpha/beta via these columns ONLY, leaving n / sum_raw / n_raw (which
-- ground honest "+N% lift" + "seen in N settled posts" claims) untouched.
ALTER TABLE arm_stats ADD COLUMN IF NOT EXISTS fb_n       DOUBLE PRECISION DEFAULT 0;
ALTER TABLE arm_stats ADD COLUMN IF NOT EXISTS fb_sum_y   DOUBLE PRECISION DEFAULT 0;

-- UX-B2a: APNs device tokens (token-based .p8 push). One row per (token, environment);
-- re-registration re-enables (disabled_at cleared). Soft-disable on 410/BadDeviceToken.
CREATE TABLE IF NOT EXISTS device_tokens (
    creator_id   TEXT NOT NULL,
    token        TEXT NOT NULL,
    environment  TEXT NOT NULL CHECK (environment IN ('sandbox', 'prod')),
    platform     TEXT DEFAULT 'ios',
    app_version  TEXT DEFAULT '',
    timezone     TEXT DEFAULT '',
    permission   TEXT DEFAULT '',
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    disabled_at  TIMESTAMPTZ,
    UNIQUE (token, environment)
);
ALTER TABLE device_tokens ENABLE ROW LEVEL SECURITY;

-- ===========================================================================
-- PALO PORT (branch: palo-port) — the ported AI brains. All idempotent; RLS on
-- with no policies (server uses the service-role key, which bypasses RLS). Apply
-- this whole block on top of the existing schema. Requires the pgvector extension.
-- ===========================================================================
CREATE EXTENSION IF NOT EXISTS vector;

-- Paid tier entitlement seam (stubbed; wire to real IAP/RevenueCat later). The
-- creators table already exists (niche/goal/coach_last_shown); just add the column.
ALTER TABLE creators ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'growth'
    CHECK (tier IN ('starter', 'growth', 'studio'));
-- Social handle/account id the metrics poller scrapes (run_insights_cron reads it).
-- Populated opportunistically at post-register time. Empty => that creator's metrics
-- loop stays a no-op (which is why it must be set before TRACK_INSIGHTS does anything).
ALTER TABLE creators ADD COLUMN IF NOT EXISTS handle TEXT DEFAULT '';

-- Prompt overrides — the get_prompt() fallback source (Palo's LD-prompt shim, no LD).
CREATE TABLE IF NOT EXISTS prompt_overrides (
    key         TEXT PRIMARY KEY,
    prompt_text TEXT NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE prompt_overrides ENABLE ROW LEVEL SECURITY;

-- Per-call cost accounting — lands in Phase 0 so every Opus-spending feature is metered.
CREATE TABLE IF NOT EXISTS ai_usage (
    id            BIGSERIAL PRIMARY KEY,
    creator_id    TEXT,
    operation     TEXT,
    model         TEXT,
    input_tokens  INT DEFAULT 0,
    output_tokens INT DEFAULT 0,
    cost_usd      NUMERIC DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE ai_usage ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS ai_usage_creator_idx ON ai_usage (creator_id, created_at);

-- Self-learning memory (pgvector, mem0-style ADD/UPDATE/DELETE reconcile).
CREATE TABLE IF NOT EXISTS memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    creator_id  TEXT NOT NULL,
    type        TEXT,
    key         TEXT,
    value       TEXT NOT NULL,
    confidence  REAL DEFAULT 0.7,
    scope       TEXT DEFAULT '',
    embedding   VECTOR(1536),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    deleted     BOOLEAN DEFAULT FALSE
);
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS memories_creator_idx ON memories (creator_id) WHERE deleted = FALSE;
CREATE INDEX IF NOT EXISTS memories_embedding_idx ON memories
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Cosine-nearest live memories for a creator (PostgREST can't express <=> directly).
CREATE OR REPLACE FUNCTION match_memories(
    p_creator_id TEXT, p_embedding VECTOR(1536), p_scope TEXT DEFAULT NULL, p_limit INT DEFAULT 8)
RETURNS TABLE (id UUID, type TEXT, key TEXT, value TEXT, confidence REAL, scope TEXT, similarity REAL)
LANGUAGE sql STABLE AS $$
    SELECT id, type, key, value, confidence, scope, 1 - (embedding <=> p_embedding) AS similarity
    FROM memories
    WHERE creator_id = p_creator_id AND deleted = FALSE AND embedding IS NOT NULL
      AND (p_scope IS NULL OR scope = p_scope)
    ORDER BY embedding <=> p_embedding
    LIMIT p_limit;
$$;

-- Recommendation ledger — append-only "never re-pitch the same idea twice".
CREATE TABLE IF NOT EXISTS recommendation_ledger (
    id          BIGSERIAL PRIMARY KEY,
    creator_id  TEXT NOT NULL,
    conversation_id TEXT DEFAULT '',
    kind        TEXT,
    summary     TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE recommendation_ledger ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS ledger_creator_idx ON recommendation_ledger (creator_id, created_at DESC);

-- Fast-loop overlay applied between full compiles.
CREATE TABLE IF NOT EXISTS strategy_updates (
    id          BIGSERIAL PRIMARY KEY,
    creator_id  TEXT NOT NULL,
    update_text TEXT,
    source      TEXT DEFAULT 'chat',
    applied     BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE strategy_updates ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS strategy_updates_creator_idx ON strategy_updates (creator_id, created_at DESC);

-- The compiled strategy artifact (the "brain"). One row per creator (Palo m0267 shape).
CREATE TABLE IF NOT EXISTS channel_strategies (
    creator_id             TEXT PRIMARY KEY,
    strategy_markdown      TEXT DEFAULT '',
    strategy_playbooks     JSONB DEFAULT '{}'::jsonb,
    strategy_footnotes     JSONB DEFAULT '{}'::jsonb,
    strategy_revision      INT DEFAULT 0,
    strategy_updated_at    TIMESTAMPTZ,
    exemplar_bank          JSONB DEFAULT '{}'::jsonb,
    element_inventory      JSONB DEFAULT '{}'::jsonb,
    exemplar_bank_revision INT DEFAULT 0,
    exemplar_bank_built_at TIMESTAMPTZ
);
ALTER TABLE channel_strategies ENABLE ROW LEVEL SECURITY;

-- Idea bank (replaces Palo's DynamoDB briefs).
CREATE TABLE IF NOT EXISTS briefs (
    id          TEXT PRIMARY KEY,
    creator_id  TEXT NOT NULL,
    source      TEXT DEFAULT 'chat' CHECK (source IN ('spitfire', 'onboarding', 'chat', 'insight')),
    title       TEXT,
    summary     TEXT,
    beginning   TEXT,
    middle      TEXT,
    ending      TEXT,
    score       REAL DEFAULT 0,
    status      TEXT DEFAULT 'new',
    meta        JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE briefs ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS briefs_creator_idx ON briefs (creator_id, score DESC, created_at DESC);

-- Post-performance insight cards + pulse (replaces DynamoDB track_feed + pulse_outbox).
-- dedup_hash UNIQUE is the anti-repetition latch: a re-run of the daily scan can never
-- post the same card twice (Palo's byte-identical-content dedup, enforced in the DB).
CREATE TABLE IF NOT EXISTS insight_feed (
    id                TEXT PRIMARY KEY,
    creator_id        TEXT NOT NULL,
    type              TEXT,
    category          TEXT,
    title             TEXT,
    description       TEXT,
    content           JSONB DEFAULT '{}'::jsonb,
    chips             JSONB DEFAULT '[]'::jsonb,
    dedup_hash        TEXT UNIQUE,
    delivered         BOOLEAN DEFAULT FALSE,
    conversation_seed JSONB DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE insight_feed ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS insight_feed_creator_idx ON insight_feed (creator_id, created_at DESC);

-- Post/account metric timeseries — the genuinely new build. source column lets the
-- poller swap/add Apify -> Post for Me -> IG Graph transparently (per tier).
CREATE TABLE IF NOT EXISTS metrics_ts (
    id          BIGSERIAL PRIMARY KEY,
    creator_id  TEXT NOT NULL,
    entity_type TEXT CHECK (entity_type IN ('post', 'account')),
    entity_id   TEXT,
    metric      TEXT,
    value       NUMERIC,
    source      TEXT CHECK (source IN ('postforme', 'apify', 'ig_graph')),
    captured_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE metrics_ts ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS metrics_ts_idx ON metrics_ts (creator_id, entity_id, metric, captured_at);

-- First-run baseline discipline (Palo's recent_channel_metrics watermarks): records a
-- baseline on the FIRST scan so day-one history never fires a flood of false milestones.
CREATE TABLE IF NOT EXISTS metric_watermarks (
    creator_id  TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       NUMERIC,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (creator_id, key)
);
ALTER TABLE metric_watermarks ENABLE ROW LEVEL SECURITY;
