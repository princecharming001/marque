import SwiftUI
import PhotosUI

struct LibraryView: View {
    @Environment(AppStore.self) private var store
    @State private var tabIndex = 0
    private let tabs = ["Clips", "Footage", "Media"]

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
                case 1: FootageSection()
                case 2: MediaSection()
                default: ClipsSection()
                }
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - Clips (rendered, grouped by status, real posters, tap → player)

struct ClipsSection: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var detail: Clip?
    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            if store.clips.isEmpty {
                EmptyStateView(icon: "rectangle.stack", title: "No clips yet",
                               message: "Record a script in Studio and your clips will land here.",
                               graphic: "ClipsIcon")
                Button { router.showCreate = true } label: {
                    Label("Create your first clip", systemImage: "video.badge.plus")
                        .font(AppFont.headline).foregroundStyle(Palette.onInk)
                        .frame(maxWidth: .infinity).frame(height: 52)
                        .background(Palette.ink).clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("library.createFirst")
            } else {
                ForEach(ClipStatus.allOrder, id: \.self) { status in
                    let group = store.clips.filter { $0.status == status }
                    if !group.isEmpty {
                        VStack(alignment: .leading, spacing: Space.md) {
                            SectionLabel(text: status.title)
                            let cols = Array(repeating: GridItem(.flexible(), spacing: 8), count: 3)
                            LazyVGrid(columns: cols, spacing: 8) {
                                ForEach(Array(group.enumerated()), id: \.element.id) { i, c in
                                    Button { detail = c } label: { ClipGridCell(clip: c) }
                                        .buttonStyle(.plain)
                                        .accessibilityIdentifier("library.clip")
                                        .staggerReveal(i)
                                }
                            }
                        }
                    }
                }
            }
        }
        .sheet(item: $detail) { ClipDetailSheet(clip: $0) }
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
                    LocalThumbnail(path: clip.thumbnailPath ?? clip.localVideoPath, isVideo: true)
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
                    // Status why-line
                    Text(clip.status.whyLine)
                        .font(AppFont.micro).tracking(0.2)
                        .foregroundStyle(clip.status.railColor.opacity(0.8))
                }
                Spacer()
                ScoreBadge(score: clip.predictedScore)
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
            LocalThumbnail(path: clip.thumbnailPath ?? clip.localVideoPath, isVideo: true)
                .aspectRatio(9/16, contentMode: .fill)
                .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))

            // Bottom gradient + status
            LinearGradient(colors: [.clear, .black.opacity(0.6)],
                           startPoint: .top, endPoint: .bottom)
                .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))

            HStack {
                Text(statusLabel).font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.white.opacity(0.9))
                Spacer()
                Text("\(clip.seconds)s").font(.system(size: 9)).foregroundStyle(.white.opacity(0.7))
            }
            .padding(6)
        }
        .overlay(alignment: .topTrailing) {
            ScoreBadge(score: clip.predictedScore)
                .scaleEffect(0.75)
                .padding(4)
        }
        .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 0.5))
    }
    private var statusLabel: String {
        switch clip.status {
        case .ready:     return "READY"
        case .scheduled: return "SCHED"
        case .posted:    return "POSTED"
        case .rendering: return "RENDERING"
        case .failed:    return "FAILED"
        }
    }
}

struct ClipDetailSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(\.dismiss) private var dismiss
    let clip: Clip
    @State private var caption: String
    @State private var showDelete = false

    init(clip: Clip) {
        self.clip = clip
        _caption = State(initialValue: clip.caption)
    }

    // A shareable file/URL for export to Photos, Messages, the platform apps, etc.
    private var shareURL: URL? {
        if let p = clip.localVideoPath { return MediaStore.url(for: p) }
        if let r = clip.remoteURL, let u = URL(string: r) { return u }
        return nil
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    LocalVideoPlayer(path: clip.localVideoPath, remoteURL: clip.remoteURL)
                        .frame(height: 340)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                    HStack(spacing: Space.sm) {
                        FormatTag(formatId: clip.formatId)
                        Text("\(clip.seconds)s").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        Spacer()
                        ScoreBadge(score: clip.predictedScore)
                    }

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

                    if !clip.captionLines.isEmpty {
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
            .navigationTitle("Clip").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Menu {
                        if let shareURL { ShareLink(item: shareURL) { Label("Share / Export", systemImage: "square.and.arrow.up") } }
                        Button(role: .destructive) { showDelete = true } label: { Label("Delete clip", systemImage: "trash") }
                    } label: { Image(systemName: "ellipsis.circle") }
                    .accessibilityIdentifier("clip.menu")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { store.updateClipCaption(clip, caption: caption); dismiss() }
                }
            }
            .safeAreaInset(edge: .bottom) {
                if clip.status == .ready {
                    PrimaryButton(title: "Schedule this clip", systemImage: "calendar") {
                        store.updateClipCaption(clip, caption: caption)
                        router.pendingScheduleClipId = clip.id
                        dismiss(); router.selectedTab = .queue
                    }
                    .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
                    .background(.ultraThinMaterial)
                }
            }
            .confirmationDialog("Delete this clip?", isPresented: $showDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) { store.deleteClip(clip); dismiss() }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This removes the clip and any times it's scheduled. This can't be undone.")
            }
        }
    }
}

// MARK: - Footage (filmed-but-undecided takes → make clips)

struct FootageSection: View {
    @Environment(AppStore.self) private var store
    @State private var detail: Footage?
    @State private var pickedVids: [PhotosPickerItem] = []
    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            Text("Takes you've filmed but haven't turned into clips yet. Tap one to cut it into formats.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
            if store.footage.isEmpty {
                EmptyStateView(icon: "film", title: "No footage yet",
                               message: "Record a script in Studio, or import a video below.")
            } else {
                ForEach(store.footage) { f in
                    Button { detail = f } label: { FootageCell(footage: f) }.buttonStyle(.plain)
                        .contextMenu {
                            Button(role: .destructive) { store.deleteFootage(f) } label: {
                                Label("Delete take", systemImage: "trash")
                            }
                        }
                }
            }
            PhotosPicker(selection: $pickedVids, maxSelectionCount: 10, matching: .videos) {
                HStack(spacing: Space.sm) {
                    Image(systemName: "square.and.arrow.down")
                    Text("Import a video").font(AppFont.headline)
                }
                .foregroundStyle(Palette.textPrimary).frame(maxWidth: .infinity).frame(height: 50)
                .background(Palette.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
            }
            .onChange(of: pickedVids) { _, items in
                guard !items.isEmpty else { return }
                Task {
                    for item in items {
                        if let data = try? await item.loadTransferable(type: Data.self) {
                            let path = MediaStore.save(data, ext: "mov")
                            store.addFootage(path: path, title: "Imported take")
                        }
                    }
                    pickedVids = []
                }
            }
        }
        .sheet(item: $detail) { FootageDetailSheet(footage: $0) }
    }
}

struct FootageCell: View {
    let footage: Footage
    var body: some View {
        HStack(spacing: Space.md) {
            LocalThumbnail(path: footage.thumbnailPath ?? footage.localPath, isVideo: true)
                .frame(width: 54, height: 72)
            VStack(alignment: .leading, spacing: 4) {
                Text(footage.title.isEmpty ? "Untitled take" : footage.title)
                    .font(AppFont.body).foregroundStyle(Palette.textPrimary).lineLimit(2)
                Text(footage.seconds > 0 ? "\(footage.seconds)s · tap to make clips" : "Tap to make clips")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
            }
            Spacer()
            Image(systemName: "scissors").foregroundStyle(Palette.textTertiary)
        }
        .marqueCard(padding: Space.md)
    }
}

struct FootageDetailSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(\.dismiss) private var dismiss
    let footage: Footage
    @State private var formats: Set<String> = []
    @State private var working = false

    private var script: Script? {
        if let sid = footage.scriptId, let s = store.scripts.first(where: { $0.id == sid }) { return s }
        return store.scripts.first
    }
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    LocalVideoPlayer(path: footage.localPath)
                        .frame(height: 340)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                    if let script {
                        Text("Cut from: \(script.title.isEmpty ? script.hook.text : script.title)")
                            .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                        SectionLabel(text: "Choose formats to cut into", accent: Palette.accent)
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: Space.sm) {
                                ForEach(Catalog.formats) { f in
                                    Button {
                                        if formats.contains(f.id) { formats.remove(f.id) } else { formats.insert(f.id) }
                                    } label: { Chip(text: f.name, selected: formats.contains(f.id)) }.buttonStyle(.plain)
                                }
                            }
                        }
                    } else {
                        Text("Write a script in Studio first — then Marque can cut this take into formats.")
                            .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Footage").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .safeAreaInset(edge: .bottom) {
                if let script, !formats.isEmpty {
                    PrimaryButton(title: working ? "Cutting…" : "Make \(formats.count) clip\(formats.count == 1 ? "" : "s")",
                                  systemImage: "scissors") {
                        working = true
                        Task {
                            await store.makeClips(from: script, formats: Array(formats), footagePath: footage.localPath)
                            working = false; dismiss(); router.selectedTab = .library
                        }
                    }
                    .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
                    .background(.ultraThinMaterial)
                }
            }
        }
    }
}

// MARK: - Media corpus (bulk import; the AI references this)

struct MediaSection: View {
    @Environment(AppStore.self) private var store
    @State private var picked: [PhotosPickerItem] = []
    @State private var importing = false
    @State private var edit: MediaAsset?
    private let cols = [GridItem(.adaptive(minimum: 92), spacing: Space.sm)]

    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            Text("Upload your photos and clips. Marque references this footage when it writes and cuts your reels — including past shots of you.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)

            PhotosPicker(selection: $picked, maxSelectionCount: 40, matching: .any(of: [.images, .videos])) {
                HStack(spacing: Space.sm) {
                    if importing { ProgressView().tint(Palette.onInk) } else { Image(systemName: "plus") }
                    Text(importing ? "Importing…" : "Import media").font(AppFont.headline)
                }
                .foregroundStyle(Palette.onInk).frame(maxWidth: .infinity).frame(height: 54)
                .background(Palette.ink)
                .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
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
                        Button { edit = m } label: {
                            LocalThumbnail(path: m.thumbnailPath ?? m.localPath, isVideo: m.isVideo)
                                .aspectRatio(1, contentMode: .fill)
                                .frame(minHeight: 92).clipped()
                                .overlay(alignment: .bottomLeading) {
                                    Text(m.kind.label).font(.system(size: 9, weight: .semibold))
                                        .foregroundStyle(.white).padding(.horizontal, 5).padding(.vertical, 2)
                                        .background(Palette.ink.opacity(0.6)).clipShape(Capsule()).padding(4)
                                }
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .sheet(item: $edit) { MediaEditSheet(asset: $0) }
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
                    // AI Analysis section
                    if asset.analysisStatus == .analyzing {
                        HStack(spacing: Space.sm) {
                            ProgressView().scaleEffect(0.8)
                            Text("Analyzing…").font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                        }
                    } else if asset.analysisStatus == .done {
                        VStack(alignment: .leading, spacing: Space.sm) {
                            SectionLabel(text: "AI description", accent: Palette.accent)
                            Text(asset.aiDescription).font(AppFont.body).foregroundStyle(Palette.textPrimary)
                            if !asset.onScreenText.isEmpty {
                                SectionLabel(text: "On-screen text", accent: Palette.accent)
                                Text(asset.onScreenText).font(AppFont.body).foregroundStyle(Palette.textSecondary)
                            }
                            SectionLabel(text: "B-roll fit", accent: Palette.accent)
                            HStack(spacing: Space.sm) {
                                GeometryReader { geo in
                                    ZStack(alignment: .leading) {
                                        Capsule().fill(Palette.hairline).frame(height: 6)
                                        Capsule()
                                            .fill(asset.brollSuitability > 60 ? Palette.accent : Palette.gold)
                                            .frame(width: geo.size.width * CGFloat(asset.brollSuitability) / 100, height: 6)
                                    }
                                }.frame(height: 6)
                                Text("\(asset.brollSuitability)%").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            }
                            if !asset.brollSuitabilityReason.isEmpty {
                                Text(asset.brollSuitabilityReason).font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                            }
                            if !asset.aiTags.isEmpty {
                                SectionLabel(text: "Auto-tags", accent: Palette.accent)
                                ScrollView(.horizontal, showsIndicators: false) {
                                    HStack(spacing: Space.sm) {
                                        ForEach(asset.aiTags, id: \.self) { tag in
                                            Chip(text: tag)
                                        }
                                    }
                                }
                            }
                        }
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
    static var allOrder: [ClipStatus] { [.ready, .rendering, .scheduled, .posted, .failed] }
    var title: String {
        switch self {
        case .ready: return "Ready"
        case .rendering: return "Rendering"
        case .scheduled: return "Scheduled"
        case .posted: return "Posted"
        case .failed: return "Needs attention"
        }
    }
    var stageLabel: String {
        switch self {
        case .rendering: return "Cutting…"
        case .ready:     return "Ready"
        case .scheduled: return "Scheduled"
        case .posted:    return "Posted"
        case .failed:    return "Failed"
        }
    }
    var railColor: Color {
        switch self {
        case .ready:     return Palette.accent
        case .rendering: return Palette.textTertiary
        case .scheduled: return Color(hex: 0x9B5CF6)
        case .posted:    return Palette.positive
        case .failed:    return Palette.critical
        }
    }
    var whyLine: String {
        switch self {
        case .ready:     return "Ready to schedule"
        case .rendering: return "Rendering your clip…"
        case .scheduled: return "Scheduled to post"
        case .posted:    return "Posted"
        case .failed:    return "Needs attention"
        }
    }
}
