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
    @State private var editItems: [PhotosPickerItem] = []   // held while the config sheet is up
    @State private var libraryClip: Clip?                    // build 66: library-attach path
    @State private var showEditConfig = false
    @State private var speech = SpeechRecognizer()    // C-10: chat dictation
    @State private var dictating = false
    @State private var peekedScript: Script?          // I-1: chat card → full reader
    @FocusState private var composerFocused: Bool

    private static let bottomAnchor = "chat.bottomAnchor"
    private static let starters = ["Build my day", "Write me a script", "What should I post today?"]

    /// P7.3: an insight card (or its push) routed here with a prompt — pre-fill the
    /// composer so the creator can send (or edit) it in one tap.
    private func consumePendingPrompt() {
        guard let p = router.pendingChatPrompt, !p.isEmpty else { return }
        draft = p
        router.pendingChatPrompt = nil
        composerFocused = true
    }

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
                        .padding(.horizontal, Space.screenH)
                        .padding(.bottom, Space.sm)
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
        // Build 66: exactly two attach sources, toggled — videos from Photos, or a clip
        // already in the Yunicorn library. (The old free-form action list with "paste a
        // link" read as clutter; a link can still just be typed into the chat.)
        .sheet(isPresented: $showAttach) {
            ChatAttachSheet(
                onPhotos: { showAttach = false; showClipPicker = true },
                onLibraryClip: { clip in
                    showAttach = false
                    libraryClip = clip
                    showEditConfig = true
                })
                .presentationDetents([.medium, .large])
        }
        // W5: attach up to 4 videos → edit them from chat with the current draft as
        // the instruction. Selection completing kicks off the edit pipeline.
        .sheet(item: $peekedScript) { s in
            NavigationStack { ScriptReaderView(script: s) }
        }
        .photosPicker(isPresented: $showClipPicker, selection: $pickedClips,
                      maxSelectionCount: 4, matching: .videos)
        .onChange(of: pickedClips) { _, items in
            guard !items.isEmpty else { return }
            // Parity with the record flow: configure the edit (composition style, toggles,
            // instruction, react source) before it runs, instead of firing with defaults.
            editItems = Array(items.prefix(4))
            pickedClips = []
            showEditConfig = true
        }
        .sheet(isPresented: $showEditConfig) {
            ChatEditConfigSheet(clipCount: libraryClip == nil ? editItems.count : 1,
                                initialInstruction: trimmedDraft) {
                config, toggles, editFormat, instruction, reactSourceURL in
                draft = ""
                if let clip = libraryClip {
                    chat.sendLibraryClip(clip, instruction: instruction, store: store,
                                         config: config, toggles: toggles,
                                         editFormat: editFormat, reactSourceURL: reactSourceURL)
                    libraryClip = nil
                } else {
                    chat.sendClips(editItems, instruction: instruction, store: store,
                                   config: config, toggles: toggles,
                                   editFormat: editFormat, reactSourceURL: reactSourceURL)
                    editItems = []
                }
            }
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

    // MARK: Header — drawer glyph / lowercase title / new chat, on the canvas (no bar)

    private var header: some View {
        HStack(spacing: 0) {
            DSIconButton(systemName: "line.3.horizontal", size: 20) { showDrawer = true }
                .accessibilityIdentifier("chat.drawer")
                .accessibilityLabel("Conversations")

            Spacer()

            DSIconButton(systemName: "square.and.pencil", size: 20) {
                chat.newConversation(in: store)
            }
            .accessibilityIdentifier("chat.newChat")
            .accessibilityLabel("New chat")
        }
        .padding(.horizontal, Space.xs)
        .frame(height: 52)
        .overlay(
            Text("chat.")
                .font(AppFont.title2)
                .tracking(-0.2)
                .foregroundStyle(Palette.textPrimary)
                .accessibilityAddTraits(.isHeader)
        )
        .background(Palette.canvas)
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
                    .padding(.horizontal, Space.screenH)
                    .padding(.top, Space.md)
                    .padding(.bottom, Space.xxl)
                }
                .scrollIndicators(.hidden)
                .scrollDismissesKeyboard(.interactively)
                .onTapGesture { composerFocused = false }
                .onAppear {
                    proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
                    consumePendingPrompt()
                }
                .onChange(of: router.pendingChatPrompt) { _, _ in consumePendingPrompt() }
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
                           maxWidth: max(220, (containerWidth - Space.screenH * 2) * 0.84))
        } else {
            ChatAssistantMessage(
                message: message,
                isTypewriting: chat.typewriterMessageId == message.id,
                onTick: { proxy.scrollTo(Self.bottomAnchor, anchor: .bottom) },
                onTypewriterDone: {
                    if chat.typewriterMessageId == message.id { chat.typewriterMessageId = nil }
                    proxy.scrollTo(Self.bottomAnchor, anchor: .bottom)
                },
                onOpenScript: { peekedScript = $0 },
                onRetryEdit: {
                    if let cid = chat.currentConversationId {
                        chat.retryEdit(cardId: message.id, convoId: cid, store: store)
                    }
                }
            )
        }
    }

    // MARK: Empty state — Stoic editor voice: a left-aligned prompt, capsule starters

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            Spacer()
            Text("What can I help with?")
                .font(AppFont.title1)
                .tracking(-0.3)
                .foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: Space.sm) {
                ForEach(Self.starters, id: \.self) { starter in
                    DSChip(title: starter) { chat.send(starter, store: store) }
                }
            }
            Spacer()
            Spacer()
        }
        .padding(.horizontal, Space.screenH)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .onTapGesture { composerFocused = false }
    }

    // MARK: Composer — outline "+" circle / sunken capsule field / ink morph circle

    private var composer: some View {
        HStack(alignment: .bottom, spacing: Space.sm) {
            DSCircleButton(systemName: "plus", kind: .outline, size: 48) { showAttach = true }
                .accessibilityIdentifier("chat.attach")
                .accessibilityLabel("Add")

            TextField("Ask Yunicorn anything", text: $draft, axis: .vertical)
                .font(AppFont.bodyText)
                .foregroundStyle(Palette.textPrimary)
                .tint(Palette.textPrimary)
                .lineLimit(1...5)
                .padding(.horizontal, Space.md)
                .padding(.vertical, 13)
                .frame(minHeight: 48)
                .background(RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .fill(Palette.surfaceSunken))
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
        .padding(.horizontal, Space.screenH)
        .padding(.top, Space.sm)
        // The tab bar is a plain bottom overlay (never a safeAreaInset) — the composer
        // owns its clearance. When the keyboard is up the bar hides (composerFocused →
        // hideTabBar) so only a small margin is needed.
        .padding(.bottom, router.hideTabBar ? Space.sm : MarqueTabBar.clearance)
        .background(Palette.canvas)
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


// MARK: - Add to chat (build 66)

/// Two attach sources, one toggle: videos from Photos, or a clip already in the library.
struct ChatAttachSheet: View {
    @Environment(AppStore.self) private var store
    let onPhotos: () -> Void
    let onLibraryClip: (Clip) -> Void

    @State private var source = 0   // 0 Photos · 1 Your library

    private var libraryClips: [Clip] {
        store.clips.filter { c in
            guard c.status == .ready || c.status == .scheduled || c.status == .posted else { return false }
            return [c.localVideoPath, c.renderLocalPath].compactMap({ $0 })
                .contains(where: { FileManager.default.fileExists(atPath: MediaStore.url(for: $0).path) })
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            // Sheet header (DESIGN.md): eyebrow + centered lowercase title.
            VStack(spacing: 4) {
                DSEyebrow(text: "Add to chat")
                Text("attach a video.").font(AppFont.title1).tracking(-0.3)
                    .foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity)
            .padding(.top, Space.lg)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)

            MarqueSegmented(options: ["Photos", "Your library"], index: $source)
                .accessibilityIdentifier("chat.attachSource")
            if source == 0 {
                VStack(spacing: Space.sm) {
                    Image(systemName: "photo.on.rectangle")
                        .font(.system(size: 22, weight: .regular))
                        .foregroundStyle(Palette.textSecondary)
                        .padding(.bottom, Space.xs)
                        .accessibilityHidden(true)
                    Text("Pick up to 4 videos from your camera roll. Yunicorn stitches and edits them.")
                        .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: 300)
                    PrimaryButton(title: "Choose from Photos", systemImage: "photo", fullWidth: false) { onPhotos() }
                        .accessibilityIdentifier("chat.attachPhotos")
                        .padding(.top, Space.md)
                }
                .frame(maxWidth: .infinity)
                .padding(.top, Space.md)
            } else if libraryClips.isEmpty {
                VStack(spacing: Space.sm) {
                    Image(systemName: "rectangle.stack")
                        .font(.system(size: 22, weight: .regular))
                        .foregroundStyle(Palette.textSecondary)
                        .padding(.bottom, Space.xs)
                        .accessibilityHidden(true)
                    Text("Nothing in your library yet, film or upload a clip first.")
                        .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: 300)
                }
                .frame(maxWidth: .infinity)
                .padding(.top, Space.md)
            } else {
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 100), spacing: Space.sm)],
                              spacing: Space.sm) {
                        ForEach(libraryClips) { c in
                            Button { onLibraryClip(c) } label: {
                                ZStack(alignment: .bottomLeading) {
                                    Color.clear
                                        .aspectRatio(9.0 / 16.0, contentMode: .fit)
                                        .overlay(LocalThumbnail(path: c.thumbnailPath ?? c.playbackLocalPath,
                                                                isVideo: true, remoteImageURL: c.thumbnailURL)
                                            .scaledToFill())
                                        .overlay(alignment: .bottom) {
                                            // Legibility scrim for the title over the (full-color) thumbnail.
                                            LinearGradient(colors: [.clear, Color.black.opacity(0.55)],
                                                           startPoint: .top, endPoint: .bottom)
                                                .frame(height: 56)
                                        }
                                        .clipShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
                                    Text(c.title.isEmpty ? c.formatName : c.title)
                                        .font(AppFont.caption.weight(.semibold)).lineLimit(1)
                                        .foregroundStyle(Palette.onNight)
                                        .padding(Space.sm)
                                }
                                .contentShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
                            }
                            .buttonStyle(PressableStyle(dim: 0.85))
                            .accessibilityIdentifier("chat.attachClip")
                        }
                    }
                    .padding(.bottom, Space.xl)
                }
                .scrollIndicators(.hidden)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Space.screenH)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.canvas.ignoresSafeArea())
    }
}
