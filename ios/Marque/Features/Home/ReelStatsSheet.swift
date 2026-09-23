import SwiftUI

// OWNER (2026-08-12): the pager's stats button used to open the full teardown sheet,
// which replays the video the user is literally already watching. This is the clean
// window they asked for instead — JUST the numbers and the caption/transcript, no
// media, medium detent. The full teardown (video + why-it-works + mimic) still
// lives in ReelDetailSheet for the Home cards.
struct ReelStatsSheet: View {
    @Environment(\.dismiss) private var dismiss
    let reel: ReelItem

    private var platformLabel: String { reel.platform == "instagram" ? "Instagram" : "TikTok" }
    private var engagementRate: Double {
        reel.views > 0 ? Double(reel.likes + reel.comments) / Double(reel.views) : 0
    }

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

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(.horizontal, Space.screenH)
                .padding(.top, Space.xl)
                .padding(.bottom, Space.xl)

            ScrollView {
                VStack(alignment: .leading, spacing: Space.sectionGap) {
                    tiles
                    caption
                }
                .padding(.horizontal, Space.screenH)
                .padding(.bottom, Space.xl)
            }
        }
        .background(Palette.canvas.ignoresSafeArea())
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    /// Sheet header (DESIGN.md §5): platform eyebrow over the centered @handle title,
    /// posted-ago caption under it, close glyph trailing.
    private var header: some View {
        ZStack(alignment: .top) {
            VStack(spacing: Space.xs) {
                Text(platformLabel.uppercased())
                    .font(AppFont.eyebrow).tracking(Track.eyebrow)
                    .foregroundStyle(Palette.textSecondary)
                Text("@\(reel.creatorHandle)")
                    .font(AppFont.title1).tracking(-0.3).foregroundStyle(Palette.textPrimary)
                    .lineLimit(1).minimumScaleFactor(0.7)
                if let ago = postedAgo {
                    Text("posted \(ago)")
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 44)
            HStack {
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark").font(.system(size: 20, weight: .regular))
                        .foregroundStyle(Palette.textPrimary)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(PressableStyle(dim: 0.6))
                .accessibilityLabel("Close")
                .accessibilityIdentifier("reelStats.close")
            }
            .padding(.trailing, -Space.md + Space.xs)
        }
    }

    // The headline metric creators optimize (engagement rate) leads; the raw
    // counts follow. Watch-time/retention isn't public for other creators'
    // posts, so nothing here is invented.
    private var tiles: some View {
        // Stoic Stats: 2-up sunken tiles, same conditional tiles in the same order.
        LazyVGrid(columns: [GridItem(.flexible(), spacing: Space.groupGap), GridItem(.flexible())],
                  alignment: .leading, spacing: Space.groupGap) {
            tile(String(format: "%.1f%%", engagementRate * 100), "engagement", strong: true)
            tile(compactNumber(reel.views), "views")
            if reel.followerCount > 0 { tile(compactNumber(reel.followerCount), "followers") }
            tile(compactNumber(reel.likes), "likes")
            if reel.comments > 0 { tile(compactNumber(reel.comments), "comments") }
            if reel.durationS > 0 { tile("\(reel.durationS)s", "length") }
        }
    }

    /// Stoic stat tile: sunken fill, big number, sentence-case label. The value scales
    /// down (never truncates: "15.6M") and the label wraps to two lines before it would
    /// ever clip ("ENGAGEME…" bug). `strong` (engagement) is the lead tile; in the mono
    /// system its emphasis is position, not color.
    private func tile(_ value: String, _ label: String, strong: Bool = false) -> some View {
        DSStatTile(value: value, label: label.prefix(1).uppercased() + label.dropFirst())
    }

    // "Make sure the captions are shown" — the spoken words when we transcribed
    // them, the post caption otherwise, honestly labeled either way.
    @ViewBuilder private var caption: some View {
        if !reel.transcript.isEmpty {
            VStack(alignment: .leading, spacing: Space.sm) {
                SectionLabel(text: reel.transcribed ? "Transcript" : "Caption", accent: nil)
                    .frame(maxWidth: .infinity)
                Text(reel.transcript)
                    .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                    .lineSpacing(5)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .dsCard(.sunken, radius: Radius.group, padding: Space.rowPad)
            }
        } else if !reel.hookText.isEmpty {
            VStack(alignment: .leading, spacing: Space.sm) {
                SectionLabel(text: "Hook", accent: nil)
                    .frame(maxWidth: .infinity)
                Text(reel.hookText)
                    .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                    .lineSpacing(5)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .dsCard(.sunken, radius: Radius.group, padding: Space.rowPad)
            }
        }
    }
}
