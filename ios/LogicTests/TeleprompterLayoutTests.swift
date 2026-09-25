import Foundation

// Owner feedback 2026-09-25: the prompter never shows the title; scripts read as short
// lines broken at breaths; scroll paces by words per minute; a written blob splits into
// hook / body / CTA.
func runTeleprompterLayoutTests() {
    typealias L = TeleprompterLayout
    suite("TeleprompterLayout — the title is never a prompter line")
    let brief = L.lines(hook: "Why your bread is dense", body: "Everyone blames the starter. It is your dough.",
                        cta: "", title: "Why your bread is dense")
    expect(!brief.contains { $0.part == .hook }, "hook == title → no hook lines")
    expect(brief.first?.text.hasPrefix("Everyone") == true, "starts straight at the body")
    let custom = L.lines(hook: "why your BREAD is dense?", body: "Body.", cta: "", title: "Why your bread is dense")
    expect(!custom.contains { $0.part == .hook }, "case and punctuation don't make the title a hook")
    let real = L.lines(hook: "Stop overthinking your content.", body: "Here is the one system.", cta: "Follow for more.",
                       title: "The one system that actually works")
    expect(real.first?.part == .hook && real.filter { $0.part == .hook }.map(\.text).joined(separator: " ")
           == "Stop overthinking your content.", "a real hook leads (wrapped to the column): \(real)")
    expect(real.last == L.Line(text: "Follow for more.", part: .cta), "the CTA closes")
    let dup = L.lines(hook: "Stop overthinking your content.", body: "Stop overthinking your content. Pick one idea.",
                      cta: "", title: "")
    expect(dup.filter { $0.text.hasPrefix("Stop overthinking") }.count == 1, "a hook that opens the body is not repeated")
    let ctaDup = L.lines(hook: "", body: "Do the thing. Follow for more.", cta: "Follow for more.", title: "")
    expect(ctaDup.filter { $0.text == "Follow for more." }.count == 1, "a CTA that already ends the body is not repeated")

    suite("TeleprompterLayout — short lines, broken at breaths")
    let s = "Most people plan the whole week on Sunday night, and then they do nothing on Monday because the plan was never real."
    let lines = L.chunk(s, maxChars: 26)
    expect(lines.allSatisfy { $0.count <= 26 || !$0.contains(" ") }, "no line longer than the column (unless one long word)")
    expect(lines.joined(separator: " ") == s, "nothing lost or reordered: \(lines)")
    expect(lines.contains { $0.hasSuffix(",") }, "a clause break lands on the comma")
    expect(!lines.contains { !$0.contains(" ") }, "no one-word orphan line: \(lines)")
    expect(L.chunk("Hi.", maxChars: 26) == ["Hi."], "a one-word sentence stays one line")
    expect(L.chunk("  ", maxChars: 26).isEmpty, "blank → no lines")
    let two = L.chunk("First sentence here. Second one there.", maxChars: 40)
    expect(two == ["First sentence here.", "Second one there."], "sentences never share a line: \(two)")
    expect(L.chunk("Wait... what? It costs 3.5 dollars.", maxChars: 40) == ["Wait... what?", "It costs 3.5 dollars."],
           "ellipses and decimals don't split")

    suite("TeleprompterLayout — pace by words per minute")
    let rows = L.rowsPerSecond(words: 150, rows: 30, speed: 1.0)
    expectClose(rows, 0.5, "150 words over 30 rows at 150 wpm → 0.5 rows/s (a row every 2 s)")
    expectClose(L.rowsPerSecond(words: 150, rows: 30, speed: 1.5), 0.75, "Fast = ×1.5")
    expectClose(L.rowsPerSecond(words: 0, rows: 30, speed: 1.0), 0, "no words → no scroll")
    expect(L.wordCount(real) == 4 + 5 + 3, "word count sums the shown lines")

    suite("TeleprompterLayout — a written blob splits into hook / body / CTA")
    let blob = "You're planning your week wrong. Most people write a list on Sunday. Then Monday eats it. Do one thing first. Follow for the next one."
    let split = L.splitSpoken(blob)
    expect(split.hook == "You're planning your week wrong.", "first sentence is the hook: \(split.hook)")
    expect(split.cta == "Follow for the next one.", "closing call to action is the CTA: \(split.cta)")
    expect(split.body == "Most people write a list on Sunday. Then Monday eats it. Do one thing first.", "the rest is the body")
    let noCta = L.splitSpoken("Hook here. Body there. And that reframes the whole thing.")
    expect(noCta.cta.isEmpty && noCta.body.hasSuffix("whole thing."), "a payoff ending is not mistaken for a CTA")
    expect(L.splitSpoken("Just one line").hook == "Just one line", "one sentence → hook only")
}
