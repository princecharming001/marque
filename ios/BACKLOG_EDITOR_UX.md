# iOS professional editor backlog (Ralph loop H worklist)

One item per iteration. Gate: keyless backend pytest (sanity) + `xcodegen generate`
+ `xcodebuild ... Debug ... BUILD SUCCEEDED` on iPhone 16e sim. Maestro-touching
items also gate on the relevant flow passing on a booted sim.

- [x] H1 Fixed: apply() now runs in a tracked applyTask; onDisappear cancels it
      and reverts clip.status to its pre-apply value if still mid-render
      (backend keeps rendering regardless — this only prevents a permanently-
      stuck "re-editing…" in the Library UI). Found + fixed a compounding bug
      while wiring this: AppStore.pollJob's while-loop never checked
      Task.isCancelled, so a cancelled task busy-spun (Task.sleep throws
      immediately once cancelled, swallowed by try?) instead of stopping —
      fixed for every caller, not just EditorView.
- [x] H2 Fixed (audit-CONFIRMED, matches the backend's own F1 bug class):
      load() now reads edl["segment_order"] when present + valid (a genuine
      permutation of the current segment count), instead of always resetting
      to identity. Added baseOrder (mirroring the base* pattern already used
      for captions/overlays/music) so hasChanges/computeOps compare against
      the ORIGINAL loaded order, not identity — otherwise re-opening an
      already-reordered clip would show Apply as active with zero new edits,
      and emit a redundant (harmless but wrong-signal) reorder op. Full E2E
      regression coverage lands in H13's reopen-editor persistence leg.
- [x] H3 Fixed: (a) explicit re-entrancy guard on Apply (applyTask==nil check)
      against a double-tap race before the toolbar item disappears; (b)
      LiveClipEngine.tweakClipOps's 409 response now carries a `transient`
      flag; EditorView.apply() checks it and stays in .editing (all staged
      local edits intact, inline dismissible banner) instead of the terminal
      .failed phase, which only offered "Close" and would have discarded the
      creator's in-progress edits over a purely transient "still rendering"
      condition. Noted a related but separately-scoped gap for H5: F9's new
      410 job_expired status isn't yet recognized by tweakClipOps at all
      (falls through to a "success" parse) — H5 will handle it alongside the
      rest of the ERROR_CODE mapping.
- [x] H4 Verified EditorView.computeOps()'s actual order (cuts/mutes by
      original index → reorder → trims → captions/music — NOT "trims →
      reorder" as originally worded here) is semantically correct given F1
      (trim walks PLAY order): reordering first means "trim the start/end"
      refers to whatever the creator's reorder just put at the front/back,
      matching what the editor UI visually displays. Added a cross-layer
      fixture-parity test (backend/test_editor_hardening.py) applying ops in
      the exact iOS-emitted sequence and pinning the result — protects
      against a future accidental reordering silently breaking the semantics.
- [x] H5 Fixed 3 gaps: (1) friendlyRenderError now explicitly cases
      internal_error (was falling to the fully generic default) and adds
      job_expired (F9); (2) the fallback/internal_error cases now accept an
      optional `detail` param and surface the raw error_detail one-liner when
      available — needed adding Clip.lastErrorDetail (Optional-with-default,
      Snapshot-safe) since pollJob only ever captured the error CODE, never
      the detail; (3) fixed the H3-flagged gap: tweakClipOps didn't recognize
      410 at all and would have silently treated an expired-session response
      as a successful tweak (parsed the {"detail":"job_expired"} body as if
      it were a normal success dict).
- [x] H6 Fixed: without this, an editor session on a job whose transcript
      isn't saved anymore (old/swept) let the creator toggle captions on and
      tap Apply, and the backend would silently SKIP the set_captions_enabled
      op ("no transcript available to rebuild captions") with zero feedback —
      apply() never inspected resp["skipped"]. Now wordsAvailable (from the
      GET response's words array) disables the whole toggle + shows an
      explanatory caption when the transcript isn't available.
- [x] H7 Implemented: RoughCutController (AVPlayer + periodic time observer,
      seeks through kept intervals — cuts removed, walked in `order`, trims
      clamped against the first/last PLAYED segment) + RoughCutPreviewSheet
      (VideoPlayer, labeled "structure only" since captions/music/overlays/
      mutes aren't simulated). Needed adding source_url to GET /v1/clips/{id}
      (backend had no way to expose the original video URL at all — small,
      tested, additive-only addition). "Preview" button in the Segments
      section header, gated on source_url being available.
- [x] H8 Implemented: EditSegment.words (from the RAW transcript, distinct
      from edl.captions' single-frame entries — needed start_ms/end_ms for a
      real frame span per word) each carry originallyCut (overlaps an
      existing drop); wordOverrides dict tracks explicit taps; wordStrip
      shows struck-through/dimmed cut words in a horizontal scroll, tap to
      toggle. computeOps emits cut_range/restore_range for words whose
      override actually flips their original state, ordered AFTER whole-
      segment cuts (so "cut this segment but keep one word" carves correctly
      out of the wholesale cut) but before mutes/reorder/trims. Verified
      restore_range against the real backend: correctly splits the existing
      drop around the restored word.
- [x] H9 Added: renderStartedAt + a TimelineView elapsed-time counter in the
      .rendering phase (so a long render doesn't read as hung), plus an
      explicit Cancel button. Cancel just calls dismiss() — H1's onDisappear
      already cancels applyTask and reverts clip.status, so no new detach
      logic was needed, only the visible affordance.
- [x] H10 Implemented both halves: (1) Undo toolbar button, gated on
      undo_available (F8), sends {"type":"undo"} via the same tweak endpoint
      then discards the unsaved local draft and reloads from the reverted
      EDL (undo targets what was actually APPLIED, not in-progress local
      changes — those the creator can just not Apply); (2) Clip.warnings
      (Optional, Snapshot-safe) populated from the per-clip JSON's warnings
      array (F6/F13 already wrote these backend-side, but nothing in iOS
      ever read them) + a warningChipLabel mapper + chip row on the Library
      card, independent of status (a "ready" clip can be quietly missing a
      feature it asked for).
- [x] H11 Implemented: tweakClipOps gains a preview param (appends ?preview=1);
      hasStyleChanges gates the button to when there's a caption/music/
      overlay change specifically (H7's rough-cut preview already covers
      structure — cuts/reorder/trims — for free; this costs a real, if
      cheap, G9 Lambda render). requestHDPreview applies the staged ops with
      preview=true, then polls GET for THIS clip's preview_status/
      preview_url (~60s budget) — never render_url/status, since a preview
      never commits. Inline VideoPlayer shows the result once ready.
- [x] H12 Audited + filled gaps: trim stepper +/- buttons and value label
      (editor.trim.start/end.*), reorder up/down buttons keyed by STABLE
      segIdx not position (editor.segment.N.moveUp/moveDown — position
      changes meaning every reorder), music track picker/volume slider/duck
      toggle (editor.music.track/volume/duck), whole-segment-row id
      (editor.segment.N). Deliberately did NOT rename the existing generic
      editor.cut/editor.mute ids — the existing editor-flow.yaml already
      targets them via Maestro's `index:` disambiguation (an established,
      working pattern here); renaming would have broken that flow, violating
      the loop's "never weaken/delete an existing test" rule.
- [ ] H13 Maestro expansion: reorder+cut+apply on mock; reopen-editor
      persistence leg (catches H2-class regressions); failed-render path
      (garbage URL) asserting the error card + Try again.

Completion promise (only when EVERY box is checked and all gates are green):
EDITOR PRO UX GREEN
