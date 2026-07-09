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
    // One-shot poller that swaps mock first-paint picks for the real AI ones when ready.
    private var aiUpgradeTask: Task<Void, Never>? = nil

    // MARK: Initial load

    func loadInitial(store: AppStore) async {
        guard !loadedOnce, !isLoading else { return }
        loadedOnce = true
        isLoading = true
        defer { isLoading = false }

        guard let page = await store.backend.fetchFeed(brand: store.brand, memory: store.memory, cursor: 0) else {
            loadedOnce = false            // network miss — allow a later re-appear / pull to retry
            return
        }
        ingest(page.entries, includeReels: true, store: store)
        feedCursor = page.nextCursor ?? -1
        reelCursor = 1                    // page 0's reels arrived inside the feed

        // First paint can be the instant "mock" fallback while the server generates the
        // real AI picks in the background (~60-90s). Silently re-fetch a couple times to
        // swap in the AI version the moment it's ready — so "Today's picks" never sits on
        // template copy without the creator having to pull-to-refresh.
        if page.mode == "mock" { scheduleAIUpgrade(store: store) }
    }

    private func scheduleAIUpgrade(store: AppStore) {
        aiUpgradeTask?.cancel()
        aiUpgradeTask = Task { [weak self] in
            for delay in [45, 40, 40] {           // ~45s, ~85s, ~125s after first paint
                try? await Task.sleep(nanoseconds: UInt64(delay) * 1_000_000_000)
                if Task.isCancelled { return }
                guard let self else { return }
                guard let page = await store.backend.fetchFeed(brand: store.brand, memory: store.memory, cursor: 0),
                      page.mode == "live" else { continue }
                // Real AI landed — swap the script picks in place (keep reels/trend/cursors).
                self.ingestScriptsOnly(page.entries, store: store)
                return
            }
        }
    }

    // MARK: Load more scripts (mixed-feed cursor; reels in these pages are ignored —
    // the reels section paginates itself via /v1/reels)

    func loadMoreScripts(store: AppStore) async {
        guard feedCursor >= 0, !isLoadingMoreScripts, !isLoading else { return }
        isLoadingMoreScripts = true
        defer { isLoadingMoreScripts = false }

        guard let page = await store.backend.fetchFeed(brand: store.brand, memory: store.memory, cursor: feedCursor) else { return }
        ingest(page.entries, includeReels: false, store: store)
        feedCursor = page.nextCursor ?? -1
    }

    // MARK: I-2 — dismiss a pick (✗): remove it, learn from it, top up so the row never empties.
    func dismiss(_ s: Script, store: AppStore) {
        scriptItems.removeAll { $0.id == s.id }
        store.dismissPick(s)
        if scriptItems.count < 3, feedCursor >= 0 {
            Task { await loadMoreScripts(store: store) }
        }
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

    private func ingest(_ entries: [BackendClient.FeedEntry], includeReels: Bool, store: AppStore) {
        for entry in entries {
            switch entry {
            case .script(let s):
                appendScript(s, store: store)
            case .reel(let r):
                if includeReels { appendReel(r) }
            case .trend(let t):
                if trend == nil { trend = t }
            }
        }
    }

    /// Replace ONLY the script picks (the AI upgrade) while leaving reels/trend/cursors
    /// intact — used when the background AI version of "Today's picks" arrives.
    private func ingestScriptsOnly(_ entries: [BackendClient.FeedEntry], store: AppStore) {
        let fresh = entries.compactMap { e -> Script? in
            if case .script(let s) = e, !store.dismissedPicks.contains(s.id) { return s } else { return nil }
        }
        guard !fresh.isEmpty else { return }
        scriptItems = fresh
    }

    private func appendScript(_ s: Script, store: AppStore) {
        guard !scriptItems.contains(where: { $0.id == s.id }),
              !store.dismissedPicks.contains(s.id) else { return }        // I-2: stay dismissed
        scriptItems.append(s)
    }

    private func appendReel(_ r: ReelItem) {
        guard !reelItems.contains(where: { $0.id == r.id }) else { return }
        reelItems.append(r)
    }
}
