import SwiftUI

// Tab bar (DESIGN.md §5): flat canvas bar, no border or blur. Outline glyph + caption label
// per tab; the selected tab fills its glyph and bolds its label. The center Film action is a
// raised ink circle with no label. Labels stay real text so Maestro taps by name.
//
// Geometry contract: rendered as a plain bottom OVERLAY (see RootTabView), never a
// safeAreaInset; screens own their clearance via `MarqueTabBar.clearance`.
struct MarqueTabBar: View {
    @Binding var selected: AppTab
    var onCreateTap: () -> Void
    @State private var createTaps = 0

    /// Vertical space a screen must keep clear at the bottom (bar + raised center + gap).
    static let clearance: CGFloat = 84

    private let filmSize: CGFloat = 52
    private let barHeight: CGFloat = 56

    private let leftItems: [(tab: AppTab, label: String, icon: String)] = [
        (.home, "Home", "sun.max"),
        (.chat, "Chat", "bubble.left.and.text.bubble.right"),
    ]

    private let rightItems: [(tab: AppTab, label: String, icon: String)] = [
        (.library, "Library", "rectangle.stack"),
        (.performance, "Performance", "chart.bar"),
    ]

    /// Tour anchor id for each tab — matches TourManager.Step.id. Home has none: its tour
    /// step points at the voice bubble inside HomeView.
    private func tourAnchorId(for tab: AppTab) -> String? {
        switch tab {
        case .home: return nil
        case .chat: return "tour.chat"
        case .library: return "tour.library"
        case .performance: return "tour.performance"
        }
    }

    var body: some View {
        HStack(alignment: .center, spacing: 0) {
            ForEach(leftItems, id: \.tab) { item in
                tabButton(item).frame(maxWidth: .infinity)
            }

            Button {
                createTaps += 1
                onCreateTap()
            } label: {
                Image(systemName: "plus")
                    .font(.system(size: 22, weight: .regular))
                    .foregroundStyle(Palette.onInk)
                    .frame(width: filmSize, height: filmSize)
                    .background(Circle().fill(Palette.ink))
                    .contentShape(Circle())
            }
            .buttonStyle(PressableStyle(dim: 0.9, scale: 0.92))
            .accessibilityLabel("Film")
            .accessibilityIdentifier("film.open")
            .sensoryFeedback(.impact(weight: .light), trigger: createTaps)
            .offset(y: -8)
            .frame(maxWidth: .infinity)
            .tourAnchor("tour.film")

            ForEach(rightItems, id: \.tab) { item in
                tabButton(item).frame(maxWidth: .infinity)
            }
        }
        .frame(height: barHeight)
        .padding(.horizontal, Space.sm)
        .background(Palette.canvas.ignoresSafeArea(edges: .bottom))
    }

    @ViewBuilder
    private func tabButton(_ item: (tab: AppTab, label: String, icon: String)) -> some View {
        let isSelected = selected == item.tab
        Button {
            selected = item.tab
        } label: {
            VStack(spacing: 4) {
                Image(systemName: isSelected ? item.icon + ".fill" : item.icon)
                    .font(.system(size: 21, weight: .regular))
                    .frame(height: 24)
                Text(item.label)
                    .font(Typeface.sans(12, isSelected ? .bold : .regular))
                    .lineLimit(1).minimumScaleFactor(0.8)
            }
            .foregroundStyle(isSelected ? Palette.textPrimary : Palette.textSecondary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 4)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
        .modifier(OptionalTourAnchor(id: tourAnchorId(for: item.tab)))
    }
}
