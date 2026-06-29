import SwiftUI

struct ScriptReaderView: View {
    @Environment(AppStore.self) private var store
    let script: Script
    @State private var showHookLab = false
    @State private var showFormatSheet = false
    @State private var showRecord = false
    @State private var steering = false

    private var live: Script { store.scripts.first { $0.id == script.id } ?? script }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                // Hook (tap → Hook Lab)
                VStack(alignment: .leading, spacing: Space.sm) {
                    SectionTitle(text: "Hook · tap to explore")
                    Button { showHookLab = true } label: {
                        Text(live.hook.text)
                            .font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
                            .multilineTextAlignment(.leading)
                            .fixedSize(horizontal: false, vertical: true)
                    }.buttonStyle(.plain)
                    .accessibilityIdentifier("script.hookButton")
                    HStack(spacing: Space.sm) {
                        Chip(text: live.hook.signal.label)
                        ScoreBadge(score: live.hook.strength)
                    }
                }

                // Format chip + swap
                HStack {
                    FormatTag(formatId: live.formatId)
                    Spacer()
                    Button("Swap format") { showFormatSheet = true }
                        .font(AppFont.callout).foregroundStyle(Palette.goldDeep)
                        .accessibilityIdentifier("script.swapFormat")
                }

                // Body + shot plan
                VStack(alignment: .leading, spacing: Space.md) {
                    SectionTitle(text: "Script")
                    Text(live.body).font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(live.cta).font(AppFont.bodyL).foregroundStyle(Palette.goldDeep)
                    Divider().background(Palette.hairline)
                    SectionTitle(text: "Shot plan")
                    ForEach(Array(live.shotPlan.enumerated()), id: \.offset) { _, s in
                        HStack(alignment: .top, spacing: Space.sm) {
                            Circle().fill(Palette.gold).frame(width: 5, height: 5).padding(.top, 7)
                            Text(s).font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        }
                    }
                }
                .marqueCard()

                // Steer
                VStack(alignment: .leading, spacing: Space.sm) {
                    SectionTitle(text: "Steer")
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: Space.sm) {
                            ForEach(["Shorter", "More contrarian", "Funnier", "More personal"], id: \.self) { label in
                                Button {
                                    steering = true
                                    Task { await store.steer(live, instruction: label); steering = false }
                                } label: { Chip(text: label) }.buttonStyle(.plain)
                                .accessibilityIdentifier("script.steer")
                            }
                        }
                    }
                    if steering { ProgressView().tint(Palette.gold) }
                }

            }
            .screenPadding()
            .padding(.vertical, Space.lg)
        }
        .background(Palette.surface.ignoresSafeArea())
        .safeAreaInset(edge: .bottom) {
            PrimaryButton(title: "Record this script", systemImage: "record.circle") { showRecord = true }
                .accessibilityIdentifier("script.record")
                .padding(.horizontal, Space.screenH)
                .padding(.vertical, Space.sm)
                .background(.ultraThinMaterial)
        }
        .navigationTitle("Script")
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showHookLab) { HookLabSheet(script: live) }
        .sheet(isPresented: $showFormatSheet) { FormatSwapSheet(script: live) }
        .fullScreenCover(isPresented: $showRecord) { RecordView(script: live) }
    }
}

// MARK: - Hook Lab (nested via progressive disclosure)

struct HookLabSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let script: Script
    @State private var hooks: [Hook] = []
    @State private var loading = true

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    Text("Pick your hook").font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
                    Text("Ranked by predicted strength across the 8 signal types.")
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    if loading {
                        ProgressView().tint(Palette.gold).frame(maxWidth: .infinity).padding()
                    } else {
                        ForEach(hooks) { h in
                            Button {
                                store.setHook(h, for: script.id); dismiss()
                            } label: {
                                VStack(alignment: .leading, spacing: Space.sm) {
                                    Text(h.text).font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                                        .fixedSize(horizontal: false, vertical: true)
                                        .multilineTextAlignment(.leading)
                                    HStack { Chip(text: h.signal.label); Spacer(); ScoreBadge(score: h.strength) }
                                }
                                .marqueCard()
                            }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("hooklab.pickHook")
                        }
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.surface.ignoresSafeArea())
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
        .task {
            hooks = await store.llm.hookLab(brand: store.brand, topic: script.pillarName)
            loading = false
        }
    }
}

// MARK: - Format swap

struct FormatSwapSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let script: Script
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: Space.md) {
                    ForEach(Catalog.formats) { f in
                        Button {
                            store.swapFormat(script, to: f.id); dismiss()
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                HStack { FormatTag(formatId: f.id); Spacer(); Text("\(f.targetSeconds)s").font(AppFont.caption).foregroundStyle(Palette.textTertiary) }
                                Text(f.blurb).font(AppFont.body).foregroundStyle(Palette.textSecondary)
                                    .multilineTextAlignment(.leading)
                            }
                            .marqueCard()
                        }.buttonStyle(.plain)
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.surface.ignoresSafeArea())
            .navigationTitle("Choose a format")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}
