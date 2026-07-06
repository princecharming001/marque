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
- [ ] H7 Rough-cut local preview: AVPlayer seek-skip playback through kept
      intervals in play order (cuts + reorder + trims, zero Lambda cost).
      Reuse LocalVideoPlayer/MediaStore.
- [ ] H8 Filler-cut review: per-segment word strip shows cut words struck-
      through; tap a struck word → restore_range; tap a kept word → cut_range
      for that word's span.
- [ ] H9 Render progress: per-phase status line + elapsed time, Cancel that
      detaches cleanly (pairs with H1).
- [ ] H10 Undo in the editor UI (backend undo op + undo_available from F8) +
      warning chips on the clip card (b-roll skipped etc., from F6/F13).
- [ ] H11 "HD preview" button when caption/music/overlay changes exist: calls
      the G9 preview path (tweak?preview=1), shows preview_url inline.
- [ ] H12 A11y ids on every editor control (segment rows, trim steppers,
      style picker, music menu, apply/cancel) — Maestro-stable.
- [ ] H13 Maestro expansion: reorder+cut+apply on mock; reopen-editor
      persistence leg (catches H2-class regressions); failed-render path
      (garbage URL) asserting the error card + Try again.

Completion promise (only when EVERY box is checked and all gates are green):
EDITOR PRO UX GREEN
