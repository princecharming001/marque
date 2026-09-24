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

/// One caption phrase placed on the OUTPUT timeline (fully-cut phrases have no strip).
struct CaptionStrip: Identifiable {
    let phrase: CaptionPhrase
    let start: Double
    let end: Double
    var id: Int { phrase.id }
}

/// ED-5: phrases + their timeline strips, rebuilt once per draft revision. The editor used
/// to rebuild the phrases several times per body pass, and the caption lane re-walked every
/// kept interval for every phrase on every pass (O(phrases·segments·drops)).
@MainActor
final class CaptionPhraseMemo {
    private var session: ObjectIdentifier?
    private var revision = -1
    private var wordCount = -1
    private var built = false
    private(set) var phrases: [CaptionPhrase] = []
    private(set) var strips: [CaptionStrip] = []
    private(set) var startByPhrase: [Int: Double] = [:]

    func refresh(session s: EditorSession?, words: [ProEditorView.WordSpan]) {
        let id = s.map(ObjectIdentifier.init)
        let rev = s?.revision ?? -1            // the read that ties the caller to edits
        guard !built || id != session || rev != revision || words.count != wordCount else { return }
        built = true; session = id; revision = rev; wordCount = words.count
        phrases = buildCaptionPhrases(words: words, captions: s?.draft.captions ?? [])
        strips = phrases.compactMap { p in
            s?.outputSpan(srcIn: p.startFrame, srcOut: p.endFrame)
                .map { CaptionStrip(phrase: p, start: $0.start, end: $0.end) }
        }
        startByPhrase = Dictionary(strips.map { ($0.id, $0.start) }, uniquingKeysWith: { a, _ in a })
    }
}

// MARK: - Track lane views (rendered inside EditorTimeline's scrolling stack)

/// One caption phrase as a white clip strip at its output-time position (CapCut's caption track).
struct CaptionClipStrip: View {
    let phrase: CaptionPhrase
    let span: (start: Double, end: Double)
    let pointsPerSecond: CGFloat
    var selected: Bool = false
    let onTap: () -> Void

    var body: some View {
        // Natural span width (a hair of trailing gap so neighbors read as separate clips);
        // a forced minimum here made short phrases overlap their neighbors.
        let w = max(12, CGFloat(span.end - span.start) * pointsPerSecond - 1.5)
        Text(w >= 26 ? phrase.text : "")
            .font(AppFont.micro)
            .foregroundStyle(Palette.night)
            .lineLimit(1)
            .padding(.horizontal, 5)
            .frame(width: w, height: 26, alignment: .leading)
            // Captions = the LIGHTEST lane (near-white strips, dark text).
            .background(RoundedRectangle(cornerRadius: 4).fill(Palette.onNight.opacity(selected ? 1 : 0.82)))
            // Selection ring: a dark inner ring (a white ring is invisible on the light fill).
            .overlay(RoundedRectangle(cornerRadius: 4)
                .strokeBorder(selected ? Palette.night : .clear, lineWidth: 2))
            .offset(x: CGFloat(span.start) * pointsPerSecond)
            .onTapGesture(perform: onTap)
            .accessibilityLabel(phrase.text.isEmpty ? "Caption" : phrase.text)
            .accessibilityAddTraits(.isButton)
            .accessibilityIdentifier("editorPro.phrase.\(phrase.startFrame)")
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
            // Voice = a dark lane (waveform glyph in the gutter names it).
            RoundedRectangle(cornerRadius: 4).fill(Palette.onNight.opacity(volume <= 0.01 ? 0.05 : 0.12))
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
                             with: .color(Palette.onNight.opacity(volume <= 0.01 ? 0.25 : 0.75)))
                }
            }
            .padding(.horizontal, 2)
        }
        .frame(width: width, height: 20)
        .overlay(alignment: .leading) {
            if volume <= 0.01 {
                Image(systemName: "speaker.slash.fill")
                    .font(.system(size: 7, weight: .bold)).foregroundStyle(Palette.onNight.opacity(0.75))
                    .padding(.leading, 4)
            }
        }
    }
}

/// The music track strip: spans the whole cut, named, tinted its own color (CapCut's music
/// lane), with a deterministic pseudo-waveform under the name (reference parity — the real
/// CapCut music strip shows one).
struct MusicStrip: View {
    let name: String
    let width: CGFloat
    let volume: Double
    var seedKey: String = ""          // the track URL — stable identity for the waveform
    var selected: Bool = false
    let onTap: () -> Void

    /// FNV-1a over the URL bytes — String.hashValue is SipHash-randomized per launch and
    /// would re-roll the waveform every session; this stays stable.
    private var seed: Double {
        var h: UInt64 = 0xcbf29ce484222325
        for b in seedKey.utf8 { h = (h ^ UInt64(b)) &* 0x100000001b3 }
        return Double(h % 997)
    }

    var body: some View {
        ZStack {
            // Music = sunken gray lane + outline (was teal); named by the note glyph + title.
            RoundedRectangle(cornerRadius: 4).fill(Palette.surfaceSunken)
            RoundedRectangle(cornerRadius: 4).strokeBorder(Palette.hairline, lineWidth: 1)
            Canvas { ctx, size in
                let barW: CGFloat = 2, gap: CGFloat = 1.5
                let n = max(1, Int(size.width / (barW + gap)))
                for i in 0..<n {
                    let x = CGFloat(i) * (barW + gap)
                    let t = seed + Double(i)
                    // Same double-sin jitter as VoiceStrip, constant 0.6 base (no speech frames).
                    let jitter = CGFloat(abs(sin(t * 0.7)) * 0.35 + abs(sin(t * 0.23)) * 0.25)
                    var h = size.height * min(1, 0.6 * (0.5 + jitter))
                    h *= CGFloat(min(1.0, 0.35 + volume * 0.65))
                    let rect = CGRect(x: x, y: (size.height - h) / 2, width: barW, height: h)
                    ctx.fill(Path(roundedRect: rect, cornerRadius: 1),
                             with: .color(Palette.onNight.opacity(0.28)))
                }
            }
            .padding(.horizontal, 2)
            HStack(spacing: 4) {
                Image(systemName: "music.note").font(.system(size: 8, weight: .semibold))
                Text(name).font(AppFont.micro).lineLimit(1)
                Spacer(minLength: 0)
                Text("\(Int((volume * 100).rounded()))%")
                    .font(AppFont.micro.monospacedDigit()).opacity(0.75)
            }
            .foregroundStyle(Palette.onNight)
            .padding(.horizontal, 6)
        }
        .frame(width: max(46, width), height: 30)
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .overlay(RoundedRectangle(cornerRadius: 4)
            .strokeBorder(selected ? Palette.onNight : .clear, lineWidth: 2))
        .contentShape(Rectangle())
        .onTapGesture(perform: onTap)
        // FT-2: one element (its id was stamped on the name/percent Texts instead).
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }
}

/// Empty-lane affordance — the lane advertises what a tap adds (CapCut "+ Add audio").
struct AddLaneStrip: View {
    let label: String
    let width: CGFloat
    var height: CGFloat = 16
    let onTap: () -> Void

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "plus").font(.system(size: 8, weight: .bold))
            Text(label).font(AppFont.micro).lineLimit(1)
        }
        .foregroundStyle(Palette.textSecondary)
        .frame(width: max(80, width), height: height)
        .background(
            RoundedRectangle(cornerRadius: 4)
                .strokeBorder(Palette.textTertiary, style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
        )
        .contentShape(Rectangle())
        .onTapGesture(perform: onTap)
        // FT-2: one element, so editorPro.musicLane.add / rollsLane.add surface as buttons.
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }
}
