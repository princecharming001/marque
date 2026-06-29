# 16 — Completeness Checklist: Everything a Shipped App Has

> **Marque** — *turn overwhelmed creators into consistent ones.*
>
> This is the **ship gate**. It is the literal, exhaustive checklist of the unglamorous production pieces that separate a demo from an App-Store-approved, EU-legal, resilient, accessible product. Every line item is a checkbox an engineer or designer can tick. Where a state, asset, or legal artifact is easy to forget, it is enumerated explicitly rather than gestured at.
>
> Marque's surface is calm (`02-design-system.md`) and anti-clutter (`00-overview.md` §6) — but *calm is not the same as incomplete*. A shipped app honors every error path, every empty grid, every permission the user denied, with the same quiet care it gives the happy path. This document is how we prove we did.

**Status:** Canonical · **Owners:** iOS lead + Product designer + QA lead · **Last updated:** 2026-06-29

### How to use this file

- Treat every `- [ ]` as a **release-blocking** item unless it is explicitly tagged `(P1)` (next-release) or `(opt)` (optional/tasteful-extra).
- Each group ends with **Acceptance criteria** and, where relevant, **States** (loading / empty / error / offline / permission-denied / rate-limited).
- This file does not re-specify features — it cross-references the canonical owner doc. The state *matrices* here are authoritative; the *feature behavior* lives in the sibling.

### Sibling map (where this doc reaches)

| File | Why we cross here |
|---|---|
| `00-overview.md` | Anti-clutter doctrine (one banner, not a modal); Section-8 feature placements |
| `02-design-system.md` | Cream `#F4F1EA` / ink `#0E0E10` / gold `#C9A227`; the gold-on-cream contrast conflict; Reduce-Motion alternatives; empty-state illustration system |
| `03-onboarding.md` | First-run / zero-data flows; permission re-request copy |
| `04-screens-create.md` | Record + "repurpose-in" (in-app **Film ⇄ Upload** source toggle; Share Extension is a *second entry point* into the same pipeline — §12); camera/mic/photos permission states |
| `05-screens-produce.md` | Render-job long-running states; Live Activity progress |
| `07-ai-system.md` | Script-generation states; secrets live only in FastAPI |
| `10-social-publishing.md` | IG 25-post cap, TikTok unaudited limits, Publisher/Insights adapter error matrix |
| `11-monetization.md` | Paywall states; Restore Purchases; subscription-lapsed; legal links by the CTA |
| `12-backend-data-security.md` | RLS, Keychain token storage, fan-out deletion job, signed-URL expiry, webhook verification |

> **Doctrine for this layer (one sentence):** *every state the user can reach has a designed screen, every key stays on the backend, and nothing the user created is ever lost or un-deletable.*

---

## 1. Universal state matrix — the six states, every surface

Marque has a small set of **canonical states**. Every data-bearing surface MUST resolve to exactly one of them at any moment. The anti-clutter doctrine means most of these are **one quiet line or one calm illustration**, never a modal stack — but they must *exist*.

| State | Definition | Marque default treatment (locked aesthetic) |
|---|---|---|
| **Loading** | Request in flight, no cached data | Serif title persists; body replaced by a single breathing skeleton (honor `accessibilityReduceMotion` → static placeholder). Never a full-screen spinner. |
| **Empty / zero-data** | Request succeeded, no rows | Calm centered illustration + one declarative line + exactly **one** gold CTA. Never a blank grid. |
| **Error (recoverable)** | Request failed, retry possible | One-line inline message + "Try again" affordance. Keep last-known content visible if cached. |
| **Offline** | `NWPathMonitor` reports no path | Quiet single-line top banner ("You're offline — we'll catch up"), not a modal. Queued actions stay pending. |
| **Permission-denied** | OS permission refused | Tailored re-request copy + deep link to iOS Settings (`UIApplication.openSettingsURLString`). |
| **Rate-limited** | Vendor/platform quota hit | Queue + show quota ("17 of 25 IG posts used today"), never a dead error. |

### 1.1 Per-surface state coverage matrix (release gate)

Each surface MUST have a designed, implemented, and QA-signed screen for every applicable state. `—` = not applicable.

| Surface (owner doc) | Loading | Empty | Error | Offline | Perm-denied | Rate-limited |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Today directive (`00-overview.md`) | ☐ | ☐ | ☐ | ☐ | — | — |
| Brand Graph build (`06-brand-graph.md`) | ☐ | ☐ | ☐ | ☐ | — | — |
| Script generation — Claude (`07-ai-system.md`) | ☐ | ☐ | ☐ | ☐ | — | ☐ |
| Record / teleprompter (`04-screens-create.md`) | ☐ | ☐ | ☐ | — | ☐ camera/mic | — |
| Repurpose-in — in-app toggle (`04-screens-create.md`) | ☐ | — | ☐ | ☐ | ☐ photos | — |
| Repurpose-in — Share Extension (`04-screens-create.md`, §12) | ☐ | — | ☐ | ☐ | — (UTType handoff, no in-app Photos prompt) | — |
| Batch upload → R2 | ☐ | — | ☐ | ☐ | — | — |
| Render job (`05-screens-produce.md`) | ☐ | — | ☐ | ☐ | — | ☐ |
| Publish (`10-social-publishing.md`) | ☐ | — | ☐ | ☐ | ☐ token | ☐ IG/TikTok |
| Trends / Trend Radar | ☐ | ☐ | ☐ | ☐ | — | — |
| Insights / teardown pull | ☐ | ☐ | ☐ | ☐ | — | ☐ |
| Paywall (`11-monetization.md`) | ☐ | — | ☐ | ☐ | — | — |
| Settings → all rows | ☐ | — | ☐ | ☐ | — | — |

**Acceptance criteria — state matrix**
- [ ] No surface can render a blank screen, a raw error string, or an infinite spinner. (QA walks the matrix with network conditioner + revoked permissions.)
- [ ] Every error state emits a PostHog event AND attaches a Sentry breadcrumb (see §11).
- [ ] Offline + reconnect replays queued mutations exactly once (idempotency — see §9).

---

## 2. App Store assets — exact specs (do not guess these)

Apple rejects on metadata as readily as on code. Use the **exact** current specs below; the locked aesthetic and the "virality" positioning create specific compliance traps, flagged inline.

### 2.1 Screenshots & preview video

| Asset | Exact spec | Notes for Marque |
|---|---|---|
| iPhone screenshots | **6.9" = 1290 × 2796 px**, flattened **JPEG/PNG**, RGB, **no alpha** | Apple up-scales from this base; 6.5"/5.5" no longer mandatory ([Apple — screenshot specs](https://developer.apple.com/help/app-store-connect/reference/screenshot-specifications/)) |
| iPad screenshots | **13" = 2064 × 2752 px**, same format rules | Only if we ship an iPad build (see Open Questions) |
| Count | Min **1**, max **10** per localization | — |
| App preview video | **15–30 sec**, **≤ 500 MB**, **H.264 or ProRes 422 HQ**, `.mov`/`.m4v`/`.mp4`, up to **3** per device size per language ([Apple — upload previews](https://developer.apple.com/help/app-store-connect/manage-app-information/upload-app-previews-and-screenshots/)) | **Hero opportunity:** the preview should *literally* be the loop — "film once → a week of clips." Marque's product is video; the preview is our strongest asset. |
| App icon | **1024 × 1024**, **no alpha**, **no rounded corners** (Apple masks) | Generated via single 1024 source → asset catalog for all sizes |

**Screenshot copy compliance (collides with our category):**
- [ ] Overlay text is **factual/descriptive only** — **no superlatives, no "#1"/ranking/award claims, no competitor comparisons** ([Apple — screenshot specs](https://developer.apple.com/help/app-store-connect/reference/screenshot-specifications/)).
- [ ] "Virality" language is rephrased to outcomes, not guarantees: ✅ "Turn one recording into a week of content." ❌ "Go viral, guaranteed."

### 2.2 Text metadata (character limits)

| Field | Limit | Marque guidance |
|---|---|---|
| App name | **30** | "Marque" + short descriptor if room |
| Subtitle | **30** | Declarative, calm: e.g. "Film once. Post all week." |
| Keywords | **100** | Comma-separated, **no spaces** (spaces waste characters) |
| Promotional text | **170** | **Editable without a new build** — wire it to current **Trend Radar** messaging |
| Description | ~4000 | One-idea-per-paragraph; no ranking claims |

- [ ] Keyword field uses commas, zero spaces.
- [ ] Promo text owned by marketing, updatable independent of binary.
- [ ] Localized metadata supplied per shipped locale (see §6).

### 2.3 App Store Connect compliance artifacts

- [ ] **Age rating** questionnaire completed (UGC publishing → declare user-generated content).
- [ ] **Privacy "nutrition label"** (App Privacy) declares all data collection.
- [ ] **Privacy Manifest** `PrivacyInfo.xcprivacy` present and declares third-party SDK data use + required-reason APIs for **RevenueCat, Sentry, PostHog, Supabase** SDKs (any SDK that ships in the binary; AssemblyAI/Shotstack/Ayrshare are backend-only and do not appear here — see `12-backend-data-security.md`).
- [ ] **Export compliance / encryption** question answered (HTTPS-only standard crypto → typically exempt; confirm and document).
- [ ] **Account-deletion** path documented for review (§4) — App Review tests this.

**Acceptance criteria — Store assets**
- [ ] Every required asset present at exact spec; a CI lint checks dimensions/format/alpha before upload.
- [ ] No claim in any asset violates the superlative/ranking rule (legal + product sign-off).

---

## 3. Onboarding, icon & illustration asset inventory

Cross-ref `02-design-system.md` (illustration system) and `03-onboarding.md` (flow).

- [ ] **App icon** — single 1024×1024 master, no alpha, exported through asset catalog to every required size; dark/tinted variants if adopting iOS 18 icon tinting (P1).
- [ ] **Launch screen** — SwiftUI/storyboard launch that **matches the first real screen** (cream `#F4F1EA` light / near-black `#0E0E10` dark). **No spinner-as-launch**, no logo bounce.
- [ ] **Onboarding illustrations** — calm/editorial, honoring cream + serif system, one per onboarding beat ("What do you want to be known for?").
- [ ] **Empty-state illustrations** — one per empty surface in §1.1 (Today, Trends, Insights, clip library, schedule).
- [ ] **Notification content icons** / category glyphs (teardown, published, streak, trend).
- [ ] **Streak gold glyph** asset (the single Today affordance) — with non-color affordance baked in (§5.4 contrast).
- [ ] Both light + dark renditions of every illustration (never pure white/black).

**Acceptance criteria**
- [ ] Launch → first frame has zero visible "pop"/relayout (launch screen pixel-matches Today/onboarding entry).
- [ ] All illustrations pass dark-mode review; no asset assumes a white canvas.

---

## 4. Account: sign-out, data export, deletion (rejection risk)

In-app, user-initiated **account deletion** has been **mandatory since June 30, 2022** for any app with account creation — *deactivate is not deletion*, and "email us" is not acceptable ([Apple — offering account deletion](https://developer.apple.com/support/offering-account-deletion-in-your-app/); [Apple announcement](https://developer.apple.com/news/?id=12m75xbj)). It must be **easy to find** (convention: Settings → Account). This is a hard App Review gate.

### 4.1 Sign-out

- [ ] Clears the Supabase session from **Keychain** (not UserDefaults — `12-backend-data-security.md`).
- [ ] Calls RevenueCat `logOut()`.
- [ ] Clears local caches (React-Query-equivalent / SwiftData store, draft scripts, cached clips).
- [ ] Returns to logged-out / re-auth state (Sign in with Apple).
- [ ] Does **not** delete server data.

### 4.2 Delete account (cascade)

Deletion must remove the account record **+ all associated personal data** not legally required to be retained. For Marque this is a **fan-out durable deletion job** (Trigger.dev — `12-backend-data-security.md`) across every store:

| Store | What gets deleted | Retention exception |
|---|---|---|
| Supabase Postgres | Brand Graph, scripts, schedules, posts, metrics | — |
| Cloudflare R2 / Stream | Raw recordings + rendered clips + thumbnails | — |
| Ayrshare | Stored social connections / tokens (revoke) | — |
| PostHog / Sentry | PII (identify + person profiles) deletion request | — |
| RevenueCat | App-user alias | **Purchase/tax records retained** per legal/financial obligation |

**UX requirements**
- [ ] Lives at **Settings → Account → Delete account**.
- [ ] **Confirm-text** step (type "DELETE") + irreversible warning copy.
- [ ] Active-subscription warning: deleting the account does **not** cancel the Apple subscription — instruct user to cancel in System Settings (Apple owns billing).
- [ ] **Receipt screen** confirming the request, with expected completion window.
- [ ] Server fan-out job is idempotent and emits a completion webhook/log; failures alert on Sentry.
- [ ] Documented **retention exception list** surfaced in Privacy Policy (§7).

### 4.3 Data export (GDPR/CCPA)

- [ ] **Settings → Account → Export my data** row.
- [ ] Async job produces Brand Graph + scripts + post history (machine-readable JSON + human-readable).
- [ ] Delivered via emailed signed-download link (expiring URL — `12-backend-data-security.md`).
- [ ] Export excludes other users' data and vendor secrets.

### 4.4 Per-connection revocation

- [ ] Each social connection (IG / TikTok via Ayrshare) can be **individually disconnected** in Settings without deleting the account.

**Acceptance criteria**
- [ ] App Review can find + complete deletion in ≤ 3 taps from Settings.
- [ ] Post-deletion, a fresh login with the same Apple ID starts at clean first-run (no orphaned data).

---

## 5. Accessibility — release gate (EAA is law)

WCAG 2.2 AA / EN 301 549 has been **legally required for B2C apps sold in the EU since June 28, 2025**, with fines up to €1M ([Forasoft — iOS accessibility & EAA](https://www.forasoft.com/blog/article/accessibility-ios-app-development)). Marque ships in the EU, so **AA is a release gate, not a nice-to-have.** Concrete SwiftUI patterns: [CVS Health — iOS SwiftUI accessibility techniques](https://github.com/cvs-health/ios-swiftui-accessibility-techniques).

### 5.1 VoiceOver & semantics

- [ ] `.accessibilityLabel` / `.accessibilityHint` / `.accessibilityValue` on every interactive + info element.
- [ ] `.accessibilityElement(children: .combine)` on grouped cards (Today directive card, Coach teardown cards) so they read as one unit.
- [ ] Custom `.accessibilityAction` for swipe gestures (clip library swipes, schedule reorder).
- [ ] Correct traits: `.isButton`, `.isHeader`, `.updatesFrequently` (render progress).
- [ ] **Rotors** for long lists (Trends, clip library) so VoiceOver users can jump.
- [ ] `AccessibilityNotification.Announcement` / `.screenChanged` fired on async errors + state transitions (publish success/fail).

### 5.2 Dynamic Type

- [ ] **No hard-coded font sizes.** Playfair/Tiempos display type scales with Dynamic Type.
- [ ] `@ScaledMetric` for spacing tied to text size.
- [ ] Tested at **AX5 (largest)** — serif-display-at-huge-whitespace is a classic clipping failure; verify no truncation on Today, paywall, onboarding.

### 5.3 Motion & haptics

- [ ] The "slow breathing" motion doctrine honors `accessibilityReduceMotion` → cross-fade or instant alternative.
- [ ] Skeleton loaders go static under Reduce Motion.
- [ ] Haptics are supplementary, never the only feedback.

### 5.4 Color / contrast (locked-aesthetic conflict)

> **Genuine constraint:** the single gold accent **#C9A227 on cream #F4F1EA fails WCAG AA for normal text (~2.1:1).** Gold is permitted for **large text, glyphs, and accents only — never body text or critical labels on cream.**

- [ ] No body text or critical label uses gold on cream.
- [ ] Body/label text uses ink `#0E0E10` (light mode) / cream (dark mode) for AA.
- [ ] The **gold streak glyph carries a non-color affordance** (accessible label + value, e.g. "7-day streak"), since color alone fails for color-blind users.
- [ ] No state is communicated by color alone (publish success/fail also uses icon + text).

### 5.5 Input, targets, forms

- [ ] All hit targets ≥ **44×44 pt**.
- [ ] Voice Control / Switch Control reachability verified on primary flows.
- [ ] Form errors are announced (not just colored) and describe the fix.
- [ ] Logical focus order on every screen.

### 5.6 Media captions

- [ ] Marque's own **app preview video has captions/descriptive overlays** for the editor preview.
- [ ] The clip editor exposes **caption editing** to the creator (also a product feature — `05-screens-produce.md`).

**Tooling / acceptance**
- [ ] **Xcode 16 Accessibility Inspector** audit (audit + point-inspect + simulator capture) is a CI/QA checkpoint with zero critical findings on primary flows.
- [ ] VoiceOver-only end-to-end pass of the hero loop (onboarding → script → record → render → schedule) signed off.

---

## 6. Localization & i18n readiness

Even if v1 ships English-only, the app must be **i18n-ready** so locale rollout is a content task, not a refactor.

- [ ] All user-facing strings in **String Catalogs** (`.xcstrings`) — zero hard-coded literals.
- [ ] Date/number/currency via `Date.FormatStyle` / `NumberFormatter` (locale-aware) — never manual formatting.
- [ ] **Subscription prices** rendered from StoreKit `displayPrice` (never hard-coded "$X").
- [ ] Layout tolerates **+30–40% German-style expansion** and **RTL** (`.leading`/`.trailing`, not left/right) — even if RTL locales are P1.
- [ ] Pluralization via `.stringsdict` / catalog plural rules.
- [ ] Localized **App Store metadata + screenshots** per shipped locale (§2).
- [ ] LLM-generated content (scripts) respects the creator's language — **Claude prompt carries the brand's language** (`07-ai-system.md`); the UI chrome and the generated content localize independently.
- [ ] Legal docs (Privacy, Terms) available in each shipped locale or English-fallback noted.

**Acceptance criteria**
- [ ] Pseudo-localization run (`--AppleLanguages` accented/expanded) shows no truncation or clipping.
- [ ] No string concatenation that assumes English word order.

---

## 7. Legal links & content/UGC policy

Marque publishes user content to third-party platforms → it needs an acceptable-use + DMCA posture, not just a privacy policy. All links live in **Settings → Legal** *and* adjacent to the paywall CTA where required.

- [ ] **Privacy Policy** (covers Supabase, R2/Stream, Ayrshare, PostHog, Sentry, Anthropic processing; retention windows; deletion).
- [ ] **Terms of Service / EULA** (Apple standard EULA acceptable if not using a custom one — link it).
- [ ] **Subscription terms + auto-renew disclosure** — Apple **requires** this text **adjacent to the paywall CTA**: title, length, price, auto-renew, how to cancel (`11-monetization.md`).
- [ ] **Data processing / GDPR** statement (controller/processor, lawful basis, DPO contact if applicable).
- [ ] **Content / community guidelines + acceptable-use** (UGC creators publish through Marque → prohibit illegal/infringing content).
- [ ] **DMCA / copyright** stance + takedown contact.
- [ ] **Licenses / acknowledgements** (open-source attributions) — Settings → About → Acknowledgements.
- [ ] **AI disclosure** consent recorded before any paid AI job (`11-monetization.md`, `07-ai-system.md`).

**Acceptance criteria**
- [ ] Every legal link resolves (no 404), opens in-app `SafariViewController` or native screen, and is reachable offline-fallback (cached or graceful error).
- [ ] Auto-renew disclosure is visible without scrolling on the paywall.

---

## 8. Help, support, contact & FAQ

- [ ] **In-app FAQ** (calm, one-question-per-row), reachable from Settings → Help.
- [ ] **Contact / Report a problem** form → FastAPI backend (email or ticket).
- [ ] Report-a-problem **attaches diagnostics**: app version/build, OS, current Sentry event id, last-N breadcrumbs (with PII scrubbed).
- [ ] **Status / known-issues** link (or in-app banner when a vendor outage is detected — e.g., Ayrshare degraded).
- [ ] **Replay onboarding** entry point (Settings → Help → "How Marque works").
- [ ] Support email is monitored and documented in App Store Connect support URL.

**Acceptance criteria**
- [ ] A user with zero technical knowledge can file a problem report in ≤ 4 taps, and engineering receives enough context to triage without a back-and-forth.

---

## 9. Settings screen inventory (complete)

The Settings tree is one layer deep, calm, grouped. Items marked `(internal)` ship only in internal/TestFlight builds.

| Group | Rows |
|---|---|
| **Account** | Email / Apple ID · **Manage Subscription** (deep link to system sheet) · **Restore Purchases** · **Export my data** · **Sign out** · **Delete account** |
| **Connected accounts** | Instagram (connect/disconnect) · TikTok (connect/disconnect) · per-account status + reconnect (`10-social-publishing.md`) |
| **Notifications** | Master toggle + categories: teardown ready · post published · streak · trend alert (each maps to an APNs category) |
| **Appearance** | Light / Dark / System (both locked palettes) |
| **Privacy & Security** | **Face ID app lock** toggle · **Analytics opt-out** (PostHog suppression — real, not cosmetic) · Crash-report opt-out |
| **Help & Support** | FAQ · Contact / Report a problem · Replay onboarding · Status |
| **Legal** | Privacy Policy · Terms / EULA · Subscription terms · Content guidelines · Acknowledgements / licenses |
| **Refer a friend** | Referral row (Section-8 feature; also surfaced once after a genuine win — `00-overview.md`) |
| **About** | Version · Build number · "Made for creators" / brand line |
| **Debug `(internal)`** | Env switch · feature-flag overrides · force-state simulator · copy push token · clear caches |

**Acceptance criteria**
- [ ] Every row above present and wired (no dead rows).
- [ ] Analytics opt-out actually suppresses PostHog capture + Sentry PII at the SDK level (verified by inspecting outbound events).
- [ ] Face ID lock gates app re-entry + the Account section specifically.

---

## 10. Network resilience matrix

Backbone: `NWPathMonitor` for connectivity transitions ([Apple — NWPathMonitor](https://developer.apple.com/documentation/network/nwpathmonitor)).

### 10.1 Connectivity

- [ ] `NWPathMonitor` observes wifi/cellular/unavailable → flips app to offline mode + shows the **one-line quiet banner** (not a modal — anti-clutter doctrine).
- [ ] Banner auto-dismisses on reconnect; queued work resumes.

### 10.2 Retry policy

- [ ] **Exponential backoff with jitter**, cap ~60s (Apple recommends jitter to avoid synchronized retry storms).
- [ ] **Only transient errors auto-retry**: 408, 5xx, connection-lost. **Never auto-retry 400/401/403** (surface re-auth / fix instead).
- [ ] 429 (rate-limited) respects `Retry-After` and routes to the queue, not a tight retry loop.

### 10.3 Idempotency & durable queue

- [ ] Every mutating call (publish, schedule, start render job, purchase-confirm) carries an **idempotency key** so a retry after a dropped response never double-posts or double-charges a render.
- [ ] Unsent actions (schedule clip, save script edit) persist locally and **replay on reconnect** (exactly-once).
- [ ] **Large uploads** (batch recording → R2) use **background `URLSession`** so they survive backgrounding/termination, with resumable progress.

### 10.4 Long-running render jobs (special case — `05-screens-produce.md`)

- [ ] Render-job state is **resumable**: app crash/background does not lose progress; on relaunch the job state is re-fetched.
- [ ] Progress is observable (`X of N clips rendered`) via Realtime/poll; reflected in Live Activity (§12).
- [ ] A job that fails server-side surfaces a recoverable error with retry-from-checkpoint where possible.

**Acceptance criteria**
- [ ] Airplane-mode mid-publish → reconnect results in exactly one published post (no dupes).
- [ ] Kill app mid-upload → relaunch resumes/cleanly restarts the upload; no orphaned partial assets billed.
- [ ] Network conditioner (3G, 100% loss, flaky) walk-through of the hero loop signed off.

---

## 11. Security checklist (mobile-specific)

Doctrine (`12-backend-data-security.md`): **the app never holds a vendor key.** All Anthropic / AssemblyAI / Shotstack / Ayrshare / R2 calls go through FastAPI. The app holds only the Supabase URL + anon/publishable key + user JWT.

### 11.1 Authorization (the real boundary)

- [ ] **RLS enabled on EVERY table in `public`** — non-negotiable. The anon key is public and ships in the binary, so RLS *is* the auth boundary. (Ref: **CVE-2025-48757** — 10.3% of analyzed apps shipped public-readable tables with RLS off — [Vibe App Scanner — Supabase RLS](https://vibeappscanner.com/supabase-row-level-security).)
- [ ] "Enable RLS on new tables" toggled on in the Supabase dashboard.
- [ ] Policies use `(select auth.uid()) = user_id` (the wrapped form — big perf win at scale), **not** `auth.uid() = user_id` ([Supabase — RLS](https://supabase.com/docs/guides/database/postgres/row-level-security)).
- [ ] **Never trust client-provided user IDs**; server derives identity from the JWT.
- [ ] No **type-mismatch bypass** (text vs uuid) in any policy.
- [ ] Storage buckets (Supabase Storage / R2-adjacent) have their own access-control policies.

### 11.2 Token & local secret storage

- [ ] Supabase refresh/access tokens live in **Keychain** (verify supabase-swift persists there — never UserDefaults), with rotation.
- [ ] Tokens cleared on sign-out and delete (§4).
- [ ] No secrets, API keys, or `service_role` in the app binary or `Info.plist` (CI grep for known key prefixes).

### 11.3 Transport & data-in-flight

- [ ] **App Transport Security**: HTTPS-only, **no arbitrary `NSAllowsArbitraryLoads` exception**.
- [ ] Signed-URL expiry enforced for R2 / Stream video assets (`12-backend-data-security.md`).
- [ ] **Webhook signature verification** on all inbound webhooks (Ayrshare, RevenueCat → FastAPI).

### 11.4 App-level posture

- [ ] Optional **Face ID app lock** (gates app + Account section).
- [ ] Jailbreak posture documented (degrade-gracefully stance; no hard block unless decided — Open Questions).
- [ ] **Sentry / PostHog PII scrubbing** configured (no emails, no tokens, no raw script text in events).

**Acceptance criteria**
- [ ] A pen-test / RLS audit confirms no table is readable/writable cross-user with the anon key.
- [ ] Static scan of the `.ipa` finds zero vendor secrets.

---

## 12. iOS extensions & affordances (tasteful, anti-clutter)

Each must respect the doctrine: surface contextually, never bolt onto Today (`00-overview.md` §6). Most are `(opt)`.

> **Repurpose-in has two entry points, one pipeline (option (c) — reconciled).** The **canonical, in-app** entry is the **Film ⇄ Upload source toggle on the `RecordSession` screen** (`01-information-architecture.md` §6.6, `04-screens-create.md` §1.6, `05-screens-produce.md` §1.6, `03-onboarding.md` §9, and 01 Open Q5) — picking an existing long video via `PhotosPicker` (camera roll) or `UIDocumentPicker` (Files), entering the same `BatchState` machine at `uploading`. The **Share Extension below is a *second, optional* entry point** — a separate app target that lets the creator start the same flow from *outside* Marque (the iOS share sheet of Photos/Files/another app). It is **not** "a toggle on Record"; it is a different lifecycle that **hands off into the identical backend pipeline and the same `BatchState`**. The two entry points differ only in *how the file arrives and which permission/UTType flow gates it*, never in what happens after (see the shared hand-off contract below).

- [ ] **Universal/deep-link routing** (required — §13).
- [ ] `(opt)` **Share Extension** — a separate app target (its own bundle id + lifecycle) that accepts a long video shared from Photos/Files/another app via the iOS share sheet and routes it into the **same** repurpose-in pipeline as the in-app Upload toggle. This is the *out-of-app* surface of the "repurpose-in" / "upload existing long video" feature (`04-screens-create.md` §1.6); the *in-app* surface is the Record source toggle, not this extension.
- [ ] `(opt)` **Home Screen Widget** — one line: next scheduled post / streak glyph. Matches Today minimalism.
- [ ] `(opt)` **Live Activity / Dynamic Island** — render + publish progress ("3 of 8 clips rendered"). Natural fit for long jobs (§10.4).
- [ ] `(opt, P1)` **App Shortcuts / App Intents** — "Marque, what should I post today."
- [ ] `(opt, P1)` **Spotlight indexing** of scripts/clips (`CoreSpotlight`).

### 12.1 Repurpose-in shared hand-off contract (in-app toggle ⇄ Share Extension)

Both entry points converge on one ingest path. The contract that makes them interchangeable:

| Concern | In-app **Upload toggle** (Record) | **Share Extension** |
|---|---|---|
| Lifecycle | Inside the main app; user is already authenticated and on `RecordSession`. | Separate app target invoked by the OS from another app's share sheet; main app may be terminated. |
| File acquisition | `PhotosPicker` (PhotosUI, camera roll) **or** `UIDocumentPicker` (Files). | OS hands the item to the extension; resolve provided **video UTTypes** (`.movie`, `.mpeg4Movie`, `.quickTimeMovie`) from the `NSItemProvider`. |
| Permission flow | **Photos** permission state (`NSPhotoLibraryUsageDescription`) for the camera-roll path; Files needs no Photos prompt. | **No in-app Photos prompt** — the OS already granted access to the shared item; the extension never requests Photos. |
| Identity | Live Supabase session in the main app. | Extension reads the session from the **shared App Group Keychain** (`12-backend-data-security.md`); if absent, it stages the item and defers ingest until the user opens the app and is authenticated. |
| Convergence point | Both create a `RecordingSession(source: .imported)` with one synthetic `ScriptTake` (no teleprompter), then **multipart-upload to R2** and enter `BatchState` at `uploading` → `processing` (`01-information-architecture.md` §5, `05-screens-produce.md` §1.6). |
| Hand-off mechanism | Direct, in-process. | Extension writes the picked file URL + a `pending_import` record to the **shared App Group container**; the main app drains the queue on next foreground and resumes the multipart upload via **background `URLSession`** (§10.3). The extension itself does **not** run the long upload (extensions are memory- and time-bounded). |

**Acceptance criteria**
- [ ] Both repurpose-in entry points produce a byte-identical downstream job: same `source: .imported` provenance, same R2 multipart upload, same `BatchState` transitions, same AssemblyAI → ClipEngine pipeline (`04-screens-create.md`).
- [ ] Share Extension accepts the platform's video UTTypes, stages the item to the shared App Group container, hands off to the same backend pipeline, and **survives the host app terminating** (the deferred-ingest queue is drained on next main-app foreground; no item lost if the extension is killed).
- [ ] Share Extension never prompts for Photos and never holds a vendor key; if no authenticated session exists in the App Group Keychain, it stages the item and routes the user to sign-in rather than failing silently.
- [ ] Live Activity ends cleanly on job completion/failure (no stuck activity).

---

## 13. Deep & universal links

Exact setup (easy to get subtly wrong — [Apple — Associated Domains](https://developer.apple.com/documentation/xcode/supporting-associated-domains)):

- [ ] **AASA file** served at `https://marque.app/.well-known/apple-app-site-association` (the `.well-known/` path; root is legacy).
- [ ] Served as **`application/json`** MIME, **no `.json` extension in the URL**, **HTTPS valid cert, NO redirects, returns 200**, file **≤ 128 KB** uncompressed.
- [ ] AASA contains `TEAMID.BundleID` + path patterns.
- [ ] **Associated Domains** capability enabled; entitlement value is `applinks:marque.app` (**no `https://` scheme**).
- [ ] `?mode=developer` used in entitlement during dev, **removed before release**.
- [ ] `onOpenURL` / `NSUserActivity` routing implemented for: open a specific clip · open a teardown card (from push) · open Trends · referral invite link · paywall deep link.
- [ ] **Universal links** (not custom URL scheme) for anything shared externally; custom scheme reserved for internal / OAuth callbacks only.

**Acceptance criteria**
- [ ] Cold-start, warm-start, and from-push all route to the correct screen.
- [ ] An unrecognized link path lands on a calm fallback (Today), never a crash or blank.

---

## 14. Subscriptions / paywall completeness (StoreKit 2 + RevenueCat)

Apple IAP is mandatory for iOS digital subscriptions; Stripe is **web-only** future surface (`11-monetization.md`). Reference: [RevenueCat — displaying paywalls](https://www.revenuecat.com/docs/tools/paywalls/displaying-paywalls).

- [ ] **Restore Purchases** button on the paywall **and** in Settings (effectively required by Apple; needed when things go wrong).
- [ ] **Two entitlements — `pro` and `studio`** — per the canonical `11-monetization.md` §2 model, **not** a single paid flag. A **Studio purchase grants BOTH `pro` and `studio`** (both entitlements are attached to every Studio product in the RevenueCat dashboard — `11-monetization.md` §2.1 option A), so app code does a flat `entitlements["studio"]?.isActive` with no hierarchy logic, while `pro`-gated surfaces remain unlocked for Studio users. Always **check the entitlement, never the product id** (`11-monetization.md` §1.3).
- [ ] **Paywall auto-dismiss is keyed to the *required* entitlement, not a single `pro`.** Each paywall presentation declares which entitlement unblocks the action the user attempted — `pro` for the publishing wall and standard gates, `studio` for the Studio-only contextual gate (faceless AI-visual / green-screen — `11-monetization.md` §3, §4.1). The RevenueCat paywall dismisses deterministically once **that specific entitlement** becomes active after purchase/restore (use the entitlement-scoped present-if-needed path; do **not** dismiss on any-paid-state, or a Pro user would wrongly clear a Studio gate).
- [ ] Offerings → Packages → Paywall structure (remote-configurable; supports A/B + charts). The **Studio contextual gate presents Studio packages**; the publishing/primary gate presents Pro (`11-monetization.md` §4.1).
- [ ] Server-side validation via RevenueCat; handle `Transaction.updates` (StoreKit 2) for out-of-band renewals/refunds.
- [ ] Auto-renew disclosure text adjacent to CTA (§7).

**Paywall / subscription state coverage**

| State | Treatment |
|---|---|
| Loading offerings | Skeleton, never blank |
| Offerings fetch failed (network) | Retry affordance — **never hard-lock the user out** |
| Purchase in progress | Disabled CTA + progress |
| Purchase deferred (Ask-to-Buy / Family) | "Waiting for approval" state |
| Purchase failed | Inline error + retry |
| Already has the required entitlement | Skip paywall → entitled (don't present at all; if reached, show "You're on Pro/Studio" + Manage link) |
| Pro user hits a **Studio-only** gate | Soft Studio upsell paywall (`pro` active but `studio` required) — present **Studio** packages, dismiss when `studio` becomes active |
| Restore found nothing | Calm "nothing to restore" message |
| Restore success | Confirm + unlock the entitlement-gated action |
| Billing grace / retry | Soft warning, keep access during grace |
| Entitlement expired | Graceful re-paywall for the **lapsed entitlement** (read-only fallback — §15); a Studio→Pro downgrade re-gates only Studio-only formats, not all paid features |

- [ ] Settings shows subscription status + **Manage Subscription** (deep link to system sheet) + Restore.

**Acceptance criteria**
- [ ] Network-off paywall still allows Restore + retry; user is never trapped behind a failed fetch.
- [ ] StoreKit config tested in sandbox for each state above.

---

## 15. Lifecycle & user states (first-run → power-user → lapse)

- [ ] **First-run (cold)** — no Brand Graph → guided onboarding (`03-onboarding.md`); Sign in with Apple is the first write.
- [ ] **Zero-data** — connected but nothing generated → calm empty states with one CTA each (never a blank grid).
- [ ] **Power-user** — many clips/schedules → **pagination, search, list virtualization, performance**; Today still shows exactly one directive (anti-clutter holds at scale).
- [ ] **Returning-after-lapse** — streak broken → gentle, non-punitive copy ("Pick up where you left off"), never shaming.
- [ ] **Logged-out** — clean re-auth entry.
- [ ] **Subscription-lapsed** — graceful read-only of existing content + re-paywall on gated actions (publish), never data loss.
- [ ] **Permission-denied (per permission)** — camera, mic, photos, notifications each have **tailored re-request copy + Settings deep link** (`03-onboarding.md`, `04-screens-create.md`).
- [ ] **Low-storage / large-upload-failed** — clear recoverable error, retry, no silent drop.
- [ ] **App-update / migration** — data migrations are versioned and tested forward; no data loss across an update.

**Acceptance criteria**
- [ ] A brand-new install and a 500-clip power-user account both render Today identically calm.
- [ ] Every OS permission can be denied, and the app remains usable with a clear path to re-grant.

---

## 16. Analytics & instrumentation coverage

Stack: **PostHog (product) + Sentry (crash/error) + remote-config flags** (`12-backend-data-security.md`, `11-monetization.md`).

### 16.1 Tracked-event taxonomy (minimum)

- [ ] Onboarding funnel (each step start/complete/drop).
- [ ] Brand Graph completion.
- [ ] Script generated / edited.
- [ ] Batch recorded · clips rendered.
- [ ] Post scheduled / published / **failed** (with failure reason).
- [ ] Paywall view → purchase / restore.
- [ ] Referral sent / redeemed.
- [ ] Teardown viewed.
- [ ] Streak milestone reached / broken.

### 16.2 Coverage & hygiene rules

- [ ] **Every error state in §1.1 emits an event** (this is how we find broken paths in the field).
- [ ] Naming is a documented, versioned taxonomy (no ad-hoc event names).
- [ ] **PII minimization** — no raw script text, no email, no tokens in analytics payloads.
- [ ] **Consent gating** — analytics opt-out (Settings §9) actually suppresses capture at the SDK level (not cosmetic).
- [ ] Sentry: source maps / dSYMs uploaded per build; release + dist tagged; alerting on new crash signatures.
- [ ] Remote-config flags gate risky surfaces (e.g., TikTok publishing) so they can be killed without a release.

**Acceptance criteria**
- [ ] The publish-failure funnel is queryable in PostHog end-to-end.
- [ ] A forced crash appears in Sentry symbolicated within minutes.

---

## 17. Final release gate (one-page sign-off)

- [ ] §1 state matrix — every cell signed off.
- [ ] §2 Store assets at exact spec; no superlative/ranking claims.
- [ ] §4 account deletion findable in ≤ 3 taps and fully cascades.
- [ ] §5 Accessibility Inspector clean on primary flows; VoiceOver hero-loop pass.
- [ ] §7 all legal links resolve; auto-renew disclosure by the CTA.
- [ ] §10 offline/idempotency: no double-post, no lost upload.
- [ ] §11 RLS audit: no cross-user access with anon key; zero secrets in binary.
- [ ] §13 universal links route from cold/warm/push.
- [ ] §14 paywall: every state designed; never trapped behind a failed fetch.
- [ ] §16 every error path emits analytics; Sentry symbolicated.
- [ ] **TikTok audit status** confirmed (see Open Questions) — publishing labeled correctly per audit state.

---

## Open questions

1. **Gold-on-cream contrast (design vs accessibility).** `#C9A227` on `#F4F1EA` is ~2.1:1 — below WCAG AA for normal text. The locked aesthetic restricts gold to large text, glyphs, and accents, which we believe is sufficient — but **design must confirm no critical label relies on gold**, and we should decide whether to introduce a darker gold token (e.g., for any borderline case) without breaking the locked palette. Owner: Product designer.
2. **TikTok Content Posting API audit is a launch blocker.** Before audit, the API client allows only **≤ 5 users posting / 24h**, accounts must be **private at posting time**, and content is forced to **SELF_ONLY** visibility; the audit takes **2–4 weeks with multiple feedback rounds** ([TikTok — Content Posting API](https://developers.tiktok.com/doc/content-posting-api-get-started)). **Submit the audit ≥ 6 weeks before any public-posting launch.** Until audited, TikTok publishing ships labeled as **Private-only beta** behind a remote-config flag. Owner: Backend lead. (See `10-social-publishing.md`.)
3. **iPad support.** Do we ship an iPad build at v1 (triggers the 13" 2064×2752 screenshot requirement and a layout pass) or iPhone-only? Owner: Product.
4. **Jailbreak posture.** Detect-and-degrade, detect-and-warn, or no detection? Affects the security checklist in §11.4. Owner: Security lead.
5. **Account-deletion retention window.** Exact legally-required retention period for RevenueCat purchase/tax records (and any other financial data) to publish in the Privacy Policy. Owner: Legal.
6. **Localization scope for v1.** English-only-but-i18n-ready (current assumption) vs. launch locales. Determines which Store metadata + legal translations are gated for release. Owner: Product.

## Sources

- Apple — App Store Connect screenshot specifications (sizes, formats, no-alpha, copy rules): https://developer.apple.com/help/app-store-connect/reference/screenshot-specifications/
- Apple — Upload app previews and screenshots (preview video limits, counts, codecs): https://developer.apple.com/help/app-store-connect/manage-app-information/upload-app-previews-and-screenshots/
- Apple — Offering account deletion in your app (Guideline 5.1.1(v)): https://developer.apple.com/support/offering-account-deletion-in-your-app/
- Apple — Announcement: in-app account deletion requirement: https://developer.apple.com/news/?id=12m75xbj
- Forasoft — iOS accessibility & the European Accessibility Act (WCAG 2.2 AA deadline, pillars, fines): https://www.forasoft.com/blog/article/accessibility-ios-app-development
- CVS Health — iOS SwiftUI accessibility techniques (labels, traits, rotors, Dynamic Type patterns): https://github.com/cvs-health/ios-swiftui-accessibility-techniques
- Meta — Instagram Graph API `content_publishing_limit` (25-posts/24h quota endpoint): https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit/
- Ayrshare — Instagram Graph API Error 9 / the 25-post daily limit (rolling window behavior): https://www.ayrshare.com/solutions/instagram-graph-api-error-9-the-25-post-daily-limit-how-to-fix-it/
- TikTok — Content Posting API getting started (unaudited limits, audit gate): https://developers.tiktok.com/doc/content-posting-api-get-started
- Apple — Supporting associated domains (AASA, universal links, entitlement, dev mode): https://developer.apple.com/documentation/xcode/supporting-associated-domains
- RevenueCat — Displaying paywalls (restore behavior, entitlement-dismiss, Offerings model): https://www.revenuecat.com/docs/tools/paywalls/displaying-paywalls
- Supabase — Row Level Security (`(select auth.uid())` perf pattern, RLS as auth boundary): https://supabase.com/docs/guides/database/postgres/row-level-security
- Vibe App Scanner — Supabase RLS (CVE-2025-48757, common bypass mistakes): https://vibeappscanner.com/supabase-row-level-security
- Apple — `NWPathMonitor` (connectivity monitoring for the resilience matrix): https://developer.apple.com/documentation/network/nwpathmonitor
