import Foundation

// MARK: - EditorDocument — the Swift mirror of the backend editorial EDL (app/edl.py:125-152)
//
// All frame coordinates are SOURCE-video frames at 30fps. The editor stages edits against a
// local copy of this document (instant preview), and on Save flushes the sequential op log to
// the server which re-applies them for real — so the server stays source of truth and we
// reload from GET after every Apply. See LocalEDLEngine for the op semantics we mirror.

let kEditorFPS: Double = 30.0

func framesToSeconds(_ f: Int) -> Double { Double(f) / kEditorFPS }
// AF-I5 lesson: banker's rounding to match Python's round() so caption/edit_caption frame
// keys agree across the client/server boundary.
func msToFrame(_ ms: Double) -> Int { Int((ms * kEditorFPS / 1000.0).rounded(.toNearestOrEven)) }
func secondsToFrame(_ s: Double) -> Int { Int((s * kEditorFPS).rounded(.toNearestOrEven)) }

struct EditorSegment: Equatable {
    var srcIn: Int
    var srcOut: Int
    var frames: Int { max(0, srcOut - srcIn) }
}

struct EditorDrop: Equatable {
    var srcIn: Int
    var srcOut: Int
    var reason: String
}

struct EditorCaption: Equatable {
    var word: String
    var frame: Int
}

struct EditorOverlay: Equatable {
    var type: String        // "punch_in" | "text_card"
    var srcIn: Int
    var srcOut: Int
    var scale: Double
    var text: String
}

struct EditorMusic: Equatable {
    var url: String
    var volume: Double
    var duckVoice: Bool
}

struct EditorVolumeRange: Equatable {
    var srcIn: Int
    var srcOut: Int
    var volume: Double
}

struct EditorDocument: Equatable {
    var style: String = "talking_head"
    var formatId: String = "myth-buster"
    var captionStyle: String = "clean"
    var segments: [EditorSegment] = []
    var drops: [EditorDrop] = []
    var captions: [EditorCaption] = []
    var overlays: [EditorOverlay] = []
    var volumeRanges: [EditorVolumeRange] = []
    var music: EditorMusic? = nil
    var segmentOrder: [Int]? = nil        // permutation of segment indices; nil = source order
    var speechFrames: [Int] = []          // for the L1 music duck-under-voice sim

    // MARK: JSON <-> document (parses the GET /v1/clips/{id} `edl` object)

    init() {}

    init(edl: [String: Any]) {
        style = edl["style"] as? String ?? "talking_head"
        formatId = edl["format_id"] as? String ?? "myth-buster"
        captionStyle = edl["caption_style"] as? String ?? "clean"
        segments = (edl["segments"] as? [[String: Any]] ?? []).compactMap {
            guard let a = $0["src_in"] as? Int, let b = $0["src_out"] as? Int else { return nil }
            return EditorSegment(srcIn: a, srcOut: b)
        }
        drops = (edl["drops"] as? [[String: Any]] ?? []).compactMap {
            guard let a = $0["src_in"] as? Int, let b = $0["src_out"] as? Int else { return nil }
            return EditorDrop(srcIn: a, srcOut: b, reason: $0["reason"] as? String ?? "manual")
        }
        captions = (edl["captions"] as? [[String: Any]] ?? []).compactMap {
            guard let w = $0["word"] as? String, let f = $0["frame"] as? Int else { return nil }
            return EditorCaption(word: w, frame: f)
        }
        overlays = (edl["overlays"] as? [[String: Any]] ?? []).compactMap {
            guard let a = $0["src_in"] as? Int, let b = $0["src_out"] as? Int else { return nil }
            return EditorOverlay(type: $0["type"] as? String ?? "punch_in", srcIn: a, srcOut: b,
                                 scale: ($0["scale"] as? Double) ?? 1.08, text: $0["text"] as? String ?? "")
        }
        if let order = edl["segment_order"] as? [Int] { segmentOrder = order }
        speechFrames = edl["speech_frames"] as? [Int] ?? []
        if let audio = edl["audio"] as? [String: Any] {
            if let m = audio["music"] as? [String: Any], let url = m["url"] as? String, !url.isEmpty {
                music = EditorMusic(url: url, volume: (m["volume"] as? Double) ?? 0.15,
                                    duckVoice: (m["duck_voice"] as? Bool) ?? true)
            }
            volumeRanges = (audio["volume_ranges"] as? [[String: Any]] ?? []).compactMap {
                guard let a = $0["src_in"] as? Int, let b = $0["src_out"] as? Int,
                      let v = $0["volume"] as? Double else { return nil }
                return EditorVolumeRange(srcIn: a, srcOut: b, volume: v)
            }
        }
    }

    // MARK: Derived timeline (port of _kept_intervals + segment_order walk, edl.py:277-317)

    /// Kept source intervals in PLAY order — segments (in segment_order) minus drops.
    var keptIntervals: [(srcIn: Int, srcOut: Int)] {
        let order = segmentOrder ?? Array(segments.indices)
        let dropRanges = drops.filter { $0.srcOut > $0.srcIn }
            .map { ($0.srcIn, $0.srcOut) }.sorted { $0.0 < $1.0 }
        var out: [(Int, Int)] = []
        for idx in order where segments.indices.contains(idx) {
            var cur = segments[idx].srcIn
            let end = segments[idx].srcOut
            for (dIn, dOut) in dropRanges {
                if dOut <= cur || dIn >= end { continue }
                if dIn > cur { out.append((cur, min(dIn, end))) }
                cur = max(cur, dOut)
                if cur >= end { break }
            }
            if cur < end { out.append((cur, end)) }
        }
        return out.filter { $0.1 > $0.0 }.map { (srcIn: $0.0, srcOut: $0.1) }
    }

    var totalKeptFrames: Int { keptIntervals.reduce(0) { $0 + ($1.srcOut - $1.srcIn) } }
    var outputSeconds: Double { framesToSeconds(totalKeptFrames) }

    /// Output-time (seconds) -> source-time (seconds). The Swift twin of map_point (edl.py).
    func sourceSeconds(forOutput outputSec: Double) -> Double {
        var acc = 0
        let target = secondsToFrame(outputSec)
        for iv in keptIntervals {
            let len = iv.srcOut - iv.srcIn
            if target < acc + len { return framesToSeconds(iv.srcIn + (target - acc)) }
            acc += len
        }
        return framesToSeconds(keptIntervals.last?.srcOut ?? 0)
    }
}
