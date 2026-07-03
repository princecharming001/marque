import SwiftUI
import Orb

// Shared animated voice visual — wraps metasidd/Orb (MIT), a purpose-built Siri-style
// orb (layered wavy blobs + particles + core glow) rather than a hand-rolled Canvas
// approximation, so the fluid motion matches the real thing. This wrapper keeps the
// app-side contract stable: `mode` picks the palette/energy, `level` (0...1, live mic
// RMS or TTS metering) adds volume reactivity by scaling the orb and swelling an outer
// glow around it, and `size` is the rendered diameter.
struct VoiceOrb: View {
    enum Mode { case idle, listening, thinking, speaking }
    var mode: Mode = .idle
    var level: Double = 0
    var size: CGFloat = 108

    private var clampedLevel: Double { min(1, max(0, level)) }

    // One configuration per mode. Stored statically so the OrbView identity is stable
    // within a mode (its internal animations keep running); switching modes intentionally
    // rebuilds it with the new palette.
    private static let configs: [Mode: OrbConfiguration] = [
        .idle: OrbConfiguration(
            backgroundColors: [Color(hex: 0x9AA6FF), Color(hex: 0x74E6FF), Color(hex: 0xE4B3FF)],
            glowColor: .white, coreGlowIntensity: 0.9, speed: 45),
        .listening: OrbConfiguration(
            backgroundColors: [Color(hex: 0x38D1FF), Color(hex: 0x5A6CFF), Color(hex: 0x27E3A9)],
            glowColor: .white, coreGlowIntensity: 1.2, speed: 75),
        .thinking: OrbConfiguration(
            backgroundColors: [Color(hex: 0xB878FF), Color(hex: 0x5A6CFF), Color(hex: 0xFF2D78)],
            glowColor: .white, coreGlowIntensity: 1.0, speed: 60),
        .speaking: OrbConfiguration(
            backgroundColors: [Color(hex: 0xFF2D78), Color(hex: 0x38D1FF), Color(hex: 0xB878FF)],
            glowColor: .white, coreGlowIntensity: 1.3, speed: 80),
    ]

    private var accent: Color {
        switch mode {
        case .idle:      return Color(hex: 0x9AA6FF)
        case .listening: return Color(hex: 0x38D1FF)
        case .thinking:  return Color(hex: 0xB878FF)
        case .speaking:  return Color(hex: 0xFF2D78)
        }
    }

    var body: some View {
        ZStack {
            // Volume-reactive halo behind the orb — the orb itself animates at its own
            // pace; the halo swelling/brightening is what tracks the live voice level.
            Circle()
                .fill(accent.opacity(0.12 + clampedLevel * 0.3))
                .frame(width: size * 1.3, height: size * 1.3)
                .scaleEffect(1 + CGFloat(clampedLevel) * 0.25)
                .blur(radius: size * 0.12)

            OrbView(configuration: Self.configs[mode] ?? OrbConfiguration())
                .frame(width: size, height: size)
                .scaleEffect(1 + CGFloat(clampedLevel) * 0.12)
                .id(mode)   // stable identity per mode; rebuild only on mode change
        }
        .animation(.interpolatingSpring(stiffness: 200, damping: 16), value: clampedLevel)
        .animation(Motion.quick, value: mode)
        .accessibilityHidden(true)
    }
}
