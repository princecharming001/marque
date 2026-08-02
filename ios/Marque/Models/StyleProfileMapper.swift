import Foundation

// MARK: - Editing-taste profile → pipeline knobs (exact mirror of backend/app/style_profile.py)
//
// WHY A MIRROR AND NOT A ROUND TRIP
// The settings screen has to show "here's what your taste means" the instant a dial
// moves — a network hop per drag would make the preview lag the gesture and, worse,
// would let the client's PREVIEW disagree with the render's ACTUAL config the moment
// either side shipped a change. So the mapping is duplicated, and
// `backend/assets/style_mapping_cases.json` is the shared contract both copies answer
// to: Python asserts it in `backend/test_style_profile.py`, and this file was verified
// against the same cases. Change the mapping in BOTH places, regenerate the cases, and
// re-run both checks — never one alone.
//
// Every function here is pure and deterministic. Thresholds are literal copies —
// resist "cleaning them up"; regenerate the golden cases first if the mapping changes.

/// One pre-rendered sample edit the profile can be matched against (settings preview).
/// Tolerant Codable: the asset is authored server-side and may grow keys.
struct StyleArchetype: Codable, Hashable, Identifiable {
    var id: String = ""
    var name: String = ""
    var themeId: String? = nil
    var previewURL: String? = nil
    var vector: [String: Double] = [:]

    enum CodingKeys: String, CodingKey {
        case id, name, vector
        case themeId = "theme_id"
        case previewURL = "preview_url"
    }

    init(id: String = "", name: String = "", themeId: String? = nil,
         previewURL: String? = nil, vector: [String: Double] = [:]) {
        self.id = id; self.name = name; self.themeId = themeId
        self.previewURL = previewURL; self.vector = vector
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decode(String.self, forKey: .id)) ?? ""
        name = (try? c.decode(String.self, forKey: .name)) ?? ""
        themeId = try? c.decode(String.self, forKey: .themeId)
        previewURL = try? c.decode(String.self, forKey: .previewURL)
        vector = (try? c.decode([String: Double].self, forKey: .vector)) ?? [:]
    }
}

enum StyleProfileMapper {
    static let schemaVersion = 1

    /// The 8 dimensions, in canonical order (must match `style_profile.DIMS`).
    static let dims: [String] = [
        "pace",                 // cuts per 30s
        "energy",               // words per minute
        "broll_density",        // b-roll segments per 30s
        "broll_share",          // fraction of runtime covered by b-roll
        "broll_overlay_bias",   // of the b-roll used, how much is overlay vs fullscreen
        "caption_boldness",     // caps + weight + box + stroke, composited
        "caption_chunking",     // 1.0 = one word at a time, 0.0 = long phrases
        "title_cta_flair",      // appetite for opening title cards + visible CTAs
    ]

    /// The Wave-3 measured-winner conventions as a vector: a creator who skips the
    /// swiper maps to today's pipeline exactly.
    static let coldStart: [String: Double] = [
        "pace": 0.35, "energy": 0.45, "broll_density": 0.20, "broll_share": 0.15,
        "broll_overlay_bias": 0.30, "caption_boldness": 0.15, "caption_chunking": 0.30,
        "title_cta_flair": 0.15,
    ]

    // Rocchio weights. beta > alpha so a decisive swipe session actually moves the
    // profile; gamma < beta because a dislike is weaker evidence than a like (a creator
    // may pass on a reel for its content, not its edit).
    static let alpha = 0.3
    static let beta = 0.8
    static let gamma = 0.3
    static let minSwipes = 12   // non-skip swipes before the profile is trusted
    static let minLikes = 3     // below this we keep the cold start (confidence "low")

    static func clamp01(_ v: Double) -> Double { max(0.0, min(1.0, v)) }

    /// Coerce whatever we were handed into a valid vector (missing dims → cold start).
    static func normalize(_ raw: [String: Double]?) -> [String: Double] {
        let raw = raw ?? [:]
        var out: [String: Double] = [:]
        for d in dims { out[d] = clamp01(raw[d] ?? coldStart[d] ?? 0) }
        return out
    }

    // MARK: Rocchio relevance feedback

    private static func mean(_ vectors: [[String: Double]], weights: [Double]? = nil) -> [String: Double] {
        guard !vectors.isEmpty else { return dims.reduce(into: [:]) { $0[$1] = 0 } }
        let w = (weights?.count == vectors.count ? weights! : [Double](repeating: 1, count: vectors.count))
        let total = w.reduce(0, +) == 0 ? 1.0 : w.reduce(0, +)
        var out: [String: Double] = [:]
        for d in dims {
            out[d] = zip(vectors, w).reduce(0.0) { $0 + ($1.0[d] ?? 0) * $1.1 } / total
        }
        return out
    }

    /// Fold a swipe session into a profile. `likeWeights` carries super-likes (2.0).
    /// Below `minLikes` there isn't enough positive evidence to leave the cold start,
    /// so `base` comes back untouched.
    static func rocchio(liked: [[String: Double]], disliked: [[String: Double]],
                        likeWeights: [Double]? = nil,
                        base: [String: Double]? = nil) -> [String: Double] {
        let p0 = base ?? coldStart
        guard liked.count >= minLikes else { return p0 }
        let lm = mean(liked, weights: likeWeights)
        let dm = mean(disliked)
        var out: [String: Double] = [:]
        for d in dims {
            out[d] = clamp01(alpha * (p0[d] ?? 0) + beta * (lm[d] ?? 0) - gamma * (dm[d] ?? 0))
        }
        return out
    }

    // MARK: Archetype matching

    static func distance(_ a: [String: Double], _ b: [String: Double]) -> Double {
        dims.reduce(0.0) { $0 + pow((a[$1] ?? 0) - (b[$1] ?? 0), 2) }.squareRoot()
    }

    /// The pre-rendered sample closest to this profile (settings-page preview).
    static func nearestArchetype(profile: [String: Double],
                                 archetypes: [StyleArchetype]) -> StyleArchetype? {
        archetypes.min { distance(profile, $0.vector) < distance(profile, $1.vector) }
    }

    // MARK: Mapping

    /// One scalar for "how loud is this edit" — drives the meme/energy dials.
    static func intensity(_ p: [String: Double]) -> Double {
        0.45 * (p["pace"] ?? 0) + 0.35 * (p["caption_boldness"] ?? 0) + 0.20 * (p["energy"] ?? 0)
    }

    /// Profile → the per-job config keys the pipeline already understands.
    ///
    /// Only keys the profile has an OPINION about are emitted; anything omitted keeps the
    /// theme/plan default. Explicit per-job picks always override these (the caller merges
    /// this UNDER the creator's own choices).
    static func mapProfileToConfig(_ p: [String: Double],
                                   archetypes: [StyleArchetype] = []) -> [String: String] {
        var out: [String: String] = [:]
        if let arc = nearestArchetype(profile: p, archetypes: archetypes),
           let theme = arc.themeId, !theme.isEmpty {
            out["theme_id"] = theme
        }

        let boldness = p["caption_boldness"] ?? 0
        let chunking = p["caption_chunking"] ?? 0
        out["caption_style"] = (boldness >= 0.60 && chunking >= 0.60) ? "bold-word" : "clean"
        // The cold-start profile must claim NOTHING it doesn't have evidence for: at
        // COLD_START boldness (0.15) we emit no size at all, so an un-quizzed creator keeps
        // today's Auto sizing. Only a profile pushed clear of the default speaks up.
        if boldness >= 0.70 {
            out["caption_size"] = "large"
        } else if boldness < (coldStart["caption_boldness"] ?? 0.15) {
            out["caption_size"] = "small"
        }

        let i = intensity(p)
        out["meme_intensity"] = i < 0.25 ? "0" : i < 0.50 ? "1" : i < 0.75 ? "2" : "3"

        let pace = p["pace"] ?? 0
        out["interrupt_density"] = pace < 0.33 ? "calm" : pace < 0.66 ? "standard" : "dense"
        out["filler_trim"] = pace >= 0.60 ? "aggressive" : "standard"

        let ob = p["broll_overlay_bias"] ?? 0
        out["broll_mode"] = ob >= 0.60 ? "panel" : ob <= 0.35 ? "cutaway" : "smart"
        if (p["broll_share"] ?? 0) >= 0.50 { out["broll_coverage"] = "full" }

        let e = p["energy"] ?? 0
        if e >= 0.66 {
            out["energy"] = "high"
        } else if e <= 0.33 {
            out["energy"] = "low"
        }
        return out
    }
}
