import SwiftUI

struct ScriptReaderView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    let script: Script
    @State private var showHookLab = false
    @State private var showRecord = false
    @State private var steering = false
    @State private var refineDraft = ""         // free-text refine ("chat with the script")
    @State private var appliedRefinements: [String] = []
    @FocusState private var refineFocused: Bool
    @State private var editingBody = false
    @State private var bodyDraft = ""
    @State private var editingHook = false
    @State private var hookDraft = ""
    @State private var editingCTA = false
    @State private var ctaDraft = ""
    @FocusState private var bodyFocused: Bool

    private var live: Script { store.scripts.first { $0.id == script.id } ?? script }

    private func commitBodyEdit() {
        let trimmed = bodyDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != live.body else { editingBody = false; return }
        if let idx = store.scripts.firstIndex(where: { $0.id == live.id }) {
            store.scripts[idx].body = trimmed
            store.save()
        }
        editingBody = false
    }

    private func commitHookEdit() {
        let t = hookDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, let idx = store.scripts.firstIndex(where: { $0.id == live.id }) else { editingHook = false; return }
        store.scripts[idx].hook.text = t; store.save(); editingHook = false
    }

    private func commitCTAEdit() {
        let t = ctaDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, let idx = store.scripts.firstIndex(where: { $0.id == live.id }) else { editingCTA = false; return }
        store.scripts[idx].cta = t; store.save(); editingCTA = false
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                // Hook hero — big Fraunces serif, the editorial centerpiece
                hookSection

                MarqueHairline()

                // Format chip (H-05: no swap — the server infers style from the take)
                HStack {
                    FormatTag(formatId: live.formatId)
                    Spacer()
                }

                // UX-G2: WHY the feed picked this script — the bandit's honest reason.
                if !live.whyPicked.isEmpty {
                    Text(live.whyPicked)
                        .font(AppFont.micro).tracking(0.2)
                        .foregroundStyle(Palette.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                // Body + shot plan
                bodySection

                // Refine
                refineSection
            }
            .screenPadding()
            .padding(.vertical, Space.lg)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .safeAreaInset(edge: .bottom) {
            PrimaryButton(title: "Record this script", systemImage: "record.circle") { showRecord = true }
                .accessibilityIdentifier("script.record")
                .padding(.horizontal, Space.screenH)
                .padding(.vertical, Space.sm)
                .background(.ultraThinMaterial)
        }
        .navigationTitle("Script")
        .navigationBarTitleDisplayMode(.inline)
        // Same save-for-later as the feed card's bookmark — reading the full script is
        // exactly when you decide you want it, so the action can't live only on Home.
        // Same source of truth (store.readiedScripts), so the two stay in sync.
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    if let saved = store.readiedScripts.first(where: { $0.script.id == live.id }) {
                        store.removeReadiedScript(saved)
                    } else {
                        store.readyScript(live, source: .daily)
                    }
                } label: {
                    Image(systemName: store.readiedScripts.contains { $0.script.id == live.id }
                          ? "bookmark.fill" : "bookmark")
                }
                .tint(Palette.accent)
                .accessibilityLabel("Save for later")
                .accessibilityIdentifier("script.save")
            }
        }
        .onAppear { router.hideTabBar = true }
        .onDisappear { router.hideTabBar = false }
        .sheet(isPresented: $showHookLab) { HookLabSheet(script: live) }
        .fullScreenCover(isPresented: $showRecord) { RecordView(script: live) }
    }

    // MARK: Hook section (serif hero)

    private var hookSection: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack {
                SectionLabel(text: editingHook ? "Hook" : "Hook · tap to explore", accent: Palette.accent)
                Spacer()
                Button(editingHook ? "Done" : "Edit") {
                    if editingHook { commitHookEdit() } else { hookDraft = live.hook.text; editingHook = true }
                }
                .font(AppFont.callout).foregroundStyle(editingHook ? Palette.accent : Palette.goldDeep)
                .accessibilityIdentifier("script.editHook")
            }
            if editingHook {
                TextField("Hook", text: $hookDraft, axis: .vertical)
                    .font(Typeface.display(32, .semibold)).foregroundStyle(Palette.textPrimary)
                    .padding(Space.md)
                    .background(Palette.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    .accessibilityIdentifier("script.hookEditor")
            } else {
                Button { showHookLab = true } label: {
                    Text(live.hook.text)
                        .font(Typeface.display(32, .semibold)).tracking(-0.5)
                        .foregroundStyle(Palette.textPrimary)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .lineSpacing(3)
                }.buttonStyle(.plain)
                .accessibilityIdentifier("script.hookButton")
            }
            HStack(spacing: Space.sm) {
                Chip(text: live.hook.signal.label)
            }
        }
    }

    // MARK: Body section

    private var bodySection: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack {
                SectionTitle(text: "Script")
                Spacer()
                if editingBody {
                    Button("Done") { commitBodyEdit() }
                        .font(AppFont.callout).foregroundStyle(Palette.accent)
                } else {
                    Button("Edit") { bodyDraft = live.body; editingBody = true; bodyFocused = true }
                        .font(AppFont.callout).foregroundStyle(Palette.goldDeep)
                }
            }
            if editingBody {
                TextEditor(text: $bodyDraft)
                    .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                    .frame(minHeight: 120)
                    .focused($bodyFocused)
                    .scrollContentBackground(.hidden)
                    .background(Palette.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
                    .accessibilityIdentifier("script.bodyEditor")
            } else {
                Text(live.body).font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .lineSpacing(7)
                    .onTapGesture { bodyDraft = live.body; editingBody = true; bodyFocused = true }
            }
            if editingCTA {
                TextField("Call to action", text: $ctaDraft, axis: .vertical)
                    .font(AppFont.bodyL).foregroundStyle(Palette.goldDeep)
                    .padding(Space.sm)
                    .background(Palette.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
                    .accessibilityIdentifier("script.ctaEditor")
                Button("Done") { commitCTAEdit() }
                    .font(AppFont.callout).foregroundStyle(Palette.accent)
            } else {
                Text(live.cta).font(AppFont.bodyL).foregroundStyle(Palette.goldDeep)
                    .onTapGesture { ctaDraft = live.cta; editingCTA = true }
            }
            Divider().background(Palette.hairline)
            SectionTitle(text: "Shot plan")
            ForEach(Array(live.shotPlan.enumerated()), id: \.offset) { _, s in
                HStack(alignment: .top, spacing: Space.sm) {
                    Circle().fill(Palette.gold).frame(width: 5, height: 5).padding(.top, 7)
                    Text(s).font(AppFont.body).foregroundStyle(Palette.textSecondary)
                }
            }
        }
        .marqueCard()
    }

    // MARK: Refine section

    private var refineSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            SectionTitle(text: "Refine")
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Space.sm) {
                    ForEach(["Shorter", "More contrarian", "Funnier", "More personal"], id: \.self) { label in
                        Button {
                            applyRefinement(label)
                        } label: { Chip(text: label) }.buttonStyle(.plain)
                        .accessibilityIdentifier("script.steer")
                    }
                }
            }

            // Free-text refine — tell Yunicorn exactly what to change, in your words.
            // Same steer pipeline as the chips; the rewrite lands in place above.
            HStack(alignment: .bottom, spacing: Space.sm) {
                TextField("Tell Yunicorn what to change…", text: $refineDraft, axis: .vertical)
                    .font(AppFont.body)
                    .lineLimit(1...3)
                    .focused($refineFocused)
                    .padding(.horizontal, Space.md).padding(.vertical, 11)
                    .background(Palette.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                    .onSubmit { sendRefine() }
                    .accessibilityIdentifier("script.refineField")
                Button(action: sendRefine) {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Palette.onInk)
                        .frame(width: 38, height: 38)
                        .background(Circle().fill(canSendRefine ? Palette.ink : Palette.textTertiary.opacity(0.4)))
                }
                .buttonStyle(PressableStyle())
                .disabled(!canSendRefine)
                .accessibilityIdentifier("script.refineSend")
            }
            .padding(.top, Space.xs)

            if steering {
                HStack(spacing: Space.sm) {
                    ProgressView().controlSize(.small).tint(Palette.gold)
                    Text("Rewriting…").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                }
            }
            // Quiet log of what's been applied this session — reads as a mini
            // conversation with the script.
            ForEach(Array(appliedRefinements.enumerated()), id: \.offset) { _, instruction in
                HStack(alignment: .top, spacing: Space.sm) {
                    Image(systemName: "checkmark")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Palette.textTertiary)
                        .padding(.top, 3)
                    Text(instruction)
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var canSendRefine: Bool {
        !refineDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !steering
    }

    private func sendRefine() {
        let text = refineDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !steering else { return }
        refineDraft = ""
        refineFocused = false
        applyRefinement(text)
    }

    /// One shared path for chips + free text: steer the script, log the instruction.
    private func applyRefinement(_ instruction: String) {
        guard !steering else { return }
        steering = true
        Task {
            await store.steer(live, instruction: instruction)
            appliedRefinements.append(instruction)
            steering = false
        }
    }
}

// MARK: - Hook Lab (nested via progressive disclosure)

struct HookLabSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let script: Script
    @State private var hooks: [Hook] = []
    @State private var loading = true

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    Text("Pick your hook").font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
                    Text("Different angles on the same idea — pick the one that sounds most like you.")
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    if loading {
                        ProgressView().tint(Palette.gold).frame(maxWidth: .infinity).padding()
                    } else {
                        ForEach(hooks) { h in
                            Button {
                                store.setHook(h, for: script.id); dismiss()
                            } label: {
                                VStack(alignment: .leading, spacing: Space.sm) {
                                    Text(h.text).font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                                        .fixedSize(horizontal: false, vertical: true)
                                        .multilineTextAlignment(.leading)
                                    Chip(text: h.signal.label)
                                }
                                .marqueCard()
                            }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("hooklab.pickHook")
                        }
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
        .task {
            hooks = await store.llm.hookLab(brand: store.brand, topic: script.pillarName, memory: store.memory)
            loading = false
        }
    }
}

