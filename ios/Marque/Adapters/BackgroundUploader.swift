import Foundation

// Build 48 — reliable large-file uploads via a BACKGROUND URLSession.
//
// "Gets stuck on upload a lot" root cause: an in-memory `.default` URLSession task is
// suspended by the OS ~30s after the app is backgrounded (that's all UIKit's
// beginBackgroundTask grants). A real take is tens-to-hundreds of MB and on cellular
// takes minutes — so switching apps mid-upload wedges the transfer until foreground,
// and often never recovers cleanly. A BACKGROUND session is the iOS-sanctioned fix: its
// upload task keeps running OUT-OF-PROCESS while the app is suspended, has its own
// stall/connectivity handling, and the delegate fires when it finishes (even after the
// app returns to the foreground). This makes the transfer robust to app-switching,
// screen-lock, and brief connectivity drops — the recurring "stuck" cases.
//
// The session also fast-fails a genuinely wedged upload: timeoutIntervalForRequest is
// the IDLE timeout between data packets, so a connection that stops sending bytes errors
// out in ~90s (→ the caller retries, then reconciles to a retryable card) instead of
// sitting at "uploading" forever.
final class BackgroundUploader: NSObject, URLSessionDataDelegate {
    static let shared = BackgroundUploader()

    private var session: URLSession!
    private let lock = NSLock()
    private var conts: [Int: CheckedContinuation<Bool, Never>] = [:]
    private var progress: [Int: @Sendable (Double) -> Void] = [:]

    private override init() {
        super.init()
        let cfg = URLSessionConfiguration.background(withIdentifier: "com.getmarque.bgupload")
        cfg.isDiscretionary = false                 // upload NOW — don't defer for wifi/charging
        cfg.sessionSendsLaunchEvents = true
        cfg.allowsCellularAccess = true
        cfg.timeoutIntervalForRequest = 90          // idle/stall timeout between packets → fast-fail
        cfg.timeoutIntervalForResource = 30 * 60    // hard ceiling for the whole transfer
        session = URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
    }

    /// PUT `fileURL` to the presigned `urlString`. Returns true only on a 2xx. Awaits the
    /// out-of-process transfer; honors Task cancellation (cancels the underlying task so
    /// the caller's outer ceiling never leaves a leaked continuation).
    func upload(fileURL: URL, to urlString: String,
                onProgress: (@Sendable (Double) -> Void)?) async -> Bool {
        guard let url = URL(string: urlString) else { return false }
        var req = URLRequest(url: url)
        req.httpMethod = "PUT"
        req.setValue("video/quicktime", forHTTPHeaderField: "Content-Type")
        let task = session.uploadTask(with: req, fromFile: fileURL)
        let id = task.taskIdentifier
        return await withTaskCancellationHandler {
            await withCheckedContinuation { (cont: CheckedContinuation<Bool, Never>) in
                lock.lock()
                conts[id] = cont
                if let onProgress { progress[id] = onProgress }
                lock.unlock()
                task.resume()
            }
        } onCancel: {
            task.cancel()   // → didCompleteWithError(.cancelled) resumes the continuation false
        }
    }

    func urlSession(_ s: URLSession, task: URLSessionTask,
                    didSendBodyData bytesSent: Int64, totalBytesSent: Int64,
                    totalBytesExpectedToSend total: Int64) {
        guard total > 0 else { return }
        lock.lock(); let cb = progress[task.taskIdentifier]; lock.unlock()
        cb?(Double(totalBytesSent) / Double(total))
    }

    func urlSession(_ s: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let id = task.taskIdentifier
        lock.lock()
        let cont = conts.removeValue(forKey: id)
        progress.removeValue(forKey: id)
        lock.unlock()
        var ok = (error == nil)
        if let http = task.response as? HTTPURLResponse {
            ok = ok && (200..<300).contains(http.statusCode)
        }
        cont?.resume(returning: ok)
    }
}
