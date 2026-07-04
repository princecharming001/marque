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
    let script: Script

    @State private var phase: Phase = .ready
    @State private var promptRunning = false
    @State private var speed: Double = 1.0
    @State private var restartToken = 0
    @State private var recordStart: Date?
    @State private var footagePath: String?
    @State private var pickedItem: PhotosPickerItem?
    @State private var selectedFormats: Set<String>
    // duet_split only: the clip the creator is reacting to (pasted URL).
    @State private var reactSourceURL: String = ""
    // Mutable copy so inline teleprompter edits flow through without modifying the store mid-take.
    @State private var liveScript: Script

    enum Phase { case ready, recording, recorded, making }

    init(script: Script) {
        self.script = script
        _liveScript = State(initialValue: script)
        // Default to just the script's own format — the creator opts INTO extra cuts, we don't pre-check them.
        _selectedFormats = State(initialValue: Set([script.formatId]))
    }

    var body: some View {
        ZStack {
            background
            VStack(spacing: Space.lg) {
                topBar
                Spacer(minLength: 0)
                Teleprompter(script: $liveScript, running: $promptRunning, speed: $speed, restartToken: $restartToken)
                    .frame(height: 300)
                Spacer(minLength: 0)
                controls
            }
            .padding(Space.lg)
        }
        .onAppear { camera.configure() }
        .onDisappear { camera.teardown() }
        .onChange(of: pickedItem) { _, item in
            guard let item else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self) {
                    footagePath = MediaStore.save(data, ext: "mov")
                }
                phase = .recorded
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

            // Format pill (right side)
            FormatTag(formatId: script.formatId).colorScheme(.dark)
        }
    }

    private var controls: some View {
        VStack(spacing: Space.lg) {
            switch phase {
            case .ready:
                Text(camera.status == .unavailable ? "Camera access is off. Enable it in Settings, or upload a video below." : "Read it once. We'll cut the rest.")
                    .font(AppFont.body).foregroundStyle(.white.opacity(0.7)).multilineTextAlignment(.center)
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
                HStack(spacing: Space.sm) {
                    Circle().fill(Palette.critical).frame(width: 8, height: 8)
                    if let start = recordStart {
                        TimelineView(.periodic(from: start, by: 1)) { ctx in
                            let secs = max(0, Int(ctx.date.timeIntervalSince(start)))
                            Text(String(format: "%d:%02d / ~%ds", secs / 60, secs % 60, liveScript.targetSeconds))
                                .font(AppFont.body).foregroundStyle(.white).monospacedDigit()
                        }
                    } else {
                        Text("Recording…").font(AppFont.body).foregroundStyle(Palette.accent)
                    }
                }
                if camera.hasCamera && !camera.hasAudio {
                    Text("Microphone is off — your clip will have no sound. Enable mic access in Settings.")
                        .font(AppFont.caption).foregroundStyle(Palette.critical).multilineTextAlignment(.center)
                }
                speedControl
                HStack(spacing: Space.xl) {
                    Button { promptRunning.toggle() } label: {
                        Image(systemName: promptRunning ? "pause.fill" : "play.fill")
                            .font(.system(size: 18)).foregroundStyle(.white)
                            .marqueGlassCircle(diameter: 52)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("record.pausePrompt")
                    recordButton(active: true) { stopRecording() }
                }
            case .recorded:
                Text("Nice take. Choose the formats to cut it into.").font(AppFont.callout).foregroundStyle(.white.opacity(0.85)).multilineTextAlignment(.center)
                Button { reRecord() } label: {
                    Label("Re-record", systemImage: "arrow.counterclockwise")
                        .font(AppFont.callout).foregroundStyle(.white.opacity(0.85))
                }
                .accessibilityIdentifier("record.reRecord")
                formatPicker
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
                .disabled(selectedFormats.isEmpty)
                .opacity(selectedFormats.isEmpty ? 0.5 : 1)
                .accessibilityIdentifier("record.makeClips")
                GhostButton(title: "Save as draft") { saveDraftAndClose() }
                    .accessibilityIdentifier("record.saveDraft")
            case .making:
                ProgressView().tint(Palette.accent)
                Text("Sending to your editor…").font(AppFont.body).foregroundStyle(.white.opacity(0.7))
            }
        }
    }

    private var speedControl: some View {
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

    // MARK: actions

    private func startRecording() {
        phase = .recording
        restartToken += 1
        recordStart = Date()
        promptRunning = true
        if camera.hasCamera && camera.status == .ready {
            camera.start()
        } else {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.4) {
                if phase == .recording { phase = .recorded }
            }
        }
    }

    private func stopRecording() {
        promptRunning = false
        if camera.hasCamera {
            camera.stop { url in
                if let url, let data = try? Data(contentsOf: url) {
                    footagePath = MediaStore.save(data, ext: "mov")
                }
                phase = .recorded
            }
        } else {
            phase = .recorded
        }
    }

    private func reRecord() {
        footagePath = nil
        recordStart = nil
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

    private func makeClips() {
        phase = .making
        // Keep the raw take in the Library so it can be re-cut later.
        if let footagePath {
            store.addFootage(path: footagePath, scriptId: script.id,
                             title: script.title.isEmpty ? script.hook.text : script.title,
                             seconds: script.targetSeconds)
        }
        Task {
            // Use liveScript so any inline teleprompter edits are reflected in the generated clips.
            await store.makeClips(from: liveScript, formats: Array(selectedFormats),
                                  footagePath: footagePath,
                                  reactSourceURL: reactSourceURL.trimmingCharacters(in: .whitespacesAndNewlines))
            dismiss()
            router.selectedTab = .library
            router.showFilm = false
        }
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

    private var formatPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.sm) {
                ForEach(Catalog.formats) { f in
                    Button {
                        if selectedFormats.contains(f.id) { selectedFormats.remove(f.id) }
                        else { selectedFormats.insert(f.id) }
                    } label: {
                        Group {
                            if selectedFormats.contains(f.id) {
                                Text(f.name).font(AppFont.callout).foregroundStyle(Palette.ink)
                                    .padding(.horizontal, Space.md).padding(.vertical, Space.sm)
                                    .background(Palette.onInk).clipShape(Capsule())
                            } else {
                                Text(f.name).font(AppFont.callout).foregroundStyle(.white)
                                    .padding(.horizontal, Space.md)
                                    .marqueGlassCapsule(height: 36)
                            }
                        }
                    }.buttonStyle(.plain)
                }
            }
        }
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
                guard running, !isEditing, maxScroll > 0 else { return }
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
