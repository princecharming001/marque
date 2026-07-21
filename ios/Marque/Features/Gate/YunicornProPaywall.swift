import SwiftUI

// Build 54 — the Yunicorn PRO paywall (paid + unpaid states), adapted from maxapp's
// consumer "Cosmos" paywall structure (headline + serif italic accent word, PREMIUM
// pill, hairline feature checklist card, two side-by-side plan cards, white pill CTA,
// reassurance line, restore + legal) rendered in Yunicorn's own register: warm ink
// canvas, Fraunces display, no photography. MOCK by design — the CTA drives the
// Entitlements flag (no StoreKit yet), so the whole gated experience (watermark-free
// exports, the Pro surface) is real end to end before IAP lands.
//
// Distinct from SubscriptionGateView (the hard entry wall): this is the PRO tier
// upsell reached from Settings / gate points, dismissible, with a paid state.
struct YunicornProPaywall: View {
    @Environment(\.dismiss) private var dismiss
    @State private var entitlements = Entitlements.shared
    @State private var working = false
    @State private var selectedPlan = 0     // 0 = trial, 1 = pay now

    private static let weeklyPrice = "$6.99"

    var body: some View {
        ZStack {
            Palette.ink.ignoresSafeArea()
            if entitlements.isPro { paidState } else { unpaidState }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: unpaid — the pitch

    private var unpaidState: some View {
        VStack(spacing: 0) {
            HStack {
                Button { dismiss() } label: {
                    Image(systemName: "xmark").font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.white.opacity(0.7))
                        .frame(width: 34, height: 34)
                        .background(.white.opacity(0.08), in: Circle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("proPaywall.close")
                Spacer()
                Button("Restore") { Task { await restore() } }
                    .font(AppFont.callout).foregroundStyle(.white.opacity(0.7))
                    .disabled(working)
                    .accessibilityIdentifier("proPaywall.restore")
            }
            .padding(.horizontal, Space.lg).padding(.top, Space.md)

            ScrollView(showsIndicators: false) {
                VStack(spacing: Space.lg) {
                    // The Yunicorn mark (Higgsfield-generated single-line seal) on a white
                    // squircle — the ink-on-white art reads as a wax-seal chip on the dark
                    // canvas. The pulsing dot ring sits behind it as ambient motion.
                    ZStack {
                        dotRing.scaleEffect(2.4).opacity(0.5)
                        Image("YunicornMark")
                            .resizable().scaledToFit()
                            .frame(width: 46, height: 46)
                            .padding(9)
                            .background(Color.white)
                            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                            .shadow(color: .black.opacity(0.35), radius: 12, y: 4)
                    }
                    .frame(height: 84)
                    .padding(.top, Space.xl)

                    // Serif headline with the signature italic accent word.
                    (Text("Make it ") + Text("unmistakable").italic())
                        .font(Typeface.display(32, .semibold)).tracking(-0.8)
                        .foregroundStyle(.white)
                        .multilineTextAlignment(.center)

                    Text("PLUS")
                        .font(.system(size: 11, weight: .semibold)).tracking(1.4)
                        .foregroundStyle(.white)
                        .padding(.horizontal, 14).padding(.vertical, 5)
                        .overlay(Capsule().strokeBorder(.white.opacity(0.14), lineWidth: 1))

                    featureCard

                    HStack(spacing: 10) {
                        planCard(idx: 0, title: "3-day trial", price: "Free",
                                 sub: "then \(Self.weeklyPrice)/wk")
                        planCard(idx: 1, title: "Go Plus now", price: "\(Self.weeklyPrice)/wk",
                                 sub: "start today")
                    }

                    VStack(spacing: Space.sm) {
                        Button { Task { await purchase() } } label: {
                            Text(working ? "Processing…"
                                 : selectedPlan == 0 ? "Start my 3-day free trial" : "Go Plus now")
                                .font(AppFont.headline).foregroundStyle(Palette.ink)
                                .frame(maxWidth: .infinity).frame(height: 56)
                                .background(Color.white).clipShape(Capsule())
                                .shadow(color: .black.opacity(0.3), radius: 16, y: 6)
                        }
                        .buttonStyle(PressableStyle())
                        .disabled(working)
                        .accessibilityIdentifier("proPaywall.cta")

                        Text(selectedPlan == 0 ? "No payment due today · cancel anytime"
                                               : "Cancel anytime in Settings")
                            .font(AppFont.caption).foregroundStyle(.white.opacity(0.58))
                    }

                    HStack(spacing: Space.sm) {
                        Link("Terms of Use", destination: LegalURLs.terms)
                        Text("·")
                        Link("Privacy Policy", destination: LegalURLs.privacy)
                    }
                    .font(AppFont.micro).foregroundStyle(.white.opacity(0.4))
                    .padding(.bottom, Space.xl)
                }
                .padding(.horizontal, Space.lg)
                .frame(maxWidth: 460)
                .frame(maxWidth: .infinity)
            }
        }
    }

    /// maxapp's pulsing 6-dot ring mark, sized for the sheet.
    private var dotRing: some View {
        TimelineView(.animation(minimumInterval: 0.1)) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate
            ZStack {
                ForEach(0..<6, id: \.self) { i in
                    let phase = (t - Double(i) * 0.3).truncatingRemainder(dividingBy: 1.8) / 1.8
                    Circle().fill(.white)
                        .frame(width: 5, height: 5)
                        .opacity(0.28 + 0.72 * (0.5 + 0.5 * cos(phase * 2 * .pi)))
                        .offset(y: -13)
                        .rotationEffect(.degrees(Double(i) * 60))
                }
            }
            .frame(width: 30, height: 30)
        }
    }

    private static let features: [(String, String)] = [
        ("Clean exports", "No \"powered by Yunicorn\" watermark on your reels"),
        ("Every look", "All composition styles, caption packs, and outros"),
        ("Priority renders", "Your edits jump the queue at peak hours"),
        ("The full brain", "Strategy, insights, and the learning loop, unlimited"),
    ]

    private var featureCard: some View {
        VStack(spacing: 0) {
            ForEach(Array(Self.features.enumerated()), id: \.offset) { i, f in
                HStack(alignment: .top, spacing: Space.md) {
                    Image(systemName: "checkmark")
                        .font(.system(size: 11, weight: .bold)).foregroundStyle(.white)
                        .frame(width: 24, height: 24)
                        .overlay(Circle().strokeBorder(.white.opacity(0.2), lineWidth: 1))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(f.0).font(.system(size: 14.5, weight: .semibold)).foregroundStyle(.white)
                        Text(f.1).font(.system(size: 12)).foregroundStyle(.white.opacity(0.58))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 18).padding(.vertical, 14)
                if i < Self.features.count - 1 {
                    Rectangle().fill(.white.opacity(0.08)).frame(height: 1).padding(.leading, 18)
                }
            }
        }
        .background(.white.opacity(0.04))
        .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous)
            .strokeBorder(.white.opacity(0.08), lineWidth: 1))
    }

    private func planCard(idx: Int, title: String, price: String, sub: String) -> some View {
        let selected = selectedPlan == idx
        return Button { selectedPlan = idx } label: {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 12.5)).foregroundStyle(.white.opacity(0.58))
                Text(price).font(.system(size: 19, weight: .semibold)).foregroundStyle(.white)
                Text(sub).font(.system(size: 11.5)).foregroundStyle(.white.opacity(0.42))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(.white.opacity(selected ? 0.09 : 0.05))
            .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous)
                .strokeBorder(.white.opacity(selected ? 0.55 : 0.08), lineWidth: selected ? 1.5 : 1))
            .overlay(alignment: .topTrailing) {
                if selected {
                    Image(systemName: "checkmark")
                        .font(.system(size: 9, weight: .bold)).foregroundStyle(Palette.ink)
                        .frame(width: 18, height: 18)
                        .background(Circle().fill(.white))
                        .padding(8)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("proPaywall.plan.\(idx)")
    }

    // MARK: paid — "You're Pro"

    private var paidState: some View {
        VStack(spacing: Space.lg) {
            HStack {
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark").font(.system(size: 15, weight: .bold))
                        .foregroundStyle(.white.opacity(0.7))
                        .frame(width: 34, height: 34)
                        .background(.white.opacity(0.08), in: Circle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("proPaywall.paidClose")
            }
            .padding(.horizontal, Space.lg).padding(.top, Space.md)
            Spacer()
            Image(systemName: "checkmark")
                .font(.system(size: 26, weight: .bold)).foregroundStyle(Palette.positive)
                .frame(width: 72, height: 72)
                .background(Circle().fill(Palette.positive.opacity(0.15)))
            Text("You’re on Plus")
                .font(Typeface.display(30, .semibold)).foregroundStyle(.white)
            Text("Clean exports, every look, priority renders.\nYunicorn Plus is active on this device.")
                .font(AppFont.bodyL).foregroundStyle(.white.opacity(0.7))
                .multilineTextAlignment(.center)
            Spacer()
            #if DEBUG
            Button("Revoke Plus (dev)") { entitlements.revoke() }
                .font(AppFont.caption).foregroundStyle(.white.opacity(0.4))
                .padding(.bottom, Space.xl)
                .accessibilityIdentifier("proPaywall.devRevoke")
            #endif
        }
    }

    // MARK: mock transactions

    private func purchase() async {
        working = true
        await entitlements.mockPurchase()
        working = false
    }

    private func restore() async {
        working = true
        await entitlements.mockRestore()
        working = false
    }
}
