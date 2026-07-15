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
    /// Reports the video's real width/height aspect once known, so the caller can size
    /// its frame to the FOOTAGE instead of assuming 9:16 — a landscape or square reel
    /// aspect-filled into a portrait frame was cropped ~4x to a zoomed blob ("reels
    /// should be displayed properly" report). Optional: existing call sites keep the
    /// fixed-frame behavior.
    var onAspect: ((CGFloat) -> Void)? = nil

    func makeCoordinator() -> Coordinator { Coordinator(onFailure: onFailure, onAspect: onAspect) }

    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let item = AVPlayerItem(url: url)
        let player = AVPlayer(playerItem: item)
        player.isMuted = muted
        let vc = AVPlayerViewController()
        vc.player = player
        vc.showsPlaybackControls = showsControls
        // Fill by default (no letterbox); when the caller adopts onAspect the container
        // matches the footage, so fill and fit coincide. The coordinator flips to
        // .resizeAspect for non-portrait footage as a belt-and-suspenders against crop
        // on callers that keep a fixed portrait frame.
        vc.videoGravity = .resizeAspectFill
        context.coordinator.observe(item, player: player, vc: vc)
        player.play()                            // autoplay — no tap-to-start
        return vc
    }
    func updateUIViewController(_ vc: AVPlayerViewController, context: Context) {}

    // Stop playback when the player leaves the hierarchy (sheet/preview dismissed) so a
    // reel keeps neither playing nor looping audio behind a closed popup.
    static func dismantleUIViewController(_ vc: AVPlayerViewController, coordinator: Coordinator) {
        vc.player?.pause()
        vc.player = nil
    }

    final class Coordinator: NSObject {
        let onFailure: () -> Void
        let onAspect: ((CGFloat) -> Void)?
        private var obs: NSKeyValueObservation?
        private var sizeObs: NSKeyValueObservation?
        private var loop: NSObjectProtocol?
        private weak var player: AVPlayer?
        init(onFailure: @escaping () -> Void, onAspect: ((CGFloat) -> Void)?) {
            self.onFailure = onFailure
            self.onAspect = onAspect
        }
        func observe(_ item: AVPlayerItem, player: AVPlayer, vc: AVPlayerViewController) {
            self.player = player
            obs = item.observe(\.status) { [weak self] it, _ in
                if it.status == .failed { DispatchQueue.main.async { self?.onFailure() } }
            }
            // One observer, three jobs once the real size is known:
            // 1. Junk-stream guard: a scraped CDN sometimes 200s a degenerate few-pixel
            //    video that "plays" fine — aspect-fill then paints it as a full-screen
            //    smear. Real reels are ≥540p; under 140px on the short side is garbage →
            //    treat as failure (thumbnail → hook panel fallback).
            // 2. Report the aspect so the container can match the footage.
            // 3. Non-portrait footage in a portrait frame must letterbox, never crop.
            sizeObs = item.observe(\.presentationSize) { [weak self, weak vc] it, _ in
                let s = it.presentationSize
                guard s != .zero else { return }        // unknown yet (or audio-only)
                DispatchQueue.main.async {
                    guard let self else { return }
                    if min(s.width, s.height) < 140 {
                        self.onFailure()
                        return
                    }
                    let aspect = s.width / s.height
                    self.onAspect?(aspect)
                    if aspect > 0.75 {                  // square/landscape: fit, don't crop
                        vc?.videoGravity = .resizeAspect
                    }
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
