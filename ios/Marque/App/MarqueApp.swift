import SwiftUI
import AVFoundation

@main
struct MarqueApp: App {
    @State private var store = AppStore()
    @State private var router = AppRouter()
    @State private var tour = TourManager()
    // UX-F1: the feed survives tab switches + relaunches (RootTabView is a ViewBuilder
    // switch, so HomeView is torn down per tab — a view-owned FeedStore lost everything).
    @State private var feed = FeedStore()

    init() {
        // Dev/Maestro hook: launch with -reset to wipe to first-run.
        // (AuthManager clears marque.auth.v1 on the same flag.)
        if CommandLine.arguments.contains("-reset") {
            UserDefaults.standard.removeObject(forKey: "marque.state.v1")
            UserDefaults.standard.removeObject(forKey: "dev.subscribed")
            UserDefaults.standard.removeObject(forKey: "mock.subscribed")
            UserDefaults.standard.removeObject(forKey: "marque.digest.jobId")
        }
        // Without an explicit category, iOS defaults every AVPlayer to .soloAmbient,
        // which the hardware ring/silent switch silences — reel previews (Home,
        // Library) played fine on screen but made no sound whenever the phone was on
        // silent. .playback matches TikTok/Instagram: audio plays regardless of the
        // switch. Voice dictation/TTS still borrow the session temporarily and
        // restore this baseline when they're done (SpeechRecognizer, VoicePlayback).
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback)
        try? AVAudioSession.sharedInstance().setActive(true)
    }

    var body: some Scene {
        WindowGroup {
            ZStack {
                RootView()
                #if DEBUG
                // Sibling of RootView, NOT an overlay on its gate Group — attaching it
                // there reset OnboardingView's @State (step → 0) whenever the keyboard
                // relaid out the overlay. As a ZStack sibling it can't touch the gate
                // machine's view identity.
                DevJumpMenu()
                #endif
            }
            .environment(store)
            .environment(router)
            .environment(tour)
            .environment(feed)
            .tint(Palette.accent)
            .preferredColorScheme(.light)
        }
    }
}

// Gate machine: onboarding → subscription wall → account wall → the app.
// Paywall BEFORE auth (conversion order): commit first, then "Save your brand"
// is literally saving the plan the digest just built.
struct RootView: View {
    @Environment(AppStore.self) private var store
    @Environment(FeedStore.self) private var feed
    @StateObject private var net = NetworkMonitor()
    @Environment(\.scenePhase) private var scenePhase
    var body: some View {
        Group {
            if !store.hasOnboarded {
                OnboardingView()
            } else if !store.subscription.isSubscribed {
                SubscriptionGateView()
            } else if !store.auth.isAuthed {
                AuthGateView()
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
        // C-03: retry transport-failed publishes when the app returns to the foreground
        // or the network comes back — a queued post lands the moment we can reach the API.
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                Task { await store.retryPendingPublishes() }
                // UX-F2: foregrounding with a >15min-stale feed → silent revalidate
                // (no skeletons; the snapshot keeps painting until fresh data lands).
                Task { await feed.revalidateIfStale(store: store) }
            }
        }
        .onChange(of: net.isOnline) { _, online in
            if online { Task { await store.retryPendingPublishes() } }
        }
        // C-13: on cold-start-when-signed-in and whenever the creator signs in
        // (userId changes), pull their cloud snapshot — restores state after a
        // reinstall. No-op unless Supabase is configured and local is empty.
        .task(id: store.auth.state?.userId) {
            if store.auth.isAuthed { await store.restoreFromCloud() }
        }
    }
}

#if DEBUG
// Floating dev-only jump menu — a ZStack sibling of RootView so it's reachable from any
// app state without touching the gate machine's view identity. Pinned bottom-trailing,
// keyboard-ignoring so it never relocates. DEBUG builds only; never ships.
private struct DevJumpMenu: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var showMenu = false

    var body: some View {
        Button { showMenu = true } label: {
            Image(systemName: "hammer.fill")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Palette.onInk)
                .frame(width: 34, height: 34)
                .background(Circle().fill(Palette.ink.opacity(0.55)))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomTrailing)
        .padding(.trailing, 6)
        .padding(.bottom, 150)
        .ignoresSafeArea(.keyboard)
        .accessibilityIdentifier("dev.jump")
        .confirmationDialog("Dev: jump to", isPresented: $showMenu, titleVisibility: .visible) {
            Button("Onboarding") { jumpToOnboarding() }
            Button("Home") { jumpToHome() }
            Button("Cancel", role: .cancel) {}
        }
    }

    /// Replays the onboarding flow. Auth + subscription are left intact, so finishing
    /// the quiz drops straight back into the app without re-hitting the gates.
    private func jumpToOnboarding() {
        store.hasOnboarded = false
        store.save()
    }

    /// Forces every gate open and lands on the Home tab.
    private func jumpToHome() {
        store.hasOnboarded = true
        if !store.auth.isAuthed { store.auth.continueAsDemo() }
        if !store.subscription.isSubscribed { store.subscription.devContinue() }
        router.showFilm = false
        router.selectedTab = .home
        store.save()
    }
}
#endif
