import Foundation
import Observation
import UserNotifications
import AVFoundation

@MainActor
@Observable
final class AppStore {
    // Persisted-ish app state
    var brand = BrandGraph()
    var pillars: [Pillar] = []
    var scripts: [Script] = []
    var clips: [Clip] = []
    var footage: [Footage] = []          // filmed-but-undecided takes (Library "Footage")
    var media: [MediaAsset] = []         // personal media corpus the AI references
    var schedule: [ScheduledPost] = []
    var pendingPublishes: [ScheduledPost] = []   // C-03: posts that hit a transport failure, retried on reconnect
    var trends: [TrendItem] = []
    var teardowns: [TeardownCard] = []
    var hasOnboarded = false
    var streak = 0
    var lastStreakDate: Date? = nil      // C-06: day-based streak gate

    // V3: conversation memory + readied scripts + chat + edit prefs
    var memory = CreatorMemory()
    var readiedScripts: [SavedScript] = []       // the Film-flow queue ("save for later")
    var conversations: [Conversation] = []       // chat threads (incl. the pinned Voice notes)
    var editPrefs = EditPrefs() { didSet { backend.editPrefs = editPrefs.asDictionary } }
    var brandSummary: BrandSummaryCard? = nil    // cached Profile hero card
    var chatPersona: ChatPersona? = nil           // nil → .closer default (drawer picker)
    var chatResponseLength: ChatResponseLength? = nil   // nil → .medium default
    var coachPersona: ChatPersona? = nil          // performance coaching style (picker)

    // V3: account + subscription gates (onboarding → auth wall → paywall → app)
    let auth = AuthManager()
    let subscription = SubscriptionManager()

    // Transient
    var isGenerating = false
    var showCelebration = false
    var coaching = ""                    // this-week coaching line (interpretInsights)

    // Learning loop
    var learnedInsights: [String: Any] = [:]
    var recommendedArms: [[String: Any]] = []
    var learningProgress: Double = 0
    var postsLearned: Int = 0

    // The AI brain lives in the backend; the app is a thin client (no vendor keys on device).
    // Shared instance so AuthManager's creator-id/token wiring reaches every call site.
    let backend = BackendClient.shared
    var llm: LLMRouting { backend }
    var aiMode: String { backend.lastMode }
    // Live clip engine when the backend URL is configured, mock otherwise.
    var clipEngine: ClipEngineProtocol {
        AppConfig.backendBaseURL.isEmpty ? MockClipEngine() : LiveClipEngine()
    }
    // All publishing goes through the backend (which holds the Ayrshare key server-side).
    // BackendPublisher falls back to MockPublisher when the backend is unreachable.
    var publisher: Publishing { BackendPublisher() }
    let insights: InsightsProviding = LiveInsights()
    let remote: RemotePersistence = SupabaseStore()
    // V3: the app itself sits behind the subscription wall, so publishing is
    // implied by being inside — kept as a second line of defense.
    var canPublish: Bool { subscription.isSubscribed }

    private let saveKey = "marque.state.v1"

    init() {
        if CommandLine.arguments.contains("-reset") {
            UserDefaults.standard.removeObject(forKey: saveKey)
        }
        load()
        backend.editPrefs = editPrefs.asDictionary
        #if DEBUG
        // Deterministic Maestro/UI-audit entry: land straight on Home (all gates open),
        // bypassing the live plan-build step. Mirrors DevJumpMenu.jumpToHome but as a
        // launch arg so it doesn't depend on a flaky confirmationDialog tap.
        if CommandLine.arguments.contains("-demoHome") {
            hasOnboarded = true
            auth.continueAsDemo()
            subscription.devContinue()
            // A demo home with an empty brand fetches an empty feed (no niche →
            // no reels/trends). Give it a real one so demo == the real experience.
            if brand.niche.isEmpty { brand.niche = "fitness" }
        }
        // Deterministic editor-verification entry: seed one READY clip with a
        // placeholder jobId so Library → Edit manually → ProEditorView opens
        // without driving the full record→makeClips flow. ProEditorView's
        // placeholder mode handles the absent source video. This is the standing
        // E-25 exit-gate harness (`.maestro/editor-pro-flow.yaml`).
        if CommandLine.arguments.contains("-demoClip") {
            hasOnboarded = true
            auth.continueAsDemo()
            subscription.devContinue()
            let script = Script(
                pillarName: "Your script",
                title: "The one system that actually works",
                summary: "Written by you",
                style: VideoStyle.talkingHead.rawValue,
                formatId: "myth-buster",
                hook: Hook(text: "Stop overthinking your content.", signal: .narrative, strength: 78),
                altHooks: [],
                body: "Stop overthinking your content. Here is the one system that actually works. Pick one idea, film it in a single take, and ship it. Follow for more.",
                cta: "Follow for more",
                shotPlan: ["Hook on frame 1, direct eye contact", "One punch-in on the key line", "CTA to camera"],
                targetSeconds: 22,
                predictedScore: 78
            )
            scripts.insert(script, at: 0)
            var clip = Clip(scriptId: script.id, formatId: script.formatId,
                            formatName: "Myth-buster", caption: script.body,
                            predictedScore: 78, status: .ready, seconds: 22)
            clip.title = script.title
            clip.jobId = "demo-clip-job"     // non-nil ⇒ Library shows "Edit manually"
            clips.insert(clip, at: 0)
        }
        #endif
    }

    // MARK: Onboarding

    /// A short, niche-aware fallback pillar set (used on the "skip" path). The richer set
    /// — with each pillar's angle + example topics — comes from the LLM via analyzePage().
    func derivePillars() {
        let niche = brand.niche.isEmpty ? "your field" : brand.niche
        let specs: [(String, String)] = [
            ("Teach the fundamentals", "Lessons that make your audience better at \(niche)."),
            ("Myth-busting", "Contrarian takes that fix what people get wrong about \(niche)."),
            ("Behind the scenes", "The real, unpolished story of your work."),
            ("Hot takes", "Sharp opinions that start conversations in \(niche)."),
            ("Proof & results", "Receipts: transformations and case studies in \(niche)."),
        ]
        let colors = Catalog.pillarColors
        pillars = specs.enumerated().map { i, s in
            Pillar(name: s.0, summary: s.1, weight: 0.2, colorHex: colors[i % colors.count])
        }
        brand.topThemes = pillars.map { $0.name }
    }

    // MARK: Connected accounts

    func connectPreview(handle: String, platform: String) async -> ConnectedAccount? {
        await backend.connectPreview(handle: handle, platform: platform)
    }
    func addConnectedAccount(_ a: ConnectedAccount) {
        brand.connectedAccounts.removeAll { $0.platform == a.platform && $0.handle.lowercased() == a.handle.lowercased() }
        brand.connectedAccounts.append(a)
        if brand.pageHandle.isEmpty { brand.pageHandle = a.handle }
        // Everything a real linked account already tells us, the quiz should never
        // ask for: prefill name, derive the follower stage, and set the platform
        // (two platforms connected → "both", i.e. nil primary).
        if (brand.creatorName ?? "").isEmpty, !a.displayName.isEmpty {
            brand.creatorName = a.displayName
        }
        if brand.stage == nil, a.followers > 0 {
            brand.stage = .from(followers: a.followers)
        }
        if !hasOnboarded {
            // Only during onboarding — the quiz auto-skips its platform step when
            // accounts are linked. Post-onboarding, connecting a second account
            // must not clobber an explicitly chosen primary platform.
            let platforms = Set(brand.connectedAccounts.map(\.platform))
            brand.primaryPlatform = platforms.count == 1
                ? SocialPlatform(rawValue: platforms.first ?? "") : nil
        }
        save()
    }
    func removeConnectedAccount(_ a: ConnectedAccount) {
        // OAuth-linked accounts also get revoked on Post for Me so we stop being billable
        // for a dangling connection; fire-and-forget (local removal is the source of truth).
        if a.canPublish { Task { await backend.socialDisconnect(accountId: a.accountId) } }
        brand.connectedAccounts.removeAll { $0.id == a.id }
        save()
    }

    // MARK: OAuth account linking (Post for Me — real posting authority)

    /// Stable per-user tag Post for Me stores against the linked account so we can find it
    /// again. One account per (user, platform); relinking the same platform replaces it.
    func socialExternalId(platform: String) -> String {
        "\(auth.state?.userId ?? "anon")_\(platform)"
    }

    /// The Post for Me account ids to publish `platforms` to (OAuth-linked accounts only).
    func publishAccountIds(for platforms: [SocialPlatform]) -> [String] {
        brand.connectedAccounts
            .filter { acct in acct.canPublish && platforms.contains { $0.rawValue == acct.platform } }
            .map(\.accountId)
    }

    /// Ask the backend for the OAuth URL to connect `platform`. nil => linking unavailable
    /// (mock backend / no key). No redirect override is sent — Post for Me Quickstart uses
    /// its own fixed success page, so we confirm the link by polling instead of a callback.
    func socialAuthURL(platform: String) async -> URL? {
        let s = await backend.socialAuthURL(platform: platform,
                                            externalId: socialExternalId(platform: platform),
                                            redirectURL: "")
        return s.flatMap(URL.init(string:))
    }

    /// After the OAuth web flow closes, poll for the now-connected account (Post for Me can
    /// take a moment to finalize the link). Stores the account carrying its spc_ id on first
    /// hit. Returns true once linked.
    @discardableResult
    func refreshLinkedAccount(platform: String, retries: Int = 4) async -> Bool {
        for attempt in 0..<max(1, retries) {
            let linked = await backend.socialAccounts(externalId: socialExternalId(platform: platform))
            if var acct = linked.first(where: { $0.platform == platform && $0.canPublish }) {
                // Post for Me returns username + photo but no follower/bio — enrich from the
                // public profile for display + voice learning, keeping the spc_ accountId.
                if !acct.handle.isEmpty,
                   let preview = await backend.connectPreview(handle: acct.handle, platform: platform) {
                    acct.followers = preview.followers
                    acct.bio = preview.bio
                    if acct.avatarUrl.isEmpty { acct.avatarUrl = preview.avatarUrl }
                    if acct.displayName.isEmpty { acct.displayName = preview.displayName }
                }
                addConnectedAccount(acct)
                return true
            }
            if attempt < retries - 1 { try? await Task.sleep(nanoseconds: 1_500_000_000) }
        }
        return false
    }

    /// "Analyze my page" — runs real inference to design pillars tailored to the creator.
    /// When a connected account is present, calls /v1/brand-scan/handle so pillars derive
    /// from what the creator actually posts; falls back to /v1/pillars (generic) otherwise.
    func analyzePage() async {
        try? await Task.sleep(nanoseconds: 600_000_000)   // brief "reading your page" UX
        brand.analyzed = true

        // Prefer the brand-scan path when a handle is known — derives pillars from real posts.
        if let account = brand.connectedAccounts.first, !account.handle.isEmpty {
            if let result = await backend.brandScan(handle: account.handle,
                                                     platform: account.platform,
                                                     niche: brand.niche) {
                if !result.pillars.isEmpty {
                    pillars = result.pillars
                    brand.topThemes = result.topThemes
                    if let v = result.voiceUpdate { brand.voice = v }
                    applyScanIdentity(result)
                    save()
                    return
                }
            }
        }

        // Fallback: generic pillar generation from brand graph alone.
        let derived = await llm.generatePillars(brand: brand)
        if !derived.isEmpty {
            pillars = derived
            brand.topThemes = derived.map { $0.name }
        } else if pillars.isEmpty {
            derivePillars()
        }
        save()
    }

    /// Prefill identity the scan derived from real posts — fill-only-if-empty, so a
    /// user's own words are never clobbered by a guess. During onboarding this turns
    /// the freeform identity steps into confirm-not-type.
    private func applyScanIdentity(_ result: BackendClient.BrandScanResult) {
        func fill(_ path: WritableKeyPath<BrandGraph, String>, _ guess: String) {
            let g = guess.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !g.isEmpty,
                  brand[keyPath: path].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            else { return }
            brand[keyPath: path] = g
        }
        fill(\.niche, result.nicheGuess)
        fill(\.audience, result.audienceGuess)
        fill(\.knownFor, result.knownForGuess)
    }

    /// Apply a voice-onboarding finalize result (called after the conversational session).
    func applyVoiceScan(_ result: BackendClient.BrandScanResult) {
        if !result.pillars.isEmpty {
            pillars = result.pillars
            brand.topThemes = result.topThemes
        }
        if let v = result.voiceUpdate { brand.voice = v }
        applyScanIdentity(result)
        brand.analyzed = true
        save()
    }

    func completeOnboarding() {
        if pillars.isEmpty { derivePillars() }
        // The aha scripts become the first entries in the Film queue.
        for s in scripts.prefix(3) { readiedScripts.append(SavedScript(script: s, source: .onboarding)) }
        hasOnboarded = true
        save()
    }

    // MARK: Scripts

    /// A compact summary of the personal-media corpus, injected so the AI plans shots that
    /// reuse footage the creator already has.
    var mediaContext: String {
        guard !media.isEmpty else { return "" }
        let byKind = Dictionary(grouping: media, by: { $0.kind })
        let counts = byKind
            .sorted { $0.value.count > $1.value.count }
            .map { "\($0.value.count) \($0.key.label.lowercased())" }
            .joined(separator: ", ")
        let notes = media.compactMap { $0.note.isEmpty ? nil : $0.note }.prefix(6)
        return notes.isEmpty ? counts : "\(counts) — tagged: \(notes.joined(separator: ", "))"
    }

    // Style is chosen BEFORE generation — it determines the script structure.
    func generateScripts(for pillar: Pillar, style: VideoStyle, count: Int = 3) async {
        isGenerating = true
        let new = await llm.generateScripts(brand: brand, pillar: pillar, count: count, mediaContext: mediaContext, style: style, memory: memory)
        scripts.insert(contentsOf: new, at: 0)
        isGenerating = false
        save()
    }

    func generateStarterScripts() async {
        guard scripts.isEmpty, let p = pillars.first else { return }
        await generateScripts(for: p, style: brand.preferredStyles.first ?? .talkingHead, count: 3)
    }

    // MARK: Starter digest (onboarding plan-building — async + backgroundable)

    enum StarterScriptsState: Equatable {
        case idle
        case running(stage: Int)     // index into PlanBuildingView.stages
        case ready
        case failed
    }
    var starterScriptsState: StarterScriptsState = .idle
    private var digestTask: Task<Void, Never>?
    private static let digestJobKey = "marque.digest.jobId"

    /// Fired from the brand-mirror step ("Build my plan"). Prefers the backend
    /// digest job (scrape reels → transcribe → derive → write scripts; keeps
    /// running server-side if the app is closed) and falls back to the local
    /// keyless path so onboarding never dead-ends.
    func beginStarterScripts() {
        switch starterScriptsState {
        case .running, .ready: return
        default: break
        }
        if !scripts.isEmpty { starterScriptsState = .ready; return }
        starterScriptsState = .running(stage: 0)
        digestTask?.cancel()
        digestTask = Task { await runStarterDigest() }
    }

    /// Resume after a relaunch mid-job (building step onAppear).
    func resumeStarterDigestIfNeeded() {
        guard case .idle = starterScriptsState, scripts.isEmpty else { return }
        guard let jobId = UserDefaults.standard.string(forKey: Self.digestJobKey) else { return }
        starterScriptsState = .running(stage: 0)
        digestTask = Task {
            if await pollDigest(jobId: jobId) { return }
            await localStarterFallback()
        }
    }

    private func runStarterDigest() async {
        if let jobId = await backend.startBrandDigest(brand: brand) {
            UserDefaults.standard.set(jobId, forKey: Self.digestJobKey)
            if await pollDigest(jobId: jobId) { return }
        }
        await localStarterFallback()
    }

    /// Poll until ready/failed. Returns false when the job can't complete
    /// server-side (offline, backend restart) so the local fallback takes over.
    private func pollDigest(jobId: String) async -> Bool {
        var misses = 0
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(2))
            guard let s = await backend.pollBrandDigest(jobId: jobId, brand: brand) else {
                misses += 1
                if misses >= 3 { return false }
                continue
            }
            misses = 0
            starterScriptsState = .running(stage: Self.digestStageIndex(s.stage))
            if s.status == "ready" {
                UserDefaults.standard.removeObject(forKey: Self.digestJobKey)
                if let scan = s.scan { applyVoiceScan(scan) }
                if pillars.isEmpty { derivePillars() }
                if scripts.isEmpty { scripts = s.scripts }
                guard !scripts.isEmpty else { return false }
                starterScriptsState = .ready
                save()
                notifyScriptsReady()
                return true
            }
            if s.status == "failed" {
                UserDefaults.standard.removeObject(forKey: Self.digestJobKey)
                return false
            }
        }
        return true    // cancelled — don't kick off the fallback
    }

    private static func digestStageIndex(_ stage: String) -> Int {
        switch stage {
        case "scraping", "transcribing": return 1
        case "deriving": return 2
        case "writing_scripts": return 3
        default: return 0
        }
    }

    private func localStarterFallback() async {
        UserDefaults.standard.removeObject(forKey: Self.digestJobKey)
        starterScriptsState = .running(stage: 2)
        if pillars.isEmpty { derivePillars() }
        starterScriptsState = .running(stage: 3)
        await generateStarterScripts()
        guard !scripts.isEmpty else { starterScriptsState = .failed; return }
        starterScriptsState = .ready
        save()
        notifyScriptsReady()
    }

    func retryStarterScripts() {
        starterScriptsState = .idle
        beginStarterScripts()
    }

    func steer(_ script: Script, instruction: String) async {
        let updated = await llm.steer(script: script, brand: brand, instruction: instruction)
        // Upsert, not update-if-present: a feed pick opened straight from Home
        // isn't in `scripts` yet — refining it must not silently no-op.
        if let idx = scripts.firstIndex(where: { $0.id == script.id }) { scripts[idx] = updated }
        else { scripts.insert(updated, at: 0) }
        save()
    }

    func setHook(_ hook: Hook, for scriptId: UUID) {
        guard let idx = scripts.firstIndex(where: { $0.id == scriptId }) else { return }
        scripts[idx].hook = hook
        save()
    }

    func swapFormat(_ script: Script, to formatId: String) {
        guard let idx = scripts.firstIndex(where: { $0.id == script.id }) else { return }
        let f = Catalog.format(formatId)
        scripts[idx].formatId = formatId
        scripts[idx].targetSeconds = f.targetSeconds
        save()
    }

    // MARK: Footage + media corpus

    /// Persist a filmed (or imported) take into the Library "Footage" tab.
    func addFootage(path: String, scriptId: UUID? = nil, title: String = "", seconds: Int = 0) {
        footage.insert(Footage(localPath: path, scriptId: scriptId, title: title, seconds: seconds), at: 0)
        save()
    }

    func addMedia(_ assets: [MediaAsset]) {
        media.insert(contentsOf: assets, at: 0)
        save()
        // I-5: analysis is LAZY — no eager upload/analyze on import. It runs only when needed
        // (the asset is opened, the user taps Analyze, or a b-roll render is about to use the corpus).
    }

    /// I-5: analyze this asset if it hasn't been yet ("only if needed" entry point). Idempotent —
    /// skips assets already analyzing/done. Called from MediaEditSheet.onAppear, a manual button,
    /// and primeBrollCorpus() before a b-roll render.
    func ensureMediaAnalyzed(_ asset: MediaAsset) {
        guard let idx = media.firstIndex(where: { $0.id == asset.id }) else { return }
        let status = media[idx].analysisStatus
        guard status == .none || status == .failed else { return }
        analyzeMedia(media[idx])
    }

    /// I-5: when a b-roll render is about to run, warm analysis on the most recent un-analyzed
    /// corpus so /v1/broll/match has something to work with. Fire-and-forget; never blocks.
    func primeBrollCorpus(limit: Int = 12) {
        for asset in media.filter({ $0.analysisStatus == .none }).prefix(limit) {
            ensureMediaAnalyzed(asset)
        }
    }

    /// Trigger async analysis of a media asset. Fills aiDescription, aiTags, brollSuitability.
    func analyzeMedia(_ asset: MediaAsset) {
        guard !asset.contentHash.isEmpty || !asset.remoteURL.isEmpty else { return }
        let hash = asset.contentHash.isEmpty ? asset.id.uuidString : asset.contentHash
        if let idx = media.firstIndex(where: { $0.id == asset.id }) {
            media[idx].analysisStatus = .analyzing
        }
        Task {
            let result = await backend.analyzeMedia(
                contentHash: hash, filename: asset.note.isEmpty ? "asset" : asset.note,
                kind: asset.kind.rawValue, publicURL: asset.remoteURL
            )
            if let idx = media.firstIndex(where: { $0.id == asset.id }) {
                guard !result.isEmpty else { media[idx].analysisStatus = .failed; save(); return }
                media[idx].aiDescription = result["description"] as? String ?? ""
                media[idx].aiTags = result["tags"] as? [String] ?? []
                media[idx].brollSuitability = result["broll_suitability"] as? Int ?? 0
                media[idx].brollSuitabilityReason = result["broll_suitability_reason"] as? String ?? ""
                media[idx].usableAs = result["usable_as"] as? String ?? "broll"
                media[idx].hasface = result["has_face"] as? Bool ?? false
                media[idx].onScreenText = result["on_screen_text"] as? String ?? ""
                media[idx].analysisStatus = .done
                save()
            }
        }
    }

    func removeMedia(_ asset: MediaAsset) {
        media.removeAll { $0.id == asset.id }
        save()
    }

    func updateMedia(_ asset: MediaAsset) {
        if let idx = media.firstIndex(where: { $0.id == asset.id }) { media[idx] = asset; save() }
    }

    // MARK: Clips

    /// Save a mid-flow take as a draft clip (Library › Drafts, resumable from Film).
    /// Nothing is submitted for editing — no streak, no celebration, no notification.
    func saveDraft(from script: Script, footagePath: String?) {
        // Keep the footage safe: absolute paths point outside the app container (e.g. the
        // OS temp dir, which gets reaped), so copy those in. Documents-relative paths from
        // MediaStore.save already live in the container and are stored as-is — the same
        // form LocalThumbnail / MediaStore.url resolve everywhere else.
        var storedPath = footagePath
        if let p = footagePath, p.hasPrefix("/"),
           let data = try? Data(contentsOf: MediaStore.url(for: p)) {
            storedPath = MediaStore.save(data, ext: "mov")
        }
        let draft = Clip(scriptId: script.id, formatId: script.formatId,
                         formatName: Catalog.format(script.formatId).name,
                         title: script.title.isEmpty ? script.hook.text : script.title,
                         caption: script.cta, predictedScore: script.predictedScore,
                         status: .draft, seconds: script.targetSeconds,
                         localVideoPath: storedPath)
        clips.insert(draft, at: 0)
        save()
    }

    func makeClips(from script: Script, formats: [String], footagePath: String? = nil,
                   reactSourceURL: String = "", useMockEngine: Bool = false) async {
        // AF-I6: the analyze-flow fallback goes straight to the mock engine — the live
        // engine would re-compress + re-upload the whole take (tens of MB, possibly
        // cellular) just to hit the analyze-first 426 cutover and mock anyway.
        let engine: ClipEngineProtocol = useMockEngine ? MockClipEngine() : clipEngine
        let made = await engine.makeClips(from: script, formats: formats,
                                          reactSourceURL: reactSourceURL, footagePath: footagePath)
        let tagged = made.map { c -> Clip in
            var c = c
            c.title = script.title.isEmpty ? script.hook.text : script.title
            c.localVideoPath = footagePath
            return c
        }
        clips.insert(contentsOf: tagged, at: 0)
        // Submitted for editing — the script is no longer waiting in the Film queue.
        readiedScripts.removeAll { $0.script.id == script.id }
        save()
        // Poll job status until all clips are ready or failed.
        if let jobId = tagged.first?.jobId {
            Task { await pollJob(jobId: jobId, clipIds: tagged.map { $0.id }) }
        } else {
            // Mock path: always use MockClipEngine.render (returns .ready after a short sleep),
            // regardless of which clipEngine is active — LiveClipEngine.render returns .rendering.
            let mock = MockClipEngine()
            var readyCount = 0
            for c in tagged {
                let status = await mock.render(clipId: c.id)
                if let idx = clips.firstIndex(where: { $0.id == c.id }) {
                    clips[idx].status = status
                }
                if status == .ready { readyCount += 1 }
                save()
            }
            notifyClipsReady(count: readyCount)
        }
        // C-06: consecutive-DAY streak (the flame reads as a day-streak) — increments
        // only on the first completed session of a calendar day.
        bumpDailyStreak()
        save()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { self.showCelebration = true }
    }

    // MARK: Analyze-first flow (Loop H)

    /// Create the analyze job against an already-uploaded public URL (RecordView hoists
    /// mint+upload so it runs while the creator reviews the take). nil → the caller
    /// falls back to the local mock pipeline; the creator is never stranded.
    func startAnalyzeJob(script: Script?, publicURL: String?,
                         customInstructions: String = "",
                         reactSourceURL: String = "",
                         editFormat: String = "",
                         referenceReel: ReelItem? = nil) async -> AnalyzeJobResponse? {
        guard !AppConfig.backendBaseURL.isEmpty, let publicURL else { return nil }
        return await backend.createAnalyzeJob(sourceURL: publicURL, script: script,
                                              customInstructions: customInstructions,
                                              reactSourceURL: reactSourceURL,
                                              editFormat: editFormat,
                                              referenceReel: referenceReel)
    }

    /// Poll until the edit brief lands (live path analyzes async). 2s cadence, ~2min
    /// cap. Returns the response carrying the brief, a failed status, or nil on timeout.
    func pollForBrief(jobId: String) async -> AnalyzeJobResponse? {
        var attempts = 0
        while attempts < 60 && !Task.isCancelled {
            if let r = await backend.getBrief(jobId: jobId) {
                if r.editBrief != nil { return r }
                if r.status == "failed" { return r }
            }
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            attempts += 1
        }
        return nil
    }

    /// Analyze-first phase 2: confirm the reviewed brief + toggles → ONE render.
    /// Inserts the tracked clips and polls exactly like the old makeClips tail; a
    /// transport failure degrades to the local mock pipeline.
    func confirmClips(jobId: String, script: Script, toggles: EditToggles,
                      customInstructions: String, footagePath: String?) async {
        if toggles.broll { primeBrollCorpus() }   // I-5: warm corpus analysis for b-roll matching
        guard let resp = await backend.confirmClip(jobId: jobId, toggles: toggles,
                                                   customInstructions: customInstructions),
              let clipDicts = resp["clips"] as? [[String: Any]], !clipDicts.isEmpty else {
            await makeClips(from: script, formats: [script.formatId], footagePath: footagePath)
            return
        }
        let tagged = clipDicts.map { d -> Clip in
            let clipId = UUID(uuidString: d["clip_id"] as? String ?? "") ?? UUID()
            let formatId = d["format"] as? String ?? script.formatId
            let ready = (d["status"] as? String) == "ready"
            var c = Clip(id: clipId, scriptId: script.id, formatId: formatId,
                         formatName: Catalog.format(formatId).name,
                         title: script.title.isEmpty ? script.hook.text : script.title,
                         caption: script.cta,
                         predictedScore: script.predictedScore,
                         status: ready ? .ready : .rendering,
                         seconds: Catalog.format(formatId).targetSeconds,
                         jobId: jobId)
            c.localVideoPath = footagePath
            return c
        }
        clips.insert(contentsOf: tagged, at: 0)
        readiedScripts.removeAll { $0.script.id == script.id }
        save()
        if tagged.contains(where: { $0.status == .rendering }) {
            Task { await pollJob(jobId: jobId, clipIds: tagged.map { $0.id }) }
        } else {
            notifyClipsReady(count: tagged.filter { $0.status == .ready }.count)
        }
        bumpDailyStreak()
        save()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { self.showCelebration = true }
    }

    // H-07: jobIds with a re-poll loop already in flight — Library appears often;
    // never stack duplicate pollers on the same job.
    private var activeRepolls: Set<String> = []

    /// H-07: called on Library appear. Any clip stuck in .rendering (its original
    /// poll window expired — a tweak that outlived EditorView's loop, or an app
    /// relaunch mid-render) gets its job re-polled until it resolves, so nothing
    /// spins forever locally while the backend finished long ago.
    func repollRenderingClips() {
        let stuck = clips.filter { $0.status == .rendering && $0.jobId != nil }
        for (jobId, group) in Dictionary(grouping: stuck, by: { $0.jobId! })
        where !activeRepolls.contains(jobId) {
            activeRepolls.insert(jobId)
            let ids = group.map { $0.id }
            Task {
                await pollClipStatuses(jobId: jobId, clipIds: ids)
                activeRepolls.remove(jobId)
            }
        }
    }

    /// AF-I4: a permanently-gone job (404 never-existed / 410 swept) fails its clips
    /// with the expired-session error — the alternative was an infinite spinner plus
    /// a futile 5-minute poll loop on every Library visit.
    private func failClipsForDeadJob(_ clipIds: [UUID]) {
        for id in clipIds {
            guard let idx = clips.firstIndex(where: { $0.id == id }),
                  clips[idx].status == .rendering else { continue }
            clips[idx].status = .failed
            clips[idx].lastError = "job_expired"
        }
        save()
    }

    /// Per-CLIP poll loop: exits when every tracked clip left .rendering (job-level
    /// status can't be trusted here — it stays "ready" during a tweak re-render).
    func pollClipStatuses(jobId: String, clipIds: [UUID]) async {
        for _ in 0..<60 {
            if Task.isCancelled { return }
            let (maybeResult, httpStatus) = await backend.pollClipJobWithStatus(jobId: jobId)
            if httpStatus == 404 || httpStatus == 410 {
                failClipsForDeadJob(clipIds)               // AF-I4: gone for good
                return
            }
            if let result = maybeResult,
               let jobClips = result["clips"] as? [[String: Any]] {
                for jobClip in jobClips {
                    let clipStatus = jobClip["status"] as? String ?? ""
                    guard let backendId = UUID(uuidString: jobClip["clip_id"] as? String ?? ""),
                          let idx = clips.firstIndex(where: { $0.id == backendId }) else { continue }
                    clips[idx].status = clipStatus == "ready" ? .ready
                                      : clipStatus == "failed" ? .failed : .rendering
                    if let url = jobClip["render_url"] as? String { clips[idx].remoteURL = url }
                    clips[idx].lastError = clipStatus == "failed"
                        ? (jobClip["error"] as? String ?? result["error"] as? String) : nil
                    clips[idx].lastErrorDetail = clipStatus == "failed"
                        ? (jobClip["error_detail"] as? String ?? result["error_detail"] as? String) : nil
                    let warnings = jobClip["warnings"] as? [String]
                    clips[idx].warnings = (warnings?.isEmpty ?? true) ? nil : warnings
                }
                save()
                if !clips.contains(where: { clipIds.contains($0.id) && $0.status == .rendering }) {
                    return                              // every tracked clip resolved
                }
            }
            try? await Task.sleep(nanoseconds: 5_000_000_000)
        }
    }

    func pollJob(jobId: String, clipIds: [UUID]) async {
        var done = false
        var attempts = 0
        // H1: without the cancellation check, a cancelled caller Task doesn't stop
        // this loop — it busy-spins instead (Task.sleep throws immediately once
        // cancelled, and the `try?` below swallows that), hammering the backend
        // until `done` or the 60-attempt ceiling instead of actually stopping.
        while !done && attempts < 60 && !Task.isCancelled {
            try? await Task.sleep(nanoseconds: 5_000_000_000)  // 5s
            attempts += 1
            let (maybeResult, httpStatus) = await backend.pollClipJobWithStatus(jobId: jobId)
            if httpStatus == 404 || httpStatus == 410 {
                failClipsForDeadJob(clipIds)                   // AF-I4: gone for good
                return
            }
            guard let result = maybeResult,
                  let jobClips = result["clips"] as? [[String: Any]] else { continue }
            let status = result["status"] as? String ?? ""
            let jobError = result["error"] as? String
            let jobErrorDetail = result["error_detail"] as? String
            for jobClip in jobClips {
                let clipIdStr = jobClip["clip_id"] as? String ?? ""
                let clipStatus = jobClip["status"] as? String ?? ""
                let renderURL = jobClip["render_url"] as? String
                let clipError = jobClip["error"] as? String ?? jobError
                let clipErrorDetail = jobClip["error_detail"] as? String ?? jobErrorDetail
                // Compare as UUIDs, not strings: Swift's uuidString is UPPERCASE while
                // the backend's uuid4() ids are lowercase — a string compare never
                // matched, silently stranding mock-path clips in "rendering".
                if let backendId = UUID(uuidString: clipIdStr),
                   let idx = clips.firstIndex(where: { $0.id == backendId }) {
                    clips[idx].status = clipStatus == "ready" ? .ready : clipStatus == "failed" ? .failed : .rendering
                    if let url = renderURL { clips[idx].remoteURL = url }
                    clips[idx].lastError = clipStatus == "failed" ? clipError : nil
                    clips[idx].lastErrorDetail = clipStatus == "failed" ? clipErrorDetail : nil
                    // H10: non-fatal warnings apply regardless of status (a
                    // "ready" clip can still be missing b-roll it asked for).
                    let warnings = jobClip["warnings"] as? [String]
                    clips[idx].warnings = (warnings?.isEmpty ?? true) ? nil : warnings
                }
            }
            save()
            done = (status == "ready" || status == "failed" || status == "mock_ready")
            // Edited clips landed — nudge the creator. ("mock_ready" is the keyless backend's
            // ready; the .ready count guard keeps failed/empty jobs silent either way.)
            if done && status != "failed" {
                let readyCount = clips.filter { clipIds.contains($0.id) && $0.status == .ready }.count
                notifyClipsReady(count: readyCount)
            }
        }
    }

    /// Plain-English copy for a structured backend render-error code, so a failed
    /// clip tells the creator what actually happened instead of spinning forever.
    /// H5: `detail` (the backend's more specific error_detail) is optional and
    /// only surfaced for the least-specific codes (internal_error, unknown) —
    /// where it's genuinely the most useful thing to show — not for the
    /// well-understood codes above, which already have precise, actionable copy.
    func friendlyRenderError(_ code: String?, detail: String? = nil) -> String {
        switch code {
        case "source_unreachable":
            return "We couldn't reach your uploaded video. Check your connection and try again."
        case "transcribe_failed", "transcribe_timeout", "transcribe_submit_failed":
            return "We couldn't read the audio in your take. Re-record with clearer sound, or try again."
        case "render_stalled", "render_timeout":
            return "The edit took too long and timed out. Tap to try again."
        case "render_fatal", "render_no_output", "render_submit_failed", "bridge_error":
            return "Something went wrong while rendering this clip. Tap to try again."
        case "internal_error":
            if let detail, !detail.isEmpty {
                return "Something unexpected happened (\(detail)). Tap to try again."
            }
            return "Something unexpected happened while processing this clip. Tap to try again."
        case "job_expired":
            return "This edit session has expired. Re-record and try again."
        default:
            if let detail, !detail.isEmpty {
                return "This clip didn't finish (\(detail)). Tap to try again."
            }
            return "This clip didn't finish. Tap to try again."
        }
    }

    /// Re-run a failed clip's render from the backend (the job still holds the
    /// source + EDL). Optimistically flips affected clips back to .rendering and
    /// resumes polling.
    func retryClipJob(_ clip: Clip) async {
        guard let jobId = clip.jobId else { return }
        let affected = clips.filter { $0.jobId == jobId && $0.status == .failed }.map { $0.id }
        for id in affected {
            if let idx = clips.firstIndex(where: { $0.id == id }) {
                clips[idx].status = .rendering
                clips[idx].lastError = nil
                clips[idx].lastErrorDetail = nil
            }
        }
        save()
        _ = await backend.retryClipJob(jobId: jobId)
        await pollJob(jobId: jobId, clipIds: affected)
    }

    // MARK: Scheduling / publishing

    func scheduleClip(_ clip: Clip, on date: Date, platforms: [SocialPlatform],
                      autoCaptions: Bool = true, caption: String? = nil) async {
        guard canPublish else { return }
        var clip = clip
        // Burn captions if requested and not already generated.
        if autoCaptions && clip.captionLines.isEmpty,
           let script = scripts.first(where: { $0.id == clip.scriptId }) {
            clip.captionLines = await llm.captions(for: script)
            clip.captioned = true
        }
        if let idx = clips.firstIndex(where: { $0.id == clip.id }) { clips[idx] = clip }

        let post = ScheduledPost(clipId: clip.id, caption: caption ?? clip.caption,
                                 platforms: platforms, date: date, autoCaptions: autoCaptions,
                                 mediaURL: clip.remoteURL ?? clip.localVideoPath)
        // Only OAuth-linked accounts (non-empty accountId) can actually be posted to.
        var scheduled = post
        let outcome = await publisher.schedule(post, accountIds: publishAccountIds(for: platforms))
        // C-02/C-03: record the honest outcome; a scheduled post is ALWAYS saved locally,
        // but the clip only advances to .scheduled when there's a real account behind it.
        scheduled.outcome = outcome
        if outcome == .queuedTransportFailure { pendingPublishes.append(scheduled) }
        schedule.append(scheduled)
        if outcome == .posted || outcome == .savedLocalNoAccounts {
            if let idx = clips.firstIndex(where: { $0.id == clip.id }) { clips[idx].status = .scheduled }
        }
        save()
        if outcome == .posted {
            let registered = scheduled
            Task { await backend.registerPost(registered, clip: clip) }
        }
    }

    /// Edit an existing scheduled post (time / platforms / caption / captions toggle).
    func updateScheduledPost(_ post: ScheduledPost) {
        guard let idx = schedule.firstIndex(where: { $0.id == post.id }) else { return }
        schedule[idx] = post
        save()
    }

    func deleteScheduledPost(_ post: ScheduledPost) {
        schedule.removeAll { $0.id == post.id }
        if let idx = clips.firstIndex(where: { $0.id == post.clipId }), clips[idx].status == .scheduled {
            clips[idx].status = .ready
        }
        save()
    }

    /// Delete a clip and any schedule entries pointing at it.
    func deleteClip(_ clip: Clip) {
        clips.removeAll { $0.id == clip.id }
        schedule.removeAll { $0.clipId == clip.id }
        save()
    }

    /// Edit a clip's social caption in place.
    func updateClipCaption(_ clip: Clip, caption: String) {
        if let idx = clips.firstIndex(where: { $0.id == clip.id }) {
            clips[idx].caption = caption
            save()
        }
    }

    /// Conversational tweaks: reflect a tweak re-render's lifecycle on the local clip
    /// so the Library grid + detail sheet stay honest while the backend re-edits.
    func setClipRendering(_ clipId: UUID) {
        if let idx = clips.firstIndex(where: { $0.id == clipId }) {
            clips[idx].status = .rendering
            save()
        }
    }

    func applyTweakResult(_ clipId: UUID, remoteURL: String?) {
        if let idx = clips.firstIndex(where: { $0.id == clipId }) {
            clips[idx].status = .ready
            if let remoteURL, !remoteURL.isEmpty { clips[idx].remoteURL = remoteURL }
            save()
        }
    }

    /// Remove a filmed/imported take from the Footage tab.
    func deleteFootage(_ f: Footage) {
        footage.removeAll { $0.id == f.id }
        save()
    }

    // MARK: Trend → script (Coach "Write a script for this")
    func generateFromTrend(title: String, formatId: String) async {
        let style = Catalog.style(for: formatId)
        let pillar = pillars.first(where: { !$0.name.isEmpty })
            ?? Pillar(name: title, summary: title, angle: title, exampleTopics: [title],
                      weight: 0.2, colorHex: Catalog.pillarColors[0])
        isGenerating = true
        let f = Catalog.format(formatId)
        var made = await llm.generateScripts(brand: brand, pillar: pillar, count: 1, mediaContext: mediaContext, style: style, memory: memory)
        made = made.map { var s = $0; s.formatId = formatId; s.targetSeconds = f.targetSeconds; return s }
        scripts.insert(contentsOf: made, at: 0)
        isGenerating = false
        save()
    }

    // MARK: Schedule helpers
    /// Re-queue a post one day later (the "film once, post all week" reuse thesis).
    func duplicatePost(_ post: ScheduledPost) {
        var copy = post
        copy.id = UUID()
        copy.posted = false
        copy.metrics = nil
        copy.date = Calendar.current.date(byAdding: .day, value: 1, to: post.date) ?? post.date
        schedule.append(copy)
        save()
    }

    var weekPostedCount: Int { schedule.filter { $0.posted }.count }

    /// Plain-language best-time guidance (evenings skew best for most creator niches). Guidance,
    /// not a measured optimum — we never present it as a precise, data-derived number.
    var learnedBestHour: Int? = nil     // C-12: the creator's measured best hour (from settled posts)
    func bestPostTime(on day: Date) -> Date {
        // C-12: use the measured best hour when the learning loop has it; else 6 PM guidance.
        let hour = learnedBestHour ?? 18
        return Calendar.current.date(bySettingHour: hour, minute: 0, second: 0, of: day) ?? day
    }

    // MARK: Reminders (local notifications) — powers the "consistency" promise
    var remindersEnabled: Bool = UserDefaults.standard.bool(forKey: "reminders.enabled") {
        didSet {
            UserDefaults.standard.set(remindersEnabled, forKey: "reminders.enabled")
            if remindersEnabled { scheduleDailyReminder() }
            else { UNUserNotificationCenter.current().removeAllPendingNotificationRequests() }
        }
    }

    /// Request permission, then enable on grant (drives the Settings toggle).
    func requestRemindersAndEnable() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, _ in
            Task { @MainActor in self.remindersEnabled = granted }
        }
    }

    private func scheduleDailyReminder() {
        let center = UNUserNotificationCenter.current()
        center.removeAllPendingNotificationRequests()
        let content = UNMutableNotificationContent()
        content.title = "Time to film"
        content.body = "One recording today keeps your week full. Open Yunicorn."
        content.sound = .default
        var comps = DateComponents(); comps.hour = 9; comps.minute = 0
        let trigger = UNCalendarNotificationTrigger(dateMatching: comps, repeats: true)
        center.add(UNNotificationRequest(identifier: "marque.daily", content: content, trigger: trigger))
    }

    /// "Your edited clips landed" nudge — called from both completion paths (live pollJob
    /// and the mock render loop in makeClips). Fires only when ≥1 clip ended .ready; never
    /// fires for drafts. Asks for permission on first use, mirroring the reminders pattern.
    private func notifyClipsReady(count: Int) {
        guard count > 0 else { return }
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            switch settings.authorizationStatus {
            case .notDetermined:
                center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
                    if granted { AppStore.postClipsReadyNotification() }
                }
            case .denied:
                break
            default:
                AppStore.postClipsReadyNotification()
            }
        }
    }

    /// Immediate local notification (nil trigger) — nonisolated so the notification-center
    /// completion handlers above can call it straight from their background queue.
    private nonisolated static func postClipsReadyNotification() {
        let content = UNMutableNotificationContent()
        content.title = "Your clip is ready 🎬"
        content.body = "The AI finished editing — review it in your Library and schedule it."
        content.sound = .default
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: "marque.clipsReady.\(UUID().uuidString)",
                                  content: content, trigger: nil))
    }

    /// C-03/C-08: "your post is live" — fired when a queued/scheduled post actually
    /// publishes upstream. Gated on the Settings "Post published" toggle (default on)
    /// so the toggle backs a real notification instead of writing a dead UserDefaults key.
    private func notifyPostPublished(_ post: ScheduledPost) {
        guard UserDefaults.standard.object(forKey: "notif.published") as? Bool ?? true else { return }
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized
                    || settings.authorizationStatus == .provisional else { return }
            let content = UNMutableNotificationContent()
            content.title = "Your post is live 🚀"
            content.body = "It just went out to your connected account."
            content.sound = .default
            center.add(UNNotificationRequest(identifier: "marque.published.\(UUID().uuidString)",
                                             content: content, trigger: nil))
        }
    }

    /// Onboarding digest completion — fires so users who backgrounded the app during
    /// plan-building ("feel free to close the app") come back at the right moment.
    private func notifyScriptsReady() {
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            switch settings.authorizationStatus {
            case .notDetermined:
                center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
                    if granted { AppStore.postScriptsReadyNotification() }
                }
            case .denied:
                break
            default:
                AppStore.postScriptsReadyNotification()
            }
        }
    }

    private nonisolated static func postScriptsReadyNotification() {
        let content = UNMutableNotificationContent()
        content.title = "Your first scripts are ready ✍️"
        content.body = "Your content plan is built — come see what Yunicorn wrote for you."
        content.sound = .default
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: "marque.scriptsReady.\(UUID().uuidString)",
                                  content: content, trigger: nil))
    }

    /// Publish immediately (live via Post for Me when linked accounts exist; mock otherwise).
    func postNow(_ post: ScheduledPost) async {
        guard canPublish else { return }
        var p = post
        p.date = Date()
        let outcome = await publisher.schedule(p, accountIds: publishAccountIds(for: p.platforms))
        p.posted = (outcome == .posted)                 // ONLY a real post counts as posted
        p.outcome = outcome
        if outcome == .queuedTransportFailure { pendingPublishes.append(p) }
        if let idx = schedule.firstIndex(where: { $0.id == post.id }) { schedule[idx] = p }
        else { schedule.append(p) }
        if outcome == .posted, let ci = clips.firstIndex(where: { $0.id == post.clipId }) {
            clips[ci].status = .posted
        }
        save()
    }

    /// C-03: retry posts that failed to reach the backend (called on app foreground and on
    /// NetworkMonitor reconnect). Each success promotes the post to truly posted and fires
    /// the "your post is live" notification; anything still unreachable stays queued.
    func retryPendingPublishes() async {
        guard !pendingPublishes.isEmpty else { return }
        let queue = pendingPublishes
        for var p in queue {
            let outcome = await publisher.schedule(p, accountIds: publishAccountIds(for: p.platforms))
            guard outcome != .queuedTransportFailure else { continue }   // still offline — keep it
            pendingPublishes.removeAll { $0.id == p.id }
            p.outcome = outcome
            p.posted = (outcome == .posted)
            if let idx = schedule.firstIndex(where: { $0.id == p.id }) { schedule[idx] = p }
            if outcome == .posted {
                notifyPostPublished(p)
                if let ci = clips.firstIndex(where: { $0.id == p.clipId }) {
                    clips[ci].status = .posted
                    let registered = p, clip = clips[ci]
                    Task { await backend.registerPost(registered, clip: clip) }
                }
            }
        }
        save()
    }

    // MARK: Metrics logging

    func logMetrics(_ metrics: PostMetrics, for post: ScheduledPost) {
        if let idx = schedule.firstIndex(where: { $0.id == post.id }) {
            schedule[idx].metrics = metrics
            save()
        }
        // Register with backend learning loop
        Task { await backend.registerPostMetrics(postId: post.id.uuidString, metrics: metrics) }
    }

    func loadRecommendations() async {
        let arms = await backend.fetchRecommendations(niche: brand.niche)
        recommendedArms = arms
        let insights = await backend.fetchLearnedInsights()
        learnedInsights = insights
        learningProgress = insights["learning_progress"] as? Double ?? 0
        postsLearned = insights["posts_learned"] as? Int ?? 0
    }

    // MARK: Coach / trends / insights

    func loadTrends() async {
        guard trends.isEmpty else { return }
        trends = await insights.trends(niche: brand.niche)
    }

    func makeTeardown(for clip: Clip) async {
        let card = await llm.teardown(for: clip)
        teardowns.insert(card, at: 0)
        save()
    }

    func loadInsights() async {
        guard !clips.isEmpty else { return }
        // Describe REAL activity + measured metrics only — no fabricated "predicted
        // score". When no post has real metrics yet, say so plainly so the coach
        // gives process advice instead of reacting to made-up numbers.
        let summary: String
        if hasRealMetrics {
            summary = "\(activeClipCount) clips out this week, about \(weekViews) measured views and +\(weekFollows) follows so far."
        } else {
            summary = "\(activeClipCount) clips scheduled or posted this week; no performance data has come back yet."
        }
        coaching = await llm.interpretInsights(brand: brand, summary: summary,
                                               persona: (coachPersona ?? .closer).rawValue)   // C-09
    }

    // MARK: Today directive + weekly metrics

    var weekGoal: Int { brand.weeklyTarget ?? 5 }
    var weekDone: Int { schedule.count }
    var weekProgress: Double { min(1, Double(weekDone) / Double(weekGoal)) }

    /// Clips that are scheduled or posted (the ones contributing reach this week).
    var activeClipCount: Int { clips.filter { $0.status == .scheduled || $0.status == .posted }.count }
    var bestClip: Clip? { clips.max { $0.predictedScore < $1.predictedScore } }

    /// True only once a post has real, logged metrics. The momentum hero stays in its honest
    /// empty/teaching state until then — we never present projected numbers as measured reach.
    var hasRealMetrics: Bool { schedule.contains { ($0.metrics?.views ?? 0) > 0 } }

    // Weekly performance. Uses real ScheduledPost.metrics when an Insights provider has
    // populated them; otherwise projects from predicted scores so the card is never empty.
    var weekViews: Int {
        let real = schedule.compactMap { $0.metrics?.views }.reduce(0, +)
        if real > 0 { return real }
        return clips.filter { $0.status == .scheduled || $0.status == .posted }
            .reduce(0) { $0 + $1.predictedScore * 120 }
    }
    var weekFollows: Int {
        let real = schedule.compactMap { $0.metrics?.followsGained }.reduce(0, +)
        if real > 0 { return real }
        return clips.filter { $0.status == .scheduled || $0.status == .posted }
            .reduce(0) { $0 + max(0, $1.predictedScore - 60) }
    }
    /// 7-point sparkline (normalized 0…1) seeded by recent clip scores — stable, not random.
    var weekTrend: [Double] {
        let scores = clips.prefix(7).map { Double($0.predictedScore) }.reversed()
        guard !scores.isEmpty else { return [0.30, 0.42, 0.36, 0.52, 0.6, 0.58, 0.74] }
        let maxV = max(1, scores.max() ?? 1)
        return scores.map { $0 / maxV }
    }

    var todayDirective: (title: String, subtitle: String) {
        if !hasOnboarded { return ("Let's set up your brand", "A couple of questions to learn your voice.") }
        let ready = scripts.filter { !$0.approved }.count
        let rendering = clips.contains { $0.status == .rendering }
        if rendering { return ("Your clips are cooking", "We'll nudge you the moment they're ready.") }
        if clips.contains(where: { $0.status == .ready }) { return ("Clips ready to schedule", "Drop them onto this week.") }
        if ready > 0 { return ("You've got \(ready) scripts ready", "Record when you've got a few minutes.") }
        return ("Film once. Post all week.", "Generate this week's scripts in Studio.")
    }

    // MARK: Persistence (lightweight)

    private struct Snapshot: Codable {
        var brand: BrandGraph; var pillars: [Pillar]; var scripts: [Script]
        var clips: [Clip]; var footage: [Footage]; var media: [MediaAsset]
        var schedule: [ScheduledPost]; var teardowns: [TeardownCard]
        var hasOnboarded: Bool; var streak: Int
        // V3 additions — Optional so pre-V3 blobs still decode (synthesized decodeIfPresent)
        var memory: CreatorMemory? = nil
        var readiedScripts: [SavedScript]? = nil
        var conversations: [Conversation]? = nil
        var editPrefs: EditPrefs? = nil
        var brandSummary: BrandSummaryCard? = nil
        var chatPersona: ChatPersona? = nil
        var chatResponseLength: ChatResponseLength? = nil
        var pendingPublishes: [ScheduledPost]? = nil   // C-03: transport-failure retry queue
        var lastStreakDate: Date? = nil                // C-06
        var likedPicks: [UUID]? = nil                  // I-2: Today's-picks feedback
        var dismissedPicks: [UUID]? = nil
    }

    func save() {
        let snap = Snapshot(brand: brand, pillars: pillars, scripts: scripts, clips: clips,
                            footage: footage, media: media, schedule: schedule, teardowns: teardowns,
                            hasOnboarded: hasOnboarded, streak: streak,
                            memory: memory, readiedScripts: readiedScripts,
                            conversations: conversations, editPrefs: editPrefs,
                            brandSummary: brandSummary, chatPersona: chatPersona,
                            chatResponseLength: chatResponseLength, pendingPublishes: pendingPublishes,
                            lastStreakDate: lastStreakDate,
                            likedPicks: likedPicks, dismissedPicks: dismissedPicks)
        if let data = try? JSONEncoder().encode(snap) {
            UserDefaults.standard.set(data, forKey: saveKey)
            // Best-effort mirror to Supabase when configured (no-op otherwise).
            if !AppConfig.supabaseAnonKey.isEmpty { Task { await remote.push(data) } }
        }
    }

    /// C-13: pull-on-sign-in restore. Keyed by the auth userId (see SupabaseStore.rowKey),
    /// so a reinstall + sign-in brings the creator's state back. Conservative merge:
    /// adopt the remote snapshot ONLY when the local store is effectively empty
    /// (fresh install / nothing created) — we never overwrite non-empty local work.
    /// No-op when Supabase isn't configured or nothing is stored remotely.
    func restoreFromCloud() async {
        guard !AppConfig.supabaseAnonKey.isEmpty else { return }
        // "Effectively empty" = the user hasn't built anything worth protecting here.
        let localHasContent = hasOnboarded || !scripts.isEmpty || !clips.isEmpty
            || !readiedScripts.isEmpty || !media.isEmpty
        guard !localHasContent else { return }
        guard let data = await remote.pull(),
              let snap = try? JSONDecoder().decode(Snapshot.self, from: data) else { return }
        applySnapshot(snap)
        save()   // write the restored state into local UserDefaults as the new baseline
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: saveKey),
              let snap = try? JSONDecoder().decode(Snapshot.self, from: data) else { return }
        applySnapshot(snap)
    }

    /// Shared snapshot→state application, used by both local load() and cloud restore.
    private func applySnapshot(_ snap: Snapshot) {
        brand = snap.brand; pillars = snap.pillars; scripts = snap.scripts
        clips = snap.clips; footage = snap.footage; media = snap.media
        schedule = snap.schedule; teardowns = snap.teardowns
        hasOnboarded = snap.hasOnboarded; streak = snap.streak
        memory = snap.memory ?? CreatorMemory()
        readiedScripts = snap.readiedScripts ?? []
        conversations = snap.conversations ?? []
        editPrefs = snap.editPrefs ?? EditPrefs()
        brandSummary = snap.brandSummary
        chatPersona = snap.chatPersona
        chatResponseLength = snap.chatResponseLength
        pendingPublishes = snap.pendingPublishes ?? []
        lastStreakDate = snap.lastStreakDate
        likedPicks = snap.likedPicks ?? []
        dismissedPicks = snap.dismissedPicks ?? []
        migrateFootageIntoMedia()
    }

    /// V3: the Library's Footage tab folded into Media — one-time migration of old takes.
    private func migrateFootageIntoMedia() {
        guard !footage.isEmpty else { return }
        let migrated = footage.map { f in
            MediaAsset(localPath: f.localPath, kind: .clip,
                       note: f.title.isEmpty ? "Imported take" : f.title,
                       isVideo: true, thumbnailPath: f.thumbnailPath, addedAt: f.addedAt)
        }
        media.insert(contentsOf: migrated, at: 0)
        footage = []
        save()
    }

    /// For Maestro/dev: wipe everything back to first-run.
    func resetAll() {
        UserDefaults.standard.removeObject(forKey: saveKey)
        brand = BrandGraph(); pillars = []; scripts = []; clips = []; footage = []; media = []
        schedule = []; trends = []; teardowns = []; hasOnboarded = false; streak = 0
        memory = CreatorMemory(); readiedScripts = []; conversations = []
        editPrefs = EditPrefs(); brandSummary = nil
        auth.signOut()
    }

    // MARK: - V3: Memory + readied scripts

    func applyMemoryUpdates(_ updates: [MemoryUpdate]) {
        guard !updates.isEmpty else { return }
        memory.apply(updates)
        save()
    }

    /// "Save for later" from the feed / chat / mimic → lands in the Film queue.
    @discardableResult
    func readyScript(_ script: Script, source: SavedScriptSource, mimickedFrom: String = "") -> SavedScript {
        if let existing = readiedScripts.first(where: { $0.script.id == script.id }) { return existing }
        let saved = SavedScript(script: script, source: source, mimickedFrom: mimickedFrom)
        readiedScripts.insert(saved, at: 0)
        save()
        return saved
    }

    func removeReadiedScript(_ saved: SavedScript) {
        readiedScripts.removeAll { $0.id == saved.id }
        save()
    }

    // MARK: Import an external clip (I-6) — schedule a video you didn't film on Yunicorn.

    @discardableResult
    func importExternalClip(data: Data, title: String) async -> Clip {
        let path = MediaStore.save(data, ext: "mov")
        let url = MediaStore.url(for: path)
        let poster = MediaStore.poster(for: url)
        let thumbPath = poster.flatMap { $0.jpegData(compressionQuality: 0.7) }.map { MediaStore.save($0, ext: "jpg") }
        let seconds = Int(CMTimeGetSeconds(AVURLAsset(url: url).duration).rounded())
        let style = brand.preferredStyles.first ?? .talkingHead
        var clip = Clip(scriptId: UUID(), formatId: style.formats.first ?? "myth-buster",
                        formatName: "Imported", caption: "",
                        predictedScore: 0, status: .ready, seconds: max(1, seconds))
        clip.title = title
        clip.localVideoPath = path
        clip.thumbnailPath = thumbPath
        clip.source = "imported"
        clips.insert(clip, at: 0)
        save()
        // Upload in the background so it's postable (real publishing needs a remote URL).
        let cid = clip.id
        Task {
            if let remote = await LiveClipEngine.mintAndUpload(footagePath: path), !remote.isEmpty {
                if let idx = clips.firstIndex(where: { $0.id == cid }) { clips[idx].remoteURL = remote; save() }
            }
        }
        return clip
    }

    // MARK: Today's-picks feedback (I-2)

    var likedPicks: [UUID] = []
    var dismissedPicks: [UUID] = []

    /// ✓ on a pick — a positive learning signal (the backend folds it into the bandit).
    func likePick(_ script: Script) {
        if !likedPicks.contains(script.id) { likedPicks.append(script.id) }
        if likedPicks.count > 200 { likedPicks.removeFirst(likedPicks.count - 200) }
        save()
        Task { await backend.sendFeedFeedback(script: script, niche: brand.niche, verdict: "like") }
    }

    /// ✗ on a pick — dismiss it (persisted so it stays gone) + a negative learning signal.
    func dismissPick(_ script: Script) {
        if !dismissedPicks.contains(script.id) { dismissedPicks.append(script.id) }
        if dismissedPicks.count > 200 { dismissedPicks.removeFirst(dismissedPicks.count - 200) }
        save()
        Task { await backend.sendFeedFeedback(script: script, niche: brand.niche, verdict: "dislike") }
    }

    /// C-06: increment the streak at most once per calendar day; a gap of >1 day resets it.
    /// The flame glyph reads as a day-streak, so a raw session counter was quietly dishonest.
    private func bumpDailyStreak() {
        let cal = Calendar.current
        let today = cal.startOfDay(for: Date())
        if let last = lastStreakDate {
            let lastDay = cal.startOfDay(for: last)
            if lastDay == today { return }
            let gap = cal.dateComponents([.day], from: lastDay, to: today).day ?? 2
            streak = gap == 1 ? streak + 1 : 1
        } else {
            streak = max(streak, 1)
        }
        lastStreakDate = today
    }

    // W4: film-queue management — fixed Queue / Archived sections; order = array order.
    var queuedScripts: [SavedScript] { readiedScripts.filter { $0.archivedAt == nil } }
    var archivedReadied: [SavedScript] { readiedScripts.filter { $0.archivedAt != nil } }

    /// Reorder within the queued subset (maps queued indices back to the master array).
    func moveReadied(fromOffsets source: IndexSet, toOffset destination: Int) {
        var queued = queuedScripts
        queued.move(fromOffsets: source, toOffset: destination)
        let archived = archivedReadied
        readiedScripts = queued + archived     // queued first (display order), archived after
        save()
    }

    func archiveReadied(_ saved: SavedScript) {
        if let i = readiedScripts.firstIndex(where: { $0.id == saved.id }) {
            readiedScripts[i].archivedAt = Date(); save()
        }
    }
    func unarchiveReadied(_ saved: SavedScript) {
        if let i = readiedScripts.firstIndex(where: { $0.id == saved.id }) {
            readiedScripts[i].archivedAt = nil; save()
        }
    }
}
