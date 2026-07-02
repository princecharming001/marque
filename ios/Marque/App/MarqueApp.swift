import SwiftUI

@main
struct MarqueApp: App {
    @State private var store = AppStore()
    @State private var router = AppRouter()

    init() {
        // Dev/Maestro hook: launch with -reset to wipe to first-run.
        // (AuthManager clears marque.auth.v1 on the same flag.)
        if CommandLine.arguments.contains("-reset") {
            UserDefaults.standard.removeObject(forKey: "marque.state.v1")
            UserDefaults.standard.removeObject(forKey: "dev.subscribed")
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

// Gate machine: onboarding → account wall → subscription wall → the app.
struct RootView: View {
    @Environment(AppStore.self) private var store
    @StateObject private var net = NetworkMonitor()
    var body: some View {
        Group {
            if !store.hasOnboarded {
                OnboardingView()
            } else if !store.auth.isAuthed {
                AuthGateView()
            } else if !store.subscription.isSubscribed {
                SubscriptionGateView()
            } else {
                RootTabView()
            }
        }
        .animation(Motion.calm, value: store.hasOnboarded)
        .animation(Motion.calm, value: store.auth.isAuthed)
        .animation(Motion.calm, value: store.subscription.isSubscribed)
        .safeAreaInset(edge: .top) {
            if !net.isOnline { OfflineBanner() }
        }
    }
}
