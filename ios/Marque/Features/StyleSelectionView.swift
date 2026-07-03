import SwiftUI

// A schematic 9:16 preview of what each video style LOOKS like — so creators can see the format
// before choosing it (the style determines the script, so it's chosen up front).
struct StylePreview: View {
    let style: VideoStyle
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Palette.surfaceSunken)
            content.padding(6)
        }
        .aspectRatio(9.0 / 16.0, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Palette.hairline, lineWidth: 1))
    }

    @ViewBuilder private var content: some View {
        switch style {
        case .talkingHead:
            VStack {
                Circle().fill(Palette.ink.opacity(0.28)).frame(width: 22, height: 22).padding(.top, 8)
                Spacer()
                captionBar
            }
        case .splitThree:
            VStack(spacing: 3) {
                ForEach(0..<3, id: \.self) { i in
                    RoundedRectangle(cornerRadius: 3)
                        .fill(Palette.ink.opacity([0.16, 0.24, 0.34][i]))
                        .overlay(Text("\(i + 1)").font(.system(size: 9, weight: .bold)).foregroundStyle(.white.opacity(0.9)))
                }
            }
        case .faceless:
            VStack(spacing: 5) {
                RoundedRectangle(cornerRadius: 4).fill(Palette.ink.opacity(0.2))
                waveform
                captionBar
            }
        case .fastCuts:
            let cols = [GridItem(.flexible(), spacing: 3), GridItem(.flexible(), spacing: 3)]
            LazyVGrid(columns: cols, spacing: 3) {
                ForEach(0..<6, id: \.self) { _ in
                    RoundedRectangle(cornerRadius: 2).fill(Palette.ink.opacity(0.2)).aspectRatio(1, contentMode: .fit)
                }
            }
        case .greenScreen:
            ZStack(alignment: .bottomLeading) {
                RoundedRectangle(cornerRadius: 4).fill(Palette.accent.opacity(0.18))
                Circle().fill(Palette.ink.opacity(0.32)).frame(width: 18, height: 18).padding(4)
            }
        }
    }

    private var captionBar: some View {
        RoundedRectangle(cornerRadius: 2).fill(Palette.ink.opacity(0.3)).frame(height: 5).padding(.horizontal, 6).padding(.bottom, 4)
    }
    private var waveform: some View {
        HStack(spacing: 2) {
            ForEach(0..<9, id: \.self) { i in
                Capsule().fill(Palette.accent.opacity(0.55)).frame(width: 2, height: [6, 11, 4, 13, 7, 12, 5, 9, 6][i])
            }
        }
    }
}

// Multi-select for the creator's preferred styles (onboarding + Settings), with visual previews.
struct StyleSelectionView: View {
    @Binding var selected: [VideoStyle]
    private let cols = [GridItem(.flexible(), spacing: Space.sm), GridItem(.flexible(), spacing: Space.sm)]
    var body: some View {
        LazyVGrid(columns: cols, spacing: Space.sm) {
            ForEach(VideoStyle.allCases) { style in
                Button { toggle(style) } label: { StyleTile(style: style, on: selected.contains(style)) }
                    .buttonStyle(PressableStyle())
                    .accessibilityIdentifier("style.\(style.rawValue)")
            }
        }
    }
    private func toggle(_ s: VideoStyle) {
        if let i = selected.firstIndex(of: s) { selected.remove(at: i) } else { selected.append(s) }
    }
}

private struct StyleTile: View {
    let style: VideoStyle
    let on: Bool
    var body: some View {
        VStack(spacing: 6) {
            ZStack(alignment: .topTrailing) {
                StylePreview(style: style).frame(height: 92)
                if on {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(Palette.positive).background(Circle().fill(.white)).padding(4)
                }
            }
            Text(style.label).font(AppFont.caption)
                .foregroundStyle(on ? Palette.textPrimary : Palette.textSecondary).lineLimit(1)
        }
        .padding(Space.sm)
        .frame(maxWidth: .infinity)
        .background(on ? Palette.positive.opacity(0.10) : Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(on ? Palette.positive : Palette.hairline, lineWidth: on ? 2 : 1))
    }
}
