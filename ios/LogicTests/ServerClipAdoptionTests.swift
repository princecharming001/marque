import Foundation

// LV-3: a re-submitted clip must be tracked under the SERVER's clip id — the id every poll
// loop matches on (UUID(uuidString: clip_id) == clip.id) — never under its local id.
func runServerClipAdoptionTests() {
    suite("ServerClipAdoption — LV-3 resubmit adopts the server clip ids")
    let script = Script(pillarName: "Money", title: "Why budgets fail", formatId: "myth-buster",
                        hook: Hook(text: "Budgets fail", signal: .narrative, strength: 70),
                        altHooks: [], body: "", cta: "Follow for more", shotPlan: [],
                        targetSeconds: 45, predictedScore: 70)
    let group = UUID()
    let created = Date(timeIntervalSince1970: 1_700_000_000)
    var failed = Clip(scriptId: script.id, formatId: "myth-buster", formatName: "Myth buster",
                      title: "My own title", caption: "hand-edited caption", predictedScore: 70,
                      status: .failed, seconds: 45, jobId: "job-old")
    failed.localVideoPath = "media/take.mov"
    failed.thumbnailPath = "media/poster.jpg"
    failed.lastError = "edit_timeout"
    failed.remoteURL = "https://cdn.example/old-render.mp4"
    failed.renderHistory = [RenderVersion(url: "https://cdn.example/older.mp4", label: "")]
    failed.setMemberGroupIds([group])
    failed.createdAt = created

    // The server's create-job response for the re-submit (backend uuid4 → lowercase).
    let serverId = "5f0c2a9e-8d61-4b7a-9c1e-2f4d6b8a0c13"
    let stubs = [ServerClipAdoption.Stub(id: serverId, format: "", ready: false)]
    let tracked = ServerClipAdoption.trackedClips(jobId: "job-new", script: script,
                                                  footagePath: failed.localVideoPath,
                                                  stubs: stubs, etaSeconds: 90, now: created,
                                                  carryOver: failed)
    expectEqual(tracked.count, 1, "one tracked row per server clip")
    let t = tracked[0]
    expectEqual(t.id, UUID(uuidString: serverId)!, "tracked under the SERVER clip id")
    expectEqual(t.jobId, "job-new", "stamped with the NEW job id")
    expectEqual(t.status, .rendering, "rendering while the new job runs")
    expectEqual(t.lastError, nil, "the old failure is cleared")
    expectEqual(t.etaSeconds, 90, "server ETA kept")

    // The poll loops' matcher (pollJob / pollClipStatuses / watchTweakRender).
    func pollMatches(_ clips: [Clip], _ jobClip: [String: Any]) -> Bool {
        guard let id = UUID(uuidString: jobClip["clip_id"] as? String ?? "") else { return false }
        return clips.contains { $0.id == id }
    }
    let response: [String: Any] = ["clip_id": serverId, "status": "ready"]
    var library = [failed]
    expect(!pollMatches(library, response),
           "the OLD behavior (local id kept) can never match the new job's responses")
    ServerClipAdoption.replace(failed.id, in: &library, with: tracked)
    expect(pollMatches(library, response), "after adoption the poll response matches")
    expect(!library.contains { $0.id == failed.id }, "the local stand-in is gone")

    suite("ServerClipAdoption — LV-3 what survives the swap")
    expectEqual(t.title, "My own title", "title carried")
    expectEqual(t.caption, "hand-edited caption", "caption carried (never reset to the CTA)")
    expectEqual(t.memberGroupIds, [group], "Library groups carried")
    expectEqual(t.createdAt, created, "created date carried (stable ordering)")
    expectEqual(t.thumbnailPath, "media/poster.jpg", "poster carried")
    expectEqual(t.localVideoPath, "media/take.mov", "raw take kept")
    expectEqual(t.scriptId, failed.scriptId, "script link kept")
    expectEqual(t.remoteURL, nil, "old job's render NOT carried")
    expect(t.renderHistory == nil, "old job's versions NOT carried (they'd rewind the wrong job)")

    suite("ServerClipAdoption — LV-3 replace keeps position, never duplicates")
    let a = Clip(scriptId: script.id, formatId: "f", formatName: "F", caption: "", predictedScore: 0,
                 status: .ready, seconds: 10)
    let b = Clip(scriptId: script.id, formatId: "f", formatName: "F", caption: "", predictedScore: 0,
                 status: .ready, seconds: 10)
    var ordered = [a, failed, b]
    ServerClipAdoption.replace(failed.id, in: &ordered, with: tracked)
    expectEqual(ordered.map(\.id), [a.id, t.id, b.id], "swapped in place (middle stays middle)")
    var replayed = [tracked[0], a, failed]   // an idempotent replay handed back an id already shown
    ServerClipAdoption.replace(failed.id, in: &replayed, with: tracked)
    expectEqual(replayed.map(\.id), [a.id, t.id], "stale copy of the server id dropped, no duplicate")
    var fresh = [a]
    ServerClipAdoption.replace(nil, in: &fresh, with: tracked)
    expectEqual(fresh.map(\.id), [t.id, a.id], "no stand-in (analyze-first confirm) → inserted at top")

    suite("ServerClipAdoption — LV-3 edge cases")
    let bad = ServerClipAdoption.trackedClips(
        jobId: "job-x", script: script, footagePath: nil,
        stubs: [ServerClipAdoption.Stub(id: "not-a-uuid", format: "", ready: false)], etaSeconds: nil)
    expectEqual(bad.first?.status, .failed, "unparseable server id → failed (can't be polled)")
    expectEqual(bad.first?.jobId, nil, "…with no jobId, so Try again re-uploads")
    expectEqual(bad.first?.lastError, "internal_error", "…and a real error code")
    let ready = ServerClipAdoption.trackedClips(
        jobId: "job-r", script: script, footagePath: nil,
        stubs: [ServerClipAdoption.Stub(id: serverId, format: "talking-head", ready: true)], etaSeconds: 30)
    expectEqual(ready.first?.status, .ready, "ready stub → ready")
    expectEqual(ready.first?.etaSeconds, nil, "no ETA on a ready clip")
    expectEqual(ready.first?.formatId, "talking-head", "server format wins")
    expectEqual(ready.first?.caption, script.cta, "no stand-in → caption from the script")

    var posts = [ScheduledPost(clipId: failed.id, caption: "c", platforms: [.instagram], date: created),
                 ScheduledPost(clipId: a.id, caption: "c", platforms: [.tiktok], date: created)]
    ServerClipAdoption.repoint(&posts, from: failed.id, to: t.id)
    expectEqual(posts.map(\.clipId), [t.id, a.id], "scheduled posts follow the stand-in to its server clip")
}
