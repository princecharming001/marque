import SwiftUI

// MARK: - DESIGN.md §5 component kit. Every screen is assembled from these; nothing here
// knows about app state. All colors come from Palette, so light and dark both hold.

// MARK: Buttons

/// Capsule button in one of four tones. Content-sized and centered unless `fullWidth`.
struct DSCapsuleStyle: ButtonStyle {
    enum Kind {
        case primary     // ink fill, onInk label (inverts in dark mode)
        case outline     // surface fill + hairline, primary label
        case ghost       // sunken fill, secondary label
        case inverse     // white fill, black label: the CTA inside a night/hero card
    }
    var kind: Kind = .primary
    var height: CGFloat = 56
    var fullWidth: Bool = false

    func makeBody(configuration: Configuration) -> some View {
        DSCapsuleBody(configuration: configuration, kind: kind, height: height, fullWidth: fullWidth)
    }
}

private struct DSCapsuleBody: View {
    let configuration: ButtonStyle.Configuration
    let kind: DSCapsuleStyle.Kind
    let height: CGFloat
    let fullWidth: Bool
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        configuration.label
            .font(height >= 52 ? AppFont.headline : AppFont.supporting.weight(.semibold))
            .lineLimit(1).minimumScaleFactor(0.85)
            .foregroundStyle(foreground)
            .padding(.horizontal, height >= 52 ? 32 : 20)
            .frame(minWidth: fullWidth ? nil : (height >= 52 ? 160 : 0))
            .frame(maxWidth: fullWidth ? .infinity : nil)
            .frame(height: height)
            .background(Capsule().fill(fill))
            .overlay(Capsule().strokeBorder(stroke, lineWidth: 1))
            .contentShape(Capsule())
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .opacity(configuration.isPressed ? 0.9 : 1)
            .animation(Motion.quick, value: configuration.isPressed)
    }

    private var fill: Color {
        guard isEnabled else { return kind == .inverse ? Palette.onNight.opacity(0.55) : Palette.surfaceSunken }
        switch kind {
        case .primary: return Palette.ink
        case .outline: return Palette.surface
        case .ghost: return Palette.surfaceSunken
        case .inverse: return Palette.onNight
        }
    }
    private var foreground: Color {
        guard isEnabled else { return kind == .inverse ? Color(hex: 0x0A0A0A).opacity(0.7) : Palette.textTertiary }
        switch kind {
        case .primary: return Palette.onInk
        case .outline: return Palette.textPrimary
        case .ghost: return Palette.textSecondary
        case .inverse: return Color(hex: 0x0A0A0A)
        }
    }
    private var stroke: Color {
        (kind == .outline && isEnabled) ? Palette.hairline : .clear
    }
}

extension ButtonStyle where Self == DSCapsuleStyle {
    static var dsPrimary: DSCapsuleStyle { DSCapsuleStyle(kind: .primary) }
    static var dsOutline: DSCapsuleStyle { DSCapsuleStyle(kind: .outline) }
    static var dsGhost: DSCapsuleStyle { DSCapsuleStyle(kind: .ghost, height: 40) }
    static var dsInverse: DSCapsuleStyle { DSCapsuleStyle(kind: .inverse, height: 48) }
    static func ds(_ kind: DSCapsuleStyle.Kind, height: CGFloat = 56, fullWidth: Bool = false) -> DSCapsuleStyle {
        DSCapsuleStyle(kind: kind, height: height, fullWidth: fullWidth)
    }
}

/// Centered text link ("Restore purchase", "Terms").
struct DSTextLinkStyle: ButtonStyle {
    var color: Color = Palette.textPrimary
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(AppFont.bodyText)
            .foregroundStyle(color)
            .opacity(configuration.isPressed ? 0.5 : 1)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
    }
}

extension ButtonStyle where Self == DSTextLinkStyle {
    static var dsLink: DSTextLinkStyle { DSTextLinkStyle() }
}

/// Round control: `.filled` (ink), `.outline` (surface + hairline), `.onNight` (translucent
/// white over media or night surfaces).
struct DSCircleButton: View {
    enum Kind { case filled, outline, onNight }
    let systemName: String
    var kind: Kind = .outline
    var size: CGFloat = 48
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: size * 0.4, weight: .regular))
                .foregroundStyle(foreground)
                .frame(width: size, height: size)
                .background(Circle().fill(fill))
                .overlay(Circle().strokeBorder(stroke, lineWidth: 1))
                .contentShape(Circle())
        }
        .buttonStyle(PressableStyle(dim: 0.85, scale: 0.92))
    }
    private var fill: Color {
        switch kind {
        case .filled: return Palette.ink
        case .outline: return Palette.surface
        case .onNight: return Color.white.opacity(0.14)
        }
    }
    private var foreground: Color {
        switch kind {
        case .filled: return Palette.onInk
        case .outline: return Palette.textPrimary
        case .onNight: return Palette.onNight
        }
    }
    private var stroke: Color {
        switch kind {
        case .filled: return .clear
        case .outline: return Palette.hairline
        case .onNight: return Color.white.opacity(0.28)
        }
    }
}

/// Bare glyph button with a 44pt hit area (close, back, share, star).
struct DSIconButton: View {
    let systemName: String
    var color: Color = Palette.textPrimary
    var size: CGFloat = 20
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: size, weight: .regular))
                .foregroundStyle(color)
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(PressableStyle(dim: 0.6))
    }
}

// MARK: Type helpers

/// UPPERCASE tracked section label ("GET INSPIRED", "ACCOUNT").
struct DSEyebrow: View {
    let text: String
    var color: Color = Palette.textSecondary
    var body: some View {
        Text(text.uppercased())
            .font(AppFont.eyebrow).tracking(Track.eyebrow)
            .foregroundStyle(color)
    }
}

/// Lowercase-with-period title helper: "Your library" -> "your library."
func dsTitle(_ s: String) -> String {
    let t = s.trimmingCharacters(in: .whitespaces).lowercased()
    guard let last = t.last else { return t }
    return ".!?".contains(last) ? t : t + "."
}

// MARK: Cards

extension View {
    /// Content card variants. `.surface` = white card, `.sunken` = gray tile,
    /// `.outline` = hairline border only (empty / completed states).
    func dsCard(_ style: DSCardStyle = .surface, radius: CGFloat = Radius.card,
                padding: CGFloat = Space.cardPad) -> some View {
        self.padding(padding)
            .background(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(style.fill))
            .overlay(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(style == .outline ? Palette.hairline : .clear, lineWidth: 1))
            .shadow(color: style == .surface ? Palette.shadowWarm.opacity(0.04) : .clear,
                    radius: 12, x: 0, y: 2)
    }
}

enum DSCardStyle: Equatable {
    case surface, sunken, outline
    var fill: Color {
        switch self {
        case .surface: return Palette.surface
        case .sunken: return Palette.surfaceSunken
        case .outline: return .clear
        }
    }
}

/// The one dark card per screen: a near-black diagonal gradient that stays dark in both
/// schemes (with a hairline in dark mode so it separates from the black canvas).
struct DSHeroCard<Content: View>: View {
    var radius: CGFloat = Radius.hero
    var padding: CGFloat = 24
    @ViewBuilder var content: () -> Content
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        content()
            .padding(padding)
            .frame(maxWidth: .infinity)
            .background(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(LinearGradient(colors: [Palette.heroStart, Palette.heroEnd],
                                         startPoint: .topLeading, endPoint: .bottomTrailing)))
            .overlay(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(scheme == .dark ? Palette.hairline : .clear, lineWidth: 1))
            .environment(\.dsOnNight, true)
    }
}

/// Flat night strip, left-aligned text, optional decorative glyph bottom-trailing.
struct DSPromoStrip: View {
    let title: String
    var message: String? = nil
    var systemImage: String? = nil
    var body: some View {
        HStack(alignment: .top, spacing: Space.md) {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(AppFont.title3).foregroundStyle(Palette.onNight)
                if let message {
                    Text(message).font(AppFont.supporting).foregroundStyle(Palette.onNight.opacity(0.85))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(Space.cardPad)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.night))
        .overlay(alignment: .bottomTrailing) {
            if let systemImage {
                Image(systemName: systemImage)
                    .font(.system(size: 44, weight: .ultraLight))
                    .foregroundStyle(Palette.onNight.opacity(0.3))
                    .padding(Space.md)
                    .accessibilityHidden(true)
            }
        }
    }
}

private struct DSOnNightKey: EnvironmentKey { static let defaultValue = false }
extension EnvironmentValues {
    /// True inside a hero/night surface, so nested components can pick onNight colors.
    var dsOnNight: Bool {
        get { self[DSOnNightKey.self] }
        set { self[DSOnNightKey.self] = newValue }
    }
}

// MARK: Grouped lists

/// Eyebrow + grouped card. The eyebrow aligns with the row text (16pt inside the card).
struct DSSection<Content: View>: View {
    var eyebrow: String? = nil
    var footer: String? = nil
    @ViewBuilder var content: () -> Content
    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            if let eyebrow {
                DSEyebrow(text: eyebrow).padding(.horizontal, Space.rowPad)
            }
            DSGroup(content: content)
            if let footer {
                Text(footer).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    .padding(.horizontal, Space.rowPad)
            }
        }
    }
}

/// White rounded container for rows. Put `DSRowDivider()` between rows.
struct DSGroup<Content: View>: View {
    @ViewBuilder var content: () -> Content
    var body: some View {
        VStack(spacing: 0) { content() }
            .background(
                RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.surface))
            .clipShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
    }
}

/// 1pt separator inset 16pt from the leading edge (aligned with row text).
struct DSRowDivider: View {
    var inset: CGFloat = Space.rowPad
    var body: some View {
        Rectangle().fill(Palette.hairline).frame(height: 1).padding(.leading, inset)
    }
}

/// Standard 52pt row: optional leading glyph, title (+ subtitle), trailing value and/or chevron.
struct DSRow: View {
    let title: String
    var subtitle: String? = nil
    var systemImage: String? = nil
    var value: String? = nil
    var showsChevron: Bool = true
    var destructive: Bool = false
    var action: (() -> Void)? = nil

    var body: some View {
        if let action {
            Button(action: action) { rowBody }.buttonStyle(DSRowPressStyle())
        } else {
            rowBody
        }
    }

    private var rowBody: some View {
        HStack(spacing: Space.md) {
            if let systemImage {
                Image(systemName: systemImage)
                    .font(.system(size: 18, weight: .regular))
                    .foregroundStyle(Palette.textPrimary)
                    .frame(width: 24)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(AppFont.bodyText)
                    .foregroundStyle(Palette.textPrimary)
                if let subtitle {
                    Text(subtitle).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: Space.sm)
            if let value {
                Text(value).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    .lineLimit(1)
            }
            if showsChevron && action != nil {
                Image(systemName: "chevron.right")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Palette.textPrimary)
            }
        }
        .padding(.horizontal, Space.rowPad)
        .frame(minHeight: 52)
        .contentShape(Rectangle())
    }
}

/// Row with a trailing monochrome switch.
struct DSToggleRow: View {
    let title: String
    var subtitle: String? = nil
    var systemImage: String? = nil
    @Binding var isOn: Bool
    var body: some View {
        HStack(spacing: Space.md) {
            if let systemImage {
                Image(systemName: systemImage)
                    .font(.system(size: 18, weight: .regular))
                    .foregroundStyle(Palette.textPrimary).frame(width: 24)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                if let subtitle {
                    Text(subtitle).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: Space.sm)
            Toggle("", isOn: $isOn).labelsHidden().toggleStyle(.ds)
        }
        .padding(.horizontal, Space.rowPad)
        .frame(minHeight: 52)
        .accessibilityElement(children: .combine)
    }
}

/// Row with a trailing circular check (filters, single/multi select).
struct DSCheckRow: View {
    let title: String
    var subtitle: String? = nil
    var isOn: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.md) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                    if let subtitle {
                        Text(subtitle).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    }
                }
                Spacer(minLength: Space.sm)
                DSCheckmark(isOn: isOn)
            }
            .padding(.horizontal, Space.rowPad)
            .frame(minHeight: 52)
            .contentShape(Rectangle())
        }
        .buttonStyle(DSRowPressStyle())
        .accessibilityAddTraits(isOn ? .isSelected : [])
    }
}

struct DSCheckmark: View {
    var isOn: Bool
    var size: CGFloat = 22
    var body: some View {
        ZStack {
            Circle().fill(isOn ? Palette.ink : .clear)
            Circle().strokeBorder(isOn ? .clear : Palette.hairline, lineWidth: 1.5)
            if isOn {
                Image(systemName: "checkmark")
                    .font(.system(size: size * 0.45, weight: .bold))
                    .foregroundStyle(Palette.onInk)
            }
        }
        .frame(width: size, height: size)
        .animation(Motion.quick, value: isOn)
    }
}

struct DSRowPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(configuration.isPressed ? Palette.surfaceSunken : .clear)
    }
}

// MARK: Switch

/// Monochrome switch: track ink when on (black light / white dark), knob is the opposite.
struct DSToggleStyle: ToggleStyle {
    /// Off-state knob: white on light, mid-gray on dark so the off switch stays visible on
    /// near-black rows.
    static let offKnob = Color(light: 0xFFFFFF, dark: 0x8E8E8E)
    func makeBody(configuration: Configuration) -> some View {
        Button { configuration.isOn.toggle() } label: {
            ZStack(alignment: configuration.isOn ? .trailing : .leading) {
                Capsule()
                    .fill(configuration.isOn ? Palette.ink : Palette.surfaceSunken)
                    .overlay(Capsule().strokeBorder(configuration.isOn ? .clear : Palette.hairline, lineWidth: 1))
                    .frame(width: 51, height: 31)
                Circle()
                    .fill(configuration.isOn ? Palette.onInk : Self.offKnob)
                    .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: configuration.isOn ? 0 : 1))
                    .frame(width: 27, height: 27)
                    .padding(2)
            }
            .animation(Motion.quick, value: configuration.isOn)
        }
        .buttonStyle(.plain)
        .accessibilityRepresentation { Toggle(isOn: configuration.$isOn) { configuration.label } }
    }
}

extension ToggleStyle where Self == DSToggleStyle {
    static var ds: DSToggleStyle { DSToggleStyle() }
}

// MARK: Headers

struct DSDragIndicator: View {
    var body: some View {
        Capsule().fill(Palette.textTertiary.opacity(0.5)).frame(width: 36, height: 5)
            .padding(.top, 8)
    }
}

/// Sheet header: optional back chevron, centered lowercase title (optional eyebrow above),
/// optional close glyph.
struct DSSheetHeader: View {
    let title: String
    var eyebrow: String? = nil
    var onBack: (() -> Void)? = nil
    var onClose: (() -> Void)? = nil
    var closeIdentifier: String? = nil

    var body: some View {
        ZStack {
            VStack(spacing: 4) {
                if let eyebrow { DSEyebrow(text: eyebrow) }
                Text(title).font(AppFont.title1).foregroundStyle(Palette.textPrimary)
                    .multilineTextAlignment(.center)
            }
            .padding(.horizontal, 52)
            HStack {
                if let onBack {
                    DSIconButton(systemName: "chevron.left", action: onBack)
                        .accessibilityLabel("Back")
                }
                Spacer()
                if let onClose {
                    DSIconButton(systemName: "xmark", action: onClose)
                        .accessibilityLabel("Close")
                        .accessibilityIdentifier(closeIdentifier ?? "sheet.close")
                }
            }
            .padding(.horizontal, Space.xs)
        }
        .padding(.top, Space.sm)
    }
}

/// Flow header: back · progress dashes · close.
struct DSFlowHeader: View {
    let total: Int
    let current: Int
    var onBack: (() -> Void)? = nil
    var onClose: (() -> Void)? = nil
    var body: some View {
        ZStack {
            DSProgressDashes(total: total, current: current)
            HStack {
                if let onBack { DSIconButton(systemName: "chevron.left", action: onBack).accessibilityLabel("Back") }
                Spacer()
                if let onClose { DSIconButton(systemName: "xmark", action: onClose).accessibilityLabel("Close") }
            }
        }
        .frame(height: 44)
    }
}

/// Step dashes: 20×2, gap 8; filled = primary text color, remainder = hairline.
struct DSProgressDashes: View {
    let total: Int
    let current: Int
    var body: some View {
        HStack(spacing: 8) {
            ForEach(0..<max(total, 0), id: \.self) { i in
                Capsule()
                    .fill(i < current ? Palette.textPrimary : Palette.hairline)
                    .frame(width: 20, height: 2)
            }
        }
        .animation(Motion.standard, value: current)
        .accessibilityElement()
        .accessibilityLabel("Step \(min(current, total)) of \(total)")
    }
}

/// Pushed-page title block: lowercase display title + optional subtitle, left aligned.
struct DSPageTitle: View {
    let title: String
    var subtitle: String? = nil
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(AppFont.pageTitle).tracking(-0.5).foregroundStyle(Palette.textPrimary)
            if let subtitle {
                Text(subtitle).font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityAddTraits(.isHeader)
    }
}

// MARK: Data display

struct DSStatTile: View {
    let value: String
    let label: String
    var body: some View {
        VStack(spacing: 4) {
            Text(value).font(AppFont.stat).tracking(-0.3).foregroundStyle(Palette.textPrimary)
                .lineLimit(1).minimumScaleFactor(0.6)
            Text(label).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center).lineLimit(2)
        }
        .frame(maxWidth: .infinity, minHeight: 84)
        .padding(.horizontal, Space.sm)
        .background(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous).fill(Palette.surfaceSunken))
        .accessibilityElement(children: .combine)
    }
}

/// One day in the 7-day strip.
struct DSWeekDay: Identifiable, Hashable {
    let id: String
    let weekday: String      // "Mo"
    let number: String       // "14"
    var done: Bool = false
    var isToday: Bool = false
    var isFuture: Bool = false
}

struct DSWeekStrip: View {
    let days: [DSWeekDay]
    var body: some View {
        HStack(spacing: 0) {
            ForEach(days) { d in
                VStack(spacing: 4) {
                    Text(d.weekday).font(AppFont.caption)
                        .foregroundStyle(d.isToday ? Palette.textPrimary : Palette.textSecondary)
                        .fontWeight(d.isToday ? .semibold : .regular)
                    Group {
                        if d.done {
                            Image(systemName: "checkmark").font(.system(size: 15, weight: .semibold))
                        } else {
                            Text(d.number).font(AppFont.bodyText.weight(d.isToday ? .bold : .regular))
                        }
                    }
                    .foregroundStyle(d.isFuture ? Palette.textTertiary : Palette.textPrimary)
                    .frame(height: 22)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: Radius.cell, style: .continuous)
                        .strokeBorder(d.isToday ? Palette.hairline : .clear, lineWidth: 1.5))
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(d.weekday) \(d.number)\(d.done ? ", done" : "")")
            }
        }
    }
}

/// "Preparing" row: done (check), active (spinner), pending (muted).
struct DSChecklistRow: View {
    enum State { case done, active, pending }
    let title: String
    var state: State
    var body: some View {
        HStack(spacing: Space.md) {
            Text(title)
                .font(AppFont.bodyText.weight(state == .pending ? .regular : .semibold))
                .foregroundStyle(state == .pending ? Palette.textTertiary : Palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: Space.sm)
            switch state {
            case .done:
                Image(systemName: "checkmark").font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Palette.textPrimary)
            case .active:
                ProgressView().controlSize(.small).tint(Palette.textPrimary)
            case .pending:
                EmptyView()
            }
        }
        .padding(.horizontal, Space.rowPad)
        .frame(minHeight: 56)
        .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
            .fill(state == .pending ? Palette.surfaceSunken.opacity(0.6) : Palette.surface))
        .animation(Motion.standard, value: state)
        .accessibilityElement(children: .combine)
    }
}

/// Journey-style timeline row: glyph, eyebrow + title, trailing time, optional preview.
struct DSTimelineRow: View {
    var systemImage: String? = nil
    var eyebrow: String? = nil
    let title: String
    var trailing: String? = nil
    var preview: String? = nil
    var body: some View {
        HStack(alignment: .top, spacing: Space.md) {
            if let systemImage {
                Image(systemName: systemImage).font(.system(size: 20, weight: .regular))
                    .foregroundStyle(Palette.textPrimary).frame(width: 28)
            }
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 2) {
                        if let eyebrow { DSEyebrow(text: eyebrow) }
                        Text(title).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    }
                    Spacer(minLength: Space.sm)
                    if let trailing {
                        Text(trailing.uppercased()).font(AppFont.eyebrow).tracking(1)
                            .foregroundStyle(Palette.textSecondary)
                    }
                }
                if let preview {
                    Text(preview).font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                        .lineLimit(3)
                }
            }
        }
        .padding(Space.rowPad)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.surface))
        .accessibilityElement(children: .combine)
    }
}

/// Centered empty state with an optional ghost-pill action.
struct DSEmptyState: View {
    let systemImage: String
    let title: String
    var message: String? = nil
    var actionTitle: String? = nil
    var action: (() -> Void)? = nil
    var body: some View {
        VStack(spacing: Space.sm) {
            Image(systemName: systemImage).font(.system(size: 22, weight: .regular))
                .foregroundStyle(Palette.textSecondary).padding(.bottom, Space.xs)
            Text(title).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
            if let message {
                Text(message).font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                    .multilineTextAlignment(.center).frame(maxWidth: 300)
            }
            if let actionTitle, let action {
                Button(actionTitle, action: action).buttonStyle(.dsGhost).padding(.top, Space.sm)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Space.huge)
    }
}

/// Capsule chip (DESIGN.md inputs). Selected = ink, unselected = surface + hairline.
struct DSChip: View {
    let title: String
    var systemImage: String? = nil
    var isSelected: Bool = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let systemImage { Image(systemName: systemImage).font(.system(size: 13, weight: .semibold)) }
                Text(title).font(AppFont.supporting.weight(isSelected ? .semibold : .regular))
            }
            .foregroundStyle(isSelected ? Palette.onInk : Palette.textPrimary)
            .padding(.horizontal, 14).frame(height: 36)
            .background(Capsule().fill(isSelected ? Palette.ink : Palette.surface))
            .overlay(Capsule().strokeBorder(isSelected ? .clear : Palette.hairline, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(PressableStyle(dim: 0.8))
        .animation(Motion.quick, value: isSelected)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

/// Full-width stacked option capsule (onboarding answers). Selected = ink.
struct DSOptionButton: View {
    let title: String
    var subtitle: String? = nil
    var systemImage: String? = nil
    var isSelected: Bool
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.sm) {
                if let systemImage { Image(systemName: systemImage).font(.system(size: 16, weight: .regular)) }
                VStack(spacing: 2) {
                    Text(title).font(AppFont.bodyText.weight(.semibold))
                    if let subtitle {
                        Text(subtitle).font(AppFont.caption)
                            .foregroundStyle(isSelected ? Palette.onInk.opacity(0.75) : Palette.textSecondary)
                    }
                }
                .multilineTextAlignment(.center)
            }
            .foregroundStyle(isSelected ? Palette.onInk : Palette.textPrimary)
            .frame(maxWidth: .infinity, minHeight: subtitle == nil ? 56 : 64)
            .padding(.horizontal, Space.md)
            .background(Capsule().fill(isSelected ? Palette.ink : Palette.surface))
            .overlay(Capsule().strokeBorder(isSelected ? .clear : Palette.hairline, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(PressableStyle(dim: 0.85))
        .animation(Motion.quick, value: isSelected)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

/// Top toast (offline, saved, errors): ink capsule, onInk caption.
struct DSToast: View {
    let text: String
    var systemImage: String? = nil
    var body: some View {
        HStack(spacing: 6) {
            if let systemImage { Image(systemName: systemImage).font(.system(size: 12, weight: .semibold)) }
            Text(text).font(AppFont.caption.weight(.semibold))
        }
        .foregroundStyle(Palette.onInk)
        .padding(.horizontal, 14).frame(height: 32)
        .background(Capsule().fill(Palette.ink))
        .accessibilityElement(children: .combine)
    }
}

/// Streak counter pill (Today header, leading slot).
struct DSStreakPill: View {
    let count: Int
    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "flame.fill").font(.system(size: 13, weight: .semibold))
            Text("\(count)").font(AppFont.headline)
        }
        .foregroundStyle(count > 0 ? Palette.textPrimary : Palette.textTertiary)
        .padding(.horizontal, 12).frame(height: 32)
        .background(Capsule().fill(count > 0 ? Palette.surface : .clear))
        .overlay(Capsule().strokeBorder(count > 0 ? .clear : Palette.hairline, lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(count) day streak")
    }
}
