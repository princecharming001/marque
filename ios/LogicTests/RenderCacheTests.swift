import Foundation

// LV-7: the render cache decides on the response's expected length BEFORE downloading the
// body — an over-cap render is never pulled down just to be deleted.
func runRenderCacheTests() {
    suite("RenderCachePolicy — LV-7 abort decision")
    let cap = RenderCachePolicy.maxBytes
    expectEqual(cap, 209_715_200, "cap is 200 MiB (unchanged)")
    expect(RenderCachePolicy.shouldAbort(expectedBytes: cap + 1, writtenBytes: 16_384, cap: cap),
           "Content-Length over the cap → abort at the FIRST progress callback")
    expect(!RenderCachePolicy.shouldAbort(expectedBytes: cap, writtenBytes: 16_384, cap: cap),
           "exactly the cap → download")
    expect(!RenderCachePolicy.shouldAbort(expectedBytes: -1, writtenBytes: 50_000_000, cap: cap),
           "unknown length, under the cap so far → keep going")
    expect(RenderCachePolicy.shouldAbort(expectedBytes: -1, writtenBytes: cap + 1, cap: cap),
           "unknown length → abort as soon as the bytes pass the cap")
    expect(RenderCachePolicy.shouldAbort(expectedBytes: 10_000_000, writtenBytes: cap + 1, cap: cap),
           "a lying Content-Length can't sneak past the cap")

    suite("CappedDownload — LV-7 end-to-end (file:// source)")
    let src = FileManager.default.temporaryDirectory
        .appendingPathComponent("logictests-render-\(UUID().uuidString).bin")
    FileManager.default.createFile(atPath: src.path, contents: Data(repeating: 7, count: 3_000_000))
    defer { try? FileManager.default.removeItem(at: src) }
    let sem = DispatchSemaphore(value: 0)
    var small: CappedDownload.Outcome = .failed
    var big: CappedDownload.Outcome = .failed
    Task.detached {
        small = await CappedDownload.fetch(src, maxBytes: 1_000_000)
        big = await CappedDownload.fetch(src, maxBytes: 10_000_000)
        sem.signal()
    }
    _ = sem.wait(timeout: .now() + 60)
    if case .tooLarge = small { expect(true, "3 MB render vs 1 MB cap → tooLarge, nothing kept") }
    else { expect(false, "3 MB render vs 1 MB cap → tooLarge, nothing kept") }
    if case .file(let url) = big {
        let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? -1
        expectEqual(size, 3_000_000, "3 MB render vs 10 MB cap → downloaded intact")
        try? FileManager.default.removeItem(at: url)
    } else {
        expect(false, "3 MB render vs 10 MB cap → downloaded intact")
    }
    let missing = FileManager.default.temporaryDirectory.appendingPathComponent("no-such-\(UUID().uuidString)")
    var gone: CappedDownload.Outcome = .tooLarge
    let sem2 = DispatchSemaphore(value: 0)
    Task.detached { gone = await CappedDownload.fetch(missing, maxBytes: 10_000_000); sem2.signal() }
    _ = sem2.wait(timeout: .now() + 60)
    if case .failed = gone { expect(true, "missing source → failed (fail-soft)") }
    else { expect(false, "missing source → failed (fail-soft)") }
}
