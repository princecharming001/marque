import Foundation

// Scripts v2 wire types: the hook / script / trend JSON the backend sends, with the new
// optional fields (hook label + checks, script craft metadata, trend promise + date).
// Foundation-only so LogicTests can decode real payloads. BackendClient aliases its DTOs
// to these. Rule for every new field: absent, null, or the wrong type decodes as "not
// there" — it can never fail the hook, the script, or the whole response.

// MARK: Hook extras (Hook Lab)

struct HookChecks: Codable, Hashable {
    var specific: Bool?
    var promisesPayoff: Bool?
    var inYourVoice: Bool?

    init(specific: Bool? = nil, promisesPayoff: Bool? = nil, inYourVoice: Bool? = nil) {
        self.specific = specific; self.promisesPayoff = promisesPayoff; self.inYourVoice = inYourVoice
    }

    private enum CodingKeys: String, CodingKey { case specific, promisesPayoff, inYourVoice }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        specific = c.lenientBool(.specific)
        promisesPayoff = c.lenientBool(.promisesPayoff)
        inYourVoice = c.lenientBool(.inYourVoice)
    }

    var isEmpty: Bool { specific == nil && promisesPayoff == nil && inYourVoice == nil }

    /// The three rows Hook Lab shows, in order. Unknown checks are left out.
    var items: [(label: String, passed: Bool)] {
        var out: [(String, Bool)] = []
        if let specific { out.append(("Specific", specific)) }
        if let promisesPayoff { out.append(("Promises the payoff", promisesPayoff)) }
        if let inYourVoice { out.append(("Sounds like you", inYourVoice)) }
        return out
    }
}

/// Where a hook's shape came from.
enum HookSource: String, Codable, Hashable {
    case yourReel = "your_reel"
    case niche

    /// "" / unknown → nil (render nothing).
    static func parse(_ raw: String?) -> HookSource? {
        HookSource(rawValue: (raw ?? "").trimmingCharacters(in: .whitespaces).lowercased())
    }

    var label: String {
        switch self {
        case .yourReel: return "From your reel"
        case .niche: return "From your niche"
        }
    }
}

struct HookExtras: Codable, Hashable {
    var label: String = ""        // e.g. "Starts mid-story"
    var why: String = ""
    var modeledOn: HookSource? = nil
    var frame: String = ""
    var checks: HookChecks? = nil

    init(label: String = "", why: String = "", modeledOn: HookSource? = nil,
         frame: String = "", checks: HookChecks? = nil) {
        self.label = label; self.why = why; self.modeledOn = modeledOn; self.frame = frame; self.checks = checks
    }

    private enum CodingKeys: String, CodingKey { case label, why, modeledOn, frame, checks }

    /// Reads the flat hook object (`{"text", "label", "checks", ...}`) — and our own
    /// persisted encoding, which uses the same keys.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = (c.lenientString(.label) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        why = (c.lenientString(.why) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        modeledOn = HookSource.parse(c.lenientString(.modeledOn))
        frame = (c.lenientString(.frame) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let ch = (try? c.decodeIfPresent(HookChecks.self, forKey: .checks)) ?? nil
        checks = (ch?.isEmpty ?? true) ? nil : ch
    }

    var isEmpty: Bool { label.isEmpty && why.isEmpty && modeledOn == nil && frame.isEmpty && checks == nil }

    /// nil when the hook carried none of the new fields (older backend).
    static func read(from decoder: Decoder) -> HookExtras? {
        guard let e = try? HookExtras(from: decoder), !e.isEmpty else { return nil }
        return e
    }
}

struct HookWire: Decodable {
    let text: String
    let signal: String?
    let strength: Int?
    let extras: HookExtras?

    private enum CodingKeys: String, CodingKey { case text, signal, strength }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        text = try c.decode(String.self, forKey: .text)          // still required, as before
        signal = c.lenientString(.signal)
        strength = c.lenientInt(.strength)
        extras = HookExtras.read(from: decoder)
    }
}

// MARK: Script craft metadata

struct ScriptCraft: Codable, Hashable {
    var openerFrame: String? = nil
    var register: String? = nil
    var payoff: String? = nil
    var particularSources: [String]? = nil
    var voiceConfidence: String? = nil
    var doctrineVersion: String? = nil
    var lintCodes: [String]? = nil

    init(openerFrame: String? = nil, register: String? = nil, payoff: String? = nil,
         particularSources: [String]? = nil, voiceConfidence: String? = nil,
         doctrineVersion: String? = nil, lintCodes: [String]? = nil) {
        self.openerFrame = openerFrame; self.register = register; self.payoff = payoff
        self.particularSources = particularSources; self.voiceConfidence = voiceConfidence
        self.doctrineVersion = doctrineVersion; self.lintCodes = lintCodes
    }

    private enum CodingKeys: String, CodingKey {
        case openerFrame, register, payoff, particularSources, voiceConfidence, doctrineVersion, lintCodes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func s(_ k: CodingKeys) -> String? {
            guard let v = c.lenientString(k)?.trimmingCharacters(in: .whitespacesAndNewlines), !v.isEmpty else { return nil }
            return v
        }
        func list(_ k: CodingKeys) -> [String]? {
            let l = c.lenientStrings(k)
            return l.isEmpty ? nil : l
        }
        openerFrame = s(.openerFrame); register = s(.register); payoff = s(.payoff)
        particularSources = list(.particularSources)
        voiceConfidence = s(.voiceConfidence); doctrineVersion = s(.doctrineVersion)
        lintCodes = list(.lintCodes)
    }

    var isEmpty: Bool {
        openerFrame == nil && register == nil && payoff == nil && particularSources == nil
            && voiceConfidence == nil && doctrineVersion == nil && lintCodes == nil
    }

    static func read(from decoder: Decoder) -> ScriptCraft? {
        guard let c = try? ScriptCraft(from: decoder), !c.isEmpty else { return nil }
        return c
    }
}

/// The script object every script-returning route sends. hook/body/cta stay required (same
/// contract as the old DTO); everything else is optional and lenient.
struct ScriptWire: Decodable {
    let title: String?
    let summary: String?
    let hook: String
    let hookSignal: String?
    let formatId: String?
    let body: String
    let cta: String
    let shotPlan: [String]?
    let targetSeconds: Int?
    let predictedScore: Int?
    let altHooks: [HookWire]?
    let style: String?
    let why_picked: String?     // UX-G2: optional — absent on old backends
    let pillar: String?         // the page's real pillar (feed picks); absent on old backends
    let craft: ScriptCraft?     // scripts v2 metadata; nil on old backends

    private enum CodingKeys: String, CodingKey {
        case title, summary, hook, hookSignal, formatId, body, cta, shotPlan
        case targetSeconds, predictedScore, altHooks, style, why_picked, pillar
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        hook = try c.decode(String.self, forKey: .hook)
        body = try c.decode(String.self, forKey: .body)
        cta = try c.decode(String.self, forKey: .cta)
        title = c.lenientString(.title)
        summary = c.lenientString(.summary)
        hookSignal = c.lenientString(.hookSignal)     // unknown values coerce in BackendClient.signal()
        formatId = c.lenientString(.formatId)
        // Absent or null → nil (steer keeps the old plan); present → the strings in it.
        let shotPlanMissing = (try? c.decodeNil(forKey: .shotPlan)) ?? true
        shotPlan = shotPlanMissing ? nil : c.lenientStrings(.shotPlan)
        targetSeconds = c.lenientInt(.targetSeconds)
        predictedScore = c.lenientInt(.predictedScore)
        altHooks = (try? c.decodeIfPresent([HookWire].self, forKey: .altHooks)) ?? nil
        style = c.lenientString(.style)
        why_picked = c.lenientString(.why_picked)
        pillar = c.lenientString(.pillar)
        craft = ScriptCraft.read(from: decoder)
    }
}

// MARK: Trends

/// GET /v1/trends rows and feed `trend` items.
struct TrendWire: Decodable {
    let title: String
    let why: String
    let formatId: String?
    let angleId: String?
    let promise: String?
    let velocity: String?
    let openers: [String]?
    let asOf: String?

    private enum CodingKeys: String, CodingKey {
        case title, why, formatId, angleId, promise, velocity, openers, asOf
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decode(String.self, forKey: .title)
        why = try c.decode(String.self, forKey: .why)
        func s(_ k: CodingKeys) -> String? {
            guard let v = c.lenientString(k)?.trimmingCharacters(in: .whitespacesAndNewlines), !v.isEmpty else { return nil }
            return v
        }
        formatId = s(.formatId)
        angleId = s(.angleId)
        promise = s(.promise)
        // Only the three known shapes; anything else is dropped rather than shown raw.
        velocity = s(.velocity).map { $0.lowercased() }.flatMap { ["rising", "steady", "new"].contains($0) ? $0 : nil }
        let o = c.lenientStrings(.openers)
        openers = o.isEmpty ? nil : o
        asOf = s(.asOf)
    }

    var item: TrendItem {
        TrendItem(title: title, why: why, formatId: formatId ?? "myth-buster",
                  angleId: angleId, promise: promise, velocity: velocity, openers: openers, asOf: asOf)
    }
}

// MARK: Feed feedback (POST /v1/feed/feedback)

enum VoiceVerdict: String, Codable, Hashable {
    case soundsLikeMe = "sounds_like_me"
    case notMe = "not_me"
}

enum FeedFeedback {
    /// The script fields the backend fingerprints (title|hook) and learns from.
    static func scriptDict(_ s: Script) -> [String: Any] {
        var d: [String: Any] = ["title": s.title, "hook": s.hook.text,
                                "pillar": s.pillarName, "style": s.style,
                                "formatId": s.formatId, "hookSignal": s.hook.signal.rawValue]
        if let craft = s.craft {
            if let v = craft.doctrineVersion { d["doctrineVersion"] = v }
            if let v = craft.openerFrame { d["openerFrame"] = v }
            if let v = craft.voiceConfidence { d["voiceConfidence"] = v }
        }
        return d
    }

    /// `verdict` nil leaves the key out (voice verdicts and edit diffs are not likes or
    /// dislikes of the pick itself).
    static func body(creatorId: String, niche: String, pillars: [String], script: Script,
                     verdict: String?, voiceVerdict: VoiceVerdict? = nil,
                     editDiff: EditDiff? = nil, reason: String? = nil) -> [String: Any] {
        var b: [String: Any] = ["creator_id": creatorId, "niche": niche, "pillars": pillars,
                                "script": scriptDict(script)]
        if let verdict { b["verdict"] = verdict }
        if let voiceVerdict { b["voice_verdict"] = voiceVerdict.rawValue }
        if let editDiff {
            b["edit_diff"] = ["before": editDiff.before, "after": editDiff.after, "field": editDiff.field]
        }
        if let reason, !reason.isEmpty { b["reason"] = reason }
        return b
    }
}
