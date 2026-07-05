# Ralph loop: Marque editor hardening + manual editor

Each iteration:
1. Read `/Users/home/Marque/backend/BACKLOG_EDITOR.md`. Pick the FIRST unchecked item.
2. Implement the smallest correct version of that item. Key files:
   - backend/main.py (pipeline, tweak endpoint, retry) — hardened in E1-E10; don't regress
   - backend/app/edl.py (EDL model, apply_edl_ops, build_render_plan)
   - backend/test_editor_hardening.py (the gate — extend it with the item's tests)
   - render/src (types.ts, components/, compositions) — bridge input contract
   - ios/Marque (EditorView, LibraryView ClipDetailSheet, AppStore, LiveClipEngine)
3. GATE (must be green before checking anything off):
   `cd /Users/home/Marque/backend && python -m pytest -q` with ZERO env keys.
   iOS items also: xcodebuild Debug for scheme Marque on iPhone 17 Pro sim → BUILD SUCCEEDED.
   E15 also: `cd /Users/home/Marque/render && npm run build` exits 0.
4. Check the item off in BACKLOG_EDITOR.md with a one-line note of what landed.
5. Commit locally with a focused message + the Co-Authored-By trailer used in this repo.

Hard rules:
- Never weaken or delete an existing test. A red suite is the only priority until green.
- Error codes come only from main.ERROR_CODES.
- EDL segments stay monotonic — reordering is expressed ONLY via the segment_order
  permutation field (validator: permutation of range(len(segments))).
- Identity segment_order must produce byte-identical render plans to today (asserted).
- Keyless-green is mandatory: every new code path needs a deterministic mock/fallback.
- No new pip/npm dependencies.

When EVERY box in BACKLOG_EDITOR.md is checked AND all gates are green, output the
completion promise exactly: EDITOR PIPELINE HARDENED
