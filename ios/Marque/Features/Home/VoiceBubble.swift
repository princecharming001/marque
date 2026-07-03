import SwiftUI

// The Home centerpiece — the Siri-style orb you tap to talk to Marque.
struct VoiceBubble: View {
    let onTap: () -> Void
    @State private var taps = 0

    var body: some View {
        Button {
            taps += 1
            onTap()
        } label: {
            VoiceOrb(mode: .idle, size: 124)
                .frame(maxWidth: .infinity)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("home.voiceBubble")
        .sensoryFeedback(.impact(weight: .light), trigger: taps)
    }
}
