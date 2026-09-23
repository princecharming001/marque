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
    @State private var showVersionHistory = false

    private var live: Script { store.scripts.first { $0.id == script.id } ?? script }

    private func commitBodyEdit() {
        let trimmed = bodyDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != live.body else { editingBody = false; return }
        store.commitScriptEdit(scriptId: live.id, body: trimmed)
        editingBody = false
    }

    private func commitHookEdit() {
        let t = hookDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { editingHook = false; return }
        store.commitScriptEdit(scriptId: live.id, hookText: t)
        editingHook = false
    }

    private func commitCTAEdit() {
        let t = ctaDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { editingCTA = false; return }
        store.commitScriptEdit(scriptId: live.id, cta: t)
        editingCTA = false
    }

    var body: some View {
        ScrollView {
            // Stoic journal editor: hook as the title1 prompt, body/CTA as bodyLarge text on
            // the canvas (tap to edit in place), refine chips as capsules underneath.
            VStack(alignment: .leading, spacing: Space.xl) {
                // Hook hero — the editorial centerpiece, now the FIRST and only
                // thing above the script body.
                hookSection

                Rectangle().fill(Palette.hairline).frame(height: 1)

                // DECLUTTER (2026-08): the format chip, the hook-signal chip and the
                // whyPicked rationale all went. They explained the machine's taxonomy
                // to a user whose only job here is to read the script and hit record —
                // three rows of classification between the hook and the body.

                bodySection

                // Refine
                refineSection
            }
            .screenPadding()
            .padding(.top, Space.sm)
            .padding(.bottom, Space.xl)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .scrollDismissesKeyboard(.interactively)
        .safeAreaInset(edge: .bottom) {
            PrimaryButton(title: "Record this script", systemImage: "record.circle", fullWidth: false) { showRecord = true }
                .accessibilityIdentifier("script.record")
                .frame(maxWidth: .infinity)
                .padding(.horizontal, Space.screenH)
                .padding(.top, Space.sm)
                .padding(.bottom, Space.sm)
                .background(Palette.canvas.ignoresSafeArea(edges: .bottom))
                .overlay(alignment: .top) { Rectangle().fill(Palette.hairline).frame(height: 1) }
        }
        .navigationTitle("Script")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Palette.canvas, for: .navigationBar)
        // Same save-for-later as the feed card's bookmark — reading the full script is
        // exactly when you decide you want it, so the action can't live only on Home.
        // Same source of truth (store.readiedScripts), so the two stay in sync.
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text("script.").font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    .accessibilityAddTraits(.isHeader)
            }
            // Only appears once there's somewhere to go back TO — no history yet means
            // no button, rather than a permanently-disabled one cluttering the bar.
            if !live.versionHistory.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    DSIconButton(systemName: "clock.arrow.circlepath") { showVersionHistory = true }
                    .accessibilityLabel("Version history")
                    .accessibilityIdentifier("script.versionHistory")
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                DSIconButton(systemName: store.readiedScripts.contains { $0.script.id == live.id }
                             ? "bookmark.fill" : "bookmark") {
                    if let saved = store.readiedScripts.first(where: { $0.script.id == live.id }) {
                        store.removeReadiedScript(saved)
                    } else {
                        store.readyScript(live, source: .daily)
                    }
                }
                .accessibilityLabel("Save for later")
                .accessibilityIdentifier("script.save")
            }
        }
        .onAppear { router.hideTabBar = true }
        .onDisappear { router.hideTabBar = false }
        .sheet(isPresented: $showHookLab) { HookLabSheet(script: live) }
        .sheet(isPresented: $showVersionHistory) { ScriptVersionHistorySheet(scriptId: live.id) }
        .fullScreenCover(isPresented: $showRecord) { RecordView(script: live) }
    }

    // MARK: Hook section (title1 prompt)

    private var hookSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack {
                Spacer()
                Button(editingHook ? "Done" : "Edit") {
                    if editingHook { commitHookEdit() } else { hookDraft = live.hook.text; editingHook = true }
                }
                .font(AppFont.supporting.weight(.semibold)).foregroundStyle(Palette.textPrimary)
                .frame(minHeight: 44)
                .accessibilityIdentifier("script.editHook")
            }
            if editingHook {
                TextField("Hook", text: $hookDraft, axis: .vertical)
                    .font(AppFont.title1).tracking(-0.3).foregroundStyle(Palette.textPrimary)
                    .padding(Space.md)
                    .background(Palette.surface)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                    .accessibilityIdentifier("script.hookEditor")
            } else {
                Button { showHookLab = true } label: {
                    // "tap to explore" is gone with the section label, so the
                    // affordance rides inline: a small arrow glyph trailing the
                    // last word, which keeps the hook the only thing you read.
                    (Text(live.hook.text)
                     + Text("  ")
                     + Text(Image(systemName: "arrow.up.right"))
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(Palette.textSecondary))
                        .font(AppFont.title1).tracking(-0.3)
                        .foregroundColor(Palette.textPrimary)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .lineSpacing(4)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                }.buttonStyle(PressableStyle(dim: 0.6))
                .accessibilityIdentifier("script.hookButton")
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
                        .font(AppFont.supporting.weight(.semibold)).foregroundStyle(Palette.textPrimary)
                        .frame(minHeight: 44)
                } else {
                    Button("Edit") { bodyDraft = live.body; editingBody = true; bodyFocused = true }
                        .font(AppFont.supporting.weight(.semibold)).foregroundStyle(Palette.textPrimary)
                        .frame(minHeight: 44)
                }
            }
            if editingBody {
                TextEditor(text: $bodyDraft)
                    .font(AppFont.bodyLarge).foregroundStyle(Palette.textPrimary)
                    .lineSpacing(6)
                    .frame(minHeight: 120)
                    .focused($bodyFocused)
                    .scrollContentBackground(.hidden)
                    .padding(Space.sm)
                    .background(Palette.surface)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                    .accessibilityIdentifier("script.bodyEditor")
            } else {
                Text(live.body).font(AppFont.bodyLarge).foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .lineSpacing(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                    .onTapGesture { bodyDraft = live.body; editingBody = true; bodyFocused = true }
            }
            // The CTA used to be set apart by the accent hue; now by weight.
            if editingCTA {
                TextField("Call to action", text: $ctaDraft, axis: .vertical)
                    .font(AppFont.bodyLarge.weight(.semibold)).foregroundStyle(Palette.textPrimary)
                    .padding(Space.md)
                    .background(Palette.surface)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                    .accessibilityIdentifier("script.ctaEditor")
                Button("Done") { commitCTAEdit() }
                    .font(AppFont.supporting.weight(.semibold)).foregroundStyle(Palette.textPrimary)
                    .frame(minHeight: 44)
            } else {
                Text(live.cta).font(AppFont.bodyLarge.weight(.semibold)).foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .lineSpacing(6)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                    .onTapGesture { ctaDraft = live.cta; editingCTA = true }
            }
            // DECLUTTER (2026-08): the "Shot plan" bullets are no longer DISPLAYED —
            // they were direction for a shoot nobody blocks out from this screen.
            // Script.shotPlan itself stays populated: the editor pipeline consumes it.
        }
    }

    // MARK: Refine section

    private var refineSection: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            SectionTitle(text: "Refine")
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: Space.sm) {
                    ForEach(["Shorter", "More contrarian", "Funnier", "More personal"], id: \.self) { label in
                        DSChip(title: label) {
                            applyRefinement(label)
                        }
                        .accessibilityIdentifier("script.steer")
                    }
                }
                .padding(.horizontal, 1)   // keep the chip hairlines from clipping
            }

            // Free-text refine — tell Yunicorn exactly what to change, in your words.
            // Same steer pipeline as the chips; the rewrite lands in place above.
            HStack(alignment: .bottom, spacing: Space.sm) {
                TextField("Tell Yunicorn what to change…", text: $refineDraft, axis: .vertical)
                    .font(AppFont.bodyText)
                    .foregroundStyle(Palette.textPrimary)
                    .lineLimit(1...3)
                    .focused($refineFocused)
                    .padding(.horizontal, Space.md).padding(.vertical, 13)
                    .background(Palette.surface)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                    .onSubmit { sendRefine() }
                    .accessibilityIdentifier("script.refineField")
                Button(action: sendRefine) {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(canSendRefine ? Palette.onInk : Palette.textTertiary)
                        .frame(width: 48, height: 48)
                        .background(Circle().fill(canSendRefine ? Palette.ink : Palette.surfaceSunken))
                        .contentShape(Circle())
                }
                .buttonStyle(PressableStyle(dim: 0.85, scale: 0.92))
                .disabled(!canSendRefine)
                .accessibilityLabel("Send")
                .accessibilityIdentifier("script.refineSend")
            }

            if steering {
                HStack(spacing: Space.sm) {
                    ProgressView().controlSize(.small).tint(Palette.textPrimary)
                    Text("Rewriting…").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                }
            }
            // Quiet log of what's been applied this session — reads as a mini
            // conversation with the script.
            ForEach(Array(appliedRefinements.enumerated()), id: \.offset) { _, instruction in
                HStack(alignment: .top, spacing: Space.sm) {
                    Image(systemName: "checkmark")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Palette.textPrimary)
                        .padding(.top, 3)
                    Text(instruction)
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
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
                VStack(spacing: Space.xl) {
                    VStack(spacing: Space.sm) {
                        Text("Pick your hook").font(AppFont.title1).tracking(-0.3)
                            .foregroundStyle(Palette.textPrimary)
                            .multilineTextAlignment(.center)
                            .accessibilityAddTraits(.isHeader)
                        Text("Different angles on the same idea. Pick the one that sounds most like you.")
                            .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity)
                    if loading {
                        ProgressView().tint(Palette.textPrimary).frame(maxWidth: .infinity).padding(Space.md)
                    } else {
                        // Single-select rows; the hook in use carries the trailing check.
                        DSGroup {
                            ForEach(Array(hooks.enumerated()), id: \.element.id) { i, h in
                                if i > 0 { DSRowDivider() }
                                Button {
                                    store.setHook(h, for: script.id); dismiss()
                                } label: {
                                    HStack(alignment: .center, spacing: Space.md) {
                                        VStack(alignment: .leading, spacing: Space.xs) {
                                            Text(h.text).font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                                                .fixedSize(horizontal: false, vertical: true)
                                                .multilineTextAlignment(.leading)
                                            DSEyebrow(text: h.signal.label)
                                        }
                                        Spacer(minLength: Space.sm)
                                        DSCheckmark(isOn: h.text == script.hook.text)
                                    }
                                    .padding(.horizontal, Space.rowPad)
                                    .padding(.vertical, 14)
                                    .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(DSRowPressStyle())
                                .accessibilityIdentifier("hooklab.pickHook")
                            }
                        }
                    }
                }
                .screenPadding().padding(.top, Space.sm).padding(.bottom, Space.xl)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                }
            }
        }
        .tint(Palette.textPrimary)
        .task {
            hooks = await store.llm.hookLab(brand: store.brand, topic: script.pillarName, memory: store.memory)
            loading = false
        }
    }
}
