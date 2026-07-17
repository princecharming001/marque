import SwiftUI
import AVFoundation

// Chrome-free full-bleed player surface (AVKit's VideoPlayer forces default transport chrome;
// the editor draws its own transport synced to the timeline playhead).
struct PlayerLayerView: UIViewRepresentable {
    let player: AVPlayer
    // v6 canvas-fidelity: the render composition COVERS the 1080×1920 frame
    // (OffthreadVideo object-fit cover), so the editor canvas must fill too —
    // letterboxing (.resizeAspect) put every overlay at the wrong on-video size.
    // Fullscreen raw preview still passes .resizeAspect.
    var gravity: AVLayerVideoGravity = .resizeAspectFill

    func makeUIView(context: Context) -> PlayerContainer {
        let v = PlayerContainer()
        v.playerLayer.player = player
        v.playerLayer.videoGravity = gravity
        v.backgroundColor = .black
        v.clipsToBounds = true
        return v
    }
    func updateUIView(_ uiView: PlayerContainer, context: Context) {
        uiView.playerLayer.player = player
    }

    final class PlayerContainer: UIView {
        override static var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    }
}
