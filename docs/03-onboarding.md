# 03 — Onboarding & Activation

> **Section owner:** Growth + iOS
> **Status:** Build spec (v1)
> **Canonical product:** Marque — *the calm app that turns overwhelmed creators into consistent ones.*
> **Sibling docs referenced:** `01-information-architecture.md`, `02-design-system.md`, `06-brand-graph.md`, `08-format-virality.md`, `08-format-virality.md`, `07-ai-system.md`, `05-screens-produce.md`, `09-video-pipeline.md`, `10-social-publishing.md`, `11-monetization.md`, `12-backend-data-security.md`, `15-infra-observability-testing.md`.

---

## 0. The one-sentence thesis

> **Onboarding is not a precursor to conversion — it *is* the conversion engine.** Marque gives away its single most expensive, hardest-to-fake artifact (three viral scripts written in the creator's *own* voice) inside the first session, *before* account friction and *before* the paywall, then gates the act of turning those scripts into posted clips.

> **One hard gate sits in front of the wow, by law.** The Brand Audit is the first thing that ships the creator's page content to third-party AI (Anthropic, AssemblyAI). Apple Guideline **5.1.2(i)** (effective Nov 13 2025) requires explicit, named-provider consent **before any such call** — including the anonymous-session audit. So the *only* thing that precedes the wow is the **named-provider AI-data-sharing consent gate** (step 3). It is captured under the anonymous session, before account creation, because a Supabase anonymous session is already a real `auth.users` row that the `ai_consent` record can key to. Consent is not "handled later" — it is on the critical path, in front of the first AI call. See `14-appstore-compliance-legal.md → §14.3`, `06-brand-graph.md → §8.4`, `17-roadmap-milestones.md → M1`.

This is a **value-first gate**. RevenueCat's 2026 data shows ~⅓ of all subscription conversions happen on **Day 0** and 60%+ within 7 days — so the moments inside this document directly produce the revenue ([RevenueCat, State of Subscription Apps 2026](https://www.revenuecat.com/state-of-subscription-apps-2026-education/)). The strongest trial-start *intent* (31–38%) comes when the paywall fires **after the first value moment**, not on cold open ([ASOHack, 7-day trial paywall data 2025](https://asohack.com/blog/7-day-trial-paywall-conversion-data-2025)).

### The AHA, named precisely

> **AHA = "I just read three videos I would actually post, and they sound like me — and I didn't write them."**

- **North-star activation event:** `aha_reached` = *viewed ≥3 generated scripts attributed to the user's own voice profile.*
- **Time-to-AHA target:** **< 90s** from first launch to first script visible; **< 60s** to first meaningful action (page paste) per the [Dots onboarding benchmark](https://userpilot.com/blog/mobile-app-onboarding/).

Everything below serves getting a real human to `aha_reached` as fast and as calmly as the locked aesthetic allows, then converting them while the feeling is warm.

---

## 1. Best-practice research → Marque principles

| Principle (sourced) | What it means for Marque |
|---|---|
| **Value before account.** Don't force signup at open; let users *experience* worth first ([Userpilot](https://userpilot.com/blog/mobile-app-onboarding/), [NextNative](https://nextnative.dev/blog/mobile-onboarding-best-practices)). | Account creation is placed **after** the 3 scripts, never before. The whole audit + script generation runs under an **anonymous Supabase session**. |
| **Consent before any third-party-AI call — by law, not by courtesy.** Apple 5.1.2(i) (Nov 13 2025) requires named-provider consent *before* personal data reaches a third-party AI ([Apple Developer news](https://developer.apple.com/news/?id=ey6d8onl), [TechCrunch](https://techcrunch.com/2025/11/13/apples-new-app-review-guidelines-clamp-down-on-apps-sharing-personal-data-with-third-party-ai/)). | The named-provider AI-data-sharing consent gate is **step 3**, *before* page-connect/audit — the audit is the first AI call, even under the anon session. It is the one hard gate in front of the wow. See `14-appstore-compliance-legal.md → §14.3`. |
| **≤2 questions before value.** More than two survey questions pre-value drives drop-off ([PostHog onboarding flags guidance](https://posthog.com/docs/feature-flags)). | Exactly **one** framing question (niche) precedes the page-connect. Cadence/format are inferred, not asked. |
| **One AHA, found and protected.** Identify the single moment of realized value and route the whole flow at it ([NextNative](https://nextnative.dev/blog/mobile-onboarding-best-practices)). | The flow has exactly one AHA (3 scripts). No competing "wow." |
| **Progressive profiling, not front-loaded forms.** Spread enrichment across the flow and across sessions ([Appcues](https://www.appcues.com/blog/essential-guide-mobile-user-onboarding-ui-ux)). | The page-paste is the profiling jackpot: one input yields dozens of Brand Graph fields. See `06-brand-graph.md`. |
| **Prime permissions; never burn the one-shot.** A custom pre-prompt precedes every OS dialog; a "Not now" on *our* screen does **not** fire the OS prompt ([AdoptKit](https://www.adoptkit.com/posts/mobile-app-onboarding-best-practices), [appofweb](https://appofweb.com/blog/best-practices-for-accessing-and-handling-user-permissions-for-ios-apps)). | Permissions are deferred out of onboarding entirely and primed contextually at Record/Publish time. |
| **Paywall after value; hard-ish gate for demonstrable value props.** Hard paywalls convert ~5× freemium (10.7% vs 2.1% D35) with near-identical year-one retention ([RevenueCat 2026](https://www.revenuecat.com/state-of-subscription-apps-2026-education/), [RevenueCat hard vs soft](https://www.revenuecat.com/blog/growth/hard-paywall-vs-soft-paywall/)). | Scripts are the free taste; recording → editing → publishing the batch is gated. A/B hard-vs-soft against it. |
| **Social proof immediately before the ask.** "personalization → social proof → value demo → paywall" lifts trial starts 15–25% ([ASOHack](https://asohack.com/blog/7-day-trial-paywall-conversion-data-2025)). | One quiet outcomes/ratings screen sits between AHA and paywall. |
| **Find the specific drop-off step and deep-link back to it.** Don't restart from screen 1 ([Userpilot](https://userpilot.com/blog/mobile-app-onboarding/)). | Resume deep-links to the last incomplete `onboarding_step`. |

---

## 2. Aesthetic & anti-clutter doctrine for onboarding

The onboarding inherits the locked aesthetic from `02-design-system.md` and applies the **anti-clutter doctrine** with unusual strictness, because onboarding is where clutter most tempts a team.

- **One idea per screen.** Every onboarding screen carries a single serif directive and, at most, one input. No progress bars with 9 segments, no feature carousels, no "5 reasons" lists.
- **Warm cream (`#F4F1EA`) / near-black (`#0E0E10`)** surfaces, never pure white/black. Subtle paper texture at ≤4% opacity.
- **Serif display** (Playfair / Tiempos) for the directive line; **grotesque** (Inter / Söhne / Matter) for body, chips, and buttons.
- **Single warm gold accent (`#C9A227`)**, used only on the primary affordance and the streak glyph. The streak glyph is **absent until the user has earned a streak** — it does not appear during onboarding.
- **Slow eased "breathing" motion.** Async waits (audit, script generation) render as a calm breathing animation, **never a spinner-of-doom**. See §6 (States) and `02-design-system.md → Motion`.
- **Quiet, declarative, slightly philosophical copy.** "What do you want to be known for?" — not "Let's set up your account!"

> **Rule:** onboarding screens never preview the cluttered version of a feature. They show the *feeling* of the outcome (a script you'd post), then get out of the way.

---

## 3. The flow, screen by screen

Canonical step ordering. Each step persists `onboarding_step` to the anonymous session (see §7, §8) so any abandon is resumable.

| # | `onboarding_step` | Screen | One directive | Primary action | Brand Graph write | Account? | Gate |
|---|---|---|---|---|---|---|---|
| 1 | `cold_open` | Brand moment | "What do you want to be known for?" | Tap *Begin* | — | No | — |
| 2 | `niche` | One-question framing | "What do you make content about?" | Pick niche chip(s) | `niche`, `topics[]` | No | — |
| 3 | `ai_consent` | AI-data-sharing consent (**hard gate**) | "Marque works with a few specialist services." | Tap *Allow & continue* | — | No (anon row) | **HARD — 5.1.2(i); blocks the first AI call** |
| 4 | `page_connect` | Page-connect | "Paste your @handle or page link." | Paste handle/URL | (triggers audit) | No | requires step 3 granted |
| 5 | `brand_audit` | Instant Brand Audit (wow) | "Here's what you're known for." | Tap *Write my scripts* | full audit → Brand Graph | No | requires step 3 granted |
| 6 | `scripts` | 3 scripts reader (**AHA**) | one script per screen | Swipe through 3; tap *Save these* | `format_affinity` (inferred from taps) | **Prompt at exit** | — |
| 7 | `account` | Account creation | "Keep these. Keep going." | Sign in with Apple / email | links anon → user | **Yes** | — |
| 8 | `social_proof` | Value recap + proof | "Creators ship 4× more with Marque." | Tap *Continue* | — | — | — |
| 9 | `paywall` | Paywall | "Film once. Post all week." | Start 7-day trial | — | — | **Hard-ish** |
| 10 | `record_handoff` | First Record handoff | "Let's film your batch." | Tap *Start recording* | — | — | Camera/mic primed here |

**The AI-data-sharing consent gate (step 3) is the one hard gate in front of the wow.** It is **not** a deferred permission and **not** in the same bucket as camera/mic/notifications/photos (which stay deferred — §5). Per Apple 5.1.2(i), no creator page content may reach Anthropic or AssemblyAI until a `granted = true` `ai_consent` row exists — and the Brand Audit (step 5) *is* exactly such a call, fired under the anonymous session. So consent **must** precede page-connect. It is captured under the anon `auth.users` id and carried forward on account link (§7). Spec + schema: `14-appstore-compliance-legal.md → §14.3.2`; precedence rule: `06-brand-graph.md → §8.4`, `17-roadmap-milestones.md → M1` ("the consent screen blocks the *first* ingestion call, so it is on the path").

**OS permissions** are a separate concern. **Notifications** are primed **after the first genuine win** (first batch generated or first script saved), never in steps 1–10. **Photos** is primed only at "Upload existing video" on Record (repurpose-in). **Camera/mic** are primed at Record (step 10 → Record). **Publish OAuth (Instagram/TikTok via Ayrshare)** is *not* in onboarding at all — it's contextual at first publish (see §9 and `10-social-publishing.md`).

### 3.1 Step 1 — `cold_open` (Brand moment)

- **Visual:** full cream surface, single centered serif line. No nav chrome, no logo wordmark fighting for attention. A single gold *Begin* affordance fades in after the line settles (≈600ms breathing ease).
- **Copy:** "What do you want to be known for?" Subtext (grotesque, muted): "Two minutes. No account yet."
- **Component:** `BrandMomentView`.
- **Telemetry:** `onboarding_started`.

### 3.2 Step 2 — `niche` (one-question framing — first Brand Graph write)

- **Input:** a calm grid of niche chips (e.g. *Fitness, Money, Tech, Beauty, Founder, Food, Mindset, Real Estate, Comedy, Education*) + a free-text "Something else" field. Multi-select up to 3, but one is enough to proceed.
- **Why one question:** ≤2 pre-value questions is the drop-off threshold ([PostHog](https://posthog.com/docs/feature-flags)). We spend our single question on the highest-signal datum.
- **Writes:** `brand_graph.niche`, `brand_graph.topics[]` (see `06-brand-graph.md` schema).
- **Component:** `NicheChipGrid`.
- **Telemetry:** `niche_selected { niche, count }`.

### 3.3 Step 3 — `ai_consent` (named-provider AI-data-sharing consent — the one hard gate)

This screen exists because the **very next step ships the creator's page content to third-party AI**. Apple Guideline **5.1.2(i)** (effective Nov 13 2025) requires explicit, named-provider consent *before* personal data is sent to a third-party AI; the Brand Audit (step 5) is precisely such a call, and it fires **under the anonymous session**, so consent cannot wait for account creation ([Apple Developer news](https://developer.apple.com/news/?id=ey6d8onl), [TechCrunch](https://techcrunch.com/2025/11/13/apples-new-app-review-guidelines-clamp-down-on-apps-sharing-personal-data-with-third-party-ai/)). This is a **hard gate**, not a primed permission: page-connect and audit are unreachable until it is granted. Canonical placement + schema + acceptance criteria live in `14-appstore-compliance-legal.md → §14.3.2`; this section specifies the onboarding-surface behavior only.

- **Visual (one idea per screen, anti-clutter):** a single serif directive — *"Marque works with a few specialist services."* Below it, a short, scannable grotesque list of the **named providers reached during onboarding and their one-line purpose** — *Anthropic (Claude): writes your scripts and reads your voice; AssemblyAI: transcribes your videos.* A quiet "Read exactly what's shared" link opens the full data-flow detail **one layer deep** (the complete processor list — Anthropic, AssemblyAI, the MCP clip engine, Shotstack, Ayrshare/Phyllo — is disclosed there and in the Privacy Policy, even though only Anthropic + AssemblyAI are exercised during onboarding). No wall of legalese on the primary screen.
- **CTA:** gold **"Allow & continue"** (the only gold affordance on screen). Quiet secondary **"Not now."**
- **"Not now" behavior:** does **not** dead-end the user and does **not** crash the flow. It blocks only the page-audit path and routes to the **guided voice mini-interview** fallback (§6) — which produces 3 scripts **without** sending page content to third-party AI… *except* that script generation itself is an Anthropic call. Therefore the interview path still requires Anthropic consent to write scripts; what "Not now" actually means here is **"don't ingest my page,"** and we present a **narrowed consent** for the interview path: *"To write scripts in your words, your answers are sent to Anthropic (Claude)."* If the user declines **all** AI processing, onboarding cannot produce scripts — we land them in a calm explanatory state ("Marque needs to send your words to Claude to write scripts — you can enable this anytime") rather than a broken screen. This keeps us compliant with 5.1.2(i) *and* with 5.1.1(ii)/4.8 (no paid feature is gated on consent — the gate is purely the AI processing the user explicitly declined).
- **Consent is logged under the anonymous identity.** On tap, write one `ai_consent` row **per provider** consented (`provider ∈ {anthropic, assemblyai}` for the onboarding scope), keyed to the **anonymous `auth.users` id** (a Supabase anonymous session is a real auth user, so the FK in `ai_consent.user_id` resolves pre-signup). Persist the exact `consent_copy` string shown, `app_version`, and `created_at` for audit. On account link (§7), these rows carry forward with the identity — no re-consent on sign-in. See `14-appstore-compliance-legal.md → §14.3.2` AC-1..AC-4 and `12-backend-data-security.md`.
- **Enforcement (server-side, not just UI):** the FastAPI Brand-Audit and script-generation endpoints **refuse to call Anthropic/AssemblyAI** unless a `granted = true` row exists for the calling (anon or permanent) identity and provider — the UI gate is a convenience, the server check is the control (`14-appstore-compliance-legal.md` AC-1, `07-ai-system.md`, `12-backend-data-security.md`).
- **Component:** `AIConsentGateView`.
- **Telemetry:** `ai_consent_viewed`, `ai_consent_granted { providers[] }`, `ai_consent_declined { scope }`.

### 3.4 Step 4 — `page_connect` (the highest-leverage screen)

- **Precondition (hard):** reachable **only** after the step-3 `ai_consent` gate is granted. If a user deep-links or resumes here without consent, route them back to step 3 first — page-connect triggers the audit, which is a third-party-AI call.
- **Input:** one text field — "Paste your @handle or page link." Accepts IG handle, IG URL, TikTok handle/URL.
- **Critical architectural call:** this uses **read-only public-profile ingestion** — **no OAuth**. The wow must be *instant and permissionless* (of social OAuth) — note this is orthogonal to the AI-data-sharing consent, which *is* required and was captured in step 3. OAuth (Instagram Graph / TikTok Content Posting via Ayrshare) is deferred to first-publish. See §9.
- **On submit:** kick off the **Brand Audit job** asynchronously (FastAPI + Trigger.dev; see `01-information-architecture.md`, `08-format-virality.md`). The job:
  1. Resolves + scrapes public profile signals (recent captions, cadence, themes, engagement shape).
  2. **Haiku 4.5** bulk-classifies posts (theme, format, language) — `07-ai-system.md`.
  3. **Opus 4.8** synthesizes the audit (voice descriptors, top themes, cadence read, "what you're known for") and seeds the Brand Graph — `06-brand-graph.md`, `07-ai-system.md`.
- **Skip affordance (always present):** "I'd rather describe my voice" → routes to the **guided voice mini-interview** fallback (§6, edge cases), which still produces 3 scripts. No user is dead-ended.
- **Component:** `PageConnectView`.
- **Telemetry:** `page_connect_submitted { platform }`, `page_connect_skipped`.

### 3.5 Step 5 — `brand_audit` (instant Brand Audit — the wow)

- **Visual:** the audit resolves into a single calm card stack — "what you're known for," 3 top-performing themes, a voice read (3–5 descriptors), a cadence line. One idea, big whitespace; the user scrolls one card at a time.
- **Async state:** while the job runs, render the **breathing wait** with rotating, *honest* status lines ("Reading your last posts…", "Listening for your voice…", "Finding what lands…"). Target p50 audit < 8s; the breathing UI must tolerate up to 60s and survive backgrounding (§6, §8).
- **Single CTA:** *Write my scripts in my voice.*
- **Consent precondition already satisfied:** the audit job (and the script generation it leads to) only runs because the step-3 `ai_consent` row exists; no in-flow re-prompt is shown.
- **Component:** `BrandAuditView`.
- **Telemetry:** `brand_audit_viewed { theme_count, voice_descriptor_count, latency_ms }`.

### 3.6 Step 6 — `scripts` (the AHA — 3 scripts in their voice)

- **Visual:** a **calm one-script-per-screen reader**, swipe horizontally between the three. Serif title (the hook), grotesque body (the script). Each script is tagged with its **format** (from `08-format-virality.md`) shown as a single quiet chip ("Myth-buster", "Before/After", "3-up talking heads").
- **Generation:** **Opus 4.8 with prompt caching** — the Brand Graph + voice profile is the **cached prefix**; only the format/topic varies per script. Big latency + cost win at three calls. See `07-ai-system.md → Prompt caching` and `08-format-virality.md`.
- **Hook Lab (progressive disclosure):** tapping the hook line expands an inline **Hook Lab** drawer with 2–3 alternate hooks and a one-line "why this hooks" rationale. Collapsed by default — it must not clutter the read. Section-8 feature #3; full spec in `08-format-virality.md`.
- **Inferred profiling:** which script the user lingers on / taps / saves writes `brand_graph.format_affinity` — *profiling without asking*.
- **Exit / save:** when the user finishes the third script or taps *Save these*, surface the **account prompt** (step 7). Saving is the natural reason to create an account ("keep these").
- **Component:** `ScriptReaderView`, `HookLabDrawer`.
- **Telemetry (critical):** `script_viewed { index, format }` for each; `aha_reached` fires when `distinct(script_viewed.index) >= 3`. Also `hooklab_opened`, `script_saved`.

### 3.7 Step 7 — `account` (creation — *after* the wow)

- **Trigger:** "Keep these. Keep going." The user has already felt the value; signup is now self-justifying (gradual engagement — [Userpilot](https://userpilot.com/blog/mobile-app-onboarding/)).
- **Methods (locked):** **Sign in with Apple** (native sheet) + **email magic-link / OTP** via Supabase Auth. We deliberately **omit Google** to dodge Guideline 4.8 friction — if any third-party social login is offered, Apple mandates a Sign-in-with-Apple equivalent ([App Store Review Guidelines 4.8](https://developer.apple.com/app-store/review/guidelines/)). Apple SiwA + email is the lowest-friction fully-compliant combo for an iOS-only app.
- **iOS-only SiwA via Supabase** needs only Apple enabled as a provider + the bundle id in Client IDs; the native flow sends the `idToken` straight to Supabase (validated against Apple's public keys). **No Services ID / .p8 / Secret Key** until web or Android is added ([SiwA + Supabase](https://dev.to/gautier/apple-sign-in-with-flutter-and-supabase-40lp)).
- **Anonymous → permanent linking:** the anon session (and its Brand Graph + 3 scripts **and its `ai_consent` rows**) is **linked** to the new identity, never discarded. Because consent was logged under the anon `auth.users` id, **no re-consent prompt fires on sign-in** — the grant simply carries forward. See §7, §8, `12-backend-data-security.md`, `14-appstore-compliance-legal.md → §14.3.2`.
- **Account deletion** must be reachable in-app (Guideline 5.1.1(v)) — implemented in `Settings`, not here, but onboarding must not create undeletable state.
- **Component:** `AccountGateView`.
- **Telemetry:** `account_created { method }`, `anon_linked { anon_session_id }`.

### 3.8 Step 8 — `social_proof` (value recap + proof)

- **Visual:** one quiet screen. A single outcome stat and/or a small ratings line, in the calm voice — "Creators ship 4× more with Marque." (Use only claims we can substantiate; see Open questions.)
- **Why here:** the "personalization → social proof → value demo → paywall" sequence lifts trial starts 15–25% ([ASOHack](https://asohack.com/blog/7-day-trial-paywall-conversion-data-2025)).
- **A/B:** this screen is feature-flagged on/off (§10, experiment E4).
- **Component:** `SocialProofView`.

### 3.9 Step 9 — `paywall` (after value)

- **Stack:** **StoreKit 2 + RevenueCat**, rendered via **RevenueCat remote Paywalls + Offerings** so copy, trial length, and gate strength are server-configurable for A/B without a binary release ([RevenueCat paywalls guide](https://www.revenuecat.com/blog/growth/guide-to-mobile-paywalls-subscription-apps/)). **Apple IAP only.** Stripe is reserved for a future web surface — *never* the iOS paywall. Full spec: `11-monetization.md`.
- **Placement:** immediately after the AHA + social proof — the data-backed highest-intent slot ([ASOHack](https://asohack.com/blog/7-day-trial-paywall-conversion-data-2025), [RevenueCat 2026](https://www.revenuecat.com/state-of-subscription-apps-2026-education/)).
- **Gate strength (default = hard-ish):** the 3 scripts are the free taste; **recording, editing, and publishing the batch are gated.** Hard paywalls convert ~5× freemium with near-identical year-one retention ([RevenueCat hard vs soft](https://www.revenuecat.com/blog/growth/hard-paywall-vs-soft-paywall/)). Default to hard-ish and **A/B against a soft gate** (E1).
- **Trial:** **7-day** default — the 2025/2026 data-backed length; top-quartile apps with >4-day trials convert >60% trial-to-paid ([ASOHack](https://asohack.com/blog/7-day-trial-paywall-conversion-data-2025)).
- **Trial reminder push (Day 5–6):** drives the documented Day 4–7 conversion spike — *requires* notification permission, which is why we prime push at the first win (§5).
- **Compliance:** no app functionality may be gated on enabling push/location/tracking ([App Store Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)); the gate is purely on the subscription, and onboarding completes fully even if push is declined. (Distinct from the **5.1.2(i)** AI-data-sharing gate at step 3 — that one *is* required before the audit; see `14-appstore-compliance-legal.md → §14.3`.)
- **Component:** RevenueCat `PaywallView` host (`MarquePaywallHost`).
- **Telemetry:** `paywall_viewed { offering_id, gate_variant }`, `trial_started`, `paywall_dismissed`.

### 3.10 Step 10 — `record_handoff`

- **Visual:** "Film once. Post all week." → *Start recording.* This hands off to the **hero batch loop** (`05-screens-produce.md`). It is the first place camera + mic are requested — primed here, fired in Record. See §5.

---

## 4. Progressive profiling → Brand Graph

Onboarding is the **primary write path** into the Brand Graph (`06-brand-graph.md`). The doctrine: *every datum is earned by value delivered, never extracted by a form.*

| Source in flow | Brand Graph node(s) written | How acquired |
|---|---|---|
| `niche` chips | `niche`, `topics[]` | 1 question, pre-value |
| **page-paste** | `voice_profile`, `themes[]`, `cadence`, `audience_read`, `platform_handles[]` | **one paste → dozens of fields** (the jackpot) |
| script interactions | `format_affinity[]` | inferred from taps/lingers — *no question* |
| (later, in-app) | `posting_cadence_pref`, `goals` | asked contextually in later sessions |

**Rules**
- **Do** make the page-paste do the heavy lifting (the progressive-profiling jackpot).
- **Don't** front-load a multi-field profile form — "ask everything at once" is the #1 abandonment driver ([Appcues](https://www.appcues.com/blog/essential-guide-mobile-user-onboarding-ui-ux)).
- **Persist server-side** keyed to the **anonymous session id** so a pre-signup abandon is resumable.
- **Re-confirm, don't re-ask:** later sessions *show* what Marque inferred and let the user nudge it, rather than re-interrogating.

```text
# Brand Graph onboarding write contract (see 12-backend-data-security.md for full schema)
brand_graph (per identity OR anon_session_id)
  ├─ niche: text                      # step 2
  ├─ topics: text[]                   # step 2
  ├─ platform_handles: jsonb          # step 3   [{platform, handle, url}]
  ├─ voice_profile: jsonb             # step 4   {descriptors[], reading_level, signature_phrases[]}
  ├─ themes: jsonb                    # step 4   [{theme, weight, sample_post_ids[]}]
  ├─ cadence: jsonb                   # step 4   {posts_per_week, best_dayparts[]}
  ├─ audience_read: jsonb             # step 4
  ├─ format_affinity: jsonb           # step 5   [{format_id, signal_weight}]
  ├─ source: enum(page_ingest | voice_interview)
  └─ confidence: numeric              # lowers when produced by the interview fallback
```

---

## 5. Permission priming (exact iOS rules)

> **Scope:** this section covers **OS permissions** (camera, mic, notifications, photos), all of which are deferred and primed contextually. The **AI-data-sharing consent gate** is *not* an OS permission and is *not* deferred — it is a hard in-app gate at **step 3**, before the first AI call, per Apple 5.1.2(i) (§3.3, `14-appstore-compliance-legal.md → §14.3.2`). Do not conflate the two: a deferred OS permission can be declined and onboarding still completes; the AI-consent gate must be granted before the audit can run at all.

> **Golden rule:** a **custom pre-prompt always precedes the OS dialog.** If the user taps "Not now" on *our* screen, we **do not fire** the system prompt — preserving the ability to ask again later. iOS shows each system prompt **once**; after `.denied`, recovery needs Settings and <5% of users do it ([AdoptKit](https://www.adoptkit.com/posts/mobile-app-onboarding-best-practices), [appofweb](https://appofweb.com/blog/best-practices-for-accessing-and-handling-user-permissions-for-ios-apps)).

### 5.1 Right-time table (mapped to Marque)

| Permission | Pre-prompt copy | Fired when | API | Info.plist key (required) |
|---|---|---|---|---|
| **Camera** | "To film your batch, Marque needs your camera." | User taps *Start recording* (step 10 → Record) | `AVCaptureDevice.requestAccess(for: .video)` | `NSCameraUsageDescription` |
| **Microphone** | "Now your mic, so we capture your voice." | **Audio capture actually begins** — a separate beat after camera | `AVCaptureDevice.requestAccess(for: .audio)` | `NSMicrophoneUsageDescription` |
| **Notifications** | "We'll nudge you when your batch is ready to post." | **After the first genuine win** (first batch generated / first script saved) | `UNUserNotificationCenter.requestAuthorization(options:)` | — |
| **Photos** | "Pick an existing video to repurpose." | User taps *Upload existing video* (repurpose-in, Record) | `PHPhotoLibrary` / picker | `NSPhotoLibraryUsageDescription`, `NSPhotoLibraryAddUsageDescription` |

### 5.2 Hard iOS rules (must implement exactly)

- **Camera ≠ mic — two separate permissions, two separate beats.** Granting `.video` does **not** grant `.audio`. Never request both in the same moment; ask camera at *Record*, mic only when audio capture begins. Fewer simultaneous prompts = higher accept ([appofweb](https://appofweb.com/blog/best-practices-for-accessing-and-handling-user-permissions-for-ios-apps), [Apple AVFoundation](https://developer.apple.com/documentation/avfoundation/avcapturedevice/requestaccess(for:completionhandler:))).
- **Usage strings are mandatory or the app crashes.** Calling `requestAccess` without the matching Info.plist usage string **raises an exception / terminates** the app ([Apple AVFoundation: requesting authorization](https://developer.apple.com/documentation/avfoundation/requesting-authorization-to-capture-and-save-media)).
- **Always check `authorizationStatus(for:)` before requesting.** Branch on `.notDetermined` (show pre-prompt → request), `.denied` / `.restricted` (explanation + deep-link to Settings + fallback), `.authorized` (proceed).
- **Settings deep-link:** `UIApplication.shared.open(URL(string: UIApplication.openSettingsURLString)!)` for recovery.
- **Notifications — strongly consider provisional authorization** (`.provisional`, iOS 12+): notifications are delivered **quietly to Notification Center with no prompt** ("show, don't tell"); the user upgrades to full later. Keeps the Day-5/6 trial reminder deliverable without spending the explicit ask early ([Apple UserNotifications](https://developer.apple.com/documentation/usernotifications/asking-permission-to-use-notifications)). A/B provisional vs explicit (E6).
- **App Store rule 5.1.2(i):** you may **not** gate app functionality, content, or compensation on the user enabling push/location/tracking — onboarding must complete fully if every permission is declined ([Guidelines](https://developer.apple.com/app-store/review/guidelines/)).

### 5.3 Permission state machine (per permission)

```text
authorizationStatus(for:)
  .notDetermined ─► show MarquePrePrompt
        ├─ user taps "Not now" ─► DO NOT call requestAccess; mark soft_declined; re-offer later, contextually
        └─ user taps "Allow"   ─► requestAccess(for:)
                 ├─ granted  ─► proceed (start camera / mic / schedule push)
                 └─ denied   ─► .denied path
  .denied / .restricted ─► explanation card + "Open Settings" deep-link + FALLBACK
        # Camera fallback: none (recording impossible) → guide to Settings
        # Mic fallback:    none (talking-head impossible) → guide to Settings
        # Photos fallback: keep camera-record path; hide repurpose-in
        # Notif fallback:  proceed silently; trial reminder via in-app banner instead of push
  .authorized / .provisional ─► proceed
```

---

## 6. States (every onboarding screen)

The async, network-dependent steps (`ai_consent` → `page_connect` → `brand_audit` → `scripts`) carry the heaviest state burden because the Brand Audit and script generation are **Trigger.dev jobs that can outlive the screen** (`01-information-architecture.md`) — and because every one of them is gated on the step-3 consent.

| State | `ai_consent` | `page_connect` / `brand_audit` | `scripts` | `paywall` | permission pre-prompts |
|---|---|---|---|---|---|
| **Loading** | Provider list + copy fetched from remote config → skeleton lines (never a half-rendered consent screen). | **Breathing** progress with honest rotating lines; no spinner. Tolerate ≤60s; survive backgrounding (§8). | Breathing while Opus generates (cached prefix → fast). Stream scripts in as ready. | RevenueCat paywall loading skeleton; retry if Offerings fail to fetch. | n/a |
| **Empty** | n/a (copy is config-driven; if config is empty, fail safe → block AI, do not silently proceed). | Handle resolves but **no usable signal** (private acct / <N posts) → fall back to **guided voice mini-interview** (still produces 3 scripts; still requires Anthropic consent). | Should never be empty — interview fallback guarantees 3. | Offering empty → fall back to a hardcoded default Offering id. | n/a |
| **Error** | Cannot load consent config → **fail safe by blocking AI features**, calm "Let's try that again" retry; never silently proceed without consent (`14-appstore-compliance-legal.md` §14.3.2 states). | Scrape / Haiku / Opus / Shotstack failure → calm "Let's try that again" + retry; never a stack trace. Job errors surfaced via Sentry. | Generation error → retry single failed script (others already shown). | Purchase error → StoreKit error mapped to calm copy; restore-purchases always available. | If `requestAccess` throws (missing usage string) it's a build bug — caught in CI, never shipped. |
| **Offline** | Cannot write the consent row → "Connect to the internet to continue"; do **not** run the audit on a cached/assumed grant. | Queue the audit request; show "We'll pick this up when you're back online"; cache last state via RQ-style persistence (`01-information-architecture.md`). | Cached scripts remain readable offline. | Block purchase; "Connect to start your trial." | Defer the ask until back online if it depends on a network action. |
| **Permission-denied / declined** | User taps "Not now" → narrowed consent path or calm "enable anytime" state (§3.3); page-audit stays blocked; **no AI call fires**. | n/a (no OS permissions here; the AI-consent gate is upstream at step 3). | n/a | n/a | Explanation + Settings deep-link + fallback (§5.3). |

### 6.1 Page-connect edge cases (explicit handling)

| Case | Behavior |
|---|---|
| **AI-data-sharing consent declined (step 3)** | "Not now" on the page-audit scope → route to the **voice mini-interview** (no page ingested), presenting the **narrowed Anthropic consent** needed to write scripts. If the user declines **all** AI processing, land in a calm "Marque needs to send your words to Claude to write scripts — enable anytime" state; onboarding cannot produce scripts but is not broken, no paid feature is gated on the refusal (5.1.1(ii)/4.8), and no AI call fires (5.1.2(i)). See §3.3. |
| **Private IG/TikTok account** | No public signal → guided **voice mini-interview** (3–4 calm questions) → still ship 3 scripts; `brand_graph.source = voice_interview`, lower `confidence`. (Still requires Anthropic consent from step 3 to generate.) |
| **Brand-new account, <N posts** | Same interview fallback; optionally blend the few posts that exist. |
| **Handle not found** | Inline calm error + "Check the handle, or describe your voice instead." Never a hard dead-end. |
| **Ambiguous handle** (matches IG + TikTok) | Disambiguation chip: "Which one?" |
| **Non-English content** | Haiku 4.5 language-detect + classify in-language; Opus generates scripts in the creator's language (`07-ai-system.md`). |
| **Job outlives the screen** | Background the Trigger.dev job; if the user navigates away or backgrounds the app, **notify on completion** ("Your Brand Audit is ready") and deep-link back. Never block the UI thread on a 20–60s job. |

---

## 7. Skip / resume / finish-later

**Anonymous-first is the enabling architecture.** The Brand Graph + 3 scripts are generated under an **anonymous Supabase session**; on sign-in they are **linked** to the permanent identity (Supabase anonymous → permanent linking — see `12-backend-data-security.md`). This makes true finish-later possible without losing the wow.

```text
onboarding_session            # 12-backend-data-security.md
  ├─ id (uuid)
  ├─ anon_session_id (uuid)    # Supabase anonymous user id
  ├─ user_id (uuid, nullable)  # set on account link
  ├─ onboarding_step (enum)    # last completed step → resume target
  ├─ ai_consent_granted (bool) # mirror of an ai_consent granted row; gate for page_connect/audit
  ├─ brand_graph_id (fk)
  ├─ scripts_generated (bool)
  ├─ paywall_seen (bool)
  ├─ trial_started (bool)
  ├─ created_at / updated_at
```

**Resume rules**
- **Never restart from screen 1.** Deep-link to the screen *after* the last completed `onboarding_step` (the Vertigo session-recording lesson — find and fix the specific drop-off step, [Userpilot](https://userpilot.com/blog/mobile-app-onboarding/)).
- **Resume entry points:** (a) APNs push "Your Brand Audit is ready" (when the user bailed during a running job); (b) an in-app **"Finish setting up"** banner on next launch.
- **Consent is a precondition for the audit steps.** Never resume directly into `page_connect`/`brand_audit` without a `granted = true` `ai_consent` row — if it's missing (e.g. a user bailed on step 3), resume *at* step 3 first. The server enforces this regardless of client deep-link (§3.3, `14-appstore-compliance-legal.md` AC-1).
- **Mid-paywall abandon:** resume *at the paywall*, not earlier — the user has already seen the value; re-deriving it wastes their goodwill and our compute.
- **Account already exists** on a returning anon device → offer sign-in and link.

**Skip affordances**
- `page_connect` is skippable → voice interview.
- The `paywall` is dismissible (hard-ish, not a forced wall) → user lands in a value-limited app state where scripts are readable but Record/Publish are gated; the paywall re-presents contextually at the next gated action (`11-monetization.md`).

---

## 8. Async generation contract (onboarding ↔ orchestration)

Onboarding must tolerate the audit/scripts jobs outliving the screen. Contract with the orchestration layer (`01-information-architecture.md`, `08-format-virality.md`, `07-ai-system.md`):

| Job | Trigger | Engine | Latency target (p50 / p95) | Surfacing |
|---|---|---|---|---|
| **Brand Audit** | `page_connect_submitted` | FastAPI → Trigger.dev → Haiku 4.5 (classify) + Opus 4.8 (synthesize) | 8s / 45s | breathing UI; on background → push + deep-link |
| **3 Scripts** | `brand_audit` CTA | Opus 4.8, cached Brand Graph prefix, 3 calls | 6s / 20s | stream in; first script unblocks the reader |

- **Consent precondition (enforced server-side):** neither job may dispatch to Anthropic/AssemblyAI without a `granted = true` `ai_consent` row for the calling identity + provider. The FastAPI entrypoint checks this **before** enqueuing the Trigger.dev job and rejects with a structured "consent_required" error that routes the client back to step 3 (`14-appstore-compliance-legal.md` §14.3.2 AC-1, `06-brand-graph.md` §8.4, `07-ai-system.md`).
- **Prompt caching:** cache the Brand Graph + voice profile as a cached prefix; only format/topic varies → large latency + cost win across the 3 calls (`07-ai-system.md`).
- **Idempotency:** jobs keyed by `(anon_session_id, job_type)` so a resumed session reuses a completed audit rather than re-running it.
- **Backgrounding:** the iOS app subscribes to job completion via Supabase Realtime; if foregrounded, update in place; if backgrounded, deliver an APNs completion push.

---

## 9. What is deliberately *not* in onboarding

> **The Instagram/TikTok publish OAuth is deferred to first-publish — not onboarding.** This is an intentional architectural call that also serves the anti-clutter doctrine: "connect socials" stays a contextual, one-layer-deep step at publish time.

Rationale (full detail in `10-social-publishing.md`):

- **TikTok Content Posting audit gauntlet.** Unaudited clients can only post `SELF_ONLY`/private, ≤5 users / 24h, and must pass a **separate Content Posting audit** (demo video, public privacy policy mentioning TikTok, real signup flow, branded landing page) before public direct-post. Scopes: `video.publish` / `video.upload`, `user.info.basic`; token limited to **6 req/min**; ~15 posts/day/creator cap shared across clients; you **must** query `/v2/post/publish/creator_info/query/` and present the creator's actual `privacy_level_options` (don't hardcode). Redirect must be HTTPS (no localhost) ([TikTok content-sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines), [PostPeer TikTok tutorial](https://www.postpeer.dev/blog/tiktok-direct-posting-api-tutorial)).
- **Going through Ayrshare sidesteps Marque's own TikTok audit** for v1 — strongly recommended.
- **Instagram:** prefer **Instagram API with Instagram Login** (direct, **no Facebook Page required**) over Facebook-Login-for-Business; scopes `instagram_business_basic`, `instagram_business_content_publish`; account must be **Professional (Business/Creator)**; short-lived business-login tokens last 1h → exchange for 60-day long-lived ([IG API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/)).
- **Ayrshare Publisher adapter:** "connect to publish" = a **white-labeled JWT social-linking page** (`/profiles/create-profile` → `/profiles/generateJWT`; **JWT URL valid 5 min**; opened in a webview/new tab; `allowedSocial: ["instagram","tiktok"]`), one Ayrshare User Profile per Marque user, Profile Key stored in Supabase, **Direct Instagram Login** enabled ([Ayrshare user integration / generate-JWT](https://www.ayrshare.com/docs/multiple-users/user-integration)).

The **read-only public ingestion** used for the Brand Audit (step 5) is entirely separate from this publish OAuth and requires **no** social permissions — that's what makes the wow instant and OAuth-free. It is **not**, however, consent-free: because ingestion sends the creator's page content to third-party AI (Anthropic, AssemblyAI), it is gated on the step-3 **AI-data-sharing consent** (5.1.2(i), §3.3). "No social OAuth" and "AI-data-sharing consent required" are both true and not in tension.

---

## 10. Activation & conversion metrics + A/B hooks

### 10.1 Funnel events (instrumented in PostHog; pipeline crashes in Sentry — `15-infra-observability-testing.md`)

| Event | Definition | Target |
|---|---|---|
| `onboarding_started` | step 1 reached | — |
| `niche_selected` | step 2 complete | >90% of starts |
| `ai_consent_granted` | step 3 — AI-data-sharing consent granted | >88% of niche-complete |
| `page_connect_submitted` | step 4 paste | >70% of starts |
| `brand_audit_viewed` | audit rendered | >85% of submits |
| **`aha_reached`** | **≥3 scripts viewed in own voice** | **>75% of audit views** |
| `account_created` | step 7 | >70% of AHA |
| `paywall_viewed` | step 9 | ~100% of account_created |
| `trial_started` | StoreKit trial begun | 28–38% of paywall views |
| `trial_to_paid` | conversion after trial | >60% (top-quartile band) |
| `first_batch_recorded` | hero loop, post-onboarding | secondary activation |
| `first_clip_published` | first publish | secondary activation |

**Derived metrics:** time-to-AHA (target <90s), per-step drop-off, activation rate, paywall-view rate & frequency, D1/D7/D30 retention, permission grant rates (push grant ~2× higher when deferred — which is why we defer).

### 10.2 A/B test hooks (PostHog feature flags + RevenueCat Offerings)

| Id | Experiment | Variants | Surface | Primary metric |
|---|---|---|---|---|
| **E1** | Paywall gate strength | hard-ish vs soft | RevenueCat Offering | `trial_to_paid`, D35 revenue |
| **E2** | Paywall placement | post-AHA vs post-first-value (after first recorded batch) | flag | `trial_started`, retention |
| **E3** | Trial length | 3-day vs **7-day** vs annual-only | RevenueCat Offering | `trial_to_paid` |
| **E4** | Social-proof screen | on vs off | flag | `trial_started` |
| **E5** | Onboarding questions | 1 vs 2 pre-value questions | flag | `aha_reached`, drop-off |
| **E6** | Notification ask | provisional vs explicit | flag | push grant rate, Day-5/6 reminder reach |

**Reading rules:** minimum **500 impressions/variant** before reading a result ([ASOHack](https://asohack.com/blog/7-day-trial-paywall-conversion-data-2025)). PostHog supports percentage rollouts + cohort/person-property targeting; experiments inherit flag targeting ([PostHog feature flags](https://posthog.com/docs/feature-flags), [A/B testing onboarding with PostHog](https://onboardjs.com/blog/a-b-testing-onboarding-flows-with-onboardjs-and-posthog)). RevenueCat remote Offerings let E1/E3 ship without a binary update ([RevenueCat paywalls guide](https://www.revenuecat.com/blog/growth/guide-to-mobile-paywalls-subscription-apps/)).

---

## 11. Component inventory (SwiftUI, iOS 17+, Observation)

| Component | Step | Responsibility | Key states |
|---|---|---|---|
| `OnboardingCoordinator` | all | Owns `onboarding_step`, routing, anon session, resume deep-linking | — |
| `BrandMomentView` | 1 | cold-open directive | static |
| `NicheChipGrid` | 2 | niche multi-select → Brand Graph write | selected / max-3 |
| `AIConsentGateView` | 3 | named-provider AI-data-sharing consent (5.1.2(i)); writes `ai_consent` rows under anon id; hard-gates the audit | loading / granted / declined / error→fail-safe-block / offline |
| `PageConnectView` | 4 | handle/URL input; kicks Trigger.dev audit; skip→interview; requires step-3 consent | idle / submitting / error |
| `BreathingWaitView` | 4–5, 6 | calm async wait; honest rotating lines; backgrounding-safe | running / timeout-extended |
| `BrandAuditView` | 5 | renders audit card stack | loading / empty→interview / error / offline |
| `VoiceInterviewView` | 4–5 fallback | 3–4 calm questions → still produce 3 scripts | — |
| `ScriptReaderView` | 6 | one-script-per-screen reader; fires `aha_reached` | loading / streaming / ready |
| `HookLabDrawer` | 6 | progressive-disclosure hook alternates | collapsed / expanded |
| `AccountGateView` | 7 | SiwA + email; anon→permanent link (carries `ai_consent` forward) | — |
| `SocialProofView` | 8 | single proof screen (flag-gated) | on / off |
| `MarquePaywallHost` | 9 | RevenueCat remote paywall host | loading / error / restore |
| `MarquePrePrompt` | 10+ | custom permission pre-prompt (camera/mic/notif/photos) | notDetermined / softDeclined / denied |

---

## 12. Acceptance criteria

- [ ] First launch → first script visible in **< 90s** (p50) on a warm network.
- [ ] No account is required before the 3 scripts; the entire audit + generation runs under an **anonymous Supabase session**.
- [ ] **The named-provider AI-data-sharing consent gate (step 3) precedes the page-connect/Brand-Audit step and is captured under the anonymous identity.** No call to Anthropic or AssemblyAI fires — including the anonymous-session audit — until a `granted = true` `ai_consent` row exists for that provider (server-enforced, not UI-only). Agrees with `14-appstore-compliance-legal.md` §14.3.2 AC-1, `06-brand-graph.md` §8.4, `17-roadmap-milestones.md` M1.
- [ ] On account link, the anonymous `ai_consent` rows carry forward; **no re-consent prompt** fires on sign-in.
- [ ] Exactly **one** question precedes the AI-consent gate and page-connect (niche). (The consent gate is a legal gate, not a survey question.)
- [ ] The page-connect uses **read-only public ingestion** — it triggers **no** social OAuth and requests **no** social permissions, but it **does** require the step-3 AI-data-sharing consent (the ingestion sends page content to third-party AI).
- [ ] `aha_reached` fires when, and only when, **≥3 distinct scripts** have been viewed.
- [ ] Account creation offers **Sign in with Apple + email**; no third-party social login is present without a SiwA equivalent (4.8 compliant).
- [ ] On sign-in, the anonymous session's Brand Graph + scripts are **linked**, never lost.
- [ ] The paywall appears **after** the AHA + social-proof screen, uses **StoreKit 2 + RevenueCat**, **Apple IAP only**, default **7-day** trial.
- [ ] Onboarding completes fully even if **every** permission (camera, mic, notifications, photos) is declined (5.1.2(i)).
- [ ] No OS permission dialog fires without a preceding `MarquePrePrompt`; a "Not now" on the pre-prompt does **not** call `requestAccess`.
- [ ] Camera and mic are requested in **separate beats**; all required Info.plist usage strings are present (build fails CI otherwise).
- [ ] Notifications are primed **only after the first genuine win**, never in steps 1–10.
- [ ] Every async screen renders the **breathing** wait (no spinner) and survives app backgrounding, notifying + deep-linking on job completion.
- [ ] Resume deep-links to the step after the last completed `onboarding_step`; it never restarts from screen 1.
- [ ] Private-account / <N-posts / handle-not-found all route to the voice-interview fallback and still ship 3 scripts.
- [ ] All six A/B hooks (E1–E6) are wired to PostHog flags / RevenueCat Offerings and read only at ≥500 impressions/variant.
- [ ] Account deletion is reachable in-app (Settings) for any account created here (4.8 / 5.1.1(v)).

---

## Open questions

1. **Social-proof claim substantiation.** The step-7 stat ("Creators ship 4× more with Marque") needs a defensible source before launch. Until we have real cohort data, do we (a) ship a softer qualitative proof line, (b) use ratings only, or (c) hide the screen and treat E4-off as the launch default? *Requires a decision — do not fabricate a metric.*
2. **`<N posts` threshold.** What is `N` (the minimum public-post count) below which we route to the voice-interview fallback instead of attempting an audit? Suggest 6–10 pending real scrape-quality data.
3. **Voice-interview length.** 3 vs 4 questions for the fallback — trade off completeness of the voice profile vs drop-off. Recommend starting at 3.
4. **Provisional-notification default (E6).** Ship provisional-by-default and A/B explicit, or vice-versa? Provisional protects the Day-5/6 trial reminder but yields softer engagement signals.
5. **Hard-ish gate exact boundary.** Confirm with `11-monetization.md`: scripts readable post-paywall-dismiss, but is *re-generating new scripts* free, or is that gated too? (Recommend: the original 3 stay free; new generation is gated.)
6. **Resume window.** How long does an abandoned anonymous session remain resumable before its Brand Graph is purged (privacy + storage)? Suggest 30 days, aligned with data-retention policy in `12-backend-data-security.md`.
7. **Annual-only trial variant (E3).** Does Finance want an annual-only Offering in the trial-length test, or restrict to monthly with a 7-day trial for v1?

---

## Sources

- [RevenueCat — State of Subscription Apps 2026](https://www.revenuecat.com/state-of-subscription-apps-2026-education/) — Day-0 = ⅓ of conversions, 60%+ within 7 days, hard paywalls ~5× freemium (10.7% vs 2.1% D35), Day 4–7 trial-expiry spike.
- [ASOHack — 7-day trial paywall conversion data 2025](https://asohack.com/blog/7-day-trial-paywall-conversion-data-2025) — paywall-after-first-value highest intent (31–38%), 7-day trial default, 500-impression minimum, Day 5–6 reminder, personalization→proof→demo→paywall lift.
- [RevenueCat — Hard paywall vs soft paywall](https://www.revenuecat.com/blog/growth/hard-paywall-vs-soft-paywall/) — hard/soft trade-offs, near-identical year-one retention.
- [RevenueCat — Guide to mobile paywalls](https://www.revenuecat.com/blog/growth/guide-to-mobile-paywalls-subscription-apps/) — placement types, remote Offerings for A/B without binary update.
- [Apple — Requesting authorization to capture and save media](https://developer.apple.com/documentation/avfoundation/requesting-authorization-to-capture-and-save-media) — required Info.plist keys, terminates without them, check `authorizationStatus` first.
- [Apple — `AVCaptureDevice.requestAccess(for:)`](https://developer.apple.com/documentation/avfoundation/avcapturedevice/requestaccess(for:completionhandler:)) — one-time prompt semantics, camera vs mic, exception without usage string.
- [Apple — Asking permission to use notifications](https://developer.apple.com/documentation/usernotifications/asking-permission-to-use-notifications) — `requestAuthorization(options:)`, provisional "show don't tell."
- [Apple — App Store Review Guidelines](https://developer.apple.com/app-store/review/guidelines/) — 4.8 Login Services (SiwA equivalence), no gating app functionality on permissions, account deletion (5.1.1(v)).
- [Apple Developer news — updated App Review Guidelines (Nov 13 2025)](https://developer.apple.com/news/?id=ey6d8onl) — Guideline 5.1.2(i): explicit, named-provider consent required *before* sharing personal data with third-party AI. The basis for the step-3 consent gate.
- [TechCrunch — Apple clamps down on third-party-AI data sharing](https://techcrunch.com/2025/11/13/apples-new-app-review-guidelines-clamp-down-on-apps-sharing-personal-data-with-third-party-ai/) — plain-English summary of the Nov 2025 rule; confirms consent-before-sharing.
- [Sign in with Apple + Supabase (iOS-only)](https://dev.to/gautier/apple-sign-in-with-flutter-and-supabase-40lp) — bundle id + idToken only; no Services ID/.p8 until web/Android.
- [TikTok — Content sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) — unaudited caps, audit requirements, creator_info privacy levels.
- [PostPeer — TikTok direct posting API tutorial](https://www.postpeer.dev/blog/tiktok-direct-posting-api-tutorial) — HTTPS redirect, audit gauntlet, why an aggregator bypasses it.
- [Meta — Instagram API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/) — no FB Page, `instagram_business_*` scopes, content publishing.
- [Ayrshare — Multiple users / generate JWT](https://www.ayrshare.com/docs/multiple-users/user-integration) — white-label JWT linking page (5-min URL), one Profile per user, `allowedSocial`, Direct Instagram Login.
- [AdoptKit — Mobile app onboarding best practices](https://www.adoptkit.com/posts/mobile-app-onboarding-best-practices) — pre-permission priming preserves the one-shot, <5% recover from denial.
- [Userpilot — Mobile app onboarding](https://userpilot.com/blog/mobile-app-onboarding/) — gradual engagement (no account at open), custom permission modals, deep-link to drop-off step.
- [Appcues — Essential guide to mobile user onboarding](https://www.appcues.com/blog/essential-guide-mobile-user-onboarding-ui-ux) — value in first screens, reduce signup friction, transparency.
- [NextNative — Mobile onboarding best practices](https://nextnative.dev/blog/mobile-onboarding-best-practices) — defer non-critical permissions, single AHA, progressive profiling.
- [appofweb — Handling iOS permissions](https://appofweb.com/blog/best-practices-for-accessing-and-handling-user-permissions-for-ios-apps) — camera≠mic, right-time table, `.denied` handling + Settings deep-link.
- [PostHog — Feature flags](https://posthog.com/docs/feature-flags) — percentage rollouts, cohort/person-property targeting, ≤2-question welcome survey.
- [OnboardJS — A/B testing onboarding flows with PostHog](https://onboardjs.com/blog/a-b-testing-onboarding-flows-with-onboardjs-and-posthog) — experiment patterns inheriting flag targeting, social proof in onboarding.
