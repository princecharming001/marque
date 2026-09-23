import SwiftUI

// The center Film button's destination: pick a readied script (saved from the
// feed / chat / a mimic), continue a draft, or write your own — then into the
// teleprompter. Drafts resume even when their script was deleted (rebuilt from
// the draft clip); submitting for editing dequeues the script + notifies on ready.
struct FilmView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var customScript = ""
    @State private var showCustomEditor = false
    @State private var showSettings = false
    @State private var showReorder = false          // W4
    @State private var showArchived = false
    @State private var showFreestyle = false        // I-4
    @State private var draftPendingDelete: Clip? = nil   // build 52: delete-draft confirm

    private var drafts: [Clip] { store.clips.filter { $0.status == .draft } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                // Stoic "Today" header inside the cover: eyebrow + lowercase title.
                VStack(alignment: .leading, spacing: Space.xs) {
                    DSEyebrow(text: "READY TO FILM")
                    Text("Film").font(AppFont.title1).tracking(-0.3).foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                }

                // I-4: film without a script — just talk, the editor finds the cut.
                // The one hero card on the screen; the whole card is the button.
                Button { showFreestyle = true } label: {
                    DSHeroCard {
                        VStack(spacing: Space.md) {
                            Image(systemName: "mic")
                                .font(.system(size: 22, weight: .regular))
                                .foregroundStyle(Palette.onNight)
                                .frame(width: 52, height: 52)
                                .overlay(Circle().strokeBorder(Color.white.opacity(0.28), lineWidth: 1))
                            Text("No script. Just talk, the editor finds the cut.")
                                .font(AppFont.title2).tracking(-0.2)
                                .foregroundStyle(Palette.onNight)
                                .multilineTextAlignment(.center)
                                .fixedSize(horizontal: false, vertical: true)
                            Text("Freestyle")
                                .font(AppFont.headline)
                                .foregroundStyle(Palette.night)
                                .padding(.horizontal, 32)
                                .frame(minWidth: 150)
                                .frame(height: 48)
                                .background(Capsule().fill(Palette.onNight))
                                .padding(.top, Space.xs)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, Space.sm)
                    }
                    .contentShape(RoundedRectangle(cornerRadius: Radius.hero, style: .continuous))
                }
                .buttonStyle(PressableStyle(dim: 0.9, scale: 0.97))
                .accessibilityIdentifier("film.freestyle")

                // Continue a draft
                if !drafts.isEmpty {
                    VStack(alignment: .leading, spacing: Space.sm) {
                        DSEyebrow(text: "Continue a draft").padding(.horizontal, Space.rowPad)
                        VStack(spacing: Space.groupGap) {
                            ForEach(drafts) { d in
                                NavigationLink(value: resolvedScript(for: d)) {
                                    draftRow(d)
                                }
                                .buttonStyle(.plain)
                                .accessibilityIdentifier("film.draft")
                                .contextMenu {
                                    Button(role: .destructive) { draftPendingDelete = d } label: {
                                        Label("Delete draft", systemImage: "trash")
                                    }
                                    .accessibilityIdentifier("film.draft.delete")
                                }
                                // Audit (build 53, B2): removed a dead `.swipeActions` here —
                                // swipe-to-delete only works on rows inside a `List`, but this is a
                                // ForEach in a ScrollView/VStack, so it never fired. Long-press
                                // (contextMenu) is the working delete affordance; leaving the
                                // swipe modifier in implied a gesture that did nothing.
                            }
                        }
                    }
                }

                // Readied scripts (the film queue) — W4: reorder / archive / delete / sections
                VStack(alignment: .leading, spacing: Space.sm) {
                    HStack(alignment: .center, spacing: Space.sm) {
                        DSEyebrow(text: "Your queue")
                        Text("\(store.queuedScripts.count)")
                            .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        Spacer(minLength: Space.sm)
                        if store.queuedScripts.count > 1 {
                            Button { showReorder = true } label: {
                                Label("Reorder", systemImage: "arrow.up.arrow.down")
                                    .font(AppFont.supporting)
                                    .foregroundStyle(Palette.textPrimary)
                                    .frame(minHeight: 44)
                                    .contentShape(Rectangle())
                            }
                            .buttonStyle(PressableStyle(dim: 0.5))
                            .accessibilityIdentifier("film.reorder")
                        }
                    }
                    .padding(.leading, Space.rowPad)
                    .frame(minHeight: 44)
                    if store.queuedScripts.isEmpty {
                        EmptyStateView(icon: "bookmark", title: "Nothing queued yet",
                                       message: "Save scripts from your Home picks, a mimic, or chat, they land here ready to film.")
                            .padding(.horizontal, Space.cardPad)
                            .background(
                                RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                                    .strokeBorder(Palette.hairline, lineWidth: 1))
                    } else {
                        VStack(spacing: Space.stack) {
                            ForEach(store.queuedScripts) { saved in
                                NavigationLink(value: saved.script) { readiedRow(saved) }
                                .buttonStyle(.plain)
                                .accessibilityIdentifier("film.readied")
                                .contextMenu {
                                    Button { store.archiveReadied(saved) } label: { Label("Archive", systemImage: "archivebox") }
                                        .accessibilityIdentifier("film.archive")
                                    Button(role: .destructive) { store.removeReadiedScript(saved) } label: {
                                        Label("Remove from queue", systemImage: "bookmark.slash")
                                    }
                                }
                            }
                        }
                    }
                }

                // Archived section
                if !store.archivedReadied.isEmpty {
                    VStack(alignment: .leading, spacing: Space.sm) {
                        DisclosureGroup(isExpanded: $showArchived) {
                            VStack(spacing: Space.stack) {
                                ForEach(store.archivedReadied) { saved in
                                    readiedRow(saved).opacity(0.7)
                                        .contextMenu {
                                            Button { store.unarchiveReadied(saved) } label: { Label("Restore", systemImage: "tray.and.arrow.up") }
                                                .accessibilityIdentifier("film.restore")
                                            Button(role: .destructive) { store.removeReadiedScript(saved) } label: {
                                                Label("Remove", systemImage: "trash")
                                            }
                                        }
                                }
                            }
                            .padding(.top, Space.sm)
                        } label: {
                            DSEyebrow(text: "Archived (\(store.archivedReadied.count))")
                                .frame(minHeight: 44, alignment: .leading)
                        }
                        .tint(Palette.textPrimary)
                        .padding(.leading, Space.rowPad)
                        .accessibilityIdentifier("film.archivedSection")
                    }
                }

                // Write your own: outline capsule + the edit-prefs text link, centered.
                VStack(spacing: Space.md) {
                    DSEyebrow(text: "Or write your own")
                    Button { showCustomEditor = true } label: {
                        HStack(spacing: Space.sm) {
                            Image(systemName: "square.and.pencil")
                                .font(.system(size: 16, weight: .regular))
                            Text("Paste or write a script")
                        }
                    }
                    .buttonStyle(.dsOutline)
                    .accessibilityIdentifier("film.customScript")
                    Button { showSettings = true } label: {
                        Text(editPrefsCaption)
                            .font(AppFont.caption)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(PressableStyle(dim: 0.6))
                    .accessibilityIdentifier("film.changeEditPrefs")
                }
                .frame(maxWidth: .infinity)
                .padding(.top, Space.sm)
            }
            .screenPadding()
            .padding(.top, Space.sm)
            .padding(.bottom, Space.huge)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            // Top-right: matches iOS modal-dismiss convention (fullScreenCover has no
            // swipe-to-dismiss, so this button is the only way out — keep it discoverable).
            ToolbarItem(placement: .topBarTrailing) {
                Button { router.showFilm = false } label: {
                    Image(systemName: "xmark").font(.system(size: 18, weight: .regular)).foregroundStyle(Palette.textPrimary)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("Close")
                .accessibilityIdentifier("film.close")
            }
        }
        .toolbarBackground(Palette.canvas, for: .navigationBar)
        .navigationDestination(for: Script.self) { ScriptReaderView(script: $0) }
        .sheet(isPresented: $showCustomEditor) { CustomScriptSheet() }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $showReorder) { QueueReorderSheet() }
        .fullScreenCover(isPresented: $showFreestyle) { RecordView(script: nil) }   // I-4
        .onAppear { consumePendingFilmScript() }
        .onChange(of: router.pendingFilmScriptId) { _, _ in consumePendingFilmScript() }
        // build 52: confirm before discarding a draft — it holds a real recording.
        .confirmationDialog("Delete this draft?",
                            isPresented: Binding(get: { draftPendingDelete != nil },
                                                 set: { if !$0 { draftPendingDelete = nil } }),
                            titleVisibility: .visible) {
            Button("Delete draft", role: .destructive) {
                if let d = draftPendingDelete { store.deleteClip(d) }
                draftPendingDelete = nil
            }
            Button("Cancel", role: .cancel) { draftPendingDelete = nil }
        } message: {
            Text("The recording saved with this draft will be discarded.")
        }
    }

    /// The edit-prefs summary with "Settings" styled as a tappable link — the whole
    /// line is one Button, this just makes the destination visually obvious.
    private var editPrefsCaption: AttributedString {
        let prefix = "Edits follow your style, captions \(store.editPrefs.autoCaptions ? "on" : "off"), " +
            "\(store.editPrefs.captionStyle?.label ?? "Auto") captions, \(store.editPrefs.fillerTrim.label.lowercased()) filler trim. Change in "
        var result = AttributedString(prefix)
        result.foregroundColor = Palette.textSecondary
        var link = AttributedString("Settings.")
        link.foregroundColor = Palette.textPrimary
        link.underlineStyle = .single
        result.append(link)
        return result
    }

    /// "Film this" deep-links land here with a preselected script — jump straight to the reader.
    private func consumePendingFilmScript() {
        guard router.pendingFilmScriptId != nil else { return }
        // The queue view highlights it at the top; the creator taps through.
        // (Auto-push is deliberately avoided: two pushes racing a fullScreenCover present is fragile.)
        router.pendingFilmScriptId = nil
    }

    private func readiedRow(_ saved: SavedScript) -> some View {
        HStack(alignment: .center, spacing: Space.md) {
            VStack(alignment: .leading, spacing: Space.sm) {
                HStack(spacing: Space.sm) {
                    DSEyebrow(text: saved.source.label)
                    if !saved.mimickedFrom.isEmpty {
                        Text(saved.mimickedFrom).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            .lineLimit(1)
                    }
                }
                Text(saved.script.title.isEmpty ? saved.script.hook.text : saved.script.title)
                    .font(AppFont.title3).foregroundStyle(Palette.textPrimary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: Space.sm) {
                    FormatTag(formatId: saved.script.formatId)
                    Text("\(saved.script.targetSeconds)s").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                }
            }
            Spacer(minLength: Space.sm)
            Image(systemName: "chevron.right").font(.system(size: 14, weight: .semibold)).foregroundStyle(Palette.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard(.surface, radius: Radius.card)
        .contentShape(Rectangle())
    }

    /// Resolve a draft back to its script; if the script's been deleted, rebuild a minimal
    /// one from the draft clip so the reader → teleprompter path still works (no dead-ends).
    private func resolvedScript(for d: Clip) -> Script {
        if let s = store.scripts.first(where: { $0.id == d.scriptId })
            ?? store.readiedScripts.first(where: { $0.script.id == d.scriptId })?.script {
            return s
        }
        return Script(
            id: d.scriptId,                 // keep the draft ↔ script link stable across resumes
            pillarName: "Your script",
            title: d.title,
            summary: "Recovered from your draft",
            style: Catalog.style(for: d.formatId).rawValue,
            formatId: d.formatId,
            hook: Hook(text: d.title.isEmpty ? d.caption : d.title, signal: .narrative, strength: 70),
            altHooks: [],
            body: "",
            cta: d.caption,
            shotPlan: ["Hook on frame 1, direct eye contact", "One punch-in on the key line", "CTA to camera"],
            targetSeconds: d.seconds > 0 ? d.seconds : Catalog.format(d.formatId).targetSeconds,
            predictedScore: d.predictedScore
        )
    }

    private func draftRow(_ d: Clip) -> some View {
        // Timeline-row style: thumbnail leading, title + status line, chevron trailing.
        // The old amber tint is carried by the pencil glyph + wording instead of color.
        HStack(spacing: Space.md) {
            LocalThumbnail(path: d.thumbnailPath ?? d.localVideoPath, isVideo: true)
                .frame(width: 44, height: 58)
                .clipShape(RoundedRectangle(cornerRadius: Radius.cell, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(d.title.isEmpty ? d.caption : d.title)
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary).lineLimit(1)
                HStack(spacing: 4) {
                    Image(systemName: "pencil.line").font(.system(size: 11, weight: .semibold))
                    Text("Draft, pick up where you left off")
                        .font(AppFont.caption)
                        .lineLimit(2)
                }
                .foregroundStyle(Palette.textSecondary)
            }
            Spacer(minLength: Space.sm)
            Image(systemName: "chevron.right").font(.system(size: 14, weight: .semibold)).foregroundStyle(Palette.textPrimary)
        }
        .padding(Space.rowPad)
        .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.surface))
        .contentShape(Rectangle())
    }
}

// MARK: - Write your own script

struct CustomScriptSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        NavigationStack {
            ScrollView {
                // Stoic journal editor: title1 prompt, borderless title + body on the canvas.
                VStack(alignment: .leading, spacing: Space.lg) {
                    Text("your script.").font(AppFont.title1).tracking(-0.3)
                        .foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                    VStack(alignment: .leading, spacing: 0) {
                        TextField("Title (optional)", text: $title)
                            .font(AppFont.title3)
                            .foregroundStyle(Palette.textPrimary)
                            .frame(minHeight: 44)
                            .accessibilityIdentifier("film.customTitle")
                        Rectangle().fill(Palette.hairline).frame(height: 1)
                    }
                    VStack(alignment: .leading, spacing: Space.sm) {
                        DSEyebrow(text: "YOUR SCRIPT")
                        TextEditor(text: $text)
                            .font(AppFont.bodyLarge)
                            .foregroundStyle(Palette.textPrimary)
                            .lineSpacing(4)
                            .focused($focused)
                            .frame(minHeight: 220)
                            .scrollContentBackground(.hidden)
                            .background(Color.clear)
                            .padding(.horizontal, -5)   // align TextEditor's inset with the title text
                            .accessibilityIdentifier("film.customBody")
                    }
                    PrimaryButton(title: "Queue it up", fullWidth: false) { saveCustom() }
                        .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        .accessibilityIdentifier("film.customSave")
                        .frame(maxWidth: .infinity)
                        .padding(.top, Space.sm)
                }
                .screenPadding().padding(.top, Space.sm).padding(.bottom, Space.xl)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .scrollDismissesKeyboard(.interactively)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                        .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                }
                // Without this the keyboard buries "Queue it up" with no way out —
                // TextEditor never dismisses on its own.
                ToolbarItem(placement: .keyboard) {
                    HStack {
                        Spacer()
                        Button("Done") { focused = false }
                            .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                            .accessibilityIdentifier("film.customDone")
                    }
                }
            }
            .onAppear { focused = true }
        }
        .tint(Palette.textPrimary)
    }

    private func saveCustom() {
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return }
        let firstLine = body.components(separatedBy: .newlines).first ?? body
        let hookText = String(firstLine.prefix(120))
        let style = store.brand.preferredStyles.first ?? .talkingHead
        let script = Script(
            pillarName: "Your script",
            title: title.isEmpty ? String(hookText.prefix(40)) : title,
            summary: "Written by you",
            style: style.rawValue,
            formatId: style.formats.first ?? "myth-buster",
            hook: Hook(text: hookText, signal: .narrative, strength: 75),
            altHooks: [],
            body: body,
            cta: "",
            shotPlan: ["Hook on frame 1, direct eye contact", "One punch-in on the key line", "CTA to camera"],
            targetSeconds: max(15, min(60, body.split(separator: " ").count / 3)),
            predictedScore: 75
        )
        store.scripts.insert(script, at: 0)
        store.readyScript(script, source: .custom)
        dismiss()
    }
}

// W4: dedicated reorder sheet — a real drag-to-reorder List (.onMove) for the film queue.
struct QueueReorderSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            // Grouped rows on the canvas; the system reorder grabber is the drag handle.
            List {
                ForEach(store.queuedScripts) { saved in
                    Text(saved.script.title.isEmpty ? saved.script.hook.text : saved.script.title)
                        .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary).lineLimit(1)
                        .frame(minHeight: 36, alignment: .leading)
                        .listRowBackground(Palette.surface)
                        .listRowSeparatorTint(Palette.hairline)
                }
                .onMove { store.moveReadied(fromOffsets: $0, toOffset: $1) }
            }
            .scrollContentBackground(.hidden)
            .background(Palette.canvas.ignoresSafeArea())
            .environment(\.editMode, .constant(.active))
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("reorder queue.").font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                }
            }
        }
        .tint(Palette.textPrimary)
    }
}
