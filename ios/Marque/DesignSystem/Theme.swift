import SwiftUI
import UIKit

// MARK: - Color primitives & semantic palette (02-design-system.md)
// Stoic-grounded: warm cream / near-black, gold as a whisper. Never pure white/black.

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

private func dyn(light: UInt, dark: UInt) -> Color {
    Color(UIColor { trait in
        let hex = trait.userInterfaceStyle == .dark ? dark : light
        return UIColor(
            red: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    })
}

enum Palette {
    // Primitives
    static let cream = Color(hex: 0xF4F1EA)
    static let night = Color(hex: 0x0E0E10)
    static let gold = Color(hex: 0xC9A227)
    static let goldDeep = Color(hex: 0xA8851A)   // accessible gold for any text use

    // Semantic (auto light/dark)
    static let surface = dyn(light: 0xF4F1EA, dark: 0x0E0E10)
    static let surfaceRaised = dyn(light: 0xFBF9F4, dark: 0x17171A)
    static let surfaceSunken = dyn(light: 0xEDE9E0, dark: 0x080809)
    static let textPrimary = dyn(light: 0x111113, dark: 0xF2EFE8)
    static let textSecondary = dyn(light: 0x6B6760, dark: 0x9C968C)
    static let textTertiary = dyn(light: 0x9A958B, dark: 0x6A655E)
    static let hairline = dyn(light: 0xE2DCD0, dark: 0x26262A)
    static let accent = Color(hex: 0xC9A227)
    static let positive = dyn(light: 0x3F7A57, dark: 0x6FB489)
    static let warning = dyn(light: 0xB5852A, dark: 0xD6A648)
    static let critical = dyn(light: 0xA84A3C, dark: 0xD08274)
}

// MARK: - Typography (serif display + grotesque body)
// System serif stands in for Playfair Display until the OFL fonts are bundled (see Open questions).

enum Typeface {
    static func display(_ size: CGFloat, _ weight: Font.Weight = .semibold) -> Font {
        .system(size: size, weight: weight, design: .serif)
    }
    static func body(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .default)
    }
}

enum AppFont {
    static let displayXL = Typeface.display(40, .bold)
    static let displayL = Typeface.display(32, .semibold)
    static let displayM = Typeface.display(26, .semibold)
    static let title = Typeface.display(21, .semibold)
    static let headline = Typeface.body(17, .semibold)
    static let bodyL = Typeface.body(17)
    static let body = Typeface.body(15)
    static let callout = Typeface.body(14, .medium)
    static let caption = Typeface.body(12, .medium)
    static let micro = Typeface.body(11, .semibold)
}

// MARK: - Spacing / radii / motion

enum Space {
    static let xxs: CGFloat = 2
    static let xs: CGFloat = 4
    static let sm: CGFloat = 8
    static let md: CGFloat = 12
    static let lg: CGFloat = 16
    static let xl: CGFloat = 24
    static let xxl: CGFloat = 32
    static let huge: CGFloat = 48
    static let screenH: CGFloat = 20  // screen horizontal inset
}

enum Radius {
    static let sm: CGFloat = 10
    static let md: CGFloat = 16
    static let lg: CGFloat = 22
    static let pill: CGFloat = 999
}

enum Motion {
    static let calm = Animation.easeInOut(duration: 0.55)
    static let enter = Animation.easeOut(duration: 0.4)
    static let quick = Animation.easeInOut(duration: 0.22)
    static let breath = Animation.easeInOut(duration: 2.4).repeatForever(autoreverses: true)
}

// MARK: - Reusable modifiers

extension View {
    /// Standard calm card surface.
    func marqueCard(padding: CGFloat = Space.lg) -> some View {
        self
            .padding(padding)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.05), radius: 14, x: 0, y: 8)
    }

    func screenPadding() -> some View {
        self.padding(.horizontal, Space.screenH)
    }
}
