import SwiftUI
import Observation

enum AppTab: Hashable {
    case today, studio, library, calendar, coach
}

@MainActor
@Observable
final class AppRouter {
    var selectedTab: AppTab = .today
}
