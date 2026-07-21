import SwiftUI

// Profile — pushed from Home's top-right avatar (not a tab). Phase 10 completes:
// AI brand summary card (traits + refresh), pillars glance, creators-to-watch,
// and the quiet "what Marque remembers" memory glance.
struct ProfileView: View {
    @Environment(AppStore.self) private var store
    @State private var showSettings = false
    @State private var showBrandEditor = false
    @State private var showVoiceEditor = false
    @State private var showPillarsEditor = false
    @State private var isRefreshingSummary = false

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
                }
                .padding(.vertical, Space.xl)
                .padding(.horizontal, Space.screenH)
                .staggerReveal(0)

                // The Marque Path — the creator's current rank + progress to the next.
                rankCard
                    .padding(.horizontal, Space.screenH)
                    .padding(.bottom, Space.lg)
                    .staggerReveal(1)

                // Brand summary — the AI-written card (skeleton until the first fetch lands)
                brandSummaryCard
                    .padding(.horizontal, Space.screenH)
                    .padding(.bottom, Space.lg)
                    .staggerReveal(1)

                MarqueHairline()

                // Brand group — editorial rows: serif label on a hairline, no icon
                // squares (the tinted-square-plus-chevron pattern reads as template UI).
                VStack(alignment: .leading, spacing: 0) {
                    sectionHeader("Brand")
                    profileRow(label: "Brand identity") { showBrandEditor = true }
                    MarqueHairline()
                    profileRow(label: "Voice & tone") { showVoiceEditor = true }
                    MarqueHairline()
                    profileRow(label: "Content pillars") { showPillarsEditor = true }
                    // H-05: "Your formats" editor removed — the server infers style
                    // per take now; there is no preferred-styles knob to set.
                    if !store.pillars.isEmpty {
                        pillarsStrip
                            .padding(.top, Space.sm)
                            .padding(.bottom, Space.md)
                    }
                }
                .padding(.horizontal, Space.screenH)
                .staggerReveal(2)

                MarqueHairline()

                // Creators to watch — feeds the mimic engine
                creatorsSection
                    .padding(.horizontal, Space.screenH)
                    .padding(.bottom, Space.lg)
                    .staggerReveal(3)

                MarqueHairline()

                // Accounts group
                VStack(alignment: .leading, spacing: 0) {
                    sectionHeader("Accounts")
                    ConnectAccountsView()
                }
                .padding(.horizontal, Space.screenH)
                .padding(.bottom, Space.lg)
                .staggerReveal(4)

                Spacer().frame(height: 120)
            }
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationTitle("Profile")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showSettings = true } label: {
                    Image(systemName: "gearshape").foregroundStyle(Palette.textSecondary)
                }
                .accessibilityIdentifier("profile.settings")
            }
        }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $showBrandEditor) { BrandEditorSheet(store: store) }
        .sheet(isPresented: $showVoiceEditor) { VoiceEditorSheet(store: store) }
        .sheet(isPresented: $showPillarsEditor) { PillarsEditorSheet(store: store) }
        .task {
            if store.brandSummary == nil { await refreshSummary() }
        }
    }

    // MARK: - Brand summary card

    // The Marque Path rank card — seal + tier + a gold progress bar to the next rank.
    private var rankCard: some View {
        let rank = store.creatorRank
        let xp = store.creatorXP
        let progress = RankSystem.progress(xp: xp, in: rank)
        let toNext = rank.nextXP.map { max(0, $0 - xp) }
        return VStack(alignment: .leading, spacing: Space.sm) {
            HStack(spacing: Space.md) {
                RankSeal(level: rank.level, size: 52)
                VStack(alignment: .leading, spacing: 2) {
                    Text("The Marque Path").font(AppFont.micro).tracking(Track.label)
                        .foregroundStyle(Palette.textTertiary)
                    Text(rank.title).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    Text(rank.subtitle).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            // Progress rail
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Palette.surfaceSunken)
                    Capsule().fill(Palette.gold).frame(width: max(4, geo.size.width * progress))
                }
            }
            .frame(height: 6)
            HStack {
                Text("\(store.reelsShot) \(store.reelsShot == 1 ? "reel" : "reels") shot")
                    .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                Spacer()
                if let toNext, !rank.isMax {
                    Text("\(toNext) XP to \(RankSystem.rank(atLevel: rank.level + 1).title)")
                        .font(AppFont.micro).foregroundStyle(Palette.textTertiary)
                } else {
                    Text("Top rank reached").font(AppFont.micro).foregroundStyle(Palette.gold)
                }
            }
        }
        .padding(Space.md)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private var brandSummaryCard: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack(alignment: .center, spacing: Space.sm) {
                SectionLabel(text: "What Yunicorn knows about you", accent: Palette.accent)
                Spacer(minLength: 0)
                Button {
                    Task { await refreshSummary() }
                } label: {
                    Group {
                        if isRefreshingSummary {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "arrow.clockwise")
                                .font(.system(size: 13, weight: .medium))
                                .foregroundStyle(Palette.textTertiary)
                        }
                    }
                    .frame(width: 24, height: 24)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(isRefreshingSummary)
                .accessibilityIdentifier("profile.refreshSummary")
                .accessibilityLabel("Refresh brand summary")
            }

            if let card = store.brandSummary {
                Text(card.summary)
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
                if !card.traits.isEmpty {
                    // Quiet hairline chips — the blue-tinted pills read as tag soup.
                    FlowWrap(spacing: 6) {
                        ForEach(Array(card.traits.enumerated()), id: \.offset) { _, trait in
                            Text(trait)
                                .font(Typeface.sans(11, .medium)).tracking(0.2)
                                .foregroundStyle(Palette.textSecondary)
                                .padding(.horizontal, 10).padding(.vertical, 4)
                                .background(Capsule().fill(Palette.surfaceRaised))
                                .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                        }
                    }
                    .padding(.top, 2)
                }
                if !card.workingOn.isEmpty {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("WORKING ON")
                            .font(AppFont.micro).tracking(Track.label)
                            .foregroundStyle(Palette.textTertiary)
                        Text(card.workingOn)
                            .font(AppFont.caption)
                            .foregroundStyle(Palette.textSecondary)
                            .lineSpacing(3)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(.top, Space.xs)
                }
            } else {
                // Skeleton paragraph while the first summary is being written
                VStack(alignment: .leading, spacing: Space.sm) {
                    RoundedRectangle(cornerRadius: 4).fill(Palette.surfaceSunken)
                        .frame(height: 12)
                        .frame(maxWidth: .infinity)
                    RoundedRectangle(cornerRadius: 4).fill(Palette.surfaceSunken)
                        .frame(height: 12)
                        .frame(maxWidth: 220)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.vertical, 2)
            }
        }
        .marqueCard()
    }

    @MainActor
    private func refreshSummary() async {
        guard !isRefreshingSummary else { return }
        isRefreshingSummary = true
        if let card = await store.backend.fetchBrandSummary(brand: store.brand, memory: store.memory) {
            store.brandSummary = card
            store.save()
        }
        isRefreshingSummary = false
    }

    // MARK: - Pillars glance (read-only; tap opens the editor)

    private var pillarsStrip: some View {
        FlowWrap(spacing: Space.sm) {
            ForEach(store.pillars) { p in
                Button { showPillarsEditor = true } label: {
                    HStack(spacing: 6) {
                        Circle().fill(Color(hex: p.colorHex)).frame(width: 8, height: 8)
                        Text(p.name)
                            .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            .lineLimit(1)
                    }
                    .padding(.horizontal, 12).padding(.vertical, 7)
                    .background(Palette.surfaceRaised)
                    .clipShape(Capsule())
                    .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                }
                .buttonStyle(PressableStyle())
            }
        }
        .accessibilityIdentifier("profile.pillarsStrip")
    }

    // MARK: - Creators to watch

    private var creatorsSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            SectionLabel(text: "Creators to watch")
                .padding(.top, Space.lg)
            Text("Two creators you love — Yunicorn studies their reels and feeds you mimicable ones.")
                .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, Space.xs)
            WatchedCreatorSlot(store: store, index: 0)
            WatchedCreatorSlot(store: store, index: 1)
        }
    }


    // MARK: - Hero + row helpers

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
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, Space.lg).padding(.bottom, Space.sm)
    }

    private func profileRow(label: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Text(label)
                    .font(Typeface.display(18, .medium)).tracking(Track.title)
                    .foregroundStyle(Palette.textPrimary)
                Spacer()
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Palette.textTertiary)
            }
            .padding(.vertical, 15)
            .contentShape(Rectangle())
        }
        .buttonStyle(PressableStyle(dim: 0.6))
    }
}

// MARK: - Creators-to-watch slot (saved row / add row / inline editor)

private struct WatchedCreatorSlot: View {
    let store: AppStore
    let index: Int
    @State private var expanded = false
    @State private var platform: SocialPlatform = .instagram
    @State private var handle = ""

    private var saved: WatchedCreator? {
        let list = store.brand.watchedCreators ?? []
        return index < list.count ? list[index] : nil
    }

    var body: some View {
        if let creator = saved {
            savedRow(creator)
        } else if expanded {
            editor
        } else {
            addRow
        }
    }

    private func savedRow(_ creator: WatchedCreator) -> some View {
        HStack(spacing: Space.md) {
            VStack(alignment: .leading, spacing: 1) {
                Text("@\(creator.handle)")
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary).lineLimit(1)
                Text(creator.platform.label.uppercased())
                    .font(AppFont.micro).tracking(Track.label)
                    .foregroundStyle(Palette.textTertiary)
            }
            Spacer(minLength: 0)
            Button { withAnimation(Motion.quick) { clear() } } label: {
                Image(systemName: "trash")
                    .font(.system(size: 13))
                    .foregroundStyle(Palette.textTertiary)
                    .frame(width: 32, height: 32)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("profile.clearCreator\(index)")
        }
        .padding(.horizontal, Space.md).padding(.vertical, 10)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private var addRow: some View {
        Button { withAnimation(Motion.quick) { expanded = true } } label: {
            HStack(spacing: Space.sm) {
                Image(systemName: "plus.circle")
                    .font(.system(size: 15))
                    .foregroundStyle(Palette.textSecondary)
                Text("Add a creator")
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                Spacer()
            }
            .padding(.horizontal, Space.md).frame(height: 50)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(Palette.hairline, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressableStyle())
        .accessibilityIdentifier("profile.addCreator\(index)")
    }

    private var editor: some View {
        VStack(spacing: Space.sm) {
            MarqueSegmented(options: SocialPlatform.allCases.map(\.label),
                            index: Binding(get: { SocialPlatform.allCases.firstIndex(of: platform) ?? 0 },
                                           set: { platform = SocialPlatform.allCases[$0] }))

            HStack(spacing: 4) {
                Text("@").foregroundStyle(Palette.textTertiary)
                TextField("\(platform.label) handle", text: $handle)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("profile.watchCreator\(index)")
            }
            .font(AppFont.bodyL)
            .padding(.horizontal, Space.md).frame(height: 50)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))

            HStack {
                Button("Cancel") { withAnimation(Motion.quick) { expanded = false; handle = "" } }
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                Spacer()
                Button { save() } label: {
                    Text("Save")
                        .font(AppFont.callout).foregroundStyle(Palette.onInk)
                        .padding(.horizontal, Space.lg).frame(height: 40)
                        .background(Palette.ink).clipShape(Capsule())
                }
                .buttonStyle(PressableStyle())
                .disabled(handle.trimmingCharacters(in: .whitespaces).isEmpty)
                .accessibilityIdentifier("profile.saveCreator\(index)")
            }
        }
        .padding(.vertical, Space.xs)
    }

    private func save() {
        let h = handle.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "@", with: "")
        guard !h.isEmpty else { return }
        var list = store.brand.watchedCreators ?? []
        let creator = WatchedCreator(platform: platform, handle: h)
        if index < list.count { list[index] = creator } else { list.append(creator) }
        store.brand.watchedCreators = Array(list.prefix(2))
        store.save()
        // Kick a background scrape so this creator's REAL reels are cached before
        // the user reaches Home — non-blocking, fire-and-forget.
        Task { await store.backend.warmWatchedCreator(handle: h, platform: platform.rawValue) }
        withAnimation(Motion.quick) { expanded = false; handle = "" }
    }

    private func clear() {
        var list = store.brand.watchedCreators ?? []
        if index < list.count { list.remove(at: index) }
        store.brand.watchedCreators = list
        store.save()
    }
}

// MARK: - Wrapping flow layout (trait chips + pillar chips)

private struct FlowWrap: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0, x + size.width > maxWidth {
                x = 0; y += rowHeight + spacing; rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        let width = maxWidth.isFinite ? maxWidth : max(0, x - spacing)
        return CGSize(width: width, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0, x + size.width > bounds.width {
                x = 0; y += rowHeight + spacing; rowHeight = 0
            }
            view.place(at: CGPoint(x: bounds.minX + x, y: bounds.minY + y),
                       anchor: .topLeading, proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
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
                .accessibilityIdentifier(label == "Known for" ? "profile.knownFor" : "profile.\(label.lowercased().replacingOccurrences(of: " ", with: ""))")
        }
    }

    private func save() {
        var b = store.brand
        b.niche = niche; b.whatYouDo = whatYouDo; b.audience = audience; b.knownFor = knownFor
        store.brand = b
        store.brandSummary = nil    // stale — Profile refetches on next open
        store.save(); dismiss()
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
    @State private var draft: [Pillar] = []
    @State private var regenerating = false
    @State private var confirmRefresh = false
    @FocusState private var focusedNew: UUID?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.md) {
                    Text("Rename, retune the mix, add or remove — these shape every script Yunicorn writes.")
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)

                    ForEach($draft) { $p in
                        PillarEditRow(pillar: $p,
                                      canDelete: draft.count > 1,
                                      focusedNew: $focusedNew,
                                      onDelete: { draft.removeAll { $0.id == p.id } })
                    }

                    if draft.count < 6 {
                        GhostButton(title: "Add pillar", systemImage: "plus") { addPillar() }
                            .accessibilityIdentifier("pillars.add")
                    }

                    GhostButton(title: regenerating ? "Regenerating…" : "Refresh with AI", systemImage: "sparkles") {
                        confirmRefresh = true
                    }
                    .disabled(regenerating)
                    .marqueConfirm($confirmRefresh, title: "Regenerate pillars?",
                                   message: "This replaces everything here with a fresh AI analysis of your brand.",
                                   confirm: "Replace my edits", destructive: true,
                                   cancel: "Keep my edits") {
                        regenerating = true
                        Task { await store.analyzePage(); draft = store.pillars; regenerating = false }
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Content pillars")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { commit(); dismiss() }.fontWeight(.semibold)
                        .accessibilityIdentifier("pillars.done")
                }
            }
        }
        .onAppear { if draft.isEmpty { draft = store.pillars } }
    }

    private func addPillar() {
        let colors = Catalog.pillarColors
        let p = Pillar(name: "", summary: "", angle: "", exampleTopics: [],
                       weight: 1.0 / Double(draft.count + 1),
                       colorHex: colors[draft.count % colors.count])
        draft.append(p)
        focusedNew = p.id
    }

    /// Drop empty-named rows, normalize weights to sum 1.0, mirror topThemes, persist.
    private func commit() {
        var kept = draft.filter { !$0.name.trimmingCharacters(in: .whitespaces).isEmpty }
        if kept.isEmpty { kept = draft }               // never leave zero pillars
        let total = kept.map(\.weight).reduce(0, +)
        if total > 0.0001 { for i in kept.indices { kept[i].weight /= total } }
        store.pillars = kept
        store.brand.topThemes = kept.map(\.name)
        store.save()
    }
}

private struct PillarEditRow: View {
    @Binding var pillar: Pillar
    let canDelete: Bool
    var focusedNew: FocusState<UUID?>.Binding
    let onDelete: () -> Void
    @State private var confirmDelete = false

    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack(spacing: Space.sm) {
                Circle().fill(Color(hex: pillar.colorHex)).frame(width: 12, height: 12)
                TextField("Pillar name", text: $pillar.name)
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    .focused(focusedNew, equals: pillar.id)
                    .accessibilityIdentifier("pillars.name")
                Spacer(minLength: 0)
                Button { confirmDelete = true } label: {
                    Image(systemName: "trash").font(.system(size: 14)).foregroundStyle(Palette.textTertiary)
                }
                .disabled(!canDelete)
                .opacity(canDelete ? 1 : 0.3)
                .accessibilityIdentifier("pillars.delete")
                .marqueConfirm($confirmDelete, title: "Delete this pillar?",
                               confirm: "Delete", destructive: true) { onDelete() }
            }
            TextField("One-line summary", text: $pillar.summary, axis: .vertical)
                .font(AppFont.body).foregroundStyle(Palette.textSecondary).lineLimit(1...2)
            TextField("Your angle — why it's yours", text: $pillar.angle, axis: .vertical)
                .font(AppFont.body).foregroundStyle(Palette.textSecondary).lineLimit(1...3)
            HStack(spacing: Space.sm) {
                Text("Mix").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                Slider(value: $pillar.weight, in: 0.05...0.5)
                    .tint(Color(hex: pillar.colorHex))
                    .accessibilityIdentifier("pillars.weight")
                Text("\(Int((pillar.weight * 100).rounded()))%")
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    .frame(width: 38, alignment: .trailing)
            }
        }
        .marqueCard(padding: Space.md)
    }
}

