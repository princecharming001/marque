import SwiftUI

struct RootTabView: View {
    @Environment(AppRouter.self) private var router
    @Environment(AppStore.self) private var store

    var body: some View {
        @Bindable var router = router
        @Bindable var store = store
        TabView(selection: $router.selectedTab) {
            NavigationStack { TodayView() }
                .tabItem { Label("Today", systemImage: "sun.max") }
                .tag(AppTab.today)

            NavigationStack { StudioView() }
                .tabItem { Label("Studio", systemImage: "square.grid.2x2") }
                .tag(AppTab.studio)

            NavigationStack { LibraryView() }
                .tabItem { Label("Library", systemImage: "rectangle.stack") }
                .tag(AppTab.library)

            NavigationStack { CalendarView() }
                .tabItem { Label("Calendar", systemImage: "calendar") }
                .tag(AppTab.calendar)

            NavigationStack { CoachView() }
                .tabItem { Label("Coach", systemImage: "bubble.left.and.text.bubble.right") }
                .tag(AppTab.coach)
        }
        .tint(Palette.ink)
        .sheet(isPresented: $store.showCelebration) { CelebrationView() }
    }
}
