import Foundation

// MARK: - Editor draft persistence (ED-2) + network outcome classification (ED-1/ED-11).
//
// Foundation-only on purpose: ios/LogicTests/editor/run.sh compiles this file (with
// EditorModel/LocalEDLEngine/LayoutConstants/EditorSession) into a macOS runner.
//
// The draft is the session's op log — one entry per committed gesture — stamped with a
// fingerprint of the server EDL the ops were made against. On the next load, the ops are
// replayed through LocalEDLEngine ONLY when the freshly fetched EDL is still that base;
// any other base means the ops' indices/frames may target a different document, so the
// draft is dropped rather than risk a wrong edit.

// MARK: Codable op log

extension EditorCaption: Codable {
    private enum CodingKeys: String, CodingKey { case word, frame }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(word: try c.decode(String.self, forKey: .word),
                  frame: try c.decode(Int.self, forKey: .frame))
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(word, forKey: .word)
        try c.encode(frame, forKey: .frame)
    }
}

/// Every field round-trips, including the LOCAL-ONLY captionSeed (never part of the wire
/// JSON) — a restored "captions on" gesture must seed the preview exactly as it did live.
/// Absent keys decode to their defaults so older drafts stay readable.
extension WireOp: Codable {
    private enum CodingKeys: String, CodingKey { case type, i, d, s, order, bool, strings, captionSeed }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.init(type: try c.decode(String.self, forKey: .type))
        i = try c.decodeIfPresent([String: Int].self, forKey: .i) ?? [:]
        d = try c.decodeIfPresent([String: Double].self, forKey: .d) ?? [:]
        s = try c.decodeIfPresent([String: String].self, forKey: .s) ?? [:]
        order = try c.decodeIfPresent([Int].self, forKey: .order)
        bool = try c.decodeIfPresent([String: Bool].self, forKey: .bool) ?? [:]
        strings = try c.decodeIfPresent([String: [String]].self, forKey: .strings) ?? [:]
        captionSeed = try c.decodeIfPresent([EditorCaption].self, forKey: .captionSeed)
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        if !i.isEmpty { try c.encode(i, forKey: .i) }
        if !d.isEmpty { try c.encode(d, forKey: .d) }
        if !s.isEmpty { try c.encode(s, forKey: .s) }
        try c.encodeIfPresent(order, forKey: .order)
        if !bool.isEmpty { try c.encode(bool, forKey: .bool) }
        if !strings.isEmpty { try c.encode(strings, forKey: .strings) }
        try c.encodeIfPresent(captionSeed, forKey: .captionSeed)
    }
}

// MARK: Base fingerprint

/// Identity of the server EDL a draft's ops were made against.
/// `full` hashes the whole fetched EDL; `structure` hashes it minus the fields a retheme
/// restamps (backend app/themes.py apply_theme: theme_id, caption_style, caption_options,
/// look, audio.duck). A retheme never touches segments/drops/overlays/broll, so every op
/// index and frame stays valid across it — that is the ONE base change a draft may
/// survive, and only when the user explicitly chose to keep their edits through it.
struct EditorDraftBase: Codable, Equatable {
    var full: String
    var structure: String

    static let themeOwnedKeys: [String] = ["theme_id", "caption_style", "caption_options", "look"]

    init(full: String, structure: String) {
        self.full = full
        self.structure = structure
    }

    init(edl: [String: Any]) {
        full = Self.fingerprint(edl)
        var stripped = edl
        for k in Self.themeOwnedKeys { stripped.removeValue(forKey: k) }
        if var audio = stripped["audio"] as? [String: Any] {
            audio.removeValue(forKey: "duck")
            stripped["audio"] = audio
        }
        structure = Self.fingerprint(stripped)
    }

    /// FNV-1a 64 over a canonical (sorted-keys) re-serialization. The same parsed EDL always
    /// serializes to the same bytes, so two GETs of an unchanged job agree.
    static func fingerprint(_ object: [String: Any]) -> String {
        guard JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        else { return "invalid" }
        var h: UInt64 = 0xcbf29ce484222325
        for b in data { h = (h ^ UInt64(b)) &* 0x100000001b3 }
        return "\(data.count)-" + String(h, radix: 16)
    }
}

// MARK: The draft document

struct EditorDraft: Codable, Equatable {
    static let currentVersion = 1

    var version: Int = EditorDraft.currentVersion
    var jobId: String
    var base: EditorDraftBase
    /// Set when the user applied a theme and chose to keep their unsaved edits: the draft
    /// may then be replayed on the rethemed EDL (same `structure` fingerprint).
    var carryAcrossRetheme: Bool = false
    /// One entry per committed gesture (EditorSession.opLog) — each replays as one undo step.
    var gestures: [[WireOp]]
    var savedAt: Date = Date()

    enum Decision: Equatable { case none, restore, drop }

    /// Restore only onto the exact base the ops were made against (or, after a kept-edits
    /// retheme, onto the same structure). Anything else is dropped.
    static func decide(_ draft: EditorDraft?, against base: EditorDraftBase) -> Decision {
        guard let draft else { return .none }
        guard draft.version == currentVersion, !draft.gestures.isEmpty else { return .drop }
        if draft.base.full == base.full { return .restore }
        if draft.carryAcrossRetheme, draft.base.structure == base.structure { return .restore }
        return .drop
    }
}

// MARK: On-disk store — Application Support/editor-drafts/<jobId>.json

enum EditorDraftStore {
    static var defaultDirectory: URL {
        let root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return root.appendingPathComponent("editor-drafts", isDirectory: true)
    }

    static func fileURL(jobId: String, in dir: URL) -> URL {
        let safe = String(jobId.unicodeScalars.map { s -> Character in
            CharacterSet.alphanumerics.contains(s) || s == "-" || s == "_" ? Character(s) : "_"
        }.prefix(120))
        return dir.appendingPathComponent((safe.isEmpty ? "job" : safe) + ".json")
    }

    @discardableResult
    static func save(_ draft: EditorDraft, in dir: URL = defaultDirectory) -> Bool {
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let data = try JSONEncoder().encode(draft)
            try data.write(to: fileURL(jobId: draft.jobId, in: dir), options: .atomic)
            return true
        } catch {
            return false
        }
    }

    static func load(jobId: String, in dir: URL = defaultDirectory) -> EditorDraft? {
        guard let data = try? Data(contentsOf: fileURL(jobId: jobId, in: dir)) else { return nil }
        return try? JSONDecoder().decode(EditorDraft.self, from: data)
    }

    static func clear(jobId: String, in dir: URL = defaultDirectory) {
        try? FileManager.default.removeItem(at: fileURL(jobId: jobId, in: dir))
    }

    /// Drafts for jobs never reopened would otherwise pile up (server jobs expire in 24h).
    static func prune(olderThan age: TimeInterval = 14 * 24 * 3600, in dir: URL = defaultDirectory,
                      now: Date = Date()) {
        guard let files = try? FileManager.default.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: [.contentModificationDateKey]) else { return }
        for f in files where f.pathExtension == "json" {
            let modified = (try? f.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate ?? now
            if now.timeIntervalSince(modified) > age { try? FileManager.default.removeItem(at: f) }
        }
    }
}

// MARK: Autosaver — debounced writes on every committed gesture, flushed on background.

@MainActor
final class EditorDraftAutosaver {
    let jobId: String
    let base: EditorDraftBase
    private let directory: URL
    private let debounce: TimeInterval
    private(set) var carryAcrossRetheme = false
    private var pending: [[WireOp]]?
    private var work: DispatchWorkItem?
    /// Discarded or successfully saved: the draft is gone and must never be rewritten.
    private(set) var closed = false

    init(jobId: String, base: EditorDraftBase, directory: URL = EditorDraftStore.defaultDirectory,
         debounce: TimeInterval = 0.6) {
        self.jobId = jobId
        self.base = base
        self.directory = directory
        self.debounce = debounce
    }

    /// Record the latest op log; written after `debounce` of quiet (an empty log deletes it).
    func schedule(_ gestures: [[WireOp]]) {
        guard !closed else { return }
        pending = gestures
        work?.cancel()
        let item = DispatchWorkItem { [weak self] in self?.flush() }
        work = item
        DispatchQueue.main.asyncAfter(deadline: .now() + debounce, execute: item)
    }

    /// Write any pending op log NOW (scene background, disappear, before a network save).
    func flush() {
        work?.cancel(); work = nil
        guard !closed, let gestures = pending else { return }
        pending = nil
        write(gestures)
    }

    /// The user applied a theme and kept their edits: persist immediately, flagged so the
    /// next load replays them onto the rethemed EDL (keep: false withdraws the flag when
    /// the retheme never reached the server).
    func persistForRetheme(_ gestures: [[WireOp]], keep: Bool = true) {
        guard !closed else { return }
        carryAcrossRetheme = keep
        work?.cancel(); work = nil
        pending = nil
        write(gestures)
    }

    /// Discard (user confirmed) or a successful Save: delete and stop writing.
    /// `deleting: false` just retires this saver (a reload hands the draft to a new one).
    func close(deleting: Bool = true) {
        work?.cancel(); work = nil
        pending = nil
        closed = true
        if deleting { EditorDraftStore.clear(jobId: jobId, in: directory) }
    }

    private func write(_ gestures: [[WireOp]]) {
        if gestures.isEmpty {
            EditorDraftStore.clear(jobId: jobId, in: directory)
        } else {
            EditorDraftStore.save(EditorDraft(jobId: jobId, base: base,
                                              carryAcrossRetheme: carryAcrossRetheme,
                                              gestures: gestures), in: directory)
        }
    }
}

// MARK: Network outcome classification (pure — tested in LogicTests)

/// GET /v1/clips/{id} for the editor. `status` 0 = transport failure (BackendClient).
enum EditorLoadOutcome: Equatable {
    case ready            // 200 with an EDL
    case notReady         // 200, job alive but no EDL yet (restored / still editing)
    case unreachable      // offline, timeout, 5xx, anything unexpected: worth a retry
    case gone             // 404 / 410: the session is really over

    static func classify(status: Int, hasEDL: Bool) -> EditorLoadOutcome {
        switch status {
        case 200: return hasEDL ? .ready : .notReady
        case 404, 410: return .gone
        default: return .unreachable
        }
    }
}

struct EditorSkippedOp: Equatable {
    var type: String
    var reason: String
}

struct EditorSaveResult: Equatable {
    var needsRender: Bool
    var changed: Bool
    var appliedCount: Int
    var skipped: [EditorSkippedOp]
}

/// The inline Save-failure bar's content: what happened, and whether Retry can help.
struct EditorSaveNotice: Equatable {
    var message: String
    var retryable: Bool
}

/// POST /v1/clips/{id}/tweak for the manual editor's Save.
enum EditorSaveOutcome: Equatable {
    case saved(EditorSaveResult)
    /// 409 (a render is in flight) / 503 (studio unavailable): nothing was applied.
    case busy(String)
    /// Transport failure or 5xx. `ambiguous` = the server may have applied the ops before
    /// the failure surfaced, so a retry must first confirm the base is unchanged.
    case unreachable(String, ambiguous: Bool)
    /// 404 / 410: the edit session is gone.
    case gone(String)
    /// Any other 4xx: the request itself was refused.
    case rejected(String)

    static let unreachableCopy = "Couldn't reach Yunicorn, so your edits aren't saved yet. They're safe on this phone."
    static let serverErrorCopy = "Yunicorn hit a problem saving your edits. They're safe on this phone."
    static let busyCopy = "Still rendering your last change, try again in a minute."
    static let unavailableCopy = "Couldn't reach the studio just now, try again in a moment."
    static let goneCopy = "This edit session has expired, so these edits can't be saved to it. They stay on this phone in case the session comes back."
    static let rejectedCopy = "These edits couldn't be applied. Undo the last change and save again."
    /// Retry found the server EDL changed since the editor opened: the earlier attempt most
    /// likely landed, and re-sending would apply every op a second time.
    static let baseMovedCopy = "Your clip changed on the server after that attempt, so your edits weren't sent again. They may already be saved, check your Library."

    /// The inline bar for a failed attempt (nil for a successful save).
    var notice: EditorSaveNotice? {
        switch self {
        case .saved: return nil
        case .busy(let m): return EditorSaveNotice(message: m, retryable: true)
        case .unreachable(let m, _): return EditorSaveNotice(message: m, retryable: true)
        case .gone(let m), .rejected(let m): return EditorSaveNotice(message: m, retryable: false)
        }
    }

    static func classify(status: Int, body: [String: Any]?) -> EditorSaveOutcome {
        switch status {
        case 200:
            guard let body else { return .unreachable(serverErrorCopy, ambiguous: true) }
            let skipped = (body["skipped"] as? [[String: Any]] ?? []).map {
                EditorSkippedOp(type: $0["type"] as? String ?? "", reason: $0["reason"] as? String ?? "")
            }
            let applied = (body["applied"] as? [Any])?.count ?? 0
            return .saved(EditorSaveResult(needsRender: body["needs_render"] as? Bool ?? false,
                                           changed: body["changed"] as? Bool ?? (applied > 0),
                                           appliedCount: applied, skipped: skipped))
        case 0:
            return .unreachable(unreachableCopy, ambiguous: true)
        case 409:
            return .busy(busyCopy)
        case 503:
            return .busy(unavailableCopy)
        case 404, 410:
            return .gone(goneCopy)
        case 408, 429:
            return .unreachable(unreachableCopy, ambiguous: false)
        case 500...599:
            return .unreachable(serverErrorCopy, ambiguous: true)
        default:
            return .rejected(rejectedCopy)
        }
    }
}
