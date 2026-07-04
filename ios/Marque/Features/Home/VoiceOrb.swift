import SwiftUI

// The voice visual — an exact-style Siri orb clone. AESTHETIC: hand-drawn organic petal
// assets (vendored from GetStream's purposeful-ios-animations Siri recreation) layered
// over the dark glass sphere, each spinning at its own differential rate/direction with
// continuous hue cycling and 3D-tilted rotation planes — the layered-asset technique
// every convincing clone uses.
//
// MOTION while talking (researched against the real orb + audio-reactive clone
// implementations): Siri doesn't just spin faster with volume — it BOUNCES per syllable.
// The pipeline here reproduces that:
//   mic/TTS level → asymmetric envelope follower (fast ~35ms attack, slow ~250ms
//   release, so each syllable registers as a distinct hit) → damped springs with mild
//   overshoot (elastic bounce, never a linear scale map) → four PER-PETAL springs with
//   different stiffness/damping so petals respond staggered and the orb deforms
//   organically instead of zooming uniformly. Speech energy also kicks the swirl's
//   angular velocity transiently and flares the core highlight. Idle breathes slowly;
//   thinking holds a brisk steady swirl with a gentle simmer.
struct VoiceOrb: View {
    enum Mode { case idle, listening, thinking, speaking }
    var mode: Mode = .idle
    var level: Double = 0
    var size: CGFloat = 132

    // Reference-type physics rig mutated during frame evaluation (safe: not
    // @State-observed; TimelineView's tick drives frames, the rig carries envelope,
    // spring, and warped-clock state across them).
    @State private var physics = OrbPhysics()

    private var clampedLevel: Double { min(1, max(0, level)) }

    /// Native canvas of the vendored assets (icon-bg diameter in points).
    private static let assetSize: CGFloat = 503.58

    var body: some View {
        TimelineView(.animation) { timeline in
            let s = physics.step(now: timeline.date.timeIntervalSinceReferenceDate,
                                 rawLevel: clampedLevel, mode: mode)
            orbBody(s)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    // Per-layer angular rates (deg/s) and hue rates (deg/s) derived from the source
    // prototype's 12s keyframes ((to - from) / 12), kept in original stacking order.
    // Petal spring assignment staggers the bounce: pinks (0), blues (1), greens (2),
    // intersect (3), highlight rides the global spring.
    private func orbBody(_ s: OrbPhysics.State) -> some View {
        let t = s.time
        return ZStack {
            Image("shadow")
                .scaleEffect(1 + 0.05 * s.global)
            Image("icon-bg")

            Group {
                Image("pink-top")
                    .scaleEffect(1 + 0.16 * s.petals[0])
                    .rotationEffect(.degrees(t * 56.7))
                    .hueRotation(.degrees(t * -27.5))
                Image("pink-left")
                    .scaleEffect(1 + 0.13 * s.petals[0])
                    .rotationEffect(.degrees(t * -45.0))
                    .hueRotation(.degrees(t * -43.3))
                Image("blue-middle")
                    .scaleEffect(1 + 0.15 * s.petals[1])
                    .rotationEffect(.degrees(t * -65.0))
                    .hueRotation(.degrees(t * -12.5))
                    .rotation3DEffect(.degrees(75), axis: (x: 3 + 2 * sin(t * 0.26), y: 0, z: 0))
                Image("blue-right")
                    .scaleEffect(1 + 0.12 * s.petals[1])
                    .rotationEffect(.degrees(t * -65.0))
                    .hueRotation(.degrees(t * 64.2))
                    .rotation3DEffect(.degrees(75), axis: (x: 1, y: 0, z: 5 + 10 * sin(t * 0.21)))
                Image("Intersect")
                    .scaleEffect(1 + 0.11 * s.petals[3])
                    .rotationEffect(.degrees(t * 37.5))
                    .hueRotation(.degrees(t * -60.0))
                    .rotation3DEffect(.degrees(15), axis: (x: 1, y: 1, z: 1),
                                      perspective: 5 * sin(t * 0.24))
                Image("green-right")
                    .scaleEffect(1 + 0.14 * s.petals[2])
                    .rotationEffect(.degrees(t * -55.0))
                    .hueRotation(.degrees(t * 26.3))
                    .rotation3DEffect(.degrees(15), axis: (x: 1, y: sin(t * 0.3), z: 0),
                                      perspective: -sin(t * 0.3))
                Image("green-left")
                    .scaleEffect(1 + 0.12 * s.petals[2])
                    .rotationEffect(.degrees(t * 60.0))
                    .hueRotation(.degrees(t * 10.8))
                    .rotation3DEffect(.degrees(75), axis: (x: 1, y: 5 + 10 * sin(t * 0.19), z: 0))
                Image("bottom-pink")
                    .scaleEffect(1 + 0.10 * s.petals[0])
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
                .opacity(0.62 + 0.38 * min(1, s.global))
                .scaleEffect(1 + 0.18 * s.global)
        }
        .scaleEffect((size / Self.assetSize) * (1 + 0.06 * s.global))
        .frame(width: size, height: size)
    }
}

// MARK: - Physics rig

/// Envelope follower + damped springs + volume-warped clock. The asymmetric follower
/// makes each syllable register as a distinct hit; the springs turn hits into elastic
/// bounces with overshoot; per-petal spring constants stagger the response so the orb
/// deforms organically rather than zooming as one rigid unit.
private final class OrbPhysics {
    struct State {
        var time: Double        // volume-warped clock driving all rotation/hue rates
        var global: Double      // main bounce spring (highlight, whole-orb swell)
        var petals: [Double]    // four staggered petal springs
    }

    private struct Spring {
        var pos = 0.0
        var vel = 0.0
        let stiffness: Double
        let damping: Double
        mutating func step(target: Double, dt: Double) {
            let acc = stiffness * (target - pos) - damping * vel
            vel += acc * dt
            pos += vel * dt
            if pos < 0 { pos = 0; vel = max(0, vel) }
        }
    }

    private var envelope = 0.0
    private var warped = 0.0
    private var lastTime: Double?
    private var global = Spring(stiffness: 140, damping: 11)    // mild overshoot
    private var petalSprings = [
        Spring(stiffness: 120, damping: 10),
        Spring(stiffness: 165, damping: 13),
        Spring(stiffness: 95, damping: 9),
        Spring(stiffness: 145, damping: 12),
    ]

    func step(now: Double, rawLevel: Double, mode: VoiceOrb.Mode) -> State {
        let dt = lastTime.map { min(0.1, max(0, now - $0)) } ?? 1.0 / 60.0
        lastTime = now

        // Mode → the signal the envelope chases.
        let target: Double
        switch mode {
        case .idle:      target = 0.05 + 0.04 * sin(now * 0.7)   // slow breathing
        case .thinking:  target = 0.22                            // steady simmer
        case .listening, .speaking: target = rawLevel
        }

        // Asymmetric follower: ~35ms attack registers each syllable, ~250ms release
        // lets it ring down between them instead of averaging speech into a blur.
        let rate = target > envelope ? 28.0 : 4.5
        envelope += (target - envelope) * min(1, rate * dt)

        global.step(target: envelope, dt: dt)
        for i in petalSprings.indices {
            petalSprings[i].step(target: envelope, dt: dt)
        }

        // Swirl speed: mode base + sustained speech energy + transient agitation from
        // the spring's velocity (bursts kick the rotation, then it relaxes).
        let base: Double
        switch mode {
        case .idle:      base = 0.45
        case .thinking:  base = 1.25
        case .listening, .speaking: base = 0.7
        }
        let agitation = mode == .idle ? 0.0 : 1.9 * envelope + 0.10 * min(3, abs(global.vel))
        warped += dt * (base + agitation)

        return State(time: warped, global: global.pos, petals: petalSprings.map(\.pos))
    }
}
