import SwiftUI
import PhotosUI


struct LibraryView: View {
    @Environment(AppStore.self) private var store
    @State private var tabIndex = 0
    private let tabs = ["Clips", "Media"]
    // Build 60: selection state lives HERE (not in ClipsSection) so the bulk action
    // bar can overlay the SCREEN. Attached inside the ScrollView it scrolled with the
    // content and sat below the fold — "I can select but there's no option to post."
    @State private var selecting = false
    @State private var selectedIDs: Set<UUID> = []
    @State private var showBulkDelete = false
    @State private var showBulkSchedule = false
    @State private var showGroupAssign = false

    private var selectedReadyCount: Int {
        store.clips.filter { selectedIDs.contains($0.id) && $0.status == .ready }.count
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                // Stoic pushed-page header: eyebrow over the display title, left aligned.
                // (Both strings are matched by Maestro flows, so they stay verbatim.)
                VStack(alignment: .leading, spacing: Space.xs) {
                    DSEyebrow(text: "YOUR CREATIVE VAULT")
                    Text("Library").font(AppFont.pageTitle).tracking(-0.5)
                        .foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                }
                UnderlineTabBar(tabs: tabs, index: $tabIndex)
                switch tabIndex {
                case 1: MediaSection()
                default: ClipsSection(selecting: $selecting, selectedIDs: $selectedIDs)
                }
            }
            .screenPadding()
            .padding(.top, Space.sm)
            // Tab root: keep the last row clear of the overlaid MarqueTabBar.
            .padding(.bottom, MarqueTabBar.clearance + Space.lg)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        // Screen-anchored floating bulk bar — never scrolls, never below the fold.
        .overlay(alignment: .bottom) {
            if selecting && tabIndex == 0 {
                bulkBar
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(Motion.quick, value: selecting)
        .onChange(of: tabIndex) { _, _ in exitSelection() }
        .sheet(isPresented: $showBulkSchedule) {
            BulkScheduleSheet(clipIDs: selectedIDs) { exitSelection() }
        }
        // Build 61: groups get a real sheet (see GroupAssignSheet's note on why the old
        // Menu made the whole feature look missing).
        .sheet(isPresented: $showGroupAssign) {
            GroupAssignSheet(clipIDs: selectedIDs)
        }
        .confirmationDialog("Delete \(selectedIDs.count) clip\(selectedIDs.count == 1 ? "" : "s")?",
                            isPresented: $showBulkDelete, titleVisibility: .visible) {
            Button("Delete \(selectedIDs.count)", role: .destructive) {
                store.deleteClips(selectedIDs); exitSelection()
            }
            Button("Cancel", role: .cancel) {}
        } message: { Text("This removes them and any scheduled times. It can't be undone.") }
        // H-07: a tweak that outlived its poll window (or an app relaunch mid-render)
        // resolves here instead of spinning forever locally.
        .task { store.repollRenderingClips() }
    }

    private func exitSelection() { selecting = false; selectedIDs = [] }

    private var bulkBar: some View {
        HStack(spacing: Space.md) {
            Text("\(selectedIDs.count) selected")
                .font(AppFont.supporting.weight(.semibold)).foregroundStyle(Palette.textPrimary)
                .lineLimit(1).minimumScaleFactor(0.8)
            Spacer(minLength: Space.xs)
            // Build 61: a plain Button into a sheet, NOT a Menu. The Menu only ever listed
            // groups that already existed, so on a fresh install it opened to a single
            // "New group…" row — the owner read that as "grouping isn't built yet". The
            // sheet always shows the full picture (groups, membership, create row).
            Button { showGroupAssign = true } label: {
                bulkIcon("folder.badge.plus", "Group", tint: Palette.textPrimary, enabled: !selectedIDs.isEmpty)
            }
            .buttonStyle(PressableStyle(dim: 0.6)).disabled(selectedIDs.isEmpty)
            .accessibilityIdentifier("library.bulk.group")
            // Destructive = black trash glyph + confirm dialog (no red in the mono system).
            Button { showBulkDelete = true } label: {
                bulkIcon("trash", "Delete", tint: Palette.textPrimary, enabled: !selectedIDs.isEmpty)
            }
            .buttonStyle(PressableStyle(dim: 0.6)).disabled(selectedIDs.isEmpty)
            .accessibilityIdentifier("library.bulk.delete")
            // The forward action is the one filled capsule, trailing (Stoic CTA row).
            Button { showBulkSchedule = true } label: {
                HStack(spacing: 6) {
                    Image(systemName: "paperplane.fill").font(.system(size: 13, weight: .semibold))
                    Text("Post")
                }
            }
            .buttonStyle(DSCapsuleStyle(kind: .primary, height: 40))
            .disabled(selectedReadyCount == 0)
            .accessibilityIdentifier("library.bulk.post")
        }
        .padding(.leading, Space.lg).padding(.trailing, Space.sm).padding(.vertical, Space.sm)
        .background(Capsule().fill(Palette.surface))
        .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
        .shadow(color: Palette.shadowWarm.opacity(0.06), radius: 12, y: 2)
        .padding(.horizontal, Space.screenH)
        // ABOVE the floating tab bar, not under it. RootTabView overlays MarqueTabBar on
        // top of tab content and screens own their clearance (see MarqueTabBar.clearance)
        // — with only Space.sm here the whole bulk bar rendered BEHIND the tab bar
        // capsule: invisible, and its taps fell through to the tab bar's Film "+" button.
        // That was the owner's "select doesn't let me add clips to groups" — the Group
        // button existed but could never be seen or hit. Reproduced by
        // .maestro/library-groups.yaml before this padding; green after.
        .padding(.bottom, MarqueTabBar.clearance + Space.sm)
    }

    private func bulkIcon(_ icon: String, _ label: String, tint: Color = Palette.textPrimary,
                          enabled: Bool = true) -> some View {
        VStack(spacing: 2) {
            Image(systemName: icon).font(.system(size: 17, weight: .regular))
            Text(label).font(AppFont.caption)
        }
        .foregroundStyle(enabled ? tint : Palette.textTertiary)
        .frame(minWidth: 44, minHeight: 44)
        .contentShape(Rectangle())
    }
}

// MARK: - Clips (rendered, grouped by status, real posters, tap → player)

/// Which group the Library clip grid is filtered to (build 59).
enum ClipGroupFilter: Hashable { case all, ungrouped, group(UUID) }

struct ClipsSection: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var detail: Clip?
    // build 59/60: group filter here; selection state lives in LibraryView so the
    // bulk bar can anchor to the screen (see LibraryView.bulkBar).
    @State private var groupFilter: ClipGroupFilter = .all
    @Binding var selecting: Bool
    @Binding var selectedIDs: Set<UUID>
    @State private var showNewGroup = false
    @State private var newGroupName = ""

    private var hasFinishedClips: Bool {
        store.clips.contains { [.ready, .scheduled, .posted].contains($0.status) }
    }

    /// Clips passing the active group filter. Build 61: membership is a SET, so
    /// "ungrouped" means no groups at all and a group filter means "contains".
    private var filteredClips: [Clip] {
        switch groupFilter {
        case .all:            return store.clips
        case .ungrouped:      return store.clips.filter { $0.memberGroupIds.isEmpty }
        case .group(let id):  return store.clips.filter { $0.memberGroupIds.contains(id) }
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            if store.clips.isEmpty {
                EmptyStateView(icon: "rectangle.stack", title: "No clips yet",
                               message: "Tap Film below to record your first script. Drafts and edited clips land here.",
                               graphic: "ClipsIcon")
                Button { router.showFilm = true } label: {
                    Label("Create your first clip", systemImage: "video.badge.plus")
                }
                .buttonStyle(.dsPrimary)
                .frame(maxWidth: .infinity)
                .accessibilityIdentifier("library.createFirst")
            } else {
                controlRow
                let visible = filteredClips
                if visible.isEmpty {
                    // Stoic outline (empty) card.
                    Text(groupFilter == .ungrouped ? "No ungrouped clips."
                                                    : "This group is empty. Select clips and add them here.")
                        .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity)
                        .dsCard(.outline, radius: Radius.group)
                }
                ForEach(ClipStatus.allOrder, id: \.self) { status in
                    let group = visible.filter { $0.status == status }
                    if !group.isEmpty {
                        VStack(alignment: .leading, spacing: Space.md) {
                            VStack(alignment: .leading, spacing: Space.xs) {
                                // Status used to be carried by color; now glyph + label.
                                HStack(spacing: 6) {
                                    Image(systemName: status.statusGlyph)
                                        .font(.system(size: 12, weight: .semibold))
                                        .foregroundStyle(Palette.textSecondary)
                                        .accessibilityHidden(true)
                                    SectionLabel(text: status.title)
                                    Spacer(minLength: Space.sm)
                                    Text("\(group.count)")
                                        .font(AppFont.caption).monospacedDigit()
                                        .foregroundStyle(Palette.textSecondary)
                                        .accessibilityHidden(true)
                                }
                                if status == .rendering {
                                    Text(renderingEtaLine(group))
                                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            let cols = Array(repeating: GridItem(.flexible(), spacing: Space.sm), count: 3)
                            LazyVGrid(columns: cols, spacing: Space.sm) {
                                ForEach(Array(group.enumerated()), id: \.element.id) { i, c in
                                    Button { onCellTap(c) } label: {
                                        ClipGridCell(clip: c, groupColors: groupColors(for: c))
                                            .overlay { if selecting { selectionOverlay(c) } }
                                    }
                                    .buttonStyle(.plain)
                                    .accessibilityIdentifier("library.clip")
                                    // Capped: an uncapped index made deep cells wait
                                    // out the whole cascade (seconds) before appearing.
                                    .staggerReveal(min(i, 8))
                                }
                            }
                        }
                    }
                }
                if !hasFinishedClips {
                    VStack(alignment: .leading, spacing: Space.sm) {
                        Text("Finished clips land here, ready to schedule.")
                            .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Button { router.showFilm = true } label: {
                            Label("Film another clip", systemImage: "video.badge.plus")
                        }
                        .buttonStyle(.dsGhost)
                        .accessibilityIdentifier("library.filmAnother")
                    }
                }
                // Room so the floating action bar (stacked above the tab bar, whose
                // clearance the page already reserves) never covers the last row.
                if selecting { Color.clear.frame(height: 64) }
            }
        }
        .sheet(item: $detail) { ClipDetailSheet(clip: $0) }
        .alert("New group", isPresented: $showNewGroup) {
            TextField("Group name", text: $newGroupName)
            Button("Cancel", role: .cancel) { newGroupName = "" }
            Button("Create") {
                let g = store.createClipGroup(newGroupName)
                newGroupName = ""
                groupFilter = .group(g.id)
            }
        } message: { Text("Organize clips into a collection you can filter by later.") }
        .onChange(of: router.pendingOpenClipId) { _, id in openPending(id) }
        .onAppear { openPending(router.pendingOpenClipId) }
    }

    // MARK: filter + select controls

    private var controlRow: some View {
        HStack(spacing: Space.sm) {
            Menu {
                Picker("Group", selection: $groupFilter) {
                    Label("All clips", systemImage: "rectangle.stack").tag(ClipGroupFilter.all)
                    if !store.clipGroups.isEmpty || store.clips.contains(where: { !$0.memberGroupIds.isEmpty }) {
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
                // Build 66: flush-left, no capsule — the pill's internal padding pushed
                // the label off the left margin every other element on this screen sits
                // on (owner: "line up with the rest of the stuff on the left").
                HStack(spacing: 6) {
                    Text(groupFilterLabel).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .lineLimit(1).truncationMode(.tail)
                    Image(systemName: "chevron.down").font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Palette.textPrimary)
                }
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .accessibilityIdentifier("library.groupFilter")
            Spacer(minLength: Space.sm)
            // Selection = inversion: outline pill while browsing, ink pill while selecting.
            Button {
                if selecting { exitSelection() } else { selecting = true }
            } label: {
                Text(selecting ? "Done" : "Select")
                    .font(AppFont.supporting.weight(.semibold))
                    .foregroundStyle(selecting ? Palette.onInk : Palette.textPrimary)
                    .padding(.horizontal, 16).frame(height: 36)
                    .background(Capsule().fill(selecting ? Palette.ink : Palette.surface))
                    .overlay(Capsule().strokeBorder(selecting ? .clear : Palette.hairline, lineWidth: 1))
                    .contentShape(Capsule())
                    .animation(Motion.quick, value: selecting)
            }
            .buttonStyle(PressableStyle(dim: 0.8))
            .accessibilityIdentifier("library.selectToggle")
        }
    }

    /// Accents for the groups this clip belongs to, in the order the groups were created
    /// (so a clip's dots don't reshuffle between renders).
    private func groupColors(for c: Clip) -> [Color] {
        let member = Set(c.memberGroupIds)
        return store.clipGroups.filter { member.contains($0.id) }.map { $0.displayColor }
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
        // Over the poster (media), so the unselected ring is white-on-scrim; selected is
        // the ink check (inverts in dark mode) with an ink frame around the tile.
        return ZStack(alignment: .topTrailing) {
            RoundedRectangle(cornerRadius: Radius.tile, style: .continuous)
                .fill(on ? Color.black.opacity(0.28) : Color.black.opacity(0.001))
                .overlay { if on {
                    RoundedRectangle(cornerRadius: Radius.tile, style: .continuous)
                        .strokeBorder(Palette.ink, lineWidth: 3)
                } }
            ZStack {
                Circle().fill(on ? Palette.ink : Color.black.opacity(0.28))
                Circle().strokeBorder(on ? Palette.onInk : Palette.onNight, lineWidth: 1.5)
                if on {
                    Image(systemName: "checkmark")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(Palette.onInk)
                }
            }
            .frame(width: 22, height: 22)
            .padding(Space.sm)
        }
        .animation(Motion.quick, value: on)
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
            return "Uploading your take. The AI starts editing the moment it lands."
        }
        let remaining = group.compactMap { c -> Int? in
            guard let eta = c.etaSeconds else { return nil }
            // Anchor at when the estimate was TAKEN — the server value is already
            // remaining-from-then; subtracting since createdAt double-counted.
            let anchor = c.etaSetAt ?? c.createdAt
            return max(0, eta - Int(Date().timeIntervalSince(anchor)))
        }.max()
        guard let remaining else {
            return "The AI is on it. You'll get a notification when it's done."
        }
        let mins = max(1, Int((Double(remaining) / 60.0).rounded(.up)))
        return "The AI is editing. Ready in about \(mins) min, and we'll notify you."
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
        // Stoic timeline row: thumbnail leading, title + meta, status as glyph + wording
        // (the old color-coded rail is gone: meaning is carried by the glyph and line).
        HStack(spacing: Space.md) {
            ZStack {
                LocalThumbnail(path: clip.thumbnailPath ?? clip.playbackLocalPath, isVideo: true,
                               cornerRadius: Radius.cell)
                    .frame(width: 54, height: 72)
                if clip.status == .rendering { ProgressView().tint(Palette.textPrimary) }
            }
            .frame(width: 54, height: 72)
            VStack(alignment: .leading, spacing: 4) {
                Text(clip.title.isEmpty ? clip.caption : clip.title)
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary).lineLimit(2)
                HStack(spacing: Space.sm) {
                    FormatTag(formatId: clip.formatId)
                    Text("\(clip.seconds)s").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    if clip.captioned {
                        Image(systemName: "captions.bubble").font(.system(size: 11))
                            .foregroundStyle(Palette.textSecondary)
                            .accessibilityLabel("Captioned")
                    }
                }
                // Build 45: an in-pipeline clip shows the live PipelineTimeline (real
                // stage + progress) instead of a single frozen word; everything else
                // keeps its plain why-line.
                if let pp = PipelineProgress.from(clip), !pp.isFailed {
                    PipelineTimeline(progress: pp).padding(.top, 2)
                } else {
                    Label(clip.status.whyLine, systemImage: clip.status.statusGlyph)
                        .font(AppFont.caption)
                        .foregroundStyle(Palette.textSecondary)
                        .lineLimit(1).minimumScaleFactor(0.85)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(Space.rowPad)
        .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.surface))
    }
}

struct ClipGridCell: View {
    let clip: Clip
    /// Build 61: one dot per group this clip is filed under. Default empty so the cell
    /// stays usable from any call site that doesn't care about groups.
    var groupColors: [Color] = []
    var body: some View {
        ZStack(alignment: .bottom) {
            // Thumbnail (the only color on screen)
            LocalThumbnail(path: clip.thumbnailPath ?? clip.playbackLocalPath, isVideo: true,
                           remoteImageURL: clip.thumbnailURL, cornerRadius: Radius.tile)
                .aspectRatio(9/16, contentMode: .fill)
                .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))

            // Bottom scrim over the poster so the status strip reads on any frame.
            LinearGradient(colors: [.clear, .black.opacity(0.65)],
                           startPoint: .center, endPoint: .bottom)
                .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))

            VStack(alignment: .leading, spacing: 2) {
                // Build 45: compact rails on in-pipeline clips so a grid tile also shows
                // live motion, not a frozen "UPLOADING".
                if let pp = PipelineProgress.from(clip), !pp.isFailed {
                    PipelineTimeline(progress: pp, compact: true, showLine: false)
                }
                // Status = glyph + label (it used to be carried by color).
                HStack(spacing: 4) {
                    Image(systemName: clip.status.statusGlyph)
                        .font(.system(size: 10, weight: .semibold))
                        .accessibilityHidden(true)
                    Text(statusLabel).font(AppFont.eyebrow).tracking(0.8)
                        .lineLimit(1).minimumScaleFactor(0.7)
                }
                .foregroundStyle(Palette.onNight)
                HStack(spacing: 4) {
                    Text("\(clip.seconds)s").font(AppFont.caption).monospacedDigit()
                        .foregroundStyle(Palette.onNight.opacity(0.85))
                    // Very subtle "finished editing" timestamp so a finished clip is scannable
                    // by when it landed. Only shown once the edit is done and a stamp exists.
                    if let finishedAgo {
                        Text(finishedAgo).font(AppFont.caption).monospacedDigit()
                            .foregroundStyle(Palette.onNight.opacity(0.7))
                            .lineLimit(1).minimumScaleFactor(0.7)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Space.sm).padding(.bottom, Space.sm)
        }
        .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 0.5))
        // topLEADING on purpose: topTrailing is the selection checkmark and the bottom
        // strip is the status/duration line, so the group badge is alone in this corner.
        .overlay(alignment: .topLeading) { groupDots }
    }

    /// Group membership badge: a folder glyph + count on a dark scrim capsule (the old
    /// per-group colored dots are gone; black and white only). Sits over the poster.
    @ViewBuilder private var groupDots: some View {
        if !groupColors.isEmpty {
            HStack(spacing: 3) {
                Image(systemName: "folder.fill").font(.system(size: 9, weight: .semibold))
                Text("\(groupColors.count)").font(AppFont.caption.weight(.semibold)).monospacedDigit()
            }
            .foregroundStyle(Palette.onNight)
            .padding(.horizontal, 6).frame(height: 20)
            .background(Capsule().fill(Color.black.opacity(0.55)))
            .padding(Space.sm)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("In \(groupColors.count) group\(groupColors.count == 1 ? "" : "s")")
        }
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
    @State private var showVersions = false
    @State private var showPostNow = false
    // Share resolves the EDITED render to a local FILE first (a remote URL shares as a
    // link, not a video) — preparing covers the one-time download when the cache is cold.
    @State private var sharePreparing = false
    @State private var shareFileURL: URL?

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

    /// Stoic text-link label (glyph + word, centered). Used for the destructive Delete:
    /// black text + trash glyph, and the confirm dialog does the warning (no red).
    private func clipActionLabel(_ title: String, systemImage: String, tint: Color) -> some View {
        HStack(spacing: Space.sm) {
            Image(systemName: systemImage).font(.system(size: 15, weight: .regular))
            Text(title).font(AppFont.bodyText)
        }
        .foregroundStyle(tint)
        .frame(maxWidth: .infinity).frame(minHeight: 44)
        .contentShape(Rectangle())
    }

    // Which rows the actions card shows (reads state only; same gates as before).
    private var showsEditRow: Bool {
        !isDraft && clip.jobId != nil && (current.status == .ready || current.status == .rendering)
    }
    private var showsVersionsRow: Bool {
        !isDraft && clip.jobId != nil && !(current.renderHistory ?? []).isEmpty
    }
    private var showsShareRow: Bool { !isDraft && shareURL != nil }

    /// Sheet footer surface: canvas with a hairline top edge (no blur material).
    private func footerBar<V: View>(@ViewBuilder _ content: () -> V) -> some View {
        content()
            .padding(.horizontal, Space.screenH).padding(.top, Space.md).padding(.bottom, Space.sm)
            .frame(maxWidth: .infinity)
            .background(Palette.canvas.ignoresSafeArea(edges: .bottom))
            .overlay(alignment: .top) { Rectangle().fill(Palette.hairline).frame(height: 1) }
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
                                          remoteURL: current.previewURL ?? current.playbackRemoteURL,
                                          cornerRadius: Radius.tile,
                                          // Build 69: the manual editor covers this sheet —
                                          // pause the moment it opens (owner: video kept playing).
                                          suspended: showEditor)
                            // Re-create the player when a tweak lands a NEW render URL,
                            // the render cache download completes, or a preview stages.
                            .id((current.previewURL ?? "") + (current.remoteURL ?? "")
                                + (current.renderLocalPath ?? "") + (current.localVideoPath ?? ""))
                        if current.previewURL != nil {
                            VStack {
                                HStack {
                                    // Over video: dark scrim capsule, white tracked label.
                                    HStack(spacing: 4) {
                                        Image(systemName: "eye").font(.system(size: 10, weight: .semibold))
                                        Text("PREVIEW").font(AppFont.eyebrow).tracking(1.2)
                                    }
                                    .foregroundStyle(Palette.onNight)
                                    .padding(.horizontal, 10).frame(height: 24)
                                    .background(Capsule().fill(Color.black.opacity(0.6)))
                                    Spacer()
                                }
                                Spacer()
                            }
                            .padding(Space.md)
                            .allowsHitTesting(false)
                        }
                        if current.status == .rendering, let pp = PipelineProgress.from(current) {
                            // Build 46: the real pipeline timeline lives here too, not a bare
                            // spinner — a light chip pinned to the bottom of the player so it
                            // reads on the video and matches the Library cards.
                            // build 52: rounded so the dim overlay follows the player's
                            // founder corners instead of squaring them off during editing.
                            RoundedRectangle(cornerRadius: Radius.tile, style: .continuous)
                                .fill(.black.opacity(0.35))
                            VStack {
                                Spacer()
                                PipelineTimeline(progress: pp, compact: true)
                                    .padding(.horizontal, Space.md).padding(.vertical, 10)
                                    .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                                        .fill(Palette.surface))
                                    .padding(Space.md)
                            }
                        }
                    }
                    // 9:16 container so the portrait render fills it exactly — no
                    // pillarbox bars. Capped height keeps the chat input + actions in view;
                    // centered horizontally, it reads like a proper vertical reel.
                    .aspectRatio(9.0 / 16.0, contentMode: .fit)
                    .frame(maxWidth: .infinity, maxHeight: 500)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))

                    // UX-D1: the tweak chat is the clip's front door, not a buried menu
                    // entry — an input-shaped affordance right under the player. This is
                    // the ONLY AI-tweak entry point, so it gates on jobId (any live
                    // server job can be tweaked), not isServerRendered — the stricter
                    // gate left clips with a jobId but no remote render URL (e.g. the
                    // demo clip) with no AI entry at all once the duplicate button went.
                    if current.status == .ready && clip.jobId != nil && !isDraft {
                        // Stoic search-capsule shape: sunken capsule, glyph + placeholder.
                        Button { showTweak = true } label: {
                            HStack(spacing: Space.sm) {
                                Image(systemName: "wand.and.stars")
                                    .font(.system(size: 17, weight: .regular))
                                    .foregroundStyle(Palette.textPrimary)
                                Text("Tell the editor what to change…")
                                    .font(AppFont.bodyText).foregroundStyle(Palette.textTertiary)
                                    .lineLimit(1).minimumScaleFactor(0.85)
                                Spacer(minLength: 0)
                            }
                            .padding(.horizontal, Space.lg).frame(height: 52)
                            .background(Capsule().fill(Palette.surfaceSunken))
                            .contentShape(Capsule())
                        }
                        .buttonStyle(PressableStyle(dim: 0.8))
                        .accessibilityIdentifier("clip.tweakAffordance")
                    }
                    // Failed render → tell the creator WHY + let them retry (the
                    // backend still holds the source + EDL). No more silent spin.
                    if !isDraft, current.status == .failed {
                        VStack(alignment: .leading, spacing: Space.md) {
                            Label(store.friendlyRenderError(current.lastError, detail: current.lastErrorDetail), systemImage: "exclamationmark.triangle")
                                .font(AppFont.supporting).foregroundStyle(Palette.textPrimary)
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
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .dsCard(.surface, radius: Radius.group, padding: Space.rowPad)
                    }

                    // Actions as one Stoic grouped-rows card: Edit manually / Versions /
                    // Share. Same gates as before, only the chrome changed.
                    if showsEditRow || showsVersionsRow || showsShareRow {
                        DSGroup {
                            // Edit tooling — only for server-edited clips whose job is still
                            // alive (jobId == nil means the offline mock engine). AI tweaks have
                            // exactly ONE entry point: the input-shaped affordance under the
                            // player above (a second "Tweak with AI" button here opened the
                            // identical sheet — pure duplication, removed).
                            if showsEditRow {
                                DSRow(title: "Edit manually", systemImage: "slider.horizontal.3", action: {
                                    showEditor = true
                                })
                                .accessibilityIdentifier("clip.editManual")
                            }
                            if showsEditRow && (showsVersionsRow || showsShareRow) { DSRowDivider(inset: 56) }

                            // Build 66: past edit versions — a timeline of every AI/manual edit,
                            // any of which can be restored (server-side EDL undo + re-render).
                            if showsVersionsRow {
                                DSRow(title: "Versions", systemImage: "clock.arrow.circlepath", action: {
                                    showVersions = true
                                })
                                .accessibilityIdentifier("clip.versions")
                            }
                            if showsVersionsRow && showsShareRow { DSRowDivider(inset: 56) }

                            // Share stays non-draft only (a half-finished take has nothing
                            // shareable yet).
                            if showsShareRow {
                                DSRow(title: sharePreparing ? "Preparing…" : "Share",
                                      systemImage: "square.and.arrow.up", showsChevron: false, action: {
                                    guard !sharePreparing else { return }
                                    sharePreparing = true
                                    Task {
                                        shareFileURL = await store.shareableRenderFile(for: clip.id)
                                        sharePreparing = false
                                    }
                                })
                                .disabled(sharePreparing)
                                .accessibilityIdentifier("clip.share")
                            }
                        }
                    }

                    if isDraft {
                        // Draft note — no caption/schedule tooling until the take is finished.
                        let hasFootage = clip.localVideoPath.map {
                            FileManager.default.fileExists(atPath: MediaStore.url(for: $0).path)
                        } ?? false
                        Text(hasFootage
                             ? "Your take is saved here. Send it to the editor whenever you're ready."
                             : "Saved mid-take. Pick up right where you left off; your script is queued in Film.")
                            .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        // Editable caption — creators tweak the copy before it goes out.
                        VStack(alignment: .leading, spacing: Space.sm) {
                            SectionLabel(text: "Caption")
                                .padding(.horizontal, Space.rowPad)
                            TextField("Caption", text: $caption, axis: .vertical)
                                .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                                .tint(Palette.textPrimary)
                                .lineLimit(2...6)
                                .padding(Space.rowPad)
                                .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                                    .fill(Palette.surface))
                                .accessibilityIdentifier("clip.caption")
                        }
                    }

                    if !isDraft, !clip.captionLines.isEmpty {
                        VStack(alignment: .leading, spacing: Space.sm) {
                            SectionLabel(text: "Auto-captions")
                                .padding(.horizontal, Space.rowPad)
                            VStack(alignment: .leading, spacing: 6) {
                                ForEach(Array(clip.captionLines.enumerated()), id: \.offset) { _, line in
                                    Text(line).font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .dsCard(.surface, radius: Radius.group, padding: Space.rowPad)
                        }
                    }

                    // build 52: Delete shows for DRAFTS too (it was gated behind !isDraft,
                    // leaving drafts un-deletable). Stoic destructive = text link + confirm.
                    Button { showDelete = true } label: {
                        clipActionLabel(isDraft ? "Delete draft" : "Delete",
                                        systemImage: "trash", tint: Palette.textPrimary)
                    }
                    .buttonStyle(PressableStyle(dim: 0.5))
                    .accessibilityIdentifier("clip.delete")
                }
                .screenPadding().padding(.top, Space.sm).padding(.bottom, Space.xl)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle(isDraft ? "Draft" : "Clip").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .toolbar {
                // Stoic sheet title: lowercase with a period, centered.
                ToolbarItem(placement: .principal) {
                    Text(dsTitle(isDraft ? "Draft" : "Clip"))
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                }
                // Share + Delete moved to custom in-body pills (clip.share / clip.delete);
                // the native ellipsis menu is gone.
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        if !isDraft { store.updateClipCaption(clip, caption: caption) }
                        dismiss()
                    }
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
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
                        footerBar {
                            PrimaryButton(title: "Send to editor", systemImage: "wand.and.stars") {
                                store.submitDraft(clip)
                                dismiss()
                                router.selectedTab = .library
                            }
                            .accessibilityIdentifier("library.sendDraftToEditor")
                        }
                    } else {
                        footerBar {
                            PrimaryButton(title: "Finish this take", systemImage: "video.fill") {
                                router.pendingFilmScriptId = clip.scriptId
                                dismiss()
                                router.showFilm = true
                            }
                            .accessibilityIdentifier("library.finishDraft")
                        }
                    }
                } else if current.status == .ready {
                    // Stoic CTA row: outline secondary leading, primary trailing.
                    footerBar {
                        HStack(spacing: Space.sm) {
                            GhostButton(title: "Schedule", systemImage: "calendar") {
                                store.updateClipCaption(clip, caption: caption)
                                router.pendingScheduleClipId = clip.id
                                dismiss(); router.selectedTab = .performance
                            }
                            .accessibilityIdentifier("clip.schedule")
                            PrimaryButton(title: "Post now", systemImage: "paperplane.fill") {
                                store.updateClipCaption(clip, caption: caption)
                                showPostNow = true
                            }
                            .accessibilityIdentifier("clip.postNow")
                        }
                    }
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
            .sheet(isPresented: $showVersions) {
                VersionTimelineSheet(clipId: clip.id)
                    .presentationDetents([.medium, .large])
            }
            .sheet(isPresented: $showPostNow) {
                PostNowSheet(clip: clip, caption: caption) { dismiss() }
                    .presentationDetents([.medium])
            }
            .sheet(isPresented: Binding(get: { shareFileURL != nil },
                                        set: { if !$0 { shareFileURL = nil } })) {
                if let url = shareFileURL { ActivityShareSheet(items: [url]) }
            }
            .fullScreenCover(isPresented: $showEditor) { ProEditorView(clip: clip) }
        }
    }
}

// MARK: - Share sheet (UIKit bridge)

/// The system share sheet for a LOCAL video file. ShareLink hands a remote URL around as
/// a link; this always receives a file URL (resolved/downloaded first), so the recipient
/// gets the edited video itself.
struct ActivityShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}

// MARK: - Post now (build 66)

/// Immediate publish: pick platforms (only OAuth-linked ones can post), confirm, done.
/// Rides scheduleClip with date = now — the publisher omits the schedule date near-now,
/// which is Post for Me's post-immediately mode.
struct PostNowSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let clip: Clip
    let caption: String
    var onPosted: () -> Void = {}

    @State private var chosen: Set<SocialPlatform> = []
    @State private var posting = false
    @State private var note: String?
    @State private var showUpgrade = false

    private func linked(_ p: SocialPlatform) -> Bool {
        !store.publishAccountIds(for: [p]).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            // Stoic sheet header: eyebrow over a centered lowercase title. One line, scaled
            // down if needed, so the .medium detent still fits on an SE.
            VStack(spacing: Space.xs) {
                DSEyebrow(text: "POST NOW")
                Text(dsTitle("Where should this go?"))
                    .font(AppFont.title1).tracking(-0.3).foregroundStyle(Palette.textPrimary)
                    .lineLimit(1).minimumScaleFactor(0.75)
                    .accessibilityAddTraits(.isHeader)
            }
            .frame(maxWidth: .infinity)
            .padding(.top, Space.sm)
            DSGroup {
                ForEach(SocialPlatform.allCases) { p in
                    let isLinked = linked(p)
                    Button {
                        if chosen.contains(p) { chosen.remove(p) } else { chosen.insert(p) }
                    } label: {
                        HStack(spacing: Space.md) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(p.label).font(AppFont.bodyText)
                                    .foregroundStyle(isLinked ? Palette.textPrimary : Palette.textTertiary)
                                if !isLinked {
                                    Label("not connected", systemImage: "link")
                                        .font(AppFont.caption)
                                        .foregroundStyle(Palette.textSecondary)
                                }
                            }
                            Spacer(minLength: Space.sm)
                            DSCheckmark(isOn: chosen.contains(p))
                                .opacity(isLinked ? 1 : 0.4)
                        }
                        .padding(.horizontal, Space.rowPad)
                        .frame(minHeight: 52)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(DSRowPressStyle())
                    .disabled(!isLinked)
                    .accessibilityAddTraits(chosen.contains(p) ? .isSelected : [])
                    .accessibilityIdentifier("postNow.\(p.rawValue)")
                    if p != SocialPlatform.allCases.last {
                        DSRowDivider()
                    }
                }
            }
            if let note {
                Label(note, systemImage: "exclamationmark.circle")
                    .font(AppFont.supporting).foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, Space.rowPad)
            }
            Spacer(minLength: 0)
            if store.canPublish {
                PrimaryButton(title: posting ? "Posting…" : "Post now", systemImage: "paperplane.fill") {
                    guard !posting, !chosen.isEmpty else { return }
                    posting = true
                    Task {
                        await store.scheduleClip(clip, on: Date(), platforms: Array(chosen),
                                                 caption: caption)
                        posting = false
                        let outcome = store.schedule.last?.outcome
                        if outcome == .posted {
                            dismiss(); onPosted()
                        } else {
                            note = "Couldn't post right now, so it's saved in your queue instead. Check your connected accounts in Profile."
                        }
                    }
                }
                .disabled(chosen.isEmpty || posting)
                .accessibilityIdentifier("postNow.confirm")
            } else {
                // Wall 2 (dual paywall): a free-tier post attempt used to silently
                // no-op behind scheduleClip's canPublish guard — this is the moment
                // the free tier's limit bites, so it re-asks honestly instead.
                PrimaryButton(title: "Upgrade to post", systemImage: "lock.fill") {
                    showUpgrade = true
                }
                .accessibilityIdentifier("postNow.upgrade")
            }
        }
        .padding(.horizontal, Space.screenH).padding(.top, Space.md).padding(.bottom, Space.sm)
        .background(Palette.canvas.ignoresSafeArea())
        .sheet(isPresented: $showUpgrade) { PaymentScreen(dismissible: true) }
        .onAppear {
            chosen = Set(SocialPlatform.allCases.filter(linked))
        }
    }
}

// MARK: - Edit-version timeline (build 66)

/// The clip's edit history as a vertical timeline: current cut on top, every past
/// version below with what produced it and a one-tap Restore. Restore is server-truthful
/// (EDL undo + re-render), so the clip briefly returns to "rendering" while the old cut
/// comes back.
struct VersionTimelineSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let clipId: UUID

    @State private var restoring: UUID?
    @State private var note: String?
    // Build 70: watch a past cut BEFORE committing to it — restoring used to be blind
    // (a re-render you could only judge after it landed). Holds the previewed entry.
    @State private var previewing: RenderVersion?

    private var clip: Clip? { store.clips.first(where: { $0.id == clipId }) }
    private var history: [RenderVersion] { clip?.renderHistory ?? [] }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            // Stoic sheet header: eyebrow + centered lowercase title + quiet subtitle.
            VStack(spacing: Space.xs) {
                DSEyebrow(text: "EDIT HISTORY")
                Text(dsTitle("Versions"))
                    .font(AppFont.title1).tracking(-0.3).foregroundStyle(Palette.textPrimary)
                    .accessibilityAddTraits(.isHeader)
                Text("Every edit is kept. Restore any version and your video re-renders exactly as it was.")
                    .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, Space.xs)
            }
            .frame(maxWidth: .infinity)
            .padding(.top, Space.md)
            ScrollView {
                VStack(alignment: .leading, spacing: Space.sm) {
                    timelineRow(label: clip?.currentVersionLabel ?? "", date: clip?.finishedAt,
                                isCurrent: true, isLast: history.isEmpty, index: nil)
                    ForEach(Array(history.enumerated()), id: \.element.id) { i, v in
                        timelineRow(label: v.label, date: v.date, isCurrent: false,
                                    isLast: i == history.count - 1, index: i)
                    }
                }
            }
            if let note {
                Label(note, systemImage: "exclamationmark.circle")
                    .font(AppFont.supporting).foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, Space.rowPad)
            }
        }
        .padding(.horizontal, Space.screenH).padding(.bottom, Space.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.canvas.ignoresSafeArea())
        .sheet(item: $previewing) { v in
            VersionPreviewSheet(version: v,
                                index: history.firstIndex(where: { $0.id == v.id }) ?? 0,
                                canRestore: clip?.status == .ready) { idx in
                previewing = nil
                Task {
                    let ok = await store.restoreEditVersion(clipId: clipId, index: idx)
                    if ok { dismiss() }
                    else { note = "That version can't be restored anymore (the edit session moved on). Newer versions may still work." }
                }
            }
        }
    }

    @ViewBuilder
    private func timelineRow(label: String, date: Date?, isCurrent: Bool, isLast: Bool, index: Int?) -> some View {
        // Stoic Journey timeline row: glyph leading, eyebrow + headline title, date, and
        // the row's actions as small capsules (outline Preview, ink Restore).
        HStack(alignment: .top, spacing: Space.md) {
            Image(systemName: isCurrent ? "checkmark.circle.fill" : "clock.arrow.circlepath")
                .font(.system(size: 20, weight: .regular))
                .foregroundStyle(Palette.textPrimary)
                .frame(width: 28)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                if isCurrent {
                    DSEyebrow(text: "CURRENT")
                }
                Text(label.isEmpty ? "Original edit" : "\u{201C}\(label)\u{201D}")
                    .font(AppFont.headline)
                    .foregroundStyle(Palette.textPrimary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                if let date {
                    Text(date.formatted(.relative(presentation: .named)))
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                }
                if let index {
                    HStack(spacing: Space.sm) {
                    Button { previewing = history[index] } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "play.fill").font(.system(size: 10, weight: .semibold))
                            Text("Preview")
                        }
                    }
                    .buttonStyle(DSCapsuleStyle(kind: .outline, height: 36))
                    .accessibilityIdentifier("versions.preview.\(index)")
                    if clip?.status == .ready {
                    Button {
                        guard restoring == nil else { return }
                        let vid = history[index].id
                        restoring = vid
                        Task {
                            let ok = await store.restoreEditVersion(clipId: clipId, index: index)
                            restoring = nil
                            if ok {
                                dismiss()
                            } else {
                                note = "That version can't be restored anymore (the edit session moved on). Newer versions may still work."
                            }
                        }
                    } label: {
                        Text(restoring == history[index].id ? "Restoring…" : "Restore")
                    }
                    .buttonStyle(DSCapsuleStyle(kind: .primary, height: 36))
                    .disabled(restoring != nil)
                    .accessibilityIdentifier("versions.restore.\(index)")
                    }
                    }
                    .padding(.top, Space.xs)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(Space.rowPad)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
            .fill(Palette.surface))
        // (isLast no longer draws a connector: rows are separate cards, Journey style.)
        .accessibilityElement(children: .contain)
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
                SectionLabel(text: "Your media")
                Text("Your photos and videos. The editor pulls from these for B-roll.")
                    .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Stoic primary capsule, content-sized and centered (not a full-width slab).
            PhotosPicker(selection: $picked, maxSelectionCount: 40, matching: .any(of: [.images, .videos])) {
                HStack(spacing: Space.sm) {
                    if importing { ProgressView().tint(Palette.onInk) }
                    else { Image(systemName: "plus").font(.system(size: 15, weight: .semibold)) }
                    Text(importing ? "Importing…" : "Import media").font(AppFont.headline)
                }
                .foregroundStyle(Palette.onInk)
                .padding(.horizontal, Space.xl).frame(height: 48)
                .background(Capsule().fill(Palette.ink))
                .contentShape(Capsule())
            }
            .buttonStyle(PressableStyle())
            .frame(maxWidth: .infinity)
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
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
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
            .overlay(LocalThumbnail(path: m.thumbnailPath ?? m.localPath, isVideo: m.isVideo,
                                    cornerRadius: Radius.tile).scaledToFill())
            .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))
            .overlay(alignment: .bottomLeading) { kindChip(m) }
            .overlay(alignment: .topTrailing) { analysisBadge(m) }
            .overlay(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 0.5))
            .accessibilityIdentifier("library.mediaCell")
    }

    /// Kind label as a tracked eyebrow on a dark scrim capsule (sits over the photo).
    private func kindChip(_ m: MediaAsset) -> some View {
        Text(m.kind.label.uppercased()).font(AppFont.eyebrow).tracking(0.8)
            .lineLimit(1).minimumScaleFactor(0.7)
            .foregroundStyle(Palette.onNight)
            .padding(.horizontal, 7).frame(height: 20)
            .background(Capsule().fill(Color.black.opacity(0.55)))
            .padding(6)
    }

    /// I-5: analysis-state badge — ✓ analyzed, spinner while running, ! on failure, nothing
    /// yet. Monochrome over the photo: done = dark disc + white check; failed = INVERTED
    /// (white disc + black "!") so it stands apart without a red.
    @ViewBuilder private func analysisBadge(_ m: MediaAsset) -> some View {
        switch (store.media.first { $0.id == m.id }?.analysisStatus ?? m.analysisStatus) {
        case .done:
            Image(systemName: "checkmark").font(.system(size: 9, weight: .bold))
                .foregroundStyle(Palette.onNight).frame(width: 20, height: 20)
                .background(Circle().fill(Color.black.opacity(0.6)))
                .padding(6)
                .accessibilityLabel("Analyzed")
        case .analyzing:
            ProgressView().tint(Palette.onNight).scaleEffect(0.6).frame(width: 20, height: 20)
                .background(Circle().fill(Color.black.opacity(0.5)))
                .padding(6)
                .accessibilityLabel("Analyzing")
        case .failed:
            Image(systemName: "exclamationmark").font(.system(size: 10, weight: .bold))
                .foregroundStyle(Color.black).frame(width: 20, height: 20)
                .background(Circle().fill(Palette.onNight))
                .overlay(Circle().strokeBorder(Color.black.opacity(0.25), lineWidth: 0.5))
                .padding(6)
                .accessibilityLabel("Analysis failed")
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
                VStack(alignment: .leading, spacing: Space.xl) {
                    LocalThumbnail(path: asset.thumbnailPath ?? asset.localPath, isVideo: asset.isVideo,
                                   cornerRadius: Radius.tile)
                        .frame(height: 280)
                        .frame(maxWidth: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))

                    VStack(alignment: .leading, spacing: Space.sm) {
                        SectionLabel(text: "What is this?")
                            .padding(.horizontal, Space.rowPad)
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: Space.sm) {
                                ForEach(MediaKind.allCases) { k in
                                    DSChip(title: k.label, isSelected: kind == k, action: { kind = k })
                                }
                            }
                        }
                    }

                    VStack(alignment: .leading, spacing: Space.sm) {
                        SectionLabel(text: "Tag (optional)")
                            .padding(.horizontal, Space.rowPad)
                        TextField("e.g. gym, office, on stage", text: $note).marqueField()
                            .tint(Palette.textPrimary)
                    }

                    // AI Analysis section (I-5: reads the LIVE asset so results appear reactively)
                    if live.analysisStatus == .analyzing {
                        DSChecklistRow(title: "Analyzing…", state: .active)
                    } else if live.analysisStatus == .done {
                        VStack(alignment: .leading, spacing: Space.lg) {
                            VStack(alignment: .leading, spacing: Space.sm) {
                                SectionLabel(text: "AI description")
                                    .padding(.horizontal, Space.rowPad)
                                VStack(alignment: .leading, spacing: Space.md) {
                                    Text(live.aiDescription).font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                                        .fixedSize(horizontal: false, vertical: true)
                                    if !live.onScreenText.isEmpty {
                                        VStack(alignment: .leading, spacing: Space.xs) {
                                            SectionLabel(text: "On-screen text")
                                            Text(live.onScreenText).font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                                                .fixedSize(horizontal: false, vertical: true)
                                        }
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .dsCard(.surface, radius: Radius.group, padding: Space.rowPad)
                            }

                            // B-roll fit as a Stoic stat tile: the number, a monochrome
                            // meter, and the reason underneath.
                            VStack(alignment: .leading, spacing: Space.sm) {
                                SectionLabel(text: "B-roll fit")
                                    .padding(.horizontal, Space.rowPad)
                                VStack(alignment: .leading, spacing: Space.sm) {
                                    Text("\(live.brollSuitability)%").font(AppFont.stat).tracking(-0.3)
                                        .foregroundStyle(Palette.textPrimary)
                                    GeometryReader { geo in
                                        ZStack(alignment: .leading) {
                                            Capsule().fill(Palette.hairline).frame(height: 6)
                                            Capsule()
                                                .fill(Palette.ink)
                                                .frame(width: geo.size.width * CGFloat(live.brollSuitability) / 100, height: 6)
                                        }
                                    }.frame(height: 6)
                                    if !live.brollSuitabilityReason.isEmpty {
                                        Text(live.brollSuitabilityReason).font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(Space.rowPad)
                                .background(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous)
                                    .fill(Palette.surfaceSunken))
                            }

                            if !live.aiTags.isEmpty {
                                VStack(alignment: .leading, spacing: Space.sm) {
                                    SectionLabel(text: "Auto-tags")
                                        .padding(.horizontal, Space.rowPad)
                                    ScrollView(.horizontal, showsIndicators: false) {
                                        HStack(spacing: Space.sm) {
                                            ForEach(live.aiTags, id: \.self) { tag in Chip(text: tag) }
                                        }
                                    }
                                }
                            }
                        }
                    } else {
                        // .none / .failed — offer a manual analyze (retry on failed).
                        VStack(spacing: Space.sm) {
                            Button { store.ensureMediaAnalyzed(live) } label: {
                                Label(live.analysisStatus == .failed ? "Analysis failed, retry" : "Analyze with AI",
                                      systemImage: "sparkles")
                            }
                            .buttonStyle(DSCapsuleStyle(kind: .outline, height: 48))
                            .accessibilityIdentifier("media.analyzeNow")
                        }
                        .frame(maxWidth: .infinity)
                    }

                    // Destructive as a text link (black glyph + wording, no red).
                    Button(role: .destructive) { store.removeMedia(asset); dismiss() } label: {
                        Label("Remove from library", systemImage: "trash")
                            .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                            .frame(maxWidth: .infinity).frame(minHeight: 44)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(PressableStyle(dim: 0.5))
                }
                .padding(.horizontal, Space.screenH).padding(.top, Space.sm).padding(.bottom, Space.xl)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Media").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .onAppear { store.ensureMediaAnalyzed(asset) }   // I-5: lazy — analyze on first open
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text(dsTitle("Media"))
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Save") {
                        var a = asset; a.kind = kind; a.note = note.trimmingCharacters(in: .whitespaces)
                        store.updateMedia(a); dismiss()
                    }
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                }
            }
        }
    }
}

extension ClipStatus {
    /// Section order in the Library, owner-directed: what the AI is working on RIGHT NOW
    /// comes first (that's what the creator opened the app to check), then what's ready to
    /// post. Drafts are unsubmitted takes, so they sit after Ready, and "Needs attention"
    /// stays last — a failure shouldn't be the first thing greeting you every launch.
    static var allOrder: [ClipStatus] { [.rendering, .ready, .draft, .scheduled, .posted, .failed] }
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
    /// Monochrome status glyph: in the black-and-white system this (plus the label) is what
    /// carries a clip's state, where the old UI used a hue.
    var statusGlyph: String {
        switch self {
        case .draft:     return "pencil.line"
        case .ready:     return "checkmark.circle"
        case .rendering: return "wand.and.stars"
        case .scheduled: return "calendar"
        case .posted:    return "paperplane"
        case .failed:    return "exclamationmark.triangle"
        }
    }
    var railColor: Color {
        switch self {
        // Mono system: no status hues. Kept for API compatibility; status meaning is
        // carried by `statusGlyph` + wording.
        case .draft:     return Palette.textPrimary
        case .ready:     return Palette.textPrimary
        case .rendering: return Palette.textSecondary
        case .scheduled: return Palette.textSecondary
        case .posted:    return Palette.textPrimary
        case .failed:    return Palette.textPrimary
        }
    }
    var whyLine: String {
        switch self {
        case .draft:     return "Saved mid-take. Finish it in Film"
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
    @State private var showUpgrade = false

    private var readyCount: Int {
        store.clips.filter { clipIDs.contains($0.id) && $0.status == .ready }.count
    }
    private var hasPostableAccount: Bool {
        store.brand.connectedAccounts.contains { $0.canPublish }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    Text("^[\(readyCount) clip](inflect: true) will be scheduled to the same time and platforms.")
                        .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)

                    // Build 60: preview strip — see exactly what's about to go out.
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: Space.sm) {
                            ForEach(store.clips.filter { clipIDs.contains($0.id) && $0.status == .ready }) { c in
                                VStack(spacing: 6) {
                                    LocalThumbnail(path: c.thumbnailPath ?? c.playbackLocalPath,
                                                   isVideo: true, remoteImageURL: c.thumbnailURL,
                                                   cornerRadius: Radius.group)
                                        .aspectRatio(9/16, contentMode: .fill)
                                        .frame(width: 72, height: 128)
                                        .clipShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
                                        .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                                            .strokeBorder(Palette.hairline, lineWidth: 0.5))
                                    Text(c.title.isEmpty ? c.formatName : c.title)
                                        .font(AppFont.caption).lineLimit(1)
                                        .foregroundStyle(Palette.textSecondary).frame(width: 72)
                                }
                            }
                        }
                    }

                    // Native-style pickers inside a surface card (Stoic time-picker card).
                    VStack(alignment: .leading, spacing: Space.sm) {
                        SectionLabel(text: "When")
                            .padding(.horizontal, Space.rowPad)
                        MarqueTimePicker(time: $date)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .dsCard(.surface, radius: Radius.group, padding: Space.rowPad)
                    }

                    VStack(alignment: .leading, spacing: Space.sm) {
                        SectionLabel(text: "Platforms")
                            .padding(.horizontal, Space.rowPad)
                        HStack(spacing: Space.sm) {
                            ForEach(SocialPlatform.allCases) { p in
                                DSChip(title: p.label, isSelected: platforms.contains(p), action: {
                                    if platforms.contains(p) { platforms.remove(p) } else { platforms.insert(p) }
                                })
                            }
                        }
                    }

                    DSGroup {
                        DSToggleRow(title: "Auto-caption", systemImage: "captions.bubble",
                                    isOn: $autoCaptions)
                    }

                    if !hasPostableAccount {
                        // Warning = black glyph + wording (no amber).
                        Button { showConnect = true } label: {
                            HStack(alignment: .top, spacing: Space.md) {
                                Image(systemName: "link").font(.system(size: 17, weight: .regular))
                                    .frame(width: 24)
                                Text("Connect an account to actually post. Otherwise this just saves reminders.")
                                    .font(AppFont.supporting)
                                    .multilineTextAlignment(.leading)
                                    .fixedSize(horizontal: false, vertical: true)
                                Spacer(minLength: Space.sm)
                                Image(systemName: "chevron.right").font(.system(size: 14, weight: .semibold))
                                    .padding(.top, 2)
                            }
                            .foregroundStyle(Palette.textPrimary)
                            .padding(Space.rowPad)
                            .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                                .strokeBorder(Palette.hairline, lineWidth: 1))
                            .contentShape(Rectangle())
                        }.buttonStyle(PressableStyle(dim: 0.7))
                    }
                }
                .padding(.horizontal, Space.screenH).padding(.vertical, Space.md)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Schedule \(readyCount)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text(dsTitle("Schedule \(readyCount)"))
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(posting ? "Scheduling…" : "Schedule") {
                        guard !platforms.isEmpty, readyCount > 0 else { return }
                        // Wall 2: scheduling/auto-posting is paid — the store-level
                        // canPublish guard would otherwise swallow this silently.
                        guard store.canPublish else { showUpgrade = true; return }
                        posting = true
                        Task {
                            await store.scheduleClips(clipIDs, on: date, platforms: Array(platforms),
                                                      autoCaptions: autoCaptions)
                            posting = false; onDone(); dismiss()
                        }
                    }.disabled(posting || platforms.isEmpty || readyCount == 0)
                    .font(AppFont.headline).tint(Palette.textPrimary)
                }
            }
            .sheet(isPresented: $showConnect) { ConnectAccountsView() }
            .sheet(isPresented: $showUpgrade) { PaymentScreen(dismissible: true) }
        }
    }
}


// MARK: - Version preview (build 70)

/// Watch a past cut before committing to it. The player is the same one the clip
/// detail uses (timecode + scrubber), so you can jump to the moment you care about;
/// "Restore this version" is right underneath, so preview → decide is one flow.
struct VersionPreviewSheet: View {
    @Environment(\.dismiss) private var dismiss
    let version: RenderVersion
    let index: Int
    var canRestore: Bool = true
    let onRestore: (Int) -> Void

    var body: some View {
        NavigationStack {
            VStack(spacing: Space.lg) {
                ClipPreviewPlayer(path: nil, remoteURL: version.url, cornerRadius: Radius.tile)
                    .aspectRatio(9.0 / 16.0, contentMode: .fit)
                    .frame(maxWidth: .infinity, maxHeight: 460)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))
                VStack(spacing: Space.xs) {
                    Text(version.label.isEmpty ? "Original edit" : "\u{201C}\(version.label)\u{201D}")
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .multilineTextAlignment(.center)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(version.date.formatted(.relative(presentation: .named)))
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, Space.screenH).padding(.vertical, Space.md)
            .frame(maxWidth: .infinity)
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Preview").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Close") { dismiss() }
                        .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                }
                ToolbarItem(placement: .principal) {
                    Text(dsTitle("Preview"))
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .accessibilityAddTraits(.isHeader)
                }
            }
            .safeAreaInset(edge: .bottom) {
                if canRestore {
                    PrimaryButton(title: "Restore this version", systemImage: "arrow.uturn.backward") {
                        onRestore(index)
                    }
                    .padding(.horizontal, Space.screenH).padding(.top, Space.md).padding(.bottom, Space.sm)
                    .frame(maxWidth: .infinity)
                    .background(Palette.canvas.ignoresSafeArea(edges: .bottom))
                    .overlay(alignment: .top) { Rectangle().fill(Palette.hairline).frame(height: 1) }
                    .accessibilityIdentifier("versions.previewRestore")
                }
            }
        }
    }
}
