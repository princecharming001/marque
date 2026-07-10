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
    @State private var pickedItem: PhotosPickerItem?
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

    enum Phase { case ready, recording, paused, stitching, recorded, analyzing, brief, making }

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
    }

    var body: some View {
        ZStack {
            background
            VStack(spacing: Space.lg) {
                topBar
                Spacer(minLength: 0)
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
                controls
            }
            .padding(Space.lg)
        }
        .onAppear { camera.configure() }
        .onDisappear {
            camera.teardown()
            submitTask?.cancel()      // AF-I3
            submitTask = nil
        }
        .onChange(of: pickedItem) { _, item in
            guard let item else { return }
            Task {
                // H-04: a failed import used to jump to .recorded with NO footage —
                // creating a doomed byte-less live job. Surface it and stay .ready.
                guard let data = try? await item.loadTransferable(type: Data.self) else {
                    importError = "Couldn't load that video — try a different one."
                    pickedItem = nil
                    phase = .ready
                    return
                }
                importError = nil
                footagePath = MediaStore.save(data, ext: "mov")
                // AF-I7: reset so re-picking the SAME video after Re-record fires
                // onChange again (equal PhotosPickerItems don't).
                pickedItem = nil
                phase = .recorded
                beginUpload()      // H-02: start the upload while the creator reviews
            }
        }
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
                PhotosPicker(selection: $pickedItem, matching: .videos) {
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
                if segments.count > 0 {
                    Text("Take \(segments.count + 1)").font(AppFont.caption).foregroundStyle(.white.opacity(0.6))
                }
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
                    recordButton(active: true) { finishTake() }
                    // Pause the TAKE — device only (lets you resume from a new angle).
                    if camera.hasCamera {
                        Button { pauseTake() } label: {
                            Image(systemName: "pause.circle")
                                .font(.system(size: 20)).foregroundStyle(.white)
                                .marqueGlassCircle(diameter: 52)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("record.pauseTake")
                    }
                }
            case .paused:
                takeTimer
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
                // H-05: no format picker — the editor infers the right style/format
                // from the take itself and shows its plan on the brief screen next.
                Text("Nice take. Your editor will study it and show you the plan.")
                    .font(AppFont.callout).foregroundStyle(.white.opacity(0.85)).multilineTextAlignment(.center)
                Button { reRecord() } label: {
                    Label("Re-record", systemImage: "arrow.counterclockwise")
                        .font(AppFont.callout).foregroundStyle(.white.opacity(0.85))
                }
                .accessibilityIdentifier("record.reRecord")
                if liveScript.style == VideoStyle.duetSplit.rawValue {
                    reactSourceField
                }
                Button { makeClips() } label: {
                    Text("Submit for editing")
                        .font(AppFont.headline).foregroundStyle(Palette.ink)
                        .frame(maxWidth: .infinity).padding(.vertical, Space.lg)
                        .background(Palette.onInk).clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("record.makeClips")
                GhostButton(title: "Save as draft") { saveDraftAndClose() }
                    .accessibilityIdentifier("record.saveDraft")
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
                        briefChip(brief.videoTypeLabel)
                        briefChip(brief.strategy == "restructure" ? "Re-ordered for the hook" : "Tightened, not re-cut")
                        if !brief.cutRegions.isEmpty {
                            briefChip(brief.cutRegions.count == 1 ? "1 cut" : "\(brief.cutRegions.count) cuts")
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .center)

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

                TextField("Anything specific? (optional)", text: $customInstructions, axis: .vertical)
                    .font(AppFont.callout).foregroundStyle(.white)
                    .lineLimit(1...3)
                    .padding(Space.md)
                    .background(Color.white.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    .accessibilityIdentifier("record.customInstructions")
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
    /// style) shows the toggle — never wrongly hide a real capability.
    private func briefCapability(_ key: String) -> Bool {
        guard let styleCaps else { return true }
        let style = brief?.inferred?.style.isEmpty == false ? brief!.inferred!.style
                                                            : liveScript.style
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
        if segments.count <= 1 {
            if let only = segments.first, let data = try? Data(contentsOf: only) {
                footagePath = MediaStore.save(data, ext: "mov")
            }
            phase = .recorded
            beginUpload()      // H-02: upload runs while the creator reviews the take
            return
        }
        phase = .stitching
        Task {
            let stitched = await VideoStitcher.stitch(segments)
            let final = stitched ?? segments.first   // never strand: fall back to take 1
            if let final, let data = try? Data(contentsOf: final) {
                footagePath = MediaStore.save(data, ext: "mov")
            }
            phase = .recorded
            beginUpload()
        }
    }

    private func reRecord() {
        uploadTask?.cancel()   // H-02: stale footage — restart the hoisted upload fresh
        uploadTask = nil
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
        phase = .analyzing
        // Keep the raw take in the Library so it can be re-cut later.
        if let footagePath {
            store.addFootage(path: footagePath, scriptId: liveScript.id,
                             title: liveScript.title.isEmpty ? liveScript.hook.text : liveScript.title,
                             seconds: liveScript.targetSeconds)
        }
        submitTask = Task {
            defer { submitTask = nil }
            if uploadTask == nil { beginUpload() }            // sim/no-footage path
            let publicURL = await uploadTask?.value
            guard !Task.isCancelled else { return }           // AF-I3: dismissed mid-submit
            guard let resp = await store.startAnalyzeJob(
                    script: isFreestyle ? nil : liveScript, publicURL: publicURL,
                    customInstructions: customInstructions,
                    reactSourceURL: reactSourceURL.trimmingCharacters(in: .whitespacesAndNewlines)) else {
                await fallbackToMock()
                return
            }
            analyzeJobId = resp.jobId
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
