import SwiftUI
import UIKit
import PhotosUI

// Chat tab — the maxapp chat-alpha port: custom header over a hairline, gray user
// bubbles vs full-width assistant text with a typewriter reveal, intent cards
// (scripts / video analysis / day plan), typing dots with rotating phrases,
// suggested-chip stack, and the morphing mic/send/stop composer pill.
struct ChatView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var chat = ChatStore()
    @State private var draft = ""
    @State private var showDrawer = false
    @State private var showAttach = false
    @State private var showClipPicker = false        // W5: PhotosPicker for "edit my clips"
    @State private var pickedClips: [PhotosPickerItem] = []
    @State private var speech = SpeechRecognizer()    // C-10: chat dictation
    @State private var dictating = false
    @State private var peekedScript: Script?          // I-1: chat card → full reader
    @FocusState private var composerFocused: Bool

    private static let bottomAnchor = "chat.bottomAnchor"
    private static let starters = ["Build my day", "Write me a script", "What should I post today?"]

    private var messages: [ChatMessage] { chat.current(in: store)?.messages ?? [] }
    private var trimmedDraft: String { draft.trimmingCharacters(in: .whitespacesAndNewlines) }
    /// Typing indicator only shows in the thread the in-flight reply belongs to.
    private var showTyping: Bool {
        chat.isStreaming && chat.streamingConversationId == chat.currentConversationId
    }
    private var showChips: Bool { !chat.chips.isEmpty && !chat.isStreaming }
    private var sendState: ComposerSendState {
        if chat.isStreaming { return .streaming }
        return trimmedDraft.isEmpty ? .empty : .ready
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            VStack(spacing: 0) {
                header
                if messages.isEmpty && !showTyping {
                    emptyState
                } else {
                    messageArea
                }
                if showChips {
                    ChatSuggestedChips(chips: chat.chips,
                                       onTap: { chat.send($0, store: store) },
                                       onEdit: { text in
                                           // Seed the composer with the chip so the creator can
                                           // customize their answer instead of sending verbatim.
                                           draft = text
                                           composerFocused = true
                                       },
                                       onOther: {
                                           // Custom answer: empty composer, keyboard up. The
                                           // chips stay until the first keystroke (draft
                                           // onChange below), so the choices remain visible
                                           // for reference while starting to type.
                                           draft = ""
                                           composerFocused = true
                                       })
                        .padding(.horizontal, 16)
                        .padding(.bottom, 4)
                        .transition(.opacity.combined(with: .move(edge: .bottom)))
                }
                composer
            }
            .animation(Motion.quick, value: showChips)

            // Floats from the left over the chat content (maxapp pattern) — not a sheet.
            ConversationsDrawer(isPresented: $showDrawer, chat: chat)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
        .marqueActions($showAttach, title: "Add to chat", actions: [
            MarqueDialogAction("Edit my clips") { showClipPicker = true },
            MarqueDialogAction("Paste video link") { pasteVideoLink() },
        ])
        // W5: attach up to 4 videos → edit them from chat with the current draft as
        // the instruction. Selection completing kicks off the edit pipeline.
        .sheet(item: $peekedScript) { s in
            NavigationStack { ScriptReaderView(script: s) }
        }
        .photosPicker(isPresented: $showClipPicker, selection: $pickedClips,
                      maxSelectionCount: 4, matching: .videos)
        .onChange(of: pickedClips) { _, items in
            guard !items.isEmpty else { return }
            let instruction = trimmedDraft
            draft = ""
            chat.sendClips(items, instruction: instruction, store: store)
            pickedClips = []
        }
        .onChange(of: draft) { _, newValue in
            if !newValue.isEmpty { chat.chips = [] }   // chips dismiss when the user types
        }
        // C-10: stream the live transcript into the draft while dictating; clear the
        // flag when the recognizer auto-stops (silence timeout / final result).
        .onChange(of: speech.transcript) { _, t in
            if dictating, !t.isEmpty { draft = t }
        }
        .onChange(of: speech.isListening) { _, listening in
            if !listening { dictating = false }
        }
        .onDisappear { if dictating { speech.stop(); dictating = false } }
        .onChange(of: composerFocused) { _, focused in
            // The persistent tab bar (with its floating Film FAB) sits in a safeAreaInset
            // outside this view's own keyboard avoidance, so it doesn't yield to the
            // keyboard the way the composer does — hide it while typing so the FAB can't
            // visually collide with (and steal taps from) the composer's send button.
            router.hideTabBar = focused
        }
        .onDisappear { router.hideTabBar = false }
    }

    // MARK: Header — menu / serif wordmark / new chat, over a 1px hairline

    private var header: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                Button { showDrawer = true } label: {
                    Image(systemName: "line.3.horizontal")
                        .font(.system(size: 22, weight: .regular))
                        .foregroundStyle(Palette.textSecondary)
                        .frame(width: 40, height: 40)
                        .contentShape(Rectangle())
                }
                .buttonStyle(PressableStyle(dim: 0.6))
                .accessibilityIdentifier("chat.drawer")
                .accessibilityLabel("Conversations")

                Spacer()

                Button {
                    chat.newConversation(in: store)
                } label: {
                    Image(systemName: "square.and.pencil")
                        .font(.system(size: 20, weight: .regular))
                        .foregroundStyle(Palette.textSecondary)
                        .frame(width: 40, height: 40)
                        .contentShape(Rectangle())
                }
                .buttonStyle(PressableStyle(dim: 0.6))
                .accessibilityIdentifier("chat.newChat")
                .accessibilityLabel("New chat")
            }
            .padding(.horizontal, 10)
            .frame(height: 52)
            .overlay(
                Text("Yunicorn")
                    .font(Typeface.display(17, .semibold))
                    .tracking(-0.2)
                    .foregroundStyle(Palette.textPrimary)
            )
            Rectangle().fill(Palette.hairline).frame(height: 1)
        }
        .background(Palette.surface)
        .contentShape(Rectangle())
        .onTapGesture { composerFocused = false }
    }

    // MARK: Message list

    private var messageArea: some View {
        GeometryReader { geo in
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(messages) { message in
                            row(message, containerWidth: geo.size.width, proxy: proxy)
                                .id(message.id)
                        }
                        if showTyping { ChatTypingIndicator() }
                        Color.clear.frame(height: 1).id(Self.bottomAnchor)
                    }
                    .padding(.horizontal, Space.xl)
                    .padding(.top, Space.xl)
                    .padding(.bottom, Space.xxl)
                }
                .scrollIndicators(.hidden)
                .scrollDismissesKeyboard(.interactively)
                .onTapGesture { composerFocused = false }
                .onAppear { proxy.scrollTo(Self.bottomAnchor, anchor: .bottom) }
                .onChange(of: messages.count) { _, _ in
                    withAnimation(Motion.quick) { proxy.scrollTo(Self.bottomAnchor, anchor: .bottom) }
                }
                .onChange(of: showTyping) { _, _ in
                    withAnimation(Motion.quick) { proxy.scrollTo(Self.bottomAnchor, anchor: .bottom) }
                }
                .onChange(of: chat.currentConversationId) { _, _ in
                    Task {   // let the swapped thread lay out before jumping to its tail
                        try? await Task.sleep(nanoseconds: 80_000_000)
                        proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
                    }
                }
                .onChange(of: composerFocused) { _, focused in
                    guard focused else { return }
                    Task {   // keep the tail visible once the keyboard has risen
                        try? await Task.sleep(nanoseconds: 350_000_000)
                        withAnimation(Motion.quick) { proxy.scrollTo(Self.bottomAnchor, anchor: .bottom) }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func row(_ message: ChatMessage, containerWidth: CGFloat, proxy: ScrollViewProxy) -> some View {
        if message.role == .user {
            ChatUserBubble(text: message.content,
                           maxWidth: max(220, (containerWidth - Space.xl * 2) * 0.84))
        } else {
            ChatAssistantMessage(
                message: message,
                isTypewriting: chat.typewriterMessageId == message.id,
                onTick: { proxy.scrollTo(Self.bottomAnchor, anchor: .bottom) },
                onTypewriterDone: {
                    if chat.typewriterMessageId == message.id { chat.typewriterMessageId = nil }
                    proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
                },
                onOpenScript: { peekedScript = $0 }
            )
        }
    }

    // MARK: Empty state

    private var emptyState: some View {
        VStack(spacing: Space.xl) {
            Spacer()
            Text("What can I help with?")
                .font(Typeface.sans(24, .semibold))
                .tracking(Track.tight)
                .foregroundStyle(Palette.textPrimary)
            VStack(spacing: 10) {
                ForEach(Self.starters, id: \.self) { starter in
                    Button { chat.send(starter, store: store) } label: {
                        Text(starter)
                            .font(AppFont.callout)
                            .foregroundStyle(Palette.textPrimary)
                            .padding(.horizontal, 15)
                            .padding(.vertical, 10)
                            .background(Palette.surface)
                            .clipShape(Capsule())
                            .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                    }
                    .buttonStyle(PressableStyle(dim: 0.7))
                }
            }
            Spacer()
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .contentShape(Rectangle())
        .onTapGesture { composerFocused = false }
    }

    // MARK: Composer — pill with attach / field / morphing mic-send-stop

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 6) {
            Button { showAttach = true } label: {
                Image(systemName: "plus")
                    .font(.system(size: 24, weight: .regular))
                    .foregroundStyle(Palette.textSecondary)
                    .frame(width: 36, height: 36)
                    .contentShape(Circle())
            }
            .buttonStyle(PressableStyle(dim: 0.6))
            .accessibilityIdentifier("chat.attach")
            .accessibilityLabel("Add")

            TextField("Ask Yunicorn anything", text: $draft, axis: .vertical)
                .font(AppFont.bodyL)
                .foregroundStyle(Palette.textPrimary)
                .lineLimit(1...5)
                .frame(minHeight: 36)
                .padding(.horizontal, 4)
                .focused($composerFocused)
                .accessibilityIdentifier("chat.composer")

            MorphSendButton(state: sendState) {
                switch sendState {
                case .streaming: chat.cancel()
                case .empty: toggleDictation()        // C-10: mic → live dictation into the draft
                case .ready: sendDraft()
                }
            }
        }
        .padding(7)
        .frame(minHeight: 50)
        .background(Palette.surface)
        .clipShape(RoundedRectangle(cornerRadius: 26, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 26, style: .continuous)
            .strokeBorder(Palette.divider, lineWidth: 1))
        .shadow(color: .black.opacity(0.05), radius: 8, x: 0, y: 2)
        .padding(.horizontal, 16)
        .padding(.top, 6)
        // The tab bar is a plain bottom overlay (never a safeAreaInset) — the composer
        // owns its clearance. When the keyboard is up the bar hides (composerFocused →
        // hideTabBar) so only a small margin is needed.
        .padding(.bottom, router.hideTabBar ? Space.sm : MarqueTabBar.clearance)
        .animation(Motion.quick, value: router.hideTabBar)
    }

    // MARK: Actions

    private func sendDraft() {
        if dictating { speech.stop(); dictating = false }
        let text = trimmedDraft
        guard !text.isEmpty else { return }
        draft = ""
        chat.send(text, store: store)
    }

    // MARK: Dictation (C-10)

    /// Mic tap toggles live speech-to-text into the draft. Auto-stops on silence
    /// (SpeechRecognizer finalizes) or when the user taps send. No-op with a quiet
    /// notice if the mic/recognizer isn't available (e.g. simulator).
    private func toggleDictation() {
        if dictating { speech.stop(); dictating = false; return }
        Task {
            guard await speech.requestAuthorization() else { return }
            speech.start()
            if speech.isAvailable {
                dictating = true
            }
        }
    }

    private func pasteVideoLink() {
        guard let link = UIPasteboard.general.string?
            .trimmingCharacters(in: .whitespacesAndNewlines), !link.isEmpty else { return }
        chat.send(link, store: store)
    }
}
