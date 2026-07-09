import SwiftUI
import AVFoundation

// Chrome-free full-bleed player surface (AVKit's VideoPlayer forces default transport chrome;
// the editor draws its own transport synced to the timeline playhead).
struct PlayerLayerView: UIViewRepresentable {
    let player: AVPlayer

    func makeUIView(context: Context) -> PlayerContainer {
        let v = PlayerContainer()
        v.playerLayer.player = player
        v.playerLayer.videoGravity = .resizeAspect
        v.backgroundColor = .black
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
