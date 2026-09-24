import Foundation

// LV-5: the journal's lifetime attempt count bounds AUTOMATIC retries only — a creator's
// "Try again" must start a fresh budget, or a take that burned 10 attempts is dead forever.
func runUploadJournalTests() {
    suite("UploadJournal — LV-5 user retry resets the lifetime budget")
    var entry = UploadJournalEntry(uploadId: "u1", placeholderId: "p1", sourcePath: "media/take.mov",
                                   contentType: "video/quicktime")
    entry.attemptCount = UploadRetryPolicy.maxLifetimeAttempts
    entry.state = .failedRetryable
    entry.lastErrorCode = "http_400"
    entry.bytesConfirmed = 12_345

    // Before the fix: the first loop iteration of any retry hit the ceiling immediately.
    expect(UploadRetryPolicy.lifetimeExhausted(prior: entry.attemptCount, sessionAttempt: 0),
           "10 lifetime attempts → exhausted before the first PUT (the dead-take bug)")

    var automatic = entry                       // the relaunch/foreground sweep keeps the cap
    automatic.state = .queued; automatic.lastErrorCode = nil
    expect(UploadRetryPolicy.lifetimeExhausted(prior: automatic.attemptCount, sessionAttempt: 0),
           "automatic resume: still capped")

    var user = entry
    user.resetForUserRetry()
    expectEqual(user.attemptCount, 0, "user retry: attempt count back to 0")
    expectEqual(user.state, .queued, "user retry: queued")
    expectEqual(user.lastErrorCode, nil, "user retry: old error cleared")
    expectEqual(user.bytesConfirmed, 0, "user retry: no stale byte progress")
    expectEqual(user.payload, entry.payload, "user retry: analyze payload (edit settings) kept")
    expectEqual(user.sourcePath, entry.sourcePath, "user retry: same take")
    expect(!UploadRetryPolicy.lifetimeExhausted(prior: user.attemptCount, sessionAttempt: 0),
           "user retry: the upload loop may run again")
    expect(UploadRetryPolicy.decide(status: 503, attempt: 0, lifetimeAttempt: user.attemptCount)
               != .fail,
           "user retry: a transient 503 is retried again, not failed on the lifetime cap")
    expectEqual(UploadRetryPolicy.decide(status: 503, attempt: 0, lifetimeAttempt: 9), .fail,
                "without the reset the same 503 fails on the lifetime cap")

    suite("UploadJournal — LV-5 lifetime ceiling arithmetic")
    expect(!UploadRetryPolicy.lifetimeExhausted(prior: 0, sessionAttempt: 5), "0 + 5 < 10")
    expect(UploadRetryPolicy.lifetimeExhausted(prior: 4, sessionAttempt: 6), "4 + 6 = 10 → exhausted")
    expect(!UploadRetryPolicy.lifetimeExhausted(prior: -3, sessionAttempt: 0), "a corrupt negative count is floored at 0")
}
