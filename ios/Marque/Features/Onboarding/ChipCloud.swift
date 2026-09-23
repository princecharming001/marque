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
        LazyVStack(spacing: Space.sm + Space.xs) {
            if !rows.isEmpty {
                // ONE flat ForEach over every (cycle, row) slot. LazyVStack flattens
                // nested ForEach children, so a per-cycle loop keyed by row offset
                // gave every cycle the same IDs 0…n ("ID used by multiple child
                // views"). `slot` is unique across all cycles.
                ForEach(0..<(Self.cycles * rows.count), id: \.self) { slot in
                    rowView(rows[slot % rows.count], index: slot % rows.count,
                            cycle: slot / rows.count)
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
    /// Re-tuned for the monochrome chip (15pt text, 16pt side padding, no
    /// category dot): ~7.9pt per character plus padding and the star slot.
    static func estimatedWidth(_ item: Item) -> CGFloat {
        CGFloat(item.title.count) * 7.9 + 46
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

/// A content-hugging capsule chip for the cloud (DESIGN.md §5 chips, sized up to a
/// 44pt tap target). Unselected = surface + hairline; selected = inverted ink.
/// `primary` marks the FIRST pick — the one the prompts treat as the creator's
/// main niche/audience — with a star glyph. `dot` is accepted for call-site
/// compatibility but no longer drawn: the black-and-white system carries no
/// category hue on chips.
struct CloudChip: View {
    let title: String
    let dot: Color?
    let selected: Bool
    var primary: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if primary {
                    Image(systemName: "star.fill")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Palette.onInk)
                        .accessibilityLabel("First pick")
                }
                Text(title)
                    .font(AppFont.supporting.weight(selected ? .semibold : .regular))
                    .lineLimit(1).fixedSize()
            }
            .foregroundStyle(selected ? Palette.onInk : Palette.textPrimary)
            .padding(.horizontal, Space.md)
            .frame(height: 44)
            .background(Capsule().fill(selected ? Palette.ink : Palette.surface))
            .overlay(Capsule().strokeBorder(selected ? .clear : Palette.hairline, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(PressableStyle(dim: 0.8))
        .animation(Motion.quick, value: selected)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
