import SwiftUI
import Observation

// Drives the guided walkthrough: same UnicornMascot that lives in onboarding, popping up
// next to real app controls (the tab bar, the voice bubble) to introduce them. Runs once
// automatically after onboarding finishes, and can be replayed from Settings any time.
@MainActor
@Observable
final class TourManager {
    struct Step: Identifiable {
        let id: String            // matches the .tourAnchor(id) tag on the target control
        let title: String
        let message: String
    }

    /// One pass through the four things a brand-new creator actually needs to find.
    /// All four anchors live on the persistent tab bar / Home, so no tab-switching is
    /// required mid-tour except landing on Home first (see `start`).
    static let steps: [Step] = [
        Step(id: "tour.voiceBubble", title: "Talk to Yuni",
             message: "Tap here anytime to talk out loud — scripts, ideas, or your whole day, planned."),
        Step(id: "tour.chat", title: "Prefer typing?",
             message: "Chat has the same brain as the voice bubble — same Yuni, just text."),
        Step(id: "tour.film", title: "Ready to record?",
             message: "Tap here to film. I'll turn your take into ready-to-post clips."),
        Step(id: "tour.library", title: "Your clips live here",
             message: "Ready clips, drafts, and saved footage — everything lands in Library."),
        Step(id: "tour.performance", title: "Track what's working",
             message: "See how your posts are doing and what to make more of."),
    ]

    private static let completedKey = "tour.completed"

    private(set) var isActive = false
    private(set) var index = 0

    var current: Step? { isActive && Self.steps.indices.contains(index) ? Self.steps[index] : nil }
    var isLastStep: Bool { index == Self.steps.count - 1 }
    var hasCompleted: Bool { UserDefaults.standard.bool(forKey: Self.completedKey) }

    /// Called from Home's first appearance post-onboarding — no-op if already seen.
    func startIfNeeded(router: AppRouter) {
        guard !hasCompleted, !isActive else { return }
        start(router: router)
    }

    /// Explicit replay (Settings → "Replay walkthrough").
    func start(router: AppRouter) {
        router.selectedTab = .home   // the voice-bubble step needs Home's content on screen
        index = 0
        isActive = true
    }

    func next(router: AppRouter) {
        guard isActive else { return }
        if isLastStep { finish(); return }
        index += 1
    }

    func skip() { finish() }

    private func finish() {
        isActive = false
        UserDefaults.standard.set(true, forKey: Self.completedKey)
    }
}
