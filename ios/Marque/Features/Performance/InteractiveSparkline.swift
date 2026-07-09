import SwiftUI

// I-3: a scrubbable views-over-time sparkline for the Performance tab. Drag across it to
// read the exact date + views at any point (vertical guide + floating badge). Copies the
// area/line/draw-on geometry of the Home momentum `Sparkline` but adds interactivity and
// keeps the real per-day dates — it renders ONLY real (non-placeholder) daily series.
struct InteractiveSparkline: View {
    let points: [BackendClient.PerformanceSummary.DailyPoint]
    var windowDays: Int = 30
    var color: Color = Palette.accent

    @State private var on = false
    @State private var scrubIndex: Int? = nil

    private var views: [Double] { points.map { Double($0.views) } }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            GeometryReader { geo in
                let pts = coords(in: geo.size)
                ZStack(alignment: .topLeading) {
                    if pts.count > 1 {
                        // area fill
                        Path { p in
                            p.move(to: CGPoint(x: pts[0].x, y: geo.size.height))
                            pts.forEach { p.addLine(to: $0) }
                            p.addLine(to: CGPoint(x: pts[pts.count - 1].x, y: geo.size.height))
                            p.closeSubpath()
                        }
                        .fill(LinearGradient(colors: [color.opacity(0.18), color.opacity(0.0)],
                                             startPoint: .top, endPoint: .bottom))
                        // line
                        Path { p in
                            p.move(to: pts[0]); pts.dropFirst().forEach { p.addLine(to: $0) }
                        }
                        .trim(from: 0, to: on ? 1 : 0)
                        .stroke(color, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))

                        // scrub guide + marker + badge
                        if let i = scrubIndex, i < pts.count {
                            Rectangle().fill(Palette.textTertiary.opacity(0.5))
                                .frame(width: 1).position(x: pts[i].x, y: geo.size.height / 2)
                                .frame(height: geo.size.height)
                            Circle().fill(color).frame(width: 7, height: 7).position(pts[i])
                            scrubBadge(for: i)
                                .position(x: min(max(pts[i].x, 44), geo.size.width - 44), y: 12)
                        } else if on, let last = pts.last {
                            Circle().fill(color).frame(width: 5, height: 5).position(last)
                        }
                    }
                }
                .contentShape(Rectangle())
                .gesture(DragGesture(minimumDistance: 0)
                    .onChanged { v in
                        guard pts.count > 1 else { return }
                        let stepX = geo.size.width / CGFloat(pts.count - 1)
                        scrubIndex = min(max(Int((v.location.x / max(stepX, 1)).rounded()), 0), pts.count - 1)
                    }
                    .onEnded { _ in scrubIndex = nil })
            }
            .frame(height: 56)

            // first/last date labels
            HStack {
                Text(label(for: 0)).font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                Spacer()
                Text(label(for: points.count - 1)).font(AppFont.micro).foregroundStyle(Palette.textTertiary)
            }
        }
        .onAppear { withAnimation(.easeOut(duration: 0.8)) { on = true } }
        .accessibilityIdentifier("performance.sparkline")
    }

    private func scrubBadge(for i: Int) -> some View {
        VStack(spacing: 1) {
            Text(label(for: i)).font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
            Text(compactNumber(points[i].views) + " views")
                .font(Typeface.sans(13, .semibold)).foregroundStyle(Palette.textPrimary)
        }
        .padding(.horizontal, 8).padding(.vertical, 4)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous).strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private func coords(in size: CGSize) -> [CGPoint] {
        guard views.count > 1 else { return [] }
        let maxV = views.max() ?? 1, minV = views.min() ?? 0
        let range = max(maxV - minV, 0.0001)
        let stepX = size.width / CGFloat(views.count - 1)
        return views.enumerated().map { i, v in
            CGPoint(x: CGFloat(i) * stepX, y: size.height - CGFloat((v - minV) / range) * size.height)
        }
    }

    private static let iso: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.locale = Locale(identifier: "en_US_POSIX"); return f
    }()
    private static let pretty: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMM d"; return f
    }()

    /// "MMM d" for a point — from its ISO date when present, else derived from today.
    private func label(for i: Int) -> String {
        guard i >= 0, i < points.count else { return "" }
        if let iso = points[i].date, let d = Self.iso.date(from: iso) { return Self.pretty.string(from: d) }
        let daysAgo = (points.count - 1) - i
        let d = Calendar.current.date(byAdding: .day, value: -daysAgo, to: Date()) ?? Date()
        return Self.pretty.string(from: d)
    }
}
