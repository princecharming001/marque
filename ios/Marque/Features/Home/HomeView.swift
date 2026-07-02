import SwiftUI

// Home — the daily driver. Centerpiece is the voice bubble (talk to Marque);
// below it the full daily feed: script picks (carousel), a quiet trend ticker,
// and influencer reels to mimic ("Steal these"). Feed state lives in FeedStore.
struct HomeView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var showVoice = false
    @State private var feed = FeedStore()
    @State private var selectedReel: ReelItem?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                topBar
                greetingBlock.staggerReveal(0)
                VoiceBubble(memoryLine: memoryLine) { showVoice = true }
                    .staggerReveal(1)
                picksSection.staggerReveal(2)
                if let trend = feed.trend {
                    TrendTicker(trend: trend).staggerReveal(3)
                }
                stealSection.staggerReveal(4)
                quietRows.staggerReveal(5)
            }
            .screenPadding()
            .padding(.top, Space.lg)
            .padding(.bottom, 140)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
        .sheet(isPresented: $showVoice) { VoiceSessionView() }
        .sheet(item: $selectedReel) { reel in
            ReelDetailSheet(reel: reel)
        }
        .task { await feed.loadInitial(store: store) }
        .refreshable { await feed.refresh(store: store) }
        .navigationDestination(for: String.self) { dest in
            if dest == "profile" { ProfileView() }
        }
    }

    // MARK: Top bar — date + streak + profile avatar

    private var dateKicker: String {
        Date().formatted(.dateTime.weekday(.wide).month(.abbreviated).day()).uppercased()
    }

    private var topBar: some View {
        HStack(alignment: .center) {
            Text(dateKicker)
                .font(AppFont.micro).tracking(Track.label)
                .foregroundStyle(Palette.textTertiary)
            Spacer()
            if store.streak > 0 { StreakGlyph(count: store.streak).padding(.trailing, Space.xs) }
            NavigationLink(value: "profile") {
                avatarButton
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("home.profile")
        }
    }

    private var avatarButton: some View {
        ZStack {
            Circle().fill(Palette.accent.opacity(0.12)).frame(width: 34, height: 34)
            if let url = store.brand.connectedAccounts.first?.avatarUrl, !url.isEmpty, let u = URL(string: url) {
                AsyncImage(url: u) { img in img.resizable().scaledToFill() } placeholder: { initial }
                    .frame(width: 34, height: 34).clipShape(Circle())
            } else {
                initial
            }
        }
        .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private var initial: some View {
        Text(String((store.brand.connectedAccounts.first?.handle ?? store.brand.niche).prefix(1)).uppercased())
            .font(Typeface.display(15, .semibold)).foregroundStyle(Palette.accent)
    }

    private var greeting: String {
        let hour = Calendar.current.component(.hour, from: Date())
        let part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"
        if let h = store.brand.connectedAccounts.first?.handle, !h.isEmpty { return "\(part), @\(h)" }
        return part
    }

    private var greetingBlock: some View {
        Text(greeting)
            .font(Typeface.display(34)).tracking(-0.8)
            .foregroundStyle(Palette.textPrimary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var memoryLine: String {
        if !store.memory.angle.isEmpty { return "Working on: \(store.memory.angle)" }
        if let idea = store.memory.ideas.last { return "Last idea: \(idea)" }
        return "Tell me what's on your mind this morning."
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
                                saved: store.readiedScripts.contains { $0.script.id == s.id }
                            )
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
            .frame(width: 96, height: 190)
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
        .frame(width: 260, height: 190, alignment: .topLeading)
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
                Text("Proven reels from your niche — mimic them in your voice.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
            }
            if feed.reelItems.isEmpty && feed.isLoading {
                LazyVGrid(columns: reelColumns, spacing: Space.md) {
                    ReelSkeletonCard()
                    ReelSkeletonCard()
                }
            } else if feed.reelItems.isEmpty {
                EmptyStateView(icon: "rectangle.stack.badge.play",
                               title: "No reels yet",
                               message: "Pull down to refresh — we'll find proven reels from your niche.")
            } else {
                LazyVGrid(columns: reelColumns, spacing: Space.md) {
                    ForEach(feed.reelItems) { r in
                        ReelCard(reel: r) { selectedReel = r }
                    }
                }
                if feed.reelCursor >= 0 {
                    loadMoreReelsButton
                }
            }
        }
    }

    // GhostButton recipe, hand-rolled so the in-flight spinner can live inside the
    // same control (keeps `feed.moreReels` stable for Maestro).
    private var loadMoreReelsButton: some View {
        Button {
            Task { await feed.loadMoreReels(store: store) }
        } label: {
            Group {
                if feed.isLoadingMoreReels {
                    ProgressView().tint(Palette.textSecondary)
                } else {
                    Text("Load more reels").font(AppFont.headline)
                }
            }
            .foregroundStyle(Palette.textPrimary)
            .frame(maxWidth: .infinity).frame(height: 54)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
        }
        .buttonStyle(PressableStyle(dim: 0.7))
        .disabled(feed.isLoadingMoreReels)
        .accessibilityIdentifier("feed.moreReels")
    }

    // MARK: Quiet rows

    private var quietRows: some View {
        VStack(spacing: 0) {
            quietRow(icon: "chart.bar", title: "Performance", subtitle: "Queue + last 30 days") {
                router.selectedTab = .performance
            }
            MarqueHairline()
            quietRow(icon: "rectangle.stack", title: "Library", subtitle: "Clips + media") {
                router.selectedTab = .library
            }
        }
        .marqueCard(padding: 0)
    }

    private func quietRow(icon: String, title: String, subtitle: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: Space.md) {
                Image(systemName: icon).font(.system(size: 16)).foregroundStyle(Palette.accent)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                    Text(subtitle).font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                }
                Spacer()
                Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
            }
            .padding(Space.lg)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
