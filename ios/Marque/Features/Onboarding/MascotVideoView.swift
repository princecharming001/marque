import SwiftUI
import UIKit
import AVFoundation

// A bare, muted, seamlessly-looping video view for the animated mascot. AVPlayerLooper
// repeats the clip with no gap; because each clip is generated with its FIRST frame ==
// LAST frame, the loop is invisible. No controls, aspect-fit — it drops straight in where
// the static mascot image used to sit.
struct MascotVideoView: UIViewRepresentable {
    let resource: String   // bundle resource name, no extension

    func makeUIView(context: Context) -> LoopingVideoUIView { LoopingVideoUIView(resource: resource) }
    func updateUIView(_ uiView: LoopingVideoUIView, context: Context) {}
    static func dismantleUIView(_ uiView: LoopingVideoUIView, coordinator: ()) { uiView.stop() }
}

final class LoopingVideoUIView: UIView {
    private var looper: AVPlayerLooper?
    private var player: AVQueuePlayer?
    private let playerLayer = AVPlayerLayer()

    init(resource: String) {
        super.init(frame: .zero)
        backgroundColor = .clear
        isUserInteractionEnabled = false
        playerLayer.videoGravity = .resizeAspect
        layer.addSublayer(playerLayer)

        // Prefer .mov (HEVC-with-alpha, transparent) then .mp4.
        guard let url = Bundle.main.url(forResource: resource, withExtension: "mov")
            ?? Bundle.main.url(forResource: resource, withExtension: "mp4") else { return }
        let item = AVPlayerItem(asset: AVURLAsset(url: url))
        let queue = AVQueuePlayer()
        queue.isMuted = true
        queue.preventsDisplaySleepDuringVideoPlayback = false
        looper = AVPlayerLooper(player: queue, templateItem: item)
        playerLayer.player = queue
        player = queue
        queue.play()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    override func layoutSubviews() {
        super.layoutSubviews()
        playerLayer.frame = bounds
    }

    func stop() {
        player?.pause()
        looper?.disableLooping()
    }
}
