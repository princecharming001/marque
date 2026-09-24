import Foundation

// Tiny assertion harness — no XCTest on purpose (runs from run.sh with plain swiftc).
var failures = 0
var checks = 0
func check(_ cond: @autoclosure () -> Bool, _ msg: String, file: StaticString = #file, line: UInt = #line) {
    checks += 1
    if !cond() {
        failures += 1
        print("FAIL \(line): \(msg)")
    }
}
func section(_ name: String) { print("• \(name)") }

func tempDir(_ tag: String) -> URL {
    let d = FileManager.default.temporaryDirectory
        .appendingPathComponent("marque-editor-logic-\(tag)-\(UUID().uuidString)", isDirectory: true)
    try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
    return d
}

func parseJSON(_ text: String) -> [String: Any] {
    (try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any]) ?? [:]
}

/// A small but realistic server EDL (3 segments, a filler drop, captions, a sticker).
let baseEDLText = """
{"style":"talking_head","format_id":"myth-buster","caption_style":"clean",
 "caption_options":{"position":"bottom","size":"medium","font":"inter","grouping":"phrase"},
 "segments":[{"src_in":0,"src_out":300,"speed":1.0},{"src_in":300,"src_out":600,"speed":1.0},
             {"src_in":600,"src_out":900,"speed":1.0}],
 "drops":[{"src_in":120,"src_out":150,"reason":"filler"}],
 "captions":[{"word":"hello","frame":10},{"word":"world","frame":40},{"word":"again","frame":400}],
 "overlays":[{"type":"text_sticker","src_in":30,"src_out":120,"scale":1.0,"text":"Hi",
              "pos_x":0.5,"pos_y":0.35,"rotation":0,"color":null,"bg":"none","font":"inter"}],
 "look":{"filter":"film","intensity":0.8,"adjust":{"brightness":0.1}},
 "audio":{"lufs_target":-14.0,"duck":{"depth":0.4},"volume_ranges":[]},
 "theme_id":"clean_pro","speech_frames":[10,40,400]}
"""

@MainActor
func runAll() {
    // MARK: op log Codable round trip
    section("op log encode/decode round trip")
    let seed = [EditorCaption(word: "hello", frame: 10), EditorCaption(word: "world", frame: 40)]
    let gestures: [[WireOp]] = [
        [.cut(120, 180)],
        [.restore(120, 150)],
        [.split(1, at: 450)],
        [.reorder([1, 0, 2, 3])],
        [.captionsEnabled(false)],
        [.captionsEnabled(true, seed: seed)],
        [.captionStyle("karaoke"),
         .captionOptions(posY: 0.4, scale: 1.2, accent: "#FFD60A", uppercase: true, font: "anton",
                         grouping: "word", highlightWords: ["money", "time"], strokePx: 8, bg: "#000000")],
        [.editSticker(index: 0, posX: 0.3, posY: 0.6, scale: 1.4, rotation: 10,
                      color: "#FF3B30", bg: "box", font: "baloo")],
        [.addTextSticker(30, 120, text: "Yo")],
        [.removeBroll(10, 40, exact: true), .addMediaRoll(10, 40, url: "https://x/y.mov")],
        [.brollRect(index: 0, x: 0.1, y: 0.2, w: 0.5, h: 0.4)],
        [.setMusic(url: "https://m/a.mp3", volume: 0.2, duck: true)],
        [.removeMusic()],
        [.transition(after: 0, style: "fade_black", frames: 9)],
        [.filter("vivid", intensity: 0.5)],
        [.adjust(brightness: 0.2, vignette: 0.3)],
        [.segmentTransform(0, scale: 1.3, offX: 0.1)],
        [.segmentSpeed(2, 1.5)],
        [.splitFraction(0.55)],
    ]
    let draft = EditorDraft(jobId: "job-123", base: EditorDraftBase(full: "f", structure: "s"),
                            carryAcrossRetheme: true, gestures: gestures)
    do {
        let data = try JSONEncoder().encode(draft)
        let back = try JSONDecoder().decode(EditorDraft.self, from: data)
        check(back == draft, "EditorDraft survives JSON round trip")
        check(back.gestures[5][0].captionSeed == seed, "captionSeed (local-only) is persisted")
        check(back.gestures[3][0].order == [1, 0, 2, 3], "reorder permutation persisted")
        check(back.gestures[6][1].strings["highlight_words"] == ["money", "time"], "string lists persisted")
        check(back.gestures[6][1].bool["uppercase"] == true, "bool args persisted")
    } catch {
        check(false, "encode/decode threw \(error)")
    }
    check(WireOp.captionsEnabled(true, seed: seed).json()["captionSeed"] == nil,
          "wire JSON never carries the local seed")
    // Older/minimal draft JSON (absent keys) still decodes.
    let minimal = #"{"type":"cut_range","i":{"start_frame":1,"end_frame":9}}"#
    let op = try? JSONDecoder().decode(WireOp.self, from: Data(minimal.utf8))
    check(op == WireOp.cut(1, 9), "absent keys decode to defaults")

    // MARK: fingerprint
    section("base fingerprint match / mismatch")
    let edl = parseJSON(baseEDLText)
    let b1 = EditorDraftBase(edl: edl)
    let b2 = EditorDraftBase(edl: parseJSON(baseEDLText))
    check(b1 == b2, "same EDL text → same fingerprint")
    // Same content, different key order in the wire text.
    let reordered = parseJSON("""
    {"theme_id":"clean_pro","speech_frames":[10,40,400],
     "audio":{"volume_ranges":[],"duck":{"depth":0.4},"lufs_target":-14.0},
     "look":{"adjust":{"brightness":0.1},"intensity":0.8,"filter":"film"},
     "overlays":[{"font":"inter","bg":"none","color":null,"rotation":0,"pos_y":0.35,"pos_x":0.5,
                  "text":"Hi","scale":1.0,"src_out":120,"src_in":30,"type":"text_sticker"}],
     "captions":[{"frame":10,"word":"hello"},{"frame":40,"word":"world"},{"frame":400,"word":"again"}],
     "drops":[{"reason":"filler","src_out":150,"src_in":120}],
     "segments":[{"speed":1.0,"src_out":300,"src_in":0},{"speed":1.0,"src_out":600,"src_in":300},
                 {"speed":1.0,"src_out":900,"src_in":600}],
     "caption_options":{"grouping":"phrase","font":"inter","size":"medium","position":"bottom"},
     "caption_style":"clean","format_id":"myth-buster","style":"talking_head"}
    """)
    check(EditorDraftBase(edl: reordered) == b1, "key order does not change the fingerprint")
    var structural = edl
    structural["drops"] = [["src_in": 120, "src_out": 150, "reason": "filler"],
                           ["src_in": 700, "src_out": 760, "reason": "manual"]]
    let b3 = EditorDraftBase(edl: structural)
    check(b3.full != b1.full && b3.structure != b1.structure, "a structural change moves both fingerprints")
    var themed = edl
    themed["theme_id"] = "bold_creator"
    themed["caption_style"] = "bold-word"
    themed["caption_options"] = ["font": "anton", "uppercase": true]
    themed["look"] = ["filter": "vivid", "intensity": 1.0]
    themed["audio"] = ["lufs_target": -14.0, "duck": ["depth": 0.7], "volume_ranges": [Any]()]
    let b4 = EditorDraftBase(edl: themed)
    check(b4.full != b1.full, "a retheme moves the full fingerprint")
    check(b4.structure == b1.structure, "a retheme keeps the structure fingerprint")
    var music = edl
    music["audio"] = ["lufs_target": -14.0, "duck": ["depth": 0.4], "volume_ranges": [Any](),
                      "music": ["url": "https://m/a.mp3", "volume": 0.15]]
    check(EditorDraftBase(edl: music).structure != b1.structure, "audio.music is NOT theme-owned")

    // MARK: restore decision
    section("restore / drop decision")
    let good = EditorDraft(jobId: "j", base: b1, gestures: [[.cut(700, 760)]])
    check(EditorDraft.decide(nil, against: b1) == .none, "no draft → none")
    check(EditorDraft.decide(good, against: b1) == .restore, "same base → restore")
    check(EditorDraft.decide(good, against: b3) == .drop, "moved base → drop")
    check(EditorDraft.decide(good, against: b4) == .drop, "retheme without the carry flag → drop")
    var carried = good; carried.carryAcrossRetheme = true
    check(EditorDraft.decide(carried, against: b4) == .restore, "kept-edits retheme → restore")
    check(EditorDraft.decide(carried, against: b3) == .drop, "carry flag never survives a structural change")
    var empty = good; empty.gestures = []
    check(EditorDraft.decide(empty, against: b1) == .drop, "empty op log → drop")
    var future = good; future.version = EditorDraft.currentVersion + 1
    check(EditorDraft.decide(future, against: b1) == .drop, "unknown draft version → drop")

    // MARK: replay through the session (the restore path)
    section("draft replay reproduces the session")
    let doc = EditorDocument(edl: edl)
    let live = EditorSession(document: doc)
    check(live.perform([.cut(700, 760)]), "cut applies")
    check(live.perform([.split(0, at: 60)]), "split applies")
    check(live.perform([.captionsEnabled(false)]), "captions off applies")
    check(live.perform([.editSticker(index: 0, color: "#FF3B30")]), "sticker colour applies")
    check(!live.perform([.cut(0, 900)]), "a cut leaving < 2s is rejected (not logged)")
    let persisted = EditorDraft(jobId: "j", base: b1, gestures: live.opLog)
    let restoredDraft = (try? JSONDecoder().decode(EditorDraft.self,
                                                   from: JSONEncoder().encode(persisted)))
    let fresh = EditorSession(document: doc)
    let applied = fresh.replay(restoredDraft?.gestures ?? [])
    check(applied == 4, "all 4 gestures replay (got \(applied))")
    check(fresh.draft == live.draft, "replayed draft equals the live draft")
    check(fresh.opLog == live.opLog, "replayed op log equals the live op log")
    check(fresh.isDirty && fresh.canUndo, "restored session is dirty and undoable")
    var undos = 0
    while fresh.undo() != nil { undos += 1 }
    check(undos == 4, "each restored gesture is its own undo step (got \(undos))")
    check(fresh.draft == doc && !fresh.isDirty, "undoing everything returns to the base")
    check(fresh.revision > 0, "revision bumps on replay/undo")

    // MARK: store + autosaver
    section("draft store + autosaver")
    let dir = tempDir("store")
    check(EditorDraftStore.save(good, in: dir), "save succeeds")
    check(EditorDraftStore.load(jobId: "j", in: dir) == good, "load returns what was saved")
    check(EditorDraftStore.fileURL(jobId: "a/b c", in: dir).lastPathComponent == "a_b_c.json",
          "job ids are sanitized for file names")
    EditorDraftStore.clear(jobId: "j", in: dir)
    check(EditorDraftStore.load(jobId: "j", in: dir) == nil, "clear removes the draft")
    EditorDraftStore.save(EditorDraft(jobId: "old", base: b1, gestures: [[.cut(1, 2)]]), in: dir)
    EditorDraftStore.save(EditorDraft(jobId: "new", base: b1, gestures: [[.cut(1, 2)]]), in: dir)
    let oldURL = EditorDraftStore.fileURL(jobId: "old", in: dir)
    try? FileManager.default.setAttributes([.modificationDate: Date(timeIntervalSinceNow: -30 * 86400)],
                                           ofItemAtPath: oldURL.path)
    EditorDraftStore.prune(in: dir)
    check(EditorDraftStore.load(jobId: "old", in: dir) == nil, "prune drops stale drafts")
    check(EditorDraftStore.load(jobId: "new", in: dir) != nil, "prune keeps recent drafts")

    let saver = EditorDraftAutosaver(jobId: "auto", base: b1, directory: dir, debounce: 60)
    saver.schedule([[.cut(10, 20)]])
    check(EditorDraftStore.load(jobId: "auto", in: dir) == nil, "debounced: nothing written yet")
    saver.flush()
    check(EditorDraftStore.load(jobId: "auto", in: dir)?.gestures == [[.cut(10, 20)]], "flush writes")
    saver.schedule([])
    saver.flush()
    check(EditorDraftStore.load(jobId: "auto", in: dir) == nil, "an empty op log deletes the draft")
    saver.persistForRetheme([[.cut(10, 20)]])
    check(EditorDraftStore.load(jobId: "auto", in: dir)?.carryAcrossRetheme == true,
          "retheme carry is written immediately with the flag")
    saver.close(deleting: false)
    check(EditorDraftStore.load(jobId: "auto", in: dir) != nil, "retiring a saver keeps the file")
    saver.schedule([[.cut(30, 40)]]); saver.flush()
    check(EditorDraftStore.load(jobId: "auto", in: dir)?.gestures == [[.cut(10, 20)]],
          "a closed saver never writes again")
    let saver2 = EditorDraftAutosaver(jobId: "auto", base: b1, directory: dir)
    saver2.close()
    check(EditorDraftStore.load(jobId: "auto", in: dir) == nil, "close() deletes (discard / saved)")

    // MARK: save outcome classification
    section("save outcome classification")
    let ok: [String: Any] = ["applied": [["type": "cut_range", "applied": true]],
                             "skipped": [["type": "add_punch_in", "applied": false,
                                          "reason": "zooms aren't rendered in this video style"]],
                             "changed": true, "needs_render": true]
    if case .saved(let r) = EditorSaveOutcome.classify(status: 200, body: ok) {
        check(r.needsRender && r.changed && r.appliedCount == 1, "200 parses needs_render/changed/applied")
        check(r.skipped == [EditorSkippedOp(type: "add_punch_in",
                                            reason: "zooms aren't rendered in this video style")],
              "200 parses skipped ops with reasons")
    } else { check(false, "200 → saved") }
    if case .saved(let r) = EditorSaveOutcome.classify(status: 200, body: ["applied": [Any](), "skipped": [Any](),
                                                                         "changed": false, "needs_render": false]) {
        check(!r.changed, "changed:false is reported, not assumed")
    } else { check(false, "200 changed:false → saved(changed: false)") }
    check(EditorSaveOutcome.classify(status: 0, body: nil)
          == .unreachable(EditorSaveOutcome.unreachableCopy, ambiguous: true), "transport → ambiguous unreachable")
    check(EditorSaveOutcome.classify(status: 500, body: ["detail": "boom"])
          == .unreachable(EditorSaveOutcome.serverErrorCopy, ambiguous: true), "5xx JSON is NOT success")
    check(EditorSaveOutcome.classify(status: 502, body: nil)
          == .unreachable(EditorSaveOutcome.serverErrorCopy, ambiguous: true), "5xx HTML → ambiguous")
    check(EditorSaveOutcome.classify(status: 200, body: nil)
          == .unreachable(EditorSaveOutcome.serverErrorCopy, ambiguous: true), "unparseable 200 → ambiguous")
    check(EditorSaveOutcome.classify(status: 503, body: nil) == .busy(EditorSaveOutcome.unavailableCopy), "503 → busy")
    check(EditorSaveOutcome.classify(status: 409, body: nil) == .busy(EditorSaveOutcome.busyCopy), "409 → busy")
    check(EditorSaveOutcome.classify(status: 404, body: nil) == .gone(EditorSaveOutcome.goneCopy), "404 → gone")
    check(EditorSaveOutcome.classify(status: 410, body: ["detail": "job_expired"])
          == .gone(EditorSaveOutcome.goneCopy), "410 → gone")
    check(EditorSaveOutcome.classify(status: 422, body: ["detail": []])
          == .rejected(EditorSaveOutcome.rejectedCopy), "other 4xx → rejected")
    check(EditorSaveOutcome.classify(status: 408, body: nil)
          == .unreachable(EditorSaveOutcome.unreachableCopy, ambiguous: false), "408 → unambiguous retry")
    check(EditorSaveOutcome.classify(status: 0, body: nil).notice?.retryable == true, "transport → Retry offered")
    check(EditorSaveOutcome.classify(status: 409, body: nil).notice?.retryable == true, "busy → Retry offered")
    check(EditorSaveOutcome.classify(status: 404, body: nil).notice?.retryable == false, "gone → no Retry")
    check(EditorSaveOutcome.classify(status: 422, body: nil).notice?.retryable == false, "rejected → no Retry")

    // MARK: load outcome classification
    section("load outcome classification")
    check(EditorLoadOutcome.classify(status: 200, hasEDL: true) == .ready, "200 + edl → ready")
    check(EditorLoadOutcome.classify(status: 200, hasEDL: false) == .notReady, "200 without edl → not ready")
    check(EditorLoadOutcome.classify(status: 404, hasEDL: false) == .gone, "404 → gone")
    check(EditorLoadOutcome.classify(status: 410, hasEDL: false) == .gone, "410 → gone")
    for s in [0, 408, 429, 500, 502, 503, 504, 401] {
        check(EditorLoadOutcome.classify(status: s, hasEDL: false) == .unreachable, "\(s) → unreachable (retry)")
    }

    // MARK: ED-6 hex parsing + sticker style parity
    section("hex parsing (ED-6)")
    check(EditorHex.parse("#FFFFFF").map { $0.rgb == 0xFFFFFF && $0.alpha == 1 } == true, "#FFFFFF")
    check(EditorHex.parse("FFFFFF")?.rgb == 0xFFFFFF, "bare FFFFFF is white (old parser gave 0x0FFFFF cyan)")
    check(EditorHex.parse("#ffd60a")?.rgb == 0xFFD60A, "lowercase hex")
    check(EditorHex.parse(" #0A84FF ")?.rgb == 0x0A84FF, "whitespace tolerated")
    if let p = EditorHex.parse("#00000080") {
        check(p.rgb == 0 && abs(p.alpha - 128.0 / 255.0) < 1e-9, "#RRGGBBAA splits alpha out of the blue channel")
    } else { check(false, "#RRGGBBAA parses") }
    for bad in ["", "#", "#12345", "#1234567", "#GGGGGG", "FFFFFFFFF", "#FFFFFF80FF"] {
        check(EditorHex.parse(bad) == nil, "rejects \(bad.debugDescription)")
    }
    check(EditorHex.isStickerColor("#FFD60A") && !EditorHex.isStickerColor("FFD60A")
          && !EditorHex.isStickerColor("#FFD60A80"), "sticker colour = server regex #[0-9a-fA-F]{6}")
    check(EditorHex.stickerWire("ffd60a") == "#FFD60A", "swatch → wire form")
    check(EditorHex.stickerWire("000000") == "#000000", "wire form keeps leading zeros")
    check(EditorHex.stickerWire("#00000080") == nil, "translucent colours are not sticker colours")

    section("sticker style ops match the server (ED-6)")
    let sDoc = EditorDocument(edl: edl)
    func sticker(_ op: WireOp) -> EditorOverlay? { LocalEDLEngine.apply(op, to: sDoc)?.overlays[0] }
    for hex in ["FFFFFF", "FFD60A", "111111", "FF3B30", "0A84FF"] {
        check(sticker(.editSticker(index: 0, color: "#" + hex))?.color == "#" + hex, "swatch #\(hex) applies")
        check(LocalEDLEngine.apply(.editSticker(index: 0, color: hex), to: sDoc) == nil,
              "bare \(hex) is rejected like the server")
    }
    check(sticker(.editSticker(index: 0, color: "default"))?.color == nil, "default resets colour")
    check(sticker(.editSticker(index: 0, bg: "box"))?.bg == "box", "bg box applies")
    check(LocalEDLEngine.apply(.editSticker(index: 0, bg: "111111"), to: sDoc) == nil, "bg 111111 rejected")
    for f in ["inter", "archivo", "baloo"] {
        check(sticker(.editSticker(index: 0, font: f))?.font == f, "font \(f) applies")
    }
    check(LocalEDLEngine.apply(.editSticker(index: 0, font: "serif"), to: sDoc) == nil, "font serif rejected")
    check(LocalEDLEngine.apply(.editOverlay(index: 0, frameIn: 5000, frameOut: 6000), to: sDoc) == nil,
          "out-of-bounds window rejects the whole op")
    check(sticker(.editOverlay(index: 0, frameIn: 30, frameOut: 200))?.srcOut == 200, "valid window applies")
    check(sticker(.editOverlayText(index: 0, text: "New"))?.text == "New", "text edit applies")
    let added = LocalEDLEngine.apply(WireOp(type: "add_text_sticker", i: ["start_frame": 0, "end_frame": 60],
                                            s: ["text": "x", "color": "FFFFFF", "bg": "111111", "font": "serif"]),
                                     to: sDoc)?.overlays.last
    check(added?.color == nil && added?.bg == "none" && added?.font == "inter",
          "add_text_sticker drops invalid look values like the server")

    section("undo/redo swaps captions back (ED-7 derivation input)")
    let cs = EditorSession(document: EditorDocument(edl: edl))
    check(!cs.draft.captions.isEmpty, "base has captions")
    cs.perform([.captionsEnabled(false)])
    check(cs.draft.captions.isEmpty, "captions off empties the draft")
    _ = cs.undo()
    check(!cs.draft.captions.isEmpty, "undo restores them — captionsOn must re-derive to ON")
    _ = cs.redo()
    check(cs.draft.captions.isEmpty, "redo empties them — captionsOn must re-derive to OFF")

    section("skipped / unchanged Save results (ED-9)")
    let names: (String) -> String = { ["add_punch_in": "Zoom", "edit_overlay": "Edit"][$0] ?? "Edit" }
    let partial = EditorSaveResult(needsRender: true, changed: true, appliedCount: 3, skipped: [
        EditorSkippedOp(type: "add_punch_in", reason: "zooms aren't rendered in this video style"),
        EditorSkippedOp(type: "add_punch_in", reason: "zooms aren't rendered in this video style"),
    ])
    let rep = partial.skippedReport(displayName: names)
    check(rep?.title == "2 changes couldn't be applied", "title counts every skipped op")
    check(rep?.message.hasPrefix("Zoom: zooms aren't rendered in this video style\n\n") == true,
          "identical reasons collapse to one line")
    check(rep?.message.hasSuffix("re-rendering now.") == true, "says the rest is rendering")
    let one = EditorSaveResult(needsRender: false, changed: true, appliedCount: 1,
                               skipped: [EditorSkippedOp(type: "edit_overlay", reason: "")])
    check(one.skippedReport(displayName: names)?.title == "1 change couldn't be applied", "singular title")
    check(one.skippedReport(displayName: names)?.message.hasSuffix("Everything else is saved.") == true,
          "no-render wording")
    let many = EditorSaveResult(needsRender: true, changed: true, appliedCount: 0, skipped: (0..<5).map {
        EditorSkippedOp(type: "edit_overlay", reason: "reason \($0)") })
    check(many.skippedReport(displayName: names)?.message.contains("+ 2 more") == true, "caps at three reasons")
    let clean = EditorSaveResult(needsRender: true, changed: true, appliedCount: 2, skipped: [])
    check(clean.skippedReport(displayName: names) == nil, "nothing skipped → no report (plain dismiss)")
    let none = EditorSaveResult(needsRender: false, changed: false, appliedCount: 0, skipped: [
        EditorSkippedOp(type: "edit_overlay", reason: "nothing to change")])
    let nb = none.nothingAppliedNotice(displayName: names)
    check(nb.message == "None of your changes could be applied (Edit: nothing to change). Undo the last change and try again."
          && !nb.retryable, "changed:false keeps editing with the reason, no Retry")

    section("memoized derived timeline == the document's own math (ED-5)")
    let ms = EditorSession(document: EditorDocument(edl: edl))
    ms.perform([.split(1, at: 450)])
    ms.perform([.reorder([2, 0, 1, 3])])
    ms.perform([.segmentSpeed(0, 2.0)])
    ms.perform([.cut(500, 540)])
    let d = ms.draft
    check(ms.keptIntervals.map { [$0.srcIn, $0.srcOut] } == d.keptIntervalsWithSpeed.map { [$0.srcIn, $0.srcOut] }
          && ms.keptIntervals.map(\.speed) == d.keptIntervalsWithSpeed.map(\.speed), "kept intervals match")
    var mismatches = 0
    for t in stride(from: 0.0, through: d.outputSeconds + 1, by: 0.05)
    where ms.sourceFrame(forOutputSeconds: t) != secondsToFrame(d.sourceSeconds(forOutput: t)) { mismatches += 1 }
    check(mismatches == 0, "playhead source frame matches at every 50ms (\(mismatches) off)")
    var spanOff = 0
    for a in stride(from: 0, to: 900, by: 17) {
        for len in [5, 30, 90, 400] {
            let x = ms.outputSpan(srcIn: a, srcOut: a + len), y = d.outputSpan(srcIn: a, srcOut: a + len)
            if x?.start != y?.start || x?.end != y?.end { spanOff += 1 }
        }
    }
    check(spanOff == 0, "output spans match for every probe (\(spanOff) off)")
    let before = ms.keptIntervals.count
    ms.perform([.restore(500, 540)])
    check(ms.keptIntervals.count == ms.draft.keptIntervalsWithSpeed.count && ms.keptIntervals.count != before,
          "the memo refreshes on the next revision")
    _ = ms.undo()
    check(ms.keptIntervals.count == before, "…and on undo")
    check(!ms.onlySplits, "mixed log is not split-only")
    let sp = EditorSession(document: EditorDocument(edl: edl))
    check(!sp.onlySplits, "empty log is not split-only")
    sp.perform([.split(0, at: 100)])
    check(sp.onlySplits, "split-only log")

    section("timeline zoom + filmstrip density (ED-12)")
    let w = 390.0
    let fit10 = TimelineZoom.fitPPS(viewportWidth: w, totalSeconds: 600)
    check(abs(fit10 - 330.0 / 600.0) < 1e-9, "10-min cut fits at 0.55 pt/s (old floor 6 showed ~55 s)")
    check(TimelineZoom.fitPPS(viewportWidth: w, totalSeconds: 3600) == 0.5, "fit never drops below 0.5 pt/s")
    check(TimelineZoom.fitPPS(viewportWidth: w, totalSeconds: 1) == 110, "fit never exceeds the max zoom")
    check(TimelineZoom.minPPS(fit: fit10) == fit10, "pinch floor drops to fit on long cuts")
    check(TimelineZoom.minPPS(fit: 41) == 6, "pinch floor stays 6 on short cuts")
    check(TimelineZoom.clamp(0.1, fit: fit10) == fit10 && TimelineZoom.clamp(500, fit: fit10) == 110, "pinch clamps")
    // Zoom cycle: long cut fit → default → close → fit.
    check(TimelineZoom.nextCycleLevel(current: 18, fit: fit10) == 60, "default → close")
    check(TimelineZoom.nextCycleLevel(current: 60, fit: fit10) == fit10, "close → fit")
    check(TimelineZoom.nextCycleLevel(current: fit10, fit: fit10) == 18, "fit → default")
    // Short cut whose fit sits between default and close (old code cycled 60 ↔ fit forever).
    let fit8 = TimelineZoom.fitPPS(viewportWidth: w, totalSeconds: 8)
    var lvl = 18.0, seen = Set<Int>()
    for _ in 0..<6 { lvl = TimelineZoom.nextCycleLevel(current: lvl, fit: fit8); seen.insert(Int(lvl.rounded())) }
    check(seen == Set([18, 60, Int(fit8.rounded())]), "short cut still cycles through all three levels")
    check(TimelineZoom.nextCycleLevel(current: 18.3, fit: 18.2) == 60, "fit ≈ default collapses, still advances")
    check(TimelineZoom.nextCycleLevel(current: 33, fit: fit10) == 60, "off-cycle pinch level uses old thresholds")
    check(TimelineZoom.rulerInterval(pps: 18) == 2 && TimelineZoom.rulerInterval(pps: 60) == 1,
          "ruler intervals unchanged at normal zoom")
    check(TimelineZoom.rulerInterval(pps: fit10) == 120, "10-min fit → 2-minute ruler steps")
    check(TimelineZoom.rulerLabel(seconds: 12, interval: 2) == "12s"
          && TimelineZoom.rulerLabel(seconds: 240, interval: 120) == "4:00", "ruler labels")
    check(TimelineZoom.acrossLabel(viewportWidth: w, pps: 18) == "18s across", "pill at default zoom")
    check(TimelineZoom.acrossLabel(viewportWidth: w, pps: fit10) == "10:00 across", "pill at long fit")

    for (pps, b) in [(0.5, -1), (0.55, -1), (1.0, 0), (6.0, 2), (18.0, 4), (60.0, 5), (110.0, 6)] {
        check(FilmstripDensity.zoomBucket(pps: pps) == b, "bucket(\(pps)) == \(b)")
    }
    // Rendered-width sizing: every clip's thumbs land ~51–102 pt wide within its bucket.
    for pps in [0.55, 1.3, 6.0, 18.0, 33.0, 60.0, 110.0] {
        let b = FilmstripDensity.zoomBucket(pps: pps)
        let n = FilmstripDensity.thumbCount(outputSeconds: 90, sourceSeconds: 90, bucket: b, totalOutputSeconds: 90)
        let perThumb = 90 * pps / Double(n)
        check(n == 1 || n == 90 || (perThumb >= 45 && perThumb <= 110),
              "90 s clip @\(pps) pt/s → \(n) thumbs, \(Int(perThumb)) pt each")
    }
    let long18 = FilmstripDensity.thumbCount(outputSeconds: 600, sourceSeconds: 600,
                                             bucket: FilmstripDensity.zoomBucket(pps: 18), totalOutputSeconds: 600)
    check(Double(600 * 18) / Double(long18) <= 110, "10-min clip at default zoom: \(long18) thumbs, not 12 (~900 pt each)")
    let longMax = FilmstripDensity.thumbCount(outputSeconds: 600, sourceSeconds: 600,
                                              bucket: FilmstripDensity.zoomBucket(pps: 110), totalOutputSeconds: 600)
    check(longMax == FilmstripDensity.timelineBudget, "max zoom on a long take is capped by the memory budget")
    // Budget is shared across clips.
    let shares = (0..<50).map { _ in FilmstripDensity.thumbCount(outputSeconds: 12, sourceSeconds: 12,
                                                                  bucket: 6, totalOutputSeconds: 600) }
    check(shares.reduce(0, +) <= FilmstripDensity.timelineBudget + 50, "50 clips stay within the timeline budget")
    check(FilmstripDensity.thumbCount(outputSeconds: 0.3, sourceSeconds: 1, bucket: 0, totalOutputSeconds: 600) == 1,
          "a sliver still gets one frame")
    let samples = FilmstripDensity.sampleSeconds(srcIn: 300, srcOut: 18300, count: 7)
    check(samples.count == 7 && Set(samples).count == 7 && samples == samples.sorted()
          && samples.first == 10 && samples.last! < 610, "samples: distinct, ascending, inside the span")
    check(FilmstripDensity.sampleSeconds(srcIn: 0, srcOut: 60, count: 9) == [0, 1], "never more than one per second")
    check(FilmstripDensity.warmStride(durationSeconds: 60) == 5 && FilmstripDensity.warmStride(durationSeconds: 3600) == 100,
          "warm stride: 5 s on short takes, ≤ ~36 frames on long ones")
}

func runStickerTyping() {
    // MARK: ED-20 on-canvas sticker typing
    section("sticker typing: Return commits, line breaks never stored (ED-20)")
    check(StickerTyping.committedOnReturn("day one") == nil, "no newline: still typing")
    check(StickerTyping.committedOnReturn("day one\n") == "day one", "Return (trailing newline) commits the text without it")
    check(StickerTyping.committedOnReturn("  day one \n") == "day one", "surrounding spaces trimmed on commit")
    check(StickerTyping.committedOnReturn("\n") == "", "Return on an empty field commits blank (the sticker is discarded)")
    check(StickerTyping.committedOnReturn("two\nlines") == nil, "a pasted line break mid-text does not commit")
    check(StickerTyping.cleaned("hello\n") == "hello", "a stray Return is not saved when committing by tapping away")
    check(StickerTyping.cleaned("two\nlines") == "two\nlines", "an inner line break the user kept survives")
}

MainActor.assumeIsolated { runAll(); runStickerTyping() }
print(failures == 0 ? "PASS \(checks) checks" : "FAILED \(failures)/\(checks) checks")
exit(failures == 0 ? 0 : 1)
