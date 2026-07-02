import SwiftUI

// The Home centerpiece — a breathing glass orb you tap to talk to Marque.
struct VoiceBubble: View {
    let memoryLine: String
    let onTap: () -> Void
    @State private var breathing = false
    @State private var taps = 0

    var body: some View {
        Button {
            taps += 1
            onTap()
        } label: {
            VStack(spacing: Space.lg) {
                ZStack {
                    // Soft accent halo
                    Circle()
                        .fill(Palette.accent.opacity(0.10))
                        .frame(width: 168, height: 168)
                        .scaleEffect(breathing ? 1.06 : 0.96)
                    Circle()
                        .fill(Palette.accent.opacity(0.08))
                        .frame(width: 132, height: 132)
                        .scaleEffect(breathing ? 1.03 : 0.98)
                    // The orb
                    ZStack {
                        Circle().fill(.ultraThinMaterial)
                        Circle().fill(
                            LinearGradient(colors: [Color.white.opacity(0.9), Palette.accent.opacity(0.10)],
                                           startPoint: .topLeading, endPoint: .bottomTrailing))
                        Image(systemName: "waveform")
                            .font(.system(size: 34, weight: .medium))
                            .foregroundStyle(Palette.accent)
                            .symbolEffect(.variableColor.iterative, options: .repeating, isActive: breathing)
                    }
                    .frame(width: 108, height: 108)
                    .overlay(Circle().strokeBorder(Color.white.opacity(0.8), lineWidth: 1))
                    .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 0.5))
                    .shadow(color: Palette.accent.opacity(0.25), radius: 24, x: 0, y: 10)
                    .scaleEffect(breathing ? 1.02 : 0.99)
                }
                VStack(spacing: 4) {
                    Text("Talk to Marque")
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    Text(memoryLine)
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        .lineLimit(2).multilineTextAlignment(.center)
                }
            }
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("home.voiceBubble")
        .sensoryFeedback(.impact(weight: .light), trigger: taps)
        .onAppear {
            withAnimation(.easeInOut(duration: 2.4).repeatForever(autoreverses: true)) {
                breathing = true
            }
        }
    }
}
