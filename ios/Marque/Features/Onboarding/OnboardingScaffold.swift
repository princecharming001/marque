import SwiftUI

// The universal onboarding layout (DESIGN.md §6 Onboarding, Stoic flow header):
//   top bar (back chevron + centered progress dashes, fixed 44pt)
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
            // Band 1 — flow header (DESIGN.md §5): back chevron leading, progress
            // dashes centered on the screen axis, an empty trailing slot.
            ZStack {
                if showsProgress {
                    SegmentedProgress(total: progressTotal, index: progressIndex)
                }
                HStack(spacing: 0) {
                    if showsBack, let onBack {
                        BackCircle(action: onBack)
                    } else {
                        // Keep the band height identical across steps with/without back.
                        Color.clear.frame(width: 44, height: 44)
                    }
                    Spacer(minLength: 0)
                }
            }
            .frame(height: 44)
            .padding(.horizontal, Space.xs)
            .padding(.top, Space.xs)

            if scrollable {
                // Header travels WITH the content down the scroll. The content gets
                // no horizontal gutter of its own — a full-bleed cloud supplies its
                // own edge bleed. A canvas fade seats the pinned CTA over the cloud.
                ScrollView {
                    VStack(spacing: 0) {
                        headerBlock
                            .padding(.horizontal, Space.screenH)
                            .padding(.top, Space.lg)
                        content()
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.bottom, Space.xl)
                }
                .scrollIndicators(.hidden)
                .overlay(alignment: .bottom) {
                    LinearGradient(colors: [Palette.canvas.opacity(0), Palette.canvas],
                                   startPoint: .top, endPoint: .bottom)
                        .frame(height: Space.xxl)
                        .allowsHitTesting(false)
                }
            } else {
                // Centered group — header + content as ONE block, floating in the
                // middle of the space between the chrome and the CTA. Internal gaps
                // are fixed so the question and its choices always read as a unit.
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

            // CTA slot — the primary capsule is content-sized and centered.
            cta()
                .frame(maxWidth: .infinity)
                .padding(.horizontal, Space.screenH)
                .padding(.top, Space.sm)
                .padding(.bottom, Space.xl)
        }
        .background(Palette.canvas.ignoresSafeArea())
    }

    /// An empty headline means the step draws its own header (e.g. a typed-out
    /// reveal), so the scaffold skips its static one entirely.
    /// Stoic onboarding prompt: centered `title1` question, `body` subtitle in
    /// textSecondary, 24pt to the content.
    @ViewBuilder private var headerBlock: some View {
        if !headline.isEmpty {
            VStack(spacing: Space.sm) {
                Text(headline)
                    .font(AppFont.title1).tracking(-0.3)
                    .foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                    .staggerReveal(0)
                if let subtitle {
                    Text(subtitle)
                        .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .staggerReveal(1)
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.bottom, Space.xl)
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
