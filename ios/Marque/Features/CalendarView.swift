import SwiftUI

struct CalendarView: View {
    @Environment(AppStore.self) private var store
    @State private var scheduleFor: DateBox?
    @State private var editPost: ScheduledPost?

    private var week: [Date] {
        let cal = Calendar.current
        let start = cal.startOfDay(for: Date())
        return (0..<7).compactMap { cal.date(byAdding: .day, value: $0, to: start) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.lg) {
                ScreenTitle(text: "Calendar")
                Text("Your week at a glance. Tap a day to schedule a ready clip; tap a post to edit, reschedule or publish.")
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)

                ForEach(week, id: \.self) { day in
                    DayRow(day: day,
                           posts: store.schedule
                            .filter { Calendar.current.isDate($0.date, inSameDayAs: day) }
                            .sorted { $0.date < $1.date },
                           hasReady: store.clips.contains { $0.status == .ready },
                           clipFor: { id in store.clips.first { $0.id == id } },
                           onAdd: { scheduleFor = DateBox(date: day) },
                           onTapPost: { editPost = $0 })
                }
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
        .background(Palette.surface.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $scheduleFor) { SchedulePickerSheet(day: $0.date) }
        .sheet(item: $editPost) { PostEditorSheet(post: $0) }
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

    private var isToday: Bool { Calendar.current.isDateInToday(day) }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack(spacing: Space.sm) {
                Text(day.formatted(.dateTime.weekday(.wide)))
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                if isToday {
                    Text("TODAY").font(.system(size: 9, weight: .bold)).tracking(0.6)
                        .foregroundStyle(Palette.onInk)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Palette.ink).clipShape(Capsule())
                        .accessibilityHidden(true)   // decorative; don't collide with the "Today" tab
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
                    }
                }
                .buttonStyle(.plain).disabled(!hasReady)
                .accessibilityIdentifier("calendar.addClip")
            } else {
                ForEach(posts) { p in
                    Button { onTapPost(p) } label: { PostRow(post: p, clip: clipFor(p.clipId)) }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("calendar.post")
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
        .marqueCard(padding: Space.md)
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
            Image(systemName: post.posted ? "checkmark.circle.fill" : "chevron.right")
                .font(.system(size: 13)).foregroundStyle(post.posted ? Palette.positive : Palette.textTertiary)
        }
        .padding(.vertical, 4)
    }
    private func icon(_ p: SocialPlatform) -> String { p == .instagram ? "camera.circle" : "music.note" }
}

// MARK: - Schedule a new post (time + platforms + auto-captions, then pick a clip)

struct SchedulePickerSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let day: Date
    @State private var platforms: Set<SocialPlatform> = [.instagram, .tiktok]
    @State private var time: Date
    @State private var autoCaptions = true

    init(day: Date) {
        self.day = day
        _time = State(initialValue: Calendar.current.date(bySettingHour: 18, minute: 0, second: 0, of: day) ?? day)
    }
    private var ready: [Clip] { store.clips.filter { $0.status == .ready } }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    SectionLabel(text: "Time", accent: Palette.accent)
                    DatePicker("", selection: $time, displayedComponents: .hourAndMinute)
                        .labelsHidden().datePickerStyle(.wheel).frame(maxHeight: 130)

                    SectionLabel(text: "Platforms")
                    HStack(spacing: Space.sm) {
                        ForEach(SocialPlatform.allCases) { p in
                            Button { toggle(p) } label: { Chip(text: p.label, selected: platforms.contains(p)) }
                                .buttonStyle(.plain)
                        }
                    }

                    Toggle(isOn: $autoCaptions) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text("Auto-captions").font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                            Text("Burn captions onto the clip before posting").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        }
                    }.tint(Palette.accent)

                    SectionLabel(text: "Pick a clip")
                    if ready.isEmpty {
                        EmptyStateView(icon: "rectangle.stack", title: "No ready clips", message: "Render some clips first.")
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
            .background(Palette.surface.ignoresSafeArea())
            .navigationTitle(day.formatted(.dateTime.weekday().month().day()))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }
    private func toggle(_ p: SocialPlatform) {
        if platforms.contains(p) { platforms.remove(p) } else { platforms.insert(p) }
    }
    private func schedule(_ c: Clip) {
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

    init(post: ScheduledPost) {
        self.post = post
        _time = State(initialValue: post.date)
        _platforms = State(initialValue: Set(post.platforms))
        _caption = State(initialValue: post.caption)
        _autoCaptions = State(initialValue: post.autoCaptions)
    }
    private var clip: Clip? { store.clips.first { $0.id == post.clipId } }

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
                    DatePicker("", selection: $time, displayedComponents: [.date, .hourAndMinute]).labelsHidden()

                    SectionLabel(text: "Platforms")
                    HStack(spacing: Space.sm) {
                        ForEach(SocialPlatform.allCases) { p in
                            Button { toggle(p) } label: { Chip(text: p.label, selected: platforms.contains(p)) }
                                .buttonStyle(.plain)
                        }
                    }

                    Toggle(isOn: $autoCaptions) {
                        Text("Auto-captions").font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                    }.tint(Palette.accent)

                    Button(role: .destructive) { store.deleteScheduledPost(post); dismiss() } label: {
                        Text("Remove from schedule").font(AppFont.callout).foregroundStyle(Palette.critical)
                    }
                    .padding(.top, Space.sm)
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.surface.ignoresSafeArea())
            .navigationTitle("Edit post").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .topBarTrailing) { Button("Save") { save() } }
            }
            .safeAreaInset(edge: .bottom) {
                if store.canPublish {
                    PrimaryButton(title: posting ? "Posting…" : "Post now", systemImage: "paperplane.fill") {
                        posting = true
                        let p = current
                        Task { await store.postNow(p); posting = false; dismiss() }
                    }
                    .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
                    .background(.ultraThinMaterial)
                } else {
                    NavigationLink(destination: PaywallView()) {
                        Label("Upgrade to publish", systemImage: "lock.fill")
                            .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                            .frame(maxWidth: .infinity).padding(Space.md)
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                    }
                    .padding(.horizontal, Space.screenH).padding(.vertical, Space.sm)
                    .background(.ultraThinMaterial)
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
