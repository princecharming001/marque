import SwiftUI
import StoreKit

// Settings — grouped: Editing (EditPrefs → every AI edit), Notifications, Subscription,
// Account (email + sign out + delete), Data & Privacy, Support & About.
struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(TourManager.self) private var tour
    @Environment(\.dismiss) private var dismiss
    @State private var showDeleteConfirm = false
    @State private var showSignOutConfirm = false
    @State private var restoring = false
    @State private var showProPaywall = false          // build 54: Yunicorn Pro upsell sheet
    @State private var entitlements = Entitlements.shared
    @State private var notifPublished = UserDefaults.standard.bool(forKey: "notif.published")
    @State private var demoTier: String = UserDefaults.standard.string(forKey: "demo.tier") ?? "growth"
    @State private var demoTierInfo: String = ""

    var body: some View {
        @Bindable var store = store
        return NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {

                    // MARK: Editing — bound to store.editPrefs; didSet threads them into
                    // every clip job via BackendClient.editPrefs.
                    settingsGroup("Editing") {
                        MarqueToggleRow(title: "Auto-captions",
                                        subtitle: "Burn word-timed captions into every clip.",
                                        isOn: $store.editPrefs.autoCaptions)
                            .accessibilityIdentifier("settings.autoCaptions")
                            .padding(.horizontal, Space.md).padding(.vertical, 10)

                        Divider().padding(.leading, Space.md)

                        VStack(alignment: .leading, spacing: Space.sm) {
                            Text("Caption style").font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                            // Build 55: "Auto" (nil) = the AI picks per take — the honest default.
                            MarqueSegmented(options: ["Auto"] + CaptionStyle.allCases.map(\.label),
                                            index: Binding(get: {
                                                store.editPrefs.captionStyle
                                                    .flatMap { CaptionStyle.allCases.firstIndex(of: $0).map { $0 + 1 } } ?? 0
                                            },
                                                           set: { store.editPrefs.captionStyle = $0 == 0 ? nil : CaptionStyle.allCases[$0 - 1] }))
                                .accessibilityIdentifier("settings.captionStyle")
                        }
                        .padding(.horizontal, Space.md).padding(.vertical, 10)

                        Divider().padding(.leading, Space.md)

                        VStack(alignment: .leading, spacing: Space.sm) {
                            Text("Trim filler").font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                            MarqueSegmented(options: FillerTrim.allCases.map(\.label),
                                            index: Binding(get: { FillerTrim.allCases.firstIndex(of: store.editPrefs.fillerTrim) ?? 0 },
                                                           set: { store.editPrefs.fillerTrim = FillerTrim.allCases[$0] }))
                                .accessibilityIdentifier("settings.fillerTrim")
                        }
                        .padding(.horizontal, Space.md).padding(.vertical, 10)
                    }
                    .onChange(of: store.editPrefs) { _, _ in store.save() }

                    Text("Every edit follows these. Changes apply to your next submission.")
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        .padding(.top, Space.sm)

                    // MARK: Notifications
                    settingsGroup("Notifications") {
                        MarqueToggleRow(title: "Daily film reminder",
                                        subtitle: "A nudge each morning to keep your week full.",
                                        isOn: Binding(
                                            get: { store.remindersEnabled },
                                            set: { on in if on { store.requestRemindersAndEnable() } else { store.remindersEnabled = false } }))
                            .accessibilityIdentifier("settings.reminders")
                            .padding(.horizontal, Space.md).padding(.vertical, 10)

                        Divider().padding(.leading, Space.md)

                        MarqueToggleRow(title: "Post published",
                                        subtitle: "Know the moment a clip goes live.",
                                        isOn: $notifPublished)
                            .onChange(of: notifPublished) { _, v in UserDefaults.standard.set(v, forKey: "notif.published") }
                            .padding(.horizontal, Space.md).padding(.vertical, 10)
                        // C-08: "Weekly recap" toggle removed — it wrote a UserDefaults key nothing
                        // consumed (no recap generator exists). "Post published" above now backs a
                        // real notification (C-03 retry-queue success path).
                    }

                    // MARK: Subscription
                    settingsGroup("Subscription") {
                        HStack(spacing: Space.md) {
                            iconTile("crown", tint: Palette.accent)
                            VStack(alignment: .leading, spacing: 1) {
                                Text("Yunicorn Pro — \(monthlyPrice)")
                                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                Text("Billed monthly. Cancel anytime.")
                                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            }
                            Spacer()
                            if store.subscription.isSubscribed {
                                Chip(text: "Active", tint: Palette.positive)
                            }
                        }
                        .padding(.horizontal, Space.md).padding(.vertical, 10)
                        .accessibilityIdentifier("settings.currentPlan")

                        Divider().padding(.leading, Space.md)

                        // Build 54 tier (renamed "Plus" in 55: the row above already sells
                        // "Yunicorn Pro" at a different price — two products, one name).
                        // Mock entitlement until StoreKit lands.
                        Button { showProPaywall = true } label: {
                            HStack(spacing: Space.md) {
                                iconTile("sparkles", tint: Palette.accent)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(entitlements.isPro ? "Yunicorn Plus — active"
                                                            : "Go Plus")
                                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                    Text(entitlements.isPro ? "Clean exports, every look, priority renders."
                                                            : "Remove the watermark from your exports.")
                                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                                }
                                Spacer()
                                if entitlements.isPro {
                                    Chip(text: "Plus", tint: Palette.positive)
                                } else {
                                    Image(systemName: "chevron.right")
                                        .font(.system(size: 12, weight: .semibold))
                                        .foregroundStyle(Palette.textTertiary)
                                }
                            }
                            .padding(.horizontal, Space.md).padding(.vertical, 10)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("settings.goPro")

                        Divider().padding(.leading, Space.md)

                        Button {
                            restoring = true
                            Task { await store.subscription.restore(); restoring = false }
                        } label: {
                            row(restoring ? "Restoring…" : "Restore purchases", "arrow.clockwise")
                                .padding(.horizontal, Space.md)
                        }
                        .buttonStyle(.plain).disabled(restoring)
                        .accessibilityIdentifier("settings.restore")

                        Divider().padding(.leading, Space.md)

                        Link(destination: URL(string: "https://apps.apple.com/account/subscriptions")!) {
                            row("Manage subscription", "creditcard")
                                .padding(.horizontal, Space.md)
                        }
                    }

                    // MARK: Account
                    settingsGroup("Account") {
                        HStack(spacing: Space.md) {
                            iconTile("person")
                            VStack(alignment: .leading, spacing: 1) {
                                Text(store.auth.state?.email ?? "Demo account")
                                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                    .lineLimit(1)
                                Text("Signed in").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            }
                            Spacer()
                        }
                        .padding(.horizontal, Space.md).padding(.vertical, 10)
                        .accessibilityIdentifier("settings.accountEmail")

                        Divider().padding(.leading, Space.md)

                        Button { showSignOutConfirm = true } label: {
                            row("Sign out", "rectangle.portrait.and.arrow.right", tint: Palette.critical)
                                .padding(.horizontal, Space.md)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("settings.signOut")

                        Divider().padding(.leading, Space.md)

                        // Deletion is an App Store requirement (5.1.1(v))
                        Button(role: .destructive) { showDeleteConfirm = true } label: {
                            row("Delete account", "trash", tint: Palette.critical)
                                .padding(.horizontal, Space.md)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("settings.deleteAccount")

                        #if DEBUG
                        Divider().padding(.leading, Space.md)
                        GhostButton(title: "Reset app to first run", systemImage: "arrow.counterclockwise") {
                            store.resetAll(); dismiss()
                        }
                        .padding(Space.md)

                        #if targetEnvironment(simulator)
                        // Simulator-only demo switch: try each paid tier without billing. The
                        // backend applies it only when ALLOW_DEV_TIER=1 (never on in prod).
                        Divider().padding(.leading, Space.md)
                        VStack(alignment: .leading, spacing: Space.sm) {
                            Text("Demo tier (simulator only)")
                                .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                            Picker("Demo tier", selection: $demoTier) {
                                Text("Starter").tag("starter")
                                Text("Growth").tag("growth")
                                Text("Studio").tag("studio")
                            }
                            .pickerStyle(.segmented)
                            .accessibilityIdentifier("settings.demoTier")
                            .onChange(of: demoTier) { _, newValue in
                                UserDefaults.standard.set(newValue, forKey: "demo.tier")
                                store.subscription.devContinue()   // unlock paid UI for the demo
                                Task {
                                    if let info = await store.backend.setDevTier(newValue),
                                       let ents = info["entitlements"] as? [String: Any] {
                                        let on = ents.filter { ($0.value as? Bool) == true }
                                            .keys.sorted().joined(separator: ", ")
                                        demoTierInfo = "Active: \(newValue)" + (on.isEmpty ? "" : " — \(on)")
                                    } else {
                                        demoTierInfo = "Backend override off (set ALLOW_DEV_TIER=1)"
                                    }
                                }
                            }
                            if !demoTierInfo.isEmpty {
                                Text(demoTierInfo).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .padding(.horizontal, Space.md).padding(.vertical, 10)
                        #endif
                        #endif
                    }

                    // MARK: Data & Privacy
                    settingsGroup("Data & Privacy") {
                        if let data = try? JSONEncoder().encode(store.brand),
                           let str = String(data: data, encoding: .utf8) {
                            ShareLink(item: str,
                                      subject: Text("Yunicorn Brand Data"),
                                      message: Text("My Yunicorn brand export")) {
                                row("Export my data", "square.and.arrow.up")
                                    .padding(.horizontal, Space.md)
                            }
                            .accessibilityIdentifier("settings.exportData")
                        }

                        Divider().padding(.leading, Space.md)

                        Link(destination: LegalURLs.privacy) {
                            row("Privacy Policy", "hand.raised")
                                .padding(.horizontal, Space.md)
                        }

                        Divider().padding(.leading, Space.md)

                        Link(destination: LegalURLs.terms) {
                            row("Terms of Use", "doc.text")
                                .padding(.horizontal, Space.md)
                        }
                    }

                    // MARK: Support & About
                    settingsGroup("Support & About") {
                        Button {
                            dismiss()
                            tour.start(router: router)
                        } label: {
                            row("Replay walkthrough", "sparkles")
                                .padding(.horizontal, Space.md)
                        }
                        .accessibilityIdentifier("settings.replayTour")

                        Divider().padding(.leading, Space.md)

                        Link(destination: LegalURLs.support) {
                            row("Support", "questionmark.circle")
                                .padding(.horizontal, Space.md)
                        }

                        Divider().padding(.leading, Space.md)

                        HStack {
                            Text("Version").font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                            Spacer()
                            Text(appVersion).font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        }
                        .padding(.horizontal, Space.md).padding(.vertical, 12)
                    }

                    Text("Yunicorn \(appVersion)")
                        .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, Space.lg)
                        .padding(.bottom, Space.xl)
                }
                .padding(.horizontal, Space.screenH)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Settings")
            .sheet(isPresented: $showProPaywall) { YunicornProPaywall() }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .marqueConfirm($showSignOutConfirm, title: "Sign out?", message: "Your brand stays on this device.",
                           confirm: "Sign out", destructive: true) {
                store.auth.signOut(); dismiss()      // gate machine swaps to the auth wall automatically
            }
            .marqueConfirm($showDeleteConfirm, title: "Delete account?",
                           message: "This permanently erases your brand, scripts, clips, and schedule from this device. This can't be undone.",
                           confirm: "Delete", destructive: true) {
                store.resetAll(); dismiss()
            }
        }
    }

    // MARK: - Helpers

    private var appVersion: String {
        let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0"
        let b = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        return b.isEmpty ? "v\(v)" : "v\(v) (\(b))"
    }

    private var monthlyPrice: String {
        store.subscription.monthly.map { "\($0.displayPrice)/mo" } ?? "$14.99/mo"
    }

    private func iconTile(_ icon: String, tint: Color = Palette.textPrimary) -> some View {
        Image(systemName: icon).font(.system(size: 16)).foregroundStyle(tint)
            .frame(width: 34, height: 34)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(tint.opacity(0.08)))
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(tint.opacity(0.10), lineWidth: 1))
    }

    @ViewBuilder
    private func row(_ title: String, _ icon: String, tint: Color = Palette.textPrimary) -> some View {
        HStack(spacing: Space.md) {
            iconTile(icon, tint: tint)
            Text(title).font(AppFont.headline)
                .foregroundStyle(tint == Palette.critical ? Palette.critical : Palette.textPrimary)
            Spacer()
            Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
        }
        .contentShape(Rectangle())
        .padding(.vertical, 6)
    }

    @ViewBuilder
    private func settingsGroup<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(title.uppercased())
                .font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
                .padding(.top, Space.lg).padding(.bottom, Space.sm)
            VStack(spacing: 0) {
                content()
            }
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
        }
    }
}
