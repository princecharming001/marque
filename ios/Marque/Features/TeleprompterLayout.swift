import Foundation

// Pure (Foundation-only) layout for the record screen's teleprompter, so the rules can be
// pinned by ios/LogicTests and the view stays a renderer.
//
// Owner feedback (2026-09-25): "I'm not reading the title in my video, so it shouldn't be
// in the teleprompter" and "are you sure dense paragraphs are the best way?". Research on
// how prompter apps and broadcast prompters lay out text agrees on the same few things:
// short lines (so the eyes barely travel: a narrow column, a handful of words per line),
// a big clean sans face, generous line spacing, the reading line near the lens, and a
// scroll rate set in words per minute (125–150 wpm is a relaxed spoken pace; short-form
// runs slightly faster). Dense paragraphs fail every one of those. So the prompter shows
// the script as a column of short lines, breaks them where a speaker would breathe (at
// sentence and clause punctuation), and paces by word count.
enum TeleprompterLayout {
    enum Part: Equatable { case hook, body, cta }
    struct Line: Equatable {
        let text: String
        let part: Part
    }

    /// Characters per line at the prompter's 22 pt semibold on a phone column (~5 words).
    static let defaultMaxChars = 26
    /// Relaxed spoken pace; the Slow/Normal/Fast chips multiply it.
    static let baseWordsPerMinute = 150.0

    /// The lines the prompter scrolls. The title is never one of them: a script whose hook
    /// is just its title (idea briefs, drafts, custom scripts) starts straight at the body.
    static func lines(hook: String, body: String, cta: String, title: String,
                      maxChars: Int = defaultMaxChars) -> [Line] {
        var out: [Line] = []
        let h = clean(hook), b = clean(body), c = clean(cta)
        if showsHook(hook: h, title: title, body: b) {
            out += chunk(h, maxChars: maxChars).map { Line(text: $0, part: .hook) }
        }
        out += chunk(b, maxChars: maxChars).map { Line(text: $0, part: .body) }
        if !c.isEmpty, !normalized(b).hasSuffix(normalized(c)) {
            out += chunk(c, maxChars: maxChars).map { Line(text: $0, part: .cta) }
        }
        return out
    }

    /// A hook line is shown only when it is real spoken copy: not the card title, and not
    /// already the opening of the body.
    static func showsHook(hook: String, title: String, body: String) -> Bool {
        let h = normalized(hook)
        guard !h.isEmpty else { return false }
        if h == normalized(title) { return false }
        if normalized(body).hasPrefix(h) { return false }
        return true
    }

    /// Short lines that break where a speaker breathes: sentence ends first, then clause
    /// punctuation once a line is reasonably full, then plain word wrap. Words are never
    /// split, and a sentence never leaves a single orphaned word on its last line.
    static func chunk(_ text: String, maxChars: Int = defaultMaxChars) -> [String] {
        let t = clean(text)
        guard !t.isEmpty else { return [] }
        var lines: [String] = []
        for sentence in sentences(t) {
            let words = sentence.split(separator: " ").map(String.init)
            var current: [String] = []
            var sentenceLines: [String] = []
            func flush() {
                if !current.isEmpty { sentenceLines.append(current.joined(separator: " ")); current = [] }
            }
            for w in words {
                let candidate = (current + [w]).joined(separator: " ")
                if !current.isEmpty, candidate.count > maxChars { flush() }
                current.append(w)
                let line = current.joined(separator: " ")
                // A clause break once the line is already half full reads as a breath.
                if let last = w.last, ",;:".contains(last), line.count >= maxChars / 2 { flush() }
            }
            flush()
            // Orphan control: a one-word last line steals a word from the line above.
            if sentenceLines.count >= 2, let last = sentenceLines.last,
               !last.contains(" ") {
                var prev = sentenceLines[sentenceLines.count - 2].split(separator: " ").map(String.init)
                if prev.count >= 3 {
                    let moved = prev.removeLast()
                    sentenceLines[sentenceLines.count - 2] = prev.joined(separator: " ")
                    sentenceLines[sentenceLines.count - 1] = moved + " " + last
                }
            }
            lines += sentenceLines
        }
        return lines
    }

    /// Word count of what the prompter shows (drives the scroll rate).
    static func wordCount(_ lines: [Line]) -> Int {
        lines.reduce(0) { $0 + $1.text.split(separator: " ").count }
    }

    /// Rows per second at `wordsPerMinute × speed` for a column of `rows` lines holding
    /// `words` words in total (the scroll advances one row per `words/rows` words).
    static func rowsPerSecond(words: Int, rows: Int, speed: Double,
                              wordsPerMinute: Double = baseWordsPerMinute) -> Double {
        guard rows > 0, words > 0 else { return 0 }
        let wordsPerRow = Double(words) / Double(rows)
        return (wordsPerMinute * max(0.1, speed) / 60.0) / wordsPerRow
    }

    /// Split a written script (one blob from the write agent) into what the app stores:
    /// the first sentence is the hook; a closing sentence that is a call to action becomes
    /// the CTA; everything between is the body.
    static func splitSpoken(_ full: String) -> (hook: String, body: String, cta: String) {
        var parts = sentences(clean(full))
        guard parts.count >= 2 else { return (parts.first ?? "", "", "") }
        let hook = parts.removeFirst()
        var cta = ""
        if let last = parts.last, isCallToAction(last) {
            cta = last
            parts.removeLast()
        }
        return (hook, parts.joined(separator: " "), cta)
    }

    static func isCallToAction(_ sentence: String) -> Bool {
        let s = normalized(sentence)
        let words = s.split(separator: " ").map(String.init)
        guard let first = words.first, words.count <= 14 else { return false }
        let verbs: Set<String> = ["follow", "comment", "save", "share", "like", "subscribe",
                                  "drop", "tag", "dm", "send", "hit", "link", "grab", "join",
                                  "check", "watch", "let"]
        return verbs.contains(first) || s.contains("follow for") || s.contains("link in bio")
            || s.contains("in the comments")
    }

    // MARK: helpers

    static func clean(_ s: String) -> String {
        s.split(whereSeparator: { $0.isWhitespace || $0.isNewline }).joined(separator: " ")
    }

    static func normalized(_ s: String) -> String {
        let lowered = clean(s).lowercased()
        let stripped = lowered.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) || $0 == " " }
        return String(String.UnicodeScalarView(stripped)).split(separator: " ").joined(separator: " ")
    }

    /// Sentences, keeping their terminal punctuation. Ellipses and decimals don't split.
    static func sentences(_ text: String) -> [String] {
        var out: [String] = []
        var current = ""
        let chars = Array(text)
        var i = 0
        while i < chars.count {
            let ch = chars[i]
            current.append(ch)
            if ".!?".contains(ch) {
                let next = i + 1 < chars.count ? chars[i + 1] : " "
                let prev = i > 0 ? chars[i - 1] : " "
                let isEllipsis = ch == "." && (next == "." || prev == ".")
                let isDecimal = ch == "." && prev.isNumber && next.isNumber
                if next == " " && !isEllipsis && !isDecimal {
                    out.append(current.trimmingCharacters(in: .whitespaces)); current = ""
                }
            }
            i += 1
        }
        let tail = current.trimmingCharacters(in: .whitespaces)
        if !tail.isEmpty { out.append(tail) }
        return out
    }
}
