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
    enum Mode: String, CaseIterable { case edit = "Edit", sound = "Sound", text = "Text", effects = "Effects", filters = "Filters" }

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
    @State var showStickerInput = false
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
    // FP1: transition boundary selection (source segIdx of the leading clip).
    @State var selectedBoundary: Int? = nil
    // FP1: speed panel target + slider draft (one op per gesture, UX-4 pattern).
    @State var speedPanelSeg: Int? = nil
    @State private var speedDraft: Double = 1.0
    // FP1: adjust-knob slider drafts (commit on release).
    @State private var adjustDraft: Double = 0
    // FP1: canvas gestures — live drafts for caption drag/pinch + sticker drag/pinch.
    @State var capDragY: Double? = nil
    @State var capPinch: Double? = nil
    @State var stickerDrag: (idx: Int, x: Double, y: Double)? = nil
    @State var stickerPinch: (idx: Int, scale: Double)? = nil
    // FP1b: the VIDEO itself is canvas-interactable — tap selects the clip under the
    // playhead, drag repositions it, pinch zooms it (CapCut preview transform).
    @State var videoDrag: (seg: Int, x: Double, y: Double)? = nil
    @State var videoPinch: (seg: Int, scale: Double)? = nil

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
        .marqueInput($showStickerInput, title: "Add text", placeholder: "Say something",
                     text: $editDraft, confirm: "Add") { addTextSticker(editDraft) }
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
                    drawerButton("Add text", "plus.square") { editDraft = ""; showStickerInput = true }
                        .accessibilityIdentifier("editorPro.addSticker")
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
                                .accessibilityIdentifier("editorPro.capStyle.\(st)")
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
                case .filters:
                    // Whole-video looks (CapCut Filters): preset chips; Adjust knobs sit
                    // in the second row below.
                    let active = session?.draft.look.filter
                    drawerButton("None", "circle.slash", active: active == nil) { setFilter(nil) }
                        .accessibilityIdentifier("editorPro.filter.none")
                    ForEach([("Vivid", "vivid"), ("Film", "film"), ("Mono", "mono"),
                             ("Golden", "golden"), ("Warm", "warm"), ("Cool", "cool")], id: \.1) { label, v in
                        drawerButton(label, "camera.filters", active: active == v) { setFilter(v) }
                            .accessibilityIdentifier("editorPro.filter.\(v)")
                    }
                }
            }.padding(.horizontal, Space.md)
        }
        .frame(height: 52).background(Palette.ink.opacity(0.25))
        // The caption customizer (research round: preset + accent/size/position/case/
        // grouping/font are the knobs creators actually touch) — its own row so the
        // presets row stays scannable.
        if mode == .text, captionsOn {
            captionOptionsRow
        }
        // CapCut Speed → Normal: slider + chips, committed as ONE op on release (UX-4).
        if let seg = speedPanelSeg {
            speedRow(seg)
        }
        // CapCut Adjust: manual color knobs under the filter presets.
        if mode == .filters {
            adjustRow
        }
    }

    private func speedRow(_ seg: Int) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: Space.md) {
            Text("SPEED").font(AppFont.micro).tracking(Track.label).foregroundStyle(.white.opacity(0.5))
            Slider(value: $speedDraft, in: 0.5...3.0, onEditingChanged: { editing in
                if !editing { setSpeed(seg, speedDraft) }
            })
            .frame(maxWidth: 150).tint(Palette.accent)
            .accessibilityIdentifier("editorPro.speedSlider")
            Text(String(format: "%.1fx", speedDraft))
                .font(.system(size: 12, weight: .bold)).monospacedDigit().foregroundStyle(.white)
                .frame(width: 38)
            ForEach([0.5, 1.0, 1.5, 2.0], id: \.self) { v in
                let active = abs((session?.draft.segments[safe: seg]?.speed ?? 1.0) - v) < 0.01
                Button { speedDraft = v; setSpeed(seg, v); bumpHaptic() } label: {
                    Text(v.truncatingRemainder(dividingBy: 1) == 0 ? String(format: "%.0fx", v) : String(format: "%.1fx", v))
                        .font(.system(size: 11, weight: active ? .bold : .medium))
                        .foregroundStyle(active ? Palette.ink : .white)
                        .padding(.horizontal, 9).frame(height: 28)
                        .background(active ? Palette.onInk : Color.white.opacity(0.12))
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("editorPro.speed.\(v)")
            }
        }
        .padding(.horizontal, Space.md)
        }
        .frame(height: 44).frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.ink.opacity(0.25))
        .accessibilityIdentifier("editorPro.speedRow")
    }

    private var adjustRow: some View {
        let a = session?.draft.look.adjust ?? EditorAdjust()
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.md) {
                adjustKnob("Bright", value: a.brightness, range: -0.5...0.5) { setAdjust(brightness: $0) }
                adjustKnob("Contrast", value: a.contrast, range: -0.5...0.5) { setAdjust(contrast: $0) }
                adjustKnob("Color", value: a.saturation, range: -0.5...0.5) { setAdjust(saturation: $0) }
                adjustKnob("Warmth", value: a.temperature, range: -0.5...0.5) { setAdjust(temperature: $0) }
                adjustKnob("Vignette", value: a.vignette, range: 0...1) { setAdjust(vignette: $0) }
            }
            .padding(.horizontal, Space.md)
        }
        .frame(height: 52)
        .background(Palette.ink.opacity(0.25))
        .accessibilityIdentifier("editorPro.adjustRow")
    }

    /// One labeled mini-slider; the op commits on release (UX-4). Local @State per
    /// knob would fight SwiftUI identity in the scroll row, so a tiny wrapper view.
    private func adjustKnob(_ label: String, value: Double, range: ClosedRange<Double>,
                            commit: @escaping (Double) -> Void) -> some View {
        AdjustKnob(label: label, initial: value, range: range, commit: commit)
    }

    private var captionOptionsRow: some View {
        let o = session?.draft.captionOptions ?? EditorCaptionOptions()
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.sm) {
                // Accent swatches — one accent per video (slash = the style's default color).
                colorSwatch(nil, active: o.accent == nil)
                ForEach(["#FFD60A", "#34D399", "#F472B6", "#60A5FA"], id: \.self) { hex in
                    colorSwatch(hex, active: o.accent == hex)
                }
                optDivider
                // Size + position moved ONTO the canvas (TikTok model): drag the caption
                // to place it, pinch to resize. The hint earns its row space once.
                Text("Drag caption to move · pinch to resize")
                    .font(.system(size: 10)).foregroundStyle(.white.opacity(0.5))
                    .accessibilityIdentifier("editorPro.capHint")
                optDivider
                optChip("AA", active: o.uppercase) { mutate([.captionOptions(uppercase: !o.uppercase)]) }
                    .accessibilityIdentifier("editorPro.capCase")
                optDivider
                // Grouping (word-by-word / ~3-word phrases / running line)
                ForEach([("Word", "word"), ("Phrase", "phrase"), ("Line", "line")], id: \.1) { label, v in
                    optChip(label, active: o.grouping == v) { mutate([.captionOptions(grouping: v)]) }
                        .accessibilityIdentifier("editorPro.capGroup.\(v)")
                }
                optDivider
                // Fonts (curated: clean sans / heavy impact / rounded)
                ForEach([("Inter", "inter"), ("Impact", "archivo"), ("Round", "baloo")], id: \.1) { label, v in
                    optChip(label, active: o.font == v) { mutate([.captionOptions(font: v)]) }
                        .accessibilityIdentifier("editorPro.capFont.\(v)")
                }
            }
            .padding(.horizontal, Space.md)
        }
        .frame(height: 44)
        .background(Palette.ink.opacity(0.25))
        .accessibilityIdentifier("editorPro.captionOptions")
    }

    private var optDivider: some View {
        Rectangle().fill(Color.white.opacity(0.15)).frame(width: 1, height: 20)
    }

    private func optChip(_ label: String, active: Bool, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label).font(.system(size: 11, weight: active ? .bold : .medium))
                .foregroundStyle(active ? Palette.ink : .white)
                .padding(.horizontal, 10).frame(height: 28)
                .background(active ? Palette.onInk : Color.white.opacity(0.12))
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }

    private func colorSwatch(_ hex: String?, active: Bool) -> some View {
        Button {
            mutate([.captionOptions(accent: hex ?? "default")])
        } label: {
            ZStack {
                Circle().fill(hex.map { colorFromHex($0) } ?? Color.white.opacity(0.15))
                    .frame(width: 24, height: 24)
                if hex == nil {
                    Image(systemName: "slash.circle").font(.system(size: 12))
                        .foregroundStyle(.white.opacity(0.7))
                }
            }
            .overlay(Circle().strokeBorder(active ? Palette.accent : Color.white.opacity(0.25),
                                           lineWidth: active ? 2 : 1))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("editorPro.capAccent.\(hex ?? "default")")
    }

    func colorFromHex(_ hex: String) -> Color {
        var v: UInt64 = 0
        Scanner(string: String(hex.dropFirst())).scanHexInt64(&v)
        return Color(hex: UInt(v))
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
            GeometryReader { canvasGeo in
            ZStack {
                Group {
                    if let player, !player.placeholder {
                        PlayerLayerView(player: player.player)
                    } else {
                        // Placeholder mode (keyless mock clip has no source video) — still fully editable.
                        Rectangle().fill(Palette.ink.opacity(0.85))
                            .overlay(Image(systemName: "film").font(.system(size: 40)).foregroundStyle(.white.opacity(0.3)))
                    }
                }
                // Canvas transform preview of the clip under the playhead (CapCut model):
                // drag repositions, pinch zooms; the render applies the same math per clip.
                .scaleEffect(liveVideoScale)
                .offset(x: liveVideoOffX * canvasGeo.size.width,
                        y: liveVideoOffY * canvasGeo.size.height)
                // L1 look preview — SwiftUI approximations of the render's CSS filter chain.
                .saturation(lookSaturation)
                .contrast(lookContrast)
                .brightness(lookBrightness)
                .hueRotation(.degrees(lookHueDegrees))
                .overlay {
                    if let v = session?.draft.look.adjust.vignette, v > 0 {
                        RadialGradient(colors: [.clear, .black.opacity(0.55 * v)],
                                       center: .center, startRadius: 90, endRadius: 320)
                            .allowsHitTesting(false)
                    }
                }
                // Selection affordance on the CANVAS when the playhead clip is selected —
                // the dashed frame invites drag/pinch (TikTok/CapCut selection box).
                .overlay {
                    if let sel = selectedSeg, sel == clipUnderPlayhead {
                        RoundedRectangle(cornerRadius: 4)
                            .strokeBorder(videoDrag != nil || videoPinch != nil ? Palette.accent : Color.white.opacity(0.55),
                                          style: StrokeStyle(lineWidth: 1.5, dash: [6, 4]))
                            .padding(2)
                            .allowsHitTesting(false)
                            .accessibilityIdentifier("editorPro.videoSelection")
                    }
                }
                captionSimOverlay
                textCardSimOverlay
                stickerSimOverlay
            }
            .scaleEffect(currentPunchScale)
            .animation(.easeInOut(duration: 0.25), value: currentPunchScale)
            // Video canvas gestures: LOW priority — stickers/captions grab theirs first.
            .gesture(videoCanvasDrag(canvasGeo.size))
            .simultaneousGesture(videoCanvasPinch())
            }
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
        // CapCut convention: tapping the canvas SELECTS the clip under the playhead
        // (the selection box + drag/pinch appear); play stays on the play button.
        .onTapGesture { canvasTapSelect() }
        .clipped()
    }

    /// The source segment index whose footage is under the playhead right now.
    var clipUnderPlayhead: Int? {
        guard let d = session?.draft else { return nil }
        let f = playheadSourceFrame
        let order = d.segmentOrder ?? Array(d.segments.indices)
        return order.first { i in d.segments[safe: i].map { f >= $0.srcIn && f < $0.srcOut } ?? false }
    }

    private func canvasTapSelect() {
        player?.pause()
        guard let idx = clipUnderPlayhead else { return }
        withAnimation(.easeOut(duration: 0.15)) {
            selectedOverlay = nil; selectedBoundary = nil
            selectedSeg = (selectedSeg == idx) ? nil : idx
        }
        bumpHaptic()
    }

    private var liveVideoScale: Double {
        guard let idx = clipUnderPlayhead, let seg = session?.draft.segments[safe: idx] else { return 1 }
        if let p = videoPinch, p.seg == idx { return p.scale }
        return seg.txScale
    }
    private var liveVideoOffX: Double {
        guard let idx = clipUnderPlayhead, let seg = session?.draft.segments[safe: idx] else { return 0 }
        if let dr = videoDrag, dr.seg == idx { return dr.x }
        return seg.txX
    }
    private var liveVideoOffY: Double {
        guard let idx = clipUnderPlayhead, let seg = session?.draft.segments[safe: idx] else { return 0 }
        if let dr = videoDrag, dr.seg == idx { return dr.y }
        return seg.txY
    }

    /// Drag the selected clip around the canvas → one set_segment_transform op.
    private func videoCanvasDrag(_ size: CGSize) -> some Gesture {
        DragGesture(minimumDistance: 4)
            .onChanged { g in
                guard let idx = clipUnderPlayhead, selectedSeg == idx,
                      let seg = session?.draft.segments[safe: idx] else { return }
                videoDrag = (idx,
                             min(0.5, max(-0.5, seg.txX + g.translation.width / max(1, size.width))),
                             min(0.5, max(-0.5, seg.txY + g.translation.height / max(1, size.height))))
            }
            .onEnded { _ in
                if let d = videoDrag { commitVideoTransform(d.seg, offX: d.x, offY: d.y); bumpHaptic() }
                videoDrag = nil
            }
    }

    /// Pinch the selected clip to zoom it → one set_segment_transform op.
    private func videoCanvasPinch() -> some Gesture {
        MagnificationGesture()
            .onChanged { v in
                guard let idx = clipUnderPlayhead, selectedSeg == idx,
                      let seg = session?.draft.segments[safe: idx] else { return }
                videoPinch = (idx, min(3.0, max(0.5, seg.txScale * v)))
            }
            .onEnded { _ in
                if let p = videoPinch { commitVideoTransform(p.seg, scale: p.scale); bumpHaptic() }
                videoPinch = nil
            }
    }

    private var timeReadout: String {
        let cur = player?.currentOutputTime ?? 0, tot = player?.totalOutputTime ?? 0
        func fmt(_ s: Double) -> String { String(format: "%d:%02d", Int(s) / 60, Int(s) % 60) }
        return "\(fmt(cur)) / \(fmt(tot))"
    }

    // MARK: FP1 — look preview approximation (maps the render's CSS chain to SwiftUI)

    private var lookVals: (sat: Double, con: Double, bri: Double, hue: Double) {
        guard let look = session?.draft.look else { return (1, 1, 0, 0) }
        var sat = 1.0, con = 1.0, bri = 0.0, hue = 0.0
        let t = min(1, max(0, look.intensity))
        func lerp(_ a: Double, _ b: Double) -> Double { a + (b - a) * t }
        switch look.filter {
        case "vivid": sat = lerp(1, 1.35); con = lerp(1, 1.08)
        case "film": con = lerp(1, 1.12); sat = lerp(1, 0.85)
        case "mono": sat = lerp(1, 0.0); con = lerp(1, 1.05)
        case "golden": sat = lerp(1, 1.2); bri = lerp(0, 0.05); hue = lerp(0, -6)
        case "warm": sat = lerp(1, 1.1); hue = lerp(0, -8)
        case "cool": sat = lerp(1, 1.05); hue = lerp(0, 10); bri = lerp(0, 0.02)
        default: break
        }
        let a = look.adjust
        bri += a.brightness * 0.6
        con *= (1 + a.contrast)
        sat *= (1 + a.saturation)
        hue += a.temperature < 0 ? -a.temperature * 20 : -a.temperature * 14
        return (sat, con, bri, hue)
    }
    private var lookSaturation: Double { lookVals.sat }
    private var lookContrast: Double { lookVals.con }
    private var lookBrightness: Double { lookVals.bri }
    private var lookHueDegrees: Double { lookVals.hue }

    // MARK: FP1 — text stickers on the canvas (drag anywhere / pinch / tap-select)

    @ViewBuilder private var stickerSimOverlay: some View {
        if let d = session?.draft {
            GeometryReader { geo in
                let f = playheadSourceFrame
                ForEach(Array(d.overlays.enumerated()), id: \.offset) { idx, o in
                    if o.type == "text_sticker", o.srcIn <= f, f < o.srcOut {
                        stickerView(idx: idx, o: o, geo: geo.size)
                    }
                }
            }
        }
    }

    private func stickerView(idx: Int, o: EditorOverlay, geo: CGSize) -> some View {
        let selected = selectedOverlay == idx
        let liveX = stickerDrag?.idx == idx ? stickerDrag!.x : o.posX
        let liveY = stickerDrag?.idx == idx ? stickerDrag!.y : o.posY
        let liveScale = stickerPinch?.idx == idx ? stickerPinch!.scale : o.scale
        return Text(o.text)
            .font(simCaptionFont(o.font, size: 24 * liveScale, heavy: true))
            .foregroundStyle(o.color.map { colorFromHex($0) } ?? .white)
            .multilineTextAlignment(.center)
            .padding(.horizontal, o.bg == "box" ? 10 : 2).padding(.vertical, o.bg == "box" ? 5 : 2)
            .background(o.bg == "box" ? Color.black.opacity(0.65) : .clear)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(selected ? Palette.accent : .clear,
                              style: StrokeStyle(lineWidth: 1.5, dash: [4, 3])))
            .shadow(radius: o.bg == "box" ? 0 : 3)
            .rotationEffect(.degrees(o.rotation))
            .position(x: liveX * geo.width, y: liveY * geo.height)
            .highPriorityGesture(
                DragGesture(minimumDistance: 2)
                    .onChanged { g in
                        stickerDrag = (idx,
                                       min(0.95, max(0.05, o.posX + g.translation.width / max(1, geo.width))),
                                       min(0.92, max(0.08, o.posY + g.translation.height / max(1, geo.height))))
                        if selectedOverlay != idx { selectedOverlay = idx; selectedSeg = nil }
                    }
                    .onEnded { _ in
                        if let s = stickerDrag, s.idx == idx { commitStickerMove(idx, x: s.x, y: s.y) }
                        stickerDrag = nil
                    })
            .simultaneousGesture(
                MagnificationGesture()
                    .onChanged { v in stickerPinch = (idx, min(3.0, max(0.4, o.scale * v))) }
                    .onEnded { _ in
                        if let p = stickerPinch, p.idx == idx { commitStickerScale(idx, scale: p.scale) }
                        stickerPinch = nil
                    })
            .onTapGesture {
                withAnimation(.easeOut(duration: 0.15)) {
                    selectedSeg = nil
                    selectedOverlay = (selectedOverlay == idx) ? nil : idx
                }
            }
            .accessibilityIdentifier("editorPro.sticker.\(idx)")
    }

    // MARK: caption + punch-in local sim (L1 fidelity)

    /// Discrete position → the canvas Y fraction it anchors at (mirrors the render).
    private func discreteCaptionY(_ position: String) -> Double {
        switch position { case "top": return 0.18; case "middle": return 0.50; default: return 0.80 }
    }

    /// The caption block on the canvas — DIRECTLY MANIPULABLE (TikTok model): drag
    /// vertically to place it (snaps to top/middle/bottom anchors), pinch to resize.
    /// One gesture = one committed op = one undo step.
    @ViewBuilder private var captionSimOverlay: some View {
        if let d = session?.draft, !d.captions.isEmpty, let group = currentCaptionGroup(d) {
            GeometryReader { geo in
                let o = d.captionOptions
                let discreteMult = o.size == "small" ? 0.78 : o.size == "large" ? 1.24 : 1.0
                let effScale = capPinch ?? o.scale ?? discreteMult
                let base: CGFloat = d.captionStyle == "bold-word" ? 30 : 17
                let effY = capDragY ?? o.posY ?? discreteCaptionY(o.position)
                let interacting = capDragY != nil || capPinch != nil
                HStack(spacing: 5) {
                    ForEach(Array(group.words.enumerated()), id: \.offset) { i, w in
                        Text(o.uppercase || d.captionStyle == "bold-word" ? w.uppercased() : w)
                            .font(simCaptionFont(o.font, size: base * effScale,
                                                 heavy: d.captionStyle == "bold-word"))
                            .foregroundStyle(simCaptionColor(d, isHot: i == group.activeInGroup,
                                                             spoken: i <= group.activeInGroup))
                            .opacity(d.captionStyle == "clean" && i != group.activeInGroup ? 0.55 : 1)
                            .shadow(radius: 4)
                    }
                }
                .padding(.horizontal, 10).padding(.vertical, 4)
                .accessibilityIdentifier("editorPro.captionSim")
                // Selection affordance: dashed bounds in Text mode invite the drag;
                // accent while a gesture is live (TikTok's selection box).
                .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(interacting ? Palette.accent
                                              : (mode == .text ? Color.white.opacity(0.35) : .clear),
                                  style: StrokeStyle(lineWidth: 1.5, dash: [4, 3])))
                .position(x: geo.size.width / 2, y: effY * geo.size.height)
                .highPriorityGesture(
                    DragGesture(minimumDistance: 3)
                        .onChanged { g in
                            let start = o.posY ?? discreteCaptionY(o.position)
                            var y = start + g.translation.height / max(1, geo.size.height)
                            // Snap to the three anchors (Edits' guide-line behavior).
                            for anchor in [0.18, 0.50, 0.80] where abs(y - anchor) < 0.025 { y = anchor }
                            capDragY = min(0.85, max(0.15, y))
                        }
                        .onEnded { _ in
                            if let y = capDragY { commitCaptionPosY(y); bumpHaptic() }
                            capDragY = nil
                        })
                .simultaneousGesture(
                    MagnificationGesture()
                        .onChanged { v in
                            let start = o.scale ?? discreteMult
                            capPinch = min(2.0, max(0.5, start * v))
                        }
                        .onEnded { _ in
                            if let s = capPinch { commitCaptionScale(s); bumpHaptic() }
                            capPinch = nil
                        })
                // Guide line while snapped to an anchor (yellow safe-zone line, Edits-style)
                .overlay {
                    if let y = capDragY, [0.18, 0.50, 0.80].contains(y) {
                        Rectangle().fill(Color(hex: 0xFFD60A).opacity(0.8))
                            .frame(height: 1)
                            .position(x: geo.size.width / 2, y: y * geo.size.height)
                            .allowsHitTesting(false)
                    }
                }
            }
        }
    }

    /// L1 approximations of the three render fonts (Inter / Archivo Black / Baloo 2).
    private func simCaptionFont(_ font: String, size: CGFloat, heavy: Bool) -> Font {
        switch font {
        case "archivo": return .system(size: size, weight: .black)
        case "baloo": return .system(size: size, weight: heavy ? .heavy : .bold, design: .rounded)
        default: return .system(size: size, weight: heavy ? .heavy : .semibold)
        }
    }

    private func simCaptionColor(_ d: EditorDocument, isHot: Bool, spoken: Bool) -> Color {
        let accent = d.captionOptions.accent.map { colorFromHex($0) }
        switch d.captionStyle {
        case "karaoke": return spoken ? (accent ?? Color(hex: 0xFFD60A)) : .white
        case "bold-word": return accent ?? .white
        default: return isHot ? (accent ?? .white) : .white
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

    /// The words visible at the playhead under the draft's grouping mode, plus which of
    /// them is the active one. nil during silences (UX-3) or before the first word.
    private func currentCaptionGroup(_ d: EditorDocument) -> (words: [String], activeInGroup: Int)? {
        let srcFrame = secondsToFrame(d.sourceSeconds(forOutput: player?.currentOutputTime ?? 0))
        guard let activeIdx = d.captions.lastIndex(where: { $0.frame <= srcFrame }) else { return nil }
        let cap = d.captions[activeIdx]
        // UX-3: bound the display window with the transcript so the last word doesn't
        // burn on screen through every silence.
        if let span = words.first(where: { $0.startFrame == cap.frame }) ?? words.last(where: { $0.startFrame <= cap.frame }),
           srcFrame > span.endFrame + 15 {
            return nil
        }
        if d.captionStyle == "bold-word" { return ([cap.word], 0) }
        let lo: Int, hi: Int
        switch d.captionOptions.grouping {
        case "word": lo = activeIdx; hi = activeIdx
        case "phrase":
            lo = (activeIdx / 3) * 3
            hi = min(d.captions.count - 1, lo + 2)
        default:
            lo = max(0, activeIdx - 2)
            hi = min(d.captions.count - 1, activeIdx + 2)
        }
        return (d.captions[lo...hi].map(\.word), activeIdx - lo)
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
            },
            selectedBoundary: selectedBoundary,
            onTapBoundary: { leading in
                player?.pause()
                withAnimation(.easeOut(duration: 0.15)) {
                    selectedSeg = nil; selectedOverlay = nil; speedPanelSeg = nil
                    selectedBoundary = (selectedBoundary == leading) ? nil : leading
                }
                bumpHaptic()
            }
        )
        .frame(height: timelineHeight)
        .background(Palette.ink.opacity(0.6))
    }

    // MARK: context strip (selection actions)

    @ViewBuilder private var contextStrip: some View {
        if let b = selectedBoundary {
            // Boundary selected: the transition styles (CapCut's between-clips picker).
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Space.sm) {
                    Text("TRANSITION").font(AppFont.micro).tracking(Track.label)
                        .foregroundStyle(.white.opacity(0.5))
                    ForEach([("None", "none"), ("Fade", "fade_black"), ("White", "fade_white"), ("Flash", "flash")], id: \.1) { label, v in
                        let active = (session?.draft.transitions.first { $0.afterSegment == b }?.style ?? "none") == v
                        Button { setTransition(after: b, style: v); bumpHaptic() } label: {
                            Text(label).font(.system(size: 11, weight: active ? .bold : .medium))
                                .foregroundStyle(active ? Palette.ink : .white)
                                .padding(.horizontal, 12).frame(height: 30)
                                .background(active ? Palette.onInk : Color.white.opacity(0.12))
                                .clipShape(Capsule())
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("editorPro.ctx.transition.\(v)")
                    }
                }
                .padding(.horizontal, Space.md)
            }
            .frame(height: 44)
            .background(Palette.ink.opacity(0.4))
        } else if let ov = selectedOverlay, let overlay = session?.draft.overlays[safe: ov] {
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
                        Text(overlay.type == "text_sticker" ? "Text" : "Text card")
                            .font(AppFont.caption).foregroundStyle(.white.opacity(0.45))
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
            contextButton("Speed", "gauge.with.needle") {
                if let s = seg {
                    speedDraft = session?.draft.segments[safe: s]?.speed ?? 1.0
                    withAnimation(.easeOut(duration: 0.15)) { speedPanelSeg = (speedPanelSeg == s) ? nil : s }
                    bumpHaptic()
                }
            }
            .disabled(seg == nil).opacity(seg == nil ? 0.35 : 1)
            .accessibilityIdentifier("editorPro.ctx.speed")
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
        switch m { case .edit: "scissors"; case .sound: "music.note"; case .text: "textformat"; case .effects: "sparkles"; case .filters: "camera.filters" }
    }

    private func openModeDrawer(_ m: Mode) {
        // Sound mode no longer auto-pops the music sheet — the timeline's "+ Add sound"
        // lane and the drawer button advertise it (empty lanes advertise themselves).
    }
}

// One labeled Adjust mini-slider (CapCut Adjust knobs). Owns its drag draft so the
// row doesn't re-init mid-gesture; commits ONE op on release (UX-4 invariant).
private struct AdjustKnob: View {
    let label: String
    let initial: Double
    let range: ClosedRange<Double>
    let commit: (Double) -> Void
    @State private var value: Double = 0
    @State private var seeded = false

    var body: some View {
        VStack(spacing: 2) {
            Slider(value: $value, in: range, onEditingChanged: { editing in
                if !editing { commit(value) }
            })
            .frame(width: 104).tint(Palette.accent)
            Text("\(label) \(value == 0 ? "" : String(format: "%+.0f", value * 100))")
                .font(.system(size: 9)).foregroundStyle(.white.opacity(0.65))
        }
        .onAppear { if !seeded { value = initial; seeded = true } }
        .accessibilityIdentifier("editorPro.adjust.\(label.lowercased())")
    }
}
