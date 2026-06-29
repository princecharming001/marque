# 11 — Monetization, Paywall & Entitlements

> **Marque** — *turn overwhelmed creators into consistent ones.*
>
> This document specifies how Marque charges, gates, and meters. It is the canonical reference for the **StoreKit 2 + RevenueCat** implementation, the **Free / Pro / Studio** tier design, the **virtual-currency credit ledger** that controls our per-clip unit costs, the **paywall UX**, the **server-side entitlement mirror** in Supabase, App Store **compliance** (IAP-only, disclosures, restore, anti-steering), and the **dunning + win-back** loop.
>
> Monetization is a **calm surface**, not a growth-hacked one. The anti-clutter doctrine (`00-overview.md` §6, Principle 1) applies to the paywall as much as to Today: one idea per screen, big legible price, quiet declarative copy. We earn the upgrade at the **first value moment**, we do not nag.

**Status:** Canonical · **Owners:** Product + iOS + Backend leads · **Last updated:** 2026-06-29

> **Filename note.** `00-overview.md`'s sibling map lists monetization as `11-monetization.md`; this document is authored at `11-monetization.md`. The two refer to the same canonical section — treat the section name **"Monetization, Paywall & Entitlements"** as the stable identifier and update the overview's map to point here. Cross-references below use the overview's logical names (e.g. `07-ai-system.md`, `10-social-publishing.md`, `05-screens-produce.md`, `14-appstore-compliance-legal.md`, `15-infra-observability-testing.md`).

### Sibling map (where this doc reaches)

| File | Why we cross here |
|---|---|
| `00-overview.md` | §7.4 subscription constraints (IAP mandate, remote paywall); §5 Section-8 referral placement |
| `01-information-architecture.md` | Entitlement check is an adapter concern; the `Billing` adapter hides RevenueCat |
| `02-design-system.md` | Paywall type/color/motion tokens; cream `#F4F1EA` / ink `#0E0E10` / gold `#C9A227` |
| `03-onboarding.md` | Paywall placement = after the first in-voice script (the value moment) |
| `08-format-virality.md` | Format gating (faceless AI-visual / green-screen are Studio); render-credit cost per recipe |
| `07-ai-system.md` | AI-credit cost per Opus/Haiku call; what a script/teardown debits |
| `10-social-publishing.md` | Publishing is the **hard gate**; Free cannot publish |
| `05-screens-produce.md` | Teardown depth gated by tier; the "earned win" that triggers a referral prompt |
| `14-appstore-compliance-legal.md` | EULA/Privacy links on the paywall; AI-disclosure consent precedes any paid AI job |
| `15-infra-observability-testing.md` | Paywall + trial + credit events; experiment readout (ARPU, cohort LTV) |

---

## 1. Principles for this surface

1. **Apple IAP is the only purchase path in the iOS app.** Apple requires in-app purchase for digital subscriptions; **Stripe must never appear in the iOS binary or paywall** (`00-overview.md` §7.4). Stripe is reserved for a *future, separate web billing surface* (§9 below). ([App Store Review Guidelines 3.1.1](https://developer.apple.com/app-store/review/guidelines/))
2. **RevenueCat is the source of truth; Supabase mirrors it.** We never call StoreKit directly for the purchase flow. RevenueCat wraps StoreKit 2 (validation, finishing transactions, cross-device sync). We **mirror entitlement + credit state into Supabase via webhooks** so FastAPI/Trigger.dev can gate jobs server-side and the app can read entitlements offline. ([RevenueCat architecture guidance](https://arfin.dev/blog/revenuecat-architecture-guide))
3. **Check entitlements, never product IDs.** App code asks "is `pro` active?", not "did they buy `marque_pro_yearly`?". This lets us re-tier and run experiments without an app update. ([RevenueCat Entitlements](https://www.revenuecat.com/docs/getting-started/entitlements))
4. **Gate the *expensive* pipeline by metered credits, not just a boolean.** Each Opus call, AssemblyAI transcription, Shotstack render, and ClipEngine job is real money. A flat unlimited subscription invites cost blowups. We meter AI + render via **RevenueCat Virtual Currency**, debited from the **backend** before a Trigger.dev job starts (§5).
5. **The paywall obeys the anti-clutter doctrine.** One calm screen, one idea, ONE recommended plan, big legible price, gold accent used once. No countdown-timer pressure tactics, no dark patterns — both because it's off-brand and because Apple rejects deceptive paywalls (3.1, 3.1.1).
6. **Everything vendor-specific hides behind a `Billing` adapter** (`01-information-architecture.md`, Principle 2). The rest of the app sees `Entitlements`, `Credits`, and `Offerings` — never `Purchases.shared`.

---

## 2. The three-object model: Products → Entitlements → Offerings

Use these **exact terms** throughout the codebase and dashboard. ([Configuring Products](https://www.revenuecat.com/docs/projects/configuring-products), [Offerings overview](https://www.revenuecat.com/docs/offerings/overview))

- **Products** — *what we sell*. A specific SKU configured in App Store Connect and registered in RevenueCat (e.g. `marque_pro_yearly`, `marque_credits_100`).
- **Entitlements** — *stable access levels*. The thing app code checks. Marque has two: **`pro`** and **`studio`**. A product *unlocks* one or more entitlements.
- **Offerings → Packages** — *how we display/sell*. An Offering is a named bundle of Packages (monthly / annual / credit-pack) shown on a paywall. We reference `offerings.current` dynamically and **never hardcode product IDs in the app**, because RevenueCat Paywalls, Experiments, and Targeting all require Offerings. ([Offerings overview](https://www.revenuecat.com/docs/offerings/overview))

### 2.1 Entitlement hierarchy (Pro / Studio)

Marque is a justified **two-entitlement** case. The rule:

> **A Studio purchase grants BOTH `pro` and `studio`.** A Pro purchase grants `pro` only.

Two implementation options; we choose **(A)** for clarity:

| Option | Mechanism | Why / why not |
|---|---|---|
| **(A) — chosen** | Attach **both** `pro` and `studio` entitlements to every Studio product in the RevenueCat dashboard. | App code does a flat `entitlements["studio"]?.isActive`. No hierarchy logic to maintain; Studio users transparently get every Pro gate. |
| (B) | Attach only `studio` to Studio products; resolve hierarchy in code (`isStudio || isPro`). | One fewer dashboard config, but spreads the tier rule across the codebase — violates "check the entitlement" simplicity. |

### 2.2 Canonical identifiers (binding)

> Bundle id, App Store Connect group naming, and the RevenueCat project live in `01-information-architecture.md` / infra. **Prices are Open Questions (§13)** — do not hardcode.

| Concept | Canonical id | Type |
|---|---|---|
| Entitlement — Pro | `pro` | entitlement |
| Entitlement — Studio | `studio` | entitlement |
| Product — Pro monthly | `marque_pro_monthly` | auto-renewable sub |
| Product — Pro annual | `marque_pro_yearly` | auto-renewable sub |
| Product — Studio monthly | `marque_studio_monthly` | auto-renewable sub |
| Product — Studio annual | `marque_studio_yearly` | auto-renewable sub |
| Product — credit pack (small) | `marque_credits_100` | **consumable** |
| Product — credit pack (large) | `marque_credits_500` | **consumable** |
| Offering — default paywall | `default` | offering |
| Offering — onboarding value-moment | `onboarding` | offering |
| Subscription group (App Store Connect) | `marque_subscriptions` | sub group |
| Virtual currency — AI credits | `INK` | VC code |
| Virtual currency — render credits | `REEL` | VC code |

> **Subscription group design matters.** All four subscriptions live in **one** App Store Connect subscription group (`marque_subscriptions`) so that Pro↔Studio and monthly↔annual are **upgrades/downgrades/crossgrades within a group** (proration handled by Apple), and so the **one-introductory-offer-per-group-per-user** rule (§6) is scoped sanely. Consumable credit packs are **not** in a subscription group.

### 2.3 SDK configuration & entitlement check (Swift, iOS 17+, Observation)

Configure once at launch. The iOS public SDK key is prefixed **`appl_`** (this is a *public* client key, safe to ship — it is not a secret; the secret/server key stays backend-side and is never in the binary). ([RevenueCat setup](https://mobileapp.wiki/en/monetization/revenuecat-integration-guide))

```swift
// AppDelegate / App init — runs once.
Purchases.logLevel = .info
Purchases.configure(
    with: Configuration.Builder(withAPIKey: "appl_REDACTED_PUBLIC_KEY")
        .with(appUserID: nil)            // anonymous until Supabase auth; then identify()
        .build()
)
```

```swift
// Billing adapter surface the rest of the app sees (01-information-architecture.md, Principle 2).
@MainActor @Observable
final class Entitlements {
    enum Tier { case free, pro, studio }
    private(set) var tier: Tier = .free
    private(set) var billingIssueSince: Date?     // drives the dunning banner (§10)
    private(set) var willRenew: Bool = true       // false after a cancel → win-back surface

    func refresh() async {
        guard let info = try? await Purchases.shared.customerInfo() else { return }
        apply(info)
    }

    func apply(_ info: CustomerInfo) {
        if info.entitlements["studio"]?.isActive == true { tier = .studio }
        else if info.entitlements["pro"]?.isActive == true { tier = .pro }
        else { tier = .free }
        let active = info.entitlements["studio"] ?? info.entitlements["pro"]
        billingIssueSince = active?.billingIssueDetectedAt
        willRenew = active?.willRenew ?? true
    }
}
```

> **DON'T:** `if productId == "marque_pro_monthly" { … }`. Re-tiering or an Apple ID change silently breaks the app.
> **DON'T:** trust *only* the client. If the network drops right after purchase, `customerInfo` may not have updated. RevenueCat is the source of truth, but **server-side gates (FastAPI/Trigger.dev) read the Supabase mirror** (§7), which is updated by webhooks — not by the client.

---

## 3. Tier design & exact feature gates

Three tiers, each mapped to an entitlement (or its absence). **Free** is a *taste*, engineered to deliver the first value moment (§8) and then hit a wall exactly where money starts being spent on the creator's behalf (publishing + bulk render). **Pro** is the hero "film once → post all week" loop. **Studio** raises the credit ceiling and unlocks the cost-heavy formats.

### 3.1 Feature-gate matrix

| Capability | Free (taste) | Pro (`pro`) | Studio (`studio`) | Gating mechanism |
|---|---|---|---|---|
| Brand Graph onboarding & seed | ✅ full | ✅ | ✅ | none (loss-leader; it's the moat hook) |
| Script generation (in-voice) | **1 total** | included, metered by `INK` | included, higher `INK` | entitlement + `INK` debit |
| Hook Lab (nested in script reader) | preview only (1 hook variant) | ✅ all variants | ✅ all variants | entitlement |
| Trend Radar — Today line | ✅ (one line) | ✅ | ✅ | none |
| Trend Radar — dedicated screen & history | ❌ | ✅ | ✅ | entitlement |
| Batch Record + teleprompter | ✅ (record allowed) | ✅ | ✅ | none (recording is free; it's *rendering* that costs) |
| Repurpose-in (upload existing long video) | ❌ | ✅ | ✅ | entitlement |
| ClipEngine render | **1 watermarked single clip** | metered by `REEL` | metered by `REEL`, higher ceiling | entitlement + `REEL` debit + watermark flag |
| Watermark on output | **always on** | off | off | entitlement |
| Formats: talking-head, split-screen, 3-up, before/after, myth-buster, listicle, POV, reaction, B-roll+caption | the 1 free clip = talking-head only | ✅ all | ✅ all | entitlement (format allow-list) |
| Formats: **faceless AI-visual, green-screen** (image/video generation = highest unit cost) | ❌ | ❌ | ✅ | entitlement (Studio-only allow-list) — see `08-format-virality.md` |
| Scheduling & publishing to IG + TikTok | ❌ **hard gate** | ✅ | ✅ | entitlement (the wall) — see `10-social-publishing.md` |
| Performance teardown cards (Coach) | ❌ | ✅ standard | ✅ advanced (deeper analysis) | entitlement |
| Insights archive / history | ❌ | ✅ | ✅ | entitlement |
| Priority render queue | ❌ | standard | **priority** | entitlement (Trigger.dev queue tag) |
| Monthly credit grant | — | `INK`/`REEL` grant, refilled each renewal | higher `INK`/`REEL` grant | virtual-currency deposit-on-renewal (§5) |
| Buy more credits (consumable packs) | ❌ (no payment method yet) | ✅ | ✅ | consumable products `marque_credits_*` |

> **The wall is deliberately placed at publishing**, not at recording or even at the first render. The creator must *feel* the full loop — script in their voice, film once, see a finished clip — before the gate. That is value-first gating (§8), worth 20–40% over a cold paywall. ([Airbridge — structural paywall decisions](https://www.airbridge.io/en/blog/paywall-conversion-structural-decisions))

### 3.2 Why credits on top of tiers

A subscription alone cannot bound unit cost: a single Studio user generating faceless AI-visual clips all day would torch margin (Opus + image/video generation + Shotstack + R2/Stream egress). So **the subscription grants a monthly *credit budget*; heavy users top up**. This is honest, Apple-compliant, and protects gross margin. See §5 for the mechanism and §13 for the open pricing/grant questions.

### 3.3 Gate honesty (compliance)

Gating must be **upfront and non-deceptive**. Apple rejects "hidden paywalls" and requires pricing be explained before purchase (3.1, 3.1.1). Free users see *what* is locked and *why* ("Publishing is a Pro feature"), never a bait-and-switch. ([Adapty — App Store review checklist](https://adapty.io/blog/how-to-pass-app-store-review/))

---

## 4. Paywall: UX, placement, variants, states

### 4.1 Placement — the #1 lever

Placement beats every visual tweak. Benchmarks (2025): on-first-open trial-start ~18–22%; **after onboarding ~28–34%; after the first value moment ~31–38%** (highest intent). ([ASOhack/Superwall — 2025 trial & paywall data](https://asohack.com/blog/7-day-trial-paywall-conversion-data-2025))

> **Marque's primary paywall fires at the first value moment: immediately after the Brand Graph analysis returns the creator's first in-their-voice viral script and they tap to act on it (record / publish).** That is the instant the promise becomes concrete. See `03-onboarding.md`.

Placement map:

| Placement | Trigger | Style | Offering |
|---|---|---|---|
| **Primary** | First in-voice script delivered → user taps **Record this** or **Publish** | **Soft** (skippable → Free taste) | `onboarding` |
| **Hard gate** | Any attempt to **publish/schedule** while Free | **Hard** (blocking; the wall) | `default` |
| **Contextual** | Tapping a Studio-only format (faceless AI-visual / green-screen) while Pro | **Soft** (upgrade Pro→Studio) | `default` (Studio packages) |
| **Credits** | Backend returns "insufficient credits" (HTTP 402, §5) | **Soft** sheet: refill or upgrade | `default` (credit packs + Studio) |
| **Settings** | "Manage subscription" row | links out to system manage-subscriptions | — |

> **Anti-clutter:** the primary paywall is **soft and skippable** to Free taste — the overwhelmed persona is not cornered. The publishing wall is the only **hard** gate, and it appears exactly once the value is undeniable.

### 4.2 Layout (one calm screen)

Modeled on the locked aesthetic (`02-design-system.md`): cream surface `#F4F1EA`, serif (Playfair/Tiempos) headline, grotesque (Inter/Söhne/Matter) body, single gold `#C9A227` accent, huge whitespace, slow eased entrance.

Structure, top → bottom:
1. **Serif headline** — quiet, declarative, one idea. e.g. *"Show up all week. Record once."* (Never "UPGRADE NOW 🔥".)
2. **Three or four value lines** — concrete benefits, not feature lists ("Publish to Instagram & TikTok on a schedule," "Every viral format," "A coach that learns what works").
3. **Plan selector** — **annual default-selected**, "Most Popular" gold badge on annual, monthly behind a quiet "**See all plans**" link. **Annual headline shows the total billed up front** (Apple rule, §6) with a faint per-month breakdown beneath.
4. **Primary CTA** — "**Start free trial**" (trial-start optimized). On the publishing hard gate, "**Continue**" is A/B-tested (can win trial→paid).
5. **Required legal strip** (always visible, in-binary): renewal terms sentence + **Terms of Use (EULA)** + **Privacy Policy** + **Restore Purchases** (§6).
6. **Dismiss affordance** for soft paywalls (a calm "Not now," never a tiny hidden ×).

> **Plan-structure tactics that move revenue:** default-select annual, "Most Popular" badge, hide monthly behind a link, anchor annual against monthly. ([RevenueCat — guide to mobile paywalls](https://www.revenuecat.com/blog/growth/guide-to-mobile-paywalls-subscription-apps/), [MWM — paywall best practices](https://mwm.ai/guides/paywall-design-best-practices))

### 4.3 Remote configuration & experiments

The paywall is **remotely configured from day one** via RevenueCat Paywalls + Offerings (`00-overview.md` §7.4). We A/B test through **RevenueCat Experiments / Targeting** (which require Offerings). Marking an Offering "default" in the dashboard makes it the `currentOffering`. ([Offerings overview](https://www.revenuecat.com/docs/offerings/overview))

**Experiment discipline (acceptance criteria for any paywall change):**
- **Test ONE variable at a time** (placement → price → trial → copy; never two at once).
- **≥200 paid conversions per variant AND ≥2 calendar weeks** minimum.
- **For any price or trial-length change, run 4–8 weeks** to capture ≥1 renewal cycle.
- **Decide on ARPU + 30/180-day cohort LTV — not trial-start or trial→paid in isolation.** The median app converts ~1.9% of downloads to paid in 35 days; the top decile ~8.5% — the gap is **structural** (placement/packaging), not cosmetic (button color). ([Airbridge — structural decisions](https://www.airbridge.io/en/blog/paywall-conversion-structural-decisions), [Adapty — experiments playbook](https://adapty.io/blog/paywall-experiments-playbook/))

### 4.4 Paywall States (required)

| State | Trigger | Behavior |
|---|---|---|
| **Loading** | `offerings()` not yet returned | Skeleton plan cards with the breathing shimmer (`02-design-system.md`); CTA disabled. Offerings are **pre-fetched at launch** so this is usually invisible. |
| **Ready** | `offerings.current` present | Render packages dynamically from the Offering; annual default-selected. |
| **Empty / no offerings** | StoreKit returns no products (misconfig, store outage, sandbox lag) | Graceful copy: "Plans are taking a moment." Retry button. **Never** show a broken empty screen; log to Sentry; let the user continue to Free. |
| **Purchasing** | User tapped CTA, payment sheet up | CTA → spinner; disable dismiss; do not double-fire. |
| **Success** | Purchase completes / `customerInfo` shows entitlement | Slow eased confirmation; dismiss paywall; unlock the action they were attempting. |
| **Cancelled** | User cancels the system sheet | Return to paywall silently; no guilt copy. |
| **Error** | Purchase throws (network, declined, pending) | Quiet inline message: "Couldn't complete that. Try again or restore." Offer **Restore Purchases**. Log to Sentry. |
| **Offline** | No connectivity at present time | If a *cached* entitlement exists, honor it (read from Supabase mirror / last `customerInfo`); else show the empty-offerings state with retry. Never lock out a paid user offline. |
| **Already subscribed** | User already has the entitlement | Don't show the paywall at all; if reached, show "You're on Pro/Studio" + manage link. |
| **Ineligible for intro** | `checkTrialOrIntroductoryEligibility` says lapsed user used the intro | Show **base price** (no "free trial" wording) — required, see §6. |

---

## 5. Usage metering & credits (RevenueCat Virtual Currency)

This is the **single most stack-relevant mechanism** in monetization. RevenueCat **Virtual Currency** (GA'd out of its Aug 2025 beta) is purpose-built for AI/usage-based apps and lets a subscription *deposit* credits on renewal while consumable packs *top up*. ([Virtual Currency announcement](https://www.revenuecat.com/blog/company/revenuecat-virtual-currency/), [VC docs](https://www.revenuecat.com/docs/offerings/virtual-currency), [Monetize your AI app with VC](https://www.revenuecat.com/blog/engineering/how-to-monetize-your-ai-app-with-virtual-currencies/))

### 5.1 Two currencies, mapped to our two cost drivers

| Currency | Code | Debited by | Cost driver behind it |
|---|---|---|---|
| **AI credits** | `INK` | Script generation, Hook Lab variants, teardown depth | Claude Opus 4.8 / Haiku 4.5 (`07-ai-system.md`) |
| **Render credits** | `REEL` | A ClipEngine render job (per output clip; recipe-weighted) | AssemblyAI + Shotstack + MCP ClipEngine + R2/Stream (`08-format-virality.md`) |

VC platform limits to design within: **up to 100 currencies/project; balance 0–2,000,000,000; no negative balances.** ([VC docs](https://www.revenuecat.com/docs/offerings/virtual-currency))

### 5.2 Deposits

- **Subscriptions deposit on every renewal.** Pro/Studio grant a monthly `INK`/`REEL` budget; the deposit recurs each billing cycle.
- **Consumable packs deposit on purchase.** `marque_credits_100` / `marque_credits_500` top up between cycles.
- **Free-trial grant toggle — set deliberately.** RevenueCat has a dashboard toggle for whether to grant currency at **free-trial start** vs **only on paid renewal**. **Marque grants a small starter `INK`/`REEL` allotment at trial start** (so the trial *works*) but the **full monthly budget deposits only on the first paid renewal** — this prevents trial-credit abuse (sign up, drain, churn). Tune in §13.

### 5.3 Expiring vs non-expiring — the Apple boundary (rejection-grade)

> **CRITICAL.** Apple Guideline **3.1.1**: *"credits or in-game currencies purchased via in-app purchase may not expire,"* and a restore mechanism is required. RevenueCat's auto-expire is designed for **granted/subscription** credits — **not** for **purchased** packs. ([App Store Review Guidelines 3.1.1](https://developer.apple.com/app-store/review/guidelines/))

Resolution, **binding**:

| Credit source | Expiry setting | Rationale |
|---|---|---|
| **Subscription-granted** monthly `INK`/`REEL` | **Auto-expire at end of billing cycle** (RevenueCat "expiring currency") | A recurring *benefit*, not a purchased balance. Expiring prevents liability accumulation. |
| **Purchased consumable packs** (`marque_credits_*`) | **NEVER expire** (non-expiring bucket) | Apple 3.1.1 forbids expiring *purchased* credits. **Do not toggle expiry on these — it is a rejection risk.** |

RevenueCat's deduction priority makes this automatic: it spends **expiring-first, soonest-expiring-first, then non-expiring**. So a user's monthly grant burns down before their purchased packs — exactly what we want. ([Expiring currencies](https://www.revenuecat.com/docs/offerings/virtual-currency/expiring-currencies))

### 5.4 Spend from the BACKEND, never the client

The client can spoof a balance; **all debits happen in FastAPI before a Trigger.dev job runs**, and refunds on failure. Flow (per the RevenueCat AI tutorial): ([Monetize your AI app with VC](https://www.revenuecat.com/blog/engineering/how-to-monetize-your-ai-app-with-virtual-currencies/))

```
iOS taps "Generate scripts" / "Render clips"
  → POST /jobs/render  (FastAPI)
      1. Resolve app_user_id ← Supabase auth
      2. Compute cost  ← recipe weight table (§5.6)
      3. RevenueCat Server API: getBalance(currency=REEL, customerID)
      4. if balance < cost  → return HTTP 402  (client shows refill/upgrade sheet)
      5. POST /virtual_currencies/transactions  (NEGATIVE adjustment = debit)
      6. enqueue Trigger.dev run  (08-publishing / 06-format-library)
      7. on job FAILURE  → POST positive adjustment (REFUND) + emit ledger row
  → 200 { job_id }
```

- **HTTP 402 Payment Required** is the canonical "out of credits" signal to the client → opens the **Credits** soft paywall (§4.1).
- **Debit then run; refund on failure.** Never run-then-charge (a crash mid-job would give free output).
- **Reconcile in Supabase.** Every debit/refund/grant also writes a row to our own `credit_ledger` (§7) for support, fraud monitoring, and audit. The authoritative balance is RevenueCat's; the ledger is our explainable mirror.

### 5.5 Client cache gotcha

`Purchases.shared.virtualCurrencies()` is **cached and does NOT auto-update after a backend debit**. After **any** server-side balance change, the client must:

```swift
Purchases.shared.invalidateVirtualCurrenciesCache()
let balances = try await Purchases.shared.virtualCurrencies()   // refetch
```

In practice: the job-submit response carries the new balance, and the app invalidates + refetches on the credits screen and after any job-state webhook. ([VC docs](https://www.revenuecat.com/docs/offerings/virtual-currency))

### 5.6 Recipe-weighted cost table (config, not code)

Render cost is **per output clip, weighted by recipe** because a faceless AI-visual clip (image+video generation) costs far more than a talking-head cut. This table lives in **remote config** (`15-infra-observability-testing.md`) so we can re-price unit costs without an app release.

| Action | Currency | Cost (units) — *illustrative, tune in §13* |
|---|---|---|
| Script generation (1 batch, in-voice) | `INK` | 1 |
| Hook Lab — full variant set | `INK` | 1 |
| Teardown (advanced, Studio) | `INK` | 1 |
| Render — talking-head / captions | `REEL` | 1 |
| Render — split-screen / 3-up / before-after / listicle / POV / reaction / B-roll | `REEL` | 1–2 |
| Render — **faceless AI-visual / green-screen** (generation-heavy, Studio) | `REEL` | 3–5 |

### 5.7 VC events

The **`VIRTUAL_CURRENCY_TRANSACTION`** webhook fires on grant / spend / refund. We consume it to (a) reconcile `credit_ledger`, (b) drive fraud monitoring (drain-then-churn patterns), and (c) keep the Supabase balance cache warm. ([Webhook event types & fields](https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields))

---

## 6. App Store rules: disclosures, restore, trials, anti-steering

Every item below is **rejection-grade** — each is cited in real App Review rejections. ([Apple subscriptions](https://developer.apple.com/app-store/subscriptions/), [Precheck — IAP/3.1.1 guide](https://precheck.tools/platforms/apple-app-store/apple-iap-subscription-guide/), [App Store Review Guidelines](https://developer.apple.com/app-store/review/guidelines/))

### 6.1 Mandatory paywall elements (in the app binary, not only the StoreKit modal)

- Subscription **name + duration + what's delivered each period**.
- **Full renewal price**, clearly/prominently, localized. **Annual must show the total billed up front** (a per-month breakdown may also appear, but the headline is the annual total).
- The sentence: **"Auto-renews unless turned off ≥24h before the end of the period"** + how to cancel (manage in Account Settings).
- **Functional links to Terms of Use (EULA) and Privacy Policy**, present on the paywall/subscription screen **in the binary**. Apple's standard EULA is acceptable: `https://www.apple.com/legal/internet-services/itunes/dev/stdeula/`. (Our Privacy Policy link → see `14-appstore-compliance-legal.md`.)
- A **visible "Restore Purchases"** control and a sign-in path for existing subscribers.
- If a **free trial** is offered: state the **duration**, **what becomes inaccessible at trial end**, the **downstream charge**, and that any **unused trial portion is forfeited** upon subscribing.
- Subscription period **≥7 days**, available across all the user's devices (3.1.2(a)).
- Show all plans on one screen; terms partly visible without scrolling; recommended plan highlighted; legible font.

### 6.2 Restore — use the SDK, never roll your own

Restore is **mandatory on iOS**, and **two distinct recovery mechanisms** are at play — keep them separate.

1. **Subscription entitlement → `restorePurchases()`.** `Purchases.shared.restorePurchases()` synchronizes the App Store receipt and **re-grants the `pro` / `studio` entitlement** transparently. This is the mandatory restore control on the paywall (§6.1). **Do not build a custom restore flow for the subscription.** ([RevenueCat setup](https://mobileapp.wiki/en/monetization/revenuecat-integration-guide))
2. **Purchased credit-pack (consumable) balances → account linkage, NOT receipt restore.** The credit packs (`marque_credits_100` / `marque_credits_500`) are declared **consumable** (§2.2), and **consumables do not persist on the App Store receipt** once consumed — so `restorePurchases()` does **not** re-grant credit balances. RevenueCat documents that *"consumables and non-renewing purchases can only be restored by using an account system"* with a stable, custom app user id. Marque already satisfies this: the `Purchases.shared.logIn(supabaseUserId)` call (§7.6) keys the RevenueCat customer to the Supabase user id, and the purchased-pack balance lives in the **RevenueCat Virtual Currency balance + our server-side `credit_ledger` (§7.5)** under that id. On a new device, the user signs into Supabase → `logIn(supabaseUserId)` → the server-held balance is theirs again. Recovery of purchased credits therefore rides on **identity + the backend ledger**, never on the Apple receipt. ([RevenueCat — restoring purchases](https://www.revenuecat.com/docs/getting-started/restoring-purchases), [RevenueCat setup](https://mobileapp.wiki/en/monetization/revenuecat-integration-guide))

```swift
// Restores the SUBSCRIPTION ENTITLEMENT only.
let info = try await Purchases.shared.restorePurchases()
entitlements.apply(info)            // updates tier (pro/studio). Does NOT recover credit-pack balances.

// Purchased credit packs are NOT recovered here — they return via the stable Supabase-keyed
// app_user_id (logIn, §7.6) + the RevenueCat Virtual Currency balance / credit_ledger (§7.5),
// because consumed consumables do not persist on the App Store receipt.
```

### 6.3 Intro / promotional / win-back offer matrix (StoreKit 2)

([RevenueCat — iOS subscription offers](https://www.revenuecat.com/docs/subscription-guidance/subscription-offers/ios-subscription-offers), [Apple — set up introductory offers](https://developer.apple.com/help/app-store-connect/manage-subscriptions/set-up-introductory-offers-for-auto-renewable-subscriptions/), [RevenueCat — win-back offers](https://www.revenuecat.com/blog/growth/guide-to-apple-win-back-offers/))

| Offer type | Audience | Auto-applied? | Marque use | Key constraints |
|---|---|---|---|---|
| **Introductory** (free trial / pay-up-front / pay-as-you-go) | **New** users | **Yes**, Apple applies automatically to eligible purchases | **7-day free trial** as default (§6.4) | Only **one** intro offer **per subscription group, per user, EVER**. Only **one** intro active at a time (a trial **OR** a discount, not both). Requires the **In-App Purchase Key uploaded to RevenueCat** for StoreKit 2. Free-trial durations: 3d, 1w, 2w, 1mo, 2mo, 3mo, 6mo, 1yr. |
| **Promotional** | Existing / lapsed | **No** — you fetch & present | Targeted win-back to voluntary churners | Present on our own paywall. |
| **Offer Codes** | New + existing | redemption sheet | Partnerships / creator referrals (§11) | iOS SDK 3.8.0+. |
| **Win-Back** | **Lapsed** | StoreKit surfaces; SDK can auto-present or we present | Re-engage churned subs (§10) | **iOS 18.0+ only**, StoreKit 2 only, IAP key uploaded to RevenueCat, **product must already be App-Review-approved**. Eligibility (Min Paid Duration / Time Since Last Subscribed / Wait Between Offers) configured in App Store Connect; Apple checks eligibility **server-side** before the sheet. |

**Eligibility check before rendering "free trial" copy:** call iOS-only `checkTrialOrIntroductoryEligibility` and only show trial wording to eligible users. **Lapsed users who already consumed the intro for that group are NOT eligible → show base price.** Failing to do this is both a UX lie and a review risk.

### 6.4 Trial length — present both findings, default to 7-day, then test

The data is **genuinely contested** and the writer/PM should not assert a winner:

| Source | Finding | What it measures |
|---|---|---|
| Superwall 2025 | **7-day trials convert best at ~5.2% trial-start**; shorter trials often lift trial→paid (user hasn't forgotten the charge). | trial-**start** rate | ([ASOhack/Superwall](https://asohack.com/blog/7-day-trial-paywall-conversion-data-2025)) |
| RevenueCat (75k-app dataset, via Airbridge) | **Longer 17–32 day trials hit ~45.7% trial→paid vs ~26.8% for 3–7 day.** | trial→**paid** rate | ([Airbridge](https://www.airbridge.io/en/blog/paywall-conversion-structural-decisions)) |

> **Decision:** **default to a 7-day free trial**, then **A/B test 3-day vs 7-day** (and consider a longer arm) **measured on ARPU and cohort LTV, not conversion rate alone**, per §4.3 discipline.

### 6.5 Anti-steering & the Stripe boundary (get this exactly right)

- The legal state here is **in flux — do not treat US zero-commission as permanent.** After the **April 30, 2025** Gonzalez Rogers contempt ruling and Apple's guideline update, **US-storefront** apps were permitted to include **external purchase links/buttons and steering language with no entitlement and no Apple commission** on those external purchases. **That zero-commission state is temporary.** On **December 11, 2025**, the **Ninth Circuit Court of Appeals upheld the contempt finding but modified the remedy**: it held that a *permanent* ban on commissions for US external-link purchases was **overbroad**, ruled that **Apple may charge a "reasonable commission"** on those purchases, and **remanded to the district court to set the rate.** As of this document's date (**2026-06-29**), Apple **cannot yet charge a commission on US external-link purchases only because the district court has not yet approved a fee** — a procedural gap, not a settled prohibition. **Plan on a forthcoming US commission**: any future external-link economics must assume Apple will charge a court-set "reasonable" fee on US link-outs once the remand resolves. ([The Verge — Apple allows external purchases (Apr 2025 ruling)](https://www.theverge.com/news/660025/apple-changes-app-store-rules-to-allow-external-purchases), [MacRumors — Ninth Circuit modifies remedy, Dec 11 2025](https://www.macrumors.com/2025/12/11/apple-epic-appeals-court-ruling/), [Perkins Coie — Ninth Circuit external-link decision analysis](https://www.perkinscoie.com/insights/update/ninth-circuit-apple-epic-games-app-store-commission), [Guidelines 3.1.1(a)](https://developer.apple.com/app-store/review/guidelines/))
- **Outside the US**, the historic prohibition still applies unless you hold the **StoreKit External Purchase Link Entitlement** (and Apple charges a commission — e.g. 27% / 12% small-biz — on those link-outs). The US "no commission" window is therefore the *exception*, and a narrowing, temporary one — not the baseline.
- **Marque v1: Apple IAP is the ONLY purchase path** — simplest and globally compliant, and **insulated from this unsettled fee fight.** Any external-link steering is a **future, storefront-conditional** option, **not v1** — and its business case must be modeled against a **forthcoming US commission**, not today's temporary zero-commission state.
- **Stripe must never appear in the iOS app or paywall** (`00-overview.md` §7.4). It is reserved for a **future, separate web billing surface** (§9). If iOS ever links to it, that becomes external-purchase steering subject to the storefront rules above.

---

## 7. Server-side validation, webhooks & the Supabase entitlement mirror

### 7.1 RevenueCat already does receipt validation — don't roll your own

Apple's `verifyReceipt` is **deprecated** (WWDC23). Modern validation is the **App Store Server API + App Store Server Notifications V2** (JWS-signed), which **RevenueCat consumes for you**. We do **not** implement Apple receipt validation. ([Apple — WWDC23 meet StoreKit / verifyReceipt deprecation](https://developer.apple.com/videos/play/wwdc2023/10141/))

### 7.2 App Store Server Notifications V2 wiring

In App Store Connect → App Information → **App Store Server Notifications**, paste the **RevenueCat URL into BOTH Production and Sandbox** fields, using **V2 (not V1; V1 is deprecated)**. Apple allows only ONE URL per environment, so RevenueCat receives Apple's notifications and forwards normalized events to us. V2 enables auto price-change detection + reliable refund handling. ([RevenueCat — Apple server notifications](https://www.revenuecat.com/docs/platform-resources/server-notifications/apple-server-notifications), [Apple — ASSN](https://developer.apple.com/documentation/appstoreservernotifications))

### 7.3 Our webhook target = a FastAPI endpoint (RevenueCat → Marque)

We consume **RevenueCat Webhooks** (normalized JSON — *not* raw Apple JWS) at a FastAPI endpoint (or Supabase Edge Function) to update the Supabase entitlement + credit mirror. Reliability rules:

- **Verify the webhook signature/authorization header.**
- **Return 2xx immediately; process async** (hand off to Trigger.dev). RevenueCat **retries with exponential backoff** and re-delivers if the handler is slow — so the handler must be idempotent (`event.id` dedupe).
- **Tolerate `TEMPORARY_ENTITLEMENT_GRANT`** (below) so creators aren't locked out mid-render during an Apple outage.

### 7.4 Events to handle

([Webhook event types & fields](https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields))

| Event | Action in Marque |
|---|---|
| `INITIAL_PURCHASE` | Set tier; deposit first paid budget (if not trial); welcome push. |
| `RENEWAL` | Extend entitlement; deposit monthly `INK`/`REEL`. **Also fired on resubscribe-after-expiry** — grant logic must treat it like a fresh entitlement, or returning users won't re-entitle. |
| `PRODUCT_CHANGE` | Pro↔Studio or monthly↔annual crossgrade; re-resolve tier + budget. |
| `CANCELLATION` | Mark `will_renew=false`; read `cancel_reason` (`UNSUBSCRIBE` / `BILLING_ERROR` / `DEVELOPER_INITIATED` / `PRICE_INCREASE`) → routes win-back targeting (§10). **Access continues until period end.** |
| `UNCANCELLATION` | Clear `will_renew=false`. |
| `BILLING_ISSUE` | Fires **once per failure episode**; carries `grace_period_expiration_at_ms`. Flag account; drive in-app banner + dunning push (§10). |
| `EXPIRATION` | Revoke entitlement (only after grace, if enabled). Downgrade to Free. |
| `SUBSCRIPTION_EXTENDED` | Apple granted an extension (e.g. outage); extend entitlement. |
| `REFUND_REVERSED` | Re-grant a previously refunded entitlement. |
| `NON_RENEWING_PURCHASE` | A consumable credit pack bought → no tier change; reconcile pack deposit. |
| `TEMPORARY_ENTITLEMENT_GRANT` | RevenueCat grants **≤24h** access during a store outage; later resolves to `INITIAL_PURCHASE` or `EXPIRATION`. Grant temporary access; do **not** deposit a recurring budget yet. |
| `VIRTUAL_CURRENCY_TRANSACTION` | Reconcile `credit_ledger`; fraud signal (§5.7). |

> With Server Notifications configured, `EXPIRATION` fires within **seconds–minutes**; without them, up to **~1h** delay — another reason §7.2 is mandatory.

### 7.5 Supabase mirror — schema

The mirror lets FastAPI/Trigger.dev gate jobs server-side, lets the app read entitlements offline, and gives support an explainable view. **RevenueCat remains source of truth**; rows are upserted by webhook (idempotent on `rc_event_id`).

```sql
-- Mirror of the user's current paid state. One row per user.
create table public.entitlements (
    user_id              uuid primary key references auth.users(id) on delete cascade,
    rc_app_user_id       text not null,                 -- RevenueCat appUserID == Supabase user id after identify()
    tier                 text not null default 'free'   -- 'free' | 'pro' | 'studio'
                         check (tier in ('free','pro','studio')),
    active_entitlements  text[] not null default '{}',  -- e.g. {'pro'} or {'pro','studio'}
    product_id           text,                          -- current sub product
    period_type          text,                          -- 'trial' | 'intro' | 'normal'
    will_renew           boolean not null default true,
    current_period_end   timestamptz,                   -- entitlement valid through
    billing_issue_since  timestamptz,                   -- non-null while in a billing issue / grace
    in_grace_period      boolean not null default false,
    grace_expires_at     timestamptz,
    last_rc_event_id     text,                          -- idempotency / dedupe
    updated_at           timestamptz not null default now()
);

-- Cached VC balances (authoritative balance is RevenueCat; this is a warm read cache).
create table public.credit_balances (
    user_id        uuid not null references auth.users(id) on delete cascade,
    currency       text not null check (currency in ('INK','REEL')),
    balance        bigint not null default 0 check (balance >= 0),  -- mirrors RC: no negatives
    updated_at     timestamptz not null default now(),
    primary key (user_id, currency)
);

-- Explainable ledger of every grant/spend/refund (our audit + support + fraud view).
create table public.credit_ledger (
    id             bigint generated always as identity primary key,
    user_id        uuid not null references auth.users(id) on delete cascade,
    currency       text not null check (currency in ('INK','REEL')),
    delta          bigint not null,                     -- negative = debit, positive = grant/refund
    reason         text not null,                       -- 'render:faceless' | 'grant:renewal' | 'refund:job_fail' | ...
    job_id         text,                                -- Trigger.dev run id, when applicable
    rc_event_id    text,                                -- VIRTUAL_CURRENCY_TRANSACTION id, if webhook-sourced
    created_at     timestamptz not null default now()
);

-- RLS: a user reads only their own rows; writes come from the service role (webhooks/jobs).
alter table public.entitlements    enable row level security;
alter table public.credit_balances enable row level security;
alter table public.credit_ledger   enable row level security;
create policy "own entitlements" on public.entitlements
    for select using (auth.uid() = user_id);
create policy "own balances" on public.credit_balances
    for select using (auth.uid() = user_id);
create policy "own ledger" on public.credit_ledger
    for select using (auth.uid() = user_id);
```

### 7.6 Identity linkage

On Supabase sign-in, call `Purchases.shared.logIn(supabaseUserId)` so the RevenueCat **appUserID == Supabase user id**. This (a) makes the webhook → `entitlements.user_id` join trivial, (b) syncs entitlements across the user's devices, and (c) lets the server `getBalance(customerID = supabaseUserId)` for credit debits. Log out on sign-out.

---

## 8. The value-first activation handoff

Monetization rides on activation (`03-onboarding.md`, `00-overview.md` §8). The sequence:

```
Onboarding ──> Brand Graph seed (free) ──> first in-voice script delivered (free)
            ──> [VALUE MOMENT] user taps Record / Publish
            ──> SOFT paywall (offering: onboarding)   ←── primary
                    ├─ Subscribe  → Pro/Studio, trial starts, full loop unlocked
                    └─ Not now    → Free taste: 1 script already used, 1 watermarked clip, NO publish
            ──> Free user hits PUBLISH ──> HARD gate (offering: default)   ←── the wall
```

Acceptance criteria for the handoff:
- The paywall **never** appears before the first in-voice script exists.
- Free taste **always** delivers exactly one finished (watermarked) clip — the persona must *feel* the loop close once.
- The publishing gate is the **only** hard block in v1.

---

## 9. The Stripe-for-web boundary (future, isolated)

- **iOS:** Apple IAP only, behind RevenueCat. **No Stripe code in the iOS target.**
- **Future web surface:** Stripe (or RevenueCat **Web Billing**) may sell the same Pro/Studio entitlements via a browser. To keep the adapter boundary clean (`01-information-architecture.md`), **web and iOS both resolve into the same Supabase `entitlements` model** — RevenueCat can manage Stripe/Web Billing **behind the same entitlement identifiers** (`pro`, `studio`), so the app's entitlement check is unchanged regardless of where the purchase happened.
- **Reconciliation rule:** a user who bought on web and signs into iOS must see their entitlement (via RevenueCat identity + the mirror). A user must **never** be double-charged across surfaces — entitlement is keyed to the RevenueCat customer, not the platform.

---

## 10. Dunning, grace periods & billing recovery

Calm, declarative, non-punitive — in Marque's voice. ([RevenueCat — how grace periods work](https://www.revenuecat.com/docs/subscription-guidance/how-grace-periods-work), [Apple subscriptions — billing retry / 60-day](https://developer.apple.com/app-store/subscriptions/))

### 10.1 Enable Billing Grace Period

Turn on **Billing Grace Period** in App Store Connect (durations: **3, 16, or 28 days** — we choose **16**, §13). During grace the user **keeps access** while Apple retries; if recovered, **no service gap and no revenue lost**. Apple separately retries failed renewals for **up to 60 days**; if recovered within 60d, paid days resume from the renewal date.

### 10.2 Webhook + entitlement behavior

- On payment failure: RevenueCat fires **`BILLING_ISSUE`** (once per episode) carrying `grace_period_expiration_at_ms`, plus a `CANCELLATION` with `cancel_reason=BILLING_ERROR`.
- **With grace enabled:** entitlement is **retained** (`isActive == true`), `billingIssueDetectedAt` is non-null. Recovery → `RENEWAL` (no revocation). Grace expiry without recovery → `EXPIRATION` + revoke.
- **Without grace:** `EXPIRATION` immediately. (So we enable grace.)
- There is a **silent grace window before our configured grace** — design dunning copy for the **visible** window only.

### 10.3 The Marque dunning loop

1. On `BILLING_ISSUE`: set `entitlements.billing_issue_since` + `in_grace_period=true` (Supabase mirror, §7.5).
2. **In-app, non-blocking banner** driven by `EntitlementInfo.billingIssueDetectedAt` (non-null while unresolved). Distinguish **grace** (`isActive==true`, calm banner, full access retained) from **hard-expired** (access lost). Copy: *"Your payment didn't go through. Update your method to keep posting."* — quiet, one line.
3. **APNs push via backend** (`notifications` / `15-infra-observability-testing.md`): the same message, calm, with a deep link to manage-subscriptions. Send **once**, not a barrage.
4. On `RENEWAL` (recovered): clear the flags, dismiss the banner silently — no "welcome back" theatrics.
5. On `EXPIRATION` (failed): downgrade to Free, retain Brand Graph + non-expiring purchased credits (Apple 3.1.1), surface a **win-back** path (§10.4).

### 10.4 Win-back & re-engagement (segment by reason)

Segment churned users by `cancel_reason`:

| `cancel_reason` | Likely cause | Treatment |
|---|---|---|
| `BILLING_ERROR` | Card failed — **not** a value rejection | Payment-fix prompt, **no discount** (don't pay people to fix a card). |
| `UNSUBSCRIBE` | Voluntary — often price/value | **iOS 18 Win-Back Offer** (§6.3) — a discounted re-entry. |
| `PRICE_INCREASE` | Reacted to a price change | Promotional offer / grandfathered price arm (test). |
| `DEVELOPER_INITIATED` | We cancelled (support) | Case-by-case. |

> **Win-back is configured, not coded.** Win-back eligibility lives in App Store Connect / RevenueCat dashboard; there is **no SDK "implementation"** — RevenueCat's SDK can **auto-present** the win-back sheet when StoreKit signals eligibility, or we fetch + present it on our own paywall for messaging control. A redeemed win-back simply arrives as a normal purchase in `CustomerInfo`. ([RevenueCat — win-back offers](https://www.revenuecat.com/blog/growth/guide-to-apple-win-back-offers/))

---

## 11. Referral loop (Section-8 feature #7 — placed, not bolted on)

Per `00-overview.md` §5, the referral loop is **a row in Settings + exactly ONE earned prompt after a genuine win** — never a persistent banner, never on Today.

- **Settings row:** "Invite a creator" → share sheet with the user's referral code (delivered as an **Offer Code**, §6.3, so the invitee redeems via the App Store redemption sheet — keeping it IAP-compliant).
- **The one earned prompt:** fires **once**, immediately after a *genuine* win surfaced by `05-screens-produce.md` (e.g. a teardown shows a posted clip outperformed the creator's baseline). Copy is quiet: *"That one worked. Know a creator who'd want this?"*
- **Reward mechanics** (referrer + invitee benefit) are an **Open Question** (§13) — likely bonus `INK`/`REEL` credits (cheap, on-brand, controllable) rather than free subscription months.
- **No reward may bypass IAP** or constitute deceptive steering. Credits-as-reward stay inside the VC system.

---

## 12. End-to-end acceptance criteria

| # | Criterion |
|---|---|
| 1 | App checks **entitlements** (`pro`/`studio`), never product IDs, everywhere a gate exists. |
| 2 | A **Studio** purchase activates **both** `pro` and `studio` (option A). |
| 3 | `offerings.current` is read dynamically; **no product ID is hardcoded** in the binary. |
| 4 | Paywall fires at the **first value moment** (post in-voice script), is **soft/skippable** there, and **hard** only at publish. |
| 5 | Paywall shows: name+duration+per-period delivery, **full renewal price** (annual total up front), auto-renew sentence, **EULA + Privacy links in-binary**, **Restore Purchases**, trial terms when applicable. |
| 6 | **Restore** uses `restorePurchases()` to re-grant the `pro`/`studio` **entitlement**; purchased credit-pack (consumable) balances are recovered **not** via the App Store receipt but via the stable Supabase-keyed `app_user_id` (`logIn`, §7.6) + the RevenueCat Virtual Currency balance / `credit_ledger` (§7.5). |
| 7 | Intro **eligibility** checked before showing "free trial" copy; lapsed-ineligible users see base price. |
| 8 | Only **one** intro offer per subscription group per user, ever; trial **OR** discount, not both. |
| 9 | **All credit debits happen server-side** (FastAPI) before a Trigger.dev job; insufficient balance → **HTTP 402** → credits sheet. |
| 10 | **Subscription-granted** credits may expire at cycle end; **purchased** consumable packs **never expire** (Apple 3.1.1). |
| 11 | Client **invalidates the VC cache** after any backend balance change, then refetches. |
| 12 | App Store Server Notifications **V2** point to the RevenueCat URL in **both** Production and Sandbox. |
| 13 | Webhook handler verifies signature, returns 2xx fast, processes async, is **idempotent** on `rc_event_id`, and handles **all** events in §7.4 — including `RENEWAL`-on-resubscribe and `TEMPORARY_ENTITLEMENT_GRANT`. |
| 14 | Supabase `entitlements` / `credit_balances` / `credit_ledger` mirror is the **server-side gate** for FastAPI/Trigger.dev; RLS restricts reads to the owner; writes are service-role only. |
| 15 | **No Stripe** code or link in the iOS target. **No external-purchase steering** in v1. |
| 16 | **Billing Grace Period** enabled; dunning banner + single APNs push during the **visible** grace window; calm copy; Brand Graph + purchased credits retained on downgrade. |
| 17 | Win-back targeted by `cancel_reason` (discount only for `UNSUBSCRIBE`/`PRICE_INCREASE`, payment-fix for `BILLING_ERROR`). |
| 18 | Referral = Settings row (Offer Code) + **one** earned prompt; never on Today. |
| 19 | All paywall **States** (§4.4) implemented, incl. offline honoring of cached entitlement and never locking out a paid user offline. |
| 20 | Every paywall/trial/credit/billing event is instrumented to PostHog; experiments decide on **ARPU + cohort LTV** with the §4.3 thresholds. |

---

## 13. Open questions

1. **Exact prices** for `marque_pro_monthly/yearly` and `marque_studio_monthly/yearly`, plus annual discount depth (anchor strategy). *Needs PM + market comp.*
2. **Monthly credit grants** per tier: how many `INK` (AI) and `REEL` (render) units does Pro vs Studio include? And the **trial-start starter allotment** vs **full-on-renewal** split.
3. **Recipe cost weights** (§5.6) — the real unit costs of Opus/Haiku, AssemblyAI, Shotstack, MCP ClipEngine, and R2/Stream egress per format must be measured before finalizing `REEL` weights (coordinate with `08-format-virality.md` / `07-ai-system.md`).
4. **Consumable pack pricing** (`marque_credits_100`, `marque_credits_500`) and unit-per-pack — and whether to add a mid pack.
5. **Trial length default** — 7-day is our starting default; confirm before launch and queue the 3-day vs 7-day (vs longer) experiment.
6. **Grace period duration** — 3 / 16 / 28 days. Proposed **16**; confirm against churn-recovery data.
7. **Referral reward mechanics** — credits (proposed) vs free month; referrer-only vs both-sides; anti-abuse caps.
8. **Studio "priority render queue"** — concrete Trigger.dev concurrency/queue-tag policy and the SLA difference vs standard (define with `01-information-architecture.md`).
9. **Watermark spec** for Free clips — placement/branding (design with `02-design-system.md` / `08-format-virality.md`).
10. **Free taste limits** — confirm "1 script + 1 watermarked clip" is the right taste vs a small recurring monthly free allotment.
11. **Web billing timing** — when (if ever) the Stripe/Web Billing surface ships, and whether it launches with RevenueCat Web Billing to preserve the single-entitlement model.

---

## 14. Sources

- [App Store Review Guidelines (3.1.1 / 3.1.2 / anti-steering)](https://developer.apple.com/app-store/review/guidelines/)
- [Apple — Auto-renewable subscriptions (disclosures, billing grace, 60-day retry)](https://developer.apple.com/app-store/subscriptions/)
- [Apple — Set up introductory offers](https://developer.apple.com/help/app-store-connect/manage-subscriptions/set-up-introductory-offers-for-auto-renewable-subscriptions/)
- [Apple — App Store Server Notifications (ASSN)](https://developer.apple.com/documentation/appstoreservernotifications)
- [Apple — WWDC23: verifyReceipt deprecation / App Store Server API](https://developer.apple.com/videos/play/wwdc2023/10141/)
- [Apple — Standard EULA](https://www.apple.com/legal/internet-services/itunes/dev/stdeula/)
- [RevenueCat — Configuring Products](https://www.revenuecat.com/docs/projects/configuring-products)
- [RevenueCat — Entitlements](https://www.revenuecat.com/docs/getting-started/entitlements)
- [RevenueCat — Offerings overview](https://www.revenuecat.com/docs/offerings/overview)
- [RevenueCat — Virtual Currency (docs)](https://www.revenuecat.com/docs/offerings/virtual-currency)
- [RevenueCat — Expiring currencies](https://www.revenuecat.com/docs/offerings/virtual-currency/expiring-currencies)
- [RevenueCat — Monetize your AI app with Virtual Currencies (backend credit flow)](https://www.revenuecat.com/blog/engineering/how-to-monetize-your-ai-app-with-virtual-currencies/)
- [RevenueCat — Virtual Currency announcement](https://www.revenuecat.com/blog/company/revenuecat-virtual-currency/)
- [RevenueCat — Webhook event types & fields](https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields)
- [RevenueCat — Apple server notifications setup](https://www.revenuecat.com/docs/platform-resources/server-notifications/apple-server-notifications)
- [RevenueCat — iOS subscription offers](https://www.revenuecat.com/docs/subscription-guidance/subscription-offers/ios-subscription-offers)
- [RevenueCat — Apple win-back offers](https://www.revenuecat.com/blog/growth/guide-to-apple-win-back-offers/)
- [RevenueCat — How grace periods work](https://www.revenuecat.com/docs/subscription-guidance/how-grace-periods-work)
- [RevenueCat — Guide to mobile paywalls](https://www.revenuecat.com/blog/growth/guide-to-mobile-paywalls-subscription-apps/)
- [RevenueCat — SDK setup guide](https://mobileapp.wiki/en/monetization/revenuecat-integration-guide)
- [RevenueCat — architecture pitfalls](https://arfin.dev/blog/revenuecat-architecture-guide)
- [ASOhack / Superwall — 2025 trial & paywall conversion data](https://asohack.com/blog/7-day-trial-paywall-conversion-data-2025)
- [Airbridge — paywall conversion structural decisions](https://www.airbridge.io/en/blog/paywall-conversion-structural-decisions)
- [Adapty — paywall experiments playbook](https://adapty.io/blog/paywall-experiments-playbook/)
- [Adapty — how to pass App Store review](https://adapty.io/blog/how-to-pass-app-store-review/)
- [MWM — paywall design best practices](https://mwm.ai/guides/paywall-design-best-practices)
- [Precheck — Apple IAP / 3.1.1 subscription guide](https://precheck.tools/platforms/apple-app-store/apple-iap-subscription-guide/)
- [RevenueCat — Restoring purchases (consumables require an account system)](https://www.revenuecat.com/docs/getting-started/restoring-purchases)
- [The Verge — Apple allows external purchases (Apr 30, 2025 ruling)](https://www.theverge.com/news/660025/apple-changes-app-store-rules-to-allow-external-purchases)
- [Mondaq — legal analysis of the April 2025 external-purchase ruling](https://www.mondaq.com/unitedstates/consumer-law/1620752/apple-violated-us-court-order-ending-apples-27-commission-on-external-purchases)
- [MacRumors — Ninth Circuit upholds contempt but lets Apple charge a commission on external links (Dec 11, 2025)](https://www.macrumors.com/2025/12/11/apple-epic-appeals-court-ruling/)
- [Perkins Coie — Ninth Circuit Apple/Epic external-link commission decision (Dec 2025)](https://www.perkinscoie.com/insights/update/ninth-circuit-apple-epic-games-app-store-commission)
