import Foundation

// The submit-time `config` dict, as a PURE function of an EditPrefs value plus the CTA
// picked for the take. The caller decides what those prefs ARE: RecordView overlays the
// per-video picks from the .recorded screen onto the standing dials (Profile → Editing
// style), so a pick made for one video wins over the profile default without touching it.
//
// WHY IT LIVES HERE AND NOT INSIDE RecordView
// This dict IS the wire contract with the edit pipeline — every key is read by name on the
// backend (`meme_intensity`, `broll_mode`, `caption_size`, `outro_*`, `cta_style_id`,
// `style_profile`). Buried inside a 1600-line SwiftUI view it could only be verified by
// eye. Pulled out, it is Foundation-only and can be compiled and asserted on its own (see
// the key-set check), so a key can't quietly disappear when the UI around it is redrawn.
//
// Values here are LITERAL COPIES of what the build-54 record screen sent. Do not "clean
// them up" (yes, `cutaway` really does send broll_mode "full") without changing the
// backend at the same time.

enum SubmitConfig {

    /// Build the per-job config. Returns nil only for a treatment with no opinion at all.
    /// - Parameters:
    ///   - editFormat: the cut treatment the creator picked on the record screen.
    ///   - prefs: the standing craft dials (Profile → Editing style). nil dials mean
    ///            "no standing opinion", and the pipeline/plan default stands.
    ///   - cta: the ending picked for THIS video; nil = the "None" tile (ends clean).
    ///   - isPro: entitlement flag — the server stamps a watermark on free-tier renders.
    static func build(editFormat: EditFormat, prefs: EditPrefs,
                      cta: SavedCTA?, isPro: Bool) -> [String: String]? {
        // The meme dial rides along for EVERY b-roll-capable format (plain Talking Head has
        // glimpse b-roll server-side, so memes can fire there too).
        let meme = ["meme_intensity": String(prefs.memeIntensity ?? defaultMemeIntensity)]
        let brollStyle = prefs.brollStyle ?? "cutaway"
        var cfg: [String: String]
        if editFormat == .talkingHeadBroll {
            switch brollStyle {
            case "cutaway": cfg = meme.merging(["broll_mode": "full",  "broll_coverage": "full"]) { a, _ in a }
            case "smart":   cfg = meme.merging(["broll_mode": "smart", "broll_coverage": "full"]) { a, _ in a }
            case "panel":   cfg = meme.merging(["broll_mode": "panel", "broll_coverage": "full"]) { a, _ in a }
            case "card":    cfg = meme.merging(["broll_mode": "card",  "broll_coverage": "full"]) { a, _ in a }
            case "green_screen", "split_screen":
                cfg = meme.merging(["composition_style": brollStyle]) { a, _ in a }
            default: cfg = meme.merging(["broll_coverage": "full"]) { a, _ in a }
            }
        } else if editFormat == .talkingHead {
            cfg = meme
        } else {
            cfg = [:]
        }

        // Caption treatment — sent ONLY on an explicit standing pick. nil = Auto, and the
        // plan LLM keeps choosing per take (sending a value every time would make the
        // planner's caption_plan dead code, which is exactly the build-55 bug).
        if let s = prefs.captionStyle { cfg["caption_style"] = s.rawValue }
        if let s = prefs.captionSize { cfg["caption_size"] = s.rawValue }
        cfg["is_pro"] = isPro ? "1" : "0"

        // The ending. An empty CTA line is the same thing as no CTA — a template with no
        // words renders an empty card — so it collapses to the first-class "none" id
        // rather than shipping a blank end card.
        let text = (cta?.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if let cta, !text.isEmpty {
            cfg["outro_text"] = text
            let h = cta.handle.trimmingCharacters(in: .whitespacesAndNewlines)
            if !h.isEmpty { cfg["outro_handle"] = h.hasPrefix("@") ? h : "@" + h }
            if !cta.logoURL.isEmpty { cfg["outro_logo_url"] = cta.logoURL }
            cfg["cta_style_id"] = cta.styleId
        } else {
            cfg["cta_style_id"] = "none"
        }

        // The learned taste vector. The backend merges its mapping UNDER everything above,
        // so a dial the creator actually set always wins; this only fills the gaps. Sent as
        // JSON text because `config` is a string map on the wire (the server accepts either
        // a dict or a JSON string here).
        if let profile = prefs.styleProfile,
           let data = try? JSONSerialization.data(withJSONObject: profile.asDictionary),
           let json = String(data: data, encoding: .utf8) {
            cfg["style_profile"] = json
        }
        return cfg
    }

    /// The pipeline's own meme default — mirrored here so an un-dialled creator sends the
    /// same value the record screen used to send from its slider's initial position.
    static let defaultMemeIntensity = 1
}

/// The 4 stops of the meme dial, shared by every surface that shows it (the standing
/// slider in Profile → Editing style, and the legacy brief screen's copy of it).
enum MemeEnergy {
    static let names = ["Off", "Subtle", "Memey", "Brainrot"]
    static func name(_ level: Int) -> String {
        names[max(0, min(names.count - 1, level))]
    }
}
