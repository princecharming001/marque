import Foundation

// The app's single AI client. Talks ONLY to our FastAPI backend (which holds every vendor key
// and decides live-vs-mock internally) — the iOS app ships no Anthropic key. Conforms to the
// existing LLMRouting protocol so all call sites are unchanged. Degrades to the local
// MockLLMRouter on any network/decoding failure so the app never hard-stalls offline.

final class BackendClient: LLMRouting, @unchecked Sendable {
    private let fallback = MockLLMRouter()
    var token: String?               // Supabase JWT, attached once auth lands
    private(set) var lastMode = "Mock"   // "Claude" once a live response comes back

    // MARK: HTTP

    private func post(_ path: String, _ body: [String: Any]) async -> Data? {
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

    private func get(_ path: String) async -> Data? {
        guard let url = URL(string: AppConfig.backendBaseURL + path) else { return nil }
        var req = URLRequest(url: url); req.timeoutInterval = 30
        if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return nil }
        return data
    }

    private func note(_ mode: String?) { if let mode { lastMode = mode == "live" ? "Claude" : "Mock" } }

    // MARK: Serialization

    private func brandBody(_ b: BrandGraph) -> [String: Any] {
        [
            "niche": b.niche, "audience": b.audience, "known_for": b.knownFor,
            "what_you_do": b.whatYouDo, "goal": b.goal.rawValue,
            "voice": ["funnyToSerious": b.voice.funnyToSerious,
                      "polishedToRaw": b.voice.polishedToRaw,
                      "teacherToPeer": b.voice.teacherToPeer],
            "non_negotiables": b.nonNegotiables,
        ]
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

    func generateScripts(brand: BrandGraph, pillar: Pillar, count: Int, mediaContext: String, style: VideoStyle) async -> [Script] {
        var body = brandBody(brand)
        body["pillar"] = pillar.name
        body["pillar_summary"] = pillar.summary
        body["pillar_angle"] = pillar.angle
        body["example_topics"] = pillar.exampleTopics
        body["style"] = style.rawValue
        body["count"] = count
        body["media_context"] = mediaContext
        guard let data = await post("/v1/scripts", body),
              let r = try? JSONDecoder().decode(ScriptsResp.self, from: data), !r.scripts.isEmpty else {
            return await fallback.generateScripts(brand: brand, pillar: pillar, count: count, mediaContext: mediaContext, style: style)
        }
        note(r.mode)
        return r.scripts.map { script($0, pillar: pillar.name, style: style) }
    }

    func hookLab(brand: BrandGraph, topic: String) async -> [Hook] {
        var body = brandBody(brand); body["topic"] = topic
        guard let data = await post("/v1/hooks", body),
              let r = try? JSONDecoder().decode(HooksResp.self, from: data), !r.hooks.isEmpty else {
            return await fallback.hookLab(brand: brand, topic: topic)
        }
        note(r.mode)
        return r.hooks.map(hook).sorted { $0.strength > $1.strength }
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
}
