import SwiftUI
import AVFoundation

// MARK: - ProEditorView — the CapCut/TikTok-style direct-manipulation editor.
// Loads the clip's server EDL + transcript, edits a local draft (instant preview via
// EditorSession + EditorPlayerController), and on Save flushes the sequential op log to
// the backend for the real re-render. Reached from LibraryView → "Edit manually".

struct ProEditorView: View {
    @Environment(AppStore.self) var store
    @Environment(\.dismiss) var dismiss
    let clip: Clip

    enum Phase: Equatable { case loading, editing, applying, rendering, failed(String) }
    enum Mode: String, CaseIterable { case edit = "Edit", sound = "Sound", text = "Text", effects = "Effects" }

    @State var phase: Phase = .loading
    @State var session: EditorSession?
    @State var player: EditorPlayerController?
    @State var filmstrip: FilmstripCache?
    @State var words: [WordSpan] = []
    @State var caps: [String: Bool]? = nil
    @State var mode: Mode = .edit
    @State var selectedSeg: Int? = nil          // index into segments (source index)
    @State var selectedOverlay: Int? = nil      // index into draft.overlays (chip lane)
    @State var editingOverlayIndex: Int? = nil  // text-card text edit in flight
    @State var captionsOn = false               // #1: enabled-state tracked in the view (local
                                                // captions may be empty while enabled → preview from words)
    @State var pointsPerSecond: CGFloat = 18
    @State var applyTask: Task<Void, Never>?
    @State var renderStartedAt: Date?
    @State var transient: String?
    @State var showMusicSheet = false
    @State var showTextCardAlert = false
    @State var editDraft = ""
    @State var editingPhrase: CaptionPhrase?     // phrase-level caption edit in flight
    @State var showCaptionList = false           // the batch caption list editor
    @State var hapticTick = 0                    // I-7: .sensoryFeedback trigger
    // One-time first-run coach: three lines of orientation, dismissed forever after.
    @AppStorage("editorPro.coachShown") private var coachShown = false
    @State private var showCoach = false
    // UX-4: slider drafts — the op commits once, on gesture end.
    @State private var musicVolDraft: Double = 0.15
    @State private var clipVolDraft: Double = 1.0
    // UX-7: X on a dirty session confirms before discarding.
    @State private var confirmDiscard = false

    struct WordSpan: Identifiable { var id: Int { startFrame }; let text: String; let startFrame: Int; let endFrame: Int }

    var body: some View {
        NavigationStack {
            ZStack {
                // Full-screen dark fill FIRST so the editor stays immersive edge to
                // edge — a .background() on the content only wraps its natural height
                // and leaves the safe-area bands white. This fills everything.
                Palette.night.ignoresSafeArea()
                Group {
                    switch phase {
                    case .loading:  ProgressView("Loading your edit…").frame(maxWidth: .infinity, maxHeight: .infinity)
                    case .applying: ProgressView("Applying…").frame(maxWidth: .infinity, maxHeight: .infinity)
                    case .rendering: renderingView
                    case .failed(let m): failedView(m)
                    case .editing:  editor
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                if showCoach { coachOverlay }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbarContent }
            .toolbarBackground(Palette.night, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
        .marqueConfirm($confirmDiscard, title: "Discard your edits?",
                       message: "You have unsaved changes. Save re-cuts the clip; discarding loses them.",
                       confirm: "Discard edits", destructive: true, cancel: "Keep editing") { dismiss() }
        // Dialogs + sheets live on the ROOT, not modeToolbar — the toolbar swaps out while the
        // caption list is open (dialog would never render), and an .overlay hosted by a 64pt
        // view clips its accessibility/hit-testing to that frame.
        .sheet(isPresented: $showMusicSheet) { musicSheet }
        .marqueInput($showTextCardAlert, title: "Text card", placeholder: "Card text",
                     text: $editDraft, confirm: "Add") { addTextCard(editDraft) }
        .marqueInput(Binding(get: { editingPhrase != nil }, set: { if !$0 { editingPhrase = nil } }),
                     title: "Edit caption", placeholder: "Caption text", text: $editDraft) { commitPhraseEdit() }
        .marqueInput(Binding(get: { editingOverlayIndex != nil }, set: { if !$0 { editingOverlayIndex = nil } }),
                     title: "Edit text card", placeholder: "Text", text: $editDraft) { commitOverlayTextEdit() }
        .sensoryFeedback(.impact(weight: .light), trigger: hapticTick)   // I-7 haptics
        .task { await load() }
        .onChange(of: phase) { _, p in
            if p == .editing, !coachShown { showCoach = true }
        }
        .onDisappear { applyTask?.cancel(); player?.teardown() }
    }

    /// First-open orientation — three lines, one button, never again.
    private var coachOverlay: some View {
        ZStack {
            Color.black.opacity(0.55).ignoresSafeArea()
            VStack(alignment: .leading, spacing: Space.md) {
                Text("Your editor")
                    .font(Typeface.display(22, .semibold)).foregroundStyle(.white)
                VStack(alignment: .leading, spacing: Space.sm) {
                    coachRow("hand.tap", "Tap a clip to select it — trim with the edge handles")
                    coachRow("square.split.2x1", "Split cuts at the playhead — scrub to the exact moment first")
                    coachRow("textformat", "Tap a caption strip on the timeline to fix its words")
                }
                Button {
                    coachShown = true
                    withAnimation(.easeOut(duration: 0.2)) { showCoach = false }
                } label: {
                    Text("Got it").font(AppFont.headline).foregroundStyle(Palette.night)
                        .frame(maxWidth: .infinity).frame(height: 46)
                        .background(Color.white).clipShape(Capsule())
                }
                .buttonStyle(PressableStyle())
                .accessibilityIdentifier("editorPro.coachDismiss")
            }
            .padding(Space.xl)
            .background(Palette.ink)
            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                .strokeBorder(Color.white.opacity(0.12), lineWidth: 1))
            .padding(Space.xl)
        }
    }

    private func coachRow(_ icon: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: Space.sm) {
            Image(systemName: icon).font(.system(size: 14)).foregroundStyle(Palette.accent)
                .frame(width: 22)
            Text(text).font(AppFont.callout).foregroundStyle(.white.opacity(0.85))
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: toolbar

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            // UX-7: X on unsaved edits asks first — silently discarding a session of work
            // is the single most rage-inducing mistake an editor can make.
            Button {
                if phase == .editing, session?.isDirty == true { confirmDiscard = true } else { dismiss() }
            } label: { Image(systemName: "xmark") }.tint(.white)
                .accessibilityIdentifier("editorPro.close")
        }
        ToolbarItemGroup(placement: .principal) {
            if phase == .editing, let session {
                // UX-8: undo/redo can change segment/overlay indices — clear stale selections.
                Button { session.undo(); selectedSeg = nil; selectedOverlay = nil; refreshPlayer() } label: { Image(systemName: "arrow.uturn.backward") }
                    .tint(.white).disabled(!session.canUndo).accessibilityIdentifier("editorPro.undo")
                Button { session.redo(); selectedSeg = nil; selectedOverlay = nil; refreshPlayer() } label: { Image(systemName: "arrow.uturn.forward") }
                    .tint(.white).disabled(!session.canRedo).accessibilityIdentifier("editorPro.redo")
            }
        }
        ToolbarItem(placement: .topBarTrailing) {
            if phase == .editing {
                // #11: overlay/caption/music edits re-render (~1 min); structural cuts apply
                // instantly. Label the cost so a text-card add isn't a surprise wait.
                Button { save() } label: { Text(saveNeedsRender ? "Render" : "Save").fontWeight(.semibold) }
                    .tint(Palette.accent).disabled(!(session?.isDirty ?? false))
                    .accessibilityIdentifier("editorPro.save")
            }
        }
    }

    /// True when the pending edits include anything that re-renders server-side (overlays,
    /// captions, music, b-roll) rather than the instant structural ops.
    var saveNeedsRender: Bool {
        let structural: Set = ["cut_range", "restore_range", "split_segment", "reorder_segments",
                               "mute_range", "set_segment_volume"]
        guard let ops = session?.flattenedOps() else { return false }
        return ops.contains { !structural.contains($0["type"] as? String ?? "") }
    }

    // MARK: editing layout

    @ViewBuilder private var editor: some View {
        VStack(spacing: 0) {
            playerSurface                       // flexes to fill; keeps the toolbar pinned bottom
            if let t = transient { transientBar(t) }
            if showCaptionList {
                // CapCut pattern: the caption list replaces the timeline pane inline —
                // a system sheet here is invisible to accessibility/automation.
                captionListPanel
            } else {
                timelinePane
                contextStrip
                modeDrawer
                modeToolbar
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
    }

    @ViewBuilder private var modeDrawer: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.md) {
                switch mode {
                case .edit:
                    Text("Trim with the handles · Split cuts at the playhead · Move ◀ ▶ reorders")
                        .font(AppFont.caption).foregroundStyle(.white.opacity(0.55))
                case .sound:
                    drawerButton(session?.draft.music == nil ? "Add sound" : "Change sound", "music.note") { showMusicSheet = true }
                        .accessibilityIdentifier("editorPro.addSound")
                    if session?.draft.music != nil {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Music volume").font(.system(size: 9)).foregroundStyle(.white.opacity(0.6))
                            // UX-4: one op per DRAG (EditorSession's one-gesture-one-undo-step
                            // invariant) — the draft value tracks the thumb; the op commits on release.
                            Slider(value: $musicVolDraft, in: 0.0...0.5, onEditingChanged: { editing in
                                if editing { musicVolDraft = session?.draft.music?.volume ?? 0.15 }
                                else { setMusicVolume(musicVolDraft) }
                            }).frame(width: 120).tint(Palette.accent)
                                .onAppear { musicVolDraft = session?.draft.music?.volume ?? 0.15 }
                        }
                    }
                    if let seg = selectedSeg {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Clip volume").font(.system(size: 9)).foregroundStyle(.white.opacity(0.6))
                            Slider(value: $clipVolDraft, in: 0.0...2.0, onEditingChanged: { editing in
                                if editing { clipVolDraft = clipVolume(seg) }
                                else { setClipVolume(seg, clipVolDraft) }
                            }).frame(width: 120).tint(Palette.accent)
                                .accessibilityIdentifier("editorPro.clipVolume")
                                .onAppear { clipVolDraft = clipVolume(seg) }
                        }
                    }
                case .text:
                    // #1: caption on/off reads the tracked state (captions can be turned back ON).
                    drawerButton(captionsOn ? "Captions on" : "Captions off",
                                 captionsOn ? "captions.bubble.fill" : "captions.bubble") { toggleCaptions(!captionsOn) }
                        .accessibilityIdentifier("editorPro.captionsToggle")
                    if captionsOn, !phrases.isEmpty {
                        // The batch list editor (CapCut "Batch edit") — where misheard words get fixed.
                        drawerButton("Edit captions", "list.bullet.rectangle") { showCaptionList = true }
                            .accessibilityIdentifier("editorPro.editCaptions")
                    }
                    if captionsOn {
                        ForEach(["clean", "bold-word", "karaoke"], id: \.self) { st in
                            drawerButton(st.capitalized, "textformat", active: session?.draft.captionStyle == st) { setCaptionStyle(st) }
                        }
                    }
                    // #5: text card is only supported for green-screen / duet styles — gate the
                    // button so a talking-head creator doesn't type one only to be rejected.
                    if textCardsSupported {
                        drawerButton("Text card", "text.badge.plus") { editDraft = ""; showTextCardAlert = true }
                            .accessibilityIdentifier("editorPro.addTextCard")
                    }
                case .effects:
                    // #8: fall back to the LOCAL style capability when the server caps didn't
                    // load (keyless/network hiccup) so Zoom doesn't silently vanish.
                    if punchInsSupported { drawerButton("Add zoom", "plus.magnifyingglass") { addPunchInOnHook() }.accessibilityIdentifier("editorPro.addPunchIn") }
                    if caps?["broll"] ?? false { drawerButton("Add b-roll", "photo.on.rectangle") { addBroll("relevant") }.accessibilityIdentifier("editorPro.addBroll") }
                    if !punchInsSupported && !(caps?["broll"] ?? false) {
                        Text("No effects for this style").font(AppFont.caption).foregroundStyle(.white.opacity(0.5)).accessibilityIdentifier("editorPro.effects.empty")
                    }
                }
            }.padding(.horizontal, Space.md)
        }
        .frame(height: 52).background(Palette.ink.opacity(0.25))
    }

    private func drawerButton(_ label: String, _ icon: String, active: Bool = false, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 5) { Image(systemName: icon); Text(label).font(AppFont.caption) }
                .foregroundStyle(active ? Palette.ink : .white)
                .padding(.horizontal, Space.md).frame(height: 34)
                .background(active ? Palette.onInk : Color.white.opacity(0.12))
                .clipShape(Capsule())
        }
    }

    private func clipVolume(_ segIdx: Int) -> Double {
        guard let seg = session?.draft.segments[safe: segIdx] else { return 1.0 }
        return session?.draft.volumeRanges.first { $0.srcIn <= seg.srcIn && $0.srcOut >= seg.srcOut }?.volume ?? 1.0
    }

    private var playerSurface: some View {
        ZStack {
            // Video + captions scale together during a punch-in window (L1 preview of the
            // rendered zoom); the play/time controls below stay unscaled.
            ZStack {
                if let player, !player.placeholder {
                    PlayerLayerView(player: player.player)
                } else {
                    // Placeholder mode (keyless mock clip has no source video) — still fully editable.
                    Rectangle().fill(Palette.ink.opacity(0.85))
                        .overlay(Image(systemName: "film").font(.system(size: 40)).foregroundStyle(.white.opacity(0.3)))
                }
                captionSimOverlay
                textCardSimOverlay
            }
            .scaleEffect(currentPunchScale)
            .animation(.easeInOut(duration: 0.25), value: currentPunchScale)
            VStack {
                Spacer()
                HStack {
                    Button { player?.togglePlay() } label: {
                        Image(systemName: (player?.isPlaying ?? false) ? "pause.fill" : "play.fill")
                            .font(.system(size: 16)).foregroundStyle(.white)
                            .frame(width: 40, height: 40).background(.black.opacity(0.4)).clipShape(Circle())
                    }.accessibilityIdentifier("editorPro.playPause")
                    Spacer()
                    Text(timeReadout).font(AppFont.caption.monospacedDigit()).foregroundStyle(.white)
                        .padding(.horizontal, 8).padding(.vertical, 4)
                        .background(.black.opacity(0.4)).clipShape(Capsule())
                        .accessibilityIdentifier("editorPro.timeReadout")
                }.padding(Space.md)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(maxHeight: .infinity)          // fill remaining space so the toolbar pins to the bottom (CapCut layout)
        .contentShape(Rectangle())
        .onTapGesture { player?.togglePlay() }
        .clipped()
    }

    private var timeReadout: String {
        let cur = player?.currentOutputTime ?? 0, tot = player?.totalOutputTime ?? 0
        func fmt(_ s: Double) -> String { String(format: "%d:%02d", Int(s) / 60, Int(s) % 60) }
        return "\(fmt(cur)) / \(fmt(tot))"
    }

    // MARK: caption + punch-in local sim (L1 fidelity)

    @ViewBuilder private var captionSimOverlay: some View {
        if let d = session?.draft, !d.captions.isEmpty, let word = currentCaptionWord(d) {
            VStack { Spacer()
                Text(word).font(.system(size: 20, weight: .heavy))
                    .foregroundStyle(.white).shadow(radius: 4)
                    .padding(.horizontal, 10).padding(.vertical, 4)
                    .background(d.captionStyle == "bold-word" ? Color.black.opacity(0.5) : .clear)
                    .padding(.bottom, 60)
            }
        }
    }

    /// The active punch-in overlay's zoom at the playhead (1.0 outside any window) —
    /// drives the L1 scale preview on the player surface.
    private var currentPunchScale: Double {
        guard let d = session?.draft else { return 1.0 }
        let f = playheadSourceFrame
        return d.overlays.first { $0.type == "punch_in" && $0.srcIn <= f && f < $0.srcOut }?.scale ?? 1.0
    }

    /// L1 sim of a text card: a centered slab over a dim layer while the playhead is inside
    /// its window — so the card is visible before the server render.
    @ViewBuilder private var textCardSimOverlay: some View {
        if let d = session?.draft {
            let f = playheadSourceFrame
            if let card = d.overlays.first(where: { $0.type == "text_card" && $0.srcIn <= f && f < $0.srcOut }),
               !card.text.isEmpty {
                ZStack {
                    Color.black.opacity(0.35)
                    Text(card.text)
                        .font(.system(size: 22, weight: .heavy))
                        .foregroundStyle(.white)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, Space.xl)
                        .shadow(radius: 6)
                }
            }
        }
    }

    private func currentCaptionWord(_ d: EditorDocument) -> String? {
        let srcFrame = secondsToFrame(d.sourceSeconds(forOutput: player?.currentOutputTime ?? 0))
        guard let cap = d.captions.last(where: { $0.frame <= srcFrame }) else { return nil }
        // UX-3: bound the word's display window with the transcript so the last word
        // doesn't burn on screen through every silence.
        if let span = words.first(where: { $0.startFrame == cap.frame }) ?? words.last(where: { $0.startFrame <= cap.frame }),
           srcFrame > span.endFrame + 15 {
            return nil
        }
        return cap.word
    }

    // (internal: +Actions' split-at-playhead reads it too)
    var playheadSourceFrame: Int {
        guard let d = session?.draft else { return 0 }
        return secondsToFrame(d.sourceSeconds(forOutput: player?.currentOutputTime ?? 0))
    }

    // MARK: timeline

    /// Transcript words grouped into caption phrase clips; edited caption text wins.
    var phrases: [CaptionPhrase] {
        buildCaptionPhrases(words: words, captions: session?.draft.captions ?? [])
    }

    /// The music track's display name (catalog lookup by URL, filename fallback).
    private var musicName: String? {
        guard let m = session?.draft.music else { return nil }
        return MusicCatalog.tracks.first { $0.url == m.url }?.name
            ?? URL(string: m.url)?.deletingPathExtension().lastPathComponent ?? "Music"
    }

    /// The lane stack is dynamic — the pane grows with the tracks it actually shows.
    private var timelineHeight: CGFloat {
        var h: CGFloat = 12 + 2 + 56 + 8                                  // ruler + video + padding
        if captionsOn, !phrases.isEmpty { h += 20 }
        if !(session?.draft.overlays.isEmpty ?? true) { h += 22 }
        h += 18                                                            // voice lane (always)
        if session?.draft.music != nil || mode == .sound { h += 20 }
        return h + 8
    }

    private var timelinePane: some View {
        EditorTimeline(
            document: session?.draft ?? EditorDocument(),
            player: player,
            filmstrip: filmstrip,
            pointsPerSecond: $pointsPerSecond,
            selectedSeg: $selectedSeg,
            selectedOverlay: $selectedOverlay,
            onTrim: { segIdx, edge, newFrame in trim(segIdx: segIdx, edge: edge, to: newFrame) },
            onReorder: { order in reorder(order) },
            phrases: phrases,
            captionsOn: captionsOn,
            musicName: musicName,
            musicVolume: session?.draft.music?.volume ?? 0.15,
            showMusicAdd: mode == .sound && session?.draft.music == nil,
            onTapPhrase: { p in beginPhraseEdit(p); bumpHaptic() },
            onTapMusic: { mode = .sound; showMusicSheet = session?.draft.music == nil },
            onTapVoice: { segIdx in
                // Voice strip tap = select that clip in Sound mode — volume + mute right there.
                mode = .sound
                withAnimation(.easeOut(duration: 0.15)) { selectedOverlay = nil; selectedSeg = segIdx }
            }
        )
        .frame(height: timelineHeight)
        .background(Palette.ink.opacity(0.6))
    }

    // MARK: context strip (selection actions)

    @ViewBuilder private var contextStrip: some View {
        if let ov = selectedOverlay, let overlay = session?.draft.overlays[safe: ov] {
            // Overlay selected (chip lane): the strip swaps to overlay ops. Scrolls —
            // the zoom controls (intensity + duration + delete) overflow a fixed row.
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Space.lg) {
                    contextButton("Delete", "trash") { deleteOverlay(ov); bumpHaptic() }
                        .accessibilityIdentifier("editorPro.ctx.deleteOverlay")
                    if overlay.type == "text_card" {
                        contextButton("Edit text", "pencil") { beginOverlayTextEdit(ov); bumpHaptic() }
                            .accessibilityIdentifier("editorPro.ctx.editOverlayText")
                    }
                    if overlay.type == "punch_in" {
                        // Zoom block controls (Screen Studio's model): intensity presets + duration.
                        ForEach([("Subtle", 1.05), ("Medium", 1.1), ("Strong", 1.2)], id: \.0) { label, scale in
                            Button { setZoomIntensity(ov, scale: scale); bumpHaptic() } label: {
                                Text(label).font(.system(size: 10, weight: abs(overlay.scale - scale) < 0.02 ? .bold : .regular))
                                    .fixedSize()
                                    .foregroundStyle(abs(overlay.scale - scale) < 0.02 ? Palette.night : .white)
                                    .padding(.horizontal, 8).padding(.vertical, 5)
                                    .background(Capsule().fill(abs(overlay.scale - scale) < 0.02 ? Color.white : Color.white.opacity(0.12)))
                            }
                            .accessibilityIdentifier("editorPro.ctx.zoom\(label)")
                        }
                        contextButton("Shorter", "minus") { adjustOverlayDuration(ov, deltaFrames: -15); bumpHaptic() }
                        contextButton("Longer", "plus") { adjustOverlayDuration(ov, deltaFrames: 15); bumpHaptic() }
                        Text(String(format: "%.1fs", framesToSeconds(overlay.srcOut - overlay.srcIn)))
                            .font(AppFont.caption).foregroundStyle(.white.opacity(0.45)).monospacedDigit()
                    } else {
                        Text("Text card").font(AppFont.caption).foregroundStyle(.white.opacity(0.45))
                    }
                }
                .padding(.horizontal, Space.md)
            }
            .frame(height: 44)
            .background(Palette.ink.opacity(0.4))
        } else {
            segContextStrip
        }
    }

    @ViewBuilder private var segContextStrip: some View {
        // Tools are ALWAYS visible (CapCut pattern) — disabled until a clip is selected, so
        // the creator discovers what's possible instead of staring at a hint sentence.
        let seg = selectedSeg
        HStack(spacing: Space.lg) {
            contextButton("Split", "square.split.2x1") { if let s = seg { splitSelected(s); bumpHaptic() } }
                .disabled(seg == nil).opacity(seg == nil ? 0.35 : 1)
            contextButton("Delete", "trash") { if let s = seg { deleteSelected(s); bumpHaptic() } }
                .disabled(seg == nil).opacity(seg == nil ? 0.35 : 1)
            // I-7: explicit reorder (drag-to-reorder fights the timeline's other gestures).
            contextButton("Move ◀", "arrow.left") { moveSelected(by: -1); bumpHaptic() }
                .disabled(!canMoveSelected(by: -1)).opacity(canMoveSelected(by: -1) ? 1 : 0.35)
                .accessibilityIdentifier("editorPro.moveLeft")
            contextButton("Move ▶", "arrow.right") { moveSelected(by: 1); bumpHaptic() }
                .disabled(!canMoveSelected(by: 1)).opacity(canMoveSelected(by: 1) ? 1 : 0.35)
                .accessibilityIdentifier("editorPro.moveRight")
            if mode == .sound, let s = seg {
                contextButton(mutedState(s) ? "Unmute" : "Mute", "speaker.slash") { toggleMute(s); bumpHaptic() }
            }
            Spacer()
            if seg == nil {
                Text("Tap a clip").font(AppFont.caption).foregroundStyle(.white.opacity(0.45))
            }
        }
        .frame(height: 44).padding(.horizontal, Space.md)
        .background(Palette.ink.opacity(0.4))
    }

    private func contextButton(_ label: String, _ icon: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 2) {
                Image(systemName: icon).font(.system(size: 15))
                Text(label).font(.system(size: 9))
            }.foregroundStyle(.white)
        }.accessibilityIdentifier("editorPro.ctx.\(label.lowercased())")
    }

    // MARK: mode toolbar + drawers

    private var modeToolbar: some View {
        HStack(spacing: 0) {
            ForEach(visibleModes, id: \.self) { m in
                Button { mode = m; openModeDrawer(m) } label: {
                    VStack(spacing: 4) {
                        Image(systemName: iconFor(m)).font(.system(size: 18))
                        Text(m.rawValue).font(.system(size: 11))
                    }
                    .foregroundStyle(mode == m ? Palette.accent : .white.opacity(0.7))
                    .frame(maxWidth: .infinity)
                }
                .accessibilityIdentifier("editorPro.mode.\(m.rawValue.lowercased())")
            }
        }
        .frame(height: 64).background(Palette.ink)
    }

    // #5/#8: capability helpers fall back to the LOCAL style rules (mirrors LocalEDLEngine's
    // gates) so tabs/buttons stay STABLE when the server caps dict didn't load — punch-ins &
    // text cards are locally supported for these styles regardless of the network.
    private var draftStyle: String { session?.draft.style ?? "talking_head" }
    var punchInsSupported: Bool { (caps?["punch_ins"] ?? false) || ["talking_head", "duet_split"].contains(draftStyle) }
    var textCardsSupported: Bool { (caps?["text_cards"] ?? false) || ["green_screen", "duet_split"].contains(draftStyle) }

    private var visibleModes: [Mode] {
        // Effects is always available when zoom or b-roll could apply; never let a nil caps
        // dict make the whole tab vanish.
        Mode.allCases.filter { $0 != .effects || punchInsSupported || (caps?["broll"] ?? false) }
    }
    private func iconFor(_ m: Mode) -> String {
        switch m { case .edit: "scissors"; case .sound: "music.note"; case .text: "textformat"; case .effects: "sparkles" }
    }

    private func openModeDrawer(_ m: Mode) {
        // Sound mode no longer auto-pops the music sheet — the timeline's "+ Add sound"
        // lane and the drawer button advertise it (empty lanes advertise themselves).
    }
}
