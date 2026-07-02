import SwiftUI

// Chat tab — Phase 5 replaces this stub with the full maxapp-design port
// (bubbles, morph composer, typewriter streaming, drawer, video-link analysis).
struct ChatView: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        VStack(spacing: Space.lg) {
            Spacer()
            Text("What can I help with?")
                .font(AppFont.title).foregroundStyle(Palette.textPrimary)
            Text("The full chat lands in the next build — for now, talk to Marque from the Home bubble.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, Space.xl)
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .background(Palette.surface.ignoresSafeArea())
        .navigationTitle("Marque")
        .navigationBarTitleDisplayMode(.inline)
    }
}
