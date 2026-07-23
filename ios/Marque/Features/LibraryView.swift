import SwiftUI
import PhotosUI


struct LibraryView: View {
    @Environment(AppStore.self) private var store
    @State private var tabIndex = 0
    private let tabs = ["Clips", "Media"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.lg) {
                // Editorial inline header — kicker + Fraunces title (maxapp signature)
                VStack(alignment: .leading, spacing: 4) {
                    Text("YOUR CREATIVE VAULT").font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
                    Text("Library").font(Typeface.display(40)).tracking(-1).foregroundStyle(Palette.textPrimary)
                }
                UnderlineTabBar(tabs: tabs, index: $tabIndex)
                switch tabIndex {
                case 1: MediaSection()
                default: ClipsSection()
                }
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        // H-07: a tweak that outlived its poll window (or an app relaunch mid-render)
        // resolves here instead of spinning forever locally.
        .task { store.repollRenderingClips() }
    }
}

// MARK: - Clips (rendered, grouped by status, real posters, tap → player)

/// Which group the Library clip grid is filtered to (build 59).
enum ClipGroupFilter: Hashable { case all, ungrouped, group(UUID) }

struct ClipsSection: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var detail: Clip?
    // build 59: group filter + multi-select bulk actions.
    @State private var groupFilter: ClipGroupFilter = .all
    @State private var selecting = false
    @State private var selectedIDs: Set<UUID> = []
    @State private var showNewGroup = false
    @State private var newGroupName = ""
    @State private var showBulkDelete = false
    @State private var showBulkSchedule = false
    @State private var pendingAssignAfterCreate = false   // "New group…" from the assign menu

    private var hasFinishedClips: Bool {
        store.clips.contains { [.ready, .scheduled, .posted].contains($0.status) }
    }

    /// Clips passing the active group filter.
    private var filteredClips: [Clip] {
        switch groupFilter {
        case .all:            return store.clips
        case .ungrouped:      return store.clips.filter { $0.groupId == nil }
        case .group(let id):  return store.clips.filter { $0.groupId == id }
        }
    }
    private var selectedReadyCount: Int {
        store.clips.filter { selectedIDs.contains($0.id) && $0.status == .ready }.count
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            if store.clips.isEmpty {
                EmptyStateView(icon: "rectangle.stack", title: "No clips yet",
                               message: "Tap Film below to record your first script — drafts and edited clips land here.",
                               graphic: "ClipsIcon")
                Button { router.showFilm = true } label: {
                    Label("Create your first clip", systemImage: "video.badge.plus")
                        .font(AppFont.headline).foregroundStyle(Palette.onInk)
                        .frame(maxWidth: .infinity).frame(height: 52)
                        .background(Palette.ink).clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("library.createFirst")
            } else {
                controlRow
                let visible = filteredClips
                if visible.isEmpty {
                    Text(groupFilter == .ungrouped ? "No ungrouped clips."
                                                    : "This group is empty. Select clips and add them here.")
                        .font(AppFont.callout).foregroundStyle(Palette.textTertiary)
                        .padding(.vertical, Space.lg)
                }
                ForEach(ClipStatus.allOrder, id: \.self) { status in
                    let group = visible.filter { $0.status == status }
                    if !group.isEmpty {
                        VStack(alignment: .leading, spacing: Space.md) {
                            VStack(alignment: .leading, spacing: Space.xs) {
                                SectionLabel(text: status.title)
                                if status == .rendering {
                                    Text(renderingEtaLine(group))
                                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                                }
                            }
                            let cols = Array(repeating: GridItem(.flexible(), spacing: 8), count: 3)
                            LazyVGrid(columns: cols, spacing: 8) {
                                ForEach(Array(group.enumerated()), id: \.element.id) { i, c in
                                    Button { onCellTap(c) } label: {
                                        ClipGridCell(clip: c)
                                            .overlay { if selecting { selectionOverlay(c) } }
                                    }
                                    .buttonStyle(.plain)
                                    .accessibilityIdentifier("library.clip")
                                    .staggerReveal(i)
                                }
                            }
                        }
                    }
                }
                if !hasFinishedClips {
                    VStack(alignment: .leading, spacing: Space.sm) {
                        Text("Finished clips land here, ready to schedule.")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        Button { router.showFilm = true } label: {
                            Label("Film another clip", systemImage: "video.badge.plus")
                                .font(AppFont.caption).foregroundStyle(Palette.accent)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("library.filmAnother")
                    }
                    .padding(.top, Space.sm)
                }
                // Room so the floating action bar never covers the last row.
                if selecting { Color.clear.frame(height: 76) }
            }
        }
        .overlay(alignment: .bottom) { if selecting { bulkBar } }
        .animation(Motion.quick, value: selecting)
        .sheet(item: $detail) { ClipDetailSheet(clip: $0) }
        .sheet(isPresented: $showBulkSchedule) {
            BulkScheduleSheet(clipIDs: selectedIDs) { exitSelection() }
        }
        .alert("New group", isPresented: $showNewGroup) {
            TextField("Group name", text: $newGroupName)
            Button("Cancel", role: .cancel) { newGroupName = ""; pendingAssignAfterCreate = false }
            Button("Create") {
                let g = store.createClipGroup(newGroupName)
                newGroupName = ""
                if pendingAssignAfterCreate {
                    store.assignClips(selectedIDs, toGroup: g.id); pendingAssignAfterCreate = false
                    exitSelection()
                } else {
                    groupFilter = .group(g.id)
                }
            }
        } message: { Text("Organize clips into a collection you can filter by later.") }
        .confirmationDialog("Delete \(selectedIDs.count) clip\(selectedIDs.count == 1 ? "" : "s")?",
                            isPresented: $showBulkDelete, titleVisibility: .visible) {
            Button("Delete \(selectedIDs.count)", role: .destructive) {
                store.deleteClips(selectedIDs); exitSelection()
            }
            Button("Cancel", role: .cancel) {}
        } message: { Text("This removes them and any scheduled times. It can't be undone.") }
        .onChange(of: router.pendingOpenClipId) { _, id in openPending(id) }
        .onAppear { openPending(router.pendingOpenClipId) }
    }

    // MARK: filter + select controls

    private var controlRow: some View {
        HStack(spacing: Space.sm) {
            Menu {
                Picker("Group", selection: $groupFilter) {
                    Label("All clips", systemImage: "rectangle.stack").tag(ClipGroupFilter.all)
                    if !store.clipGroups.isEmpty || store.clips.contains(where: { $0.groupId != nil }) {
                        Label("Ungrouped", systemImage: "tray").tag(ClipGroupFilter.ungrouped)
                    }
                    ForEach(store.clipGroups) { g in
                        Text(g.name).tag(ClipGroupFilter.group(g.id))
                    }
                }
                if case .group(let id) = groupFilter {
                    Divider()
                    Button(role: .destructive) { store.deleteClipGroup(id); groupFilter = .all } label: {
                        Label("Delete this group", systemImage: "trash")
                    }
                }
                Divider()
                Button { showNewGroup = true } label: { Label("New group…", systemImage: "plus") }
            } label: {
                HStack(spacing: 5) {
                    Text(groupFilterLabel).font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                    Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Palette.textTertiary)
                }
                .padding(.horizontal, Space.md).padding(.vertical, 8)
                .background(Palette.surfaceSunken, in: Capsule())
            }
            .accessibilityIdentifier("library.groupFilter")
            Spacer()
            Button {
                if selecting { exitSelection() } else { selecting = true }
            } label: {
                Text(selecting ? "Done" : "Select")
                    .font(AppFont.callout.weight(.semibold)).foregroundStyle(Palette.accent)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("library.selectToggle")
        }
    }

    private var groupFilterLabel: String {
        switch groupFilter {
        case .all: return "All clips"
        case .ungrouped: return "Ungrouped"
        case .group(let id): return store.clipGroups.first(where: { $0.id == id })?.name ?? "Group"
        }
    }

    private func selectionOverlay(_ c: Clip) -> some View {
        let on = selectedIDs.contains(c.id)
        return ZStack(alignment: .topTrailing) {
            RoundedRectangle(cornerRadius: Radius.sm, style: .continuous)
                .fill(on ? Palette.accent.opacity(0.18) : Color.black.opacity(0.001))
                .overlay { if on {
                    RoundedRectangle(cornerRadius: Radius.sm, style: .continuous)
                        .strokeBorder(Palette.accent, lineWidth: 2)
                } }
            Image(systemName: on ? "checkmark.circle.fill" : "circle")
                .font(.system(size: 20, weight: .semibold))
                .foregroundStyle(on ? Palette.accent : .white)
                .background(Circle().fill(on ? .white : Color.black.opacity(0.28)).frame(width: 20, height: 20))
                .padding(6)
        }
    }

    // MARK: bulk action bar

    private var bulkBar: some View {
        HStack(spacing: Space.md) {
            Text("\(selectedIDs.count) selected")
                .font(AppFont.callout.weight(.semibold)).foregroundStyle(Palette.textSecondary)
            Spacer()
            Menu {
                ForEach(store.clipGroups) { g in
                    Button { store.assignClips(selectedIDs, toGroup: g.id); exitSelection() } label: {
                        Label(g.name, systemImage: "folder")
                    }
                }
                Button { store.assignClips(selectedIDs, toGroup: nil); exitSelection() } label: {
                    Label("Remove from group", systemImage: "folder.badge.minus")
                }
                Divider()
                Button { pendingAssignAfterCreate = true; showNewGroup = true } label: {
                    Label("New group…", systemImage: "plus")
                }
            } label: { bulkIcon("folder.badge.plus", "Group") }
                .disabled(selectedIDs.isEmpty)
            bulkButton("calendar", "Post", enabled: selectedReadyCount > 0) { showBulkSchedule = true }
            bulkButton("trash", "Delete", tint: Palette.critical, enabled: !selectedIDs.isEmpty) {
                showBulkDelete = true
            }
        }
        .padding(.horizontal, Space.lg).padding(.vertical, Space.sm)
        .frame(maxWidth: .infinity)
        .background(.ultraThinMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
        .shadow(color: Palette.shadowCool.opacity(0.18), radius: 16, y: 6)
        .padding(.horizontal, Space.md).padding(.bottom, Space.sm)
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }

    private func bulkButton(_ icon: String, _ label: String, tint: Color = Palette.accent,
                            enabled: Bool, _ action: @escaping () -> Void) -> some View {
        Button(action: action) { bulkIcon(icon, label, tint: tint) }
            .buttonStyle(.plain).disabled(!enabled).opacity(enabled ? 1 : 0.35)
            .accessibilityIdentifier("library.bulk.\(label.lowercased())")
    }
    private func bulkIcon(_ icon: String, _ label: String, tint: Color = Palette.accent) -> some View {
        VStack(spacing: 2) {
            Image(systemName: icon).font(.system(size: 16, weight: .semibold))
            Text(label).font(.system(size: 10, weight: .medium))
        }.foregroundStyle(tint)
    }

    private func onCellTap(_ c: Clip) {
        if selecting {
            if selectedIDs.contains(c.id) { selectedIDs.remove(c.id) } else { selectedIDs.insert(c.id) }
        } else {
            detail = c
        }
    }
    private func exitSelection() { selecting = false; selectedIDs = [] }

    /// "Ready in about N min" — the server's estimate minus elapsed, floored at 1 min;
    /// falls back to the generic line when no estimate exists (old backend).
    private func renderingEtaLine(_ group: [Clip]) -> String {
        if group.contains(where: { $0.uploading }) {
            return "Uploading your take — the AI starts editing the moment it lands."
        }
        let remaining = group.compactMap { c -> Int? in
            guard let eta = c.etaSeconds else { return nil }
            // Anchor at when the estimate was TAKEN — the server value is already
            // remaining-from-then; subtracting since createdAt double-counted.
            let anchor = c.etaSetAt ?? c.createdAt
            return max(0, eta - Int(Date().timeIntervalSince(anchor)))
        }.max()
        guard let remaining else {
            return "The AI is on it — you'll get a notification when it's done."
        }
        let mins = max(1, Int((Double(remaining) / 60.0).rounded(.up)))
        return "The AI is editing — ready in about \(mins) min. We'll notify you."
    }

    private func openPending(_ id: UUID?) {
        guard let id, let clip = store.clips.first(where: { $0.id == id }) else { return }
        router.pendingOpenClipId = nil
        detail = clip
    }
}

struct ClipCell: View {
    let clip: Clip
    var body: some View {
        HStack(spacing: 0) {
            // Leading status rail — color-coded by clip state
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(clip.status.railColor)
                .frame(width: 3)
                .padding(.vertical, Space.sm)

            HStack(spacing: Space.md) {
                ZStack {
                    LocalThumbnail(path: clip.thumbnailPath ?? clip.playbackLocalPath, isVideo: true)
                        .frame(width: 54, height: 72)
                    if clip.status == .rendering { ProgressView().tint(Palette.accent) }
                }
                .frame(width: 54, height: 72)
                VStack(alignment: .leading, spacing: 4) {
                    Text(clip.title.isEmpty ? clip.caption : clip.title)
                        .font(AppFont.body).foregroundStyle(Palette.textPrimary).lineLimit(2)
                    HStack(spacing: Space.sm) {
                        FormatTag(formatId: clip.formatId)
                        Text("\(clip.seconds)s").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        if clip.captioned {
                            Image(systemName: "captions.bubble").font(.system(size: 11)).foregroundStyle(Palette.accent)
                        }
                    }
                    // Build 45: an in-pipeline clip shows the live PipelineTimeline (real
                    // stage + progress) instead of a single frozen word; everything else
                    // keeps its plain why-line.
                    if let pp = PipelineProgress.from(clip), !pp.isFailed {
                        PipelineTimeline(progress: pp).padding(.top, 2)
                    } else {
                        Text(clip.status.whyLine)
                            .font(AppFont.micro).tracking(0.2)
                            .foregroundStyle(clip.status.railColor.opacity(0.8))
                    }
                }
                Spacer()
            }
            .padding(Space.md)
        }
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
        .shadow(color: Palette.shadowWarm.opacity(0.07), radius: 18, x: 0, y: 8)
    }
}

struct ClipGridCell: View {
    let clip: Clip
    var body: some View {
        ZStack(alignment: .bottom) {
            // Thumbnail
            LocalThumbnail(path: clip.thumbnailPath ?? clip.playbackLocalPath, isVideo: true,
                           remoteImageURL: clip.thumbnailURL)
                .aspectRatio(9/16, contentMode: .fill)
                .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))

            // Bottom gradient + status
            LinearGradient(colors: [.clear, .black.opacity(0.6)],
                           startPoint: .top, endPoint: .bottom)
                .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))

            VStack(alignment: .leading, spacing: 3) {
                // Build 45: compact rails on in-pipeline clips so a grid tile also shows
                // live motion, not a frozen "UPLOADING".
                if let pp = PipelineProgress.from(clip), !pp.isFailed {
                    PipelineTimeline(progress: pp, compact: true, showLine: false)
                }
                HStack {
                    Text(statusLabel).font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.white.opacity(0.9))
                    Spacer()
                    Text("\(clip.seconds)s").font(.system(size: 9)).foregroundStyle(.white.opacity(0.7))
                }
                // Very subtle "finished editing" timestamp so a finished clip is scannable by
                // when it landed. Only shown once the edit is done and a stamp exists.
                if let finishedAgo {
                    Text(finishedAgo).font(.system(size: 8, weight: .medium)).monospacedDigit()
                        .foregroundStyle(.white.opacity(0.45))
                }
            }
            .padding(6)
        }
        .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 0.5))
    }
    private var statusLabel: String {
        switch clip.status {
        case .draft:     return "DRAFT"
        case .ready:     return "READY"
        case .scheduled: return "SCHED"
        case .posted:    return "POSTED"
        case .rendering:
            // Build 45: name the real phase (UPLOAD/ANALYZE/EDIT/RENDER) rather than
            // one static word for the whole minute-long pipeline.
            return (PipelineProgress.from(clip)?.active.label ?? "Working").uppercased()
        case .failed:    return "FAILED"
        }
    }
    /// "3h ago" / "just now" — only for finished clips that carry a stamp (old clips → nil).
    private var finishedAgo: String? {
        guard let f = clip.finishedAt,
              clip.status == .ready || clip.status == .scheduled || clip.status == .posted
        else { return nil }
        return ClipTimeFormat.relative.localizedString(for: f, relativeTo: Date())
    }
}

/// Shared abbreviated relative-time formatter ("3h ago") for finished-clip stamps.
enum ClipTimeFormat {
    static let relative: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f
    }()
}

struct ClipDetailSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(\.dismiss) private var dismiss
    let clip: Clip
    @State private var caption: String
    @State private var showDelete = false
    @State private var showTweak = false
    @State private var showEditor = false

    init(clip: Clip) {
        self.clip = clip
        _caption = State(initialValue: clip.caption)
    }

    /// Live view of this clip — the sheet captures an immutable copy at present
    /// time, but tweaks mutate the store's clip (status/remoteURL) while the
    /// sheet is up. Reading through the store keeps the player + actions honest.
    private var current: Clip {
        store.clips.first(where: { $0.id == clip.id }) ?? clip
    }

    /// Draft-aware mode: a draft is a half-finished take — the only forward action
    /// is picking it back up in the Film flow (plus delete). No schedule/share/caption.
    private var isDraft: Bool { clip.status == .draft }

    // A shareable file/URL for export to Photos, Messages, the platform apps, etc.
    // UX-C1: for server-rendered clips share the RENDER (cached file, else its URL) —
    // never the raw take, which is what localVideoPath always is.
    private var shareURL: URL? {
        if let p = current.playbackLocalPath { return MediaStore.url(for: p) }
        if let r = current.playbackRemoteURL, let u = URL(string: r) { return u }
        return nil
    }

    /// A custom, on-brand action pill (Share / Delete) — GhostButton's card look at a
    /// compact height, tintable so Delete reads destructive.
    private func clipActionLabel(_ title: String, systemImage: String, tint: Color) -> some View {
        HStack(spacing: Space.xs) {
            Image(systemName: systemImage).font(.system(size: 14, weight: .medium))
            Text(title).font(AppFont.callout)
        }
        .foregroundStyle(tint)
        .frame(maxWidth: .infinity).frame(height: 46)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    ZStack {
                        // UX-C1: play the RENDER for server-rendered clips (cached file
                        // first, stream fallback); raw take only for drafts/imported.
                        // UX-D2: a staged tweak PREVIEW (uncommitted candidate) wins over
                        // everything while it exists — badged so it can't be mistaken
                        // for the committed cut.
                        ClipPreviewPlayer(path: current.previewURL == nil ? current.playbackLocalPath : nil,
                                          remoteURL: current.previewURL ?? current.playbackRemoteURL)
                            // Re-create the player when a tweak lands a NEW render URL,
                            // the render cache download completes, or a preview stages.
                            .id((current.previewURL ?? "") + (current.remoteURL ?? "")
                                + (current.renderLocalPath ?? "") + (current.localVideoPath ?? ""))
                        if current.previewURL != nil {
                            VStack {
                                HStack {
                                    Text("PREVIEW")
                                        .font(.system(size: 10, weight: .bold)).tracking(1.0)
                                        .foregroundStyle(.white)
                                        .padding(.horizontal, 8).padding(.vertical, 4)
                                        .background(Palette.accent.opacity(0.9))
                                        .clipShape(Capsule())
                                    Spacer()
                                }
                                Spacer()
                            }
                            .padding(Space.sm)
                            .allowsHitTesting(false)
                        }
                        if current.status == .rendering, let pp = PipelineProgress.from(current) {
                            // Build 46: the real pipeline timeline lives here too, not a bare
                            // spinner — a light chip pinned to the bottom of the player so it
                            // reads on the video and matches the Library cards.
                            // build 52: rounded so the dim overlay follows the player's
                            // founder corners instead of squaring them off during editing.
                            RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                                .fill(.black.opacity(0.35))
                            VStack {
                                Spacer()
                                PipelineTimeline(progress: pp, compact: true)
                                    .padding(.horizontal, Space.md).padding(.vertical, 10)
                                    .background(.ultraThinMaterial)
                                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                                    .padding(Space.md)
                            }
                        }
                    }
                    // 9:16 container so the portrait render fills it exactly — no
                    // pillarbox bars. Capped height keeps the chat input + actions in view;
                    // centered horizontally, it reads like a proper vertical reel.
                    .aspectRatio(9.0 / 16.0, contentMode: .fit)
                    .frame(maxWidth: .infinity, maxHeight: 500)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))

                    // UX-D1: the tweak chat is the clip's front door, not a buried menu
                    // entry — an input-shaped affordance right under the player. This is
                    // the ONLY AI-tweak entry point, so it gates on jobId (any live
                    // server job can be tweaked), not isServerRendered — the stricter
                    // gate left clips with a jobId but no remote render URL (e.g. the
                    // demo clip) with no AI entry at all once the duplicate button went.
                    if current.status == .ready && clip.jobId != nil && !isDraft {
                        Button { showTweak = true } label: {
                            HStack(spacing: Space.sm) {
                                Image(systemName: "wand.and.stars")
                                    .font(.system(size: 14)).foregroundStyle(Palette.accent)
                                Text("Tell the editor what to change…")
                                    .font(AppFont.callout).foregroundStyle(Palette.textTertiary)
                                Spacer()
                            }
                            .padding(.horizontal, Space.md).padding(.vertical, 12)
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.pill, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: Radius.pill, style: .continuous)
                                .strokeBorder(Palette.hairline, lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("clip.tweakAffordance")
                    }
                    // Failed render → tell the creator WHY + let them retry (the
                    // backend still holds the source + EDL). No more silent spin.
                    if !isDraft, current.status == .failed {
                        VStack(alignment: .leading, spacing: Space.sm) {
                            Label(store.friendlyRenderError(current.lastError, detail: current.lastErrorDetail), systemImage: "exclamationmark.triangle")
                                .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                            // Liveness v2: an upload that died before a job existed has
                            // jobId nil but the take on disk — retryClipJob recovers it via
                            // resubmitFailedClip, so show the button for that case too
                            // (previously hidden → an unretryable dead card).
                            if clip.jobId != nil || clip.localVideoPath != nil {
                                PrimaryButton(title: "Try again", systemImage: "arrow.clockwise") {
                                    Task { await store.retryClipJob(clip) }
                                }
                                .accessibilityIdentifier("clip.retry")
                            }
                        }
                        .padding(Space.md)
                        .background(Palette.surfaceRaised)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    }

                    // Edit tooling — only for server-edited clips whose job is still
                    // alive (jobId == nil means the offline mock engine). AI tweaks have
                    // exactly ONE entry point: the input-shaped affordance under the
                    // player above (a second "Tweak with AI" button here opened the
                    // identical sheet — pure duplication, removed).
                    if !isDraft, clip.jobId != nil, current.status == .ready || current.status == .rendering {
                        GhostButton(title: "Edit manually", systemImage: "slider.horizontal.3") {
                            showEditor = true
                        }
                        .accessibilityIdentifier("clip.editManual")
                    }

                    // Custom Share / Delete actions — first-class, on-brand pills instead of
                    // a buried Apple-native ellipsis menu. build 52: Delete now shows for
                    // DRAFTS too (it was gated behind !isDraft, leaving drafts un-deletable
                    // from the Library — the reported bug); Share stays non-draft only (a
                    // half-finished take has nothing shareable yet).
                    HStack(spacing: Space.sm) {
                        if !isDraft, let shareURL {
                            ShareLink(item: shareURL) {
                                clipActionLabel("Share", systemImage: "square.and.arrow.up",
                                                tint: Palette.textPrimary)
                            }
                            .accessibilityIdentifier("clip.share")
                        }
                        Button { showDelete = true } label: {
                            clipActionLabel(isDraft ? "Delete draft" : "Delete",
                                            systemImage: "trash", tint: Palette.critical)
                        }
                        .buttonStyle(PressableStyle(dim: 0.7))
                        .accessibilityIdentifier("clip.delete")
                    }

                    if isDraft {
                        // Draft note — no caption/schedule tooling until the take is finished.
                        let hasFootage = clip.localVideoPath.map {
                            FileManager.default.fileExists(atPath: MediaStore.url(for: $0).path)
                        } ?? false
                        Text(hasFootage
                             ? "Your take is saved here. Send it to the editor whenever you're ready."
                             : "Saved mid-take. Pick up right where you left off — your script is queued in Film.")
                            .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    } else {
                        // Editable caption — creators tweak the copy before it goes out.
                        VStack(alignment: .leading, spacing: Space.sm) {
                            SectionLabel(text: "Caption", accent: Palette.accent)
                            TextField("Caption", text: $caption, axis: .vertical)
                                .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                                .lineLimit(2...6)
                                .padding(Space.md)
                                .background(Palette.surfaceRaised)
                                .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                                .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                                    .strokeBorder(Palette.hairline, lineWidth: 1))
                                .accessibilityIdentifier("clip.caption")
                        }
                    }

                    if !isDraft, !clip.captionLines.isEmpty {
                        VStack(alignment: .leading, spacing: 6) {
                            SectionLabel(text: "Auto-captions", accent: Palette.accent)
                            ForEach(Array(clip.captionLines.enumerated()), id: \.offset) { _, line in
                                Text(line).font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                            }
                        }
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle(isDraft ? "Draft" : "Clip").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                // Share + Delete moved to custom in-body pills (clip.share / clip.delete);
                // the native ellipsis menu is gone.
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        if !isDraft { store.updateClipCaption(clip, caption: caption) }
                        dismiss()
                    }
                }
            }
            .safeAreaInset(edge: .bottom) {
                if isDraft {
                    // Build 46: a draft that HAS footage (recorded or uploaded) goes
                    // straight to the editor — routing it back to Film by scriptId used to
                    // drop an uploaded video's footage entirely. Script-only drafts (no
                    // take yet) still route to Film to finish recording.
                    let hasFootage = clip.localVideoPath.map {
                        FileManager.default.fileExists(atPath: MediaStore.url(for: $0).path)
                    } ?? false
                    if hasFootage {
                        PrimaryButton(title: "Send to editor", systemImage: "wand.and.stars") {
                            store.submitDraft(clip)
                            dismiss()
                            router.selectedTab = .library
                        }
                        .accessibilityIdentifier("library.sendDraftToEditor")
                        .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
                        .background(.ultraThinMaterial)
                    } else {
                        PrimaryButton(title: "Finish this take", systemImage: "video.fill") {
                            router.pendingFilmScriptId = clip.scriptId
                            dismiss()
                            router.showFilm = true
                        }
                        .accessibilityIdentifier("library.finishDraft")
                        .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
                        .background(.ultraThinMaterial)
                    }
                } else if current.status == .ready {
                    PrimaryButton(title: "Schedule this clip", systemImage: "calendar") {
                        store.updateClipCaption(clip, caption: caption)
                        router.pendingScheduleClipId = clip.id
                        dismiss(); router.selectedTab = .performance
                    }
                    .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
                    .background(.ultraThinMaterial)
                }
            }
            .marqueConfirm($showDelete, title: isDraft ? "Delete this draft?" : "Delete this clip?",
                           message: isDraft ? "The recording saved with this draft will be discarded. This can't be undone."
                                            : "This removes the clip and any times it's scheduled. This can't be undone.",
                           confirm: "Delete", destructive: true) {
                store.deleteClip(clip); dismiss()
            }
            .sheet(isPresented: $showTweak) {
                TweakChatSheet(clip: clip, autoFocus: true)
                    .presentationDetents([.medium, .large])   // UX-D1: chat over the player
            }
            .fullScreenCover(isPresented: $showEditor) { ProEditorView(clip: clip) }
        }
    }
}

// MARK: - Media corpus (bulk import; the AI references this)

struct MediaSection: View {
    @Environment(AppStore.self) private var store
    @State private var picked: [PhotosPickerItem] = []
    @State private var importing = false
    @State private var edit: MediaAsset?
    // Fixed 3-column square grid — the old .adaptive + aspectRatio(.fill) let each cell take
    // its image's natural shape, so rows came out ragged with bleed/overlap.
    private let cols = [GridItem(.flexible(), spacing: Space.sm),
                        GridItem(.flexible(), spacing: Space.sm),
                        GridItem(.flexible(), spacing: Space.sm)]

    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            VStack(alignment: .leading, spacing: Space.xs) {
                SectionLabel(text: "Your media", accent: Palette.accent)
                Text("Your photos and videos. The editor pulls from these for B-roll.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
            }

            // A hairline ghost, not a hero button — importing reference media is a
            // secondary action; the big ink slab overpowered the whole section.
            PhotosPicker(selection: $picked, maxSelectionCount: 40, matching: .any(of: [.images, .videos])) {
                HStack(spacing: Space.sm) {
                    if importing { ProgressView().tint(Palette.textSecondary) }
                    else { Image(systemName: "plus").font(.system(size: 13, weight: .medium)) }
                    Text(importing ? "Importing…" : "Import media").font(AppFont.callout)
                }
                .foregroundStyle(Palette.textPrimary).frame(maxWidth: .infinity).frame(height: 48)
                .background(Palette.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
            }
            .accessibilityIdentifier("library.importMedia")
            .onChange(of: picked) { _, items in
                guard !items.isEmpty else { return }
                importing = true
                Task {
                    let assets = await importPickedMedia(items)
                    store.addMedia(assets); picked = []; importing = false
                }
            }

            if store.media.isEmpty {
                EmptyStateView(icon: "photo.on.rectangle.angled", title: "No media yet",
                               message: "Import a batch above to build your reference library.")
            } else {
                Text("\(store.media.count) item\(store.media.count == 1 ? "" : "s") in your media library")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                LazyVGrid(columns: cols, spacing: Space.sm) {
                    ForEach(store.media) { m in
                        Button { edit = m } label: { mediaCell(m) }
                            .buttonStyle(.plain)
                    }
                }
            }
        }
        .sheet(item: $edit) { MediaEditSheet(asset: $0) }
    }

    /// A guaranteed-square cell: Color.clear pins the 1:1 frame, the thumbnail fills it and
    /// is clipped — so mixed portrait/landscape media all render as an even grid.
    private func mediaCell(_ m: MediaAsset) -> some View {
        Color.clear
            .aspectRatio(1, contentMode: .fit)
            .overlay(LocalThumbnail(path: m.thumbnailPath ?? m.localPath, isVideo: m.isVideo).scaledToFill())
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(alignment: .bottomLeading) { kindChip(m) }
            .overlay(alignment: .topTrailing) { analysisBadge(m) }
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 0.5))
            .accessibilityIdentifier("library.mediaCell")
    }

    private func kindChip(_ m: MediaAsset) -> some View {
        Text(m.kind.label).font(.system(size: 9, weight: .semibold))
            .foregroundStyle(.white).padding(.horizontal, 5).padding(.vertical, 2)
            .background(Palette.ink.opacity(0.6)).clipShape(Capsule()).padding(4)
    }

    /// I-5: analysis-state badge — ✓ analyzed, spinner while running, ! on failure, nothing yet.
    @ViewBuilder private func analysisBadge(_ m: MediaAsset) -> some View {
        switch (store.media.first { $0.id == m.id }?.analysisStatus ?? m.analysisStatus) {
        case .done:
            Image(systemName: "checkmark").font(.system(size: 8, weight: .bold))
                .foregroundStyle(.white).frame(width: 16, height: 16)
                .background(Circle().fill(Palette.ink)).padding(4)
        case .analyzing:
            ProgressView().scaleEffect(0.6).frame(width: 16, height: 16)
                .background(Circle().fill(Palette.ink.opacity(0.5))).padding(4)
        case .failed:
            Image(systemName: "exclamationmark").font(.system(size: 8, weight: .bold))
                .foregroundStyle(.white).frame(width: 16, height: 16)
                .background(Circle().fill(Palette.critical)).padding(4)
        case .none:
            EmptyView()
        }
    }
}

struct MediaEditSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let asset: MediaAsset
    @State private var kind: MediaKind
    @State private var note: String
    init(asset: MediaAsset) {
        self.asset = asset
        _kind = State(initialValue: asset.kind)
        _note = State(initialValue: asset.note)
    }
    /// I-5: the live asset from the store so analysis results appear reactively (the passed
    /// `asset` is a value snapshot that never updates).
    private var live: MediaAsset { store.media.first { $0.id == asset.id } ?? asset }
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    LocalThumbnail(path: asset.thumbnailPath ?? asset.localPath, isVideo: asset.isVideo)
                        .frame(height: 280)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                    SectionLabel(text: "What is this?", accent: Palette.accent)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: Space.sm) {
                            ForEach(MediaKind.allCases) { k in
                                Button { kind = k } label: { Chip(text: k.label, selected: kind == k) }.buttonStyle(.plain)
                            }
                        }
                    }
                    SectionLabel(text: "Tag (optional)")
                    TextField("e.g. gym, office, on stage", text: $note).marqueField()
                    // AI Analysis section (I-5: reads the LIVE asset so results appear reactively)
                    if live.analysisStatus == .analyzing {
                        HStack(spacing: Space.sm) {
                            ProgressView().scaleEffect(0.8)
                            Text("Analyzing…").font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                        }
                    } else if live.analysisStatus == .done {
                        VStack(alignment: .leading, spacing: Space.sm) {
                            SectionLabel(text: "AI description", accent: Palette.accent)
                            Text(live.aiDescription).font(AppFont.body).foregroundStyle(Palette.textPrimary)
                            if !live.onScreenText.isEmpty {
                                SectionLabel(text: "On-screen text", accent: Palette.accent)
                                Text(live.onScreenText).font(AppFont.body).foregroundStyle(Palette.textSecondary)
                            }
                            SectionLabel(text: "B-roll fit", accent: Palette.accent)
                            HStack(spacing: Space.sm) {
                                GeometryReader { geo in
                                    ZStack(alignment: .leading) {
                                        Capsule().fill(Palette.hairline).frame(height: 6)
                                        Capsule()
                                            .fill(live.brollSuitability > 60 ? Palette.accent : Palette.gold)
                                            .frame(width: geo.size.width * CGFloat(live.brollSuitability) / 100, height: 6)
                                    }
                                }.frame(height: 6)
                                Text("\(live.brollSuitability)%").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            }
                            if !live.brollSuitabilityReason.isEmpty {
                                Text(live.brollSuitabilityReason).font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                            }
                            if !live.aiTags.isEmpty {
                                SectionLabel(text: "Auto-tags", accent: Palette.accent)
                                ScrollView(.horizontal, showsIndicators: false) {
                                    HStack(spacing: Space.sm) {
                                        ForEach(live.aiTags, id: \.self) { tag in Chip(text: tag) }
                                    }
                                }
                            }
                        }
                    } else {
                        // .none / .failed — offer a manual analyze (retry on failed).
                        Button { store.ensureMediaAnalyzed(live) } label: {
                            Label(live.analysisStatus == .failed ? "Analysis failed — retry" : "Analyze with AI",
                                  systemImage: "sparkles")
                                .font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                                .frame(maxWidth: .infinity).frame(height: 44)
                                .background(Palette.surfaceRaised)
                                .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                                .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                                    .strokeBorder(Palette.hairline, lineWidth: 1))
                        }
                        .buttonStyle(PressableStyle()).accessibilityIdentifier("media.analyzeNow")
                    }
                    Button(role: .destructive) { store.removeMedia(asset); dismiss() } label: {
                        Text("Remove from library").font(AppFont.callout).foregroundStyle(Palette.critical)
                    }
                    .padding(.top, Space.sm)
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Media").navigationBarTitleDisplayMode(.inline)
            .onAppear { store.ensureMediaAnalyzed(asset) }   // I-5: lazy — analyze on first open
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Save") {
                        var a = asset; a.kind = kind; a.note = note.trimmingCharacters(in: .whitespaces)
                        store.updateMedia(a); dismiss()
                    }
                }
            }
        }
    }
}

extension ClipStatus {
    static var allOrder: [ClipStatus] { [.draft, .ready, .rendering, .scheduled, .posted, .failed] }
    var title: String {
        switch self {
        case .draft: return "Drafts"
        case .ready: return "Ready"
        case .rendering: return "Editing"
        case .scheduled: return "Scheduled"
        case .posted: return "Posted"
        case .failed: return "Needs attention"
        }
    }
    var stageLabel: String {
        switch self {
        case .draft:     return "Draft"
        case .rendering: return "Editing…"
        case .ready:     return "Ready"
        case .scheduled: return "Scheduled"
        case .posted:    return "Posted"
        case .failed:    return "Failed"
        }
    }
    var railColor: Color {
        switch self {
        case .draft:     return Palette.warning
        case .ready:     return Palette.accent
        case .rendering: return Palette.textTertiary
        case .scheduled: return Palette.scheduled
        case .posted:    return Palette.positive
        case .failed:    return Palette.critical
        }
    }
    var whyLine: String {
        switch self {
        case .draft:     return "Saved mid-take — finish it in Film"
        case .ready:     return "Ready to schedule"
        case .rendering: return "The AI is editing your clip…"
        case .scheduled: return "Scheduled to post"
        case .posted:    return "Posted"
        case .failed:    return "Needs attention"
        }
    }
}

// MARK: - Bulk schedule (build 59): one time + platforms applied to all selected clips.

struct BulkScheduleSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let clipIDs: Set<UUID>
    var onDone: () -> Void = {}

    @State private var platforms: Set<SocialPlatform> = [.instagram, .tiktok]
    @State private var date = Calendar.current.date(bySettingHour: 18, minute: 0, second: 0, of: Date()) ?? Date()
    @State private var autoCaptions = true
    @State private var posting = false
    @State private var showConnect = false

    private var readyCount: Int {
        store.clips.filter { clipIDs.contains($0.id) && $0.status == .ready }.count
    }
    private var hasPostableAccount: Bool {
        store.brand.connectedAccounts.contains { $0.canPublish }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    Text("^[\(readyCount) clip](inflect: true) will be scheduled to the same time and platforms.")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)

                    SectionLabel(text: "When")
                    MarqueTimePicker(time: $date)

                    SectionLabel(text: "Platforms")
                    HStack(spacing: Space.sm) {
                        ForEach(SocialPlatform.allCases) { p in
                            Button {
                                if platforms.contains(p) { platforms.remove(p) } else { platforms.insert(p) }
                            } label: { Chip(text: p.label, selected: platforms.contains(p)) }
                                .buttonStyle(.plain)
                        }
                    }

                    Toggle(isOn: $autoCaptions) {
                        Text("Auto-caption").font(AppFont.body).foregroundStyle(Palette.textPrimary)
                    }.tint(Palette.accent)

                    if !hasPostableAccount {
                        Button { showConnect = true } label: {
                            HStack(spacing: Space.sm) {
                                Image(systemName: "link").font(.system(size: 13, weight: .semibold))
                                Text("Connect an account to actually post — otherwise this just saves reminders.")
                                    .font(AppFont.caption)
                            }.foregroundStyle(Palette.warning)
                        }.buttonStyle(.plain)
                    }
                }
                .padding(Space.lg)
            }
            .navigationTitle("Schedule \(readyCount)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(posting ? "Scheduling…" : "Schedule") {
                        guard !platforms.isEmpty, readyCount > 0 else { return }
                        posting = true
                        Task {
                            await store.scheduleClips(clipIDs, on: date, platforms: Array(platforms),
                                                      autoCaptions: autoCaptions)
                            posting = false; onDone(); dismiss()
                        }
                    }.disabled(posting || platforms.isEmpty || readyCount == 0)
                }
            }
            .sheet(isPresented: $showConnect) { ConnectAccountsView() }
        }
    }
}
