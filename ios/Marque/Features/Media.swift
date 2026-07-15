import SwiftUI
import UIKit
import AVKit
import AVFoundation
import PhotosUI
import UniformTypeIdentifiers
import CryptoKit

// MARK: - Local media helpers (thumbnails, playback, bulk import to the app container)

enum MediaStore {
    private static var documents: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    /// Resolve a stored path (relative to Documents, or absolute) to a file URL.
    static func url(for relativePath: String) -> URL {
        relativePath.hasPrefix("/")
            ? URL(fileURLWithPath: relativePath)
            : documents.appendingPathComponent(relativePath)
    }

    /// Persist data into media/<uuid>.<ext>; returns the Documents-relative path.
    static func save(_ data: Data, ext: String) -> String {
        let dir = documents.appendingPathComponent("media", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let name = "media/\(UUID().uuidString).\(ext)"
        try? data.write(to: documents.appendingPathComponent(name))
        return name
    }

    /// Copy an existing file into media/<uuid>.<ext> WITHOUT loading it into memory —
    /// required for library videos, which can be hundreds of MB (Data(contentsOf:) on
    /// those gets the app memory-killed). Returns the Documents-relative path, or nil.
    static func saveFile(from src: URL, ext: String) -> String? {
        let dir = documents.appendingPathComponent("media", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let name = "media/\(UUID().uuidString).\(ext)"
        do {
            try FileManager.default.copyItem(at: src, to: documents.appendingPathComponent(name))
            return name
        } catch {
            return nil
        }
    }

    /// Poster frame from a video file (sync — call off the main actor).
    static func poster(for url: URL) -> UIImage? {
        let gen = AVAssetImageGenerator(asset: AVURLAsset(url: url))
        gen.appliesPreferredTrackTransform = true
        gen.maximumSize = CGSize(width: 480, height: 480)
        let t = CMTime(seconds: 0.1, preferredTimescale: 600)
        guard let cg = try? gen.copyCGImage(at: t, actualTime: nil) else { return nil }
        return UIImage(cgImage: cg)
    }
}

/// File-URL transferable for picked library videos. PhotosPicker's
/// loadTransferable(type: Data.self) materializes the ENTIRE asset in RAM — fine for a
/// 10s selfie, a memory-kill/timeout for a real multi-minute library video, which is
/// exactly the "upload existing footage" case. FileRepresentation streams to a temp
/// file instead; we copy it out before the transfer's sandbox URL is reclaimed.
struct PickedVideoFile: Transferable {
    let url: URL
    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { file in
            SentTransferredFile(file.url)
        } importing: { received in
            let ext = received.file.pathExtension.isEmpty ? "mov" : received.file.pathExtension
            let dest = FileManager.default.temporaryDirectory
                .appendingPathComponent("picked-\(UUID().uuidString).\(ext)")
            try FileManager.default.copyItem(at: received.file, to: dest)
            return Self(url: dest)
        }
    }
}

/// Bulk-import picked photos/videos into the app container as MediaAssets.
/// I-5: stamps a real contentHash (so lazy analysis can run later) and pre-generates a
/// video poster at import — but does NOT upload or analyze here (that's deferred until needed).
func importPickedMedia(_ items: [PhotosPickerItem]) async -> [MediaAsset] {
    var out: [MediaAsset] = []
    for item in items {
        let isVideo = item.supportedContentTypes.contains { $0.conforms(to: .movie) || $0.conforms(to: .video) }
        guard let data = try? await item.loadTransferable(type: Data.self) else { continue }
        let path = MediaStore.save(data, ext: isVideo ? "mov" : "jpg")
        let hash = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        var asset = MediaAsset(localPath: path, kind: isVideo ? .clip : .selfie, isVideo: isVideo)
        asset.contentHash = hash
        if isVideo, let poster = MediaStore.poster(for: MediaStore.url(for: path)),
           let jpg = poster.jpegData(compressionQuality: 0.7) {
            asset.thumbnailPath = MediaStore.save(jpg, ext: "jpg")
        }
        out.append(asset)
    }
    return out
}

/// Thumbnail for a local image or video path, or a remote poster image. Prefers the
/// server-generated poster (remoteImageURL) when present — that's what fills a
/// server-rendered clip's Library card, whose local render/raw-take poster was cleared
/// on render. Falls back to a local poster, then a play-icon placeholder.
struct LocalThumbnail: View {
    let path: String?
    var isVideo: Bool = false
    var remoteImageURL: String? = nil
    var cornerRadius: CGFloat = Radius.sm
    @State private var image: UIImage?
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: cornerRadius, style: .continuous).fill(Palette.surfaceSunken)
            if let image {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Image(systemName: isVideo ? "play.fill" : "photo")
                    .font(.system(size: 16)).foregroundStyle(Palette.textTertiary)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
        .task(id: (remoteImageURL ?? "") + (path ?? "")) { await load() }
    }
    private func load() async {
        guard image == nil else { return }
        // 1) server poster image (jpg over http) — the primary path for rendered clips
        if let remoteImageURL, let u = URL(string: remoteImageURL) {
            if let (data, _) = try? await URLSession.shared.data(from: u), let img = UIImage(data: data) {
                image = img
                return
            }
        }
        // 2) local file (raw take poster / imported still)
        guard let path, !path.isEmpty else { return }
        let url = MediaStore.url(for: path)
        if isVideo {
            let img = await Task.detached(priority: .utility) { MediaStore.poster(for: url) }.value
            image = img
        } else if let data = try? Data(contentsOf: url), let img = UIImage(data: data) {
            image = img
        }
    }
}

/// Inline player for a local (relative/absolute) path or a remote URL.
struct LocalVideoPlayer: View {
    let path: String?
    var remoteURL: String? = nil
    var body: some View {
        Group {
            if let url = resolved {
                VideoPlayer(player: AVPlayer(url: url))
            } else {
                ZStack {
                    Palette.surfaceSunken
                    VStack(spacing: Space.sm) {
                        Image(systemName: "video.slash").font(.system(size: 24)).foregroundStyle(Palette.textTertiary)
                        Text("Preview unavailable").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                    }
                }
            }
        }
    }
    private var resolved: URL? {
        if let path, !path.isEmpty { return MediaStore.url(for: path) }
        if let remoteURL, let u = URL(string: remoteURL) { return u }
        return nil
    }
}

/// Polished clip preview: the in-house InkVideoPlayer (fill gravity + custom controls)
/// instead of AVKit's letterboxing VideoPlayer. Resolves a local path or remote URL the
/// same way LocalVideoPlayer does; falls back to an "unavailable" panel. Meant to sit in
/// a 9:16 container so the render fills with no black bars.
struct ClipPreviewPlayer: View {
    let path: String?
    var remoteURL: String? = nil
    var body: some View {
        if let url = resolved {
            InkVideoPlayer(url: url, loops: false, startMuted: false)
        } else {
            ZStack {
                Palette.surfaceSunken
                VStack(spacing: Space.sm) {
                    Image(systemName: "video.slash").font(.system(size: 24)).foregroundStyle(Palette.textTertiary)
                    Text("Preview unavailable").font(AppFont.caption).foregroundStyle(Palette.textTertiary)
                }
            }
        }
    }
    private var resolved: URL? {
        if let path, !path.isEmpty { return MediaStore.url(for: path) }
        if let remoteURL, let u = URL(string: remoteURL) { return u }
        return nil
    }
}
