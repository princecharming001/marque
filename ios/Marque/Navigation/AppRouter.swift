import SwiftUI
import Observation

enum AppTab: Hashable {
    case today, queue, library, you
}

@MainActor
@Observable
final class AppRouter {
    var selectedTab: AppTab = .today
    var hideTabBar = false
    var showCreate = false
    var pendingScheduleClipId: UUID? = nil
    var pendingQueueDate: Date? = nil
}
