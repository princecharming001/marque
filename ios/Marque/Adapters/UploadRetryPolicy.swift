import Foundation

// Build 49 — one place that decides "retry, re-mint, park for network, or fail fast."
// Replaces the old ad-hoc 3×/2s/4s loop in LiveClipEngine.uploadFootage. Backoff is
// FULL JITTER (AWS Architecture Blog: `sleep = random(0, min(cap, base·2^attempt))`),
// which minimizes total calls under contention vs plain exponential. Retry decisions
// follow a status-code table; a 403/expiry means the signed URL died and the caller must
// re-mint before the next attempt.
enum UploadRetryPolicy {
    static let maxAttemptsPerSession = 6     // within one foreground run
    static let maxLifetimeAttempts = 10      // journal-counted across launches
    private static let baseDelay: Double = 1.0
    private static let capDelay: Double = 60.0

    /// What the caller should do after a failed attempt.
    enum Decision: Equatable {
        case retry(after: TimeInterval)   // transient — back off and try again
        case remintThenRetry              // signed URL is dead (403/expired) — mint a fresh one
        case waitForNetwork               // parked; resume when NWPathMonitor reports satisfied
        case fail                         // permanent — surface a retryable failed card
    }

    /// Full-jitter backoff for `attempt` (0-based). Honors a server `Retry-After` when
    /// present, clamped to [computed, computed+30] so a hostile header can't park us forever.
    static func backoff(attempt: Int, retryAfter: TimeInterval? = nil) -> TimeInterval {
        let ceiling = min(capDelay, baseDelay * pow(2, Double(attempt)))
        let jittered = Double.random(in: 0...ceiling)
        guard let ra = retryAfter, ra > 0 else { return jittered }
        return min(max(ra, jittered), jittered + 30)
    }

    /// Classify an HTTP status. `status == 0` means a transport-level error (see `nsError`).
    static func decide(status: Int, attempt: Int, retryAfter: TimeInterval? = nil,
                       networkSatisfied: Bool = true, nsError: Error? = nil) -> Decision {
        if attempt + 1 >= maxAttemptsPerSession { return .fail }

        // Signed-URL death — the object store rejects the token; a fresh mint is required.
        // 403 = expired/invalid token; 409/400 "already exists" is handled by the caller's
        // HEAD-verified success path BEFORE this is consulted, so here it's a hard fail.
        if status == 403 { return .remintThenRetry }
        if status == 400 || status == 404 || status == 409 || status == 413 { return .fail }
        if (200..<300).contains(status) { return .retry(after: 0) }   // caller shouldn't reach here on 2xx

        // Transport failure: distinguish "no network" (park) from "flaky" (backoff).
        if status == 0 {
            if !networkSatisfied { return .waitForNetwork }
            if let ns = nsError as NSError?, Self.isRetryableTransport(ns) {
                return .retry(after: backoff(attempt: attempt, retryAfter: retryAfter))
            }
            // Unknown transport error but we're online — one more jittered try.
            return .retry(after: backoff(attempt: attempt, retryAfter: retryAfter))
        }

        // Retryable server / rate-limit classes.
        if status == 408 || status == 425 || status == 429 || (500..<600).contains(status) {
            return .retry(after: backoff(attempt: attempt, retryAfter: retryAfter))
        }
        return .fail
    }

    /// URLError codes worth retrying (timeouts, drops, DNS) vs. fail-fast (bad URL, cancelled).
    static func isRetryableTransport(_ error: NSError) -> Bool {
        guard error.domain == NSURLErrorDomain else { return true }
        switch error.code {
        case NSURLErrorTimedOut, NSURLErrorNetworkConnectionLost, NSURLErrorNotConnectedToInternet,
             NSURLErrorCannotFindHost, NSURLErrorCannotConnectToHost, NSURLErrorDNSLookupFailed,
             NSURLErrorResourceUnavailable, NSURLErrorRequestBodyStreamExhausted,
             NSURLErrorInternationalRoamingOff, NSURLErrorCallIsActive, NSURLErrorDataNotAllowed:
            return true
        case NSURLErrorCancelled, NSURLErrorBadURL, NSURLErrorUnsupportedURL:
            return false
        default:
            return true
        }
    }
}
