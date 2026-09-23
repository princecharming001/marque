import SwiftUI
import UIKit

// MARK: - Design system (DESIGN.md). Black and white only: every "color" is a point on a
// grayscale ramp, and every token resolves per color scheme. Emphasis comes from inversion,
// weight, size and tracking, never from hue. Photos and video are the only color on screen.

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

    /// A color that resolves per color scheme (light value, dark value).
    init(light: UInt, dark: UInt, lightAlpha: Double = 1, darkAlpha: Double = 1) {
        self.init(uiColor: UIColor { trait in
            let isDark = trait.userInterfaceStyle == .dark
            let hex = isDark ? dark : light
            return UIColor(
                red: CGFloat((hex >> 16) & 0xFF) / 255,
                green: CGFloat((hex >> 8) & 0xFF) / 255,
                blue: CGFloat(hex & 0xFF) / 255,
                alpha: isDark ? darkAlpha : lightAlpha)
        })
    }
}

enum Palette {
    // Surfaces
    static let canvas = Color(light: 0xF2F2F4, dark: 0x000000)          // page background
    static let surface = Color(light: 0xFFFFFF, dark: 0x111111)         // cards, grouped rows
    static let surfaceRaised = Color(light: 0xFFFFFF, dark: 0x111111)   // (alias of surface)
    static let surfaceSunken = Color(light: 0xEDEDEF, dark: 0x1C1C1C)   // search, tiles, ghost pills

    // Text. Secondary is darker than Stoic's gray on purpose: it must clear 4.5:1 on BOTH
    // canvas and surface (it is used for body-size copy). Tertiary is placeholder/disabled.
    static let textPrimary = Color(light: 0x1A1A1A, dark: 0xFFFFFF)
    static let textSecondary = Color(light: 0x6B6B6B, dark: 0x8E8E8E)
    static let textTertiary = Color(light: 0x8C8C8C, dark: 0x6E6E6E)

    // Lines
    static let hairline = Color(light: 0x000000, dark: 0xFFFFFF, lightAlpha: 0.13, darkAlpha: 0.14)
    static let divider = Color(light: 0xDCDCDC, dark: 0x262626)

    // Ink = the primary CONTROL fill. It inverts in dark mode (black button on light, light
    // button on black), and onInk inverts with it, so `background(ink) + foreground(onInk)`
    // is correct in both schemes.
    static let ink = Color(light: 0x0A0A0A, dark: 0xECECEC)
    static let onInk = Color(light: 0xFFFFFF, dark: 0x000000)

    // Night = a surface that is dark in BOTH schemes (hero cards, promo strips, camera
    // chrome). Text on it is always onNight.
    static let night = Color(light: 0x0A0A0A, dark: 0x161616)
    static let onNight = Color(hex: 0xFFFFFF)
    static let onNightSecondary = Color(hex: 0x9A9A9A)
    static let heroStart = Color(light: 0x1F1C1F, dark: 0x1E1E1E)       // hero gradient, top-left
    static let heroEnd = Color(light: 0x121012, dark: 0x141414)         // hero gradient, bottom-right

    // There is no accent hue. "Accent" (links, selection, focus, system tint) is ink.
    static let accent = ink
    static let accentMuted = Color(light: 0x000000, dark: 0xFFFFFF, lightAlpha: 0.08, darkAlpha: 0.12)

    // Status is monochrome: meaning is carried by a glyph and wording, not by color.
    static let positive = textPrimary
    static let warning = textPrimary
    static let critical = textPrimary
    static let scheduled = textSecondary

    // Back-compat aliases (older code paths).
    static let gold = accent
    static let goldDeep = accent

    // Shadows only ever render in light mode (black on black is invisible).
    static let shadowWarm = Color.black
    static let shadowCool = Color.black

    static let scrim = Color(light: 0x000000, dark: 0x000000, lightAlpha: 0.45, darkAlpha: 0.65)
}

// MARK: - Typography: one geometric grotesk (Matter) for everything. No serif anywhere.

enum Typeface {
    /// Formerly the serif display face. Retired: display text is Matter at the same size,
    /// so every legacy call site renders in the single app typeface.
    static func display(_ size: CGFloat, _ weight: Font.Weight = .bold) -> Font {
        .custom(matter(weight == .semibold ? .bold : weight), size: size)
    }
    static func sans(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        .custom(matter(weight), size: size)
    }
    static func body(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font { sans(size, weight) }

    private static func matter(_ w: Font.Weight) -> String {
        switch w {
        case .bold, .heavy, .black: return "Matter-Bold"
        case .semibold: return "Matter-SemiBold"
        case .medium: return "Matter-Medium"
        case .light, .thin, .ultraLight: return "Matter-Light"
        default: return "Matter-Regular"
        }
    }
}

enum AppFont {
    // DESIGN.md scale (§2). Titles are lowercase and end with a period by convention.
    static let pageTitle = Typeface.sans(34, .bold)      // pushed-page titles ("your library.")
    static let title1 = Typeface.sans(26, .bold)         // sheet titles, prompts, onboarding questions
    static let title2 = Typeface.sans(22, .bold)         // greeting, card titles, promo titles
    static let title3 = Typeface.sans(20, .semibold)     // tile titles, plan names
    static let headline = Typeface.sans(17, .semibold)   // button labels, selected values
    static let bodyText = Typeface.sans(17)              // list rows, card questions, inputs
    static let bodyLarge = Typeface.sans(20)             // editor text, quotes, assistant replies
    static let supporting = Typeface.sans(15)            // secondary copy in cards, footers
    static let caption = Typeface.sans(13)               // timestamps, week strip, tab labels
    static let eyebrow = Typeface.sans(12, .semibold)    // UPPERCASE section labels (Track.eyebrow)
    static let displayMuted = Typeface.sans(30, .bold)   // oversized tertiary taglines
    static let stat = Typeface.sans(28, .bold)           // numbers in stat tiles

    // Legacy tokens, re-pointed onto the scale above (same roles, no serif).
    static let displayXL = pageTitle
    static let displayL = pageTitle
    static let displayM = title1
    static let serifL = title1
    static let serifM = title2
    static let heroNumeral = Typeface.sans(44, .bold)
    static let question = title1
    static let title = title3
    static let bodyL = Typeface.sans(16)
    static let body = Typeface.sans(15)
    static let callout = Typeface.sans(14, .medium)
    static let micro = Typeface.sans(11, .semibold)
}

// Letter-spacing: tight negatives on titles, wide positive on uppercase eyebrows.
enum Track {
    static let hero: CGFloat = -1.5
    static let title: CGFloat = -0.4
    static let tight: CGFloat = -0.2
    static let body: CGFloat = 0.1
    static let label: CGFloat = 1.4
    static let eyebrow: CGFloat = 2.4                    // 0.2em on 12pt
}

// MARK: - Spacing / radii / motion

enum Space {
    static let xxs: CGFloat = 2
    static let xs: CGFloat = 4
    static let sm: CGFloat = 8
    static let md: CGFloat = 16
    static let lg: CGFloat = 20
    static let xl: CGFloat = 24
    static let xxl: CGFloat = 32
    static let huge: CGFloat = 48
    static let screenH: CGFloat = 16          // page margin everywhere
    static let cardPad: CGFloat = 20          // inside cards (24 on hero cards)
    static let rowPad: CGFloat = 16           // horizontal inside grouped rows
    static let stack: CGFloat = 12            // between cards in a vertical list
    static let groupGap: CGFloat = 4          // between adjacent grouped cards
    static let sectionGap: CGFloat = 32       // between sections
}

enum Radius {
    static let sm: CGFloat = 10
    static let md: CGFloat = 14
    static let lg: CGFloat = 18
    static let xl: CGFloat = 22
    static let pill: CGFloat = 999
    static let hero: CGFloat = 32             // hero cards, tall 2:3 tiles
    static let card: CGFloat = 24             // content cards, sheet tops, paywall container
    static let tile: CGFloat = 20             // plan / stat / media tiles
    static let group: CGFloat = 16            // grouped lists, promo strips, form fields
    static let cell: CGFloat = 8              // week-strip day cell
}

enum Motion {
    static let calm = Animation.easeInOut(duration: 0.45)
    static let enter = Animation.easeOut(duration: 0.38)
    static let quick = Animation.spring(response: 0.25, dampingFraction: 0.9)
    static let standard = Animation.spring(response: 0.4, dampingFraction: 0.85)
    static let breath = Animation.easeInOut(duration: 2.4).repeatForever(autoreverses: true)
    static let spring = Animation.spring(response: 0.42, dampingFraction: 0.82)
}

// MARK: - Reusable surfaces

extension View {
    /// Content card: surface fill, separated from the canvas by tone (no border), with a
    /// whisper shadow that only shows in light mode.
    func marqueCard(padding: CGFloat = Space.cardPad, radius: CGFloat = Radius.card) -> some View {
        self
            .padding(padding)
            .background(Palette.surface)
            .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
            .shadow(color: Palette.shadowWarm.opacity(0.04), radius: 12, x: 0, y: 2)
    }

    func screenPadding() -> some View { self.padding(.horizontal, Space.screenH) }
}

// MARK: - Staggered reveal (fade + slide-up entrance, staggered by index).

private struct StaggerReveal: ViewModifier {
    let index: Int
    var distance: CGFloat = 14
    @State private var shown = false

    func body(content: Content) -> some View {
        content
            .opacity(shown ? 1 : 0)
            .offset(y: shown ? 0 : distance)
            .onAppear {
                withAnimation(Motion.enter.delay(Double(index) * 0.055)) {
                    shown = true
                }
            }
    }
}

extension View {
    /// Fade + slide-up entrance, staggered by `index` within its group (0-based).
    func staggerReveal(_ index: Int, distance: CGFloat = 14) -> some View {
        modifier(StaggerReveal(index: index, distance: distance))
    }
}
