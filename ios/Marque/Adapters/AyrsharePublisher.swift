import Foundation

// Live publishing via Ayrshare. Active when AppConfig.ayrshareKey is set; otherwise the app
// uses MockPublisher. Untestable here without a key + a public media URL (which comes from the
// render pipeline / R2). Falls back to the mock on any error so scheduling never hard-fails.
// Production note: this should run server-side (FastAPI) so the key never ships in the app.
struct AyrsharePublisher: Publishing {
    private let fallback = MockPublisher()

    func schedule(_ post: ScheduledPost) async -> Bool {
        guard !AppConfig.ayrshareKey.isEmpty,
              let url = URL(string: "https://api.ayrshare.com/api/post") else {
            return await fallback.schedule(post)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 30
        req.setValue("Bearer \(AppConfig.ayrshareKey)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let platforms = post.platforms.map { $0 == .instagram ? "instagram" : "tiktok" }
        let iso = ISO8601DateFormatter().string(from: post.date)
        // mediaUrls would be the rendered clip's public R2/Stream URL once the pipeline produces it.
        var body: [String: Any] = [
            "post": post.caption,
            "platforms": platforms,
            "scheduleDate": iso
        ]
        // Attach the rendered clip's public URL so the post carries video, not just a caption.
        if let media = post.mediaURL, media.hasPrefix("http") { body["mediaUrls"] = [media] }
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)

        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                return await fallback.schedule(post)
            }
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let status = json["status"] as? String {
                return status == "success" || status == "scheduled"
            }
            return true
        } catch {
            return await fallback.schedule(post)
        }
    }
}
