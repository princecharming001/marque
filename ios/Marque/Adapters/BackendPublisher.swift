import Foundation

// Server-side publisher: sends all publish requests to the backend /v1/publish, which holds
// the Post for Me key and is the single place publish decisions are made. No vendor key ships
// in the iOS app (three-trust-plane model).
//
// C-02: returns a truthful PublishOutcome. The old code fell back to MockPublisher (which
// returned true) on ANY transport failure — so a dropped connection or an unlinked account
// showed the creator "Posted" when nothing was posted. That lie is deleted: transport
// failure → .queuedTransportFailure (retryable), a mock/no-accounts response →
// .savedLocalNoAccounts, an upstream reject → .failed.
struct BackendPublisher: Publishing {
    private let client = BackendClient.shared

    func schedule(_ post: ScheduledPost, accountIds: [String]) async -> PublishOutcome {
        let platforms = post.platforms.map { $0 == .instagram ? "instagram" : "tiktok" }
        let iso = ISO8601DateFormatter().string(from: post.date)
        var body: [String: Any] = [
            "caption": post.caption,
            "platforms": platforms,              // legacy hint (backend uses account ids)
            "social_account_ids": accountIds,    // Post for Me spc_ids — the real targets
            "schedule_date": iso,
        ]
        if let media = post.mediaURL, media.hasPrefix("http") {
            body["media_url"] = media
        }
        let (data, status) = await client.postWithStatus("/v1/publish", body)
        guard let data, status == 200,
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            // Couldn't reach the backend at all — honest, retryable, never "Posted".
            return .queuedTransportFailure
        }
        // `posted` is the honest field (C-01); older servers without it → infer from ok+mode.
        let posted = (json["posted"] as? Bool)
            ?? ((json["ok"] as? Bool == true) && (json["mode"] as? String) == "live")
        if posted { return .posted }
        let reason = json["reason"] as? String
        switch reason {
        case "no_key", "no_accounts", nil: return .savedLocalNoAccounts
        default:                           return .failed(reason ?? "unknown")
        }
    }
}
