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
    confidence  TEXT CHECK (confidence IN ('insufficient', 'early_read', 'confirmed')),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (creator_id, arm_key)             -- upsert target (on_conflict=creator_id,arm_key)
);

CREATE TABLE IF NOT EXISTS post_registry (
    post_id         TEXT PRIMARY KEY,
    creator_id      TEXT,
    platform        TEXT,
    scheduled_at    TEXT,
    pillar          TEXT,
    style           TEXT,
    format_id       TEXT,
    hook_signal     TEXT,
    predicted_score INT,
    outcome_y       FLOAT,
    settled         BOOLEAN DEFAULT FALSE,
    metrics         JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_arm_stats_creator     ON arm_stats(creator_id);
CREATE INDEX IF NOT EXISTS idx_post_registry_creator ON post_registry(creator_id);
CREATE INDEX IF NOT EXISTS idx_post_registry_settled ON post_registry(settled);

-- These tables are written ONLY by the backend using the Supabase service-role key,
-- which bypasses RLS. Enabling RLS with no policies denies the anon/authenticated
-- roles entirely — the correct, closed-by-default posture for internal learning state.
ALTER TABLE arm_stats     ENABLE ROW LEVEL SECURITY;
ALTER TABLE post_registry ENABLE ROW LEVEL SECURITY;
