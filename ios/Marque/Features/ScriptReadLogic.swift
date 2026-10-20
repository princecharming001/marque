import Foundation

// Pure helpers for the script reader and trend card (Foundation-only; LogicTests covers
// them): how long the script takes to say, whether an inline edit is worth reporting,
// and the "as of Oct 19" date on a trend.

/// Seconds to read a script aloud at a natural talking-head pace.
enum ReadTime {
    static let wordsPerMinute: Double = 165

    /// Words = whitespace-separated tokens holding at least one letter or digit, so a
    /// stray "—" or "..." isn't counted.
    static func wordCount(_ text: String) -> Int {
        text.split(whereSeparator: { $0.isWhitespace })
            .filter { $0.contains(where: { $0.isLetter || $0.isNumber }) }
            .count
    }

    static func seconds(_ text: String, wordsPerMinute wpm: Double = wordsPerMinute) -> Int {
        let words = wordCount(text)
        guard words > 0, wpm > 0 else { return 0 }
        return Int((Double(words) / wpm * 60).rounded())
    }

    /// "~45 sec", "~1 min 10 sec", "~2 min". Rounded to the nearest 5 seconds (it's an
    /// estimate; "~43 sec" claims a precision it doesn't have). "" for no words.
    static func label(seconds: Int) -> String {
        guard seconds > 0 else { return "" }
        let s = max(5, Int((Double(seconds) / 5).rounded()) * 5)
        if s < 60 { return "~\(s) sec" }
        let m = s / 60, r = s % 60
        return r == 0 ? "~\(m) min" : "~\(m) min \(r) sec"
    }

    /// The whole spoken script: hook, body, CTA.
    static func label(hook: String, body: String, cta: String) -> String {
        label(seconds: seconds([hook, body, cta].joined(separator: " ")))
    }
}

/// An inline edit the creator made to a script (sent as `edit_diff`).
struct EditDiff: Hashable {
    let before: String
    let after: String
    var field: String = "body"      // "body" | "cta"

    /// Case and whitespace don't count: "Here's why" → "here's  why" teaches nothing.
    static func normalized(_ s: String) -> String {
        s.lowercased().split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }

    static func isMeaningful(before: String, after: String) -> Bool {
        normalized(before) != normalized(after)
    }

    /// nil when the edit isn't worth reporting.
    static func make(before: String, after: String, field: String) -> EditDiff? {
        guard isMeaningful(before: before, after: after) else { return nil }
        return EditDiff(before: before, after: after, field: field)
    }
}

/// The trend card's freshness line.
enum TrendDate {
    private static let posix = Locale(identifier: "en_US_POSIX")

    /// Reads the calendar date the backend wrote ("2026-10-19" or a full ISO timestamp —
    /// only the date part is used, so a UTC timestamp never shifts a day on the phone).
    static func parse(_ raw: String?) -> DateComponents? {
        guard let raw else { return nil }
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard t.count >= 10 else { return nil }
        let parts = t.prefix(10).split(separator: "-")
        guard parts.count == 3, parts[0].count == 4,
              let y = Int(parts[0]), let m = Int(parts[1]), let d = Int(parts[2]),
              (1...12).contains(m), (1...31).contains(d) else { return nil }
        if t.count > 10 {
            let next = t[t.index(t.startIndex, offsetBy: 10)]
            guard next == "T" || next == " " else { return nil }
        }
        return DateComponents(year: y, month: m, day: d)
    }

    /// "as of Oct 19"; the year is added when it isn't the current one ("as of Dec 30, 2025").
    static func asOfLabel(_ raw: String?, now: Date = Date()) -> String? {
        guard let comps = parse(raw) else { return nil }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        guard let date = cal.date(from: comps) else { return nil }
        let f = DateFormatter()
        f.locale = posix
        f.timeZone = cal.timeZone
        let thisYear = Calendar(identifier: .gregorian).component(.year, from: now)
        f.dateFormat = comps.year == thisYear ? "MMM d" : "MMM d, yyyy"
        return "as of " + f.string(from: date)
    }
}
