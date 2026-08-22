import Foundation
import Observation
import SwiftUI
import PhotosUI

// Chat-tab session state. Conversations themselves live in AppStore (persisted);
// this owns which thread is open, the in-flight request, and the reply chrome
// (typing indicator, typewriter target, suggested chips).
@MainActor
@Observable
final class ChatStore {
    var currentConversationId: UUID?
    var isStreaming = false
    var chips: [String] = []
    /// The conversation the in-flight reply belongs to (typing indicator only shows there).
    var streamingConversationId: UUID?
    /// The just-arrived assistant message that should reveal with the typewriter effect.
    var typewriterMessageId: UUID?

    @ObservationIgnored private var inFlight: Task<Void, Never>?

    // MARK: Current thread

    func current(in store: AppStore) -> Conversation? {
        guard let id = currentConversationId else { return nil }
        return store.conversations.first { $0.id == id }
    }

    /// "New chat" — resets to a fresh, empty thread. The Conversation itself is created
    /// lazily on first send so abandoned new-chats never pollute the drawer.
    func newConversation(in store: AppStore) {
        cancel()
        currentConversationId = nil
        chips = []
        typewriterMessageId = nil
    }

    // MARK: Send

    func send(_ raw: String, store: AppStore) {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isStreaming else { return }
        chips = []
        typewriterMessageId = nil

        let convoId = ensureConversation(in: store, firstMessage: text)
        append(ChatMessage(role: .user, content: text), to: convoId, in: store)
        store.save()

        isStreaming = true
        streamingConversationId = convoId
        inFlight = Task {
            if let url = Self.videoURL(in: text) {
                await runAnalyzeVideo(url: url, convoId: convoId, store: store)
            } else {
                await runConverse(convoId: convoId, store: store)
            }
        }
    }

    // MARK: Send attached clips for editing (W5)

    /// The user attached video(s) + (optionally) an instruction and wants them
    /// edited. Appends the user turn + a live ClipEditCard, then runs the
    /// stitch → upload → analyze → edit pipeline, updating the card in place.
    /// Edit attached clips from chat. `config`/`toggles`/`editFormat`/`reactSourceURL`
    /// give chat the SAME steering as the record flow (composition style, b-roll/punch/
    /// music toggles, cut treatment, react source) — so a creator who'd rather upload than
    /// record on the spot gets the same fully-edited output. Defaults reproduce the old
    /// behavior (server-inferred toggles, no composition override).
    func sendClips(_ items: [PhotosPickerItem], instruction raw: String, store: AppStore,
                   config: [String: String]? = nil, toggles: EditToggles? = nil,
                   editFormat: String = "", reactSourceURL: String = "") {
        guard !items.isEmpty, !isStreaming else { return }
        let instruction = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        chips = []
        typewriterMessageId = nil

        let n = min(items.count, 4)
        let firstLine = instruction.isEmpty ? "Edit my \(n) clip\(n == 1 ? "" : "s")" : instruction
        let convoId = ensureConversation(in: store, firstMessage: firstLine)
        let userText = instruction.isEmpty
            ? "📎 Attached \(n) clip\(n == 1 ? "" : "s") to edit"
            : "\(instruction)\n📎 \(n) clip\(n == 1 ? "" : "s") attached"
        append(ChatMessage(role: .user, content: userText), to: convoId, in: store)

        var card = ChatMessage(role: .assistant, content: "")
        card.kind = .clipEdit
        card.clipEdit = ClipEditState(stage: .stitching, clipCount: n)
        append(card, to: convoId, in: store)
        store.save()

        isStreaming = true
        streamingConversationId = convoId
        let picked = Array(items.prefix(4))
        inFlight = Task {
            await runEditClips(items: picked, instruction: instruction,
                               cardId: card.id, convoId: convoId, store: store,
                               config: config, toggles: toggles,
                               editFormat: editFormat, reactSourceURL: reactSourceURL)
        }
    }

    /// Build 66: attach a clip FROM THE LIBRARY instead of Photos. Same pipeline as
    /// sendClips minus the import/stitch — the footage is already on disk. Uses the RAW
    /// take when it exists (re-editing an already-rendered cut would double captions);
    /// the cached render is the fallback for clips whose raw take is gone.
    func sendLibraryClip(_ clip: Clip, instruction raw: String, store: AppStore,
                         config: [String: String]? = nil, toggles: EditToggles? = nil,
                         editFormat: String = "", reactSourceURL: String = "") {
        guard !isStreaming else { return }
        guard let footagePath = [clip.localVideoPath, clip.renderLocalPath]
            .compactMap({ $0 })
            .first(where: { FileManager.default.fileExists(atPath: MediaStore.url(for: $0).path) })
        else { return }
        let instruction = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        chips = []
        typewriterMessageId = nil

        let name = clip.title.isEmpty ? "a clip from my library" : "\u{201C}\(clip.title)\u{201D}"
        let convoId = ensureConversation(in: store,
                                         firstMessage: instruction.isEmpty ? "Edit \(name)" : instruction)
        append(ChatMessage(role: .user,
                           content: instruction.isEmpty ? "📎 Attached \(name) to edit"
                                                        : "\(instruction)\n📎 \(name) attached"),
               to: convoId, in: store)

        var card = ChatMessage(role: .assistant, content: "")
        card.kind = .clipEdit
        card.clipEdit = ClipEditState(stage: .uploading, clipCount: 1)
        append(card, to: convoId, in: store)
        // Same recovery payload as the Photos path — a failed edit retries from disk.
        updateCard(card.id, in: convoId, store: store) {
            $0.footagePath = footagePath; $0.instruction = instruction
            $0.editFormat = editFormat; $0.reactSourceURL = reactSourceURL
            $0.config = config; $0.toggles = toggles
        }
        store.save()

        isStreaming = true
        streamingConversationId = convoId
        inFlight = Task {
            await runEditFromFootage(footagePath: footagePath, instruction: instruction,
                                     cardId: card.id, convoId: convoId, store: store,
                                     config: config, chosenToggles: toggles,
                                     editFormat: editFormat, reactSourceURL: reactSourceURL)
        }
    }

    /// Liveness v2: cards with a LIVE driving task this process. Static (survives the
    /// per-view ChatStore lifecycle) so AppStore.reconcileTransientState can tell a
    /// genuinely-orphaned persisted card (app was killed → set is empty) from one whose
    /// pipeline is mid-flight right now.
    static var liveEditCardIds: Set<UUID> = []

    private func updateCard(_ cardId: UUID, in convoId: UUID, store: AppStore,
                            _ mutate: (inout ClipEditState) -> Void) {
        guard let ci = store.conversations.firstIndex(where: { $0.id == convoId }),
              let mi = store.conversations[ci].messages.firstIndex(where: { $0.id == cardId }),
              var state = store.conversations[ci].messages[mi].clipEdit else { return }
        mutate(&state)
        store.conversations[ci].messages[mi].clipEdit = state
        store.save()
    }

    private func runEditClips(items: [PhotosPickerItem], instruction: String,
                              cardId: UUID, convoId: UUID, store: AppStore,
                              config: [String: String]? = nil, toggles chosenToggles: EditToggles? = nil,
                              editFormat: String = "", reactSourceURL: String = "") async {
        defer { isStreaming = false; streamingConversationId = nil }
        func fail(_ why: String) {
            BackendClient.shared.reportClientEvent("chat_edit_failed", detail: why)
            updateCard(cardId, in: convoId, store: store) { $0.stage = .failed; $0.detail = why }
        }

        // Liveness: own the card from the very first await. liveEditCardIds was only
        // registered in runEditFromFootage, so during a slow import the orphan sweep
        // (_reconcileChatCards) saw a transient-stage card with no live owner and flipped
        // a perfectly-alive card to .failed mid-pick. Removing twice (here + the inner
        // defer in runEditFromFootage) is harmless — it's a Set.
        ChatStore.liveEditCardIds.insert(cardId)
        defer { ChatStore.liveEditCardIds.remove(cardId) }

        // 1) + 2) Import the picked videos + stitch multiple takes into one source —
        // BOUNDED. This phase used to run outside ANY watchdog (the 10-min guard lives in
        // runEditFromFootage, which we haven't reached yet), and PhotosPicker's
        // loadTransferable can hang indefinitely on an iCloud-only original with no
        // connectivity — wedging the card AND isStreaming for the whole session. 120s
        // comfortably covers real multi-clip imports (streamed to disk, no transcode);
        // past it the card lands on .failed. There's no footagePath yet at this point, so
        // the retry is a re-pick — the detail copy says exactly that.
        enum ImportOutcome { case ready(String); case noVideos; case timedOut }
        let picked = items
        let outcome: ImportOutcome = await withTaskGroup(of: ImportOutcome.self) { group in
            group.addTask {
                let assets = await importPickedMedia(picked).filter { $0.isVideo }
                guard !assets.isEmpty else { return .noVideos }
                var path = assets[0].localPath
                if assets.count > 1 {
                    let urls = assets.map { MediaStore.url(for: $0.localPath) }
                    // saveFile streams the stitched output into the container — the old
                    // Data(contentsOf:) loaded the WHOLE stitched video into RAM and
                    // memory-killed the app on real multi-minute takes.
                    if let stitched = await VideoStitcher.stitch(urls),
                       let saved = MediaStore.saveFile(from: stitched, ext: "mov") {
                        path = saved
                    }   // stitch failure → fall back to the first clip rather than stranding the turn
                }
                return .ready(path)
            }
            group.addTask {
                try? await Task.sleep(nanoseconds: 120 * 1_000_000_000)
                return .timedOut
            }
            let first = await group.next() ?? .timedOut
            group.cancelAll()
            return first
        }
        guard !Task.isCancelled else { return }
        let footagePath: String
        switch outcome {
        case .ready(let path):
            footagePath = path
        case .noVideos:
            return fail("Those didn't come through as videos.")
        case .timedOut:
            return fail("Importing those videos took too long — please pick them again.")
        }

        // Stash the recovery payload on the card the moment the footage exists, so a failed
        // edit is retryable WITHOUT re-picking the videos (the picked items are gone by then).
        updateCard(cardId, in: convoId, store: store) {
            $0.footagePath = footagePath; $0.instruction = instruction
            $0.editFormat = editFormat; $0.reactSourceURL = reactSourceURL
            $0.config = config; $0.toggles = chosenToggles
        }
        await runEditFromFootage(footagePath: footagePath, instruction: instruction,
                                 cardId: cardId, convoId: convoId, store: store,
                                 config: config, chosenToggles: chosenToggles,
                                 editFormat: editFormat, reactSourceURL: reactSourceURL)
    }

    /// The pipeline from ready-on-disk footage onward (upload → analyze → confirm → render).
    /// Shared by the first run and the "Try again" retry, so a failed edit re-runs end-to-end
    /// from the same footage without re-importing.
    private func runEditFromFootage(footagePath: String, instruction: String,
                                    cardId: UUID, convoId: UUID, store: AppStore,
                                    config: [String: String]?, chosenToggles: EditToggles?,
                                    editFormat: String, reactSourceURL: String) async {
        func fail(_ why: String) {
            BackendClient.shared.reportClientEvent("chat_edit_failed", detail: why)
            updateCard(cardId, in: convoId, store: store) { $0.stage = .failed; $0.detail = why }
        }
        // Cancellation (Stop / a new conversation) previously returned mid-stage, stranding the
        // card on an .uploading/.analyzing/.editing spinner forever. Land it on a retryable
        // .failed instead so the creator can pick it back up (or dismiss it).
        func bail() {
            updateCard(cardId, in: convoId, store: store) {
                guard $0.stage != .ready else { return }
                $0.stage = .failed
                $0.detail = "Stopped — tap Try again."
            }
        }
        guard !Task.isCancelled else { return bail() }
        // Liveness v2: register as live (blocks the orphan sweep) + a hard watchdog —
        // the record path has a submit ceiling but this path had NONE, so a wedged poll
        // could spin the card forever even with the app foregrounded. Sized to the new
        // long-footage windows (beta 2026-08-22): the brief wait alone can honestly run
        // 6min (AppStore.pollForBrief) and the upload ahead of it several more on a big
        // take, so the old 10-min guard fired mid-pipeline on edits that were still fine.
        // Every stage inside is individually bounded now (compress budget ≤420s, stalled
        // PUTs restart, brief 360s, render polling is detached) — this only catches a
        // wedge none of them saw, so jobPollCeiling (20min) is the right belt-and-braces.
        ChatStore.liveEditCardIds.insert(cardId)
        let watchdog = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(AppStore.jobPollCeiling) * 1_000_000_000)
            guard !Task.isCancelled else { return }
            self?.updateCard(cardId, in: convoId, store: store) {
                guard $0.stage != .ready && $0.stage != .failed else { return }
                $0.stage = .failed
                $0.detail = "This edit took too long — tap Try again."
            }
        }
        defer {
            ChatStore.liveEditCardIds.remove(cardId)
            watchdog.cancel()
        }

        // 3) Upload the source.
        updateCard(cardId, in: convoId, store: store) { $0.stage = .uploading }
        // Build 78: keep the uploadId so the failure branch can read WHY it failed. An
        // over-cap source is a permanent condition ("check your connection" is a lie that
        // sends the creator round the same doomed loop) — the journal carries the reason.
        let chatUploadId = UUID().uuidString
        guard let sourceURL = await LiveClipEngine.mintAndUpload(uploadId: chatUploadId, footagePath: footagePath) else {
            let tooLarge = UploadJournal.shared.entry(uploadId: chatUploadId)?.lastErrorCode
                == MediaCompressor.tooLargeErrorCode
            return fail(tooLarge
                        ? "That video is too large to upload — try a shorter take or trim it first."
                        : "Couldn't upload your clips — check your connection and try again.")
        }
        guard !Task.isCancelled else { return }

        // 4) A minimal script carries the user's instruction into the edit. When the
        // creator picked a cut treatment in the config sheet, honor it; otherwise fall
        // back to their preferred style.
        let style = store.brand.preferredStyles.first ?? .talkingHead
        let script = Script(
            pillarName: "Your clips", title: instruction.isEmpty ? "Your edit" : String(instruction.prefix(40)),
            summary: "Edited from your footage", style: style.rawValue,
            formatId: style.formats.first ?? "myth-buster",
            hook: Hook(text: instruction.isEmpty ? "Your clips" : instruction, signal: .narrative, strength: 70),
            altHooks: [], body: instruction, cta: "",
            shotPlan: [], targetSeconds: 30, predictedScore: 70)

        // 5) Analyze → brief. Thread the creator's chosen composition style + toggles +
        // cut treatment + react source through, exactly like the record flow does.
        updateCard(cardId, in: convoId, store: store) { $0.stage = .analyzing }
        guard let job = await store.backend.createAnalyzeJob(
                sourceURL: sourceURL, script: script, customInstructions: instruction,
                reactSourceURL: reactSourceURL, editFormat: editFormat,
                config: config, toggles: chosenToggles),
              !job.jobId.isEmpty else {
            return fail("Couldn't start the edit — try again in a moment.")
        }
        // pollForBrief spans the full long-footage analyze window (360s, AppStore) — the
        // chat path shares it rather than running its own shorter cap, so a 2-3min take
        // gets the same patience here as on the record path.
        let brief = await store.pollForBrief(jobId: job.jobId)
        guard !Task.isCancelled else { return }
        if brief?.status == "failed" { return fail("The edit couldn't be planned from that footage.") }
        // nil = the analyze genuinely timed out. This used to FALL THROUGH to confirm,
        // which the backend rejects (no brief yet) — and confirmClips' transport fallback
        // then forked a DUPLICATE local mock job, so the card "succeeded" with the wrong
        // output while the real job was still (or never) finishing. A timeout is a
        // failure: land the card retryably and let "Try again" re-run from the stored
        // footage instead of quietly shipping a counterfeit result.
        guard brief != nil else {
            return fail("The edit took too long to analyze — tap Try again.")
        }

        // 6) Confirm → render (confirmClips inserts the tracked clip + polls + streak).
        // Creator-chosen toggles win over the server-inferred ones.
        updateCard(cardId, in: convoId, store: store) { $0.stage = .editing }
        let toggles = chosenToggles ?? brief?.toggles ?? job.toggles ?? EditToggles()
        let before = Set(store.clips.map { $0.id })
        await store.confirmClips(jobId: job.jobId, script: script, toggles: toggles,
                                 customInstructions: instruction, footagePath: footagePath)
        guard !Task.isCancelled else { return }
        let newClipId = store.clips.first(where: { !before.contains($0.id) })?.id
        updateCard(cardId, in: convoId, store: store) {
            $0.stage = .ready
            $0.resultClipId = newClipId
        }
    }

    /// "Try again" on a failed chat-edit card — re-runs the whole pipeline from the stored
    /// footage (no re-picking). Needs the recovery payload the run stashed once footage existed.
    func retryEdit(cardId: UUID, convoId: UUID, store: AppStore) {
        guard !isStreaming,
              let ci = store.conversations.firstIndex(where: { $0.id == convoId }),
              let mi = store.conversations[ci].messages.firstIndex(where: { $0.id == cardId }),
              let s = store.conversations[ci].messages[mi].clipEdit,
              !s.footagePath.isEmpty else { return }
        isStreaming = true
        streamingConversationId = convoId
        updateCard(cardId, in: convoId, store: store) { $0.stage = .uploading; $0.detail = "" }
        inFlight = Task {
            defer { isStreaming = false; streamingConversationId = nil }
            await runEditFromFootage(footagePath: s.footagePath, instruction: s.instruction,
                                     cardId: cardId, convoId: convoId, store: store,
                                     config: s.config, chosenToggles: s.toggles,
                                     editFormat: s.editFormat, reactSourceURL: s.reactSourceURL)
        }
    }

    /// Stop button — cancels the in-flight request; nothing is appended.
    func cancel() {
        inFlight?.cancel()
        inFlight = nil
        isStreaming = false
        streamingConversationId = nil
    }

    // MARK: Request runners

    private func runConverse(convoId: UUID, store: AppStore) async {
        let history = store.conversations.first(where: { $0.id == convoId })?.messages ?? []
        let result = await store.backend.converse(mode: "chat",
                                                  messages: Array(history.suffix(20)),
                                                  brand: store.brand, memory: store.memory,
                                                  persona: store.chatPersona ?? .closer,
                                                  responseLength: store.chatResponseLength ?? .medium)
        guard !Task.isCancelled else { return }
        defer { isStreaming = false; streamingConversationId = nil }

        guard let result else {
            appendAssistant(ChatMessage(role: .assistant,
                                        content: "Hit a snag — tap to try again."),
                            to: convoId, in: store)
            return
        }

        var reply = ChatMessage(role: .assistant, content: result.reply)
        // Key card kind off payload presence (mirrors the voice session) so intent-string
        // drift on the backend can never drop a card. Scripts win when both arrive.
        if let plan = result.plan {
            reply.kind = .dayPlan
            reply.plan = plan
        }
        if let scripts = result.scripts, !scripts.isEmpty {
            reply.kind = .scriptCard
            reply.scripts = scripts
            for s in scripts.reversed() { store.scripts.insert(s, at: 0) }
        }
        appendAssistant(reply, to: convoId, in: store)
        if convoId == currentConversationId { chips = result.chips }
        store.applyMemoryUpdates(result.memoryUpdates)
        store.save()
    }

    private func runAnalyzeVideo(url: String, convoId: UUID, store: AppStore) async {
        let analysis = await store.backend.analyzeVideo(url: url, brand: store.brand,
                                                        memory: store.memory)
        guard !Task.isCancelled else { return }
        defer { isStreaming = false; streamingConversationId = nil }

        guard let analysis else {
            appendAssistant(ChatMessage(role: .assistant,
                                        content: "Hit a snag — tap to try again."),
                            to: convoId, in: store)
            return
        }

        var reply = ChatMessage(role: .assistant,
                                content: "Broke it down — here's what's working and your version:")
        reply.kind = .videoAnalysis
        reply.analysis = analysis
        appendAssistant(reply, to: convoId, in: store)
        store.save()
    }

    // MARK: Conversation mutations (always write through store.conversations)

    private func ensureConversation(in store: AppStore, firstMessage: String) -> UUID {
        if let id = currentConversationId,
           store.conversations.contains(where: { $0.id == id }) {
            return id
        }
        var convo = Conversation()
        convo.title = Self.title(from: firstMessage)
        store.conversations.insert(convo, at: 0)
        currentConversationId = convo.id
        return convo.id
    }

    private func append(_ message: ChatMessage, to convoId: UUID, in store: AppStore) {
        guard let idx = store.conversations.firstIndex(where: { $0.id == convoId }) else { return }
        store.conversations[idx].messages.append(message)
        store.conversations[idx].updatedAt = Date()
    }

    private func appendAssistant(_ message: ChatMessage, to convoId: UUID, in store: AppStore) {
        typewriterMessageId = message.id
        append(message, to: convoId, in: store)
    }

    // MARK: Helpers

    /// Title = first 4 words of the first message.
    static func title(from text: String) -> String {
        let words = text.split(whereSeparator: { $0.isWhitespace }).prefix(4)
        let t = words.joined(separator: " ")
        return t.isEmpty ? "New chat" : t
    }

    /// Pull a pasteable video link out of a message (TikTok / Instagram / YouTube).
    static func videoURL(in text: String) -> String? {
        let markers = ["tiktok.com", "instagram.com", "youtu"]
        let lower = text.lowercased()
        guard markers.contains(where: { lower.contains($0) }) else { return nil }
        let tokens = text.split(whereSeparator: { $0.isWhitespace })
        guard let match = tokens.first(where: { token in
            let l = token.lowercased()
            return markers.contains { l.contains($0) }
        }) else { return nil }
        var url = String(match).trimmingCharacters(in: CharacterSet(charactersIn: ".,;:!?()[]<>\"'"))
        if !url.lowercased().hasPrefix("http") { url = "https://" + url }
        return url
    }
}
