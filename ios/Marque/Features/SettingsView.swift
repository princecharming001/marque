import SwiftUI
import StoreKit

// Settings — grouped: Notifications, Subscription, Account (email + sign out + delete),
// Data & Privacy, Support & About.
//
// Visual language: Stoic's "your profile." sheet (DESIGN.md §5 list rows, §6 sheet).
// An eyebrow over the centered title, an identity card, then eyebrow sections of grouped
// 52pt rows on surface cards (no strokes, no shadow), the Plus upsell as a night promo
// strip under the subscription rows, and a version caption at the foot. Monochrome:
// status is carried by glyph + wording ("Active", "Pro"), destructive rows by their
// glyph and the confirm dialog.
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
                VStack(alignment: .leading, spacing: Space.xl) {

                    // Sheet header — eyebrow + centered title. "Settings" stays verbatim
                    // (a Maestro flow asserts it).
                    VStack(spacing: Space.xs) {
                        DSEyebrow(text: "YOUR ACCOUNT & APP")
                        Text("Settings")
                            .font(AppFont.title1).tracking(-0.3)
                            .foregroundStyle(Palette.textPrimary)
                            .accessibilityAddTraits(.isHeader)
                    }
                    .frame(maxWidth: .infinity)

                    // Identity card — leads with WHO before what/toggles, the way an
                    // Apple-ID-style settings screen does.
                    HStack(spacing: Space.md) {
                        AccountAvatarMark(label: displayName, size: 52)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(displayName).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                .lineLimit(1)
                            if !store.brand.niche.isEmpty {
                                Text(store.brand.niche.capitalized)
                                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                                    .lineLimit(1)
                            }
                        }
                        Spacer(minLength: Space.sm)
                        if store.subscription.isSubscribed {
                            statusPill("Pro")
                        }
                    }
                    .padding(Space.rowPad)
                    .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                        .fill(Palette.surface))
                    .accessibilityElement(children: .combine)
                    .accessibilityIdentifier("settings.identityCard")

                    // Build 61: the "Editing" group moved WHOLESALE to Profile → Editing
                    // style; the craft dials now have exactly one home, next to the sample
                    // reel that shows what they do.

                    // MARK: Notifications
                    settingsGroup("Notifications") {
                        DSToggleRow(title: "Daily film reminder",
                                    subtitle: "A nudge each morning to keep your week full.",
                                    isOn: Binding(
                                        get: { store.remindersEnabled },
                                        set: { on in if on { store.requestRemindersAndEnable() } else { store.remindersEnabled = false } }))
                            .padding(.vertical, 6)
                            .accessibilityIdentifier("settings.reminders")

                        textDivider

                        DSToggleRow(title: "Post published",
                                    subtitle: "Know the moment a clip goes live.",
                                    isOn: $notifPublished)
                            .padding(.vertical, 6)
                            .onChange(of: notifPublished) { _, v in UserDefaults.standard.set(v, forKey: "notif.published") }
                        // C-08: "Weekly recap" toggle removed — it wrote a UserDefaults key nothing
                        // consumed (no recap generator exists). "Post published" above now backs a
                        // real notification (C-03 retry-queue success path).
                    }

                    // MARK: Subscription
                    VStack(alignment: .leading, spacing: Space.groupGap) {
                        settingsGroup("Subscription") {
                            HStack(spacing: Space.md) {
                                ProMark()
                                VStack(alignment: .leading, spacing: Space.xxs) {
                                    Text("Yunicorn Pro, \(monthlyPrice)")
                                        .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                                    Text("Billed monthly. Cancel anytime.")
                                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                                }
                                Spacer(minLength: Space.sm)
                                if store.subscription.isSubscribed {
                                    statusPill("Active")
                                }
                            }
                            .padding(.horizontal, Space.rowPad).padding(.vertical, 10)
                            .frame(minHeight: 52)
                            .accessibilityElement(children: .combine)
                            .accessibilityIdentifier("settings.currentPlan")

                            insetDivider

                            Button {
                                restoring = true
                                Task { await store.subscription.restore(); restoring = false }
                            } label: {
                                row(restoring ? "Restoring…" : "Restore purchases", "arrow.clockwise")
                            }
                            .buttonStyle(DSRowPressStyle()).disabled(restoring)
                            .accessibilityIdentifier("settings.restore")

                            insetDivider

                            Link(destination: URL(string: "https://apps.apple.com/account/subscriptions")!) {
                                row("Manage subscription", "creditcard")
                            }
                            .buttonStyle(DSRowPressStyle())
                        }

                        // Build 54 tier (renamed "Plus" in 55: the row above already sells
                        // "Yunicorn Pro" at a different price — two products, one name).
                        // Stoic's upgrade strip: night surface, left-aligned title + message.
                        Button { showProPaywall = true } label: {
                            HStack(alignment: .center, spacing: Space.md) {
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(entitlements.isPro ? "Yunicorn Plus, active"
                                                            : "Go Plus")
                                        .font(AppFont.title3).foregroundStyle(Palette.onNight)
                                    Text(entitlements.isPro ? "Clean exports, every look, priority renders."
                                                            : "Remove the watermark from your exports.")
                                        .font(AppFont.supporting).foregroundStyle(Palette.onNight.opacity(0.85))
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                .multilineTextAlignment(.leading)
                                Spacer(minLength: Space.sm)
                                if entitlements.isPro {
                                    HStack(spacing: 4) {
                                        Image(systemName: "checkmark")
                                            .font(.system(size: 11, weight: .bold))
                                        Text("Plus").font(AppFont.caption.weight(.semibold))
                                    }
                                    .foregroundStyle(Palette.night)
                                    .padding(.horizontal, 10).frame(height: 26)
                                    .background(Capsule().fill(Palette.onNight))
                                } else {
                                    Image(systemName: "chevron.right")
                                        .font(.system(size: 14, weight: .semibold))
                                        .foregroundStyle(Palette.onNight)
                                }
                            }
                            .padding(Space.cardPad)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                                .fill(Palette.night))
                            .overlay(alignment: .bottomTrailing) {
                                Image(systemName: "sparkles")
                                    .font(.system(size: 44, weight: .ultraLight))
                                    .foregroundStyle(Palette.onNight.opacity(0.18))
                                    .padding(.trailing, 44).padding(.bottom, 6)
                                    .accessibilityHidden(true)
                                    .allowsHitTesting(false)
                            }
                            .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                                .strokeBorder(Palette.hairline, lineWidth: 1))
                            .contentShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
                        }
                        .buttonStyle(PressableStyle())
                        .accessibilityIdentifier("settings.goPro")
                    }

                    // MARK: Account
                    settingsGroup("Account") {
                        HStack(spacing: Space.md) {
                            AccountAvatarMark(label: displayName)
                            VStack(alignment: .leading, spacing: Space.xxs) {
                                Text(store.auth.state?.email ?? "Demo account")
                                    .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                                    .lineLimit(1).truncationMode(.middle)
                                Text("Signed in").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(.horizontal, Space.rowPad).padding(.vertical, 10)
                        .frame(minHeight: 52)
                        .accessibilityElement(children: .combine)
                        .accessibilityIdentifier("settings.accountEmail")

                        insetDivider

                        Button { showSignOutConfirm = true } label: {
                            row("Sign out", "rectangle.portrait.and.arrow.right", tint: Palette.critical)
                        }
                        .buttonStyle(DSRowPressStyle())
                        .accessibilityIdentifier("settings.signOut")

                        insetDivider

                        // Deletion is an App Store requirement (5.1.1(v))
                        Button(role: .destructive) { showDeleteConfirm = true } label: {
                            row("Delete account", "trash", tint: Palette.critical)
                        }
                        .buttonStyle(DSRowPressStyle())
                        .accessibilityIdentifier("settings.deleteAccount")

                        #if DEBUG
                        insetDivider
                        GhostButton(title: "Reset app to first run", systemImage: "arrow.counterclockwise") {
                            store.resetAll(); dismiss()
                        }
                        .padding(Space.rowPad)

                        #if targetEnvironment(simulator)
                        // Simulator-only demo switch: try each paid tier without billing. The
                        // backend applies it only when ALLOW_DEV_TIER=1 (never on in prod).
                        textDivider
                        VStack(alignment: .leading, spacing: Space.sm) {
                            Text("Demo tier (simulator only)")
                                .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
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
                                Text(demoTierInfo).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            }
                        }
                        .padding(.horizontal, Space.rowPad).padding(.vertical, 12)
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
                            .buttonStyle(DSRowPressStyle())
                            .accessibilityIdentifier("settings.exportData")

                            insetDivider
                        }

                        Link(destination: LegalURLs.privacy) {
                            row("Privacy Policy", "hand.raised", tint: Palette.textSecondary)
                        }
                        .buttonStyle(DSRowPressStyle())

                        insetDivider

                        Link(destination: LegalURLs.terms) {
                            row("Terms of Use", "doc.text", tint: Palette.textSecondary)
                        }
                        .buttonStyle(DSRowPressStyle())
                    }

                    // MARK: Support & About
                    settingsGroup("Support & About") {
                        Button {
                            dismiss()
                            tour.start(router: router)
                        } label: {
                            row("Replay walkthrough", "arrow.triangle.2.circlepath", tint: Palette.accent)
                        }
                        .buttonStyle(DSRowPressStyle())
                        .accessibilityIdentifier("settings.replayTour")

                        insetDivider

                        Link(destination: LegalURLs.support) {
                            row("Support", "questionmark.circle", mark: AnyView(SupportMark()))
                        }
                        .buttonStyle(DSRowPressStyle())

                        insetDivider

                        HStack {
                            Text("Version").font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                            Spacer()
                            Text(appVersion).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                .lineLimit(1)
                        }
                        .padding(.horizontal, Space.rowPad)
                        .frame(minHeight: 52)
                        .accessibilityElement(children: .combine)
                    }

                    Text("Yunicorn \(appVersion)")
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.bottom, Space.xl)
                }
                .screenPadding()
                .padding(.top, Space.sm)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("")
            .sheet(isPresented: $showProPaywall) { YunicornProPaywall() }
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(AppFont.headline)
                        .tint(Palette.textPrimary)
                }
            }
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

    /// Hairline divider inset to the text column (past the 32pt icon tile + gutters),
    /// so the icon rail reads as one continuous column.
    private var insetDivider: some View {
        DSRowDivider(inset: Space.rowPad + 32 + Space.md)
    }

    /// Hairline divider for icon-less rows (toggles) — inset to the row text.
    private var textDivider: some View {
        DSRowDivider()
    }

    /// Monochrome status pill ("Pro", "Active"): check glyph + wording on a sunken capsule.
    private func statusPill(_ text: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: "checkmark").font(.system(size: 11, weight: .bold))
            Text(text).font(AppFont.caption.weight(.semibold))
        }
        .foregroundStyle(Palette.textPrimary)
        .padding(.horizontal, 10).frame(height: 26)
        .background(Capsule().fill(Palette.surfaceSunken))
    }

    /// A standard 52pt tappable row: mark + title + trailing chevron. `mark` overrides the
    /// default glyph tile with a custom one (PrivacyMark, SupportMark, ...). `tint` only
    /// selects the destructive mark (Palette.critical); every glyph and label is textPrimary.
    @ViewBuilder
    private func row(_ title: String, _ icon: String, tint: Color = Palette.textSecondary,
                     mark: AnyView? = nil) -> some View {
        HStack(spacing: Space.md) {
            if let mark {
                mark
            } else if tint == Palette.critical {
                DestructiveMark(systemImage: icon)
            } else {
                UtilityMark(systemImage: icon)
            }
            Text(title).font(AppFont.bodyText)
                .foregroundStyle(Palette.textPrimary)
                .lineLimit(1).minimumScaleFactor(0.85)
            Spacer(minLength: Space.sm)
            Image(systemName: "chevron.right")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Palette.textPrimary)
        }
        .padding(.horizontal, Space.rowPad)
        .frame(minHeight: 52)
        .contentShape(Rectangle())
    }

    /// Eyebrow section label + a grouped surface card (DSSection).
    @ViewBuilder
    private func settingsGroup<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        let rows = content()
        DSSection(eyebrow: title) { rows }
    }
}
