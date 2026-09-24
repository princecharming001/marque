import SwiftUI

// MARK: - Feed cards (Home daily feed)
// Three surfaces: the day's script picks (horizontal carousel), influencer reels to
// mimic (2-col 9:16 grid), and a quiet trend ticker. Reels are designed typographic-
// first — thumbnails are often empty in mock mode, so the no-imagery card is the
// primary design, not the fallback.

// MARK: Script pick — Stoic content card in the peeking carousel

/// Shared carousel metrics (the pick card, its skeleton, the offline card and the "More"
/// card all share one height so the row never jitters between states).
enum FeedCardMetrics {
    static let pickHeight: CGFloat = 240
}

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

    var body: some View {
        // Fixed height (FeedCardMetrics.pickHeight) is sized to the card's own minimum
        // content (feedback row + 3-line title + CTA row), so nothing ever clips the
        // Film-this/save row (the old "formatting is wrong on Today's Picks" bug).
        VStack(spacing: Space.sm) {
            HStack(spacing: Space.sm) {
                FormatTag(formatId: script.formatId)
                    .lineLimit(1).minimumScaleFactor(0.8)
                Spacer(minLength: Space.xs)
                Button(action: onLike) {
                    // Selection = inversion: liked is an ink disc with an onInk check.
                    Image(systemName: "checkmark")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(liked ? Palette.onInk : Palette.textSecondary)
                        .frame(width: 32, height: 32)
                        .background(Circle().fill(liked ? Palette.ink : Palette.surfaceSunken))
                        .animation(Motion.quick, value: liked)
                }
                .buttonStyle(PressableStyle(scale: 0.92)).accessibilityIdentifier("feed.like")
                .accessibilityLabel(liked ? "Liked" : "Like")
                Button(action: onDismiss) {
                    Image(systemName: "xmark")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Palette.textSecondary)
                        .frame(width: 32, height: 32)
                        .background(Circle().fill(Palette.surfaceSunken))
                }
                .buttonStyle(PressableStyle(scale: 0.92)).accessibilityIdentifier("feed.dismiss")
                .accessibilityLabel("Dismiss")
            }
            Spacer(minLength: 0)
            // Titles are clamped server-side (≤42 chars) so three lines always
            // fits the whole thing — never an ellipsis mid-word. Lowercase for the
            // editorial look — the classification above carries the caps.
            Text((script.title.isEmpty ? script.hook.text : script.title).lowercased())
                .font(AppFont.title2).tracking(-0.2)
                .foregroundStyle(Palette.textPrimary)
                .multilineTextAlignment(.center)
                .lineLimit(3).minimumScaleFactor(0.85)
                .frame(maxWidth: .infinity)
            // v15 fluff mandate: the bandit's why-picked line ("contrarian hooks +
            // myth-buster tend to over-index...") is exactly the explainer class the
            // owner cut from the script popup — the card is title + Film this, nothing
            // to justify. whyPicked stays on the model for the editor/insights surfaces.
            Spacer(minLength: 0)
            HStack(spacing: Space.sm) {
                Button(action: onFilm) {
                    Text("Film this")
                }
                .buttonStyle(.ds(.primary, height: 44))
                Button(action: onSave) {
                    Image(systemName: saved ? "bookmark.fill" : "bookmark")
                        .font(.system(size: 16, weight: .regular))
                        .foregroundStyle(Palette.textPrimary)
                        .frame(width: 44, height: 44)
                        .background(Circle().fill(Palette.surface))
                        .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
                        .contentShape(Circle())
                }
                .buttonStyle(PressableStyle(scale: 0.92))
                .accessibilityLabel(saved ? "Saved" : "Save")
                .accessibilityIdentifier("feed.save")
            }
        }
        .padding(Space.cardPad)
        .frame(maxWidth: .infinity)
        .frame(height: FeedCardMetrics.pickHeight, alignment: .top)
        .background(RoundedRectangle(cornerRadius: Radius.card, style: .continuous).fill(Palette.surface))
        .shadow(color: Palette.shadowWarm.opacity(0.04), radius: 12, x: 0, y: 2)
        // Whole card opens the full script; the inner Film/Save buttons keep
        // their own hit areas (buttons beat a background tap gesture).
        .contentShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
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
    /// Home-owned: the ONE reel currently allowed to stream. Every visible cell used
    /// to autoplay its remote MP4, so 4-6 AVPlayers streamed at once; now the
    /// most-recently-appeared cell claims this and everyone else shows their poster.
    @Binding var activeReelId: String?
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
    // without tapping in (owner: "I'm unable to play the reels"). Only the cell holding
    // the active claim plays, and only when it has a durable video URL; a failed/absent
    // URL falls back to the blur-fill thumbnail (already aspect-safe).
    @State private var onScreen = false
    @State private var videoFailed = false
    /// Downsampled + cached poster (ThumbnailCache) — AsyncImage decoded the scraped
    /// cover at full resolution, per appearance.
    @State private var poster: UIImage?
    private var canPlay: Bool {
        onScreen && activeReelId == reel.id && !videoFailed && !reel.videoURL.isEmpty
    }

    var body: some View {
        Button(action: onTap) {
            Color.clear
                .aspectRatio(9.0 / 16.0, contentMode: .fit)
                .background(backdrop)
                .overlay(content)
                .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))
                .contentShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))
        }
        .buttonStyle(PressableStyle())
        .onAppear {
            onScreen = true
            // Most-recently-appeared visible cell wins the single player slot.
            if !reel.videoURL.isEmpty && !videoFailed { activeReelId = reel.id }
        }
        .onDisappear {
            onScreen = false
            if activeReelId == reel.id { activeReelId = nil }
        }
        .task(id: reel.thumbnailURL) {
            guard poster == nil, let url = thumbURL else { return }
            if let img = await ThumbnailCache.remote(url, maxPixel: 800) {
                poster = img
                imageLoaded = true
            }
        }
        .accessibilityLabel("Reel by @\(reel.creatorHandle): \(reel.hookText)")
        .accessibilityIdentifier("feed.reel")
    }

    // Background: subtle Palette-derived vertical gradient; thumbnail (when present)
    // fills behind a darkening gradient so the white text stays legible. Poster-first:
    // the playing cell keeps its poster underneath while the stream warms up.
    @ViewBuilder private var backdrop: some View {
        if canPlay, let vurl = URL(string: reel.videoURL) {
            // Muted looping preview — the shared player autoplays, loops, guards junk
            // streams, and flips its own gravity to fit non-portrait footage.
            ZStack {
                posterLayer
                FailableVideoPlayer(url: vurl, muted: true, showsControls: false,
                                    isActive: canPlay,   // pause when another cell claims the slot
                                    onFailure: { videoFailed = true })
                scrim
            }
            .onAppear { imageLoaded = true }   // text stays white over the video
        } else if poster != nil {
            ZStack {
                posterLayer
                scrim
            }
        } else {
            typographicGround
        }
    }

    /// Blur-fill + fit (aspect-safe): a landscape/square scraped cover used to
    /// `scaledToFill` into the 9:16 cell as a ~3x center-crop ("overblown
    /// proportions"). The sharp copy `scaledToFit`s (portrait fills exactly;
    /// non-portrait letterboxes) over a blurred fill of itself, so the whole frame
    /// shows without a zoom-crop.
    @ViewBuilder private var posterLayer: some View {
        typographicGround              // visible until the poster lands (and if it never does)
        if let poster {
            ZStack {
                Image(uiImage: poster).resizable().scaledToFill()
                    .blur(radius: 16).opacity(0.55)
                    .overlay(Color.black.opacity(0.18))   // over media: stays dark in both schemes
                Image(uiImage: poster).resizable().scaledToFit()
            }
        }
    }

    /// Just enough scrim at the edges for the handle (top) and views (bottom).
    private var scrim: some View {
        LinearGradient(stops: [.init(color: .black.opacity(0.35), location: 0),
                               .init(color: .clear, location: 0.22),
                               .init(color: .clear, location: 0.72),
                               .init(color: .black.opacity(0.45), location: 1)],
                       startPoint: .top, endPoint: .bottom)
    }

    private var typographicGround: some View {
        ZStack {
            Palette.surface
            LinearGradient(colors: [Palette.accentMuted.opacity(0), Palette.accentMuted],
                           startPoint: .top, endPoint: .bottom)
        }
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            // Platform + handle
            HStack(spacing: Space.xs) {
                Image(systemName: reel.platform == "instagram" ? "camera.fill" : "music.note")
                    .font(.system(size: 10, weight: .semibold))
                Text("@\(reel.creatorHandle)")
                    .font(AppFont.caption.weight(.semibold))
                    .lineLimit(1)
            }
            .foregroundStyle(overImage ? Color.white.opacity(0.9) : Palette.textSecondary)

            Spacer(minLength: 0)

            // Typographic ground only: the hook carries the card when there's no
            // footage. Over a real thumbnail the video IS the content — text on
            // top just fights it (the idea lives in the detail sheet).
            if !overImage {
                Text(reel.hookText)
                    .font(AppFont.headline)
                    .tracking(Track.tight)
                    .foregroundStyle(Palette.textPrimary)
                    .lineLimit(4).minimumScaleFactor(0.85)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)

            // Views + provenance
            HStack(spacing: Space.xs) {
                Image(systemName: "eye").font(.system(size: 10))
                Text(compactNumber(reel.views)).font(AppFont.caption)
                Spacer(minLength: 0)
                if reel.fromWatched {
                    WatchingTag(overMedia: overImage)
                }
            }
            .foregroundStyle(overImage ? Color.white.opacity(0.9) : Palette.textSecondary)
        }
        .padding(Space.md)
    }
}

/// Monochrome "WATCHING" provenance tag: a sunken capsule on the typographic ground,
/// a translucent white capsule over footage.
struct WatchingTag: View {
    var overMedia: Bool
    var body: some View {
        Text("WATCHING")
            .font(AppFont.eyebrow).tracking(1)
            .foregroundStyle(overMedia ? Palette.onNight : Palette.textSecondary)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(Capsule().fill(overMedia ? Color.white.opacity(0.18) : Palette.surfaceSunken))
            .lineLimit(1).fixedSize()
    }
}

// MARK: Trend carousel — infinite scroll through trends with timed pauses

/// Measures a view's rendered width via its background — used to size the marquee's
/// single-copy width so the seamless-loop offset is exact, not guessed.
private struct TickerWidthKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

struct TrendTicker: View {
    let trend: TrendItem
    var all: [TrendItem] = []          // W1: the full niche-trend list (rotates the ticker)
    @State private var currentIndex = 0
    @State private var allTrends: [TrendItem] = []
    @State private var expanded = false
    @State private var pulse = false
    @State private var slideFromTrailing = true    // last advance direction → transition edges
    // Owner spec: idle state is a continuously-scrolling marquee (never static, no
    // discrete jumps) — it reads as ambient/alive rather than "wait for it to switch."
    // The FIRST tap is a one-way ratchet into the discrete, readable interval mode
    // (today's 30s auto-advance + swipe + expand-to-read-why) — engaged never resets.
    @State private var engaged = false
    @State private var marqueeOffset: CGFloat = 0
    @State private var marqueeCopyWidth: CGFloat = 0
    private static let marqueePointsPerSecond: Double = 34

    private var displayTrend: TrendItem { allTrends.isEmpty ? trend : allTrends[currentIndex % max(1, allTrends.count)] }

    /// Direction-aware slide: forward swipes push in from the trailing edge,
    /// backward swipes from the leading edge — the ticker reads as a carousel.
    private var slide: AnyTransition {
        .asymmetric(
            insertion: .move(edge: slideFromTrailing ? .trailing : .leading).combined(with: .opacity),
            removal: .move(edge: slideFromTrailing ? .leading : .trailing).combined(with: .opacity))
    }

    var body: some View {
        VStack(alignment: .center, spacing: Space.md) {
            // Stoic section eyebrow above the one surface card that holds the ticker.
            Text("TRENDING")
                .font(AppFont.eyebrow).tracking(Track.eyebrow)
                .foregroundStyle(Palette.textSecondary)
            VStack(alignment: .leading, spacing: 0) {
                Button {
                    // First tap is the ratchet: stop the ambient scroll, settle into the
                    // readable interval mode — and, same as always, toggle the why-detail.
                    engaged = true
                    withAnimation(Motion.quick) { expanded.toggle() }
                } label: {
                    HStack(spacing: Space.md) {
                        // Live marker: a quiet pulsing monochrome dot.
                        Circle().fill(Palette.textPrimary)
                            .frame(width: 6, height: 6)
                            .scaleEffect(pulse ? 1.0 : 0.7)
                            .opacity(pulse ? 1.0 : 0.4)
                        Group {
                            if engaged {
                                ZStack(alignment: .leading) {
                                    HStack(spacing: 6) {
                                        trendGlyph
                                        Text(Self.headline(displayTrend.title))
                                            .font(AppFont.caption)
                                            .foregroundStyle(Palette.textPrimary)
                                            .lineLimit(1)
                                    }
                                    .id("trend-title-\(currentIndex)")
                                    .transition(slide)
                                }
                            } else {
                                marquee
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .clipped()
                        Image(systemName: "chevron.right")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(Palette.textPrimary)
                            .rotationEffect(.degrees(expanded ? 90 : 0))
                    }
                    .padding(.horizontal, Space.rowPad)
                    .frame(minHeight: 52)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("feed.trend")

                if expanded {
                    DSRowDivider()
                    ZStack(alignment: .topLeading) {
                        Text(displayTrend.why)
                            .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                            .lineSpacing(3)
                            .fixedSize(horizontal: false, vertical: true)
                            .id("trend-why-\(currentIndex)")
                            .transition(slide)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .clipped()
                    .padding(.horizontal, Space.rowPad)
                    .padding(.vertical, Space.md)
                }
            }
            .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.surface))
            .clipShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
        }
        .contentShape(Rectangle())
        // Swipe to move between trends — works collapsed or expanded. HIGH priority
        // so the drag beats the expand button's tap recognition; minimumDistance
        // keeps plain taps flowing through to the button (a sub-24pt touch fails
        // the drag and falls back to the tap).
        .highPriorityGesture(DragGesture(minimumDistance: 24).onEnded { v in
            // Swipe is part of the engaged/interval experience — the same tap-in ratchet
            // as the button, so a swipe before any tap also settles the ticker in place.
            guard allTrends.count > 1, abs(v.translation.width) > abs(v.translation.height) else { return }
            engaged = true
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
        // Auto-advance every 30s once engaged AND collapsed; reading an expanded trend
        // never yanks it away — the cycle resumes on collapse. Task cancels itself on
        // expand/engage/list change, so there are no stray timers. Before the first
        // tap this never fires — the marquee owns the motion instead.
        .task(id: "\(engaged)-\(expanded)-\(allTrends.count)") {
            guard engaged, !expanded, allTrends.count > 1 else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                guard !Task.isCancelled else { return }
                advance(1)
            }
        }
    }

    // MARK: Ambient marquee (pre-engagement idle state)

    /// Trends read as trends, not as more video titles (owner 2026-09-23): each item is an
    /// up-trend glyph + the trend in sentence case. The backend now writes trend headlines;
    /// capitalizing here also fixes older lowercase ones still in caches.
    static func headline(_ raw: String) -> String {
        var t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        while t.hasSuffix(".") { t.removeLast() }
        guard let first = t.first else { return t }
        return first.uppercased() + t.dropFirst()
    }

    private var trendGlyph: some View {
        Image(systemName: "arrow.up.right")
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(Palette.textSecondary)
            .accessibilityHidden(true)
    }

    private var tickerTrends: [TrendItem] { allTrends.isEmpty ? [trend] : allTrends }

    /// Even spacing between items. It used to be a run of spaces around a "•" inside one
    /// string: proportional-width spaces made the gaps uneven, and a separator dot landed
    /// right next to the live dot.
    private static let itemGap: CGFloat = 28

    /// One copy of the tape: every trend, evenly spaced, with the same gap after the last
    /// item, so copies laid end to end loop seamlessly.
    private var tapeCopy: some View {
        HStack(spacing: Self.itemGap) {
            ForEach(Array(tickerTrends.enumerated()), id: \.offset) { _, t in
                HStack(spacing: 6) {
                    trendGlyph
                    Text(Self.headline(t.title))
                        .font(AppFont.caption)
                        .foregroundStyle(Palette.textPrimary)
                        .lineLimit(1)
                }
            }
        }
        .padding(.trailing, Self.itemGap)
        .fixedSize()
    }

    /// A seamless, continuously-scrolling tape of every trend — the "always moving, never
    /// switching at intervals" idle state. Identical copies laid side by side; animating the
    /// offset by exactly one copy's width and snapping back the instant it lands makes the
    /// loop invisible. Enough copies are laid down to cover the window even when the list
    /// is short (one trend used to leave a blank run at the trailing edge).
    ///
    /// The copies are `.fixedSize()` — deliberately far wider than their slot. A fixed-size
    /// view still reports that huge WIDTH upward during layout even once it's clipped, so
    /// the GeometryReader wrapper is what stops it from pushing the leading dot off-screen:
    /// a GeometryReader reports exactly the size ITS parent offers it, never its children's.
    private var marquee: some View {
        GeometryReader { windowGeo in
            let copies = marqueeCopyWidth > 0
                ? max(2, Int((windowGeo.size.width / marqueeCopyWidth).rounded(.up)) + 1) : 2
            HStack(spacing: 0) {
                tapeCopy.background(GeometryReader { g in
                    Color.clear.preference(key: TickerWidthKey.self, value: g.size.width)
                })
                ForEach(1..<copies, id: \.self) { _ in tapeCopy }
            }
            .offset(x: marqueeOffset)
            .frame(width: windowGeo.size.width, alignment: .leading)
        }
        .frame(height: 20)
        .clipped()
        // Soft edges: items fade in and out instead of being cut mid-word at the clip.
        .mask(LinearGradient(stops: [.init(color: .clear, location: 0),
                                     .init(color: .black, location: 0.07),
                                     .init(color: .black, location: 0.93),
                                     .init(color: .clear, location: 1)],
                             startPoint: .leading, endPoint: .trailing))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(tickerTrends.map { Self.headline($0.title) }.joined(separator: ", "))
        .onPreferenceChange(TickerWidthKey.self) { w in
            guard w > 0, w != marqueeCopyWidth else { return }
            marqueeCopyWidth = w
            startMarquee()
        }
    }

    private func startMarquee() {
        guard marqueeCopyWidth > 0, !engaged else { return }
        marqueeOffset = 0
        withAnimation(.linear(duration: marqueeCopyWidth / Self.marqueePointsPerSecond)
            .repeatForever(autoreverses: false)) {
            marqueeOffset = -marqueeCopyWidth
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

    private static let base = Palette.surfaceSunken      // gray block on the surface card
    private static let highlight = Palette.surface

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
        .padding(Space.cardPad)
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .frame(height: FeedCardMetrics.pickHeight, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: Radius.card, style: .continuous).fill(Palette.surface))
    }
}

/// Shimmering 9:16 placeholder for a reel grid cell, with a caption bar so it
/// reads as a reel thumbnail loading.
struct ReelSkeletonCard: View {
    // A `surface` tile hosts the sunken shimmer blocks: a bare surfaceSunken block sits
    // almost invisibly on the light canvas, so the tile gives the placeholder its shape.
    var body: some View {
        RoundedRectangle(cornerRadius: Radius.tile, style: .continuous)
            .fill(Palette.surface)
            .aspectRatio(9.0 / 16.0, contentMode: .fit)
            .overlay {
                VStack(alignment: .leading, spacing: Space.sm) {
                    SkeletonBlock(cornerRadius: Radius.sm)                        // poster
                    SkeletonBlock(cornerRadius: Radius.sm).frame(width: 90, height: 10)
                    SkeletonBlock(cornerRadius: Radius.sm).frame(width: 60, height: 10)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(Space.sm)
            }
    }
}
