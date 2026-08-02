import SwiftUI

// The two taste-teaching decks. Both are plain content views (no scaffold, no chrome) so
// the SAME view backs the onboarding step and the "retake" sheet in the Editing-style
// settings — one deck, one set of copy, no drift between where you first meet it and
// where you come back to change it.

// MARK: - Editing taste (style deck → StyleProfile)

/// Swipe real reels whose editing has already been MEASURED. Each like/pass is a labelled
/// observation in `StyleProfileMapper.dims` space, folded into one profile by Rocchio.
struct StyleTasteSwiper: View {
    /// Handed the folded profile when the deck runs out. The caller decides where it lands
    /// (onboarding writes editPrefs; the settings retake overwrites it).
    let onFinish: (StyleProfile) -> Void
    /// Called when the deck can't be loaded at all — the caller skips the step rather than
    /// parking the creator on a spinner forever.
    var onUnavailable: () -> Void = {}

    @Environment(AppStore.self) private var store
    @State private var deck: StyleDeckPayload? = nil
    @State private var loadFailed = false
    @State private var liked: [[String: Double]] = []
    @State private var disliked: [[String: Double]] = []

    var body: some View {
        Group {
            if let deck, !deck.reels.isEmpty {
                SwiperStepShell(
                    cards: deck.reels,
                    payoff: "Every swipe teaches your editor — liked looks show up in your cuts.",
                    videoURL: { URL(string: $0.videoURL) },
                    onLike: { liked.append($0.vector) },
                    onPass: { disliked.append($0.vector) },
                    onUndo: { reel in
                        // Withdraw whichever bucket holds it — the shell doesn't know (and
                        // shouldn't) which verdict was recorded.
                        if let i = liked.lastIndex(where: { $0 == reel.vector }) { liked.remove(at: i) }
                        else if let i = disliked.lastIndex(where: { $0 == reel.vector }) { disliked.remove(at: i) }
                    },
                    onComplete: { onFinish(foldProfile()) },
                    cardView: { reel, ctx in reelCard(reel, ctx) }
                )
            } else if loadFailed {
                unavailable
            } else {
                ProgressView().tint(Palette.ink)
                    .frame(maxWidth: .infinity, minHeight: 380)
            }
        }
        .task { await load() }
    }

    private var unavailable: some View {
        VStack(spacing: Space.sm) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 22, weight: .light)).foregroundStyle(Palette.textTertiary)
            Text("Couldn't load the deck right now.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
            Text("Your editor starts on the proven defaults — you can teach it later in Profile.")
                .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, minHeight: 380)
        .onAppear { onUnavailable() }
    }

    private func load() async {
        guard deck == nil else { return }
        if let d = await store.backend.styleDeck(), !d.reels.isEmpty {
            deck = d
        } else {
            loadFailed = true
        }
    }

    /// Fold the session. Below `minLikes` Rocchio returns the base untouched — so the
    /// profile is honestly labelled "low" confidence and stays on cold start rather than
    /// pretending two taps taught us a taste.
    private func foldProfile() -> StyleProfile {
        let dims = StyleProfileMapper.rocchio(liked: liked, disliked: disliked,
                                              base: store.editPrefs.styleProfile?.dims)
        return StyleProfile(dims: dims,
                            confidence: liked.count < StyleProfileMapper.minLikes ? "low" : "medium",
                            source: "quiz", handTuned: false, swipedAt: Date())
    }

    @ViewBuilder private func reelCard(_ reel: StyleDeckReel, _ ctx: SwiperCardContext) -> some View {
        ZStack(alignment: .bottom) {
            // MEDIA IS DECORATION, NOT LAYOUT. `Color.clear` is what sizes this ZStack, so
            // the card is exactly the frame the shell gave it. Previously the .fill poster
            // sized the stack — a portrait thumb scaled to cover a 300x430 card is TALLER
            // than 430, so the bottom-aligned chip row was pushed below the visible rect
            // and sliced in half by the clip (owner: "not showing all the information that
            // should be at the bottom of the reel").
            //
            // The poster sits UNDER the player at every depth, not as an either/or: it is
            // both the depth-≥2 face (those cards never get a player — that's the whole
            // two-player budget) and the honest fallback when a clip URL is dead or still
            // buffering, where a bare player would paint a black rectangle.
            Color.clear
                .overlay {
                    AsyncImage(url: URL(string: reel.thumbnailURL)) { img in
                        img.resizable().aspectRatio(contentMode: .fill)
                    } placeholder: {
                        Palette.surfaceSunken
                    }
                }
                .overlay { if let player = ctx.player {
                    PooledPlayerView(player: player, cornerRadius: Radius.xl)
                } }
                .clipped()
            // Owner (build 63): "lighter everything". The old scrim was .black 0.72 from
            // the CARD MIDPOINT — it darkened half of every reel just to make two chips
            // legible. The chips now carry their own light pills, so this only needs to
            // be a shallow foot to seat them: a third of the strength, over a quarter of
            // the height.
            LinearGradient(colors: [.clear, .black.opacity(0.24)],
                           startPoint: .init(x: 0.5, y: 0.74), endPoint: .bottom)
            VStack(alignment: .leading, spacing: Space.sm) {
                // The WHY: what we measured about this edit, not what the video is about.
                FlowWrapChips(items: reel.displayAttrs)
                Text("@\(reel.author) · \(compactNumber(reel.views)) views")
                    .font(AppFont.caption).foregroundStyle(.white.opacity(0.92))
                    .lineLimit(1)
                    .shadow(color: .black.opacity(0.5), radius: 3, y: 1)
            }
            .padding(Space.md)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

// MARK: - Endings (CTA templates → the CTA library)

/// Swipe the pre-rendered ending templates. Unlike the taste deck this is multi-select by
/// design: every like becomes a one-tap CTA on the record screen, and the first one is the
/// default.
struct CTAPickSwiper: View {
    let onFinish: ([CTAStyleOption]) -> Void
    var onUnavailable: () -> Void = {}

    @Environment(AppStore.self) private var store
    @State private var styles: [CTAStyleOption] = []
    @State private var loadFailed = false
    @State private var liked: [CTAStyleOption] = []

    var body: some View {
        Group {
            if !styles.isEmpty {
                SwiperStepShell(
                    cards: styles,
                    payoff: "Liked endings become one-tap CTAs on every video.",
                    // The "No CTA" card is typographic — there is nothing to play.
                    videoURL: { $0.isNone ? nil : URL(string: $0.videoURL) },
                    onLike: { liked.append($0) },
                    onPass: { _ in },
                    onUndo: { style in liked.removeAll { $0.id == style.id } },
                    onComplete: { onFinish(liked) },
                    cardView: { style, ctx in ctaCard(style, ctx) }
                )
            } else if loadFailed {
                unavailable
            } else {
                ProgressView().tint(Palette.ink)
                    .frame(maxWidth: .infinity, minHeight: 380)
            }
        }
        .task { await load() }
    }

    private var unavailable: some View {
        VStack(spacing: Space.sm) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 22, weight: .light)).foregroundStyle(Palette.textTertiary)
            Text("Couldn't load the endings right now.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
            Text("Your videos will end clean — you can build a CTA library later.")
                .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, minHeight: 380)
        .onAppear { onUnavailable() }
    }

    private func load() async {
        guard styles.isEmpty else { return }
        let s = await store.backend.ctaStyles()
        if s.isEmpty { loadFailed = true; return }
        // v2 (build 63): when the backend curates a deck, the swiper shows ONLY those
        // cards — None + five finished-feeling endings, each with its own copy and
        // music. The full 20-template catalog stays reachable via Manage/library. An
        // older backend curates nothing → every card has inDeck == false except None →
        // fall back to the whole catalog.
        let curated = s.filter { $0.inDeck }
        styles = curated.count > 1 ? curated : s
    }

    @ViewBuilder private func ctaCard(_ style: CTAStyleOption, _ ctx: SwiperCardContext) -> some View {
        ZStack(alignment: .bottom) {
            if style.isNone {
                noCTAFace
            } else {
                // Ink plate UNDER the player (not instead of it): a template whose preview
                // render hasn't been generated yet degrades to a labelled card rather than
                // a black rectangle.
                Palette.ink
                    .overlay(Image(systemName: "rectangle.on.rectangle")
                        .font(.system(size: 26, weight: .ultraLight))
                        .foregroundStyle(Palette.onInk.opacity(0.35)))
                if let player = ctx.player, !style.videoURL.isEmpty {
                    PooledPlayerView(player: player, cornerRadius: Radius.xl)
                }
            }
            if !style.isNone {
                LinearGradient(colors: [.clear, .black.opacity(0.72)],
                               startPoint: .center, endPoint: .bottom)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(style.label)
                    .font(Typeface.display(19, .semibold)).tracking(Track.title)
                    .foregroundStyle(style.isNone ? Palette.textPrimary : .white)
                Text(style.blurb)
                    .font(AppFont.caption)
                    .foregroundStyle(style.isNone ? Palette.textSecondary : .white.opacity(0.75))
                    .lineLimit(2).fixedSize(horizontal: false, vertical: true)
            }
            .padding(Space.md)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// "No CTA" earns a real card, not a greyed-out one: it is the measured winner (most
    /// reels that perform end clean), so it reads as a confident typographic choice.
    private var noCTAFace: some View {
        ZStack {
            Palette.canvas
            VStack(spacing: Space.sm) {
                Text("No CTA")
                    .font(Typeface.display(30, .semibold)).tracking(Track.title)
                    .foregroundStyle(Palette.textPrimary)
                Rectangle().fill(Palette.hairline).frame(width: 44, height: 1)
                Text("ends clean")
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
            }
            .offset(y: -30)
        }
    }
}

// MARK: - Shared bits

/// Small hairline chips for a card's measured attributes. A LOCAL wrapping row rather than
/// the Profile's FlowWrap layout (that one is file-private there) — three short chips
/// never need real flow logic.
struct FlowWrapChips: View {
    let items: [String]

    var body: some View {
        HStack(spacing: 6) {
            ForEach(items.prefix(3), id: \.self) { t in
                Text(t)
                    .font(Typeface.sans(10, .medium)).tracking(0.2)
                    .foregroundStyle(Palette.ink)
                    .lineLimit(1)
                    .padding(.horizontal, 9).padding(.vertical, 5)
                    // A light pill carries its own contrast, so the card underneath no
                    // longer needs a heavy scrim to make these readable.
                    .background(Capsule().fill(.white.opacity(0.92)))
            }
        }
    }
}
