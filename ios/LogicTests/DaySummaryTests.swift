import Foundation

// Owner 2026-09-25: tapping a day on Home's week strip shows what was filmed and posted.
func runDaySummaryTests() {
    suite("DaySummary — what lands on which day")
    var cal = Calendar(identifier: .gregorian); cal.timeZone = TimeZone(identifier: "America/Los_Angeles")!
    let day = cal.date(from: DateComponents(year: 2026, month: 9, day: 23, hour: 12))!
    let morning = cal.date(byAdding: .hour, value: -3, to: day)!
    let nextDay = cal.date(byAdding: .day, value: 1, to: day)!
    let sid = UUID(), otherSid = UUID()
    var a = Clip(scriptId: sid, formatId: "f", formatName: "Myth buster", caption: "", predictedScore: 0,
                 status: .ready, seconds: 42); a.createdAt = morning; a.title = "why budgets fail"
    var b = Clip(scriptId: otherSid, formatId: "f", formatName: "F", caption: "", predictedScore: 0,
                 status: .posted, seconds: 30); b.createdAt = nextDay
    var take = Footage(localPath: "t.mov"); take.scriptId = sid; take.addedAt = morning       // became clip a
    var loose = Footage(localPath: "u.mov"); loose.addedAt = day                                // raw take only
    var posted = ScheduledPost(clipId: b.id, caption: "c", platforms: [.tiktok], date: day)
    posted.posted = true; posted.metrics = PostMetrics(views: 1200)
    var saved = ScheduledPost(clipId: a.id, caption: "c", platforms: [.instagram], date: day)
    saved.outcome = .savedLocalNoAccounts
    let s = DaySummary.build(day: day, clips: [a, b], footage: [take, loose], schedule: [posted, saved], calendar: cal)
    expect(s.clips.map(\.id) == [a.id], "only that day's clips")
    expect(s.footage.map(\.id) == [loose.id], "a take that became a clip is shown once, as the clip")
    expect(s.filmedCount == 2, "filmed = clips + loose takes")
    expect(s.posted.count == 1 && s.posted[0].clip?.id == b.id, "a real publish is posted, with its clip")
    expect(s.scheduled.count == 1, "a local save without accounts is not 'posted'")
    expect(s.views == 1200, "views sum the day's posts")
    let empty = DaySummary.build(day: cal.date(byAdding: .day, value: -5, to: day)!, clips: [a, b],
                                 footage: [take, loose], schedule: [posted, saved], calendar: cal)
    expect(empty.isEmpty && empty.views == nil, "a quiet day is empty")
    let active = DaySummary.activeDays(clips: [a, b], footage: [], schedule: [], calendar: cal)
    expect(active.contains(cal.startOfDay(for: day)) && active.contains(cal.startOfDay(for: nextDay)), "active days")
}
