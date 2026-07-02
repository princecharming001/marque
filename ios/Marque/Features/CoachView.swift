import SwiftUI

struct CoachView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var showInsights = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("YOUR EDGE").font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
                        ScreenTitle(text: "Coach")
                    }
                    Spacer()
                    Button { showInsights = true } label: {
                        Image(systemName: "chart.bar").foregroundStyle(Palette.textSecondary)
                    }
                    .padding(.top, 28)
                    .accessibilityLabel("Insights")
                    .accessibilityIdentifier("coach.insights")
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
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.loadTrends() }
        .refreshable { await store.loadTrends() }
        .sheet(isPresented: $showInsights) { InsightsView() }
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
