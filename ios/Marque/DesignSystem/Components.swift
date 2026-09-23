import SwiftUI

// MARK: - Shared components (DESIGN.md §5: capsule buttons, tone-separated cards, no hue)

struct PressableStyle: ButtonStyle {
    var dim: Double = 0.9
    var scale: CGFloat = 0.97
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? dim : 1)
            .scaleEffect(configuration.isPressed ? scale : 1)
            .animation(Motion.quick, value: configuration.isPressed)
    }
}

// Slow diagonal highlight sweep on premium CTAs (maxapp signature: ~90px streak rotated 18°,
// quick sweep then a long pause).
struct ShineSweep: View {
    @State private var x: CGFloat = -1.0
    var body: some View {
        GeometryReader { geo in
            LinearGradient(colors: [.clear, .white.opacity(0.18), .clear],
                           startPoint: .leading, endPoint: .trailing)
                .frame(width: 90)
                .rotationEffect(.degrees(18))
                .offset(x: x * (geo.size.width * 0.7 + 90))
                .onAppear {
                    withAnimation(.easeInOut(duration: 1.4).repeatForever(autoreverses: false).delay(2.0)) {
                        x = 1.0
                    }
                }
        }
        .allowsHitTesting(false)
    }
}

// MARK: - LiquidGlass (maxapp's "Apple liquid glass" surface)
// Native blur material + corner speculars + top sheen + luminous top rim + cool float shadow.
// Reads as glass ONLY over contrasty content — use on the tab bar, the center FAB, controls
// over media/camera, and media-hero overlays. NOT on flat white cards (rejected on light bg).

struct LiquidGlassFill: View {
    var radius: CGFloat = 24
    var tint: Color? = nil
    var sheen: Double = 1
    var corners: Bool = true
    var body: some View {
        ZStack {
            Rectangle().fill(.ultraThinMaterial)
            if let tint { tint.opacity(0.45) }
            Color.white.opacity(0.10)                       // milky lift
            if corners {
                RadialGradient(colors: [.white.opacity(0.9 * sheen), .white.opacity(0.12 * sheen), .clear],
                               center: .topLeading, startRadius: 0, endRadius: 130)
                RadialGradient(colors: [.white.opacity(0.5 * sheen), .clear],
                               center: .bottomTrailing, startRadius: 0, endRadius: 90)
            }
            LinearGradient(colors: [.white.opacity(0.55 * sheen), .white.opacity(0.06 * sheen), .clear],
                           startPoint: .top, endPoint: .bottom)
            VStack(spacing: 0) {                            // luminous top rim
                Rectangle().fill(Color.white.opacity(0.95)).frame(height: 1.5)
                Spacer(minLength: 0)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
        .allowsHitTesting(false)
    }
}

struct LiquidGlass<Content: View>: View {
    var radius: CGFloat = 24
    var tint: Color? = nil
    var sheen: Double = 1
    @ViewBuilder var content: () -> Content
    var body: some View {
        content()
            .background(LiquidGlassFill(radius: radius, tint: tint, sheen: sheen))
            .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: radius, style: .continuous)
                .strokeBorder(Color.white.opacity(0.62), lineWidth: 1))
            .shadow(color: Palette.shadowCool.opacity(0.22), radius: 26, x: 0, y: 14)
    }
}

// MARK: - Sized Liquid Glass shortcuts (built on LiquidGlassFill above).
// Same "over contrasty content only" rule — camera/media surfaces, not flat light cards.

extension View {
    /// Circular glass surface (pause/play buttons, close buttons, chip-style icon controls).
    func marqueGlassCircle(diameter: CGFloat, tint: Color? = nil) -> some View {
        self.frame(width: diameter, height: diameter)
            .background(LiquidGlassFill(radius: diameter / 2, tint: tint, corners: false))
            .clipShape(Circle())
    }

    /// Capsule/pill glass surface (segmented controls, format chips over camera).
    func marqueGlassCapsule(height: CGFloat, tint: Color? = nil) -> some View {
        self.frame(height: height)
            .background(LiquidGlassFill(radius: height / 2, tint: tint, corners: false))
            .clipShape(Capsule())
    }
}

// Frosted secondary action (maxapp glass variant) — for use over media/contrasty surfaces.
struct GlassButton: View {
    let title: String
    var systemImage: String? = nil
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.sm) {
                if let s = systemImage { Image(systemName: s).font(.system(size: 15, weight: .semibold)) }
                Text(title).font(AppFont.headline)
            }
            .foregroundStyle(Palette.onNight)
            .frame(maxWidth: .infinity).frame(height: 56)
            .background(Capsule().fill(Color.white.opacity(0.14)))
            .overlay(Capsule().strokeBorder(Color.white.opacity(0.35), lineWidth: 1))
        }
        .buttonStyle(PressableStyle(dim: 0.7))
    }
}

/// Primary capsule (DESIGN.md §5). Full width by default because every existing call site
/// sits in a full-width slot; pass `fullWidth: false` for the centered content-sized capsule.
struct PrimaryButton: View {
    let title: String
    var systemImage: String? = nil
    var shine: Bool = false            // kept for call-site compatibility; no sweep in the mono system
    var fullWidth: Bool = true
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.sm) {
                if let s = systemImage { Image(systemName: s).font(.system(size: 16, weight: .semibold)) }
                Text(title).font(AppFont.headline)
            }
        }
        .buttonStyle(DSCapsuleStyle(kind: .primary, fullWidth: fullWidth))
    }
}

/// Outline capsule: surface fill + hairline, primary text.
struct GhostButton: View {
    let title: String
    var systemImage: String? = nil
    var fullWidth: Bool = true
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.sm) {
                if let s = systemImage { Image(systemName: s).font(.system(size: 15, weight: .medium)) }
                Text(title).font(AppFont.headline)
            }
        }
        .buttonStyle(DSCapsuleStyle(kind: .outline, fullWidth: fullWidth))
    }
}

struct SectionTitle: View {
    let text: String
    var body: some View { DSEyebrow(text: text) }
}

/// Big editorial screen title. Case is left to the caller so it matches the
/// hand-rolled titles on Film/Library ("Film", "Library" — capitalized).
struct ScreenTitle: View {
    let text: String
    var size: CGFloat = 34
    var body: some View {
        Text(text)
            .font(Typeface.sans(size, .bold))
            .tracking(size >= 28 ? -0.5 : Track.tight)
            .foregroundStyle(Palette.textPrimary)
    }
}

/// Section eyebrow. `accent` is accepted for call-site compatibility and ignored: the mono
/// system has no colored bars.
struct SectionLabel: View {
    let text: String
    var accent: Color? = nil
    var body: some View { DSEyebrow(text: text) }
}

/// Warm hairline for zone breaks between sections (heavier than the shared card hairline).
struct MarqueHairline: View {
    var body: some View {
        Rectangle().fill(Palette.hairline).frame(height: 1)
    }
}

/// Minimal underline tab bar (maxapp: active = ink text + 2px ink underline; inactive = muted).
struct UnderlineTabBar: View {
    let tabs: [String]
    @Binding var index: Int
    var body: some View {
        HStack(spacing: Space.xl) {
            ForEach(Array(tabs.enumerated()), id: \.offset) { i, t in
                let active = i == index
                Button { withAnimation(Motion.quick) { index = i } } label: {
                    VStack(spacing: 7) {
                        Text(t)
                            .font(active ? AppFont.headline : AppFont.bodyText)
                            .foregroundStyle(active ? Palette.textPrimary : Palette.textSecondary)
                        Rectangle().fill(active ? Palette.textPrimary : Color.clear).frame(height: 2)
                    }
                    .fixedSize()
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("tab.\(t)")
            }
            Spacer()
        }
    }
}

// (ScoreBadge removed — predicted "virality scores" had no data basis and read as
//  filler. predictedScore/strength model fields stay for the learning loop; nothing
//  in the UI surfaces a fabricated number until it's backed by real measured lift.)

struct StreakGlyph: View {
    let count: Int
    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "flame.fill").font(.system(size: 12)).foregroundStyle(Palette.textPrimary)
            Text("\(count)").font(AppFont.caption.weight(.semibold)).foregroundStyle(Palette.textPrimary)
        }
        .accessibilityLabel("\(count) day streak")
    }
}

struct Chip: View {
    let text: String
    var selected: Bool = false
    var onDark: Bool = false          // over camera/media: translucent white instead of paper
    var tint: Color? = nil            // tiny tinted variant (e.g. provenance pill)
    var body: some View {
        if tint != nil {
            Text(text)
                .font(Typeface.sans(10, .semibold))
                .foregroundStyle(Palette.textSecondary)
                .padding(.horizontal, 7).padding(.vertical, 2)
                .background(Capsule().fill(Palette.surfaceSunken))
        } else {
            Text(text)
                .font(AppFont.supporting)
                .foregroundStyle(fg)
                .padding(.horizontal, 14).padding(.vertical, 9)
                .background(bg)
                .clipShape(Capsule())
                .overlay(Capsule().strokeBorder(stroke, lineWidth: 1))
                .accessibilityAddTraits(selected ? .isSelected : [])
        }
    }
    private var fg: Color {
        if selected { return Palette.onInk }
        return onDark ? Color.white.opacity(0.85) : Palette.textPrimary
    }
    private var bg: Color {
        if selected { return Palette.ink }
        return onDark ? Color.white.opacity(0.12) : Palette.surfaceRaised
    }
    private var stroke: Color {
        if selected { return .clear }
        return onDark ? Color.white.opacity(0.14) : Palette.hairline
    }
}

struct PillarNode: View {
    let pillar: Pillar
    var body: some View {
        VStack(spacing: Space.sm) {
            ZStack {
                Circle().fill(Palette.surfaceSunken)
                Circle().strokeBorder(Palette.hairline, lineWidth: 1)
                Text(String(pillar.name.prefix(1)))
                    .font(Typeface.sans(22, .semibold))
                    .foregroundStyle(Palette.textPrimary)
            }
            .frame(width: 64, height: 64)
            Text(pillar.name)
                .font(AppFont.caption)
                .foregroundStyle(Palette.textSecondary)
                .lineLimit(1)
        }
    }
}

// Editorial empty state: a hairline-weight glyph, sans display title, quiet
// capped-width message. (The old 72pt 3D-render graphics read as clip-art —
// the `graphic` param is kept for call-site compatibility but intentionally
// ignored; restraint over ornament.)
struct EmptyStateView: View {
    let icon: String
    let title: String
    let message: String
    var graphic: String? = nil
    var body: some View {
        VStack(spacing: Space.sm) {
            Image(systemName: icon)
                .font(.system(size: 22, weight: .regular))
                .foregroundStyle(Palette.textSecondary)
                .padding(.bottom, Space.xs)
            Text(title)
                .font(AppFont.headline)
                .foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
            Text(message)
                .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
                .lineSpacing(3)
                .frame(maxWidth: 300)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Space.huge)
    }
}

// Format classification mark (MYTH-BUSTER, POV/STORY, …) — bare tracked caps,
// no background, no border, no icon. The classification reads as a small label;
// the card's title (set lowercase) carries the visual weight.
struct FormatTag: View {
    let formatId: String
    var body: some View {
        Text(Catalog.format(formatId).name.uppercased())
            .font(AppFont.eyebrow).tracking(Track.eyebrow)
            .foregroundStyle(Palette.textSecondary)
    }
}

// Animated progress ring (maxapp home). value 0…1.
struct ProgressRing: View {
    let value: Double
    let centerTop: String
    let centerBottom: String
    var size: CGFloat = 116
    @State private var animated = false
    var body: some View {
        ZStack {
            Circle().stroke(Palette.hairline, lineWidth: 6)
            Circle().trim(from: 0, to: animated ? max(0.001, value) : 0)
                .stroke(Palette.ink, style: StrokeStyle(lineWidth: 6, lineCap: .round))
                .rotationEffect(.degrees(-90))
            VStack(spacing: 1) {
                Text(centerTop).font(Typeface.sans(26, .semibold)).foregroundStyle(Palette.textPrimary)
                Text(centerBottom.uppercased()).font(AppFont.micro).tracking(1).foregroundStyle(Palette.textSecondary)
            }
        }
        .frame(width: size, height: size)
        .onAppear { withAnimation(.easeOut(duration: 0.9)) { animated = true } }
    }
}

// Compact 1.2k / 3.4M number formatting for stat heroes.
func compactNumber(_ n: Int) -> String {
    if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
    if n >= 1_000 { return String(format: "%.1fk", Double(n) / 1_000) }
    return "\(n)"
}

// Lightweight area sparkline for the Today momentum card.
struct Sparkline: View {
    let values: [Double]
    var color: Color = Palette.textPrimary
    @State private var on = false
    var body: some View {
        GeometryReader { geo in
            let pts = points(in: geo.size)
            ZStack {
                if pts.count > 1 {
                    Path { p in
                        p.move(to: CGPoint(x: pts[0].x, y: geo.size.height))
                        pts.forEach { p.addLine(to: $0) }
                        p.addLine(to: CGPoint(x: pts[pts.count - 1].x, y: geo.size.height))
                        p.closeSubpath()
                    }
                    .fill(LinearGradient(colors: [color.opacity(0.18), color.opacity(0.0)],
                                         startPoint: .top, endPoint: .bottom))
                    Path { p in
                        p.move(to: pts[0]); pts.dropFirst().forEach { p.addLine(to: $0) }
                    }
                    .trim(from: 0, to: on ? 1 : 0)
                    .stroke(color, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                    if on, let last = pts.last {
                        Circle().fill(color).frame(width: 5, height: 5).position(last)
                    }
                }
            }
        }
        .onAppear { withAnimation(.easeOut(duration: 0.8)) { on = true } }
    }
    private func points(in size: CGSize) -> [CGPoint] {
        guard values.count > 1 else { return [] }
        let maxV = values.max() ?? 1
        let minV = values.min() ?? 0
        let range = max(maxV - minV, 0.0001)
        let stepX = size.width / CGFloat(values.count - 1)
        return values.enumerated().map { i, v in
            CGPoint(x: CGFloat(i) * stepX,
                    y: size.height - CGFloat((v - minV) / range) * size.height)
        }
    }
}

// Boxed text-field style (maxapp 54pt hairline field).
extension View {
    func marqueField() -> some View {
        self
            .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
            .padding(.horizontal, Space.rowPad).frame(height: 52)
            .background(Palette.surface)
            .clipShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
    }
}

// MARK: - Onboarding chrome (docs/ONBOARDING-DESIGN.md §3)

/// Step progress as short dashes (DESIGN.md §5 flow header). Keeps its accessibility id.
struct SegmentedProgress: View {
    let total: Int
    let index: Int
    var body: some View {
        DSProgressDashes(total: total, current: index)
            .accessibilityIdentifier("onboard.progress")
    }
}

/// Bare back chevron (44pt hit area) pinned top-left of every onboarding step.
struct BackCircle: View {
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: "chevron.left")
                .font(.system(size: 20, weight: .regular))
                .foregroundStyle(Palette.textPrimary)
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(PressableStyle(dim: 0.6))
        .accessibilityLabel("Back")
        .accessibilityIdentifier("onboard.back")
    }
}
