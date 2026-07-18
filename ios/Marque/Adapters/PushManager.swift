import SwiftUI
import UIKit
import UserNotifications

// UX-B2b: APNs plumbing. Local notifications only fire while the app is alive (the
// in-app poll loop) — a creator who backgrounds the app during the 1-3 min edit never
// hears their clip land. This delegate registers the device token with the backend
// (POST /v1/devices) so the server can push "Your clip is ready" with a
// marque://library/clip/{id} deeplink, and routes notification taps into the app.
final class PushManager: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    static private(set) var shared: PushManager?

    /// Assigned by MarqueApp so notification taps route through AppRouter.
    var onDeepLink: ((URL) -> Void)?
    /// Job ids whose clips_ready push already reached us this session — the local
    /// fallback (AppStore.notifyClipsReady) checks this to avoid double-notifying.
    private(set) var receivedJobIds = Set<String>()

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        PushManager.shared = self
        UNUserNotificationCenter.current().delegate = self
        registerIfAuthorized()
        return true
    }

    /// Register for remote notifications on every launch when the creator has already
    /// granted permission (tokens rotate; Apple wants registration each launch). The
    /// PERMISSION ask itself lives in PushPrimerSheet — never a cold system prompt.
    func registerIfAuthorized() {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized
                    || settings.authorizationStatus == .provisional else { return }
            DispatchQueue.main.async { UIApplication.shared.registerForRemoteNotifications() }
        }
    }

    func application(_ application: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { await BackendClient.shared.registerDevice(token: token) }
    }

    func application(_ application: UIApplication,
                     didFailToRegisterForRemoteNotificationsWithError error: Error) {
        // Fail-soft: local notifications remain the fallback.
        print("[push] remote registration failed: \(error.localizedDescription)")
    }

    // Build 49 — background-upload relaunch contract. When a background PUT completes while
    // the app is suspended/terminated, iOS relaunches us and calls this with the session's
    // identifier. Handing the completion handler to BackgroundUploader lets its delegate
    // drain the pending events (updating the upload journal) and then signal the system that
    // we're done, so the OS can suspend us again cleanly. Without this the completed transfer
    // is never surfaced and iOS deprioritizes the session.
    func application(_ application: UIApplication,
                     handleEventsForBackgroundURLSession identifier: String,
                     completionHandler: @escaping () -> Void) {
        guard identifier == BackgroundUploader.sessionIdentifier else {
            completionHandler(); return
        }
        BackgroundUploader.shared.setSystemCompletionHandler(completionHandler)
    }

    // Foreground arrival: suppress the clips_ready banner (the in-app poll loop +
    // celebration already covers the moment) but remember the job id for dedup.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        let info = notification.request.content.userInfo
        if let jobId = info["job_id"] as? String { receivedJobIds.insert(jobId) }
        // Suppress the clips_ready banner in the foreground for BOTH the remote push AND
        // the local fallback (identified by category now, not just the push trigger). While
        // the app is open the Library updates live, so a banner is redundant — and one that
        // pops on another screen while a clip is mid-render reads as "premature." When the
        // app is backgrounded, willPresent isn't called, so the notification still delivers.
        if notification.request.content.categoryIdentifier == "clips_ready" {
            completionHandler([])
        } else {
            completionHandler([.banner, .sound])
        }
    }

    // Tap on a notification (push or local): follow the deeplink into the Library.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        if let jobId = info["job_id"] as? String { receivedJobIds.insert(jobId) }
        if let link = info["deeplink"] as? String, let url = URL(string: link) {
            DispatchQueue.main.async { self.onDeepLink?(url) }
        }
        completionHandler()
    }
}

// MARK: - Push primer (UX-B2b) — the permission ask, never a cold system prompt.
// Shown once at the first clips-ready moment: explain WHY, then request. UserDefaults
// cooldown (72h) + max 3 lifetime shows; never once authorized or hard-denied.
enum PushPrimer {
    private static let countKey = "marque.pushPrimer.count"
    private static let lastKey = "marque.pushPrimer.lastShownAt"

    /// Whether the primer may be shown right now (status must still be undetermined).
    static func shouldShow(status: UNAuthorizationStatus) -> Bool {
        guard status == .notDetermined else { return false }
        let d = UserDefaults.standard
        guard d.integer(forKey: countKey) < 3 else { return false }
        let last = d.double(forKey: lastKey)
        return last == 0 || Date().timeIntervalSince1970 - last > 72 * 3600
    }

    static func markShown() {
        let d = UserDefaults.standard
        d.set(d.integer(forKey: countKey) + 1, forKey: countKey)
        d.set(Date().timeIntervalSince1970, forKey: lastKey)
    }
}

struct PushPrimerSheet: View {
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(spacing: Space.lg) {
            Image(systemName: "bell.badge")
                .font(.system(size: 40, weight: .medium))
                .foregroundStyle(Palette.accent)
                .padding(.top, Space.xl)
            Text("Know the moment your clip lands")
                .font(AppFont.title).foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
            Text("Editing takes a couple of minutes. Turn on notifications and we'll tell you the second it's ready — even if you've closed the app.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
            Button {
                UNUserNotificationCenter.current()
                    .requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
                        if granted {
                            DispatchQueue.main.async {
                                UIApplication.shared.registerForRemoteNotifications()
                            }
                        }
                    }
                dismiss()
            } label: {
                Text("Turn on notifications")
                    .font(AppFont.headline).foregroundStyle(Palette.onInk)
                    .frame(maxWidth: .infinity).padding(.vertical, Space.lg)
                    .background(Palette.ink)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("pushPrimer.enable")
            Button { dismiss() } label: {
                Text("Not now").font(AppFont.callout).foregroundStyle(Palette.textTertiary)
            }
            .buttonStyle(.plain)
            .padding(.bottom, Space.lg)
        }
        .padding(.horizontal, Space.xl)
        .presentationDetents([.medium])
        .onAppear { PushPrimer.markShown() }
    }
}
