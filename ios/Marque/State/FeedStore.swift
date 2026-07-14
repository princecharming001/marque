import Foundation
import Observation

// The Home daily feed's own state: mixed pages of scripts + influencer reels + a trend,
// split into section buckets with independent "load more" cursors. Kept separate from
// AppStore because the feed is browse-state — but (UX-F2) it is no longer ephemeral:
// a disk snapshot (Documents/marque.feed.v1.json) makes Home paint instantly on
// tab-switch/relaunch, with a silent background revalidate replacing it when fresh
// data lands. Only what the creator *saves* still graduates into AppStore.readiedScripts.
@MainActor
@Observable
final class FeedStore {
    // Section buckets (a feed page ≈ 3 scripts + 4 reels + 1 trend, split on arrival)
    var scriptItems: [Script] = []
    var reelItems: [ReelItem] = []
    var trend: TrendItem? = nil

    // Loading flags
    var isLoading = false                 // initial page (drives skeletons — never set when cache painted)
    var isLoadingMoreScripts = false      // "More" pill in the picks carousel
    var isLoadingMoreReels = false        // "Load more reels" under the grid

    // Cursors: -1 means exhausted (hide the corresponding load-more control).
    var feedCursor: Int = 0               // mixed-feed pagination (scripts + trend)
    var reelCursor: Int = 1               // reels-only pagination; page 0's reels came in the feed
    var loadedOnce = false
    // One-shot poller that swaps mock first-paint picks for the real AI ones when ready.
    private var aiUpgradeTask: Task<Void, Never>? = nil

    // MARK: UX-F2 — disk snapshot (instant paint)

    /// Everything needed to repaint Home exactly as it last looked.
    struct FeedSnapshot: Codable {
        var scripts: [Script] = []
        var reels: [ReelItem] = []
        var trend: TrendItem? = nil
        var feedCursor: Int = 0
        var reelCursor: Int = 1
        var savedAt: Date = Date()
        // The backend that produced this snapshot. If it differs from the current
        // backendBaseURL on load, the snapshot is discarded — otherwise a dev backend
        // switch (or a stale localhost override) repaints another environment's content
        // forever, which is exactly the stale-mock-reels incident.
        var backend: String = ""
    }

    /// True when init painted from disk — revalidates are then SILENT (no skeletons).
    private(set) var paintedFromDisk = false
    /// When the buckets last came from the network (drives the >15min staleness rule).
    private var lastFreshLoadAt: Date? = nil
    private var saveTask: Task<Void, Never>? = nil

    private static var snapshotURL: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("marque.feed.v1.json")
    }

    init() {
        // Instant paint: restore the last feed before any network work — but ONLY if it was
        // produced by the backend we're pointed at now (else discard, never repaint stale/
        // wrong-env content).
        if let data = try? Data(contentsOf: Self.snapshotURL),
           let snap = try? JSONDecoder().decode(FeedSnapshot.self, from: data),
           snap.backend == AppConfig.backendBaseURL,
           !snap.scripts.isEmpty || !snap.reels.isEmpty {
            scriptItems = snap.scripts
            reelItems = snap.reels
            trend = snap.trend
            feedCursor = snap.feedCursor
            reelCursor = snap.reelCursor
            lastFreshLoadAt = snap.savedAt
            paintedFromDisk = true
        } else {
            FeedStore.clearSnapshot()   // stale / wrong-backend / empty → drop it
        }
    }

    /// Delete the disk snapshot — on `-reset` and on a backend mismatch.
    static func clearSnapshot() {
        try? FileManager.default.removeItem(at: snapshotURL)
    }

    /// Debounced snapshot write — called after any ingest/refresh/dismiss mutation.
    private func scheduleSave() {
        saveTask?.cancel()
        saveTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            guard let self, !Task.isCancelled else { return }
            let snap = FeedSnapshot(scripts: self.scriptItems, reels: self.reelItems,
                                    trend: self.trend, feedCursor: self.feedCursor,
                                    reelCursor: self.reelCursor, savedAt: self.lastFreshLoadAt ?? Date(),
                                    backend: AppConfig.backendBaseURL)
            if let data = try? JSONEncoder().encode(snap) {
                try? data.write(to: Self.snapshotURL, options: .atomic)
            }
        }
    }

    /// UX-F2: foregrounding with a stale feed (>15 min) → silent revalidate.
    func revalidateIfStale(store: AppStore) async {
        guard let last = lastFreshLoadAt, Date().timeIntervalSince(last) > 15 * 60,
              !isLoading else { return }
        await silentRevalidate(store: store)
    }

    /// In-flight guard: the foreground trigger (revalidateIfStale) and loadInitial's
    /// painted-from-disk path could otherwise run two overlapping page-0 refetches
    /// racing on the same buckets.
    private var revalidating = false

    /// Re-fetch page 0 WITHOUT skeletons; replace the buckets only on success.
    private func silentRevalidate(store: AppStore) async {
        guard !revalidating else { return }
        revalidating = true
        defer { revalidating = false }
        guard let page = await store.backend.fetchFeed(brand: store.brand, memory: store.memory, cursor: 0) else { return }
        applyPageZero(page, store: store)
    }

    /// Commit a fresh page 0 into the buckets — the ONLY place that's allowed to clear
    /// them, and it runs strictly after a successful fetch (no await between clear and
    /// re-ingest, so the screen can never sit empty on a network miss).
    private func applyPageZero(_ page: BackendClient.FeedPage, store: AppStore) {
        // A page without a trend entry must not kill the ticker: keep the last one.
        let priorTrend = trend
        scriptItems = []; reelItems = []; trend = nil
        ingest(page.entries, includeReels: true, store: store)
        if trend == nil { trend = priorTrend }
        feedCursor = page.nextCursor ?? -1
        reelCursor = 1                    // page 0's reels arrived inside the feed
        lastFreshLoadAt = Date()
        loadedOnce = true
        scheduleSave()
        // First paint can be the instant "mock" fallback while the server generates the
        // real AI picks in the background (~60-90s). Silently re-fetch a couple times to
        // swap in the AI version the moment it's ready — so "Today's picks" never sits on
        // template copy without the creator having to pull-to-refresh.
        if page.mode == "mock" { scheduleAIUpgrade(store: store) }
    }

    // MARK: Initial load

    func loadInitial(store: AppStore) async {
        guard !loadedOnce, !isLoading else { return }
        loadedOnce = true

        // Cache painted → this is a background REVALIDATE, not a first load: no
        // skeletons, keep showing the snapshot until fresh data actually arrives.
        if paintedFromDisk {
            await silentRevalidate(store: store)
            return
        }

        isLoading = true
        defer { isLoading = false }

        // One quiet retry: a single transient blip (radio waking up, backend cold
        // start) used to land the error card immediately — the feed is the first
        // thing a creator sees, so it gets a second chance before giving up.
        var page = await store.backend.fetchFeed(brand: store.brand, memory: store.memory, cursor: 0)
        if page == nil {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            page = await store.backend.fetchFeed(brand: store.brand, memory: store.memory, cursor: 0)
        }
        guard let page else {
            loadedOnce = false            // network miss — allow a later re-appear / pull to retry
            return
        }
        applyPageZero(page, store: store)
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
        scheduleSave()
    }

    // MARK: I-2 — dismiss a pick (✗): remove it, learn from it, top up so the row never empties.
    func dismiss(_ s: Script, store: AppStore) {
        scriptItems.removeAll { $0.id == s.id }
        store.dismissPick(s)
        scheduleSave()
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
        scheduleSave()
    }

    // MARK: Pull-to-refresh

    /// Fetch FIRST, swap the buckets only when a fresh page actually arrives. The old
    /// clear-up-front version meant one transient failure during a pull-to-refresh
    /// blanked a previously-fine screen (error card + vanished ticker) — the reported
    /// "couldn't load today's picks for no reason" bug. The refresh spinner is the
    /// activity indicator; existing content stays put until it's replaced.
    func refresh(store: AppStore) async {
        guard !isLoading, !revalidating else { return }
        isLoadingMoreScripts = false
        isLoadingMoreReels = false
        // Skeletons only when there's nothing on screen to keep showing.
        isLoading = scriptItems.isEmpty && reelItems.isEmpty
        defer { isLoading = false }
        guard let page = await store.backend.fetchFeed(brand: store.brand, memory: store.memory, cursor: 0) else { return }
        applyPageZero(page, store: store)
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
        scheduleSave()
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
