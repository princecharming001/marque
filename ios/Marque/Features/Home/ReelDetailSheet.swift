import SwiftUI
import AVKit

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
    @State private var playbackFailed = false      // W2-4: 403/expired CDN URL → fall back to the hook panel
    @State private var thumbFailed = false         // expired thumbnail CDN URL → fall back to the hook panel
    // The footage's REAL aspect (w/h), reported by the player once known. Scraped reels
    // aren't all 9:16 — a landscape/square source aspect-filled into a portrait frame
    // was cropped ~4x into a zoomed blob. Clamped so an extreme source can't make the
    // media card degenerate (skyscraper or ribbon).
    @State private var videoAspect: CGFloat? = nil

    var body: some View {
        VStack(spacing: 0) {
            header
                .padding(.horizontal, Space.screenH)
                .padding(.top, Space.xl)
                .padding(.bottom, Space.lg)

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
                    .font(AppFont.title1).foregroundStyle(Palette.textPrimary)
                    .lineLimit(1).minimumScaleFactor(0.7)
                HStack(spacing: Space.md) {
                    Text(platformLabel.uppercased())
                        .font(AppFont.eyebrow).tracking(Track.eyebrow)
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
            Spacer(minLength: Space.sm)
            Button { dismiss() } label: {
                Image(systemName: "xmark").font(.system(size: 20, weight: .regular))
                    .foregroundStyle(Palette.textPrimary)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(PressableStyle(dim: 0.6))
            .accessibilityLabel("Close")
            .accessibilityIdentifier("reel.close")
        }
    }

    // MARK: Performance — honest public stats (retention/watch-time isn't available for
    // other creators' posts, so we surface the metrics that ARE: reach, engagement rate,
    // the interaction mix, and how it's aged).

    private var engagementRate: Double { reel.views > 0 ? Double(reel.likes + reel.comments) / Double(reel.views) : 0 }

    private var postedAgo: String? {
        guard !reel.postedAt.isEmpty else { return nil }
        let fmts = ["yyyy-MM-dd'T'HH:mm:ss.SSSZ", "yyyy-MM-dd'T'HH:mm:ssZ", "yyyy-MM-dd'T'HH:mm:ss"]
        let df = DateFormatter(); df.locale = Locale(identifier: "en_US_POSIX")
        for f in fmts { df.dateFormat = f; if let d = df.date(from: reel.postedAt) {
            let rel = RelativeDateTimeFormatter(); rel.unitsStyle = .full
            return rel.localizedString(for: d, relativeTo: Date())
        } }
        return nil
    }

    @ViewBuilder private var performance: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            SectionLabel(text: "Performance")
            // The headline metric creators optimize: engagement rate (likes+comments per view).
            HStack(spacing: Space.groupGap) {
                statTile(String(format: "%.1f%%", engagementRate * 100), "engagement", strong: true)
                statTile(compactNumber(reel.views), "views")
                if reel.followerCount > 0 { statTile(compactNumber(reel.followerCount), "followers") }
            }
            HStack(spacing: Space.groupGap) {
                statTile(compactNumber(reel.likes), "likes")
                if reel.comments > 0 { statTile(compactNumber(reel.comments), "comments") }
                if reel.durationS > 0 { statTile("\(reel.durationS)s", "length") }
            }
            if let ago = postedAgo {
                Text("Posted \(ago)").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
            }
            Text("Watch-time/retention isn't public for other creators' posts, engagement rate is the honest proxy.")
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Stoic stat tile (sunken, big number, sentence-case label); engagement leads by
    /// position, not color.
    private func statTile(_ value: String, _ label: String, strong: Bool = false) -> some View {
        DSStatTile(value: value, label: label.prefix(1).uppercased() + label.dropFirst())
    }

    // MARK: Detail — media + why it's working + structure

    private var detailContent: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            media

            performance

            if !reel.whyTrending.isEmpty {
                VStack(alignment: .leading, spacing: Space.sm) {
                    SectionLabel(text: "Why it's working")
                    Text(reel.whyTrending)
                        .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if !reel.transcript.isEmpty {
                VStack(alignment: .leading, spacing: Space.sm) {
                    // Honest labeling: only call it a transcript when it IS the
                    // spoken words — otherwise it's the post caption.
                    SectionLabel(text: reel.transcribed ? "Transcript" : "Caption", accent: nil)
                    Text(reel.transcript)
                        .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                        .lineSpacing(5)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    @ViewBuilder private var media: some View {
        if !reel.videoURL.isEmpty && !playbackFailed, let url = URL(string: reel.videoURL) {
            // Autoplaying + looping — the reel starts the moment the sheet opens, like
            // opening it on the platform itself. The frame starts 9:16 and snaps to the
            // FOOTAGE's real aspect once the player reports it, so a landscape or square
            // source shows whole instead of aspect-fill-cropped into a zoomed blob.
            FailableVideoPlayer(url: url,
                                onFailure: { playbackFailed = true },
                                onAspect: { a in
                                    withAnimation(Motion.quick) {
                                        videoAspect = min(max(a, 9.0 / 16.0), 16.0 / 9.0)
                                    }
                                })
                .aspectRatio(videoAspect ?? 9.0 / 16.0, contentMode: .fit)
                .frame(maxWidth: .infinity)
                .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))
        } else if !reel.thumbnailURL.isEmpty && !thumbFailed, let url = URL(string: reel.thumbnailURL) {
            // No playable footage but we do have the platform thumbnail (the same
            // one the feed's ReelCard shows) — a real preview beats a text panel.
            thumbnailPanel(url)
        } else {
            hookPanel
        }
    }

    /// Thumbnail preview: the reel's cover image at 9:16 with the hook over a
    /// bottom scrim. If the (scraped, expiring) CDN image fails, flip to the
    /// typographic hook panel — never a dead gray box (same posture as W2-4).
    private func thumbnailPanel(_ url: URL) -> some View {
        ZStack {
            Palette.surface                // ground while the image loads
            AsyncImage(url: url) { imgPhase in
                switch imgPhase {
                case .success(let img):
                    img.resizable().scaledToFill()
                case .failure:
                    Color.clear.onAppear { thumbFailed = true }
                default:
                    ProgressView().tint(Palette.textSecondary)
                }
            }
            LinearGradient(stops: [.init(color: .black.opacity(0.30), location: 0),
                                   .init(color: .clear, location: 0.24),
                                   .init(color: .clear, location: 0.58),
                                   .init(color: .black.opacity(0.60), location: 1)],
                           startPoint: .top, endPoint: .bottom)
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 5) {
                    Image(systemName: platformGlyph).font(.system(size: 10, weight: .semibold))
                    Text("@\(reel.creatorHandle)").font(AppFont.caption.weight(.semibold)).lineLimit(1)
                }
                .foregroundStyle(Palette.onNight.opacity(0.9))
                Spacer(minLength: 0)
                Text(reel.hookText)
                    .font(AppFont.title2)
                    .tracking(-0.2)
                    .foregroundStyle(Palette.onNight)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .shadow(color: .black.opacity(0.35), radius: 6, y: 1)
            }
            .padding(Space.lg)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        }
        .aspectRatio(9.0 / 16.0, contentMode: .fit)
        .frame(maxWidth: .infinity)
        .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))
    }

    /// No footage in mock mode — the hook *is* the media. Larger cut of the ReelCard look.
    private var hookPanel: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack(spacing: 5) {
                Image(systemName: platformGlyph).font(.system(size: 10, weight: .semibold))
                Text("@\(reel.creatorHandle)").font(AppFont.caption.weight(.semibold)).lineLimit(1)
            }
            .foregroundStyle(Palette.textSecondary)

            Spacer(minLength: 0)

            Text(reel.hookText)
                .font(AppFont.title1)
                .tracking(-0.3)
                .foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: 0)

            HStack(spacing: 4) {
                Image(systemName: "eye").font(.system(size: 11))
                Text(compactNumber(reel.views)).font(AppFont.caption)
                Spacer(minLength: 0)
                if reel.fromWatched {
                    WatchingTag(overMedia: false)
                }
            }
            .foregroundStyle(Palette.textSecondary)
        }
        .padding(Space.lg)
        .frame(maxWidth: .infinity, minHeight: 250, alignment: .leading)
        .background(
            ZStack {
                Palette.surface
                LinearGradient(colors: [Palette.accentMuted.opacity(0), Palette.accentMuted],
                               startPoint: .top, endPoint: .bottom)
            }
        )
        .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))
    }

    // MARK: Result — your version

    private func resultContent(_ script: Script, _ from: String) -> some View {
        VStack(alignment: .leading, spacing: Space.md) {
            // Success is carried by a check glyph, not a green bar.
            HStack(spacing: 6) {
                Image(systemName: "checkmark").font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Palette.textPrimary)
                SectionLabel(text: "Your version")
            }

            VStack(alignment: .leading, spacing: Space.sm) {
                HStack {
                    FormatTag(formatId: script.formatId)
                    Spacer()
                }
                Text(script.title.isEmpty ? script.hook.text : script.title)
                    .font(AppFont.title2).tracking(-0.2)
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("\u{201C}\(script.hook.text)\u{201D}")
                    .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                MarqueHairline()
                Text(script.body)
                    .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                    .lineSpacing(5)
                    .lineLimit(5)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .dsCard(.surface)

            Text("Structure from \(from), substance is all yours")
                .font(AppFont.caption)
                .foregroundStyle(Palette.textSecondary)
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
                            .foregroundStyle(Palette.textPrimary)
                        Text("Couldn't reach the studio just now, give it another try.")
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
        .background(Palette.canvas)
        .overlay(alignment: .top) { Rectangle().fill(Palette.hairline).frame(height: 1).opacity(0.6) }
        .animation(Motion.quick, value: phase)
    }

    // PrimaryButton recipe, hand-rolled so the in-flight state can host a spinner
    // ("Rewriting as you…") without swapping views out from under Maestro's `reel.mimic`.
    private var mimicCTA: some View {
        Button(action: runMimic) {
            HStack(spacing: Space.sm) {
                if phase == .working {
                    // MimicCTAStyle keeps the ink fill while disabled: onInk spinner.
                    ProgressView().tint(Palette.onInk)
                    Text("Rewriting as you…").font(AppFont.headline)
                } else {
                    Image(systemName: "wand.and.stars").font(.system(size: 16, weight: .semibold))
                    Text(phase == .failed ? "Try again" : "Mimic in my voice").font(AppFont.headline)
                }
            }
        }
        .buttonStyle(MimicCTAStyle())
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

// UX-A3: FailableVideoPlayer moved to DesignSystem/FailableVideoPlayer.swift
// (shared with the mimic cards; identical behavior, + muted/showsControls params).


/// Ink capsule for the sheet's mimic CTA. Deliberately ignores `isEnabled`: while the
/// rewrite is in flight the button is disabled, and the DS disabled look (sunken fill,
/// tertiary label) would make "Rewriting as you…" unreadable.
private struct MimicCTAStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(AppFont.headline)
            .lineLimit(1).minimumScaleFactor(0.85)
            .foregroundStyle(Palette.onInk)
            .padding(.horizontal, 32)
            .frame(maxWidth: .infinity)
            .frame(height: 56)
            .background(Capsule().fill(Palette.ink))
            .contentShape(Capsule())
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .opacity(configuration.isPressed ? 0.9 : 1)
            .animation(Motion.quick, value: configuration.isPressed)
    }
}
