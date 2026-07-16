import SwiftUI

// MARK: - EditorTimeline — the direct-manipulation timeline: a fixed CENTER playhead over an
// offset-driven filmstrip (no nested ScrollView — SwiftUI scroll momentum can't be pixel-synced
// to playback and would fight the trim/reorder drags). Clips render as proportional blocks with
// filmstrip thumbnails; tap selects, drag scrubs, edge handles trim, long-press enters reorder.

struct EditorTimeline: View {
    let document: EditorDocument
    let player: EditorPlayerController?
    let filmstrip: FilmstripCache?
    @Binding var pointsPerSecond: CGFloat
    @Binding var selectedSeg: Int?
    @Binding var selectedOverlay: Int?     // chip-lane selection (mutually exclusive with selectedSeg)
    let onTrim: (Int, TrimEdge, Int) -> Void
    let onReorder: ([Int]) -> Void
    // Track lanes (CapCut layout: captions under video, then effects, then audio lanes).
    var phrases: [CaptionPhrase] = []
    var captionsOn: Bool = false
    var musicName: String? = nil           // nil = no music set
    var musicVolume: Double = 0.15
    var showMusicAdd: Bool = false         // empty-lane "+ Add sound" affordance (sound mode)
    var onTapPhrase: (CaptionPhrase) -> Void = { _ in }
    var onTapMusic: () -> Void = {}
    var onTapVoice: (Int) -> Void = { _ in }
    // Transition diamonds at clip boundaries (CapCut's between-clips square): tapping
    // one selects that boundary; the context strip offers the dip styles.
    var selectedBoundary: Int? = nil          // source segIdx of the LEADING clip
    var onTapBoundary: (Int) -> Void = { _ in }
    // Media rolls (B-roll/C-roll…) + the "+" add-media tile at the track end.
    var selectedBroll: Int? = nil
    var onTapBroll: (Int) -> Void = { _ in }
    var onTapAddMedia: () -> Void = {}
    var showRollsAdd: Bool = false            // empty-lane "+ Add b-roll" affordance
    var rollThumbs: [String: String] = [:]    // roll url → local file path (own-media preview)
    var onTrimRoll: (Int, TrimEdge, Int) -> Void = { _, _, _ in }   // drag-trim a roll window
    var showVoice: Bool = true                // R10: collapse the voice lane when idle

    @State private var dragBaseOffset: CGFloat?
    @GestureState private var pinch: CGFloat = 1
    // Live trim rubber-band: the in-flight drag's effect, applied to the selected cell's
    // width + a floating duration badge, committed as ONE op on release.
    @State private var trimPreview: (segIdx: Int, edge: TrimEdge, deltaFrames: Int)?
    // Scrub snapping: haptic tick when the playhead locks onto a clip boundary.
    @State private var snapTick = 0
    @State private var lastSnapIndex: Int? = nil

    // Play-order clips = (sourceSegmentIndex, KEPT bounds after drops, kept frame count, speed).
    // Using kept bounds (not raw srcIn/srcOut) means a trim — which drops an interior range
    // without moving the segment boundary — visibly shrinks the cell + its duration label.
    // Cell WIDTH reflects OUTPUT duration (kept/speed) so a sped-up clip visibly shortens.
    private var clips: [(segIdx: Int, srcIn: Int, srcOut: Int, keptFrames: Int, speed: Double)] {
        let order = document.segmentOrder ?? Array(document.segments.indices)
        return order.compactMap { idx in
            guard let kb = document.keptBounds(ofSegment: idx) else { return nil }   // fully-dropped → hidden
            return (idx, kb.first, kb.last, kb.frames, min(3.0, max(0.5, document.segments[idx].speed)))
        }
    }

    private var totalSeconds: Double { document.outputSeconds }
    private func width(_ frames: Int) -> CGFloat { CGFloat(framesToSeconds(frames)) * pointsPerSecond }
    private func width(_ frames: Int, speed: Double) -> CGFloat {
        CGFloat(framesToSeconds(outputFrames(frames, speed: speed))) * pointsPerSecond
    }

    /// During a LEADING-edge trim drag, shift the whole content strip so the DRAGGED edge tracks
    /// the finger and the trailing edge + every downstream clip stay visually stationary. The clip
    /// cell lives in a left-anchored HStack, so a width change alone pins the LEFT edge and moves
    /// the RIGHT edge (and all following clips) — the "extends/detracts from both sides" bug. A
    /// trailing drag already moves the correct (dragged) edge, so it needs no shift (returns 0).
    private var trimContentShift: CGFloat {
        guard let t = trimPreview, t.edge == .leading,
              let c = clips.first(where: { $0.segIdx == t.segIdx }) else { return 0 }
        let baseW = max(30, width(c.keptFrames, speed: c.speed))
        let prevW = max(30, width(previewFrames(segIdx: c.segIdx, base: c.keptFrames,
                                                srcIn: c.srcIn, srcOut: c.srcOut), speed: c.speed))
        return baseW - prevW      // >0 (inward trim) shifts content right so the right edge holds
    }

    var body: some View {
        GeometryReader { geo in
            let mid = geo.size.width / 2
            let playheadOffset = CGFloat(player?.currentOutputTime ?? 0) * pointsPerSecond
            ZStack(alignment: .leading) {
                // UX-9: the ruler and the clips scroll TOGETHER under the fixed playhead —
                // the ruler used to stay pinned, so its tick marks lied about position.
                VStack(alignment: .leading, spacing: 2) {
                    ruler(width: geo.size.width)
                    HStack(spacing: 3) {
                        ForEach(Array(clips.enumerated()), id: \.offset) { pos, c in
                            clipCell(pos: pos, segIdx: c.segIdx, srcIn: c.srcIn, srcOut: c.srcOut, keptFrames: c.keptFrames, speed: c.speed)
                        }
                        addMediaTile
                    }
                    .overlay(alignment: .topLeading) { transitionDiamonds }
                    // CapCut lane order: captions under video, then effects, then media
                    // rolls (B/C/D…), then the audio lanes.
                    if captionsOn, !phrases.isEmpty { captionLane }
                    if !document.overlays.isEmpty { overlayLane }   // zoom blocks + text cards
                    if !document.broll.isEmpty { rollsLane }         // media rolls, stacked on overlap
                    else if showRollsAdd { rollsAddLane }            // empty lane advertises itself
                    if showVoice { voiceLane }                       // original voice (collapses when idle)
                    if musicName != nil || showMusicAdd { musicLane }
                }
                .padding(.horizontal, mid)     // lets first/last clip reach the center playhead
                .offset(x: -playheadOffset + trimContentShift)   // scroll + leading-trim rubber-band
                laneGutter                     // CapCut track heads, pinned at the left edge
                // Fixed center playhead
                Rectangle().fill(Palette.accent).frame(width: 2)
                    .frame(maxHeight: .infinity).offset(x: mid - 1)
            }
            .contentShape(Rectangle())
            .gesture(scrubGesture(mid: mid))
            .gesture(MagnificationGesture().updating($pinch) { v, s, _ in s = v }
                .onChanged { v in pointsPerSecond = max(8, min(60, pointsPerSecond * v / max(pinch, 0.01))) })
            // Double-tap the timeline background → reset zoom to the default scale.
            .onTapGesture(count: 2) { withAnimation(.easeOut(duration: 0.2)) { pointsPerSecond = 18 } }
            // UX-8: tapping empty timeline space clears the selection (clip cells' own
            // tap gestures win when a clip is hit).
            .onTapGesture {
                if selectedSeg != nil || selectedOverlay != nil {
                    withAnimation(.easeOut(duration: 0.15)) { selectedSeg = nil; selectedOverlay = nil }
                }
            }
            .sensoryFeedback(.selection, trigger: snapTick)
        }
    }

    /// One diamond per clip boundary, centered on the seam. Filled accent when that
    /// boundary carries a transition; hollow otherwise. Tap → boundary selection.
    @ViewBuilder private var transitionDiamonds: some View {
        let cs = clips
        ZStack(alignment: .topLeading) {
            Color.clear.frame(width: 1, height: 1)
            ForEach(0..<max(0, cs.count - 1), id: \.self) { i in
                let x = cs[0...i].reduce(CGFloat(0)) { acc, c in
                    acc + max(30, width(previewFrames(segIdx: c.segIdx, base: c.keptFrames,
                                                      srcIn: c.srcIn, srcOut: c.srcOut), speed: c.speed)) + 3
                } - 1.5
                let leading = cs[i].segIdx
                let has = document.transitions.contains { $0.afterSegment == leading }
                let selected = selectedBoundary == leading
                Button { onTapBoundary(leading) } label: {
                    Image(systemName: has ? "square.fill" : "square")
                        .font(.system(size: 8, weight: .bold))
                        .rotationEffect(.degrees(45))
                        .foregroundStyle(selected ? Palette.night : (has ? Palette.night : .white.opacity(0.8)))
                        .frame(width: 18, height: 18)
                        .background(Circle().fill(selected ? Palette.accent : (has ? Color(hex: 0xFFD60A) : Color.black.opacity(0.6))))
                        .overlay(Circle().strokeBorder(Color.white.opacity(0.5), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .offset(x: x - 9, y: 19)
                .accessibilityIdentifier("editorPro.boundary.\(i)")
            }
        }
    }

    private func ruler(width: CGFloat) -> some View {
        HStack(spacing: 0) {
            ForEach(0..<max(1, Int(totalSeconds / 3) + 1), id: \.self) { i in
                Text("\(i * 3)s").font(.system(size: 8)).foregroundStyle(.white.opacity(0.4))
                    .frame(width: 3 * pointsPerSecond, alignment: .leading)
            }
        }.frame(height: 12)
    }

    /// How far this clip's edge can GROW outward (restore previously-trimmed footage):
    /// the extent of the drop that abuts the edge; 0 when nothing was trimmed there. Clamped to
    /// the segment's OWN raw source bounds — the commit (ProEditorView+Actions.trim) never restores
    /// past seg.srcIn/srcOut, so a drop that crosses the segment boundary must not let the
    /// rubber-band overshoot what will actually commit.
    private func restorableFrames(edge: TrimEdge, srcIn: Int, srcOut: Int) -> Int {
        for d in document.drops {
            if edge == .leading, d.srcOut == srcIn {
                let segLo = document.segments.first(where: { $0.srcIn <= srcIn && srcIn <= $0.srcOut })?.srcIn ?? d.srcIn
                return srcIn - max(d.srcIn, segLo)
            }
            if edge == .trailing, d.srcIn == srcOut {
                let segHi = document.segments.first(where: { $0.srcIn <= srcOut && srcOut <= $0.srcOut })?.srcOut ?? d.srcOut
                return min(d.srcOut, segHi) - srcOut
            }
        }
        return 0
    }

    /// The clip's kept frame count with any in-flight trim drag applied — the rubber-band is
    /// HONEST: it clamps to exactly what the commit will produce (min 30 frames of clip;
    /// outward growth capped at the abutting trimmed footage, 0 when there's none).
    private func previewFrames(segIdx: Int, base: Int, srcIn: Int, srcOut: Int) -> Int {
        guard let t = trimPreview, t.segIdx == segIdx else { return base }
        let adjusted = t.edge == .leading ? base - t.deltaFrames : base + t.deltaFrames
        let maxGrow = base + restorableFrames(edge: t.edge, srcIn: srcIn, srcOut: srcOut)
        return min(maxGrow, max(30, adjusted))
    }

    @ViewBuilder private func clipCell(pos: Int, segIdx: Int, srcIn: Int, srcOut: Int, keptFrames: Int, speed: Double) -> some View {
        let frames = previewFrames(segIdx: segIdx, base: keptFrames, srcIn: srcIn, srcOut: srcOut)
        let trimming = trimPreview?.segIdx == segIdx
        let w = max(30, width(frames, speed: speed))
        let selected = selectedSeg == segIdx
        // I-7: dim the other clips when one is selected so the target is unmistakable.
        let dimmed = selectedSeg != nil && !selected
        ZStack {
            FilmstripThumbs(filmstrip: filmstrip, srcIn: srcIn, srcOut: srcOut, width: w)
                .frame(width: w, height: 56).clipped()
            RoundedRectangle(cornerRadius: 6).strokeBorder(selected ? Palette.accent : .white.opacity(0.15),
                                                           lineWidth: selected ? 2.5 : 1)
        }
        .frame(width: w, height: 56)
        .opacity(dimmed ? 0.55 : 1)
        // Duration label — every editor shows clip lengths; hide on slivers.
        .overlay(alignment: .bottomTrailing) {
            if w >= 44 {
                Text(String(format: "%.1fs", Double(outputFrames(frames, speed: speed)) / 30.0))
                    .font(.system(size: 8, weight: .semibold)).monospacedDigit()
                    .foregroundStyle(.white.opacity(0.9))
                    .padding(.horizontal, 4).padding(.vertical, 1.5)
                    .background(Color.black.opacity(0.45)).clipShape(Capsule())
                    .padding(3)
            }
        }
        // Speed badge — a retimed clip must say so (CapCut shows "2x" on the cell).
        .overlay(alignment: .topLeading) {
            if abs(speed - 1.0) > 0.01 {
                Text(String(format: speed.truncatingRemainder(dividingBy: 1) == 0 ? "%.0fx" : "%.1fx", speed))
                    .font(.system(size: 8, weight: .bold)).monospacedDigit()
                    .foregroundStyle(.black)
                    .padding(.horizontal, 4).padding(.vertical, 1.5)
                    .background(Color(hex: 0xFFD60A)).clipShape(Capsule())
                    .padding(3)
            }
        }
        // (Mute state now lives on the voice lane below — the audio is a visible object there,
        // so the video cell no longer doubles it with a badge.)
        // Floating duration badge while trimming — the live feedback that was missing.
        .overlay(alignment: edgeAlignment) {
            if trimming {
                Text(String(format: "%.1fs", Double(outputFrames(frames, speed: speed)) / 30.0))
                    .font(.system(size: 11, weight: .bold)).monospacedDigit()
                    .foregroundStyle(.black)
                    .padding(.horizontal, 7).padding(.vertical, 3)
                    .background(Palette.accent).clipShape(Capsule())
                    .offset(y: -40)
            }
        }
        .overlay(alignment: .leading) { if selected { trimHandle(.leading, segIdx: segIdx, srcIn: srcIn, srcOut: srcOut) } }
        .overlay(alignment: .trailing) { if selected { trimHandle(.trailing, segIdx: segIdx, srcIn: srcIn, srcOut: srcOut) } }
        .onTapGesture {
            player?.pause()          // #10: freeze the playhead so Split cuts where they see
            withAnimation(.easeOut(duration: 0.15)) {
                selectedOverlay = nil
                selectedSeg = (selectedSeg == segIdx) ? nil : segIdx
            }
        }
        // Same accessibilityIdentifier-leak fix as cleanupPanel (ProEditorView+Actions.swift):
        // when selected, the leading/trailing trimHandle overlays have their own
        // "editorPro.trimHandle.left/right" identifiers — without .accessibilityElement
        // (children: .contain) those get clobbered by this cell's own identifier.
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("editorPro.clip.\(pos)")
    }

    private var edgeAlignment: Alignment {
        (trimPreview?.edge == .leading) ? .topLeading : .topTrailing
    }

    /// CapCut's track heads: a fixed icon at the screen's left edge names each
    /// lane while its content scrolls underneath. Mirrors the lane stack's
    /// conditionals + heights exactly (VStack spacing 2).
    private var laneGutter: some View {
        VStack(spacing: 2) {
            Color.clear.frame(width: 14, height: 12)                       // ruler
            gutterIcon("film").frame(height: 56)
            if captionsOn, !phrases.isEmpty { gutterIcon("captions.bubble").frame(height: 18) }
            if !document.overlays.isEmpty { gutterIcon("sparkles").frame(height: 20) }
            if !document.broll.isEmpty { gutterIcon("photo.on.rectangle").frame(height: CGFloat(rollsRows) * 18) }
            else if showRollsAdd { gutterIcon("photo.on.rectangle").frame(height: 18) }
            if showVoice { gutterIcon("waveform").frame(height: 16) }
            if musicName != nil || showMusicAdd { gutterIcon("music.note").frame(height: 16) }
        }
        .padding(.leading, 3)
        .allowsHitTesting(false)
    }

    private func gutterIcon(_ name: String) -> some View {
        Image(systemName: name)
            .font(.system(size: 8, weight: .semibold))
            .foregroundStyle(.white.opacity(0.6))
            .frame(width: 15, height: 15)
            .background(Circle().fill(Color.black.opacity(0.55)))
    }

    /// CapCut's "+" at the end of the main track — the fast path to add media.
    private var addMediaTile: some View {
        Button(action: onTapAddMedia) {
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.white.opacity(0.12))
                .frame(width: 40, height: 56)
                .overlay(Image(systemName: "plus").font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("editorPro.addMedia")
    }

    // MARK: Media rolls lane — every photo/video window as a labeled strip
    // (B-roll, C-roll, …). Overlapping rolls stack onto a second row so each
    // stays visible and tappable (the TikTok multi-layer read).
    // Greedy row assignment: a roll overlapping the previous row's occupant
    // moves down one row (max 2 rows keeps the lane compact).
    private var rollPlacements: [(idx: Int, roll: EditorBroll, span: (start: Double, end: Double), row: Int)] {
        var rowEnd: [Double] = []
        var placed: [(idx: Int, roll: EditorBroll, span: (start: Double, end: Double), row: Int)] = []
        for (i, r) in document.broll.enumerated() {
            guard let span = document.outputSpan(srcIn: r.srcIn, srcOut: r.srcOut) else { continue }
            var row = 0
            while row < rowEnd.count, span.start < rowEnd[row] - 0.01 { row += 1 }
            row = min(row, 1)
            if row >= rowEnd.count { rowEnd.append(span.end) } else { rowEnd[row] = max(rowEnd[row], span.end) }
            placed.append((i, r, span, row))
        }
        return placed
    }
    private var rollsRows: Int { min(2, max(1, (rollPlacements.map(\.row).max() ?? 0) + 1)) }

    /// The empty rolls lane sells the feature (music-lane pattern): a dashed
    /// "+ Add b-roll" strip straight into the media panel.
    private var rollsAddLane: some View {
        AddLaneStrip(label: "Add b-roll", width: max(1, CGFloat(totalSeconds) * pointsPerSecond),
                     onTap: onTapAddMedia)
            .frame(height: 18, alignment: .topLeading)
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityIdentifier("editorPro.rollsLane.add")
    }

    private var rollsLane: some View {
        let placed = rollPlacements
        let rows = rollsRows
        return ZStack(alignment: .topLeading) {
            Color.clear.frame(width: max(1, CGFloat(totalSeconds) * pointsPerSecond),
                              height: CGFloat(rows) * 18)
            ForEach(placed, id: \.idx) { p in
                rollStrip(p.idx, p.roll, p.span)
                    .offset(x: CGFloat(p.span.start) * pointsPerSecond, y: CGFloat(p.row) * 18 + 1)
            }
        }
        .frame(height: CGFloat(rows) * 18, alignment: .topLeading)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func rollStrip(_ idx: Int, _ roll: EditorBroll, _ span: (start: Double, end: Double)) -> some View {
        let w = max(34, CGFloat(span.end - span.start) * pointsPerSecond - 1.5)
        let selected = selectedBroll == idx
        let letter = String(UnicodeScalar(UInt8(66 + min(idx, 24))))   // B, C, D, …
        // Own-media rolls show the imported frame as the strip fill (CapCut overlay-track
        // read); stock rolls keep the amber tint + film icon + cue text.
        let thumbPath = roll.resolvedURL.flatMap { rollThumbs[$0] }
        return ZStack {
            if let thumbPath {
                RollThumb(path: thumbPath).frame(width: w, height: 16).clipped()
                Color.black.opacity(0.15)
            } else {
                Color(hex: 0xB56635).opacity(selected ? 1 : 0.9)
            }
            HStack(spacing: 3) {
                Image(systemName: roll.source == "own_media" ? "photo.fill" : "film.fill")
                    .font(.system(size: 8, weight: .semibold))
                Text(w > 64 ? "\(letter)-roll" : letter).font(.system(size: 9, weight: .bold))
                if w > 120, thumbPath == nil, !roll.cueText.isEmpty {
                    Text(roll.cueText).font(.system(size: 8)).lineLimit(1).opacity(0.8)
                }
                Spacer(minLength: 0)
            }
            .foregroundStyle(.white).shadow(radius: thumbPath != nil ? 2 : 0)
            .padding(.horizontal, 5).frame(width: w, alignment: .leading)
        }
        .frame(width: w, height: 16, alignment: .leading)
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .overlay(RoundedRectangle(cornerRadius: 4)
            .strokeBorder(selected ? Color(hex: 0x30D6C4) : Color.white.opacity(0.15),
                          lineWidth: selected ? 2 : 0.5))
        // Selected rolls get real bracket handles — drag to retrim the window (CapCut).
        .overlay(alignment: .leading) { if selected { rollTrimHandle(.leading, idx: idx) } }
        .overlay(alignment: .trailing) { if selected { rollTrimHandle(.trailing, idx: idx) } }
        .onTapGesture { onTapBroll(idx) }
        .accessibilityIdentifier("editorPro.roll.\(idx)")
    }

    /// A roll's edge bracket — commits a retrim on release (no live rubber-band on the
    /// thin strip; the clip trim keeps the full rubber-band).
    private func rollTrimHandle(_ edge: TrimEdge, idx: Int) -> some View {
        TrimBracket(edge: edge, height: 16)
            .contentShape(Rectangle().inset(by: -16))
            .highPriorityGesture(
                DragGesture()
                    .onEnded { g in
                        onTrimRoll(idx, edge, secondsToFrame(Double(g.translation.width / pointsPerSecond)))
                    })
            .accessibilityIdentifier("editorPro.rollTrim.\(edge == .leading ? "left" : "right")")
    }

    // MARK: Caption track — one white phrase clip per caption group (CapCut's text lane).
    private var captionLane: some View {
        ZStack(alignment: .topLeading) {
            Color.clear.frame(width: max(1, CGFloat(totalSeconds) * pointsPerSecond), height: 18)
            ForEach(phrases) { p in
                if let span = document.outputSpan(srcIn: p.startFrame, srcOut: p.endFrame) {
                    CaptionClipStrip(phrase: p, span: span, pointsPerSecond: pointsPerSecond) { onTapPhrase(p) }
                        .offset(y: 1)
                }
            }
        }
        .frame(height: 18, alignment: .topLeading)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityIdentifier("editorPro.captionLane")
    }

    // MARK: Voice track — the original audio, mirrored clip-for-clip as waveform strips so
    // mute/low-volume states are visible objects (not just a badge on the video cell).
    private var voiceLane: some View {
        HStack(spacing: 3) {
            ForEach(Array(clips.enumerated()), id: \.offset) { _, c in
                let frames = previewFrames(segIdx: c.segIdx, base: c.keptFrames, srcIn: c.srcIn, srcOut: c.srcOut)
                VoiceStrip(srcIn: c.srcIn, srcOut: c.srcOut,
                           width: max(30, width(frames, speed: c.speed)),
                           volume: effectiveVolume(srcIn: c.srcIn, srcOut: c.srcOut),
                           speechFrames: speechFrameSet)
                    .onTapGesture { onTapVoice(c.segIdx) }
            }
        }
        .accessibilityIdentifier("editorPro.voiceLane")
    }

    private var speechFrameSet: Set<Int> { Set(document.speechFrames) }

    private func effectiveVolume(srcIn: Int, srcOut: Int) -> Double {
        document.volumeRanges.filter { $0.srcIn <= srcIn && $0.srcOut >= srcOut }
            .map(\.volume).min() ?? 1.0
    }

    // MARK: Music track — a named teal strip spanning the cut; "+ Add sound" when empty.
    @ViewBuilder private var musicLane: some View {
        let w = max(1, CGFloat(totalSeconds) * pointsPerSecond)
        if let name = musicName {
            MusicStrip(name: name, width: w, volume: musicVolume, onTap: onTapMusic)
                .accessibilityIdentifier("editorPro.musicLane")
        } else {
            AddLaneStrip(label: "Add sound", width: w, onTap: onTapMusic)
                .accessibilityIdentifier("editorPro.musicLane.add")
        }
    }

    // MARK: Overlay chip lane — punch-ins/text cards become visible, tappable objects.
    // Chips sit at their OUTPUT-time position (mapped through kept intervals) so they stay
    // glued to the footage they decorate through cuts and reorders; fully-dropped footage
    // hides its chips.
    private var overlayLane: some View {
        ZStack(alignment: .topLeading) {
            Color.clear
                .frame(width: max(1, CGFloat(totalSeconds) * pointsPerSecond), height: 20)
            ForEach(Array(document.overlays.enumerated()), id: \.offset) { idx, o in
                if let span = document.outputSpan(srcIn: o.srcIn, srcOut: o.srcOut) {
                    overlayChip(idx: idx, overlay: o, span: span)
                }
            }
        }
        .frame(height: 20, alignment: .topLeading)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var zoomPurple: Color { Color(hex: 0x8B5CF6) }

    private func overlayChip(idx: Int, overlay o: EditorOverlay, span: (start: Double, end: Double)) -> some View {
        let w = max(26, CGFloat(span.end - span.start) * pointsPerSecond)
        let selected = selectedOverlay == idx
        return HStack(spacing: 3) {
            Image(systemName: o.type == "punch_in" ? "plus.magnifyingglass" : "textformat")
                .font(.system(size: 9, weight: .semibold))
            if o.type == "text_card", w > 54 {
                Text(String(o.text.prefix(8))).font(.system(size: 9, weight: .semibold)).lineLimit(1)
            }
        }
        // Zoom blocks read purple (the effect-block color: Screen Studio's zoom track,
        // CapCut effect clips); text cards stay neutral white.
        .foregroundStyle(selected ? .white : (o.type == "punch_in" ? zoomPurple : .white))
        .frame(width: w, height: 16)
        .background(
            RoundedRectangle(cornerRadius: 4)
                .fill(selected ? (o.type == "punch_in" ? zoomPurple : Palette.accent)
                               : (o.type == "punch_in" ? zoomPurple.opacity(0.25) : Color.white.opacity(0.16)))
        )
        .overlay(RoundedRectangle(cornerRadius: 4)
            .strokeBorder(selected ? (o.type == "punch_in" ? zoomPurple : Palette.accent) : Color.white.opacity(0.2),
                          lineWidth: selected ? 1.5 : 0.5))
        .offset(x: CGFloat(span.start) * pointsPerSecond, y: 2)
        .onTapGesture {
            withAnimation(.easeOut(duration: 0.15)) {
                selectedSeg = nil
                selectedOverlay = (selectedOverlay == idx) ? nil : idx
            }
        }
        .accessibilityIdentifier("editorPro.overlay.\(idx)")
    }

    private func trimHandle(_ edge: TrimEdge, segIdx: Int, srcIn: Int, srcOut: Int) -> some View {
        TrimBracket(edge: edge, height: 56)
            .contentShape(Rectangle().inset(by: -14))     // 44pt-ish hit target
            .highPriorityGesture(
                DragGesture()
                    .onChanged { g in
                        // Live rubber-band: the cell resizes + shows its new duration as you
                        // drag; nothing commits until release.
                        let deltaFrames = secondsToFrame(Double(g.translation.width / pointsPerSecond))
                        trimPreview = (segIdx, edge, deltaFrames)
                        // UX-5: the PICTURE follows the trim edge (independent of the playhead)
                        // so you see the exact frame you're cutting on — the trim feedback for
                        // talking-head content.
                        let candidate = edge == .leading ? srcIn + deltaFrames : srcOut + deltaFrames
                        player?.previewSourceSeconds(framesToSeconds(max(0, candidate)))
                    }
                    .onEnded { g in
                        trimPreview = nil
                        let deltaFrames = secondsToFrame(Double(g.translation.width / pointsPerSecond))
                        let newFrame = edge == .leading ? srcIn + deltaFrames : srcOut + deltaFrames
                        onTrim(segIdx, edge, newFrame)
                        // Snap the picture back to the composition playhead.
                        if let p = player { p.seek(toOutput: p.currentOutputTime) }
                    }
            )
            .accessibilityIdentifier("editorPro.trimHandle.\(edge == .leading ? "left" : "right")")
    }

    /// Output-timeline positions of every clip boundary (cut points) — snap targets.
    private var boundarySeconds: [Double] {
        var acc = 0.0
        var out: [Double] = [0]
        for c in clips {
            acc += framesToSeconds(outputFrames(c.keptFrames, speed: c.speed))
            out.append(acc)
        }
        return out
    }

    private func scrubGesture(mid: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 6)
            .onChanged { g in
                guard let player else { return }
                if dragBaseOffset == nil { dragBaseOffset = CGFloat(player.currentOutputTime) * pointsPerSecond; player.pause() }
                let base = dragBaseOffset ?? 0
                let newOffset = base - g.translation.width
                var target = Double(newOffset / pointsPerSecond)
                // Magnetic boundaries: within ~8pt of a cut point the playhead locks on,
                // with a selection tick the first time it engages (CapCut behavior — makes
                // split/trim at exact cut points effortless).
                let threshold = Double(8 / pointsPerSecond)
                if let (i, b) = boundarySeconds.enumerated().min(by: { abs($0.1 - target) < abs($1.1 - target) }),
                   abs(b - target) < threshold {
                    target = b
                    if lastSnapIndex != i { lastSnapIndex = i; snapTick += 1 }
                } else {
                    lastSnapIndex = nil
                }
                player.seek(toOutput: target)
            }
            .onEnded { _ in dragBaseOffset = nil; lastSnapIndex = nil }
    }
}

// CapCut trim bracket: a white rounded cap (rounded only on its outer edge) with a dark
// grip notch, used at the edges of a selected clip or roll.
struct TrimBracket: View {
    let edge: TrimEdge
    var height: CGFloat = 56
    var body: some View {
        ZStack {
            UnevenRoundedRectangle(
                topLeadingRadius: edge == .leading ? 5 : 0,
                bottomLeadingRadius: edge == .leading ? 5 : 0,
                bottomTrailingRadius: edge == .trailing ? 5 : 0,
                topTrailingRadius: edge == .trailing ? 5 : 0,
                style: .continuous)
                .fill(Color.white)
            Capsule().fill(Palette.night.opacity(0.55))
                .frame(width: 2, height: min(height - 6, 14))
        }
        .frame(width: 11, height: height)
    }
}

// A roll's own-media preview frame, loaded once from the local file.
struct RollThumb: View {
    let path: String
    @State private var img: UIImage?
    var body: some View {
        Group {
            if let img { Image(uiImage: img).resizable().aspectRatio(contentMode: .fill) }
            else { Color(hex: 0xB56635).opacity(0.9) }
        }
        .task(id: path) {
            if img == nil, let i = UIImage(contentsOfFile: MediaStore.url(for: path).path) { img = i }
        }
    }
}

// Renders the filmstrip thumbnails across a clip's source span (async-loaded, placeholder solid).
struct FilmstripThumbs: View {
    let filmstrip: FilmstripCache?
    let srcIn: Int
    let srcOut: Int
    let width: CGFloat
    @State private var images: [Int: UIImage] = [:]

    private var sampleSeconds: [Int] {
        let start = Int(framesToSeconds(srcIn)), end = max(start + 1, Int(framesToSeconds(srcOut)))
        let step = max(1, (end - start) / max(1, Int(width / 40)))
        return Array(stride(from: start, to: end, by: step))
    }

    var body: some View {
        HStack(spacing: 0) {
            ForEach(sampleSeconds, id: \.self) { sec in
                Group {
                    if let img = images[sec] { Image(uiImage: img).resizable().aspectRatio(contentMode: .fill) }
                    else { Palette.ink.opacity(0.7) }
                }
                .frame(maxWidth: .infinity).frame(height: 56).clipped()
            }
        }
        .task(id: "\(srcIn)-\(srcOut)-\(width)") {
            guard let filmstrip else { return }
            for sec in sampleSeconds {
                if let img = await filmstrip.thumbnail(atSourceSecond: Double(sec)) { images[sec] = img }
            }
        }
    }
}
