# 01 — Information Architecture & Navigation

> **Product:** Marque — a calm, premium iOS app that turns overwhelmed creators into consistent ones.
> **Section owner:** Navigation / app-shell.
> **Locked stack referenced here:** Swift + SwiftUI (iOS 17+), Observation framework, Swift Concurrency, AVFoundation, Supabase (Auth / Postgres / Realtime / Storage), RevenueCat (StoreKit 2), Ayrshare (Publisher adapter), the MCP **ClipEngine** adapter, APNs.
> **Sibling docs cross-referenced:** `02-design-system.md` (tokens, type, motion), `03-onboarding.md` (Brand Graph setup), `04-screens-create.md` (RecordSession / teleprompter), `09-video-pipeline.md` (ClipEngine + render recipes), `10-social-publishing.md` (Ayrshare Publisher + Insights), `07-ai-system.md` (Claude scripts / teardowns / Virality Engine), `11-monetization.md` (RevenueCat placements), `13-notifications-retention.md` (APNs payloads), `12-backend-data-security.md` (canonical Postgres schema).

This document is the single source of truth for **how a user moves through Marque**: the tab structure, the complete screen map, the SwiftUI navigation architecture (`NavigationStack` + a typed Router per tab), deep links / universal links, the **core-loop state machine**, and the IA placement of every Section-8 feature under the anti-clutter doctrine.

---

## 1. Governing principles (the IA contract)

Every navigation decision in Marque is bound by five non-negotiable rules. When a future feature request conflicts with one of these, the rule wins.

| # | Principle | Concrete consequence for IA |
|---|-----------|-----------------------------|
| **P1** | **One idea per screen.** | The Today home shows exactly ONE directive + one gold streak glyph + one trend line. Nothing else may be added to Today. |
| **P2** | **Five tabs is the ceiling.** | Apple's HIG caps a tab bar at 3–5 tabs and warns that each tab adds complexity; exceeding the visible count silently spawns a "More" overflow tab — a UX dead-end we forbid. Marque ships exactly 5 named tabs at the legal maximum, so **every** Section-8 feature MUST nest, never become a tab. ([Apple HIG — Tab bars](https://developer.apple.com/design/human-interface-guidelines/tab-bars)) |
| **P3** | **Progressive disclosure.** | Depth is reached one layer at a time (Hook Lab lives *inside* the Script Reader; Insights lives *inside* Coach). Critical info first, expandable detail behind a tap. ([LogRocket — Progressive disclosure](https://blog.logrocket.com/ux-design/progressive-disclosure-ux-types-use-cases/)) |
| **P4** | **Color guides, never decorates.** | The single warm gold (`#C9A227`) appears on the tab bar only as one contextual "needs you" badge — never as a colorized second tab. ([Building calm interfaces, 2026](https://medium.com/@mindcodersindore/building-calm-interfaces-less-is-more-in-2026-eab5fd810413)) |
| **P5** | **State drives navigation, not imperative pushes.** | Long-running AI work (the `processing` phase) surfaces via observed state, so the user can leave and the result appears when ready — no spinner-jail. ([State-Driven Navigation for AI Workflows](https://dev.to/programmingcentral/beyond-the-back-button-mastering-state-driven-navigation-for-ai-workflows-in-swiftui-2ab4)) |

---

## 2. Bottom-tab structure

Marque has **five tabs**, in this fixed left-to-right order. The order encodes the creator's mental flow: see today's one thing → make it → review the assets → schedule them → learn.

| Order | Tab | Canonical `enum AppTab` case | SF Symbol (filled when selected) | Job-to-be-done | Badge source (derived) |
|-------|-----|------------------------------|----------------------------------|----------------|------------------------|
| 1 | **Today** | `.today` | `sun.max` | The single directive for right now + streak glyph + one trend line. | none (Today is never a notification target) |
| 2 | **Studio** | `.studio` | `wand.and.stars` | The **HERO** batch loop: brand → scripts → formats → record → process. | string badge `"●"` (gold) when a batch needs the user (scripts to approve / record ready) |
| 3 | **Library** | `.library` | `square.grid.2x2` | Finished clips: preview, format, virality score, caption edits. | count = clips finished since last visit |
| 4 | **Calendar** | `.calendar` | `calendar` | Scheduled posts across Instagram & TikTok; approve / reschedule. | count = posts needing approval |
| 5 | **Coach** | `.coach` | `chart.line.uptrend.xyaxis` | Performance teardown cards + a push; Insights archive nested within. | count = unread teardowns |

### 2.1 Why this exact set (and what is deliberately NOT a tab)

- **Record is not a tab.** Recording is a *step inside* the Studio batch loop (`RecordSession`), not a destination. Making it a tab would fragment the hero loop and violate P2.
- **Insights is not a tab.** Teardowns live in **Coach**; the archive is a sub-screen (`InsightsArchive`) one layer deep. Naming overlap is resolved: Coach = the live feed + push surface, Insights = the archive within it. (See Open Questions for final label sign-off.)
- **Trends is not a tab.** Trend Radar is one line on Today → a dedicated screen one layer deep.
- **Profile / Settings is not a tab.** Reached from a top-trailing avatar/gear affordance on Today and Coach, presented as its own stack (modal-rooted), keeping the tab bar reserved for the creative loop.

### 2.2 Implementation: value-based selection across the iOS 17 floor

Marque's deployment target is **iOS 17** (the locked product floor — see the doc header and `04-screens-create.md` §0.1). That floor governs the tab API we can ship, because the modern value-based `Tab` builder is **not** available on iOS 17:

- The value-based `Tab(_:systemImage:value:)` initializer and the `Tab`-level `.badge(...)` modifier were introduced in **iOS 18** and are not back-deployable to iOS 17. The compiler will reject them when the deployment target is iOS 17. ([Apple — `Tab`](https://developer.apple.com/documentation/swiftui/tab), [SwiftLee — TabView](https://www.avanderlee.com/swiftui/tabview-tabbed-views/), [SerialCoder — new Tab API](https://serialcoder.dev/text-tutorials/swiftui/exploring-the-new-tabview-and-tab-api-in-swiftui/))
- On iOS 17 the supported path is the trailing-closure `TabView` with each tab declared via the `.tabItem { … }` modifier, and **programmatic selection / deep-link routing** achieved by attaching a `.tag(AppTab.…)` matching the type bound to `TabView(selection:)`. `.tabItem` was only **deprecated in iOS 18.1** — it remains the correct, fully supported API on iOS 17. ([Apple — `tabItem(_:)`](https://developer.apple.com/documentation/swiftui/view/tabitem(_:)), [SerialCoder — new Tab API](https://serialcoder.dev/text-tutorials/swiftui/exploring-the-new-tabview-and-tab-api-in-swiftui/))

We therefore **gate the value-based `Tab` builder behind `#available(iOS 18, *)`** and ship a `.tabItem`/`.tag` fallback for iOS 17. Both branches bind to the **same** `TabView(selection: $appRouter.selectedTab)`, so deep-link dispatch (§7) is identical on either OS. The badge story splits the same way: `Tab.badge(...)` on iOS 18, the view-level `.badge(...)` modifier (available since iOS 15) on the `.tabItem` content for iOS 17.

```swift
enum AppTab: String, Hashable, CaseIterable, Identifiable {
    case today, studio, library, calendar, coach
    var id: String { rawValue }
}

struct RootTabView: View {
    @Environment(AppRouter.self) private var appRouter   // Observation, see §3

    var body: some View {
        @Bindable var appRouter = appRouter
        TabView(selection: $appRouter.selectedTab) {
            if #available(iOS 18, *) {
                modernTabs(appRouter)
            } else {
                legacyTabs(appRouter)
            }
        }
        .tint(.brandGold)               // token from 02-design-system.md
    }

    // MARK: iOS 18+ — value-based Tab builder
    @available(iOS 18, *)
    @TabContentBuilder<AppTab>
    private func modernTabs(_ appRouter: AppRouter) -> some TabContent<AppTab> {
        Tab("Today", systemImage: "sun.max", value: AppTab.today) {
            TodayStackHost()            // owns its own NavigationStack
        }
        Tab("Studio", systemImage: "wand.and.stars", value: AppTab.studio) {
            StudioStackHost()
        }
        .badge(appRouter.studioNeedsYou ? "●" : nil)   // derived gold dot (P4)

        Tab("Library", systemImage: "square.grid.2x2", value: AppTab.library) {
            LibraryStackHost()
        }
        .badge(appRouter.newClipCount)                  // derived count

        Tab("Calendar", systemImage: "calendar", value: AppTab.calendar) {
            CalendarStackHost()
        }
        .badge(appRouter.postsNeedingApproval)

        Tab("Coach", systemImage: "chart.line.uptrend.xyaxis", value: AppTab.coach) {
            CoachStackHost()
        }
        .badge(appRouter.unreadTeardowns)
    }

    // MARK: iOS 17 fallback — .tabItem + .tag for programmatic selection
    @ViewBuilder
    private func legacyTabs(_ appRouter: AppRouter) -> some View {
        TodayStackHost()                // owns its own NavigationStack
            .tabItem { Label("Today", systemImage: "sun.max") }
            .tag(AppTab.today)

        StudioStackHost()
            .tabItem { Label("Studio", systemImage: "wand.and.stars") }
            .badge(appRouter.studioNeedsYou ? "●" : nil)   // view-level .badge (iOS 15+)
            .tag(AppTab.studio)

        LibraryStackHost()
            .tabItem { Label("Library", systemImage: "square.grid.2x2") }
            .badge(appRouter.newClipCount)
            .tag(AppTab.library)

        CalendarStackHost()
            .tabItem { Label("Calendar", systemImage: "calendar") }
            .badge(appRouter.postsNeedingApproval)
            .tag(AppTab.calendar)

        CoachStackHost()
            .tabItem { Label("Coach", systemImage: "chart.line.uptrend.xyaxis") }
            .badge(appRouter.unreadTeardowns)
            .tag(AppTab.coach)
    }
}
```

**Rule — selection binding is OS-agnostic.** Both branches feed the **same** `TabView(selection: $appRouter.selectedTab)`. On iOS 18 the `value:` argument is the selection identity; on iOS 17 the matching `.tag(AppTab.…)` is. Either way `appRouter.handle(_:)` (§7) sets `selectedTab` and the correct tab activates — the deep-link layer never branches on OS.

**Rule — load-bearing:** `TabView` is the **parent**; each `*StackHost` owns its **own** `NavigationStack`. Never wrap a single `NavigationStack` around the `TabView`. Per-tab stacks preserve independent back-history and let a deep link reset one tab without nuking the others. ([SwiftLee — TabView](https://www.avanderlee.com/swiftui/tabview-tabbed-views/))

**Rule — derived badges only (both OS paths).** Every badge value — `Tab.badge(...)` on iOS 18, the view-level `.badge(...)` on iOS 17 — is a *computed property* on the router/data model, never an imperatively-written counter. Imperative badge writes drift out of sync with the data. The view-level `.badge(_:)` modifier used on the iOS 17 `.tabItem` content has been available since iOS 15, so it needs no further availability gate. ([SwiftLee — TabView](https://www.avanderlee.com/swiftui/tabview-tabbed-views/), [DevTechie — badges](https://www.devtechie.com/blog/swiftui-badges-for-toolbars-and-tab-bars-in-ios-26))

---

## 3. Navigation architecture: Routers + per-tab `NavigationStack`

Marque uses **one `@Observable` Router per tab**, plus a thin top-level **`AppRouter`** for tab selection and deep-link dispatch. This is the current production consensus pattern. ([2025 Routing Guide](https://medium.com/@dinaga119/mastering-navigation-in-swiftui-the-2025-guide-to-clean-scalable-routing-bbcb6dbce929), [Tiago Henriques — Router Pattern](https://www.tiagohenriques.dev/blog/swiftui-navigation-router-pattern))

### 3.1 The spine

```
AppRouter (@Observable)
├── selectedTab: AppTab               // bound to TabView(selection:)
├── todayRouter:    TodayRouter
├── studioRouter:   StudioRouter
├── libraryRouter:  LibraryRouter
├── calendarRouter: CalendarRouter
├── coachRouter:    CoachRouter
├── sheet: AppSheet?                  // global modal layer (paywall, settings, primers)
├── derived badge counts (computed)
└── handle(_ deepLink: DeepLink)      // selects tab, then pushes onto that tab's router
```

### 3.2 Type-safe route enums (one per tab)

Routes are **enums with associated values**. The compiler then demands the correct payload at every push — no stringly-typed navigation. ([2025 Routing Guide](https://medium.com/@dinaga119/mastering-navigation-in-swiftui-the-2025-guide-to-clean-scalable-routing-bbcb6dbce929), [Tiago Henriques](https://www.tiagohenriques.dev/blog/swiftui-navigation-router-pattern))

```swift
enum TodayRoute: Hashable {
    case trendDetail(trendID: UUID)
    case streakDetail
}

enum StudioRoute: Hashable {
    case batchDetail(batchID: UUID)
    case scriptReader(scriptID: UUID)
    case hookLab(scriptID: UUID)            // nested depth, see §6.3
    case formatPicker(batchID: UUID)
    case formatDetail(formatID: String)     // render-recipe id (see 09-video-pipeline.md)
    case recordSession(batchID: UUID)
    case uploadProgress(batchID: UUID)
    case processingStatus(batchID: UUID)
}

enum LibraryRoute: Hashable {
    case clipDetail(clipID: UUID)
}

enum CalendarRoute: Hashable {
    case dayView(date: Date)
    case scheduledPostDetail(postID: UUID)
    case publishStatus(postID: UUID)
}

enum CoachRoute: Hashable {
    case teardownDetail(teardownID: UUID)
    case insightsArchive
}
```

### 3.3 The Router base contract

Each Router holds a **typed `[Route]` array** (not a bare `NavigationPath`). A typed array is fully inspectable and serializable, which we need for state restoration (§3.6). ([Tiago Henriques](https://www.tiagohenriques.dev/blog/swiftui-navigation-router-pattern), [iCommunity — Router Pattern](https://medium.com/icommunity/the-swiftui-navigation-architecture-that-will-save-your-projects-the-router-pattern-a38349198702))

```swift
@MainActor @Observable
final class StudioRouter {
    var path: [StudioRoute] = []

    func push(_ route: StudioRoute) { path.append(route) }
    func pop() { _ = path.popLast() }
    func popToRoot() { path.removeAll() }
    func reset(to route: StudioRoute) { path = [route] }
}
```

### 3.4 Injection via Observation (NOT `ObservableObject`)

Routers are `@Observable` and injected with `.environment(_:)`, read with `@Environment(_.self)`. This is the locked-stack choice and avoids the over-invalidation of `ObservableObject`/`@EnvironmentObject`. ([SwiftUI Nav 2026](https://dev.to/__be2942592/swiftui-navigation-in-2026-the-complete-guide-navigationstack-deep-links-coordinators-hpk), [Advanced patterns, Dec 2025](https://medium.com/@chandra.welim/advanced-swiftui-navigation-patterns-production-ready-code-7886e7ae1937))

```swift
struct StudioStackHost: View {
    @Environment(StudioRouter.self) private var router

    var body: some View {
        @Bindable var router = router
        NavigationStack(path: $router.path) {
            StudioHome()
                // Register destinations ONCE at the stack root.
                .navigationDestination(for: StudioRoute.self) { route in
                    switch route {
                    case .batchDetail(let id):     BatchDetail(batchID: id)
                    case .scriptReader(let id):    ScriptReader(scriptID: id)
                    case .hookLab(let id):         HookLab(scriptID: id)
                    case .formatPicker(let id):    FormatPicker(batchID: id)
                    case .formatDetail(let fid):   FormatDetail(formatID: fid)
                    case .recordSession(let id):   RecordSession(batchID: id)
                    case .uploadProgress(let id):  UploadProgress(batchID: id)
                    case .processingStatus(let id):ProcessingStatus(batchID: id)
                    }
                }
        }
    }
}
```

**Rule:** register `.navigationDestination(for:)` **once per stack root**. Scattering the modifier down the hierarchy causes unpredictable shadowing. ([2025 Routing Guide](https://medium.com/@dinaga119/mastering-navigation-in-swiftui-the-2025-guide-to-clean-scalable-routing-bbcb6dbce929))

### 3.5 Navigation guards live in the Router

Entry conditions (entitlement gates, prerequisite states) are centralized in the Router, never duplicated in views. ([Advanced patterns](https://medium.com/@chandra.welim/advanced-swiftui-navigation-patterns-production-ready-code-7886e7ae1937))

```swift
extension StudioRouter {
    /// Block Record unless ≥1 script is approved; gate behind paywall if no entitlement.
    func enterRecord(batch: Batch, entitlements: EntitlementState) {
        guard batch.hasApprovedScript else {
            push(.scriptReader(scriptID: batch.primaryScriptID)); return
        }
        guard entitlements.canRecordBatch else {
            appSheet = .paywall(placement: "paywalled_feature"); return   // see 11-monetization.md
        }
        push(.recordSession(batchID: batch.id))
    }
}
```

Views call `router.push(.hookLab(id))` and never know what screen comes next — the destination switch is the only place that knows. Guards being in the Router means a deep link, a push notification, and a button tap all hit the same enforcement.

### 3.6 State restoration (per-tab path persistence)

Because each Router's `path` is a typed `[Route]` of `Codable` enums, we persist all five paths + `selectedTab` to `UserDefaults`/`SceneStorage` on background and rehydrate on cold launch, so a creator returns to exactly where they were. **Exception:** the `RecordSession` and `uploadProgress` routes are *not* restored deep (they resume to `BatchDetail`) — re-entering a live camera session from cold launch is brittle. (Final restoration depth is an Open Question.)

> **Off-the-shelf alternative considered:** [SwiftfulRouting](https://github.com/SwiftfulThinking/SwiftfulRouting) is a credible library, but Marque hand-rolls the Router to avoid a vendor dependency in the navigation spine.

---

## 4. Full screen map / sitemap

Legend: **`stack push`** = pushed onto a tab's NavigationStack · **`sheet`** = modal sheet (global `AppSheet`) · **`fullScreenCover`** = full-screen modal · **`primer`** = permission-priming sheet.

### 4.1 Root

```
RootTabView  (TabView parent — §2.2)
├── Today   stack
├── Studio  stack
├── Library stack
├── Calendar stack
└── Coach   stack
   + global modal layer (AppSheet / fullScreenCover) hosted above the TabView
```

### 4.2 Today stack

| Screen | Type | Reached from | Notes |
|--------|------|--------------|-------|
| `TodayHome` | stack root | tab | **ONE** directive + gold streak glyph + one trend line. Nothing else (P1). |
| `TrendDetail` | stack push (`.trendDetail`) | tap the trend line | Trend Radar full screen → §6.2 |
| `StreakDetail` | stack push (`.streakDetail`) | tap the gold glyph | (Profile also surfaces this; canonical view here) |
| Earned-referral prompt | `sheet` | win event (one-time) | §6.7 |

### 4.3 Studio stack (HERO loop)

| Screen | Type | Reached from | Notes |
|--------|------|--------------|-------|
| `StudioHome` | stack root | tab | List of batches; primary CTA "Record this week's batch". |
| `BatchDetail` | push (`.batchDetail`) | a batch row / Today directive deep link | Hub for one batch; shows `BatchState` (§5). |
| `ScriptReader` | push (`.scriptReader`) | BatchDetail | One script at a time; calm reader (P1). |
| `HookLab` | push (`.hookLab`) | inside ScriptReader (progressive disclosure) | §6.3 |
| `FormatPicker` | push (`.formatPicker`) | BatchDetail / after approve | Render-recipe library (split-screen, 3-up, green-screen, faceless, before/after, myth-buster, listicle, POV, reaction, B-roll+hook…). See `09-video-pipeline.md`. |
| `FormatDetail` | push (`.formatDetail`) | FormatPicker | Recipe preview + why-it-works. |
| `RecordSession` | push (`.recordSession`) | guard `enterRecord` | AVFoundation camera + teleprompter; **source toggle: fresh take OR repurpose-in upload** (§6.6). See `04-screens-create.md`. |
| `UploadProgress` | push (`.uploadProgress`) | after record/upload | Drives `uploading` state. |
| `ProcessingStatus` | push (`.processingStatus`) | after upload | State-driven; user may leave (P5). |

### 4.4 Library stack

| Screen | Type | Reached from | Notes |
|--------|------|--------------|-------|
| `ClipGrid` | stack root | tab | Grid of finished clips. |
| `ClipDetail` | push (`.clipDetail`) | a clip cell | Preview, format chip, virality score (`07-ai-system.md`). |
| `EditCaptions` | `sheet` | ClipDetail | Inline caption / hook edit. |
| `Reframe` | `sheet` | ClipDetail | Aspect re-crop via ClipEngine reframe (`09-video-pipeline.md`). |

### 4.5 Calendar stack

| Screen | Type | Reached from | Notes |
|--------|------|--------------|-------|
| `ScheduleCalendar` | stack root | tab | Month/week of scheduled posts (IG + TikTok). |
| `DayView` | push (`.dayView`) | a day cell | Posts for that date. |
| `ScheduledPostDetail` | push (`.scheduledPostDetail`) | a post | Approve / edit / reschedule. Publisher adapter = Ayrshare (`10-social-publishing.md`). |
| `PublishStatus` | push (`.publishStatus`) | after publish/approve | Live publish result per platform. |

### 4.6 Coach stack

| Screen | Type | Reached from | Notes |
|--------|------|--------------|-------|
| `CoachFeed` | stack root | tab | Teardown cards. |
| `TeardownDetail` | push (`.teardownDetail`) | a card / APNs push | Single teardown (`07-ai-system.md`). |
| `InsightsArchive` | push (`.insightsArchive`) | CoachFeed top affordance | The archive; NOT a tab (§2.1). |

### 4.7 Cross-cutting (modal / supporting — hosted above the TabView)

| Screen | Type | Trigger | Notes |
|--------|------|---------|-------|
| `OnboardingFlow` | `fullScreenCover` | first launch / unauthenticated | "What do you want to be known for?" See `03-onboarding.md`. |
| `BrandGraphSetup` | `fullScreenCover` (within onboarding) | onboarding / Settings | Connect existing page; builds the Brand Graph context layer. |
| `Paywall` | `sheet` (RevenueCat placement-driven) | `AppSheet.paywall(placement:)` | §6.8 / `11-monetization.md`. Never bolted onto Today. |
| `InboxView` | `sheet` (own NavigationStack) | **tray glyph** in the top-trailing affordance cluster on Today/Coach | Un-pushable notification signals land here (clips ready / post failed when push is denied, off, deferred, or Focus-suppressed). Reverse-chron list; rows reuse the same `DeepLink` routes as their pushes. **Not a tab; never on Today.** Full spec in `13-notifications-retention.md` §4.6. |
| `Settings` | `sheet` (own NavigationStack) | gear on Today/Coach | Connected accounts (Ayrshare), notifications, **referral row** (§6.7). |
| `Profile` | within Settings stack | Settings | Full streak view, account. |
| `ConnectedAccounts` | within Settings stack | Settings | IG Graph / TikTok via Publisher adapter; OAuth web flow. |
| Permission primers | `sheet` | before system prompt | Camera, mic, Photos, notifications — each a calm one-screen primer (§7). |
| `AccountConnectWeb` | `fullScreenCover` (ASWebAuthenticationSession) | ConnectedAccounts | Instagram Graph / TikTok auth via Ayrshare. |

---

## 5. The core-loop state machine

The batch loop — **brand analyzed → scripts written → batch recorded → clips edited → scheduled/published → learned-from** — is modeled as a **Swift enum state machine with associated values**, driven by **Supabase Realtime**, with all transitions centralized in a `reduce(state, event)` reducer. Illegal states are made unrepresentable. ([Swift by Sundell — Modelling state](https://www.swiftbysundell.com/articles/modelling-state-in-swift/), [Splinter — State machines with enums](https://www.splinter.com.au/2019/04/10/swift-state-machines-with-enums/))

### 5.1 The state enum

```swift
enum BatchState: Equatable {
    case draftScripts([Script])              // Claude wrote scripts; awaiting approval
    case scriptsApproved([Script])
    case formatsSelected(plan: RenderPlan)   // render recipes chosen
    case recording
    case uploading(progress: Double)
    case processing(jobID: UUID, progress: Double)   // ClipEngine via FastAPI/Trigger.dev
    case clipsReady([Clip])
    case scheduled([ScheduledPost])
    case published
    case failed(reason: BatchFailure)
}
```

### 5.2 Events and the reducer

Transition methods are **not** `async`. Async work (network, ClipEngine, Realtime) happens *outside*; its results are fed in as events. State mutation stays on the `MainActor`, and the reducer is the single place transitions occur. ([Robust state machine with Swift Concurrency — LY Corp](https://techblog.lycorp.co.jp/en/20250117a))

```swift
enum BatchEvent {
    case scriptsApproved([Script])
    case formatsChosen(RenderPlan)
    case recordingStarted
    case uploadProgressed(Double)
    case uploadFinished
    case jobProgressed(jobID: UUID, progress: Double)   // from Supabase Realtime
    case clipsArrived([Clip])
    case schedulingConfirmed([ScheduledPost])
    case publishConfirmed
    case failure(BatchFailure)
}

@MainActor @Observable
final class BatchStore {
    private(set) var state: BatchState

    func send(_ event: BatchEvent) { state = Self.reduce(state, event) }

    static func reduce(_ s: BatchState, _ e: BatchEvent) -> BatchState {
        switch (s, e) {
        case (.draftScripts, .scriptsApproved(let a)):        return .scriptsApproved(a)
        case (.scriptsApproved, .formatsChosen(let p)):       return .formatsSelected(plan: p)
        case (.formatsSelected, .recordingStarted):           return .recording
        case (.recording, .uploadProgressed(let p)):          return .uploading(progress: p)
        case (.uploading, .uploadFinished):                   return .processing(jobID: .init(), progress: 0)
        case (.processing(let id, _), .jobProgressed(let j, let p)) where id == j:
                                                              return .processing(jobID: id, progress: p)
        case (.processing, .clipsArrived(let c)):             return .clipsReady(c)
        case (.clipsReady, .schedulingConfirmed(let posts)):  return .scheduled(posts)
        case (.scheduled, .publishConfirmed):                 return .published
        case (_, .failure(let r)):                            return .failed(reason: r)
        default:                                              return s   // ignore illegal transitions
        }
    }
}
```

### 5.3 State → navigation mapping (state-driven, P5)

The `processing` phase **does not** push a blocking spinner. The user may navigate away; `BatchDetail` and the Studio tab badge reflect state when it changes.

| `BatchState` | Where it surfaces | What the UI shows | User can leave? |
|--------------|-------------------|-------------------|-----------------|
| `draftScripts` | `ScriptReader` | "Review your scripts" + gold Studio badge | yes |
| `scriptsApproved` | `BatchDetail` | CTA → `FormatPicker` | yes |
| `formatsSelected` | `BatchDetail` | CTA → Record | yes |
| `recording` | `RecordSession` | live camera + teleprompter | — (active) |
| `uploading(p)` | `UploadProgress` | calm progress, cancellable | yes (resumes) |
| `processing(_,p)` | `ProcessingStatus` / badge | "Editing your clips…"; **leaving is safe** | yes |
| `clipsReady` | Library badge + push | clips appear in `ClipGrid` | yes |
| `scheduled` | Calendar | posts on `ScheduleCalendar` | yes |
| `published` | `PublishStatus` + Coach (later) | published; teardown will follow | yes |
| `failed(r)` | `BatchDetail` error state | reason + retry; never silent | yes |

### 5.4 Transport: Supabase Realtime (not polling)

The FastAPI + Trigger.dev worker writes job progress to a Postgres `jobs` row; the app **subscribes** and feeds events into the reducer. Do not poll. ([Supabase — Subscribing to DB changes](https://supabase.com/docs/guides/realtime/subscribing-to-database-changes))

- **v1:** *Postgres Changes* — simplest, less setup; fine for per-user job rows.
- **Scale path:** *Broadcast from triggers* via `realtime.broadcast_changes()` attached to a Postgres trigger — the scalable option; migrate as job volume grows. ([Supabase — Broadcast](https://supabase.com/docs/guides/realtime/broadcast))
- **supabase-swift specifics:** register Postgres-change callbacks **before** calling `subscribe()`; consume via the AsyncStream API; binary payloads require supabase-swift **≥ 2.44.0**. ([Supabase Swift — subscribe](https://supabase.com/docs/reference/swift/subscribe))

```swift
let channel = supabase.channel("batch:\(batchID)")
let changes = channel.postgresChange(
    AnyAction.self, schema: "public", table: "jobs",
    filter: .eq("batch_id", value: batchID.uuidString)
)
await channel.subscribe()                       // callbacks registered BEFORE subscribe()
for await change in changes {
    if let progress = change.progress { store.send(.jobProgressed(jobID: change.jobID, progress: progress)) }
    if let clips = change.clips        { store.send(.clipsArrived(clips)) }
    if let reason = change.failure     { store.send(.failure(reason)) }
}
```

(See `12-backend-data-security.md` for the `jobs` table + trigger, and `09-video-pipeline.md` for the worker contract.)

### 5.5 Per-screen States (loading / empty / error / offline / permission-denied)

Every loop screen must implement all relevant states explicitly.

| Screen | loading | empty | error | offline | permission-denied |
|--------|---------|-------|-------|---------|-------------------|
| `TodayHome` | skeleton directive | "Connect your page to begin" → Brand Graph | retry banner | last-known directive from cache | n/a |
| `ScriptReader` | shimmer lines | "No scripts yet" → generate | "Couldn't write scripts" + retry | cached scripts read-only | n/a |
| `RecordSession` | camera warm-up | n/a | recorder error + retry | n/a (local) | **camera/mic primer → Settings deep link** |
| `UploadProgress` | progress bar | n/a | "Upload failed" + resume | **paused; auto-resume on reconnect** | n/a |
| `ProcessingStatus` | calm progress | n/a | `failed(reason)` + retry | **show last job state from cache; reconcile on reconnect** | n/a |
| Repurpose-in source | thumbnail load | "No videos found" | import error | n/a | **Photos primer → Settings** |
| `Calendar` | skeleton cells | "Nothing scheduled" | sync error | cached schedule read-only | n/a |
| `CoachFeed` | skeleton cards | "No teardowns yet" | retry | cached feed | **notifications primer** (so pushes can arrive) |

**Offline rule:** the loop always shows last-known job state from cache and reconciles on reconnect — never a blank screen.

---

## 6. Section-8 feature placement (anti-clutter doctrine → IA)

The governing principle: relegate features to secondary screens via progressive disclosure; present critical info first with expandable depth; one idea per screen. ([Usability Geek — Calm UX](https://usabilitygeek.com/ux-case-study-calm-mobile-app/), [LogRocket — Progressive disclosure](https://blog.logrocket.com/ux-design/progressive-disclosure-ux-types-use-cases/))

| # | Feature | IA placement (per doctrine) | Navigation mechanism / route |
|---|---------|----------------------------|------------------------------|
| 1 | **Batch "film once → post all week" (HERO)** | Spine of the **Studio** tab + the single Today directive ("Record this week's batch"). | Today directive deep link → `studio/batch/{id}` → FormatPicker → Record → processing → Library/Calendar |
| 2 | **Trend Radar** | ONE trend line on `TodayHome` → dedicated `TrendDetail`, one layer deep (not a tab). | `TodayRoute.trendDetail(trendID:)` |
| 3 | **Hook Lab** | **Nested inside `ScriptReader`** via progressive disclosure (expandable depth). | `StudioRoute.hookLab(scriptID:)` pushed from the reader |
| 4 | **Performance teardown cards** | **Coach** feed + an APNs push; archived in `InsightsArchive` (within Coach). | push → `coach/teardown/{id}`; Coach badge = unread count |
| 5 | **Streaks / consistency** | ONE gold glyph on `TodayHome`; full view in **Profile** (within Settings). | `TodayRoute.streakDetail` / Profile |
| 6 | **Repurpose-in (upload existing long video)** | A **second source toggle ON the `RecordSession` screen**, same pipeline — not a separate flow. | source segment on Record → same `BatchState` entry |
| 7 | **Referral loop** | A **row in Settings** + ONE earned prompt after a genuine win. | `settings/referral`; earned prompt is a one-time `sheet` gated on a win event |

### 6.1 Hero loop (detail)
The hero loop is the reason the Studio tab exists. The Today directive is the *only* place it is advertised; it deep-links straight into the relevant batch. It is never an "add-on" card bolted onto Today.

### 6.2 Trend Radar
Today shows a single trend line (e.g. one rising format/topic from the Virality Engine, `07-ai-system.md`). Tapping it pushes `TrendDetail` within the Today stack. No trend list ever appears on Today itself.

### 6.3 Hook Lab (progressive disclosure)
Hook Lab is **inside** the Script Reader, not a sibling screen in the nav tree. The reader shows the script calmly (P1); an expandable "Hooks" affordance pushes `HookLab(scriptID:)` for hook variants/A-B testing. This is the canonical "expandable section for deeper detail" pattern. ([LogRocket — Progressive disclosure](https://blog.logrocket.com/ux-design/progressive-disclosure-ux-types-use-cases/))

### 6.4 Teardown cards
A teardown becomes available after a post has performance data. It arrives as a Coach card **and** an APNs push routed through the same `DeepLink` enum (`marque://coach/teardown/{id}`). The Coach tab badge shows unread count. Old teardowns live in `InsightsArchive`.

### 6.5 Streaks
Consistency is one gold streak glyph on Today (P4). Tapping reveals `StreakDetail`; the comprehensive history lives in Profile. Streaks never grow into a Today dashboard.

### 6.6 Repurpose-in
"Upload existing long video" is a **source segment on the Record screen**, not a separate import flow — it enters the *same* `BatchState` machine at `uploading`/`processing`. It triggers the **Photos** permission state. (Confirm final entry point — Open Questions.)

### 6.7 Referral loop
A quiet **row in Settings** is always available. Additionally, exactly ONE earned prompt appears as a one-time `sheet` after a genuine win (first published batch / streak milestone), surfaced contextually — never an interstitial nag.

### 6.8 Paywall placement (IA-relevant; full spec in `11-monetization.md`)
Paywall logic lives in the **Router guard**, not strewn through views. Use RevenueCat **Placements/Targeting**: named locations (`onboarding_end`, `paywalled_feature`, `sale`), each serving a different Offering via `offerings.getCurrentOffering(forPlacement:)`. Gate locked features with `presentPaywallIfNeeded(requiredEntitlementIdentifier:)` (auto-dismisses when the entitlement activates). The highest-converting placement (`onboarding_end`) lives in the onboarding flow — **never** bolted onto Today. ([RevenueCat — Displaying Paywalls](https://www.revenuecat.com/docs/tools/paywalls/displaying-paywalls), [RevenueCat — Guide to mobile paywalls](https://www.revenuecat.com/blog/growth/guide-to-mobile-paywalls-subscription-apps/))

---

## 7. Deep links + universal links

Support **both** a custom scheme (`marque://`) for internal/push routing **and** HTTPS **Universal Links** (`https://marque.app/…`) for external/shared/web-fallback. Universal Links open the web page if the app isn't installed; custom-scheme links silently fail when uninstalled — so Universal Links are the **public** surface and the scheme is the **private** one. ([SwiftLee — Universal Links](https://www.avanderlee.com/swiftui/universal-links-ios/))

### 7.1 AASA + entitlement

Host `apple-app-site-association` (no extension, HTTPS, `Content-Type: application/json`) at `https://marque.app/.well-known/apple-app-site-association`. Add the **Associated Domains** entitlement `applinks:marque.app`. ([SwiftLee — Universal Links](https://www.avanderlee.com/swiftui/universal-links-ios/), [Bugfender — Universal Links](https://bugfender.com/blog/ios-universal-links/))

```json
{
  "applinks": {
    "details": [
      {
        "appIDs": ["TEAMID.com.marque.app"],
        "components": [
          { "/": "/script/*" },
          { "/": "/batch/*" },
          { "/": "/teardown/*" },
          { "/": "/clip/*" },
          { "/": "/calendar/*" },
          { "/": "/referral" }
        ]
      }
    ]
  }
}
```

> `TEAMID` and the final apex/bundle id are pending (Open Questions).

### 7.2 Handle BOTH entry points

In SwiftUI, handle `.onOpenURL` (custom scheme + cold-launch URL) **and** `.onContinueUserActivity(NSUserActivityTypeBrowsingWeb:)` (universal links resumed/handed-off). Missing the second drops links. Both parse into one `DeepLink` enum, then call `appRouter.handle(_:)`. ([SwiftLee — Deeplink handling](https://www.avanderlee.com/swiftui/deeplink-url-handling/), [SwiftLee — Universal Links](https://www.avanderlee.com/swiftui/universal-links-ios/))

```swift
enum DeepLink: Equatable {
    case today
    case batch(UUID)
    case script(UUID)
    case hookLab(scriptID: UUID)
    case record(batchID: UUID)
    case clip(UUID)
    case calendar(Date)
    case teardown(UUID)
    case paywall(placement: String)
    case referral
}

extension AppRouter {
    /// Single code path for universal links, custom-scheme links, AND APNs.
    func handle(_ link: DeepLink) {
        switch link {
        case .today:                  selectedTab = .today
        case .batch(let id):          selectedTab = .studio;   studioRouter.reset(to: .batchDetail(batchID: id))
        case .script(let id):         selectedTab = .studio;   studioRouter.push(.scriptReader(scriptID: id))
        case .hookLab(let id):        selectedTab = .studio;   studioRouter.push(.hookLab(scriptID: id))
        case .record(let id):         selectedTab = .studio;   studioRouter.push(.recordSession(batchID: id))
        case .clip(let id):           selectedTab = .library;  libraryRouter.push(.clipDetail(clipID: id))
        case .calendar(let date):     selectedTab = .calendar; calendarRouter.push(.dayView(date: date))
        case .teardown(let id):       selectedTab = .coach;    coachRouter.push(.teardownDetail(teardownID: id))
        case .paywall(let placement): sheet = .paywall(placement: placement)
        case .referral:               sheet = .settings(deepRow: .referral)
        }
    }
}
```

Because `TabView` is the parent with per-tab stacks, dispatch is clean: **switch tab, then push** onto that tab's router. ([createwithswift — Programmatic Tab Nav](https://www.createwithswift.com/programmatic-navigation-with-tab-view-in-swiftui/))

### 7.3 APNs → same code path

Push payloads carry a deep link (e.g. teardown ready → `marque://teardown/{id}` → Coach). APNs handling parses into the **same** `DeepLink` enum so push, universal links, and custom-scheme links share one route table. (Payload schema in `13-notifications-retention.md`.)

### 7.4 Canonical deep-link route table

| URL / scheme | `DeepLink` | Lands on |
|--------------|-----------|----------|
| `https://marque.app/` · `marque://today` | `.today` | Today |
| `…/batch/{id}` · `marque://studio/batch/{id}` | `.batch(id)` | BatchDetail (resets Studio stack) |
| `…/script/{id}` | `.script(id)` | ScriptReader |
| `…/script/{id}/hooklab` | `.hookLab(id)` | HookLab |
| `marque://record/{batchId}` | `.record(id)` | RecordSession (guarded) |
| `…/clip/{id}` | `.clip(id)` | ClipDetail (Library) |
| `…/calendar/{date}` | `.calendar(date)` | DayView |
| `…/teardown/{id}` | `.teardown(id)` | TeardownDetail (Coach) |
| `marque://paywall?placement={id}` | `.paywall(id)` | Paywall sheet |
| `…/referral` · `marque://settings/referral` | `.referral` | Settings → referral row |
| `marque://inbox` | `.inbox` | `InboxView` (own modal-rooted stack; un-pushable signal landing — `13-notifications-retention.md` §4.6) |

(Cross-ref `11-monetization.md` for placement IDs and `13-notifications-retention.md` for APNs payloads + the in-app inbox.)

---

## 8. Acceptance criteria

**Tab bar**
- [ ] Exactly 5 tabs render in the fixed order Today/Studio/Library/Calendar/Coach; no "More" overflow tab ever appears.
- [ ] The build compiles and the tab bar functions on the **iOS 17** deployment floor: the value-based `Tab` builder is gated behind `#available(iOS 18, *)`, with the `.tabItem`/`.tag` fallback driving programmatic selection on iOS 17. Both branches bind the same `TabView(selection:)`.
- [ ] Each tab hosts its own `NavigationStack`; switching tabs preserves each tab's back-history.
- [ ] All badges are derived from the data model; no imperative badge writes exist in the codebase.
- [ ] Only the gold "needs you" badge uses the gold accent on the tab bar; no second tab is colorized.

**Router**
- [ ] Each tab has one `@Observable` Router with a typed `[Route]` path; routes are enums with associated values.
- [ ] Exactly one `.navigationDestination(for:)` is registered per stack root.
- [ ] Routers are injected via `.environment(_:)` and read via `@Environment(_.self)`; no `ObservableObject`/`@EnvironmentObject` in the nav layer.
- [ ] Entry guards (script-approved, entitlement) live in the Router and are exercised identically by button tap, deep link, and push.

**Deep / universal links**
- [ ] AASA is served at `/.well-known/apple-app-site-association` with `application/json`; the Associated Domains entitlement is present.
- [ ] Both `.onOpenURL` and `.onContinueUserActivity` are handled and funnel into one `DeepLink` enum.
- [ ] Cold-launch from a universal link lands on the correct screen with the correct tab selected.
- [ ] Every row of the §7.4 table resolves to the listed screen.

**Core-loop state machine**
- [ ] `BatchState` makes illegal states unrepresentable; all transitions go through `reduce`.
- [ ] State mutation occurs only on the `MainActor`; transition methods are non-async.
- [ ] Job progress arrives via Supabase Realtime (not polling); callbacks are registered before `subscribe()`.
- [ ] The user can leave during `processing` and the result surfaces via state when ready.
- [ ] Every loop screen implements loading / empty / error / offline / permission-denied as specified in §5.5.

**Anti-clutter doctrine**
- [ ] `TodayHome` contains exactly one directive + one streak glyph + one trend line and nothing else.
- [ ] No Section-8 feature is a tab; each is placed per the §6 table.
- [ ] Hook Lab is reachable only from inside the Script Reader; Insights only from inside Coach.

---

## 9. Quick-reference: do / don't

**DO** — TabView parent + per-tab `NavigationStack`; gate the value-based `Tab` builder behind `#available(iOS 18, *)` with a `.tabItem`/`.tag` iOS 17 fallback on the same `TabView(selection:)`; `@Observable` Routers via `@Environment`; type-safe route enums; one `navigationDestination` per stack root; derived badges (both OS paths); handle both `onOpenURL` + `onContinueUserActivity`; Supabase Realtime into a centralized `reduce`; centralize nav guards in the Router; one shared `DeepLink` enum for links + push.

**DON'T** — ship the value-based `Tab(_:systemImage:value:)` / `Tab.badge` API ungated against an iOS 17 deployment target (it won't compile); a 6th tab or "More" overflow; one shared `NavigationPath` across tabs; `ObservableObject`/`@EnvironmentObject` in nav; scattered `navigationDestination` modifiers; imperative badge writes; custom-scheme links as the public/shared surface; polling for job state; bolting any Section-8 feature onto Today.

---

## Open questions

1. **Tab labels — final?** Confirm "Studio" (vs "Record"/"Create") for the hero-loop tab, and confirm **Insights is a sub-screen of Coach, not a 6th tab** (this doc assumes so). Owner: Product + Design.
2. **Universal Link domain / IDs.** Is `marque.app` the registered apex, and will the marketing site host the AASA? Need the **Team ID** and final **bundle id** to fill `appIDs` (`TEAMID.com.marque.app`). Owner: Eng + Ops.
3. **Realtime scaling cutover.** Start on Postgres Changes; at what concurrent-job volume do we migrate to Broadcast-from-triggers? Affects the `jobs` table + trigger design in `12-backend-data-security.md`. Owner: Backend.
4. **State-restoration depth.** Should per-tab `NavigationPath`s persist across cold launch (typed `[Route]` arrays serialized) for *all* routes, or always reset transient routes (RecordSession/UploadProgress) to tab root? This doc assumes "persist except live-capture routes." Owner: Eng.
5. **Repurpose-in entry point.** Confirm it is a **source toggle on the Record screen** (per doctrine, assumed here) rather than a Library import action — this changes the `BatchState` entry point. Owner: Product.

---

## Sources

- [Apple HIG — Tab bars](https://developer.apple.com/design/human-interface-guidelines/tab-bars) — 3–5 tab ceiling; avoid the "More" overflow tab.
- [Apple — TabView documentation](https://developer.apple.com/documentation/swiftui/tabview) — `selection` binding shared across both OS paths.
- [Apple — `Tab` documentation](https://developer.apple.com/documentation/swiftui/tab) — value-based `Tab(_:systemImage:value:)` builder; iOS 18+.
- [Apple — `tabItem(_:)`](https://developer.apple.com/documentation/swiftui/view/tabitem(_:)) — legacy iOS 17 tab API (`.tabItem` + `.tag`); deprecated only in iOS 18.1.
- [SerialCoder — Exploring the new TabView and Tab API](https://serialcoder.dev/text-tutorials/swiftui/exploring-the-new-tabview-and-tab-api-in-swiftui/) — value-based `Tab` is iOS 18; `.tabItem` deprecated in 18.1, still required on iOS 17.
- [SwiftLee — TabView explained](https://www.avanderlee.com/swiftui/tabview-tabbed-views/) — TabView-as-parent, per-tab `NavigationStack`, derived badges, iOS 18 `Tab` API.
- [DevTechie — SwiftUI badges](https://www.devtechie.com/blog/swiftui-badges-for-toolbars-and-tab-bars-in-ios-26) — string vs count badges.
- [Mastering SwiftUI Navigation — 2025 Guide](https://medium.com/@dinaga119/mastering-navigation-in-swiftui-the-2025-guide-to-clean-scalable-routing-bbcb6dbce929) — type-safe route enums; single `navigationDestination` registration.
- [Tiago Henriques — Router Pattern](https://www.tiagohenriques.dev/blog/swiftui-navigation-router-pattern) — Router with `path` + push/pop/popToRoot; views decoupled from destinations.
- [iCommunity — The Router Pattern](https://medium.com/icommunity/the-swiftui-navigation-architecture-that-will-save-your-projects-the-router-pattern-a38349198702) — typed path arrays for inspectability.
- [Advanced SwiftUI Navigation Patterns (Dec 2025)](https://medium.com/@chandra.welim/advanced-swiftui-navigation-patterns-production-ready-code-7886e7ae1937) — navigation guards, Observation injection, persistence.
- [SwiftUI Navigation in 2026 — NavigationStack, Deep Links, Coordinators](https://dev.to/__be2942592/swiftui-navigation-in-2026-the-complete-guide-navigationstack-deep-links-coordinators-hpk) — coordinator/deep-link dispatch tying tab selection + path.
- [createwithswift — Programmatic navigation with TabView](https://www.createwithswift.com/programmatic-navigation-with-tab-view-in-swiftui/) — switch-tab-then-push dispatch.
- [SwiftLee — Universal Links on iOS](https://www.avanderlee.com/swiftui/universal-links-ios/) — AASA format, Associated Domains, `onContinueUserActivity`.
- [SwiftLee — Deeplink URL handling in SwiftUI](https://www.avanderlee.com/swiftui/deeplink-url-handling/) — `onOpenURL` parsing, cold-launch handling.
- [Bugfender — iOS Universal Links guide](https://bugfender.com/blog/ios-universal-links/) — AASA hosting requirements.
- [Swift by Sundell — Modelling state in Swift](https://www.swiftbysundell.com/articles/modelling-state-in-swift/) — enum-with-associated-values; illegal states unrepresentable.
- [Splinter — Swift state machines with enums](https://www.splinter.com.au/2019/04/10/swift-state-machines-with-enums/) — enum state machine modeling.
- [LY Corp techblog — Robust state machine with Swift Concurrency](https://techblog.lycorp.co.jp/en/20250117a) — centralized `reduce`, non-async transitions, MainActor safety.
- [State-Driven Navigation for AI Workflows in SwiftUI](https://dev.to/programmingcentral/beyond-the-back-button-mastering-state-driven-navigation-for-ai-workflows-in-swiftui-2ab4) — drive UI from state during long jobs; no spinner-jail.
- [Supabase — Subscribing to database changes](https://supabase.com/docs/guides/realtime/subscribing-to-database-changes) — Postgres Changes for live job rows.
- [Supabase — Broadcast](https://supabase.com/docs/guides/realtime/broadcast) — `realtime.broadcast_changes()` from triggers for scale.
- [Supabase Swift — subscribe reference](https://supabase.com/docs/reference/swift/subscribe) — register callbacks before `subscribe()`; AsyncStream; binary payloads ≥ 2.44.0.
- [RevenueCat — Displaying Paywalls](https://www.revenuecat.com/docs/tools/paywalls/displaying-paywalls) — Placements/Targeting, `getCurrentOffering(forPlacement:)`, `presentPaywallIfNeeded`.
- [RevenueCat — Guide to mobile paywalls](https://www.revenuecat.com/blog/growth/guide-to-mobile-paywalls-subscription-apps/) — onboarding-end as highest-converting placement.
- [Usability Geek — Calm app UX case study](https://usabilitygeek.com/ux-case-study-calm-mobile-app/) — relegate features to secondary screens; one idea per screen.
- [LogRocket — Progressive disclosure in UX](https://blog.logrocket.com/ux-design/progressive-disclosure-ux-types-use-cases/) — expandable depth pattern (Hook Lab nesting model).
- [Building calm interfaces: less is more in 2026](https://medium.com/@mindcodersindore/building-calm-interfaces-less-is-more-in-2026-eab5fd810413) — color guides, not decorates.
- [SwiftfulRouting (reference library)](https://github.com/SwiftfulThinking/SwiftfulRouting) — off-the-shelf router considered; Marque hand-rolls instead.
