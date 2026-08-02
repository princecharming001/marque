import SwiftUI
import AVFoundation

// A Tinder-style card stack, generic over whatever the step is asking about (deck reels
// in the taste quiz, CTA templates in the endings quiz). It owns the gesture, the
// dismissal animation, the undo history and — critically — the AVPlayer budget: exactly
// TWO players exist no matter how long the deck is (see SwiperPlayerPool). Callers supply
// the card face and a video URL per card; everything else is the same in both steps.

// MARK: - Player pool

/// The two-player budget for a whole swipe session. One plays the top card; the other
/// pre-rolls the NEXT card so a swipe cuts to already-buffered video instead of a black
/// frame. One player per card would stack up a dozen decoders and get the app jetsammed
/// mid-onboarding, so the pool never grows — it swaps roles.
@MainActor final class SwiperPlayerPool: ObservableObject {
    private let players = [AVPlayer(), AVPlayer()]
    private var urls: [URL?] = [nil, nil]
    private var topSlot = 0
    private var loopObservers: [NSObjectProtocol] = []
    /// Bumped on every sync so the card views re-read `top`/`next` (AVPlayer is a
    /// reference type — SwiftUI can't see a role swap on its own).
    @Published private(set) var revision = 0

    var top: AVPlayer { players[topSlot] }
    var next: AVPlayer { players[1 - topSlot] }

    init() {
        // The deck plays SOUND — the owner's editing-taste swipe is meaningless without
        // hearing each reel's music/voice ("the slider for editing style doesn't have
        // music"). .playback so the cards are audible even with the ring switch on
        // silent, same as every reels surface.
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback)
        try? AVAudioSession.sharedInstance().setActive(true)
        // Loop like the feed does — a preview that ends on a frozen frame reads as broken.
        for p in players {
            p.isMuted = true
            p.actionAtItemEnd = .none
        }
        loopObservers = players.map { player in
            NotificationCenter.default.addObserver(
                forName: .AVPlayerItemDidPlayToEndTime, object: nil, queue: .main) { note in
                    // object: nil + an identity check — the item is replaced on every card,
                    // so an observer bound to one item would die after the first swipe.
                    guard let item = note.object as? AVPlayerItem,
                          item === player.currentItem else { return }
                    item.seek(to: .zero) { _ in }
                    if player.rate == 0 { return }     // don't resurrect the paused preload
                    player.play()
                }
        }
    }

    deinit {
        loopObservers.forEach(NotificationCenter.default.removeObserver)
    }

    /// Point the pool at the current top/next clip. Idempotent — safe to call on every
    /// render pass.
    func sync(top newTop: URL?, next newNext: URL?) {
        // If the preload slot ALREADY holds what's now on top, just swap roles. That's the
        // entire payoff of preloading: no re-buffer on the swipe the creator just made.
        if let newTop, urls[1 - topSlot] == newTop { topSlot = 1 - topSlot }
        load(slot: topSlot, url: newTop)
        load(slot: 1 - topSlot, url: newNext)
        players[1 - topSlot].pause()
        // Only the TOP card is audible; the preload slot stays muted so a buffering next
        // card never bleeds its track under the one the creator is judging.
        players[topSlot].isMuted = false
        players[1 - topSlot].isMuted = true
        if newTop == nil { players[topSlot].pause() } else { players[topSlot].play() }
        revision += 1
    }

    func pauseAll() { players.forEach { $0.pause(); $0.isMuted = true } }

    private func load(slot: Int, url: URL?) {
        guard urls[slot] != url else { return }        // same clip: never re-buffer
        urls[slot] = url
        players[slot].pause()
        players[slot].replaceCurrentItem(with: url.map { AVPlayerItem(url: $0) })
        players[slot].isMuted = true
    }
}

/// Renders one pooled AVPlayer. Deliberately NOT AVPlayerViewController (no chrome, no
/// per-view player) and NOT FailableVideoPlayer (which mints its own player per card).
/// Corner rounding is applied on the LAYER — a SwiftUI clipShape does not clip an
/// AVPlayerLayer.
struct PooledPlayerView: UIViewRepresentable {
    let player: AVPlayer
    var cornerRadius: CGFloat = 0

    func makeUIView(context: Context) -> PlayerHostView {
        let v = PlayerHostView()
        v.playerLayer.player = player
        v.playerLayer.videoGravity = .resizeAspectFill
        v.layer.cornerRadius = cornerRadius
        v.layer.cornerCurve = .continuous
        v.layer.masksToBounds = true
        v.backgroundColor = .black
        return v
    }

    func updateUIView(_ v: PlayerHostView, context: Context) {
        if v.playerLayer.player !== player { v.playerLayer.player = player }
        v.layer.cornerRadius = cornerRadius
    }

    final class PlayerHostView: UIView {
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    }
}

// MARK: - Card context

/// What the shell tells a card face about its position in the stack. `player` is non-nil
/// only for the top card (playing) and the one behind it (pre-rolled, paused); everything
/// deeper must render its poster image.
struct SwiperCardContext {
    let depth: Int                  // 0 = top of the stack
    let player: AVPlayer?
    var isTop: Bool { depth == 0 }
}

// MARK: - Shell

struct SwiperStepShell<Card: Identifiable, CardView: View>: View {
    let cards: [Card]
    /// One line under the buttons explaining what the swiping BUYS the creator.
    let payoff: String
    /// The clip this card previews, if any (the "No CTA" card is typographic).
    let videoURL: (Card) -> URL?
    let onLike: (Card) -> Void
    let onPass: (Card) -> Void
    /// Hands back the card whose verdict was just withdrawn so the caller can un-record it.
    let onUndo: (Card) -> Void
    let onComplete: () -> Void
    @ViewBuilder let cardView: (Card, SwiperCardContext) -> CardView

    @State private var index = 0
    @State private var history: [Bool] = []          // one entry per committed swipe
    /// The live drag. @GestureState (never @State) so an interrupted gesture can never
    /// strand a card mid-flight — SwiftUI resets it for us when the touch dies.
    @GestureState private var drag: CGSize = .zero
    /// The commit fling — @State because it must OUTLIVE the gesture that started it.
    @State private var fling: CGSize = .zero
    @State private var committing = false            // gate: one dismissal at a time
    @State private var commitTick = 0                // haptic trigger
    @StateObject private var pool = SwiperPlayerPool()

    private let cardW: CGFloat = 300
    private let cardH: CGFloat = 430

    private var offset: CGSize {
        CGSize(width: drag.width + fling.width, height: drag.height + fling.height)
    }
    /// Tilt tracks the drag but is clamped — past ~8° the card reads as spinning, not swiping.
    private var rotation: Double { max(-8, min(8, Double(offset.width) / 40)) }

    var body: some View {
        VStack(spacing: Space.lg) {
            stack
            VStack(spacing: Space.sm) {
                Text("\(min(index + 1, cards.count)) OF \(cards.count)")
                    .font(AppFont.micro).tracking(Track.label)
                    .foregroundStyle(Palette.textTertiary)
                buttons
                Text(payoff)
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .sensoryFeedback(.impact(weight: .light), trigger: commitTick)
        .onAppear { syncPool() }
        .onDisappear { pool.pauseAll() }
        .onChange(of: index) { _, _ in syncPool() }
    }

    // MARK: Stack

    private var stack: some View {
        ZStack {
            // Top 3 only: anything deeper is invisible behind the falloff and would just
            // cost layout. Drawn back-to-front so depth 0 receives the gesture.
            ForEach(visible.reversed(), id: \.1) { depth, i in
                cardView(cards[i], context(depth: depth))
                    .frame(width: cardW, height: cardH)
                    .background(Palette.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                    .shadow(color: Palette.shadowCool.opacity(0.16), radius: 18, y: 10)
                    .scaleEffect(depth == 0 ? 1 : (depth == 1 ? 0.97 : 0.94))
                    .offset(y: CGFloat(depth) * 8)
                    .offset(depth == 0 ? offset : .zero)
                    .rotationEffect(.degrees(depth == 0 ? rotation : 0))
                    .zIndex(Double(3 - depth))
                    .allowsHitTesting(depth == 0)
                    .gesture(depth == 0 ? swipe : nil)
                    .onTapGesture(count: 2) { if depth == 0 { commit(liked: true) } }
            }
            if cards.isEmpty || index >= cards.count {
                // The settle beat before onComplete fires — an empty frame, never a
                // collapsed layout that yanks the buttons up.
                Color.clear.frame(width: cardW, height: cardH)
            }
        }
        .frame(width: cardW, height: cardH + 16)
        .animation(Motion.spring, value: index)
    }

    private var visible: [(Int, Int)] {
        guard index < cards.count else { return [] }
        return (index..<min(index + 3, cards.count)).enumerated().map { ($0.offset, $0.element) }
    }

    private func context(depth: Int) -> SwiperCardContext {
        // Re-read on every revision so a role swap in the pool re-renders both faces.
        _ = pool.revision
        return SwiperCardContext(depth: depth,
                                 player: depth == 0 ? pool.top : (depth == 1 ? pool.next : nil))
    }

    private var swipe: some Gesture {
        DragGesture()
            .updating($drag) { value, state, _ in
                guard !committing else { return }
                state = value.translation
            }
            .onEnded { value in
                guard !committing else { return }
                let w = value.translation.width
                // Commit on distance OR on a fling: a fast flick that never travels far is
                // still an unambiguous verdict, and requiring the distance made the deck
                // feel sticky.
                let flung = abs(value.predictedEndTranslation.width) > 480
                guard abs(w) > cardW * 0.35 || (flung && abs(w) > 24) else { return }
                commit(liked: w > 0)
            }
    }

    // MARK: Buttons

    private var buttons: some View {
        HStack(spacing: Space.lg) {
            circleButton("xmark", size: 56, fill: Palette.surfaceRaised, tint: Palette.ink) {
                commit(liked: false)
            }
            .accessibilityIdentifier("swiper.pass")
            .accessibilityLabel("Pass")

            Button { undo() } label: {
                Image(systemName: "arrow.uturn.backward")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Palette.textSecondary)
                    .frame(width: 36, height: 36)
                    .background(Circle().fill(Palette.surfaceRaised))
                    .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
            }
            .buttonStyle(PressableStyle())
            .disabled(history.isEmpty)
            .opacity(history.isEmpty ? 0.3 : 1)
            .accessibilityIdentifier("swiper.undo")
            .accessibilityLabel("Undo last swipe")

            circleButton("heart.fill", size: 56, fill: Palette.ink, tint: Palette.onInk) {
                commit(liked: true)
            }
            .accessibilityIdentifier("swiper.like")
            .accessibilityLabel("Like")
        }
    }

    private func circleButton(_ symbol: String, size: CGFloat, fill: Color, tint: Color,
                              action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 19, weight: .semibold))
                .foregroundStyle(tint)
                .frame(width: size, height: size)
                .background(Circle().fill(fill))
                .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
                .shadow(color: Palette.shadowWarm.opacity(0.08), radius: 10, y: 4)
        }
        .buttonStyle(PressableStyle())
        .disabled(index >= cards.count)
    }

    // MARK: Commit / undo

    /// THE dismissal path — the drag, the buttons and the double-tap all land here, so a
    /// tapped verdict animates and records exactly like a swiped one.
    private func commit(liked: Bool) {
        guard !committing, index < cards.count else { return }
        committing = true
        commitTick += 1
        let card = cards[index]
        withAnimation(Motion.spring) {
            fling = CGSize(width: liked ? 620 : -620, height: offset.height - 40)
        }
        if liked { onLike(card) } else { onPass(card) }
        history.append(liked)
        Task { @MainActor in
            // Let the card clear the frame before the stack re-indexes under it.
            try? await Task.sleep(for: .milliseconds(220))
            fling = .zero
            index += 1
            committing = false
            guard index >= cards.count else { return }
            try? await Task.sleep(for: .milliseconds(250))   // the settle beat
            onComplete()
        }
    }

    private func undo() {
        guard !committing, !history.isEmpty else { return }
        history.removeLast()
        index = max(0, index - 1)
        fling = .zero
        onUndo(cards[index])
    }

    private func syncPool() {
        let top = index < cards.count ? videoURL(cards[index]) : nil
        let next = index + 1 < cards.count ? videoURL(cards[index + 1]) : nil
        pool.sync(top: top, next: next)
    }
}
