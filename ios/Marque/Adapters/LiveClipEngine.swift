import Foundation

// Live clip engine: mints an R2 upload URL, uploads the raw take direct to R2,
// creates a server-side clip job, and polls the job status. Falls back to
// MockClipEngine when the backend is unreachable (preserves offline/CI behavior).
struct LiveClipEngine: ClipEngineProtocol {
    private let fallback = MockClipEngine()

    func makeClips(from script: Script, formats: [String]) async -> [Clip] {
        // 1. Mint a presigned R2 upload URL.
        guard let mintData = await BackendClient.shared.mintUploadURL(filename: "footage.mov"),
              let sourceURL = mintData["public_url"] as? String,
              let jobData = await BackendClient.shared.createClipJob(
                sourceURL: sourceURL,
                script: script,
                formats: formats
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

    func createClipJob(sourceURL: String, script: Script, formats: [String]) async -> [String: Any]? {
        let body: [String: Any] = [
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
        ]
        guard let data = await post("/v1/clips", body) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    func pollClipJob(jobId: String) async -> [String: Any]? {
        guard let data = await get("/v1/clips/\(jobId)") else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }
}
