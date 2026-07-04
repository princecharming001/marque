import SwiftUI

// The voice visual — an exact-style Siri orb clone built the way the convincing clones
// are built: hand-drawn organic blob assets (the actual petal shapes — vendored from
// GetStream's purposeful-ios-animations Siri recreation) layered in a ZStack, each
// spinning around the orb center at its own differential rate and direction, with
// continuous hue cycling and 3D-tilted rotation planes so petals foreshorten as they
// sweep around the sphere. That layered differential rotation + hue drift is the real
// Siri motion; procedural math never quite gets there.
//
// Adaptation from the source prototype: the original drove a single 12s repeatForever
// animation (which visually snaps at the loop seam); here a TimelineView clock drives
// the same per-layer angular rates CONTINUOUSLY (no seam), and the clock is
// volume-warped so `level` (live mic RMS / TTS metering) speeds the swirl and brightens
// the highlight exactly like Siri reacting to speech. `thinking` holds a brisk steady
// swirl; `idle` turns slowly.
struct VoiceOrb: View {
    enum Mode { case idle, listening, thinking, speaking }
    var mode: Mode = .idle
    var level: Double = 0
    var size: CGFloat = 132

    // Reference-type clock mutated during frame evaluation (safe: not @State-observed;
    // TimelineView's tick drives frames). Integrates volume-warped time so swirl speed
    // follows the voice without phase jumps.
    @State private var clock = MotionClock()

    private var clampedLevel: Double { min(1, max(0, level)) }

    /// Native canvas of the vendored assets (icon-bg diameter in points).
    private static let assetSize: CGFloat = 503.58

    private var speedMul: Double {
        switch mode {
        case .idle:      return 0.45
        case .thinking:  return 1.2
        case .listening, .speaking: return 0.7 + 1.8 * clampedLevel
        }
    }

    var body: some View {
        TimelineView(.animation) { timeline in
            let state = clock.update(target: clampedLevel,
                                     at: timeline.date.timeIntervalSinceReferenceDate,
                                     speedMul: speedMul)
            orbBody(t: state.1, lvl: state.0)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    // Per-layer angular rates (deg/s) and hue rates (deg/s) derived from the source
    // prototype's 12s keyframes ((to - from) / 12), kept in original stacking order.
    private func orbBody(t: Double, lvl: Double) -> some View {
        ZStack {
            Image("shadow")
            Image("icon-bg")

            Group {
                Image("pink-top")
                    .rotationEffect(.degrees(t * 56.7))
                    .hueRotation(.degrees(t * -27.5))
                Image("pink-left")
                    .rotationEffect(.degrees(t * -45.0))
                    .hueRotation(.degrees(t * -43.3))
                Image("blue-middle")
                    .rotationEffect(.degrees(t * -65.0))
                    .hueRotation(.degrees(t * -12.5))
                    .rotation3DEffect(.degrees(75), axis: (x: 3 + 2 * sin(t * 0.26), y: 0, z: 0))
                Image("blue-right")
                    .rotationEffect(.degrees(t * -65.0))
                    .hueRotation(.degrees(t * 64.2))
                    .rotation3DEffect(.degrees(75), axis: (x: 1, y: 0, z: 5 + 10 * sin(t * 0.21)))
                Image("Intersect")
                    .rotationEffect(.degrees(t * 37.5))
                    .hueRotation(.degrees(t * -60.0))
                    .rotation3DEffect(.degrees(15), axis: (x: 1, y: 1, z: 1),
                                      perspective: 5 * sin(t * 0.24))
                Image("green-right")
                    .rotationEffect(.degrees(t * -55.0))
                    .hueRotation(.degrees(t * 26.3))
                    .rotation3DEffect(.degrees(15), axis: (x: 1, y: sin(t * 0.3), z: 0),
                                      perspective: -sin(t * 0.3))
                Image("green-left")
                    .rotationEffect(.degrees(t * 60.0))
                    .hueRotation(.degrees(t * 10.8))
                    .rotation3DEffect(.degrees(75), axis: (x: 1, y: 5 + 10 * sin(t * 0.19), z: 0))
                Image("bottom-pink")
                    .rotationEffect(.degrees(t * 63.3))
                    .hueRotation(.degrees(t * -19.2))
                    .opacity(0.25)
                    .blendMode(.multiply)
                    .rotation3DEffect(.degrees(75), axis: (x: 5, y: -22 + 23 * sin(t * 0.17), z: 0))
            }
            .blendMode(.hardLight)

            Image("highlight")
                .rotationEffect(.degrees(t * 9.2))
                .hueRotation(.degrees(t * -19.2))
                .opacity(0.85 + 0.15 * lvl)
                .scaleEffect(1 + 0.06 * lvl)
        }
        .scaleEffect(size / Self.assetSize)
        .frame(width: size, height: size)
    }
}

/// Smooths raw metering into an analog level and integrates a volume-warped clock, so
/// swirl speed follows the voice without the phase jumps a naive `time * speed` causes.
private final class MotionClock {
    private var level: Double = 0
    private var warped: Double = 0
    private var lastTime: Double?

    func update(target: Double, at time: Double, speedMul: Double) -> (Double, Double) {
        let dt = lastTime.map { min(0.1, max(0, time - $0)) } ?? 1.0 / 60.0
        lastTime = time
        level += (target - level) * min(1, 8.0 * dt)
        warped += dt * speedMul
        return (level, warped)
    }
}
