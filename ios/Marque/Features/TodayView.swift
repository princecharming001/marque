import SwiftUI

struct TodayView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var showSettings = false
    @State private var showProfile = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                topBar
                Text(greeting)
                    .font(Typeface.display(38)).tracking(-0.8)
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 2)
                momentum
                upcomingStrip
                command
                quietRows
            }
            .padding(.horizontal, 22)
            .padding(.top, Space.lg)
            .padding(.bottom, 110)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationTitle("Today")
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.loadTrends(); await store.loadInsights(); await store.loadRecommendations() }
        .refreshable { await store.loadTrends(); await store.loadInsights(); await store.loadRecommendations() }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $showProfile) { BrandProfileView() }
    }

    // MARK: Top bar — date kicker + streak + profile/settings

    private var dateKicker: String {
        Date().formatted(.dateTime.weekday(.wide).month(.abbreviated).day()).uppercased()
    }

    private var greeting: String {
        let hour = Calendar.current.component(.hour, from: Date())
        let part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"
        if let h = store.brand.connectedAccounts.first?.handle, !h.isEmpty { return "\(part), @\(h)" }
        return part
    }

    private var topBar: some View {
        HStack(alignment: .center) {
            Text(dateKicker)
                .font(AppFont.micro).tracking(Track.label)
                .foregroundStyle(Palette.textTertiary)
            Spacer()
            if store.streak > 0 { StreakGlyph(count: store.streak).padding(.trailing, Space.xs) }
            Button { showProfile = true } label: {
                Image(systemName: "person.crop.circle").font(.system(size: 22)).foregroundStyle(Palette.textSecondary)
            }
            .accessibilityIdentifier("today.profile")
            Button { showSettings = true } label: {
                Image(systemName: "gearshape").font(.system(size: 19)).foregroundStyle(Palette.textSecondary)
            }
            .accessibilityIdentifier("today.settings")
            .padding(.leading, Space.md)
        }
    }

    // MARK: Momentum / growth insights (the numeral-led hero)

    private var hasMomentum: Bool { store.hasRealMetrics }

    private var momentum: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack {
                SectionLabel(text: "This week", accent: Palette.accent)
                if store.weekPostedCount > 0 {
                    Text("· \(store.weekPostedCount) posted")
                        .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                }
                Spacer()
                if hasMomentum && store.weekFollows > 0 {
                    HStack(spacing: 3) {
                        Image(systemName: "arrow.up.right").font(.system(size: 10, weight: .bold))
                        Text("+\(store.weekFollows) follows").font(AppFont.micro).tracking(0.3)
                    }
                    .foregroundStyle(Palette.positive)
                }
            }

            if hasMomentum {
                HStack(alignment: .lastTextBaseline, spacing: Space.sm) {
                    Text(compactNumber(store.weekViews))
                        .font(AppFont.heroNumeral).tracking(Track.hero)
                        .foregroundStyle(Palette.textPrimary)
                    Text("views this week").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        .padding(.bottom, 6)
                }
                Sparkline(values: store.weekTrend).frame(height: 42)
                if !store.coaching.isEmpty {
                    Text(store.coaching)
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text("Post your first clip")
                    .font(AppFont.headline)
                    .foregroundStyle(Palette.textPrimary)
                Text("Once your posts start collecting views, your real reach and follower growth show up here every week.")
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .marqueCard(radius: 22)
    }

    // MARK: Upcoming posts strip — next 3 scheduled posts

    @ViewBuilder private var upcomingStrip: some View {
        let upcoming = store.schedule
            .filter { !$0.posted && $0.date >= Date() }
            .sorted { $0.date < $1.date }
            .prefix(3)
        if !upcoming.isEmpty {
            VStack(alignment: .leading, spacing: Space.sm) {
                SectionLabel(text: "Coming up", accent: Palette.accent)
                HStack(spacing: Space.sm) {
                    ForEach(Array(upcoming)) { post in
                        Button { router.selectedTab = .plan } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(post.date.formatted(.dateTime.weekday(.abbreviated)))
                                    .font(AppFont.micro).tracking(Track.label)
                                    .foregroundStyle(Palette.textTertiary)
                                Text(post.caption)
                                    .font(AppFont.caption).foregroundStyle(Palette.textPrimary)
                                    .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                                Text(post.date.formatted(.dateTime.hour().minute()))
                                    .font(AppFont.micro).foregroundStyle(Palette.textSecondary)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(Space.sm)
                            .background(Palette.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
                            .overlay(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous)
                                .strokeBorder(Palette.hairline, lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    // MARK: Command card — directive + queued ring + next action

    private var command: some View {
        let d = store.todayDirective
        return VStack(alignment: .leading, spacing: Space.lg) {
            HStack(alignment: .center, spacing: Space.lg) {
                ProgressRing(value: store.weekProgress,
                             centerTop: "\(store.weekDone)/\(store.weekGoal)",
                             centerBottom: "queued", size: 104)
                VStack(alignment: .leading, spacing: Space.sm) {
                    SectionLabel(text: "Your move", accent: Palette.accent)
                    Text(d.title)
                        .font(AppFont.serifL)
                        .foregroundStyle(Palette.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(d.subtitle)
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            PrimaryButton(title: ctaTitle, systemImage: ctaIcon) { ctaAction() }
                .accessibilityIdentifier("today.cta")
        }
        .marqueCard(padding: 20, radius: 24)
    }

    private var ctaTitle: String {
        if store.clips.contains(where: { $0.status == .ready }) { return "Schedule this week" }
        if store.scripts.contains(where: { !$0.approved }) { return "Record your batch" }
        return "Open Studio"
    }
    private var ctaIcon: String {
        store.clips.contains(where: { $0.status == .ready }) ? "calendar" : "square.grid.2x2"
    }
    private func ctaAction() {
        if store.clips.contains(where: { $0.status == .ready }) { router.selectedTab = .plan }
        else { router.showCreate = true }
    }

    // MARK: Quiet-row stack — next up, learning, trend in one hairline-separated card

    @ViewBuilder private var quietRows: some View {
        let next = store.schedule.sorted { $0.date < $1.date }.first { !$0.posted }
        let trend = store.trends.first
        let learning = store.postsLearned > 0
        if next != nil || learning || trend != nil {
            VStack(spacing: 0) {
                if let next {
                    nextUpRow(next)
                    if learning || trend != nil { MarqueHairline() }
                }
                if learning {
                    learningRow
                    if trend != nil { MarqueHairline() }
                }
                if let trend { trendRow(trend) }
            }
            .marqueCard(padding: Space.md, radius: 20)
        }
    }

    private func nextUpRow(_ post: ScheduledPost) -> some View {
        Button { router.selectedTab = .plan } label: {
            HStack(spacing: Space.md) {
                Image(systemName: "calendar.badge.clock").font(.system(size: 16)).foregroundStyle(Palette.accent).frame(width: 22)
                VStack(alignment: .leading, spacing: 3) {
                    SectionLabel(text: "Next up")
                    Text(post.caption).font(AppFont.callout).foregroundStyle(Palette.textPrimary).lineLimit(1)
                }
                Spacer()
                Text(post.date.formatted(.dateTime.weekday().hour().minute()))
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                Image(systemName: "chevron.right").font(.system(size: 11)).foregroundStyle(Palette.textTertiary)
            }
            .padding(.vertical, Space.sm).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("today.nextUp")
    }

    private var learningRow: some View {
        HStack(spacing: Space.md) {
            Image(systemName: "chart.line.uptrend.xyaxis").font(.system(size: 15)).foregroundStyle(Palette.accent).frame(width: 22)
            VStack(alignment: .leading, spacing: 4) {
                Text(learningLine).font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                if store.postsLearned < 15 {
                    ProgressView(value: store.learningProgress).tint(Palette.accent)
                }
            }
        }
        .padding(.vertical, Space.sm)
    }
    private var learningLine: String {
        if store.postsLearned >= 15, let f = store.learnedInsights["winning_formula"] as? String { return f }
        return "Marque has learned from \(store.postsLearned) post\(store.postsLearned == 1 ? "" : "s") — recommendations sharpen at 15"
    }

    private func trendRow(_ t: TrendItem) -> some View {
        Button { router.selectedTab = .coach } label: {
            HStack(spacing: Space.md) {
                Image(systemName: "wave.3.right").font(.system(size: 15)).foregroundStyle(Palette.accent).frame(width: 22)
                VStack(alignment: .leading, spacing: 3) {
                    SectionLabel(text: "Trending")
                    Text(t.title).font(AppFont.callout).foregroundStyle(Palette.textPrimary).lineLimit(1)
                }
                Spacer()
                Image(systemName: "chevron.right").font(.system(size: 11)).foregroundStyle(Palette.textTertiary)
            }
            .padding(.vertical, Space.sm).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}


struct LearningMeterCard: View {
    let postsLearned: Int
    let learningProgress: Double
    let winningFormula: String?

    var body: some View {
        if postsLearned == 0 {
            EmptyView()
        } else if postsLearned >= 15, let formula = winningFormula {
            HStack(spacing: Space.sm) {
                Image(systemName: "lightbulb.fill").foregroundStyle(Palette.accent)
                Text(formula).font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .marqueCard(padding: Space.md)
        } else {
            VStack(alignment: .leading, spacing: Space.xs) {
                HStack(spacing: Space.sm) {
                    Image(systemName: "chart.line.uptrend.xyaxis").foregroundStyle(Palette.accent)
                    Text("Marque has learned from \(postsLearned) post\(postsLearned == 1 ? "" : "s") — recommendations sharpen at 15")
                        .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                ProgressView(value: learningProgress)
                    .tint(Palette.accent)
            }
            .marqueCard(padding: Space.md)
        }
    }
}
