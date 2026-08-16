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
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack(spacing: Space.sm) {
                tile(String(format: "%.1f%%", engagementRate * 100), "engagement", strong: true)
                tile(compactNumber(reel.views), "views")
                if reel.followerCount > 0 { tile(compactNumber(reel.followerCount), "followers") }
            }
            HStack(spacing: Space.sm) {
                tile(compactNumber(reel.likes), "likes")
                if reel.comments > 0 { tile(compactNumber(reel.comments), "comments") }
                if reel.durationS > 0 { tile("\(reel.durationS)s", "length") }
            }
        }
    }

    private func tile(_ value: String, _ label: String, strong: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(value).font(strong ? AppFont.title : AppFont.headline)
                .foregroundStyle(strong ? Palette.accent : Palette.textPrimary)
                .lineLimit(1).minimumScaleFactor(0.7)   // 15.6M never truncates
            // "ENGAGEMENT" at Track.label overflows a third-width tile and used to
            // render as "ENGAGEME…". Tighter tracking + scale-down keeps every label
            // whole rather than truncating the one that names the headline metric.
            Text(label.uppercased()).font(AppFont.micro).tracking(0.8)
                .foregroundStyle(Palette.textTertiary)
                .lineLimit(1).minimumScaleFactor(0.72)
        }
        .frame(maxWidth: .infinity, minHeight: 58, alignment: .leading)
        .padding(.horizontal, Space.sm).padding(.vertical, Space.sm)
        // Card, not a flat gray block: white over the canvas with a hairline rim, so
        // the tiles read as one set with the rest of the app's surfaces.
        .background(Palette.surfaceRaised, in: RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
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
