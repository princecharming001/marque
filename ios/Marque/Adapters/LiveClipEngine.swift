import Foundation

// Live clip-engine scaffold. The real pipeline (upload -> AssemblyAI transcribe -> moment
// detect -> Shotstack render per format -> R2) runs SERVER-SIDE via the backend's Trigger.dev
// orchestration (see backend/). This client holds the structural request builders and is
// key-gated; makeClips mirrors the mock so the app works end-to-end without keys/backend.
struct LiveClipEngine: ClipEngineProtocol {
    private let fallback = MockClipEngine()

    func makeClips(from script: Script, formats: [String]) async -> [Clip] {
        // Production: POST the recorded take to the backend, which fans out one render per format.
        // Until that pipeline + keys are live, mirror the deterministic mock.
        await fallback.makeClips(from: script, formats: formats)
    }

    func render(clipId: UUID) async -> ClipStatus {
        await fallback.render(clipId: clipId)
    }

    // MARK: - Structural clients (invoked by the backend pipeline)

    /// Submit a public video URL to AssemblyAI for transcription + word timings + key phrases.
    func submitTranscription(videoURL: String) async -> String? {
        guard !AppConfig.assemblyAIKey.isEmpty,
              let url = URL(string: "https://api.assemblyai.com/v2/transcript") else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue(AppConfig.assemblyAIKey, forHTTPHeaderField: "authorization")
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "audio_url": videoURL, "auto_highlights": true, "speaker_labels": false,
        ])
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return j["id"] as? String
    }

    /// Kick off a Shotstack template render with merge fields (one per format recipe).
    func submitRender(templateId: String, merge: [String: String]) async -> String? {
        guard !AppConfig.shotstackKey.isEmpty,
              let url = URL(string: "https://api.shotstack.io/v1/templates/render") else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue(AppConfig.shotstackKey, forHTTPHeaderField: "x-api-key")
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        let mergeFields = merge.map { ["find": $0.key, "replace": $0.value] }
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["id": templateId, "merge": mergeFields])
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (200..<300).contains((resp as? HTTPURLResponse)?.statusCode ?? 0),
              let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let response = j["response"] as? [String: Any] else { return nil }
        return response["id"] as? String
    }
}
