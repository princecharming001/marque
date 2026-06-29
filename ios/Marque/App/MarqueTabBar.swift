import SwiftUI

// Custom floating frosted tab bar — maxapp's signature nav (milky blur + top sheen +
// bright rim, ink active / muted inactive). Labels kept as text so Maestro taps by name.
struct MarqueTabBar: View {
    @Binding var selected: AppTab

    private let items: [(tab: AppTab, label: String, icon: String)] = [
        (.today, "Today", "sun.max"),
        (.studio, "Studio", "square.grid.2x2"),
        (.library, "Library", "rectangle.stack"),
        (.calendar, "Calendar", "calendar"),
        (.coach, "Coach", "bubble.left.and.text.bubble.right"),
    ]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(items, id: \.tab) { item in
                Button {
                    selected = item.tab
                } label: {
                    VStack(spacing: 3) {
                        Image(systemName: item.icon).font(.system(size: 20, weight: .regular))
                        Text(item.label).font(.custom("Inter-Medium", size: 10))
                    }
                    .foregroundStyle(selected == item.tab ? Palette.textPrimary : Palette.textTertiary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 2)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.top, 10)
        .padding(.bottom, 8)
        .padding(.horizontal, 6)
        .background(frost)
        .clipShape(RoundedRectangle(cornerRadius: 26, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 26, style: .continuous)
                .strokeBorder(Color.white.opacity(0.7), lineWidth: 1)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 26, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 0.5)
        )
        .shadow(color: .black.opacity(0.10), radius: 22, x: 0, y: 10)
        .padding(.horizontal, 16)
    }

    private var frost: some View {
        ZStack {
            Rectangle().fill(.ultraThinMaterial)
            Rectangle().fill(Color.white.opacity(0.5))                  // milky legibility fill
            RadialGradient(colors: [Color.white.opacity(0.75), .clear], // top-left specular
                           center: .topLeading, startRadius: 0, endRadius: 90)
            RadialGradient(colors: [Color.white.opacity(0.4), .clear],  // bottom-right hotspot
                           center: .bottomTrailing, startRadius: 0, endRadius: 70)
            LinearGradient(colors: [Color.white.opacity(0.55), .clear], // top sheen
                           startPoint: .top, endPoint: .bottom)
                .frame(maxHeight: .infinity, alignment: .top)
        }
    }
}
