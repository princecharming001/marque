import SwiftUI

// The opening screen, in the Stoic interstitial pattern (DESIGN.md §6 Onboarding):
// the Yunicorn mark centered, then the brand word and an oversized muted tagline
// left-aligned, one content-sized primary capsule, a text link and the legal line.
struct WelcomeLanding: View {
    let onStart: () -> Void
    let onHaveAccount: () -> Void

    var body: some View {
        GeometryReader { geo in
            // The mascot gives up height first on short phones (SE 375x667) so the
            // tagline, CTA and legal line never collide; it caps at 240 on large ones.
            let mascot = min(240, max(150, geo.size.height * 0.3))

            VStack(spacing: 0) {
                Spacer(minLength: Space.md)

                UnicornMascot(pose: .hero, size: mascot)
                    .frame(maxWidth: .infinity)
                    .staggerReveal(0)

                Spacer(minLength: Space.lg)

                VStack(alignment: .leading, spacing: Space.sm) {
                    Text("yunicorn.")
                        .font(AppFont.title1).tracking(-0.3)
                        .foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                        .staggerReveal(1)
                    Text("Film once.\nPost every day.")
                        .font(AppFont.displayMuted)
                        .tracking(-0.3)
                        .foregroundStyle(Palette.textTertiary)
                        .multilineTextAlignment(.leading)
                        .lineLimit(3).minimumScaleFactor(0.85)
                        .fixedSize(horizontal: false, vertical: true)
                        .staggerReveal(1)
                    Text("Your AI content partner for short-form video.")
                        .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.top, Space.xs)
                        .staggerReveal(2)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Space.screenH + Space.md)

                Spacer(minLength: Space.xl)

                VStack(spacing: Space.xs) {
                    OnbPill(title: "Get started", action: onStart)
                        .accessibilityIdentifier("onboard.start")
                        .staggerReveal(3)

                    Button(action: onHaveAccount) {
                        Text("I already have an account")
                    }
                    .buttonStyle(.dsLink)
                    .accessibilityIdentifier("onboard.haveAccount")
                    .staggerReveal(4)

                    legalLine
                        .padding(.top, Space.xs)
                        .staggerReveal(5)
                }
                .frame(maxWidth: .infinity)
                .padding(.horizontal, Space.screenH)
                .padding(.bottom, Space.xl)
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
        .background(Palette.canvas.ignoresSafeArea())
    }

    /// One line when it fits, two centered lines on narrow phones.
    private var legalLine: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: Space.xs) {
                Text("By continuing you accept our")
                    .foregroundStyle(Palette.textSecondary)
                Link("Terms", destination: LegalURLs.terms)
                    .foregroundStyle(Palette.textPrimary)
                Text("and").foregroundStyle(Palette.textSecondary)
                Link("Privacy Policy", destination: LegalURLs.privacy)
                    .foregroundStyle(Palette.textPrimary)
            }
            .lineLimit(1)
            VStack(spacing: 2) {
                Text("By continuing you accept our")
                    .foregroundStyle(Palette.textSecondary)
                HStack(spacing: Space.xs) {
                    Link("Terms", destination: LegalURLs.terms)
                        .foregroundStyle(Palette.textPrimary)
                    Text("and").foregroundStyle(Palette.textSecondary)
                    Link("Privacy Policy", destination: LegalURLs.privacy)
                        .foregroundStyle(Palette.textPrimary)
                }
            }
        }
        .font(AppFont.caption)
    }
}
