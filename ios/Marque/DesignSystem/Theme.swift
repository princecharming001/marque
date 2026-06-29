import SwiftUI

// MARK: - Design system, modeled on maxapp (white/ink/hairline, Playfair serif-display +
// Inter sans-body, soft shadows, ink-fill buttons, blue accent). Light to match maxapp.

extension Color {
    init(hex: UInt, alpha: Double = 1) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: alpha
        )
    }
}

enum Palette {
    // Surfaces
    static let surface = Color(hex: 0xFFFFFF)          // white canvas
    static let surfaceRaised = Color(hex: 0xFFFFFF)    // cards: white + hairline + soft shadow
    static let surfaceSunken = Color(hex: 0xF2F2F2)    // insets, thumbnails
    static let canvas = Color(hex: 0xF1F1EF)           // "Stoic" off-white (onboarding/home)

    // Text
    static let textPrimary = Color(hex: 0x111113)
    static let textSecondary = Color(hex: 0x555555)
    static let textTertiary = Color(hex: 0x9A9A9A)

    // Lines
    static let hairline = Color(hex: 0x000000, alpha: 0.08)
    static let divider = Color(hex: 0xE5E5E5)

    // Ink (fills) + accent
    static let ink = Color(hex: 0x111113)              // primary button fill
    static let onInk = Color(hex: 0xFFFFFF)            // text on ink
    static let accent = Color(hex: 0x2C6BED)           // blue — links, selection, focus
    static let accentMuted = Color(hex: 0x2C6BED, alpha: 0.10)

    // Status
    static let positive = Color(hex: 0x2F9E60)
    static let warning = Color(hex: 0xB5791C)
    static let critical = Color(hex: 0xC0452C)

    // Back-compat aliases (older code paths) — repointed to the new system.
    static let gold = accent
    static let goldDeep = accent
    static let night = ink

    // Warm-tinted shadow color (maxapp uses warm/cool tints, never harsh pure black on light).
    static let shadowWarm = Color(hex: 0x2E2A20)
    static let shadowCool = Color(hex: 0x3A3358)       // the glass float-shadow tint
}

// MARK: - Typography (Playfair serif for hero/editorial; Inter sans for everything else)

enum Typeface {
    // Playfair Display (serif, variable — weighted via .weight) + Inter (Matter-substitute sans).
    static func display(_ size: CGFloat, _ weight: Font.Weight = .semibold) -> Font {
        .custom("PlayfairDisplay-Regular", size: size).weight(weight)
    }
    static func sans(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        .custom(inter(weight), size: size)
    }
    static func body(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font { sans(size, weight) }

    private static func inter(_ w: Font.Weight) -> String {
        switch w {
        case .bold, .heavy, .black: return "Inter-Bold"
        case .semibold: return "Inter-SemiBold"
        case .medium: return "Inter-Medium"
        default: return "Inter-Regular"
        }
    }
}

enum AppFont {
    static let displayXL = Typeface.display(44, .semibold)  // serif hero (onboarding/paywall)
    static let displayL = Typeface.sans(32, .bold)          // sans screen titles
    static let displayM = Typeface.display(28, .semibold)   // serif editorial (hooks, big moments)
    static let serifL = Typeface.display(30, .semibold)     // editorial hero/section title (often lowercase)
    static let serifM = Typeface.display(22, .semibold)     // editorial card title (often lowercase)
    static let heroNumeral = Typeface.sans(44, .bold)       // giant numeral hero (Today)
    static let question = Typeface.sans(30, .bold)          // onboarding step question (sans-bold)
    static let title = Typeface.sans(20, .semibold)
    static let headline = Typeface.sans(17, .semibold)
    static let bodyL = Typeface.sans(16)
    static let body = Typeface.sans(15)
    static let callout = Typeface.sans(14, .medium)
    static let caption = Typeface.sans(13)
    static let micro = Typeface.sans(11, .semibold)         // uppercase labels (apply Track.label)
}

// Letter-spacing system (the premium tell): tight negatives on headlines/numerals, wide
// positives on uppercase micro-labels, neutral on body. Applied via Text.tracking() at call sites.
enum Track {
    static let hero: CGFloat = -1.5     // giant numerals
    static let title: CGFloat = -0.4    // headlines / editorial titles
    static let tight: CGFloat = -0.2    // sub-headlines
    static let body: CGFloat = 0.1
    static let label: CGFloat = 1.4     // UPPERCASE micro-labels
}

// MARK: - Spacing / radii / motion (maxapp scale)

enum Space {
    static let xxs: CGFloat = 2
    static let xs: CGFloat = 4
    static let sm: CGFloat = 8
    static let md: CGFloat = 16
    static let lg: CGFloat = 20
    static let xl: CGFloat = 24
    static let xxl: CGFloat = 32
    static let huge: CGFloat = 48
    static let screenH: CGFloat = 20
}

enum Radius {
    static let sm: CGFloat = 10
    static let md: CGFloat = 14
    static let lg: CGFloat = 18
    static let xl: CGFloat = 22
    static let pill: CGFloat = 999
}

enum Motion {
    static let calm = Animation.easeInOut(duration: 0.45)
    static let enter = Animation.easeOut(duration: 0.38)
    static let quick = Animation.easeInOut(duration: 0.2)
    static let breath = Animation.easeInOut(duration: 2.4).repeatForever(autoreverses: true)
    static let spring = Animation.spring(response: 0.42, dampingFraction: 0.82)  // maxapp 18/170 feel
}

// MARK: - Reusable surfaces

extension View {
    /// White card with a hairline border + soft downward shadow (maxapp elevated card).
    func marqueCard(padding: CGFloat = Space.lg, radius: CGFloat = Radius.xl) -> some View {
        self
            .padding(padding)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1)
            )
            .shadow(color: Palette.shadowWarm.opacity(0.06), radius: 16, x: 0, y: 6)
    }

    func screenPadding() -> some View { self.padding(.horizontal, Space.screenH) }
}
