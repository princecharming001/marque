import SwiftUI

// The Home centerpiece — the Yunicorn voice orb you tap to talk to Yuni. Drawn as the
// screen's one hero card (DESIGN.md §5): a near-black card that stays dark in both
// schemes, the orb as a white drop centered on it, the caption in onNightSecondary.
// The whole card is the tap target.
struct VoiceBubble: View {
    let onTap: () -> Void
    @State private var taps = 0
    @State private var isPressed = false

    var body: some View {
        Button {
            taps += 1
            onTap()
        } label: {
            DSHeroCard(padding: Space.xl) {
                VStack(spacing: Space.md) {
                    VoiceOrb(mode: .idle, size: 128, onDark: true)
                        .scaleEffect(isPressed ? 0.96 : 1)
                    Text("Tap to talk")
                        .font(AppFont.supporting)
                        .foregroundStyle(Palette.onNightSecondary)
                }
                .padding(.vertical, Space.md)
                .frame(maxWidth: .infinity)
            }
            .contentShape(RoundedRectangle(cornerRadius: Radius.hero, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("home.voiceBubble")
        .sensoryFeedback(.impact(weight: .light), trigger: taps)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in isPressed = true }
                .onEnded { _ in isPressed = false }
        )
        .animation(Motion.quick, value: isPressed)
    }
}
