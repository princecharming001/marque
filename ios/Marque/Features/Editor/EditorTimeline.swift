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
    let onTrim: (Int, TrimEdge, Int) -> Void
    let onReorder: ([Int]) -> Void

    @State private var dragBaseOffset: CGFloat?
    @State private var reordering = false
    @GestureState private var pinch: CGFloat = 1

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
                // Time ruler
                VStack(spacing: 2) {
                    ruler(width: geo.size.width)
                    HStack(spacing: 3) {
                        ForEach(Array(clips.enumerated()), id: \.offset) { pos, c in
                            clipCell(pos: pos, segIdx: c.segIdx, srcIn: c.srcIn, srcOut: c.srcOut)
                        }
                    }
                    .padding(.horizontal, mid)     // lets first/last clip reach the center playhead
                    .offset(x: mid - playheadOffset - mid)   // content scrolls under the fixed playhead
                }
                // Fixed center playhead
                Rectangle().fill(Palette.accent).frame(width: 2)
                    .frame(maxHeight: .infinity).offset(x: mid - 1)
            }
            .contentShape(Rectangle())
            .gesture(scrubGesture(mid: mid))
            .gesture(MagnificationGesture().updating($pinch) { v, s, _ in s = v }
                .onChanged { v in pointsPerSecond = max(8, min(60, pointsPerSecond * v / max(pinch, 0.01))) })
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

    @ViewBuilder private func clipCell(pos: Int, segIdx: Int, srcIn: Int, srcOut: Int) -> some View {
        let w = max(30, width(srcOut - srcIn))
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
        .overlay(alignment: .leading) { if selected { trimHandle(.leading, segIdx: segIdx, srcIn: srcIn, srcOut: srcOut) } }
        .overlay(alignment: .trailing) { if selected { trimHandle(.trailing, segIdx: segIdx, srcIn: srcIn, srcOut: srcOut) } }
        .onTapGesture { withAnimation(.easeOut(duration: 0.15)) { selectedSeg = (selectedSeg == segIdx) ? nil : segIdx } }
        .accessibilityIdentifier("editorPro.clip.\(pos)")
    }

    private func trimHandle(_ edge: TrimEdge, segIdx: Int, srcIn: Int, srcOut: Int) -> some View {
        RoundedRectangle(cornerRadius: 3).fill(Palette.accent)
            .frame(width: 10, height: 56)
            .overlay(Image(systemName: "chevron.compact.\(edge == .leading ? "left" : "right")").font(.system(size: 10)).foregroundStyle(.black))
            .contentShape(Rectangle().inset(by: -14))     // 44pt-ish hit target
            .highPriorityGesture(
                DragGesture()
                    .onEnded { g in
                        let deltaFrames = secondsToFrame(Double(g.translation.width / pointsPerSecond))
                        let newFrame = edge == .leading ? srcIn + deltaFrames : srcOut + deltaFrames
                        onTrim(segIdx, edge, newFrame)
                    }
            )
            .accessibilityIdentifier("editorPro.trimHandle.\(edge == .leading ? "left" : "right")")
    }

    private func scrubGesture(mid: CGFloat) -> some Gesture {
        DragGesture(minimumDistance: 6)
            .onChanged { g in
                guard let player else { return }
                if dragBaseOffset == nil { dragBaseOffset = CGFloat(player.currentOutputTime) * pointsPerSecond; player.pause() }
                let base = dragBaseOffset ?? 0
                let newOffset = base - g.translation.width
                player.seek(toOutput: Double(newOffset / pointsPerSecond))
            }
            .onEnded { _ in dragBaseOffset = nil }
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
