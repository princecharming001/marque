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
            HStack {
                ForEach(["S","M","T","W","T","F","S"], id: \.self) { d in
                    Text(d).font(AppFont.micro).foregroundStyle(Palette.textTertiary).frame(maxWidth: .infinity)
                }
            }
            LazyVGrid(columns: cols, spacing: 6) {
                ForEach(days, id: \.self) { day in
                    let cal = Calendar.current
                    let inMonth = cal.isDate(day, equalTo: Date(), toGranularity: .month)
                    let count = schedule.filter { cal.isDate($0.date, inSameDayAs: day) }.count
                    Button { onPickDay(day) } label: {
                        VStack(spacing: 3) {
                            Text("\(cal.component(.day, from: day))")
                                .font(AppFont.caption)
                                .foregroundStyle(inMonth ? Palette.textPrimary : Palette.textTertiary)
                            Circle().fill(count > 0 ? Palette.accent : Color.clear).frame(width: 5, height: 5)
                        }
                        .frame(maxWidth: .infinity).frame(height: 40)
                        .background(cal.isDateInToday(day) ? Palette.surfaceRaised : Color.clear)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
                    }
                    .buttonStyle(.plain)
                    .opacity(inMonth ? 1 : 0.4)
                }
            }
        }
        .marqueCard(padding: Space.md)
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
        HStack(spacing: 0) {
            // 3pt accent rail — visible only when this day has posts
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(hasContent ? Palette.accent : Color.clear)
                .frame(width: 3)
                .padding(.vertical, Space.md)

            VStack(alignment: .leading, spacing: Space.sm) {
                HStack(spacing: Space.sm) {
                    Text(day.formatted(.dateTime.weekday(.wide)))
                        .font(Typeface.display(17, .semibold)).tracking(Track.tight)
                        .foregroundStyle(Palette.textPrimary)
                    if isToday {
                        Text("TODAY").font(.system(size: 9, weight: .bold)).tracking(0.6)
                            .foregroundStyle(Palette.onInk)
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(Palette.ink).clipShape(Capsule())
                            .accessibilityHidden(true)
                    }
                    Spacer()
                    Text(day.formatted(.dateTime.month().day()))
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                }
                if posts.isEmpty {
                    Button(action: onAdd) {
                        HStack {
                            Image(systemName: "plus.circle").foregroundStyle(Palette.accent)
                            Text(hasReady ? "Schedule a clip" : "Nothing scheduled")
                                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                            Spacer()
                            if hasReady {
                                Text("best ~6 PM").font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                            }
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain).disabled(!hasReady)
                    .accessibilityIdentifier("calendar.addClip")
                } else {
                    ForEach(posts) { p in
                        Button { onTapPost(p) } label: { PostRow(post: p, clip: clipFor(p.clipId)) }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("calendar.post")
                            .contextMenu {
                                Button { onTapPost(p) } label: { Label("Edit", systemImage: "pencil") }
                                Button { onDuplicate(p) } label: { Label("Duplicate to next day", systemImage: "plus.square.on.square") }
                            }
                    }
                    if hasReady {
                        Button(action: onAdd) {
                            HStack(spacing: 6) {
                                Image(systemName: "plus").font(.system(size: 12, weight: .semibold))
                                Text("Add another").font(AppFont.caption)
                            }
                            .foregroundStyle(Palette.accent)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("calendar.addClip")
                    }
                }
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

struct PostRow: View {
    let post: ScheduledPost
    let clip: Clip?
    var body: some View {
        HStack(spacing: Space.sm) {
            LocalThumbnail(path: clip.flatMap { $0.thumbnailPath ?? $0.localVideoPath }, isVideo: true)
                .frame(width: 40, height: 54)
            VStack(alignment: .leading, spacing: 2) {
                Text(post.caption).font(AppFont.callout).foregroundStyle(Palette.textPrimary).lineLimit(1)
                HStack(spacing: 6) {
                    Text(post.date.formatted(.dateTime.hour().minute()))
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    ForEach(post.platforms) { Image(systemName: icon($0)).font(.system(size: 11)).foregroundStyle(Palette.textTertiary) }
                    if post.autoCaptions {
                        Image(systemName: "captions.bubble").font(.system(size: 11)).foregroundStyle(Palette.accent)
                    }
                }
            }
            Spacer()
            // C-03: the badge tells the TRUTH about what happened — never "Posted" for a
            // local save. Only a real upstream post is green.
            let s = postStatus(post)
            Text(s.label)
                .font(.system(size: 9, weight: .bold)).tracking(0.4)
                .foregroundStyle(s.color)
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(s.color.opacity(0.12))
                .clipShape(Capsule())
            Image(systemName: s.icon)
                .font(.system(size: 13)).foregroundStyle(s.color)
        }
        .padding(.vertical, 4)
    }
    private func icon(_ p: SocialPlatform) -> String { p == .instagram ? "camera.circle" : "music.note" }

    private func postStatus(_ p: ScheduledPost) -> (label: String, icon: String, color: Color) {
        switch p.outcome {
        case .posted:                   return ("Posted", "checkmark.circle.fill", Palette.positive)
        case .queuedTransportFailure:   return ("Will retry", "arrow.clockwise.circle", Palette.warning)
        case .savedLocalNoAccounts:     return ("Saved — connect account", "link.circle", Palette.textSecondary)
        case .failed:                   return ("Failed", "exclamationmark.circle", Palette.critical)
        case nil:                       return (p.posted ? "Posted" : "Scheduled",
                                                p.posted ? "checkmark.circle.fill" : "chevron.right",
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
                VStack(alignment: .leading, spacing: Space.lg) {
                    SectionLabel(text: "Time", accent: Palette.accent)
                    Text("Evenings (around 6 PM) tend to land best for most niches.")
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    MarqueTimePicker(time: $time)

                    SectionLabel(text: "Platforms")
                    HStack(spacing: Space.sm) {
                        ForEach(SocialPlatform.allCases) { p in
                            Button { toggle(p) } label: { Chip(text: p.label, selected: platforms.contains(p)) }
                                .buttonStyle(.plain)
                        }
                    }

                    if !hasPostableAccount {
                        // Be honest up front: with nothing connected, "scheduling" only saves
                        // a reminder locally — it can't reach Instagram or TikTok.
                        Button { showConnect = true } label: {
                            HStack(spacing: Space.sm) {
                                Image(systemName: "link").font(.system(size: 13, weight: .semibold))
                                Text("Connect an account to actually post — otherwise this just saves to your calendar.")
                                    .font(AppFont.caption).multilineTextAlignment(.leading)
                                Spacer(minLength: 0)
                                Image(systemName: "chevron.right").font(.system(size: 11, weight: .semibold))
                            }
                            .foregroundStyle(Palette.textSecondary)
                            .padding(Space.sm)
                            .background(Palette.warning.opacity(0.12))
                            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("schedule.connectBanner")
                    }

                    MarqueToggleRow(title: "Auto-captions",
                                    subtitle: "Burn captions onto the clip before posting",
                                    isOn: $autoCaptions)

                    SectionLabel(text: "Pick a clip")
                    // I-6: schedule a video you didn't film on Yunicorn.
                    if preselectClipId == nil {
                        PhotosPicker(selection: $importPick, matching: .videos) {
                            HStack(spacing: Space.sm) {
                                if importing { ProgressView().tint(Palette.textSecondary) }
                                else { Image(systemName: "plus").font(.system(size: 13, weight: .medium)) }
                                Text(importing ? "Importing…" : "Import a video").font(AppFont.callout)
                            }
                            .foregroundStyle(Palette.textPrimary).frame(maxWidth: .infinity).frame(height: 46)
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                                .strokeBorder(Palette.hairline, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
                        }
                        .accessibilityIdentifier("schedule.importClip")
                        .onChange(of: importPick) { _, item in
                            guard let item else { return }
                            importing = true
                            Task {
                                if let data = try? await item.loadTransferable(type: Data.self) {
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
                        ForEach(ready) { c in
                            Button { schedule(c) } label: { ClipCell(clip: c) }
                                .buttonStyle(.plain)
                                .accessibilityIdentifier("schedule.pickClip")
                        }
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle(day.formatted(.dateTime.weekday().month().day()))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .sheet(isPresented: $showConnect) { ConnectAccountsView() }
            .alert("No account connected", isPresented: Binding(
                get: { pendingClip != nil }, set: { if !$0 { pendingClip = nil } })) {
                Button("Connect") { pendingClip = nil; showConnect = true }
                Button("Save to calendar") { if let c = pendingClip { pendingClip = nil; commitSchedule(c) } }
                Button("Cancel", role: .cancel) { pendingClip = nil }
            } message: {
                Text("Connect Instagram or TikTok to actually post this. Without one, it's only saved to your calendar as a reminder — nothing gets published.")
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
    @State private var showMetrics = false
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
                VStack(alignment: .leading, spacing: Space.lg) {
                    if let clip {
                        LocalVideoPlayer(path: clip.localVideoPath, remoteURL: clip.remoteURL)
                            .frame(height: 280)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                    }
                    SectionLabel(text: "Caption", accent: Palette.accent)
                    TextField("Caption", text: $caption, axis: .vertical)
                        .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                        .lineLimit(2...5)
                        .padding(Space.md)
                        .background(Palette.surfaceRaised)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                            .strokeBorder(Palette.hairline, lineWidth: 1))

                    SectionLabel(text: "When")
                    MarqueTimePicker(time: $time, includeDate: true)

                    SectionLabel(text: "Platforms")
                    HStack(spacing: Space.sm) {
                        ForEach(SocialPlatform.allCases) { p in
                            Button { toggle(p) } label: { Chip(text: p.label, selected: platforms.contains(p)) }
                                .buttonStyle(.plain)
                        }
                    }

                    MarqueToggleRow(title: "Auto-captions", isOn: $autoCaptions)

                    // Log real results so Today/Insights/Coach learn from measured reach, not guesses.
                    Button { showMetrics = true } label: {
                        HStack(spacing: Space.sm) {
                            Image(systemName: post.metrics == nil ? "chart.bar.doc.horizontal" : "checkmark.circle.fill")
                                .foregroundStyle(post.metrics == nil ? Palette.goldDeep : Palette.positive)
                            Text(post.metrics == nil ? "Log results" : "Results logged — edit")
                                .font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                            Spacer()
                            Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
                        }
                        .padding(.vertical, Space.xs)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("post.logMetrics")

                    Button(role: .destructive) { showRemoveConfirm = true } label: {
                        Text("Remove from schedule").font(AppFont.callout).foregroundStyle(Palette.critical)
                    }
                    .padding(.top, Space.sm)
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Edit post").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .topBarTrailing) { Button("Save") { save() } }
            }
            .marqueConfirm($showRemoveConfirm, title: "Remove this post from your schedule?",
                           confirm: "Remove", destructive: true) { store.deleteScheduledPost(post); dismiss() }
            .sheet(isPresented: $showMetrics) { MetricsEntrySheet(post: post) }
            .safeAreaInset(edge: .bottom) {
                if store.canPublish && hasPostableAccount {
                    PrimaryButton(title: posting ? "Posting…" : "Post now", systemImage: "paperplane.fill") {
                        posting = true
                        let p = current
                        Task { await store.postNow(p); posting = false; dismiss() }
                    }
                    .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
                    .background(.ultraThinMaterial)
                } else if store.canPublish {
                    // Subscribed but nothing to post TO — don't offer a "Post now" that
                    // silently does nothing. Send them to connect an account instead.
                    Button { showConnect = true } label: {
                        Label("Connect an account to post", systemImage: "link")
                            .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                            .frame(maxWidth: .infinity).padding(Space.md)
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                    }
                    .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
                    .background(.ultraThinMaterial)
                    .accessibilityIdentifier("post.connectToPost")
                    .sheet(isPresented: $showConnect) { ConnectAccountsView() }
                } else {
                    // C-07: the real subscription gate (StoreKit2), not the dead PaywallView.
                    Button { showSubscribe = true } label: {
                        Label("Upgrade to publish", systemImage: "lock.fill")
                            .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                            .frame(maxWidth: .infinity).padding(Space.md)
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                    }
                    .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
                    .background(.ultraThinMaterial)
                    .sheet(isPresented: $showSubscribe) { SubscriptionGateView() }
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
