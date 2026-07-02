import SwiftUI
import StoreKit

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var showPaywall = false
    @State private var showDeleteConfirm = false
    @State private var restoring = false
    @State private var notifPublished = UserDefaults.standard.bool(forKey: "notif.published")
    @State private var notifRecap = UserDefaults.standard.bool(forKey: "notif.recap")

    var body: some View {
        @Bindable var store = store
        return NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {

                    // MARK: Subscription
                    settingsGroup("Subscription") {
                        // Pro hero card
                        Button { showPaywall = true } label: {
                            ZStack(alignment: .topTrailing) {
                                RadialGradient(colors: [Palette.accent.opacity(0.12), .clear],
                                               center: .topTrailing, startRadius: 0, endRadius: 170)
                                VStack(alignment: .leading, spacing: Space.sm) {
                                    SectionLabel(text: "Marque Pro", accent: Palette.accent)
                                    Text("Publish everything Marque makes for you.")
                                        .font(AppFont.serifL).foregroundStyle(Palette.textPrimary)
                                        .multilineTextAlignment(.leading)
                                        .fixedSize(horizontal: false, vertical: true)
                                    HStack(spacing: 6) {
                                        Text("Go Pro").font(AppFont.callout).foregroundStyle(Palette.accent)
                                        Image(systemName: "arrow.right")
                                            .font(.system(size: 12, weight: .semibold))
                                            .foregroundStyle(Palette.accent)
                                    }
                                    .padding(.top, 2)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(Space.md)
                            }
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("settings.upgrade")

                        Divider().padding(.leading, Space.md)

                        Button {
                            restoring = true
                            Task { try? await StoreKit.AppStore.sync(); restoring = false }
                        } label: {
                            row(restoring ? "Restoring…" : "Restore purchases", "arrow.clockwise")
                                .padding(.horizontal, Space.md)
                        }
                        .buttonStyle(.plain).disabled(restoring)

                        Divider().padding(.leading, Space.md)

                        Link(destination: URL(string: "https://apps.apple.com/account/subscriptions")!) {
                            row("Manage subscription", "creditcard")
                                .padding(.horizontal, Space.md)
                        }
                    }

                    // MARK: Notifications
                    settingsGroup("Notifications") {
                        Toggle(isOn: Binding(
                            get: { store.remindersEnabled },
                            set: { on in if on { store.requestRemindersAndEnable() } else { store.remindersEnabled = false } }
                        )) {
                            VStack(alignment: .leading, spacing: 1) {
                                Text("Daily film reminder").font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                                Text("A nudge each morning to keep your week full.")
                                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            }
                        }
                        .tint(Palette.accent)
                        .accessibilityIdentifier("settings.reminders")
                        .padding(.horizontal, Space.md).padding(.vertical, 10)

                        Divider().padding(.leading, Space.md)

                        Toggle(isOn: $notifPublished) {
                            VStack(alignment: .leading, spacing: 1) {
                                Text("Post published").font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                                Text("Know the moment a clip goes live.")
                                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            }
                        }
                        .tint(Palette.accent)
                        .onChange(of: notifPublished) { _, v in UserDefaults.standard.set(v, forKey: "notif.published") }
                        .padding(.horizontal, Space.md).padding(.vertical, 10)

                        Divider().padding(.leading, Space.md)

                        Toggle(isOn: $notifRecap) {
                            VStack(alignment: .leading, spacing: 1) {
                                Text("Weekly recap").font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                                Text("Your reach, top clip, and what to film next.")
                                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            }
                        }
                        .tint(Palette.accent)
                        .onChange(of: notifRecap) { _, v in UserDefaults.standard.set(v, forKey: "notif.recap") }
                        .padding(.horizontal, Space.md).padding(.vertical, 10)
                    }

                    // MARK: Accounts
                    settingsGroup("Accounts") {
                        VStack(alignment: .leading, spacing: Space.sm) {
                            Text("Link Instagram and TikTok so Marque can publish for you and learn from what works.")
                                .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            ConnectAccountsView()
                        }
                        .padding(Space.md)
                    }

                    // MARK: Content styles
                    settingsGroup("Content styles") {
                        VStack(alignment: .leading, spacing: Space.sm) {
                            Text("Which kinds of video should Marque write? Each gets its own script style.")
                                .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            StyleSelectionView(selected: $store.brand.preferredStyles)
                        }
                        .padding(Space.md)
                    }
                    .onChange(of: store.brand.preferredStyles) { _, _ in store.save() }

                    // MARK: Data & Privacy
                    settingsGroup("Data & Privacy") {
                        if let data = try? JSONEncoder().encode(store.brand),
                           let str = String(data: data, encoding: .utf8) {
                            ShareLink(item: str,
                                      subject: Text("Marque Brand Data"),
                                      message: Text("My Marque brand export")) {
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

                    // MARK: Account — deletion is an App Store requirement (5.1.1(v))
                    settingsGroup("Account") {
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
                        #endif
                    }

                    Text("Marque \(appVersion)")
                        .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, Space.lg)
                        .padding(.bottom, Space.xl)
                }
                .padding(.horizontal, Space.screenH)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .sheet(isPresented: $showPaywall) { PaywallView() }
            .alert("Delete account?", isPresented: $showDeleteConfirm) {
                Button("Cancel", role: .cancel) {}
                Button("Delete", role: .destructive) { store.resetAll(); dismiss() }
            } message: {
                Text("This permanently erases your brand, scripts, clips, and schedule from this device. This can't be undone.")
            }
        }
    }

    // MARK: - Helpers

    private var appVersion: String {
        let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0"
        let b = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        return b.isEmpty ? "v\(v)" : "v\(v) (\(b))"
    }

    @ViewBuilder
    private func row(_ title: String, _ icon: String, tint: Color = Palette.textPrimary) -> some View {
        HStack(spacing: Space.md) {
            Image(systemName: icon).font(.system(size: 16)).foregroundStyle(tint)
                .frame(width: 34, height: 34)
                .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(tint.opacity(0.08)))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(tint.opacity(0.10), lineWidth: 1))
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
