import SwiftUI

@main
struct MarqueApp: App {
    @State private var store = AppStore()
    @State private var router = AppRouter()

    init() {
        // Dev/Maestro hook: launch with -reset to wipe to first-run.
        if CommandLine.arguments.contains("-reset") {
            UserDefaults.standard.removeObject(forKey: "marque.state.v1")
        }
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(store)
                .environment(router)
                .tint(Palette.accent)
                .preferredColorScheme(.light)
        }
    }
}

struct RootView: View {
    @Environment(AppStore.self) private var store
    @StateObject private var net = NetworkMonitor()
    var body: some View {
        Group {
            if store.hasOnboarded {
                RootTabView()
            } else {
                OnboardingView()
            }
        }
        .animation(Motion.calm, value: store.hasOnboarded)
        .safeAreaInset(edge: .top) {
            if !net.isOnline { OfflineBanner() }
        }
    }
}
