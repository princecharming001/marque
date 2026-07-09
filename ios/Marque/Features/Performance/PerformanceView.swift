import SwiftUI

// Performance tab: the upcoming queue (next 7 days) on top, 30-day
// Instagram/TikTok insights below.
struct PerformanceView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var sheet: CalSheet?
    @State private var mode: CalMode = .week

    private var week: [Date] {
        let cal = Calendar.current
        let start = cal.startOfDay(for: Date())
        return (0..<7).compactMap { cal.date(byAdding: .day, value: $0, to: start) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.lg) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("QUEUE + INSIGHTS").font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
                    ScreenTitle(text: "Performance")
                }

                // MARK: Upcoming queue
                SectionLabel(text: "Coming up", accent: Palette.accent)
                Picker("View", selection: $mode) {
                    ForEach(CalMode.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("calendar.modeToggle")

                if mode == .week {
                    // Seven identical "Nothing scheduled" cards read as a wall of holes —
                    // when the whole week is empty, say it once with a way in instead.
                    if !week.contains(where: { day in
                        store.schedule.contains { Calendar.current.isDate($0.date, inSameDayAs: day) }
                    }) {
                        VStack(spacing: Space.md) {
                            EmptyStateView(icon: "calendar.badge.plus",
                                           title: "Nothing scheduled this week",
                                           message: "Queue a ready clip and it shows up here with its posting time.")
                            Button {
                                sheet = .schedule(day: Calendar.current.startOfDay(for: Date()), clipId: nil)
                            } label: {
                                Text("Schedule a clip").font(AppFont.headline)
                                    .foregroundStyle(Palette.textPrimary)
                                    .frame(maxWidth: .infinity).frame(height: 54)
                                    .background(Palette.surfaceRaised)
                                    .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                                    .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                                        .strokeBorder(Palette.hairline, lineWidth: 1))
                            }
                            .buttonStyle(PressableStyle(dim: 0.7))
                            .accessibilityIdentifier("performance.addClip")
                        }
                    } else {
                        VStack(spacing: 12) {
                            ForEach(Array(week.enumerated()), id: \.element) { i, day in
                                DayRow(day: day,
                                       posts: store.schedule
                                        .filter { Calendar.current.isDate($0.date, inSameDayAs: day) }
                                        .sorted { $0.date < $1.date },
                                       hasReady: store.clips.contains { $0.status == .ready },
                                       clipFor: { id in store.clips.first { $0.id == id } },
                                       onAdd: { sheet = .schedule(day: day, clipId: nil) },
                                       onTapPost: { sheet = .edit($0) },
                                       onDuplicate: { store.duplicatePost($0) })
                                    .staggerReveal(i)
                            }
                        }
                    }
                } else {
                    MonthGrid(schedule: store.schedule) { day in sheet = .schedule(day: day, clipId: nil) }
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
    @State private var loaded = false

    /// The learning loop needs real posted metrics before it has anything to say —
    /// gate on posts_learned (populated once /v1/metrics/ingest has fired at least
    /// once), the same signal the backend uses for learning_progress.
    // C-05: honest empty state whenever the data is placeholder — not just when postsLearned==0.
    // The backend flags a seeded/placeholder series with no_data:true (or mode:"mock").
    private var hasLearningData: Bool {
        guard store.postsLearned > 0 else { return false }
        guard let s = summary else { return true }             // still loading → don't flash the teaser
        return !(s.no_data ?? false) && s.mode != "mock"
    }
    private let learningTarget = 15   // mirrors backend main.py get_learned_insights target

    var body: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            SectionLabel(text: "Last 30 days", accent: Palette.accent)

            if loaded, !hasLearningData {
                learningTeaser
            } else {
                Picker("Platform", selection: $platform) {
                    Text("All").tag(0); Text("Instagram").tag(1); Text("TikTok").tag(2)
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("performance.platformToggle")

                // Coach persona picker — controls the tone of performance coaching feedback
                VStack(alignment: .leading, spacing: Space.sm) {
                    Text("COACH").font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
                    HStack(spacing: Space.sm) {
                        ForEach(ChatPersona.allCases) { persona in
                            Button { store.coachPersona = persona } label: {
                                VStack(spacing: 4) {
                                    Image(systemName: persona.icon)
                                        .font(.system(size: 14, weight: .semibold))
                                    Text(persona.label)
                                        .font(AppFont.micro).tracking(0.3)
                                }
                                .frame(maxWidth: .infinity)
                                .padding(Space.md)
                                .background(store.coachPersona == persona ? Color(hex: persona.glow) : Palette.surfaceRaised)
                                .foregroundStyle(store.coachPersona == persona ? Color.white : Palette.textPrimary)
                                .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                                .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                                    .strokeBorder(store.coachPersona == persona ? Color.clear : Palette.hairline, lineWidth: 1))
                            }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("performance.coach.\(persona.rawValue)")
                        }
                    }
                }

                if let s = summary {
                    HStack(spacing: Space.md) {
                        statTile(compactNumber(views(s)), "Views")
                        statTile(compactNumber(likes(s)), "Likes")
                        statTile("+\(follows(s))", "Follows")
                    }
                    if platform == 0, !s.daily.isEmpty {
                        Sparkline(values: normalized(s.daily.map { Double($0.views) }))
                            .frame(height: 44)
                            .padding(.vertical, Space.xs)
                    }
                    if !store.coaching.isEmpty {
                        Text(store.coaching)
                            .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                            .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
                    }
                } else {
                    HStack { Spacer(); ProgressView().tint(Palette.accent); Spacer() }
                        .padding(.vertical, Space.lg)
                }
            }
        }
        .task {
            summary = await store.backend.fetchPerformanceSummary(days: 30)
            store.learnedBestHour = summary?.best_hour          // C-12
            await store.loadInsights()
            loaded = true
        }
    }

    /// Pre-data locked state: markets what's coming (a real, personalized winning
    /// formula) instead of showing empty tiles, and gives the tab a reason to pull
    /// creators back before they've posted anything.
    private var learningTeaser: some View {
        VStack(spacing: Space.md) {
            Image(systemName: "chart.line.uptrend.xyaxis")
                .font(.system(size: 26)).foregroundStyle(Palette.accent)
            Text("Unlock your winning formula").font(AppFont.title).foregroundStyle(Palette.textPrimary)
            Text("Post \(learningTarget) clips and Yunicorn learns what actually works for you — the hooks, formats, and topics ranked by real results.")
                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            VStack(spacing: Space.xs) {
                ProgressView(value: Double(store.postsLearned), total: Double(learningTarget))
                    .tint(Palette.accent)
                Text("\(store.postsLearned) of \(learningTarget) posts")
                    .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
            }
            .padding(.horizontal, Space.xl)

            PrimaryButton(title: "Film your next clip") { router.showFilm = true }
                .accessibilityIdentifier("performance.learningTeaserFilm")
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Space.lg)
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
            Text(value).font(Typeface.display(22, .semibold)).foregroundStyle(Palette.textPrimary)
            Text(label.uppercased()).font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(Space.md)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }
}
