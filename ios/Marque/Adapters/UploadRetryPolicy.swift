import Foundation

/// LV-1 (2026-09-24) — the object store's REAL per-object size limit, which the mint's
/// advertised cap has not told the truth about. Supabase Storage enforces a PROJECT-level
/// limit of 52,428,800 B (50 MiB) that no API exposes; measured live: a 52,000,000 B PUT
/// → 200, a 52,428,801 B PUT → HTTP **400** whose JSON body says statusCode "413" /
/// EntityTooLarge. Meanwhile /v1/uploads/mint advertised max_upload_bytes = 150,000,000,
/// so every take the compressor left between 50 MiB and the advertised cap was PUT whole,
/// refused, and — 400 being fail-fast — landed on a dead card. The backend is being made
/// honest, but old servers and future env drift can lie again, so the client treats a
/// size refusal as recoverable (see UploadRetryPolicy.decide) instead of trusting the cap.
enum StorageObjectLimit {
    /// The measured storage limit. A body at or under it is never refused for SIZE, so a
    /// 400 on such a body is a different fault (bad/used token, …) and stays fail-fast.
    static let knownBytes: Int64 = 52_428_800
    /// What a "too large for storage" recompress aims for: under the limit with headroom
    /// for the container overhead the bitrate plan doesn't see.
    static let recompressTargetBytes = 50_000_000
    /// Cap used when the mint response omits max_upload_bytes (unchanged from build 78).
    static let defaultCapBytes = 48_000_000
}

/// LV-1 (a) — "don't trust an advertised cap above the storage limit once storage has
/// proven it lower." After a size refusal the client remembers it for a while, so the
/// NEXT take compresses under the real limit up front instead of paying another full
/// transfer to be refused. Time-boxed (not permanent) so an owner who genuinely raises
/// the storage limit gets the higher cap back without an app update.
enum StorageSizeMemory {
    private static let key = "marque.upload.storageSizeRefusedAt"
    static let ttl: TimeInterval = 7 * 24 * 3600

    static var lastRefusalEpoch: Double? {
        let v = UserDefaults.standard.double(forKey: key)
        return v > 0 ? v : nil
    }

    static func noteRefusal(now: Double = Date().timeIntervalSince1970) {
        UserDefaults.standard.set(now, forKey: key)
    }
}

// Build 49 — one place that decides "retry, re-mint, park for network, or fail fast."
// Replaces the old ad-hoc 3×/2s/4s loop in LiveClipEngine.uploadFootage. Backoff is
// FULL JITTER (AWS Architecture Blog: `sleep = random(0, min(cap, base·2^attempt))`),
// which minimizes total calls under contention vs plain exponential. Retry decisions
// follow a status-code table; a 403/expiry means the signed URL died and the caller must
// re-mint before the next attempt.
enum UploadRetryPolicy {
    static let maxAttemptsPerSession = 6     // within one foreground run
    static let maxLifetimeAttempts = 10      // journal-counted across launches
    /// LV-1: size-driven recompressions per upload session. One is enough by construction
    /// (the smaller body is at most `recompressTargetBytes`, under the limit, so a second
    /// refusal can't be about size) — the bound is belt-and-braces against a loop.
    static let maxSizeRecompressions = 1
    private static let baseDelay: Double = 1.0
    private static let capDelay: Double = 60.0

    /// What the caller should do after a failed attempt.
    enum Decision: Equatable {
        case retry(after: TimeInterval)   // transient — back off and try again
        case remintThenRetry              // signed URL is dead (403/expired) — mint a fresh one
        case waitForNetwork               // parked; resume when NWPathMonitor reports satisfied
        case restartStalled(after: TimeInterval)  // OUR watchdog cancelled it — restart, don't bill an attempt
        case recompressSmaller(targetBytes: Int)  // LV-1: storage refused the body SIZE — shrink it, re-mint, retry
        case fail                         // permanent — surface a retryable failed card
    }

    /// LV-1: did storage refuse this body for its SIZE? Supabase answers an over-limit PUT
    /// with HTTP 400 (JSON statusCode "413"); a proxy may answer a real 413. Either status
    /// only counts when the body is over the measured limit — the only 400 a smaller body
    /// can fix. A 400 on a body under the limit (used/bad token, …) stays fail-fast.
    static func storageRefusedSize(status: Int, bodyBytes: Int64) -> Bool {
        (status == 400 || status == 413) && bodyBytes > StorageObjectLimit.knownBytes
    }

    /// LV-1: the size to recompress to after a size refusal — min(advertised cap, 50 MB).
    /// nil when that wouldn't actually be smaller than the body that was refused.
    static func recompressTarget(capBytes: Int, refusedBodyBytes: Int64) -> Int? {
        let cap = capBytes > 0 ? capBytes : StorageObjectLimit.recompressTargetBytes
        let target = min(cap, StorageObjectLimit.recompressTargetBytes)
        return Int64(target) < refusedBodyBytes ? target : nil
    }

    /// LV-1 (a): the cap one upload compresses against. The mint's advertised cap, except
    /// that within `StorageSizeMemory.ttl` of storage refusing a body for size it is
    /// clamped to the recompress target — the advertised number was just proven wrong.
    static func effectiveCap(mintCap: Int?, lastSizeRefusalEpoch: Double?, now: Double) -> Int {
        let advertised = mintCap.flatMap { $0 > 0 ? $0 : nil } ?? StorageObjectLimit.defaultCapBytes
        guard let at = lastSizeRefusalEpoch, now >= at, now - at < StorageSizeMemory.ttl else {
            return advertised
        }
        return min(advertised, StorageObjectLimit.recompressTargetBytes)
    }

    /// Build 53 (A5) lifetime ceiling: has an upload whose journal already counts `prior`
    /// attempts used up its budget by this session's `sessionAttempt` (0-based)? LV-5: the
    /// journal count is reset by a USER retry (UploadJournalEntry.resetForUserRetry), so
    /// only the automatic resume path can exhaust it for good.
    static func lifetimeExhausted(prior: Int, sessionAttempt: Int) -> Bool {
        max(0, prior) + sessionAttempt >= maxLifetimeAttempts
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
    /// `lifetimeAttempt` is the journal-persisted count across launches — build 53 (A5) makes
    /// `maxLifetimeAttempts` actually bite (it was declared-but-dead), so a clip that exhausts
    /// its per-session budget every cold start can't retry forever via the reconcile sweep.
    /// `watchdogStalled` (build 78) marks a failure the APP caused: BackgroundUploader's
    /// foreground stall watchdog cancelled a transfer whose bytes had gone quiet.
    /// LV-1: `bodyBytes` (size of the body that was just refused), `capBytes` (the cap it
    /// was compressed against) and `sizeRecompressions` (already spent this session) let a
    /// storage SIZE refusal become `.recompressSmaller` instead of a dead card.
    static func decide(status: Int, attempt: Int, retryAfter: TimeInterval? = nil,
                       networkSatisfied: Bool = true, nsError: Error? = nil,
                       lifetimeAttempt: Int = 0, watchdogStalled: Bool = false,
                       bodyBytes: Int64 = 0, capBytes: Int = 0,
                       sizeRecompressions: Int = 0) -> Decision {
        if attempt + 1 >= maxAttemptsPerSession { return .fail }
        if lifetimeAttempt + 1 >= maxLifetimeAttempts { return .fail }

        // Build 78 — a watchdog cancel is OUR abort of a transfer that may be perfectly
        // healthy (a background-session body file that nsurlsessiond is still copying emits
        // no didSendBodyData for tens of seconds on a large take). It surfaces as status 0 +
        // NSURLErrorCancelled, which fell through to the unconditional "one more jittered
        // try" below and BILLED an attempt every time — six self-inflicted stalls and the
        // upload was dead with no transport failure anywhere in the log, each one restarting
        // a several-hundred-MB PUT from byte 0. Restart it, but tell the caller not to spend
        // budget on it; the caller bounds stall restarts separately, exactly like network
        // parks. Ordering matters: this sits AFTER the exhaustion checks (a genuinely
        // exhausted upload still fails) and BEFORE status classification, and it only fires
        // on status 0 so a real HTTP verdict — including the 400/404/409/413 fail-fast set —
        // is always classified on its own merits below.
        if watchdogStalled, status == 0 {
            if !networkSatisfied { return .waitForNetwork }
            return .restartStalled(after: backoff(attempt: min(attempt, 2)))
        }

        // LV-1: storage refused the body for its SIZE (Supabase: 400 + {"statusCode":"413"}
        // on anything over its 50 MiB project limit, whatever the mint advertised). That is
        // the one 400/413 a smaller body fixes, so it must not fall into the fail-fast set
        // below: recompress under the real limit and retry. Bounded per session.
        if storageRefusedSize(status: status, bodyBytes: bodyBytes),
           sizeRecompressions < maxSizeRecompressions,
           let target = recompressTarget(capBytes: capBytes, refusedBodyBytes: bodyBytes) {
            return .recompressSmaller(targetBytes: target)
        }

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
