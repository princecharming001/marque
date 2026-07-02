import SwiftUI

// Chat building blocks — maxapp chat-alpha port: right-aligned gray user bubbles,
// full-width plain assistant text with typewriter reveal, intent cards, typing dots
// with rotating status phrases, suggested-chip cards, and the conversations drawer.

// MARK: - Markdown

/// Inline-only markdown (bold/italic/links) preserving whitespace; nil on parse failure.
func marqueMarkdown(_ s: String) -> AttributedString? {
    try? AttributedString(markdown: s,
                          options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))
}

// MARK: - User bubble

struct ChatUserBubble: View {
    let text: String
    /// 84% of the available (padded) list width, measured by ChatView.
    let maxWidth: CGFloat

    var body: some View {
        HStack(spacing: 0) {
            Spacer(minLength: 0)
            Text(text)
                .font(AppFont.bodyL)
                .lineSpacing(5)
                .foregroundStyle(Palette.textPrimary)
                .padding(.horizontal, 16)
                .padding(.vertical, 11)
                .background(Color(hex: 0xF4F4F4))
                .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                .frame(maxWidth: maxWidth, alignment: .trailing)
        }
        .padding(.bottom, 10)
    }
}

// MARK: - Assistant message (typewriter reveal + markdown + intent cards)

struct ChatAssistantMessage: View {
    let message: ChatMessage
    /// True while this message is the one that should animate in.
    let isTypewriting: Bool
    var onTick: () -> Void = {}
    var onTypewriterDone: () -> Void = {}

    @State private var displayed = ""
    @State private var caretOn = true
    @State private var revealDone = false

    private var showCards: Bool { !isTypewriting || revealDone }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            textBlock
                .font(AppFont.bodyL)
                .lineSpacing(7)
                .foregroundStyle(Palette.textPrimary)
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 4)
                .padding(.top, 6)
                .padding(.bottom, 8)
            if showCards, hasCards {
                cards
                    .padding(.bottom, Space.md)
                    .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.3), value: showCards)
        .task(id: isTypewriting) { await typewrite() }
    }

    private var textBlock: Text {
        if isTypewriting && !revealDone {
            return Text(displayed)
                + Text("\u{258D}").foregroundStyle(caretOn ? Palette.textPrimary : Color.clear)
        }
        if let md = marqueMarkdown(message.content) { return Text(md) }
        return Text(message.content)
    }

    private var hasCards: Bool {
        message.plan != nil
            || (message.scripts?.isEmpty == false)
            || message.analysis != nil
    }

    @ViewBuilder private var cards: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            if let plan = message.plan { DayPlanCard(plan: plan) }
            if let scripts = message.scripts, !scripts.isEmpty {
                ForEach(scripts) { ChatScriptCard(script: $0) }
            }
            if let analysis = message.analysis { ChatVideoAnalysisCard(analysis: analysis) }
        }
    }

    /// Reveal ~2–4 characters every 18ms with a blinking "▍" caret; markdown renders on completion.
    private func typewrite() async {
        guard isTypewriting, !revealDone else { return }
        let full = message.content
        var idx = full.startIndex
        var tick = 0
        while idx < full.endIndex, !Task.isCancelled {
            idx = full.index(idx, offsetBy: Int.random(in: 2...4),
                             limitedBy: full.endIndex) ?? full.endIndex
            displayed = String(full[..<idx])
            tick += 1
            if tick % 13 == 0 { caretOn.toggle() }   // ~480ms blink cycle at 18ms ticks
            if tick % 3 == 0 { onTick() }
            try? await Task.sleep(nanoseconds: 18_000_000)
        }
        revealDone = true
        onTypewriterDone()
    }
}

// MARK: - Typing indicator (three pulsing dots + rotating status phrase)

struct ChatTypingIndicator: View {
    private static let phrases = ["Thinking it through…", "Checking your brand…", "Sharpening the hook…"]
    @State private var dotsOn = false
    @State private var phrase = 0

    var body: some View {
        HStack(spacing: 10) {
            HStack(spacing: 4) {
                ForEach(0..<3) { i in
                    Circle()
                        .fill(Palette.textTertiary)
                        .frame(width: 5, height: 5)
                        .opacity(dotsOn ? 0.95 : 0.35)
                        .animation(.easeInOut(duration: 0.35).repeatForever(autoreverses: true)
                            .delay(Double(i) * 0.2333), value: dotsOn)
                }
            }
            Text(Self.phrases[phrase])
                .font(AppFont.caption)
                .foregroundStyle(Palette.textSecondary)
                .id(phrase)
                .transition(.opacity)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 4)
        .padding(.vertical, 10)
        .onAppear { dotsOn = true }
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 2_800_000_000)
                guard !Task.isCancelled else { break }
                withAnimation(.easeInOut(duration: 0.45)) {
                    phrase = (phrase + 1) % Self.phrases.count
                }
            }
        }
    }
}

// MARK: - Script card (compact — chat intent payload)

struct ChatScriptCard: View {
    let script: Script
    var saveLabel: String = "Save for later"
    var saveId: String = "chat.save"

    var body: some View {
        ChatScriptCardContent(script: script, saveLabel: saveLabel, saveId: saveId)
            .marqueCard(padding: Space.md)
    }
}

/// Inner content, shared by the standalone card and the analysis card's "Your version" block.
struct ChatScriptCardContent: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    let script: Script
    var saveLabel: String = "Save for later"
    var saveId: String = "chat.save"

    private var isSaved: Bool { store.readiedScripts.contains { $0.script.id == script.id } }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            FormatTag(formatId: script.formatId)
            Text(script.title.isEmpty ? script.hook.text : script.title)
                .font(AppFont.serifM)
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text("\u{201C}\(script.hook.text)\u{201D}")
                .font(AppFont.caption)
                .foregroundStyle(Palette.textSecondary)
                .lineLimit(2)
            HStack(spacing: Space.md) {
                Button {
                    store.readyScript(script, source: .chat)
                    router.pendingFilmScriptId = script.id
                    router.showFilm = true
                } label: {
                    Text("Film this")
                        .font(AppFont.callout)
                        .foregroundStyle(Palette.onInk)
                        .padding(.horizontal, Space.md)
                        .frame(height: 32)
                        .background(Palette.ink)
                        .clipShape(Capsule())
                }
                .buttonStyle(PressableStyle())
                Button {
                    store.readyScript(script, source: .chat)
                } label: {
                    Label(isSaved ? "Saved" : saveLabel,
                          systemImage: isSaved ? "bookmark.fill" : "bookmark")
                        .font(AppFont.callout)
                        .foregroundStyle(Palette.accent)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier(saveId)
            }
            .padding(.top, 2)
        }
    }
}

// MARK: - Video-analysis card

struct ChatVideoAnalysisCard: View {
    let analysis: VideoAnalysis

    var body: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            SectionLabel(text: "Why it works")
            if !analysis.hookAnalysis.isEmpty {
                Text(analysis.hookAnalysis)
                    .font(AppFont.body)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !analysis.structureBeats.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(analysis.structureBeats.enumerated()), id: \.offset) { i, beat in
                        HStack(alignment: .top, spacing: Space.sm) {
                            Text("\(i + 1).")
                                .font(AppFont.caption)
                                .foregroundStyle(Palette.textTertiary)
                                .frame(width: 18, alignment: .leading)
                            Text(beat)
                                .font(AppFont.caption)
                                .foregroundStyle(Palette.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
            if !analysis.whyItWorks.isEmpty {
                Text(analysis.whyItWorks)
                    .font(AppFont.body)
                    .foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let version = analysis.yourVersion {
                MarqueHairline()
                SectionLabel(text: "Your version")
                ChatScriptCardContent(script: version,
                                      saveLabel: "Save to film queue",
                                      saveId: "chat.saveVersion")
            }
        }
        .marqueCard(padding: Space.md)
    }
}

// MARK: - Suggested chips (vertical card stack above the composer)

struct ChatSuggestedChips: View {
    let chips: [String]
    let onTap: (String) -> Void

    var body: some View {
        VStack(spacing: 8) {
            ForEach(chips, id: \.self) { chip in
                Button { onTap(chip) } label: {
                    HStack(spacing: Space.sm) {
                        Text(chip)
                            .font(AppFont.callout)
                            .foregroundStyle(Palette.textPrimary)
                            .multilineTextAlignment(.leading)
                        Spacer(minLength: Space.sm)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 14, weight: .medium))
                            .foregroundStyle(Palette.textTertiary)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .frame(minHeight: 44)
                    .background(Palette.surface)
                    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                    .contentShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                }
                .buttonStyle(PressableStyle(dim: 0.7))
            }
        }
    }
}

// MARK: - Morphing send button (mic ↔ arrow ↔ stop)

enum ComposerSendState: Equatable { case empty, ready, streaming }

struct MorphSendButton: View {
    let state: ComposerSendState
    let action: () -> Void
    @State private var pulsing = false

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle().fill(Palette.ink)
                switch state {
                case .streaming:
                    RoundedRectangle(cornerRadius: 3.5, style: .continuous)
                        .fill(Color.white)
                        .frame(width: 13, height: 13)
                        .opacity(pulsing ? 0.55 : 1)
                        .onAppear {
                            pulsing = false
                            withAnimation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true)) {
                                pulsing = true
                            }
                        }
                        .transition(.scale.combined(with: .opacity))
                case .empty:
                    Image(systemName: "mic.fill")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundStyle(Palette.onInk)
                        .transition(.scale.combined(with: .opacity))
                case .ready:
                    Image(systemName: "arrow.up")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(Palette.onInk)
                        .transition(.scale.combined(with: .opacity))
                }
            }
            .frame(width: 36, height: 36)
            .contentShape(Circle())
        }
        .buttonStyle(PressableStyle())
        .animation(Motion.quick, value: state)
        .accessibilityIdentifier("chat.send")
        .accessibilityLabel(state == .streaming ? "Stop" : state == .ready ? "Send" : "Voice input")
    }
}

// MARK: - Conversations drawer

struct ConversationsDrawer: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let chat: ChatStore

    private var sorted: [Conversation] {
        let pinned = store.conversations.filter { $0.isVoiceNotes }
        let rest = store.conversations.filter { !$0.isVoiceNotes }
            .sorted { $0.updatedAt > $1.updatedAt }
        return pinned + rest
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("chats")
                .font(Typeface.display(20, .semibold))
                .tracking(Track.title)
                .foregroundStyle(Palette.textPrimary)
                .padding(.horizontal, Space.lg)
                .padding(.top, Space.xl)
                .padding(.bottom, Space.sm)

            List {
                Button {
                    chat.newConversation(in: store)
                    dismiss()
                } label: {
                    HStack(spacing: Space.md) {
                        Image(systemName: "square.and.pencil")
                            .font(.system(size: 17, weight: .medium))
                            .foregroundStyle(Palette.textPrimary)
                            .frame(width: 30, height: 30)
                            .background(Palette.surfaceSunken)
                            .clipShape(Circle())
                        Text("New chat")
                            .font(AppFont.headline)
                            .foregroundStyle(Palette.textPrimary)
                        Spacer()
                    }
                    .padding(.vertical, 4)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("chat.newChatRow")
                .listRowBackground(Palette.surface)
                .listRowSeparatorTint(Palette.hairline)

                ForEach(sorted) { convo in
                    Button {
                        chat.currentConversationId = convo.id
                        chat.chips = []
                        dismiss()
                    } label: {
                        row(convo)
                    }
                    .buttonStyle(.plain)
                    .listRowBackground(Palette.surface)
                    .listRowSeparatorTint(Palette.hairline)
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        if !convo.isVoiceNotes {
                            Button(role: .destructive) {
                                remove(convo)
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                    }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
        }
        .background(Palette.surface.ignoresSafeArea())
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    }

    private func row(_ convo: Conversation) -> some View {
        HStack(spacing: Space.md) {
            if convo.isVoiceNotes {
                Image(systemName: "waveform")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Palette.accent)
                    .frame(width: 30, height: 30)
                    .background(Palette.accentMuted)
                    .clipShape(Circle())
            }
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: Space.sm) {
                    Text(convo.title)
                        .font(AppFont.headline)
                        .foregroundStyle(Palette.textPrimary)
                        .lineLimit(1)
                    Spacer(minLength: Space.sm)
                    Text(convo.updatedAt.formatted(.relative(presentation: .named)))
                        .font(Typeface.sans(12))
                        .foregroundStyle(Palette.textTertiary)
                        .lineLimit(1)
                }
                Text(preview(convo))
                    .font(AppFont.caption)
                    .foregroundStyle(Palette.textTertiary)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }

    private func preview(_ convo: Conversation) -> String {
        guard let last = convo.messages.last, !last.content.isEmpty else { return "No messages yet" }
        return last.content
    }

    private func remove(_ convo: Conversation) {
        store.conversations.removeAll { $0.id == convo.id }
        if chat.currentConversationId == convo.id { chat.currentConversationId = nil }
        store.save()
    }
}
