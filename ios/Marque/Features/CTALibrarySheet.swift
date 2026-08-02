import SwiftUI
import PhotosUI

// The CTA library — add / edit / delete the endings the record screen offers as one-tap
// tiles. Structurally a sibling of PillarsEditorSheet: a local `draft` array edited in
// place, committed on Done, so a half-typed CTA never reaches the store (or a submit).
//
// The FIRST entry is the default — it is what a fresh take arrives pre-selected with — so
// reordering is a real action, not decoration; hence the "Make default" affordance.
struct CTALibrarySheet: View {
    let store: AppStore
    /// Handed the committed library so the caller can re-point a selection at a CTA that
    /// still exists (the one it had chosen may have just been deleted here).
    var onCommit: ([SavedCTA]) -> Void = { _ in }

    @Environment(\.dismiss) private var dismiss
    @State private var draft: [SavedCTA] = []
    @State private var styles: [CTAStyleOption] = []
    @FocusState private var focusedNew: UUID?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: Space.md) {
                    Text("Your endings. The first one is the default on every new take — the rest are one tap away.")
                        .font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)

                    ForEach($draft) { $cta in
                        CTAEditRow(cta: $cta,
                                   styles: styles,
                                   isDefault: draft.first?.id == cta.id,
                                   focusedNew: $focusedNew,
                                   onMakeDefault: { makeDefault(cta.id) },
                                   onDelete: { draft.removeAll { $0.id == cta.id } })
                    }

                    if draft.isEmpty {
                        Text("Nothing saved yet — your videos end clean.")
                            .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                            .frame(maxWidth: .infinity, alignment: .center)
                            .padding(.vertical, Space.lg)
                    }

                    if draft.count < 8 {
                        GhostButton(title: "Add an ending", systemImage: "plus") { addCTA() }
                            .accessibilityIdentifier("cta.add")
                    }
                }
                .screenPadding().padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("Your CTAs")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { commit(); dismiss() }.fontWeight(.semibold)
                        .accessibilityIdentifier("cta.done")
                }
            }
        }
        .onAppear { if draft.isEmpty { draft = store.brand.savedCTAs ?? [] } }
        .task { if styles.isEmpty { styles = await store.backend.ctaStyles().filter { !$0.isNone } } }
    }

    private func addCTA() {
        // Seed from the catalog's first real template rather than a hard-coded id — the
        // catalog is the source of truth and its lead entry is the restrained default.
        let style = styles.first
        let cta = SavedCTA(name: style?.label ?? "Ending",
                           text: style.map { SavedCTA.defaultCopy(for: $0) } ?? "Follow for more",
                           handle: store.brand.pageHandle.isEmpty ? "" : "@" + store.brand.pageHandle,
                           logoURL: "", styleId: style?.id ?? "classic")
        draft.append(cta)
        focusedNew = cta.id
    }

    private func makeDefault(_ id: UUID) {
        guard let i = draft.firstIndex(where: { $0.id == id }), i != 0 else { return }
        withAnimation(Motion.spring) { draft.move(fromOffsets: IndexSet(integer: i), toOffset: 0) }
    }

    /// Drop endings with no words (a template with no copy renders a blank card) and
    /// persist. An emptied library stores nil, not [] — "never set one" and "deleted them
    /// all" behave identically downstream, so the simpler shape wins.
    private func commit() {
        let kept = draft.filter { !$0.text.trimmingCharacters(in: .whitespaces).isEmpty }
        store.brand.savedCTAs = kept.isEmpty ? nil : kept
        store.save()
        onCommit(kept)
    }
}

private struct CTAEditRow: View {
    @Binding var cta: SavedCTA
    let styles: [CTAStyleOption]
    let isDefault: Bool
    var focusedNew: FocusState<UUID?>.Binding
    let onMakeDefault: () -> Void
    let onDelete: () -> Void

    @State private var confirmDelete = false
    @State private var logoItem: PhotosPickerItem? = nil
    @State private var logoUploading = false
    @State private var logoFailed = false

    /// The slots the picked template actually renders. Fields for slots it ignores are
    /// hidden rather than disabled — a field that silently does nothing is worse than
    /// no field at all.
    private var params: [String] {
        styles.first { $0.id == cta.styleId }?.params ?? ["text", "handle", "logo"]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.sm) {
            HStack(spacing: Space.sm) {
                TextField("Name", text: $cta.name)
                    .font(AppFont.headline).foregroundStyle(Palette.textPrimary)
                    .focused(focusedNew, equals: cta.id)
                    .accessibilityIdentifier("cta.name")
                Spacer(minLength: 0)
                if isDefault {
                    Chip(text: "Default", tint: Palette.accent)
                } else {
                    Button { onMakeDefault() } label: {
                        Text("Make default")
                            .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("cta.makeDefault")
                }
                Button { confirmDelete = true } label: {
                    Image(systemName: "trash").font(.system(size: 14))
                        .foregroundStyle(Palette.textTertiary)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("cta.delete")
                .marqueConfirm($confirmDelete, title: "Delete this ending?",
                               confirm: "Delete", destructive: true) { onDelete() }
            }

            if params.contains("text") {
                TextField("Your call to action", text: $cta.text, axis: .vertical)
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary).lineLimit(1...2)
                    .accessibilityIdentifier("cta.text")
            }
            if params.contains("handle") {
                TextField("@handle (optional)", text: $cta.handle)
                    .font(AppFont.body).foregroundStyle(Palette.textSecondary)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("cta.handle")
            }
            if params.contains("logo") { logoRow }

            if !styles.isEmpty {
                VStack(alignment: .leading, spacing: Space.xs) {
                    Text("TEMPLATE").font(AppFont.micro).tracking(Track.label)
                        .foregroundStyle(Palette.textTertiary)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: Space.sm) {
                            ForEach(styles) { s in
                                styleChip(s)
                            }
                        }
                    }
                }
            }
        }
        .marqueCard(padding: Space.md)
        .onChange(of: logoItem) { _, item in
            if let item { Task { await uploadLogo(item) } }
        }
    }

    private func styleChip(_ s: CTAStyleOption) -> some View {
        let active = cta.styleId == s.id
        return Button {
            withAnimation(.easeOut(duration: 0.12)) { cta.styleId = s.id }
        } label: {
            Text(s.label)
                .font(Typeface.sans(11, active ? .semibold : .regular))
                .foregroundStyle(active ? Palette.onInk : Palette.textSecondary)
                .lineLimit(1)
                .padding(.horizontal, 12).padding(.vertical, 7)
                .background(Capsule().fill(active ? Palette.ink : Palette.surfaceRaised))
                .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("cta.style.\(s.id)")
    }

    private var logoRow: some View {
        HStack(spacing: Space.sm) {
            PhotosPicker(selection: $logoItem, matching: .images) {
                ZStack {
                    Circle().fill(Palette.surfaceSunken)
                    if logoUploading {
                        ProgressView().controlSize(.small)
                    } else if !cta.logoURL.isEmpty, let url = URL(string: cta.logoURL) {
                        AsyncImage(url: url) { $0.resizable().scaledToFill() } placeholder: {
                            ProgressView().controlSize(.mini)
                        }
                        .frame(width: 34, height: 34).clipShape(Circle())
                    } else {
                        Image(systemName: "plus").font(.system(size: 13, weight: .medium))
                            .foregroundStyle(Palette.textTertiary)
                    }
                }
                .frame(width: 34, height: 34)
                .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
            }
            .accessibilityIdentifier("cta.logo")
            Text(logoFailed ? "Couldn't add that logo — try another image."
                            : (cta.logoURL.isEmpty ? "Add a logo (optional)" : "Logo added"))
                .font(AppFont.caption)
                .foregroundStyle(logoFailed ? Palette.critical : Palette.textTertiary)
            Spacer(minLength: 0)
            if !cta.logoURL.isEmpty {
                Button { cta.logoURL = "" } label: {
                    Text("Remove").font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func uploadLogo(_ item: PhotosPickerItem) async {
        logoUploading = true
        logoFailed = false
        defer { logoUploading = false; logoItem = nil }
        guard let data = try? await item.loadTransferable(type: Data.self),
              let img = UIImage(data: data) else { logoFailed = true; return }
        // Build 55 (audit): the picker hands back the ORIGINAL bytes — iPhone photos are
        // HEIC, and Chromium (the Lambda renderer) cannot decode HEIC, so a raw
        // pass-through uploaded fine and then rendered a BLANK logo. Re-encode to real PNG,
        // downscaled to 512px (it renders at 168px).
        let scaled = img.preparingThumbnail(of: Self.fit(img.size, maxEdge: 512)) ?? img
        guard let png = scaled.pngData() else { logoFailed = true; return }
        let path = MediaStore.save(png, ext: "png")
        if let url = await LiveClipEngine.uploadMedia(path: path, filename: "cta-logo.png") {
            cta.logoURL = url
        } else {
            logoFailed = true
        }
    }

    /// Aspect-preserving fit within maxEdge — preparingThumbnail stretches to the exact
    /// size it's given, so the target must already carry the aspect ratio.
    private static func fit(_ size: CGSize, maxEdge: CGFloat) -> CGSize {
        let m = max(size.width, size.height)
        guard m > maxEdge, m > 0 else { return size }
        let k = maxEdge / m
        return CGSize(width: size.width * k, height: size.height * k)
    }
}
