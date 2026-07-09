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
    private let cache = NSCache<NSNumber, UIImage>()
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
    func thumbnail(atSourceSecond sec: Double) -> UIImage? {
        guard let generator else { return nil }
        let key = Int(sec.rounded())
        if let img = cache.object(forKey: NSNumber(value: key)) { return img }
        guard !inFlight.contains(key) else { return nil }
        inFlight.insert(key)
        let time = CMTime(seconds: Double(key), preferredTimescale: 600)
        if let cg = try? generator.copyCGImage(at: time, actualTime: nil) {
            let img = UIImage(cgImage: cg)
            cache.setObject(img, forKey: NSNumber(value: key), cost: cg.bytesPerRow * cg.height)
            inFlight.remove(key)
            return img
        }
        inFlight.remove(key)
        return nil
    }

    /// Warm tier-0 thumbnails across the whole source (fire and forget).
    func warm(durationSeconds: Double, everySeconds: Double = 5) {
        guard generator != nil, durationSeconds > 0 else { return }
        var t = 0.0
        while t < durationSeconds {
            _ = thumbnail(atSourceSecond: t)
            t += everySeconds
        }
    }
}
