import Foundation

// Owner (2026-09-25): "if I click on past days I should be able to see what I filmed and
// posted, kind of like a summary of that day — that's the function of the calendar at the
// top of the screen." Pure (Foundation-only) grouping for the Home week strip's day sheet,
// so ios/LogicTests can pin which items land on which day.
struct DaySummary: Equatable {
    struct PostedItem: Equatable {
        let post: ScheduledPost
        let clip: Clip?
    }

    let day: Date                  // start of the calendar day
    var posted: [PostedItem] = []  // published that day (a real publish, not a local save)
    var scheduled: [PostedItem] = []   // set for that day, not published (upcoming or missed)
    var clips: [Clip] = []         // clips made that day (filmed or imported), newest first
    var footage: [Footage] = []    // raw takes saved that day that never became a clip

    var filmedCount: Int { clips.count + footage.count }
    var isEmpty: Bool { posted.isEmpty && scheduled.isEmpty && clips.isEmpty && footage.isEmpty }
    /// Summed views across that day's published posts (nil when no post reported metrics).
    var views: Int? {
        let m = posted.compactMap { $0.post.metrics }
        return m.isEmpty ? nil : m.reduce(0) { $0 + $1.views }
    }

    static func build(day: Date, clips: [Clip], footage: [Footage], schedule: [ScheduledPost],
                      calendar: Calendar = .current) -> DaySummary {
        let start = calendar.startOfDay(for: day)
        func same(_ d: Date) -> Bool { calendar.isDate(d, inSameDayAs: start) }
        let byId = Dictionary(clips.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        var out = DaySummary(day: start)
        for p in schedule.filter({ same($0.date) }).sorted(by: { $0.date < $1.date }) {
            let item = PostedItem(post: p, clip: byId[p.clipId])
            if p.posted || p.outcome?.didPost == true { out.posted.append(item) } else { out.scheduled.append(item) }
        }
        out.clips = clips.filter { same($0.createdAt) }.sorted { $0.createdAt > $1.createdAt }
        // A take that became a clip is shown once (as the clip): footage filmed against a
        // script already represented by a same-day clip is folded away.
        let clipScripts = Set(out.clips.map(\.scriptId))
        out.footage = footage.filter { same($0.addedAt) && !($0.scriptId.map(clipScripts.contains) ?? false) }
            .sorted { $0.addedAt > $1.addedAt }
        return out
    }

    /// Days in `days` that have anything to show (drives the strip's activity dot).
    static func activeDays(clips: [Clip], footage: [Footage], schedule: [ScheduledPost],
                           calendar: Calendar = .current) -> Set<Date> {
        var s = Set<Date>()
        clips.forEach { s.insert(calendar.startOfDay(for: $0.createdAt)) }
        footage.forEach { s.insert(calendar.startOfDay(for: $0.addedAt)) }
        schedule.forEach { s.insert(calendar.startOfDay(for: $0.date)) }
        return s
    }
}
