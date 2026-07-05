import SwiftUI

// "Let me learn your voice" — voice-teaching IN the flow (replaces the old
// optional VoiceOnboardingSheet + the standalone connect step). Two paths:
//   1. PRIMARY: connect Instagram/TikTok → the backend studies recent reels
//      (captions + transcripts) and derives voice + pillars.
//   2. Answer 4 quick questions (the embedded interview).
// A small "Skip for now" keeps the step non-blocking.
struct VoiceTeachStep: View {
    @Environment(AppStore.self) private var store
    let onDone: () -> Void        // advance()
    let onSkip: () -> Void        // derivePillars + advance()

    private enum Mode { case choose, connect, interview }
    @State private var mode: Mode = .choose
    @State private var analyzing = false

    var body: some View {
        VStack(spacing: Space.lg) {
            switch mode {
            case .choose:  chooser
            case .connect: connectView
            case .interview:
                VoiceInterviewView {
                    onDone()
                }
            }
        }
        .animation(Motion.enter, value: mode == .choose)
    }

    // MARK: chooser

    private var chooser: some View {
        VStack(spacing: Space.md) {
            OptionCard(icon: "OnbIcon-voice-connect", sfFallback: "link",
                       title: "Connect Instagram or TikTok",
                       subtitle: "I'll study your recent reels and learn how you actually talk",
                       selected: false) {
                withAnimation(Motion.enter) { mode = .connect }
            }
            .accessibilityIdentifier("onboard.voiceTeach.connect")

            OptionCard(icon: "OnbIcon-voice-interview", sfFallback: "bubble.left.and.text.bubble.right",
                       title: "Answer 4 quick questions",
                       subtitle: "Two minutes, typed — I listen for your real voice",
                       selected: false) {
                withAnimation(Motion.enter) { mode = .interview }
            }
            .accessibilityIdentifier("onboard.voiceTeach.interview")

            Button {
                store.derivePillars()
                onSkip()
            } label: {
                Text("Skip for now")
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
            }
            .accessibilityIdentifier("onboard.voiceTeach.skip")
            .padding(.top, Space.sm)
        }
    }

    // MARK: connect

    private var connectView: some View {
        VStack(spacing: Space.lg) {
            ConnectAccountsView()

            if store.brand.connectedAccounts.isEmpty {
                Button {
                    withAnimation(Motion.enter) { mode = .choose }
                } label: {
                    Text("Actually, let me answer questions instead")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                }
            } else {
                OnbPill(title: analyzing ? "Reading your reels…" : "Learn my voice",
                        enabled: !analyzing) {
                    analyzing = true
                    Task {
                        await store.analyzePage()
                        analyzing = false
                        onDone()
                    }
                }
                .accessibilityIdentifier("onboard.voiceTeach.analyze")
            }
        }
    }
}

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
