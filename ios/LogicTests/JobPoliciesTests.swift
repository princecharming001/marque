import Foundation

// LV-4: the job poll ceiling scales with the source take; client-side edit_timeout verdicts
// are re-polled; a /retry 409 ("still running") keeps polling instead of re-uploading.
func runJobPoliciesTests() {
    suite("JobPollBudget — LV-4 duration-scaled ceiling")
    expectClose(JobPollBudget.ceiling(sourceSeconds: nil), 20 * 60, "unknown source → 20 min (old ceiling)")
    expectClose(JobPollBudget.ceiling(sourceSeconds: 0), 20 * 60, "zero-length probe → 20 min")
    expectClose(JobPollBudget.ceiling(sourceSeconds: .nan), 20 * 60, "NaN probe → 20 min")
    expectClose(JobPollBudget.ceiling(sourceSeconds: 60), 23 * 60, "1-min take → 23 min")
    expectClose(JobPollBudget.ceiling(sourceSeconds: 600), 50 * 60, "10-min take → 50 min")
    expectClose(JobPollBudget.ceiling(sourceSeconds: 900), 65 * 60, "15-min take → 65 min")
    expectClose(JobPollBudget.ceiling(sourceSeconds: 7200), 90 * 60, "2-hour take → capped at 90 min")
    expect(JobPollBudget.ceiling(sourceSeconds: 600) > 22 * 60,
           "a long render landing at minute 22 is still inside the ceiling (the audit's case)")
    expectClose(JobPollBudget.interval(elapsed: 10), 5, "5 s cadence while fresh")
    expectClose(JobPollBudget.interval(elapsed: 301), 10, "10 s cadence after 5 min")

    suite("JobPollBudget — LV-4 which failed cards are re-polled")
    expect(JobPollBudget.shouldRepollFailed(lastError: "edit_timeout", hasJobId: true),
           "edit_timeout with a job → re-poll (client verdict, not the server's)")
    expect(!JobPollBudget.shouldRepollFailed(lastError: "edit_timeout", hasJobId: false),
           "edit_timeout without a job → nothing to poll")
    expect(!JobPollBudget.shouldRepollFailed(lastError: "render_fatal", hasJobId: true),
           "a server verdict (render_fatal) is not re-polled")
    expect(!JobPollBudget.shouldRepollFailed(lastError: "job_expired", hasJobId: true),
           "job_expired is final")
    expect(!JobPollBudget.shouldRepollFailed(lastError: nil, hasJobId: true), "no code → not re-polled")

    suite("RetryJobPolicy — LV-4 /retry answers")
    expectEqual(RetryJobPolicy.classify(status: 200), .restarted, "200 → restarted, poll")
    expectEqual(RetryJobPolicy.classify(status: 409), .stillRunning,
                "409 → the original run is still going: keep polling, never re-upload")
    expectEqual(RetryJobPolicy.classify(status: 404), .jobGone, "404 → job gone: re-upload from the take")
    expectEqual(RetryJobPolicy.classify(status: 410), .jobGone, "410 → job gone: re-upload from the take")
}
