import SwiftUI

struct QueueView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var sheet: CalSheet?
    @State private var mode: CalMode = .week

    private var week: [Date] {
        let cal = Calendar.current
        let start = cal.startOfDay(for: Date())
        return (0..<7).compactMap { cal.date(byAdding: .day, value: $0, to: start) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.lg) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("YOUR SCHEDULE").font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
                    ScreenTitle(text: "Queue")
                }
                Picker("View", selection: $mode) {
                    ForEach(CalMode.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("calendar.modeToggle")
                Text("Tap to schedule or edit posts.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)

                if mode == .week {
                    VStack(spacing: 12) {
                        ForEach(Array(week.enumerated()), id: \.element) { i, day in
                            DayRow(day: day,
                                   posts: store.schedule
                                    .filter { Calendar.current.isDate($0.date, inSameDayAs: day) }
                                    .sorted { $0.date < $1.date },
                                   hasReady: store.clips.contains { $0.status == .ready },
                                   clipFor: { id in store.clips.first { $0.id == id } },
                                   onAdd: { sheet = .schedule(day: day, clipId: nil) },
                                   onTapPost: { sheet = .edit($0) },
                                   onDuplicate: { store.duplicatePost($0) })
                                .staggerReveal(i)
                        }
                    }
                } else {
                    MonthGrid(schedule: store.schedule) { day in sheet = .schedule(day: day, clipId: nil) }
                }
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $sheet) { s in
            switch s {
            case .schedule(let day, let clipId): SchedulePickerSheet(day: day, preselectClipId: clipId)
            case .edit(let post): PostEditorSheet(post: post)
            }
        }
        .onAppear { consumePendingSchedule(); consumePendingQueueDate() }
        .onChange(of: router.pendingScheduleClipId) { _, _ in consumePendingSchedule() }
        .onChange(of: router.pendingQueueDate) { _, _ in consumePendingQueueDate() }
    }

    /// Library "Schedule this clip" deep-links here — open the scheduler for today, pre-filtered to that clip.
    private func consumePendingSchedule() {
        guard let id = router.pendingScheduleClipId else { return }
        sheet = .schedule(day: Calendar.current.startOfDay(for: Date()), clipId: id)
        router.pendingScheduleClipId = nil
    }

    /// Today WeekStrip tap deep-links here — open the scheduler for the tapped day.
    private func consumePendingQueueDate() {
        guard let date = router.pendingQueueDate else { return }
        sheet = .schedule(day: Calendar.current.startOfDay(for: date), clipId: nil)
        router.pendingQueueDate = nil
    }
}
