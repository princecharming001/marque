import Foundation
import Observation
import AVFoundation

// Spoken replies for the voice session (Phase 6).
//
// Two-tier playback:
//   1. Backend TTS (/v1/tts — ElevenLabs when the server holds a key) → AVAudioPlayer.
//   2. Keyless/offline fallback → AVSpeechSynthesizer with the best local en-US voice.
//
// Audio-session strategy (the record ⇄ playback dance):
// - .playback(.spokenAudio) is claimed right before audio actually starts — never
//   during the TTS fetch — so the mic can be tapped mid-fetch without a session fight.
// - MANUAL stops never deactivate the session: they're usually followed within
//   milliseconds by SpeechRecognizer claiming .playAndRecord, and a late async
//   deactivate would yank the session out from under the freshly started engine.
// - Only NATURAL finishes (didFinish / player-finished) deactivate, politely, with
//   .notifyOthersOnDeactivation so ducked audio can resume. All activation errors
//   are swallowed — audio-session quirks must never crash the app.
@MainActor
@Observable
final class VoicePlayback: NSObject, AVAudioPlayerDelegate, AVSpeechSynthesizerDelegate {

    /// True while a reply is audibly playing (either tier).
    var isSpeaking = false

    private var player: AVAudioPlayer?      // strong ref — the player stops if released
    private let synthesizer = AVSpeechSynthesizer()
    private var usingSynth = false          // which tier owns the current utterance
    private var generation = 0              // invalidates in-flight TTS fetches on stop

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    // MARK: Speak

    /// Speaks `text`: backend mp3 if the server can mint one, local synthesis otherwise.
    /// Returns once playback has started; the delegates clear `isSpeaking` at the end.
    func speak(_ text: String) async {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }

        stopSpeaking()                     // one voice at a time
        generation += 1
        let ticket = generation

        let mp3 = await BackendClient.shared.tts(text: clean)   // nil in keyless/mock mode
        guard ticket == generation else { return }              // stopped/superseded mid-fetch

        activatePlaybackSession()
        if let mp3, playData(mp3) { return }
        speakLocally(clean)
    }

    /// Stops both tiers and invalidates any in-flight TTS fetch. Never touches the
    /// audio session (see header — the mic may be about to claim it).
    func stopSpeaking() {
        generation += 1
        usingSynth = false
        isSpeaking = false
        if let player {
            player.stop()                  // manual stop fires no delegate callback
            self.player = nil
        }
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)   // fires didCancel (guarded no-op)
        }
    }

    // MARK: Tier 1 — backend mp3

    private func playData(_ data: Data) -> Bool {
        do {
            let p = try AVAudioPlayer(data: data)
            p.delegate = self
            guard p.play() else { return false }
            player = p
            usingSynth = false
            isSpeaking = true
            return true
        } catch {
            return false                   // undecodable bytes → local fallback
        }
    }

    // MARK: Tier 2 — local synthesis

    private func speakLocally(_ text: String) {
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = Self.preferredVoice
        utterance.rate = 0.5
        usingSynth = true
        isSpeaking = true
        synthesizer.speak(utterance)
    }

    /// Best local en-US voice: premium > enhanced > default (novelty voices rank last).
    private static let preferredVoice: AVSpeechSynthesisVoice? = {
        func rank(_ quality: AVSpeechSynthesisVoiceQuality) -> Int {
            switch quality {
            case .premium: return 3
            case .enhanced: return 2
            default: return 1
            }
        }
        let american = AVSpeechSynthesisVoice.speechVoices().filter { $0.language == "en-US" }
        return american.max { rank($0.quality) < rank($1.quality) }
            ?? AVSpeechSynthesisVoice(language: "en-US")
    }()

    // MARK: Audio session

    private func activatePlaybackSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playback, mode: .spokenAudio)
            try session.setActive(true)
        } catch {
            // Silent by design — playback still tries; worst case audio stays quiet.
        }
    }

    private func deactivateSessionPolitely() {
        guard !isSpeaking else { return }
        do {
            try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        } catch {
            // Best-effort.
        }
    }

    // MARK: Delegates (arrive off-main; hop to the main actor before touching state)

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in self.playerFinished(player) }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        Task { @MainActor in self.playerFinished(player) }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in self.synthFinishedNaturally() }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in self.synthCancelled() }
    }

    private func playerFinished(_ finished: AVAudioPlayer) {
        guard finished === player else { return }   // stale delegate from a superseded player
        player = nil
        isSpeaking = false
        deactivateSessionPolitely()
    }

    private func synthFinishedNaturally() {
        guard usingSynth, !synthesizer.isSpeaking else { return }   // superseded already
        usingSynth = false
        isSpeaking = false
        deactivateSessionPolitely()
    }

    private func synthCancelled() {
        // Manual stops already reset state in stopSpeaking(); this catches external
        // cancellations (audio interruptions). No session deactivation here — the
        // interrupter owns the session now, and a mic start may be racing us.
        if usingSynth {
            usingSynth = false
            isSpeaking = false
        }
    }
}
