import SwiftUI
import PhotosUI

// MARK: - Add to chat (build 66)

/// Two attach sources, one toggle: videos from Photos, or a clip already in the library.
struct ChatAttachSheet: View {
    @Environment(AppStore.self) private var store
    let onPhotos: () -> Void
    let onLibraryClip: (Clip) -> Void

    @State private var source = 0   // 0 Photos · 1 Your library

    private var libraryClips: [Clip] {
        store.clips.filter { c in
            guard c.status == .ready || c.status == .scheduled || c.status == .posted else { return false }
            return [c.localVideoPath, c.renderLocalPath].compactMap({ $0 })
                .contains(where: { FileManager.default.fileExists(atPath: MediaStore.url(for: $0).path) })
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.lg) {
            VStack(alignment: .leading, spacing: 4) {
                Text("ADD TO CHAT").font(AppFont.micro).tracking(Track.label)
                    .foregroundStyle(Palette.textTertiary)
                Text("Attach a video").font(Typeface.display(24)).foregroundStyle(Palette.textPrimary)
            }
            MarqueSegmented(options: ["Photos", "Your library"], index: $source)
                .accessibilityIdentifier("chat.attachSource")
            if source == 0 {
                VStack(spacing: Space.md) {
                    Image(systemName: "photo.on.rectangle")
                        .font(.system(size: 26, weight: .ultraLight))
                        .foregroundStyle(Palette.textTertiary)
                    Text("Pick up to 4 videos from your camera roll — Yunicorn stitches and edits them.")
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.center)
                    PrimaryButton(title: "Choose from Photos", systemImage: "photo") { onPhotos() }
                        .accessibilityIdentifier("chat.attachPhotos")
                }
                .frame(maxWidth: .infinity)
                .padding(.top, Space.xl)
            } else if libraryClips.isEmpty {
                VStack(spacing: Space.sm) {
                    Image(systemName: "rectangle.stack")
                        .font(.system(size: 26, weight: .ultraLight))
                        .foregroundStyle(Palette.textTertiary)
                    Text("Nothing in your library yet — film or upload a clip first.")
                        .font(AppFont.caption).foregroundStyle(Palette.textSecondary)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity)
                .padding(.top, Space.xl)
            } else {
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 100), spacing: Space.sm)],
                              spacing: Space.sm) {
                        ForEach(libraryClips) { c in
                            Button { onLibraryClip(c) } label: {
                                ZStack(alignment: .bottomLeading) {
                                    Color.clear
                                        .aspectRatio(9.0 / 16.0, contentMode: .fit)
                                        .overlay(LocalThumbnail(path: c.thumbnailPath ?? c.playbackLocalPath,
                                                                isVideo: true, remoteImageURL: c.thumbnailURL)
                                            .scaledToFill())
                                        .clipShape(RoundedRectangle(cornerRadius: Radius.md, style: .continuous))
                                    Text(c.title.isEmpty ? c.formatName : c.title)
                                        .font(Typeface.sans(11, .medium)).lineLimit(1)
                                        .foregroundStyle(.white)
                                        .shadow(color: .black.opacity(0.6), radius: 3, y: 1)
                                        .padding(6)
                                }
                            }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("chat.attachClip")
                        }
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(Space.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Palette.canvas)
    }
}
