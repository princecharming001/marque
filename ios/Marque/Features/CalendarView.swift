import SwiftUI

struct CalendarView: View {
    @Environment(AppStore.self) private var store
    @State private var pickClipFor: Date?

    private var week: [Date] {
        let cal = Calendar.current
        let start = cal.startOfDay(for: Date())
        return (0..<7).compactMap { cal.date(byAdding: .day, value: $0, to: start) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.lg) {
                Text("This week").font(AppFont.displayL).foregroundStyle(Palette.textPrimary)
                Text("Drop your ready clips onto the days that fit. We'll pick the best times.")
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)

                ForEach(week, id: \.self) { day in
                    DayRow(day: day,
                           posts: store.schedule.filter { Calendar.current.isDate($0.date, inSameDayAs: day) },
                           hasReady: store.clips.contains { $0.status == .ready }) {
                        pickClipFor = day
                    }
                }
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
        .background(Palette.surface.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: Binding(get: { pickClipFor.map { DateBox(date: $0) } }, set: { pickClipFor = $0?.date })) { box in
            SchedulePickerSheet(day: box.date)
        }
    }
}

private struct DateBox: Identifiable { let date: Date; var id: TimeInterval { date.timeIntervalSince1970 } }

struct DayRow: View {
    let day: Date
    let posts: [ScheduledPost]
    let hasReady: Bool
    let onAdd: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack {
                Text(day.formatted(.dateTime.weekday(.wide))).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                Spacer()
                Text(day.formatted(.dateTime.month().day())).font(AppFont.caption).foregroundStyle(Palette.textTertiary)
            }
            if posts.isEmpty {
                Button(action: onAdd) {
                    HStack {
                        Image(systemName: "plus.circle").foregroundStyle(Palette.gold)
                        Text(hasReady ? "Schedule a clip" : "Nothing scheduled").font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        Spacer()
                    }
                }.buttonStyle(.plain).disabled(!hasReady)
            } else {
                ForEach(posts) { p in
                    HStack(spacing: Space.sm) {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(Palette.positive)
                        Text(p.caption).font(AppFont.body).foregroundStyle(Palette.textPrimary).lineLimit(1)
                        Spacer()
                        ForEach(p.platforms) { Image(systemName: icon($0)).font(.system(size: 12)).foregroundStyle(Palette.textTertiary) }
                    }
                }
            }
        }
        .marqueCard(padding: Space.md)
    }
    private func icon(_ p: SocialPlatform) -> String { p == .instagram ? "camera.circle" : "music.note" }
}

struct SchedulePickerSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let day: Date
    @State private var platforms: Set<SocialPlatform> = [.instagram, .tiktok]

    var ready: [Clip] { store.clips.filter { $0.status == .ready } }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    SectionTitle(text: "Platforms")
                    HStack(spacing: Space.sm) {
                        ForEach(SocialPlatform.allCases) { p in
                            Button {
                                if platforms.contains(p) { platforms.remove(p) } else { platforms.insert(p) }
                            } label: { Chip(text: p.label, selected: platforms.contains(p)) }.buttonStyle(.plain)
                        }
                    }
                    SectionTitle(text: "Pick a clip")
                    if ready.isEmpty {
                        EmptyStateView(icon: "rectangle.stack", title: "No ready clips", message: "Render some clips first.")
                    } else {
                        ForEach(ready) { c in
                            Button {
                                Task {
                                    let cal = Calendar.current
                                    let date = cal.date(bySettingHour: 18, minute: 0, second: 0, of: day) ?? day
                                    await store.scheduleClip(c, on: date, platforms: Array(platforms))
                                    dismiss()
                                }
                            } label: { ClipCell(clip: c) }.buttonStyle(.plain)
                        }
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.surface.ignoresSafeArea())
            .navigationTitle(day.formatted(.dateTime.weekday().month().day()))
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}
