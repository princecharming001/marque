import SwiftUI

// The payment-plan screen, ported from maxapp's PaymentScreen.tsx structure
// beat-for-beat: full-bleed hero art with a slow Ken Burns drift under a 4-stop
// scrim, a pulsing dot ring, a serif headline with one italic word, a PREMIUM
// pill, a glass feature checklist that absorbs the leftover height, two
// side-by-side plan boxes (trial preselected), a solid white CTA pill, a
// reassurance line, and a Terms/Privacy footer. Restore lives top-right because
// App Review Guideline 3.1.1 requires a working restore control on any screen
// that sells a subscription.
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

    @State private var payNow = false          // false = the 7-day trial box (preselected)
    @State private var busy = false
    @State private var restoring = false
    @State private var bgScale: CGFloat = 1.0
    @State private var bgOffset: CGFloat = 0
    @State private var dotPhase: Double = 0

    private struct Feature { let title: String; let sub: String }
    private let features: [Feature] = [
        .init(title: "Unlimited edits", sub: "Every take, cut and captioned by the AI editor"),
        .init(title: "Your voice, learned", sub: "Scripts that sound like you, not like a template"),
        .init(title: "Daily post ideas", sub: "Fresh angles from what's working in your niche"),
        .init(title: "Auto-posting", sub: "Straight to Instagram and TikTok on your schedule"),
        .init(title: "No watermark", sub: "Your clips ship clean"),
    ]

    /// Localized price straight from StoreKit when products are loaded; maxapp's
    /// same hardcoded-fallback pattern otherwise (theirs is $5.99/wk).
    private var price: String {
        (store.subscription.monthly ?? store.subscription.products.first)?
            .displayPrice ?? "$14.99"
    }
    // 7-day, NOT 3: the ASC intro offer was configured as 1 week, so 3-day copy was
    // under-selling what StoreKit will actually grant — and RevenueCat 2026 puts
    // ≤4-day trials in the worst-converting bucket (25.5% vs 42.5% for longer).
    // A week spans at least one full film → edit → post → see-results cycle.
    private var ctaLabel: String {
        busy ? "Processing…" : (payNow ? "Subscribe now" : "Start my 7-day free trial")
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
            return "\(goalPhrase) — \(pace) posts a week is \(pace * 52) videos this year, every one in your voice."
        }
        return "\(goalPhrase) — every video in your voice."
    }

    var body: some View {
        ZStack {
            Palette.night.ignoresSafeArea()

            // Hero art with a slow Ken Burns drift (maxapp: scale 1→1.07 over 9s,
            // x 0→9pt over 12s, both auto-reversing). MUST be geometry-bound and
            // clipped: an unclipped scaledToFill sizes the whole ZStack to the
            // image's intrinsic width, which shoved every sibling off both edges.
            GeometryReader { geo in
                Image("UnicornHero")
                    .resizable().scaledToFill()
                    .frame(width: geo.size.width, height: geo.size.height)
                    .scaleEffect(bgScale)
                    .offset(x: bgOffset)
                    .clipped()
            }
            .ignoresSafeArea()
            .allowsHitTesting(false)

            LinearGradient(stops: [
                .init(color: Palette.night.opacity(0.86), location: 0),
                .init(color: Palette.night.opacity(0.30), location: 0.32),
                .init(color: Palette.night.opacity(0.40), location: 0.60),
                .init(color: Palette.night.opacity(0.94), location: 1),
            ], startPoint: .top, endPoint: .bottom)
            .ignoresSafeArea()
            .allowsHitTesting(false)

            VStack(spacing: 8) {
                pulseDotRing
                    .padding(.bottom, 6)

                (Text("Unlock your ") + Text("everything").italic())
                    .font(Typeface.display(32, .semibold))
                    .tracking(-0.8)
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.center)

                Text("PREMIUM")
                    .font(Typeface.sans(11, .semibold)).tracking(1.4)
                    .foregroundStyle(.white)
                    .padding(.horizontal, 14).padding(.vertical, 5)
                    .overlay(Capsule().strokeBorder(.white.opacity(0.14), lineWidth: 1))

                Text(personalLine)
                    .font(Typeface.sans(13, .medium)).tracking(0.1)
                    .foregroundStyle(.white.opacity(0.72))
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 8)
                    .padding(.bottom, 14)
                    .accessibilityIdentifier("payment.personalLine")

                featureCard

                HStack(spacing: 10) {
                    planBox(selected: !payNow, title: "7-day trial",
                            price: "Free", sub: "then \(price)/mo") { payNow = false }
                    planBox(selected: payNow, title: "Subscribe now",
                            price: "\(price)/mo", sub: "start today") { payNow = true }
                }

                Button { Task { await subscribe() } } label: {
                    Group {
                        if busy { ProgressView().tint(Palette.night) }
                        else { Text(ctaLabel).font(Typeface.sans(16, .semibold)).tracking(0.1) }
                    }
                    .foregroundStyle(Palette.night)
                    .frame(maxWidth: .infinity).frame(height: 56)
                    .background(Capsule().fill(.white))
                }
                .buttonStyle(.plain)
                .disabled(busy)
                .opacity(busy ? 0.5 : 1)
                .shadow(color: .black.opacity(0.30), radius: 16, y: 6)
                .padding(.top, 4)
                .accessibilityIdentifier("payment.cta")

                Text(payNow ? "Cancel anytime in Settings"
                            : "No payment due today · cancel anytime")
                    .font(Typeface.sans(13, .medium)).tracking(0.1)
                    .foregroundStyle(.white.opacity(0.58))
                    .padding(.top, 2)

                // A failed/unavailable purchase must say so — silently staying on
                // the wall reads as a frozen app.
                if !store.subscription.lastError.isEmpty {
                    Text(store.subscription.lastError)
                        .font(Typeface.sans(13, .medium))
                        .foregroundStyle(.white.opacity(0.85))
                        .multilineTextAlignment(.center)
                        .padding(.top, 6)
                        .accessibilityIdentifier("payment.error")
                }

                HStack(spacing: 14) {
                    Link("Terms of Service", destination: LegalURLs.terms)
                    Link("Privacy Policy", destination: LegalURLs.privacy)
                }
                .font(Typeface.sans(11, .regular))
                .foregroundStyle(.white.opacity(0.42))
                .underline()

                // The soft-wall escape: quiet, below the fold of attention, but real.
                // Free tier = scripts/recording/editing with the watermark; the hard
                // re-ask happens at publish/export where sunk cost is highest.
                if let onContinueFree {
                    Button {
                        store.backend.reportClientEvent("paywall_action", detail: "continue_free")
                        onContinueFree()
                    } label: {
                        Text("Continue with the watermark for now")
                            .font(Typeface.sans(13, .medium)).tracking(0.1)
                            .foregroundStyle(.white.opacity(0.58))
                            .underline()
                    }
                    .buttonStyle(.plain)
                    .padding(.top, 8)
                    .accessibilityIdentifier("payment.continueFree")
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 100).padding(.bottom, 36)

            // Top bar: close (only when dismissible) · Restore (always — 3.1.1).
            VStack {
                HStack {
                    if dismissible {
                        Button { dismiss() } label: {
                            Image(systemName: "xmark")
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(.white)
                                .frame(width: 32, height: 32)
                                .background(Circle().fill(.white.opacity(0.12)))
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("payment.close")
                    } else {
                        Spacer().frame(width: 32, height: 32)
                    }
                    Spacer()
                    Button { Task { await restore() } } label: {
                        Text(restoring ? "Restoring…" : "Restore")
                            .font(Typeface.sans(13.5, .medium))
                            .foregroundStyle(.white.opacity(0.58))
                    }
                    .buttonStyle(.plain)
                    .disabled(busy || restoring)
                    .accessibilityIdentifier("payment.restore")
                }
                .padding(.horizontal, 20)
                Spacer()
            }
            .padding(.top, 8)
        }
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

    /// Six dots chasing around a 13pt-radius ring (maxapp's PulseDotRing).
    private var pulseDotRing: some View {
        TimelineView(.animation) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate / 1.8
            ZStack {
                ForEach(0..<6, id: \.self) { i in
                    let angle = Double(i) / 6 * 2 * .pi
                    let phase = t * 2 * .pi + Double(i) * (.pi / 3)
                    Circle().fill(.white)
                        .frame(width: 5, height: 5)
                        .opacity(0.28 + 0.72 * ((sin(phase) + 1) / 2))
                        .offset(x: cos(angle) * 13, y: sin(angle) * 13)
                }
            }
            .frame(width: 30, height: 30)
        }
    }

    private var featureCard: some View {
        VStack(spacing: 0) {
            ForEach(Array(features.enumerated()), id: \.offset) { i, f in
                if i > 0 { Rectangle().fill(.white.opacity(0.08)).frame(height: 0.5) }
                HStack(spacing: 13) {
                    Image(systemName: "checkmark")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 24, height: 24)
                        .overlay(Circle().strokeBorder(.white.opacity(0.14), lineWidth: 1))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(f.title).font(Typeface.sans(14.5, .semibold)).tracking(-0.1)
                            .foregroundStyle(.white)
                        Text(f.sub).font(Typeface.sans(12, .regular))
                            .foregroundStyle(.white.opacity(0.58))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 0)
                }
                .frame(maxHeight: .infinity)
                .padding(.vertical, 6)
            }
        }
        .padding(.horizontal, 18).padding(.vertical, 6)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(LiquidGlassFill(radius: 22, sheen: 0.7))
        .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous)
            .strokeBorder(.white.opacity(0.24), lineWidth: 1))
        .shadow(color: .black.opacity(0.36), radius: 26, y: 14)
    }

    private func planBox(selected: Bool, title: String, price: String, sub: String,
                         _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 0) {
                Text(title).font(Typeface.sans(12.5, .medium)).tracking(0.1)
                    .foregroundStyle(.white.opacity(0.58))
                Text(price).font(Typeface.sans(19, .semibold)).tracking(-0.3)
                    .foregroundStyle(.white).padding(.top, 5)
                Text(sub).font(Typeface.sans(11.5, .regular))
                    .foregroundStyle(.white.opacity(0.42)).padding(.top, 2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(.white.opacity(selected ? 0.09 : 0.05)))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(.white.opacity(selected ? 0.55 : 0.08), lineWidth: 1))
            .overlay(alignment: .topTrailing) {
                if selected {
                    Image(systemName: "checkmark")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(Palette.night)
                        .frame(width: 20, height: 20)
                        .background(Circle().fill(.white))
                        .padding(10)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("payment.plan.\(selected ? "selected" : "other")")
    }

    // MARK: actions

    // App Store submission (2026-08-16, owner: "wire real StoreKit now"): the
    // click-through era is over. This buys com.marque.pro.monthly through
    // StoreKit 2 — real sheet, real charge, receipt-verified entitlement. The
    // screen only advances when StoreKit reports an entitlement; a cancelled or
    // failed purchase stays here with the error shown.
    private func subscribe() async {
        guard !busy else { return }
        // FUNNEL: which plan box was live when they committed — the trial-vs-pay-now split
        // is what tells us whether the trial length is doing any work.
        store.backend.reportClientEvent(
            "paywall_action", detail: payNow ? "subscribe_now" : "start_trial")
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
