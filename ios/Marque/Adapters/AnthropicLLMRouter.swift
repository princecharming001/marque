import Foundation

// MARK: - Config
// Where the app finds the Anthropic key. NOTE: the locked architecture (DECISIONS.md) routes
// AI through the FastAPI backend so the key never ships in the app. This direct path is a
// DEV convenience so "paste a key -> real Claude" works today; production swaps the baseURL
// to the Marque backend (which holds the real key) and drops the x-api-key header.

enum AppConfig {
    static var anthropicKey: String {
        if let k = ProcessInfo.processInfo.environment["ANTHROPIC_API_KEY"], !k.isEmpty { return k }
        if let k = UserDefaults.standard.string(forKey: "anthropic.key"), !k.isEmpty { return k }
        if let k = Bundle.main.object(forInfoDictionaryKey: "ANTHROPIC_API_KEY") as? String, !k.isEmpty { return k }
        return ""
    }
    static var anthropicBaseURL: String {
        ProcessInfo.processInfo.environment["ANTHROPIC_BASE_URL"] ?? "https://api.anthropic.com"
    }
    static var useLiveAI: Bool { !anthropicKey.isEmpty }
}

// MARK: - Live Claude adapter

struct AnthropicLLMRouter: LLMRouting {
    private let opus = "claude-opus-4-8"
    private let haiku = "claude-haiku-4-5-20251001"
    private let fallback = MockLLMRouter()   // resilience: any failure degrades to the mock

    // MARK: API call

    private struct MsgReq: Encodable {
        let model: String; let max_tokens: Int; let system: String; let messages: [Msg]
        struct Msg: Encodable { let role: String; let content: String }
    }
    private struct MsgResp: Decodable {
        let content: [Block]
        struct Block: Decodable { let type: String; let text: String? }
    }

    private func call(model: String, system: String, user: String, maxTokens: Int) async throws -> String {
        guard let url = URL(string: AppConfig.anthropicBaseURL + "/v1/messages") else { throw URLError(.badURL) }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 60
        req.setValue(AppConfig.anthropicKey, forHTTPHeaderField: "x-api-key")
        req.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        let body = MsgReq(model: model, max_tokens: maxTokens, system: system,
                          messages: [.init(role: "user", content: user)])
        req.httpBody = try JSONEncoder().encode(body)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else {
            throw NSError(domain: "anthropic", code: (resp as? HTTPURLResponse)?.statusCode ?? -1)
        }
        let decoded = try JSONDecoder().decode(MsgResp.self, from: data)
        return decoded.content.compactMap { $0.text }.joined()
    }

    // MARK: JSON helpers

    private func extractJSON(_ s: String, array: Bool) -> Data? {
        let open: Character = array ? "[" : "{"
        let close: Character = array ? "]" : "}"
        guard let start = s.firstIndex(of: open), let end = s.lastIndex(of: close), start < end else { return nil }
        return String(s[start...end]).data(using: .utf8)
    }

    private struct HookDTO: Decodable { let text: String; let signal: String?; let strength: Int? }
    private struct ScriptDTO: Decodable {
        let hook: String; let hookSignal: String?; let formatId: String?
        let body: String; let cta: String; let shotPlan: [String]?
        let targetSeconds: Int?; let predictedScore: Int?; let altHooks: [HookDTO]?
    }
    private struct TeardownDTO: Decodable { let headline: String; let detail: String; let liftPercent: Int? }

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
    private func baseScript(_ dto: ScriptDTO, pillar: String) -> Script {
        let fid = formatId(dto.formatId)
        let primary = Hook(text: dto.hook, signal: signal(dto.hookSignal),
                           strength: min(100, max(0, dto.predictedScore ?? 80)))
        return Script(pillarName: pillar, formatId: fid,
                      hook: primary, altHooks: (dto.altHooks ?? []).map(hook),
                      body: dto.body, cta: dto.cta,
                      shotPlan: dto.shotPlan ?? [], targetSeconds: dto.targetSeconds ?? Catalog.format(fid).targetSeconds,
                      predictedScore: min(100, max(0, dto.predictedScore ?? 80)))
    }

    private func brandBlock(_ b: BrandGraph) -> String {
        """
        Creator brand:
        - niche: \(b.niche)
        - what they do: \(b.whatYouDo)
        - audience: \(b.audience)
        - wants to be known for: \(b.knownFor)
        - goal: \(b.goal.rawValue)
        - voice (0..1): funny→serious \(b.voice.funnyToSerious), polished→raw \(b.voice.polishedToRaw), teacher→peer \(b.voice.teacherToPeer)
        - never say: \(b.nonNegotiables.joined(separator: ", "))
        """
    }

    private var formatList: String {
        Catalog.formats.map { "  \($0.id): \($0.name) — \($0.blurb)" }.joined(separator: "\n")
    }

    private let scriptSchema = """
    Each item: {"hook": str, "hookSignal": one of [stakes,authority,curiosity,patternInterrupt,specificity,contrarian,narrative,callOut], "formatId": one of the format ids, "body": str, "cta": str, "shotPlan": [str], "targetSeconds": int, "predictedScore": int 0-100, "altHooks": [{"text": str, "signal": str, "strength": int}]}
    """

    // MARK: LLMRouting

    func generateScripts(brand: BrandGraph, pillar: Pillar, count: Int) async -> [Script] {
        let system = "You are Marque's script engine. Write short-form video scripts in the creator's EXACT voice — match their tone sliders, never use banned phrases. Hooks must land in the first 3 seconds. Reply with ONLY valid JSON, no prose, no code fences."
        let user = """
        \(brandBlock(brand))
        Content pillar: \(pillar.name)
        Available formats (choose formatId from these):
        \(formatList)

        Write \(count) scripts. Return ONLY a JSON array. \(scriptSchema)
        """
        do {
            let txt = try await call(model: opus, system: system, user: user, maxTokens: 3000)
            guard let data = extractJSON(txt, array: true) else { throw URLError(.cannotParseResponse) }
            let dtos = try JSONDecoder().decode([ScriptDTO].self, from: data)
            let scripts = dtos.map { baseScript($0, pillar: pillar.name) }
            return scripts.isEmpty ? await fallback.generateScripts(brand: brand, pillar: pillar, count: count) : scripts
        } catch {
            return await fallback.generateScripts(brand: brand, pillar: pillar, count: count)
        }
    }

    func hookLab(brand: BrandGraph, topic: String) async -> [Hook] {
        let system = "You are Marque's hook engine. Generate scroll-stopping hooks in the creator's voice across the 8 signal types. Reply with ONLY a JSON array, no prose."
        let user = """
        \(brandBlock(brand))
        Topic: \(topic)
        Return ONLY a JSON array of 6 hooks, ranked strongest first.
        Each: {"text": str, "signal": one of [stakes,authority,curiosity,patternInterrupt,specificity,contrarian,narrative,callOut], "strength": int 0-100}
        """
        do {
            let txt = try await call(model: haiku, system: system, user: user, maxTokens: 1200)
            guard let data = extractJSON(txt, array: true) else { throw URLError(.cannotParseResponse) }
            let dtos = try JSONDecoder().decode([HookDTO].self, from: data)
            let hooks = dtos.map(hook).sorted { $0.strength > $1.strength }
            return hooks.isEmpty ? await fallback.hookLab(brand: brand, topic: topic) : hooks
        } catch {
            return await fallback.hookLab(brand: brand, topic: topic)
        }
    }

    func steer(script: Script, brand: BrandGraph, instruction: String) async -> Script {
        let system = "You revise a short-form video script per an instruction while preserving the creator's voice. Reply with ONLY a JSON object, no prose."
        let user = """
        \(brandBlock(brand))
        Current script:
        - hook: \(script.hook.text)
        - body: \(script.body)
        - cta: \(script.cta)
        Instruction: \(instruction)
        Return ONLY one JSON object. \(scriptSchema)
        """
        do {
            let txt = try await call(model: opus, system: system, user: user, maxTokens: 1500)
            guard let data = extractJSON(txt, array: false) else { throw URLError(.cannotParseResponse) }
            let dto = try JSONDecoder().decode(ScriptDTO.self, from: data)
            var s = script   // keep the same id so the store can replace in place
            s.hook = Hook(text: dto.hook, signal: signal(dto.hookSignal), strength: min(100, max(0, dto.predictedScore ?? script.hook.strength)))
            s.body = dto.body
            s.cta = dto.cta
            if let sp = dto.shotPlan { s.shotPlan = sp }
            if let ts = dto.targetSeconds { s.targetSeconds = ts }
            if let ps = dto.predictedScore { s.predictedScore = min(100, max(0, ps)) }
            return s
        } catch {
            return await fallback.steer(script: script, brand: brand, instruction: instruction)
        }
    }

    func teardown(for clip: Clip) async -> TeardownCard {
        let system = "You explain why a short-form clip performed, in one tight insight, and suggest a follow-up. Reply with ONLY a JSON object."
        let user = """
        Clip: format=\(clip.formatName), caption="\(clip.caption)", predicted score=\(clip.predictedScore).
        Return ONLY: {"headline": str, "detail": str, "liftPercent": int}
        """
        do {
            let txt = try await call(model: haiku, system: system, user: user, maxTokens: 500)
            guard let data = extractJSON(txt, array: false) else { throw URLError(.cannotParseResponse) }
            let dto = try JSONDecoder().decode(TeardownDTO.self, from: data)
            return TeardownCard(clipCaption: clip.caption, headline: dto.headline, detail: dto.detail,
                                liftPercent: min(100, max(0, dto.liftPercent ?? 30)))
        } catch {
            return await fallback.teardown(for: clip)
        }
    }
}
