import SwiftUI

// MARK: - Shared components (02-design-system.md)
// Accessibility rule: gold is fill-or-glyph only; on a gold fill the label is ink, never gold-on-cream text.

struct PrimaryButton: View {
    let title: String
    var systemImage: String? = nil
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.sm) {
                if let s = systemImage { Image(systemName: s) }
                Text(title).font(AppFont.headline)
            }
            .foregroundStyle(Palette.night)
            .frame(maxWidth: .infinity)
            .padding(.vertical, Space.lg)
            .background(Palette.gold)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

struct GhostButton: View {
    let title: String
    var systemImage: String? = nil
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: Space.sm) {
                if let s = systemImage { Image(systemName: s) }
                Text(title).font(AppFont.callout)
            }
            .foregroundStyle(Palette.textPrimary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, Space.md)
            .overlay(
                RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }
}

struct SectionTitle: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(AppFont.micro)
            .tracking(1.4)
            .foregroundStyle(Palette.textTertiary)
    }
}

struct ScoreBadge: View {
    let score: Int
    var body: some View {
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text("\(score)").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
        }
        .accessibilityLabel("Predicted score \(score) of 100")
    }
    private var color: Color {
        score >= 85 ? Palette.positive : score >= 70 ? Palette.warning : Palette.textTertiary
    }
}

struct StreakGlyph: View {
    let count: Int
    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "flame.fill").font(.system(size: 13)).foregroundStyle(Palette.gold)
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
            .foregroundStyle(selected ? Palette.night : Palette.textPrimary)
            .padding(.horizontal, Space.md)
            .padding(.vertical, Space.sm)
            .background(selected ? Palette.gold : Palette.surfaceRaised)
            .clipShape(Capsule())
            .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: selected ? 0 : 1))
    }
}

struct PillarNode: View {
    let pillar: Pillar
    var body: some View {
        VStack(spacing: Space.sm) {
            ZStack {
                Circle()
                    .fill(Color(hex: pillar.colorHex).opacity(0.18))
                Circle()
                    .strokeBorder(Color(hex: pillar.colorHex), lineWidth: 2)
                Text(String(pillar.name.prefix(1)))
                    .font(Typeface.display(22, .semibold))
                    .foregroundStyle(Palette.textPrimary)
            }
            .frame(width: 66, height: 66)
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
            Image(systemName: icon).font(.system(size: 30)).foregroundStyle(Palette.textTertiary)
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
        .padding(.horizontal, Space.sm)
        .padding(.vertical, 5)
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
