import SwiftUI

// The voice session — morning thoughts in, strategy + memory out.
// Phase 6 adds SFSpeechRecognizer capture + TTS playback; the typed path below
// is permanent (sim STT flakiness + Maestro + noisy environments).
struct VoiceSessionView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var exchanges: [ChatMessage] = []
    @State private var draft = ""
    @State private var thinking = false
    @State private var lastChips: [String] = []
    @FocusState private var inputFocused: Bool

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
    }

    // MARK: Orb (state: idle / thinking)

    private var orb: some View {
        ZStack {
            Circle().fill(Palette.accent.opacity(0.10)).frame(width: 120, height: 120)
            ZStack {
                Circle().fill(.ultraThinMaterial)
                Circle().fill(LinearGradient(colors: [Color.white.opacity(0.9), Palette.accent.opacity(0.12)],
                                             startPoint: .topLeading, endPoint: .bottomTrailing))
                Image(systemName: thinking ? "ellipsis" : "waveform")
                    .font(.system(size: 26, weight: .medium))
                    .foregroundStyle(Palette.accent)
                    .symbolEffect(.variableColor.iterative, options: .repeating, isActive: thinking)
            }
            .frame(width: 84, height: 84)
            .overlay(Circle().strokeBorder(Color.white.opacity(0.8), lineWidth: 1))
            .shadow(color: Palette.accent.opacity(0.22), radius: 18, x: 0, y: 8)
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

    // MARK: Composer (typed path — Phase 6 adds the hold-to-talk mic)

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
                exchanges.append(ChatMessage(role: .assistant,
                                             content: "I couldn't reach the studio just now — try that again in a moment."))
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
            // Phase 6: TTS playback of result.reply lands here.
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
