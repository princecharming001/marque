import SwiftUI

// Custom floating tab bar — 4 verb-tabs + a raised center Create FAB.
// Labels kept as text so Maestro taps by name.
// Center gap is a FIXED-width slot (not a flexible spacer) so the FAB always lands dead-center
// regardless of how the flanking labels ("Home"/"Chat" vs "Library"/"Performance") measure —
// two flexible items on each side of a fixed gap keeps it symmetric at any bar width.
struct MarqueTabBar: View {
    @Binding var selected: AppTab
    var onCreateTap: () -> Void
    @State private var createTaps = 0

    private let leftItems: [(tab: AppTab, label: String, icon: String)] = [
        (.home, "Home", "sun.max"),
        (.chat, "Chat", "bubble.left.and.text.bubble.right"),
    ]

    private let rightItems: [(tab: AppTab, label: String, icon: String)] = [
        (.library, "Library", "rectangle.stack"),
        (.performance, "Performance", "chart.bar"),
    ]

    // NOTE: safeAreaInset's reported size does NOT reserve room for content rendered outside its
    // own layout bounds via .offset() — padding tricks on the inset view are provably a no-op
    // (verified via pixel-diff) for non-scrolling short content, since ScrollView positions
    // top-anchored content purely by cumulative height from the top, independent of bottom inset
    // size. So the FAB's raw float distance above the bar is what has to stay small enough to
    // clear whatever the last card renders (e.g. TodayView's "Record your batch" CTA).
    private let fabSize: CGFloat = 56
    private let fabOffset: CGFloat = -8
    private let fabGap: CGFloat = 76   // fixed center slot, > fabSize so the bar peeks out at the edges

    var body: some View {
        ZStack(alignment: .top) {
            // Tab bar row — fixed center slot, not a flexible spacer, so the gap is always
            // exactly centered regardless of label width on either side.
            HStack(spacing: 0) {
                ForEach(leftItems, id: \.tab) { item in
                    tabButton(item).frame(maxWidth: .infinity)
                }
                Spacer().frame(width: fabGap)
                ForEach(rightItems, id: \.tab) { item in
                    tabButton(item).frame(maxWidth: .infinity)
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

            // Center Film FAB — floats clearly above the bar, just kissing its top edge,
            // liquid-glass tinted so it reads as one family with the frosted bar beneath it.
            Button {
                createTaps += 1
                onCreateTap()
            } label: {
                Image(systemName: "video.badge.plus")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(.white)
                    .marqueGlassCircle(diameter: fabSize, tint: Palette.accent)
                    .overlay(Circle().strokeBorder(Color.white.opacity(0.35), lineWidth: 1))
                    .shadow(color: Palette.accent.opacity(0.42), radius: 16, x: 0, y: 8)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Film")
            .accessibilityIdentifier("film.open")
            .sensoryFeedback(.impact(weight: .medium), trigger: createTaps)
            .offset(y: fabOffset)
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
