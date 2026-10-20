import Foundation

// Scripts v2: GET/PUT /v1/voice decoding. The backend may omit any field, send `{}` for
// "no voice yet", or invent enum values — none of that may sink the screen.
func runVoiceProfileTests() {
    func decode(_ json: String) -> VoiceProfile? {
        try? JSONDecoder().decode(VoiceProfile.self, from: Data(json.utf8))
    }
    func fetch(_ json: String?, _ status: Int) -> VoiceFetchResult {
        VoiceFetchResult.from(data: json.map { Data($0.utf8) }, status: status)
    }

    suite("VoiceProfile — full payload")
    let full = decode("""
    {"paragraph":"You talk fast and swear a little.","phrases":[{"text":"and like","on":true},{"text":"lowkey","on":false}],
     "openers":["Okay so"],"closers":["Go try it."],"address":"you guys","swearing":"light","typical_words":140,
     "never_say":["delve"],"corrections":["I never say 'folks'"],"confidence":"high",
     "built_from":{"reels":12,"transcribed":9,"uploads":2},"updated_at":"2026-10-19T10:00:00Z","version":3}
    """)
    expect(full != nil, "decodes")
    expectEqual(full?.paragraph ?? "", "You talk fast and swear a little.", "paragraph")
    expectEqual(full?.phrases.count ?? 0, 2, "two phrases")
    expectEqual(full?.phrases.last?.on ?? true, false, "phrase off survives")
    expectEqual(full?.swearing ?? .none, .light, "swearing")
    expectEqual(full?.typicalWords ?? 0, 140, "typical words")
    expectEqual(full?.neverSay ?? [], ["delve"], "never say")
    expectEqual(full?.confidence ?? .low, .high, "confidence")
    expectEqual(full?.builtFrom.reels ?? 0, 12, "built from reels")
    expectEqual(full?.version ?? 0, 3, "version")
    expect(full?.isEmpty == false, "not empty")

    suite("VoiceProfile — missing and odd fields")
    let empty = decode("{}")
    expect(empty != nil, "{} decodes")
    expect(empty?.isEmpty == true, "{} is empty")
    expectEqual(empty?.confidence ?? .high, .low, "missing confidence = low")
    let odd = decode("""
    {"paragraph":"x","swearing":"constant","confidence":"VERY_HIGH","typical_words":"95",
     "phrases":["okay so", {"text":"no cap"}, {"on":true}, ""],"never_say":["delve", 4, null],
     "built_from":{"reels":7.0},"version":null}
    """)
    expect(odd != nil, "odd payload still decodes")
    expectEqual(odd?.swearing ?? .heavy, .none, "unknown swearing = none")
    expectEqual(odd?.confidence ?? .high, .low, "unknown confidence = low")
    expectEqual(odd?.typicalWords ?? 0, 95, "numeric string words")
    expectEqual(odd?.phrases.map(\.text) ?? [], ["okay so", "no cap"], "bare-string phrases + empties dropped")
    expectEqual(odd?.phrases.first?.on ?? false, true, "bare string phrase is on")
    expectEqual(odd?.neverSay ?? [], ["delve"], "non-strings dropped from never_say")
    expectEqual(odd?.builtFrom.reels ?? 0, 7, "double reels count")
    let wrongTypes = decode(#"{"paragraph":["not","a","string"],"phrases":"nope","built_from":"x"}"#)
    expect(wrongTypes != nil, "wrong types never fail the profile")
    expect(wrongTypes?.isEmpty == true, "wrong types read as empty")

    suite("VoiceFetchResult — screen state")
    let live = #"{"mode":"live","voice":{"paragraph":"You talk slow."}}"#
    if case .loaded(let p) = fetch(live, 200) { expectEqual(p.paragraph, "You talk slow.", "live voice loads") }
    else { expect(false, "live voice loads") }
    expectEqual(fetch(#"{"mode":"live","voice":{}}"#, 200), .empty, "{} voice = empty state")
    expectEqual(fetch(#"{"mode":"live"}"#, 200), .empty, "no voice key = empty state")
    expectEqual(fetch(#"{"mode":"mock","voice":{"paragraph":"stub"}}"#, 200), .empty, "mock = empty state")
    expectEqual(fetch(nil, 404), .empty, "older backend (404) = empty state")
    expectEqual(fetch(nil, 0), .failed, "offline = failed")
    expectEqual(fetch("oops", 200), .failed, "garbage = failed")
    expectEqual(fetch(nil, 500), .failed, "server error = failed")

    suite("VoiceBuildStatus")
    expectEqual(VoiceBuildStatus.from(data: Data(#"{"mode":"live","status":"started"}"#.utf8)), .started, "started")
    expectEqual(VoiceBuildStatus.from(data: Data(#"{"status":"Running"}"#.utf8)), .running, "case-insensitive")
    expectEqual(VoiceBuildStatus.from(data: Data(#"{"status":"queued_v9"}"#.utf8)), .unavailable, "unknown = unavailable")
    expectEqual(VoiceBuildStatus.from(data: nil), .failed, "no reply = failed")
    expect(VoiceCopy.buildLine(.done) == nil, "done says nothing (reloads)")

    suite("VoiceUpdate — PUT body only carries what changed")
    let para = VoiceUpdate(paragraph: "  New paragraph. \n").body(creatorId: "c1")
    expectEqual(para["creator_id"] as? String ?? "", "c1", "creator id")
    expectEqual(para["paragraph"] as? String ?? "", "New paragraph.", "paragraph trimmed")
    expect(para["phrases"] == nil && para["never_say"] == nil && para["correction"] == nil, "nothing else sent")
    let never = VoiceUpdate(neverSay: [" delve", "Delve", "", "folks "]).body(creatorId: "c1")
    expectEqual(never["never_say"] as? [String] ?? [], ["delve", "folks"], "never_say trimmed + deduped")
    let phrases = VoiceUpdate(phrases: [VoicePhrase(text: "and like", on: false), VoicePhrase(text: " ")]).body(creatorId: "c1")
    let wire = phrases["phrases"] as? [[String: Any]] ?? []
    expectEqual(wire.count, 1, "blank phrase dropped")
    expectEqual(wire.first?["on"] as? Bool ?? true, false, "phrase on/off sent")
    let blank = VoiceUpdate(correction: "   ").body(creatorId: "c1")
    expect(blank["correction"] == nil, "blank correction not sent")
    let corr = VoiceUpdate(correction: " I say 'y'all' ").body(creatorId: "c1")
    expectEqual(corr["correction"] as? String ?? "", "I say 'y'all'", "correction trimmed")

    suite("VoiceCopy — confidence line")
    var p = VoiceProfile(); p.paragraph = "x"
    expectEqual(VoiceCopy.confidenceLine(p), "Add a few reels so this sounds like you", "low → add reels")
    p.confidence = .medium; p.builtFrom.reels = 0
    expectEqual(VoiceCopy.confidenceLine(p), "Add a few reels so this sounds like you", "no reels → add reels")
    p.builtFrom.reels = 12
    expectEqual(VoiceCopy.confidenceLine(p), "Built from 12 of your reels", "built from N")
}
