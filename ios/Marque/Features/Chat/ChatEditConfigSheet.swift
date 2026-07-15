import SwiftUI
import PhotosUI

// Chat-side edit configuration — brings the CHAT "edit my clips" flow to parity with the
// record flow. Before the upload→edit pipeline runs, the creator picks the same things the
// record screen offers: a composition style (cutaway / panel / floating card / green
// screen / split screen), the b-roll / punch-in / music toggles, an optional instruction,
// and (for split screen) the clip they're reacting to. The choices are handed back as the
// exact config dict + toggles + edit format the backend already consumes.
struct ChatEditConfigSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    let clipCount: Int
    let initialInstruction: String
    /// (config, toggles, editFormat, instruction, reactSourceURL)
    let onSubmit: ([String: String]?, EditToggles, String, String, String) -> Void

    @State private var styles: [BrollStyleOption] = []
    @State private var selectedStyle = "cutaway"
    @State private var toggles = EditToggles(broll: true, punchIns: true, music: false)
    @State private var instruction = ""
    @State private var reactSourceURL = ""
    @State private var visibleDemos: Set<String> = []
    @State private var failedDemos: Set<String> = []

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    Text("\(clipCount) clip\(clipCount == 1 ? "" : "s") attached")
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)

                    if !styles.isEmpty {
                        VStack(alignment: .leading, spacing: Space.xs) {
                            SectionLabel(text: "B-roll style — pick a look")
                            ScrollView(.horizontal, showsIndicators: false) {
                                HStack(spacing: Space.sm) {
                                    ForEach(Array(styles.enumerated()), id: \.element.id) { i, s in
                                        styleCard(s, index: i)
                                    }
                                }
                            }
                        }
                    }

                    if selectedStyle == "split_screen" {
                        VStack(alignment: .leading, spacing: Space.xs) {
                            SectionLabel(text: "What are you reacting to?")
                            TextField("", text: $reactSourceURL,
                                      prompt: Text("Paste a video link").foregroundStyle(.white.opacity(0.5)))
                                .textFieldStyle(.plain).font(AppFont.body)
                                .foregroundStyle(Palette.textPrimary)
                                .padding(Space.md)
                                .background(Palette.surfaceRaised)
                                .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                                .accessibilityIdentifier("chatEdit.reactSource")
                        }
                    }

                    VStack(spacing: Space.xs) {
                        MarqueToggleRow(title: "B-roll cutaways", subtitle: nil, isOn: $toggles.broll)
                        MarqueToggleRow(title: "Punch-ins for emphasis", subtitle: nil, isOn: $toggles.punchIns)
                        MarqueToggleRow(title: "Background music", subtitle: nil, isOn: $toggles.music)
                    }

                    VStack(alignment: .leading, spacing: Space.xs) {
                        SectionLabel(text: "Anything specific?")
                        TextField("", text: $instruction,
                                  prompt: Text("e.g. keep it under 30s, punchy").foregroundStyle(.white.opacity(0.5)),
                                  axis: .vertical)
                            .textFieldStyle(.plain).font(AppFont.body)
                            .foregroundStyle(Palette.textPrimary)
                            .padding(Space.md)
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                            .accessibilityIdentifier("chatEdit.instruction")
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Edit these clips").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } } }
            .safeAreaInset(edge: .bottom) {
                PrimaryButton(title: "Create edit", systemImage: "wand.and.stars") { submit() }
                    .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
                    .background(.ultraThinMaterial)
                    .accessibilityIdentifier("chatEdit.create")
            }
            .task {
                instruction = initialInstruction
                styles = await store.backend.brollStyles(niche: store.brand.niche)
            }
        }
    }

    private func styleCard(_ s: BrollStyleOption, index: Int) -> some View {
        let selected = selectedStyle == s.id
        let playable = !s.videoURL.isEmpty && !failedDemos.contains(s.id)
        return Button {
            withAnimation(.easeOut(duration: 0.15)) { selectedStyle = s.id }
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                ZStack(alignment: .topTrailing) {
                    if playable, visibleDemos.contains(s.id), let url = URL(string: s.videoURL) {
                        FailableVideoPlayer(url: url, muted: true, showsControls: false,
                                            onFailure: { failedDemos.insert(s.id) })
                            .frame(width: 112, height: 140)
                            .allowsHitTesting(false)
                    } else {
                        Rectangle().fill(Color.white.opacity(0.08))
                            .frame(width: 112, height: 140)
                            .overlay(Image(systemName: "film").foregroundStyle(.white.opacity(0.3)))
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
                .onAppear { visibleDemos.insert(s.id) }
                .onDisappear { visibleDemos.remove(s.id) }
                Text(s.label).font(.system(size: 11, weight: .bold)).foregroundStyle(Palette.textPrimary).lineLimit(1)
                Text(s.blurb).font(.system(size: 10)).foregroundStyle(Palette.textSecondary)
                    .lineLimit(2, reservesSpace: true).multilineTextAlignment(.leading)
            }
            .frame(width: 112).padding(4)
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(selected ? Palette.accent : .clear, lineWidth: 2))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("chatEdit.style.\(index)")
    }

    /// Maps the picked style to the backend config (same contract as RecordView): cutaway/
    /// panel/card force the b-roll mode; green_screen/split_screen override the job style.
    private func styleConfig() -> [String: String]? {
        switch selectedStyle {
        case "cutaway": return ["broll_mode": "full"]
        case "panel":   return ["broll_mode": "panel"]
        case "card":    return ["broll_mode": "card"]
        case "green_screen", "split_screen": return ["composition_style": selectedStyle]
        default: return nil
        }
    }

    private func submit() {
        let editFormat = toggles.broll ? EditFormat.talkingHeadBroll.rawValue : EditFormat.talkingHead.rawValue
        onSubmit(styleConfig(), toggles, editFormat, instruction,
                 reactSourceURL.trimmingCharacters(in: .whitespacesAndNewlines))
        dismiss()
    }
}
