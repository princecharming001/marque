import AVFoundation
import CoreGraphics

// Concatenates the segments of a multi-take recording (pause → resume, possibly
// from a different camera angle) into one continuous .mov ON DEVICE, so the
// backend still receives a single source_url — zero backend change. A single
// segment exports pass-through; mixed front/back takes get per-segment transform
// instructions so each angle keeps its correct orientation.
enum VideoStitcher {

    /// Stitch `segments` (in order) into one file. Returns the output URL, or nil
    /// on failure. Async via a continuation over AVAssetExportSession.
    static func stitch(_ segments: [URL]) async -> URL? {
        let existing = segments.filter { FileManager.default.fileExists(atPath: $0.path) }
        guard !existing.isEmpty else { return nil }
        if existing.count == 1 { return existing[0] }   // nothing to stitch

        let composition = AVMutableComposition()
        guard let vTrack = composition.addMutableTrack(withMediaType: .video,
                                                       preferredTrackID: kCMPersistentTrackID_Invalid) else {
            return nil
        }
        let aTrack = composition.addMutableTrack(withMediaType: .audio,
                                                 preferredTrackID: kCMPersistentTrackID_Invalid)

        var cursor = CMTime.zero
        var instructions: [AVMutableVideoCompositionInstruction] = []
        var renderSize = CGSize(width: 1080, height: 1920)
        var anyTransform = false

        for url in existing {
            let asset = AVURLAsset(url: url)
            guard let srcV = try? await asset.loadTracks(withMediaType: .video).first else { continue }
            let dur = (try? await asset.load(.duration)) ?? .zero
            guard dur.seconds > 0 else { continue }
            let range = CMTimeRange(start: .zero, duration: dur)
            do {
                try vTrack.insertTimeRange(range, of: srcV, at: cursor)
            } catch {
                continue
            }
            if let srcA = try? await asset.loadTracks(withMediaType: .audio).first, let aTrack {
                try? aTrack.insertTimeRange(range, of: srcA, at: cursor)
            }

            // Per-segment transform so each take renders upright regardless of
            // which camera captured it.
            let t = (try? await srcV.load(.preferredTransform)) ?? .identity
            if !t.isIdentity { anyTransform = true }
            let natural = (try? await srcV.load(.naturalSize)) ?? renderSize
            let transformed = natural.applying(t)
            renderSize = CGSize(width: abs(transformed.width), height: abs(transformed.height))

            let layer = AVMutableVideoCompositionLayerInstruction(assetTrack: vTrack)
            layer.setTransform(t, at: cursor)
            let inst = AVMutableVideoCompositionInstruction()
            inst.timeRange = CMTimeRange(start: cursor, duration: dur)
            inst.layerInstructions = [layer]
            instructions.append(inst)

            cursor = CMTimeAdd(cursor, dur)
        }
        guard cursor.seconds > 0 else { return nil }

        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".mov")

        // Pass-through when a single consistent orientation was used (fast, no
        // re-encode). A mixed-angle take needs a video composition to apply the
        // per-segment transforms.
        let presetName = anyTransform ? AVAssetExportPresetHighestQuality : AVAssetExportPresetPassthrough
        guard let export = AVAssetExportSession(asset: composition, presetName: presetName) else {
            return nil
        }
        export.outputURL = out
        export.outputFileType = .mov
        if anyTransform {
            let vc = AVMutableVideoComposition()
            vc.instructions = instructions
            vc.frameDuration = CMTime(value: 1, timescale: 30)
            vc.renderSize = renderSize
            export.videoComposition = vc
        }

        return await withCheckedContinuation { cont in
            export.exportAsynchronously {
                cont.resume(returning: export.status == .completed ? out : nil)
            }
        }
    }
}
