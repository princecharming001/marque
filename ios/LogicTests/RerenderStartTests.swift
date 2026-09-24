import Foundation

// ED-19: a re-render (editor save / AI tweak / restore) must not read as an upload. The
// pipeline card maps `uploading || pipelineStage == nil` to the Upload phase.
func runRerenderStartTests() {
    suite("RerenderStart — ED-19 a re-render never shows the upload phase")
    let script = Script(pillarName: "Money", title: "Why budgets fail", formatId: "myth-buster",
                        hook: Hook(text: "Budgets fail", signal: .narrative, strength: 70),
                        altHooks: [], body: "", cta: "Follow for more", shotPlan: [],
                        targetSeconds: 45, predictedScore: 70)
    var clip = Clip(scriptId: script.id, formatId: "f", formatName: "F", caption: "", predictedScore: 0,
                    status: .ready, seconds: 45, jobId: "job-1")
    clip.pipelineStage = nil            // what the last terminal poll leaves behind
    RerenderStart.apply(to: &clip)
    expect(clip.status == .rendering, "status flips to rendering")
    expect(!clip.uploading, "the server already has the take: not uploading")
    expectEqual(clip.pipelineStage, "rendering", "stage is set, so the card shows Render, not Upload")

    var stale = Clip(scriptId: script.id, formatId: "f", formatName: "F", caption: "", predictedScore: 0,
                     status: .ready, seconds: 45, jobId: "job-2")
    stale.uploading = true              // a flag left over from an interrupted first upload
    RerenderStart.apply(to: &stale)
    expect(!stale.uploading, "a stale uploading flag is cleared")
}
