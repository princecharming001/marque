# 00 — Product Overview, Principles & Scope

> **Marque** — a calm, premium iOS app that turns overwhelmed creators into consistent ones.
>
> This is the root document of the Marque build specification. It establishes the vision, the persona, the competitive wedge, the core loop, the product principles, the metrics that define success, and — most importantly for engineers — the **hard external constraints that gate v1 scope**. Every other section in `docs/` inherits the canonical names, the locked stack, and the anti-clutter doctrine defined here.

**Status:** Canonical · **Owners:** Product + Engineering leads · **Last updated:** 2026-06-29

### Reading order / sibling map

| File | Section | Why you'll cross here from this doc |
|---|---|---|
| `01-information-architecture.md` | IA, the Today home + nav shell | The anti-clutter doctrine made concrete (Principle 1) |
| `02-design-system.md` | Aesthetic, type, color, motion | The locked Stoic-modeled aesthetic referenced throughout |
| `03-onboarding.md` | Signup → Brand Graph seed → first script | The activation funnel north of "first publish" |
| `04-screens-create.md` | Today, Script reader, Hook Lab | The CREATE surface; progressive-disclosure Hook Lab |
| `05-screens-produce.md` | Batch record + teleprompter, Library, Calendar, Coach | The HERO loop's highest-friction step + the produce/learn screens |
| `06-brand-graph.md` | The persistent CONTEXT LAYER | The Brand Graph moat, in depth |
| `07-ai-system.md` | Claude, Virality Engine, prompt caching | The VIRALITY ENGINE + script generation |
| `08-format-virality.md` | Render-recipe catalog + virality prediction | The Format Library differentiator |
| `09-video-pipeline.md` | Capture → ClipEngine → Shotstack render | The durable clip pipeline (AVFoundation, AssemblyAI, R2/Stream) |
| `10-social-publishing.md` | Ayrshare Publisher, Insights, IG/TikTok limits | The scope-gating platform constraints + analytics pullback |
| `11-monetization.md` | StoreKit 2 + RevenueCat paywall, referral | Apple-IAP mandate; remote paywalls |
| `12-backend-data-security.md` | FastAPI orchestrator, adapters, Postgres schema, RLS | The ClipEngine / Publisher / Insights adapter boundaries (Principle 2) |
| `13-notifications-retention.md` | APNs, streaks, teardown pushes | Retention triggers + the consistency loop |
| `14-appstore-compliance-legal.md` | AI disclosure, UGC moderation, App Review | The launch-blocking compliance layer |
| `15-infra-observability-testing.md` | PostHog/Sentry, remote config, CI/test | The NSM/activation/funnel instrumentation + ops |

---

## 1. Vision

Creators with real expertise — coaches, founders, experts, operators with a personal brand — are losing to people with worse ideas and better consistency. The bottleneck is not talent or even effort; it is the **production tax**: the grinding sequence of ideate → script → film → edit → reformat → schedule → analyze that has to be paid *every single time* you want to show up. Most people pay it for three weeks, burn out, and go quiet.

**Marque's vision is to collapse that tax to a single recurring act — one calm batch recording session per week — and let an AI system carry everything else.** The app analyzes who you are, writes in your voice, lets you film once, edits into many platform-native formats, publishes on a schedule, and learns what works so next week is sharper. The creator's only job is to keep being themselves on camera for twenty minutes a week.

The experience is deliberately the opposite of the category it competes in. Where incumbents are dense dashboards optimized to make you feel productive, Marque is a quiet, declarative space modeled on the Stoic journaling app — warm cream surfaces, high-contrast serif display type, one idea per screen, slow eased motion. **Calm is the product strategy, not the decoration.** The persona we serve is overwhelmed; a control panel adds to the overwhelm. Marque's job is to *remove decisions*, and the interface is the proof.

> **The one sentence everything ladders to:** *Turn overwhelmed creators into consistent ones.*

---

## 2. Target creator & jobs-to-be-done

### 2.1 Persona — "the overwhelmed creator-operator"

| Attribute | Detail |
|---|---|
| **Who** | A coach, founder, expert, consultant, or personal brand. They have genuine expertise and a point of view. They are **not** a full-time video editor and have no desire to become one. |
| **Situation** | Knows they *should* post consistently. Has tried. Has a graveyard of half-edited clips and a posting cadence that collapsed weeks ago. |
| **Asset** | A voice, a face, a body of knowledge, and (often) an existing page/profile that already hints at their brand. |
| **Constraint** | Time and attention. The work happens in the cracks of a busy operator's week. |
| **What they are NOT** | Not a team running a content studio (that's `Vizard`'s buyer). Not someone who wants a faceless avatar to speak for them (that's `HeyGen`/`Revid`). Not someone with a pile of long-form footage already sitting on a drive waiting to be sliced (that's `Opus Clip`'s starting assumption). |

### 2.2 Emotional state we design for

The persona arrives **overwhelmed, guilty about inconsistency, and suffering decision fatigue.** This is the single most important fact in the entire spec. It is the reason for the anti-clutter doctrine (see §6, Principle 1), the reason Today shows exactly one directive, and the reason copy is quiet and declarative rather than gamified and loud.

Design implication, stated bluntly: **a feature that adds a decision is a feature that subtracts from the promise.** Every screen must be defensible against the question "does this reduce the creator's cognitive load, or add to it?"

### 2.3 Jobs-to-be-done

**Core JTBD:** *"Help me show up consistently in my own voice without it eating my week."*

| # | Sub-job (creator's words) | Marque's answer | Lives in |
|---|---|---|---|
| 1 | "Don't make me face a blank page." | Virality Engine writes scripts in the creator's voice | `07-ai-system.md` |
| 2 | "Let me film once, not every day." | Batch record + teleprompter — the HERO loop | `05-screens-produce.md` |
| 3 | "Don't make me learn editing." | ClipEngine + Format Library render-recipes | `08-format-virality.md`, `09-video-pipeline.md` |
| 4 | "Don't make me guess what'll work." | Virality Engine (prescriptive), Hook Lab, teardowns | `07-ai-system.md`, `08-format-virality.md` |
| 5 | "Don't make me babysit a posting calendar." | Publisher schedules to IG + TikTok | `10-social-publishing.md` |
| 6 | "Use what I already have." | Repurpose-in: upload an existing long video as a second Record source | `05-screens-produce.md` |

---

## 3. Positioning vs. competitors

The category — "AI video repurposing" — has split into two archetypes, and **Marque belongs to neither.** Most tools are **clippers** (they need you to already have long footage and they slice it) or **generators** (they make faceless/avatar video that isn't really *you*). ([Vizard — 9 best AI clipping tools 2026](https://vizard.ai/blog/9-best-ai-video-clipping-tools-2026))

### 3.1 The landscape

| Competitor | Archetype | What it's great at | The gap Marque exploits |
|---|---|---|---|
| **Opus Clip** | Clipper (speed) | Fast pile of ready-made shorts from one long video; dynamic captions; a virality *score*. ([Vizard comparison](https://vizard.ai/blog/9-best-ai-video-clipping-tools-2026)) | Stateless (every job starts cold); no voice/brand persistence; doesn't *create* (only slices); scheduling isn't a learning loop. |
| **Vizard** | Clipper (control, teams) | Transcript-based editing, brand kits, multi-language subtitles; ~$16–39/mo. ([Vizard](https://vizard.ai/the-12-best-video-editing-tools-in-2025-turn-long-demos-into-viral-ugc-clips-vizard-vs-opus-clip-more/)) | Still fundamentally a clipper of *existing* long video; built for teams, not a calm solo operator. |
| **HeyGen** | Generator (avatar/translation) | Avatar-led explainers, translation/dubbing — "when the clip itself isn't enough." ([Vizard comparison](https://vizard.ai/blog/9-best-ai-video-clipping-tools-2026)) | Not a clipper at all; it's *not the real you*. Different job entirely. |
| **Captions** | Editor (mobile) | Mobile-first AI editing, captions, AI script assists. | An editing surface; no persistent brand context; no film-once batch loop. |
| **Revid** | Generator (faceless) | Faceless AI-video generation. | No real creator; no voice-sacred principle. |
| **CapCut** | Editor (mobile) | General-purpose mobile editing. | A toolbox, not a consistency system; the production tax remains the user's problem. |

### 3.2 The wedge

**No competitor owns the loop `batch talking-head → many formats → publish → learn`, and none have a persistent context layer.** That is white space, and Marque's three differentiators map onto it directly:

1. **Brand Graph — the persistent CONTEXT LAYER (the moat).** Every competitor is stateless; each job starts from zero. Marque carries a structured, compounding model of *who the creator is* across every script, render, and post. This is the differentiator that strengthens with use and is hardest to copy. See `06-brand-graph.md`.
2. **Virality Engine — prescriptive, not a scoreboard.** Opus gives you a number *after* the fact. Marque's engine is *prescriptive*: it chooses hooks and formats *before* you record. See `07-ai-system.md` and `08-format-virality.md`.
3. **Format Library as RENDER-RECIPES.** Competitors output a captioned talking head. Marque ships **structured formats** — split-screen, 3-up talking heads, green-screen, faceless AI-visual, before/after, myth-buster, listicle, POV, reaction, B-roll+caption-hook. These are concrete, demonstrable render-recipes, not blank frames. See `08-format-virality.md`.

### 3.3 Positioning rules (binding)

- **DO** position as the *"consistency engine for creators."*
- **DON'T** position as a *"clip generator."*
- **DON'T** benchmark on "clips per minute" — that is Opus Clip's game and a race to the bottom. Marque competes on **consistency, voice fidelity, and format breadth**, not throughput.

---

## 4. Core promise & core loop

### 4.1 Core promise

> **Turn overwhelmed creators into consistent ones.**

### 4.2 The core loop — batch is the spine, not a feature

Section-8 feature **#1 — "film once → post all week" — is the HERO loop.** It is the spine of the product, not an add-on. The entire information architecture exists to funnel the creator into the batch session and then carry the output the rest of the way.

```
            ┌──────────────────────────────────────────────────────────────┐
            │                      THE MARQUE CORE LOOP                      │
            └──────────────────────────────────────────────────────────────┘

  ① Brand Graph        ② Virality Engine      ③ BATCH RECORD          ④ ClipEngine
  analyzes the     →   writes scripts     →   ONE talking-head    →   edits into many
  creator's page       in THEIR voice         session (teleprompter)   clips × chosen FORMATS
  (CONTEXT LAYER)      (prescriptive)          [HERO step]              (render-recipes)
                                                                              │
            ┌─────────────────────────────────────────────────────────────────┘
            ▼
  ⑤ Publisher          ⑥ Insights
  schedules + posts →  learns from performance →  tightens ①–④ next cycle
  to IG + TikTok       (teardowns, voice/hook/format signals)
            └────────────────────────── context compounds ──────────────────────┘
```

| Stage | What happens | Stack | Spec |
|---|---|---|---|
| ① Brand Graph | Analyze the creator's existing page; seed a structured brand model | Claude (Opus 4.8 reason + Haiku 4.5 bulk), Supabase | `06-brand-graph.md`, `07-ai-system.md` |
| ② Virality Engine | Generate scripts in the creator's voice; pick hooks + formats | Claude Opus 4.8, prompt caching over Brand Graph prefix | `07-ai-system.md`, `08-format-virality.md` |
| ③ **Batch Record (HERO)** | One sit-down; teleprompter scrolls scripts; capture all takes | SwiftUI + AVFoundation | `05-screens-produce.md` |
| ④ ClipEngine render | Cut + reframe + caption + apply render-recipes → many clips | MCP creative toolchain → Shotstack render, AssemblyAI moments, R2/Stream | `08-format-virality.md`, `09-video-pipeline.md` |
| ⑤ Publish | Schedule + post to Instagram & TikTok | Ayrshare (Publisher adapter) | `10-social-publishing.md` |
| ⑥ Learn | Pull performance, generate teardowns, feed Brand Graph | Phyllo/Ayrshare (Insights adapter) | `10-social-publishing.md`, `13-notifications-retention.md` |

The loop is **durable end-to-end** — every long-running step (transcription, render, publish) is a durable job that survives app-close and network loss (see §6, Principle 3).

---

## 5. Section-8 features — placement map

All seven are **included in the product**, placed tastefully, **never cluttering Today.**

| # | Feature | Placement (anti-clutter rule) | Spec |
|---|---|---|---|
| 1 | Batch "film once → post all week" | The HERO loop — the spine, not an add-on | `05-screens-produce.md` |
| 2 | Trend Radar | **One line** on Today → dedicated Trends screen one layer deep | `04-screens-create.md`, `05-screens-produce.md` |
| 3 | Hook Lab | **Nested inside the script reader** (progressive disclosure) | `04-screens-create.md`, `07-ai-system.md` |
| 4 | Performance teardown cards | Coach feed + a push; archived in Insights | `05-screens-produce.md`, `13-notifications-retention.md` |
| 5 | Streaks / consistency | **One gold streak glyph** on Today; full view in Profile | `01-information-architecture.md`, `13-notifications-retention.md` |
| 6 | Repurpose-in (upload existing long video) | A **second source** on Record; same pipeline | `05-screens-produce.md`, `09-video-pipeline.md` |
| 7 | Referral loop | A **row in Settings** + **one** earned prompt after a genuine win | `11-monetization.md` |

---

## 6. Product principles

These are **product laws**, not guidelines. A design or implementation decision that violates one of these requires explicit sign-off from product + engineering leads.

### Principle 1 — One idea per screen (the anti-clutter doctrine)

The **Today** home screen shows **exactly one directive at a time + one small gold streak glyph + one trend line.** Nothing else. Every other feature is **one layer deep**, surfaced contextually, or lives in its own calm screen. **Never bolt features onto Today.**

This is a direct expression of designing for an overwhelmed persona (§2.2): the product's job is to *remove* decisions. The aesthetic — warm cream `#F4F1EA` / near-black `#0E0E10`, serif display, single warm gold `#C9A227` accent, huge whitespace, slow eased "breathing" motion, subtle paper texture — is the embodiment of "calm in a noisy category." Full system in `02-design-system.md`.

**Acceptance criteria**
- Today renders ≤ 1 primary directive, ≤ 1 streak glyph, ≤ 1 trend line at any time.
- No feature ships an entry point *on* Today that isn't one of those three elements.
- Any new surface is reachable in ≤ 1 navigation step from its contextual home, not from a Today widget.

### Principle 2 — Adapters hide every vendor (one-file vendor swaps)

The entire vendor stack is volatile: the MCP creative toolchain, Shotstack, AssemblyAI, Ayrshare, Phyllo, Cloudflare R2/Stream. Therefore **every vendor sits behind an adapter** — `ClipEngine`, `Publisher`, `Insights` — and **swapping a vendor is a one-file change.** No vendor SDK type, error, or concept leaks past its adapter boundary. This is a first-class principle precisely *because* the stack will change. Adapter contracts and DI wiring in `12-backend-data-security.md`.

**Acceptance criteria**
- No file outside `adapters/<name>/` imports a vendor SDK or references a vendor-specific identifier.
- Each adapter exposes a stable Marque-domain interface; callers depend only on that interface.
- A vendor swap is demonstrably achievable by editing a single adapter file + config.

### Principle 3 — Durable by default (the user never babysits a spinner)

Long video/AI jobs are async and **must survive app-close, network loss, and vendor latency.** The orchestration service (FastAPI) drives **Trigger.dev** durable runs: tasks **checkpoint and freeze during waits** (zero idle compute) and **resume on webhook** with automatic retries/backoff and concurrency caps to respect vendor rate limits. ([Trigger.dev — How it works](https://trigger.dev/docs/how-it-works)) This is the architectural expression of "calm" — the creator records, closes the app, and comes back to finished clips.

**Acceptance criteria**
- Transcription (AssemblyAI), render (Shotstack), ClipEngine (MCP), and publish (Ayrshare) each run as a durable Trigger.dev run with idempotent steps.
- Killing the app mid-job loses no work; the job completes and notifies via APNs.
- Concurrency caps are enforced per vendor to respect the rate limits in §7.

### Principle 4 — The creator's voice is sacred / human-in-the-loop

AI **drafts**; the creator **approves.** This is both a trust principle and an **App Store compliance necessity** — Apple's AI transparency rules require disclosure and consent when personal data (prompts, audio, the creator's footage) is shared with third-party AI services. ([Apple's Guideline 5.1.2(i) explainer](https://dev.to/arshtechpro/apples-guideline-512i-the-ai-data-sharing-rule-that-will-impact-every-ios-developer-1b0p)) No script auto-publishes; no clip posts without an approve step. Voice fidelity is measured (Haiku 4.5 voice checks) and surfaced. See `07-ai-system.md` and `14-appstore-compliance-legal.md`.

**Acceptance criteria**
- No generated script or clip reaches a published state without an explicit creator approval.
- AI data-sharing disclosure + consent is presented before any third-party AI call on creator data, with a visible user-facing control.

### Principle 5 — Context compounds (the retention moat)

Every post and its performance feed the **Brand Graph.** The product **gets better the longer you use it** — voice fidelity tightens, hook/format selection learns from the creator's own results. This is the retention engine and the reason the moat is durable. Economically, the Brand Graph is structured as a **stable cached prefix** for Claude prompt caching — system + Brand Graph context + format library + tool defs sit at the front so per-creator reasoning is cheap and fast. ([Anthropic — Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) See `07-ai-system.md`.

**Acceptance criteria**
- Each published clip's performance writes back into the Brand Graph / learning store.
- The Claude request layout places the stable Brand Graph prefix before varying content (caching hierarchy `tools → system → messages`), with `cache_control` at the end of the static prefix.

---

## 7. Hard external constraints that gate v1 scope

> **This is the most operationally important section of the document.** These are non-negotiable platform rules. They shape v1 scope, the non-goals, and the Publisher/scheduler design directly. Engineers building `10-social-publishing.md`, `11-monetization.md`, and `14-appstore-compliance-legal.md` must treat this as binding input.

### 7.1 TikTok Content Posting API — **[SCOPE-GATING]**

| Constraint | Detail | Implication |
|---|---|---|
| Unaudited = sandboxed | All posts forced to `SELF_ONLY` (private); **max 5 users** post per 24h; all accounts must be **private at post time**. ([Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines)) | **Public TikTok posting is NOT a day-one capability** without passing TikTok's audit. |
| Scope approval | Requires approved `video.publish` scope (app-level **and** per-user authorization). ([Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)) | OAuth + approval timeline must be planned. |
| Mandatory creator-info call | Must call `/publish/creator_info/query/` and **render the creator's real privacy options**; honor the choice or get `privacy_level_option_mismatch`. Commercial-content disclosure UI is mandatory. | Publish flow must fetch + display real privacy options before posting. |
| PULL_FROM_URL gate | Pulling from R2/Stream (`PULL_FROM_URL`) requires **domain/URL-prefix ownership verification** in the TT4D console. Audited clients still face **~15 posts/creator/day**, shared across all clients. | Verify R2/Stream domain; respect the per-creator cap in the scheduler. |

**v1 resolution:** ship TikTok publishing **behind the Ayrshare Publisher adapter** (Ayrshare carries its own approved TikTok integration), de-risking the audit. Whether Marque *also* pursues its own Content Posting API audit is an **Open question** (see §11), not an assumption.

### 7.2 Instagram Graph API Content Publishing — **[SCOPE-GATING]**

| Constraint | Detail | Implication |
|---|---|---|
| Publish rate limit | **~25 API-published posts per rolling 24h per IG account** (Meta documents up to 100, but real-world throttling lands at 25–50; Reels + feed + stories share the bucket; it's a **rolling timestamp window**, not a calendar reset; newer/low-engagement accounts get stricter limits). ([Meta `content_publishing_limit`](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit/), [Ayrshare on Error 9](https://www.ayrshare.com/solutions/instagram-graph-api-error-9-the-25-post-daily-limit-how-to-fix-it/)) | "Post all week" is fine (a week of clips ≪ 25/day). **No "flood" use case is permitted.** |
| Pre-flight check | Must query `GET /<IG_ID>/content_publishing_limit` before bulk scheduling to avoid Error 9. | The **Publisher adapter must check the limit endpoint** and the scheduler must be limit-aware. |
| Account + flow | Requires an IG **Business/Creator** account linked to a Facebook Page; publishing is async (container → publish two-step). | Onboarding must verify account type; publish is two-step async. |

**v1 resolution:** validates the "schedule across the week" framing and **rules out any flooding feature.** The scheduler surfaces the rolling window calmly (see Open question in §11 on the IG-limit UX).

### 7.3 Apple App Store review — AI + UGC — **[SCOPE-GATING for launch]**

| Requirement | Detail | Implication |
|---|---|---|
| Guideline 1.2 — UGC moderation | Marque publishes user-generated video, so Apple requires a moderation system: **filter objectionable content, a report mechanism, a way to block abusive users, a published support contact, and action on reports within 24h.** ([App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)) | **Launch-blocking.** v1 must ship report/block + a 24h-actionable moderation path. |
| AI transparency (5.1.2(i), enforced from **Nov 13, 2025**) | If personal data (prompts, audio, images, the creator's footage) is shared with third-party AI (Anthropic, AssemblyAI, the MCP toolchain), the app must **clearly disclose and obtain explicit consent**, with a visible user-facing control. ([5.1.2(i) explainer](https://dev.to/arshtechpro/apples-guideline-512i-the-ai-data-sharing-rule-that-will-impact-every-ios-developer-1b0p), [OpenForge 2025 AI rules](https://openforge.io/app-store-review-guidelines-2025-essential-ai-app-rules/)) | **Launch-blocking.** v1 must ship an AI-disclosure consent layer. |

**v1 resolution:** the consent/disclosure layer **and** basic content-safety moderation + report/block are **in v1, not a later phase.** See `14-appstore-compliance-legal.md`. Aligns with Principle 4 (human-in-the-loop).

### 7.4 Subscriptions — **[SCOPE-GATING]**

| Constraint | Detail | Implication |
|---|---|---|
| Apple IAP mandatory | iOS digital subscription **must** use Apple IAP — **StoreKit 2 + RevenueCat.** **Stripe must not appear on the iOS paywall** (reserved for a future web billing surface only). ([RevenueCat StoreKit 2 tutorial](https://www.revenuecat.com/blog/engineering/ios-in-app-subscription-tutorial-with-storekit-2-and-swift/)) | iOS paywall is StoreKit 2 / RevenueCat **only.** Stripe is a non-goal for v1 (§9). |
| Remote paywall config | Serve product identifiers / paywall config remotely (RevenueCat remote paywalls) to A/B test without app updates. ([RevenueCat Paywalls docs](https://www.revenuecat.com/docs/tools/paywalls)) | Paywall is remotely configurable from day one. |
| Server-side entitlement truth | Rely on **App Store Server Notifications + RevenueCat webhooks** for entitlement truth — **never trust the client.** ([StoreKit views guide](https://www.revenuecat.com/blog/engineering/storekit-views-guide-paywall-swift-ui/)) | Entitlement state is server-validated. |

See `11-monetization.md`.

---

## 8. Metrics

### 8.1 North-star metric (NSM)

> **% of creators who published ≥ 3 clips in the last 7 days ("consistent creators").**

This directly encodes the mission — *consistency* — rather than raw volume. We deliberately reject "clips per minute" (Opus Clip's vanity throughput metric, §3.3). A volume-based secondary, **weekly published clips per active creator**, is tracked as a supporting indicator. (Exact NSM wording is an Open question — see §11.)

### 8.2 Activation metric (the "aha")

> **First batch recorded → ≥ 1 clip scheduled/published within the first session / 48h.**

This is the moment the promise is *felt* — film once, and clips appear, scheduled. A **sub-activation** milestone — *Brand Graph built + first script approved* — is tracked as the leading indicator that predicts activation.

### 8.3 Key funnel (time-to-value oriented)

```
signup
  → page connected (Brand Graph seeded)
    → first script approved
      → first batch recorded            ← highest-friction step; watch drop-off
        → first clip rendered
          → first publish               ← "north of the funnel" / activation
            → first performance teardown viewed
              → second batch            ← the loop closing = retention
```

| Stage | Why it matters | Drop-off watch |
|---|---|---|
| Page connected | Brand Graph seed; powers everything downstream | OAuth / account-type friction |
| First script approved | Sub-activation; voice-fidelity moment | Voice mismatch → abandon |
| **First batch recorded** | Highest-friction step in the whole product | **Primary drop-off to instrument** |
| First clip rendered | Proof the pipeline works | Render latency / failure |
| **First publish** | Activation; promise realized | **Publish-auth** (OAuth + platform limits, §7) |
| Second batch | The loop closing — retention | Cadence break → churn |

### 8.4 Retention proxies

- **Weekly batch cadence** (does the creator come back to record each week?).
- **Streak length** (consecutive consistent weeks — surfaced as the single gold glyph on Today, §5).

All events are instrumented via **PostHog**, with crash/error via **Sentry** and remote config / flags for paywall + feature gating. See `15-infra-observability-testing.md`.

---

## 9. Scope — v1 vs. later phases vs. non-goals

### 9.0 Device & platform scope (BINDING — the single source of truth)

This subsection is the **authoritative platform decision** for the entire spec. Sibling docs do **not** re-litigate it; they inherit it. Any device/idiom question elsewhere resolves here.

| Decision | Value | Rationale |
|---|---|---|
| OS | **iOS 17+** | Observation framework, Swift Concurrency maturity, StoreKit 2. |
| Device family | **iPhone only** for v1 | The persona records on the phone they already carry; the HERO loop (batch record + teleprompter) is a held-in-hand, portrait, one-thumb interaction. An iPad-optimized layout pass is unjustified spend against the v1 thesis. |
| iPad | **In scope only as a scaled-iPhone build** — i.e. the iPhone app runs on iPad in **compatibility mode** (portrait, iPhone-idiom layout, letterboxed/scaled by the system). **No iPad-native layouts, no split-view, no multi-column, no Apple Pencil, no 13-inch screenshot assets.** | Keeps the app installable on iPad without committing engineering or design to a second layout system. It is a *fallback*, not a supported surface. |
| iPad-native (future) | **Deferred** (later phase) | A true iPad layout for Record/teleprompter, the calendar grid, and Library would be a dedicated milestone if data justifies it. |
| Android / web | **Out of scope** (see §9.3) | — |

**Binding consequences that cascade (so no sibling doc is left dangling):**

- **`02-design-system.md`** — the design system targets the **iPhone idiom only**. Where haptics or layout would "degrade on iPad," that is the *expected and accepted* behavior of compatibility mode, not a gap to spec around. No iPad breakpoints are required.
- **`05-screens-produce.md` / `09-video-pipeline.md`** — Record, the teleprompter, the Library/Clip editor, and the calendar grid are specified for **portrait iPhone** and are **not** required to have an iPad-native layout. Capture is always portrait-first (vertical-clip output anyway).
- **`16-completeness-checklist.md`** — App Store screenshot assets are required for **iPhone display sizes only** (6.9" / 6.5" / 5.5"). The **13-inch iPad 2064×2752 asset requirement does not apply** to v1 and is removed from the checklist (it returns only if iPad-native ships per the deferred milestone above).

> Any sibling doc that still carries this as an *Open Question* is stale against this decision and should defer to §9.0.

### 9.1 v1 (must-ship)

| Capability | Notes | Spec |
|---|---|---|
| Brand Graph seed | Analyze the creator's existing page → structured brand model | `03-onboarding.md`, `06-brand-graph.md` |
| Script generation in voice | Virality Engine; Opus 4.8 + cached Brand Graph prefix | `07-ai-system.md` |
| Batch record + teleprompter | AVFoundation; the HERO step | `05-screens-produce.md`, `09-video-pipeline.md` |
| Format Library (subset) | A starting set of render-recipes via ClipEngine / Shotstack | `08-format-virality.md` |
| Durable render pipeline | Trigger.dev runs; AssemblyAI + Shotstack; R2/Stream | `09-video-pipeline.md`, `12-backend-data-security.md` |
| Schedule + publish | Via **Ayrshare Publisher adapter** (de-risks TikTok audit + IG limits) | `10-social-publishing.md` |
| Today (one directive + streak + trend line) | The anti-clutter home | `01-information-architecture.md`, `04-screens-create.md` |
| RevenueCat paywall | StoreKit 2; remotely configurable; server-validated entitlements | `11-monetization.md` |
| AI-disclosure consent + UGC moderation | **Launch-blocking** (§7.3): consent layer + report/block + 24h action | `14-appstore-compliance-legal.md` |
| Basic Insights pullback | Performance data + first teardown cards | `05-screens-produce.md`, `10-social-publishing.md` |

### 9.2 Later phases

| Capability | Why deferred |
|---|---|
| In-house ClipEngine (bring the MCP toolchain in-house) | Build on MCP first; internalize once the loop is proven (Principle 2 makes this a swap) |
| Full Format Library | Ship a subset in v1; expand the render-recipe catalog over time |
| Trend Radar dedicated screen (full depth) | v1 ships the one Today line; deepen the dedicated screen later |
| Hook Lab depth | v1 ships nested basics; expand progressive disclosure later |
| Coach teardown feed (full) | v1 ships basic teardown cards; build the feed + push cadence later |
| Referral loop | A Settings row + one earned prompt; expand mechanics later |
| Web billing (Stripe) | **Future web surface only — never the iOS paywall** (§7.4) |
| Expanded platforms (YouTube Shorts) | After IG + TikTok are solid |
| Direct (non-Ayrshare) IG/TikTok integrations | Post-audit; behind the same Publisher adapter (Principle 2) |

### 9.3 Explicit non-goals

Marque is deliberately **not**:

- **A general-purpose video editor.** We are a consistency system, not a timeline editor (vs. CapCut/Captions).
- **A faceless / avatar generator.** The real creator's face and voice are the point (vs. HeyGen/Revid). This follows from Principle 4.
- **A multi-user team dashboard.** We serve the solo creator-operator, not content teams (vs. Vizard).
- **A public-TikTok-posting tool at launch** — gated by TikTok's audit (§7.1).
- **A "clip flooding" tool** — IG's rolling ~25/day cap (§7.2) and our calm doctrine both forbid volume-maxing.
- **Android.** iOS 17+ only for v1.
- **An iPad-native app** in v1. iPhone-only; the app runs on iPad solely as a scaled-iPhone compatibility build (§9.0). A true iPad layout is a deferred milestone, not a v1 commitment.
- **A web app** in v1.
- **A Stripe-on-iOS paywall** — Apple IAP is mandatory (§7.4).

---

## 10. Glossary of canonical names

| Term | Meaning |
|---|---|
| **Marque** | The product. |
| **Brand Graph** | The persistent CONTEXT LAYER — a structured, compounding model of the creator. The moat. |
| **Virality Engine** | The prescriptive system that writes scripts in voice and chooses hooks + formats. |
| **Format Library** | The catalog of structured **render-recipes** (split-screen, 3-up, green-screen, faceless AI-visual, before/after, myth-buster, listicle, POV, reaction, B-roll+caption-hook). |
| **Core loop / HERO loop** | `Brand Graph → Virality Engine → batch record → ClipEngine → Publisher → Insights`. |
| **ClipEngine** | Adapter over the clip pipeline (MCP creative toolchain v1 → in-house later). |
| **Publisher** | Adapter over social publish/schedule (Ayrshare in v1). |
| **Insights** | Adapter over analytics pullback (Phyllo/Ayrshare). |
| **Today** | The calm home screen: one directive + one gold streak glyph + one trend line. |
| **Anti-clutter doctrine** | Product Principle 1 — one idea per screen; never bolt features onto Today. |

---

## Open questions

1. **TikTok audit path (§7.1).** Does v1 rely solely on Ayrshare's pre-approved TikTok integration (the working assumption — likely yes), or does Marque also pursue its own Content Posting API audit? This determines whether *public* TikTok posting under Marque's own client is in v1. **Owner: Eng + Legal.**
2. **IG rolling-limit UX (§7.2).** How does the scheduler surface the rolling ~25/day cap inside a calm, one-idea-per-screen interface — without alarming the creator or violating Principle 1? Needs a design spec in `10-social-publishing.md`. **Owner: Design + Eng.**
3. **Exact NSM wording (§8.1).** *"Consistent creators (≥ 3 published / 7d)"* vs. *"weekly published clips per active creator."* The former is recommended as the headline NSM; confirm before instrumentation locks in `15-infra-observability-testing.md`. **Owner: Product.**
4. **Moderation depth for Guideline 1.2 (§7.3).** Automated content-safety classifier (Haiku 4.5) at submit time vs. report-only at launch. Apple wants *demonstrable* 24h action on reports — confirm the minimum viable scope with `14-appstore-compliance-legal.md`. **Owner: Product + Eng + Legal.**

## Sources

- [Vizard — 9 best AI video clipping tools 2026](https://vizard.ai/blog/9-best-ai-video-clipping-tools-2026) — competitor positioning (Opus speed vs. Vizard control vs. HeyGen avatars), pricing.
- [Vizard — best video editing tools 2025 (Vizard vs. Opus Clip)](https://vizard.ai/the-12-best-video-editing-tools-in-2025-turn-long-demos-into-viral-ugc-clips-vizard-vs-opus-clip-more/) — Vizard feature set and pricing.
- [TikTok — Content Posting API: Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post) — `video.publish` scope, `creator_info/query`, unaudited = private-only, error codes.
- [TikTok — Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) — unaudited caps: 5 users/24h, `SELF_ONLY`, ~15 posts/creator/day, `PULL_FROM_URL` domain verification.
- [Meta — `content_publishing_limit` reference](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit/) — the endpoint to query the IG rolling 24h publish quota.
- [Ayrshare — Instagram Graph API Error 9 / the 25-post daily limit](https://www.ayrshare.com/solutions/instagram-graph-api-error-9-the-25-post-daily-limit-how-to-fix-it/) — real-world IG publishing cap behavior + mitigation.
- [Apple — App Store Review Guidelines](https://developer.apple.com/app-store/review/guidelines/) — 1.2 UGC moderation (report/block/24h action), 3.1.1 IAP, AI rules.
- [Apple's Guideline 5.1.2(i) — the AI data-sharing rule](https://dev.to/arshtechpro/apples-guideline-512i-the-ai-data-sharing-rule-that-will-impact-every-ios-developer-1b0p) — Nov 13 2025 third-party-AI disclosure/consent requirement.
- [OpenForge — App Store Review Guidelines 2025: essential AI app rules](https://openforge.io/app-store-review-guidelines-2025-essential-ai-app-rules/) — 2025 AI app review expectations.
- [RevenueCat — iOS in-app subscription tutorial (StoreKit 2)](https://www.revenuecat.com/blog/engineering/ios-in-app-subscription-tutorial-with-storekit-2-and-swift/) — server notifications, remote product config, entitlement validation.
- [RevenueCat — Paywalls docs](https://www.revenuecat.com/docs/tools/paywalls) — remote-configurable paywalls / A/B testing without app updates.
- [RevenueCat — StoreKit views & SwiftUI paywall guide](https://www.revenuecat.com/blog/engineering/storekit-views-guide-paywall-swift-ui/) — entitlement truth via server notifications.
- [Anthropic — Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — TTL (5m/1h), 4 breakpoints, `tools → system → messages` hierarchy, breakpoint placement.
- [Trigger.dev — How it works](https://trigger.dev/docs/how-it-works) — checkpoint/freeze-during-wait, webhook resume, retries — durability model for video/AI jobs.
