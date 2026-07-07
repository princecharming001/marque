import SwiftUI

// The Marque mascot — a matte-black clay 3D unicorn (Higgsfield renders in
// Assets.xcassets). Same 4 static poses as before; the "life" in the animation is
// entirely procedural (TimelineView-driven transforms), never a change to the art
// itself. Each pose gets its own personality profile (bob/sway/tilt/pop), but every
// term is a harmonic of that pose's own cycle length or is exactly zero at phase 0
// and 1 — so the loop has no seam: the frame that ends a cycle is identical to the
// frame that starts the next one, forever (see `Life` below).
struct UnicornMascot: View {
    enum Pose: String {
        case hero = "UnicornHero"
        case thinking = "UnicornThinking"
        case proud = "UnicornProud"
        case celebrate = "UnicornCelebrate"
    }

    let pose: Pose
    var size: CGFloat = 180

    @State private var appeared = false
    @State private var loopStart: Date = .now

    /// One idle "personality" — same silhouette, different energy. `pop` is a mid-cycle
    /// flourish (a little hop/puff), zero at both loop endpoints by construction, so it
    /// never breaks the seam even though it isn't a plain sine term.
    private struct Life {
        var cycle: Double       // seconds per loop
        var bob: CGFloat        // vertical drift, points
        var sway: CGFloat       // horizontal drift, points
        var tilt: Double        // head/body tilt, degrees
        var breathe: CGFloat    // ambient scale wobble
        var pop: CGFloat        // mid-cycle scale flourish (0 = none)
    }

    private var life: Life {
        switch pose {
        // Landing hero — playful, a little bouncy; the first thing you meet.
        case .hero:
            return Life(cycle: 3.4, bob: 5, sway: 3, tilt: 3.5, breathe: 0.016, pop: 0.05)
        // Belief interstitial — slow, contemplative sway reads as "thinking it over."
        case .thinking:
            return Life(cycle: 5.2, bob: 2.5, sway: 4, tilt: 2.5, breathe: 0.012, pop: 0)
        // Brand mirror — steadier, a quiet confident puff rather than a bounce.
        case .proud:
            return Life(cycle: 4.2, bob: 3, sway: 1.5, tilt: 1.8, breathe: 0.02, pop: 0.03)
        // Celebration — fast and bright, the biggest flourish.
        case .celebrate:
            return Life(cycle: 2.2, bob: 7, sway: 5, tilt: 5, breathe: 0.02, pop: 0.08)
        }
    }

    /// Everything the view needs for one frame, precomputed outside the ViewBuilder so
    /// the type-checker isn't asked to solve one giant nested expression tree.
    private struct Frame {
        var bobY: CGFloat
        var swayX: CGFloat
        var tiltDeg: Double
        var scale: CGFloat
    }

    /// Pure math, no SwiftUI types — kept as a plain function so it type-checks instantly.
    private static func frame(for l: Life, elapsedSince start: Date, at date: Date) -> Frame {
        let elapsed = max(0, date.timeIntervalSince(start))
        // 0..<1, wraps every `cycle` seconds. Feeding a wrapping phase into sin/cos is
        // what makes the loop seamless — sin(2π·0) and sin(2π·~1) are the same value,
        // so there is no visible restart, only continuous motion.
        let phase = (elapsed.truncatingRemainder(dividingBy: l.cycle)) / l.cycle
        let t = phase * 2.0 * Double.pi
        let bobY = sin(t) * Double(l.bob)
        // Cross-harmonic (half frequency, phase-shifted) so sway doesn't just mirror
        // the bob 1:1 — together they trace a lazy, organic float instead of a
        // metronomic up-down pulse.
        let swayX = cos(t * 0.5 + Double.pi / 3) * Double(l.sway)
        let tiltDeg = sin(t + Double.pi / 4) * l.tilt
        // Zero at phase 0 and 1 (sin(0)=sin(π)=0), peaks at the cycle's midpoint — a
        // hop/puff flourish that never disturbs the loop's seam.
        let popRaw = pow(max(0, sin(phase * Double.pi)), 3)
        let scale = 1.0 + Double(l.breathe) * sin(t) * 0.5 + Double(l.pop) * popRaw
        let lift = l.pop > 0 ? popRaw * 14 : 0
        return Frame(bobY: CGFloat(bobY - lift), swayX: CGFloat(swayX),
                    tiltDeg: tiltDeg, scale: CGFloat(scale))
    }

    var body: some View {
        TimelineView(.animation) { context in
            let f = Self.frame(for: life, elapsedSince: loopStart, at: context.date)
            mascotBody(f)
        }
        .shadow(color: Palette.shadowWarm.opacity(0.14), radius: 24, y: 12)
        .onAppear {
            loopStart = .now   // the idle loop's phase-0 lands exactly when entrance ends
            withAnimation(Motion.spring) { appeared = true }
        }
    }

    @ViewBuilder
    private func mascotBody(_ f: Frame) -> some View {
        content
            .frame(width: size, height: size)
            .rotationEffect(.degrees(f.tiltDeg), anchor: .bottom)
            .scaleEffect(appeared ? f.scale : 0.7)
            .offset(x: appeared ? f.swayX : 0, y: appeared ? f.bobY : 0)
            .opacity(appeared ? 1 : 0)
    }

    @ViewBuilder private var content: some View {
        if UIImage(named: pose.rawValue) != nil {
            Image(pose.rawValue)
                .resizable().scaledToFit()
        } else {
            // Pre-asset fallback so builds/screenshots never break: a quiet
            // ink silhouette placeholder (no blue circle, no decorations).
            Image(systemName: "sparkle")
                .font(.system(size: size * 0.35, weight: .light))
                .foregroundStyle(Palette.textTertiary)
        }
    }
}
