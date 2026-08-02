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
                VStack(alignment: .leading, spacing: Space.lg) {
                    samplePlayer
                    captionsSection
                    fillerSection
                    brollSection
                    memeSection
                    resolvedSection
                    retakeSection
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Editing style")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { store.save(); dismiss() }.fontWeight(.semibold)
                        .accessibilityIdentifier("editingStyle.done")
                }
            }
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

    // MARK: - Sample player

    @ViewBuilder private var samplePlayer: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            ZStack {
                RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                    .fill(Palette.surfaceSunken)
                if let match, !samplePlaybackFailed, let url = URL(string: match.clip.videoURL) {
                    FailableVideoPlayer(url: url, muted: true, showsControls: false,
                                        onFailure: { samplePlaybackFailed = true })
                        .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                        .allowsHitTesting(false)
                } else {
                    VStack(spacing: Space.xs) {
                        Image(systemName: "film")
                            .font(.system(size: 22, weight: .ultraLight))
                            .foregroundStyle(Palette.textTertiary)
                        Text(deck == nil ? "Loading your sample…" : "No sample for this look yet.")
                            .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    }
                }
            }
            .frame(width: 180, height: 320)
            .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                .strokeBorder(Palette.hairline, lineWidth: 1))
            .frame(maxWidth: .infinity, alignment: .center)

            VStack(spacing: 2) {
                Text("CLOSEST MATCH TO YOUR STYLE")
                    .font(AppFont.micro).tracking(Track.label)
                    .foregroundStyle(Palette.textTertiary)
                if let match {
                    Text(match.archetype.name)
                        .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .center)
        }
    }

    // MARK: - Captions

    private var captionsSection: some View {
        @Bindable var store = store
        return group("Captions") {
            MarqueToggleRow(title: "Auto-captions",
                            subtitle: "Burn word-timed captions into every clip.",
                            isOn: $store.editPrefs.autoCaptions)
                .accessibilityIdentifier("editingStyle.autoCaptions")

            Divider()

            // WYSIWYG picker (moved from the record screen): each style is a mini 9:16
            // frame with the caption at its REAL lower-third position, so the choice reads
            // as "this is what my video looks like" rather than an abstract word.
            VStack(alignment: .leading, spacing: Space.sm) {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: Space.sm) {
                        captionFrameCard(nil, label: "Auto") {
                            VStack(spacing: 2) {
                                Image(systemName: "wand.and.stars")
                                    .font(.system(size: 11, weight: .medium))
                                    .foregroundStyle(.white.opacity(0.85))
                                Text("AI picks").font(.system(size: 8, weight: .medium))
                                    .foregroundStyle(.white.opacity(0.6))
                            }
                        }
                        captionFrameCard(.clean, label: "Clean") {
                            Text("your words").font(.system(size: 9, weight: .semibold))
                                .foregroundStyle(.white)
                                .shadow(color: .black.opacity(0.7), radius: 1.5, y: 1)
                        }
                        captionFrameCard(.boldWord, label: "Bold") {
                            VStack(spacing: 1) {
                                Text("YOUR").font(.system(size: 10, weight: .black)).foregroundStyle(.white)
                                Text("WORDS").font(.system(size: 10, weight: .black)).foregroundStyle(Palette.accent)
                            }
                        }
                        captionFrameCard(.karaoke, label: "Karaoke") {
                            HStack(spacing: 2) {
                                Text("your").font(.system(size: 9, weight: .bold)).foregroundStyle(Palette.ink)
                                    .padding(.horizontal, 3).padding(.vertical, 1)
                                    .background(Palette.accent).clipShape(RoundedRectangle(cornerRadius: 2))
                                Text("words").font(.system(size: 9, weight: .semibold)).foregroundStyle(.white)
                            }
                        }
                    }
                }
                // Size = literal type scale: three "Aa" at their relative sizes, not S/M/L
                // circles disconnected from what they resize.
                HStack(spacing: Space.sm) {
                    Text("Size").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    ForEach(CaptionSize.allCases) { size in
                        let active = store.editPrefs.captionSize == size
                        Button {
                            withAnimation(.easeOut(duration: 0.12)) {
                                store.editPrefs.captionSize = active ? nil : size
                            }
                        } label: {
                            Text("Aa")
                                .font(.system(size: Self.captionPointSize(size),
                                              weight: active ? .bold : .medium))
                                .foregroundStyle(active ? Palette.onInk : Palette.textPrimary)
                                .frame(width: 44, height: 34)
                                .background(active ? Palette.ink : Palette.surfaceSunken)
                                .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("editingStyle.capSize.\(size.rawValue)")
                    }
                    if store.editPrefs.captionSize == nil {
                        Text("Auto").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    }
                }
            }
            .padding(.horizontal, Space.md).padding(.vertical, 10)
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
            VStack(spacing: 4) {
                ZStack {
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(LinearGradient(colors: [Color.white.opacity(0.22), Palette.ink],
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
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(active ? Palette.accent : Palette.hairline,
                                  lineWidth: active ? 2 : 1))
                Text(label).font(.system(size: 10, weight: active ? .bold : .medium))
                    .foregroundStyle(active ? Palette.accent : Palette.textTertiary)
            }
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("editingStyle.capStyle.\(style?.rawValue ?? "auto")")
    }

    // MARK: - Filler

    private var fillerSection: some View {
        @Bindable var store = store
        return group("Filler") {
            VStack(alignment: .leading, spacing: Space.sm) {
                Text("How hard to cut the ums, restarts and dead air.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                MarqueSegmented(options: FillerTrim.allCases.map(\.label),
                                index: Binding(
                                    get: { FillerTrim.allCases.firstIndex(of: store.editPrefs.fillerTrim) ?? 0 },
                                    set: { store.editPrefs.fillerTrim = FillerTrim.allCases[$0] }))
                    .accessibilityIdentifier("editingStyle.fillerTrim")
            }
            .padding(.horizontal, Space.md).padding(.vertical, 10)
        }
    }

    // MARK: - B-roll style

    @ViewBuilder private var brollSection: some View {
        if !brollStyles.isEmpty {
            group("B-roll style") {
                VStack(alignment: .leading, spacing: Space.sm) {
                    Text("What a cutaway looks like when your edit calls for one.")
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: Space.sm) {
                            ForEach(brollStyles) { s in brollCard(s) }
                        }
                    }
                }
                .padding(.horizontal, Space.md).padding(.vertical, 10)
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
            VStack(alignment: .leading, spacing: 4) {
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
                                .foregroundStyle(Palette.textTertiary))
                    }
                    .frame(width: 110, height: 138).clipped()
                    if selected {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundStyle(Palette.accent)
                            .background(Circle().fill(.white).padding(2))
                            .padding(5)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: Radius.sm, style: .continuous))
                Text(s.label).font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Palette.textPrimary).lineLimit(1)
                Text(s.blurb).font(.system(size: 10)).foregroundStyle(Palette.textTertiary)
                    .lineLimit(2, reservesSpace: true).multilineTextAlignment(.leading)
            }
            .frame(width: 110)
            .padding(4)
            .overlay(RoundedRectangle(cornerRadius: Radius.md, style: .continuous)
                .strokeBorder(selected ? Palette.accent : .clear, lineWidth: 2))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("editingStyle.brollStyle.\(s.id)")
    }

    // MARK: - Meme energy

    private var memeSection: some View {
        let level = Binding<Double>(
            get: { Double(store.editPrefs.memeIntensity ?? SubmitConfig.defaultMemeIntensity) },
            set: { store.editPrefs.memeIntensity = Int($0) })
        return group("Meme energy") {
            VStack(alignment: .leading, spacing: Space.sm) {
                HStack {
                    Text("How culturally unhinged the cutaways get.")
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    Spacer(minLength: Space.md)
                    Text(MemeEnergy.name(Int(level.wrappedValue)))
                        .font(AppFont.callout).foregroundStyle(Palette.accent)
                }
                Slider(value: level, in: 0...3, step: 1)
                    .tint(Palette.ink)
                    .accessibilityIdentifier("editingStyle.memeLevel")
            }
            .padding(.horizontal, Space.md).padding(.vertical, 10)
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

        return group("What gets sent") {
            VStack(alignment: .leading, spacing: Space.sm) {
                Text(store.editPrefs.styleProfile == nil
                     ? "You haven't taken the taste quiz — these are the proven defaults."
                     : "Your taste, resolved into the settings your editor actually receives. A dial you set by hand always wins.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                FlowWrap(spacing: 6) {
                    ForEach(keys, id: \.self) { k in
                        // `explicit` wins the display exactly the way it wins on the wire.
                        resolvedChip(key: k, value: explicit[k] ?? mapped[k] ?? "",
                                     byHand: explicit[k] != nil)
                    }
                }
            }
            .padding(.horizontal, Space.md).padding(.vertical, 10)
        }
    }

    private func resolvedChip(key: String, value: String, byHand: Bool) -> some View {
        HStack(spacing: 4) {
            Text(key.replacingOccurrences(of: "_", with: " "))
                .font(Typeface.sans(10, .regular)).foregroundStyle(Palette.textTertiary)
            Text(value)
                .font(Typeface.sans(10, .semibold))
                .foregroundStyle(byHand ? Palette.accent : Palette.textPrimary)
        }
        .padding(.horizontal, 9).padding(.vertical, 5)
        .background(Capsule().fill(Palette.surfaceSunken))
        .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
    }

    // MARK: - Retake

    private var retakeSection: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            GhostButton(title: "Retake the taste quiz", systemImage: "arrow.counterclockwise") {
                showRetake = true
            }
            .accessibilityIdentifier("editingStyle.retake")
            if store.editPrefs.styleProfile?.handTuned == true {
                // Never silently overwrite hand-tuned work — say what a retake costs.
                Text("You've tuned these by hand — a retake replaces the learned part of your profile.")
                    .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var retakeSheet: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: Space.lg) {
                    Text("Swipe right on the looks you'd actually post.")
                        .font(AppFont.body).foregroundStyle(Palette.textSecondary)
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
            .navigationTitle("Which edits feel like you?")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { showRetake = false }
                }
            }
        }
    }

    // MARK: - Chrome

    @ViewBuilder
    private func group<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(title.uppercased())
                .font(AppFont.micro).tracking(Track.label).foregroundStyle(Palette.textTertiary)
                .padding(.bottom, Space.sm)
            VStack(spacing: 0) { content() }
                .background(Palette.surfaceRaised)
                .clipShape(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Radius.lg, style: .continuous)
                    .strokeBorder(Palette.hairline, lineWidth: 1))
        }
    }
}
