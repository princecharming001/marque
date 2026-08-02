import Foundation

// Submit-contract check: compiles the APP'S OWN SubmitConfig (plus the models it reads)
// and asserts the KEY SET it emits for each representative state. This is the same code
// RecordView.brollConfig() calls — not a re-implementation — so a key that disappears from
// the app disappears here too.

var failures = 0

func check(_ name: String, _ got: [String: String]?, expect: Set<String>) {
    let keys = Set((got ?? [:]).keys)
    let ok = keys == expect
    if !ok { failures += 1 }
    print("\(ok ? "PASS" : "FAIL")  \(name)")
    print("      keys: \(keys.sorted().joined(separator: ", "))")
    if !ok {
        print("      missing: \(expect.subtracting(keys).sorted())")
        print("      extra:   \(keys.subtracting(expect).sorted())")
    }
}

// ---- states ----------------------------------------------------------------

// 1. Fresh install: no standing dials, no profile, no CTA, free tier.
check("talking_head · cold prefs · no CTA · free",
      SubmitConfig.build(editFormat: .talkingHead, prefs: EditPrefs(), cta: nil, isPro: false),
      expect: ["meme_intensity", "is_pro", "cta_style_id"])

// 2. TH+b-roll, cutaway (the default b-roll look).
check("talking_head_broll · cutaway",
      SubmitConfig.build(editFormat: .talkingHeadBroll, prefs: EditPrefs(), cta: nil, isPro: false),
      expect: ["meme_intensity", "broll_mode", "broll_coverage", "is_pro", "cta_style_id"])

// 3. TH+b-roll, split screen → composition_style instead of broll_mode/coverage.
var split = EditPrefs(); split.brollStyle = "split_screen"
check("talking_head_broll · split_screen",
      SubmitConfig.build(editFormat: .talkingHeadBroll, prefs: split, cta: nil, isPro: false),
      expect: ["meme_intensity", "composition_style", "is_pro", "cta_style_id"])

// 4. Every standing dial set + a learned profile + a full CTA + Pro. The maximal payload.
var full = EditPrefs()
full.captionStyle = .boldWord
full.captionSize = .large
full.memeIntensity = 3
full.brollStyle = "panel"
full.styleProfile = StyleProfile(dims: StyleProfileMapper.coldStart, confidence: "medium",
                                 source: "quiz", handTuned: false, swipedAt: Date())
let cta = SavedCTA(name: "Classic Build", text: "Follow for more", handle: "yunicorn",
                   logoURL: "https://example.com/logo.png", styleId: "classic")
check("talking_head_broll · all dials · full CTA · pro",
      SubmitConfig.build(editFormat: .talkingHeadBroll, prefs: full, cta: cta, isPro: true),
      expect: ["meme_intensity", "broll_mode", "broll_coverage", "caption_style", "caption_size",
               "is_pro", "outro_text", "outro_handle", "outro_logo_url", "cta_style_id",
               "style_profile"])

// 5. A CTA with no handle and no logo — those slots must simply not appear.
let bare = SavedCTA(name: "Knockout", text: "Comment your take", handle: "", logoURL: "",
                    styleId: "knockout")
check("talking_head · CTA without handle/logo",
      SubmitConfig.build(editFormat: .talkingHead, prefs: EditPrefs(), cta: bare, isPro: false),
      expect: ["meme_intensity", "is_pro", "outro_text", "cta_style_id"])

// 6. A CTA whose copy was emptied collapses to the first-class "none" id (a template with
//    no words renders a blank card).
let empty = SavedCTA(name: "Classic", text: "   ", handle: "@me", logoURL: "x", styleId: "classic")
check("talking_head · CTA with empty copy collapses to none",
      SubmitConfig.build(editFormat: .talkingHead, prefs: EditPrefs(), cta: empty, isPro: false),
      expect: ["meme_intensity", "is_pro", "cta_style_id"])

// ---- value spot-checks (the keys are half the contract; the values are the other half) --

func expectValue(_ name: String, _ got: String?, _ want: String) {
    let ok = got == want
    if !ok { failures += 1 }
    print("\(ok ? "PASS" : "FAIL")  \(name) = \(got ?? "nil") (want \(want))")
}

let c4 = SubmitConfig.build(editFormat: .talkingHeadBroll, prefs: full, cta: cta, isPro: true) ?? [:]
expectValue("meme_intensity", c4["meme_intensity"], "3")
expectValue("broll_mode (panel)", c4["broll_mode"], "panel")
expectValue("broll_coverage", c4["broll_coverage"], "full")
expectValue("caption_style", c4["caption_style"], "bold-word")
expectValue("caption_size", c4["caption_size"], "large")
expectValue("is_pro", c4["is_pro"], "1")
expectValue("cta_style_id", c4["cta_style_id"], "classic")
expectValue("outro_handle (@ prefixed)", c4["outro_handle"], "@yunicorn")
expectValue("cta_style_id when None tile",
            (SubmitConfig.build(editFormat: .talkingHead, prefs: EditPrefs(), cta: nil, isPro: false) ?? [:])["cta_style_id"],
            "none")
// The legacy oddity is load-bearing: "cutaway" really does send broll_mode "full".
expectValue("broll_mode (cutaway → \"full\", unchanged from build 54)",
            (SubmitConfig.build(editFormat: .talkingHeadBroll, prefs: EditPrefs(), cta: nil, isPro: false) ?? [:])["broll_mode"],
            "full")

// style_profile must be parseable JSON carrying the wire shape the backend normalizes.
if let raw = c4["style_profile"],
   let obj = try? JSONSerialization.jsonObject(with: Data(raw.utf8)) as? [String: Any] {
    let want: Set<String> = ["schema_version", "dims", "confidence", "source", "hand_tuned"]
    let got = Set(obj.keys)
    let ok = got == want
    if !ok { failures += 1 }
    print("\(ok ? "PASS" : "FAIL")  style_profile JSON keys: \(got.sorted().joined(separator: ", "))")
} else {
    failures += 1
    print("FAIL  style_profile is not valid JSON")
}

print(failures == 0 ? "\nALL CHECKS PASSED" : "\n\(failures) CHECK(S) FAILED")
exit(failures == 0 ? 0 : 1)
