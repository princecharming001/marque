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

    private let lastInputStep = 5

    var body: some View {
        ZStack {
            Palette.canvas.ignoresSafeArea()
            VStack(spacing: Space.xl) {
                if step > 0 && step <= lastInputStep {
                    OnboardProgress(total: lastInputStep, index: step).padding(.top, Space.md)
                }
                Spacer(minLength: 0)
                Group {
                    switch step {
                    case 0: welcome
                    case 1: goalStep()
                    case 2: aboutStep()
                    case 3: knownForStep()
                    case 4: voiceStep()
                    case 5: connectStep()
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

    private var welcome: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            Text("Marque").font(Typeface.display(52, .bold)).foregroundStyle(Palette.textPrimary)
            Text("Film once a week.\nPost every day.")
                .font(Typeface.display(30, .semibold)).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text("We learn your voice, write scripts that sound like you, and turn one recording into a week of clips.")
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
            Spacer().frame(height: Space.sm)
            PillButton(title: "Get started") { advance() }
                .accessibilityIdentifier("onboard.start")
        }
    }

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
            PillButton(title: "Continue") { advance() }
        }
    }

    private func voiceStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "How do you sound?") {
            VStack(spacing: Space.lg) {
                VoiceSlider(label: "Funny ⟷ Serious", value: $store.brand.voice.funnyToSerious)
                VoiceSlider(label: "Polished ⟷ Raw", value: $store.brand.voice.polishedToRaw)
                VoiceSlider(label: "Teacher ⟷ Peer", value: $store.brand.voice.teacherToPeer)
            }
            PillButton(title: "Continue") { advance() }
        }
    }

    private func connectStep() -> some View {
        @Bindable var store = store
        return StepScaffold(question: "Connect your page",
                            note: "We'll read your recent posts to learn what already works for you. Optional — but it makes everything sharper.") {
            TextField("@handle", text: $store.brand.pageHandle).marqueField().accessibilityIdentifier("onboard.handle")
            if analyzing {
                HStack(spacing: Space.sm) { ProgressView().tint(Palette.ink); Text("Reading your page…").font(AppFont.body).foregroundStyle(Palette.textSecondary) }
            }
            VStack(spacing: Space.sm) {
                PillButton(title: analyzing ? "Analyzing…" : "Analyze my page", enabled: !analyzing) {
                    analyzing = true
                    Task { await store.analyzePage(); analyzing = false; advance() }
                }
                Button("Skip for now") { store.derivePillars(); advance() }
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
            }
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

private struct StepScaffold<Content: View>: View {
    let question: String
    var note: String? = nil
    @ViewBuilder let content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            Text(question).font(Typeface.display(28, .semibold)).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            if let note { Text(note).font(AppFont.body).foregroundStyle(Palette.textSecondary) }
            content
        }
    }
}

private struct PillButton: View {
    let title: String
    var enabled: Bool = true
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(title).font(AppFont.headline)
                .foregroundStyle(enabled ? Palette.onInk : Color(hex: 0xA4A29D))
                .frame(maxWidth: .infinity).frame(height: 56)
                .background(enabled ? Palette.ink : Color(hex: 0xDAD9D6))
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

private struct VoiceSlider: View {
    let label: String
    @Binding var value: Double
    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(label).font(AppFont.callout).foregroundStyle(Palette.textSecondary)
            Slider(value: $value).tint(Palette.ink)
        }
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
