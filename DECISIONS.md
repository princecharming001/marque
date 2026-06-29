# Marque — Locked Decisions

> The single source of truth for **why this, not that**. Every choice below is *locked*: the section docs build on these and must not contradict them. If a decision needs to change, change it here first, then propagate. Genuinely-unresolved choices live in [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md), not here.

**Status:** Canonical · **Last updated:** 2026-06-29

---

## 1. The locked stack (every vendor + what it's for + the adapter that hides it)

**Doctrine:** *Adapters hide every vendor. Swapping a vendor is a one-file change. No call site names a vendor string.* The adapter is the contract; the vendor is an implementation detail behind it.

| Layer | What it does | Vendor (v1) | Adapter / boundary | Why this, not that |
|---|---|---|---|---|
| **iOS app** | The client | Swift + SwiftUI (iOS 17+), AVFoundation (camera + teleprompter), Swift Concurrency, Observation framework | — (the app itself) | Native is mandatory for camera/teleprompter quality, StoreKit, and the "not-a-wrapper" App Store posture (Guideline 4.2/4.3). iOS 17+ unlocks Observation, `@Entry`, value-based `Tab`. |
| **Data / Auth / Storage / Realtime** | Postgres, auth, object storage, live updates | **Supabase** | Direct (with RLS); the only thing the untrusted client holds is the anon key | One integrated backend with row-level security as the authorization spine. Postgres (not a document store) because the Brand Graph is relational + bitemporal. |
| **Orchestration service** | Owns all secrets; coordinates long jobs | **FastAPI** (Python) + **Trigger.dev** | The backend boundary; Trigger.dev tasks carry idempotency keys | Durable, idempotent, re-runnable video/AI jobs that survive app death. Trigger.dev v3 has no execution-time limit and first-class fan-out — the right shape for "1 transcribe → N renders." |
| **LLM** | Scripts, brand reasoning, teardowns, classification | **Claude — Opus 4.8** (reasoning) + **Haiku 4.5** (bulk) via the **Anthropic API** | **`LLMRouter`** | Two-tier routing: Opus for scripts/brand reasoning/teardowns, Haiku for bulk classification + voice checks. Prompt caching (0.1× reads) + structured tool outputs make the Brand Graph a cheap cached prefix. |
| **Clip pipeline** | Reframe, personal-clipper, video analysis, virality prediction, image/video gen | **MCP creative toolchain** (v1), in-house later | **`ClipEngine`** | "Build on MCP first, then bring in-house." Ship the loop without owning the render farm; the adapter makes the migration a one-file swap. |
| **Transcription / moment detection** | Word-level transcript + highlights for trims | **AssemblyAI** | Behind `ClipEngine` / pipeline | Word-boundary timestamps drive caption sync + trim snapping. (Note: `auto_chapters`/`summarization` are deprecated → moment *reasoning* is done with Claude, not those flags.) |
| **Templated rendering** | Split-screen, captions, overlays, kinetic text | **Shotstack** | Behind `ClipEngine` | Formats = render-recipes = **Shotstack Templates with merge fields**. Templated rendering is the literal mechanism of the Format Library. |
| **Video storage / delivery** | Source video + delivery | **Cloudflare R2** (source) + **Cloudflare Stream** (delivery) | Storage adapter / signed-URL layer | R2 gives the **public HTTPS URL** that *both* IG (`video_url` cURL) and TikTok (`PULL_FROM_URL`) require — load-bearing. Stream handles adaptive delivery + thumbnails. |
| **Social publish + schedule** | Post to IG + TikTok, schedule | **Ayrshare** (v1) → real targets are **Instagram Graph API** (Content Publishing) + **TikTok Content Posting API** | **`Publisher`** | Ayrshare inherits TikTok audited-client status + Meta App Review and holds the platform tokens in v1, so Marque ships faster. The adapter lets a future `DirectPublisher` (own audits) drop in. |
| **Analytics pullback** | Post performance metrics | **Phyllo / Ayrshare** | **`Insights`** | Feeds the optimal-time model, Coach teardowns, and the Virality Engine learning loop. Separate adapter from `Publisher` so the metrics source can differ from the publish source. |
| **Subscriptions** | IAP, entitlements, paywall | **StoreKit 2 + RevenueCat** | **`Billing`** | Apple IAP is *mandatory* for iOS digital subscriptions. RevenueCat is the entitlement source of truth, mirrored into Supabase for server-side gating. **Stripe is reserved for a future web surface only — never the iOS paywall.** |
| **Push** | Notifications | **APNs** (token-based .p8/ES256) via the FastAPI backend | Backend send-governor | Token auth (one signing key, all environments) over certificate auth. The backend enforces caps + quiet hours so no feature can spam Today. |
| **Analytics / crash / flags** | Product analytics, crash reporting, feature flags + remote config | **PostHog** + **Sentry** + remote config | Direct SDKs (behind a thin event wrapper) | PostHog for the AARRR funnel + flags + JSON remote config; Sentry for crashes + distributed tracing across the pipeline (one `trace_id`). |

> **Future web billing only:** Stripe exists in the plan exclusively for a *web* billing surface that does not exist yet. It must never appear in the iOS binary. See `11-monetization.md` and `14-appstore-compliance-legal.md`.

---

## 2. The locked aesthetic (modeled on the Stoic journaling app)

**Doctrine:** quiet, declarative, slightly philosophical. One idea per screen. The interface recedes so the creator's work is the subject. Full token system lives in `02-design-system.md`.

| Decision | Locked value | Rationale |
|---|---|---|
| **Surfaces** | Warm cream `#F4F1EA` (light) / near-black `#0E0E10` (dark) — **never pure white/black** | Pure white/black reads as "tech product." Warm cream + near-black reads as paper + ink: calm, premium, easy on the eyes. |
| **Display type** | High-contrast serif (Playfair Display / Tiempos) for titles | Serif display gives editorial gravity and the "slightly philosophical" voice; the high stroke-contrast is the signature. (v1 defaults to OFL-licensed Playfair; paid Tiempos/Söhne is an open question.) |
| **Body / UI type** | Clean grotesque (Inter / Söhne / Matter) | Neutral, legible body that disappears under the serif headlines. |
| **Accent** | A single warm gold `#C9A227`, used **sparingly** | One accent, used as a whisper. **Hard rule:** gold fails AA as text on cream (~2.1–2.9:1), so gold is restricted to **glyphs, large display, hairlines, or fills with an ink label** — never body/label text. |
| **Layout** | Huge whitespace, **one idea per screen**, soft single-direction shadows, subtle paper texture | Whitespace *is* the premium signal. Soft shadows + paper texture make it tactile, not flat-corporate. |
| **Motion** | Slow, eased "breathing" motion; calm enter/quick/breath curves; mandatory Reduce-Motion path | Motion should feel like a held breath, never a bounce. Calm motion reinforces the "this app lowers my stress" promise. |
| **Copy** | Quiet, declarative, slightly philosophical ("What do you want to be known for?") | The copy is part of the product's emotional contract with an overwhelmed creator. |
| **Errors** | Calm, never red, never a modal-only dead end — one declarative line + one tap to act | An error screen must not spike the anxiety the app exists to lower. |

---

## 3. The anti-clutter doctrine (binding across every section)

> **The Today home screen shows exactly ONE directive at a time + a small gold streak glyph + one trend line. Nothing else.** Every other feature is one layer deep, surfaced contextually, or lives in its own calm screen. **Never bolt features onto Today.**

A milestone, feature, or PR is **not done** if it added a second element to Today. This is the rule that makes Marque feel calm despite shipping a large feature set. The Section-8 features are all included — placed tastefully, never cluttering:

| Section-8 feature | Placement (never on Today) |
|---|---|
| **Batch "film once → post all week"** | The **hero loop** — the spine of Studio → Record → Library → Calendar, not an add-on. |
| **Trend Radar** | **One line** on Today → a dedicated **Trends** screen for the rest. |
| **Hook Lab** | **Nested inside the Script Reader** via progressive disclosure — never its own cluttering screen. |
| **Performance teardown cards** | The **Coach** feed + one push; archived in **Insights**. |
| **Streaks / consistency** | **One gold glyph** on Today; full view in **Profile**. |
| **Repurpose-in (upload existing long video)** | A **second source toggle on Record** — same pipeline. |
| **Referral loop** | A **row in Settings** + **one earned prompt** after a genuine win — never nagging. |

---

## 4. Key product decisions (why this, not that)

### 4.1 The Brand Graph + Virality Engine are the moat — not the clip-cutting
Opus Clip, Vizard, Captions, HeyGen, and Revid all cut clips. **None owns a persistent context layer.** Marque's wedge is the **batch-talking-head loop** wrapped around a **Brand Graph** that compounds: it learns the creator's voice, pillars, audience, and what performs, and injects that into every script and format choice. The clip pipeline is table-stakes infrastructure (hence MCP-first); the **context that compounds** is the defensible asset. See `00-overview.md`, `06-brand-graph.md`.

### 4.2 Formats are structured RENDER-RECIPES, not blank talking heads
The **Format Library** is not "pick an aspect ratio." Each format (split-screen, 3-up talking heads, green-screen, faceless AI-visual, before/after, myth-buster, listicle, POV, reaction, B-roll+caption-hook) is a structured render-recipe mapped **1:1 onto a Shotstack Template with merge fields**. A format swap = a template re-render with the same merge fields. This is what makes the output feel *produced*, not auto-captioned. See `08-format-virality.md`, `09-video-pipeline.md`.

### 4.3 MCP-first video path, in-house later
Build the clip toolchain on the **MCP creative toolchain** first (personal-clipper, reframe, video-analysis, virality-predictor, image/video gen), behind the **`ClipEngine`** adapter. This ships the loop without standing up a render farm. Bringing it in-house (FFmpeg/Remotion) is a deliberate **post-v1** migration that the adapter makes a one-file change. *Why not in-house from day one:* it would delay the hero loop by months for infrastructure the adapter lets us defer.

### 4.4 Apple IAP, not Stripe, on iOS
iOS digital subscriptions **must** use Apple In-App Purchase (StoreKit 2 + RevenueCat). Stripe in the iOS binary is an instant rejection (Guideline 3.1.1) and is therefore **forbidden** in the app. Stripe is reserved exclusively for a *future* web billing surface. RevenueCat is the entitlement source of truth; entitlements are mirrored into Supabase for server-side gating and verified via App Store Server Notifications V2. See `11-monetization.md`.

### 4.5 The batch-record loop is the HERO, not a feature
"Film once → post all week" is the product's reason to exist — it's what collapses the production tax. The entire app is arranged to funnel toward and out of **one weekly batch session**: the Brand Graph and Virality Engine *prepare* it (scripts + formats ready), Record *captures* it (teleprompter + repurpose-in), the pipeline *fans it out* (N clips × formats), and publishing + Insights *close the loop*. Record is not a tab you visit occasionally; it's the heartbeat. See `05-screens-produce.md`, `17-roadmap-milestones.md`.

### 4.6 Compliance is a shippable feature set, sequenced early
The app sits on top of *five* high-rejection App Store clusters simultaneously (UGC 1.2, third-party-AI disclosure 5.1.2(i), paywall 3.1.2, not-a-wrapper 4.2/4.3, in-app deletion 5.1.1(v)). These are built in **M1/M2/M5**, not crammed into M6 — M6 only verifies them. The longest-lead external dependencies (Meta IG App Review, the 2–6 week TikTok Content Posting audit) run on a **Day-1 swimlane** that is the program's true critical path. See `14-appstore-compliance-legal.md`, `17-roadmap-milestones.md`.

### 4.7 Two-tier LLM routing (Opus 4.8 + Haiku 4.5)
The locked spec is strictly two-tier: **Opus 4.8** for scripts, brand reasoning, and teardowns; **Haiku 4.5** for bulk classification, voice checks, and the publish-safety gate (often via the 50%-off Batch API for non-interactive work). A Sonnet middle tier is *not* in the locked stack (it's an open question for cost tuning). Prompt caching is mandatory and verified in prod (`cache_read_input_tokens > 0`). See `07-ai-system.md`.

---

## 5. Cross-cutting conventions (applied everywhere)

- **Adapters or it doesn't ship.** No call site names a vendor. Adding a platform, LLM, or render engine is a one-file change.
- **Durable + idempotent by default.** Any external effect (render, publish, paid AI call) runs in a Trigger.dev task with an `idempotencyKey`; payloads >10 MB go to R2, not the task payload.
- **Consent precedes compute.** No paid AI/processing job runs before the AI-data-sharing consent row exists (Guideline 5.1.2(i)).
- **RLS on every table.** Row-level security is the authorization spine; the untrusted client holds only the anon key.
- **Calm errors only.** Never red, never a modal-only dead end — one declarative line + one tap to act.
- **Today stays sacred.** Every change adds capability one layer deep; Today never gains a second element.
- **Verify caching in prod.** Every Brand-Graph-backed Claude call asserts `cache_read_input_tokens > 0`.

---

## 6. What is explicitly *not* locked

These are deferred to [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md) and must not be guessed in section docs:

- **Social publishing provider depth** — Ayrshare-only vs. also pursuing Marque's own TikTok + Meta audits (the single decision that most reshapes the plan).
- **Paid typefaces** — OFL Playfair/Inter vs. budgeting for Tiempos/Söhne.
- **Pricing, credit grants, trial length, referral economics** — genuine PM/finance decisions pending unit-cost data.
- **Embedding model + dimension** for the Brand Graph vector column.
- **iPad support** at v1.
- **Phyllo vs Ayrshare** as the v1 `Insights` default.
- **FastAPI host, Anthropic plan tier, vision-moderation vendor**, and other infra picks.

> If you find yourself about to guess one of these in a section doc, stop and record it in `OPEN-QUESTIONS.md` instead.
