import SwiftUI

// MARK: - Feed cards (Home daily feed)
// Three surfaces: the day's script picks (horizontal carousel), influencer reels to
// mimic (2-col 9:16 grid), and a quiet trend ticker. Reels are designed typographic-
// first — thumbnails are often empty in mock mode, so the no-imagery card is the
// primary design, not the fallback.

// MARK: Script pick — 260×190 carousel card

struct ScriptFeedCard: View {
    let script: Script
    var onFilm: () -> Void
    var onSave: () -> Void
    var saved: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack {
                FormatTag(formatId: script.formatId)
                Spacer()
                ScoreBadge(score: script.predictedScore).scaleEffect(0.85)
            }
            Text(script.title.isEmpty ? script.hook.text : script.title)
                .font(AppFont.serifM).tracking(Track.title)
                .foregroundStyle(Palette.textPrimary)
                .lineLimit(2).fixedSize(horizontal: false, vertical: true)
            Text("\u{201C}\(script.hook.text)\u{201D}")
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                .lineLimit(2)
            Spacer(minLength: 0)
            HStack(spacing: Space.sm) {
                Button(action: onFilm) {
                    Text("Film this").font(AppFont.callout).foregroundStyle(Palette.onInk)
                        .padding(.horizontal, Space.md).frame(height: 32)
                        .background(Palette.ink).clipShape(Capsule())
                }
                .buttonStyle(.plain)
                Button(action: onSave) {
                    Image(systemName: saved ? "bookmark.fill" : "bookmark")
                        .font(.system(size: 14))
                        .foregroundStyle(Palette.accent)
                        .frame(width: 32, height: 32)
                        .background(Palette.accent.opacity(0.08)).clipShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("feed.save")
            }
        }
        .padding(Space.lg)
        .frame(width: 260, height: 190, alignment: .topLeading)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
        .shadow(color: Palette.shadowWarm.opacity(0.06), radius: 12, x: 0, y: 6)
    }
}

// MARK: Reel — 9:16 typographic card (grid cell)

struct ReelCard: View {
    let reel: ReelItem
    var onTap: () -> Void

    private var thumbURL: URL? {
        guard !reel.thumbnailURL.isEmpty else { return nil }
        return URL(string: reel.thumbnailURL)
    }
    /// Text goes white over a darkened thumbnail; ink over the typographic ground.
    private var overImage: Bool { thumbURL != nil }

    var body: some View {
        Button(action: onTap) {
            Color.clear
                .aspectRatio(9.0 / 16.0, contentMode: .fit)
                .background(backdrop)
                .overlay(content)
                .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
                .contentShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        }
        .buttonStyle(PressableStyle())
        .accessibilityLabel("Reel by @\(reel.creatorHandle): \(reel.hookText)")
        .accessibilityIdentifier("feed.reel")
    }

    // Background: subtle Palette-derived vertical gradient; thumbnail (when present)
    // fills behind a darkening gradient so the white text stays legible.
    @ViewBuilder private var backdrop: some View {
        if let url = thumbURL {
            ZStack {
                typographicGround          // visible while the image loads
                AsyncImage(url: url) { img in
                    img.resizable().scaledToFill()
                } placeholder: {
                    Color.clear
                }
                LinearGradient(colors: [.black.opacity(0.28), .black.opacity(0.62)],
                               startPoint: .top, endPoint: .bottom)
            }
        } else {
            typographicGround
        }
    }

    private var typographicGround: some View {
        ZStack {
            Palette.surfaceRaised
            LinearGradient(colors: [Palette.ink.opacity(0.04), Palette.ink.opacity(0.10)],
                           startPoint: .top, endPoint: .bottom)
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            // Platform + handle
            HStack(spacing: 5) {
                Image(systemName: reel.platform == "instagram" ? "camera.fill" : "music.note")
                    .font(.system(size: 9, weight: .semibold))
                Text("@\(reel.creatorHandle)")
                    .font(AppFont.micro).tracking(0.4)
                    .lineLimit(1)
            }
            .foregroundStyle(overImage ? Color.white.opacity(0.85) : Palette.textTertiary)

            Spacer(minLength: 0)

            // The hook carries the card — big serif over the middle
            Text(reel.hookText)
                .font(Typeface.display(17))
                .tracking(Track.tight)
                .foregroundStyle(overImage ? Color.white : Palette.textPrimary)
                .lineLimit(4)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: 0)

            // Views + provenance
            HStack(spacing: 4) {
                Image(systemName: "eye").font(.system(size: 10))
                Text(compactNumber(reel.views)).font(AppFont.caption)
                Spacer(minLength: 0)
                if reel.fromWatched {
                    Chip(text: "WATCHING", tint: overImage ? Color.white : Palette.accent)
                }
            }
            .foregroundStyle(overImage ? Color.white.opacity(0.8) : Palette.textSecondary)
        }
        .padding(Space.md)
    }
}

// MARK: Trend ticker — one quiet expandable row, hairline-bounded (no card)

struct TrendTicker: View {
    let trend: TrendItem
    @State private var expanded = false
    @State private var pulse = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            MarqueHairline()
            Button {
                withAnimation(Motion.quick) { expanded.toggle() }
            } label: {
                HStack(spacing: Space.sm) {
                    Circle().fill(Palette.accent)
                        .frame(width: 6, height: 6)
                        .scaleEffect(pulse ? 1.0 : 0.7)
                        .opacity(pulse ? 1.0 : 0.4)
                    Text("TRENDING")
                        .font(AppFont.micro).tracking(Track.label)
                        .foregroundStyle(Palette.textTertiary)
                    Text(trend.title)
                        .font(AppFont.callout)
                        .foregroundStyle(Palette.textPrimary)
                        .lineLimit(1)
                    Spacer(minLength: Space.sm)
                    Image(systemName: "chevron.down")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Palette.textTertiary)
                        .rotationEffect(.degrees(expanded ? 180 : 0))
                }
                .padding(.vertical, Space.md)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("feed.trend")

            if expanded {
                Text(trend.why)
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    .lineSpacing(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, 14)   // clears the dot, aligns with the label
                    .padding(.bottom, Space.md)
                    .transition(.opacity)
            }
            MarqueHairline()
        }
        .onAppear {
            withAnimation(Motion.breath) { pulse = true }
        }
    }
}

// MARK: Skeletons (initial load)

/// Pulsing placeholder for a script pick card.
struct FeedSkeletonCard: View {
    @State private var dim = false
    var body: some View {
        RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .fill(Palette.surfaceSunken)
            .frame(width: 260, height: 190)
            .opacity(dim ? 0.55 : 1)
            .onAppear {
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { dim = true }
            }
    }
}

/// Pulsing 9:16 placeholder for a reel grid cell.
struct ReelSkeletonCard: View {
    @State private var dim = false
    var body: some View {
        RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .fill(Palette.surfaceSunken)
            .aspectRatio(9.0 / 16.0, contentMode: .fit)
            .opacity(dim ? 0.55 : 1)
            .onAppear {
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { dim = true }
            }
    }
}
