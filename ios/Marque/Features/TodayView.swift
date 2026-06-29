import SwiftUI

struct TodayView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var showSettings = false
    @State private var showProfile = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.lg) {
                header
                commandCenter

                if let t = store.trends.first {
                    Button { router.selectedTab = .coach } label: {
                        HStack(spacing: Space.sm) {
                            Image(systemName: "wave.3.right").font(.system(size: 13)).foregroundStyle(Palette.accent)
                            Text(t.title).font(AppFont.callout).foregroundStyle(Palette.textSecondary).lineLimit(1)
                            Spacer()
                            Image(systemName: "chevron.right").font(.system(size: 11)).foregroundStyle(Palette.textTertiary)
                        }
                    }.buttonStyle(.plain)
                }

                if let next = store.schedule.sorted(by: { $0.date < $1.date }).first(where: { !$0.posted }) {
                    nextPostRow(next)
                }
            }
            .screenPadding()
            .padding(.vertical, Space.lg)
        }
        .background(Palette.surface.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.loadTrends() }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $showProfile) { BrandProfileView() }
    }

    private var header: some View {
        HStack {
            Text("Today").font(AppFont.displayL).foregroundStyle(Palette.textPrimary)
            Spacer()
            if store.streak > 0 { StreakGlyph(count: store.streak).padding(.trailing, Space.xs) }
            Button { showProfile = true } label: {
                Image(systemName: "person.crop.circle").font(.system(size: 20)).foregroundStyle(Palette.textSecondary)
            }
            .accessibilityIdentifier("today.profile")
            Button { showSettings = true } label: {
                Image(systemName: "gearshape").font(.system(size: 19)).foregroundStyle(Palette.textSecondary)
            }
            .accessibilityIdentifier("today.settings")
            .padding(.leading, Space.md)
        }
    }

    private var commandCenter: some View {
        let d = store.todayDirective
        return VStack(alignment: .leading, spacing: Space.lg) {
            HStack(alignment: .center, spacing: Space.lg) {
                ProgressRing(value: store.weekProgress,
                             centerTop: "\(store.weekDone)/\(store.weekGoal)",
                             centerBottom: "queued", size: 104)
                VStack(alignment: .leading, spacing: Space.xs) {
                    SectionTitle(text: "This week")
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

    private func nextPostRow(_ post: ScheduledPost) -> some View {
        HStack(spacing: Space.md) {
            VStack(alignment: .leading, spacing: 2) {
                SectionTitle(text: "Next up")
                Text(post.caption).font(AppFont.body).foregroundStyle(Palette.textPrimary).lineLimit(1)
            }
            Spacer()
            Text(post.date.formatted(.dateTime.weekday().hour().minute()))
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
        }
        .marqueCard(padding: Space.md)
    }
}
