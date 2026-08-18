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

    // Chat folded into Home (beta feedback 2026-08-18), so the left side carries one
    // tab against the right's two. The center Film button stays optically centered
    // because both sides are `.frame(maxWidth: .infinity)` distributed.
    private let leftItems: [(tab: AppTab, label: String, icon: String)] = [
        (.home, "Home", "sun.max"),
    ]

    private let rightItems: [(tab: AppTab, label: String, icon: String)] = [
        (.library, "Library", "rectangle.stack"),
        (.performance, "Performance", "chart.bar"),
    ]

    /// Tour anchor id for each tab, keyed by AppTab — matches TourManager.Step.id.
    /// Home has no anchor here: its tour step points at the chat composer inside
    /// HomeView's own content, not at this tab-bar icon.
    private func tourAnchorId(for tab: AppTab) -> String? {
        switch tab {
        case .home: return nil
        case .library: return "tour.library"
        case .performance: return "tour.performance"
        }
    }

    var body: some View {
        HStack(spacing: 0) {
            ForEach(leftItems, id: \.tab) { item in
                tabButton(item).frame(maxWidth: .infinity)
            }

            // Center Film button — solid blue circle with + icon (the one primary
            // create action, so it pops against the neutral glass bar).
            Button {
                createTaps += 1
                onCreateTap()
            } label: {
                Image(systemName: "plus")
                    .font(.system(size: 20, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: filmSize, height: filmSize)
                    .background(
                        Circle().fill(
                            LinearGradient(colors: [Palette.accent,
                                                    Palette.accent.opacity(0.82)],
                                           startPoint: .top, endPoint: .bottom)))
                    .overlay(Circle().strokeBorder(Color.white.opacity(0.28), lineWidth: 1))
                    .shadow(color: Palette.accent.opacity(0.42), radius: 10, y: 4)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Film")
            .accessibilityIdentifier("film.open")
            .sensoryFeedback(.impact(weight: .medium), trigger: createTaps)
            .padding(.horizontal, 10)
            .tourAnchor("tour.film")

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
                // OWNER (2026-08-15): tab icons are blue — the selected one at full
                // accent, the rest at 40% so the bar still reads as one blue family
                // while the current tab stays obvious. Labels keep the neutral ink
                // ramp: five blue words would fight the icons for attention.
                Image(systemName: item.icon).font(.system(size: 20, weight: .regular))
                    .foregroundStyle(selected == item.tab ? Palette.accent
                                                          : Palette.accent.opacity(0.40))
                Text(item.label)
                    // "Inter-Medium" isn't a bundled face (the app ships Matter +
                    // Fraunces), so this silently fell back to system at a size
                    // that wrapped "Performance" onto two lines in the bar.
                    .font(Typeface.sans(10, .medium))
                    .lineLimit(1).minimumScaleFactor(0.8)
                    .foregroundStyle(selected == item.tab ? Palette.textPrimary : Palette.textTertiary)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 2)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .modifier(OptionalTourAnchor(id: tourAnchorId(for: item.tab)))
    }
}
