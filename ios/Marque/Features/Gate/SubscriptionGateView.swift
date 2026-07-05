import SwiftUI
import StoreKit

// The hard subscription wall — no way past without subscribing (or the DEBUG
// dev-continue used by Maestro/keyless dev). Restore + legal links kept for
// App Store review compliance.
struct SubscriptionGateView: View {
    @Environment(AppStore.self) private var store

    private let features = [
        "A strategist that knows you — talk to it every morning",
        "Daily scripts in your voice, ranked by what works",
        "Mimic proven reels from creators you watch",
        "AI editing tuned to your style — captions, cuts, pacing",
        "Schedule to Instagram + TikTok and learn from every post",
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                Image("CameraIcon").resizable().scaledToFit().frame(width: 88, height: 88)
                    .frame(maxWidth: .infinity)
                    .padding(.top, Space.huge)

                VStack(alignment: .leading, spacing: Space.sm) {
                    SectionLabel(text: "MARQUE PRO", accent: Palette.accent)
                    Text("Your brand,\non autopilot.")
                        .font(AppFont.displayXL).tracking(-1)
                        .foregroundStyle(Palette.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text("Yunicorn is a subscription. One plan, everything included.")
                        .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                        .lineSpacing(3)
                }

                VStack(alignment: .leading, spacing: Space.md) {
                    ForEach(features, id: \.self) { f in
                        HStack(alignment: .top, spacing: Space.sm) {
                            Image(systemName: "checkmark.circle.fill").foregroundStyle(Palette.accent)
                            Text(f).font(AppFont.body).foregroundStyle(Palette.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .marqueCard()

                VStack(spacing: Space.sm) {
                    PrimaryButton(title: store.subscription.isWorking ? "One moment…" : "Start my week free",
                                  shine: true) {
                        Task { await store.subscription.purchase() }
                    }
                    .disabled(store.subscription.isWorking)
                    .accessibilityIdentifier("paywall.subscribe")

                    Text(priceLine)
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)

                    if !store.subscription.lastError.isEmpty {
                        Text(store.subscription.lastError)
                            .font(AppFont.caption).foregroundStyle(Palette.critical)
                            .multilineTextAlignment(.center)
                    }

                    Button("Restore purchases") {
                        Task { await store.subscription.restore() }
                    }
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                    .disabled(store.subscription.isWorking)
                    .accessibilityIdentifier("paywall.restore")

                    #if DEBUG
                    Button("Continue (dev)") { store.subscription.devContinue() }
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        .padding(.top, Space.xs)
                        .accessibilityIdentifier("paywall.devContinue")
                    #endif

                    HStack(spacing: Space.sm) {
                        Link("Privacy Policy", destination: LegalURLs.privacy)
                        Text("·").foregroundStyle(Palette.textTertiary)
                        Link("Terms of Use", destination: LegalURLs.terms)
                    }
                    .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                    .padding(.top, Space.xs)
                }

                Spacer(minLength: Space.xl)
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .task { await store.subscription.load() }
    }

    private var priceLine: String {
        if let p = store.subscription.monthly {
            return "\(p.displayPrice)/month after a 1-week free trial. Cancel anytime."
        }
        return "$14.99/month after a 1-week free trial. Cancel anytime."
    }
}
