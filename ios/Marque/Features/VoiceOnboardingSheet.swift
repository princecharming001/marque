import SwiftUI

// Voice onboarding: lets creators describe their content by talking (ElevenLabs when keyed)
// or by answering simple text prompts (mock path). Either way finalizes via /v1/voice-onboarding/finalize
// to derive brand pillars from the conversation.
struct VoiceOnboardingSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let onComplete: () -> Void

    @State private var phase: Phase = .intro
    @State private var answers: [String] = Array(repeating: "", count: questions.count)
    @State private var currentQ = 0
    @State private var loading = false

    private static let questions = [
        "What do you make videos about? Be specific.",
        "What do your best viewers say about your content?",
        "What's a topic you could talk about for an hour without notes?",
        "What's something your niche gets wrong that you love to fix?",
    ]

    enum Phase { case intro, interview, finalizing, done }

    var body: some View {
        NavigationStack {
            ZStack {
                Palette.canvas.ignoresSafeArea()
                switch phase {
                case .intro:    introView
                case .interview: interviewView
                case .finalizing: finalizingView
                case .done:     doneView
                }
            }
            .navigationTitle("Teach Marque your voice")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { store.showVoiceOnboarding = false; dismiss() }
                }
            }
        }
    }

    private var introView: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            Text("Four questions.\nFive minutes.\nA brain that sounds like you.")
                .font(Typeface.display(28, .semibold)).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text("Answer honestly — the less polished, the better. Marque listens for your *actual* voice, not the brand-speak version.")
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
            Spacer()
            PrimaryButton(title: "Let's go") { withAnimation(Motion.enter) { phase = .interview } }
        }
        .screenPadding()
    }

    private var interviewView: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            Text("Q\(currentQ + 1) of \(Self.questions.count)")
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
            Text(Self.questions[currentQ])
                .font(Typeface.display(24, .semibold)).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            TextEditor(text: Binding(get: { answers[currentQ] },
                                     set: { answers[currentQ] = $0 }))
                .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                .scrollContentBackground(.hidden)
                .frame(minHeight: 120)
                .padding(Space.md)
                .background(Palette.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
            Spacer()
            let answerReady = !answers[currentQ].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            PrimaryButton(title: currentQ < Self.questions.count - 1 ? "Next" : "Build my voice") {
                if currentQ < Self.questions.count - 1 {
                    withAnimation(Motion.enter) { currentQ += 1 }
                } else {
                    Task { await finalize() }
                }
            }
            .opacity(answerReady ? 1 : 0.4)
            .disabled(!answerReady)
        }
        .screenPadding()
    }

    private var finalizingView: some View {
        VStack(spacing: Space.lg) {
            ProgressView().tint(Palette.accent)
            Text("Building your voice profile…")
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var doneView: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 48)).foregroundStyle(Palette.accent)
            Text("Your voice is set.")
                .font(Typeface.display(28, .semibold)).foregroundStyle(Palette.textPrimary)
            Text("Marque built \(store.pillars.count) starter pillars from your answers — refine them anytime in Studio.")
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
            Spacer()
            PrimaryButton(title: "See my pillars") {
                store.showVoiceOnboarding = false
                dismiss()
                onComplete()
            }
        }
        .screenPadding()
    }

    private func finalize() async {
        withAnimation { phase = .finalizing }
        let transcript: [[String: String]] = zip(Self.questions, answers).flatMap { q, a in
            [["role": "agent", "text": q], ["role": "user", "text": a]]
        }
        if let result = await store.backend.voiceOnboardingFinalize(niche: store.brand.niche, transcript: transcript) {
            store.applyVoiceScan(result)
        } else {
            // Fallback: derive pillars from the typed answers using standard path.
            store.derivePillars()
            store.brand.analyzed = true
            store.save()
        }
        withAnimation { phase = .done }
    }
}
