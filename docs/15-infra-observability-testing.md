# 15 · Infra, Observability, CI/CD & Testing

> **Section owner:** Platform / DevEx
> **Status:** Build-ready
> **Canonical names used here:** `Brand Graph`, `ClipEngine` adapter, `Publisher` adapter, `Insights` adapter, `Subscriptions` adapter, the **Format Library** of render-recipes, the **Today** screen one-directive doctrine.
> **Cross-references:** `00-overview.md` (system map), `06-brand-graph.md` (Brand Graph schema), `07-ai-system.md` (Anthropic/Opus 4.8 + Haiku 4.5 prompts & evals — referenced, not re-specced), `09-video-pipeline.md` (`ClipEngine` MCP toolchain), `09-video-pipeline.md` (FastAPI + Trigger.dev task shapes), `10-social-publishing.md` (`Publisher`/`Insights`, Instagram Graph + TikTok Content Posting), `12-backend-data-security.md` (RLS, secrets, encryption, threat model, `storage_backend` residency — the DR *prevention* counterpart), `13-notifications-retention.md` / `11-monetization.md` (`Subscriptions` / StoreKit 2 + RevenueCat entitlements), `14-appstore-compliance-legal.md` (GDPR export/erasure §14.11, account-deletion cascade §14.6 — coordinated with the residency + backup-erasure posture in 15.9). This section owns the *operational* surface: environments, pipelines, observability, cost, **disaster recovery & data residency**, performance, and test strategy. It deliberately does **not** re-specify vendor adapter internals — it points to those docs.

This document is the operational backbone for Marque. It is written to be executed against, not admired. Every table, schema block, and checklist here is meant to be lifted directly into code, CI config, dashboards, or runbooks.

The governing constraint from the rest of the spec applies here too: **Marque is a calm app.** That doctrine has an infra corollary — *operational complexity must never leak into the product.* Flags hide unfinished features one layer deep instead of shipping half-built UI to **Today**; cost spikes trigger a silent kill-switch, not a degraded user experience; a failed render retries durably and streams honest progress rather than dumping an error toast. The product is quiet because the plumbing is loud (to us) and disciplined.

---

## 15.1 Environments & configuration

### 15.1.1 The three-environment model

Marque runs **three fully isolated stacks**. No stack ever shares a database, a vendor key, or a bucket with another. "Isolated" is load-bearing: a staging migration must never be able to touch a prod row, and a dev render must never be able to publish to a real Instagram account.

| Environment | Purpose | Audience | Supabase project | Anthropic workspace | Trigger.dev env | iOS build config | Bundle id |
|---|---|---|---|---|---|---|---|
| **dev** | Local + shared integration; chaos allowed | Engineers | `marque-dev` | `marque-dev` | `dev` | `Debug-Dev` | `com.marque.app.dev` |
| **staging** | Pre-prod soak; mirrors prod exactly | Internal TestFlight, QA | `marque-staging` | `marque-staging` | `staging` | `Release-Staging` | `com.marque.app.staging` |
| **prod** | Live | App Store users | `marque-prod` | `marque-prod` | `production` | `Release-Prod` | `com.marque.app` |

**Supabase: one project per environment — never share a project across envs.** Each project gets its own Postgres, Auth, Storage, Realtime, and its own `anon`/`publishable` key + `service_role` key. For ephemeral per-PR preview databases we use **Supabase branching** (`create_branch` / `merge_branch`) — a branch is a short-lived clone of the parent project for testing a migration in isolation, not a long-lived environment. Branches are created on PR open and torn down (`delete_branch`) on merge/close. The three long-lived projects above are permanent.

The three iOS bundle ids co-exist on a single device (dev + staging + prod side-by-side), which lets QA run all three without uninstall churn. App icons are tinted per-env (dev: red corner ribbon, staging: amber, prod: clean) so nobody ever demos the wrong build.

### 15.1.2 iOS configuration via `.xcconfig`

iOS configuration is driven by **per-scheme `.xcconfig` files** mapped to Build Configurations (`Debug-Dev`, `Release-Staging`, `Release-Prod`). Each config file carries only the values that legitimately ship in a client binary.

```
ios/Config/
  Base.xcconfig          # shared: app name template, deployment target (17.0)
  Dev.xcconfig           # SUPABASE_URL, SUPABASE_ANON_KEY, API_BASE_URL, ...
  Staging.xcconfig
  Prod.xcconfig
  Secrets.local.xcconfig  # gitignored; CI writes this at build time
```

```ini
# Dev.xcconfig
APP_DISPLAY_NAME        = Marque Dev
PRODUCT_BUNDLE_ID       = com.marque.app.dev
API_BASE_URL            = https:/$()/api-dev.marque.app
SUPABASE_URL            = https:/$()/<dev-ref>.supabase.co
SUPABASE_ANON_KEY       = $(SUPABASE_ANON_KEY)        // injected by CI, never committed
REVENUECAT_PUBLIC_KEY   = $(REVENUECAT_PUBLIC_KEY)
SENTRY_DSN              = $(SENTRY_DSN)
POSTHOG_PROJECT_KEY     = $(POSTHOG_PROJECT_KEY)
POSTHOG_HOST            = https:/$()/us.i.posthog.com
ENV_NAME                = dev
```

> The `/$()/` interpolation guard is the standard `.xcconfig` trick to keep `//` from being parsed as a comment in a URL.

**Critical pitfall — the empty-`.xcconfig` failure mode.** An empty or partially-populated `.xcconfig` lets the build **succeed, archive, upload to TestFlight, and then break on a real device** when the missing `SUPABASE_URL` resolves to an empty string and every network call 404s. This is the worst failure mode because every gate before the device is green. The mitigation is a **fail-fast pre-build check** that asserts every required variable is non-empty and exits hard if not — see [Claude Lab's Xcode Cloud field report](https://claudelab.net/en/articles/claude-code/claude-code-xcode-cloud-ci-scripts-testflight-indie-dev-workflow).

```bash
# ci_scripts/ci_pre_xcodebuild.sh  — runs in Xcode Cloud, fails closed
set -euo pipefail
REQUIRED=(SUPABASE_URL SUPABASE_ANON_KEY API_BASE_URL REVENUECAT_PUBLIC_KEY \
          SENTRY_DSN POSTHOG_PROJECT_KEY ENV_NAME)
missing=0
for var in "${REQUIRED[@]}"; do
  if [ -z "${!var:-}" ]; then echo "::error:: missing required config: $var"; missing=1; fi
done
[ "$missing" -eq 0 ] || exit 64   # EX_USAGE — abort the whole build, do NOT ship empty secrets
```

> **Xcode Cloud gotcha:** turn **OFF "Continue on script failure."** With it on, a failing `ci_pre_xcodebuild.sh` still proceeds to archive and ship the empty-secrets binary — defeating the entire guard ([Claude Lab](https://claudelab.net/en/articles/claude-code/claude-code-xcode-cloud-ci-scripts-testflight-indie-dev-workflow)).

### 15.1.3 Secrets doctrine — nothing sensitive in the binary

The iOS app ships **only publishable/public keys**. Every secret lives behind the FastAPI orchestration service (see `09-video-pipeline.md`). This mirrors the standing "move the key to the backend" rule already adopted elsewhere in the product.

| Key | Where it lives | Shipped in iOS binary? |
|---|---|---|
| Supabase `anon` / `publishable` | iOS + backend | ✅ public by design (RLS-gated) |
| Supabase `service_role` | Backend only | ❌ |
| RevenueCat **public SDK** key | iOS | ✅ |
| RevenueCat **secret** (`sk_…` v2) key | Backend / PostHog connector only | ❌ |
| Anthropic API key (Opus 4.8 / Haiku 4.5) | Backend only | ❌ |
| AssemblyAI, Shotstack, Ayrshare/Phyllo | Backend only | ❌ |
| Cloudflare R2 / Stream credentials | Backend only | ❌ |
| Sentry **DSN** | iOS + backend | ✅ DSN is not a secret |
| PostHog **project** key | iOS + backend | ✅ |
| Anthropic **Admin** key (Usage/Cost API) | Cost-monitor cron only | ❌ |

Backend secrets are stored in the host's secret manager (see Open questions on host choice) and surfaced as env vars. A `settings.is_production` flag gates every test/bypass endpoint: in prod those routes return `404`, exactly mirroring the paywall-bypass hardening pattern already in the codebase. The Supabase pooler port is pinned (`SUPABASE_DB_PORT=6543`) per the established backend convention.

### 15.1.4 Config States

| State | Behavior |
|---|---|
| **Missing required config (CI)** | `exit 64`, build aborts, Slack alert to `#marque-ci` |
| **Missing optional config (runtime)** | Feature flag off; feature hidden one layer deep; no crash |
| **Stale config cache (app)** | Remote config (15.4) revalidates on foreground; last-good cached value used until then |
| **Wrong env keyed into a build** | Bundle id mismatch fails `ci_post_clone.sh` assertion before archive |

---

## 15.2 iOS CI/CD & TestFlight

### 15.2.1 Recommendation: Xcode Cloud as the spine

For an iOS-only SwiftUI app, **Xcode Cloud is the spine; Fastlane is held in reserve** for the day Android or a web surface appears. Xcode Cloud removes the single most fragile layer in iOS CI — **code signing** — by using App Store Connect team certs directly rather than Fastlane `match`'s encrypted cert repo (which historically "broke a few times a year" on cert renewals). TestFlight delivery is native to the workflow, avoiding flaky `pilot`/`upload_to_testflight` hangs. The free tier is **25 compute hours/month** ([techconcepts](https://techconcepts.org/blog/github-actions-ios)), enough for early Marque; we scale the plan with team size. Source: [Claude Lab field report](https://claudelab.net/en/articles/claude-code/claude-code-xcode-cloud-ci-scripts-testflight-indie-dev-workflow).

### 15.2.2 The three-workflow topology

This is the battle-tested shape; copy it.

| Workflow | Trigger | Actions | Delivery | Notes |
|---|---|---|---|---|
| **Pull Request** | PR open/update | build + unit tests + snapshot tests + lint | **none** | **No dSYM upload** — PR builds must not pollute Sentry symbol history |
| **TestFlight Beta** | push to `develop` | build + tests + Maestro smoke + dSYM upload + TestFlight **Internal** Group | immediate | Internal testers get it in minutes, no review |
| **Production** | tag push `v*` | build + dSYM upload + TestFlight **External** Group + Slack notify | via Apple **Beta App Review** | External hits review (hours–~1 day) — notify only here |

### 15.2.3 CI scripts (Xcode Cloud hooks) & hard-won rules

Xcode Cloud runs three optional shell hooks. Marque uses all three:

| Hook | Job |
|---|---|
| `ci_post_clone.sh` | Assert `CI_PRODUCT_BUNDLE_ID` matches the env being built; bootstrap SPM cache |
| `ci_pre_xcodebuild.sh` | Inject secrets into `Secrets.local.xcconfig`; run the fail-fast config check (15.1.2) |
| `ci_post_xcodebuild.sh` | Upload dSYMs to Sentry — **only on non-PR workflows** |

```bash
# ci_scripts/ci_post_xcodebuild.sh
set -euo pipefail
# Branch on CI_WORKFLOW so PR builds NEVER dump dSYMs (fills Sentry symbol history in a day)
if [ "$CI_WORKFLOW" = "Pull Request" ]; then
  echo "PR workflow — skipping dSYM upload"; exit 0
fi
if [ -d "$CI_ARCHIVE_PATH/dSYMs" ]; then
  sentry-cli debug-files upload --include-sources \
    --org marque --project marque-ios "$CI_ARCHIVE_PATH/dSYMs"
fi
```

**Rules distilled from the field report ([Claude Lab](https://claudelab.net/en/articles/claude-code/claude-code-xcode-cloud-ci-scripts-testflight-indie-dev-workflow)):**

1. **"Continue on script failure" OFF.** A failing pre-build script must abort, not ship.
2. **Branch dSYM upload on `CI_WORKFLOW`.** PR builds without this filter fill Sentry's symbol store within a day. dSYMs live in `$CI_ARCHIVE_PATH/dSYMs/`.
3. **Internal vs External delivery.** Internal Group = immediate; External Group = Beta App Review. Only the Production workflow notifies, because only it crosses review.
4. **`DEBUG_INFORMATION_FORMAT = "DWARF with dSYM File"`** on every Release config (default for Release, *not* Debug). Without dSYMs, Sentry crashes are unsymbolicated stack addresses (see 15.5).

### 15.2.4 Build numbering

TestFlight requires a **monotonic, unique `CFBundleVersion`** per upload. Derive it from CI, never by hand:

```
CFBundleVersion = $CI_BUILD_NUMBER            # Xcode Cloud auto-increments
# Fastlane fallback: git rev-list --count HEAD  (or latest_testflight_build_number + 1)
CFBundleShortVersionString = read from tag v1.4.0 → 1.4.0
```

### 15.2.5 Fastlane fallback (reserved for cross-platform)

If Android/web later forces Fastlane in, these are the non-obvious facts that prevent the recurring failures:

- `match(readonly: ENV["CI"] ? true : false)` — stop CI from overwriting/regenerating certs.
- `latest_testflight_build_number + 1` — auto-bump build number.
- Pin Xcode explicitly: `sudo xcode-select -s /Applications/Xcode_16.app` — the default runner Xcode changes silently and breaks build-number resolution ([soarias](https://soarias.com/swiftui/how-to-implement-ci-with-fastlane/), [techconcepts](https://techconcepts.org/blog/github-actions-ios)).
- Start at **2 parallel test workers** on 3-core runners.
- App Store Connect API key needs the **App Manager** role for uploads.

### 15.2.6 CI/CD States

| State | Behavior |
|---|---|
| **Loading** | PR shows pending checks; merge blocked until all green |
| **Build fail (compile/test)** | PR check red, no delivery, author notified |
| **Config-missing** | `exit 64`, no archive, `#marque-ci` alert |
| **Beta App Review pending (External)** | Slack notice; release manager waits for "Ready to Test" |
| **dSYM upload fail** | Build still ships; Sentry "missing dSYM" alert fires; release checklist item flips red |

---

## 15.3 Backend deploy & migrations

See `09-video-pipeline.md` for FastAPI/Trigger.dev internals. Operationally:

### 15.3.1 Database migrations — forward-only, gated

- Migrations are **Supabase CLI SQL files** committed to the repo: `supabase/migrations/<timestamp>_<name>.sql`. They are **forward-only** — no destructive auto-rollback against prod.
- CI applies them with `supabase db push` (or MCP `apply_migration`) to a **Supabase branch** first, runs the test suite against the branch, then `merge_branch` on green.
- After every migration, CI runs **`get_advisors`** (Supabase security + performance lints) and fails the job on any new security finding (e.g., a table without RLS, an exposed view).
- **Never auto-apply to prod without a staging soak ≥ 24h** (see release checklist 15.12).

```
PR opened ──► create_branch(marque-staging) ──► apply migration to branch
          └─► run pytest + get_advisors against branch
              ├─ pass ─► allow merge ─► staging soak 24h ─► tag v* ─► apply to prod
              └─ fail ─► block merge ─► delete_branch
```

### 15.3.2 FastAPI service deploy

- Containerized; **rolling deploy with a readiness gate** (host TBD — see Open questions; Fly.io / Render / Railway all viable). Old instances drain in-flight requests before termination.
- Endpoints required for orchestration:
  - `GET /healthz` — liveness (process up).
  - `GET /readyz` — readiness (Supabase reachable, Anthropic reachable, Trigger.dev token valid). The load balancer routes only when `readyz` is 200.
- `settings.is_production` gates bypass/test routes to `404` in prod.

### 15.3.3 Trigger.dev deploy

- Tasks deploy **separately** from FastAPI: `trigger.dev deploy --env staging|production`. Trigger.dev natively supports `staging` and `production` environments ([Trigger.dev runs](https://trigger.dev/docs/runs)), each with its own queue and secrets.
- A task version bump is independent of an API deploy, so the long-running video pipeline can be updated without a web restart.

### 15.3.4 Deploy States

| State | Behavior |
|---|---|
| **Migration lints fail (`get_advisors`)** | Merge blocked; finding posted to PR |
| **Readiness fail post-deploy** | New instances never receive traffic; rollback to last-good image |
| **Trigger.dev deploy fail** | Old task version keeps serving; alert; no partial cutover |
| **Staging soak not met** | Production tag rejected by release gate |

---

## 15.4 Feature flags & remote config

**PostHog feature flags are the single flag/config system** — already in the stack, one SDK for analytics + flags + experiments + remote config. Flags appear automatically on every captured event as `$active_feature_flags`, so flag exposure is analyzable without extra instrumentation.

### 15.4.1 What flags are for in Marque

| Flag | Type | Purpose |
|---|---|---|
| `format_<recipe>_enabled` | boolean | Gradual rollout of a new Format Library render-recipe (e.g. `format_green_screen_enabled`) without a rebuild |
| `clipengine_vendor` | string (multivariate) | Route `ClipEngine` to a vendor; **per-vendor kill-switch** if one degrades |
| `paywall_price_experiment` | multivariate | Price/plan experiment (coordinates with `11-monetization.md`) |
| `trend_radar_enabled` | boolean | Toggle the **Today** trend line + Trends screen entirely |
| `repurpose_upload_enabled` | boolean | Gate the "upload existing long video" second source on Record |
| `perf_budgets` | JSON payload | Remote-tuned performance budget thresholds (15.10) |
| `default_render_preset` | JSON payload | Non-boolean config: default format set per niche |
| `maintenance_mode` | boolean | Global write-freeze for a DR restore (15.9.5) — app shows a calm "We'll be right back" screen, never a raw error |

### 15.4.2 Rules

- **Flag/config keys are static and never interpolated** — same discipline as event names (15.6). `format_${name}_enabled` is forbidden; enumerate each recipe.
- **Non-boolean config rides on flag payloads** (JSON) rather than a second system.
- A flag must be able to **hide an entire feature one layer deep** without shipping UI — this is the infra expression of the anti-clutter doctrine. A half-finished Trends screen is flagged off, not bolted onto **Today** in a broken state.
- Every new format/feature ships **flag-off by default** and is ramped (15.12 release checklist).

---

## 15.5 Sentry — crash & performance

### 15.5.1 iOS setup

- SDK: **sentry-cocoa via SPM** (CocoaPods support is dropped). Initialize in `application(_:didFinishLaunchingWithOptions:)`, **on the main thread, as early as possible** ([Sentry iOS docs](https://docs.sentry.io/platforms/apple/guides/ios/)).

```swift
SentrySDK.start { options in
    options.dsn = AppConfig.sentryDSN
    options.environment = AppConfig.envName            // dev | staging | prod
    options.releaseName = "marque@\(AppConfig.version)+\(AppConfig.build)"
    options.tracesSampleRate = AppConfig.isProd ? 0.2 : 1.0   // < 1.0 in prod
    options.sendDefaultPii = false                     // privacy-forward: keep false
    options.enableLogs = true
    options.configureProfiling = {
        $0.lifecycle = .trace
        $0.sessionSampleRate = AppConfig.isProd ? 0.2 : 1.0
    }
    options.sessionReplay.onErrorSampleRate = 0.1      // replay only around errors
}
```

- **dSYMs are mandatory for symbolication.** Require `DEBUG_INFORMATION_FORMAT = "DWARF with dSYM File"` on Release. Upload via `sentry-cli debug-files upload` (skips already-uploaded files). `--include-sources` adds source context; omit it if we don't want source uploaded ([Sentry dSYM docs](https://docs.sentry.io/platforms/apple/dsym/)).
- **Crashes are captured only without a debugger attached** — verify crash reporting in a **TestFlight** build, never an Xcode-run build.
- `sendDefaultPii = false` by default for a privacy-forward app; the creator's content is sensitive.

### 15.5.2 Backend + pipeline tracing

FastAPI and every Trigger.dev task use the **Sentry Python SDK with distributed tracing enabled**, so a single `trace_id` spans the whole hop chain:

```
iOS (record) ─► FastAPI ─► Trigger.dev run ─► AssemblyAI ─► ClipEngine (MCP) ─► Shotstack ─► R2/Stream ─► Publisher (Ayrshare)
        └──────────────────────── one trace_id ────────────────────────────────────────────────┘
```

The `trace_id` is generated on the iOS device when a job starts and propagated as a header into FastAPI, then onto the Trigger.dev run, then into each vendor call via breadcrumbs. This makes "creator X's Tuesday batch failed at Shotstack" a one-click trace, not a log-grep.

### 15.5.3 Sentry States / alerts

| Condition | Alert |
|---|---|
| New crash signature in a release | Slack `#marque-crashes`, assign release manager |
| Crash-free sessions < 99.5% (prod) | Page on-call |
| Missing dSYM for current release | `#marque-ci`, blocks release checklist item |
| Trace failure rate in pipeline stage > 5% | Slack `#marque-pipeline` with the failing `stage` |

---

## 15.6 PostHog product analytics — event taxonomy

This is the single most reusable artifact in this section. Get the names right *once*; **event names cannot be changed after creation** in PostHog.

### 15.6.1 Naming rules (enforced in a shared constants file)

Per [PostHog best practices](https://posthog.com/docs/product-analytics/best-practices) and [Scopetrics' tracking-plan guide](https://scopetrics.com/resources/posthog-tracking-plan-guide):

- **Framework:** `category:object_action`, **snake_case, lowercase, present-tense verbs.**
- **Allowed verbs:** `click, submit, create, view, add, invite, update, delete, remove, start, end, cancel, fail, generate, send, complete`.
- **Never interpolate** event or property names. `format_${name}_render` creates unbounded event definitions and gets rate-limited. Use a static name plus a property: event `clip:render_complete`, property `format = "green_screen"`.
- **Property naming:** `object_adjective` (`clip_count`, `script_id`); booleans `is_`/`has_` (`is_subscribed`, `has_seen_paywall`); timestamps end `_timestamp`, dates end `_date`.
- **Schema management:** type events upfront via PostHog **Property Groups** with a committed `posthog.json`; CI lints every `capture()` against it.

All events are emitted through a single typed wrapper so naming can't drift:

```swift
// Analytics.swift — the ONLY place capture() is called
enum MarqueEvent: String {
    case onboardingAppOpen        = "onboarding:app_open"
    case brandGraphAnalysisComplete = "brand_graph:analysis_complete"
    case recordSessionComplete    = "record:session_complete"
    case clipRenderComplete       = "clip:render_complete"
    case publishPublishComplete   = "publish:publish_complete"
    // ...full enum below
}
```

### 15.6.2 Context properties on **every** event

| Property | Example | Notes |
|---|---|---|
| `platform` | `ios` | constant |
| `app_version` | `1.4.0` | from `CFBundleShortVersionString` |
| `build_number` | `812` | for crash↔analytics correlation |
| `subscription_status` | `trial` / `active` / `free` / `expired` | sourced from RevenueCat entitlement, **not** guessed |
| `experiment_variant` | `paywall_b` | mirrors `$active_feature_flags` |
| `session_id` | uuid | client session |
| `env` | `prod` | so dev/staging events are filterable out |

### 15.6.3 The full funnel taxonomy (AARRR-mapped)

| Stage | Event | Key properties |
|---|---|---|
| **Acquisition** | `onboarding:app_open` | `is_first_open`, `referrer` |
| Acquisition | `onboarding:question_view` | `question_id` (e.g. `what_known_for`) |
| Acquisition | `onboarding:question_submit` | `question_id`, `answer_length` |
| **Activation** | `brand_graph:page_connect_submit` | `source` (`instagram`/`tiktok`/`url`), `is_success` |
| Activation | `brand_graph:analysis_start` | `source` |
| Activation | `brand_graph:analysis_complete` | `duration_ms`, `niche_detected`, `voice_confidence` |
| Activation | `script:generate_start` | `model` (`opus-4-8`), `brand_graph_id` |
| Activation | `script:generate_complete` | `script_count`, `cached_tokens`, `uncached_tokens`, `duration_ms` |
| Activation | `hook_lab:variant_view` | `script_id`, `hook_id` (Hook Lab nested in script reader) |
| Activation | `hook_lab:variant_select` | `script_id`, `hook_id` |
| **Core / Hero** | `record:session_start` | `source` (`camera`/`upload_existing`), `script_id` |
| Core / Hero | `record:session_complete` | `source`, `take_count`, `duration_s` |
| Core | `repurpose:upload_existing_submit` | `video_length_s`, `file_size_mb` |
| **Engagement** | `clip:render_start` | `format`, `engine` (ClipEngine vendor), `clip_count`, `trace_id` |
| Engagement | `clip:render_complete` | `format`, `engine`, `clip_count`, `duration_ms`, `cost_usd` |
| Engagement | `clip:render_fail` | `format`, `engine`, `error_code`, `stage` |
| Engagement | `publish:schedule_submit` | `target` (`instagram`/`tiktok`), `scheduled_timestamp`, `clip_count` |
| Engagement | `publish:publish_complete` | `target`, `clip_id`, `post_id` |
| Engagement | `publish:publish_fail` | `target`, `error_code` |
| Engagement | `trends:radar_line_click` | `trend_id` (the one **Today** trend line) |
| Engagement | `trends:screen_view` | `trend_id`, `source` |
| **Retention** | `today:directive_view` | `directive_id` (one-directive doctrine) |
| Retention | `today:directive_complete` | `directive_id`, `directive_type` |
| Retention | `coach:teardown_view` | `clip_id`, `metric_delta`, `source` (`feed`/`push`) |
| Retention | `streak:milestone_reach` | `streak_days` |
| **Revenue** | `paywall:view` | `source`, `price_tier`, `has_seen_paywall` |
| Revenue | `paywall:plan_select` | `price_tier`, `trial` |
| Revenue | `subscription:start` / `:renew` / `:cancel` | `price_tier`, `trial` — **but see 15.6.4** |
| **Referral** | `referral:prompt_view` | `trigger` (`genuine_win`) — the ONE earned prompt |
| Referral | `referral:invite_send` | `channel` |

> `format` enumerated values (the Format Library render-recipes): `split_screen`, `three_up`, `green_screen`, `faceless`, `before_after`, `myth_buster`, `listicle`, `pov`, `reaction`, `broll_hook`. These are a **closed set** — adding a recipe adds an enum value, never a new event name.

### 15.6.4 Revenue events: trust RevenueCat, not the client

Client-side purchase events are **not** the source of truth. Use the **RevenueCat → PostHog data-warehouse connector**: it syncs customers/products/entitlements plus real-time webhook events (`initial_purchase`, `renewal`, `cancellation`, `billing_issue`). It requires a RevenueCat **v2 secret key** (`sk_…`) with Integrations **write** permission so PostHog can auto-register the webhook (otherwise register it manually) ([PostHog RevenueCat source](https://posthog.com/docs/data-warehouse/sources/revenuecat)). The client `subscription:*` events above are kept for *funnel-step* analysis only; revenue reporting reconciles against the connector. See `11-monetization.md` for entitlement details.

### 15.6.5 Analytics States

| State | Behavior |
|---|---|
| **Offline** | Events buffered by the PostHog SDK, flushed on reconnect |
| **Opt-out** | If the user declines tracking, capture is disabled; only crash (Sentry, no PII) remains |
| **New unbounded property detected (CI)** | `posthog.json` lint fails the PR |
| **Version skew** | `app_version` on the event lets us scope a regression to the offending build |

---

## 15.7 Structured logging & tracing across the video pipeline

### 15.7.1 One `trace_id` per creator job

Every job carries **one `trace_id`** from the device through every hop. All logs are **structured JSON** with a fixed envelope:

```json
{
  "trace_id": "0f3c…",
  "job_id": "job_8821",
  "creator_id": "usr_4410",
  "stage": "render",                  // ingest | transcribe | analyze | clip | render | store | publish
  "engine": "shotstack",
  "format": "three_up",
  "status": "ok",                     // ok | retry | fail
  "duration_ms": 41280,
  "cost_usd": 0.1840,
  "attempt": 1,
  "env": "prod",
  "ts": "2026-06-29T14:02:11.004Z"
}
```

`cost_usd` on every stage feeds the cost ledger (15.8) directly — logging and cost accounting share one envelope so they can never disagree.

### 15.7.2 Trigger.dev as durable orchestration + observability

Trigger.dev gives durable, multi-hour orchestration with built-in run observability ([Trigger.dev media-processing](https://trigger.dev/docs/guides/use-cases/media-processing), [tasks overview](https://trigger.dev/docs/tasks/overview)). The operational levers we rely on (task internals in `09-video-pipeline.md`):

| Lever | Setting | Why |
|---|---|---|
| `retry` | default 3 attempts, exponential `factor` with jitter (`randomize: true`), `minTimeoutInMs`/`maxTimeoutInMs` | transient AssemblyAI/Shotstack failures self-heal |
| `maxDuration` | per task | kill runaway renders before they burn cost |
| `ttl` | on enqueue | expire stale queued runs (creator abandoned the batch) |
| Lifecycle hooks | `onStart`/`onSuccess`/`onFailure`/`catchError`/`middleware` | emit the structured log + Sentry breadcrumb at each boundary |
| **`idempotencyKey`** | on **every** trigger | **prevents duplicate renders/publishes on retry** — if a run with the same key is in progress it's ignored; if finished, the prior output is returned |
| Fan-out | router/coordinator + `batchTriggerAndWait` with concurrency limit | one record → N format clips in parallel = the "film once → post all week" hero loop |
| Progress stream | streamed to UI | honest progress on **Record/Produce**, never a spinner-of-mystery |

**Run states to alert on** ([Trigger.dev runs](https://trigger.dev/docs/runs)): `Crashed` (OOM — *not* retried), `Timed out`, `Expired`. These are the states that silently strand a creator's batch.

### 15.7.3 Pipeline States

| State | Surfaced to creator | Internal |
|---|---|---|
| **Loading** | Streamed per-stage progress ("Transcribing… Cutting clips…") | breadcrumb per `onStart` |
| **Partial** | "4 of 6 clips ready" — usable subset shown | failed format logged with `error_code`, retried |
| **Fail (terminal)** | Calm retry affordance, no stack trace | `Crashed`/`Expired` alert in `#marque-pipeline` |
| **Offline (device)** | Job already durable server-side; app reattaches on reconnect via `trace_id` | n/a |
| **Duplicate trigger** | No-op (idempotencyKey) — no double-charge, no double-post | logged `status: ok, attempt: n` |

---

## 15.8 Cost monitoring & alerts for AI / video spend

Marque's variable cost is dominated by AI (Anthropic), transcription (AssemblyAI), rendering (Shotstack), and delivery (Cloudflare Stream egress). Cost is observed at two layers: **the vendor's own billing API** and **our per-job ledger**.

### 15.8.1 Anthropic Usage & Cost (Admin API)

Requires an **Admin key** (Enterprise plan for the analytics endpoints) — kept to the cost-monitor cron, never in the app ([Usage & Cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api), [cookbook](https://platform.claude.com/cookbook/observability-usage-cost-api)):

| Endpoint | Gives | Notes |
|---|---|---|
| `GET /v1/organizations/usage_report/messages` | tokens split into **uncached_input / cache_creation / cache_read_input / output**, grouped by model/workspace/API key/service tier | buckets `1m` (≤1440), `1h` (≤168), `1d` (≤31) |
| `GET /v1/organizations/cost_report` | USD as decimal strings **in cents**, `1d` only, grouped by workspace/description | **Priority Tier costs never appear here** — track usage, not cost, for priority |
| **Spend Limits API** `/v1/organizations/spend_limits` | per-member spend caps + increase-request queue | scopes `read:spend_limits` / `write:spend_limits` ([Spend Limits API](https://platform.claude.com/docs/en/manage-claude/spend-limits-api)) |

**Cost levers we actively track as KPIs:**

- **Separate Anthropic workspaces per env *and* per function** (`scripts` = Opus 4.8 over the Brand Graph; `bulk` = Haiku 4.5 classification/voice-checks) so cost attribution is clean per `cost_report` grouping.
- **Prompt caching is a first-class cost lever.** Track `cache_read_input / (cache_read_input + uncached_input)` as a ratio KPI. The Opus 4.8 reasoning over the `Brand Graph` is engineered as a **stable cached prefix** (see `07-ai-system.md`) — a falling cache-hit ratio is a cost regression alarm, often signalling a prompt edit that broke the cache boundary.
- **Batch processing = 50% discount** for non-realtime bulk Haiku jobs (overnight voice-checks, teardown generation).

### 15.8.2 Per-job cost ledger (Postgres)

Authoritative for *our* unit economics. Written from the structured-log envelope (15.7.1), keyed by `creator_id` + `trace_id`.

```sql
create table cost_ledger (
  id            bigint generated always as identity primary key,
  trace_id      text not null,
  job_id        text not null,
  creator_id    uuid not null references profiles(id),
  stage         text not null,         -- anthropic | assemblyai | shotstack | stream_egress | r2
  vendor        text not null,
  units         numeric not null,      -- tokens | minutes | render_seconds | gb
  unit_cost_usd numeric not null,
  cost_usd      numeric not null,
  env           text not null,
  created_at    timestamptz not null default now()
);
create index on cost_ledger (creator_id, created_at);
create index on cost_ledger (trace_id);
```

Derived metrics (materialized views, refreshed hourly): **cost-per-clip** (rolling 7-day avg), **cost-per-active-creator**, **gross-margin-per-subscription** (ledger vs RevenueCat MRR from 15.6.4).

### 15.8.3 Alert thresholds & kill-switch

| Alarm | Threshold (initial) | Action |
|---|---|---|
| Per-job cost ceiling | `cost_usd > $1.50` single job | flag job, page on-call, inspect format/engine |
| Daily org spend | `> $X/day` (Open question) | Slack `#marque-cost` + Anthropic Spend Limit check |
| Cost-per-clip rolling avg | `> 1.5×` 30-day baseline | review ClipEngine vendor mix |
| Cache-hit ratio | `< 0.6` for scripts workspace | prompt-cache regression alarm |
| Vendor spend spike | per-vendor anomaly | **flip the PostHog `clipengine_vendor` kill-switch** to a cheaper/healthy vendor — no rebuild |

The vendor kill-switch is the cost-side payoff of the adapter pattern: a runaway Shotstack or ClipEngine vendor is rerouted via a remote flag, not an app release.

---

## 15.9 Disaster recovery, backups & data residency

Cost and crashes are recoverable; **lost data is not.** Marque stores the two most irreplaceable asset classes the product has: the **creator's face+voice source video** (the highest-PII asset in the system, biometric per `12-backend-data-security.md` §7) and the **Brand Graph** — the compounding context layer that is explicitly the moat (`06-brand-graph.md`). A render can always be re-run; a wiped Brand Graph or a corrupted `cost_ledger` cannot be reconstructed. This section states Marque's backup posture, recovery targets, the restore runbook, and where source video and the Brand Graph physically live. It is the DR counterpart to the RLS/secrets/threat-model in `12-backend-data-security.md` (which owns *prevention*) — this section owns *recovery*.

### 15.9.1 What we are protecting (asset criticality tiers)

DR effort is tiered by reconstructability, not by size. The largest assets (source video, renders) are the *least* critical because they are re-derivable or re-recordable; the smallest (Brand Graph, ledger) are the *most* critical because loss is permanent.

| Tier | Asset | Store | Reconstructable? | Target RPO | Target RTO |
|---|---|---|---|---|---|
| **T0 — irreplaceable** | `Brand Graph` (voice profile, niche, embeddings), profiles, entitlements mirror, `cost_ledger`, consent records | Supabase Postgres (`marque-prod`) | **No** | **≤ 5 min** (PITR) | **≤ 1 h** |
| **T0 — irreplaceable** | Creator **source video** (face+voice batch / repurpose uploads) | Cloudflare R2 (`recordings/`) | **No** — a re-record is a different take | **0** (versioned object store) | **≤ 4 h** to restorable access |
| **T1 — high value, derivable** | Rendered output clips | R2 + Cloudflare Stream | **Yes** — re-render from source via the pipeline | ≤ 24 h | best-effort; re-render on demand |
| **T1 — high value, derivable** | Transcripts, moment-detection, virality scores | Postgres + R2 | **Yes** — re-run AssemblyAI/ClipEngine | ≤ 5 min (in PITR) | with the DB restore |
| **T2 — operational** | Structured logs, MetricKit payloads, PostHog events | Sentry / PostHog / log sink | Vendor-retained | vendor SLA | vendor SLA |

> **RPO** (Recovery Point Objective) = maximum acceptable *data loss* window. **RTO** (Recovery Time Objective) = maximum acceptable *time to restore service*. T0 targets are deliberately aggressive because T0 loss is unrecoverable by any other means.

### 15.9.2 Supabase Postgres — PITR + backup cadence

Supabase's default daily backup is **insufficient** for a T0 RPO — a daily snapshot implies up to 24 h of data loss. Marque enables **Point-in-Time Recovery (PITR)** on `marque-prod` (a paid add-on; requires at least the relevant compute add-on), which ships WAL to object storage continuously and lets us restore to any moment, typically to a **~2-minute granularity**, within the configured retention horizon ([Supabase — Point-in-Time Recovery](https://supabase.com/docs/guides/platform/backups#point-in-time-recovery)).

| Layer | Setting | Notes |
|---|---|---|
| **PITR** | Enabled on `marque-prod`; **7-day** restore window at launch, raise to **14-day** as MRR justifies | RPO ≤ 5 min (WAL granularity). The *primary* T0 mechanism. |
| **Daily logical backup** | Supabase automated daily backup retained per plan | Coarse fallback; not the RPO driver |
| **Weekly off-platform `pg_dump`** | FastAPI cost-monitor cron also runs a weekly `pg_dump --no-owner` of `marque-prod` → an **independent** R2 bucket (`marque-dr-pgdump`, separate Cloudflare account/region from app data) | Guards against a **Supabase-account-level** failure (billing lapse, account compromise, vendor outage) that PITR alone cannot — PITR lives inside the same Supabase project it protects |
| **Staging/dev** | Daily backup only; **no** PITR | Non-T0; reconstructable |

The weekly off-platform dump is the deliberate break-glass against single-vendor risk: PITR restores *within* Supabase, but if the Supabase project itself is lost, the `pg_dump` in an independent account is the only path back. The dump is encrypted at rest (R2 SSE) and access is `service_role`-cron-only.

### 15.9.3 Cloudflare R2 + Stream — durability & source-video backup posture

R2 is designed for **eleven nines (99.999999999%) of annual object durability** ([Cloudflare R2 durability](https://developers.cloudflare.com/r2/reference/durability/)), so silent bit-rot is not the threat model — *accidental or malicious deletion* is. Durability is not backup; a buggy deletion job (the same job that legitimately wipes media on account deletion, `12-backend-data-security.md` §7) or a leaked credential can delete a live object on an eleven-nines store just as easily.

| Control | Setting | Why |
|---|---|---|
| **Object versioning** | **Enabled** on the `recordings/` (source video) bucket | A delete writes a delete-marker; the prior version is recoverable → effective **RPO 0** for source video against accidental/buggy deletes |
| **Lifecycle on noncurrent versions** | Retain noncurrent source-video versions **30 days**, then expire | Bounds storage cost while giving a 30-day undo window |
| **Deletion-cascade interaction** | The account-deletion job (GDPR erasure, `14-appstore-compliance-legal.md` §14.6) issues a **permanent, version-purging delete** — erasure must defeat versioning | Right-to-erasure beats the undo window *by design*; versioning protects against *unintended* loss, not lawful deletion |
| **Stream (delivery)** | Renders delivered via Stream are **T1 (re-derivable)** | No separate backup; a lost Stream asset is re-rendered from the versioned R2 source |
| **Cross-account dump (T0 only)** | Source video is **already the master copy**; we do *not* dual-write a second copy at launch (cost) | Revisit if R2-account-level risk warrants — tracked in Open questions |

> Renders are intentionally **not** backed up beyond Stream's own redundancy: they are a pure function of `source video × Format Library recipe`, both of which *are* protected, so the pipeline is the backup. This keeps DR spend on the irreplaceable T0 tier.

### 15.9.4 Data residency

Residency is a launch-gating question the moment an **EU creator** onboards, because source video is biometric personal data under GDPR (coordinate with the GDPR posture in `14-appstore-compliance-legal.md` §14.11 and the `storage_backend` residency note in `12-backend-data-security.md`).

| Data class | Physical location | Residency control |
|---|---|---|
| **Brand Graph + all Postgres T0** | Supabase project **region pinned at project creation** — `marque-prod` region is a deliberate decision, not a default (see Open questions) | Supabase region is fixed for a project's life; an EU-resident requirement means an **EU region** (e.g. `eu-central-1`) or an EU-region second project |
| **Source video + renders (R2)** | R2 is region-agnostic by default; pin a **jurisdiction** via R2 **Location Hints / Jurisdictional restrictions** (`eu` jurisdiction keeps objects in EU data centers) | Set the `eu` jurisdiction on EU-creator buckets so biometric video never leaves the EU ([R2 data location](https://developers.cloudflare.com/r2/reference/data-location/)) |
| **Off-platform `pg_dump`** | The DR bucket inherits the **same jurisdiction** as the primary | An EU dump must not land in a US bucket — residency applies to backups too |
| **Vendor processing (Anthropic, AssemblyAI, Shotstack, ClipEngine)** | Transient processing, governed by DPAs (`14-appstore-compliance-legal.md` Open Q.6) | No durable residency obligation if processors don't retain — but the DPA must confirm no-retention |
| **Analytics (PostHog)** | EU vs US cloud — `POSTHOG_HOST` (`eu.i.posthog.com` vs `us.i.posthog.com`) | Resolved in Open questions; pseudonymous, not the biometric concern, but still EU-creator-relevant |

The simplest defensible launch posture: pin `marque-prod` and the primary R2 jurisdiction to a **single stated region**, document it in the Privacy Policy processor/residency section (`14-appstore-compliance-legal.md`), and only stand up a second EU-region stack if/when EU creator volume or a customer contract demands true in-region storage. Until that decision is made (Open questions), **do not** onboard EU creators with a claim of EU residency we can't honor.

### 15.9.5 Restore runbook (rehearsed, not theoretical)

A backup that has never been restored is a hypothesis. Marque rehearses each restore path on **`marque-staging` quarterly** and after any major Postgres version bump; the rehearsal is a release-checklist-adjacent operational gate (15.12).

**Scenario A — accidental destructive write / bad migration reaches prod (most likely).**

```
1. DECLARE incident in #marque-incidents; freeze writes (flip a global PostHog
   `maintenance_mode` flag → app shows a calm "We'll be right back" screen,
   never an error). On-call owns the timeline.
2. IDENTIFY the bad timestamp T0 (from the structured log / migration record).
3. Supabase Dashboard → Database → Backups → Restore to a point BEFORE T0
   (PITR ~2-min granularity). Supabase restores into the SAME project.
4. RECONCILE side effects: replay any T0-window publishes/charges from the
   idempotent Trigger.dev run log; idempotencyKey (15.7.2) prevents double-posts.
5. RUN get_advisors + smoke suite (15.11) against the restored DB before lifting
   maintenance_mode.
6. POST-MORTEM: the migration that caused it is forward-only (15.3.1); ship a
   forward-fix, never a destructive rollback.
```
**Target: RTO ≤ 1 h, RPO ≤ 5 min.**

**Scenario B — Supabase project/account loss (catastrophic, low-probability).**

```
1. DECLARE incident; maintenance_mode on.
2. STAND UP a fresh Supabase project (or restore into the EU sibling).
3. RESTORE schema + data from the latest weekly off-platform pg_dump
   (marque-dr-pgdump). Accept up to 1 week RPO on T0 Postgres for THIS path —
   the off-platform dump is the floor, PITR is unavailable if the project is gone.
4. RE-POINT FastAPI/Trigger.dev SUPABASE_URL + service_role to the new project;
   rotate the leaked/compromised keys if loss was a breach.
5. R2 source video is unaffected (separate vendor) — re-link object keys.
6. RE-DERIVE T1 (renders/transcripts) lazily via the pipeline as creators return.
```
**Target: RTO ≤ 4 h, RPO ≤ 1 week (this path only).** The asymmetry is intentional: account-loss is rare enough that a weekly off-platform floor is an acceptable trade against the cost of continuous cross-vendor replication.

**Scenario C — accidental source-video deletion (single creator or bulk).**

```
1. RESTORE from R2 noncurrent object version (within the 30-day window) —
   remove the delete-marker / copy the prior version forward. RPO 0.
2. If beyond 30 days: the take is gone; notify the creator. (This is why the
   window exists — extend it before reducing it.)
```

### 15.9.6 DR States & alerts

| Condition | Behavior / alert |
|---|---|
| **PITR window healthy** | Daily automated check asserts `marque-prod` PITR is enabled + within retention; failure → page on-call |
| **Weekly off-platform dump fails** | `#marque-ci` alert; dump is a hard release-adjacent gate — a stale DR floor is treated as a P1 |
| **Restore rehearsal overdue (>1 quarter)** | Release-checklist item (15.12) flips red |
| **R2 versioning disabled on `recordings/`** | `get_advisors`-style config check fails CI/IaC drift detection; T0 source video unprotected = block |
| **Maintenance mode active** | App shows calm full-screen "We'll be right back" (anti-clutter doctrine — never a raw error); Today/Record entry points gated, draft recovery preserved on reconnect |
| **Residency misconfig (EU creator on US region)** | Onboarding gate blocks EU-residency claim until region is correct (coordinates with `14-appstore-compliance-legal.md`) |

---

## 15.10 Performance budgets

Performance is measured in the field with **MetricKit**, aggregated server-side by app version + device tier — not by hand-timing in Xcode.

### 15.10.1 Instrumentation

- **MetricKit** (`MXMetricManager`) delivers a daily `MXMetricPayload`: `MXAppLaunchMetric` / `TimeToFirstDrawMetric`, hang metrics, scroll-hitch metrics; plus `MXAppLaunchDiagnostic` for outliers ([Apple launch-time docs](https://developer.apple.com/documentation/xcode/reducing-your-app-s-launch-time), [Meet new MetricKit, WWDC26](https://developer.apple.com/videos/play/wwdc2026/222/), [Uber scale](https://www.uber.com/us/en/blog/measuring-performance-for-ios-apps-at-uber-scale/)).
- We ship each `MXMetricPayload` to the backend, bucketed by `app_version` + device tier, so we can see "low-end devices regressed launch on 1.4.0."
- **iOS 27 MetricKit additions** we adopt: per-app-state metrics via the **StateReporting framework** — we tag app state `today` / `record` / `trends` / `produce` so we know *which screen* hitches; plus memory-exception diagnostics and the termination-category field.
- Custom **signposts** (`os_signpost`) for the two product-critical spans MetricKit doesn't cover: record→upload-start and clip-render visible-progress latency.

### 15.10.2 Budgets (p50 / p90 — the percentiles Xcode Organizer uses)

| Metric | Budget | Definition / source |
|---|---|---|
| Cold launch → first frame | **p90 < 400 ms, p50 < 250 ms** | icon tap → first frame; a splash does **not** stop the counter |
| Scroll hitch rate | **< 5 ms/s** per screen | Apple flags this critical; **Today** and **Record** must be buttery |
| Hang rate | **< 1 s/hr** | WWDC example showed 6 s/hr as bad |
| Record → upload start | **< 2 s** after stop | custom signpost |
| Clip render: first visible progress | **< 5 s** | Trigger.dev streamed progress event |

### 15.10.3 Practices

- Move work **out of the launch path**: defer static initializers (inspect with the `dyld Activity` instrument); keep the first view hierarchy minimal (only the main thread can build SwiftUI views).
- Enable **Thread Performance Checker** + on-device hang detection in dev builds.
- Wire **App Store Connect Power & Performance API** regression notifications (Xcode Organizer → Regressions) to alert on hang/hitch spikes per release.
- Budgets are **remote-tunable** via the `perf_budgets` PostHog payload (15.4) so we can tighten thresholds without a release.

### 15.10.4 Performance States

| State | Behavior |
|---|---|
| **Regression detected (Organizer)** | Slack `#marque-perf` with the metric + device tier + offending release |
| **Budget breach on low-end tier** | Release checklist item flips red (15.12) |
| **Hitch on a tagged state** | StateReporting pinpoints `today` vs `record` vs `produce` |

---

## 15.11 Test strategy

A layered pyramid. The two **non-negotiable, differentiating** layers are **snapshot tests** (the calm aesthetic must not regress) and **AI-output evals** (voice fidelity is the product).

| Layer | Tooling | Scope |
|---|---|---|
| **Unit (iOS)** | **Swift Testing** (`@Test` / `#expect`, iOS 17+) + XCTest where needed | Brand Graph reducers, scheduler, format-recipe selection logic |
| **Unit (backend)** | pytest | Adapter contracts (`ClipEngine`/`Publisher`/`Insights`/`Subscriptions`), cost-ledger math |
| **Snapshot** | `swift-snapshot-testing` (pointfreeco) | Design-system regression — see 15.11.1 |
| **Integration** | Supabase branch DB + vendor sandboxes | End-to-end script → render → publish on staging keys |
| **UI / E2E** | **Maestro** YAML flows | Onboarding, record-once → multi-clip, paywall, publish |
| **AI-output evals** | **promptfoo** + golden datasets + LLM-as-judge | Voice-match, hooks, teardowns — see 15.11.2 |
| **Load** | k6 / Locust | Fan-out render concurrency, queue depth |

### 15.11.1 Snapshot tests — the calm aesthetic is a tested invariant

The locked aesthetic is a regression surface like any other. Snapshot tests render each design-system component and screen and diff against a committed reference image, in **both light and near-black dark mode**:

- Surfaces: warm cream **`#F4F1EA`** (light) / near-black **`#0E0E10`** (dark) — assert **never pure white/black**.
- Type: Playfair/Tiempos serif display titles; Inter/Söhne/Matter body.
- Accent: the single warm gold **`#C9A227`**, used sparingly — a snapshot catches an accidental second accent or an over-gold screen.
- Run across device sizes; a diff on the **Today** screen (one directive + gold streak glyph + one trend line) is treated as a P0 because **Today** is the product's whole posture.

### 15.11.2 AI-output evals — the differentiator, done rigorously

Because the stack **pins Opus 4.8 / Haiku 4.5**, a model bump (or a prompt edit) is a real regression risk; evals are the gate. Sources: [BitNet](https://www.bitnetinc.com/blog/evaluating-llm-outputs-testing-pipeline), [Monte Carlo](https://montecarlo.ai/blog-llm-as-judge/), [Evidently](https://www.evidentlyai.com/llm-guide/llm-as-a-judge), [arXiv 2506.13023](https://arxiv.org/html/2506.13023v1).

**Golden datasets** — 50–150 **human-verified** `(input → expected)` pairs per AI feature (voice-match scripts, hook generation, performance teardowns). Mix production samples + adversarial/edge cases + full category coverage. Expected outputs are **human-verified, never model-generated**. Treat each as a living dataset that grows from real misses.

**LLM-as-judge** — a capable model scores each output on multiple dimensions and is **constrained to structured JSON**:

```json
{
  "voice_match": 0.0,      // fidelity to the creator's Brand Graph voice
  "hook_strength": 0.0,    // virality-engine signal
  "on_brand": 0.0,
  "clarity": 0.0,
  "safety": "pass",        // pass | fail
  "verdict": "pass"        // composite gate
}
```

- Aggregate dimensions into a **composite pass/fail**.
- Handle judge flakiness: a **soft failure auto-re-runs**; investigate only on a repeat fail.
- **Validate the judge itself** against a hand-labeled set (track precision/recall) — prioritize catching **off-voice** outputs, since "sounds like someone else" is Marque's worst failure.

**CI gate** — `promptfoo eval` runs on **any prompt or model change** and **blocks merge if pass-rate < 90%** (threshold tunable). Store every eval result with `timestamp`, `model_version`, `prompt_version`, and **chart pass-rate over time**, correlating drops with deploys. A **small production sample runs continuously with a human in the loop**.

### 15.11.3 Integration, E2E, load

- **Integration** runs against a Supabase **branch DB** + vendor **sandbox** keys (Ayrshare sandbox, AssemblyAI/Shotstack test modes). Covers the full `script → render → publish` chain.
- **Maestro** E2E flows (the team already uses Maestro). Known operational gotcha: the local Maestro driver can wedge — recovery is `pkill -9 java`. Core flows: `onboarding.yaml`, `record_to_multiclip.yaml`, `paywall.yaml`, `publish_ig_tiktok.yaml`.
- **Load** (k6/Locust) hammers FastAPI + Trigger.dev fan-out: render concurrency, queue depth under a "Monday morning, 200 creators batch at once" scenario, idempotency under retry storms.

### 15.11.4 Test States / matrix

| Layer | Runs on | Blocks |
|---|---|---|
| Unit + snapshot + lint | every PR | merge |
| AI evals (promptfoo) | any prompt/model change | merge if pass-rate < 90% |
| Maestro smoke | push to `develop` | TestFlight Internal promotion |
| Integration | nightly + pre-tag | production tag |
| Load | pre-tag + capacity changes | production tag (advisory) |

---

## 15.12 QA process & release checklist

### 15.12.1 QA gates (promotion ladder)

```
PR ──► unit + snapshot + lint + (AI evals if AI touched)  ── all green ──► merge to develop
develop ──► TestFlight Internal ──► Maestro suite + eval suite + manual smoke ──► OK
tag v* ──► TestFlight External (Apple Beta App Review) ──► soak ≥ 24h ──► App Store submit
```

No path skips a gate. A PR cannot reach TestFlight; a build cannot reach External without an Internal soak; prod cannot ship without staging soak + green budgets.

### 15.12.2 Release checklist (literal — paste into the release issue)

- [ ] All required `.xcconfig` vars present — CI fails closed (`exit 64`) on any missing var
- [ ] dSYMs uploaded to Sentry; symbolication **verified on a real crash** (TestFlight build, not Xcode-run)
- [ ] Migrations applied to staging, **`get_advisors` clean**, soaked **≥ 24 h**
- [ ] RevenueCat **production** API key swapped in before review; purchase **flow** verified in sandbox — test the **flow, not metadata** (sandbox prices/names are unreliable; **TestFlight subs renew once / 24 h** as of Dec 2024, sandbox up to 12×/day) ([RevenueCat Apple sandbox](https://www.revenuecat.com/docs/test-and-launch/sandbox/apple-app-store), [RevenueCat sandbox](https://www.revenuecat.com/docs/test-and-launch/sandbox))
- [ ] **Apple IAP only** on the iOS paywall — Stripe reserved for future web, **never** iOS; App Store **Guideline 3.1.1** compliant (see `11-monetization.md`)
- [ ] **Instagram Graph** + **TikTok Content Posting** publish verified end-to-end against prod tokens (see `10-social-publishing.md`)
- [ ] Performance budgets **green** (launch p90, hang rate, hitch rate) on the **low-end device tier**
- [ ] AI eval **pass-rate ≥ 90%** on the current `opus-4-8` / `haiku-4-5` pins
- [ ] Feature flags at **safe defaults**; new-format flags **off / ramped**
- [ ] **Cost ledger + spend alerts armed**; cache-hit-ratio KPI nominal
- [ ] **DR posture verified (15.9):** `marque-prod` PITR enabled + within retention; last weekly off-platform `pg_dump` succeeded; R2 `recordings/` versioning on; restore rehearsal not overdue (≤1 quarter); `marque-prod` + R2 jurisdiction match the stated residency (no EU-residency claim we can't honor)
- [ ] PostHog events firing with correct `app_version`; **no new unbounded property definitions** (`posthog.json` lint clean)
- [ ] Crash-free sessions baseline acceptable in Sentry for the build (≥ 99.5%)
- [ ] Release notes + Slack `#marque-release` post with the `v*` tag

### 15.12.3 Rollback playbook

| Failure post-release | Rollback |
|---|---|
| Crash spike (Sentry) | Flag-off the offending feature (instant); expedited point release if core |
| Bad render/publish behavior | Flip `clipengine_vendor` / format flag; no app release needed |
| Migration regression | Forward-fix migration (forward-only); never destructive rollback against prod |
| Cost runaway | Vendor kill-switch + Anthropic Spend Limit; investigate before re-enabling |
| Pricing/paywall issue | Revert `paywall_price_experiment` flag to control |
| Data loss / bad migration on prod | Run the DR restore runbook (15.9.5): `maintenance_mode` flag → PITR restore (Scenario A) → reconcile via idempotent run log; off-platform `pg_dump` only if the project itself is lost (Scenario B) |

The recurring theme: **most rollbacks are a flag flip, not a release.** That is the entire point of the adapter + flag architecture — operational recovery happens in seconds without shipping a binary through Apple review.

---

## Open questions

1. **FastAPI host.** Fly.io vs Render vs Railway for the orchestration service. All support rolling/blue-green deploys + a secret manager; decision hinges on region needs, GPU adjacency for any in-house ClipEngine work, and on-call familiarity. *Owner: Platform.*
2. **Anthropic plan tier.** The Usage/Cost **analytics** endpoints (and Spend Limits) require Enterprise. Do we start on Enterprise for clean cost attribution, or defer and approximate cost from the per-job ledger until volume justifies it? *Owner: Founders / Finance.*
3. **Daily org-spend alert threshold (`$X/day`).** Needs a real unit-economics model (target cost-per-active-creator) before we can set the number. *Owner: Finance + Platform.*
4. **Device-tier definition for perf budgets.** Which concrete device is "low-end tier" for the budget gate (e.g., iPhone SE 3 vs the oldest iOS-17-capable device)? *Owner: iOS.*
5. **AI eval pass-rate threshold per feature.** 90% is a sensible default for scripts; hook generation and teardowns may warrant different bars. Needs calibration against the first golden datasets. *Owner: AI (see `07-ai-system.md`).*
6. **Session Replay scope.** `sessionReplay.onErrorSampleRate` is privacy-sensitive for creator content — confirm it's acceptable to capture replay around errors, or disable entirely. *Owner: Privacy / Legal.*
7. **PostHog EU vs US data residency.** Affects `POSTHOG_HOST` (`eu.i.posthog.com` vs `us.i.posthog.com`) and any GDPR posture if we onboard EU creators (15.9.4). Pseudonymous, not the biometric concern, but still EU-creator-relevant. *Owner: Privacy / Legal.*
8. **Primary region + EU-residency strategy (15.9.4).** Which region does `marque-prod` (Supabase) and the primary R2 jurisdiction pin to at creation — region is fixed for a Supabase project's life. And do we (a) launch single-region and gate EU-residency claims, or (b) stand up an EU-region second stack before onboarding EU creators? Coordinates with the GDPR posture in `14-appstore-compliance-legal.md` §14.11 and the `storage_backend` residency note in `12-backend-data-security.md`. *Owner: Platform + Privacy / Legal.*
9. **PITR retention window + cross-account replication depth (15.9.2–15.9.3).** Start at 7-day PITR and a weekly off-platform `pg_dump`, raising to 14-day as MRR justifies — confirm the launch numbers. And do we ever dual-write a second copy of T0 source video to an independent R2 account/region, or is single-master + 30-day versioning acceptable indefinitely? Drives DR spend. *Owner: Platform.*
10. **Load-test SLOs.** What's the target sustained render concurrency and acceptable queue-depth latency for the "Monday batch surge"? Needs a target creator-count assumption. *Owner: Platform.*

---

## Sources

1. [Claude Lab — Xcode Cloud CI scripts / TestFlight indie workflow](https://claudelab.net/en/articles/claude-code/claude-code-xcode-cloud-ci-scripts-testflight-indie-dev-workflow) — three-workflow topology, "Continue on script failure" trap, empty-`.xcconfig` failure mode, dSYM/PR-branch pitfalls, Xcode Cloud vs Fastlane `match` signing.
2. [Sentry — iOS SDK setup](https://docs.sentry.io/platforms/apple/guides/ios/) — SPM install, main-thread early init, `tracesSampleRate`, profiling, logs, session replay.
3. [Sentry — Apple dSYM / symbolication](https://docs.sentry.io/platforms/apple/dsym/) — `DWARF with dSYM File`, `sentry-cli debug-files upload`, `--include-sources`.
4. [PostHog — product analytics best practices](https://posthog.com/docs/product-analytics/best-practices) — `category:object_action` naming, static-name rule, property conventions, rate-limit warning.
5. [Scopetrics — PostHog tracking-plan guide](https://scopetrics.com/resources/posthog-tracking-plan-guide) — verb list, AARRR tracking-plan structure, property naming.
6. [PostHog — RevenueCat data-warehouse source](https://posthog.com/docs/data-warehouse/sources/revenuecat) — v2 `sk_` key, Integrations write permission, webhook events as revenue source of truth.
7. [Anthropic — Usage & Cost Admin API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api) — token breakdowns (cached/uncached), cost in cents, bucket limits, cache metrics.
8. [Anthropic — Spend Limits API](https://platform.claude.com/docs/en/manage-claude/spend-limits-api) — per-member caps, increase-request workflow, scopes.
9. [Anthropic — Usage/Cost observability cookbook](https://platform.claude.com/cookbook/observability-usage-cost-api) — practical cost-monitoring patterns.
10. [Trigger.dev — tasks overview](https://trigger.dev/docs/tasks/overview) — retry/`maxDuration`/`ttl`/lifecycle-hook config for durable jobs.
11. [Trigger.dev — runs](https://trigger.dev/docs/runs) — run/attempt states (Crashed/Timed out/Expired), idempotency keys, staging/production environments.
12. [Trigger.dev — media processing use case](https://trigger.dev/docs/guides/use-cases/media-processing) — fan-out, streamed progress for long renders.
13. [Apple — Reducing your app's launch time](https://developer.apple.com/documentation/xcode/reducing-your-app-s-launch-time) — cold-launch definition, `TimeToFirstDrawMetric`, p50/p90, dyld static-initializer guidance.
14. [Apple — Meet the new MetricKit (WWDC26)](https://developer.apple.com/videos/play/wwdc2026/222/) — hang/hitch math, per-app-state metrics via StateReporting, new crash diagnostics.
15. [Uber Engineering — measuring iOS performance at scale](https://www.uber.com/us/en/blog/measuring-performance-for-ios-apps-at-uber-scale/) — shipping MetricKit payloads to backend, version/device-tier aggregation.
16. [BitNet — evaluating LLM outputs / testing pipeline](https://www.bitnetinc.com/blog/evaluating-llm-outputs-testing-pipeline) — golden-dataset + promptfoo + CI pass-rate gate.
17. [Monte Carlo — LLM-as-judge](https://montecarlo.ai/blog-llm-as-judge/) — structured-output judges, composite scoring, soft-failure re-run.
18. [Evidently — LLM-as-a-judge guide](https://www.evidentlyai.com/llm-guide/llm-as-a-judge) — judge design + validating the judge.
19. [arXiv 2506.13023 — LLM evaluation methods](https://arxiv.org/html/2506.13023v1) — eval methodology grounding.
20. [RevenueCat — Apple App Store sandbox](https://www.revenuecat.com/docs/test-and-launch/sandbox/apple-app-store) — sandbox/TestFlight subscription renewal realities (24h as of Dec 2024), test-the-flow-not-metadata.
21. [RevenueCat — sandbox testing](https://www.revenuecat.com/docs/test-and-launch/sandbox) — general sandbox guidance.
22. [techconcepts — GitHub Actions iOS / CI compute](https://techconcepts.org/blog/github-actions-ios) — Xcode Cloud free-tier hours, parallel workers, runner Xcode pinning.
23. [soarias — CI with Fastlane for SwiftUI](https://soarias.com/swiftui/how-to-implement-ci-with-fastlane/) — `match(readonly:)`, build-number auto-bump, Xcode pin.
24. [Supabase — Backups & Point-in-Time Recovery](https://supabase.com/docs/guides/platform/backups#point-in-time-recovery) — daily backups vs PITR, WAL-based ~2-minute restore granularity, retention windows (15.9.2).
25. [Cloudflare R2 — Durability](https://developers.cloudflare.com/r2/reference/durability/) — eleven-nines (99.999999999%) annual object durability; durability ≠ backup (15.9.3).
26. [Cloudflare R2 — Data location & jurisdictional restrictions](https://developers.cloudflare.com/r2/reference/data-location/) — Location Hints + `eu` jurisdiction to pin object storage region for GDPR (15.9.4).
