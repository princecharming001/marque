import SwiftUI

// Shared animated voice visual — an iridescent plasma sphere rendered by a Metal
// fragment shader (VoiceOrb.metal): domain-warped noise flowing through a cosine
// palette, shaded as a glass ball and contained in an anti-aliased circle. Pure GPU,
// no third-party dependency. `mode` shifts the palette phase and energy, `level`
// (0...1, live mic RMS or TTS metering) warps time (the plasma churns faster),
// swells the hot core, and drives the outer halo; `size` is the rendered diameter.
struct VoiceOrb: View {
    enum Mode { case idle, listening, thinking, speaking }
    var mode: Mode = .idle
    var level: Double = 0
    var size: CGFloat = 108

    // Reference-type clock mutated during frame evaluation (safe: not @State-observed;
    // TimelineView's tick drives frames). Smooths ~20Hz metering into an analog level
    // and integrates a volume-warped time so churn speed follows the voice without
    // phase jumps.
    @State private var clock = MotionClock()

    private var clampedLevel: Double { min(1, max(0, level)) }

    /// Palette phase per mode (offsets the cosine palette: blues → cyans → violets → pinks).
    private var phase: Double {
        switch mode {
        case .idle:      return 0.0
        case .listening: return 0.12
        case .thinking:  return 0.3
        case .speaking:  return 0.52
        }
    }

    private var halo: Color {
        switch mode {
        case .idle:      return Color(hex: 0x7A8CFF)
        case .listening: return Color(hex: 0x38D1FF)
        case .thinking:  return Color(hex: 0xB878FF)
        case .speaking:  return Color(hex: 0xFF4D9E)
        }
    }

    var body: some View {
        TimelineView(.animation) { timeline in
            let now = timeline.date.timeIntervalSinceReferenceDate
            // Thinking holds a brisk, level-independent churn ("working", not
            // "reacting"); idle simmers; listening/speaking surge with volume.
            let baseRate = mode == .thinking ? 1.5 : 0.7
            let levelGain = mode == .thinking ? 0 : 2.2
            let (lvl, warped) = clock.update(target: clampedLevel, at: now,
                                             baseRate: baseRate, levelGain: levelGain)

            ZStack {
                // Volume-reactive halo seats the sphere on the flat canvas.
                Circle()
                    .fill(halo.opacity(0.16 + lvl * 0.3))
                    .frame(width: size * 1.28, height: size * 1.28)
                    .scaleEffect(1 + CGFloat(lvl) * 0.22)
                    .blur(radius: size * 0.11)

                Rectangle()
                    .aspectRatio(1, contentMode: .fit)
                    .frame(width: size, height: size)
                    .colorEffect(ShaderLibrary.voiceOrb(
                        .float2(Float(size), Float(size)),
                        .float(Float(warped)),
                        .float(Float(lvl)),
                        .float(Float(phase))
                    ))
                    .clipShape(Circle())
                    .shadow(color: halo.opacity(0.35), radius: size * 0.16, x: 0, y: size * 0.05)
                    .scaleEffect(1 + CGFloat(lvl) * 0.08)
            }
        }
        .animation(Motion.quick, value: mode)
        .accessibilityHidden(true)
    }
}

/// Smooths raw metering into an analog level and integrates a volume-warped clock, so
/// churn speed follows the voice without the phase jumps a naive `time * speed` causes.
private final class MotionClock {
    private var level: Double = 0
    private var warped: Double = 0
    private var lastTime: Double?

    func update(target: Double, at time: Double, baseRate: Double, levelGain: Double) -> (Double, Double) {
        let dt = lastTime.map { min(0.1, max(0, time - $0)) } ?? 1.0 / 60.0
        lastTime = time
        level += (target - level) * min(1, 8.0 * dt)
        warped += dt * (baseRate + level * levelGain)
        return (level, warped)
    }
}
