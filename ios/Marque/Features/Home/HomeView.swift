import SwiftUI

// Home — the daily driver. Centerpiece is the voice bubble (talk to Marque);
// below it the day's picks. Phase 7 swaps the picks strip for the full feed
// (scripts + influencer reels + trends).
struct HomeView: View {
    @Environment(AppStore.self) private var store
    @Environment(AppRouter.self) private var router
    @State private var showVoice = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                topBar
                greetingBlock.staggerReveal(0)
                VoiceBubble(memoryLine: memoryLine) { showVoice = true }
                    .staggerReveal(1)
                picksSection.staggerReveal(2)
                quietRows.staggerReveal(3)
            }
            .screenPadding()
            .padding(.top, Space.lg)
            .padding(.bottom, 140)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
        .sheet(isPresented: $showVoice) { VoiceSessionView() }
        .navigationDestination(for: String.self) { dest in
            if dest == "profile" { ProfileView() }
        }
    }

    // MARK: Top bar — date + streak + profile avatar

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
            NavigationLink(value: "profile") {
                avatarButton
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("home.profile")
        }
    }

    private var avatarButton: some View {
        ZStack {
            Circle().fill(Palette.accent.opacity(0.12)).frame(width: 34, height: 34)
            if let url = store.brand.connectedAccounts.first?.avatarUrl, !url.isEmpty, let u = URL(string: url) {
                AsyncImage(url: u) { img in img.resizable().scaledToFill() } placeholder: { initial }
                    .frame(width: 34, height: 34).clipShape(Circle())
            } else {
                initial
            }
        }
        .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private var initial: some View {
        Text(String((store.brand.connectedAccounts.first?.handle ?? store.brand.niche).prefix(1)).uppercased())
            .font(Typeface.display(15, .semibold)).foregroundStyle(Palette.accent)
    }

    private var greeting: String {
        let hour = Calendar.current.component(.hour, from: Date())
        let part = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"
        if let h = store.brand.connectedAccounts.first?.handle, !h.isEmpty { return "\(part), @\(h)" }
        return part
    }

    private var greetingBlock: some View {
        Text(greeting)
            .font(Typeface.display(34)).tracking(-0.8)
            .foregroundStyle(Palette.textPrimary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var memoryLine: String {
        if !store.memory.angle.isEmpty { return "Working on: \(store.memory.angle)" }
        if let idea = store.memory.ideas.last { return "Last idea: \(idea)" }
        return "Tell me what's on your mind this morning."
    }

    // MARK: Today's picks — Phase 7 replaces with the full mixed feed

    @ViewBuilder private var picksSection: some View {
        let picks = store.scripts.prefix(6)
        if !picks.isEmpty {
            VStack(alignment: .leading, spacing: Space.md) {
                SectionLabel(text: "Today's picks", accent: Palette.accent)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: Space.md) {
                        ForEach(Array(picks)) { s in
                            pickCard(s)
                        }
                    }
                    .padding(.horizontal, Space.screenH)
                }
                .padding(.horizontal, -Space.screenH)
            }
        }
    }

    private func pickCard(_ s: Script) -> some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack {
                FormatTag(formatId: s.formatId)
                Spacer()
                ScoreBadge(score: s.predictedScore).scaleEffect(0.85)
            }
            Text(s.title.isEmpty ? s.hook.text : s.title)
                .font(AppFont.serifM).foregroundStyle(Palette.textPrimary)
                .lineLimit(2).fixedSize(horizontal: false, vertical: true)
            Text("\u{201C}\(s.hook.text)\u{201D}")
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                .lineLimit(2)
            Spacer(minLength: 0)
            HStack(spacing: Space.sm) {
                Button {
                    store.readyScript(s, source: .daily)
                    router.pendingFilmScriptId = s.id
                    router.showFilm = true
                } label: {
                    Text("Film this").font(AppFont.callout).foregroundStyle(Palette.onInk)
                        .padding(.horizontal, Space.md).frame(height: 32)
                        .background(Palette.ink).clipShape(Capsule())
                }
                .buttonStyle(.plain)
                Button {
                    store.readyScript(s, source: .daily)
                } label: {
                    Image(systemName: store.readiedScripts.contains { $0.script.id == s.id }
                          ? "bookmark.fill" : "bookmark")
                        .font(.system(size: 14))
                        .foregroundStyle(Palette.accent)
                        .frame(width: 32, height: 32)
                        .background(Palette.accent.opacity(0.08)).clipShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("feed.save")
            }
        }
        .padding(Space.lg)
        .frame(width: 260, height: 190, alignment: .topLeading)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
        .shadow(color: Palette.shadowWarm.opacity(0.06), radius: 12, x: 0, y: 6)
    }

    // MARK: Quiet rows

    private var quietRows: some View {
        VStack(spacing: 0) {
            quietRow(icon: "chart.bar", title: "Performance", subtitle: "Queue + last 30 days") {
                router.selectedTab = .performance
            }
            MarqueHairline()
            quietRow(icon: "rectangle.stack", title: "Library", subtitle: "Clips + media") {
                router.selectedTab = .library
            }
        }
        .marqueCard(padding: 0)
    }

    private func quietRow(icon: String, title: String, subtitle: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: Space.md) {
                Image(systemName: icon).font(.system(size: 16)).foregroundStyle(Palette.accent)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                    Text(subtitle).font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                }
                Spacer()
                Image(systemName: "chevron.right").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
            }
            .padding(Space.lg)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
