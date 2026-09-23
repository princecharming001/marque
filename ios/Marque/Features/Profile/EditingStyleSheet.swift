import SwiftUI

// Editing style — the ONE home for every standing craft dial. Before build 61 these lived
// in three places at once: Settings ("Editing" group), the record screen (asked again on
// every take), and nowhere at all for the learned taste profile. Now they live here and
// the record screen just reads them.
//
// The screen opens with the payoff, not the controls: a real sample edit matching the
// creator's current profile, and — at the bottom — the literal config the pipeline will be
// sent. Dials in the middle. That ordering is deliberate: it makes the abstract vector
// ("caption_boldness 0.42") answerable in the only terms that matter — what comes out.
//
// Visual language (Stoic redesign): eyebrow + page title, hero sample reel
// framed in a surface card, then each
// dial group as an eyebrow-headed grouped card with its caption in textSecondary
// and a consistent Space.md inner grid.
struct EditingStyleSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var deck: StyleDeckPayload? = nil
    @State private var brollStyles: [BrollStyleOption] = []
    /// The sample resolved for the CURRENT profile. Debounced (see `matchTask`) because
    /// every dial drag re-resolves it and swapping an AVPlayer item per frame of a drag
    /// would stutter the gesture it's reacting to.
    @State private var match: (archetype: StyleArchetype, clip: StyleSampleClip)? = nil
    @State private var matchTask: Task<Void, Never>? = nil
    @State private var samplePlaybackFailed = false
    @State private var showRetake = false

    private var profile: [String: Double] {
        StyleProfileMapper.normalize(store.editPrefs.styleProfile?.dims)
    }

    var body: some View {
        @Bindable var store = store
        return NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.xl) {
                    header
                    samplePlayer
                    captionsSection
                    fillerSection
                    brollSection
                    memeSection
                    resolvedSection
                    retakeSection
                }
                .screenPadding()
                .padding(.top, Space.sm)
                .padding(.bottom, Space.xxl)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { store.save(); dismiss() }.fontWeight(.semibold)
                        .accessibilityIdentifier("editingStyle.done")
                }
            }
            .tint(Palette.ink)
        }
        .task { await load() }
        // Every dial writes straight through — the sheet has no cancel, so there is no
        // draft to keep. save() also re-publishes editPrefs onto the backend client.
        .onChange(of: store.editPrefs) { _, _ in
            store.save()
            scheduleMatch()
        }
        .sheet(isPresented: $showRetake) { retakeSheet }
    }

    private func load() async {
        if deck == nil { deck = await store.backend.styleDeck() }
        if brollStyles.isEmpty {
            brollStyles = await store.backend.brollStyles(niche: store.brand.niche)
        }
        scheduleMatch()
    }

    /// 0.25s debounce: the archetype match is cheap, but REPLACING the sample clip isn't.
    private func scheduleMatch() {
        matchTask?.cancel()
        matchTask = Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(250))
            guard !Task.isCancelled else { return }
            let next = deck?.sample(for: profile)
            if next?.clip.videoURL != match?.clip.videoURL { samplePlaybackFailed = false }
            match = next
        }
    }

    // MARK: - Header (Library's kicker + serif title treatment)

    private var header: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            DSEyebrow(text: "YOUR SIGNATURE CUT")
            // Title kept verbatim ("Editing style"): a Maestro flow asserts on it.
            DSPageTitle(title: "Editing style",
                        subtitle: "Set it once, every edit Yunicorn cuts for you starts here.")
        }
    }

    // MARK: - Sample player (hero)

    @ViewBuilder private var samplePlayer: some View {
        VStack(spacing: Space.md) {
            ZStack {
                RoundedRectangle(cornerRadius: Radius.tile, style: .continuous)
                    .fill(Palette.surfaceSunken)
                if let match, !samplePlaybackFailed, let url = URL(string: match.clip.videoURL) {
                    FailableVideoPlayer(url: url, muted: true, showsControls: false,
                                        onFailure: { samplePlaybackFailed = true })
                        .clipShape(RoundedRectangle(cornerRadius: Radius.tile, style: .continuous))
                        .allowsHitTesting(false)
                } else {
                    VStack(spacing: Space.sm) {
                        Image(systemName: "film")
                            .font(.system(size: 22, weight: .regular))
                            .foregroundStyle(Palette.textSecondary)
                        Text(deck == nil ? "Loading your sample…" : "No sample for this look yet.")
                            .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                            .multilineTextAlignment(.center)
                    }
                    .padding(.horizontal, Space.md)
                }
            }
            .frame(width: 180, height: 320)

            VStack(spacing: Space.xs) {
                DSEyebrow(text: "Closest match to your style")
                    .multilineTextAlignment(.center)
                if let match {
                    Text(match.archetype.name)
                        .font(AppFont.title3)
                        .foregroundStyle(Palette.textPrimary)
                        .multilineTextAlignment(.center)
                }
            }
            .padding(.horizontal, Space.md)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, Space.cardPad)
        .background(RoundedRectangle(cornerRadius: Radius.card, style: .continuous).fill(Palette.surface))
    }

    // MARK: - Captions

    private var captionsSection: some View {
        @Bindable var store = store
        return group("Captions") {
            MarqueToggleRow(title: "Auto-captions",
                            subtitle: "Burn word-timed captions into every clip.",
                            isOn: $store.editPrefs.autoCaptions)
                .accessibilityIdentifier("editingStyle.autoCaptions")
                .padding(.horizontal, Space.rowPad).padding(.vertical, 12)

            cardDivider

            // WYSIWYG picker (moved from the record screen): each style is a mini 9:16
            // frame with the caption at its REAL lower-third position, so the choice reads
            // as "this is what my video looks like" rather than an abstract word.
            VStack(alignment: .leading, spacing: Space.md) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: Space.sm) {
                        captionFrameCard(nil, label: "Auto") {
                            VStack(spacing: 2) {
                                Image(systemName: "wand.and.stars")
                                    .font(.system(size: 11, weight: .medium))
                                    .foregroundStyle(Palette.onNight.opacity(0.85))
                                Text("AI picks").font(AppFont.caption)
                                    .foregroundStyle(Palette.onNightSecondary)
                                    .lineLimit(1)
                            }
                        }
                        captionFrameCard(.clean, label: "Clean") {
                            Text("your words").font(AppFont.caption.weight(.semibold))
                                .foregroundStyle(Palette.onNight)
                                .lineLimit(1)
                        }
                        captionFrameCard(.boldWord, label: "Bold") {
                            VStack(spacing: 1) {
                                Text("YOUR").font(AppFont.eyebrow).foregroundStyle(Palette.onNightSecondary)
                                Text("WORDS").font(AppFont.eyebrow).foregroundStyle(Palette.onNight)
                            }
                        }
                        captionFrameCard(.karaoke, label: "Karaoke") {
                            HStack(spacing: 2) {
                                Text("your").font(AppFont.caption.weight(.semibold)).foregroundStyle(Palette.night)
                                    .padding(.horizontal, 3).padding(.vertical, 1)
                                    .background(Palette.onNight).clipShape(RoundedRectangle(cornerRadius: 2))
                                Text("words").font(AppFont.caption.weight(.semibold)).foregroundStyle(Palette.onNight)
                            }
                        }
                    }
                }
                // Size = literal type scale: three "Aa" at their relative sizes, not S/M/L
                // circles disconnected from what they resize.
                HStack(spacing: Space.sm) {
                    DSEyebrow(text: "Size")
                    ForEach(CaptionSize.allCases) { size in
                        let active = store.editPrefs.captionSize == size
                        Button {
                            withAnimation(.easeOut(duration: 0.12)) {
                                store.editPrefs.captionSize = active ? nil : size
                            }
                        } label: {
                            Text("Aa")
                                .font(Self.aaFont(size).weight(active ? .bold : .regular))
                                .foregroundStyle(active ? Palette.onInk : Palette.textPrimary)
                                .frame(width: 48, height: 36)
                                .background(Capsule().fill(active ? Palette.ink : Palette.surfaceSunken))
                                .contentShape(Capsule())
                                .animation(Motion.quick, value: active)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("editingStyle.capSize.\(size.rawValue)")
                    }
                    if store.editPrefs.captionSize == nil {
                        Text("Auto").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    }
                }
            }
            .padding(Space.rowPad)
        }
    }

    /// The three "Aa" samples, stepped on the type scale so their relative size reads.
    private static func aaFont(_ s: CaptionSize) -> Font {
        switch s {
        case .small: AppFont.caption
        case .medium: AppFont.bodyText
        case .large: AppFont.title3
        }
    }

    private static func captionPointSize(_ s: CaptionSize) -> CGFloat {
        switch s {
        case .small: 11
        case .medium: 14
        case .large: 17
        }
    }

    /// A mini 9:16 "video frame" chip: faint head silhouette up top implies the footage,
    /// the caption preview sits at the real lower-third position.
    @ViewBuilder private func captionFrameCard(_ style: CaptionStyle?, label: String,
                                               @ViewBuilder preview: () -> some View) -> some View {
        let active = store.editPrefs.captionStyle == style
        Button {
            withAnimation(.easeOut(duration: 0.12)) { store.editPrefs.captionStyle = style }
        } label: {
            VStack(spacing: Space.xs) {
                ZStack {
                    // A mini video frame: night in both schemes, like real footage.
                    RoundedRectangle(cornerRadius: Radius.cell, style: .continuous)
                        .fill(Palette.night)
                    RoundedRectangle(cornerRadius: Radius.cell, style: .continuous)
                        .fill(LinearGradient(colors: [Color.white.opacity(0.22), Color.white.opacity(0)],
                                             startPoint: .top, endPoint: .bottom))
                    Circle().fill(Color.white.opacity(0.10))
                        .frame(width: 20, height: 20)
                        .offset(y: -16)
                    preview()
                        .frame(maxWidth: 56)
                        .minimumScaleFactor(0.6)
                        .offset(y: 18)
                }
                .frame(width: 62, height: 96)
                .overlay(RoundedRectangle(cornerRadius: Radius.cell, style: .continuous)
                    .strokeBorder(active ? Palette.textPrimary : Palette.hairline,
                                  lineWidth: active ? 2 : 1))
                HStack(spacing: 3) {
                    if active {
                        Image(systemName: "checkmark").font(.system(size: 9, weight: .bold))
                    }
                    Text(label).font(AppFont.caption.weight(active ? .semibold : .regular))
                        .lineLimit(1)
                }
                .foregroundStyle(active ? Palette.textPrimary : Palette.textSecondary)
            }
        }
        .buttonStyle(PressableStyle(dim: 0.8))
        .accessibilityAddTraits(active ? .isSelected : [])
        .accessibilityIdentifier("editingStyle.capStyle.\(style?.rawValue ?? "auto")")
    }

    // MARK: - Filler

    private var fillerSection: some View {
        @Bindable var store = store
        return group("Filler",
                     caption: "How hard to cut the ums, restarts and dead air.") {
            MarqueSegmented(options: FillerTrim.allCases.map(\.label),
                            index: Binding(
                                get: { FillerTrim.allCases.firstIndex(of: store.editPrefs.fillerTrim) ?? 0 },
                                set: { store.editPrefs.fillerTrim = FillerTrim.allCases[$0] }))
                .accessibilityIdentifier("editingStyle.fillerTrim")
                .padding(Space.rowPad)
        }
    }

    // MARK: - B-roll style

    @ViewBuilder private var brollSection: some View {
        if !brollStyles.isEmpty {
            group("B-roll style",
                  caption: "What a cutaway looks like when your edit calls for one.") {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: Space.sm) {
                        ForEach(brollStyles) { s in brollCard(s) }
                    }
                    .padding(Space.rowPad)
                }
            }
        }
    }

    /// The card SHOWS the style via a demo clip rendered through that exact composition —
    /// a pixel-accurate preview of the treatment, not a mimicked creator reel.
    private func brollCard(_ s: BrollStyleOption) -> some View {
        let selected = (store.editPrefs.brollStyle ?? "cutaway") == s.id
        return Button {
            withAnimation(.easeOut(duration: 0.15)) { store.editPrefs.brollStyle = s.id }
        } label: {
            VStack(alignment: .leading, spacing: Space.xs) {
                ZStack(alignment: .topTrailing) {
                    AsyncImage(url: URL(string: s.thumbnailURL)) { img in
                        ZStack {
                            img.resizable().aspectRatio(contentMode: .fill)
                                .blur(radius: 12).opacity(0.55)
                            img.resizable().aspectRatio(contentMode: .fit)
                        }
                    } placeholder: {
                        Rectangle().fill(Palette.surfaceSunken)
                            .overlay(Image(systemName: "photo.on.rectangle.angled")
                                .foregroundStyle(Palette.textSecondary))
                    }
                    .frame(width: 110, height: 138).clipped()
                    if selected {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 18, weight: .regular))
                            .foregroundStyle(Palette.ink)
                            .background(Circle().fill(Palette.onInk).padding(2))
                            .padding(6)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: Radius.cell, style: .continuous))
                Text(s.label).font(AppFont.caption.weight(.semibold))
                    .foregroundStyle(Palette.textPrimary).lineLimit(1)
                Text(s.blurb).font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    .lineLimit(2, reservesSpace: true).multilineTextAlignment(.leading)
            }
            .frame(width: 110)
            .padding(Space.sm)
            .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                .fill(selected ? Palette.surfaceSunken : .clear))
            .overlay(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                .strokeBorder(selected ? Palette.textPrimary : .clear, lineWidth: 2))
        }
        .buttonStyle(PressableStyle(dim: 0.85))
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("editingStyle.brollStyle.\(s.id)")
    }

    // MARK: - Meme energy

    private var memeSection: some View {
        let level = Binding<Double>(
            get: { Double(store.editPrefs.memeIntensity ?? SubmitConfig.defaultMemeIntensity) },
            set: { store.editPrefs.memeIntensity = Int($0) })
        return group("Meme energy",
                     caption: "How culturally unhinged the cutaways get.") {
            VStack(alignment: .leading, spacing: Space.sm) {
                HStack {
                    DSEyebrow(text: "Level")
                    Spacer(minLength: Space.md)
                    Text(MemeEnergy.name(Int(level.wrappedValue)))
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                }
                Slider(value: level, in: 0...3, step: 1)
                    .tint(Palette.ink)
                    .accessibilityIdentifier("editingStyle.memeLevel")
            }
            .padding(Space.rowPad)
        }
    }

    // MARK: - Resolved settings (the explainability payoff)

    /// Exactly what `mapProfileToConfig` will hand the pipeline, plus the dials the creator
    /// set by hand (which override it). Nothing here is editable — it exists so "your
    /// editing style" is a claim the creator can CHECK rather than trust.
    private var resolvedSection: some View {
        let mapped = StyleProfileMapper.mapProfileToConfig(profile, archetypes: deck?.archetypes ?? [])
        let prefs = store.editPrefs
        var explicit: [String: String] = [:]
        if let s = prefs.captionStyle { explicit["caption_style"] = s.rawValue }
        if let s = prefs.captionSize { explicit["caption_size"] = s.rawValue }
        if let m = prefs.memeIntensity { explicit["meme_intensity"] = String(m) }
        explicit["filler_trim"] = prefs.fillerTrim.rawValue
        let keys = Set(mapped.keys).union(explicit.keys).sorted()

        return group("What gets sent",
                     caption: store.editPrefs.styleProfile == nil
                        ? "You haven't taken the taste quiz, these are the proven defaults."
                        : "Your taste, resolved into the settings your editor actually receives. A dial you set by hand always wins.") {
            FlowWrap(spacing: 6) {
                ForEach(keys, id: \.self) { k in
                    // `explicit` wins the display exactly the way it wins on the wire.
                    resolvedChip(key: k, value: explicit[k] ?? mapped[k] ?? "",
                                 byHand: explicit[k] != nil)
                }
            }
            .padding(Space.rowPad)
        }
    }

    private func resolvedChip(key: String, value: String, byHand: Bool) -> some View {
        // A dial set by hand is marked with a hand glyph and an outline, never a hue.
        HStack(spacing: Space.xs) {
            if byHand {
                Image(systemName: "hand.point.up.left")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Palette.textPrimary)
                    .accessibilityLabel("set by hand")
            }
            Text(key.replacingOccurrences(of: "_", with: " "))
                .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
            Text(value)
                .font(AppFont.caption.weight(.semibold))
                .foregroundStyle(Palette.textPrimary)
        }
        .lineLimit(1)
        .padding(.horizontal, 10).frame(height: 28)
        .background(Capsule().fill(Palette.surfaceSunken))
        .overlay(Capsule().strokeBorder(byHand ? Palette.textPrimary : .clear, lineWidth: 1))
    }

    // MARK: - Retake

    private var retakeSection: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            GhostButton(title: "Retake the taste quiz", systemImage: "arrow.counterclockwise") {
                showRetake = true
            }
            .accessibilityIdentifier("editingStyle.retake")
            if store.editPrefs.styleProfile?.handTuned == true {
                // Never silently overwrite hand-tuned work — say what a retake costs.
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.circle")
                        .font(.system(size: 14, weight: .regular))
                        .foregroundStyle(Palette.textPrimary)
                        .padding(.top, 1)
                    Text("You've tuned these by hand, a retake replaces the learned part of your profile.")
                        .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, Space.xs)
            }
        }
    }

    private var retakeSheet: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: Space.lg) {
                    Text("Swipe right on the looks you'd actually post.")
                        .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.center)
                    StyleTasteSwiper(onFinish: { profile in
                        store.editPrefs.styleProfile = profile
                        store.save()
                        scheduleMatch()
                        showRetake = false
                    })
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("which edits feel like you?")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { showRetake = false }
                }
            }
            .tint(Palette.ink)
        }
    }

    // MARK: - Chrome

    /// Inset hairline between rows inside a card.
    private var cardDivider: some View {
        DSRowDivider()
    }

    /// Eyebrow label (+ optional explanatory caption in textSecondary) above a surface card
    /// (grouped-list chrome, DESIGN.md §5 List rows).
    @ViewBuilder
    private func group<Content: View>(_ title: String, caption: String? = nil,
                                      @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            VStack(alignment: .leading, spacing: Space.xs) {
                DSEyebrow(text: title)
                if let caption {
                    Text(caption)
                        .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, Space.rowPad)
            VStack(alignment: .leading, spacing: 0) { content() }
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: Radius.group, style: .continuous)
                    .fill(Palette.surface))
                .clipShape(RoundedRectangle(cornerRadius: Radius.group, style: .continuous))
        }
    }
}
