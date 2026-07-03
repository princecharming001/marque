import SwiftUI
import UIKit

// Stoic onboarding, modeled on maxapp: warm off-white canvas, 2px ink progress bar,
// white pill choice cards (ink when selected), boxed hairline fields, ink pill buttons,
// one idea per screen. Accessibility ids + button text preserved for the Maestro flow.
//
// Steps 0–20: opens with a mascot-led intro (self-intro → collect name → "let me explain
// what I can do" → one feature preview) before the quiz, mirroring Gentler Streak's
// Yorhart pattern. The mascot is a code-only placeholder (PlaceholderMascot) — swap for
// custom art later without touching flow/copy. Progress bar only covers the quiz portion
// (steps 5–19); the intro screens are un-numbered, like the reference.
//
//  0: hero              — HeroWelcome
//  1: mascotIntro       — "Hi, I'm Marque" + tagline
//  2: name              — "…and who are you?" — collects creatorName
//  3: mascotReady       — "Let's get to know each other, {name}!"
//  4: featureExplainer  — one feature preview ("I learn what works for you")
//  5: goal              — "What are you here to do?"
//  6: platform          — "Where does your audience live?"
//  7: stage             — "Where's your audience today?"
//  8: frequency         — "How often do you post right now?"
//  9: methodInterstitial — "Consistency beats virality"
// 10: blocker           — "What gets in the way most?"
// 11: niche             — niche field only
// 12: whatYouDo         — whatYouDo + audience fields
// 13: knownFor          — knownFor field
// 14: mirrorInterstitial — brand mirror sentence
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
            HeroWelcome { advance() }
        } else {
            inputFlow
        }
    }

    private var inputFlow: some View {
        ZStack {
            Palette.canvas.ignoresSafeArea()
            VStack(spacing: Space.xl) {
                if step >= 1 && step <= lastInputStep {
                    VStack(spacing: Space.sm) {
                        HStack {
                            if step >= 2 {
                                Button {
                                    withAnimation(Motion.enter) { step -= 1 }
                                } label: {
                                    Image(systemName: "chevron.left")
                                        .font(.system(size: 16, weight: .semibold))
                                        .foregroundStyle(Palette.textSecondary)
                                }
                                .accessibilityIdentifier("onboard.back")
                            }
                            Spacer()
                        }
                        // The mascot-intro screens (1–4) are un-numbered, like the reference —
                        // the bar only appears once the actual quiz starts.
                        if step >= quizStartStep {
                            OnboardProgress(total: lastInputStep - quizStartStep + 1,
                                           index: step - quizStartStep + 1)
                        }
                    }
                    .padding(.top, Space.md)
                }
                Spacer(minLength: 0)
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
                Spacer(minLength: 0)
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
        VStack(alignment: .leading, spacing: Space.sm) {
            Text("Hi, I\u{2019}m Marque")
                .font(AppFont.headline).foregroundStyle(Palette.textTertiary)
            Text("Your dedicated partner in building a content habit that actually sticks.")
                .font(Typeface.display(30, .semibold)).tracking(-0.6)
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: Space.xl)
            PlaceholderMascot()
                .frame(maxWidth: .infinity)
            Spacer(minLength: Space.xl)
            PillButton(title: "Hi, Marque") { advance() }
                .accessibilityIdentifier("onboard.mascotIntro.continue")
        }
    }

    // Step 2: Collect the creator's name
    private func nameStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "\u{2026}and who are you?", note: "Enter the name you\u{2019}d like to go by.") {
            TextField("Name", text: Binding(
                get: { store.brand.creatorName ?? "" },
                set: { store.brand.creatorName = $0 }
            ))
            .marqueField()
            .accessibilityIdentifier("onboard.creatorName")
            PillButton(
                title: "Continue",
                enabled: !(store.brand.creatorName ?? "").trimmingCharacters(in: .whitespaces).isEmpty
            ) { advance() }
        }
    }

    // Step 3: Mascot greets the creator by name, sets expectation for what's next
    private func mascotReadyStep() -> some View {
        let name = (store.brand.creatorName ?? "").trimmingCharacters(in: .whitespaces)
        let greeting = name.isEmpty ? "Let\u{2019}s get to know each other." : "Let\u{2019}s get to know each other, \(name)!"
        return VStack(alignment: .leading, spacing: Space.sm) {
            Text(greeting)
                .font(Typeface.display(30, .semibold)).tracking(-0.6)
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text("I\u{2019}ll start by explaining what I can do for you.")
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
            Spacer(minLength: Space.xl)
            PlaceholderMascot()
                .frame(maxWidth: .infinity)
            Spacer(minLength: Space.xl)
            PillButton(title: "Go for it") { advance() }
                .accessibilityIdentifier("onboard.mascotReady.continue")
        }
    }

    // Step 4: One feature preview before the quiz starts
    private func featureExplainerStep() -> some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            ZStack {
                RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                    .fill(Palette.surfaceRaised)
                    .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                VStack(spacing: Space.md) {
                    Circle()
                        .fill(Palette.accent.opacity(0.15))
                        .frame(width: 88, height: 88)
                        .overlay(
                            Image(systemName: "waveform")
                                .font(.system(size: 28, weight: .medium))
                                .foregroundStyle(Palette.accent)
                        )
                    Text("\u{201C}Talk to me every morning.\u{201D}")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                }
            }
            .frame(height: 220)

            Text("I learn what works for you.")
                .font(Typeface.display(28, .semibold)).tracking(-0.6)
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text("Tell me your ideas, your angle, what\u{2019}s on your mind \u{2014} I remember it all and use it to write sharper scripts every day.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                .lineSpacing(4)

            Spacer(minLength: Space.md)
            PillButton(title: "Continue") { advance() }
                .accessibilityIdentifier("onboard.featureExplainer.continue")
        }
    }

    // MARK: - Steps

    // Step 5: Goal
    private func goalStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "What are you here to do?") {
            VStack(spacing: Space.sm) {
                ForEach(Goal.allCases) { g in
                    Button { store.brand.goal = g } label: {
                        ChoiceCard(text: g.rawValue, selected: store.brand.goal == g)
                    }.buttonStyle(.plain)
                }
            }
            PillButton(title: "Continue") { advance() }
        }
    }

    // Step 6: Platform
    // platformBothChosen disambiguates "nil because unset" from "nil because user picked Both"
    @State private var platformBothChosen = false

    private func platformStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "Where does your audience live?") {
            VStack(spacing: Space.sm) {
                Button {
                    store.brand.primaryPlatform = .instagram
                    platformBothChosen = false
                } label: {
                    ChoiceCard(text: "Instagram", selected: store.brand.primaryPlatform == .instagram)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.platform.instagram")

                Button {
                    store.brand.primaryPlatform = .tiktok
                    platformBothChosen = false
                } label: {
                    ChoiceCard(text: "TikTok", selected: store.brand.primaryPlatform == .tiktok)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.platform.tiktok")

                Button {
                    store.brand.primaryPlatform = nil  // nil = "Both" — no single primary
                    platformBothChosen = true
                } label: {
                    ChoiceCard(text: "Both", selected: platformBothChosen)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.platform.both")
            }
            PillButton(title: "Continue") { advance() }
        }
    }

    // Step 7: Stage
    private func stageStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "Where's your audience today?") {
            VStack(spacing: Space.sm) {
                ForEach(CreatorStage.allCases) { s in
                    Button { store.brand.stage = s } label: {
                        ChoiceCard(text: s.rawValue, selected: store.brand.stage == s)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier(stageAccessID(s))
                }
            }
            PillButton(title: "Continue") { advance() }
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
        return StepScaffold(question: "How often do you post right now?") {
            VStack(spacing: Space.sm) {
                ForEach(PostingFrequency.allCases) { f in
                    Button { store.brand.postingFrequency = f } label: {
                        ChoiceCard(text: f.rawValue, selected: store.brand.postingFrequency == f)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier(frequencyAccessID(f))
                }
            }
            PillButton(title: "Continue") { advance() }
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

    // Step 9: Method Interstitial
    private func methodInterstitialStep() -> some View {
        OnboardInterstitial(
            headline: "Consistency beats virality.",
            message: "Most creators burn out chasing hits. Marque helps you build a posting habit first — then the algorithm rewards you for it.",
            onContinue: { advance() }
        )
        .accessibilityElement(children: .contain)
    }

    // Step 10: Blocker
    private func blockerStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "What gets in the way most?") {
            VStack(spacing: Space.sm) {
                ForEach(CreatorBlocker.allCases) { b in
                    Button { store.brand.biggestBlocker = b } label: {
                        EmojiChoiceCard(
                            emoji: b.emoji,
                            text: b.rawValue,
                            selected: store.brand.biggestBlocker == b
                        )
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier(blockerAccessID(b))
                }
            }
            PillButton(title: "Continue") { advance() }
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

    // Step 11: Niche
    private func nicheStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "Tell me about your niche") {
            TextField("Your niche", text: $store.brand.niche)
                .marqueField()
                .accessibilityIdentifier("onboard.niche")
            PillButton(title: "Continue", enabled: !store.brand.niche.isEmpty) { advance() }
        }
    }

    // Step 12: What You Do + Audience
    private func whatYouDoStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "Tell me about you") {
            VStack(spacing: Space.md) {
                TextField("What you do", text: $store.brand.whatYouDo)
                    .marqueField()
                    .accessibilityIdentifier("onboard.whatYouDo")
                TextField("Who you serve", text: $store.brand.audience)
                    .marqueField()
                    .accessibilityIdentifier("onboard.audience")
            }
            PillButton(title: "Continue", enabled: !store.brand.whatYouDo.isEmpty) { advance() }
        }
    }

    // Step 13: Known For
    private func knownForStep() -> some View {
        @Bindable var store = store
        return StepScaffold(
            question: "What do you want to be known for?",
            note: "This is the heart of your brand. Everything we write points back here."
        ) {
            TextField("In a sentence…", text: $store.brand.knownFor)
                .marqueField()
                .accessibilityIdentifier("onboard.knownFor")
            PillButton(
                title: "Continue",
                enabled: !store.brand.knownFor.trimmingCharacters(in: .whitespaces).isEmpty
            ) { advance() }
            Button("Skip — I'll add this later") { advance() }
                .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                .accessibilityIdentifier("onboard.knownForSkip")
        }
    }

    // Step 14: Mirror Interstitial
    private func mirrorInterstitialStep() -> some View {
        let niche     = store.brand.niche.isEmpty    ? "your niche" : store.brand.niche
        let audience  = store.brand.audience.isEmpty ? "your audience" : store.brand.audience
        let knownFor  = store.brand.knownFor.isEmpty ? "what you stand for" : store.brand.knownFor
        let msg = "You\u{2019}re a \(niche) creator for \(audience), known for \(knownFor). Every script Marque writes points back to this."
        return OnboardInterstitial(
            headline: "Your brand, in a sentence.",
            message: msg,
            onContinue: { advance() }
        )
    }

    // Step 15: Voice
    private func voiceStep() -> some View {
        @Bindable var store = store
        return StepScaffold(
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

            PillButton(title: "Continue") { advance() }
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

    // Step 16: Camera Comfort
    private func cameraComfortStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "How do you feel on camera?") {
            VStack(spacing: Space.sm) {
                Button {
                    store.brand.cameraComfort = .natural
                    seedStyles(for: .natural, store: store)
                } label: {
                    ChoiceCard(text: CameraComfort.natural.rawValue, selected: store.brand.cameraComfort == .natural)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.comfort.natural")

                Button {
                    store.brand.cameraComfort = .gettingThere
                    seedStyles(for: .gettingThere, store: store)
                } label: {
                    ChoiceCard(text: CameraComfort.gettingThere.rawValue, selected: store.brand.cameraComfort == .gettingThere)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.comfort.getting")

                Button {
                    store.brand.cameraComfort = .preferOff
                    seedStyles(for: .preferOff, store: store)
                } label: {
                    ChoiceCard(text: CameraComfort.preferOff.rawValue, selected: store.brand.cameraComfort == .preferOff)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.comfort.off")
            }
            PillButton(title: "Continue") { advance() }
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
        return StepScaffold(
            question: "What kind of videos?",
            note: "Pick the styles you want to make. Each gets its own kind of script."
        ) {
            StyleSelectionView(selected: $store.brand.preferredStyles)
            PillButton(title: "Continue", enabled: !store.brand.preferredStyles.isEmpty) { advance() }
        }
    }

    // Step 18: Pace
    private func paceStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "Pick your weekly pace") {
            VStack(spacing: Space.sm) {
                Button { store.brand.weeklyTarget = 3 } label: {
                    PaceCard(count: 3, sub: "~20 min filming", selected: store.brand.weeklyTarget == 3)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.pace.3")

                Button { store.brand.weeklyTarget = 5 } label: {
                    PaceCard(count: 5, sub: "~35 min filming", selected: store.brand.weeklyTarget == 5)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.pace.5")

                Button { store.brand.weeklyTarget = 7 } label: {
                    PaceCard(count: 7, sub: "~50 min filming", selected: store.brand.weeklyTarget == 7)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("onboard.pace.7")
            }
            PillButton(title: "Continue") { advance() }
        }
    }

    // Step 19: Connect
    private func connectStep() -> some View {
        @Bindable var store = store
        return StepScaffold(
            question: "Connect your accounts",
            note: "Link your Instagram and TikTok so Marque learns from what already works. Add more than one if you like."
        ) {
            ConnectAccountsView()
            VStack(spacing: Space.sm) {
                PillButton(
                    title: analyzing ? "Reading your page\u{2026}" : "Continue",
                    enabled: !analyzing
                ) {
                    analyzing = true
                    Task { await store.analyzePage(); analyzing = false; advance() }
                }
                Button("Teach Marque your voice instead") {
                    store.showVoiceOnboarding = true
                }
                .font(AppFont.callout).foregroundStyle(Palette.accent)
                .accessibilityIdentifier("onboard.voiceInstead")
                Button("Skip for now") { store.derivePillars(); advance() }
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
            }
        }
        .sheet(isPresented: $store.showVoiceOnboarding) {
            VoiceOnboardingSheet { advance() }
        }
    }

    // Step 20: Aha
    private var ahaStep: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            if generating {
                Text("Writing your first scripts\u{2026}")
                    .font(Typeface.display(30, .semibold)).foregroundStyle(Palette.textPrimary)
                Text("In your voice. Built to stop the scroll.")
                    .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                ProgressView().tint(Palette.ink).padding(.top, Space.sm)
                Color.clear.frame(height: 1).onAppear {
                    Task { await store.generateStarterScripts(); generating = false }
                }
            } else {
                Text("Your first 3 scripts are ready.")
                    .font(Typeface.display(30, .semibold)).foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("They\u{2019}re waiting in Studio. Record when you\u{2019}ve got a few minutes \u{2014} we\u{2019}ll do the editing.")
                    .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                ForEach(store.scripts.prefix(3)) { s in
                    HStack(alignment: .top, spacing: Space.sm) {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(Palette.accent)
                        Text(s.hook.text).font(AppFont.body).foregroundStyle(Palette.textPrimary).lineLimit(2)
                    }
                }
                Spacer().frame(height: Space.sm)
                PillButton(title: "Enter Marque") { store.completeOnboarding() }
                    .accessibilityIdentifier("onboard.finish")
            }
        }
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

// MARK: - Private Sub-views

// Code-only filler mascot for the intro screens — no image assets, so it's not blocked on
// generated art. Swap the body for a real character illustration later; call sites (frame
// size + placement) stay the same.
private struct PlaceholderMascot: View {
    var size: CGFloat = 180
    var body: some View {
        ZStack {
            Circle()
                .fill(LinearGradient(colors: [Palette.accent, Palette.accent.opacity(0.75)],
                                     startPoint: .topLeading, endPoint: .bottomTrailing))
            VStack(spacing: size * 0.07) {
                HStack(spacing: size * 0.16) {
                    Circle().fill(Palette.onInk).frame(width: size * 0.09, height: size * 0.09)
                    Circle().fill(Palette.onInk).frame(width: size * 0.09, height: size * 0.09)
                }
                Capsule().fill(Palette.onInk).frame(width: size * 0.3, height: size * 0.05)
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .overlay(Circle().strokeBorder(.white.opacity(0.5), lineWidth: 1))
        .shadow(color: Palette.shadowWarm.opacity(0.18), radius: 24, x: 0, y: 12)
        .accessibilityHidden(true)
    }
}

private struct HeroWelcome: View {
    let start: () -> Void
    var body: some View {
        ZStack(alignment: .bottomLeading) {
            Image("Hero").resizable().scaledToFill().ignoresSafeArea()
            LinearGradient(
                colors: [.clear, .clear, .black.opacity(0.55), .black.opacity(0.92)],
                startPoint: .top, endPoint: .bottom
            )
            .ignoresSafeArea()
            VStack(alignment: .leading, spacing: Space.lg) {
                Text("Film once.\nPost every day.")
                    .font(Typeface.display(46, .semibold)).foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)
                    .shadow(color: .black.opacity(0.3), radius: 14, y: 1)
                Text("Marque learns your voice, writes scripts that sound like you, and turns one recording into a week of clips.")
                    .font(AppFont.bodyL).foregroundStyle(.white.opacity(0.82))
                Button(action: start) {
                    HStack(spacing: Space.sm) {
                        Text("Get started").font(AppFont.headline)
                        Image(systemName: "arrow.right").font(.system(size: 15, weight: .semibold))
                    }
                    .foregroundStyle(Palette.ink)
                    .frame(maxWidth: .infinity).frame(height: 56)
                    .background(.white)
                    .clipShape(Capsule())
                    .shadow(color: .black.opacity(0.25), radius: 18, y: 8)
                }
                .buttonStyle(PressableStyle())
                .accessibilityIdentifier("onboard.start")
            }
            .padding(.horizontal, Space.xl)
            .padding(.bottom, Space.huge)
        }
    }
}

private struct StepScaffold<Content: View>: View {
    let question: String
    var note: String? = nil
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            Text(question)
                .font(AppFont.question).tracking(-0.6).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            if let note {
                Text(note).font(AppFont.body).foregroundStyle(Palette.textSecondary)
            }
            content
        }
    }
}

private struct PillButton: View {
    let title: String
    var enabled: Bool = true
    var shine: Bool = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(title).font(AppFont.headline)
                .foregroundStyle(enabled ? Palette.onInk : Color(hex: 0xA4A29D))
                .frame(maxWidth: .infinity).frame(height: 56)
                .background(ZStack {
                    (enabled ? Palette.ink : Color(hex: 0xDAD9D6))
                    if shine && enabled { ShineSweep() }
                })
                .clipShape(Capsule())
        }
        .buttonStyle(PressableStyle())
        .disabled(!enabled)
    }
}

private struct ChoiceCard: View {
    let text: String
    let selected: Bool
    var body: some View {
        HStack {
            Text(text).font(AppFont.bodyL).foregroundStyle(selected ? Palette.onInk : Palette.textPrimary)
            Spacer()
            Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(selected ? Palette.onInk : Palette.textTertiary)
        }
        .padding(.horizontal, Space.lg).frame(height: 58)
        .background(selected ? Palette.ink : Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(selected ? Color.clear : Palette.hairline, lineWidth: 1))
        .shadow(color: .black.opacity(selected ? 0 : 0.05), radius: 12, x: 0, y: 4)
    }
}

private struct EmojiChoiceCard: View {
    let emoji: String
    let text: String
    let selected: Bool
    var body: some View {
        HStack {
            Text(emoji).font(.system(size: 24))
            Text(text).font(AppFont.bodyL).foregroundStyle(selected ? Palette.onInk : Palette.textPrimary)
            Spacer()
            Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(selected ? Palette.onInk : Palette.textTertiary)
        }
        .padding(.horizontal, Space.lg).frame(height: 60)
        .background(selected ? Palette.ink : Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(selected ? Color.clear : Palette.hairline, lineWidth: 1))
    }
}

private struct OnboardInterstitial: View {
    let headline: String
    let message: String
    let onContinue: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            Text(headline)
                .font(Typeface.display(32, .semibold)).tracking(-0.8)
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text(message)
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                .lineSpacing(5).fixedSize(horizontal: false, vertical: true)
            PillButton(title: "Continue", action: onContinue)
                .accessibilityIdentifier("onboard.continue")
        }
    }
}

private struct PaceCard: View {
    let count: Int
    let sub: String
    let selected: Bool
    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("\(count) posts/week").font(AppFont.bodyL)
                    .foregroundStyle(selected ? Palette.onInk : Palette.textPrimary)
                Text(sub).font(AppFont.caption)
                    .foregroundStyle(selected ? Palette.onInk.opacity(0.7) : Palette.textTertiary)
            }
            Spacer()
            Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(selected ? Palette.onInk : Palette.textTertiary)
        }
        .padding(.horizontal, Space.lg).frame(height: 70)
        .background(selected ? Palette.ink : Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(selected ? Color.clear : Palette.hairline, lineWidth: 1))
    }
}

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
