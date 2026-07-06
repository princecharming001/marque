import Foundation

// Live clip engine: mints a signed upload URL, uploads the raw take to storage
// (Supabase Storage), creates a server-side clip job pointing at the public URL,
// and polls the job status. Falls back to MockClipEngine when the backend is
// unreachable, when no storage backend is configured, or when the upload fails —
// in every one of those cases a live job would just fail on an unfetchable source.
struct LiveClipEngine: ClipEngineProtocol {
    private let fallback = MockClipEngine()

    func makeClips(from script: Script, formats: [String], reactSourceURL: String = "",
                   footagePath: String? = nil) async -> [Clip] {
        // 1. Mint a signed upload URL + the public read URL from the backend.
        guard let mintData = await BackendClient.shared.mintUploadURL(filename: "footage.mov") else {
            return await fallback.makeClips(from: script, formats: formats)   // backend unreachable
        }
        let uploadURLString = mintData["upload_url"] as? String ?? ""
        let publicURL = mintData["public_url"] as? String ?? ""
        // 2. Upload the recorded take when we have BOTH footage and a real (non-empty)
        //    signed upload URL. A live backend fetches the public URL to transcribe +
        //    render, so the bytes must actually land first. A genuine upload FAILURE
        //    (couldn't store the video) falls back — a live job on a source that
        //    isn't really there would just fail at fetch. When there's no footage or
        //    no storage backend (empty upload URL), we skip the upload and still
        //    create the job: a keyless/mock backend returns a mock-ready job (jobId +
        //    mock EDL) regardless of source, which is the offline/demo path.
        if let footagePath, !footagePath.isEmpty, !uploadURLString.isEmpty {
            guard await uploadFootage(to: uploadURLString,
                                      fileURL: MediaStore.url(for: footagePath)) else {
                return await fallback.makeClips(from: script, formats: formats)
            }
        }
        // 3. Create the clip job pointing at the (now-uploaded) public source. The
        //    backend decides mock vs live: keyless → mock-ready; live + real source →
        //    the real pipeline; live + unconfigured storage → fails fast + cleanly
        //    (source_unreachable), which /readyz surfaces as storage:"mock".
        guard let jobData = await BackendClient.shared.createClipJob(
                sourceURL: publicURL,
                script: script,
                formats: formats,
                reactSourceURL: reactSourceURL
              ),
              let clipDicts = jobData["clips"] as? [[String: Any]] else {
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

    /// PUT the recorded take to the minted signed-upload URL. Streams from the file
    /// on disk (a full talking-head take is tens/hundreds of MB — never load it all
    /// into memory). Content-Type matches what the mint request declared. Returns
    /// true only on a 2xx; any transport/HTTP failure returns false so makeClips
    /// falls back instead of creating a job with an empty source object.
    private func uploadFootage(to uploadURLString: String, fileURL: URL) async -> Bool {
        guard let url = URL(string: uploadURLString),
              FileManager.default.fileExists(atPath: fileURL.path) else { return false }
        var req = URLRequest(url: url)
        req.httpMethod = "PUT"
        req.timeoutInterval = 300   // large upload, possibly on cellular
        req.setValue("video/quicktime", forHTTPHeaderField: "Content-Type")
        guard let (_, resp) = try? await URLSession.shared.upload(for: req, fromFile: fileURL),
              let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            return false
        }
        return true
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
    /// H11: preview=true asks the backend for the G9 cheap proof render
    /// instead of committing to the full one — never overwrites render_url.
    func tweakClipOps(jobId: String, clipId: String, ops: [[String: Any]], preview: Bool = false) async -> [String: Any] {
        let body: [String: Any] = ["clip_id": clipId, "ops": ops]
        let path = "/v1/clips/\(jobId)/tweak" + (preview ? "?preview=1" : "")
        let (data, status) = await postWithStatus(path, body)
        if status == 404 { return ["error": true, "reply": "This edit session has expired — re-submit the take."] }
        // H5: F9 added a structured 410 (a job that genuinely existed but was
        // TTL-swept) distinct from 404 (never existed) — this was previously
        // unrecognized entirely and fell through to the JSON-parse guard
        // below, which would have "succeeded" parsing the 410 body
        // ({"detail":"job_expired"}) and returned it as if the tweak worked.
        if status == 410 { return ["error": true, "reply": "This edit session has expired. Re-record and try again."] }
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
