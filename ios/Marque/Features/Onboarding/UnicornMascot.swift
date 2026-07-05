import SwiftUI

// The Marque mascot — a matte-black clay 3D unicorn (Higgsfield renders in
// Assets.xcassets). Replaces the old code-drawn blue-circle PlaceholderMascot.
// Keeps the appear-bounce + slow breath; no blink (no code-drawn features), and
// never surrounded by floating decoration chips.
struct UnicornMascot: View {
    enum Pose: String {
        case hero = "UnicornHero"
        case thinking = "UnicornThinking"
        case proud = "UnicornProud"
        case celebrate = "UnicornCelebrate"
    }

    let pose: Pose
    var size: CGFloat = 180

    @State private var appeared = false
    @State private var breathing = false

    var body: some View {
        Group {
            if UIImage(named: pose.rawValue) != nil {
                Image(pose.rawValue)
                    .resizable().scaledToFit()
            } else {
                // Pre-asset fallback so builds/screenshots never break: a quiet
                // ink silhouette placeholder (no blue circle, no decorations).
                Image(systemName: "sparkle")
                    .font(.system(size: size * 0.35, weight: .light))
                    .foregroundStyle(Palette.textTertiary)
            }
        }
        .frame(width: size, height: size)
        .scaleEffect(appeared ? (breathing ? 1.03 : 1.0) : 0.7)
        .opacity(appeared ? 1 : 0)
        .shadow(color: Palette.shadowWarm.opacity(0.14), radius: 24, y: 12)
        .onAppear {
            withAnimation(Motion.spring) { appeared = true }
            withAnimation(Motion.breath.delay(0.35)) { breathing = true }
        }
    }
}
