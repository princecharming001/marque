import SwiftUI

// The universal onboarding layout (docs/ONBOARDING-DESIGN.md §2):
//   top bar (back + progress, fixed 44pt)
//   ONE centered group: headline + subtitle + content, tight fixed gaps inside
//   optional CTA slot (only multi-select / freeform / interstitial steps)
//
// Every step renders through this — no step lays itself out. The header and the
// content travel TOGETHER as a single block that floats in the vertical center of
// the space between the chrome and the CTA: the question never hugs the top, and
// the choices never drift away from their question (the two earlier complaints,
// respectively). The scaffold does NOT ignore the keyboard safe area: when a
// keyboard rises the flexible spacers compress symmetrically, so the group stays
// centered in whatever room remains above it.
struct OnboardingScaffold<Content: View, CTA: View>: View {
    var headline: String
    var subtitle: String? = nil
    var showsBack: Bool = true
    var showsProgress: Bool = false
    var progressIndex: Int = 0
    var progressTotal: Int = 1
    /// Chip-cloud steps (Gymshark reference): the header pins under the chrome
    /// and the cloud fills the remaining height, instead of the default
    /// float-in-the-middle block that leaves a dead band above the title.
    var topAligned: Bool = false
    /// Multi-select cloud steps (build 83): the whole header + content block
    /// scrolls VERTICALLY as one, so an endlessly-looping chip cloud can run off
    /// the bottom of the screen. The CTA stays pinned outside the scroll view.
    var scrollable: Bool = false
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

            if scrollable {
                // Header travels WITH the content down the scroll. The content gets
                // no horizontal gutter of its own — a full-bleed cloud supplies its
                // own edge bleed.
                ScrollView {
                    VStack(spacing: 0) {
                        headerBlock
                            .padding(.horizontal, Space.screenH)
                            .padding(.top, Space.lg)
                        content()
                    }
                    .frame(maxWidth: .infinity)
                }
                .scrollIndicators(.hidden)
            } else {
                // Centered group — header + content as ONE block, floating in the
                // middle of the space between the chrome and the CTA. Internal gaps
                // are fixed (Space.xxl between header and content) so the question and
                // its choices always read as a unit wherever the block lands.
                if topAligned {
                    Color.clear.frame(height: Space.lg)
                } else {
                    Spacer(minLength: Space.md)
                }

                VStack(spacing: 0) {
                    headerBlock
                    content()
                }
                .frame(maxWidth: .infinity)
                .padding(.horizontal, Space.screenH)

                Spacer(minLength: Space.md)
            }

            // CTA slot
            cta()
                .padding(.horizontal, Space.screenH)
                .padding(.bottom, Space.lg)
        }
        .background(Palette.canvas.ignoresSafeArea())
    }

    /// An empty headline means the step draws its own header (e.g. a typed-out
    /// reveal), so the scaffold skips its static one entirely.
    @ViewBuilder private var headerBlock: some View {
        if !headline.isEmpty {
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
            .padding(.bottom, Space.xxl)
        }
    }
}

extension OnboardingScaffold where CTA == EmptyView {
    init(headline: String, subtitle: String? = nil, showsBack: Bool = true,
         showsProgress: Bool = false, progressIndex: Int = 0, progressTotal: Int = 1,
         scrollable: Bool = false,
         onBack: (() -> Void)? = nil, @ViewBuilder content: @escaping () -> Content) {
        self.init(headline: headline, subtitle: subtitle, showsBack: showsBack,
                  showsProgress: showsProgress, progressIndex: progressIndex,
                  progressTotal: progressTotal, scrollable: scrollable, onBack: onBack,
                  content: content, cta: { EmptyView() })
    }
}
