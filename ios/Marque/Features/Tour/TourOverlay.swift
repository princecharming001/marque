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

    /// Yuni peeking in from the corner next to the highlight, with a compact speech bubble.
    /// Sits below the target when it's up top, above it when it's down low, so it never
    /// covers what it points at.
    @ViewBuilder
    private func coachCluster(step: TourManager.Step, target: CGRect, screen: CGSize) -> some View {
        let peekLeft = target.midX < screen.width * 0.5
        let below = target.midY < screen.height * 0.55
        // The unicorn sits BEHIND the bubble (zIndex -1) so it peeks out from behind the
        // corner and can never cover the Skip/Next controls (which it did at zIndex 1 —
        // making them untappable). Overlap tucks its body behind the bubble edge.
        HStack(alignment: .bottom, spacing: -30) {
            if peekLeft {
                UnicornPeekView(mirrored: false).zIndex(-1)
                bubble(step)
            } else {
                bubble(step)
                UnicornPeekView(mirrored: true).zIndex(-1)
            }
        }
        .fixedSize()
        .position(x: clusterX(peekLeft: peekLeft, screen: screen),
                  y: below ? min(target.maxY + 108, screen.height - 150)
                           : max(target.minY - 108, 160))
        .transition(.opacity.combined(with: .scale(scale: 0.95)))
    }

    /// Keep the whole cluster comfortably on-screen, biased toward the peek side.
    private func clusterX(peekLeft: Bool, screen: CGSize) -> CGFloat {
        peekLeft ? screen.width * 0.42 : screen.width * 0.58
    }

    private func bubble(_ step: TourManager.Step) -> some View {
        TourSpeechBubble(
            step: step,
            index: tour.index,
            total: TourManager.steps.count,
            isLast: tour.isLastStep,
            onNext: { tour.next(router: router) },
            onSkip: { tour.skip() }
        )
    }
}

// MARK: - Peeking unicorn

/// The Yuni "peek + wave" render (transparent). A gentle seamless wobble reads as an
/// excited "hi!" wave. `mirrored` flips it to peek from the right edge instead of the left.
private struct UnicornPeekView: View {
    var mirrored: Bool
    @State private var start: Date = .now

    var body: some View {
        TimelineView(.animation) { ctx in
            let t = ctx.date.timeIntervalSince(start)
            let tilt = sin(t * 2.2) * 5.0    // seamless: pure sines, equal at loop endpoints
            let bob = sin(t * 1.6) * 3.0
            image
                .rotationEffect(.degrees(mirrored ? -tilt : tilt), anchor: .bottom)
                .offset(y: bob)
        }
        .frame(width: 96, height: 132)
    }

    @ViewBuilder private var image: some View {
        if UIImage(named: "UnicornPeek") != nil {
            Image("UnicornPeek").resizable().scaledToFit()
                .scaleEffect(x: mirrored ? -1 : 1, y: 1)
        } else {
            UnicornMascot(pose: .hero, size: 92)   // fallback keeps the tour intact
        }
    }
}

// MARK: - Speech bubble

private struct TourSpeechBubble: View {
    let step: TourManager.Step
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
        .frame(width: 250, alignment: .leading)
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
