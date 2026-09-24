import Foundation

// LV-7 (2026-09-24) — never download a render just to throw it away.
//
// AppStore.cacheRender downloaded the WHOLE server render (URLSession.download) and only
// then compared its size with the 200 MB cache cap — so every long take's render (a
// 10-minute cut is easily past the cap) was pulled down in full, possibly on cellular,
// on every ready/poll tick that re-triggered the cache, and then deleted. The size is
// known from the response headers before any body arrives: this downloader aborts at the
// first progress callback when the expected length (Content-Length) is over the cap, or
// as soon as the bytes written pass it when the length is unknown or wrong.
//
// Foundation-only (URLSession) so ios/LogicTests can compile the policy.

enum RenderCachePolicy {
    /// Renders larger than this stream instead of caching (keeps Documents sane).
    static let maxBytes: Int64 = 200 * 1024 * 1024

    /// Abort a cache download? `expectedBytes` is the response's expected length (-1 when
    /// unknown); `writtenBytes` what has landed so far.
    static func shouldAbort(expectedBytes: Int64, writtenBytes: Int64, cap: Int64) -> Bool {
        expectedBytes > cap || writtenBytes > cap
    }
}

final class CappedDownload: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    enum Outcome {
        case file(URL)     // a temp file the caller now owns (move or delete it)
        case tooLarge      // aborted before/while exceeding the cap — nothing kept
        case failed        // transport / HTTP error — nothing kept
    }

    private let cap: Int64
    private let lock = NSLock()
    private var cont: CheckedContinuation<Outcome, Never>?
    private var landed: URL?
    private var oversize = false
    private var outcome: Outcome?     // set once the task completed (possibly before awaiting)

    private init(cap: Int64) { self.cap = cap }

    /// Download `url` only if it fits `maxBytes`. Cancelling the calling task cancels it.
    static func fetch(_ url: URL, maxBytes: Int64) async -> Outcome {
        let d = CappedDownload(cap: maxBytes)
        let session = URLSession(configuration: .default, delegate: d, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let task = session.downloadTask(with: url)
        return await withTaskCancellationHandler {
            await withCheckedContinuation { (c: CheckedContinuation<Outcome, Never>) in
                d.lock.lock()
                if let done = d.outcome {          // cancelled + completed before we got here
                    d.lock.unlock(); c.resume(returning: done); return
                }
                d.cont = c
                d.lock.unlock()
                task.resume()
            }
        } onCancel: {
            task.cancel()
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didWriteData bytesWritten: Int64, totalBytesWritten: Int64,
                    totalBytesExpectedToWrite: Int64) {
        guard RenderCachePolicy.shouldAbort(expectedBytes: totalBytesExpectedToWrite,
                                            writtenBytes: totalBytesWritten, cap: cap) else { return }
        lock.lock(); oversize = true; lock.unlock()
        downloadTask.cancel()
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didFinishDownloadingTo location: URL) {
        // The system deletes `location` when this returns — move it out synchronously.
        let status = (downloadTask.response as? HTTPURLResponse)?.statusCode ?? 200
        let size = (try? FileManager.default.attributesOfItem(atPath: location.path)[.size] as? Int64) ?? 0
        guard (200..<300).contains(status) else { return }
        guard size <= cap else { lock.lock(); oversize = true; lock.unlock(); return }
        let dest = FileManager.default.temporaryDirectory
            .appendingPathComponent("render-\(UUID().uuidString).mp4")
        if (try? FileManager.default.moveItem(at: location, to: dest)) != nil {
            lock.lock(); landed = dest; lock.unlock()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock()
        let c = cont; cont = nil
        let result: Outcome = landed.map { .file($0) } ?? (oversize ? .tooLarge : .failed)
        outcome = result
        lock.unlock()
        c?.resume(returning: result)
    }
}
