import SwiftUI

// MARK: - EditorTracks — the CapCut-style secondary lanes under the video filmstrip.
//
// Research (CapCut/InShot/VN, Mobbin):
// - Captions live on their own track as PHRASE clips (white strips, one per phrase) directly
//   below the video — tap one to edit the whole phrase, never word-by-word chips.
// - Audio lives in separate lanes below: original voice as a waveform strip that mirrors the
//   clips (muted spans read flat + dimmed), added music as a named strip.
// - Empty lanes advertise themselves ("+ Add sound") instead of hiding.

// MARK: Caption phrases — transcript words grouped into caption "clips"

struct CaptionPhrase: Identifiable, Equatable {
    var id: Int { startFrame }
    var startFrame: Int          // first word's start (source frames)
    var endFrame: Int            // last word's end (exclusive)
    var wordFrames: [Int]        // transcript word start-frames (the edit_caption slots)
    var text: String             // display text — edited captions win over the transcript
}

/// Group transcript words into phrases the way caption tracks do: break on a speech gap
/// (> 0.4s), on sentence punctuation, or after 6 words. Display text prefers the EDITED
/// captions in the phrase's range; falls back to the transcript when captions are empty
/// (enabled-but-rebuilding server-side).
func buildCaptionPhrases(words: [ProEditorView.WordSpan], captions: [EditorCaption]) -> [CaptionPhrase] {
    guard !words.isEmpty else { return [] }
    var groups: [[ProEditorView.WordSpan]] = []
    var cur: [ProEditorView.WordSpan] = []
    for w in words {
        if let last = cur.last {
            let gap = w.startFrame - last.endFrame
            let sentenceEnd = last.text.hasSuffix(".") || last.text.hasSuffix("?") || last.text.hasSuffix("!")
            if gap > 12 || sentenceEnd || cur.count >= 6 {
                groups.append(cur); cur = []
            }
        }
        cur.append(w)
    }
    if !cur.isEmpty { groups.append(cur) }

    // Per-slot display: an edited caption AT a word's exact start-frame overrides that word;
    // captions at off-slot frames are ignored for display (production captions are keyed to
    // word start-frames; anything else is seed noise or a server-side rewrite in flight).
    let byFrame = Dictionary(captions.map { ($0.frame, $0.word) }, uniquingKeysWith: { a, _ in a })
    return groups.map { g in
        let text = g.map { byFrame[$0.startFrame] ?? $0.text }.joined(separator: " ")
        return CaptionPhrase(startFrame: g.first!.startFrame, endFrame: g.last!.endFrame,
                             wordFrames: g.map(\.startFrame), text: text)
    }
}

// MARK: - Track lane views (rendered inside EditorTimeline's scrolling stack)

/// One caption phrase as a white clip strip at its output-time position (CapCut's caption track).
struct CaptionClipStrip: View {
    let phrase: CaptionPhrase
    let span: (start: Double, end: Double)
    let pointsPerSecond: CGFloat
    let onTap: () -> Void

    var body: some View {
        // Natural span width (a hair of trailing gap so neighbors read as separate clips);
        // a forced minimum here made short phrases overlap their neighbors.
        let w = max(12, CGFloat(span.end - span.start) * pointsPerSecond - 1.5)
        Text(w >= 26 ? phrase.text : "")
            .font(.system(size: 9, weight: .medium))
            .foregroundStyle(Palette.night)
            .lineLimit(1)
            .padding(.horizontal, 5)
            .frame(width: w, height: 16, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 4).fill(Color.white.opacity(0.88)))
            .offset(x: CGFloat(span.start) * pointsPerSecond)
            .onTapGesture(perform: onTap)
    }
}

/// The original-voice audio strip for one clip: a deterministic pseudo-waveform that mirrors
/// the clip's kept width. Muted spans draw flat and dim (the CapCut "extracted audio" read).
struct VoiceStrip: View {
    let srcIn: Int
    let srcOut: Int
    let width: CGFloat
    let volume: Double            // effective clip volume (0 = muted)
    let speechFrames: Set<Int>

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 4).fill(Palette.accent.opacity(volume <= 0.01 ? 0.10 : 0.22))
            Canvas { ctx, size in
                let barW: CGFloat = 2, gap: CGFloat = 1.5
                let n = max(1, Int(size.width / (barW + gap)))
                for i in 0..<n {
                    let x = CGFloat(i) * (barW + gap)
                    let f = srcIn + Int(Double(srcOut - srcIn) * Double(i) / Double(n))
                    // Deterministic wave: speech frames read tall; silence short; muted flat.
                    let speech = speechFrames.isEmpty || speechFrames.contains(where: { abs($0 - f) < 8 })
                    let base: CGFloat = speech ? 0.75 : 0.25
                    let jitter = CGFloat(abs(sin(Double(f) * 0.7)) * 0.35 + abs(sin(Double(f) * 0.23)) * 0.25)
                    var h = size.height * min(1, base * (0.5 + jitter))
                    if volume <= 0.01 { h = 2 } else { h *= CGFloat(min(1.0, 0.35 + volume * 0.65)) }
                    let rect = CGRect(x: x, y: (size.height - h) / 2, width: barW, height: h)
                    ctx.fill(Path(roundedRect: rect, cornerRadius: 1),
                             with: .color(.white.opacity(volume <= 0.01 ? 0.25 : 0.75)))
                }
            }
            .padding(.horizontal, 2)
        }
        .frame(width: width, height: 16)
        .overlay(alignment: .leading) {
            if volume <= 0.01 {
                Image(systemName: "speaker.slash.fill")
                    .font(.system(size: 7, weight: .bold)).foregroundStyle(.white.opacity(0.7))
                    .padding(.leading, 4)
            }
        }
    }
}

/// The music track strip: spans the whole cut, named, tinted its own color (CapCut's music lane).
struct MusicStrip: View {
    let name: String
    let width: CGFloat
    let volume: Double
    let onTap: () -> Void

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "music.note").font(.system(size: 8, weight: .semibold))
            Text(name).font(.system(size: 9, weight: .medium)).lineLimit(1)
            Spacer(minLength: 0)
            Text("\(Int((volume * 100).rounded()))%")
                .font(.system(size: 8, weight: .semibold)).monospacedDigit().opacity(0.7)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 6)
        .frame(width: max(46, width), height: 16)
        .background(RoundedRectangle(cornerRadius: 4).fill(Color(hex: 0x2E7D6B).opacity(0.85)))
        .onTapGesture(perform: onTap)
    }
}

/// Empty-lane affordance — the lane advertises what a tap adds (CapCut "+ Add audio").
struct AddLaneStrip: View {
    let label: String
    let width: CGFloat
    let onTap: () -> Void

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "plus").font(.system(size: 8, weight: .bold))
            Text(label).font(.system(size: 9, weight: .medium))
        }
        .foregroundStyle(.white.opacity(0.55))
        .frame(width: max(80, width), height: 16)
        .background(
            RoundedRectangle(cornerRadius: 4)
                .strokeBorder(Color.white.opacity(0.25), style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
        )
        .contentShape(Rectangle())
        .onTapGesture(perform: onTap)
    }
}
