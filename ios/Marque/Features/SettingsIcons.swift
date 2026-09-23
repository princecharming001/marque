import SwiftUI

// Code-drawn marks for Settings rows. Monochrome (DESIGN.md §8): every mark sits in a
// surfaceSunken circle and draws its glyph in textPrimary with 1.5pt strokes, so the
// list reads as one quiet column in light and dark. Meaning comes from the glyph shape,
// never from a tint.

/// Circular tile shell every mark in this file sits inside. `fill` is kept for call-site
/// compatibility; the monochrome system always uses surfaceSunken.
struct SettingsIconTile<Content: View>: View {
    var fill: AnyShapeStyle
    var size: CGFloat = 32
    @ViewBuilder var content: Content

    init(fill: some ShapeStyle = Palette.surfaceSunken, size: CGFloat = 32,
         @ViewBuilder content: () -> Content) {
        self.fill = AnyShapeStyle(fill)
        self.size = size
        self.content = content()
    }

    var body: some View {
        ZStack { content }
            .foregroundStyle(Palette.textPrimary)
            .frame(width: size, height: size)
            .background(Circle().fill(fill))
            .accessibilityHidden(true)
    }
}

/// Notifications: a "ping" abstraction — a solid core with two rings expanding outward —
/// instead of a literal bell.
struct NotificationMark: View {
    var body: some View {
        SettingsIconTile(fill: Palette.surfaceSunken) {
            ZStack {
                Circle().strokeBorder(Palette.textPrimary.opacity(0.35), lineWidth: 1.5)
                    .frame(width: 22, height: 22)
                Circle().strokeBorder(Palette.textPrimary.opacity(0.7), lineWidth: 1.5)
                    .frame(width: 14, height: 14)
                Circle().fill(Palette.textPrimary).frame(width: 6, height: 6)
            }
        }
    }
}

/// Subscription / Pro: an abstract bloom mark — three offset stroked circles at 120°,
/// radiating from a filled core.
struct ProMark: View {
    var body: some View {
        SettingsIconTile(fill: Palette.surfaceSunken) {
            ZStack {
                ForEach(0..<3) { i in
                    Circle().strokeBorder(Palette.textPrimary.opacity(0.7), lineWidth: 1.5)
                        .frame(width: 13, height: 13)
                        .offset(y: -6)
                        .rotationEffect(.degrees(Double(i) * 120))
                }
                Circle().fill(Palette.textPrimary).frame(width: 5, height: 5)
            }
        }
    }
}

/// The secondary-tier variant of ProMark — same bloom family, a single ring.
struct PlusMark: View {
    var body: some View {
        SettingsIconTile(fill: Palette.surfaceSunken) {
            ZStack {
                Circle().strokeBorder(Palette.textPrimary, lineWidth: 1.5).frame(width: 15, height: 15)
                Circle().fill(Palette.textPrimary).frame(width: 5, height: 5)
            }
        }
    }
}

/// Account: the creator's own initial in a sunken circle — a real identity mark instead
/// of a generic silhouette. Falls back to a dot if the name/email is empty.
struct AccountAvatarMark: View {
    let label: String

    private var initial: String {
        let t = label.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? "•" : String(t.prefix(1)).uppercased()
    }

    var size: CGFloat = 32

    var body: some View {
        SettingsIconTile(fill: Palette.surfaceSunken, size: size) {
            Text(initial)
                .font(size >= 44 ? AppFont.title3 : AppFont.supporting.weight(.semibold))
                .foregroundStyle(Palette.textPrimary)
        }
    }
}

/// Data & Privacy: a lock built from a stroked arc (shackle) over a filled body.
struct PrivacyMark: View {
    var body: some View {
        SettingsIconTile(fill: Palette.surfaceSunken) {
            ZStack {
                Circle().trim(from: 0.52, to: 0.98)
                    .stroke(Palette.textPrimary, style: StrokeStyle(lineWidth: 1.5, lineCap: .round))
                    .frame(width: 12, height: 12)
                    .offset(y: -4)
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .fill(Palette.textPrimary)
                    .frame(width: 14, height: 10)
                    .offset(y: 3)
            }
        }
    }
}

/// Support: three dots inside a ring — an abstract "conversation" mark.
struct SupportMark: View {
    var body: some View {
        SettingsIconTile(fill: Palette.surfaceSunken) {
            ZStack {
                Circle().strokeBorder(Palette.textPrimary.opacity(0.7), lineWidth: 1.5)
                    .frame(width: 22, height: 22)
                HStack(spacing: 2.5) {
                    ForEach(0..<3, id: \.self) { _ in
                        Circle().fill(Palette.textPrimary).frame(width: 3, height: 3)
                    }
                }
            }
        }
    }
}

/// Destructive rows (sign out / delete): a plain SF Symbol — HIG legibility for an
/// exit/destroy action matters more than novelty. Monochrome: the glyph and the confirm
/// dialog carry the meaning, not red.
struct DestructiveMark: View {
    let systemImage: String
    var body: some View {
        SettingsIconTile(fill: Palette.surfaceSunken) {
            Image(systemName: systemImage).font(.system(size: 14, weight: .regular))
        }
    }
}

/// Secondary utility rows (restore, manage, export, legal links, support link, tour) —
/// outline SF Symbols in a sunken circle. `tint` is kept for call-site compatibility and
/// ignored: every glyph is textPrimary.
struct UtilityMark: View {
    let systemImage: String
    var tint: Color = Palette.textPrimary
    var body: some View {
        SettingsIconTile(fill: Palette.surfaceSunken) {
            Image(systemName: systemImage)
                .font(.system(size: 14, weight: .regular))
        }
    }
}
