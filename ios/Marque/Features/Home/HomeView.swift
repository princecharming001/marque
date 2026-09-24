import SwiftUI

// Home — the daily driver. Centerpiece is the voice bubble (talk to Marque);
// below it the full daily feed: script picks (carousel), a quiet trend ticker,
// and influencer reels to mimic ("Steal these"). Feed state lives in FeedStore.
struct HomeView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(TourManager.self) private var tour
    @State private var showVoice = false
    // UX-F1: app-owned (MarqueApp) so the feed survives tab switches — a view-owned
    // @State store was torn down with HomeView on every RootTabView switch, forcing
    // skeletons + a refetch every time the creator came back to Home.
    @Environment(FeedStore.self) private var feed
    @State private var selectedReel: ReelItem?
    @State private var peekedScript: Script?    // tapped pick card → full script sheet
    // Single-player grid: the one "Steal these" cell allowed to stream right now
    // (most-recently-appeared wins; see ReelCard).
    @State private var activeReelId: String?
    // Presentation only: measured carousel width, so pick cards size to the phone.
    @State private var carouselWidth: CGFloat = 375

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.sectionGap) {
                // Stoic "Today" header: streak · greeting · avatar, then the 7-day posting
                // strip. Grouped so the header reads as one block. (Owner 2026-09-23: no
                // date or @handle line under the greeting.)
                VStack(spacing: Space.md) {
                    topBar
                    DSWeekStrip(days: weekDays)
                }
                .staggerReveal(0)
                // The one hero card per screen: the voice orb (VoiceBubble draws the card).
                VoiceBubble { showVoice = true }
                    .tourAnchor("tour.voiceBubble")
                    .staggerReveal(1)
                    .padding(.top, -Space.sm)
                picksSection.staggerReveal(2)
                if let trend = feed.trend {
                    TrendTicker(trend: trend, all: store.trends).staggerReveal(3)
                }
                stealSection.staggerReveal(4)
            }
            .screenPadding()
            // Owner 2026-09-23: the greeting sat too tight under the status bar on device.
            .padding(.top, Space.md)
            .padding(.bottom, MarqueTabBar.clearance + Space.xxl)
        }
        .background(Palette.canvas.ignoresSafeArea())
        // The nav bar is hidden, so scrolled content ran straight under the system
        // clock ("STEAL TH[3:55]ESE"). A canvas fade over the status-bar band keeps
        // that region legible without blocking touches or reserving layout space.
        .overlay(alignment: .top) {
            GeometryReader { geo in
                LinearGradient(colors: [Palette.canvas, Palette.canvas.opacity(0)],
                               startPoint: .top, endPoint: .bottom)
                    .frame(height: geo.safeAreaInsets.top + 14)
            }
            .ignoresSafeArea(edges: .top)
            .allowsHitTesting(false)
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
        .sheet(isPresented: $showVoice) { VoiceSessionView() }
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
        .task { await feed.loadInitial(store: store) }
        .task { await store.loadTrends() }          // W1: full niche-trend list for the rotating ticker
        .refreshable { await feed.refresh(store: store) }
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

    // MARK: Top bar — streak · greeting · profile avatar (Stoic "Today" header)

    private var topBar: some View {
        ZStack {
            // Centered lowercase greeting with a period ("good evening."). Side slots are
            // reserved so a long greeting shrinks instead of running under the controls.
            Text(greeting)
                .font(AppFont.title2).tracking(-0.2)
                .foregroundStyle(Palette.textPrimary)
                .lineLimit(1).minimumScaleFactor(0.75)
                .padding(.horizontal, 76)
                .accessibilityAddTraits(.isHeader)
            HStack(alignment: .center) {
                DSStreakPill(count: store.streak)
                Spacer()
                NavigationLink(value: "profile") {
                    avatarButton
                        .frame(width: 44, height: 44)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("home.profile")
            }
        }
    }

    private var avatarButton: some View {
        ZStack {
            Circle().fill(Palette.surfaceSunken).frame(width: 32, height: 32)
            if let url = store.primaryAccount?.avatarUrl, !url.isEmpty, let u = URL(string: url) {
                AsyncImage(url: u) { img in img.resizable().scaledToFill() } placeholder: { initial }
                    .frame(width: 32, height: 32).clipShape(Circle())
            } else {
                initial
            }
        }
        .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private var initial: some View {
        Text(String((store.primaryAccount?.handle ?? store.brand.niche).prefix(1)).uppercased())
            .font(AppFont.caption.weight(.semibold)).foregroundStyle(Palette.textPrimary)
    }

    private var greeting: String {
        let hour = Calendar.current.component(.hour, from: Date())
        return hour < 12 ? "good morning." : hour < 18 ? "good afternoon." : "good evening."
    }

    /// This calendar week, read-only from the posting schedule: a day is checked when a
    /// post actually went out that day; today is outlined; later days are muted.
    private var weekDays: [DSWeekDay] {
        let cal = Calendar.current
        let today = cal.startOfDay(for: Date())
        guard let week = cal.dateInterval(of: .weekOfYear, for: today) else { return [] }
        let postedDays = Set(store.schedule
            .filter { $0.posted || $0.outcome?.didPost == true }
            .map { cal.startOfDay(for: $0.date) })
        let symbols = cal.shortWeekdaySymbols
        return (0..<7).compactMap { i -> DSWeekDay? in
            guard let d = cal.date(byAdding: .day, value: i, to: week.start) else { return nil }
            let day = cal.startOfDay(for: d)
            let sym = symbols[(cal.component(.weekday, from: day) - 1) % symbols.count]
            return DSWeekDay(id: "\(Int(day.timeIntervalSince1970))",
                             weekday: String(sym.prefix(2)),
                             number: "\(cal.component(.day, from: day))",
                             done: postedDays.contains(day),
                             isToday: day == today,
                             isFuture: day > today)
        }
    }

    // MARK: Today's picks — peeking carousel of surface cards (FeedStore page 0+)

    /// Card width: the page width less both margins, less a little more so the next card
    /// visibly peeks past the trailing edge (Stoic's carousel). Measured, never fixed.
    private var pickCardWidth: CGFloat {
        max(240, carouselWidth - 2 * Space.screenH - Space.md)
    }

    private var picksSection: some View {
        VStack(alignment: .center, spacing: Space.md) {
            DSEyebrow(text: "Today's picks")
            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(spacing: Space.stack) {
                    if feed.scriptItems.isEmpty && feed.isLoading {
                        FeedSkeletonCard().frame(width: pickCardWidth)
                        FeedSkeletonCard().frame(width: pickCardWidth)
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
                            .frame(width: pickCardWidth)
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
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { carouselWidth = $0 }
        }
        .frame(maxWidth: .infinity)
    }

    /// Trailing "More" card — pulls the next mixed-feed page (scripts only land here).
    private var morePicksCard: some View {
        Button {
            Task { await feed.loadMoreScripts(store: store) }
        } label: {
            VStack(spacing: Space.sm) {
                if feed.isLoadingMoreScripts {
                    ProgressView().tint(Palette.textSecondary)
                } else {
                    Image(systemName: "arrow.right")
                        .font(.system(size: 17, weight: .regular))
                        .foregroundStyle(Palette.textPrimary)
                        .frame(width: 44, height: 44)
                        .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
                    Text("More").font(AppFont.supporting).foregroundStyle(Palette.textPrimary)
                }
            }
            .frame(width: 96, height: FeedCardMetrics.pickHeight)   // matches ScriptFeedCard's height
            .background(Palette.surface)
            .clipShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
        }
        .buttonStyle(PressableStyle(dim: 0.7))
        .disabled(feed.isLoadingMoreScripts)
        .accessibilityIdentifier("feed.moreScripts")
    }

    /// Shown only when the initial feed load came back empty (offline / backend miss).
    private var picksOfflineCard: some View {
        VStack(spacing: Space.sm) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 22, weight: .regular)).foregroundStyle(Palette.textSecondary)
                .padding(.bottom, Space.xs)
            Text("Couldn't load today's picks")
                .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
            Text("Pull down to refresh when you're back online.")
                .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(Space.cardPad)
        .frame(width: pickCardWidth, height: FeedCardMetrics.pickHeight)   // matches ScriptFeedCard
        .background(RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }

    // MARK: Steal these — proven reels from the niche, 2-col grid + own pagination

    private var reelColumns: [GridItem] {
        [GridItem(.flexible(), spacing: Space.md), GridItem(.flexible())]
    }

    private var stealSection: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            VStack(spacing: Space.xs) {
                DSEyebrow(text: "Steal these")
                // Only promise "your niche" when the server actually served it — a cold
                // niche cache falls back to the cross-niche aggregate (off_niche flag),
                // and claiming those are "from your niche" is how a photographer tester
                // concluded the app had no idea what their account was about.
                Text(feed.reelsAreOffNiche
                     ? "Still scanning your niche, here's what's working elsewhere meanwhile."
                     : "Proven reels from your niche, mimic them in your voice.")
                    .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity)
            if feed.reelItems.isEmpty && feed.isLoading {
                LazyVGrid(columns: reelColumns, spacing: Space.md) {
                    ReelSkeletonCard()
                    ReelSkeletonCard()
                }
            } else if feed.reelItems.isEmpty {
                EmptyStateView(icon: "rectangle.stack.badge.play",
                               title: "Finding real reels…",
                               message: "We're scanning your niche and the creators you watch for reels that are actually performing. Pull to refresh in a moment, or add creators to watch in your profile.")
            } else {
                LazyVGrid(columns: reelColumns, spacing: Space.md) {
                    ForEach(feed.reelItems) { r in
                        ReelCard(reel: r, activeReelId: $activeReelId) { selectedReel = r }
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

}
