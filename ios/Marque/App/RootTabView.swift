import SwiftUI

struct RootTabView: View {
    @Environment(AppRouter.self) private var router
    @Environment(AppStore.self) private var store
    @Environment(TourManager.self) private var tour

    var body: some View {
        @Bindable var router = router
        @Bindable var store = store
        content(for: router.selectedTab)
            // Plain overlay, NOT safeAreaInset — inset reservation proved flaky with this
            // bar (screens ended up under it anyway); an overlay makes the geometry
            // deterministic and screens own their clearance via MarqueTabBar.clearance.
            .overlay(alignment: .bottom) {
                if !router.hideTabBar {
                    MarqueTabBar(selected: $router.selectedTab) {
                        router.showFilm = true
                    }
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }
            .animation(Motion.quick, value: router.hideTabBar)
            .onChange(of: router.selectedTab) { _, _ in router.hideTabBar = false }
            .background(Palette.surface.ignoresSafeArea())
            .sheet(isPresented: $store.showCelebration) { CelebrationView() }
            .fullScreenCover(isPresented: $router.showFilm) { NavigationStack { FilmView() } }
            // Sits above the tab bar + all tab content — resolves every `.tourAnchor`
            // tagged below (tab bar buttons, Home's voice bubble) into real screen rects.
            .tourOverlay { rects in
                TourOverlay(tour: tour, router: router, anchors: rects)
            }
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
