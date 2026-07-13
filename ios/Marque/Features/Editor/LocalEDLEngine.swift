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
    var strings: [String: [String]] = [:]   // string-list args (highlight_words)

    /// The JSON dict the backend expects.
    func json() -> [String: Any] {
        var out: [String: Any] = ["type": type]
        for (k, v) in i { out[k] = v }
        for (k, v) in d { out[k] = v }
        for (k, v) in s { out[k] = v }
        for (k, v) in bool { out[k] = v }
        for (k, v) in strings { out[k] = v }
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
    /// Partial caption-options update — only the provided keys change (backend parity).
    /// `accent` uses "default" to reset to the style's own color; posY/scale are the
    /// continuous canvas drag/pinch overrides.
    static func captionOptions(position: String? = nil, size: String? = nil,
                               posY: Double? = nil, scale: Double? = nil,
                               accent: String? = nil, uppercase: Bool? = nil,
                               font: String? = nil, grouping: String? = nil,
                               highlightWords: [String]? = nil) -> WireOp {
        var s: [String: String] = [:]
        if let position { s["position"] = position }
        if let size { s["size"] = size }
        if let accent { s["accent"] = accent }
        if let font { s["font"] = font }
        if let grouping { s["grouping"] = grouping }
        var d: [String: Double] = [:]
        if let posY { d["pos_y"] = posY }
        if let scale { d["scale"] = scale }
        var b: [String: Bool] = [:]
        if let uppercase { b["uppercase"] = uppercase }
        var op = WireOp(type: "set_caption_options", d: d, s: s, bool: b)
        if let highlightWords { op.strings = ["highlight_words": highlightWords] }
        return op
    }
    static func editCaption(frame: Int, word: String) -> WireOp { WireOp(type: "edit_caption", i: ["frame": frame], s: ["word": word]) }
    static func addPunchIn(_ a: Int, _ b: Int, scale: Double) -> WireOp { WireOp(type: "add_punch_in", i: ["start_frame": a, "end_frame": b], d: ["scale": scale]) }
    static func addTextCard(_ a: Int, _ b: Int, text: String) -> WireOp { WireOp(type: "add_text_card", i: ["start_frame": a, "end_frame": b], s: ["text": text]) }
    static func editOverlay(index: Int, frameIn: Int, frameOut: Int) -> WireOp { WireOp(type: "edit_overlay", i: ["index": index, "frame_in": frameIn, "frame_out": frameOut]) }
    static func editOverlayText(index: Int, text: String) -> WireOp { WireOp(type: "edit_overlay", i: ["index": index], s: ["text": text]) }
    static func removeOverlay(kind: String, _ a: Int, _ b: Int) -> WireOp { WireOp(type: "remove_overlays", i: ["start_frame": a, "end_frame": b], s: ["kind": kind]) }
    static func addBroll(_ a: Int, _ b: Int, query: String) -> WireOp { WireOp(type: "add_broll", i: ["start_frame": a, "end_frame": b], s: ["query": query]) }
    /// The creator's own photo/video as a roll — direct URL (already uploaded).
    static func addMediaRoll(_ a: Int, _ b: Int, url: String) -> WireOp { WireOp(type: "add_broll", i: ["start_frame": a, "end_frame": b], s: ["url": url]) }
    static func removeBroll(_ a: Int, _ b: Int) -> WireOp { WireOp(type: "remove_broll", i: ["start_frame": a, "end_frame": b]) }
    static func setMusic(url: String, volume: Double, duck: Bool) -> WireOp { WireOp(type: "set_music", d: ["volume": volume], s: ["url": url], bool: ["enabled": true, "duck_voice": duck]) }
    static func removeMusic() -> WireOp { WireOp(type: "set_music", bool: ["enabled": false]) }
    static func splitFraction(_ v: Double) -> WireOp { WireOp(type: "set_split_fraction", d: ["value": v]) }
    static func segmentSpeed(_ index: Int, _ speed: Double) -> WireOp { WireOp(type: "set_segment_speed", i: ["index": index], d: ["speed": speed]) }
    /// Canvas transform of the clip itself (partial: only provided values change).
    static func segmentTransform(_ index: Int, scale: Double? = nil,
                                 offX: Double? = nil, offY: Double? = nil) -> WireOp {
        var d: [String: Double] = [:]
        if let scale { d["scale"] = scale }
        if let offX { d["off_x"] = offX }
        if let offY { d["off_y"] = offY }
        return WireOp(type: "set_segment_transform", i: ["index": index], d: d)
    }
    static func transition(after: Int, style: String, frames: Int = 12) -> WireOp { WireOp(type: "set_transition", i: ["after_segment": after, "frames": frames], s: ["style": style]) }
    static func filter(_ name: String?, intensity: Double = 1.0) -> WireOp { WireOp(type: "set_filter", d: ["intensity": intensity], s: ["name": name ?? "none"]) }
    static func adjust(brightness: Double? = nil, contrast: Double? = nil, saturation: Double? = nil,
                       temperature: Double? = nil, vignette: Double? = nil) -> WireOp {
        var d: [String: Double] = [:]
        if let brightness { d["brightness"] = brightness }
        if let contrast { d["contrast"] = contrast }
        if let saturation { d["saturation"] = saturation }
        if let temperature { d["temperature"] = temperature }
        if let vignette { d["vignette"] = vignette }
        return WireOp(type: "set_adjust", d: d)
    }
    static func addTextSticker(_ a: Int, _ b: Int, text: String,
                               posX: Double = 0.5, posY: Double = 0.35) -> WireOp {
        WireOp(type: "add_text_sticker", i: ["start_frame": a, "end_frame": b],
               d: ["pos_x": posX, "pos_y": posY, "scale": 1.0], s: ["text": text])
    }
    /// Sticker placement/look tweaks (canvas drag/pinch/rotate + styling), partial.
    static func editSticker(index: Int, posX: Double? = nil, posY: Double? = nil,
                            scale: Double? = nil, rotation: Double? = nil,
                            color: String? = nil, bg: String? = nil, font: String? = nil) -> WireOp {
        var d: [String: Double] = [:]
        if let posX { d["pos_x"] = posX }
        if let posY { d["pos_y"] = posY }
        if let scale { d["scale"] = scale }
        if let rotation { d["rotation"] = rotation }
        var s: [String: String] = [:]
        if let color { s["color"] = color }
        if let bg { s["bg"] = bg }
        if let font { s["font"] = font }
        return WireOp(type: "edit_overlay", i: ["index": index], d: d, s: s)
    }
}

// MARK: - LocalEDLEngine — deterministic Swift port of the subset of apply_edl_ops (app/edl.py:569)
// the editor emits. The server remains authoritative (we reload after Save); this exists for
// instant local preview + to keep op indices valid at their sequence position.
//
// P4 (schema v2): end_card, progress_bar, and audio.sfx are backend+render-only for v1 — they're
// generation-time retention-pass decisions (app/retention.py place_end_card/synthesize_sfx), not
// creator-facing tweak ops, so there's no WireOp for them and EditorDocument/EditorModel has no
// mirror field either (same as react_source/react_schedule/speech_frames/trim_aggressiveness,
// which are also backend-authored fields this editor never locally simulates). A future manual
// toggle would need a real WireOp + EditorDocument field + this engine's local-preview branch,
// same as any other creator-facing op.

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
            // #45: both halves inherit the parent's speed + canvas transform (backend
            // parity) — a bare EditorSegment(srcIn:srcOut:) reset them to defaults, so
            // splitting a sped-up / repositioned clip changed the local preview and
            // diverged from the delivered render.
            var first = seg; first.srcOut = at
            var second = seg; second.srcIn = at
            d.segments = Array(d.segments[..<idx]) + [first, second] + Array(d.segments[(idx + 1)...])
            if let old = d.segmentOrder {
                var newOrder: [Int] = []
                for iVal in old {
                    newOrder.append(iVal <= idx ? iVal : iVal + 1)
                    if iVal == idx { newOrder.append(idx + 1) }
                }
                d.segmentOrder = newOrder
            }
            // #10 (iOS mirror): the insert shifts source indices at/after idx by +1, so a
            // transition anchored there moves with the second half — otherwise the local
            // preview shows the fade on the wrong boundary vs the delivered render.
            d.transitions = d.transitions.map {
                var t = $0; if t.afterSegment >= idx { t.afterSegment += 1 }; return t
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
        case "set_caption_options":
            // Partial merge with whole-op rejection on a bad value (backend parity).
            // A discrete position/size word CLEARS its continuous drag/pinch override
            // so the newest intent always wins.
            var o = d.captionOptions
            var changed = false
            if let v = op.s["position"] { guard ["top", "middle", "bottom"].contains(v) else { return nil }; o.position = v; o.posY = nil; changed = true }
            if let v = op.s["size"] { guard ["small", "medium", "large"].contains(v) else { return nil }; o.size = v; o.scale = nil; changed = true }
            if let v = op.d["pos_y"] { o.posY = min(LayoutConstants.captionPosYMax, max(LayoutConstants.captionPosYMin, v)); changed = true }
            if let v = op.d["scale"] { o.scale = min(2.0, max(0.5, v)); changed = true }
            if let v = op.s["font"] { guard ["inter", "archivo", "baloo"].contains(v) else { return nil }; o.font = v; changed = true }
            if let v = op.s["grouping"] { guard ["word", "phrase", "line"].contains(v) else { return nil }; o.grouping = v; changed = true }
            if let v = op.s["accent"] {
                if v == "default" { o.accent = nil; changed = true }
                else {
                    guard v.count == 7, v.hasPrefix("#"),
                          v.dropFirst().allSatisfy({ $0.isHexDigit }) else { return nil }
                    o.accent = v; changed = true
                }
            }
            if let v = op.bool["uppercase"] { o.uppercase = v; changed = true }
            if let hw = op.strings["highlight_words"] {
                o.highlightWords = hw.map { $0.lowercased().filter { $0.isLetter || $0.isNumber } }
                    .filter { !$0.isEmpty }
                changed = true
            }
            guard changed else { return nil }
            d.captionOptions = o
        case "set_segment_transform":
            guard let idx = op.i["index"], d.segments.indices.contains(idx), !op.d.isEmpty else { return nil }
            if let v = op.d["scale"] { d.segments[idx].txScale = min(3.0, max(0.5, v)) }
            if let v = op.d["off_x"] { d.segments[idx].txX = min(0.5, max(-0.5, v)) }
            if let v = op.d["off_y"] { d.segments[idx].txY = min(0.5, max(-0.5, v)) }
        case "set_segment_speed":
            guard let idx = op.i["index"], d.segments.indices.contains(idx),
                  let speed = op.d["speed"], (0.5...3.0).contains(speed) else { return nil }
            d.segments[idx].speed = speed
        case "set_transition":
            guard let idx = op.i["after_segment"], d.segments.indices.contains(idx),
                  let style = op.s["style"],
                  ["none", "fade_black", "fade_white", "flash"].contains(style) else { return nil }
            d.transitions.removeAll { $0.afterSegment == idx }
            if style != "none" {
                d.transitions.append(EditorTransition(afterSegment: idx, style: style,
                                                      frames: min(45, max(4, op.i["frames"] ?? 12))))
            }
        case "set_filter":
            let name = op.s["name"] ?? "none"
            if name == "none" { d.look.filter = nil }
            else {
                guard ["vivid", "film", "mono", "golden", "warm", "cool"].contains(name) else { return nil }
                d.look.filter = name
                d.look.intensity = min(1.0, max(0.0, op.d["intensity"] ?? 1.0))
            }
        case "set_adjust":
            guard !op.d.isEmpty else { return nil }
            var a = d.look.adjust
            for (k, v) in op.d {
                switch k {
                case "brightness": a.brightness = min(0.5, max(-0.5, v))
                case "contrast": a.contrast = min(0.5, max(-0.5, v))
                case "saturation": a.saturation = min(0.5, max(-0.5, v))
                case "temperature": a.temperature = min(0.5, max(-0.5, v))
                case "vignette": a.vignette = min(1.0, max(0.0, v))
                default: return nil
                }
            }
            d.look.adjust = a
        case "add_text_sticker":
            let text = (op.s["text"] ?? "").trimmingCharacters(in: .whitespaces)
            guard !text.isEmpty, let (a, b) = clamp(op.i["start_frame"] ?? 0, op.i["end_frame"] ?? 0) else { return nil }
            d.overlays.append(EditorOverlay(
                type: "text_sticker", srcIn: a, srcOut: b,
                scale: min(3.0, max(0.4, op.d["scale"] ?? 1.0)), text: String(text.prefix(120)),
                posX: min(LayoutConstants.stickerPosXMax, max(LayoutConstants.stickerPosXMin, op.d["pos_x"] ?? 0.5)),
                posY: min(LayoutConstants.stickerPosYMax, max(LayoutConstants.stickerPosYMin, op.d["pos_y"] ?? 0.5)),
                rotation: min(45, max(-45, op.d["rotation"] ?? 0)),
                color: op.s["color"], bg: op.s["bg"] ?? "none", font: op.s["font"] ?? "inter"))
        case "set_captions_enabled":
            // Enabling is a logged no-op locally (the server rebuilds captions from the
            // transcript on render); the view shows a live preview from its `words` array.
            // Disabling clears the local captions. Previously enabling returned nil, so the
            // op was dropped and captions could NEVER be turned back on.
            if op.bool["enabled"] == false { d.captions = [] }
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
            if let text = op.s["text"] { d.overlays[idx].text = String(text.prefix(120)) }
            if let fi = op.i["frame_in"], let fo = op.i["frame_out"], let (a, b) = clamp(fi, fo) {
                d.overlays[idx].srcIn = a; d.overlays[idx].srcOut = b
            }
            // text_sticker placement/look (canvas drag/pinch/rotate + styling)
            if let v = op.d["pos_x"] { d.overlays[idx].posX = min(LayoutConstants.stickerPosXMax, max(LayoutConstants.stickerPosXMin, v)) }
            if let v = op.d["pos_y"] { d.overlays[idx].posY = min(LayoutConstants.stickerPosYMax, max(LayoutConstants.stickerPosYMin, v)) }
            if let v = op.d["scale"] { d.overlays[idx].scale = min(3.0, max(0.4, v)) }
            if let v = op.d["rotation"] { d.overlays[idx].rotation = min(45, max(-45, v)) }
            if let v = op.s["color"] { d.overlays[idx].color = v == "default" ? nil : v }
            if let v = op.s["bg"], ["none", "box"].contains(v) { d.overlays[idx].bg = v }
            if let v = op.s["font"], ["inter", "archivo", "baloo"].contains(v) { d.overlays[idx].font = v }
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
        case "add_broll":
            // Media rolls are style-universal (every composition draws BrollLayer).
            let query = (op.s["query"] ?? "").trimmingCharacters(in: .whitespaces)
            let url = (op.s["url"] ?? "").trimmingCharacters(in: .whitespaces)
            guard query.isEmpty == false || url.isEmpty == false,
                  let (a, b) = clamp(op.i["start_frame"] ?? 0, op.i["end_frame"] ?? 0) else { return nil }
            d.broll.append(EditorBroll(srcIn: a, srcOut: b,
                                       cueText: query.isEmpty ? "your media" : query,
                                       source: url.isEmpty ? "stock" : "own_media",
                                       resolvedURL: url.isEmpty ? nil : url))
        case "remove_broll":
            let a = op.i["start_frame"]; let b = op.i["end_frame"]
            let before = d.broll.count
            d.broll.removeAll { r in a == nil || b == nil || !(r.srcOut <= a! || r.srcIn >= b!) }
            guard d.broll.count < before else { return nil }
        case "set_split_fraction":
            break   // no local visual sim — applied server-side on Save; return doc unchanged-but-dirty
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
