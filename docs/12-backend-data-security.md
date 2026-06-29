# 12 — Backend, Data Model & Security

> **Marque** — the durable spine beneath the calm surface.
>
> This document specifies the persistence layer (Supabase Postgres), the authorization layer (Row Level Security), authentication (Sign in with Apple), the orchestration service (FastAPI + Trigger.dev), storage and signed-URL handling for sensitive face/voice video, secrets and encryption, PII retention and deletion, rate limiting and abuse prevention, input validation, and a concise threat model.

**Status:** Canonical · **Owners:** Backend lead + Security lead · **Last updated:** 2026-06-29

### Where this sits in the spec

This is the foundation layer. The aesthetic (`02-design-system.md`) and the anti-clutter doctrine (`04-screens-create.md`) never touch this file directly, but every feature surface depends on it:

| Cross-reference | Why you'll come here |
|---|---|
| `01-information-architecture.md` | Screen/IA map only — **not** the system-architecture or API owner. The adapter-DI boundaries (ClipEngine / Publisher / Insights) and the FastAPI surface are owned **here**: see §5.3 (orchestration responsibilities) and §11 (the canonical FastAPI API contract). This doc is what the adapters persist to and authorize against. |
| `03-onboarding.md` | Brand Graph seeding writes the `brand_graph` + sub-tables defined here; Sign in with Apple is the first write. |
| `08-format-virality.md` | `formats` is a global read-only catalog (render-recipes); `render_jobs` mirrors the ClipEngine/Shotstack job state machine. |
| `07-ai-system.md` | Claude Opus 4.8 / Haiku 4.5 calls happen **only** in FastAPI; this doc holds the secrets and the prompt-caching trust boundary. |
| `10-social-publishing.md` | `schedules`, `posts`, `post_metrics`, `social_connections`, and the IG/TikTok posting-cap enforcement live here. |
| `05-screens-produce.md` | The learning loop reads `post_metrics`; this doc defines the `security_invoker` views it consumes. |
| `11-monetization.md` | `subscriptions` + `credits` are server-authoritative, synced from RevenueCat webhooks defined here. |
| `14-appstore-compliance-legal.md` | PII retention windows, the account-deletion job, and the consent record are co-owned with that doc. |

> **Doctrine for this layer (one sentence):** *the iOS app is never trusted; the database enforces ownership via RLS, and every privileged action goes through FastAPI holding the only dangerous keys.*

---

## 1. Architecture of trust — the three planes

Marque has exactly three trust planes. Knowing which plane a piece of code runs in tells you which key it holds and what it is allowed to do.

```
┌─────────────────────────────────────────────────────────────────┐
│ PLANE 1 — iOS app (SwiftUI, untrusted)                           │
│   Holds: Supabase URL + ANON/PUBLISHABLE key only.               │
│   Talks to: Supabase (RLS-scoped) + FastAPI (JWT-authed).        │
│   NEVER holds: service_role, Anthropic, AssemblyAI, Shotstack,   │
│   Ayrshare, Phyllo, R2, RevenueCat secret keys.                  │
└─────────────────────────────────────────────────────────────────┘
            │ anon key + user JWT          │ user JWT (Authorization: Bearer)
            ▼                              ▼
┌───────────────────────────┐   ┌─────────────────────────────────┐
│ PLANE 2 — Supabase         │   │ PLANE 3 — FastAPI orchestration  │
│   Postgres + RLS           │   │   Holds ALL vendor secrets +     │
│   Auth (GoTrue)            │◄──│   service_role. Validates input, │
│   Storage (RLS objects)    │   │   verifies entitlements, mints   │
│   Realtime                 │   │   publish-time URLs, enqueues    │
│                            │   │   durable work to Trigger.dev.   │
└───────────────────────────┘   └─────────────────────────────────┘
                                          │ secrets
                                          ▼
                         ┌──────────────────────────────────────┐
                         │ Trigger.dev v3 (durable jobs)         │
                         │ ClipEngine MCP · AssemblyAI ·         │
                         │ Shotstack · Cloudflare R2/Stream ·    │
                         │ Ayrshare/Phyllo · Anthropic · APNs    │
                         └──────────────────────────────────────┘
```

**The load-bearing claim:** it is *safe* for Plane 1 to hit Postgres directly with the anon key **only because** every exposed table has RLS enabled with explicit, correct policies. RLS is the spine of this entire section. ([Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security))

The `service_role` key bypasses RLS unconditionally (`BYPASSRLS`). It is the single most dangerous secret in the system and lives **only** in Plane 3. ([Wonsuk Choi — production RLS patterns](https://wonsukchoi.co/en/blog/supabase-rls-production-patterns))

---

## 2. Row Level Security — the five non-negotiable patterns

RLS is not a feature we "turn on at the end." It is the authorization model. Three states matter:

| RLS state | Effect | Verdict |
|---|---|---|
| **RLS off** | Anyone with the anon key reads/writes everything | **Catastrophic** — never ship |
| **RLS on, zero policies** | Deny-all; the app silently breaks | Safe but broken |
| **RLS on, explicit policies** | Correct ownership enforcement | **Required for every table** |

Every table in an exposed schema (`public`, `storage`) MUST have RLS enabled AND explicit policies. ([Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security))

### 2.1 The five patterns (mandatory on every policy)

**Pattern 1 — Wrap `auth.uid()` / `auth.jwt()` in a subselect.** Write `(select auth.uid()) = user_id`, never `auth.uid() = user_id`. The subselect promotes the call to an `initPlan` evaluated **once per statement** and cached, instead of once per row. Benchmarks show 171 ms → <0.1 ms on 100k rows — "the single highest-impact change." ([adamarant — RLS at scale](https://adamarant.com/en/blog/supabase-rls-at-scale-7-patterns-for-queries-that-stay-fast); [Wonsuk Choi](https://wonsukchoi.co/en/blog/supabase-rls-production-patterns))

**Pattern 2 — Always scope `TO authenticated`.** Short-circuits evaluation for anonymous requests entirely — a security guard *and* a perf win.

**Pattern 3 — Index every column used in a policy.** `user_id`, `brand_graph_id`, `script_id`, etc. Unindexed RLS is 20–40× slower at 100k rows; indexed is ~1–4 ms — near parity with RLS-off. ([Jake's Insights — RLS performance benchmark](https://jakeinsight.com/tech/2026-03-24-supabase-postgres-row-level-security-performance-i/))

**Pattern 4 — Pair `USING` with `WITH CHECK`.** `USING` alone lets a user write rows they cannot see; `WITH CHECK` blocks inserting/updating rows they would not own. Every INSERT/UPDATE policy must specify both. ([AgileSoftLabs — RLS guide 2026](https://www.agilesoftlabs.com/blog/2026/06/supabase-row-level-security-guide-2026))

**Pattern 5 — `SECURITY DEFINER` helper functions** for any check spanning 3+ tables, reused across 5+ policies, or that would recurse (a policy on A that reads B whose policy reads A). They run as `postgres` (BYPASSRLS) and break the cascade/recursion. Mandatory hardening: `SET search_path = ''`, mark `STABLE`, **never** place them in an API-exposed schema (else they are RPC-callable), and **filter inside the function on `(select auth.uid())`** — never trust a `user_id` passed as an argument (spoofable). ([MakerKit — RLS best practices](https://makerkit.dev/blog/tutorials/supabase-rls-best-practices); [Wonsuk Choi](https://wonsukchoi.co/en/blog/supabase-rls-production-patterns))

### 2.2 Marque's RLS shape

Almost every table is **single-owner**: the policy is the direct, indexed check `(select auth.uid()) = user_id`. To keep it that way, **child tables denormalize a `user_id` column** rather than joining back to the parent. `hooks` (under `scripts`), `clips` (under `recordings`), and `post_metrics` (under `posts`) all carry their own `user_id`, so the policy is an indexed ownership check, not a per-row join.

Where a child genuinely must reference parent ownership (e.g. a write that must confirm the parent is owned), use a `SECURITY DEFINER` helper:

```sql
-- Lives in a PRIVATE schema (e.g. `private`), NEVER in `public`.
create or replace function private.owns_recording(p_recording_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.recordings r
    where r.id = p_recording_id
      and r.user_id = (select auth.uid())   -- trust the JWT, never an argument
  );
$$;
```

### 2.3 Views bypass RLS — the silent leak

Views run as `postgres` and **bypass RLS by default**. Any view the app reads — the aggregated Today directive, the Insights rollups in `05-screens-produce.md` — MUST be created with `security_invoker = true` (Postgres 15+) so it obeys the caller's RLS. Supabase's Security Advisor flags views missing this. ([Supabase RLS](https://supabase.com/docs/guides/database/postgres/row-level-security))

```sql
create view public.v_today_directive
with (security_invoker = true) as
  select ...;
```

### 2.4 Custom Access Token Hook (tier in the JWT) — a best-effort cache, never the authoritative gate

Embed `subscription_tier` and the internal `user_id` into the JWT at issue time via a **Custom Access Token Hook**. The hook reads `subscriptions` and stamps the claim, which lets cheap, non-sensitive UI shaping (e.g. choosing which Today copy to render) avoid a DB lookup. That is the *only* thing the claim is good for.

**The hard limit you must design around:** a JWT claim is a point-in-time snapshot, frozen until the access token is refreshed. Supabase access tokens stay valid until they expire (default ~1 h TTL) and **cannot be cleanly force-revoked** — even sign-out does not invalidate an already-issued access token. So a user who downgrades, churns, hits a billing failure, or gets a refund **keeps the elevated `subscription_tier` claim for up to one full access-token lifetime**. Treat the claim as a cache that may be stale for that window. ([Supabase — Custom Access Token Hook](https://supabase.com/docs/guides/auth/auth-hooks/custom-access-token-hook); [Supabase — sessions / token TTL](https://supabase.com/docs/guides/auth/sessions))

Consequences, stated as rules:

- **RLS MUST NOT gate row visibility on the tier claim** for any tier-restricted content or premium compute. An RLS policy that reads `subscription_tier` from the JWT will over-grant access for the whole stale window. RLS gates **ownership** (`(select auth.uid()) = user_id`), not entitlement.
- **The authoritative tier check is server-side in FastAPI (Plane 3), against the `subscriptions` table** — the §10 trust boundary. Every premium/render/publish call **re-verifies entitlement** there at call time (§5.3, §10); it never trusts the claim.
- For any flow where a *short* staleness window still matters, **shorten the access-token TTL** rather than leaning on the claim.

This was already the doctrine for "anything that costs money"; the rule above closes the remaining gap — **RLS row visibility must not be gated on the claim either.**

### 2.5 RLS acceptance criteria

- [ ] `get_advisors` / Security Advisor returns **zero** "RLS disabled" and zero "view without `security_invoker`" findings before any release.
- [ ] Every policy uses `(select auth.uid())`, `TO authenticated`, and (for writes) both `USING` and `WITH CHECK`.
- [ ] Every RLS-filter column has a btree index; verified by an `EXPLAIN ANALYZE` regression test at 100k synthetic rows (<5 ms target).
- [ ] No `SECURITY DEFINER` function exists in `public` or any API-exposed schema.
- [ ] A negative test exists per table: user A signed in cannot SELECT/UPDATE/DELETE user B's rows (returns 0 rows / 403, never B's data).

---

## 3. The Supabase Postgres schema

Conventions across **all** tables: `uuid` PKs via `gen_random_uuid()`; `user_id uuid not null references auth.users(id) on delete cascade` + index; `created_at timestamptz not null default now()`; `updated_at timestamptz not null default now()` (bumped by trigger); `deleted_at timestamptz` for soft-delete where retention matters (recordings/clips); state machines as `text` + `CHECK` constraints; index on every FK, every RLS-filter column, and every status column used by worker polling.

### 3.1 Entity relationship overview

```
auth.users (Supabase Auth)
   │ 1:1 (trigger on insert)
   ▼
users ──1:1── brand_graph ──┬─< pillars
   │                        ├─< voice_profile (1:1)
   │                        ├─< audience (1:1)
   │                        └─< competitors
   │
   ├─< scripts ──< hooks
   │      │
   │      └── format_id ─► formats (GLOBAL catalog, read-only)
   │
   ├─< recordings ──< clips ──< render_jobs ──► posts ──< post_metrics
   │      (batch session OR repurpose-in upload)            ▲
   │                                                        │
   ├─< schedules ──────────────────────────────────────────┘
   ├─< social_connections   (Ayrshare/Phyllo; encrypted tokens)
   ├─< subscriptions  (RevenueCat-synced)
   ├─< credits        (metering ledger)
   ├─< devices        (APNs tokens)
   └─< events         (analytics / audit)
```

### 3.2 `users`

Public projection of `auth.users`, created by a trigger on `auth.users` insert. PK equals the auth id.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | = `auth.users.id` |
| `display_name` | `text` | Captured from Apple on **first** sign-in (§4) |
| `email` | `text` | Captured first sign-in only; may be a private relay address |
| `avatar_key` | `text` | Object key in the public `avatars` bucket |
| `subscription_tier` | `text` CHECK in (`free`,`pro`,`studio`) | Mirror of entitlement; source of truth is `subscriptions` |
| `onboarding_completed_at` | `timestamptz` | Gate for Today |
| `ai_consent_at` | `timestamptz` | Consent to AI processing of face/voice (§7, `14-appstore-compliance-legal.md`) |
| `created_at` / `updated_at` / `deleted_at` | `timestamptz` | |

```sql
alter table public.users enable row level security;
create policy "users self-select" on public.users
  for select to authenticated using ((select auth.uid()) = id);
create policy "users self-update" on public.users
  for update to authenticated
  using ((select auth.uid()) = id)
  with check ((select auth.uid()) = id);
-- INSERT happens via the auth.users trigger (service context), not the client.
```

### 3.3 Brand Graph cluster (the CONTEXT LAYER)

`brand_graph` is 1:1 with the user. Sub-tables carry both `brand_graph_id` and a denormalized `user_id` so RLS is a direct ownership check. See `06-brand-graph.md` / `07-ai-system.md` for semantics.

**`brand_graph`**

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `uuid` UNIQUE | enforces 1:1 |
| `source_handles` | `jsonb` | IG/TikTok handles ingested during onboarding |
| `positioning` | `text` | "What do you want to be known for?" answer |
| `summary` | `text` | Opus 4.8-generated brand thesis |
| `graph_version` | `int` default 1 | bumped each retraining; supports diffing the learning loop |

**`pillars`** — content themes.

| Column | Type | Notes |
|---|---|---|
| `id` `uuid` PK · `brand_graph_id` `uuid` · `user_id` `uuid` | | denormalized owner |
| `name` `text` · `description` `text` · `weight` `numeric` | | weight tunes script topic mix |

**`voice_profile`** (1:1 with brand_graph) — tone/lexicon/cadence descriptors + few-shot exemplars used for voice checks by Haiku 4.5.

| Column | Type | Notes |
|---|---|---|
| `id` `uuid` PK · `brand_graph_id` `uuid` UNIQUE · `user_id` `uuid` | | |
| `tone` `jsonb` · `lexicon` `text[]` · `banned_phrases` `text[]` · `exemplars` `jsonb` | | exemplars = verbatim creator lines |

**`audience`** (1:1) — `persona` `jsonb`, `pain_points` `text[]`, `platforms` `text[]`.
**`competitors`** — `handle` `text`, `platform` `text`, `teardown` `jsonb` (Opus 4.8 teardown output).

```sql
-- Pattern applied to EVERY brand-graph sub-table:
alter table public.pillars enable row level security;
create policy "pillars owner all" on public.pillars
  for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create index pillars_user_id_idx on public.pillars (user_id);
create index pillars_brand_graph_id_idx on public.pillars (brand_graph_id);
```

### 3.4 `formats` — the GLOBAL render-recipe catalog (read-only to users)

The Format Library (`08-format-virality.md`) is a **shared catalog**, not per-user data. Formats are structured render-recipes (split-screen, 3-up talking heads, green-screen, faceless AI-visual, before/after, myth-buster, listicle, POV, reaction, B-roll+caption-hook). It is the one table users may read but never write.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | stable; referenced by `scripts.format_id` |
| `slug` | `text` UNIQUE | e.g. `split-screen`, `myth-buster` |
| `name` / `description` | `text` | display copy |
| `recipe` | `jsonb` | the render-recipe: layers, caption style, overlay, kinetic-text params, target ratio |
| `engine` | `text` CHECK in (`shotstack`,`clipengine`) | which adapter renders it |
| `min_tier` | `text` CHECK in (`free`,`pro`,`studio`) | tier gate; enforced server-side, not just in RLS |
| `enabled` | `bool` default true | soft-disable without delete |

```sql
alter table public.formats enable row level security;
create policy "formats public read" on public.formats
  for select to authenticated using (enabled = true);
-- No insert/update/delete policy => writes only via service_role (admin tooling).
```

### 3.5 Content lineage tables

**`scripts`** — written by the Virality Engine (`07-ai-system.md`).

| Column | Type | Notes |
|---|---|---|
| `id` `uuid` PK · `user_id` `uuid` | | |
| `pillar_id` `uuid` nullable · `format_id` `uuid` references `formats` | | suggested format |
| `title` `text` · `body` `text` · `language` `text` default `'en'` | | |
| `virality_score` `numeric` | | from virality-predictor (`08-format-virality.md`) |
| `status` `text` CHECK in (`draft`,`approved`,`recorded`,`archived`) | | |

**`hooks`** — Hook Lab, nested in the script reader (progressive disclosure, `07-ai-system.md`). Denormalized `user_id`.

| `id` `uuid` PK · `script_id` `uuid` · `user_id` `uuid` · `text` `text` · `style` `text` · `predicted_strength` `numeric` |

**`recordings`** — one batch session **or** a repurpose-in upload (the second Record source, `05-screens-produce.md`). This is the highest-PII row (face + voice).

| Column | Type | Notes |
|---|---|---|
| `id` `uuid` PK · `user_id` `uuid` | | |
| `source` `text` CHECK in (`batch`,`repurpose_upload`) | | repurpose-in shares the same pipeline |
| `source_object_key` `text` | | **key only**, never a signed URL (§5) |
| `storage_backend` `text` CHECK in (`r2`,`supabase`) | | source video residency (Open Q.) |
| `duration_sec` `int` · `transcript_id` `text` | | AssemblyAI transcript reference |
| `status` `text` CHECK in (`uploading`,`uploaded`,`transcribing`,`ready`,`failed`) | | |
| `deleted_at` `timestamptz` | | soft-delete; purge job wipes object (§7) |

**`clips`** — moment-detected segments. Denormalized `user_id`.

| `id` `uuid` PK · `recording_id` `uuid` · `user_id` `uuid` · `format_id` `uuid` · `start_ms` `int` · `end_ms` `int` · `caption_data` `jsonb` · `output_object_key` `text` · `status` `text` CHECK in (`pending`,`rendering`,`rendered`,`failed`) |

**`render_jobs`** — mirrors the Trigger.dev / Shotstack / ClipEngine job state machine (§5).

| Column | Type | Notes |
|---|---|---|
| `id` `uuid` PK · `clip_id` `uuid` · `user_id` `uuid` | | |
| `engine` `text` CHECK in (`shotstack`,`clipengine`) | | |
| `idempotency_key` `text` UNIQUE | | mirrors `idempotencyKeys.create(...)` (§5) |
| `trigger_run_id` `text` | | Trigger.dev run handle |
| `status` `text` CHECK in (`queued`,`running`,`waiting_approval`,`succeeded`,`failed`,`canceled`) | | |
| `error_code` `text` · `attempts` `int` default 0 | | `error_code` is a value from the **canonical taxonomy (§3.9)** (render/transcription families) — not a raw Shotstack/ClipEngine/AssemblyAI string |

```sql
-- Worker-poll indexes (status-driven):
create index render_jobs_status_idx on public.render_jobs (status);
create index render_jobs_user_id_idx on public.render_jobs (user_id);
```

### 3.6 Publishing tables

See `10-social-publishing.md` for the platform-constraint detail; this section defines the persistence and the cap-enforcement columns.

**`schedules`** — planned posts.

| `id` `uuid` PK · `user_id` `uuid` · `clip_id` `uuid` · `platform` `text` CHECK in (`instagram`,`tiktok`) · `scheduled_at` `timestamptz` · `status` `text` CHECK in (`scheduled`,`publishing`,`published`,`failed`,`canceled`) |

**`posts`** — a published (or attempted) post.

| Column | Type | Notes |
|---|---|---|
| `id` `uuid` PK · `user_id` `uuid` · `clip_id` `uuid` · `schedule_id` `uuid` nullable | | |
| `platform` `text` CHECK in (`instagram`,`tiktok`) | | |
| `provider_post_id` `text` | | Ayrshare/native id |
| `permalink` `text` | | |
| `status` `text` CHECK in (`pending`,`published`,`failed`) | | |
| `error_code` `text` | | a value from the **canonical taxonomy (§3.9)** — same enum the publish UI keys off as `GatedReason` |
| `published_at` `timestamptz` | | |

`posts.error_code` is **not** a free-form platform string. Raw TikTok/IG errors (`spam_risk_too_many_posts`, `reached_active_user_cap`, `scope_not_authorized`, `rate_limit_exceeded`, `url_ownership_unverified`, …) are **mapped at the adapter boundary into the canonical, vendor-neutral taxonomy defined in §3.9** before they are persisted. The Coach feed and retry logic in `05-screens-produce.md` key off this canonical `error_code`; the publish UI in `11-monetization.md` keys off the *same* enum under the name `GatedReason` (they are one taxonomy, §3.9). ([TikTok Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post))

**`post_metrics`** — pulled back via the Insights adapter (Phyllo/Ayrshare). Denormalized `user_id`.

| `id` `uuid` PK · `post_id` `uuid` · `user_id` `uuid` · `captured_at` `timestamptz` · `views` `bigint` · `likes` `bigint` · `comments` `bigint` · `shares` `bigint` · `watch_time_sec` `numeric` · `retention_curve` `jsonb` |

**`social_connections`** — Ayrshare/Phyllo profile keys + provider account ids. **Tokens encrypted at the application layer** (§7). Also stores the TikTok creator constraints the UI must respect (allowed privacy levels, `max_video_post_duration_sec`) per their UX guideline. ([TikTok content-sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines))

| Column | Type | Notes |
|---|---|---|
| `id` `uuid` PK · `user_id` `uuid` | | |
| `platform` `text` CHECK in (`instagram`,`tiktok`) | | |
| `provider` `text` CHECK in (`ayrshare`,`phyllo`) | | |
| `provider_account_id` `text` | | |
| `access_token_enc` `bytea` | | **encrypted** OAuth/profile token |
| `creator_constraints` `jsonb` | | TikTok privacy levels, max duration, IG account type |
| `daily_publish_count` `int` default 0 · `daily_window_started_at` `timestamptz` | | local mirror for cap pre-check (§5, §8) |

### 3.7 Commerce + device + audit tables

**`subscriptions`** — RevenueCat-synced, server-authoritative (`11-monetization.md`, §10 here).

| `id` `uuid` PK · `user_id` `uuid` · `rc_app_user_id` `text` (= `user_id`) · `entitlement` `text` · `product_id` `text` · `status` `text` CHECK in (`active`,`expired`,`billing_issue`,`canceled`) · `current_period_end` `timestamptz` · `rc_last_event_id` `text` (idempotency) |

**`credits`** — the metering ledger for expensive AI/render operations; also doubles as an abuse throttle (§8, §10).

| `id` `uuid` PK · `user_id` `uuid` · `delta` `int` (signed) · `reason` `text` (`grant`,`script_gen`,`render`,`refund`) · `ref_id` `uuid` (the consuming row) · `created_at` |

> Balance = `sum(delta)`. Debits happen **transactionally in FastAPI**, never on the client.

**`devices`** — APNs tokens for push (Coach teardown pushes, the one earned referral prompt).

| `id` `uuid` PK · `user_id` `uuid` · `apns_token` `text` · `bundle_env` `text` CHECK in (`prod`,`sandbox`) · `last_seen_at` `timestamptz` |

**`events`** — analytics/audit ledger (mirrors the PostHog events in `15-infra-observability-testing.md`, but persisted server-side for security-relevant auditing: sign-ins, entitlement changes, publishes, deletions).

| `id` `uuid` PK · `user_id` `uuid` nullable · `type` `text` · `payload` `jsonb` · `created_at` |

> **RLS on `events`:** users may SELECT their own rows; INSERT of security-audit events happens via `service_role` in FastAPI (so the audit trail cannot be forged by the client).

### 3.8 Schema acceptance criteria

- [ ] Every table: RLS enabled, owner policy present, FK + RLS-column indexes created.
- [ ] `formats` is read-only to `authenticated`; no client write policy exists.
- [ ] `recordings` and `clips` have `deleted_at` and a corresponding storage-purge job (§7).
- [ ] `render_jobs.idempotency_key` and `subscriptions.rc_last_event_id` are UNIQUE (replay defense).
- [ ] `generate_typescript_types` output is checked in and matches the Swift model layer.
- [ ] Every persisted `error_code` (`posts`, `render_jobs`) is a member of the §3.9 canonical taxonomy; a CHECK constraint or `error_codes` lookup table enforces it.

---

## 3.9 The canonical error / gating taxonomy (one enum, owned here)

Across the spec, the same handful of failures — TikTok over-cap, IG over-cap, URL not verified, token expired, render blew up — were being invented under different names in different docs (`GatedReason` in `11-monetization.md`, raw `posts.error_code` strings here, "normalized platform errors" referenced loosely in `05-screens-produce.md` / `10-social-publishing.md`, render `error_code` in `08-format-virality.md` with no enumeration, pass/fail checks in `05-screens-produce.md` with no codes). That divergence is a bug: the Coach feed + retry logic key off `error_code` while the UI keys off `GatedReason`, and there was no single mapping.

**This section is the single source of truth.** `12-backend-data-security.md` owns the taxonomy because it owns persistence (`posts.error_code`, `render_jobs.error_code`). The rule for every sibling doc:

> **`GatedReason` and `posts.error_code` are the same enum.** There is exactly one taxonomy. Adapters (`05` ingest/transcription, `06`/`render_jobs`, `08`/`10` publish, `09` insights) map vendor-specific errors **into** it by reference; none of them defines its own parallel enum.

### 3.9.1 Code shape

A canonical code is `{family}.{reason}` (lowercase, dotted). The `family` lets the iOS client and the Coach route handling without string-matching every leaf:

| Family | Domain | Persisted on | Surfaced to UI as |
|---|---|---|---|
| `publish` | publish gates + publish-time failures | `posts.error_code` | `GatedReason` (pre-flight gate) / post-failure card |
| `render` | clip render / Shotstack / ClipEngine | `render_jobs.error_code` | render-failed state (§5.6) |
| `transcribe` | AssemblyAI moment detection / transcription | `recordings.status='failed'` + `error_code` | "couldn't read your session" |
| `auth` | token / scope / reconnect | `posts.error_code` or surfaced live | reconnect prompt |

### 3.9.2 The canonical enum + crosswalk

Each row is one canonical code. `Retriable` drives the §5.6 / `09` retry logic; `GatedReason` is the **same string** the publish UI (`11-monetization.md`) reads — the column exists only to make the equivalence explicit.

| Canonical `error_code` (= `GatedReason`) | Meaning | Maps from (raw vendor / internal) | Retriable | Coach copy intent |
|---|---|---|---|---|
| `publish.over_daily_cap` | IG 50/24h **or** TikTok daily cap hit | IG `content_publishing_limit` exhausted; TikTok `reached_active_user_cap`, `spam_risk_too_many_posts` | yes — after window resets | "You've hit today's limit — this posts automatically tomorrow." |
| `publish.client_unaudited` | TikTok app not yet audited → forced `SELF_ONLY` / 5-user cap | TikTok unaudited-app constraint (§5.5) | no (blocked until audit) | explain private-only state |
| `publish.media_not_reachable` | platform couldn't fetch the `video_url` | IG container `ERROR` on fetch; TikTok `PULL_FROM_URL` fetch fail | yes — re-mint URL (§5.2) | silent re-mint, then retry |
| `publish.url_ownership_unverified` | TikTok `PULL_FROM_URL` domain not pre-verified | TikTok `url_ownership_unverified` | no (config fix) | ops alert, not creator-facing |
| `publish.scope_not_authorized` | missing IG/TikTok publish scope | TikTok `scope_not_authorized`; IG permission error | no → `auth.needs_reconnect` | route to reconnect |
| `publish.rate_limited` | per-minute API throttle (TikTok 6 req/min, IG burst) | TikTok/IG `rate_limit_exceeded` | yes — backoff + retry | invisible to creator |
| `auth.needs_reconnect` | token expired/revoked; connection must be re-established | Ayrshare/Phyllo token error; OAuth revoked | no (user action) | "Reconnect Instagram to keep posting." |
| `render.engine_failed` | Shotstack/ClipEngine render error | Shotstack render `failed`; ClipEngine job error | yes — bounded attempts | quiet retry (§5.6) |
| `render.source_invalid` | recording unusable (corrupt/too short/no face) | internal validation; ClipEngine reject | no | "Let's re-record this one." |
| `render.timeout` | render exceeded the job budget | Trigger.dev / engine timeout | yes — once | quiet retry |
| `transcribe.failed` | AssemblyAI transcription/moment detection failed | AssemblyAI error / empty result | yes — once, then `transcribe.unusable` | "Listening didn't work — retrying." |
| `transcribe.unusable` | audio present but no usable moments | AssemblyAI low-confidence / silent track | no | "We couldn't find clip-worthy moments." |

Anything unmapped falls to `publish.unknown` / `render.unknown` / `transcribe.unknown` (always **non**-retriable, always logged to Sentry with the raw vendor payload) so a new vendor error never silently retries forever.

### 3.9.3 Where the mapping lives

The crosswalk is implemented **once**, server-side, at each adapter boundary in FastAPI (Plane 3) — the Publisher adapter maps IG/TikTok; the ClipEngine adapter maps render; the transcription step maps AssemblyAI. Persisted codes are always canonical; raw vendor strings are retained only in the `events` audit payload (§3.7) and Sentry, never in `error_code`. Sibling docs **reference §3.9.2 by code**, e.g. `10-social-publishing.md` says "gate with `publish.over_daily_cap`" rather than re-listing strings.

---

## 4. Authentication — Sign in with Apple + Supabase Auth

Marque is iOS-only, so **native** Sign in with Apple via `ASAuthorizationAppleIDProvider` is both the best practice and effectively required: App Store Review Guideline 4.8 mandates offering Apple sign-in when you offer any third-party login. ([Supabase — Apple login, Swift](https://supabase.com/docs/guides/auth/social-login/auth-apple?platform=swift))

### 4.1 The exact nonce flow

1. Generate a cryptographically random **raw nonce**.
2. Compute its **SHA-256 hash**.
3. Pass the **hashed** nonce to `ASAuthorizationAppleIDRequest.nonce`.
4. Receive the `identityToken` from Apple.
5. Call `supabase.auth.signInWithIdToken(provider: .apple, idToken:, nonce: rawNonce)` — Supabase receives the **raw** nonce; GoTrue re-hashes and compares to the token's `nonce` claim.

```swift
// SwiftUI — Authentication Services + Supabase
let rawNonce = randomNonceString()
request.nonce = sha256(rawNonce)                    // Apple gets the HASH
// …on credential callback…
try await supabase.auth.signInWithIdToken(
    credentials: .init(provider: .apple,
                       idToken: identityTokenString,
                       nonce: rawNonce)             // Supabase gets the RAW nonce
)
```

### 4.2 First-sign-in capture (irreversible)

Apple returns the user's `fullName`/`email` **only on the very first authorization, ever**. Capture them on first sign-in and persist to `users` immediately — they cannot be retrieved again. ([Supabase docs](https://supabase.com/docs/guides/auth/social-login/auth-apple)) The relay email may be a private `@privaterelay.appleid.com` address; store it as-is.

### 4.3 Provider config + the nonce-encoding gotcha

- Configure the Apple provider in the Supabase dashboard: Services ID, Team ID, Key ID, `.p8` key.
- **Known GoTrue bug** ([supabase/auth #2378](https://github.com/supabase/auth/issues/2378)): historically GoTrue hex-encoded the nonce hash while Apple uses base64url, causing "Nonces mismatch." Verify the running GoTrue version handles Apple base64url; an integration test that performs a real round-trip is required (also see Open Questions).

### 4.4 Auth states (UI)

| State | Behavior |
|---|---|
| **Loading** | Calm spinner on cream; no copy churn. |
| **First success** | Capture name/email → write `users` → route to `03-onboarding.md`. |
| **Returning success** | Restore session (Keychain), route to Today. |
| **Canceled** | Silent return to the calm sign-in screen; no error styling. |
| **Error / nonce mismatch** | Quiet declarative copy ("Let's try that again"), log to Sentry, never expose the token. |
| **Offline** | Defer; show a single retry affordance, not a stack trace. |

---

## 5. Storage, signed URLs & the orchestration service

### 5.1 Buckets and the key convention

Supabase Storage buckets are **private by default**; private means every operation (including download) goes through RLS on `storage.objects`, and uploads are impossible without an INSERT policy. ([Storage buckets](https://supabase.com/docs/guides/storage/buckets/fundamentals); [Access control](https://supabase.com/docs/guides/storage/security/access-control))

| Bucket | Visibility | Holds | Backend |
|---|---|---|---|
| `recordings` | **private** | raw batch/repurpose source video (face + voice) | Cloudflare R2 preferred for large source; Supabase for thumbnails/derived (Open Q.) |
| `clips` | **private** | rendered output clips | R2 + Stream for delivery |
| `thumbnails` | private | derived thumbnails | Supabase Storage |
| `avatars` | public | non-sensitive avatars only | Supabase Storage |

**Object-key convention = `{user_id}/...`** so RLS reads ownership straight from the path. Postgres arrays are **1-indexed** — the first path segment is `[1]`, not `[0]`. ([SecureStartKit — multi-tenant storage RLS](https://securestartkit.com/blog/supabase-storage-multi-tenant-rls-2026))

```sql
create policy "own recordings read" on storage.objects
  for select to authenticated
  using (
    bucket_id = 'recordings'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );
-- Mirror INSERT/UPDATE/DELETE policies with WITH CHECK on the same predicate.
```

### 5.2 Signed URLs are unbound bearer tokens — treat as secrets

A signed URL is an **unbound bearer token**: anyone holding it can fetch the object until expiry, ignoring RLS and owner checks. Therefore:

- **Cap expiry at minutes-to-hours**, never days.
- **Generate per-render, server-side** (FastAPI / Edge Function) so `service_role` is never on the client.
- **Store the object KEY in the DB** (`recordings.source_object_key`, `clips.output_object_key`) — **never** the signed URL.
- Treat the URL as a **secret** in the threat model (§9). ([SecureStartKit](https://securestartkit.com/blog/supabase-storage-multi-tenant-rls-2026); [Serving downloads](https://supabase.com/docs/guides/storage/serving/downloads))

Signed URLs use a **separate internal signing key** unaffected by Auth JWT rotation, and conversely **cannot be revoked** once issued (except via Supabase support) — another reason for short TTLs. ([Serving downloads](https://supabase.com/docs/guides/storage/serving/downloads))

**Publish-time minting:** Instagram's Content Publishing fetches a **publicly reachable `video_url`** server-side, so the pipeline mints a short-lived signed/public URL (R2 public or Stream) precisely at publish, then lets it expire. TikTok `PULL_FROM_URL` requires the **URL prefix/domain be pre-verified** with TikTok first, and the `upload_url` is valid only **1 hour**. ([Meta Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/); [TikTok Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post))

### 5.3 FastAPI orchestration service — responsibilities

FastAPI is Plane 3. It is the only place secrets live. It:

- Holds all vendor secrets + `service_role` (`Anthropic`, `AssemblyAI`, `Shotstack`, `Ayrshare`/`Phyllo`, `RevenueCat`, `R2`).
- Validates every input (Pydantic, §8).
- Owns all **Claude Opus 4.8 / Haiku 4.5** calls (prompt caching + structured tool outputs, see `07-ai-system.md`).
- Enqueues durable work to Trigger.dev.
- Verifies entitlements server-side (§10) before any premium AI/render op; debits `credits` transactionally.
- Mints publish-time signed/public URLs.
- Receives RevenueCat + Ayrshare webhooks (idempotent).

The iOS app talks **only** to FastAPI (JWT-authed) and Supabase (RLS-scoped) — never to vendors directly. The full FastAPI surface — endpoint inventory, auth model, idempotency-key convention, error envelope, and versioning — is the **canonical API contract in §11**; sibling docs reference endpoints there rather than inventing them inline.

### 5.4 Trigger.dev v3 — durable jobs

Trigger.dev v3 is purpose-built for Marque's long-running pipeline: no timeouts, and Checkpoint-Resume (CRIU) so tasks paused on AssemblyAI transcription / Shotstack render / AI generation do not bill idle compute. ([Trigger.dev — how it works](https://trigger.mintlify.dev/docs/how-it-works); [media processing](https://trigger.dev/docs/guides/use-cases/media-processing))

| Pattern | Use in Marque |
|---|---|
| **Router + Coordinator + `batchTriggerAndWait`** | one `recording` fans out to parallel clip renders (one subtask per format/clip) → gather → upload → notify; mirrored in `render_jobs` rows. |
| **`idempotencyKeys.create(recordingId)`** (run-scoped) | retries reuse cached subtask results; only the failed subtask re-runs. Mirrored by `render_jobs.idempotency_key`. |
| **`wait.forToken`** | the human approval gate — the creator reviews/edits clips before scheduling — without burning compute. |
| **Concurrency caps** | respect downstream rate limits (Shotstack, IG 50/24h, TikTok 6 req/min). |

**Hosting:** self-hosting v3 is hard (needs CRIU); use Trigger.dev **cloud** unless a constraint forces otherwise (Open Question).

### 5.5 Platform posting caps — enforce *before* scheduling

The publisher must enforce caps locally (mirror in `social_connections`) to avoid account lockouts:

- **Instagram:** 50 published posts / 24h moving window per professional account (treat 50 as the safe ceiling; carousel = 1). Read current usage via `GET /{ig-id}/content_publishing_limit`. Requires `instagram_basic` + `instagram_content_publish` and a professional/business account. ([Meta Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/))
- **TikTok:** an **unaudited** client forces posts to `SELF_ONLY` (private) and caps at **5 users / 24h**; going public requires passing TikTok's **audit** (a launch-blocking milestone — Open Question). Even audited: ~15 posts/creator/day shared across clients, ≤5 pending shares/24h, `/init` and `/upload` rate-limited to **6 req/min**. Must call **Query Creator Info** and surface TikTok's privacy/interaction options before posting. ([TikTok content-sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines))

### 5.6 Pipeline states (Record → Publish)

| State | DB reflection | UI (calm) |
|---|---|---|
| **Uploading** | `recordings.status = uploading` | one breathing progress line |
| **Transcribing** | `recordings.status = transcribing` | "Listening to your session" |
| **Rendering** | `render_jobs.status = running` | per-clip soft shimmer, no dashboard |
| **Waiting approval** | `render_jobs.status = waiting_approval` | one directive: "Review your clips" |
| **Empty** | no recordings yet | single Record CTA |
| **Error** | `*.status = failed` + `error_code` | quiet retry; Coach explains if platform-side |
| **Offline** | client-cached last-known | queued; reconciles on foreground |
| **Permission-denied** | RLS 403 / scope error | re-auth prompt, never raw error |

---

## 6. Secrets management

- **No secret ships in the app binary.** The IPA contains only the Supabase URL + the **anon/publishable** key (safe because RLS). ([davidmuraya — FastAPI security](https://davidmuraya.com/blog/fastapi-security-guide/))
- All vendor keys + `service_role` load into FastAPI from a **secrets manager** (Doppler / AWS Secrets Manager / Vault) at deploy time — never a committed `.env`, never baked into the image. ([Fastro — production guide](https://benavlabs.github.io/FastAPI-boilerplate/user-guide/production/))
- **Validate at boot** that production secrets are present and non-default; fail closed if not.
- `service_role` is the highest-value secret; it lives only in FastAPI / Trigger.dev / Edge Functions and is never logged.

| Key | Plane | Notes |
|---|---|---|
| Supabase anon/publishable | iOS app | safe; RLS-gated |
| `service_role` | FastAPI / Trigger.dev | BYPASSRLS — never client-visible |
| Anthropic (Opus 4.8 / Haiku 4.5) | FastAPI | prompt-caching trust boundary |
| AssemblyAI · Shotstack · R2 | FastAPI / Trigger.dev | pipeline |
| Ayrshare / Phyllo | FastAPI | publish + insights |
| RevenueCat **secret** key | FastAPI | webhook verify + REST entitlement check |

---

## 7. Encryption, PII & sensitive-video retention

### 7.1 Encryption

- **In transit:** TLS/HTTPS everywhere — Supabase, FastAPI behind an HTTPS proxy, R2/Stream.
- **At rest:** Supabase Postgres + Storage and Cloudflare R2 are encrypted at rest by default.
- **Application-layer encryption** for social OAuth tokens in `social_connections.access_token_enc` (pgsodium / Supabase `vault`, or KMS-wrapped). A stolen token = the ability to post to a creator's account — encrypting it is non-negotiable.

### 7.2 Sensitive video is the highest-PII asset

A recording is the creator's **face and voice** — biometric-adjacent data. Handling rules:

- Store in **private** buckets / R2 keyed by `{user_id}`; access only via short-lived, server-minted signed URLs (§5).
- Record **consent for AI processing** of face/voice at onboarding (`users.ai_consent_at`); co-owned with `14-appstore-compliance-legal.md`.

### 7.3 Retention windows + deletion

| Asset | Retention | Mechanism |
|---|---|---|
| Abandoned **draft** uploads | auto-purge after **N days** (default 14; Open Q.) | scheduled purge job reads `deleted_at`/staleness |
| Raw recording **source** | retained while referenced; purged on account deletion | `on delete cascade` + storage-object wipe |
| Published **clips** | retained per active subscription | tier-aware retention |
| `post_metrics` | retained for the learning loop | aggregate; no media |

**Account deletion (App Store + GDPR/CCPA "right to erasure"):** a single deletion job (a) cascades DB deletes via `on delete cascade`, and (b) **wipes the corresponding Storage/R2 objects** — DB cascade alone leaves orphaned media. This is launch-blocking for App Review's account-deletion requirement.

```
DELETE auth.users → cascade public.* rows → deletion job enumerates
{user_id}/ prefixes in recordings/clips/thumbnails buckets + R2 →
hard-delete objects → write an `events` audit row (service_role).
```

---

## 8. Rate limiting, abuse prevention & input validation

### 8.1 Rate limiting (FastAPI)

- **Layer it:** a global gateway limit + per-user limits in middleware.
- **Key on the authenticated `user_id`, not IP** (bots rotate IPs) — therefore **run rate limiting AFTER auth**. ([Darshan Turakhia — FastAPI rate limiting](https://darshanturakhia.com/lab/fastapi/rate-limiting-throttling))
- Use a **Redis sliding-window** counter (in-process counters multiply by worker count and allow boundary attacks).
- Always return `Retry-After` + `X-RateLimit-Remaining` on 429 so the iOS client backs off cleanly (prevents retry storms).
- **Tighter limits on expensive endpoints** (AI script generation, render enqueue, publish) than on reads.

| Endpoint class | Suggested limit (per user) | Why |
|---|---|---|
| Reads (scripts, clips, metrics) | generous | cheap |
| Script generation (Opus 4.8) | tight | token cost |
| Render enqueue | tight + credit debit | compute cost + downstream caps |
| Publish | bounded by IG/TikTok caps (§5.5) | account-lockout risk |

### 8.2 Input validation

- **Strict Pydantic models** on every endpoint — body, path, and query. ([oneuptime — FastAPI/OWASP](https://oneuptime.com/blog/post/2025-01-06-fastapi-owasp-security/view))
- Constrain ID params with `pattern="^[\w-]*$"` to block path traversal/injection.
- Cap request body size; parameterized queries only.

### 8.3 SSRF guard (repurpose-in is the attack surface)

The pipeline fetches **user-supplied URLs** for repurpose-in uploads and thumbnails. Validate/allowlist URL **schemes and hosts** before fetching — SSRF is explicitly on the OWASP checklist. ([oneuptime](https://oneuptime.com/blog/post/2025-01-06-fastapi-owasp-security/view))

### 8.4 Production hardening

- **CORS:** production origins only; never `*` with credentials.
- Disable `/docs` and `/redoc` in production. ([Fastro](https://benavlabs.github.io/FastAPI-boilerplate/user-guide/production/))
- **Credits ledger** doubles as an abuse throttle: expensive ops debit `credits` transactionally; a depleted balance blocks abuse before it reaches a vendor (§10).

---

## 9. Threat model (STRIDE-ish)

| # | Threat | Vector | Mitigation |
|---|---|---|---|
| a | **Anon-key data exfiltration** | client queries Postgres directly | RLS enabled + correct on **every** table; negative tests (§2.5) |
| b | **`service_role`/secret leakage** | secret in binary or logs | server-only + secrets manager (§6); never logged |
| c | **Signed-URL leakage of face/voice** | URL forwarded/captured | short TTL, per-render mint, key-in-DB, URL = secret (§5.2) |
| d | **Paywall bypass (jailbroken device)** | spoofed client entitlement | server-authoritative entitlements via RC webhooks/REST (§10) |
| e | **Credit / AI abuse** | scripted expensive calls | per-user rate limits + transactional credit debits (§8) |
| f | **Webhook replay** | replayed RC/Trigger event | idempotency keys (RC `event.id`, Trigger idempotency keys) (§5.4, §10) |
| g | **Social-token theft** | DB read of `social_connections` | application-layer encryption of tokens (§7.1) |
| h | **SSRF via repurpose-in URLs** | malicious upload URL | scheme/host allowlist before fetch (§8.3) |
| i | **IG/TikTok account lockout** | exceeding posting caps | enforce caps client/server-side before scheduling (§5.5) |
| j | **Audit-trail forgery** | client writes fake `events` | security-audit events written only via `service_role` (§3.7) |

---

## 10. Subscriptions & credits — server-authoritative (summary)

Full paywall UX is in `11-monetization.md`; this is the backend trust boundary. **Never trust the client for an access decision.**

- RevenueCat verifies receipts server-side; for anything FastAPI serves (premium AI/render endpoints), verify entitlement via **RC webhooks → `subscriptions` table** or the REST API `GET /v1/subscribers/{app_user_id}` (authenticated with the RC **secret** key, server-only). Client `CustomerInfo` is a cache the server can override. ([RevenueCat — backend architecture](https://www.revenuecat.com/guides/revenuecat-android-sdk/backend-architecture); [security](https://www.revenuecat.com/guides/revenuecat-android-sdk/security))
- Identify users with the authenticated Supabase `user_id` as the RevenueCat `app_user_id` so webhooks map cleanly to `subscriptions`.
- **Webhook handler rules** ([RevenueCat — webhooks](https://www.revenuecat.com/guides/revenuecat-android-sdk/webhooks)): verify `X-RevenueCat-Signature`; **idempotent on `event.id`** (`subscriptions.rc_last_event_id`); gate on `entitlement_ids`. `INITIAL_PURCHASE/RENEWAL/UNCANCELLATION` → grant; `EXPIRATION` → revoke; `CANCELLATION` → schedule revoke at `expiration_at_ms` (do **not** revoke immediately — access through period end); `BILLING_ISSUE` → flag (`status = billing_issue`). Return 2xx fast; RC retries with backoff.
- StoreKit 2 + RevenueCat is the **only** iOS paywall (Apple IAP mandate). Stripe is reserved for a future web billing surface — never the iOS paywall.

---

## 11. The canonical FastAPI API contract

The iOS app talks to exactly two backends — **Supabase** (RLS-scoped, anon key) for owned-row CRUD and Realtime, and **FastAPI** (JWT-authed) for every privileged action (§1). Endpoints had been invented piecemeal across sibling docs (`05`/`09` `POST /sessions/{id}/process`, `11` `POST /jobs/render`, `13` `POST /v1/devices`, `14` export/delete, `03`/`08` a `today_directive` resolver, `17` the orchestration surface). **This section is the single owning inventory.** Sibling docs reference an endpoint by `method + path` here instead of declaring their own. The architecture/adapter-DI owner is this file (§5.3 + this section), not the IA doc (`01-information-architecture.md`, see the cross-reference table); the maps that cite "`01-information-architecture.md`" mean **this** contract.

### 11.1 Conventions (apply to every endpoint)

- **Base + versioning:** all routes are prefixed `/v1`. Versioning is **URL-path major** (`/v1`, `/v2`); breaking changes bump the prefix, additive changes do not. `/healthz` and `/readyz` are **unversioned** (ops probes).
- **Auth:** `Authorization: Bearer <supabase_access_token>`. FastAPI verifies the JWT signature against Supabase's JWKS and extracts `sub` (= `user_id`); the route handler scopes all work to that `user_id`. **The `subscription_tier` claim is never trusted for access** — entitlement is re-checked against `subscriptions` per §2.4/§10. Webhook routes (`/webhooks/*`) are unauthenticated by JWT and instead verified by provider signature.
- **Idempotency:** every **non-GET** endpoint accepts an `Idempotency-Key` header (client-generated UUID, required on `POST` that creates billable/durable work). FastAPI stores `(user_id, idempotency_key) → response` and replays the stored response on retry. For render/publish this maps to `render_jobs.idempotency_key` and Trigger.dev `idempotencyKeys.create(...)` (§5.4).
- **Error envelope:** every 4xx/5xx returns one shape — `{ "error": { "code": "<§3.9 canonical code or http-ish code>", "message": "<safe, declarative>", "retriable": <bool>, "request_id": "<uuid>" } }`. Domain failures (publish/render/transcribe) use the **§3.9 taxonomy** verbatim so the client can branch on `family`. Never leak vendor payloads or tokens (§4.4, §9-b).
- **Rate limiting:** applied **after** auth, keyed on `user_id`, with `Retry-After` + `X-RateLimit-Remaining` on 429 (§8.1).
- **Idempotency-Key + version negotiation** failures return `409` / `400` in the envelope above.

### 11.2 Endpoint inventory

| Method + path | Purpose | Auth | Idempotent | Owner doc |
|---|---|---|---|---|
| `GET /healthz` | liveness probe (process up) | none | n/a | this doc (§5.3) |
| `GET /readyz` | readiness (DB + Trigger.dev + secrets loaded) | none | n/a | this doc (§6) |
| `POST /v1/brand-graph/seed` | ingest IG/TikTok handles → Opus 4.8 brand thesis | JWT | yes | `03-onboarding.md` |
| `GET /v1/today/directive` | the single Today directive (resolver over `v_today_directive`) | JWT | n/a | `04-screens-create.md` |
| `POST /v1/scripts/generate` | Virality Engine script gen (Opus 4.8, credit-debited) | JWT | yes | `08-format-virality.md` / `07-ai-system.md` |
| `POST /v1/sessions/{recording_id}/process` | kick off transcription + clip detection + render fan-out | JWT | yes | `05-screens-produce.md` |
| `POST /v1/jobs/render` | enqueue a render for one clip/format (Trigger.dev) | JWT | yes | `08-format-virality.md` |
| `POST /v1/clips/{clip_id}/approve` | resolve the `wait.forToken` approval gate (§5.4) | JWT | yes | `05`/`06` |
| `POST /v1/schedules` | schedule a clip to IG/TikTok (cap pre-check, §5.5) | JWT | yes | `10-social-publishing.md` |
| `POST /v1/posts/{post_id}/retry` | retry a failed publish (keys off §3.9 `retriable`) | JWT | yes | `08` / `05-screens-produce.md` |
| `POST /v1/social-connections` | begin/store an Ayrshare/Phyllo connection | JWT | yes | `08` |
| `POST /v1/devices` | register/refresh an APNs token | JWT | yes (upsert on token) | `13-notifications-retention.md` |
| `POST /v1/account/export` | start the data-export job (GDPR/CCPA) | JWT | yes | `14-appstore-compliance-legal.md` / `14-appstore-compliance-legal.md` |
| `POST /v1/account/delete` | start the account-deletion job (§7.3) | JWT | yes | `14` / `11` |
| `POST /webhooks/revenuecat` | RC entitlement events (idempotent on `event.id`, §10) | RC signature | yes | `11-monetization.md` |
| `POST /webhooks/trigger` | Trigger.dev run-status callbacks → `render_jobs` | Trigger signature | yes | this doc (§5.4) |
| `POST /webhooks/ayrshare` | publish/analytics callbacks → `posts` / `post_metrics` | Ayrshare signature | yes | `08` / `09` |

> Paths above are the canonical strings. Where a sibling doc historically wrote an unversioned variant (`POST /jobs/render`, `POST /v1/devices` already-versioned, `POST /sessions/{id}/process`), the `/v1`-prefixed form here wins. A checked-in OpenAPI document (`openapi.json`, generated from the FastAPI app) is the machine-readable mirror of this table and the contract the Swift client codegen targets. `/docs` + `/redoc` are disabled in production (§8.4); the OpenAPI artifact is published to the repo, not served live.

### 11.3 API-contract acceptance criteria

- [ ] Every non-GET endpoint enforces `Idempotency-Key` and replays the stored response on retry.
- [ ] Every error response conforms to the §11.1 envelope; domain errors carry a §3.9 canonical `code`.
- [ ] The generated `openapi.json` is checked in and CI-diffed; a drift between it and this table fails the build.
- [ ] No endpoint trusts the `subscription_tier` JWT claim for access (§2.4); premium routes re-verify against `subscriptions`.
- [ ] Webhook routes verify provider signatures and are idempotent on the provider event id.

---

## Open questions

1. **Trigger.dev hosting** — cloud vs. self-host. Self-hosting v3 requires CRIU and is operationally hard; recommend **cloud** unless a data-residency or cost constraint forces otherwise. *Owner: Backend lead.*
2. **TikTok audit timeline** — passing TikTok's audit is launch-blocking for public posting (unaudited = private + 5-user cap). Who owns the submission and what's the dependency on App Review? *Owner: Product + Backend.*
3. **Biometric-data legal exposure** — storing/processing creator face + voice may trigger BIPA (Illinois) and similar state laws. Needs counsel, an explicit consent record (`users.ai_consent_at` is the hook), and a final retention-window decision. *Owner: Legal + `14-appstore-compliance-legal.md`.*
4. **GoTrue Apple nonce encoding** — confirm the running Supabase/GoTrue version handles Apple's base64url nonce (issue #2378) on whatever tier we deploy; gate release on a real round-trip integration test. *Owner: Backend lead.*
5. **Source-video residency split** — final call on Cloudflare R2 (large source) + Stream (delivery) + Supabase Storage (thumbnails/derived). Recommend that split; it affects the `storage_backend` column and the signed-URL/RLS story. *Owner: Backend lead.*
6. **Draft retention window `N`** — default proposed 14 days for abandoned uploads; confirm against cost + creator expectation. *Owner: Product.*

## Sources

1. [Supabase — Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security) — canonical RLS rules, `auth.uid()`, `security_invoker` views, perf recommendations.
2. [adamarant — RLS at scale, 7 patterns](https://adamarant.com/en/blog/supabase-rls-at-scale-7-patterns-for-queries-that-stay-fast) — subselect wrap, indexing, `SECURITY DEFINER` to break recursion.
3. [Wonsuk Choi — production RLS patterns](https://wonsukchoi.co/en/blog/supabase-rls-production-patterns) — `TO authenticated`, `search_path=''`, service_role danger, custom token hook.
4. [MakerKit — Supabase RLS best practices](https://makerkit.dev/blog/tutorials/supabase-rls-best-practices) — when to use `SECURITY DEFINER` + privilege-escalation guard.
5. [Jake's Insights — RLS performance benchmark](https://jakeinsight.com/tech/2026-03-24-supabase-postgres-row-level-security-performance-i/) — 100k-row latency (indexed vs not, subselect vs not).
6. [AgileSoftLabs — Supabase RLS guide 2026](https://www.agilesoftlabs.com/blog/2026/06/supabase-row-level-security-guide-2026) — `USING` vs `WITH CHECK` mistakes table.
7. [Supabase — Sign in with Apple (Swift)](https://supabase.com/docs/guides/auth/social-login/auth-apple?platform=swift) — native flow, raw vs hashed nonce, first-sign-in name/email.
8. [supabase/auth #2378](https://github.com/supabase/auth/issues/2378) — Apple nonce hex-vs-base64url "Nonces mismatch" bug.
9. [Supabase — Storage access control](https://supabase.com/docs/guides/storage/security/access-control) — RLS on `storage.objects`, `{user_id}` folder pattern, 1-indexing.
10. [Supabase — Storage buckets fundamentals](https://supabase.com/docs/guides/storage/buckets/fundamentals) — private-by-default buckets.
11. [SecureStartKit — multi-tenant Storage RLS](https://securestartkit.com/blog/supabase-storage-multi-tenant-rls-2026) — signed URLs as unbound bearer tokens; short TTL, store key not URL.
12. [Supabase — serving downloads](https://supabase.com/docs/guides/storage/serving/downloads) — signed-URL signing key + non-revocability.
13. [Trigger.dev — how it works](https://trigger.mintlify.dev/docs/how-it-works) & [media processing](https://trigger.dev/docs/guides/use-cases/media-processing) — durable jobs, idempotency keys, `batchTrigger` fan-out, `wait.forToken`.
14. [Meta — Instagram Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/) — container→publish, 50/24h cap, public `video_url`, `content_publishing_limit`.
15. [TikTok — content-sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) & [Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post) — unaudited caps, audit requirement, 6 req/min, `PULL_FROM_URL` prefix verification, error codes.
16. [RevenueCat — webhooks](https://www.revenuecat.com/guides/revenuecat-android-sdk/webhooks), [backend architecture](https://www.revenuecat.com/guides/revenuecat-android-sdk/backend-architecture), [security](https://www.revenuecat.com/guides/revenuecat-android-sdk/security) — server-authoritative entitlements, signed idempotent webhooks.
17. [Darshan Turakhia — FastAPI rate limiting](https://darshanturakhia.com/lab/fastapi/rate-limiting-throttling) — auth-before-rate-limit, key-by-user-id, Redis sliding window, `Retry-After`.
18. [oneuptime — FastAPI OWASP security](https://oneuptime.com/blog/post/2025-01-06-fastapi-owasp-security/view) — Pydantic validation, SSRF guard, CORS, disable docs.
19. [davidmuraya — FastAPI security guide](https://davidmuraya.com/blog/fastapi-security-guide/) & [Fastro — production guide](https://benavlabs.github.io/FastAPI-boilerplate/user-guide/production/) — secrets from a manager, boot-time validation, production hardening.
20. [Supabase — Custom Access Token Hook](https://supabase.com/docs/guides/auth/auth-hooks/custom-access-token-hook) — stamping `subscription_tier` into the JWT at issue time; why it is a point-in-time claim.
21. [Supabase — sessions & token lifetimes](https://supabase.com/docs/guides/auth/sessions) — access-token TTL, non-revocability of issued access tokens until expiry (the §2.4 staleness window).
