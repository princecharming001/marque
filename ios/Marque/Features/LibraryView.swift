import SwiftUI

struct LibraryView: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                Text("Library").font(AppFont.displayL).foregroundStyle(Palette.textPrimary)

                if store.clips.isEmpty {
                    EmptyStateView(icon: "rectangle.stack",
                                   title: "No clips yet",
                                   message: "Record a script in Studio and your clips will land here.")
                } else {
                    ForEach(ClipStatus.allOrder, id: \.self) { status in
                        let group = store.clips.filter { $0.status == status }
                        if !group.isEmpty {
                            VStack(alignment: .leading, spacing: Space.md) {
                                SectionTitle(text: status.title)
                                ForEach(group) { ClipCell(clip: $0) }
                            }
                        }
                    }
                }
            }
            .screenPadding().padding(.vertical, Space.lg)
        }
        .background(Palette.surface.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct ClipCell: View {
    let clip: Clip
    var body: some View {
        HStack(spacing: Space.md) {
            ZStack {
                RoundedRectangle(cornerRadius: Radius.sm, style: .continuous)
                    .fill(Palette.surfaceSunken).frame(width: 54, height: 72)
                Image(systemName: "play.fill").foregroundStyle(Palette.textTertiary)
                if clip.status == .rendering { ProgressView().tint(Palette.gold) }
            }
            VStack(alignment: .leading, spacing: 4) {
                Text(clip.caption).font(AppFont.body).foregroundStyle(Palette.textPrimary).lineLimit(2)
                HStack(spacing: Space.sm) {
                    FormatTag(formatId: clip.formatId)
                    Text("\(clip.seconds)s").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                }
            }
            Spacer()
            ScoreBadge(score: clip.predictedScore)
        }
        .marqueCard(padding: Space.md)
    }
}

extension ClipStatus {
    static var allOrder: [ClipStatus] { [.ready, .rendering, .scheduled, .posted, .failed] }
    var title: String {
        switch self {
        case .ready: return "Ready"
        case .rendering: return "Rendering"
        case .scheduled: return "Scheduled"
        case .posted: return "Posted"
        case .failed: return "Needs attention"
        }
    }
}
