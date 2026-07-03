import SwiftUI

// The Home centerpiece — a 3D gradient ball you tap to talk to Marque.
struct VoiceBubble: View {
    let memoryLine: String
    let onTap: () -> Void
    @State private var taps = 0

    var body: some View {
        Button {
            taps += 1
            onTap()
        } label: {
            VStack(spacing: Space.lg) {
                VoiceOrb(mode: .idle, size: 108)
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
    }
}
