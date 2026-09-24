import Foundation
import AVFoundation
import UIKit

// MARK: - FilmstripCache — bounded, cancellable thumbnail generation for the timeline filmstrip.
// The app's only prior AVAssetImageGenerator use (MediaStore.poster) grabs a single frame; a
// CapCut filmstrip needs many. Tier-0 = 1 thumb / 5s (eager, coarse); tier-1 = 1 thumb / 1s
// (on zoom-in). NSCache with a cost cap keeps a 90s 1080p clip well under budget.

actor FilmstripCache {
    private let asset: AVAsset?
    private let generator: AVAssetImageGenerator?
    // NSCache is thread-safe; nonisolated so view bodies can read hits synchronously
    // (ED-12: the cells no longer keep their own copies of every frame).
    nonisolated(unsafe) private let cache = NSCache<NSNumber, UIImage>()
    private var inFlight: Set<Int> = []

    init(sourceURL: URL?) {
        if let sourceURL {
            let a = AVURLAsset(url: sourceURL)
            asset = a
            let g = AVAssetImageGenerator(asset: a)
            g.appliesPreferredTrackTransform = true
            g.maximumSize = CGSize(width: 120, height: 214)
            g.requestedTimeToleranceBefore = CMTime(seconds: 2.5, preferredTimescale: 600)
            g.requestedTimeToleranceAfter = CMTime(seconds: 2.5, preferredTimescale: 600)
            generator = g
        } else {
            asset = nil; generator = nil
        }
        cache.totalCostLimit = 24 * 1024 * 1024   // ~24MB
    }

    /// The thumbnail nearest a source-second, generating it on miss. nil in placeholder mode
    /// (no source video) — the timeline renders solid labeled cells instead.
    ///
    /// Split-preview fix: an in-flight collision used to return nil PERMANENTLY (the
    /// caller never retried), which is exactly how one subclip of a split lost its
    /// filmstrip — both new cells requested the same rounded second concurrently and
    /// the loser stayed blank forever. Now peers WAIT for the winner's result.
    /// Generation uses the async image(at:) API (WWDC22 "Create a more responsive
    /// media app") instead of the synchronous copyCGImage.
    /// A cache hit, synchronously (nil on a miss — call thumbnail(atSourceSecond:) to fill it).
    nonisolated func cachedThumbnail(atSourceSecond sec: Double) -> UIImage? {
        cache.object(forKey: NSNumber(value: Int(sec.rounded())))
    }

    func thumbnail(atSourceSecond sec: Double) async -> UIImage? {
        guard let generator else { return nil }
        let key = Int(sec.rounded())
        if let img = cache.object(forKey: NSNumber(value: key)) { return img }
        while inFlight.contains(key) {
            // A cancelled waiter must leave: Task.sleep throws immediately once cancelled,
            // so without this check the loop spun hot until the winner finished.
            if Task.isCancelled { return nil }
            try? await Task.sleep(nanoseconds: 40_000_000)
            if let img = cache.object(forKey: NSNumber(value: key)) { return img }
        }
        if let img = cache.object(forKey: NSNumber(value: key)) { return img }
        inFlight.insert(key)
        defer { inFlight.remove(key) }
        let time = CMTime(seconds: Double(key), preferredTimescale: 600)
        guard let result = try? await generator.image(at: time) else { return nil }
        let cg = result.image
        let img = UIImage(cgImage: cg)
        cache.setObject(img, forKey: NSNumber(value: key), cost: cg.bytesPerRow * cg.height)
        return img
    }

    /// Warm tier-0 thumbnails across the whole source (cancellable). ED-12: never more than
    /// ~36 frames — a 10-minute take no longer queues 120 decodes that crowd the cache.
    func warm(durationSeconds: Double, everySeconds: Double = 5) async {
        guard generator != nil, durationSeconds > 0 else { return }
        let stride = FilmstripDensity.warmStride(durationSeconds: durationSeconds, minimum: everySeconds)
        var t = 0.0
        while t < durationSeconds {
            if Task.isCancelled { return }
            _ = await thumbnail(atSourceSecond: t)
            t += stride
        }
    }
}
