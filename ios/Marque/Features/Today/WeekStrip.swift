import SwiftUI

struct WeekStripView: View {
    let schedule: [ScheduledPost]
    let onTapDay: (Date) -> Void

    private var days: [Date] {
        let cal = Calendar.current
        let start = cal.startOfDay(for: Date())
        return (0..<7).compactMap { cal.date(byAdding: .day, value: $0, to: start) }
    }

    var body: some View {
        HStack(spacing: 6) {
            ForEach(days, id: \.self) { day in
                DayCell(day: day, schedule: schedule, onTap: { onTapDay(day) })
            }
        }
        .padding(.horizontal, Space.sm)
        .padding(.vertical, Space.md)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }
}

private struct DayCell: View {
    let day: Date
    let schedule: [ScheduledPost]
    let onTap: () -> Void

    private var isToday: Bool { Calendar.current.isDateInToday(day) }
    private var hasPost: Bool {
        schedule.contains { Calendar.current.isDate($0.date, inSameDayAs: day) }
    }
    private var dayLetter: String {
        day.formatted(.dateTime.weekday(.narrow))
    }
    private var dayNumber: String {
        "\(Calendar.current.component(.day, from: day))"
    }

    var body: some View {
        Button(action: onTap) {
            VStack(spacing: 4) {
                Text(dayLetter)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(isToday ? Palette.accent : Palette.textTertiary)
                ZStack {
                    if isToday {
                        Circle()
                            .strokeBorder(Palette.accent, lineWidth: 2)
                            .frame(width: 30, height: 30)
                    }
                    if hasPost {
                        Circle()
                            .fill(isToday ? Palette.accent : Palette.ink.opacity(0.12))
                            .frame(width: 26, height: 26)
                        Image(systemName: "checkmark")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(isToday ? .white : Palette.textSecondary)
                    } else {
                        Text(dayNumber)
                            .font(.system(size: 14, weight: isToday ? .semibold : .regular))
                            .foregroundStyle(isToday ? Palette.accent : Palette.textPrimary)
                    }
                }
                .frame(width: 30, height: 30)
            }
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
