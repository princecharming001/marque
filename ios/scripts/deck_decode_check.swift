import Foundation

// Decode check: runs the APP'S OWN DeckDecoding over payloads captured from the real
// /v1/cta-styles and /v1/style-deck routes.

var failures = 0
func expect(_ name: String, _ cond: Bool, _ detail: String = "") {
    if !cond { failures += 1 }
    print("\(cond ? "PASS" : "FAIL")  \(name)\(detail.isEmpty ? "" : " — \(detail)")")
}

let ctaData = try! Data(contentsOf: URL(fileURLWithPath: "/tmp/cta.json"))
let deckData = try! Data(contentsOf: URL(fileURLWithPath: "/tmp/deck.json"))

// ---- CTA styles ----
let styles = DeckDecoding.ctaStyles(from: ctaData)
expect("cta: decoded a non-empty catalog", styles.count >= 20, "count=\(styles.count)")
expect("cta: \"none\" is FIRST", styles.first?.id == "none", "first=\(styles.first?.id ?? "nil")")
expect("cta: \"No CTA\" label", styles.first?.label == "No CTA", "label=\(styles.first?.label ?? "nil")")
expect("cta: isNone flags exactly one entry", styles.filter(\.isNone).count == 1)
expect("cta: every entry has a label", styles.allSatisfy { !$0.label.isEmpty })
expect("cta: templates carry params", styles.dropFirst().allSatisfy { !$0.params.isEmpty })
if let classic = styles.first(where: { $0.id == "classic" }) {
    expect("cta: classic params", Set(classic.params) == ["text", "handle", "logo"],
           "\(classic.params)")
    expect("cta: classic cluster/ui_class",
           classic.cluster == "minimal" && classic.uiClass == "full_end_card",
           "\(classic.cluster)/\(classic.uiClass)")
    expect("cta: seeded copy is non-empty", !SavedCTA.defaultCopy(for: classic).isEmpty)
} else { failures += 1; print("FAIL  cta: no `classic` template") }

// ---- Style deck ----
guard let deck = DeckDecoding.styleDeck(from: deckData) else {
    print("FAIL  deck: decode returned nil"); exit(1)
}
expect("deck: reels decoded", !deck.reels.isEmpty, "count=\(deck.reels.count)")
expect("deck: archetypes decoded", !deck.archetypes.isEmpty, "count=\(deck.archetypes.count)")
expect("deck: samples decoded", !deck.samples.isEmpty, "count=\(deck.samples.count)")
expect("deck: dims match the mapper", deck.dims == StyleProfileMapper.dims)
expect("deck: cold_start matches the mapper",
       deck.coldStart == StyleProfileMapper.normalize(StyleProfileMapper.coldStart))
expect("deck: every reel has a playable url", deck.reels.allSatisfy { $0.videoURL.hasPrefix("http") })
expect("deck: every reel vector is complete (all 8 dims)",
       deck.reels.allSatisfy { r in StyleProfileMapper.dims.allSatisfy { r.vector[$0] != nil } })
expect("deck: reels carry display attrs", deck.reels.allSatisfy { !$0.displayAttrs.isEmpty })
expect("deck: archetype NAMES survived the label→name remap",
       deck.archetypes.allSatisfy { !$0.name.isEmpty && $0.name != $0.id },
       deck.archetypes.first.map { "\($0.id) → \"\($0.name)\"" } ?? "")
expect("deck: archetypes carry theme ids",
       deck.archetypes.allSatisfy { !($0.themeId ?? "").isEmpty })

// The settings sheet's whole premise: a profile resolves to a playable sample.
let cold = StyleProfileMapper.normalize(nil)
if let m = deck.sample(for: cold) {
    print("PASS  deck: cold-start profile → \"\(m.archetype.name)\" sample \(m.clip.videoURL.suffix(28))")
} else {
    failures += 1; print("FAIL  deck: cold-start profile resolved no sample")
}

// A decisive swipe session must actually MOVE the profile off cold start, and the moved
// profile must still resolve to a sample.
let bold = deck.reels.sorted { ($0.vector["caption_boldness"] ?? 0) > ($1.vector["caption_boldness"] ?? 0) }
let liked = bold.prefix(5).map(\.vector)
let disliked = bold.suffix(5).map(\.vector)
let moved = StyleProfileMapper.rocchio(liked: Array(liked), disliked: Array(disliked))
expect("rocchio: 5 likes move the profile off cold start", moved != cold)
if let m = deck.sample(for: moved) {
    print("PASS  deck: swiped profile → \"\(m.archetype.name)\"")
} else {
    failures += 1; print("FAIL  deck: swiped profile resolved no sample")
}
// Below MIN_LIKES the profile must stay put — no pretending 2 taps taught us a taste.
expect("rocchio: 2 likes keep the cold start",
       StyleProfileMapper.rocchio(liked: Array(liked.prefix(2)), disliked: []) == cold)

print(failures == 0 ? "\nALL CHECKS PASSED" : "\n\(failures) CHECK(S) FAILED")
exit(failures == 0 ? 0 : 1)
