import SwiftUI

// MARK: - Chip cloud (build 83)
//
// The Gymshark-reference chip cloud, rebuilt for MULTI-select and VERTICAL
// scrolling. The old version was a stack of independently side-scrolling rows,
// which hid most of the options behind a gesture nobody made and could only
// hold one answer. This one packs the chips into staggered rows that bleed off
// both screen edges, scrolls with the page, and repeats forever so the cloud
// never bottoms out into dead space.
//
// Selection is keyed by TITLE (not by index or identity), so a chip toggled in
// one repeat cycle reads as selected in every other cycle too.

struct ChipCloud: View {
    struct Item: Hashable {
        let title: String
        /// Index into `catColors` for the leading dot; nil = no dot.
        let cat: Int?
    }

    let items: [Item]
    var catColors: [Color] = []
    /// Accessibility ids are `idPrefix + lowercased title` — and are attached to
    /// the FIRST repeat cycle only, so Maestro never sees the same id 40 times.
    let idPrefix: String
    let isSelected: (String) -> Bool
    let isPrimary: (String) -> Bool
    let toggle: (String) -> Void

    @State private var width: CGFloat = 0

    /// Row stagger: alternating alignment plus a small horizontal nudge, so chips
    /// never line up into columns and every row bleeds past a different edge.
    private static let stagger: [(alignment: Alignment, dx: CGFloat)] = [
        (.leading, -10), (.center, 14), (.leading, 6), (.trailing, 10), (.center, -16),
    ]

    /// How many times the packed rows repeat. Enough that no one scrolls past the
    /// end; lazy, so only the visible cycles are ever built.
    private static let cycles = 40

    var body: some View {
        let rows = Self.pack(items, availableWidth: width)
        LazyVStack(spacing: Space.md) {
            if !rows.isEmpty {
                ForEach(0..<Self.cycles, id: \.self) { cycle in
                    ForEach(Array(rows.enumerated()), id: \.offset) { i, row in
                        rowView(row, index: i, cycle: cycle)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity)
        .background(
            GeometryReader { geo in
                Color.clear.preference(key: CloudWidthKey.self, value: geo.size.width)
            }
        )
        .onPreferenceChange(CloudWidthKey.self) { w in
            if w > 0, abs(w - width) > 0.5 { width = w }
        }
    }

    private func rowView(_ row: [Item], index: Int, cycle: Int) -> some View {
        let s = Self.stagger[index % Self.stagger.count]
        return HStack(spacing: Space.sm) {
            ForEach(row, id: \.self) { item in
                CloudChip(title: item.title,
                          dot: dotColor(item),
                          selected: isSelected(item.title),
                          primary: isPrimary(item.title)) {
                    toggle(item.title)
                }
                .cloudAccessibilityID(cycle == 0 ? idPrefix + item.title.lowercased() : nil)
            }
        }
        .frame(maxWidth: .infinity, alignment: s.alignment)
        .offset(x: s.dx)
    }

    private func dotColor(_ item: Item) -> Color? {
        guard let c = item.cat, catColors.indices.contains(c) else { return nil }
        return catColors[c]
    }

    // MARK: Greedy row packing

    /// Estimated rendered width of a chip. Cheap arithmetic beats measuring 56
    /// text views: the cloud is deliberately ragged, so a few points of drift is
    /// invisible — and the 6% overflow allowance below turns it into the bleed.
    static func estimatedWidth(_ item: Item) -> CGFloat {
        CGFloat(item.title.count) * 8.4 + 44 + (item.cat == nil ? 0 : 15)
    }

    /// Pack the items into rows, greedily, closing a row once the next chip would
    /// push it past 106% of the available width. That deliberate 6% overflow is
    /// what makes the last chip of most rows run off the screen edge.
    static func pack(_ items: [Item], availableWidth: CGFloat) -> [[Item]] {
        guard availableWidth > 0 else { return [] }
        let limit = availableWidth * 1.06
        var rows: [[Item]] = []
        var row: [Item] = []
        var used: CGFloat = 0
        for item in items {
            let w = estimatedWidth(item)
            let next = row.isEmpty ? w : used + Space.sm + w
            if !row.isEmpty, next > limit {
                rows.append(row)
                row = [item]
                used = w
            } else {
                row.append(item)
                used = next
            }
        }
        if !row.isEmpty { rows.append(row) }
        return rows
    }
}

private struct CloudWidthKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

private extension View {
    /// Apply an accessibility id only when one is supplied (repeat cycles pass nil
    /// so the same id never appears twice on screen).
    @ViewBuilder func cloudAccessibilityID(_ id: String?) -> some View {
        if let id { self.accessibilityIdentifier(id) } else { self }
    }
}

/// A content-hugging capsule chip for the cloud. `primary` marks the FIRST pick —
/// the one the prompts treat as the creator's main niche/audience — with a star
/// where the category dot would sit.
struct CloudChip: View {
    let title: String
    let dot: Color?
    let selected: Bool
    var primary: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if primary {
                    Image(systemName: "star.fill")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(Palette.onInk.opacity(0.9))
                } else if let dot {
                    Circle().fill(dot).frame(width: 7, height: 7)
                }
                Text(title)
                    .font(Typeface.sans(16, selected ? .semibold : .regular))
                    .lineLimit(1).fixedSize()
            }
            .foregroundStyle(selected ? Palette.canvas : Palette.textPrimary)
            .padding(.horizontal, 18)
            .padding(.vertical, 15)
            .background(selected ? Palette.ink : Palette.surfaceRaised, in: Capsule())
            .overlay(Capsule().strokeBorder(selected ? .clear : Palette.hairline, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}
