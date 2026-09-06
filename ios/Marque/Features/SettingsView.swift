import SwiftUI
import StoreKit

// Settings — grouped: Notifications, Subscription, Account (email + sign out + delete),
// Data & Privacy, Support & About.
//
// Visual language (build 62 polish): editorial kicker + Fraunces title up top (Library's
// header treatment), then card groups on surfaceRaised with hairline strokes and a soft
// warm shadow. Every row sits on the same grid — 13pt vertical padding, Space.md gutters,
// dividers inset to the text column — with Space.lg of air between groups.
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

    // Build 61: no `@Bindable var store` any more — the only two-way binding on this screen
    // was the Editing group, which now lives in Profile → Editing style.
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {

                    // Editorial inline header — kicker + Fraunces title (Library's signature)
                    VStack(alignment: .leading, spacing: Space.xs) {
                        Text("YOUR ACCOUNT & APP")
                            .font(AppFont.micro).tracking(Track.label)
                            .foregroundStyle(Palette.textTertiary)
                        Text("Settings")
                            .font(Typeface.sans(34, .bold)).tracking(-1)
                            .foregroundStyle(Palette.textPrimary)
                    }

                    // Identity card — leads with WHO before what/toggles, the way an
                    // Apple-ID-style settings screen does. Was a flat list starting
                    // straight into "Notifications"; this gives the screen a face.
                    HStack(spacing: Space.md) {
                        AccountAvatarMark(label: displayName, size: 52)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(displayName).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                .lineLimit(1)
                            if !store.brand.niche.isEmpty {
                                Text(store.brand.niche.capitalized)
                                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            }
                        }
                        Spacer()
                        if store.subscription.isSubscribed {
                            Chip(text: "Pro", tint: Palette.positive)
                        }
                    }
                    .padding(Space.md)
                    .background(Palette.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                    .shadow(color: Palette.shadowWarm.opacity(0.06), radius: 14, x: 0, y: 6)
                    .accessibilityIdentifier("settings.identityCard")

                    // Build 61: the "Editing" group moved WHOLESALE to Profile → Editing
                    // style. It was a partial duplicate of the record screen's per-take
                    // pickers (caption style lived in both, and the winner depended on
                    // which screen you touched last); the craft dials now have exactly one
                    // home, next to the sample reel that shows what they do.

                    // MARK: Notifications
                    settingsGroup("Notifications") {
                        MarqueToggleRow(title: "Daily film reminder",
                                        subtitle: "A nudge each morning to keep your week full.",
                                        isOn: Binding(
                                            get: { store.remindersEnabled },
                                            set: { on in if on { store.requestRemindersAndEnable() } else { store.remindersEnabled = false } }))
                            .accessibilityIdentifier("settings.reminders")
                            .padding(.horizontal, Space.md).padding(.vertical, 13)

                        textDivider

                        MarqueToggleRow(title: "Post published",
                                        subtitle: "Know the moment a clip goes live.",
                                        isOn: $notifPublished)
                            .onChange(of: notifPublished) { _, v in UserDefaults.standard.set(v, forKey: "notif.published") }
                            .padding(.horizontal, Space.md).padding(.vertical, 13)
                        // C-08: "Weekly recap" toggle removed — it wrote a UserDefaults key nothing
                        // consumed (no recap generator exists). "Post published" above now backs a
                        // real notification (C-03 retry-queue success path).
                    }

                    // MARK: Subscription
                    settingsGroup("Subscription") {
                        HStack(spacing: Space.md) {
                            ProMark()
                            VStack(alignment: .leading, spacing: Space.xxs) {
                                Text("Yunicorn Pro, \(monthlyPrice)")
                                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                Text("Billed monthly. Cancel anytime.")
                                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            }
                            Spacer()
                            if store.subscription.isSubscribed {
                                Chip(text: "Active", tint: Palette.positive)
                            }
                        }
                        .padding(.horizontal, Space.md).padding(.vertical, 13)
                        .accessibilityIdentifier("settings.currentPlan")

                        insetDivider

                        // Build 54 tier (renamed "Plus" in 55: the row above already sells
                        // "Yunicorn Pro" at a different price — two products, one name).
                        // Mock entitlement until StoreKit lands.
                        Button { showProPaywall = true } label: {
                            HStack(spacing: Space.md) {
                                PlusMark()
                                VStack(alignment: .leading, spacing: Space.xxs) {
                                    Text(entitlements.isPro ? "Yunicorn Plus, active"
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
                            .padding(.horizontal, Space.md).padding(.vertical, 13)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("settings.goPro")

                        insetDivider

                        Button {
                            restoring = true
                            Task { await store.subscription.restore(); restoring = false }
                        } label: {
                            row(restoring ? "Restoring…" : "Restore purchases", "arrow.clockwise")
                        }
                        .buttonStyle(.plain).disabled(restoring)
                        .accessibilityIdentifier("settings.restore")

                        insetDivider

                        Link(destination: URL(string: "https://apps.apple.com/account/subscriptions")!) {
                            row("Manage subscription", "creditcard")
                        }
                    }

                    // MARK: Account
                    settingsGroup("Account") {
                        HStack(spacing: Space.md) {
                            AccountAvatarMark(label: displayName)
                            VStack(alignment: .leading, spacing: Space.xxs) {
                                Text(store.auth.state?.email ?? "Demo account")
                                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                    .lineLimit(1)
                                Text("Signed in").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            }
                            Spacer()
                        }
                        .padding(.horizontal, Space.md).padding(.vertical, 13)
                        .accessibilityIdentifier("settings.accountEmail")

                        insetDivider

                        Button { showSignOutConfirm = true } label: {
                            row("Sign out", "rectangle.portrait.and.arrow.right", tint: Palette.critical)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("settings.signOut")

                        insetDivider

                        // Deletion is an App Store requirement (5.1.1(v))
                        Button(role: .destructive) { showDeleteConfirm = true } label: {
                            row("Delete account", "trash", tint: Palette.critical)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("settings.deleteAccount")

                        #if DEBUG
                        insetDivider
                        GhostButton(title: "Reset app to first run", systemImage: "arrow.counterclockwise") {
                            store.resetAll(); dismiss()
                        }
                        .padding(Space.md)

                        #if targetEnvironment(simulator)
                        // Simulator-only demo switch: try each paid tier without billing. The
                        // backend applies it only when ALLOW_DEV_TIER=1 (never on in prod).
                        textDivider
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
                                        demoTierInfo = "Active: \(newValue)" + (on.isEmpty ? "" : ", \(on)")
                                    } else {
                                        demoTierInfo = "Backend override off (set ALLOW_DEV_TIER=1)"
                                    }
                                }
                            }
                            if !demoTierInfo.isEmpty {
                                Text(demoTierInfo).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .padding(.horizontal, Space.md).padding(.vertical, 13)
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
                                row("Export my data", "square.and.arrow.up", mark: AnyView(PrivacyMark()))
                            }
                            .accessibilityIdentifier("settings.exportData")

                            insetDivider
                        }

                        Link(destination: LegalURLs.privacy) {
                            row("Privacy Policy", "hand.raised", tint: Palette.textSecondary)
                        }

                        insetDivider

                        Link(destination: LegalURLs.terms) {
                            row("Terms of Use", "doc.text", tint: Palette.textSecondary)
                        }
                    }

                    // MARK: Support & About
                    settingsGroup("Support & About") {
                        Button {
                            dismiss()
                            tour.start(router: router)
                        } label: {
                            row("Replay walkthrough", "arrow.triangle.2.circlepath", tint: Palette.accent)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("settings.replayTour")

                        insetDivider

                        Link(destination: LegalURLs.support) {
                            row("Support", "questionmark.circle", mark: AnyView(SupportMark()))
                        }

                        insetDivider

                        HStack {
                            Text("Version").font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                            Spacer()
                            Text(appVersion).font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        }
                        .padding(.horizontal, Space.md).padding(.vertical, 13)
                    }

                    Text("Yunicorn \(appVersion)")
                        .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, Space.sm)
                        .padding(.bottom, Space.xl)
                }
                .screenPadding()
                .padding(.top, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("")
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
        store.subscription.monthly.map { "\($0.displayPrice)/mo" } ?? "$19.99/mo"
    }

    /// The name shown on the identity card and the account-row avatar's initial —
    /// creator's own name first (collected at onboarding), then email, then a plain
    /// fallback for a fresh demo account with neither.
    private var displayName: String {
        let n = (store.brand.creatorName ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if !n.isEmpty { return n }
        if let email = store.auth.state?.email, !email.isEmpty { return email }
        return "Your account"
    }

    /// Hairline divider inset to the text column (past the 36pt icon tile + gutters),
    /// so the icon rail reads as one continuous column.
    private var insetDivider: some View {
        Divider().overlay(Palette.hairline)
            .padding(.leading, Space.md + 36 + Space.md)
    }

    /// Hairline divider for icon-less rows (toggles) — inset to the card's text margin.
    private var textDivider: some View {
        Divider().overlay(Palette.hairline)
            .padding(.leading, Space.md)
    }

    /// A standard tappable row: icon tile + title + trailing chevron, on the shared
    /// 13pt-vertical / Space.md-horizontal grid (padding lives HERE so every call site
    /// lands on the same rhythm). `mark` overrides the default tinted-glyph tile with a
    /// custom one (ProMark, PrivacyMark, ...) when the row deserves its own identity.
    @ViewBuilder
    private func row(_ title: String, _ icon: String, tint: Color = Palette.textSecondary,
                     mark: AnyView? = nil) -> some View {
        HStack(spacing: Space.md) {
            if let mark {
                mark
            } else if tint == Palette.critical {
                DestructiveMark(systemImage: icon)
            } else {
                UtilityMark(systemImage: icon, tint: tint)
            }
            Text(title).font(AppFont.headline)
                .foregroundStyle(tint == Palette.critical ? Palette.critical : Palette.textPrimary)
            Spacer()
            Image(systemName: "chevron.right")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Palette.textTertiary)
        }
        .contentShape(Rectangle())
        .padding(.horizontal, Space.md)
        .padding(.vertical, 13)
    }

    /// Kicker section label + a white card with hairline stroke and soft warm shadow.
    /// Vertical air between groups comes from the parent VStack's Space.lg spacing.
    @ViewBuilder
    private func settingsGroup<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            SectionLabel(text: title)
            VStack(spacing: 0) {
                content()
            }
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
            .shadow(color: Palette.shadowWarm.opacity(0.06), radius: 14, x: 0, y: 6)
        }
    }
}
