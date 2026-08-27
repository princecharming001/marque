import SwiftUI

// Performance tab: the upcoming queue (next 7 days) on top, 30-day
// Instagram/TikTok insights below.
struct PerformanceView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var sheet: CalSheet?
    @State private var mode: CalMode = .week
    // P7.3/P7.4: the Palo brain surfaces — insight inbox + the compiled strategy.
    @State private var aiInsights: [BackendClient.InsightItem] = []
    @State private var showStrategy = false


    /// Small inline glass segmented control — the row-height replacement for the old
    /// full-width MarqueSegmented blocks (three of which stacked on this one screen).
    @ViewBuilder
    static func compactToggleView(options: [String], index: Binding<Int>) -> some View {
        HStack(spacing: 2) {
            ForEach(Array(options.enumerated()), id: \.offset) { i, label in
                Button { index.wrappedValue = i } label: {
                    Text(label)
                        .font(Typeface.sans(12, index.wrappedValue == i ? .semibold : .regular))
                        .foregroundStyle(index.wrappedValue == i ? Palette.onInk : Palette.textSecondary)
                        .padding(.horizontal, 12).padding(.vertical, 6)
                        .background(Capsule().fill(index.wrappedValue == i ? Palette.ink : Color.clear))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(2)
        .background(LiquidGlassFill(radius: 20, sheen: 0.35, corners: false))
        .clipShape(Capsule())
        .overlay(Capsule().strokeBorder(Color.white.opacity(0.5), lineWidth: 1))
    }

    private func compactToggle(options: [String], index: Binding<Int>) -> some View {
        Self.compactToggleView(options: options, index: index)
    }

    private var week: [Date] {
        let cal = Calendar.current
        let start = cal.startOfDay(for: Date())
        return (0..<7).compactMap { cal.date(byAdding: .day, value: $0, to: start) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.lg) {
                ScreenTitle(text: "Performance")

                // MARK: Upcoming queue — one quiet label, one compact control. The old
                // stack (accent-bar eyebrow + full-width segmented pill) was two rows of
                // chrome before any content.
                HStack {
                    Text("COMING UP").font(AppFont.micro).tracking(Track.label)
                        .foregroundStyle(Palette.textTertiary)
                    Spacer()
                    compactToggle(options: CalMode.allCases.map(\.rawValue),
                                  index: Binding(get: { CalMode.allCases.firstIndex(of: mode) ?? 0 },
                                                 set: { mode = CalMode.allCases[$0] }))
                        .accessibilityIdentifier("calendar.modeToggle")
                }

                if mode == .week {
                    // Seven identical "Nothing scheduled" cards read as a wall of holes —
                    // when the whole week is empty, say it once with a way in instead.
                    if !week.contains(where: { day in
                        store.schedule.contains { Calendar.current.isDate($0.date, inSameDayAs: day) }
                    }) {
                        VStack(spacing: Space.md) {
                            VStack(spacing: 6) {
                                Text("Nothing scheduled this week")
                                    .font(Typeface.sans(16, .semibold)).foregroundStyle(Palette.textPrimary)
                                Text("Queue a ready clip and it shows up here with its posting time.")
                                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                                    .multilineTextAlignment(.center)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, Space.xl)
                            GhostButton(title: "Schedule a clip", systemImage: "calendar") {
                                sheet = .schedule(day: Calendar.current.startOfDay(for: Date()), clipId: nil)
                            }
                            .accessibilityIdentifier("performance.addClip")
                        }
                    } else {
                        VStack(spacing: 12) {
                            ForEach(Array(week.enumerated()), id: \.element) { _, day in
                                DayRow(day: day,
                                       posts: store.schedule
                                        .filter { Calendar.current.isDate($0.date, inSameDayAs: day) }
                                        .sorted { $0.date < $1.date },
                                       hasReady: store.clips.contains { $0.status == .ready },
                                       clipFor: { id in store.clips.first { $0.id == id } },
                                       onAdd: { sheet = .schedule(day: day, clipId: nil) },
                                       onTapPost: { sheet = .edit($0) },
                                       onDuplicate: { store.duplicatePost($0) })
                            }
                        }
                    }
                } else {
                    MonthGrid(schedule: store.schedule) { day in sheet = .schedule(day: day, clipId: nil) }
                }

                // MARK: P7.3/P7.4 — the AI coach: strategy entry + post-performance insights
                MarqueHairline().padding(.vertical, Space.sm)
                Text("FROM YOUR AI").font(AppFont.micro).tracking(Track.label)
                    .foregroundStyle(Palette.textTertiary)
                Button { showStrategy = true } label: {
                    HStack(spacing: Space.md) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Your Strategy").font(AppFont.headline)
                                .foregroundStyle(Palette.textPrimary)
                            Text("What Yunicorn has learned about your content")
                                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        }
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(Palette.textTertiary)
                    }
                    .padding(Space.md)
                    .background(LiquidGlassFill(radius: Radius.md, sheen: 0.5))
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                        .strokeBorder(Color.white.opacity(0.55), lineWidth: 1))
                    .shadow(color: Palette.shadowCool.opacity(0.14), radius: 18, y: 8)
                }
                .buttonStyle(PressableStyle(dim: 0.7))
                .accessibilityIdentifier("performance.yourStrategy")

                if !aiInsights.isEmpty {
                    VStack(spacing: 0) {
                        ForEach(Array(aiInsights.prefix(5).enumerated()), id: \.element.id) { i, ins in
                            Button {
                                router.pendingChatPrompt = ins.seedPrompt
                                router.selectedTab = .chat
                            } label: {
                                HStack(alignment: .top, spacing: Space.md) {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(ins.title).font(Typeface.sans(14, .semibold))
                                            .foregroundStyle(Palette.textPrimary)
                                            .multilineTextAlignment(.leading)
                                        if !ins.description.isEmpty {
                                            Text(ins.description).font(AppFont.caption)
                                                .foregroundStyle(Palette.textSecondary)
                                                .multilineTextAlignment(.leading)
                                                .lineLimit(2)
                                        }
                                    }
                                    Spacer(minLength: 0)
                                    Image(systemName: "chevron.right")
                                        .font(.system(size: 11, weight: .semibold))
                                        .foregroundStyle(Palette.textTertiary)
                                        .padding(.top, 4)
                                }
                                .padding(Space.md)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            if i < min(aiInsights.count, 5) - 1 {
                                Divider().overlay(Palette.hairline).padding(.leading, Space.md)
                            }
                        }
                    }
                    .background(Palette.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                        .strokeBorder(Palette.hairline, lineWidth: 1))
                }

                // MARK: 30-day insights (Phase 9 completes: platform toggle, series, best post)
                MarqueHairline().padding(.vertical, Space.sm)
                InsightsSection()
            }
            .screenPadding().padding(.vertical, Space.lg).padding(.bottom, 120)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $sheet) { s in
            switch s {
            case .schedule(let day, let clipId): SchedulePickerSheet(day: day, preselectClipId: clipId)
            case .edit(let post): PostEditorSheet(post: post)
            }
        }
        .onAppear { consumePendingSchedule() }
        .sheet(isPresented: $showStrategy) { StrategyView() }
        .task {
            aiInsights = await store.backend.fetchInsights()
            await store.syncPostMetrics()        // build 68: results arrive on their own
        }
        .onChange(of: router.pendingScheduleClipId) { _, _ in consumePendingSchedule() }
    }

    /// Library "Schedule this clip" deep-links here — open the scheduler for today, pre-filtered to that clip.
    private func consumePendingSchedule() {
        guard let id = router.pendingScheduleClipId else { return }
        sheet = .schedule(day: Calendar.current.startOfDay(for: Date()), clipId: id)
        router.pendingScheduleClipId = nil
    }
}

// MARK: - 30-day insights

struct InsightsSection: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var summary: BackendClient.PerformanceSummary?
    @State private var platform = 0   // 0 all · 1 instagram · 2 tiktok
    @State private var period = 1     // 0 = 7d · 1 = 30d · 2 = 90d
    @State private var loaded = false
    @State private var loading = false

    private let periodDays = [7, 30, 90]
    private let periodLabels = ["7 days", "30 days", "90 days"]

    /// Real, measured data — as opposed to a seeded/placeholder series the backend
    /// flags with no_data:true or mode:"mock". When false we still show the tracker,
    /// just with honest zeros and a one-line note (never a "post N to unlock" gate).
    private var hasRealData: Bool {
        guard let s = summary else { return false }
        return !(s.no_data ?? false) && s.mode != "mock"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack {
                Text("PERFORMANCE").font(AppFont.micro).tracking(Track.label)
                    .foregroundStyle(Palette.textTertiary)
                Spacer()
                if loading { ProgressView().controlSize(.small).tint(Palette.textTertiary) }
            }

            // One control row instead of two stacked full-width segmented pills: the
            // time window as a compact glass toggle, the platform as a quiet dropdown.
            HStack {
                PerformanceView.compactToggleView(options: ["7d", "30d", "90d"], index: $period)
                    .accessibilityIdentifier("performance.periodToggle")
                    .onChange(of: period) { _, _ in Task { await reload() } }
                Spacer()
                Menu {
                    Picker("Platform", selection: $platform) {
                        Text("All platforms").tag(0)
                        Text("Instagram").tag(1)
                        Text("TikTok").tag(2)
                    }
                } label: {
                    HStack(spacing: 4) {
                        Text(platform == 0 ? "All" : platform == 1 ? "Instagram" : "TikTok")
                            .font(Typeface.sans(12, .medium)).foregroundStyle(Palette.textSecondary)
                        Image(systemName: "chevron.down")
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(Palette.textTertiary)
                    }
                    .padding(.vertical, 6)
                    .contentShape(Rectangle())
                }
                .accessibilityIdentifier("performance.platformToggle")
            }

            // Stat tiles — real numbers only. I-3: never show fabricated totals when the
            // series is placeholder (no_data); dashes read honestly instead.
            HStack(spacing: Space.md) {
                statTile(hasRealData ? compactNumber(views(summary!)) : "", "Views")
                statTile(hasRealData ? compactNumber(likes(summary!)) : "", "Likes")
                statTile(hasRealData ? "+\(follows(summary!))" : "", "Follows")
            }

            // I-3: interactive, dated graph — only for real data (a fabricated series is as
            // dishonest as fabricated tiles).
            if let s = summary, hasRealData, platform == 0, s.daily.contains(where: { $0.views > 0 }) {
                InteractiveSparkline(points: s.daily, windowDays: s.days)
                    .padding(.vertical, Space.xs)
            }

            if loaded, !hasRealData {
                // Honest, quiet note — not a locked feature.
                Text("No posts in this window yet. Publish a clip and your views, likes, and follows show up here.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    .lineSpacing(3).fixedSize(horizontal: false, vertical: true)
            }

            // Coaching read-out (only when the loop has something real to say).
            if hasRealData, !store.coaching.isEmpty {
                MarqueHairline().padding(.vertical, Space.xs)
                VStack(alignment: .leading, spacing: Space.sm) {
                    HStack {
                        Text("YOUR COACH").font(AppFont.micro).tracking(Track.label)
                            .foregroundStyle(Palette.textTertiary)
                        Spacer()
                    }
                    Text(store.coaching)
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .task {
            if !loaded { await reload(); loaded = true }
        }
    }

    private func reload() async {
        loading = true
        summary = await store.backend.fetchPerformanceSummary(days: periodDays[period])
        store.learnedBestHour = summary?.best_hour          // C-12
        await store.loadInsights()
        loading = false
    }


    private func views(_ s: BackendClient.PerformanceSummary) -> Int {
        switch platform {
        case 1: return s.platforms["instagram"]?.views ?? 0
        case 2: return s.platforms["tiktok"]?.views ?? 0
        default: return s.totals.views
        }
    }
    private func likes(_ s: BackendClient.PerformanceSummary) -> Int {
        switch platform {
        case 1: return s.platforms["instagram"]?.likes ?? 0
        case 2: return s.platforms["tiktok"]?.likes ?? 0
        default: return s.totals.likes
        }
    }
    private func follows(_ s: BackendClient.PerformanceSummary) -> Int {
        switch platform {
        case 1: return s.platforms["instagram"]?.follows_gained ?? 0
        case 2: return s.platforms["tiktok"]?.follows_gained ?? 0
        default: return s.totals.follows_gained
        }
    }
    private func normalized(_ values: [Double]) -> [Double] {
        guard let mx = values.max(), mx > 0 else { return values }
        return values.map { $0 / mx }
    }

    private func statTile(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            // Sans, not the serif display face — numbers are data, not headlines
            // (owner: "uses the fancy font way too much").
            Text(value).font(Typeface.sans(22, .semibold)).foregroundStyle(Palette.textPrimary)
            Text(label.uppercased()).font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Space.md)
        .background(LiquidGlassFill(radius: Radius.md, sheen: 0.45))
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(Color.white.opacity(0.55), lineWidth: 1))
        .shadow(color: Palette.shadowCool.opacity(0.12), radius: 14, y: 6)
    }
}
