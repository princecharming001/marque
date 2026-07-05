import SwiftUI

// Voice teaching lives as two consecutive onboarding steps now (connectAccounts
// then voiceInterview in OnboardingView.swift) — every user walks through both
// instead of choosing one path. This file keeps the embedded 4-question
// interview component that the voiceInterview step renders.

// MARK: - Embedded 4-question interview (refactored from VoiceOnboardingSheet)

struct VoiceInterviewView: View {
    @Environment(AppStore.self) private var store
    let onComplete: () -> Void

    @State private var answers: [String] = Array(repeating: "", count: questions.count)
    @State private var currentQ = 0
    @State private var finalizing = false

    private static let questions = [
        "What do you make videos about? Be specific.",
        "What do your best viewers say about your content?",
        "What's a topic you could talk about for an hour without notes?",
        "What's something your niche gets wrong that you love to fix?",
    ]

    var body: some View {
        if finalizing {
            VStack(spacing: Space.lg) {
                ProgressView().tint(Palette.ink)
                Text("Building your voice profile…")
                    .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
            }
        } else {
            VStack(alignment: .leading, spacing: Space.md) {
                Text("Q\(currentQ + 1) of \(Self.questions.count)")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                Text(Self.questions[currentQ])
                    .font(Typeface.display(22)).tracking(-0.4)
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                TextEditor(text: Binding(get: { answers[currentQ] },
                                         set: { answers[currentQ] = $0 }))
                    .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                    .scrollContentBackground(.hidden)
                    .frame(minHeight: 110, maxHeight: 160)
                    .padding(Space.md)
                    .background(Palette.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                    .accessibilityIdentifier("onboard.interview.answer")

                let ready = !answers[currentQ].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                OnbPill(title: currentQ < Self.questions.count - 1 ? "Next question" : "Build my voice",
                        enabled: ready) {
                    if currentQ < Self.questions.count - 1 {
                        withAnimation(Motion.enter) { currentQ += 1 }
                    } else {
                        Task { await finalize() }
                    }
                }
                .accessibilityIdentifier("onboard.interview.next")
            }
        }
    }

    private func finalize() async {
        withAnimation { finalizing = true }
        let transcript: [[String: String]] = zip(Self.questions, answers).flatMap { q, a in
            [["role": "agent", "text": q], ["role": "user", "text": a]]
        }
        if let result = await store.backend.voiceOnboardingFinalize(niche: store.brand.niche,
                                                                    transcript: transcript) {
            store.applyVoiceScan(result)
        } else {
            store.derivePillars()
            store.brand.analyzed = true
            store.save()
        }
        onComplete()
    }
}
