import Foundation

// MARK: - Adapter protocols (every vendor hides behind one of these — see DECISIONS.md)
// In mock mode the implementations are deterministic and logic-shaped so the whole
// loop runs without any API keys. Swapping to live = a new conformer + a key.

protocol LLMRouting {
    func generatePillars(brand: BrandGraph) async -> [Pillar]
    func generateScripts(brand: BrandGraph, pillar: Pillar, count: Int, mediaContext: String, style: VideoStyle, memory: CreatorMemory) async -> [Script]
    func hookLab(brand: BrandGraph, topic: String, memory: CreatorMemory) async -> [Hook]
    func steer(script: Script, brand: BrandGraph, instruction: String) async -> Script
    func captions(for script: Script) async -> [String]
    func teardown(for clip: Clip) async -> TeardownCard
    func interpretInsights(brand: BrandGraph, summary: String) async -> String
}

extension LLMRouting {
    // Back-compat convenience overloads — callers without creator memory still compile.
    func generateScripts(brand: BrandGraph, pillar: Pillar, count: Int, mediaContext: String, style: VideoStyle) async -> [Script] {
        await generateScripts(brand: brand, pillar: pillar, count: count, mediaContext: mediaContext, style: style, memory: CreatorMemory())
    }
    func hookLab(brand: BrandGraph, topic: String) async -> [Hook] {
        await hookLab(brand: brand, topic: topic, memory: CreatorMemory())
    }
}

protocol ClipEngineProtocol {
    // reactSourceURL is the reacted-to clip for a duet_split render (empty otherwise).
    // footagePath is the local (Documents-relative) path to the recorded take; the
    // live engine uploads it to storage so the backend can actually fetch it to
    // transcribe + render. Without an upload the source URL points at nothing.
    func makeClips(from script: Script, formats: [String], reactSourceURL: String, footagePath: String?) async -> [Clip]
    func render(clipId: UUID) async -> ClipStatus
}

extension ClipEngineProtocol {
    func makeClips(from script: Script, formats: [String]) async -> [Clip] {
        await makeClips(from: script, formats: formats, reactSourceURL: "", footagePath: nil)
    }
    // Back-compat convenience for callers that don't have footage (kept so existing
    // 3-arg call sites still compile).
    func makeClips(from script: Script, formats: [String], reactSourceURL: String) async -> [Clip] {
        await makeClips(from: script, formats: formats, reactSourceURL: reactSourceURL, footagePath: nil)
    }
}

protocol Publishing {
    /// `accountIds` are the Post for Me account ids (spc_…) to publish to. Empty means
    /// no OAuth-linked account for the chosen platforms → the backend degrades to mock.
    func schedule(_ post: ScheduledPost, accountIds: [String]) async -> PublishOutcome
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
        // The niche is the subject; the pillar supplies the angle, not the topic noun.
        if !brand.niche.isEmpty { return brand.niche.lowercased() }
        let t = pillar.name.lowercased()
        return t.isEmpty ? "your craft" : t
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

    func hookLab(brand: BrandGraph, topic: String, memory: CreatorMemory = CreatorMemory()) async -> [Hook] {
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

    func generateScripts(brand: BrandGraph, pillar: Pillar, count: Int, mediaContext: String, style: VideoStyle, memory: CreatorMemory = CreatorMemory()) async -> [Script] {
        try? await Task.sleep(nanoseconds: 900_000_000)
        let top = topic(brand, pillar)
        let formats = style.formats.map { Catalog.format($0) }
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
            var shots = shotPlan(for: fmt)
            if !mediaContext.isEmpty { shots.append("Reuse your reference footage — \(mediaContext)") }
            let score = min(96, (hooks.first?.strength ?? 70) - 4 + Int(seed("score\(i)") % 7))
            let title = pillar.exampleTopics.isEmpty
                ? "\(fmt.name): \(top)"
                : pillar.exampleTopics[i % pillar.exampleTopics.count]
            let summary = "A \(fmt.targetSeconds)s \(fmt.name.lowercased()) — \(pillar.summary.isEmpty ? "on \(top)" : pillar.summary.lowercased())"

            return Script(pillarName: pillar.name, title: title, summary: summary, style: style.rawValue, formatId: fmt.id,
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
        return TeardownCard(
            clipCaption: clip.caption,
            headline: "Why this one's built to land",
            detail: "The hook lands in the first 2 seconds and the format keeps a visual change every few seconds. Make two more like it.",
            liftPercent: 0)
    }

    // Niche-specific content pillars derived from the creator's actual brand (NOT a static list).
    func generatePillars(brand: BrandGraph) async -> [Pillar] {
        try? await Task.sleep(nanoseconds: 700_000_000)
        let niche = brand.niche.isEmpty ? "your field" : brand.niche
        let aud = brand.audience.isEmpty ? "your audience" : brand.audience.lowercased()
        let known = brand.knownFor.isEmpty ? niche : brand.knownFor
        let what = brand.whatYouDo.isEmpty ? "what you do" : brand.whatYouDo.lowercased()

        var seeds: [(name: String, summary: String, angle: String, topics: [String])] = [
            ("Teach the fundamentals",
             "Bite-size lessons that make \(aud) better at \(niche).",
             "You break \(known) into steps \(aud) can copy today — no fluff, no gatekeeping.",
             ["The \(niche) mistake most beginners make",
              "A 60-second framework for \(known.lowercased())",
              "What I wish I knew about \(niche) on day one"]),
            ("Myth-busting",
             "Contrarian takes that fix what \(aud) get wrong about \(niche).",
             "You call out popular \(niche) advice that quietly backfires — and show what works instead.",
             ["The \(niche) advice hurting \(aud) most",
              "“Everyone says this about \(niche)” — why it's wrong",
              "Stop doing this one thing in \(niche)"]),
            ("Behind the scenes",
             "The real, unpolished story of \(what).",
             "You let \(aud) watch how the work actually happens — the messy middle, not the highlight reel.",
             ["A day in the life of \(what)",
              "The part of \(niche) nobody shows you",
              "How I actually \(known.lowercased())"]),
            ("Hot takes",
             "Sharp opinions that start conversations in \(niche).",
             "You stake a clear position \(aud) will want to share or argue with.",
             ["My most controversial \(niche) opinion",
              "An unpopular truth about \(niche)",
              "Why most \(aud) are wrong about \(known.lowercased())"]),
            ("Proof & results",
             "Receipts: transformations, case studies and before/afters in \(niche).",
             "You show concrete outcomes so \(aud) trust the method, not the talk.",
             ["A before/after that proves \(known.lowercased()) works",
              "The result that changed how I see \(niche)",
              "Walk through a real \(niche) win, step by step"]),
        ]
        if brand.goal == .clients || brand.goal == .monetize { seeds.swapAt(0, 4) }
        else if brand.goal == .authority { seeds.swapAt(0, 1) }

        let colors = Catalog.pillarColors
        return seeds.prefix(5).enumerated().map { i, s in
            Pillar(name: s.name, summary: s.summary, angle: s.angle, exampleTopics: s.topics,
                   weight: 1.0 / 5.0, colorHex: colors[i % colors.count])
        }
    }

    // On-screen / burned-in caption lines (~5 words each) from the hook + body.
    func captions(for script: Script) async -> [String] {
        try? await Task.sleep(nanoseconds: 200_000_000)
        let sentences = ([script.hook.text] +
            script.body.split(whereSeparator: { ".!?".contains($0) })
                .map { $0.trimmingCharacters(in: .whitespaces) })
            .filter { !$0.isEmpty }
        return sentences.flatMap { chunk -> [String] in
            let words = chunk.split(separator: " ")
            var lines: [String] = []; var cur: [Substring] = []
            for w in words { cur.append(w); if cur.count >= 5 { lines.append(cur.joined(separator: " ")); cur = [] } }
            if !cur.isEmpty { lines.append(cur.joined(separator: " ")) }
            return lines
        }
    }

    func interpretInsights(brand: BrandGraph, summary: String) async -> String {
        try? await Task.sleep(nanoseconds: 300_000_000)
        return "Your contrarian hooks are outperforming. Lean into myth-busting this week, and make two more in whichever format spiked."
    }

    // MARK: - V3: Conversation / mimic / video-analysis fallbacks (not part of LLMRouting —
    // BackendClient calls these directly so Chat/Home never dead-end offline, mirroring the
    // backend's own mock_converse / _mock_mimic tone.)

    func converse(mode: String, messages: [ChatMessage], brand: BrandGraph,
                  memory: CreatorMemory) async -> BackendClient.ConverseResult {
        try? await Task.sleep(nanoseconds: 500_000_000)
        let lastUser = messages.last(where: { $0.role == .user })?.content ?? ""
        let lower = lastUser.lowercased()

        var updates: [MemoryUpdate] = []
        var intent = "none"
        var scripts: [Script]? = nil
        var plan: DayPlan? = nil
        var reply: String
        var chips = ["Write me a script", "Build my day", "What should I post today?"]

        if lower.contains("plan") || lower.contains("day") || lower.contains("schedule") {
            intent = "day_plan"
            plan = DayPlan(blocks: [
                DayPlanBlock(time: "Morning", action: "Film", detail: "Record today's script while your energy's fresh."),
                DayPlanBlock(time: "Afternoon", action: "Edit", detail: "Submit for AI editing — captions and trim happen automatically."),
                DayPlanBlock(time: "Evening", action: "Post", detail: "Schedule for your best posting window."),
            ])
            reply = mode == "voice"
                ? "Here's your day: film this morning while you're fresh, submit for editing this afternoon, and schedule tonight."
                : "Here's a simple shape for today:\n\n1. **Film** this morning while you're fresh.\n2. **Submit for editing** — captions and trim happen automatically.\n3. **Schedule** for your best posting window."
            chips = ["Show me a script", "Change the plan", "What's my best time to post?"]
        } else if lower.contains("script") || lower.contains("write") || lower.contains("idea") || lower.contains("post today") {
            intent = "generate_scripts"
            let pillar = Pillar(name: "From chat", summary: "", angle: "",
                                exampleTopics: [], weight: 1, colorHex: Catalog.pillarColors[0])
            let style = brand.preferredStyles.first ?? .talkingHead
            scripts = await generateScripts(brand: brand, pillar: pillar, count: 1, mediaContext: "", style: style)
            let top = topic(brand, pillar)
            reply = mode == "voice"
                ? "Wrote you one on \(top) — check your queue when you're ready to film."
                : "Wrote you a script on \(top). It's saved to your Film queue whenever you're ready."
            chips = ["Give me another", "Make it shorter", "Build my day"]
        } else {
            reply = mode == "voice"
                ? "Got it — noted. Tell me more whenever something's on your mind, and I'll fold it into your scripts."
                : "Got it — noted. The more you tell me like this, the sharper your scripts get. Anything you want me to turn into a post?"
            if !lastUser.isEmpty {
                let trimmed = lastUser.count > 140 ? String(lastUser.prefix(140)) + "…" : lastUser
                updates.append(MemoryUpdate(op: "add", field: "ideas", value: trimmed))
            }
        }

        return BackendClient.ConverseResult(mode: "mock", reply: reply, memoryUpdates: updates,
                                            intent: intent, scripts: scripts, plan: plan, chips: chips)
    }

    func mimic(reelItem: ReelItem, brand: BrandGraph, memory: CreatorMemory) async -> (script: Script, from: String) {
        try? await Task.sleep(nanoseconds: 700_000_000)
        let niche = brand.niche.isEmpty ? "your niche" : brand.niche
        let style = VideoStyle(rawValue: reelItem.style) ?? .talkingHead
        let fmt = Catalog.format(reelItem.formatId)
        let salt = seed(reelItem.id + "mimic")
        let hook = Hook(text: "Everyone in \(niche) gets this wrong — here's the fix.",
                        signal: .contrarian, strength: strength(.contrarian, brand: brand, salt: salt))
        let body = "Same skeleton as @\(reelItem.creatorHandle)'s take, your substance: open on the boldest claim you can defend about \(niche). Walk the same beats — but every example, number, and story is yours."
        let s = Script(pillarName: "Mimic: @\(reelItem.creatorHandle)",
                       title: "Your version of @\(reelItem.creatorHandle)'s hit",
                       summary: "Same structure, your \(niche) substance.",
                       style: style.rawValue, formatId: fmt.id,
                       hook: hook, altHooks: [], body: body, cta: ctaLine(for: brand.goal),
                       shotPlan: shotPlan(for: fmt), targetSeconds: fmt.targetSeconds,
                       predictedScore: max(60, min(95, 74 + Int(salt % 12))))
        return (s, "@\(reelItem.creatorHandle)")
    }

    func analyzeVideo(url: String, brand: BrandGraph, memory: CreatorMemory) async -> VideoAnalysis {
        try? await Task.sleep(nanoseconds: 600_000_000)
        let niche = brand.niche.isEmpty ? "your niche" : brand.niche
        let platform = url.contains("tiktok") ? "tiktok" : url.contains("instagram") ? "instagram" : "video"
        let placeholder = ReelItem(id: "link", creatorHandle: "this video", platform: platform,
                                   title: "", hookText: "", transcript: "",
                                   formatId: "myth-buster", style: "talking_head")
        let (version, _) = await mimic(reelItem: placeholder, brand: brand, memory: memory)
        return VideoAnalysis(
            url: url, platform: platform,
            transcript: "Hook: a bold claim delivered in the first second, mirrored in on-screen text. Beat 2: the creator stakes credibility with one specific number. Beat 3: quick visual proof — the pattern is shown, not described. Beat 4: the reframe — why everyone reads this wrong. Close: a single takeaway line and a one-word comment prompt.",
            hookAnalysis: "The hook lands a bold claim inside the first second and mirrors it in on-screen text — a double pattern-interrupt that stops both sound-on and sound-off scrollers.",
            structureBeats: [
                "Bold claim + on-screen text (0–1s)",
                "One specific number for credibility",
                "Visual proof, not narration",
                "The reframe — why everyone reads this wrong",
                "One-line takeaway + comment prompt",
            ],
            whyItWorks: "Every beat earns the next second: specificity builds trust, the proof is shown rather than told, and the loop opened in the hook only closes on the final line — which is what holds retention to the end.",
            suggestions: [
                "Reuse this skeleton for your next \(niche) post",
                "Mirror your hook in on-screen text",
                "End on a one-word comment prompt to drive replies",
            ],
            yourVersion: version)
    }
}

// MARK: - Mock clip engine

struct MockClipEngine: ClipEngineProtocol {
    func makeClips(from script: Script, formats: [String], reactSourceURL: String = "",
                   footagePath: String? = nil) async -> [Clip] {
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
    func schedule(_ post: ScheduledPost, accountIds: [String]) async -> PublishOutcome {
        try? await Task.sleep(nanoseconds: 300_000_000)
        // Honest even in the mock provider: nothing was actually posted.
        return .savedLocalNoAccounts
    }
}

struct MockInsights: InsightsProviding {
    func trends(niche: String) async -> [TrendItem] {
        try? await Task.sleep(nanoseconds: 300_000_000)
        let n = niche.isEmpty ? "your niche" : niche
        return [
            .init(title: "Myth-busting is spiking in \(n)", why: "Contrarian hooks are getting shared a lot this week.", formatId: "myth-buster"),
            .init(title: "“Do this, not that” splits", why: "Side-by-side comparisons are getting high rewatch.", formatId: "do-this-not-that"),
            .init(title: "Faceless explainers", why: "AI-visual voiceovers are cheap to test and trending.", formatId: "faceless"),
        ]
    }
}
