import Foundation
import AVFoundation
import Observation

// MARK: - EditorPlayerController — plays the DRAFT edit by seeking a single AVPlayer through the
// kept source intervals in play order (evolves RoughCutController, EditorView.swift:1139). No
// AVMutableComposition rebuild per edit — interval-seek mirrors the render plan's _kept_intervals
// and updates instantly. Publishes currentOutputTime for the timeline playhead. `placeholder`
// (no source URL — keyless mock jobs) keeps the whole editor drivable in the simulator.

@MainActor
@Observable
final class EditorPlayerController {
    let player = AVPlayer()
    private(set) var placeholder: Bool
    private(set) var isPlaying = false
    var currentOutputTime: Double = 0        // seconds along the OUTPUT (edited) timeline
    var totalOutputTime: Double = 0

    private var intervals: [(srcIn: Int, srcOut: Int, speed: Double)] = []
    private var timeObserver: Any?
    private var boundaryObserver: Any?
    private var placeholderClock: Timer?         // I-7: synthetic playhead for keyless mode
    private var pendingSeek = false
    private var queuedSeekTarget: Double?
    // UX-3: audio parity — the preview honors mute/clip-volume and plays the picked music
    // (with voice ducking) instead of lying at full volume until the server render.
    private var volumeRanges: [EditorVolumeRange] = []
    private var speechFrames: [Int] = []
    private var musicPlayer: AVPlayer?
    private var musicURL: String?
    private var musicVolume: Double = 0.15
    private var musicDucks = true
    private var musicLoop: NSObjectProtocol?

    init(sourceURL: URL?) {
        if let sourceURL {
            placeholder = false
            player.replaceCurrentItem(with: AVPlayerItem(url: sourceURL))
            player.isMuted = false
        } else {
            placeholder = true
        }
        installTimeObserver()
    }

    /// Rebuild the interval map after any draft change; preserves the playhead when possible.
    func update(document: EditorDocument) {
        intervals = document.keptIntervalsWithSpeed
        totalOutputTime = document.outputSeconds
        currentOutputTime = min(currentOutputTime, totalOutputTime)
        // UX-3: preview honors the draft's audio — mute/volume ranges + the picked music.
        volumeRanges = document.volumeRanges
        speechFrames = document.speechFrames
        syncMusicPlayer(document.music)
        applyVolume(atSourceFrame: secondsToFrame(sourceSeconds(forOutput: currentOutputTime) ?? 0))
        if isPlaying { pause() }
    }

    // MARK: UX-3 audio parity

    /// Set the main player's volume from the draft's volume ranges at a source frame.
    /// (AVPlayer caps at 1.0 — a >1 boost stays render-only, which is honest enough.)
    private func applyVolume(atSourceFrame f: Int) {
        let v = volumeRanges.first { $0.srcIn <= f && f < $0.srcOut }?.volume ?? 1.0
        player.volume = Float(min(1.0, max(0.0, v)))
        // Music ducks under speech: drop to 40% of its set volume near any speech frame.
        if let mp = musicPlayer {
            // Perf: binary search the (sorted) speech frames — the linear contains{} scan
            // ran on every 30Hz tick and compounded the caption-change hitch.
            let nearSpeech = musicDucks && Self.hasNear(speechFrames, f, within: 15)
            mp.volume = Float(min(1.0, musicVolume * (nearSpeech ? 0.4 : 1.0)))
        }
    }

    /// Binary search: any element within ±window of x in a SORTED array.
    static func hasNear(_ sorted: [Int], _ x: Int, within window: Int) -> Bool {
        var lo = 0, hi = sorted.count
        while lo < hi {
            let mid = (lo + hi) / 2
            if sorted[mid] < x - window { lo = mid + 1 } else { hi = mid }
        }
        return lo < sorted.count && abs(sorted[lo] - x) < window
    }

    /// Create/replace/remove the looping preview player for the draft's music track.
    private func syncMusicPlayer(_ music: EditorMusic?) {
        guard !placeholder else { return }
        guard let music, let url = URL(string: music.url) else {
            musicPlayer?.pause(); musicPlayer = nil; musicURL = nil
            if let musicLoop { NotificationCenter.default.removeObserver(musicLoop); self.musicLoop = nil }
            return
        }
        musicVolume = music.volume
        musicDucks = music.duckVoice
        if musicURL != music.url {
            musicURL = music.url
            if let musicLoop { NotificationCenter.default.removeObserver(musicLoop) }
            let item = AVPlayerItem(url: url)
            let mp = AVPlayer(playerItem: item)
            mp.volume = Float(music.volume)
            musicLoop = NotificationCenter.default.addObserver(
                forName: .AVPlayerItemDidPlayToEndTime, object: item, queue: .main) { [weak mp] _ in
                    mp?.seek(to: .zero); mp?.play()
                }
            musicPlayer = mp
            if isPlaying { mp.play() }
        } else {
            musicPlayer?.volume = Float(music.volume)
        }
    }

    func togglePlay() { isPlaying ? pause() : play() }

    func play() {
        // UX-6: playing from the end restarts from the top; mid-timeline just resumes.
        // (The playhead PARKS at the end after a play-through — it no longer yanks to 0:00.)
        if currentOutputTime >= totalOutputTime - 0.03 { currentOutputTime = 0 }
        // I-7: keyless/mock clips have no source video — drive the playhead with a synthetic
        // clock so Play still animates the timeline (and Maestro can verify playback).
        if placeholder {
            guard !isPlaying, totalOutputTime > 0 else { return }
            isPlaying = true
            placeholderClock?.invalidate()
            placeholderClock = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
                guard let self, self.isPlaying else { return }
                self.currentOutputTime += 1.0 / 30.0
                if self.currentOutputTime >= self.totalOutputTime {
                    self.currentOutputTime = self.totalOutputTime   // park, don't reset
                    self.pause()
                }
            }
            return
        }
        guard !intervals.isEmpty else { return }
        isPlaying = true
        seek(toOutput: currentOutputTime) { [weak self] in
            guard let self else { return }
            self.player.play()
            self.applyRate(atSourceFrame: secondsToFrame(self.player.currentTime().seconds))
        }
        musicPlayer?.play()
        installBoundaryObserver()
    }

    func pause() {
        isPlaying = false
        placeholderClock?.invalidate(); placeholderClock = nil
        player.pause()
        musicPlayer?.pause()
    }

    /// UX-5: show a source frame on the picture WITHOUT moving the composition playhead —
    /// used while dragging a trim handle so the creator sees the exact frame they're cutting
    /// on while the timeline stays put under their finger. Coalesced like seek().
    func previewSourceSeconds(_ srcSec: Double) {
        guard !placeholder else { return }
        if pendingSeek { queuedSeekTarget = nil; return }   // drop stale preview targets
        pendingSeek = true
        // Trim-lag fix: a WIDE-tolerance seek during the drag (WWDC22: zero-tolerance
        // forces dependent-frame decode and stalls; keyframe-snapped is instant). The
        // exact frame lands on gesture end via the normal zero-tolerance seek path.
        let tol = CMTime(seconds: 0.15, preferredTimescale: 600)
        player.seek(to: CMTime(seconds: max(0, srcSec), preferredTimescale: 600),
                    toleranceBefore: tol, toleranceAfter: tol) { [weak self] _ in
            self?.pendingSeek = false
        }
    }

    /// Seek to an OUTPUT-timeline position (coalesced so drags never queue-pile).
    func seek(toOutput outputSec: Double, completion: (() -> Void)? = nil) {
        currentOutputTime = max(0, min(outputSec, totalOutputTime))
        guard !placeholder else { completion?(); return }
        guard let srcSec = sourceSeconds(forOutput: currentOutputTime) else { completion?(); return }
        applyVolume(atSourceFrame: secondsToFrame(srcSec))    // UX-3: audio state tracks scrubs too
        if pendingSeek { queuedSeekTarget = currentOutputTime; return }
        pendingSeek = true
        player.seek(to: CMTime(seconds: srcSec, preferredTimescale: 600),
                    toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] _ in
            guard let self else { return }
            self.pendingSeek = false
            if let q = self.queuedSeekTarget { self.queuedSeekTarget = nil; self.seek(toOutput: q) }
            else { completion?() }
        }
    }

    // MARK: internals

    private func sourceSeconds(forOutput outputSec: Double) -> Double? {
        guard !intervals.isEmpty else { return nil }
        var acc = 0
        let target = secondsToFrame(outputSec)
        for iv in intervals {
            let outLen = outputFrames(iv.srcOut - iv.srcIn, speed: iv.speed)
            if target < acc + outLen {
                let srcOffset = Int((Double(target - acc) * iv.speed).rounded(.toNearestOrEven))
                return framesToSeconds(min(iv.srcOut - 1, iv.srcIn + srcOffset))
            }
            acc += outLen
        }
        return framesToSeconds(intervals.last?.srcOut ?? 0)
    }

    /// Map the player's SOURCE time back to OUTPUT time, so playback advances the playhead and
    /// jumps across cut boundaries.
    private func outputSeconds(forSource srcSec: Double) -> Double {
        let srcFrame = secondsToFrame(srcSec)
        var acc = 0
        for iv in intervals {
            if srcFrame >= iv.srcIn && srcFrame < iv.srcOut {
                let outOffset = Int((Double(srcFrame - iv.srcIn) / iv.speed).rounded(.toNearestOrEven))
                return framesToSeconds(acc + outOffset)
            }
            acc += outputFrames(iv.srcOut - iv.srcIn, speed: iv.speed)
        }
        return currentOutputTime
    }

    private func installTimeObserver() {
        timeObserver = player.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 1.0 / 30.0, preferredTimescale: 600), queue: .main
        ) { [weak self] time in
            guard let self, self.isPlaying, !self.placeholder else { return }
            let srcSec = time.seconds
            // If we've run past the current kept interval, jump to the next one.
            let srcFrame = secondsToFrame(srcSec)
            self.applyVolume(atSourceFrame: srcFrame)      // UX-3: live mute/volume/duck
            self.applyRate(atSourceFrame: srcFrame)        // per-clip speed in preview
            if let iv = self.currentInterval(srcFrame: srcFrame), srcFrame >= iv.srcOut - 1 {
                self.advanceToNextInterval(after: iv)
                return
            }
            self.currentOutputTime = self.outputSeconds(forSource: srcSec)
            // UX-6: park at the end (don't yank the playhead back to 0:00).
            if self.currentOutputTime >= self.totalOutputTime - 0.03 { self.pause() }
        }
    }

    private func installBoundaryObserver() {
        if let boundaryObserver { player.removeTimeObserver(boundaryObserver); self.boundaryObserver = nil }
        let bounds = intervals.map { NSValue(time: CMTime(seconds: framesToSeconds($0.srcOut), preferredTimescale: 600)) }
        guard !bounds.isEmpty else { return }
        boundaryObserver = player.addBoundaryTimeObserver(forTimes: bounds, queue: .main) { [weak self] in
            guard let self, self.isPlaying else { return }
            let srcFrame = secondsToFrame(self.player.currentTime().seconds)
            if let iv = self.currentInterval(srcFrame: srcFrame - 1) { self.advanceToNextInterval(after: iv) }
        }
    }

    private func currentInterval(srcFrame: Int) -> (srcIn: Int, srcOut: Int, speed: Double)? {
        intervals.first { srcFrame >= $0.srcIn && srcFrame < $0.srcOut }
    }

    /// Set the player rate to the interval's speed (CapCut per-clip speed in preview).
    private func applyRate(atSourceFrame f: Int) {
        guard isPlaying else { return }
        let speed = currentInterval(srcFrame: f)?.speed ?? 1.0
        if abs(Double(player.rate) - speed) > 0.01 { player.rate = Float(speed) }
    }

    private func advanceToNextInterval(after iv: (srcIn: Int, srcOut: Int, speed: Double)) {
        guard let i = intervals.firstIndex(where: { $0.srcIn == iv.srcIn && $0.srcOut == iv.srcOut }) else { return }
        if i + 1 < intervals.count {
            let next = intervals[i + 1]
            player.seek(to: CMTime(seconds: framesToSeconds(next.srcIn), preferredTimescale: 600),
                        toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] _ in
                guard let self, self.isPlaying else { return }
                self.player.rate = Float(next.speed)      // carry the next clip's speed
            }
        } else {
            pause(); currentOutputTime = totalOutputTime   // UX-6: park at the end
        }
    }

    func teardown() {
        if let timeObserver { player.removeTimeObserver(timeObserver) }
        if let boundaryObserver { player.removeTimeObserver(boundaryObserver) }
        placeholderClock?.invalidate(); placeholderClock = nil
        if let musicLoop { NotificationCenter.default.removeObserver(musicLoop) }
        musicPlayer?.pause(); musicPlayer = nil
        player.pause()
    }
}
