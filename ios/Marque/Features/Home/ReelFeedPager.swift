import SwiftUI

// "Steal these" as a full-screen, TikTok/Instagram-style vertical feed: swipe up for the
// next proven reel, endlessly. The backend serves an effectively infinite feed (cycling
// proven reels once uniques run out), and this pager auto-loads the next page as you near
// the end — so the scroll never dead-ends. Only the on-screen reel plays sound; the rest
// are muted. "Mimic in my voice" is one tap away on every reel; "Details" opens the full
// teardown sheet.
struct ReelFeedPager: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    let feed: FeedStore
    let startReel: ReelItem

    @State private var currentId: ReelItem.ID?
    @State private var detailReel: ReelItem?
    @State private var mimicking: ReelItem.ID?          // a mimic() is in flight for this reel
    @State private var mimicFailed: ReelItem.ID?
    // Lazily-fetched creator profiles (pfp + follower count), keyed by handle; and the set of
    // creators the user has tracked this session (reflected into brand.watchedCreators).
    @State private var profiles: [String: (pfp: String, followers: Int)] = [:]
    @State private var justTracked: Set<String> = []

    private func isTracked(_ handle: String) -> Bool {
        justTracked.contains(handle.lowercased())
            || (store.brand.watchedCreators ?? []).contains { $0.handle.lowercased() == handle.lowercased() }
    }

    private func trackCreator(_ reel: ReelItem) {
        let h = reel.creatorHandle
        guard !isTracked(h) else { return }
        justTracked.insert(h.lowercased())
        let plat: SocialPlatform = reel.platform == "tiktok" ? .tiktok : .instagram
        var list = store.brand.watchedCreators ?? []
        if !list.contains(where: { $0.handle.lowercased() == h.lowercased() }) {
            list.append(WatchedCreator(platform: plat, handle: h))
            store.brand.watchedCreators = Array(list.suffix(20))   // keep the most-recent 20
            store.save()
            Task { _ = await store.backend.warmWatchedCreator(handle: h, platform: plat.rawValue) }
        }
    }

    private func fetchProfile(for reel: ReelItem) {
        let key = reel.creatorHandle.lowercased()
        guard profiles[key] == nil, !reel.creatorHandle.isEmpty else { return }
        Task {
            if let p = await store.backend.creatorProfile(handle: reel.creatorHandle, platform: reel.platform) {
                profiles[key] = (p.pfpURL, p.followers)
            }
        }
    }

    private func openProfile(_ reel: ReelItem) {
        let s = reel.profileURL.isEmpty
            ? (reel.platform == "tiktok" ? "https://www.tiktok.com/@\(reel.creatorHandle)"
                                         : "https://www.instagram.com/\(reel.creatorHandle)")
            : reel.profileURL
        if let url = URL(string: s) { openURL(url) }
    }

    var body: some View {
        ScrollView(.vertical) {
            LazyVStack(spacing: 0) {
                ForEach(feed.reelItems) { reel in
                    cell(reel)
                        .containerRelativeFrame([.horizontal, .vertical])
                        .id(reel.id)
                        .onAppear { maybeLoadMore(around: reel) }
                }
            }
            .scrollTargetLayout()
        }
        .scrollTargetBehavior(.paging)
        .scrollPosition(id: $currentId)
        .scrollIndicators(.hidden)
        .ignoresSafeArea()
        .background(Color.black)
        .overlay(alignment: .topTrailing) { closeButton }
        .task { if currentId == nil { currentId = startReel.id } }
        .sheet(item: $detailReel) { ReelDetailSheet(reel: $0) }
        .preferredColorScheme(.dark)
    }

    private var closeButton: some View {
        Button { dismiss() } label: {
            Image(systemName: "xmark").font(.system(size: 15, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 34, height: 34)
                .background(.black.opacity(0.35), in: Circle())
        }
        .buttonStyle(.plain)
        .padding(.top, Space.xl)
        .padding(.trailing, Space.md)
        .accessibilityIdentifier("reelPager.close")
    }

    @ViewBuilder private func cell(_ reel: ReelItem) -> some View {
        ZStack {
            Color.black
            ReelPagerMedia(reel: reel, active: reel.id == currentId)
            // Legibility scrim + bottom info/actions overlay.
            LinearGradient(colors: [.clear, .clear, .black.opacity(0.55), .black.opacity(0.8)],
                           startPoint: .top, endPoint: .bottom)
                .allowsHitTesting(false)
            VStack(alignment: .leading, spacing: Space.sm) {
                Spacer()
                creatorRow(reel)
                if !reel.hookText.isEmpty {
                    Text(reel.hookText)
                        .font(AppFont.body).foregroundStyle(.white.opacity(0.95))
                        .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                }
                HStack(spacing: Space.md) {
                    Label(compactNumber(reel.views), systemImage: "eye")
                    Label(compactNumber(reel.likes), systemImage: "heart")
                }
                .font(AppFont.caption).foregroundStyle(.white.opacity(0.8))
                actionRow(reel)
            }
            .onAppear { fetchProfile(for: reel) }
            .padding(Space.lg)
            // The pager .ignoresSafeArea() (so the video fills), so the overlay must add the
            // bottom safe-area inset itself — otherwise the 50pt Mimic button is clipped under
            // the home indicator ("mimic not visible properly").
            .safeAreaPadding(.bottom, Space.sm)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
        }
    }

    /// Creator identity: real profile pic + tappable @handle (→ their Instagram/TikTok) +
    /// follower count. pfp/followers arrive lazily from GET /v1/reels/creator; until then the
    /// avatar is a monogram placeholder and the follower line is hidden.
    @ViewBuilder private func creatorRow(_ reel: ReelItem) -> some View {
        let prof = profiles[reel.creatorHandle.lowercased()]
        HStack(spacing: Space.sm) {
            avatar(reel, pfp: prof?.pfp ?? "")
            VStack(alignment: .leading, spacing: 1) {
                Button { openProfile(reel) } label: {
                    HStack(spacing: 5) {
                        Text("@\(reel.creatorHandle)").font(AppFont.headline).lineLimit(1)
                        Image(systemName: "arrow.up.right").font(.system(size: 10, weight: .bold)).opacity(0.7)
                    }.foregroundStyle(.white)
                }.buttonStyle(.plain).accessibilityIdentifier("reelPager.handle")
                if let f = prof?.followers, f > 0 {
                    Text("\(compactNumber(f)) followers")
                        .font(AppFont.micro).foregroundStyle(.white.opacity(0.75))
                }
            }
            if reel.fromWatched {
                Text("WATCHING").font(AppFont.micro).tracking(0.5)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .foregroundStyle(.white)
                    .background(Palette.accent.opacity(0.25), in: Capsule())
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder private func avatar(_ reel: ReelItem, pfp: String) -> some View {
        let ring = Circle().strokeBorder(.white.opacity(0.6), lineWidth: 1.5)
        Group {
            if let url = URL(string: pfp), !pfp.isEmpty {
                AsyncImage(url: url) { img in img.resizable().scaledToFill() }
                placeholder: { monogram(reel.creatorHandle) }
            } else {
                monogram(reel.creatorHandle)
            }
        }
        .frame(width: 34, height: 34).clipShape(Circle()).overlay(ring)
    }

    private func monogram(_ handle: String) -> some View {
        Circle().fill(Palette.accent.opacity(0.35)).overlay(
            Text(String(handle.prefix(1)).uppercased()).font(AppFont.caption.weight(.bold)).foregroundStyle(.white))
    }

    /// Elegant, minimal "track this creator" pill — feeds their reels into the feed as
    /// inspiration for future videos. Reflects into brand.watchedCreators.
    private func trackButton(_ reel: ReelItem) -> some View {
        let tracked = isTracked(reel.creatorHandle)
        return Button { trackCreator(reel) } label: {
            Image(systemName: tracked ? "checkmark" : "plus")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(tracked ? Palette.accent : .white)
                .frame(width: 50, height: 50)
                .background(.white.opacity(0.15), in: RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        }
        .buttonStyle(.plain)
        .disabled(tracked)
        .accessibilityIdentifier("reelPager.track")
    }

    private func actionRow(_ reel: ReelItem) -> some View {
        HStack(spacing: Space.sm) {
            Button { runMimic(reel) } label: {
                HStack(spacing: 6) {
                    if mimicking == reel.id {
                        ProgressView().tint(Palette.onInk)
                        Text("Rewriting…").font(AppFont.headline)
                    } else {
                        Image(systemName: "wand.and.stars").font(.system(size: 15, weight: .semibold))
                        Text(mimicFailed == reel.id ? "Try again" : "Mimic in my voice").font(AppFont.headline)
                    }
                }
                .foregroundStyle(Palette.onInk)
                .frame(maxWidth: .infinity).frame(height: 50)
                .background(.white, in: RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            }
            .buttonStyle(PressableStyle())
            .disabled(mimicking == reel.id)
            .accessibilityIdentifier("reelPager.mimic")

            trackButton(reel)

            Button {
                var r = reel      // carry the lazily-fetched pfp/followers into the stats sheet
                if let p = profiles[reel.creatorHandle.lowercased()] { r.pfpURL = p.pfp; r.followerCount = p.followers }
                detailReel = r
            } label: {
                Image(systemName: "chart.bar.fill").font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 50, height: 50)
                    .background(.white.opacity(0.15), in: RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("reelPager.details")
        }
    }

    /// Endless scroll: when a reel within the last few appears, pull the next page. The
    /// backend keeps producing pages, so this never dead-ends.
    private func maybeLoadMore(around reel: ReelItem) {
        guard let idx = feed.reelItems.firstIndex(where: { $0.id == reel.id }) else { return }
        if idx >= feed.reelItems.count - 3 {
            Task { await feed.loadMoreReels(store: store) }
        }
    }

    private func runMimic(_ reel: ReelItem) {
        guard mimicking == nil else { return }
        mimicking = reel.id; mimicFailed = nil
        Task {
            let result = await store.backend.mimic(reelItem: reel, brand: store.brand, memory: store.memory)
            mimicking = nil
            if let result {
                store.readyScript(result.script, source: .mimic, mimickedFrom: result.from)
                router.pendingFilmScriptId = result.script.id
                dismiss()
                router.showFilm = true
            } else {
                mimicFailed = reel.id
            }
        }
    }
}

// One reel's media, resilient to dead CDN URLs (video → thumbnail → hook panel) and muted
// unless it's the on-screen page. Full-bleed, aspect-filled to cover the frame like the
// platforms do.
private struct ReelPagerMedia: View {
    let reel: ReelItem
    let active: Bool
    @State private var videoFailed = false
    @State private var thumbFailed = false

    var body: some View {
        if !reel.videoURL.isEmpty && !videoFailed, let url = URL(string: reel.videoURL) {
            FailableVideoPlayer(url: url, muted: !active, showsControls: false,
                                isActive: active,      // only the current reel plays; others pause
                                onFailure: { videoFailed = true })
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .clipped()
        } else if !reel.thumbnailURL.isEmpty && !thumbFailed, let url = URL(string: reel.thumbnailURL) {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let img): img.resizable().scaledToFill()
                case .failure: Color.black.onAppear { thumbFailed = true }
                default: ProgressView().tint(.white)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity).clipped()
        } else {
            VStack {
                Spacer()
                Text(reel.hookText.isEmpty ? "@\(reel.creatorHandle)" : reel.hookText)
                    .font(Typeface.display(26)).foregroundStyle(.white)
                    .multilineTextAlignment(.center).padding(Space.xl)
                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}
