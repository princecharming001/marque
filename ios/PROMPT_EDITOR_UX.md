# Ralph loop: iOS professional editor (Loop H)

Each iteration:
1. Read `/Users/home/Marque/ios/BACKLOG_EDITOR_UX.md`. Pick the FIRST unchecked item.
2. For bug items: confirm the claim by reading the actual code path first — the
   audit that seeded this backlog had both real bugs and false positives (see
   Loop F/G's track record). If a claim doesn't hold up, check it off as
   "no-repro" with a one-line justification instead of changing code.
3. Key files: ios/Marque/Features/EditorView.swift (the manual editor),
   ios/Marque/Features/LibraryView.swift (ClipDetailSheet), ios/Marque/State/
   AppStore.swift, ios/Marque/Adapters/LiveClipEngine.swift + BackendClient.swift,
   ios/Marque/Features/Media.swift (LocalVideoPlayer/MediaStore — reuse for H7),
   ios/Marque/Features/TweakChatSheet.swift (cancel-safety pattern to mirror for H1),
   .maestro/editor-flow.yaml.
4. GATE (must be green before checking anything off):
   `cd /Users/home/Marque/backend && python -m pytest -q` (keyless — most items
   don't touch backend, but confirm nothing broke) AND
   `xcodegen generate` (from ios/) AND
   `xcodebuild -project ios/Marque.xcodeproj -scheme Marque -configuration Debug
   -destination 'platform=iOS Simulator,name=iPhone 16e' build` → BUILD SUCCEEDED.
   Maestro-touching items (H13, and any item that changes a11y ids used by an
   existing flow) additionally gate on the relevant .maestro/*.yaml passing on
   a booted simulator.
5. Check the item off in BACKLOG_EDITOR_UX.md with a one-line note.
6. Commit locally with a focused message + the repo's Co-Authored-By trailer.

Hard rules:
- Never weaken or delete an existing test/Maestro assertion.
- Do NOT touch ios/Marque/Features/Onboarding/** or onboarding asset catalogs
  (owned by a parallel session) — if a change would touch shared design-system
  files those onboarding screens also use (DesignSystem/Components.swift etc.),
  only ADD, never rename/remove existing symbols.
- SourceKit "Cannot find X in scope" on a single-file Read is a known false
  positive in this project (single-file has no module context) — the ONLY real
  signal is a full xcodebuild BUILD SUCCEEDED/FAILED.
- No new backend contract changes without checking Loop F/G's backlogs first —
  H5/H8/H10/H11 consume contract fields Loop F/G already added
  (undo_available, ERROR_CODES, job_expired, preview_url/preview_status,
  words) — reuse them, don't re-invent.
- Do NOT build for TestFlight or run any deploy/ship command. Local builds +
  simulator + Maestro only. The end-of-loop TestFlight build is a standing
  checkpoint — implement + commit, then STOP and surface it instead of
  building/uploading.

When EVERY box in BACKLOG_EDITOR_UX.md is checked AND all gates are green,
output the completion promise exactly: EDITOR PRO UX GREEN
