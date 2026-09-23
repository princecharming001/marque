import SwiftUI

// Build 54 — the Yunicorn PRO paywall (paid + unpaid states), in the Stoic sheet +
// paywall language (DESIGN.md §5/§6): canvas sheet with a centered lowercase title,
// Yunicorn's line mark, the features as grouped rows with glyphs, ONE selected plan tile
// (the only product) in a surface container, and a sticky footer with the billed-amount
// CTA and the legal links. Follows the system color scheme (no forced dark).
//
// Distinct from PaymentScreen (the entry wall): this is the PLUS upsell reached from
// Settings, dismissible, with a paid state.
struct YunicornProPaywall: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppStore.self) private var store
    @State private var entitlements = Entitlements.shared
    @State private var working = false

    // The ONE real product (com.marque.pro.monthly, $19.99/mo, 7-day free trial).
    // This sheet used to advertise a $6.99 WEEKLY plan that never existed in ASC
    // while purchase() bought the monthly — a price-mismatch rejection waiting to
    // happen. Price now comes from StoreKit itself, hardcoded only as fallback.
    private var monthlyPrice: String {
        (store.subscription.monthly ?? store.subscription.products.first)?
            .displayPrice ?? "$19.99"
    }

    var body: some View {
        ZStack {
            Palette.canvas.ignoresSafeArea()
            if entitlements.isPro { paidState } else { unpaidState }
        }
    }

    // MARK: unpaid — the pitch

    private var unpaidState: some View {
        ScrollView(showsIndicators: false) {
            VStack(spacing: 0) {
                GateMonoMark(size: 56)
                    .padding(.top, Space.md)

                DSEyebrow(text: "PLUS")
                    .padding(.top, Space.stack)

                Text("make it unmistakable.")
                    .font(AppFont.title2).tracking(-0.2)
                    .foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2).minimumScaleFactor(0.8)
                    .padding(.top, Space.xs)

                // 3.1.2(c) (App Review, build 82): the billed amount is the hero of the
                // pricing surface and of the CTA; the trial is one subordinate line.
                // Same product as PaymentScreen, same hierarchy — see its header comment.
                priceCard
                    .padding(Space.sm)
                    .background(RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                        .fill(Palette.surface))
                    .padding(.top, Space.xl)

                featureCard
                    .padding(.top, Space.sectionGap)
            }
            .padding(.horizontal, Space.screenH)
            .padding(.bottom, Space.xl)
        }
        .safeAreaInset(edge: .top, spacing: 0) { unpaidHeader }
        .safeAreaInset(edge: .bottom, spacing: 0) { unpaidFooter }
    }

    /// Sheet header: Restore (leading text link) · "yunicorn plus." · close.
    private var unpaidHeader: some View {
        ZStack {
            Text("yunicorn plus.")
                .font(AppFont.title1).tracking(-0.3)
                .foregroundStyle(Palette.textPrimary)
                .lineLimit(1).minimumScaleFactor(0.7)
                .padding(.horizontal, 88)
                .accessibilityAddTraits(.isHeader)
            HStack {
                Button("Restore") { Task { await restore() } }
                    .buttonStyle(DSTextLinkStyle(color: Palette.textPrimary))
                    .disabled(working)
                    .padding(.leading, Space.screenH)
                    .accessibilityIdentifier("proPaywall.restore")
                Spacer()
                DSIconButton(systemName: "xmark") { dismiss() }
                    .accessibilityLabel("Close")
                    .accessibilityIdentifier("proPaywall.close")
                    .padding(.trailing, Space.xs)
            }
        }
        .padding(.top, Space.sm).padding(.bottom, Space.xs)
        .background(Palette.canvas)
    }

    private var unpaidFooter: some View {
        VStack(spacing: 0) {
            Rectangle().fill(Palette.hairline).frame(height: 1)
            VStack(spacing: Space.sm) {
                Text("\(monthlyPrice)/month after a 7-day free trial · cancel anytime")
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)

                Button { Task { await purchase() } } label: {
                    Text(working ? "Processing…" : "Subscribe for \(monthlyPrice)/month")
                }
                .buttonStyle(DSCapsuleStyle(kind: .primary))
                .disabled(working)
                .accessibilityIdentifier("proPaywall.cta")

                HStack(spacing: Space.sm) {
                    Link("Terms of Use", destination: LegalURLs.terms)
                    Text("·").accessibilityHidden(true)
                    Link("Privacy Policy", destination: LegalURLs.privacy)
                }
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                .frame(minHeight: 28)
            }
            .padding(.horizontal, Space.screenH)
            .padding(.top, Space.stack)
            .padding(.bottom, Space.sm)
        }
        .background(Palette.canvas.ignoresSafeArea(edges: .bottom))
    }

    private static let features: [(String, String)] = [
        ("Clean exports", "No \"powered by Yunicorn\" watermark on your reels"),
        ("Every look", "All composition styles, caption packs, and outros"),
        ("Priority renders", "Your edits jump the queue at peak hours"),
        ("The full brain", "Strategy, insights, and the learning loop, unlimited"),
    ]
    private static let featureGlyphs = ["checkmark.seal", "square.stack", "bolt", "brain"]

    private var featureCard: some View {
        DSSection(eyebrow: "What you get") {
            ForEach(Array(Self.features.enumerated()), id: \.offset) { i, f in
                if i > 0 { DSRowDivider(inset: Space.rowPad + 24 + Space.md) }
                DSRow(title: f.0, subtitle: f.1, systemImage: Self.featureGlyphs[i],
                      showsChevron: false)
                    .padding(.vertical, 6)
            }
        }
    }

    /// The selected (and only) plan tile.
    private var priceCard: some View {
        VStack(spacing: Space.xs) {
            Text("YUNICORN PLUS")
                .font(AppFont.eyebrow).tracking(Track.eyebrow)
                .foregroundStyle(Palette.onInk.opacity(0.72))
            HStack(alignment: .firstTextBaseline, spacing: 5) {
                Text(monthlyPrice).font(AppFont.pageTitle).tracking(-0.5)
                    .foregroundStyle(Palette.onInk)
                Text("/ month").font(AppFont.headline)
                    .foregroundStyle(Palette.onInk.opacity(0.8))
            }
            .lineLimit(1).minimumScaleFactor(0.7)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("proPaywall.price")
            Text("Billed \(monthlyPrice) monthly after a 7-day free trial. Cancel anytime.")
                .font(AppFont.caption).foregroundStyle(Palette.onInk.opacity(0.75))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity)
        .padding(Space.cardPad)
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
        .accessibilityIdentifier("proPaywall.plan.0")
    }

    // MARK: paid — "You're on Plus"

    private var paidState: some View {
        VStack(spacing: 0) {
            HStack {
                Spacer()
                DSIconButton(systemName: "xmark") { dismiss() }
                    .accessibilityLabel("Close")
                    .accessibilityIdentifier("proPaywall.paidClose")
            }
            .padding(.horizontal, Space.xs).padding(.top, Space.sm)

            ScrollView(showsIndicators: false) {
                VStack(spacing: 0) {
                    Image(systemName: "checkmark")
                        .font(.system(size: 24, weight: .semibold))
                        .foregroundStyle(Palette.onInk)
                        .frame(width: 64, height: 64)
                        .background(Circle().fill(Palette.ink))
                        .accessibilityHidden(true)
                        .padding(.top, Space.xl)

                    Text("you’re on plus.")
                        .font(AppFont.title1).tracking(-0.3)
                        .foregroundStyle(Palette.textPrimary)
                        .multilineTextAlignment(.center)
                        .padding(.top, Space.lg)
                        .accessibilityAddTraits(.isHeader)

                    Text("Clean exports, every look, priority renders.\nYunicorn Plus is active on this device.")
                        .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, Space.sm)

                    VStack(spacing: Space.sm) {
                        ForEach(Array(Self.features.enumerated()), id: \.offset) { _, f in
                            DSChecklistRow(title: f.0, state: .done)
                        }
                    }
                    .padding(.top, Space.xl)

                    #if DEBUG
                    Button("Revoke Plus (dev)") { entitlements.revoke() }
                        .buttonStyle(DSTextLinkStyle(color: Palette.textSecondary))
                        .font(AppFont.caption)
                        .padding(.top, Space.xl)
                        .accessibilityIdentifier("proPaywall.devRevoke")
                    #endif
                }
                .padding(.horizontal, Space.screenH)
                .padding(.bottom, Space.xl)
            }
        }
    }

    // MARK: transactions — real StoreKit 2 (App Store submission, 2026-08-16).
    // SubscriptionManager verifies the transaction and syncs
    // Entitlements.shared.isPro, which is what flips this sheet to paidState.

    private func purchase() async {
        working = true
        await store.subscription.purchase()
        working = false
    }

    private func restore() async {
        working = true
        await store.subscription.restore()
        working = false
    }
}
