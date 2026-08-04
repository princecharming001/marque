import SwiftUI
import UIKit
import AVFoundation

// A polished, in-house video player — deliberately NOT AVKit's VideoPlayer /
// AVPlayerViewController (which paints Apple's stock chrome and can't be told to fill,
// so a 9:16 clip in a non-9:16 box gets thick pillarbox bars). This is an AVPlayerLayer
// with .resizeAspectFill (no letterbox — put it in a 9:16 container and the render fills
// exactly) plus a small custom control layer: tap to play/pause, a slim accent progress
// bar you can scrub, and a mute toggle. Starts paused on a poster frame so a detail view
// doesn't autoplay in the user's face.

final class InkPlayerModel: ObservableObject {
    let player: AVPlayer
    @Published var isPlaying = false
    @Published var progress: Double = 0        // 0…1 of duration
    // Build 68: the numbers. A bar alone can't answer "how far in am I" (owner
    // complaint + the iOS-18 Photos regression thread) — elapsed/total ride every frame.
    @Published var currentS: Double = 0
    @Published var durationS: Double = 0
    @Published var muted: Bool
    let loops: Bool
    private var timeObs: Any?
    private var endObs: NSObjectProtocol?

    init(url: URL, loops: Bool, muted: Bool) {
        self.loops = loops
        self.muted = muted
        let item = AVPlayerItem(url: url)
        player = AVPlayer(playerItem: item)
        player.isMuted = muted
        timeObs = player.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 0.2, preferredTimescale: 600), queue: .main
        ) { [weak self] t in
            guard let self, let dur = self.player.currentItem?.duration.seconds,
                  dur.isFinite, dur > 0 else { return }
            self.durationS = dur
            self.currentS = min(dur, max(0, t.seconds))
            self.progress = min(1, max(0, t.seconds / dur))
        }
        endObs = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            self.player.seek(to: .zero)
            if self.loops { self.player.play() } else { self.isPlaying = false }
        }
    }

    func toggle() {
        if isPlaying { player.pause() } else { player.play() }
        isPlaying.toggle()
    }
    func toggleMute() { muted.toggle(); player.isMuted = muted }
    /// Pause immediately — called when the player's view leaves screen (sheet dismissed)
    /// so audio never keeps playing behind a closed preview.
    func stop() { player.pause(); isPlaying = false }
    func seek(to frac: Double) {
        guard let dur = player.currentItem?.duration.seconds, dur.isFinite, dur > 0 else { return }
        let f = min(1, max(0, frac))
        player.seek(to: CMTime(seconds: f * dur, preferredTimescale: 600),
                    toleranceBefore: .zero, toleranceAfter: .zero)
        progress = f
        durationS = dur
        currentS = f * dur
    }

    static func timecode(_ s: Double) -> String {
        let t = max(0, Int(s.rounded()))
        return String(format: "%d:%02d", t / 60, t % 60)
    }
    deinit {
        player.pause()          // belt: never leave audio running past the model's life
        if let timeObs { player.removeTimeObserver(timeObs) }
        if let endObs { NotificationCenter.default.removeObserver(endObs) }
    }
}

private final class InkPlayerHostView: UIView {
    override class var layerClass: AnyClass { AVPlayerLayer.self }
    var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
}

private struct InkPlayerLayerHost: UIViewRepresentable {
    let player: AVPlayer
    var cornerRadius: CGFloat = 0
    func makeUIView(context: Context) -> UIView {
        let v = InkPlayerHostView()
        v.backgroundColor = .black
        v.playerLayer.player = player
        v.playerLayer.videoGravity = .resizeAspectFill   // fill — no pillarbox bars
        // Round the AVPlayerLayer DIRECTLY: a SwiftUI .clipShape mask does not reliably
        // clip a UIViewRepresentable-hosted AVPlayerLayer (it composites separately and
        // overdraws its bounds), which is why the detail player showed square corners
        // under a rounded container. masksToBounds on the layer itself always clips.
        v.layer.cornerRadius = cornerRadius
        v.layer.masksToBounds = true
        return v
    }
    func updateUIView(_ v: UIView, context: Context) {
        (v as? InkPlayerHostView)?.playerLayer.player = player
        v.layer.cornerRadius = cornerRadius
        v.layer.masksToBounds = true
    }
}

struct InkVideoPlayer: View {
    @StateObject private var model: InkPlayerModel
    var showsMute: Bool
    var cornerRadius: CGFloat
    // Build 69: external mute switch. fullScreenCover does NOT remove the presenting
    // view, so onDisappear never fires and the detail player kept talking under the
    // editor. The presenter flips this when it covers the player.
    var suspended: Bool = false

    init(url: URL, loops: Bool = false, startMuted: Bool = false, showsMute: Bool = true,
         cornerRadius: CGFloat = Radius.lg, suspended: Bool = false) {
        _model = StateObject(wrappedValue: InkPlayerModel(url: url, loops: loops, muted: startMuted))
        self.showsMute = showsMute
        self.cornerRadius = cornerRadius
        self.suspended = suspended
    }

    @State private var chromeVisible = true
    @State private var hideTask: DispatchWorkItem?
    @State private var scrubbing = false

    var body: some View {
        ZStack {
            InkPlayerLayerHost(player: model.player, cornerRadius: cornerRadius)

            // Whole-surface tap target: toggle play/pause and flash the chrome.
            Color.black.opacity(0.001)
                .contentShape(Rectangle())
                .onTapGesture { model.toggle(); flashChrome() }

            // Center play/pause — shown while paused, or briefly after a tap.
            if !model.isPlaying || chromeVisible {
                Image(systemName: model.isPlaying ? "pause.fill" : "play.fill")
                    .font(.system(size: 22, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 58, height: 58)
                    .background(.ultraThinMaterial, in: Circle())
                    .overlay(Circle().strokeBorder(.white.opacity(0.28), lineWidth: 1))
                    .shadow(color: .black.opacity(0.25), radius: 8, y: 2)
                    .allowsHitTesting(false)
                    .transition(.opacity.combined(with: .scale(scale: 0.9)))
            }

            // Bottom chrome (build 68, research-grounded: TikTok/CapCut hybrid for
            // review-before-posting). Elapsed/total timecode is ALWAYS readable — it
            // dims with the chrome but never disappears; the thin bar never hides at
            // all. Dragging thickens the bar and grows the timecode, TikTok-style.
            VStack(spacing: 6) {
                Spacer()
                HStack(spacing: 10) {
                    if model.durationS > 0 {
                        Text("\(InkPlayerModel.timecode(model.currentS)) / \(InkPlayerModel.timecode(model.durationS))")
                            .font(.system(size: scrubbing ? 15 : 11, weight: .semibold))
                            .monospacedDigit()
                            .foregroundStyle(.white)
                            .padding(.horizontal, 8).padding(.vertical, 3)
                            .background(Capsule().fill(.black.opacity(0.35)))
                            .accessibilityIdentifier("player.timecode")
                    }
                    Spacer(minLength: 0)
                    if showsMute {
                        Button { model.toggleMute() } label: {
                            Image(systemName: model.muted ? "speaker.slash.fill" : "speaker.wave.2.fill")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(.white)
                                .frame(width: 30, height: 30)
                                .background(.ultraThinMaterial, in: Circle())
                        }
                        .opacity(model.isPlaying && !chromeVisible ? 0 : 1)
                    }
                }
                .padding(.horizontal, 12)
                .opacity(scrubbing || !model.isPlaying || chromeVisible ? 1 : 0.55)

                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(.white.opacity(0.28))
                        Capsule().fill(Palette.accent)
                            .frame(width: max(2, geo.size.width * model.progress))
                        if scrubbing {
                            Circle().fill(.white)
                                .frame(width: 12, height: 12)
                                .offset(x: max(0, geo.size.width * model.progress - 6))
                                .shadow(color: .black.opacity(0.3), radius: 2)
                        }
                    }
                    .frame(height: scrubbing ? 6 : 3)
                    .frame(maxHeight: .infinity, alignment: .center)
                    .contentShape(Rectangle())
                    .gesture(
                        DragGesture(minimumDistance: 0)
                            .onChanged { v in
                                scrubbing = true
                                model.seek(to: v.location.x / max(1, geo.size.width))
                                flashChrome()
                            }
                            .onEnded { _ in scrubbing = false }
                    )
                }
                .frame(height: 26)
                .padding(.horizontal, 12)
                .padding(.bottom, 8)
            }
            .animation(.easeInOut(duration: 0.18), value: scrubbing)
            .animation(.easeInOut(duration: 0.25), value: chromeVisible)
            .animation(.easeInOut(duration: 0.25), value: model.isPlaying)
        }
        .background(Color.black)
        // Built-in rounding so the aspect-fill video surface is always clipped, even
        // if a caller forgets its own .clipShape (the AVPlayerLayer overdraws its bounds).
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
        .animation(.easeInOut(duration: 0.2), value: model.isPlaying)
        .animation(.easeInOut(duration: 0.2), value: chromeVisible)
        // Stop playback the moment the view leaves screen (preview sheet dismissed) so
        // audio never keeps running behind a closed popup.
        .onDisappear { model.stop() }
        .onChange(of: suspended) { _, covered in
            if covered { model.stop() }
        }
    }

    private func flashChrome() {
        hideTask?.cancel()
        chromeVisible = true
        let task = DispatchWorkItem { chromeVisible = false }
        hideTask = task
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.4, execute: task)
    }
}
