import SwiftUI

struct TodayView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var showSettings = false
    @State private var showProfile = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.lg) {
                topBar
                momentum
                command

                if let next = store.schedule.sorted(by: { $0.date < $1.date }).first(where: { !$0.posted }) {
                    nextPostRow(next)
                }
                if let t = store.trends.first {
                    trendTeaser(t)
                }
            }
            .screenPadding()
            .padding(.vertical, Space.lg)
        }
        .background(Palette.surface.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.loadTrends(); await store.loadInsights() }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $showProfile) { BrandProfileView() }
    }

    // MARK: Top bar — date kicker + streak + profile/settings

    private var dateKicker: String {
        Date().formatted(.dateTime.weekday(.wide).month(.abbreviated).day()).uppercased()
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

    private var hasMomentum: Bool { store.activeClipCount > 0 }

    private var momentum: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack {
                SectionLabel(text: "This week", accent: Palette.accent)
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
                    Text("projected views").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
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
                    .font(AppFont.serifL).tracking(Track.title).textCase(.lowercase)
                    .foregroundStyle(Palette.textPrimary)
                Text("Once you schedule clips, your reach and follower growth show up here every week.")
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .marqueCard()
    }

    // MARK: Command card — directive + queued ring + next action

    private var command: some View {
        let d = store.todayDirective
        return VStack(alignment: .leading, spacing: Space.lg) {
            HStack(alignment: .center, spacing: Space.lg) {
                ProgressRing(value: store.weekProgress,
                             centerTop: "\(store.weekDone)/\(store.weekGoal)",
                             centerBottom: "queued", size: 104)
                VStack(alignment: .leading, spacing: Space.xs) {
                    SectionLabel(text: "Your move")
                    Text(d.title)
                        .font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(d.subtitle)
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            PrimaryButton(title: ctaTitle, systemImage: ctaIcon) { ctaAction() }
                .accessibilityIdentifier("today.cta")
        }
        .marqueCard()
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
        if store.clips.contains(where: { $0.status == .ready }) { router.selectedTab = .calendar }
        else { router.selectedTab = .studio }
    }

    // MARK: Next up + trend

    private func nextPostRow(_ post: ScheduledPost) -> some View {
        HStack(spacing: Space.md) {
            Image(systemName: "calendar.badge.clock").font(.system(size: 18)).foregroundStyle(Palette.accent)
            VStack(alignment: .leading, spacing: 2) {
                SectionLabel(text: "Next up")
                Text(post.caption).font(AppFont.body).foregroundStyle(Palette.textPrimary).lineLimit(1)
            }
            Spacer()
            Text(post.date.formatted(.dateTime.weekday().hour().minute()))
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
        }
        .marqueCard(padding: Space.md)
    }

    private func trendTeaser(_ t: TrendItem) -> some View {
        Button { router.selectedTab = .coach } label: {
            HStack(spacing: Space.sm) {
                Image(systemName: "wave.3.right").font(.system(size: 13)).foregroundStyle(Palette.accent)
                Text(t.title).font(AppFont.callout).foregroundStyle(Palette.textSecondary).lineLimit(1)
                Spacer()
                Image(systemName: "chevron.right").font(.system(size: 11)).foregroundStyle(Palette.textTertiary)
            }
            .padding(.horizontal, Space.md)
        }
        .buttonStyle(.plain)
    }
}
