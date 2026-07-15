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
    let feed: FeedStore
    let startReel: ReelItem

    @State private var currentId: ReelItem.ID?
    @State private var detailReel: ReelItem?
    @State private var mimicking: ReelItem.ID?          // a mimic() is in flight for this reel
    @State private var mimicFailed: ReelItem.ID?

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
                HStack(spacing: 6) {
                    Image(systemName: reel.platform == "instagram" ? "camera.fill" : "music.note")
                        .font(.system(size: 11, weight: .semibold))
                    Text("@\(reel.creatorHandle)").font(AppFont.headline).lineLimit(1)
                    if reel.fromWatched {
                        Text("WATCHING").font(AppFont.micro).tracking(0.5)
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(Palette.accent.opacity(0.25), in: Capsule())
                    }
                }
                .foregroundStyle(.white)
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
            .padding(Space.lg)
            .padding(.bottom, Space.xl)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
        }
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

            Button { detailReel = reel } label: {
                Image(systemName: "info.circle").font(.system(size: 18, weight: .semibold))
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
