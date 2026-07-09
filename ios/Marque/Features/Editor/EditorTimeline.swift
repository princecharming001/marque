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

    @State private var dragBaseOffset: CGFloat?
    @GestureState private var pinch: CGFloat = 1
    // Live trim rubber-band: the in-flight drag's effect, applied to the selected cell's
    // width + a floating duration badge, committed as ONE op on release.
    @State private var trimPreview: (segIdx: Int, edge: TrimEdge, deltaFrames: Int)?
    // Scrub snapping: haptic tick when the playhead locks onto a clip boundary.
    @State private var snapTick = 0
    @State private var lastSnapIndex: Int? = nil

    // Play-order clips = (sourceSegmentIndex, kept source interval). Only un-fully-dropped segs show.
    private var clips: [(segIdx: Int, srcIn: Int, srcOut: Int)] {
        let order = document.segmentOrder ?? Array(document.segments.indices)
        return order.compactMap { idx in
            guard document.segments.indices.contains(idx) else { return nil }
            let s = document.segments[idx]
            return (idx, s.srcIn, s.srcOut)
        }
    }

    private var totalSeconds: Double { document.outputSeconds }
    private func width(_ frames: Int) -> CGFloat { CGFloat(framesToSeconds(frames)) * pointsPerSecond }

    var body: some View {
        GeometryReader { geo in
            let mid = geo.size.width / 2
            let playheadOffset = CGFloat(player?.currentOutputTime ?? 0) * pointsPerSecond
            ZStack(alignment: .leading) {
                // UX-9: the ruler and the clips scroll TOGETHER under the fixed playhead —
                // the ruler used to stay pinned, so its tick marks lied about position.
                VStack(spacing: 2) {
                    ruler(width: geo.size.width)
                    HStack(spacing: 3) {
                        ForEach(Array(clips.enumerated()), id: \.offset) { pos, c in
                            clipCell(pos: pos, segIdx: c.segIdx, srcIn: c.srcIn, srcOut: c.srcOut)
                        }
                    }
                    if !document.overlays.isEmpty { overlayLane }   // punch-ins/text cards as objects
                }
                .padding(.horizontal, mid)     // lets first/last clip reach the center playhead
                .offset(x: -playheadOffset)    // content scrolls under the fixed playhead
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

    private func ruler(width: CGFloat) -> some View {
        HStack(spacing: 0) {
            ForEach(0..<max(1, Int(totalSeconds / 3) + 1), id: \.self) { i in
                Text("\(i * 3)s").font(.system(size: 8)).foregroundStyle(.white.opacity(0.4))
                    .frame(width: 3 * pointsPerSecond, alignment: .leading)
            }
        }.frame(height: 12)
    }

    /// How far this clip's edge can GROW outward (restore previously-trimmed footage):
    /// the extent of the drop that abuts the edge; 0 when nothing was trimmed there.
    private func restorableFrames(edge: TrimEdge, srcIn: Int, srcOut: Int) -> Int {
        for d in document.drops {
            if edge == .leading, d.srcOut == srcIn { return d.srcOut - d.srcIn }
            if edge == .trailing, d.srcIn == srcOut { return d.srcOut - d.srcIn }
        }
        return 0
    }

    /// The clip's frame count with any in-flight trim drag applied — the rubber-band is
    /// HONEST: it clamps to exactly what the commit will produce (min 30 frames of clip;
    /// outward growth capped at the abutting trimmed footage, 0 when there's none).
    private func previewFrames(segIdx: Int, srcIn: Int, srcOut: Int) -> Int {
        let base = srcOut - srcIn
        guard let t = trimPreview, t.segIdx == segIdx else { return base }
        let adjusted = t.edge == .leading ? base - t.deltaFrames : base + t.deltaFrames
        let maxGrow = base + restorableFrames(edge: t.edge, srcIn: srcIn, srcOut: srcOut)
        return min(maxGrow, max(30, adjusted))
    }

    @ViewBuilder private func clipCell(pos: Int, segIdx: Int, srcIn: Int, srcOut: Int) -> some View {
        let frames = previewFrames(segIdx: segIdx, srcIn: srcIn, srcOut: srcOut)
        let trimming = trimPreview?.segIdx == segIdx
        let w = max(30, width(frames))
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
                Text(String(format: "%.1fs", Double(frames) / 30.0))
                    .font(.system(size: 8, weight: .semibold)).monospacedDigit()
                    .foregroundStyle(.white.opacity(0.9))
                    .padding(.horizontal, 4).padding(.vertical, 1.5)
                    .background(Color.black.opacity(0.45)).clipShape(Capsule())
                    .padding(3)
            }
        }
        // Floating duration badge while trimming — the live feedback that was missing.
        .overlay(alignment: edgeAlignment) {
            if trimming {
                Text(String(format: "%.1fs", Double(frames) / 30.0))
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
            withAnimation(.easeOut(duration: 0.15)) {
                selectedOverlay = nil
                selectedSeg = (selectedSeg == segIdx) ? nil : segIdx
            }
        }
        .accessibilityIdentifier("editorPro.clip.\(pos)")
    }

    private var edgeAlignment: Alignment {
        (trimPreview?.edge == .leading) ? .topLeading : .topTrailing
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
        .foregroundStyle(selected ? Palette.night : (o.type == "punch_in" ? Palette.accent : .white))
        .frame(width: w, height: 16)
        .background(
            RoundedRectangle(cornerRadius: 4)
                .fill(selected ? Palette.accent
                               : (o.type == "punch_in" ? Palette.accent.opacity(0.22) : Color.white.opacity(0.16)))
        )
        .overlay(RoundedRectangle(cornerRadius: 4)
            .strokeBorder(selected ? Palette.accent : Color.white.opacity(0.2), lineWidth: selected ? 1.5 : 0.5))
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
        RoundedRectangle(cornerRadius: 3).fill(Palette.accent)
            .frame(width: 10, height: 56)
            .overlay(Image(systemName: "chevron.compact.\(edge == .leading ? "left" : "right")").font(.system(size: 10)).foregroundStyle(.black))
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
            acc += framesToSeconds(c.srcOut - c.srcIn)
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
