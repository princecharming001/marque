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
        captionsOn = !doc.captions.isEmpty       // #1: seed enabled-state from what loaded
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
        guard let seg = session?.draft.segments[safe: segIdx],
              let kb = session?.draft.keptBounds(ofSegment: segIdx) else { return }
        // Trims act on the KEPT edge (kb.first/kb.last), not the raw segment boundary — so a
        // handle already dragged inward keeps trimming/restoring from where the footage
        // currently starts. Emitted as cut_range/restore_range (reversible, coalescing).
        // UX-1: CLAMPED so an over-drag can't eat the neighbor; OUTWARD drag restores the
        // interior drop the trim created (up to the raw segment bounds).
        switch edge {
        case .leading where newFrame > kb.first:
            mutate([.cut(kb.first, min(newFrame, kb.last - 30))], rejectMsg: "That leaves too little footage.")
        case .leading where newFrame < kb.first:
            mutate([.restore(max(seg.srcIn, newFrame), kb.first)], rejectMsg: "Nothing trimmed there to bring back.")
        case .trailing where newFrame < kb.last:
            mutate([.cut(max(newFrame, kb.first + 30), kb.last)], rejectMsg: "That leaves too little footage.")
        case .trailing where newFrame > kb.last:
            mutate([.restore(kb.last, min(seg.srcOut, newFrame))], rejectMsg: "Nothing trimmed there to bring back.")
        default: break
        }
    }

    func splitSelected(_ segIdx: Int) {
        player?.pause()          // #10: stabilize the playhead before capturing the cut frame
        guard let seg = session?.draft.segments[safe: segIdx] else { return }
        guard seg.frames >= 90 else { flash("That clip is too short to split."); return }
        // #10: if the playhead isn't inside this clip, tell the user the cut used its center.
        if !(playheadSourceFrame > seg.srcIn + 30 && playheadSourceFrame < seg.srcOut - 30) {
            flash("Cut at the clip's middle — scrub onto a clip to cut there.")
        }
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

    func toggleCaptions(_ on: Bool) {
        // #1: enabling needs a transcript; the op is a logged no-op locally (server rebuilds),
        // and captionsOn drives the live word-preview + button label.
        if on, words.isEmpty { flash("No transcript to caption."); return }
        mutate([.captionsEnabled(on)])
        captionsOn = on
    }
    func setCaptionStyle(_ s: String) { mutate([.captionStyle(s)]) }

    // MARK: Phrase-level caption editing (the caption-track model — never word chips)

    func beginPhraseEdit(_ p: CaptionPhrase) {
        guard captionsOn else { flash("Turn captions on first."); return }
        editDraft = p.text
        editingPhrase = p
    }

    /// Commit an edited phrase: the new text is redistributed across the phrase's transcript
    /// word slots (extra words merge into shared slots; missing words clear their slot), as ONE
    /// perform → one undo step. Word timing never changes — fixing text never breaks sync
    /// (the Descript "Correct" principle).
    func commitPhraseEdit() {
        guard let p = editingPhrase else { return }
        editingPhrase = nil
        let newWords = editDraft.split(separator: " ").map(String.init).filter { !$0.isEmpty }
        var ops: [WireOp] = []
        // Clear any stray captions in the phrase's span that sit off the transcript slots
        // (e.g. server-side chat edits) so the redistribute below fully owns the range.
        for cap in session?.draft.captions ?? []
        where cap.frame >= p.startFrame && cap.frame < p.endFrame && !p.wordFrames.contains(cap.frame) {
            ops.append(.editCaption(frame: cap.frame, word: ""))
        }
        let slots = p.wordFrames
        if newWords.count <= slots.count {
            for (i, slot) in slots.enumerated() {
                ops.append(.editCaption(frame: slot, word: i < newWords.count ? newWords[i] : ""))
            }
        } else {
            // More words than slots: balanced contiguous chunks share slots.
            let base = newWords.count / slots.count, extra = newWords.count % slots.count
            var idx = 0
            for (j, slot) in slots.enumerated() {
                let take = base + (j < extra ? 1 : 0)
                ops.append(.editCaption(frame: slot, word: newWords[idx..<idx + take].joined(separator: " ")))
                idx += take
            }
        }
        mutate(ops)
    }
    /// UX-2: an insert's [start, start+len) window ANCHORED AT THE PLAYHEAD — every editor
    /// inserts where you're parked, not at 0:00. Falls back to the first kept interval when
    /// the playhead sits outside all kept footage (e.g. parked at the very end).
    private func insertWindow(len: Int) -> (Int, Int)? {
        guard let d = session?.draft else { return nil }
        let f = playheadSourceFrame
        // #9: when parked at/after the end (no interval contains the playhead), anchor to the
        // LAST kept interval — not the first — so an outro zoom/card lands where the user is.
        let iv = d.keptIntervals.first { $0.srcIn <= f && f < $0.srcOut } ?? d.keptIntervals.last
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
        // 2.5s default block — the documented talking-head push-in idiom (100→~108% over 2-6s).
        guard let (a, b) = insertWindow(len: 75) else { return }
        // UX-2: don't stack an invisible duplicate — repeated taps used to append identical
        // overlays the user couldn't see or remove.
        if session?.draft.overlays.contains(where: { $0.type == "punch_in" && $0.srcIn < b && a < $0.srcOut }) == true {
            flash("There's already a zoom here — tap its block to adjust it.")
            return
        }
        mutate([.addPunchIn(a, b, scale: 1.08)], rejectMsg: "Zooms aren't rendered for this style.")
        // Select the new block so the intensity/duration controls appear immediately.
        if let idx = session?.draft.overlays.lastIndex(where: { $0.type == "punch_in" && $0.srcIn == a }) {
            selectedSeg = nil
            selectedOverlay = idx
        }
    }

    /// Swap a zoom block's intensity. edit_overlay doesn't carry scale, so this is a
    /// remove+re-add at the same span — one perform, one undo step.
    func setZoomIntensity(_ idx: Int, scale: Double) {
        guard let o = session?.draft.overlays[safe: idx], o.type == "punch_in" else { return }
        mutate([.removeOverlay(kind: "punch_in", o.srcIn, o.srcOut),
                .addPunchIn(o.srcIn, o.srcOut, scale: scale)])
        // The re-added block lands at the end of the overlays array — keep it selected.
        if let ni = session?.draft.overlays.lastIndex(where: { $0.type == "punch_in" && $0.srcIn == o.srcIn }) {
            selectedOverlay = ni
        }
    }

    /// Grow/shrink a zoom block's tail by ±0.5s, clamped to a 0.5s minimum.
    func adjustOverlayDuration(_ idx: Int, deltaFrames: Int) {
        guard let o = session?.draft.overlays[safe: idx] else { return }
        let newOut = max(o.srcIn + 15, o.srcOut + deltaFrames)
        guard newOut != o.srcOut else { flash("That's as short as a zoom gets.") ; return }
        mutate([.editOverlay(index: idx, frameIn: o.srcIn, frameOut: newOut)])
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

    // MARK: caption list sheet — the batch editor (rows: timecode + phrase, tap to fix)

    var captionListPanel: some View {
        VStack(spacing: 0) {
            // Custom header (CapCut's caption bar).
            ZStack {
                Text("\(phrases.count) captions")
                    .font(AppFont.headline).foregroundStyle(.white)
                HStack {
                    Spacer()
                    Button { showCaptionList = false } label: {
                        Text("Done").font(AppFont.headline).foregroundStyle(Palette.accent)
                            .padding(.horizontal, Space.md).padding(.vertical, 8)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("editorPro.captionList.done")
                }
            }
            .padding(.horizontal, Space.sm).padding(.top, Space.lg).padding(.bottom, Space.sm)

            ScrollView {
                VStack(spacing: 0) {
                    ForEach(phrases) { p in
                        Button {
                            // List stays open — fixing captions is a serial workflow; the edit
                            // dialog floats above and the row updates in place on commit.
                            beginPhraseEdit(p)
                        } label: {
                            HStack(alignment: .firstTextBaseline, spacing: Space.md) {
                                Text(timecode(forPhrase: p))
                                    .font(.system(size: 11, weight: .medium)).monospacedDigit()
                                    .foregroundStyle(.white.opacity(0.45))
                                    .frame(width: 44, alignment: .leading)
                                Text(p.text)
                                    .font(AppFont.callout).foregroundStyle(.white)
                                    .multilineTextAlignment(.leading)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                Image(systemName: "pencil")
                                    .font(.system(size: 11)).foregroundStyle(.white.opacity(0.35))
                            }
                            .padding(.horizontal, Space.lg).padding(.vertical, 12)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("editorPro.captionRow.\(p.startFrame)")
                        Rectangle().fill(Color.white.opacity(0.08)).frame(height: 0.5)
                            .padding(.leading, Space.lg)
                    }
                }
                .padding(.vertical, Space.sm)
            }
        }
        .frame(height: 320, alignment: .top)
        .frame(maxWidth: .infinity)
        .background(Palette.ink.opacity(0.6))
    }

    /// The phrase's output-time position as m:ss (where it plays in the cut, drops applied).
    private func timecode(forPhrase p: CaptionPhrase) -> String {
        guard let span = session?.draft.outputSpan(srcIn: p.startFrame, srcOut: p.endFrame) else { return "–" }
        let s = Int(span.start)
        return String(format: "%d:%02d", s / 60, s % 60)
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
