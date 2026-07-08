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
    // H9: when the current render/apply started, for the elapsed-time display.
    @State private var renderStartedAt: Date?
    // H10: undo the last APPLIED server-side tweak (not the current unsaved
    // local draft — the creator can just not tap Apply for that). From F8's
    // undo_available in the GET response.
    @State private var undoAvailable = false
    @State private var undoing = false
    // H11: HD preview — a real (if cheap, G9) Lambda render of the staged
    // caption/music/overlay changes, shown inline before committing to Apply.
    @State private var hdPreviewPhase: HDPreviewPhase = .idle
    @State private var hdPreviewPollTask: Task<Void, Never>?
    // H-08: staged caption-text edits (caption frame → new word; empty = delete)…
    @State private var captionEdits: [Int: String] = [:]
    // …staged text edits on existing overlays (original edl index → new text)…
    @State private var overlayTextEdits: [Int: String] = [:]
    // …staged overlay additions (become add_punch_in / add_text_card on Apply)…
    @State private var addedOverlays: [OverlayRow] = []
    // …and the per-style capability map (G-04) gating the add affordances.
    @State private var editorCaps: [String: Bool]? = nil
    @State private var clipStyle = ""
    // Text-edit alert plumbing (caption word or overlay card text).
    @State private var editingCaptionFrame: Int? = nil
    @State private var editingOverlayIndex: Int? = nil
    @State private var showTextCardAlert = false
    @State private var editDraft = ""

    enum HDPreviewPhase: Equatable {
        case idle, requesting, rendering, ready(String), failed(String)
    }

    struct OverlayRow: Identifiable, Equatable {
        let id = UUID()
        let type: String
        let srcIn: Int
        let srcOut: Int
        let text: String
        // H-08: position in edl.overlays (edit_overlay targets by index); -1 = staged add.
        var index: Int = -1
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
                    // H9: elapsed time (so a long render doesn't read as
                    // hung) + an explicit Cancel that detaches cleanly —
                    // dismiss() triggers onDisappear, which already cancels
                    // applyTask and reverts clip.status (H1).
                    VStack(spacing: Space.md) {
                        ProgressView()
                        Text("Re-rendering your clip…").font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        if let renderStartedAt {
                            TimelineView(.periodic(from: renderStartedAt, by: 1)) { context in
                                Text("\(Int(context.date.timeIntervalSince(renderStartedAt)))s elapsed")
                                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                                    .monospacedDigit()
                            }
                        }
                        Button("Cancel") { dismiss() }
                            .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                            .padding(.top, Space.sm)
                            .accessibilityIdentifier("editor.cancelRender")
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
                ToolbarItem(placement: .topBarLeading) {
                    if phase == .editing && undoAvailable {
                        Button {
                            Task { await undo() }
                        } label: {
                            if undoing { ProgressView().controlSize(.small) }
                            else { Image(systemName: "arrow.uturn.backward") }
                        }
                        .disabled(undoing)
                        .accessibilityIdentifier("editor.undo")
                    }
                }
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
            hdPreviewPollTask?.cancel()   // H11: never poll for a preview after dismissal
            hdPreviewPollTask = nil
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
        // H-08: caption-word text edit (empty text deletes that caption)
        .alert("Edit caption", isPresented: Binding(
            get: { editingCaptionFrame != nil },
            set: { if !$0 { editingCaptionFrame = nil } })) {
            TextField("Caption text", text: $editDraft)
            Button("Save") {
                if let f = editingCaptionFrame {
                    captionEdits[f] = editDraft.trimmingCharacters(in: .whitespaces)
                }
                editingCaptionFrame = nil
            }
            Button("Cancel", role: .cancel) { editingCaptionFrame = nil }
        } message: {
            Text("Fix the caption's text — leave it empty to remove it.")
        }
        // H-08: text-card copy edit (existing overlay, by original index)
        .alert("Edit text card", isPresented: Binding(
            get: { editingOverlayIndex != nil },
            set: { if !$0 { editingOverlayIndex = nil } })) {
            TextField("Card text", text: $editDraft)
            Button("Save") {
                if let i = editingOverlayIndex, !editDraft.trimmingCharacters(in: .whitespaces).isEmpty {
                    overlayTextEdits[i] = String(editDraft.trimmingCharacters(in: .whitespaces).prefix(80))
                }
                editingOverlayIndex = nil
            }
            Button("Cancel", role: .cancel) { editingOverlayIndex = nil }
        }
        // H-08: stage a new text card over the opening seconds
        .alert("New text card", isPresented: $showTextCardAlert) {
            TextField("Card text", text: $editDraft)
            Button("Add") {
                let text = editDraft.trimmingCharacters(in: .whitespaces)
                if !text.isEmpty, let firstIdx = order.first(where: { !cut.contains($0) }) {
                    let seg = segments[firstIdx]
                    let end = min(seg.srcIn + 75, seg.srcOut)
                    if end > seg.srcIn {
                        addedOverlays.append(OverlayRow(type: "text_card", srcIn: seg.srcIn,
                                                        srcOut: end, text: String(text.prefix(80))))
                    }
                }
            }
            Button("Cancel", role: .cancel) {}
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
                        SectionLabel(text: "Timeline", accent: Palette.accent)
                        Text("Tap a block to cut it; use a row's tools to split, reorder, or mute.")
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

                timelineStrip

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
                        .contentShape(Rectangle())
                        // A bare Toggle's real interactive target is its UIKit-backed
                        // switch knob only — the label is a separate, non-interactive
                        // sibling, and contentShape can't extend a hit region into that
                        // embedded UIKit control. A tap landing anywhere else on the row
                        // (label, or the gap between them) falls through to this gesture
                        // instead; a tap that lands ON the knob is already consumed by
                        // its own native control and never reaches here, so this can't
                        // double-toggle.
                        .onTapGesture {
                            guard wordsAvailable else { return }
                            captionsEnabled.toggle()
                        }
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
                hdPreviewSection
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
    }

    // H11: only offered when there's a caption/music/overlay change to show —
    // the H7 rough-cut preview already covers structure (cuts/reorder/trims)
    // for free; this costs a real (if cheap, G9) Lambda render.
    @ViewBuilder private var hdPreviewSection: some View {
        if hasStyleChanges {
            VStack(alignment: .leading, spacing: Space.sm) {
                SectionLabel(text: "HD preview", accent: Palette.accent)
                switch hdPreviewPhase {
                case .idle, .failed:
                    if case .failed(let msg) = hdPreviewPhase {
                        Text(msg).font(AppFont.caption).foregroundStyle(Palette.critical)
                    }
                    GhostButton(title: "Preview captions & music", systemImage: "sparkles.tv") {
                        requestHDPreview()
                    }
                    .accessibilityIdentifier("editor.hdPreview")
                case .requesting, .rendering:
                    HStack(spacing: Space.sm) {
                        ProgressView()
                        Text(hdPreviewPhase == .requesting ? "Starting preview…" : "Rendering a quick preview…")
                            .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                    }
                case .ready(let url):
                    if let previewURL = URL(string: url) {
                        VideoPlayer(player: AVPlayer(url: previewURL))
                            .aspectRatio(9.0/16.0, contentMode: .fit)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                            .accessibilityIdentifier("editor.hdPreviewPlayer")
                    }
                }
            }
            .padding(Space.md)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        }
    }

    // H-08: the horizontal timeline — every segment as a proportional block in PLAY
    // order (reorder/cuts reflected live). CapCut-basic: a glanceable map of the cut,
    // not a scrubber; the rows below stay the precision tools.
    private var timelineStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 3) {
                ForEach(Array(order.enumerated()), id: \.offset) { _, segIdx in
                    timelineBlock(segIdx)
                }
            }
        }
    }

    private func timelineBlock(_ segIdx: Int) -> some View {
        let seg = segments[segIdx]
        let isCut = cut.contains(segIdx)
        let width: CGFloat = max(44, min(160, CGFloat(seg.seconds) * 16))
        let label: String = seg.words.first?.text
            ?? seg.preview.components(separatedBy: " ").first ?? "•"
        let shape = RoundedRectangle(cornerRadius: 8, style: .continuous)
        return VStack(spacing: 3) {
            Text(label)
                .font(AppFont.micro).lineLimit(1)
                .foregroundStyle(isCut ? Palette.textTertiary : Palette.textPrimary)
                .strikethrough(isCut)
            Text(String(format: "%.1fs", seg.seconds))
                .font(AppFont.micro).foregroundStyle(Palette.textTertiary).monospacedDigit()
            if muted.contains(segIdx) {
                Image(systemName: "speaker.slash.fill")
                    .font(.system(size: 8)).foregroundStyle(Palette.critical)
            }
        }
        .frame(width: width, height: 52)
        .background(isCut ? Palette.surfaceSunken : Palette.surfaceRaised)
        .clipShape(shape)
        .overlay(shape.strokeBorder(isCut ? Palette.hairline : Palette.accent.opacity(0.45), lineWidth: 1))
        .contentShape(Rectangle())
        .onTapGesture { toggle(&cut, segIdx) }
        .accessibilityIdentifier("editor.timeline.\(segIdx)")
    }

    /// H-08: split a segment at the word boundary nearest its midpoint — applied
    /// immediately as ONE direct op (staged diffs are index-based and would go stale),
    /// via preview=1 so it never spends a full commit render on a structurally
    /// identical cut. Requires a clean slate: staged edits survive a split badly.
    private func splitSegment(_ segIdx: Int) {
        guard let jobId = clip.jobId else { return }
        guard !hasChanges else {
            transientMessage = "Apply your changes first, then split."
            return
        }
        let seg = segments[segIdx]
        guard seg.srcOut - seg.srcIn >= 90 else {
            transientMessage = "That segment is too short to split."
            return
        }
        let mid: Int = (seg.srcIn + seg.srcOut) / 2
        let lo: Int = seg.srcIn + 30
        let hi: Int = seg.srcOut - 30
        var candidates: [Int] = []
        for w in seg.words where w.startFrame > lo && w.startFrame < hi {
            candidates.append(w.startFrame)
        }
        let boundary: Int = candidates.min { a, b in abs(a - mid) < abs(b - mid) } ?? mid
        phase = .loading
        Task {
            let resp = await store.backend.tweakClipOps(
                jobId: jobId, clipId: clip.id.uuidString,
                ops: [["type": "split_segment", "index": segIdx, "at_frame": boundary]],
                preview: true)
            if resp["error"] as? Bool == true {
                transientMessage = resp["reply"] as? String ?? "Couldn't split that segment."
            }
            await load()
        }
    }

    private var trimSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            SectionLabel(text: "Trim", accent: Palette.accent)
            HStack(spacing: Space.lg) {
                trimStepper(label: "Start", id: "start", value: $trimStart)
                trimStepper(label: "End", id: "end", value: $trimEnd)
            }
        }
    }

    private func trimStepper(label: String, id: String, value: Binding<Int>) -> some View {
        HStack(spacing: Space.sm) {
            Text(label).font(AppFont.callout).foregroundStyle(Palette.textSecondary)
            Button { value.wrappedValue = max(0, value.wrappedValue - 15) } label: {
                Image(systemName: "minus.circle").foregroundStyle(Palette.textPrimary)
            }
            .accessibilityIdentifier("editor.trim.\(id).decrement")
            Text(String(format: "%.1fs", Double(value.wrappedValue) / 30.0))
                .font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                .monospacedDigit().frame(minWidth: 40)
                .accessibilityIdentifier("editor.trim.\(id).value")
            Button { value.wrappedValue += 15 } label: {
                Image(systemName: "plus.circle").foregroundStyle(Palette.textPrimary)
            }
            .accessibilityIdentifier("editor.trim.\(id).increment")
        }
        .padding(.horizontal, Space.md).padding(.vertical, Space.sm)
        .background(Palette.surfaceRaised)
        .clipShape(Capsule())
        .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
        .accessibilityIdentifier("editor.trim.\(id)")
    }

    // H-08: which optional overlay ops this clip's style can actually render (G-04).
    // nil caps (fetch failed) hides the ADD affordances — a staged add whose ops all
    // get skipped server-side would silently discard the creator's intent.
    private func styleCan(_ key: String) -> Bool { editorCaps?[key] ?? false }

    @ViewBuilder private var overlaysSection: some View {
        let canAdd = styleCan("punch_ins") || styleCan("text_cards")
        if !overlays.isEmpty || !addedOverlays.isEmpty || canAdd {
            VStack(alignment: .leading, spacing: Space.sm) {
                SectionLabel(text: "Overlays", accent: Palette.accent)
                ForEach(overlays) { o in
                    overlayRow(o, staged: false)
                }
                ForEach(addedOverlays) { o in
                    overlayRow(o, staged: true)
                }
                if canAdd {
                    HStack(spacing: Space.sm) {
                        if styleCan("punch_ins"), !hasStagedPunchIn {
                            GhostButton(title: "Zoom on the hook", systemImage: "plus.magnifyingglass") {
                                addPunchInOnHook()
                            }
                            .accessibilityIdentifier("editor.addPunchIn")
                        }
                        if styleCan("text_cards") {
                            GhostButton(title: "Text card", systemImage: "textformat") {
                                editDraft = ""
                                showTextCardAlert = true
                            }
                            .accessibilityIdentifier("editor.addTextCard")
                        }
                    }
                }
            }
        }
    }

    private func overlayRow(_ o: OverlayRow, staged: Bool) -> some View {
        let displayText = o.index >= 0 ? (overlayTextEdits[o.index] ?? o.text) : o.text
        return HStack {
            Image(systemName: o.type == "punch_in" ? "plus.magnifyingglass" : "textformat")
                .foregroundStyle(staged ? Palette.accent : Palette.textSecondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(o.type == "punch_in" ? "Zoom" : "Card: “\(displayText)”")
                    .font(AppFont.callout).foregroundStyle(Palette.textPrimary).lineLimit(1)
                Text(String(format: "%.1fs – %.1fs%@", Double(o.srcIn) / 30.0, Double(o.srcOut) / 30.0,
                            staged ? " · new" : ""))
                    .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
            }
            Spacer()
            // H-08: edit a text card's copy (edit_overlay by original index)
            if o.type == "text_card", o.index >= 0 {
                Button {
                    editDraft = displayText
                    editingOverlayIndex = o.index
                } label: {
                    Image(systemName: "pencil").font(.system(size: 13))
                        .foregroundStyle(Palette.textTertiary)
                }
                .accessibilityIdentifier("editor.editOverlay")
            }
            Button {
                if staged { addedOverlays.removeAll { $0.id == o.id } }
                else { overlays.removeAll { $0.id == o.id } }
            } label: {
                Image(systemName: "trash").font(.system(size: 13))
                    .foregroundStyle(Palette.textTertiary)
            }
            .accessibilityIdentifier("editor.deleteOverlay")
        }
        .padding(Space.md)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
    }

    private var hasStagedPunchIn: Bool {
        addedOverlays.contains { $0.type == "punch_in" }
    }

    /// H-08: stage a punch-in over the opening hook (first ~2s of the first PLAYED,
    /// un-cut segment) — mirrors the backend's suggested-edit chip semantics.
    private func addPunchInOnHook() {
        guard let firstIdx = order.first(where: { !cut.contains($0) }) else { return }
        let seg = segments[firstIdx]
        let end = min(seg.srcIn + 60, seg.srcOut)
        guard end > seg.srcIn else { return }
        addedOverlays.append(OverlayRow(type: "punch_in", srcIn: seg.srcIn, srcOut: end, text: ""))
    }

    private var audioSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            SectionLabel(text: "Music", accent: Palette.accent)
            Toggle("Background music", isOn: $musicEnabled)
                .font(AppFont.body).tint(Palette.ink)
                .contentShape(Rectangle())
                .onTapGesture { musicEnabled.toggle() }
                .accessibilityIdentifier("editor.music")
            if musicEnabled {
                Picker("Track", selection: $musicURL) {
                    ForEach(MusicCatalog.tracks, id: \.url) { t in
                        Text(t.name).tag(t.url)
                    }
                }
                .pickerStyle(.menu).tint(Palette.textPrimary)
                .accessibilityIdentifier("editor.music.track")
                HStack {
                    Image(systemName: "speaker.wave.1").foregroundStyle(Palette.textTertiary)
                    Slider(value: $musicVolume, in: 0.05...0.5).tint(Palette.ink)
                        .accessibilityIdentifier("editor.music.volume")
                    Image(systemName: "speaker.wave.3").foregroundStyle(Palette.textTertiary)
                }
                Toggle("Duck under my voice", isOn: $duckVoice)
                    .font(AppFont.callout).tint(Palette.ink)
                    .contentShape(Rectangle())
                    .onTapGesture { duckVoice.toggle() }
                    .accessibilityIdentifier("editor.music.duck")
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
            // Reorder — ids keyed by segIdx (stable identity), not position
            // (which changes meaning every time the order changes).
            VStack(spacing: 2) {
                Button { move(position, by: -1) } label: { Image(systemName: "chevron.up") }
                    .disabled(position == 0)
                    .accessibilityIdentifier("editor.segment.\(segIdx).moveUp")
                Button { move(position, by: 1) } label: { Image(systemName: "chevron.down") }
                    .disabled(position == order.count - 1)
                    .accessibilityIdentifier("editor.segment.\(segIdx).moveDown")
            }
            .font(.system(size: 12)).foregroundStyle(Palette.textSecondary)
            // H-08: split at the midpoint word boundary (immediate direct op)
            Button { splitSegment(segIdx) } label: {
                Image(systemName: "square.split.2x1")
                    .foregroundStyle(Palette.textTertiary)
            }
            .accessibilityIdentifier("editor.split")
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
        // H13 regression found + fixed: an accessibilityIdentifier on THIS row
        // (the same view carrying .onTapGesture) made SwiftUI collapse the
        // whole HStack into one opaque accessibility element, hiding the
        // child mute/cut/reorder buttons' own identifiers from XCUITest
        // entirely (editor.cut became unfindable) — do not add one here.
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
                        // H-08: show the staged caption edit where one exists
                        Text(captionEdits[w.startFrame] ?? w.text)
                            .font(AppFont.callout)
                            .foregroundStyle(effectivelyCut ? Palette.textTertiary : Palette.textPrimary)
                            .strikethrough(effectivelyCut)
                            .underline(captionEdits[w.startFrame] != nil, color: Palette.accent)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("editor.word.\(w.startFrame)")
                    // H-08: long-press → fix the caption's text (typos, casing)
                    .contextMenu {
                        Button {
                            editDraft = captionEdits[w.startFrame] ?? w.text
                            editingCaptionFrame = w.startFrame
                        } label: { Label("Edit caption text", systemImage: "pencil") }
                    }
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
            || !captionEdits.isEmpty || !overlayTextEdits.isEmpty || !addedOverlays.isEmpty
    }

    /// H11: specifically caption/music/overlay changes — the ones the H7
    /// rough-cut preview CAN'T show (it only reflects structure: cuts,
    /// reorder, trims). Gates the "HD preview" button, which costs a real
    /// (if cheap) Lambda render, so it's only offered when it would actually
    /// show the creator something new.
    private var hasStyleChanges: Bool {
        captionsEnabled != baseCaptionsEnabled || captionStyle != baseCaptionStyle
            || overlays != baseOverlays
            || musicEnabled != baseMusic.enabled
            || (musicEnabled && (musicURL != baseMusic.url
                                 || musicVolume != baseMusic.volume
                                 || duckVoice != baseMusic.duck))
            || !captionEdits.isEmpty || !overlayTextEdits.isEmpty || !addedOverlays.isEmpty
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
        undoAvailable = (result["undo_available"] as? Bool) ?? false
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
        // H-08: reset staged edit state (load() also runs after split/undo reloads)
        captionEdits = [:]
        overlayTextEdits = [:]
        addedOverlays = []
        clipStyle = (edl["style"] as? String) ?? ""
        if editorCaps == nil {
            let allCaps = await store.backend.editorCapabilities()
            editorCaps = allCaps?[clipStyle]
        }
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
        overlays = ((edl["overlays"] as? [[String: Any]]) ?? []).enumerated().compactMap { i, o in
            guard let a = o["src_in"] as? Int, let b = o["src_out"] as? Int else { return nil }
            return OverlayRow(type: (o["type"] as? String) ?? "punch_in",
                              srcIn: a, srcOut: b, text: (o["text"] as? String) ?? "",
                              index: i)   // H-08: edit_overlay addresses this position
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
        // H-08: caption-text fixes (frame-addressed; empty word deletes the caption)
        for (frame, word) in captionEdits.sorted(by: { $0.key < $1.key }) {
            ops.append(["type": "edit_caption", "frame": frame, "word": word])
        }
        // H-08: overlay text edits BEFORE removals — edit_overlay addresses the
        // ORIGINAL edl index, which removals would invalidate.
        for (idx, text) in overlayTextEdits.sorted(by: { $0.key < $1.key }) {
            ops.append(["type": "edit_overlay", "index": idx, "text": text])
        }
        for o in baseOverlays where !overlays.contains(o) {
            ops.append(["type": "remove_overlays", "kind": o.type,
                        "start_frame": o.srcIn, "end_frame": o.srcOut])
        }
        // H-08: staged overlay additions (already style-gated by the add affordances)
        for o in addedOverlays {
            if o.type == "punch_in" {
                ops.append(["type": "add_punch_in", "start_frame": o.srcIn,
                            "end_frame": o.srcOut, "scale": 1.08])
            } else {
                ops.append(["type": "add_text_card", "start_frame": o.srcIn,
                            "end_frame": o.srcOut, "text": o.text])
            }
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

    /// H10: undo the last APPLIED server-side tweak. Discards the current
    /// unsaved local draft and reloads from the (now-reverted) EDL — undo
    /// targets what was actually applied, not in-progress local changes the
    /// creator hasn't tapped Apply for yet.
    private func undo() async {
        guard let jobId = clip.jobId, !undoing else { return }
        undoing = true
        defer { undoing = false }
        let resp = await store.backend.tweakClipOps(jobId: jobId, clipId: clip.id.uuidString,
                                                     ops: [["type": "undo"]])
        if resp["error"] as? Bool == true {
            if resp["transient"] as? Bool == true {
                transientMessage = resp["reply"] as? String ?? "Still busy — try again shortly."
            } else {
                phase = .failed(resp["reply"] as? String ?? "Couldn't undo.")
                return
            }
        }
        // H-06: a live undo re-renders — poll THIS clip (job status stays "ready")
        // so the reverted cut is actually live before reloading the editor state.
        if resp["needs_render"] as? Bool == true {
            phase = .rendering
            renderStartedAt = Date()
            store.setClipRendering(clip.id)
            let (_, message) = await pollClipUntilDone(jobId: jobId)
            guard !Task.isCancelled else { return }
            if let message { transientMessage = message }
        }
        phase = .loading
        await load()
    }

    /// H11: requests the G9 cheap proof render of the staged caption/music/
    /// overlay changes, then polls until it's ready — mirrors the shape of
    /// AppStore.pollJob but targets THIS clip's preview_status/preview_url
    /// specifically, never render_url/status (a preview never commits).
    private func requestHDPreview() {
        guard let jobId = clip.jobId else { return }
        hdPreviewPollTask?.cancel()
        hdPreviewPhase = .requesting
        hdPreviewPollTask = Task {
            let ops = computeOps()
            guard !ops.isEmpty else { hdPreviewPhase = .idle; return }
            let resp = await store.backend.tweakClipOps(jobId: jobId, clipId: clip.id.uuidString,
                                                         ops: ops, preview: true)
            guard !Task.isCancelled else { return }
            if resp["error"] as? Bool == true {
                hdPreviewPhase = .failed(resp["reply"] as? String ?? "Couldn't build a preview.")
                return
            }
            guard resp["preview_requested"] as? Bool == true else {
                hdPreviewPhase = .failed("Preview isn't available for this clip right now.")
                return
            }
            hdPreviewPhase = .rendering
            for _ in 0..<30 {   // ~60s budget at 2s intervals
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                guard !Task.isCancelled else { return }
                guard let result = await store.backend.pollClipJob(jobId: jobId),
                      let jobClips = result["clips"] as? [[String: Any]],
                      let mine = jobClips.first(where: {
                          ($0["clip_id"] as? String)?.lowercased() == clip.id.uuidString.lowercased()
                      }) else { continue }
                let previewStatus = mine["preview_status"] as? String
                if previewStatus == "ready", let url = mine["preview_url"] as? String {
                    hdPreviewPhase = .ready(url)
                    return
                }
                if previewStatus == "failed" {
                    hdPreviewPhase = .failed(mine["preview_error"] as? String ?? "The preview render failed.")
                    return
                }
            }
            guard !Task.isCancelled else { return }
            hdPreviewPhase = .failed("The preview is taking longer than expected. Try again shortly.")
        }
    }

    /// H-06 (audit D7): watch THIS clip until its re-render lands. AppStore.pollJob
    /// watches the JOB status — which stays "ready" during a tweak re-render — so its
    /// loop exited on the first poll while the clip was still rendering, and the clip
    /// showed "AI is editing…" forever. Mirrors TweakChatSheet.startPolling, and also
    /// surfaces a failed re-render (clip "failed" OR last_render_failed on a "ready"
    /// clip whose old cut survived).
    private func pollClipUntilDone(jobId: String) async -> (ready: Bool, message: String?) {
        for _ in 0..<60 {
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            if Task.isCancelled { return (false, nil) }
            guard let result = await store.backend.pollClipJob(jobId: jobId),
                  let jobClips = result["clips"] as? [[String: Any]],
                  // UUID-compare (backend ids are lowercase, uuidString is uppercase)
                  let mine = jobClips.first(where: {
                      UUID(uuidString: ($0["clip_id"] as? String) ?? "") == clip.id
                  }) else { continue }
            let status = mine["status"] as? String ?? ""
            if status == "ready" {
                store.applyTweakResult(clip.id, remoteURL: mine["render_url"] as? String)
                if mine["last_render_failed"] as? Bool == true {
                    // G-05: the re-render failed but the previous cut is still live.
                    let err = (mine["last_render_error"] as? String)?.trimmingCharacters(in: .whitespaces)
                    return (false, (err?.isEmpty == false ? err! : "That edit's render failed — your previous cut is untouched."))
                }
                return (true, nil)
            }
            if status == "failed" {
                let code = mine["error"] as? String
                let detail = mine["error_detail"] as? String
                return (false, store.friendlyRenderError(code, detail: detail))
            }
        }
        return (false, "Still working — check back in the Library in a bit.")
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
            renderStartedAt = Date()
            store.setClipRendering(clip.id)
            // H-06: per-CLIP poll (job status stays "ready" during a tweak re-render).
            let (ready, message) = await pollClipUntilDone(jobId: jobId)
            guard !Task.isCancelled else { return }   // H1: dismissed mid-poll — onDisappear owns cleanup now
            if ready {
                dismiss()
            } else {
                phase = .failed(message ?? "Couldn't finish that render.")
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
