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
}

MainActor.assumeIsolated { runAll() }
print(failures == 0 ? "PASS \(checks) checks" : "FAILED \(failures)/\(checks) checks")
exit(failures == 0 ? 0 : 1)
