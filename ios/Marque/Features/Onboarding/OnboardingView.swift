import SwiftUI
import UIKit
import UserNotifications

// Onboarding — Cal AI-clean chrome (docs/ONBOARDING-DESIGN.md), flow per the
// documented thesis (docs/03-onboarding.md §1): ONE framing question → connect
// (the inference jackpot) → confirm what the scan learned → taste → goal → pace
// → async plan-building aha. ≤2 questions before value; a connected user types
// nothing. Single-select questions auto-advance (no "Next"); back is always
// available and cancels a pending advance.
struct OnboardingView: View {
    @Environment(AppStore.self) private var store

    enum Step: Int, CaseIterable {
        // REBUILT 2026-08-04 to the documented thesis (docs/03-onboarding.md §1):
        // "≤2 questions before value … cadence/format are inferred, not asked",
        // time-to-aha <90s. The shipped flow had drifted to 20 screens / 9 typed
        // fields; the funnel research (RevenueCat/Superwall/Videoleap teardowns,
        // plan file 2026-08-04) says tolerance for questions is proportional to how
        // visibly each answer changes the output. Every surviving step passes that
        // test; everything else was cut or moved to progressive profiling:
        //   CUT  blocker/whyNow (overlapping motivation asks; never steered output
        //        beyond one prompt line), cameraComfort (promised formats we retired),
        //        name (captured, never read ANYWHERE), stage/platform (derived from
        //        the linked account), method interstitial, the 4-question typed
        //        voiceInterview (voice is derived from real posts), the social-proof
        //        screen (no real numbers yet — inventing them is the Blinkist trap).
        //   MOVED voiceSliders + neverSay → Profile (already live there);
        //        styleTaste swiper → Profile's Editing-style page (already live).
        // Order: one framing question → connect (the inference jackpot) → confirm
        // what the scan learned → taste (emulate) → goal (personalizes the paywall)
        // → pace (commitment device) → the aha.
        // `notifications` (OWNER 2026-08-12): the permission ask was popping "at a
        // random time" (the first clips-ready moment, wherever that caught you).
        // Research placement (NN/g pre-permission priming; the RevenueCat/Superwall
        // funnel work in docs/03-onboarding.md): a dedicated page immediately AFTER
        // the aha — value just demonstrated, concrete promise attached — and before
        // the paywall. Never a cold OS prompt anywhere else in the app.
        // `emulate` ("Who do you want to sound like?" — Hormozi/Tate/Sapp/MrBeast) was
        // cut on beta feedback 2026-08-18: a photographer found the four hardcoded
        // creators irrelevant to their account, and they were. The step also failed the
        // flow's own contract above — the first three scripts are generated on-device
        // and never read emulationTargets, so the answer changed nothing the user could
        // see. Emulation still exists where the choice is INFORMED: Profile →
        // "Creators to watch", and the track button on any reel in the niche feed.
        case landing, niche, connect, identity, goal, frequency, building,
             notifications

        /// Quiz-progress dashes cover the QUESTIONS between landing and building.
        /// building/notifications are outcome screens, not quiz steps.
        var quizIndex: Int? {
            guard self != .landing, self != .building, self != .notifications else { return nil }
            return rawValue - 1
        }
        static let quizTotal = allCases.count - 3
    }

    @State private var step: Step = .landing
    @State private var goingBack = false
    // Build 71: quitting mid-quiz used to drop you back on the landing screen and make
    // you re-tap every answer (the answers themselves were already persisted on the
    // brand graph — only the POSITION was @State). The furthest step reached is
    // remembered so a relaunch resumes exactly where you left off.
    // v2 key: the rebuild renumbered every step, so a persisted v1 position would
    // resume onto the wrong screen. Old key is simply ignored.
    // v3: cutting `emulate` renumbered every step after it, so a v2 position would
    // resume a mid-flight tester onto the wrong screen.
    @AppStorage("onboarding.resumeStep.v3") private var resumeStepRaw = 0

    // Auto-advance plumbing: last tap wins within the 300ms window; back cancels.
    @State private var advanceTask: Task<Void, Never>?
    @State private var selectionTick = 0

    // Defaulted enums render unselected until touched (so every MCQ auto-advances
    // on a real choice, never on a default).
    @State private var goalTouched = false
    @State private var audienceTouched = false
    // Scan hold: connected users wait on branded theater while the real page scan
    // runs (owner-decided over confirm-later), capped at ~12s so a slow or dead
    // backend can never strand anyone — the identity screen just falls back to
    // editable-empty and the scan keeps filling in the background if it lands.
    @State private var scanHolding = false
    @State private var scanTask: Task<Void, Never>?

    var body: some View {
        Group {
            switch step {
            case .landing:
                WelcomeLanding(
                    onStart: { go(.niche) },
                    onHaveAccount: {
                        // Existing users skip the quiz — straight to the gates;
                        // their brand data restores after sign-in.
                        resumeStepRaw = 0
                        store.hasOnboarded = true
                        store.save()
                    }
                )
            case .niche:     nicheStep
            case .connect:   connectStep
            case .identity:  identityStep
            case .goal:          goalStep
            case .frequency:     frequencyStep
            case .building:      buildingStep
            case .notifications: notificationsStep
            }
        }
        .id(step)
        .transition(.asymmetric(
            insertion: goingBack ? .opacity : .move(edge: .trailing).combined(with: .opacity),
            removal: .opacity))
        .animation(Motion.enter, value: step)
        .sensoryFeedback(.impact(weight: .light), trigger: selectionTick)
        .background(Palette.canvas.ignoresSafeArea())
        .task { restoreProgress() }
    }

    // MARK: - Navigation

    private func go(_ target: Step) {
        let back = target.rawValue < step.rawValue
        goingBack = back
        withAnimation(Motion.enter) { step = target }
        // Remember the position (including backwards moves — where you ARE is what a
        // relaunch should restore). Cleared when onboarding finishes.
        resumeStepRaw = target.rawValue
        // FUNNEL (2026-08-04): the onboarding had ZERO instrumentation — drop-off was
        // unmeasurable, so every redesign was an opinion. One fire-and-forget event per
        // step entry gives us the per-step funnel. Back-navigation is tagged so it
        // doesn't inflate forward-progress counts.
        store.backend.reportClientEvent(
            "onboard_step",
            detail: "\(target.rawValue):\(String(describing: target))\(back ? ":back" : "")")
    }

    /// Restore the saved position on launch. Guards: never resume onto `.landing`
    /// (nothing to restore) and never past the enum.
    private func restoreProgress() {
        guard resumeStepRaw > Step.landing.rawValue,
              let target = Step(rawValue: resumeStepRaw) else { return }
        step = target
    }

    private func advance() {
        guard let next = Step(rawValue: step.rawValue + 1) else { return }
        go(next)
    }

    private func retreat() {
        advanceTask?.cancel()
        advanceTask = nil
        guard step != .landing else { return }
        guard let prev = Step(rawValue: step.rawValue - 1) else { return }
        go(prev)
    }

    /// Single-select answer: apply the choice, animate + haptic, then auto-advance
    /// after a cancellable beat. Re-taps within the window re-arm it (last wins).
    private func selectAndAdvance(_ apply: () -> Void) {
        withAnimation(Motion.spring) { apply() }
        selectionTick += 1
        advanceTask?.cancel()
        advanceTask = Task {
            try? await Task.sleep(for: .milliseconds(300))
            guard !Task.isCancelled else { return }
            advanceTask = nil
            advance()
        }
    }

    private func scaffold<C: View>(_ headline: String, _ subtitle: String? = nil,
                                   @ViewBuilder content: @escaping () -> C) -> some View {
        OnboardingScaffold(headline: headline, subtitle: subtitle,
                           showsProgress: step.quizIndex != nil,
                           progressIndex: (step.quizIndex ?? 0) + 1,
                           progressTotal: Step.quizTotal,
                           onBack: { retreat() },
                           content: content)
    }

    private func scaffold<C: View, T: View>(_ headline: String, _ subtitle: String? = nil,
                                            topAligned: Bool = false,
                                            @ViewBuilder content: @escaping () -> C,
                                            @ViewBuilder cta: @escaping () -> T) -> some View {
        OnboardingScaffold(headline: headline, subtitle: subtitle,
                           showsProgress: step.quizIndex != nil,
                           progressIndex: (step.quizIndex ?? 0) + 1,
                           progressTotal: Step.quizTotal,
                           topAligned: topAligned,
                           onBack: { retreat() },
                           content: content, cta: cta)
    }

    // MARK: - Q1: niche — the ONE framing question the spec allows before value

    // Tap-only (owner, 2026-08-16: "remove all of the text-based questions"),
    // then rebuilt as a Gymshark-style CHIP CLOUD (owner, same day: "more options
    // and niches… slide left and right… mimic this format almost exactly"):
    // staggered horizontal bands of capsule chips that bleed off both screen
    // edges and scroll independently. Coverage by breadth — ~50 niches across
    // three color-coded families — instead of typing; for connected users the
    // page scan derives the true niche anyway, so the chip is a seed.
    // cat: 0 = money & business (blue) · 1 = health & lifestyle (green) ·
    //      2 = create & entertain (amber)
    private static let nicheCloud: [(title: String, cat: Int)] = [
        ("Fitness", 1), ("Finance", 0), ("Comedy", 2), ("Beauty", 1),
        ("Startups", 0), ("Food & cooking", 1), ("Gaming", 2), ("Investing", 0),
        ("Self-improvement", 1), ("Music", 2), ("Real estate", 0), ("Fashion", 1),
        ("Filmmaking", 2), ("Marketing", 0), ("Nutrition", 1), ("Art & design", 2),
        ("E-commerce", 0), ("Gym & lifting", 1), ("Storytelling", 2), ("Crypto", 0),
        ("Skincare", 1), ("Photography", 2), ("Sales", 0), ("Travel", 1),
        ("Education", 2), ("Career growth", 0), ("Mental health", 1), ("Books & learning", 2),
        ("Side hustles", 0), ("Parenting", 1), ("Dance", 2), ("AI & tech", 0),
        ("Relationships", 1), ("Pop culture", 2), ("Coding", 0), ("Motivation", 1),
        ("Language learning", 2), ("Productivity", 0), ("Running", 1), ("Sports", 2),
        ("Business", 0), ("Yoga", 1), ("Pets & dogs", 2), ("Weight loss", 1),
        ("Home & DIY", 1), ("Cars", 2), ("Coffee", 1), ("Faith & spirituality", 1),
        ("Outdoors", 1), ("Lifestyle", 1), ("Golf", 2), ("Basketball", 2),
        ("Interior design", 1), ("Streetwear", 1), ("Baking", 1), ("Soccer", 2),
    ]
    private static let nicheCatColors: [Color] = [Palette.accent, Palette.positive, Palette.warning]

    /// Deal the flat list into `rows` horizontal bands, round-robin, so every band
    /// mixes families like the reference.
    private static func cloudRows(_ items: [(title: String, cat: Int)], rows: Int) -> [[(title: String, cat: Int)]] {
        var out = Array(repeating: [(title: String, cat: Int)](), count: rows)
        for (i, item) in items.enumerated() { out[i % rows].append(item) }
        return out
    }

    private var nicheStep: some View {
        @Bindable var store = store
        return scaffold("What do you make videos about?",
                        "One tap — this seeds everything I write for you.",
                        topAligned: true) {
            VStack(spacing: Space.md) {
                // Category legend, Gymshark-style.
                HStack(spacing: Space.md) {
                    legendDot(Self.nicheCatColors[0], "Money")
                    legendDot(Self.nicheCatColors[1], "Lifestyle")
                    legendDot(Self.nicheCatColors[2], "Create")
                }
                .frame(maxWidth: .infinity)

                // The staggered cloud: horizontal bands that bleed off both edges
                // and slide independently.
                VStack(spacing: Space.md) {
                    ForEach(Array(Self.cloudRows(Self.nicheCloud, rows: 8).enumerated()),
                            id: \.offset) { i, row in
                        ChipCloudRow(phase: i) {
                            ForEach(row, id: \.title) { item in
                                CloudChip(title: item.title,
                                          dot: Self.nicheCatColors[item.cat],
                                          selected: store.brand.niche == item.title) {
                                    selectAndAdvance { store.brand.niche = item.title }
                                }
                                .accessibilityIdentifier("onboard.niche.\(item.title.lowercased())")
                            }
                        }
                    }
                }
                .padding(.horizontal, -Space.screenH)   // full-bleed past the scaffold gutter
            }
        } cta: {}
    }

    private func legendDot(_ color: Color, _ label: String) -> some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(label).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
        }
    }

    // MARK: - Connect (hero) — the inference jackpot

    private var connectStep: some View {
        scaffold("Let me study your page",
                 "Connect one account and I'll read your recent posts — your niche, your audience, and how you actually talk. No typing.") {
            VStack(spacing: Space.lg) {
                ConnectAccountsView()
                // Named-provider AI consent (Apple 5.1.2(i)) — page content reaches
                // Anthropic's Claude; say so where the connection happens.
                Text("Your public posts are analyzed by Claude (Anthropic) to build your voice profile. Nothing is ever posted without you.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("onboard.connect.consent")
            }
        } cta: {
            VStack(spacing: Space.md) {
                OnbPill(title: "Read my posts",
                        enabled: !store.brand.connectedAccounts.isEmpty) {
                    beginScanHold()
                    advance()
                }
                .accessibilityIdentifier("onboard.connect.continue")
                Button {
                    advance()
                } label: {
                    Text("Skip for now")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                }
                .accessibilityIdentifier("onboard.connect.skip")
            }
        }
    }

    /// Hold the connected user on branded theater while the real scan runs.
    /// Two release paths — scan lands, or the 12s cap fires — whichever first.
    private func beginScanHold() {
        scanHolding = true
        scanTask?.cancel()
        scanTask = Task {
            await store.analyzePage()
            guard !Task.isCancelled else { return }
            withAnimation(Motion.enter) { scanHolding = false }
        }
        Task {
            try? await Task.sleep(for: .seconds(12))
            withAnimation(Motion.enter) { scanHolding = false }
        }
    }

    // MARK: - Identity: theater → the audience question
    // Replaces SIX typed screens (name/stage/niche/about/knownFor/platform).
    // OWNER (2026-08-12): this was a three-field form whose first field asked
    // "What do you do?" — which is the niche question two screens earlier, asked
    // again. Audience is the one thing niche does NOT already tell us, so it is
    // the only thing left to ask, and this is now a single-question page like
    // every other one. `whatYouDo`/`knownFor` are still filled by the page scan
    // for connected users and remain editable in Profile; for a skipper they stay
    // empty and `brand_block` simply omits them (niche carries the topic).

    private var identityStep: some View {
        Group {
            if scanHolding {
                OnboardingScaffold(headline: "Reading your posts",
                                   subtitle: nil,
                                   showsBack: false) {
                    ScanTheaterView()
                }
            } else {
                identityConfirm
            }
        }
    }

    /// Tap-only audience archetypes (owner, 2026-08-16). This was the ONE field
    /// every skipper had to type — the keyboard popping mid-quiz is the exact
    /// momentum break the funnel research says kills completion. Archetypes read
    /// as first-person audience descriptors, which is precisely what the script
    /// prompts consume; a connected user's scan-derived audience gets the top
    /// card so the honor-the-scan moment survives with zero typing.
    private static let audienceChips = [
        "Beginners starting out", "People a few steps behind me",
        "Busy professionals", "Founders & business owners",
        "Parents & families", "Students & Gen Z",
        "Women 30+", "Men 30+",
        "Creators & freelancers", "Corporate professionals",
        "Athletes & gym-goers", "Small business owners",
        "New grads", "Career changers", "Retirees",
        "Coaches & consultants", "Side hustlers",
        "Stay-at-home parents", "College students",
        "First-time founders", "Remote workers",
        "People trying to get fit", "Aspiring creators",
        "Local customers",
    ]

    private var identityConfirm: some View {
        // Prefilled only when the scan actually derived an audience — a scan that
        // came back empty (scrape found no posts) must not claim knowledge; those
        // users pick an archetype like everyone else.
        let derived = store.brand.analyzed ? store.brand.audience
            .trimmingCharacters(in: .whitespacesAndNewlines) : ""
        let prefilled = !derived.isEmpty
        return scaffold("Who's it for?",
                        prefilled ? "Pulled from your posts — or pick a better fit."
                                  : "The people on the other side of the camera.",
                        topAligned: true) {
            VStack(spacing: Space.md) {
                if prefilled {
                    OptionCard(icon: "OnbIcon-identity", sfFallback: "sparkles",
                               title: derived,
                               subtitle: "From your posts",
                               selected: audienceTouched && store.brand.audience == derived) {
                        selectAndAdvance {
                            store.brand.audience = derived
                            audienceTouched = true
                        }
                    }
                    .accessibilityIdentifier("onboard.audience.derived")
                }
                VStack(spacing: Space.md) {
                    ForEach(Array(Self.cloudRows(Self.audienceChips.map { ($0, 0) },
                                                 rows: 7).enumerated()),
                            id: \.offset) { i, row in
                        ChipCloudRow(phase: i) {
                            ForEach(row, id: \.title) { item in
                                CloudChip(title: item.title, dot: nil,
                                          selected: audienceTouched && store.brand.audience == item.title) {
                                    selectAndAdvance {
                                        store.brand.audience = item.title
                                        audienceTouched = true
                                    }
                                }
                                .accessibilityIdentifier("onboard.audience.\(item.title.prefix(8).lowercased())")
                            }
                        }
                    }
                }
                .padding(.horizontal, -Space.screenH)
            }
        } cta: {}
    }

    // MARK: - Goal — kept because it personalizes the paywall headline

    private var goalStep: some View {
        scaffold("What are you here to do?", "This shapes every script I write for you.") {
            VStack(spacing: Space.md) {
                goalCard(.audience, "OnbIcon-goal-audience", "megaphone")
                goalCard(.clients, "OnbIcon-goal-clients", "briefcase")
                goalCard(.authority, "OnbIcon-goal-authority", "crown")
                goalCard(.monetize, "OnbIcon-goal-monetize", "dollarsign.circle")
            }
        }
    }

    private func goalCard(_ goal: Goal, _ icon: String, _ sf: String) -> some View {
        OptionCard(icon: icon, sfFallback: sf, title: goal.rawValue,
                   selected: goalTouched && store.brand.goal == goal) {
            selectAndAdvance {
                store.brand.goal = goal
                goalTouched = true
            }
        }
        .accessibilityIdentifier("onboard.goal.\(String(describing: goal))")
    }

    // MARK: - Pace — the commitment device

    /// THE posting-cadence question — there is exactly one (build 63). Onboarding used to
    /// ask twice: "how often do you post right now?" here and "pick your weekly pace"
    /// eleven steps later, which read as the same question asked again. This is the one
    /// that matters, because `weeklyTarget` is what actually drives the plan
    /// (AppStore.weekGoal); the old before-picture answer only ever rode along to the
    /// backend, so it is DERIVED below instead of asked for.
    ///
    /// The ceiling is not 7. Serious creators run 2-3 posts a day across platforms, and
    /// capping the answer at "daily" told them the app wasn't built for their volume.
    private var frequencyStep: some View {
        scaffold("How often do you want to post?", "You can change this anytime.") {
            PaceSlider(weekly: Binding(
                get: { store.brand.weeklyTarget ?? 5 },
                set: { n in
                    store.brand.weeklyTarget = n
                    // Keep the coarse bucket the backend still reads in sync.
                    store.brand.postingFrequency = PostingFrequency.bucket(forWeekly: n)
                }))
        } cta: {
            OnbPill(title: "That's my pace") {
                if store.brand.weeklyTarget == nil {
                    store.brand.weeklyTarget = 5
                    store.brand.postingFrequency = PostingFrequency.bucket(forWeekly: 5)
                }
                // Pace was the last question — the aha starts NOW (the brand mirror
                // interstitial that used to sit here was cut; its ×52 math lives on
                // the pace slider itself).
                store.beginStarterScripts()
                advance()
            }
            .accessibilityIdentifier("onboard.continue")
        }
    }

    // MARK: - Emulate creators

    // MARK: - Building → ready (non-blocking aha)

    private var buildingStep: some View {
        Group {
            switch store.starterScriptsState {
            case .ready:
                OnboardingScaffold(headline: "Your first 3 scripts are ready",
                                   subtitle: "Record when you've got a few minutes — I'll do the editing.",
                                   showsBack: false) {
                    // The aha lands, then ONE more page: the notification primer
                    // (permission is asked right after demonstrated value, never
                    // at a random moment mid-app).
                    PlanReadyView { go(.notifications) }
                }
            case .failed:
                // BETA STUCK-STATE FIX: .failed used to render the building spinner
                // forever — a dead end with no affordance. Own screen + retry
                // (beginStarterScripts re-runs from .failed; the generator is local,
                // so a retry costs ~3s and can't hang on the network).
                OnboardingScaffold(headline: "That didn't build right",
                                   subtitle: "One tap and I'll write your scripts again.",
                                   showsBack: false) {
                    VStack(spacing: Space.xl) {
                        UnicornMascot(pose: .proud, size: 130)
                        OnbPill(title: "Try again") { store.beginStarterScripts() }
                            .accessibilityIdentifier("onboard.buildRetry")
                    }
                }
            case .idle, .running:
                OnboardingScaffold(headline: "Building your content plan",
                                   subtitle: nil,
                                   showsBack: false) {
                    PlanBuildingView()
                }
            }
        }
    }

    // MARK: - Notifications — the ONE permission page (owner, 2026-08-12)
    // Placement per the funnel research: immediately after the aha, before the
    // paywall, with concrete promises (clip-ready ping + filming reminder). The OS
    // dialog fires only from the explicit button — "Not now" never burns the
    // one-shot system prompt, and no other surface in the app cold-prompts.

    private var notificationsStep: some View {
        OnboardingScaffold(headline: "Know the moment it's ready",
                          subtitle: "Editing takes a couple of minutes — I'll ping you when your clip lands, and nudge you on filming days so the plan actually happens.",
                          showsBack: false) {
            VStack(spacing: Space.xl) {
                Spacer(minLength: 0)
                Image(systemName: "bell.badge")
                    .font(.system(size: 44, weight: .medium))
                    .foregroundStyle(Palette.accent)
                Spacer(minLength: 0)
                VStack(spacing: Space.md) {
                    OnbPill(title: "Turn on notifications") { finishWithNotifications() }
                        .accessibilityIdentifier("onboard.notifications.enable")
                    Button { completeAll() } label: {
                        Text("Not now")
                            .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                    }
                    .accessibilityIdentifier("onboard.notifications.skip")
                }
            }
        }
    }

    private func finishWithNotifications() {
        UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            Task { @MainActor in
                if granted {
                    // Both promises made above: remote "clip landed" pushes and the
                    // local filming-day reminder.
                    UIApplication.shared.registerForRemoteNotifications()
                    store.remindersEnabled = true
                }
                completeAll()
            }
        }
    }

    private func completeAll() {
        resumeStepRaw = 0
        store.completeOnboarding()
    }
}

// MARK: - Niche chip

/// Capsule chip for the one framing question — tap-not-type. OptionCard rows with
/// icons would make eight niches a full screen of scrolling; a 2-up capsule grid
/// keeps every choice above the fold.
// MARK: - Chip cloud (Gymshark-style staggered horizontal bands)

/// One horizontal band of the cloud. `phase` staggers where each row's content
/// starts so chips never align into columns, and the default scroll anchor
/// offsets rows differently so pills are cut off at BOTH screen edges — the
/// visual cue that there's more to slide to.
private struct ChipCloudRow<Content: View>: View {
    let phase: Int
    @ViewBuilder let content: () -> Content

    private var anchor: UnitPoint {
        [UnitPoint(x: 0.30, y: 0), UnitPoint(x: 0.62, y: 0),
         UnitPoint(x: 0.45, y: 0), UnitPoint(x: 0.70, y: 0),
         UnitPoint(x: 0.38, y: 0)][phase % 5]
    }

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: Space.sm, content: content)
                .padding(.horizontal, Space.lg)
        }
        .scrollIndicators(.hidden)
        .defaultScrollAnchor(anchor)
    }
}

/// A content-hugging capsule chip for the cloud (NicheChip stretches to fill a
/// grid column; these must size to their label like the reference).
private struct CloudChip: View {
    let title: String
    let dot: Color?
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if let dot {
                    Circle().fill(dot).frame(width: 7, height: 7)
                }
                Text(title)
                    .font(Typeface.sans(16, selected ? .semibold : .regular))
                    .lineLimit(1).fixedSize()
            }
            .foregroundStyle(selected ? Palette.canvas : Palette.textPrimary)
            .padding(.horizontal, 18)
            .padding(.vertical, 15)
            .background(selected ? Palette.ink : Palette.surfaceRaised, in: Capsule())
            .overlay(Capsule().strokeBorder(selected ? .clear : Palette.hairline, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

private struct NicheChip: View {
    let title: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(Typeface.sans(16, selected ? .semibold : .regular))
                .foregroundStyle(selected ? Palette.canvas : Palette.textPrimary)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(selected ? Palette.ink : Palette.surfaceRaised)
                .clipShape(Capsule())
                .overlay(Capsule().strokeBorder(selected ? .clear : Palette.hairline, lineWidth: 1))
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Scan theater

/// The hold screen while the real page scan runs — rotating progress lines so the
/// ~5-12s of genuine scraping reads as work being done for you, not a hang.
private struct ScanTheaterView: View {
    private static let lines = [
        "Reading your recent posts…",
        "Learning how you hook…",
        "Mapping your audience…",
        "Finding your voice…",
    ]
    @State private var idx = 0

    var body: some View {
        VStack(spacing: Space.xl) {
            UnicornMascot(pose: .proud, size: 130)
            Text(Self.lines[idx])
                .font(Typeface.display(24)).tracking(-0.4)
                .foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
                .id(idx)
                .transition(.opacity)
            ProgressView().tint(Palette.ink)
        }
        .frame(maxWidth: .infinity)
        .accessibilityIdentifier("onboard.scanTheater")
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2.4))
                withAnimation(.easeInOut(duration: 0.3)) { idx = (idx + 1) % Self.lines.count }
            }
        }
    }
}


// MARK: - Posts-per-week slider (build 70)

/// Cadence as a real slider, not five stacked cards. 1–21 posts a week with a live
/// readout, a plain-language read of what that pace means, and snap ticks at the
/// meaningful thresholds (daily = 7, twice daily = 14, three a day = 21) so the common
/// answers are still one gesture away.
private struct PaceSlider: View {
    @Binding var weekly: Int
    @State private var draft: Double = 5

    /// Below daily volume a per-day readout ("5 a week") just repeats the headline —
    /// only translate once the number means more than one a day.
    private var perDay: String? {
        guard weekly >= 7 else { return nil }
        if weekly % 7 == 0 { return weekly == 7 ? "1 a day" : "\(weekly / 7) a day" }
        return String(format: "%.1f a day", Double(weekly) / 7.0)
    }

    private var meaning: String {
        switch weekly {
        case ...2:  return "A gentle start — one filming session covers a month."
        case 3...4: return "A strong start — one filming session covers the week."
        case 5...6: return "The growth sweet spot for most niches."
        case 7:     return "Daily presence — maximum compounding."
        case 8...14: return "A serious posting operation."
        default:    return "Full content-machine volume."
        }
    }

    var body: some View {
        VStack(spacing: Space.xl) {
            VStack(spacing: 2) {
                Text("\(weekly)")
                    .font(Typeface.display(64, .semibold)).monospacedDigit()
                    .foregroundStyle(Palette.textPrimary)
                    .contentTransition(.numericText())
                    .animation(.snappy(duration: 0.2), value: weekly)
                Text(weekly == 1 ? "post a week" : "posts a week")
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                if let perDay {
                    Text(perDay)
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                }
            }
            VStack(spacing: Space.sm) {
                Slider(value: $draft, in: 1...21, step: 1)
                    .tint(Palette.ink)
                    .accessibilityIdentifier("onboard.paceSlider")
                    .onChange(of: draft) { _, v in
                        let n = Int(v.rounded())
                        if n != weekly {
                            weekly = n
                            UIImpactFeedbackGenerator(style: .light).impactOccurred()
                        }
                    }
                // Tick labels at the answers people actually mean.
                HStack {
                    ForEach([1, 7, 14, 21], id: \.self) { t in
                        Button { draft = Double(t); weekly = t } label: {
                            Text("\(t)")
                                .font(Typeface.sans(11, weekly == t ? .bold : .regular))
                                .foregroundStyle(weekly == t ? Palette.textPrimary : Palette.textTertiary)
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("onboard.pace.\(t)")
                    }
                }
            }
            VStack(spacing: Space.xs) {
                Text(meaning)
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                // The answer visibly changing the output is the contract that earns
                // the question (the cut brand-mirror screen carried this math before).
                Text("\(weekly * 52) posts a year — every one in your voice.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
            }
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity)
            .animation(.easeOut(duration: 0.15), value: weekly)
        }
        .onAppear { draft = Double(weekly) }
    }
}
