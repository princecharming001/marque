import SwiftUI

// Onboarding — Cal AI-clean rebuild (docs/ONBOARDING-DESIGN.md).
// One universal scaffold: pain cluster (incl. "why now") → belief interstitial →
// connect (scan prefills identity) → identity confirm → voice-teach → format →
// brand mirror → async plan-building aha. Single-select questions auto-advance
// (no "Next"); back is always available and cancels a pending advance. Steps
// whose answer a linked account already gave (stage, platform) auto-skip.
struct OnboardingView: View {
    @Environment(AppStore.self) private var store

    enum Step: Int, CaseIterable {
        // H-05: no styles step — the server infers style from the take now.
        // Connect comes BEFORE the identity cluster: the brand-scan derives
        // niche/audience/knownFor from real posts, so identity becomes
        // confirm-not-type for connected users (typing is the #1 completion
        // killer; taps and prefills are near-free).
        case landing, goal, blocker, whyNow, frequency, method,
             connectAccounts, name, stage, niche, about, knownFor, platform,
             voiceInterview, voiceSliders, emulate, cameraComfort, pace,
             mirror, building

        /// Quiz-progress dashes cover everything between landing and building.
        var quizIndex: Int? {
            guard self != .landing, self != .building else { return nil }
            return rawValue - 1
        }
        static let quizTotal = allCases.count - 2
    }

    @State private var step: Step = .landing
    @State private var goingBack = false

    // Auto-advance plumbing: last tap wins within the 300ms window; back cancels.
    @State private var advanceTask: Task<Void, Never>?
    @State private var selectionTick = 0

    // Defaulted enums render unselected until touched (so every MCQ auto-advances
    // on a real choice, never on a default).
    @State private var goalTouched = false
    // Distinguishes "stage answered in the quiz" from "stage derived from a linked
    // account" — only the derived case auto-skips the step (incl. on back-nav).
    @State private var stageTouched = false
    // Disambiguates "nil because unset" from "nil because user picked Both".
    @State private var platformBothChosen = false

    var body: some View {
        Group {
            switch step {
            case .landing:
                WelcomeLanding(
                    onStart: { go(.goal) },
                    onHaveAccount: {
                        // Existing users skip the quiz — straight to the gates;
                        // their brand data restores after sign-in.
                        store.hasOnboarded = true
                        store.save()
                    }
                )
            case .goal:          goalStep
            case .blocker:       blockerStep
            case .whyNow:        whyNowStep
            case .frequency:     frequencyStep
            case .method:        methodStep
            case .connectAccounts: connectAccountsStep
            case .name:          nameStep
            case .stage:         stageStep
            case .niche:         nicheStep
            case .about:         aboutStep
            case .knownFor:      knownForStep
            case .platform:        platformStep
            case .voiceInterview:  voiceInterviewStep
            case .voiceSliders:    voiceSlidersStep
            case .emulate:         emulateStep
            case .cameraComfort:   cameraComfortStep
            case .pace:          paceStep
            case .mirror:        mirrorStep
            case .building:      buildingStep
            }
        }
        .id(step)
        .transition(.asymmetric(
            insertion: goingBack ? .opacity : .move(edge: .trailing).combined(with: .opacity),
            removal: .opacity))
        .animation(Motion.enter, value: step)
        .sensoryFeedback(.impact(weight: .light), trigger: selectionTick)
        .background(Palette.canvas.ignoresSafeArea())
    }

    // MARK: - Navigation

    private func go(_ target: Step) {
        goingBack = target.rawValue < step.rawValue
        withAnimation(Motion.enter) { step = target }
    }

    /// Steps we already have the answer to (from a linked account) are never shown.
    private func shouldSkip(_ s: Step) -> Bool {
        switch s {
        case .stage:    return store.brand.stage != nil && !stageTouched // derived from real follower count
        case .platform: return !store.brand.connectedAccounts.isEmpty // derived from what they linked
        default:        return false
        }
    }

    private func advance() {
        var raw = step.rawValue + 1
        while let next = Step(rawValue: raw), shouldSkip(next) { raw += 1 }
        guard let next = Step(rawValue: raw) else { return }
        go(next)
    }

    private func retreat() {
        advanceTask?.cancel()
        advanceTask = nil
        guard step != .landing else { return }
        var raw = step.rawValue - 1
        while let prev = Step(rawValue: raw), shouldSkip(prev) { raw -= 1 }
        guard let prev = Step(rawValue: raw) else { return }
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
                                            @ViewBuilder content: @escaping () -> C,
                                            @ViewBuilder cta: @escaping () -> T) -> some View {
        OnboardingScaffold(headline: headline, subtitle: subtitle,
                           showsProgress: step.quizIndex != nil,
                           progressIndex: (step.quizIndex ?? 0) + 1,
                           progressTotal: Step.quizTotal,
                           onBack: { retreat() },
                           content: content, cta: cta)
    }

    // MARK: - Pain cluster

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

    private var blockerStep: some View {
        scaffold("What gets in the way most?", "I'll build your plan around fixing this.") {
            VStack(spacing: Space.md) {
                blockerCard(.ideas, "OnbIcon-blocker-ideas", "lightbulb", "ideas")
                blockerCard(.time, "OnbIcon-blocker-time", "hourglass", "time")
                blockerCard(.editing, "OnbIcon-blocker-editing", "scissors", "editing")
                blockerCard(.confidence, "OnbIcon-blocker-confidence", "face.dashed", "confidence")
            }
        }
    }

    private func blockerCard(_ b: CreatorBlocker, _ icon: String, _ sf: String, _ idKey: String) -> some View {
        OptionCard(icon: icon, sfFallback: sf, title: b.rawValue,
                   selected: store.brand.biggestBlocker == b) {
            selectAndAdvance { store.brand.biggestBlocker = b }
        }
        .accessibilityIdentifier("onboard.blocker.\(idKey)")
    }

    private var whyNowStep: some View {
        scaffold("Why now?", "This is the moment we build everything around.") {
            VStack(spacing: Space.md) {
                whyNowCard(.serious, "OnbIcon-why-serious", "flame")
                whyNowCard(.launch, "OnbIcon-why-launch", "paperplane")
                whyNowCard(.inspired, "OnbIcon-why-inspired", "chart.line.uptrend.xyaxis")
                whyNowCard(.income, "OnbIcon-why-income", "banknote")
            }
        }
    }

    private func whyNowCard(_ w: WhyNow, _ icon: String, _ sf: String) -> some View {
        OptionCard(icon: icon, sfFallback: sf, title: w.rawValue,
                   selected: store.brand.whyNow == w) {
            selectAndAdvance { store.brand.whyNow = w }
        }
        .accessibilityIdentifier("onboard.whyNow.\(w.key)")
    }

    private var frequencyStep: some View {
        scaffold("How often do you post right now?", "No judgment — this is the before picture.") {
            VStack(spacing: Space.md) {
                freqCard(.rarely, "OnbIcon-freq-rarely", "tortoise", "rarely")
                freqCard(.sometimes, "OnbIcon-freq-sometimes", "figure.walk", "sometimes")
                freqCard(.often, "OnbIcon-freq-often", "hare", "often")
                freqCard(.daily, "OnbIcon-freq-daily", "flame", "daily")
            }
        }
    }

    private func freqCard(_ f: PostingFrequency, _ icon: String, _ sf: String, _ idKey: String) -> some View {
        OptionCard(icon: icon, sfFallback: sf, title: f.rawValue,
                   selected: store.brand.postingFrequency == f) {
            selectAndAdvance { store.brand.postingFrequency = f }
        }
        .accessibilityIdentifier("onboard.frequency.\(idKey)")
    }

    // MARK: - Interstitial A: the method (belief builder)

    private var methodStep: some View {
        let freq = store.brand.postingFrequency
        let line: String = switch freq {
        case .rarely, .none:
            "Most creators stall because every post is built from scratch. Yunicorn flips that: film once a week, and I turn it into daily content."
        case .sometimes:
            "You're already posting — the problem is the cost per post. Film once a week with me, and every session becomes 5+ pieces of content."
        case .often, .daily:
            "You've got the volume. Now make every post compound: one filming session, scripts in your voice, edits handled."
        }
        // No mascot, no stat card — just the copy, typed out. The scaffold's static
        // header is suppressed (empty headline) so the typewriter owns the reveal.
        return scaffold("") {
            OnboardingTypewriter(headline: "Consistency beats virality", message: line)
        } cta: {
            OnbPill(title: "Let's do it") { advance() }
                .accessibilityIdentifier("onboard.continue")
        }
    }

    // MARK: - Identity cluster (freeform)

    @FocusState private var nameFocused: Bool

    private var nameStep: some View {
        @Bindable var store = store
        return scaffold("What should I call you?", "The name you'd like to go by.") {
            FreeformField(placeholder: "Your name", text: $store.brand.creatorNameBinding,
                          capitalization: .words, focused: $nameFocused,
                          accessibilityID: "onboard.creatorName")
        } cta: {
            OnbPill(title: "Continue",
                    enabled: !(store.brand.creatorName ?? "").trimmingCharacters(in: .whitespaces).isEmpty) {
                nameFocused = false
                advance()
            }
            .accessibilityIdentifier("onboard.nameContinue")
        }
        .onAppear { nameFocused = true }
    }

    // Only shown when no account was connected — a linked account's real follower
    // count sets `stage` and this step auto-skips (see shouldSkip).
    private var stageStep: some View {
        scaffold("Where are you today?", "So I calibrate for where you are — not where you're going.") {
            VStack(spacing: Space.md) {
                stageCard(.nano, "OnbIcon-stage-nano", "person", "nano")
                stageCard(.micro, "OnbIcon-stage-micro", "person.2", "micro")
                stageCard(.established, "OnbIcon-stage-established", "person.3", "established")
                stageCard(.pro, "OnbIcon-stage-pro", "crown", "pro")
            }
        }
    }

    private func stageCard(_ s: CreatorStage, _ icon: String, _ sf: String, _ idKey: String) -> some View {
        OptionCard(icon: icon, sfFallback: sf, title: s.rawValue,
                   selected: store.brand.stage == s) {
            selectAndAdvance {
                store.brand.stage = s
                stageTouched = true
            }
        }
        .accessibilityIdentifier("onboard.stage.\(idKey)")
    }

    @FocusState private var nicheFocused: Bool

    private var nicheStep: some View {
        @Bindable var store = store
        let prefilled = store.brand.analyzed && !store.brand.niche.trimmingCharacters(in: .whitespaces).isEmpty
        return scaffold("What's your niche?",
                        prefilled ? "Pulled from your page — fix it if it's off."
                                  : "Fitness, finance, cooking… whatever you make content about.") {
            FreeformField(placeholder: "Your niche", text: $store.brand.niche,
                          fontSize: 30, focused: $nicheFocused,
                          accessibilityID: "onboard.niche")
        } cta: {
            OnbPill(title: "Continue",
                    enabled: !store.brand.niche.trimmingCharacters(in: .whitespaces).isEmpty) {
                nicheFocused = false
                advance()
            }
            .accessibilityIdentifier("onboard.nicheContinue")
        }
        .onAppear { nicheFocused = true }
    }

    @FocusState private var aboutFocused: Bool

    private var aboutStep: some View {
        @Bindable var store = store
        return scaffold("Tell me about you", "What you do, and who it's for.") {
            VStack(spacing: Space.lg) {
                TextField("What do you do?", text: $store.brand.whatYouDo, axis: .vertical)
                    .focused($aboutFocused)
                    .marqueField()
                    .lineLimit(1...3)
                    .accessibilityIdentifier("onboard.whatYouDo")
                TextField("Who is it for?", text: $store.brand.audience, axis: .vertical)
                    .marqueField()
                    .lineLimit(1...3)
                    .accessibilityIdentifier("onboard.audience")
            }
        } cta: {
            OnbPill(title: "Continue",
                    enabled: !store.brand.whatYouDo.trimmingCharacters(in: .whitespaces).isEmpty
                          && !store.brand.audience.trimmingCharacters(in: .whitespaces).isEmpty) {
                aboutFocused = false
                advance()
            }
            .accessibilityIdentifier("onboard.aboutContinue")
        }
        .onAppear { aboutFocused = true }
    }

    @FocusState private var knownForFocused: Bool

    private var knownForStep: some View {
        @Bindable var store = store
        let prefilled = store.brand.analyzed && !store.brand.knownFor.trimmingCharacters(in: .whitespaces).isEmpty
        return scaffold("What do you want to be known for?",
                        prefilled ? "Here's what your page already says — make it yours."
                                  : "The heart of your brand — one sentence.") {
            FreeformField(placeholder: "Known for…", text: $store.brand.knownFor,
                          fontSize: 26, focused: $knownForFocused,
                          accessibilityID: "onboard.knownFor")
        } cta: {
            VStack(spacing: Space.md) {
                OnbPill(title: "Continue",
                        enabled: !store.brand.knownFor.trimmingCharacters(in: .whitespaces).isEmpty) {
                    knownForFocused = false
                    advance()
                }
                .accessibilityIdentifier("onboard.knownForContinue")
                Button {
                    knownForFocused = false
                    advance()
                } label: {
                    Text("Skip — I'll add this later")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                }
                .accessibilityIdentifier("onboard.knownForSkip")
            }
        }
        .onAppear { knownForFocused = true }
    }

    // MARK: - Platform

    private var platformStep: some View {
        scaffold("Where does your audience live?", "Where your clips will land first.") {
            VStack(spacing: Space.md) {
                OptionCard(icon: "OnbIcon-platform-instagram", sfFallback: "camera",
                           title: "Instagram",
                           selected: !platformBothChosen && store.brand.primaryPlatform == .instagram) {
                    selectAndAdvance {
                        store.brand.primaryPlatform = .instagram
                        platformBothChosen = false
                    }
                }
                .accessibilityIdentifier("onboard.platform.instagram")

                OptionCard(icon: "OnbIcon-platform-tiktok", sfFallback: "music.note",
                           title: "TikTok",
                           selected: !platformBothChosen && store.brand.primaryPlatform == .tiktok) {
                    selectAndAdvance {
                        store.brand.primaryPlatform = .tiktok
                        platformBothChosen = false
                    }
                }
                .accessibilityIdentifier("onboard.platform.tiktok")

                OptionCard(icon: "OnbIcon-platform-both", sfFallback: "square.on.square",
                           title: "Both",
                           selected: platformBothChosen) {
                    selectAndAdvance {
                        store.brand.primaryPlatform = nil
                        platformBothChosen = true
                    }
                }
                .accessibilityIdentifier("onboard.platform.both")
            }
        }
    }

    // MARK: - Voice teach (in flow, two consecutive steps — connect THEN interview)

    private var connectAccountsStep: some View {
        scaffold("Connect your accounts", "I'll read your posts, learn how you actually talk, and fill in the next steps for you.") {
            ConnectAccountsView()
        } cta: {
            VStack(spacing: Space.md) {
                OnbPill(title: "Continue") {
                    // Reading past posts is never worth blocking onboarding for — kick it
                    // off detached and move on immediately. By the time onboarding
                    // finishes (several more screens), the scan has usually landed;
                    // analyzePage() is safe to call concurrently with itself/mirrorStep
                    // and just overwrites pillars/voice when it resolves.
                    if !store.brand.connectedAccounts.isEmpty {
                        Task { await store.analyzePage() }
                    }
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

    private var voiceInterviewStep: some View {
        scaffold("A few quick questions",
                 store.brand.analyzed
                    ? "I've studied your posts already — these sharpen what I learned. Skip if you're short on time."
                    : "Two minutes, typed — I listen for your real voice.") {
            VoiceInterviewView { advance() }
        } cta: {
            Button {
                // Don't clobber a successful connect-analyze from the prior step.
                if !store.brand.analyzed { store.derivePillars() }
                advance()
            } label: {
                Text("Skip for now")
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
            }
            .accessibilityIdentifier("onboard.interview.skip")
        }
    }

    private var voiceSlidersStep: some View {
        @Bindable var store = store
        return scaffold("Fine-tune your voice", "Slide until the preview sounds like you.") {
            VStack(spacing: Space.lg) {
                VStack(spacing: Space.lg) {
                    voiceSliderRow("Funny", "Serious", value: $store.brand.voice.funnyToSerious)
                    MarqueHairline()
                    voiceSliderRow("Polished", "Raw", value: $store.brand.voice.polishedToRaw)
                    MarqueHairline()
                    voiceSliderRow("Teacher", "Peer", value: $store.brand.voice.teacherToPeer)
                }
                .padding(Space.lg)
                .background(Palette.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))

                Text(voicePreviewLine)
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)

                // Optional guardrail — the backend's "never say" prompt line existed
                // for months with nothing ever collecting it.
                TextField("Words I should never use (optional)", text: $neverSayDraft)
                    .marqueField()
                    .accessibilityIdentifier("onboard.neverSay")
            }
        } cta: {
            OnbPill(title: "Continue") {
                store.brand.nonNegotiables = neverSayDraft
                    .split(separator: ",")
                    .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                    .filter { !$0.isEmpty }
                advance()
            }
            .accessibilityIdentifier("onboard.voiceContinue")
        }
    }

    @State private var neverSayDraft = ""

    private var voicePreviewLine: String {
        let funny = store.brand.voice.funnyToSerious
        let polished = store.brand.voice.polishedToRaw
        let teacher = store.brand.voice.teacherToPeer
        let tone = funny < 0.35 ? "witty and light" : funny > 0.65 ? "grounded and serious" : "balanced"
        let style = polished < 0.35 ? "clean and produced" : polished > 0.65 ? "unfiltered and real" : "conversational"
        let mode = teacher < 0.35 ? "teaching the room" : teacher > 0.65 ? "talking to peers" : "guiding alongside"
        return "\u{201C}\(tone.capitalized), \(style), \(mode).\u{201D} Yunicorn will write every script in this voice."
    }

    private func voiceSliderRow(_ leading: String, _ trailing: String, value: Binding<Double>) -> some View {
        VStack(spacing: Space.xs) {
            HStack {
                Text(leading)
                    .font(AppFont.callout)
                    .foregroundStyle(value.wrappedValue < 0.4 ? Palette.textPrimary : Palette.textTertiary)
                Spacer()
                Text(trailing)
                    .font(AppFont.callout)
                    .foregroundStyle(value.wrappedValue > 0.6 ? Palette.textPrimary : Palette.textTertiary)
            }
            Slider(value: value).tint(Palette.ink)
        }
    }

    // MARK: - Emulate creators

    private var emulateStep: some View {
        scaffold("Who do you want to sound like?",
                 "Pick creators whose style you admire — I'll study how they hook, pace, and talk.") {
            EmulateStep()
        } cta: {
            VStack(spacing: Space.md) {
                OnbPill(title: "Continue", enabled: !(store.brand.emulationTargets ?? []).isEmpty) {
                    advance()
                }
                .accessibilityIdentifier("onboard.emulate.continue")
                Button { advance() } label: {
                    Text("Skip for now")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                }
                .accessibilityIdentifier("onboard.emulate.skip")
            }
        }
    }

    // MARK: - Format cluster

    private var cameraComfortStep: some View {
        scaffold("How do you feel on camera?", "There's a format for every comfort level.") {
            VStack(spacing: Space.md) {
                comfortCard(.natural, "OnbIcon-comfort-natural", "video", "natural")
                comfortCard(.gettingThere, "OnbIcon-comfort-getting", "video.badge.checkmark", "getting")
                comfortCard(.preferOff, "OnbIcon-comfort-off", "mic", "off")
            }
        }
    }

    private func comfortCard(_ c: CameraComfort, _ icon: String, _ sf: String, _ idKey: String) -> some View {
        // H-05: comfort no longer seeds preferredStyles — the server infers the
        // right style from the actual take (analyze-first), not a quiz answer.
        OptionCard(icon: icon, sfFallback: sf, title: c.rawValue,
                   selected: store.brand.cameraComfort == c) {
            selectAndAdvance { store.brand.cameraComfort = c }
        }
        .accessibilityIdentifier("onboard.comfort.\(idKey)")
    }

    private var paceStep: some View {
        scaffold("Pick your weekly pace", "You can change this anytime.") {
            VStack(spacing: Space.md) {
                paceCard(3, "OnbIcon-pace-3", "A strong start — one filming session covers it")
                paceCard(5, "OnbIcon-pace-5", "The growth sweet spot for most niches")
                paceCard(7, "OnbIcon-pace-7", "Maximum compounding — daily presence")
            }
        }
    }

    private func paceCard(_ n: Int, _ icon: String, _ subtitle: String) -> some View {
        OptionCard(icon: icon, sfFallback: "\(n).circle", title: "\(n) posts a week",
                   subtitle: subtitle, selected: store.brand.weeklyTarget == n) {
            selectAndAdvance { store.brand.weeklyTarget = n }
        }
        .accessibilityIdentifier("onboard.pace.\(n)")
    }

    // MARK: - Interstitial B: brand mirror → build

    private var mirrorStep: some View {
        let niche = store.brand.niche.isEmpty ? "your niche" : store.brand.niche
        let audience = store.brand.audience.isEmpty ? "your audience" : store.brand.audience
        let knownFor = store.brand.knownFor.isEmpty ? "what you stand for" : store.brand.knownFor
        return scaffold("Your brand, in a sentence") {
            VStack(spacing: Space.xl) {
                UnicornMascot(pose: .proud, size: 130)
                (Text("A ").foregroundStyle(Palette.textSecondary)
                 + Text(niche).foregroundStyle(Palette.textPrimary)
                 + Text(" creator for ").foregroundStyle(Palette.textSecondary)
                 + Text(audience).foregroundStyle(Palette.textPrimary)
                 + Text(", known for ").foregroundStyle(Palette.textSecondary)
                 + Text(knownFor).foregroundStyle(Palette.textPrimary)
                 + Text(".").foregroundStyle(Palette.textSecondary))
                    .font(Typeface.display(26)).tracking(-0.4)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Every script I write points back to this.")
                    .font(AppFont.body).foregroundStyle(Palette.textTertiary)
                if let pace = store.brand.weeklyTarget {
                    Text("\(pace) posts a week — that's \(pace * 52) in a year, every one in your voice.")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.center)
                }
            }
        } cta: {
            OnbPill(title: "Build my plan") {
                store.beginStarterScripts()
                advance()
            }
            .accessibilityIdentifier("onboard.buildPlan")
        }
    }

    // MARK: - Building → ready (non-blocking aha)

    private var buildingStep: some View {
        Group {
            if store.starterScriptsState == .ready {
                OnboardingScaffold(headline: "Your first 3 scripts are ready",
                                   subtitle: "Record when you've got a few minutes — I'll do the editing.",
                                   showsBack: false) {
                    PlanReadyView { store.completeOnboarding() }
                }
            } else {
                OnboardingScaffold(headline: "Building your content plan",
                                   subtitle: nil,
                                   showsBack: false) {
                    PlanBuildingView()
                }
            }
        }
    }
}

// MARK: - Small helpers

private extension BrandGraph {
    /// `creatorName` is Optional in the model; the text field wants a String binding.
    var creatorNameBinding: String {
        get { creatorName ?? "" }
        set { creatorName = newValue.isEmpty ? nil : newValue }
    }
}

// MARK: - Method interstitial stat card

/// The "consistency beats virality" comparison — two labeled, animated bars with
/// trailing multiplier values, on a sunken track. Replaces the old floaty capsule
/// pair; owns its own appear-animation state so the bars grow in on first render.
// A typed-out reveal for interstitial copy: the serif headline types itself first, then
// the body types beneath it, each trailed by a blinking caret — a calm "message arriving"
// feel that replaces the old mascot + stat card. Reuses the chat typewriter cadence.
private struct OnboardingTypewriter: View {
    let headline: String
    let message: String

    @State private var headlineShown = ""
    @State private var bodyShown = ""
    @State private var stage = 0          // 0 = typing headline · 1 = typing body · 2 = done
    @State private var caretOn = true

    var body: some View {
        VStack(spacing: Space.lg) {
            headlineText
                .fixedSize(horizontal: false, vertical: true)
            if stage >= 1 {
                bodyText
                    .fixedSize(horizontal: false, vertical: true)
                    .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity)
        .multilineTextAlignment(.center)
        .animation(Motion.quick, value: stage)
        .task { await type() }
        .task { await blink() }
    }

    private var headlineText: Text {
        let base = Text(headlineShown)
            .font(Typeface.display(30)).tracking(-0.6)
            .foregroundColor(Palette.textPrimary)
        return stage == 0 ? base + caret : base
    }

    private var bodyText: Text {
        let base = Text(bodyShown)
            .font(AppFont.body).foregroundColor(Palette.textSecondary)
        return stage == 1 ? base + caret : base
    }

    private var caret: Text {
        Text("▍").foregroundColor(caretOn ? Palette.accent : .clear)
    }

    private func type() async {
        await reveal(headline) { headlineShown = $0 }
        try? await Task.sleep(nanoseconds: 280_000_000)   // a beat before the body
        withAnimation(Motion.quick) { stage = 1 }
        await reveal(message) { bodyShown = $0 }
        stage = 2
    }

    /// Reveal `full` 2–3 characters at a time (~20ms/step), the chat typewriter cadence.
    private func reveal(_ full: String, _ set: (String) -> Void) async {
        var idx = full.startIndex
        while idx < full.endIndex, !Task.isCancelled {
            idx = full.index(idx, offsetBy: Int.random(in: 2...3), limitedBy: full.endIndex) ?? full.endIndex
            set(String(full[..<idx]))
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    private func blink() async {
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: 500_000_000)
            caretOn.toggle()
        }
    }
}
