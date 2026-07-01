import SwiftUI

// "Your creative profile" — the full Brand Graph, editable and richly displayed.
struct BrandProfileView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var newNonNeg = ""
    @State private var regenerating = false

    var body: some View {
        @Bindable var store = store
        return NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    VStack(alignment: .leading, spacing: Space.lg) {
                        avatarHero
                        statsStrip
                    }
                    brandSection(store: store)
                    voiceSection(store: store)
                    pillarsSection
                    nonNegsSection(store: store)
                    accountsSection
                }
                .screenPadding()
                .padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Your Profile")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { store.save(); dismiss() }
                }
            }
        }
    }

    // MARK: Avatar hero

    private var avatarHero: some View {
        HStack(alignment: .center, spacing: Space.lg) {
            ZStack {
                Circle()
                    .fill(LinearGradient(
                        colors: [Palette.accent.opacity(0.18), Palette.accent.opacity(0.05)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    ))
                Circle().strokeBorder(Palette.accent.opacity(0.28), lineWidth: 1.5)
                Text(initials)
                    .font(Typeface.display(26, .semibold))
                    .foregroundStyle(Palette.textPrimary)
            }
            .frame(width: 72, height: 72)

            VStack(alignment: .leading, spacing: 5) {
                if !store.brand.knownFor.isEmpty {
                    Text(store.brand.knownFor)
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                        .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                } else if !store.brand.niche.isEmpty {
                    Text(store.brand.niche)
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                }
                if !store.brand.niche.isEmpty && !store.brand.knownFor.isEmpty {
                    Text(store.brand.niche)
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                }
                if let handle = store.brand.connectedAccounts.first?.handle, !handle.isEmpty {
                    Text("@\(handle)").font(AppFont.callout).foregroundStyle(Palette.accent)
                }
            }
            Spacer(minLength: 0)
        }
    }

    private var initials: String {
        let src = store.brand.knownFor.isEmpty ? store.brand.niche : store.brand.knownFor
        return String(src.prefix(1)).uppercased().isEmpty ? "M" : String(src.prefix(1)).uppercased()
    }

    // MARK: Stats

    private var statsStrip: some View {
        HStack(spacing: Space.sm) {
            statCell("\(store.clips.count)", "Clips")
            statCell("\(store.scripts.count)", "Scripts")
            statCell("\(store.streak)", "Sessions")
        }
    }

    private func statCell(_ value: String, _ label: String) -> some View {
        VStack(spacing: 4) {
            Text(value)
                .font(Typeface.display(28, .semibold)).tracking(-0.5)
                .foregroundStyle(Palette.textPrimary)
            Text(label)
                .font(AppFont.micro).tracking(Track.label)
                .foregroundStyle(Palette.textTertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Space.md)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
        .shadow(color: Palette.shadowWarm.opacity(0.05), radius: 12, x: 0, y: 4)
    }

    // MARK: Brand details

    private func brandSection(store: AppStore) -> some View {
        @Bindable var store = store
        return VStack(alignment: .leading, spacing: Space.md) {
            SectionLabel(text: "Your brand", accent: Palette.accent)
            VStack(spacing: Space.md) {
                profileField("Niche", text: $store.brand.niche, id: "profile.niche")
                MarqueHairline()
                profileField("What you do", text: $store.brand.whatYouDo, id: "profile.whatYouDo")
                MarqueHairline()
                profileField("Who you serve", text: $store.brand.audience, id: "profile.audience")
                MarqueHairline()
                profileField("Known for", text: $store.brand.knownFor, id: "profile.knownFor")
            }
            .marqueCard()
        }
    }

    // MARK: Voice

    private func voiceSection(store: AppStore) -> some View {
        @Bindable var store = store
        return VStack(alignment: .leading, spacing: Space.md) {
            SectionLabel(text: "Voice fingerprint", accent: Palette.accent)
            Text("Marque writes every script in this register.")
                .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
            VStack(spacing: Space.md) {
                voiceSlider("Funny", "Serious", value: $store.brand.voice.funnyToSerious)
                MarqueHairline()
                voiceSlider("Polished", "Raw", value: $store.brand.voice.polishedToRaw)
                MarqueHairline()
                voiceSlider("Teacher", "Peer", value: $store.brand.voice.teacherToPeer)
            }
            .marqueCard()
        }
    }

    // MARK: Pillars

    private var pillarsSection: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            HStack(alignment: .center) {
                SectionLabel(text: "Content pillars", accent: Palette.accent)
                Spacer()
                Button {
                    regenerating = true
                    Task { await store.analyzePage(); regenerating = false }
                } label: {
                    HStack(spacing: 5) {
                        if regenerating {
                            ProgressView().controlSize(.mini).tint(Palette.accent)
                        } else {
                            Image(systemName: "arrow.clockwise").font(.system(size: 11, weight: .medium))
                        }
                        Text(regenerating ? "Refreshing…" : "Refresh")
                            .font(AppFont.caption)
                    }
                    .foregroundStyle(Palette.accent)
                }
                .buttonStyle(.plain)
                .disabled(regenerating)
            }
            if store.pillars.isEmpty {
                Text("Complete your brand setup to get pillars.")
                    .font(AppFont.callout).foregroundStyle(Palette.textTertiary)
                    .marqueCard(padding: Space.md)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(store.pillars.enumerated()), id: \.element.id) { i, p in
                        if i > 0 { MarqueHairline() }
                        PillarProfileRow(pillar: p)
                    }
                }
                .background(Palette.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
                .shadow(color: Palette.shadowWarm.opacity(0.07), radius: 18, x: 0, y: 8)
            }
        }
    }

    // MARK: Non-negotiables

    private func nonNegsSection(store: AppStore) -> some View {
        @Bindable var store = store
        return VStack(alignment: .leading, spacing: Space.md) {
            SectionLabel(text: "Never say", accent: Palette.accent)
            VStack(alignment: .leading, spacing: Space.md) {
                if !store.brand.nonNegotiables.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: Space.sm) {
                            ForEach(store.brand.nonNegotiables, id: \.self) { item in
                                Button {
                                    store.brand.nonNegotiables.removeAll { $0 == item }
                                } label: {
                                    HStack(spacing: 4) {
                                        Text(item).font(AppFont.callout).foregroundStyle(Palette.textPrimary)
                                        Image(systemName: "xmark").font(.system(size: 9)).foregroundStyle(Palette.textTertiary)
                                    }
                                    .padding(.horizontal, Space.md).padding(.vertical, 8)
                                    .background(Palette.surfaceRaised)
                                    .clipShape(Capsule())
                                    .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                    MarqueHairline()
                }
                HStack {
                    TextField("Add a no-go word or topic", text: $newNonNeg)
                        .font(AppFont.body).foregroundStyle(Palette.textPrimary)
                        .accessibilityIdentifier("profile.addNonNeg")
                    Button("Add") {
                        let t = newNonNeg.trimmingCharacters(in: .whitespaces)
                        if !t.isEmpty { store.brand.nonNegotiables.append(t); newNonNeg = "" }
                    }
                    .font(AppFont.callout).foregroundStyle(Palette.accent)
                    .disabled(newNonNeg.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .marqueCard()
        }
    }

    // MARK: Connected accounts

    private var accountsSection: some View {
        VStack(alignment: .leading, spacing: Space.md) {
            SectionLabel(text: "Connected accounts", accent: Palette.accent)
            Text("Link Instagram and TikTok so Marque can learn from what works.")
                .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
            ConnectAccountsView()
        }
    }

    // MARK: Helpers

    private func profileField(_ label: String, text: Binding<String>, id: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(AppFont.micro).tracking(Track.label)
                .foregroundStyle(Palette.textTertiary)
            TextField(label, text: text)
                .font(AppFont.bodyL).foregroundStyle(Palette.textPrimary)
                .padding(.vertical, Space.sm)
                .accessibilityIdentifier(id)
        }
    }

    private func voiceSlider(_ leading: String, _ trailing: String, value: Binding<Double>) -> some View {
        VStack(spacing: Space.xs) {
            HStack {
                Text(leading)
                    .font(AppFont.callout)
                    .foregroundStyle(value.wrappedValue < 0.4 ? Palette.accent : Palette.textTertiary)
                Spacer()
                Text(trailing)
                    .font(AppFont.callout)
                    .foregroundStyle(value.wrappedValue > 0.6 ? Palette.accent : Palette.textTertiary)
            }
            Slider(value: value).tint(Palette.accent)
        }
    }
}

// MARK: - Pillar row

struct PillarProfileRow: View {
    let pillar: Pillar
    var body: some View {
        HStack(spacing: 0) {
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(Color(hex: pillar.colorHex))
                .frame(width: 3)
                .padding(.vertical, Space.md)

            HStack(spacing: Space.md) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(pillar.name)
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    if !pillar.summary.isEmpty {
                        Text(pillar.summary)
                            .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            .lineLimit(2).fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer()
                ZStack {
                    Circle().fill(Color(hex: pillar.colorHex).opacity(0.12)).frame(width: 26, height: 26)
                    Text(String(pillar.name.prefix(1)))
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Color(hex: pillar.colorHex))
                }
            }
            .padding(.horizontal, Space.lg)
            .padding(.vertical, Space.md)
        }
        .contentShape(Rectangle())
    }
}
