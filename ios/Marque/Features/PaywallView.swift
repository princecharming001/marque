import SwiftUI
import StoreKit

// Calm one-screen paywall, presented at the publish gate (11-monetization.md).
struct PaywallView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var restoring = false

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
                    Image("CameraIcon").resizable().scaledToFit().frame(width: 88, height: 88)
                        .frame(maxWidth: .infinity)
                        .padding(.top, Space.md)

                    VStack(alignment: .leading, spacing: Space.sm) {
                        SectionLabel(text: "Yunicorn Pro", accent: Palette.accent)
                        Text("Film once.\nPost all week.")
                            .font(AppFont.displayXL).tracking(-1).foregroundStyle(Palette.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("Go Pro to publish everything Yunicorn makes for you.")
                            .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                            .lineSpacing(3)
                    }
                    .padding(.top, Space.sm)

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
                        PrimaryButton(title: "Go Pro", shine: true) { dismiss() }
                            .accessibilityIdentifier("paywall.subscribe")
                        Text("$14.99/mo. Cancel anytime.")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        Button(restoring ? "Restoring…" : "Restore purchases") {
                            restoring = true
                            Task { try? await StoreKit.AppStore.sync(); restoring = false }
                        }
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                        .disabled(restoring)

                        HStack(spacing: Space.sm) {
                            Link("Privacy Policy", destination: LegalURLs.privacy)
                            Text("·").foregroundStyle(Palette.textTertiary)
                            Link("Terms of Use", destination: LegalURLs.terms)
                        }
                        .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                        .padding(.top, Space.xs)
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Yunicorn Pro")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Close") { dismiss() } } }
        }
    }
}
