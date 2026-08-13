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
                .padding(.top, Space.lg)
                .padding(.bottom, Space.md)

            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
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

    private var header: some View {
        HStack(alignment: .center, spacing: Space.md) {
            VStack(alignment: .leading, spacing: 3) {
                Text("@\(reel.creatorHandle)")
                    .font(AppFont.title).foregroundStyle(Palette.textPrimary)
                    .lineLimit(1)
                HStack(spacing: Space.sm) {
                    Text(platformLabel.uppercased())
                        .font(AppFont.micro).tracking(Track.label)
                        .foregroundStyle(Palette.textTertiary)
                    if let ago = postedAgo {
                        Text("· \(ago)")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    }
                }
            }
            Spacer()
            Button { dismiss() } label: {
                Image(systemName: "xmark").font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Palette.textSecondary)
                    .frame(width: 30, height: 30)
                    .background(Palette.surfaceSunken).clipShape(Circle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("reelStats.close")
        }
    }

    // The headline metric creators optimize (engagement rate) leads; the raw
    // counts follow. Watch-time/retention isn't public for other creators'
    // posts, so nothing here is invented.
    private var tiles: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack(spacing: Space.md) {
                tile(String(format: "%.1f%%", engagementRate * 100), "engagement", strong: true)
                tile(compactNumber(reel.views), "views")
                if reel.followerCount > 0 { tile(compactNumber(reel.followerCount), "followers") }
            }
            HStack(spacing: Space.md) {
                tile(compactNumber(reel.likes), "likes")
                if reel.comments > 0 { tile(compactNumber(reel.comments), "comments") }
                if reel.durationS > 0 { tile("\(reel.durationS)s", "length") }
            }
        }
    }

    private func tile(_ value: String, _ label: String, strong: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(strong ? AppFont.title : AppFont.headline)
                .foregroundStyle(strong ? Palette.accent : Palette.textPrimary)
            Text(label).font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Space.sm)
        .background(Palette.surfaceSunken, in: RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
    }

    // "Make sure the captions are shown" — the spoken words when we transcribed
    // them, the post caption otherwise, honestly labeled either way.
    @ViewBuilder private var caption: some View {
        if !reel.transcript.isEmpty {
            VStack(alignment: .leading, spacing: Space.sm) {
                SectionLabel(text: reel.transcribed ? "Transcript" : "Caption", accent: nil)
                Text(reel.transcript)
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    .lineSpacing(5)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        } else if !reel.hookText.isEmpty {
            VStack(alignment: .leading, spacing: Space.sm) {
                SectionLabel(text: "Hook", accent: nil)
                Text(reel.hookText)
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    .lineSpacing(5)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
