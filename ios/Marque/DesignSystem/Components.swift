import SwiftUI

// MARK: - Shared components (maxapp recipes: ink-fill buttons, hairline surfaces, pill chips)

struct PressableStyle: ButtonStyle {
    var dim: Double = 0.9
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.opacity(configuration.isPressed ? dim : 1)
    }
}

// Slow diagonal highlight sweep on premium CTAs (maxapp signature).
struct ShineSweep: View {
    @State private var x: CGFloat = -1.2
    var body: some View {
        GeometryReader { geo in
            LinearGradient(colors: [.clear, .white.opacity(0.28), .clear],
                           startPoint: .leading, endPoint: .trailing)
                .frame(width: geo.size.width * 0.45)
                .offset(x: x * geo.size.width)
                .onAppear {
                    withAnimation(.easeInOut(duration: 2.4).repeatForever(autoreverses: false).delay(0.6)) {
                        x = 1.7
                    }
                }
        }
        .allowsHitTesting(false)
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
            .font(AppFont.micro).tracking(0.8)
            .foregroundStyle(Palette.textTertiary)
    }
}

struct ScoreBadge: View {
    let score: Int
    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text("\(score)").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
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
    var body: some View {
        Text(text)
            .font(AppFont.callout)
            .foregroundStyle(selected ? Palette.onInk : Palette.textSecondary)
            .padding(.horizontal, 14).padding(.vertical, 9)
            .background(selected ? Palette.ink : Palette.surfaceRaised)
            .clipShape(Capsule())
            .overlay(Capsule().strokeBorder(selected ? Color.clear : Palette.hairline, lineWidth: 1))
            .shadow(color: selected ? .clear : .black.opacity(0.05), radius: 8, x: 0, y: 2)
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
