import Foundation

// ED-11: only a server that says the job is GONE (404/410) may trigger the re-upload + new
// job path; offline, timeouts and 5xx keep the clip (and its server edit history) as-is.
func runRetryJobPolicyTests() {
    suite("RetryJobPolicy — ED-11 no re-upload on transport errors")
    expectEqual(RetryJobPolicy.classify(status: 0), .unreachable,
                "offline / timeout (status 0) → keep the clip, never re-upload")
    expectEqual(RetryJobPolicy.classify(status: 503), .unreachable, "503 → unreachable")
    expectEqual(RetryJobPolicy.classify(status: 500), .unreachable, "500 → unreachable (a re-upload can't fix a server error)")
    expectEqual(RetryJobPolicy.classify(status: 502), .unreachable, "502 → unreachable")
    expectEqual(RetryJobPolicy.classify(status: 429), .unreachable, "429 → unreachable")
    expectEqual(RetryJobPolicy.classify(status: 404), .jobGone, "404 → the only re-upload trigger…")
    expectEqual(RetryJobPolicy.classify(status: 410), .jobGone, "…along with 410")
    expectEqual(RetryJobPolicy.classify(status: 409), .stillRunning, "409 still keeps polling")
    expectEqual(RetryJobPolicy.classify(status: 200), .restarted, "200 still restarts")

    suite("RetryJobPolicy — ED-11 an unreachable retry is re-polled later")
    expect(JobPollBudget.shouldRepollFailed(lastError: "retry_unreachable", hasJobId: true),
           "retry_unreachable with a job → re-polled on foreground (server state unknown)")
    expect(!JobPollBudget.shouldRepollFailed(lastError: "retry_unreachable", hasJobId: false),
           "…but only while it has a job")
}
