import Foundation

// Scripts v2 wire: the new hook / script / trend fields are additive and lenient. An old
// payload must decode exactly as before; a new one must carry its extras through.
func runScriptsV2WireTests() {
    func dec<T: Decodable>(_ t: T.Type, _ json: String) -> T? {
        try? JSONDecoder().decode(t, from: Data(json.utf8))
    }

    suite("ScriptWire — old payload unchanged")
    let old = dec(ScriptWire.self, """
    {"title":"Why budgets fail","hook":"Your budget is lying to you.","hookSignal":"contrarian","formatId":"myth-buster",
     "body":"Here's the thing.","cta":"Follow for part two.","shotPlan":["A","B"],"targetSeconds":40,"predictedScore":82,
     "altHooks":[{"text":"Stop budgeting.","signal":"stakes","strength":70}],"style":"talking_head","why_picked":"x"}
    """)
    expect(old != nil, "decodes")
    expectEqual(old?.hook ?? "", "Your budget is lying to you.", "hook")
    expectEqual(old?.shotPlan ?? [], ["A", "B"], "shot plan")
    expectEqual(old?.altHooks?.count ?? 0, 1, "alt hooks")
    expect(old?.craft == nil, "no craft on an old payload")
    expect(old?.altHooks?.first?.extras == nil, "no hook extras on an old payload")
    let noPlan = dec(ScriptWire.self, #"{"hook":"h","body":"b","cta":"c"}"#)
    expect(noPlan != nil && noPlan?.shotPlan == nil, "absent shot plan stays nil")
    let nullPlan = dec(ScriptWire.self, #"{"hook":"h","body":"b","cta":"c","shotPlan":null,"altHooks":null}"#)
    expect(nullPlan != nil && nullPlan?.shotPlan == nil, "null shot plan stays nil")
    expect(dec(ScriptWire.self, #"{"hook":"h","body":"b"}"#) == nil, "cta still required")

    suite("ScriptWire — v2 craft fields")
    let v2 = dec(ScriptWire.self, """
    {"hook":"I quit sugar for 30 days.","hookSignal":"mid_story_v2","body":"b","cta":"c",
     "openerFrame":"mid_story","register":"peer","payoff":"the sleep thing","particularSources":["your reel 3"],
     "voiceConfidence":"high","doctrineVersion":"v2.1","lintCodes":["TELL_HERES_WHY"]}
    """)
    expect(v2 != nil, "decodes")
    expectEqual(v2?.hookSignal ?? "", "mid_story_v2", "unknown hookSignal passes through (coerced later)")
    expectEqual(v2?.craft?.openerFrame ?? "", "mid_story", "opener frame")
    expectEqual(v2?.craft?.doctrineVersion ?? "", "v2.1", "doctrine version")
    expectEqual(v2?.craft?.particularSources ?? [], ["your reel 3"], "particular sources")
    expectEqual(v2?.craft?.lintCodes ?? [], ["TELL_HERES_WHY"], "lint codes")
    let wrong = dec(ScriptWire.self, """
    {"hook":"h","body":"b","cta":"c","particularSources":"not-a-list","lintCodes":[1,"X"],"voiceConfidence":null,
     "predictedScore":81.6,"targetSeconds":"45"}
    """)
    expect(wrong != nil, "wrong-typed v2 fields never fail the script")
    expect(wrong?.craft?.particularSources == nil, "bad list dropped")
    expectEqual(wrong?.craft?.lintCodes ?? [], ["X"], "mixed list keeps strings")
    expectEqual(wrong?.predictedScore ?? 0, 82, "double score rounds")
    expectEqual(wrong?.targetSeconds ?? 0, 45, "string seconds")
    expect(dec(ScriptWire.self, #"{"hook":"h","body":"b","cta":"c","doctrineVersion":""}"#)?.craft == nil,
           "blank craft = nil")

    suite("HookWire — Hook Lab extras")
    let h = dec(HookWire.self, """
    {"text":"I was halfway through my set when it hit me.","signal":"narrative","strength":88,
     "label":"Starts mid-story","why":"Drops you in.","modeledOn":"your_reel","frame":"mid_story",
     "checks":{"specific":true,"promisesPayoff":false,"inYourVoice":true}}
    """)
    expect(h != nil, "decodes")
    expectEqual(h?.extras?.label ?? "", "Starts mid-story", "label")
    expectEqual(h?.extras?.modeledOn, .yourReel, "modeled on your reel")
    expectEqual(h?.extras?.modeledOn?.label ?? "", "From your reel", "source copy")
    expectEqual(h?.extras?.checks?.items.map(\.label) ?? [], ["Specific", "Promises the payoff", "Sounds like you"], "three checks in order")
    expectEqual(h?.extras?.checks?.items.map(\.passed) ?? [], [true, false, true], "check values")
    let plain = dec(HookWire.self, #"{"text":"t","signal":"stakes","strength":70}"#)
    expect(plain != nil && plain?.extras == nil, "old hook: no extras")
    let niche = dec(HookWire.self, #"{"text":"t","modeledOn":"niche","checks":{"specific":1}}"#)
    expectEqual(niche?.extras?.modeledOn, .niche, "niche source")
    expectEqual(niche?.extras?.checks?.items.count ?? 0, 1, "only known checks shown")
    let blankSource = dec(HookWire.self, #"{"text":"t","modeledOn":"","checks":{}}"#)
    expect(blankSource != nil && blankSource?.extras == nil, "empty modeledOn + empty checks = no extras")
    let unknownSource = dec(HookWire.self, #"{"text":"t","modeledOn":"competitor","label":"Bold claim"}"#)
    expect(unknownSource?.extras?.modeledOn == nil, "unknown source not shown")
    expectEqual(unknownSource?.extras?.label ?? "", "Bold claim", "label still shown")
    expect(dec(HookWire.self, #"{"signal":"x"}"#) == nil, "text still required")

    suite("Hook extras round-trip through the Snapshot")
    var hook = Hook(text: "t", signal: .narrative, strength: 80)
    hook.extras = h?.extras
    let data = try? JSONEncoder().encode(hook)
    let back = data.flatMap { try? JSONDecoder().decode(Hook.self, from: $0) }
    expectEqual(back?.extras, h?.extras, "extras survive encode/decode")
    let legacyHook = dec(Hook.self, #"{"id":"6F9619FF-8B86-D011-B42D-00C04FC964FF","text":"t","signal":"stakes","strength":1}"#)
    expect(legacyHook != nil && legacyHook?.extras == nil, "a hook saved before v2 still decodes")

    var sc = Script(pillarName: "p", formatId: "myth-buster", hook: hook, altHooks: [], body: "b", cta: "c",
                    shotPlan: [], targetSeconds: 30, predictedScore: 80)
    sc.craft = v2?.craft
    let scBack = (try? JSONEncoder().encode(sc)).flatMap { try? JSONDecoder().decode(Script.self, from: $0) }
    expectEqual(scBack?.craft, v2?.craft, "script craft survives encode/decode")
    expectEqual(scBack?.hook.extras, h?.extras, "hook extras inside a script survive")

    suite("TrendWire — v2 angle fields")
    let t = dec(TrendWire.self, """
    {"title":"Sugar detox day counts","why":"Day-by-day posts are climbing.","formatId":"pov-story","angleId":"a1",
     "promise":"What 30 days without sugar did to my sleep","velocity":"Rising","openers":["Day 1:"],"asOf":"2026-10-19"}
    """)
    expect(t != nil, "decodes")
    expectEqual(t?.item.promise ?? "", "What 30 days without sugar did to my sleep", "promise")
    expectEqual(t?.item.velocity ?? "", "rising", "velocity lowercased")
    expectEqual(t?.item.asOf ?? "", "2026-10-19", "as of")
    expectEqual(t?.item.formatId ?? "", "pov-story", "format id")
    let oldTrend = dec(TrendWire.self, #"{"title":"x","why":"y"}"#)
    expect(oldTrend != nil, "old trend decodes")
    expectEqual(oldTrend?.item.formatId ?? "", "myth-buster", "format fallback")
    expect(oldTrend?.item.promise == nil && oldTrend?.item.asOf == nil, "no extras on an old trend")
    let oddTrend = dec(TrendWire.self, #"{"title":"x","why":"y","velocity":"exploding","openers":"no","promise":""}"#)
    expect(oddTrend?.item.velocity == nil, "unknown velocity dropped")
    expect(oddTrend?.item.openers == nil && oddTrend?.item.promise == nil, "bad/blank fields dropped")

    suite("FeedFeedback — body")
    var s = Script(pillarName: "Money", title: "Why budgets fail", formatId: "myth-buster",
                   hook: Hook(text: "Your budget lies.", signal: .contrarian, strength: 80), altHooks: [],
                   body: "b", cta: "c", shotPlan: [], targetSeconds: 40, predictedScore: 80)
    let like = FeedFeedback.body(creatorId: "c1", niche: "finance", pillars: ["Money"], script: s, verdict: "like")
    expectEqual(like["verdict"] as? String ?? "", "like", "verdict kept")
    expect(like["voice_verdict"] == nil && like["edit_diff"] == nil && like["reason"] == nil, "no v2 keys on a plain like")
    expectEqual((like["script"] as? [String: Any])?["hook"] as? String ?? "", "Your budget lies.", "script hook")
    let topic = FeedFeedback.body(creatorId: "c1", niche: "", pillars: [], script: s, verdict: "dislike", reason: "topic")
    expectEqual(topic["reason"] as? String ?? "", "topic", "not-this-topic reason")
    expectEqual(topic["verdict"] as? String ?? "", "dislike", "topic is a dislike")
    s.craft = ScriptCraft(doctrineVersion: "v2.1")
    let voice = FeedFeedback.body(creatorId: "c1", niche: "", pillars: [], script: s, verdict: nil, voiceVerdict: .notMe)
    expect(voice["verdict"] == nil, "voice verdict sends no like/dislike")
    expectEqual(voice["voice_verdict"] as? String ?? "", "not_me", "voice verdict raw value")
    expectEqual((voice["script"] as? [String: Any])?["doctrineVersion"] as? String ?? "", "v2.1", "doctrine version rides along")
    let diff = FeedFeedback.body(creatorId: "c1", niche: "", pillars: [], script: s, verdict: nil,
                                 editDiff: EditDiff(before: "a", after: "b", field: "cta"))
    let ed = diff["edit_diff"] as? [String: Any]
    expectEqual(ed?["before"] as? String ?? "", "a", "diff before")
    expectEqual(ed?["after"] as? String ?? "", "b", "diff after")
    expectEqual(ed?["field"] as? String ?? "", "cta", "diff field")
}
