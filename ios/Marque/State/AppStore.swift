import Foundation
import Observation
import UserNotifications

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
    var trends: [TrendItem] = []
    var teardowns: [TeardownCard] = []
    var hasOnboarded = false
    var streak = 0

    // V3: conversation memory + readied scripts + chat + edit prefs
    var memory = CreatorMemory()
    var readiedScripts: [SavedScript] = []       // the Film-flow queue ("save for later")
    var conversations: [Conversation] = []       // chat threads (incl. the pinned Voice notes)
    var editPrefs = EditPrefs() { didSet { backend.editPrefs = editPrefs.asDictionary } }
    var brandSummary: BrandSummaryCard? = nil    // cached Profile hero card
    var chatPersona: ChatPersona? = nil           // nil → .closer default (drawer picker)
    var chatResponseLength: ChatResponseLength? = nil   // nil → .medium default

    // V3: account + subscription gates (onboarding → auth wall → paywall → app)
    let auth = AuthManager()
    let subscription = SubscriptionManager()

    // Transient
    var isGenerating = false
    var showCelebration = false
    var showVoiceOnboarding = false
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
        save()
    }
    func removeConnectedAccount(_ a: ConnectedAccount) {
        brand.connectedAccounts.removeAll { $0.id == a.id }
        save()
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

    /// Apply a voice-onboarding finalize result (called after the conversational session).
    func applyVoiceScan(_ result: BackendClient.BrandScanResult) {
        if !result.pillars.isEmpty {
            pillars = result.pillars
            brand.topThemes = result.topThemes
        }
        if let v = result.voiceUpdate { brand.voice = v }
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
        let new = await llm.generateScripts(brand: brand, pillar: pillar, count: count, mediaContext: mediaContext, style: style)
        scripts.insert(contentsOf: new, at: 0)
        isGenerating = false
        save()
    }

    func generateStarterScripts() async {
        guard scripts.isEmpty, let p = pillars.first else { return }
        await generateScripts(for: p, style: brand.preferredStyles.first ?? .talkingHead, count: 3)
    }

    func steer(_ script: Script, instruction: String) async {
        let updated = await llm.steer(script: script, brand: brand, instruction: instruction)
        if let idx = scripts.firstIndex(where: { $0.id == script.id }) { scripts[idx] = updated }
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
        for asset in assets { analyzeMedia(asset) }
    }

    /// Trigger async analysis of a media asset after upload. Fills aiDescription, aiTags, brollSuitability.
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

    func makeClips(from script: Script, formats: [String], footagePath: String? = nil) async {
        let made = await clipEngine.makeClips(from: script, formats: formats)
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
        // Consistency measures showing up: one per completed recording session.
        streak += 1
        save()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { self.showCelebration = true }
    }

    func pollJob(jobId: String, clipIds: [UUID]) async {
        var done = false
        var attempts = 0
        while !done && attempts < 60 {
            try? await Task.sleep(nanoseconds: 5_000_000_000)  // 5s
            attempts += 1
            guard let result = await backend.pollClipJob(jobId: jobId),
                  let jobClips = result["clips"] as? [[String: Any]] else { continue }
            let status = result["status"] as? String ?? ""
            for jobClip in jobClips {
                let clipIdStr = jobClip["clip_id"] as? String ?? ""
                let clipStatus = jobClip["status"] as? String ?? ""
                let renderURL = jobClip["render_url"] as? String
                if let idx = clips.firstIndex(where: { $0.id.uuidString == clipIdStr }) {
                    clips[idx].status = clipStatus == "ready" ? .ready : clipStatus == "failed" ? .failed : .rendering
                    if let url = renderURL { clips[idx].remoteURL = url }
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
        let ok = await publisher.schedule(post)
        if ok {
            schedule.append(post)
            if let idx = clips.firstIndex(where: { $0.id == clip.id }) { clips[idx].status = .scheduled }
            save()
            // Register with learning loop so it tracks this arm.
            let registered = post
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
        var made = await llm.generateScripts(brand: brand, pillar: pillar, count: 1, mediaContext: mediaContext, style: style)
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
    func bestPostTime(on day: Date) -> Date {
        Calendar.current.date(bySettingHour: 18, minute: 0, second: 0, of: day) ?? day
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
        content.body = "One recording today keeps your week full. Open Marque."
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

    /// Publish immediately (live via Ayrshare when keyed; mock otherwise) and mark posted.
    func postNow(_ post: ScheduledPost) async {
        guard canPublish else { return }
        var p = post
        p.date = Date()
        let ok = await publisher.schedule(p)
        p.posted = ok
        if let idx = schedule.firstIndex(where: { $0.id == post.id }) { schedule[idx] = p }
        else { schedule.append(p) }
        if ok, let ci = clips.firstIndex(where: { $0.id == post.clipId }) { clips[ci].status = .posted }
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
        let summary = "\(activeClipCount) clips out this week, top predicted score \(bestClip?.predictedScore ?? 0), about \(weekViews) projected views and +\(weekFollows) follows."
        coaching = await llm.interpretInsights(brand: brand, summary: summary)
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
    }

    func save() {
        let snap = Snapshot(brand: brand, pillars: pillars, scripts: scripts, clips: clips,
                            footage: footage, media: media, schedule: schedule, teardowns: teardowns,
                            hasOnboarded: hasOnboarded, streak: streak,
                            memory: memory, readiedScripts: readiedScripts,
                            conversations: conversations, editPrefs: editPrefs,
                            brandSummary: brandSummary, chatPersona: chatPersona,
                            chatResponseLength: chatResponseLength)
        if let data = try? JSONEncoder().encode(snap) {
            UserDefaults.standard.set(data, forKey: saveKey)
            // Best-effort mirror to Supabase when configured (no-op otherwise).
            if !AppConfig.supabaseAnonKey.isEmpty { Task { await remote.push(data) } }
        }
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: saveKey),
              let snap = try? JSONDecoder().decode(Snapshot.self, from: data) else { return }
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
}
