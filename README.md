# Marque — Build Specification

> *Turn overwhelmed creators into consistent ones.*

**Marque** is a calm, premium iOS app that collapses the content-production tax to a single weekly batch session. Its loop: an AI analyzes the creator's brand and existing page, writes viral scripts in *their* voice, the creator records **one** batch talking-head session, the AI edits it into many platform-ready clips in chosen viral **formats**, schedules and publishes them across Instagram and TikTok, and learns from performance to tighten the next loop. Two things make it defensible: a persistent **Brand Graph** context layer that compounds over time, and a **Virality Engine** feeding a **Format Library** where formats are structured *render-recipes* — not blank talking heads. The aesthetic is modeled on the Stoic journaling app: warm cream surfaces, near-black dark mode, high-contrast serif display type, a single restrained gold accent, huge whitespace, slow breathing motion, and a strict anti-clutter doctrine — the Today home screen shows exactly **one directive, one gold streak glyph, and one trend line**, never more.

---

## How to read this spec

This is an **implementation-grade** specification, not a pitch. Each section under `docs/` is self-contained and authored to the standard of a senior staff engineer plus a senior product designer: real schemas, component contracts, explicit States (loading / empty / error / offline / permission-denied), acceptance criteria, and inline-cited best practices.

- **Canonical names are load-bearing.** `Brand Graph`, `Virality Engine`, `Format Library`, `ClipEngine`, `Publisher`, `Insights`, `Billing`, `LLMRouter` are exact names used across every doc. Don't rename them.
- **Adapters hide every vendor.** Swapping AssemblyAI, Shotstack, Ayrshare, Anthropic, etc. is a one-file change behind its adapter. No call site names a vendor string.
- **Two companion docs sit above the sections.** [`DECISIONS.md`](DECISIONS.md) is the single source of truth for *why this, not that* — the locked stack, aesthetic, and product decisions. [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md) aggregates every unresolved decision plus the credentials checklist.
- **Cross-references use filenames.** When a section says "see `06-brand-graph.md`," that's a relative link into `docs/`.
- **Every section ends with `## Open questions` and `## Sources`.** Open questions are mirrored, grouped, into [`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md).

---

## Table of contents

| # | Section | One-line summary |
|---|---|---|
| 00 | [Product Overview, Principles & Scope](docs/00-overview.md) | Vision, persona + jobs-to-be-done, positioning vs Opus Clip/Vizard/HeyGen/Captions, the core-loop diagram, 5 product principles, NSM + activation funnel, and the v1 / later / non-goals scope gates. |
| 01 | [Information Architecture & Navigation](docs/01-information-architecture.md) | The locked 5-tab bar (Today/Studio/Library/Calendar/Coach), the `AppRouter` + per-tab `@Observable` Router spine, full sitemap, the `BatchState` core-loop state machine, deep/universal links, and anti-clutter placement of all Section-8 features. |
| 02 | [Design System (Stoic-grounded)](docs/02-design-system.md) | Token architecture via the iOS 17 `@Entry` Environment `Theme`; full cream/night/gold color system, Playfair + Inter type scale, spacing/radii/elevation, calm motion + haptics, accessibility (the gold-is-glyph-only contrast rule), and the component inventory with States. |
| 03 | [Onboarding & Activation](docs/03-onboarding.md) | The value-first AHA ("three viral scripts in my own voice, before the paywall"), the 9-step flow, progressive Brand Graph profiling, iOS permission priming, skip/resume via anonymous→permanent Supabase linking, and activation/conversion metrics with A/B hooks. |
| 04 | [Core Screens I — Today, Studio, Script Reader & Hook Lab](docs/04-screens-create.md) | The four upstream "create" screens: the single-directive Today resolver, Studio's pillar constellation + batch generation, the Script Reader (format swap, variants, steer controls), and the Hook Lab nested via progressive disclosure. |
| 05 | [Core Screens II — Record, Library/Clip Editor, Calendar, Coach](docs/05-screens-produce.md) | The production half: AVFoundation Record + teleprompter (the hero batch loop + repurpose-in), the Clip Editor mapped to Shotstack templates, the draggable Calendar with platform-compliance gates, and Coach/Insights/Trends/Brand Profile. |
| 06 | [The Brand Graph (Context Layer)](docs/06-brand-graph.md) | The flagship moat: a bitemporal append-only fact store in Postgres, the structured Voice Fingerprint, the Context Pack assembled for Claude, prompt-cache rules, stated-vs-observed conflict resolution, and the editable "What Marque knows about you" view. |
| 07 | [AI System Architecture](docs/07-ai-system.md) | The six-service map (Brand Analyzer, Voice Engine, Script Studio, Virality Engine, Clip Engine, Coach), the `LLMRouter` adapter + Opus 4.8 / Haiku 4.5 routing, prompt/cache architecture, structured outputs, the guardrail pipeline, eval gates, and the two-regime latency design. |
| 08 | [Format Library, Hooks & Virality Engine](docs/08-format-virality.md) | The 3-second-cliff north-star, the Format Library as render-recipes mapped 1:1 to Shotstack templates, the 8-signal hook taxonomy, the two-surface Virality Engine, the per-creator Thompson-sampling learning loop, and first-party Trend Radar. |
| 09 | [Video Capture, Processing & Rendering Pipeline](docs/09-video-pipeline.md) | The end-to-end chain: AVFoundation capture → resumable tus upload → R2/Stream → AssemblyAI transcription → MCP ClipEngine + Shotstack render-recipes → Trigger.dev durable orchestration, with cost/latency budgets and the MCP→in-house migration path. |
| 10 | [Social Publishing, Scheduling & Analytics Pullback](docs/10-social-publishing.md) | The `Publisher`/`Insights` adapter protocols, the v1 Ayrshare decision, the real IG Graph + TikTok Content Posting limits and mandatory UX, OAuth/token-refresh for the future DirectPublisher, the scheduling engine, and analytics pullback. |
| 11 | [Monetization, Paywall & Entitlements](docs/11-monetization.md) | StoreKit 2 + RevenueCat architecture, the Free/Pro/Studio tier matrix with the hard wall at publishing, credit metering, paywall placement + States, server-side validation via ASSN V2, App Store compliance, dunning/grace, and the referral loop. |
| 12 | [Backend, Data Model & Security](docs/12-backend-data-security.md) | The three-trust-plane architecture, RLS as the authorization spine, the full Postgres schema with DDL + RLS, Sign in with Apple, private storage + signed URLs, the FastAPI + Trigger.dev orchestration spine, secrets, retention/deletion, rate limiting, and a STRIDE threat model. |
| 13 | [Notifications, Retention & Growth Loops](docs/13-notifications-retention.md) | Token-based APNs, the one-shot opt-in at the value moment, the notification taxonomy + send-governor caps, the per-category preference center, the anti-vanity batch-session streak system, RevenueCat-driven lifecycle, and the referral loop. |
| 14 | [App Store Compliance, Privacy & Legal](docs/14-appstore-compliance-legal.md) | Compliance as a feature set across the five high-rejection clusters: 1.2 UGC (moderation/report/block/contact), 5.1.2(i) third-party-AI disclosure, 3.1.2 paywall metadata, 4.2/4.3 not-a-wrapper, 5.1.1(v) in-app deletion, plus the privacy manifest, age rating, and legal docs. |
| 15 | [Infra, Observability, CI/CD & Testing](docs/15-infra-observability-testing.md) | The three-env model, Xcode Cloud workflows, gated Supabase migrations, PostHog flags + remote config, Sentry + distributed tracing, the PostHog event taxonomy, cost monitoring, performance budgets, the full test strategy, and the release/rollback playbook. |
| 16 | [Completeness Checklist — Everything a Shipped App Has](docs/16-completeness-checklist.md) | The release-gate checklist: the six-state coverage matrix, App Store asset specs, account deletion cascade, WCAG 2.2 AA accessibility, network resilience, mobile security, extensions, and the one-page final release gate. |
| 17 | [Build Roadmap, Milestones & Risks](docs/17-roadmap-milestones.md) | The M0→M6 build plan with the social-audit swimlane as the program critical path, per-milestone DoD, the recommended first thing to build, the top-risk table, and the binding v1 submission Definition of Done. |

---

## Recommended reading order

1. **Start with the "why."** [`DECISIONS.md`](DECISIONS.md) — the locked stack, aesthetic, and product decisions in one page.
2. **Understand the product.** `00-overview.md` → `01-information-architecture.md` → `02-design-system.md`. Vision, navigation spine, then the visual language everything is built in.
3. **Walk the loop in product order.** `03-onboarding.md` → `04-screens-create.md` → `05-screens-produce.md`. This is the creator's actual path: onboard → script → record/clip → schedule.
4. **Go deep on the moat + engine.** `06-brand-graph.md` → `07-ai-system.md` → `08-format-virality.md`. The context layer, the AI architecture, and the virality/format machinery.
5. **The hard infrastructure.** `09-video-pipeline.md` → `10-social-publishing.md` → `12-backend-data-security.md`. The pipeline, the publishing surface, and the backend/security spine.
6. **Business + ship-readiness.** `11-monetization.md` → `13-notifications-retention.md` → `14-appstore-compliance-legal.md` → `15-infra-observability-testing.md` → `16-completeness-checklist.md`.
7. **Sequence it.** `17-roadmap-milestones.md` last — it tells you what to build first and in what order.

> **If you only read three:** `00-overview.md` (what & why), `17-roadmap-milestones.md` (build order), and `06-brand-graph.md` (the thing competitors don't have).

---

## Build order at a glance

The roadmap (`17-roadmap-milestones.md`) phases the build **M0 → M6**. The load-bearing sequencing insight: the **Meta IG App Review** and the **2–6 week TikTok Content Posting audit** are the longest-lead items and cannot be compressed by engineering — so "submit the social-API audits" is a **Day-1 swimlane**, not an M4 task.

| M | Name | Goal (one line) | Exit gate |
|---|---|---|---|
| **M0** | Foundations: spine + shell + auth | The durable Trigger.dev orchestration spine + adapter pattern + design system + auth all exist; a stub clip flows end-to-end | Stub video → transcribe → render → R2 URL → TikTok `SELF_ONLY`; **both audits submitted** |
| **M1** | Onboarding + Brand Graph + ingestion | A creator connects their page, we ingest it, build the Brand Graph, and capture AI-data-sharing consent | A real creator's Brand Graph exists; consent + report/block/contact scaffold in place |
| **M2** | Script Studio + Format Library + Hook Lab | Opus writes ≥3 voice-matched scripts; creator picks render-recipe formats; Hook Lab nested in the reader | Haiku voice-check gate passes; ≥3 scripts in the creator's voice; formats selectable |
| **M3** | Record + clip pipeline (HERO loop) | One batch talking-head session (or uploaded long video) fans out into N rendered clips in ≥3 formats | ≥5 clips render to public R2 URLs from one session in ≥3 recipes |
| **M4** | Publishing + scheduling | Clips schedule + publish to IG (real) and TikTok (audited→PUBLIC), rate-limit-safe | Real post to both platforms within rate limits; scheduler honors caps |
| **M5** | Retention + virality + learning loop | Metrics pull back, Coach teardown cards + push, streaks, moderation/report live | Loop closes: a post's performance refines the next directive; moderation acts <24h |
| **M6** | Compliance + polish + submission | Every App Store gate satisfied; all States hardened; paywall live | v1 submission DoD fully green |

**The recommended first thing to build:** the durable Trigger.dev job + adapter skeleton end-to-end on a stub (Supabase auth → AssemblyAI transcribe → Shotstack render → R2 public URL → `Publisher` posts TikTok `SELF_ONLY`). It de-risks the entire critical path, proves the adapter pattern, and produces the live demo + screencast needed to start the social audits on Day 1. See `17-roadmap-milestones.md §9`.

---

## What I need from you

A handful of decisions and credentials are blocking or shaping the build. They're all aggregated — grouped by theme, with a credentials-to-paste checklist — in **[`OPEN-QUESTIONS.md`](OPEN-QUESTIONS.md)**. The single decision that most changes the plan is the **social publishing provider** (Ayrshare-only vs. also pursuing Marque's own TikTok + Meta audits), because it sets the longest-lead clock. Start there.
