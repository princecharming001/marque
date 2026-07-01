import SwiftUI

// Performance overview (10-… insights). Mock stats derived from local clips/schedule until
// the Insights adapter pulls real metrics back. Reached from Coach.
struct InsightsView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    private var bestClip: Clip? { store.clips.max(by: { $0.predictedScore < $1.predictedScore }) }
    private var formatCounts: [(String, Int)] {
        Dictionary(grouping: store.clips, by: { $0.formatName })
            .map { ($0.key, $0.value.count) }
            .sorted { $0.1 > $1.1 }
    }
    private var logged: [ScheduledPost] { store.schedule.filter { ($0.metrics?.views ?? 0) > 0 } }
    private var totalViews: Int { logged.compactMap { $0.metrics?.views }.reduce(0, +) }
    private var totalLikes: Int { logged.compactMap { $0.metrics?.likes }.reduce(0, +) }
    private var avgEngagement: Double {
        let rates = logged.compactMap { $0.metrics?.engagementRate }
        return rates.isEmpty ? 0 : rates.reduce(0, +) / Double(rates.count)
    }
    private var heroValue: String {
        if totalViews > 0 { return compactNumber(totalViews) }
        return "\(store.clips.count)"
    }
    private var heroLabel: String {
        totalViews > 0 ? "total views" : "clips made"
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    // Hero numeral moment
                    heroCard

                    // How posts did (if logged)
                    if !logged.isEmpty {
                        logsSection
                    } else if !store.clips.isEmpty {
                        HStack(spacing: Space.sm) {
                            Image(systemName: "info.circle").foregroundStyle(Palette.textTertiary)
                            Text("No results logged yet. Tap \u{201C}Log results\u{201D} on a post to see real views and engagement here.")
                                .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .marqueCard(padding: Space.md)
                    }

                    // Best clip
                    if let best = bestClip {
                        VStack(alignment: .leading, spacing: Space.sm) {
                            SectionLabel(text: "Highest predicted clip", accent: Palette.accent)
                            VStack(alignment: .leading, spacing: Space.sm) {
                                Text(best.caption)
                                    .font(AppFont.title).foregroundStyle(Palette.textPrimary).lineLimit(2)
                                HStack {
                                    FormatTag(formatId: best.formatId)
                                    Spacer()
                                    ScoreBadge(score: best.predictedScore)
                                }
                            }
                            .marqueCard()
                        }
                    }

                    // Format mix — visual bar rows
                    if !formatCounts.isEmpty {
                        VStack(alignment: .leading, spacing: Space.md) {
                            SectionLabel(text: "Your format mix", accent: Palette.accent)
                            VStack(spacing: 0) {
                                ForEach(Array(formatCounts.enumerated()), id: \.element.0) { i, item in
                                    if i > 0 { MarqueHairline() }
                                    FormatBarRow(name: item.0, count: item.1, maxCount: formatCounts.first?.1 ?? 1)
                                }
                            }
                            .marqueCard(padding: 0)
                        }
                    }

                    if store.clips.isEmpty {
                        EmptyStateView(icon: "chart.bar", title: "No data yet",
                                       message: "Make and schedule clips to see how you're doing.")
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Insights")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }

    // MARK: Hero numeral

    private var heroCard: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack(alignment: .lastTextBaseline, spacing: Space.sm) {
                Text(heroValue)
                    .font(Typeface.display(56, .semibold)).tracking(Track.hero)
                    .foregroundStyle(Palette.textPrimary)
                Text(heroLabel)
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    .padding(.bottom, 8)
            }
            HStack(spacing: Space.md) {
                miniStat("\(store.schedule.count)", "Scheduled")
                miniStat("\(store.streak)", "Sessions")
                if totalLikes > 0 { miniStat(compactNumber(totalLikes), "Likes") }
            }
        }
        .marqueCard(radius: 22)
    }

    private func miniStat(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
            Text(label).font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Logged posts section

    private var logsSection: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            SectionLabel(text: "How your posts did", accent: Palette.accent)
            HStack(spacing: Space.md) {
                stat(compactNumber(totalViews), "Views")
                stat(compactNumber(totalLikes), "Likes")
                stat("\(logged.count)", "Logged")
            }
            if avgEngagement > 0 {
                HStack {
                    Text("Avg engagement").font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                    Spacer()
                    Text(String(format: "%.1f%%", avgEngagement * 100))
                        .font(AppFont.body).foregroundStyle(Palette.textPrimary)
                }
                .marqueCard(padding: Space.md)
            }
        }
    }

    private func stat(_ value: String, _ label: String) -> some View {
        VStack(spacing: 4) {
            Text(value).font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
            Text(label).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .marqueCard(padding: Space.md)
    }
}

// MARK: - Format bar row

struct FormatBarRow: View {
    let name: String
    let count: Int
    let maxCount: Int

    private var pct: Double { maxCount > 0 ? Double(count) / Double(maxCount) : 0 }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            HStack {
                Text(name).font(AppFont.body).foregroundStyle(Palette.textPrimary)
                Spacer()
                Text("\(count) clip\(count == 1 ? "" : "s")")
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Palette.hairline).frame(height: 5)
                    Capsule().fill(Palette.accent)
                        .frame(width: geo.size.width * pct, height: 5)
                        .animation(.easeOut(duration: 0.6), value: pct)
                }
            }
            .frame(height: 5)
        }
        .padding(.horizontal, Space.lg)
        .padding(.vertical, Space.md)
        .contentShape(Rectangle())
    }
}
