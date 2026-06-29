import SwiftUI

struct StudioView: View {
    @Environment(AppStore.self) private var store
    @State private var generatingPillar: UUID?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                ScreenTitle(text: "Studio")

                // Pillars — each carries the creator's angle; tap to write 3 scripts on it.
                VStack(alignment: .leading, spacing: Space.md) {
                    HStack {
                        SectionLabel(text: "Your pillars")
                        Spacer()
                        Text("AI · \(store.aiMode)")
                            .font(AppFont.micro).tracking(Track.label)
                            .foregroundStyle(store.aiMode == "Claude" ? Palette.positive : Palette.textTertiary)
                    }
                    if store.pillars.isEmpty {
                        Text("Finish your brand setup to get pillars.")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    } else {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: Space.md) {
                                ForEach(store.pillars) { p in
                                    PillarCard(pillar: p, generating: generatingPillar == p.id) {
                                        generatingPillar = p.id
                                        Task { await store.generateScripts(for: p); generatingPillar = nil }
                                    }
                                    .accessibilityIdentifier("studio.pillar.\(p.name)")
                                }
                            }
                            .padding(.horizontal, Space.screenH)
                        }
                        .padding(.horizontal, -Space.screenH)   // full-bleed carousel
                    }
                }

                // Scripts — collapsed title card → expand for summary+hook → open full reader.
                VStack(alignment: .leading, spacing: Space.md) {
                    HStack {
                        SectionLabel(text: "Ready to record")
                        Spacer()
                        if store.isGenerating { ProgressView().tint(Palette.accent) }
                    }
                    if store.scripts.isEmpty {
                        EmptyStateView(icon: "text.quote",
                                       title: "No scripts yet",
                                       message: "Tap a pillar above to generate your first batch.")
                    } else {
                        ForEach(store.scripts) { s in
                            ScriptCard(script: s).accessibilityIdentifier("studio.scriptRow")
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

// MARK: - Pillar card (angle + summary + generate)

struct PillarCard: View {
    let pillar: Pillar
    let generating: Bool
    let onGenerate: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack(spacing: 7) {
                Circle().fill(Color(hex: pillar.colorHex)).frame(width: 8, height: 8)
                Text(pillar.name)
                    .font(AppFont.serifM).tracking(Track.tight).textCase(.lowercase)
                    .foregroundStyle(Palette.textPrimary).lineLimit(1)
            }
            Text(pillar.summary.isEmpty ? "Write fresh scripts on this pillar." : pillar.summary)
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                .lineLimit(3).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            Spacer(minLength: Space.sm)
            Button(action: onGenerate) {
                HStack(spacing: 6) {
                    if generating { ProgressView().controlSize(.small).tint(Palette.onInk) }
                    else { Image(systemName: "sparkles").font(.system(size: 12, weight: .semibold)) }
                    Text(generating ? "Writing…" : "Write 3 scripts").font(AppFont.callout)
                }
                .foregroundStyle(Palette.onInk)
                .frame(maxWidth: .infinity).frame(height: 40)
                .background(Palette.ink)
                .clipShape(Capsule())
            }
            .buttonStyle(PressableStyle())
            .disabled(generating)
        }
        .padding(Space.md)
        .frame(width: 232, height: 158, alignment: .topLeading)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
        .shadow(color: Palette.shadowWarm.opacity(0.06), radius: 14, x: 0, y: 5)
    }
}

// MARK: - Expandable script card (title → summary+hook → open full reader)

struct ScriptCard: View {
    let script: Script
    @State private var expanded = false

    private var heading: String { script.title.isEmpty ? script.hook.text : script.title }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(Motion.spring) { expanded.toggle() }
            } label: {
                HStack(alignment: .top, spacing: Space.md) {
                    VStack(alignment: .leading, spacing: Space.sm) {
                        Text(heading)
                            .font(AppFont.serifM).tracking(Track.tight).textCase(.lowercase)
                            .foregroundStyle(Palette.textPrimary)
                            .multilineTextAlignment(.leading)
                            .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                        HStack(spacing: Space.sm) {
                            FormatTag(formatId: script.formatId)
                            Text("\(script.targetSeconds)s").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                            ScoreBadge(score: script.predictedScore)
                        }
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.down")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Palette.textTertiary)
                        .rotationEffect(.degrees(expanded ? 180 : 0))
                        .padding(.top, 3)
                }
            }
            .buttonStyle(PressableStyle())

            if expanded {
                VStack(alignment: .leading, spacing: Space.md) {
                    if !script.summary.isEmpty {
                        Text(script.summary)
                            .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        SectionLabel(text: "Hook", accent: Palette.accent)
                        Text("“\(script.hook.text)”")
                            .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    NavigationLink(value: script) {
                        HStack(spacing: Space.sm) {
                            Text("Open script").font(AppFont.headline)
                            Spacer()
                            Image(systemName: "arrow.right").font(.system(size: 15, weight: .semibold))
                        }
                        .foregroundStyle(Palette.onInk)
                        .padding(.horizontal, Space.md).frame(height: 48)
                        .frame(maxWidth: .infinity)
                        .background(Palette.ink)
                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    }
                    .buttonStyle(PressableStyle())
                    .accessibilityIdentifier("studio.openScript")
                }
                .padding(.top, Space.md)
                .transition(.opacity)
            }
        }
        .marqueCard()
    }
}
