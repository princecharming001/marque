import SwiftUI

// The payment-plan screen, in the Stoic paywall layout (DESIGN.md §6): canvas page,
// Yunicorn's line mark in the badge slot, a lowercase title, ONE selected plan tile
// inside a surface container (we sell one product, so no fake plan grid), the feature
// list as grouped rows, and a sticky footer (summary, the billed-amount CTA, the
// free-tier escape, Terms/Privacy). Restore lives in the top bar because App Review
// Guideline 3.1.1 requires a working restore control on any screen that sells a
// subscription, and it must stay visible without scrolling on iPhone SE.
//
// PRICING HIERARCHY IS A REVIEW REQUIREMENT, not a taste call. App Review rejected
// 1.0 (build 82) under 3.1.2(c): the old layout made "Free" the 19pt hero of a
// "7-day trial" box and tucked the billed amount underneath at 11.5pt/42%, with a
// "Start my 7-day free trial" CTA. The billed amount must be the most conspicuous
// pricing element (font, size, color, position); trial/intro copy must sit below
// it in a subordinate size. So: the price card leads with the monthly amount at
// display size, the trial is one small line under it, and the CTA itself carries
// the billed amount. The two "trial vs subscribe now" boxes are gone as well —
// both paths bought the SAME product and StoreKit applies the intro offer purely
// by eligibility, so "start today" was a choice that didn't exist.
//
// Yunicorn differences from maxapp, all deliberate: our own hero art + copy, our
// font stack (Fraunces is shared; maxapp's Matter isn't bundled here), and the
// purchase/restore calls go through StoreKitBilling instead of react-native-iap.
struct PaymentScreen: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    /// Shown as a dismissible sheet (upgrade path) vs the hard gate in the funnel.
    var dismissible: Bool = false
    /// Soft-wall mode (the onboarding gate): non-nil adds a quiet "continue with the
    /// watermark" escape into the free tier. nil keeps the sheet/dismiss behavior.
    var onContinueFree: (() -> Void)? = nil

    @State private var busy = false
    @State private var restoring = false
    // Kept from the retired cinematic hero (Ken Burns / dot pulse); nothing reads them now.
    @State private var bgScale: CGFloat = 1.0
    @State private var bgOffset: CGFloat = 0
    @State private var dotPhase: Double = 0

    private struct Feature { let title: String; let sub: String; var glyph: String = "checkmark" }
    private let features: [Feature] = [
        .init(title: "Unlimited edits", sub: "Every take, cut and captioned by the AI editor", glyph: "scissors"),
        .init(title: "Your voice, learned", sub: "Scripts that sound like you, not like a template", glyph: "waveform"),
        .init(title: "Daily post ideas", sub: "Fresh angles from what's working in your niche", glyph: "lightbulb"),
        .init(title: "Auto-posting", sub: "Straight to Instagram and TikTok on your schedule", glyph: "paperplane"),
        .init(title: "No watermark", sub: "Your clips ship clean", glyph: "checkmark.seal"),
    ]

    /// Localized price straight from StoreKit when products are loaded; the fallback
    /// matches the live ASC price ($19.99/mo USD, set 2026-08-25) so a slow product
    /// fetch never shows a number the sheet won't charge.
    private var price: String {
        (store.subscription.monthly ?? store.subscription.products.first)?
            .displayPrice ?? "$19.99"
    }
    // 7-day, NOT 3: the ASC intro offer was configured as 1 week, so 3-day copy was
    // under-selling what StoreKit will actually grant — and RevenueCat 2026 puts
    // ≤4-day trials in the worst-converting bucket (25.5% vs 42.5% for longer).
    // A week spans at least one full film → edit → post → see-results cycle.
    /// The CTA carries the billed amount itself — the single most conspicuous element
    /// on the screen now states what the subscription costs (3.1.2(c)).
    private var ctaLabel: String {
        busy ? "Processing…" : "Subscribe for \(price)/month"
    }

    /// The two onboarding answers we kept exist to be honored HERE — a paywall that
    /// reflects the user's own goal and pace outperforms layout experiments
    /// (Airbridge: a single personalized string beats redesigns).
    private var personalLine: String {
        let goalPhrase: String
        switch store.brand.goal {
        case .audience:  goalPhrase = "Grow your audience"
        case .clients:   goalPhrase = "Land clients from your content"
        case .authority: goalPhrase = "Become the name in your niche"
        case .monetize:  goalPhrase = "Turn content into income"
        }
        if let pace = store.brand.weeklyTarget {
            return "\(goalPhrase), \(pace) posts a week is \(pace * 52) videos this year, every one in your voice."
        }
        return "\(goalPhrase), every video in your voice."
    }

    var body: some View {
        ScrollView(showsIndicators: false) {
            VStack(spacing: 0) {
                // Badge line (Stoic's laurel slot) holds Yunicorn's own line mark.
                GateMonoMark(size: 48)
                    .padding(.top, Space.sm)

                Text("unlock your everything.")
                    .font(AppFont.title1).tracking(-0.3)
                    .foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2).minimumScaleFactor(0.8)
                    .padding(.top, Space.stack)
                    .accessibilityAddTraits(.isHeader)

                Text(personalLine)
                    .font(AppFont.supporting)
                    .foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, Space.sm)
                    .accessibilityIdentifier("payment.personalLine")

                // One product, one selected tile: no fake plan options.
                priceCard
                    .padding(Space.sm)
                    .background(RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                        .fill(Palette.surface))
                    .padding(.top, Space.lg)

                featureCard
                    .padding(.top, Space.sectionGap)
            }
            .padding(.horizontal, Space.screenH)
            .padding(.bottom, Space.xl)
        }
        .background(Palette.canvas.ignoresSafeArea())
        // Top bar: Restore (always — 3.1.1) · close (only when dismissible).
        .safeAreaInset(edge: .top, spacing: 0) { topBar }
        // Sticky footer: summary, the billed-amount CTA, the free-tier escape and the legal
        // links, so every one of them is on screen without scrolling (iPhone SE included).
        .safeAreaInset(edge: .bottom, spacing: 0) { footer }
        .task {
            await store.subscription.load()
            // FUNNEL: paywall impression — the denominator for every conversion rate.
            store.backend.reportClientEvent(
                "paywall_view", detail: dismissible ? "upgrade_sheet" : "onboarding_gate")
            withAnimation(.easeInOut(duration: 9).repeatForever(autoreverses: true)) { bgScale = 1.07 }
            withAnimation(.easeInOut(duration: 12).repeatForever(autoreverses: true)) { bgOffset = 9 }
            withAnimation(.linear(duration: 1.8).repeatForever(autoreverses: false)) { dotPhase = 1 }
        }
    }

    // MARK: pieces

    private var topBar: some View {
        HStack {
            Button { Task { await restore() } } label: {
                Text(restoring ? "Restoring…" : "Restore")
            }
            .buttonStyle(DSTextLinkStyle(color: Palette.textPrimary))
            .disabled(busy || restoring)
            .padding(.leading, Space.screenH)
            .accessibilityIdentifier("payment.restore")

            Spacer()

            if dismissible {
                DSIconButton(systemName: "xmark") { dismiss() }
                    .accessibilityLabel("Close")
                    .accessibilityIdentifier("payment.close")
                    .padding(.trailing, Space.xs)
            }
        }
        .frame(height: 44)
        .background(Palette.canvas)
    }

    private var footer: some View {
        VStack(spacing: 0) {
            Rectangle().fill(Palette.hairline).frame(height: 1)
            VStack(spacing: Space.sm) {
                VStack(spacing: 2) {
                    Text("Yunicorn Pro · \(price)/month")
                        .font(AppFont.bodyText)
                        .foregroundStyle(Palette.textPrimary)
                    // Terms live in the price tile above; keep this to one line so the
                    // free-tier escape stays on-screen on iPhone SE.
                    Text("Cancel anytime")
                        .font(AppFont.supporting)
                        .foregroundStyle(Palette.textSecondary)
                }
                .multilineTextAlignment(.center)
                .lineLimit(1).minimumScaleFactor(0.85)

                // A failed/unavailable purchase must say so — silently staying on
                // the wall reads as a frozen app.
                if !store.subscription.lastError.isEmpty {
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Image(systemName: "exclamationmark.circle")
                            .font(.system(size: 13, weight: .semibold))
                        Text(store.subscription.lastError)
                            .font(AppFont.caption)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .foregroundStyle(Palette.textPrimary)
                    .accessibilityElement(children: .combine)
                    .accessibilityIdentifier("payment.error")
                }

                Button { Task { await subscribe() } } label: {
                    if busy { ProgressView().tint(Palette.textPrimary) }
                    else { Text(ctaLabel) }
                }
                .buttonStyle(DSCapsuleStyle(kind: .primary))
                .disabled(busy)
                .accessibilityIdentifier("payment.cta")

                // The soft-wall escape: quiet, but real. Free tier = scripts/recording/
                // editing with the watermark; the hard re-ask happens at publish/export
                // where sunk cost is highest.
                if let onContinueFree {
                    Button {
                        store.backend.reportClientEvent("paywall_action", detail: "continue_free")
                        onContinueFree()
                    } label: {
                        Text("Continue with the watermark for now")
                    }
                    .buttonStyle(.dsGhost)
                    .accessibilityIdentifier("payment.continueFree")
                }

                HStack(spacing: Space.lg) {
                    Link("Terms of Service", destination: LegalURLs.terms)
                    Link("Privacy Policy", destination: LegalURLs.privacy)
                }
                .font(AppFont.caption)
                .foregroundStyle(Palette.textSecondary)
                .frame(minHeight: 28)
            }
            .padding(.horizontal, Space.screenH)
            .padding(.top, Space.stack)
            .padding(.bottom, Space.sm)
        }
        .background(Palette.canvas.ignoresSafeArea(edges: .bottom))
    }

    private var featureCard: some View {
        DSSection(eyebrow: "What you get") {
            ForEach(Array(features.enumerated()), id: \.offset) { i, f in
                if i > 0 { DSRowDivider(inset: Space.rowPad + 24 + Space.md) }
                DSRow(title: f.title, subtitle: f.sub, systemImage: f.glyph, showsChevron: false)
                    .padding(.vertical, 6)
            }
        }
    }

    /// The one pricing surface: the selected (and only) plan tile. The monthly amount is
    /// the largest pricing text on the screen; the trial is a single subordinate line.
    private var priceCard: some View {
        VStack(spacing: Space.xs) {
            Text("YUNICORN PRO")
                .font(AppFont.eyebrow).tracking(Track.eyebrow)
                .foregroundStyle(Palette.onInk.opacity(0.72))
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                Text(price).font(AppFont.pageTitle).tracking(-0.5)
                    .foregroundStyle(Palette.onInk)
                Text("/ month").font(AppFont.headline)
                    .foregroundStyle(Palette.onInk.opacity(0.8))
            }
            .lineLimit(1).minimumScaleFactor(0.7)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("payment.price")
            Text("Billed \(price) monthly after a 7-day free trial. Cancel anytime.")
                .font(AppFont.caption)
                .foregroundStyle(Palette.onInk.opacity(0.75))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, Space.cardPad).padding(.vertical, Space.cardPad)
        .background(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous)
            .fill(Palette.ink))
        .overlay(alignment: .topTrailing) {
            Image(systemName: "checkmark")
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(Palette.ink)
                .frame(width: 22, height: 22)
                .background(Circle().fill(Palette.onInk))
                .padding(Space.stack)
                .accessibilityHidden(true)
        }
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isSelected)
        .accessibilityIdentifier("payment.plan.selected")
    }

    // MARK: actions

    // App Store submission (2026-08-16, owner: "wire real StoreKit now"): the
    // click-through era is over. This buys com.marque.pro.monthly through
    // StoreKit 2 — real sheet, real charge, receipt-verified entitlement. The
    // screen only advances when StoreKit reports an entitlement; a cancelled or
    // failed purchase stays here with the error shown.
    private func subscribe() async {
        guard !busy else { return }
        // FUNNEL: one path now — the old trial-vs-pay-now split never measured anything
        // real (both boxes bought the same product; StoreKit decides trial eligibility).
        store.backend.reportClientEvent("paywall_action", detail: "subscribe")
        busy = true
        await store.subscription.purchase()
        busy = false
        if store.subscription.isSubscribed {
            store.backend.reportClientEvent("paywall_action", detail: "purchase_success")
            if dismissible { dismiss() }
        }
    }

    /// Real restore (Apple's 3.1.1 control must never dead-end): syncs with the
    /// App Store and re-reads current entitlements.
    private func restore() async {
        guard !restoring else { return }
        restoring = true
        await store.subscription.restore()
        restoring = false
        if store.subscription.isSubscribed, dismissible { dismiss() }
    }
}

/// Yunicorn's line-drawn unicorn mark, monochrome in both schemes. The asset is black
/// line art on an opaque white square, so it is turned into an alpha mask (invert, then
/// luminance -> alpha) and filled with textPrimary: black lines on light, white on dark,
/// never a white tile.
struct GateMonoMark: View {
    var size: CGFloat = 48
    var body: some View {
        Palette.textPrimary
            .frame(width: size, height: size)
            .mask(
                // The line art fills ~45% of its square canvas: scale it up and clip so
                // `size` is roughly the visible drawing.
                Image("YunicornMark").resizable().scaledToFit()
                    .scaleEffect(1.8)
                    .frame(width: size, height: size)
                    .clipped()
                    .colorInvert()
                    .luminanceToAlpha())
            .accessibilityHidden(true)
    }
}
