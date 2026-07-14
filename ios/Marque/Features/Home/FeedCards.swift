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
    /// Tap anywhere on the card (outside the buttons) → open the full script.
    var onOpen: () -> Void = {}
    // I-2: Today's-picks feedback — ✓ likes (learning signal), ✗ dismisses.
    var liked: Bool = false
    var onLike: () -> Void = {}
    var onDismiss: () -> Void = {}

    /// The bandit's "why" is often a long phrase with a repetitive "(niche baseline — …)"
    /// tail that truncated mid-word and left the card looking broken. Drop the parenthetical
    /// and cap at a word boundary so it reads as a clean 1–2 lines, never an ellipsis mid-word.
    private var shortWhy: String {
        var s = script.whyPicked
        if let r = s.range(of: " (") { s = String(s[..<r.lowerBound]) }
        s = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.count > 64 {
            let cut = s.prefix(64)
            s = (cut.lastIndex(of: " ").map { String(cut[..<$0]) } ?? String(cut)) + "…"
        }
        return s
    }

    var body: some View {
        // spacing sm (not md) + height 260 (not 190): the old fixed 190pt frame was
        // SHORTER than the card's own minimum content (tag row + 2-3 line title +
        // why-picked + buttons ≈ 250-260pt), so the clipShape cut the Film-this/save
        // row clean off — the "formatting is wrong on Today's Picks" bug.
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack(spacing: Space.sm) {
                FormatTag(formatId: script.formatId)
                Spacer()
                Button(action: onLike) {
                    Image(systemName: liked ? "checkmark.circle.fill" : "checkmark")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(liked ? Palette.accent : Palette.textTertiary)
                        .frame(width: 26, height: 26)
                        .background(Circle().fill(liked ? Palette.accent.opacity(0.12) : Palette.surfaceSunken))
                }
                .buttonStyle(PressableStyle()).accessibilityIdentifier("feed.like")
                Button(action: onDismiss) {
                    Image(systemName: "xmark")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Palette.textTertiary)
                        .frame(width: 26, height: 26)
                        .background(Circle().fill(Palette.surfaceSunken))
                }
                .buttonStyle(PressableStyle()).accessibilityIdentifier("feed.dismiss")
            }
            // Titles are clamped server-side (≤42 chars) so three lines always
            // fits the whole thing — never an ellipsis mid-word. Lowercase for the
            // editorial look — the classification above carries the caps.
            Text((script.title.isEmpty ? script.hook.text : script.title).lowercased())
                .font(AppFont.serifM).tracking(Track.title)
                .foregroundStyle(Palette.textPrimary)
                .lineLimit(3).fixedSize(horizontal: false, vertical: true)
            // UX-G2: WHY this pick is here — the bandit's honest reason, micro type.
            if !shortWhy.isEmpty {
                Text(shortWhy)
                    .font(AppFont.micro).tracking(0.2)
                    .foregroundStyle(Palette.textTertiary)
                    .lineLimit(2)
            }
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
        .frame(width: 260, height: 220, alignment: .topLeading)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
        .shadow(color: Palette.shadowWarm.opacity(0.06), radius: 12, x: 0, y: 6)
        // Whole card opens the full script; the inner Film/Save buttons keep
        // their own hit areas (buttons beat a background tap gesture).
        .contentShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .onTapGesture(perform: onOpen)
        // Same accessibilityIdentifier-leak fix as cleanupPanel (ProEditorView+Actions.swift):
        // without .accessibilityElement(children: .contain), this card's own identifier
        // clobbers the inner feed.like/feed.dismiss/feed.save button identifiers.
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("feed.pick")
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
    /// Keyed off the ACTUAL load, not URL presence — a URL that fails to load must
    /// fall back to ink-on-light or the text ends up white over the light ground.
    @State private var imageLoaded = false
    private var overImage: Bool { imageLoaded }
    // WS4: loop-play the reel right in the grid so the creator can see what it's about
    // without tapping in (owner: "I'm unable to play the reels"). Only the on-screen cell
    // plays (visibleReel), and only when it has a durable video URL; a failed/absent URL
    // falls back to the blur-fill thumbnail (already aspect-safe).
    @State private var onScreen = false
    @State private var videoFailed = false
    private var canPlay: Bool { onScreen && !videoFailed && !reel.videoURL.isEmpty }

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
        .onAppear { onScreen = true }
        .onDisappear { onScreen = false }
        .accessibilityLabel("Reel by @\(reel.creatorHandle): \(reel.hookText)")
        .accessibilityIdentifier("feed.reel")
    }

    // Background: subtle Palette-derived vertical gradient; thumbnail (when present)
    // fills behind a darkening gradient so the white text stays legible.
    @ViewBuilder private var backdrop: some View {
        if canPlay, let vurl = URL(string: reel.videoURL) {
            // Muted looping preview — the shared player autoplays, loops, guards junk
            // streams, and flips its own gravity to fit non-portrait footage.
            ZStack {
                typographicGround
                FailableVideoPlayer(url: vurl, muted: true, showsControls: false,
                                    onFailure: { videoFailed = true })
                LinearGradient(stops: [.init(color: .black.opacity(0.35), location: 0),
                                       .init(color: .clear, location: 0.22),
                                       .init(color: .clear, location: 0.72),
                                       .init(color: .black.opacity(0.45), location: 1)],
                               startPoint: .top, endPoint: .bottom)
            }
            .onAppear { imageLoaded = true }   // text stays white over the video
        } else if let url = thumbURL {
            ZStack {
                typographicGround          // visible while the image loads (and if it never does)
                AsyncImage(url: url) { phase in
                    if case .success(let img) = phase {
                        // Blur-fill + fit (aspect-safe): a landscape/square scraped cover
                        // used to `scaledToFill` into the 9:16 cell as a ~3x center-crop
                        // ("overblown proportions"). Now the sharp copy `scaledToFit`s
                        // (portrait fills exactly; non-portrait letterboxes) over a blurred
                        // fill of itself, so the whole frame shows without a zoom-crop.
                        ZStack {
                            img.resizable().scaledToFill()
                                .blur(radius: 16).opacity(0.55)
                                .overlay(Palette.ink.opacity(0.18))
                            img.resizable().scaledToFit()
                        }
                        .onAppear { imageLoaded = true }
                    } else {
                        Color.clear
                    }
                }
                if imageLoaded {
                    // With no text overlay the thumbnail can breathe — just enough
                    // scrim at the edges for the handle (top) and views (bottom).
                    LinearGradient(stops: [.init(color: .black.opacity(0.35), location: 0),
                                           .init(color: .clear, location: 0.22),
                                           .init(color: .clear, location: 0.72),
                                           .init(color: .black.opacity(0.45), location: 1)],
                                   startPoint: .top, endPoint: .bottom)
                }
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

            // Typographic ground only: the hook carries the card when there's no
            // footage. Over a real thumbnail the video IS the content — text on
            // top just fights it (the idea lives in the detail sheet).
            if !overImage {
                Text(reel.hookText)
                    .font(Typeface.display(17))
                    .tracking(Track.tight)
                    .foregroundStyle(Palette.textPrimary)
                    .lineLimit(4)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }

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

// MARK: Trend carousel — infinite scroll through trends with timed pauses

struct TrendTicker: View {
    let trend: TrendItem
    var all: [TrendItem] = []          // W1: the full niche-trend list (rotates the ticker)
    @State private var currentIndex = 0
    @State private var allTrends: [TrendItem] = []
    @State private var expanded = false
    @State private var pulse = false
    @State private var slideFromTrailing = true    // last advance direction → transition edges

    private var displayTrend: TrendItem { allTrends.isEmpty ? trend : allTrends[currentIndex % max(1, allTrends.count)] }

    /// Direction-aware slide: forward swipes push in from the trailing edge,
    /// backward swipes from the leading edge — the ticker reads as a carousel.
    private var slide: AnyTransition {
        .asymmetric(
            insertion: .move(edge: slideFromTrailing ? .trailing : .leading).combined(with: .opacity),
            removal: .move(edge: slideFromTrailing ? .leading : .trailing).combined(with: .opacity))
    }

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
                    ZStack(alignment: .leading) {
                        Text(displayTrend.title)
                            .font(AppFont.callout)
                            .foregroundStyle(Palette.textPrimary)
                            .lineLimit(1)
                            .id("trend-title-\(currentIndex)")
                            .transition(slide)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .clipped()
                    Image(systemName: "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Palette.textTertiary)
                        .rotationEffect(.degrees(expanded ? 90 : 0))
                }
                .padding(.vertical, Space.md)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("feed.trend")

            if expanded {
                ZStack(alignment: .topLeading) {
                    Text(displayTrend.why)
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                        .id("trend-why-\(currentIndex)")
                        .transition(slide)
                }
                .clipped()
                .padding(.leading, 14)
                .padding(.bottom, Space.md)
            }
            MarqueHairline()
        }
        .contentShape(Rectangle())
        // Swipe to move between trends — works collapsed or expanded. HIGH priority
        // so the drag beats the expand button's tap recognition; minimumDistance
        // keeps plain taps flowing through to the button (a sub-24pt touch fails
        // the drag and falls back to the tap).
        .highPriorityGesture(DragGesture(minimumDistance: 24).onEnded { v in
            guard allTrends.count > 1, abs(v.translation.width) > abs(v.translation.height) else { return }
            if v.translation.width < 0 { advance(1) } else { advance(-1) }
        })
        .onAppear {
            withAnimation(Motion.breath) { pulse = true }
            allTrends = all.count > 1 ? all : [trend]
        }
        .onChange(of: all) { _, new in
            allTrends = new.count > 1 ? new : [trend]
            currentIndex = 0
        }
        // Auto-advance every 30s while collapsed; reading an expanded trend never
        // yanks it away — the cycle resumes on collapse. Task cancels itself on
        // expand/list change, so there are no stray timers.
        .task(id: "\(expanded)-\(allTrends.count)") {
            guard !expanded, allTrends.count > 1 else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                guard !Task.isCancelled else { return }
                advance(1)
            }
        }
    }

    private func advance(_ step: Int) {
        guard !allTrends.isEmpty else { return }
        slideFromTrailing = step > 0
        withAnimation(Motion.quick) {
            currentIndex = (currentIndex + step + allTrends.count) % allTrends.count
        }
    }
}

// MARK: Skeletons (initial load)

// MARK: - Skeleton loading placeholders

/// A single shimmering placeholder block. The base is deliberately a touch darker
/// than the Home canvas (which is near-identical to `surfaceSunken`) so the shape
/// is legible, and a highlight band sweeps across so it clearly reads as *loading*
/// rather than empty/broken.
struct SkeletonBlock: View {
    var cornerRadius: CGFloat = Radius.sm
    @State private var travel = false

    private static let base = Color(hex: 0xE6E5E2)       // darker than canvas 0xF1F1EF → visible
    private static let highlight = Color(hex: 0xF7F7F5)

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        shape
            .fill(Self.base)
            .overlay(
                GeometryReader { geo in
                    let w = geo.size.width
                    LinearGradient(
                        colors: [.clear, Self.highlight.opacity(0.9), .clear],
                        startPoint: .leading, endPoint: .trailing)
                        .frame(width: w * 0.6)
                        // sweep from just off the left edge to just off the right edge
                        .offset(x: travel ? w * 1.1 : -w * 0.7)
                }
            )
            .clipShape(shape)
            .onAppear {
                withAnimation(.linear(duration: 1.15).repeatForever(autoreverses: false)) {
                    travel = true
                }
            }
    }
}

/// Shimmering placeholder for a script pick card — mirrors ScriptFeedCard's shape
/// (title lines, hook block, a CTA pill) so the load reads as "a card is coming".
struct FeedSkeletonCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            SkeletonBlock(cornerRadius: Radius.sm).frame(width: 70, height: 12)   // pillar tag
            SkeletonBlock(cornerRadius: Radius.sm).frame(height: 16)              // title line 1
            SkeletonBlock(cornerRadius: Radius.sm).frame(width: 150, height: 16)  // title line 2
            Spacer(minLength: 0)
            SkeletonBlock(cornerRadius: Radius.sm).frame(height: 13)              // hook line
            SkeletonBlock(cornerRadius: Radius.sm).frame(width: 120, height: 13)
            Spacer(minLength: 0)
            SkeletonBlock(cornerRadius: Radius.pill).frame(width: 96, height: 30) // CTA pill
        }
        .padding(Space.lg)
        .frame(width: 260, height: 220, alignment: .topLeading)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }
}

/// Shimmering 9:16 placeholder for a reel grid cell, with a caption bar so it
/// reads as a reel thumbnail loading.
struct ReelSkeletonCard: View {
    var body: some View {
        SkeletonBlock(cornerRadius: Radius.lg)
            .aspectRatio(9.0 / 16.0, contentMode: .fit)
            .overlay(alignment: .bottomLeading) {
                VStack(alignment: .leading, spacing: 6) {
                    SkeletonBlock(cornerRadius: Radius.sm).frame(width: 90, height: 10)
                    SkeletonBlock(cornerRadius: Radius.sm).frame(width: 60, height: 10)
                }
                .padding(Space.md)
            }
    }
}
