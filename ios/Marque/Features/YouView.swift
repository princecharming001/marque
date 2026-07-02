import SwiftUI

struct YouView: View {
    @Environment(AppStore.self) private var store
    @State private var showSettings = false
    @State private var showBrandEditor = false
    @State private var showVoiceEditor = false
    @State private var showPillarsEditor = false
    @State private var showStyleEditor = false
    @State private var showPaywall = false
    @State private var showCoach = false

    private var account: ConnectedAccount? { store.brand.connectedAccounts.first }
    private var displayName: String { account?.displayName ?? account?.handle ?? "Creator" }
    private var handle: String { account.map { "@\($0.handle)" } ?? "" }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                // Avatar hero
                VStack(spacing: Space.md) {
                    avatarHero
                    VStack(spacing: 4) {
                        Text(displayName)
                            .font(Typeface.display(24, .semibold)).tracking(-0.5)
                            .foregroundStyle(Palette.textPrimary)
                        if !handle.isEmpty {
                            Text(handle).font(AppFont.body).foregroundStyle(Palette.textSecondary)
                        }
                        if !store.brand.niche.isEmpty {
                            Text(store.brand.niche)
                                .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        }
                    }
                    YouStatStrip(store: store)
                }
                .padding(.vertical, Space.xl)
                .padding(.horizontal, Space.screenH)
                .staggerReveal(0)

                MarqueHairline()

                // Brand group
                VStack(alignment: .leading, spacing: 0) {
                    sectionHeader("Brand")
                    youRow(icon: "pencil", label: "Brand identity") { showBrandEditor = true }
                    MarqueHairline().padding(.leading, 56)
                    youRow(icon: "waveform", label: "Voice & tone") { showVoiceEditor = true }
                    MarqueHairline().padding(.leading, 56)
                    youRow(icon: "square.grid.2x2", label: "Content pillars") { showPillarsEditor = true }
                    MarqueHairline().padding(.leading, 56)
                    youRow(icon: "play.rectangle", label: "Video styles") { showStyleEditor = true }
                    if let target = store.brand.weeklyTarget {
                        MarqueHairline().padding(.leading, 56)
                        youRow(icon: "calendar.badge.clock", label: "Weekly pace: \(target) posts/week") { showSettings = true }
                    }
                }
                .padding(.horizontal, Space.screenH)
                .staggerReveal(1)

                MarqueHairline()

                // Accounts group
                VStack(alignment: .leading, spacing: 0) {
                    sectionHeader("Accounts")
                    ConnectAccountsView()
                        .padding(.horizontal, Space.screenH)
                }
                .staggerReveal(2)

                MarqueHairline()

                // Performance + Pro
                VStack(alignment: .leading, spacing: 0) {
                    sectionHeader("Analytics")
                    youRow(icon: "chart.bar", label: "Performance") { showCoach = true }
                    MarqueHairline().padding(.leading, 56)
                    youRow(icon: "star.fill", label: "Marque Pro", accent: true) { showPaywall = true }
                }
                .padding(.horizontal, Space.screenH)
                .staggerReveal(3)

                Spacer().frame(height: 120)
            }
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationTitle("You")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showSettings = true } label: {
                    Image(systemName: "gearshape").foregroundStyle(Palette.textSecondary)
                }
                .accessibilityIdentifier("you.settings")
            }
        }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $showBrandEditor) { BrandEditorSheet(store: store) }
        .sheet(isPresented: $showVoiceEditor) { VoiceEditorSheet(store: store) }
        .sheet(isPresented: $showPillarsEditor) { PillarsEditorSheet(store: store) }
        .sheet(isPresented: $showStyleEditor) { StyleEditorSheet(store: store) }
        .sheet(isPresented: $showPaywall) { PaywallView() }
        .sheet(isPresented: $showCoach) { NavigationStack { CoachView() } }
    }

    private var avatarHero: some View {
        ZStack {
            Circle()
                .fill(Palette.accent.opacity(0.12))
                .frame(width: 88, height: 88)
            if let url = account?.avatarUrl, !url.isEmpty, let u = URL(string: url) {
                AsyncImage(url: u) { img in img.resizable().scaledToFill() } placeholder: { monogram }
                    .frame(width: 88, height: 88)
                    .clipShape(Circle())
            } else {
                monogram
            }
        }
        .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private var monogram: some View {
        Text(String(displayName.prefix(1)).uppercased())
            .font(Typeface.display(32, .semibold))
            .foregroundStyle(Palette.accent)
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title.uppercased())
            .font(AppFont.micro).tracking(Track.label)
            .foregroundStyle(Palette.textTertiary)
            .padding(.top, Space.lg).padding(.bottom, Space.sm)
    }

    private func youRow(icon: String, label: String, accent: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: Space.md) {
                Image(systemName: icon)
                    .font(.system(size: 15))
                    .foregroundStyle(accent ? Palette.gold : Palette.accent)
                    .frame(width: 32, height: 32)
                    .background(RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill((accent ? Palette.gold : Palette.accent).opacity(0.10)))
                Text(label)
                    .font(AppFont.bodyL)
                    .foregroundStyle(accent ? Palette.gold : Palette.textPrimary)
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.textTertiary)
            }
            .padding(.vertical, Space.sm)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

struct YouStatStrip: View {
    let store: AppStore
    var body: some View {
        HStack(spacing: 0) {
            statCell("\(store.streak)", "Sessions")
            Divider().frame(height: 28)
            statCell("\(store.schedule.count)", "Scheduled")
            Divider().frame(height: 28)
            statCell("\(store.weekDone)/\(store.weekGoal)", "This week")
        }
        .padding(.vertical, Space.sm)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private func statCell(_ value: String, _ label: String) -> some View {
        VStack(spacing: 2) {
            Text(value).font(AppFont.displayM).foregroundStyle(Palette.textPrimary)
            Text(label).font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Space.xs)
    }
}

// MARK: - Inline brand editor sheets

struct BrandEditorSheet: View {
    let store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var niche: String
    @State private var whatYouDo: String
    @State private var audience: String
    @State private var knownFor: String

    init(store: AppStore) {
        self.store = store
        _niche = State(initialValue: store.brand.niche)
        _whatYouDo = State(initialValue: store.brand.whatYouDo)
        _audience = State(initialValue: store.brand.audience)
        _knownFor = State(initialValue: store.brand.knownFor)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.lg) {
                    fieldGroup("Your niche", placeholder: "e.g. fitness, personal finance, cooking", text: $niche)
                    fieldGroup("What you do", placeholder: "Your day-to-day work", text: $whatYouDo)
                    fieldGroup("Who you serve", placeholder: "Your target audience", text: $audience)
                    fieldGroup("Known for", placeholder: "What you want to be remembered for", text: $knownFor)
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Brand identity")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .topBarTrailing) { Button("Save") { save() } }
            }
        }
    }

    private func fieldGroup(_ label: String, placeholder: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            Text(label).font(AppFont.caption).tracking(Track.label).foregroundStyle(Palette.textTertiary)
            TextField(placeholder, text: text).marqueField()
        }
    }

    private func save() {
        var b = store.brand
        b.niche = niche; b.whatYouDo = whatYouDo; b.audience = audience; b.knownFor = knownFor
        store.brand = b; store.save(); dismiss()
    }
}

struct VoiceEditorSheet: View {
    let store: AppStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            @Bindable var store = store
            ScrollView {
                VStack(spacing: Space.lg) {
                    VStack(spacing: Space.lg) {
                        voiceRow("Funny", "Serious", value: $store.brand.voice.funnyToSerious)
                        MarqueHairline()
                        voiceRow("Polished", "Raw", value: $store.brand.voice.polishedToRaw)
                        MarqueHairline()
                        voiceRow("Teacher", "Peer", value: $store.brand.voice.teacherToPeer)
                    }
                    .marqueCard()
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Voice & tone")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { store.save(); dismiss() }
                }
            }
        }
    }

    private func voiceRow(_ l: String, _ r: String, value: Binding<Double>) -> some View {
        VStack(spacing: Space.xs) {
            HStack {
                Text(l).font(AppFont.callout).foregroundStyle(value.wrappedValue < 0.4 ? Palette.accent : Palette.textTertiary)
                Spacer()
                Text(r).font(AppFont.callout).foregroundStyle(value.wrappedValue > 0.6 ? Palette.accent : Palette.textTertiary)
            }
            Slider(value: value).tint(Palette.accent)
        }
    }
}

struct PillarsEditorSheet: View {
    let store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var regenerating = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.md) {
                    ForEach(store.pillars) { p in
                        VStack(alignment: .leading, spacing: Space.sm) {
                            HStack {
                                Circle().fill(Color(hex: p.colorHex)).frame(width: 10, height: 10)
                                Text(p.name).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                            }
                            Text(p.angle.isEmpty ? p.summary : p.angle)
                                .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .marqueCard(padding: Space.md)
                    }
                    GhostButton(title: regenerating ? "Regenerating…" : "Refresh pillars", systemImage: "sparkles") {
                        regenerating = true
                        Task { await store.analyzePage(); regenerating = false }
                    }
                    .disabled(regenerating)
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Content pillars")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }
}

struct StyleEditorSheet: View {
    let store: AppStore
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            @Bindable var store = store
            ScrollView {
                VStack(alignment: .leading, spacing: Space.md) {
                    StyleSelectionView(selected: $store.brand.preferredStyles)
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Video styles")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) {
                Button("Done") { store.save(); dismiss() }
            }}
        }
    }
}
