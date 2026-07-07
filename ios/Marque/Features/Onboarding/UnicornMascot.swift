import SwiftUI

// The Marque mascot — a matte-black clay 3D unicorn (Higgsfield renders in Assets.xcassets).
// Poses that have a real character animation (a looping video where the unicorn actually
// performs an action — the landing "whip & nae nae" dance, the pondering leg-kicks) play
// that clip; the clip's first frame == last frame so it loops invisibly. Remaining poses
// render the static image with only a gentle breath (NO whole-body bobbing/sliding — the
// character should look alive, not like a sticker being dragged around).
struct UnicornMascot: View {
    enum Pose: String {
        case hero = "UnicornHero"
        case thinking = "UnicornThinking"
        case proud = "UnicornProud"
        case celebrate = "UnicornCelebrate"

        /// Looping video clip (bundle .mp4) for poses with a real performed animation.
        var videoResource: String? {
            switch self {
            case .hero: return "unicorn_dance"       // whip & nae nae
            case .thinking: return "unicorn_ponder"  // leg-kicks + thought bubble
            default: return nil
            }
        }
    }

    let pose: Pose
    var size: CGFloat = 180

    @State private var appeared = false
    @State private var breathing = false

    private var videoResource: String? {
        guard let r = pose.videoResource,
              Bundle.main.url(forResource: r, withExtension: "mov") != nil
                || Bundle.main.url(forResource: r, withExtension: "mp4") != nil else { return nil }
        return r
    }

    var body: some View {
        Group {
            if let r = videoResource {
                // Real performed animation — no procedural transform, and NO drop shadow
                // (the clip bakes in its own soft contact shadow; an outer shadow would
                // outline the square video frame and read as a card).
                MascotVideoView(resource: r)
                    .frame(width: size, height: size)
                    .scaleEffect(appeared ? 1 : 0.85)
            } else {
                staticImage
                    .frame(width: size, height: size)
                    // Breath = a tiny scale pulse only. No offset/rotation — the whole
                    // character must never look like it's sliding around as one block.
                    .scaleEffect(appeared ? (breathing ? 1.02 : 1.0) : 0.7)
                    .shadow(color: Palette.shadowWarm.opacity(0.14), radius: 24, y: 12)
            }
        }
        .opacity(appeared ? 1 : 0)
        .onAppear {
            withAnimation(Motion.spring) { appeared = true }
            withAnimation(Motion.breath.delay(0.35)) { breathing = true }
        }
    }

    @ViewBuilder private var staticImage: some View {
        if UIImage(named: pose.rawValue) != nil {
            Image(pose.rawValue).resizable().scaledToFit()
        } else {
            // Pre-asset fallback so builds/screenshots never break.
            Image(systemName: "sparkle")
                .font(.system(size: size * 0.35, weight: .light))
                .foregroundStyle(Palette.textTertiary)
        }
    }
}
