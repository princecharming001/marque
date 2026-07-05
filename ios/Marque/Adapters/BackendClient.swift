import Foundation

// The app's single AI client. Talks ONLY to our FastAPI backend (which holds every vendor key
// and decides live-vs-mock internally) — the iOS app ships no Anthropic key. Conforms to the
// existing LLMRouting protocol so all call sites are unchanged. Degrades to the local
// MockLLMRouter on any network/decoding failure so the app never hard-stalls offline.

final class BackendClient: LLMRouting, @unchecked Sendable {
    private let fallback = MockLLMRouter()
    var token: String?               // Supabase JWT, attached once auth lands
    var creatorId = "default"        // set by AuthManager on sign-in (scopes memory + learning)
    var editPrefs: [String: Any] = [:]   // set by AppStore; threaded into every clip job
    private(set) var lastMode = "Mock"   // "Claude" once a live response comes back

    // MARK: HTTP

    func post(_ path: String, _ body: [String: Any]) async -> Data? {
        guard let url = URL(string: AppConfig.backendBaseURL + path) else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 90
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return nil }
        return data
    }

    func get(_ path: String) async -> Data? {
        guard let url = URL(string: AppConfig.backendBaseURL + path) else { return nil }
        var req = URLRequest(url: url); req.timeoutInterval = 30
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return nil }
        return data
    }

    /// Like post(), but surfaces the HTTP status code so callers can distinguish
    /// meaningful non-200s (the tweak endpoint uses 404/409 as part of its contract).
    /// Returns (nil, 0) on transport failure.
    func postWithStatus(_ path: String, _ body: [String: Any]) async -> (Data?, Int) {
        guard let url = URL(string: AppConfig.backendBaseURL + path) else { return (nil, 0) }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 90
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse else { return (nil, 0) }
        return (data, http.statusCode)
    }

    private func note(_ mode: String?) { if let mode { lastMode = mode == "live" ? "Claude" : "Mock" } }

    // MARK: Serialization

    private func brandBody(_ b: BrandGraph) -> [String: Any] {
        var body: [String: Any] = [
            "niche": b.niche, "audience": b.audience, "known_for": b.knownFor,
            "what_you_do": b.whatYouDo, "goal": b.goal.rawValue,
            "voice": ["funnyToSerious": b.voice.funnyToSerious,
                      "polishedToRaw": b.voice.polishedToRaw,
                      "teacherToPeer": b.voice.teacherToPeer],
            "non_negotiables": b.nonNegotiables,
            "catchphrases": b.voice.catchphrases,   // verbatim signature phrases → prompt voice
        ]
        if let p = b.primaryPlatform { body["primary_platform"] = p.rawValue }
        if let s = b.stage { body["stage"] = s.rawValue }
        if let f = b.postingFrequency { body["posting_frequency"] = f.rawValue }
        if let bl = b.biggestBlocker { body["biggest_blocker"] = bl.rawValue }
        if let c = b.cameraComfort { body["camera_comfort"] = c.rawValue }
        if let wt = b.weeklyTarget { body["weekly_target"] = wt }
        if let targets = b.emulationTargets, !targets.isEmpty {
            body["emulation_targets"] = targets.map { t -> [String: Any] in
                var d: [String: Any] = ["name": t.name, "source": t.source.rawValue]
                if !t.handle.isEmpty { d["handle"] = t.handle }
                if !t.platform.isEmpty { d["platform"] = t.platform }
                return d
            }
        }
        return body
    }

    private func signal(_ raw: String?) -> HookSignal {
        guard let r = raw?.lowercased() else { return .curiosity }
        return HookSignal.allCases.first { $0.rawValue.lowercased() == r || $0.label.lowercased() == r } ?? .curiosity
    }
    private func formatId(_ raw: String?) -> String {
        guard let r = raw, Catalog.formats.contains(where: { $0.id == r }) else { return Catalog.formats[0].id }
        return r
    }
    private func hook(_ dto: HookDTO) -> Hook {
        Hook(text: dto.text, signal: signal(dto.signal), strength: min(100, max(0, dto.strength ?? 75)))
    }
    private func script(_ dto: ScriptDTO, pillar: String, style: VideoStyle) -> Script {
        let fid = formatId(dto.formatId)
        let primary = Hook(text: dto.hook, signal: signal(dto.hookSignal),
                           strength: min(100, max(0, dto.predictedScore ?? 80)))
        return Script(pillarName: pillar, title: dto.title ?? "", summary: dto.summary ?? "",
                      style: dto.style ?? style.rawValue, formatId: fid,
                      hook: primary, altHooks: (dto.altHooks ?? []).map(hook),
                      body: dto.body, cta: dto.cta, shotPlan: dto.shotPlan ?? [],
                      targetSeconds: dto.targetSeconds ?? Catalog.format(fid).targetSeconds,
                      predictedScore: min(100, max(0, dto.predictedScore ?? 80)))
    }

    // MARK: DTOs

    struct HookDTO: Decodable { let text: String; let signal: String?; let strength: Int? }
    struct ScriptDTO: Decodable {
        let title: String?; let summary: String?
        let hook: String; let hookSignal: String?; let formatId: String?
        let body: String; let cta: String; let shotPlan: [String]?
        let targetSeconds: Int?; let predictedScore: Int?; let altHooks: [HookDTO]?; let style: String?
    }
    struct PillarDTO: Decodable {
        let name: String; let summary: String?; let angle: String?
        let exampleTopics: [String]?; let weight: Double?; let colorHex: UInt?
    }
    private struct PillarsResp: Decodable { let mode: String?; let pillars: [PillarDTO] }
    private struct ScriptsResp: Decodable { let mode: String?; let scripts: [ScriptDTO] }
    private struct HooksResp: Decodable { let mode: String?; let hooks: [HookDTO] }
    private struct SteerResp: Decodable { let mode: String?; let script: ScriptDTO }
    private struct CaptionsResp: Decodable { let mode: String?; let lines: [String] }
    private struct TeardownResp: Decodable { let mode: String?; let headline: String; let detail: String; let liftPercent: Int? }
    private struct InsightsResp: Decodable { let mode: String?; let coaching: String }

    // MARK: LLMRouting

    func generatePillars(brand: BrandGraph) async -> [Pillar] {
        guard let data = await post("/v1/pillars", brandBody(brand)),
              let r = try? JSONDecoder().decode(PillarsResp.self, from: data), !r.pillars.isEmpty else {
            return await fallback.generatePillars(brand: brand)
        }
        note(r.mode)
        let colors = Catalog.pillarColors
        return r.pillars.enumerated().map { i, d in
            Pillar(name: d.name, summary: d.summary ?? "", angle: d.angle ?? "",
                   exampleTopics: d.exampleTopics ?? [], weight: d.weight ?? 0.2,
                   colorHex: d.colorHex ?? colors[i % colors.count])
        }
    }

    func generateScripts(brand: BrandGraph, pillar: Pillar, count: Int, mediaContext: String, style: VideoStyle, memory: CreatorMemory = CreatorMemory()) async -> [Script] {
        var body = brandBody(brand)
        body["pillar"] = pillar.name
        body["pillar_summary"] = pillar.summary
        body["pillar_angle"] = pillar.angle
        body["example_topics"] = pillar.exampleTopics
        body["style"] = style.rawValue
        body["count"] = count
        body["media_context"] = mediaContext
        body["creator_id"] = creatorId
        if !memory.isEmpty { body["memory"] = memoryDict(memory) }
        guard let data = await post("/v1/scripts", body),
              let r = try? JSONDecoder().decode(ScriptsResp.self, from: data), !r.scripts.isEmpty else {
            return await fallback.generateScripts(brand: brand, pillar: pillar, count: count, mediaContext: mediaContext, style: style, memory: memory)
        }
        note(r.mode)
        return r.scripts.map { script($0, pillar: pillar.name, style: style) }
    }

    func hookLab(brand: BrandGraph, topic: String, memory: CreatorMemory = CreatorMemory()) async -> [Hook] {
        var body = brandBody(brand); body["topic"] = topic
        if !memory.isEmpty { body["memory"] = memoryDict(memory) }
        guard let data = await post("/v1/hooks", body),
              let r = try? JSONDecoder().decode(HooksResp.self, from: data), !r.hooks.isEmpty else {
            return await fallback.hookLab(brand: brand, topic: topic, memory: memory)
        }
        note(r.mode)
        return r.hooks.map(hook).sorted { $0.strength > $1.strength }
    }

    /// Serialize creator memory into the wire shape the backend's memory_block expects
    /// (only non-empty fields, mirroring how /v1/converse sends it).
    private func memoryDict(_ m: CreatorMemory) -> [String: Any] {
        var dict: [String: Any] = [:]
        if !m.angle.isEmpty { dict["angle"] = m.angle }
        if !m.facts.isEmpty { dict["facts"] = m.facts }
        if !m.perspective.isEmpty { dict["perspective"] = m.perspective }
        if !m.ideas.isEmpty { dict["ideas"] = m.ideas }
        if !m.preferences.isEmpty { dict["preferences"] = m.preferences }
        return dict
    }

    func steer(script s: Script, brand: BrandGraph, instruction: String) async -> Script {
        var body = brandBody(brand)
        body["instruction"] = instruction
        body["script"] = ["hook": s.hook.text, "body": s.body, "cta": s.cta, "formatId": s.formatId]
        guard let data = await post("/v1/steer", body),
              let r = try? JSONDecoder().decode(SteerResp.self, from: data) else {
            return await fallback.steer(script: s, brand: brand, instruction: instruction)
        }
        note(r.mode)
        var out = s
        out.hook = Hook(text: r.script.hook, signal: signal(r.script.hookSignal),
                        strength: min(100, max(0, r.script.predictedScore ?? s.hook.strength)))
        out.body = r.script.body; out.cta = r.script.cta
        if let sp = r.script.shotPlan { out.shotPlan = sp }
        if let ts = r.script.targetSeconds { out.targetSeconds = ts }
        if let ps = r.script.predictedScore { out.predictedScore = min(100, max(0, ps)) }
        return out
    }

    func captions(for s: Script) async -> [String] {
        guard let data = await post("/v1/captions", ["hook": s.hook.text, "body": s.body]),
              let r = try? JSONDecoder().decode(CaptionsResp.self, from: data), !r.lines.isEmpty else {
            return await fallback.captions(for: s)
        }
        note(r.mode)
        return r.lines
    }

    func teardown(for clip: Clip) async -> TeardownCard {
        let body: [String: Any] = ["clip": ["formatName": clip.formatName, "caption": clip.caption,
                                            "predictedScore": clip.predictedScore]]
        guard let data = await post("/v1/teardown", body),
              let r = try? JSONDecoder().decode(TeardownResp.self, from: data) else {
            return await fallback.teardown(for: clip)
        }
        note(r.mode)
        return TeardownCard(clipCaption: clip.caption, headline: r.headline, detail: r.detail,
                            liftPercent: min(100, max(0, r.liftPercent ?? 30)))
    }

    func interpretInsights(brand: BrandGraph, summary: String) async -> String {
        var body = brandBody(brand); body["summary"] = summary
        guard let data = await post("/v1/insights", body),
              let r = try? JSONDecoder().decode(InsightsResp.self, from: data), !r.coaching.isEmpty else {
            return await fallback.interpretInsights(brand: brand, summary: summary)
        }
        note(r.mode)
        return r.coaching
    }

    // MARK: Connect (verify an IG/TikTok link by fetching the real public profile)

    private struct ConnectPreviewResp: Decodable {
        let found: Bool; let platform: String?; let handle: String?
        let displayName: String?; let followers: Int?; let avatarUrl: String?; let bio: String?
    }

    func connectPreview(handle: String, platform: String) async -> ConnectedAccount? {
        guard let data = await post("/v1/connect/preview", ["handle": handle, "platform": platform]),
              let r = try? JSONDecoder().decode(ConnectPreviewResp.self, from: data), r.found else { return nil }
        return ConnectedAccount(platform: r.platform ?? platform, handle: r.handle ?? handle,
                                displayName: r.displayName ?? handle, followers: r.followers ?? 0,
                                avatarUrl: r.avatarUrl ?? "", bio: r.bio ?? "")
    }

    // MARK: Emulate (analyze a linked creator's style — fire-and-forget from onboarding)

    private struct EmulateAnalyzeResp: Decodable {
        let mode: String?; let ok: Bool?
    }

    /// Kicks off style analysis of a custom emulation target. Non-blocking by
    /// design — the profile is cached server-side and resolved lazily the next
    /// time this creator generates scripts/hooks, so onboarding never waits on it.
    @discardableResult
    func emulateAnalyze(handle: String, platform: String) async -> Bool {
        guard let data = await post("/v1/emulate/analyze", ["handle": handle, "platform": platform]),
              let r = try? JSONDecoder().decode(EmulateAnalyzeResp.self, from: data) else { return false }
        return r.ok ?? true
    }

    // MARK: Brand scan (derive pillars + voice from real scraped posts)

    struct BrandScanResult {
        let pillars: [Pillar]
        let voiceUpdate: VoiceFingerprint?
        let topThemes: [String]
    }

    private struct BrandScanResp: Decodable {
        let mode: String?
        let scan: ScanBlock?
        struct ScanBlock: Decodable {
            let pillars: [PillarDTO]?
            let voice: VoiceBlock?
            let top_themes: [String]?
            struct VoiceBlock: Decodable {
                let funnyToSerious: Double?
                let polishedToRaw: Double?
                let teacherToPeer: Double?
                let catchphrases: [String]?
            }
        }
    }

    func brandScan(handle: String, platform: String, niche: String) async -> BrandScanResult? {
        let body: [String: Any] = ["handle": handle, "platform": platform, "niche": niche]
        guard let data = await post("/v1/brand-scan/handle", body),
              let r = try? JSONDecoder().decode(BrandScanResp.self, from: data),
              let scan = r.scan else { return nil }
        note(r.mode)
        return mapScan(scan)
    }

    private func mapScan(_ scan: BrandScanResp.ScanBlock) -> BrandScanResult {
        let colors = Catalog.pillarColors
        let pillars = (scan.pillars ?? []).enumerated().map { i, d in
            Pillar(name: d.name, summary: d.summary ?? "", angle: d.angle ?? "",
                   exampleTopics: d.exampleTopics ?? [], weight: d.weight ?? 0.2,
                   colorHex: d.colorHex ?? colors[i % colors.count])
        }
        var voice: VoiceFingerprint? = nil
        if let v = scan.voice {
            voice = VoiceFingerprint(funnyToSerious: v.funnyToSerious ?? 0.5,
                                     polishedToRaw: v.polishedToRaw ?? 0.5,
                                     teacherToPeer: v.teacherToPeer ?? 0.5,
                                     catchphrases: v.catchphrases ?? [])
        }
        return BrandScanResult(pillars: pillars, voiceUpdate: voice,
                               topThemes: scan.top_themes ?? pillars.map { $0.name })
    }

    // MARK: Onboarding brand digest (async job — clone of the clip-job pattern)

    struct DigestStatus {
        let status: String          // queued | running | ready | failed
        let stage: String           // scraping | transcribing | deriving | writing_scripts | ready
        let scan: BrandScanResult?
        let scripts: [Script]
    }

    private struct DigestResp: Decodable {
        let job_id: String?
        let status: String?
        let stage: String?
        let scan: BrandScanResp.ScanBlock?
        let scripts: [ScriptDTO]?
        let pillar: String?
    }

    /// Kick off the comprehensive onboarding digest (scrape recent reels → transcribe
    /// the top ones → derive brand/voice → write starter scripts). Returns the job id,
    /// or nil when keyless/offline (caller falls back to local generation).
    func startBrandDigest(brand: BrandGraph, voiceTranscript: String? = nil) async -> String? {
        var body = brandBody(brand)
        if let acct = brand.connectedAccounts.first {
            body["handle"] = acct.handle
            body["scan_platform"] = acct.platform
        }
        if let voiceTranscript { body["voice_transcript"] = voiceTranscript }
        guard let data = await post("/v1/onboarding/digest", body),
              let r = try? JSONDecoder().decode(DigestResp.self, from: data) else { return nil }
        return r.job_id
    }

    /// Poll a digest job. Returns nil on network failure or unknown job (404 after
    /// a backend restart) — the caller treats nil as "fall back to local".
    func pollBrandDigest(jobId: String, brand: BrandGraph) async -> DigestStatus? {
        guard let data = await get("/v1/onboarding/digest/\(jobId)"),
              let r = try? JSONDecoder().decode(DigestResp.self, from: data),
              r.status != nil else { return nil }
        let scanResult = r.scan.map(mapScan)
        let style = brand.preferredStyles.first ?? .talkingHead
        let scripts = (r.scripts ?? []).map { script($0, pillar: r.pillar ?? "", style: style) }
        return DigestStatus(status: r.status ?? "failed", stage: r.stage ?? "",
                            scan: scanResult, scripts: scripts)
    }

    // MARK: Voice onboarding

    struct VoiceSession {
        let agentId: String
        let conversationToken: String
        let sessionId: String
        let mode: String
    }

    private struct VoiceSessionResp: Decodable {
        let mode: String?; let agent_id: String?; let conversation_token: String?; let session_id: String?
    }
    private struct VoiceFinalizeResp: Decodable {
        let mode: String?; let scan: BrandScanResp.ScanBlock?
    }

    func voiceOnboardingSession(niche: String) async -> VoiceSession? {
        guard let data = await post("/v1/voice-onboarding/session", ["niche": niche]),
              let r = try? JSONDecoder().decode(VoiceSessionResp.self, from: data) else { return nil }
        note(r.mode)
        return VoiceSession(agentId: r.agent_id ?? "mock",
                            conversationToken: r.conversation_token ?? "mock",
                            sessionId: r.session_id ?? UUID().uuidString,
                            mode: r.mode ?? "mock")
    }

    // MARK: Media analysis

    func analyzeMedia(contentHash: String, filename: String, kind: String, publicURL: String) async -> [String: Any] {
        let body: [String: Any] = [
            "content_hash": contentHash, "filename": filename,
            "kind": kind, "public_url": publicURL,
        ]
        guard let data = await post("/v1/media/analyze", body),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return ["description": "Analyzing…", "tags": [], "broll_suitability": 0,
                    "broll_suitability_reason": "", "usable_as": "broll", "has_face": false,
                    "on_screen_text": "", "mode": "mock"]
        }
        return json
    }

    // MARK: Learning loop

    func registerPost(_ post: ScheduledPost, clip: Clip) async {
        let body: [String: Any] = [
            "post_id": post.id.uuidString,
            "clip_id": clip.id.uuidString,
            "platform": post.platforms.first.map { $0 == .instagram ? "instagram" : "tiktok" } ?? "instagram",
            "scheduled_at": ISO8601DateFormatter().string(from: post.date),
            "pillar": clip.title,
            "style": clip.formatName,
            "format_id": clip.formatId,
            "hook_signal": "",
            "predicted_score": clip.predictedScore,
        ]
        _ = await self.post("/v1/posts/register", body)
    }

    func registerPostMetrics(postId: String, metrics: PostMetrics) async {
        let body: [String: Any] = [
            "post_id": postId,
            "views": metrics.views,
            "likes": metrics.likes,
            "comments": metrics.comments,
            "shares": metrics.shares,
            "saves": metrics.saves,
            "reach": metrics.reach,
            "avg_watch_pct": metrics.avgWatchPct,
            "follows_gained": metrics.followsGained,
        ]
        _ = await self.post("/v1/metrics/ingest", body)
    }

    func fetchRecommendations(niche: String) async -> [[String: Any]] {
        let encoded = niche.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? niche
        guard let data = await get("/v1/recommendations?niche=\(encoded)"),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arms = json["arms"] as? [[String: Any]] else { return [] }
        return arms
    }

    func fetchLearnedInsights() async -> [String: Any] {
        guard let data = await get("/v1/insights/learned"),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return json
    }

    // MARK: - V3: Conversation engine (voice bubble + chat)

    struct ConverseResult {
        let mode: String
        let reply: String
        let memoryUpdates: [MemoryUpdate]
        let intent: String
        let scripts: [Script]?
        let plan: DayPlan?
        let chips: [String]
    }

    private struct ConverseResp: Decodable {
        let mode: String?
        let reply: String
        let memory_updates: [MemoryUpdateDTO]?
        let intent: String?
        let payload: PayloadDTO?
        let suggested_chips: [String]?
        struct MemoryUpdateDTO: Decodable { let op: String; let field: String; let value: String }
        struct PayloadDTO: Decodable {
            let scripts: [ScriptDTO]?
            let plan: PlanDTO?
            struct PlanDTO: Decodable { let blocks: [BlockDTO]? }
            struct BlockDTO: Decodable { let time: String?; let action: String?; let detail: String? }
        }
    }

    func converse(mode: String, messages: [ChatMessage], brand: BrandGraph, memory: CreatorMemory,
                  persona: ChatPersona = .closer, responseLength: ChatResponseLength = .medium) async -> ConverseResult? {
        var body: [String: Any] = [
            "creator_id": creatorId,
            "mode": mode,
            "brand": brandBody(brand),
            "memory": memory.asDictionary,
            "persona": persona.rawValue,
            "response_length": responseLength.rawValue,
        ]
        body["messages"] = messages.suffix(20).map { ["role": $0.role.rawValue, "content": $0.content] }
        guard let data = await post("/v1/converse", body),
              let r = try? JSONDecoder().decode(ConverseResp.self, from: data) else {
            return await fallback.converse(mode: mode, messages: messages, brand: brand, memory: memory)
        }
        note(r.mode)
        let updates = (r.memory_updates ?? []).map { MemoryUpdate(op: $0.op, field: $0.field, value: $0.value) }
        var scripts: [Script]? = nil
        if let dtos = r.payload?.scripts, !dtos.isEmpty {
            scripts = dtos.map { script($0, pillar: $0.title ?? "From chat", style: VideoStyle(rawValue: $0.style ?? "") ?? .talkingHead) }
        }
        var plan: DayPlan? = nil
        if let blocks = r.payload?.plan?.blocks, !blocks.isEmpty {
            plan = DayPlan(blocks: blocks.map { DayPlanBlock(time: $0.time ?? "", action: $0.action ?? "", detail: $0.detail ?? "") })
        }
        return ConverseResult(mode: r.mode ?? "mock", reply: r.reply, memoryUpdates: updates,
                              intent: r.intent ?? "none", scripts: scripts, plan: plan,
                              chips: r.suggested_chips ?? [])
    }

    /// ElevenLabs TTS via the backend. Returns mp3 bytes, or nil → caller uses AVSpeechSynthesizer.
    func tts(text: String) async -> Data? {
        guard let url = URL(string: AppConfig.backendBaseURL + "/v1/tts") else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 30
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["text": text])
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse, http.statusCode == 200,
              http.value(forHTTPHeaderField: "Content-Type")?.contains("audio") == true else { return nil }
        return data
    }

    // MARK: - V3: Daily feed + reels + mimic

    enum FeedEntry {
        case script(Script)
        case reel(ReelItem)
        case trend(TrendItem)
    }

    struct FeedPage {
        let entries: [FeedEntry]
        let nextCursor: Int?
    }

    private struct ReelDTO: Decodable {
        let id: String; let creator_handle: String; let platform: String
        let title: String; let hook_text: String; let transcript: String
        let thumbnail_url: String?; let video_url: String?
        let views: Int?; let likes: Int?; let why_trending: String?
        let format_id: String?; let style: String?; let from_watched: Bool?
    }
    private struct TrendDTO: Decodable { let title: String; let why: String; let formatId: String? }
    private struct FeedItemDTO: Decodable {
        let type: String
        let script: ScriptDTO?
        let reel: ReelDTO?
        let trend: TrendDTO?
    }
    private struct FeedResp: Decodable { let mode: String?; let items: [FeedItemDTO]; let next_cursor: Int? }
    private struct ReelsResp: Decodable { let mode: String?; let reels: [ReelDTO]; let next_cursor: Int? }

    private func reel(_ d: ReelDTO) -> ReelItem {
        ReelItem(id: d.id, creatorHandle: d.creator_handle, platform: d.platform,
                 title: d.title, hookText: d.hook_text, transcript: d.transcript,
                 thumbnailURL: d.thumbnail_url ?? "", videoURL: d.video_url ?? "",
                 views: d.views ?? 0, likes: d.likes ?? 0, whyTrending: d.why_trending ?? "",
                 formatId: d.format_id ?? "myth-buster", style: d.style ?? "talking_head",
                 fromWatched: d.from_watched ?? false)
    }

    private func watchedParam(_ brand: BrandGraph) -> String {
        (brand.watchedCreators ?? []).map { $0.handle }.filter { !$0.isEmpty }.joined(separator: ",")
    }

    private func q(_ s: String) -> String {
        s.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? s
    }

    func fetchFeed(brand: BrandGraph, cursor: Int) async -> FeedPage? {
        let styles = brand.preferredStyles.map { $0.rawValue }.joined(separator: ",")
        let path = "/v1/feed?creator_id=\(q(creatorId))&niche=\(q(brand.niche))"
            + "&audience=\(q(brand.audience))&known_for=\(q(brand.knownFor))"
            + "&goal=\(q(brand.goal.rawValue))&styles=\(q(styles))"
            + "&watched=\(q(watchedParam(brand)))&cursor=\(cursor)"
        guard let data = await get(path),
              let r = try? JSONDecoder().decode(FeedResp.self, from: data) else { return nil }
        note(r.mode)
        let entries: [FeedEntry] = r.items.compactMap { item in
            switch item.type {
            case "script":
                guard let dto = item.script else { return nil }
                let style = VideoStyle(rawValue: dto.style ?? "") ?? .talkingHead
                return .script(script(dto, pillar: dto.title ?? "Daily pick", style: style))
            case "reel":
                guard let dto = item.reel else { return nil }
                return .reel(reel(dto))
            case "trend":
                guard let dto = item.trend else { return nil }
                return .trend(TrendItem(title: dto.title, why: dto.why, formatId: dto.formatId ?? "myth-buster"))
            default: return nil
            }
        }
        return FeedPage(entries: entries, nextCursor: r.next_cursor)
    }

    func fetchReels(brand: BrandGraph, cursor: Int) async -> (reels: [ReelItem], nextCursor: Int?)? {
        let niche = brand.niche.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? ""
        let watched = watchedParam(brand).addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? ""
        guard let data = await get("/v1/reels?niche=\(niche)&creator_id=\(creatorId)&watched=\(watched)&cursor=\(cursor)"),
              let r = try? JSONDecoder().decode(ReelsResp.self, from: data) else { return nil }
        return (r.reels.map(reel), r.next_cursor)
    }

    private struct MimicResp: Decodable {
        let mode: String?
        let script: ScriptDTO
        let mimicked_from: ProvenanceDTO?
        struct ProvenanceDTO: Decodable { let creator_handle: String?; let platform: String? }
    }

    func mimic(reelItem: ReelItem, brand: BrandGraph, memory: CreatorMemory) async -> (script: Script, from: String)? {
        let body: [String: Any] = [
            "creator_id": creatorId,
            "brand": brandBody(brand),
            "memory": memory.asDictionary,
            "reel": ["id": reelItem.id, "creator_handle": reelItem.creatorHandle,
                     "platform": reelItem.platform, "title": reelItem.title,
                     "hook_text": reelItem.hookText, "transcript": reelItem.transcript,
                     "why_trending": reelItem.whyTrending, "views": reelItem.views,
                     "likes": reelItem.likes, "format_id": reelItem.formatId, "style": reelItem.style],
        ]
        guard let data = await post("/v1/mimic", body),
              let r = try? JSONDecoder().decode(MimicResp.self, from: data) else {
            return await fallback.mimic(reelItem: reelItem, brand: brand, memory: memory)
        }
        note(r.mode)
        let style = VideoStyle(rawValue: r.script.style ?? "") ?? .talkingHead
        let s = script(r.script, pillar: "Mimic: @\(reelItem.creatorHandle)", style: style)
        return (s, "@" + (r.mimicked_from?.creator_handle ?? reelItem.creatorHandle))
    }

    // MARK: - V3: Video-link analysis (chat)

    private struct AnalyzeVideoResp: Decodable {
        let mode: String?; let platform: String?; let transcript: String?
        let hook_analysis: String?; let structure_beats: [String]?
        let why_it_works: String?; let suggestions: [String]?
        let your_version: ScriptDTO?
    }

    func analyzeVideo(url: String, brand: BrandGraph, memory: CreatorMemory) async -> VideoAnalysis? {
        let body: [String: Any] = ["url": url, "creator_id": creatorId,
                                   "brand": brandBody(brand), "memory": memory.asDictionary]
        guard let data = await post("/v1/analyze-video", body),
              let r = try? JSONDecoder().decode(AnalyzeVideoResp.self, from: data) else {
            return await fallback.analyzeVideo(url: url, brand: brand, memory: memory)
        }
        note(r.mode)
        var version: Script? = nil
        if let dto = r.your_version {
            let style = VideoStyle(rawValue: dto.style ?? "") ?? .talkingHead
            version = script(dto, pillar: "Your version", style: style)
        }
        return VideoAnalysis(url: url, platform: r.platform ?? "",
                             transcript: r.transcript ?? "",
                             hookAnalysis: r.hook_analysis ?? "",
                             structureBeats: r.structure_beats ?? [],
                             whyItWorks: r.why_it_works ?? "",
                             suggestions: r.suggestions ?? [],
                             yourVersion: version)
    }

    // MARK: - V3: Brand summary + performance summary

    private struct BrandSummaryResp: Decodable {
        let mode: String?; let summary: String; let traits: [String]?; let working_on: String?
    }

    func fetchBrandSummary(brand: BrandGraph, memory: CreatorMemory) async -> BrandSummaryCard? {
        let body: [String: Any] = ["creator_id": creatorId,
                                   "brand": brandBody(brand), "memory": memory.asDictionary]
        guard let data = await post("/v1/brand-summary", body),
              let r = try? JSONDecoder().decode(BrandSummaryResp.self, from: data) else { return nil }
        note(r.mode)
        return BrandSummaryCard(summary: r.summary, traits: r.traits ?? [],
                                workingOn: r.working_on ?? "", updatedAt: Date())
    }

    struct PerformanceSummary: Decodable {
        struct Totals: Decodable {
            let views: Int; let likes: Int; let follows_gained: Int
            let posts: Int; let engagement_rate: Double
        }
        struct PlatformStats: Decodable {
            let views: Int; let likes: Int; let follows_gained: Int; let posts: Int
        }
        struct DailyPoint: Decodable { let day: Int; let views: Int; let likes: Int }
        struct BestPost: Decodable {
            let post_id: String?; let views: Int; let likes: Int
            let format_id: String?; let platform: String?
        }
        struct FormatMix: Decodable { let format: String; let count: Int }
        let mode: String?
        let days: Int
        let totals: Totals
        let platforms: [String: PlatformStats]
        let daily: [DailyPoint]
        let best_post: BestPost?
        let format_mix: [FormatMix]
    }

    func fetchPerformanceSummary(days: Int = 30) async -> PerformanceSummary? {
        guard let data = await get("/v1/performance/summary?creator_id=\(creatorId)&days=\(days)"),
              let r = try? JSONDecoder().decode(PerformanceSummary.self, from: data) else { return nil }
        note(r.mode)
        return r
    }

    func voiceOnboardingFinalize(niche: String, transcript: [[String: String]]) async -> BrandScanResult? {
        let body: [String: Any] = ["niche": niche, "transcript": transcript]
        guard let data = await post("/v1/voice-onboarding/finalize", body),
              let r = try? JSONDecoder().decode(VoiceFinalizeResp.self, from: data),
              let scan = r.scan else { return nil }
        note(r.mode)
        let colors = Catalog.pillarColors
        let pillars = (scan.pillars ?? []).enumerated().map { i, d in
            Pillar(name: d.name, summary: d.summary ?? "", angle: d.angle ?? "",
                   exampleTopics: d.exampleTopics ?? [], weight: d.weight ?? 0.2,
                   colorHex: d.colorHex ?? colors[i % colors.count])
        }
        var voice: VoiceFingerprint? = nil
        if let v = scan.voice {
            voice = VoiceFingerprint(funnyToSerious: v.funnyToSerious ?? 0.5,
                                     polishedToRaw: v.polishedToRaw ?? 0.5,
                                     teacherToPeer: v.teacherToPeer ?? 0.5,
                                     catchphrases: v.catchphrases ?? [])
        }
        return BrandScanResult(pillars: pillars, voiceUpdate: voice,
                               topThemes: scan.top_themes ?? pillars.map { $0.name })
    }
}
