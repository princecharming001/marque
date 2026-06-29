import SwiftUI

// Mock record screen: a teleprompter + simulated capture so the loop runs end-to-end.
// Real AVFoundation camera + auto-scroll teleprompter is the next milestone (see 05-screens-produce.md).

struct RecordView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(\.dismiss) private var dismiss
    let script: Script

    @State private var phase: Phase = .ready
    @State private var scroll = false
    @State private var selectedFormats: Set<String>

    enum Phase { case ready, recording, recorded, making }

    init(script: Script) {
        self.script = script
        _selectedFormats = State(initialValue: Set([script.formatId, "broll-hook", "faceless"]))
    }

    var body: some View {
        ZStack {
            Palette.night.ignoresSafeArea()
            VStack(spacing: Space.lg) {
                topBar
                Spacer(minLength: 0)
                teleprompter
                Spacer(minLength: 0)
                controls
            }
            .padding(Space.lg)
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

    private var teleprompter: some View {
        // Pure-white teleprompter text on dark (the documented contrast exception).
        ScrollView {
            VStack(alignment: .leading, spacing: Space.lg) {
                Text(script.hook.text).font(Typeface.display(28, .semibold)).foregroundStyle(.white)
                Text(script.body).font(Typeface.body(22)).foregroundStyle(.white.opacity(0.92))
                Text(script.cta).font(Typeface.body(22, .semibold)).foregroundStyle(Palette.gold)
            }
            .padding(.vertical, scroll ? 0 : 40)
            .offset(y: scroll ? -60 : 0)
            .animation(.linear(duration: Double(script.targetSeconds)), value: scroll)
        }
        .frame(maxHeight: 320)
        .mask(LinearGradient(colors: [.clear, .black, .black, .clear], startPoint: .top, endPoint: .bottom))
    }

    private var controls: some View {
        VStack(spacing: Space.lg) {
            switch phase {
            case .ready:
                Text("Read it once. We'll cut the rest.")
                    .font(AppFont.body).foregroundStyle(.white.opacity(0.7))
                recordButton {
                    phase = .recording; scroll = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2.4) { phase = .recorded }
                }
            case .recording:
                Text("Recording…").font(AppFont.body).foregroundStyle(Palette.gold)
                recordButton(active: true) {}
            case .recorded:
                Text("Choose the formats to cut into").font(AppFont.callout).foregroundStyle(.white.opacity(0.8))
                formatPicker
                Button {
                    phase = .making
                    Task {
                        await store.makeClips(from: script, formats: Array(selectedFormats))
                        dismiss(); router.selectedTab = .library
                    }
                } label: {
                    Text("Make my clips")
                        .font(AppFont.headline).foregroundStyle(Palette.night)
                        .frame(maxWidth: .infinity).padding(.vertical, Space.lg)
                        .background(Palette.gold).clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("record.makeClips")
            case .making:
                ProgressView().tint(Palette.gold)
                Text("Cutting your clips…").font(AppFont.body).foregroundStyle(.white.opacity(0.7))
            }
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
                            .foregroundStyle(selectedFormats.contains(f.id) ? Palette.night : .white)
                            .padding(.horizontal, Space.md).padding(.vertical, Space.sm)
                            .background(selectedFormats.contains(f.id) ? Palette.gold : Color.white.opacity(0.12))
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
