import SwiftUI

// Custom floating frosted tab bar — 4 verb-tabs + raised center Create FAB.
// Labels kept as text so Maestro taps by name.
struct MarqueTabBar: View {
    @Binding var selected: AppTab
    var onCreateTap: () -> Void

    private let leftItems: [(tab: AppTab, label: String, icon: String)] = [
        (.home, "Today", "sun.max"),
        (.plan, "Calendar", "calendar"),
    ]

    private let rightItems: [(tab: AppTab, label: String, icon: String)] = [
        (.library, "Library", "rectangle.stack"),
        (.coach, "Coach", "sparkles"),
    ]

    var body: some View {
        ZStack(alignment: .top) {
            // Tab bar row
            HStack(spacing: 0) {
                ForEach(leftItems, id: \.tab) { item in
                    tabButton(item)
                }
                // Center space for the FAB
                Spacer().frame(maxWidth: .infinity)
                ForEach(rightItems, id: \.tab) { item in
                    tabButton(item)
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

            // Center Create FAB — raised above the tab bar
            Button {
                onCreateTap()
            } label: {
                Image(systemName: "video.badge.plus")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 56, height: 56)
                    .background(Palette.accent)
                    .clipShape(Circle())
                    .shadow(color: Palette.accent.opacity(0.45), radius: 12, x: 0, y: 6)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Studio")
            .offset(y: -24)
        }
        .padding(.horizontal, 16)
    }

    @ViewBuilder
    private func tabButton(_ item: (tab: AppTab, label: String, icon: String)) -> some View {
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

    private var frost: some View {
        ZStack {
            Rectangle().fill(.ultraThinMaterial)
            Rectangle().fill(Color.white.opacity(0.5))
            RadialGradient(colors: [Color.white.opacity(0.75), .clear],
                           center: .topLeading, startRadius: 0, endRadius: 90)
            RadialGradient(colors: [Color.white.opacity(0.4), .clear],
                           center: .bottomTrailing, startRadius: 0, endRadius: 70)
            LinearGradient(colors: [Color.white.opacity(0.55), .clear],
                           startPoint: .top, endPoint: .bottom)
                .frame(maxHeight: .infinity, alignment: .top)
        }
    }
}
