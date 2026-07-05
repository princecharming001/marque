import SwiftUI

// The reel teardown sheet: watch (or read) a proven reel, see why it's working and how
// it's built, then "Mimic in my voice" — the backend rewrites the *structure* with the
// creator's substance (brand + memory), and the result flows straight into the Film queue.
struct ReelDetailSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @Environment(\.dismiss) private var dismiss

    let reel: ReelItem

    private enum Phase: Equatable {
        case detail                      // reading the teardown
        case working                     // mimic() in flight
        case result(Script, String)      // your version + "@handle" provenance
        case failed                      // mimic() returned nil
    }
    @State private var phase: Phase = .detail

    var body: some View {
        VStack(spacing: 0) {
            header
                .padding(.horizontal, Space.screenH)
                .padding(.top, Space.lg)
                .padding(.bottom, Space.md)

            ScrollView {
                Group {
                    if case .result(let script, let from) = phase {
                        resultContent(script, from)
                            .transition(.opacity.combined(with: .move(edge: .bottom)))
                    } else {
                        detailContent
                            .transition(.opacity)
                    }
                }
                .padding(.horizontal, Space.screenH)
                .padding(.bottom, Space.xl)
            }
            .animation(Motion.calm, value: phase)

            bottomBar
        }
        .background(Palette.canvas.ignoresSafeArea())
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
    }

    // MARK: Header — who + where + how big

    private var platformLabel: String { reel.platform == "instagram" ? "Instagram" : "TikTok" }
    private var platformGlyph: String { reel.platform == "instagram" ? "camera.fill" : "music.note" }

    private var header: some View {
        HStack(alignment: .center, spacing: Space.md) {
            VStack(alignment: .leading, spacing: 3) {
                Text("@\(reel.creatorHandle)")
                    .font(AppFont.title).foregroundStyle(Palette.textPrimary)
                    .lineLimit(1)
                HStack(spacing: Space.md) {
                    Text(platformLabel.uppercased())
                        .font(AppFont.micro).tracking(Track.label)
                        .foregroundStyle(Palette.textTertiary)
                    HStack(spacing: 3) {
                        Image(systemName: "eye").font(.system(size: 10))
                        Text(compactNumber(reel.views)).font(AppFont.caption)
                    }
                    HStack(spacing: 3) {
                        Image(systemName: "heart").font(.system(size: 10))
                        Text(compactNumber(reel.likes)).font(AppFont.caption)
                    }
                }
                .foregroundStyle(Palette.textSecondary)
            }
            Spacer()
            Button { dismiss() } label: {
                Image(systemName: "xmark").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Palette.textSecondary)
                    .frame(width: 30, height: 30)
                    .background(Palette.surfaceSunken).clipShape(Circle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("reel.close")
        }
    }

    // MARK: Detail — media + why it's working + structure

    private var detailContent: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            media

            if !reel.whyTrending.isEmpty {
                VStack(alignment: .leading, spacing: Space.sm) {
                    SectionLabel(text: "Why it's working", accent: Palette.accent)
                    Text(reel.whyTrending)
                        .font(AppFont.body).foregroundStyle(Palette.textPrimary)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if !reel.transcript.isEmpty {
                VStack(alignment: .leading, spacing: Space.sm) {
                    SectionLabel(text: "Structure", accent: nil)
                    Text(reel.transcript)
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        .lineSpacing(5)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    @ViewBuilder private var media: some View {
        if !reel.videoURL.isEmpty {
            LocalVideoPlayer(path: nil, remoteURL: reel.videoURL)
                .aspectRatio(9.0 / 16.0, contentMode: .fit)
                .frame(height: 340)
                .frame(maxWidth: .infinity)
                .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
        } else {
            hookPanel
        }
    }

    /// No footage in mock mode — the hook *is* the media. Larger cut of the ReelCard look.
    private var hookPanel: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack(spacing: 5) {
                Image(systemName: platformGlyph).font(.system(size: 10, weight: .semibold))
                Text("@\(reel.creatorHandle)").font(AppFont.micro).tracking(0.4).lineLimit(1)
            }
            .foregroundStyle(Palette.textTertiary)

            Spacer(minLength: 0)

            Text(reel.hookText)
                .font(Typeface.display(24))
                .tracking(Track.title)
                .foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: 0)

            HStack(spacing: 4) {
                Image(systemName: "eye").font(.system(size: 11))
                Text(compactNumber(reel.views)).font(AppFont.caption)
                Spacer(minLength: 0)
                if reel.fromWatched {
                    Chip(text: "WATCHING", tint: Palette.accent)
                }
            }
            .foregroundStyle(Palette.textSecondary)
        }
        .padding(Space.lg)
        .frame(maxWidth: .infinity, minHeight: 250, alignment: .leading)
        .background(
            ZStack {
                Palette.surfaceRaised
                LinearGradient(colors: [Palette.ink.opacity(0.04), Palette.ink.opacity(0.10)],
                               startPoint: .top, endPoint: .bottom)
            }
        )
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }

    // MARK: Result — your version

    private func resultContent(_ script: Script, _ from: String) -> some View {
        VStack(alignment: .leading, spacing: Space.md) {
            SectionLabel(text: "Your version", accent: Palette.positive)

            VStack(alignment: .leading, spacing: Space.sm) {
                HStack {
                    FormatTag(formatId: script.formatId)
                    Spacer()
                }
                Text(script.title.isEmpty ? script.hook.text : script.title)
                    .font(AppFont.serifM).tracking(Track.title)
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("\u{201C}\(script.hook.text)\u{201D}")
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                MarqueHairline()
                Text(script.body)
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    .lineSpacing(5)
                    .lineLimit(5)
            }
            .marqueCard()

            Text("Structure from \(from) — substance is all yours")
                .font(AppFont.micro)
                .foregroundStyle(Palette.textTertiary)
                .padding(.leading, Space.xs)
        }
        .padding(.top, Space.sm)
    }

    // MARK: Pinned bottom CTA

    private var bottomBar: some View {
        VStack(spacing: Space.sm) {
            if case .result(let script, let from) = phase {
                PrimaryButton(title: "Film this now", systemImage: "video.fill") {
                    store.readyScript(script, source: .mimic, mimickedFrom: from)
                    router.pendingFilmScriptId = script.id
                    dismiss()
                    router.showFilm = true
                }
                GhostButton(title: "Save to film queue", systemImage: "bookmark") {
                    store.readyScript(script, source: .mimic, mimickedFrom: from)
                    dismiss()
                }
                .accessibilityIdentifier("reel.saveMimic")
            } else {
                if phase == .failed {
                    HStack(spacing: Space.sm) {
                        Image(systemName: "wifi.exclamationmark")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(Palette.warning)
                        Text("Couldn't reach the studio just now — give it another try.")
                            .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        Spacer(minLength: 0)
                    }
                    .transition(.opacity)
                }
                mimicCTA
            }
        }
        .padding(.horizontal, Space.screenH)
        .padding(.top, Space.md)
        .padding(.bottom, Space.md)
        .background(.ultraThinMaterial)
        .animation(Motion.quick, value: phase)
    }

    // PrimaryButton recipe, hand-rolled so the in-flight state can host a spinner
    // ("Rewriting as you…") without swapping views out from under Maestro's `reel.mimic`.
    private var mimicCTA: some View {
        Button(action: runMimic) {
            HStack(spacing: Space.sm) {
                if phase == .working {
                    ProgressView().tint(Palette.onInk)
                    Text("Rewriting as you…").font(AppFont.headline)
                } else {
                    Image(systemName: "wand.and.stars").font(.system(size: 16, weight: .semibold))
                    Text(phase == .failed ? "Try again" : "Mimic in my voice").font(AppFont.headline)
                }
            }
            .foregroundStyle(Palette.onInk)
            .frame(maxWidth: .infinity).frame(height: 54)
            .background(Palette.ink)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .shadow(color: .black.opacity(0.12), radius: 10, x: 0, y: 4)
        }
        .buttonStyle(PressableStyle())
        .disabled(phase == .working)
        .accessibilityIdentifier("reel.mimic")
    }

    private func runMimic() {
        guard phase != .working else { return }
        withAnimation(Motion.quick) { phase = .working }
        Task {
            let result = await store.backend.mimic(reelItem: reel, brand: store.brand, memory: store.memory)
            withAnimation(Motion.calm) {
                if let result {
                    phase = .result(result.script, result.from)
                } else {
                    phase = .failed
                }
            }
        }
    }
}
