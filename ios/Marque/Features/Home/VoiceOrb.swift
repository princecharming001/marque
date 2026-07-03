import SwiftUI
import UIKit

// Shared animated voice visual — a Siri-style "liquid light" orb: a glossy glass bezel
// around a disc where a few soft, tapered ribbons of light slowly writhe and cross,
// building a bright highlight where they overlap. Driven by TimelineView(.animation) so
// it's a continuous Canvas draw rather than a handful of animated shape modifiers — alive
// at rest, and volume-reactive: `level` (0...1, live mic RMS or TTS metering) widens the
// ribbons, speeds their drift, and brightens the center highlight in real time.
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

    /// Fixed rainbow ribbon palette (matches the reference regardless of mode); only the
    /// disc's base tint shifts per mode, kept light per the brief rather than the
    /// reference's near-black glass.
    private static let ribbons: [Color] = [
        Color(hex: 0xFF3D8F),   // magenta
        Color(hex: 0x4FD8FF),   // saturated cyan — reads clearly before fading to its white peak
        Color(hex: 0x2FE0B0),   // teal-green
        Color(hex: 0x5B7CFF),   // soft blue wash
    ]

    private var baseTint: (Color, Color) {
        switch mode {
        case .idle:      return (Color(hex: 0xF2F0FF), Color(hex: 0xE3ECFF))
        case .listening: return (Color(hex: 0xEAFBFF), Color(hex: 0xDCF3FF))
        case .thinking:  return (Color(hex: 0xF3ECFF), Color(hex: 0xE6DCFF))
        case .speaking:  return (Color(hex: 0xFFEEF7), Color(hex: 0xFFE0F0))
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
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    private func draw(_ context: inout GraphicsContext, canvasSize: CGSize, time: Double, level: Double) {
        let center = CGPoint(x: canvasSize.width / 2, y: canvasSize.height / 2)
        let outerRadius = size / 2
        let bezelWidth = size * 0.045
        let innerRadius = outerRadius - bezelWidth

        drawBezel(&context, center: center, outerRadius: outerRadius, bezelWidth: bezelWidth)

        context.drawLayer { disc in
            disc.clip(to: Path(ellipseIn: rect(center: center, radius: innerRadius)))

            let (baseA, baseB) = baseTint
            disc.fill(Path(ellipseIn: rect(center: center, radius: innerRadius)),
                      with: .radialGradient(Gradient(colors: [baseA, baseB]),
                                             center: center, startRadius: 0, endRadius: innerRadius * 1.1))

            // Idle drifts slowly; listening/speaking spin up with volume; thinking holds a
            // steady, level-independent drift so it reads as "working," not "reacting."
            let baseSpeed = mode == .thinking ? 0.22 : 0.1
            let speed = baseSpeed + (mode == .thinking ? 0 : level * 0.4)
            let rotation = time * speed

            // Angle offsets are spread mod π (a ribbon's orientation repeats every 180°),
            // not mod 2π — evenly spacing them by π/4 is what keeps all four visually
            // distinct instead of two pairs landing on the same line.
            let ribbonSpecs: [(angleOffset: Double, spin: Double, lengthMul: CGFloat, widthMul: CGFloat, bow: CGFloat)] = [
                (0.0, 1.0, 2.3, 0.16, 0.46),
                (.pi / 4, -0.7, 2.4, 0.12, -0.6),
                (.pi / 2, 0.55, 2.2, 0.14, 0.34),
                (3 * .pi / 4, -0.4, 2.35, 0.17, -0.4),
            ]

            for (i, spec) in ribbonSpecs.enumerated() {
                let color = Self.ribbons[i % Self.ribbons.count]
                let angle = spec.angleOffset + rotation * spec.spin
                    + 0.15 * sin(time * 0.4 + Double(i) * 1.7)   // slow organic writhe
                let length = innerRadius * spec.lengthMul
                let width = innerRadius * spec.widthMul * (1 + CGFloat(level) * 0.5)
                let bow = length * spec.bow

                disc.drawLayer { layer in
                    // Plain alpha compositing (not .screen) — screen blending washes
                    // color out fast against a light base; normal keeps the ribbons vivid.
                    layer.addFilter(.blur(radius: size * 0.03))
                    layer.translateBy(x: center.x, y: center.y)
                    layer.rotate(by: .radians(angle))

                    let half = length / 2
                    // A curved, tapered "brush stroke": both edges share the same tip
                    // points (so width -> 0 at the ends) but the second edge is the first
                    // shifted by `width` in y, so the ribbon bows as a single unit along
                    // its curved spine instead of reading as a symmetric flat eye shape.
                    var path = Path()
                    path.move(to: CGPoint(x: -half, y: 0))
                    path.addCurve(to: CGPoint(x: half, y: 0),
                                  control1: CGPoint(x: -half * 0.4, y: -bow),
                                  control2: CGPoint(x: half * 0.4, y: -bow * 0.4))
                    path.addCurve(to: CGPoint(x: -half, y: 0),
                                  control1: CGPoint(x: half * 0.4, y: -bow * 0.4 + width),
                                  control2: CGPoint(x: -half * 0.4, y: -bow + width))
                    path.closeSubpath()

                    layer.fill(path, with: .linearGradient(
                        Gradient(stops: [
                            .init(color: color.opacity(0), location: 0),
                            .init(color: color.opacity(0.95), location: 0.3),
                            .init(color: .white.opacity(0.95), location: 0.5),
                            .init(color: color.opacity(0.95), location: 0.7),
                            .init(color: color.opacity(0), location: 1),
                        ]),
                        startPoint: CGPoint(x: -half, y: 0), endPoint: CGPoint(x: half, y: 0)))
                }
            }

            // Bright crossing highlight — guarantees the crisp hot-spot the reference
            // shows at the disc's center, rather than leaving it to blend-mode chance.
            // Plain alpha (not .screen, which flattens straight to white on a light base).
            disc.drawLayer { glow in
                glow.addFilter(.blur(radius: size * 0.03))
                let r = innerRadius * (0.22 + CGFloat(level) * 0.1)
                glow.fill(Path(ellipseIn: rect(center: center, radius: r)),
                          with: .radialGradient(Gradient(colors: [.white, .white.opacity(0.7), .clear]),
                                                 center: center, startRadius: 0, endRadius: r))
            }
        }
    }

    private func rect(center: CGPoint, radius: CGFloat) -> CGRect {
        CGRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)
    }

    /// Glossy glass rim: a light ring with a top-left highlight and bottom-right shade,
    /// plus a soft drop shadow, matching the reference's lens-like bevel.
    private func drawBezel(_ context: inout GraphicsContext, center: CGPoint, outerRadius: CGFloat, bezelWidth: CGFloat) {
        context.drawLayer { shadow in
            shadow.addFilter(.shadow(color: .black.opacity(0.18), radius: size * 0.06, x: 0, y: size * 0.025))
            shadow.fill(Path(ellipseIn: rect(center: center, radius: outerRadius)), with: .color(.white))
        }
        let ring = Path(ellipseIn: rect(center: center, radius: outerRadius))
            .subtracting(Path(ellipseIn: rect(center: center, radius: outerRadius - bezelWidth)))
        context.fill(ring, with: .linearGradient(
            Gradient(colors: [.white, Color(hex: 0xD8DCE6), .white]),
            startPoint: CGPoint(x: center.x - outerRadius, y: center.y - outerRadius),
            endPoint: CGPoint(x: center.x + outerRadius, y: center.y + outerRadius)))
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
