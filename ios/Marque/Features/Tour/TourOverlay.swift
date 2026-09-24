import SwiftUI

// Anchor plumbing. Each tour target tags itself with `.tourAnchor("id")`, which records its
// frame in GLOBAL (screen) coordinates into a preference. RootTabView reads them and renders
// TourOverlay full-screen (ignoring safe area) so its local origin == the global origin —
// meaning the captured rects map 1:1 with no coordinate-space drift. (An earlier version
// resolved SwiftUI Anchors through a safe-area-inset proxy, which offset every tab-bar
// highlight ~100pt too high — the "not highlighting the right stuff" bug.)

private struct TourFrameKey: PreferenceKey {
    static var defaultValue: [String: CGRect] = [:]
    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue()) { _, new in new }
    }
}

extension View {
    /// Tags this view as a tour target under `id` (must match a TourManager.Step.id),
    /// recording its global frame.
    func tourAnchor(_ id: String) -> some View {
        background(
            GeometryReader { g in
                Color.clear.preference(key: TourFrameKey.self, value: [id: g.frame(in: .global)])
            }
        )
    }
}

/// Applies `.tourAnchor(id)` only when `id` is non-nil — lets call sites compute the id
/// (e.g. per loop item) without branching the whole view tree.
struct OptionalTourAnchor: ViewModifier {
    let id: String?
    func body(content: Content) -> some View {
        if let id {
            content.background(
                GeometryReader { g in
                    Color.clear.preference(key: TourFrameKey.self, value: [id: g.frame(in: .global)])
                }
            )
        } else {
            content
        }
    }
}

extension View {
    /// Collects every `.tourAnchor` (global frames) in this subtree and hands them to
    /// `overlay`. The overlay is rendered full-screen so its coordinate origin matches the
    /// global origin the frames were captured in.
    func tourOverlay<Overlay: View>(@ViewBuilder overlay: @escaping ([String: CGRect]) -> Overlay) -> some View {
        overlayPreferenceValue(TourFrameKey.self) { frames in
            overlay(frames)
        }
    }
}

// MARK: - The tour overlay

/// Stoic-style coach card (DESIGN.md §5 sheets/cards, §7 motion): a dimmed backdrop with a
/// spotlight hole on the real control, a hairline ring around it, and ONE full-width surface
/// card placed above or below the target so it never covers what it introduces. The card
/// holds the progress dashes + Skip, a flat ink illustration, a tab-name eyebrow, a
/// lowercase-with-period title, one line of copy and a centered primary capsule.
struct TourOverlay: View {
    let tour: TourManager
    let router: AppRouter
    let anchors: [String: CGRect]

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let ringPad: CGFloat = -10
    private let targetGap: CGFloat = Space.md

    /// Measured coach-card height ("bubble" = the card that speaks for the step), so the
    /// card can be placed flush above/below the target without a hardcoded guess.
    @State private var bubbleH: CGFloat = 320

    /// Full-width card: `screenH` margins on every device, so both margins match by
    /// construction (SE through Pro Max).
    private static func bubbleWidth(_ screenW: CGFloat) -> CGFloat {
        max(260, screenW - 2 * Space.screenH)
    }

    var body: some View {
        if let step = tour.current, let target = anchors[step.id] {
            GeometryReader { proxy in
                overlay(step: step, target: target, screen: proxy.size,
                        safeTop: proxy.safeAreaInsets.top)
            }
            .ignoresSafeArea()
            .transition(.opacity)
        }
    }

    @ViewBuilder
    private func overlay(step: TourManager.Step, target: CGRect, screen: CGSize,
                         safeTop: CGFloat) -> some View {
        let hole = target.insetBy(dx: ringPad, dy: ringPad)
        let radius = min(Radius.group + 2, min(hole.width, hole.height) / 2)
        let cardW = Self.bubbleWidth(screen.width)
        // Low targets (the tab bar, the Film button) get the card ABOVE them; high targets
        // (the voice drop) get it BELOW, clamped on screen either way.
        let above = target.midY > screen.height * 0.5
        let topLimit = max(safeTop, 20) + Space.sm
        let centerY: CGFloat = above
            ? max(topLimit + bubbleH / 2, hole.minY - targetGap - bubbleH / 2)
            : min(screen.height - 24 - bubbleH / 2, hole.maxY + targetGap + bubbleH / 2)

        ZStack {
            // Scrim with an ANIMATABLE spotlight hole: the cutout slides + resizes to the next
            // control. It absorbs every touch so a tour tap can never reach a gated control.
            Spotlight(hole: hole, radius: radius)
                .fill(Palette.scrim, style: FillStyle(eoFill: true))
                .contentShape(Rectangle())
                .onTapGesture { }
                .accessibilityHidden(true)

            // Hairline ring on the target (no hue, no glow).
            RoundedRectangle(cornerRadius: radius, style: .continuous)
                .strokeBorder(Palette.onNight, lineWidth: 1.5)
                .frame(width: hole.width, height: hole.height)
                .position(x: hole.midX, y: hole.midY)
                .allowsHitTesting(false)
                .accessibilityHidden(true)

            bubble(step, width: cardW, compact: screen.height < 700)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { bubbleH = $0 }
                .position(x: screen.width / 2, y: centerY)
        }
        // Motion.standard travel between steps; Reduce Motion keeps only the crossfades.
        .animation(reduceMotion ? nil : Motion.standard, value: tour.index)
    }

    private func bubble(_ step: TourManager.Step, width: CGFloat, compact: Bool) -> some View {
        TourCard(step: step, index: tour.index, total: TourManager.steps.count,
                 isLast: tour.isLastStep, compact: compact,
                 onNext: { tour.next(router: router) }, onSkip: { tour.skip() })
            .frame(width: width)
    }
}

// MARK: - Coach card

private struct TourCard: View {
    let step: TourManager.Step
    let index: Int
    let total: Int
    let isLast: Bool
    let compact: Bool            // SE-class heights: smaller art so the card never crowds the target
    let onNext: () -> Void
    let onSkip: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .center) {
                DSProgressDashes(total: total, current: index + 1)
                Spacer(minLength: Space.md)
                Button("Skip", action: onSkip)
                    .font(AppFont.supporting)
                    .foregroundStyle(Palette.textSecondary)
                    .frame(minHeight: 44)
                    .contentShape(Rectangle())
                    .accessibilityLabel("Skip walkthrough")
                    .accessibilityIdentifier("tour.skip")
            }

            // Copy + art crossfade to the next step while the card travels; the dashes,
            // Skip and the capsule stay put so controls never double up mid-transition.
            VStack(spacing: Space.sm) {
                TourArt(name: step.art, height: compact ? 72 : 104)
                    .padding(.bottom, Space.xs)
                DSEyebrow(text: step.eyebrow)
                Text(step.title)
                    .font(AppFont.title2)
                    .tracking(-0.2)
                    .foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                Text(step.message)
                    .font(AppFont.supporting)
                    .foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity)
            .padding(.top, compact ? Space.xs : Space.sm)
            .id(step.id)
            .transition(.opacity)

            Button(action: onNext) {
                Text(isLast ? "Done" : "Next")
            }
            .buttonStyle(.ds(.primary, height: 48))
            .accessibilityIdentifier("tour.next")
            .padding(.top, compact ? Space.md : Space.lg)
        }
        .padding(.horizontal, Space.cardPad)
        .padding(.top, Space.sm)
        .padding(.bottom, Space.cardPad)
        .background(RoundedRectangle(cornerRadius: Radius.card, style: .continuous).fill(Palette.surface))
        .overlay(RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isModal)
    }
}

/// Flat ink illustration, template-rendered so it is ink on paper in light mode and paper
/// on ink in dark mode. Decorative: the title/copy carry the meaning for VoiceOver.
private struct TourArt: View {
    let name: String
    let height: CGFloat
    var body: some View {
        Image(name)
            .renderingMode(.template)
            .resizable()
            .scaledToFit()
            .foregroundStyle(Palette.textPrimary)
            .frame(height: height)
            .accessibilityHidden(true)
    }
}

// MARK: - Spotlight shape

/// Full-screen dim rect with a rounded-rect hole cut at `hole` via the even-odd fill rule.
/// Animatable so the hole slides + resizes smoothly to the next control between steps.
private struct Spotlight: Shape {
    var hole: CGRect
    var radius: CGFloat = 18
    var animatableData: AnimatablePair<AnimatablePair<CGFloat, CGFloat>, AnimatablePair<CGFloat, CGFloat>> {
        get { AnimatablePair(AnimatablePair(hole.origin.x, hole.origin.y),
                             AnimatablePair(hole.size.width, hole.size.height)) }
        set {
            hole = CGRect(x: newValue.first.first, y: newValue.first.second,
                          width: newValue.second.first, height: newValue.second.second)
        }
    }
    func path(in rect: CGRect) -> Path {
        var p = Path(rect)
        p.addRoundedRect(in: hole, cornerSize: CGSize(width: radius, height: radius))
        return p
    }
}
