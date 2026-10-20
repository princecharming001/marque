import Foundation

// Reader helpers: seconds-to-read, "did the edit really change anything", trend dates.
func runScriptReadLogicTests() {
    suite("ReadTime — ~165 words a minute")
    let words = { (n: Int) in Array(repeating: "word", count: n).joined(separator: " ") }
    expectEqual(ReadTime.wordCount("Okay so — here's the thing ... 3 days."), 7, "dashes and dots aren't words")
    expectEqual(ReadTime.seconds(words(165)), 60, "165 words = 60 sec")
    expectEqual(ReadTime.seconds(words(124)), 45, "124 words ≈ 45 sec")
    expectEqual(ReadTime.seconds(""), 0, "empty = 0")
    expectEqual(ReadTime.label(seconds: 45), "~45 sec", "45 sec label")
    expectEqual(ReadTime.label(seconds: 43), "~45 sec", "rounds to nearest 5")
    expectEqual(ReadTime.label(seconds: 1), "~5 sec", "floor of 5 sec")
    expectEqual(ReadTime.label(seconds: 60), "~1 min", "one minute")
    expectEqual(ReadTime.label(seconds: 71), "~1 min 10 sec", "minute and seconds")
    expectEqual(ReadTime.label(seconds: 0), "", "nothing to read = no label")
    expectEqual(ReadTime.label(hook: words(20), body: words(100), cta: words(4)), "~45 sec", "hook + body + cta")

    suite("EditDiff — only meaningful edits are reported")
    expect(!EditDiff.isMeaningful(before: "Here's why it works.", after: "here's why it works."), "case-only")
    expect(!EditDiff.isMeaningful(before: "Here's why\nit works.", after: "  Here's   why it works.  "), "whitespace-only")
    expect(EditDiff.isMeaningful(before: "Here's why it works.", after: "And like, it works."), "real rewrite")
    expect(EditDiff.isMeaningful(before: "It works.", after: "It works!"), "punctuation counts")
    expect(EditDiff.make(before: "a b", after: "A  B", field: "body") == nil, "make → nil when not meaningful")
    let d = EditDiff.make(before: "old", after: "new", field: "cta")
    expectEqual(d?.field ?? "", "cta", "field kept")
    expectEqual(d?.before ?? "", "old", "before kept verbatim")

    suite("TrendDate — as of label")
    var cal = Calendar(identifier: .gregorian); cal.timeZone = TimeZone(identifier: "UTC")!
    let now = cal.date(from: DateComponents(year: 2026, month: 10, day: 20))!
    expectEqual(TrendDate.asOfLabel("2026-10-19", now: now) ?? "", "as of Oct 19", "date only")
    expectEqual(TrendDate.asOfLabel("2026-10-19T23:30:00Z", now: now) ?? "", "as of Oct 19", "timestamp keeps its calendar day")
    expectEqual(TrendDate.asOfLabel("2026-10-19T23:30:00.123-07:00", now: now) ?? "", "as of Oct 19", "offset timestamp")
    expectEqual(TrendDate.asOfLabel("2025-12-30", now: now) ?? "", "as of Dec 30, 2025", "other year shows the year")
    expect(TrendDate.asOfLabel(nil, now: now) == nil, "nil = no label")
    expect(TrendDate.asOfLabel("", now: now) == nil, "empty = no label")
    expect(TrendDate.asOfLabel("yesterday", now: now) == nil, "garbage = no label")
    expect(TrendDate.asOfLabel("2026-13-40", now: now) == nil, "impossible date = no label")
    expect(TrendDate.asOfLabel("2026-10-19xyz", now: now) == nil, "junk suffix = no label")
}
