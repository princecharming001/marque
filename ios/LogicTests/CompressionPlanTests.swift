import Foundation

// LV-2: one bitrate-targeted plan for every duration. Reference outputs were measured
// offline by encoding real footage with exactly this plan (HEVC via AVAssetWriter):
//   60s 26.7MB@1080p · 180s 42.3MB@720p · 300s 42.6MB@720p · 600s 43.3MB@540p · 900s 44.0MB@540p
// The plan's nominal size must fit the cap and sit at/above the measured output (encoders
// undershoot the average bitrate slightly; they must not be planned to overshoot it).
func runCompressionPlanTests() {
    suite("CompressionPlanner — LV-2 reference durations at 50,000,000 B")
    let cap = 50_000_000
    let reference: [(seconds: Double, measuredMB: Double, edge: Int, w: Int, h: Int)] = [
        (60, 26.7, 1080, 1080, 1920),
        (180, 42.3, 720, 720, 1280),
        (300, 42.6, 720, 720, 1280),
        (600, 43.3, 540, 540, 960),
        (900, 44.0, 540, 540, 960),
    ]
    for r in reference {
        guard let p = CompressionPlanner.plan(seconds: r.seconds, maxBytes: cap,
                                              sourceWidth: 1080, sourceHeight: 1920) else {
            expect(false, "\(Int(r.seconds))s: a plan exists"); continue
        }
        expect(p.nominalBytes <= cap, "\(Int(r.seconds))s: nominal \(p.nominalBytes) fits the cap")
        expect(Double(p.nominalBytes) >= r.measuredMB * 1_000_000,
               "\(Int(r.seconds))s: nominal ≥ measured \(r.measuredMB)MB")
        expectEqual(p.shortEdge, r.edge, "\(Int(r.seconds))s: \(r.edge)p rung")
        expectEqual(p.width, r.w, "\(Int(r.seconds))s: width \(r.w) (portrait kept)")
        expectEqual(p.height, r.h, "\(Int(r.seconds))s: height \(r.h)")
    }

    suite("CompressionPlanner — LV-2 bitrate formula")
    let p60 = CompressionPlanner.plan(seconds: 60, maxBytes: cap, sourceWidth: 1920, sourceHeight: 1080)
    expectEqual(p60?.videoBps, 3_800_000, "60s: capped at the ≤90s tier (3.8 Mbps)")
    expectEqual(p60?.audioBps, 96_000, "60s: 96k audio")
    let p120 = CompressionPlanner.plan(seconds: 120, maxBytes: cap, sourceWidth: 1920, sourceHeight: 1080)
    expectEqual(p120?.videoBps, 2_600_000, "120s: capped at the 90–150s tier (2.6 Mbps)")
    let p180 = CompressionPlanner.plan(seconds: 180, maxBytes: cap, sourceWidth: 1920, sourceHeight: 1080)
    expectEqual(p180?.videoBps, 1_985_564, "180s: (cap·8/dur − 64k)·0.92")
    expectEqual(p180?.audioBps, 64_000, "180s: 64k audio past 150s")
    expectEqual(p180.map { [$0.width, $0.height] }, [1280, 720], "180s landscape → 1280×720")
    let p150 = CompressionPlanner.plan(seconds: 150, maxBytes: cap, sourceWidth: 1080, sourceHeight: 1920)
    expectEqual(p150?.audioBps, 96_000, "150s: still 96k audio")
    expectEqual(p150?.shortEdge, 1080, "150s: 2.37 Mbps affords 1080p")

    suite("CompressionPlanner — LV-2 every duration fits, too-long is refused")
    var allFit = true
    var d = 5.0
    while d <= 1400 {
        if let p = CompressionPlanner.plan(seconds: d, maxBytes: cap, sourceWidth: 1080, sourceHeight: 1920) {
            if p.nominalBytes > cap { allFit = false; print("    overshoot at \(d)s: \(p.nominalBytes)") }
        } else { allFit = false; print("    no plan at \(d)s") }
        d += 5
    }
    expect(allFit, "5s…1400s (5s steps): every plan's nominal size ≤ cap")
    expect(CompressionPlanner.plan(seconds: 1450, maxBytes: cap, sourceWidth: 1080, sourceHeight: 1920) == nil,
           "1450s at 50MB: under the 200 kbps floor → nil (too large, trim it)")
    expect(CompressionPlanner.plan(seconds: 3600, maxBytes: cap, sourceWidth: 1080, sourceHeight: 1920) == nil,
           "1h at 50MB → nil")
    expect(CompressionPlanner.plan(seconds: .nan, maxBytes: cap, sourceWidth: 1080, sourceHeight: 1920) == nil,
           "unknown duration → nil (preset fallback decides)")
    expect(CompressionPlanner.plan(seconds: 60, maxBytes: cap, sourceWidth: 0, sourceHeight: 0) == nil,
           "no video dimensions → nil")
    let bigCap = CompressionPlanner.plan(seconds: 600, maxBytes: 150_000_000, sourceWidth: 1080, sourceHeight: 1920)
    expectEqual(bigCap?.videoBps, 1_781_120, "600s at 150MB: (2,000,000 − 64,000)·0.92")
    expectEqual(bigCap?.shortEdge, 720, "600s at 150MB → 720p")

    suite("CompressionPlanner — LV-2 dimensions (no upscale, even, aspect kept)")
    expect(CompressionPlanner.dimensions(width: 3840, height: 2160, shortEdge: 1080) == (1920, 1080),
           "4K landscape → 1920×1080")
    expect(CompressionPlanner.dimensions(width: 1080, height: 1920, shortEdge: 540) == (540, 960),
           "1080×1920 → 540×960")
    expect(CompressionPlanner.dimensions(width: 720, height: 1280, shortEdge: 1080) == (720, 1280),
           "720p source at the 1080 rung → unchanged (never upscaled)")
    expect(CompressionPlanner.dimensions(width: 1080, height: 1350, shortEdge: 720) == (720, 900),
           "4:5 → 720×900")
    let odd = CompressionPlanner.dimensions(width: 1081, height: 1921, shortEdge: 1080)
    expect(odd.0 % 2 == 0 && odd.1 % 2 == 0, "odd source → even output \(odd)")
    expect(CompressionPlanner.dimensions(width: -1920, height: -1080, shortEdge: 720) == (1280, 720),
           "negative natural size (transform quirk) → absolute")

    suite("CompressionPlanner — LV-2 overshoot replan")
    if let p = CompressionPlanner.plan(seconds: 300, maxBytes: cap, sourceWidth: 1080, sourceHeight: 1920) {
        let r = CompressionPlanner.replan(p, outputBytes: 55_000_000, maxBytes: cap,
                                          sourceWidth: 1080, sourceHeight: 1920)
        expect(r != nil && r!.videoBps < p.videoBps, "300s overshoot 55MB → lower bitrate")
        expect(r.map { Double($0.nominalBytes) <= Double(cap) * 0.92 } ?? false,
               "replan targets the cap with the margin again")
        expect(r.map { $0.shortEdge <= p.shortEdge } ?? false, "replan never raises resolution")
        expect(CompressionPlanner.replan(p, outputBytes: 49_000_000, maxBytes: cap,
                                         sourceWidth: 1080, sourceHeight: 1920) == nil,
               "no replan when the output already fits")
    }
    if let p = CompressionPlanner.plan(seconds: 900, maxBytes: cap, sourceWidth: 1080, sourceHeight: 1920) {
        expect(CompressionPlanner.replan(p, outputBytes: 120_000_000, maxBytes: cap,
                                         sourceWidth: 1080, sourceHeight: 1920) == nil,
               "replan below the bitrate floor → nil")
    }

    suite("CompressionPlanner — LV-2 budget + transcode deadline")
    expectClose(CompressionPlanner.compressionBudget(bytes: 90_000_000, seconds: 60), 240,
                "60s/90MB → 240s floor (unchanged)")
    expectClose(CompressionPlanner.compressionBudget(bytes: 450_000_000, seconds: 300), 247.5,
                "5-min in-app take → 247.5s (unchanged size scaling)")
    expectClose(CompressionPlanner.compressionBudget(bytes: 500_000_000, seconds: 90), 275,
                "90s 4K HDR import → 275s (unchanged)")
    expectClose(CompressionPlanner.compressionBudget(bytes: 3_000_000_000, seconds: 60), 420,
                "pathological short import → 420s ceiling (unchanged)")
    expectClose(CompressionPlanner.compressionBudget(bytes: 900_000_000, seconds: 600), 495,
                "10-min in-app take → 495s (was clamped to 420)")
    expectClose(CompressionPlanner.compressionBudget(bytes: 1_350_000_000, seconds: 900), 742.5,
                "15-min in-app take → 742.5s")
    expectClose(CompressionPlanner.compressionBudget(bytes: 450_000_000, seconds: 900), 450,
                "15-min low-bitrate import → duration floor 450s")
    expectClose(CompressionPlanner.compressionBudget(bytes: 90_000_000, seconds: .nan), 240,
                "unknown duration → size budget")
    expectClose(CompressionPlanner.transcodeDeadline(budget: 240, seconds: 60, maxBytes: cap), 144,
                "60s: a 540p preset could fit → transcode keeps 40% reserve")
    expectClose(CompressionPlanner.transcodeDeadline(budget: 742.5, seconds: 900, maxBytes: cap), 742.5,
                "900s: no preset can fit → the transcode gets the whole budget")
    expect(!CompressionPlanner.presetMayFit(bps: CompressionPlanner.preset540Bps, seconds: 300, maxBytes: cap),
           "5-min at the measured 4.9 Mbps preset (184MB) can't fit 50MB")
    expect(CompressionPlanner.presetMayFit(bps: CompressionPlanner.preset540Bps, seconds: .nan, maxBytes: cap),
           "unknown duration → the preset may try")
}
