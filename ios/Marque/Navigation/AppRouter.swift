import SwiftUI
import Observation

enum AppTab: Hashable {
    case today, studio, library, calendar, coach
}

@MainActor
@Observable
final class AppRouter {
    var selectedTab: AppTab = .today
    var hideTabBar = false   // hidden while a detail with its own bottom CTA is up
}
