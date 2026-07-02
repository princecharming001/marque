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
                        router.showFilm = true
                    }
                }
            }
            .onChange(of: router.selectedTab) { _, _ in router.hideTabBar = false }
            .background(Palette.surface.ignoresSafeArea())
            .sheet(isPresented: $store.showCelebration) { CelebrationView() }
            .fullScreenCover(isPresented: $router.showFilm) { NavigationStack { FilmView() } }
    }

    @ViewBuilder
    private func content(for tab: AppTab) -> some View {
        switch tab {
        case .home: NavigationStack { HomeView() }
        case .chat: NavigationStack { ChatView() }
        case .library: NavigationStack { LibraryView() }
        case .performance: NavigationStack { PerformanceView() }
        }
    }
}
