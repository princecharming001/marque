import SwiftUI
import UIKit

// BitePal-style onboarding (Mobbin reference): centered bold titles, tall white choice
// cards with leading icon badges (SF Symbols ONLY — never Apple emojis), soft-green ring
// selection, compact centered dark pill CTAs ("Skip ›" flips to "Next ›" once something's
// picked), mascot interstitials with floating decorations, and a landing page with a giant
// centered headline surrounded by floating feature badges.
//
// Freeform text steps (name/niche/about/knownFor) keep the Gentler Streak pattern instead:
// big display type directly on the canvas, X-clear, circular arrow submit — left-aligned,
// while everything choice-based is centered.
//
// The mascot is a code-only placeholder (PlaceholderMascot) — swap for custom art later.
// Progress bar covers only the quiz portion (steps 5–19); intro screens are un-numbered.
//
//  0: landing           — WelcomeLanding ("Film once. Post every day." + floating badges)
//  1: mascotIntro       — "Hi, I'm Marque" + tagline
//  2: name              — "…and who are you?" — collects creatorName (freeform)
//  3: mascotReady       — "Let's get to know each other, {name}!"
//  4: featureExplainer  — one feature preview ("I learn what works for you")
//  5: goal              — "What are you here to do?"
//  6: platform          — "Where does your audience live?"
//  7: stage             — "Where's your audience today?"
//  8: frequency         — "How often do you post right now?"
//  9: methodInterstitial — "Consistency beats virality" (mascot scene)
// 10: blocker           — "What gets in the way most?"
// 11: niche             — freeform
// 12: whatYouDo         — freeform ×2
// 13: knownFor          — freeform
// 14: mirrorInterstitial — brand mirror sentence (mascot scene)
// 15: voice             — sliders
// 16: cameraComfort     — "How do you feel on camera?"
// 17: styles            — StyleSelectionView
// 18: pace              — "Pick your weekly pace"
// 19: connect           — ConnectAccountsView
// 20: aha                — scripts ready / finish

struct OnboardingView: View {
    @Environment(AppStore.self) private var store
    @State private var step = 0
    @State private var analyzing = false
    @State private var generating = false

    private let lastInputStep = 19
    private let quizStartStep = 5   // progress bar covers only the quiz portion, not the mascot intro

    var body: some View {
        if step == 0 {
            WelcomeLanding(
                start: { advance() },
                haveAccount: {
                    // Existing users skip the quiz entirely — straight to the account gate;
                    // their brand data restores after sign-in.
                    store.hasOnboarded = true
                    store.save()
                }
            )
        } else {
            inputFlow
        }
    }

    private var inputFlow: some View {
        ZStack {
            Palette.canvas.ignoresSafeArea()
            VStack(spacing: Space.lg) {
                if step >= 1 && step <= lastInputStep {
                    VStack(spacing: Space.sm) {
                        HStack {
                            if step >= 2 {
                                Button {
                                    withAnimation(Motion.enter) { step -= 1 }
                                } label: {
                                    Image(systemName: "chevron.left")
                                        .font(.system(size: 18, weight: .semibold))
                                        .foregroundStyle(Palette.textPrimary)
                                        .frame(width: 40, height: 40, alignment: .leading)
                                        .contentShape(Rectangle())
                                }
                                .accessibilityIdentifier("onboard.back")
                            }
                            Spacer()
                        }
                        // Intro screens (1–4) are un-numbered, like the reference —
                        // the bar only appears once the actual quiz starts.
                        if step >= quizStartStep {
                            OnboardProgress(total: lastInputStep - quizStartStep + 1,
                                           index: step - quizStartStep + 1)
                        }
                    }
                    .padding(.top, Space.sm)
                }
                Group {
                    switch step {
                    case 1:  mascotIntroStep()
                    case 2:  nameStep()
                    case 3:  mascotReadyStep()
                    case 4:  featureExplainerStep()
                    case 5:  goalStep()
                    case 6:  platformStep()
                    case 7:  stageStep()
                    case 8:  frequencyStep()
                    case 9:  methodInterstitialStep()
                    case 10: blockerStep()
                    case 11: nicheStep()
                    case 12: whatYouDoStep()
                    case 13: knownForStep()
                    case 14: mirrorInterstitialStep()
                    case 15: voiceStep()
                    case 16: cameraComfortStep()
                    case 17: styleStep()
                    case 18: paceStep()
                    case 19: connectStep()
                    default: ahaStep
                    }
                }
                .frame(maxHeight: .infinity)
            }
            .screenPadding()
        }
        .contentShape(Rectangle())
        .onTapGesture { hideKeyboard() }
    }

    private func hideKeyboard() {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }

    // MARK: - Mascot intro (steps 1–4, un-numbered — precedes the quiz)

    // Step 1: Mascot self-intro
    private func mascotIntroStep() -> some View {
        VStack(spacing: Space.sm) {
            Text("Hi, I\u{2019}m Marque")
                .font(AppFont.headline).foregroundStyle(Palette.textTertiary)
                .staggerReveal(0)
            Text("Your dedicated partner in building a content habit that actually sticks.")
                .font(AppFont.question).tracking(-0.6)
                .foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .staggerReveal(1)
            Spacer(minLength: Space.lg)
            MascotScene(decorations: .greeting)
            Spacer(minLength: Space.lg)
            CompactPill(title: "Hi, Marque") { advance() }
                .accessibilityIdentifier("onboard.mascotIntro.continue")
                .staggerReveal(2)
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, Space.lg)
    }

    // Step 2: Collect the creator's name — freeform (Gentler Streak), left-aligned
    @FocusState private var nameFieldFocused: Bool

    private func nameStep() -> some View {
        @Bindable var store = store
        let hasText = !(store.brand.creatorName ?? "").trimmingCharacters(in: .whitespaces).isEmpty
        return VStack(alignment: .leading, spacing: Space.lg) {
            Spacer()
            Text("\u{2026}and who are you?")
                .font(AppFont.question).tracking(-0.6).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .staggerReveal(0)
            Text("Enter the name you\u{2019}d like to go by.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                .staggerReveal(1)

            HStack(alignment: .center, spacing: Space.sm) {
                TextField("Name", text: Binding(
                    get: { store.brand.creatorName ?? "" },
                    set: { store.brand.creatorName = $0 }
                ))
                .font(Typeface.display(40, .semibold)).tracking(-0.6)
                .foregroundStyle(Palette.textPrimary)
                .tint(Palette.accent)
                .textInputAutocapitalization(.words)
                .focused($nameFieldFocused)
                .accessibilityIdentifier("onboard.creatorName")

                if hasText {
                    ClearFieldButton { withAnimation(Motion.quick) { store.brand.creatorName = "" } }
                }
            }
            .padding(.top, Space.md)
            .staggerReveal(2)

            Spacer()

            HStack {
                Spacer()
                FreeformArrow(enabled: hasText) {
                    nameFieldFocused = false
                    advance()
                }
                .accessibilityIdentifier("onboard.nameContinue")
            }
        }
        .animation(Motion.quick, value: hasText)
        .padding(.bottom, Space.lg)
        .onAppear { nameFieldFocused = true }
    }

    // Step 3: Mascot greets the creator by name
    private func mascotReadyStep() -> some View {
        let name = (store.brand.creatorName ?? "").trimmingCharacters(in: .whitespaces)
        let greeting = name.isEmpty ? "Let\u{2019}s get to know each other." : "Let\u{2019}s get to know each other, \(name)!"
        return VStack(spacing: Space.sm) {
            Text(greeting)
                .font(AppFont.question).tracking(-0.6)
                .foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .staggerReveal(0)
            Text("I\u{2019}ll start by explaining what I can do for you.")
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
                .staggerReveal(1)
            Spacer(minLength: Space.lg)
            MascotScene(decorations: .thinking)
            Spacer(minLength: Space.lg)
            CompactPill(title: "Go for it") { advance() }
                .accessibilityIdentifier("onboard.mascotReady.continue")
                .staggerReveal(2)
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, Space.lg)
    }

    // Step 4: One feature preview before the quiz starts
    private func featureExplainerStep() -> some View {
        VStack(spacing: Space.xl) {
            ZStack {
                RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
                    .fill(Palette.surfaceRaised)
                    .shadow(color: .black.opacity(0.05), radius: 16, x: 0, y: 6)
                VStack(spacing: Space.md) {
                    PulsingWaveformBadge()
                    Text("\u{201C}Talk to me every morning.\u{201D}")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                }
            }
            .frame(height: 200)
            .staggerReveal(0)

            VStack(spacing: Space.sm) {
                Text("I learn what works for you.")
                    .font(AppFont.question).tracking(-0.6)
                    .foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .staggerReveal(1)
                Text("Tell me your ideas, your angle, what\u{2019}s on your mind \u{2014} I remember it all and use it to write sharper scripts every day.")
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .lineSpacing(4)
                    .staggerReveal(2)
            }

            Spacer(minLength: Space.md)
            CompactPill(title: "Continue") { advance() }
                .accessibilityIdentifier("onboard.featureExplainer.continue")
                .staggerReveal(3)
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, Space.lg)
    }

    // MARK: - Quiz steps (BitePal card style)

    // Step 5: Goal (has a default → always "Next")
    private func goalStep() -> some View {
        @Bindable var store = store
        return QuizScaffold(question: "What are you here to do?") {
            VStack(spacing: Space.md) {
                iconCard(for: .audience, icon: "person.2.fill", tint: Palette.accent, store: store)
                iconCard(for: .clients, icon: "briefcase.fill", tint: Palette.ink, store: store)
                iconCard(for: .authority, icon: "crown.fill", tint: Palette.warning, store: store)
                iconCard(for: .monetize, icon: "dollarsign.circle.fill", tint: Palette.positive, store: store)
            }
        } cta: {
            CompactPill(title: "Next", chevron: true) { advance() }
        }
    }

    private func iconCard(for goal: Goal, icon: String, tint: Color, store: AppStore) -> some View {
        Button { store.brand.goal = goal } label: {
            IconChoiceCard(icon: icon, tint: tint, text: goal.rawValue, selected: store.brand.goal == goal)
        }
        .buttonStyle(.plain)
    }

    // Step 6: Platform
    // platformBothChosen disambiguates "nil because unset" from "nil because user picked Both"
    @State private var platformBothChosen = false

    private func platformStep() -> some View {
        @Bindable var store = store
        return QuizScaffold(question: "Where does your audience live?") {
            VStack(spacing: Space.md) {
                Button {
                    store.brand.primaryPlatform = .instagram
                    platformBothChosen = false
                } label: {
                    IconChoiceCard(icon: "camera.fill", tint: Color(hex: 0xE1306C),
                                   text: "Instagram", selected: store.brand.primaryPlatform == .instagram)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.platform.instagram")

                Button {
                    store.brand.primaryPlatform = .tiktok
                    platformBothChosen = false
                } label: {
                    IconChoiceCard(icon: "music.note", tint: Palette.ink,
                                   text: "TikTok", selected: store.brand.primaryPlatform == .tiktok)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.platform.tiktok")

                Button {
                    store.brand.primaryPlatform = nil  // nil = "Both" — no single primary
                    platformBothChosen = true
                } label: {
                    IconChoiceCard(icon: "square.filled.on.square", tint: Palette.accent,
                                   text: "Both", selected: platformBothChosen)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.platform.both")
            }
        } cta: {
            SkipNextPill(hasSelection: store.brand.primaryPlatform != nil || platformBothChosen) { advance() }
        }
    }

    // Step 7: Stage
    private func stageStep() -> some View {
        @Bindable var store = store
        let icons: [CreatorStage: (String, Color)] = [
            .nano: ("leaf.fill", Palette.positive),
            .micro: ("chart.bar.fill", Palette.accent),
            .established: ("chart.line.uptrend.xyaxis", Palette.warning),
            .pro: ("crown.fill", Palette.ink),
        ]
        return QuizScaffold(question: "Where\u{2019}s your audience today?") {
            VStack(spacing: Space.md) {
                ForEach(CreatorStage.allCases) { s in
                    Button { store.brand.stage = s } label: {
                        IconChoiceCard(icon: icons[s]?.0 ?? "circle", tint: icons[s]?.1 ?? Palette.accent,
                                       text: s.rawValue, selected: store.brand.stage == s)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier(stageAccessID(s))
                }
            }
        } cta: {
            SkipNextPill(hasSelection: store.brand.stage != nil) { advance() }
        }
    }

    private func stageAccessID(_ s: CreatorStage) -> String {
        switch s {
        case .nano:        return "onboard.stage.nano"
        case .micro:       return "onboard.stage.micro"
        case .established: return "onboard.stage.established"
        case .pro:         return "onboard.stage.pro"
        }
    }

    // Step 8: Frequency
    private func frequencyStep() -> some View {
        @Bindable var store = store
        let icons: [PostingFrequency: (String, Color)] = [
            .rarely: ("tortoise.fill", Palette.textSecondary),
            .sometimes: ("figure.walk", Palette.accent),
            .often: ("hare.fill", Palette.warning),
            .daily: ("flame.fill", Palette.critical),
        ]
        return QuizScaffold(question: "How often do you post right now?") {
            VStack(spacing: Space.md) {
                ForEach(PostingFrequency.allCases) { f in
                    Button { store.brand.postingFrequency = f } label: {
                        IconChoiceCard(icon: icons[f]?.0 ?? "circle", tint: icons[f]?.1 ?? Palette.accent,
                                       text: f.rawValue, selected: store.brand.postingFrequency == f)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier(frequencyAccessID(f))
                }
            }
        } cta: {
            SkipNextPill(hasSelection: store.brand.postingFrequency != nil) { advance() }
        }
    }

    private func frequencyAccessID(_ f: PostingFrequency) -> String {
        switch f {
        case .rarely:    return "onboard.frequency.rarely"
        case .sometimes: return "onboard.frequency.sometimes"
        case .often:     return "onboard.frequency.often"
        case .daily:     return "onboard.frequency.daily"
        }
    }

    // Step 9: Method interstitial — mascot scene
    private func methodInterstitialStep() -> some View {
        MascotInterstitial(
            title: "Consistency beats virality",
            message: "Most creators burn out chasing hits. Marque helps you build a posting habit first \u{2014} then the algorithm rewards you for it.",
            decorations: .habit,
            ctaTitle: "Let\u{2019}s go",
            onContinue: { advance() }
        )
    }

    // Step 10: Blocker — SF-symbol badges, never emojis
    private func blockerStep() -> some View {
        @Bindable var store = store
        let icons: [CreatorBlocker: (String, Color)] = [
            .ideas: ("lightbulb.fill", Palette.warning),
            .time: ("clock.fill", Palette.accent),
            .editing: ("scissors", Palette.critical),
            .confidence: ("face.dashed", Palette.positive),
        ]
        return QuizScaffold(question: "What gets in the way most?") {
            VStack(spacing: Space.md) {
                ForEach(CreatorBlocker.allCases) { b in
                    Button { store.brand.biggestBlocker = b } label: {
                        IconChoiceCard(icon: icons[b]?.0 ?? "circle", tint: icons[b]?.1 ?? Palette.accent,
                                       text: b.rawValue, selected: store.brand.biggestBlocker == b)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier(blockerAccessID(b))
                }
            }
        } cta: {
            SkipNextPill(hasSelection: store.brand.biggestBlocker != nil) { advance() }
        }
    }

    private func blockerAccessID(_ b: CreatorBlocker) -> String {
        switch b {
        case .ideas:      return "onboard.blocker.ideas"
        case .time:       return "onboard.blocker.time"
        case .editing:    return "onboard.blocker.editing"
        case .confidence: return "onboard.blocker.confidence"
        }
    }

    // MARK: - Freeform text steps (Gentler Streak style)

    // Step 11: Niche
    @FocusState private var nicheFocused: Bool

    private func nicheStep() -> some View {
        @Bindable var store = store
        let hasText = !store.brand.niche.trimmingCharacters(in: .whitespaces).isEmpty
        return VStack(alignment: .leading, spacing: Space.lg) {
            Spacer()
            Text("What\u{2019}s your niche?")
                .font(AppFont.question).tracking(-0.6).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .staggerReveal(0)
            Text("Fitness, personal finance, cooking\u{2026} whatever you make content about.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                .staggerReveal(1)

            HStack(alignment: .center, spacing: Space.sm) {
                TextField("Your niche", text: $store.brand.niche)
                    .font(Typeface.display(34, .semibold)).tracking(-0.6)
                    .foregroundStyle(Palette.textPrimary)
                    .tint(Palette.accent)
                    .focused($nicheFocused)
                    .accessibilityIdentifier("onboard.niche")
                if hasText {
                    ClearFieldButton { withAnimation(Motion.quick) { store.brand.niche = "" } }
                }
            }
            .padding(.top, Space.md)
            .staggerReveal(2)

            Spacer()

            HStack {
                Spacer()
                FreeformArrow(enabled: hasText) {
                    nicheFocused = false
                    advance()
                }
                .accessibilityIdentifier("onboard.nicheContinue")
            }
        }
        .animation(Motion.quick, value: hasText)
        .padding(.bottom, Space.lg)
        .onAppear { nicheFocused = true }
    }

    // Step 12: What you do + audience
    @FocusState private var aboutFocused: Bool

    private func whatYouDoStep() -> some View {
        @Bindable var store = store
        let hasText = !store.brand.whatYouDo.trimmingCharacters(in: .whitespaces).isEmpty
        return VStack(alignment: .leading, spacing: Space.lg) {
            Spacer()
            Text("Tell me about you")
                .font(AppFont.question).tracking(-0.6).foregroundStyle(Palette.textPrimary)
                .staggerReveal(0)

            VStack(alignment: .leading, spacing: Space.xl) {
                TextField("What you do", text: $store.brand.whatYouDo)
                    .font(Typeface.display(26, .semibold)).tracking(-0.4)
                    .foregroundStyle(Palette.textPrimary)
                    .tint(Palette.accent)
                    .focused($aboutFocused)
                    .accessibilityIdentifier("onboard.whatYouDo")
                MarqueHairline()
                TextField("Who you serve", text: $store.brand.audience)
                    .font(Typeface.display(26, .semibold)).tracking(-0.4)
                    .foregroundStyle(Palette.textPrimary)
                    .tint(Palette.accent)
                    .accessibilityIdentifier("onboard.audience")
            }
            .padding(.top, Space.md)
            .staggerReveal(1)

            Spacer()

            HStack {
                Spacer()
                FreeformArrow(enabled: hasText) {
                    aboutFocused = false
                    advance()
                }
                .accessibilityIdentifier("onboard.aboutContinue")
            }
        }
        .animation(Motion.quick, value: hasText)
        .padding(.bottom, Space.lg)
        .onAppear { aboutFocused = true }
    }

    // Step 13: Known for
    @FocusState private var knownForFocused: Bool

    private func knownForStep() -> some View {
        @Bindable var store = store
        let hasText = !store.brand.knownFor.trimmingCharacters(in: .whitespaces).isEmpty
        return VStack(alignment: .leading, spacing: Space.lg) {
            Spacer()
            Text("What do you want to be known for?")
                .font(AppFont.question).tracking(-0.6).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .staggerReveal(0)
            Text("This is the heart of your brand. Everything we write points back here.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                .staggerReveal(1)

            HStack(alignment: .center, spacing: Space.sm) {
                TextField("In a sentence\u{2026}", text: $store.brand.knownFor, axis: .vertical)
                    .font(Typeface.display(28, .semibold)).tracking(-0.4)
                    .foregroundStyle(Palette.textPrimary)
                    .tint(Palette.accent)
                    .lineLimit(1...3)
                    .focused($knownForFocused)
                    .accessibilityIdentifier("onboard.knownFor")
                if hasText {
                    ClearFieldButton { withAnimation(Motion.quick) { store.brand.knownFor = "" } }
                }
            }
            .padding(.top, Space.md)
            .staggerReveal(2)

            Spacer()

            HStack {
                Button("Skip \u{2014} I\u{2019}ll add this later") {
                    knownForFocused = false
                    advance()
                }
                .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                .accessibilityIdentifier("onboard.knownForSkip")
                Spacer()
                FreeformArrow(enabled: hasText) {
                    knownForFocused = false
                    advance()
                }
                .accessibilityIdentifier("onboard.knownForContinue")
            }
        }
        .animation(Motion.quick, value: hasText)
        .padding(.bottom, Space.lg)
        .onAppear { knownForFocused = true }
    }

    // Step 14: Mirror interstitial — mascot scene with the brand sentence
    private func mirrorInterstitialStep() -> some View {
        let niche     = store.brand.niche.isEmpty    ? "your niche" : store.brand.niche
        let audience  = store.brand.audience.isEmpty ? "your audience" : store.brand.audience
        let knownFor  = store.brand.knownFor.isEmpty ? "what you stand for" : store.brand.knownFor
        let msg = "You\u{2019}re a \(niche) creator for \(audience), known for \(knownFor). Every script I write points back to this."
        return MascotInterstitial(
            title: "Your brand, in a sentence",
            message: msg,
            decorations: .proud,
            ctaTitle: "That\u{2019}s me",
            onContinue: { advance() }
        )
    }

    // Step 15: Voice
    private func voiceStep() -> some View {
        @Bindable var store = store
        return QuizScaffold(
            question: "What\u{2019}s your voice like?",
            note: "Marque writes in your register \u{2014} tune these to match how you actually talk."
        ) {
            VStack(spacing: Space.lg) {
                voiceSliderRow("Funny", "Serious", value: $store.brand.voice.funnyToSerious)
                MarqueHairline()
                voiceSliderRow("Polished", "Raw", value: $store.brand.voice.polishedToRaw)
                MarqueHairline()
                voiceSliderRow("Teacher", "Peer", value: $store.brand.voice.teacherToPeer)
            }
            .marqueCard()

            Text(voicePreviewLine(store: store))
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, Space.sm)
                .padding(.top, Space.md)
        } cta: {
            CompactPill(title: "Next", chevron: true) { advance() }
        }
    }

    private func voicePreviewLine(store: AppStore) -> String {
        let funny   = store.brand.voice.funnyToSerious
        let polished = store.brand.voice.polishedToRaw
        let teacher  = store.brand.voice.teacherToPeer
        let tone  = funny    < 0.35 ? "witty and light"       : funny    > 0.65 ? "grounded and serious" : "balanced"
        let style = polished < 0.35 ? "clean and produced"    : polished > 0.65 ? "unfiltered and real"  : "conversational"
        let mode  = teacher  < 0.35 ? "teaching the room"     : teacher  > 0.65 ? "talking to peers"     : "guiding alongside"
        return "\u{201C}\(tone.capitalized), \(style), \(mode).\u{201D} Marque will write every script in this voice."
    }

    private func voiceSliderRow(_ leading: String, _ trailing: String, value: Binding<Double>) -> some View {
        VStack(spacing: Space.xs) {
            HStack {
                Text(leading)
                    .font(AppFont.callout)
                    .foregroundStyle(value.wrappedValue < 0.4 ? Palette.accent : Palette.textTertiary)
                Spacer()
                Text(trailing)
                    .font(AppFont.callout)
                    .foregroundStyle(value.wrappedValue > 0.6 ? Palette.accent : Palette.textTertiary)
            }
            Slider(value: value).tint(Palette.accent)
        }
    }

    // Step 16: Camera comfort
    private func cameraComfortStep() -> some View {
        @Bindable var store = store
        return QuizScaffold(question: "How do you feel on camera?") {
            VStack(spacing: Space.md) {
                Button {
                    store.brand.cameraComfort = .natural
                    seedStyles(for: .natural, store: store)
                } label: {
                    IconChoiceCard(icon: "video.fill", tint: Palette.positive,
                                   text: CameraComfort.natural.rawValue,
                                   selected: store.brand.cameraComfort == .natural)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.comfort.natural")

                Button {
                    store.brand.cameraComfort = .gettingThere
                    seedStyles(for: .gettingThere, store: store)
                } label: {
                    IconChoiceCard(icon: "video", tint: Palette.accent,
                                   text: CameraComfort.gettingThere.rawValue,
                                   selected: store.brand.cameraComfort == .gettingThere)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.comfort.getting")

                Button {
                    store.brand.cameraComfort = .preferOff
                    seedStyles(for: .preferOff, store: store)
                } label: {
                    IconChoiceCard(icon: "video.slash.fill", tint: Palette.textSecondary,
                                   text: CameraComfort.preferOff.rawValue,
                                   selected: store.brand.cameraComfort == .preferOff)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.comfort.off")
            }
        } cta: {
            SkipNextPill(hasSelection: store.brand.cameraComfort != nil) { advance() }
        }
    }

    private func seedStyles(for comfort: CameraComfort, store: AppStore) {
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

    // Step 17: Styles
    private func styleStep() -> some View {
        @Bindable var store = store
        return QuizScaffold(
            question: "What kind of videos?",
            note: "Pick the styles you want to make. Each gets its own kind of script."
        ) {
            StyleSelectionView(selected: $store.brand.preferredStyles)
        } cta: {
            CompactPill(title: "Next", chevron: true, enabled: !store.brand.preferredStyles.isEmpty) { advance() }
        }
    }

    // Step 18: Pace
    private func paceStep() -> some View {
        @Bindable var store = store
        return QuizScaffold(question: "Pick your weekly pace") {
            VStack(spacing: Space.md) {
                Button { store.brand.weeklyTarget = 3 } label: {
                    IconChoiceCard(icon: "3.circle.fill", tint: Palette.accent,
                                   text: "3 posts/week", sub: "~20 min filming",
                                   selected: store.brand.weeklyTarget == 3)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.pace.3")

                Button { store.brand.weeklyTarget = 5 } label: {
                    IconChoiceCard(icon: "5.circle.fill", tint: Palette.warning,
                                   text: "5 posts/week", sub: "~35 min filming",
                                   selected: store.brand.weeklyTarget == 5)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.pace.5")

                Button { store.brand.weeklyTarget = 7 } label: {
                    IconChoiceCard(icon: "7.circle.fill", tint: Palette.critical,
                                   text: "7 posts/week", sub: "~50 min filming",
                                   selected: store.brand.weeklyTarget == 7)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.pace.7")
            }
        } cta: {
            SkipNextPill(hasSelection: store.brand.weeklyTarget != nil) { advance() }
        }
    }

    // Step 19: Connect
    private func connectStep() -> some View {
        @Bindable var store = store
        return QuizScaffold(
            question: "Connect your accounts",
            note: "Link your Instagram and TikTok so Marque learns from what already works."
        ) {
            ConnectAccountsView()
            VStack(spacing: Space.md) {
                Button("Teach Marque your voice instead") {
                    store.showVoiceOnboarding = true
                }
                .font(AppFont.callout).foregroundStyle(Palette.accent)
                .accessibilityIdentifier("onboard.voiceInstead")
                Button("Skip for now") { store.derivePillars(); advance() }
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
            }
            .padding(.top, Space.md)
        } cta: {
            CompactPill(title: analyzing ? "Reading your page\u{2026}" : "Next",
                        chevron: !analyzing, enabled: !analyzing) {
                analyzing = true
                Task { await store.analyzePage(); analyzing = false; advance() }
            }
        }
        .sheet(isPresented: $store.showVoiceOnboarding) {
            VoiceOnboardingSheet { advance() }
        }
    }

    // Step 20: Aha
    private var ahaStep: some View {
        VStack(spacing: Space.lg) {
            if generating {
                Spacer()
                MascotScene(decorations: .thinking, mascotSize: 140)
                Text("Writing your first scripts\u{2026}")
                    .font(AppFont.question).tracking(-0.6).foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
                Text("In your voice. Built to stop the scroll.")
                    .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                ProgressView().tint(Palette.ink).padding(.top, Space.sm)
                Spacer()
                Color.clear.frame(height: 1).onAppear {
                    Task { await store.generateStarterScripts(); generating = false }
                }
            } else {
                Spacer()
                Text("Your first 3 scripts are ready")
                    .font(AppFont.question).tracking(-0.6).foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Record when you\u{2019}ve got a few minutes \u{2014} I\u{2019}ll do the editing.")
                    .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
                VStack(alignment: .leading, spacing: Space.md) {
                    ForEach(store.scripts.prefix(3)) { s in
                        HStack(alignment: .top, spacing: Space.sm) {
                            Image(systemName: "checkmark.circle.fill").foregroundStyle(Palette.positive)
                            Text(s.hook.text).font(AppFont.body).foregroundStyle(Palette.textPrimary).lineLimit(2)
                        }
                    }
                }
                .marqueCard()
                Spacer()
                CompactPill(title: "Enter Marque") { store.completeOnboarding() }
                    .accessibilityIdentifier("onboard.finish")
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, Space.lg)
    }

    // MARK: - Navigation

    private func advance() {
        if step == lastInputStep {
            generating = true
            step += 1
        } else {
            withAnimation(Motion.enter) { step += 1 }
        }
    }
}

// MARK: - Landing (BitePal style: giant centered headline + floating badges)

// Landing background studied from palo.ai (Playwright): near-black base, a subtle
// scattered star/particle field, faint constellation lines connecting floating nodes.
// Ported into Marque's warm palette (charcoal instead of cold black) rather than a
// literal copy — a deliberate dramatic dark "cold open" before the light quiz, echoing
// the app's original photographic hero.
private struct WelcomeLanding: View {
    let start: () -> Void
    let haveAccount: () -> Void

    // Relative node positions (unit space) shared by the connector lines and the badges
    // so the constellation lines always terminate exactly at each badge's center.
    private let nodes: [(x: CGFloat, y: CGFloat)] = [
        (0.22, 0.13), (0.8, 0.16), (0.12, 0.42), (0.87, 0.45),
    ]
    private let links: [(Int, Int)] = [(0, 2), (1, 3), (0, 1)]

    var body: some View {
        ZStack {
            Color(hex: 0x14120F).ignoresSafeArea()
            LinearGradient(
                colors: [Palette.accent.opacity(0.22), .clear, .clear],
                startPoint: .top, endPoint: .bottom
            )
            .ignoresSafeArea()
            StarField(count: 90).ignoresSafeArea().opacity(0.8)

            GeometryReader { geo in
                let w = geo.size.width
                let h = geo.size.height

                ConstellationLines(nodes: nodes.map { CGPoint(x: $0.x * w, y: $0.y * h) }, links: links)

                FloatingDecor(phase: 0) {
                    // ScoreBadge's canvas grays disappear on the dark frosted chip —
                    // inline a white-on-dark rendering for this one-off dark scene.
                    DarkLandingBadge {
                        HStack(spacing: 4) {
                            Image(systemName: "sparkle")
                                .font(.system(size: 8, weight: .semibold))
                                .foregroundStyle(Palette.accent)
                            Text("82").font(AppFont.caption).foregroundStyle(.white)
                            Text("est.").font(AppFont.micro).foregroundStyle(.white.opacity(0.55))
                        }
                        .padding(.horizontal, 6).padding(.vertical, 4)
                    }
                }
                .position(x: w * nodes[0].x, y: h * nodes[0].y)

                FloatingDecor(phase: 0.6) {
                    ZStack(alignment: .topTrailing) {
                        DarkLandingBadge { PlaceholderMascot(size: 64) }
                        Image(systemName: "heart.fill")
                            .font(.system(size: 16))
                            .foregroundStyle(Color(hex: 0xF08080))
                            .offset(x: 10, y: -12)
                        Image(systemName: "heart.fill")
                            .font(.system(size: 10))
                            .foregroundStyle(Color(hex: 0xF08080).opacity(0.7))
                            .offset(x: 24, y: 2)
                    }
                }
                .position(x: w * nodes[1].x, y: h * nodes[1].y)

                FloatingDecor(phase: 1.1) {
                    DarkLandingBadge {
                        Image(systemName: "video.fill")
                            .font(.system(size: 20, weight: .semibold))
                            .foregroundStyle(Palette.accent)
                            .frame(width: 56, height: 56)
                            .background(Circle().fill(Palette.accent.opacity(0.18)))
                    }
                }
                .position(x: w * nodes[2].x, y: h * nodes[2].y)

                FloatingDecor(phase: 1.7) {
                    DarkLandingBadge {
                        ZStack {
                            Circle()
                                .stroke(Palette.positive.opacity(0.25), lineWidth: 5)
                            Circle()
                                .trim(from: 0, to: 0.7)
                                .stroke(Palette.positive, style: StrokeStyle(lineWidth: 5, lineCap: .round))
                                .rotationEffect(.degrees(-90))
                            Text("5/7")
                                .font(AppFont.micro).foregroundStyle(.white.opacity(0.85))
                        }
                        .frame(width: 52, height: 52)
                        .padding(6)
                    }
                }
                .position(x: w * nodes[3].x, y: h * nodes[3].y)
            }
            .allowsHitTesting(false)

            VStack(spacing: 0) {
                Spacer()
                Text("Film once.\nPost every\nday.")
                    .font(Typeface.sans(58, .bold)).tracking(-1.5)
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.center)
                    .lineSpacing(0)
                    .fixedSize(horizontal: false, vertical: true)
                    .staggerReveal(0, distance: 20)
                Spacer()
                VStack(spacing: Space.lg) {
                    Button(action: start) {
                        Text("Get started").font(AppFont.headline)
                            .foregroundStyle(.white)
                            .frame(height: 56).padding(.horizontal, 64)
                            .background(Palette.accent)
                            .clipShape(Capsule())
                            .shadow(color: Palette.accent.opacity(0.4), radius: 20, y: 8)
                    }
                    .buttonStyle(PressableStyle())
                    .accessibilityIdentifier("onboard.start")

                    Button(action: haveAccount) {
                        Text("I already have an account")
                            .font(AppFont.headline).foregroundStyle(.white.opacity(0.85))
                    }
                    .accessibilityIdentifier("onboard.haveAccount")

                    HStack(spacing: 4) {
                        Text("By continuing you\u{2019}re accepting our")
                            .foregroundStyle(.white.opacity(0.4))
                        Link("Terms", destination: LegalURLs.terms)
                            .foregroundStyle(.white.opacity(0.65))
                        Text("and").foregroundStyle(.white.opacity(0.4))
                        Link("Privacy Notice", destination: LegalURLs.privacy)
                            .foregroundStyle(.white.opacity(0.65))
                    }
                    .font(AppFont.caption)
                }
                .staggerReveal(1, distance: 20)
                .padding(.bottom, Space.xl)
            }
            .screenPadding()
        }
        .preferredColorScheme(.dark)
    }
}

// A frosted dark holder — the palo-style node badges, reskinned for Marque's palette.
private struct DarkLandingBadge<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        content
            .padding(8)
            .background(.ultraThinMaterial.opacity(0.9), in: RoundedRectangle(cornerRadius: Radius.pill, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.pill, style: .continuous)
                .strokeBorder(.white.opacity(0.12), lineWidth: 1))
            .shadow(color: .black.opacity(0.35), radius: 16, x: 0, y: 8)
    }
}

// Faint scattered dot/particle texture — positions randomized once on appear (not
// per-redraw, so the field doesn't flicker) and drawn via Canvas for cheap rendering
// of ~100 dots.
private struct StarField: View {
    let count: Int
    @State private var dots: [(x: CGFloat, y: CGFloat, r: CGFloat, o: Double)] = []

    var body: some View {
        Canvas { context, size in
            for d in dots {
                let rect = CGRect(x: d.x * size.width, y: d.y * size.height, width: d.r, height: d.r)
                context.fill(Path(ellipseIn: rect), with: .color(.white.opacity(d.o)))
            }
        }
        .onAppear {
            guard dots.isEmpty else { return }
            dots = (0..<count).map { _ in
                (CGFloat.random(in: 0...1), CGFloat.random(in: 0...1),
                 CGFloat.random(in: 1...2.4), Double.random(in: 0.1...0.5))
            }
        }
    }
}

// Faint straight lines connecting a few badge nodes — the palo "constellation" motif.
private struct ConstellationLines: View {
    let nodes: [CGPoint]
    let links: [(Int, Int)]
    var body: some View {
        Canvas { context, _ in
            for (a, b) in links where a < nodes.count && b < nodes.count {
                var path = Path()
                path.move(to: nodes[a])
                path.addLine(to: nodes[b])
                context.stroke(path, with: .color(.white.opacity(0.08)), lineWidth: 1)
            }
        }
    }
}

// Gentle vertical bobbing for decorative elements; phase staggers instances apart.
private struct FloatingDecor<Content: View>: View {
    let phase: Double
    @ViewBuilder let content: Content
    @State private var up = false
    var body: some View {
        content
            .offset(y: up ? -7 : 7)
            .onAppear {
                withAnimation(.easeInOut(duration: 2.6).repeatForever(autoreverses: true).delay(phase)) {
                    up = true
                }
            }
    }
}

// MARK: - Mascot scenes (BitePal raccoon-style interstitials)

// What floats around the mascot — all SF Symbols and drawn shapes, never emojis.
private enum MascotDecorations {
    case greeting   // sparkles + hearts
    case thinking   // thought clouds with idea symbols + question glyphs
    case habit      // calendar + upward trend in thought clouds
    case proud      // quote bubble + sparkles
}

private struct MascotScene: View {
    let decorations: MascotDecorations
    var mascotSize: CGFloat = 170

    var body: some View {
        ZStack {
            switch decorations {
            case .greeting:
                FloatingDecor(phase: 0) {
                    Image(systemName: "sparkles")
                        .font(.system(size: 26)).foregroundStyle(Palette.warning)
                }
                .offset(x: -mascotSize * 0.72, y: -mascotSize * 0.38)
                FloatingDecor(phase: 0.8) {
                    Image(systemName: "heart.fill")
                        .font(.system(size: 20)).foregroundStyle(Color(hex: 0xF08080))
                }
                .offset(x: mascotSize * 0.72, y: -mascotSize * 0.46)
                FloatingDecor(phase: 1.4) {
                    Image(systemName: "sparkle")
                        .font(.system(size: 16)).foregroundStyle(Palette.accent)
                }
                .offset(x: mascotSize * 0.62, y: mascotSize * 0.3)

            case .thinking:
                FloatingDecor(phase: 0.2) {
                    ThoughtCloud { Image(systemName: "lightbulb.fill")
                        .font(.system(size: 20)).foregroundStyle(Palette.warning) }
                }
                .offset(x: -mascotSize * 0.6, y: -mascotSize * 0.52)
                FloatingDecor(phase: 0.9) {
                    ThoughtCloud { Image(systemName: "video.fill")
                        .font(.system(size: 20)).foregroundStyle(Palette.accent) }
                }
                .offset(x: mascotSize * 0.55, y: -mascotSize * 0.62)
                questionGlyph(size: 30, color: Palette.accent)
                    .offset(x: -mascotSize * 0.78, y: mascotSize * 0.1)
                questionGlyph(size: 22, color: Color(hex: 0x5AC8B0))
                    .offset(x: mascotSize * 0.8, y: -mascotSize * 0.05)

            case .habit:
                FloatingDecor(phase: 0.2) {
                    ThoughtCloud { Image(systemName: "calendar")
                        .font(.system(size: 20)).foregroundStyle(Palette.accent) }
                }
                .offset(x: -mascotSize * 0.6, y: -mascotSize * 0.55)
                FloatingDecor(phase: 0.9) {
                    ThoughtCloud { Image(systemName: "chart.line.uptrend.xyaxis")
                        .font(.system(size: 20)).foregroundStyle(Palette.positive) }
                }
                .offset(x: mascotSize * 0.58, y: -mascotSize * 0.6)
                FloatingDecor(phase: 1.5) {
                    Image(systemName: "flame.fill")
                        .font(.system(size: 22)).foregroundStyle(Palette.critical)
                }
                .offset(x: mascotSize * 0.75, y: mascotSize * 0.25)

            case .proud:
                FloatingDecor(phase: 0.2) {
                    ThoughtCloud { Image(systemName: "quote.opening")
                        .font(.system(size: 18)).foregroundStyle(Palette.ink) }
                }
                .offset(x: -mascotSize * 0.58, y: -mascotSize * 0.56)
                FloatingDecor(phase: 0.9) {
                    Image(systemName: "sparkles")
                        .font(.system(size: 26)).foregroundStyle(Palette.warning)
                }
                .offset(x: mascotSize * 0.7, y: -mascotSize * 0.35)
                FloatingDecor(phase: 1.5) {
                    Image(systemName: "checkmark.seal.fill")
                        .font(.system(size: 22)).foregroundStyle(Palette.positive)
                }
                .offset(x: mascotSize * 0.72, y: mascotSize * 0.28)
            }

            PlaceholderMascot(size: mascotSize)
        }
        .frame(height: mascotSize * 1.7)
        .accessibilityHidden(true)
    }

    private func questionGlyph(size: CGFloat, color: Color) -> some View {
        FloatingDecor(phase: Double(size) * 0.05) {
            Text("?")
                .font(Typeface.sans(size, .bold))
                .foregroundStyle(color)
                .rotationEffect(.degrees(size.truncatingRemainder(dividingBy: 2) == 0 ? 12 : -10))
        }
    }
}

// A drawn thought-bubble cloud (overlapping white circles) holding one symbol.
private struct ThoughtCloud<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        ZStack {
            Circle().fill(Palette.surfaceRaised).frame(width: 34, height: 34).offset(x: -20, y: 8)
            Circle().fill(Palette.surfaceRaised).frame(width: 40, height: 40).offset(x: 18, y: 6)
            Circle().fill(Palette.surfaceRaised).frame(width: 52, height: 52)
            content
        }
        .compositingGroup()
        .shadow(color: Palette.shadowWarm.opacity(0.12), radius: 10, x: 0, y: 5)
    }
}

// Full interstitial screen: centered title, message, mascot scene, compact CTA.
private struct MascotInterstitial: View {
    let title: String
    let message: String
    let decorations: MascotDecorations
    let ctaTitle: String
    let onContinue: () -> Void

    var body: some View {
        VStack(spacing: Space.sm) {
            Text(title)
                .font(AppFont.question).tracking(-0.6)
                .foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .staggerReveal(0)
            Text(message)
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .staggerReveal(1)
            Spacer(minLength: Space.lg)
            MascotScene(decorations: decorations)
            Spacer(minLength: Space.lg)
            CompactPill(title: ctaTitle, action: onContinue)
                .accessibilityIdentifier("onboard.continue")
                .staggerReveal(2)
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, Space.lg)
    }
}

// MARK: - Quiz scaffold (centered title, content, pinned compact CTA)

private struct QuizScaffold<Content: View, CTA: View>: View {
    let question: String
    var note: String? = nil
    @ViewBuilder let content: Content
    @ViewBuilder let cta: CTA

    var body: some View {
        VStack(spacing: Space.lg) {
            VStack(spacing: Space.sm) {
                Text(question)
                    .font(AppFont.question).tracking(-0.6)
                    .foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                if let note {
                    Text(note)
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.center)
                }
            }
            .padding(.top, Space.md)
            .staggerReveal(0)

            content
                .staggerReveal(1)

            Spacer(minLength: Space.md)

            cta
                .staggerReveal(2)
        }
        .frame(maxWidth: .infinity)
        .padding(.bottom, Space.lg)
    }
}

// MARK: - BitePal choice card: leading icon badge, tall white card, green-ring selection

private struct IconChoiceCard: View {
    let icon: String
    let tint: Color
    let text: String
    var sub: String? = nil
    let selected: Bool

    var body: some View {
        HStack(spacing: Space.md) {
            Image(systemName: icon)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(tint)
                .frame(width: 42, height: 42)
                .background(Circle().fill(tint.opacity(0.12)))
            VStack(alignment: .leading, spacing: 2) {
                Text(text).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                if let sub {
                    Text(sub).font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                }
            }
            Spacer()
        }
        .padding(.horizontal, Space.lg)
        .frame(height: 72)
        .background(selected ? Palette.positive.opacity(0.10) : Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
            .strokeBorder(selected ? Palette.positive : Color.clear, lineWidth: 2))
        .shadow(color: .black.opacity(selected ? 0.02 : 0.06), radius: 14, x: 0, y: 5)
        .scaleEffect(selected ? 1.015 : 1.0)
        .animation(Motion.spring, value: selected)
    }
}

// MARK: - CTAs

// Compact centered dark pill (BitePal), hugging its label.
private struct CompactPill: View {
    let title: String
    var chevron: Bool = false
    var enabled: Bool = true
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.sm) {
                Text(title).font(AppFont.headline)
                if chevron {
                    Image(systemName: "chevron.right").font(.system(size: 13, weight: .bold))
                }
            }
            .foregroundStyle(enabled ? Palette.onInk : Color(hex: 0xA4A29D))
            .frame(height: 56).padding(.horizontal, 44)
            .background(enabled ? Palette.ink : Color(hex: 0xDAD9D6))
            .clipShape(Capsule())
            .shadow(color: .black.opacity(enabled ? 0.15 : 0), radius: 14, y: 6)
        }
        .buttonStyle(PressableStyle())
        .disabled(!enabled)
    }
}

// The optional-step CTA: label flips "Skip ›" → "Next ›" once something's picked.
// Always tappable — these questions are skippable; the label just signals intent.
private struct SkipNextPill: View {
    let hasSelection: Bool
    let action: () -> Void
    var body: some View {
        CompactPill(title: hasSelection ? "Next" : "Skip", chevron: true, action: action)
            .animation(Motion.quick, value: hasSelection)
    }
}

// Circular arrow submit for freeform text steps (Gentler Streak).
private struct FreeformArrow: View {
    let enabled: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: "arrow.right")
                .font(.system(size: 20, weight: .semibold))
                .foregroundStyle(enabled ? Palette.onInk : Color(hex: 0xA4A29D))
                .frame(width: 56, height: 56)
                .background(Circle().fill(enabled ? Palette.ink : Color(hex: 0xDAD9D6)))
        }
        .buttonStyle(PressableStyle())
        .disabled(!enabled)
        .scaleEffect(enabled ? 1 : 0.85)
        .animation(Motion.spring, value: enabled)
    }
}

// Inline X-clear for freeform fields.
private struct ClearFieldButton: View {
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: "xmark")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Palette.textSecondary)
                .frame(width: 30, height: 30)
                .background(Circle().fill(Palette.surfaceSunken))
        }
        .buttonStyle(PressableStyle())
        .transition(.scale.combined(with: .opacity))
    }
}

// MARK: - Mascot + badges

// A gently pulsing "listening" ring behind the waveform icon on the feature-explainer
// screen — cheap, apt personality for the "talk to me" moment.
private struct PulsingWaveformBadge: View {
    @State private var pulsing = false
    var body: some View {
        ZStack {
            Circle()
                .fill(Palette.accent.opacity(0.10))
                .frame(width: 88, height: 88)
                .scaleEffect(pulsing ? 1.25 : 1.0)
                .opacity(pulsing ? 0 : 1)
            Circle()
                .fill(Palette.accent.opacity(0.15))
                .frame(width: 88, height: 88)
                .overlay(
                    Image(systemName: "waveform")
                        .font(.system(size: 28, weight: .medium))
                        .foregroundStyle(Palette.accent)
                )
        }
        .onAppear {
            withAnimation(.easeOut(duration: 1.6).repeatForever(autoreverses: false)) {
                pulsing = true
            }
        }
    }
}

// Code-only filler mascot — no image assets, so it's not blocked on generated art. Swap
// the body for a real character illustration later; call sites (frame size + placement)
// stay the same. Bounces in on appear, breathes gently while idle, and blinks on a
// randomized loop for a touch of life.
private struct PlaceholderMascot: View {
    var size: CGFloat = 180
    @State private var appeared = false
    @State private var breathing = false
    @State private var blinking = false

    var body: some View {
        ZStack {
            Circle()
                .fill(LinearGradient(colors: [Palette.accent, Palette.accent.opacity(0.75)],
                                     startPoint: .topLeading, endPoint: .bottomTrailing))
            VStack(spacing: size * 0.07) {
                HStack(spacing: size * 0.16) {
                    eye
                    eye
                }
                Capsule().fill(Palette.onInk).frame(width: size * 0.3, height: size * 0.05)
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .overlay(Circle().strokeBorder(.white.opacity(0.5), lineWidth: 1))
        .shadow(color: Palette.shadowWarm.opacity(0.18), radius: 24, x: 0, y: 12)
        .scaleEffect(appeared ? (breathing ? 1.03 : 1.0) : 0.7)
        .opacity(appeared ? 1 : 0)
        .accessibilityHidden(true)
        .task {
            withAnimation(Motion.spring) { appeared = true }
            withAnimation(Motion.breath.delay(0.35)) { breathing = true }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64.random(in: 2_400_000_000...4_600_000_000))
                guard !Task.isCancelled else { break }
                withAnimation(.easeInOut(duration: 0.08)) { blinking = true }
                try? await Task.sleep(nanoseconds: 110_000_000)
                withAnimation(.easeInOut(duration: 0.12)) { blinking = false }
            }
        }
    }

    private var eye: some View {
        Capsule()
            .fill(Palette.onInk)
            .frame(width: size * 0.09, height: blinking ? size * 0.012 : size * 0.09)
    }
}

// MARK: - Progress

private struct OnboardProgress: View {
    let total: Int
    let index: Int
    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color(hex: 0xE2E1DE)).frame(height: 2)
                Capsule().fill(Palette.ink)
                    .frame(width: geo.size.width * CGFloat(index) / CGFloat(total), height: 2)
                    .animation(.easeOut(duration: 0.38), value: index)
            }
        }
        .frame(height: 2)
    }
}
