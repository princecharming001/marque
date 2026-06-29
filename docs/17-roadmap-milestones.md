# 17 — Build Roadmap, Milestones & Risks

> **Marque** — *turn overwhelmed creators into consistent ones.*
>
> **Scope.** This document is the build plan: how Marque goes from an empty repository to an App Store submission that ships the full hero loop. It defines seven milestones (**M0 → M6**), each with a goal, deliverables, dependencies, the critical path, and an explicit **Definition of Done (DoD)**. It names the top execution risks with concrete mitigations, the single recommended first thing to build, and the binding **v1 "Definition of Done" for submission**.
>
> This is a **sequencing and risk** contract, not a feature spec. Where a feature's internals live elsewhere, this doc cross-references the owning sibling and does not re-specify it.

**Status:** Canonical · **Owners:** Eng lead + Product lead · **Last updated:** 2026-06-29

### Sibling map (where this doc reaches)

| File | Why we cross here |
|---|---|
| `00-overview.md` | Product principles; the binding platform-constraints table (defer to it on any IG/TikTok limit conflict) |
| `02-design-system.md` | The cream/serif/gold shell + calm-error doctrine built in M0, in parallel by design |
| `03-onboarding.md` | M1 onboarding flow, professional-account detection, AI-data-sharing consent placement |
| `04-screens-create.md` / `05-screens-produce.md` | M2 Script Studio / Hook Lab; M3 Record; M4 Calendar/publish screens |
| `06-brand-graph.md` | M1 Brand Graph + page ingestion — the context layer the whole loop reads |
| `07-ai-system.md` | `LLMRouter`, Claude routing, prompt caching, voice-check gate, moderation (M1/M2/M5) |
| `10-social-publishing.md` | `Publisher` / `Insights` adapters, scheduler, IG/TikTok constraints (M4) |
| `11-monetization.md` | StoreKit 2 + RevenueCat paywall, entitlement mirror, ASSN V2 (M0 wiring, M6 gate) |
| `12-backend-data-security.md` | Supabase schema, RLS, the FastAPI + Trigger.dev orchestration spine (M0) |

> **Anti-clutter doctrine (binding, `00-overview.md`).** Nothing in this roadmap permits bolting a feature onto **Today**. Every milestone that adds capability adds it *one layer deep* — its own calm screen or a contextual sheet. The Today home screen ships in M0 and stays exactly **one directive + one gold streak glyph + one trend line** through M6. A milestone is not "done" if it added a second thing to Today.

---

## 0. The one sequencing insight that shapes everything

The two longest-lead items on the entire plan are **external review processes that gate the publishing milestone (M4) and cannot be compressed by engineering effort**:

1. **Meta Instagram App Review** for Advanced Access to `instagram_business_content_publish` — and App Review **requires at least one successful API call with the permission plus an end-to-end screencast** before you may submit ([Instagram App Review](https://developers.facebook.com/docs/instagram-platform/app-review/)).
2. **TikTok Content Posting API audit** — which takes **2–6 weeks** and **frequently rejects apps framed as "personal account management utilities."** Until audited, every TikTok post is force-downgraded to `SELF_ONLY` (private) regardless of the `privacy_level` you send, capped at ≤5 private test accounts ([TikTok Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines); [audit timeline + framing](https://www.rapidevelopers.com/api-automations/how-to-automate-tiktok-video-posting-using-the-api)).

**Therefore:** "submit the social-API audits" is a **Day-1 (M0/M1) deliverable with its own swimlane**, not an M4 task. Both audits are submitted against a working demo + screencast produced by the very first thing we build (§9). Everything else — Brand Graph, Script Studio, Record, the clip pipeline — proceeds **behind the `Publisher` adapter** using **TikTok `SELF_ONLY` sandbox posting** and **a single dev Instagram Business account** until the audits clear. This single decision is the difference between launching on schedule and launching 6 weeks late.

```
        M0 ───────── M1 ───────── M2 ───────── M3 ───────── M4 ───────── M5 ───────── M6
   (spine+shell) (brand graph)  (scripts)    (record+clip) (publish)   (learning)  (compliance
        │                                                     │                       +submit)
        │  ◀═══════════ SOCIAL AUDIT SWIMLANE (runs the whole time) ═══════════▶      │
        └─ submit Meta App Review + TikTok audit here, NOT at M4 ───────────────────▶ cleared
```

The audit swimlane is the critical path of the *program*. The engineering milestones are the critical path of the *product*. They run concurrently and converge at M4 → M6.

---

## 1. Milestone phasing at a glance

| M | Name | Goal (one line) | Exit gate |
|---|---|---|---|
| **M0** | Foundations: spine + shell + auth | The durable orchestration spine + adapter pattern + design system + auth all exist and a stub clip flows end-to-end | Stub video → transcribe → render → R2 URL → TikTok `SELF_ONLY` works; **both audits submitted** |
| **M1** | Onboarding + Brand Graph + ingestion | A creator connects their page, we ingest it, build the Brand Graph, and capture AI-data-sharing consent | A real creator's Brand Graph exists; consent + report/block/contact scaffold in place |
| **M2** | Script Studio + Format Library + Hook Lab | Opus writes ≥3 voice-matched scripts; creator picks render-recipe formats; Hook Lab nested in the reader | Haiku voice-check gate passes; ≥3 scripts in the creator's voice; formats selectable |
| **M3** | Record + clip pipeline | One batch talking-head session (or uploaded long video) fans out into N rendered clips in ≥3 formats | ≥5 clips render to public R2 URLs from one session in ≥3 recipes |
| **M4** | Publishing + scheduling | Clips schedule + publish to IG (real) and TikTok (audited→PUBLIC), rate-limit-safe | Real post to both platforms within rate limits; scheduler honors caps |
| **M5** | Retention + virality + learning loop | Metrics pull back, Coach teardown cards + push, streaks, moderation/report live | Loop closes: a post's performance refines the next directive; moderation acts in <24h |
| **M6** | Compliance + polish + submission | Every App Store gate satisfied; all States hardened; paywall live | v1 submission DoD (§8) fully green |

---

## 2. M0 — Foundations: orchestration spine, adapters, design system, auth

**Goal.** Stand up the *risky structural spine* end-to-end on a stub before any feature exists, so the async/idempotent orchestration and the public-URL handoff that **both** IG and TikTok require are proven on Day 1 — and so we have the live demo that lets us start the social audits immediately.

### Key deliverables

- **Repo + CI/CD**: Swift + SwiftUI (iOS 17+) app target; FastAPI orchestration service; Trigger.dev project wired; Supabase project (Postgres + Auth + Storage + Realtime); Cloudflare R2 bucket + Stream; PostHog + Sentry SDKs initialized; remote-config flag store.
- **Adapter skeletons** (`01`/`07`/`10`): `LLMRouter`, `ClipEngine`, `Publisher`, `Insights`, `Billing` — each a one-file vendor-hiding module with a stub + one real impl behind a flag. *No call site names a vendor string.*
- **The orchestration spine (the spike):** a Trigger.dev pipeline using the **Router + Coordinator pattern** — analyze → route by source → `batchTriggerAndWait` parallel post-processing — that takes a sample video → **AssemblyAI** transcribe (webhook + polling fallback) → **Shotstack** template render (one format, sandbox) → **R2 public HTTPS URL** → `Publisher` posts to **TikTok `SELF_ONLY`**. Every external-effect child task carries an **`idempotencyKey`** so parent retries never double-render or double-publish; payloads >10 MB go to R2, not the task payload ([Trigger.dev media processing](https://trigger.dev/docs/guides/use-cases/media-processing); [idempotency](https://trigger.dev/docs/idempotency)).
- **Auth**: Supabase Auth (Sign in with Apple) + RLS baseline; session restored at launch/foreground.
- **Design system shell** (parallel, by design — see `02-design-system.md`): cream `#F4F1EA` / near-black `#0E0E10` tokens, Playfair/Tiempos display + Inter/Söhne/Matter body, single gold `#C9A227`, soft shadows, paper texture, the breathing-motion curves, and the **Today** scaffold (one directive + gold streak glyph + one trend line) wired to placeholder data.
- **RevenueCat wiring** (`11-monetization.md`): SDK installed, **Offering** fetched dynamically (never product-ID branching), `Transaction.updates` listener registered. No paywall UI yet — just the rails.
- **Audit swimlane kickoff (critical):** register the Meta app + request `instagram_business_content_publish`; make ≥1 real publish call to the dev IG Business account; register the TikTok app + complete the Content Posting audit form framing Marque as a **broad-audience creator tool**; record the screencast from the M0 spike. **Submit both.**

### Dependencies
None upstream (this is the floor). Downstream, *everything* depends on the spine + adapters existing.

### Critical path
`Supabase auth` → `Trigger.dev spine spike` → `R2 public URL` → `Publisher SELF_ONLY post` → **screencast** → **audits submitted**. The design shell runs fully in parallel and is off the critical path.

### Definition of Done
- A sample MP4 dropped into the pipeline produces a watermarked Shotstack render at a **public R2 URL** and a live `SELF_ONLY` TikTok post, with the whole job durable, idempotent, and re-runnable.
- `cache_read_input_tokens` plumbing and the `LLMRouter` answer a trivial Haiku call.
- Sign in with Apple works; RLS denies cross-user reads.
- **Meta App Review submitted with a passing real API call + screencast; TikTok audit submitted.** (Submission, not approval, is the M0 gate.)
- Sentry captures a forced crash; PostHog logs a launch event.

---

## 3. M1 — Onboarding, Brand Graph & page ingestion

**Goal.** A real creator connects Instagram/TikTok, we ingest their existing page, and build the persistent **Brand Graph** context layer — *with explicit AI-data-sharing consent captured first.* This is where the "context is the moat" thesis becomes real (`06-brand-graph.md`, `07-ai-system.md`).

### Key deliverables
- **Onboarding** (`03-onboarding.md`): the quiet, declarative flow ("What do you want to be known for?"), one idea per screen. Connect IG Business + TikTok.
- **Professional-account detection (real gotcha):** IG publishing only works for **Business/Creator accounts connected to a Facebook Page with Page Publishing Authorization (PPA) and Page-level 2FA complete**. Onboarding must detect a personal/unauthorized account and surface a calm fix-it path — not a dead end ([IG Content Publishing](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing/)).
- **Page ingestion** → Brand Analyzer → **Brand Graph** built and persisted (Supabase, `06`/`12`). Brand Graph is structured as the **cached prefix** for every downstream LLM call.
- **AI-data-sharing consent screen (hard App Store gate, App Store Guideline 5.1.2(i)):** explicitly disclose that the creator's page content, voice, and video are shared with third-party AI/processing — **Anthropic, AssemblyAI, Shotstack, the MCP toolchain, Ayrshare** — and obtain permission **before** any such call ([Nov 13 2025 Guidelines update](https://developer.apple.com/news/?id=ey6d8onl)). No paid AI job may run until this consent row exists.
- **UGC scaffold (start it here, finish in M5):** the data model + UI hooks for **report/flag**, **block user**, and **published contact info** — Apple Guideline 1.2 requires all four (moderation, report, block, contact) ([App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)).

### Dependencies
M0 (auth, adapters, Supabase). The `Insights` adapter's token exchange reuses the same OAuth connections established here.

### Critical path
`Connect account` → `professional-account / PPA validation` → `page ingestion` → `Brand Graph persisted`. The consent screen blocks the *first* ingestion call, so it is on the path.

### States (binding for this milestone's screens)
| State | Behavior |
|---|---|
| Loading | Breathing skeleton; "Reading your page…" — never a spinner-only screen |
| Empty | New creator, no page content → guided "post one thing or connect another account" |
| Error | Ingestion partial → calm line + retry; Brand Graph builds from what we have |
| Offline | Queue ingestion; build Brand Graph on reconnect (foreground revalidation) |
| Permission-denied | Personal/non-PPA account → fix-it path with steps, not a wall |

### Definition of Done
A real creator completes onboarding, **AI-data-sharing consent is captured**, and a populated Brand Graph exists in Supabase. Report/block/contact scaffolding compiles and is reachable. The M0 screencast is updated to show real onboarding for the audit reviewers.

---

## 4. M2 — Script Studio, Format Library & Hook Lab

**Goal.** Opus 4.8 writes **≥3 viral scripts in the creator's voice**; the creator chooses viral **Formats** (structured render-recipes, not blank talking heads); the **Hook Lab** lives nested inside the script reader via progressive disclosure (`04-screens-create.md`, `08-format-virality.md`, `07-ai-system.md`).

### Key deliverables
- **Script Studio**: Opus 4.8 generates scripts using the **prompt-cached** Brand Graph + voice exemplars as the stable prefix. Cache reads cost **0.1× input (~90% off)**; the **1-hour-TTL** cache block (now GA) covers a creator running many scripts in a session; minimum cacheable prefix on Opus 4.8 is **1,024 tokens**. Stable content (system + Brand Graph + tool defs) goes first, the volatile incoming message last — caching is a literal byte-match prefix, so one reordered key silently invalidates it. Verify `cache_read_input_tokens > 0` in prod ([Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching); [release notes](https://platform.claude.com/docs/en/release-notes/overview)).
- **Haiku 4.5 voice-check gate**: every script passes a Haiku **structured-output** pass/fail voice check (`structured-outputs-2025-11-13` strict tool use) before it can reach the creator. A fail loops back to Opus, never shows the creator an off-voice draft.
- **Format Library**: render-recipes (split-screen, 3-up talking heads, green-screen, faceless AI-visual, before/after, myth-buster, listicle, POV, reaction, B-roll+caption-hook). Each recipe is a **Shotstack Template with merge fields** (`{{ }}`) — the natural fit for "formats = render-recipes." Recipes carry **seeded virality priors** (from the `virality-predictor` MCP) so the loop works on Day 1 with zero performance history.
- **Hook Lab**: nested in the script reader; surfaces hook variants on demand, never as a separate cluttering screen.
- **AI moderation (start it here):** generated scripts pass output moderation before display; **named-person / deepfake-style prompts are gated** for the faceless/AI-visual formats (App Store 1.1) ([rejection index](https://pushmyapp.ai/blog/app-store-rejection-reasons)).

### Dependencies
M1 (Brand Graph + voice exemplars + consent). M0 (`LLMRouter`). Format recipes depend on Shotstack templates authored here, consumed by M3.

### Critical path
`Brand Graph cached prefix` → `Opus script gen` → `Haiku voice-check gate` → `creator-readable scripts`. Format-recipe authoring runs in parallel.

### Definition of Done
For a real creator, Opus produces **≥3 scripts that pass the Haiku voice-check**, the creator can select **≥3 Format recipes**, and Hook Lab opens inside the reader. `cache_read_input_tokens > 0` confirmed in logs.

---

## 5. M3 — Record + clip pipeline (the HERO loop)

**Goal.** The **batch "film once → post all week"** loop: one talking-head session (AVFoundation camera + teleprompter) — **or an uploaded existing long video** (repurpose-in, same pipeline) — fans out into **N platform-ready clips in ≥3 Format recipes** (`05-screens-produce.md`, `08-clip-pipeline`/`ClipEngine`). This is the hero, not an add-on.

### Key deliverables
- **Record screen**: AVFoundation capture + teleprompter scrolling the M2 script; Swift Concurrency for capture state. **Two sources on the same Record entry point:** live batch session **and** "upload existing long video."
- **Clip pipeline (the spine, now loaded for real):** one upload → **AssemblyAI** transcribe (async, **RTF ≈ 0.008×, typical file <45s**; webhooks with **10× retries / 10s apart, receiver must respond <10s**, plus a polling fallback at ~2× mean TaT) → **LLM moment/chapter detection via the `LLMRouter`** (⚠️ AssemblyAI `auto_chapters` + `summarization` are **deprecated and silently 500 on Universal-3 Pro** — do moment detection with our own Claude call, not those flags) → **Shotstack** template renders, one per clip × format (**≈20s render per minute of video**; prod rate limit **300 Edit req/60s ≈ 5 rps**, 429 → exponential backoff; source ≤5 GB, source+output ≤10 GB) → outputs to **R2 public URL** ([AssemblyAI pricing/deprecation](https://www.assemblyai.com/pricing); [TaT](https://www.assemblyai.com/docs/faq/how-long-does-it-take-to-transcribe-a-file); [webhooks](https://assembly-preview.mintlify.app/docs/getting-started/webhooks); [Shotstack limits](https://shotstack.io/docs/guide/architecting-an-application/limitations/); [templates](https://shotstack.io/docs/guide/architecting-an-application/templates/)).
- **Bounded fan-out economics**: one session = **1 transcribe + N renders**. Transcription is pennies/hour (Universal-3 Pro **$0.21/hr**); render is ~20s/clip-minute. The expensive path is **MCP AI-visual/faceless generation** — gate it behind a credits/feature flag and cap per-job (mirrors the maxapp credit pattern; see `11-monetization.md`).
- **Idempotency everywhere**: each render child task carries an `idempotencyKey` (scope `run`); a parent retry never re-bills a render or re-posts.

### Dependencies
M2 (scripts + Format recipes). M0 (full spine — now exercised at production fan-out instead of stub). R2/Stream public URLs.

### Critical path
`Record/upload` → `single transcribe` → `LLM moment detection` → `N parallel Shotstack renders` → `N R2 URLs`. The fan-out is the gating latency; webhooks + polling fallback ensure no job silently stalls.

### States
| State | Behavior |
|---|---|
| Loading | Per-clip render progress (calm, breathing); never a frozen modal |
| Empty | No recording yet → the one directive to film today's batch |
| Error | One render fails → that clip alone retries; the others ship |
| Offline | Upload queues; render fires on reconnect; durable Trigger.dev run survives app death |
| Permission-denied | Camera/mic denied → calm explainer + Settings deep-link |

### Definition of Done
From **one real batch session (or one uploaded long video)**, **≥5 clips render to public R2 URLs across ≥3 Format recipes**, with the job durable and idempotent end-to-end.

---

## 6. M4 — Publishing & scheduling

**Goal.** Clips schedule and publish to **Instagram (real)** and **TikTok (audited → PUBLIC)**, rate-limit-safe, behind the `Publisher` adapter (`10-social-publishing.md`). This milestone **converges with the audit swimlane** — it can only fully complete once audits clear, which is why they started at M0.

### Key deliverables
- **Instagram publish** (2-step async container): `POST /<IG_ID>/media` (`media_type=REELS`, public `video_url`, `upload_type=resumable` for large files) → `POST /<IG_ID>/media_publish` with `creation_id`. **Containers expire after 24h.** Meta **cURLs the `video_url`**, so the **R2/Stream public URL is load-bearing** ([IG Content Publishing](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing/)).
- **IG rate-limit self-enforcement (scheduler must bake in):** **100 API-published posts / rolling 24h** and **400 containers / rolling 24h**; check live usage via `GET /<IG_ID>/content_publishing_limit` before each scheduled publish ([Media Publish ref](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media_publish/)).
- **TikTok publish — the audit-locked UX (build exactly, or the audit fails).** The reviewer tests these in our *actual* UI: (1) call `/post/publish/creator_info/query/` and **display the creator's nickname**; (2) **render the exact `privacy_level_options` returned and honor the choice** (an unlisted value → `privacy_level_option_mismatch`); (3) a **Commercial Content disclosure toggle** with "Your Brand" / "Branded Content (Paid Partnership)" — and when Branded Content is on, **disable "Only Me"**; (4) **music-usage consent**; (5) post **only after explicit consent**; (6) **no preset captions or promo watermarks** injected by the app ([Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines); [Direct Post ref](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)). **Design constraint:** this UX is spec-locked by the audit, but the calm one-idea-per-screen doctrine must still surface nickname + privacy options + commercial toggle (flag for `10-social-publishing.md`).
- **TikTok caps (even when audited):** ~**15 posts/day/creator** (shared across all clients) + a per-client active-creator cap from the audit estimate. **`PULL_FROM_URL` requires domain/URL-prefix ownership verification** → schedule the **R2/Stream domain-verification task** as an M4 dependency.
- **Scheduler**: Trigger.dev delayed tasks fire at `target_publish_at_utc`; idempotency keys prevent double-publish; reconcile via poll/webhook into `publish_jobs`. "Save & remind" (APNs) fallback when direct publish is gated.

### Dependencies
M3 (clips at public URLs). **The audit swimlane started in M0** (Meta Advanced Access + TikTok audit). The R2/Stream **public domain** must be verified for both IG cURL reach and TikTok `PULL_FROM_URL`.

### Critical path
`Audit clearance` (program-level, started M0) ∥ `Publisher impl + scheduler + rate-limit guard` → `real IG publish` + `real TikTok PUBLIC publish`. If audits lag, M4 ships **provably** in `SELF_ONLY` (TikTok) + dev-IG mode and flips to PUBLIC on clearance with **zero code change** (adapter + flag).

### Definition of Done
A real clip publishes to **both** IG (real) and TikTok (PUBLIC if audited; else `SELF_ONLY` with the flip ready), within rate limits, on schedule, with idempotent reconciliation. The TikTok audit-mandated UX is present and verified against the checklist.

---

## 7. M5 — Retention, virality engine & the learning loop

**Goal.** Close the loop: pull each post's performance back, refine the next directive, and land the retention + safety surfaces — **streaks**, **Trend Radar**, **Coach teardown cards + push**, and the **moderation/report/block** machinery Apple requires (`05-screens-produce.md`, `07-ai-system.md`).

### Key deliverables
- **Insights pullback**: `Insights` adapter (Phyllo/Ayrshare) writes `post_metrics`; the learning loop tightens virality priors per creator.
- **Coach feed + push**: performance teardown cards (Opus), one directive surfaced on Today, full archive in Insights. Bulk teardown scoring + voice checks use **Haiku 4.5 via the Message Batches API (50% off)** since they're non-interactive.
- **Trend Radar**: **one line on Today** → dedicated Trends screen (anti-clutter doctrine). **v1 source is committed (`08-format-virality.md §7.0`)**: a daily **Claude + web-search external pass per active niche** (Haiku 4.5 + Message Batches, cached and fanned out — cost flat in creator count), so the Today line is **never blank at GA**; aggregated first-party Marque-creator lift blends in per niche as `clip_outcomes` accrue. This is *not* an open question — Open Q3 below remains scoped to Phyllo-vs-Ayrshare for the **Insights** pullback, which is a separate adapter from the trend source.
- **Streaks/consistency**: **one gold glyph on Today**, full view in Profile.
- **Referral loop**: a **row in Settings** + **ONE earned prompt** after a genuine win — never nagging.
- **Moderation + report + block (App Store 1.2 — finish what M1/M2 started):** filter objectionable material **before posting**, a user-facing **report/flag** mechanism, **block abusive users**, **published contact info**, and an enforced policy to **act on reports within 24h** (remove + eject). Generative-AI output is moderated before display; a **content policy URL** exists ([App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)).
- **Creator-content age mechanism (App Store 1.2.1(a), Nov 13 2025):** let users identify content exceeding the app's age rating and apply an **age-restriction mechanism on verified/declared age** — Marque is literally a creator-content app ([Nov 2025 update](https://developer.apple.com/news/?id=ey6d8onl)).

### Dependencies
M4 (live posts to pull metrics from). M1/M2 (consent + moderation scaffolds). The learning loop's *cold start* is covered by M2's seeded priors, so M5 refines rather than bootstraps.

### Critical path
`Post goes live (M4)` → `Insights pullback` → `Coach teardown` → `next directive`. Moderation/report/block runs in parallel and is **on the submission critical path** (Apple blocks without it).

### Definition of Done
A real post's performance pulls back, generates a Coach teardown + push, and measurably shifts the next directive. Moderation acts on a test report in <24h; report/block/contact + content-policy URL + age mechanism all live.

---

## 8. M6 — Compliance, polish & submission

**Goal.** Verify (not build) every App Store gate, harden every State, ship the paywall, and submit. M6 is short *only because* the compliance items were built in M1/M2/M5 — M6 audits them.

### Key deliverables
- **Paywall (`11-monetization.md`)**: RevenueCat **Offering**-driven, **Entitlement-checked** (never product-ID), one calm screen. **Server-truth**: verify the **JWS-signed transaction** server-side, treat `VerificationResult.unverified` as a failed purchase, **only `transaction.finish()` after the backend confirms the entitlement**, subscribe to **App Store Server Notifications V2** (`DID_RENEW`/`EXPIRED`/`REFUND` → revoke immediately), mirror entitlement state into Supabase for offline + server-side gating ([StoreKit 2 entitlement design](https://rorklab.net/en/articles/rork-business/storekit2-subscription-server-receipt-entitlement-design); [RevenueCat architecture](https://arfin.dev/blog/revenuecat-architecture-guide)). **"Restore Purchases" is mandatory.** Stripe **never** in the iOS binary.
- **Trial → paid sandbox test** on a **real device** (intro-offer transition).
- **App Store questionnaire**: honest answers to the **AI age-rating questions**; the 5.1.2(i) third-party-AI disclosure consent already shipped (M1) is linked.
- **States hardened on every loop screen**: loading / empty / error / offline / permission-denied — calm, never red, one next action.
- **Observability green**: Sentry crash-free baseline, PostHog funnel events on the whole loop, remote-config kill-switches for AI-visual generation.

### Dependencies
M0–M5 complete; **audits cleared** (else ship the audit-pending posture: TikTok `SELF_ONLY` provable + IG dev-account, with the public flip staged).

### Definition of Done
The **v1 submission DoD (§10)** is fully green and the build is submitted to App Review.

---

## 9. The recommended FIRST thing to build (and why)

**Build the durable job + adapter skeleton end-to-end on a stub, before any feature:**

> Supabase auth → a Trigger.dev pipeline that takes a sample video → **AssemblyAI** transcribe (webhook + polling fallback) → **Shotstack** template render (one format, sandbox/watermarked) → **R2 public URL** → `Publisher` adapter posting **TikTok `SELF_ONLY`**.

**Why this first, specifically:**
1. **It de-risks the entire critical path.** The async/idempotent orchestration spine and the **public-URL handoff that both IG and TikTok require** are the highest-uncertainty structural pieces. Proving them on a stub means every later feature plugs into known-good rails.
2. **It proves the adapter pattern** (`ClipEngine`, `Publisher`) — the "swap a vendor = one-file change" thesis — before we've committed any feature code to a vendor's shape.
3. **It produces the live demo + screencast needed to start the social audits on Day 1**, which is the longest pole in the whole program.

The design system / aesthetic shell (cream/serif/gold, the Today screen) is built **in parallel by design** and is comparatively low-risk — it is off the critical path and must never be allowed to block the spine.

---

## 10. v1 "Definition of Done" for App Store submission (binding)

The hero loop works **end-to-end on a real device for a real creator**:

1. Connect **IG Business + TikTok** (professional-account / PPA validated).
2. **Brand Graph** built from page ingestion, with **AI-data-sharing consent captured** first.
3. **Opus 4.8 writes ≥3 voice-matched scripts**, each having **passed the Haiku 4.5 voice-check**.
4. Record **one batch session** (or upload one long video).
5. **≥5 clips rendered in ≥3 Format-Library recipes** to public R2 URLs.
6. **Scheduled + published to both IG (real publish) and TikTok (PUBLIC, audit cleared)** within rate limits.
7. **Performance pulled back into Insights/Coach**, refining the next directive.

**Plus the App Store gates, all green:**

- Moderation + **report** + **block** + **published contact info** (1.2); acts on reports <24h.
- **Age-restriction mechanism** for creator content (1.2.1(a)).
- **Content-policy URL** + generative-AI output moderation (1.1).
- **AI-data-sharing consent** screen + honest AI age-rating answers (5.1.2(i)).
- **RevenueCat paywall**: working **free-trial → paid**, **Restore**, **server-verified entitlements via ASSN V2**; Stripe absent from the binary.
- **Sentry + PostHog** wired; remote-config kill-switches live.
- **Loading / empty / error / offline / permission-denied** states on **every** screen in the loop — calm, declarative, one next action.

If TikTok's audit has not cleared by submission, the build ships in the **provable `SELF_ONLY` posture** (loop demonstrable end-to-end) with the **PUBLIC flip staged behind the `Publisher` adapter + flag** — submission is **not blocked** on TikTok PUBLIC, but is blocked on real IG publish + every App Store gate above.

---

## 11. Top risks & concrete mitigations

| Risk | Why it bites | Concrete mitigation |
|---|---|---|
| **TikTok audit rejection / 2–6 wk delay** | Unaudited = forced `SELF_ONLY`, ≤5 users; "personal utility" framing is rejected ([guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines); [timeline](https://www.rapidevelopers.com/api-automations/how-to-automate-tiktok-video-posting-using-the-api)) | **Submit at M0/M1** against a working demo; frame as a **broad-audience creator tool**; build the exact mandatory UX (nickname, `privacy_level_options`, commercial toggle, music consent) before submitting; ship to TestFlight able to post `SELF_ONLY` so the loop is provable while pending. |
| **Meta IG Advanced Access delay** | App Review needs ≥1 real call + screencast; personal/non-PPA accounts can't publish ([App Review](https://developers.facebook.com/docs/instagram-platform/app-review/)) | Make ≥1 **real publish call early** (M0); record screencast at M1; **detect non-professional/PPA accounts in onboarding**; keep one dev IG Business account for end-to-end testing pre-approval. |
| **App Store rejection (UGC 1.2 + AI 5.1.2(i)/1.2.1(a))** | Missing moderation/report/block/contact, AI consent, or age mechanism = rejection ([Guidelines](https://developer.apple.com/app-store/review/guidelines/); [Nov 2025](https://developer.apple.com/news/?id=ey6d8onl)) | Ship moderation + report + block + contact, **AI-data-sharing consent**, **age mechanism**, and a **content policy** as **M1/M5** deliverables — not M6; answer AI age-rating questions honestly; M6 only verifies. |
| **Video cost / latency blowout** | N renders + AI-visual gen can balloon cost/time; stalled jobs hang the loop | Bound the loop (**1 transcribe + N template renders ≈ 20s/min**); **Trigger.dev idempotency** to avoid double-render/double-publish; **cap + feature-flag MCP AI-visual generation** per job; **webhooks + polling fallback** so jobs never silently stall ([Shotstack limits](https://shotstack.io/docs/guide/architecting-an-application/limitations/); [Trigger.dev idempotency](https://trigger.dev/docs/idempotency)). |
| **AI voice quality (scripts don't sound like them)** | Off-voice scripts kill trust on the very first value moment | **Brand Graph as a cached few-shot voice corpus** from page ingestion; **Haiku voice-check gate** (structured pass/fail) before any script reaches the creator; **human-in-the-loop edit** in Script Studio; never show a failed-voice draft ([prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)). |
| **Cold-start data (no performance history)** | Day-1 creators have no metrics, so the learning loop can't yet learn | **Format Library ships with seeded virality priors** (`virality-predictor` MCP) so the loop works on day one; the **M5 learning loop refines** after first posts pull back via the `Insights` adapter. |

---

## 12. Cross-cutting build conventions (applied every milestone)

- **Adapters or it doesn't ship.** No call site names a vendor. Adding IG, TikTok, a second LLM, or a new render engine is a one-file change (`07`, `10`).
- **Durable + idempotent by default.** Any external effect (render, publish, paid AI call) runs in a Trigger.dev task with an `idempotencyKey`; payloads >10 MB go to R2.
- **Consent precedes compute.** No paid AI/processing job runs before the AI-data-sharing consent row exists (M1).
- **Calm errors only.** Per `02-design-system.md`: never red, never a modal-only dead end — one declarative line + one tap to act.
- **Today stays sacred.** Every milestone adds capability one layer deep; the Today screen never gains a second element.
- **Verify caching in prod.** Every Brand-Graph-backed call asserts `cache_read_input_tokens > 0` — a zero is a silent cost regression.

---

## Open questions

1. **Apple Developer + Meta + TikTok accounts on Day 1.** The M0 audit swimlane assumes the org's Apple Developer Program enrollment, a Meta app + dev IG Business account, and a TikTok developer app already exist (or can be created same-day). If any is not yet provisioned, the audit clock starts later and M4 convergence slips. *Needs a user decision on who owns provisioning and when.*
2. **MCP-vs-in-house ClipEngine timeline.** The locked stack says "build on MCP first, then bring in-house." Is bringing the clip toolchain in-house a **post-v1** effort (assumed here), or must any part land before submission? This changes M3/M5 scope.
3. **Phyllo vs Ayrshare for the `Insights` pullback at v1.** Both are named behind the `Insights` adapter. Which is the v1 default for metrics, and does it gate any TikTok analytics scopes that need their own approval? *Affects M5 dependencies.*
4. **R2/Stream public domain ownership.** TikTok `PULL_FROM_URL` and Meta's `video_url` cURL both require a verified public domain. Which domain do we verify, and is it ready before M4? *Owner + date needed.*
5. **Launch market / age-gating posture.** The 1.2.1(a) age mechanism's exact threshold and whether we use *declared* vs *verified* age depends on target markets and rating. *Needs a product call before M5.*
6. **Audit-pending launch decision.** If TikTok PUBLIC is not approved by the target submission date, do we ship v1 with the `SELF_ONLY`-provable posture (PUBLIC flip staged) or hold? §10 assumes we ship; confirm.

## Sources

- [Instagram App Review — Advanced Access, ≥1 API call, screencast](https://developers.facebook.com/docs/instagram-platform/app-review/)
- [Instagram Content Publishing — 2-step container, REELS, public `video_url`, PPA, 24h expiry](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing/)
- [Instagram Media Publish reference — 100 post / 400 container rolling-24h limits, `content_publishing_limit`](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media_publish/)
- [TikTok Content Sharing Guidelines — unaudited `SELF_ONLY`, 5-user cap, mandatory UX](https://developers.tiktok.com/doc/content-sharing-guidelines)
- [TikTok Direct Post API reference — error codes, `privacy_level_options`](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)
- [How to Automate TikTok API — 2–6 wk audit, "personal utility" rejection, required UI](https://www.rapidevelopers.com/api-automations/how-to-automate-tiktok-video-posting-using-the-api)
- [Apple App Review Guidelines — 1.2 UGC four requirements, 1.1 AI, 3.1.1 IAP](https://developer.apple.com/app-store/review/guidelines/)
- [Apple News — Nov 13 2025 update: 1.2.1(a) creator age-gate, 5.1.2(i) third-party-AI consent](https://developer.apple.com/news/?id=ey6d8onl)
- [App Store Rejection Reasons Index 2026 — AI moderation, deepfake, consent](https://pushmyapp.ai/blog/app-store-rejection-reasons)
- [StoreKit 2 server-side entitlement design — JWS verify, finish-after-confirm, ASSN V2 REFUND](https://rorklab.net/en/articles/rork-business/storekit2-subscription-server-receipt-entitlement-design)
- [RevenueCat architecture — Entitlements not products, Offerings, webhooks](https://arfin.dev/blog/revenuecat-architecture-guide)
- [AssemblyAI pricing — rates; deprecated `auto_chapters`/`summarization` 500 on Universal-3 Pro](https://www.assemblyai.com/pricing)
- [AssemblyAI TaT / RTF — typical file <45s](https://www.assemblyai.com/docs/faq/how-long-does-it-take-to-transcribe-a-file)
- [AssemblyAI Webhooks — 10× retries / 10s apart, <10s receiver timeout](https://assembly-preview.mintlify.app/docs/getting-started/webhooks)
- [Shotstack Limitations — 20s/min render, 300 req/60s, 5GB/10GB, sandbox watermark](https://shotstack.io/docs/guide/architecting-an-application/limitations/)
- [Shotstack Templates + merge fields](https://shotstack.io/docs/guide/architecting-an-application/templates/)
- [Trigger.dev media processing — Router+Coordinator, no execution-time limit](https://trigger.dev/docs/guides/use-cases/media-processing)
- [Trigger.dev idempotency — keys, scopes, >10MB payloads to external storage](https://trigger.dev/docs/idempotency)
- [Anthropic Prompt Caching — 0.1× read, 1h GA, byte-match prefix, 4 breakpoints](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Anthropic release notes — Opus 4.8 1,024-token min, structured outputs, Batches 50%](https://platform.claude.com/docs/en/release-notes/overview)
