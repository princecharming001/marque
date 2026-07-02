import Foundation
import Observation

// The Home daily feed's own state: mixed pages of scripts + influencer reels + a trend,
// split into section buckets with independent "load more" cursors. Kept separate from
// AppStore because the feed is ephemeral browse-state (re-fetched, not persisted) —
// only what the creator *saves* graduates into AppStore.readiedScripts.
@MainActor
@Observable
final class FeedStore {
    // Section buckets (a feed page ≈ 3 scripts + 4 reels + 1 trend, split on arrival)
    var scriptItems: [Script] = []
    var reelItems: [ReelItem] = []
    var trend: TrendItem? = nil

    // Loading flags
    var isLoading = false                 // initial page
    var isLoadingMoreScripts = false      // "More" pill in the picks carousel
    var isLoadingMoreReels = false        // "Load more reels" under the grid

    // Cursors: -1 means exhausted (hide the corresponding load-more control).
    var feedCursor: Int = 0               // mixed-feed pagination (scripts + trend)
    var reelCursor: Int = 1               // reels-only pagination; page 0's reels came in the feed
    var loadedOnce = false

    // MARK: Initial load

    func loadInitial(store: AppStore) async {
        guard !loadedOnce, !isLoading else { return }
        loadedOnce = true
        isLoading = true
        defer { isLoading = false }

        guard let page = await store.backend.fetchFeed(brand: store.brand, cursor: 0) else {
            loadedOnce = false            // network miss — allow a later re-appear / pull to retry
            return
        }
        ingest(page.entries, includeReels: true)
        feedCursor = page.nextCursor ?? -1
        reelCursor = 1                    // page 0's reels arrived inside the feed
    }

    // MARK: Load more scripts (mixed-feed cursor; reels in these pages are ignored —
    // the reels section paginates itself via /v1/reels)

    func loadMoreScripts(store: AppStore) async {
        guard feedCursor >= 0, !isLoadingMoreScripts, !isLoading else { return }
        isLoadingMoreScripts = true
        defer { isLoadingMoreScripts = false }

        guard let page = await store.backend.fetchFeed(brand: store.brand, cursor: feedCursor) else { return }
        ingest(page.entries, includeReels: false)
        feedCursor = page.nextCursor ?? -1
    }

    // MARK: Load more reels (reels-only endpoint)

    func loadMoreReels(store: AppStore) async {
        guard reelCursor >= 0, !isLoadingMoreReels, !isLoading else { return }
        isLoadingMoreReels = true
        defer { isLoadingMoreReels = false }

        guard let result = await store.backend.fetchReels(brand: store.brand, cursor: reelCursor) else { return }
        for r in result.reels { appendReel(r) }
        reelCursor = result.nextCursor ?? -1
    }

    // MARK: Pull-to-refresh — full reset + reload

    func refresh(store: AppStore) async {
        guard !isLoading else { return }
        scriptItems = []
        reelItems = []
        trend = nil
        feedCursor = 0
        reelCursor = 1
        isLoadingMoreScripts = false
        isLoadingMoreReels = false
        loadedOnce = false
        await loadInitial(store: store)
    }

    // MARK: Bucketing + dedupe

    private func ingest(_ entries: [BackendClient.FeedEntry], includeReels: Bool) {
        for entry in entries {
            switch entry {
            case .script(let s):
                appendScript(s)
            case .reel(let r):
                if includeReels { appendReel(r) }
            case .trend(let t):
                if trend == nil { trend = t }
            }
        }
    }

    private func appendScript(_ s: Script) {
        guard !scriptItems.contains(where: { $0.id == s.id }) else { return }
        scriptItems.append(s)
    }

    private func appendReel(_ r: ReelItem) {
        guard !reelItems.contains(where: { $0.id == r.id }) else { return }
        reelItems.append(r)
    }
}
