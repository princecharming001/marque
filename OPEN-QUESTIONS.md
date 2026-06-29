# Marque — Open Questions & Credentials Checklist

> Every unresolved decision across the spec, aggregated and grouped, plus the **credentials/keys to paste** checklist. Each question is tagged with the section doc(s) that raised it so you can read the surrounding context. When a question is answered, record the answer in [`DECISIONS.md`](DECISIONS.md) and remove it here.

**Status:** Living · **Last updated:** 2026-06-29

---

## ⭐ The one decision that most reshapes the plan

**Social publishing provider depth.** Does v1 rely *solely* on **Ayrshare** (which inherits TikTok audited-client status + Meta App Review and holds the platform tokens), or do we *also* pursue Marque's own **TikTok Content Posting API audit** + **Meta App Review** for a future `DirectPublisher`?

This sets the **longest-lead clock** in the whole program. The TikTok audit takes **2–6 weeks** and frequently rejects "personal account utility" framing; unaudited, every TikTok post is force-downgraded to `SELF_ONLY` and capped at ≤5 test accounts. If we pursue our own audits, they must be submitted on **Day 1 (M0)** against a working demo + screencast, and we own token-refresh forever. If Ayrshare-only, we ship faster but inherit their constraints.
*Raised by:* `00-overview.md`, `05-screens-produce.md`, `08-format-virality.md`, `10-social-publishing.md`, `17-roadmap-milestones.md`.
**→ Decide this first. Everything in the publishing milestone (M4) and the audit swimlane depends on it.**

---

## A. Platform, publishing & audits

| # | Question | Raised by | Recommendation / default |
|---|---|---|---|
| A1 | **TikTok audit absorption.** Does Ayrshare's managed integration absorb the TikTok app audit, or must Marque undergo it directly? (Sub-question of the ⭐ decision.) | 00, 05, 08, 12 | Confirm with Ayrshare before assuming absorption. |
| A2 | **Audit-pending launch posture.** If TikTok PUBLIC isn't approved by submission, ship the provable `SELF_ONLY` posture with the PUBLIC flip staged behind the `Publisher` adapter + flag, or hold? | 14, 17 | §10/§17 assume **ship**; confirm. |
| A3 | **IG rolling-limit UX.** How to surface the ~25/day real-world rolling cap inside a calm one-idea-per-screen UI without violating the anti-clutter doctrine. | 00 | Calm "next slot" line, not a counter. |
| A4 | **IG rate-limit number.** Documented 100/24h vs real-world ~25 (Error-9) — pinned to media_publish ref (50/100); re-confirm at integration. | 08, 10 | Self-enforce the conservative number. |
| A5 | **R2/Stream public domain ownership + readiness.** TikTok `PULL_FROM_URL` and Meta's `video_url` cURL both require a verified public domain. Which domain, and is it verified before M4? | 17 | Owner + date needed. |
| A6 | **Phyllo vs Ayrshare for the `Insights` pullback** at v1, and whether it gates TikTok analytics scopes needing separate approval. | 10, 17 | Pick the v1 default before M5. |
| A7 | **Optimal-time model ownership.** Computed in the FastAPI orchestrator from Insights pullback, or surfaced by Ayrshare/Phyllo? And the audience-timezone source (account tz vs stated audience region vs follower geo). | 05, 10 | Orchestrator-computed; tz source TBD. |
| A8 | **Carousel/photo posts in v1**, or video/Reels + TikTok-video only? (Changes IG container fan-out + adds TikTok photo endpoint.) | 10 | Assumed video-only. |
| A9 | **TikTok inbox-draft fallback** (needs `video.upload` scope) vs library-only save-&-remind. | 09, 10 | Library save-&-remind default. |
| A10 | **Per-platform caption variants** vs a shared caption at schedule time. | 05, 10 | v1 assumes shared caption. |
| A11 | **Does Ayrshare satisfy TikTok's Creator-Info-before-every-post + must-choose-privacy** review requirement, or must Marque surface privacy in its own publish UI? | 09, 10 | Build the mandated UX in Marque to be safe. |
| A12 | **Render boundary.** Does Shotstack do the final template render (MCP only makes B-roll/AI-visual + scores), or does MCP personal-clipper produce finished clips? Decides whether a format swap is a Shotstack re-render or an MCP re-clip. | 05 | Lean Shotstack-final. |
| A13 | **Captions source-of-truth for v1** — AssemblyAI word-timings → custom kinetic template, vs Shotstack built-in caption asset from SRT. | 09 | Not a guessable call. |
| A14 | **Trend Radar data source for GA** — first-party-only (recommended) vs licensing an external trend feed; determines refresh cadence + empty/offline states. | 05, 08 | First-party + daily Claude+web-search pass per niche. |

## B. AI system & Brand Graph

| # | Question | Raised by | Recommendation / default |
|---|---|---|---|
| B1 | **Embedding model + dimension** for the Brand Graph `embedding` column (placeholder `vector(1536)`). Must be locked before the first migration — all indexes + cross-fact comparisons depend on it. Anthropic has no embeddings API, so this also picks the third-party embedder behind the **`Embedder`** adapter (or defer RAG to cached long-context). | 06, 07 | **Blocking for first migration.** |
| B2 | **Publish-gate hard-block vs warn+override** on a flagged script (liability vs friction). | 07 | Needs legal input. |
| B3 | **Acceptable Opus spend per creator per generation session**, so Haiku/Opus routing thresholds + per-plan token quotas can be set. | 07 | Needs unit-economics model. |
| B4 | **Sonnet 4.6 middle tier** for Script Studio to cut cost, or stay strictly two-tier Opus/Haiku per the locked spec? | 07 | Stay two-tier unless cost forces it. |
| B5 | **Does Marque ever train/fine-tune on creator content?** If so it needs a *separate* explicit 5.1.2(i) opt-in distinct from inference consent. | 06 | Default: inference-only, no training. |
| B6 | **Confidence-decay half-life** for unverified Brand Graph facts, plus per-layer overrides. | 06 | Pick a default + overrides. |
| B7 | **Full per-predicate stated-vs-observed precedence table** (only the two-class default is specified); lives in `conflict_policy` data. | 06 | Needs product sign-off. |
| B8 | **Per-creator trend-fit persistence** as cultural facts vs recompute each Trend Radar refresh (storage/bloat vs latency). | 06 | Align with Trend Radar cadence. |
| B9 | **Pillar count cap** for the Studio constellation (proposed 6). | 04 | Confirm 6. |
| B10 | **Strength-score surfacing** — numeric 1–100, 5-dot, or 3-tier word? | 04 | Pick one for consistency. |
| B11 | **Canonical steer-chip set** + whether custom pinned steers are allowed. | 04 | Lock the set. |
| B12 | **Default content-mix policy** for a newly analyzed brand (even vs winner-weighted). | 04 | Even at cold start. |
| B13 | **Priority-6 "rest" Today** — truly empty/celebratory vs always a gentle next action. | 04 | Lean gentle next action. |
| B14 | **AssemblyAI Auto-Chapters surfacing** — how much output to expose as editable shot-plan beats (and note: chapters/summarization are deprecated → use Claude for moment reasoning). | 04, 09 | Minimal, Claude-derived beats. |

## C. Monetization, retention & growth

| # | Question | Raised by | Recommendation / default |
|---|---|---|---|
| C1 | **Pricing + economics (bundle).** Exact prices, monthly credit grants (INK/REEL per tier), recipe cost weights, consumable pack pricing, trial length, grace duration, referral reward mechanics, watermark spec, and Free-taste limits. Genuine PM + finance decisions pending measured unit-cost data. | 11, 13 | **Needs PM + unit-cost data.** |
| C2 | **Referral reward economics** — confirm "1 month free Pro per converted referral" (referrer) + "14-day trial" (referee) + the fraud-hold delay (7 vs 30 days). | 13 | Affects RevenueCat offer config. |
| C3 | **Annual-only trial Offering** in the E3 trial-length test? | 03 | Optional. |
| C4 | **Apple Retention Messaging API** — adopt at v1 (needs <700ms endpoint + sandbox perf test) or fast-follow? | 13 | Fast-follow unless cheap. |
| C5 | **Hard-ish gate boundary** — are re-generated *new* scripts free or gated (recommend: original 3 free, new generation gated)? | 03 | Original 3 free, new gated. |
| C6 | **Social-proof claim** ("4x more") needs a defensible source or a softer fallback before launch — do not fabricate. | 03 | Soften if unsourced. |
| C7 | **Provisional vs explicit-first notification authorization** (E6 default). | 03, 13 | Explicit primer at clips-ready, provisional as complement. |
| C8 | **Streak cadence default** (weekly vs 3×/week) and grace-day allotment (1/week) — both remote-config-tunable but need a launch value. | 13 | Pick a launch default. |
| C9 | **Minimum public-post count N** below which onboarding routes to the voice-interview fallback (suggested 6–10), and the **voice-interview length** (3 vs 4 questions). | 03 | N≈6–10; 3 questions. |
| C10 | **Resume window** before an abandoned anonymous session's Brand Graph is purged (suggested 30 days; draft-upload window suggested 14 days). | 03, 12 | 30 days / 14 days. |
| C11 | **Email channel** — `notification_prefs` includes `email` but no email provider is in the locked stack. Is lifecycle/digest email in scope, and via which adapter? | 13 | Out of scope unless a provider is added. |
| C12 | **Notification Service Extension thumbnail source** — Cloudflare Stream signed URL vs R2 direct, and the signing path. | 13 | Stream signed URL. |

## D. Design, platform support & accessibility

| # | Question | Raised by | Recommendation / default |
|---|---|---|---|
| D1 | **Commercial faces vs OFL.** Default is free Playfair Display + Inter (SIL OFL 1.1). Budget for paid Tiempos/Söhne (Klim) instead? One-file `TypographyTokens` swap + a license purchase. | 02 | OFL default; paid is a spend decision. |
| D2 | **Gold-text ruling.** `#C9A227` text fails AA at ~2.1–2.9:1 on cream, so the doc locks gold to glyph/large-display/hairline/fill-with-ink-label only. Confirm design accepts this hard rule (it shapes the entire accent strategy) and whether a darker gold token is needed. | 02, 16 | Accept the rule; consider a darker accessible-gold token for any text use. |
| D3 | **iPad support at v1?** Default is iPhone-first (iPad runs as a scaled iPhone app). True iPad layout changes Record + Calendar specs and triggers the 13" screenshot requirement. | 02, 16 | iPhone-first default. |
| D4 | **User-facing appearance override.** Currently follows the system color scheme with no in-app light/dark toggle. Add a Settings override (stored preference fed into `ThemeProvider`)? | 02 | System-follow default. |
| D5 | **Localization scope for v1** — English-only-but-i18n-ready vs specific launch locales (gates Store metadata + legal translations). | 16 | English-only, i18n-ready. |
| D6 | **Jailbreak posture** — detect-and-degrade, detect-and-warn, or no detection? | 16 | No detection at v1. |

## E. Backend, security, infra & legal

| # | Question | Raised by | Recommendation / default |
|---|---|---|---|
| E1 | **Trigger.dev hosting** — cloud vs self-host (CRIU makes self-host hard). | 12 | Cloud recommended. Owner: Backend lead. |
| E2 | **FastAPI host** — Fly.io vs Render vs Railway. | 15 | Unresolved. |
| E3 | **Anthropic plan tier** — Enterprise needed for Usage/Cost analytics + Spend Limits, vs deferring to ledger-only metering. | 15 | Decide vs cost-of-Enterprise. |
| E4 | **Daily org-spend alert threshold** ($X/day) — needs a unit-economics model. | 15 | Set after C1/B3. |
| E5 | **Biometric-data legal exposure** (BIPA / state laws) for storing/processing creator face + voice; needs counsel + consent record + final retention decision. | 12 | **Needs counsel.** |
| E6 | **GoTrue version** — confirm it handles Apple base64url nonce (issue #2378); gate release on a real round-trip integration test. | 12 | Gate release on the test. |
| E7 | **Source-video residency split** — R2 (source) + Stream (delivery) + Supabase Storage (thumbnails); affects `storage_backend` column + signed-URL/RLS story. | 12 | As specified; confirm. |
| E8 | **Vision moderation vendor** for uploaded video + AI-generated visuals (text is covered by Haiku 4.5; image/video is not). | 14 | **Needs a pick.** |
| E9 | **User-to-user surfaces** (shared teardown cards, referral feed, comments)? Determines whether a block mechanism is mandatory under 1.2. | 14 | Default: single-tenant v1, no U2U. |
| E10 | **Final age-rating tier** under Apple's 2025 tiered system (recommended 17+ given UGC + outbound posting) and the 1.2.1(a) declared-vs-verified posture + threshold (depends on launch market). | 14, 16, 17 | Recommend 17+, declared age. |
| E11 | **PrivacyInfo.xcprivacy manifest status** of supabase-swift + PostHog iOS SDKs at build time (RevenueCat + Sentry ship manifests). | 14 | Verify at build. |
| E12 | **DPA / no-training posture** for Anthropic, AssemblyAI, Shotstack, MCP clip engine, Ayrshare, Phyllo — for the Privacy Policy processor list + deletion cascade. | 14 | Confirm each DPA. |
| E13 | **RevenueCat purchase/tax record retention window** — the legally-required value to publish in the Privacy Policy. | 16 | Counsel to confirm. |
| E14 | **Realtime scaling cutover** — at what concurrent-job volume do we migrate from Postgres Changes to Broadcast-from-triggers? | 01 | Define a threshold. |
| E15 | **State-restoration depth** — persist all per-tab NavigationPaths across cold launch, or always reset transient capture routes? Doc assumes persist-except-live-capture. | 01 | Persist-except-live-capture. |
| E16 | **Sentry Session Replay scope** vs creator-content privacy; **PostHog EU vs US** data residency / GDPR posture. | 15 | Privacy-first defaults. |
| E17 | **Per-feature AI eval pass-rate thresholds** (90% default may not fit hooks/teardowns). | 15 | Tune per feature. |
| E18 | **Load-test SLOs** — sustained render concurrency + queue-depth latency for the Monday surge; **concrete low-end device tier** for the perf-budget gate; **batch SLA wall-clock target** (drives concurrency + sync-vs-deferred AI-visual rendering). | 09, 15 | Set SLOs before load test. |

## F. Roadmap & provisioning

| # | Question | Raised by | Recommendation / default |
|---|---|---|---|
| F1 | **Day-1 account provisioning** — who owns and when: Apple Developer Program enrollment, Meta app + dev IG Business account, TikTok developer app. Gates the audit clock + M4 convergence. | 17 | **Needs an owner + date now.** |
| F2 | **ClipEngine in-house timing** — strictly post-v1 (assumed), or must any part land before submission? Affects M3/M5 scope. | 17 | Post-v1 default. |
| F3 | **Exact NSM wording** — "consistent creators (≥3 published/7d)" vs "weekly published clips per active creator." Lock before analytics instrumentation. | 00 | Lock the wording. |
| F4 | **Moderation depth for Guideline 1.2** — automated Haiku 4.5 content-safety classifier at submit time vs report-only at launch (Apple wants demonstrable 24h action). | 00, 14 | Lean classifier-at-submit. |
| F5 | **Tab labels final?** Confirm "Studio" vs "Record"/"Create" for the hero tab; confirm Insights is a Coach sub-screen (not a 6th tab). | 01 | Assumed Studio + Insights-under-Coach. |
| F6 | **Universal Link domain/IDs** — is `marque.app` the registered apex, will the marketing site host the AASA, and what is the Team ID + final bundle id (`TEAMID.com.marque.app`)? | 01 | Needed for AASA. |
| F7 | **Repurpose-in entry point** — confirm it's a source toggle ON Record (assumed) vs a Library import action. Changes the `BatchState` entry point. | 01 | Source toggle on Record. |
| F8 | **Quartile→3s-retention mapping** — quartiles give 25/50/75/100% watch points, not a literal 3.0s point. Confirm the chosen reward proxy. | 08 | Confirm proxy. |
| F9 | **Contextual vs plain bandit** — launching with plain batched Beta-Bernoulli Thompson sampling; confirm the v2 trigger for contextual (LinUCB/neural-TS). | 08 | Plain at v1. |
| F10 | **In-house virality predictor timing** — v1 = MCP virality_predictor + Opus rubric; when to invest in an in-house model trained on accrued `clip_outcomes`. | 08 | Roadmap call. |

## G. Spec housekeeping (filename/numbering reconciliation) — ✅ RESOLVED

Because the 18 sections were authored in parallel, several used *logical* sibling filenames that differed from the final on-disk numbering. **This has been reconciled:** a deterministic link-resolution pass mapped and rewrote **271 dangling cross-references** across all docs to the canonical 18 filenames. A verification sweep confirms **zero dangling `NN-name.md` references remain**. The canonical filenames are the 18 files listed in [`README.md`](README.md)'s table of contents (`docs/00-…` through `docs/17-…`); they are authoritative. No further action required (anchor-level `#section` links are best-effort and not yet individually verified).

---

## Credentials / keys to paste

The backend (FastAPI) holds **all** secrets; the iOS client holds only the Supabase **anon/publishable** key. Paste these into the backend secret store (and the Supabase/RevenueCat dashboards) — **never** into the app binary. Do not commit any of these to the repo.

| Service | What to paste | Where it lives | Notes |
|---|---|---|---|
| **Supabase** | Project URL, **anon/publishable key** (client), **service_role key** (backend only) | Client: app config. service_role: backend secret store only | service_role bypasses RLS — backend-only, never in the binary. |
| **RevenueCat** | Public SDK API key (client), secret API key + webhook signing secret (backend) | Client: app. Secrets: backend | Entitlement source of truth; webhook mirrors into Supabase. |
| **Apple / App Store** | App Store Connect API key (.p8 + key id + issuer id) for ASSN V2 + sandbox testing; **APNs auth key (.p8 / ES256)** + key id + Team ID + bundle id | Backend secret store | One APNs `.p8` signs JWTs for all environments. |
| **Social publishing provider** ⭐ | **Ayrshare** API key + Profile Keys (v1). *If pursuing direct:* Meta app id/secret + IG long-lived tokens, TikTok client key/secret + content-posting scopes | Backend | **This is the one credential set gated on the ⭐ social-provider decision.** Ayrshare-only is far less to manage. |
| **Phyllo** (if chosen for Insights) | Client id + secret + environment | Backend | Only if Phyllo wins A6 over Ayrshare analytics. |
| **AssemblyAI** | API key | Backend | Transcription + word timings; webhooks point at the backend. |
| **Shotstack** | API key (sandbox + production) | Backend | Sandbox renders are watermarked; flip to production key for real output. |
| **Cloudflare R2 + Stream** | R2 access key id + secret + bucket + **verified public domain**; Stream API token + account id | Backend | The **public domain must be verified** for IG `video_url` cURL + TikTok `PULL_FROM_URL` (see A5). |
| **Anthropic** | API key (+ workspace per environment) | Backend | Opus 4.8 + Haiku 4.5 via `LLMRouter`. Enterprise tier gates Usage/Cost analytics (E3). |
| **Embedder** (TBD) | API key for the chosen embedding provider | Backend | Blocked on B1 — pick the model/dimension before the first migration. |
| **Sentry** | DSN (iOS) + auth token (dSYM upload in CI) | Client DSN is public-ish; CI token in CI secrets | Ships a privacy manifest. |
| **PostHog** | Project API key (client) + personal API key (CI/server) + host (US/EU per E16) | Client + backend | Decide data residency (E16). |
| **Trigger.dev** | Project ref + API key (per environment) | Backend | Cloud vs self-host is E1. |

> **The single decision that needs founder input on the credential side** is the **social publishing provider** (⭐). Picking **Ayrshare-only** collapses the entire "Social publishing provider" row to one API key + Profile Keys and lets Marque inherit the audited-client status — the fastest path to a shippable v1. Pursuing **direct** integration adds the Meta + TikTok credential sets *and* the 2–6 week audit clock + perpetual token-refresh ownership. Every other credential above is a standard paste-and-go.
