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

    private var drafts: [Clip] { store.clips.filter { $0.status == .draft } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("READY TO FILM").font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
                    Text("Film").font(Typeface.display(40)).tracking(-1).foregroundStyle(Palette.textPrimary)
                }
                .padding(.top, Space.md)

                // Continue a draft
                if !drafts.isEmpty {
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionLabel(text: "Continue a draft", accent: Palette.warning)
                        ForEach(drafts) { d in
                            NavigationLink(value: resolvedScript(for: d)) {
                                draftRow(d)
                            }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("film.draft")
                        }
                    }
                }

                // Readied scripts (the film queue)
                VStack(alignment: .leading, spacing: Space.md) {
                    HStack {
                        SectionLabel(text: "Your queue", accent: Palette.accent)
                        Spacer()
                        Text("\(store.readiedScripts.count)")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    }
                    if store.readiedScripts.isEmpty {
                        EmptyStateView(icon: "bookmark", title: "Nothing queued yet",
                                       message: "Save scripts from your Home picks, a mimic, or chat — they land here ready to film.")
                    } else {
                        ForEach(store.readiedScripts) { saved in
                            NavigationLink(value: saved.script) {
                                readiedRow(saved)
                            }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("film.readied")
                            .contextMenu {
                                Button(role: .destructive) {
                                    store.removeReadiedScript(saved)
                                } label: { Label("Remove from queue", systemImage: "bookmark.slash") }
                            }
                        }
                    }
                }

                // Write your own
                VStack(alignment: .leading, spacing: Space.md) {
                    SectionLabel(text: "Or write your own")
                    Button { showCustomEditor = true } label: {
                        HStack(spacing: Space.md) {
                            Image(systemName: "square.and.pencil")
                                .font(.system(size: 16)).foregroundStyle(Palette.accent)
                            Text("Paste or write a script")
                                .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                            Spacer()
                            Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
                        }
                        .padding(Space.lg)
                        .background(Palette.surfaceRaised)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                            .strokeBorder(Palette.hairline, lineWidth: 1))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("film.customScript")
                    Button { showSettings = true } label: {
                        Text(editPrefsCaption)
                            .font(AppFont.caption)
                            .multilineTextAlignment(.leading)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("film.changeEditPrefs")
                }
            }
            .screenPadding()
            .padding(.top, Space.lg)
            .padding(.bottom, 110)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            // Top-right: matches iOS modal-dismiss convention (fullScreenCover has no
            // swipe-to-dismiss, so this button is the only way out — keep it discoverable).
            ToolbarItem(placement: .topBarTrailing) {
                Button { router.showFilm = false } label: {
                    Image(systemName: "xmark").font(.system(size: 15, weight: .semibold)).foregroundStyle(Palette.textSecondary)
                }
                .accessibilityLabel("Close")
                .accessibilityIdentifier("film.close")
            }
        }
        .navigationDestination(for: Script.self) { ScriptReaderView(script: $0) }
        .sheet(isPresented: $showCustomEditor) { CustomScriptSheet() }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .onAppear { consumePendingFilmScript() }
        .onChange(of: router.pendingFilmScriptId) { _, _ in consumePendingFilmScript() }
    }

    /// The edit-prefs summary with "Settings" styled as a tappable link — the whole
    /// line is one Button, this just makes the destination visually obvious.
    private var editPrefsCaption: AttributedString {
        let prefix = "Edits follow your style — captions \(store.editPrefs.autoCaptions ? "on" : "off"), " +
            "\(store.editPrefs.captionStyle.label) captions, \(store.editPrefs.fillerTrim.label.lowercased()) filler trim. Change in "
        var result = AttributedString(prefix)
        result.foregroundColor = Palette.textTertiary
        var link = AttributedString("Settings.")
        link.foregroundColor = Palette.accent
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
        HStack(spacing: Space.md) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: Space.sm) {
                    Text(saved.source.label.uppercased())
                        .font(.system(size: 9, weight: .bold)).tracking(0.6)
                        .foregroundStyle(Palette.accent)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Palette.accent.opacity(0.10)).clipShape(Capsule())
                    if !saved.mimickedFrom.isEmpty {
                        Text(saved.mimickedFrom).font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                    }
                }
                Text(saved.script.title.isEmpty ? saved.script.hook.text : saved.script.title)
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    .lineLimit(2)
                HStack(spacing: Space.sm) {
                    FormatTag(formatId: saved.script.formatId)
                    Text("\(saved.script.targetSeconds)s").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                }
            }
            Spacer()
            Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
        }
        .padding(Space.lg)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
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
        HStack(spacing: Space.md) {
            LocalThumbnail(path: d.thumbnailPath ?? d.localVideoPath, isVideo: true)
                .frame(width: 44, height: 58)
                .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
            VStack(alignment: .leading, spacing: 3) {
                Text(d.title.isEmpty ? d.caption : d.title)
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary).lineLimit(1)
                Text("Draft — pick up where you left off")
                    .font(AppFont.caption).foregroundStyle(Palette.warning)
            }
            Spacer()
            Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
        }
        .padding(Space.md)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.warning.opacity(0.35), lineWidth: 1))
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
                VStack(alignment: .leading, spacing: Space.lg) {
                    TextField("Title (optional)", text: $title)
                        .marqueField()
                        .accessibilityIdentifier("film.customTitle")
                    VStack(alignment: .leading, spacing: Space.xs) {
                        Text("YOUR SCRIPT").font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
                        TextEditor(text: $text)
                            .font(AppFont.bodyL)
                            .focused($focused)
                            .frame(minHeight: 220)
                            .padding(Space.sm)
                            .scrollContentBackground(.hidden)
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                                .strokeBorder(Palette.hairline, lineWidth: 1))
                            .accessibilityIdentifier("film.customBody")
                    }
                    PrimaryButton(title: "Queue it up") { saveCustom() }
                        .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        .accessibilityIdentifier("film.customSave")
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle("Your script")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } }
                // Without this the keyboard buries "Queue it up" with no way out —
                // TextEditor never dismisses on its own.
                ToolbarItem(placement: .keyboard) {
                    HStack {
                        Spacer()
                        Button("Done") { focused = false }
                            .accessibilityIdentifier("film.customDone")
                    }
                }
            }
            .onAppear { focused = true }
        }
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
