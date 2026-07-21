import SwiftUI

// Quiet, earned celebration after a recording session — measures showing up, not vanity views.
// build 52: when the take that just wrapped crossed a rank threshold, the sheet upgrades
// itself into a Marque Path level-up moment (a bigger, once-per-tier reward) instead of the
// routine wrap.
struct CelebrationView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Group {
            if let rank = store.pendingRankUp {
                rankUp(rank)
            } else {
                wrap
            }
        }
        .screenPadding().padding(.vertical, Space.xxl)
        .background(Palette.surface)
        .presentationDetents([.medium])
    }

    private var wrap: some View {
        VStack(spacing: Space.lg) {
            Spacer()
            Image("FlameIcon").resizable().scaledToFit().frame(width: 96, height: 96)
            Text("That's a wrap").font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
            Text("You showed up. That's \(store.reelsShot) \(store.reelsShot == 1 ? "reel" : "reels") shot.")
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
            Spacer()
            PrimaryButton(title: "Keep going") { dismiss() }
                .accessibilityIdentifier("celebration.dismiss")
        }
    }

    private func rankUp(_ rank: CreatorRank) -> some View {
        VStack(spacing: Space.md) {
            Spacer()
            RankSeal(level: rank.level, size: 92)
            Text("New rank").font(AppFont.micro).tracking(Track.label)
                .foregroundStyle(Palette.gold)
            Text(rank.title).font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
            Text(rank.subtitle)
                .font(AppFont.bodyL).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
            if !rank.isMax {
                Text("Level \(rank.level) of \(RankSystem.maxLevel)")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary).padding(.top, 2)
            }
            Spacer()
            PrimaryButton(title: "Keep building") {
                store.pendingRankUp = nil
                dismiss()
            }
            .accessibilityIdentifier("rankUp.dismiss")
        }
        // Clearing on any dismissal path so a swipe-down doesn't strand the pending flag.
        .onDisappear { store.pendingRankUp = nil }
    }
}

// A minimal, on-brand rank medallion: an ink seal ringed in gold, the level as a Roman
// numeral (Stoic register). Deterministic, no assets — scales cleanly on the celebration
// sheet and the Profile card.
struct RankSeal: View {
    let level: Int
    var size: CGFloat = 56

    var body: some View {
        ZStack {
            Circle().fill(Palette.ink)
            Circle().strokeBorder(Palette.gold, lineWidth: max(1.5, size * 0.03))
                .padding(size * 0.09)
            Text(Self.roman(level))
                .font(.system(size: size * 0.34, weight: .bold, design: .serif))
                .foregroundStyle(Palette.gold)
        }
        .frame(width: size, height: size)
        .shadow(color: Palette.gold.opacity(0.25), radius: size * 0.12, y: 2)
    }

    static func roman(_ n: Int) -> String {
        let table: [(Int, String)] = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
        var v = max(1, n), out = ""
        for (val, sym) in table { while v >= val { out += sym; v -= val } }
        return out
    }
}
