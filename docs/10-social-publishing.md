# 10 — Social Publishing, Scheduling & Analytics Pullback

> **Scope.** This document is the implementation contract for the last two legs of Marque's core loop: **publish** (get a rendered clip onto Instagram & TikTok, now or on a schedule) and **learn** (pull each post's performance back into the Brand Graph). It specifies the two adapters — **Publisher** and **Insights** — that hide every vendor behind a stable protocol, the scheduling engine, the optimal-time logic, the "save & remind" fallback for when direct publishing is gated, and the exact 2025/2026 platform constraints that shape all of it.
>
> **Sibling docs (authoritative where noted):**
> - `00-overview.md` — product principles; the binding platform-constraints table (§"Hard platform constraints"). On any conflict about IG/TikTok limits, that table and this document are kept in sync; defer to the primary dev-doc links cited inline.
> - `01-information-architecture.md` — adapter contracts (`ClipEngine`, `Publisher`, `Insights`), Trigger.dev job topology, FastAPI orchestrator surface. **Authoritative for the adapter DI wiring**; this doc specifies the Publisher/Insights *internals*.
> - `02-design-system.md` — color tokens, type ramp, motion curves, calm-error doctrine.
> - `12-backend-data-security.md` — canonical Supabase schema authority. The `publish_jobs`, `scheduled_posts`, `post_metrics`, `platform_connections` blocks below are the **owning specification** for those tables; defer to `12-backend-data-security.md` only on cross-cutting concerns (RLS, FK to `clips`/`brand_graph`).
> - `05-screens-produce.md` — Calendar/Library screens that *call* the Publisher; per-platform toggles map 1:1 to `scheduled_posts` rows.
> - `08-format-virality.md` — ClipEngine renders to a **public HTTPS R2/Stream URL**; that URL is the `mediaUrl` this adapter consumes.
> - `07-ai-system.md` — Brand Graph; the optimal-time-v2 learned slots and the Virality Engine training signal are *written from here, read from there*.
> - `05-screens-produce.md` — the Coach teardown cards + push consume `post_metrics`; this doc fills that table, that doc renders it.
> - `11-monetization.md` — StoreKit 2 / RevenueCat (publishing volume is a paid-tier lever; not specified here).
>
> **Locked aesthetic (see `02-design-system.md`).** Cream `#F4F1EA` / near-black `#0E0E10`. Serif display (Playfair/Tiempos), grotesque body (Inter/Söhne/Matter), single gold accent `#C9A227` used sparingly. **Errors are never red** — they are calm, declarative, and actionable.
>
> **Anti-clutter doctrine (binding).** Publishing **never appears on Today** beyond a *single gold status glyph* and the one next-post line. The entire publish/schedule/reconnect surface lives in **Calendar** and contextual sheets. Performance lives in **Coach/Insights**. A failed publish surfaces as one calm line + one tap to act — never a modal, never a red banner, never a feature bolted onto Today.

---

## 0. The publish loop at a glance

```
ClipEngine (06) ──renders──▶  R2/Stream public HTTPS URL
                                       │
                         clip.status = "ready"
                                       │
   Calendar (05) ──schedule──▶  scheduled_posts row (per platform)
                                       │  Trigger.dev delayed task fires at target_publish_at_utc
                                       ▼
                              ┌──────────────────┐
                              │  Publisher.publish │  (FastAPI · AyrsharePublisher v1)
                              └──────────────────┘
                                 │            │
                          GatedResult     PublishAccepted → publish_jobs row (state machine)
                          (save & remind)        │  poll / webhook → reconcile
                                 │                ▼
                          APNs reminder    platform live post (postId)
                                                  │
                              T+1h / T+24h / T+7d  │  Insights.pull
                                                  ▼
                              ┌──────────────────┐
                              │   Insights.pull    │  (AyrshareInsights v1)
                              └──────────────────┘
                                       │
                          post_metrics (vendor-neutral) ──▶ Brand Graph (07)
                                       │
                    ┌──────────────────┼──────────────────────┐
              optimal-time v2     Coach teardowns (09)   Virality training (07)
```

**Three non-negotiables encoded everywhere below:**

1. **Publish is asynchronous.** The SwiftUI client is *never* blocked on a platform round-trip. A publish is `enqueue → poll/webhook → reconcile`, backed by a durable `publish_jobs` state machine.
2. **No vendor concept leaks past the adapter.** Ayrshare profile keys, Meta container IDs, TikTok `publish_id`s are *internal* to the adapter. Callers see Marque types only.
3. **Gating is normal, not exceptional.** Direct publishing is frequently unavailable (unaudited client, over-cap, personal account, token rot, unresolved disclosure). The adapter returns a typed `GatedResult`; the product degrades gracefully to inbox-draft or save-&-remind so the loop *always delivers a clip*.

---

## 1. Adapter design — `Publisher` & `Insights`

Both adapters live in the FastAPI orchestration service (`01-information-architecture.md`). v1 concrete impls are `AyrsharePublisher` / `AyrshareInsights`. A future `DirectPublisher` (raw IG Graph + TikTok Content Posting) implements the **same protocol** — swapping is **one DI line**. This is the locked "adapter = one-file change" doctrine.

### 1.1 Why Ayrshare for v1 (decision, not a guess)

Marque ships on the **Ayrshare** aggregator for v1, behind the Publisher adapter. The single biggest reason: **Ayrshare already holds the approved TikTok audited-client status and Meta App Review**, so Marque inherits TikTok **Direct Post / public visibility** and IG content publishing **without** running its own 2–6 week TikTok audit or Meta App Review on day one. A single API also fans out to Instagram + TikTok and normalizes scheduling. ([Ayrshare](https://www.ayrshare.com/), [TikTok — Content Posting API Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started))

The DirectPublisher migration is a deliberate *later* move (lower per-post cost, no aggregator dependency) and is non-trivial precisely because Marque would then own Meta/TikTok token refresh (§4). It is an **Open question**, not a v1 commitment.

### 1.2 `Publisher` protocol

The Publisher operates on **one clip → one platform** at a time. Fan-out across platforms is the caller's concern (one `scheduled_posts` row per platform), so a per-platform failure never poisons the sibling platform.

```swift
// Marque-owned types. No Ayrshare / Meta / TikTok type appears here.

protocol Publisher {
    /// Validate that this clip CAN be published to this platform right now.
    /// Pure-ish: may hit creator_info / content_publishing_limit. No side effects on success.
    func preflight(_ req: PublishRequest) async throws -> PreflightResult

    /// Enqueue a publish (now or scheduled). Returns immediately with a job handle.
    /// NEVER blocks on platform processing. Idempotent on req.idempotencyKey.
    func enqueue(_ req: PublishRequest) async throws -> PublishOutcome

    /// Cancel a not-yet-fired scheduled publish.
    func cancel(jobId: PublishJobID) async throws

    /// Read current status (fallback when webhooks are delayed/lost).
    func status(jobId: PublishJobID) async throws -> PublishJobState
}

struct PublishRequest {
    let creatorId: CreatorID
    let clipId: ClipID
    let platform: Platform                 // .instagramReels | .tiktok  (v1)
    let mediaURL: URL                      // public HTTPS R2/Stream URL (06)
    let caption: String                    // ≤ platform cap; see §3.4
    let coverURL: URL?                     // IG cover_url / thumb
    let schedule: PublishSchedule          // .now | .at(Date /*UTC*/)
    let platformOptions: PlatformOptions   // IG shareToFeed; TikTok privacy + disclosure
    let idempotencyKey: String             // = "\(clipId):\(platform):\(scheduleSlot)"
}

enum PublishOutcome {
    case accepted(PublishJobID)            // queued → publish_jobs row created
    case gated(GatedResult)                // cannot direct-publish → fallback ladder (§5)
}

enum GatedReason: String, Codable {
    case clientUnaudited                   // TikTok: forced SELF_ONLY
    case overDailyCap                      // IG ≥ limit, or TikTok ~15/creator/24h
    case accountNotProfessional            // IG personal account (not Business/Creator)
    case needsReconnect                    // token expired / revoked
    case disclosureUnresolved              // TikTok commercial-content toggle on, nothing selected
    case privacyOptionUnavailable          // requested privacy not in creator_info options
    case mediaNotReachable                 // R2/Stream URL not public / not domain-verified (TikTok)
    case platformTemporarilyUnavailable    // 5xx / maintenance → retry later, not a hard gate
}

struct GatedResult {
    let reason: GatedReason
    let fallback: FallbackRoute            // .tiktokInboxDraft | .saveAndRemind  (§5)
    let userMessage: String                // calm, declarative copy for the sheet
    let retryable: Bool
}
```

**`PreflightResult`** carries the platform-truth needed to render the compose sheet *before* a publish is attempted — most importantly TikTok's **dynamic** privacy options (§2.2):

```swift
struct PreflightResult {
    let canPublishNow: Bool
    let remainingDailyQuota: Int?          // IG: from content_publishing_limit; TikTok: derived
    let privacyOptions: [TikTokPrivacy]?   // ONLY for TikTok; from creator_info/query (§2.2)
    let requiresCommercialDisclosure: Bool // TikTok
    let maxDurationSec: Int?               // TikTok creator_info max_video_post_duration_sec
    let blockingGate: GatedReason?         // non-nil ⇒ render the gated/fallback path
}
```

### 1.3 `Insights` protocol

```swift
protocol Insights {
    /// Per-post metrics for one published post.
    func postMetrics(postRef: PlatformPostRef) async throws -> PostMetrics

    /// Per-account profile-level metrics (followers, profile reach).
    func accountMetrics(creatorId: CreatorID, platform: Platform) async throws -> AccountMetrics
}

/// Vendor-neutral. Fields absent on a platform are nil, never zero-faked.
struct PostMetrics: Codable {
    let postRef: PlatformPostRef
    let capturedAt: Date
    let views: Int?
    let reach: Int?
    let likes: Int?
    let comments: Int?
    let shares: Int?
    let saves: Int?
    let watchTimeSec: Double?
    let avgWatchTimeSec: Double?           // retention proxy when full curve unavailable
    let completionRate: Double?            // 0…1 when platform exposes it
    let followsFromPost: Int?
}
```

> **Design rule.** `nil` means *the platform did not report this metric*; it must never be coerced to `0`. Reach-normalization (§6) and Coach teardowns (`05-screens-produce.md`) branch on presence, not value.

### 1.4 Vendor mechanics behind the v1 adapters (Ayrshare)

| Concern | Ayrshare mechanism | Adapter responsibility |
|---|---|---|
| Multi-tenant scoping | **Business plan → User Profiles**, one per Marque creator. Every call carries a **`Profile-Key`** HTTP header. ([Ayrshare Profiles](https://www.ayrshare.com/docs/introduction)) | Map `creatorId → Profile-Key`; store in `platform_connections`. Never expose the key client-side. |
| Publish / schedule | `POST /api/post` with `post` (caption), `platforms` (e.g. `["instagram","tiktok"]`), `mediaUrls` (R2/Stream URL), optional `scheduleDate` in **ISO-8601 UTC** (`"2026-07-01T18:00:00Z"`). Per-platform overrides in named objects, e.g. `instagram: { reels: true, shareReelsFeed: true }`, `tiktok: { ... }`. Omit `scheduleDate` to post now. ([Ayrshare introduction](https://www.ayrshare.com/docs/introduction)) | Marque schedules **one platform per call** (idempotency + isolation); Ayrshare's multi-platform array is *not* used for fan-out. |
| Cheap default scheduling | **Auto-Schedule** endpoint: register named time-slot schedules; post into the next open slot. ([Auto-Schedule](https://www.ayrshare.com/understanding-the-ayrshare-auto-schedule-api-endpoint/)) | Used only as the **fallback** before optimal-time-v2 is trained (§7). Marque's own scheduler (§6) is primary. |
| Status | Webhook-first; `status(jobId:)` falls back to Ayrshare post lookup. | Reconcile into `publish_jobs.state`. |
| Analytics | `POST /api/analytics/post` (per-post) and `POST /api/analytics/social` (per-account), both scoped by `Profile-Key`. ([Ayrshare](https://www.ayrshare.com/docs/introduction)) | Normalize → `PostMetrics` / `AccountMetrics`. |
| Webhooks | Register post-status + analytics webhooks; Ayrshare POSTs to a FastAPI endpoint on state change. ([Ayrshare](https://www.ayrshare.com/docs/introduction)) | Prefer webhook over poll; verify signature; idempotent on event id. |

> **Pass-through truth.** Even via Ayrshare, the underlying IG and TikTok limits in §2 **still bite** — Ayrshare relays IG Error 9 and TikTok per-creator rejections. The adapter must **surface platform error codes**, mapped to `GatedReason`, not swallow them.

---

## 2. Real platform constraints to encode

These shape v1 scope, the scheduler, and the compose UI directly. They apply *whether* publishing goes through Ayrshare (pass-through) or a future DirectPublisher (raw). Limits below are from primary dev docs as of mid-2026; **treat them as ceilings and design for them being lowered.**

### 2.1 Instagram Graph API — Content Publishing

| Constraint | Detail | Encode as |
|---|---|---|
| Account type | **Business or Creator** account only. Personal accounts cannot publish via API. | `GatedReason.accountNotProfessional` → save-&-remind. Detect at connect time. |
| Two-step container flow | `POST /<IG_ID>/media` (create container) → returns container `id` → `POST /<IG_ID>/media_publish` with `creation_id=<id>`. ([Meta — Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)) | DirectPublisher state machine: `creating → processing → publishing → live`. |
| Reels params | `media_type=REELS`, `video_url` = **publicly reachable HTTPS** (R2/Stream public URL), optional `share_to_feed`, `cover_url`, `thumb_offset`, `audio_name`. | `mediaURL` must be public HTTPS; `coverURL`→`cover_url`; `platformOptions.shareToFeed`→`share_to_feed`. |
| **Async processing — must poll** | After creating the container, poll `GET /<CONTAINER_ID>?fields=status_code` until `FINISHED` (`IN_PROGRESS` \| `FINISHED` \| `ERROR`) **before** calling `media_publish`. Publishing early fails. ([Meta — Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)) | Trigger.dev step with capped exponential backoff; `ERROR` → `publish_jobs.state = failed`. |
| **Rate limit** | Meta documents **100 API-published posts per rolling 24h** per IG account (all types; a carousel = 1), enforced at `media_publish`. Query remaining via `GET /<IG_ID>/content_publishing_limit`. ([Meta — `content_publishing_limit`](https://developers.facebook.com/docs/instagram-platform/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit/)) **Real-world throttling commonly lands at 25–50**, and exceeding returns **Error 9**; the window is a **rolling timestamp window, not a calendar reset**, and newer/low-engagement accounts get stricter limits. ([Ayrshare — IG Graph Error 9](https://www.ayrshare.com/solutions/instagram-graph-api-error-9-the-25-post-daily-limit-how-to-fix-it/)) | Scheduler is **limit-aware**: pre-check `content_publishing_limit`; treat **25/24h as the practical ceiling** for batch spreading, 100 as the documented max. Over-cap → `GatedReason.overDailyCap`. (Consistent with `00-overview.md` and `05-screens-produce.md`.) |
| Carousel | `media_type=CAROUSEL`, up to **10** child containers via `children` (comma-sep IDs). | Out of v1 scope (video/Reels only — see Open questions); model leaves room. |
| Media format | Images must be **JPEG**. Video per Reels spec. | Not relevant to v1 (clips are video). |
| Scopes | `instagram_business_content_publish` (Instagram Business Login) **or** `instagram_content_publish` + `pages_read_engagement` (Facebook Login). | v1: Ayrshare owns scopes. DirectPublisher: request exactly these. |

> **"Post all week" is always safe.** A week of batch clips (≈5–10 posts) is far under even the conservative 25/24h ceiling. **There is no "flood" use case in Marque** — the scheduler spreads a batch across days (§6), never bursts.

### 2.2 TikTok Content Posting API

| Constraint | Detail | Encode as |
|---|---|---|
| **Mandatory pre-flight** | Call `POST /v2/post/publish/creator_info/query/` **before** showing the post screen, and render **ONLY** the returned `privacy_level_options` (e.g. `PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`, `SELF_ONLY`). **Hardcoding the privacy list = audit rejection** (a private account has no `PUBLIC_TO_EVERYONE`). ([TikTok — Query Creator Info](https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info)) | `preflight()` populates `PreflightResult.privacyOptions`; compose sheet renders only those. |
| Direct Post | `POST /v2/post/publish/video/init/` with `post_info` (`title` ≤ **2200 UTF-16 runes**, `privacy_level`, interaction toggles) + `source_info` (`source: PULL_FROM_URL` for an R2/Stream URL, or `FILE_UPLOAD` with `video_size`/`chunk_size`/`total_chunk_count`). Poll `POST /v2/post/publish/status/fetch/`. **Upload/pull URLs expire 1h after issuance.** ([TikTok — Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)) | v1: `PULL_FROM_URL` from R2/Stream. DirectPublisher polls `status/fetch`. Re-issue media URL if a scheduled fire is >1h after URL mint. |
| **`PULL_FROM_URL` domain verification** | The URL **host must be domain-verified** with TikTok (TT4D console URL-prefix ownership). | R2/Stream custom domain must be verified before TikTok Direct Post works; else `GatedReason.mediaNotReachable`. (Noted in `00-overview.md`.) |
| Upload-to-inbox (Creator Draft) | `/v2/post/publish/inbox/video/init/` deposits a **draft into the user's TikTok inbox**; the creator finishes/posts inside TikTok. | The **TikTok fallback** when Direct Post is gated (§5): `FallbackRoute.tiktokInboxDraft`. |
| **Unaudited-client trap** | Until the client passes audit, **all content is forced `SELF_ONLY` (private)** regardless of requested privacy. ([TikTok — Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started)) | v1 avoids this via Ayrshare's audited status. For any unaudited path → `GatedReason.clientUnaudited`. |
| **Commercial-content disclosure** | The post UI must expose a disclosure toggle (Your Brand / Branded Content). If toggled **on** with nothing selected, **disable the publish button**. ([TikTok — Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines)) | Compose sheet surfaces the toggle; unresolved → `GatedReason.disclosureUnresolved`, publish button disabled. |
| **Rate limits** | **6 requests/min per user access_token** for Direct Post init (`video/init/`) and for `status/fetch/`; **20 requests/min** for `creator_info/query/`; **~15 posts per creator per 24h via Direct Post, shared across ALL API clients** using that creator. Audit also imposes a per-client 24h active-creator cap from the stated usage estimate. ([TikTok — Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post), [TikTok — Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines)) | Scheduler caps TikTok at **≤15/creator/24h** *and* throttles publish calls to the **6/min** init ceiling (one preflight `creator_info/query` is cheaper at 20/min, so the binding constraint on a publish is the 6/min `video/init/` + `status/fetch/` budget — never size against 20/min). A burst of preflight→init→status for several clips must stay under 6 `video/init/` calls/min/token; spread batches accordingly. Over-cap → `GatedReason.overDailyCap` → inbox-draft fallback. |
| Scopes | `video.publish` (Direct Post), `video.upload` (inbox). | v1: Ayrshare owns scopes. |

---

## 3. OAuth / connection flow, token refresh, reconnection

### 3.1 v1 connection — Ayrshare JWT-SSO link flow (no Marque-held platform tokens)

In v1 Marque **does not store or refresh Meta/TikTok OAuth tokens** — a major reduction in token-rot surface. The flow:

1. Creator taps **Connect Instagram / Connect TikTok** (in Calendar's "Accounts" sheet, or the Settings → Connections row — never on Today).
2. FastAPI mints an **Ayrshare SSO JWT** scoped to the creator's `Profile-Key`.
3. The app opens Ayrshare's hosted link page in an **`ASWebAuthenticationSession`** (system-managed, ephemeral, no embedded webview — App Review-safe).
4. The creator authorizes IG/TikTok on the platform's own pages; **Ayrshare stores and refreshes the underlying platform tokens**.
5. On return, FastAPI reads connection state from Ayrshare and writes a `platform_connections` row with `health = connected`.

> **Why `ASWebAuthenticationSession`.** It is the Apple-blessed primitive for third-party auth: ephemeral session, shared system cookies optional, no token interception, and it satisfies App Review's prohibition on credential-harvesting webviews. SwiftUI wraps it via `AuthenticationServices`.

### 3.2 Token refresh — the DirectPublisher future (designed now, dormant in v1)

The refresh mechanics below are why the DirectPublisher migration is non-trivial. They are built behind a flag and exercised only when Marque owns tokens.

| Platform | Token model | Refresh rule | Trap |
|---|---|---|---|
| **Meta (IG)** | Long-lived token = **60 days** | Refresh once the token is **≥24h old** and not expired: `GET /refresh_access_token?grant_type=ig_refresh_token`. ([Meta — Refresh Access Token](https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token/)) | A token **not refreshed within 60 days is dead and un-refreshable → forced reconnect.** |
| **TikTok** | `access_token` ~24h + `refresh_token` ~365d | Refresh `access_token` **proactively** before expiry using the refresh token. | Let the refresh token lapse (~365d) → forced reconnect. |

**Scheduled refresh job (DirectPublisher only):** a **Trigger.dev cron** sweeps `platform_connections` for Meta tokens aged **24h–~50 days** and refreshes them well before the 60-day wall; TikTok tokens are refreshed on a tighter daily cadence. Each refresh updates `platform_connections.token_expires_at` and resets `health`.

### 3.3 `connection_health` model & reconnection UX

A per-platform, per-creator health enum drives everything:

```
connected      → green; publishing allowed
expiring       → amber; refresh attempted (DirectPublisher) or surfaced as a gentle nudge
needs_reconnect→ a publish failed with an auth/permission error
```

**On an auth/permission failure** the adapter:
1. Sets `platform_connections.health = needs_reconnect` for **that platform only**.
2. **Pauses the queue for that platform only** (sibling platform keeps publishing).
3. Surfaces a **calm one-line reconnect prompt** — in Calendar, and as a single optional gold glyph state, **never blocking Today**:

> *"Instagram needs to be reconnected to keep posting. Tap to reconnect."*

Reconnect re-runs §3.1. Queued `scheduled_posts` for that platform resume automatically once `health = connected`. In v1 this maps to Ayrshare's account-link state; in DirectPublisher it maps to the token tables above.

---

## 4. (reserved — token internals folded into §3)

> Token-refresh internals are specified inline in §3.2/§3.3 to keep the connection story in one place.

---

## 5. "Save & remind" fallback (REQUIRED — gating is the common case)

Direct publishing is gated when **any** of: TikTok client unaudited · creator over the ~15/day TikTok cap · IG over its 24h cap · IG account is Personal not Business · token `needs_reconnect` · TikTok disclosure unresolved · requested privacy unavailable · media URL not reachable/domain-verified. The Publisher returns a typed **`GatedResult`** (never throws for these), and Marque degrades gracefully so **the core loop always delivers a finished clip** — critical for first-run *before any account is connected.*

### 5.1 The fallback ladder

```
Publisher.enqueue(req)
  → .accepted(jobId)               // normal path: queued/published
  → .gated(GatedResult)
        ├─ platform == .tiktok AND reason ∈ {overDailyCap, clientUnaudited, privacyOptionUnavailable}
        │     → FallbackRoute.tiktokInboxDraft
        │       (deposit clip into TikTok inbox via /inbox/video/init/; creator finishes in-app)
        │
        └─ otherwise (hard gate: personal account, needs_reconnect, no connection at all)
              → FallbackRoute.saveAndRemind
                ├─ save rendered clip + caption to the creator's Library (already in R2/Stream)
                ├─ schedule a LOCAL APNs reminder at the optimal slot (§6)
                │   "Your Tuesday clip is ready — post it in 2 taps."
                └─ deep link → share sheet / "Save to Photos" export
```

- **TikTok inbox-draft** keeps the clip *inside TikTok's drafts* so the creator just hits post — the smoothest degraded path.
- **Save-&-remind** is the universal floor: even with **zero publishing permissions and no connected account**, the creator gets a finished, captioned clip in their Library plus a perfectly-timed nudge. This makes Marque valuable on day one, pre-connection.

### 5.2 Reminder copy & timing (aesthetic-locked)

Reminders use the calm, declarative voice (`02-design-system.md`). They fire at the **same optimal slot** the post *would* have been scheduled for (§6), localized to the creator's timezone. One reminder per gated clip; no nagging, no re-fire storms.

---

## 6. Scheduling engine

### 6.1 Storage — `scheduled_posts` & `publish_jobs`

> **Owning spec.** These tables are defined here; `12-backend-data-security.md` governs RLS + cross-table FKs. One `scheduled_posts` row **per (clip, platform)** — exactly matching the per-platform toggles in `05-screens-produce.md`.

```sql
-- One row per (clip, platform). The creator's INTENT to publish.
create table scheduled_posts (
  id                    uuid primary key default gen_random_uuid(),
  creator_id            uuid not null references creators(id),
  clip_id               uuid not null references clips(id),
  platform              text not null,            -- 'instagram_reels' | 'tiktok'
  caption               text not null,            -- per-platform variant (see Open Q in 05)
  cover_url             text,
  platform_options      jsonb not null default '{}',  -- shareToFeed / privacy / disclosure
  target_publish_at_utc timestamptz not null,     -- canonical scheduled instant (UTC)
  creator_tz            text not null,            -- IANA tz, e.g. 'America/Los_Angeles'
  status                text not null default 'scheduled',
    -- scheduled | publishing | published | gated | failed | canceled
  fallback_route        text,                     -- null | 'tiktok_inbox_draft' | 'save_and_remind'
  attempt_count         int  not null default 0,
  last_error            jsonb,                    -- { code, gatedReason, message, at }
  idempotency_key       text not null,            -- "{clip_id}:{platform}:{slot}"
  created_at            timestamptz not null default now(),
  unique (idempotency_key)
);

-- The DURABLE publish state machine. One row per actual publish attempt handed to a vendor.
create table publish_jobs (
  id                    uuid primary key default gen_random_uuid(),
  scheduled_post_id     uuid not null references scheduled_posts(id),
  state                 text not null default 'queued',
    -- queued | submitted | processing | live | gated | failed | canceled
  vendor                text not null default 'ayrshare',  -- 'ayrshare' | 'direct'
  vendor_ref            text,             -- Ayrshare post id / IG creation_id / TikTok publish_id
  platform_post_ref     text,             -- final live post id (for Insights)
  next_poll_at          timestamptz,
  attempt_count         int not null default 0,
  last_error            jsonb,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
```

### 6.2 Firing — Trigger.dev, never an in-process timer

- Each scheduled post is driven by a **Trigger.dev delayed/scheduled task** keyed to `target_publish_at_utc`. **No in-process timers** (they die with the FastAPI worker; durability is the whole point).
- **Idempotency:** the `idempotency_key = "{clip_id}:{platform}:{slot}"` is passed to `Publisher.enqueue`; a retry after a partial failure **cannot double-publish**.
- **Media-URL freshness (TikTok):** if a scheduled fire is **>1h** after the R2/Stream URL was minted, re-issue the public URL before calling the adapter (TikTok pull/upload URLs expire in 1h — §2.2).
- **Publish-call throttle (TikTok):** the per-token publish budget is **6 `video/init/` requests/min** and **6 `status/fetch/` requests/min** (§2.2) — *not* the 20/min that applies only to `creator_info/query`. The Trigger.dev firing layer serializes a creator's TikTok inits behind a **≤6/min token-scoped rate limiter** and backs `status/fetch` polling off the same 6/min ceiling, so a burst preflight+publish sequence across several batched clips never trips a 429. Size all TikTok scheduler concurrency against 6/min, never 20/min.
- **Pre-fire quota check (IG):** before firing an IG post in a batch, check `content_publishing_limit`; if exhausted, mark the row `gated(overDailyCap)` and route to save-&-remind rather than burning a failed attempt.

### 6.3 Optimal-time logic

**v1 — heuristic (ship this).** Default windows from aggregated industry data, **always computed in the audience's local timezone**, not the server's:

| Platform | Default windows (local time) | Source |
|---|---|---|
| Instagram | Mid-week afternoons — **Tue–Thu ~13:00–16:00** | [Sprout Social — Best Times to Post on Instagram](https://sproutsocial.com/insights/best-times-to-post-on-instagram/), [Buffer — Best Time to Post](https://buffer.com/resources/best-time-to-post-social-media/) |
| TikTok | **Tue–Thu 17:00–21:00**, plus a strong **Sun ~09:00** slot | [Buffer — Best Time to Post](https://buffer.com/resources/best-time-to-post-social-media/) |

**v2 — the moat (learned, per-creator).** Once Insights pullback (§7) accrues, **override the heuristic per creator**: bucket each creator's own posts by **(day-of-week × hour)** and rank by **reach-normalized** engagement (raw likes bias toward already-popular posts). Store the learned slots in the **Brand Graph** (`07-ai-system.md`) and feed them back into the scheduler. Every "best time" source explicitly says *your own audience data beats generic charts* — so the override is designed-in from day one. This is the literal "learns from performance and tightens the loop" promise.

**Batch-spreading rules (the hero loop is "film once → post all week"):**
- **Never** schedule a TikTok burst that blows the **~15/creator/24h shared cap** — spread the batch across days — *and* never let concurrent fires exceed the **6 `video/init/` requests/min/token** publish ceiling (§2.2/§6.2); the limiter serializes inits within a minute.
- **Never** assume a slot is free — pre-check IG `content_publishing_limit` (§6.2).
- Spread one batch across distinct days at each platform's optimal window; the creator sees a calm, pre-filled week in Calendar (`05-screens-produce.md`).

---

## 7. Analytics pullback → performance memory

### 7.1 Pull cadence

**Webhook-first**, with a polling safety net because engagement keeps accruing after publish:

| Trigger | When | Why |
|---|---|---|
| Webhook (Ayrshare analytics event) | On platform update | Cheapest, freshest. ([Ayrshare](https://www.ayrshare.com/docs/introduction)) |
| Poll `analytics/post` | **T+1h, T+24h, T+7d** after going live | Captures the early hook signal, the day-one curve, and the settled total. |

Each pull is a **Trigger.dev** task scheduled relative to `publish_jobs.platform_post_ref` going `live`. Pulls are **idempotent on `(post_ref, capturedAt-bucket)`** so a re-fire doesn't duplicate rows.

### 7.2 Storage — `post_metrics`

```sql
create table post_metrics (
  id                uuid primary key default gen_random_uuid(),
  creator_id        uuid not null references creators(id),
  clip_id           uuid not null references clips(id),
  platform          text not null,
  platform_post_ref text not null,
  captured_at       timestamptz not null,
  bucket            text not null,         -- 't+1h' | 't+24h' | 't+7d' | 'webhook'
  views             int, reach int, likes int, comments int,
  shares int, saves int,
  watch_time_sec    double precision,
  avg_watch_time_sec double precision,
  completion_rate   double precision,      -- 0..1, null if unreported
  follows_from_post int,
  raw               jsonb,                 -- vendor payload for forensic re-normalization
  unique (platform_post_ref, bucket)
);
```

Keyed by **`clip_id + platform`** so a single clip published to both networks is comparable head-to-head. Rows are **reach-normalized before any cross-post comparison** (same lesson as Cadence's learning loop): compare engagement *per reach*, not raw counts.

### 7.3 What the pullback feeds (three consumers)

1. **Optimal-time v2** (§6.3) — learned per-creator slots written into the Brand Graph.
2. **Coach teardown cards + push** (`05-screens-produce.md`, Section-8 feature 4) — each settled post becomes a calm teardown card and one push.
3. **Virality Engine training signal** (`07-ai-system.md`) — *which formats/hooks actually performed for THIS creator*, closing the prescriptive loop.

> All three read from `post_metrics` + Brand Graph; **none** of this surfaces on Today beyond the single trend line.

---

## 8. States (binding — every publishing surface implements these)

Per `02-design-system.md`, errors are calm and declarative; **never red, never a blocking modal on Today.**

| State | Trigger | UI / behavior |
|---|---|---|
| **loading** | Publish in progress / video still processing (IG container, TikTok status) | Per-item soft progress on the Calendar card — *not* a full-screen spinner. Breathing motion. "Posting your Tuesday clip…" |
| **empty** | No connected accounts | Calm "Connect Instagram or TikTok to publish" CTA **plus** the save-&-remind path so the clip is still usable. Today shows nothing extra. |
| **error** | Platform rejection with a typed reason (non-gating, e.g. media too long) | One calm line + **Retry** + the specific declarative reason. Logged to Sentry; surfaced in Calendar only. |
| **offline** | No network at fire time | The post **queues locally and fires on reconnect** (Trigger.dev durability owns the eventual fire; the client reflects "Scheduled, will post when back online"). No data loss. |
| **permission-denied / gated** | `GatedResult` (unaudited / over-cap / personal account / disclosure / privacy) | Run the **fallback ladder** (§5): TikTok inbox-draft or save-&-remind + reminder. Copy explains the *why* in one line and the *what next* in one tap. |
| **needs-reconnect** | Token expired / revoked | Pause **that platform's** queue only; single calm reconnect prompt (§3.3). Sibling platform unaffected. Today untouched (at most one gold glyph). |

---

## 9. Acceptance criteria

**Publisher adapter**
- [ ] No Ayrshare/Meta/TikTok type, error code, or concept appears in any caller of `Publisher`/`Insights`. Swapping `AyrsharePublisher → DirectPublisher` is a **single DI-line change** in `01-information-architecture.md`.
- [ ] `enqueue` returns within p95 < 800ms and **never** blocks on platform processing; the publish completes via `publish_jobs` reconciliation.
- [ ] `enqueue` is idempotent on `idempotency_key`: a duplicate fire produces **at most one** live post.
- [ ] Every platform rejection maps to a typed `GatedReason`; **none are swallowed**.

**Platform compliance**
- [ ] TikTok compose sheet renders **only** the privacy options returned by `creator_info/query` for *this* creator (no hardcoded list).
- [ ] TikTok commercial-disclosure toggle is present; when on with nothing selected, **publish is disabled**.
- [ ] IG publish is attempted only after the container reports `status_code = FINISHED`.
- [ ] Scheduler checks IG `content_publishing_limit` before firing a batch and caps TikTok at ≤15/creator/24h.
- [ ] R2/Stream media URL is public HTTPS and (for TikTok) on a **domain-verified** host; a fire >1h after URL mint re-issues the URL.

**Scheduling & fallback**
- [ ] All scheduled fires run on **Trigger.dev**, not in-process timers; an app/worker restart loses **zero** scheduled posts.
- [ ] Optimal-time computes in the **creator/audience timezone**; v2 learned slots override the heuristic once `post_metrics` exists.
- [ ] A "film once → post all week" batch is **spread across days**, never bursted past any cap.
- [ ] With **no connected account**, a finished, captioned clip still lands in the Library + a single optimal-slot reminder fires.

**Analytics**
- [ ] `post_metrics` populated at T+1h / T+24h / T+7d (or via webhook), idempotent on `(post_ref, bucket)`.
- [ ] Unreported metrics are `nil`, never `0`. Cross-post comparison is reach-normalized.
- [ ] Metrics flow to optimal-time v2, Coach teardowns (`09`), and the Virality Engine (`07`).

**Anti-clutter**
- [ ] Today shows at most a single gold status glyph + one next-post line; **no** publishing controls, errors, or reconnect modals appear on Today.

---

## 10. Hard do's / don'ts (quick reference)

**DO**
- Render to a **public HTTPS R2/Stream URL** — both IG `video_url` and TikTok `PULL_FROM_URL` require it; TikTok additionally requires the host be **domain-verified**.
- **Poll status before publishing** (IG `status_code=FINISHED`; TikTok `status/fetch`).
- Call TikTok `creator_info/query` **every time** and render only returned privacy options; surface commercial disclosure.
- Keep **all** publishing behind the adapter so the Ayrshare → DirectPublisher migration is one file.
- Design the token-refresh cron for the DirectPublisher future even though v1 doesn't run it.

**DON'T**
- Store Meta/TikTok tokens in v1 (Ayrshare owns them).
- Block the SwiftUI client on a publish round-trip.
- Burst a batch past IG's practical 24h ceiling, TikTok's ~15/creator/24h shared cap, or TikTok's **6 `video/init/` requests/min/token** publish rate limit — and never size the TikTok publish throttle against the 20/min figure (that limit covers only `creator_info/query`, not publishing).
- Let any of this touch **Today** beyond one status glyph; publishing lives in Calendar + push reminders.
- Coerce an unreported metric to `0`, or compare raw counts across posts without reach-normalizing.

---

## Open questions

1. **v1 = Ayrshare confirmed?** Assumed yes (de-risks TikTok audit + Meta App Review). If Marque *also* pursues its own Content Posting API audit + Meta App Review for the DirectPublisher, budget the **2–6 week TikTok audit** (recorded demo + privacy-policy URL + stated usage estimate) and Meta App Review — and the token-refresh ownership in §3.2. **Decision needed before any DirectPublisher work.**
2. **Audience timezone source.** Optimal-time accuracy depends on whether "audience local time" = the creator's account timezone, a stated primary-audience region, or **Insights-derived follower geo**. v1 uses creator account tz; v2 should prefer follower geo if exposed. **Which source?**
3. **Carousel / photo posts in v1?** This doc scopes v1 to **video/Reels + TikTok video only**. Enabling IG carousels changes the container fan-out (≤10 children) and would add the TikTok photo-post endpoint. **Confirm video-only for v1.**
4. **TikTok inbox-draft as the standard fallback** vs. *only* the save-&-remind library path? Inbox-draft is smoother but requires the `video.upload` scope/flow; save-&-remind always works. **Enable both, or library-only for v1?**
5. **Per-platform caption variants.** Cross-ref `05-screens-produce.md` Open Q: v1 assumes a shared caption with the `scheduled_posts.caption` column already per-row to allow divergence. **Confirm shared-vs-variant for v1 compose UI.**

---

## Sources

- [Meta — Publish Content using the Instagram Platform](https://developers.facebook.com/docs/instagram-platform/content-publishing/) — canonical container → `media_publish` flow, Reels params, `status_code` polling.
- [Meta — `content_publishing_limit` reference](https://developers.facebook.com/docs/instagram-platform/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit/) — the documented 100-posts/24h limit and how to query remaining quota.
- [Ayrshare — Instagram Graph API Error 9 (the ~25-post daily limit)](https://www.ayrshare.com/solutions/instagram-graph-api-error-9-the-25-post-daily-limit-how-to-fix-it/) — real-world throttling, Error 9, rolling-window behavior.
- [Meta — Refresh Access Token](https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token/) — 60-day long-lived token refresh rules and the un-refreshable-after-60-days trap.
- [TikTok — Content Posting API Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started) — audit requirement; unaudited client = `SELF_ONLY` only; sandbox restrictions.
- [TikTok — Direct Post reference](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post) — `video/init/`, `PULL_FROM_URL` vs `FILE_UPLOAD`, `post_info`/`source_info`, 1h URL TTL, `status/fetch`; the per-token rate limits (**6 requests/min** each for `video/init/` and `status/fetch/`, **20 requests/min** for `creator_info/query/`).
- [TikTok — Query Creator Info](https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info) — mandatory pre-flight; dynamic `privacy_level_options`.
- [TikTok — Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) — mandatory commercial-content disclosure UI; the ~15 posts/creator/24h Direct Post cap shared across all clients.
- [Ayrshare — Docs Introduction](https://www.ayrshare.com/docs/introduction) — `/post`, `scheduleDate` ISO-8601, `/analytics/post` + `/analytics/social`, `Profile-Key` multi-user, webhooks, supported platforms.
- [Ayrshare — Auto-Schedule endpoint](https://www.ayrshare.com/understanding-the-ayrshare-auto-schedule-api-endpoint/) — pre-defined time-slot scheduling for the heuristic default.
- [Ayrshare](https://www.ayrshare.com/) — aggregator with approved TikTok audited-client status + Meta App Review (the v1 de-risking rationale).
- [Buffer — Best Time to Post on Social Media](https://buffer.com/resources/best-time-to-post-social-media/) — IG/TikTok heuristic windows (local time), large-dataset basis.
- [Sprout Social — Best Times to Post on Instagram](https://sproutsocial.com/insights/best-times-to-post-on-instagram/) — IG mid-week-afternoon data; "your own audience beats generic charts."
