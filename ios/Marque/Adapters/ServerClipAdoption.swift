import Foundation

// LV-3 (2026-09-24) — the ONE way a create-job response lands in the Library.
//
// Every poll loop (pollJob, pollClipStatuses, watchTweakRender) matches server responses
// to local clips by SERVER clip id (UUID(uuidString: clip_id) == clip.id). The normal
// submit paths (instant submit, journal finalize) honoured that by replacing the local
// placeholder with clips carrying the server's ids — but resubmitFailedClip ("Try again",
// relaunch auto-resume, the editor's "Re-create") ignored `resp.clips`, stamped the new
// jobId onto the LOCAL clip id and polled [clip.id]. No response could ever name that id,
// so the card spun for the whole poll ceiling and then showed edit_timeout while the job
// had long finished. All three paths now build their rows here and swap them in with
// `replace`, so a local stand-in can never outlive its server job.
//
// Foundation-only (Models.swift types) so ios/LogicTests can compile and assert it.
enum ServerClipAdoption {
    struct Stub: Equatable {
        let id: String
        let format: String
        let ready: Bool
    }

    /// The tracked Library rows for a create-job response. A clip_id that doesn't parse as
    /// a UUID can never be matched by a poll, so it lands `.failed` (internal_error) with no
    /// jobId — the path that routes "Try again" to a fresh upload, the only one that heals it.
    ///
    /// `carryOver` is the local stand-in being replaced (upload placeholder, failed card):
    /// what the creator owns on it survives — title, caption, groups, script link, poster,
    /// created date, source tag. Render-lineage state (remoteURL, versions, cached render)
    /// is NOT carried: it belongs to the old job, and restoring a version against the new
    /// job's history would rewind the wrong edit.
    static func trackedClips(jobId: String, script: Script, footagePath: String?,
                             stubs: [Stub], etaSeconds: Int?, now: Date = Date(),
                             carryOver old: Clip? = nil) -> [Clip] {
        stubs.map { stub in
            let parsedId = UUID(uuidString: stub.id)
            let formatId = stub.format.isEmpty ? script.formatId : stub.format
            var c = Clip(id: parsedId ?? UUID(), scriptId: script.id, formatId: formatId,
                         formatName: Catalog.format(formatId).name,
                         title: script.title.isEmpty ? script.hook.text : script.title,
                         caption: script.cta,
                         predictedScore: script.predictedScore,
                         status: parsedId == nil ? .failed : (stub.ready ? .ready : .rendering),
                         seconds: Catalog.format(formatId).targetSeconds,
                         jobId: parsedId == nil ? nil : jobId)
            c.localVideoPath = footagePath
            if parsedId == nil {
                c.lastError = "internal_error"
                c.lastErrorDetail = "unreadable clip id from the server"
            }
            if !stub.ready && parsedId != nil { c.etaSeconds = etaSeconds; c.etaSetAt = now }
            if let old {
                c.scriptId = old.scriptId
                if !old.title.isEmpty { c.title = old.title }
                c.caption = old.caption
                c.captionLines = old.captionLines
                c.captioned = old.captioned
                c.setMemberGroupIds(old.memberGroupIds)
                c.createdAt = old.createdAt
                c.source = old.source
                c.thumbnailPath = old.thumbnailPath
                if c.localVideoPath == nil { c.localVideoPath = old.localVideoPath }
            }
            return c
        }
    }

    /// Swap `localId` for `tracked` IN PLACE (the card keeps its Library position; not
    /// found ⇒ inserted at the top). Any other row already holding one of the new server
    /// ids is dropped first: an idempotent create-job REPLAYS its first response, so a
    /// resume can hand back ids the Library already shows — never list a clip twice.
    static func replace(_ localId: UUID?, in clips: inout [Clip], with tracked: [Clip]) {
        let newIds = Set(tracked.map(\.id))
        clips.removeAll { newIds.contains($0.id) && $0.id != localId }
        if let localId, let at = clips.firstIndex(where: { $0.id == localId }) {
            clips.remove(at: at)
            clips.insert(contentsOf: tracked, at: at)
        } else {
            clips.insert(contentsOf: tracked, at: 0)
        }
    }

    /// Scheduled / pending posts pointing at the replaced stand-in follow it to its server
    /// clip (the first one — re-creation is one render per job).
    static func repoint(_ posts: inout [ScheduledPost], from oldId: UUID, to newId: UUID) {
        for i in posts.indices where posts[i].clipId == oldId { posts[i].clipId = newId }
    }
}
