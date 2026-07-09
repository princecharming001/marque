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
    func sendClips(_ items: [PhotosPickerItem], instruction raw: String, store: AppStore) {
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
                               cardId: card.id, convoId: convoId, store: store)
        }
    }

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
                              cardId: UUID, convoId: UUID, store: AppStore) async {
        defer { isStreaming = false; streamingConversationId = nil }
        func fail(_ why: String) {
            updateCard(cardId, in: convoId, store: store) { $0.stage = .failed; $0.detail = why }
        }

        // 1) Import the picked videos into the app container.
        let assets = await importPickedMedia(items).filter { $0.isVideo }
        guard !assets.isEmpty else { return fail("Those didn't come through as videos.") }
        guard !Task.isCancelled else { return }

        // 2) Stitch multiple takes into one source (single take → use as-is).
        var footagePath = assets[0].localPath
        if assets.count > 1 {
            let urls = assets.map { MediaStore.url(for: $0.localPath) }
            if let stitched = await VideoStitcher.stitch(urls),
               let data = try? Data(contentsOf: stitched) {
                footagePath = MediaStore.save(data, ext: "mov")
            }   // stitch failure → fall back to the first clip rather than stranding the turn
        }
        guard !Task.isCancelled else { return }

        // 3) Upload the source.
        updateCard(cardId, in: convoId, store: store) { $0.stage = .uploading }
        guard let sourceURL = await LiveClipEngine.mintAndUpload(footagePath: footagePath) else {
            return fail("Couldn't upload your clips — check your connection and try again.")
        }
        guard !Task.isCancelled else { return }

        // 4) A minimal script carries the user's instruction into the edit.
        let style = store.brand.preferredStyles.first ?? .talkingHead
        let script = Script(
            pillarName: "Your clips", title: instruction.isEmpty ? "Your edit" : String(instruction.prefix(40)),
            summary: "Edited from your footage", style: style.rawValue,
            formatId: style.formats.first ?? "myth-buster",
            hook: Hook(text: instruction.isEmpty ? "Your clips" : instruction, signal: .narrative, strength: 70),
            altHooks: [], body: instruction, cta: "",
            shotPlan: [], targetSeconds: 30, predictedScore: 70)

        // 5) Analyze → brief.
        updateCard(cardId, in: convoId, store: store) { $0.stage = .analyzing }
        guard let job = await store.backend.createAnalyzeJob(
                sourceURL: sourceURL, script: script, customInstructions: instruction),
              !job.jobId.isEmpty else {
            return fail("Couldn't start the edit — try again in a moment.")
        }
        let brief = await store.pollForBrief(jobId: job.jobId)
        guard !Task.isCancelled else { return }
        if brief?.status == "failed" { return fail("The edit couldn't be planned from that footage.") }

        // 6) Confirm → render (confirmClips inserts the tracked clip + polls + streak).
        updateCard(cardId, in: convoId, store: store) { $0.stage = .editing }
        let toggles = brief?.toggles ?? job.toggles ?? EditToggles()
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
