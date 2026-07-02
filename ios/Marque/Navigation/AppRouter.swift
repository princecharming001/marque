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
    /// The center Film button — fullScreenCover with the script picker + teleprompter flow.
    var showFilm = false
    /// Deep-link: "Film this" from the feed/chat preselects a readied script in the Film flow.
    var pendingFilmScriptId: UUID? = nil
    /// Deep-link: Library "Schedule this clip" → Performance queue opens the scheduler.
    var pendingScheduleClipId: UUID? = nil
}
