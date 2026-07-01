import SwiftUI
import UIKit

// Stoic onboarding, modeled on maxapp: warm off-white canvas, 2px ink progress bar,
// white pill choice cards (ink when selected), boxed hairline fields, ink pill buttons,
// one idea per screen. Accessibility ids + button text preserved for the Maestro flow.
struct OnboardingView: View {
    @Environment(AppStore.self) private var store
    @State private var step = 0
    @State private var analyzing = false
    @State private var generating = false

    private let lastInputStep = 6

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
                if step <= lastInputStep {
                    VStack(spacing: Space.sm) {
                        HStack {
                            if step >= 2 {
                                Button { withAnimation(Motion.enter) { step -= 1 } } label: {
                                    Image(systemName: "chevron.left")
                                        .font(.system(size: 16, weight: .semibold))
                                        .foregroundStyle(Palette.textSecondary)
                                }
                                .accessibilityIdentifier("onboard.back")
                            }
                            Spacer()
                        }
                        OnboardProgress(total: lastInputStep, index: step)
                    }
                    .padding(.top, Space.md)
                }
                Spacer(minLength: 0)
                Group {
                    switch step {
                    case 1: goalStep()
                    case 2: aboutStep()
                    case 3: knownForStep()
                    case 4: voiceStep()
                    case 5: styleStep()
                    case 6: connectStep()
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

    // MARK: Steps

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

    private func aboutStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "Tell me about you") {
            VStack(spacing: Space.md) {
                TextField("Your niche", text: $store.brand.niche).marqueField().accessibilityIdentifier("onboard.niche")
                TextField("What you do", text: $store.brand.whatYouDo).marqueField().accessibilityIdentifier("onboard.whatYouDo")
                TextField("Who you serve", text: $store.brand.audience).marqueField().accessibilityIdentifier("onboard.audience")
            }
            PillButton(title: "Continue", enabled: !store.brand.niche.isEmpty) { advance() }
        }
    }

    private func knownForStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "What do you want to be known for?",
                            note: "This is the heart of your brand. Everything we write points back here.") {
            TextField("In a sentence…", text: $store.brand.knownFor).marqueField().accessibilityIdentifier("onboard.knownFor")
            PillButton(title: "Continue", enabled: !store.brand.knownFor.trimmingCharacters(in: .whitespaces).isEmpty) { advance() }
            Button("Skip — I'll add this later") { advance() }
                .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                .accessibilityIdentifier("onboard.knownForSkip")
        }
    }

    private func voiceStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "What's your voice like?",
                            note: "Marque writes in your register — tune these to match how you actually talk.") {
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
                    .foregroundStyle(value.wrappedValue < 0.4 ? Palette.accent : Palette.textTertiary)
                Spacer()
                Text(trailing)
                    .font(AppFont.callout)
                    .foregroundStyle(value.wrappedValue > 0.6 ? Palette.accent : Palette.textTertiary)
            }
            Slider(value: value).tint(Palette.accent)
        }
    }

    private func styleStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "What kind of videos?",
                            note: "Pick the styles you want to make. Each gets its own kind of script.") {
            StyleSelectionView(selected: $store.brand.preferredStyles)
            PillButton(title: "Continue", enabled: !store.brand.preferredStyles.isEmpty) { advance() }
        }
    }

    private func connectStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "Connect your accounts",
                            note: "Link your Instagram and TikTok so Marque learns from what already works. Add more than one if you like.") {
            ConnectAccountsView()
            VStack(spacing: Space.sm) {
                PillButton(title: analyzing ? "Reading your page…" : "Continue", enabled: !analyzing) {
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

    private var ahaStep: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            if generating {
                Text("Writing your first scripts…")
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
                Text("They're waiting in Studio. Record when you've got a few minutes — we'll do the editing.")
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

    private func advance() {
        if step == lastInputStep { generating = true; step += 1 }
        else { withAnimation(Motion.enter) { step += 1 } }
    }
}

// MARK: - Onboarding sub-views

private struct HeroWelcome: View {
    let start: () -> Void
    var body: some View {
        ZStack(alignment: .bottomLeading) {
            Image("Hero").resizable().scaledToFill().ignoresSafeArea()
            LinearGradient(colors: [.clear, .clear, .black.opacity(0.55), .black.opacity(0.92)],
                           startPoint: .top, endPoint: .bottom)
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
            Text(question).font(AppFont.question).tracking(-0.6).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            if let note { Text(note).font(AppFont.body).foregroundStyle(Palette.textSecondary) }
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
