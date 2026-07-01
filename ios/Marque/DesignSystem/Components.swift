import SwiftUI

// MARK: - Shared components (maxapp recipes: ink-fill buttons, hairline surfaces, pill chips)

struct PressableStyle: ButtonStyle {
    var dim: Double = 0.9
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? dim : 1)
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
            .foregroundStyle(Palette.textPrimary)
            .frame(maxWidth: .infinity).frame(height: 54)
            .background(LiquidGlassFill(radius: Radius.md, corners: false))
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(Color.white.opacity(0.5), lineWidth: 1))
        }
        .buttonStyle(PressableStyle(dim: 0.7))
    }
}

struct PrimaryButton: View {
    let title: String
    var systemImage: String? = nil
    var shine: Bool = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.sm) {
                if let s = systemImage { Image(systemName: s).font(.system(size: 16, weight: .semibold)) }
                Text(title).font(AppFont.headline)
            }
            .foregroundStyle(Palette.onInk)
            .frame(maxWidth: .infinity).frame(height: 54)
            .background(ZStack { Palette.ink; if shine { ShineSweep() } })
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .shadow(color: .black.opacity(0.12), radius: 10, x: 0, y: 4)
        }
        .buttonStyle(PressableStyle())
    }
}

struct GhostButton: View {
    let title: String
    var systemImage: String? = nil
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.sm) {
                if let s = systemImage { Image(systemName: s).font(.system(size: 15, weight: .medium)) }
                Text(title).font(AppFont.headline)
            }
            .foregroundStyle(Palette.textPrimary)
            .frame(maxWidth: .infinity).frame(height: 54)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
        }
        .buttonStyle(PressableStyle(dim: 0.7))
    }
}

struct SectionTitle: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(AppFont.micro).tracking(Track.label)
            .foregroundStyle(Palette.textTertiary)
    }
}

/// Big editorial screen title — lowercase Playfair, maxapp's signature move.
struct ScreenTitle: View {
    let text: String
    var size: CGFloat = 30
    var body: some View {
        Text(text)
            .font(Typeface.display(size, .semibold))
            .tracking(Track.title)
            .textCase(.lowercase)
            .foregroundStyle(Palette.textPrimary)
    }
}

/// UPPERCASE tracked micro-label with an optional 3×14 accent bar (maxapp section eyebrow).
struct SectionLabel: View {
    let text: String
    var accent: Color? = nil
    var body: some View {
        HStack(spacing: 8) {
            if let accent {
                RoundedRectangle(cornerRadius: 1.5).fill(accent).frame(width: 3, height: 14)
            }
            Text(text.uppercased())
                .font(AppFont.micro).tracking(Track.label)
                .foregroundStyle(Palette.textTertiary)
        }
    }
}

/// Warm hairline for zone breaks between sections (heavier than the shared card hairline).
struct MarqueHairline: View {
    var body: some View {
        Rectangle().fill(Palette.textPrimary.opacity(0.12)).frame(height: 0.5)
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
                            .font(active ? AppFont.headline : Typeface.sans(15, .medium))
                            .foregroundStyle(active ? Palette.textPrimary : Palette.textTertiary)
                        Rectangle().fill(active ? Palette.ink : Color.clear).frame(height: 2)
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

// A predicted (not measured) virality signal. The leading sparkle + "est." keep it
// visually distinct from real metrics, so a creator never mistakes it for a measured stat.
struct ScoreBadge: View {
    let score: Int
    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "sparkle").font(.system(size: 8, weight: .semibold)).foregroundStyle(color)
            Text("\(score)").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
            Text("est.").font(AppFont.micro).foregroundStyle(Palette.textTertiary)
        }
        .accessibilityLabel("Predicted score \(score) of 100")
    }
    private var color: Color {
        score >= 85 ? Palette.positive : score >= 70 ? Palette.accent : Palette.textTertiary
    }
}

struct StreakGlyph: View {
    let count: Int
    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "flame.fill").font(.system(size: 12)).foregroundStyle(Palette.textPrimary)
            Text("\(count)").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
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
        if let tint {
            Text(text)
                .font(Typeface.sans(10, .medium))
                .foregroundStyle(tint)
                .padding(.horizontal, 7).padding(.vertical, 2)
                .background(Capsule().fill(tint.opacity(0.14)))
        } else {
            Text(text)
                .font(AppFont.callout)
                .foregroundStyle(fg)
                .padding(.horizontal, 14).padding(.vertical, 9)
                .background(bg)
                .clipShape(Capsule())
                .overlay(Capsule().strokeBorder(stroke, lineWidth: 1))
                .shadow(color: (selected || onDark) ? .clear : .black.opacity(0.05), radius: 8, x: 0, y: 2)
                .accessibilityAddTraits(selected ? .isSelected : [])
        }
    }
    private var fg: Color {
        if selected { return Palette.onInk }
        return onDark ? Color.white.opacity(0.6) : Palette.textSecondary
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
                Circle().fill(Color(hex: pillar.colorHex).opacity(0.12))
                Circle().strokeBorder(Color(hex: pillar.colorHex), lineWidth: 1.5)
                Text(String(pillar.name.prefix(1)))
                    .font(Typeface.display(22, .semibold))
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

struct EmptyStateView: View {
    let icon: String
    let title: String
    let message: String
    var body: some View {
        VStack(spacing: Space.md) {
            Image(systemName: icon).font(.system(size: 28)).foregroundStyle(Palette.textTertiary)
            Text(title).font(AppFont.title).foregroundStyle(Palette.textPrimary)
            Text(message).font(AppFont.body).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Space.huge)
    }
}

struct FormatTag: View {
    let formatId: String
    var body: some View {
        let f = Catalog.format(formatId)
        HStack(spacing: 4) {
            Image(systemName: icon(f.faceMode)).font(.system(size: 11))
            Text(f.name).font(AppFont.caption)
        }
        .foregroundStyle(Palette.textSecondary)
        .padding(.horizontal, Space.sm).padding(.vertical, 5)
        .background(Palette.surfaceSunken)
        .clipShape(Capsule())
    }
    private func icon(_ m: VideoFormat.FaceMode) -> String {
        switch m {
        case .face: return "person.fill"
        case .faceless: return "sparkles"
        case .split: return "rectangle.split.2x1"
        case .greenScreen: return "photo.fill"
        }
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
                Text(centerBottom.uppercased()).font(AppFont.micro).tracking(1).foregroundStyle(Palette.textTertiary)
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
    var color: Color = Palette.accent
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
            .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
            .padding(.horizontal, 16).frame(height: 54)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
    }
}
