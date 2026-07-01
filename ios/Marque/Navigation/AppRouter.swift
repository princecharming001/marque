import SwiftUI
import Observation

enum AppTab: Hashable {
    case home, plan, library, coach
}

@MainActor
@Observable
final class AppRouter {
    var selectedTab: AppTab = .home
    var hideTabBar = false   // hidden while a detail with its own bottom CTA is up
    var showCreate = false   // raised center FAB → presents StudioView modally
}
