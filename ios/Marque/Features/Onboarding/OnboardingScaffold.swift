import SwiftUI

// The universal 3-band onboarding layout (docs/ONBOARDING-DESIGN.md §2):
//   top bar (back + progress, fixed 44pt)
//   headline + subtitle (centered)
//   content region (vertically CENTERED in the remaining space)
//   optional CTA slot (only multi-select / freeform / interstitial steps)
//
// Every step renders through this — no step lays itself out — which is what fixes
// the old flow's content drifting to the top/bottom per step. The scaffold does
// NOT ignore the keyboard safe area: when a keyboard rises, the centered content
// region compresses symmetrically so a text field stays visually centered.
struct OnboardingScaffold<Content: View, CTA: View>: View {
    var headline: String
    var subtitle: String? = nil
    var showsBack: Bool = true
    var showsProgress: Bool = false
    var progressIndex: Int = 0
    var progressTotal: Int = 1
    var onBack: (() -> Void)? = nil
    @ViewBuilder var content: () -> Content
    @ViewBuilder var cta: () -> CTA

    var body: some View {
        VStack(spacing: 0) {
            // Band 1 — chrome
            HStack(spacing: Space.md) {
                if showsBack, let onBack {
                    BackCircle(action: onBack)
                } else {
                    // Keep the progress bar aligned across steps with/without back.
                    Color.clear.frame(width: 36, height: 36)
                }
                if showsProgress {
                    SegmentedProgress(total: progressTotal, index: progressIndex)
                } else {
                    Spacer()
                }
                Color.clear.frame(width: 36, height: 36)   // symmetric right gutter
            }
            .frame(height: 44)
            .padding(.horizontal, Space.screenH)
            .padding(.top, Space.sm)

            // Band 2 — header
            VStack(spacing: Space.sm) {
                Text(headline)
                    .font(Typeface.display(30)).tracking(-0.6)
                    .foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .staggerReveal(0)
                if let subtitle {
                    Text(subtitle)
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .staggerReveal(1)
                }
            }
            .padding(.horizontal, Space.screenH)
            .padding(.top, Space.xl)

            // Band 3 — content, centered in whatever space remains
            VStack(spacing: 0) {
                Spacer(minLength: Space.md)
                content()
                Spacer(minLength: Space.md)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding(.horizontal, Space.screenH)

            // CTA slot
            cta()
                .padding(.horizontal, Space.screenH)
                .padding(.bottom, Space.lg)
        }
        .background(Palette.canvas.ignoresSafeArea())
    }
}

extension OnboardingScaffold where CTA == EmptyView {
    init(headline: String, subtitle: String? = nil, showsBack: Bool = true,
         showsProgress: Bool = false, progressIndex: Int = 0, progressTotal: Int = 1,
         onBack: (() -> Void)? = nil, @ViewBuilder content: @escaping () -> Content) {
        self.init(headline: headline, subtitle: subtitle, showsBack: showsBack,
                  showsProgress: showsProgress, progressIndex: progressIndex,
                  progressTotal: progressTotal, onBack: onBack,
                  content: content, cta: { EmptyView() })
    }
}
