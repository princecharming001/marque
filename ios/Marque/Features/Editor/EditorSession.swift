import Foundation
import Observation

// MARK: - EditorSession — the editing state machine.
//
// Holds the committed document (last server GET), the working draft, a snapshot undo/redo
// stack, and the sequential op log. One user GESTURE = one perform() call (possibly >1 wire
// op) = one undo step. On Save, the FLATTENED op log is sent in order to the server, which
// applies it sequentially exactly as we did — so split/reorder/overlay indices are valid at
// their position in the sequence (kills the index-invalidation class). We always reload from
// GET after a successful Save, so the server stays source of truth.

@MainActor
@Observable
final class EditorSession {
    private(set) var committed: EditorDocument
    private(set) var draft: EditorDocument

    private struct Step: Equatable { var doc: EditorDocument; var ops: [WireOp]; var label: String? = nil }
    private var undoStack: [Step] = []
    private var redoStack: [Step] = []
    private(set) var opLog: [[WireOp]] = []      // one entry per applied gesture

    /// Bumps on EVERY draft change (gesture, undo, redo, commit, replay) — the key the view
    /// hangs draft-derived work on (autosave, memoized timeline geometry).
    private(set) var revision = 0

    var canUndo: Bool { !undoStack.isEmpty }
    var canRedo: Bool { !redoStack.isEmpty }
    var isDirty: Bool { !opLog.isEmpty }

    init(document: EditorDocument) {
        committed = document
        draft = document
    }

    /// Apply a gesture's ops to the draft. Returns false (and mutates nothing) if EVERY op was
    /// rejected by the local engine (e.g. a cut that would leave < 2s) so the caller can snap back.
    /// The user-facing name of the step the last undo()/redo() moved across ("Delete clip"),
    /// nil when the gesture didn't name itself (the toast then names the op type).
    private(set) var lastStepLabel: String? = nil

    @discardableResult
    func perform(_ ops: [WireOp], label: String? = nil) -> Bool {
        var next = draft
        var accepted: [WireOp] = []
        for op in ops {
            if let applied = LocalEDLEngine.apply(op, to: next) {
                next = applied
                accepted.append(op)
            } else if op.type == "add_broll" || op.type == "remove_broll" || op.type == "set_split_fraction" {
                accepted.append(op)   // no local sim, but a valid server op — keep it
            }
        }
        guard !accepted.isEmpty else { return false }
        undoStack.append(Step(doc: draft, ops: accepted, label: label))
        redoStack.removeAll()
        draft = next
        opLog.append(accepted)
        revision += 1
        return true
    }

    /// ED-2 draft restore: re-apply persisted gestures in order, each through perform() —
    /// so each is ONE undo step exactly as the user made it. Returns how many applied.
    @discardableResult
    func replay(_ gestures: [[WireOp]]) -> Int {
        var applied = 0
        for g in gestures where perform(g) { applied += 1 }
        return applied
    }

    /// Returns the primary op type of the step that was undone (for a named toast), nil if nothing.
    @discardableResult
    func undo() -> String? {
        guard let step = undoStack.popLast() else { return nil }
        lastStepLabel = step.label
        redoStack.append(Step(doc: draft, ops: step.ops, label: step.label))
        draft = step.doc
        if !opLog.isEmpty { opLog.removeLast() }
        revision += 1
        return step.ops.first?.type
    }

    @discardableResult
    func redo() -> String? {
        guard let step = redoStack.popLast() else { return nil }
        lastStepLabel = step.label
        undoStack.append(Step(doc: draft, ops: step.ops, label: step.label))
        draft = step.doc
        opLog.append(step.ops)
        revision += 1
        return step.ops.first?.type
    }

    // MARK: ED-5 — derived timeline, computed once per revision (not per frame / per phrase)

    @ObservationIgnored private var memoRevision = -1
    @ObservationIgnored private var memoIntervals: [(srcIn: Int, srcOut: Int, speed: Double)] = []

    /// draft.keptIntervalsWithSpeed, memoized per revision. Reading it tracks `revision`.
    var keptIntervals: [(srcIn: Int, srcOut: Int, speed: Double)] {
        if memoRevision != revision {
            memoIntervals = draft.keptIntervalsWithSpeed
            memoRevision = revision
        }
        return memoIntervals
    }

    /// The playhead's SOURCE frame — draft.sourceSeconds(forOutput:) without re-deriving the
    /// kept intervals on every 30 Hz tick.
    func sourceFrame(forOutputSeconds t: Double) -> Int {
        secondsToFrame(EditorDocument.sourceSeconds(forOutput: t, intervals: keptIntervals))
    }

    /// draft.outputSpan(srcIn:srcOut:) over the memoized intervals.
    func outputSpan(srcIn: Int, srcOut: Int) -> (start: Double, end: Double)? {
        EditorDocument.outputSpan(srcIn: srcIn, srcOut: srcOut, intervals: keptIntervals)
    }

    /// True when every logged op is a split — the Save button's "Save" vs "Render" label,
    /// answered from op types instead of serializing the whole log per body pass.
    var onlySplits: Bool {
        !opLog.isEmpty && opLog.allSatisfy { $0.allSatisfy { $0.type == "split_segment" } }
    }

    /// The wire payload for Save — the op log flattened in order.
    func flattenedOps() -> [[String: Any]] {
        opLog.flatMap { $0 }.map { $0.json() }
    }

    /// After a successful server Apply we reload the authoritative EDL and reset local state.
    func commit(reloaded: EditorDocument) {
        committed = reloaded
        draft = reloaded
        undoStack.removeAll()
        redoStack.removeAll()
        opLog.removeAll()
        revision += 1
    }
}
