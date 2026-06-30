import SwiftUI

// Multi-select for the creator's video styles. Each chosen style gets its own script structure
// (talking-head spoken-to-camera, faceless voiceover+b-roll, split-screen reaction). Used in
// onboarding and in Settings.
struct StyleSelectionView: View {
    @Binding var selected: [VideoStyle]
    var body: some View {
        VStack(spacing: Space.md) {
            ForEach(VideoStyle.allCases) { style in
                Button { toggle(style) } label: {
                    StyleCard(style: style, on: selected.contains(style))
                }
                .buttonStyle(PressableStyle())
                .accessibilityIdentifier("style.\(style.rawValue)")
            }
        }
    }
    private func toggle(_ s: VideoStyle) {
        if let i = selected.firstIndex(of: s) { selected.remove(at: i) } else { selected.append(s) }
    }
}

private struct StyleCard: View {
    let style: VideoStyle
    let on: Bool
    var body: some View {
        HStack(spacing: Space.md) {
            Image(systemName: style.icon).font(.system(size: 20))
                .foregroundStyle(on ? Palette.onInk : Palette.textPrimary).frame(width: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(style.label).font(AppFont.headline)
                    .foregroundStyle(on ? Palette.onInk : Palette.textPrimary)
                Text(style.blurb).font(AppFont.caption)
                    .foregroundStyle(on ? Palette.onInk.opacity(0.85) : Palette.textSecondary)
            }
            Spacer(minLength: 0)
            Image(systemName: on ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(on ? Palette.onInk : Palette.textTertiary)
        }
        .padding(Space.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(on ? Palette.ink : Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(on ? Color.clear : Palette.hairline, lineWidth: 1))
        .shadow(color: .black.opacity(on ? 0 : 0.05), radius: 10, x: 0, y: 4)
    }
}
