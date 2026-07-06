import Foundation

// Live clip engine: mints an R2 upload URL, uploads the raw take direct to R2,
// creates a server-side clip job, and polls the job status. Falls back to
// MockClipEngine when the backend is unreachable (preserves offline/CI behavior).
struct LiveClipEngine: ClipEngineProtocol {
    private let fallback = MockClipEngine()

    func makeClips(from script: Script, formats: [String], reactSourceURL: String = "") async -> [Clip] {
        // 1. Mint a presigned R2 upload URL.
        guard let mintData = await BackendClient.shared.mintUploadURL(filename: "footage.mov"),
              let sourceURL = mintData["public_url"] as? String,
              let jobData = await BackendClient.shared.createClipJob(
                sourceURL: sourceURL,
                script: script,
                formats: formats,
                reactSourceURL: reactSourceURL
              ) else {
            return await fallback.makeClips(from: script, formats: formats)
        }
        // 2. Parse clips from the job creation response.
        guard let clipDicts = jobData["clips"] as? [[String: Any]] else {
            return await fallback.makeClips(from: script, formats: formats)
        }
        let jobId = jobData["job_id"] as? String ?? UUID().uuidString
        return clipDicts.map { d in
            let clipId = UUID(uuidString: d["clip_id"] as? String ?? "") ?? UUID()
            let formatId = d["format"] as? String ?? (formats.first ?? script.formatId)
            return Clip(
                id: clipId,
                scriptId: script.id,
                formatId: formatId,
                formatName: Catalog.format(formatId).name,
                title: script.title.isEmpty ? script.hook.text : script.title,
                caption: script.cta,
                predictedScore: script.predictedScore,
                status: .rendering,
                seconds: Catalog.format(formatId).targetSeconds,
                jobId: jobId
            )
        }
    }

    func render(clipId: UUID) async -> ClipStatus {
        // Render is driven by the backend job; caller uses pollJob(jobId:) for status.
        return .rendering
    }
}

// MARK: - BackendClient extensions for Phase 1

extension BackendClient {
    static let shared = BackendClient()

    func mintUploadURL(filename: String) async -> [String: Any]? {
        guard let data = await post("/v1/uploads/mint",
                                    ["filename": filename, "content_type": "video/quicktime"]) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    func createClipJob(sourceURL: String, script: Script, formats: [String],
                       reactSourceURL: String = "") async -> [String: Any]? {
        var body: [String: Any] = [
            "source_url": sourceURL,
            "source_id": script.id.uuidString,
            "formats": formats,
            "style": script.style,
            "script": [
                "hook": script.hook.text,
                "body": script.body,
                "cta": script.cta,
                "formatId": script.formatId,
                "shotPlan": script.shotPlan,
            ],
            "edit_prefs": editPrefs,
        ]
        if !reactSourceURL.isEmpty { body["react_source_url"] = reactSourceURL }
        guard let data = await post("/v1/clips", body) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    func pollClipJob(jobId: String, includeWords: Bool = false) async -> [String: Any]? {
        let path = "/v1/clips/\(jobId)" + (includeWords ? "?include_words=1" : "")
        guard let data = await get(path) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    /// Re-run a failed clip job's render (backend keeps the source + EDL). Returns
    /// true on 200; 404/409 are treated as no-op (nothing to retry / already running).
    @discardableResult
    func retryClipJob(jobId: String) async -> Bool {
        let (_, status) = await postWithStatus("/v1/clips/\(jobId)/retry", [:])
        return status == 200
    }

    /// The manual-editor apply path: pre-typed EDL ops bypass LLM interpretation
    /// and go straight to deterministic application + a single re-render.
    func tweakClipOps(jobId: String, clipId: String, ops: [[String: Any]]) async -> [String: Any] {
        let body: [String: Any] = ["clip_id": clipId, "ops": ops]
        let (data, status) = await postWithStatus("/v1/clips/\(jobId)/tweak", body)
        if status == 404 { return ["error": true, "reply": "This edit session has expired — re-submit the take."] }
        // H3: "transient" lets the caller distinguish "a render is still in
        // flight, just retry shortly" from a genuinely fatal error — the
        // manual editor uses this to stay on the editing screen (preserving
        // all staged local edits) instead of treating it as terminal.
        if status == 409 {
            return ["error": true, "transient": true,
                    "reply": "Still rendering your last change — try again in a minute."]
        }
        guard let data, let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return ["error": true, "reply": "Couldn't reach the editor — check your connection."]
        }
        return dict
    }

    /// One conversational tweak turn on a finished clip. Returns the decoded
    /// response dict, or a synthesized error dict mapping the backend's status
    /// codes to user-facing copy (404 = expired in-memory session, 409 = a
    /// render is already in flight).
    func tweakClip(jobId: String, clipId: String, instruction: String) async -> [String: Any] {
        let body: [String: Any] = ["clip_id": clipId, "instruction": instruction]
        let (data, status) = await postWithStatus("/v1/clips/\(jobId)/tweak", body)
        if status == 404 {
            return ["error": true, "reply": "This edit session has expired — re-submit the take to tweak it."]
        }
        if status == 409 {
            return ["error": true, "reply": "Hold on — I'm still rendering your last tweak. Try again in a minute."]
        }
        guard let data, let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return ["error": true, "reply": "I couldn't reach the editor — check your connection and try again."]
        }
        return dict
    }
}
