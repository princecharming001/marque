import SwiftUI
import AVFoundation
import PhotosUI

// MARK: - ProEditorView — the CapCut/TikTok-style direct-manipulation editor.
// Loads the clip's server EDL + transcript, edits a local draft (instant preview via
// EditorSession + EditorPlayerController), and on Save flushes the sequential op log to
// the backend for the real re-render. Reached from LibraryView → "Edit manually".

struct ProEditorView: View {
    @Environment(AppStore.self) var store
    @Environment(\.dismiss) var dismiss
    let clip: Clip

    enum Phase: Equatable { case loading, editing, applying, rendering, failed(String) }
    // One-bar model: the root panels a root tile can open (replaces the old 5-tab Mode bar).
    enum RootPanel: Equatable { case sound, text, captions, effects, filters }
    // A sub-tool expansion REPLACES the toolbar row while open (CapCut's volume/adjust sheets;
    // also the small-screen budget — stacking above the bar starved the SE's preview).
    // Exactly one open at a time; any selection/rootPanel change closes it.
    enum Expansion: Equatable {
        case speed(seg: Int)
        case clipVolume(seg: Int)
        case musicVolume
        case captionStyle
        case captionCustomize
        case transitionDuration(boundary: Int)
    }

    @State var phase: Phase = .loading
    @State var session: EditorSession?
    @State var player: EditorPlayerController?
    @State var filmstrip: FilmstripCache?
    @State var words: [WordSpan] = []
    @State var caps: [String: Bool]? = nil
    @State var rootPanel: RootPanel? = nil      // nil = plain root vocabulary
    @State var expansion: Expansion? = nil
    @State var selectedSeg: Int? = nil          // index into segments (source index)
    @State var selectedOverlay: Int? = nil      // index into draft.overlays (chip lane)
    @State var selectedMusic = false            // INVARIANT: true only while draft.music != nil
    @State var selectedPhraseID: Int? = nil     // CaptionPhrase.id (= startFrame) — never an
                                                // array index; phrases recomputes per draft change
    @State var editingOverlayIndex: Int? = nil  // text-card text edit in flight
    @State var captionsOn = false               // #1: enabled-state tracked in the view (local
                                                // captions may be empty while enabled → preview from words)
    @State var pointsPerSecond: CGFloat = 18
    @State var applyTask: Task<Void, Never>?
    @State var renderStartedAt: Date?
    @State var transient: String?
    @State var editorRecoverable = false        // gone job + local footage → offer re-create
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
    // FP1: transition boundary selection (source segIdx of the leading clip).
    @State var selectedBoundary: Int? = nil
    // Expansion slider drafts (one op per gesture, UX-4 pattern).
    @State private var speedDraft: Double = 1.0
    @State private var transDurDraft: Double = 0.4
    // A7 feature #1: theme picker + report card (#8) — the active bundle + whatever
    // the pipeline computed (self_review/lint), surfaced read-only.
    @State var themes: [ThemeChoice] = []
    @State var activeThemeId: String = ""
    @State var showThemeSheet = false
    @State var rethemeTask: Task<Void, Never>?
    // Declutter disclosures: caption fine-tune knobs + manual Adjust knobs hide by default.
    @State var showCaptionCustomize = false
    @State var showFilterAdvanced = false
    // FP1: canvas gestures — live drafts for caption drag/pinch + sticker drag/pinch.
    @State var capDragY: Double? = nil
    @State var capPinch: Double? = nil
    @State var stickerDrag: (idx: Int, x: Double, y: Double)? = nil
    @State var stickerPinch: (idx: Int, scale: Double)? = nil
    // FP1b: the VIDEO itself is canvas-interactable — tap selects the clip under the
    // playhead, drag repositions it, pinch zooms it (CapCut preview transform).
    @State var videoDrag: (seg: Int, x: Double, y: Double)? = nil
    @State var videoPinch: (seg: Int, scale: Double)? = nil
    // FP1c: media rolls — selection, the add-media panel, photo/video import.
    @State var selectedBroll: Int? = nil
    @State var showMediaPanel = false
    @State var showStockInput = false
    @State var mediaPickerItem: PhotosPickerItem? = nil
    @State var uploadingMedia = false
    @State var replacingRoll: Int? = nil          // roll index being swapped via the media panel
    // R10: filler/pause cleanup — the transcript-driven bulk-cut panel.
    @State var showCleanup = false
    @State var cleanupSkip: Set<Int> = []         // target indices the user unchecked
    // url → local file path, so the player sim can show freshly-imported media
    // before the server round-trip.
    @State var localMediaPreviews: [String: String] = [:]
    // R10: CapCut transport row + reclaimed layout.
    @State var showFullscreen = false            // fullScreenCover preview (transport ⛶)
    @State var toast: String? = nil              // named-undo / completion toast text
    @State private var toastTick = 0             // drives auto-dismiss
    // R10: filter intensity (client-side preview blend; also written to the look op).
    @State var filterIntensityDraft: Double = 1.0
    @State private var filterPreviewImage: UIImage? = nil   // representative frame for filter cards
    // R10: keyboard-first text — the sticker index being live-typed (bound TextField).
    @State var typingSticker: Int? = nil
    @FocusState var stickerFieldFocused: Bool

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
        .sheet(isPresented: $showThemeSheet) { themeSheet }
        .marqueInput($showTextCardAlert, title: "Text card", placeholder: "Card text",
                     text: $editDraft, confirm: "Add") { addTextCard(editDraft) }
        .marqueInput($showStockInput, title: "Stock clip", placeholder: "What should it show?",
                     text: $editDraft, confirm: "Add") { addStockRoll(editDraft) }
        .marqueInput(Binding(get: { editingPhrase != nil }, set: { if !$0 { editingPhrase = nil } }),
                     title: "Edit caption", placeholder: "Caption text", text: $editDraft) { commitPhraseEdit() }
        .marqueInput(Binding(get: { editingOverlayIndex != nil }, set: { if !$0 { editingOverlayIndex = nil } }),
                     title: "Edit text card", placeholder: "Text", text: $editDraft) { commitOverlayTextEdit() }
        .sensoryFeedback(.impact(weight: .light), trigger: hapticTick)   // I-7 haptics
        .fullScreenCover(isPresented: $showFullscreen) { fullscreenPreview }
        .onChange(of: mediaPickerItem) { _, item in
            if let item { Task { await importRollMedia(item) } }
        }
        .task { await load() }
        .onChange(of: phase) { _, p in
            if p == .editing, !coachShown { showCoach = true }
        }
        .onChange(of: rootPanel) { _, p in
            // The filter cards' representative frame loads lazily on first entry to Filters —
            // this is its ONLY trigger (without it the cards show the placeholder gradient).
            if p == .filters, filterPreviewImage == nil { Task { await loadFilterPreview() } }
            // Collapse the declutter disclosures when leaving their panel so each opens fresh.
            if p != .captions { showCaptionCustomize = false }
            if p != .filters { showFilterAdvanced = false }
        }
        // R10: keyboard-first text commits when the on-canvas field loses focus.
        .onChange(of: stickerFieldFocused) { _, focused in
            if !focused, let idx = typingSticker { commitTyping(idx) }
        }
        .onDisappear {
            // #47: do NOT cancel a save that's already committing/rendering. Once Save
            // fires, applyTask owns the server commit + render poll and writes the result
            // back to the STORE (which outlives this view) via applyTweakResult — so the
            // Library reflects the finished edit even after the editor closes. Cancelling
            // here abandoned a render already committing server-side, stranding the Library
            // on the pre-edit clip. Post-teardown writes to this view's @State are no-ops.
            // Only a save still mid-flight is worth keeping; anything else has nothing to run.
            switch phase {
            case .applying, .rendering: break                 // let the commit + poll finish
            default: applyTask?.cancel()
            }
            player?.teardown()
        }
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
                    coachRow("textformat", "Tap a caption strip, then Edit, to fix its words")
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
        // R10: undo/redo moved to the transport strip (CapCut keeps them by the play head).
        ToolbarItem(placement: .topBarTrailing) {
            if phase == .editing {
                // Label the cost up front: nearly every edit re-renders (~1 min); only a
                // pure split-only batch commits instantly (#6/#43). So a cut/overlay/caption
                // shows "Render", a bare split shows "Save".
                Button { save() } label: { Text(saveNeedsRender ? "Render" : "Save").fontWeight(.semibold) }
                    .tint(Palette.accent).disabled(!(session?.isDirty ?? false))
                    .accessibilityIdentifier("editorPro.save")
            }
        }
    }

    /// True when the save will re-render server-side. MUST mirror save()'s defer rule
    /// (#6/#43): only a pure split-only batch renders pixel-identically and is deferred;
    /// everything else — cuts, reorders, mutes, volume, captions, overlays, music, b-roll —
    /// changes the delivered video and re-renders. Kept in lockstep so the button label
    /// ("Render" vs "Save") never lies about what the tap actually costs.
    var saveNeedsRender: Bool {
        guard let ops = session?.flattenedOps(), !ops.isEmpty else { return false }
        return !ops.allSatisfy { ($0["type"] as? String) == "split_segment" }
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
            } else if showMediaPanel {
                mediaPanel
            } else if showCleanup {
                cleanupPanel
            } else {
                transportRow                       // R10: CapCut transport strip
                timelinePane
                // One-bar model: an open expansion REPLACES the toolbar in the same slot
                // (CapCut's volume/adjust sheets — and the iPhone SE height budget).
                if let e = expansion {
                    expansionRow(e)
                } else {
                    rootPanelRows
                    oneBar
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
    }

    /// R10: the CapCut/TikTok transport strip — time readout · play · undo · redo ·
    /// fullscreen. Replaces the play/time controls that used to float ON the canvas
    /// (CapCut keeps the picture clean).
    private var transportRow: some View {
        VStack(spacing: 0) {
            HStack(spacing: Space.lg) {
                Text(timeReadout).font(.system(size: 11).monospacedDigit())
                    .foregroundStyle(.white.opacity(0.85))
                    .accessibilityIdentifier("editorPro.timeReadout")
                Spacer()
                Button { player?.togglePlay() } label: {
                    Image(systemName: (player?.isPlaying ?? false) ? "pause.fill" : "play.fill")
                        .font(.system(size: 17))
                }
                .tint(.white).accessibilityIdentifier("editorPro.playPause")
                Spacer()
                Button { doUndo() } label: { Image(systemName: "arrow.uturn.backward") }
                    .tint(.white).disabled(!(session?.canUndo ?? false))
                    .opacity((session?.canUndo ?? false) ? 1 : 0.35)
                    .accessibilityIdentifier("editorPro.undo")
                Button { doRedo() } label: { Image(systemName: "arrow.uturn.forward") }
                    .tint(.white).disabled(!(session?.canRedo ?? false))
                    .opacity((session?.canRedo ?? false) ? 1 : 0.35)
                    .accessibilityIdentifier("editorPro.redo")
                Button { player?.pause(); showFullscreen = true } label: {
                    Image(systemName: "arrow.up.left.and.arrow.down.right")
                }
                .tint(.white).accessibilityIdentifier("editorPro.fullscreen")
            }
            .font(.system(size: 15))
            .padding(.horizontal, Space.md).frame(height: 32)
        }
        .background(Palette.night)
    }

    // MARK: root panels — the content rows a root tile opens (render ABOVE the one bar)

    @ViewBuilder private var rootPanelRows: some View {
        switch rootPanel {
        case .sound:
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Space.md) {
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
                }.padding(.horizontal, Space.md)
            }
            .frame(height: 52).background(Palette.ink.opacity(0.25))
        case .text:
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Space.md) {
                    drawerButton("Add text", "plus.square") { startTextEntry() }
                        .accessibilityIdentifier("editorPro.addSticker")
                    // #5: text card is only supported for some styles — gate the button so a
                    // creator doesn't type one only to be rejected.
                    if textCardsSupported {
                        drawerButton("Text card", "text.badge.plus") { editDraft = ""; showTextCardAlert = true }
                            .accessibilityIdentifier("editorPro.addTextCard")
                    }
                }.padding(.horizontal, Space.md)
            }
            .frame(height: 52).background(Palette.ink.opacity(0.25))
        case .captions:
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Space.md) {
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
                        // Declutter: the fine-tune knobs (position/accent/case/grouping/font) hide
                        // behind "Customize" so the 10-preset row stays the scannable default.
                        drawerButton("Customize", "slider.horizontal.3", active: showCaptionCustomize) {
                            withAnimation(.easeOut(duration: 0.15)) { showCaptionCustomize.toggle() }
                        }
                        .accessibilityIdentifier("editorPro.capCustomize")
                    }
                }.padding(.horizontal, Space.md)
            }
            .frame(height: 52).background(Palette.ink.opacity(0.25))
            if captionsOn {
                captionStyleRow      // 10 popular styles
                if showCaptionCustomize { captionOptionsRow }
            }
        case .effects:
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Space.md) {
                    // #8: fall back to the LOCAL style capability when the server caps didn't
                    // load (keyless/network hiccup) so Zoom doesn't silently vanish.
                    if punchInsSupported { drawerButton("Punch-in", "plus.magnifyingglass") { addPunchInOnHook() }.accessibilityIdentifier("editorPro.addPunchIn") }
                    // B-roll is universal now (Round 9) — the button opens the media panel
                    // so the creator picks stock vs their own photo/video.
                    drawerButton("Add b-roll", "photo.on.rectangle") {
                        player?.pause()
                        withAnimation(.easeOut(duration: 0.18)) { showMediaPanel = true }
                    }
                    .accessibilityIdentifier("editorPro.addBroll")
                    if !punchInsSupported && !brollSupported {
                        Text("No effects for this style").font(AppFont.caption).foregroundStyle(.white.opacity(0.5)).accessibilityIdentifier("editorPro.effects.empty")
                    }
                }.padding(.horizontal, Space.md)
            }
            .frame(height: 52).background(Palette.ink.opacity(0.25))
        case .filters:
            // Visual thumbnail cards (real frame through each look) + a tools row
            // (Theme + Advanced) + intensity when a filter is active. Manual color knobs
            // (Adjust) hide behind "Advanced" to keep the idle Filters panel to two rows.
            filterCardsRow
            filterToolsRow
            if session?.draft.look.filter != nil { filterIntensityRow }
            if showFilterAdvanced { adjustRow }
        case nil:
            EmptyView()
        }
    }

    /// Filters tools: the Theme sheet (one-tap coherent look — captions+grade+music) plus an
    /// "Advanced" toggle that reveals the manual Adjust knobs. Keeps the idle Filters tab tidy.
    private var filterToolsRow: some View {
        HStack(spacing: Space.sm) {
            if !themes.isEmpty {
                optChip("Theme", active: !activeThemeId.isEmpty) { showThemeSheet = true }
                    .accessibilityIdentifier("editorPro.themeButton")
            }
            optChip("Advanced", active: showFilterAdvanced) {
                withAnimation(.easeOut(duration: 0.15)) { showFilterAdvanced.toggle() }
            }
            .accessibilityIdentifier("editorPro.filterAdvanced")
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Space.md)
        .frame(height: 38).background(Palette.ink.opacity(0.25))
    }

    /// CapCut filter cards — each shows a representative frame with the look applied, a name
    /// footer, a leading None card, and an accent border on the active one.
    private var filterCardsRow: some View {
        let active = session?.draft.look.filter
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.sm) {
                filterCard(nil, label: "None", active: active == nil)
                ForEach([("Vivid", "vivid"), ("Film", "film"), ("Mono", "mono"),
                         ("Golden", "golden"), ("Warm", "warm"), ("Cool", "cool")], id: \.1) { label, v in
                    filterCard(v, label: label, active: active == v)
                }
            }
            .padding(.horizontal, Space.md)
        }
        .frame(height: 74).background(Palette.ink.opacity(0.25))
        .accessibilityIdentifier("editorPro.filterCards")
    }

    private func filterCard(_ filter: String?, label: String, active: Bool) -> some View {
        let p = filterParams(filter, intensity: 1.0)
        return Button { setFilter(filter); bumpHaptic() } label: {
            VStack(spacing: 3) {
                ZStack {
                    Group {
                        if let img = filterPreviewImage {
                            Image(uiImage: img).resizable().aspectRatio(contentMode: .fill)
                        } else {
                            LinearGradient(colors: [Color(hex: 0x4A5568), Color(hex: 0x8B95A5)],
                                           startPoint: .top, endPoint: .bottom)
                        }
                    }
                    .frame(width: 46, height: 42).clipped()
                    .saturation(p.sat).contrast(p.con).brightness(p.bri).hueRotation(.degrees(p.hue))
                    if filter == nil {
                        Image(systemName: "slash.circle").font(.system(size: 15))
                            .foregroundStyle(.white.opacity(0.85))
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .strokeBorder(active ? Palette.accent : Color.white.opacity(0.18), lineWidth: active ? 2 : 1))
                Text(label).font(.system(size: 9, weight: active ? .bold : .regular))
                    .foregroundStyle(active ? Palette.accent : .white.opacity(0.7))
            }
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("editorPro.filter.\(filter ?? "none")")
    }

    private var filterIntensityRow: some View {
        HStack(spacing: Space.md) {
            Text("INTENSITY").font(AppFont.micro).tracking(Track.label).foregroundStyle(.white.opacity(0.5))
            Slider(value: $filterIntensityDraft, in: 0...1, onEditingChanged: { editing in
                if editing { filterIntensityDraft = session?.draft.look.intensity ?? 1.0 }
                else { setFilterIntensity(filterIntensityDraft) }
            }).tint(Palette.accent)
            Text("\(Int(filterIntensityDraft * 100))")
                .font(.system(size: 10, weight: .semibold)).monospacedDigit().foregroundStyle(.white).frame(width: 30)
        }
        .padding(.horizontal, Space.md).frame(height: 38)
        .background(Palette.ink.opacity(0.25))
        .onAppear { filterIntensityDraft = session?.draft.look.intensity ?? 1.0 }
        .accessibilityIdentifier("editorPro.filterIntensity")
    }

    /// #8 AI report card — the self-review vision score + issues, and the
    /// deterministic lint scoreboard, both surfaced read-only exactly as the
    /// Grab a representative frame for the filter cards (middle of the first kept interval).
    private func loadFilterPreview() async {
        guard let fs = filmstrip else { return }
        let startFrame = session?.draft.keptIntervals.first?.srcIn ?? 30
        let sec = Double(startFrame + 15) / 30.0
        if let img = await fs.thumbnail(atSourceSecond: sec) { filterPreviewImage = img }
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

    // The 10 popular caption styles (Feature 2). Each chip carries a color swatch + name and
    // applies the full preset (base style + font/caps/color/outline/box) on tap.
    private var captionStyleRow: some View {
        let activeId = activeCaptionPresetId()
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.sm) {
                ForEach(CaptionPreset.all) { p in
                    Button { applyCaptionPreset(p) } label: {
                        HStack(spacing: 6) {
                            Circle().fill(p.swatch).frame(width: 12, height: 12)
                                .overlay(Circle().strokeBorder(.white.opacity(0.4), lineWidth: 0.5))
                            Text(p.label).font(.system(size: 11, weight: activeId == p.id ? .bold : .medium))
                        }
                        .foregroundStyle(activeId == p.id ? Palette.ink : .white)
                        .padding(.horizontal, 10).frame(height: 30)
                        .background(activeId == p.id ? Palette.onInk : Color.white.opacity(0.12))
                        .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("editorPro.capPreset.\(p.id)")
                }
            }
            .padding(.horizontal, Space.md)
        }
        .frame(height: 42)
        .background(Palette.ink.opacity(0.25))
        .accessibilityIdentifier("editorPro.captionStyleRow")
    }

    private var captionOptionsRow: some View {
        let o = session?.draft.captionOptions ?? EditorCaptionOptions()
        // The current effective vertical anchor (a discrete position enum, or the continuous
        // pos_y drag override) — used to light the matching position chip.
        let curY = o.posY ?? (o.position == "top" ? 0.20 : o.position == "middle" ? 0.46 : 0.74)
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.sm) {
                // POSITION — shift ALL captions at once (one track-wide pos_y). Also draggable on canvas.
                ForEach([("Top", 0.20), ("Mid", 0.46), ("Low", 0.74)], id: \.0) { label, y in
                    optChip(label, active: abs(curY - y) < 0.06) { setCaptionPosition(y) }
                        .accessibilityIdentifier("editorPro.capPos.\(label)")
                }
                optDivider
                // Accent swatches — one accent per video (slash = the style's default color).
                colorSwatch(nil, active: o.accent == nil)
                ForEach(["#FFD60A", "#34D399", "#F472B6", "#60A5FA"], id: \.self) { hex in
                    colorSwatch(hex, active: o.accent == hex)
                }
                optDivider
                // CapCut "auto-highlight keywords" — paints notable words in the accent color.
                optChip("Keywords", active: !o.highlightWords.isEmpty) { toggleKeywordHighlight() }
                    .accessibilityIdentifier("editorPro.capKeywords")
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
                // Fonts (curated: clean sans / heavy impact / rounded / A2: two more)
                ForEach([("Inter", "inter"), ("Impact", "archivo"), ("Round", "baloo"),
                        ("Bold", "montserrat"), ("Caps", "anton")], id: \.1) { label, v in
                    optChip(label, active: o.font == v) { mutate([.captionOptions(font: v)]) }
                        .accessibilityIdentifier("editorPro.capFont.\(v)")
                }
                // (The one-tap "Hormozi" chip was removed — the presets row above already has a
                // Hormozi preset (capPreset.hormozi); this fine-tune row is just the manual knobs.)
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
                // Formatting fix #1: style-aware framing backdrop (the colored/placeholder
                // zone behind the framed card for green_screen/duet_split) — EmptyView for
                // the other styles. See framingStyle/framingBackdrop below.
                framingBackdrop(canvasGeo.size)
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
                // Formatting fix #1: constrain the player into the real composition's card/
                // band for the 2 styles whose render frames the source video into a sub-
                // region (green_screen, duet_split); every other style (incl. split_three,
                // which dims/lines the SAME full-frame track — see framingChrome) passes
                // through unmodified. L1 fidelity, not pixel-perfect.
                .proEditorPlayerFraming(style: framingStyle, canvas: canvasGeo.size)
                // Dividers/dimming — drawn UNDER the caption/sticker/text-card sim overlays
                // below, matching the real compositions where Captions/TextStickers paint
                // above the video panels and are never dimmed by the panel highlighting.
                framingChrome(canvasGeo.size)
                rollSimOverlay
                captionSimOverlay
                textCardSimOverlay
                stickerSimOverlay
                // Formatting fix #11: transition dip (fade_black/fade_white/flash) — full-
                // screen, topmost, matching Grade.tsx's own paint order (above captions).
                transitionSimOverlay
            }
            .scaleEffect(currentPunchScale)
            .animation(.easeInOut(duration: 0.25), value: currentPunchScale)
            // Video canvas gestures: LOW priority — stickers/captions grab theirs first.
            .gesture(videoCanvasDrag(canvasGeo.size))
            .simultaneousGesture(videoCanvasPinch())
            }
            // R10: play/time controls moved to the transport strip (CapCut keeps the
            // picture clean); a toast surfaces mid-canvas for undo/redo/completion.
            if let toast {
                VStack {
                    Spacer()
                    Text(toast)
                        .font(.system(size: 12, weight: .medium)).foregroundStyle(.white)
                        .padding(.horizontal, 14).padding(.vertical, 8)
                        .background(.black.opacity(0.78)).clipShape(Capsule())
                        .padding(.bottom, 24)
                        .transition(.opacity)
                        .accessibilityIdentifier("editorPro.toast")
                }
                .allowsHitTesting(false)
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

    // MARK: Formatting fix #1 — style-aware composition framing (L1 fidelity)
    //
    // 3 of the 7 render compositions (render/src/compositions/{GreenScreen,DuetSplit,
    // SplitThree}.tsx) look nothing like a full-frame player; the local preview used to show
    // the same plain full-frame surface for every style regardless. This section adds a
    // rough (not pixel-perfect) approximation of each real layout so a formatting mistake is
    // visible before the server render, not just after. Every other style (talking_head,
    // faceless, fast_cuts, broll_cutaway) is completely untouched — framingStyle is nil.

    /// nil for the 4 styles that render full-frame; otherwise the draft's own style string.
    private var framingStyle: String? {
        guard let s = session?.draft.style,
              ["green_screen", "duet_split", "split_three"].contains(s) else { return nil }
        return s
    }

    /// Backdrop shown BEHIND the framed player — the colored/placeholder zone the real
    /// composition paints outside the video card. EmptyView for every other style.
    @ViewBuilder private func framingBackdrop(_ canvas: CGSize) -> some View {
        switch framingStyle {
        case "green_screen":
            // GreenScreen.tsx: AbsoluteFill background behind the reference-text zone
            // (top ~45%) and the speaker card (bottom 54%, added by proEditorPlayerFraming).
            Color(hex: 0x0F3460)
                .frame(width: canvas.width, height: canvas.height)
                .allowsHitTesting(false)
                .accessibilityIdentifier("editorPro.framing.green_screen")
        case "duet_split":
            // DuetSplit.tsx's TOP panel (the reacted-to clip) — the local sim has no react
            // source to actually show, so a dark placeholder stands in (same treatment the
            // app already uses for a sourceless player, just labeled for this band).
            let h = canvas.height * editorDuetSplitFraction
            ZStack {
                // Same "no source to show" treatment as the placeholder player below —
                // this band has no react clip to render client-side.
                Rectangle().fill(Palette.ink.opacity(0.85))
                Image(systemName: "film")
                    .font(.system(size: 32)).foregroundStyle(.white.opacity(0.3))
            }
            .frame(width: canvas.width, height: h)
            .position(x: canvas.width / 2, y: h / 2)
            .allowsHitTesting(false)
            .accessibilityIdentifier("editorPro.framing.duet_split")
        default:
            EmptyView()
        }
    }

    /// Foreground chrome (dividers / dimming). Drawn UNDER the caption/sticker/text-card sim
    /// overlays (see playerSurface) so those stay crisp — matches the real compositions,
    /// where Captions/TextStickers paint above the video panels uninvolved in the highlight.
    @ViewBuilder private func framingChrome(_ canvas: CGSize) -> some View {
        switch framingStyle {
        case "duet_split":
            // The hairline between DuetSplit.tsx's top/bottom panels.
            Rectangle().fill(Color.white.opacity(0.14))
                .frame(width: canvas.width, height: 2)
                .position(x: canvas.width / 2, y: canvas.height * editorDuetSplitFraction)
                .allowsHitTesting(false)
        case "split_three":
            splitThreeChrome(canvas)
        default:
            EmptyView()
        }
    }

    /// SplitThree.tsx: three equal horizontal panels of the SAME cut track; the "active"
    /// third is left clear while the other two dim. Approximated as chrome drawn over the
    /// single existing player (tripling the video view is a much larger lift for an L1
    /// preview) — divider lines + dimming still telegraph the real 3-panel layout.
    private func splitThreeChrome(_ canvas: CGSize) -> some View {
        let thirdH = canvas.height / 3
        let totalOut = max(1, session?.draft.totalOutputFrames ?? 1)
        let third = max(1, totalOut / 3)
        // Mirrors SplitThree.tsx exactly: Math.min(2, Math.floor(frame / third)) — evaluated
        // against the player's current OUTPUT frame, the same coordinate space the render uses.
        let active = min(2, secondsToFrame(player?.currentOutputTime ?? 0) / third)
        return ZStack {
            ForEach(0..<3, id: \.self) { i in
                if i != active {
                    Rectangle().fill(Color.black.opacity(0.45))
                        .frame(width: canvas.width, height: thirdH)
                        .position(x: canvas.width / 2, y: thirdH * (CGFloat(i) + 0.5))
                }
            }
            Rectangle().fill(Color.white.opacity(0.18)).frame(width: canvas.width, height: 2)
                .position(x: canvas.width / 2, y: thirdH)
            Rectangle().fill(Color.white.opacity(0.18)).frame(width: canvas.width, height: 2)
                .position(x: canvas.width / 2, y: thirdH * 2)
        }
        .allowsHitTesting(false)
        .accessibilityIdentifier("editorPro.framing.split_three")
    }

    // MARK: Formatting fix #11 — transition dip local sim (L1 fidelity)

    /// L1 sim of Grade.tsx's boundary transition dip: a full-screen color overlay whose
    /// opacity ramps 0→1→0 centered on the OUTPUT frame where the transition's leading
    /// segment finishes playing.
    @ViewBuilder private var transitionSimOverlay: some View {
        if let d = session?.draft, let dip = currentTransitionDip(d) {
            Rectangle().fill(dip.color)
                .opacity(dip.opacity)
                .allowsHitTesting(false)
                .accessibilityIdentifier("editorPro.transitionDip")
        }
    }

    /// Finds the transition (if any) whose ramp window contains the current OUTPUT frame, and
    /// the color/opacity Grade.tsx would paint there. Ports Grade.tsx's exact ramp math
    /// (`half = max(2, frames/2)`, `ramp = 1 - dist/half`, flash = white @ ramp*0.9) rather
    /// than inventing a new curve. The frame anchor comes from
    /// `EditorDocument.outputBoundary(afterSegment:)` — an existing helper already defined
    /// (by its own doc comment) as "the anchor a transition dip centers on" — so no new
    /// source→output mapping needed here.
    private func currentTransitionDip(_ d: EditorDocument) -> (color: Color, opacity: Double)? {
        let outFrame = secondsToFrame(player?.currentOutputTime ?? 0)
        for t in d.transitions {
            guard let boundarySec = d.outputBoundary(afterSegment: t.afterSegment) else { continue }
            let atFrame = secondsToFrame(boundarySec)
            let half = max(2.0, Double(t.frames) / 2.0)
            let dist = abs(Double(outFrame - atFrame))
            guard dist <= half else { continue }
            let ramp = 1 - dist / half
            if t.style == "flash" { return (.white, ramp * 0.9) }
            return (t.style == "fade_white" ? .white : .black, ramp)
        }
        return nil
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
        select(selectedSeg == idx ? nil : .seg(idx))
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

    // MARK: R10 — transport helpers (named undo/redo toasts + fullscreen preview)

    func doUndo() {
        guard let t = session?.undo() else { return }
        // An undo can remove the selected object (or the values an open expansion edits) —
        // clear EVERYTHING selection-shaped so the toolbar never shows a dead vocabulary.
        select(nil)
        refreshPlayer()
        showToast("Undo: \(opDisplayName(t))")
    }

    func doRedo() {
        guard let t = session?.redo() else { return }
        select(nil)
        refreshPlayer()
        showToast("Redo: \(opDisplayName(t))")
    }

    /// A capsule toast over the canvas (CapCut's "Undo: Split" pattern), auto-dismissed.
    func showToast(_ msg: String) {
        withAnimation(.easeOut(duration: 0.15)) { toast = msg }
        toastTick += 1
        let mine = toastTick
        Task {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            if toastTick == mine { withAnimation(.easeOut(duration: 0.2)) { toast = nil } }
        }
    }

    /// Friendly op names for the undo/redo toast.
    func opDisplayName(_ type: String) -> String {
        switch type {
        case "cut_range": return "Trim"
        case "restore_range": return "Restore"
        case "split_segment": return "Split clip"
        case "reorder_segments": return "Reorder"
        case "mute_range": return "Mute"
        case "set_segment_volume": return "Volume"
        case "set_segment_speed": return "Speed"
        case "set_transition": return "Transition"
        case "set_filter": return "Filter"
        case "set_adjust": return "Adjust"
        case "add_text_sticker": return "Add text"
        case "add_text_card": return "Text card"
        case "add_punch_in": return "Zoom"
        case "add_broll": return "Add b-roll"
        case "remove_broll": return "Remove b-roll"
        case "remove_overlays": return "Remove"
        case "edit_overlay": return "Edit"
        case "edit_caption": return "Caption"
        case "set_caption_style", "set_caption_options": return "Caption style"
        case "set_captions_enabled": return "Captions"
        case "set_music": return "Music"
        case "set_segment_transform": return "Reframe"
        default: return "Edit"
        }
    }

    /// R10: the transport ⛶ — the picture full-screen with a minimal play/close overlay.
    private var fullscreenPreview: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let player, !player.placeholder {
                PlayerLayerView(player: player.player)
                    .aspectRatio(9.0/16.0, contentMode: .fit)
            } else {
                Image(systemName: "film").font(.system(size: 48)).foregroundStyle(.white.opacity(0.3))
            }
            VStack {
                HStack {
                    Spacer()
                    Button { player?.pause(); showFullscreen = false } label: {
                        Image(systemName: "xmark").font(.system(size: 16, weight: .semibold))
                            .foregroundStyle(.white).frame(width: 40, height: 40)
                            .background(.black.opacity(0.4)).clipShape(Circle())
                    }.padding(Space.md)
                    .accessibilityIdentifier("editorPro.fullscreen.close")
                }
                Spacer()
                Button { player?.togglePlay() } label: {
                    Image(systemName: (player?.isPlaying ?? false) ? "pause.fill" : "play.fill")
                        .font(.system(size: 22)).foregroundStyle(.white)
                        .frame(width: 56, height: 56).background(.black.opacity(0.4)).clipShape(Circle())
                }.padding(.bottom, Space.xl)
            }
        }
    }

    // MARK: FP1 — look preview approximation (maps the render's CSS chain to SwiftUI)

    /// The SwiftUI filter params for a given look — shared by the live preview AND the
    /// filter thumbnail cards (so a card looks exactly like the applied result).
    func filterParams(_ filter: String?, intensity: Double,
                      adjust: EditorAdjust = EditorAdjust()) -> (sat: Double, con: Double, bri: Double, hue: Double) {
        var sat = 1.0, con = 1.0, bri = 0.0, hue = 0.0
        let t = min(1, max(0, intensity))
        func lerp(_ a: Double, _ b: Double) -> Double { a + (b - a) * t }
        switch filter {
        case "vivid": sat = lerp(1, 1.35); con = lerp(1, 1.08)
        case "film": con = lerp(1, 1.12); sat = lerp(1, 0.85)
        case "mono": sat = lerp(1, 0.0); con = lerp(1, 1.05)
        case "golden": sat = lerp(1, 1.2); bri = lerp(0, 0.05); hue = lerp(0, -6)
        case "warm": sat = lerp(1, 1.1); hue = lerp(0, -8)
        case "cool": sat = lerp(1, 1.05); hue = lerp(0, 10); bri = lerp(0, 0.02)
        default: break
        }
        bri += adjust.brightness * 0.6
        con *= (1 + adjust.contrast)
        sat *= (1 + adjust.saturation)
        hue += adjust.temperature < 0 ? -adjust.temperature * 20 : -adjust.temperature * 14
        return (sat, con, bri, hue)
    }

    private var lookVals: (sat: Double, con: Double, bri: Double, hue: Double) {
        guard let look = session?.draft.look else { return (1, 1, 0, 0) }
        return filterParams(look.filter, intensity: look.intensity, adjust: look.adjust)
    }
    private var lookSaturation: Double { lookVals.sat }
    private var lookContrast: Double { lookVals.con }
    private var lookBrightness: Double { lookVals.bri }
    private var lookHueDegrees: Double { lookVals.hue }

    // MARK: FP1c — the add-media panel (replaces the timeline pane; sheets are
    // invisible to accessibility/automation, and this reads cleaner anyway).

    var mediaPanel: some View {
        VStack(spacing: 0) {
            // Same fix as cleanupPanel (ProEditorView+Actions.swift) — see the
            // .accessibilityElement(children: .contain) on this panel's outer VStack
            // below for the actual root-cause explanation.
            HStack {
                Text("Add media").font(AppFont.headline).foregroundStyle(.white)
                Spacer()
                Button { withAnimation(.easeOut(duration: 0.15)) { showMediaPanel = false } } label: {
                    Text("Cancel").font(AppFont.headline).foregroundStyle(Palette.accent)
                        .padding(.horizontal, Space.md).padding(.vertical, 8)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("editorPro.mediaPanel.cancel")
            }
            .padding(.horizontal, Space.sm).padding(.top, Space.lg).padding(.bottom, Space.sm)

            VStack(spacing: Space.sm) {
                PhotosPicker(selection: $mediaPickerItem, matching: .any(of: [.images, .videos])) {
                    mediaRow("Photo or video", "photo.on.rectangle.angled",
                             "Drop your own shot over the cut")
                }
                .accessibilityIdentifier("editorPro.media.photo")
                Button {
                    withAnimation(.easeOut(duration: 0.15)) { showMediaPanel = false }
                    editDraft = ""; showStockInput = true
                } label: {
                    mediaRow("Stock clip", "film.stack", "Describe it — we find the footage")
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("editorPro.media.stock")
                Button {
                    withAnimation(.easeOut(duration: 0.15)) { showMediaPanel = false }
                    showMusicSheet = true
                } label: {
                    mediaRow("Music", "music.note", "A track under the whole cut")
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("editorPro.media.music")
                if uploadingMedia {
                    HStack(spacing: Space.sm) {
                        ProgressView().tint(Palette.accent)
                        Text("Adding your media…").font(AppFont.caption).foregroundStyle(.white.opacity(0.7))
                    }
                    .padding(.top, Space.sm)
                }
            }
            .padding(.horizontal, Space.lg)
            Spacer(minLength: 0)
        }
        .frame(height: 300, alignment: .top)
        .frame(maxWidth: .infinity)
        .background(Palette.ink.opacity(0.6))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("editorPro.mediaPanel")
    }

    private func mediaRow(_ title: String, _ icon: String, _ subtitle: String) -> some View {
        HStack(spacing: Space.md) {
            Image(systemName: icon).font(.system(size: 18)).foregroundStyle(Palette.accent)
                .frame(width: 34)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(AppFont.headline).foregroundStyle(.white)
                Text(subtitle).font(AppFont.caption).foregroundStyle(.white.opacity(0.55))
            }
            Spacer()
            Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(.white.opacity(0.4))
        }
        .padding(.horizontal, Space.md).padding(.vertical, 10)
        .background(Color.white.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .contentShape(Rectangle())
    }

    // MARK: FP1c — media roll preview on the canvas (full-frame, under captions,
    // exactly where BrollLayer renders it).

    @ViewBuilder private var rollSimOverlay: some View {
        if let d = session?.draft {
            let f = playheadSourceFrame
            if let idx = d.broll.firstIndex(where: { $0.srcIn <= f && f < $0.srcOut }) {
                let roll = d.broll[idx]
                Group {
                    if let url = roll.resolvedURL, let path = localMediaPreviews[url],
                       let img = UIImage(contentsOfFile: MediaStore.url(for: path).path) {
                        Image(uiImage: img).resizable().scaledToFill()
                    } else {
                        ZStack {
                            Rectangle().fill(Color(hex: 0xB56635).opacity(0.30))
                            VStack(spacing: 6) {
                                Image(systemName: roll.source == "own_media" ? "photo" : "film")
                                    .font(.system(size: 26)).foregroundStyle(.white.opacity(0.8))
                                Text(roll.source == "own_media" ? "Your media" : roll.cueText)
                                    .font(AppFont.caption).foregroundStyle(.white.opacity(0.8))
                                    .lineLimit(1)
                            }
                        }
                    }
                }
                .allowsHitTesting(false)
                .clipped()
                .accessibilityIdentifier("editorPro.rollSim")
            }
        }
    }

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
        let typing = typingSticker == idx
        let liveX = stickerDrag?.idx == idx ? stickerDrag!.x : o.posX
        let liveY = stickerDrag?.idx == idx ? stickerDrag!.y : o.posY
        let liveScale = stickerPinch?.idx == idx ? stickerPinch!.scale : o.scale
        let fontSize = 24 * liveScale
        return Group {
            if typing {
                // Keyboard-first: type directly on the canvas (CapCut/TikTok).
                TextField("Text", text: $editDraft, axis: .vertical)
                    .focused($stickerFieldFocused)
                    .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: geo.width * 0.8)
                    .submitLabel(.done)
                    .onSubmit { commitTyping(idx) }
            } else {
                Text(o.text)
            }
        }
        .font(simCaptionFont(o.font, size: fontSize, heavy: true))
        .foregroundStyle(o.color.map { colorFromHex($0) } ?? .white)
        .multilineTextAlignment(.center)
        .padding(.horizontal, o.bg == "box" ? 10 : 2).padding(.vertical, o.bg == "box" ? 5 : 2)
        .background(o.bg == "box" ? Color.black.opacity(0.65) : .clear)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous)
            .strokeBorder(selected ? Palette.accent : .clear,
                          style: StrokeStyle(lineWidth: 1.5, dash: typing ? [] : [4, 3])))
        // Corner action handles (CapCut selection box): ✕ delete · ✎ edit · ⧉ dup · resize.
        .overlay { if selected && !typing { stickerCornerHandles(idx: idx, o: o) } }
        .shadow(radius: o.bg == "box" ? 0 : 3)
        .rotationEffect(.degrees(o.rotation))
        .position(x: liveX * geo.width, y: liveY * geo.height)
        .highPriorityGestureIf(!typing,
            DragGesture(minimumDistance: 2)
                .onChanged { g in
                    stickerDrag = (idx,
                                   min(LayoutConstants.stickerPosXMax, max(LayoutConstants.stickerPosXMin, o.posX + g.translation.width / max(1, geo.width))),
                                   min(LayoutConstants.stickerPosYMax, max(LayoutConstants.stickerPosYMin, o.posY + g.translation.height / max(1, geo.height))))
                    if selectedOverlay != idx { select(.overlay(idx)) }
                }
                .onEnded { _ in
                    if let s = stickerDrag, s.idx == idx { commitStickerMove(idx, x: s.x, y: s.y) }
                    stickerDrag = nil
                })
        .simultaneousGestureIf(!typing,
            MagnificationGesture()
                .onChanged { v in stickerPinch = (idx, min(3.0, max(0.4, o.scale * v))) }
                .onEnded { _ in
                    if let p = stickerPinch, p.idx == idx { commitStickerScale(idx, scale: p.scale) }
                    stickerPinch = nil
                })
        .onTapGesture {
            if typing { return }
            select(selectedOverlay == idx ? nil : .overlay(idx))
        }
        // Same fix as cleanupPanel: when selected, stickerCornerHandles overlays 4 buttons
        // with their own "editorPro.sticker.<idx>.<action>" identifiers — without
        // .accessibilityElement(children: .contain) those get clobbered by this sticker's
        // own identifier.
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("editorPro.sticker.\(idx)")
    }

    /// The four corner controls on a selected sticker (CapCut/TikTok selection box).
    private func stickerCornerHandles(idx: Int, o: EditorOverlay) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                stickerHandle("xmark") { deleteOverlay(idx); bumpHaptic() }
                    .accessibilityIdentifier("editorPro.sticker.\(idx).delete")
                Spacer(minLength: 0)
                stickerHandle("pencil") { beginTypingSticker(idx); bumpHaptic() }
                    .accessibilityIdentifier("editorPro.sticker.\(idx).edit")
            }
            Spacer(minLength: 0)
            HStack(spacing: 0) {
                stickerHandle("plus.square.on.square") { duplicateSticker(idx); bumpHaptic() }
                    .accessibilityIdentifier("editorPro.sticker.\(idx).dup")
                Spacer(minLength: 0)
                stickerResizeGrip(idx: idx, o: o)
            }
        }
        .padding(-13)   // push handles just outside the text bounds, onto the corners
    }

    private func stickerHandle(_ icon: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: icon).font(.system(size: 10, weight: .bold))
                .foregroundStyle(Palette.night).frame(width: 22, height: 22)
                .background(Circle().fill(.white))
        }
        .buttonStyle(.plain)
    }

    /// One-finger scale (bottom-right grip): drag distance grows/shrinks the sticker.
    private func stickerResizeGrip(idx: Int, o: EditorOverlay) -> some View {
        Image(systemName: "arrow.up.left.and.arrow.down.right")
            .font(.system(size: 9, weight: .bold)).foregroundStyle(Palette.night)
            .frame(width: 22, height: 22).background(Circle().fill(.white))
            .contentShape(Rectangle().inset(by: -10))
            .highPriorityGesture(
                DragGesture()
                    .onChanged { g in
                        let delta = (g.translation.width + g.translation.height) / 200.0
                        stickerPinch = (idx, min(3.0, max(0.4, o.scale + delta)))
                    }
                    .onEnded { _ in
                        if let p = stickerPinch, p.idx == idx { commitStickerScale(idx, scale: p.scale) }
                        stickerPinch = nil
                    })
            .accessibilityIdentifier("editorPro.sticker.\(idx).resize")
    }

    // MARK: caption + punch-in local sim (L1 fidelity)

    /// Discrete position → the canvas Y fraction it anchors at (mirrors the render's
    /// LAYOUT.CAPTION_ANCHOR_Y via LayoutConstants).
    private func discreteCaptionY(_ position: String) -> Double {
        LayoutConstants.captionAnchorY[position] ?? LayoutConstants.captionAnchorY["bottom"]!
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
                        let norm = w.lowercased().filter { $0.isLetter || $0.isNumber }
                        let isHi = o.highlightWords.contains(norm)
                        Text(o.uppercase || d.captionStyle == "bold-word" ? w.uppercased() : w)
                            .font(simCaptionFont(o.font, size: base * effScale,
                                                 heavy: d.captionStyle == "bold-word" || isHi))
                            .foregroundStyle(isHi ? (o.accent.map { colorFromHex($0) } ?? Color(hex: 0xFFD60A))
                                                  : simCaptionColor(d, isHot: i == group.activeInGroup,
                                                                    spoken: i <= group.activeInGroup))
                            .opacity(d.captionStyle == "clean" && i != group.activeInGroup && !isHi ? 0.55 : 1)
                            .shadow(radius: 4)
                    }
                }
                .padding(.horizontal, o.bg.isEmpty ? 10 : 14).padding(.vertical, o.bg.isEmpty ? 4 : 6)
                // v6: live preview of the background pill (Boxed / Bubble presets).
                .background {
                    if !o.bg.isEmpty {
                        RoundedRectangle(cornerRadius: 10, style: .continuous).fill(colorFromHex(o.bg))
                    }
                }
                .accessibilityIdentifier("editorPro.captionSim")
                // Selection affordance: dashed bounds invite the drag whenever captions are
                // the working context (Captions panel open or a phrase selected); accent
                // while a gesture is live (TikTok's selection box).
                .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(interacting ? Palette.accent
                                              : (rootPanel == .captions || selectedPhraseID != nil
                                                 ? Color.white.opacity(0.35) : .clear),
                                  style: StrokeStyle(lineWidth: 1.5, dash: [4, 3])))
                .position(x: geo.size.width / 2, y: effY * geo.size.height)
                .highPriorityGesture(
                    DragGesture(minimumDistance: 3)
                        .onChanged { g in
                            let start = o.posY ?? discreteCaptionY(o.position)
                            var y = start + g.translation.height / max(1, geo.size.height)
                            // Snap to the three anchors (Edits' guide-line behavior).
                            for anchor in LayoutConstants.captionAnchorY.values where abs(y - anchor) < 0.025 { y = anchor }
                            capDragY = min(LayoutConstants.captionPosYMax, max(LayoutConstants.captionPosYMin, y))
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
                    if let y = capDragY, LayoutConstants.captionAnchorY.values.contains(y) {
                        Rectangle().fill(Color(hex: 0xFFD60A).opacity(0.8))
                            .frame(height: 1)
                            .position(x: geo.size.width / 2, y: y * geo.size.height)
                            .allowsHitTesting(false)
                    }
                }
            }
        }
    }

    /// L1 approximations of the render fonts (Inter / Archivo Black / Baloo 2 /
    /// Montserrat 900 / Anton — A2). None of the last two ship as bundled fonts on
    /// device, so this is a system-font weight/width approximation, same as the
    /// existing three — the real render (Remotion/Lambda) is the source of truth.
    private func simCaptionFont(_ font: String, size: CGFloat, heavy: Bool) -> Font {
        switch font {
        case "archivo": return .system(size: size, weight: .black)
        case "baloo": return .system(size: size, weight: heavy ? .heavy : .bold, design: .rounded)
        case "montserrat": return .system(size: size, weight: .black, design: .rounded)
        case "anton": return .system(size: size, weight: .black).width(.condensed)
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
        // UX-3 / formatting fix #12: bound the display window with the transcript so a
        // caption doesn't burn on screen through a silence — mirrors Captions.tsx's two-tier
        // hide rule (thresholds unified via LayoutConstants; the transcript `words` array is
        // this preview's stand-in for the render's own per-caption end_frame).
        if let span = words.first(where: { $0.startFrame == cap.frame }) ?? words.last(where: { $0.startFrame <= cap.frame }) {
            let isLastCaption = activeIdx == d.captions.count - 1
            if isLastCaption, srcFrame > span.endFrame + LayoutConstants.captionHideAfterLast {
                return nil
            }
            if let nextSpan = words.first(where: { $0.startFrame > span.startFrame }),
               srcFrame > span.endFrame, srcFrame < nextSpan.startFrame,
               nextSpan.startFrame - span.endFrame > LayoutConstants.captionSilenceGap {
                return nil
            }
        }
        if d.captionStyle == "bold-word" { return ([cap.word], 0) }
        let lo: Int, hi: Int
        switch d.captionOptions.grouping {
        case "word": lo = activeIdx; hi = activeIdx
        case "phrase":
            lo = (activeIdx / 3) * 3
            hi = min(d.captions.count - 1, lo + 2)
        default:   // "line" — P0.7: stable 5-word chunks (mirrors Captions.tsx), not sliding
            lo = (activeIdx / 5) * 5
            hi = min(d.captions.count - 1, lo + 4)
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
        var h: CGFloat = 12 + 2 + 64 + 8                                  // ruler + video + padding
        if captionsOn, !phrases.isEmpty { h += 26 }
        if !(session?.draft.overlays.isEmpty ?? true) { h += 24 }
        if let rolls = session?.draft.broll, !rolls.isEmpty {
            // Matches rollsLane's stacking: 22pt per row, second row only on overlap.
            let sorted = rolls.sorted { $0.srcIn < $1.srcIn }
            let overlaps = zip(sorted, sorted.dropFirst()).contains { $0.srcOut > $1.srcIn }
            h += overlaps ? 46 : 24
        } else if rootPanel == .effects {
            h += 24                                                        // "+ Add b-roll" strip
        }
        if showVoiceLane { h += 18 }                                       // voice lane (collapses when idle)
        if session?.draft.music != nil || rootPanel == .sound { h += 30 }
        return h + 8
    }

    /// R10: the voice waveform lane collapses when idle — it appears with the Sound panel,
    /// with a clip's Volume expansion, or once any clip's volume has been touched
    /// (muted / adjusted), so the idle timeline stays lean.
    private var showVoiceLane: Bool {
        if !(session?.draft.volumeRanges.isEmpty ?? true) { return true }
        if rootPanel == .sound { return true }
        if case .clipVolume = expansion { return true }
        return false
    }

    private var timelinePane: some View {
        EditorTimeline(
            document: session?.draft ?? EditorDocument(),
            player: player,
            filmstrip: filmstrip,
            pointsPerSecond: $pointsPerSecond,
            selectedSeg: selectedSeg,
            selectedOverlay: selectedOverlay,
            onTrim: { segIdx, edge, newFrame in trim(segIdx: segIdx, edge: edge, to: newFrame) },
            onReorder: { order in reorder(order) },
            onTapClip: { i in select(selectedSeg == i ? nil : .seg(i)) },
            onTapOverlay: { i in select(selectedOverlay == i ? nil : .overlay(i)) },
            onTapBackground: { if anySelection { select(nil) } },
            phrases: phrases,
            captionsOn: captionsOn,
            selectedPhraseID: selectedPhraseID,
            musicName: musicName,
            musicSeed: session?.draft.music?.url ?? "",
            musicVolume: session?.draft.music?.volume ?? 0.15,
            selectedMusic: selectedMusic,
            showMusicAdd: rootPanel == .sound && session?.draft.music == nil,
            onTapPhrase: { p in select(selectedPhraseID == p.id ? nil : .phrase(p.id)) },
            onTapMusic: { select(selectedMusic ? nil : .music) },
            onTapAddMusic: { showMusicSheet = true },
            onTapVoice: { segIdx in
                // Voice strip tap = select that clip with its Volume expansion open.
                select(.seg(segIdx))
                clipVolDraft = clipVolume(segIdx)
                expansion = .clipVolume(seg: segIdx)
            },
            selectedBoundary: selectedBoundary,
            onTapBoundary: { leading in select(selectedBoundary == leading ? nil : .boundary(leading)) },
            selectedBroll: selectedBroll,
            onTapBroll: { idx in select(selectedBroll == idx ? nil : .broll(idx)) },
            onTapAddMedia: { player?.pause(); withAnimation(.easeOut(duration: 0.18)) { showMediaPanel = true } },
            showRollsAdd: rootPanel == .effects,
            rollThumbs: localMediaPreviews,
            onTrimRoll: { idx, edge, delta in trimRoll(idx, edge: edge, deltaFrames: delta); bumpHaptic() },
            showVoice: showVoiceLane
        )
        .frame(height: timelineHeight)
        .background(Palette.ink.opacity(0.6))
    }

    // MARK: selection choke point (the one-bar invariant's enforcement)

    enum SelectionTarget {
        case seg(Int), overlay(Int), broll(Int), boundary(Int), music, phrase(Int)
    }

    private var anySelection: Bool {
        selectedSeg != nil || selectedOverlay != nil || selectedBroll != nil
            || selectedBoundary != nil || selectedMusic || selectedPhraseID != nil
    }

    /// The ONLY writer of selection state. Guarantees: at most one selection set; a selection
    /// and a rootPanel are never both active; expansions never outlive their host; in-flight
    /// sticker typing commits BEFORE anything touches the shared editDraft buffer.
    func select(_ target: SelectionTarget?) {
        if let idx = typingSticker { commitTyping(idx) }
        player?.pause()
        withAnimation(.easeOut(duration: 0.15)) {
            selectedSeg = nil; selectedOverlay = nil; selectedBroll = nil; selectedBoundary = nil
            selectedMusic = false; selectedPhraseID = nil
            expansion = nil
            rootPanel = nil
            switch target {
            case .seg(let i): selectedSeg = i
            case .overlay(let i): selectedOverlay = i
            case .broll(let i): selectedBroll = i
            case .boundary(let i): selectedBoundary = i
            case .music: if session?.draft.music != nil { selectedMusic = true }
            case .phrase(let id): selectedPhraseID = id
            case nil: break
            }
        }
        bumpHaptic()
    }

    /// Root tile tap: opens/toggles a panel (mutually exclusive with any selection).
    func openRootPanel(_ p: RootPanel) {
        if let idx = typingSticker { commitTyping(idx) }
        withAnimation(.easeOut(duration: 0.15)) {
            selectedSeg = nil; selectedOverlay = nil; selectedBroll = nil; selectedBoundary = nil
            selectedMusic = false; selectedPhraseID = nil
            expansion = nil
            rootPanel = (rootPanel == p) ? nil : p
        }
        bumpHaptic()
    }

    // MARK: the one bar — a single contextual toolbar that swaps wholesale by selection
    // (the CapCut/TikTok model). Root shows entry tiles; a selection shows that object's
    // verbs; the fixed chevron-down tile deselects layer by layer.

    enum ToolbarState {
        case root
        case clip(Int)
        case music
        case phrase(CaptionPhrase)
        case textSticker(Int)
        case textCard(Int)
        case punchIn(Int)
        case boundary(Int)
        case broll(Int)
    }

    /// Priority order (belt-and-suspenders for any transient double-set window):
    /// broll > boundary > overlay > phrase > music > seg > root.
    var toolbarState: ToolbarState {
        if let i = selectedBroll, session?.draft.broll[safe: i] != nil { return .broll(i) }
        if let b = selectedBoundary { return .boundary(b) }
        if let o = selectedOverlay, let ov = session?.draft.overlays[safe: o] {
            switch ov.type {
            case "punch_in": return .punchIn(o)
            case "text_card": return .textCard(o)
            default: return .textSticker(o)
            }
        }
        if let pid = selectedPhraseID, let p = phrases.first(where: { $0.id == pid }) { return .phrase(p) }
        if selectedMusic, session?.draft.music != nil { return .music }
        if let s = selectedSeg, session?.draft.segments[safe: s] != nil { return .clip(s) }
        return .root
    }

    private var atPlainRoot: Bool {
        if case .root = toolbarState { return rootPanel == nil }
        return false
    }

    var oneBar: some View {
        HStack(spacing: 0) {
            // Fixed deselect tile — hidden at plain root (nothing to pop). Topmost layer
            // only: expansion → selection → rootPanel (see chevronTap).
            if !atPlainRoot {
                Button { chevronTap() } label: {
                    Image(systemName: "chevron.down").font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(.white).frame(width: 44, height: 64)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("editorPro.ctx.back")
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Space.sm) { vocabularyTiles }
                    .padding(.horizontal, Space.sm)
            }
        }
        .frame(height: 84).background(Palette.ink)
    }

    /// Chevron pops the topmost layer only (CapCut drill-out): an open expansion first,
    /// then the selection, then the root panel.
    func chevronTap() {
        withAnimation(.easeOut(duration: 0.15)) {
            if expansion != nil { expansion = nil; return }
        }
        if anySelection { select(nil); return }
        if rootPanel != nil { withAnimation(.easeOut(duration: 0.15)) { rootPanel = nil }; bumpHaptic() }
    }

    @ViewBuilder private var vocabularyTiles: some View {
        switch toolbarState {
        case .root:
            barTile("Edit", "scissors", id: "editorPro.root.edit") { rootEditTap() }
            barTile("Sound", "music.note", id: "editorPro.root.sound", active: rootPanel == .sound) { openRootPanel(.sound) }
            barTile("Text", "textformat", id: "editorPro.root.text", active: rootPanel == .text) { openRootPanel(.text) }
            barTile("Captions", "captions.bubble", id: "editorPro.root.captions", active: rootPanel == .captions) { openRootPanel(.captions) }
            barTile("Clean up", "wand.and.sparkles", id: "editorPro.cleanup") {
                player?.pause(); cleanupSkip = []
                withAnimation(.easeOut(duration: 0.18)) { showCleanup = true }
            }
            if punchInsSupported || brollSupported {
                barTile("Effects", "sparkles", id: "editorPro.root.effects", active: rootPanel == .effects) { openRootPanel(.effects) }
            }
            barTile("Filters", "camera.filters", id: "editorPro.root.filters", active: rootPanel == .filters,
                    dot: filtersModified) { openRootPanel(.filters) }
        case .clip(let s):
            let speed = session?.draft.segments[safe: s]?.speed ?? 1.0
            barTile("Split", "square.split.2x1", id: "editorPro.ctx.split") { splitSelected(s); bumpHaptic() }
            barTile("Speed", "gauge.with.needle", id: "editorPro.ctx.speed", dot: abs(speed - 1.0) > 0.01) {
                speedDraft = speed
                withAnimation(.easeOut(duration: 0.15)) {
                    expansion = (expansion == .speed(seg: s)) ? nil : .speed(seg: s)
                }
                bumpHaptic()
            }
            barTile("Volume", "speaker.wave.2", id: "editorPro.ctx.volume", dot: abs(clipVolume(s) - 1.0) > 0.01) {
                clipVolDraft = clipVolume(s)
                withAnimation(.easeOut(duration: 0.15)) {
                    expansion = (expansion == .clipVolume(seg: s)) ? nil : .clipVolume(seg: s)
                }
                bumpHaptic()
            }
            barTile(mutedState(s) ? "Unmute" : "Mute",
                    mutedState(s) ? "speaker.slash.fill" : "speaker.slash",
                    id: "editorPro.muteToggle") { toggleMute(s); bumpHaptic() }
            barTile("Delete", "trash", id: "editorPro.ctx.delete") { deleteSelected(s); bumpHaptic() }
            barTile("Move ◀", "arrow.left", id: "editorPro.moveLeft", disabled: !canMoveSelected(by: -1)) {
                moveSelected(by: -1); bumpHaptic()
            }
            barTile("Move ▶", "arrow.right", id: "editorPro.moveRight", disabled: !canMoveSelected(by: 1)) {
                moveSelected(by: 1); bumpHaptic()
            }
        case .music:
            let vol = session?.draft.music?.volume ?? 0.15
            barTile("Replace", "arrow.triangle.2.circlepath", id: "editorPro.music.replace") { showMusicSheet = true }
            barTile("Volume", "speaker.wave.2", id: "editorPro.music.volume", dot: abs(vol - 0.15) > 0.001) {
                musicVolDraft = vol
                withAnimation(.easeOut(duration: 0.15)) {
                    expansion = (expansion == .musicVolume) ? nil : .musicVolume
                }
                bumpHaptic()
            }
            barTile("Delete", "trash", id: "editorPro.music.delete") { removeMusic(); bumpHaptic() }
        case .phrase(let p):
            barTile("Edit", "pencil", id: "editorPro.phrase.edit") { beginPhraseEdit(p); bumpHaptic() }
            barTile("Edit all", "list.bullet.rectangle", id: "editorPro.editCaptions") { showCaptionList = true }
            barTile("Style", "paintbrush", id: "editorPro.phrase.style", active: expansion == .captionStyle) {
                withAnimation(.easeOut(duration: 0.15)) {
                    expansion = (expansion == .captionStyle) ? nil : .captionStyle
                }
                bumpHaptic()
            }
            barTile("Customize", "slider.horizontal.3", id: "editorPro.capCustomize", active: expansion == .captionCustomize) {
                withAnimation(.easeOut(duration: 0.15)) {
                    expansion = (expansion == .captionCustomize) ? nil : .captionCustomize
                }
                bumpHaptic()
            }
            barTile("Remove fillers", "wand.and.sparkles", id: "editorPro.phrase.fillers") {
                player?.pause(); cleanupSkip = []
                withAnimation(.easeOut(duration: 0.18)) { showCleanup = true }
            }
        case .textSticker(let i):
            barTile("Edit", "pencil", id: "editorPro.ctx.edit") { beginTypingSticker(i); bumpHaptic() }
            barTile("Duplicate", "plus.square.on.square", id: "editorPro.ctx.duplicate") { duplicateSticker(i); bumpHaptic() }
            barTile("Delete", "trash", id: "editorPro.ctx.deleteOverlay") { deleteOverlay(i); bumpHaptic() }
        case .textCard(let i):
            barTile("Edit", "pencil", id: "editorPro.ctx.editOverlayText") { beginOverlayTextEdit(i); bumpHaptic() }
            barTile("Delete", "trash", id: "editorPro.ctx.deleteOverlay") { deleteOverlay(i); bumpHaptic() }
        case .punchIn(let i):
            let o = session?.draft.overlays[safe: i]
            ForEach([("Subtle", 1.05), ("Medium", 1.1), ("Strong", 1.2)], id: \.0) { label, scale in
                let active = abs((o?.scale ?? 1.08) - scale) < 0.02
                barTile(label, active ? "plus.magnifyingglass" : "magnifyingglass",
                        id: "editorPro.ctx.zoom\(label)", active: active) { setZoomIntensity(i, scale: scale); bumpHaptic() }
            }
            barTile("Shorter", "minus", id: "editorPro.ctx.shorter") { adjustOverlayDuration(i, deltaFrames: -15); bumpHaptic() }
            barTile("Longer", "plus", id: "editorPro.ctx.longer") { adjustOverlayDuration(i, deltaFrames: 15); bumpHaptic() }
            barTile("Delete", "trash", id: "editorPro.ctx.deleteOverlay") { deleteOverlay(i); bumpHaptic() }
            if let o {
                Text(String(format: "%.1fs", framesToSeconds(o.srcOut - o.srcIn)))
                    .font(AppFont.caption).foregroundStyle(.white.opacity(0.45)).monospacedDigit()
            }
        case .boundary(let b):
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
            if let t = session?.draft.transitions.first(where: { $0.afterSegment == b }) {
                barTile("Duration", "timer", id: "editorPro.ctx.duration") {
                    transDurDraft = Double(t.frames) / 30.0
                    withAnimation(.easeOut(duration: 0.15)) {
                        expansion = (expansion == .transitionDuration(boundary: b)) ? nil : .transitionDuration(boundary: b)
                    }
                    bumpHaptic()
                }
                Text(String(format: "%.1fs", Double(t.frames) / 30.0))
                    .font(.system(size: 10, weight: .semibold)).monospacedDigit().foregroundStyle(.white)
            }
        case .broll(let ri):
            barTile("Replace", "arrow.triangle.2.circlepath", id: "editorPro.ctx.replace") { replaceRoll(ri); bumpHaptic() }
            barTile("Duplicate", "plus.square.on.square", id: "editorPro.ctx.duplicate") { duplicateRoll(ri); bumpHaptic() }
            barTile("Shorter", "minus", id: "editorPro.ctx.shorter") { adjustRoll(ri, deltaFrames: -15); bumpHaptic() }
            barTile("Longer", "plus", id: "editorPro.ctx.longer") { adjustRoll(ri, deltaFrames: 15); bumpHaptic() }
            barTile("Delete", "trash", id: "editorPro.ctx.deleteRoll") { deleteRoll(ri); bumpHaptic() }
            if let roll = session?.draft.broll[safe: ri] {
                Text(roll.source == "own_media" ? "Your media" : roll.cueText)
                    .font(AppFont.caption).foregroundStyle(.white.opacity(0.45)).lineLimit(1)
            }
        }
    }

    /// Root "Edit" = select the clip under the playhead so its verbs appear. When playback
    /// parked at/after the output end (clipUnderPlayhead nil — the half-open interval test
    /// excludes the final frame), fall back to the LAST clip in play order (#9 convention).
    private func rootEditTap() {
        player?.pause()
        if let idx = clipUnderPlayhead {
            select(.seg(idx))
        } else if let d = session?.draft {
            let order = d.segmentOrder ?? Array(d.segments.indices)
            if let last = order.last(where: { d.keptBounds(ofSegment: $0) != nil }) {
                select(.seg(last))
            }
        }
    }

    /// One icon-over-label tile (~60pt wide, CapCut geometry) with an optional green
    /// modified-from-default dot under the label.
    private func barTile(_ label: String, _ icon: String, id: String, active: Bool = false,
                         dot: Bool = false, disabled: Bool = false,
                         _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 19))
                Text(label).font(.system(size: 10)).lineLimit(1)
                Circle().fill(Color(hex: 0x34D399)).frame(width: 4, height: 4).opacity(dot ? 1 : 0)
            }
            .foregroundStyle(active ? Palette.accent : .white)
            .frame(minWidth: 56)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled ? 0.35 : 1)
        .accessibilityIdentifier(id)
    }

    // MARK: expansion rows — one sub-tool at a time, REPLACING the toolbar slot.
    // Header: Reset (left, guarded — only emits when off-default) · content · checkmark
    // (right, closes). Sliders keep the UX-4 draft + one-op-on-release pattern.

    @ViewBuilder func expansionRow(_ e: Expansion) -> some View {
        HStack(spacing: Space.md) {
            switch e {
            case .speed(let seg):
                expansionReset {
                    let cur = session?.draft.segments[safe: seg]?.speed ?? 1.0
                    if abs(cur - 1.0) > 0.01 { setSpeed(seg, 1.0) }
                    speedDraft = 1.0
                }
                Text("SPEED").font(AppFont.micro).tracking(Track.label).foregroundStyle(.white.opacity(0.5))
                Slider(value: $speedDraft, in: 0.5...3.0, onEditingChanged: { editing in
                    if !editing { setSpeed(seg, speedDraft) }
                })
                .tint(Palette.accent)
                .accessibilityIdentifier("editorPro.speedSlider")
                Text(String(format: "%.1fx", speedDraft))
                    .font(.system(size: 12, weight: .bold)).monospacedDigit().foregroundStyle(.white)
                    .frame(width: 38)
                ForEach([1.0, 2.0], id: \.self) { v in
                    let active = abs((session?.draft.segments[safe: seg]?.speed ?? 1.0) - v) < 0.01
                    Button { speedDraft = v; setSpeed(seg, v); bumpHaptic() } label: {
                        Text(String(format: "%.0fx", v))
                            .font(.system(size: 11, weight: active ? .bold : .medium))
                            .foregroundStyle(active ? Palette.ink : .white)
                            .padding(.horizontal, 9).frame(height: 28)
                            .background(active ? Palette.onInk : Color.white.opacity(0.12))
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("editorPro.speed.\(v)")
                }
                expansionConfirm()
            case .clipVolume(let seg):
                expansionReset {
                    if abs(clipVolume(seg) - 1.0) > 0.01 { setClipVolume(seg, 1.0) }
                    clipVolDraft = 1.0
                }
                Text("VOLUME").font(AppFont.micro).tracking(Track.label).foregroundStyle(.white.opacity(0.5))
                Slider(value: $clipVolDraft, in: 0.0...2.0, onEditingChanged: { editing in
                    if editing { clipVolDraft = clipVolume(seg) }
                    else { setClipVolume(seg, clipVolDraft) }
                })
                .tint(Palette.accent)
                .accessibilityIdentifier("editorPro.clipVolume")
                Text("\(Int((clipVolDraft * 100).rounded()))%")
                    .font(.system(size: 11, weight: .semibold)).monospacedDigit().foregroundStyle(.white)
                    .frame(width: 42)
                expansionConfirm()
            case .musicVolume:
                expansionReset {
                    if let m = session?.draft.music, abs(m.volume - 0.15) > 0.001 { setMusicVolume(0.15) }
                    musicVolDraft = 0.15
                }
                Text("MUSIC").font(AppFont.micro).tracking(Track.label).foregroundStyle(.white.opacity(0.5))
                Slider(value: $musicVolDraft, in: 0.0...0.5, onEditingChanged: { editing in
                    if editing { musicVolDraft = session?.draft.music?.volume ?? 0.15 }
                    else { setMusicVolume(musicVolDraft) }
                })
                .tint(Palette.accent)
                .accessibilityIdentifier("editorPro.musicVolume")
                Text("\(Int((musicVolDraft * 100).rounded()))%")
                    .font(.system(size: 11, weight: .semibold)).monospacedDigit().foregroundStyle(.white)
                    .frame(width: 42)
                expansionConfirm()
            case .captionStyle:
                // Picker rows get no Reset — active state is visible on the chips.
                captionStyleRow
                expansionConfirm()
            case .captionCustomize:
                captionOptionsRow
                expansionConfirm()
            case .transitionDuration(let b):
                expansionReset {
                    if let t = session?.draft.transitions.first(where: { $0.afterSegment == b }),
                       t.frames != 12 { setTransitionDuration(after: b, seconds: 0.4) }
                    transDurDraft = 0.4
                }
                Text("DUR").font(AppFont.micro).tracking(Track.label).foregroundStyle(.white.opacity(0.5))
                // Draft + commit-on-release (UX-4) — the old inline slider committed one op
                // per drag TICK, spraying undo steps.
                Slider(value: $transDurDraft, in: 0.1...1.5, onEditingChanged: { editing in
                    if !editing { setTransitionDuration(after: b, seconds: transDurDraft) }
                })
                .tint(Palette.accent)
                .accessibilityIdentifier("editorPro.transitionDuration")
                Text(String(format: "%.1fs", transDurDraft))
                    .font(.system(size: 11, weight: .semibold)).monospacedDigit().foregroundStyle(.white)
                    .frame(width: 36)
                expansionConfirm()
            }
        }
        .padding(.horizontal, Space.md)
        .frame(height: 84).frame(maxWidth: .infinity)
        .background(Palette.ink)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier({ if case .speed = e { return "editorPro.speedRow" } else { return "editorPro.expansionRow" } }())
    }

    private func expansionReset(_ action: @escaping () -> Void) -> some View {
        Button { action(); bumpHaptic() } label: {
            Text("Reset").font(.system(size: 12, weight: .medium)).foregroundStyle(.white.opacity(0.7))
                .padding(.vertical, 8).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("editorPro.expansion.reset")
    }

    private func expansionConfirm() -> some View {
        Button {
            withAnimation(.easeOut(duration: 0.15)) { expansion = nil }
            bumpHaptic()
        } label: {
            Image(systemName: "checkmark").font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.white).frame(width: 36, height: 36)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("editorPro.expansion.confirm")
    }

    // #5/#8: capability helpers fall back to the LOCAL style rules (mirrors LocalEDLEngine's
    // gates — kept in lockstep with backend _PUNCH_STYLES/_TEXTCARD_STYLES) so tiles/buttons
    // stay STABLE when the server caps dict didn't load.
    private var draftStyle: String { session?.draft.style ?? "talking_head" }
    var punchInsSupported: Bool {
        (caps?["punch_ins"] ?? false)
            || ["talking_head", "duet_split", "green_screen", "broll_cutaway", "split_three"].contains(draftStyle)
    }
    var textCardsSupported: Bool {
        (caps?["text_cards"] ?? false)
            || ["green_screen", "duet_split", "talking_head", "broll_cutaway"].contains(draftStyle)
    }
    /// One definition, one nil-default: the backend always serves broll:true (BrollLayer is
    /// drawn by every composition) and the local engine is style-universal — so a missing
    /// caps dict must NOT hide the Effects tile (the old code had ?? false here and ?? true
    /// in the empty-state, contradicting each other).
    var brollSupported: Bool { caps?["broll"] ?? true }

    /// Green-dot condition for the Filters root tile (any look change off default).
    private var filtersModified: Bool {
        guard let look = session?.draft.look else { return false }
        return look.filter != nil || !look.adjust.isNeutral || abs(look.intensity - 1.0) > 0.001
    }
}

// R10: conditionally attach a gesture (SwiftUI has no optional-gesture overload) — used to
// drop the sticker drag/pinch while its TextField is being typed into.
extension View {
    @ViewBuilder func highPriorityGestureIf<G: Gesture>(_ on: Bool, _ g: G) -> some View {
        if on { highPriorityGesture(g) } else { self }
    }
    @ViewBuilder func simultaneousGestureIf<G: Gesture>(_ on: Bool, _ g: G) -> some View {
        if on { simultaneousGesture(g) } else { self }
    }

    /// Formatting fix #1: constrains the player view into the real composition's card/band
    /// for the 2 styles whose render frames the source video into a sub-region (green_screen,
    /// duet_split); every other style (incl. split_three, which dims/lines the SAME full-frame
    /// track rather than showing a distinct panel — see framingChrome) passes `self` through
    /// unmodified. Uses `.frame()/.position()` — never `.scaleEffect()`/other transforms — so
    /// the drag/pinch gestures already attached higher up the canvas ZStack keep working
    /// exactly as before (they key off `canvasGeo.size`, which this doesn't change).
    @ViewBuilder func proEditorPlayerFraming(style: String?, canvas: CGSize) -> some View {
        switch style {
        case "green_screen":
            // GreenScreen.tsx: speaker card inset 4% left/right, 3% from the bottom, 54% tall.
            let w = canvas.width * 0.92
            let h = canvas.height * 0.54
            let card = CGRect(x: canvas.width * 0.04, y: canvas.height * 0.97 - h, width: w, height: h)
            self
                .frame(width: card.width, height: card.height)
                .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                        .strokeBorder(Color.white.opacity(0.9), lineWidth: 2)
                )
                .position(x: card.midX, y: card.midY)
        case "duet_split":
            // DuetSplit.tsx: the creator's BOTTOM panel — full width, height = 1-topFrac.
            let h = canvas.height * (1 - editorDuetSplitFraction)
            self
                .frame(width: canvas.width, height: h)
                .position(x: canvas.width / 2, y: canvas.height - h / 2)
        default:
            self
        }
    }
}

/// DuetSplit.tsx: `edl.layout.split_fraction` sizes the top (reacted-to) band server-side;
/// EditorDocument carries no client-side layout field today, so this mirrors the render's
/// own fallback default exactly (`?? 0.58`). Shared by the framing backdrop/chrome (above)
/// and the player-framing modifier (below) so both sides of the divider always agree.
private let editorDuetSplitFraction: Double = 0.58

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
