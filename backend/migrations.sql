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
