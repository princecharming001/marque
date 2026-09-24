import Foundation

// ED-4: a version restore changes local state only when the server rewound ALL the way;
// partial and failed rewinds are reported as failures with the picture untouched.
func runEditRestorePolicyTests() {
    suite("EditRestorePolicy — ED-4 server-first verdict")
    expectEqual(EditRestorePolicy.undosNeeded(forIndex: 0), 1, "version right before current → 1 undo")
    expectEqual(EditRestorePolicy.undosNeeded(forIndex: 7), 8, "8th entry → 8 undos")
    expectEqual(EditRestorePolicy.outcome(requestedUndos: 3, appliedUndos: 3, error: false), .restored,
                "all undos applied → restored (swap the picture)")
    expectEqual(EditRestorePolicy.outcome(requestedUndos: 8, appliedUndos: 5, error: false),
                .partial(applied: 5, requested: 8),
                "server history only 5 deep, 8 asked → partial, NOT success (the old `undos > 0`)")
    expectEqual(EditRestorePolicy.outcome(requestedUndos: 2, appliedUndos: 0, error: false), .failed,
                "nothing to undo on the server → failed")
    expectEqual(EditRestorePolicy.outcome(requestedUndos: 2, appliedUndos: 0, error: true), .failed,
                "offline / 404 / 410 / 409 (error dict) → failed")
    expectEqual(EditRestorePolicy.outcome(requestedUndos: 1, appliedUndos: 1, error: true), .failed,
                "an error response never counts as restored")

    suite("EditRestorePolicy — ED-4 history after a full restore")
    let h = ["v4", "v3", "v2", "v1", "v0"]      // newest first, like Clip.renderHistory
    expectEqual(EditRestorePolicy.historyAfterRestoring(h, index: 0), ["v3", "v2", "v1", "v0"],
                "restore v4 → it becomes current, older ones stay")
    expectEqual(EditRestorePolicy.historyAfterRestoring(h, index: 2), ["v1", "v0"],
                "restore v2 → v4, v3, v2 leave the list")
    expectEqual(EditRestorePolicy.historyAfterRestoring(h, index: 4), [String](), "restore the oldest → empty")
    expectEqual(EditRestorePolicy.historyAfterRestoring(h, index: 9), [String](), "out of range → empty, no crash")
}
