import SwiftUI
import PhotosUI

// Real camera on device (CameraModel/AVFoundation); simulated capture in the Simulator
// (no camera hardware) so the batch loop stays end-to-end testable. (05-screens-produce.md)

struct RecordView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(\.dismiss) private var dismiss
    @StateObject private var camera = CameraModel()
    let script: Script

    @State private var phase: Phase = .ready
    @State private var promptRunning = false
    @State private var speed: Double = 1.0
    @State private var restartToken = 0
    @State private var footagePath: String?
    @State private var pickedItem: PhotosPickerItem?
    @State private var selectedFormats: Set<String>
    // Mutable copy so inline teleprompter edits flow through without modifying the store mid-take.
    @State private var liveScript: Script

    enum Phase { case ready, recording, recorded, making }

    init(script: Script) {
        self.script = script
        _liveScript = State(initialValue: script)
        _selectedFormats = State(initialValue: Set([script.formatId, "broll-hook", "faceless"]))
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
            Button { dismiss() } label: { Image(systemName: "xmark").foregroundStyle(.white) }
            Spacer()
            FormatTag(formatId: script.formatId).colorScheme(.dark)
            Spacer()
            Image(systemName: "camera.rotate").foregroundStyle(.white.opacity(0.7))
        }
    }

    private var controls: some View {
        VStack(spacing: Space.lg) {
            switch phase {
            case .ready:
                Text(camera.status == .unavailable ? "No camera in the Simulator — tap to simulate a take." : "Read it once. We'll cut the rest.")
                    .font(AppFont.body).foregroundStyle(.white.opacity(0.7)).multilineTextAlignment(.center)
                speedControl
                recordButton { startRecording() }
                PhotosPicker(selection: $pickedItem, matching: .videos) {
                    Label("Upload existing video", systemImage: "square.and.arrow.up")
                        .font(AppFont.callout).foregroundStyle(.white.opacity(0.85))
                }
                .accessibilityIdentifier("record.upload")
            case .recording:
                Text("Recording…").font(AppFont.body).foregroundStyle(Palette.accent)
                speedControl
                HStack(spacing: Space.xl) {
                    Button { promptRunning.toggle() } label: {
                        Image(systemName: promptRunning ? "pause.fill" : "play.fill")
                            .font(.system(size: 18)).foregroundStyle(.white)
                            .frame(width: 52, height: 52).background(Color.white.opacity(0.15)).clipShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("record.pausePrompt")
                    recordButton(active: true) { stopRecording() }
                }
            case .recorded:
                Text("Choose the formats to cut into").font(AppFont.callout).foregroundStyle(.white.opacity(0.8))
                formatPicker
                Button { makeClips() } label: {
                    Text("Make my clips")
                        .font(AppFont.headline).foregroundStyle(Palette.ink)
                        .frame(maxWidth: .infinity).padding(.vertical, Space.lg)
                        .background(Palette.onInk).clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("record.makeClips")
            case .making:
                ProgressView().tint(Palette.accent)
                Text("Cutting your clips…").font(AppFont.body).foregroundStyle(.white.opacity(0.7))
            }
        }
    }

    private var speedControl: some View {
        HStack(spacing: Space.sm) {
            ForEach([("Slow", 0.6), ("Normal", 1.0), ("Fast", 1.5)], id: \.0) { label, val in
                Button { speed = val } label: {
                    Text(label).font(AppFont.caption)
                        .foregroundStyle(speed == val ? Palette.ink : .white)
                        .padding(.horizontal, Space.md).padding(.vertical, 7)
                        .background(speed == val ? Palette.onInk : Color.white.opacity(0.12))
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
            }
        }
    }

    // MARK: actions

    private func startRecording() {
        phase = .recording
        restartToken += 1
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
            await store.makeClips(from: liveScript, formats: Array(selectedFormats), footagePath: footagePath)
            dismiss()
            router.selectedTab = .library
            router.showCreate = false
        }
    }

    private var formatPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: Space.sm) {
                ForEach(Catalog.formats) { f in
                    Button {
                        if selectedFormats.contains(f.id) { selectedFormats.remove(f.id) }
                        else { selectedFormats.insert(f.id) }
                    } label: {
                        Text(f.name)
                            .font(AppFont.callout)
                            .foregroundStyle(selectedFormats.contains(f.id) ? Palette.ink : .white)
                            .padding(.horizontal, Space.md).padding(.vertical, Space.sm)
                            .background(selectedFormats.contains(f.id) ? Palette.onInk : Color.white.opacity(0.12))
                            .clipShape(Capsule())
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
