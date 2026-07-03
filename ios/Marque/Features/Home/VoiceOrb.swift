import SwiftUI
import UIKit

// Shared animated voice visual — a particle bloom: concentric rings of dots, each ring
// phase-shifted from the last, which reads as spiral arms winding out of a glowing core
// (classic spirograph/phyllotaxis construction). Replaces the old glossy-sphere orb
// everywhere (Home hero, voice session sheet). Driven by TimelineView(.animation) so it's
// a continuous, GPU-friendly particle draw rather than a handful of animated shape
// modifiers — genuinely alive at rest, and volume-reactive: `level` (0...1, live mic RMS
// or TTS metering) widens the spiral's ripple and brightens the core in real time.
struct VoiceOrb: View {
    enum Mode { case idle, listening, thinking, speaking }
    var mode: Mode = .idle
    var level: Double = 0
    var size: CGFloat = 108

    // Non-observed mutable smoothing so per-frame reactivity feels analog rather than
    // snapping to each ~20Hz metering tick. Mutating this inside Canvas's draw closure
    // is safe — it's a plain reference type, not @State, so it never triggers a re-render;
    // TimelineView's own clock is what drives the next frame.
    @State private var smoother = LevelSmoother()

    private var clampedLevel: Double { min(1, max(0, level)) }

    /// [core glow tint, inner petal color, outer petal color] per mode.
    private var palette: (core: Color, inner: Color, outer: Color) {
        switch mode {
        case .idle:      return (.white, Color(hex: 0x8FD7FF), Color(hex: 0x4B3FE0))
        case .listening: return (.white, Color(hex: 0x6FE0FF), Color(hex: 0x7A5CFF))
        case .thinking:  return (.white, Color(hex: 0xC59CFF), Color(hex: 0x4B3FE0))
        case .speaking:  return (.white, Color(hex: 0xFF9AD8), Color(hex: 0x7A5CFF))
        }
    }

    var body: some View {
        TimelineView(.animation) { timeline in
            Canvas { context, canvasSize in
                let now = timeline.date.timeIntervalSinceReferenceDate
                let smoothed = smoother.update(target: clampedLevel, at: now)
                draw(&context, canvasSize: canvasSize, time: now, level: smoothed)
            }
        }
        .frame(width: size * 1.7, height: size * 1.7)
        .accessibilityHidden(true)
    }

    private func draw(_ context: inout GraphicsContext, canvasSize: CGSize, time: Double, level: Double) {
        let center = CGPoint(x: canvasSize.width / 2, y: canvasSize.height / 2)
        let tint = palette
        let maxRadius = size * 0.5 * (1 + CGFloat(level) * 0.14)

        // Idle drifts slowly; listening/speaking spin up with volume; thinking holds a
        // steady, level-independent turn so it reads as "working," not "reacting."
        let baseSpeed = mode == .thinking ? 0.34 : 0.16
        let speed = baseSpeed + (mode == .thinking ? 0 : level * 0.55)
        let rotation = time * speed

        // Ambient seating glow — soft, low-opacity, keeps the particles from floating
        // on nothing against the app's flat background.
        context.drawLayer { layer in
            layer.addFilter(.blur(radius: size * 0.22))
            let r = size * 0.34 * (1 + CGFloat(level) * 0.2)
            let rect = CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)
            layer.fill(Path(ellipseIn: rect),
                       with: .radialGradient(Gradient(colors: [tint.outer.opacity(0.5), .clear]),
                                              center: center, startRadius: 0, endRadius: r))
        }

        // The spiral rosette: each concentric ring is sine-modulated in radius and
        // phase-shifted further than the last — the progressive phase offset is what
        // braids straight rings into curved arms.
        let ringCount = 20
        let dotsPerRing = 26
        let petalCount = 9.0
        let spiralTwist = 3.1 * (.pi * 2)

        for ring in 0..<ringCount {
            let ringT = Double(ring) / Double(ringCount - 1)
            let ringRadius = maxRadius * CGFloat(ringT)
            let phaseShift = ringT * spiralTwist + rotation
            let waveAmp = maxRadius * 0.1 * (1 - ringT * 0.35) * (1 + CGFloat(level) * 0.7)
            let color = tint.inner.mix(with: tint.outer, amount: ringT)

            for dot in 0..<dotsPerRing {
                let dotT = Double(dot) / Double(dotsPerRing)
                let angle = dotT * (.pi * 2) + phaseShift
                let r = ringRadius + waveAmp * CGFloat(sin(petalCount * angle))
                guard r > 0 else { continue }

                // Deterministic per-dot phase (index-derived, not per-frame random) for a
                // gentle twinkle that never pops.
                let twinkleSeed = Double(ring * dotsPerRing + dot) * 2.399963
                let twinkle = 0.78 + 0.22 * sin(time * 1.4 + twinkleSeed)

                let x = center.x + r * CGFloat(cos(Double(angle)))
                let y = center.y + r * CGFloat(sin(Double(angle)))
                let dotSize = max(0.9, (3.1 * (1 - ringT) + 0.7)) * (1 + CGFloat(level) * 0.25)
                let opacity = (0.18 + 0.82 * pow(1 - ringT, 2)) * twinkle

                let rect = CGRect(x: x - dotSize / 2, y: y - dotSize / 2, width: dotSize, height: dotSize)
                context.fill(Path(ellipseIn: rect), with: .color(color.opacity(opacity)))
            }
        }

        // Bright core — the "light source" the arms spiral out of.
        context.drawLayer { layer in
            layer.addFilter(.blur(radius: size * 0.05))
            let r = size * (0.1 + CGFloat(level) * 0.03)
            let rect = CGRect(x: center.x - r, y: center.y - r, width: r * 2, height: r * 2)
            layer.fill(Path(ellipseIn: rect),
                       with: .radialGradient(Gradient(colors: [tint.core, tint.core.opacity(0.6), .clear]),
                                              center: center, startRadius: 0, endRadius: r))
        }
    }
}

private final class LevelSmoother {
    private var value: Double = 0
    private var lastTime: Double?

    func update(target: Double, at time: Double) -> Double {
        let dt = lastTime.map { min(0.1, time - $0) } ?? 1.0 / 60.0
        lastTime = time
        let rate = 8.0
        value += (target - value) * min(1, rate * dt)
        return value
    }
}

private extension Color {
    /// Manual RGB lerp — Canvas draws from raw Color values every frame, so this needs
    /// to be cheap and not depend on SwiftUI's view-level animation system.
    func mix(with other: Color, amount: Double) -> Color {
        let t = min(1, max(0, amount))
        let a = UIColor(self).cgColor.components ?? [0, 0, 0, 1]
        let b = UIColor(other).cgColor.components ?? [0, 0, 0, 1]
        func lerp(_ i: Int) -> Double {
            let av = a.count > i ? a[i] : a[0]
            let bv = b.count > i ? b[i] : b[0]
            return av + (bv - av) * t
        }
        if a.count >= 4 && b.count >= 4 {
            return Color(red: lerp(0), green: lerp(1), blue: lerp(2), opacity: lerp(3))
        }
        return Color(red: lerp(0), green: lerp(0), blue: lerp(0))
    }
}
