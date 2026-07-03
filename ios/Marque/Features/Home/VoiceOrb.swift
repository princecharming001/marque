import SwiftUI

// The voice visual — a Siri orb clone. Dark glass sphere containing large translucent
// color petals (teal / cyan / magenta / crimson) that morph fluidly and cross at a
// blazing white flare, compositing additively so overlaps bloom toward white exactly
// like the reference. The MOTION is ported from the reverse-engineered Siri wave
// lifecycle (kopiro/siriwave, iOS9 curve): each petal runs a GENERATION of 2-4 radial
// harmonics with random amplitude/frequency/speed that ramp up, hold 0.8-2.5s, collapse,
// and respawn in a fresh random arrangement — while each harmonic's phase advances at
// its own speed, so ripples travel THROUGH the petal outline rather than the petal
// rigidly rotating. That generational swell → churn → collapse → reborn rhythm is what
// makes Siri feel alive.
//
// Function matches Siri too: `level` (live mic RMS while listening / TTS metering while
// speaking) drives petal deformation, churn speed, and flare brightness — quiet input
// calms the orb toward a gentle simmer, speech makes it surge. `thinking` holds a
// steady level-independent churn; `idle` breathes slowly.
struct VoiceOrb: View {
    enum Mode { case idle, listening, thinking, speaking }
    var mode: Mode = .idle
    var level: Double = 0
    var size: CGFloat = 132

    // Reference-type engine mutated during frame evaluation (safe: not @State-observed;
    // TimelineView's tick drives frames, the engine carries generation state across them).
    @State private var engine = SiriOrbEngine()

    private var clampedLevel: Double { min(1, max(0, level)) }

    var body: some View {
        TimelineView(.animation) { timeline in
            Canvas { context, canvasSize in
                let now = timeline.date.timeIntervalSinceReferenceDate
                let target: Double
                let speedMul: Double
                switch mode {
                case .idle:      target = 0.30 + 0.08 * sin(now * 0.7); speedMul = 0.55
                case .thinking:  target = 0.55;                          speedMul = 1.1
                case .listening, .speaking:
                    target = 0.35 + 0.65 * clampedLevel
                    speedMul = 0.8 + 1.6 * clampedLevel
                }
                engine.step(now: now, target: target, speedMul: speedMul)
                engine.draw(&context, canvasSize: canvasSize)
            }
        }
        .frame(width: size, height: size)
        .shadow(color: Color(hex: 0x1B2B4D).opacity(0.45), radius: size * 0.13, x: 0, y: size * 0.05)
        .accessibilityHidden(true)
    }
}

// MARK: - Engine

private final class SiriOrbEngine {
    // Lifecycle constants carried over from the siriwave iOS9 curve.
    private let DESPAWN = 0.02          // amplitude ramp per 60fps frame
    private let PHASE_SPEED = 0.10      // phase advance per 60fps frame (radians)

    private struct Harmonic {
        var freq: Double        // integer lobe count 1-3 keeps petals organic, not starry
        var phase: Double
        var amp: Double
        var final: Double
        var speed: Double
        var despawn: Double
    }

    private final class Petal {
        let color: Color
        let orbitPhase: Double      // where its center wanders (lissajous phase)
        let orbitRate: Double
        let aspect: CGFloat         // squash → elongated petal like the reference
        let rotRate: Double         // slow drift of the squash axis
        var harmonics: [Harmonic] = []
        var spawnAt: Double = 0
        var prevMaxAmp: Double = 0
        init(color: Color, orbitPhase: Double, orbitRate: Double, aspect: CGFloat, rotRate: Double) {
            self.color = color
            self.orbitPhase = orbitPhase
            self.orbitRate = orbitRate
            self.aspect = aspect
            self.rotRate = rotRate
        }
    }

    // Reference-image petals: teal, cyan-blue, magenta-pink, crimson.
    private let petals = [
        Petal(color: Color(red: 0.09, green: 0.90, blue: 0.78), orbitPhase: 0.0, orbitRate: 0.23, aspect: 0.72, rotRate: 0.11),
        Petal(color: Color(red: 0.18, green: 0.70, blue: 1.00), orbitPhase: 2.1, orbitRate: -0.17, aspect: 0.80, rotRate: -0.09),
        Petal(color: Color(red: 1.00, green: 0.31, blue: 0.64), orbitPhase: 4.2, orbitRate: 0.19, aspect: 0.68, rotRate: 0.13),
        Petal(color: Color(red: 0.96, green: 0.16, blue: 0.34), orbitPhase: 1.1, orbitRate: -0.21, aspect: 0.76, rotRate: -0.07),
    ]

    private var amplitude: Double = 0
    private var warped: Double = 0      // volume-warped clock → churn speed follows voice
    private var lastTime: Double?

    private func rand(_ lo: Double, _ hi: Double) -> Double { .random(in: lo...hi) }

    private func spawn(_ petal: Petal, now: Double) {
        petal.spawnAt = now
        let n = Int(rand(2, 4.99))
        petal.harmonics = (0..<n).map { _ in
            Harmonic(freq: Double(Int(rand(1, 3.99))),
                     phase: rand(0, 2 * .pi),
                     amp: 0,
                     final: rand(0.3, 1),
                     speed: rand(0.5, 1),
                     despawn: rand(0.8, 2.5))
        }
    }

    func step(now: Double, target: Double, speedMul: Double) {
        let dt = lastTime.map { min(0.1, max(0, now - $0)) } ?? 1.0 / 60.0
        lastTime = now
        amplitude += (target - amplitude) * min(1, 7.0 * dt)
        warped += dt * speedMul
        let frames = dt * 60.0

        for petal in petals {
            if petal.spawnAt == 0 { spawn(petal, now: now) }
            var maxAmp = 0.0
            for h in petal.harmonics.indices {
                if now - petal.spawnAt >= petal.harmonics[h].despawn {
                    petal.harmonics[h].amp -= DESPAWN * frames
                } else {
                    petal.harmonics[h].amp += DESPAWN * frames
                }
                petal.harmonics[h].amp = min(max(petal.harmonics[h].amp, 0), petal.harmonics[h].final)
                petal.harmonics[h].phase += PHASE_SPEED * petal.harmonics[h].speed * speedMul * frames
                maxAmp = max(maxAmp, petal.harmonics[h].amp)
            }
            // Generation flattened out → respawn a fresh random arrangement.
            if maxAmp < 0.02 && petal.prevMaxAmp > maxAmp {
                petal.spawnAt = 0
            }
            petal.prevMaxAmp = maxAmp
        }
    }

    func draw(_ context: inout GraphicsContext, canvasSize: CGSize) {
        let S = min(canvasSize.width, canvasSize.height)
        let center = CGPoint(x: canvasSize.width / 2, y: canvasSize.height / 2)
        let R = S / 2

        // --- Dark glass sphere ---
        let disc = Path(ellipseIn: CGRect(x: center.x - R, y: center.y - R, width: R * 2, height: R * 2))
        context.fill(disc, with: .linearGradient(
            Gradient(colors: [Color(hex: 0x101B33), Color(hex: 0x060609), Color(hex: 0x2E0716)]),
            startPoint: CGPoint(x: center.x - R * 0.8, y: center.y - R),
            endPoint: CGPoint(x: center.x + R * 0.5, y: center.y + R)))

        context.drawLayer { inner in
            inner.clip(to: disc)

            // --- Petals: additive translucent lobes, generation-driven deformation ---
            for petal in petals {
                let orbitT = warped * petal.orbitRate + petal.orbitPhase
                let cx = center.x + R * 0.22 * CGFloat(sin(orbitT))
                let cy = center.y + R * 0.22 * CGFloat(sin(orbitT * 1.31 + 1.2))
                let rot = warped * petal.rotRate + petal.orbitPhase

                var path = Path()
                let steps = 64
                let n = Double(petal.harmonics.count)
                for k in 0...steps {
                    let theta = Double(k) / Double(steps) * 2 * .pi
                    var deform = 0.0
                    for h in petal.harmonics {
                        deform += h.amp * sin(h.freq * theta + h.phase)
                    }
                    deform = deform / max(1, n) * amplitude
                    let r = R * (0.52 + 0.32 * CGFloat(deform))
                    // Squash along a drifting axis → elongated organic petal.
                    let px = r * CGFloat(cos(theta))
                    let py = r * CGFloat(sin(theta)) * petal.aspect
                    let x = cx + px * CGFloat(cos(rot)) - py * CGFloat(sin(rot))
                    let y = cy + px * CGFloat(sin(rot)) + py * CGFloat(cos(rot))
                    k == 0 ? path.move(to: CGPoint(x: x, y: y)) : path.addLine(to: CGPoint(x: x, y: y))
                }
                path.closeSubpath()

                inner.drawLayer { layer in
                    layer.blendMode = .plusLighter
                    layer.opacity = 0.44 + 0.24 * amplitude
                    layer.addFilter(.blur(radius: S * 0.02))
                    layer.fill(path, with: .color(petal.color))
                }
            }

            // --- Center flare: hot core + two crossing streaks, brightens with voice ---
            let flare = 0.55 + 0.45 * amplitude
            inner.drawLayer { glow in
                glow.blendMode = .plusLighter
                glow.addFilter(.blur(radius: S * 0.035))
                let r = R * (0.30 + 0.12 * CGFloat(amplitude))
                glow.fill(Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)),
                          with: .radialGradient(
                            Gradient(colors: [.white.opacity(flare), .white.opacity(flare * 0.35), .clear]),
                            center: center, startRadius: 0, endRadius: r))
            }
            for (j, tilt) in [-0.62, 0.55].enumerated() {
                inner.drawLayer { streak in
                    streak.blendMode = .plusLighter
                    streak.addFilter(.blur(radius: S * 0.015))
                    streak.translateBy(x: center.x, y: center.y)
                    streak.rotate(by: .radians(tilt + 0.2 * sin(warped * 0.6 + Double(j) * 2.4)))
                    let len = R * (0.9 + 0.25 * CGFloat(amplitude))
                    let thick = R * 0.05
                    streak.fill(Path(ellipseIn: CGRect(x: -len / 2, y: -thick / 2, width: len, height: thick)),
                                with: .linearGradient(
                                    Gradient(stops: [
                                        .init(color: .clear, location: 0),
                                        .init(color: .white.opacity(flare * 0.9), location: 0.5),
                                        .init(color: .clear, location: 1),
                                    ]),
                                    startPoint: CGPoint(x: -len / 2, y: 0),
                                    endPoint: CGPoint(x: len / 2, y: 0)))
                }
            }

            // --- Glass shading: edge vignette + top sheen ---
            inner.fill(disc, with: .radialGradient(
                Gradient(stops: [
                    .init(color: .clear, location: 0),
                    .init(color: .clear, location: 0.78),
                    .init(color: .black.opacity(0.42), location: 1),
                ]),
                center: center, startRadius: 0, endRadius: R))
            inner.fill(disc, with: .linearGradient(
                Gradient(stops: [
                    .init(color: .white.opacity(0.10), location: 0),
                    .init(color: .clear, location: 0.35),
                ]),
                startPoint: CGPoint(x: center.x, y: center.y - R),
                endPoint: CGPoint(x: center.x, y: center.y + R)))
        }

        // Rim: faint glossy edge.
        context.stroke(disc, with: .linearGradient(
            Gradient(colors: [Color.white.opacity(0.22), Color.white.opacity(0.04), Color(hex: 0xFF2D55).opacity(0.18)]),
            startPoint: CGPoint(x: center.x - R, y: center.y - R),
            endPoint: CGPoint(x: center.x + R, y: center.y + R)), lineWidth: 1)
    }
}
