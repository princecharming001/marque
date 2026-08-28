import SwiftUI

// Custom, code-drawn marks for Settings — same "built from primitives, not an SF Symbol"
// language ConnectAccountsView already established for its platform badges. Bare system
// glyphs read as template/unfinished on a screen this central; these read as designed.

/// Circular tile shell every mark in this file sits inside — replaces the old flat
/// rounded-square `iconTile()`. Circular reads more "identity/brand," less "utility list."
struct SettingsIconTile<Content: View>: View {
    var fill: AnyShapeStyle
    var size: CGFloat = 36
    @ViewBuilder var content: Content

    init(fill: some ShapeStyle, size: CGFloat = 36, @ViewBuilder content: () -> Content) {
        self.fill = AnyShapeStyle(fill)
        self.size = size
        self.content = content()
    }

    var body: some View {
        ZStack { content }
            .frame(width: size, height: size)
            .background(Circle().fill(fill))
            .overlay(Circle().strokeBorder(.white.opacity(0.14), lineWidth: 1))
    }
}

/// Notifications: a "ping" abstraction — a solid core with two rings expanding outward —
/// instead of a literal bell. Reads as "something reaching you" without a stock glyph.
struct NotificationMark: View {
    var body: some View {
        SettingsIconTile(fill: Palette.accent.opacity(0.12)) {
            ZStack {
                Circle().strokeBorder(Palette.accent.opacity(0.35), lineWidth: 1.4)
                    .frame(width: 24, height: 24)
                Circle().strokeBorder(Palette.accent.opacity(0.6), lineWidth: 1.4)
                    .frame(width: 16, height: 16)
                Circle().fill(Palette.accent).frame(width: 7, height: 7)
            }
        }
    }
}

/// Subscription / Pro: an abstract bloom mark — three offset stroked circles at 120°,
/// radiating from a filled core — on a deep gradient tile. Reads as "premium" without
/// borrowing the crown/sparkles glyph every paywall in the App Store already uses.
struct ProMark: View {
    private static let tile = LinearGradient(
        colors: [Palette.ink, Color(hex: 0x3A362E)], startPoint: .topLeading, endPoint: .bottomTrailing)

    var body: some View {
        SettingsIconTile(fill: Self.tile) {
            ZStack {
                ForEach(0..<3) { i in
                    Circle().strokeBorder(.white.opacity(0.55), lineWidth: 1.2)
                        .frame(width: 14, height: 14)
                        .offset(y: -6.5)
                        .rotationEffect(.degrees(Double(i) * 120))
                }
                Circle().fill(.white).frame(width: 5, height: 5)
            }
        }
    }
}

/// The lighter secondary-tier variant of ProMark — same bloom family, single ring on an
/// accent tile instead of the dark gradient, so the two paid tiers read as related but
/// the primary one still reads as the bigger deal.
struct PlusMark: View {
    var body: some View {
        SettingsIconTile(fill: Palette.accent.opacity(0.12)) {
            ZStack {
                Circle().strokeBorder(Palette.accent, lineWidth: 1.3).frame(width: 16, height: 16)
                Circle().fill(Palette.accent).frame(width: 5, height: 5)
            }
        }
    }
}

/// Account: the creator's own initial on a warm gradient — a real identity mark (the
/// Apple-ID-card move) instead of a generic silhouette. Falls back to a dot if the
/// name/email is empty (fresh demo account).
struct AccountAvatarMark: View {
    let label: String

    private var initial: String {
        let t = label.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? "•" : String(t.prefix(1)).uppercased()
    }

    var size: CGFloat = 36

    var body: some View {
        SettingsIconTile(fill: LinearGradient(
            colors: [Palette.accent, Color(hex: 0x1B3E8C)], startPoint: .top, endPoint: .bottom
        ), size: size) {
            Text(initial).font(Typeface.sans(size >= 44 ? 18 : 14, .bold)).foregroundStyle(.white)
        }
    }
}

/// Data & Privacy: a lock built from a stroked arc (shackle) over a filled body — two
/// primitives, same discipline as the IG glyph's stroke-square + stroke-circle + dot.
struct PrivacyMark: View {
    var body: some View {
        SettingsIconTile(fill: Palette.textSecondary.opacity(0.10)) {
            ZStack {
                Circle().trim(from: 0.52, to: 0.98)
                    .stroke(Palette.textSecondary, style: StrokeStyle(lineWidth: 1.6, lineCap: .round))
                    .frame(width: 13, height: 13)
                    .offset(y: -4.5)
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .fill(Palette.textSecondary)
                    .frame(width: 15, height: 11)
                    .offset(y: 3)
            }
        }
    }
}

/// Support: three dots inside a ring — an abstract "conversation" mark, same visual
/// family as NotificationMark (ring + core elements) so the section reads as one system.
struct SupportMark: View {
    var body: some View {
        SettingsIconTile(fill: Palette.positive.opacity(0.12)) {
            ZStack {
                Circle().strokeBorder(Palette.positive.opacity(0.4), lineWidth: 1.4)
                    .frame(width: 24, height: 24)
                HStack(spacing: 2.5) {
                    ForEach(0..<3, id: \.self) { _ in
                        Circle().fill(Palette.positive).frame(width: 3.5, height: 3.5)
                    }
                }
            }
        }
    }
}

/// Destructive rows (sign out / delete): a plain SF Symbol still earns its place here —
/// HIG legibility for an exit/destroy action matters more than novelty — but the circular
/// tile + centered glyph keeps it visually consistent with every other row on the screen
/// instead of the old flat rounded-square.
struct DestructiveMark: View {
    let systemImage: String
    var body: some View {
        SettingsIconTile(fill: Palette.critical.opacity(0.10)) {
            Image(systemName: systemImage).font(.system(size: 14, weight: .semibold))
                .foregroundStyle(Palette.critical)
        }
    }
}

/// Secondary utility rows (restore, manage, export, legal links, support link, tour) —
/// filled SF Symbol variants in a circular tile, tinted per row. Not hand-drawn (that
/// would be overkill for a "Terms of Use" link), but consistent and deliberate rather
/// than the old bare-glyph-on-flat-square treatment.
struct UtilityMark: View {
    let systemImage: String
    var tint: Color = Palette.textSecondary
    var body: some View {
        SettingsIconTile(fill: tint.opacity(0.10)) {
            Image(systemName: systemImage).symbolVariant(.fill)
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(tint)
        }
    }
}
