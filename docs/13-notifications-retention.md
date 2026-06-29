# 13 — Notifications, Retention & Growth Loops

> **Status:** Canonical. This document is the single source of truth for **how Marque earns the right to interrupt a creator, keeps them showing up, and grows by word of mouth** — APNs plumbing, the notification taxonomy + send governor, the per-category preference center, the consistency/streak system, achievements, lifecycle (re-engagement + win-back), and the referral loop.
>
> **Audience:** Backend engineers (FastAPI + Supabase + APNs), iOS engineers (Swift + SwiftUI, iOS 17+), and product designers.
>
> **Cross-references:** visual/motion/haptic tokens come from `02-design-system.md` (this doc never re-defines a color or easing); navigation, deep-link routes, and the anti-clutter doctrine come from `01-information-architecture.md`; the **in-app inbox** (§4.6) is the off-Today landing surface for un-pushable signals — its screen-map entry, route, and affordance are added to `01-information-architecture.md` §4.7 / §7.4; the streak glyph + one trend line on Today live in `01-information-architecture.md` §4.2 / §6.5; teardown content + virality scoring come from `07-ai-system.md`; scheduled-post timing + Publisher/Insights adapters come from `10-social-publishing.md`; subscription lifecycle events come from `11-monetization.md` (StoreKit 2 + RevenueCat); the canonical Supabase schema is owned by `12-backend-data-security.md` (this doc proposes the notification/streak/referral tables for it to absorb); onboarding + Brand Graph context come from `03-onboarding.md`.
>
> **Doctrine inherited from `01`/`02`:** *One idea per screen. Gold is a whisper. Never bolt features onto Today.* Notifications are governed by the same restraint — **the right cadence, not the maximum cadence.** Silence and spam are both churn engines; this document picks the narrow road between them.

---

## 0. Principles (the notification contract)

These eight rules bind every trigger, payload, and preference below. They are testable; see §12 acceptance criteria.

1. **Earn the prompt.** Marque never fires the iOS system permission prompt cold. A Marque-styled soft-ask (push primer) precedes it, shown only at a demonstrated-value moment — recommended: *the moment a creator's first batch of clips is ready* (§2). [Plotline](https://www.plotline.so/blog/how-to-improve-push-notification-opt-in-rates)
2. **Transactional is sacred; promotional is earned.** A creator who turns off marketing still gets *"your clips are ready"* and *"a post failed."* Those two classes are never collapsed into one toggle (§4, §5).
3. **The send governor lives in the backend, not the campaign.** Frequency caps, quiet hours, de-duplication, and permission/preference gating are enforced once, in the FastAPI send layer, so no individual feature can spam (§4.4). [vmobify](https://vmobify.com/blog/push-notification-strategy)
4. **Measure showing up, not vanity.** The streak unit is a *completed batch record session* on the creator's committed cadence — never app opens, never views, never likes (§6). [Trophy](https://trophy.so/blog/when-your-app-needs-streak-feature)
5. **Forgiveness is built in on day one.** Grace days, quiet resets, and personal-best framing ship with v1, not as a retention patch at day 30 (§6.3). [Atomic Habit](https://habit.redesigned.app/blog/forgiving-streaks-design-for-long-term-engagement)
6. **One earned moment, not a drip.** Re-engagement is a single calm touch; the referral ask appears once after a genuine win. Marque does not nag (§7, §8).
7. **Apple's rules are encoded, not hoped for.** Explicit in-app marketing-push consent, an in-app opt-out, push never required to function, rewards never gated behind enabling push (§3).
8. **Everything tunable is remote.** Caps, quiet-hours windows, primer cadence, and streak cadence live behind remote config + PostHog flags so they change without a release (§9, §11).

---

## 1. APNs setup + token lifecycle

### 1.1 Auth model — token-based (.p8 / JWT), not certificates

Marque uses **token-based APNs authentication** (a `.p8` signing key + ES256 JWT), Apple's recommended path for all new projects: the signing key never expires, one key works across every app and both environments, and there is no annual certificate renewal. ([Apple — Communicate with APNs using auth tokens](https://developer.apple.com/help/account/capabilities/communicate-with-apns-using-authentication-tokens/), [Apple — Establishing a token-based connection](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns))

**Hard constraints the FastAPI provider MUST honor** (all from Apple primary docs):

| Constraint | Value | Failure mode if violated |
|---|---|---|
| JWT signing alg | `ES256` (the only supported alg) | APNs rejects the token |
| JWT header `kid` | 10-char Key ID of the `.p8` | `InvalidProviderToken` |
| JWT claim `iss` | 10-char Team ID | `InvalidProviderToken` |
| JWT claim `iat` | issued-at, epoch **seconds** | `iat` > 1h old → `ExpiredProviderToken (403)` |
| Refresh window | regenerate JWT **no more than once / 20 min, no less than once / 60 min** | minting per-request → APNs throttles you; stale > 1h → 403 |
| Endpoint (prod) | `api.push.apple.com` (HTTP/2) | — |
| Endpoint (dev) | `api.sandbox.push.apple.com` (HTTP/2) | sandbox/prod tokens are **not interchangeable** |

**Implementation:** a single cached signer mints **one** JWT and reuses it across all sends for its validity hour; a recurring background task (Trigger.dev cron or an internal asyncio task) refreshes it every **~30–50 min** (inside the 20–60 min window). Do **not** mint a JWT per push. The `.p8` is stored in the secret manager (never in version control); if it leaks, mint a **new** key first, transition, then revoke the old.

> **2025 option (optional):** Apple now offers team-scoped keys (dev-only or prod-only) and topic-specific keys (bound to one bundle id). Existing keys keep working for all topics/envs. Recommended for Marque once stable, to blast-radius-limit a leaked key. ([Apple Dev News, Feb 2025](https://developer.apple.com/news/?id=wy4tb0uo))

### 1.2 Device-token lifecycle (the #1 cause of delivery failure)

- Call `UIApplication.shared.registerForRemoteNotifications()` on **every app launch**, not once. iOS returns the current valid token via `application(_:didRegisterForRemoteNotificationsWithDeviceToken:)`.
- Tokens **change** on OS update, app reinstall, restore-from-backup, or at iOS's discretion. On every `didRegister…`, **upsert** the token to Supabase. The **backend is the source of truth** — never treat the local cached token as canonical.
- On send, when APNs returns **`410 Gone`** or **`400 BadDeviceToken`**, immediately **soft-delete** that token row (`disabled_at = now()`). Continuing to push dead tokens degrades delivery and can trigger APNs rate-limiting. ([TRTC APNs setup guide](https://trtc.io/blog/details/apple-push-notification-service-setup-guide))
- The token carries its **environment** (`sandbox` for dev/TestFlight-debug builds, `prod` for App Store / TestFlight release). The send layer must route by environment; a dev-build token will silently never deliver against prod.

```
  iOS launch ──▶ registerForRemoteNotifications()
       │                       │
       │            didRegister(deviceToken)
       │                       ▼
       │      POST /v1/devices  (token, env, app_version, tz)   ── FastAPI
       │                       ▼
       │            UPSERT device_tokens (Supabase)  ◀── source of truth
       ▼
  send time ──▶ APNs HTTP/2 ──▶ 410/400 ? ──▶ soft-delete token row
```

### 1.3 Supabase data model — `device_tokens`

```sql
-- Owned by 12-backend-data-security.md; proposed here.
create table device_tokens (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  token         text not null,
  environment   text not null check (environment in ('sandbox','prod')),
  platform      text not null default 'ios',
  app_version   text,
  permission    text not null default 'not_determined'
                  check (permission in ('not_determined','provisional','authorized','denied')),
  timezone      text,                       -- IANA, e.g. "America/Los_Angeles"; used by the send governor
  last_seen_at  timestamptz not null default now(),
  disabled_at   timestamptz,                -- set on 410/400 or OS-level revocation
  created_at    timestamptz not null default now(),
  unique (token, environment)
);
create index on device_tokens (user_id) where disabled_at is null;
alter table device_tokens enable row level security;
create policy "own tokens" on device_tokens
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
```

One creator → many devices (multi-device fan-out is the default; a send targets *all* of a user's non-disabled tokens in the right environment). Each token carries its own `permission` status, synced from iOS on every launch so the backend detects an OS-level revocation even when in-app prefs still say "on."

### 1.4 States — notification permission

| State | Meaning | Marque behavior |
|---|---|---|
| `not_determined` | system prompt never shown | show primer at next value moment (§2); never auto-fire system prompt |
| `provisional` | quiet-delivery granted, no prompt | deliver low-interrupt categories to Notification Center; later ask to "keep" (§2.2) |
| `authorized` | full alert/sound/badge | normal delivery, governed by §4 |
| `denied` | user declined (or revoked in Settings) | **no system re-prompt is possible**; show an in-app banner explaining the Settings → Marque → Notifications path; route what *would* have been pushed to the **in-app inbox** (§4.6) so a declined user never silently loses a clips-ready / post-failed signal |
| Focus-suppressed | authorized but user is in a Focus | only `timeSensitive`/`critical` break through; ordinary pushes silently suppressed (§4) |

---

## 2. Opt-in strategy (one irreversible shot — get it right)

iOS gives Marque **exactly one** system permission prompt. A declined `UNUserNotificationCenter.requestAuthorization` is **permanent in-app**; re-enabling requires the user to dig into Settings (almost nobody does). Industry iOS opt-in averages ~51–56%; a well-designed soft-ask flow reaches ~55–70% on the same cohort. ([Pushwoosh study](https://www.pushwoosh.com/blog/ios-push-notifications/), [Plotline](https://www.plotline.so/blog/how-to-improve-push-notification-opt-in-rates))

### 2.1 The mandatory soft-ask (push primer) pattern

1. **Never** fire the system prompt cold on first launch — the single biggest mistake.
2. Show a Marque-styled **soft-ask sheet** (the `primer` type in `01-information-architecture.md` §4.7) at the **first demonstrated-value moment**. Recommended trigger: **the creator's first batch of clips finishes rendering / is ready to review** — Marque's natural "first value" beat, the equivalent of "your order shipped." Copy in the calm, declarative house voice (one idea, cream surface, serif headline):
   > **"We'll tell you the second your clips are ready."**
   > *No noise. Just the moments that matter.*
   > `[ Enable notifications ]`   `[ Not now ]`
3. **Enable** → call `requestAuthorization([.alert,.sound,.badge])`, then `registerForRemoteNotifications()`. **Not now** → dismiss; the system prompt **never fires**; record a cooldown in `UserDefaults` so the primer does not nag.
4. Re-show the primer at up to **3 different value moments** across sessions (different copy each — "clips ready," then "your first teardown is in," then "a post needs your eyes"). After 3 declines, back off to passive in-app banners only. ([Plotline](https://www.plotline.so/blog/how-to-improve-push-notification-opt-in-rates))

### 2.2 Provisional authorization (complement — flagged as an Open Question)

Requesting with the `.provisional` option is auto-granted with **no prompt**; notifications arrive **quietly** (Notification Center only — no sound, banner, or lock-screen). iOS later offers the user "Keep / Turn off," and "Keep" promotes to Deliver Immediately or Scheduled Summary. This lets Marque prove value with *real* clips-ready / teardown notifications before asking for full alert permission. Always **check `authorizationStatus` before scheduling** — it remains `.provisional` until the user keeps or turns off. ([Apple — Asking permission to use notifications](https://developer.apple.com/documentation/usernotifications/asking-permission-to-use-notifications))

The trade is real (quiet delivery = lower interrupt-ability), so **provisional-vs-explicit-first is an Open Question** (§ Open questions).

### 2.3 Focus-mode reality

Only `timeSensitive` and `critical` notifications break through an active Focus filter; ordinary pushes to a user in Focus are **silently suppressed**. This is why scheduled-post and post-window-closing reminders are the only categories allowed `timeSensitive` (§4). [Pushwoosh](https://www.pushwoosh.com/blog/ios-push-notifications/)

---

## 3. App Store compliance (hard guardrails — encoded, not hoped for)

| Guideline | Rule | Marque obligation |
|---|---|---|
| **4.5.4** | Push must not be used for promotion/marketing **unless** the user explicitly opts in via consent language shown in-app, **and** the app provides an in-app opt-out. Push must **not** be required to function; no sensitive data in payloads. | (a) explicit in-app marketing-push consent copy in the primer/settings; (b) per-category opt-out in the preference center (§5); (c) Marque fully usable with push off. [Guidelines](https://developer.apple.com/app-store/review/guidelines/) · [4.5.4 history](https://www.appstorereviewguidelineshistory.com/articles/2020-03-04-push-notifications-marketing-and-more/) |
| **4.5.3** | No spam / phishing / unsolicited messages via push. | send governor caps + relevance gating (§4.4). |
| **5.1.1(i)** | May **not** require enabling push (or location/tracking) to access functionality, content, **or to receive compensation** (incl. referral rewards). | referral rewards (§8) are **never** gated behind enabling notifications. |
| **3.2.2(x)** *(new, June 2025)* | Incentivizing in-app actions is now **permitted** — explicitly opens referral campaigns + reward-based onboarding. No manipulating reviews/charts/referrals. | referral loop (§8) is compliant; never tie rewards to App Store reviews. [ASO World, June 2025](https://asoworld.com/en/blog/apple-app-store-agreement-guideline-updates-june-2025/) |
| **4.10 / 2.5.16** | Don't monetize push itself; notifications must relate to app content. | all categories map to Marque's loop (§4). |

---

## 4. Notification taxonomy + triggers

### 4.1 iOS interruption levels (set in the payload)

Set `interruption-level` in the APNs `aps` dictionary (or `content.interruptionLevel` for local notifications). ([WWDC21 — Send communication & Time Sensitive notifications](https://developer.apple.com/videos/play/wwdc2021/10091/), [Apple — `timeSensitive`](https://developer.apple.com/documentation/usernotifications/unnotificationinterruptionlevel/timesensitive))

| Level | Behavior | Marque use | Entitlement |
|---|---|---|---|
| `passive` | added silently to list; no sound/screen | digests, low-urgency teardowns | none |
| `active` (default) | sound + screen; respects Focus/Summary | most nudges | none |
| `timeSensitive` | breaks through Focus **and** Notification Summary | scheduled-post-in-X reminders, "post window closing" | **Time Sensitive Notifications entitlement = YES** in `.entitlements` **and** the capability in the provisioning profile — else it silently won't break through |
| `critical` | bypasses ringer/mute | **NOT used by Marque** | special Apple-approved entitlement — do not request |

> iOS 18+ on-device prioritization **down-ranks** apps that abuse `timeSensitive`. Marque uses it for exactly two categories and nothing else.

### 4.2 The taxonomy (category key → trigger → level → cap tier)

Every category has a stable `category` key (mirrored in the preference center §5 and analytics §9) and a deep link into the canonical route table in `01-information-architecture.md` §7.4.

| `category` key | Trigger (source) | Level | Cap tier | Deep link |
|---|---|---|---|---|
| `clips_ready` | ClipEngine / Trigger.dev render job completes (`07-ai-system.md`, `10-social-publishing.md`) | `active` (→ `timeSensitive` only if a post window is imminent) | **Transactional** | `marque://library/clip/{id}` review screen |
| `scheduled_post_reminder` | Ayrshare scheduled slot approaching / needs creator confirm (`10-social-publishing.md`) | `timeSensitive` | **Transactional** | `marque://calendar/{date}` → `ScheduledPostDetail` |
| `post_published` | Publisher adapter success callback (IG Graph / TikTok) | `active` | **Transactional** | `marque://calendar/{date}` → `PublishStatus` |
| `post_failed` | Publisher adapter failure (re-auth needed, media rejected) | `active` | **Transactional** | `…/PublishStatus` (always delivered, even with marketing off) |
| `performance_teardown` | Insights adapter detects a win/loss; Coach card generated (`07-ai-system.md`) | `passive` / `active` | **Behavioural** | `marque://teardown/{id}` → `TeardownDetail` |
| `streak_nudge` | Streak at-risk near end of local day, streak ≥ threshold (§6) | `active` | **Behavioural** | `marque://today` (then `StreakDetail`) |
| `trend_radar` | Trend Radar surfaces a high-fit trend (`07-ai-system.md`) | `passive` | **Behavioural** | `marque://today` → `TrendDetail` (push only for high-relevance) |
| `achievement_earned` | A milestone badge is awarded once (§6.4) | `active` | **Behavioural** | `marque://today` → `StreakDetail` / Profile |
| `re_engagement` | No session in N days (lifecycle, §7) | `active` | **Promotional** | `marque://today` |
| `win_back` | RevenueCat churn / billing event (`11-monetization.md`, §7) | `active` | **Promotional** | `marque://paywall?placement=winback` |
| `referral_earned` | A referral qualifying action fires (§8) | `active` | **Transactional-ish** | `marque://settings/referral` |

**Doctrine binding:** `streak_nudge`, `trend_radar`, and `achievement_earned` mirror onto Today as the **single gold glyph** / **one trend line** (`01` §6.5, §6.2) — the push is the *off-app* surface of the same one-idea Today element, never a second competing surface.

### 4.3 APNs payload shape

```jsonc
{
  "aps": {
    "alert": { "title": "Your clips are ready", "body": "8 clips, 4 formats. Take a look." },
    "sound": "default",
    "interruption-level": "active",        // passive | active | time-sensitive
    "thread-id": "clips_ready",            // groups in Notification Center by category
    "mutable-content": 1                   // allows a Notification Service Extension (R2/Stream thumbnail)
  },
  "category": "clips_ready",               // app-level routing key (matches §4.2 + §5)
  "deeplink": "marque://library/clip/0e2…",// → 01-information-architecture.md §7.4
  "entity_id": "0e2…",
  "send_id": "snd_…"                       // idempotency + analytics correlation (§9)
}
```

A **Notification Service Extension** uses `mutable-content` to attach a clip thumbnail from **Cloudflare R2 + Stream** for `clips_ready` / `post_published`, turning the push itself into a small proof-of-value.

### 4.4 The send governor — caps, quiet hours, de-dupe (FastAPI, enforced once)

All gating lives in **one** send-layer function, **never** per-campaign. Order matters (cheap checks first, DB count last): ([vmobify](https://vmobify.com/blog/push-notification-strategy), [SashiDo](https://www.sashido.io/en/blog/push-notification-preference-center), [Appbot 2026](https://appbot.co/blog/app-push-notifications-2026-best-practices/))

```
send(user, category, payload):
  1. global_enabled?                        else → inbox-or-drop(category)
  2. category enabled in prefs?             else → inbox-or-drop(category)   (Transactional bypasses this gate)
  3. device permission ∈ {authorized, provisional}?  else → inbox-or-drop(category)
  4. quiet hours? (user tz)                 if yes AND tier ≠ Transactional → defer to window open
  5. de-dupe: same entity_id+category in 24h? → drop (already in inbox if it landed there)
  6. collision: another Behavioural send in last 4h? → defer or drop the lower-priority one
  7. frequency cap (tier, see table)        else defer/drop
  8. global ceiling: ≤ 3 non-transactional / 24h  else drop
  → write an inbox row (always, for Transactional + Behavioural) → §4.6
  → fan out to all non-disabled device_tokens (right env)

# inbox-or-drop(category): the single defined fallback for an un-pushable send.
#   Transactional  → persist an inbox row (unread) so the creator can still
#                    find "clips ready" / "a post failed" on next launch. Never lost.
#   Behavioural    → persist an inbox row IFF it is still actionable at next launch
#                    (streak-at-risk and trend alerts decay → drop; teardowns + achievements persist).
#   Promotional    → silent drop. A stale re-engagement / win-back nudge surfaced
#                    days later is noise, not signal; lifecycle re-fires on its own schedule (§7).
```

**Inbox is the system of record for what was *meant* to reach the creator, not just what a push delivered.** Every Transactional and persisted-Behavioural send writes an `inbox_items` row (§4.6) *whether or not* the push went out — so a creator on `denied`, in quiet hours, or simply offline opens Marque and finds the same signal waiting. A delivered+tapped push marks its row read; a gated/dropped send leaves it unread. This is the one place the send-governor's "else" branches resolve, and it is a **real, specified surface** (§4.6), not a promise the rest of the spec never keeps.

**Cap tiers:**

| Tier | Cap | Quiet hours (10pm–8am local) | Notes |
|---|---|---|---|
| **Transactional** | uncapped, but only on a real event | **exempt** (time-sensitive only) | `clips_ready`, `post_failed`, `scheduled_post_reminder`, security |
| **Behavioural** | **max 2 / day**, **min 4h gap** | honored | de-dupe collisions: a streak-at-risk + a teardown must not both fire in the same hour |
| **Promotional / lifecycle** | **max 1 / day**, **5 / week** | honored | `re_engagement`, `win_back` |
| **Global ceiling** | **≤ 3 non-transactional / 24h** | — | the most-cited cause of opt-out/uninstall is >3 low-relevance pushes/24h |

**Quiet hours** are computed in the **creator's own timezone** (stored on `device_tokens.timezone` and the profile). Sending "good morning" at 10pm destroys trust instantly; transactional `timeSensitive` is the only exemption. The earliest fatigue signal is a rising **7-day opt-out rate** (§9) — monitored weekly, plus App Store review mining (frequency complaints surface in reviews weeks before metrics move). ([vmobify](https://vmobify.com/blog/push-notification-strategy))

### 4.5 States — delivery

`queued` → `gated` (dropped by §4.4, with reason logged) · `deferred` (quiet hours → window open) · `sent` → APNs `accepted` · `dead_token` (410/400 → soft-delete) · `suppressed` (Focus) · `inboxed` (un-pushable but persisted to the in-app inbox per §4.6 — the resolution of the governor's "else" branches for Transactional + persisted-Behavioural sends) · `opened` (deep-link tapped, §9).

A single send can be **both `inboxed` and `sent`**: the inbox row is written first, then the push fans out; tapping the push (or opening the row) marks it `opened`/read in one place.

### 4.6 The in-app inbox — where un-pushable signals land

The send governor (§4.4) and the `denied` permission path (§1.4) both depend on a place for signals to land when a push can't or shouldn't fire (permission denied, prefs off, offline, dead token, Focus-suppressed). Without it, a creator who declines push — a **deliberately supported, common path** (§2, §3: push is never required) — is promised clips-ready / post-failed signals that vanish. The in-app inbox is that surface. It is defined here as a real screen, not a hand-wave.

**Doctrine fit.** The inbox is **not a sixth tab** and **never lands on Today** (`01` §2.1, P1). It is reached one layer deep from a small **tray glyph** in the existing top-trailing affordance cluster on **Today and Coach** (alongside the gear/avatar, `01` §4.7), presented as its own modal-rooted `NavigationStack` exactly like Settings/Profile — keeping the five-tab creative loop pristine. The glyph carries a **derived** unread count (computed property, never an imperative counter, per `01` §2.2) and uses the gold accent **only** when unread > 0, consistent with "gold is a whisper." When the inbox is empty and has never had items, the glyph is hidden entirely.

**What it holds.** A reverse-chronological list of `inbox_items` — every Transactional send and every *persisted* Behavioural send (§4.4), regardless of whether the matching push delivered. Each row shows the category glyph (`16` §128 content icons), the same title/body the push carried, a relative timestamp, and read/unread state. Tapping a row fires the **same `deeplink`** the push carried (§4.3) through the **same `DeepLink` enum** (`01` §7.3) — so a clips-ready row opens `marque://library/clip/{id}`, a post-failed row opens `PublishStatus`, identical to tapping the push. Reading a row (or tapping its push) marks it read; the unread count decrements reactively.

**What it does NOT hold.** Promotional/lifecycle sends (`re_engagement`, `win_back`) — those silent-drop when un-pushable (§4.4) and re-fire on their own schedule (§7); surfacing a days-stale win-back nudge is noise. The inbox is a **signal record, not a marketing channel** — this keeps it compliant with 4.5.4 (it is not a second promo surface) and aligned with the anti-nag doctrine (Principle 6).

**Retention & housekeeping.** Inbox rows auto-expire: Transactional after **30 days**, persisted-Behavioural after **14 days** (remote-config-tunable, §9). Acting on the row's deep-link target also resolves it (e.g. once a failed post is re-published, its row is marked `resolved` and de-emphasized). A "Mark all read" affordance lives in the nav bar. There is no manual delete in v1 (expiry handles it) — an Open Question.

#### Supabase data model — `inbox_items`

```sql
-- Owned by 12-backend-data-security.md / 12-backend-data-security.md; proposed here.
create table inbox_items (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  category    text not null,                 -- matches the §4.2 `category` keys
  tier        text not null check (tier in ('transactional','behavioural')),
  title       text not null,                 -- same copy the push carried (§4.3)
  body        text not null,
  deeplink    text not null,                 -- same marque:// route the push carried (01 §7.4)
  entity_id   text,                          -- for de-dupe + resolve-on-action
  send_id     text,                          -- correlates to the §4.3 payload + analytics (§9)
  state       text not null default 'unread'
                check (state in ('unread','read','resolved')),
  expires_at  timestamptz not null,          -- 30d transactional / 14d behavioural (remote-config)
  created_at  timestamptz not null default now(),
  read_at     timestamptz,
  resolved_at timestamptz
);
create index on inbox_items (user_id, created_at desc) where state <> 'resolved';
create index on inbox_items (user_id) where state = 'unread';      -- powers the derived badge
create unique index on inbox_items (user_id, category, entity_id)  -- de-dupe with §4.4 step 5
  where entity_id is not null;
alter table inbox_items enable row level security;
create policy "own inbox" on inbox_items
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
```

The same `entity_id + category` uniqueness that de-dupes pushes (§4.4 step 5) de-dupes inbox rows, so a re-fired send updates the existing row rather than stacking duplicates. The unread-count query backing the tray badge is the `where state = 'unread'` partial index; **Supabase Realtime** on `inbox_items` (filtered by `user_id`) pushes live unread-count updates so the glyph reacts without polling.

#### States — inbox

| State | Behavior |
|---|---|
| **loading** | skeleton rows; never block on network (cached last list shown first) |
| **empty** | calm one-line empty state in house voice (*"Nothing waiting. You're caught up."*); tray glyph hidden |
| **populated** | reverse-chron rows; unread visually weighted; "Mark all read" in nav bar |
| **error** | inline retry on the list; last-good cached rows stay visible, never a blank screen |
| **offline** | serve cached rows from the local store; new server-side rows reconcile on reconnect (Realtime catch-up) |
| **permission-denied (push)** | the inbox is **fully functional** — this is precisely the state it exists to serve; a one-time top banner explains *"Notifications are off, so we'll keep these here for you"* + an "Open Settings" affordance (mirrors §1.4) |

#### Deep-link routing

| URL / scheme | `DeepLink` | Lands on |
|---|---|---|
| `marque://inbox` | `.inbox` | `InboxView` (own modal-rooted stack) |

This row is added to the canonical route table in `01-information-architecture.md` §7.4, and `InboxView` is added to the `01` §4.7 cross-cutting (modal/supporting) screen map. Tapping any inbox row dispatches that row's stored `deeplink` through the existing `AppRouter.handle(_:)` path — the inbox introduces **no new routing mechanism**, only one new entry point.

---

## 5. Per-category preference controls (a real preference center)

A single "Allow" toggle is no longer acceptable UX; apps with granular per-category preferences show materially **lower total opt-out** — give creators a way to say *"yes, but less."* ([Appbot 2026](https://appbot.co/blog/app-push-notifications-2026-best-practices/), [SashiDo](https://www.sashido.io/en/blog/push-notification-preference-center), [Knock](https://knock.app/manuals/push-notifications/building-a-preference-system))

The preference center lives in **Settings** (its own `NavigationStack`, `01` §4.7) — **never** on Today. It opens with a calm one-screen list of value-framed categories, a global "Pause all," and a quiet-hours control.

### 5.1 Category labeling (house voice — value, not jargon)

| `category` keys grouped | Settings label | Default |
|---|---|---|
| `clips_ready`, `post_published`, `post_failed`, `scheduled_post_reminder` | **Clips & posting** | **ON** (transactional — preserved even when marketing off) |
| `performance_teardown` | **Your performance** | ON |
| `streak_nudge`, `achievement_earned` | **Showing up** | ON |
| `trend_radar` | **Trends** | ON |
| `re_engagement`, `win_back` | **From Marque** | **OFF** (promotional — explicit opt-in per 4.5.4) |

Always preserve the transactional core (clips ready / post failed) even when "From Marque" is off. Offer a global **Pause all** reachable in ≤ 2 taps **and** a digest option — but note: ~95% of opted-in users who receive **zero** pushes in 90 days churn anyway. **Silence is its own churn engine**; the goal is the right cadence, not zero. ([mobilegrowthhacks Ch.4](https://mobilegrowthhacks.com/push-guide-chapter-4))

### 5.2 Supabase data model — `notification_prefs`

```sql
-- Owned by 12-backend-data-security.md; proposed here. Mirrors Knock's model.
create table notification_prefs (
  user_id        uuid primary key references auth.users(id) on delete cascade,
  global_enabled boolean not null default true,
  categories     jsonb not null default '{}'::jsonb,
    -- per category: { "clips_ready": {"enabled":true,"channels":{"push":true,"email":false}}, ... }
  quiet_hours    jsonb not null default
    '{"enabled":true,"start":"22:00","end":"08:00","timezone":null,"exceptions":["transactional"]}'::jsonb,
  updated_at     timestamptz not null default now()
);
alter table notification_prefs enable row level security;
create policy "own prefs" on notification_prefs
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
```

### 5.3 Knock caveats (encoded as requirements)

- **Real-time changes** — a preference edit applies immediately, no app restart (write-through to Supabase; the send governor reads live).
- **Detect OS-level revocation and sync it** — if iOS reports `denied` even though in-app prefs say "on," surface that mismatch in the UI ("Notifications are off in iOS Settings — [Open Settings]") rather than silently swallowing sends.
- **Default conservatively** — promotional OFF, transactional ON.
- **UI feedback when OS permission is blocking** despite in-app prefs allowing.

### 5.4 States — preference center

| State | Behavior |
|---|---|
| loading | skeleton list; never block on network |
| saved | optimistic + real-time write-through; subtle confirmation per `02` motion tokens |
| OS-permission-blocking-despite-app-prefs | inline banner + "Open Settings" affordance |
| offline | queue the change locally; reconcile on reconnect (last-write-wins on `updated_at`) |

---

## 6. Streaks / consistency — measure SHOWING UP, not vanity

This is the crux of Marque's anti-vanity ethos. The system is built on current behavioral-science guidance, not arcade gamification.

### 6.1 What we measure

The streak unit is a **completed batch record session** (the creator "showed up to create"), credited **on the cadence they committed to** during onboarding (`03-onboarding.md`) — **never** app opens, **never** view/like counts. *"Avoid tracking actions just because they're measurable. App opens don't indicate value."* Marque tracks **quality of showing up**, not minimum box-ticking. ([Trophy — when you need a streak](https://trophy.so/blog/when-your-app-needs-streak-feature), [Trophy — long-term growth](https://trophy.so/blog/designing-streaks-for-long-term-user-growth))

### 6.2 Cadence flexibility (critical for a weekly batch product)

Marque's loop is *"film once → post all week."* A **daily** streak is therefore *wrong*. Offer a **non-daily anchor** — recommended default **1×/week**, optional **3×/week** for power creators — chosen at onboarding and changeable in Settings. (In a fitness-tracker A/B, 27% of new users chose a 3×/week mode and their 30-day retention rose **12%** vs forced-daily.) ([Atomic Habit — forgiving streaks](https://habit.redesigned.app/blog/forgiving-streaks-design-for-long-term-engagement))

### 6.3 Forgiveness (mandatory, day one)

- **Grace days / streak freezes** — earned through prior consistency, so loss-aversion works *for* the creator (they spend a banked resource, not lose the streak). A **1/week grace day** beat 2/month in testing: **+6–9%** 30-day retention, **+15–25%** 3-day rebound rate. ([Atomic Habit](https://habit.redesigned.app/blog/forgiving-streaks-design-for-long-term-engagement), [Product Coalition](https://www.productcoalition.com/p/streaks-nudges-and-the-behavioral))
- **Quiet reset, no guilt** — on a break, *"No funeral."* Show the **personal best** as a record: *"Your best: 23 weeks. Let's start a new one."* Broken streaks durably suppress engagement, and re-engagement after a break is *lower* than designers expect — worst for long streaks (loss aversion scales with investment), so recovery mechanisms are central, not optional. ([Silverman & Barasch via Yu-kai Chou](https://yukaichou.com/gamification-analysis/streak-design-gamification-motivation-burnout/))
- **Private by default** — no public leaderboards, no comparison; fits Marque's calm, non-comparative tone.
- **Celebration immediacy** — fire the streak/milestone microinteraction within **~200ms** of the qualifying action; richer feedback at milestones (4 / 12 / 52 weeks). Rendered as the **single gold glyph** on Today (`01` §6.5) + the full view in **Profile** (`StreakDetail`, `01` §4.2). Motion + haptics come from `02-design-system.md` (the gold-glyph "breath," disabled under Reduce Motion). ([Product Coalition](https://www.productcoalition.com/p/streaks-nudges-and-the-behavioral))

### 6.4 Achievements

Milestone badges tied to **showing-up behavior** — *first batch*, *first 4-week run*, *first viral clip from a Marque-edited format*, *first repurpose-in* — with **earned / locked** states, celebrated **once**. Kept private/personal. Awarding follows the same rule as the sibling app's badge timing: **award once, on the first qualifying fetch; never re-trigger.** The `achievement_earned` push (§4.2) is Behavioural-tier and de-duped by `entity_id` so a re-fetch can't re-fire it.

### 6.5 Supabase data model — streaks + achievements

```sql
-- Owned by 12-backend-data-security.md; proposed here.
create table consistency_streaks (
  user_id          uuid primary key references auth.users(id) on delete cascade,
  cadence          text not null default 'weekly'    -- 'weekly' | 'thrice_weekly'
                     check (cadence in ('weekly','thrice_weekly')),
  current_count    int  not null default 0,          -- units in the current run
  longest_count    int  not null default 0,          -- personal best (never decreases)
  grace_remaining  int  not null default 1,          -- banked grace for the period
  last_credit_at   timestamptz,
  period_end_at    timestamptz,                       -- when the current period's window closes
  state            text not null default 'building'
                     check (state in ('building','at_risk','frozen','broken','personal_best')),
  updated_at       timestamptz not null default now()
);

create table achievements (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  code        text not null,                          -- 'first_batch','run_4w','first_viral',...
  earned_at   timestamptz not null default now(),
  notified_at timestamptz,                            -- guarantees "celebrate once"
  unique (user_id, code)
);
```

### 6.6 States — streak

`building` (on track) · `at_risk` (period closing, not yet credited → eligible for one `streak_nudge`) · `frozen` (grace day spent — kept alive, no guilt) · `broken` (quiet reset; `longest_count` preserved) · `personal_best` (current run beat the prior best → richer celebration).

### 6.7 Streak-health metrics (instrument in PostHog — §9)

Beyond completion: retention **by streak length** (if 52+-week streakers retain *worse* at week 60 than 12-week streakers → unhealthy pressure); streak-length distribution; breakage timing (most breaks in week 1 = the action is too demanding); freeze-utilization rate; **`rebound_rate_3d`** (returns within 3 days of a miss); restart rate after break; cohort comparison (streakers vs non-streakers — does the streak *actually* drive retention or just short-term metrics?). Survey at day 60/90: *"Accomplishment or obligation?"* / *"Would you continue if the counter disappeared?"* ([Trophy](https://trophy.so/blog/designing-streaks-for-long-term-user-growth), [Atomic Habit](https://habit.redesigned.app/blog/forgiving-streaks-design-for-long-term-engagement))

---

## 7. Lifecycle messaging — re-engagement + win-back

Marque's churn signals come from **RevenueCat webhooks** consumed by the FastAPI backend (entitlement source of truth is owned by `11-monetization.md`). Lifecycle pushes are driven off those events, **segmented by reason.**

### 7.1 Billing-issue recovery (gentle, transactional)

The RevenueCat **`BILLING_ISSUE`** event carries `grace_period_expiration_at_ms` — the creator retains access during the store grace period while Apple retries. Send a **gentle "update your payment" push** (transactional tier, **not** promo), spaced out, **before** grace expiry. RevenueCat emits only **one** billing-issue event per failure episode; recovery fires a `RENEWAL` event that resets `grace_period_expires_date` to null. ([RevenueCat — grace periods](https://www.revenuecat.com/docs/subscription-guidance/how-grace-periods-work))

### 7.2 Cancellation segmentation (win-back copy depends on reason)

The **`CANCELLATION`** event's `cancel_reason` ∈ `UNSUBSCRIBE` (user chose) | `BILLING_ERROR` | `DEVELOPER_INITIATED` | `PRICE_INCREASE`; `unsubscribeDetectedAt` is set but `isActive` stays true until period end. Show **"subscription ends on [date]"** in-app, and segment win-back by reason — *recover payment* (`BILLING_ERROR`) is different copy from *re-sell value* (`UNSUBSCRIBE`). Win-back campaigns are **configured, not coded** (Apple win-back offers via App Store Connect / RevenueCat dashboard); a redeemed offer arrives as a normal purchase in `CustomerInfo`, and resubscribe-after-expiry fires **`RENEWAL`** (not `INITIAL_PURCHASE`) — grant entitlement on both. ([RevenueCat — cancellations, pauses & winback](https://www.revenuecat.com/guides/revenuecat-android-sdk/cancellations-pauses-and-winback))

### 7.3 Apple Retention Messaging API (recommended save flow)

The **Apple Retention Messaging API** (late 2025) shows a message/offer **inside iOS Settings the moment the user taps Cancel.** The backend must respond in **< 700ms**, and the feature requires passing Apple sandbox performance testing first (can take ~1h — use a long-duration product to test). RevenueCat automates setup, AI localization, and delivery under **Lifecycle → Retention**. **Strongly recommended** for Marque's cancellation save flow. ([RevenueCat — Apple Retention Messaging API](https://www.revenuecat.com/blog/engineering/apple-retention-messaging-api/))

### 7.4 Re-engagement (non-billing) — one touch, then leave them alone

After **~2 weeks** of inactivity, send **one** calm `re_engagement` push:
> *"Here's something new since you've been away. No pressure."*

**Not** a drip campaign. If they don't return, Marque leaves them alone — autonomy outranks juicing a metric. Every lifecycle nudge carries a 1-click opt-out. ([Product Coalition](https://www.productcoalition.com/p/streaks-nudges-and-the-behavioral))

### 7.5 States — win-back

`in_grace` (billing issue, access retained) · `past_due` (grace expired) · `churned_by_reason` (segmented) · `recovered` (`RENEWAL` fired → entitlement restored, suppress further win-back).

---

## 8. Referral loop (Settings row + ONE earned moment)

### 8.1 Structure

- **Double-sided reward** is the empirical winner for subscription apps: the **referrer** gets 1 month free Pro **per converted referral**; the **referee** gets an extended free trial (e.g. 14d vs the standard 7d). ([ASOhack](https://asohack.com/blog/mobile-app-referral-program-design))
- **Credit on PAID CONVERSION, not install** — install-crediting invites fake-account fraud; paid-conversion crediting is the gold standard. ([ASOhack](https://asohack.com/blog/mobile-app-referral-program-design))

### 8.2 Placement (matches the anti-clutter doctrine)

- A **dedicated row in Settings** (always available) — `marque://settings/referral` (`01` §7.4).
- **ONE earned prompt** after a genuine win — e.g. right after a Marque-edited clip goes viral (an `Insights` win event, `07-ai-system.md`). Shown as the one-time **earned-referral prompt sheet** on Today (`01` §4.2). **No** aggressive launch-time prompts, **no** notification reminders — they burn goodwill needed for other permissions.

### 8.3 iOS plumbing

- **Universal Links** (or App Clips) for attribution — IDFA/SKAdNetwork restrictions mean referral must use **ID-less deep linking**; expect some attribution-signal loss. AASA + Associated Domains entitlement are already specified in `01` §7.1.
- Native share sheet (`UIActivityViewController`) with a **pre-filled message + deep link** and a personalized landing (*"Sara invited you to Marque"*).
- **Reward logic is server-side** (FastAPI), applied only **after** install + paid conversion are verified, and entitlement is granted via RevenueCat (`11-monetization.md`).

### 8.4 Fraud controls

Reward in a sane range; **rate-limit invites/day**; **delay reward 7–30 days** and check for immediate uninstalls; enforce phone/email uniqueness. ([ASOtext](https://asotxt.com/wiki/referral-programs))

### 8.5 Compliance (binds §3)

Per **5.1.1(i)** Marque may **not** require enabling push/tracking to receive referral rewards; per **3.2.2(x)** incentivized referral is now allowed; **never** manipulate App Store charts/reviews via referrals.

### 8.6 Supabase data model — referrals

```sql
-- Owned by 12-backend-data-security.md; proposed here.
create table referrals (
  id              uuid primary key default gen_random_uuid(),
  referrer_id     uuid not null references auth.users(id) on delete cascade,
  referee_id      uuid references auth.users(id),      -- null until install attributed
  code            text not null unique,
  state           text not null default 'link_generated'
                    check (state in ('link_generated','pending','earned','fraud_held','reversed')),
  installed_at    timestamptz,
  converted_at    timestamptz,                          -- paid conversion = credit trigger
  reward_grant_at timestamptz,                          -- delayed 7–30d for fraud window
  rewarded_at     timestamptz,
  created_at      timestamptz not null default now()
);
create index on referrals (referrer_id);
alter table referrals enable row level security;
create policy "own referrals" on referrals
  for select using (auth.uid() = referrer_id);
```

### 8.7 Metric

Viral coefficient **K = invites/user × invite→conversion rate**; instrument **both** levers separately in PostHog (§9).

### 8.8 States — referral

`not_started` · `link_generated` · `pending` (install attributed, awaiting paid conversion) · `earned` (converted, reward queued) · `fraud_held` (in the 7–30d window / flagged) · `reversed`.

---

## 9. Retention + habit metrics (PostHog + Sentry + remote config)

The only retention that matters is **behavior**, not open rate. Tie opted-into categories → return sessions / clips published / feature adoption.

| Domain | Metric | Why |
|---|---|---|
| **Opt-in funnel** | `primer_shown → primer_allow → system_prompt_granted`; per-category opt-in mix | locate the leak in the §2 flow |
| **Push health** | **7-day opt-out rate** (weekly); per-category open/CTR (not just overall); avg pushes/user/week split transactional vs marketing; preference-edit rate; OS-permission-revocation rate | rising opt-out is the earliest fatigue signal (§4.4) |
| **Habit / streak** | streak-length distribution; retention-by-streak-length cohorts; **`rebound_rate_3d`**; freeze utilization; restart-after-break; streaker-vs-non-streaker retention delta; day-60/90 obligation-vs-accomplishment survey | detect *unhealthy* streak pressure, not just completion (§6.7) |
| **Lifecycle** | billing-issue recovery rate; win-back redemption by `cancel_reason`; Retention-Messaging-API save rate; re-engagement return rate | §7 effectiveness |
| **Growth** | viral coefficient **K**; invite→conversion; referral fraud-hold rate | §8 effectiveness |

If **nobody edits** preferences, the entry point / value-explanation is weak. If **streak length correlates negatively** with late retention, the cadence is too demanding. Crashes/errors in the NSE or send path go to **Sentry**.

**Tunable via remote config + PostHog flags** (no release needed): cap tiers, quiet-hours windows, primer cadence (the "up to 3 value moments"), streak cadence default, grace-day allotment, re-engagement inactivity threshold. A/B these with PostHog flags.

---

## 10. States summary (one place for QA)

| Surface | States |
|---|---|
| **Notification permission** | not_determined · provisional · authorized · denied (→ in-app Settings-path banner + inbox fallback) · Focus-suppressed |
| **Preference center** | loading · saved (real-time) · OS-permission-blocking-despite-app-prefs · offline (queue + reconcile) |
| **In-app inbox** | loading · empty (tray glyph hidden) · populated · error (cached rows stay) · offline (cached + reconcile) · permission-denied (fully functional — the state it exists for) |
| **Streak** | building · at_risk · frozen (grace used) · broken (quiet reset) · personal_best |
| **Referral** | not_started · link_generated · pending · earned · fraud_held · reversed |
| **Win-back** | in_grace · past_due · churned_by_reason · recovered |
| **Delivery** | queued · gated (reason logged) · deferred · sent · dead_token · suppressed · inboxed (persisted to §4.6) · opened |

---

## 11. Component specs (iOS)

| Component | Type | Spec highlights |
|---|---|---|
| `PushPrimerSheet` | `primer` sheet (`01` §4.7) | cream surface, serif headline, one idea; Enable → `requestAuthorization` + `registerForRemoteNotifications`; Not now → cooldown in `UserDefaults`; re-show ≤ 3× across sessions with rotating copy; respects `02` motion tokens |
| `NotificationSettingsView` | within Settings `NavigationStack` | per-category toggles (§5.1), global "Pause all," quiet-hours picker (tz-aware), OS-permission-mismatch banner + "Open Settings"; optimistic real-time writes to `notification_prefs` |
| `StreakGlyph` | Today element (`01` §6.5) | single gold glyph; ~200ms award microinteraction; 2.4s breath (disabled under Reduce Motion per `02`); tap → `StreakDetail` |
| `StreakDetailView` | `.streakDetail` push (Today) + Profile | current/longest/cadence/grace; quiet-reset + personal-best framing; private (no leaderboard) |
| `AchievementBadge` | within Profile / `StreakDetail` | earned/locked states; celebrated once (`notified_at` guard) |
| `ReferralRow` + `ReferralView` | Settings row (`marque://settings/referral`) | double-sided reward copy; `UIActivityViewController` share sheet with pre-filled deep link; server-verified rewards |
| `EarnedReferralPromptSheet` | one-time sheet on Today (`01` §4.2) | fires once after a genuine win; never on launch; never repeated |
| `InboxTrayGlyph` | top-trailing affordance on Today & Coach (`01` §4.7) | small tray icon beside gear/avatar; **derived** unread count (computed, never imperative); gold only when unread > 0; hidden when never-populated; tap → `InboxView` |
| `InboxView` | own modal-rooted `NavigationStack` (`marque://inbox`) | reverse-chron `inbox_items` list; category glyph + title/body + relative time + read/unread; row tap dispatches the row's stored `deeplink` via `AppRouter.handle`; "Mark all read"; loading/empty/error/offline/permission-denied states (§4.6); Realtime-driven unread updates |
| `NotificationServiceExtension` | NSE target | `mutable-content` → attach R2/Stream clip thumbnail for `clips_ready`/`post_published` |

---

## 12. Acceptance criteria

**APNs / tokens**
- [ ] Provider uses **token-based** (.p8) auth with ES256; one JWT is cached and refreshed every 30–50 min (never minted per request).
- [ ] `registerForRemoteNotifications()` runs on **every** launch; every `didRegister` upserts the token to Supabase with its environment + timezone.
- [ ] `410 Gone` / `400 BadDeviceToken` soft-deletes the token row; dead tokens are never re-sent.
- [ ] Sends route by environment; sandbox tokens never hit prod APNs and vice-versa.

**Opt-in / compliance**
- [ ] The iOS system prompt is **never** fired before a Marque soft-ask; primer appears at the clips-ready value moment.
- [ ] Declining the primer never fires the system prompt and sets a cooldown; primer re-shows ≤ 3× then backs off.
- [ ] Explicit in-app marketing-push consent exists; a per-category opt-out exists; Marque is fully usable with push off (4.5.4).
- [ ] Referral rewards are **not** gated behind enabling push (5.1.1(i)).

**Send governor**
- [ ] All caps / quiet-hours / de-dupe / permission+preference gating run in **one** backend function, in the §4.4 order.
- [ ] Quiet hours (10pm–8am) are computed in the **user's** timezone; only transactional `timeSensitive` is exempt.
- [ ] Global ceiling ≤ 3 non-transactional/24h; Behavioural ≤ 2/day with a ≥ 4h gap; Promotional ≤ 1/day & ≤ 5/week.
- [ ] `timeSensitive` is used **only** for `scheduled_post_reminder` and imminent post-window `clips_ready`, and the entitlement is present.
- [ ] A streak-at-risk and a teardown in the same hour de-dupe to one send.

**In-app inbox**
- [ ] Every Transactional send (and every persisted-Behavioural send) writes an `inbox_items` row whether or not the push delivered; Promotional sends never inbox.
- [ ] A creator on `denied` permission still finds clips-ready / post-failed in the inbox on next launch; the inbox is fully functional with push off.
- [ ] Tapping an inbox row dispatches the **same** `deeplink` through the **same** `DeepLink` enum as the equivalent push (no second routing path).
- [ ] The tray glyph's unread count is a derived/computed value (Realtime-backed), never an imperative counter, and the glyph is hidden when never-populated.
- [ ] The inbox is not a tab and never appears on Today; it is reached one layer deep from the Today/Coach affordance cluster.
- [ ] `marque://inbox` is in the `01` §7.4 route table and `InboxView` is in the `01` §4.7 screen map; rows auto-expire (30d transactional / 14d behavioural, remote-config).

**Streak / achievements**
- [ ] The streak unit is a completed batch record session on the chosen cadence — never app opens/views.
- [ ] Default cadence is non-daily (weekly), with a 3×/week option; grace day = 1/week, earned.
- [ ] A break is a quiet reset that preserves `longest_count`; no guilt copy; personal-best surfaced.
- [ ] Award microinteraction fires within ~200ms; achievements celebrate **once** (`notified_at` guard).
- [ ] Streak is private (no public leaderboard).

**Lifecycle / referral**
- [ ] Billing-issue push is transactional, sent before grace expiry; win-back copy is segmented by `cancel_reason`.
- [ ] `RENEWAL` (resubscribe-after-expiry) and win-back redemptions both grant entitlement.
- [ ] Re-engagement is a **single** touch after ~2 weeks, with 1-click opt-out — not a drip.
- [ ] Referral credits on **paid conversion**, not install; reward delayed 7–30d for the fraud window.

**Metrics**
- [ ] 7-day opt-out rate, per-category CTR, `rebound_rate_3d`, and viral coefficient K are instrumented in PostHog.
- [ ] Caps, quiet-hours windows, primer cadence, and streak cadence are remote-config-tunable without a release.

---

## 13. Quick-reference: do / don't

**DO** — token-based APNs with a cached/refreshed JWT; register on every launch; backend = token source of truth; soft-ask before the system prompt; preserve transactional even when marketing is off; enforce caps/quiet-hours/de-dupe in one backend governor; measure showing-up (batch sessions); build forgiveness day one; one earned referral moment; tune everything via remote config.

**DO** *(inbox)* — land every un-pushable Transactional (and persisted-Behavioural) signal in the in-app inbox so a declined / offline / Focus'd creator never loses "clips ready" or "post failed"; reuse the push's own deep link from inbox rows; expire rows automatically.

**DON'T** — certificate auth; mint a JWT per request; fire the system prompt cold; ship a single Allow toggle; cap per-campaign instead of centrally; abuse `timeSensitive`; send promo in quiet hours; build daily streaks; punish a broken streak; gate referral rewards behind push; bolt the streak/referral/teardown/**inbox** onto Today as new surfaces; make the inbox a sixth tab; inbox promotional/lifecycle nudges; drip re-engagement.

---

## Open questions

1. **File-number reconciliation.** `01-information-architecture.md` §7.4 cross-references this content as `13-notifications-retention.md`, but the assigned path is `13-notifications-retention.md`. One must move so the deep-link-table cross-ref resolves. The **inbox additions this doc requires in `01`** (the `marque://inbox` route in §7.4 and `InboxView` in the §4.7 screen map) should land in the same reconciliation pass. Owner: Docs lead / Eng.
2. **In-app inbox placement + scope sign-off.** §4.6 places the inbox one layer deep from the Today/Coach top-trailing affordance cluster (not a tab, never on Today). Confirm: (a) tray-glyph vs. nesting it under Settings; (b) v1 has no manual row-delete (expiry only) — acceptable? (c) the 30d/14d expiry windows. All remote-config-tunable but need launch values + a `01` design sign-off. Owner: Product + iOS.
3. **Provisional vs explicit-first authorization.** Should Marque request `.provisional` (quiet delivery, prove value, lower interrupt-ability) or go straight to the explicit primer → full prompt at the clips-ready moment? This doc specifies the explicit primer as primary and flags provisional as a complement. Owner: Product + Growth.
4. **Streak cadence default + grace allotment.** Confirm weekly (vs 3×/week) as the default unit and 1 grace/week as the starting allotment; both are remote-config-tunable but need a launch value. Owner: Product.
5. **Referral reward economics.** Confirm "1 month free Pro per converted referral" (referrer) + "14-day trial" (referee), and the exact fraud-hold delay (7 vs 30 days). Affects RevenueCat offer config in `11-monetization.md`. Owner: Growth + Finance.
6. **Apple Retention Messaging API rollout.** Adopt at v1 (needs the < 700ms backend endpoint + sandbox perf test), or fast-follow? Owner: Backend + Monetization.
7. **NSE thumbnail source.** Confirm the Notification Service Extension pulls clip thumbnails from Cloudflare **Stream** (signed URL) vs **R2** directly, and the signing path. Owner: Backend + Media.
8. **Email channel.** The `notification_prefs.categories.channels` schema includes `email`, but no email provider is in the locked stack. Is lifecycle/digest email in scope, and via which adapter? Owner: Product + Eng.

## Sources

- [Apple — Communicate with APNs using authentication tokens](https://developer.apple.com/help/account/capabilities/communicate-with-apns-using-authentication-tokens/) — one signing key, dev+prod, never expires, revocable.
- [Apple — Establishing a token-based connection to APNs](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns) — ES256/kid/iss/iat, 20–60 min refresh, `ExpiredProviderToken` 403.
- [Apple Developer News, Feb 2025 — team-scoped & topic-specific keys](https://developer.apple.com/news/?id=wy4tb0uo) — new key-scoping security options.
- [Apple — Asking permission to use notifications](https://developer.apple.com/documentation/usernotifications/asking-permission-to-use-notifications) — provisional authorization mechanics; check status before scheduling.
- [Apple WWDC21 — Send communication and Time Sensitive notifications](https://developer.apple.com/videos/play/wwdc2021/10091/) — interruption levels + payload key + entitlement notes.
- [Apple — `UNNotificationInterruptionLevel.timeSensitive`](https://developer.apple.com/documentation/usernotifications/unnotificationinterruptionlevel/timesensitive) — breaks through Focus/Summary; entitlement requirement.
- [Apple — App Store Review Guidelines](https://developer.apple.com/app-store/review/guidelines/) — 4.5.3 / 4.5.4 (push consent + opt-out), 5.1.1(i) (no gating rewards on push), 4.10.
- [App Store Review Guidelines History — 4.5.4](https://www.appstorereviewguidelineshistory.com/articles/2020-03-04-push-notifications-marketing-and-more/) — verbatim push marketing-consent rule.
- [ASO World — App Store guideline updates, June 2025](https://asoworld.com/en/blog/apple-app-store-agreement-guideline-updates-june-2025/) — 3.2.2(x) incentivized actions/referrals now permitted.
- [TRTC — Apple Push Notification Service setup guide](https://trtc.io/blog/details/apple-push-notification-service-setup-guide) — device-token lifecycle, 410/400 cleanup.
- [vmobify — Push notification strategy (2026)](https://vmobify.com/blog/push-notification-strategy) — single-shot prompt stakes, cap tiers, 10pm–8am quiet hours, fatigue signals.
- [Plotline — How to improve push opt-in rates](https://www.plotline.so/blog/how-to-improve-push-notification-opt-in-rates) — soft-ask lift, preserve the system prompt, up to 3 value moments.
- [Pushwoosh — iOS push notifications](https://www.pushwoosh.com/blog/ios-push-notifications/) — opt-in benchmarks, Focus-mode suppression.
- [SashiDo — Push notification preference center](https://www.sashido.io/en/blog/push-notification-preference-center) — topics/frequency/quiet-hours preference design.
- [Knock — Building a push preference system](https://knock.app/manuals/push-notifications/building-a-preference-system) — prefs schema, send-time gate, real-time + OS-revocation caveats.
- [Appbot — App push notifications: 2026 best practices](https://appbot.co/blog/app-push-notifications-2026-best-practices/) — granular categories lower opt-out; review mining.
- [mobilegrowthhacks — Push guide, Ch. 4](https://mobilegrowthhacks.com/push-guide-chapter-4) — silence-as-churn, digest/pause patterns.
- [Trophy — When your app needs a streak feature](https://trophy.so/blog/when-your-app-needs-streak-feature) — measure value not opens.
- [Trophy — Designing streaks for long-term growth](https://trophy.so/blog/designing-streaks-for-long-term-user-growth) — streak-health metrics, cohort comparisons.
- [Atomic Habit — Forgiving streaks design](https://habit.redesigned.app/blog/forgiving-streaks-design-for-long-term-engagement) — grace-day A/B, non-daily anchors, `rebound_rate_3d`.
- [Yu-kai Chou — Streak design, motivation & burnout](https://yukaichou.com/gamification-analysis/streak-design-gamification-motivation-burnout/) — Silverman & Barasch broken-streak suppression.
- [Product Coalition — Streaks, nudges, and the behavioral](https://www.productcoalition.com/p/streaks-nudges-and-the-behavioral) — celebration immediacy, one-touch re-engagement.
- [RevenueCat — How grace periods work](https://www.revenuecat.com/docs/subscription-guidance/how-grace-periods-work) — BILLING_ISSUE + grace fields, single event per episode.
- [RevenueCat — Cancellations, pauses & winback](https://www.revenuecat.com/guides/revenuecat-android-sdk/cancellations-pauses-and-winback) — cancel_reason segmentation, dashboard-configured win-back, RENEWAL on resubscribe.
- [RevenueCat — Apple Retention Messaging API](https://www.revenuecat.com/blog/engineering/apple-retention-messaging-api/) — in-Settings cancel save, < 700ms backend, sandbox perf test.
- [ASOhack — Mobile app referral program design](https://asohack.com/blog/mobile-app-referral-program-design) — double-sided reward, credit-on-paid-conversion, fraud controls.
- [ASOtext — Referral programs wiki](https://asotxt.com/wiki/referral-programs) — fraud-window delay, uniqueness checks, viral coefficient.
