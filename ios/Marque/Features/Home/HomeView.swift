import SwiftUI
import PhotosUI

// Home — the daily driver AND the chat. Beta feedback (2026-08-18) killed the voice
// orb outright ("tapping the orb again to stop listening is unintuitive… I'd rather
// have a chat bot section, ChatGPT style") and folded the Chat tab in here ("the Home
// Tab and the Chat tab can be consolidated").
//
// The shape that satisfies both: Home IS the chat. Its empty state is the daily feed
// (script picks, trend ticker, "Steal these" reels) instead of ChatGPT's blank
// "What can I help with?" — so the surface is useful before you say anything. The
// moment a thread has messages the scroll region becomes the transcript; "new chat"
// in the header returns to the feed. One pinned composer owns the bottom either way.
//
// Deliberately NOT nested: the feed scroll and the message scroll are siblings that
// swap, never a scroll inside a scroll — ChatStore's ScrollViewReader bottom-anchor
// machinery only works when it owns its scroll view.
struct HomeView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(TourManager.self) private var tour
    // UX-F1: app-owned (MarqueApp) so the feed survives tab switches — a view-owned
    // @State store was torn down with HomeView on every RootTabView switch, forcing
    // skeletons + a refetch every time the creator came back to Home.
    @Environment(FeedStore.self) private var feed
    // Same reasoning, same fix: the chat thread must survive a trip to Library.
    @Environment(ChatStore.self) private var chat
    @State private var selectedReel: ReelItem?
    @State private var peekedScript: Script?    // tapped pick card → full script sheet

    // Chat surface state
    @State private var draft = ""
    @State private var showDrawer = false
    @State private var showAttach = false
    @State private var showClipPicker = false
    @State private var pickedClips: [PhotosPickerItem] = []
    @State private var editItems: [PhotosPickerItem] = []
    @State private var libraryClip: Clip?
    @State private var showEditConfig = false
    @FocusState private var composerFocused: Bool

    private static let bottomAnchor = "chat.bottomAnchor"

    private var messages: [ChatMessage] { chat.current(in: store)?.messages ?? [] }
    private var trimmedDraft: String { draft.trimmingCharacters(in: .whitespacesAndNewlines) }
    /// Typing indicator only shows in the thread the in-flight reply belongs to.
    private var showTyping: Bool {
        chat.isStreaming && chat.streamingConversationId == chat.currentConversationId
    }
    private var showChips: Bool { !chat.chips.isEmpty && !chat.isStreaming }
    private var showingThread: Bool { !messages.isEmpty || showTyping }
    private var sendState: ComposerSendState {
        if chat.isStreaming { return .streaming }
        return trimmedDraft.isEmpty ? .empty : .ready
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            VStack(spacing: 0) {
                homeHeader
                if showingThread {
                    messageArea
                } else {
                    feedScroll
                }
                if showChips {
                    ChatSuggestedChips(chips: chat.chips,
                                       onTap: { chat.send($0, store: store) },
                                       onEdit: { text in
                                           draft = text
                                           composerFocused = true
                                       },
                                       onOther: {
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

            // Floats from the left over the content (maxapp pattern) — not a sheet.
            ConversationsDrawer(isPresented: $showDrawer, chat: chat)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
        // Tapping a reel opens the full-screen TikTok/IG-style vertical feed (swipe for the
        // next, endlessly) rather than a single teardown sheet — the teardown is still one
        // tap away via the pager's "Details" button.
        .fullScreenCover(item: $selectedReel) { reel in
            ReelFeedPager(feed: feed, startReel: reel)
        }
        // Tapping a pick opens the full script (read it, tweak it, film it) —
        // the card only ever shows the title + hook.
        .sheet(item: $peekedScript) { s in
            NavigationStack { ScriptReaderView(script: s) }
        }
        // Build 66: exactly two attach sources, toggled — videos from Photos, or a clip
        // already in the Yunicorn library.
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
        .onChange(of: composerFocused) { _, focused in
            // The persistent tab bar (with its floating Film FAB) sits in an overlay
            // outside this view's own keyboard avoidance, so it doesn't yield to the
            // keyboard the way the composer does — hide it while typing so the FAB can't
            // visually collide with (and steal taps from) the composer's send button.
            router.hideTabBar = focused
        }
        .onDisappear { router.hideTabBar = false }
        .onAppear { consumePendingPrompt() }
        .onChange(of: router.pendingChatPrompt) { _, _ in consumePendingPrompt() }
        .task { await feed.loadInitial(store: store) }
        .task { await store.loadTrends() }          // W1: full niche-trend list for the rotating ticker
        .navigationDestination(for: String.self) { dest in
            if dest == "profile" { ProfileView() }
        }
        .task {
            // Let the staggered entrance settle before the tour dims the screen —
            // starting mid-entrance would fight the reveal animation for attention.
            try? await Task.sleep(nanoseconds: 900_000_000)
            tour.startIfNeeded(router: router)
        }
    }

    // MARK: Feed — the chat's empty state (and the reason to open the app)

    private var feedScroll: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                greetingBlock.staggerReveal(0)
                picksSection.staggerReveal(1)
                if let trend = feed.trend {
                    TrendTicker(trend: trend, all: store.trends).staggerReveal(2)
                }
                stealSection.staggerReveal(3)
            }
            .screenPadding()
            .padding(.top, Space.lg)
            .padding(.bottom, Space.xxl)
        }
        .scrollDismissesKeyboard(.interactively)
        .refreshable { await feed.refresh(store: store) }
    }

    // MARK: Header — drawer / wordmark / new-chat + profile

    private var dateKicker: String {
        Date().formatted(.dateTime.weekday(.wide).month(.abbreviated).day()).uppercased()
    }

    private var homeHeader: some View {
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

                // "New chat" doubles as "back to my feed" — an empty thread renders the
                // daily feed, so one control covers both mental models.
                Button {
                    chat.newConversation(in: store)
                    composerFocused = false
                } label: {
                    Image(systemName: "square.and.pencil")
                        .font(.system(size: 20, weight: .regular))
                        .foregroundStyle(showingThread ? Palette.textPrimary : Palette.textSecondary)
                        .frame(width: 40, height: 40)
                        .contentShape(Rectangle())
                }
                .buttonStyle(PressableStyle(dim: 0.6))
                .accessibilityIdentifier("chat.newChat")
                .accessibilityLabel("New chat")

                if store.streak > 0 {
                    StreakGlyph(count: store.streak).padding(.horizontal, Space.xs)
                }
                NavigationLink(value: "profile") { avatarButton }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("home.profile")
                    .padding(.trailing, Space.xs)
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

    private var avatarButton: some View {
        ZStack {
            Circle().fill(Palette.accent.opacity(0.16)).frame(width: 34, height: 34)
            if let url = store.primaryAccount?.avatarUrl, !url.isEmpty, let u = URL(string: url) {
                AsyncImage(url: u) { img in img.resizable().scaledToFill() } placeholder: { initial }
                    .frame(width: 34, height: 34).clipShape(Circle())
            } else {
                initial
            }
        }
        // A brand-tinted ring (vs. a bare neutral hairline) reads as a deliberate
        // identity mark instead of a placeholder — same restraint, more presence.
        .overlay(Circle().strokeBorder(Palette.accent.opacity(0.35), lineWidth: 1.5))
        .shadow(color: Palette.shadowWarm.opacity(0.10), radius: 4, x: 0, y: 2)
    }

    private var initial: some View {
        Text(String((store.primaryAccount?.handle ?? store.brand.niche).prefix(1)).uppercased())
            .font(Typeface.display(15, .semibold)).foregroundStyle(Palette.accent)
    }

    private var greeting: String {
        let hour = Calendar.current.component(.hour, from: Date())
        let part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"
        if let h = store.primaryAccount?.handle, !h.isEmpty { return "\(part), @\(h)" }
        return part
    }

    private var greetingBlock: some View {
        Text(greeting)
            .font(Typeface.display(34)).tracking(-0.8)
            .foregroundStyle(Palette.textPrimary)
            .fixedSize(horizontal: false, vertical: true)
    }

    // MARK: Today's picks — snap carousel of daily scripts (FeedStore page 0+)

    private var picksSection: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            SectionLabel(text: "Today's picks", accent: Palette.accent)
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: Space.md) {
                    if feed.scriptItems.isEmpty && feed.isLoading {
                        FeedSkeletonCard()
                        FeedSkeletonCard()
                    } else if feed.scriptItems.isEmpty {
                        picksOfflineCard
                    } else {
                        ForEach(feed.scriptItems) { s in
                            ScriptFeedCard(
                                script: s,
                                onFilm: {
                                    store.readyScript(s, source: .daily)
                                    router.pendingFilmScriptId = s.id
                                    router.showFilm = true
                                },
                                onSave: { store.readyScript(s, source: .daily) },
                                saved: store.readiedScripts.contains { $0.script.id == s.id },
                                onOpen: {
                                    peekedScript = s          // open instantly with what we have
                                    // Idea-brief cards carry only a one-line summary as the
                                    // body — expand to the full script so the reader never
                                    // shows a bare summary ("incomplete script").
                                    if store.isUnexpandedBrief(s) {
                                        Task {
                                            if let full = await store.expandedBriefForPeek(s),
                                               peekedScript?.id == s.id {
                                                peekedScript = full
                                            }
                                        }
                                    }
                                },
                                liked: store.likedPicks.contains(s.id),
                                onLike: { store.likePick(s) },
                                onDismiss: { withAnimation(Motion.quick) { feed.dismiss(s, store: store) } }
                            )
                            .transition(.scale(scale: 0.92).combined(with: .opacity))
                        }
                        if feed.feedCursor >= 0 {
                            morePicksCard
                        }
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.viewAligned)
            .contentMargins(.horizontal, Space.screenH, for: .scrollContent)
            .padding(.horizontal, -Space.screenH)
        }
    }

    /// Trailing "More" pill card — pulls the next mixed-feed page (scripts only land here).
    private var morePicksCard: some View {
        Button {
            Task { await feed.loadMoreScripts(store: store) }
        } label: {
            VStack(spacing: Space.sm) {
                if feed.isLoadingMoreScripts {
                    ProgressView().tint(Palette.textSecondary)
                } else {
                    Image(systemName: "arrow.right")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Palette.textPrimary)
                    Text("More").font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                }
            }
            .frame(width: 96, height: 220)   // matches ScriptFeedCard's height
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
            .contentShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        }
        .buttonStyle(PressableStyle(dim: 0.7))
        .disabled(feed.isLoadingMoreScripts)
        .accessibilityIdentifier("feed.moreScripts")
    }

    /// Shown only when the initial feed load came back empty (offline / backend miss).
    private var picksOfflineCard: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 18)).foregroundStyle(Palette.textTertiary)
            Text("Couldn't load today's picks")
                .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
            Text("Pull down to refresh when you're back online.")
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(Space.lg)
        .frame(width: 260, height: 220, alignment: .topLeading)   // matches ScriptFeedCard
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }

    // MARK: Steal these — proven reels from the niche, 2-col grid + own pagination

    private var reelColumns: [GridItem] {
        [GridItem(.flexible(), spacing: Space.md), GridItem(.flexible())]
    }

    private var stealSection: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            VStack(alignment: .leading, spacing: Space.xs) {
                SectionLabel(text: "Steal these", accent: Palette.warning)
                // Only promise "your niche" when the server actually served your niche.
                // A cold niche cache falls back to the cross-niche aggregate, and
                // labelling that "from your niche" is how a photographer concluded the
                // app had no idea what their account was about (beta feedback).
                Text(feed.reelsAreOffNiche
                     ? "Still scanning your niche — here's what's working elsewhere meanwhile."
                     : "Proven reels from your niche — mimic them in your voice.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
            }
            if feed.reelItems.isEmpty && feed.isLoading {
                LazyVGrid(columns: reelColumns, spacing: Space.md) {
                    ReelSkeletonCard()
                    ReelSkeletonCard()
                }
            } else if feed.reelItems.isEmpty {
                EmptyStateView(icon: "rectangle.stack.badge.play",
                               title: "Finding real reels…",
                               message: "We're scanning your niche and the creators you watch for reels that are actually performing. Pull to refresh in a moment — or add creators to watch in your profile.")
            } else {
                LazyVGrid(columns: reelColumns, spacing: Space.md) {
                    ForEach(feed.reelItems) { r in
                        ReelCard(reel: r) { selectedReel = r }
                            // Infinite scroll: nearing the end auto-loads the next page, so
                            // the grid keeps growing as you scroll (no manual "Load more").
                            .onAppear { autoLoadMoreReels(near: r) }
                    }
                }
                if feed.isLoadingMoreReels {
                    ProgressView().tint(Palette.textSecondary)
                        .frame(maxWidth: .infinity).frame(height: 44)
                }
            }
        }
    }

    /// Trigger the next reels page when one of the last cells appears.
    private func autoLoadMoreReels(near reel: ReelItem) {
        guard feed.reelCursor >= 0, !feed.isLoadingMoreReels,
              let idx = feed.reelItems.firstIndex(where: { $0.id == reel.id }),
              idx >= feed.reelItems.count - 2 else { return }
        Task { await feed.loadMoreReels(store: store) }
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
                onOpenScript: { peekedScript = $0 },
                onRetryEdit: {
                    if let cid = chat.currentConversationId {
                        chat.retryEdit(cardId: message.id, convoId: cid, store: store)
                    }
                }
            )
        }
    }

    // MARK: Composer — pill with attach / field / morphing send-stop

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
                case .empty: composerFocused = true   // was the dictation mic (orb-era)
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
        .tourAnchor("tour.chatComposer")
        // The tab bar is a plain bottom overlay (never a safeAreaInset) — the composer
        // owns its clearance. When the keyboard is up the bar hides (composerFocused →
        // hideTabBar) so only a small margin is needed.
        .padding(.bottom, router.hideTabBar ? Space.sm : MarqueTabBar.clearance)
        .animation(Motion.quick, value: router.hideTabBar)
    }

    private func sendDraft() {
        let text = trimmedDraft
        guard !text.isEmpty else { return }
        draft = ""
        chat.send(text, store: store)
    }

    /// P7.3: an insight card (or its push) routed here with a prompt — pre-fill the
    /// composer so the creator can send (or edit) it in one tap.
    private func consumePendingPrompt() {
        guard let p = router.pendingChatPrompt, !p.isEmpty else { return }
        draft = p
        router.pendingChatPrompt = nil
        composerFocused = true
    }
}
