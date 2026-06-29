import SwiftUI
import UIKit

struct OnboardingView: View {
    @Environment(AppStore.self) private var store
    @State private var step = 0
    @State private var analyzing = false
    @State private var generating = false

    private let lastInputStep = 5  // 0 welcome,1 goal,2 about,3 knownFor,4 voice,5 connect

    var body: some View {
        ZStack {
            Palette.surface.ignoresSafeArea()
            VStack(spacing: Space.xl) {
                // progress
                if step > 0 && step <= lastInputStep {
                    ProgressDots(total: lastInputStep, index: step)
                        .padding(.top, Space.lg)
                }
                Spacer(minLength: 0)

                switch step {
                case 0: welcome
                case 1: goalStep()
                case 2: aboutStep()
                case 3: knownForStep()
                case 4: voiceStep()
                case 5: connectStep()
                default: ahaStep
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
            Text("Marque")
                .font(AppFont.displayXL).foregroundStyle(Palette.textPrimary)
            Text("Film once a week.\nPost every day.")
                .font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
            Text("We learn your voice, write scripts that sound like you, and turn one recording into a week of clips.")
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
            Spacer().frame(height: Space.lg)
            PrimaryButton(title: "Get started") { advance() }
                .accessibilityIdentifier("onboard.start")
        }
    }

    private func goalStep() -> some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            Title("What are you here to do?")
            ForEach(Goal.allCases) { g in
                Button { store.brand.goal = g } label: {
                    SelectRow(text: g.rawValue, selected: store.brand.goal == g)
                }.buttonStyle(.plain)
            }
            Spacer().frame(height: Space.sm)
            PrimaryButton(title: "Continue") { advance() }
        }
    }

    private func aboutStep() -> some View {
        @Bindable var store = store
        return VStack(alignment: .leading, spacing: Space.lg) {
            Title("Tell me about you")
            Field("Your niche", text: $store.brand.niche, id: "onboard.niche")
            Field("What you do", text: $store.brand.whatYouDo, id: "onboard.whatYouDo")
            Field("Who you serve", text: $store.brand.audience, id: "onboard.audience")
            Spacer().frame(height: Space.sm)
            PrimaryButton(title: "Continue") { advance() }
                .disabled(store.brand.niche.isEmpty)
                .opacity(store.brand.niche.isEmpty ? 0.5 : 1)
        }
    }

    private func knownForStep() -> some View {
        @Bindable var store = store
        return VStack(alignment: .leading, spacing: Space.lg) {
            Title("What do you want to be known for?")
            Field("In a sentence…", text: $store.brand.knownFor, id: "onboard.knownFor")
            Text("This is the heart of your brand. Everything we write points back here.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
            Spacer().frame(height: Space.sm)
            PrimaryButton(title: "Continue") { advance() }
        }
    }

    private func voiceStep() -> some View {
        @Bindable var store = store
        return VStack(alignment: .leading, spacing: Space.lg) {
            Title("How do you sound?")
            VoiceSlider(label: "Funny ⟷ Serious", value: $store.brand.voice.funnyToSerious)
            VoiceSlider(label: "Polished ⟷ Raw", value: $store.brand.voice.polishedToRaw)
            VoiceSlider(label: "Teacher ⟷ Peer", value: $store.brand.voice.teacherToPeer)
            Spacer().frame(height: Space.sm)
            PrimaryButton(title: "Continue") { advance() }
        }
    }

    private func connectStep() -> some View {
        @Bindable var store = store
        return VStack(alignment: .leading, spacing: Space.lg) {
            Title("Connect your page")
            Text("We'll read your recent posts to learn what already works for you. Optional — but it makes everything sharper.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
            Field("@handle", text: $store.brand.pageHandle, id: "onboard.handle")
            if analyzing {
                HStack(spacing: Space.sm) { ProgressView().tint(Palette.gold); Text("Reading your page…").font(AppFont.body).foregroundStyle(Palette.textSecondary) }
            }
            Spacer().frame(height: Space.sm)
            PrimaryButton(title: analyzing ? "Analyzing…" : "Analyze my page") {
                analyzing = true
                Task { await store.analyzePage(); analyzing = false; advance() }
            }
            .disabled(analyzing)
            GhostButton(title: "Skip for now") {
                store.derivePillars(); advance()
            }
        }
    }

    private var ahaStep: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            if generating {
                VStack(alignment: .leading, spacing: Space.md) {
                    Text("Writing your first scripts…")
                        .font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
                    Text("In your voice. Built to stop the scroll.")
                        .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                    ProgressView().tint(Palette.gold).padding(.top, Space.md)
                }
                .onAppear {
                    Task {
                        await store.generateStarterScripts()
                        generating = false
                    }
                }
            } else {
                Text("Your first 3 scripts are ready.")
                    .font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
                Text("They're waiting in Studio. Record when you've got a few minutes — we'll do the editing.")
                    .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                ForEach(store.scripts.prefix(3)) { s in
                    HStack(spacing: Space.sm) {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(Palette.gold)
                        Text(s.hook.text).font(AppFont.body).foregroundStyle(Palette.textPrimary).lineLimit(2)
                    }
                }
                Spacer().frame(height: Space.sm)
                PrimaryButton(title: "Enter Marque") { store.completeOnboarding() }
                    .accessibilityIdentifier("onboard.finish")
            }
        }
    }

    private func advance() {
        if step == lastInputStep {
            generating = true
            step += 1
        } else {
            withAnimation(Motion.enter) { step += 1 }
        }
    }
}

// MARK: - Onboarding sub-views

private struct Title: View {
    let t: String
    init(_ t: String) { self.t = t }
    var body: some View {
        Text(t).font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
            .fixedSize(horizontal: false, vertical: true)
    }
}

private struct Field: View {
    let placeholder: String
    @Binding var text: String
    let id: String
    init(_ placeholder: String, text: Binding<String>, id: String) {
        self.placeholder = placeholder; self._text = text; self.id = id
    }
    var body: some View {
        TextField(placeholder, text: $text)
            .font(AppFont.bodyL)
            .foregroundStyle(Palette.textPrimary)
            .padding(.vertical, Space.md)
            .overlay(alignment: .bottom) { Rectangle().fill(Palette.hairline).frame(height: 1) }
            .accessibilityIdentifier(id)
    }
}

private struct SelectRow: View {
    let text: String
    let selected: Bool
    var body: some View {
        HStack {
            Text(text).font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
            Spacer()
            Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                .foregroundStyle(selected ? Palette.gold : Palette.textTertiary)
        }
        .padding(.vertical, Space.md)
        .overlay(alignment: .bottom) { Rectangle().fill(Palette.hairline).frame(height: 1) }
    }
}

private struct VoiceSlider: View {
    let label: String
    @Binding var value: Double
    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(label).font(AppFont.callout).foregroundStyle(Palette.textSecondary)
            Slider(value: $value).tint(Palette.gold)
        }
    }
}

private struct ProgressDots: View {
    let total: Int
    let index: Int
    var body: some View {
        HStack(spacing: 6) {
            ForEach(0..<total, id: \.self) { i in
                Capsule()
                    .fill(i < index ? Palette.gold : Palette.hairline)
                    .frame(width: i == index - 1 ? 18 : 6, height: 6)
            }
        }
    }
}
