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
    // v4 gen-z dial (parity with RecordView): 0 off · 1 subtle · 2 memey · 3 brainrot.
    @State private var memeLevel: Double = 1
    @State private var visibleDemos: Set<String> = []
    @State private var failedDemos: Set<String> = []

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    Text("\(clipCount) clip\(clipCount == 1 ? "" : "s") attached")
                        .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .center)

                    if !styles.isEmpty {
                        VStack(alignment: .leading, spacing: Space.sm) {
                            DSEyebrow(text: "B-roll style, pick a look")
                                .padding(.horizontal, Space.rowPad)
                            ScrollView(.horizontal, showsIndicators: false) {
                                HStack(alignment: .top, spacing: Space.stack) {
                                    ForEach(Array(styles.enumerated()), id: \.element.id) { i, s in
                                        styleCard(s, index: i)
                                    }
                                }
                                .padding(.horizontal, Space.screenH)
                            }
                            .padding(.horizontal, -Space.screenH)
                        }
                    }

                    if selectedStyle == "split_screen" {
                        VStack(alignment: .leading, spacing: Space.sm) {
                            DSEyebrow(text: "What are you reacting to?")
                                .padding(.horizontal, Space.rowPad)
                            TextField("", text: $reactSourceURL,
                                      prompt: Text("Paste a video link").foregroundStyle(Palette.textTertiary))
                                .textFieldStyle(.plain)
                                .tint(Palette.textPrimary)
                                .marqueField()
                                .accessibilityIdentifier("chatEdit.reactSource")
                        }
                    }

                    VStack(alignment: .leading, spacing: Space.sm) {
                        DSGroup {
                            DSToggleRow(title: "B-roll cutaways", isOn: $toggles.broll)
                            DSRowDivider()
                            DSToggleRow(title: "Punch-ins for emphasis", isOn: $toggles.punchIns)
                            DSRowDivider()
                            DSToggleRow(title: "Background music", isOn: $toggles.music)
                            if toggles.broll {
                                DSRowDivider()
                                VStack(alignment: .leading, spacing: Space.sm) {
                                    HStack(alignment: .firstTextBaseline) {
                                        DSEyebrow(text: "Meme energy")
                                        Spacer(minLength: Space.md)
                                        Text(["Off", "Subtle", "Memey", "Brainrot"][Int(memeLevel)])
                                            .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                    }
                                    Slider(value: $memeLevel, in: 0...3, step: 1)
                                        .tint(Palette.textPrimary)
                                        .accessibilityIdentifier("chatEdit.memeLevel")
                                }
                                .padding(.horizontal, Space.rowPad)
                                .padding(.vertical, Space.md)
                            }
                        }
                    }

                    VStack(alignment: .leading, spacing: Space.sm) {
                        DSEyebrow(text: "Anything specific?").padding(.horizontal, Space.rowPad)
                        TextField("", text: $instruction,
                                  prompt: Text("e.g. keep it under 30s, punchy").foregroundStyle(Palette.textTertiary),
                                  axis: .vertical)
                            .textFieldStyle(.plain).font(AppFont.bodyText)
                            .foregroundStyle(Palette.textPrimary)
                            .tint(Palette.textPrimary)
                            .lineLimit(1...5)
                            .padding(.horizontal, Space.rowPad)
                            .padding(.vertical, 15)
                            .frame(minHeight: 52)
                            .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                                .fill(Palette.surface))
                            .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                                .strokeBorder(Palette.hairline, lineWidth: 1))
                            .accessibilityIdentifier("chatEdit.instruction")
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("edit these clips.").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("edit these clips.").font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                        .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                }
            }
            .safeAreaInset(edge: .bottom) {
                PrimaryButton(title: "Create edit", systemImage: "wand.and.stars", fullWidth: false) { submit() }
                    .accessibilityIdentifier("chatEdit.create")
                    .frame(maxWidth: .infinity)
                    .padding(.horizontal, Space.screenH).padding(.top, Space.sm).padding(.bottom, Space.sm)
                    .background(Palette.canvas.ignoresSafeArea(edges: .bottom))
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
            withAnimation(Motion.quick) { selectedStyle = s.id }
        } label: {
            VStack(alignment: .leading, spacing: Space.sm) {
                ZStack(alignment: .topTrailing) {
                    if playable, visibleDemos.contains(s.id), let url = URL(string: s.videoURL) {
                        FailableVideoPlayer(url: url, muted: true, showsControls: false,
                                            onFailure: { failedDemos.insert(s.id) })
                            .frame(width: 112, height: 140)
                            .allowsHitTesting(false)
                    } else {
                        Rectangle().fill(Palette.surfaceSunken)
                            .frame(width: 112, height: 140)
                            .overlay(Image(systemName: "film")
                                .font(.system(size: 20, weight: .regular))
                                .foregroundStyle(Palette.textSecondary))
                    }
                    if selected {
                        DSCheckmark(isOn: true, size: 24)
                            .background(Circle().fill(Palette.surface).padding(-2))
                            .padding(Space.sm)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                    .strokeBorder(selected ? Palette.textPrimary : Palette.hairline, lineWidth: selected ? 2 : 1))
                .onAppear { visibleDemos.insert(s.id) }
                .onDisappear { visibleDemos.remove(s.id) }
                VStack(alignment: .leading, spacing: 2) {
                    Text(s.label).font(AppFont.caption.weight(.semibold)).foregroundStyle(Palette.textPrimary)
                        .lineLimit(1)
                    Text(s.blurb).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        .lineLimit(2, reservesSpace: true).multilineTextAlignment(.leading)
                }
                .padding(.horizontal, 2)
            }
            .frame(width: 112)
        }
        .buttonStyle(PressableStyle(dim: 0.85))
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("chatEdit.style.\(index)")
    }

    /// Maps the picked style to the backend config (same contract as RecordView.brollConfig):
    /// cutaway/panel/card force the b-roll mode AND send broll_coverage:"full" (the opt-in that
    /// arms the density floor + stock fallback — without it chat-edits got far fewer, action-only
    /// inserts than the record flow from identical footage); green_screen/split_screen override
    /// the job style; and a plain b-roll toggle still opts in via coverage.
    private func styleConfig() -> [String: String]? {
        // v4: the meme dial rides along whenever b-roll is in play.
        let meme = ["meme_intensity": String(Int(memeLevel))]
        switch selectedStyle {
        case "cutaway": return meme.merging(["broll_mode": "full",  "broll_coverage": "full"]) { a, _ in a }
        case "smart":   return meme.merging(["broll_mode": "smart", "broll_coverage": "full"]) { a, _ in a }
        case "panel":   return meme.merging(["broll_mode": "panel", "broll_coverage": "full"]) { a, _ in a }
        case "card":    return meme.merging(["broll_mode": "card",  "broll_coverage": "full"]) { a, _ in a }
        case "green_screen", "split_screen": return meme.merging(["composition_style": selectedStyle]) { a, _ in a }
        default: return toggles.broll ? meme.merging(["broll_coverage": "full"]) { a, _ in a } : nil
        }
    }

    private func submit() {
        let editFormat = toggles.broll ? EditFormat.talkingHeadBroll.rawValue : EditFormat.talkingHead.rawValue
        onSubmit(styleConfig(), toggles, editFormat, instruction,
                 reactSourceURL.trimmingCharacters(in: .whitespacesAndNewlines))
        dismiss()
    }
}
