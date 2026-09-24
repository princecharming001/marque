import Foundation

// MARK: - Timeline zoom + filmstrip density (ED-12) — Foundation-only, covered by LogicTests.
//
// The hand-pinch range stays 6…110 pt/s on normal takes, but the zoom-cycle "fit" level
// must fit ANY cut in the viewport — a 10-minute take needs ~0.5 pt/s — so the floor drops
// to whatever fit needs (never below 0.5). Filmstrip density follows the RENDERED width
// (≈ one frame per 60–90 pt) instead of a fixed per-clip count, bounded by a timeline-wide
// budget so a long take at max zoom can't blow the thumbnail cache.

enum TimelineZoom {
    static let defaultPPS: Double = 18
    static let closePPS: Double = 60
    static let minPinchPPS: Double = 6
    static let maxPPS: Double = 110
    /// Only ever reached as the "fit" level of a very long cut.
    static let absoluteMinPPS: Double = 0.5
    /// The old `(screen width - 60)` visible budget (lane gutter + breathing room).
    static let viewportInset: Double = 60

    /// pt/s that shows the whole cut across the viewport.
    static func fitPPS(viewportWidth: Double, totalSeconds: Double) -> Double {
        min(maxPPS, max(absoluteMinPPS, (viewportWidth - viewportInset) / max(1.0, totalSeconds)))
    }

    /// The pinch floor: 6 pt/s, or lower when that is what fitting the cut takes.
    static func minPPS(fit: Double) -> Double { max(absoluteMinPPS, min(minPinchPPS, fit)) }

    static func clamp(_ pps: Double, fit: Double) -> Double {
        min(maxPPS, max(minPPS(fit: fit), pps))
    }

    /// The zoom button cycles fit-whole-video → default → close-up. Levels closer than
    /// 1 pt/s collapse into one (a short take whose fit ≈ default must still advance), and
    /// a level reached by pinching falls back to the original thresholds.
    static func nextCycleLevel(current: Double, fit: Double) -> Double {
        var levels: [Double] = []
        for l in [fit, defaultPPS, closePPS] where !levels.contains(where: { abs($0 - l) < 1 }) {
            levels.append(l)
        }
        if let i = levels.firstIndex(where: { abs($0 - current) < 1 }) {
            return levels[(i + 1) % levels.count]
        }
        return current < 17 ? defaultPPS : (current < 55 ? closePPS : fit)
    }

    /// Seconds between ruler labels: the densest interval keeping labels ≥ 36 pt apart.
    static func rulerInterval(pps: Double) -> Int {
        [1, 2, 5, 10, 15, 30, 60, 120, 300, 600].first { Double($0) * pps >= 36 } ?? 600
    }

    /// "12s" as before; minute-scale rulers (long cuts at fit) read "2:00".
    static func rulerLabel(seconds: Int, interval: Int) -> String {
        interval >= 60 ? String(format: "%d:%02d", seconds / 60, seconds % 60) : "\(seconds)s"
    }

    /// The transient "Ns across" pill.
    static func acrossLabel(viewportWidth: Double, pps: Double) -> String {
        let s = (viewportWidth - viewportInset) / max(absoluteMinPPS, pps)
        return s < 100 ? String(format: "%.0fs across", s)
                       : String(format: "%d:%02d across", Int(s) / 60, Int(s) % 60)
    }
}

enum FilmstripDensity {
    /// One thumbnail per ~72 pt of rendered strip (51–102 pt across a zoom bucket).
    static let targetThumbWidth: Double = 72
    /// Thumbnails across the WHOLE timeline (~110 KB each at 120×214) — kept under the
    /// FilmstripCache's 24 MB NSCache cap so visible frames aren't evicted by off-screen ones.
    static let timelineBudget = 160

    /// Quantized zoom level (log2 buckets): crossed by real zoom changes only, never by a
    /// trim drag, so the generation task never re-keys mid-drag.
    static func zoomBucket(pps: Double) -> Int { Int(floor(log2(max(0.25, pps)))) }

    /// The geometric middle of a bucket — the pps a bucket's count is sized for.
    static func bucketPPS(_ bucket: Int) -> Double { pow(2.0, Double(bucket)) * 2.0.squareRoot() }

    /// Thumbnails for one clip cell: ≈ its rendered width / 72 pt, at most one per whole
    /// source second, at most its share of the timeline budget, at least one.
    static func thumbCount(outputSeconds: Double, sourceSeconds: Int, bucket: Int,
                           totalOutputSeconds: Double) -> Int {
        let byWidth = Int((max(0, outputSeconds) * bucketPPS(bucket) / targetThumbWidth).rounded(.up))
        let share = Int(Double(timelineBudget) * max(0, outputSeconds) / max(1e-6, max(outputSeconds, totalOutputSeconds)))
        return max(1, min(byWidth, max(1, sourceSeconds), max(1, share)))
    }

    /// `count` evenly spaced, distinct whole source seconds across [srcIn, srcOut).
    static func sampleSeconds(srcIn: Int, srcOut: Int, count: Int) -> [Int] {
        let start = Int(framesToSeconds(srcIn))
        let end = max(start + 1, Int(framesToSeconds(srcOut)))
        let n = max(1, min(count, end - start))
        return (0..<n).map { start + ($0 * (end - start)) / n }
    }

    /// Tier-0 warm: at most ~36 frames across the source, never denser than one per 5 s.
    static func warmStride(durationSeconds: Double, minimum: Double = 5) -> Double {
        max(minimum, durationSeconds / 36)
    }
}
