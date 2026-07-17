import Foundation

// MARK: - EditorDocument — the Swift mirror of the backend editorial EDL (app/edl.py:125-152)
//
// All frame coordinates are SOURCE-video frames at 30fps. The editor stages edits against a
// local copy of this document (instant preview), and on Save flushes the sequential op log to
// the server which re-applies them for real — so the server stays source of truth and we
// reload from GET after every Apply. See LocalEDLEngine for the op semantics we mirror.

let kEditorFPS: Double = 30.0
// P0.3: kept clips shorter than this in OUTPUT frames (12 = 400ms) are dropped as slivers.
// Exact mirror of MIN_CLIP_OUTPUT_FRAMES in backend/app/edl.py.
let kMinClipOutputFrames = 12

func framesToSeconds(_ f: Int) -> Double { Double(f) / kEditorFPS }
// AF-I5 lesson: banker's rounding to match Python's round() so caption/edit_caption frame
// keys agree across the client/server boundary.
func msToFrame(_ ms: Double) -> Int { Int((ms * kEditorFPS / 1000.0).rounded(.toNearestOrEven)) }
func secondsToFrame(_ s: Double) -> Int { Int((s * kEditorFPS).rounded(.toNearestOrEven)) }

struct EditorSegment: Equatable {
    var srcIn: Int
    var srcOut: Int
    var speed: Double = 1.0            // playback rate; output duration = frames/speed
    // Canvas transform (pinch-zoom / drag the clip on the preview). Identity = untouched.
    var txScale: Double = 1.0
    var txX: Double = 0.0
    var txY: Double = 0.0
    var frames: Int { max(0, srcOut - srcIn) }
}

/// Speed-aware output length of a frame count — the Swift twin of the backend's
/// round(kept/speed) (banker's rounding on both sides so coords agree).
func outputFrames(_ frames: Int, speed: Double) -> Int {
    max(1, Int((Double(frames) / max(0.5, speed)).rounded(.toNearestOrEven)))
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
    var type: String        // "punch_in" | "text_card" | "text_sticker"
    var srcIn: Int
    var srcOut: Int
    var scale: Double
    var text: String
    // text_sticker placement + look (fractions of frame; ignored by other types)
    var posX: Double = 0.5
    var posY: Double = 0.5
    var rotation: Double = 0
    var color: String? = nil
    var bg: String = "none"          // none | box
    var font: String = "inter"
}

// One media roll (B-roll/C-roll…): a photo/video window over the base cut.
// `localPath` is set for freshly-imported media so the player sim can show it
// before the uploaded URL round-trips through the server.
struct EditorBroll: Equatable {
    var srcIn: Int
    var srcOut: Int
    var cueText: String = ""
    var source: String = "stock"        // stock | own_media | giphy | klipy
    var resolvedURL: String? = nil
    var localPath: String? = nil        // sim-only, never serialized
    // v4: the composition mode + smart-mode inset (normalized frame fractions) so the
    // canvas sim shows the roll at its TRUE position/size, not always full-frame.
    var mode: String = "full"           // full | panel | card | smart
    var insetX: Double? = nil
    var insetY: Double? = nil
    var insetW: Double? = nil
    var insetH: Double? = nil
}

struct EditorTransition: Equatable {
    var afterSegment: Int            // source index of the leading segment
    var style: String                // fade_black | fade_white | flash
    var frames: Int
}

struct EditorAdjust: Equatable {
    var brightness: Double = 0
    var contrast: Double = 0
    var saturation: Double = 0
    var temperature: Double = 0
    var vignette: Double = 0
    var isNeutral: Bool { self == EditorAdjust() }
}

struct EditorLook: Equatable {
    var filter: String? = nil        // vivid | film | mono | golden | warm | cool
    var intensity: Double = 1.0
    var adjust = EditorAdjust()
}

struct EditorMusic: Equatable {
    var url: String
    var volume: Double
    var duckVoice: Bool
}

// Caption tuning knobs under the style preset (mirror of backend CaptionOptions).
struct EditorCaptionOptions: Equatable {
    var position: String = "bottom"    // top | middle | bottom
    var size: String = "medium"        // small | medium | large
    var posY: Double? = nil            // canvas-drag override (fraction of height); nil = word
    var scale: Double? = nil           // pinch override (font multiplier); nil = word
    var accent: String? = nil          // #RRGGBB; nil = the style's own default
    var uppercase: Bool = false
    var font: String = "inter"         // inter | archivo | baloo | montserrat | anton (A2)
    var grouping: String = "phrase"    // word | phrase (DEFAULT, P0.7) | line — mirrors edl.py
    var highlightWords: [String] = []  // normalized keywords painted in the accent color
    var strokePx: Double = 0           // A2: dual-span outline width (Hormozi/Submagic look)
    var bg: String = ""                // v6: rounded background pill ("" = none; #RRGGBB[AA])
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
    var captionOptions = EditorCaptionOptions()
    var segments: [EditorSegment] = []
    var drops: [EditorDrop] = []
    var captions: [EditorCaption] = []
    var overlays: [EditorOverlay] = []
    var volumeRanges: [EditorVolumeRange] = []
    var music: EditorMusic? = nil
    var segmentOrder: [Int]? = nil        // permutation of segment indices; nil = source order
    var speechFrames: [Int] = []          // for the L1 music duck-under-voice sim
    var transitions: [EditorTransition] = []
    var look = EditorLook()
    var broll: [EditorBroll] = []

    // MARK: JSON <-> document (parses the GET /v1/clips/{id} `edl` object)

    init() {}

    init(edl: [String: Any]) {
        style = edl["style"] as? String ?? "talking_head"
        formatId = edl["format_id"] as? String ?? "myth-buster"
        captionStyle = edl["caption_style"] as? String ?? "clean"
        if let co = edl["caption_options"] as? [String: Any] {
            captionOptions = EditorCaptionOptions(
                position: co["position"] as? String ?? "bottom",
                size: co["size"] as? String ?? "medium",
                posY: co["pos_y"] as? Double,
                scale: co["scale"] as? Double,
                accent: co["accent"] as? String,
                uppercase: co["uppercase"] as? Bool ?? false,
                font: co["font"] as? String ?? "inter",
                grouping: co["grouping"] as? String ?? "phrase",
                highlightWords: co["highlight_words"] as? [String] ?? [],
                strokePx: co["stroke_px"] as? Double ?? 0,
                bg: co["bg"] as? String ?? "")
        }
        segments = (edl["segments"] as? [[String: Any]] ?? []).compactMap {
            guard let a = $0["src_in"] as? Int, let b = $0["src_out"] as? Int else { return nil }
            return EditorSegment(srcIn: a, srcOut: b, speed: $0["speed"] as? Double ?? 1.0,
                                 txScale: $0["tx_scale"] as? Double ?? 1.0,
                                 txX: $0["tx_x"] as? Double ?? 0.0,
                                 txY: $0["tx_y"] as? Double ?? 0.0)
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
                                 scale: ($0["scale"] as? Double) ?? 1.08, text: $0["text"] as? String ?? "",
                                 posX: $0["pos_x"] as? Double ?? 0.5,
                                 posY: $0["pos_y"] as? Double ?? 0.5,
                                 rotation: $0["rotation"] as? Double ?? 0,
                                 color: $0["color"] as? String,
                                 bg: $0["bg"] as? String ?? "none",
                                 font: $0["font"] as? String ?? "inter")
        }
        if let order = edl["segment_order"] as? [Int] { segmentOrder = order }
        speechFrames = edl["speech_frames"] as? [Int] ?? []
        broll = (edl["broll"] as? [[String: Any]] ?? []).compactMap {
            guard let a = $0["src_in"] as? Int, let b = $0["src_out"] as? Int else { return nil }
            let inset = $0["inset_rect"] as? [String: Any]
            return EditorBroll(srcIn: a, srcOut: b,
                               cueText: $0["cue_text"] as? String ?? "",
                               source: $0["source"] as? String ?? "stock",
                               resolvedURL: $0["resolved_url"] as? String,
                               mode: $0["mode"] as? String ?? "full",
                               insetX: (inset?["x"] as? NSNumber)?.doubleValue,
                               insetY: (inset?["y"] as? NSNumber)?.doubleValue,
                               insetW: (inset?["w"] as? NSNumber)?.doubleValue,
                               insetH: (inset?["h"] as? NSNumber)?.doubleValue)
        }
        transitions = (edl["transitions"] as? [[String: Any]] ?? []).compactMap {
            guard let a = $0["after_segment"] as? Int else { return nil }
            return EditorTransition(afterSegment: a, style: $0["style"] as? String ?? "fade_black",
                                    frames: $0["frames"] as? Int ?? 12)
        }
        if let lk = edl["look"] as? [String: Any] {
            var adj = EditorAdjust()
            if let a = lk["adjust"] as? [String: Any] {
                adj = EditorAdjust(brightness: a["brightness"] as? Double ?? 0,
                                   contrast: a["contrast"] as? Double ?? 0,
                                   saturation: a["saturation"] as? Double ?? 0,
                                   temperature: a["temperature"] as? Double ?? 0,
                                   vignette: a["vignette"] as? Double ?? 0)
            }
            look = EditorLook(filter: lk["filter"] as? String,
                              intensity: lk["intensity"] as? Double ?? 1.0, adjust: adj)
        }
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

    /// Kept source intervals in PLAY order — segments (in segment_order) minus drops —
    /// each carrying its segment's playback speed (output duration = kept/speed).
    var keptIntervalsWithSpeed: [(srcIn: Int, srcOut: Int, speed: Double)] {
        let order = segmentOrder ?? Array(segments.indices)
        let dropRanges = drops.filter { $0.srcOut > $0.srcIn }
            .map { ($0.srcIn, $0.srcOut) }.sorted { $0.0 < $1.0 }
        var out: [(Int, Int, Double)] = []
        for idx in order where segments.indices.contains(idx) {
            let speed = min(3.0, max(0.5, segments[idx].speed))
            var cur = segments[idx].srcIn
            let end = segments[idx].srcOut
            for (dIn, dOut) in dropRanges {
                if dOut <= cur || dIn >= end { continue }
                if dIn > cur { out.append((cur, min(dIn, end), speed)) }
                cur = max(cur, dOut)
                if cur >= end { break }
            }
            if cur < end { out.append((cur, end, speed)) }
        }
        let candidates = out.filter { $0.1 > $0.0 }
        // P0.3 min-clip guard — exact mirror of build_render_plan (edl.py): drop kept
        // intervals whose OUTPUT length is < 12 frames (400ms slivers), but never empty
        // the plan — if every candidate is a sliver, keep the single longest (first on ties,
        // matching Python's max()).
        let kept = candidates.filter { outputFrames($0.1 - $0.0, speed: $0.2) >= kMinClipOutputFrames }
        let final: [(Int, Int, Double)]
        if !kept.isEmpty {
            final = kept
        } else if var best = candidates.first {
            for c in candidates.dropFirst()
            where outputFrames(c.1 - c.0, speed: c.2) > outputFrames(best.1 - best.0, speed: best.2) {
                best = c
            }
            final = [best]
        } else {
            final = []
        }
        return final.map { (srcIn: $0.0, srcOut: $0.1, speed: $0.2) }
    }

    var keptIntervals: [(srcIn: Int, srcOut: Int)] {
        keptIntervalsWithSpeed.map { (srcIn: $0.srcIn, srcOut: $0.srcOut) }
    }

    var totalKeptFrames: Int { keptIntervals.reduce(0) { $0 + ($1.srcOut - $1.srcIn) } }
    /// True OUTPUT frame count — speed-adjusted (the twin of the backend's out_cursor).
    var totalOutputFrames: Int {
        keptIntervalsWithSpeed.reduce(0) { $0 + outputFrames($1.srcOut - $1.srcIn, speed: $1.speed) }
    }
    var outputSeconds: Double { framesToSeconds(totalOutputFrames) }

    /// The kept sub-range of a segment after drops: first & last kept SOURCE frame and the kept
    /// frame count. The timeline renders cells at this size (not raw srcOut-srcIn) so a trim —
    /// which adds an interior drop without moving the segment boundary — visibly shrinks the clip,
    /// and its duration label stays honest. nil when the segment is fully dropped.
    func keptBounds(ofSegment idx: Int) -> (first: Int, last: Int, frames: Int)? {
        guard segments.indices.contains(idx) else { return nil }
        let s = segments[idx]
        let dropRanges = drops.filter { $0.srcOut > $0.srcIn }.map { ($0.srcIn, $0.srcOut) }.sorted { $0.0 < $1.0 }
        var kept = 0, first = s.srcOut, last = s.srcIn, cur = s.srcIn
        for (dIn, dOut) in dropRanges {
            if dOut <= cur || dIn >= s.srcOut { continue }
            if dIn > cur { let hi = min(dIn, s.srcOut); kept += hi - cur; first = min(first, cur); last = max(last, hi) }
            cur = max(cur, dOut)
            if cur >= s.srcOut { break }
        }
        if cur < s.srcOut { kept += s.srcOut - cur; first = min(first, cur); last = s.srcOut }
        return kept > 0 ? (first, last, kept) : nil
    }

    /// Output-time (seconds) -> source-time (seconds). The Swift twin of map_point
    /// (edl.py) — speed-aware: output frames inside an interval advance speed× in source.
    func sourceSeconds(forOutput outputSec: Double) -> Double {
        var acc = 0
        let target = secondsToFrame(outputSec)
        for iv in keptIntervalsWithSpeed {
            let outLen = outputFrames(iv.srcOut - iv.srcIn, speed: iv.speed)
            if target < acc + outLen {
                let srcOffset = Int((Double(target - acc) * iv.speed).rounded(.toNearestOrEven))
                return framesToSeconds(min(iv.srcOut - 1, iv.srcIn + srcOffset))
            }
            acc += outLen
        }
        return framesToSeconds(keptIntervals.last?.srcOut ?? 0)
    }

    /// The first visible OUTPUT-time span (seconds) of a source range, clipped to the kept
    /// intervals in play order — where an overlay's chip sits on the timeline. Contiguous
    /// pieces merge; a discontiguous tail (a cut through the overlay's middle) is dropped in
    /// favor of the first piece. nil when the range's footage is fully dropped.
    func outputSpan(srcIn: Int, srcOut: Int) -> (start: Double, end: Double)? {
        var acc = 0
        var found: (Int, Int)? = nil
        for iv in keptIntervalsWithSpeed {
            let a = max(iv.srcIn, srcIn), b = min(iv.srcOut, srcOut)
            let outLen = outputFrames(iv.srcOut - iv.srcIn, speed: iv.speed)
            if b > a {
                let start = acc + Int((Double(a - iv.srcIn) / iv.speed).rounded(.toNearestOrEven))
                let end = start + outputFrames(b - a, speed: iv.speed)
                if let f = found {
                    if f.1 == start { found = (f.0, end) }   // contiguous in output — merge
                    else { break }                            // discontiguous — first span wins
                } else {
                    found = (start, end)
                }
            }
            acc += outLen
        }
        guard let f = found else { return nil }
        return (framesToSeconds(f.0), framesToSeconds(f.1))
    }

    /// Output boundary (seconds) where this segment's last kept footage ends — the
    /// anchor a transition dip centers on. nil when the segment keeps nothing.
    func outputBoundary(afterSegment idx: Int) -> Double? {
        guard segments.indices.contains(idx) else { return nil }
        var acc = 0
        var best: Int? = nil
        let order = segmentOrder ?? Array(segments.indices)
        for (walkPos, segIdx) in order.enumerated() where segments.indices.contains(segIdx) {
            _ = walkPos
            for iv in keptIntervalsForSegment(segIdx) {
                let outLen = outputFrames(iv.1 - iv.0, speed: min(3.0, max(0.5, segments[segIdx].speed)))
                if segIdx == idx { best = acc + outLen }
                acc += outLen
            }
        }
        guard let b = best, b < totalOutputFrames else { return nil }   // final clip: no dip
        return framesToSeconds(b)
    }

    /// This segment's kept sub-intervals in play position (helper for outputBoundary).
    private func keptIntervalsForSegment(_ idx: Int) -> [(Int, Int)] {
        let s = segments[idx]
        let dropRanges = drops.filter { $0.srcOut > $0.srcIn }.map { ($0.srcIn, $0.srcOut) }.sorted { $0.0 < $1.0 }
        var out: [(Int, Int)] = []
        var cur = s.srcIn
        for (dIn, dOut) in dropRanges {
            if dOut <= cur || dIn >= s.srcOut { continue }
            if dIn > cur { out.append((cur, min(dIn, s.srcOut))) }
            cur = max(cur, dOut)
            if cur >= s.srcOut { break }
        }
        if cur < s.srcOut { out.append((cur, s.srcOut)) }
        return out.filter { $0.1 > $0.0 }
    }
}
