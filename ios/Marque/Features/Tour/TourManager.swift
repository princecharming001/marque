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
        let mascot: String        // per-step Yuni pose asset (distinct, static, whimsical)
    }

    /// One pass through the things a brand-new creator needs to find. Each step gets its
    /// own Yuni pose so it's never the same unicorn twice.
    static let steps: [Step] = [
        Step(id: "tour.voiceBubble", title: "Talk to Yuni",
             message: "Tap here anytime to talk out loud — scripts, ideas, or your whole day, planned.",
             mascot: "UnicornTourWave"),
        Step(id: "tour.chat", title: "Prefer typing?",
             message: "Chat has the same brain as the voice bubble — same Yuni, just text.",
             mascot: "UnicornTourLean"),
        Step(id: "tour.film", title: "Ready to record?",
             message: "Tap here to film. I'll turn your take into ready-to-post clips.",
             mascot: "UnicornTourPoint"),
        Step(id: "tour.library", title: "Your clips live here",
             message: "Ready clips, drafts, and saved footage — everything lands in Library.",
             mascot: "UnicornTourChill"),
        Step(id: "tour.performance", title: "Track what's working",
             message: "See how your posts are doing and what to make more of.",
             mascot: "UnicornTourCheer"),
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
