import Foundation

// Server-side publisher: sends all publish requests to the backend /v1/publish, which holds
// the Ayrshare key and is the single place publish decisions are made. The Ayrshare key
// never ships in the iOS app (three-trust-plane model). Falls back to MockPublisher on
// any network failure so scheduling never hard-fails in dev/CI.
struct BackendPublisher: Publishing {
    private let client = BackendClient()
    private let fallback = MockPublisher()

    func schedule(_ post: ScheduledPost) async -> Bool {
        let platforms = post.platforms.map { $0 == .instagram ? "instagram" : "tiktok" }
        let iso = ISO8601DateFormatter().string(from: post.date)
        var body: [String: Any] = [
            "caption": post.caption,
            "platforms": platforms,
            "schedule_date": iso,
        ]
        if let media = post.mediaURL, media.hasPrefix("http") {
            body["media_url"] = media
        }
        guard let data = await client.post("/v1/publish", body),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let ok = json["ok"] as? Bool else {
            return await fallback.schedule(post)
        }
        return ok
    }
}
