import SwiftUI

// Custom floating tab bar — 4 verb-tabs with an INLINE center Film button (no raised FAB).
// Clear Apple-style liquid glass: plain ultraThinMaterial capsule, hairline strokes, no
// white washes. Labels kept as text so Maestro taps by name.
//
// Geometry contract: the bar is rendered as a plain bottom OVERLAY (see RootTabView), never
// a safeAreaInset — inset reservation proved flaky with this bar historically (see git
// history), so screens own their clearance explicitly via `MarqueTabBar.clearance`.
struct MarqueTabBar: View {
    @Binding var selected: AppTab
    var onCreateTap: () -> Void
    @State private var createTaps = 0

    /// Total vertical space a screen must keep clear at the bottom (bar height + its
    /// bottom margin + a breathing gap). Non-scrolling screens pad fixed bottom content
    /// by this; scrolling screens keep generous bottom padding as before.
    static let clearance: CGFloat = 84

    private let filmSize: CGFloat = 48

    private let leftItems: [(tab: AppTab, label: String, icon: String)] = [
        (.home, "Home", "sun.max"),
        (.chat, "Chat", "bubble.left.and.text.bubble.right"),
    ]

    private let rightItems: [(tab: AppTab, label: String, icon: String)] = [
        (.library, "Library", "rectangle.stack"),
        (.performance, "Performance", "chart.bar"),
    ]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(leftItems, id: \.tab) { item in
                tabButton(item).frame(maxWidth: .infinity)
            }

            // Center Film button — liquid glass circle with + icon
            Button {
                createTaps += 1
                onCreateTap()
            } label: {
                Image(systemName: "plus")
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundStyle(.white)
                    .marqueGlassCircle(diameter: filmSize)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Film")
            .accessibilityIdentifier("film.open")
            .sensoryFeedback(.impact(weight: .medium), trigger: createTaps)
            .padding(.horizontal, 10)

            ForEach(rightItems, id: \.tab) { item in
                tabButton(item).frame(maxWidth: .infinity)
            }
        }
        .padding(.vertical, 8)
        .padding(.horizontal, 10)
        .background(.ultraThinMaterial, in: Capsule(style: .continuous))
        .overlay(Capsule(style: .continuous).strokeBorder(Color.white.opacity(0.35), lineWidth: 1))
        .overlay(Capsule(style: .continuous).strokeBorder(Palette.hairline, lineWidth: 0.5))
        .shadow(color: .black.opacity(0.08), radius: 18, x: 0, y: 8)
        .padding(.horizontal, 16)
        .padding(.bottom, 4)
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
}
