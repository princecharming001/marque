import SwiftUI
import PhotosUI

// Shared scheduling components used by the Performance tab's queue section
// (formerly the Calendar tab): DayRow/PostRow cards, the month grid, and the
// schedule/edit sheets.

enum CalMode: String, CaseIterable, Identifiable { case week = "Week", month = "Month"; var id: String { rawValue } }

// One sheet enum avoids the SwiftUI "two .sheet(item:) on one view" conflict where only one presents.
enum CalSheet: Identifiable {
    case schedule(day: Date, clipId: UUID?)
    case edit(ScheduledPost)
    var id: String {
        switch self {
        case .schedule(let day, let clip): return "sched-\(day.timeIntervalSince1970)-\(clip?.uuidString ?? "")"
        case .edit(let p): return "edit-\(p.id.uuidString)"
        }
    }
}

// MARK: - Month grid (bird's-eye planning view)

struct MonthGrid: View {
    let schedule: [ScheduledPost]
    let onPickDay: (Date) -> Void
    private let cols = Array(repeating: GridItem(.flexible(), spacing: 6), count: 7)

    private var days: [Date] {
        let cal = Calendar.current
        let now = Date()
        guard let interval = cal.dateInterval(of: .month, for: now),
              let firstWeekday = cal.dateComponents([.weekday], from: interval.start).weekday else { return [] }
        let leading = firstWeekday - cal.firstWeekday
        let start = cal.date(byAdding: .day, value: -max(0, leading), to: interval.start) ?? interval.start
        return (0..<42).compactMap { cal.date(byAdding: .day, value: $0, to: start) }
    }

    var body: some View {
        VStack(spacing: Space.sm) {
            HStack(spacing: 6) {
                ForEach(Array(["S","M","T","W","T","F","S"].enumerated()), id: \.offset) { _, d in
                    Text(d).font(AppFont.caption).foregroundStyle(Palette.textSecondary).frame(maxWidth: .infinity)
                }
            }
            LazyVGrid(columns: cols, spacing: 6) {
                ForEach(days, id: \.self) { day in
                    let cal = Calendar.current
                    let inMonth = cal.isDate(day, equalTo: Date(), toGranularity: .month)
                    let count = schedule.filter { cal.isDate($0.date, inSameDayAs: day) }.count
                    let today = cal.isDateInToday(day)
                    Button { onPickDay(day) } label: {
                        // Styled like a week-strip cell (DESIGN.md §5 Headers): number, a
                        // monochrome post dot, today outlined with Radius.cell.
                        VStack(spacing: 3) {
                            Text("\(cal.component(.day, from: day))")
                                .font(AppFont.bodyText.weight(today ? .bold : .regular))
                                .foregroundStyle(inMonth ? Palette.textPrimary : Palette.textTertiary)
                                .lineLimit(1).minimumScaleFactor(0.7)
                            Circle().fill(count > 0 ? (inMonth ? Palette.textPrimary : Palette.textTertiary) : Color.clear)
                                .frame(width: 5, height: 5)
                        }
                        .frame(maxWidth: .infinity).frame(height: 44)
                        .background(
                            RoundedRectangle(cornerRadius: Radius.cell, style: .continuous)
                                .strokeBorder(today ? Palette.textPrimary.opacity(0.35) : .clear, lineWidth: 1.5))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(PressableStyle(dim: 0.6))
                    .accessibilityLabel("\(day.formatted(.dateTime.month(.wide).day()))\(count > 0 ? ", \(count) scheduled" : "")")
                }
            }
        }
        .dsCard(.surface, radius: Radius.card, padding: Space.md)
    }
}

struct DateBox: Identifiable { let date: Date; var id: TimeInterval { date.timeIntervalSince1970 } }

struct DayRow: View {
    let day: Date
    let posts: [ScheduledPost]
    let hasReady: Bool
    let clipFor: (UUID) -> Clip?
    let onAdd: () -> Void
    let onTapPost: (ScheduledPost) -> Void
    let onDuplicate: (ScheduledPost) -> Void

    private var isToday: Bool { Calendar.current.isDateInToday(day) }
    private var hasContent: Bool { !posts.isEmpty }

    var body: some View {
        // Journey date header (title2) above a grouped card of timeline rows.
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack(alignment: .firstTextBaseline, spacing: Space.sm) {
                Text(day.formatted(.dateTime.weekday(.wide)))
                    .font(AppFont.title2).tracking(-0.2)
                    .foregroundStyle(Palette.textPrimary)
                    .lineLimit(1).minimumScaleFactor(0.8)
                if isToday {
                    DSEyebrow(text: "Today", color: Palette.textPrimary)
                        .accessibilityHidden(true)
                }
                Spacer(minLength: Space.sm)
                Text(day.formatted(.dateTime.month().day()))
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
            }
            .padding(.horizontal, Space.xs)
            if posts.isEmpty {
                Button(action: onAdd) {
                    HStack(spacing: Space.md) {
                        Image(systemName: "plus.circle")
                            .font(.system(size: 18, weight: .regular))
                            .foregroundStyle(hasReady ? Palette.textPrimary : Palette.textTertiary)
                        Text(hasReady ? "Schedule a clip" : "Nothing scheduled")
                            .font(AppFont.bodyText)
                            .foregroundStyle(hasReady ? Palette.textPrimary : Palette.textSecondary)
                        Spacer(minLength: Space.sm)
                        if hasReady {
                            Text("best ~6 PM").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        }
                    }
                    .padding(.horizontal, Space.rowPad)
                    .frame(minHeight: 52)
                    .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                        .fill(hasReady ? Palette.surface : Color.clear))
                    .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                        .strokeBorder(hasReady ? .clear : Palette.hairline, lineWidth: 1))
                    .contentShape(Rectangle())
                }
                .buttonStyle(PressableStyle(dim: 0.7))
                .disabled(!hasReady)
                .accessibilityIdentifier("calendar.addClip")
            } else {
                DSGroup {
                    ForEach(Array(posts.enumerated()), id: \.element.id) { i, p in
                        Button { onTapPost(p) } label: { PostRow(post: p, clip: clipFor(p.clipId)) }
                            .buttonStyle(DSRowPressStyle())
                            .accessibilityIdentifier("calendar.post")
                            .contextMenu {
                                Button { onTapPost(p) } label: { Label("Edit", systemImage: "pencil") }
                                Button { onDuplicate(p) } label: { Label("Duplicate to next day", systemImage: "plus.square.on.square") }
                            }
                        if i < posts.count - 1 { DSRowDivider(inset: Space.rowPad + 44 + Space.md) }
                    }
                    // Build 68: "Add another" removed — queueing more clips happens
                    // from the Library (select → Post), not from the day row.
                }
            }
        }
    }
}

struct PostRow: View {
    let post: ScheduledPost
    let clip: Clip?
    var body: some View {
        // Journey timeline row: status as the eyebrow, caption as the title, time +
        // platform glyphs as the meta line; thumbnail (the only color) leading.
        let s = postStatus(post)
        HStack(alignment: .center, spacing: Space.md) {
            LocalThumbnail(path: clip.flatMap { $0.thumbnailPath ?? $0.localVideoPath }, isVideo: true)
                .frame(width: 44, height: 60)
                .clipShape(RoundedRectangle(cornerRadius: Radius.cell, style: .continuous))
            VStack(alignment: .leading, spacing: 3) {
                // C-03: the status tells the TRUTH about what happened — never "Posted" for
                // a local save. Meaning rides on the glyph + wording, not on color.
                HStack(spacing: 5) {
                    Image(systemName: s.icon).font(.system(size: 11, weight: .semibold))
                    Text(s.label.uppercased()).font(AppFont.eyebrow).tracking(1.2)
                        .lineLimit(1).minimumScaleFactor(0.75)
                }
                .foregroundStyle(s.color)
                Text(post.caption).font(AppFont.headline).foregroundStyle(Palette.textPrimary).lineLimit(1)
                HStack(spacing: 6) {
                    Text(post.date.formatted(.dateTime.hour().minute()))
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    ForEach(post.platforms) { Image(systemName: icon($0)).font(.system(size: 12)).foregroundStyle(Palette.textSecondary) }
                    if post.autoCaptions {
                        Image(systemName: "captions.bubble").font(.system(size: 12)).foregroundStyle(Palette.textSecondary)
                    }
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Palette.textPrimary)
        }
        .padding(.horizontal, Space.rowPad).padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
    }
    private func icon(_ p: SocialPlatform) -> String { p == .instagram ? "camera.circle" : "music.note" }

    private func postStatus(_ p: ScheduledPost) -> (label: String, icon: String, color: Color) {
        switch p.outcome {
        case .posted:                   return ("Posted", "checkmark.circle.fill", Palette.positive)
        case .queuedTransportFailure:   return ("Will retry", "arrow.clockwise.circle", Palette.warning)
        case .savedLocalNoAccounts:     return ("Saved, connect account", "link.circle", Palette.textSecondary)
        case .failed:                   return ("Failed", "exclamationmark.circle", Palette.critical)
        case nil:                       return (p.posted ? "Posted" : "Scheduled",
                                                p.posted ? "checkmark.circle.fill" : "clock",
                                                p.posted ? Palette.positive : Palette.textSecondary)
        }
    }
}

// MARK: - Schedule a new post (time + platforms + auto-captions, then pick a clip)

struct SchedulePickerSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let day: Date
    let preselectClipId: UUID?
    @State private var platforms: Set<SocialPlatform> = [.instagram, .tiktok]
    @State private var time: Date
    @State private var autoCaptions = true
    @State private var importPick: PhotosPickerItem? = nil       // I-6
    @State private var importing = false
    @State private var showConnect = false
    @State private var pendingClip: Clip? = nil                  // held while the no-account alert is up

    /// At least one OAuth-linked account that can actually publish (empty accountId =
    /// voice-learning link only). Posting/scheduling with none reaches nothing.
    private var hasPostableAccount: Bool {
        store.brand.connectedAccounts.contains { $0.canPublish }
    }

    init(day: Date, preselectClipId: UUID? = nil) {
        self.day = day
        self.preselectClipId = preselectClipId
        _time = State(initialValue: Calendar.current.date(bySettingHour: 18, minute: 0, second: 0, of: day) ?? day)
    }
    // When deep-linked from a specific clip, show only that clip; otherwise all ready clips.
    private var ready: [Clip] {
        let all = store.clips.filter { $0.status == .ready }
        if let id = preselectClipId, let target = all.first(where: { $0.id == id }) { return [target] }
        return all
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    // Time — native-style picker inside a surface card (Rows + pickers).
                    VStack(alignment: .leading, spacing: Space.sm) {
                        DSEyebrow(text: "Time").padding(.horizontal, Space.rowPad)
                        VStack(alignment: .leading, spacing: Space.md) {
                            MarqueTimePicker(time: $time)
                            Text("Evenings (around 6 PM) tend to land best for most niches.")
                                .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .dsCard(.surface, radius: Radius.group, padding: Space.rowPad)
                    }

                    VStack(alignment: .leading, spacing: Space.sm) {
                        DSEyebrow(text: "Platforms").padding(.horizontal, Space.rowPad)
                        HStack(spacing: Space.sm) {
                            ForEach(SocialPlatform.allCases) { p in
                                DSChip(title: p.label, isSelected: platforms.contains(p)) { toggle(p) }
                            }
                        }

                        if !hasPostableAccount {
                            // Be honest up front: with nothing connected, "scheduling" only saves
                            // a reminder locally — it can't reach Instagram or TikTok.
                            Button { showConnect = true } label: {
                                HStack(alignment: .top, spacing: Space.md) {
                                    Image(systemName: "exclamationmark.circle")
                                        .font(.system(size: 17, weight: .regular))
                                    Text("Connect an account to actually post, otherwise this just saves to your calendar.")
                                        .font(AppFont.supporting).multilineTextAlignment(.leading)
                                        .fixedSize(horizontal: false, vertical: true)
                                    Spacer(minLength: 0)
                                    Image(systemName: "chevron.right").font(.system(size: 14, weight: .semibold))
                                        .padding(.top, 2)
                                }
                                .foregroundStyle(Palette.textPrimary)
                                .padding(Space.rowPad)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                                    .fill(Palette.surfaceSunken))
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(PressableStyle(dim: 0.7))
                            .accessibilityIdentifier("schedule.connectBanner")
                            .padding(.top, Space.xs)
                        }
                    }

                    DSGroup {
                        DSToggleRow(title: "Auto-captions",
                                    subtitle: "Burn captions onto the clip before posting",
                                    isOn: $autoCaptions)
                            .padding(.vertical, Space.xs)
                    }

                    VStack(alignment: .leading, spacing: Space.sm) {
                        DSEyebrow(text: "Pick a clip").padding(.horizontal, Space.rowPad)
                        // I-6: schedule a video you didn't film on Yunicorn.
                        if preselectClipId == nil {
                            PhotosPicker(selection: $importPick, matching: .videos) {
                                HStack(spacing: Space.sm) {
                                    if importing { ProgressView().tint(Palette.textSecondary) }
                                    else { Image(systemName: "plus").font(.system(size: 15, weight: .medium)) }
                                    Text(importing ? "Importing…" : "Import a video").font(AppFont.headline)
                                }
                                .foregroundStyle(Palette.textPrimary).frame(maxWidth: .infinity).frame(height: 52)
                                .background(Capsule().fill(Palette.surface))
                                .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                                .contentShape(Capsule())
                            }
                            .accessibilityIdentifier("schedule.importClip")
                            .onChange(of: importPick) { _, item in
                                guard let item else { return }
                                importing = true
                                Task {
                                    // LV-8: file-URL transfer first (streams to disk, like
                                    // RecordView) — `Data.self` put the WHOLE video in RAM,
                                    // a memory-kill for a real multi-minute library video.
                                    // Data stays only as the fallback for providers with
                                    // no file representation.
                                    if let picked = try? await item.loadTransferable(type: PickedVideoFile.self) {
                                        await store.importExternalClip(fileAt: picked.url, title: "Imported clip")
                                    } else if let data = try? await item.loadTransferable(type: Data.self) {
                                        await store.importExternalClip(data: data, title: "Imported clip")
                                    }
                                    importPick = nil; importing = false
                                }
                            }
                        }
                        if ready.isEmpty {
                            EmptyStateView(icon: "rectangle.stack", title: "No ready clips",
                                           message: "Render a clip, or import a video above.")
                        } else {
                            VStack(spacing: Space.stack) {
                                ForEach(ready) { c in
                                    Button { schedule(c) } label: { ClipCell(clip: c) }
                                        .buttonStyle(.plain)
                                        .accessibilityIdentifier("schedule.pickClip")
                                }
                            }
                        }
                    }
                }
                .screenPadding().padding(.top, Space.lg).padding(.bottom, Space.xxl)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle(day.formatted(.dateTime.weekday().month().day()))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() }.fontWeight(.semibold) } }
            .toolbarBackground(Palette.canvas, for: .navigationBar)
            .tint(Palette.ink)
            .sheet(isPresented: $showConnect) {
                ConnectAccountsView()
                    .padding(Space.screenH)
                    .frame(maxHeight: .infinity, alignment: .top)
                    .presentationBackground(Palette.canvas)
                    .presentationDragIndicator(.visible)
            }
            .alert("No account connected", isPresented: Binding(
                get: { pendingClip != nil }, set: { if !$0 { pendingClip = nil } })) {
                Button("Connect") { pendingClip = nil; showConnect = true }
                Button("Save to calendar") { if let c = pendingClip { pendingClip = nil; commitSchedule(c) } }
                Button("Cancel", role: .cancel) { pendingClip = nil }
            } message: {
                Text("Connect Instagram or TikTok to actually post this. Without one, it's only saved to your calendar as a reminder, nothing gets published.")
            }
        }
    }
    private func toggle(_ p: SocialPlatform) {
        if platforms.contains(p) { platforms.remove(p) } else { platforms.insert(p) }
    }
    private func schedule(_ c: Clip) {
        // No connected account → don't silently "schedule" into the void. Surface it and let
        // the creator connect or knowingly save a local reminder.
        guard hasPostableAccount else { pendingClip = c; return }
        commitSchedule(c)
    }
    private func commitSchedule(_ c: Clip) {
        let comps = Calendar.current.dateComponents([.hour, .minute], from: time)
        let date = Calendar.current.date(bySettingHour: comps.hour ?? 18, minute: comps.minute ?? 0, second: 0, of: day) ?? day
        Task {
            await store.scheduleClip(c, on: date, platforms: Array(platforms), autoCaptions: autoCaptions)
            dismiss()
        }
    }
}

// MARK: - Edit an existing scheduled post (preview, caption, time, platforms, post now / delete)

struct PostEditorSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let post: ScheduledPost
    @State private var time: Date
    @State private var platforms: Set<SocialPlatform>
    @State private var caption: String
    @State private var autoCaptions: Bool
    @State private var posting = false
    @State private var showSubscribe = false      // C-07
    @State private var showRemoveConfirm = false
    @State private var showConnect = false

    init(post: ScheduledPost) {
        self.post = post
        _time = State(initialValue: post.date)
        _platforms = State(initialValue: Set(post.platforms))
        _caption = State(initialValue: post.caption)
        _autoCaptions = State(initialValue: post.autoCaptions)
    }
    private var clip: Clip? { store.clips.first { $0.id == post.clipId } }
    /// An OAuth-linked account that can actually publish (not a voice-only link).
    private var hasPostableAccount: Bool {
        store.brand.connectedAccounts.contains { $0.canPublish }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    if let clip {
                        LocalVideoPlayer(path: clip.localVideoPath, remoteURL: clip.remoteURL)
                            .frame(height: 280)
                            .frame(maxWidth: .infinity)
                            .background(Palette.night)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))
                    }

                    VStack(alignment: .leading, spacing: Space.sm) {
                        DSEyebrow(text: "Caption").padding(.horizontal, Space.rowPad)
                        TextField("Caption", text: $caption, axis: .vertical)
                            .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                            .lineLimit(2...5)
                            .padding(Space.rowPad)
                            .frame(minHeight: 52, alignment: .topLeading)
                            .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                                .fill(Palette.surface))
                    }

                    VStack(alignment: .leading, spacing: Space.sm) {
                        DSEyebrow(text: "When").padding(.horizontal, Space.rowPad)
                        MarqueTimePicker(time: $time, includeDate: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .dsCard(.surface, radius: Radius.group, padding: Space.rowPad)
                    }

                    VStack(alignment: .leading, spacing: Space.sm) {
                        DSEyebrow(text: "Platforms").padding(.horizontal, Space.rowPad)
                        HStack(spacing: Space.sm) {
                            ForEach(SocialPlatform.allCases) { p in
                                DSChip(title: p.label, isSelected: platforms.contains(p)) { toggle(p) }
                            }
                        }
                    }

                    DSGroup {
                        DSToggleRow(title: "Auto-captions", isOn: $autoCaptions)
                    }

                    // Build 68: results are never hand-typed — the backend polls the
                    // connected Instagram/TikTok account and metrics flow in on their own.
                    if let m = post.metrics {
                        HStack(alignment: .top, spacing: Space.sm) {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.system(size: 15, weight: .regular))
                                .foregroundStyle(Palette.positive)
                            Text("\(compactNumber(m.views)) views · \(compactNumber(m.likes)) likes, synced from your account")
                                .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 0)
                        }
                        .padding(.horizontal, Space.xs)
                    } else if post.outcome == .posted {
                        Text("Results sync automatically from your connected account once views come in.")
                            .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, Space.xs)
                    }

                    // Destructive = black text + trash glyph + confirm dialog (no red).
                    Button(role: .destructive) { showRemoveConfirm = true } label: {
                        HStack(spacing: Space.sm) {
                            Image(systemName: "trash").font(.system(size: 15, weight: .regular))
                            Text("Remove from schedule").font(AppFont.bodyText)
                        }
                        .foregroundStyle(Palette.critical)
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.dsLink)
                }
                .screenPadding().padding(.top, Space.lg).padding(.bottom, Space.xl)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("edit post.").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .topBarTrailing) { Button("Save") { save() }.fontWeight(.semibold) }
            }
            .tint(Palette.ink)
            .marqueConfirm($showRemoveConfirm, title: "Remove this post from your schedule?",
                           confirm: "Remove", destructive: true) { store.deleteScheduledPost(post); dismiss() }
            .safeAreaInset(edge: .bottom) {
                if store.canPublish && hasPostableAccount {
                    PrimaryButton(title: posting ? "Posting…" : "Post now", systemImage: "paperplane.fill") {
                        posting = true
                        let p = current
                        Task { await store.postNow(p); posting = false; dismiss() }
                    }
                    .padding(.horizontal, Space.screenH).padding(.top, Space.sm).padding(.bottom, Space.sm)
                    .background(Palette.canvas.ignoresSafeArea(edges: .bottom))
                } else if store.canPublish {
                    // Subscribed but nothing to post TO — don't offer a "Post now" that
                    // silently does nothing. Send them to connect an account instead.
                    Button { showConnect = true } label: {
                        Label("Connect an account to post", systemImage: "link")
                    }
                    .buttonStyle(DSCapsuleStyle(kind: .outline, fullWidth: true))
                    .padding(.horizontal, Space.screenH).padding(.top, Space.sm).padding(.bottom, Space.sm)
                    .background(Palette.canvas.ignoresSafeArea(edges: .bottom))
                    .accessibilityIdentifier("post.connectToPost")
                    .sheet(isPresented: $showConnect) {
                ConnectAccountsView()
                    .padding(Space.screenH)
                    .frame(maxHeight: .infinity, alignment: .top)
                    .presentationBackground(Palette.canvas)
                    .presentationDragIndicator(.visible)
            }
                } else {
                    // C-07: the real subscription gate (StoreKit2), not the dead PaywallView.
                    Button { showSubscribe = true } label: {
                        Label("Upgrade to publish", systemImage: "lock.fill")
                    }
                    .buttonStyle(DSCapsuleStyle(kind: .outline, fullWidth: true))
                    .padding(.horizontal, Space.screenH).padding(.top, Space.sm).padding(.bottom, Space.sm)
                    .background(Palette.canvas.ignoresSafeArea(edges: .bottom))
                    // Wall 2 of the dual-paywall shape: the free tier ends exactly here,
                    // where the user has already made something worth posting. Same
                    // PaymentScreen as the onboarding soft wall, sheet-dismissible.
                    .sheet(isPresented: $showSubscribe) { PaymentScreen(dismissible: true) }
                }
            }
        }
    }
    private var current: ScheduledPost {
        var p = post; p.date = time; p.platforms = Array(platforms); p.caption = caption; p.autoCaptions = autoCaptions
        return p
    }
    private func toggle(_ p: SocialPlatform) {
        if platforms.contains(p) { platforms.remove(p) } else { platforms.insert(p) }
    }
    private func save() { store.updateScheduledPost(current); dismiss() }
}
