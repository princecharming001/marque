import Foundation

// MARK: - Adapter protocols (every vendor hides behind one of these — see DECISIONS.md)
// In mock mode the implementations are deterministic and logic-shaped so the whole
// loop runs without any API keys. Swapping to live = a new conformer + a key.

protocol LLMRouting {
    func generateScripts(brand: BrandGraph, pillar: Pillar, count: Int) async -> [Script]
    func hookLab(brand: BrandGraph, topic: String) async -> [Hook]
    func steer(script: Script, brand: BrandGraph, instruction: String) async -> Script
    func teardown(for clip: Clip) async -> TeardownCard
}

protocol ClipEngineProtocol {
    func makeClips(from script: Script, formats: [String]) async -> [Clip]
    func render(clipId: UUID) async -> ClipStatus
}

protocol Publishing {
    func schedule(_ post: ScheduledPost) async -> Bool
}

protocol InsightsProviding {
    func trends(niche: String) async -> [TrendItem]
}

// MARK: - Mock LLM router (deterministic script/hook engine)

struct MockLLMRouter: LLMRouting {

    private func seed(_ s: String) -> UInt64 {
        var h: UInt64 = 1469598103934665603
        for b in s.utf8 { h = (h ^ UInt64(b)) &* 1099511628211 }
        return h
    }

    private func topic(_ brand: BrandGraph, _ pillar: Pillar) -> String {
        let t = pillar.name.lowercased()
        return t.isEmpty ? (brand.niche.isEmpty ? "your craft" : brand.niche.lowercased()) : t
    }

    private func hookText(_ signal: HookSignal, topic: String, brand: BrandGraph) -> String {
        let aud = brand.audience.isEmpty ? "creators" : brand.audience.lowercased()
        switch signal {
        case .contrarian:   return "Stop optimizing your \(topic). Here's what actually moves the needle."
        case .specificity:  return "The 3 \(topic) mistakes quietly costing you followers."
        case .curiosity:    return "Nobody talks about this part of \(topic) — but it changes everything."
        case .authority:    return "I've spent years on \(topic). Here's the one thing I'd tell my younger self."
        case .stakes:       return "If you're serious about \(topic), the first 3 seconds decide everything."
        case .patternInterrupt: return "Wait — your \(topic) isn't broken. Your hook is."
        case .narrative:    return "A year ago my \(topic) was invisible. Then I changed one thing."
        case .callOut:      return "\(aud.capitalized): you're doing \(topic) on hard mode."
        }
    }

    private func strength(_ signal: HookSignal, brand: BrandGraph, salt: UInt64) -> Int {
        var base: Int
        switch signal {
        case .contrarian, .curiosity, .specificity: base = 82
        case .authority, .stakes, .narrative: base = 74
        case .patternInterrupt, .callOut: base = 70
        }
        // brand alignment nudges
        if brand.goal == .authority && signal == .authority { base += 6 }
        if brand.goal == .audience && (signal == .contrarian || signal == .curiosity) { base += 5 }
        let jitter = Int(salt % 9) - 4
        return max(40, min(98, base + jitter))
    }

    func hookLab(brand: BrandGraph, topic: String) async -> [Hook] {
        try? await Task.sleep(nanoseconds: 500_000_000)
        let signals: [HookSignal] = [.contrarian, .curiosity, .specificity, .authority, .stakes, .narrative]
        return signals.map { sig in
            let salt = seed(topic + sig.rawValue + brand.knownFor)
            return Hook(text: hookText(sig, topic: topic, brand: brand),
                        signal: sig,
                        strength: strength(sig, brand: brand, salt: salt))
        }
        .sorted { $0.strength > $1.strength }
    }

    func generateScripts(brand: BrandGraph, pillar: Pillar, count: Int) async -> [Script] {
        try? await Task.sleep(nanoseconds: 900_000_000)
        let top = topic(brand, pillar)
        let formats = Catalog.formats
        return (0..<count).map { i in
            let fmt = formats[Int(seed(pillar.name + "\(i)") % UInt64(formats.count))]
            let signals = fmt.bestHooks + [.specificity, .narrative]
            let hooks = signals.prefix(3).enumerated().map { idx, sig -> Hook in
                let salt = seed(pillar.name + sig.rawValue + "\(i)\(idx)")
                return Hook(text: hookText(sig, topic: top, brand: brand), signal: sig,
                            strength: strength(sig, brand: brand, salt: salt))
            }.sorted { $0.strength > $1.strength }

            let body = scriptBody(brand: brand, pillar: pillar, format: fmt, topic: top)
            let cta = ctaLine(for: brand.goal)
            let shots = shotPlan(for: fmt)
            let score = min(96, (hooks.first?.strength ?? 70) - 4 + Int(seed("score\(i)") % 7))

            return Script(pillarName: pillar.name, formatId: fmt.id,
                          hook: hooks[0], altHooks: Array(hooks.dropFirst()),
                          body: body, cta: cta, shotPlan: shots,
                          targetSeconds: fmt.targetSeconds, predictedScore: score)
        }
    }

    private func scriptBody(brand: BrandGraph, pillar: Pillar, format: VideoFormat, topic: String) -> String {
        switch format.faceMode {
        case .split:
            return "Show the wrong way first — the thing most people do with \(topic). Then cut to your way and name the single difference that matters. Keep each side under 4 seconds."
        case .faceless:
            return "Voiceover over three quick visuals. Beat 1: the surprising claim about \(topic). Beat 2: the proof. Beat 3: what to do instead. Let the captions carry it."
        case .greenScreen:
            return "Stand in front of the screenshot/chart. Point at the part everyone misses about \(topic), then deliver your take in one sentence."
        default:
            return "Open on the hook — no intro. In one breath, give the core idea about \(topic), back it with a specific you've actually lived, then land the lesson. Fast cuts, no filler."
        }
    }

    private func ctaLine(for goal: Goal) -> String {
        switch goal {
        case .audience: return "Follow for more — I post this stuff every week."
        case .clients: return "If this is you, my link's in bio."
        case .authority: return "Save this for the next time you forget it."
        case .monetize: return "Comment ‘guide’ and I'll send it over."
        }
    }

    private func shotPlan(for fmt: VideoFormat) -> [String] {
        switch fmt.faceMode {
        case .split: return ["0–4s: wrong way (left)", "4–8s: right way (right)", "8s+: the one difference"]
        case .faceless: return ["Beat 1: claim (AI visual)", "Beat 2: proof (B-roll)", "Beat 3: do-this (text)"]
        case .greenScreen: return ["Key in the screenshot", "Point + react", "One-line verdict"]
        default: return ["Hook on frame 1", "Body with 1 punch-in", "CTA to camera"]
        }
    }

    func steer(script: Script, brand: BrandGraph, instruction: String) async -> Script {
        try? await Task.sleep(nanoseconds: 600_000_000)
        var s = script
        let i = instruction.lowercased()
        if i.contains("short") { s.body = String(s.body.prefix(120)); s.targetSeconds = max(10, s.targetSeconds - 6) }
        if i.contains("contrarian") { s.hook = Hook(text: hookText(.contrarian, topic: script.pillarName.lowercased(), brand: brand), signal: .contrarian, strength: min(97, script.hook.strength + 3)) }
        if i.contains("funny") { s.body = "Lean into the joke: " + s.body }
        if i.contains("vulnerable") || i.contains("personal") { s.body = "Open with a real moment you're a little embarrassed by. " + s.body }
        s.predictedScore = min(97, s.predictedScore + 2)
        return s
    }

    func teardown(for clip: Clip) async -> TeardownCard {
        try? await Task.sleep(nanoseconds: 400_000_000)
        let lift = 20 + Int(clip.predictedScore % 60)
        return TeardownCard(
            clipCaption: clip.caption,
            headline: "This one beat \(lift)% of your posts",
            detail: "The hook landed in the first 2 seconds and the format kept a visual change every few seconds. Make two more like it.",
            liftPercent: lift)
    }
}

// MARK: - Mock clip engine

struct MockClipEngine: ClipEngineProtocol {
    func makeClips(from script: Script, formats: [String]) async -> [Clip] {
        try? await Task.sleep(nanoseconds: 700_000_000)
        return formats.map { fid in
            let f = Catalog.format(fid)
            return Clip(scriptId: script.id, formatId: fid, formatName: f.name,
                        caption: script.hook.text, predictedScore: max(50, script.predictedScore - Int.random(in: 0...8)),
                        status: .rendering, seconds: f.targetSeconds)
        }
    }
    func render(clipId: UUID) async -> ClipStatus {
        try? await Task.sleep(nanoseconds: 1_200_000_000)
        return .ready
    }
}

// MARK: - Mock publisher & insights

struct MockPublisher: Publishing {
    func schedule(_ post: ScheduledPost) async -> Bool {
        try? await Task.sleep(nanoseconds: 300_000_000)
        return true
    }
}

struct MockInsights: InsightsProviding {
    func trends(niche: String) async -> [TrendItem] {
        try? await Task.sleep(nanoseconds: 300_000_000)
        let n = niche.isEmpty ? "your niche" : niche
        return [
            .init(title: "Myth-busting is spiking in \(n)", why: "Contrarian hooks are over-indexing on shares this week.", formatId: "myth-buster"),
            .init(title: "“Do this, not that” splits", why: "Side-by-side comparisons are getting high rewatch.", formatId: "do-this-not-that"),
            .init(title: "Faceless explainers", why: "AI-visual voiceovers are cheap to test and trending.", formatId: "faceless"),
        ]
    }
}
