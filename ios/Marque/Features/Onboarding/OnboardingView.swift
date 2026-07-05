import SwiftUI

// Onboarding — Cal AI-clean rebuild (docs/ONBOARDING-DESIGN.md).
// 17 screens on one universal scaffold: pain cluster → belief interstitial →
// identity → platform → voice-teach (in flow) → format → brand mirror → async
// plan-building aha. Single-select questions auto-advance (no "Next"); back is
// always available and cancels a pending advance.
struct OnboardingView: View {
    @Environment(AppStore.self) private var store

    enum Step: Int, CaseIterable {
        case landing, goal, blocker, frequency, method, name, niche, about,
             knownFor, platform, voiceTeach, voiceSliders, cameraComfort,
             styles, pace, mirror, building

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
            case .frequency:     frequencyStep
            case .method:        methodStep
            case .name:          nameStep
            case .niche:         nicheStep
            case .about:         aboutStep
            case .knownFor:      knownForStep
            case .platform:      platformStep
            case .voiceTeach:    voiceTeachStep
            case .voiceSliders:  voiceSlidersStep
            case .cameraComfort: cameraComfortStep
            case .styles:        stylesStep
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

    private func advance() {
        guard let next = Step(rawValue: step.rawValue + 1) else { return }
        go(next)
    }

    private func retreat() {
        advanceTask?.cancel()
        advanceTask = nil
        guard step != .landing, let prev = Step(rawValue: step.rawValue - 1) else { return }
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
                goalCard(.clients, "OnbIcon-goal-clients", "handshake")
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
                blockerCard(.confidence, "OnbIcon-blocker-confidence", "theatermasks", "confidence")
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
            "Most creators stall because every post is built from scratch. Marque flips that: film once a week, and I turn it into daily content."
        case .sometimes:
            "You're already posting — the problem is the cost per post. Film once a week with me, and every session becomes 5+ pieces of content."
        case .often, .daily:
            "You've got the volume. Now make every post compound: one filming session, scripts in your voice, edits handled."
        }
        return scaffold("Consistency beats virality", line) {
            VStack(spacing: Space.xl) {
                // Two-bar comparison, drawn with capsules — ink vs neutral.
                VStack(alignment: .leading, spacing: Space.lg) {
                    barRow(label: "Chasing viral hits", fraction: 0.3, filled: false)
                    barRow(label: "Posting weekly with Marque", fraction: 0.9, filled: true)
                }
                .padding(Space.lg)
                .background(Palette.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))

                Text("Accounts that post 3×+ a week grow ~4× faster than accounts that post sporadically.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    .multilineTextAlignment(.center)

                UnicornMascot(pose: .thinking, size: 120)
            }
        } cta: {
            OnbPill(title: "Let's do it") { advance() }
                .accessibilityIdentifier("onboard.continue")
        }
    }

    private func barRow(label: String, fraction: CGFloat, filled: Bool) -> some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(label)
                .font(AppFont.callout)
                .foregroundStyle(filled ? Palette.textPrimary : Palette.textTertiary)
            GeometryReader { geo in
                Capsule()
                    .fill(filled ? Palette.ink : Color(hex: 0xE2E1DE))
                    .frame(width: geo.size.width * fraction, height: 10)
            }
            .frame(height: 10)
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

    @FocusState private var nicheFocused: Bool

    private var nicheStep: some View {
        @Bindable var store = store
        return scaffold("What's your niche?", "Fitness, finance, cooking… whatever you make content about.") {
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
        return scaffold("What do you want to be known for?", "The heart of your brand — one sentence.") {
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

    // MARK: - Voice teach (in flow)

    private var voiceTeachStep: some View {
        scaffold("Let me learn your voice", "So every script sounds like you — not a template.") {
            VoiceTeachStep(onDone: { advance() }, onSkip: { advance() })
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
            }
        } cta: {
            OnbPill(title: "Continue") { advance() }
                .accessibilityIdentifier("onboard.voiceContinue")
        }
    }

    private var voicePreviewLine: String {
        let funny = store.brand.voice.funnyToSerious
        let polished = store.brand.voice.polishedToRaw
        let teacher = store.brand.voice.teacherToPeer
        let tone = funny < 0.35 ? "witty and light" : funny > 0.65 ? "grounded and serious" : "balanced"
        let style = polished < 0.35 ? "clean and produced" : polished > 0.65 ? "unfiltered and real" : "conversational"
        let mode = teacher < 0.35 ? "teaching the room" : teacher > 0.65 ? "talking to peers" : "guiding alongside"
        return "\u{201C}\(tone.capitalized), \(style), \(mode).\u{201D} Marque will write every script in this voice."
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

    // MARK: - Format cluster

    private var cameraComfortStep: some View {
        scaffold("How do you feel on camera?", "There's a format for every comfort level.") {
            VStack(spacing: Space.md) {
                comfortCard(.natural, "OnbIcon-comfort-natural", "video", "natural")
                comfortCard(.gettingThere, "OnbIcon-comfort-getting", "video.badge.ellipsis", "getting")
                comfortCard(.preferOff, "OnbIcon-comfort-off", "mic", "off")
            }
        }
    }

    private func comfortCard(_ c: CameraComfort, _ icon: String, _ sf: String, _ idKey: String) -> some View {
        OptionCard(icon: icon, sfFallback: sf, title: c.rawValue,
                   selected: store.brand.cameraComfort == c) {
            selectAndAdvance {
                store.brand.cameraComfort = c
                seedStyles(for: c)
            }
        }
        .accessibilityIdentifier("onboard.comfort.\(idKey)")
    }

    private func seedStyles(for comfort: CameraComfort) {
        switch comfort {
        case .natural:
            if !store.brand.preferredStyles.contains(.talkingHead) {
                store.brand.preferredStyles.append(.talkingHead)
            }
        case .preferOff:
            if !store.brand.preferredStyles.contains(.faceless) {
                store.brand.preferredStyles.append(.faceless)
            }
        case .gettingThere:
            if !store.brand.preferredStyles.contains(.talkingHead) {
                store.brand.preferredStyles.append(.talkingHead)
            }
            if !store.brand.preferredStyles.contains(.faceless) {
                store.brand.preferredStyles.append(.faceless)
            }
        }
    }

    private var stylesStep: some View {
        @Bindable var store = store
        return scaffold("Pick your video styles", "Each gets its own kind of script.") {
            StyleSelectionView(selected: $store.brand.preferredStyles)
        } cta: {
            OnbPill(title: "Continue", enabled: !store.brand.preferredStyles.isEmpty) { advance() }
                .accessibilityIdentifier("onboard.stylesContinue")
        }
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
