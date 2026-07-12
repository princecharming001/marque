import SwiftUI

// Conversational post-edit tweaks — a compact chat over ONE finished clip.
// The creator types small changes ("karaoke captions", "cut the pause at 12s",
// "undo that"); the backend turns them into typed EDL ops and re-renders just
// this clip. Session-local history (nothing persisted; zero Snapshot changes),
// completely separate from the main Chat tab.
struct TweakChatSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let clip: Clip
    // UX-D1: the detail-sheet affordance opens with the composer focused.
    var autoFocus: Bool = false

    private struct Msg: Identifiable {
        enum Role { case user, assistant, status }
        let id = UUID()
        let role: Role
        let text: String
    }

    @State private var messages: [Msg] = []
    @State private var input = ""
    @State private var sending = false
    @State private var rendering = false
    @State private var pollTask: Task<Void, Never>?
    @State private var sendTask: Task<Void, Never>?
    @FocusState private var composerFocused: Bool
    // UX-D2 preview-first: the staged (uncommitted) ops awaiting Apply/Discard, and
    // whether a preview proof-render is on screen.
    @State private var pendingOps: [[String: Any]] = []
    @State private var previewLive = false

    private let starters: [(chip: String, message: String)] = [
        ("Karaoke captions", "Make the captions karaoke style"),
        ("Bolder captions", "Make the captions bold-word style"),
        ("Remove the zooms", "Remove all the punch-in zooms"),
        ("Undo last tweak", "Undo the last tweak"),
    ]

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: Space.md) {
                        intro
                        if messages.isEmpty { starterChips }
                        ForEach(messages) { msg in
                            row(msg).id(msg.id)
                        }
                        if sending {
                            HStack(spacing: Space.sm) {
                                ProgressView().tint(Palette.accent)
                                Text("Thinking…").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            }
                        }
                        if previewLive { applyDiscardBar }
                    }
                    .screenPadding().padding(.vertical, Space.lg)
                }
                .onChange(of: messages.count) { _, _ in
                    if let last = messages.last {
                        withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Tweak this edit").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
            .safeAreaInset(edge: .bottom) { composer }
        }
        .onDisappear {
            // Cancel BOTH in-flight tasks — an untracked send() Task would otherwise
            // keep running after dismissal and write to this view's dead @State.
            sendTask?.cancel(); sendTask = nil
            pollTask?.cancel(); pollTask = nil
            // UX-D2: previews are look-don't-commit — dismissing discards the staged
            // candidate (the server never installed it; just drop the local URL).
            store.clearClipPreview(clip.id)
        }
        .onAppear { if autoFocus { composerFocused = true } }
    }

    // MARK: rows

    private var intro: some View {
        Text("Tell me what to change — captions, cuts, zooms, b-roll. I'll re-edit just this clip.")
            .font(AppFont.body).foregroundStyle(Palette.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var starterChips: some View {
        FlowChips(items: starters.map(\.chip)) { chip in
            if let s = starters.first(where: { $0.chip == chip }) {
                send(s.message)
            }
        }
    }

    @ViewBuilder
    private func row(_ msg: Msg) -> some View {
        switch msg.role {
        case .user:
            HStack {
                Spacer(minLength: Space.huge)
                Text(msg.text)
                    .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                    .padding(.horizontal, Space.md).padding(.vertical, 10)
                    .background(Palette.surfaceSunken)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
            }
        case .assistant:
            Text(msg.text)
                .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        case .status:
            HStack(spacing: Space.sm) {
                if rendering { ProgressView().tint(Palette.accent) }
                else { Image(systemName: "checkmark.circle.fill").font(.system(size: 13)).foregroundStyle(Palette.positive) }
                Text(msg.text).font(AppFont.caption).foregroundStyle(Palette.textTertiary)
            }
        }
    }

    private var composer: some View {
        HStack(spacing: Space.sm) {
            TextField("Change something…", text: $input, axis: .vertical)
                .focused($composerFocused)
                .font(AppFont.bodyL)
                .lineLimit(1...4)
                .padding(.horizontal, Space.md).padding(.vertical, 12)
                .background(Palette.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: Radius.pill, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.pill, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
                .accessibilityIdentifier("tweak.input")
            Button {
                let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty else { return }
                input = ""
                send(text)
            } label: {
                Image(systemName: "arrow.up")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Palette.onInk)
                    .frame(width: 38, height: 38)
                    .background(Circle().fill(Palette.ink))
            }
            .disabled(sending)
            .accessibilityIdentifier("tweak.send")
        }
        .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
        .background(.ultraThinMaterial)
    }

    // MARK: actions

    /// UX-D2: Apply / Discard for a staged preview — the whole point of preview-first:
    /// see the change BEFORE committing a full render to it.
    private var applyDiscardBar: some View {
        HStack(spacing: Space.sm) {
            Button { applyPreview() } label: {
                Text("Apply this change")
                    .font(AppFont.headline).foregroundStyle(Palette.onInk)
                    .frame(maxWidth: .infinity).padding(.vertical, 12)
                    .background(Palette.ink)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("tweak.apply")
            Button { discardPreview() } label: {
                Text("Discard")
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                    .padding(.horizontal, Space.lg).padding(.vertical, 12)
                    .background(Palette.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("tweak.discard")
        }
    }

    private func send(_ text: String) {
        guard let jobId = clip.jobId, !sending else { return }
        // A new instruction supersedes any staged preview — drop it first.
        if previewLive || !pendingOps.isEmpty { discardPreview(quiet: true) }
        messages.append(Msg(role: .user, text: text))
        sending = true
        sendTask = Task {
            // UX-D2 preview-first: stage + proof-render without committing. The
            // backend answers preview_requested=false when it can't preview
            // (keyless, no renderer, undo, nothing changed) — then the classic
            // direct-commit flow below is exactly what should happen, so retry
            // the same instruction as a normal turn.
            let resp = await store.backend.tweakClipPreview(jobId: jobId,
                                                            clipId: clip.id.uuidString,
                                                            instruction: text)
            guard !Task.isCancelled else { return }
            let reply = resp["reply"] as? String ?? "Something went sideways — try that again."
            if resp["preview_requested"] as? Bool == true {
                sending = false
                messages.append(Msg(role: .assistant, text: reply))
                pendingOps = (resp["ops"] as? [[String: Any]]) ?? []
                rendering = true
                messages.append(Msg(role: .status, text: "Rendering a quick preview — nothing is committed yet."))
                startPreviewPolling(jobId: jobId)
                return
            }
            if resp["error"] as? Bool == true {
                sending = false
                messages.append(Msg(role: .assistant, text: reply))
                return
            }
            // No preview possible → today's direct flow, unchanged.
            let direct = await store.backend.tweakClip(jobId: jobId,
                                                       clipId: clip.id.uuidString,
                                                       instruction: text)
            guard !Task.isCancelled else { return }
            sending = false
            let directReply = direct["reply"] as? String ?? "Something went sideways — try that again."
            messages.append(Msg(role: .assistant, text: directReply))
            if direct["needs_render"] as? Bool == true {
                rendering = true
                store.setClipRendering(clip.id)
                messages.append(Msg(role: .status, text: "Re-editing your clip — this usually takes a minute or two."))
                startPolling(jobId: jobId)
            }
        }
    }

    /// Commit the previewed ops deterministically (the direct-ops path — no second
    /// LLM interpretation) and ride the existing render poll.
    private func applyPreview() {
        guard let jobId = clip.jobId, !pendingOps.isEmpty else { discardPreview(); return }
        let ops = pendingOps
        pendingOps = []
        previewLive = false
        store.clearClipPreview(clip.id)
        sending = true
        sendTask = Task {
            let resp = await store.backend.tweakClipOps(jobId: jobId,
                                                        clipId: clip.id.uuidString, ops: ops)
            guard !Task.isCancelled else { return }
            sending = false
            if resp["error"] as? Bool == true {
                messages.append(Msg(role: .assistant,
                                    text: resp["reply"] as? String ?? "Couldn't apply that — try again."))
                return
            }
            messages.append(Msg(role: .status, text: "Applying it for real now."))
            if resp["needs_render"] as? Bool == true {
                rendering = true
                store.setClipRendering(clip.id)
                startPolling(jobId: jobId)
            } else {
                rendering = false
                messages.append(Msg(role: .status, text: "Done — the change is saved."))
            }
        }
    }

    /// Drop the staged preview. The server committed NOTHING on a preview turn, so
    /// this is purely local: clear the URL + ops.
    private func discardPreview(quiet: Bool = false) {
        pendingOps = []
        previewLive = false
        rendering = false
        pollTask?.cancel(); pollTask = nil
        store.clearClipPreview(clip.id)
        if !quiet { messages.append(Msg(role: .status, text: "Discarded — your clip is untouched.")) }
    }

    /// UX-D2: watch MY clip's preview_status/preview_url (3s cadence) until the
    /// proof render lands, then surface it on the detail player with a PREVIEW badge.
    private func startPreviewPolling(jobId: String) {
        pollTask?.cancel()
        pollTask = Task {
            for _ in 0..<40 {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                if Task.isCancelled { return }
                guard let result = await store.backend.pollClipJob(jobId: jobId),
                      let jobClips = result["clips"] as? [[String: Any]],
                      let mine = jobClips.first(where: {
                          UUID(uuidString: ($0["clip_id"] as? String) ?? "") == clip.id
                      })
                else { continue }
                let status = mine["preview_status"] as? String
                if status == "ready", let url = mine["preview_url"] as? String, !url.isEmpty {
                    store.setClipPreview(clip.id, url: url)
                    previewLive = true
                    rendering = false
                    messages.append(Msg(role: .status,
                                        text: "Preview is up — check the player, then apply or discard."))
                    pollTask = nil
                    return
                }
                if status == "failed" {
                    rendering = false
                    messages.append(Msg(role: .status,
                                        text: "Preview didn't render — you can still apply the change directly."))
                    previewLive = true          // apply/discard remain available
                    pollTask = nil
                    return
                }
            }
            rendering = false
            previewLive = !pendingOps.isEmpty   // let Apply/Discard resolve a slow preview
            messages.append(Msg(role: .status, text: "Preview is taking a while — you can apply or discard anyway."))
            pollTask = nil
        }
    }

    /// Watch THIS clip until its re-render lands (AppStore.pollJob watches the
    /// whole job's status, which stays "ready" during tweaks — hence a dedicated loop).
    private func startPolling(jobId: String) {
        pollTask?.cancel()
        pollTask = Task {
            for _ in 0..<60 {
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                if Task.isCancelled { return }
                guard let result = await store.backend.pollClipJob(jobId: jobId),
                      let jobClips = result["clips"] as? [[String: Any]],
                      // UUID-compare (backend ids are lowercase, uuidString is uppercase)
                      let mine = jobClips.first(where: {
                          UUID(uuidString: ($0["clip_id"] as? String) ?? "") == clip.id
                      })
                else { continue }
                if (mine["status"] as? String) == "ready" {
                    store.applyTweakResult(clip.id, remoteURL: mine["render_url"] as? String)
                    rendering = false
                    messages.append(Msg(role: .status, text: "Done — the new cut is live."))
                    pollTask = nil
                    return
                }
            }
            // Timed out politely; the Library keeps polling state honest on next open.
            rendering = false
            messages.append(Msg(role: .status, text: "Still working — check back in the Library in a bit."))
            pollTask = nil
        }
    }
}

// Simple wrapping chip row for the starter suggestions.
private struct FlowChips: View {
    let items: [String]
    let onTap: (String) -> Void
    var body: some View {
        FlexWrap(spacing: Space.sm) {
            ForEach(items, id: \.self) { chip in
                Button { onTap(chip) } label: {
                    Text(chip)
                        .font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                        .padding(.horizontal, Space.md).padding(.vertical, 8)
                        .background(Palette.surfaceRaised)
                        .clipShape(Capsule())
                        .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                }
                .buttonStyle(PressableStyle(dim: 0.7))
                .accessibilityIdentifier("tweak.chip")
            }
        }
    }
}

// Minimal flow layout (iOS 16+ Layout protocol) so chips wrap naturally.
private struct FlexWrap: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for v in subviews {
            let size = v.sizeThatFits(.unspecified)
            if x > 0, x + size.width > maxWidth { x = 0; y += rowHeight + spacing; rowHeight = 0 }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: maxWidth == .infinity ? x : maxWidth, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowHeight: CGFloat = 0
        for v in subviews {
            let size = v.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX { x = bounds.minX; y += rowHeight + spacing; rowHeight = 0 }
            v.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
