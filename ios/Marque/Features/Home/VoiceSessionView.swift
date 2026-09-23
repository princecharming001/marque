import SwiftUI

// The voice session — morning thoughts in, strategy + memory out.
// Phase 6: SFSpeechRecognizer tap-to-talk capture + spoken replies (backend TTS with
// AVSpeechSynthesizer fallback). The typed path below is permanent (sim STT
// flakiness + Maestro + noisy environments).
struct VoiceSessionView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var exchanges: [ChatMessage] = []
    @State private var draft = ""
    @State private var thinking = false
    @State private var lastChips: [String] = []
    @FocusState private var inputFocused: Bool

    // Phase 6: speech in / speech out
    @State private var speech = SpeechRecognizer()
    @State private var playback = VoicePlayback()
    @State private var micTaps = 0
    @State private var sessionLive = true   // gates late replies from speaking after dismissal

    var body: some View {
        VStack(spacing: 0) {
            // Sheet header (DESIGN.md §5): eyebrow + centered lowercase title, close trailing.
            ZStack {
                VStack(spacing: Space.xs) {
                    Text("MORNING SESSION").font(AppFont.eyebrow).tracking(Track.eyebrow)
                        .foregroundStyle(Palette.textSecondary)
                    Text("talk to yuni.")
                        .font(AppFont.title1).tracking(-0.3)
                        .foregroundStyle(Palette.textPrimary)
                        .lineLimit(1).minimumScaleFactor(0.8)
                        .accessibilityAddTraits(.isHeader)
                }
                .padding(.horizontal, 52)
                HStack {
                    Spacer()
                    Button { dismiss() } label: {
                        Image(systemName: "xmark").font(.system(size: 20, weight: .regular))
                            .foregroundStyle(Palette.textPrimary)
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(PressableStyle(dim: 0.6))
                    .accessibilityLabel("Close")
                    .accessibilityIdentifier("voice.close")
                }
                .padding(.horizontal, Space.xs)
            }
            .padding(.top, Space.xl)

            ScrollViewReader { proxy in
                ScrollView {
                    VStack(spacing: Space.lg) {
                        // The orb IS the mic — tap it to talk, tap again to stop.
                        // (No separate mic button; the orb visualization already
                        // conveys idle/listening/thinking/speaking state.)
                        Button(action: micTapped) { orb }
                            .buttonStyle(.plain)
                            .disabled(thinking)
                            .opacity(thinking ? 0.55 : 1)
                            .accessibilityIdentifier("voice.mic")
                            .accessibilityLabel(speech.isListening ? "Stop listening" : "Tap to talk")
                            .sensoryFeedback(.impact, trigger: micTaps)
                            .padding(.top, Space.xl)
                        Text(speech.isListening ? "Listening… tap the orb to stop" : "Tap the orb to talk")
                            .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                            .animation(Motion.quick, value: speech.isListening)
                        if speech.isListening, !speech.transcript.isEmpty {
                            Text(speech.transcript)
                                .font(AppFont.bodyText)
                                .foregroundStyle(Palette.textSecondary)
                                .multilineTextAlignment(.center)
                                .padding(.horizontal, Space.xl)
                        }
                        if !speech.isAvailable {
                            Label("Mic unavailable, type below", systemImage: "mic.slash")
                                .font(AppFont.caption)
                                .foregroundStyle(Palette.textSecondary)
                        }
                        if exchanges.isEmpty {
                            Text("Tell me what's on your mind, an idea, an angle, a question about your content. I remember what matters.")
                                .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                                .multilineTextAlignment(.center)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(.horizontal, Space.xl)
                        }
                        ForEach(exchanges) { m in
                            exchangeRow(m).id(m.id)
                        }
                        if thinking {
                            HStack(spacing: Space.sm) {
                                ProgressView().tint(Palette.textSecondary)
                                Text("Yunicorn is thinking…").font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                            }
                        }
                        if !lastChips.isEmpty && !thinking {
                            chipsRow
                        }
                    }
                    .padding(.horizontal, Space.screenH)
                    .padding(.bottom, Space.xl)
                }
                .onChange(of: exchanges.count) { _, _ in
                    if let last = exchanges.last { withAnimation(Motion.quick) { proxy.scrollTo(last.id, anchor: .bottom) } }
                }
            }

            composer
        }
        .background(Palette.canvas.ignoresSafeArea())
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .onChange(of: speech.isListening) { wasListening, isListening in
            // Auto-stop (the recognizer finalized on its own — silence timeout or the
            // 1-minute cap): harvest what was heard and send it through the normal path.
            // Manual stops clear the transcript before this fires, so no double-send.
            if wasListening, !isListening {
                let heard = speech.transcript.trimmingCharacters(in: .whitespacesAndNewlines)
                speech.transcript = ""
                if !heard.isEmpty { send(heard) }
            }
        }
        .onAppear { sessionLive = true }
        .onDisappear {
            sessionLive = false
            playback.stopSpeaking()
            _ = speech.stop()
            distillSessionMemory()      // I-8: pull anything the per-turn extraction missed
        }
    }

    // MARK: Orb (mode: idle / listening / thinking / speaking) — the shared Yunicorn orb,
    // volume-reactive off live mic input (listening) or TTS output (speaking) levels.

    private var orbMode: VoiceOrb.Mode {
        if speech.isListening { return .listening }
        if thinking { return .thinking }
        if playback.isSpeaking { return .speaking }
        return .idle
    }

    private var orbLevel: Double {
        if speech.isListening { return speech.inputLevel }
        if playback.isSpeaking { return playback.outputLevel }
        return 0
    }

    private var orb: some View {
        VoiceOrb(mode: orbMode, level: orbLevel, size: 148)
    }

    // MARK: Transcript rows

    @ViewBuilder
    private func exchangeRow(_ m: ChatMessage) -> some View {
        if m.role == .user {
            HStack {
                Spacer(minLength: 40)
                Text(m.content)
                    .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, Space.md).padding(.vertical, 12)
                    .background(Palette.surfaceSunken)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
            }
        } else {
            VStack(alignment: .leading, spacing: Space.sm) {
                Text(m.content)
                    .font(AppFont.bodyLarge).foregroundStyle(Palette.textPrimary)
                    .lineSpacing(4)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                if let plan = m.plan { DayPlanCard(plan: plan) }
                if let scripts = m.scripts, !scripts.isEmpty {
                    ForEach(scripts) { s in
                        VoiceScriptRow(script: s)
                    }
                }
            }
        }
    }

    private var chipsRow: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.sm) {
                ForEach(lastChips, id: \.self) { chip in
                    DSChip(title: chip) { send(chip) }
                }
            }
            .padding(.horizontal, Space.screenH)
        }
        .padding(.horizontal, -Space.screenH)
    }

    // MARK: Mic (tap the orb to talk / tap to stop — the typed composer stays as the fallback)

    private func micTapped() {
        micTaps += 1
        if speech.isListening {
            // Tap-to-stop: harvest the take and send it through the normal path.
            let heard = speech.stop()
            if !heard.isEmpty { send(heard) }
            return
        }
        guard !thinking else { return }
        // Barge-in: silence the current reply (and any in-flight TTS fetch) first.
        playback.stopSpeaking()
        Task {
            guard await speech.requestAuthorization() else { return }  // denial → caption via isAvailable
            speech.start()
        }
    }

    // MARK: Composer (typed path — permanent; also the sim-STT fallback)

    private var composer: some View {
        HStack(spacing: Space.sm) {
            // Search-capsule field (surfaceSunken); grows to 4 lines, so the corner radius is
            // half the single-line height rather than a true capsule.
            TextField("Say it or type it…", text: $draft, axis: .vertical)
                .font(AppFont.bodyText)
                .foregroundStyle(Palette.textPrimary)
                .tint(Palette.textPrimary)
                .lineLimit(1...4)
                .focused($inputFocused)
                .padding(.horizontal, Space.lg).padding(.vertical, 14)
                .frame(minHeight: 52)
                .background(Palette.surfaceSunken)
                .clipShape(RoundedRectangle(cornerRadius: 26, style: .continuous))
                .accessibilityIdentifier("voice.textInput")
            Button {
                send(draft)
            } label: {
                // Circular ink send; disabled = sunken fill + tertiary glyph (DESIGN.md §5).
                let empty = draft.trimmingCharacters(in: .whitespaces).isEmpty
                Image(systemName: "arrow.up")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(empty ? Palette.textTertiary : Palette.onInk)
                    .frame(width: 48, height: 48)
                    .background(Circle().fill(empty ? Palette.surfaceSunken : Palette.ink))
                    .contentShape(Circle())
                    .animation(Motion.quick, value: empty)
            }
            .buttonStyle(PressableStyle(dim: 0.85, scale: 0.92))
            .accessibilityLabel("Send")
            .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty || thinking)
            .accessibilityIdentifier("voice.send")
        }
        .padding(.horizontal, Space.screenH)
        .padding(.top, Space.sm)
        .padding(.bottom, Space.md)
        .background(Palette.canvas)
        .overlay(alignment: .top) { Rectangle().fill(Palette.hairline).frame(height: 1).opacity(0.6) }
    }

    // MARK: Send → converse → memory + voice-notes log

    private func send(_ text: String) {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, !thinking else { return }
        if speech.isListening { _ = speech.stop() }   // a typed send mid-capture wins; drop the take
        playback.stopSpeaking()                        // a new exchange silences the current reply
        draft = ""
        let userMsg = ChatMessage(role: .user, content: clean)
        exchanges.append(userMsg)
        thinking = true
        lastChips = []
        Task {
            let result = await store.backend.converse(mode: "voice", messages: exchanges,
                                                      brand: store.brand, memory: store.memory,
                                                      persona: store.chatPersona ?? .closer,
                                                      responseLength: store.chatResponseLength ?? .medium)
            thinking = false
            guard let result else {
                let apology = "I couldn't reach the studio just now, try that again in a moment."
                exchanges.append(ChatMessage(role: .assistant, content: apology))
                if sessionLive { Task { await playback.speak(apology) } }
                return
            }
            var reply = ChatMessage(role: .assistant, content: result.reply)
            if let plan = result.plan { reply.kind = .dayPlan; reply.plan = plan }
            if let scripts = result.scripts, !scripts.isEmpty {
                reply.kind = .scriptCard; reply.scripts = scripts
                for s in scripts { store.scripts.insert(s, at: 0) }
            }
            exchanges.append(reply)
            lastChips = result.chips
            store.applyMemoryUpdates(result.memoryUpdates)
            logToVoiceNotes(user: userMsg, reply: reply)
            // Phase 6: speak every assistant reply (speak() stops any current playback first).
            if sessionLive { Task { await playback.speak(result.reply) } }
        }
    }

    /// I-8: a "yap session" is captured only per-turn while talking; on close, ask the backend
    /// to re-read the whole transcript and pull any durable memory the turn extraction missed.
    /// Fire-and-forget, 404-tolerant, and a no-op for short sessions.
    private func distillSessionMemory() {
        let userTurns = exchanges.filter { $0.role == .user }
        guard userTurns.count >= 2 else { return }
        let transcript = exchanges.map { ["role": $0.role == .user ? "user" : "assistant", "text": $0.content] }
        let mem = store.memory, brand = store.brand
        Task {
            let updates = await store.backend.distillMemory(transcript: transcript, memory: mem, brand: brand)
            if !updates.isEmpty { await MainActor.run { store.applyMemoryUpdates(updates) } }
        }
    }

    /// Voice sessions are reviewable later — they append to a pinned "Voice notes" thread in Chat.
    private func logToVoiceNotes(user: ChatMessage, reply: ChatMessage) {
        if let idx = store.conversations.firstIndex(where: { $0.isVoiceNotes }) {
            store.conversations[idx].messages.append(contentsOf: [user, reply])
            store.conversations[idx].updatedAt = Date()
        } else {
            var convo = Conversation(title: "Voice notes", isVoiceNotes: true)
            convo.messages = [user, reply]
            store.conversations.insert(convo, at: 0)
        }
        store.save()
    }
}

// MARK: - Small cards used in the session transcript

/// The day plan as Journey-style timeline rows: one surface group, a row per block
/// (time leading, action as the headline, detail as secondary copy), inset dividers.
struct DayPlanCard: View {
    let plan: DayPlan
    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            SectionLabel(text: "Your day")
                .padding(.horizontal, Space.rowPad)
            VStack(spacing: 0) {
                ForEach(Array(plan.blocks.enumerated()), id: \.element.id) { i, b in
                    if i > 0 { DSRowDivider(inset: Space.rowPad + 56 + Space.md) }
                    HStack(alignment: .firstTextBaseline, spacing: Space.md) {
                        Text(b.time)
                            .font(AppFont.caption.weight(.semibold)).foregroundStyle(Palette.textPrimary)
                            .lineLimit(1).minimumScaleFactor(0.8)
                            .frame(width: 56, alignment: .leading)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(b.action).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                            Text(b.detail).font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, Space.rowPad)
                    .padding(.vertical, 14)
                    .accessibilityElement(children: .combine)
                }
            }
            .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.surface))
        }
    }
}

struct VoiceScriptRow: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(\.dismiss) private var dismiss
    let script: Script

    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack {
                FormatTag(formatId: script.formatId)
                Spacer()
            }
            Text(script.title.isEmpty ? script.hook.text : script.title)
                .font(AppFont.title2).tracking(-0.2).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text("\u{201C}\(script.hook.text)\u{201D}")
                .font(AppFont.supporting).foregroundStyle(Palette.textSecondary).lineLimit(2)
            HStack(spacing: Space.sm) {
                Button {
                    store.readyScript(script, source: .chat)
                    router.pendingFilmScriptId = script.id
                    dismiss()
                    router.showFilm = true
                } label: {
                    Text("Film this")
                }
                .buttonStyle(.ds(.primary, height: 44))
                Button {
                    store.readyScript(script, source: .chat)
                } label: {
                    Label("Save for later", systemImage: "bookmark")
                }
                .buttonStyle(.ds(.outline, height: 44))
            }
            .padding(.top, Space.xs)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard(.surface)
    }
}
