import SwiftUI
import AVKit
import AVFoundation

// The manual video editor — for creators who want frame-level control instead of
// chatting with the AI. Loads the clip's server EDL + transcript, lets them cut /
// reorder / mute segments and change captions, then sends the exact typed ops
// straight to the deterministic apply path (no LLM interpretation) + one re-render.
struct EditorView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let clip: Clip

    @State private var segments: [EditSegment] = []
    @State private var order: [Int] = []          // permutation of segment indices
    // H2: the EDL's existing segment_order (if any) — loaded once, compared
    // against for both hasChanges and computeOps' reorder-op emission. Without
    // this, the editor reset to identity order on every load(), silently
    // discarding a PRIOR reorder the moment the creator re-opened the editor
    // to make an unrelated change (e.g. just a caption tweak).
    @State private var baseOrder: [Int] = []
    @State private var cut: Set<Int> = []         // segment indices the user cut
    @State private var muted: Set<Int> = []        // segment indices muted (volume 0)
    @State private var captionsEnabled = true
    @State private var captionStyle = "clean"
    @State private var baseCaptionsEnabled = true
    @State private var baseCaptionStyle = "clean"
    // Trim: frames shaved off the front/back this session (~0.5s steps).
    @State private var trimStart = 0
    @State private var trimEnd = 0
    // Overlays (zooms/text cards) the user deletes.
    @State private var overlays: [OverlayRow] = []
    @State private var baseOverlays: [OverlayRow] = []
    // Background music.
    @State private var musicEnabled = false
    @State private var musicURL = MusicCatalog.tracks[0].url
    @State private var musicVolume = 0.15
    @State private var duckVoice = true
    @State private var baseMusic: (enabled: Bool, url: String, volume: Double, duck: Bool) = (false, "", 0.15, true)
    @State private var phase: Phase = .loading
    // H1: cancel-safety, mirroring TweakChatSheet's pattern. An untracked
    // `Task { await apply() }` kept polling after the sheet was dismissed,
    // writing to dead @State — worse, AppStore.pollJob's loop didn't even check
    // Task.isCancelled, so a cancelled task busy-spun instead of stopping.
    @State private var applyTask: Task<Void, Never>?
    @State private var statusBeforeApply: ClipStatus?
    // H3: a 409 ("still rendering, retry shortly") must not be treated the
    // same as a fatal error — it stays on the editing screen (all staged
    // local edits preserved) with an inline, auto-dismissing message instead
    // of the terminal .failed phase (which only offers "Close").
    @State private var transientMessage: String?
    // H6: whether the job's transcript (job.words) is present — captions can
    // only be rebuilt from it, so this gates the captions toggle.
    @State private var wordsAvailable = true
    // H7: rough-cut local preview — the job's original source video URL.
    @State private var sourceURLString: String?
    @State private var showRoughCutPreview = false
    // H8: per-word filler-cut review overrides, keyed by the word's startFrame.
    // Absent = use the word's original (AI-decided) cut state; present = the
    // creator's explicit override (true = force-kept/restored, false =
    // force-cut) for that exact word's frame span.
    @State private var wordOverrides: [Int: Bool] = [:]

    struct OverlayRow: Identifiable, Equatable {
        let id = UUID()
        let type: String
        let srcIn: Int
        let srcOut: Int
        let text: String
    }

    enum Phase: Equatable { case loading, editing, applying, rendering, failed(String) }

    struct EditSegment: Identifiable {
        let id: Int          // source index
        let srcIn: Int
        let srcOut: Int
        let preview: String
        var words: [WordEntry] = []   // H8: per-word filler-cut review
        var seconds: Double { Double(srcOut - srcIn) / 30.0 }
    }

    // H8: one word from the raw transcript, with its own frame span (distinct
    // from edl.captions' single-frame entries) and whether the AI's filler/
    // dead-air pass already cut it (falls inside an existing drop).
    struct WordEntry: Identifiable {
        var id: Int { startFrame }
        let text: String
        let startFrame: Int
        let endFrame: Int
        let originallyCut: Bool
    }

    private let captionStyles = ["clean", "bold-word", "karaoke"]

    var body: some View {
        NavigationStack {
            Group {
                switch phase {
                case .loading:
                    ProgressView("Loading your edit…").frame(maxWidth: .infinity, maxHeight: .infinity)
                case .applying:
                    ProgressView("Applying your edits…").frame(maxWidth: .infinity, maxHeight: .infinity)
                case .rendering:
                    VStack(spacing: Space.md) {
                        ProgressView()
                        Text("Re-rendering your clip…").font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    }.frame(maxWidth: .infinity, maxHeight: .infinity)
                case .failed(let msg):
                    VStack(spacing: Space.md) {
                        Image(systemName: "exclamationmark.triangle").font(.system(size: 32)).foregroundStyle(Palette.textTertiary)
                        Text(msg).font(AppFont.body).foregroundStyle(Palette.textSecondary).multilineTextAlignment(.center)
                        Button("Close") { dismiss() }.font(AppFont.headline).foregroundStyle(Palette.accent)
                    }.padding(Space.xl).frame(maxWidth: .infinity, maxHeight: .infinity)
                case .editing:
                    editor
                }
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Edit clip").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .topBarTrailing) {
                    if phase == .editing {
                        // H3: explicit re-entrancy guard — the toolbar item
                        // itself disappears once phase leaves .editing, but
                        // that only takes effect once the Task actually starts
                        // running; a rapid double-tap before then could
                        // otherwise queue two overlapping applies.
                        Button("Apply") {
                            guard applyTask == nil else { return }
                            applyTask = Task { await apply() }
                        }
                        .fontWeight(.semibold).disabled(!hasChanges)
                        .accessibilityIdentifier("editor.apply")
                    }
                }
            }
        }
        .onDisappear {
            applyTask?.cancel()
            applyTask = nil
            // If we're dismissed mid-render, revert the clip to its pre-apply
            // status locally — the backend keeps rendering regardless (it
            // doesn't know or care that the sheet closed), so this only avoids
            // the clip looking permanently stuck in "re-editing…" in the
            // Library; the next real poll (Library refresh / reopening this
            // clip) picks up whatever the server actually finished with.
            if let prev = statusBeforeApply,
               let idx = store.clips.firstIndex(where: { $0.id == clip.id }),
               store.clips[idx].status == .rendering {
                store.clips[idx].status = prev
            }
        }
        .task { await load() }
        .sheet(isPresented: $showRoughCutPreview) {
            if let sourceURLString, let url = URL(string: sourceURLString) {
                RoughCutPreviewSheet(sourceURL: url, intervals: keptIntervalsSeconds)
            }
        }
    }

    private var editor: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                if let transientMessage {
                    HStack(spacing: Space.sm) {
                        Image(systemName: "clock.arrow.circlepath").foregroundStyle(Palette.textSecondary)
                        Text(transientMessage).font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                        Spacer(minLength: 0)
                        Button {
                            withAnimation(Motion.quick) { self.transientMessage = nil }
                        } label: {
                            Image(systemName: "xmark").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
                        }
                    }
                    .padding(Space.md)
                    .background(Palette.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    .accessibilityIdentifier("editor.transientMessage")
                }
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: Space.xs) {
                        SectionLabel(text: "Segments", accent: Palette.accent)
                        Text("Tap to cut a line, reorder with the arrows, or mute a section.")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    }
                    Spacer(minLength: Space.md)
                    // H7: rough-cut local preview — instant, zero Lambda cost.
                    if sourceURLString != nil {
                        Button {
                            showRoughCutPreview = true
                        } label: {
                            Label("Preview", systemImage: "play.circle")
                                .font(AppFont.callout)
                        }
                        .buttonStyle(.plain).foregroundStyle(Palette.accent)
                        .accessibilityIdentifier("editor.previewCuts")
                    }
                }
                VStack(spacing: Space.sm) {
                    ForEach(Array(order.enumerated()), id: \.offset) { pos, segIdx in
                        segmentRow(segIdx: segIdx, position: pos)
                    }
                }

                trimSection

                VStack(alignment: .leading, spacing: Space.sm) {
                    SectionLabel(text: "Captions", accent: Palette.accent)
                    Toggle("Show captions", isOn: $captionsEnabled)
                        .font(AppFont.body).tint(Palette.ink)
                        .disabled(!wordsAvailable)
                        .accessibilityIdentifier("editor.captionsToggle")
                    // H6: the backend can only rebuild captions from the saved
                    // transcript (job.words) — a job whose transcript isn't
                    // available anymore (old/swept) would silently SKIP a
                    // set_captions_enabled op with zero feedback to the user.
                    // Disable proactively instead of failing silently later.
                    if !wordsAvailable {
                        Text("This clip's transcript isn't available anymore, so captions can't be changed.")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    }
                    if captionsEnabled {
                        Picker("Style", selection: $captionStyle) {
                            Text("Clean").tag("clean")
                            Text("Bold word").tag("bold-word")
                            Text("Karaoke").tag("karaoke")
                        }
                        .pickerStyle(.segmented)
                        .accessibilityIdentifier("editor.captionStyle")
                    }
                }
                .padding(Space.md)
                .background(Palette.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))

                overlaysSection
                audioSection
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
    }

    private var trimSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            SectionLabel(text: "Trim", accent: Palette.accent)
            HStack(spacing: Space.lg) {
                trimStepper(label: "Start", value: $trimStart)
                trimStepper(label: "End", value: $trimEnd)
            }
        }
    }

    private func trimStepper(label: String, value: Binding<Int>) -> some View {
        HStack(spacing: Space.sm) {
            Text(label).font(AppFont.callout).foregroundStyle(Palette.textSecondary)
            Button { value.wrappedValue = max(0, value.wrappedValue - 15) } label: {
                Image(systemName: "minus.circle").foregroundStyle(Palette.textPrimary)
            }
            Text(String(format: "%.1fs", Double(value.wrappedValue) / 30.0))
                .font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                .monospacedDigit().frame(minWidth: 40)
            Button { value.wrappedValue += 15 } label: {
                Image(systemName: "plus.circle").foregroundStyle(Palette.textPrimary)
            }
        }
        .padding(.horizontal, Space.md).padding(.vertical, Space.sm)
        .background(Palette.surfaceRaised)
        .clipShape(Capsule())
        .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
    }

    @ViewBuilder private var overlaysSection: some View {
        if !overlays.isEmpty {
            VStack(alignment: .leading, spacing: Space.sm) {
                SectionLabel(text: "Overlays", accent: Palette.accent)
                ForEach(overlays) { o in
                    HStack {
                        Image(systemName: o.type == "punch_in" ? "plus.magnifyingglass" : "textformat")
                            .foregroundStyle(Palette.textSecondary)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(o.type == "punch_in" ? "Zoom" : "Card: “\(o.text)”")
                                .font(AppFont.callout).foregroundStyle(Palette.textPrimary).lineLimit(1)
                            Text(String(format: "%.1fs – %.1fs", Double(o.srcIn) / 30.0, Double(o.srcOut) / 30.0))
                                .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                        }
                        Spacer()
                        Button { overlays.removeAll { $0.id == o.id } } label: {
                            Image(systemName: "trash").font(.system(size: 13))
                                .foregroundStyle(Palette.textTertiary)
                        }
                        .accessibilityIdentifier("editor.deleteOverlay")
                    }
                    .padding(Space.md)
                    .background(Palette.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                }
            }
        }
    }

    private var audioSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            SectionLabel(text: "Music", accent: Palette.accent)
            Toggle("Background music", isOn: $musicEnabled)
                .font(AppFont.body).tint(Palette.ink)
                .accessibilityIdentifier("editor.music")
            if musicEnabled {
                Picker("Track", selection: $musicURL) {
                    ForEach(MusicCatalog.tracks, id: \.url) { t in
                        Text(t.name).tag(t.url)
                    }
                }
                .pickerStyle(.menu).tint(Palette.textPrimary)
                HStack {
                    Image(systemName: "speaker.wave.1").foregroundStyle(Palette.textTertiary)
                    Slider(value: $musicVolume, in: 0.05...0.5).tint(Palette.ink)
                    Image(systemName: "speaker.wave.3").foregroundStyle(Palette.textTertiary)
                }
                Toggle("Duck under my voice", isOn: $duckVoice)
                    .font(AppFont.callout).tint(Palette.ink)
            }
        }
        .padding(Space.md)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
    }

    private func segmentRow(segIdx: Int, position: Int) -> some View {
        let seg = segments[segIdx]
        let isCut = cut.contains(segIdx)
        let isMuted = muted.contains(segIdx)
        return HStack(spacing: Space.sm) {
            VStack(alignment: .leading, spacing: 2) {
                // H8: word-level filler-cut review when the transcript has
                // per-word spans for this segment; falls back to the plain
                // joined-caption preview otherwise (e.g. keyless/mock jobs).
                if !seg.words.isEmpty {
                    wordStrip(seg.words)
                        .opacity(isCut ? 0.5 : 1)
                } else {
                    Text(seg.preview)
                        .font(AppFont.callout)
                        .foregroundStyle(isCut ? Palette.textTertiary : Palette.textPrimary)
                        .strikethrough(isCut)
                        .lineLimit(2)
                }
                Text(String(format: "%.1fs%@", seg.seconds, isMuted ? " · muted" : ""))
                    .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
            }
            Spacer(minLength: 0)
            // Reorder
            VStack(spacing: 2) {
                Button { move(position, by: -1) } label: { Image(systemName: "chevron.up") }
                    .disabled(position == 0)
                Button { move(position, by: 1) } label: { Image(systemName: "chevron.down") }
                    .disabled(position == order.count - 1)
            }
            .font(.system(size: 12)).foregroundStyle(Palette.textSecondary)
            // Mute
            Button { toggle(&muted, segIdx) } label: {
                Image(systemName: isMuted ? "speaker.slash.fill" : "speaker.wave.2")
                    .foregroundStyle(isMuted ? Palette.critical : Palette.textTertiary)
            }
            .accessibilityIdentifier("editor.mute")
            // Cut
            Button { toggle(&cut, segIdx) } label: {
                Image(systemName: isCut ? "arrow.uturn.backward" : "scissors")
                    .foregroundStyle(isCut ? Palette.accent : Palette.textSecondary)
            }
            .accessibilityIdentifier("editor.cut")
        }
        .padding(Space.md)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
        .contentShape(Rectangle())
        .onTapGesture { toggle(&cut, segIdx) }
    }

    // H8: the AI already cut filler/dead-air words (struck-through, dimmed) —
    // tap a struck word to restore it, tap a kept word to cut it. A horizontal
    // scroll rather than a wrapping flow layout: simpler, still fully
    // functional, and every word stays independently tappable.
    private func wordStrip(_ words: [WordEntry]) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 4) {
                ForEach(words) { w in
                    let effectivelyCut = wordOverrides[w.startFrame] ?? w.originallyCut
                    Button {
                        wordOverrides[w.startFrame] = !effectivelyCut
                    } label: {
                        Text(w.text)
                            .font(AppFont.callout)
                            .foregroundStyle(effectivelyCut ? Palette.textTertiary : Palette.textPrimary)
                            .strikethrough(effectivelyCut)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("editor.word.\(w.startFrame)")
                }
            }
        }
    }

    // MARK: helpers

    private func toggle(_ set: inout Set<Int>, _ i: Int) {
        if set.contains(i) { set.remove(i) } else { set.insert(i) }
    }

    private func move(_ pos: Int, by delta: Int) {
        let dest = pos + delta
        guard dest >= 0, dest < order.count else { return }
        withAnimation(Motion.quick) { order.swapAt(pos, dest) }
    }

    /// H8: words whose override actually flips their original AI-decided cut
    /// state (a word toggled back to its original state twice is not a
    /// change) — shared by hasChanges and computeOps.
    private var meaningfulWordOverrides: [(word: WordEntry, cut: Bool)] {
        segments.flatMap(\.words).compactMap { w in
            guard let override = wordOverrides[w.startFrame], override != w.originallyCut
            else { return nil }
            return (w, !override)   // override==true means "force kept", i.e. cut==false
        }
    }

    private var hasChanges: Bool {
        !cut.isEmpty || !muted.isEmpty || order != baseOrder
            || captionsEnabled != baseCaptionsEnabled || captionStyle != baseCaptionStyle
            || trimStart > 0 || trimEnd > 0
            || overlays != baseOverlays
            || musicEnabled != baseMusic.enabled
            || (musicEnabled && (musicURL != baseMusic.url
                                 || musicVolume != baseMusic.volume
                                 || duckVoice != baseMusic.duck))
            || !meaningfulWordOverrides.isEmpty
    }

    /// H7: the DRAFT edit's kept intervals (source seconds, PLAY order) for the
    /// rough-cut preview — cut segments removed, walked in `order`, trims
    /// clamped against the first/last PLAYED segment (mirrors F1's play-order-
    /// aware trim on the backend; a simplified single-segment clamp rather
    /// than F1's cross-segment spillover, which is plenty accurate for a
    /// scrub-through preview — the real render still uses the exact backend
    /// logic). Mutes/captions/overlays/music are NOT reflected — this is a
    /// structure-only preview.
    private var keptIntervalsSeconds: [(start: Double, end: Double)] {
        var kept: [(Double, Double)] = order.compactMap { idx in
            cut.contains(idx) ? nil : (Double(segments[idx].srcIn), Double(segments[idx].srcOut))
        }
        if trimStart > 0, !kept.isEmpty {
            kept[0].0 = min(kept[0].0 + Double(trimStart), kept[0].1)
        }
        if trimEnd > 0, !kept.isEmpty {
            let last = kept.count - 1
            kept[last].1 = max(kept[last].1 - Double(trimEnd), kept[last].0)
        }
        return kept.filter { $0.1 > $0.0 }.map { (start: $0.0 / 30.0, end: $0.1 / 30.0) }
    }

    private func load() async {
        guard let jobId = clip.jobId,
              let result = await store.backend.pollClipJob(jobId: jobId, includeWords: true),
              let edl = result["edl"] as? [String: Any] else {
            phase = .failed("Couldn't load this clip's edit — the session may have expired.")
            return
        }
        let segs = (edl["segments"] as? [[String: Any]]) ?? []
        let caps = (edl["captions"] as? [[String: Any]]) ?? []
        let rawWords = (result["words"] as? [[String: Any]]) ?? []
        wordsAvailable = !rawWords.isEmpty
        sourceURLString = result["source_url"] as? String
        captionsEnabled = !caps.isEmpty
        captionStyle = (edl["caption_style"] as? String) ?? "clean"
        baseCaptionsEnabled = captionsEnabled
        baseCaptionStyle = captionStyle

        // H8: raw transcript words, each carrying its OWN frame span (distinct
        // from edl.captions' single start-frame entries) and whether the AI's
        // filler/dead-air pass already cut it (falls inside an existing drop).
        let drops: [(Int, Int)] = ((edl["drops"] as? [[String: Any]]) ?? []).compactMap {
            guard let a = $0["src_in"] as? Int, let b = $0["src_out"] as? Int else { return nil }
            return (a, b)
        }
        func msToFrame(_ ms: Double) -> Int { Int((ms * 30.0 / 1000.0).rounded()) }
        let wordEntries: [WordEntry] = rawWords.compactMap { w in
            guard let text = w["word"] as? String, !text.isEmpty else { return nil }
            let startMs = (w["start_ms"] as? Double) ?? Double(w["start_ms"] as? Int ?? 0)
            let endMs = (w["end_ms"] as? Double) ?? Double(w["end_ms"] as? Int ?? 0)
            let sf = msToFrame(startMs), ef = max(sf + 1, msToFrame(endMs))
            let cut = drops.contains { d in sf < d.1 && ef > d.0 }   // overlaps any drop
            return WordEntry(text: text, startFrame: sf, endFrame: ef, originallyCut: cut)
        }.sorted { $0.startFrame < $1.startFrame }

        var built: [EditSegment] = []
        for (i, s) in segs.enumerated() {
            let si = s["src_in"] as? Int ?? 0
            let so = s["src_out"] as? Int ?? 0
            let capWords = caps.filter { let f = $0["frame"] as? Int ?? -1; return f >= si && f < so }
                               .compactMap { $0["word"] as? String }
            let preview = capWords.joined(separator: " ")
            let segWords = wordEntries.filter { $0.startFrame >= si && $0.startFrame < so }
            built.append(EditSegment(id: i, srcIn: si, srcOut: so,
                                     preview: preview.isEmpty ? "Segment \(i + 1)" : preview,
                                     words: segWords))
        }
        segments = built
        wordOverrides = [:]
        // H2: honor an existing segment_order rather than always resetting to
        // identity — validated as a genuine permutation of the CURRENT segment
        // count (defensive: a stale/malformed order from an old shape must
        // never crash the picker or produce an invalid reorder_segments op).
        if let existingOrder = edl["segment_order"] as? [Int],
           existingOrder.count == built.count,
           Set(existingOrder) == Set(built.indices) {
            order = existingOrder
        } else {
            order = Array(built.indices)
        }
        baseOrder = order
        overlays = ((edl["overlays"] as? [[String: Any]]) ?? []).compactMap { o in
            guard let a = o["src_in"] as? Int, let b = o["src_out"] as? Int else { return nil }
            return OverlayRow(type: (o["type"] as? String) ?? "punch_in",
                              srcIn: a, srcOut: b, text: (o["text"] as? String) ?? "")
        }
        baseOverlays = overlays
        if let audio = edl["audio"] as? [String: Any],
           let music = audio["music"] as? [String: Any] {
            musicEnabled = true
            musicURL = (music["url"] as? String) ?? MusicCatalog.tracks[0].url
            musicVolume = (music["volume"] as? Double) ?? 0.15
            duckVoice = (music["duck_voice"] as? Bool) ?? true
        }
        baseMusic = (musicEnabled, musicURL, musicVolume, duckVoice)
        phase = built.isEmpty ? .failed("This clip has no editable segments yet.") : .editing
    }

    /// Diff the edited state into typed EDL ops (restores → cuts → mutes → reorder
    /// → caption changes; deterministic order so apply semantics are stable).
    private func computeOps() -> [[String: Any]] {
        var ops: [[String: Any]] = []
        for i in cut.sorted() {
            ops.append(["type": "cut_range", "start_frame": segments[i].srcIn, "end_frame": segments[i].srcOut])
        }
        // H8: word-level overrides AFTER whole-segment cuts (so "cut this
        // segment but keep this one word" restores correctly carve out of the
        // wholesale cut, rather than being immediately overwritten by it), but
        // BEFORE mutes/reorder/trims (both are SOURCE-frame ops, immune to
        // segment_order/trims either way — this ordering is about coarse-to-
        // fine within the cut/restore family specifically).
        for (w, isCut) in meaningfulWordOverrides.sorted(by: { $0.word.startFrame < $1.word.startFrame }) {
            ops.append(["type": isCut ? "cut_range" : "restore_range",
                        "start_frame": w.startFrame, "end_frame": w.endFrame])
        }
        for i in muted.sorted() {
            ops.append(["type": "mute_range", "start_frame": segments[i].srcIn, "end_frame": segments[i].srcOut])
        }
        if order != baseOrder {
            ops.append(["type": "reorder_segments", "order": order])
        }
        if trimStart > 0 {
            ops.append(["type": "trim_start", "frames": trimStart])
        }
        if trimEnd > 0 {
            ops.append(["type": "trim_end", "frames": trimEnd])
        }
        if captionsEnabled != baseCaptionsEnabled {
            ops.append(["type": "set_captions_enabled", "enabled": captionsEnabled])
        }
        if captionsEnabled && captionStyle != baseCaptionStyle {
            ops.append(["type": "set_caption_style", "style": captionStyle])
        }
        for o in baseOverlays where !overlays.contains(o) {
            ops.append(["type": "remove_overlays", "kind": o.type,
                        "start_frame": o.srcIn, "end_frame": o.srcOut])
        }
        if musicEnabled != baseMusic.enabled
            || (musicEnabled && (musicURL != baseMusic.url
                                 || musicVolume != baseMusic.volume
                                 || duckVoice != baseMusic.duck)) {
            if musicEnabled {
                ops.append(["type": "set_music", "enabled": true, "url": musicURL,
                            "volume": musicVolume, "duck_voice": duckVoice])
            } else {
                ops.append(["type": "set_music", "enabled": false])
            }
        }
        return ops
    }

    private func apply() async {
        let ops = computeOps()
        guard !ops.isEmpty, let jobId = clip.jobId else { dismiss(); return }
        phase = .applying
        transientMessage = nil
        let resp = await store.backend.tweakClipOps(jobId: jobId, clipId: clip.id.uuidString, ops: ops)
        if resp["error"] as? Bool == true {
            // H3: a 409 ("still rendering") is transient — stay on the editing
            // screen with every staged local edit intact so the creator can
            // just retry in a moment, instead of the terminal .failed phase
            // (which only offers "Close" and would discard their edits).
            if resp["transient"] as? Bool == true {
                phase = .editing
                applyTask = nil
                transientMessage = resp["reply"] as? String ?? "Still busy — try again shortly."
                return
            }
            phase = .failed(resp["reply"] as? String ?? "Couldn't apply your edits.")
            return
        }
        let needsRender = resp["needs_render"] as? Bool ?? false
        if needsRender {
            statusBeforeApply = store.clips.first { $0.id == clip.id }?.status
            phase = .rendering
            store.setClipRendering(clip.id)
            await store.pollJob(jobId: jobId, clipIds: [clip.id])
            guard !Task.isCancelled else { return }   // H1: dismissed mid-poll — onDisappear owns cleanup now
            let finalClip = store.clips.first { $0.id == clip.id }
            if finalClip?.status == .failed {
                phase = .failed(store.friendlyRenderError(finalClip?.lastError, detail: finalClip?.lastErrorDetail))
            } else {
                dismiss()
            }
        } else {
            dismiss()   // keyless/mock path: applied in place, no render needed
        }
    }
}

// H7: rough-cut local preview — seeks a single AVPlayer through the DRAFT
// edit's kept intervals (cuts/reorder/trims), in PLAY order, against the
// ORIGINAL source video. Zero Lambda cost, instant feedback. Deliberately a
// "rough cut": captions/overlays/music/mutes are NOT simulated (this previews
// STRUCTURE — what's kept, in what order — not the final look), labeled as
// such so it's never mistaken for the real render.
@Observable
final class RoughCutController {
    private var player: AVPlayer?
    private var timeObserver: Any?
    private var intervals: [(start: Double, end: Double)] = []
    private var currentIndex = 0
    var isPlaying = false
    var isEmpty: Bool { intervals.isEmpty }

    func configure(url: URL, intervals: [(start: Double, end: Double)]) {
        teardown()
        self.intervals = intervals
        guard !intervals.isEmpty else { return }
        player = AVPlayer(url: url)
    }

    var avPlayer: AVPlayer? { player }

    func play() {
        guard let player, !intervals.isEmpty else { return }
        currentIndex = 0
        seekToCurrent()
        player.play()
        isPlaying = true
        observeTime()
    }

    func pause() {
        player?.pause()
        isPlaying = false
    }

    private func seekToCurrent() {
        guard currentIndex < intervals.count else { return }
        let t = CMTime(seconds: intervals[currentIndex].start, preferredTimescale: 600)
        player?.seek(to: t, toleranceBefore: .zero, toleranceAfter: .zero)
    }

    private func observeTime() {
        guard let player, timeObserver == nil else { return }
        let interval = CMTime(seconds: 0.1, preferredTimescale: 600)
        timeObserver = player.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] time in
            guard let self, self.currentIndex < self.intervals.count else { return }
            if time.seconds >= self.intervals[self.currentIndex].end {
                self.currentIndex += 1
                if self.currentIndex < self.intervals.count {
                    self.seekToCurrent()
                } else {
                    self.pause()
                }
            }
        }
    }

    func teardown() {
        if let timeObserver, let player { player.removeTimeObserver(timeObserver) }
        timeObserver = nil
        player?.pause()
        player = nil
        isPlaying = false
        currentIndex = 0
    }
}

struct RoughCutPreviewSheet: View {
    let sourceURL: URL
    let intervals: [(start: Double, end: Double)]
    @State private var controller = RoughCutController()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: Space.md) {
                if controller.isEmpty {
                    VStack(spacing: Space.sm) {
                        Image(systemName: "film.stack").font(.system(size: 28)).foregroundStyle(Palette.textTertiary)
                        Text("Nothing left to preview — every segment is cut.")
                            .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    }.frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    if let player = controller.avPlayer {
                        VideoPlayer(player: player)
                            .aspectRatio(9.0/16.0, contentMode: .fit)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                    }
                    Text("Rough cut — structure only. Captions, music, and overlays render when you tap Apply.")
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, Space.lg)
                }
            }
            .padding(Space.lg)
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Preview cuts").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } }
            }
        }
        .onAppear {
            controller.configure(url: sourceURL, intervals: intervals)
            controller.play()
        }
        .onDisappear { controller.teardown() }
        .accessibilityIdentifier("editor.roughCutPreview")
    }
}

// Small bundled music catalog — direct, stable, license-free URLs the Lambda
// renderer can fetch. Swappable for a real hosted catalog later.
enum MusicCatalog {
    struct Track { let name: String; let url: String }
    static let tracks: [Track] = [
        Track(name: "Neverwritten (upbeat)",
              url: "https://commondatastorage.googleapis.com/codeskulptor-demos/DDR_assets/Kangaroo_MusiQue_-_The_Neverwritten_Role_Playing_Game.mp3"),
        Track(name: "Epoq (chill)",
              url: "https://commondatastorage.googleapis.com/codeskulptor-assets/Epoq-Lepidoptera.ogg"),
        Track(name: "Race Menu (driving)",
              url: "https://commondatastorage.googleapis.com/codeskulptor-demos/riceracer_assets/music/menu.ogg"),
    ]
}
