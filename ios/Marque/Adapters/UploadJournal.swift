import Foundation

// Build 49 — the durable upload journal. The #1 remaining "stuck / lost upload" class
// after build 48 was that everything about an in-flight transfer lived in memory: the
// awaiting continuation, the byte progress, and the analyze-job payload. A kill (or a
// suspend-to-death) between the PUT landing and the create-job call threw all of it away,
// so the launch reconcile could only "silently restart from byte 0" — and it re-uploaded
// with a bare EditToggles(), dropping the creator's edit settings (resubmitFailedClip).
//
// This journal is the on-disk source of truth for every logical upload. It is written at
// every state transition (queued → compressed → task-enqueued → put-2xx → job-created) so
// that on relaunch we can: (a) re-attach a still-live background task, (b) recognize a PUT
// that already succeeded and skip straight to create-job, and (c) restart with FULL edit
// fidelity. It lives in Application Support (not tmp — must outlive the transfer, not be
// purged) and is rewritten atomically.
//
// Thread-safety: a plain final class guarded by an NSLock, matching BackgroundUploader —
// the background URLSession's delegate queue and the MainActor both touch it.

/// The truthful upload lifecycle. Replaces the old `uploading: Bool` so the card can never
/// claim "uploading" when nothing is actually moving.
enum UploadState: String, Codable {
    case queued            // journal created, nothing enqueued yet
    case compressing       // device-side transcode in progress
    case uploading         // a live PUT task exists and bytes are moving
    case waitingForNetwork // parked on connectivity loss (NWPathMonitor gates the resume)
    case retrying          // between attempts (backoff)
    case putComplete       // storage returned 2xx — bytes are durable
    case finalizing        // create-job in flight against the uploaded object
    case jobCreated        // server owns it — journal entry can be retired
    case failedRetryable   // gave up; the local take survives so "Try again" works
}

/// The analyze-job settings needed to recreate the submit with full fidelity on resume.
/// Everything here was previously lost on a kill-then-resume (resubmit passed EditToggles()).
struct UploadPayload: Codable, Hashable {
    var scriptId: String?           // nil ⇒ freestyle (isFreestyle)
    var isFreestyle: Bool
    var customInstructions: String
    var reactSourceURL: String
    var editFormat: String
    var themeId: String?
    var config: [String: String]?
    var referenceReelId: String?    // reel is re-hydrated from the feed by id when present
    var broll: Bool
    var punchIns: Bool
    var music: Bool
}

struct UploadJournalEntry: Codable, Hashable {
    var uploadId: String            // client UUID — also the server Idempotency-Key
    var placeholderId: String       // the Clip placeholder UUID string
    var sourcePath: String          // MediaStore-relative path to the raw take
    var compressedPath: String?     // absolute path under Documents/uploads/{uploadId} if compressed
    var contentType: String
    var storageKey: String?
    var publicUrl: String?
    var signedUrl: String?
    var mintedAtServerEpoch: Double? // server clock at mint (authoritative for expiry)
    var expiresIn: Double?           // seconds the signed URL is valid
    var mintedAtLocalEpoch: Double?  // local clock at mint (fallback only)
    var bytesTotal: Int64 = 0
    var bytesConfirmed: Int64 = 0
    var lastProgressEpoch: Double = 0 // wall-clock of the last byte delta (lying-state detector)
    var attemptCount: Int = 0
    var state: UploadState = .queued
    var lastErrorCode: String?
    var jobId: String?
    var payload: UploadPayload?
    var createdAtEpoch: Double = Date().timeIntervalSince1970

    /// Expiry check. A signed URL is treated as expired `margin` seconds early to avoid
    /// racing the PUT against the real deadline.
    ///
    /// Build 53 (audit A4): the old two-branch form was a NO-OP. Projecting server time
    /// forward by local elapsed — `serverAtMint + elapsed >= serverAtMint + expiresIn − margin`
    /// — algebraically cancels `serverAtMint`, so it computed exactly the local-only result.
    /// That's actually correct in spirit: an absolute clock OFFSET never affects a DURATION,
    /// so "elapsed since mint" is the right quantity regardless of device↔server skew (and it
    /// survives a reboot, unlike a monotonic uptime clock, which is why we keep wall-time
    /// elapsed for a persisted journal). Two real fixes: (1) clamp margin to at most half the
    /// TTL so a short-lived mint (e.g. a 60s test URL) isn't reported expired the instant it's
    /// created — the old 600s margin exceeded any short TTL and forced an endless re-mint loop;
    /// (2) floor elapsed at 0 so a backwards wall-clock correction can't yield a negative age.
    /// `mintedAtServerEpoch` is retained on the entry for telemetry but isn't needed here.
    func signedURLExpired(margin: Double = 600) -> Bool {
        guard let expiresIn, expiresIn > 0 else { return false }   // no TTL known → treat as valid
        guard let localAtMint = mintedAtLocalEpoch else { return false }
        let elapsed = max(0, Date().timeIntervalSince1970 - localAtMint)
        let safeMargin = min(margin, expiresIn * 0.5)
        return elapsed >= expiresIn - safeMargin
    }
}

final class UploadJournal: @unchecked Sendable {
    static let shared = UploadJournal()

    private let lock = NSLock()
    private var entries: [String: UploadJournalEntry] = [:]   // keyed by uploadId
    private let fileURL: URL

    private init() {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("uploads", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        fileURL = dir.appendingPathComponent("journal.json")
        load()
    }

    // MARK: Persistence

    private func load() {
        guard let data = try? Data(contentsOf: fileURL),
              let decoded = try? JSONDecoder().decode([UploadJournalEntry].self, from: data) else { return }
        entries = Dictionary(uniqueKeysWithValues: decoded.map { ($0.uploadId, $0) })
    }

    /// Atomic replace. Called under `lock`.
    private func persistLocked() {
        guard let data = try? JSONEncoder().encode(Array(entries.values)) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }

    // MARK: Reads

    func entry(uploadId: String) -> UploadJournalEntry? {
        lock.lock(); defer { lock.unlock() }
        return entries[uploadId]
    }

    func entry(placeholderId: String) -> UploadJournalEntry? {
        lock.lock(); defer { lock.unlock() }
        return entries.values.first { $0.placeholderId == placeholderId }
    }

    /// Entries not yet handed to the server — the reconcile sweep's work-list.
    func unfinished() -> [UploadJournalEntry] {
        lock.lock(); defer { lock.unlock() }
        return entries.values.filter { $0.state != .jobCreated }
    }

    // MARK: Writes (each persists atomically)

    @discardableResult
    func upsert(_ entry: UploadJournalEntry) -> UploadJournalEntry {
        lock.lock(); defer { lock.unlock() }
        entries[entry.uploadId] = entry
        persistLocked()
        return entry
    }

    /// Mutate an entry in place; no-op if it's gone. Returns the updated copy.
    @discardableResult
    func update(uploadId: String, _ mutate: (inout UploadJournalEntry) -> Void) -> UploadJournalEntry? {
        lock.lock(); defer { lock.unlock() }
        guard var e = entries[uploadId] else { return nil }
        mutate(&e)
        entries[uploadId] = e
        persistLocked()
        return e
    }

    func remove(uploadId: String) {
        lock.lock(); defer { lock.unlock() }
        if let e = entries.removeValue(forKey: uploadId) {
            // Best-effort cleanup of the compressed part file (raw take is managed elsewhere).
            if let cp = e.compressedPath { try? FileManager.default.removeItem(atPath: cp) }
            persistLocked()
        }
    }

    /// Build 53: wipe the whole journal (used by AppStore.resetAll / -reset) — clears every
    /// entry, its compressed part file, and the on-disk journal.
    func reset() {
        lock.lock(); defer { lock.unlock() }
        for e in entries.values {
            if let cp = e.compressedPath { try? FileManager.default.removeItem(atPath: cp) }
        }
        entries.removeAll()
        try? FileManager.default.removeItem(at: fileURL)
    }

    /// A stable per-upload scratch directory that survives relaunch (unlike tmp/), for the
    /// compressed file a background task streams from.
    static func workDir(uploadId: String) -> URL {
        let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("uploads", isDirectory: true)
            .appendingPathComponent(uploadId, isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
}
