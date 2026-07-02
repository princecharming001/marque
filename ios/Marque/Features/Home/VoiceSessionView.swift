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
            // Grabber-adjacent header
            HStack {
                Text("MORNING SESSION").font(AppFont.micro).tracking(Track.label)
                    .foregroundStyle(Palette.textTertiary)
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark").font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Palette.textSecondary)
                        .frame(width: 30, height: 30)
                        .background(Palette.surfaceSunken).clipShape(Circle())
                }
                .accessibilityIdentifier("voice.close")
            }
            .padding(.horizontal, Space.screenH).padding(.top, Space.lg)

            ScrollViewReader { proxy in
                ScrollView {
                    VStack(spacing: Space.lg) {
                        orb
                            .padding(.top, Space.lg)
                        if speech.isListening, !speech.transcript.isEmpty {
                            Text(speech.transcript)
                                .font(AppFont.body).italic()
                                .foregroundStyle(Palette.textSecondary)
                                .multilineTextAlignment(.center)
                                .padding(.horizontal, Space.xl)
                        }
                        micButton
                        if !speech.isAvailable {
                            Text("Mic unavailable — type below")
                                .font(AppFont.caption)
                                .foregroundStyle(Palette.textTertiary)
                        }
                        if exchanges.isEmpty {
                            Text("Tell me what's on your mind — an idea, an angle, a question about your content. I remember what matters.")
                                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                                .multilineTextAlignment(.center)
                                .padding(.horizontal, Space.xl)
                        }
                        ForEach(exchanges) { m in
                            exchangeRow(m).id(m.id)
                        }
                        if thinking {
                            HStack(spacing: 6) {
                                ProgressView().tint(Palette.accent)
                                Text("Marque is thinking…").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
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
        }
    }

    // MARK: Orb (state: idle / listening / thinking / speaking)

    private enum OrbState { case idle, listening, thinking, speaking }

    private var orbState: OrbState {
        if speech.isListening { return .listening }
        if thinking { return .thinking }
        if playback.isSpeaking { return .speaking }
        return .idle
    }

    private var orb: some View {
        ZStack {
            if orbState == .listening { ListeningRings() }
            Circle()
                .fill(Palette.accent.opacity(orbState == .speaking ? 0.16 : 0.10))
                .frame(width: 120, height: 120)
            ZStack {
                Circle().fill(.ultraThinMaterial)
                Circle().fill(orbFill)
                orbSymbol
                    .font(.system(size: 26, weight: .medium))
                    .foregroundStyle(orbState == .listening ? Color.white : Palette.accent)
            }
            .frame(width: 84, height: 84)
            .overlay(Circle().strokeBorder(Color.white.opacity(0.8), lineWidth: 1))
            .shadow(color: Palette.accent.opacity(orbState == .speaking ? 0.4 : 0.22),
                    radius: orbState == .speaking ? 24 : 18, x: 0, y: 8)
        }
        .animation(Motion.quick, value: orbState)
    }

    private var orbFill: LinearGradient {
        orbState == .listening
            ? LinearGradient(colors: [Palette.accent, Palette.accent.opacity(0.75)],
                             startPoint: .topLeading, endPoint: .bottomTrailing)
            : LinearGradient(colors: [Color.white.opacity(0.9), Palette.accent.opacity(0.12)],
                             startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    @ViewBuilder
    private var orbSymbol: some View {
        switch orbState {
        case .idle:
            Image(systemName: "waveform")
        case .listening:
            Image(systemName: "waveform")
        case .thinking:
            Image(systemName: "ellipsis")
                .symbolEffect(.variableColor.iterative, options: .repeating)
        case .speaking:
            Image(systemName: "waveform")
                .symbolEffect(.variableColor.iterative, options: .repeating)
        }
    }

    // MARK: Transcript rows

    @ViewBuilder
    private func exchangeRow(_ m: ChatMessage) -> some View {
        if m.role == .user {
            HStack {
                Spacer(minLength: 40)
                Text(m.content)
                    .font(AppFont.body).foregroundStyle(Palette.textPrimary)
                    .padding(.horizontal, Space.md).padding(.vertical, 10)
                    .background(Palette.surfaceSunken)
                    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            }
        } else {
            VStack(alignment: .leading, spacing: Space.sm) {
                Text(m.content)
                    .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
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
                    Button { send(chip) } label: {
                        Text(chip).font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                            .padding(.horizontal, Space.md).frame(height: 36)
                            .background(Palette.surfaceRaised)
                            .clipShape(Capsule())
                            .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, Space.screenH)
        }
        .padding(.horizontal, -Space.screenH)
    }

    // MARK: Mic (tap to talk / tap to stop — the typed composer stays as the fallback)

    private var micButton: some View {
        Button(action: micTapped) {
            Image(systemName: speech.isListening ? "stop.fill" : "mic.fill")
                .font(.system(size: 26, weight: .medium))
                .foregroundStyle(Palette.onInk)
                .frame(width: 72, height: 72)
                .background(speech.isListening ? Palette.critical : Palette.ink)
                .clipShape(Circle())
                .shadow(color: Palette.shadowWarm.opacity(0.18), radius: 14, x: 0, y: 6)
        }
        .buttonStyle(.plain)
        .disabled(thinking)
        .opacity(thinking ? 0.45 : 1)
        .accessibilityIdentifier("voice.mic")
        .sensoryFeedback(.impact, trigger: micTaps)
        .animation(Motion.quick, value: speech.isListening)
    }

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
            TextField("Say it or type it…", text: $draft, axis: .vertical)
                .font(AppFont.bodyL)
                .lineLimit(1...4)
                .focused($inputFocused)
                .padding(.horizontal, Space.md).padding(.vertical, 12)
                .background(Palette.surface)
                .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
                .accessibilityIdentifier("voice.textInput")
            Button {
                send(draft)
            } label: {
                Image(systemName: "arrow.up")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Palette.onInk)
                    .frame(width: 42, height: 42)
                    .background(draft.trimmingCharacters(in: .whitespaces).isEmpty ? Palette.textTertiary : Palette.ink)
                    .clipShape(Circle())
            }
            .buttonStyle(.plain)
            .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty || thinking)
            .accessibilityIdentifier("voice.send")
        }
        .padding(.horizontal, Space.screenH)
        .padding(.vertical, Space.md)
        .background(.ultraThinMaterial)
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
                                                      brand: store.brand, memory: store.memory)
            thinking = false
            guard let result else {
                let apology = "I couldn't reach the studio just now — try that again in a moment."
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

// MARK: - Listening rings (expanding accent pulses behind the orb while the mic is live)

private struct ListeningRings: View {
    @State private var pulsing = false

    var body: some View {
        ZStack {
            ring(delay: 0)
            ring(delay: 0.7)
        }
        .onAppear { pulsing = true }
    }

    private func ring(delay: Double) -> some View {
        Circle()
            .stroke(Palette.accent.opacity(0.5), lineWidth: 2)
            .frame(width: 104, height: 104)
            .scaleEffect(pulsing ? 1.55 : 0.92)
            .opacity(pulsing ? 0 : 0.8)
            .animation(.easeOut(duration: 1.6).repeatForever(autoreverses: false).delay(delay),
                       value: pulsing)
    }
}

// MARK: - Small cards used in the session transcript

struct DayPlanCard: View {
    let plan: DayPlan
    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            SectionLabel(text: "Your day", accent: Palette.accent)
            ForEach(plan.blocks) { b in
                HStack(alignment: .top, spacing: Space.md) {
                    Text(b.time)
                        .font(AppFont.caption).foregroundStyle(Palette.accent)
                        .frame(width: 46, alignment: .leading)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(b.action).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        Text(b.detail).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .marqueCard(padding: Space.md)
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
                ScoreBadge(score: script.predictedScore).scaleEffect(0.85)
            }
            Text(script.title.isEmpty ? script.hook.text : script.title)
                .font(AppFont.serifM).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text("\u{201C}\(script.hook.text)\u{201D}")
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary).lineLimit(2)
            HStack(spacing: Space.sm) {
                Button {
                    store.readyScript(script, source: .chat)
                    router.pendingFilmScriptId = script.id
                    dismiss()
                    router.showFilm = true
                } label: {
                    Text("Film this").font(AppFont.callout).foregroundStyle(Palette.onInk)
                        .padding(.horizontal, Space.md).frame(height: 32)
                        .background(Palette.ink).clipShape(Capsule())
                }
                .buttonStyle(.plain)
                Button {
                    store.readyScript(script, source: .chat)
                } label: {
                    Label("Save for later", systemImage: "bookmark")
                        .font(AppFont.callout).foregroundStyle(Palette.accent)
                }
                .buttonStyle(.plain)
            }
        }
        .marqueCard(padding: Space.md)
    }
}
