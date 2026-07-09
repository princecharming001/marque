import Foundation

// MARK: - WireOp — one typed EDL tweak op, exactly the JSON the backend /tweak endpoint takes
// (app/edl.py TWEAK_OP_TYPES). Emitted by the editor and applied both locally (instant
// preview, via LocalEDLEngine) and on the server (on Save). Codable for the op log.

struct WireOp: Equatable {
    var type: String
    var i: [String: Int] = [:]        // int args (start_frame, end_frame, index, at_frame, frames, order via list handled separately)
    var d: [String: Double] = [:]     // double args (scale, volume)
    var s: [String: String] = [:]     // string args (style, word, text, url, kind, enabled-as-string handled below)
    var order: [Int]? = nil           // reorder_segments permutation
    var bool: [String: Bool] = [:]

    /// The JSON dict the backend expects.
    func json() -> [String: Any] {
        var out: [String: Any] = ["type": type]
        for (k, v) in i { out[k] = v }
        for (k, v) in d { out[k] = v }
        for (k, v) in s { out[k] = v }
        for (k, v) in bool { out[k] = v }
        if let order { out["order"] = order }
        return out
    }

    // Convenience builders for the ops the editor emits.
    static func cut(_ a: Int, _ b: Int) -> WireOp { WireOp(type: "cut_range", i: ["start_frame": a, "end_frame": b]) }
    static func restore(_ a: Int, _ b: Int) -> WireOp { WireOp(type: "restore_range", i: ["start_frame": a, "end_frame": b]) }
    static func mute(_ a: Int, _ b: Int) -> WireOp { WireOp(type: "mute_range", i: ["start_frame": a, "end_frame": b]) }
    static func segmentVolume(_ a: Int, _ b: Int, _ v: Double) -> WireOp { WireOp(type: "set_segment_volume", i: ["start_frame": a, "end_frame": b], d: ["volume": v]) }
    static func split(_ index: Int, at: Int) -> WireOp { WireOp(type: "split_segment", i: ["index": index, "at_frame": at]) }
    static func reorder(_ order: [Int]) -> WireOp { WireOp(type: "reorder_segments", order: order) }
    static func captionsEnabled(_ on: Bool) -> WireOp { WireOp(type: "set_captions_enabled", bool: ["enabled": on]) }
    static func captionStyle(_ style: String) -> WireOp { WireOp(type: "set_caption_style", s: ["style": style]) }
    static func editCaption(frame: Int, word: String) -> WireOp { WireOp(type: "edit_caption", i: ["frame": frame], s: ["word": word]) }
    static func addPunchIn(_ a: Int, _ b: Int, scale: Double) -> WireOp { WireOp(type: "add_punch_in", i: ["start_frame": a, "end_frame": b], d: ["scale": scale]) }
    static func addTextCard(_ a: Int, _ b: Int, text: String) -> WireOp { WireOp(type: "add_text_card", i: ["start_frame": a, "end_frame": b], s: ["text": text]) }
    static func editOverlay(index: Int, frameIn: Int, frameOut: Int) -> WireOp { WireOp(type: "edit_overlay", i: ["index": index, "frame_in": frameIn, "frame_out": frameOut]) }
    static func editOverlayText(index: Int, text: String) -> WireOp { WireOp(type: "edit_overlay", i: ["index": index], s: ["text": text]) }
    static func removeOverlay(kind: String, _ a: Int, _ b: Int) -> WireOp { WireOp(type: "remove_overlays", i: ["start_frame": a, "end_frame": b], s: ["kind": kind]) }
    static func addBroll(_ a: Int, _ b: Int, query: String) -> WireOp { WireOp(type: "add_broll", i: ["start_frame": a, "end_frame": b], s: ["query": query]) }
    static func removeBroll(_ a: Int, _ b: Int) -> WireOp { WireOp(type: "remove_broll", i: ["start_frame": a, "end_frame": b]) }
    static func setMusic(url: String, volume: Double, duck: Bool) -> WireOp { WireOp(type: "set_music", d: ["volume": volume], s: ["url": url], bool: ["enabled": true, "duck_voice": duck]) }
    static func removeMusic() -> WireOp { WireOp(type: "set_music", bool: ["enabled": false]) }
    static func splitFraction(_ v: Double) -> WireOp { WireOp(type: "set_split_fraction", d: ["value": v]) }
}

// MARK: - LocalEDLEngine — deterministic Swift port of the subset of apply_edl_ops (app/edl.py:569)
// the editor emits. The server remains authoritative (we reload after Save); this exists for
// instant local preview + to keep op indices valid at their sequence position.

enum LocalEDLEngine {
    static let minDurationFrames = 60      // _MIN_DURATION_FRAMES (edl.py:519)

    /// Apply one op to a document. Returns nil (no-op) when the op would be rejected server-side
    /// (e.g. a cut leaving < 2s), so the UI can rubber-band before ever emitting it.
    static func apply(_ op: WireOp, to doc: EditorDocument) -> EditorDocument? {
        var d = doc
        let extent = doc.segments.map(\.srcOut).max() ?? 0
        func clamp(_ a: Int, _ b: Int) -> (Int, Int)? {
            let lo = max(0, a), hi = min(extent, b)
            return hi > lo ? (lo, hi) : nil
        }
        switch op.type {
        case "cut_range":
            guard let (a, b) = clamp(op.i["start_frame"] ?? 0, op.i["end_frame"] ?? 0) else { return nil }
            var trial = d
            trial.drops = coalesce(d.drops + [EditorDrop(srcIn: a, srcOut: b, reason: "manual")])
            guard trial.totalKeptFrames >= minDurationFrames else { return nil }
            d.drops = trial.drops
        case "restore_range":
            guard let (a, b) = clamp(op.i["start_frame"] ?? 0, op.i["end_frame"] ?? 0) else { return nil }
            var newDrops: [EditorDrop] = []
            var touched = false
            for dr in d.drops {
                if dr.srcOut <= a || dr.srcIn >= b { newDrops.append(dr); continue }
                touched = true
                if dr.srcIn < a { newDrops.append(EditorDrop(srcIn: dr.srcIn, srcOut: a, reason: dr.reason)) }
                if dr.srcOut > b { newDrops.append(EditorDrop(srcIn: b, srcOut: dr.srcOut, reason: dr.reason)) }
            }
            guard touched else { return nil }
            d.drops = newDrops.sorted { $0.srcIn < $1.srcIn }
        case "split_segment":
            guard let idx = op.i["index"], let at = op.i["at_frame"], d.segments.indices.contains(idx),
                  d.segments[idx].srcIn < at, at < d.segments[idx].srcOut else { return nil }
            let seg = d.segments[idx]
            let halves = [EditorSegment(srcIn: seg.srcIn, srcOut: at), EditorSegment(srcIn: at, srcOut: seg.srcOut)]
            d.segments = Array(d.segments[..<idx]) + halves + Array(d.segments[(idx + 1)...])
            if let old = d.segmentOrder {
                var newOrder: [Int] = []
                for iVal in old {
                    newOrder.append(iVal <= idx ? iVal : iVal + 1)
                    if iVal == idx { newOrder.append(idx + 1) }
                }
                d.segmentOrder = newOrder
            }
        case "reorder_segments":
            guard let order = op.order, order.sorted() == Array(d.segments.indices) else { return nil }
            d.segmentOrder = order == Array(d.segments.indices) ? nil : order
        case "mute_range":
            guard let (a, b) = clamp(op.i["start_frame"] ?? 0, op.i["end_frame"] ?? 0) else { return nil }
            d.volumeRanges = mergeVolume(d.volumeRanges, EditorVolumeRange(srcIn: a, srcOut: b, volume: 0))
        case "set_segment_volume":
            guard let (a, b) = clamp(op.i["start_frame"] ?? 0, op.i["end_frame"] ?? 0) else { return nil }
            let v = min(2.0, max(0.0, op.d["volume"] ?? 1.0))
            d.volumeRanges = mergeVolume(d.volumeRanges, EditorVolumeRange(srcIn: a, srcOut: b, volume: v))
        case "set_caption_style":
            guard let style = op.s["style"], ["clean", "bold-word", "karaoke"].contains(style) else { return nil }
            d.captionStyle = style
        case "set_captions_enabled":
            if op.bool["enabled"] == false { d.captions = [] } else { return nil }   // rebuild needs words (server)
        case "edit_caption":
            guard let frame = op.i["frame"] else { return nil }
            let word = op.s["word"] ?? ""
            if let existing = d.captions.firstIndex(where: { $0.frame == frame }) {
                if word.trimmingCharacters(in: .whitespaces).isEmpty { d.captions.remove(at: existing) }
                else { d.captions[existing].word = String(word.prefix(60)) }
            } else if !word.trimmingCharacters(in: .whitespaces).isEmpty {
                d.captions.append(EditorCaption(word: String(word.prefix(60)), frame: frame))
                d.captions.sort { $0.frame < $1.frame }
            } else { return nil }
        case "add_punch_in":
            guard ["talking_head", "duet_split"].contains(d.style),
                  let (a, b) = clamp(op.i["start_frame"] ?? 0, op.i["end_frame"] ?? 0) else { return nil }
            d.overlays.append(EditorOverlay(type: "punch_in", srcIn: a, srcOut: b,
                                            scale: min(1.35, max(1.02, op.d["scale"] ?? 1.08)), text: ""))
        case "add_text_card":
            let text = (op.s["text"] ?? "").trimmingCharacters(in: .whitespaces)
            guard ["green_screen", "duet_split"].contains(d.style), !text.isEmpty,
                  let (a, b) = clamp(op.i["start_frame"] ?? 0, op.i["end_frame"] ?? 0) else { return nil }
            d.overlays.append(EditorOverlay(type: "text_card", srcIn: a, srcOut: b, scale: 1.0, text: String(text.prefix(80))))
        case "edit_overlay":
            guard let idx = op.i["index"], d.overlays.indices.contains(idx) else { return nil }
            if let text = op.s["text"] { d.overlays[idx].text = String(text.prefix(80)) }
            if let fi = op.i["frame_in"], let fo = op.i["frame_out"], let (a, b) = clamp(fi, fo) {
                d.overlays[idx].srcIn = a; d.overlays[idx].srcOut = b
            }
        case "remove_overlays":
            let kind = op.s["kind"] ?? "all"
            let a = op.i["start_frame"]; let b = op.i["end_frame"]
            let before = d.overlays.count
            d.overlays.removeAll { o in
                (kind == "all" || o.type == kind) && (a == nil || b == nil || !(o.srcOut <= a! || o.srcIn >= b!))
            }
            guard d.overlays.count < before else { return nil }
        case "set_music":
            if op.bool["enabled"] == false { guard d.music != nil else { return nil }; d.music = nil }
            else if let url = op.s["url"], !url.isEmpty {
                d.music = EditorMusic(url: url, volume: min(1.0, max(0.0, op.d["volume"] ?? 0.15)),
                                      duckVoice: op.bool["duck_voice"] ?? true)
            } else { return nil }
        case "add_broll", "remove_broll", "set_split_fraction":
            break   // no local visual sim in v1 — applied server-side on Save; return doc unchanged-but-dirty
        default:
            return nil
        }
        return d
    }

    // Sort + union-merge overlapping/adjacent drops (edl.py _coalesce_drops).
    static func coalesce(_ drops: [EditorDrop]) -> [EditorDrop] {
        let ordered = drops.sorted { $0.srcIn < $1.srcIn }
        var out: [EditorDrop] = []
        for dr in ordered {
            if let last = out.last, dr.srcIn <= last.srcOut {
                out[out.count - 1].srcOut = max(last.srcOut, dr.srcOut)
            } else { out.append(dr) }
        }
        return out
    }

    // A new volume range overrides overlaps (mirrors set_segment_volume range-splitting intent).
    static func mergeVolume(_ ranges: [EditorVolumeRange], _ add: EditorVolumeRange) -> [EditorVolumeRange] {
        var out: [EditorVolumeRange] = []
        for r in ranges {
            if r.srcOut <= add.srcIn || r.srcIn >= add.srcOut { out.append(r); continue }
            if r.srcIn < add.srcIn { out.append(EditorVolumeRange(srcIn: r.srcIn, srcOut: add.srcIn, volume: r.volume)) }
            if r.srcOut > add.srcOut { out.append(EditorVolumeRange(srcIn: add.srcOut, srcOut: r.srcOut, volume: r.volume)) }
        }
        out.append(add)
        return out.sorted { $0.srcIn < $1.srcIn }
    }
}
