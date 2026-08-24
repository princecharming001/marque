import SwiftUI
import Observation

// V3 tab set: Home (voice AI + daily feed) · Chat · [center Film] · Library · Performance.
// Profile is pushed from Home's top-right avatar, not a tab.
enum AppTab: Hashable {
    case home, chat, library, performance
}

@MainActor
@Observable
final class AppRouter {
    var selectedTab: AppTab = .home
    var hideTabBar = false
    /// Home's navigation path (currently only "profile"). Bound so the tour can pop
    /// back to Home before it starts — replaying the walkthrough from Settings used
    /// to leave Profile pushed on top, so the spotlight rings landed on Home's
    /// coordinates over an unrelated screen.
    var homePath: [String] = []
    /// The center Film button — fullScreenCover with the script picker + teleprompter flow.
    var showFilm = false
    /// Deep-link: "Film this" from the feed/chat preselects a readied script in the Film flow.
    var pendingFilmScriptId: UUID? = nil
    /// Deep-link: Library "Schedule this clip" → Performance queue opens the scheduler.
    var pendingScheduleClipId: UUID? = nil
    /// UX-B2b deep-link: a clips_ready push tap → Library tab + open this clip's detail.
    var pendingOpenClipId: UUID? = nil
    /// P7.3 deep-link: tapping an insight card (or its push) → Chat tab with this prompt
    /// pre-filled, so the strategist picks up right where the insight left off.
    var pendingChatPrompt: String? = nil

    /// UX-B2b: route a marque:// URL (push tap / onOpenURL). Recognized today:
    /// marque://library/clip/{uuid} → Library tab + clip detail. OAuth callbacks
    /// (marque://auth-callback) belong to ASWebAuthenticationSession — ignored here.
    @discardableResult
    func handle(url: URL) -> Bool {
        guard url.scheme == "marque", url.host == "library" else { return false }
        let parts = url.pathComponents.filter { $0 != "/" }
        guard parts.count == 2, parts[0] == "clip", let id = UUID(uuidString: parts[1]) else {
            selectedTab = .library                     // marque://library alone still lands there
            return true
        }
        selectedTab = .library
        pendingOpenClipId = id
        return true
    }
}
