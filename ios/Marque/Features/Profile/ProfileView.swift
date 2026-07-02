import SwiftUI

// Profile — pushed from Home's top-right avatar (not a tab). Phase 10 completes:
// AI brand summary card, formats hard-filter, creators-to-watch.
struct ProfileView: View {
    @Environment(AppStore.self) private var store
    @State private var showSettings = false
    @State private var showBrandEditor = false
    @State private var showVoiceEditor = false
    @State private var showPillarsEditor = false
    @State private var showStyleEditor = false

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

                // Brand summary — Phase 10 replaces with the AI-written card
                if let card = store.brandSummary {
                    VStack(alignment: .leading, spacing: Space.sm) {
                        SectionLabel(text: "What Marque knows about you", accent: Palette.accent)
                        Text(card.summary)
                            .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                            .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
                    }
                    .marqueCard()
                    .padding(.horizontal, Space.screenH)
                    .padding(.bottom, Space.lg)
                }

                MarqueHairline()

                // Brand group
                VStack(alignment: .leading, spacing: 0) {
                    sectionHeader("Brand")
                    profileRow(icon: "pencil", label: "Brand identity") { showBrandEditor = true }
                    MarqueHairline().padding(.leading, 56)
                    profileRow(icon: "waveform", label: "Voice & tone") { showVoiceEditor = true }
                    MarqueHairline().padding(.leading, 56)
                    profileRow(icon: "square.grid.2x2", label: "Content pillars") { showPillarsEditor = true }
                    MarqueHairline().padding(.leading, 56)
                    profileRow(icon: "play.rectangle", label: "Your formats") { showStyleEditor = true }
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
        .sheet(isPresented: $showStyleEditor) { StyleEditorSheet(store: store) }
        .task {
            if store.brandSummary == nil,
               let card = await store.backend.fetchBrandSummary(brand: store.brand, memory: store.memory) {
                store.brandSummary = card
                store.save()
            }
        }
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
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, Space.lg).padding(.bottom, Space.sm)
    }

    private func profileRow(icon: String, label: String, accent: Bool = false, action: @escaping () -> Void) -> some View {
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
                    Text("Only these formats are suggested across the app — your feed, your scripts, your mimics.")
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                    StyleSelectionView(selected: $store.brand.preferredStyles)
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Your formats")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) {
                Button("Done") { store.save(); dismiss() }
            }}
        }
    }
}
