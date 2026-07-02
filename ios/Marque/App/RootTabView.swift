import SwiftUI

struct RootTabView: View {
    @Environment(AppRouter.self) private var router
    @Environment(AppStore.self) private var store

    var body: some View {
        @Bindable var router = router
        @Bindable var store = store
        content(for: router.selectedTab)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if !router.hideTabBar {
                    MarqueTabBar(selected: $router.selectedTab) {
                        router.showCreate = true
                    }
                }
            }
            .onChange(of: router.selectedTab) { _, _ in router.hideTabBar = false }
            .background(Palette.surface.ignoresSafeArea())
            .sheet(isPresented: $store.showCelebration) { CelebrationView() }
            .fullScreenCover(isPresented: $router.showCreate) { NavigationStack { StudioView() } }
    }

    @ViewBuilder
    private func content(for tab: AppTab) -> some View {
        switch tab {
        case .today: NavigationStack { TodayView() }
        case .queue: NavigationStack { QueueView() }
        case .library: NavigationStack { LibraryView() }
        case .you: NavigationStack { YouView() }
        }
    }
}
