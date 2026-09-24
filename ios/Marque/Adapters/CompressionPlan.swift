import Foundation

// LV-2 (2026-09-24) — ONE size-targeted plan for every take length.
//
// MediaCompressor.forUpload used to run a bitrate-targeted HEVC transcode only for takes
// ≤150s; anything longer went straight to fixed AVAssetExportSession presets, whose output
// bitrate is whatever the preset picks — measured ≈4.9 Mbps at 960×540 (5-min → 184 MB,
// 10-min → 367 MB, 15-min → 551 MB). No cap on any server fits that, so every take over
// ~2.5 minutes ended on "too large, trim it" after minutes of encoding. This planner sizes
// the bitrate from the cap for ANY duration and steps the resolution down to what that
// bitrate can carry. Foundation-only so ios/LogicTests can compile and assert it.

struct CompressionPlan: Equatable {
    let seconds: Double
    let videoBps: Int
    let audioBps: Int
    /// The resolution rung the video bitrate affords (1080 / 720 / 540). The actual output
    /// never exceeds the source (no upscale), so `width`/`height` may be smaller.
    let shortEdge: Int
    /// Output size in the track's natural (pre-transform) space, even, aspect preserved.
    /// The writer keeps the source's preferredTransform, so orientation is untouched.
    let width: Int
    let height: Int

    /// Nominal payload: (video + audio) bits over the duration. Measured encoder output
    /// lands a few % under it; the planner's 0.92 margin absorbs container overhead + VBR.
    var nominalBytes: Int { Int((Double(videoBps + audioBps) * seconds / 8).rounded(.up)) }
}

enum CompressionPlanner {
    /// Share of the cap's bit budget handed to the encoder (muxing overhead + VBR swing).
    static let margin = 0.92
    /// Tier ceilings (unchanged from the ≤150s path): short takes don't need a huge bitrate.
    static let shortTierMaxSeconds = 90.0
    static let shortTierBps = 3_800_000
    static let longTierBps = 2_600_000
    /// Voice-audio budget: 96k mono for short takes, 64k past this length.
    static let longAudioThresholdSeconds = 150.0
    /// Resolution rungs by affordable video bitrate.
    static let rung1080MinBps = 2_000_000
    static let rung720MinBps = 900_000
    /// Below this the take is too long for the cap to be worth encoding (≈23 min at 50 MB);
    /// the caller reports "too large, trim it" instead of shipping mush.
    static let minVideoBps = 200_000
    /// Export-preset fallback, measured output bitrates. 540p measured with avconvert on real
    /// footage (5-min take → 184 MB ≈ 4.9 Mbps); 720p scaled by pixel count (1.78×).
    static let preset540Bps = 4_900_000
    static let preset720Bps = 8_700_000

    static func audioBps(seconds: Double) -> Int {
        seconds <= longAudioThresholdSeconds ? 96_000 : 64_000
    }

    static func tierCapBps(seconds: Double) -> Int {
        seconds <= shortTierMaxSeconds ? shortTierBps : longTierBps
    }

    static func shortEdge(forVideoBps bps: Int) -> Int {
        bps >= rung1080MinBps ? 1080 : (bps >= rung720MinBps ? 720 : 540)
    }

    /// The plan for a `seconds`-long source of `sourceWidth`×`sourceHeight` (natural size)
    /// against `maxBytes`. nil when the inputs are unusable or the take is too long for the
    /// cap (video bitrate would fall under `minVideoBps`).
    static func plan(seconds: Double, maxBytes: Int,
                     sourceWidth: Double, sourceHeight: Double) -> CompressionPlan? {
        guard seconds.isFinite, seconds > 0, maxBytes > 0,
              sourceWidth.isFinite, sourceHeight.isFinite,
              abs(sourceWidth) > 0, abs(sourceHeight) > 0 else { return nil }
        let audio = audioBps(seconds: seconds)
        let budget = (Double(maxBytes) * 8.0 / seconds - Double(audio)) * margin
        guard budget.isFinite else { return nil }
        let video = min(tierCapBps(seconds: seconds), Int(budget))
        guard video >= minVideoBps else { return nil }
        return make(seconds: seconds, videoBps: video, audioBps: audio,
                    sourceWidth: sourceWidth, sourceHeight: sourceHeight)
    }

    /// One overshoot retry: the transcode came out at `outputBytes` > `maxBytes` (VBR on
    /// high-motion footage). Scale the whole stream by how far it missed, with the same
    /// margin again; the resolution rung can only step down. nil if it can't get smaller.
    static func replan(_ p: CompressionPlan, outputBytes: Int, maxBytes: Int,
                       sourceWidth: Double, sourceHeight: Double) -> CompressionPlan? {
        guard outputBytes > maxBytes, outputBytes > 0, maxBytes > 0 else { return nil }
        let scaled = Double(p.videoBps + p.audioBps) * Double(maxBytes) / Double(outputBytes) * margin
        let video = Int(scaled) - p.audioBps
        guard video >= minVideoBps, video < p.videoBps else { return nil }
        let next = make(seconds: p.seconds, videoBps: video, audioBps: p.audioBps,
                        sourceWidth: sourceWidth, sourceHeight: sourceHeight)
        guard next.shortEdge <= p.shortEdge else { return nil }
        return next
    }

    private static func make(seconds: Double, videoBps: Int, audioBps: Int,
                             sourceWidth: Double, sourceHeight: Double) -> CompressionPlan {
        let edge = shortEdge(forVideoBps: videoBps)
        let (w, h) = dimensions(width: sourceWidth, height: sourceHeight, shortEdge: edge)
        return CompressionPlan(seconds: seconds, videoBps: videoBps, audioBps: audioBps,
                               shortEdge: edge, width: w, height: h)
    }

    /// Natural dimensions with the short edge capped at `shortEdge` (never upscaled),
    /// aspect ratio preserved, rounded to even numbers (H.265 needs even width/height).
    static func dimensions(width: Double, height: Double, shortEdge: Int) -> (Int, Int) {
        let w = abs(width), h = abs(height)
        let short = min(w, h)
        let scale = short > Double(shortEdge) ? Double(shortEdge) / short : 1.0
        func even(_ v: Double) -> Int { let n = Int((v * scale).rounded()); return max(2, n - (n % 2)) }
        return (even(w), even(h))
    }

    /// Could an export preset of measured output `bps` land under `maxBytes`? Unknown
    /// duration → assume it might (the preset is then the only thing that can try).
    static func presetMayFit(bps: Int, seconds: Double, maxBytes: Int) -> Bool {
        guard seconds.isFinite, seconds > 0 else { return true }
        return Double(bps) * seconds / 8 <= Double(maxBytes)
    }

    /// Build 78's size-scaled wall-clock budget for the WHOLE compression (≈0.55 s per MB
    /// of source — 4K HDR decode is per-sample work), now also scaled by DURATION: at least
    /// half the take's length (a transcode at ≥2× realtime), and a ceiling that grows with
    /// the take (max(420 s, duration), ≤ 30 min) — the flat 420 s ceiling guillotined long
    /// transcodes that were on track. Takes up to 7 min keep exactly the old numbers.
    static func compressionBudget(bytes: Int, seconds: Double) -> TimeInterval {
        let dur = (seconds.isFinite && seconds > 0) ? seconds : 0
        let bySize = Double(max(0, bytes)) / 1_000_000 * 0.55
        let byDuration = dur * 0.5
        let ceiling = max(420, min(1800, dur))
        return min(ceiling, max(240, bySize, byDuration))
    }

    /// How much of `budget` the HEVC transcode may spend. It keeps 40% in reserve for the
    /// export-preset fallback only when a preset could actually fit the cap; for long takes
    /// (no preset can — 4.9 Mbps) the transcode gets the whole, duration-scaled budget.
    static func transcodeDeadline(budget: TimeInterval, seconds: Double, maxBytes: Int) -> TimeInterval {
        presetMayFit(bps: preset540Bps, seconds: seconds, maxBytes: maxBytes) ? budget * 0.6 : budget
    }
}
