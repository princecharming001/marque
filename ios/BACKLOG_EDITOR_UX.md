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
- [ ] H2 Editor initializes from the EDL's existing segment_order (today it
      resets to identity, so re-editing a reordered clip silently loses the
      reorder) — audit-CONFIRMED.
- [ ] H3 Apply hardening: button disabled while applying; 409 surfaces as
      transient "still rendering — try again shortly", not terminal failure.
- [ ] H4 Canonical op order tested end-to-end: cuts/mutes (original indices) →
      trims → reorder → captions/music; one test asserts iOS-computed ops
      applied by the real backend produce the intended EDL.
- [ ] H5 friendlyRenderError covers every backend ERROR_CODE + job_expired
      (F9) + fallback shows the raw error_detail one-liner.
- [ ] H6 Words-unavailable path: captions toggle disabled with explanation
      when the job has no words.
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
