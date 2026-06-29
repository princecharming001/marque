import Foundation
import Observation

@MainActor
@Observable
final class AppStore {
    // Persisted-ish app state
    var brand = BrandGraph()
    var pillars: [Pillar] = []
    var scripts: [Script] = []
    var clips: [Clip] = []
    var schedule: [ScheduledPost] = []
    var trends: [TrendItem] = []
    var teardowns: [TeardownCard] = []
    var hasOnboarded = false
    var streak = 0

    // Transient
    var isGenerating = false
    var showCelebration = false

    // Adapters — live Claude when an Anthropic key is present, deterministic mock otherwise.
    // Computed so pasting a key in Settings takes effect without relaunch.
    var llm: LLMRouting { AppConfig.useLiveAI ? AnthropicLLMRouter() : MockLLMRouter() }
    var aiMode: String { AppConfig.useLiveAI ? "Claude" : "Mock" }
    let clipEngine: ClipEngineProtocol = MockClipEngine()
    // Live Ayrshare publishing when a key is present, mock otherwise.
    var publisher: Publishing { AppConfig.ayrshareKey.isEmpty ? MockPublisher() : AyrsharePublisher() }
    let insights: InsightsProviding = MockInsights()
    let remote: RemotePersistence = SupabaseStore()
    let billing: Billing = MockBilling()
    var canPublish: Bool { billing.isPro }   // hard wall at publishing (11-monetization.md)

    private let saveKey = "marque.state.v1"

    init() {
        if CommandLine.arguments.contains("-reset") {
            UserDefaults.standard.removeObject(forKey: saveKey)
        }
        load()
    }

    // MARK: Onboarding

    /// Derive starter pillars from the brand once analyzed.
    func derivePillars() {
        let names = brand.topThemes.isEmpty
            ? ["Lessons", "Behind the scenes", "Hot takes", "How-to"]
            : brand.topThemes
        pillars = names.prefix(5).enumerated().map { i, n in
            Pillar(name: n, weight: 1.0 / Double(min(5, names.count)),
                   colorHex: Catalog.pillarColors[i % Catalog.pillarColors.count])
        }
    }

    func analyzePage() async {
        // Mock Brand Audit from a connected page.
        try? await Task.sleep(nanoseconds: 1_100_000_000)
        if brand.topThemes.isEmpty {
            brand.topThemes = ["Lessons learned", "Behind the scenes", "Hot takes", "Quick how-to"]
        }
        brand.analyzed = true
        derivePillars()
        save()
    }

    func completeOnboarding() {
        if pillars.isEmpty { derivePillars() }
        hasOnboarded = true
        save()
    }

    // MARK: Scripts

    func generateScripts(for pillar: Pillar, count: Int = 3) async {
        isGenerating = true
        let new = await llm.generateScripts(brand: brand, pillar: pillar, count: count)
        scripts.insert(contentsOf: new, at: 0)
        isGenerating = false
        save()
    }

    func generateStarterScripts() async {
        guard scripts.isEmpty, let p = pillars.first else { return }
        await generateScripts(for: p, count: 3)
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

    // MARK: Clips

    func makeClips(from script: Script, formats: [String]) async {
        let made = await clipEngine.makeClips(from: script, formats: formats)
        clips.insert(contentsOf: made, at: 0)
        save()
        // render each
        for c in made {
            let status = await clipEngine.render(clipId: c.id)
            if let idx = clips.firstIndex(where: { $0.id == c.id }) {
                clips[idx].status = status
            }
            save()
        }
        // Consistency measures showing up: one per completed recording session.
        streak += 1
        save()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { self.showCelebration = true }
    }

    // MARK: Scheduling

    func scheduleClip(_ clip: Clip, on date: Date, platforms: [SocialPlatform]) async {
        let post = ScheduledPost(clipId: clip.id, caption: clip.caption, platforms: platforms, date: date)
        let ok = await publisher.schedule(post)
        if ok {
            schedule.append(post)
            if let idx = clips.firstIndex(where: { $0.id == clip.id }) { clips[idx].status = .scheduled }
            save()
        }
    }

    // MARK: Coach / trends

    func loadTrends() async {
        guard trends.isEmpty else { return }
        trends = await insights.trends(niche: brand.niche)
    }

    func makeTeardown(for clip: Clip) async {
        let card = await llm.teardown(for: clip)
        teardowns.insert(card, at: 0)
        save()
    }

    // MARK: Today directive

    // Weekly command-center metric (queued toward a weekly goal).
    var weekGoal: Int { 5 }
    var weekDone: Int { schedule.count }
    var weekProgress: Double { min(1, Double(weekDone) / Double(weekGoal)) }

    var todayDirective: (title: String, subtitle: String) {
        if !hasOnboarded { return ("Let's set up your brand", "A couple of questions to learn your voice.") }
        let ready = scripts.filter { !$0.approved }.count
        let rendering = clips.contains { $0.status == .rendering }
        if rendering { return ("Your clips are cooking", "We'll nudge you the moment they're ready.") }
        if ready > 0 { return ("You've got \(ready) scripts ready", "Record when you've got a few minutes.") }
        if clips.contains(where: { $0.status == .ready }) { return ("Clips ready to schedule", "Drop them onto this week.") }
        return ("Film once. Post all week.", "Generate this week's scripts in Studio.")
    }

    // MARK: Persistence (lightweight)

    private struct Snapshot: Codable {
        var brand: BrandGraph; var pillars: [Pillar]; var scripts: [Script]
        var clips: [Clip]; var schedule: [ScheduledPost]; var teardowns: [TeardownCard]
        var hasOnboarded: Bool; var streak: Int
    }

    func save() {
        let snap = Snapshot(brand: brand, pillars: pillars, scripts: scripts, clips: clips,
                            schedule: schedule, teardowns: teardowns, hasOnboarded: hasOnboarded, streak: streak)
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
        clips = snap.clips; schedule = snap.schedule; teardowns = snap.teardowns
        hasOnboarded = snap.hasOnboarded; streak = snap.streak
    }

    /// For Maestro/dev: wipe everything back to first-run.
    func resetAll() {
        UserDefaults.standard.removeObject(forKey: saveKey)
        brand = BrandGraph(); pillars = []; scripts = []; clips = []
        schedule = []; trends = []; teardowns = []; hasOnboarded = false; streak = 0
    }
}
