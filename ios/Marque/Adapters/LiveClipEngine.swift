import Foundation
import UserNotifications
import AVFoundation
import UIKit

/// Liveness v2: runs an async operation under a UIKit background-task assertion so an
/// app-switch mid-upload gives the OS ~30s of continued execution to finish the network
/// critical section instead of freezing it mid-flight. Expiration just releases the
/// assertion — losing the race is SAFE because the take persists on disk and the
/// launch/foreground reconcile sweep (AppStore.reconcileTransientState) auto-resumes
/// the upload; this guard exists so short backgroundings (a notification peek, an app
/// switch) don't interrupt an upload that was seconds from finishing.
enum BackgroundGuard {
    @MainActor private final class Token { var id: UIBackgroundTaskIdentifier = .invalid }

    static func run<T: Sendable>(_ name: String, _ op: @Sendable () async -> T) async -> T {
        let token = await MainActor.run { () -> Token in
            let t = Token()
            t.id = UIApplication.shared.beginBackgroundTask(withName: name) {
                if t.id != .invalid {
                    UIApplication.shared.endBackgroundTask(t.id)
                    t.id = .invalid
                }
            }
            return t
        }
        let result = await op()
        await MainActor.run {
            if token.id != .invalid {
                UIApplication.shared.endBackgroundTask(token.id)
                token.id = .invalid
            }
        }
        return result
    }
}

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
            // Compress the raw take (full device bitrate — often >50MB) down to fit
            // the storage cap before uploading; keep the on-disk original for the
            // local rough-cut preview. Fall back to the original if compression
            // fails (an oversize original just fails the upload → mock fallback).
            let original = MediaStore.url(for: footagePath)
            let cap = (mintData["max_upload_bytes"] as? Int) ?? MediaCompressor.defaultMaxUploadBytes
            let compressed = await MediaCompressor.forUpload(original, maxBytes: cap)
            let toUpload = compressed ?? original
            let ok = await Self.uploadFootage(to: uploadURLString, fileURL: toUpload)
            if let compressed { try? FileManager.default.removeItem(at: compressed) }
            guard ok else { return await fallback.makeClips(from: script, formats: formats) }
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
        // Misconfigured-backend guard: a LIVE backend (real transcribe/render) with
        // storage unconfigured mints an empty public_url, so the job just created will
        // fail on an unfetchable source. Hand the user working local mock clips instead
        // of a doomed .rendering clip. A MOCK backend returns mode="mock" here and is
        // the intended offline/demo path (mock-ready job + jobId, editor works) — keep it.
        if publicURL.isEmpty, (jobData["mode"] as? String) == "live" {
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

    /// H-02: mint + compress + upload as ONE hoisted step, so RecordView can start
    /// the upload the moment a take lands (while the creator reviews) and the public
    /// URL already exists when the analyze job is created. Returns the public read
    /// URL ("" on the keyless/mock backend — no storage, mock brief still works),
    /// or nil on a hard failure (backend unreachable / upload failed) so the caller
    /// falls back to the local mock pipeline instead of creating a doomed live job.
    /// Upload any media file (photo or video) for a roll — mintAndUpload with a
    /// caller-chosen filename (content-type follows it) and no compressor for images.
    static func uploadMedia(path: String, filename: String) async -> String? {
        await BackgroundGuard.run("media-upload") {
            await _uploadMedia(path: path, filename: filename)
        }
    }

    private static func _uploadMedia(path: String, filename: String) async -> String? {
        guard let mintData = await BackendClient.shared.mintUploadURL(filename: filename) else { return nil }
        let uploadURLString = mintData["upload_url"] as? String ?? ""
        let publicURL = mintData["public_url"] as? String ?? ""
        guard !uploadURLString.isEmpty else { return publicURL }     // mock mint: nothing to move
        let original = MediaStore.url(for: path)
        var toUpload = original
        var cleanup: URL? = nil
        if filename.lowercased().hasSuffix(".mov") || filename.lowercased().hasSuffix(".mp4") {
            let cap = (mintData["max_upload_bytes"] as? Int) ?? MediaCompressor.defaultMaxUploadBytes
            if let compressed = await MediaCompressor.forUpload(original, maxBytes: cap) { toUpload = compressed; cleanup = compressed }
        }
        let ok = await uploadFootage(to: uploadURLString, fileURL: toUpload)
        if let cleanup { try? FileManager.default.removeItem(at: cleanup) }
        return ok ? publicURL : nil
    }

    static func mintAndUpload(footagePath: String?,
                              onProgress: (@Sendable (Double) -> Void)? = nil) async -> String? {
        // Background-guarded: compression + PUT is the longest client-side critical
        // section — a brief app-switch must not kill an almost-done upload.
        await BackgroundGuard.run("take-upload") {
            await _mintAndUpload(footagePath: footagePath, onProgress: onProgress)
        }
    }

    private static func _mintAndUpload(footagePath: String?,
                                       onProgress: (@Sendable (Double) -> Void)? = nil) async -> String? {
        guard let mintData = await BackendClient.shared.mintUploadURL(filename: "footage.mov") else {
            return nil                                        // backend unreachable
        }
        let uploadURLString = mintData["upload_url"] as? String ?? ""
        let publicURL = mintData["public_url"] as? String ?? ""
        guard let footagePath, !footagePath.isEmpty, !uploadURLString.isEmpty else {
            onProgress?(1.0)
            return publicURL                                  // mock mint or no footage: no bytes to move
        }
        let original = MediaStore.url(for: footagePath)
        let cap = (mintData["max_upload_bytes"] as? Int) ?? MediaCompressor.defaultMaxUploadBytes
        // Build 45: compression fills the first 40% of the bar, the PUT the last 60% —
        // so the creator sees continuous motion across both device-side phases.
        let compressed = await MediaCompressor.forUpload(original, maxBytes: cap) { p in
            onProgress?(min(0.4, p * 0.4))
        }
        let toUpload = compressed ?? original
        let ok = await uploadFootage(to: uploadURLString, fileURL: toUpload) { p in
            onProgress?(0.4 + min(0.6, p * 0.6))
        }
        if let compressed { try? FileManager.default.removeItem(at: compressed) }
        return ok ? publicURL : nil
    }

    /// PUT the recorded take to the minted signed-upload URL. Streams from the file
    /// on disk (a full talking-head take is tens/hundreds of MB — never load it all
    /// into memory). Content-Type matches what the mint request declared. Returns
    /// true only on a 2xx; any transport/HTTP failure returns false so makeClips
    /// falls back instead of creating a job with an empty source object.
    private static func uploadFootage(to uploadURLString: String, fileURL: URL,
                                      onProgress: (@Sendable (Double) -> Void)? = nil) async -> Bool {
        guard let url = URL(string: uploadURLString),
              FileManager.default.fileExists(atPath: fileURL.path) else {
            BackendClient.shared.reportClientEvent("upload_precondition_failed",
                                                   detail: "missing file or bad url")
            return false
        }
        // Up to 3 attempts with backoff — a single transient network blip on a large
        // cellular upload used to hard-fail the whole edit with no retry.
        var lastDetail = ""
        for attempt in 0..<3 {
            if attempt > 0 {
                try? await Task.sleep(nanoseconds: UInt64(attempt) * 2_000_000_000)
                onProgress?(0)                 // retry restarts the byte count
            }
            var req = URLRequest(url: url)
            req.httpMethod = "PUT"
            req.timeoutInterval = 300   // large upload, possibly on cellular
            req.setValue("video/quicktime", forHTTPHeaderField: "Content-Type")
            do {
                let delegate = onProgress.map { UploadProgressDelegate(onProgress: $0) }
                let (_, resp) = try await URLSession.shared.upload(for: req, fromFile: fileURL,
                                                                   delegate: delegate)
                if let http = resp as? HTTPURLResponse {
                    if (200..<300).contains(http.statusCode) { return true }
                    lastDetail = "http \(http.statusCode)"
                    if (400..<500).contains(http.statusCode) { break }   // permanent — don't retry
                } else {
                    lastDetail = "non-http response"
                }
            } catch {
                lastDetail = error.localizedDescription
            }
        }
        // Surface the failure server-side so a client-only breakage is diagnosable from
        // Render logs (this failure class previously left ZERO server trace).
        let mb = (try? FileManager.default.attributesOfItem(atPath: fileURL.path)[.size] as? Int).flatMap { $0 } ?? 0
        BackendClient.shared.reportClientEvent("upload_failed",
                                               detail: "\(lastDetail) | \(mb / 1_000_000)MB")
        return false
    }

    func render(clipId: UUID) async -> ClipStatus {
        // Render is driven by the backend job; caller uses pollJob(jobId:) for status.
        return .rendering
    }
}

// Compresses a recorded take so it fits object storage before upload. The raw camera
// file is full device bitrate (a 60–90s 1080p take is often 80–150MB); the cap is
// server-driven (mint response `max_upload_bytes`, default ~48MB).
//
// P0.1 — quality ladder: the old path compressed to 720p/540p, which the render then
// UPSCALED to 1080p — soft faces, b-roll sharper than the speaker. Now short/medium
// takes transcode to 1080p HEVC at an explicit bitrate that fits the cap (native
// resolution preserved, no upscale), and only genuinely long takes (>150s, where a
// fitting 1080p bitrate would look worse than clean 720p) fall to the export-preset
// ladder. The preset ladder also stays as the safety net if the 1080p transcode
// overshoots the cap or the writer fails.
enum MediaCompressor {
    static let defaultMaxUploadBytes = 48_000_000
    private static let audioBps = 96_000            // AAC voice budget subtracted from the cap
    private static let longTakeThresholdSec = 150.0 // above this, 1080p bitrate would be too low → 720p ladder

    /// `maxBytes` comes from the mint response so raising the storage tier is backend-only.
    static func forUpload(_ source: URL, maxBytes: Int = defaultMaxUploadBytes,
                          onProgress: (@Sendable (Double) -> Void)? = nil) async -> URL? {
        // OPT-7: skip re-encoding entirely when the source already fits — short takes
        // upload as-is instead of paying a full export on the critical path.
        let srcSize = (try? FileManager.default.attributesOfItem(atPath: source.path))?[.size] as? Int
        if let srcSize, srcSize <= maxBytes { onProgress?(1.0); return nil }   // caller uploads the original

        let asset = AVURLAsset(url: source)
        let seconds = CMTimeGetSeconds(asset.duration)

        // 1080p HEVC path for takes short enough that a cap-fitting bitrate still looks
        // good. Budget = the cap's bits/sec minus audio, held under the cap with an 8%
        // muxing-overhead margin; capped at a duration-tiered target so short takes don't
        // get a needlessly huge bitrate.
        if seconds.isFinite, seconds > 0, seconds <= longTakeThresholdSec {
            let capBudgetBps = Int(Double(maxBytes) * 8.0 / seconds) - audioBps
            let tierTarget = seconds <= 90 ? 3_800_000 : 2_600_000   // ≤90s vs 90–150s
            let videoBps = max(1_200_000, min(tierTarget, Int(Double(capBudgetBps) * 0.92)))
            // transcodeHEVC self-bounds via an internal 90s reader/writer-cancel deadline, so an
            // undecodable import can't hang here — it returns nil and we fall through to the
            // robust export preset ladder (AVAssetExportSession tonemaps HDR→SDR / handles odd formats).
            if let out = await transcodeHEVC(asset, videoBps: videoBps),
               let size = fileSize(out), size <= maxBytes {
                return out
            }
            // Overshoot / writer failure / transcode timeout → fall through to the preset ladder.
        }

        // Long takes, or a 1080p transcode that overshot: the export-preset ladder
        // (720p → 540p). A take that will obviously blow the cap at 720p (~2.5Mbps) skips
        // straight to 540p instead of paying a wasted 720p export first.
        var presets = [AVAssetExportPreset1280x720, AVAssetExportPreset960x540]
        if seconds.isFinite, seconds * 2_500_000 / 8 > Double(maxBytes) {
            presets = [AVAssetExportPreset960x540]
        }
        // Build 45: cap the WHOLE ladder to one export budget so it can't chain two
        // 120s cancel-deadlines back to back (a stalled ladder used to burn ~4 min
        // before the outer submit ceiling caught it — that's the "stuck" window).
        let ladderDeadline = Date().addingTimeInterval(140)
        for preset in presets {
            if Date() > ladderDeadline { break }
            // export() self-bounds via its own cancelExport() deadline — a wedged export drops
            // to the next preset / fails instead of hanging.
            guard let out = await export(source, preset: preset, onProgress: onProgress) else { continue }
            if let size = fileSize(out), size <= maxBytes { return out }
            try? FileManager.default.removeItem(at: out)   // too big — drop and try a smaller preset
        }
        return nil
    }

    private static func fileSize(_ url: URL) -> Int? {
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.size] as? Int
    }

    /// Native-resolution HEVC transcode at an explicit average bitrate (AVAssetReader →
    /// AVAssetWriter). Preserves the source dimensions + orientation so 1080p stays 1080p
    /// (the whole point — no upscale in the render). Re-encodes audio to AAC at `audioBps`.
    private static func transcodeHEVC(_ asset: AVURLAsset, videoBps: Int) async -> URL? {
        guard let vTrack = asset.tracks(withMediaType: .video).first else { return nil }
        let out = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".mov")
        guard let reader = try? AVAssetReader(asset: asset),
              let writer = try? AVAssetWriter(outputURL: out, fileType: .mov) else { return nil }

        // Decode video to biplanar 420 (video range), then re-encode HEVC at target bitrate.
        let readerVideoOut = AVAssetReaderTrackOutput(
            track: vTrack,
            outputSettings: [kCVPixelBufferPixelFormatTypeKey as String:
                                kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange])
        readerVideoOut.alwaysCopiesSampleData = false
        guard reader.canAdd(readerVideoOut) else { return nil }
        reader.add(readerVideoOut)

        // Encode at native size, but cap the short edge at 1080 so a >1080 source (e.g. a
        // 4K capture) is downscaled to true 1080p rather than starved of bitrate at 4K.
        let (outW, outH) = cappedDimensions(width: abs(vTrack.naturalSize.width),
                                            height: abs(vTrack.naturalSize.height), cap: 1080)
        let writerVideoIn = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.hevc,
            AVVideoWidthKey: outW,
            AVVideoHeightKey: outH,
            AVVideoCompressionPropertiesKey: [
                AVVideoAverageBitRateKey: videoBps,
                AVVideoMaxKeyFrameIntervalKey: 60,
                AVVideoExpectedSourceFrameRateKey: 30,
            ],
        ])
        writerVideoIn.expectsMediaDataInRealTime = false
        writerVideoIn.transform = vTrack.preferredTransform   // preserve orientation
        guard writer.canAdd(writerVideoIn) else { return nil }
        writer.add(writerVideoIn)

        // Audio: re-encode to AAC at the voice budget (nil if the take has no audio track).
        var readerAudioOut: AVAssetReaderTrackOutput?
        var writerAudioIn: AVAssetWriterInput?
        if let aTrack = asset.tracks(withMediaType: .audio).first {
            let aOut = AVAssetReaderTrackOutput(
                track: aTrack,
                outputSettings: [AVFormatIDKey: kAudioFormatLinearPCM])
            aOut.alwaysCopiesSampleData = false
            let aIn = AVAssetWriterInput(mediaType: .audio, outputSettings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVNumberOfChannelsKey: 1,
                AVSampleRateKey: 44_100,
                AVEncoderBitRateKey: audioBps,
            ])
            aIn.expectsMediaDataInRealTime = false
            if reader.canAdd(aOut), writer.canAdd(aIn) {
                reader.add(aOut); writer.add(aIn)
                readerAudioOut = aOut; writerAudioIn = aIn
            }
        }

        guard reader.startReading(), writer.startWriting() else {
            try? FileManager.default.removeItem(at: out); return nil
        }
        writer.startSession(atSourceTime: .zero)

        // HARD deadline: on an undecodable import (HDR 10-bit / ProRes / slow-mo) the pump's
        // copyNextSampleBuffer can block forever and never resume its continuation. A plain
        // Task/taskGroup cancel does NOT interrupt that non-cooperative AVFoundation call —
        // cancelReading()/cancelWriting() DO (subsequent copyNextSampleBuffer returns nil, the
        // pump resumes, reader.status != .completed, we bail to the robust export ladder).
        let deadline = Task {
            try? await Task.sleep(nanoseconds: 90 * 1_000_000_000)
            reader.cancelReading(); writer.cancelWriting()
        }
        // Pump video + (optional) audio inputs concurrently; resume once both drain.
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await pump(writerVideoIn, from: readerVideoOut) }
            if let aIn = writerAudioIn, let aOut = readerAudioOut {
                group.addTask { await pump(aIn, from: aOut) }
            }
        }
        deadline.cancel()

        guard reader.status == .completed else {
            writer.cancelWriting(); try? FileManager.default.removeItem(at: out); return nil
        }
        await writer.finishWriting()
        guard writer.status == .completed else {
            try? FileManager.default.removeItem(at: out); return nil
        }
        return out
    }

    /// Native dimensions with the short edge capped at `cap`, rounded to even numbers
    /// (H.265 encoders require even width/height). Aspect ratio preserved.
    private static func cappedDimensions(width: CGFloat, height: CGFloat, cap: CGFloat) -> (Int, Int) {
        let shortEdge = min(width, height)
        let scale = shortEdge > cap ? cap / shortEdge : 1.0
        func even(_ v: CGFloat) -> Int { let n = Int((v * scale).rounded()); return n - (n % 2) }
        return (max(2, even(width)), max(2, even(height)))
    }

    /// Drain one reader output into one writer input, honoring back-pressure.
    private static func pump(_ input: AVAssetWriterInput, from output: AVAssetReaderTrackOutput) async {
        let queue = DispatchQueue(label: "mediacompressor.pump.\(UUID().uuidString)")
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            input.requestMediaDataWhenReady(on: queue) {
                while input.isReadyForMoreMediaData {
                    guard let sample = output.copyNextSampleBuffer() else {
                        input.markAsFinished(); cont.resume(); return
                    }
                    // append returns false if the writer failed mid-stream — stop cleanly
                    // (finishWriting will then report a non-.completed status → caller drops it).
                    if !input.append(sample) {
                        input.markAsFinished(); cont.resume(); return
                    }
                }
            }
        }
    }

    private static func export(_ source: URL, preset: String,
                               onProgress: (@Sendable (Double) -> Void)? = nil) async -> URL? {
        let asset = AVURLAsset(url: source)
        guard let session = AVAssetExportSession(asset: asset, presetName: preset) else { return nil }
        let out = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".mov")
        session.outputURL = out
        session.outputFileType = .mov
        session.shouldOptimizeForNetworkUse = true
        return await withCheckedContinuation { cont in
            // HARD deadline: an export can wedge on a pathological import. cancelExport()
            // forces the completion handler to fire with .cancelled — a Task/taskGroup cancel
            // does NOT touch the underlying AVFoundation work, so we cancel the SESSION itself.
            let deadline = Task {
                try? await Task.sleep(nanoseconds: 120 * 1_000_000_000)
                session.cancelExport()
            }
            // Poll session.progress on a light timer so the bar moves during compression
            // (AVAssetExportSession has no progress callback). Ends when export completes.
            let progressPoll = onProgress.map { cb in
                Task {
                    while !Task.isCancelled {
                        try? await Task.sleep(nanoseconds: 250_000_000)
                        cb(Double(session.progress))
                    }
                }
            }
            session.exportAsynchronously {
                deadline.cancel()
                progressPoll?.cancel()
                if session.status == .completed {
                    onProgress?(1.0)
                    cont.resume(returning: out)
                } else {
                    try? FileManager.default.removeItem(at: out)   // partial/cancelled — don't leak
                    cont.resume(returning: nil)
                }
            }
        }
    }
}

// Build 45: forwards URLSession upload byte-progress → a 0–1 fraction so the take card's
// timeline shows a real bar during the PUT. Per-task delegate (iOS 15+), so it never
// touches the shared session's global behavior.
final class UploadProgressDelegate: NSObject, URLSessionTaskDelegate {
    private let onProgress: @Sendable (Double) -> Void
    init(onProgress: @escaping @Sendable (Double) -> Void) { self.onProgress = onProgress }
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    didSendBodyData bytesSent: Int64, totalBytesSent: Int64,
                    totalBytesExpectedToSend: Int64) {
        guard totalBytesExpectedToSend > 0 else { return }
        onProgress(Double(totalBytesSent) / Double(totalBytesExpectedToSend))
    }
}

// MARK: - BackendClient extensions for Phase 1

extension BackendClient {
    static let shared = BackendClient()
    // OPT-8: session cache for GET /v1/editor/capabilities (static per backend build).
    static var cachedEditorCaps: [String: [String: Bool]]? = nil
    // A7: session cache for GET /v1/themes (static per backend build).
    static var cachedThemes: [ThemeChoice]? = nil

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

    // MARK: Analyze-first pair (Loop H) — the server analyzes the raw take into an
    // edit brief FIRST; the creator reviews brief + toggles, then /confirm edits+renders.

    /// POST /v1/clips with analyze_first — returns immediately with either a ready
    /// brief (keyless mock) or status "analyzing" (poll getBrief until brief_ready).
    func createAnalyzeJob(sourceURL: String, script: Script?,
                          customInstructions: String = "",
                          reactSourceURL: String = "",
                          editFormat: String = "",
                          referenceReel: ReelItem? = nil,
                          themeId: String? = nil,
                          config: [String: String]? = nil,
                          autoConfirm: Bool = false,
                          toggles: EditToggles? = nil,
                          corpus: [[String: Any]] = []) async -> AnalyzeJobResponse? {
        var body: [String: Any] = [
            "analyze_first": true,
            "source_url": sourceURL,
            "custom_instructions": customInstructions,
            "edit_prefs": editPrefs,
            "creator_id": creatorId,
        ]
        // WS4: the creator's analyzed own-media corpus. When b-roll is on the backend
        // scores these against each cue and places OWN footage before stock (this was
        // never sent → imported media was never used as b-roll).
        if !corpus.isEmpty { body["corpus"] = corpus }
        // UX-B1b one-tap submit: run the whole pipeline (no brief_ready stop); the
        // response then carries the clips array for immediate tracking.
        if autoConfirm {
            body["auto_confirm"] = true
            if let t = toggles {
                body["toggles"] = ["broll": t.broll, "punch_ins": t.punchIns, "music": t.music]
            }
        }
        // The creator's explicit cut treatment — pins the engine style server-side
        // (brief inference never overrides an explicit pick).
        if !editFormat.isEmpty { body["edit_format"] = editFormat }
        // "Match a vibe" style pick → theme_id, which actually drives the edit
        // (apply_theme + retention passes). Overrides the format's default theme.
        if let themeId, !themeId.isEmpty { body["theme_id"] = themeId }
        // Creator style config (Addendum Part 1) — e.g. the B-ROLL STYLE pick maps to
        // config.broll_coverage, which steers the plan prompt's coverage hints.
        if let config, !config.isEmpty { body["config"] = config }
        // The reel this cut should FEEL like (pacing/energy/caption vibe, never words).
        if let r = referenceReel {
            var ref: [String: Any] = [
                "id": r.id, "creator_handle": r.creatorHandle, "platform": r.platform,
                "title": r.title, "hook_text": r.hookText, "why_trending": r.whyTrending,
                "format_id": r.formatId, "style": r.style,
            ]
            // The backend whitelists video_url and MEASURES the picked reel (cut density,
            // caption vibe, energy) when VIDEO_UNDERSTANDING is on — omitting it left the
            // reel a text-only nudge and the measurement path dead. Send it.
            if !r.videoURL.isEmpty { ref["video_url"] = r.videoURL }
            body["reference_reel"] = ref
        }
        // AF-I2 (audit): the duet react source was silently dropped on the whole
        // analyze path — the rendered duet had no react panel with zero errors.
        if !reactSourceURL.isEmpty { body["react_source_url"] = reactSourceURL }
        if let script {
            body["source_id"] = script.id.uuidString
            body["style"] = script.style
            body["script"] = [
                "hook": script.hook.text,
                "body": script.body,
                "cta": script.cta,
                "formatId": script.formatId,
            ]
        }
        guard let data = await post("/v1/clips", body) else { return nil }
        return try? JSONDecoder().decode(AnalyzeJobResponse.self, from: data)
    }

    /// UX-B2b: register this device's APNs token (POST /v1/devices — idempotent
    /// upsert). DEBUG builds run against the sandbox APNs gateway.
    func registerDevice(token: String) async {
        #if DEBUG
        let environment = "sandbox"
        #else
        let environment = "prod"
        #endif
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        let permission: String
        switch settings.authorizationStatus {
        case .authorized: permission = "authorized"
        case .provisional: permission = "provisional"
        case .denied: permission = "denied"
        default: permission = "notDetermined"
        }
        let body: [String: Any] = [
            "token": token,
            "environment": environment,
            "creator_id": creatorId,
            "platform": "ios",
            "app_version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "",
            "timezone": TimeZone.current.identifier,
            "permission": permission,
        ]
        _ = await post("/v1/devices", body)
    }

    /// GET /v1/editor/capabilities → which optional edit ops each style can actually
    /// render (G-04). The brief screen + manual editor hide toggles that would be
    /// silent no-ops. nil on transport failure — callers show everything rather than
    /// wrongly hiding a real capability.
    func editorCapabilities() async -> [String: [String: Bool]]? {
        // OPT-8: version-stable per backend process — fetch once per app session
        // instead of on every RecordView/EditorView instance.
        if let cached = BackendClient.cachedEditorCaps { return cached }
        guard let data = await get("/v1/editor/capabilities"),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let caps = dict["capabilities"] as? [String: Any] else { return nil }
        var out: [String: [String: Bool]] = [:]
        for (style, v) in caps {
            if let m = v as? [String: Any] {
                out[style] = m.compactMapValues { $0 as? Bool }
            }
        }
        BackendClient.cachedEditorCaps = out
        return out
    }

    /// A7: GET /v1/themes — the style-bundle catalog. Version-stable per backend
    /// process (same caching rationale as editorCapabilities), so this fetches
    /// once per app session rather than every time the theme picker opens.
    func fetchThemes() async -> [ThemeChoice] {
        if let cached = BackendClient.cachedThemes { return cached }
        guard let data = await get("/v1/themes"),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let raw = dict["themes"] as? [[String: Any]] else { return [] }
        let out = raw.compactMap { t -> ThemeChoice? in
            guard let id = t["id"] as? String, let label = t["label"] as? String else { return nil }
            return ThemeChoice(id: id, label: label, blurb: t["blurb"] as? String ?? "",
                               defaultForFormats: t["default_for_formats"] as? [String] ?? [])
        }
        BackendClient.cachedThemes = out
        return out
    }

    /// A7 feature #1 ("Change theme"): POST /v1/clips/{id}/retheme — force-restamps
    /// a finished clip's caption/grade/duck to a different bundle and re-renders.
    /// clipId="" retargets every clip on the job (the common single-clip case).
    func rethemeClip(jobId: String, themeId: String, clipId: String = "") async -> [String: Any] {
        let body: [String: Any] = ["theme_id": themeId, "clip_id": clipId]
        let (data, status) = await postWithStatus("/v1/clips/\(jobId)/retheme", body)
        if status == 404 { return ["error": true, "reply": "This edit session has expired — re-submit the take."] }
        if status == 422 { return ["error": true, "reply": "That theme isn't available right now."] }
        if status == 409 {
            return ["error": true, "transient": true,
                    "reply": "Still rendering your last change — try again in a minute."]
        }
        if status == 503 {
            return ["error": true, "transient": true,
                    "reply": "Couldn't reach the studio just now — try again in a moment."]
        }
        guard let data, let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return ["error": true, "reply": "Couldn't reach the editor — check your connection."]
        }
        return dict
    }

    /// GET /v1/clips/{id} decoded for the analyze phase (status + edit_brief + toggles).
    func getBrief(jobId: String) async -> AnalyzeJobResponse? {
        guard let data = await get("/v1/clips/\(jobId)") else { return nil }
        return try? JSONDecoder().decode(AnalyzeJobResponse.self, from: data)
    }

    /// POST /v1/clips/{id}/confirm — the reviewed brief + toggles → edit + ONE render.
    /// Returns the raw response (job_id/status/clips) or nil on transport failure.
    func confirmClip(jobId: String, toggles: EditToggles,
                     customInstructions: String = "") async -> [String: Any]? {
        var body: [String: Any] = [
            "toggles": ["broll": toggles.broll, "punch_ins": toggles.punchIns,
                        "music": toggles.music],
        ]
        if !customInstructions.isEmpty { body["custom_instructions"] = customInstructions }
        guard let data = await post("/v1/clips/\(jobId)/confirm", body) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    func pollClipJob(jobId: String, includeWords: Bool = false) async -> [String: Any]? {
        let path = "/v1/clips/\(jobId)" + (includeWords ? "?include_words=1" : "")
        guard let data = await get(path) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    /// AF-I4: poll variant that surfaces the status code — 404/410 mean the job is
    /// permanently gone (restart with no durable copy / TTL-swept) and the poller
    /// must fail its clips instead of spinning "rendering" forever.
    func pollClipJobWithStatus(jobId: String, includeWords: Bool = false) async -> (result: [String: Any]?, status: Int) {
        let (data, status) = await getWithStatus("/v1/clips/\(jobId)" + (includeWords ? "?include_words=1" : ""))
        guard let data,
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return (nil, status)
        }
        return (dict, status)
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
    /// AF-6: deferRender commits the ops but spends no render (split's structural
    /// no-op case); preview renders a candidate WITHOUT committing (HD preview).
    func tweakClipOps(jobId: String, clipId: String, ops: [[String: Any]],
                      preview: Bool = false, deferRender: Bool = false) async -> [String: Any] {
        let body: [String: Any] = ["clip_id": clipId, "ops": ops]
        let path = "/v1/clips/\(jobId)/tweak"
            + (preview ? "?preview=1" : (deferRender ? "?defer_render=1" : ""))
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
        if status == 503 {
            return ["error": true, "transient": true,
                    "reply": "Couldn't reach the studio just now — try again in a moment."]
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
        if status == 503 {
            return ["error": true, "reply": "Couldn't reach the studio just now — try again in a moment."]
        }
        guard let data, let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return ["error": true, "reply": "I couldn't reach the editor — check your connection and try again."]
        }
        return dict
    }

    /// UX-D2: one PREVIEW-FIRST tweak turn — the instruction path with ?preview=1.
    /// The backend stages a candidate EDL (commits NOTHING), kicks a cheap proof
    /// render, and returns the full typed `ops` so Apply can commit them later via
    /// tweakClipOps. Same status-code copy as tweakClip.
    func tweakClipPreview(jobId: String, clipId: String, instruction: String) async -> [String: Any] {
        let body: [String: Any] = ["clip_id": clipId, "instruction": instruction]
        let (data, status) = await postWithStatus("/v1/clips/\(jobId)/tweak?preview=1", body)
        if status == 404 {
            return ["error": true, "reply": "This edit session has expired — re-submit the take to tweak it."]
        }
        if status == 409 {
            return ["error": true, "reply": "Hold on — I'm still rendering your last tweak. Try again in a minute."]
        }
        if status == 503 {
            return ["error": true, "reply": "Couldn't reach the studio just now — try again in a moment."]
        }
        guard let data, let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return ["error": true, "reply": "I couldn't reach the editor — check your connection and try again."]
        }
        return dict
    }
}
