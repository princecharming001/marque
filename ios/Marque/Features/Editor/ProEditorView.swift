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
    @State var pointsPerSecond: CGFloat = 18
    @State var applyTask: Task<Void, Never>?
    @State var renderStartedAt: Date?
    @State var transient: String?
    @State var showMusicSheet = false
    @State var showTextCardAlert = false
    @State var editDraft = ""
    @State var editingCaptionFrame: Int?
    @State var hapticTick = 0                    // I-7: .sensoryFeedback trigger

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
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { toolbarContent }
            .toolbarBackground(Palette.night, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
        .sensoryFeedback(.impact(weight: .light), trigger: hapticTick)   // I-7 haptics
        .task { await load() }
        .onDisappear { applyTask?.cancel(); player?.teardown() }
    }

    // MARK: toolbar

    @ToolbarContentBuilder private var toolbarContent: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button { dismiss() } label: { Image(systemName: "xmark") }.tint(.white)
                .accessibilityIdentifier("editorPro.close")
        }
        ToolbarItemGroup(placement: .principal) {
            if phase == .editing, let session {
                Button { session.undo(); refreshPlayer() } label: { Image(systemName: "arrow.uturn.backward") }
                    .tint(.white).disabled(!session.canUndo).accessibilityIdentifier("editorPro.undo")
                Button { session.redo(); refreshPlayer() } label: { Image(systemName: "arrow.uturn.forward") }
                    .tint(.white).disabled(!session.canRedo).accessibilityIdentifier("editorPro.redo")
            }
        }
        ToolbarItem(placement: .topBarTrailing) {
            if phase == .editing {
                Button { save() } label: { Text("Save").fontWeight(.semibold) }
                    .tint(Palette.accent).disabled(!(session?.isDirty ?? false))
                    .accessibilityIdentifier("editorPro.save")
            }
        }
    }

    // MARK: editing layout

    @ViewBuilder private var editor: some View {
        VStack(spacing: 0) {
            playerSurface                       // flexes to fill; keeps the toolbar pinned bottom
            if let t = transient { transientBar(t) }
            timelinePane
            if mode == .text, !words.isEmpty { wordStrip }   // I-7: per-word caption editing
            contextStrip
            modeDrawer
            modeToolbar
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
    }

    @ViewBuilder private var modeDrawer: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.md) {
                switch mode {
                case .edit:
                    Text("Trim with the handles · tap Split/Delete · long-press a clip to reorder")
                        .font(AppFont.caption).foregroundStyle(.white.opacity(0.55))
                case .sound:
                    drawerButton(session?.draft.music == nil ? "Add sound" : "Change sound", "music.note") { showMusicSheet = true }
                        .accessibilityIdentifier("editorPro.addSound")
                    if session?.draft.music != nil {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Music volume").font(.system(size: 9)).foregroundStyle(.white.opacity(0.6))
                            Slider(value: Binding(get: { session?.draft.music?.volume ?? 0.15 },
                                                  set: { setMusicVolume($0) }), in: 0.0...0.5).frame(width: 120).tint(Palette.accent)
                        }
                    }
                    if let seg = selectedSeg {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Clip volume").font(.system(size: 9)).foregroundStyle(.white.opacity(0.6))
                            Slider(value: Binding(get: { clipVolume(seg) }, set: { setClipVolume(seg, $0) }), in: 0.0...2.0)
                                .frame(width: 120).tint(Palette.accent).accessibilityIdentifier("editorPro.clipVolume")
                        }
                    }
                case .text:
                    let on = !(session?.draft.captions.isEmpty ?? true)
                    drawerButton(on ? "Captions on" : "Captions off", on ? "captions.bubble.fill" : "captions.bubble") { toggleCaptions(!on) }
                        .accessibilityIdentifier("editorPro.captionsToggle")
                    if on {
                        ForEach(["clean", "bold-word", "karaoke"], id: \.self) { st in
                            drawerButton(st.capitalized, "textformat", active: session?.draft.captionStyle == st) { setCaptionStyle(st) }
                        }
                    }
                    drawerButton("Text card", "text.badge.plus") { editDraft = ""; showTextCardAlert = true }
                        .accessibilityIdentifier("editorPro.addTextCard")
                case .effects:
                    if caps?["punch_ins"] ?? false { drawerButton("Zoom on hook", "plus.magnifyingglass") { addPunchInOnHook() }.accessibilityIdentifier("editorPro.addPunchIn") }
                    if caps?["broll"] ?? false { drawerButton("Add b-roll", "photo.on.rectangle") { addBroll("relevant") }.accessibilityIdentifier("editorPro.addBroll") }
                    if !(caps?["punch_ins"] ?? false) && !(caps?["broll"] ?? false) {
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
            if let player, !player.placeholder {
                PlayerLayerView(player: player.player)
            } else {
                // Placeholder mode (keyless mock clip has no source video) — still fully editable.
                Rectangle().fill(Palette.ink.opacity(0.85))
                    .overlay(Image(systemName: "film").font(.system(size: 40)).foregroundStyle(.white.opacity(0.3)))
            }
            captionSimOverlay
            punchInSimOverlay
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

    @ViewBuilder private var punchInSimOverlay: some View {
        EmptyView()   // scaleEffect handled on the player surface below when in a punch-in window
    }

    private func currentCaptionWord(_ d: EditorDocument) -> String? {
        let srcFrame = secondsToFrame(d.sourceSeconds(forOutput: player?.currentOutputTime ?? 0))
        return d.captions.last(where: { $0.frame <= srcFrame })?.word
    }

    // I-7: the transcript as tappable word chips — tap a word to fix its caption. The chip
    // nearest the playhead is highlighted so the creator knows where they are.
    private var playheadSourceFrame: Int {
        guard let d = session?.draft else { return 0 }
        return secondsToFrame(d.sourceSeconds(forOutput: player?.currentOutputTime ?? 0))
    }
    private var wordStrip: some View {
        let cur = playheadSourceFrame
        return ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(Array(words.enumerated()), id: \.offset) { i, w in
                        let active = cur >= w.startFrame && cur < w.endFrame
                        Button { beginCaptionEdit(frame: w.startFrame, current: w.text); bumpHaptic() } label: {
                            Text(w.text)
                                .font(.system(size: 13, weight: active ? .semibold : .regular))
                                .foregroundStyle(active ? Palette.night : .white)
                                .padding(.horizontal, 9).padding(.vertical, 6)
                                .background(Capsule().fill(active ? Palette.accent : Color.white.opacity(0.12)))
                        }
                        .buttonStyle(.plain)
                        .id(i)
                        .accessibilityIdentifier("editorPro.word.\(i)")
                    }
                }
                .padding(.horizontal, Space.md)
            }
            .frame(height: 40)
            .accessibilityIdentifier("editorPro.wordStrip")
            .onChange(of: cur) { _, _ in
                if let idx = words.firstIndex(where: { cur >= $0.startFrame && cur < $0.endFrame }) {
                    withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(idx, anchor: .center) }
                }
            }
        }
    }

    // MARK: timeline

    private var timelinePane: some View {
        EditorTimeline(
            document: session?.draft ?? EditorDocument(),
            player: player,
            filmstrip: filmstrip,
            pointsPerSecond: $pointsPerSecond,
            selectedSeg: $selectedSeg,
            onTrim: { segIdx, edge, newFrame in trim(segIdx: segIdx, edge: edge, to: newFrame) },
            onReorder: { order in reorder(order) }
        )
        .frame(height: 132)
        .background(Palette.ink.opacity(0.6))
    }

    // MARK: context strip (selection actions)

    @ViewBuilder private var contextStrip: some View {
        HStack(spacing: Space.lg) {
            if let seg = selectedSeg {
                contextButton("Split", "square.split.2x1") { splitSelected(seg); bumpHaptic() }
                contextButton("Delete", "trash") { deleteSelected(seg); bumpHaptic() }
                // I-7: explicit reorder (drag-to-reorder fights the timeline's other gestures).
                contextButton("Move ◀", "arrow.left") { moveSelected(by: -1); bumpHaptic() }
                    .disabled(!canMoveSelected(by: -1)).opacity(canMoveSelected(by: -1) ? 1 : 0.35)
                    .accessibilityIdentifier("editorPro.moveLeft")
                contextButton("Move ▶", "arrow.right") { moveSelected(by: 1); bumpHaptic() }
                    .disabled(!canMoveSelected(by: 1)).opacity(canMoveSelected(by: 1) ? 1 : 0.35)
                    .accessibilityIdentifier("editorPro.moveRight")
                if mode == .sound {
                    contextButton(mutedState(seg) ? "Unmute" : "Mute", "speaker.slash") { toggleMute(seg); bumpHaptic() }
                }
            } else {
                Text("Tap a clip · Split / Delete / Move from here").font(AppFont.caption).foregroundStyle(.white.opacity(0.5))
            }
            Spacer()
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
        .sheet(isPresented: $showMusicSheet) { musicSheet }
        .alert("Text card", isPresented: $showTextCardAlert) {
            TextField("Card text", text: $editDraft)
            Button("Add") { addTextCard(editDraft) }
            Button("Cancel", role: .cancel) {}
        }
        .alert("Edit caption", isPresented: Binding(get: { editingCaptionFrame != nil },
                                                    set: { if !$0 { editingCaptionFrame = nil } })) {
            TextField("Word", text: $editDraft)
            Button("Save") { commitCaptionEdit() }
            Button("Cancel", role: .cancel) { editingCaptionFrame = nil }
        }
    }

    private var visibleModes: [Mode] {
        Mode.allCases.filter { $0 != .effects || (caps?["punch_ins"] ?? false) || (caps?["broll"] ?? false) || (caps?["text_cards"] ?? false) }
    }
    private func iconFor(_ m: Mode) -> String {
        switch m { case .edit: "scissors"; case .sound: "music.note"; case .text: "textformat"; case .effects: "sparkles" }
    }

    private func openModeDrawer(_ m: Mode) {
        switch m {
        case .sound where session?.draft.music == nil: showMusicSheet = true
        default: break
        }
    }
}
