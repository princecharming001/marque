import Foundation
import UIKit
import Network

// Build 49 — reliable large-file uploads on a BACKGROUND URLSession, hardened per Apple's
// documented background-transfer contract (Downloading Files in the Background; Quinn/DTS).
//
// Root cause of "gets stuck on upload": before build 48 the PUT ran on an in-memory
// `.default` session that the OS suspended ~30s after backgrounding. Build 48 moved it to a
// background session, but relied on `timeoutIntervalForRequest=90` for stall fast-fail —
// which Apple confirms is IGNORED for background sessions (forums thread 70682): the system
// silently retries idempotent requests on its own schedule instead of failing. So build 48
// had NO real stall detection. Build 49 fixes the model properly:
//
//  • `timeoutIntervalForResource` (20 min) is the only system-enforced ceiling for the
//    whole transfer — the real backstop.
//  • A FOREGROUND-ONLY watchdog does the fast-fail: if bytes stop moving for 60s while the
//    app is active AND the network is satisfied, cancel the task so the caller can retry.
//    Backgrounded, we do nothing — the system owns transport retries there, and an app-side
//    cancel/retry would double-retry and inflate the relaunch rate-limiter delay.
//  • Every task carries its uploadId in `taskDescription`, which the OS persists across a
//    kill. On relaunch we recreate the same-identifier session, `getAllTasks` re-binds live
//    transfers, and `didCompleteWithError` updates the JOURNAL (not just an in-memory
//    continuation) — so a PUT that finished while the app was dead is recognized, not lost.
//  • `handleEventsForBackgroundURLSession` is wired (PushManager) so the system delivers
//    completion events after relaunching us in the background.
struct UploadResult {
    let ok: Bool          // true iff a 2xx landed
    let statusCode: Int   // HTTP status (0 on transport error)
    let error: Error?
}

final class BackgroundUploader: NSObject, URLSessionDataDelegate {
    static let shared = BackgroundUploader()

    static let sessionIdentifier = "com.getmarque.bgupload"

    private var session: URLSession!
    private let lock = NSLock()
    // Per-live-task callback state, keyed by taskIdentifier (valid only within this process).
    private var conts: [Int: CheckedContinuation<UploadResult, Never>] = [:]
    private var progress: [Int: @Sendable (Double) -> Void] = [:]
    private var uploadIds: [Int: String] = [:]
    private var lastProgressAt: [Int: Date] = [:]

    // App-lifecycle flag read by the watchdog (kept off the main thread's hot path).
    private let appActive = NSLock()
    private var _appIsActive = true
    // System's stored completion handler for background-session relaunch events.
    private var systemCompletionHandler: (() -> Void)?
    // Shared connectivity signal for the watchdog + retry gating.
    private let pathMonitor = NWPathMonitor()
    private var _netSatisfied = true

    private override init() {
        super.init()
        let cfg = URLSessionConfiguration.background(withIdentifier: Self.sessionIdentifier)
        cfg.isDiscretionary = false                 // upload NOW — don't defer for wifi/charging
        cfg.sessionSendsLaunchEvents = true         // relaunch us to deliver completion
        cfg.allowsCellularAccess = true
        cfg.waitsForConnectivity = true             // ride out short drops instead of failing
        // NOTE: timeoutIntervalForRequest is intentionally NOT set — Apple ignores it for
        // background sessions. The resource timeout is the real ceiling; the app-side
        // watchdog below does foreground fast-fail.
        cfg.timeoutIntervalForResource = 20 * 60
        cfg.httpMaximumConnectionsPerHost = 4
        session = URLSession(configuration: cfg, delegate: self, delegateQueue: nil)

        NotificationCenter.default.addObserver(self, selector: #selector(didBecomeActive),
                                               name: UIApplication.didBecomeActiveNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(didEnterBackground),
                                               name: UIApplication.didEnterBackgroundNotification, object: nil)
        pathMonitor.pathUpdateHandler = { [weak self] p in
            self?.appActive.lock(); self?._netSatisfied = (p.status == .satisfied); self?.appActive.unlock()
        }
        pathMonitor.start(queue: DispatchQueue(label: "marque.bgupload.path"))
    }

    @objc private func didBecomeActive() { appActive.lock(); _appIsActive = true; appActive.unlock() }
    @objc private func didEnterBackground() { appActive.lock(); _appIsActive = false; appActive.unlock() }

    private var appIsActive: Bool { appActive.lock(); defer { appActive.unlock() }; return _appIsActive }
    var networkSatisfied: Bool { appActive.lock(); defer { appActive.unlock() }; return _netSatisfied }

    /// Store the system's completion handler (from the app delegate) and force the session
    /// to exist so its delegate can drain the pending events.
    func setSystemCompletionHandler(_ handler: @escaping () -> Void) {
        lock.lock(); systemCompletionHandler = handler; lock.unlock()
        _ = session   // ensure the same-identifier session is instantiated
    }

    /// Re-bind any transfers the system kept alive across a relaunch. Returns the set of
    /// uploadIds that are still live so the reconcile sweep won't restart them from byte 0.
    func liveUploadIds() async -> Set<String> {
        let tasks = await session.allTasks
        var ids = Set<String>()
        for t in tasks {
            if let uid = t.taskDescription, !uid.isEmpty,
               t.state == .running || t.state == .suspended {
                lock.lock(); uploadIds[t.taskIdentifier] = uid; lock.unlock()
                ids.insert(uid)
            }
        }
        return ids
    }

    /// PUT `fileURL` to `urlString` on the background session. Returns a classified result so
    /// the caller's retry policy can decide. Honors Task cancellation (cancels the transfer).
    /// The upload's byte progress + terminal state are also mirrored into the journal so a
    /// relaunch can pick up where this left off.
    func upload(uploadId: String, fileURL: URL, contentType: String, to urlString: String,
                onProgress: (@Sendable (Double) -> Void)?) async -> UploadResult {
        guard let url = URL(string: urlString) else {
            return UploadResult(ok: false, statusCode: 400, error: nil)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "PUT"
        req.setValue(contentType, forHTTPHeaderField: "Content-Type")
        let task = session.uploadTask(with: req, fromFile: fileURL)
        task.taskDescription = uploadId    // survives a kill → relaunch re-binding
        if let total = try? FileManager.default.attributesOfItem(atPath: fileURL.path)[.size] as? Int, total > 0 {
            task.countOfBytesClientExpectsToSend = Int64(total)
        }
        let id = task.taskIdentifier
        let watchdog = startWatchdog(taskId: id, task: task)
        return await withTaskCancellationHandler {
            await withCheckedContinuation { (cont: CheckedContinuation<UploadResult, Never>) in
                lock.lock()
                conts[id] = cont
                if let onProgress { progress[id] = onProgress }
                uploadIds[id] = uploadId
                lastProgressAt[id] = Date()
                lock.unlock()
                UploadJournal.shared.update(uploadId: uploadId) { $0.state = .uploading }
                task.resume()
            }
        } onCancel: {
            watchdog.cancel()
            task.cancel()   // → didCompleteWithError(.cancelled) resumes the continuation
        }
    }

    /// Foreground-only stall watchdog: cancel a task whose bytes have frozen for 60s while the
    /// app is active and connected. Does nothing while backgrounded (the OS owns retries there).
    private func startWatchdog(taskId: Int, task: URLSessionTask) -> Task<Void, Never> {
        Task.detached { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 15 * 1_000_000_000)
                if Task.isCancelled { return }
                self.lock.lock()
                let last = self.lastProgressAt[taskId]
                let stillLive = self.conts[taskId] != nil
                self.lock.unlock()
                guard stillLive, let last else { return }
                if self.appIsActive, self.networkSatisfied,
                   Date().timeIntervalSince(last) > 60 {
                    task.cancel()   // stalled in the foreground → fast-fail into a retry
                    return
                }
            }
        }
    }

    // MARK: URLSessionDataDelegate

    // Audit (build 51): didSendBodyData fires many times/sec on fast links, and every
    // journal update is an ATOMIC FILE REWRITE — throttle the durable write to ~2/sec.
    // The in-memory progress callback stays per-event (the UI bar wants smoothness);
    // the journal only needs coarse resume points.
    private var lastJournalWrite: [Int: Date] = [:]

    func urlSession(_ s: URLSession, task: URLSessionTask,
                    didSendBodyData bytesSent: Int64, totalBytesSent: Int64,
                    totalBytesExpectedToSend total: Int64) {
        guard total > 0 else { return }
        let id = task.taskIdentifier
        lock.lock()
        let cb = progress[id]
        lastProgressAt[id] = Date()
        let uid = uploadIds[id] ?? task.taskDescription
        let lastWrite = lastJournalWrite[id]
        let shouldPersist = lastWrite.map { Date().timeIntervalSince($0) > 0.5 } ?? true
        if shouldPersist { lastJournalWrite[id] = Date() }
        lock.unlock()
        cb?(Double(totalBytesSent) / Double(total))
        if let uid, shouldPersist {
            UploadJournal.shared.update(uploadId: uid) {
                $0.bytesTotal = total
                $0.bytesConfirmed = totalBytesSent
                $0.lastProgressEpoch = Date().timeIntervalSince1970
                if $0.state == .queued || $0.state == .compressing { $0.state = .uploading }
            }
        }
    }

    func urlSession(_ s: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let id = task.taskIdentifier
        lock.lock()
        let cont = conts.removeValue(forKey: id)
        progress.removeValue(forKey: id)
        lastProgressAt.removeValue(forKey: id)
        lastJournalWrite.removeValue(forKey: id)
        let uid = uploadIds.removeValue(forKey: id) ?? task.taskDescription
        lock.unlock()

        var status = 0
        if let http = task.response as? HTTPURLResponse { status = http.statusCode }
        let ok = (error == nil) && (200..<300).contains(status)

        // Mirror the terminal state into the journal even when there's NO in-memory
        // continuation — i.e. a transfer that completed after a kill+relaunch. The reconcile
        // sweep reads this to skip straight to create-job instead of re-uploading from 0.
        if let uid {
            UploadJournal.shared.update(uploadId: uid) {
                if ok {
                    $0.state = .putComplete
                    $0.lastErrorCode = nil
                } else if $0.state == .uploading {
                    // Leave retry/fail classification to the caller when it's live; for an
                    // orphaned relaunch completion, mark it back to queued so reconcile retries.
                    if cont == nil { $0.state = .queued }
                    $0.lastErrorCode = (error as NSError?).map { "\($0.domain):\($0.code)" } ?? "http_\(status)"
                }
            }
        }
        cont?.resume(returning: UploadResult(ok: ok, statusCode: status, error: error))
    }

    /// Called after the system relaunched us to deliver background events; fire the stored
    /// completion handler on the main queue per Apple's contract.
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        lock.lock(); let h = systemCompletionHandler; systemCompletionHandler = nil; lock.unlock()
        if let h { DispatchQueue.main.async(execute: h) }
    }
}
