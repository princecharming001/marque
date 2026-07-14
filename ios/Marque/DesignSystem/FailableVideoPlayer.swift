import SwiftUI
import AVKit

// UX-A3: extracted from ReelDetailSheet (was private there) so the mimic cards can
// autoplay too. An AVPlayerViewController that autoplays, loops like the platforms do,
// and reports a hard failure so callers can fall back (thumbnail → text) instead of
// showing a dead black box.
struct FailableVideoPlayer: UIViewControllerRepresentable {
    let url: URL
    var muted: Bool = false
    var showsControls: Bool = true
    let onFailure: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onFailure: onFailure) }

    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let item = AVPlayerItem(url: url)
        let player = AVPlayer(playerItem: item)
        player.isMuted = muted
        context.coordinator.observe(item, player: player)
        let vc = AVPlayerViewController()
        vc.player = player
        vc.showsPlaybackControls = showsControls
        vc.videoGravity = .resizeAspectFill      // fill the 9:16 frame, no letterbox bars
        player.play()                            // autoplay — no tap-to-start
        return vc
    }
    func updateUIViewController(_ vc: AVPlayerViewController, context: Context) {}

    final class Coordinator: NSObject {
        let onFailure: () -> Void
        private var obs: NSKeyValueObservation?
        private var sizeObs: NSKeyValueObservation?
        private var loop: NSObjectProtocol?
        private weak var player: AVPlayer?
        init(onFailure: @escaping () -> Void) { self.onFailure = onFailure }
        func observe(_ item: AVPlayerItem, player: AVPlayer) {
            self.player = player
            obs = item.observe(\.status) { [weak self] it, _ in
                if it.status == .failed { DispatchQueue.main.async { self?.onFailure() } }
            }
            // Junk-stream guard: a scraped CDN sometimes 200s a degenerate few-pixel
            // video that "plays" fine — .resizeAspectFill then paints it as a
            // full-screen smear. Real reels are ≥540p; anything under 140px on its
            // short side is garbage, so treat it as a failure and let the caller
            // fall back (thumbnail → hook panel), same posture as a hard load error.
            sizeObs = item.observe(\.presentationSize) { [weak self] it, _ in
                let s = it.presentationSize
                guard s != .zero else { return }        // unknown yet (or audio-only)
                if min(s.width, s.height) < 140 {
                    DispatchQueue.main.async { self?.onFailure() }
                }
            }
            // Loop like the platforms do — a reel that ends and sits on a black
            // frame reads as broken.
            loop = NotificationCenter.default.addObserver(
                forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main) { [weak self] _ in
                    self?.player?.seek(to: .zero)
                    self?.player?.play()
                }
        }
        deinit { if let loop { NotificationCenter.default.removeObserver(loop) } }
    }
}
