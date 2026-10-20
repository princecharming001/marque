import SwiftUI

// Scripts v2 — "How you talk". The backend learns the creator's voice from their own reels
// (GET /v1/voice); this screen lets them see it and correct it: the paragraph, the phrases
// they really say (on/off), what they never say, free-text corrections, a rebuild from
// their reels, and a with/without preview. Presented as a sheet from Profile and from the
// script reader's "Not me". Every write is a PUT of just the changed part.
struct HowYouTalkView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    private enum Phase: Equatable { case loading, empty, failed, loaded }
    @State private var phase: Phase = .loading
    @State private var profile = VoiceProfile()
    @State private var paragraphDraft = ""
    @State private var newNeverSay = ""
    @State private var correctionDraft = ""
    @State private var saving: Set<String> = []          // sections mid-save
    @State private var notice: String?
    @State private var noticeTask: Task<Void, Never>?
    @State private var building = false
    @State private var buildLine: String?
    @State private var preview: BackendClient.VoicePreview?
    @State private var previewing = false
    @State private var previewFailed = false

    private var account: ConnectedAccount? { store.primaryAccount }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    switch phase {
                    case .loading:
                        ProgressView().tint(Palette.textPrimary)
                            .frame(maxWidth: .infinity).padding(.vertical, Space.huge)
                    case .failed:
                        DSEmptyState(systemImage: "wifi.exclamationmark",
                                     title: "Couldn't load how you talk",
                                     message: "Check your connection and try again.",
                                     actionTitle: "Try again") { Task { await load() } }
                    case .empty:
                        emptyState
                    case .loaded:
                        paragraphSection
                        builtFromSection
                        if !profile.phrases.isEmpty { phrasesSection }
                        neverSaySection
                        correctionSection
                        previewSection
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("how you talk.")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.fontWeight(.semibold)
                }
            }
            .tint(Palette.ink)
            .overlay(alignment: .top) {
                if let notice {
                    DSToast(text: notice)
                        .padding(.top, Space.sm)
                        .transition(.move(edge: .top).combined(with: .opacity))
                }
            }
        }
        .task { await load() }
    }

    // MARK: Empty (no voice yet / older or mock backend)

    private var emptyState: some View {
        VStack(spacing: Space.sm) {
            DSEmptyState(systemImage: "waveform",
                         title: "No voice yet",
                         message: account == nil
                            ? "Connect Instagram or TikTok in Profile and we'll learn how you talk from your reels."
                            : "We learn how you talk from your reels. It takes a few minutes.",
                         actionTitle: account == nil || building ? nil : "Build from my reels") {
                Task { await rebuild() }
            }
            if building { ProgressView().tint(Palette.textPrimary) }
            if let buildLine {
                Text(buildLine).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: Paragraph

    private var paragraphChanged: Bool {
        paragraphDraft.trimmingCharacters(in: .whitespacesAndNewlines)
            != profile.paragraph.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var paragraphSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            DSEyebrow(text: "How you sound").padding(.horizontal, Space.rowPad)
            TextEditor(text: $paragraphDraft)
                .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                .lineSpacing(4)
                .frame(minHeight: 120)
                .scrollContentBackground(.hidden)
                .padding(Space.sm)
                .background(Palette.surface)
                .clipShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
                .accessibilityIdentifier("voice.paragraph")
            if paragraphChanged {
                HStack {
                    Spacer()
                    Button {
                        Task { await saveParagraph() }
                    } label: {
                        if saving.contains("paragraph") {
                            ProgressView().controlSize(.small).tint(Palette.textSecondary)
                        } else {
                            Text("Save")
                        }
                    }
                    .buttonStyle(.ds(.primary, height: 44))
                    .disabled(saving.contains("paragraph")
                              || paragraphDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityIdentifier("voice.saveParagraph")
                }
            }
        }
    }

    // MARK: Built from + rebuild

    private var builtFromSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            Text(VoiceCopy.confidenceLine(profile))
                .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("voice.confidence")
            if account != nil {
                Button {
                    Task { await rebuild() }
                } label: {
                    if building {
                        ProgressView().controlSize(.small).tint(Palette.textSecondary)
                    } else {
                        Text("Rebuild from my reels")
                    }
                }
                .buttonStyle(.ds(.outline, height: 40))
                .disabled(building)
                .accessibilityIdentifier("voice.rebuild")
            } else {
                Text("Connect Instagram or TikTok in Profile to rebuild from your reels.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let buildLine {
                Text(buildLine).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, Space.rowPad)
    }

    // MARK: Phrases

    private var phrasesSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            DSEyebrow(text: "Things you really say").padding(.horizontal, Space.rowPad)
            FlowWrap(spacing: Space.sm) {
                ForEach(profile.phrases) { phrase in
                    DSChip(title: phrase.text,
                           systemImage: phrase.on ? "checkmark" : nil,
                           isSelected: phrase.on) {
                        Task { await togglePhrase(phrase) }
                    }
                    .accessibilityLabel("\(phrase.text), \(phrase.on ? "on" : "off")")
                    .accessibilityIdentifier("voice.phrase")
                }
            }
            .disabled(saving.contains("phrases"))
            Text("Tap one to stop using it.")
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                .padding(.horizontal, Space.rowPad)
        }
    }

    // MARK: Never say

    private var canAddNeverSay: Bool {
        !newNeverSay.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !saving.contains("never_say")
    }

    private var neverSaySection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            DSEyebrow(text: "You never say").padding(.horizontal, Space.rowPad)
            DSGroup {
                ForEach(Array(profile.neverSay.enumerated()), id: \.offset) { i, word in
                    if i > 0 { DSRowDivider() }
                    HStack(spacing: Space.sm) {
                        Text(word).font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: Space.sm)
                        Button { Task { await removeNeverSay(word) } } label: {
                            Image(systemName: "xmark")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(Palette.textSecondary)
                                .frame(width: 44, height: 44)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(PressableStyle(scale: 0.92))
                        .accessibilityLabel("Remove \(word)")
                    }
                    .padding(.leading, Space.rowPad).padding(.trailing, Space.xs)
                    .frame(minHeight: 52)
                }
                if !profile.neverSay.isEmpty { DSRowDivider() }
                HStack(spacing: Space.sm) {
                    TextField("Add a word or phrase", text: $newNeverSay)
                        .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                        .textInputAutocapitalization(.never)
                        .submitLabel(.done)
                        .onSubmit { Task { await addNeverSay() } }
                        .accessibilityIdentifier("voice.neverSayField")
                    Button("Add") { Task { await addNeverSay() } }
                        .font(AppFont.supporting.weight(.semibold))
                        .foregroundStyle(canAddNeverSay ? Palette.textPrimary : Palette.textTertiary)
                        .frame(minWidth: 44, minHeight: 44)
                        .disabled(!canAddNeverSay)
                        .accessibilityIdentifier("voice.neverSayAdd")
                }
                .padding(.leading, Space.rowPad).padding(.trailing, Space.sm)
                .frame(minHeight: 52)
            }
        }
    }

    // MARK: Corrections

    private var canSendCorrection: Bool {
        !correctionDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !saving.contains("correction")
    }

    private var correctionSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            DSEyebrow(text: "Tell us what's off").padding(.horizontal, Space.rowPad)
            HStack(alignment: .bottom, spacing: Space.sm) {
                TextField("e.g. I'd never say \u{201C}folks\u{201D}", text: $correctionDraft, axis: .vertical)
                    .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                    .lineLimit(1...4)
                    .padding(.horizontal, Space.md).padding(.vertical, 13)
                    .background(Palette.surface)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: Radius.card, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                    .accessibilityIdentifier("voice.correctionField")
                Button { Task { await sendCorrection() } } label: {
                    Group {
                        if saving.contains("correction") {
                            ProgressView().controlSize(.small).tint(Palette.textSecondary)
                        } else {
                            Image(systemName: "arrow.up")
                                .font(.system(size: 17, weight: .semibold))
                                .foregroundStyle(canSendCorrection ? Palette.onInk : Palette.textTertiary)
                        }
                    }
                    .frame(width: 48, height: 48)
                    .background(Circle().fill(canSendCorrection ? Palette.ink : Palette.surfaceSunken))
                    .contentShape(Circle())
                }
                .buttonStyle(PressableStyle(dim: 0.85, scale: 0.92))
                .disabled(!canSendCorrection)
                .accessibilityLabel("Send")
                .accessibilityIdentifier("voice.correctionSend")
            }
            if !profile.corrections.isEmpty {
                VStack(alignment: .leading, spacing: Space.sm) {
                    DSEyebrow(text: "You told us")
                    ForEach(Array(profile.corrections.enumerated()), id: \.offset) { _, c in
                        HStack(alignment: .top, spacing: Space.sm) {
                            Image(systemName: "checkmark")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(Palette.textPrimary)
                                .padding(.top, 3)
                            Text(c).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .padding(.horizontal, Space.rowPad)
                .padding(.top, Space.xs)
            }
        }
    }

    // MARK: Hear the difference

    private var previewSection: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            DSEyebrow(text: "Hear the difference").padding(.horizontal, Space.rowPad)
            if let preview {
                previewCard("Without your voice", preview.withoutVoice, muted: true)
                previewCard("With your voice", preview.withVoice, muted: false)
            } else {
                Text("The same idea, written both ways.")
                    .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                    .padding(.horizontal, Space.rowPad)
            }
            if previewFailed {
                Text("Couldn't write the preview. Try again.")
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    .padding(.horizontal, Space.rowPad)
            }
            Button {
                Task { await loadPreview() }
            } label: {
                if previewing {
                    ProgressView().controlSize(.small).tint(Palette.textSecondary)
                } else {
                    Text(preview == nil ? "Show me" : "Try another")
                }
            }
            .buttonStyle(.ds(.outline, height: 44))
            .disabled(previewing)
            .accessibilityIdentifier("voice.preview")
        }
    }

    private func previewCard(_ title: String, _ s: Script, muted: Bool) -> some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            DSEyebrow(text: title)
            Text(s.hook.text).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Text(s.body).font(AppFont.supporting)
                .foregroundStyle(muted ? Palette.textSecondary : Palette.textPrimary)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
            if !s.cta.isEmpty {
                Text(s.cta).font(AppFont.supporting.weight(.semibold))
                    .foregroundStyle(muted ? Palette.textSecondary : Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard(muted ? .outline : .surface, radius: Radius.group)
    }

    // MARK: Actions

    private func load() async {
        phase = .loading
        switch await store.backend.getVoice() {
        case .loaded(let p):
            profile = p
            paragraphDraft = p.paragraph
            phase = .loaded
        case .empty:
            phase = .empty
        case .failed:
            phase = .failed
        }
    }

    /// PUT one change. On success the server's profile replaces ours (an unsaved paragraph
    /// draft is kept); on failure the caller reverts and the toast says so.
    private func save(_ update: VoiceUpdate, key: String) async -> Bool {
        saving.insert(key)
        defer { saving.remove(key) }
        guard let saved = await store.backend.putVoice(update) else {
            flash("Couldn't save. Try again.")
            return false
        }
        if !saved.isEmpty {
            let draftWasClean = !paragraphChanged
            profile = saved
            if draftWasClean || key == "paragraph" { paragraphDraft = saved.paragraph }
        }
        return true
    }

    private func saveParagraph() async {
        let text = paragraphDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        if await save(VoiceUpdate(paragraph: text), key: "paragraph") {
            if paragraphChanged { profile.paragraph = text }   // server didn't echo the profile
            flash("Saved")
        }
    }

    private func togglePhrase(_ phrase: VoicePhrase) async {
        guard let i = profile.phrases.firstIndex(where: { $0.id == phrase.id }) else { return }
        let before = profile.phrases
        profile.phrases[i].on.toggle()                         // optimistic
        if await save(VoiceUpdate(phrases: profile.phrases), key: "phrases") == false {
            profile.phrases = before
        }
    }

    private func addNeverSay() async {
        let t = newNeverSay.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, !saving.contains("never_say") else { return }
        let before = profile.neverSay
        profile.neverSay = VoiceUpdate.cleanList(before + [t])
        newNeverSay = ""
        if await save(VoiceUpdate(neverSay: profile.neverSay), key: "never_say") == false {
            profile.neverSay = before
            newNeverSay = t
        }
    }

    private func removeNeverSay(_ word: String) async {
        guard !saving.contains("never_say") else { return }
        let before = profile.neverSay
        profile.neverSay.removeAll { $0 == word }
        if await save(VoiceUpdate(neverSay: profile.neverSay), key: "never_say") == false {
            profile.neverSay = before
        }
    }

    private func sendCorrection() async {
        let t = correctionDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, !saving.contains("correction") else { return }
        if await save(VoiceUpdate(correction: t), key: "correction") {
            correctionDraft = ""
            // Show it even if this backend doesn't echo corrections back yet.
            if !profile.corrections.contains(t) { profile.corrections.append(t) }
        }
    }

    private func rebuild() async {
        guard let acct = account, !building else { return }
        building = true
        buildLine = nil
        let status = await store.backend.buildVoice(handle: acct.handle, platform: acct.platform)
        building = false
        buildLine = VoiceCopy.buildLine(status)
        if status == .done { await load() }
    }

    private func loadPreview() async {
        guard !previewing else { return }
        previewing = true
        previewFailed = false
        let topic = store.pillars.first?.name ?? store.brand.niche
        let r = await store.backend.previewVoice(brand: store.brand, topic: topic)
        previewing = false
        if let r { preview = r } else { previewFailed = true }
    }

    private func flash(_ text: String) {
        noticeTask?.cancel()
        withAnimation(Motion.quick) { notice = text }
        noticeTask = Task {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard !Task.isCancelled else { return }
            withAnimation(Motion.quick) { notice = nil }
        }
    }
}
