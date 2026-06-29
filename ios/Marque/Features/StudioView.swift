import SwiftUI

struct StudioView: View {
    @Environment(AppStore.self) private var store
    @State private var generatingPillar: UUID?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                Text("Studio").font(AppFont.displayL).foregroundStyle(Palette.textPrimary)

                // Pillars
                VStack(alignment: .leading, spacing: Space.md) {
                    SectionTitle(text: "Your pillars")
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: Space.lg) {
                            ForEach(store.pillars) { p in
                                Button {
                                    generatingPillar = p.id
                                    Task { await store.generateScripts(for: p); generatingPillar = nil }
                                } label: {
                                    ZStack {
                                        PillarNode(pillar: p)
                                        if generatingPillar == p.id {
                                            ProgressView().tint(Palette.gold)
                                        }
                                    }
                                }
                                .buttonStyle(.plain)
                                .accessibilityIdentifier("studio.pillar.\(p.name)")
                            }
                        }
                    }
                    Text("Tap a pillar to write 3 fresh scripts.")
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                }

                // Scripts
                VStack(alignment: .leading, spacing: Space.md) {
                    HStack {
                        SectionTitle(text: "Ready to record")
                        Spacer()
                        if store.isGenerating { ProgressView().tint(Palette.gold) }
                    }
                    if store.scripts.isEmpty {
                        EmptyStateView(icon: "text.quote",
                                       title: "No scripts yet",
                                       message: "Tap a pillar above to generate your first batch.")
                    } else {
                        ForEach(store.scripts) { s in
                            NavigationLink(value: s) { ScriptRow(script: s) }
                                .buttonStyle(.plain)
                                .accessibilityIdentifier("studio.scriptRow")
                        }
                    }
                }
            }
            .screenPadding()
            .padding(.vertical, Space.lg)
        }
        .background(Palette.surface.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(for: Script.self) { ScriptReaderView(script: $0) }
    }
}

struct ScriptRow: View {
    let script: Script
    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            Text(script.hook.text)
                .font(AppFont.title).foregroundStyle(Palette.textPrimary)
                .lineLimit(2).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: Space.sm) {
                FormatTag(formatId: script.formatId)
                Text("\(script.targetSeconds)s").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                Spacer()
                ScoreBadge(score: script.predictedScore)
            }
        }
        .marqueCard()
    }
}
