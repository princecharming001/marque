import SwiftUI
import StoreKit

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var showPaywall = false
    @State private var showDeleteConfirm = false
    @State private var restoring = false

    var body: some View {
        @Bindable var store = store
        return NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    // Upgrade
                    Button { showPaywall = true } label: {
                        HStack {
                            Text("Upgrade to Pro").font(AppFont.headline).foregroundStyle(Palette.onInk)
                            Spacer()
                            Image(systemName: "sparkles").foregroundStyle(Palette.onInk)
                        }
                        .padding(Space.md)
                        .background(Palette.ink)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("settings.upgrade")

                    // Accounts — connectable after onboarding, not only during it
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionTitle(text: "Accounts")
                        Text("Link Instagram and TikTok so Marque can publish for you and learn from what works.")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        ConnectAccountsView()
                    }

                    // Content styles
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionTitle(text: "Content styles")
                        Text("Which kinds of video should Marque write? Each gets its own script style.")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        StyleSelectionView(selected: $store.brand.preferredStyles)
                    }
                    .onChange(of: store.brand.preferredStyles) { _, _ in store.save() }

                    // Notifications — powers the consistency promise
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionTitle(text: "Notifications")
                        Toggle(isOn: Binding(
                            get: { store.remindersEnabled },
                            set: { on in if on { store.requestRemindersAndEnable() } else { store.remindersEnabled = false } }
                        )) {
                            VStack(alignment: .leading, spacing: 1) {
                                Text("Daily film reminder").font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                                Text("A nudge each morning to keep your week full.").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            }
                        }
                        .tint(Palette.accent)
                        .accessibilityIdentifier("settings.reminders")
                    }

                    // Subscription
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionTitle(text: "Subscription")
                        Button {
                            restoring = true
                            Task { try? await StoreKit.AppStore.sync(); restoring = false }
                        } label: {
                            row(restoring ? "Restoring…" : "Restore purchases", "arrow.clockwise")
                        }
                        .buttonStyle(.plain).disabled(restoring)
                        Link(destination: URL(string: "https://apps.apple.com/account/subscriptions")!) {
                            row("Manage subscription", "creditcard")
                        }
                    }

                    // Legal
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionTitle(text: "Legal")
                        Link(destination: LegalURLs.privacy) { row("Privacy Policy", "hand.raised") }
                        Link(destination: LegalURLs.terms) { row("Terms of Use", "doc.text") }
                        Link(destination: LegalURLs.support) { row("Support", "questionmark.circle") }
                    }

                    // Account — deletion is an App Store requirement (5.1.1(v))
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionTitle(text: "Account")
                        Button(role: .destructive) { showDeleteConfirm = true } label: {
                            row("Delete account", "trash", tint: Palette.critical)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("settings.deleteAccount")
                        #if DEBUG
                        GhostButton(title: "Reset app to first run", systemImage: "arrow.counterclockwise") {
                            store.resetAll(); dismiss()
                        }
                        #endif
                    }

                    Text("Marque \(appVersion)")
                        .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, Space.sm)
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.surface.ignoresSafeArea())
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

    private var appVersion: String {
        let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0"
        let b = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        return b.isEmpty ? "v\(v)" : "v\(v) (\(b))"
    }

    private func row(_ title: String, _ icon: String, tint: Color = Palette.textPrimary) -> some View {
        HStack(spacing: Space.md) {
            Image(systemName: icon).foregroundStyle(tint).frame(width: 22)
            Text(title).font(AppFont.body).foregroundStyle(tint)
            Spacer()
            Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
        }
        .contentShape(Rectangle())
        .padding(.vertical, 2)
    }
}
