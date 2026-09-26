import SwiftUI

// Quiet, earned celebration after a recording session — measures showing up, not vanity views.
// build 52: when the take that just wrapped crossed a rank threshold, the sheet upgrades
// itself into a Marque Path level-up moment (a bigger, once-per-tier reward) instead of the
// routine wrap.
struct CelebrationView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Group {
            if let rank = store.pendingRankUp {
                rankUp(rank)
            } else {
                wrap
            }
        }
        .screenPadding().padding(.top, Space.xl).padding(.bottom, Space.lg)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Palette.canvas.ignoresSafeArea())
        .presentationDetents([.medium])
    }

    // Stoic badge moment: centered mascot, title1, one line of copy, primary capsule.
    private var wrap: some View {
        VStack(spacing: Space.md) {
            Spacer(minLength: 0)
            // The Yunicorn mark on a tone disc (it inverts to white in dark mode).
            YunicornMarkView(size: 58)
                .frame(width: 76, height: 76)
                .frame(width: 104, height: 104)
                .background(Circle().fill(scheme == .dark ? Palette.ink : Palette.surface))
                .accessibilityHidden(true)
            Text("That's a wrap").font(AppFont.title1).tracking(-0.3).foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
                .lineLimit(2).minimumScaleFactor(0.8)
                .accessibilityAddTraits(.isHeader)
            Text("You showed up. That's \(store.reelsShot) \(store.reelsShot == 1 ? "reel" : "reels") shot.")
                .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: Space.sm)
            PrimaryButton(title: "Keep going", fullWidth: false) { dismiss() }
                .accessibilityIdentifier("celebration.dismiss")
        }
    }

    private func rankUp(_ rank: CreatorRank) -> some View {
        VStack(spacing: Space.sm) {
            Spacer(minLength: 0)
            RankSeal(level: rank.level, size: 80)
                .padding(.bottom, Space.xs)
            DSEyebrow(text: "New rank")
            Text(rank.title).font(AppFont.title1).tracking(-0.3).foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
                .lineLimit(2).minimumScaleFactor(0.8)
                .accessibilityAddTraits(.isHeader)
            Text(rank.subtitle)
                .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
                .lineLimit(3).minimumScaleFactor(0.85)
            if !rank.isMax {
                Text("Level \(rank.level) of \(RankSystem.maxLevel)")
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
            }
            Spacer(minLength: Space.sm)
            PrimaryButton(title: "Keep building", fullWidth: false) { dismiss() }
                .accessibilityIdentifier("rankUp.dismiss")
        }
        // Audit (build 53, B4): clear the flag ONLY in onDisappear. Nil-ing it inside the button
        // flipped the parent `Group` from rankUp→wrap while the sheet was still animating out,
        // flashing "That's a wrap" for a frame. onDisappear fires on every dismissal path
        // (button or swipe-down), so the flag is still never stranded.
        .onDisappear { store.pendingRankUp = nil }
    }
}

// A minimal rank medallion, monochrome: an ink seal with an inner onInk ring and the level
// as a Roman numeral. ink/onInk invert together in dark mode, so it reads on both canvases.
// Deterministic, no assets — scales cleanly on the celebration sheet and the Profile card.
struct RankSeal: View {
    let level: Int
    var size: CGFloat = 56

    var body: some View {
        ZStack {
            Circle().fill(Palette.ink)
            Circle().strokeBorder(Palette.onInk.opacity(0.55), lineWidth: max(1, size * 0.02))
                .padding(size * 0.09)
            // Graphic numeral sized to the seal (not body copy), in the app's one typeface.
            Text(Self.roman(level))
                .font(Typeface.sans(size * 0.32, .bold))
                .tracking(size * 0.01)
                .foregroundStyle(Palette.onInk)
        }
        .frame(width: size, height: size)
        .accessibilityElement()
        .accessibilityLabel("Rank \(level)")
    }

    static func roman(_ n: Int) -> String {
        let table: [(Int, String)] = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
        var v = max(1, n), out = ""
        for (val, sym) in table { while v >= val { out += sym; v -= val } }
        return out
    }
}
