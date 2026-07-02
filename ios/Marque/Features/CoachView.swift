import SwiftUI

struct CoachView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router

    // MARK: Computed properties for inline insights
    private var logged: [ScheduledPost] { store.schedule.filter { ($0.metrics?.views ?? 0) > 0 } }
    private var totalViews: Int { logged.compactMap { $0.metrics?.views }.reduce(0, +) }
    private var totalLikes: Int { logged.compactMap { $0.metrics?.likes }.reduce(0, +) }
    private var avgEngagement: Double {
        let rates = logged.compactMap { $0.metrics?.engagementRate }
        return rates.isEmpty ? 0 : rates.reduce(0, +) / Double(rates.count)
    }
    private var heroValue: String { totalViews > 0 ? compactNumber(totalViews) : "\(store.clips.count)" }
    private var heroLabel: String { totalViews > 0 ? "total views" : "clips made" }
    private var formatCounts: [(String, Int)] {
        Dictionary(grouping: store.clips, by: { $0.formatName })
            .map { ($0.key, $0.value.count) }
            .sorted { $0.1 > $1.1 }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("YOUR EDGE").font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
                        ScreenTitle(text: "Coach")
                    }
                    Spacer()
                }

                // Teardown cards (performance feedback)
                if !store.teardowns.isEmpty {
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionLabel(text: "What worked", accent: Palette.accent)
                        ForEach(Array(store.teardowns.enumerated()), id: \.element.id) { i, t in
                            HStack(spacing: 0) {
                                RoundedRectangle(cornerRadius: 2, style: .continuous)
                                    .fill(Palette.accent)
                                    .frame(width: 3)
                                    .padding(.vertical, Space.md)
                                VStack(alignment: .leading, spacing: Space.sm) {
                                    Text(t.headline).font(AppFont.serifM).foregroundStyle(Palette.textPrimary)
                                    Text(t.detail).font(AppFont.body).foregroundStyle(Palette.textSecondary)
                                        .lineSpacing(5).fixedSize(horizontal: false, vertical: true)
                                    Text("\u{201C}\(t.clipCaption)\u{201D}").font(AppFont.caption)
                                        .foregroundStyle(Palette.textTertiary).lineLimit(1)
                                }
                                .padding(.horizontal, Space.lg).padding(.vertical, Space.md)
                            }
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                                .strokeBorder(Palette.hairline, lineWidth: 1))
                            .shadow(color: Palette.shadowWarm.opacity(0.06), radius: 10, x: 0, y: 4)
                            .staggerReveal(i)
                        }
                    }
                    MarqueHairline().padding(.vertical, Space.sm)
                }

                // Trend radar
                VStack(alignment: .leading, spacing: Space.md) {
                    SectionLabel(text: "Trending in your niche", accent: Palette.accent)
                    if store.trends.isEmpty {
                        TrendSkeletonView()
                    } else {
                        ForEach(Array(store.trends.enumerated()), id: \.element.id) { i, t in
                            HStack(spacing: 0) {
                                RoundedRectangle(cornerRadius: 2, style: .continuous)
                                    .fill(Palette.accent)
                                    .frame(width: 3)
                                    .padding(.vertical, Space.md)
                                VStack(alignment: .leading, spacing: Space.sm) {
                                    Text(t.title).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                    Text(t.why).font(AppFont.body).foregroundStyle(Palette.textSecondary)
                                        .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
                                    HStack {
                                        FormatTag(formatId: t.formatId)
                                        Spacer()
                                        Button {
                                            Task { await store.generateFromTrend(title: t.title, formatId: t.formatId) }
                                            router.showCreate = true
                                        } label: {
                                            HStack(spacing: 5) {
                                                Image(systemName: "sparkles").font(.system(size: 11, weight: .semibold))
                                                Text("Write a script").font(AppFont.callout)
                                            }
                                            .foregroundStyle(Palette.accent)
                                            .padding(.horizontal, Space.md).frame(height: 32)
                                            .background(Palette.accent.opacity(0.08))
                                            .clipShape(Capsule())
                                            .overlay(Capsule().strokeBorder(Palette.accent.opacity(0.25), lineWidth: 1))
                                        }
                                        .buttonStyle(.plain)
                                        .accessibilityIdentifier("coach.writeScript")
                                    }
                                }
                                .padding(.horizontal, Space.lg).padding(.vertical, Space.md)
                            }
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                                .strokeBorder(Palette.hairline, lineWidth: 1))
                            .shadow(color: Palette.shadowWarm.opacity(0.06), radius: 10, x: 0, y: 4)
                            .staggerReveal(i)
                        }
                    }
                }

                // Demo: generate a teardown for the latest ready/posted clip
                if let c = store.clips.first(where: { $0.status == .ready || $0.status == .scheduled || $0.status == .posted }),
                   store.teardowns.isEmpty {
                    GhostButton(title: "Show me what worked", systemImage: "sparkles") {
                        Task { await store.makeTeardown(for: c) }
                    }
                }

                // Inline performance summary (formerly InsightsView)
                MarqueHairline().padding(.vertical, Space.sm)
                VStack(alignment: .leading, spacing: Space.md) {
                    SectionLabel(text: "Your stats", accent: Palette.accent)
                    HStack(alignment: .lastTextBaseline, spacing: Space.sm) {
                        Text(heroValue)
                            .font(Typeface.display(36, .semibold)).tracking(Track.hero)
                            .foregroundStyle(Palette.textPrimary)
                        Text(heroLabel)
                            .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    }
                    HStack(spacing: Space.md) {
                        statMini("\(store.schedule.count)", "Scheduled")
                        statMini("\(store.streak)", "Sessions")
                        if totalLikes > 0 { statMini(compactNumber(totalLikes), "Likes") }
                    }
                }

                if !formatCounts.isEmpty {
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionLabel(text: "Format mix", accent: Palette.accent)
                        VStack(spacing: 0) {
                            ForEach(Array(formatCounts.enumerated()), id: \.element.0) { i, item in
                                if i > 0 { MarqueHairline() }
                                FormatBarRow(name: item.0, count: item.1, maxCount: formatCounts.first?.1 ?? 1)
                            }
                        }
                        .marqueCard(padding: 0)
                    }
                }
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.loadTrends(); await store.loadRecommendations() }
        .refreshable { await store.loadTrends(); await store.loadRecommendations() }
    }

    private func statMini(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(Typeface.display(20, .semibold)).foregroundStyle(Palette.textPrimary)
            Text(label).font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Trend skeleton (3-bar shimmer while loading)

private struct TrendSkeletonView: View {
    var body: some View {
        VStack(spacing: Space.md) {
            ForEach(0..<3, id: \.self) { _ in
                HStack(spacing: 0) {
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(Palette.textPrimary.opacity(0.07))
                        .frame(width: 3)
                        .padding(.vertical, Space.md)
                    VStack(alignment: .leading, spacing: Space.sm) {
                        RoundedRectangle(cornerRadius: 4, style: .continuous)
                            .fill(Palette.textPrimary.opacity(0.07))
                            .frame(height: 16)
                        RoundedRectangle(cornerRadius: 4, style: .continuous)
                            .fill(Palette.textPrimary.opacity(0.07))
                            .frame(height: 12).padding(.trailing, 40)
                    }
                    .padding(.horizontal, Space.lg).padding(.vertical, Space.md)
                }
                .background(Palette.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            }
        }
    }
}
