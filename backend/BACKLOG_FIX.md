# Backend editor correctness backlog (Ralph loop F worklist)

One item per iteration. Gate: `cd /Users/home/Marque/backend && python -m pytest -q`
fully green, keyless (no env keys).

- [x] F0 Committed (966e34b). Deploy is a standing checkpoint — NOT deployed;
      surfaced to user, awaiting explicit "deploy it" authorization.
- [x] F1 Fixed: trim_start/trim_end now walk PLAY order (segment_order) instead of
      array order. One pre-existing test encoded the bug as its expectation
      ("index 0 removed" despite segment_order=[2,0,1] meaning segment 2 plays
      first) — corrected with 3 new regression tests (partial spillover, trim_end,
      identity-order no-op preserved).
- [x] F2 no-repro: map_point's half-open interval math is internally consistent
      (segments/drops/captions all derive from the same ms_to_frame). Verified with
      a direct repro at every boundary frame around a drop; pinned as a permanent
      regression test (test_caption_frame_exactly_at_drop_boundary_maps_correctly).
- [x] F3 Fixed: overlays/b-roll now use new map_range_all (merges adjacent pieces,
      keeps non-adjacent reordered pieces separate) instead of map_range
      (longest-only, silently dropped the rest). Adjacent-piece case (single-
      segment drop) still merges identically to before — pinned in a dedicated
      test. react_schedule stays on map_range by design (freeze/play windows
      can't split without desyncing clip_from — that's F14's scope).
- [x] F4 no-repro: mute_range already does replace-with-split (no overlapping
      volume_ranges can persist); _kept_frames already unions drops before
      summing (no double-subtraction). Both pinned as regression tests.
- [x] F5 no-repro: clamp_range already rejects reversed/negative/way-out-of-bounds
      ranges (b>a check); the min-duration guard already blocks an over-long-end
      clamp from cutting the whole clip; malformed inputs already caught by the
      outer except and reported as "malformed op", never crash. All 4 pinned.
- [x] F6 Fixed: build_render_plan now skips broll entries with no resolved_url
      (was passing a None-URL layer straight to the renderer). warnings[] was
      already flowing to the client unfiltered (job["clips"] is a raw dict) —
      no change needed there.
- [x] F7 Fixed: per-clip render_gen counter (_bump_render_gen/_is_current_render).
      Simultaneous concurrent tweaks were already serialized via the status flag,
      but a watchdog-killed-then-retried render's ORIGINAL task (asyncio doesn't
      cancel it) could still complete late and silently overwrite a newer
      successful render_url. Every write site in _render_all_clips and
      _rerender_clip now checks the generation first.
- [x] F8 Verified undo already restores the triple (wholesale EDL snapshot swap,
      not per-field — structurally can't partially restore). Depth bumped 10->25.
      Added undo_available to both the tweak response and GET /v1/clips/{id}.
- [ ] F9 Swept/expired jobs return structured 410 job_expired (not bare 404).
- [ ] F10 Transcript hygiene: drop malformed word entries, dedupe identical caption
      frames, guard zero/duplicate timestamps (_normalize_words).
- [ ] F11 _merge_drops unions overlapping LLM+filler windows instead of silently
      skipping the LLM's cut.
- [ ] F12 Tweaked EDL wins over stale edit_prefs on re-render (manual
      set_captions_enabled/style must not be reverted by _apply_edit_prefs).
- [ ] F13 Silent-except sweep across the edit path: every swallow becomes a
      structured warnings[] entry or log line.
- [ ] F14 Duet clip_from/react_windows remapped under segment_order (backend side).
- [ ] F15 Durable edit sessions: persist {job_id: words, edl, clip meta} to Supabase
      (clone upsert_arm_stat pattern) with lazy restore on job-miss; keyless
      fallback stays in-memory.
- [ ] F16 Fuzz gate: seeded randomized op sequences over randomized EDLs assert
      invariants (segments monotonic, order a valid permutation, kept-frames>0 or
      rejected, plan always builds, captions within output bounds).

Completion promise (only when EVERY box is checked and the full pytest suite is
green): EDITOR CORRECTNESS GREEN
