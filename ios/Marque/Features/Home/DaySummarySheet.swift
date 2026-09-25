import SwiftUI

/// The Home week strip's day sheet: what the creator filmed and posted that day. Tapping a
/// clip opens its usual detail sheet (play, share, edit). Presentation only; the grouping
/// rules live in DaySummary.
struct DaySummarySheet: View {
    let day: Date
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var openClip: Clip? = nil

    private var summary: DaySummary {
        DaySummary.build(day: day, clips: store.clips, footage: store.footage, schedule: store.schedule)
    }

    private var isToday: Bool { Calendar.current.isDateInToday(day) }

    private var title: String {
        if isToday { return "today." }
        if Calendar.current.isDateInYesterday(day) { return "yesterday." }
        let f = DateFormatter()
        f.setLocalizedDateFormatFromTemplate("EEEEMMMd")
        return f.string(from: day).lowercased() + "."
    }

    var body: some View {
        let s = summary
        ScrollView {
            VStack(alignment: .leading, spacing: Space.lg) {
                DSSheetHeader(title: title, eyebrow: "YOUR DAY", onClose: { dismiss() },
                              closeIdentifier: "day.close")
                HStack(spacing: Space.sm) {
                    DSStatTile(value: "\(s.filmedCount)", label: "Filmed")
                    DSStatTile(value: "\(s.posted.count)", label: "Posted")
                    DSStatTile(value: s.views.map(Self.compact) ?? "", label: "Views")
                }
                .accessibilityIdentifier("day.stats")

                if s.isEmpty {
                    Text(isToday ? "Nothing filmed or posted yet today." : "Nothing filmed or posted this day.")
                        .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityIdentifier("day.empty")
                }
                if !s.posted.isEmpty { postSection("Posted", s.posted, id: "day.posted") }
                if !s.clips.isEmpty || !s.footage.isEmpty { filmedSection(s) }
                if !s.scheduled.isEmpty {
                    postSection(s.day < Calendar.current.startOfDay(for: Date()) ? "Scheduled, not posted" : "Scheduled",
                                s.scheduled, id: "day.scheduled")
                }
            }
            .padding(.horizontal, Space.screenH)
            .padding(.top, Space.xl)          // clear of the sheet's grabber
            .padding(.bottom, Space.xl)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .sheet(item: $openClip) { ClipDetailSheet(clip: $0) }
    }

    // MARK: sections

    private func postSection(_ label: String, _ items: [DaySummary.PostedItem], id: String) -> some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            DSEyebrow(text: label.uppercased())
            VStack(spacing: 0) {
                ForEach(Array(items.enumerated()), id: \.offset) { i, item in
                    if i > 0 { DSRowDivider(inset: 76) }
                    row(thumbPath: item.clip?.thumbnailPath, thumbURL: item.clip?.thumbnailURL,
                        title: item.clip.map(Self.clipTitle) ?? (item.post.caption.isEmpty ? "Post" : item.post.caption),
                        detail: postDetail(item.post)) {
                        if let c = item.clip { openClip = c }
                    }
                }
            }
            .dsCard(.surface, radius: Radius.group, padding: 0)
        }
        .accessibilityIdentifier(id)
    }

    private func filmedSection(_ s: DaySummary) -> some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            DSEyebrow(text: "FILMED")
            VStack(spacing: 0) {
                ForEach(Array(s.clips.enumerated()), id: \.element.id) { i, c in
                    if i > 0 { DSRowDivider(inset: 76) }
                    row(thumbPath: c.thumbnailPath, thumbURL: c.thumbnailURL, title: Self.clipTitle(c),
                        detail: clipDetail(c)) { openClip = c }
                }
                ForEach(Array(s.footage.enumerated()), id: \.element.id) { i, f in
                    if i > 0 || !s.clips.isEmpty { DSRowDivider(inset: 76) }
                    row(thumbPath: f.thumbnailPath, thumbURL: nil,
                        title: f.title.isEmpty ? "Raw take" : f.title,
                        detail: "Raw take · \(Self.duration(f.seconds))", action: nil)
                }
            }
            .dsCard(.surface, radius: Radius.group, padding: 0)
        }
        .accessibilityIdentifier("day.filmed")
    }

    private func row(thumbPath: String?, thumbURL: String?, title: String, detail: String,
                     action: (() -> Void)?) -> some View {
        Button { action?() } label: {
            HStack(spacing: Space.md) {
                LocalThumbnail(path: thumbPath, isVideo: true, remoteImageURL: thumbURL, cornerRadius: Radius.sm)
                    .frame(width: 48, height: 64)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(AppFont.headline).foregroundStyle(Palette.textPrimary).lineLimit(2)
                    Text(detail).font(AppFont.caption).foregroundStyle(Palette.textSecondary).lineLimit(1)
                }
                Spacer(minLength: 0)
                if action != nil {
                    Image(systemName: "chevron.right").font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(Palette.textTertiary)
                }
            }
            .padding(.horizontal, Space.rowPad).padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressableStyle(dim: 0.8))
        .disabled(action == nil)
    }

    // MARK: copy

    private static func clipTitle(_ c: Clip) -> String {
        !c.title.isEmpty ? c.title : (!c.caption.isEmpty ? c.caption : c.formatName)
    }

    private func clipDetail(_ c: Clip) -> String {
        let state: String
        switch c.status {
        case .draft: state = "Draft"
        case .rendering: state = "Editing"
        case .ready: state = "Ready"
        case .scheduled: state = "Scheduled"
        case .posted: state = "Posted"
        case .failed: state = "Needs a retry"
        }
        return "\(state) · \(Self.duration(c.seconds)) · \(Self.time(c.createdAt))"
    }

    private func postDetail(_ p: ScheduledPost) -> String {
        var parts = [p.platforms.map(\.label).joined(separator: " + "), Self.time(p.date)]
        if let m = p.metrics, m.views > 0 { parts.append("\(Self.compact(m.views)) views") }
        return parts.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    private static func time(_ d: Date) -> String {
        let f = DateFormatter(); f.timeStyle = .short; f.dateStyle = .none
        return f.string(from: d)
    }

    private static func duration(_ s: Int) -> String {
        s >= 60 ? "\(s / 60)m \(s % 60)s" : "\(max(s, 0))s"
    }

    static func compact(_ n: Int) -> String {
        n >= 1_000_000 ? String(format: "%.1fM", Double(n) / 1_000_000)
            : n >= 10_000 ? "\(n / 1000)K"
            : n >= 1_000 ? String(format: "%.1fK", Double(n) / 1000) : "\(n)"
    }
}
