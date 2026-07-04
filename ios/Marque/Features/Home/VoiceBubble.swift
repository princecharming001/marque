import SwiftUI

// The Home centerpiece — the Siri-style orb you tap to talk to Marque.
struct VoiceBubble: View {
    let onTap: () -> Void
    @State private var taps = 0
    @State private var isPressed = false

    var body: some View {
        Button {
            taps += 1
            onTap()
        } label: {
            VStack(spacing: Space.sm) {
                VoiceOrb(mode: .idle, size: 124)
                    .scaleEffect(isPressed ? 0.96 : 1)
                Text("Tap to talk")
                    .font(Typeface.body(13, .medium))
                    .foregroundStyle(Palette.textTertiary)
            }
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("home.voiceBubble")
        .sensoryFeedback(.impact(weight: .light), trigger: taps)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in isPressed = true }
                .onEnded { _ in isPressed = false }
        )
        .animation(.spring(response: 0.28, dampingFraction: 0.6), value: isPressed)
    }
}
