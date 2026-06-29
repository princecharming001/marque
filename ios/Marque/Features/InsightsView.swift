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

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    HStack(spacing: Space.md) {
                        stat("\(store.clips.count)", "Clips made")
                        stat("\(store.schedule.count)", "Scheduled")
                        stat("\(store.streak)", "Day streak")
                    }

                    if let best = bestClip {
                        VStack(alignment: .leading, spacing: Space.sm) {
                            SectionTitle(text: "Top clip")
                            Text(best.caption).font(AppFont.title).foregroundStyle(Palette.textPrimary).lineLimit(2)
                            HStack { FormatTag(formatId: best.formatId); Spacer(); ScoreBadge(score: best.predictedScore) }
                        }.marqueCard()
                    }

                    if !formatCounts.isEmpty {
                        VStack(alignment: .leading, spacing: Space.md) {
                            SectionTitle(text: "Your format mix")
                            ForEach(formatCounts, id: \.0) { name, count in
                                HStack {
                                    Text(name).font(AppFont.body).foregroundStyle(Palette.textPrimary)
                                    Spacer()
                                    Text("\(count)").font(AppFont.body).foregroundStyle(Palette.textSecondary)
                                }
                            }
                        }.marqueCard()
                    }

                    if store.clips.isEmpty {
                        EmptyStateView(icon: "chart.bar", title: "No data yet",
                                       message: "Make and schedule clips to see how you're doing.")
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.surface.ignoresSafeArea())
            .navigationTitle("Insights")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
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
