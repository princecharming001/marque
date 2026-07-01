import SwiftUI

struct CoachView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var showInsights = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                HStack {
                    ScreenTitle(text: "Coach")
                    Spacer()
                    Button { showInsights = true } label: {
                        Image(systemName: "chart.bar").foregroundStyle(Palette.textSecondary)
                    }
                    .accessibilityLabel("Insights")
                    .accessibilityIdentifier("coach.insights")
                }

                // Teardown cards (performance feedback)
                if !store.teardowns.isEmpty {
                    VStack(alignment: .leading, spacing: Space.md) {
                        SectionTitle(text: "What worked")
                        ForEach(store.teardowns) { t in
                            VStack(alignment: .leading, spacing: Space.sm) {
                                Text(t.headline).font(AppFont.title).foregroundStyle(Palette.textPrimary)
                                Text(t.detail).font(AppFont.body).foregroundStyle(Palette.textSecondary)
                                Text("“\(t.clipCaption)”").font(AppFont.caption).foregroundStyle(Palette.textTertiary).lineLimit(1)
                            }
                            .marqueCard()
                        }
                    }
                }

                // Trend radar
                VStack(alignment: .leading, spacing: Space.md) {
                    SectionTitle(text: "Trending in your niche")
                    if store.trends.isEmpty {
                        ProgressView().tint(Palette.gold)
                    } else {
                        ForEach(store.trends) { t in
                            VStack(alignment: .leading, spacing: Space.sm) {
                                Text(t.title).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                Text(t.why).font(AppFont.body).foregroundStyle(Palette.textSecondary)
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
                                        .foregroundStyle(Palette.onInk)
                                        .padding(.horizontal, Space.md).frame(height: 34)
                                        .background(Palette.ink).clipShape(Capsule())
                                    }
                                    .buttonStyle(.plain)
                                    .accessibilityIdentifier("coach.writeScript")
                                }
                            }
                            .marqueCard(padding: Space.md)
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
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
        .background(Palette.surface.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.loadTrends() }
        .refreshable { await store.loadTrends() }
        .sheet(isPresented: $showInsights) { InsightsView() }
    }
}
