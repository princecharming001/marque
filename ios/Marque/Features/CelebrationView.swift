import SwiftUI

// Quiet, earned celebration after a recording session — measures showing up, not vanity views.
struct CelebrationView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: Space.lg) {
            Spacer()
            Image("FlameIcon").resizable().scaledToFit().frame(width: 96, height: 96)
            Text("That's a wrap").font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
            Text("You showed up. That's \(store.streak) \(store.streak == 1 ? "session" : "sessions") in.")
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
            Spacer()
            PrimaryButton(title: "Keep going") { dismiss() }
                .accessibilityIdentifier("celebration.dismiss")
        }
        .screenPadding().padding(.vertical, Space.xxl)
        .background(Palette.surface)
        .presentationDetents([.medium])
    }
}
