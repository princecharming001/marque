import SwiftUI

// The Marque Path (build 52) — a creator mastery ladder that rewards the ONE thing that
// actually builds a personal brand: showing up and shipping, repeatedly. It is deliberately
// consistency-weighted (reps + streak), not vanity-weighted (views/likes), which matches the
// app's Stoic, craft-over-clout positioning. "Marque" = the mark you leave, so the tiers
// track the arc from an unknown voice to a permanent one.
//
// XP is transparent and MONOTONE by construction: it's driven by lifetime takes shot
// (`reelsShot`, never decremented) plus a small streak bonus. AppStore also keeps a persisted
// level FLOOR so a temporary streak dip can never demote a creator — you keep what you earned.

struct CreatorRank: Equatable {
    let level: Int          // 1-based
    let title: String
    let subtitle: String    // one motivating line, Stoic-toned
    let minXP: Int          // XP at which this tier begins
    let nextXP: Int?        // XP for the next tier (nil at the top)
    var isMax: Bool { nextXP == nil }
}

enum RankSystem {
    // (title, subtitle, minXP). Curve is gently super-linear so early wins come fast
    // (momentum) and mastery stays aspirational.
    private static let tiers: [(title: String, subtitle: String, minXP: Int)] = [
        ("The First Mark",    "Every brand starts with one. You made yours.",         0),
        ("Finding Your Voice","Reps compound. The voice comes from volume.",          40),
        ("The Craftsman",     "You're building a habit, not chasing a hope.",         120),
        ("Signal",            "People are starting to notice the pattern.",           280),
        ("Momentum",          "Consistency is becoming your unfair advantage.",       560),
        ("Resonance",         "Your voice is unmistakable now.",                      1000),
        ("Authority",         "You set the tone others follow.",                      1800),
        ("Luminary",          "A name people say without being prompted.",            3200),
        ("Icon",              "The mark is permanent.",                               6000),
    ]

    // XP weights — reps dominate; streak is a modest multiplier on showing up daily.
    static let xpPerTake = 12
    static let xpPerStreakDay = 8

    static func xp(reelsShot: Int, streak: Int) -> Int {
        max(0, reelsShot) * xpPerTake + max(0, streak) * xpPerStreakDay
    }

    static var maxLevel: Int { tiers.count }

    static func rank(atLevel level: Int) -> CreatorRank {
        let i = min(max(1, level), tiers.count) - 1
        let t = tiers[i]
        return CreatorRank(level: i + 1, title: t.title, subtitle: t.subtitle,
                           minXP: t.minXP, nextXP: i + 1 < tiers.count ? tiers[i + 1].minXP : nil)
    }

    static func rank(forXP xp: Int) -> CreatorRank {
        var idx = 0
        for (i, t) in tiers.enumerated() where xp >= t.minXP { idx = i }
        return rank(atLevel: idx + 1)
    }

    /// 0…1 progress from this rank's floor to the next (1.0 at max tier).
    static func progress(xp: Int, in rank: CreatorRank) -> Double {
        guard let next = rank.nextXP, next > rank.minXP else { return 1.0 }
        return min(1.0, max(0.0, Double(xp - rank.minXP) / Double(next - rank.minXP)))
    }
}
