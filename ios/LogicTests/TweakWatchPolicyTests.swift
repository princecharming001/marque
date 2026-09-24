import Foundation

// LV-6: the tweak re-render watcher's budget scales with the clip, and a gone session
// (404/410) restores the previous cut instead of leaving the card on "rendering".
func runTweakWatchPolicyTests() {
    suite("TweakWatchPolicy — LV-6 budget")
    expectClose(TweakWatchPolicy.ceiling(sourceSeconds: nil), 10 * 60, "unknown length → 10 min (old budget)")
    expectClose(TweakWatchPolicy.ceiling(sourceSeconds: 45), 10 * 60 + 135, "45 s clip → 12¼ min")
    expectClose(TweakWatchPolicy.ceiling(sourceSeconds: 600), 40 * 60, "10-min take → 40 min")
    expectClose(TweakWatchPolicy.ceiling(sourceSeconds: 3600), 90 * 60, "1-hour take → capped at 90 min")
    expect(TweakWatchPolicy.ceiling(sourceSeconds: 600) > 120 * 5,
           "a 10-min take gets more than the old 120 × 5 s")

    suite("TweakWatchPolicy — LV-6 per-tick verdict")
    expectEqual(TweakWatchPolicy.step(httpStatus: 404, clipStatus: nil, lastRenderFailed: false),
                .sessionGone, "404 → session gone (restore the previous cut)")
    expectEqual(TweakWatchPolicy.step(httpStatus: 410, clipStatus: "rendering", lastRenderFailed: false),
                .sessionGone, "410 → session gone even if a stale body says rendering")
    expectEqual(TweakWatchPolicy.step(httpStatus: 200, clipStatus: "rendering", lastRenderFailed: false),
                .keepWaiting, "rendering → keep waiting")
    expectEqual(TweakWatchPolicy.step(httpStatus: 0, clipStatus: nil, lastRenderFailed: false),
                .keepWaiting, "transport failure → keep waiting (not gone)")
    expectEqual(TweakWatchPolicy.step(httpStatus: 503, clipStatus: nil, lastRenderFailed: false),
                .keepWaiting, "503 → keep waiting")
    expectEqual(TweakWatchPolicy.step(httpStatus: 200, clipStatus: "ready", lastRenderFailed: false),
                .landed(renderFailed: false), "ready → the new cut landed")
    expectEqual(TweakWatchPolicy.step(httpStatus: 200, clipStatus: "ready", lastRenderFailed: true),
                .landed(renderFailed: true), "ready + last_render_failed → previous cut restored")
    expectEqual(TweakWatchPolicy.step(httpStatus: 200, clipStatus: "failed", lastRenderFailed: false),
                .renderFailed, "failed → render failed")
}
