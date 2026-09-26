import SwiftUI

// The Yunicorn mascot is the app-icon mark (owner 2026-09-26, icon option "B"): one chunky
// blob with a horn and a closed happy eye. It is a template image tinted textPrimary, so it
// is black on the light canvas and white on the dark one, with the eye knocked out to the
// canvas either way. Poses add one small accent (thought dots, sparkles) and the mark only
// ever breathes (a tiny scale pulse), never bobs or slides.
struct UnicornMascot: View {
    enum Pose { case hero, thinking, proud, celebrate }

    let pose: Pose
    var size: CGFloat = 180

    @State private var appeared = false
    @State private var breathing = false
    @State private var beat = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            YunicornMarkView(size: size * 0.72)
                .scaleEffect(appeared ? (breathing ? 1.02 : 1.0) : 0.7)
            accent
        }
        .frame(width: size, height: size)
        .opacity(appeared ? 1 : 0)
        .accessibilityHidden(true)
        .onAppear {
            withAnimation(Motion.standard) { appeared = true }
            guard !reduceMotion else { return }
            withAnimation(Motion.breath.delay(0.35)) { breathing = true }
            withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true).delay(0.2)) { beat = true }
        }
    }

    @ViewBuilder private var accent: some View {
        switch pose {
        case .thinking:
            // Three thought dots off the face side, pulsing in turn.
            HStack(spacing: size * 0.035) {
                ForEach(0..<3, id: \.self) { i in
                    Circle().fill(Palette.textPrimary)
                        .frame(width: size * 0.06, height: size * 0.06)
                        .opacity(reduceMotion ? 0.8 : (beat ? (i == 1 ? 0.35 : 1) : (i == 1 ? 1 : 0.35)))
                }
            }
            .offset(x: size * 0.36, y: -size * 0.34)
        case .celebrate:
            ZStack {
                sparkle(size * 0.14).offset(x: size * 0.38, y: -size * 0.36)
                sparkle(size * 0.09).offset(x: -size * 0.40, y: -size * 0.20)
                sparkle(size * 0.07).offset(x: size * 0.44, y: size * 0.02)
            }
            .opacity(reduceMotion ? 1 : (beat ? 1 : 0.45))
        case .hero, .proud:
            EmptyView()
        }
    }

    private func sparkle(_ s: CGFloat) -> some View {
        Image(systemName: "sparkle")
            .font(.system(size: s, weight: .bold))
            .foregroundStyle(Palette.textPrimary)
    }
}

/// The app-icon mark on its own: black on light, white on dark (template image).
struct YunicornMarkView: View {
    var size: CGFloat = 48
    var body: some View {
        Image("YunicornMark")
            .renderingMode(.template)
            .resizable()
            .scaledToFit()
            .foregroundStyle(Palette.textPrimary)
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}
