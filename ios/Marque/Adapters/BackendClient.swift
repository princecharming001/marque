import Foundation

// The app's single AI client. Talks ONLY to our FastAPI backend (which holds every vendor key
// and decides live-vs-mock internally) — the iOS app ships no Anthropic key. Conforms to the
// existing LLMRouting protocol so all call sites are unchanged. Degrades to the local
// MockLLMRouter on any network/decoding failure so the app never hard-stalls offline.

final class BackendClient: LLMRouting, @unchecked Sendable {
    private let fallback = MockLLMRouter()
    var token: String?               // Supabase JWT, attached once auth lands
    var creatorId = "default"        // set by AuthManager on sign-in (scopes memory + learning)
    var creatorHandle = ""           // creator's own social handle — feeds the metrics poller
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

    #if DEBUG
    /// DEBUG demo only: force this creator's paid tier on the backend (empty string clears).
    /// Returns the resolved {tier, entitlements, metrics_sources}, or nil if the backend has
    /// the dev override disabled (ALLOW_DEV_TIER unset → 403) or is offline.
    func setDevTier(_ tier: String) async -> [String: Any]? {
        guard let data = await post("/v1/dev/tier", ["creator_id": creatorId, "tier": tier]) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }
    #endif

    /// AF-I4: like get(), but surfaces the HTTP status so poll loops can distinguish
    /// "transient miss, keep polling" from a permanently-gone job (404 never existed /
    /// structured 410 swept). Returns (nil, 0) on transport failure.
    func getWithStatus(_ path: String) async -> (Data?, Int) {
        guard let url = URL(string: AppConfig.backendBaseURL + path) else { return (nil, 0) }
        var req = URLRequest(url: url); req.timeoutInterval = 30
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse else { return (nil, 0) }
        return (data, http.statusCode)
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
        // Short keys, not display labels — the backend's strategy-hint maps are
        // keyed 'ideas'/'natural'/… and never matched the full label strings.
        if let bl = b.biggestBlocker { body["biggest_blocker"] = bl.key }
        if let c = b.cameraComfort { body["camera_comfort"] = c.key }
        if let wt = b.weeklyTarget { body["weekly_target"] = wt }
        if let w = b.whyNow { body["why_now"] = w.key }
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
                      predictedScore: min(100, max(0, dto.predictedScore ?? 80)),
                      whyPicked: dto.why_picked ?? "")
    }
    /// Palo port (audit: /v1/write/from-brief had no consumer): the write agent expands a
    /// brief's one-line summary into the FULL filmable script — strategy/memory/exemplar
    /// aware, in the creator's voice. nil on off/keyless/failure → caller keeps the summary.
    func expandBrief(title: String, summary: String, brand: BrandGraph) async -> (title: String, body: String)? {
        // _BriefScriptRequest nests the brand dict (unlike the Brand-inheriting routes).
        let payload: [String: Any] = ["creator_id": creatorId,
                                      "brief": ["title": title, "summary": summary],
                                      "brand": brandBody(brand)]
        guard let data = await post("/v1/write/from-brief", payload),
              let r = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              (r["mode"] as? String) == "live",
              let full = r["body"] as? String, !full.isEmpty else { return nil }
        note("live")
        return ((r["title"] as? String) ?? title, full)
    }

    /// Palo idea-bank brief → a starter Script pick. Title + summary make a fileable card;
    /// filming expands the full body on demand (from the brief). Renders identically to the
    /// bandit's script picks, so the idea bank is finally visible on Home.
    private func briefScript(_ item: FeedItemDTO) -> Script {
        let fid = formatId(nil)
        let title = item.title ?? "New idea"
        return Script(pillarName: "Idea", title: title, summary: item.summary ?? "",
                      style: VideoStyle.talkingHead.rawValue, formatId: fid,
                      hook: Hook(text: title, signal: .curiosity, strength: 80), altHooks: [],
                      body: item.summary ?? "", cta: "", shotPlan: [],
                      targetSeconds: Catalog.format(fid).targetSeconds,
                      predictedScore: 80, whyPicked: item.summary ?? "")
    }

    // MARK: DTOs

    struct HookDTO: Decodable { let text: String; let signal: String?; let strength: Int? }
    struct ScriptDTO: Decodable {
        let title: String?; let summary: String?
        let hook: String; let hookSignal: String?; let formatId: String?
        let body: String; let cta: String; let shotPlan: [String]?
        let targetSeconds: Int?; let predictedScore: Int?; let altHooks: [HookDTO]?; let style: String?
        let why_picked: String?     // UX-G2: optional — absent on old backends
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
        // P7.5: try the Palo write agent first (strategy+memory+exemplar-aware co-writing).
        // It edits the script BODY via exact-substring actions; if it returns a real edit we
        // take it, otherwise (off / answer-only / no change) fall through to the legacy steer
        // so hook/CTA rewrites keep today's behavior.
        if let data = await post("/v1/write/turn",
                                 ["creator_id": creatorId, "instruction": instruction,
                                  "script": ["title": s.title, "body": s.body]]),
           let r = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           (r["mode"] as? String) != "off",
           let preview = r["preview"] as? [String: Any],
           let newBody = preview["body"] as? String,
           !newBody.isEmpty, newBody != s.body {
            note("Claude")
            var out = s
            out.body = newBody
            return out
        }
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

    func interpretInsights(brand: BrandGraph, summary: String, persona: String = "closer") async -> String {
        var body = brandBody(brand); body["summary"] = summary; body["persona"] = persona   // C-09
        guard let data = await post("/v1/insights", body),
              let r = try? JSONDecoder().decode(InsightsResp.self, from: data), !r.coaching.isEmpty else {
            return await fallback.interpretInsights(brand: brand, summary: summary, persona: persona)
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

    // MARK: Social account linking (Post for Me OAuth — real posting authority)

    private struct AuthURLResp: Decodable { let url: String?; let platform: String?; let mode: String? }
    private struct SocialAccountsResp: Decodable {
        let accounts: [Acct]; let mode: String?
        struct Acct: Decodable {
            let id: String; let platform: String; let username: String?
            let profile_photo_url: String?; let status: String?
        }
    }

    /// Mint an OAuth URL for the user to connect one platform account. `externalId`
    /// tags the account so we can look it up afterwards. Empty url => linking unavailable
    /// (no key / mock backend).
    func socialAuthURL(platform: String, externalId: String, redirectURL: String) async -> String? {
        guard let data = await post("/v1/social/auth-url",
                                    ["platform": platform, "external_id": externalId,
                                     "redirect_url": redirectURL]),
              let r = try? JSONDecoder().decode(AuthURLResp.self, from: data),
              let url = r.url, !url.isEmpty else { return nil }
        note(r.mode ?? "")
        return url
    }

    /// Fetch connected accounts, filtered by our `externalId` tag and/or `platform`
    /// (post-OAuth). Returns the ConnectedAccount(s) carrying the Post for Me `accountId`
    /// (spc_…) needed to publish. Passing only `platform` lists every account on that
    /// platform — used to ADOPT an account already linked under another tag (Post for Me
    /// forbids re-linking it under a new tag, but it still posts fine by spc_ id).
    func socialAccounts(externalId: String = "", platform: String = "") async -> [ConnectedAccount] {
        var items: [String] = []
        if !externalId.isEmpty {
            items.append("external_id=" + (externalId.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? externalId))
        }
        if !platform.isEmpty { items.append("platform=" + platform) }
        let query = items.isEmpty ? "" : "?" + items.joined(separator: "&")
        guard let data = await get("/v1/social/accounts\(query)"),
              let r = try? JSONDecoder().decode(SocialAccountsResp.self, from: data) else { return [] }
        note(r.mode ?? "")
        return r.accounts
            .filter { $0.status == nil || $0.status == "connected" }
            .map { a in
                ConnectedAccount(platform: a.platform, handle: a.username ?? "",
                                 displayName: a.username ?? "", avatarUrl: a.profile_photo_url ?? "",
                                 accountId: a.id)
            }
    }

    private struct MusicResp: Decodable {
        let tracks: [MTrack]
        struct MTrack: Decodable { let name: String; let url: String }
    }

    /// GET /v1/music — the beds the render/auto-selection uses, so the editor picker shows
    /// the SAME catalog (single source of truth). Empty on failure → caller keeps fallback.
    func musicCatalog() async -> [MusicCatalog.Track] {
        guard let data = await get("/v1/music"),
              let r = try? JSONDecoder().decode(MusicResp.self, from: data) else { return [] }
        return r.tracks
            .filter { !$0.url.isEmpty }
            .map { MusicCatalog.Track(name: $0.name, url: $0.url) }
    }

    /// Revoke an OAuth-linked account on Post for Me (best-effort).
    @discardableResult
    func socialDisconnect(accountId: String) async -> Bool {
        guard !accountId.isEmpty,
              let data = await post("/v1/social/disconnect", ["account_id": accountId]),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return (json["ok"] as? Bool) ?? false
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
        // Identity the scan derived from the creator's REAL posts. Used to prefill
        // the quiz's freeform steps (confirm-not-type) — empty when unknown.
        var nicheGuess: String = ""
        var audienceGuess: String = ""
        var knownForGuess: String = ""
    }

    private struct BrandScanResp: Decodable {
        let mode: String?
        let scan: ScanBlock?
        struct ScanBlock: Decodable {
            let pillars: [PillarDTO]?
            let voice: VoiceBlock?
            let top_themes: [String]?
            let niche: String?
            let audience: String?
            let knownFor: String?
            struct VoiceBlock: Decodable {
                let funnyToSerious: Double?
                let polishedToRaw: Double?
                let teacherToPeer: Double?
                let catchphrases: [String]?
            }
        }
    }

    func brandScan(handle: String, platform: String, niche: String) async -> BrandScanResult? {
        // B3: creator_id so the backend persists the real scraped posts to creator_posts
        // (the feed/mimic/analyze-video/converse prompts hydrate them server-side later —
        // the client never holds these posts at all).
        let body: [String: Any] = ["handle": handle, "platform": platform, "niche": niche,
                                   "creator_id": creatorId]
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
                               topThemes: scan.top_themes ?? pillars.map { $0.name },
                               nicheGuess: scan.niche ?? "",
                               audienceGuess: scan.audience ?? "",
                               knownForGuess: scan.knownFor ?? "")
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

    // MARK: Palo brain surfaces (P7.3 insights inbox + P7.4 Your Strategy)

    struct InsightItem: Identifiable, Hashable {
        let id: String
        let category: String        // blue|yellow|green|orange (server-side type color)
        let title: String
        let description: String
        let seedPrompt: String      // what tapping the card asks the chat
    }

    /// P7.3: the creator's post-performance insight feed. Empty when the feature is off,
    /// nothing has fired yet, or offline — the section simply doesn't render.
    func fetchInsights(limit: Int = 20) async -> [InsightItem] {
        guard let data = await get("/v1/insights?creator_id=\(q(creatorId))&limit=\(limit)"),
              let r = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rows = r["insights"] as? [[String: Any]] else { return [] }
        note(r["mode"] as? String ?? "Mock")
        return rows.compactMap { row in
            guard let id = row["id"] as? String,
                  let title = row["title"] as? String, !title.isEmpty else { return nil }
            let desc = row["description"] as? String ?? ""
            let seed = (row["conversation_seed"] as? [String: Any])?["prompt"] as? String
            return InsightItem(id: id,
                               category: row["category"] as? String ?? "blue",
                               title: title, description: desc,
                               seedPrompt: seed ?? "My insight: \(title). \(desc) What should I make next to build on this?")
        }
    }

    struct StrategyDoc: Hashable {
        let markdown: String
        let revision: Int
        let updatedAt: String
        let updates: [String]       // recent "what changed" one-liners
    }

    /// P7.4: the compiled strategy (the creator's "brain"). nil when off / not yet compiled.
    func fetchStrategy() async -> StrategyDoc? {
        guard let data = await get("/v1/strategy?creator_id=\(q(creatorId))"),
              let r = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let strat = r["strategy"] as? [String: Any],
              let md = strat["strategy_markdown"] as? String, !md.isEmpty else { return nil }
        note(r["mode"] as? String ?? "Mock")
        let updates = (r["updates"] as? [[String: Any]] ?? [])
            .compactMap { $0["update_text"] as? String }
        return StrategyDoc(markdown: md,
                           revision: strat["strategy_revision"] as? Int ?? 0,
                           updatedAt: strat["strategy_updated_at"] as? String ?? "",
                           updates: updates)
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
            // Palo port: the creator's own handle — lets the backend metrics poller
            // scrape this account so post-performance insights can fire.
            "handle": creatorHandle,
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
        // "mock" = instant first-paint fallback; the server upgrades it to real AI
        // in the background, so the client re-fetches once to swap it in.
        var mode: String? = nil
    }

    private struct ReelDTO: Decodable {
        let id: String; let creator_handle: String; let platform: String
        let title: String; let hook_text: String; let transcript: String
        let thumbnail_url: String?; let video_url: String?
        let views: Int?; let likes: Int?; let why_trending: String?
        let format_id: String?; let style: String?; let from_watched: Bool?
        let transcribed: Bool?      // true = real spoken transcript, false = caption fallback
        // UX-A3: optional — absent on old backends, decode stays safe.
        let edit_format: String?; let why_match: String?; let sample: Bool?
    }
    private struct TrendDTO: Decodable { let title: String; let why: String; let formatId: String? }
    private struct FeedItemDTO: Decodable {
        let type: String?               // OPTIONAL: Palo idea-bank briefs merge in with NO `type`.
        let script: ScriptDTO?          // A non-optional `type` made the whole feed fail to decode
        let reel: ReelDTO?              // → silent mock fallback whenever IDEA_BANK was on (audit CRITICAL-1).
        let trend: TrendDTO?
        // Palo idea-bank brief shape: {kind:"idea", source:"idea_bank", title, summary, brief_id}
        let kind: String?
        let source: String?
        let title: String?
        let summary: String?
    }
    private struct FeedResp: Decodable { let mode: String?; let items: [FeedItemDTO]; let next_cursor: Int? }
    private struct ReelsResp: Decodable { let mode: String?; let reels: [ReelDTO]; let next_cursor: Int? }

    private func reel(_ d: ReelDTO) -> ReelItem {
        ReelItem(id: d.id, creatorHandle: d.creator_handle, platform: d.platform,
                 title: d.title, hookText: d.hook_text, transcript: d.transcript,
                 thumbnailURL: d.thumbnail_url ?? "", videoURL: d.video_url ?? "",
                 views: d.views ?? 0, likes: d.likes ?? 0, whyTrending: d.why_trending ?? "",
                 formatId: d.format_id ?? "myth-buster", style: d.style ?? "talking_head",
                 fromWatched: d.from_watched ?? false, transcribed: d.transcribed ?? false,
                 editFormat: d.edit_format ?? "", whyMatch: d.why_match ?? "",
                 sample: d.sample ?? false)
    }

    private func watchedParam(_ brand: BrandGraph) -> String {
        // `platform:handle` tokens so the backend scrapes the RIGHT network — you
        // can't tell IG from TikTok from a bare handle. Back-compat: the backend
        // still accepts bare handles (defaults instagram).
        (brand.watchedCreators ?? [])
            .filter { !$0.handle.isEmpty }
            .map { "\($0.platform.rawValue):\($0.handle)" }
            .joined(separator: ",")
    }

    /// Example reels for one edit format — the "match a vibe" cards shown before
    /// submit; the picked one returns to POST /v1/clips as `reference_reel`.
    func editExamples(format: String, niche: String) async -> [ReelItem] {
        guard let data = await get("/v1/reels/examples?format=\(q(format))&niche=\(q(niche))"),
              let r = try? JSONDecoder().decode(ReelsResp.self, from: data) else { return [] }
        return r.reels.map(reel)
    }

    private struct StylesResp: Decodable { let styles: [StyleDTO] }
    private struct StyleDTO: Decodable {
        let theme_id: String; let label: String; let blurb: String
        let video_url: String?; let thumbnail_url: String?; let handle: String?; let sample: Bool?
    }

    /// The "match a vibe" style gallery — editing styles (theme bundles), each with a
    /// playable talking-head demo. The picked style's themeId returns to POST /v1/clips
    /// and drives the actual edit.
    func styles(niche: String) async -> [StyleOption] {
        guard let data = await get("/v1/styles?niche=\(q(niche))"),
              let r = try? JSONDecoder().decode(StylesResp.self, from: data) else { return [] }
        return r.styles.map {
            StyleOption(themeId: $0.theme_id, label: $0.label, blurb: $0.blurb,
                        videoURL: $0.video_url ?? "", thumbnailURL: $0.thumbnail_url ?? "",
                        handle: $0.handle ?? "", sample: $0.sample ?? false)
        }
    }

    private struct BrollStylesResp: Decodable { let styles: [BrollStyleDTO] }
    private struct BrollStyleDTO: Decodable {
        let id: String; let label: String; let blurb: String
        let video_url: String?; let thumbnail_url: String?; let handle: String?; let sample: Bool?
    }

    /// The B-ROLL STYLE picker — how much cutaway coverage (full/balanced/minimal/none),
    /// each option demonstrated by a real example reel. The picked id returns to
    /// POST /v1/clips as config.broll_coverage and drives the actual edit.
    func brollStyles(niche: String) async -> [BrollStyleOption] {
        guard let data = await get("/v1/broll-styles?niche=\(q(niche))"),
              let r = try? JSONDecoder().decode(BrollStylesResp.self, from: data) else { return [] }
        return r.styles.map {
            BrollStyleOption(id: $0.id, label: $0.label, blurb: $0.blurb,
                             videoURL: $0.video_url ?? "", thumbnailURL: $0.thumbnail_url ?? "",
                             handle: $0.handle ?? "", sample: $0.sample ?? false)
        }
    }

    private struct WarmResp: Decodable { let ok: Bool? }

    /// Fire-and-forget: pre-scrape a newly-added watched creator so their real
    /// reels are cached by the time the user reaches Home. Non-blocking.
    @discardableResult
    func warmWatchedCreator(handle: String, platform: String) async -> Bool {
        guard let data = await post("/v1/reels/warm", ["handle": handle, "platform": platform]),
              let r = try? JSONDecoder().decode(WarmResp.self, from: data) else { return false }
        return r.ok ?? false
    }

    private func q(_ s: String) -> String {
        s.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? s
    }

    /// I-2/I-8: fire-and-forget Today's-picks feedback → the backend learning loop.
    /// 404-tolerant (older backends), so it's safe to call before the endpoint deploys.
    func sendFeedFeedback(script: Script, niche: String, verdict: String) async {
        _ = await post("/v1/feed/feedback", [
            "creator_id": creatorId, "verdict": verdict, "niche": niche,
            "script": ["title": script.title, "hook": script.hook.text,
                       "pillar": script.pillarName, "style": script.style,
                       "formatId": script.formatId, "hookSignal": script.hook.signal.rawValue],
        ])
    }

    /// I-8: end-of-voice-session memory distill. Returns extracted updates (empty on keyless,
    /// short session, 404, or transport failure — all treated as "nothing to add").
    func distillMemory(transcript: [[String: String]], memory: CreatorMemory, brand: BrandGraph) async -> [MemoryUpdate] {
        guard let data = await post("/v1/memory/distill", [
            "creator_id": creatorId, "transcript": transcript,
            "memory": memory.asDictionary, "brand": brandBody(brand),
        ]) else { return [] }
        struct Resp: Decodable { let memory_updates: [MU]?; struct MU: Decodable { let op: String; let field: String; let value: String } }
        guard let r = try? JSONDecoder().decode(Resp.self, from: data) else { return [] }
        return (r.memory_updates ?? []).map { MemoryUpdate(op: $0.op, field: $0.field, value: $0.value) }
    }

    func fetchFeed(brand: BrandGraph, memory: CreatorMemory, cursor: Int) async -> FeedPage? {
        let styles = brand.preferredStyles.map { $0.rawValue }.joined(separator: ",")
        // I-8: POST so the creator's memory (yap-session context) personalizes picks. Falls
        // back to the GET path if POST isn't available (older backend → 404/nil).
        // B3: merge the FULL brand (brandBody already builds voice/catchphrases/
        // non_negotiables/what_you_do/emulation_targets for every other route) — this
        // previously sent only 4 fields, so the feed had never seen how the creator
        // actually talks. brandBody's keys win on overlap (niche/audience/known_for/goal
        // are identical either way).
        var body: [String: Any] = [
            "creator_id": creatorId, "styles": styles,
            "watched": watchedParam(brand), "cursor": cursor,
            "memory": memory.asDictionary,
        ]
        body.merge(brandBody(brand)) { _, new in new }
        var data = await post("/v1/feed", body)
        if data == nil {                        // fallback: GET (no personalization)
            let path = "/v1/feed?creator_id=\(q(creatorId))&niche=\(q(brand.niche))"
                + "&audience=\(q(brand.audience))&known_for=\(q(brand.knownFor))"
                + "&goal=\(q(brand.goal.rawValue))&styles=\(q(styles))"
                + "&watched=\(q(watchedParam(brand)))&cursor=\(cursor)"
            data = await get(path)
        }
        guard let data, let r = try? JSONDecoder().decode(FeedResp.self, from: data) else { return nil }
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
            default:
                // Palo idea-bank brief (no `type`) → surface as a fileable pick card.
                if item.source == "idea_bank" || item.kind == "idea", let t = item.title, !t.isEmpty {
                    return .script(briefScript(item))
                }
                return nil
            }
        }
        return FeedPage(entries: entries, nextCursor: r.next_cursor, mode: r.mode)
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
            let comments: Int?; let shares: Int?      // B-2 additive
        }
        struct PlatformStats: Decodable {
            let views: Int; let likes: Int; let follows_gained: Int; let posts: Int
        }
        struct DailyPoint: Decodable {
            let day: Int; let views: Int; let likes: Int
            let date: String?                         // B-1 additive: ISO yyyy-MM-dd for the graph
        }
        struct BestPost: Decodable {
            let post_id: String?; let views: Int; let likes: Int
            let format_id: String?; let platform: String?
        }
        struct FormatMix: Decodable { let format: String; let count: Int }
        let mode: String?
        let no_data: Bool?          // C-04/C-05: true when the series is placeholder, not measured
        let best_hour: Int?         // C-11/C-12: the creator's real best posting hour
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
                               topThemes: scan.top_themes ?? pillars.map { $0.name },
                               nicheGuess: scan.niche ?? "",
                               audienceGuess: scan.audience ?? "",
                               knownForGuess: scan.knownFor ?? "")
    }
}
