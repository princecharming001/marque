import SwiftUI
import AVFoundation

extension ProEditorView {

    // MARK: load

    func load() async {
        guard let jobId = clip.jobId,
              let result = await store.backend.pollClipJob(jobId: jobId, includeWords: true),
              let edlDict = result["edl"] as? [String: Any] else {
            phase = .failed("Couldn't load this clip's edit — the session may have expired.")
            return
        }
        let doc = EditorDocument(edl: edlDict)
        let sess = EditorSession(document: doc)
        session = sess

        // Source video: prefer the local recording, else the server public URL, else placeholder.
        var url: URL?
        if let local = clip.localVideoPath { url = MediaStore.url(for: local) }
        if url == nil, let src = result["source_url"] as? String, let u = URL(string: src) { url = u }
        let pc = EditorPlayerController(sourceURL: url)
        pc.update(document: doc)
        player = pc
        filmstrip = FilmstripCache(sourceURL: url)
        if let fs = filmstrip { Task { await fs.warm(durationSeconds: doc.outputSeconds) } }

        // Transcript words for the Text-mode word editor.
        let raw = result["words"] as? [[String: Any]] ?? []
        words = raw.compactMap { w in
            guard let text = w["word"] as? String, !text.isEmpty else { return nil }
            let sm = (w["start_ms"] as? Double) ?? Double(w["start_ms"] as? Int ?? 0)
            let em = (w["end_ms"] as? Double) ?? Double(w["end_ms"] as? Int ?? 0)
            let sf = msToFrame(sm)
            return WordSpan(text: text, startFrame: sf, endFrame: max(sf + 1, msToFrame(em)))
        }.sorted { $0.startFrame < $1.startFrame }

        // Per-style capabilities gate the Effects tab.
        if let all = await store.backend.editorCapabilities() { caps = all[doc.style] }
        phase = .editing
    }

    // MARK: gesture → op helpers (one gesture = one perform() = one undo step)

    func mutate(_ ops: [WireOp], rejectMsg: String? = nil) {
        guard let session else { return }
        if session.perform(ops) { refreshPlayer() }
        else if let rejectMsg { flash(rejectMsg) }
    }

    func refreshPlayer() {
        guard let session, let player else { return }
        player.update(document: session.draft)
    }

    private func flash(_ msg: String) {
        transient = msg
        Task { try? await Task.sleep(nanoseconds: 2_500_000_000); if transient == msg { transient = nil } }
    }

    // MARK: Edit-mode actions

    func trim(segIdx: Int, edge: TrimEdge, to newFrame: Int) {
        guard let seg = session?.draft.segments[safe: segIdx] else { return }
        // Trims are emitted as cut_range (reversible via restore_range) — not destructive trim_*.
        // UX-1: CLAMPED — an over-drag past the clip's far edge used to emit an unbounded cut
        // that silently ate the NEIGHBORING clip's footage (drops are global source ranges).
        // And an OUTWARD drag now honors the rubber-band's promise: it restores previously
        // trimmed footage via restore_range (no-op flash when there's nothing to restore).
        switch edge {
        case .leading where newFrame > seg.srcIn:
            mutate([.cut(seg.srcIn, min(newFrame, seg.srcOut - 30))], rejectMsg: "That leaves too little footage.")
        case .leading where newFrame < seg.srcIn:
            mutate([.restore(max(0, newFrame), seg.srcIn)], rejectMsg: "Nothing trimmed there to bring back.")
        case .trailing where newFrame < seg.srcOut:
            mutate([.cut(max(newFrame, seg.srcIn + 30), seg.srcOut)], rejectMsg: "That leaves too little footage.")
        case .trailing where newFrame > seg.srcOut:
            mutate([.restore(seg.srcOut, newFrame)], rejectMsg: "Nothing trimmed there to bring back.")
        default: break
        }
    }

    func splitSelected(_ segIdx: Int) {
        guard let seg = session?.draft.segments[safe: segIdx] else { return }
        guard seg.frames >= 90 else { flash("That clip is too short to split."); return }
        let lo: Int = seg.srcIn + 30
        let hi: Int = seg.srcOut - 30
        // Editor convention (CapCut/InShot/VN): split cuts AT THE PLAYHEAD. Use the playhead's
        // source frame when it's inside this clip; fall back to the clip middle otherwise
        // (e.g. the user selected a clip the playhead isn't parked on).
        let playhead: Int = playheadSourceFrame
        let target: Int = (playhead > lo && playhead < hi) ? playhead : (seg.srcIn + seg.srcOut) / 2
        // Snap to the nearest word boundary near the target so cuts never land mid-word —
        // but only within half a second; beyond that honor the exact playhead position.
        var candidates: [Int] = []
        for w in words where w.startFrame > lo && w.startFrame < hi { candidates.append(w.startFrame) }
        let nearest: Int? = candidates.min { a, b in abs(a - target) < abs(b - target) }
        let boundary: Int = (nearest != nil && abs(nearest! - target) <= 15) ? nearest! : target
        mutate([.split(segIdx, at: boundary)])
        // UX-8: keep the FIRST half selected (same source index) so follow-up trims/moves
        // continue naturally instead of dropping the user's context.
        selectedSeg = segIdx
    }

    func deleteSelected(_ segIdx: Int) {
        guard let seg = session?.draft.segments[safe: segIdx] else { return }
        mutate([.cut(seg.srcIn, seg.srcOut)], rejectMsg: "You can't delete the whole clip.")
        selectedSeg = nil
    }

    func reorder(_ order: [Int]) { mutate([.reorder(order)]) }

    // I-7: move the selected clip one slot left/right in play order (explicit, reliable
    // reorder — the timeline can't host a drag without fighting scrub/trim/zoom gestures).
    private func currentOrder() -> [Int] {
        session?.draft.segmentOrder ?? Array(session?.draft.segments.indices ?? (0..<0))
    }
    func canMoveSelected(by delta: Int) -> Bool {
        guard let seg = selectedSeg else { return false }
        let order = currentOrder()
        guard let pos = order.firstIndex(of: seg) else { return false }
        let np = pos + delta
        return np >= 0 && np < order.count
    }
    func moveSelected(by delta: Int) {
        guard canMoveSelected(by: delta), let seg = selectedSeg else { return }
        var order = currentOrder()
        guard let pos = order.firstIndex(of: seg) else { return }
        order.swapAt(pos, pos + delta)
        reorder(order)
    }

    func bumpHaptic() { hapticTick += 1 }

    // MARK: Sound-mode actions

    func mutedState(_ segIdx: Int) -> Bool {
        guard let seg = session?.draft.segments[safe: segIdx] else { return false }
        return session?.draft.volumeRanges.contains { $0.srcIn <= seg.srcIn && $0.srcOut >= seg.srcOut && $0.volume == 0 } ?? false
    }
    func toggleMute(_ segIdx: Int) {
        guard let seg = session?.draft.segments[safe: segIdx] else { return }
        mutate([mutedState(segIdx) ? .segmentVolume(seg.srcIn, seg.srcOut, 1.0) : .mute(seg.srcIn, seg.srcOut)])
    }
    func setClipVolume(_ segIdx: Int, _ v: Double) {
        guard let seg = session?.draft.segments[safe: segIdx] else { return }
        mutate([.segmentVolume(seg.srcIn, seg.srcOut, v)])
    }
    func pickMusic(_ track: MusicCatalog.Track) { mutate([.setMusic(url: track.url, volume: 0.15, duck: true)]); showMusicSheet = false }
    func removeMusic() { mutate([.removeMusic()]) }
    func setMusicVolume(_ v: Double) {
        guard let m = session?.draft.music else { return }
        mutate([.setMusic(url: m.url, volume: v, duck: m.duckVoice)])
    }

    // MARK: Text-mode actions

    func toggleCaptions(_ on: Bool) { mutate([.captionsEnabled(on)], rejectMsg: on ? "No transcript to caption." : nil) }
    func setCaptionStyle(_ s: String) { mutate([.captionStyle(s)]) }
    func beginCaptionEdit(frame: Int, current: String) { editDraft = current; editingCaptionFrame = frame }
    func commitCaptionEdit() {
        if let f = editingCaptionFrame { mutate([.editCaption(frame: f, word: editDraft.trimmingCharacters(in: .whitespaces))]) }
        editingCaptionFrame = nil
    }
    /// UX-2: an insert's [start, start+len) window ANCHORED AT THE PLAYHEAD — every editor
    /// inserts where you're parked, not at 0:00. Falls back to the first kept interval when
    /// the playhead sits outside all kept footage (e.g. parked at the very end).
    private func insertWindow(len: Int) -> (Int, Int)? {
        guard let d = session?.draft else { return nil }
        let f = playheadSourceFrame
        let iv = d.keptIntervals.first { $0.srcIn <= f && f < $0.srcOut } ?? d.keptIntervals.first
        guard let iv else { return nil }
        let start = min(max(f, iv.srcIn), max(iv.srcIn, iv.srcOut - 30))
        return (start, min(start + len, iv.srcOut))
    }

    func addTextCard(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty, let (a, b) = insertWindow(len: 75) else { return }
        mutate([.addTextCard(a, b, text: t)], rejectMsg: "Text cards aren't supported for this style.")
    }

    // MARK: Effects-mode actions

    func addPunchInOnHook() {
        guard let (a, b) = insertWindow(len: 60) else { return }
        // UX-2: don't stack an invisible duplicate — repeated taps used to append identical
        // overlays the user couldn't see or remove.
        if session?.draft.overlays.contains(where: { $0.type == "punch_in" && $0.srcIn < b && a < $0.srcOut }) == true {
            flash("There's already a zoom here — undo to remove it.")
            return
        }
        mutate([.addPunchIn(a, b, scale: 1.08)], rejectMsg: "Punch-ins aren't rendered for this style.")
    }
    func addBroll(_ query: String) {
        guard let (a, b) = insertWindow(len: 90) else { return }
        mutate([.addBroll(a, b, query: query)], rejectMsg: "B-roll isn't supported for this style.")
    }

    // MARK: Overlay chip-lane actions

    /// Delete exactly this overlay (remove_overlays scoped to its kind + exact frames —
    /// an overlapping sibling of the same kind at the same span would go too; acceptable).
    func deleteOverlay(_ idx: Int) {
        guard let o = session?.draft.overlays[safe: idx] else { return }
        mutate([.removeOverlay(kind: o.type, o.srcIn, o.srcOut)])
        selectedOverlay = nil
    }

    func beginOverlayTextEdit(_ idx: Int) {
        guard let o = session?.draft.overlays[safe: idx], o.type == "text_card" else { return }
        editDraft = o.text
        editingOverlayIndex = idx
    }

    func commitOverlayTextEdit() {
        if let idx = editingOverlayIndex {
            let t = editDraft.trimmingCharacters(in: .whitespaces)
            if !t.isEmpty { mutate([.editOverlayText(index: idx, text: t)]) }
        }
        editingOverlayIndex = nil
    }

    // MARK: Save (flatten op log → one tweak POST → per-clip poll → reload)

    func save() {
        guard let session, session.isDirty, let jobId = clip.jobId, applyTask == nil else { dismiss(); return }
        let ops = session.flattenedOps()
        // A structure-only change (all cut/restore/split/reorder) renders pixel-identically —
        // commit without spending a render (defer_render).
        let structural = ops.allSatisfy { ["cut_range", "restore_range", "split_segment", "reorder_segments", "mute_range", "set_segment_volume"].contains($0["type"] as? String ?? "") }
        phase = .applying
        applyTask = Task {
            let resp = await store.backend.tweakClipOps(jobId: jobId, clipId: clip.id.uuidString, ops: ops, deferRender: structural)
            if resp["error"] as? Bool == true {
                if resp["transient"] as? Bool == true { phase = .editing; applyTask = nil; flash(resp["reply"] as? String ?? "Still busy — try again."); return }
                phase = .failed(resp["reply"] as? String ?? "Couldn't apply your edits."); return
            }
            let needsRender = resp["needs_render"] as? Bool ?? false
            if needsRender {
                phase = .rendering; renderStartedAt = Date(); store.setClipRendering(clip.id)
                let (ready, message) = await pollClipUntilDone(jobId: jobId)
                guard !Task.isCancelled else { return }
                if ready { dismiss() } else { phase = .failed(message ?? "Couldn't finish that render.") }
            } else {
                dismiss()   // keyless/mock: applied in place
            }
        }
    }

    func pollClipUntilDone(jobId: String) async -> (ready: Bool, message: String?) {
        for _ in 0..<60 {
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            if Task.isCancelled { return (false, nil) }
            let (maybe, http) = await store.backend.pollClipJobWithStatus(jobId: jobId)
            if http == 404 || http == 410 { return (false, "This edit session expired — re-record to keep editing.") }
            guard let result = maybe, let jobClips = result["clips"] as? [[String: Any]],
                  let mine = jobClips.first(where: { UUID(uuidString: ($0["clip_id"] as? String) ?? "") == clip.id }) else { continue }
            let status = mine["status"] as? String ?? ""
            if status == "ready" {
                store.applyTweakResult(clip.id, remoteURL: mine["render_url"] as? String)
                if mine["last_render_failed"] as? Bool == true { return (false, "That edit's render failed — your previous cut is untouched.") }
                return (true, nil)
            }
            if status == "failed" { return (false, store.friendlyRenderError(mine["error"] as? String, detail: mine["error_detail"] as? String)) }
        }
        return (false, "Still working — check back in the Library shortly.")
    }

    // MARK: rendering / failed / transient views

    var renderingView: some View {
        VStack(spacing: Space.md) {
            ProgressView().tint(Palette.accent)
            Text("Re-rendering your clip…").font(AppFont.body).foregroundStyle(.white.opacity(0.8))
            if let renderStartedAt {
                TimelineView(.periodic(from: renderStartedAt, by: 1)) { ctx in
                    Text("\(Int(ctx.date.timeIntervalSince(renderStartedAt)))s").font(AppFont.caption).foregroundStyle(.white.opacity(0.5)).monospacedDigit()
                }
            }
            Button("Cancel") { dismiss() }.tint(.white).padding(.top, Space.sm)
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    func failedView(_ msg: String) -> some View {
        VStack(spacing: Space.md) {
            Image(systemName: "exclamationmark.triangle").font(.system(size: 32)).foregroundStyle(.white.opacity(0.5))
            Text(msg).font(AppFont.body).foregroundStyle(.white.opacity(0.8)).multilineTextAlignment(.center)
            Button("Close") { dismiss() }.tint(Palette.accent)
        }.padding(Space.xl).frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    func transientBar(_ t: String) -> some View {
        HStack(spacing: Space.sm) {
            Image(systemName: "info.circle").foregroundStyle(.white.opacity(0.7))
            Text(t).font(AppFont.caption).foregroundStyle(.white.opacity(0.85))
            Spacer()
        }.padding(.horizontal, Space.md).padding(.vertical, 6).background(Palette.ink.opacity(0.8))
    }

    // MARK: music sheet

    var musicSheet: some View {
        NavigationStack {
            List {
                ForEach(Array(MusicCatalog.tracks.enumerated()), id: \.offset) { i, track in
                    Button { pickMusic(track) } label: {
                        HStack { Image(systemName: "music.note"); Text(track.name); Spacer() }
                    }.accessibilityIdentifier("editorPro.track.\(i)")
                }
                if session?.draft.music != nil {
                    Button(role: .destructive) { removeMusic() } label: { Label("Remove music", systemImage: "speaker.slash") }
                }
            }.navigationTitle("Add sound").navigationBarTitleDisplayMode(.inline)
        }.presentationDetents([.medium])
    }
}

enum TrimEdge { case leading, trailing }

extension Array {
    subscript(safe i: Int) -> Element? { indices.contains(i) ? self[i] : nil }
}
