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

    private struct Step: Equatable { var doc: EditorDocument; var ops: [WireOp] }
    private var undoStack: [Step] = []
    private var redoStack: [Step] = []
    private(set) var opLog: [[WireOp]] = []      // one entry per applied gesture

    var canUndo: Bool { !undoStack.isEmpty }
    var canRedo: Bool { !redoStack.isEmpty }
    var isDirty: Bool { !opLog.isEmpty }

    init(document: EditorDocument) {
        committed = document
        draft = document
    }

    /// Apply a gesture's ops to the draft. Returns false (and mutates nothing) if EVERY op was
    /// rejected by the local engine (e.g. a cut that would leave < 2s) so the caller can snap back.
    @discardableResult
    func perform(_ ops: [WireOp]) -> Bool {
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
        undoStack.append(Step(doc: draft, ops: accepted))
        redoStack.removeAll()
        draft = next
        opLog.append(accepted)
        return true
    }

    /// Returns the primary op type of the step that was undone (for a named toast), nil if nothing.
    @discardableResult
    func undo() -> String? {
        guard let step = undoStack.popLast() else { return nil }
        redoStack.append(Step(doc: draft, ops: step.ops))
        draft = step.doc
        if !opLog.isEmpty { opLog.removeLast() }
        return step.ops.first?.type
    }

    @discardableResult
    func redo() -> String? {
        guard let step = redoStack.popLast() else { return nil }
        undoStack.append(Step(doc: draft, ops: step.ops))
        draft = step.doc
        opLog.append(step.ops)
        return step.ops.first?.type
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
    }
}
