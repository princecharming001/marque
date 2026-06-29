import SwiftUI

struct RootTabView: View {
    @Environment(AppRouter.self) private var router
    @Environment(AppStore.self) private var store

    var body: some View {
        @Bindable var router = router
        @Bindable var store = store
        content(for: router.selectedTab)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if !router.hideTabBar { MarqueTabBar(selected: $router.selectedTab) }
            }
            .onChange(of: router.selectedTab) { _, _ in router.hideTabBar = false }
            .background(Palette.surface.ignoresSafeArea())
            .sheet(isPresented: $store.showCelebration) { CelebrationView() }
    }

    @ViewBuilder
    private func content(for tab: AppTab) -> some View {
        switch tab {
        case .today: NavigationStack { TodayView() }
        case .studio: NavigationStack { StudioView() }
        case .library: NavigationStack { LibraryView() }
        case .calendar: NavigationStack { CalendarView() }
        case .coach: NavigationStack { CoachView() }
        }
    }
}
