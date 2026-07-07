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

    @ViewBuilder
    private func overlay(step: TourManager.Step, target: CGRect, screen: CGSize) -> some View {
        let hole = target.insetBy(dx: ringPad, dy: ringPad)
        ZStack {
            // Dimmed backdrop with a spotlight hole. Absorbs EVERY touch so a tour tap can
            // never leak through to a paywall-gated control on the screen behind it.
            Spotlight(hole: hole)
                .fill(Color.black.opacity(0.62), style: FillStyle(eoFill: true))
                .contentShape(Rectangle())
                .onTapGesture { }

            // Bright accent ring so it's unmistakable which control the step points at
            // (a plain white ring vanished against light cards like the greeting).
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(Palette.accent, lineWidth: 3)
                .frame(width: hole.width, height: hole.height)
                .position(x: hole.midX, y: hole.midY)
                .shadow(color: Palette.accent.opacity(0.5), radius: 8)
                .allowsHitTesting(false)

            coachCluster(step: step, target: target, screen: screen)
        }
        .animation(Motion.calm, value: step.id)
    }

    // Fixed geometry so the cluster can be positioned exactly on-screen (no clipping).
    private static let bubbleW: CGFloat = 232
    private static let mascotW: CGFloat = 104
    private static let gap: CGFloat = 6
    private static let edge: CGFloat = 12
    private var clusterW: CGFloat { Self.bubbleW + Self.gap + Self.mascotW }

    /// A distinct STATIC Yuni pose sits NEXT TO the bubble (never behind/clipped), on the
    /// side nearest the highlight so it reads as leaning in toward it. Sits below the target
    /// when it's up top, above when it's down low, so it never covers what it points at.
    @ViewBuilder
    private func coachCluster(step: TourManager.Step, target: CGRect, screen: CGSize) -> some View {
        let peekLeft = target.midX < screen.width * 0.5
        let below = target.midY < screen.height * 0.55
        HStack(alignment: .bottom, spacing: Self.gap) {
            if peekLeft {
                mascot(step, mirrored: false)   // generated facing right → faces the bubble
                bubble(step)
            } else {
                bubble(step)
                mascot(step, mirrored: true)    // flip → faces left, toward the bubble
            }
        }
        .frame(width: clusterW, alignment: .bottom)
        .position(x: peekLeft ? clusterW / 2 + Self.edge : screen.width - clusterW / 2 - Self.edge,
                  y: below ? min(target.maxY + 104, screen.height - 150)
                           : max(target.minY - 104, 170))
        .transition(.opacity.combined(with: .scale(scale: 0.96)))
    }

    private func mascot(_ step: TourManager.Step, mirrored: Bool) -> some View {
        TourMascotView(resource: step.mascot, size: Self.mascotW, mirrored: mirrored)
    }

    private func bubble(_ step: TourManager.Step) -> some View {
        TourSpeechBubble(
            step: step, width: Self.bubbleW,
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
        .frame(width: size, height: size * 1.25)
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
            Text(step.title)
                .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
            Text(step.message)
                .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            controls
        }
        .padding(Space.lg)
        .frame(width: width, alignment: .leading)
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
private struct Spotlight: Shape {
    let hole: CGRect
    func path(in rect: CGRect) -> Path {
        var p = Path(rect)
        p.addRoundedRect(in: hole, cornerSize: CGSize(width: 18, height: 18))
        return p
    }
}
