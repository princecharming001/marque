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

    private var intervals: [(srcIn: Int, srcOut: Int)] = []
    private var timeObserver: Any?
    private var boundaryObserver: Any?
    private var pendingSeek = false
    private var queuedSeekTarget: Double?

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
        intervals = document.keptIntervals
        totalOutputTime = document.outputSeconds
        currentOutputTime = min(currentOutputTime, totalOutputTime)
        if isPlaying { pause() }
    }

    func togglePlay() { isPlaying ? pause() : play() }

    func play() {
        guard !placeholder, !intervals.isEmpty else { return }
        isPlaying = true
        seek(toOutput: currentOutputTime) { [weak self] in self?.player.play() }
        installBoundaryObserver()
    }

    func pause() {
        isPlaying = false
        player.pause()
    }

    /// Seek to an OUTPUT-timeline position (coalesced so drags never queue-pile).
    func seek(toOutput outputSec: Double, completion: (() -> Void)? = nil) {
        currentOutputTime = max(0, min(outputSec, totalOutputTime))
        guard !placeholder else { completion?(); return }
        guard let srcSec = sourceSeconds(forOutput: currentOutputTime) else { completion?(); return }
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
            let len = iv.srcOut - iv.srcIn
            if target < acc + len { return framesToSeconds(iv.srcIn + (target - acc)) }
            acc += len
        }
        return framesToSeconds(intervals.last?.srcOut ?? 0)
    }

    /// Map the player's SOURCE time back to OUTPUT time, so playback advances the playhead and
    /// jumps across cut boundaries.
    private func outputSeconds(forSource srcSec: Double) -> Double {
        let srcFrame = secondsToFrame(srcSec)
        var acc = 0
        for iv in intervals {
            if srcFrame >= iv.srcIn && srcFrame < iv.srcOut { return framesToSeconds(acc + (srcFrame - iv.srcIn)) }
            acc += iv.srcOut - iv.srcIn
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
            if let iv = self.currentInterval(srcFrame: srcFrame), srcFrame >= iv.srcOut - 1 {
                self.advanceToNextInterval(after: iv)
                return
            }
            self.currentOutputTime = self.outputSeconds(forSource: srcSec)
            if self.currentOutputTime >= self.totalOutputTime - 0.03 { self.pause(); self.currentOutputTime = 0 }
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

    private func currentInterval(srcFrame: Int) -> (srcIn: Int, srcOut: Int)? {
        intervals.first { srcFrame >= $0.srcIn && srcFrame < $0.srcOut }
    }

    private func advanceToNextInterval(after iv: (srcIn: Int, srcOut: Int)) {
        guard let i = intervals.firstIndex(where: { $0.srcIn == iv.srcIn && $0.srcOut == iv.srcOut }) else { return }
        if i + 1 < intervals.count {
            let next = intervals[i + 1]
            player.seek(to: CMTime(seconds: framesToSeconds(next.srcIn), preferredTimescale: 600),
                        toleranceBefore: .zero, toleranceAfter: .zero)
        } else {
            pause(); currentOutputTime = 0
        }
    }

    func teardown() {
        if let timeObserver { player.removeTimeObserver(timeObserver) }
        if let boundaryObserver { player.removeTimeObserver(boundaryObserver) }
        player.pause()
    }
}
