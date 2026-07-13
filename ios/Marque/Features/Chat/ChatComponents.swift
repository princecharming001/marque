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
                .background(Palette.surfaceSunken)
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
    var onOpenScript: (Script) -> Void = { _ in }   // I-1

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
            || message.clipEdit != nil
    }

    @ViewBuilder private var cards: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            if let plan = message.plan { DayPlanCard(plan: plan) }
            if let scripts = message.scripts, !scripts.isEmpty {
                ForEach(scripts) { s in ChatScriptCard(script: s, onOpen: { onOpenScript(s) }) }
            }
            if let analysis = message.analysis { ChatVideoAnalysisCard(analysis: analysis) }
            if let edit = message.clipEdit { ClipEditCard(state: edit) }
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
    var onOpen: (() -> Void)? = nil        // I-1: tap the card to open the full reader

    var body: some View {
        ChatScriptCardContent(script: script, saveLabel: saveLabel, saveId: saveId, showChevron: onOpen != nil)
            .marqueCard(padding: Space.md)
            .contentShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
            .onTapGesture { onOpen?() }     // inner Film/Save buttons keep their own hit areas
            // Same accessibilityIdentifier-leak fix as cleanupPanel (ProEditorView+Actions.swift):
            // without .accessibilityElement(children: .contain), this card's own identifier
            // clobbers the inner Save button's own identifier (saveId, default "chat.save").
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("chat.scriptCard")
    }
}

/// Inner content, shared by the standalone card and the analysis card's "Your version" block.
struct ChatScriptCardContent: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    let script: Script
    var saveLabel: String = "Save for later"
    var saveId: String = "chat.save"
    var showChevron: Bool = false

    private var isSaved: Bool { store.readiedScripts.contains { $0.script.id == script.id } }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack {
                FormatTag(formatId: script.formatId)
                Spacer()
                if showChevron {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Palette.textTertiary)
                }
            }
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

// MARK: - Clip-edit progress card (W5)

struct ClipEditCard: View {
    let state: ClipEditState
    @Environment(AppRouter.self) private var router

    private var isTerminal: Bool { state.stage == .ready || state.stage == .failed }

    private var stageLabel: String {
        switch state.stage {
        case .stitching:  return state.clipCount > 1 ? "Stitching your clips…" : "Preparing your clip…"
        case .uploading:  return "Uploading your footage…"
        case .analyzing:  return "Reading the footage…"
        case .editing:    return "Cutting your edit…"
        case .ready:      return "Your edit is ready"
        case .failed:     return "Couldn't finish this edit"
        }
    }

    // The ordered pipeline for the little step tracker.
    private static let steps: [ClipEditState.Stage] = [.stitching, .uploading, .analyzing, .editing]
    private func stepDone(_ s: ClipEditState.Stage) -> Bool {
        if state.stage == .ready { return true }
        if state.stage == .failed { return false }
        let order: [ClipEditState.Stage] = Self.steps
        guard let cur = order.firstIndex(of: state.stage), let idx = order.firstIndex(of: s) else { return false }
        return idx < cur
    }
    private func stepActive(_ s: ClipEditState.Stage) -> Bool { state.stage == s }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack(spacing: Space.sm) {
                if !isTerminal { ProgressView().controlSize(.small).tint(Palette.accent) }
                else {
                    Image(systemName: state.stage == .ready ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(state.stage == .ready ? Palette.accent : Palette.textTertiary)
                }
                Text(stageLabel).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
            }

            if !isTerminal {
                HStack(spacing: 6) {
                    ForEach(Self.steps, id: \.self) { s in
                        Capsule()
                            .fill(stepDone(s) || stepActive(s) ? Palette.accent : Palette.hairline)
                            .frame(height: 3)
                            .opacity(stepActive(s) ? 0.7 : 1)
                    }
                }
            }

            if state.stage == .failed, !state.detail.isEmpty {
                Text(state.detail).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if state.stage == .ready {
                Button {
                    router.selectedTab = .library
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "photo.stack")
                        Text("View in Library").font(AppFont.callout.weight(.semibold))
                    }
                    .foregroundStyle(Palette.textPrimary)
                    .padding(.horizontal, 14).frame(height: 40)
                    .background(Palette.surfaceRaised)
                    .clipShape(Capsule())
                    .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                }
                .buttonStyle(PressableStyle(dim: 0.7))
                .accessibilityIdentifier("chat.clipEdit.viewInLibrary")
            }
        }
        .padding(Space.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.surface)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
        // Same fix as cleanupPanel: without this, the card's own identifier clobbers the
        // conditional "View in Library" button's own "chat.clipEdit.viewInLibrary" identifier.
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("chat.clipEdit.card")
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

/// One-tap suggested next messages. Tap sends; LONG-PRESS loads the chip into the
/// composer for editing — so a suggestion can seed a custom answer instead of
/// forcing a verbatim pick. The hint line keeps "you can just type" discoverable.
struct ChatSuggestedChips: View {
    let chips: [String]
    let onTap: (String) -> Void
    var onEdit: ((String) -> Void)? = nil

    @State private var pressed: String?

    var body: some View {
        VStack(spacing: 8) {
            ForEach(chips, id: \.self) { chip in
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
                .opacity(pressed == chip ? 0.7 : 1)
                .onTapGesture { onTap(chip) }
                .onLongPressGesture(minimumDuration: 0.35, perform: {
                    (onEdit ?? onTap)(chip)
                }, onPressingChanged: { down in
                    pressed = down ? chip : nil
                })
            }
            if onEdit != nil {
                Text("Tap to send — hold to edit, or just type")
                    .font(AppFont.micro)
                    .foregroundStyle(Palette.textTertiary)
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
                    Circle().fill(Palette.onInk.opacity(0.35))
                        .frame(width: 6, height: 6)
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

// MARK: - Conversations drawer (maxapp pattern: floats from the LEFT, hugs its content
// height — not a full-screen sheet — with the Coach persona + response-length picker
// stacked at the bottom, exactly like maxapp's ChatConversationsDrawer.)

struct ConversationsDrawer: View {
    @Environment(AppStore.self) private var store
    @Binding var isPresented: Bool
    let chat: ChatStore

    private static let panelWidth: CGFloat = 312

    private var sorted: [Conversation] {
        let pinned = store.conversations.filter { $0.isVoiceNotes }
        let rest = store.conversations.filter { !$0.isVoiceNotes }
            .sorted { $0.updatedAt > $1.updatedAt }
        return pinned + rest
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            // Fully transparent tap-to-close layer — opening the drawer must NOT dim
            // or blur the rest of the screen (maxapp's own spec).
            Color.clear
                .contentShape(Rectangle())
                .onTapGesture { close() }
                .accessibilityIdentifier("chat.drawerBackdrop")

            panel
                .padding(.top, 54)
                .padding(.leading, 10)
        }
        .opacity(isPresented ? 1 : 0)
        .offset(x: isPresented ? 0 : -(Self.panelWidth + 28))
        .allowsHitTesting(isPresented)
        .animation(Motion.spring, value: isPresented)
        .ignoresSafeArea()
    }

    private var panel: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Yunicorn")
                    .font(Typeface.display(20, .semibold)).tracking(-0.3)
                    .foregroundStyle(Palette.textPrimary)
                Spacer()
                Button { close() } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Palette.textSecondary)
                        .frame(width: 28, height: 28)
                        .background(Circle().fill(Palette.surfaceSunken))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("chat.drawerClose")
            }
            .padding(.bottom, Space.md)

            Button {
                chat.newConversation(in: store)
                close()
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "plus").font(.system(size: 13, weight: .semibold))
                    Text("New chat").font(AppFont.callout).fontWeight(.semibold)
                }
                .foregroundStyle(Palette.textPrimary)
                .frame(maxWidth: .infinity).frame(height: 38)
                .background(Palette.surfaceSunken)
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("chat.newChatRow")
            .padding(.bottom, Space.md)

            Text("RECENT")
                .font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
                .padding(.bottom, Space.xs)

            // ScrollView is greedy — it expands to any maxHeight even with one row,
            // leaving a dead gap in the panel. Collapse entirely when empty and cap
            // the height to the rows actually present otherwise.
            if sorted.isEmpty {
                Text("No chats yet")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    .padding(.vertical, Space.sm)
                    .padding(.bottom, Space.md)
            } else {
                ScrollView(showsIndicators: false) {
                    VStack(spacing: 2) {
                        ForEach(sorted) { convo in
                            Button {
                                chat.currentConversationId = convo.id
                                chat.chips = []
                                close()
                            } label: {
                                row(convo)
                            }
                            .buttonStyle(.plain)
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                if !convo.isVoiceNotes {
                                    Button(role: .destructive) { remove(convo) } label: {
                                        Label("Delete", systemImage: "trash")
                                    }
                                }
                            }
                        }
                    }
                }
                .frame(maxHeight: min(220, CGFloat(sorted.count) * 52))
                .padding(.bottom, Space.md)
            }

            MarqueHairline().padding(.bottom, Space.md)

            CoachPersonaPicker()
            LengthPicker()
        }
        .padding(Space.md)
        .frame(width: Self.panelWidth)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 30, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 30, style: .continuous)
            .strokeBorder(Color.white.opacity(0.5), lineWidth: 1))
        .overlay(RoundedRectangle(cornerRadius: 30, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 0.5))
        .shadow(color: .black.opacity(0.18), radius: 26, x: 4, y: 10)
    }

    private func close() { isPresented = false }

    private func row(_ convo: Conversation) -> some View {
        HStack(spacing: Space.sm) {
            if convo.isVoiceNotes {
                Image(systemName: "waveform")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Palette.accent)
                    .frame(width: 26, height: 26)
                    .background(Palette.accentMuted)
                    .clipShape(Circle())
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(convo.title)
                    .font(AppFont.callout).fontWeight(.medium)
                    .foregroundStyle(Palette.textPrimary)
                    .lineLimit(1)
                Text(convo.updatedAt.formatted(.relative(presentation: .named)))
                    .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 9).padding(.horizontal, 10)
        .background(convo.id == chat.currentConversationId ? Palette.surfaceSunken : .clear)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .contentShape(Rectangle())
    }

    private func remove(_ convo: Conversation) {
        store.conversations.removeAll { $0.id == convo.id }
        if chat.currentConversationId == convo.id { chat.currentConversationId = nil }
        store.save()
    }
}

// MARK: - Coach persona picker (3 original archetypes — same energy as the reference,
// generated fresh rather than using real people's names/likeness)

private struct CoachPersonaPicker: View {
    @Environment(AppStore.self) private var store
    @State private var applied: ChatPersona?

    private var current: ChatPersona { store.chatPersona ?? .closer }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            Text("COACH")
                .font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
            HStack(spacing: Space.sm) {
                ForEach(ChatPersona.allCases) { persona in
                    personaColumn(persona)
                }
            }
            Text(current.tagline)
                .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.top, 2)
        }
        .padding(.bottom, Space.md)
    }

    private func personaColumn(_ persona: ChatPersona) -> some View {
        let active = persona == current
        let glow = Color(hex: persona.glow)
        return Button {
            withAnimation(Motion.quick) { store.chatPersona = persona }
            store.save()
        } label: {
            VStack(spacing: 6) {
                ZStack {
                    if active {
                        Circle().fill(glow.opacity(0.22)).frame(width: 54, height: 54).blur(radius: 6)
                    }
                    Circle()
                        .fill(Palette.surfaceRaised)
                        .overlay(Circle().strokeBorder(active ? glow : Palette.hairline, lineWidth: active ? 2 : 1))
                    Image(systemName: persona.icon)
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(active ? glow : Palette.textTertiary)
                }
                .frame(width: 46, height: 46)
                Text(persona.label)
                    .font(.system(size: 10.5, weight: active ? .semibold : .medium))
                    .foregroundStyle(active ? Palette.textPrimary : Palette.textTertiary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("chat.persona.\(persona.rawValue)")
    }
}

private struct LengthPicker: View {
    @Environment(AppStore.self) private var store
    private var current: ChatResponseLength { store.chatResponseLength ?? .medium }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            Text("LENGTH")
                .font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
            HStack(spacing: 6) {
                ForEach(ChatResponseLength.allCases) { opt in
                    let active = opt == current
                    Button {
                        store.chatResponseLength = opt
                        store.save()
                    } label: {
                        Text(opt.label)
                            .font(.system(size: 12, weight: .medium))
                            .foregroundStyle(active ? Palette.onInk : Palette.textPrimary)
                            .frame(maxWidth: .infinity).frame(height: 34)
                            .background(active ? Palette.ink : Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .strokeBorder(active ? Color.clear : Palette.hairline, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("chat.length.\(opt.rawValue)")
                }
            }
            Text(current.hint)
                .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                .frame(maxWidth: .infinity, alignment: .center)
        }
    }
}
