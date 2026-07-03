import Foundation
import Observation
import Speech
import AVFoundation

// Tap-to-talk speech capture for the voice session (Phase 6).
//
// Design notes:
// - Everything is @MainActor; the audio tap and recognition callbacks hop back here
//   before touching observable state.
// - Every throwing call is wrapped — simulators without host-mic passthrough must
//   degrade to isAvailable=false (the view keeps the typed composer), never crash.
// - The audio session is claimed only while capturing (.playAndRecord / .measurement)
//   and released on stop with .notifyOthersOnDeactivation so VoicePlayback (and any
//   ducked background audio) can take over cleanly.
@MainActor
@Observable
final class SpeechRecognizer {

    // MARK: Observable state

    /// True while the engine is running and partials are streaming in.
    var isListening = false
    /// Live partial transcript — updates as the user speaks.
    var transcript = ""
    /// False when the recognizer/mic can't run here (simulator quirks, denied auth).
    var isAvailable = true
    /// Human-readable reason for the last failure ("" when fine).
    var lastError = ""
    /// Live mic input level, 0...1 (smoothed RMS) — drives the voice orb's reactivity
    /// while listening. Reset to 0 whenever capture stops.
    var inputLevel: Double = 0

    // MARK: Internals

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let audioEngine = AVAudioEngine()
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var tapInstalled = false

    // MARK: Authorization

    /// Requests speech-recognition AND microphone permission (both are needed).
    /// Idempotent — the system only prompts once; later calls resolve immediately.
    func requestAuthorization() async -> Bool {
        let speechStatus = await withCheckedContinuation { (cont: CheckedContinuation<SFSpeechRecognizerAuthorizationStatus, Never>) in
            SFSpeechRecognizer.requestAuthorization { cont.resume(returning: $0) }
        }
        guard speechStatus == .authorized else {
            isAvailable = false
            lastError = "Speech recognition permission was denied."
            return false
        }
        let micGranted = await AVAudioApplication.requestRecordPermission()   // iOS 17 API
        guard micGranted else {
            isAvailable = false
            lastError = "Microphone permission was denied."
            return false
        }
        return true
    }

    // MARK: Capture

    /// Begins live capture. Safe on simulators without audio input — flips
    /// isAvailable to false and returns instead of crashing.
    func start() {
        guard !isListening else { return }
        lastError = ""
        transcript = ""

        guard let recognizer, recognizer.isAvailable else {
            isAvailable = false
            lastError = "Speech recognition isn't available right now."
            return
        }

        // Claim the audio session for capture.
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .measurement,
                                    options: [.duckOthers, .defaultToSpeaker])
            try session.setActive(true)
        } catch {
            isAvailable = false
            lastError = "Couldn't open the audio session."
            return
        }
        guard session.isInputAvailable else {
            isAvailable = false
            lastError = "No microphone input is available."
            deactivateSession()
            return
        }

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        recognitionRequest = request

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        // A dead format (0 Hz / 0 ch) is the classic no-mic simulator signature —
        // installing a tap with it would throw an unrecoverable ObjC exception.
        guard format.sampleRate > 0, format.channelCount > 0 else {
            isAvailable = false
            lastError = "The microphone can't record on this device."
            recognitionRequest = nil
            deactivateSession()
            return
        }

        if tapInstalled { inputNode.removeTap(onBus: 0) }
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            // Audio-render thread: append + compute RMS only; hop for state writes.
            request.append(buffer)
            let level = Self.rms(of: buffer)
            Task { @MainActor [weak self] in self?.inputLevel = level }
        }
        tapInstalled = true

        recognitionTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if let result {
                    self.transcript = result.bestTranscription.formattedString
                    // Auto-stop when the service finalizes (silence timeout / 1-min cap).
                    // The transcript is left intact for the view to harvest.
                    if result.isFinal, self.isListening { self.teardownCapture() }
                }
                if error != nil, self.isListening {
                    // Service hiccup or cancellation — end quietly; any partial
                    // already captured stays usable.
                    self.teardownCapture()
                }
            }
        }

        audioEngine.prepare()
        do {
            try audioEngine.start()
        } catch {
            isAvailable = false
            lastError = "Couldn't start the microphone."
            teardownCapture()
            return
        }

        isAvailable = true
        isListening = true
    }

    /// Ends capture and returns what was heard (trimmed; "" if nothing).
    /// Resets the live transcript so observers don't double-consume it.
    @discardableResult
    func stop() -> String {
        let heard = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        transcript = ""
        guard isListening else { return heard }
        teardownCapture()
        return heard
    }

    // MARK: Teardown

    /// Shared teardown for manual stop, auto-stop (final result) and error paths.
    /// Leaves `transcript` alone — the auto-stop path needs it readable afterwards.
    private func teardownCapture() {
        if audioEngine.isRunning { audioEngine.stop() }
        recognitionRequest?.endAudio()
        if tapInstalled {
            audioEngine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
        recognitionTask?.cancel()   // fires the callback with an error; guarded by isListening
        recognitionTask = nil
        recognitionRequest = nil
        isListening = false
        inputLevel = 0
        deactivateSession()
    }

    /// Root-mean-square of the buffer's first channel, mapped to a perceptually-useful
    /// 0...1 range. Runs on the audio-render thread — pure math, no shared state.
    nonisolated private static func rms(of buffer: AVAudioPCMBuffer) -> Double {
        guard let data = buffer.floatChannelData?[0] else { return 0 }
        let count = Int(buffer.frameLength)
        guard count > 0 else { return 0 }
        var sum: Float = 0
        for i in 0..<count { sum += data[i] * data[i] }
        let rms = sqrt(sum / Float(count))
        // Typical speech RMS sits well under 1.0 — scale + clamp so normal talking
        // reads as a lively 0.3–0.9 rather than a barely-visible 0.01–0.05.
        return Double(min(1, max(0, rms * 12)))
    }

    private func deactivateSession() {
        do {
            try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        } catch {
            // Best-effort — deactivation can fail mid-route-change; never crash for it.
        }
    }
}
