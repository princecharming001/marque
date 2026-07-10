import SwiftUI

// "Who do you want to sound like?" — multi-select emulation targets. Three
// hand-picked presets work instantly (curated style profiles on the backend,
// no scraping needed); "Link a creator you love" fires a non-blocking backend
// analysis of a real page. Skip is always available — this step never blocks.
struct EmulateStep: View {
    @Environment(AppStore.self) private var store
    @State private var linking = false
    @State private var showLinkRow = false
    @State private var linkPlatform = "instagram"
    @State private var handle = ""
    @State private var error: String?

    private static let presets: [(name: String, id: String)] = [
        ("Alex Hormozi", "hormozi"),
        ("Andrew Tate", "tate"),
        ("Shelby Sapp", "sapp"),
        ("MrBeast", "mrbeast"),
    ]

    private let columns = [GridItem(.flexible(), spacing: Space.md),
                           GridItem(.flexible(), spacing: Space.md)]

    private var targets: [EmulationTarget] {
        store.brand.emulationTargets ?? []
    }

    var body: some View {
        VStack(spacing: Space.md) {
            // 2×2 grid of larger liquid-glass preset buttons.
            LazyVGrid(columns: columns, spacing: Space.md) {
                ForEach(Self.presets, id: \.id) { preset in
                    presetCard(preset)
                }
            }

            ForEach(targets.filter { $0.source == .custom }) { target in
                customTargetRow(target)
            }

            if showLinkRow {
                linkInputRow
            } else {
                OptionCard(icon: "OnbIcon-emulate-custom", sfFallback: "link.badge.plus",
                           title: "Link a creator you love",
                           subtitle: "I'll study their page and learn their style",
                           selected: false) {
                    withAnimation(Motion.enter) { showLinkRow = true }
                }
                .accessibilityIdentifier("onboard.emulate.custom")
            }

            if let error {
                Text(error).font(AppFont.caption).foregroundStyle(Palette.critical)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    private func presetCard(_ preset: (name: String, id: String)) -> some View {
        let selected = targets.contains { $0.source == .preset && $0.name == preset.name }
        return PresetGlassCard(iconName: "OnbIcon-emulate-\(preset.id)",
                               name: preset.name, selected: selected) {
            togglePreset(preset.name)
        }
        .accessibilityIdentifier("onboard.emulate.preset.\(preset.id)")
    }

    private func togglePreset(_ name: String) {
        var list = store.brand.emulationTargets ?? []
        if let idx = list.firstIndex(where: { $0.source == .preset && $0.name == name }) {
            list.remove(at: idx)
        } else {
            list.append(EmulationTarget(name: name, source: .preset))
        }
        store.brand.emulationTargets = list
        store.save()
    }

    private func customTargetRow(_ target: EmulationTarget) -> some View {
        HStack(spacing: Space.md) {
            AsyncImage(url: URL(string: target.avatarUrl)) { img in
                img.resizable().scaledToFill()
            } placeholder: {
                Palette.surfaceSunken.overlay(Image(systemName: "person.fill").foregroundStyle(Palette.textTertiary))
            }
            .frame(width: 40, height: 40).clipShape(Circle())
            .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))

            VStack(alignment: .leading, spacing: 2) {
                Text(target.name).font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                Text(target.followers > 0
                     ? "\(compactNumber(target.followers)) followers · \(target.platform.capitalized)"
                     : "@\(target.handle) · \(target.platform.capitalized)")
                    .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
            }
            Spacer(minLength: 0)
            Image(systemName: "checkmark.circle.fill").foregroundStyle(Palette.positive)
            Button {
                store.brand.emulationTargets?.removeAll { $0.id == target.id }
                store.save()
            } label: {
                Image(systemName: "xmark").font(.system(size: 12)).foregroundStyle(Palette.textTertiary)
            }
            .padding(.leading, 4)
        }
        .padding(Space.md)
        .background(Palette.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
            .strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private var linkInputRow: some View {
        VStack(spacing: Space.sm) {
            MarqueSegmented(options: ["Instagram", "TikTok"],
                            index: Binding(get: { linkPlatform == "tiktok" ? 1 : 0 },
                                           set: { linkPlatform = $0 == 1 ? "tiktok" : "instagram" }))

            HStack(spacing: 4) {
                Text("@").foregroundStyle(Palette.textTertiary)
                TextField("creator handle", text: $handle)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("onboard.emulate.handle")
            }
            .font(AppFont.bodyL)
            .padding(.horizontal, Space.md).frame(height: 50)
            .background(Palette.surfaceRaised)
            .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))

            HStack {
                Button("Cancel") { showLinkRow = false; handle = ""; error = nil }
                    .font(AppFont.callout).foregroundStyle(Palette.textSecondary)
                Spacer()
                Button { addCustom() } label: {
                    HStack(spacing: 6) {
                        if linking { ProgressView().controlSize(.small).tint(Palette.onInk) }
                        Text(linking ? "Adding…" : "Add").font(AppFont.callout)
                    }
                    .foregroundStyle(Palette.onInk)
                    .padding(.horizontal, Space.lg).frame(height: 40)
                    .background(Palette.ink).clipShape(Capsule())
                }
                .buttonStyle(PressableStyle())
                .disabled(linking || handle.trimmingCharacters(in: .whitespaces).isEmpty)
                .accessibilityIdentifier("onboard.emulate.add")
            }
        }
    }

    private func addCustom() {
        linking = true; error = nil
        let h = handle.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "@", with: "")
        let platform = linkPlatform
        Task {
            // Verify the page is real first — same check "Connect your accounts" uses
            // (fetches the actual public profile), just without the OAuth grant since
            // an emulation target only needs to be studied, never posted to.
            guard let preview = await store.connectPreview(handle: h, platform: platform) else {
                error = "Couldn't find @\(h) on \(platform.capitalized). Check the handle and try again."
                linking = false
                return
            }
            var list = store.brand.emulationTargets ?? []
            list.append(EmulationTarget(
                name: preview.displayName.isEmpty ? "@\(h)" : preview.displayName,
                handle: h, platform: platform, source: .custom,
                avatarUrl: preview.avatarUrl, followers: preview.followers))
            store.brand.emulationTargets = list
            store.save()
            showLinkRow = false
            handle = ""
            linking = false
            // Fire-and-forget: the backend caches the analyzed style profile and
            // resolves it lazily on the next generation call — onboarding never
            // waits on a scrape.
            Task { await store.backend.emulateAnalyze(handle: h, platform: platform) }
        }
    }
}

// A large square liquid-glass preset button: clay bust on top, name below,
// selected state = ink ring + checkmark. Sits in the emulate step's 2-column grid.
private struct PresetGlassCard: View {
    let iconName: String
    let name: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: Space.sm) {
                bust
                Text(name)
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    .lineLimit(1).minimumScaleFactor(0.8)
            }
            .frame(maxWidth: .infinity)
            .frame(height: 190)
            .background(LiquidGlassFill(radius: Radius.xl))
            .clipShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous)
                .strokeBorder(selected ? Palette.ink : Color.white.opacity(0.6),
                              lineWidth: selected ? 2 : 1))
            .overlay(alignment: .topTrailing) {
                if selected {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 20))
                        .foregroundStyle(Palette.ink)
                        .padding(10)
                }
            }
            .shadow(color: Palette.shadowCool.opacity(0.16), radius: 18, y: 10)
            .scaleEffect(selected ? 1.02 : 1)
            .contentShape(RoundedRectangle(cornerRadius: Radius.xl, style: .continuous))
        }
        .buttonStyle(PressableStyle(dim: 0.9))
        .animation(Motion.spring, value: selected)
    }

    // The regenerated busts share identical framing (same shoulder crop + canvas), so a
    // fixed-height scaledToFit renders them all at a consistent visual size — no more
    // MrBeast-smaller-than-Hormozi. Bumped up from 76pt (they read as too small).
    @ViewBuilder private var bust: some View {
        if UIImage(named: iconName) != nil {
            Image(iconName).resizable().scaledToFit().frame(width: 104, height: 104)
        } else {
            Image(systemName: "person.crop.circle")
                .font(.system(size: 52, weight: .light))
                .foregroundStyle(Palette.textSecondary)
                .frame(width: 104, height: 104)
        }
    }
}
