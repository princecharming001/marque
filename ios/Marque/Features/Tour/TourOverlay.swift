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

struct TourOverlay: View {
    let tour: TourManager
    let router: AppRouter
    let anchors: [String: CGRect]

    private let ringPad: CGFloat = -10

    var body: some View {
        if let step = tour.current, let target = anchors[step.id] {
            GeometryReader { proxy in
                overlay(step: step, target: target, screen: proxy.size)
            }
            .ignoresSafeArea()
            .transition(.opacity)
        }
    }

    // OWNER (2026-08-15, "the guided walkthrough spacing on my phone is wonky…
    // spacing, font, and the yunicorns sized/placed weird"): the cluster used to be
    // FIXED at edge(12) + mascot(104) + gap(6) + bubble(232) = 354pt. On a 393pt
    // iPhone that dumped all 39 leftover points on one side — 12pt margin against
    // 39pt — and on a Pro Max the imbalance grew to 12 vs 76. The bubble width is now
    // DERIVED from the screen, so both margins are `edge` by construction on every
    // device, and the card self-sizes instead of being pinned to a fake 176pt.
    private static let mascotW: CGFloat = 104
    private static let mascotH: CGFloat = mascotW * 1.25   // one source of truth (was 132 vs 130)
    private static let gap: CGFloat = Space.sm
    private static let edge: CGFloat = Space.md

    private static func bubbleWidth(_ screenW: CGFloat) -> CGFloat {
        max(200, screenW - 2 * edge - mascotW - gap)
    }

    /// Measured height of the speech card, so the cluster can be bottom-anchored for
    /// real. The old code positioned by a hardcoded 176 that the card didn't actually
    /// honor — short steps left a hollow band above the buttons, long ones overflowed.
    @State private var bubbleH: CGFloat = 150

    @ViewBuilder
    private func overlay(step: TourManager.Step, target: CGRect, screen: CGSize) -> some View {
        let hole = target.insetBy(dx: ringPad, dy: ringPad)
        let bubbleW = Self.bubbleWidth(screen.width)
        // A target dead-center (the Film button sits at exactly screen.width/2) used to
        // fall to the `false` branch and throw the whole cluster off-axis under a
        // perfectly centered control. Treat near-center as "point from the left".
        let centered = abs(target.midX - screen.width / 2) < 40
        let peekLeft = centered ? true : target.midX < screen.width * 0.5
        let below = target.midY < screen.height * 0.55
        // Bottom edge the bubble + mascot both sit on (below the target up top, above it
        // when it's down low, so the cluster never covers what it points at).
        let bottomY: CGFloat = below ? min(target.maxY + 24 + bubbleH, screen.height - 24)
                                     : max(target.minY - 24, 24 + bubbleH)
        let mascotX: CGFloat = peekLeft ? Self.edge + Self.mascotW / 2
                                        : screen.width - Self.edge - Self.mascotW / 2
        let bubbleX: CGFloat = peekLeft
            ? Self.edge + Self.mascotW + Self.gap + bubbleW / 2
            : screen.width - Self.edge - Self.mascotW - Self.gap - bubbleW / 2

        ZStack {
            // Dimmed backdrop with an ANIMATABLE spotlight hole — the dark cutout slides +
            // resizes to the next control instead of snapping. Absorbs every touch so a
            // tour tap can never leak to a paywall-gated control behind it.
            Spotlight(hole: hole)
                .fill(Color.black.opacity(0.62), style: FillStyle(eoFill: true))
                .contentShape(Rectangle())
                .onTapGesture { }

            // Accent ring travels + resizes with the highlight.
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Palette.accent, lineWidth: 3)
                .frame(width: hole.width, height: hole.height)
                .position(x: hole.midX, y: hole.midY)
                .shadow(color: Palette.accent.opacity(0.5), radius: 8)
                .allowsHitTesting(false)

            // Bubble — stable identity, so its .position animates: it TRAVELS to the next
            // step rather than fading in and out. (Its text crossfades inside — see below.)
            bubble(step, width: bubbleW)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { bubbleH = $0 }
                .position(x: bubbleX, y: bottomY - bubbleH / 2)

            // Mascot — the frame travels while the POSE crossfades to the next one, so Yuni
            // glides toward the next control and changes pose on the way.
            ZStack {
                TourMascotView(resource: step.mascot, size: Self.mascotW, mirrored: !peekLeft)
                    .id(step.mascot)
                    .transition(.opacity)
            }
            .frame(width: Self.mascotW, height: Self.mascotH, alignment: .bottom)
            .position(x: mascotX, y: bottomY - Self.mascotH / 2)
        }
        .animation(.spring(response: 0.5, dampingFraction: 0.82), value: tour.index)
    }

    private func bubble(_ step: TourManager.Step, width: CGFloat) -> some View {
        TourSpeechBubble(
            step: step, width: width,
            index: tour.index,
            total: TourManager.steps.count,
            isLast: tour.isLastStep,
            onNext: { tour.next(router: router) },
            onSkip: { tour.skip() }
        )
    }
}

// MARK: - Static per-step mascot

/// A single static Yuni pose, fully visible beside the bubble. No motion — the character
/// holds a whimsical pose (wave / lean / point / chill / cheer) that differs per step.
private struct TourMascotView: View {
    let resource: String
    let size: CGFloat
    var mirrored: Bool

    var body: some View {
        Group {
            if UIImage(named: resource) != nil {
                Image(resource).resizable().scaledToFit()
                    .scaleEffect(x: mirrored ? -1 : 1, y: 1)
            } else {
                UnicornMascot(pose: .hero, size: size * 0.9)   // fallback keeps the tour intact
            }
        }
        // The poses' natural aspects run from 0.59 (Cheer, tall) to 1.65 (Chill, wide),
        // and scaledToFit centers the letterbox — so wide poses used to float ~34pt off
        // the shared baseline while tall ones sat on it ("yunicorns placed weird").
        // Bottom-aligning puts every pose's feet on the same line as the bubble's edge.
        .frame(width: size, height: size * 1.25, alignment: .bottom)
    }
}

// MARK: - Speech bubble

private struct TourSpeechBubble: View {
    let step: TourManager.Step
    let width: CGFloat
    let index: Int
    let total: Int
    let isLast: Bool
    let onNext: () -> Void
    let onSkip: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            progressDots
            // Title + message crossfade to the next step's copy while the card travels;
            // the progress dots and controls stay put so buttons never double up.
            VStack(alignment: .leading, spacing: Space.sm) {
                // Sans, not the Fraunces Black display face: serif is reserved for
                // screen titles, and the heavy cut wrapped these short titles onto
                // two lines inside a narrow card — the main source of the cramped
                // look. Sans at 18 fits every step's title on one line.
                Text(step.title)
                    .font(Typeface.sans(18, .semibold)).tracking(Track.tight)
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(step.message)
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .id(step.id)
            .transition(.opacity)
            controls
                .padding(.top, Space.xs)
        }
        .padding(Space.lg)
        // Self-sizing: the old fixed 176pt left a hollow band under short steps and
        // squeezed long ones. Height now follows the copy.
        .frame(width: width, alignment: .topLeading)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .shadow(color: .black.opacity(0.28), radius: 22, y: 10)
    }

    private var progressDots: some View {
        HStack(spacing: 5) {
            ForEach(0..<total, id: \.self) { i in
                let isCurrent = i == index
                Capsule()
                    .fill(isCurrent ? Palette.accent : Palette.textTertiary.opacity(0.35))
                    .frame(width: isCurrent ? 14 : 5, height: 5)
            }
        }
    }

    private var controls: some View {
        HStack {
            Button("Skip", action: onSkip)
                .font(AppFont.callout).foregroundStyle(Palette.textTertiary)
                .accessibilityIdentifier("tour.skip")
            Spacer()
            Button(action: onNext) { nextLabel }
                .buttonStyle(PressableStyle())
                .accessibilityIdentifier("tour.next")
        }
    }

    private var nextLabel: some View {
        Text(isLast ? "Got it" : "Next")
            .font(AppFont.callout).foregroundStyle(Palette.onInk)
            .padding(.horizontal, Space.lg).frame(height: 38)
            .background(Palette.ink).clipShape(Capsule())
    }
}

// MARK: - Spotlight shape

/// Full-screen dim rect with a rounded-rect hole cut at `hole` via the even-odd fill rule.
/// Animatable so the hole slides + resizes smoothly to the next control between steps.
private struct Spotlight: Shape {
    var hole: CGRect
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
        p.addRoundedRect(in: hole, cornerSize: CGSize(width: 18, height: 18))
        return p
    }
}
