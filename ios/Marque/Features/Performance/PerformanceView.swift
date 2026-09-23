import SwiftUI

// Performance tab: the upcoming queue (next 7 days) on top, 30-day
// Instagram/TikTok insights below.
struct PerformanceView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var sheet: CalSheet?
    @State private var mode: CalMode = .week
    @State private var showStrategy = false

    // P7.3/P7.4: the Palo brain surfaces — insight inbox + the compiled strategy.
    // The data itself is store-cached (AppStore.aiInsights) so a tab switch inside
    // the staleness window repaints without refetching.
    private var aiInsights: [BackendClient.InsightItem] { store.aiInsights }


    /// Filter-pill segmented control (DESIGN.md Journey header: outline pills, the selected
    /// one inverted to ink). Row-height, so the three windows/modes never stack as blocks.
    @ViewBuilder
    static func compactToggleView(options: [String], index: Binding<Int>) -> some View {
        HStack(spacing: Space.sm) {
            ForEach(Array(options.enumerated()), id: \.offset) { i, label in
                let active = index.wrappedValue == i
                Button { index.wrappedValue = i } label: {
                    Text(label)
                        .font(AppFont.supporting.weight(active ? .semibold : .regular))
                        .foregroundStyle(active ? Palette.onInk : Palette.textPrimary)
                        .lineLimit(1)
                        .padding(.horizontal, 14).frame(height: 36)
                        .background(Capsule().fill(active ? Palette.ink : Palette.surface))
                        .overlay(Capsule().strokeBorder(active ? .clear : Palette.hairline, lineWidth: 1))
                        .contentShape(Capsule())
                        .animation(Motion.quick, value: active)
                }
                .buttonStyle(PressableStyle(dim: 0.8))
                .accessibilityAddTraits(active ? .isSelected : [])
            }
        }
    }

    private func compactToggle(options: [String], index: Binding<Int>) -> some View {
        Self.compactToggleView(options: options, index: index)
    }

    private var week: [Date] {
        let cal = Calendar.current
        let start = cal.startOfDay(for: Date())
        return (0..<7).compactMap { cal.date(byAdding: .day, value: $0, to: start) }
    }

    /// Read-only counts for the insights card's stat row (display only).
    private var weekPostCount: Int {
        store.schedule.filter { post in
            week.contains { Calendar.current.isDate(post.date, inSameDayAs: $0) }
        }.count
    }
    private var readyClipCount: Int { store.clips.filter { $0.status == .ready }.count }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                DSPageTitle(title: "Performance")

                // MARK: Journey insights card — the strategy door + a stat row, one night
                // card at the top of the page (DESIGN.md §6 Journey).
                VStack(alignment: .leading, spacing: Space.stack) {
                    Button { showStrategy = true } label: {
                        DSHeroCard(radius: Radius.card, padding: Space.cardPad) {
                            VStack(alignment: .leading, spacing: Space.md) {
                                HStack(alignment: .top, spacing: Space.md) {
                                    VStack(alignment: .leading, spacing: Space.xs) {
                                        DSEyebrow(text: "From your AI", color: Palette.onNightSecondary)
                                        Text("your strategy.")
                                            .font(AppFont.title2).tracking(-0.2)
                                            .foregroundStyle(Palette.onNight)
                                        Text("What Yunicorn has learned about your content")
                                            .font(AppFont.supporting)
                                            .foregroundStyle(Palette.onNight.opacity(0.8))
                                            .fixedSize(horizontal: false, vertical: true)
                                            .multilineTextAlignment(.leading)
                                    }
                                    Spacer(minLength: 0)
                                    Image(systemName: "chevron.right")
                                        .font(.system(size: 15, weight: .semibold))
                                        .foregroundStyle(Palette.onNight)
                                        .padding(.top, Space.lg)
                                }
                                Rectangle().fill(Color.white.opacity(0.14)).frame(height: 1)
                                HStack(spacing: 0) {
                                    heroStat("\(weekPostCount)", "this week")
                                    heroStat("\(readyClipCount)", "ready clips")
                                    heroStat("\(aiInsights.count)", "insights")
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .contentShape(RoundedRectangle(cornerRadius: Radius.card, style: .continuous))
                    }
                    .buttonStyle(PressableStyle(dim: 0.9))
                    .accessibilityIdentifier("performance.yourStrategy")

                    if !aiInsights.isEmpty {
                        DSGroup {
                            ForEach(Array(aiInsights.prefix(5).enumerated()), id: \.element.id) { i, ins in
                                Button {
                                    router.pendingChatPrompt = ins.seedPrompt
                                    router.selectedTab = .chat
                                } label: {
                                    HStack(alignment: .top, spacing: Space.md) {
                                        Image(systemName: "sparkle")
                                            .font(.system(size: 15, weight: .regular))
                                            .foregroundStyle(Palette.textPrimary)
                                            .frame(width: 20)
                                            .padding(.top, 2)
                                        VStack(alignment: .leading, spacing: 3) {
                                            Text(ins.title).font(AppFont.headline)
                                                .foregroundStyle(Palette.textPrimary)
                                                .multilineTextAlignment(.leading)
                                                .fixedSize(horizontal: false, vertical: true)
                                            if !ins.description.isEmpty {
                                                Text(ins.description).font(AppFont.supporting)
                                                    .foregroundStyle(Palette.textSecondary)
                                                    .multilineTextAlignment(.leading)
                                                    .lineLimit(2)
                                            }
                                        }
                                        Spacer(minLength: 0)
                                        Image(systemName: "chevron.right")
                                            .font(.system(size: 14, weight: .semibold))
                                            .foregroundStyle(Palette.textPrimary)
                                            .padding(.top, 3)
                                    }
                                    .padding(.horizontal, Space.rowPad).padding(.vertical, 14)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .contentShape(Rectangle())
                                }
                                .buttonStyle(DSRowPressStyle())
                                if i < min(aiInsights.count, 5) - 1 {
                                    DSRowDivider(inset: Space.rowPad + 20 + Space.md)
                                }
                            }
                        }
                    }
                }

                // MARK: Upcoming queue — eyebrow + filter pills, then dated timeline.
                VStack(alignment: .leading, spacing: Space.md) {
                    HStack(alignment: .center) {
                        DSEyebrow(text: "Coming up")
                        Spacer(minLength: Space.sm)
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
                                VStack(spacing: Space.sm) {
                                    Image(systemName: "calendar")
                                        .font(.system(size: 22, weight: .regular))
                                        .foregroundStyle(Palette.textSecondary)
                                        .padding(.bottom, Space.xs)
                                    Text("Nothing scheduled this week")
                                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                                        .multilineTextAlignment(.center)
                                    Text("Queue a ready clip and it shows up here with its posting time.")
                                        .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                                        .multilineTextAlignment(.center)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                .frame(maxWidth: .infinity)
                                GhostButton(title: "Schedule a clip", systemImage: "calendar", fullWidth: false) {
                                    sheet = .schedule(day: Calendar.current.startOfDay(for: Date()), clipId: nil)
                                }
                                .accessibilityIdentifier("performance.addClip")
                            }
                            .padding(.vertical, Space.sm)
                            .frame(maxWidth: .infinity)
                            .dsCard(.outline, radius: Radius.card)
                        } else {
                            VStack(alignment: .leading, spacing: Space.xl) {
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
                }

                // MARK: 7/30/90-day metrics (Stats pattern)
                InsightsSection()
            }
            .screenPadding().padding(.top, Space.sm).padding(.bottom, MarqueTabBar.clearance + Space.xl)
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
        .onChange(of: router.pendingScheduleClipId) { _, _ in consumePendingSchedule() }
    }

    private func heroStat(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(AppFont.stat).tracking(-0.3)
                .foregroundStyle(Palette.onNight)
                .lineLimit(1).minimumScaleFactor(0.6)
            Text(label).font(AppFont.caption).foregroundStyle(Palette.onNightSecondary)
                .lineLimit(1).minimumScaleFactor(0.85)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
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
    @State private var platform = 0   // 0 all · 1 instagram · 2 tiktok
    @State private var period = 1     // 0 = 7d · 1 = 30d · 2 = 90d

    private let periodDays = [7, 30, 90]
    private let periodLabels = ["7 days", "30 days", "90 days"]

    // Store-cached per window (AppStore.refreshPerformance): a tab revisit or a
    // period flip inside the staleness window paints from cache, no refetch.
    private var summary: BackendClient.PerformanceSummary? { store.perfSummaries[periodDays[period]] }
    private var loading: Bool { store.perfLoading }
    private var loaded: Bool { store.perfLoadedOnce }

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
                DSEyebrow(text: "Performance")
                Spacer()
                if loading { ProgressView().controlSize(.small).tint(Palette.textSecondary) }
            }

            // Journey filter-pill row: the window as pills, the platform as a "Days ▾"-style
            // outline dropdown pill.
            HStack(spacing: Space.sm) {
                PerformanceView.compactToggleView(options: ["7d", "30d", "90d"], index: $period)
                    .accessibilityIdentifier("performance.periodToggle")
                    .onChange(of: period) { _, _ in
                        Task { await store.refreshPerformance(days: periodDays[period]) }
                    }
                Spacer(minLength: 0)
                Menu {
                    Picker("Platform", selection: $platform) {
                        Text("All platforms").tag(0)
                        Text("Instagram").tag(1)
                        Text("TikTok").tag(2)
                    }
                } label: {
                    HStack(spacing: 6) {
                        Text(platform == 0 ? "All" : platform == 1 ? "Instagram" : "TikTok")
                            .font(AppFont.supporting.weight(.semibold))
                            .foregroundStyle(Palette.textPrimary)
                            .lineLimit(1).minimumScaleFactor(0.85)
                        Image(systemName: "chevron.down")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Palette.textPrimary)
                    }
                    .padding(.horizontal, 14).frame(height: 36)
                    .background(Capsule().fill(Palette.surface))
                    .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                    .contentShape(Capsule())
                }
                .accessibilityIdentifier("performance.platformToggle")
            }

            // Stat tiles (Stats pattern, one row of three) — real numbers only. I-3: never
            // show fabricated totals when the series is placeholder (no_data).
            HStack(spacing: Space.sm) {
                statTile(hasRealData ? compactNumber(views(summary!)) : "", "Views")
                statTile(hasRealData ? compactNumber(likes(summary!)) : "", "Likes")
                statTile(hasRealData ? "+\(follows(summary!))" : "", "Follows")
            }

            // I-3: interactive, dated graph — only for real data (a fabricated series is as
            // dishonest as fabricated tiles).
            if let s = summary, hasRealData, platform == 0, s.daily.contains(where: { $0.views > 0 }) {
                InteractiveSparkline(points: s.daily, windowDays: s.days)
                    .dsCard(.surface, radius: Radius.tile, padding: Space.md)
            }

            if loaded, !hasRealData {
                // Honest, quiet note — not a locked feature.
                Text("No posts in this window yet. Publish a clip and your views, likes, and follows show up here.")
                    .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                    .lineSpacing(3).fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, Space.xs)
            }

            // Coaching read-out (only when the loop has something real to say).
            if hasRealData, !store.coaching.isEmpty {
                VStack(alignment: .leading, spacing: Space.sm) {
                    HStack {
                        DSEyebrow(text: "Your coach")
                        Spacer()
                    }
                    Text(store.coaching)
                        .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                        .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .dsCard(.surface, radius: Radius.group)
                .padding(.top, Space.sm)
            }
        }
        .task {
            await store.refreshPerformance(days: periodDays[period])
        }
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
        DSStatTile(value: value, label: label)
    }
}
