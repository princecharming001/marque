import SwiftUI
import Observation

// Drives the guided walkthrough: a Stoic-style coach card that points at the real app
// controls (the voice drop, the tabs, the Film button) one at a time. Runs once
// automatically after onboarding finishes, and can be replayed from Settings any time.
//
// 2026-09-24 redesign: the clay-render mascot poses are gone. Each step now carries a flat
// ink illustration (template-rendered, so it follows the ink/paper inversion in dark mode),
// a tab-name eyebrow, and a lowercase-with-period title in the app's black-and-white voice.
@MainActor
@Observable
final class TourManager {
    struct Step: Identifiable {
        let id: String            // matches the .tourAnchor(id) tag on the target control
        let eyebrow: String       // the name of the place being introduced (uppercased by DSEyebrow)
        let title: String         // lowercase-with-period, DESIGN.md §2
        let message: String
        let art: String           // ink illustration asset (template rendering)
    }

    /// One pass through the five places a brand-new creator needs to find, in the order
    /// they'd use them: talk an idea through, film it, find the finished clip, see how it did.
    static let steps: [Step] = [
        Step(id: "tour.voiceBubble", eyebrow: "Yuni", title: "talk it out.",
             message: "Tap the drop to plan a script, riff on ideas, or map out your week out loud.",
             art: "TourTalk"),
        Step(id: "tour.chat", eyebrow: "Chat", title: "rather type?",
             message: "Same Yuni in text. Ask for a script, or send a take and say how to edit it.",
             art: "TourChat"),
        Step(id: "tour.film", eyebrow: "Film", title: "film yourself.",
             message: "Talk to the camera. We cut the pauses, add captions and b-roll, and hand you a finished clip.",
             art: "TourFilm"),
        Step(id: "tour.library", eyebrow: "Library", title: "your clips live here.",
             message: "Finished clips, drafts and raw takes. Open any clip to tweak the edit or post it.",
             art: "TourLibrary"),
        Step(id: "tour.performance", eyebrow: "Performance", title: "see what's working.",
             message: "How your posts are doing, and what to make more of next.",
             art: "TourGrowth"),
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
        router.selectedTab = .home   // the voice-drop step needs Home's content on screen
        router.homePath.removeAll()  // …and nothing pushed over it (Settings → Replay)
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
