import SwiftUI

// Calm one-screen paywall, presented at the publish gate (11-monetization.md).
struct PaywallView: View {
    @Environment(\.dismiss) private var dismiss

    private let proFeatures = [
        "Unlimited scripts in your voice",
        "Full clips, no watermark",
        "Schedule & auto-post to IG + TikTok",
        "Hook Lab + format library",
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    VStack(alignment: .leading, spacing: Space.sm) {
                        Text("Film once.\nPost all week.")
                            .font(AppFont.displayL).foregroundStyle(Palette.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("Go Pro to publish everything Marque makes for you.")
                            .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                    }

                    VStack(alignment: .leading, spacing: Space.md) {
                        ForEach(proFeatures, id: \.self) { f in
                            HStack(alignment: .top, spacing: Space.sm) {
                                Image(systemName: "checkmark.circle.fill").foregroundStyle(Palette.gold)
                                Text(f).font(AppFont.body).foregroundStyle(Palette.textPrimary)
                            }
                        }
                    }
                    .marqueCard()

                    VStack(spacing: Space.sm) {
                        PrimaryButton(title: "Start 7-day free trial", shine: true) { dismiss() }
                            .accessibilityIdentifier("paywall.subscribe")
                        Text("Then $14.99/mo. Cancel anytime.")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        Button("Restore purchases") { }
                            .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.surface.ignoresSafeArea())
            .navigationTitle("Marque Pro")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Close") { dismiss() } } }
        }
    }
}
