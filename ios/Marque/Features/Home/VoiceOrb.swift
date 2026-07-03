import SwiftUI

// Shared animated "3D gradient ball" voice visual — replaces the old flat glass-circle
// waveform badge everywhere (Home hero, voice session sheet). A glossy sphere built from
// layered gradients (radial body + rotating iridescent shimmer + specular highlight +
// rim shade), with a slow independent breathing/shimmer loop so it's never static, PLUS
// real-time reactivity: `level` (0...1, live mic RMS or TTS metering) drives scale and
// glow intensity via a snappy spring so the ball visibly pulses with actual volume.
struct VoiceOrb: View {
    enum Mode { case idle, listening, thinking, speaking }
    var mode: Mode = .idle
    var level: Double = 0
    var size: CGFloat = 108

    @State private var shimmerAngle: Double = 0
    @State private var breathing = false

    private var palette: [Color] {
        switch mode {
        case .idle:      return [Color(hex: 0x9AA6FF), Palette.accent, Color(hex: 0x4B3FE0)]
        case .listening: return [Color(hex: 0x6FE0FF), Palette.accent, Color(hex: 0x7A5CFF)]
        case .thinking:  return [Color(hex: 0xC59CFF), Palette.accent, Color(hex: 0x4B3FE0)]
        case .speaking:  return [Color(hex: 0xFF9AD8), Palette.accent, Color(hex: 0x4B3FE0)]
        }
    }

    private var clampedLevel: Double { min(1, max(0, level)) }
    private var liveScale: CGFloat { 1 + CGFloat(clampedLevel) * 0.22 }
    private var glowOpacity: Double { 0.14 + clampedLevel * 0.34 }

    var body: some View {
        ZStack {
            // Outer reactive glow — brightens and swells with live level, breathes at rest
            Circle()
                .fill(palette[1].opacity(glowOpacity))
                .frame(width: size * 1.75, height: size * 1.75)
                .scaleEffect((breathing ? 1.05 : 0.95) * liveScale)
                .blur(radius: size * 0.2)

            Circle()
                .fill(palette[1].opacity(glowOpacity * 0.65))
                .frame(width: size * 1.32, height: size * 1.32)
                .scaleEffect(liveScale)
                .blur(radius: size * 0.06)

            // The sphere
            ZStack {
                Circle()
                    .fill(RadialGradient(colors: palette,
                                         center: UnitPoint(x: 0.32, y: 0.28),
                                         startRadius: 0, endRadius: size * 0.62))
                Circle()
                    .fill(AngularGradient(colors: [.clear, .white.opacity(0.4), .clear,
                                                   .white.opacity(0.18), .clear],
                                         center: .center, angle: .degrees(shimmerAngle)))
                    .blendMode(.plusLighter)
                Ellipse()
                    .fill(Color.white.opacity(0.55))
                    .frame(width: size * 0.34, height: size * 0.2)
                    .rotationEffect(.degrees(-28))
                    .offset(x: -size * 0.18, y: -size * 0.26)
                    .blur(radius: size * 0.035)
                Circle()
                    .fill(RadialGradient(colors: [.clear, .black.opacity(0.18)],
                                         center: UnitPoint(x: 0.72, y: 0.8),
                                         startRadius: size * 0.1, endRadius: size * 0.6))
            }
            .frame(width: size, height: size)
            .clipShape(Circle())
            .overlay(Circle().strokeBorder(.white.opacity(0.4), lineWidth: 1))
            .shadow(color: palette[1].opacity(0.4), radius: size * 0.22, x: 0, y: size * 0.08)
            .scaleEffect(liveScale)

            if mode == .thinking {
                Image(systemName: "ellipsis")
                    .font(.system(size: size * 0.24, weight: .bold))
                    .foregroundStyle(.white)
                    .symbolEffect(.variableColor.iterative, options: .repeating)
            }
        }
        .animation(.interpolatingSpring(stiffness: 220, damping: 15), value: clampedLevel)
        .animation(Motion.quick, value: mode)
        .onAppear {
            withAnimation(.easeInOut(duration: 2.6).repeatForever(autoreverses: true)) {
                breathing = true
            }
            withAnimation(.linear(duration: 7).repeatForever(autoreverses: false)) {
                shimmerAngle = 360
            }
        }
        .accessibilityHidden(true)
    }
}
