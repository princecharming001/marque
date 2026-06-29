# 14 — App Store Compliance, Privacy & Legal

> **Doctrine for this section.** Compliance is not paperwork bolted on at submission — it is a **shippable feature set** with its own data model, UI surfaces, acceptance criteria, and reviewer-facing artifacts. Marque sits at the intersection of *five* high-rejection guideline clusters at once: **1.2 (UGC safety)**, **5.1.2(i) (third-party-AI data disclosure — new Nov 13 2025)**, **3.1.2 (subscription metadata)**, **4.2 / 4.3 (not-a-wrapper / minimum functionality)**, and **5.1.1(v) (in-app account deletion)**. Each requires a demonstrable in-app mechanism *and* a reviewer artifact (demo account, notes, screencast). This document specifies every one.
>
> **Aesthetic constraint.** Compliance surfaces are not exempt from the locked aesthetic (see `02-design-system.md`). Consent gates, the EULA acceptance, the report sheet, the delete flow, the privacy screens — all use warm cream (`#F4F1EA`) / near-black (`#0E0E10`), serif display titles, a single warm-gold (`#C9A227`) accent, huge whitespace, and slow eased motion. A consent gate is *one idea per screen*, written in quiet declarative copy, never a wall of legalese. The legalese lives one layer deep behind a "Read in full" link.
>
> **Cross-references.** AI providers and prompt flows: `07-ai-system.md`. Clip pipeline and render recipes: `09-video-pipeline.md`. Publishing/scheduling and the Publisher/Insights adapters: `10-social-publishing.md`. Subscriptions/paywall mechanics: `11-monetization.md`. Auth, Supabase data model, and storage: `12-backend-data-security.md`. Trend Radar and trending-audio handling: `08-format-virality.md`.

---

## 14.1 Compliance posture at a glance

| Cluster | Guideline | Marque trigger | Shippable mechanism | Reviewer artifact |
|---|---|---|---|---|
| UGC safety | 1.2 / 1.2.1 | Creator-uploaded video + AI-derived clips + AI scripts/visuals | Moderation pipeline, Report on every shareable object & AI output, EULA gate, support contact, age gate | Demo shows Report sheet + EULA acceptance + moderation note |
| Third-party AI data | 5.1.2(i) | Page/brand/transcript/video sent to Anthropic, AssemblyAI, Shotstack, the ClipEngine MCP creative toolchain, Ayrshare/Phyllo | Named-provider AI-sharing consent gate, logged with timestamp | Demo account shows the consent gate; data-flow table in notes |
| Biometric data | BIPA / CCPA-CPRA sensitive data (not an Apple guideline) | Source video = creator's face + voice; derived face/voice embeddings | Separate biometric-consent surface (distinct from AI-sharing), published retention + destruction schedule, no-sale stance, deletion cascade | Demo shows Settings → Privacy → Biometric data; biometric policy URL; consent copy in notes |
| Subscriptions | 3.1.2 | StoreKit 2 + RevenueCat paywall | Full metadata on the purchase screen + functional PP/EULA links | Paywall screenshot; App Store Connect metadata |
| Not-a-wrapper | 4.2 / 4.3 | Claude-generated scripts; AI-visual formats | Native AVFoundation camera/teleprompter/render + Brand Graph context layer | Review notes enumerate native subsystems |
| Account deletion | 5.1.1(v) | Supabase Auth accounts | In-app delete flow + SiwA token revoke + cascade to all processors | Demo: Settings → Delete account walkthrough |
| Privacy label / manifest | 5.1.1 + manifest | Multiple SDKs collecting data | Accurate App Privacy label + `PrivacyInfo.xcprivacy` for app & every SDK | Label matches data-flow table |
| Demo access | 2.1 | App gated behind onboarding + subscription + connected socials | Pre-populated demo account, pre-connected sandbox publish | Credentials in App Review Information |
| Copyright / music | 5.2 + civil liability | Trending-audio + rendered clips | Licensed/royalty-free audio only; user warranty + indemnity in EULA | EULA clause; render pipeline never embeds platform audio |

Primary source for all guideline numbers: [Apple — App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/). The Nov 13 2025 AI update: [Apple Developer news — updated guidelines](https://developer.apple.com/news/?id=ey6d8onl).

---

## 14.2 Guideline 1.2 — User-Generated Content

Marque stores and processes creator-uploaded source video, AI-derived clips, and AI-generated scripts/visuals. Even though *most* content is the creator's own face and own brand, this is squarely UGC, and Apple requires the full 1.2 scaffold to be present and demonstrable. We do **not** rely on the "it's only the user's own content" argument — reviewers still expect the report + EULA + filter scaffolding for any generative or upload surface ([App Review Guidelines, 1.2](https://developer.apple.com/app-store/review/guidelines/#user-generated-content)).

### 14.2.1 The five required mechanisms

| # | Requirement | Marque implementation |
|---|---|---|
| 1 | **Filter objectionable material** before it appears | Automated pre-moderation on (a) uploaded source video, (b) AI-generated visuals (faceless / green-screen / AI-visual formats), and (c) AI-generated script text. Text via **Haiku 4.5** classifier (see `07-ai-system.md`); image/video via a vision moderation pass (vendor = **Open Question 1**). |
| 2 | **Report mechanism** with ability to act | Every clip object, every AI script draft, and every AI-generated visual carries a `Report` affordance. Reports enter a queue with a documented **24-hour action SLA**. |
| 3 | **Block abusive users** | Single-tenant by default: a creator sees only their own content, so there is **no user-to-user content surface** in v1. This *narrows* the block obligation but does **not** remove the Report-AI-output or EULA requirements. If any sharing surface ships (shared teardown cards, referral feed — **Open Question 2**), a block mechanism becomes mandatory. |
| 4 | **Published contact info** | Support email surfaced in-app (Settings → Support) *and* in App Store Connect metadata. |
| 5 | **Enforced EULA** | Affirmative acceptance at signup gating account creation. EULA prohibits objectionable content + abusive behavior, with no-tolerance + 24h enforcement language. See §14.10. |

### 14.2.2 Guideline 1.2.1 — age restriction (Nov 2025 reinforcement)

Creator/UGC apps must provide a way to flag content exceeding the age rating and to restrict underage access via a declared/verified age ([ppc.land — Apple tightens age controls + data-sharing disclosure](https://ppc.land/apple-tightens-app-store-age-controls-and-data-sharing-disclosure/)). Marque sets an **age gate at signup** (date-of-birth or "I am 17+" affirmation) and stores a `content_age_signal` on generated outputs. See §14.7 for the target age tier.

### 14.2.3 Moderation pipeline (first-class subsystem)

```
                 ┌─────────────────────────────────────────────────────────┐
  upload / AI    │  PRE-PUBLISH SCAN                                        │
  generation ───▶│  • video frames → vision moderation (Open Q1)           │
                 │  • AI visual    → vision moderation                      │
                 │  • script text  → Haiku 4.5 classifier                   │
                 └───────────────┬─────────────────────────────────────────┘
                                 │ score
              ┌──────────────────┼───────────────────────┐
              ▼ pass             ▼ flag                   ▼ block
        publishable        HOLD QUEUE                 hard-reject
        (clear)            (human review)             (never surfaces;
                                 ▲                     user told why)
                                 │
        in-app  Report ─────────┘  → 24h SLA action → remove + (if surface) eject
```

#### Data model (Supabase Postgres — see `12-backend-data-security.md`)

```sql
-- Moderation verdict attached to any moderatable object (source video, clip, script, AI visual)
create type moderation_status as enum ('pending','clear','flagged','blocked','removed');
create type moderatable_kind  as enum ('source_video','clip','script','ai_visual');

create table moderation_record (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users(id) on delete cascade,
  object_kind     moderatable_kind not null,
  object_id       uuid not null,                 -- FK resolved per kind in app layer
  status          moderation_status not null default 'pending',
  classifier      text,                          -- 'haiku-4.5' | '<vision-vendor>'
  scores          jsonb,                         -- raw category scores
  decided_at      timestamptz,
  decided_by      text,                          -- 'auto' | reviewer id
  created_at      timestamptz not null default now()
);
create index on moderation_record (status) where status in ('pending','flagged');

-- User-initiated reports
create type report_reason as enum ('sexual','violence','hate','harassment','illegal','copyright','other');

create table content_report (
  id              uuid primary key default gen_random_uuid(),
  reporter_id     uuid not null references auth.users(id) on delete set null,
  object_kind     moderatable_kind not null,
  object_id       uuid not null,
  reason          report_reason not null,
  note            text,
  status          text not null default 'open',  -- open | actioned | dismissed
  sla_due_at      timestamptz not null default (now() + interval '24 hours'),
  actioned_at     timestamptz,
  created_at      timestamptz not null default now()
);
create index on content_report (status, sla_due_at);
```

A FastAPI worker (Trigger.dev scheduled job; see `10-social-publishing.md` for the durable-job pattern) sweeps `content_report` for rows approaching `sla_due_at` and alerts the on-call moderator so the **24h SLA** is never missed.

#### Report sheet — component spec

| Property | Value |
|---|---|
| Surface | Bottom sheet, half-height, cream background, serif title "Report this content" |
| Trigger | Long-press or overflow `•••` on any clip / script / AI visual |
| Body | Single list of `report_reason` rows (radio), gold check on selection; optional one-line note field (hairline underline, no box) |
| CTA | Single gold "Submit report" button; quiet "Cancel" text below |
| Motion | Slow ease-in-out spring, ~360ms |

**States:** *loading* (submit in flight — button shows a thin gold progress hairline); *success* ("Thank you. We review reports within 24 hours." — auto-dismiss 2s); *error* (inline cream toast, retry); *offline* (queues locally, submits on reconnect, optimistic "Saved — will send when you're back online"); *permission-denied* (n/a — no OS permission needed).

---

## 14.3 Guideline 5.1.2(i) — Third-party AI data-sharing disclosure (the sleeper rejection)

**Effective Nov 13 2025**, Apple requires that you *"clearly disclose where personal data will be shared with third parties, including with third-party AI, and obtain explicit permission before doing so"* ([Apple Developer news](https://developer.apple.com/news/?id=ey6d8onl); plain-English: [TechCrunch](https://techcrunch.com/2025/11/13/apples-new-app-review-guidelines-clamp-down-on-apps-sharing-personal-data-with-third-party-ai/)). This is the single most consequential *new* rule for Marque's exact stack.

Two non-negotiables:

1. **The disclosure must be a clear, in-context consent surface — it cannot be buried in the Privacy Policy or Terms.** A generic "we share with service providers" is explicitly insufficient.
2. **It must name the provider** and the data + purpose: *"Your script content is sent to Anthropic to generate drafts," "Your video is sent to AssemblyAI for transcription."* ([arshtechpro — 5.1.2(i) deep-dive](https://dev.to/arshtechpro/apples-guideline-512i-the-ai-data-sharing-rule-that-will-impact-every-ios-developer-1b0p)).

On-device AI (Core ML) is exempt; **any network call to a third-party model triggers the rule.** Every Marque AI feature is a network call.

### 14.3.1 Data-flow map (embed this verbatim in App Review notes)

| Vendor | Adapter | Data sent | Purpose | Named in consent string |
|---|---|---|---|---|
| **Anthropic (Claude — Opus 4.8 / Haiku 4.5)** | LLM adapter (`07-ai-system.md`) | Page/brand text, brand graph, transcripts, draft scripts | Script generation, brand reasoning, teardowns, voice/safety classification | "…sent to **Anthropic** to write and check your scripts." |
| **AssemblyAI** | Transcription adapter (`09-video-pipeline.md`) | Recorded/uploaded video audio | Transcription + moment detection | "…sent to **AssemblyAI** to transcribe your recording." |
| **ClipEngine MCP creative toolchain** (`personal_clipper`, `reframe`, `video_analysis`, `virality_predictor`, `generate_image`/`generate_video`, `remove_background`, `outpaint_image`) — see `09-video-pipeline.md` §8 and `10-social-publishing.md` §12.1 | ClipEngine adapter | Video, frames, transcripts | Clipping, reframing, virality scoring, AI visuals | "…sent to our **clip engine** to cut and analyze your clips." — the literal vendor identity behind the MCP toolchain is pinned by **Open Question 7** and the consent string is finalized against that name before any US/EU launch. |
| **Shotstack** | Render adapter | Clip media, captions, overlay text | Templated rendering of formats | "…sent to **Shotstack** to render your formats." |
| **Ayrshare** | Publisher adapter (`10-social-publishing.md`) | Rendered clips, captions, schedule | Publishing to Instagram/TikTok | "…sent to **Ayrshare** to publish on your behalf." |
| **Phyllo / Ayrshare** | Insights adapter | Account IDs, post IDs | Performance pullback | "…retrieved via **Phyllo** to read your post performance." |

> Note: Cloudflare R2 / Stream is storage/CDN, not a "third-party AI," but it *is* a third-party recipient of User Content and must appear in the Privacy Policy processor list and nutrition label (§14.6).

### 14.3.2 Consent gate — placement and behavior

- **Where:** an explicit named-provider AI-data-sharing consent gate during onboarding (see `03-onboarding.md`), presented *before* the first AI call. Per-feature re-confirmation is shown on first use of any feature whose vendor was not yet consented (defensive design — covers vendors added post-onboarding).
- **One idea per screen.** Serif title: *"Marque works with a few specialist services."* Body: a short, scannable list of the named providers + the one-line purpose each. A single "Read exactly what's shared" link opens the full data-flow detail one layer deep. CTA: gold "Allow & continue"; quiet "Not now" which blocks the gated feature (not the whole app) and explains what won't work.
- **Logged.** Every grant is timestamp-logged in Supabase. Withdrawal is possible from Settings → Privacy.

```sql
create table ai_consent (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  provider     text not null,        -- 'anthropic' | 'assemblyai' | 'clip_engine' | 'shotstack' | 'ayrshare' | 'phyllo'
  scope        text not null,        -- short purpose key
  granted      boolean not null,
  consent_copy text not null,        -- exact string the user saw (audit)
  app_version  text not null,
  created_at   timestamptz not null default now()
);
create index on ai_consent (user_id, provider, created_at desc);
```

**Acceptance criteria:**
- AC-1: No third-party AI network call fires for a user until a `granted = true` row exists for that provider.
- AC-2: The consent screen names every provider that will receive personal data; no generic "service providers" wording.
- AC-3: Withdrawing consent in Settings writes a `granted = false` row and disables the dependent feature gracefully (empty-state copy, not a crash).
- AC-4: The exact consent copy shown is persisted in `consent_copy` for audit.

**States:** *loading* (provider list fetched from remote config — show skeleton lines); *error* (cannot load config → fail safe by blocking AI features, not by silently proceeding); *offline* (cannot grant → AI features show "Connect to the internet to enable"); *withdrawn* (feature shows a calm re-enable prompt).

---

## 14.3a Biometric data — face + voice (BIPA / state biometric law)

> **Why this is its own section, not a row in §14.3.** Marque's source recording *is* the creator's face and voice — the single highest-PII asset in the product. The §14.3 AI-sharing consent satisfies Apple Guideline 5.1.2(i), which is a **disclosure-and-permission** rule about *sending data to a third party*. It is **not** a valid legal basis for *collecting, storing, or deriving identifiers from biometric information*. Illinois' **Biometric Information Privacy Act (BIPA)** and the biometric provisions of state privacy laws (e.g. Texas CUBI, Washington HB 1493, and the "sensitive data" categories in **CCPA/CPRA**, Colorado CPA, Virginia VCDPA, etc.) require a **separate, biometric-specific basis**: informed written consent *before* collection, a *published* retention-and-destruction schedule, a no-sale/no-disclosure stance, and a reasonable standard of care. BIPA in particular carries a **private right of action with statutory damages per violation**, so this is genuine civil liability, not only an App Store concern. We therefore do **not** overload `ai_consent` or `users.ai_consent_at` for biometric consent — biometrics get their own consent record, their own surface, and their own retention rule. (This section is the owner that `12-backend-data-security.md` Open Q3 points to.)

**Scope of what counts as biometric here.** The raw source/repurpose video (face + voice), any **face geometry / face embeddings** used for reframing or speaker-tracking, and any **voiceprint / voice embedding** used for voice-match script checks (see `07-ai-system.md`). Transcribed *words* are not biometric; the *audio that carries the voice* and any voice embedding are.

### 14.3a.1 Posture (binding policy)

| Requirement | Marque rule |
|---|---|
| **Separate written consent, pre-collection** | A distinct biometric consent — separate from the §14.3 AI-sharing gate and from the EULA — is obtained **before the first recording is captured or uploaded** (and before any repurpose upload). It states *what* is collected (face + voice), *why*, *who* it is disclosed to (the §14.3.1 processors that receive biometric data: the ClipEngine MCP toolchain, AssemblyAI, Shotstack, R2/Stream), *how long* it is kept, and *how* it is destroyed. |
| **No sale, no profiting, no disclosure beyond processors** | Marque does **not** sell, lease, trade, or otherwise profit from biometric identifiers, and discloses them only to the named sub-processors strictly to deliver the feature. Stated verbatim in the consent copy and the Privacy Policy. This is consistent with the CCPA "do not sell/share" stance in §14.9. |
| **Published retention + destruction schedule** | Retention is the **shorter of**: the purpose being satisfied, or a fixed outer bound. Default schedule (confirm with counsel — **Open Question 8**): raw source video + derived face/voice embeddings destroyed **within 30 days of the clip batch being finalized or the project being deleted**, and in all cases **no later than 1 year after the creator's last interaction with that asset** (BIPA's statutory ceiling is "when the purpose is satisfied or within 3 years of last interaction, whichever is first"; Marque commits to a tighter bound). Embeddings are never retained longer than the media they derive from. |
| **Deletion guarantee** | Account deletion (§14.6) and per-project deletion both destroy biometric data and derived embeddings across **every** processor that received them, on the same idempotent cascade. Biometric rows are explicitly enumerated in the cascade (§14.6.2). |
| **Standard of care** | Biometric media lives only in **private** buckets (Supabase Storage / R2 `recordings`, never public), encrypted at rest and in transit, access-scoped by RLS to the owning `user_id` (see `12-backend-data-security.md`). |
| **No biometric collection from minors** | Tied to the §14.2.2 age gate; BIPA and several state laws treat minors' biometrics as especially sensitive. The 17+ posture (§14.7.4) plus the age affirmation gate the recording surface. |

### 14.3a.2 Consent surface (distinct from the AI-sharing gate)

- **Where:** a dedicated **Biometric data consent** screen presented at the **first Record / repurpose-upload**, not during the §14.3 onboarding AI-sharing gate. Rationale: it must be *informed and specific to recording*, and a creator who never records never grants it. It is also reachable and revocable at **Settings → Privacy → Biometric data**. Specified jointly with `03-onboarding.md` (which owns the Record entry flow).
- **One idea per screen.** Serif title: *"Marque records your face and voice."* Body: a short, scannable statement of what's collected, the processors it's shared with, the retention window, and the no-sale promise — in quiet declarative copy. A single "Read the full biometric policy" link opens the detail one layer deep. CTA: gold "I agree — start recording"; quiet "Not now" returns to the prior screen **without** recording (the feature is blocked, not the app).
- **Affirmative + specific.** Consent is an explicit tap on biometric-specific copy — it is **not** bundled into the EULA checkbox or the AI-sharing "Allow & continue." The exact string shown is persisted for audit (BIPA "written consent" + CCPA recordkeeping).

```sql
-- Biometric consent — SEPARATE from ai_consent; do NOT overload users.ai_consent_at
create table biometric_consent (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  granted       boolean not null,
  consent_copy  text not null,        -- exact biometric-consent string the user saw (BIPA/CCPA audit)
  policy_version text not null,       -- version of the published biometric policy at grant time
  app_version   text not null,
  granted_at    timestamptz,
  withdrawn_at  timestamptz,
  created_at    timestamptz not null default now()
);
create index on biometric_consent (user_id, created_at desc);
```

> **Relationship to `users.ai_consent_at`.** `12-backend-data-security.md` currently lists `users.ai_consent_at` as the consent "hook" for face/voice. That column remains the marker for **AI-*processing*/sharing** consent (§14.3). Biometric **collection** consent is the `biometric_consent` row above. The two are recorded independently; the recording surface requires a `granted = true` biometric row regardless of AI-sharing state.

### 14.3a.3 Acceptance criteria

- AC-1: No camera/mic capture and no upload of source video occurs until a `biometric_consent` row with `granted = true` exists for the user.
- AC-2: The biometric consent copy is biometric-specific, names the processors that receive face/voice, states the retention window, and includes the no-sale statement — and is persisted verbatim in `consent_copy`.
- AC-3: Withdrawal at Settings → Privacy → Biometric data writes `withdrawn_at`, blocks further recording, and **triggers destruction** of retained biometric media + embeddings on the §14.6.2 cascade (consistent with the deletion guarantee).
- AC-4: Retention worker destroys source video + face/voice embeddings per the §14.3a.1 schedule, idempotently, across every processor; destruction is logged.
- AC-5: **US-launch-gating (incl. Illinois):** the biometric consent surface, the published biometric policy, and the destruction worker must all be live before any US release. Illinois availability is explicitly blocked until counsel signs off (Open Question 8).

**States:** *loading* (policy text from remote config — skeleton); *error* (cannot load policy → block recording, never capture without showing consent); *offline* (consent text is bundled as a fallback so a creator can still grant and record offline; the grant syncs on reconnect); *withdrawn* (Record shows a calm re-enable prompt explaining recording is paused and prior media is being destroyed); *permission-denied* (OS camera/mic denial is separate — deep-link to iOS Settings).

---

## 14.4 Guideline 3.1.2 — Subscription metadata

Marque's paywall is **StoreKit 2 + RevenueCat** (see `11-monetization.md`). **Apple IAP is mandatory** for iOS digital subscriptions; **Stripe must never appear on the iOS paywall** (reserved for a future web billing surface only). RevenueCat is the receipt/entitlement layer behind StoreKit 2 — the *purchase* itself is a StoreKit IAP.

### 14.4.1 Required on the same screen as the purchase button

Per [App Review Guidelines, 3.1.2](https://developer.apple.com/app-store/review/guidelines/#subscriptions) and the practical [3.1.2 fix checklist](https://blog.wenhaofree.com/en/posts/articles/app-store-guideline-3-1-2-subscription-fix/):

| Element | Requirement | Marque copy/behavior |
|---|---|---|
| Subscription **name** | Un-abbreviated title | "Marque Pro" |
| **Length** | Billing period spelled out | "Billed monthly" / "Billed annually" |
| **Price per period** | Full price, per period | localized price from StoreKit, e.g. "$X.XX / month" |
| **Auto-renewal terms** | Apple's standard disclosure | "Renews automatically unless turned off at least 24 hours before the end of the period. Manage or cancel anytime in Settings." |
| **Privacy Policy link** | Functional, on the paywall | bottom-of-paywall link → in-app PP webview |
| **Terms of Use (EULA) link** | Functional, on the paywall | bottom-of-paywall link → in-app EULA webview |

The same Privacy Policy and Terms of Use links must also populate the **App Store Connect metadata fields** (App Privacy URL + Terms field / EULA).

### 14.4.2 "Ongoing value" — pre-empting the 3.1.2 value rejection

Reviewers reject subscriptions that read as a one-time unlock. Marque frames value as **continuously delivered**: ongoing Trend Radar updates, recurring batch film-once → schedule-all-week publishing, and the performance-learning loop that tightens scripts over time. Review notes (§14.11) state this explicitly. (The June 2025 guideline update removed the old 3.1.2(a) "one subscription across your own apps" clause — irrelevant unless Marque ships multiple apps. [ASO World — June 2025 update](https://asoworld.com/blog/apple-app-store-agreement-guideline-updates-june-2025/).)

**Paywall component states** (see `11-monetization.md` for the full spec): *loading* (StoreKit products fetching — gold hairline, no fake prices); *empty/products-unavailable* ("Subscriptions are momentarily unavailable — try again shortly"); *error* (purchase failed → StoreKit error mapped to calm copy + retry); *offline* (purchase disabled, "Connect to subscribe"); *restore* (always-present "Restore purchases" — Apple requires a restore path).

---

## 14.5 Guideline 4.2 / 4.3 — Minimum functionality & "not a wrapper"

Marque must read as a native app delivering value **beyond a website or a generic LLM chat**. AI apps that "just call an LLM" draw heightened review ([OpenForge — AI app rules 2025](https://openforge.io/app-store-review-guidelines-2025-essential-ai-app-rules/); [Guideline 4.2 minimum functionality](https://shopapper.com/fix-apple-guideline-4-2-rejection-minimum-functionality-explained/)).

| Defensible differentiator | Why it is "beyond a chatbot" | Native subsystem |
|---|---|---|
| **Brand Graph** | Persistent, proprietary context layer that compounds over time | Supabase-backed graph (`12-backend-data-security.md`) |
| **Film-once batch loop** (HERO) | On-device capture, teleprompter, render pipeline | AVFoundation camera + teleprompter; ClipEngine + Shotstack render |
| **Format Library render-recipes** | Structured render-recipes (split-screen, 3-up, green-screen, faceless AI-visual, before/after, etc.) — not raw LLM text | Shotstack templated rendering (`09-video-pipeline.md`) |
| **Performance learning** | Closes the loop on real post analytics | Insights adapter + Trend Radar (`08-format-virality.md`) |
| **Native scheduling/publish** | Real third-party publishing, push, offline handling | Publisher adapter + APNs |

**Anti-pattern to avoid:** an MVP that is visually a chat box over Claude. The Today screen is a single directive + gold streak glyph + one trend line (anti-clutter doctrine, `01-information-architecture.md`) — it does not look like a chat UI, which also helps the 4.3 argument. **Review notes must enumerate these native subsystems explicitly** (§14.11).

---

## 14.6 Guideline 5.1.1(v) — In-app account deletion

Marque has accounts (Supabase Auth), so it **must** offer in-app, easy-to-find account deletion that removes the account **and** associated personal data — deactivation/disable is insufficient ([Apple — Offering account deletion in your app](https://developer.apple.com/support/offering-account-deletion-in-your-app/), effective June 30 2022; reinforced via [Apple Developer news](https://developer.apple.com/news/?id=12m75xbj)).

### 14.6.1 Requirements

- **Easy to find:** Settings → Account → "Delete account" (one layer deep, not buried).
- **Identity confirmation** before delete (re-auth or email/SMS code) is permitted and used.
- **Sign in with Apple:** if SiwA is offered, Marque **must call the Sign in with Apple REST API to revoke the user's tokens** on deletion.
- **Available to all users regardless of region.**
- **Subscription caveat copy:** "Deleting your account does not cancel your Apple subscription. Manage it in Settings → Apple ID → Subscriptions." (Apple subscriptions are managed in iOS Settings, not by us.)

### 14.6.2 Deletion cascade (must reach every processor)

| Target | Action |
|---|---|
| Supabase Postgres | Cascade delete all `user_id`-scoped rows (`on delete cascade` throughout `12-backend-data-security.md`) |
| Supabase Storage | Delete brand assets, drafts |
| Cloudflare R2 / Stream | Delete source video + rendered clips + thumbnails (this is the biometric face+voice media; §14.3a) |
| Biometric embeddings | Destroy any derived **face/voice embeddings** held in Postgres/Storage or passed to a processor (§14.3a) — enumerated explicitly so biometric data is never left behind under a generic "user content" sweep |
| RevenueCat | Delete/anonymize the app-user; note Apple subscription must be cancelled by user in iOS Settings |
| AssemblyAI | Call transcript delete/erase endpoint; confirm uploaded **audio (voice)** is also purged, not just the transcript text (§14.3a) |
| Shotstack | Delete rendered assets / source where retained |
| Ayrshare / Phyllo | Disconnect linked accounts + request data erase |
| Anthropic | API default no-training/no-retention posture (confirm in DPA — **Open Question 6**); no stored personal data to delete beyond transient request logs |
| Sign in with Apple | REST API token revocation |
| PostHog / Sentry | Issue per-user delete/erasure request |

### 14.6.3 Delete flow — component spec

| Step | Screen | Copy / behavior |
|---|---|---|
| 1 | Settings → Account | Quiet "Delete account" row, gold-free (destructive lives in calm grey-ink until confirmed) |
| 2 | Confirmation | Serif title "Delete your account?" + plain consequences list (content removed, not recoverable) + the subscription caveat |
| 3 | Identity | Re-auth (SiwA / email code) |
| 4 | Final | Single destructive-styled "Delete permanently"; quiet "Keep my account" |
| 5 | Processing | Durable Trigger.dev cascade job; in-app "We're deleting your data" with a completion email |

**States:** *loading* (cascade running — non-blocking, user is signed out immediately, email confirms completion); *error* (partial-failure path: job retries idempotently; user is told deletion is in progress and will complete); *offline* (delete cannot be initiated — "Connect to delete your account"); *permission-denied* (re-auth failed → cannot proceed).

**Acceptance criteria:**
- AC-1: Delete option reachable in ≤2 taps from Settings.
- AC-2: SiwA users have tokens revoked via the REST API on deletion.
- AC-3: Cascade job is idempotent and verifiably touches every processor in §14.6.2.
- AC-4: User receives a completion confirmation; data the developer is legally required to retain (e.g., tax records for purchases) is the only exception.

---

## 14.7 App Privacy "nutrition label," Privacy Manifest, ATT, age rating

### 14.7.1 App Privacy nutrition label (App Store Connect → App Privacy)

Declare per data type with **"Linked to user"** and **"Used for tracking"** flags. The 2025 rule requires **naming third-party recipients**, not generic categories ([Apple — App Privacy Details](https://developer.apple.com/app-store/app-privacy-details/); [Apple — User Privacy and Data Use](https://developer.apple.com/app-store/user-privacy-and-data-use/)).

| Data type | Collected? | Linked to user | Used for tracking | Recipients / purpose |
|---|---|---|---|---|
| **User Content** (video, scripts, brand assets) | Yes | Yes | No | App functionality (Anthropic, AssemblyAI, ClipEngine MCP toolchain, Shotstack, R2/Stream, Ayrshare) |
| **Sensitive Info — biometric** (face + voice in source video, derived voice/face embeddings) | Yes | Yes | No | App functionality (recording, clipping, voice-match script checks); see §14.3a — separate biometric consent governs this row |
| **Contact Info** (email, name) | Yes | Yes | No | Account, support |
| **Identifiers** (user/account IDs) | Yes | Yes | No | App functionality, RevenueCat entitlement |
| **Usage Data** | Yes | Yes | No | Product analytics (PostHog) |
| **Diagnostics** | Yes | No (de-identified where possible) | No | Crash/perf (Sentry) |
| **Purchases** | Yes | Yes | No | Subscription management (RevenueCat) |

> The label must mirror the Privacy Policy and the §14.3 data-flow table exactly. Any drift is a 5.1.1 rejection risk.

### 14.7.2 Privacy Manifest (`PrivacyInfo.xcprivacy`)

Mandatory since **May 1 2024**. The app and **each SDK** must declare collected data types + **Required Reason API** codes ([Apple — Privacy manifest files](https://developer.apple.com/documentation/bundleresources/privacy-manifest-files)).

- `NSPrivacyTracking` = **false** (Marque does not track for ads — see ATT below). Therefore no `NSPrivacyTrackingDomains` needed.
- Declare `NSPrivacyCollectedDataTypes` matching the label.
- Declare `NSPrivacyAccessedAPITypes` with reason codes for any Required-Reason APIs used (e.g., file timestamp, user defaults).
- **Verify each third-party SDK ships its own signed manifest:** RevenueCat, Supabase-swift, Sentry, PostHog. Confirm current manifest status at build time (**Open Question 4**).

```xml
<!-- PrivacyInfo.xcprivacy (app-level skeleton) -->
<dict>
  <key>NSPrivacyTracking</key><false/>
  <key>NSPrivacyTrackingDomains</key><array/>
  <key>NSPrivacyCollectedDataTypes</key>
  <array>
    <!-- one dict per type from §14.7.1, with NSPrivacyCollectedDataTypeLinked = true,
         NSPrivacyCollectedDataTypeTracking = false, and purpose codes -->
  </array>
  <key>NSPrivacyAccessedAPITypes</key>
  <array>
    <!-- e.g. NSPrivacyAccessedAPICategoryUserDefaults with reason code CA92.1 -->
  </array>
</dict>
```

### 14.7.3 ATT (App Tracking Transparency)

ATT is required **only** if Marque links its data with other companies' data for ad targeting/measurement or shares with data brokers ([Apple — User Privacy and Data Use](https://developer.apple.com/app-store/user-privacy-and-data-use/)).

> **Decision: Marque avoids cross-app ad tracking entirely → no ATT prompt.** PostHog/Sentry first-party product analytics are **not** "tracking" so long as they are not joined with third-party data for advertising. This yields a cleaner nutrition label, simpler review, and a truthful CCPA "we do not sell/share" stance (§14.9). If an ad-attribution SDK is ever added, ATT becomes mandatory and `NSPrivacyTracking` flips to true.

### 14.7.4 Age rating

Apple's **2025 tiered age-rating system** added 13+/16+/18+ tiers. UGC + AI-generated content + links to third-party social platforms (where unrestricted content lives) typically push to **17+/18+** or require an honest content-age declaration ([ppc.land](https://ppc.land/apple-tightens-app-store-age-controls-and-data-sharing-disclosure/)).

> **Recommendation: target 17+** given UGC + outbound social posting. Set the UGC and "unrestricted web/social access" flags honestly in App Store Connect. Final tier = **Open Question 3** (confirm against the 2025 tiered system at submission).

---

## 14.8 Copyright + trending-audio / music licensing (designed out, not just disclaimed)

This is genuine civil liability, not only an App Store concern.

- Platform music libraries (TikTok Commercial Music Library, Instagram audio) are **licensed only for use *on that platform***; downloading audio from one and posting it elsewhere **infringes** ([TikTok Music Terms of Service](https://www.tiktok.com/legal/page/global/music-terms-eea/en)).
- **Business/commercial accounts** have far narrower music rights than personal accounts, and **brands have been sued even for reposting** UGC containing popular music ([Bennett Creative — why brands get sued over TikTok/IG music](https://www.bennettcreative.co/post/why-brands-are-suddenly-getting-sued-over-music-on-tiktok-and-instagram); [SRIPLAW — Instagram business accounts & copyright](https://sriplaw.com/blog/instagram-business-accounts-and-copyright/)). Marque's users are creators/brands → high exposure.

**Design rules (binding on the render pipeline — see `09-video-pipeline.md` and `08-format-virality.md`):**

1. The **Shotstack render pipeline never pulls or embeds platform "trending audio."** It uses a **cleared/licensed or royalty-free library**, no music, or creator-supplied audio the creator warrants they own/licensed.
2. **"Trending audio" in Trend Radar is a *signal/recommendation*** — a prompt to add a specific sound *inside the native IG/TikTok app at post time*, never audio Marque downloads and re-embeds.
3. The **EULA** makes the creator **warrant they own or have licensed all uploaded content** and **indemnify Marque** for infringement claims (§14.10).
4. **AI-content labeling:** for AI-visual / faceless formats, Marque auto-applies the destination platform's AI-content disclosure flag at publish (TikTok/Meta require labeling AI-generated content) — handled in the Publisher adapter (`10-social-publishing.md`).

---

## 14.9 Platform publishing compliance (Instagram Graph + TikTok)

These reviews are **separate from Apple** and are on the critical path. Marque hides them behind the **Publisher adapter (Ayrshare)** and **Insights adapter (Phyllo / Ayrshare)**, but the underlying platform policies (Meta Platform Terms, TikTok Developer ToS) bind Marque independently, and Apple may ask how publishing works.

| Platform | Gating reality | Plan |
|---|---|---|
| **TikTok Content Posting API** | Unaudited clients can post **SELF_ONLY (private)** and only **≤5 users / 24h**; full public **Direct Post** requires passing TikTok's **audit (2–4 weeks, multi-round)** ([Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started), [Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)) | Ship publish **gated/private-first** until audit clears, or block the public-publish feature on audit — **Open Question 5** |
| **Instagram Graph API (Content Publishing)** | Needs a Meta app with `instagram_business_content_publish`, **Advanced Access** (publishing on behalf of accounts we don't own), **per-permission App Review with a screencast**, ~2–4 weeks; requires IG Professional account + linked FB Page ([Instagram Platform overview](https://developers.facebook.com/docs/instagram-platform/overview/), [media_publish reference](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media_publish/)) | Begin Meta App Review early; treat as launch-gating |

> Even via Ayrshare, the *underlying* TikTok app still needs audit for public posting at scale. Sequence: submit both platform reviews before Apple submission so the publish feature is demonstrable.

---

## 14.10 Required legal documents

The Privacy Policy, the Terms of Use / EULA, and the standalone **Biometric Data Policy** (§14.10.4) must each be **functional URLs** in App Store Connect (where applicable) and reachable in-app. The Privacy Policy and Terms (EULA) are also linked on the paywall (§14.4); the Biometric Data Policy is linked from the §14.3a.2 consent surface and the Privacy Policy.

### 14.10.1 Privacy Policy — contents checklist
- Data collected (mirrors the §14.7.1 nutrition label exactly).
- **Each third-party processor named + purpose** — especially AI providers (Anthropic, AssemblyAI, the ClipEngine MCP creative toolchain, Shotstack) and Ayrshare/Phyllo and Cloudflare R2/Stream (per 5.1.2(i)). The ClipEngine processor name must be the *literal* vendor identity resolved in **Open Question 7**, not the abstract adapter name, so the policy, the consent string (§14.3.1), and the nutrition label (§14.7.1) all name the same recipient.
- Retention periods; export & delete rights (§14.11); children's-data stance; CCPA "do not sell/share" statement (true because no ad tracking).
- **Biometric-information disclosures** (§14.3a): face + voice are collected, who receives them, the retention-and-destruction schedule, and the explicit **no-sale/no-profit** statement. The Privacy Policy either contains, or links to, the standalone **Biometric Data Policy** (§14.10.4).
- Sub-processor list + DPA posture; contact email.

### 14.10.2 Terms of Use / EULA — contents checklist
Apple's standard EULA **or** a custom one at least as protective. Must include UGC + AI clauses:
- Prohibition of objectionable content & abusive behavior; **no-tolerance + 24h enforcement**; report/block reference.
- **Content ownership & license warranty** — user warrants they own/have licensed all uploaded content.
- **Music/copyright indemnity** — user indemnifies Marque for infringement claims (§14.8).
- **AI-output disclaimer** — outputs may be inaccurate; **no guarantee of virality/performance**; user is responsible for what they publish; AI content provided "as-is."
- Linked on the paywall and accepted affirmatively at signup.

### 14.10.3 AI-specific disclosures
- Named-provider data-sharing (5.1.2(i)) — see §14.3.
- AI-content disclaimer (above).
- AI-content **labeling** note: Marque auto-applies platform AI-content flags for AI-generated formats at publish (§14.8).

### 14.10.4 Biometric Data Policy (standalone)
A dedicated, publicly reachable policy (its own URL; also surfaced in-app behind the §14.3a.2 "Read the full biometric policy" link and at Settings → Privacy → Biometric data). BIPA and several state laws expect a **written, publicly available policy** covering biometric handling. Contents:
- **What is collected and why** — face + voice in source video; derived face/voice embeddings; the specific features they power.
- **Who receives it** — the §14.3.1 processors that touch biometric data (the ClipEngine MCP toolchain, AssemblyAI, Shotstack, Cloudflare R2/Stream), named to match the consent string and Privacy Policy.
- **Retention + destruction schedule** — the §14.3a.1 bound, stated as a definite period and trigger, plus the destruction mechanism.
- **No-sale / no-profit / no-disclosure-beyond-processors statement.**
- **Consent + withdrawal** — how consent is obtained (separate written biometric consent) and how to withdraw it (which triggers destruction).
The EULA (§14.10.2) cross-references this policy; the biometric consent screen (§14.3a.2) is the affirmative gate, the policy is the full text behind it.

---

## 14.11 GDPR / CCPA — export + delete

| Right | Mechanism |
|---|---|
| **Access / portability (GDPR Art. 15/20)** | User-triggered **data export**: machine-readable JSON/zip of Brand Graph, scripts, transcripts, and asset links, generated by a Supabase Edge Function / FastAPI endpoint, delivered via a signed download link |
| **Erasure (GDPR Art. 17) / CCPA delete** | The §14.6 account-deletion cascade *is* the erasure mechanism; reaches every processor |
| **Lawful basis + consent logging** | 5.1.2(i) AI-sharing consent (`ai_consent`), **biometric collection consent** (`biometric_consent`, §14.3a — separate basis under BIPA/CCPA), EULA acceptance, and marketing/push consent each timestamp-logged |
| **Biometric retention + destruction (BIPA / state law)** | Published retention-and-destruction schedule (§14.3a.1); destruction worker enforces it idempotently; face/voice + embeddings enumerated in the deletion cascade (§14.6.2) |
| **DPAs + sub-processor list** | Sign DPAs with each vendor; publish a sub-processor list; confirm no-training/retention posture (**Open Question 6**) |
| **CCPA "Do Not Sell/Share"** | Marque does **not** sell or share personal info for cross-context behavioral advertising — kept true by avoiding ATT-triggering tracking (§14.7.3) |

```sql
-- Data export request (durable Trigger.dev job builds the archive)
create table data_export_request (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  status        text not null default 'pending',   -- pending | building | ready | failed | expired
  download_url  text,                              -- signed, short-lived
  expires_at    timestamptz,
  created_at    timestamptz not null default now()
);
```

**Export flow states:** *loading* ("Preparing your export — we'll email you when it's ready"); *ready* (signed link, expires in 24h); *error* (retry); *offline* (request queues).

---

## 14.12 Demo account + App Review submission artifacts

Marque is gated behind onboarding + subscription + connected social accounts, so reviewers **cannot** connect their own IG/TikTok. Provide:

1. **Working demo account** (username/password) in App Review Information, with the **Brand Graph pre-populated**, sample scripts, and sample rendered clips so the full loop is demonstrable without setup.
2. **Sandbox/demo publish path** — either a stubbed sandbox publish or a pre-connected sandbox social account, so the publish step is visible. Notes must explain that real publishing requires the user's own connected accounts.
3. **Notes to reviewer** (template below).

### Review-notes template (paste into App Review Information)

```
DEMO ACCOUNT
  user: <demo email>   pass: <demo pass>
  The Brand Graph, sample scripts, and sample clips are pre-loaded.

WHY THIS IS NOT A WRAPPER (4.2/4.3)
  Native subsystems: AVFoundation camera + teleprompter; on-device batch
  recording; ClipEngine + Shotstack render pipeline (structured format
  render-recipes, not raw LLM text); persistent proprietary Brand Graph;
  performance-learning loop; native scheduling + push.

AI DATA SHARING (5.1.2(i))
  Named-provider consent gate appears in onboarding (and on first use of any
  new AI feature). Providers named: Anthropic, AssemblyAI, the ClipEngine MCP
  creative toolchain, Shotstack, Ayrshare, Phyllo. See in-app Settings → Privacy.

BIOMETRIC CONSENT (BIPA / state biometric law)
  Source video contains the creator's face + voice (biometric identifiers). A
  SEPARATE written biometric consent (distinct from the AI-sharing consent) is
  collected at first recording, with a published retention + destruction
  schedule and a no-sale statement. See in-app Settings → Privacy → Biometric data.

UGC SAFETY (1.2)
  Report affordance on every clip/script/AI visual; EULA accepted at signup;
  automated pre-publish moderation; 24h action SLA; support email in Settings.

ACCOUNT DELETION (5.1.1(v))
  Settings → Account → Delete account (re-auth required; cascades to all
  processors; revokes Sign in with Apple tokens).

SUBSCRIPTION (3.1.2)
  Paywall shows name, period, price, auto-renew terms, and PP/EULA links.
  Value is ongoing: continuous Trend Radar, recurring publishing, learning loop.

PUBLISHING
  Real publishing requires the user's own connected IG/TikTok accounts. The
  demo uses a pre-connected sandbox so the flow is visible.
```

---

## 14.13 Common rejection reasons → pre-emption

| Likely rejection | Guideline | Pre-emption |
|---|---|---|
| No content filter / report / block / EULA | 1.2 | Moderation pipeline + Report on every AI output/clip + EULA gate + 24h SLA + support email (§14.2) |
| AI data sharing not disclosed/consented in-context | 5.1.2(i) | Named-provider consent gate, logged; not buried in PP (§14.3) |
| Face/voice handled as generic content (BIPA/state-law exposure) | BIPA / CCPA-CPRA (civil, not Apple) | Separate biometric consent surface, published retention + destruction, no-sale stance, biometrics enumerated in deletion cascade; US-launch-gating (§14.3a) |
| Paywall missing price/duration/auto-renew/legal links | 3.1.2 | Full metadata + PP/EULA links on the purchase screen (§14.4) |
| "Just an LLM/web wrapper" | 4.2 / 4.3 | Native camera/teleprompter/render + Brand Graph; review notes (§14.5) |
| No in-app account deletion / SiwA token revoke | 5.1.1(v) | Deletion flow + SiwA REST revoke + cascade (§14.6) |
| Inaccurate privacy label / missing manifest | 5.1.1 / manifest | Audit data flows; verify every SDK manifest (§14.7) |
| Can't access gated app | 2.1 | Pre-populated demo account + notes (§14.12) |
| Music/copyright in rendered output | 5.2 / civil | Licensed/royalty-free only; user warranty + indemnity in EULA (§14.8) |
| AI content unlabeled on platforms | platform policy | Auto-apply platform AI-content flag at publish (§14.8/§14.9) |

---

## Open questions

1. **Vision moderation vendor** for uploaded video frames + AI-generated visuals. Text moderation is covered by Haiku 4.5; image/video is not. (Needed for §14.2 moderation pipeline.)
2. **Any user-to-user surface?** (shared teardown cards, referral feed, comments) — determines whether a *block* mechanism is mandatory under 1.2, and the depth of the moderation obligation. Default assumption: single-tenant, no user-to-user surface in v1.
3. **Final age-rating tier** under Apple's 2025 tiered system — recommendation is **17+** given UGC + outbound social posting; confirm at submission.
4. **`PrivacyInfo.xcprivacy` status** of Supabase-swift and PostHog iOS SDKs at build time (RevenueCat and Sentry ship manifests; confirm all four).
5. **Publish launch scope:** ship gated/private-only (TikTok `SELF_ONLY`, ≤5 users/24h) until TikTok audit + Meta Advanced Access clear, or block the public-publish feature on those approvals?
6. **DPA confirmation** of no-training / data-retention posture for Anthropic, AssemblyAI, Shotstack, the ClipEngine MCP toolchain vendor (Open Question 7), Ayrshare, and Phyllo — for the Privacy Policy processor list and the deletion cascade. The ClipEngine and any transcription vendor DPAs must explicitly cover **biometric data** (face + voice), not just generic "user content" (§14.3a).
7. **Canonical ClipEngine vendor identity.** `07-ai-system.md`, `09-video-pipeline.md`, and `10-social-publishing.md` specify the ClipEngine as the abstract **MCP creative toolchain** (`personal_clipper`, `reframe`, `video_analysis`, `virality_predictor`, image/video generation) behind the `ClipEngine` adapter, and never name a single underlying vendor. Because the 5.1.2(i) consent string, the Privacy Policy, the nutrition label, and the deletion cascade must all name the **actual data recipient(s)** exactly, the literal vendor identity (single provider, or the enumerated set if the MCP toolchain is multi-vendor) must be pinned and propagated identically into 07/08/09 (technical) and §14.3.1 / §14.7.1 / §14.10.1 / §14.6.2 (legal) before any US/EU launch. Until pinned, the consent copy uses the generic "our clip engine" wording and naming it precisely is **launch-gating**. *Owner: Eng + Legal.*
8. **Biometric retention window + Illinois availability (BIPA).** Counsel to confirm the §14.3a.1 destruction schedule (proposed: source video + face/voice embeddings destroyed within 30 days of batch finalization/deletion and no later than 1 year after last interaction — tighter than BIPA's 3-year ceiling) and to sign off the biometric consent copy + standalone Biometric Data Policy (§14.10.4). Illinois (and any state with a biometric private right of action) is **blocked from launch** until this clears. *Owner: Legal + Eng. **US-launch-gating.***

## Sources

- [Apple — App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/) — all guideline numbers (1.2, 3.1.2, 4.2/4.3, 5.1.1, 5.1.2).
- [Apple Developer news — updated App Review Guidelines (Nov 13 2025)](https://developer.apple.com/news/?id=ey6d8onl) — Guideline 5.1.2(i) third-party-AI disclosure, effective date confirmed.
- [TechCrunch — Apple clamps down on third-party-AI data sharing](https://techcrunch.com/2025/11/13/apples-new-app-review-guidelines-clamp-down-on-apps-sharing-personal-data-with-third-party-ai/) — plain-English summary of the Nov 2025 rule.
- [arshtechpro — Guideline 5.1.2(i) deep-dive](https://dev.to/arshtechpro/apples-guideline-512i-the-ai-data-sharing-rule-that-will-impact-every-ios-developer-1b0p) — named-provider implementation specifics.
- [Apple — Offering account deletion in your app](https://developer.apple.com/support/offering-account-deletion-in-your-app/) — 5.1.1(v), data-deletion scope, Sign in with Apple REST token revocation (effective June 30 2022, confirmed).
- [Apple Developer news — account deletion requirement](https://developer.apple.com/news/?id=12m75xbj).
- [App Store Guideline 3.1.2 subscription fix guide](https://blog.wenhaofree.com/en/posts/articles/app-store-guideline-3-1-2-subscription-fix/) — exact paywall metadata checklist.
- [ASO World — June 2025 App Store guideline updates](https://asoworld.com/blog/apple-app-store-agreement-guideline-updates-june-2025/) — removal of 3.1.2(a) shared-subscription clause.
- [Guideline 4.2 minimum functionality](https://shopapper.com/fix-apple-guideline-4-2-rejection-minimum-functionality-explained/) and [OpenForge — AI app rules 2025](https://openforge.io/app-store-review-guidelines-2025-essential-ai-app-rules/) — not-a-wrapper / AI-spam scrutiny.
- [Apple — App Privacy Details](https://developer.apple.com/app-store/app-privacy-details/) and [User Privacy and Data Use](https://developer.apple.com/app-store/user-privacy-and-data-use/) — nutrition label data types + ATT definition.
- [Apple — Privacy manifest files](https://developer.apple.com/documentation/bundleresources/privacy-manifest-files) — `PrivacyInfo.xcprivacy`, Required Reason API, `NSPrivacyTracking`.
- [ppc.land — Apple tightens age controls + data-sharing disclosure (2025)](https://ppc.land/apple-tightens-app-store-age-controls-and-data-sharing-disclosure/) — new age-rating tiers + 1.2.1 creator-app age restriction.
- [TikTok — Content Posting API Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started) and [Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post) — audit / `SELF_ONLY` / 5-user limits.
- [Meta — Instagram Platform overview](https://developers.facebook.com/docs/instagram-platform/overview/) and [media_publish reference](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media_publish/) — content-publish permission, Advanced Access, App Review.
- [TikTok Music Terms of Service](https://www.tiktok.com/legal/page/global/music-terms-eea/en), [Bennett Creative — brands sued over TikTok/IG music](https://www.bennettcreative.co/post/why-brands-are-suddenly-getting-sued-over-music-on-tiktok-and-instagram), [SRIPLAW — Instagram business accounts & copyright](https://sriplaw.com/blog/instagram-business-accounts-and-copyright/) — cross-platform audio licensing risk.
- [Illinois Biometric Information Privacy Act (BIPA), 740 ILCS 14](https://www.ilga.gov/legislation/ilcs/ilcs3.asp?ActID=3004&ChapterID=57) — written-consent-before-collection, published retention/destruction schedule, no-sale rule, and private right of action with statutory damages (§14.3a).
- [California CCPA/CPRA — sensitive personal information (incl. biometric)](https://oag.ca.gov/privacy/ccpa) — biometric data as a "sensitive" category and the do-not-sell/share framework (§14.3a, §14.9).
- [IAPP — US state biometric privacy laws overview](https://iapp.org/resources/article/us-state-privacy-legislation-tracker/) — Texas CUBI, Washington, and the biometric provisions added by comprehensive state privacy laws (Colorado, Virginia, etc.) (§14.3a).
