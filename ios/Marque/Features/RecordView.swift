import SwiftUI
import PhotosUI

// Real camera on device (CameraModel/AVFoundation); simulated capture in the Simulator
// (no camera hardware) so the batch loop stays end-to-end testable. (05-screens-produce.md)

struct RecordView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @StateObject private var camera = CameraModel()
    let script: Script?                          // I-4: nil = freestyle (no teleprompter)
    private var isFreestyle: Bool { script == nil }

    @State private var phase: Phase = .ready
    @State private var promptRunning = false
    @State private var speed: Double = 1.0
    @State private var restartToken = 0
    @State private var recordStart: Date?
    @State private var footagePath: String?
    @State private var pickedItems: [PhotosPickerItem] = []
    // Import progress while picked videos load off the Photos library (large videos take
    // several seconds each) — without this the UI looked frozen on the record screen.
    @State private var isImporting = false
    @State private var importDone = 0
    @State private var importTotal = 0
    // duet_split only: the clip the creator is reacting to (pasted URL).
    @State private var reactSourceURL: String = ""
    // Mutable copy so inline teleprompter edits flow through without modifying the store mid-take.
    @State private var liveScript: Script
    // Multi-take: each pause finalizes a segment; finishing stitches them into one
    // continuous clip on-device (so the backend still gets a single source_url).
    @State private var segments: [URL] = []
    @State private var takeElapsed: TimeInterval = 0   // accumulated across finished segments
    // Analyze-first (H-02): mint+upload starts the moment a take lands (runs while the
    // creator reviews), so the public URL usually exists before "Submit for editing".
    @State private var uploadTask: Task<String?, Never>? = nil
    // AF-I3: the submit/analyze task is tracked so dismissal cancels it — an orphaned
    // task otherwise inserted clips, bumped the streak, and yanked navigation minutes
    // after the user left this screen.
    @State private var submitTask: Task<Void, Never>? = nil
    @State private var analyzeJobId: String? = nil
    @State private var brief: EditBrief? = nil
    @State private var briefToggles = EditToggles()
    @State private var customInstructions = ""
    @State private var importError: String? = nil
    // Per-style edit capabilities (G-04) — gates which toggles the brief screen shows.
    @State private var styleCaps: [String: [String: Bool]]? = nil
    // The submit-time cut treatment — pins the engine style server-side. Seeded from
    // the script's style lane; the creator can change it before submitting.
    @State private var editFormat: EditFormat
    // "Match a vibe": per-format example reels + the one the creator picked to mimic.
    @State private var exampleReels: [ReelItem] = []
    // Honest submit-failure surface (never fake-ready mock clips against a real backend).
    @State private var submitFailedMessage: String? = nil
    // Which treatment the toggles were last seeded from (view re-creation must not reseed).
    @State private var lastSeededFormat: EditFormat? = nil
    // The take already saved to Footage (dedup across submit retries).
    @State private var savedFootagePath: String? = nil
    // UX-A3: mimic-card playback state — play only the cards actually on screen,
    // and remember hard playback failures so those cards fall back to their poster.
    @State private var visibleMimicIds: Set<String> = []
    @State private var failedMimicIds: Set<String> = []
    @State private var referenceReel: ReelItem? = nil
    // B-ROLL STYLE picker: how much cutaway coverage the creator wants (full/balanced/
    // minimal/none), each option demonstrated by a real example reel. The pick drives the
    // edit via config.broll_coverage + the b-roll toggle ("none" switches cutaways off).
    @State private var brollStyles: [BrollStyleOption] = []
    @State private var selectedBrollStyle: String = "cutaway"

    enum Phase { case ready, recording, paused, stitching, recorded, analyzing, brief, making }

    /// The prompter shows only while filming; review/brief phases reclaim its space.
    private var showsPrompter: Bool {
        switch phase {
        case .ready, .recording, .paused, .stitching: return true
        case .recorded, .analyzing, .brief, .making: return false
        }
    }

    init(script: Script?) {
        self.script = script
        // Freestyle: a minimal placeholder script so clip metadata + the analyze pipeline
        // have something to hang on; the teleprompter is hidden (I-4). Mirrors the synthesized
        // script ChatStore.runEditClips uses for its script-less edit flow.
        _liveScript = State(initialValue: script ?? Script(
            pillarName: "Freestyle", title: "Freestyle take", summary: "Filmed off script",
            style: VideoStyle.talkingHead.rawValue, formatId: "myth-buster",
            hook: Hook(text: "Freestyle take", signal: .narrative, strength: 70),
            altHooks: [], body: "", cta: "", shotPlan: [], targetSeconds: 60, predictedScore: 70))
        _editFormat = State(initialValue: EditFormat.inferred(fromScriptStyle: script?.style ?? ""))
    }

    var body: some View {
        ZStack {
            background
            VStack(spacing: Space.lg) {
                topBar
                Spacer(minLength: 0)
                // The prompter matters only while filming — after the take it's dead
                // copy stealing 300pt the format picker/brief need.
                if showsPrompter {
                    if isFreestyle {
                        VStack(spacing: Space.sm) {
                            Image(systemName: "mic.fill").font(.system(size: 22)).foregroundStyle(.white.opacity(0.7))
                            Text("No script — just talk.")
                                .font(AppFont.title).foregroundStyle(.white)
                            Text("Film it your way; the editor finds the cut after.")
                                .font(AppFont.caption).foregroundStyle(.white.opacity(0.7))
                                .multilineTextAlignment(.center)
                        }
                        .frame(height: 300)
                    } else {
                        Teleprompter(script: $liveScript, running: $promptRunning, speed: $speed, restartToken: $restartToken)
                            .frame(height: 300)
                    }
                    Spacer(minLength: 0)
                }
                controls
            }
            .padding(Space.lg)
        }
        .overlay { if isImporting { importingOverlay } }
        .onAppear { camera.configure() }
        .onDisappear {
            camera.teardown()
            submitTask?.cancel()      // AF-I3
            submitTask = nil
        }
        .onChange(of: pickedItems) { _, items in
            guard !items.isEmpty else { return }
            let picked = items
            // Reset so re-picking the SAME video (equal PhotosPickerItems don't re-fire
            // onChange) works after a Re-record.
            pickedItems = []
            Task { await handlePickedVideos(picked) }
        }
    }

    @ViewBuilder private var importingOverlay: some View {
        ZStack {
            Color.black.opacity(0.72).ignoresSafeArea()
            VStack(spacing: Space.md) {
                ProgressView().tint(.white).scaleEffect(1.3)
                Text(importTotal > 1 ? "Importing \(min(importDone + 1, importTotal)) of \(importTotal)…"
                                     : "Importing video…")
                    .font(AppFont.headline).foregroundStyle(.white)
                Text("Keep the app open — larger videos take a moment.")
                    .font(AppFont.caption).foregroundStyle(.white.opacity(0.65))
            }
            .padding(Space.xl)
        }
        .transition(.opacity)
        .accessibilityIdentifier("record.importing")
    }

    /// Import one picked video into the app container. File-URL transfer first (streams to
    /// disk — real library videos are hundreds of MB and a Data transfer gets memory-killed);
    /// Data is the fallback for odd providers with no file rep. Returns the saved path or nil.
    private func importOne(_ item: PhotosPickerItem) async -> String? {
        if let picked = try? await item.loadTransferable(type: PickedVideoFile.self) {
            let ext = picked.url.pathExtension.isEmpty ? "mov" : picked.url.pathExtension
            let p = MediaStore.saveFile(from: picked.url, ext: ext)
            try? FileManager.default.removeItem(at: picked.url)
            return p
        } else if let data = try? await item.loadTransferable(type: Data.self) {
            return MediaStore.save(data, ext: "mov")
        }
        return nil
    }

    /// A fresh freestyle placeholder script for an uploaded video (mirrors the init seed).
    private func freestyleTakeScript() -> Script {
        Script(pillarName: "Freestyle", title: "Uploaded video", summary: "Imported footage",
               style: VideoStyle.talkingHead.rawValue, formatId: "myth-buster",
               hook: Hook(text: "Uploaded video", signal: .narrative, strength: 70),
               altHooks: [], body: "", cta: "", shotPlan: [], targetSeconds: 60, predictedScore: 70)
    }

    private func handlePickedVideos(_ items: [PhotosPickerItem]) async {
        importError = nil
        importTotal = items.count
        importDone = 0
        isImporting = true
        defer { isImporting = false }

        // Single video → the review flow (style picker, toggles) as before.
        if items.count == 1 {
            if let path = await importOne(items[0]) {
                importDone = 1
                importError = nil
                footagePath = path
                phase = .recorded
                beginUpload()          // start the upload while the creator reviews
            } else {
                importDone = 1
                importError = "Couldn't load that video — try a different one."
                phase = .ready
            }
            return
        }

        // Multiple videos → each becomes its own freestyle clip, submitted to the queue.
        // The store owns each upload → create-job → reconcile, so leaving the screen never
        // cancels them; Library shows one "Uploading…" card per video.
        var failures = 0
        for item in items {
            if let path = await importOne(item) {
                store.addFootage(path: path, scriptId: liveScript.id, title: "Uploaded video", seconds: 60)
                let toggles = editFormat.defaultToggles
                store.submitTakeInstant(script: freestyleTakeScript(), footagePath: path,
                                        isFreestyle: true, customInstructions: customInstructions,
                                        reactSourceURL: "", editFormat: editFormat.rawValue,
                                        referenceReel: nil, config: brollConfig(),
                                        toggles: toggles)
            } else {
                failures += 1
            }
            importDone += 1
        }
        if failures > 0 { importError = "\(failures) of \(items.count) couldn't be imported." }
        dismiss()
        router.selectedTab = .library
        router.showFilm = false
    }

    @ViewBuilder private var background: some View {
        if camera.status == .ready || camera.status == .recording {
            CameraPreview(session: camera.session).ignoresSafeArea()
            Color.black.opacity(0.45).ignoresSafeArea()   // legibility for the teleprompter
        } else {
            Palette.night.ignoresSafeArea()
        }
    }

    private var topBar: some View {
        HStack {
            // Close — LiquidGlass pill (glass reads on camera, marqueCard doesn't)
            Button { dismiss() } label: {
                ZStack {
                    LiquidGlassFill(radius: 19, corners: false)
                    Image(systemName: "xmark")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(.white)
                }
                .frame(width: 38, height: 38)
                .shadow(color: Palette.shadowCool.opacity(0.18), radius: 12, x: 0, y: 6)
            }
            .buttonStyle(.plain)

            Spacer()

            // Kicker — "TELEPROMPTER"
            Text("TELEPROMPTER")
                .font(AppFont.micro).tracking(Track.label)
                .foregroundStyle(.white.opacity(0.7))

            Spacer()

            // Balancer keeps the kicker optically centered against the 38pt close
            // pill (the old format badge here read as an odd floating tab).
            Color.clear.frame(width: 38, height: 38)
        }
    }

    private var controls: some View {
        VStack(spacing: Space.lg) {
            switch phase {
            case .ready:
                Text(camera.status == .unavailable ? "Camera access is off. Enable it in Settings, or upload a video below." : "Read it once. We'll cut the rest.")
                    .font(AppFont.body).foregroundStyle(.white.opacity(0.7)).multilineTextAlignment(.center)
                if let importError {
                    Text(importError)
                        .font(AppFont.caption).foregroundStyle(Palette.critical)
                        .multilineTextAlignment(.center)
                        .accessibilityIdentifier("record.importError")
                }
                if camera.status == .unavailable {
                    Button {
                        if let url = URL(string: "app-settings:") { openURL(url) }
                    } label: {
                        Label("Enable camera access", systemImage: "gearshape")
                            .font(AppFont.callout).foregroundStyle(.white)
                    }
                    .accessibilityIdentifier("record.openSettings")
                }
                speedControl
                recordButton { startRecording() }
                PhotosPicker(selection: $pickedItems, maxSelectionCount: 10, matching: .videos) {
                    Label("Upload existing video", systemImage: "square.and.arrow.up")
                        .font(AppFont.callout).foregroundStyle(.white.opacity(0.85))
                }
                .accessibilityIdentifier("record.upload")
            case .recording:
                takeTimer
                if camera.hasCamera && !camera.hasAudio {
                    Text("Microphone is off — your clip will have no sound. Enable mic access in Settings.")
                        .font(AppFont.caption).foregroundStyle(Palette.critical).multilineTextAlignment(.center)
                }
                takeSegmentsBar(liveTake: true)
                speedControl
                HStack(spacing: Space.xl) {
                    // Teleprompter scroll play/pause (does not stop recording).
                    Button { promptRunning.toggle() } label: {
                        Image(systemName: promptRunning ? "pause.fill" : "play.fill")
                            .font(.system(size: 18)).foregroundStyle(.white)
                            .marqueGlassCircle(diameter: 52)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("record.pausePrompt")
                    // The record button TOGGLES the take: tap again to pause (ends this
                    // take, ready for the next angle). Finishing is always the separate
                    // Done button — one consistent meaning per button, in both states.
                    // Sim has no camera → pause is meaningless there; record = finish,
                    // which keeps the Maestro fast path (single record.capture tap) intact.
                    recordButton(active: true) { if camera.hasCamera { pauseTake() } else { finishTake() } }
                    Button { finishTake() } label: {
                        Text("Done").font(AppFont.headline).foregroundStyle(.white)
                            .frame(width: 52, height: 52).marqueGlassCircle(diameter: 52)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("record.finishTake")
                }
            case .paused:
                takeTimer
                takeSegmentsBar(liveTake: false)
                Text("Paused — flip the camera for a new angle, then resume. Your takes stitch into one clip.")
                    .font(AppFont.caption).foregroundStyle(.white.opacity(0.7)).multilineTextAlignment(.center)
                HStack(spacing: Space.xl) {
                    Button { camera.flip() } label: {
                        Image(systemName: "arrow.triangle.2.circlepath.camera")
                            .font(.system(size: 18)).foregroundStyle(.white)
                            .marqueGlassCircle(diameter: 52)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("record.flipCamera")
                    recordButton(active: false) { resumeTake() }
                    Button { finishTake() } label: {
                        Text("Done").font(AppFont.headline).foregroundStyle(.white)
                            .frame(width: 52, height: 52).marqueGlassCircle(diameter: 52)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("record.finishTake")
                }
            case .stitching:
                ProgressView().tint(Palette.accent)
                Text("Stitching your takes…").font(AppFont.body).foregroundStyle(.white.opacity(0.7))
            case .recorded:
                // The creator picks the CUT TREATMENT here (it pins the engine style
                // server-side) and can optionally point at a reel to mimic the vibe of.
                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: Space.md) {
                        Text("HOW SHOULD WE CUT THIS?")
                            .font(AppFont.micro).tracking(Track.label)
                            .foregroundStyle(.white.opacity(0.6))
                            .frame(maxWidth: .infinity, alignment: .center)
                        formatGrid
                        mimicSection
                        if liveScript.style == VideoStyle.duetSplit.rawValue || selectedBrollStyle == "split_screen" {
                            reactSourceField
                        }
                        // UX-B1b: the recorded screen is now the SINGLE context screen —
                        // toggles + instructions live here (moved from the brief screen)
                        // so submit goes straight to the render with no approve stop.
                        VStack(spacing: Space.xs) {
                            if briefCapability("broll") {
                                briefToggleRow("B-roll cutaways", isOn: $briefToggles.broll)
                            }
                            if briefCapability("punch_ins") {
                                briefToggleRow("Punch-ins for emphasis", isOn: $briefToggles.punchIns)
                            }
                            briefToggleRow("Background music", isOn: $briefToggles.music)
                        }
                        // prompt: gives the placeholder a legible color — the plain title
                        // form renders it in system gray, unreadable on the dark overlay.
                        TextField("", text: $customInstructions,
                                  prompt: Text("Anything specific? (optional)")
                                    .foregroundColor(.white.opacity(0.6)),
                                  axis: .vertical)
                            .font(AppFont.callout).foregroundStyle(.white)
                            .lineLimit(1...3)
                            .padding(Space.md)
                            .background(Color.white.opacity(0.08))
                            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                            .accessibilityIdentifier("record.customInstructions")
                    }
                }
                .frame(maxHeight: 560)
                .task(id: editFormat) {
                    // Reseed ONLY when the treatment actually changed — this task also
                    // re-runs when the .recorded view is re-created (add-a-take, honest
                    // submit-failure return) and must not wipe the creator's flips.
                    if lastSeededFormat != editFormat {
                        briefToggles = editFormat.defaultToggles
                        lastSeededFormat = editFormat
                        syncBrollToggle()      // the picked b-roll style survives a format change
                    }
                    await loadBrollStyles()
                }
                .task { await loadCapabilities() }
                HStack(spacing: Space.lg) {
                    // Multi-take: keep everything filmed so far and add one more take.
                    // Device-only (the simulator path records no segments to extend).
                    if camera.hasCamera && !segments.isEmpty {
                        Button { addAnotherTake() } label: {
                            Label("Add a take", systemImage: "plus.circle")
                                .font(AppFont.callout).foregroundStyle(.white.opacity(0.85))
                        }
                        .accessibilityIdentifier("record.addTake")
                    }
                    Button { reRecord() } label: {
                        Label("Re-record", systemImage: "arrow.counterclockwise")
                            .font(AppFont.callout).foregroundStyle(.white.opacity(0.85))
                    }
                    .accessibilityIdentifier("record.reRecord")
                    GhostButton(title: "Save as draft") { saveDraftAndClose() }
                        .accessibilityIdentifier("record.saveDraft")
                }
                if let msg = submitFailedMessage {
                    Text(msg)
                        .font(AppFont.caption).foregroundStyle(Palette.critical)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .multilineTextAlignment(.center)
                }
                Button { makeClips() } label: {
                    Text("Submit for editing")
                        .font(AppFont.headline).foregroundStyle(Palette.ink)
                        .frame(maxWidth: .infinity).padding(.vertical, Space.lg)
                        .background(Palette.onInk).clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("record.makeClips")
            case .analyzing:
                ProgressView().tint(Palette.accent)
                Text("Studying your take — cuts, hook, pacing…")
                    .font(AppFont.body).foregroundStyle(.white.opacity(0.7)).multilineTextAlignment(.center)
            case .brief:
                briefReview
            case .making:
                ProgressView().tint(Palette.accent)
                Text("Sending to your editor…").font(AppFont.body).foregroundStyle(.white.opacity(0.7))
            }
        }
    }

    // H-03: the brief + toggles review — what the editor UNDERSTOOD and PLANS before
    // any render is spent. Toggles are hidden when the inferred style can't render
    // them (GET /v1/editor/capabilities); captions + filler cuts are always-on and
    // deliberately not toggles. No auto-reframe toggle by design.
    @ViewBuilder private var briefReview: some View {
        // The confirm CTA stays PINNED below the scrollable plan — it must never
        // need a scroll to reach (it's also what the E2E flow taps).
        VStack(spacing: Space.md) {
        ScrollView(showsIndicators: false) {
            VStack(alignment: .leading, spacing: Space.md) {
                Text("YOUR EDIT PLAN")
                    .font(AppFont.micro).tracking(Track.label)
                    .foregroundStyle(.white.opacity(0.6))
                    .frame(maxWidth: .infinity, alignment: .center)

                if let brief {
                    HStack(spacing: Space.sm) {
                        briefChip(editFormat.label)          // the treatment the creator picked
                        briefChip(brief.strategy == "restructure" ? "Re-ordered for the hook" : "Tightened, not re-cut")
                        if !brief.cutRegions.isEmpty {
                            briefChip(brief.cutRegions.count == 1 ? "1 cut" : "\(brief.cutRegions.count) cuts")
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .center)
                    if let ref = referenceReel {
                        Text("Matching the vibe of @\(ref.creatorHandle)")
                            .font(AppFont.caption).foregroundStyle(Palette.accent)
                            .frame(maxWidth: .infinity, alignment: .center)
                    }

                    if !brief.throughLine.isEmpty {
                        Text(brief.throughLine)
                            .font(AppFont.callout).foregroundStyle(.white.opacity(0.9))
                            .multilineTextAlignment(.leading)
                    }
                    if let hook = brief.hookCandidates.first, !hook.quote.isEmpty {
                        VStack(alignment: .leading, spacing: Space.xs) {
                            Text("OPENING ON").font(AppFont.micro).tracking(Track.label)
                                .foregroundStyle(.white.opacity(0.5))
                            Text("“\(hook.quote)”")
                                .font(AppFont.body).foregroundStyle(Palette.accent)
                        }
                        .padding(Space.md)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.white.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    }
                }

                VStack(spacing: Space.xs) {
                    if briefCapability("broll") {
                        briefToggleRow("B-roll cutaways", isOn: $briefToggles.broll)
                    }
                    if briefCapability("punch_ins") {
                        briefToggleRow("Punch-ins for emphasis", isOn: $briefToggles.punchIns)
                    }
                    briefToggleRow("Background music", isOn: $briefToggles.music)
                }

                // UX-B1b: the customInstructions TextField moved to the recorded
                // (single-context) screen; anything typed there still flows through
                // this fallback path's confirm call.
            }
        }
        .frame(maxHeight: 320)

        Button { confirmBrief() } label: {
            Text("Make my clip")
                .font(AppFont.headline).foregroundStyle(Palette.ink)
                .frame(maxWidth: .infinity).padding(.vertical, Space.lg)
                .background(Palette.onInk)
                .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("record.makeMyClip")
        }
        .task { await loadCapabilities() }
    }

    private func briefChip(_ text: String) -> some View {
        Text(text)
            .font(AppFont.caption).foregroundStyle(.white)
            .padding(.horizontal, Space.md).padding(.vertical, 6)
            .background(Color.white.opacity(0.12))
            .clipShape(Capsule())
    }

    // MARK: Edit-format picker + "match a vibe" (pre-submit)

    private var formatGrid: some View {
        let cols = [GridItem(.flexible(), spacing: Space.sm), GridItem(.flexible(), spacing: Space.sm)]
        return LazyVGrid(columns: cols, spacing: Space.sm) {
            ForEach(EditFormat.allCases) { f in
                let selected = editFormat == f
                Button { selectFormat(f) } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Image(systemName: f.icon).font(.system(size: 15, weight: .semibold))
                        Text(f.label)
                            .font(AppFont.caption.weight(.semibold))
                            .lineLimit(1).minimumScaleFactor(0.75)
                        Text(f.blurb)
                            .font(.system(size: 10))
                            .opacity(0.75)
                            .lineLimit(2, reservesSpace: true)
                            .multilineTextAlignment(.leading)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(Space.sm + 2)
                    .background(selected ? Color.white : Color.white.opacity(0.10))
                    .foregroundStyle(selected ? Palette.ink : .white)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("record.format.\(f.rawValue)")
            }
        }
    }

    private func selectFormat(_ f: EditFormat) {
        guard editFormat != f else { return }
        withAnimation(.easeOut(duration: 0.15)) {
            editFormat = f
            referenceReel = nil        // the picked vibe belongs to the old format
            exampleReels = []          // .task(id: editFormat) refetches
        }
    }

    @ViewBuilder private var mimicSection: some View {
        if !brollStyles.isEmpty {
            VStack(alignment: .leading, spacing: Space.xs) {
                Text("B-ROLL STYLE — PICK A LOOK")
                    .font(AppFont.micro).tracking(Track.label)
                    .foregroundStyle(.white.opacity(0.5))
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: Space.sm) {
                        ForEach(Array(brollStyles.enumerated()), id: \.element.id) { i, s in
                            brollStyleCard(s, index: i)
                        }
                    }
                }
            }
        }
    }

    private func brollStyleCard(_ s: BrollStyleOption, index: Int) -> some View {
        let selected = selectedBrollStyle == s.id
        // The card SHOWS the style via a self-rendered demo clip through this exact
        // composition (cutaway/panel/card/green-screen/split-screen) — a pixel-accurate
        // preview of the treatment, not a mimicked creator reel. Picking the card sends
        // config.broll_mode or config.composition_style, which forces that treatment.
        let playable = !s.videoURL.isEmpty && !failedMimicIds.contains(s.id)
        return Button {
            withAnimation(.easeOut(duration: 0.15)) {
                selectedBrollStyle = s.id
                syncBrollToggle()
            }
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                ZStack(alignment: .topTrailing) {
                    if playable, visibleMimicIds.contains(s.id), let url = URL(string: s.videoURL) {
                        FailableVideoPlayer(url: url, muted: true, showsControls: false,
                                            onFailure: { failedMimicIds.insert(s.id) })
                        .frame(width: 118, height: 148)
                        .allowsHitTesting(false)         // the CARD is the tap target
                    } else {
                        AsyncImage(url: URL(string: s.thumbnailURL)) { img in
                            ZStack {
                                img.resizable().aspectRatio(contentMode: .fill)
                                    .blur(radius: 12).opacity(0.55)
                                img.resizable().aspectRatio(contentMode: .fit)
                            }
                        } placeholder: {
                            Rectangle().fill(Color.white.opacity(0.08))
                                .overlay(Image(systemName: "photo.on.rectangle.angled")
                                    .foregroundStyle(.white.opacity(0.3)))
                        }
                        .frame(width: 118, height: 148).clipped()
                    }
                    if selected {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundStyle(Palette.accent)
                            .background(Circle().fill(.white).padding(2))
                            .padding(5)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
                .onAppear { visibleMimicIds.insert(s.id) }
                .onDisappear { visibleMimicIds.remove(s.id) }
                Text(s.label)                            // the B-ROLL style — this is the choice
                    .font(.system(size: 11, weight: .bold)).foregroundStyle(.white)
                    .lineLimit(1)
                Text(s.blurb)                            // what the style means for the cut
                    .font(.system(size: 10)).foregroundStyle(.white.opacity(0.6))
                    .lineLimit(2, reservesSpace: true)
                    .multilineTextAlignment(.leading)
            }
            .frame(width: 118)
            .padding(4)
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(selected ? Palette.accent : .clear, lineWidth: 2))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("record.brollStyle.\(index)")
    }

    private func loadBrollStyles() async {
        let opts = await store.backend.brollStyles(niche: store.brand.niche)
        guard !Task.isCancelled else { return }
        brollStyles = opts
    }

    /// Every composition style involves some visual treatment now (there's no "none"
    /// option), so b-roll cutaways stay available; the picked style steers the LOOK.
    private func syncBrollToggle() {
        briefToggles.broll = true
    }

    /// The config dict the pick maps to: cutaway/panel/card force every b-roll insert's
    /// mode (config.broll_mode); green_screen/split_screen override the whole job style
    /// (config.composition_style) since they're full composition treatments, not b-roll.
    private func brollConfig() -> [String: String]? {
        switch selectedBrollStyle {
        case "cutaway": return ["broll_mode": "full"]
        case "panel":   return ["broll_mode": "panel"]
        case "card":    return ["broll_mode": "card"]
        case "green_screen", "split_screen":
            return ["composition_style": selectedBrollStyle]
        default: return nil
        }
    }

    private func briefToggleRow(_ label: String, isOn: Binding<Bool>) -> some View {
        HStack {
            Text(label).font(AppFont.callout).foregroundStyle(.white)
            Spacer(minLength: Space.md)
            MarqueToggle(isOn: isOn, offTrack: Color.white.opacity(0.22))
        }
        .contentShape(Rectangle())
        .onTapGesture { isOn.wrappedValue.toggle() }
        .padding(.horizontal, Space.md).padding(.vertical, Space.sm)
        .background(Color.white.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
    }

    /// Style-gated toggle visibility. Missing data (caps fetch failed / unknown
    /// style) shows the toggle — never wrongly hide a real capability. The picked
    /// edit format pins the engine style, so it (not the script lane) is the fallback.
    private func briefCapability(_ key: String) -> Bool {
        guard let styleCaps else { return true }
        let style = brief?.inferred?.style.isEmpty == false ? brief!.inferred!.style
                                                            : editFormat.engineStyle
        return styleCaps[style]?[key] ?? true
    }

    private func loadCapabilities() async {
        guard styleCaps == nil else { return }
        styleCaps = await BackendClient.shared.editorCapabilities()
    }

    @ViewBuilder private var speedControl: some View {
        if isFreestyle {
            EmptyView()          // no teleprompter to pace in freestyle mode
        } else {
        HStack(spacing: Space.sm) {
            ForEach([("Slow", 0.6), ("Normal", 1.0), ("Fast", 1.5)], id: \.0) { label, val in
                Button { speed = val } label: {
                    Group {
                        if speed == val {
                            Text(label).font(AppFont.caption).foregroundStyle(Palette.ink)
                                .padding(.horizontal, Space.md).padding(.vertical, 7)
                                .background(Palette.onInk).clipShape(Capsule())
                        } else {
                            Text(label).font(AppFont.caption).foregroundStyle(.white)
                                .padding(.horizontal, Space.md)
                                .marqueGlassCapsule(height: 30)
                        }
                    }
                }
                .buttonStyle(.plain)
            }
        }
        }
    }

    // MARK: actions

    // Accumulated take time across finished segments + the live segment.
    private var takeTimer: some View {
        HStack(spacing: Space.sm) {
            Circle().fill(phase == .paused ? Palette.textTertiary : Palette.critical)
                .frame(width: 8, height: 8)
            if phase == .recording, let start = recordStart {
                TimelineView(.periodic(from: start, by: 1)) { ctx in
                    let secs = Int(takeElapsed + max(0, ctx.date.timeIntervalSince(start)))
                    Text(String(format: "%d:%02d / ~%ds", secs / 60, secs % 60, liveScript.targetSeconds))
                        .font(AppFont.body).foregroundStyle(.white).monospacedDigit()
                }
            } else {
                let secs = Int(takeElapsed)
                Text(String(format: "%d:%02d / ~%ds", secs / 60, secs % 60, liveScript.targetSeconds))
                    .font(AppFont.body).foregroundStyle(.white.opacity(0.85)).monospacedDigit()
            }
        }
    }

    private func startRecording() {
        segments = []
        takeElapsed = 0
        phase = .recording
        restartToken += 1
        recordStart = Date()
        promptRunning = true
        if camera.hasCamera && camera.status == .ready {
            camera.start()
        } else {
            // Simulator (no camera): single mock take → straight to .recorded, so
            // the Maestro fast path (one record.capture tap) is unchanged.
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.4) {
                if phase == .recording { phase = .recorded }
            }
        }
    }

    /// Pause the current take: finalize this segment, hold in .paused so the user
    /// can flip the camera and resume. Device only.
    private func pauseTake() {
        guard camera.hasCamera else { return }
        promptRunning = false
        if let s = recordStart { takeElapsed += Date().timeIntervalSince(s) }
        camera.stop { url in
            if let url { segments.append(url) }
            recordStart = nil
            phase = .paused
        }
    }

    private func resumeTake() {
        phase = .recording
        recordStart = Date()
        promptRunning = true
        if camera.hasCamera && camera.status == .ready { camera.start() }
    }

    /// Finish the take from either .recording (stop the live segment first) or
    /// .paused (no live segment), then stitch all segments into one clip.
    private func finishTake() {
        promptRunning = false
        guard camera.hasCamera else { phase = .recorded; return }   // sim
        if phase == .recording {
            if let s = recordStart { takeElapsed += Date().timeIntervalSince(s) }
            camera.stop { url in
                if let url { segments.append(url) }
                recordStart = nil
                stitchAndFinish()
            }
        } else {
            stitchAndFinish()
        }
    }

    private func stitchAndFinish() {
        // saveFile (copy, not Data(contentsOf:)) — a multi-take session can be minutes
        // of footage, and materializing that in RAM risks the same memory-kill as the
        // old library-upload path.
        if segments.count <= 1 {
            if let only = segments.first {
                footagePath = MediaStore.saveFile(from: only, ext: "mov")
            }
            phase = .recorded
            beginUpload()      // H-02: upload runs while the creator reviews the take
            return
        }
        phase = .stitching
        Task {
            let stitched = await VideoStitcher.stitch(segments)
            let final = stitched ?? segments.first   // never strand: fall back to take 1
            if let final {
                footagePath = MediaStore.saveFile(from: final, ext: "mov")
            }
            phase = .recorded
            beginUpload()
        }
    }

    private func reRecord() {
        uploadTask?.cancel()   // H-02: stale footage — restart the hoisted upload fresh
        uploadTask = nil
        submitFailedMessage = nil
        analyzeJobId = nil
        brief = nil
        footagePath = nil
        recordStart = nil
        segments = []
        takeElapsed = 0
        restartToken += 1
        phase = .ready
    }

    /// Keep the take without submitting it: footage + script land as a draft clip
    /// (Library › Drafts), resumable from the Film screen. No streak, no clips yet.
    private func saveDraftAndClose() {
        // liveScript so inline teleprompter edits survive into the draft (same as makeClips).
        store.saveDraft(from: liveScript, footagePath: footagePath)
        dismiss()
        router.showFilm = false
    }

    /// H-02: start mint+upload as soon as footage exists — by the time the creator
    /// taps "Submit for editing" the public URL is usually already minted+uploaded.
    private func beginUpload() {
        guard uploadTask == nil else { return }
        let path = footagePath
        uploadTask = Task { await LiveClipEngine.mintAndUpload(footagePath: path) }
    }

    /// H-02 analyze-first submit: await the hoisted upload → create the analyze job →
    /// show the brief (immediately keyless; after a short poll live). Any failure
    /// falls back to the legacy local mock pipeline — the creator is never stranded.
    private func makeClips() {
        guard submitTask == nil else { return }               // AF-I3: no double-submit
        submitFailedMessage = nil
        // Keep the raw take in the Library so it can be re-cut later — ONCE per take.
        if let footagePath, footagePath != savedFootagePath {
            store.addFootage(path: footagePath, scriptId: liveScript.id,
                             title: liveScript.title.isEmpty ? liveScript.hook.text : liveScript.title,
                             seconds: liveScript.targetSeconds)
            savedFootagePath = footagePath
        }
        // WS4 — instant return: only the one-tap (auto-confirm) path benefits, and it's
        // what every founder hits. Insert an "Uploading…" card and go straight to Library;
        // the store owns the upload → create-job → reconcile so dismissing never cancels it.
        // (The legacy brief-approve path below still awaits inline — it needs the brief on
        //  screen before the creator can act.)
        if uploadTask != nil { uploadTask?.cancel(); uploadTask = nil }   // store re-runs the upload
        store.submitTakeInstant(script: isFreestyle ? liveScript : liveScript,
                                footagePath: footagePath, isFreestyle: isFreestyle,
                                customInstructions: customInstructions,
                                reactSourceURL: reactSourceURL.trimmingCharacters(in: .whitespacesAndNewlines),
                                editFormat: editFormat.rawValue, referenceReel: referenceReel,
                                config: brollConfig(),
                                toggles: briefToggles)
        dismiss()
        router.selectedTab = .library
        router.showFilm = false
    }

    // Legacy analyze/brief-approve submit — retained for the non-auto-confirm path (kept
    // intact; the one-tap flow above is what ships to founders).
    private func makeClipsLegacy() {
        guard submitTask == nil else { return }
        submitFailedMessage = nil
        phase = .analyzing
        if let footagePath, footagePath != savedFootagePath {
            store.addFootage(path: footagePath, scriptId: liveScript.id,
                             title: liveScript.title.isEmpty ? liveScript.hook.text : liveScript.title,
                             seconds: liveScript.targetSeconds)
            savedFootagePath = footagePath
        }
        submitTask = Task {
            defer { submitTask = nil }
            if uploadTask == nil { beginUpload() }            // sim/no-footage path
            let publicURL = await uploadTask?.value
            guard !Task.isCancelled else { return }           // AF-I3: dismissed mid-submit
            guard let resp = await store.startAnalyzeJob(
                    script: isFreestyle ? nil : liveScript, publicURL: publicURL,
                    customInstructions: customInstructions,
                    reactSourceURL: reactSourceURL.trimmingCharacters(in: .whitespacesAndNewlines),
                    editFormat: editFormat.rawValue,
                    referenceReel: referenceReel,
                    config: brollConfig(),
                    autoConfirm: true,                        // UX-B1b: one-tap submit
                    toggles: briefToggles) else {
                await fallbackToMock()
                return
            }
            analyzeJobId = resp.jobId
            // UX-B1b: a clips array in the create response = the new one-tap backend —
            // the pipeline is already running; track the clips and go straight to the
            // Library (no approve stop). Streak/celebration ride trackSubmittedClips.
            if let stubs = resp.clips, !stubs.isEmpty {
                guard !Task.isCancelled else { return }
                if briefToggles.broll { store.primeBrollCorpus() }
                store.trackSubmittedClips(jobId: resp.jobId, script: liveScript,
                                          footagePath: footagePath,
                                          stubs: stubs.map { ($0.clipId, $0.format, $0.status == "ready") },
                                          etaSeconds: resp.etaSeconds)
                dismiss()
                router.selectedTab = .library
                router.showFilm = false
                return
            }
            // Old backend (no clips in the response) → the existing analyze/brief
            // approve flow, fully intact.
            if let b = resp.editBrief {                       // keyless: brief is immediate
                brief = b
                briefToggles = resp.toggles ?? EditToggles()
                phase = .brief
            } else if let polled = await store.pollForBrief(jobId: resp.jobId),
                      let b = polled.editBrief {
                guard !Task.isCancelled else { return }
                brief = b
                briefToggles = polled.toggles ?? EditToggles()
                phase = .brief
            } else {
                await fallbackToMock()                        // analysis failed/timed out
            }
        }
    }

    private func confirmBrief() {
        guard let jobId = analyzeJobId else { return }
        phase = .making
        Task {
            await store.confirmClips(jobId: jobId, script: liveScript, toggles: briefToggles,
                                     customInstructions: customInstructions,
                                     footagePath: footagePath)
            dismiss()
            router.selectedTab = .library
            router.showFilm = false
        }
    }

    /// Legacy local pipeline — mock clips via the mock engine directly (also the
    /// offline/demo path when the backend is unreachable). AF-I3: a cancelled submit
    /// (user dismissed mid-analyze) must not insert clips, bump the streak, or yank
    /// navigation minutes later. AF-I6: the mock engine directly — the live engine
    /// would re-compress + re-upload the whole take just to hit the 426 cutover.
    private func fallbackToMock() async {
        guard !Task.isCancelled else { return }
        // HONESTY FIX (the "ready clip plays raw footage" bug): against a REAL backend,
        // a transport failure / stalled pipeline must NOT insert fake-"ready" mock clips
        // whose video is the raw take — the creator sees a finished-looking clip that
        // plays unedited footage. Instead: keep them on this screen with an honest error
        // so they can retry (the take is already saved as Footage). The mock path stays
        // for genuinely backendless demo/dev runs only.
        if !AppConfig.backendBaseURL.isEmpty {
            // Reset the hoisted upload: when the FAILURE was the upload itself, retrying
            // with the cached nil result could never succeed (review finding).
            uploadTask?.cancel()
            uploadTask = nil
            phase = .recorded
            submitFailedMessage = "Couldn't reach the editor — your take is saved. Tap Submit to try again."
            return
        }
        await store.makeClips(from: liveScript, formats: [liveScript.formatId],
                              footagePath: footagePath,
                              reactSourceURL: reactSourceURL.trimmingCharacters(in: .whitespacesAndNewlines),
                              useMockEngine: true)
        guard !Task.isCancelled else { return }
        dismiss()
        router.selectedTab = .library
        router.showFilm = false
    }

    // Paste the reacted-to clip for a duet/react split (top panel of the render).
    private var reactSourceField: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text("What are you reacting to?")
                .font(AppFont.caption).foregroundStyle(.white.opacity(0.7))
            TextField("Paste a video link", text: $reactSourceURL)
                .font(AppFont.callout).foregroundStyle(.white)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .padding(.horizontal, Space.md).frame(height: 44)
                .marqueGlassCapsule(height: 44)
                .accessibilityIdentifier("record.reactSource")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// TikTok-style take indicator: one pill per finished segment, plus a pulsing
    /// red pill for the take being recorded right now. Shows only once takes exist.
    @ViewBuilder private func takeSegmentsBar(liveTake: Bool) -> some View {
        if !segments.isEmpty || liveTake {
            HStack(spacing: 5) {
                ForEach(0..<segments.count, id: \.self) { _ in
                    Capsule().fill(.white.opacity(0.9)).frame(width: 22, height: 5)
                }
                if liveTake {
                    Capsule().fill(Palette.critical).frame(width: 22, height: 5)
                        .opacity(0.9)
                }
                if segments.count > 0 {
                    Text(liveTake ? "Take \(segments.count + 1)" : "\(segments.count) take\(segments.count == 1 ? "" : "s")")
                        .font(AppFont.micro).tracking(0.4)
                        .foregroundStyle(.white.opacity(0.7))
                        .padding(.leading, 4)
                }
            }
            .accessibilityIdentifier("record.takesBar")
        }
    }

    /// From the review screen: come back for ANOTHER take (device only). Existing
    /// segments are preserved — Done re-stitches everything into one clip; the hoisted
    /// upload restarts because the stitched footage is about to change.
    private func addAnotherTake() {
        uploadTask?.cancel()
        uploadTask = nil
        analyzeJobId = nil
        submitFailedMessage = nil
        footagePath = nil          // will be re-stitched from ALL segments on Done
        phase = .paused
    }

    private func recordButton(active: Bool = false, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            ZStack {
                Circle().strokeBorder(.white.opacity(0.5), lineWidth: 4).frame(width: 78, height: 78)
                RoundedRectangle(cornerRadius: active ? 6 : 30, style: .continuous)
                    .fill(Palette.critical)
                    .frame(width: active ? 32 : 62, height: active ? 32 : 62)
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(active ? "Stop recording" : "Start recording")
        .accessibilityIdentifier("record.capture")
    }
}

// MARK: - Teleprompter (speed-adjustable, pausable, inline-editable while filming)

struct Teleprompter: View {
    @Binding var script: Script
    @Binding var running: Bool
    @Binding var speed: Double
    @Binding var restartToken: Int
    @State private var offset: CGFloat = 0
    @State private var contentH: CGFloat = 1
    @State private var editingField: EditField? = nil
    @State private var draft = ""
    // Non-nil while the user is dragging the teleprompter to override the auto-pace.
    // Captures the offset at touch-down; auto-scroll suspends until release, then
    // resumes immediately from wherever they left it.
    @State private var dragBase: CGFloat? = nil
    @FocusState private var editorFocused: Bool
    private let ticker = Timer.publish(every: 1.0 / 60.0, on: .main, in: .common).autoconnect()

    enum EditField { case hook, body, cta }

    private var isEditing: Bool { editingField != nil }

    private func startEdit(_ field: EditField) {
        switch field {
        case .hook: draft = script.hook.text
        case .body: draft = script.body
        case .cta: draft = script.cta
        }
        running = false
        editingField = field
        editorFocused = true
    }

    private func commitEdit() {
        let trimmed = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let field = editingField else { editingField = nil; return }
        switch field {
        case .hook: script.hook.text = trimmed
        case .body: script.body = trimmed
        case .cta: script.cta = trimmed
        }
        editingField = nil
    }

    var body: some View {
        GeometryReader { geo in
            let viewport = geo.size.height
            let maxScroll = max(0, contentH - viewport * 0.5)
            ZStack(alignment: .topLeading) {
                VStack(alignment: .leading, spacing: Space.lg) {
                    teleprompterLine(script.hook.text, font: Typeface.display(28, .semibold), field: .hook)
                    teleprompterLine(script.body, font: Typeface.body(23), field: .body)
                    teleprompterLine(script.cta, font: Typeface.body(23, .semibold), field: .cta, color: Palette.accent)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(GeometryReader { g in
                    Color.clear.preference(key: TeleHeightKey.self, value: g.size.height)
                })
                .offset(y: -offset)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .clipped()
                .contentShape(Rectangle())
                .gesture(
                    // Drag to scrub the script yourself — auto-pace hands off while
                    // you drag and picks back up from where you release. 12pt min so
                    // a tap still routes to a line's edit gesture.
                    DragGesture(minimumDistance: 12)
                        .onChanged { g in
                            if dragBase == nil { dragBase = offset }
                            offset = min(maxScroll, max(0, (dragBase ?? offset) - g.translation.height))
                        }
                        .onEnded { _ in dragBase = nil }
                )

                if isEditing {
                    Color.black.opacity(0.7).ignoresSafeArea()
                        .onTapGesture { commitEdit() }
                    VStack(spacing: Space.md) {
                        TextEditor(text: $draft)
                            .font(Typeface.body(22))
                            .foregroundStyle(.white)
                            .scrollContentBackground(.hidden)
                            .frame(minHeight: 120, maxHeight: 240)
                            .padding(Space.md)
                            .background(Color.white.opacity(0.12))
                            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                            .focused($editorFocused)
                        HStack(spacing: Space.md) {
                            Button("Cancel") { editingField = nil }
                                .font(AppFont.callout).foregroundStyle(.white.opacity(0.7))
                            Spacer()
                            Button("Done") { commitEdit() }
                                .font(AppFont.headline).foregroundStyle(Palette.accent)
                        }
                    }
                    .padding(Space.lg)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
                }
            }
            .onPreferenceChange(TeleHeightKey.self) { contentH = $0 }
            .onChange(of: restartToken) { _, _ in offset = 0 }
            .onReceive(ticker) { _ in
                guard running, !isEditing, dragBase == nil, maxScroll > 0 else { return }
                let pxPerSec = contentH / CGFloat(max(6, script.targetSeconds)) * CGFloat(speed)
                offset = min(maxScroll, offset + pxPerSec / 60.0)
            }
        }
        .mask(LinearGradient(colors: [.clear, .black, .black, .clear], startPoint: .top, endPoint: .bottom))
    }

    @ViewBuilder
    private func teleprompterLine(_ text: String, font: Font, field: EditField,
                                   color: Color = .white.opacity(0.94)) -> some View {
        Text(text)
            .font(font)
            .foregroundStyle(color)
            .lineSpacing(field == .body ? 6 : 0)
            .fixedSize(horizontal: false, vertical: true)
            .onTapGesture { startEdit(field) }
            .overlay(alignment: .topTrailing) {
                Image(systemName: "pencil")
                    .font(.system(size: 11))
                    .foregroundStyle(.white.opacity(0.4))
                    .padding(4)
            }
    }
}

private struct TeleHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = nextValue() }
}
