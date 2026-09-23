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
    @State private var showEditingStyle = false
    @State private var showCreatorProfile = false
    @State private var isRefreshingSummary = false

    // The first-synced account (see AppStore.primaryAccount) — its picture is the
    // creator's profile picture everywhere.
    private var account: ConnectedAccount? { store.primaryAccount }
    private var displayName: String { account?.displayName ?? account?.handle ?? "Creator" }
    private var handle: String { account.map { "@\($0.handle)" } ?? "" }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Space.xl) {
                // Header — identity only: avatar, name (title2), ONE quiet meta line.
                // The rank is a word here; its bar lives in the Creator profile sheet.
                VStack(spacing: Space.md) {
                    avatarHero
                    VStack(spacing: 4) {
                        Text(displayName)
                            .font(AppFont.title2).tracking(-0.2)
                            .foregroundStyle(Palette.textPrimary)
                            .lineLimit(1).minimumScaleFactor(0.8)
                        Text(metaLine)
                            .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            .lineLimit(1).minimumScaleFactor(0.85)
                    }
                    .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity)
                .padding(.top, Space.sm)

                // Evidence — three stat tiles (Stats pattern).
                statRow

                // Brand — doors as grouped rows under an eyebrow.
                VStack(alignment: .leading, spacing: Space.sm) {
                    sectionHeader("Brand")
                    DSGroup {
                        // The AI summary + rank progression, demoted from an on-page card
                        // to a door.
                        profileRow(label: "Creator profile", systemImage: "person.crop.circle") { showCreatorProfile = true }
                        DSRowDivider(inset: rowTextInset)
                        profileRow(label: "Brand identity", systemImage: "textformat") { showBrandEditor = true }
                        DSRowDivider(inset: rowTextInset)
                        profileRow(label: "Voice & tone", systemImage: "waveform") { showVoiceEditor = true }
                        DSRowDivider(inset: rowTextInset)
                        profileRow(label: "Content pillars", systemImage: "square.grid.2x2") { showPillarsEditor = true }
                        Group {
                            if !store.pillars.isEmpty {
                                pillarsStrip
                            } else {
                                // Build 67: pillars are never invented. Cold start says so
                                // plainly instead of showing generic buckets as if yours.
                                Text("No pillars yet, connect your Instagram or TikTok and they're built from your real posts, or write your own.")
                                    .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .accessibilityIdentifier("profile.pillarsEmpty")
                            }
                        }
                        .padding(.leading, rowTextInset).padding(.trailing, Space.rowPad)
                        .padding(.bottom, Space.rowPad)
                        DSRowDivider(inset: rowTextInset)
                        // Build 61: the single home for every standing craft dial.
                        profileRow(label: "Editing style", systemImage: "scissors") { showEditingStyle = true }
                        // H-05: "Your formats" editor removed — the server infers style
                        // per take now; there is no preferred-styles knob to set.
                    }
                }

                // Creators to watch — feeds the mimic engine
                creatorsSection

                // Accounts group
                VStack(alignment: .leading, spacing: Space.sm) {
                    sectionHeader("Accounts")
                    ConnectAccountsView()
                }
            }
            .screenPadding()
            .padding(.top, Space.sm)
            .padding(.bottom, MarqueTabBar.clearance + Space.xl)
        }
        .background(Palette.canvas.ignoresSafeArea())
        .navigationTitle("Profile")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showSettings = true } label: {
                    Image(systemName: "gearshape")
                        .font(.system(size: 18, weight: .regular))
                        .foregroundStyle(Palette.textPrimary)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("Open settings")
                .accessibilityIdentifier("profile.settings")
            }
        }
        .sheet(isPresented: $showSettings) { SettingsView() }
        .sheet(isPresented: $showBrandEditor) { BrandEditorSheet(store: store) }
        .sheet(isPresented: $showVoiceEditor) { VoiceEditorSheet(store: store) }
        .sheet(isPresented: $showPillarsEditor) { PillarsEditorSheet(store: store) }
        .sheet(isPresented: $showEditingStyle) { EditingStyleSheet() }
        .sheet(isPresented: $showCreatorProfile) { CreatorProfileSheet() }

    }

    /// Leading inset of row text inside the Brand group (row pad + glyph column + gap).
    private var rowTextInset: CGFloat { Space.rowPad + 24 + Space.md }

    // MARK: - Header meta + stats

    private var metaLine: String {
        var parts: [String] = []
        if !handle.isEmpty { parts.append(handle) }
        if !store.brand.niche.isEmpty { parts.append(store.brand.niche) }
        parts.append(store.creatorRank.title)
        return parts.joined(separator: " · ")
    }

    private var statRow: some View {
        HStack(spacing: Space.sm) {
            stat(value: "\(store.reelsShot)", label: store.reelsShot == 1 ? "reel" : "reels")
            stat(value: "\(store.creatorXP)", label: "xp")
            stat(value: "\(store.brand.connectedAccounts.count)", label: store.brand.connectedAccounts.count == 1 ? "account" : "accounts")
        }
    }

    private func stat(value: String, label: String) -> some View {
        DSStatTile(value: value, label: label)
            .monospacedDigit()
    }

    // MARK: - Pillars glance (read-only; tap opens the editor). Monochrome: a pillar's
    // stored colorHex is never rendered as a hue.

    private var pillarsStrip: some View {
        FlowWrap(spacing: Space.sm) {
            ForEach(store.pillars) { p in
                Button { showPillarsEditor = true } label: {
                    Text(p.name)
                        .font(AppFont.supporting).foregroundStyle(Palette.textPrimary)
                        .lineLimit(1)
                        .padding(.horizontal, 12).frame(height: 32)
                        .background(Capsule().fill(Palette.surfaceSunken))
                }
                .buttonStyle(PressableStyle(dim: 0.8))
            }
        }
        .accessibilityIdentifier("profile.pillarsStrip")
    }

    // MARK: - Creators to watch

    private var creatorsSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            sectionHeader("Creators to watch")
            Text("Two creators you love. Yunicorn studies their reels and feeds you mimicable ones.")
                .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, Space.rowPad)
                .padding(.bottom, Space.xs)
            VStack(spacing: Space.groupGap) {
                WatchedCreatorSlot(store: store, index: 0)
                WatchedCreatorSlot(store: store, index: 1)
            }
        }
    }


    // MARK: - Hero + row helpers

    private var avatarHero: some View {
        ZStack {
            Circle()
                .fill(Palette.surfaceSunken)
                .frame(width: 64, height: 64)
            if let url = account?.avatarUrl, !url.isEmpty, let u = URL(string: url) {
                AsyncImage(url: u) { img in img.resizable().scaledToFill() } placeholder: { monogram }
                    .frame(width: 64, height: 64)
                    .clipShape(Circle())
            } else {
                monogram
            }
        }
        .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private var monogram: some View {
        Text(String(displayName.prefix(1)).uppercased())
            .font(AppFont.title2)
            .foregroundStyle(Palette.textPrimary)
    }

    private func sectionHeader(_ title: String) -> some View {
        DSEyebrow(text: title)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Space.rowPad)
    }

    private func profileRow(label: String, systemImage: String? = nil, action: @escaping () -> Void) -> some View {
        DSRow(title: label, systemImage: systemImage, action: action)
    }
}

// MARK: - Creators-to-watch slot (saved row / add row / inline editor)

private struct WatchedCreatorSlot: View {
    let store: AppStore
    let index: Int
    @State private var expanded = false
    @State private var platform: SocialPlatform = .instagram
    @State private var handle = ""
    // Build 67 verify-then-add: Save fetches the REAL profile first, shows it (avatar,
    // name, followers), and only a confirmed preview is added — a watched creator never
    // reads as "a random name".
    @State private var verifying = false
    @State private var preview: ConnectedAccount?
    @State private var lookupFailed = false

    private var saved: WatchedCreator? {
        let list = store.brand.watchedCreators ?? []
        return index < list.count ? list[index] : nil
    }

    var body: some View {
        if let creator = saved {
            savedRow(creator)
                .task(id: creator.handle) { await backfill(creator) }
        } else if expanded {
            editor
        } else {
            addRow
        }
    }

    private func savedRow(_ creator: WatchedCreator) -> some View {
        HStack(spacing: Space.md) {
            creatorAvatar(url: creator.avatarUrl, handle: creator.handle, size: 40)
            VStack(alignment: .leading, spacing: 1) {
                Text(creator.displayName?.isEmpty == false ? creator.displayName! : "@\(creator.handle)")
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary).lineLimit(1)
                HStack(spacing: 5) {
                    Text("@\(creator.handle)")
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary).lineLimit(1)
                    if let f = creator.followers, f > 0 {
                        Text("· \(compactNumber(f)) followers")
                            .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            .lineLimit(1)
                    }
                    Text("· \(creator.platform.label)")
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        .lineLimit(1)
                }
                .minimumScaleFactor(0.85)
            }
            Spacer(minLength: 0)
            Button { withAnimation(Motion.quick) { clear() } } label: {
                Image(systemName: "trash")
                    .font(.system(size: 16, weight: .regular))
                    .foregroundStyle(Palette.textPrimary)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(PressableStyle(dim: 0.6))
            .accessibilityLabel("Remove creator")
            .accessibilityIdentifier("profile.clearCreator\(index)")
        }
        .padding(.leading, Space.rowPad).padding(.trailing, Space.xs).padding(.vertical, Space.sm)
        .frame(minHeight: 60)
        .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.surface))
    }

    /// Rows saved before build 67 (or added from the reel feed without profile data)
    /// hydrate lazily: one preview fetch, persisted, never repeated once filled.
    private func backfill(_ creator: WatchedCreator) async {
        guard creator.avatarUrl == nil || creator.avatarUrl?.isEmpty == true else { return }
        guard let p = await store.backend.connectPreview(handle: creator.handle,
                                                        platform: creator.platform.rawValue) else { return }
        var list = store.brand.watchedCreators ?? []
        guard let i = list.firstIndex(where: { $0.id == creator.id }) else { return }
        list[i].displayName = p.displayName
        list[i].avatarUrl = p.avatarUrl
        list[i].followers = p.followers
        store.brand.watchedCreators = list
        store.save()
    }

    @ViewBuilder
    private func creatorAvatar(url: String?, handle: String, size: CGFloat) -> some View {
        ZStack {
            Circle().fill(Palette.surfaceSunken)
            if let url, !url.isEmpty, let u = URL(string: url) {
                AsyncImage(url: u) { img in
                    img.resizable().scaledToFill()
                } placeholder: {
                    Text(String(handle.prefix(1)).uppercased())
                        .font(AppFont.headline).foregroundStyle(Palette.textSecondary)
                }
            } else {
                Text(String(handle.prefix(1)).uppercased())
                    .font(AppFont.headline).foregroundStyle(Palette.textSecondary)
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
    }

    private var addRow: some View {
        Button { withAnimation(Motion.quick) { expanded = true } } label: {
            HStack(spacing: Space.md) {
                Image(systemName: "plus.circle")
                    .font(.system(size: 18, weight: .regular))
                    .foregroundStyle(Palette.textPrimary)
                    .frame(width: 24)
                Text("Add a creator")
                    .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                Spacer()
            }
            .padding(.horizontal, Space.rowPad).frame(height: 52)
            .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(PressableStyle(dim: 0.7))
        .accessibilityIdentifier("profile.addCreator\(index)")
    }

    private var editor: some View {
        VStack(spacing: Space.sm) {
            MarqueSegmented(options: SocialPlatform.allCases.map(\.label),
                            index: Binding(get: { SocialPlatform.allCases.firstIndex(of: platform) ?? 0 },
                                           set: { platform = SocialPlatform.allCases[$0] }))

            HStack(spacing: 4) {
                Text("@").foregroundStyle(Palette.textSecondary)
                TextField("\(platform.label) handle", text: $handle)
                    .foregroundStyle(Palette.textPrimary)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("profile.watchCreator\(index)")
            }
            .font(AppFont.bodyText)
            .padding(.horizontal, Space.rowPad).frame(height: 52)
            .background(Capsule().fill(Palette.surfaceSunken))

            // The verified profile, shown BEFORE anything is added — this is the
            // preview the whole flow exists for.
            if let p = preview {
                HStack(spacing: Space.md) {
                    creatorAvatar(url: p.avatarUrl, handle: p.handle, size: 40)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(p.displayName.isEmpty ? "@\(p.handle)" : p.displayName)
                            .font(AppFont.headline).foregroundStyle(Palette.textPrimary).lineLimit(1)
                        Text("@\(p.handle)" + (p.followers > 0 ? " · \(compactNumber(p.followers)) followers" : ""))
                            .font(AppFont.caption).foregroundStyle(Palette.textSecondary).lineLimit(1)
                    }
                    Spacer(minLength: Space.sm)
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 16, weight: .regular))
                        .foregroundStyle(Palette.textPrimary)
                        .accessibilityHidden(true)
                }
                .padding(Space.md)
                .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                    .fill(Palette.surfaceSunken))
                .accessibilityIdentifier("profile.creatorPreview\(index)")
            } else if lookupFailed {
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.circle")
                        .font(.system(size: 14, weight: .regular))
                    Text("Couldn't find that account, check the handle and platform.")
                        .font(AppFont.supporting)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .foregroundStyle(Palette.critical)
                .frame(maxWidth: .infinity, alignment: .leading)
            }

            HStack {
                Button("Cancel") {
                    withAnimation(Motion.quick) {
                        expanded = false; handle = ""; preview = nil; lookupFailed = false
                    }
                }
                    .buttonStyle(DSTextLinkStyle(color: Palette.textSecondary))
                Spacer()
                Button { preview == nil ? verify() : confirmAdd() } label: {
                    HStack(spacing: 6) {
                        if verifying { ProgressView().controlSize(.small).tint(Palette.textSecondary) }
                        Text(preview == nil ? (verifying ? "Checking…" : "Preview") : "Add")
                    }
                }
                .buttonStyle(DSCapsuleStyle(kind: .primary, height: 44))
                .disabled(handle.trimmingCharacters(in: .whitespaces).isEmpty || verifying)
                .accessibilityIdentifier("profile.saveCreator\(index)")
            }
        }
        .padding(Space.rowPad)
        .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous).fill(Palette.surface))
        .onChange(of: handle) { _, _ in preview = nil; lookupFailed = false }
        .onChange(of: platform) { _, _ in preview = nil; lookupFailed = false }
    }

    /// Step 1: resolve the handle into the real profile and show it.
    private func verify() {
        let h = handle.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "@", with: "")
        guard !h.isEmpty, !verifying else { return }
        verifying = true
        lookupFailed = false
        Task {
            let p = await store.backend.connectPreview(handle: h, platform: platform.rawValue)
            verifying = false
            if let p { preview = p } else { lookupFailed = true }
        }
    }

    /// Step 2: the creator confirmed the previewed profile — store it WITH its identity.
    private func confirmAdd() {
        guard let p = preview else { return }
        var list = store.brand.watchedCreators ?? []
        let creator = WatchedCreator(platform: platform, handle: p.handle,
                                     displayName: p.displayName, avatarUrl: p.avatarUrl,
                                     followers: p.followers)
        if index < list.count { list[index] = creator } else { list.append(creator) }
        store.brand.watchedCreators = Array(list.prefix(2))
        store.save()
        // Kick a background scrape so this creator's REAL reels are cached before
        // the user reaches Home — non-blocking, fire-and-forget.
        let h = p.handle
        let plat = platform.rawValue
        Task { await store.backend.warmWatchedCreator(handle: h, platform: plat) }
        withAnimation(Motion.quick) { expanded = false; handle = ""; preview = nil }
    }

    private func clear() {
        var list = store.brand.watchedCreators ?? []
        if index < list.count { list.remove(at: index) }
        store.brand.watchedCreators = list
        store.save()
    }
}

// MARK: - Wrapping flow layout (trait chips + pillar chips)

// Internal (not file-private): the Editing-style sheet's resolved-config chip row wraps
// the same way, and a second copy of a Layout is exactly the kind of duplication that
// drifts.
struct FlowWrap: Layout {
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
                VStack(alignment: .leading, spacing: Space.xl) {
                    fieldGroup("Your niche", placeholder: "e.g. fitness, personal finance, cooking", text: $niche)
                    fieldGroup("What you do", placeholder: "Your day-to-day work", text: $whatYouDo)
                    fieldGroup("Who you serve", placeholder: "Your target audience", text: $audience)
                    fieldGroup("Known for", placeholder: "What you want to be remembered for", text: $knownFor)
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("brand identity.")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .topBarTrailing) { Button("Save") { save() }.fontWeight(.semibold) }
            }
            .tint(Palette.ink)
        }
    }

    private func fieldGroup(_ label: String, placeholder: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            DSEyebrow(text: label).padding(.horizontal, Space.rowPad)
            TextField(placeholder, text: text).marqueField()
                .accessibilityIdentifier(label == "Known for" ? "profile.knownFor" : "profile.\(label.lowercased().replacingOccurrences(of: " ", with: ""))")
        }
    }

    private func save() {
        var b = store.brand
        b.niche = niche; b.whatYouDo = whatYouDo; b.audience = audience; b.knownFor = knownFor
        // Multi-select (build 83): editing the singular field rewrites the PRIMARY
        // entry of the array — the rest of the picks the creator made in onboarding
        // survive, and the invariant (niche == niches?.first) holds.
        Self.replacePrimary(niche, in: &b.niches)
        Self.replacePrimary(audience, in: &b.audiences)
        store.brand = b
        store.brandSummary = nil    // stale — Profile refetches on next open
        store.save(); dismiss()
    }

    /// Swap element 0 of a multi-select list (append when the list is empty).
    /// Leaves an un-migrated (nil) list alone — `allNiches` derives it from the
    /// singular field there.
    private static func replacePrimary(_ value: String, in list: inout [String]?) {
        guard var arr = list else { return }
        let v = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !v.isEmpty else { return }
        if arr.isEmpty { arr.append(v) } else { arr[0] = v }
        list = arr
    }
}

struct VoiceEditorSheet: View {
    let store: AppStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            @Bindable var store = store
            ScrollView {
                VStack(spacing: Space.stack) {
                    voiceRow("Funny", "Serious", value: $store.brand.voice.funnyToSerious)
                    voiceRow("Polished", "Raw", value: $store.brand.voice.polishedToRaw)
                    voiceRow("Teacher", "Peer", value: $store.brand.voice.teacherToPeer)
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("voice & tone.")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { store.save(); dismiss() }.fontWeight(.semibold)
                }
            }
            .tint(Palette.ink)
        }
    }

    private func voiceRow(_ l: String, _ r: String, value: Binding<Double>) -> some View {
        // One surface card per dial: eyebrow, the two poles (the leaning pole bold in
        // primary ink, the other secondary), monochrome track.
        VStack(alignment: .leading, spacing: Space.sm) {
            DSEyebrow(text: "\(l) to \(r)")
            HStack {
                Text(l).font(value.wrappedValue < 0.4 ? AppFont.headline : AppFont.bodyText)
                    .foregroundStyle(value.wrappedValue < 0.4 ? Palette.textPrimary : Palette.textSecondary)
                Spacer()
                Text(r).font(value.wrappedValue > 0.6 ? AppFont.headline : AppFont.bodyText)
                    .foregroundStyle(value.wrappedValue > 0.6 ? Palette.textPrimary : Palette.textSecondary)
            }
            Slider(value: value).tint(Palette.ink)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard(.surface, radius: Radius.group)
    }
}

struct PillarsEditorSheet: View {
    let store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var draft: [Pillar] = []
    @State private var regenerating = false
    @State private var confirmRefresh = false
    /// The row awaiting delete confirmation. OWNER (2026-08-15, "deleting/adjusting
    /// pillars doesn't work"): the confirm used to be a `.marqueConfirm` attached to
    /// the 14pt trash Button INSIDE the row's `.marqueCard`. marqueConfirm is an
    /// `.overlay`, not a presentation — so the dialog laid out against a 14pt frame
    /// and was then clipped away by the card's `.clipShape`. Tapping trash did
    /// nothing visible and the Delete action was unreachable, so pillars could never
    /// be removed. The repo already documents this rule (ProEditorView.swift:159):
    /// dialogs must be hosted on the ROOT view. Hoisted here, one per sheet.
    @State private var pendingDelete: UUID?
    /// Cancel means DISCARD. `.onDisappear` fires on every teardown path, so without
    /// this flag the commit-on-dismiss (added so swipe-down stops silently losing
    /// edits) would make Cancel save too — identical to Done.
    @State private var cancelled = false
    @FocusState private var focusedNew: UUID?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.stack) {
                    Text("Rename, retune the mix, add or remove, these shape every script Yunicorn writes.")
                        .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, Space.xs)
                        .padding(.bottom, Space.xs)

                    ForEach($draft) { $p in
                        PillarEditRow(pillar: $p,
                                      total: draftWeightTotal,
                                      canDelete: draft.count > 1,
                                      focusedNew: $focusedNew,
                                      onDelete: { pendingDelete = p.id })
                    }

                    if draft.count < 6 {
                        GhostButton(title: "Add pillar", systemImage: "plus") { addPillar() }
                            .accessibilityIdentifier("pillars.add")
                            .padding(.top, Space.xs)
                    }

                    // Build 67: AI refresh derives from REAL posts only — without a
                    // connected account there is nothing honest to generate from.
                    if store.brand.connectedAccounts.contains(where: { !$0.handle.isEmpty }) {
                    PrimaryButton(title: regenerating ? "Regenerating…" : "Refresh with AI", systemImage: "sparkles") {
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
                    } else {
                        Text("Connect your Instagram or TikTok and Yunicorn builds pillars from your real posts.")
                            .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, Space.xs)
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("content pillars.")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { cancelled = true; dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { commit(); dismiss() }.fontWeight(.semibold)
                        .accessibilityIdentifier("pillars.done")
                }
            }
            .tint(Palette.ink)
        }
        // Root-level host: full-screen scrim, nothing clips it, Delete is tappable.
        .marqueConfirm(Binding(get: { pendingDelete != nil },
                               set: { if !$0 { pendingDelete = nil } }),
                       title: "Delete this pillar?",
                       confirm: "Delete", destructive: true) {
            if let id = pendingDelete { draft.removeAll { $0.id == id } }
            pendingDelete = nil
        }
        // Swiping the sheet away used to discard every edit silently (only Done
        // committed). Commit on the way out so "adjusting" always sticks — unless
        // the creator explicitly hit Cancel.
        .onDisappear { if !cancelled { commit() } }
        .onAppear { if draft.isEmpty { draft = store.pillars } }
    }

    /// Sum of the raw slider weights. Weights are STORED raw (so a slider never snaps
    /// back) and normalized where they're consumed, so the row must show the same
    /// normalized share the generator actually uses — otherwise three pillars at 0.2
    /// would each read "20%" while each is really drawn a third of the time.
    private var draftWeightTotal: Double {
        max(0.0001, draft.map { max(0, $0.weight) }.reduce(0, +))
    }

    private func addPillar() {
        let colors = Catalog.pillarColors
        // An even share of the mix, clamped into the slider's own 0.05…0.5 range —
        // the first pillar used to be seeded at 1.0 (rendered "100%") with the handle
        // pinned past the end of a slider that maxes at 50%.
        let even = min(0.5, max(0.05, 1.0 / Double(draft.count + 1)))
        let p = Pillar(name: "", summary: "", angle: "", exampleTopics: [],
                       weight: even,
                       colorHex: colors[draft.count % colors.count])
        draft.append(p)
        focusedNew = p.id
    }

    /// Drop empty-named rows, mirror topThemes, persist.
    ///
    /// Weights are stored EXACTLY as the user set them. They used to be renormalized
    /// to sum 1.0 here, so someone who dragged a pillar to 50% reopened the sheet and
    /// found 38% — "adjusting doesn't work". Normalization is a consumption-time
    /// concern (weightedPillar), not a storage one.
    private func commit() {
        var kept = draft.filter { !$0.name.trimmingCharacters(in: .whitespaces).isEmpty }
        if kept.isEmpty { kept = draft }               // never leave zero pillars
        guard kept != store.pillars else { return }    // no-op on plain dismiss
        store.pillars = kept
        store.brand.topThemes = kept.map(\.name)
        store.pillarsUserEdited = true                 // scans must stop overwriting
        store.save()
    }
}

private struct PillarEditRow: View {
    @Binding var pillar: Pillar
    /// Sum of all pillars' raw weights — this row's percentage is its share of it.
    let total: Double
    let canDelete: Bool
    var focusedNew: FocusState<UUID?>.Binding
    let onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack(spacing: Space.sm) {
                TextField("Pillar name", text: $pillar.name)
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    .focused(focusedNew, equals: pillar.id)
                    .accessibilityIdentifier("pillars.name")
                Spacer(minLength: 0)
                Button { onDelete() } label: {
                    Image(systemName: "trash").font(.system(size: 16, weight: .regular))
                        .foregroundStyle(Palette.textPrimary)
                        .frame(width: 44, height: 44)          // real 44pt tap target
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(!canDelete)
                .opacity(canDelete ? 1 : 0.3)
                .accessibilityIdentifier("pillars.delete")
            }
            Rectangle().fill(Palette.hairline).frame(height: 1)
            TextField("One-line summary", text: $pillar.summary, axis: .vertical)
                .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary).lineLimit(1...2)
            TextField("Your angle, why it's yours", text: $pillar.angle, axis: .vertical)
                .font(AppFont.supporting).foregroundStyle(Palette.textSecondary).lineLimit(1...3)
            HStack(spacing: Space.sm) {
                DSEyebrow(text: "Mix")
                Slider(value: $pillar.weight, in: 0.05...0.5)
                    .tint(Palette.ink)
                    .accessibilityIdentifier("pillars.weight")
                Text("\(Int((pillar.weight / max(total, 0.0001) * 100).rounded()))%")
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    .monospacedDigit()
                    .frame(width: 48, alignment: .trailing)
            }
        }
        .dsCard(.surface, radius: Radius.group, padding: Space.rowPad)
    }
}



// MARK: - Creator profile sheet (build 68)

/// The AI's read on the creator + the Marque Path progression — moved off the profile
/// page (no best-in-class profile carries a prose card; Strava collapses its AI summary
/// the same way) into this tap-through sheet.
struct CreatorProfileSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var refreshing = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    // The Marque Path — seal, ink rail, one meta line, in one surface card.
                    let rank = store.creatorRank
                    let xp = max(store.creatorXP, rank.minXP)
                    let progress = RankSystem.progress(xp: xp, in: rank)
                    HStack(alignment: .top, spacing: Space.md) {
                        RankSeal(level: rank.level, size: 48)
                        VStack(alignment: .leading, spacing: Space.sm) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(rank.title).font(AppFont.title3)
                                    .foregroundStyle(Palette.textPrimary)
                                    .fixedSize(horizontal: false, vertical: true)
                                if let next = rank.nextXP, !rank.isMax {
                                    Text("\(max(0, next - xp)) XP to \(RankSystem.rank(atLevel: rank.level + 1).title)")
                                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                                } else {
                                    HStack(spacing: 4) {
                                        Image(systemName: "crown").font(.system(size: 11, weight: .semibold))
                                        Text("Top rank").font(AppFont.caption.weight(.semibold))
                                    }
                                    .foregroundStyle(Palette.textPrimary)
                                }
                            }
                            GeometryReader { geo in
                                ZStack(alignment: .leading) {
                                    Capsule().fill(Palette.surfaceSunken)
                                    Capsule().fill(Palette.ink)
                                        .frame(width: max(4, geo.size.width * progress))
                                }
                            }
                            .frame(height: 4)
                            Text(rank.subtitle).font(AppFont.supporting)
                                .foregroundStyle(Palette.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .dsCard(.surface, radius: Radius.card)

                    VStack(alignment: .leading, spacing: Space.sm) {
                        HStack {
                            DSEyebrow(text: "What Yunicorn knows")
                            Spacer()
                            Button {
                                Task { await refresh() }
                            } label: {
                                Group {
                                    if refreshing {
                                        ProgressView().controlSize(.small).tint(Palette.textSecondary)
                                    } else {
                                        Image(systemName: "arrow.clockwise")
                                            .font(.system(size: 16, weight: .regular))
                                            .foregroundStyle(Palette.textPrimary)
                                    }
                                }
                                .frame(width: 44, height: 44)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(PressableStyle(dim: 0.6))
                            .disabled(refreshing)
                            .accessibilityLabel("Refresh")
                            .accessibilityIdentifier("profile.refreshSummary")
                        }
                        .padding(.leading, Space.rowPad)

                        VStack(alignment: .leading, spacing: Space.md) {
                            if let card = store.brandSummary {
                                Text(card.summary)
                                    .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                                    .lineSpacing(4).fixedSize(horizontal: false, vertical: true)
                                if !card.traits.isEmpty {
                                    FlowWrap(spacing: Space.sm) {
                                        ForEach(Array(card.traits.enumerated()), id: \.offset) { _, trait in
                                            Text(trait)
                                                .font(AppFont.supporting)
                                                .foregroundStyle(Palette.textPrimary)
                                                .lineLimit(1)
                                                .padding(.horizontal, 12).frame(height: 32)
                                                .background(Capsule().fill(Palette.surfaceSunken))
                                        }
                                    }
                                }
                                if !card.workingOn.isEmpty {
                                    VStack(alignment: .leading, spacing: Space.xs) {
                                        DSEyebrow(text: "Working on")
                                        Text(card.workingOn).font(AppFont.supporting)
                                            .foregroundStyle(Palette.textSecondary).lineSpacing(3)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                    .padding(.top, Space.xs)
                                }
                            } else {
                                VStack(alignment: .leading, spacing: Space.sm) {
                                    RoundedRectangle(cornerRadius: Radius.cell).fill(Palette.surfaceSunken)
                                        .frame(height: 12).frame(maxWidth: .infinity)
                                    RoundedRectangle(cornerRadius: Radius.cell).fill(Palette.surfaceSunken)
                                        .frame(height: 12).frame(maxWidth: 220)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .dsCard(.surface, radius: Radius.card)
                    }
                }
                .screenPadding().padding(.top, Space.lg).padding(.bottom, Space.xxl)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("creator profile.").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() }.fontWeight(.semibold) }
            }
            .tint(Palette.ink)
            .task { if store.brandSummary == nil { await refresh() } }
        }
    }

    @MainActor
    private func refresh() async {
        guard !refreshing else { return }
        refreshing = true
        if let card = await store.backend.fetchBrandSummary(brand: store.brand, memory: store.memory) {
            store.brandSummary = card
            store.save()
        }
        refreshing = false
    }
}
