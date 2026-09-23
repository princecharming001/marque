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
                ProgressView().tint(Palette.textPrimary)
                Text("Building your voice profile…")
                    .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
            }
        } else {
            VStack(alignment: .leading, spacing: Space.md) {
                // Stoic editor prompt: progress dashes, eyebrow count, title1 question.
                DSProgressDashes(total: Self.questions.count, current: currentQ + 1)
                Text("Q\(currentQ + 1) of \(Self.questions.count)")
                    .font(AppFont.eyebrow).tracking(Track.eyebrow)
                    .textCase(.uppercase)
                    .foregroundStyle(Palette.textSecondary)
                Text(Self.questions[currentQ])
                    .font(AppFont.title1).tracking(-0.3)
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                TextEditor(text: Binding(get: { answers[currentQ] },
                                         set: { answers[currentQ] = $0 }))
                    .font(AppFont.bodyLarge).foregroundStyle(Palette.textPrimary)
                    .scrollContentBackground(.hidden)
                    .frame(minHeight: 110, maxHeight: 160)
                    .padding(Space.md)
                    .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                        .fill(Palette.surface))
                    .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
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
                .frame(maxWidth: .infinity)
                .padding(.top, Space.sm)
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
            // Build 67: no fabricated pillars — the profile shows an honest empty state
            // until a real account is connected or the creator writes their own.
            store.brand.analyzed = true
            store.save()
        }
        onComplete()
    }
}
