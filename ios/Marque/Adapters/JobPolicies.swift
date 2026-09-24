import Foundation

// Pure (Foundation-only) decisions for the job poll / retry / re-render paths of the
// Library. AppStore does the I/O; these decide — so ios/LogicTests can pin the behavior.

/// LV-4 (2026-09-24) — how long the client keeps polling a job before calling it.
///
/// The ceiling was a flat 20 min of wall clock. The server legitimately spends longer on a
/// long take (transcribe ≈ realtime + edit + a render budget that scales with frames), so a
/// 10-minute take was marked `edit_timeout` while its render was still on track — and since
/// failed clips were never re-polled, a render that landed at minute 22 never appeared.
enum JobPollBudget {
    static let baseCeiling: TimeInterval = 20 * 60
    static let maxCeiling: TimeInterval = 90 * 60
    /// Seconds of extra patience per second of source footage.
    static let perSourceSecond: TimeInterval = 3

    /// 20 min + 3× the source duration, capped at 90 min. Unknown duration → 20 min.
    static func ceiling(sourceSeconds: Double?) -> TimeInterval {
        guard let s = sourceSeconds, s.isFinite, s > 0 else { return baseCeiling }
        return min(maxCeiling, baseCeiling + perSourceSecond * s)
    }

    /// 5 s while fresh, 10 s once the job has been going 5 min (unchanged cadence).
    static func interval(elapsed: TimeInterval) -> TimeInterval {
        elapsed < 300 ? 5 : 10
    }

    /// Failure codes that are the CLIENT's verdict, not the server's: the server may well
    /// have finished (or still be working), so a failed clip carrying one of these — and a
    /// job id — is re-polled on foreground / Library appear instead of being written off.
    static let clientVerdictErrors: Set<String> = ["edit_timeout"]

    static func shouldRepollFailed(lastError: String?, hasJobId: Bool) -> Bool {
        hasJobId && lastError.map { clientVerdictErrors.contains($0) } == true
    }
}

/// LV-4 / ED-11 — what a POST /v1/clips/{id}/retry answer means for the client.
enum RetryJobPolicy {
    enum Outcome: Equatable {
        case restarted      // 200 — the server re-runs it: poll
        case stillRunning   // 409 — the ORIGINAL run is still going: keep polling, never re-upload
        case jobGone        // the server has no such job any more: re-upload from the local take
    }

    static func classify(status: Int) -> Outcome {
        switch status {
        case 200: return .restarted
        case 409: return .stillRunning
        default: return .jobGone
        }
    }
}

/// LV-6 (2026-09-24) — the store-owned watcher for a tweak / manual-edit / retheme
/// re-render (AppStore.watchTweakRender).
///
/// It gave up after a flat 120 × 5 s (~10 min) — short of a long take's re-render — and on
/// a 404/410 (the edit session is gone, so the render can never land) it simply stopped,
/// leaving the Library card on "rendering" forever.
enum TweakWatchPolicy {
    static let baseCeiling: TimeInterval = 10 * 60

    /// 10 min + 3× the clip's source length, capped like the job polls (90 min).
    static func ceiling(sourceSeconds: Double?) -> TimeInterval {
        guard let s = sourceSeconds, s.isFinite, s > 0 else { return baseCeiling }
        return min(JobPollBudget.maxCeiling, baseCeiling + JobPollBudget.perSourceSecond * s)
    }

    enum Step: Equatable {
        case keepWaiting                  // still rendering, or no readable answer this tick
        case landed(renderFailed: Bool)   // "ready" — the new cut, or the previous one restored
        case renderFailed                 // "failed" — the server kept the previous cut
        case sessionGone                  // 404/410 — restore the previous cut; nothing will land
    }

    /// One poll tick. `clipStatus` is MY clip's status in the job response (nil when the
    /// response didn't include it or didn't parse).
    static func step(httpStatus: Int, clipStatus: String?, lastRenderFailed: Bool) -> Step {
        if httpStatus == 404 || httpStatus == 410 { return .sessionGone }
        switch clipStatus {
        case "ready": return .landed(renderFailed: lastRenderFailed)
        case "failed": return .renderFailed
        default: return .keepWaiting
        }
    }
}
