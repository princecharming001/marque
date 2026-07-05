import SwiftUI

// The opening screen — rebuilt per docs/ONBOARDING-DESIGN.md §Landing.
// Cream canvas, the clay unicorn hero, serif headline, one CTA. The old dark
// StarField/constellation/floating-badge collage is gone.
struct WelcomeLanding: View {
    let onStart: () -> Void
    let onHaveAccount: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            Spacer(minLength: Space.xl)

            UnicornMascot(pose: .hero, size: 240)
                .staggerReveal(0)

            Spacer(minLength: Space.lg)

            VStack(spacing: Space.md) {
                Text("Film once.\nPost every day.")
                    .font(Typeface.display(44)).tracking(-1)
                    .foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .staggerReveal(1)
                Text("Your AI content partner for short-form video.")
                    .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .staggerReveal(2)
            }
            .padding(.horizontal, Space.screenH)

            Spacer(minLength: Space.xl)

            VStack(spacing: Space.lg) {
                OnbPill(title: "Get started", action: onStart)
                    .accessibilityIdentifier("onboard.start")
                    .staggerReveal(3)

                Button(action: onHaveAccount) {
                    Text("I already have an account")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                        .underline()
                }
                .accessibilityIdentifier("onboard.haveAccount")
                .staggerReveal(4)

                HStack(spacing: Space.xs) {
                    Text("By continuing you accept our")
                        .foregroundStyle(Palette.textTertiary)
                    Link("Terms", destination: LegalURLs.terms)
                        .foregroundStyle(Palette.textSecondary)
                    Text("and").foregroundStyle(Palette.textTertiary)
                    Link("Privacy Policy", destination: LegalURLs.privacy)
                        .foregroundStyle(Palette.textSecondary)
                }
                .font(AppFont.micro)
                .staggerReveal(5)
            }
            .padding(.horizontal, Space.screenH)
            .padding(.bottom, Space.lg)
        }
        .background(Palette.canvas.ignoresSafeArea())
    }
}
