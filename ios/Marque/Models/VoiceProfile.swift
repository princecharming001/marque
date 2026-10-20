import Foundation

// Scripts v2: the creator's own voice, learned from their reels (GET/PUT /v1/voice).
// Foundation-only on purpose: LogicTests compiles this file for macOS. Every field decodes
// leniently with a default — the backend may omit any of them, send `{}` for "no voice
// yet", or add enum values this build has never seen. A bad field never sinks the profile.

/// One phrase the creator really says, with the switch that lets them turn it off.
struct VoicePhrase: Codable, Hashable, Identifiable {
    var text: String
    var on: Bool = true
    var id: String { text.lowercased() }

    init(text: String, on: Bool = true) { self.text = text; self.on = on }

    private enum CodingKeys: String, CodingKey { case text, on }

    init(from decoder: Decoder) throws {
        // A bare string is accepted too ("phrases": ["and like"]) and reads as on.
        if let single = try? decoder.singleValueContainer(), let s = try? single.decode(String.self) {
            text = s; on = true; return
        }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        text = (try? c.decodeIfPresent(String.self, forKey: .text)) ?? ""
        on = ((try? c.decodeIfPresent(Bool.self, forKey: .on)) ?? nil) ?? true
    }
}

enum VoiceConfidence: String, Codable, Hashable {
    case low, medium, high
    /// Unknown or missing reads as low — the conservative claim.
    static func parse(_ raw: String?) -> VoiceConfidence {
        VoiceConfidence(rawValue: (raw ?? "").trimmingCharacters(in: .whitespaces).lowercased()) ?? .low
    }
}

enum VoiceSwearing: String, Codable, Hashable {
    case none, light, heavy
    static func parse(_ raw: String?) -> VoiceSwearing {
        VoiceSwearing(rawValue: (raw ?? "").trimmingCharacters(in: .whitespaces).lowercased()) ?? .none
    }
}

struct VoiceBuiltFrom: Codable, Hashable {
    var reels: Int = 0
    var transcribed: Int = 0
    var uploads: Int = 0

    init(reels: Int = 0, transcribed: Int = 0, uploads: Int = 0) {
        self.reels = reels; self.transcribed = transcribed; self.uploads = uploads
    }

    private enum CodingKeys: String, CodingKey { case reels, transcribed, uploads }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        reels = c.lenientInt(.reels) ?? 0
        transcribed = c.lenientInt(.transcribed) ?? 0
        uploads = c.lenientInt(.uploads) ?? 0
    }
}

struct VoiceProfile: Decodable, Hashable {
    var paragraph: String = ""
    var phrases: [VoicePhrase] = []
    var openers: [String] = []
    var closers: [String] = []
    var address: String = ""
    var swearing: VoiceSwearing = .none
    var typicalWords: Int = 0
    var neverSay: [String] = []
    var corrections: [String] = []
    var confidence: VoiceConfidence = .low
    var builtFrom = VoiceBuiltFrom()
    var updatedAt: String = ""
    var version: Int = 0

    init() {}

    private enum CodingKeys: String, CodingKey {
        case paragraph, phrases, openers, closers, address, swearing, confidence, corrections, version
        case typicalWords = "typical_words"
        case neverSay = "never_say"
        case builtFrom = "built_from"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        paragraph = c.lenientString(.paragraph) ?? ""
        phrases = ((try? c.decodeIfPresent([VoicePhrase].self, forKey: .phrases)) ?? nil)?
            .filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty } ?? []
        openers = c.lenientStrings(.openers)
        closers = c.lenientStrings(.closers)
        address = c.lenientString(.address) ?? ""
        swearing = VoiceSwearing.parse(c.lenientString(.swearing))
        typicalWords = c.lenientInt(.typicalWords) ?? 0
        neverSay = c.lenientStrings(.neverSay)
        corrections = c.lenientStrings(.corrections)
        confidence = VoiceConfidence.parse(c.lenientString(.confidence))
        builtFrom = ((try? c.decodeIfPresent(VoiceBuiltFrom.self, forKey: .builtFrom)) ?? nil) ?? VoiceBuiltFrom()
        updatedAt = c.lenientString(.updatedAt) ?? ""
        version = c.lenientInt(.version) ?? 0
    }

    /// True when there is nothing to show the creator (the backend's `{}` / never built).
    var isEmpty: Bool {
        paragraph.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && phrases.isEmpty && neverSay.isEmpty && corrections.isEmpty
    }
}

/// What a GET /v1/voice came back as — the screen picks its state from this.
enum VoiceFetchResult: Equatable {
    case loaded(VoiceProfile)
    case empty            // no voice yet, a mock backend, or an older backend without the route
    case failed           // offline / server error: worth a "try again"

    /// `status` is the HTTP status (0 = transport failure).
    static func from(data: Data?, status: Int) -> VoiceFetchResult {
        if status == 404 || status == 405 { return .empty }          // older backend: no route
        guard status == 200, let data else { return .failed }
        guard let env = try? JSONDecoder().decode(VoiceEnvelope.self, from: data) else { return .failed }
        if env.mode == "mock" { return .empty }
        guard let p = env.voice, !p.isEmpty else { return .empty }
        return .loaded(p)
    }
}

/// {"mode", "voice": VoiceProfile | {}} — the GET and PUT response shape.
struct VoiceEnvelope: Decodable {
    var mode: String = ""
    var voice: VoiceProfile?

    private enum CodingKeys: String, CodingKey { case mode, voice }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mode = c.lenientString(.mode) ?? ""
        voice = (try? c.decodeIfPresent(VoiceProfile.self, forKey: .voice)) ?? nil
    }
}

/// POST /v1/voice/build → {"status"}. `failed` = transport failure or an unreadable reply.
enum VoiceBuildStatus: String, Equatable {
    case started, running, done, unavailable, failed

    static func from(data: Data?) -> VoiceBuildStatus {
        guard let data,
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let raw = json["status"] as? String else { return .failed }
        return VoiceBuildStatus(rawValue: raw.lowercased()) ?? .unavailable
    }
}

/// A PUT /v1/voice change. Only the fields that are set go on the wire, so one edit never
/// overwrites another part of the profile.
struct VoiceUpdate: Equatable {
    var paragraph: String? = nil
    var phrases: [VoicePhrase]? = nil
    var neverSay: [String]? = nil
    var correction: String? = nil

    func body(creatorId: String) -> [String: Any] {
        var b: [String: Any] = ["creator_id": creatorId]
        if let paragraph { b["paragraph"] = paragraph.trimmingCharacters(in: .whitespacesAndNewlines) }
        if let phrases {
            b["phrases"] = phrases
                .filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
                .map { ["text": $0.text, "on": $0.on] as [String: Any] }
        }
        if let neverSay { b["never_say"] = VoiceUpdate.cleanList(neverSay) }
        if let correction {
            let t = correction.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty { b["correction"] = t }
        }
        return b
    }

    /// Trimmed, empties dropped, de-duplicated case-insensitively (first spelling wins).
    static func cleanList(_ items: [String]) -> [String] {
        var seen = Set<String>()
        var out: [String] = []
        for raw in items {
            let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !t.isEmpty, seen.insert(t.lowercased()).inserted else { continue }
            out.append(t)
        }
        return out
    }
}

/// Plain copy for the How-you-talk screen (kept here so the wording is tested).
enum VoiceCopy {
    static func confidenceLine(_ p: VoiceProfile) -> String {
        let n = p.builtFrom.reels
        if p.confidence == .low || n <= 0 { return "Add a few reels so this sounds like you" }
        return "Built from \(n) of your reels"
    }

    /// nil = nothing to say (done reloads the profile instead).
    static func buildLine(_ s: VoiceBuildStatus) -> String? {
        switch s {
        case .started, .running: return "Rebuilding from your reels. Check back in a few minutes."
        case .done: return nil
        case .unavailable: return "Rebuilding isn't available right now."
        case .failed: return "Couldn't start the rebuild. Try again."
        }
    }
}

// MARK: - Lenient decoding helpers (shared by the scripts-v2 wire types)

extension KeyedDecodingContainer {
    func lenientString(_ key: Key) -> String? {
        if let s = (try? decodeIfPresent(String.self, forKey: key)) ?? nil { return s }
        if let i = (try? decodeIfPresent(Int.self, forKey: key)) ?? nil { return String(i) }
        return nil
    }

    func lenientInt(_ key: Key) -> Int? {
        if let i = (try? decodeIfPresent(Int.self, forKey: key)) ?? nil { return i }
        if let d = (try? decodeIfPresent(Double.self, forKey: key)) ?? nil, d.isFinite { return Int(d.rounded()) }
        if let s = (try? decodeIfPresent(String.self, forKey: key)) ?? nil { return Int(s) }
        return nil
    }

    func lenientBool(_ key: Key) -> Bool? {
        if let b = (try? decodeIfPresent(Bool.self, forKey: key)) ?? nil { return b }
        if let i = (try? decodeIfPresent(Int.self, forKey: key)) ?? nil { return i != 0 }
        return nil
    }

    /// A string list; a non-string element drops only itself, never the whole list.
    func lenientStrings(_ key: Key) -> [String] {
        if let list = (try? decodeIfPresent([String].self, forKey: key)) ?? nil {
            return list.filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        }
        if let mixed = (try? decodeIfPresent([LenientString].self, forKey: key)) ?? nil {
            return mixed.compactMap(\.value).filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        }
        return []
    }
}

/// One list element that may or may not be a string.
struct LenientString: Decodable {
    let value: String?
    init(from decoder: Decoder) throws {
        value = (try? decoder.singleValueContainer().decode(String.self))
    }
}
