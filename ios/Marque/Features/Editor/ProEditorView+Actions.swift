import SwiftUI
import AVFoundation
import PhotosUI

extension ProEditorView {

    // MARK: load

    func load() async {
        guard let jobId = clip.jobId else {
            phase = .failed("Couldn't load this clip's edit — the session may have expired.")
            return
        }
        // Use the status-aware poll (restart-fragility audit): pollClipJob swallowed the
        // HTTP code, so a transient 503 (DB blip) or a restored-but-not-yet-edited job
        // (no EDL) both mis-reported "session may have expired." Distinguish them.
        let (result, http) = await store.backend.pollClipJobWithStatus(jobId: jobId, includeWords: true)
        if http == 503 {
            phase = .failed("Couldn't reach the studio just now — pull to try again.")
            return
        }
        guard let result, let edlDict = result["edl"] as? [String: Any] else {
            // The job is gone (404/410 → a body with no `edl`). If we still hold the local
            // take, the editor is recoverable — offer to re-create the edit from footage
            // rather than dead-ending.
            editorRecoverable = (clip.localVideoPath != nil)
            phase = .failed(editorRecoverable
                ? "This edit session expired. Re-create it from your footage to keep editing."
                : "Couldn't load this clip's edit — the session may have expired.")
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
        await MusicCatalog.hydrate(using: store.backend)   // show the same beds the render uses
        // A7: the active theme (if EDIT_THEMES produced one) — optional, absent-safe
        // (older jobs / EDIT_THEMES off never carry it). The read-only report card was removed
        // in the declutter (self_review/lint are still computed server-side, just not surfaced here).
        activeThemeId = result["theme_id"] as? String ?? ""
        if themes.isEmpty { themes = await store.backend.fetchThemes() }
        phase = .editing
    }

    // MARK: A7 feature #1 — retheme (a SEPARATE endpoint from /tweak: it only
    // restamps caption/grade/duck, never touches segments/drops/overlays, so it
    // skips the local op-log entirely and re-renders directly).

    func retheme(to themeId: String) {
        guard let jobId = clip.jobId, rethemeTask == nil, applyTask == nil else { return }
        phase = .applying
        rethemeTask = Task {
            let resp = await store.backend.rethemeClip(jobId: jobId, themeId: themeId, clipId: clip.id.uuidString)
            if resp["error"] as? Bool == true {
                if resp["transient"] as? Bool == true {
                    phase = .editing; rethemeTask = nil
                    flash(resp["reply"] as? String ?? "Still busy — try again."); return
                }
                phase = .editing; rethemeTask = nil
                flash(resp["reply"] as? String ?? "Couldn't switch themes."); return
            }
            activeThemeId = resp["theme_id"] as? String ?? themeId
            let rendering = resp["rendering"] as? [String] ?? []
            if !rendering.isEmpty {
                phase = .rendering; renderStartedAt = Date(); store.setClipRendering(clip.id)
                let (ready, message) = await pollClipUntilDone(jobId: jobId)
                guard !Task.isCancelled else { return }
                rethemeTask = nil
                if ready { await load() } else { phase = .failed(message ?? "Couldn't finish that render.") }
            } else {
                rethemeTask = nil
                phase = .editing   // keyless/mock, or nothing needed re-rendering
            }
        }
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

    /// Apply one of the 10 popular presets — sets the base render style AND a full options
    /// bundle so every knob is defined (switching presets resets outline/color/box/font/caps).
    func applyCaptionPreset(_ p: CaptionPreset) {
        mutate([
            .captionStyle(p.style),
            .captionOptions(accent: p.accent ?? "default", uppercase: p.uppercase,
                            font: p.font, grouping: p.grouping, strokePx: p.strokePx,
                            bg: p.bg.isEmpty ? "none" : p.bg),
        ])
    }

    /// The preset id whose full bundle matches the current caption options (for the picker's
    /// active-chip highlight); nil when the creator has hand-tuned away from any preset.
    func activeCaptionPresetId() -> String? {
        guard let d = session?.draft else { return nil }
        let o = d.captionOptions
        return CaptionPreset.all.first {
            $0.style == d.captionStyle && $0.font == o.font && $0.uppercase == o.uppercase
                && ($0.accent ?? "") == (o.accent ?? "") && $0.strokePx == o.strokePx
                && $0.grouping == o.grouping && $0.bg == o.bg
        }?.id
    }

    /// Shift ALL captions at once — one track-wide pos_y (0.15…0.85). The three chips map to
    /// safe top/middle/bottom anchors that clear the platform chrome.
    func setCaptionPosition(_ y: Double) {
        mutate([.captionOptions(posY: min(LayoutConstants.captionPosYMax, max(LayoutConstants.captionPosYMin, y)))])
    }

    /// R10: CapCut "auto-highlight keywords" — toggles the highlight list between empty
    /// and a heuristic pick of significant transcript words (5+ chars, non-stopword).
    func toggleKeywordHighlight() {
        guard let d = session?.draft else { return }
        if !d.captionOptions.highlightWords.isEmpty {
            mutate([.captionOptions(highlightWords: [])]); return
        }
        let stop: Set<String> = ["the","and","that","this","with","your","you","for","are","was",
                                 "have","has","not","but","all","can","will","just","what","when",
                                 "they","them","from","into","about","would","could","should","been",
                                 "here","there","then","than","were","who","how","why","our","out",
                                 "get","got","one","two","more","most","some","like","really"]
        var picks: [String] = [], seen = Set<String>()
        for w in words {
            let n = w.text.lowercased().filter { $0.isLetter || $0.isNumber }
            if n.count >= 5, !stop.contains(n), !seen.contains(n) { seen.insert(n); picks.append(n) }
            if picks.count >= 12 { break }
        }
        guard !picks.isEmpty else { flashPublic("No keywords found to highlight."); return }
        mutate([.captionOptions(highlightWords: picks)])
    }

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

    // MARK: FP1 — speed / transitions / look / text stickers

    func setSpeed(_ segIdx: Int, _ speed: Double) {
        mutate([.segmentSpeed(segIdx, (speed * 10).rounded() / 10)],
               rejectMsg: "Speed must be between 0.5x and 3x.")
    }

    func setTransition(after segIdx: Int, style: String) {
        mutate([.transition(after: segIdx, style: style)])
    }

    /// R10: retime an existing transition (0.1–1.5s → 4–45 frames).
    func setTransitionDuration(after segIdx: Int, seconds: Double) {
        guard let t = session?.draft.transitions.first(where: { $0.afterSegment == segIdx }) else { return }
        let frames = min(45, max(4, Int((seconds * 30).rounded())))
        mutate([.transition(after: segIdx, style: t.style, frames: frames)])
    }

    func setFilter(_ name: String?) {
        mutate([.filter(name)])
        filterIntensityDraft = 1.0
    }

    /// R10: filter strength (0–1) — re-emits the look op with the new intensity.
    func setFilterIntensity(_ v: Double) {
        guard let f = session?.draft.look.filter else { return }
        mutate([.filter(f, intensity: v)])
    }

    func setAdjust(brightness: Double? = nil, contrast: Double? = nil, saturation: Double? = nil,
                   temperature: Double? = nil, vignette: Double? = nil) {
        mutate([.adjust(brightness: brightness, contrast: contrast, saturation: saturation,
                        temperature: temperature, vignette: vignette)])
    }

    /// "Add text" (TikTok text tool): a 3s sticker at the playhead, upper third, then
    /// selected so the canvas drag/pinch + styling controls are immediately live.
    func addTextSticker(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty, let (a, b) = insertWindow(len: 90) else { return }
        mutate([.addTextSticker(a, b, text: t)])
        if let idx = session?.draft.overlays.lastIndex(where: { $0.type == "text_sticker" && $0.srcIn == a }) {
            selectedSeg = nil
            selectedOverlay = idx
        }
    }

    // MARK: R10 — keyboard-first text + canvas corner handles

    /// "Add text" (keyboard-first): drop a draft sticker at the playhead, select it, and
    /// focus an on-canvas TextField immediately — no detour through a dialog (CapCut/TikTok).
    func startTextEntry() {
        player?.pause()
        guard let (a, b) = insertWindow(len: 90) else { return }
        mutate([.addTextSticker(a, b, text: "Text")])
        guard let idx = session?.draft.overlays.lastIndex(where: { $0.type == "text_sticker" && $0.srcIn == a }) else { return }
        selectedSeg = nil; selectedOverlay = idx
        beginTypingSticker(idx, seed: "")   // start empty → placeholder shows; blank discards
    }

    /// Re-enter typing on an existing sticker (corner ✎).
    func beginTypingSticker(_ idx: Int, seed: String? = nil) {
        guard let o = session?.draft.overlays[safe: idx], o.type == "text_sticker" else { return }
        editDraft = seed ?? o.text
        typingSticker = idx
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 120_000_000)
            stickerFieldFocused = true
        }
    }

    /// Commit the live-typed text; empty text removes the sticker (CapCut discards blank text).
    func commitTyping(_ idx: Int) {
        typingSticker = nil
        stickerFieldFocused = false
        let t = editDraft.trimmingCharacters(in: .whitespaces)
        if t.isEmpty { deleteOverlay(idx); return }
        if session?.draft.overlays[safe: idx]?.text != t { mutate([.editOverlayText(index: idx, text: t)]) }
    }

    /// Duplicate a text sticker slightly offset (corner ⧉).
    func duplicateSticker(_ idx: Int) {
        guard let o = session?.draft.overlays[safe: idx], o.type == "text_sticker" else { return }
        mutate([.addTextSticker(o.srcIn, o.srcOut, text: o.text,
                                posX: min(0.9, o.posX + 0.05), posY: min(0.9, o.posY + 0.05))])
        if let ni = session?.draft.overlays.lastIndex(where: { $0.type == "text_sticker" }) {
            selectedOverlay = ni
        }
    }

    /// Canvas drag end → one position op (one gesture = one undo step).
    func commitStickerMove(_ idx: Int, x: Double, y: Double) {
        mutate([.editSticker(index: idx, posX: x, posY: y)])
    }

    func commitStickerScale(_ idx: Int, scale: Double) {
        mutate([.editSticker(index: idx, scale: scale)])
    }

    func setStickerStyle(_ idx: Int, color: String? = nil, bg: String? = nil, font: String? = nil) {
        mutate([.editSticker(index: idx, color: color, bg: bg, font: font)])
    }

    /// Video canvas gesture end → one transform op for that clip.
    func commitVideoTransform(_ segIdx: Int, scale: Double? = nil,
                              offX: Double? = nil, offY: Double? = nil) {
        mutate([.segmentTransform(segIdx, scale: scale, offX: offX, offY: offY)])
    }

    /// Caption canvas drag end → pos_y op; pinch end → scale op.
    func commitCaptionPosY(_ y: Double) {
        mutate([.captionOptions(posY: y)])
    }

    func commitCaptionScale(_ s: Double) {
        mutate([.captionOptions(scale: s)])
    }

    // MARK: FP1c — media rolls (B-roll/C-roll…)

    func addStockRoll(_ query: String) {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        // Replace flow: swap the source in the existing roll's window.
        if let ri = replacingRoll, let r = session?.draft.broll[safe: ri] {
            replacingRoll = nil
            mutate([.removeBroll(r.srcIn, r.srcOut), .addBroll(r.srcIn, r.srcOut, query: q)])
            selectLastRoll(); return
        }
        guard let (aF, bF) = insertWindow(len: 90) else { return }
        mutate([.addBroll(aF, bF, query: q)], rejectMsg: "Couldn't add that clip here.")
        selectLastRoll()
    }

    /// Photo/video from the library → save → upload → own-media roll at the playhead.
    func importRollMedia(_ item: PhotosPickerItem) async {
        uploadingMedia = true
        defer { uploadingMedia = false; mediaPickerItem = nil }
        guard let data = try? await item.loadTransferable(type: Data.self) else {
            flashPublic("Couldn't load that media — try another.")
            return
        }
        let isVideo = item.supportedContentTypes.contains { $0.conforms(to: .movie) }
        let ext = isVideo ? "mov" : "jpg"
        let path = MediaStore.save(data, ext: ext)
        guard let url = await LiveClipEngine.uploadMedia(path: path, filename: "roll.\(ext)") else {
            flashPublic("Couldn't upload that media — check your connection.")
            return
        }
        // Replace flow: swap the source in the existing roll's window.
        if let ri = replacingRoll, let r = session?.draft.broll[safe: ri] {
            replacingRoll = nil
            mutate([.removeBroll(r.srcIn, r.srcOut), .addMediaRoll(r.srcIn, r.srcOut, url: url)])
            localMediaPreviews[url] = path; selectLastRoll()
            withAnimation(.easeOut(duration: 0.15)) { showMediaPanel = false }
            return
        }
        guard let (aF, bF) = insertWindow(len: 90) else { return }
        mutate([.addMediaRoll(aF, bF, url: url)])
        localMediaPreviews[url] = path              // instant canvas preview
        selectLastRoll()
        withAnimation(.easeOut(duration: 0.15)) { showMediaPanel = false }
    }

    private func selectLastRoll() {
        if let idx = session?.draft.broll.indices.last {
            selectedSeg = nil; selectedOverlay = nil; selectedBoundary = nil
            selectedBroll = idx
        }
    }

    func deleteRoll(_ idx: Int) {
        guard let r = session?.draft.broll[safe: idx] else { return }
        mutate([.removeBroll(r.srcIn, r.srcOut)])
        selectedBroll = nil
    }

    /// Grow/shrink a roll's tail — expressed as remove+re-add (no edit op exists).
    func adjustRoll(_ idx: Int, deltaFrames: Int) {
        guard let r = session?.draft.broll[safe: idx] else { return }
        let extent = session?.draft.segments.map(\.srcOut).max() ?? r.srcOut
        let newOut = min(extent, max(r.srcIn + 15, r.srcOut + deltaFrames))
        guard newOut != r.srcOut else { flashPublic("That's as short as a roll gets."); return }
        let readd: WireOp = (r.source == "own_media" && r.resolvedURL != nil)
            ? .addMediaRoll(r.srcIn, newOut, url: r.resolvedURL!)
            : .addBroll(r.srcIn, newOut, query: r.cueText)
        mutate([.removeBroll(r.srcIn, r.srcOut), readd])
        selectLastRoll()
    }

    /// Drag-trim a roll edge (bracket handle) — retrim the window as remove+re-add,
    /// carrying the roll's local preview path across so the sim keeps showing it.
    func trimRoll(_ idx: Int, edge: TrimEdge, deltaFrames: Int) {
        guard let r = session?.draft.broll[safe: idx] else { return }
        let extent = session?.draft.segments.map(\.srcOut).max() ?? r.srcOut
        var a = r.srcIn, b = r.srcOut
        if edge == .leading { a = max(0, min(b - 15, r.srcIn + deltaFrames)) }
        else { b = min(extent, max(a + 15, r.srcOut + deltaFrames)) }
        guard a != r.srcIn || b != r.srcOut else { return }
        readdRoll(r, a: a, b: b)
    }

    /// Duplicate a roll one window-length later (CapCut Duplicate).
    func duplicateRoll(_ idx: Int) {
        guard let r = session?.draft.broll[safe: idx] else { return }
        let extent = session?.draft.segments.map(\.srcOut).max() ?? r.srcOut
        let len = r.srcOut - r.srcIn
        let a = min(extent - 15, r.srcOut), b = min(extent, r.srcOut + len)
        guard b > a else { flashPublic("No room to duplicate here."); return }
        let op: WireOp = (r.source == "own_media" && r.resolvedURL != nil)
            ? .addMediaRoll(a, b, url: r.resolvedURL!) : .addBroll(a, b, query: r.cueText)
        mutate([op])
        selectLastRoll()
    }

    /// Replace a roll's source — reopen the media panel remembering which roll to swap.
    func replaceRoll(_ idx: Int) {
        replacingRoll = idx
        player?.pause()
        withAnimation(.easeOut(duration: 0.18)) { showMediaPanel = true }
    }

    /// Shared remove+re-add for a roll at a new [a,b), preserving own-media preview.
    private func readdRoll(_ r: EditorBroll, a: Int, b: Int) {
        let readd: WireOp = (r.source == "own_media" && r.resolvedURL != nil)
            ? .addMediaRoll(a, b, url: r.resolvedURL!) : .addBroll(a, b, query: r.cueText)
        mutate([.removeBroll(r.srcIn, r.srcOut), readd])
        selectLastRoll()
    }

    /// flash() is private to this file's little world; a public door for panel flows.
    func flashPublic(_ msg: String) {
        transient = msg
        Task { try? await Task.sleep(nanoseconds: 2_500_000_000); if transient == msg { transient = nil } }
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
    // MARK: Overlay chip-lane actions

    /// Delete exactly this overlay (remove_overlays scoped to its kind + exact frames —
    /// an overlapping sibling of the same kind at the same span would go too; acceptable).
    func deleteOverlay(_ idx: Int) {
        guard let o = session?.draft.overlays[safe: idx] else { return }
        mutate([.removeOverlay(kind: o.type, o.srcIn, o.srcOut)])
        selectedOverlay = nil
    }

    func beginOverlayTextEdit(_ idx: Int) {
        guard let o = session?.draft.overlays[safe: idx],
              o.type == "text_card" || o.type == "text_sticker" else { return }
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

    // MARK: R10 — filler / pause cleanup (transcript-driven bulk cut)

    struct CleanupTarget: Identifiable {
        let id: Int; let label: String; let detail: String
        let srcIn: Int; let srcOut: Int; let kind: String   // filler | pause
    }

    /// Detect filler words + long pauses from the transcript word timings.
    ///
    /// Formatting fix #10: mirrors backend/app/edl.py's ALWAYS_FILLERS / DISCOURSE_MARKERS /
    /// strip_fillers clause-boundary rule (not byte-identical — the goal is correctness, not
    /// parity). The old version cut every listed word UNCONDITIONALLY, which flagged real
    /// content ("turn RIGHT here", "I feel like it WORKS") as filler. Now:
    ///   - ALWAYS-fillers (pure hesitation sounds) are cut wherever they appear;
    ///   - DISCOURSE markers are cut ONLY at a clause boundary — the first transcript word,
    ///     after a real pause (≥ clauseGapFrames since the previous word's end), or right
    ///     after another word already flagged as filler (so "um, so, ..." chains cut whole);
    ///   - "you" + "know" (small gap) is flagged as a bigram (the "you know" discourse tic);
    ///     either word alone is content and is never flagged.
    func cleanupTargets() -> [CleanupTarget] {
        // Pure hesitation sounds — never real words, safe to cut wherever they appear.
        let alwaysFillers: Set<String> = ["um", "uh", "uhh", "er", "erm", "hmm", "mmm"]
        // Filler ONLY when it opens a clause; otherwise legitimate content.
        let discourseMarkers: Set<String> = ["like", "so", "basically", "literally", "actually",
                                             "right", "okay", "ok", "yeah", "yep", "well"]
        // ~267ms @ 30fps (kEditorFPS) — a word starting this long after the previous one's
        // end reads as a fresh clause, not a continuation of the same breath.
        let clauseGapFrames = 8

        var out: [CleanupTarget] = []
        var id = 0
        let sorted = words.sorted { $0.startFrame < $1.startFrame }
        var prevWasFiller = false
        for (i, w) in sorted.enumerated() {
            let norm = w.text.lowercased().filter { $0.isLetter }
            let gapBefore: Int? = i > 0 ? w.startFrame - sorted[i - 1].endFrame : nil
            let clauseBoundary = i == 0 || prevWasFiller || (gapBefore.map { $0 >= clauseGapFrames } ?? false)

            var isFiller = alwaysFillers.contains(norm)
                || (discourseMarkers.contains(norm) && clauseBoundary)

            // "you know" bigram: flag BOTH words only when they're adjacent with a small
            // gap. A standalone "you" or "know" is content and must not be flagged.
            if !isFiller, norm == "you", i + 1 < sorted.count {
                let next = sorted[i + 1]
                let nextNorm = next.text.lowercased().filter { $0.isLetter }
                if nextNorm == "know", next.startFrame - w.endFrame < clauseGapFrames { isFiller = true }
            }
            if !isFiller, norm == "know", i > 0 {
                let prev = sorted[i - 1]
                let prevNorm = prev.text.lowercased().filter { $0.isLetter }
                if prevNorm == "you", w.startFrame - prev.endFrame < clauseGapFrames { isFiller = true }
            }

            if isFiller {
                out.append(CleanupTarget(id: id, label: "“\(w.text)”", detail: "filler word",
                                         srcIn: w.startFrame, srcOut: w.endFrame, kind: "filler")); id += 1
            }
            prevWasFiller = isFiller

            if i + 1 < sorted.count {
                let gap = sorted[i + 1].startFrame - w.endFrame
                if gap > 24 {   // > ~0.8s of silence
                    out.append(CleanupTarget(id: id, label: "Pause",
                                             detail: String(format: "%.1fs of silence", Double(gap) / 30.0),
                                             srcIn: w.endFrame, srcOut: sorted[i + 1].startFrame, kind: "pause")); id += 1
                }
            }
        }
        return out
    }

    /// Bulk-cut every selected target as ONE undo step (over-cuts that would leave too little
    /// footage are skipped by the engine).
    func applyCleanup() {
        let keep = cleanupTargets().filter { !cleanupSkip.contains($0.id) }
        guard !keep.isEmpty else { withAnimation { showCleanup = false }; return }
        let secs = Double(keep.reduce(0) { $0 + ($1.srcOut - $1.srcIn) }) / 30.0
        mutate(keep.map { WireOp.cut($0.srcIn, $0.srcOut) },
               rejectMsg: "Couldn't remove those — too little footage would remain.")
        withAnimation { showCleanup = false }
        showToast(String(format: "Removed %d · %.1fs", keep.count, secs))
    }

    var cleanupPanel: some View {
        let targets = cleanupTargets()
        let keep = targets.filter { !cleanupSkip.contains($0.id) }
        let secs = Double(keep.reduce(0) { $0 + ($1.srcOut - $1.srcIn) }) / 30.0
        return VStack(spacing: 0) {
            // Header was a ZStack (centered title overlaid by a trailing-Cancel HStack).
            // Flattening it to a plain HStack (below) removed a theoretical frame overlap,
            // but wasn't the actual bug — see the .accessibilityElement(children: .contain)
            // at the bottom of this view for the real root cause and fix.
            HStack {
                Text("Clean up").font(AppFont.headline).foregroundStyle(.white)
                Spacer()
                Button { withAnimation(.easeOut(duration: 0.15)) { showCleanup = false } } label: {
                    Text("Cancel").font(AppFont.headline).foregroundStyle(Palette.accent)
                        .padding(.horizontal, Space.md).padding(.vertical, 8).contentShape(Rectangle())
                }.buttonStyle(.plain).accessibilityIdentifier("editorPro.cleanup.cancel")
            }
            .padding(.horizontal, Space.sm).padding(.top, Space.lg).padding(.bottom, Space.sm)

            if targets.isEmpty {
                Spacer()
                Text("Nothing to clean up — no filler words or long pauses found.")
                    .font(AppFont.callout).foregroundStyle(.white.opacity(0.6))
                    .multilineTextAlignment(.center).padding(Space.xl)
                Spacer()
            } else {
                ScrollView {
                    VStack(spacing: 0) {
                        ForEach(targets) { t in
                            let on = !cleanupSkip.contains(t.id)
                            Button {
                                if on { cleanupSkip.insert(t.id) } else { cleanupSkip.remove(t.id) }
                            } label: {
                                HStack(spacing: Space.md) {
                                    Image(systemName: on ? "checkmark.circle.fill" : "circle")
                                        .foregroundStyle(on ? Palette.accent : .white.opacity(0.3))
                                    VStack(alignment: .leading, spacing: 1) {
                                        Text(t.label).font(AppFont.callout).foregroundStyle(.white)
                                            .strikethrough(on, color: .white.opacity(0.5))
                                        Text(t.detail).font(AppFont.caption).foregroundStyle(.white.opacity(0.5))
                                    }
                                    Spacer()
                                    Image(systemName: t.kind == "pause" ? "pause.circle" : "waveform")
                                        .foregroundStyle(.white.opacity(0.3))
                                }
                                .padding(.horizontal, Space.lg).padding(.vertical, 10).contentShape(Rectangle())
                            }.buttonStyle(.plain)
                            Rectangle().fill(Color.white.opacity(0.08)).frame(height: 0.5).padding(.leading, Space.lg)
                        }
                    }.padding(.vertical, Space.sm)
                }
                Button { applyCleanup() } label: {
                    Text(keep.isEmpty ? "Select something to remove"
                                      : String(format: "Remove %d · %.1fs", keep.count, secs))
                        .font(AppFont.headline).foregroundStyle(Palette.night)
                        .frame(maxWidth: .infinity).frame(height: 48)
                        .background(keep.isEmpty ? Color.white.opacity(0.2) : Color.white).clipShape(Capsule())
                }.buttonStyle(.plain).disabled(keep.isEmpty).padding(Space.md)
                    .accessibilityIdentifier("editorPro.cleanup.apply")
            }
        }
        .frame(height: 340, alignment: .top)
        .frame(maxWidth: .infinity)
        .background(Palette.ink.opacity(0.6))
        // Root cause of editorPro.cleanup.cancel never surfacing (confirmed via a live
        // `maestro hierarchy` dump while this panel was on screen): without an explicit
        // .accessibilityElement(children:), applying .accessibilityIdentifier directly to
        // a plain container broadcasts that SAME identifier onto every one of its
        // flattened descendant accessibility elements — the dump showed the title Text,
        // the Cancel Button, AND the "Nothing to clean up" message ALL reporting
        // resource-id "editorPro.cleanupPanel", clobbering the Button's own identifier
        // even though it was set correctly in code. `.accessibilityElement(children:
        // .contain)` makes this VStack a real container instead, so each child's own
        // identifier surfaces on its own — verified by re-dumping the hierarchy after
        // adding this line (editorPro.cleanup.cancel then showed up correctly) and by a
        // full .maestro/format-audit.yaml re-run.
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("editorPro.cleanupPanel")
    }

    // MARK: Save (flatten op log → one tweak POST → per-clip poll → reload)

    func save() {
        guard let session, session.isDirty, let jobId = clip.jobId, applyTask == nil else { dismiss(); return }
        let ops = session.flattenedOps()
        // defer_render is ONLY safe when the delivered MP4 is byte-for-byte unchanged.
        // A pure split qualifies (it just adds a cut point; the same frames play in the
        // same order). Cuts, restores, reorders, mutes and volume changes all change the
        // output — deferring their render leaves the Library playing AND publishing the
        // pre-edit video (audit #6/#43). Only defer a split-only batch.
        let structural = !ops.isEmpty && ops.allSatisfy { ($0["type"] as? String) == "split_segment" }
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
            if editorRecoverable {
                // Re-create the edit from the local take (store.retryClipJob re-uploads +
                // starts a fresh job in place when the server lost this one), then close so
                // the Library shows it re-processing.
                Button("Re-create this edit") {
                    Task { await store.retryClipJob(clip) }
                    dismiss()
                }.tint(Palette.accent).font(AppFont.callout.weight(.semibold))
                Button("Close") { dismiss() }.tint(.white.opacity(0.5))
            } else {
                Button("Close") { dismiss() }.tint(Palette.accent)
            }
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

    /// A7 feature #1: the style-bundle picker. Tapping a theme calls the SEPARATE
    /// /retheme endpoint (not a tweak op) — it never touches the local op log,
    /// since it only restamps caption/grade/duck and re-renders directly.
    var themeSheet: some View {
        NavigationStack {
            List {
                ForEach(themes) { t in
                    Button {
                        showThemeSheet = false
                        retheme(to: t.id)
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(t.label).font(.system(size: 15, weight: .semibold))
                                if t.id == activeThemeId {
                                    Image(systemName: "checkmark.circle.fill").foregroundStyle(Palette.accent)
                                }
                            }
                            Text(t.blurb).font(.system(size: 12)).foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityIdentifier("editorPro.theme.\(t.id)")
                }
            }.navigationTitle("Theme").navigationBarTitleDisplayMode(.inline)
        }.presentationDetents([.medium, .large])
    }
}

enum TrimEdge { case leading, trailing }

extension Array {
    subscript(safe i: Int) -> Element? { indices.contains(i) ? self[i] : nil }
}
