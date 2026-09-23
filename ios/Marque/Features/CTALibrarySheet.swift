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
                    Text("Your endings. The first one is the default on every new take, and the rest are one tap away.")
                        .font(AppFont.supporting).foregroundStyle(Palette.textSecondary)
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
                        // Stoic outline (empty) card with a centered secondary line.
                        Text("Nothing saved yet, so your videos end clean.")
                            .font(AppFont.bodyText).foregroundStyle(Palette.textSecondary)
                            .multilineTextAlignment(.center)
                            .frame(maxWidth: .infinity, alignment: .center)
                            .dsCard(.outline, radius: Radius.group)
                    }

                    if draft.count < 8 {
                        GhostButton(title: "Add an ending", systemImage: "plus") { addCTA() }
                            .accessibilityIdentifier("cta.add")
                    }
                }
                .padding(.horizontal, Space.screenH).padding(.vertical, Space.lg)
            }
            .background(Palette.canvas.ignoresSafeArea())
            .navigationTitle("your ctas.")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }.tint(Palette.textPrimary)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { commit(); dismiss() }.fontWeight(.semibold)
                        .tint(Palette.textPrimary)
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
                    .font(AppFont.title3).foregroundStyle(Palette.textPrimary)
                    .focused(focusedNew, equals: cta.id)
                    .accessibilityIdentifier("cta.name")
                Spacer(minLength: 0)
                if isDefault {
                    // Selected = inversion: an ink capsule tag (was an accent-tinted chip).
                    Text("Default")
                        .font(AppFont.caption.weight(.semibold)).foregroundStyle(Palette.onInk)
                        .padding(.horizontal, 10).frame(height: 26)
                        .background(Capsule().fill(Palette.ink))
                } else {
                    Button { onMakeDefault() } label: {
                        Text("Make default")
                            .font(AppFont.caption.weight(.semibold)).foregroundStyle(Palette.textPrimary)
                            .padding(.horizontal, 10).frame(height: 26)
                            .overlay(Capsule().strokeBorder(Palette.hairline, lineWidth: 1))
                            .contentShape(Capsule())
                    }
                    .buttonStyle(PressableStyle(dim: 0.7))
                    .accessibilityIdentifier("cta.makeDefault")
                }
                Button { confirmDelete = true } label: {
                    Image(systemName: "trash").font(.system(size: 15, weight: .regular))
                        .foregroundStyle(Palette.textPrimary)
                        .frame(width: 32, height: 32).contentShape(Rectangle())
                }
                .buttonStyle(PressableStyle(dim: 0.6))
                .accessibilityLabel("Delete")
                .accessibilityIdentifier("cta.delete")
                .marqueConfirm($confirmDelete, title: "Delete this ending?",
                               confirm: "Delete", destructive: true) { onDelete() }
            }

            if params.contains("text") {
                TextField("Your call to action", text: $cta.text, axis: .vertical)
                    .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary).lineLimit(1...2)
                    .accessibilityIdentifier("cta.text")
            }
            if params.contains("handle") {
                TextField("@handle (optional)", text: $cta.handle)
                    .font(AppFont.bodyText).foregroundStyle(Palette.textPrimary)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("cta.handle")
            }
            if params.contains("logo") { logoRow }

            if !styles.isEmpty {
                VStack(alignment: .leading, spacing: Space.xs) {
                    DSEyebrow(text: "TEMPLATE")
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
        .dsCard(.surface, radius: Radius.group, padding: Space.md)
        .onChange(of: logoItem) { _, item in
            if let item { Task { await uploadLogo(item) } }
        }
    }

    private func styleChip(_ s: CTAStyleOption) -> some View {
        let active = cta.styleId == s.id
        return Button {
            withAnimation(.easeOut(duration: 0.12)) { cta.styleId = s.id }
        } label: {
            // DESIGN.md chip: surface + hairline; selected = ink / onInk.
            Text(s.label)
                .font(AppFont.caption.weight(active ? .semibold : .regular))
                .foregroundStyle(active ? Palette.onInk : Palette.textPrimary)
                .lineLimit(1)
                .padding(.horizontal, 14).frame(height: 34)
                .background(Capsule().fill(active ? Palette.ink : Palette.surface))
                .overlay(Capsule().strokeBorder(active ? Color.clear : Palette.hairline, lineWidth: 1))
                .contentShape(Capsule())
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
                        .frame(width: 40, height: 40).clipShape(Circle())
                    } else {
                        Image(systemName: "plus").font(.system(size: 14, weight: .regular))
                            .foregroundStyle(Palette.textPrimary)
                    }
                }
                .frame(width: 40, height: 40)
                .overlay(Circle().strokeBorder(Palette.hairline, lineWidth: 1))
            }
            .accessibilityIdentifier("cta.logo")
            // Failure reads by glyph + wording, not red.
            if logoFailed {
                Image(systemName: "exclamationmark.circle").font(.system(size: 13, weight: .regular))
                    .foregroundStyle(Palette.textPrimary)
            }
            Text(logoFailed ? "Couldn't add that logo. Try another image."
                            : (cta.logoURL.isEmpty ? "Add a logo (optional)" : "Logo added"))
                .font(AppFont.caption)
                .foregroundStyle(logoFailed ? Palette.textPrimary : Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
            if !cta.logoURL.isEmpty {
                Button { cta.logoURL = "" } label: {
                    Text("Remove").font(AppFont.caption.weight(.semibold)).foregroundStyle(Palette.textPrimary)
                        .frame(minHeight: 32).contentShape(Rectangle())
                }
                .buttonStyle(PressableStyle(dim: 0.6))
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
